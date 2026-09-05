"""KHATA (खाता) — the udhaar book. Collected by Razorpay; drops only on a signed webhook.

Every kirana runs on credit written in a notebook. "Sharma ji, 650, likh do" —
and the money flow the till could not see is the one the shop lives on. This
module is that notebook, and it is the one part of the counter where a bill
closes with NO colour: not green (nothing settled), not amber (nothing is
being abstained on), not red (nothing was refused). A bill on the book is a
debt in neutral ink, and it stays neutral until the gateway says otherwise.

THREE FACTS, THREE PLACES
=========================
  WHO the customer is — a name and a phone, keyed the way `customers.py` keys
  a phone — lives HERE, in `<shop>/khata.json`, behind an opaque `bk_…` id.
  The money service never sees a name or a number for a booking; it sees the
  id. For a COLLECT it is handed the contact ONCE, so that Razorpay can send
  the reminders, and it keeps nothing (paisa scrubs the entity it gets back).

  WHAT is owed — every bill closed onto a book and every capture against it
  — lives in the money service's kernel, and reaches this screen through the
  hash-chained audit log the way loyalty.py reads settlement: `gawaah/
  manage.py` reads and verifies the chain, and this module folds the
  kernel's own lines (`intent.booked`, `capture.credited`, `capture.parked`,
  `collection.*`) by book id. Nothing here keeps a second copy of a balance.

      outstanding = sum(booked bills) - sum(credited captures)   integers, always

  WHAT THIS MODULE DID — the press of ON THE BOOK, the press of COLLECT —
  is chained in `<shop>/khata.audit.jsonl` by `gawaah/ledger.py`, verifiable
  by the same `verify()` as the money log. Phone numbers do not reach the
  chain; the last four digits do.

WHAT NEVER HAPPENS HERE
=======================
No route in this file mints, settles, refunds or nets anything. COLLECT asks
paisa for ONE Payment Link (accept_partial, reminders on, SMS by Razorpay) and
renders the opaque `short_url` it gets back; a second COLLECT while one is
open is refused BY NAME by paisa (`collection_link_already_open`) and the
refusal is shown as it came. A capture that does not reconcile is parked and
NAMED by the kernel, and this screen shows it parked — it does not subtract
it, round it, or hide it.

MOUNTING. The router carries NO prefix; these paths are absolute::

    GET  /khata                          every household, the value line
    GET  /khata/lookup?q=                a name or a number said at the counter
    GET  /khata/{book_id}                one household's ledger
    POST /khata/book                     close the bill on the counter onto a book
    POST /khata/{book_id}/collect        ONE link for the outstanding balance
    GET  /khata/{book_id}/qr/{collection_id}   a QR of the gateway's own link
    POST /khata/sim/pay                  simulator only: a partial payment
    GET  /khata/health
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
from fastapi.responses import JSONResponse, Response

from .ledger import Ledger, verify
from .money import MoneyError, paise, to_rupees_str

router = APIRouter()


# --------------------------------------------------------------- refusals --

R_BAD_BODY = "khata_body_not_json"
R_NO_PHONE = "phone_missing"
R_BAD_PHONE = "phone_not_a_number"
R_SHORT_PHONE = "phone_too_short"
R_PHONE_TOO_LONG = "phone_too_long"
R_NO_NAME = "name_missing"
R_NAME_TOO_LONG = "name_too_long"
R_NO_SESSION = "session_id_missing"
R_BAD_SESSION = "session_id_malformed"
R_NO_SCAN = "scan_id_missing"
R_BAD_AMOUNT = "amount_not_integer_paise"
R_NO_BOOK = "no_such_household"
R_BAD_BOOK = "book_id_malformed"
R_NO_COLLECTION = "no_such_collection"
R_BAD_COLLECTION = "collection_id_malformed"
R_SEVERAL = "several_households_match"
R_NO_QUERY = "nothing_to_look_up"
R_CHAIN_UNAVAILABLE = "audit_chain_unavailable"
R_FILE_UNREADABLE = "khata_file_unreadable"
R_FILE_UNWRITABLE = "khata_file_unwritable"
R_PAISA = "paisa_unreachable"
R_REFUSED_QR = "refused_to_encode_this_string"
R_NO_LINK = "no_payable_link_on_this_collection"
R_INTERNAL = "khata_internal_error"

KHATA_FILENAME = "khata.json"
KHATA_AUDIT_FILENAME = "khata.audit.jsonl"
KHATA_FORMAT = 1

MIN_PHONE_DIGITS = 7
MAX_PHONE_DIGITS = 15
MAX_PHONE_TEXT = 24
MAX_NAME = 80
MAX_QUERY = 60
MAX_HOUSEHOLDS = 500

BOOK_ID_RE = re.compile(r"^bk_[0-9a-f]{8,64}$")
COLLECTION_ID_RE = re.compile(r"^col_[0-9a-f]{8,64}$")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,79}$")
SCAN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")

#: Honorifics and particles that surround a name when it is said out loud.
#: "Sharma ji ka" is Sharma. Stripped from a lookup, never from a stored name.
_SAID_AROUND_A_NAME = frozenset({
    "ji", "jee", "sahab", "saheb", "sahib", "bhai", "bhaiya", "didi", "aunty",
    "uncle", "babu", "da", "dada", "boudi", "ka", "ke", "ki", "ko", "ka",
    "er", "r", "the", "mr", "mrs", "shri", "sri", "smt",
})

#: Hosts a payable link may live on — the till's own allowlist, asked of the
#: till when it is loaded so there is one list and not two.
#: Used only when `tools.upload_app.LINK_HOSTS` cannot be imported. It must
#: carry the simulator's unresolvable host for the same reason that list does
#: — otherwise a counter in sim mode refuses to show its own collection link.
_LINK_HOSTS_FALLBACK = ("rzp.io", "razorpay.com", "rzp.link",
                        "pay.gawaah-sim.invalid")


class KhataRefused(Exception):
    """A named refusal with a sentence a shopkeeper can act on."""

    def __init__(self, reason: str, detail: str, status: int = 400,
                 **extra: Any) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.status = status
        self.extra = dict(extra)


def _refusal(exc: KhataRefused) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "reason": exc.reason, "detail": exc.detail,
         "settles_money": False, **exc.extra},
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

from gawaah import till_ref as _till_ref

_TILL_NAMES = _till_ref.TILL_NAMES


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def shop_dir() -> Path:
    """The shopkeeper's directory — the till's own answer, never a second one.
    Same rule as loyalty.py: a loaded till's `store_dir()` wins, then the
    environment, then results/shop."""
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


def khata_path() -> Path:
    return shop_dir() / KHATA_FILENAME


def audit_path() -> Path:
    """This module's own hash-chained log. Not results/audit.jsonl: that file
    has one writer, the kernel, in another process."""
    return shop_dir() / KHATA_AUDIT_FILENAME


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _phone_tail(phone: str) -> str:
    return phone[-4:] if len(phone) >= 4 else phone


def phone_masked(phone: str) -> str:
    """98xxxx1234 — what a list shows beside a name. The full number is on
    the detail view, where one person was asked for by name."""
    if len(phone) <= 4:
        return phone
    if len(phone) <= 6:
        return "x" * (len(phone) - 4) + phone[-4:]
    return phone[:2] + "x" * (len(phone) - 6) + phone[-4:]


def _audit(event: str, **fields: Any) -> Optional[str]:
    """Append one line to this module's own chain. None if it failed; every
    endpoint that gets None says `audited: false` rather than pretending."""
    try:
        return Ledger(audit_path()).append(
            ts=_now_iso(), module="khata", event=event, **fields)
    except Exception:  # noqa: BLE001 - a failed audit must not lose a change
        return None


# ---------------------------------------------------------------- the file --


def _blank_doc() -> dict[str, Any]:
    return {"format": KHATA_FORMAT, "books": {}}


def load_doc() -> dict[str, Any]:
    p = khata_path()
    if not p.exists():
        return _blank_doc()
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - a corrupt file is a named answer
        raise KhataRefused(
            R_FILE_UNREADABLE,
            f"{p} exists but could not be read ({type(exc).__name__}: {exc}). "
            f"Nothing was guessed in its place.") from None
    if not isinstance(doc, dict):
        raise KhataRefused(R_FILE_UNREADABLE, f"{p} is not a khata document.")
    base = _blank_doc()
    if isinstance(doc.get("books"), dict):
        base["books"] = doc["books"]
    return base


def save_doc(doc: dict[str, Any]) -> Path:
    """Write via a temp file and rename, so a reader never sees half a file."""
    p = khata_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, sort_keys=True, indent=1) + "\n",
                       encoding="utf-8")
        os.replace(tmp, p)
    except OSError as exc:
        raise KhataRefused(
            R_FILE_UNWRITABLE,
            f"{p} could not be written ({type(exc).__name__}: {exc}). "
            f"Nothing was changed.") from None
    return p


# ------------------------------------------------------------ the person --


def normalise_phone(raw: Any) -> str:
    """customers.py's rule, asked of customers.py. One subscriber, one key."""
    try:
        from . import customers  # noqa: WPS433 - late; it may be mid-edit
        fn = getattr(customers, "normalise_phone", None)
        if fn is not None:
            return str(fn(raw))
    except Exception:  # noqa: BLE001
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
        raise KhataRefused(
            R_NO_PHONE,
            "no phone number was given. A book is kept against the number the "
            "customer says at the counter; a name alone is two Sharmas.")
    text = str(raw).strip()
    if len(text) > MAX_PHONE_TEXT:
        raise KhataRefused(
            R_PHONE_TOO_LONG,
            f"{len(text)} characters is longer than any phone number.")
    digits = normalise_phone(text)
    if not digits:
        raise KhataRefused(
            R_BAD_PHONE, f"{text!r} has no digits in it, so it is not a phone number.")
    if len(digits) < MIN_PHONE_DIGITS:
        raise KhataRefused(
            R_SHORT_PHONE,
            f"{text!r} has {len(digits)} digits in it. A number that can be "
            f"dialled has at least {MIN_PHONE_DIGITS}.")
    if len(digits) > MAX_PHONE_DIGITS:
        raise KhataRefused(
            R_BAD_PHONE, f"{text!r} has {len(digits)} digits; no number has more "
                         f"than {MAX_PHONE_DIGITS}.")
    return digits


def _require_name(raw: Any) -> str:
    text = " ".join(str(raw or "").split())
    if not text:
        raise KhataRefused(
            R_NO_NAME, "no name was given. Say who the bill is for.")
    if len(text) > MAX_NAME:
        raise KhataRefused(
            R_NAME_TOO_LONG, f"that name is {len(text)} characters; the cap is {MAX_NAME}.")
    return text


def _require_session_id(raw: Any) -> str:
    if raw is None or not str(raw).strip():
        raise KhataRefused(
            R_NO_SESSION,
            "no session id was given. The till has one for every bill; it is "
            "what ties the booking to the witness.")
    s = str(raw).strip()
    if not SESSION_ID_RE.match(s):
        raise KhataRefused(
            R_BAD_SESSION,
            f"{s!r} is not a session id from this counter.")
    return s


def _require_scan_id(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        raise KhataRefused(
            R_NO_SCAN,
            "no scan id was given. A bill goes on the book only from a witness "
            "the counter photographed and wrote down — the same evidence a "
            "charge needs.")
    if not SCAN_ID_RE.match(s):
        raise KhataRefused(R_NO_SCAN, f"{s!r} is not a scan id from this counter.")
    return s


def _require_amount(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise KhataRefused(
            R_BAD_AMOUNT,
            f"{raw!r} is not integer paise. Money on this counter is a whole "
            f"number of paise, never a decimal.")
    try:
        n = int(paise(raw))
    except MoneyError as exc:
        raise KhataRefused(R_BAD_AMOUNT, str(exc)) from None
    if n <= 0:
        raise KhataRefused(R_BAD_AMOUNT, f"a bill on the book must be positive, got {n}.")
    return n


def _require_book_id(raw: Any) -> str:
    s = str(raw or "").strip()
    if not BOOK_ID_RE.match(s):
        raise KhataRefused(R_BAD_BOOK, f"{raw!r} is not a household id from this book.")
    return s


def _require_collection_id(raw: Any) -> str:
    s = str(raw or "").strip()
    if not COLLECTION_ID_RE.match(s):
        raise KhataRefused(R_BAD_COLLECTION, f"{raw!r} is not a collection id.")
    return s


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise KhataRefused(R_BAD_BODY, "this request's body is not JSON.") from None
    if not isinstance(body, dict):
        raise KhataRefused(R_BAD_BODY, "this request's body must be a JSON object.")
    return body


def book_for_phone(doc: dict[str, Any], phone: str) -> Optional[dict[str, Any]]:
    for rec in (doc.get("books") or {}).values():
        if isinstance(rec, dict) and rec.get("phone") == phone:
            return rec
    return None


def open_book(doc: dict[str, Any], phone: str, name: str,
              phone_as_given: str) -> tuple[dict[str, Any], bool]:
    """The book for this number, created if there is none. Returns (rec, new).

    The newest name said wins the label and every name ever said is kept, as
    customers.py does: the shopkeeper sees a number change hands rather than
    having it merged out of sight.
    """
    rec = book_for_phone(doc, phone)
    if rec is None:
        rec = {
            "book_id": "bk_" + secrets.token_hex(8),
            "phone": phone,
            "phone_as_given": phone_as_given,
            "name": name,
            "names_seen": [name],
            "opened_at": _now_iso(),
        }
        doc.setdefault("books", {})[rec["book_id"]] = rec
        return rec, True
    if name and name != rec.get("name"):
        rec["name"] = name
        seen = list(rec.get("names_seen") or [])
        if name not in seen:
            seen.append(name)
        rec["names_seen"] = seen
    return rec, False


def _name_tokens(text: str) -> list[str]:
    toks = re.findall(r"[^\W\d_]+", (text or "").casefold())
    return [t for t in toks if t not in _SAID_AROUND_A_NAME]


def lookup(doc: dict[str, Any], q: str) -> list[dict[str, Any]]:
    """Households a phrase could mean. By digits if it has four or more; else
    by name, every token of the query in the name. Never a fuzzy guess."""
    q = " ".join((q or "").split())
    digits = re.sub(r"\D", "", q)
    out: list[dict[str, Any]] = []
    books = [r for r in (doc.get("books") or {}).values() if isinstance(r, dict)]
    if len(digits) >= 4:
        for r in books:
            if digits in str(r.get("phone") or ""):
                out.append(r)
        return out
    want = _name_tokens(q)
    if not want:
        return []
    for r in books:
        names = [str(r.get("name") or "")] + list(r.get("names_seen") or [])
        for nm in names:
            have = set(_name_tokens(nm))
            if all(any(h.startswith(w) for h in have) for w in want):
                out.append(r)
                break
    return out


# ------------------------------------------------------------- the chain --
#
# Read through gawaah/manage.py, which verifies the money chain and is what
# History, Today and Loyalty derive from. Imported late for the same reason
# loyalty.py imports it late.


def _manage() -> Any:
    try:
        from . import manage  # noqa: WPS433 - deliberately late
    except Exception as exc:  # noqa: BLE001
        raise KhataRefused(
            R_CHAIN_UNAVAILABLE,
            f"gawaah/manage.py is not importable ({type(exc).__name__}: {exc}), "
            f"and it is the module that reads the money chain. No balance can "
            f"be derived without it.") from None
    if not hasattr(manage, "read_chain"):
        raise KhataRefused(
            R_CHAIN_UNAVAILABLE,
            "gawaah/manage.py has no read_chain, so the money chain cannot be "
            "read the way every other screen reads it.")
    return manage


def _whole(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _parse_ts(s: Any) -> Optional[_dt.datetime]:
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        d = _dt.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo is not None else d.replace(tzinfo=_dt.timezone.utc)


def _days_since(ts: Any, now: _dt.datetime) -> Optional[int]:
    d = _parse_ts(ts)
    if d is None:
        return None
    return max(0, int((now - d).days))


def _same_local_month(ts: Any, now_local: _dt.datetime) -> bool:
    d = _parse_ts(ts)
    if d is None:
        return False
    loc = d.astimezone(now_local.tzinfo)
    return (loc.year, loc.month) == (now_local.year, now_local.month)


def fold_chain(records: tuple[dict, ...]) -> dict[str, dict[str, Any]]:
    """The kernel's KHATA lines, folded by book id. Nothing is summed twice.

    Every figure names the line it came from:
      intent.booked      -> a bill on the book (session_id, amount_paise)
      capture.credited   -> paise that arrived, keyed on the signed event id
      capture.parked     -> paise the kernel refused to net; a person's row
      collection.*       -> one link's life: created, calling, open, updated,
                            paid, closed. The LAST line for an id wins.
    """
    books: dict[str, dict[str, Any]] = {}

    def bucket(book_id: str) -> dict[str, Any]:
        b = books.get(book_id)
        if b is None:
            b = {"book_id": book_id, "bills": [], "captures": [],
                 "collections": {}, "minted": {}}
            books[book_id] = b
        return b

    for rec in records:
        if rec.get("module") not in ("kernel", "paisa"):
            continue
        book_id = rec.get("book_id")
        if not isinstance(book_id, str) or not BOOK_ID_RE.match(book_id):
            continue
        event = rec.get("event")
        ts = rec.get("ts")
        b = bucket(book_id)
        if rec.get("module") == "kernel" and event == "intent.booked":
            amt = _whole(rec.get("amount_paise"))
            if amt is None:
                continue
            b["bills"].append({
                "kind": "bill",
                "session_id": rec.get("session_id"),
                "nonce": rec.get("nonce"),
                "amount_paise": amt,
                "amount_rupees": to_rupees_str(paise(amt)),
                "at": ts,
            })
        elif rec.get("module") == "kernel" and event in ("capture.credited", "capture.parked"):
            amt = _whole(rec.get("amount_paise"))
            if amt is None:
                continue
            b["captures"].append({
                "kind": "capture",
                "event_id": rec.get("event_id"),
                "collection_id": rec.get("collection_id"),
                "payment_id": rec.get("payment_id"),
                "amount_paise": amt,
                "amount_rupees": to_rupees_str(paise(amt)),
                "credited": event == "capture.credited",
                "parked": event == "capture.parked",
                "reason": rec.get("reason"),
                "razorpay_event": rec.get("razorpay_event"),
                "final": bool(rec.get("final")),
                "outstanding_after_paise": _whole(rec.get("outstanding_paise")),
                "at": ts,
            })
        elif rec.get("module") == "kernel" and isinstance(event, str) and event.startswith("collection."):
            cid = rec.get("collection_id")
            if not isinstance(cid, str):
                continue
            cur = b["collections"].get(cid) or {"kind": "collection",
                                                "collection_id": cid,
                                                "opened_at": ts}
            cur.update({
                "state": rec.get("to_state") or cur.get("state"),
                "amount_paise": _whole(rec.get("amount_paise")),
                "captured_paise": _whole(rec.get("captured_paise")) or 0,
                "short_url": rec.get("short_url") or cur.get("short_url"),
                "payment_link_id": rec.get("payment_link_id") or cur.get("payment_link_id"),
                "expire_by": _whole(rec.get("expire_by")) if rec.get("expire_by") is not None else cur.get("expire_by"),
                "needs_human": bool(rec.get("needs_human")),
                "reason": rec.get("reason"),
                "at": ts,
            })
            amt = cur.get("amount_paise")
            cur["amount_rupees"] = to_rupees_str(paise(amt)) if amt is not None else None
            cur["captured_rupees"] = to_rupees_str(paise(cur["captured_paise"]))
            b["collections"][cid] = cur
        elif rec.get("module") == "paisa" and event == "collection.minted":
            cid = rec.get("collection_id")
            if isinstance(cid, str):
                b["minted"][cid] = {
                    "at": ts,
                    "reminder_enable": bool(rec.get("reminder_enable")),
                    "notify_sms": bool(rec.get("notify_sms")),
                    "first_min_partial_amount": _whole(rec.get("first_min_partial_amount")),
                }
    return books


#: Collection states in which the link is still payable.
LIVE_COLLECTION_STATES = frozenset({"NEW", "CALLING", "OPEN", "INDETERMINATE"})


def household_figures(b: dict[str, Any], now: _dt.datetime) -> dict[str, Any]:
    """One book's figures, from its folded lines. Integers only.

    `oldest_days` is a reading aid, not a balance: captures are against the
    BOOK, not against a bill, so "which bill is still unpaid" is a convention.
    The convention here is oldest-first — the customer's payments cover the
    oldest bill first — and it is named on the response as `oldest_by`. The
    outstanding figure itself does not depend on it.
    """
    bills = sorted(b["bills"], key=lambda x: (str(x.get("at") or ""), str(x.get("session_id") or "")))
    caps = sorted(b["captures"], key=lambda x: (str(x.get("at") or ""), str(x.get("event_id") or "")))
    booked = sum(int(x["amount_paise"]) for x in bills)
    credited = sum(int(x["amount_paise"]) for x in caps if x["credited"])
    parked = sum(int(x["amount_paise"]) for x in caps if x["parked"])
    outstanding = booked - credited

    cover = credited
    oldest_at: Optional[str] = None
    for bill in bills:
        if cover >= int(bill["amount_paise"]):
            cover -= int(bill["amount_paise"])
            continue
        oldest_at = bill.get("at")
        break
    last_capture = next((c for c in reversed(caps) if c["credited"]), None)
    cols = sorted(b["collections"].values(), key=lambda c: str(c.get("opened_at") or ""))
    # What each link has collected is the SUM OF ITS CREDITED CAPTURES, not a
    # field read off the last `collection.*` line: the kernel writes a
    # collection line when the link's STATE moves, and a plain credit on an
    # open link moves nothing but the money. Read off the line, an open link
    # with ₹200 paid on it said "₹0.00 paid so far".
    for col in cols:
        got = sum(int(c["amount_paise"]) for c in caps
                  if c["credited"] and c.get("collection_id") == col.get("collection_id"))
        col["captured_paise"] = got
        col["captured_rupees"] = to_rupees_str(paise(got))
        amt = col.get("amount_paise")
        col["still_due_paise"] = (int(amt) - got) if amt is not None else None
        col["still_due_rupees"] = (to_rupees_str(paise(int(amt) - got))
                                   if amt is not None else None)
    live = next((c for c in reversed(cols) if c.get("state") in LIVE_COLLECTION_STATES), None)
    now_local = now.astimezone()
    month_paise = sum(int(c["amount_paise"]) for c in caps
                      if c["credited"] and _same_local_month(c.get("at"), now_local))
    reminded_month = sum(1 for cid, m in b["minted"].items()
                         if m.get("reminder_enable") and _same_local_month(m.get("at"), now_local))
    return {
        "book_id": b["book_id"],
        "bills": len(bills),
        "booked_paise": booked,
        "booked_rupees": to_rupees_str(paise(booked)),
        "captured_paise": credited,
        "captured_rupees": to_rupees_str(paise(credited)),
        "parked_paise": parked,
        "parked_rupees": to_rupees_str(paise(parked)),
        "outstanding_paise": outstanding,
        "outstanding_rupees": to_rupees_str(paise(outstanding)),
        "oldest_at": oldest_at if outstanding > 0 else None,
        "oldest_days": _days_since(oldest_at, now) if (outstanding > 0 and oldest_at) else None,
        "oldest_by": "oldest_bill_first",
        "last_capture": last_capture,
        "last_booked_at": bills[-1].get("at") if bills else None,
        "live_collection": live,
        "collections": len(cols),
        "collected_this_month_paise": month_paise,
        "collected_this_month_rupees": to_rupees_str(paise(month_paise)),
        "reminder_links_this_month": reminded_month,
        "needs_human": parked > 0 or any(bool(c.get("needs_human")) for c in cols),
    }


def derive() -> dict[str, Any]:
    """Every household with its figures, fresh from the chain and the file."""
    doc = load_doc()
    manage = _manage()
    try:
        records, chain = manage.read_chain()
    except Exception as exc:  # noqa: BLE001
        raise KhataRefused(
            R_CHAIN_UNAVAILABLE,
            f"the audit chain could not be read ({type(exc).__name__}: {exc}). "
            f"No balance was invented in its place.") from None
    folded = fold_chain(records)
    now = _now()
    households: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for book_id, rec in (doc.get("books") or {}).items():
        if not isinstance(rec, dict):
            continue
        b = folded.get(book_id) or {"book_id": book_id, "bills": [], "captures": [],
                                     "collections": {}, "minted": {}}
        fig = household_figures(b, now)
        row = {
            "book_id": book_id,
            "name": str(rec.get("name") or ""),
            "phone": str(rec.get("phone") or ""),
            "phone_masked": phone_masked(str(rec.get("phone") or "")),
            "phone_tail": _phone_tail(str(rec.get("phone") or "")),
            "names_seen": list(rec.get("names_seen") or []),
            "opened_at": rec.get("opened_at"),
            **fig,
        }
        households.append(row)
        by_id[book_id] = {"row": row, "folded": b}
    # Books the chain knows and the file does not — a booking made against an
    # id this shop dir never minted (another shop dir, a wiped file). Shown,
    # so a balance is never silently lost, with no name because none is known.
    for book_id, b in folded.items():
        if book_id in by_id:
            continue
        fig = household_figures(b, now)
        row = {"book_id": book_id, "name": "", "phone": "", "phone_masked": "",
               "phone_tail": "", "names_seen": [], "opened_at": None,
               "unnamed": True, **fig}
        households.append(row)
        by_id[book_id] = {"row": row, "folded": b}
    households.sort(key=lambda r: (-int(r["outstanding_paise"]),
                                   -(r["oldest_days"] or 0), r["book_id"]))
    return {"households": households, "by_id": by_id, "chain": chain, "doc": doc,
            "now": now}


def value_line(households: list[dict[str, Any]]) -> dict[str, Any]:
    """The one sentence a judge hears, as figures. All derived, all integers."""
    with_balance = [h for h in households if int(h["outstanding_paise"]) > 0]
    outstanding = sum(int(h["outstanding_paise"]) for h in with_balance)
    month = sum(int(h["collected_this_month_paise"]) for h in households)
    reminded = sum(int(h["reminder_links_this_month"]) for h in households)
    oldest = max((int(h["oldest_days"]) for h in with_balance
                  if h.get("oldest_days") is not None), default=0)
    parked = sum(int(h["parked_paise"]) for h in households)
    return {
        "outstanding_paise": outstanding,
        "outstanding_rupees": to_rupees_str(paise(outstanding)),
        "households": len(with_balance),
        "households_total": len(households),
        "oldest_days": oldest,
        "collected_this_month_paise": month,
        "collected_this_month_rupees": to_rupees_str(paise(month)),
        "reminder_links_this_month": reminded,
        "parked_paise": parked,
        "parked_rupees": to_rupees_str(paise(parked)),
        "links_open": sum(1 for h in households if h.get("live_collection")),
    }


def _chain_block(view: dict[str, Any]) -> dict[str, Any]:
    c = view["chain"]
    return {
        "ok": bool(c.get("ok", True)),
        "exists": bool(c.get("exists")),
        "lines_verified": int(c.get("lines_verified") or 0),
        "error": c.get("error"),
        "path": c.get("path"),
    }


# ----------------------------------------------------------- the money --
#
# The money service is asked, never imitated. GETs go through manage.paisa_get
# (one implementation of "read paisa"); the two POSTs below are the only
# writes this module makes to it, and both forward a request paisa re-derives
# from its own tables before it acts.


def _paisa_base() -> str:
    return str(getattr(_manage(), "PAISA_BASE", os.environ.get(
        "GAWAAH_PAISA_URL", "http://127.0.0.1:8788")))


def _paisa_get(path: str) -> tuple[int, dict[str, Any]]:
    return _manage().paisa_get(path)


def _paisa_post(path: str, body: dict[str, Any], *, timeout_s: int = 30
                ) -> tuple[int, dict[str, Any]]:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"{_paisa_base()}{path}", data=json.dumps(body).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return exc.code, {"ok": False, "reason": f"paisa returned HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001
        return 503, {
            "ok": False, "reason": R_PAISA,
            "detail": (f"The money service did not answer at {_paisa_base()} "
                       f"({type(exc).__name__}). Nothing was booked or minted."),
        }


def _paisa_refusal(status: int, body: dict[str, Any], fallback: str) -> KhataRefused:
    """paisa's own reason and sentence, carried out by name."""
    reason = str(body.get("error") or body.get("reason") or fallback)
    detail = str(body.get("detail") or f"the money service answered HTTP {status}.")
    extra = {k: v for k, v in body.items()
             if k not in ("error", "reason", "detail", "ok", "minted", "module")}
    return KhataRefused(reason, detail, status=409 if status < 500 else status, **extra)


# ----------------------------------------------------------------- routes --


def _ok(**fields: Any) -> JSONResponse:
    return JSONResponse({"ok": True, "settles_money": False, **fields})


KHATA_NOTE = ("Every rupee here is derived from the money service's hash-chained "
              "log at the moment you asked. A balance drops only on a "
              "signature-verified webhook. A capture that does not reconcile "
              "is parked and named, never netted.")


@router.get("/khata")
def khata_ep() -> JSONResponse:
    """Every household with a book, worst balance first, and the value line."""
    try:
        view = derive()
        rows = view["households"]
        return _ok(
            value=value_line(rows),
            households=rows[:MAX_HOUSEHOLDS],
            count=len(rows),
            truncated=len(rows) > MAX_HOUSEHOLDS,
            chain=_chain_block(view),
            note=KHATA_NOTE,
        )
    except KhataRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(KhataRefused(
            R_CHAIN_UNAVAILABLE,
            f"an amount in the chain is not integer paise ({exc}). No balance "
            f"was derived."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/khata/lookup")
def lookup_ep(q: str | None = None) -> JSONResponse:
    """A name or a number said at the counter. Finding nobody is an answer."""
    try:
        text = " ".join(str(q or "").split())
        if not text:
            raise KhataRefused(R_NO_QUERY, "say a name or a phone number to look up.")
        if len(text) > MAX_QUERY:
            raise KhataRefused(R_NO_QUERY, f"that is {len(text)} characters; a name "
                                           f"or a number is shorter than {MAX_QUERY}.")
        view = derive()
        found = lookup(view["doc"], text)
        rows = [view["by_id"][r["book_id"]]["row"] for r in found
                if r.get("book_id") in view["by_id"]]
        return _ok(asked_for=text, matches=rows, count=len(rows),
                   matched_on=("phone" if len(re.sub(r"\D", "", text)) >= 4 else "name"))
    except KhataRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001
        return _crash(exc)


@router.get("/khata/health")
def health_ep() -> JSONResponse:
    try:
        p = khata_path()
        a = audit_path()
        ok, lines, head, error = verify(a)
        doc: Optional[dict[str, Any]] = None
        file_error: Optional[str] = None
        try:
            doc = load_doc()
        except KhataRefused as exc:
            file_error = exc.detail
        chain_ok = None
        chain_path = None
        try:
            _, chain = _manage().read_chain()
            chain_ok = bool(chain.get("ok", True))
            chain_path = chain.get("path")
        except KhataRefused:
            pass
        return _ok(
            module="khata",
            file=str(p), exists=p.exists(), file_error=file_error,
            audit_file=str(a),
            audit={"ok": ok, "lines": lines, "head": head, "error": error},
            money_chain={"ok": chain_ok, "path": chain_path},
            shop_dir=str(shop_dir()),
            households=len((doc or {}).get("books") or {}),
            paisa=_paisa_base(),
            drops_on="a signature-verified webhook crediting a capture, and nothing else",
        )
    except Exception as exc:  # noqa: BLE001
        return _crash(exc)



def _detail(view: dict[str, Any], book_id: str) -> dict[str, Any]:
    ent = view["by_id"].get(book_id)
    if ent is None:
        raise KhataRefused(
            R_NO_BOOK,
            f"this book has no household {book_id}. A household appears when a "
            f"bill is put on its book at the till.", status=404)
    row, folded = ent["row"], ent["folded"]
    entries: list[dict[str, Any]] = list(folded["bills"]) + list(folded["captures"]) + \
        list(folded["collections"].values())
    entries.sort(key=lambda e: (str(e.get("at") or ""), str(e.get("session_id") or e.get("event_id") or e.get("collection_id") or "")),
                 reverse=True)
    return {**row, "entries": entries,
            "collections_detail": sorted(folded["collections"].values(),
                                         key=lambda c: str(c.get("opened_at") or ""),
                                         reverse=True)}


@router.get("/khata/{book_id}")
def household_ep(book_id: str) -> JSONResponse:
    try:
        bid = _require_book_id(book_id)
        view = derive()
        body = _detail(view, bid)
        live = body.get("live_collection")
        if live and live.get("collection_id"):
            body["qr_url"] = f"/khata/{bid}/qr/{live['collection_id']}"
        return _ok(**body, chain=_chain_block(view), note=KHATA_NOTE)
    except KhataRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001
        return _crash(exc)


@router.post("/khata/book")
async def book_ep(request: Request) -> JSONResponse:
    """Body: {session_id, phone, name, amount_paise, scan_id}. ON THE BOOK.

    The book is opened (or found) for the phone HERE; the money service is
    then asked to close the bill onto its id, and it re-derives the amount
    from the witness before it agrees. Only after paisa has said BOOKED is a
    line appended to this module's chain — a booking that paisa refused is
    not a booking, and the refusal is carried out by name.
    """
    try:
        body = await _json_body(request)
        sid = _require_session_id(body.get("session_id"))
        phone = _require_phone(body.get("phone"))
        name = _require_name(body.get("name"))
        amount = _require_amount(body.get("amount_paise"))
        scan_id = _require_scan_id(body.get("scan_id"))
        doc = load_doc()
        rec, new = open_book(doc, phone, name, str(body.get("phone") or "").strip())
        status, ans = _paisa_post("/book", {
            "session_id": sid, "amount_paise": amount,
            "scan": {"scan_id": scan_id}, "book_id": rec["book_id"]})
        if status != 200 or not ans.get("booked"):
            raise _paisa_refusal(status, ans, "booking_refused")
        save_doc(doc)
        head = _audit("bill.booked", book_id=rec["book_id"], session_id=sid,
                      nonce=ans.get("nonce"), amount_paise=amount,
                      phone_tail=_phone_tail(phone), new_household=new,
                      replayed=bool(ans.get("replayed")), minted=False)
        return _ok(
            booked=True,
            book_id=rec["book_id"],
            name=rec["name"],
            phone=phone,
            phone_masked=phone_masked(phone),
            new_household=new,
            session_id=sid,
            nonce=ans.get("nonce"),
            state=ans.get("state"),
            amount_paise=amount,
            amount_rupees=to_rupees_str(paise(amount)),
            outstanding_paise=_whole(ans.get("outstanding_paise")),
            outstanding_rupees=(to_rupees_str(paise(ans["outstanding_paise"]))
                                if _whole(ans.get("outstanding_paise")) is not None else None),
            replayed=bool(ans.get("replayed")),
            audited=head is not None,
            colour="none",
            note=("Nothing settled and nothing was refused: this bill is a debt in "
                  "neutral ink. It drops only when the gateway's signed webhook "
                  "says money arrived against this household's collection link."),
        )
    except KhataRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001
        return _crash(exc)


@router.post("/khata/{book_id}/collect")
async def collect_ep(book_id: str) -> JSONResponse:
    """COLLECT: ask paisa for ONE link for this household's outstanding balance.

    The balance sent is the one this screen derived from the chain; paisa
    compares it against its own rows and mints for ITS figure or refuses. A
    second press while a link is open comes back `collection_link_already_open`
    from paisa, by name, with the open link on it.
    """
    try:
        bid = _require_book_id(book_id)
        view = derive()
        body = _detail(view, bid)
        outstanding = int(body["outstanding_paise"])
        customer: dict[str, str] = {}
        if body.get("phone"):
            customer["contact"] = str(body["phone"])
        if body.get("name"):
            customer["name"] = str(body["name"])
        status, ans = _paisa_post("/collect", {
            "book_id": bid, "amount_paise": outstanding,
            "customer": customer or None})
        if status != 200 or not ans.get("collection_id"):
            raise _paisa_refusal(status, ans, "collect_refused")
        head = _audit("collect.pressed", book_id=bid,
                      collection_id=ans.get("collection_id"),
                      amount_paise=outstanding, phone_tail=body.get("phone_tail"),
                      payment_link_id=ans.get("payment_link_id"), minted=True)
        return _ok(
            **{k: v for k, v in ans.items() if k not in ("ok", "settles_money")},
            name=body.get("name"),
            phone_masked=body.get("phone_masked"),
            qr_url=f"/khata/{bid}/qr/{ans['collection_id']}",
            audited=head is not None,
            note=("One link, for the whole balance, payable in parts. Razorpay "
                  "sends the reminders to the customer's number; this counter "
                  "sends nothing. Green appears here only when a signed webhook "
                  "credits a capture."),
        )
    except KhataRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001
        return _crash(exc)


def _looks_like_upi(url: str) -> bool:
    return url.lstrip("\x00-\x20 \t\r\n").lower().lstrip().startswith("upi:")


def _link_hosts() -> tuple[str, ...]:
    """The till's own allowlist when the till is loaded; the same tuple otherwise."""
    for name in _TILL_NAMES:
        mod = sys.modules.get(name)
        hosts = getattr(mod, "LINK_HOSTS", None) if mod is not None else None
        if isinstance(hosts, tuple) and hosts:
            return hosts
    return _LINK_HOSTS_FALLBACK


@router.get("/khata/{book_id}/qr/{collection_id}")
def collection_qr_ep(book_id: str, collection_id: str, px: int = 620):
    """A QR of the gateway's own `short_url` for one collection link.

    THE PAGE NEVER CHOOSES THE BYTES: this route fetches the collection from
    paisa, takes `short_url` off it, checks the host against the till's
    allowlist, and encodes THAT. The rules are `tools/upload_app.py`'s
    `/qr/link` rules, applied to a collection instead of a session, for the
    same reason: there is no code path anywhere in this program that builds a
    payment target locally (invariant 6). The simulator mints on a reserved
    `.invalid` host and this route refuses to encode it — that is correct, and
    the refusal is returned by name with the link beside it.
    """
    from urllib.parse import urlsplit

    try:
        bid = _require_book_id(book_id)
        cid = _require_collection_id(collection_id)
        status, col = _paisa_get(f"/collection/{cid}")
        if status == 404:
            raise KhataRefused(
                R_NO_COLLECTION,
                f"the money service has no collection {cid}. Nothing was encoded.",
                status=404)
        if status != 200 or not isinstance(col, dict):
            raise _paisa_refusal(status, col if isinstance(col, dict) else {},
                                 "money_service_error")
        if col.get("book_id") != bid:
            raise KhataRefused(
                R_NO_COLLECTION,
                f"collection {cid} is not on household {bid}. Nothing was encoded.",
                status=404)
        url = col.get("short_url")
        if not isinstance(url, str) or not url.strip():
            raise KhataRefused(
                R_NO_LINK,
                "this collection carries no short_url, so nothing payable exists "
                "to show. Nothing was encoded.")
        url = url.strip()
        if _looks_like_upi(url):
            raise KhataRefused(
                R_REFUSED_QR,
                "That string is a UPI payload, not a gateway link. This program "
                "does not encode payment targets it did not receive from the "
                "gateway, and will not start now.", short_url=url)
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        hosts = _link_hosts()
        if parts.scheme not in ("http", "https"):
            raise KhataRefused(R_REFUSED_QR,
                               f"A payable link must be http or https, not {parts.scheme!r}.",
                               short_url=url)
        if not re.fullmatch(r"[a-z0-9.-]+", host):
            raise KhataRefused(R_REFUSED_QR,
                               "That link's host is not a plain hostname, so where it "
                               "actually points cannot be agreed on. Nothing was encoded.",
                               short_url=url)
        if not any(host == h or host.endswith("." + h) for h in hosts):
            raise KhataRefused(
                R_REFUSED_QR,
                f"The link points at {host!r}, which is not one of the gateway "
                f"hosts a payable link may live on ({', '.join(hosts)}). Nothing "
                f"was encoded.", short_url=url, host=host)
        import cv2  # noqa: WPS433 - only the encoder, only here
        import numpy as np  # noqa: WPS433

        enc = cv2.QRCodeEncoder.create()
        q = enc.encode(url)
        q = (q * 255).astype(np.uint8) if q.max() <= 1 else q.astype(np.uint8)
        side = max(200, min(int(px), 1600))
        q = cv2.resize(q, (side, side), interpolation=cv2.INTER_NEAREST)
        pad = side // 14
        card = np.full((side + 2 * pad, side + 2 * pad), 255, np.uint8)
        card[pad:pad + side, pad:pad + side] = q
        ok, buf = cv2.imencode(".png", cv2.cvtColor(card, cv2.COLOR_GRAY2BGR))
        if not ok:
            raise KhataRefused(R_INTERNAL, "the QR would not encode")
        return Response(buf.tobytes(), media_type="image/png",
                        headers={"Cache-Control": "no-store",
                                 "X-Gawaah-Link-Host": host})
    except KhataRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001
        return _crash(exc)


@router.post("/khata/sim/pay")
async def sim_pay_ep(request: Request) -> JSONResponse:
    """Simulator only. Body: {collection_id, amount_paise?}.

    Forwards to paisa's `/sim/pay`, which refuses by name on the live gateway.
    It exists so a demo with no customer's phone in the room can show a
    partial payment arriving the only way one can: as a signed webhook.
    """
    try:
        body = await _json_body(request)
        cid = _require_collection_id(body.get("collection_id"))
        amt = body.get("amount_paise")
        if amt is not None:
            amt = _require_amount(amt)
        status, col = _paisa_get(f"/collection/{cid}")
        if status != 200 or not isinstance(col, dict) or not col.get("payment_link_id"):
            raise KhataRefused(R_NO_COLLECTION,
                               f"the money service has no payable collection {cid}.",
                               status=404)
        forward: dict[str, Any] = {"payment_link_id": str(col["payment_link_id"])}
        if amt is not None:
            forward["amount_paise"] = amt
        status, ans = _paisa_post("/sim/pay", forward)
        if status != 200:
            raise _paisa_refusal(status, ans, "sim_pay_refused")
        _audit("sim.paid", collection_id=cid, amount_paise=amt,
               link_status=ans.get("link_status"), simulated=True, minted=False)
        return _ok(**{k: v for k, v in ans.items() if k not in ("ok", "settles_money")},
                   collection_id=cid)
    except KhataRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001
        return _crash(exc)


__all__ = [
    "KhataRefused", "router", "shop_dir", "khata_path", "audit_path",
    "load_doc", "save_doc", "normalise_phone", "open_book", "book_for_phone",
    "lookup", "fold_chain", "household_figures", "derive", "value_line",
    "phone_masked",
]
