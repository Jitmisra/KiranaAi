"""HISAAB — closing the shop for the day, and the record of having done it.

`gawaah/manage.py` already answers "aaj kitna hua?". `GET /manage/today`
derives the day's takings from the hash-chained audit log every time it is
asked, which is the right shape for a screen: nothing is cached, nothing is
stored twice, and the answer cannot drift from the chain because it IS the
chain, folded.

This module adds the thing that screen cannot do, which is the ACT of closing.
The shopkeeper reviews the day, counts the drawer, types what he found, and
closes it. What gets written down is the derived figures AS THEY STOOD AT THAT
MOMENT, beside his own count.

WHY THE SNAPSHOT MATTERS, AND WHY THIS IS NOT A CACHE
=====================================================
The chain keeps growing. A day closed on Tuesday must still read the same on
Friday, and a derivation cannot promise that: re-folding the log on Friday
re-reads every line written since, including lines about Tuesday. A late
webhook settles a Tuesday bill on Wednesday morning. A session that was open
when the shutter came down closes an hour later and lands inside Tuesday's
window. `results/shop/catalog.json` is edited and a product is renamed, so the
top seller Tuesday's brief called "Parle-G 200g" is called something else now.
Each of those is a correct thing for the live screen to show and a WRONG thing
for a closed day to change into, because the closed day is what the shopkeeper
signed off on.

So the close-out is a record, not a cache. It is never recomputed, never
refreshed, and never quietly corrected. `GET /daybook/{day}` serves exactly the
numbers that were frozen — and, separately and clearly labelled, what the chain
says about that same day NOW, so a divergence is visible rather than hidden.
Neither figure overwrites the other.

DERIVED THERE, FROZEN HERE
==========================
Nothing in this file folds the audit chain itself. The figures come from
`gawaah.manage` — the same `read_chain()`, `bills_from()`, `_local_day_bounds()`
and `_brief_for()` that draw the History and Today screens — because a second
definition of "the day's takings" is a second truth, and the first time the two
disagreed there would be no way to tell which screen was lying. What this module
owns is the moment, the shopkeeper's count, and the writing down.

CLOSING EARLY IS ALLOWED, AND IS RECORDED AS SUCH
=================================================
A kirana closes when the owner goes home, which is often before midnight and
occasionally after it. Refusing to close a day that has not ended would be a
calendar's opinion imposed on a shop. So an early close is accepted, and the
record carries `day_had_ended: false` with the moment it was actually closed
and how much of the day was still to run. If somebody rings up a bill
afterwards, the frozen figures do not move; the detail endpoint reports the
difference under `after_close`.

CLOSING TWICE IS REFUSED BY NAME
================================
`day_already_closed`, with the moment of the first close in the detail. There is
deliberately no reopen and no overwrite: a close-out that can be replaced is not
a record of anything, and the failure it would hide — a day closed on a figure
the shopkeeper later wished were different — is the exact failure the record
exists to prevent. What it costs when this is wrong: a genuine mistake in the
counted cash cannot be corrected here, and has to be noted against the next
day's close. That is a real cost and it is the cheaper of the two.

WHAT THIS FILE DOES NOT DO
==========================
It does not reconcile the drawer. `GET /cash` in `gawaah/expenses.py` does
that, from a counted opening float, the day's unsettled bills and the expenses
paid in cash. This module records what was counted and shows it beside the
day's takings WITHOUT computing a difference between them, because those two
numbers are not comparable — the takings include bills the gateway settled
straight to the bank, and the drawer includes an opening float and money paid
out for chai. A difference between them would look like an answer and be one.

It settles no money, mints nothing, holds no gateway credential, constructs no
payable string, and never calls the money service — a shopkeeper must be able to
close his day with the payment process stopped. `settles_money` is False on
every response and that is a fact about the code, not a promise.

MOUNTING
========
An ``APIRouter`` with NO prefix and absolute paths::

    from gawaah import daybook
    app.include_router(daybook.router)      # -> /daybook, /daybook/preview

Do not pass a prefix; the paths below are already what a browser asks for. The
static paths are declared BEFORE ``/daybook/{day}`` so that one cannot shadow
them.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .ledger import Ledger, canonical
from .money import MoneyError, from_rupees_str, paise, to_rupees_str

router = APIRouter()


# --------------------------------------------------------------- refusals --
#
# Every one of these is a state this module can actually reach from a request,
# and each is written so a shopkeeper can act on it without reading the code.
# The reason names the STATE; the sentence that says what to do goes in
# `detail`, never in the reason.

R_BAD_BODY = "body_not_a_json_object"
R_BAD_DAY = "day_not_a_calendar_date"
R_DAY_IN_FUTURE = "day_has_not_started"
R_ALREADY_CLOSED = "day_already_closed"
R_NOT_CLOSED = "day_not_closed"
R_NO_CASH = "counted_cash_missing"
R_CASH_TWICE = "counted_cash_given_twice"
R_CASH_NOT_INTEGER = "counted_cash_not_integer_paise"
R_BAD_RUPEES = "counted_cash_not_a_rupee_string"
R_CASH_NEGATIVE = "counted_cash_negative"
R_CASH_TOO_LARGE = "counted_cash_implausible"
R_NOTE_TOO_LONG = "note_too_long"
R_CLOSED_BY_TOO_LONG = "closed_by_too_long"
R_BAD_LIMIT = "limit_not_a_positive_integer"
R_NOT_WRITTEN = "not_written_to_disk"
R_NO_TILL = "till_module_unavailable"
R_NO_BILL_BOOK = "bill_book_unavailable"
R_INTERNAL = "daybook_internal_error"


# ------------------------------------------------------------- the shape of --

CLOSE_FORMAT = 1

#: A day is a calendar day in the counter's own timezone, written the way the
#: rest of this program writes one.
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Five lakh rupees counted out of one drawer. Past this, somebody typed paise
#: where they meant rupees, which is the mistake this bound exists to catch —
#: not a large day's trading, which a kirana does not do in cash. Deliberately
#: the same figure `expenses.MAX_CASH_PAISE` uses for the same reason; it is
#: restated rather than imported so this module does not pull a second router in
#: to read one integer. What it costs when this is wrong: a shop that genuinely
#: holds more than this cannot record the true count and has to say so in the
#: note. It is refused by name, never clamped — a clamped figure would be stored
#: as the shopkeeper's own count when he never typed it.
MAX_COUNTED_CASH_PAISE = 50_000_000

MAX_NOTE = 400
MAX_CLOSED_BY = 80

#: How many products the record names. The units for every SKU are frozen in
#: full beside it; this is only the part a page puts at the top.
TOP_SELLERS = 5

DEFAULT_LIMIT = 60
MAX_LIMIT = 400

DAYBOOK_SUBDIR = "daybook"
AUDIT_FILE = "daybook.audit.jsonl"

#: The two keys the record carries ABOUT itself rather than about the day. They
#: are excluded from the digest by construction — see `_digest_of` — so a reader
#: can recompute the hash from a file on disk without guessing what was hashed.
BINDING_KEYS = ("record_sha256", "audit_head")


class DaybookRefused(Exception):
    """A named refusal with a sentence a shopkeeper can act on."""

    def __init__(self, reason: str, detail: str, status: int = 400) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.status = status


def _refusal(exc: DaybookRefused) -> JSONResponse:
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
    denied: .../daybook' says what to do; 'Internal Server Error' does not.
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
    does not reach the copy serving requests. The symptom would be a day closed
    beside a different shop from the one it was trading in, with nothing
    anywhere saying so.
    """
    import sys

    for name in _TILL_NAMES:
        mod = sys.modules.get(name)
        if mod is not None and _till_ref.is_the_till(mod):
            return mod
    try:
        return _import_till()
    except Exception as exc:  # noqa: BLE001 - a missing till is a named answer
        raise DaybookRefused(
            R_NO_TILL,
            f"tools/upload_app.py is not importable ({type(exc).__name__}: "
            f"{exc}). Closed days are kept beside the shop's own catalogue and "
            f"this module will not guess where that is.") from None


def shop_dir() -> Path:
    """Where this shop's files live — the till's own answer, never a second one.

    This is what honours `GAWAAH_SHOP_DIR`: `upload_app.store_dir()` reads that
    environment variable and `upload_app.set_store_dir()` redirects it for a
    test. Deriving the path here from the environment ourselves would be a
    second answer to one question, and the day a test moved the catalogue and
    the closed days stayed behind is the day a harness overwrites a live shop.
    """
    return Path(_till().store_dir())


def daybook_dir() -> Path:
    """One file per closed day, beside the catalogue it was trading from."""
    return shop_dir() / DAYBOOK_SUBDIR


def audit_path() -> Path:
    """This module's own hash-chained log.

    DELIBERATELY NOT `results/audit.jsonl`. That file is held open by the money
    service in a DIFFERENT PROCESS, which keeps the chain head in memory and
    computes `prev_hash` from it. A second process appending between two of its
    writes gives it a stale head, and every line paisa writes afterwards fails
    `gawaah.ledger.verify` — `make verify-ledger` goes red and the money audit
    trail, the one thing in this program that must be beyond argument, is the
    casualty.

    So the closings get their own chain, in the shop directory, written by the
    one process that owns it and verifiable by exactly the same `verify()`.

    What it costs when this is wrong: there are two chains to walk instead of
    one, and a reader who checks only `results/audit.jsonl` will not see the
    closings. That is a documentation problem. The alternative was a corrupted
    money ledger.
    """
    return shop_dir() / AUDIT_FILE


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _audit(event: str, **fields: Any) -> Optional[str]:
    """Append one auditable line. Returns the new head, or None if it failed.

    THE NOTE AND THE NAME ARE NOT IN THE CHAIN, only their lengths and digests.
    A closing note reads 'short 200, Ramu took it for the gas' often enough that
    it is somebody's name on record, and an audit log is the file most likely to
    end up pasted into a bug report. The digests still bind them: change either
    on disk afterwards and the recorded hash no longer matches.

    Best effort, but never silent: a caller that gets None says so in its
    response rather than reporting a witnessed close that was not.
    """
    try:
        return Ledger(audit_path()).append(
            ts=_now_iso(), module="daybook", event=event, minted=False,
            **fields)
    except Exception:  # noqa: BLE001 - a failed audit must not lose the close
        return None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest_of(record: dict[str, Any]) -> str:
    """The canonical digest of a close-out record, over everything but its own
    binding fields.

    `canonical` is the ledger's own encoder, so this digest is computed exactly
    the way every hash in the audit chain is computed and a reader verifying one
    by hand does not need a second convention. The two binding keys are dropped
    first because they describe the digest rather than the day — leaving them in
    would make the hash unrecomputable from the file it is stored in.
    """
    body = {k: v for k, v in record.items() if k not in BINDING_KEYS}
    return hashlib.sha256(canonical(body)).hexdigest()


# ------------------------------------------------------------ the bill book --


def _import_manage() -> Any:
    """Import the day brief's own derivation. A seam, as `_import_till` is."""
    from . import manage  # noqa: WPS433 - deliberately late, see _bill_book

    return manage


def _bill_book() -> tuple[Callable[..., Any], Callable[..., Any],
                          Callable[..., Any], Callable[..., Any]]:
    """`(read_chain, bills_from, brief_for, day_bounds)` from `gawaah.manage`.

    Imported LATE for two reasons: the orchestrator mounts both routers into one
    app, and `gawaah.manage` pulls in the vision constants through `identity`
    and `takhti`, which a list of closed days should not pay for.

    Imported AT ALL because `/manage/today` already derives every figure a
    close-out freezes, off the same chain, with the same definition of a
    calendar day at this counter. Re-deriving them here would put a second
    opinion in the program about what a day's takings are, and the day the two
    disagreed the shopkeeper would have no way to tell which screen was lying.

    `_brief_for` and `_local_day_bounds` are private to that module, and taking
    a dependency on a private name is a real cost — it can be renamed without
    anybody noticing this file. That is why they are fetched by `getattr` and
    turn into a NAMED REFUSAL when they are missing, rather than an
    AttributeError from inside a route: the failure then says which function
    went, which is the thing a maintainer needs. Copying the derivation instead
    would have been worse in a way nobody would notice for months.
    """
    missing = None
    try:
        mod = _import_manage()
        read_chain = getattr(mod, "read_chain", None)
        bills_from = getattr(mod, "bills_from", None)
        brief_for = getattr(mod, "_brief_for", None)
        day_bounds = getattr(mod, "_local_day_bounds", None)
        for name, fn in (("read_chain", read_chain), ("bills_from", bills_from),
                         ("_brief_for", brief_for),
                         ("_local_day_bounds", day_bounds)):
            if not callable(fn):
                missing = name
                break
    except Exception as exc:  # noqa: BLE001 - a named answer, never a 500
        raise DaybookRefused(
            R_NO_BILL_BOOK,
            f"the day brief could not be read ({type(exc).__name__}: {exc}). "
            f"Closed days already on disk can still be listed; nothing new can "
            f"be closed without the figures to close it on.") from None
    if missing is not None:
        raise DaybookRefused(
            R_NO_BILL_BOOK,
            f"gawaah.manage has no {missing}. This module freezes the figures "
            f"that screen derives rather than deriving its own, so it cannot "
            f"close a day without it. Nothing was changed.")
    return read_chain, bills_from, brief_for, day_bounds


def _names() -> dict[str, str]:
    """{sku_id -> name} as the catalogue reads RIGHT NOW, for freezing.

    Frozen with the record on purpose. A product renamed next month must not
    rename itself inside a day that was closed before the rename, and a product
    deleted next month must not turn into a bare sku id in a record that used to
    read 'Parle-G 200g'.

    An unreadable catalogue is no names, not an error: the units and the money
    come off the chain and are the part that matters. A close that failed
    because a sidecar was hand-edited would be a close nobody could perform on
    the one evening they needed to.
    """
    try:
        mod = _import_manage()
        cat = mod.catalogue()
        items = cat.get("items") or {}
    except Exception:  # noqa: BLE001 - names are decoration on top of counts
        return {}
    out: dict[str, str] = {}
    for sku, row in items.items():
        if isinstance(row, dict):
            out[str(sku)] = str(row.get("name") or sku)
    return out


# ------------------------------------------------------------------- days --


def _local_tz() -> Any:
    return _dt.datetime.now().astimezone().tzinfo


def _today_label() -> str:
    return _dt.datetime.now(_local_tz()).strftime("%Y-%m-%d")


def _valid_day(day: Optional[str]) -> str:
    """A calendar day in the counter's own timezone, defaulting to today.

    Refuses a day in the FUTURE and nothing else about the clock. Closing today
    before midnight is the normal case — shops close early — so a day that has
    started may be closed at any hour of it. A day that has not started has no
    takings to freeze and no drawer to count, so closing it would record an
    empty day as a fact and hide whatever actually happens on it.
    """
    if day is None or (isinstance(day, str) and not day.strip()):
        return _today_label()
    if not isinstance(day, str):
        raise DaybookRefused(
            R_BAD_DAY,
            f"a day is written as YYYY-MM-DD, for example 2026-09-01 — got a "
            f"{type(day).__name__}.")
    text = day.strip()
    if not DAY_RE.match(text):
        raise DaybookRefused(
            R_BAD_DAY,
            f"{day!r} is not a calendar day. Write it as YYYY-MM-DD, for "
            f"example 2026-09-01.")
    try:
        _dt.datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise DaybookRefused(
            R_BAD_DAY,
            f"{day!r} is not a day that exists. Write it as YYYY-MM-DD, for "
            f"example 2026-09-01.") from None
    if text > _today_label():
        raise DaybookRefused(
            R_DAY_IN_FUTURE,
            f"{text} has not started — today is {_today_label()} at this "
            f"counter. A day with nothing in it yet cannot be closed. Nothing "
            f"was changed.")
    return text


# ---------------------------------------------------------- reading a body --


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a named refusal
        raise DaybookRefused(
            R_BAD_BODY,
            'this request\'s body is not JSON. It should look like '
            '{"counted_cash_rupees": "4820.00"}.') from None
    if not isinstance(body, dict):
        raise DaybookRefused(
            R_BAD_BODY,
            f"this request's body is a {type(body).__name__}; it must be a "
            f"JSON object.")
    return body


def _read_counted_cash(body: dict[str, Any]) -> int:
    """What the shopkeeper counted, as integer paise, from either spelling.

    TWO SPELLINGS ON PURPOSE, AND ONLY ONE AT A TIME. A page that converts
    rupees to paise in the browser is a page that writes `parseFloat(x) * 100`,
    and 48.20 * 100 is 4819.999999999999 in every browser on earth. So this
    accepts the rupee STRING the shopkeeper actually typed and parses it with
    `money.from_rupees_str`, which never touches a float. Sending both keys is
    refused rather than resolved: two numbers that disagree have no correct
    winner, and picking one silently is how the wrong one gets frozen into a
    record that cannot be reopened.

    Zero is a valid count and means the drawer was empty. That is a different
    statement from not counting, which is refused — a close-out with no count is
    only a timestamp, and what was in the drawer is the one number on this
    screen that cannot be recovered tomorrow.
    """
    has_int = "counted_cash_paise" in body and body["counted_cash_paise"] is not None
    has_str = "counted_cash_rupees" in body and body["counted_cash_rupees"] is not None

    if has_int and has_str:
        raise DaybookRefused(
            R_CASH_TWICE,
            f"this request counts the drawer twice, as counted_cash_paise="
            f"{body['counted_cash_paise']!r} and counted_cash_rupees="
            f"{body['counted_cash_rupees']!r}. Send one of them. The day was "
            f"not closed.")
    if not has_int and not has_str:
        raise DaybookRefused(
            R_NO_CASH,
            "no count of the drawer in the body. Send counted_cash_paise as "
            "whole paise, or counted_cash_rupees as a string like \"4820.00\". "
            "Zero is a valid count and means the drawer was empty. The day was "
            "not closed.")

    if has_int:
        raw = body["counted_cash_paise"]
        # bool first: True is an int in Python and a drawer holding True is not
        # a thing anybody meant.
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise DaybookRefused(
                R_CASH_NOT_INTEGER,
                f"counted_cash_paise={raw!r} is not whole paise. Money here is "
                f"integer paise — 4820.00 rupees is 482000 — because a rupee "
                f"that is a decimal fraction stops adding up. Send the rupees "
                f"as a string in counted_cash_rupees if that is easier.")
        try:
            amount = int(paise(raw))
        except MoneyError as exc:
            raise DaybookRefused(
                R_CASH_NOT_INTEGER,
                f"counted_cash_paise={raw!r} is not money ({exc}).") from None
    else:
        raw = body["counted_cash_rupees"]
        if not isinstance(raw, str) or not raw.strip():
            raise DaybookRefused(
                R_BAD_RUPEES,
                f"counted_cash_rupees={raw!r} is not a rupee amount. Write it "
                f"as a string, like \"4820.00\".")
        try:
            amount = int(from_rupees_str(raw))
        except MoneyError as exc:
            raise DaybookRefused(
                R_BAD_RUPEES,
                f"counted_cash_rupees={raw!r} could not be read as rupees "
                f"({exc}). Two decimal places at most; a shop does not deal in "
                f"half paise.") from None

    if amount < 0:
        raise DaybookRefused(
            R_CASH_NEGATIVE,
            f"the drawer was counted at {to_rupees_str(paise(amount))} rupees. "
            f"A negative count is not a thing a drawer can hold; zero is, and "
            f"means it was empty. The day was not closed.")
    if amount > MAX_COUNTED_CASH_PAISE:
        raise DaybookRefused(
            R_CASH_TOO_LARGE,
            f"the drawer was counted at {to_rupees_str(paise(amount))} rupees, "
            f"past the {to_rupees_str(paise(MAX_COUNTED_CASH_PAISE))} this "
            f"counter records in one close. Check whether paise were typed "
            f"where rupees were meant. Nothing was stored.")
    return amount


def _read_text(body: dict[str, Any], key: str, cap: int,
               reason: str) -> str:
    raw = body.get(key, "")
    if raw is None:
        raw = ""
    if not isinstance(raw, str):
        raise DaybookRefused(
            R_BAD_BODY,
            f"{key}={raw!r} must be text, not a {type(raw).__name__}.")
    text = " ".join(raw.split())
    if len(text) > cap:
        raise DaybookRefused(
            reason,
            f"{key} is {len(text)} characters and the cap is {cap}. The day "
            f"was not closed.")
    return text


def _read_limit(limit: Optional[str]) -> int:
    if limit is None or not str(limit).strip():
        return DEFAULT_LIMIT
    text = str(limit).strip()
    if not text.isdigit() or int(text) <= 0:
        raise DaybookRefused(
            R_BAD_LIMIT,
            f"limit={limit!r} is not a positive whole number of days. Leave it "
            f"out for {DEFAULT_LIMIT}.")
    return min(int(text), MAX_LIMIT)


# -------------------------------------------------------------- on the disk --


def _closed_path(day: str) -> Path:
    """The file one closed day lives in.

    `day` has already been through `_valid_day`, which matches it against a
    strict `\\d{4}-\\d{2}-\\d{2}` before it is ever joined to a path. That shape
    check is what stops a request for `../../catalog` reading the shop's price
    list, and it is cheaper than trusting every caller downstream to remember.
    """
    return daybook_dir() / f"{day}.json"


def _store(path: Path, doc: dict[str, Any], what: str) -> None:
    """Write via a temp file and rename, or refuse by name.

    Atomic because a reader must never see half a close-out: a truncated record
    read back tomorrow would look like a day that was closed on nothing.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(doc, sort_keys=True, indent=1) + "\n",
                       encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise DaybookRefused(
            R_NOT_WRITTEN,
            f"{what} could not be written to {path} ({type(exc).__name__}: "
            f"{exc}). The day was NOT closed, so the page is not about to show "
            f"you a close-out that is not on disk.") from None


def _read_closed(day: str) -> Optional[dict[str, Any]]:
    """The record for one closed day, or None if that day is not closed.

    A file that will not parse is treated as not closed rather than as a crash —
    but only for READING. `close_ep` checks the path's existence, not this, so a
    corrupt record can never be silently overwritten by a second close.
    """
    try:
        doc = json.loads(_closed_path(day).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - a corrupt record is not an outage
        return None
    if not isinstance(doc, dict) or doc.get("day") != day:
        return None
    return doc


def _all_closed() -> list[dict[str, Any]]:
    """Every closed day, newest first. An unreadable file is skipped, not fatal.

    One file per day and a glob to list them. A shop closes once a day, so ten
    years is under four thousand small files; if that ever bites, the fix is an
    index, not a rewrite of the records.
    """
    out: list[dict[str, Any]] = []
    directory = daybook_dir()
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        if not DAY_RE.match(path.stem):
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - one bad file must not hide the rest
            continue
        if isinstance(doc, dict) and doc.get("day") == path.stem:
            out.append(doc)
    # `day` is YYYY-MM-DD, so lexical order IS chronological order.
    out.sort(key=lambda d: str(d.get("day") or ""), reverse=True)
    return out


# ------------------------------------------------------------ the derivation --


def _int_or_none(value: Any) -> Optional[int]:
    """A whole number, or None. bool is not a number here."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _derive(day: str) -> dict[str, Any]:
    """The day's figures as `/manage/today` would state them, right now.

    Nothing is computed here. The window, the fold and the arithmetic all belong
    to `gawaah.manage`; this assembles what it returns, adds the product names
    as they read at this moment, and hands the lot back to be frozen or shown.
    """
    read_chain, bills_from, brief_for, day_bounds = _bill_book()
    start, end, label = day_bounds(day)
    records, chain = read_chain()
    bills = bills_from(records)
    brief = dict(brief_for(bills, start, end))

    names = _names()
    units = dict(brief.get("units_by_sku") or {})
    per_sku = dict(brief.get("line_revenue_by_sku") or {})
    ranked = sorted(units.items(), key=lambda kv: (-int(kv[1]), str(kv[0])))
    sellers = []
    for sku, n_units in ranked[:TOP_SELLERS]:
        earned = _int_or_none(per_sku.get(sku)) or 0
        sellers.append({
            "sku_id": sku,
            # The name AS IT READS TODAY, frozen with the record. See `_names`.
            "name": names.get(sku, sku),
            "units": int(n_units),
            "revenue_paise": earned,
            "revenue_rupees": to_rupees_str(paise(earned)),
            "in_catalogue_at_close": sku in names,
        })

    return {
        "day": label,
        "derived": brief,
        "top_sellers": sellers,
        "chain": chain,
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "start": start,
        "end": end,
    }


# ----------------------------------------------------------- reconciliation --
#
# WHY THIS LIVES HERE AND NOT ON THE DAY BRIEF.
#
# `/manage/today` answers "aaj kitna hua?" — what was billed, what settled, what
# is awaited. It is correct and it is not enough, because every figure on it is
# a count of BILLS and the questions a shopkeeper actually loses money to are
# about the gaps between books:
#
#   * A bill that closed and for which no payment link was ever minted. The day
#     brief counts it under "awaiting the gateway", which is a sentence about a
#     link that does not exist. Measured on this counter's own chain the day
#     this was written: 238 bills closed, 233 links minted, 5 bills the gateway
#     was never asked for. Those 5 are not awaiting anything.
#   * A webhook that arrived and was REFUSED — a bad signature, or a session
#     this counter has never heard of. `paisa /health` reports `webhooks_seen`
#     for the CURRENT PROCESS, so a restarted money service reports zero while
#     the chain holds fifteen. A screen that prints "no webhook has reached this
#     counter" over a chain recording eleven bad-signature posts is not merely
#     unhelpful; it is the opposite of the truth, and the eleven are the single
#     most alarming thing in the log.
#   * A mint the gateway ERRORED on, and a mint this counter REFUSED. Both are
#     money that did not move and neither appears on any books screen.
#
# NOTHING HERE IS NETTED OUT. Each of these is reported as its own count and its
# own integer-paise figure, beside the day brief's, and no figure is adjusted to
# make another agree with it. A disagreement between what the till billed and
# what the gateway did is the most valuable thing this counter can say, and the
# moment it is silently reconciled it stops being sayable.
#
# THE MONEY SERVICE IS STILL NOT CALLED. Everything below is folded out of
# `results/audit.jsonl`, which is where paisa writes its own lines — so the
# gateway's side of the story is already in the chain and this module keeps its
# promise that a day can be closed with the payment process stopped.

#: How a session id names where the bill was rung up.
#:
#: These are not guesses at a naming convention: `gawaah/storefront.py` writes
#: `session_id = f"shop_{doc['order_id']}"` where an order id is `ord_` plus
#: twelve hex characters, and the till writes `till_<...>`. Anything else is
#: reported as UNNAMED rather than being pushed into whichever bucket is
#: closer — a demo session, a probe and a hand-written id all land there, and a
#: books screen that filed them under "till" would be inventing a channel.
CHANNEL_PREFIXES = (("storefront", "shop_ord_"), ("till", "till_"))
CHANNEL_UNNAMED = "unnamed"


def _channel_of(session_id: str) -> str:
    for name, prefix in CHANNEL_PREFIXES:
        if session_id.startswith(prefix):
            return name
    return CHANNEL_UNNAMED


def _bucket(bills: int = 0, paise_total: int = 0) -> dict[str, Any]:
    """One count-and-money pair, in integer paise, with its rupee string."""
    return {
        "bills": bills,
        "paise": paise_total,
        "rupees": to_rupees_str(paise(paise_total)),
    }


def _add(bucket: dict[str, Any], amount: Any) -> None:
    """Fold one bill into a bucket. A bill with no recorded total adds nothing
    to the money and still counts as a bill — dropping it would make the counts
    and the figures describe different sets."""
    bucket["bills"] += 1
    whole = _int_or_none(amount)
    if whole is not None:
        bucket["paise"] += whole
    bucket["rupees"] = to_rupees_str(paise(bucket["paise"]))


def _reconcile_bills(day_bills: list[dict[str, Any]]) -> dict[str, Any]:
    """Split one window's closed bills into the states that actually differ.

    The five buckets are DISJOINT and they sum back to `billed`, so a reader can
    check the split by addition rather than trusting it. The order of the tests
    is the order of authority:

      settled            a signature-verified webhook matched this session
      settled_unwitnessed  the payment kernel recorded a settlement and the
                         webhook line is not in this chain. INVARIANT 2 says
                         only the first of these may be called settled, so this
                         is counted apart and never added in. It is money that
                         may well have arrived; it is not money this counter can
                         witness, and the difference is the whole product.
      awaiting           a link exists and no verified webhook has matched it.
                         THIS IS TESTED BEFORE `refused`, and the order is the
                         fix: a refusal is an event on a session, not a state of
                         it, and this counter retries — 95 of 422 sessions on
                         the live chain have had a link minted more than once.
                         A basket the money service declined at 14:02 and minted
                         a link for at 14:03 is waiting on the gateway like any
                         other, and the customer may already have paid it.
                         Bucketing it under `refused` because a refusal line
                         exists took a live link out of the awaited money and
                         printed "money that did not move" over it.
      refused            the money service declined to mint AND no link was ever
                         minted after that, so nothing was left to pay against.
      never_asked        the basket closed, nothing was minted and nothing was
                         refused — the gateway was never asked at all.
    """
    out = {
        "billed": _bucket(),
        "settled": _bucket(),
        "settled_unwitnessed": _bucket(),
        "refused": _bucket(),
        "never_asked": _bucket(),
        "awaiting": _bucket(),
        "owed": _bucket(),
        "by_channel": {},
    }
    channels: dict[str, dict[str, Any]] = {}
    for b in day_bills:
        total = b.get("total_paise")
        _add(out["billed"], total)

        chan = _channel_of(str(b.get("session_id") or ""))
        row = channels.setdefault(chan, {"billed": _bucket(), "settled": _bucket()})
        _add(row["billed"], total)

        witnessed = bool(b.get("settled")) and b.get("settled_by") == "webhook"
        if witnessed:
            _add(out["settled"], total)
            _add(row["settled"], total)
            continue
        # Everything past this line is money the gateway has not confirmed to
        # this counter, so it is all owed — including the unwitnessed kernel
        # settlement, which is exactly the case where netting it out would hide
        # the one bill worth looking at.
        _add(out["owed"], total)
        if b.get("settled"):
            _add(out["settled_unwitnessed"], total)
        elif b.get("minted"):
            # A LIVE LINK OUTRANKS AN EARLIER REFUSAL. `bills_from` keeps every
            # refusal on the bill, so a session the money service declined once
            # and minted for on the retry carries both flags; testing
            # `refusals` first filed it under "the counter refused to charge"
            # and dropped it out of the awaited money, over a link the customer
            # could still be paying. Measured: one bill, refused at 09:00:02 and
            # minted at 09:00:03, came back `refused Rs 30.00 / awaiting Rs 0`.
            _add(out["awaiting"], total)
        elif b.get("refusals"):
            _add(out["refused"], total)
        else:
            _add(out["never_asked"], total)

    out["by_channel"] = dict(sorted(channels.items()))
    return out


#: Reasons a webhook post did NOT turn a bill green, as `gawaah/webhook.py` and
#: `gawaah/paisa.py` record them. Named here so the count can be split into
#: "arrived and was trusted" and "arrived and was refused" without a screen
#: having to know the vocabulary.
WEBHOOK_GREEN = "green"


def _reconcile_events(records: list[dict[str, Any]],
                      start: _dt.datetime,
                      end: _dt.datetime) -> dict[str, Any]:
    """What happened at the gateway in this window, from the chain's own lines.

    These are EVENTS, not bills, and they are counted separately for that
    reason: eleven bad-signature webhook posts may carry no session id at all,
    and five gateway errors may all belong to sessions that later minted
    successfully. Folding them into a bill count would make both numbers wrong.
    """
    mints = 0
    mint_sessions: set[str] = set()
    gateway_errors = 0
    mint_refusals: dict[str, int] = {}
    webhooks_total = 0
    webhooks_green = 0
    webhooks_refused: dict[str, int] = {}
    abstained_lines = 0

    for rec in records:
        at = _parse_iso(rec.get("ts"))
        if at is None or not (start <= at < end):
            continue
        module = rec.get("module")
        event = rec.get("event")
        reason = rec.get("reason")

        if module == "paisa" and event == "intent.minted":
            mints += 1
            sid = rec.get("session_id")
            if isinstance(sid, str) and sid:
                mint_sessions.add(sid)
        elif module == "paisa" and event == "intent.gateway_error":
            gateway_errors += 1
        elif module == "paisa" and event == "intent.refused":
            key = str(reason or "unnamed")
            mint_refusals[key] = mint_refusals.get(key, 0) + 1
        elif module == "webhook":
            # `gawaah/webhook.py` writes one line per POST it received, before
            # anything decides whether to believe it. That is the only place in
            # this program that knows a webhook arrived at all — a post with a
            # bad signature never reaches a session, so it exists nowhere else.
            webhooks_total += 1
            if reason == WEBHOOK_GREEN:
                webhooks_green += 1
            else:
                key = str(reason or "unnamed")
                webhooks_refused[key] = webhooks_refused.get(key, 0) + 1
        elif module == "session" and event == "exit":
            if rec.get("excluded_from_total") or rec.get("abstained"):
                abstained_lines += 1

    return {
        "mint_attempts": mints,
        "sessions_minted": len(mint_sessions),
        # WHY THIS IS A COUNT AND NOT A VERDICT. 517 mints against 422 sessions
        # looks like a discrepancy until it is named, so it is named — but
        # nothing here has established whether a second mint on one session is
        # a retry policy working or a defect, and the earlier wording of this
        # comment called it a retry as though that had been checked. The chain
        # records that a second link was issued; it does not record why. The
        # figure is reported for a reader who can answer that, and no screen
        # draws a conclusion from it.
        "mints_beyond_one_per_session": max(0, mints - len(mint_sessions)),
        "gateway_errors": gateway_errors,
        "mint_refusals": dict(sorted(mint_refusals.items())),
        "mint_refusals_total": sum(mint_refusals.values()),
        "webhooks_received": webhooks_total,
        "webhooks_green": webhooks_green,
        "webhooks_refused": dict(sorted(webhooks_refused.items())),
        "webhooks_refused_total": sum(webhooks_refused.values()),
        "abstained_lines": abstained_lines,
    }


def _parse_iso(value: Any) -> Optional[_dt.datetime]:
    """A chain timestamp, or None. Never raises: a line with an unreadable `ts`
    is left out of every window rather than being guessed into one."""
    if not isinstance(value, str) or not value:
        return None
    try:
        at = _dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if at.tzinfo is None:
        return None
    return at


def _disagreements(day: dict[str, Any], events: dict[str, Any],
                   label: str) -> list[dict[str, Any]]:
    """The things worth saying out loud, each with the figures behind it.

    A screen may choose how to draw these; it may not decide whether they are
    true. Every entry below is emitted only when its own condition holds on this
    chain, and each carries the numbers a shopkeeper would check it with.
    """
    out: list[dict[str, Any]] = []

    refused_wh = events["webhooks_refused_total"]
    if refused_wh:
        named = ", ".join(f"{n} {r.replace('_', ' ')}"
                          for r, n in events["webhooks_refused"].items())
        out.append({
            "code": "webhooks_arrived_and_were_refused",
            "count": refused_wh,
            "headline": (f"{refused_wh} webhook post"
                         f"{'' if refused_wh == 1 else 's'} reached this "
                         f"counter {label} and {'was' if refused_wh == 1 else 'were'} "
                         f"not trusted"),
            "detail": (f"{named}. A post the signature check refuses never "
                       f"reaches a bill, so it turns nothing green and appears "
                       f"in no total. It is recorded here because the money "
                       f"service reports only what it has seen since it last "
                       f"started, and a counter that has been restarted will "
                       f"say it has seen none of these."),
        })

    if events["gateway_errors"]:
        n = events["gateway_errors"]
        out.append({
            "code": "the_gateway_errored_on_a_mint",
            "count": n,
            "headline": (f"the gateway failed {n} time{'' if n == 1 else 's'} "
                         f"{label} while a link was being issued"),
            "detail": ("The basket was priced and the counter asked for a "
                       "payment link; the gateway did not give one. No money "
                       "was refused and none was taken — the customer was left "
                       "with nothing to pay against."),
        })

    never = day["never_asked"]
    if never["bills"]:
        n = never["bills"]
        out.append({
            "code": "bills_the_gateway_was_never_asked_for",
            "count": n,
            "paise": never["paise"],
            # "Rs", not "₹": these sentences are also read back through the
            # assistant's text-to-speech and end up in log lines, and the rest
            # of the server writes money this way (see gawaah/assistant.py).
            "headline": (f"{n} bill{'' if n == 1 else 's'} {label} — "
                         f"Rs {never['rupees']} — closed with no payment link"),
            "detail": ("These are not waiting on the gateway; nothing was ever "
                       "asked of it for them. They are counted apart from the "
                       "awaited money for that reason, and they are still owed."),
        })

    unwitnessed = day["settled_unwitnessed"]
    if unwitnessed["bills"]:
        n = unwitnessed["bills"]
        out.append({
            "code": "settled_without_a_webhook_line",
            "count": n,
            "paise": unwitnessed["paise"],
            "headline": (f"{n} bill{'' if n == 1 else 's'} {label} "
                         f"{'is' if n == 1 else 'are'} recorded settled with no "
                         f"verified webhook behind {'it' if n == 1 else 'them'}"),
            "detail": ("The payment kernel recorded a settlement and the "
                       "webhook line is not in this chain. That money is not "
                       "counted as settled anywhere on these books, because on "
                       "this counter only a signature-verified webhook may say "
                       "so. It is reported instead of being quietly promoted."),
        })

    refused = day["refused"]
    if refused["bills"]:
        n = refused["bills"]
        out.append({
            "code": "the_counter_refused_to_charge",
            "count": n,
            "paise": refused["paise"],
            "headline": (f"the counter refused to charge {n} "
                         f"basket{'' if n == 1 else 's'} {label}"),
            # "did not move" is only true because the bucket above it now takes
            # every bill that got a link on a retry. These are the baskets left
            # with nothing to pay against, so the sentence is safe to make.
            "detail": ("The money service re-prices every basket from its own "
                       "book and declines to mint when the two disagree. No "
                       "link was minted for these afterwards, so a refusal is "
                       "the product working and it is money that did not "
                       "move."),
        })

    return out


def _reconcile(day: str) -> dict[str, Any]:
    """The day's books and the counter's whole trading, reconciled and unnetted.

    Both windows are folded from ONE walk of the chain. Reading it twice would
    let a line written between the two walks appear in one figure and not the
    other, which is a discrepancy the screen would then report as the shop's.
    """
    read_chain, bills_from, _brief, day_bounds = _bill_book()
    start, end, label = day_bounds(day)
    records, chain = read_chain()
    records = list(records)
    bills = bills_from(records)

    closed = [b for b in bills.values() if b.get("closed")]
    in_window = []
    for b in closed:
        at = _parse_iso(b.get("at"))
        if at is not None and start <= at < end:
            in_window.append(b)

    # Lifetime has no window, so the event fold is asked about one wide enough
    # to hold the chain rather than being given a second code path.
    far_past = _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)
    far_future = _dt.datetime.max.replace(tzinfo=_dt.timezone.utc)

    day_events = _reconcile_events(records, start, end)
    all_events = _reconcile_events(records, far_past, far_future)
    day_money = _reconcile_bills(in_window)
    all_money = _reconcile_bills(closed)

    return {
        "day": label,
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "chain": chain,
        "today": {**day_money, "events": day_events,
                  "disagreements": _disagreements(day_money, day_events, "today")},
        "lifetime": {**all_money, "events": all_events,
                     "disagreements": _disagreements(all_money, all_events,
                                                     "on this counter")},
        "derived_from": RECONCILE_NOTE,
    }


RECONCILE_NOTE = (
    "Every figure here is folded out of the hash-chained audit log — the same "
    "read_chain() and bills_from() the Today and History screens use — in one "
    "walk, in integer paise. The money service is not called: paisa writes its "
    "own lines into that chain, so the gateway's side of this is already in it. "
    "Settled means a signature-verified webhook matched the session and nothing "
    "else. No figure here has been adjusted to agree with another.")


def _chain_warning(chain: dict[str, Any]) -> Optional[str]:
    """Say it out loud when the figures are short, and do not adjust them.

    `read_chain` stops at the first link whose hash does not recompute and
    returns the verified prefix. That is the right call for a screen — a
    shopkeeper whose chain broke this morning can still see last week — but a
    close-out FREEZES the number, so a day closed over a broken chain records
    takings that are lower than the day's trading by whatever came after the
    break, and it records them permanently.

    Closing is still allowed. Refusing would leave a shop with one corrupt line
    unable to close a day ever again, and the information is not lost: the
    chain's state is frozen INTO the record beside the figures, so a reader on
    Friday can see that Tuesday was closed over a chain that did not verify
    past line N. Nothing is adjusted to cover the gap.
    """
    if not chain.get("exists") or chain.get("ok"):
        return None
    return (
        f"The audit chain does not verify past line "
        f"{chain.get('lines_verified')} ({chain.get('error')}). Any bill "
        f"recorded after that line is not in the figures above, so the day's "
        f"takings read LOW by whatever those bills came to. Nothing has been "
        f"adjusted to hide that. A day closed in this state is closed on a "
        f"short figure, and the record says so.")


def _summary(doc: dict[str, Any]) -> dict[str, Any]:
    """One closed day as a LIST row. The full record stays behind the detail
    endpoint — a year of closings each carrying every sku's units is a megabyte
    of JSON to draw twelve rows."""
    brief = doc.get("derived") or {}
    counted = _int_or_none(doc.get("counted_cash_paise"))
    chain = doc.get("chain_at_close") or {}
    return {
        "day": doc.get("day"),
        "closed_at": doc.get("closed_at"),
        "day_had_ended": bool(doc.get("day_had_ended")),
        "closed_by": doc.get("closed_by") or "",
        "note": doc.get("note") or "",
        "bills": _int_or_none(brief.get("bills")),
        "revenue_paise": _int_or_none(brief.get("revenue_paise")),
        "revenue_rupees": brief.get("revenue_rupees"),
        "settled_count": _int_or_none(brief.get("settled_count")),
        "settled_paise": _int_or_none(brief.get("settled_paise")),
        "awaiting_count": _int_or_none(brief.get("awaiting_count")),
        "awaiting_paise": _int_or_none(brief.get("awaiting_paise")),
        "counted_cash_paise": counted,
        "counted_cash_rupees": (to_rupees_str(paise(counted))
                                if counted is not None else None),
        "chain_verified_at_close": bool(chain.get("ok")),
        "record_sha256": doc.get("record_sha256"),
        "audit_head": doc.get("audit_head"),
    }


#: The sentence that goes on every response carrying a counted figure. It is one
#: string so the two endpoints cannot drift into saying different things about
#: the same number.
CASH_NOTE = (
    "The counted cash is your own count of the drawer, recorded as you gave it. "
    "It is not compared with the day's takings here, because the two are not "
    "the same thing: the takings include bills the gateway settled straight to "
    "the account, and the drawer includes the float you started with and "
    "whatever was paid out of it. The cash drawer on the Expenses screen "
    "reconciles the drawer; this record only witnesses what you counted.")
# NAMED BY THE SCREEN, NOT BY THE ROUTE. This sentence used to send the reader
# to "/cash", which is `GET /cash` in gawaah/expenses.py — a JSON endpoint with
# no page behind it. A shopkeeper typing that into the address bar gets a wall
# of JSON, and there is no "cash" entry in the sidebar to find instead: the
# drawer is drawn on the Expenses screen, under "The cash drawer".

DERIVED_NOTE = (
    "Every figure under 'derived' is counted from the hash-chained audit log "
    "for this calendar day, in this counter's own timezone, by the same code "
    "that draws the day brief. Nothing is estimated.")


# ------------------------------------------------------------------ routes --
#
# The two static paths are declared BEFORE `/daybook/{day}`. A day is matched
# against a strict date shape, so `preview` would be refused by name rather than
# read as a date — but the order is kept anyway, because relying on a refusal to
# stop a route colliding is one rename away from a bug.


@router.get("/daybook/reconcile")
def reconcile_ep(day: str | None = None) -> JSONResponse:
    """What the till billed against what the gateway actually did, unnetted.

    `?day=YYYY-MM-DD` for a past day; the response always carries the lifetime
    figures beside the day's, because "nothing settled today" and "nothing has
    ever settled here" are different shops and a day-shaped answer cannot tell
    them apart.
    """
    try:
        wanted = _valid_day(day)
        state = _reconcile(wanted)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            **state,
            "chain_warning": _chain_warning(state["chain"]),
        })
    except DaybookRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/daybook/preview")
def preview_ep(day: str | None = None) -> JSONResponse:
    """What closing right now would write down. `?day=YYYY-MM-DD` for a past day.

    This is the review step: the same figures the close will freeze, in the same
    shape, so what the shopkeeper reads on the screen is what ends up in the
    record. It writes nothing.

    A day that is already closed is still previewable — the response says so and
    carries the moment it was closed, so a page can show that instead of drawing
    a button that is going to be refused.
    """
    try:
        wanted = _valid_day(day)
        state = _derive(wanted)
        existing = _read_closed(wanted)

        now = _dt.datetime.now(_local_tz())
        ended = now >= state["end"]
        # Integer seconds. This is a duration, not money, and it is reported so
        # that "closed early" is a quantity rather than a feeling.
        left = 0 if ended else int((state["end"] - now).total_seconds())

        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "day": wanted,
            "already_closed": existing is not None,
            "closed_at": None if existing is None else existing.get("closed_at"),
            "day_has_ended": ended,
            "seconds_left_in_day": left,
            "closing_early_note": (
                "This day has ended, so closing it now freezes a day that is "
                "finished." if ended else
                "This day has not ended yet. Closing it now is allowed — shops "
                "close early — and the record will say when it was actually "
                "closed. Anything rung up afterwards will not change the "
                "figures in it; it will be reported separately when the closed "
                "day is read back."),
            "derived": state["derived"],
            "top_sellers": state["top_sellers"],
            "window": state["window"],
            "chain": state["chain"],
            "chain_warning": _chain_warning(state["chain"]),
            "needs": {
                "counted_cash_paise": "whole paise, or",
                "counted_cash_rupees": 'a string like "4820.00"',
                "note": f"optional, up to {MAX_NOTE} characters",
                "closed_by": f"optional, up to {MAX_CLOSED_BY} characters",
            },
            "derived_from": DERIVED_NOTE,
            "note": CASH_NOTE,
        })
    except DaybookRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/daybook/close")
async def close_ep(request: Request) -> JSONResponse:
    """Close the day. The figures are frozen as they stand; the count is yours.

    Body: {counted_cash_paise | counted_cash_rupees, day, note, closed_by}.
    `day` defaults to today at this counter and may not be in the future. A day
    that is already closed is refused by name — there is no reopen and no
    overwrite, because a close-out that can be replaced is not a record.
    """
    try:
        body = await _json_body(request)
        amount = _read_counted_cash(body)
        note = _read_text(body, "note", MAX_NOTE, R_NOTE_TOO_LONG)
        closed_by = _read_text(body, "closed_by", MAX_CLOSED_BY,
                               R_CLOSED_BY_TOO_LONG)
        wanted = _valid_day(body.get("day"))

        # Existence of the FILE, not a successful read: a record that will not
        # parse is still a day somebody closed, and letting a second close
        # overwrite it would destroy the only copy of the first one.
        if _closed_path(wanted).exists():
            existing = _read_closed(wanted)
            when = (existing or {}).get("closed_at")
            raise DaybookRefused(
                R_ALREADY_CLOSED,
                f"{wanted} was already closed"
                + (f" at {when}" if when else "")
                + ". A close-out is a record and this counter does not reopen "
                  "or overwrite one. Nothing was changed. If the count was "
                  "wrong, say so in a note on the next day's close.")

        state = _derive(wanted)
        now_local = _dt.datetime.now(_local_tz())
        ended = now_local >= state["end"]
        left = 0 if ended else int((state["end"] - now_local).total_seconds())

        record: dict[str, Any] = {
            "format": CLOSE_FORMAT,
            "day": wanted,
            # WHEN IT WAS ACTUALLY CLOSED, which is the point of recording it.
            # UTC, as every other stamp in this program is, with the local
            # reading beside it because that is the one the shopkeeper
            # remembers.
            "closed_at": _now_iso(),
            "closed_at_local": now_local.isoformat(),
            "day_had_ended": ended,
            "seconds_left_in_day_at_close": left,
            "closed_by": closed_by,
            "note": note,
            "counted_cash_paise": amount,
            "counted_cash_rupees": to_rupees_str(paise(amount)),
            # The figures AS THEY STOOD. Never recomputed, never refreshed.
            "derived": state["derived"],
            "top_sellers": state["top_sellers"],
            "window": state["window"],
            "chain_at_close": state["chain"],
            "chain_warning_at_close": _chain_warning(state["chain"]),
            "derived_from": DERIVED_NOTE,
        }

        # Digest FIRST, chain SECOND, disk THIRD. The chain line carries the
        # digest of the record, so an edit to the day file afterwards no longer
        # matches a hash that was written before the file existed. Writing the
        # file first and hashing after would let a crash in between leave a
        # closed day with nothing standing behind it. The cost of this order is
        # that a failed write leaves a `day.closed` line for a close that did
        # not land — which is why the write failure appends its own retraction,
        # below.
        digest = _digest_of(record)
        head = _audit(
            "day.closed",
            day=wanted,
            closed_at=record["closed_at"],
            day_had_ended=ended,
            counted_cash_paise=amount,
            bills=_int_or_none(state["derived"].get("bills")),
            revenue_paise=_int_or_none(state["derived"].get("revenue_paise")),
            settled_paise=_int_or_none(state["derived"].get("settled_paise")),
            awaiting_paise=_int_or_none(state["derived"].get("awaiting_paise")),
            chain_ok_at_close=bool(state["chain"].get("ok")),
            chain_head_at_close=state["chain"].get("head"),
            record_sha256=digest,
            note_len=len(note),
            note_sha256=_sha256(note),
            closed_by_len=len(closed_by),
            closed_by_sha256=_sha256(closed_by),
        )
        doc = {**record, "record_sha256": digest, "audit_head": head}
        try:
            _store(_closed_path(wanted), doc, "this close-out")
        except DaybookRefused:
            # The chain already carries `day.closed` for a close that never
            # reached the disk. A chain saying a day was closed when it was not
            # is worse than the failed write itself, so the retraction is
            # appended to the CHAIN rather than to a log nobody reads. Both
            # lines survive; a reader sees the attempt and sees it fail. The day
            # stays open and can be closed again once the disk is fixed.
            _audit("day.close_not_written", day=wanted, record_sha256=digest)
            raise

        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "day": wanted,
            "closed": True,
            "record": doc,
            "audited": head is not None,
            "chain_warning": record["chain_warning_at_close"],
            "note": (
                "This day is closed. The figures above are frozen as they stood "
                "at " + record["closed_at"] + " and will not change when the "
                "audit chain grows. " + CASH_NOTE),
        })
    except DaybookRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(DaybookRefused(
            R_CASH_NOT_INTEGER,
            f"a figure in this close-out is not integer paise ({exc}). The day "
            f"was not closed."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/daybook")
def list_ep(limit: str | None = None) -> JSONResponse:
    """The days this shop has closed, newest first.

    Rows only. The frozen figures for one day are behind `/daybook/{day}`.
    `truncated` says when there were more, so a capped list never reads as a
    complete one.
    """
    try:
        want = _read_limit(limit)
        rows = _all_closed()
        shown = rows[:want]
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "count": len(shown),
            "days_on_record": len(rows),
            "truncated": len(rows) > len(shown),
            "limit": want,
            "days": [_summary(d) for d in shown],
            "dir": str(daybook_dir()),
            "note": ("Each row is what was frozen when the day was closed, not "
                     "what the audit chain says about that day now. Read one "
                     "day to see both."),
        })
    except DaybookRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


def _milan_beside(day: str) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """The settlement match for a closed day, read-only, or the reason it is
    missing. A seam like `_bill_book`: imported late, absent by name, and
    never a 500 — the record is the point of the endpoint that calls this."""
    try:
        from . import milan  # noqa: WPS433 - deliberately late
    except Exception as exc:  # noqa: BLE001
        return None, f"gawaah/milan.py is not importable ({type(exc).__name__}: {exc})"
    fn = getattr(milan, "summary_beside_close", None)
    if not callable(fn):
        return None, "gawaah/milan.py has no summary_beside_close"
    try:
        return fn(day), None
    except Exception as exc:  # noqa: BLE001 - a named absence, never a crash
        reason = getattr(exc, "reason", None)
        detail = getattr(exc, "detail", None)
        if reason and detail:
            return None, f"{reason}: {detail}"
        return None, f"{type(exc).__name__}: {exc}"


@router.get("/daybook/{day}")
def one_day_ep(day: str) -> JSONResponse:
    """One closed day in full, exactly as it was frozen — plus what changed.

    `record` is the close-out and is served byte for byte as it was written. It
    is never recomputed and never corrected.

    `after_close` is a SEPARATE block: the same day, derived from the chain as
    it stands right now, and the difference against the frozen figures. A day
    closed at seven that took another bill at eight shows one extra bill there
    and an unchanged record. A late webhook that settled a bill the next morning
    shows up there too. Neither touches the record, and the record is not the
    thing that is wrong when they differ — that is what closing early means.

    A negative difference is possible and is reported as one: it means the chain
    no longer verifies as far as it did when the day was closed, so the live
    figures are SHORTER than the frozen ones. That is a fact about the chain, not
    about the day, and inventing a zero to hide it would be the worse answer.
    """
    try:
        wanted = _valid_day(day)
        doc = _read_closed(wanted)
        if doc is None:
            raise DaybookRefused(
                R_NOT_CLOSED,
                f"{wanted} has not been closed on this counter, so there is no "
                f"record of closing it. What that day took is still readable "
                f"from the audit chain at /daybook/preview?day={wanted} and at "
                f"/manage/today?day={wanted}.",
                status=404)

        after: Optional[dict[str, Any]] = None
        after_problem = None
        try:
            state = _derive(wanted)
            frozen = doc.get("derived") or {}
            live = state["derived"]
            deltas: dict[str, Any] = {}
            for key in ("bills", "revenue_paise", "settled_count",
                        "settled_paise", "awaiting_count", "awaiting_paise",
                        "excluded_lines"):
                was = _int_or_none(frozen.get(key))
                is_now = _int_or_none(live.get(key))
                deltas[key] = (None if was is None or is_now is None
                               else is_now - was)
            moved = [k for k, v in deltas.items() if v not in (None, 0)]
            after = {
                "derived_now": live,
                "difference": deltas,
                "changed": bool(moved),
                "changed_fields": moved,
                "chain": state["chain"],
                "chain_warning": _chain_warning(state["chain"]),
                "note": (
                    "Nothing has been billed or settled for this day since it "
                    "was closed." if not moved else
                    "The audit chain now says something different about this "
                    "day than it did when the day was closed. The record above "
                    "has not been changed and will not be. This normally means "
                    "the shop kept trading after the shutter figures were "
                    "taken, or a payment settled late. A NEGATIVE difference "
                    "means the chain no longer verifies as far as it did, so "
                    "the live figures are the short ones."),
            }
        except DaybookRefused as exc:
            # The record is the point of this endpoint. If the chain cannot be
            # read at all, the frozen day is still served and the reason the
            # comparison is missing is named rather than left blank.
            after_problem = f"{exc.reason}: {exc.detail}"
        except Exception as exc:  # noqa: BLE001 - never a 500, never lose the record
            after_problem = f"{type(exc).__name__}: {exc}"

        # MILAN, BESIDE THE RECORD AND NEVER IN IT. The bills this day settled
        # reach the bank on the gateway's next cycle, so the report to read is
        # the one for the day after. It is asked for now, shown as a separate
        # block, and nothing from it is copied into `doc`: the frozen figures
        # are the shopkeeper's signed-off day, the report is the gateway's,
        # and a difference between them is the finding, not a correction.
        milan_block, milan_problem = _milan_beside(wanted)

        recomputed = _digest_of(doc)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "day": wanted,
            "record": doc,
            "milan": milan_block,
            "milan_unavailable": milan_problem,
            # The record's own digest, recomputed from the file as served. It
            # matching the stored one means nothing has edited the file since
            # the chain line was written; the chain line is the thing that makes
            # that check worth anything.
            "record_sha256_recomputed": recomputed,
            "record_unedited": recomputed == doc.get("record_sha256"),
            "after_close": after,
            "after_close_unavailable": after_problem,
            "note": CASH_NOTE,
        })
    except DaybookRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


__all__ = [
    "CLOSE_FORMAT",
    "DaybookRefused",
    "MAX_CLOSED_BY",
    "MAX_COUNTED_CASH_PAISE",
    "MAX_NOTE",
    "audit_path",
    "daybook_dir",
    "router",
    "shop_dir",
]
