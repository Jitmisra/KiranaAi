"""KHARCHA — what the shop spends, and what is actually in the drawer.

The rest of this program is about money coming IN: a packet is recognised, a
bill is closed, a gateway link is minted, a webhook turns it green. None of that
tells a kirana owner the one thing he checks before he pulls the shutter down —
whether the notes in the drawer add up.

Two things live here, and the second is the one that bites.

  1. EXPENSES. Rent, the electricity bill, the boy's wages, chai. An amount in
     integer paise, a category from a short fixed list, a note, a calendar day,
     and whether it came out of the drawer or off the bank account. A day's
     summary is those rows grouped by category with a total.

  2. THE CASH POSITION. Counted opening cash, plus the sales the gateway never
     confirmed, minus the expenses paid in cash, gives an EXPECTED closing
     figure. The shopkeeper then counts the drawer and types what he found. The
     two numbers are shown side by side with the difference between them.

WHERE "CASH SALES" COMES FROM, AND WHY IT IS NOT INVENTED HERE
=============================================================
There is no cash button on this counter and there is no cash flag in any bill.
What there is, in the hash-chained audit log, is a record of which bills the
GATEWAY settled: ``session/webhook`` with reason ``settled_green`` is the only
thing in this program that turns a bill green (invariant 2), and
``kernel/intent.settled`` is written downstream of that same webhook.

So this module reads the bill book through ``gawaah.manage`` — the same
``read_chain()`` and ``bills_from()`` that draw the History screen — and splits
the day's closed bills in two: the ones the chain says settled, and the ones it
does not. It does not re-fold the chain itself, because a second definition of
"settled" is a second truth, and the first time the two disagreed there would be
no way to tell which screen was lying.

A STATED LIMIT, BECAUSE THIS ONE MATTERS
========================================
A bill the gateway never confirmed is not necessarily a bill paid in cash. It is
a bill this counter cannot see the money for. That set contains cash sales, and
it also contains a customer who paid by UPI straight to a printed sticker the
counter never issued, a link that was minted and abandoned, and a basket that
walked out of the door. Every response that uses the figure says so in its own
body rather than in a document nobody opens. The number is a starting point for
a person who was standing there, not an accounting entry.

A DIFFERENCE IS A FACT, NOT AN ACCUSATION
=========================================
The drawer goes short for ordinary reasons: change made from a pocket, a note
stuck to another note, a sale settled on a customer's own UPI, a bill rung twice.
Nothing in this file describes a difference as missing, unaccounted, or a loss,
and nothing here computes a per-person figure. It reports two counts and the gap
between them.

WHAT THIS FILE NEVER DOES
=========================
It settles no money, mints nothing, holds no gateway credential and constructs
no payable string. ``settles_money`` is False on every response and that is a
fact about the code, not a promise. It never divides — every rupee here is
integer paise through ``gawaah/money.py``, and the rupee strings are rendered by
``to_rupees_str`` rather than formatted.

MOUNTING
========
An ``APIRouter`` with NO prefix and absolute paths::

    from gawaah import expenses
    app.include_router(expenses.router)          # -> /expenses, /cash

Do not pass a prefix; the paths below are already what a browser asks for.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .ledger import Ledger
from .money import MoneyError, from_rupees_str, paise, to_rupees_str

router = APIRouter()


# --------------------------------------------------------------- refusals --
#
# Every one of these is a state this module can actually reach from a request,
# and each is written so a shopkeeper can act on it without reading the code.

R_BAD_BODY = "body_not_a_json_object"
R_NO_AMOUNT = "amount_missing"
R_AMOUNT_TWICE = "amount_given_twice"
R_AMOUNT_NOT_INTEGER = "amount_not_integer_paise"
R_BAD_RUPEES = "rupee_string_not_money"
R_AMOUNT_NOT_POSITIVE = "amount_not_positive"
R_AMOUNT_TOO_LARGE = "amount_beyond_this_counter"
R_NO_CATEGORY = "category_missing"
R_BAD_CATEGORY = "category_not_on_the_list"
R_NOTE_REQUIRED = "other_needs_a_note"
R_NOTE_TOO_LONG = "note_too_long"
R_BAD_PAID_WITH = "paid_with_not_cash_or_bank"
R_BAD_DAY = "day_not_a_calendar_date"
R_DAY_IN_FUTURE = "day_is_in_the_future"
R_BAD_EXPENSE_ID = "expense_id_malformed"
R_NO_EXPENSE = "no_such_expense"
R_ALREADY_VOID = "expense_already_voided"
R_NO_VOID_REASON = "void_reason_missing"
R_BAD_LIMIT = "limit_not_a_positive_integer"
R_CASH_NEGATIVE = "counted_cash_negative"
R_CASH_TOO_LARGE = "counted_cash_implausible"
R_NOT_WRITTEN = "not_written_to_disk"
R_NO_TILL = "till_module_unavailable"
R_NO_BILL_BOOK = "bill_book_unavailable"
R_INTERNAL = "expenses_internal_error"


# ------------------------------------------------------------ the shape of --

#: The short fixed list. It is short on purpose: a category list a shopkeeper
#: scrolls is a category list he stops using, and "other" with a written note is
#: more honest than a taxonomy nobody agrees on. What it costs when this is
#: wrong: a shop with an unusual regular cost files it under "other" every time
#: and has to read the notes to total it.
CATEGORIES: tuple[str, ...] = (
    "rent",
    "electricity",
    "wages",
    "tea",
    "transport",
    "supplies",
    "repairs",
    "stock",
    "other",
)

#: Plain English for the page. No marketing voice; these are what a shopkeeper
#: would say out loud.
CATEGORY_LABELS: dict[str, str] = {
    "rent": "Rent",
    "electricity": "Electricity",
    "wages": "Wages",
    "tea": "Tea and snacks",
    "transport": "Transport and delivery",
    "supplies": "Bags and packing",
    "repairs": "Repairs",
    "stock": "Stock bought",
    "other": "Other",
}

#: Cash out of the drawer, or off the bank account. Only the first moves the
#: cash position, and getting this wrong is the difference between a drawer that
#: reconciles and one that appears to be short by a month's rent.
PAID_CASH = "cash"
PAID_BANK = "bank"
PAID_WITH = (PAID_CASH, PAID_BANK)

#: One lakh rupees in a single line. A stray zero on a chai is far more likely
#: than a one-lakh chai, so the cap catches typing rather than commerce. What it
#: costs when this is wrong: a genuine wholesale payment has to be entered as
#: two lines, which is a nuisance and leaves both of them visible.
MAX_EXPENSE_PAISE = 10_000_000

#: Five lakh rupees in a counter drawer. Past this, somebody typed paise where
#: they meant rupees, which is the mistake this bound exists to catch.
MAX_CASH_PAISE = 50_000_000

MAX_NOTE = 200
MAX_VOID_REASON = 200
DEFAULT_LIMIT = 100
MAX_LIMIT = 500

EXPENSE_FORMAT = 1
CASH_FORMAT = 1
EXPENSE_ID_RE = re.compile(r"^exp_[0-9a-f]{12}$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

EXPENSES_SUBDIR = "expenses"
CASH_SUBDIR = "cash"
AUDIT_FILE = "expenses.audit.jsonl"


class ExpenseRefused(Exception):
    """A named refusal with a sentence a shopkeeper can act on."""

    def __init__(self, reason: str, detail: str, status: int = 400) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.status = status


def _refusal(exc: ExpenseRefused) -> JSONResponse:
    """The shape every other endpoint in this program answers a refusal with."""
    return JSONResponse(
        {"ok": False, "reason": exc.reason, "detail": exc.detail,
         "settles_money": False},
        status_code=exc.status,
    )


def _crash(exc: BaseException) -> JSONResponse:
    """The last resort. A 500 teaches the reader nothing, so there are none.

    The exception TYPE is named and the message passed through, because on a
    shop's own books the message is usually the whole diagnosis — 'Permission
    denied: .../expenses' says what to do; 'Internal Server Error' does not.
    """
    return JSONResponse(
        {"ok": False, "reason": R_INTERNAL,
         "detail": f"{type(exc).__name__}: {exc}", "settles_money": False},
        status_code=400,
    )


# ------------------------------------------------------------ where things --
#
# Resolved per call, never memoised at import. A test that redirects the shop
# directory in a fixture must be able to change it between tests, and a
# module-level constant captured at import time silently ignores that — which is
# how a harness once wrote over the live catalogue in results/.

from gawaah import till_ref as _till_ref

#: One definition, in `gawaah/till_ref.py`. This was sixteen copies of a tuple
#: that was missing `__main__`, and every one of them was wrong the same way.
_TILL_NAMES = _till_ref.TILL_NAMES


def _import_till() -> Any:
    """Import the till module. A seam, so a test can prove the refusal below.

    Separated from `_till()` for one reason: the missing-till path is a named
    refusal a shopkeeper could actually see (a broken install, a partial
    checkout), and the only honest way to test it is to make the import fail.
    """
    import sys

    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    from tools import upload_app  # noqa: WPS433 - deliberately late

    return upload_app


def _till() -> Any:
    """The already-loaded till module, or a named refusal.

    LOOK IN sys.modules FIRST, AND DO NOT SKIP THAT STEP. `make serve` runs
    `uvicorn upload_app:app --app-dir tools`, so the module is registered under
    the bare name `upload_app`; the test suite does `from tools import
    upload_app` and registers it as `tools.upload_app`. Importing the other
    spelling loads a SECOND copy of the file with its own cached store handle —
    a second catalogue directory, and a `set_store_dir` in a test that silently
    does not reach the copy serving requests. The symptom would be expenses
    written beside a different shop from the till they were entered on, with
    nothing anywhere saying so.
    """
    import sys

    for name in _TILL_NAMES:
        mod = sys.modules.get(name)
        if mod is not None and _till_ref.is_the_till(mod):
            return mod
    try:
        return _import_till()
    except Exception as exc:  # noqa: BLE001 - a missing till is a named answer
        raise ExpenseRefused(
            R_NO_TILL,
            f"tools/upload_app.py is not importable ({type(exc).__name__}: "
            f"{exc}). The day book is kept beside the shop's own catalogue and "
            f"this module will not guess where that is.") from None


def shop_dir() -> Path:
    """Where this shop's files live — the till's own answer, never a second one.

    This is what honours `GAWAAH_SHOP_DIR`: `upload_app.store_dir()` reads that
    environment variable and `upload_app.set_store_dir()` redirects it for a
    test. Deriving the path here from the environment ourselves would be a
    second answer to one question, and the day a test moved the catalogue and
    the day book stayed behind is the day a harness overwrites a live shop.
    """
    return Path(_till().store_dir())


def expenses_dir() -> Path:
    """One file per expense, beside the catalogue it is a cost of running."""
    return shop_dir() / EXPENSES_SUBDIR


def cash_dir() -> Path:
    """One file per calendar day: what was counted in, what was counted out."""
    return shop_dir() / CASH_SUBDIR


def audit_path() -> Path:
    """This module's own hash-chained log.

    DELIBERATELY NOT `results/audit.jsonl`. That file is held open by the money
    service in a DIFFERENT PROCESS, which keeps the chain head in memory and
    computes `prev_hash` from it. A second process appending between two of its
    writes gives it a stale head, and every line paisa writes afterwards fails
    `gawaah.ledger.verify` — `make verify-ledger` goes red and the money audit
    trail, the one thing in this program that must be beyond argument, is the
    casualty.

    So the day book gets its own chain, in the shop directory, written by the
    one process that owns it and verifiable by exactly the same `verify()`.

    What it costs when this is wrong: there are two chains to walk instead of
    one, and a reader who checks only `results/audit.jsonl` will not see the
    expenses. That is a documentation problem. The alternative was a corrupted
    money ledger.
    """
    return shop_dir() / AUDIT_FILE


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _audit(event: str, **fields: Any) -> Optional[str]:
    """Append one auditable line. Returns the new head, or None if it failed.

    THE NOTE ITSELF IS NOT IN THE CHAIN, only its length and a digest. A note
    reads 'gave Ramu 500 for the week' often enough that it is somebody's wage
    on record, and an audit log is the file most likely to end up pasted into a
    bug report. The digest still binds the note to the chain: change it on disk
    afterwards and the recorded hash no longer matches.

    Best effort, but never silent: a caller that gets None says so in its
    response rather than reporting a witnessed entry that was not.
    """
    try:
        return Ledger(audit_path()).append(
            ts=_now_iso(), module="expenses", event=event, minted=False,
            **fields)
    except Exception:  # noqa: BLE001 - a failed audit must not lose an entry
        return None


def _note_digest(note: str) -> str:
    return hashlib.sha256(note.encode("utf-8")).hexdigest()


# ------------------------------------------------------------- the bill book --


def _import_manage() -> Any:
    """Import the History screen's chain reader. A seam, as `_import_till` is."""
    from . import manage  # noqa: WPS433 - deliberately late, see _bill_book

    return manage


def _bill_book() -> tuple[Callable[[], tuple[Any, dict]],
                          Callable[[Iterable[dict]], dict]]:
    """`(read_chain, bills_from)` from gawaah.manage, or a named refusal.

    Imported LATE for the same two reasons the till is: the orchestrator mounts
    both routers into one app, and `gawaah.manage` pulls in the vision constants
    through `identity` and `takhti`, which an expenses page that never asks for
    a cash position should not pay for.

    Imported AT ALL because the split between a bill the gateway settled and a
    bill it did not is already derived, once, by the code that draws the History
    screen. Re-deriving it here would put a second definition of "settled" in
    the program, and invariant 2 is not a thing to hold two opinions about.
    """
    try:
        mod = _import_manage()
        read_chain = getattr(mod, "read_chain")
        bills_from = getattr(mod, "bills_from")
    except Exception as exc:  # noqa: BLE001 - a named answer, never a 500
        raise ExpenseRefused(
            R_NO_BILL_BOOK,
            f"the bill book could not be read ({type(exc).__name__}: {exc}). "
            f"Expenses can still be recorded and listed; the cash position "
            f"cannot be worked out without the day's sales.") from None
    return read_chain, bills_from


def _parse_ts(value: Any) -> Optional[_dt.datetime]:
    """An ISO-8601 stamp as the ledger writes them, or None.

    A naive stamp is read as UTC, which is what the ledger writes; guessing
    local for a naive stamp would move an evening bill into the next day on a
    counter east of Greenwich.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


# ------------------------------------------------------------------- days --


def _local_tz() -> Any:
    return _dt.datetime.now().astimezone().tzinfo


def _today_label() -> str:
    return _dt.datetime.now(_local_tz()).strftime("%Y-%m-%d")


def _valid_day(day: Optional[str]) -> str:
    """A calendar day in the counter's own timezone, defaulting to today.

    Refuses a day in the future. A shopkeeper dating chai to next Tuesday has
    mistyped, and an expense that has not happened yet sitting in a total is a
    number he will act on. What it costs when this is wrong: rent cannot be
    entered in advance, and has to be entered on the day it is paid.
    """
    if day is None or (isinstance(day, str) and not day.strip()):
        return _today_label()
    if not isinstance(day, str):
        raise ExpenseRefused(
            R_BAD_DAY,
            f"a day is written as YYYY-MM-DD, for example 2026-09-01 — got a "
            f"{type(day).__name__}.")
    text = day.strip()
    if not DAY_RE.match(text):
        raise ExpenseRefused(
            R_BAD_DAY,
            f"{day!r} is not a calendar day. Write it as YYYY-MM-DD, for "
            f"example 2026-09-01.")
    try:
        _dt.datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise ExpenseRefused(
            R_BAD_DAY,
            f"{day!r} is not a day that exists. Write it as YYYY-MM-DD, for "
            f"example 2026-09-01.") from None
    if text > _today_label():
        raise ExpenseRefused(
            R_DAY_IN_FUTURE,
            f"{text} has not happened yet — today is {_today_label()} at this "
            f"counter. Nothing was recorded.")
    return text


def _day_bounds(day: str) -> tuple[_dt.datetime, _dt.datetime]:
    """Midnight to midnight in the COUNTER'S OWN timezone.

    The chain stamps UTC. A shopkeeper's day does not start at 05:30, and
    answering "what came in today" with a UTC window quietly moves last
    evening's sales into tomorrow. The same rule `/manage/today` uses, so the
    two screens describe the same day. What it costs when this is wrong: a
    counter physically moved across timezones mid-shift splits that day oddly.
    That is a case this product does not claim.
    """
    start = _dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=_local_tz())
    return start, start + _dt.timedelta(days=1)


# ---------------------------------------------------------- reading a body --


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a named refusal
        raise ExpenseRefused(
            R_BAD_BODY,
            'this request\'s body is not JSON. It should look like '
            '{"amount_paise": 12000, "category": "tea"}.') from None
    if not isinstance(body, dict):
        raise ExpenseRefused(
            R_BAD_BODY,
            f"this request's body is a {type(body).__name__}; it must be a "
            f"JSON object.")
    return body


def _read_amount(body: dict[str, Any], int_key: str, str_key: str, *,
                 cap: int, allow_zero: bool, what: str) -> int:
    """One money field, as integer paise, from either spelling.

    TWO SPELLINGS ON PURPOSE, AND ONLY ONE AT A TIME. A page that converts
    rupees to paise in the browser is a page that writes `parseFloat(x) * 100`,
    and 12.10 * 100 is 1209.9999999999998 in every browser on earth. So this
    accepts the rupee STRING the shopkeeper actually typed and parses it with
    `money.from_rupees_str`, which never touches a float. Sending both keys is
    refused rather than resolved: two numbers that disagree have no correct
    winner, and picking one silently is how the wrong one gets stored.
    """
    has_int = int_key in body and body[int_key] is not None
    has_str = str_key in body and body[str_key] is not None
    if has_int and has_str:
        raise ExpenseRefused(
            R_AMOUNT_TWICE,
            f"this request gives {what} twice, as {int_key}={body[int_key]!r} "
            f"and {str_key}={body[str_key]!r}. Send one of them. Nothing was "
            f"recorded.")
    if not has_int and not has_str:
        raise ExpenseRefused(
            R_NO_AMOUNT,
            f"no {what} in the body. Send {int_key} as whole paise, or "
            f"{str_key} as a string like \"120.50\".")

    if has_int:
        raw = body[int_key]
        # bool first: True is an int in Python and an amount of True is not a
        # thing anybody meant.
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ExpenseRefused(
                R_AMOUNT_NOT_INTEGER,
                f"{int_key}={raw!r} is not whole paise. Money here is integer "
                f"paise — 120.50 rupees is 12050 — because a rupee that is a "
                f"decimal fraction stops adding up. Send the rupees as a "
                f"string in {str_key} if that is easier.")
        try:
            amount = int(paise(raw))
        except MoneyError as exc:
            raise ExpenseRefused(
                R_AMOUNT_NOT_INTEGER, f"{int_key}={raw!r} is not money ({exc}).",
            ) from None
    else:
        raw = body[str_key]
        if not isinstance(raw, str) or not raw.strip():
            raise ExpenseRefused(
                R_BAD_RUPEES,
                f"{str_key}={raw!r} is not a rupee amount. Write it as a "
                f"string, like \"120.50\".")
        try:
            amount = int(from_rupees_str(raw))
        except MoneyError as exc:
            raise ExpenseRefused(
                R_BAD_RUPEES,
                f"{str_key}={raw!r} could not be read as rupees ({exc}). Two "
                f"decimal places at most; a shop does not deal in half paise.",
            ) from None

    if amount < 0:
        raise ExpenseRefused(
            R_CASH_NEGATIVE if allow_zero else R_AMOUNT_NOT_POSITIVE,
            f"{what} is {to_rupees_str(paise(amount))} rupees. A negative "
            f"figure is not a thing this counter can record. Nothing was "
            f"stored.")
    if amount == 0 and not allow_zero:
        raise ExpenseRefused(
            R_AMOUNT_NOT_POSITIVE,
            f"{what} is zero. An expense of nothing is not an expense; leave it "
            f"out. Nothing was recorded.")
    if amount > cap:
        raise ExpenseRefused(
            R_AMOUNT_TOO_LARGE if not allow_zero else R_CASH_TOO_LARGE,
            f"{what} is {to_rupees_str(paise(amount))} rupees, past the "
            f"{to_rupees_str(paise(cap))} this counter takes in one entry. "
            f"Check whether paise were typed where rupees were meant. Nothing "
            f"was stored.")
    return amount


def _read_category(body: dict[str, Any]) -> str:
    raw = body.get("category")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise ExpenseRefused(
            R_NO_CATEGORY,
            f"no category in the body. This shop keeps: "
            f"{', '.join(CATEGORIES)}.")
    if not isinstance(raw, str):
        raise ExpenseRefused(
            R_BAD_CATEGORY,
            f"category={raw!r} is a {type(raw).__name__}. It is one of: "
            f"{', '.join(CATEGORIES)}.")
    text = raw.strip().lower()
    if text not in CATEGORIES:
        raise ExpenseRefused(
            R_BAD_CATEGORY,
            f"{raw!r} is not a category this shop keeps. It keeps: "
            f"{', '.join(CATEGORIES)}. Use 'other' with a note if none of them "
            f"fit. Nothing was recorded.")
    return text


def _read_note(body: dict[str, Any], category: str) -> str:
    raw = body.get("note", "")
    if raw is None:
        raw = ""
    if not isinstance(raw, str):
        raise ExpenseRefused(
            R_BAD_BODY,
            f"note={raw!r} must be text, not a {type(raw).__name__}.")
    note = " ".join(raw.split())
    if len(note) > MAX_NOTE:
        raise ExpenseRefused(
            R_NOTE_TOO_LONG,
            f"the note is {len(note)} characters and the cap is {MAX_NOTE}. "
            f"Nothing was recorded.")
    if category == "other" and not note:
        # The one category that carries no meaning of its own. A month of
        # unlabelled "other" is a number the shopkeeper cannot do anything with,
        # and the moment to ask is while he still remembers what it was for.
        raise ExpenseRefused(
            R_NOTE_REQUIRED,
            "'other' needs a note saying what it was for. In a month's time "
            "'other' on its own says nothing. Nothing was recorded.")
    return note


def _read_paid_with(body: dict[str, Any]) -> str:
    """Cash or bank. Defaults to cash, because a kirana mostly pays in cash.

    This is the field the cash position turns on: an expense marked `bank` is a
    real cost and appears in every total, and it does NOT come out of the
    drawer. Marking a bank transfer as cash makes the drawer look short by
    exactly that amount at closing time.
    """
    raw = body.get("paid_with", PAID_CASH)
    if raw is None:
        return PAID_CASH
    if not isinstance(raw, str) or raw.strip().lower() not in PAID_WITH:
        raise ExpenseRefused(
            R_BAD_PAID_WITH,
            f"paid_with={raw!r} is not something this counter knows. It is "
            f"'{PAID_CASH}' (out of the drawer) or '{PAID_BANK}' (off the "
            f"account). Nothing was recorded.")
    return raw.strip().lower()


def _read_limit(limit: Optional[str]) -> int:
    if limit is None or not str(limit).strip():
        return DEFAULT_LIMIT
    text = str(limit).strip()
    if not text.isdigit() or int(text) <= 0:
        raise ExpenseRefused(
            R_BAD_LIMIT,
            f"limit={limit!r} is not a positive whole number of rows. Leave it "
            f"out for {DEFAULT_LIMIT}.")
    return min(int(text), MAX_LIMIT)


# -------------------------------------------------------------- on the disk --


def _valid_expense_id(expense_id: str) -> str:
    """Checked against a strict charset BEFORE it is ever joined to a path.

    The id becomes a filename. A shape check here is what stops a request for
    `../../catalog` reading the shop's price list, and it is cheaper than
    trusting every caller downstream to remember.
    """
    text = (expense_id or "").strip()
    if not EXPENSE_ID_RE.match(text):
        raise ExpenseRefused(
            R_BAD_EXPENSE_ID,
            f"{expense_id!r} is not an expense id from this shop. They look "
            f"like 'exp_' followed by twelve hex characters.")
    return text


def _write_json(path: Path, doc: dict[str, Any]) -> None:
    """Write via a temp file and rename, so a reader never sees half a record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, sort_keys=True, indent=1) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def _store(path: Path, doc: dict[str, Any], what: str) -> None:
    """Write, or refuse by name. Never report something that is not on disk."""
    try:
        _write_json(path, doc)
    except OSError as exc:
        raise ExpenseRefused(
            R_NOT_WRITTEN,
            f"{what} could not be written to {path} ({type(exc).__name__}: "
            f"{exc}). Nothing was recorded, so the page is not about to show "
            f"you a number that is not on disk.") from None


def _read_expense(expense_id: str) -> dict[str, Any]:
    path = expenses_dir() / f"{_valid_expense_id(expense_id)}.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ExpenseRefused(
            R_NO_EXPENSE,
            f"this shop has no expense {expense_id!r}. Nothing was changed.",
            status=404) from None
    except Exception as exc:  # noqa: BLE001 - a corrupt file is not a crash
        raise ExpenseRefused(
            R_NO_EXPENSE,
            f"expense {expense_id!r} is on disk but could not be read "
            f"({type(exc).__name__}: {exc}). Nothing was changed.",
            status=404) from None
    if not isinstance(doc, dict) or not doc.get("expense_id"):
        raise ExpenseRefused(
            R_NO_EXPENSE,
            f"expense {expense_id!r} is not an expense record.", status=404)
    return doc


def _all_expenses(day: Optional[str] = None) -> list[dict[str, Any]]:
    """Every expense, newest first. An unreadable file is skipped, not fatal.

    One file per entry and a glob to list them: a kirana writes a handful a day,
    and the alternative — one document rewritten on every entry — loses the lot
    if the process dies mid-write. What it costs when this is wrong: a shop that
    has been running for ten years reads a few thousand small files to draw one
    day. If that ever bites, the fix is an index, not a rewrite of the records.
    """
    out: list[dict[str, Any]] = []
    directory = expenses_dir()
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("exp_*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - one bad file must not hide the rest
            continue
        if not isinstance(doc, dict) or not doc.get("expense_id"):
            continue
        if day is not None and doc.get("day") != day:
            continue
        out.append(doc)
    # NEWEST BUSINESS DAY FIRST, not newest keystroke first. A shopkeeper who
    # sits down on Monday and enters Saturday's rent should see it filed under
    # Saturday, not at the top above today's chai — the `day` is what the entry
    # is ABOUT and `at` is only when it was typed. Both are YYYY-MM-DD and
    # ISO-8601 strings, so lexical order IS chronological order; the id is the
    # tiebreak so two entries in the same microsecond have a stable order
    # rather than a filesystem-dependent one.
    out.sort(key=lambda d: (str(d.get("day") or ""), str(d.get("at") or ""),
                            str(d.get("expense_id"))),
             reverse=True)
    return out


def _cash_path(day: str) -> Path:
    return cash_dir() / f"{day}.json"


def _whole_paise_or_none(value: Any) -> Optional[int]:
    """A stored count, or None if it is not integer paise.

    The file on disk is the one thing here a person can edit with a text editor,
    and `2032.40` written into it by a well-meaning hand would otherwise be read
    straight into the drawer figure. A count that is not integer paise is not a
    count, and a screen that says "not counted" is honest where one that says
    "2032.4" is not.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    try:
        return int(paise(value))
    except MoneyError:
        return None


def _read_cash_day(day: str) -> dict[str, Any]:
    """The counts recorded for one day. An absent file is an uncounted day."""
    blank = {
        "format": CASH_FORMAT,
        "day": day,
        "opening_paise": None,
        "opening_counted_at": None,
        "closing_paise": None,
        "closing_counted_at": None,
        "closing_note": "",
    }
    try:
        doc = json.loads(_cash_path(day).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return blank
    except Exception:  # noqa: BLE001 - a corrupt count is an uncounted day
        return blank
    if not isinstance(doc, dict):
        return blank
    blank.update({k: v for k, v in doc.items() if k in blank})
    blank["day"] = day
    blank["opening_paise"] = _whole_paise_or_none(blank["opening_paise"])
    blank["closing_paise"] = _whole_paise_or_none(blank["closing_paise"])
    return blank


# -------------------------------------------------------------- the totals --


def _totals(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Group a day's expenses by category. Integer addition and nothing else.

    A VOIDED ROW IS COUNTED IN NOTHING AND HIDDEN FROM NOTHING. It is excluded
    from every total and still returned in the list with `void: true`, because a
    correction that makes the original disappear is indistinguishable from an
    edit, and a day book you can edit is not evidence of anything.
    """
    by_category: dict[str, dict[str, Any]] = {}
    total = 0
    cash_out = 0
    cash_rows = 0
    bank_out = 0
    bank_rows = 0
    voided = 0
    voided_count = 0
    counted = 0

    for row in rows:
        try:
            amount = int(paise(row.get("amount_paise")))
        except (MoneyError, TypeError, ValueError):
            # A record whose amount is not integer paise is not silently read as
            # zero: it is left out of the totals and the caller reports how many
            # rows it could not add up.
            continue
        if row.get("void"):
            voided += amount
            voided_count += 1
            continue
        category = str(row.get("category") or "other")
        bucket = by_category.get(category)
        if bucket is None:
            bucket = {"category": category,
                      "label": CATEGORY_LABELS.get(category, category),
                      "count": 0, "paise": 0}
            by_category[category] = bucket
        bucket["count"] += 1
        bucket["paise"] += amount
        total += amount
        counted += 1
        if row.get("paid_with") == PAID_BANK:
            bank_out += amount
            bank_rows += 1
        else:
            cash_out += amount
            cash_rows += 1

    ordered = sorted(by_category.values(),
                     key=lambda b: (-int(b["paise"]), str(b["category"])))
    for bucket in ordered:
        bucket["rupees"] = to_rupees_str(int(paise(bucket["paise"])))

    return {
        "count": counted,
        "total_paise": total,
        "total_rupees": to_rupees_str(paise(total)),
        "cash_count": cash_rows,
        "cash_paise": cash_out,
        "cash_rupees": to_rupees_str(paise(cash_out)),
        "bank_count": bank_rows,
        "bank_paise": bank_out,
        "bank_rupees": to_rupees_str(paise(bank_out)),
        "voided_count": voided_count,
        "voided_paise": voided,
        "voided_rupees": to_rupees_str(paise(voided)),
        "by_category": ordered,
    }


def _view(row: dict[str, Any]) -> dict[str, Any]:
    """One expense as the page shows it, with the rupee string rendered here.

    Rendered SERVER-SIDE for the same reason the amount is parsed server-side: a
    browser that formats paise into rupees divides by a hundred, and invariant 1
    does not have a browser-shaped exception.
    """
    try:
        amount = int(paise(row.get("amount_paise")))
        rupees = to_rupees_str(paise(amount))
    except (MoneyError, TypeError, ValueError):
        amount, rupees = None, None
    category = str(row.get("category") or "other")
    return {
        "expense_id": row.get("expense_id"),
        "at": row.get("at"),
        "day": row.get("day"),
        "amount_paise": amount,
        "amount_rupees": rupees,
        "category": category,
        "category_label": CATEGORY_LABELS.get(category, category),
        "note": row.get("note") or "",
        "paid_with": row.get("paid_with") or PAID_CASH,
        "void": bool(row.get("void")),
        "voided_at": row.get("voided_at"),
        "void_reason": row.get("void_reason"),
    }


# ------------------------------------------------------------------ routes --


@router.get("/expenses/categories")
def categories_ep() -> JSONResponse:
    """The fixed list, so the page draws only the buttons that will work.

    Declared before any path that could shadow it. There is deliberately no
    `GET /expenses/{id}`, so nothing here depends on route ordering — but the
    order is kept anyway, because the day somebody adds one is the day this
    endpoint would start reading as an expense id.
    """
    return JSONResponse({
        "ok": True,
        "settles_money": False,
        "categories": [
            {"category": c, "label": CATEGORY_LABELS.get(c, c)}
            for c in CATEGORIES
        ],
        "paid_with": list(PAID_WITH),
        "max_expense_paise": MAX_EXPENSE_PAISE,
        "max_expense_rupees": to_rupees_str(paise(MAX_EXPENSE_PAISE)),
        "max_note": MAX_NOTE,
        "note": ("Anything that does not fit goes under 'other', which needs a "
                 "note saying what it was for."),
    })


@router.post("/expenses")
async def add_expense_ep(request: Request) -> JSONResponse:
    """Record one thing the shop paid for.

    Body: {amount_paise | amount_rupees, category, note, day, paid_with}.
    `day` defaults to today at this counter and cannot be in the future;
    `paid_with` defaults to cash, which is the only kind that moves the drawer.
    """
    try:
        body = await _json_body(request)
        amount = _read_amount(
            body, "amount_paise", "amount_rupees",
            cap=MAX_EXPENSE_PAISE, allow_zero=False, what="an amount")
        category = _read_category(body)
        note = _read_note(body, category)
        paid_with = _read_paid_with(body)
        day = _valid_day(body.get("day"))

        expense_id = "exp_" + secrets.token_hex(6)
        doc = {
            "format": EXPENSE_FORMAT,
            "expense_id": expense_id,
            "at": _now_iso(),
            "day": day,
            "amount_paise": amount,
            "category": category,
            "note": note,
            "paid_with": paid_with,
            "void": False,
            "voided_at": None,
            "void_reason": None,
        }
        _store(expenses_dir() / f"{expense_id}.json", doc, "this expense")
        head = _audit(
            "expense.recorded",
            expense_id=expense_id,
            day=day,
            amount_paise=amount,
            category=category,
            paid_with=paid_with,
            note_len=len(note),
            note_sha256=_note_digest(note),
        )
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "expense": _view(doc),
            "audited": head is not None,
            "note": ("Recorded against this shop's own day book. Nothing was "
                     "charged and no payment was made by this counter."),
        })
    except ExpenseRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/expenses")
def list_expenses_ep(day: str | None = None,
                     limit: str | None = None) -> JSONResponse:
    """What the shop spent, newest first. `?day=YYYY-MM-DD` for one day.

    With no `day` this is every expense on record, capped by `limit`. The totals
    describe the rows RETURNED, and `truncated` says when there were more, so a
    capped list never reads as a complete one.
    """
    try:
        want = _read_limit(limit)
        wanted_day = _valid_day(day) if day else None
        rows = _all_expenses(wanted_day)
        shown = rows[:want]
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "day": wanted_day,
            "count": len(shown),
            "total_on_record": len(rows),
            "truncated": len(rows) > len(shown),
            "limit": want,
            "expenses": [_view(r) for r in shown],
            **_totals(shown),
        })
    except ExpenseRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/expenses/day")
def expenses_day_ep(day: str | None = None) -> JSONResponse:
    """One day's spending, grouped by category, with a total.

    Every row for the day is read, not a capped page of them: a total that
    silently stops at a hundred rows is worse than no total.
    """
    try:
        wanted_day = _valid_day(day)
        rows = _all_expenses(wanted_day)
        summary = _totals(rows)
        unreadable = len(
            [r for r in rows if not r.get("void")]) - int(summary["count"])
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "day": wanted_day,
            "rows_on_record": len(rows),
            "unreadable_rows": max(unreadable, 0),
            "expenses": [_view(r) for r in rows],
            **summary,
            "note": ("Voided entries are listed and counted in nothing. Only "
                     "the ones paid in cash come out of the drawer."),
        })
    except ExpenseRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/expenses/{expense_id}/void")
async def void_expense_ep(expense_id: str, request: Request) -> JSONResponse:
    """Undo a mistyped expense. The record stays; it stops counting.

    THERE IS NO DELETE. A day book whose entries can be removed is not evidence
    of anything, and the most likely reader of a voided line is the shopkeeper
    trying to work out why the drawer was short. So the row keeps its id, its
    amount and its original note, gains the reason it was voided, and is left
    out of every total.

    Body: {"reason": "typed twice"}. The reason is required: 'void' with no word
    beside it is the same problem as 'other' with no note.
    """
    try:
        body = await _json_body(request)
        raw = body.get("reason")
        reason = " ".join(raw.split()) if isinstance(raw, str) else ""
        if not reason:
            raise ExpenseRefused(
                R_NO_VOID_REASON,
                'no "reason" in the body. Say why in a few words — '
                '{"reason": "typed twice"} — so the line explains itself later. '
                'Nothing was changed.')
        if len(reason) > MAX_VOID_REASON:
            raise ExpenseRefused(
                R_NOTE_TOO_LONG,
                f"the reason is {len(reason)} characters and the cap is "
                f"{MAX_VOID_REASON}. Nothing was changed.")

        doc = _read_expense(expense_id)
        if doc.get("void"):
            raise ExpenseRefused(
                R_ALREADY_VOID,
                f"expense {doc['expense_id']} was already voided at "
                f"{doc.get('voided_at')}. Nothing was changed.")

        doc["void"] = True
        doc["voided_at"] = _now_iso()
        doc["void_reason"] = reason
        _store(expenses_dir() / f"{doc['expense_id']}.json", doc, "the void")
        head = _audit(
            "expense.voided",
            expense_id=doc["expense_id"],
            day=doc.get("day"),
            amount_paise=int(doc.get("amount_paise") or 0),
            category=doc.get("category"),
            paid_with=doc.get("paid_with"),
            reason_len=len(reason),
            reason_sha256=_note_digest(reason),
        )
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "expense": _view(doc),
            "audited": head is not None,
            "note": ("The entry is still on record and still visible. It is "
                     "counted in no total from here."),
        })
    except ExpenseRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


# ----------------------------------------------------------- the cash drawer --


def _sales_split(day: str) -> dict[str, Any]:
    """The day's closed bills, split by whether the GATEWAY settled them.

    Neither figure is invented here. `bills_from` is the History screen's own
    fold of the hash-chained log, and `settled` on a bill is set by exactly one
    thing: `session/webhook` with reason `settled_green` (invariant 2), with
    `kernel/intent.settled` accepted downstream of the same webhook and labelled
    as such. Everything else that closed is in the other column.
    """
    read_chain, bills_from = _bill_book()
    records, chain = read_chain()
    bills = bills_from(records)
    start, end = _day_bounds(day)

    gateway = 0
    gateway_count = 0
    settled_by: dict[str, int] = {}
    cash = 0
    cash_count = 0
    undated = 0

    for bill in bills.values():
        if not bill.get("closed"):
            continue
        at = _parse_ts(bill.get("at"))
        if at is None:
            undated += 1
            continue
        if not (start <= at < end):
            continue
        try:
            amount = int(paise(bill.get("total_paise") or 0))
        except (MoneyError, TypeError, ValueError):
            continue
        if bill.get("settled"):
            gateway += amount
            gateway_count += 1
            key = str(bill.get("settled_by") or "unknown")
            settled_by[key] = settled_by.get(key, 0) + 1
        else:
            cash += amount
            cash_count += 1

    return {
        "gateway_paise": gateway,
        "gateway_count": gateway_count,
        "gateway_settled_by": settled_by,
        "cash_paise": cash,
        "cash_count": cash_count,
        "undated_bills": undated,
        "chain": chain,
    }


@router.get("/cash")
def cash_position_ep(day: str | None = None) -> JSONResponse:
    """The cash position for one day. `?day=YYYY-MM-DD` for a past day.

        counted opening
      + sales the gateway never confirmed
      - expenses paid in cash
      = what should be in the drawer

    and beside it what the shopkeeper counted, and the difference.

    THE EXPECTED FIGURE IS ABSENT UNTIL THE OPENING IS COUNTED, rather than
    computed from a zero. A drawer that started with two thousand rupees of
    change and is reported against an assumed empty opening reads as two
    thousand over, and a figure that is wrong by exactly the float is worse than
    no figure, because it looks like an answer.
    """
    try:
        wanted_day = _valid_day(day)
        counts = _read_cash_day(wanted_day)
        sales = _sales_split(wanted_day)
        spend = _totals(_all_expenses(wanted_day))

        opening = counts.get("opening_paise")
        closing = counts.get("closing_paise")
        cash_in = int(sales["cash_paise"])
        cash_out = int(spend["cash_paise"])
        movement = cash_in - cash_out

        expected = None
        if isinstance(opening, int) and not isinstance(opening, bool):
            expected = int(paise(opening)) + movement

        difference = None
        direction = None
        if expected is not None and isinstance(closing, int) \
                and not isinstance(closing, bool):
            difference = int(paise(closing)) - expected
            direction = ("exact" if difference == 0
                         else "over" if difference > 0 else "short")

        # A BROKEN CHAIN MAKES THIS PAGE UNDERSTATE THE SALES, AND IT MUST SAY
        # SO. `read_chain` stops at the first link whose hash does not recompute
        # and returns the verified prefix, which is the right call for a history
        # screen — but here the missing bills come straight off the cash sales
        # figure, so the drawer reads OVER by whatever they came to and the
        # shopkeeper is looking at a difference the counter caused. Nothing is
        # adjusted to cover it; the gap is named instead.
        chain = sales["chain"]
        chain_warning = None
        if chain.get("exists") and not chain.get("ok"):
            chain_warning = (
                f"The audit chain does not verify past line "
                f"{chain.get('lines_verified')} ({chain.get('error')}). Any "
                f"bill recorded after that line is not in the sales figures "
                f"above, so the drawer will read over by whatever those bills "
                f"came to. Nothing here has been adjusted to hide that.")

        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "day": wanted_day,
            "opening": {
                "counted": opening is not None,
                "paise": opening,
                "rupees": (to_rupees_str(int(paise(opening)))
                           if isinstance(opening, int) else None),
                "counted_at": counts.get("opening_counted_at"),
            },
            "cash_sales": {
                "bills": int(sales["cash_count"]),
                "paise": cash_in,
                "rupees": to_rupees_str(paise(cash_in)),
            },
            "gateway_sales": {
                "bills": int(sales["gateway_count"]),
                "paise": int(sales["gateway_paise"]),
                "rupees": to_rupees_str(int(paise(sales["gateway_paise"]))),
                "settled_by": sales["gateway_settled_by"],
            },
            "cash_expenses": {
                "count": int(spend["cash_count"]),
                "paise": cash_out,
                "rupees": to_rupees_str(paise(cash_out)),
            },
            # Listed beside the cash ones and deliberately NOT subtracted from
            # the drawer. A bank transfer is a real cost of running the shop and
            # belongs in the day's spending; treating it as cash would make the
            # drawer read short by exactly the rent.
            "bank_expenses": {
                "count": int(spend["bank_count"]),
                "paise": int(spend["bank_paise"]),
                "rupees": spend["bank_rupees"],
            },
            "movement_paise": movement,
            "movement_rupees": to_rupees_str(paise(movement)),
            "expected_closing_paise": expected,
            "expected_closing_rupees": (to_rupees_str(paise(expected))
                                        if expected is not None else None),
            "counted_closing": {
                "counted": closing is not None,
                "paise": closing,
                "rupees": (to_rupees_str(int(paise(closing)))
                           if isinstance(closing, int) else None),
                "counted_at": counts.get("closing_counted_at"),
                "note": counts.get("closing_note") or "",
            },
            "difference_paise": difference,
            "difference_rupees": (to_rupees_str(paise(difference))
                                  if difference is not None else None),
            "difference_direction": direction,
            "difference_note": _difference_note(direction, expected, closing),
            "cash_sales_note": (
                "These are the day's bills the gateway never confirmed. That "
                "set is mostly cash, and it is not only cash: a customer who "
                "paid on their own UPI, a link that was minted and left, or a "
                "basket that walked out looks exactly the same from here. This "
                "counter can see what it billed and what the gateway settled; "
                "it cannot see a note change hands."),
            "undated_bills": int(sales["undated_bills"]),
            "chain": chain,
            "chain_warning": chain_warning,
            "derived_from": (
                "opening and closing are what you counted; the sales split is "
                "read from the hash-chained audit log for this calendar day in "
                "this counter's own timezone; the expenses are this shop's own "
                "day book. Nothing is cached and nothing is estimated."),
        })
    except ExpenseRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


def _difference_note(direction: Optional[str], expected: Optional[int],
                     closing: Optional[int]) -> str:
    """Plain English for the gap, and no word of blame in any branch.

    A difference is a FACT. The drawer goes short and over for ordinary reasons
    and this counter cannot tell which one happened, so it says what it can see
    and stops. There is no per-person figure here and no path that produces one.
    """
    if expected is None:
        return ("Count the opening cash to get an expected figure. Without it "
                "this page can show what moved today, but not what should be "
                "in the drawer.")
    if closing is None:
        return ("Count the drawer and enter what you found. Nothing is "
                "compared until you do.")
    if direction == "exact":
        return "The drawer matches the expected figure to the paisa."
    if direction == "over":
        return ("There is more in the drawer than expected. That is a fact, "
                "not an accusation. It happens when a bill was settled on the "
                "gateway and paid in cash as well, when change was taken from "
                "a pocket, or when an expense was paid from somewhere else.")
    return ("There is less in the drawer than expected. That is a fact, not an "
            "accusation. It happens when change was made from the drawer for "
            "something that was not billed, when a customer paid on their own "
            "UPI against a bill this counter is still holding open, or when an "
            "expense has not been entered yet.")


def _record_count(kind: str, day: str, amount: int, note: str) -> dict[str, Any]:
    """Store one counted figure for one day. Shared by opening and closing."""
    doc = _read_cash_day(day)
    now = _now_iso()
    doc["format"] = CASH_FORMAT
    doc[f"{kind}_paise"] = amount
    doc[f"{kind}_counted_at"] = now
    if kind == "closing":
        doc["closing_note"] = note
    _store(_cash_path(day), doc, f"the {kind} count")
    head = _audit(
        f"cash.{kind}_counted",
        day=day,
        amount_paise=amount,
        note_len=len(note),
        note_sha256=_note_digest(note),
    )
    return {
        "ok": True,
        "settles_money": False,
        "day": day,
        "kind": kind,
        "counted_paise": amount,
        "counted_rupees": to_rupees_str(paise(amount)),
        "counted_at": now,
        "audited": head is not None,
        "cash": doc,
    }


@router.post("/cash/opening")
async def cash_opening_ep(request: Request) -> JSONResponse:
    """What was in the drawer when the shutter went up.

    Body: {counted_paise | counted_rupees, day}. Zero is a valid count and means
    the drawer was empty; that is a different statement from not having counted,
    which is what an absent record means.

    Recorded again for the same day, it OVERWRITES. A shopkeeper who miscounted
    at seven in the morning is correcting a figure he entered, not editing
    evidence — the chain keeps both lines, so what he first said is still there.
    """
    try:
        body = await _json_body(request)
        amount = _read_amount(
            body, "counted_paise", "counted_rupees",
            cap=MAX_CASH_PAISE, allow_zero=True, what="the counted cash")
        day = _valid_day(body.get("day"))
        payload = _record_count("opening", day, amount, "")
        payload["note"] = (
            "This is your count, not the counter's. Everything the cash "
            "position shows from here is measured against it.")
        return JSONResponse(payload)
    except ExpenseRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/cash/closing")
async def cash_closing_ep(request: Request) -> JSONResponse:
    """What you actually counted at the end of the day.

    Body: {counted_paise | counted_rupees, day, note}. Accepted whether or not
    an opening count exists: what is in the drawer is a fact on its own, and
    refusing to record it because an earlier step was skipped would lose the one
    number that cannot be recovered tomorrow.
    """
    try:
        body = await _json_body(request)
        amount = _read_amount(
            body, "counted_paise", "counted_rupees",
            cap=MAX_CASH_PAISE, allow_zero=True, what="the counted cash")
        day = _valid_day(body.get("day"))
        raw = body.get("note", "")
        if raw is None:
            raw = ""
        if not isinstance(raw, str):
            raise ExpenseRefused(
                R_BAD_BODY,
                f"note={raw!r} must be text, not a {type(raw).__name__}.")
        note = " ".join(raw.split())
        if len(note) > MAX_NOTE:
            raise ExpenseRefused(
                R_NOTE_TOO_LONG,
                f"the note is {len(note)} characters and the cap is "
                f"{MAX_NOTE}. Nothing was recorded.")
        payload = _record_count("closing", day, amount, note)
        payload["note"] = (
            "Recorded. Any difference against the expected figure is shown as "
            "a difference and described as nothing else.")
        return JSONResponse(payload)
    except ExpenseRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)
