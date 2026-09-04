"""MILAN (मिलान, "matching") — the day close, matched against Razorpay's own
settlement report.

Every other screen in Books answers from ONE source: the hash-chained audit
log, folded. That is the right shape for "what did I bill" and it cannot
answer "what reached the bank", because the bank is on the gateway's side of
the wire. Razorpay files a settlement report per day — one row per payment,
refund and adjustment it paid out, with the fee and tax it took — and this
module puts that report beside the chain, row by row, and names every place
the two disagree.

THE MATCH IS BY PAYMENT ID AND NOTHING SOFTER
=============================================
A webhook-settled bill on the chain carries the gateway's `payment_id` (the
kernel writes it on `intent.settled`). A settlement row carries the same id
as `entity_id`. A bill is MATCHED when the two are equal and the row's amount
is the bill's amount. There is no fuzzy step: not by amount, not by time, not
by "probably". A row that finds no bill and a bill that finds no row are each
reported under the name of what they are, and the figures are never netted
against each other to make the totals agree.

THE EXCEPTION LIST, EACH BY NAME
================================
    settled_not_yet_in_recon  the chain settled it; the gateway has not filed
                              it yet. UPI settles T+1, so a bill paid today is
                              in tomorrow's report. Expected, and said so.
    settled_not_in_recon      the chain settled it, its report day has come,
                              and the row is not there. The gateway's word is
                              missing and a person must ask it why.
    in_recon_not_on_chain     THE FOUND MONEY. The gateway paid out a payment
                              no bill on the chain settled. When the row's
                              notes name a nonce this counter minted and never
                              heard back about — a customer who paid while the
                              tunnel was down — a button offers to run the
                              kernel's EXISTING reconcile path for it: a
                              read-only lookup of that one link, settled only
                              if the gateway says it was paid for exactly the
                              intent's amount. Nothing is minted or charged.
    amount_mismatch           matched by id, the paise differ. Parked, named,
                              needs_human. Never rounded, never corrected.
    refunds                   rows of type `refund` — money going back.
    adjustments               anything the gateway filed that is neither a
                              payment nor a refund.
    unreadable_rows           a row whose money fields are not whole paise.
                              Abstained on, never coerced.

READ-ONLY, AND PINNED
=====================
This module holds no key and reaches no gateway. It reads the chain through
`gawaah/manage.py` (the module that decides what a bill is and when it
settled), reads the report through paisa's `GET /recon` (the only process
with a key), and its one POST forwards a nonce to paisa's `/recon/settle`,
which runs `kernel.reconcile` — the lookup path that has existed since the
kernel was written and has no code path that can charge.
`tests/test_milan.py::test_milan_is_read_only_by_construction` greps this
file's imports and paisa's recon routes for every gateway write method and
fails if one appears.

The frozen day-close figures are not touched. `gawaah/daybook.py` asks this
module for a summary to show BESIDE a closed day and copies nothing into the
record.

MOUNTING. An ``APIRouter`` with NO prefix; paths are absolute::

    GET  /milan?day=YYYY-MM-DD   the match (default: yesterday, T+1)
    POST /milan/settle           {nonce} -> paisa /recon/settle
    POST /milan/sim/settle       simulator only: run the settlement batch now
    GET  /milan/health
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import till_ref as _till_ref
from .ledger import Ledger
from .money import MoneyError, paise, to_rupees_str

router = APIRouter()

MODULE = "milan"

# --------------------------------------------------------------- refusals --

R_BAD_DAY = "day_not_a_calendar_date"
R_BAD_BODY = "milan_body_not_json"
R_NO_NONCE = "nonce_missing"
R_BAD_NONCE = "nonce_malformed"
R_CHAIN_UNAVAILABLE = "audit_chain_unavailable"
R_PAISA = "paisa_unreachable"
R_RECON_REFUSED = "recon_refused"
R_SETTLE_REFUSED = "settle_refused"
R_INTERNAL = "milan_internal_error"

# ------------------------------------------------------------ vocabulary --

#: UPI settles on the next cycle. The simulator files at T+1 as well, so the
#: "not yet" class is computed the same way on both gateways.
SETTLEMENT_T_PLUS_DAYS = 1

X_NOT_YET = "settled_not_yet_in_recon"
X_NOT_IN_RECON = "settled_not_in_recon"
X_FOUND = "in_recon_not_on_chain"
X_MISMATCH = "amount_mismatch"
X_REFUNDS = "refunds"
X_ADJUSTMENTS = "adjustments"
X_UNREADABLE = "unreadable_rows"
EXCEPTION_CLASSES: tuple[str, ...] = (
    X_NOT_YET, X_NOT_IN_RECON, X_FOUND, X_MISMATCH, X_REFUNDS, X_ADJUSTMENTS,
    X_UNREADABLE,
)

#: Kernel states from which paisa's reconcile path may settle a found row.
#: Copied as strings, not imported: this module does not import the kernel.
SETTLEABLE_STATES = frozenset({"CALLING", "INDETERMINATE", "RETRIEVE"})

NONCE_RE = re.compile(r"^[A-Za-z0-9_\-]{8,80}$")
MILAN_AUDIT_FILENAME = "milan.audit.jsonl"

#: One list, in one file — see gawaah/till_ref.py for the bug a local copy was.
_TILL_NAMES = _till_ref.TILL_NAMES


class MilanRefused(Exception):
    def __init__(self, reason: str, detail: str, status: int = 400,
                 **extra: Any) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.status = status
        self.extra = dict(extra)


def _refusal(exc: MilanRefused) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "reason": exc.reason, "detail": exc.detail,
         "settles_money": False, **exc.extra},
        status_code=exc.status,
    )


def _crash(exc: BaseException) -> JSONResponse:
    """Never a 500: a reader learns nothing from one."""
    return JSONResponse(
        {"ok": False, "reason": R_INTERNAL,
         "detail": f"{type(exc).__name__}: {exc}", "settles_money": False},
        status_code=400,
    )


def _ok(**fields: Any) -> JSONResponse:
    return JSONResponse({"ok": True, "settles_money": False, **fields})


# ------------------------------------------------------- where things are --


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def shop_dir() -> Path:
    """The shopkeeper's directory: a loaded till's own answer first, then the
    environment, then results/shop — the same order khata.py uses."""
    till = _till_ref.find_loaded_till(sys.modules)
    if till is not None:
        try:
            return Path(till.store_dir())
        except Exception:  # noqa: BLE001 - fall through to the environment
            pass
    override = os.environ.get("GAWAAH_SHOP_DIR")
    if override:
        return Path(override)
    return _repo_root().joinpath("results", "shop")


def audit_path() -> Path:
    """This module's own chain, under the shop dir. NEVER the money chain:
    that file has one writer, gawaah/kernel.py. (`joinpath`, not `/`: the
    strict float lint reads a Path division as a division.)"""
    return shop_dir().joinpath(MILAN_AUDIT_FILENAME)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds")


def _audit(event: str, **fields: Any) -> Optional[str]:
    try:
        return Ledger(audit_path()).append(ts=_now_iso(), module=MODULE,
                                            event=event, **fields)
    except Exception:  # noqa: BLE001 - a failed audit is reported, not hidden
        return None


# ----------------------------------------------------------------- manage --
#
# The chain is read through gawaah/manage.py, the module that decides what a
# bill is and when it settled, so a bill this screen calls settled is one the
# History screen calls settled. Imported late: manage pulls in the vision
# constants and a matcher should not pay for them at import.


def _manage() -> Any:
    try:
        from . import manage  # noqa: WPS433 - deliberately late
    except Exception as exc:  # noqa: BLE001
        raise MilanRefused(
            R_CHAIN_UNAVAILABLE,
            f"gawaah/manage.py is not importable ({type(exc).__name__}: {exc}), "
            f"and it is the module that decides which bills settled. Nothing "
            f"can be matched without it.") from None
    for needed in ("read_chain", "bills_from", "_local_day_bounds", "paisa_get"):
        if not hasattr(manage, needed):
            raise MilanRefused(
                R_CHAIN_UNAVAILABLE,
                f"gawaah/manage.py has no {needed!r}, so the chain cannot be "
                f"read the way the History screen reads it.")
    return manage


def _paisa_get(path: str) -> tuple[int, dict[str, Any]]:
    return _manage().paisa_get(path)


def _paisa_base() -> str:
    return str(getattr(_manage(), "PAISA_BASE", os.environ.get(
        "GAWAAH_PAISA_URL", "http://127.0.0.1:8788")))


def _paisa_post(path: str, body: dict[str, Any], *, timeout_s: int = 30
                ) -> tuple[int, dict[str, Any]]:
    """The one write this module makes to the money service: a nonce, to the
    route that runs the kernel's reconcile lookup. No amount travels."""
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
        return 503, {"ok": False, "reason": R_PAISA,
                     "detail": f"the money service did not answer "
                               f"({type(exc).__name__}). Nothing was settled."}


# ------------------------------------------------------------- the fold --


def _whole(value: Any) -> Optional[int]:
    """A whole number of paise, or None. Never a bool, never a coerced float."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    try:
        return int(paise(value))
    except MoneyError:
        return None


def _parse_ts(value: Any) -> Optional[_dt.datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        d = _dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d


def _unix_to_iso(value: Any) -> Optional[str]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return _dt.datetime.fromtimestamp(int(value), _dt.timezone.utc).isoformat()


def fold_intents(records: Any) -> dict[str, dict[str, Any]]:
    """session_id -> what the chain says about the kernel row behind it.

    `bills_from` decides WHETHER a bill settled; this narrower fold carries the
    nonce (from `paisa/intent.minted` and every `kernel/intent.*` line), the
    kernel's last state, and the paise the gateway said arrived — preferring
    the session's own `settled_green` line for the same reason manage.py
    prefers it: it is the line written from the signature check.
    """
    out: dict[str, dict[str, Any]] = {}
    for rec in records:
        sid = rec.get("session_id")
        if not isinstance(sid, str) or not sid:
            continue
        row = out.get(sid)
        if row is None:
            row = out[sid] = {"nonce": None, "state": None, "payment_id": None,
                              "settled_paise": None, "settled_by": None,
                              "minted_at": None, "settled_at": None}
        module, event = rec.get("module"), rec.get("event")
        nonce = rec.get("nonce")
        if isinstance(nonce, str) and nonce:
            row["nonce"] = nonce
        if module == "paisa" and event == "intent.minted":
            row["minted_at"] = rec.get("ts")
        if module == "kernel" and isinstance(event, str) and event.startswith("intent."):
            to_state = rec.get("to_state")
            if isinstance(to_state, str):
                row["state"] = to_state
            pid = rec.get("payment_id")
            if isinstance(pid, str) and pid:
                row["payment_id"] = pid
            if event == "intent.settled":
                amt = _whole(rec.get("amount_paise"))
                if row["settled_by"] != "webhook":
                    row["settled_paise"] = amt
                    row["settled_by"] = "kernel"
                    row["settled_at"] = rec.get("ts")
        if module == "session" and event == "webhook":
            green = rec.get("reason") == "settled_green" or rec.get("to") == "PAID"
            amt = _whole(rec.get("webhook_amount_paise"))
            if green and amt is not None and row["settled_by"] != "webhook":
                row["settled_paise"] = amt
                row["settled_by"] = "webhook"
                row["settled_at"] = rec.get("ts")
    return out


def _local_date(ts: Any, tz: Any) -> Optional[_dt.date]:
    d = _parse_ts(ts)
    return d.astimezone(tz).date() if d is not None else None


def match(bills: dict[str, dict[str, Any]], records: Any, rows: list[dict[str, Any]],
          *, day: _dt.date, tz: Any) -> dict[str, Any]:
    """The pure matcher. Integers in, integers out, nothing netted.

    `bills` is `manage.bills_from(records)`; `rows` is the gateway's settlement
    report for `day` as paisa returned it (scrubbed, each row annotated with
    `counter_intent`). `day` is the report's day; `tz` is the counter's own
    timezone, used only to decide whether a chain settlement's T+1 day has
    come — the match itself never looks at a date.
    """
    intents = fold_intents(records)
    session_of_nonce = {v["nonce"]: sid for sid, v in intents.items() if v["nonce"]}

    # Settled bills, keyed the only way a row can find them.
    by_payment_id: dict[str, dict[str, Any]] = {}
    for sid, bill in bills.items():
        if not bill.get("settled"):
            continue
        pid = bill.get("payment_id") or intents.get(sid, {}).get("payment_id")
        if isinstance(pid, str) and pid:
            by_payment_id[pid] = bill

    matched: list[dict[str, Any]] = []
    found: list[dict[str, Any]] = []
    mismatched: list[dict[str, Any]] = []
    refunds: list[dict[str, Any]] = []
    adjustments: list[dict[str, Any]] = []
    unreadable: list[dict[str, Any]] = []
    seen_payment_ids: set[str] = set()

    def _row_base(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "entity_id": r.get("entity_id"),
            "type": r.get("type"),
            "settlement_id": r.get("settlement_id"),
            "settled_at": _unix_to_iso(r.get("settled_at")),
            "created_at": _unix_to_iso(r.get("created_at")),
            "simulated": r.get("_gawaah_sim") is True,
        }

    for r in rows:
        if not isinstance(r, dict):
            unreadable.append({"entity_id": None, "why": "row is not an object"})
            continue
        base = _row_base(r)
        amount, credit, debit = _whole(r.get("amount")), _whole(r.get("credit")), _whole(r.get("debit"))
        fee, tax = _whole(r.get("fee")), _whole(r.get("tax"))
        if amount is None or credit is None or debit is None:
            unreadable.append({**base, "why": "amount, credit or debit is not whole paise",
                               "raw": {k: r.get(k) for k in ("amount", "credit", "debit")}})
            continue
        kind = r.get("type")
        notes = r.get("notes") if isinstance(r.get("notes"), dict) else {}
        ci = r.get("counter_intent") if isinstance(r.get("counter_intent"), dict) else None
        sid = notes.get("session_id") if isinstance(notes.get("session_id"), str) else None
        nonce = notes.get("nonce") if isinstance(notes.get("nonce"), str) else None
        if sid is None and nonce is not None:
            sid = session_of_nonce.get(nonce)
        money_row = {**base, "amount_paise": amount, "amount_rupees": to_rupees_str(amount),
                     "credit_paise": credit, "debit_paise": debit,
                     "fee_paise": fee, "tax_paise": tax, "session_id": sid, "nonce": nonce}

        if kind == "refund":
            pid = r.get("payment_id")
            refunds.append({**money_row, "payment_id": pid if isinstance(pid, str) else None,
                            "bill_session_id": (by_payment_id.get(pid) or {}).get("session_id")
                            if isinstance(pid, str) else None})
            continue
        if kind != "payment":
            adjustments.append(money_row)
            continue
        if fee is None or tax is None:
            unreadable.append({**base, "why": "fee or tax is not whole paise",
                               "raw": {"fee": r.get("fee"), "tax": r.get("tax")}})
            continue

        pid = base["entity_id"] if isinstance(base["entity_id"], str) else None
        bill = by_payment_id.get(pid) if pid else None
        if bill is not None:
            seen_payment_ids.add(pid)
            chain = intents.get(bill["session_id"], {})
            expected = chain.get("settled_paise")
            if expected is None:
                expected = _whole(bill.get("total_paise"))
            entry = {**money_row, "session_id": bill["session_id"],
                     "bill_paise": expected,
                     "bill_rupees": to_rupees_str(expected) if expected is not None else None,
                     "bill_at": bill.get("at"),
                     "settled_by": bill.get("settled_by"),
                     "chain_settled_at": bill.get("settled_at")}
            if expected is not None and expected == amount:
                matched.append(entry)
            else:
                mismatched.append({**entry, "needs_human": True,
                                   "difference_paise": (amount - expected) if expected is not None else None})
            continue

        # Nobody on the chain settled this payment. Say what the counter DOES
        # hold for it — a bill it minted and never heard back about is the one
        # a person can act on; a row naming nothing is only reportable.
        state = (ci or {}).get("state")
        on_chain_bill = bills.get(sid) if sid else None
        found.append({
            **money_row,
            "counter_state": state,
            "counter_amount_paise": (ci or {}).get("amount_paise"),
            "counter_payment_id": (ci or {}).get("payment_id"),
            "bill_on_chain": on_chain_bill is not None,
            "bill_at": (on_chain_bill or {}).get("at"),
            "bill_settled_on_chain": bool((on_chain_bill or {}).get("settled")),
            # Settleable only when the kernel holds an open-ended row for it
            # AND the row's amount is the intent's: reconcile re-checks that
            # itself, so this is a hint for the button, not the decision.
            "settleable": bool(nonce) and state in SETTLEABLE_STATES
                          and (ci or {}).get("amount_paise") == amount,
            "needs_human": state not in SETTLEABLE_STATES,
        })

    # Chain settlements the report did not carry, bucketed by whether their
    # T+1 day has come. The match above never used a date; this is the one
    # place one is read, and it decides a NAME, never a figure.
    not_yet: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    earlier_count = earlier_paise = 0
    for sid, bill in bills.items():
        if not bill.get("settled"):
            continue
        pid = bill.get("payment_id") or intents.get(sid, {}).get("payment_id")
        if isinstance(pid, str) and pid in seen_payment_ids:
            continue
        chain = intents.get(sid, {})
        amt = chain.get("settled_paise")
        if amt is None:
            amt = _whole(bill.get("total_paise")) or 0
        settled_on = _local_date(bill.get("settled_at"), tz)
        due = settled_on + _dt.timedelta(days=SETTLEMENT_T_PLUS_DAYS) if settled_on else None
        entry = {"session_id": sid, "payment_id": pid, "amount_paise": amt,
                 "amount_rupees": to_rupees_str(amt), "settled_at": bill.get("settled_at"),
                 "settled_by": bill.get("settled_by"), "bill_at": bill.get("at"),
                 "due_day": due.isoformat() if due else None}
        if due is None or due > day:
            not_yet.append(entry)
        elif due == day:
            missing.append({**entry, "needs_human": True})
        else:
            earlier_count += 1
            earlier_paise += amt

    gross = sum(m["amount_paise"] for m in matched)
    fee_sum = sum(m["fee_paise"] for m in matched)
    tax_sum = sum(m["tax_paise"] for m in matched)
    net = sum(m["credit_paise"] for m in matched) - sum(m["debit_paise"] for m in matched)

    def _bucket(items: list[dict[str, Any]], key: str = "amount_paise") -> dict[str, Any]:
        total = sum(int(i.get(key) or 0) for i in items)
        return {"count": len(items), "paise": total, "rupees": to_rupees_str(total),
                "rows": items}

    exceptions = {
        X_NOT_YET: _bucket(not_yet),
        X_NOT_IN_RECON: _bucket(missing),
        X_FOUND: _bucket(found),
        X_MISMATCH: _bucket(mismatched),
        X_REFUNDS: _bucket(refunds, "debit_paise"),
        X_ADJUSTMENTS: _bucket(adjustments),
        X_UNREADABLE: {"count": len(unreadable), "paise": None, "rupees": None,
                       "rows": unreadable},
    }
    return {
        "matched": {
            "count": len(matched),
            "gross_paise": gross, "gross_rupees": to_rupees_str(gross),
            "fee_paise": fee_sum, "fee_rupees": to_rupees_str(fee_sum),
            "tax_paise": tax_sum, "tax_rupees": to_rupees_str(tax_sum),
            # The gateway's own credit, summed. NOT gross minus fee minus tax:
            # the real entity folds tax into fee and the simulator does not,
            # and a net worked out from either convention would be wrong on
            # the other. `deducted` is what the gateway actually kept.
            "net_paise": net, "net_rupees": to_rupees_str(net),
            "deducted_paise": gross - net, "deducted_rupees": to_rupees_str(gross - net),
            "by_webhook": sum(1 for m in matched if m.get("settled_by") == "webhook"),
            "by_kernel": sum(1 for m in matched if m.get("settled_by") == "kernel"),
            "rows": matched,
        },
        "exceptions": exceptions,
        "exception_count": sum(b["count"] for b in exceptions.values()),
        "earlier_days": {"count": earlier_count, "paise": earlier_paise,
                         "rupees": to_rupees_str(earlier_paise)},
    }


def value_line(day: str, state: dict[str, Any]) -> str:
    """The one sentence: what reached the bank, net of what, and what did not."""
    m = state["matched"]
    x = state["exceptions"]
    n = m["count"]
    if n == 0 and state["exception_count"] == 0:
        return (f"Nothing settled to the bank on {day}: the gateway's report "
                f"for that day is empty and the chain expects nothing in it.")
    parts = [f"Rs {m['net_rupees']} reached the bank on {day}, net of "
             f"Rs {m['deducted_rupees']} fees and tax, {n} bill{'s' if n != 1 else ''} matched."]
    ny = x[X_NOT_YET]["count"]
    if ny:
        parts.append(f"{ny} bill{'s' if ny != 1 else ''} still with Razorpay (settles T+1).")
    fd = x[X_FOUND]["count"]
    if fd:
        parts.append(f"{fd} payment{'s' if fd != 1 else ''} in Razorpay's report "
                     f"that no bill on this counter settled — Rs {x[X_FOUND]['rupees']}.")
    mm = x[X_MISMATCH]["count"]
    if mm:
        parts.append(f"{mm} amount{'s' if mm != 1 else ''} do not agree and "
                     f"{'are' if mm != 1 else 'is'} parked for you.")
    ms = x[X_NOT_IN_RECON]["count"]
    if ms:
        parts.append(f"{ms} settled bill{'s' if ms != 1 else ''} missing from the report.")
    rf = x[X_REFUNDS]["count"]
    if rf:
        parts.append(f"{rf} refund{'s' if rf != 1 else ''}, Rs {x[X_REFUNDS]['rupees']} back.")
    return " ".join(parts)


# -------------------------------------------------------------- the day --


def _valid_day(day: Optional[str], manage: Any) -> tuple[_dt.date, Any]:
    """(the report day, the counter's tz). Default: yesterday, because the
    report for today is not filed until tomorrow."""
    start, _end, _label = manage._local_day_bounds(None)
    tz = start.tzinfo
    if day is None or not str(day).strip():
        return (start - _dt.timedelta(days=1)).date(), tz
    try:
        return _dt.date.fromisoformat(str(day).strip()), tz
    except ValueError:
        raise MilanRefused(
            R_BAD_DAY,
            f"{day!r} is not a calendar day. Write it as YYYY-MM-DD, for "
            f"example {(start - _dt.timedelta(days=1)).date().isoformat()}.") from None


def match_day(day: Optional[str] = None) -> dict[str, Any]:
    """The whole answer for one report day. Raises MilanRefused by name."""
    manage = _manage()
    wanted, tz = _valid_day(day, manage)
    records, chain = manage.read_chain()
    bills = manage.bills_from(records)

    status, recon = _paisa_get(f"/recon?day={wanted.isoformat()}")
    if status != 200 or not isinstance(recon, dict) or not recon.get("ok"):
        reason = str((recon or {}).get("reason") or (recon or {}).get("error") or R_RECON_REFUSED)
        raise MilanRefused(
            R_PAISA if status == 503 else R_RECON_REFUSED,
            f"the money service did not return a settlement report for "
            f"{wanted.isoformat()} ({reason}: {(recon or {}).get('detail') or ''}). "
            f"Nothing was matched and nothing was estimated.",
            status=503 if status == 503 else 400, paisa_status=status)
    rows = list(recon.get("rows") or [])
    state = match(bills, records, rows, day=wanted, tz=tz)
    label = wanted.isoformat()
    return {
        "day": label,
        "settlement_cycle": "T+1",
        "counter_tz": str(tz),
        "mode": recon.get("mode"),
        "simulated": bool(recon.get("simulated")),
        "recon": {"count": len(rows), "fetched_at": recon.get("fetched_at"),
                  "source": recon.get("source"), "day": recon.get("day")},
        **state,
        "value_line": value_line(label, state),
        "chain": chain,
        "derived_from": DERIVED_FROM,
    }


DERIVED_FROM = (
    "Bills and their settlements are folded from the hash-chained audit log by "
    "gawaah/manage.py, the same way the History and Today screens fold them. "
    "The settlement rows are Razorpay's own report for that day, read by the "
    "money service with its key and handed here scrubbed. A bill is matched to "
    "a row by payment id and exact paise, nothing softer. Every figure is "
    "integer paise summed; net is the gateway's own credit, not gross minus "
    "fee. Nothing is netted, rounded or corrected to make two sides agree."
)


def summary_beside_close(closed_day: str) -> dict[str, Any]:
    """What the day book shows BESIDE a frozen record: the report for the day
    those bills settle (T+1), counts and totals only. Nothing here is written
    into the record and nothing here changes it."""
    d = _dt.date.fromisoformat(closed_day) + _dt.timedelta(days=SETTLEMENT_T_PLUS_DAYS)
    full = match_day(d.isoformat())
    return {
        "settlement_day": full["day"],
        "settlement_cycle": full["settlement_cycle"],
        "simulated": full["simulated"],
        "matched": {k: v for k, v in full["matched"].items() if k != "rows"},
        "exceptions": {name: {"count": b["count"], "paise": b["paise"], "rupees": b["rupees"]}
                       for name, b in full["exceptions"].items()},
        "exception_count": full["exception_count"],
        "value_line": full["value_line"],
        "note": ("Read from the gateway's settlement report just now; the frozen "
                 "figures above were not touched by it."),
    }


# ----------------------------------------------------------------- routes --


@router.get("/milan/health")
def health_ep() -> JSONResponse:
    return _ok(module=MODULE, reads=["audit chain via gawaah/manage.py",
                                     "paisa GET /recon"],
               writes=["paisa POST /recon/settle (a nonce, no amount)",
                       str(audit_path())],
               settlement_cycle="T+1", exception_classes=list(EXCEPTION_CLASSES),
               holds_gateway_key=False, can_mint=False, can_charge=False,
               can_refund=False)


@router.get("/milan")
def match_ep(day: str | None = None) -> JSONResponse:
    """The match for one report day. `?day=YYYY-MM-DD`; default yesterday."""
    try:
        return _ok(**match_day(day))
    except MilanRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise MilanRefused(R_BAD_BODY, f"the body is not JSON ({type(exc).__name__}).") from None
    if not isinstance(body, dict):
        raise MilanRefused(R_BAD_BODY, f"the body is a {type(body).__name__}; it must be an object.")
    return body


def _require_nonce(value: Any) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise MilanRefused(R_NO_NONCE, "say which intent: the row's nonce is missing.")
    if not isinstance(value, str) or not NONCE_RE.match(value.strip()):
        raise MilanRefused(R_BAD_NONCE, f"{value!r} is not an intent nonce.")
    return value.strip()


@router.post("/milan/settle")
async def settle_ep(request: Request) -> JSONResponse:
    """SETTLE FROM THE GATEWAY'S RECORD. Body: {nonce}.

    Forwards the nonce — and nothing else — to paisa's `/recon/settle`, which
    runs `kernel.reconcile`: a read-only lookup of the link minted under it,
    settled only if the gateway says it was paid for exactly the intent's
    amount, parked for a person otherwise. This module writes the press on
    its own chain and shows paisa's answer as it came.
    """
    try:
        body = await _json_body(request)
        nonce = _require_nonce(body.get("nonce"))
        status, ans = _paisa_post("/recon/settle", {"nonce": nonce})
        if status != 200 or not isinstance(ans, dict) or not ans.get("ok"):
            reason = str((ans or {}).get("reason") or (ans or {}).get("error") or R_SETTLE_REFUSED)
            _audit("settle.refused", nonce=nonce, paisa_status=status, reason=reason,
                   minted=False, charged=False)
            raise MilanRefused(
                R_PAISA if status == 503 else R_SETTLE_REFUSED,
                f"{reason}: {(ans or {}).get('detail') or 'the money service refused.'}",
                status=503 if status == 503 else 409, paisa_status=status,
                paisa_reason=reason)
        head = _audit("settle.pressed", nonce=nonce, session_id=ans.get("session_id"),
                      state_before=ans.get("state_before"), state=ans.get("state"),
                      payment_id=ans.get("payment_id"), reason=ans.get("reason"),
                      settled=bool(ans.get("settled")), minted=False, charged=False)
        return _ok(**{k: v for k, v in ans.items() if k not in ("ok", "settles_money")},
                   audited=head is not None)
    except MilanRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001
        return _crash(exc)


@router.post("/milan/sim/settle")
async def sim_settle_ep() -> JSONResponse:
    """Simulator only: run the settlement batch now. paisa refuses this by
    name on the live gateway, and that refusal is shown as it came."""
    try:
        status, ans = _paisa_post("/sim/settle", {})
        if status != 200 or not isinstance(ans, dict) or not ans.get("ok"):
            reason = str((ans or {}).get("reason") or (ans or {}).get("error") or R_SETTLE_REFUSED)
            raise MilanRefused(
                R_PAISA if status == 503 else R_SETTLE_REFUSED,
                f"{reason}: {(ans or {}).get('detail') or 'the money service refused.'}",
                status=503 if status == 503 else 409, paisa_status=status)
        _audit("sim.settled", settlement_id=ans.get("settlement_id"),
               payments=ans.get("payments"), simulated=True, minted=False, charged=False)
        return _ok(**{k: v for k, v in ans.items() if k not in ("ok", "settles_money")})
    except MilanRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001
        return _crash(exc)


__all__ = [
    "EXCEPTION_CLASSES", "MilanRefused", "SETTLEMENT_T_PLUS_DAYS", "audit_path",
    "fold_intents", "match", "match_day", "router", "shop_dir",
    "summary_beside_close", "value_line",
]
