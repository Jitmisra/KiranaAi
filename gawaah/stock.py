"""MAAL — stock that MOVES, built on top of the count the shopkeeper typed.

`gawaah/manage.py` already holds an opening count — the shopkeeper counts a
shelf, types a number, and the inventory screen shows that number minus what
the counter has billed since. It says so honestly and it stops there:

    remaining = units he counted  -  packets billed SINCE he counted

That figure is blind, by its own admission, to everything that moves stock
WITHOUT passing the counter. A delivery of forty packets does not appear. Two
packets dropped on the floor do not appear. A bottle taken home for the house
does not appear. So the number drifts, always downward-biased, until the
shopkeeper counts again and the drift is silently thrown away with no record of
where it went.

THIS MODULE IS THE MISSING MIDDLE. Three things happen to stock between two
counts, and each one gets a line in a hash-chained log:

    IN     a delivery arrived, a customer brought something back
    OUT    breakage, expiry, taken for the house, gone missing
    COUNT  the shopkeeper counted the shelf again — a new baseline

and the figure this module derives is:

    on hand = the last count
            + every movement recorded since that count
            - what the counter has billed since that count

THE THIRD TERM IS NOT COMPUTED HERE, AND THAT IS DELIBERATE
===========================================================
`billed_since_count` is read straight out of `manage.py`'s own derivation. This
module does not walk the audit chain looking for sold packets, does not decide
what "sold" means, and does not keep a sales counter. There is exactly one
answer in this program to "how many of these has the counter billed since the
count", it lives in manage.py, and both screens show the same number because
they are the same number. A second implementation that agreed today would
disagree the first time either changed, and there would be no way to tell which
screen was lying.

The same goes the other way: a re-count here writes through
`manage.write_opening_stock()` — the baseline the inventory screen already
reads. Counting a shelf on this screen moves the figure on that one. There is
one baseline, not two.

WHAT SUPERSEDES WHAT
====================
A count is the shopkeeper's own eyes, and it beats every derivation. So a count
SUPERSEDES the movements before it: they stay in the log, they are still
readable, and they stop counting towards the figure. A movement stamped at the
exact instant of the count is treated as part of what was counted (strictly
after, not at or after) — the one microsecond that costs is worth less than the
ambiguity of double-counting a delivery that was booked in as it was counted.

A movement is never edited and never deleted. A mistake is corrected with an
opposite movement carrying the reason `correction`, so the log reads as what
actually happened rather than as what somebody wished had happened.

UNITS ARE COUNTS, NEVER MONEY
=============================
Nothing in this file is money. It imports no rupee, holds no paise, and prints
no valuation of what is on the shelf — the one place money could plausibly
appear (stock on hand × price) is absent on purpose, because a valuation is an
arithmetic claim about money and this module has no business making one. What
units share with money is that they may not be fractional: half a packet is not
something a shelf holds, cannot be delivered, and cannot break. A fractional
movement is refused BY NAME rather than rounded, because rounding it would
store a number nobody typed.

DAYS OF COVER, AND WHEN IT REFUSES TO GUESS
===========================================
"Will this last the week?" is answered from the billing rate the audit chain
already records — units billed over the days observed, both integers, divided
with `//` and never with a float. When there is not enough history to divide by,
the answer is `null` and a sentence saying which of the three reasons it is
(nothing counted, nothing billed, or not enough days). It is never a confident
number derived from one afternoon's trade: a shopkeeper who reorders on a made
-up figure is worse off than one who reorders on his own memory.

THE ONLINE FLOOR
================
"Stop selling online below N." A shopkeeper keeps the last two packets of
Parle-G for the regular who walks in at nine; the storefront must not sell
them to a phone. The floor is a per-product integer, default 0, recorded here
as a `stock.online_floor` event and replayed exactly the way a reorder level
is. This module only RECORDS it: whether a product is sellable online is a
storefront question — it needs the open orders, which this module has no
business reading — and `gawaah/storefront.py` derives it as

    sellable online = on hand − units in open orders − floor, never below 0

so a floor of 0 means "sell down to the last packet" and a floor of 2 means
"stop at two". A product nobody has counted has NO figure, and the floor is
compared against nothing: the storefront sells it as before, because a floor
against an absent number is not a rule, it is a guess.

THE LOG IS THIS MODULE'S ONLY STORE
===================================
Movements, reorder levels and online floors are events in
`<shop>/stock.audit.jsonl`, a hash chain written by `gawaah/ledger.py` and
verifiable by the same `gawaah.ledger.verify()` that walks every other chain
here. There is no sidecar of derived state to fall out of step with it.

DELIBERATELY NOT `results/audit.jsonl` — the money service holds that file open
in another process and keeps the chain head in memory, so a second writer
between two of its appends breaks `make verify-ledger` on the one log that must
be beyond argument. See the same note in `storefront.py` and `offers.py`.

A REFUSAL IS A RESULT
=====================
Every failure below has a name in the body, a 400 (404 for a sku that is not in
the catalogue), and no 500s. And a movement that could not be appended to the
chain is a REFUSAL, not a warning: the chain is the store, so an unwritten
movement did not happen and must not be reported as though it had.

MOUNTING
========
The router carries NO prefix and the paths below are absolute::

    from gawaah import stock
    app.include_router(stock.router)        # -> /stock

Do not pass `prefix=`; the literal routes `/stock/low` and `/stock/movements`
are declared before `/stock/{sku_id}` and that order is what stops `low` being
read as the name of a product.
"""
from __future__ import annotations

import json
import math
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import manage
from .ledger import GENESIS, Ledger, verify

router = APIRouter()


# ---------------------------------------------------------------- refusals --
#
# Lowercase snake_case naming the STATE, matching manage.py and upload_app.py.
# The sentence saying what to do about it goes in `detail`, never in the reason.

R_BAD_BODY = "stock_body_not_json"
R_UNKNOWN_SKU = "sku_not_in_the_catalogue"

R_UNITS_MISSING = "stock_units_missing"
R_UNITS_FRACTIONAL = "stock_units_fractional"
R_UNITS_NOT_INTEGER = "stock_units_not_a_whole_number"
R_UNITS_NOT_POSITIVE = "stock_units_not_positive"
R_UNITS_NEGATIVE = "stock_units_negative"
R_UNITS_TOO_LARGE = "stock_units_implausible"

R_REASON_MISSING = "stock_reason_missing"
R_REASON_NOT_TEXT = "stock_reason_not_text"
R_REASON_UNKNOWN = "stock_reason_unknown"
R_REASON_WRONG_WAY = "stock_reason_is_the_wrong_direction"
R_REASON_MACHINE_ONLY = "stock_reason_written_by_the_counter_only"
R_BAD_REFUND_KEY = "refund_key_malformed"

R_NOTE_NOT_TEXT = "stock_note_not_text"
R_NOTE_TOO_LONG = "stock_note_too_long"

R_LEVEL_MISSING = "reorder_level_missing"
R_LEVEL_FRACTIONAL = "reorder_level_fractional"
R_LEVEL_NOT_INTEGER = "reorder_level_not_a_whole_number"
R_LEVEL_NEGATIVE = "reorder_level_negative"
R_LEVEL_TOO_LARGE = "reorder_level_implausible"

R_FLOOR_MISSING = "online_floor_missing"
R_FLOOR_FRACTIONAL = "online_floor_fractional"
R_FLOOR_NOT_INTEGER = "online_floor_not_a_whole_number"
R_FLOOR_NEGATIVE = "online_floor_negative"
R_FLOOR_TOO_LARGE = "online_floor_implausible"

R_BAD_LIMIT = "limit_not_a_positive_integer"
R_NOT_RECORDED = "stock_movement_not_recorded"
R_COUNT_NOT_WRITTEN = "stock_count_not_written"
R_NO_INVENTORY = "inventory_derivation_unavailable"
R_INTERNAL = "stock_internal_error"


# ------------------------------------------------------------------ limits --

#: The events this module writes. Named once so a reader of the log does not
#: have to guess which line means what, and so a rename cannot happen in one
#: place only.
EV_IN = "stock.in"
EV_OUT = "stock.out"
EV_COUNT = "stock.count"
EV_LEVEL = "stock.reorder_level"
EV_FLOOR = "stock.online_floor"

MOVEMENT_EVENTS = (EV_IN, EV_OUT)

#: Why stock arrives. A closed vocabulary, because a free-text reason cannot be
#: grouped, cannot be totalled, and cannot be asked a question later — "how much
#: did I lose to breakage this month" is unanswerable if half the lines say
#: "broke" and the other half say "dropped it". The free text goes in `note`,
#: which is exactly what a note is for.
IN_REASONS = {
    "delivery": "a delivery arrived",
    "customer_return": "a customer brought it back",
    "found": "found on the shelf and not in the last count",
    "correction": "correcting an earlier mistake",
    # WAAPSI. A packet whose money went back through the gateway. It is not
    # written into THIS module's chain: it is DERIVED from the kernel's own
    # `refund.processed` lines in results/audit.jsonl, the same way a SALE is
    # derived and subtracted (invariant 4 — one writer per chain, read
    # settlement and refunds through the money chain). A shopkeeper who took a
    # packet back WITHOUT a refund has `customer_return` for that; this one is
    # the gateway's word, not his, so he cannot type it — see MACHINE_IN_REASONS.
    "return": "returned by the customer and refunded through the gateway",
}

#: Reasons this module never accepts on its write endpoint. Posted by hand they
#: are refused BY NAME (`_reason`), because the figure they explain — a refund
#: the gateway confirmed — is one the shopkeeper cannot vouch for by typing it;
#: it is derived from the money chain, read-only.
RETURN_REASON = "return"
RETURN_LABEL = IN_REASONS["return"]
MACHINE_IN_REASONS = frozenset({RETURN_REASON})

#: Why stock leaves WITHOUT being billed. Selling is not on this list on
#: purpose: a sale leaves through the counter, the counter writes it to the
#: audit chain, and manage.py already subtracts it. Recording a sale here as
#: well would take the same packet off the shelf twice.
OUT_REASONS = {
    "breakage": "broken or spoiled",
    "expiry": "past its date",
    "personal_use": "taken for the house",
    "theft": "missing, believed taken",
    "returned_to_supplier": "sent back to the supplier",
    "sample": "given away",
    "correction": "correcting an earlier mistake",
}

#: One movement is a delivery or an accident, not a warehouse transfer. The cap
#: exists so a fat-fingered paste cannot put a number on the page that no human
#: entered meaning to. It is refused by name and never clamped: clamping stores
#: a figure the shopkeeper never typed and shows it back to him as his own.
MAX_MOVEMENT_UNITS = 100_000

def max_count_units() -> int:
    """The re-count cap, READ FROM manage.py rather than declared again here.

    The two ways of recording a count must not disagree about what is
    implausible: a count this endpoint accepted and manage's refused would be a
    baseline the inventory screen cannot reproduce.
    """
    return int(getattr(manage, "MAX_OPENING_STOCK_UNITS", 1_000_000))


#: A reorder level is a shelf, not a warehouse, and it is compared against the
#: same units a count is typed in.
MAX_REORDER_UNITS = 100_000

#: The online floor when nobody has set one: sell down to the last packet.
#: Zero and not None, because "no floor" and "a floor of nothing" are the same
#: rule and the storefront should not have to carry two spellings of it.
DEFAULT_ONLINE_FLOOR = 0

#: Same shelf, same cap as the reorder level, for the same reason.
MAX_ONLINE_FLOOR = 100_000

MAX_NOTE = 200

#: How far back the billing rate is measured. Long enough that a slow-moving
#: product still shows a rate, short enough that last season's Diwali trade does
#: not set today's reorder advice.
RATE_WINDOW_DAYS = 30

#: The floor under a rate. Below EITHER of these the answer is "not enough
#: history" and not a number.
#:
#: WHAT IT COSTS WHEN THESE ARE WRONG, both ways. Too low and the page prints
#: "2 days of cover" off a single busy afternoon, the shopkeeper orders on it,
#: and he learns to distrust the column. Too high and a genuinely fast-moving
#: product shows "not enough history" for a fortnight while it runs out. Three
#: packets over three days is the smallest observation this module is willing to
#: call a rate; anything less is a coincidence with a decimal point.
MIN_RATE_UNITS = 3
MIN_RATE_DAYS = 3

DEFAULT_MOVEMENT_LIMIT = 200
MAX_MOVEMENT_LIMIT = 2000

STOCK_AUDIT_FILENAME = "stock.audit.jsonl"

#: One process appends to this chain, and inside it one thread at a time. The
#: chain head is read from the file and written back, so two interleaved
#: appends would produce two lines claiming the same `prev_hash` and
#: `verify()` would fail on the second one forever after.
#:
#: A STATED LIMIT: this lock is per-process. Two till processes pointed at the
#: same shop directory can still interleave, exactly as they could on any other
#: chain here. The deployment this program describes runs one till.
_WRITE_LOCK = threading.Lock()


class StockRefused(Exception):
    """A named refusal with a sentence a shopkeeper can act on."""

    def __init__(self, reason: str, detail: str, status: int = 400) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.status = status


def _refusal(exc: StockRefused) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "reason": exc.reason,
            "detail": exc.detail,
            "settles_money": False,
        },
        status_code=exc.status,
    )


def _crash(exc: BaseException) -> JSONResponse:
    """The last resort. A 500 teaches the reader nothing, so there are none.

    The exception TYPE is named and the message passed through: on a stock
    screen the message is usually the whole diagnosis ("No space left on
    device").
    """
    return JSONResponse(
        {
            "ok": False,
            "reason": R_INTERNAL,
            "detail": f"{type(exc).__name__}: {exc}",
            "settles_money": False,
        },
        status_code=400,
    )


# ------------------------------------------------------------ where things are
#
# Resolved per call, never memoised at import, for the reason manage.py records:
# a test that sets GAWAAH_SHOP_DIR in a fixture must be able to change it
# between tests, and a module-level constant captured at import time silently
# ignores that — which is how a harness once wrote over the live catalogue.


def shop_dir() -> Path:
    """The shopkeeper's catalogue directory — MANAGE'S ANSWER, not a second one.

    Deliberately `manage.store_dir()` rather than reading GAWAAH_SHOP_DIR here.
    The baseline count and the billed-since figure both come out of manage.py,
    so a directory this module resolved differently would put the movements in
    one shop and the count they are added to in another, and the resulting
    figure would be wrong in a way nothing on the page could show.
    """
    return Path(manage.store_dir())


def audit_path() -> Path:
    """This module's own hash chain. See the module docstring on why it is not
    `results/audit.jsonl`."""
    return shop_dir() / STOCK_AUDIT_FILENAME


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _parse_ts(value: Any) -> Optional[datetime]:
    """An ISO-8601 stamp as this program writes them, or None.

    Local rather than manage.py's `_parse_ts`, which additionally repairs a '+'
    that a URL query string turned into a space. Nothing here takes a timestamp
    from a query string: every stamp compared below was written by this module
    or by `manage.write_opening_stock`, both with `datetime.isoformat()`. A
    naive stamp — which only a hand-edited file can produce — is read as UTC,
    the same assumption manage.py makes, so the two cannot disagree about the
    order of a count and a movement.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# ------------------------------------------------------------------- the chain


def read_events() -> tuple[tuple[dict, ...], dict]:
    """Every verified line of this module's log, plus the state of the chain.

    Truncated at the first broken link, exactly as manage.py truncates the money
    chain and for the same reason: a line whose hash does not recompute is not
    evidence of anything. The break is reported in a `chain` block on every
    response rather than raised, so a shopkeeper whose log was edited this
    morning can still see the movements from before the edit and is told, in
    the same breath, that something is wrong with the file.

    NOT CACHED, unlike manage.read_chain(). That cache exists because the money
    chain is thousands of lines and a settings page polls it; a kirana records
    perhaps a dozen movements a week and a cache here would be a stale-read bug
    waiting for a slow clock.
    """
    path = audit_path()
    ok, verified, head, error = verify(path)
    records: list[dict] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                break
            if not isinstance(rec, dict):
                break
            records.append(rec)
    if not ok:
        # Parsing and verifying are two different bars, and only the second one
        # counts towards a figure on the page.
        records = records[:verified]
    return tuple(records), {
        "ok": bool(ok),
        "exists": path.exists(),
        "lines_verified": int(verified),
        "lines_readable": len(records),
        "head": head if path.exists() else GENESIS,
        "error": error,
        "path": str(path),
    }


def _movement_of(rec: dict) -> Optional[dict]:
    """One movement out of one chain line, or None if the line is not one.

    A line whose direction and sign disagree — `stock.in` with -5 units — is
    dropped rather than believed. It cannot be written by this module, so if one
    is there the file has been hand-edited, and guessing which half of a
    self-contradicting line to trust is how a figure becomes fiction.
    """
    if rec.get("event") not in MOVEMENT_EVENTS:
        return None
    sku_id = rec.get("sku_id")
    units = rec.get("units")
    if not isinstance(sku_id, str) or not sku_id:
        return None
    if isinstance(units, bool) or not isinstance(units, int) or units == 0:
        return None
    if rec["event"] == EV_IN and units < 0:
        return None
    if rec["event"] == EV_OUT and units > 0:
        return None
    reason = rec.get("reason")
    note = rec.get("note")
    return {
        "movement_id": rec.get("movement_id"),
        "at": rec.get("ts"),
        "sku_id": sku_id,
        "kind": "in" if rec["event"] == EV_IN else "out",
        "units": int(units),
        "reason": reason if isinstance(reason, str) else None,
        "reason_label": _reason_label(rec["event"], reason),
        "note": note if isinstance(note, str) and note else None,
        "hash": rec.get("hash"),
    }


def _reason_label(event: str, reason: Any) -> Optional[str]:
    table = IN_REASONS if event == EV_IN else OUT_REASONS
    if isinstance(reason, str):
        return table.get(reason)
    return None


def returns_by_sku() -> tuple[dict[str, list[dict[str, Any]]], int]:
    """{sku -> derived return movements, oldest first} and how many refund
    rows named no sku this module could attribute.

    WAAPSI. One derived IN of one unit per refund the kernel marked PROCESSED —
    a packet that came back and was paid for going back. Folded from
    `results/audit.jsonl` through `receipts.refunds_from` so there is ONE fold
    of the money chain's refund lines in this program, shared with the receipt
    and the loyalty clawback. Read-only: nothing here writes a movement, and a
    refund that is merely requested (not PROCESSED) puts nothing on the shelf,
    because the packet's money has not actually gone back yet.
    """
    from . import receipts  # noqa: WPS433 - late; it pulls in manage

    records, _chain = manage.read_chain()
    by_session = receipts.refunds_from(records)
    out: dict[str, list[dict[str, Any]]] = {}
    unattributed = 0
    for session_id, by_key in by_session.items():
        for refund_key, rf in by_key.items():
            if not rf.get("refunded"):
                continue
            sku = rf.get("sku_id")
            if not isinstance(sku, str) or not sku:
                unattributed += 1
                continue
            out.setdefault(sku, []).append({
                "movement_id": refund_key,
                "at": rf.get("processed_at") or rf.get("last_at"),
                "sku_id": sku,
                "kind": "in",
                "units": 1,
                "reason": RETURN_REASON,
                "reason_label": RETURN_LABEL,
                "note": None,
                "refund_key": refund_key,
                "session_id": session_id,
                "derived": True,
            })
    for rows in out.values():
        rows.sort(key=lambda m: (str(m.get("at") or ""), m["refund_key"]))
    return out, unattributed


def movements_by_sku(events: tuple[dict, ...]) -> tuple[dict[str, list[dict]], int]:
    """{sku -> movements, oldest first} and how many lines were not readable.

    The unreadable count is returned rather than swallowed: a log with lines
    this module cannot read is a log whose figures are short by an unknown
    amount, and a page that showed the figure without the count would be
    reporting a number it knew to be incomplete.
    """
    out: dict[str, list[dict]] = {}
    skipped = 0
    for rec in events:
        if rec.get("event") not in MOVEMENT_EVENTS:
            continue
        mv = _movement_of(rec)
        if mv is None:
            skipped += 1
            continue
        out.setdefault(mv["sku_id"], []).append(mv)
    return out, skipped


def reorder_levels(events: tuple[dict, ...]) -> dict[str, dict[str, Any]]:
    """{sku -> level, set_at}. Last write wins; a cleared level disappears.

    Replayed from the chain rather than kept in a sidecar so this module has one
    store and not two. WHAT IT COSTS WHEN THAT IS WRONG: a chain break older
    than a level change reverts the level to what it was before the break. The
    break is on the page, in the `chain` block on every response, which is the
    difference between a figure that is wrong and a figure that is wrong
    silently.
    """
    out: dict[str, dict[str, Any]] = {}
    for rec in events:
        if rec.get("event") != EV_LEVEL:
            continue
        sku_id = rec.get("sku_id")
        if not isinstance(sku_id, str) or not sku_id:
            continue
        if rec.get("cleared") is True:
            out.pop(sku_id, None)
            continue
        level = rec.get("level_units")
        if isinstance(level, bool) or not isinstance(level, int) or level < 0:
            continue
        out[sku_id] = {"level": int(level), "set_at": rec.get("ts")}
    return out


def online_floors(events: tuple[dict, ...]) -> dict[str, dict[str, Any]]:
    """{sku -> floor, set_at}. Last write wins.

    Replayed from the chain, like `reorder_levels`, and with the same stated
    cost: a chain break older than a floor change reverts the floor to what it
    was before the break. A floor of zero is written as a line rather than
    dropped, so the log says "back to selling the last packet" instead of going
    quiet about it. A product with no line here has the DEFAULT floor, which
    the storefront reads as `DEFAULT_ONLINE_FLOOR` — this function does not fill
    it in, so a reader can tell "never set" from "set to nothing".
    """
    out: dict[str, dict[str, Any]] = {}
    for rec in events:
        if rec.get("event") != EV_FLOOR:
            continue
        sku_id = rec.get("sku_id")
        if not isinstance(sku_id, str) or not sku_id:
            continue
        floor = rec.get("floor_units")
        if isinstance(floor, bool) or not isinstance(floor, int) or floor < 0:
            continue
        out[sku_id] = {"floor": int(floor), "set_at": rec.get("ts")}
    return out


# ------------------------------------------------- manage.py's own derivation


def _inventory() -> dict[str, Any]:
    """manage.py's inventory rows: the catalogue, the count, and billed-since.

    `inventory_rows` first so that the day the orchestrator promotes manage's
    private helper to a public name, this module uses the public one without an
    edit. If NEITHER exists — manage.py renamed or refactored — that is a named
    refusal and not an AttributeError, because "the screen is empty" and "the
    module it reads from moved" must not look the same to a shopkeeper.
    """
    fn = getattr(manage, "inventory_rows", None) or getattr(
        manage, "_inventory_rows", None)
    if fn is None:
        raise StockRefused(
            R_NO_INVENTORY,
            "gawaah/manage.py no longer exposes the inventory derivation this "
            "screen reads its counts and its billed-since figures from. Stock "
            "movements are still recorded in the log; the derived figures are "
            "not shown, because this module will not compute a second answer "
            "to a question manage.py owns.")
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - a broken read is a named answer
        raise StockRefused(
            R_NO_INVENTORY,
            f"the inventory derivation in gawaah/manage.py could not be read "
            f"({type(exc).__name__}: {exc}). No stock figure is shown rather "
            f"than one derived from half of it.") from None


def _catalogue_skus() -> dict[str, dict[str, Any]]:
    """Everything the shop can price, through manage.py's reader.

    That reader parses the sidecars as JSON rather than through ShopStore, so a
    hand-edited catalogue is reported as a problem instead of raising — which is
    exactly what a management screen needs. Nothing here writes a SKU, so no
    write-side validation is being skipped.
    """
    return dict(manage.catalogue().get("items") or {})


def billing_rates(now: datetime) -> dict[str, dict[str, Any]]:
    """Units billed per sku inside the rate window, and over how many days.

    Folded out of `manage.bills_from()` — the same derivation the history and
    inventory screens use — so "billed" means one thing in this program. What is
    added here is only the SPAN: manage reports how many were billed, and a rate
    needs to know across how long, which manage has no reason to publish.

    The rate is never expressed as a float. It is a pair of integers, units over
    days, and the division that turns it into days-of-cover happens once, with
    `//`, in `_cover()`.
    """
    records, _chain = manage.read_chain()
    bills = manage.bills_from(records)
    window_start = now - timedelta(days=RATE_WINDOW_DAYS)
    out: dict[str, dict[str, Any]] = {}
    for bill in bills.values():
        if not bill.get("closed"):
            continue
        for line in bill.get("line_items") or []:
            at = _parse_ts(line.get("at") or bill.get("at"))
            if at is None or at < window_start or at > now:
                continue
            sku_id = line.get("sku_id")
            if not isinstance(sku_id, str) or not sku_id:
                continue
            row = out.setdefault(
                sku_id, {"units": 0, "first_at": None, "last_at": None})
            row["units"] += 1
            first = _parse_ts(row["first_at"])
            last = _parse_ts(row["last_at"])
            if first is None or at < first:
                row["first_at"] = line.get("at") or bill.get("at")
            if last is None or at > last:
                row["last_at"] = line.get("at") or bill.get("at")
    return out


# ------------------------------------------------------------- days of cover


def _cover(on_hand: Optional[int], rate: Optional[dict[str, Any]],
           now: datetime) -> dict[str, Any]:
    """How many days the shelf lasts at the rate the chain recorded — or why not.

    Every branch that cannot honestly divide returns `days: None` WITH the
    sentence saying which branch it was. A null with no explanation is the thing
    that makes a shopkeeper distrust the whole column, and a number with no
    history behind it is worse than the null.
    """
    blank = {
        "days": None,
        "units_billed": 0 if rate is None else int(rate["units"]),
        "over_days": None,
        "window_days": RATE_WINDOW_DAYS,
        "rate_text": None,
    }
    if on_hand is None:
        return {**blank, "why": (
            "Nothing has been counted for this product, so there is no figure "
            "to divide. Count the shelf once and this fills in.")}
    if rate is None or int(rate["units"]) <= 0:
        return {**blank, "why": (
            f"The counter has billed none of this in the last "
            f"{RATE_WINDOW_DAYS} days, so there is no rate to divide by. That "
            f"is not the same as saying it will last forever.")}

    units = int(rate["units"])
    first = _parse_ts(rate.get("first_at"))
    if first is None:
        return {**blank, "why": (
            "The bills for this product carry no readable timestamp, so the "
            "days they cover cannot be measured.")}
    # First sale in the window to now, floored to whole days and never zero. A
    # denominator of nought is not a rate and a denominator of "part of today"
    # is a rate off one afternoon.
    over_days = (now - first).days
    if over_days < 1:
        over_days = 1
    known = {**blank, "over_days": over_days, "units_billed": units,
             "rate_text": f"{units} billed in {over_days} days"}

    if units < MIN_RATE_UNITS:
        return {**known, "why": (
            f"Only {units} of these have been billed in the last "
            f"{RATE_WINDOW_DAYS} days. That is too little to call a rate — "
            f"{MIN_RATE_UNITS} is the least this counter will divide by.")}
    if over_days < MIN_RATE_DAYS:
        return {**known, "why": (
            f"All {units} were billed inside {over_days} day(s). A rate from "
            f"one day's trade is a guess with a decimal point; this fills in "
            f"after {MIN_RATE_DAYS} days of history.")}
    if on_hand < 0:
        return {**known, "why": (
            f"The derived figure is {on_hand}, which is below zero and "
            f"therefore wrong: something has left the shelf that nobody "
            f"recorded. Count it again and the cover figure returns.")}
    if on_hand == 0:
        return {**known, "days": 0, "why": (
            "There is nothing on the shelf on this counter's figures.")}

    # THE ONLY DIVISION IN THIS FILE, and it is integer division on counts. It
    # floors, which under-states the cover — the safe direction to be wrong in
    # when the answer is used to decide whether to reorder.
    days = (on_hand * over_days) // units
    return {**known, "days": int(days), "why": (
        f"{on_hand} on hand, and {units} were billed over {over_days} days. "
        f"Rounded down, and blind to anything that leaves the shelf without "
        f"being billed.")}


# ---------------------------------------------------------------- the figures


def stock_rows() -> dict[str, Any]:
    """Every catalogue product with its baseline, its movements and its figure.

    One assembly function behind all three read endpoints, so the list, the
    single-product view and the low-stock report cannot drift apart.
    """
    inv = _inventory()
    events, chain = read_events()
    moves, unreadable = movements_by_sku(events)
    levels = reorder_levels(events)
    floors = online_floors(events)
    now = _now()
    rates = billing_rates(now)
    # WAAPSI. Derived IN "return" movements, off the money chain, one per
    # refund the kernel marked PROCESSED. Read-only; see `returns_by_sku`.
    returns, returns_unattributed = returns_by_sku()

    rows: list[dict[str, Any]] = []
    for item in inv.get("items") or []:
        sku_id = item.get("sku_id")
        if not isinstance(sku_id, str) or not sku_id:
            continue
        rows.append(_row(item, moves.get(sku_id, []), levels.get(sku_id),
                         rates.get(sku_id), now, floors.get(sku_id),
                         returns.get(sku_id, [])))

    # A movement can outlive its product: a SKU is deleted from the catalogue
    # and the deliveries booked against it are still in the log. Hiding them
    # would make the movement list not add up to the movements on the products,
    # and a shopkeeper chasing that difference has nowhere to look.
    in_catalogue = {r["sku_id"] for r in rows}
    orphans = sorted((set(moves) | set(levels)) - in_catalogue)
    orphan_rows = [
        {"sku_id": sku_id,
         "name": None,
         "in_catalogue": False,
         "movements": len(moves.get(sku_id, [])),
         "units_in": sum(m["units"] for m in moves.get(sku_id, []) if m["units"] > 0),
         "units_out": -sum(m["units"] for m in moves.get(sku_id, []) if m["units"] < 0),
         "reorder_level": (levels.get(sku_id) or {}).get("level")}
        for sku_id in orphans
    ]

    return {
        "items": rows,
        "moved_but_not_in_catalogue": orphan_rows,
        "chain": chain,
        "bill_chain": inv.get("chain"),
        "unreadable_movement_lines": unreadable,
        "returns_unattributed": returns_unattributed,
        "store_dir": str(shop_dir()),
        "now": now.isoformat(),
    }


def _row(item: dict[str, Any], movements: list[dict[str, Any]],
         level: Optional[dict[str, Any]], rate: Optional[dict[str, Any]],
         now: datetime, floor: Optional[dict[str, Any]] = None,
         returns: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """One product: the count, what moved since it, and what that leaves.

    `remaining_units` is manage.py's own subtraction (count minus billed since
    the count) and is used AS IT STANDS. This function adds the movements to it
    and nothing else — which is the whole of what this module contributes to
    the figure, and is why the two screens can never disagree by more than the
    movements they are both showing.

    WAAPSI. `returns` are DERIVED IN movements off the money chain (one per
    refund the kernel marked PROCESSED). They are treated exactly like a
    recorded IN — windowed by the count and added to the figure — because a
    refunded return is a packet back on the shelf, and derived is the honest
    source for it (invariant 4). They are counted separately in the response so
    a shopkeeper can see the two ins apart.
    """
    sku_id = str(item.get("sku_id"))
    counted = item.get("opening_stock_units")
    counted_at_raw = item.get("opening_stock_counted_at")
    counted_at = _parse_ts(counted_at_raw)
    remaining = item.get("remaining_units")

    since: list[dict[str, Any]] = []
    superseded = 0
    for mv in movements:
        at = _parse_ts(mv.get("at"))
        # No baseline, or a baseline with no timestamp: every movement counts.
        # manage.py treats a count with no timestamp the same way for bills, so
        # the two halves of the sum cover the same window.
        if counted_at is None or (at is not None and at > counted_at):
            since.append(mv)
        else:
            superseded += 1

    returns = returns or []
    returns_since: list[dict[str, Any]] = []
    for mv in returns:
        at = _parse_ts(mv.get("at"))
        if counted_at is None or (at is not None and at > counted_at):
            returns_since.append(mv)

    manual_in = sum(m["units"] for m in since if m["units"] > 0)
    returned_since = sum(int(m["units"]) for m in returns_since)
    units_in = manual_in + returned_since
    units_out = -sum(m["units"] for m in since if m["units"] < 0)
    delta = units_in - units_out

    on_hand: Optional[int] = None
    basis = "never_counted"
    if isinstance(counted, int) and not isinstance(counted, bool) \
            and isinstance(remaining, int) and not isinstance(remaining, bool):
        on_hand = int(remaining) + delta
        basis = "counted"

    cover = _cover(on_hand, rate, now)
    lvl = None if level is None else int(level["level"])
    at_or_under = (lvl is not None and on_hand is not None and on_hand <= lvl)

    return {
        "sku_id": sku_id,
        "name": item.get("name"),
        "in_catalogue": True,
        "taught_label": item.get("taught_label"),
        # --- the baseline, straight from manage.py
        "counted_units": counted if isinstance(counted, int) else None,
        "counted_at": counted_at_raw,
        "billed_since_count": item.get("billed_since_count"),
        "remaining_after_billing": remaining,
        # --- what this module adds
        "units_in_since_count": units_in,
        "units_out_since_count": units_out,
        "movement_delta_units": delta,
        "movements_since_count": len(since),
        "movements_superseded_by_count": superseded,
        "last_movement_at": since[-1]["at"] if since else (
            movements[-1]["at"] if movements else None),
        # --- WAAPSI: the derived returns, apart from the manual ins
        "returned_count": len(returns),
        "returned_since_count": returned_since,
        "manual_in_since_count": manual_in,
        "returns": returns,
        "last_return_at": returns[-1]["at"] if returns else None,
        # --- the figure, and how it was reached
        "on_hand_units": on_hand,
        "basis": basis,
        "needs_recount": bool(on_hand is not None and on_hand < 0),
        "derivation": (
            f"{counted} counted at {counted_at_raw}"
            f" + {manual_in} in + {returned_since} returned - {units_out} out"
            f" - {item.get('billed_since_count')} billed"
            f" = {on_hand}"
            if basis == "counted" else
            "Nothing has ever been counted for this product, so there is no "
            "figure. A zero here would be a claim; this is an absence."),
        # --- reordering
        "reorder_level": lvl,
        "reorder_level_set_at": None if level is None else level.get("set_at"),
        "at_or_under_reorder_level": at_or_under,
        "days_of_cover": cover["days"],
        "cover": cover,
        # --- the storefront's floor. Recorded here, applied in storefront.py.
        "online_floor": DEFAULT_ONLINE_FLOOR if floor is None else int(floor["floor"]),
        "online_floor_set_at": None if floor is None else floor.get("set_at"),
    }


# --------------------------------------------------------------- reading input


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a named refusal
        raise StockRefused(
            R_BAD_BODY,
            'the body of this request is not JSON. It should look like '
            '{"units": 12, "reason": "delivery"}.') from None
    if not isinstance(body, dict):
        raise StockRefused(
            R_BAD_BODY,
            f'the body of this request is a {type(body).__name__}; it must be a '
            f'JSON object like {{"units": 12, "reason": "delivery"}}.')
    return body


def _whole_units(body: dict[str, Any], key: str, *,
                 missing: str, fractional: str, not_integer: str) -> int:
    """A whole number of packets, or a refusal that names which kind of wrong.

    THE FRACTIONAL CASE IS SEPARATED FROM THE REST ON PURPOSE. "2.5 is not a
    whole number" and "'2.5' is not a number at all" are different mistakes with
    different fixes, and a shopkeeper reading one message on a phone at a
    counter should not have to work out which of the two he made. 2.0 is a third
    case: the value is whole but it arrived as a decimal, which means something
    upstream is doing arithmetic on packets in floating point, and that is worth
    saying out loud rather than quietly accepting.
    """
    if key not in body:
        raise StockRefused(
            missing, f'no "{key}" in the body. Send {{"{key}": 12}}.')
    raw = body[key]
    if isinstance(raw, float):
        # JSON has no infinity but Python's parser accepts `Infinity` and `NaN`,
        # and int() of either raises. Named here rather than falling through to
        # the generic handler, which would answer a typed number with an
        # OverflowError.
        if not math.isfinite(raw):
            raise StockRefused(
                not_integer,
                f"{key}={raw!r} is not a number of packets at all. Nothing was "
                f"recorded.")
        if raw != int(raw):
            raise StockRefused(
                fractional,
                f"{key}={raw!r} is a fraction of a packet. Half a packet is not "
                f"something a shelf holds, a supplier delivers or a customer "
                f"drops. Nothing was recorded.")
        raise StockRefused(
            not_integer,
            f"{key}={raw!r} arrived as a decimal. Packets are counted, not "
            f"measured — send {int(raw)}. Nothing was recorded.")
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise StockRefused(
            not_integer,
            f"{key}={raw!r} is not a whole number of packets. Nothing was "
            f"recorded.")
    return int(raw)


def _movement_units(body: dict[str, Any], direction: str) -> int:
    """The MAGNITUDE of a movement. The sign belongs to the route, not the page.

    INVARIANT 8, applied to stock: the browser sends intent — "five of these
    arrived" — and the server derives what that means for the figure. A page
    that could post a negative number to `/in` would be authoring the direction
    of a movement, and the first typo would be a delivery that emptied a shelf.
    """
    units = _whole_units(
        body, "units",
        missing=R_UNITS_MISSING,
        fractional=R_UNITS_FRACTIONAL,
        not_integer=R_UNITS_NOT_INTEGER)
    if units <= 0:
        other = "out" if direction == "in" else "in"
        raise StockRefused(
            R_UNITS_NOT_POSITIVE,
            f"units={units} on a stock-{direction} movement. Send how many "
            f"packets moved, as a positive number; to record stock going the "
            f"other way use the stock-{other} endpoint. Nothing was recorded.")
    if units > MAX_MOVEMENT_UNITS:
        raise StockRefused(
            R_UNITS_TOO_LARGE,
            f"units={units} is over {MAX_MOVEMENT_UNITS} in one movement. "
            f"Nothing is recorded: a number nobody typed on purpose must not "
            f"appear on the page as though somebody had.")
    return units


def _reason(body: dict[str, Any], direction: str) -> str:
    """One of the reasons this module knows, in the direction it belongs to."""
    table = IN_REASONS if direction == "in" else OUT_REASONS
    other = OUT_REASONS if direction == "in" else IN_REASONS
    raw = body.get("reason")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise StockRefused(
            R_REASON_MISSING,
            f'no "reason" in the body. Stock that moves without a reason is a '
            f'figure nobody can explain later. This counter knows: '
            f'{", ".join(sorted(table))}.')
    if not isinstance(raw, str):
        raise StockRefused(
            R_REASON_NOT_TEXT,
            f"reason={raw!r} is a {type(raw).__name__}. It must be one of: "
            f"{', '.join(sorted(table))}.")
    reason = raw.strip()
    if direction == "in" and reason in MACHINE_IN_REASONS:
        raise StockRefused(
            R_REASON_MACHINE_ONLY,
            f"{reason!r} is written by the counter when the gateway confirms a "
            f"refund, and by nothing else. A packet brought back without a "
            f"refund is 'customer_return'. Nothing was recorded.")
    if reason in table:
        return reason
    if reason in other:
        # NAMED SEPARATELY because it is a different mistake. 'breakage' posted
        # to the stock-in endpoint is a shopkeeper who found the right word and
        # the wrong button, and telling him the word is unknown would send him
        # looking for a different word.
        the_other_way = "out" if direction == "in" else "in"
        raise StockRefused(
            R_REASON_WRONG_WAY,
            f"{reason!r} is a reason stock goes {the_other_way}, not "
            f"{direction}. Post it to the stock-{the_other_way} endpoint for "
            f"this product. Nothing was recorded.")
    raise StockRefused(
        R_REASON_UNKNOWN,
        f"{reason!r} is not a reason this counter records. It knows: "
        f"{', '.join(sorted(table))}. Anything else belongs in the note, which "
        f"is free text.")


def _note(body: dict[str, Any]) -> Optional[str]:
    raw = body.get("note")
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise StockRefused(
            R_NOTE_NOT_TEXT,
            f"note={raw!r} is a {type(raw).__name__}. A note is free text, or "
            f"leave it out.")
    note = " ".join(raw.split())
    if not note:
        return None
    if len(note) > MAX_NOTE:
        raise StockRefused(
            R_NOTE_TOO_LONG,
            f"the note is {len(note)} characters and the cap is {MAX_NOTE}. "
            f"Nothing was recorded.")
    return note


def _known_sku(sku_id: str) -> dict[str, Any]:
    known = _catalogue_skus()
    rec = known.get(sku_id)
    if rec is None:
        raise StockRefused(
            R_UNKNOWN_SKU,
            f"{sku_id!r} is not in the catalogue at {shop_dir()}. Teach the "
            f"product first: a movement against a product that does not exist "
            f"is a number with nothing to attach it to.",
            status=404)
    return rec


def _limit(raw: Any) -> int:
    if raw is None or raw == "":
        return DEFAULT_MOVEMENT_LIMIT
    try:
        want = int(str(raw))
    except (TypeError, ValueError):
        raise StockRefused(
            R_BAD_LIMIT,
            f"limit={raw!r} is not a whole number. Leave it out for "
            f"{DEFAULT_MOVEMENT_LIMIT}.") from None
    if want < 1:
        raise StockRefused(
            R_BAD_LIMIT,
            f"limit={want} asks for no movements at all; the smallest useful "
            f"limit is 1.")
    if want > MAX_MOVEMENT_LIMIT:
        raise StockRefused(
            R_BAD_LIMIT,
            f"limit={want} is over the ceiling of {MAX_MOVEMENT_LIMIT}. Ask in "
            f"pages, or read {audit_path().name} directly.")
    return want


# ----------------------------------------------------------------- the writes


def _append(event: str, ts: str, **fields: Any) -> str:
    """One line on the chain, or a refusal. NEVER best-effort.

    The chain IS this module's store. `storefront.py` and `offers.py` can treat
    a failed audit as a warning because their state is in a JSON file that was
    already written; here an unappended movement did not happen, and reporting
    it as recorded would put a figure on the page that no file behind it
    supports.

    THE CALLER SUPPLIES THE TIMESTAMP, and it is the one the caller then reports
    back. Stamping it here would mean the time in the response and the time on
    the log differ by however long the write took — small, invisible, and enough
    to make a movement recorded in the same instant as a count fall on the wrong
    side of it.
    """
    try:
        with _WRITE_LOCK:
            return Ledger(audit_path()).append(
                ts=ts, module="stock", event=event, **fields)
    except Exception as exc:  # noqa: BLE001 - an unwritten movement is a refusal
        raise StockRefused(
            R_NOT_RECORDED,
            f"the movement could not be appended to {audit_path()} "
            f"({type(exc).__name__}: {exc}). NOTHING WAS RECORDED and no figure "
            f"has changed, which is better than a page showing a movement that "
            f"is not in the log.") from None


def _row_for(sku_id: str) -> Optional[dict[str, Any]]:
    """The derived row for one product, or None if the derivation is unavailable.

    Used by the write endpoints to answer with the figure the movement produced.
    A write must not fail because the READ side could not assemble a figure, so
    this swallows a refusal that `stock_rows()` would raise — the movement is on
    the chain either way, and the response says the figure is absent.
    """
    try:
        for row in stock_rows()["items"]:
            if row["sku_id"] == sku_id:
                return row
    except Exception:  # noqa: BLE001 - a missing figure must not lose a write
        return None
    return None


# ---------------------------------------------------------------- the routes
#
# ORDER MATTERS AND IS LOAD-BEARING. FastAPI matches in declaration order, so
# `/stock/low` and `/stock/movements` are declared BEFORE `/stock/{sku_id}`.
# Reversed, the low-stock report becomes a 404 for a product called 'low', and
# the only symptom is a screen that is empty for a reason nobody can see.


@router.get("/stock")
def stock_ep() -> JSONResponse:
    """Every product: what was counted, what has moved, what is on the shelf."""
    try:
        payload = stock_rows()
        rows = payload["items"]
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "count": len(rows),
            "counted_skus": sum(
                1 for r in rows if r["on_hand_units"] is not None),
            "at_or_under_level": sum(
                1 for r in rows if r["at_or_under_reorder_level"]),
            "needs_recount": sum(1 for r in rows if r["needs_recount"]),
            "reasons": {"in": IN_REASONS, "out": OUT_REASONS},
            "note": (
                "On hand is your own count, plus the movements recorded since "
                "it, minus what this counter has billed since it. It cannot "
                "see anything that left the shop without either being billed "
                "or being written down here."),
            **payload,
        })
    except StockRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/stock/low")
def stock_low_ep() -> JSONResponse:
    """What is at or under the level the shopkeeper set, worst first.

    Three lists, because collapsing them would hide the honest half:

      - `low`      at or under the level, with days of cover where there is
                   enough history to derive one
      - `unknown`  a level is set but the shelf has never been counted, so
                   whether it is low cannot be said
      - `negative` the derived figure is below zero, which means stock has left
                   without being recorded and the baseline needs re-counting
    """
    try:
        payload = stock_rows()
        rows = payload["items"]

        low = [r for r in rows if r["at_or_under_reorder_level"]]
        # Worst first: the biggest shortfall against the level, then by name so
        # the order does not shuffle between two identical requests.
        low.sort(key=lambda r: (-(r["reorder_level"] - r["on_hand_units"]),
                                r["sku_id"]))
        unknown = [
            {"sku_id": r["sku_id"], "name": r["name"],
             "reorder_level": r["reorder_level"],
             "why": ("A reorder level is set but this shelf has never been "
                     "counted, so there is nothing to compare it against.")}
            for r in rows
            if r["reorder_level"] is not None and r["on_hand_units"] is None
        ]
        negative = [r for r in rows if r["needs_recount"]]

        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "count": len(low),
            "low": low,
            "unknown": unknown,
            "needs_recount": negative,
            "skus_with_a_level": sum(
                1 for r in rows if r["reorder_level"] is not None),
            "skus_without_a_level": sum(
                1 for r in rows if r["reorder_level"] is None),
            "chain": payload["chain"],
            "bill_chain": payload["bill_chain"],
            "now": payload["now"],
            "note": (
                "Days of cover come from what this counter has billed, so they "
                "do not allow for breakage or for anything else that leaves "
                "the shelf unbilled. Where there is not enough history to "
                "derive a rate, the figure is absent and says why."),
        })
    except StockRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/stock/movements")
def stock_movements_ep(sku: str | None = None,
                       limit: str | None = None) -> JSONResponse:
    """The movement log, newest first. `?sku=` narrows it, `?limit=` shortens it.

    Read from the chain, so what is listed here is exactly what is on disk and
    verifiable. A line the chain could not stand behind is not listed at all,
    and the count of those is in `chain`.
    """
    try:
        want = _limit(limit)
        events, chain = read_events()
        moves, unreadable = movements_by_sku(events)
        if sku is not None:
            rows = list(moves.get(sku, []))
        else:
            rows = [m for lst in moves.values() for m in lst]
        # Grouping by sku loses the chain's own order, so it is restored here by
        # sorting the ISO stamps AS STRINGS: every stamp this module writes is
        # UTC from `datetime.isoformat()`, and lexical order on those is
        # chronological order. Not parsed, and not re-sorted afterwards, so two
        # movements sharing a microsecond keep a stable order instead of
        # swapping between two identical requests.
        rows.sort(key=lambda m: str(m.get("at") or ""))
        rows.reverse()
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "count": min(want, len(rows)),
            "matched": len(rows),
            "limit": want,
            "sku": sku,
            "movements": rows[:want],
            "unreadable_movement_lines": unreadable,
            "chain": chain,
        })
    except StockRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/stock/{sku_id}")
def stock_one_ep(sku_id: str) -> JSONResponse:
    """One product in full, with the movements behind its figure."""
    try:
        _known_sku(sku_id)
        payload = stock_rows()
        row = next((r for r in payload["items"] if r["sku_id"] == sku_id), None)
        if row is None:
            raise StockRefused(
                R_UNKNOWN_SKU,
                f"{sku_id!r} is in the catalogue but produced no stock row. "
                f"Nothing was changed.", status=404)
        events, _chain = read_events()
        moves, _skipped = movements_by_sku(events)
        history = list(moves.get(sku_id, []))
        history.reverse()
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            **row,
            "movements": history,
            "reasons": {"in": IN_REASONS, "out": OUT_REASONS},
            "chain": payload["chain"],
            "bill_chain": payload["bill_chain"],
            "now": payload["now"],
        })
    except StockRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/stock/{sku_id}/in")
async def stock_in_ep(sku_id: str, request: Request) -> JSONResponse:
    """Stock arrived. Body: {"units": 24, "reason": "delivery", "note": "..."}"""
    return await _movement(sku_id, request, "in")


@router.post("/stock/{sku_id}/out")
async def stock_out_ep(sku_id: str, request: Request) -> JSONResponse:
    """Stock left without being billed. Breakage, expiry, taken for the house.

    Selling is NOT one of the reasons here. A sale leaves through the counter,
    which writes it to the audit chain, which manage.py already subtracts —
    recording it here as well would take the same packet off the shelf twice.
    """
    return await _movement(sku_id, request, "out")


async def _movement(sku_id: str, request: Request, direction: str) -> JSONResponse:
    """Both movement endpoints, because they differ only in the sign and the
    vocabulary, and two copies of this would eventually differ in more."""
    try:
        body = await _json_body(request)
        rec = _known_sku(sku_id)
        units = _movement_units(body, direction)
        reason = _reason(body, direction)
        note = _note(body)

        signed = units if direction == "in" else -units
        movement_id = "mv_" + secrets.token_hex(6)
        at = _now_iso()
        head = _append(
            EV_IN if direction == "in" else EV_OUT,
            ts=at,
            movement_id=movement_id,
            sku_id=sku_id,
            units=signed,
            reason=reason,
            note=note,
            name=str(rec.get("name") or sku_id),
        )

        row = _row_for(sku_id)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "sku_id": sku_id,
            "movement_id": movement_id,
            "kind": direction,
            "units": signed,
            "reason": reason,
            "reason_label": (IN_REASONS if direction == "in"
                             else OUT_REASONS)[reason],
            "note": note,
            "recorded_at": at,
            "chain_head": head,
            "on_hand_units": None if row is None else row["on_hand_units"],
            "derivation": None if row is None else row["derivation"],
            "needs_recount": bool(row is not None and row["needs_recount"]),
            "at_or_under_reorder_level": bool(
                row is not None and row["at_or_under_reorder_level"]),
            "detail": (
                f"{units} recorded as stock {direction} for {sku_id} "
                f"({reason}). This is your word rather than something the "
                f"counter saw, and it is now on the log."),
        })
    except StockRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/stock/{sku_id}/count")
async def stock_count_ep(sku_id: str, request: Request) -> JSONResponse:
    """A re-count. Body: {"units": 40}. This RESETS the baseline.

    Two things happen, in this order:

      1. the derived figure is read BEFORE the write, so the response can say
         what the counter expected and by how much the shelf disagreed — which
         is the entire point of counting a shelf that a computer is tracking;
      2. the count is written through `manage.write_opening_stock()`, the same
         baseline the inventory screen reads, so there is one baseline in this
         program and not two.

    Movements before this moment stay in the log and stop counting. The
    discrepancy is recorded on the chain rather than quietly absorbed: an
    unexplained three packets is the shopkeeper's evidence that something is
    going missing, and it only exists if somebody wrote it down.
    """
    try:
        body = await _json_body(request)
        _known_sku(sku_id)
        units = _whole_units(
            body, "units",
            missing=R_UNITS_MISSING,
            fractional=R_UNITS_FRACTIONAL,
            not_integer=R_UNITS_NOT_INTEGER)
        if units < 0:
            raise StockRefused(
                R_UNITS_NEGATIVE,
                f"units={units} is negative. Zero is a valid count and means "
                f"the shelf is empty. Nothing was recorded.")
        cap = max_count_units()
        if units > cap:
            raise StockRefused(
                R_UNITS_TOO_LARGE,
                f"units={units} is over {cap}. Nothing is recorded: a number "
                f"nobody typed on purpose must not appear on the page as "
                f"though somebody had.")
        note = _note(body)

        before = _row_for(sku_id)
        expected = None if before is None else before["on_hand_units"]
        superseded = 0 if before is None else (
            before["movements_since_count"]
            + before["movements_superseded_by_count"])
        discrepancy = None if expected is None else units - expected

        stock, _err = manage.read_opening_stock()
        counted_at = _now_iso()
        stock[sku_id] = {"units": units, "counted_at": counted_at}
        try:
            with _WRITE_LOCK:
                manage.write_opening_stock(stock)
        except OSError as exc:
            raise StockRefused(
                R_COUNT_NOT_WRITTEN,
                f"the count could not be written to {manage.stock_path()} "
                f"({type(exc).__name__}: {exc}). Nothing was recorded, so the "
                f"page is not about to show you a number that is not on disk."
            ) from None

        # After the baseline, not before: if the append fails the count is
        # already the truth on disk, and a refusal here would tell the
        # shopkeeper his count was lost when it was not. He is told instead
        # that the count stands and the audit line did not.
        head: Optional[str] = None
        audit_error: Optional[str] = None
        try:
            head = _append(
                EV_COUNT,
                ts=counted_at,
                sku_id=sku_id,
                counted_units=units,
                counted_at=counted_at,
                expected_units=expected,
                discrepancy_units=discrepancy,
                superseded_movements=superseded,
                note=note)
        except StockRefused as exc:
            audit_error = exc.detail

        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "sku_id": sku_id,
            "counted_units": units,
            "counted_at": counted_at,
            "expected_units": expected,
            "discrepancy_units": discrepancy,
            "superseded_movements": superseded,
            "on_hand_units": units,
            "audited": head is not None,
            "audit_error": audit_error,
            "chain_head": head,
            "note": note,
            "detail": _count_sentence(units, expected, discrepancy),
        })
    except StockRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


def _count_sentence(units: int, expected: Optional[int],
                    discrepancy: Optional[int]) -> str:
    """What the count means, in the shopkeeper's terms and without a euphemism."""
    if expected is None or discrepancy is None:
        return (f"Counted {units} by you just now. From here the counter "
                f"subtracts what it bills and adds what you record; it cannot "
                f"see anything that leaves the shop another way.")
    if discrepancy == 0:
        return (f"Counted {units}, which is exactly what the counter expected. "
                f"Nothing has gone missing since the last count.")
    if discrepancy < 0:
        return (f"Counted {units}; the counter expected {expected}. "
                f"{-discrepancy} fewer than it can account for — that is stock "
                f"that left without being billed or written down. The gap is "
                f"on the log with this count.")
    return (f"Counted {units}; the counter expected {expected}. {discrepancy} "
            f"more than it can account for — a delivery that was never booked "
            f"in, or an earlier count that was short. The gap is on the log "
            f"with this count.")


@router.post("/stock/{sku_id}/reorder")
async def stock_reorder_ep(sku_id: str, request: Request) -> JSONResponse:
    """Set the level at which this product should be reordered.

    Body: {"units": 12}, or {"units": null} to clear it. The level is the
    shopkeeper's judgement about his own shelf and this module does not propose
    one: a suggested level derived from a fortnight of trade would be a number
    the counter invented, sitting in a field that says he chose it.
    """
    try:
        body = await _json_body(request)
        _known_sku(sku_id)

        if "units" not in body:
            raise StockRefused(
                R_LEVEL_MISSING,
                'no "units" in the body. Send {"units": 12} to set the level, '
                'or {"units": null} to clear it.')

        if body["units"] is None:
            head = _append(EV_LEVEL, ts=_now_iso(), sku_id=sku_id,
                           level_units=None, cleared=True)
            return JSONResponse({
                "ok": True, "settles_money": False, "sku_id": sku_id,
                "reorder_level": None, "cleared": True, "chain_head": head,
                "detail": (f"The reorder level for {sku_id} is cleared. It will "
                           f"not appear on the low-stock list again until a "
                           f"level is set."),
            })

        level = _whole_units(
            body, "units",
            missing=R_LEVEL_MISSING,
            fractional=R_LEVEL_FRACTIONAL,
            not_integer=R_LEVEL_NOT_INTEGER)
        if level < 0:
            raise StockRefused(
                R_LEVEL_NEGATIVE,
                f"units={level} is negative. A reorder level of zero is valid "
                f"and means 'tell me when the shelf is empty'.")
        if level > MAX_REORDER_UNITS:
            raise StockRefused(
                R_LEVEL_TOO_LARGE,
                f"units={level} is over {MAX_REORDER_UNITS}. Nothing was "
                f"recorded.")

        head = _append(EV_LEVEL, ts=_now_iso(), sku_id=sku_id,
                       level_units=level, cleared=False)
        row = _row_for(sku_id)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "sku_id": sku_id,
            "reorder_level": level,
            "cleared": False,
            "chain_head": head,
            "on_hand_units": None if row is None else row["on_hand_units"],
            "at_or_under_reorder_level": bool(
                row is not None and row["at_or_under_reorder_level"]),
            "detail": (
                f"{sku_id} will appear on the low-stock list when the shelf is "
                f"at or under {level}. Whether it is low right now cannot be "
                f"said until the shelf has been counted once."
                if row is None or row["on_hand_units"] is None else
                f"{sku_id} will appear on the low-stock list when the shelf is "
                f"at or under {level}. It is at {row['on_hand_units']} now."),
        })
    except StockRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/stock/{sku_id}/floor")
async def stock_floor_ep(sku_id: str, request: Request) -> JSONResponse:
    """Stop selling this product ONLINE when the shelf is down to N.

    Body: {"units": 2}. {"units": 0} or {"units": null} puts it back to the
    default, which is "sell down to the last packet". The floor is the
    shopkeeper's own judgement about who the last packets are for, and this
    module does not propose one.

    WHAT IT DOES NOT DO: it does not hide the product, does not change the
    count, and does not touch the till — a walk-in customer can still buy the
    reserved packets at the counter. The storefront reads the floor through
    `stock_rows()` and refuses an online order that would go under it; that
    refusal is the rule, and this line is what it reads.
    """
    try:
        body = await _json_body(request)
        _known_sku(sku_id)

        if "units" not in body:
            raise StockRefused(
                R_FLOOR_MISSING,
                'no "units" in the body. Send {"units": 2} to stop selling '
                'online at two, or {"units": 0} to sell down to the last one.')
        if body["units"] is None:
            floor = DEFAULT_ONLINE_FLOOR
        else:
            floor = _whole_units(
                body, "units",
                missing=R_FLOOR_MISSING,
                fractional=R_FLOOR_FRACTIONAL,
                not_integer=R_FLOOR_NOT_INTEGER)
        if floor < 0:
            raise StockRefused(
                R_FLOOR_NEGATIVE,
                f"units={floor} is negative. The smallest floor is 0, which "
                f"means the storefront may sell the last packet.")
        if floor > MAX_ONLINE_FLOOR:
            raise StockRefused(
                R_FLOOR_TOO_LARGE,
                f"units={floor} is over {MAX_ONLINE_FLOOR}. Nothing was "
                f"recorded.")

        head = _append(EV_FLOOR, ts=_now_iso(), sku_id=sku_id,
                       floor_units=floor)
        row = _row_for(sku_id)
        on_hand = None if row is None else row["on_hand_units"]
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "sku_id": sku_id,
            "online_floor": floor,
            "is_default": floor == DEFAULT_ONLINE_FLOOR,
            "chain_head": head,
            "on_hand_units": on_hand,
            "detail": (
                f"The storefront will not sell {sku_id} below {floor} on the "
                f"shelf. "
                + ("Whether that stops it right now cannot be said until the "
                   "shelf has been counted once."
                   if on_hand is None else
                   f"The shelf is at {on_hand} now; open online orders are "
                   f"subtracted before the floor is compared.")
                if floor > DEFAULT_ONLINE_FLOOR else
                f"The storefront may sell {sku_id} down to the last packet."),
        })
    except StockRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


__all__ = [
    "DEFAULT_ONLINE_FLOOR",
    "StockRefused",
    "audit_path",
    "billing_rates",
    "movements_by_sku",
    "online_floors",
    "read_events",
    "reorder_levels",
    "router",
    "shop_dir",
    "stock_rows",
]
