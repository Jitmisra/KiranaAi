"""WAFADAARI — loyalty points, earned only on money that actually arrived.

A kirana already runs a loyalty scheme; it is the shopkeeper remembering who
comes in every day. This module writes that memory down, and it writes down
ONLY what the audit chain can prove:

  A bill EARNS points when the gateway's signature-verified webhook has settled
  it. A bill that was merely link-sent earns nothing — the customer may have
  closed the page, the link may have expired, the money may never have moved.
  `gawaah/manage.py` is the module that decides what "settled" means on this
  counter (the History and Today screens derive from it), and this module asks
  it rather than keeping a second opinion.

THE RULE. Integer points per WHOLE RUPEE settled — 6950 paise is 69 rupees is
69 points at one point per rupee, and the 50 paise earns nothing. A redemption
is worth an integer number of paise per point. Both numbers are the
shopkeeper's to set; both default to zero, which means OFF, because a scheme
that starts handing out points before anybody announced one is a gift nobody
agreed to. A bill earns at the rule that was IN FORCE WHEN IT SETTLED, so
changing the rule on Friday does not rewrite Monday's balances.

WHO. Points are keyed on the customer's phone number, normalised the way
`gawaah/customers.py` normalises it (one subscriber, three spellings). A
storefront order already carries a phone, so its settlement earns without
anybody typing anything. A counter bill has no phone unless the shopkeeper
enters one, so `POST /loyalty/attach` binds a session id to a number.

REDEMPTION IS A PROPOSAL, NOT A DEBIT. `POST /loyalty/redeem` writes down what
the customer asked to spend and what it is worth; nothing leaves the balance
until the till says which bill it went on (`POST /loyalty/redemptions/{id}/
apply`). The balance is checked at both moments, and a request past it is
refused by name. THE TILL OWNS THE BASKET, and the money service re-prices every
basket from its own tables before it mints — so until paisa is taught to read a
redemption, a till that subtracts the value from `amount_paise` will be refused
with `scan_total_disagreement`. That limit is stated on the screen, in the
proposal body, and in this docstring, rather than implied away.

INTEGER PAISE, INTEGER POINTS. There is no float, no `/` and no `round()` in
this file. `tools/lint_no_float.py` reads it.

A REFUSAL IS A RESULT. Every failure has a name in `reason` and a sentence in
`detail`, with a 400. Nothing here raises a 500.

THE FILES. State lives in `<shop>/loyalty.json` — the rules and their history,
which session belongs to which phone, and every redemption. Every change to it
is chained in `<shop>/loyalty.audit.jsonl` by `gawaah/ledger.py`, verifiable by
the same `verify()` as the money log. DELIBERATELY NOT `results/audit.jsonl`:
that file is held open by the money service in another process, and a second
writer breaks its chain. Phone numbers do not reach the chain — a ten-digit
number is a few seconds of brute force away from any digest of it, so the
chain carries the last four digits, which is what a shopkeeper reading the log
needs and no more.

MOUNTING. The router carries NO prefix; these paths are already absolute::

    GET  /loyalty/rules                        the rule, and what 100 points buy
    POST /loyalty/rules                        set it
    GET  /loyalty/balance/{phone}              earned, redeemed, what is left
    GET  /loyalty/ledger/{phone}               every bill and redemption, and why
    GET  /loyalty/members                      every number with a balance
    POST /loyalty/attach                       bind a counter bill to a phone
    POST /loyalty/redeem                       propose spending points
    GET  /loyalty/redemptions/{id}             read a proposal back
    POST /loyalty/redemptions/{id}/apply       the till says which bill it went on
    GET  /loyalty/health                       where the files are

    from gawaah import loyalty
    app.include_router(loyalty.router)
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .ledger import Ledger, verify
from .money import MoneyError, paise, to_rupees_str

router = APIRouter()


# --------------------------------------------------------------- refusals --
#
# Every one of these is a state this module can actually reach. The reason
# names the state; the sentence that says what to change lives in `detail`.

R_BAD_BODY = "loyalty_body_not_json"
R_RULE_MISSING = "rule_missing"
R_RULE_NOT_INTEGER = "rule_not_a_whole_number"
R_RULE_OUT_OF_RANGE = "rule_out_of_range"
R_NO_PHONE = "phone_missing"
R_BAD_PHONE = "phone_not_a_number"
R_SHORT_PHONE = "phone_too_short"
R_PHONE_TOO_LONG = "phone_too_long"
R_NO_SESSION = "session_id_missing"
R_BAD_SESSION = "session_id_malformed"
R_CREDITED_ELSEWHERE = "bill_already_credited_to_another_number"
R_POINTS_MISSING = "points_missing"
R_POINTS_NOT_INTEGER = "points_not_a_whole_number"
R_POINTS_NOT_POSITIVE = "points_not_positive"
R_POINTS_TOO_MANY = "points_beyond_this_counter"
R_EXCEEDS_BALANCE = "redemption_exceeds_balance"
R_NO_RULE = "no_loyalty_rule_set"
R_POINT_WORTHLESS = "point_worth_nothing"
R_BAD_REDEMPTION_ID = "redemption_id_malformed"
R_NO_REDEMPTION = "no_such_redemption"
R_ALREADY_APPLIED = "redemption_already_applied"
R_BILL_SETTLED = "bill_already_settled"
R_CHAIN_UNAVAILABLE = "audit_chain_unavailable"
R_FILE_UNREADABLE = "loyalty_file_unreadable"
R_FILE_UNWRITABLE = "loyalty_file_unwritable"
R_INTERNAL = "loyalty_internal_error"


# ------------------------------------------------------------------ limits --

#: Caps on the rule. Neither is a recommendation; each bounds a number that
#: multiplies money. What it costs when they are wrong: a shop that genuinely
#: wants a thousand points a rupee is refused. That shop can write in.
MAX_POINTS_PER_RUPEE = 1000
MAX_PAISE_PER_POINT = 100_000          # ten rupees a point

#: The most points one redemption may name. Past this, the value is past what
#: this till will price in one line and the request is almost certainly a typo.
MAX_POINTS_PER_REDEMPTION = 1_000_000

#: Phone digits. The floor is the same as customers.py's: fewer than seven
#: digits cannot be dialled. The ceiling is E.164's.
MIN_PHONE_DIGITS = 7
MAX_PHONE_DIGITS = 15
MAX_PHONE_TEXT = 24

MAX_MEMBERS = 500

LOYALTY_FILENAME = "loyalty.json"
LOYALTY_AUDIT_FILENAME = "loyalty.audit.jsonl"
LOYALTY_FORMAT = 1

#: A session id as the till, the camera session and the storefront write them:
#: `till_…`, `counter_live_4`, `shop_ord_…`. Checked before it is used as a
#: key, because it is echoed into files and into the chain.
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,79}$")
REDEMPTION_ID_RE = re.compile(r"^red_[0-9a-f]{12}$")

#: Why a bill earned nothing. Each is a state the chain can be in, named so the
#: screen can say it instead of showing a zero with no account of itself.
WHY_EARNED = "settled_by_the_gateway"
WHY_NOT_IN_LEDGER = "bill_not_in_the_ledger"
WHY_LINK_SENT = "link_sent_but_not_settled"
WHY_CLOSED_NOT_MINTED = "bill_closed_but_no_link_issued"
WHY_OPEN = "bill_still_open"
WHY_AMOUNT_UNKNOWN = "settled_but_amount_not_recorded"
WHY_NO_RULE = "no_rule_in_force_when_it_settled"
WHY_UNDER_A_RUPEE = "settled_for_less_than_a_whole_rupee"
#: WAAPSI. Points earned on what STAYED after a gateway-processed refund.
WHY_PART_REFUNDED = "earned_on_what_stayed_after_a_refund"
WHY_ALL_REFUNDED = "the_whole_bill_was_refunded"

WHY_SAID: dict[str, str] = {
    WHY_EARNED: "the gateway's signed webhook settled this bill.",
    WHY_PART_REFUNDED: ("part of this bill was refunded through the gateway; "
                        "points are on what the shop kept."),
    WHY_ALL_REFUNDED: ("the whole of this bill was refunded through the "
                       "gateway, so nothing was kept and nothing is earned."),
    WHY_NOT_IN_LEDGER: ("this session id is not in the audit chain yet. It "
                        "earns when a bill under it settles."),
    WHY_LINK_SENT: ("a payment link was issued but the gateway has not "
                    "confirmed the money. A link that was sent is not money "
                    "that arrived."),
    WHY_CLOSED_NOT_MINTED: "the basket closed but no payment link was issued.",
    WHY_OPEN: "the bill is still open on the counter.",
    WHY_AMOUNT_UNKNOWN: ("the chain says this bill settled but the record "
                         "carries no amount, so nothing can be counted."),
    WHY_NO_RULE: ("no loyalty rule was in force when this bill settled. "
                  "Rules are not applied backwards."),
    WHY_UNDER_A_RUPEE: "less than a whole rupee settled, and points are per rupee.",
}

SOURCE_ATTACHED = "attached_at_the_counter"
SOURCE_STOREFRONT = "storefront_order"


class LoyaltyRefused(Exception):
    """A named refusal with a reason a human can act on."""

    def __init__(self, reason: str, detail: str, status: int = 400) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.status = status


def _refusal(exc: LoyaltyRefused) -> JSONResponse:
    """The shape every other endpoint in this program answers a refusal with."""
    return JSONResponse(
        {"ok": False, "reason": exc.reason, "detail": exc.detail,
         "settles_money": False},
        status_code=exc.status,
    )


def _crash(exc: BaseException) -> JSONResponse:
    """The last resort. A 500 teaches the reader nothing, so there are none."""
    return JSONResponse(
        {"ok": False, "reason": R_INTERNAL,
         "detail": f"{type(exc).__name__}: {exc}", "settles_money": False},
        status_code=400,
    )


# ------------------------------------------------------- where things are --
#
# Resolved per call, never memoised at import. A test that sets GAWAAH_SHOP_DIR
# in a fixture must be able to change it between tests, and a module-level
# constant captured at import time silently ignores that — which is how a test
# harness once wrote over the live catalogue in results/.

from gawaah import till_ref as _till_ref

#: One definition, in `gawaah/till_ref.py`. This was sixteen copies of a tuple
#: that was missing `__main__`, and every one of them was wrong the same way.
_TILL_NAMES = _till_ref.TILL_NAMES


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def shop_dir() -> Path:
    """The shopkeeper's directory — the till's own answer, never a second one.

    If the till module is ALREADY LOADED its `store_dir()` is authoritative,
    because `set_store_dir()` can move the shop without touching the
    environment and a second answer here would leave the points behind. It is
    looked up in `sys.modules` rather than imported: importing the till drags
    the whole vision stack in, and a module about points should not pay for a
    camera.
    """
    for name in _TILL_NAMES:
        mod = sys.modules.get(name)
        fn = getattr(mod, "store_dir", None) if mod is not None else None
        if fn is not None:
            try:
                return Path(fn())
            except Exception:  # noqa: BLE001 - fall through to the environment
                pass
    override = os.environ.get("GAWAAH_SHOP_DIR")
    if override:
        return Path(override)
    return _repo_root() / "results" / "shop"


def loyalty_path() -> Path:
    return shop_dir() / LOYALTY_FILENAME


def audit_path() -> Path:
    """This module's own hash-chained log. See the docstring for why it is not
    `results/audit.jsonl`."""
    return shop_dir() / LOYALTY_AUDIT_FILENAME


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _phone_tail(phone: str) -> str:
    """The last four digits — what the chain records instead of the number."""
    return phone[-4:] if len(phone) >= 4 else phone


def _audit(event: str, **fields: Any) -> Optional[str]:
    """Append one auditable line. Returns the new head, or None if it failed.

    Best effort, but never silent: every endpoint that gets None says
    `audited: false` in its response rather than reporting a witnessed change
    that was not written.
    """
    try:
        return Ledger(audit_path()).append(
            ts=_now_iso(), module="loyalty", event=event, **fields)
    except Exception:  # noqa: BLE001 - a failed audit must not lose a change
        return None


# --------------------------------------------------------------- the file --


def _blank_doc() -> dict[str, Any]:
    return {
        "format": LOYALTY_FORMAT,
        "rules": {"points_per_rupee": 0, "paise_per_point": 0, "set_at": None},
        "rules_history": [],
        "attachments": {},
        "redemptions": {},
    }


def load_doc() -> dict[str, Any]:
    """The state file, or a blank one if it has never been written.

    A file that exists and cannot be parsed is a REFUSAL, not a blank: treating
    it as empty would forget every applied redemption and hand the same points
    out twice.
    """
    p = loyalty_path()
    if not p.exists():
        return _blank_doc()
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - a corrupt file is a named answer
        raise LoyaltyRefused(
            R_FILE_UNREADABLE,
            f"{p} exists but could not be read ({type(exc).__name__}: {exc}). "
            f"Nothing was guessed in its place.") from None
    if not isinstance(doc, dict):
        raise LoyaltyRefused(
            R_FILE_UNREADABLE, f"{p} is not a loyalty document.")
    base = _blank_doc()
    for key in ("rules", "attachments", "redemptions"):
        if isinstance(doc.get(key), dict):
            base[key] = doc[key]
    if isinstance(doc.get("rules_history"), list):
        base["rules_history"] = doc["rules_history"]
    return base


def save_doc(doc: dict[str, Any]) -> Path:
    """Write via a temp file and rename, so a reader never sees half a file."""
    p = loyalty_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, sort_keys=True, indent=1) + "\n",
                       encoding="utf-8")
        os.replace(tmp, p)
    except OSError as exc:
        raise LoyaltyRefused(
            R_FILE_UNWRITABLE,
            f"{p} could not be written ({type(exc).__name__}: {exc}). "
            f"Nothing was changed.") from None
    return p


# --------------------------------------------------------------- the rule --


def _whole(value: Any) -> Optional[int]:
    """An int, or None. bool first: True is an int in Python and a rule of
    True points per rupee is not something anybody meant."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _rule_at(doc: dict[str, Any], when: Optional[str]) -> Optional[dict[str, Any]]:
    """The rule in force at an instant, or None if none was.

    Timestamps are ISO-8601 UTC strings, so lexical order is chronological
    order, and the newest entry at or before `when` wins. A bill with no
    settlement time cannot have a rule in force for it.
    """
    if not when:
        return None
    best: Optional[dict[str, Any]] = None
    for h in doc.get("rules_history") or []:
        at = h.get("at")
        if not isinstance(at, str) or at > when:
            continue
        if best is None or at >= str(best.get("at")):
            best = h
    return best


def current_rules(doc: dict[str, Any]) -> dict[str, Any]:
    r = doc.get("rules") or {}
    ppr = _whole(r.get("points_per_rupee")) or 0
    ppp = _whole(r.get("paise_per_point")) or 0
    return {"points_per_rupee": max(0, ppr), "paise_per_point": max(0, ppp),
            "set_at": r.get("set_at"), "on": ppr > 0}


def save_rules(points_per_rupee: int, paise_per_point: int, *,
               at: Optional[str] = None) -> dict[str, Any]:
    """Set the rule. `at` is for tests that need history; the endpoint uses now."""
    doc = load_doc()
    when = at or _now_iso()
    entry = {"at": when, "points_per_rupee": int(points_per_rupee),
             "paise_per_point": int(paise_per_point)}
    doc["rules_history"] = list(doc.get("rules_history") or []) + [entry]
    doc["rules"] = {"points_per_rupee": int(points_per_rupee),
                    "paise_per_point": int(paise_per_point), "set_at": when}
    save_doc(doc)
    return doc


def _rules_view(doc: dict[str, Any]) -> dict[str, Any]:
    rules = current_rules(doc)
    example = None
    if rules["paise_per_point"] > 0:
        value = 100 * int(rules["paise_per_point"])
        example = {"points": 100, "value_paise": int(paise(value)),
                   "value_rupees": to_rupees_str(paise(value))}
    return {
        "rules": rules,
        "example": example,
        "history_count": len(doc.get("rules_history") or []),
        "limits": {"max_points_per_rupee": MAX_POINTS_PER_RUPEE,
                   "max_paise_per_point": MAX_PAISE_PER_POINT},
    }


# ------------------------------------------------------------ the phone --


def normalise_phone(raw: Any) -> str:
    """One subscriber, one key. customers.py's rule, asked of customers.py.

    Two modules with two ideas of which digits identify a person would list one
    customer twice on one screen and once on another. The fallback is the same
    rule copied, for the day that module is not importable; it is not a second
    opinion.
    """
    try:
        from . import customers  # noqa: WPS433 - late; it may be mid-edit
        fn = getattr(customers, "normalise_phone", None)
        if fn is not None:
            return str(fn(raw))
    except Exception:  # noqa: BLE001 - fall through to the copy below
        pass
    if not isinstance(raw, str):
        return ""
    digits = re.sub(r"\D", "", raw)
    while digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    return digits


def _require_phone(raw: Any) -> str:
    if raw is None or not str(raw).strip():
        raise LoyaltyRefused(
            R_NO_PHONE,
            "no phone number was given. Points are kept against the number "
            "the customer says at the counter.")
    text = str(raw).strip()
    if len(text) > MAX_PHONE_TEXT:
        raise LoyaltyRefused(
            R_PHONE_TOO_LONG,
            f"{len(text)} characters is longer than any phone number. The cap "
            f"is {MAX_PHONE_TEXT}.")
    digits = normalise_phone(text)
    if not digits:
        raise LoyaltyRefused(
            R_BAD_PHONE,
            f"{text!r} has no digits in it, so it is not a phone number.")
    if len(digits) < MIN_PHONE_DIGITS:
        raise LoyaltyRefused(
            R_SHORT_PHONE,
            f"{text!r} has {len(digits)} digits in it. A number that can be "
            f"dialled has at least {MIN_PHONE_DIGITS}.")
    if len(digits) > MAX_PHONE_DIGITS:
        raise LoyaltyRefused(
            R_BAD_PHONE,
            f"{text!r} has {len(digits)} digits in it; no phone number has "
            f"more than {MAX_PHONE_DIGITS}.")
    return digits


def _require_session_id(raw: Any) -> str:
    if raw is None or not str(raw).strip():
        raise LoyaltyRefused(
            R_NO_SESSION,
            "no session id was given. The till shows one on every bill; it "
            "is what ties a phone number to the money that settled.")
    s = str(raw).strip()
    if not SESSION_ID_RE.match(s):
        raise LoyaltyRefused(
            R_BAD_SESSION,
            f"{s!r} is not a session id from this counter. They are letters, "
            f"digits, dots, dashes and underscores, up to 80 long.")
    return s


def _require_points(raw: Any) -> int:
    if raw is None:
        raise LoyaltyRefused(
            R_POINTS_MISSING, "say how many points to redeem.")
    n = _whole(raw)
    if n is None and isinstance(raw, str) and raw.strip().isdigit():
        n = int(raw.strip())
    if n is None:
        raise LoyaltyRefused(
            R_POINTS_NOT_INTEGER,
            f"{raw!r} is not a whole number of points. Half a point is not "
            f"something this counter keeps.")
    if n <= 0:
        raise LoyaltyRefused(
            R_POINTS_NOT_POSITIVE,
            f"{n} points is nothing to redeem. Say a positive number.")
    if n > MAX_POINTS_PER_REDEMPTION:
        raise LoyaltyRefused(
            R_POINTS_TOO_MANY,
            f"{n} points is past the {MAX_POINTS_PER_REDEMPTION} this counter "
            f"redeems in one line.")
    return n


def _require_redemption_id(raw: Any) -> str:
    """Checked against a strict charset BEFORE it is used as a key or echoed."""
    s = (str(raw) if raw is not None else "").strip()
    if not REDEMPTION_ID_RE.match(s):
        raise LoyaltyRefused(
            R_BAD_REDEMPTION_ID,
            f"{raw!r} is not a redemption id from this counter. They look like "
            f"'red_' followed by twelve hex characters.")
    return s


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a named refusal
        raise LoyaltyRefused(
            R_BAD_BODY, "this request's body is not JSON.") from None
    if not isinstance(body, dict):
        raise LoyaltyRefused(
            R_BAD_BODY,
            f"this request's body is a {type(body).__name__}; it must be a "
            f"JSON object.")
    return body


# ------------------------------------------------------------- the chain --
#
# Read through gawaah/manage.py, which is the module that decides what a bill
# is and when it settled — the History and Today screens derive from the same
# functions, so this screen cannot say a bill earned points that History says
# never settled. Imported late: manage pulls in the vision constants and a
# points module should not pay for them at import.


def _manage() -> Any:
    try:
        from . import manage  # noqa: WPS433 - deliberately late
    except Exception as exc:  # noqa: BLE001 - a missing module is a named answer
        raise LoyaltyRefused(
            R_CHAIN_UNAVAILABLE,
            f"gawaah/manage.py is not importable ({type(exc).__name__}: "
            f"{exc}), and it is the module that decides which bills settled. "
            f"No points can be derived without it.") from None
    for needed in ("read_chain", "bills_from"):
        if not hasattr(manage, needed):
            raise LoyaltyRefused(
                R_CHAIN_UNAVAILABLE,
                f"gawaah/manage.py has no {needed!r}, so settled bills cannot "
                f"be derived the same way the History screen derives them.")
    return manage


def _settled_amounts(records: tuple[dict, ...]) -> dict[str, Optional[int]]:
    """session_id -> the paise the gateway said arrived, or None if unrecorded.

    `bills_from` decides WHETHER a bill settled; it does not carry the amount,
    which is why this second, narrower fold exists. Two records can carry it:
    the counter session's own view of the webhook (`webhook_amount_paise` on
    the `settled_green` line) and the kernel's `intent.settled`
    (`amount_paise`). The webhook line is preferred, for the same reason
    manage.py prefers it: it is the one written from the signature check. A
    later webhook the session IGNORED carries `null` and must not overwrite a
    real figure — the live chain has exactly that shape in it.
    """
    out: dict[str, Optional[int]] = {}
    from_webhook: set[str] = set()
    for rec in records:
        sid = rec.get("session_id")
        if not isinstance(sid, str) or not sid:
            continue
        module = rec.get("module")
        event = rec.get("event")
        if module == "session" and event == "webhook":
            green = rec.get("reason") == "settled_green" or rec.get("to") == "PAID"
            amt = _whole(rec.get("webhook_amount_paise"))
            if green and amt is not None and sid not in from_webhook:
                out[sid] = amt
                from_webhook.add(sid)
        elif module == "kernel" and event == "intent.settled":
            amt = _whole(rec.get("amount_paise"))
            if sid not in from_webhook and amt is not None and sid not in out:
                out[sid] = amt
    return out


def _refunded_amounts(records: tuple[dict, ...]) -> dict[str, int]:
    """session_id -> paise the gateway PROCESSED back on it. WAAPSI clawback.

    Folded off the kernel's own `refund.processed` lines in results/audit.jsonl
    — the one writer — the same chain `_settled_amounts` reads. A refund merely
    requested (`refund.requested`) is not here: nothing is clawed back until
    the signed callback says the money went back, which is the same bar
    settlement itself is held to. Read through `receipts.refunds_from` so this
    program folds refund lines in exactly one place.
    """
    try:
        from . import receipts  # noqa: WPS433 - late; it pulls in manage
    except Exception:  # noqa: BLE001 - no refunds readable is "no clawback"
        return {}
    out: dict[str, int] = {}
    for sid, by_key in receipts.refunds_from(records).items():
        total = 0
        for rf in by_key.values():
            amt = rf.get("amount_paise")
            if rf.get("refunded") and isinstance(amt, int) and not isinstance(amt, bool):
                total += amt
        if total > 0:
            out[sid] = total
    return out


def _storefront_phones() -> tuple[dict[str, dict[str, str]], bool, int]:
    """session_id -> {phone, phone_as_given, order_id} for every storefront
    order, read through the module that wrote them. Never guessed from a
    directory here.

    Returns (map, readable, orders_seen). `readable` False means the orders
    could not be read at all and the response says so; it is not "no orders".
    """
    try:
        from . import storefront  # noqa: WPS433 - late; it may be absent
        docs = list(storefront._all_orders())
    except Exception:  # noqa: BLE001 - a named limit, reported, not a crash
        return {}, False, 0
    out: dict[str, dict[str, str]] = {}
    for d in docs:
        pay = d.get("payment") or {}
        sid = pay.get("session_id")
        raw = (d.get("customer") or {}).get("phone")
        digits = normalise_phone(raw) if isinstance(raw, str) else ""
        if not isinstance(sid, str) or not sid or not digits:
            continue
        out[sid] = {"phone": digits, "phone_as_given": str(raw),
                    "order_id": str(d.get("order_id") or "")}
    return out, True, len(docs)


def _bill_state(bill: Optional[dict[str, Any]], settled_paise: Optional[int],
                refunded_paise: int = 0) -> dict[str, Any]:
    """What the chain says about one session, in fields a screen can show.

    WAAPSI. `refunded_paise` is what the gateway sent back on this bill, off
    its signed `refund.processed` lines. `net_settled_paise` is what actually
    STAYED — settled minus refunded, never below zero — and it is the number
    points are earned on, because a rupee that came in and went straight back
    out is not a rupee the shop kept.
    """
    if bill is None:
        return {"found": False, "closed": False, "minted": False,
                "settled": False, "settled_at": None, "settled_by": None,
                "settled_paise": None, "settled_rupees": None,
                "refunded_paise": 0, "net_settled_paise": None,
                "total_paise": None}
    net = None
    if settled_paise is not None:
        net = max(0, int(paise(settled_paise)) - int(refunded_paise))
    return {
        "found": True,
        "closed": bool(bill.get("closed")),
        "minted": bool(bill.get("minted")),
        "settled": bool(bill.get("settled")),
        "settled_at": bill.get("settled_at"),
        "settled_by": bill.get("settled_by"),
        "settled_paise": settled_paise,
        "settled_rupees": (to_rupees_str(paise(settled_paise))
                           if settled_paise is not None else None),
        "refunded_paise": int(refunded_paise),
        "refunded_rupees": (to_rupees_str(paise(refunded_paise))
                            if refunded_paise else None),
        "net_settled_paise": net,
        "net_settled_rupees": (to_rupees_str(paise(net)) if net is not None else None),
        "total_paise": _whole(bill.get("total_paise")),
    }


def _earn_for(doc: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Points one bill earned, and WHY that number — including why zero.

    Integer arithmetic only: whole rupees are `paise // 100`, and the points
    are that times the rule in force at the moment the gateway settled it.
    """
    if not state["found"]:
        return {"points": 0, "why": WHY_NOT_IN_LEDGER, "rule": None,
                "whole_rupees": 0}
    if not state["settled"]:
        if state["minted"]:
            why = WHY_LINK_SENT
        elif state["closed"]:
            why = WHY_CLOSED_NOT_MINTED
        else:
            why = WHY_OPEN
        return {"points": 0, "why": why, "rule": None, "whole_rupees": 0}
    settled = state["settled_paise"]
    if settled is None:
        return {"points": 0, "why": WHY_AMOUNT_UNKNOWN, "rule": None,
                "whole_rupees": 0, "refunded_paise": 0, "clawed_back": False}
    rule = _rule_at(doc, state["settled_at"])
    # WAAPSI. Points are earned on money that arrived AND STAYED, so a
    # refund the gateway processed is netted off BEFORE the rupees are
    # counted — the clawback is the earn rule applied to the net, not a
    # separate subtraction of points bolted on afterwards. `net_settled_paise`
    # is `_bill_state`'s own figure (settled − refunded, floored at zero).
    refunded = int(state.get("refunded_paise") or 0)
    net = state.get("net_settled_paise")
    if net is None:
        net = int(paise(settled))
    if rule is None or (_whole(rule.get("points_per_rupee")) or 0) <= 0:
        return {"points": 0, "why": WHY_NO_RULE, "rule": rule,
                "whole_rupees": int(net) // 100,
                "refunded_paise": refunded, "clawed_back": refunded > 0}
    whole_rupees = int(net) // 100
    if whole_rupees <= 0:
        why = WHY_ALL_REFUNDED if refunded > 0 else WHY_UNDER_A_RUPEE
        return {"points": 0, "why": why, "rule": rule, "whole_rupees": 0,
                "refunded_paise": refunded, "clawed_back": refunded > 0}
    pts = whole_rupees * int(rule["points_per_rupee"])
    return {"points": pts,
            "why": WHY_PART_REFUNDED if refunded > 0 else WHY_EARNED,
            "rule": rule, "whole_rupees": whole_rupees,
            "refunded_paise": refunded, "clawed_back": refunded > 0}


def derive() -> dict[str, Any]:
    """Every phone's points, derived fresh from the chain and the state file.

    Returns::

        {"by_phone": {digits: {"entries": [...], "earned": n, "redeemed": n,
                               "proposed": n, "balance": n, ...}},
         "sessions": {session_id: state},
         "chain": {...}, "orders_readable": bool, "orders_seen": n, "doc": doc}

    Nothing is cached across calls. The chain is small and the cost of a
    stale balance is a redemption the shop cannot cover.
    """
    doc = load_doc()
    manage = _manage()
    try:
        records, chain = manage.read_chain()
        bills = manage.bills_from(records)
    except Exception as exc:  # noqa: BLE001
        raise LoyaltyRefused(
            R_CHAIN_UNAVAILABLE,
            f"the audit chain could not be read ({type(exc).__name__}: {exc}). "
            f"No balance was invented in its place.") from None
    amounts = _settled_amounts(records)
    refunded = _refunded_amounts(records)
    store_phones, orders_readable, orders_seen = _storefront_phones()

    # Which phone each session belongs to. An explicit attachment at the
    # counter beats the storefront's own record, because the shopkeeper typed
    # it on purpose; both sources are named on the entry.
    owner: dict[str, tuple[str, str, str]] = {}
    for sid, rec in store_phones.items():
        owner[sid] = (rec["phone"], SOURCE_STOREFRONT, rec.get("order_id", ""))
    for sid, rec in (doc.get("attachments") or {}).items():
        digits = normalise_phone(str((rec or {}).get("phone") or ""))
        if isinstance(sid, str) and digits:
            owner[sid] = (digits, SOURCE_ATTACHED, "")

    by_phone: dict[str, dict[str, Any]] = {}

    def bucket(phone: str) -> dict[str, Any]:
        b = by_phone.get(phone)
        if b is None:
            b = {"phone": phone, "entries": [], "earned": 0, "redeemed": 0,
                 "proposed": 0, "settled_paise": 0, "refunded_paise": 0,
                 "bills_settled": 0,
                 "bills_awaiting": 0, "bills_not_in_ledger": 0}
            by_phone[phone] = b
        return b

    sessions: dict[str, dict[str, Any]] = {}
    for sid, (phone, source, order_id) in owner.items():
        state = _bill_state(bills.get(sid), amounts.get(sid), refunded.get(sid, 0))
        sessions[sid] = state
        earn = _earn_for(doc, state)
        b = bucket(phone)
        b["earned"] += int(earn["points"])
        if state["settled"] and state["settled_paise"] is not None:
            b["settled_paise"] += int(paise(state["settled_paise"]))
            b["refunded_paise"] += int(state.get("refunded_paise") or 0)
            b["bills_settled"] += 1
        elif not state["found"]:
            b["bills_not_in_ledger"] += 1
        else:
            b["bills_awaiting"] += 1
        attached = (doc.get("attachments") or {}).get(sid) or {}
        b["entries"].append({
            "kind": "earn",
            "session_id": sid,
            "at": state["settled_at"] or attached.get("at"),
            "source": source,
            "order_id": order_id or None,
            "bill": state,
            "points": int(earn["points"]),
            "whole_rupees": int(earn["whole_rupees"]),
            "points_per_rupee": (_whole((earn["rule"] or {}).get("points_per_rupee"))
                                 if earn["rule"] else None),
            "refunded_paise": int(earn.get("refunded_paise") or 0),
            "clawed_back": bool(earn.get("clawed_back")),
            "why": earn["why"],
            "said": WHY_SAID.get(earn["why"], earn["why"]),
        })

    for rid, red in (doc.get("redemptions") or {}).items():
        phone = normalise_phone(str((red or {}).get("phone") or ""))
        pts = _whole((red or {}).get("points")) or 0
        if not phone or pts <= 0:
            continue
        b = bucket(phone)
        applied = bool(red.get("applied"))
        if applied:
            b["redeemed"] += pts
        else:
            b["proposed"] += pts
        sid = red.get("session_id")
        state = None
        if isinstance(sid, str) and sid:
            state = sessions.get(sid) or _bill_state(
                bills.get(sid), amounts.get(sid), refunded.get(sid, 0))
        value = _whole(red.get("value_paise")) or 0
        b["entries"].append({
            "kind": "redeem",
            "redemption_id": rid,
            "at": red.get("applied_at") or red.get("proposed_at"),
            "proposed_at": red.get("proposed_at"),
            "applied": applied,
            "applied_at": red.get("applied_at"),
            "session_id": sid,
            "bill": state,
            "points": pts,
            "value_paise": int(paise(value)),
            "value_rupees": to_rupees_str(paise(value)),
            "paise_per_point": _whole(red.get("paise_per_point")),
            "said": ("taken off a bill" if applied else
                     "proposed; not yet put on a bill, so not yet deducted"),
        })

    rules = current_rules(doc)
    for b in by_phone.values():
        b["balance"] = int(b["earned"]) - int(b["redeemed"])
        value = int(b["balance"]) * int(rules["paise_per_point"])
        b["balance_value_paise"] = int(paise(value)) if value > 0 else 0
        b["balance_value_rupees"] = to_rupees_str(paise(b["balance_value_paise"]))
        b["settled_rupees"] = to_rupees_str(int(paise(b["settled_paise"])))
        b["refunded_rupees"] = to_rupees_str(int(paise(b.get("refunded_paise") or 0)))
        # Newest first, by the entry's own timestamp; the id breaks ties so two
        # entries in one second come out in a stable order.
        b["entries"].sort(
            key=lambda e: (str(e.get("at") or ""),
                           str(e.get("session_id") or e.get("redemption_id") or "")),
            reverse=True)

    return {"by_phone": by_phone, "sessions": sessions, "chain": chain,
            "orders_readable": orders_readable, "orders_seen": orders_seen,
            "doc": doc, "rules": rules}


def balance_of(phone: str, view: Optional[dict[str, Any]] = None
               ) -> dict[str, Any]:
    """One number's account, with every figure it is made of."""
    view = view or derive()
    b = view["by_phone"].get(phone)
    if b is None:
        b = {"phone": phone, "entries": [], "earned": 0, "redeemed": 0,
             "proposed": 0, "balance": 0, "balance_value_paise": 0,
             "balance_value_rupees": "0.00", "settled_paise": 0,
             "settled_rupees": "0.00", "refunded_paise": 0,
             "refunded_rupees": "0.00", "bills_settled": 0,
             "bills_awaiting": 0, "bills_not_in_ledger": 0}
    return {
        "phone": phone,
        "known": bool(b["entries"]),
        "earned_points": int(b["earned"]),
        "redeemed_points": int(b["redeemed"]),
        "proposed_points": int(b["proposed"]),
        "balance_points": int(b["balance"]),
        "balance_value_paise": int(b["balance_value_paise"]),
        "balance_value_rupees": b["balance_value_rupees"],
        "settled_paise": int(b["settled_paise"]),
        "settled_rupees": b["settled_rupees"],
        "refunded_paise": int(b.get("refunded_paise") or 0),
        "refunded_rupees": b.get("refunded_rupees", "0.00"),
        "bills_settled": int(b["bills_settled"]),
        "bills_awaiting": int(b["bills_awaiting"]),
        "bills_not_in_ledger": int(b["bills_not_in_ledger"]),
        "entries": list(b["entries"]),
        "rules": view["rules"],
        "chain": _chain_block(view),
    }


def _chain_block(view: dict[str, Any]) -> dict[str, Any]:
    c = view["chain"]
    return {
        "ok": bool(c.get("ok", True)),
        "exists": bool(c.get("exists")),
        "lines_verified": int(c.get("lines_verified") or 0),
        "error": c.get("error"),
        "path": c.get("path"),
        "orders_readable": bool(view["orders_readable"]),
        "orders_seen": int(view["orders_seen"]),
    }


# ----------------------------------------------------------------- routes --


def _ok(**fields: Any) -> JSONResponse:
    return JSONResponse({"ok": True, "settles_money": False, **fields})


RULES_NOTE = ("Points are earned only on bills the gateway settled, at the "
              "rule in force when they settled. A link that was sent but not "
              "paid earns nothing. Zero points per rupee means the scheme is "
              "off.")


@router.get("/loyalty/rules")
def rules_ep() -> JSONResponse:
    try:
        doc = load_doc()
        return _ok(**_rules_view(doc), file=str(loyalty_path()), note=RULES_NOTE)
    except LoyaltyRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


def _read_rule(body: dict[str, Any], key: str, cap: int) -> int:
    if key not in body:
        raise LoyaltyRefused(
            R_RULE_MISSING,
            f"{key!r} was not given. Both 'points_per_rupee' and "
            f"'paise_per_point' are needed; send 0 for either to turn it off.")
    n = _whole(body.get(key))
    if n is None and isinstance(body.get(key), str) and str(body[key]).strip().isdigit():
        n = int(str(body[key]).strip())
    if n is None:
        raise LoyaltyRefused(
            R_RULE_NOT_INTEGER,
            f"{key} is {body.get(key)!r}. It has to be a whole number — points "
            f"are counted, not measured, and paise are already the smallest "
            f"unit of money.")
    if n < 0 or n > cap:
        raise LoyaltyRefused(
            R_RULE_OUT_OF_RANGE,
            f"{key} is {n}; this counter takes 0 to {cap}.")
    return n


@router.post("/loyalty/rules")
async def rules_set_ep(request: Request) -> JSONResponse:
    """Body: {points_per_rupee: int, paise_per_point: int}. Both whole numbers.

    The change is dated, and every bill keeps the rule that was in force when
    it settled — see `_rule_at`. Audited in this module's own chain.
    """
    try:
        body = await _json_body(request)
        ppr = _read_rule(body, "points_per_rupee", MAX_POINTS_PER_RUPEE)
        ppp = _read_rule(body, "paise_per_point", MAX_PAISE_PER_POINT)
        before = current_rules(load_doc())
        doc = save_rules(ppr, ppp)
        head = _audit("rules.set", points_per_rupee=ppr, paise_per_point=ppp,
                      was_points_per_rupee=before["points_per_rupee"],
                      was_paise_per_point=before["paise_per_point"],
                      minted=False)
        return _ok(**_rules_view(doc), file=str(loyalty_path()),
                   audited=head is not None, was=before, note=RULES_NOTE)
    except LoyaltyRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


BALANCE_NOTE = ("Every point here was derived from the audit chain at the "
                "moment you asked. Earned points come from bills the gateway "
                "settled; redeemed points are the ones the till put on a bill. "
                "A proposal that was never put on a bill is listed but not "
                "deducted.")


@router.get("/loyalty/balance/{phone}")
def balance_ep(phone: str) -> JSONResponse:
    try:
        digits = _require_phone(phone)
        view = derive()
        b = balance_of(digits, view)
        b.pop("entries", None)
        return _ok(**b, note=BALANCE_NOTE)
    except LoyaltyRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(LoyaltyRefused(
            R_CHAIN_UNAVAILABLE,
            f"an amount in the chain is not integer paise ({exc}). No balance "
            f"was derived."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/loyalty/ledger/{phone}")
def ledger_ep(phone: str) -> JSONResponse:
    """Every bill this number is tied to and every redemption, newest first,
    each with the reason for its points — including a reason for zero."""
    try:
        digits = _require_phone(phone)
        view = derive()
        b = balance_of(digits, view)
        return _ok(**b, count=len(b["entries"]), why=WHY_SAID, note=BALANCE_NOTE)
    except LoyaltyRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(LoyaltyRefused(
            R_CHAIN_UNAVAILABLE,
            f"an amount in the chain is not integer paise ({exc}). No ledger "
            f"was derived."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/loyalty/members")
def members_ep() -> JSONResponse:
    """Every number with any history, highest balance first. No addresses, no
    names: this module never had them."""
    try:
        view = derive()
        rows = []
        for phone, b in view["by_phone"].items():
            rows.append({
                "phone": phone,
                "earned_points": int(b["earned"]),
                "redeemed_points": int(b["redeemed"]),
                "proposed_points": int(b["proposed"]),
                "balance_points": int(b["balance"]),
                "balance_value_paise": int(b["balance_value_paise"]),
                "balance_value_rupees": b["balance_value_rupees"],
                "bills_settled": int(b["bills_settled"]),
                "bills_awaiting": int(b["bills_awaiting"]),
                "last_at": max((str(e.get("at") or "") for e in b["entries"]),
                               default=""),
            })
        rows.sort(key=lambda r: (-int(r["balance_points"]), r["phone"]))
        return _ok(count=len(rows), truncated=len(rows) > MAX_MEMBERS,
                   members=rows[:MAX_MEMBERS], rules=view["rules"],
                   chain=_chain_block(view))
    except LoyaltyRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(LoyaltyRefused(
            R_CHAIN_UNAVAILABLE,
            f"an amount in the chain is not integer paise ({exc})."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/loyalty/attach")
async def attach_ep(request: Request) -> JSONResponse:
    """Body: {session_id, phone}. Tie a counter bill to a number.

    A session that is not in the chain yet is ACCEPTED and said so: the till
    knows its session id before the first packet is priced, and refusing until
    the mint would mean typing the number twice. A session whose bill has
    already settled and been credited to a DIFFERENT number is refused —
    moving points after they were earned is how one customer's points reach
    another's redemption.
    """
    try:
        body = await _json_body(request)
        sid = _require_session_id(body.get("session_id"))
        phone = _require_phone(body.get("phone"))
        view = derive()
        doc = view["doc"]
        attachments = dict(doc.get("attachments") or {})
        was = attachments.get(sid) or {}
        was_phone = normalise_phone(str(was.get("phone") or ""))
        state = view["sessions"].get(sid)
        if state is None:
            bills = _manage().bills_from(_manage().read_chain()[0])
            state = _bill_state(bills.get(sid),
                                _settled_amounts(_manage().read_chain()[0]).get(sid))
        changed = was_phone != phone
        if changed and was_phone and state["settled"]:
            raise LoyaltyRefused(
                R_CREDITED_ELSEWHERE,
                f"bill {sid} settled and its points went to the number ending "
                f"{_phone_tail(was_phone)}. Points that were earned are not "
                f"moved. Nothing was changed.")
        if changed:
            attachments[sid] = {"phone": phone,
                                "phone_as_given": str(body.get("phone")).strip(),
                                "at": _now_iso()}
            doc["attachments"] = attachments
            save_doc(doc)
        head = _audit("phone.attached", session_id=sid,
                      phone_tail=_phone_tail(phone), changed=changed,
                      bill_settled=bool(state["settled"]), minted=False) \
            if changed else None
        earn = _earn_for(doc, state)
        return _ok(session_id=sid, phone=phone, changed=changed,
                   bill=state,
                   earns={"points": int(earn["points"]), "why": earn["why"],
                          "said": WHY_SAID.get(earn["why"], earn["why"])},
                   audited=(head is not None) if changed else None,
                   note=("Points are credited when the gateway settles this "
                         "bill, not now. A bill that was only link-sent "
                         "earns nothing until it is paid."))
    except LoyaltyRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


#: What the till has to do with a proposal. Sent with every one, so the screen
#: and the till read the same list and nobody has to remember it.
TILL_MUST = [
    "Show this line on the basket as it is written here, and take its amount "
    "off the total the customer sees.",
    "When the bill is minted, tell this counter which bill it went on: POST "
    "/loyalty/redemptions/{id}/apply with the bill's session id. That is the "
    "moment the points leave the balance. Until then nothing is deducted.",
    "Never let the amount taken off exceed the basket. The money service "
    "refuses a total that is not positive.",
    "The money service re-prices every basket from its own tables before it "
    "mints. Until it is taught to read this redemption from the witness and "
    "subtract the same amount itself, a mint at the discounted total is "
    "refused (scan_total_disagreement). That wiring is outside this module.",
]


@router.post("/loyalty/redeem")
async def redeem_ep(request: Request) -> JSONResponse:
    """Body: {phone, points}. Propose spending points; deduct nothing yet.

    Refused by name past the balance. The value is at TODAY'S paise-per-point
    and is frozen on the proposal, so a rule changed between proposal and
    apply does not change what the customer was shown.
    """
    try:
        body = await _json_body(request)
        phone = _require_phone(body.get("phone"))
        points = _require_points(body.get("points"))
        view = derive()
        rules = view["rules"]
        if not rules["on"]:
            raise LoyaltyRefused(
                R_NO_RULE,
                "no loyalty rule is set, so nothing has been earned and nothing "
                "can be redeemed. Set points per rupee first.")
        ppp = int(rules["paise_per_point"])
        if ppp <= 0:
            raise LoyaltyRefused(
                R_POINT_WORTHLESS,
                "the rule says a point is worth 0 paise, so redeeming points "
                "would take nothing off. Set paise per point first.")
        bal = balance_of(phone, view)
        available = int(bal["balance_points"])
        if points > available:
            raise LoyaltyRefused(
                R_EXCEEDS_BALANCE,
                f"the number ending {_phone_tail(phone)} has {available} points "
                f"and this asks for {points}. Nothing was proposed.")
        value = points * ppp
        rid = "red_" + secrets.token_hex(6)
        now = _now_iso()
        red = {
            "redemption_id": rid,
            "phone": phone,
            "points": int(points),
            "paise_per_point": ppp,
            "value_paise": int(paise(value)),
            "value_rupees": to_rupees_str(paise(value)),
            "proposed_at": now,
            "applied": False,
            "applied_at": None,
            "session_id": None,
            "balance_before_points": available,
        }
        doc = view["doc"]
        reds = dict(doc.get("redemptions") or {})
        reds[rid] = red
        doc["redemptions"] = reds
        save_doc(doc)
        head = _audit("redemption.proposed", redemption_id=rid,
                      phone_tail=_phone_tail(phone), points=int(points),
                      value_paise=int(paise(value)), applied=False,
                      minted=False)
        return _ok(redemption=red, line=_line_for(red), applied=False,
                   balance_before_points=available,
                   balance_if_applied_points=available - int(points),
                   audited=head is not None, till_must=TILL_MUST,
                   note=("Nothing has been deducted. The till puts this line "
                         "on a bill and then confirms it with the bill's "
                         "session id; that is when the points leave."))
    except LoyaltyRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(LoyaltyRefused(
            R_CHAIN_UNAVAILABLE,
            f"an amount in the chain is not integer paise ({exc}). Nothing "
            f"was proposed."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


def _line_for(red: dict[str, Any]) -> dict[str, Any]:
    """The discount line as the till should draw it. `off_paise`, positive,
    the way offers.py names a discount — a negative price is a thing this
    program never writes."""
    value = int(paise(_whole(red.get("value_paise")) or 0))
    return {
        "kind": "loyalty_redemption",
        "redemption_id": red["redemption_id"],
        "label": f"Loyalty points ({int(red['points'])} pts)",
        "off_paise": value,
        "off_rupees": to_rupees_str(paise(value)),
        "points": int(red["points"]),
    }


def _read_redemption(doc: dict[str, Any], rid: str) -> dict[str, Any]:
    red = (doc.get("redemptions") or {}).get(rid)
    if not isinstance(red, dict):
        raise LoyaltyRefused(
            R_NO_REDEMPTION,
            f"this counter has no redemption {rid!r}. Nothing was changed.",
            status=404)
    return red


@router.get("/loyalty/redemptions/{redemption_id}")
def redemption_ep(redemption_id: str) -> JSONResponse:
    try:
        rid = _require_redemption_id(redemption_id)
        doc = load_doc()
        red = _read_redemption(doc, rid)
        return _ok(redemption=red, line=_line_for(red),
                   applied=bool(red.get("applied")), till_must=TILL_MUST)
    except LoyaltyRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/loyalty/redemptions/{redemption_id}/apply")
async def redemption_apply_ep(redemption_id: str, request: Request
                              ) -> JSONResponse:
    """Body: {session_id}. The till says which bill the line went on.

    THIS IS THE DEBIT. The balance is re-derived and re-checked here, because
    two proposals can each fit inside a balance that cannot cover both. A bill
    that has already settled is refused: the gateway took the full amount, and
    a discount recorded after money moved is a discount nobody received.
    """
    try:
        rid = _require_redemption_id(redemption_id)
        body = await _json_body(request)
        sid = _require_session_id(body.get("session_id"))
        view = derive()
        doc = view["doc"]
        red = _read_redemption(doc, rid)
        if red.get("applied"):
            raise LoyaltyRefused(
                R_ALREADY_APPLIED,
                f"redemption {rid} was already put on bill "
                f"{red.get('session_id')!r} at {red.get('applied_at')}. Points "
                f"leave a balance once.")
        state = view["sessions"].get(sid)
        if state is None:
            records, _ = _manage().read_chain()
            bills = _manage().bills_from(records)
            state = _bill_state(bills.get(sid), _settled_amounts(records).get(sid))
        if state["settled"]:
            raise LoyaltyRefused(
                R_BILL_SETTLED,
                f"bill {sid} already settled for "
                f"{state['settled_rupees'] or 'an unrecorded amount'}; the "
                f"gateway took the full amount. A discount cannot be applied "
                f"after the money moved. Nothing was changed.")
        phone = normalise_phone(str(red.get("phone") or ""))
        points = int(_whole(red.get("points")) or 0)
        bal = balance_of(phone, view)
        available = int(bal["balance_points"])
        if points > available:
            raise LoyaltyRefused(
                R_EXCEEDS_BALANCE,
                f"the number ending {_phone_tail(phone)} has {available} points "
                f"now and this proposal needs {points}. Another redemption was "
                f"applied since it was proposed. Nothing was changed.")
        now = _now_iso()
        red["applied"] = True
        red["applied_at"] = now
        red["session_id"] = sid
        red["balance_after_points"] = available - points
        doc["redemptions"][rid] = red
        save_doc(doc)
        head = _audit("redemption.applied", redemption_id=rid, session_id=sid,
                      phone_tail=_phone_tail(phone), points=points,
                      value_paise=int(paise(_whole(red.get("value_paise")) or 0)),
                      balance_before_points=available,
                      balance_after_points=available - points,
                      applied=True, minted=False)
        return _ok(redemption=red, line=_line_for(red), applied=True,
                   session_id=sid, bill=state,
                   balance_before_points=available,
                   balance_after_points=available - points,
                   audited=head is not None, till_must=TILL_MUST,
                   note=(f"{points} points left the balance and are recorded "
                         f"against bill {sid}. The money service still "
                         f"re-prices the basket itself; see till_must."))
    except LoyaltyRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(LoyaltyRefused(
            R_CHAIN_UNAVAILABLE,
            f"an amount in the chain is not integer paise ({exc}). Nothing "
            f"was changed."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/loyalty/health")
def health_ep() -> JSONResponse:
    """Where the files are, and whether both chains verify."""
    try:
        p = loyalty_path()
        a = audit_path()
        ok, lines, head, error = verify(a)
        doc: Optional[dict[str, Any]] = None
        file_error: Optional[str] = None
        try:
            doc = load_doc()
        except LoyaltyRefused as exc:
            file_error = exc.detail
        chain_ok = None
        chain_path = None
        try:
            _, chain = _manage().read_chain()
            chain_ok = bool(chain.get("ok", True))
            chain_path = chain.get("path")
        except LoyaltyRefused:
            pass
        return _ok(
            module="loyalty",
            file=str(p), exists=p.exists(), file_error=file_error,
            audit_file=str(a),
            audit={"ok": ok, "lines": lines, "head": head, "error": error},
            money_chain={"ok": chain_ok, "path": chain_path},
            shop_dir=str(shop_dir()),
            rules=current_rules(doc) if doc else None,
            attachments=len((doc or {}).get("attachments") or {}),
            redemptions=len((doc or {}).get("redemptions") or {}),
            earns_on="bills the gateway's signed webhook settled, and nothing else",
        )
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


__all__ = [
    "LoyaltyRefused",
    "MAX_PAISE_PER_POINT",
    "MAX_POINTS_PER_REDEMPTION",
    "MAX_POINTS_PER_RUPEE",
    "MIN_PHONE_DIGITS",
    "TILL_MUST",
    "audit_path",
    "balance_of",
    "current_rules",
    "derive",
    "load_doc",
    "loyalty_path",
    "normalise_phone",
    "router",
    "save_doc",
    "save_rules",
    "shop_dir",
]
