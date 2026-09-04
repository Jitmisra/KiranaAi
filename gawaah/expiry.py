"""MIYAAD — what goes off, and what it is worth when it does.

Dahi, bread, milk, biscuits: a kirana's shelves carry dates, and every packet
that passes its date is money the shop paid a supplier and will never get back.
Nothing on the counter sees a date — the camera reads a code, not the small
print under it — so this module holds the one thing a shopkeeper CAN know at
the moment stock arrives: this many units, going off on this day.

A BATCH IS A NOTE THE SHOPKEEPER MADE
=====================================
A batch is `sku_id`, a whole number of units, and the date printed on the
packet. It is recorded when the delivery arrives, and from then on the counter
can answer two questions it could not answer before:

    what goes off within N days, soonest first?
    what has already gone off, and is it still on the shelf?

WHAT "UNITS" MEANS HERE, AND WHAT IT DOES NOT
==============================================
The units on a batch are what was booked in, less what the shopkeeper has since
written off or said sold through. THE COUNTER DOES NOT KNOW WHICH BATCH A SOLD
PACKET CAME FROM: a sale on the audit chain names a product, not a delivery, and
guessing first-in-first-out would put a confident number on the page that
nothing observed. So a batch stays at its booked units until a person says
otherwise, and the page says so beside every figure.

VALUE AT RISK IS A DESCRIPTION, NOT A CHARGE
============================================
Every value here is `units remaining × the marked price in the catalogue`, as
integer paise, multiplied and summed with integer arithmetic and nothing else.
It is what the shelf would fetch if every unit sold at its marked price, and it
is a way of ranking what to clear first. It moves no money, appears on no bill,
is not a loss the books record, and an offer running today would make the real
figure lower. Where a product has no price, or has left the catalogue, the value
is `null` with a sentence saying which — never a zero standing in for "unknown".

THE WRITE-OFF GOES THROUGH THE STOCK MODULE'S OWN WRITER
========================================================
Writing off an expired batch appends a stock OUT with reason `expiry` to
`gawaah/stock.py`'s hash chain, through that module's own append function and
in its own vocabulary, so the Stock screen's on-hand figure comes down by the
same units and the movement is listed there with every other movement. This
module keeps no second stock figure and no second sales count.

That writer is looked up at call time, by name. If `stock.py` no longer exposes
it, or the append fails, the write-off is still recorded HERE and the response
says, in as many words, that the stock figure has not moved and the shelf needs
a re-count. The batch is never left half-written: the stock line is written
first, and if this module's own line then cannot be appended, a reversing stock
IN with reason `correction` is written and the whole write-off is refused.

TODAY IS THE SHOP'S CALENDAR DAY
================================
A date on a packet is a day, not an instant. "Expired" means the packet's date
is before today on the machine's own local calendar, so a packet marked with
today's date is not expired yet. Every response carries `today` so the page can
show what day the figures were computed for.

A REFUSAL IS A RESULT
=====================
Every failure below has a name, a sentence a shopkeeper can act on, and a 400
(404 for a batch or a product that is not there). Nothing raises a 500, and a
line that could not be appended to the chain is a refusal, because the chain
is this module's only store.

MOUNTING
========
The router carries NO prefix; the paths are absolute::

    GET  /expiry                       the overview: expired, expiring, totals
    GET  /expiry/soon?days=7           expiring within N days, soonest first
    GET  /expiry/expired               past their date and still on a batch
    GET  /expiry/batches?sku=          every batch, optionally for one product
    POST /expiry/batch                 record a batch
    POST /expiry/batch/{id}/write-off  it went off: stock OUT, reason expiry
    POST /expiry/batch/{id}/sold       it sold through before the date

    from gawaah import expiry
    app.include_router(expiry.router)
"""
from __future__ import annotations

import json
import math
import re
import secrets
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import manage
from .ledger import GENESIS, Ledger, verify
from .money import MoneyError, paise, to_rupees_str

router = APIRouter()


# ---------------------------------------------------------------- refusals --
#
# Lowercase snake_case naming the STATE, as every other module here does. The
# sentence saying what to do about it goes in `detail`, never in the reason.

R_BAD_BODY = "expiry_body_not_json"
R_SKU_MISSING = "expiry_sku_missing"
R_UNKNOWN_SKU = "sku_not_in_the_catalogue"

R_UNITS_MISSING = "expiry_units_missing"
R_UNITS_FRACTIONAL = "expiry_units_fractional"
R_UNITS_NOT_INTEGER = "expiry_units_not_a_whole_number"
R_UNITS_NOT_POSITIVE = "expiry_units_not_positive"
R_UNITS_TOO_LARGE = "expiry_units_implausible"
R_UNITS_OVER_REMAINING = "expiry_units_more_than_the_batch_has_left"

R_DATE_MISSING = "expiry_date_missing"
R_DATE_NOT_TEXT = "expiry_date_not_text"
R_DATE_MALFORMED = "expiry_date_not_yyyy_mm_dd"
R_DATE_IMPOSSIBLE = "expiry_date_not_on_the_calendar"
R_DATE_TOO_FAR = "expiry_date_implausibly_far_ahead"
R_DATE_TOO_OLD = "expiry_date_implausibly_far_back"

R_NOTE_NOT_TEXT = "expiry_note_not_text"
R_NOTE_TOO_LONG = "expiry_note_too_long"

R_CLIENT_PRICED = "client_tried_to_price_the_batch"
R_BAD_BATCH_ID = "batch_id_malformed"
R_NO_BATCH = "no_such_batch"
R_BATCH_CLOSED = "batch_already_closed"
R_BAD_DAYS = "days_not_a_whole_number_in_range"
R_NOT_RECORDED = "expiry_line_not_recorded"
R_CATALOGUE_UNAVAILABLE = "catalogue_unavailable"
R_INTERNAL = "expiry_internal_error"


# ------------------------------------------------------------------ limits --

#: The events this module writes. Named once, so a reader of the log does not
#: have to guess which line means what.
EV_BATCH = "expiry.batch"
EV_WRITE_OFF = "expiry.written_off"
EV_SOLD = "expiry.sold"

#: The stock module's own words for the two movements this module causes.
#: `expiry` is the reason that module labels "past its date"; `delivery` is
#: what a batch arriving IS. Neither is invented here — both are checked against
#: `stock.OUT_REASONS` / `stock.IN_REASONS` before a line is written, so a
#: rename there makes this module fall back rather than write a word the Stock
#: screen cannot label.
STOCK_OUT_REASON = "expiry"
STOCK_IN_REASON = "delivery"
STOCK_REVERSAL_REASON = "correction"

#: One batch is a delivery, not a warehouse. The same cap as one stock movement,
#: refused by name and never clamped, because clamping stores a figure the
#: shopkeeper did not type and shows it back as his own.
MAX_BATCH_UNITS = 100_000

#: How far ahead a printed date can plausibly be. Ten years covers rice and salt;
#: a date past that is a typo (2062 for 2026) and is refused rather than filed
#: as something that will show up on a grandchild's screen.
MAX_DAYS_AHEAD = 3653
#: How far back. A packet found on the shelf a month past its date is a real
#: thing to record and write off; a date more than a year ago is a typo.
MAX_DAYS_BEHIND = 366

MAX_NOTE = 200
#: Stock's own note cap. The note this module puts on a stock line is trimmed
#: to it, so the line is one that module's own endpoint would have accepted.
STOCK_MAX_NOTE = 200

DEFAULT_WINDOW_DAYS = 7
MAX_WINDOW_DAYS = 365

AUDIT_FILENAME = "expiry.audit.jsonl"
BATCH_ID_RE = re.compile(r"^bt_[0-9a-f]{12}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Keys a page has no business sending. A batch is units and a date; anything
#: that looks like a price is refused by name, because a value the browser
#: asserted and the server quietly dropped is a value the page can go on
#: showing as though the shop agreed to it.
PRICE_KEYS = ("price_paise", "price", "value_paise", "value_at_risk_paise",
              "amount_paise", "amount", "value_rupees", "price_rupees")

#: One process appends to this chain, one thread at a time. See the same note
#: in stock.py: the chain head is read from the file and written back, and two
#: interleaved appends would leave a line `verify()` fails on forever after.
_WRITE_LOCK = threading.Lock()


class ExpiryRefused(Exception):
    """A named refusal with a sentence a shopkeeper can act on."""

    def __init__(self, reason: str, detail: str, status: int = 400) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.status = status


def _refusal(exc: ExpiryRefused) -> JSONResponse:
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


# --------------------------------------------------------- where things are --
#
# Resolved per call, never memoised at import: a test that sets GAWAAH_SHOP_DIR
# in a fixture must be able to change it between tests, and a module constant
# captured at import silently ignores that — which is how a harness once wrote
# over the live catalogue.


def shop_dir() -> Path:
    """The catalogue directory — manage.py's answer, the one stock.py uses too.

    The write-off lands on stock.py's chain, and stock.py resolves its chain
    from `manage.store_dir()`. A directory this module resolved differently
    would put the batches in one shop and the stock movement in another.
    """
    return Path(manage.store_dir())


def audit_path() -> Path:
    """This module's own hash chain. Not `results/audit.jsonl`: that file is
    held open by the money service in another process, and a second writer
    between two of its appends breaks the one log that must be beyond argument
    — see the same note in storefront.py, offers.py and stock.py."""
    # `joinpath`, not the `/` operator: the AST check in test_expiry.py — the
    # same three checks tools/lint_no_float.py applies to the money modules —
    # reads a pathlib `/` as a division, and a lint people learn to wave
    # through is worse than no lint.
    return shop_dir().joinpath(AUDIT_FILENAME)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> date:
    """The shop's calendar day: the machine's LOCAL date, not UTC.

    A packet marked 2 September is good on 2 September in the shop it sits in.
    At 11 pm in India UTC is still the day before, and "expired" computed on
    UTC would be a day late for every shopkeeper this counter is for.
    """
    return datetime.now().astimezone().date()


# ------------------------------------------------------------------ the chain


def read_events() -> tuple[tuple[dict, ...], dict]:
    """Every verified line of this module's log, plus the state of the chain.

    Truncated at the first broken link, as every chain reader here does: a line
    whose hash does not recompute is not evidence. The break is reported in a
    `chain` block on every response rather than raised, so a shopkeeper whose
    file was edited can still see the batches from before the edit and is told,
    in the same breath, that something is wrong with the file.
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


def _whole(value: Any) -> Optional[int]:
    """An int that is not a bool, or None. Chain lines are read, not trusted."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _valid_date_str(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not DATE_RE.match(value):
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        return None
    return value


def batches_from(events: tuple[dict, ...]) -> tuple[dict[str, dict[str, Any]], int]:
    """{batch_id -> batch} replayed from the chain, and how many lines were not
    readable as anything.

    The unreadable count is returned rather than swallowed: a log with lines
    this module cannot read is a log whose figures are short by an unknown
    amount, and a page showing the figures without the count would be reporting
    a number it knew to be incomplete.

    A write-off or a sale against a batch that is not on the chain is such a
    line — it cannot be written by this module, so if one is there the file was
    edited by hand, and guessing which batch it meant would be fiction.
    """
    out: dict[str, dict[str, Any]] = {}
    skipped = 0
    for rec in events:
        ev = rec.get("event")
        if ev == EV_BATCH:
            bid = rec.get("batch_id")
            sku_id = rec.get("sku_id")
            units = _whole(rec.get("units"))
            expires_on = _valid_date_str(rec.get("expires_on"))
            if not isinstance(bid, str) or not BATCH_ID_RE.match(bid) \
                    or not isinstance(sku_id, str) or not sku_id \
                    or units is None or units <= 0 or expires_on is None:
                skipped += 1
                continue
            note = rec.get("note")
            out[bid] = {
                "batch_id": bid,
                "sku_id": sku_id,
                "name": rec.get("name") if isinstance(rec.get("name"), str) else sku_id,
                "units": units,
                "expires_on": expires_on,
                "note": note if isinstance(note, str) and note else None,
                "recorded_at": rec.get("ts"),
                "written_off_units": 0,
                "sold_units": 0,
                "stock_in_movement_id": rec.get("stock_movement_id"),
                "stock_in_recorded": bool(rec.get("stock_recorded")),
                "history": [{"at": rec.get("ts"), "kind": "booked",
                             "units": units,
                             "note": note if isinstance(note, str) and note else None,
                             "stock_movement_id": rec.get("stock_movement_id"),
                             "stock_recorded": bool(rec.get("stock_recorded")),
                             "hash": rec.get("hash")}],
            }
            continue
        if ev in (EV_WRITE_OFF, EV_SOLD):
            bid = rec.get("batch_id")
            units = _whole(rec.get("units"))
            batch = out.get(bid) if isinstance(bid, str) else None
            if batch is None or units is None or units <= 0:
                skipped += 1
                continue
            key = "written_off_units" if ev == EV_WRITE_OFF else "sold_units"
            batch[key] += units
            note = rec.get("note")
            batch["history"].append({
                "at": rec.get("ts"),
                "kind": "written_off" if ev == EV_WRITE_OFF else "sold",
                "units": units,
                "note": note if isinstance(note, str) and note else None,
                "stock_movement_id": rec.get("stock_movement_id"),
                "stock_recorded": bool(rec.get("stock_recorded")),
                "hash": rec.get("hash"),
            })
    for batch in out.values():
        batch["units_remaining"] = (
            batch["units"] - batch["written_off_units"] - batch["sold_units"])
    return out, skipped


# --------------------------------------------------------------- catalogue --


def _catalogue_skus() -> dict[str, dict[str, Any]]:
    """Everything the shop can price, through manage.py's reader — the same
    reader stock.py uses, so a product is either on both screens or neither."""
    try:
        return dict(manage.catalogue().get("items") or {})
    except Exception as exc:  # noqa: BLE001 - a broken read is a named answer
        raise ExpiryRefused(
            R_CATALOGUE_UNAVAILABLE,
            f"the catalogue at {shop_dir()} could not be read "
            f"({type(exc).__name__}: {exc}). No batch was touched.") from None


def _price_of(rec: Optional[dict[str, Any]]) -> Optional[int]:
    """The marked price of one product as integer paise, or None.

    `paise()` refuses a float and a bool. A catalogue that held 21.45 instead of
    2145 would make this None, and the row would say "no price" rather than
    multiply a float into a rupee figure.
    """
    if rec is None:
        return None
    raw = rec.get("price_paise")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return None
    try:
        return int(paise(raw))
    except MoneyError:
        return None


def _known_sku(sku_id: str) -> dict[str, Any]:
    rec = _catalogue_skus().get(sku_id)
    if rec is None:
        raise ExpiryRefused(
            R_UNKNOWN_SKU,
            f"{sku_id!r} is not in the catalogue at {shop_dir()}. Teach the "
            f"product first: a batch of something the shop does not sell is a "
            f"date with nothing to attach it to.",
            status=404)
    return rec


# ----------------------------------------------------------------- the rows --


def _row(batch: dict[str, Any], known: dict[str, dict[str, Any]],
         today: date) -> dict[str, Any]:
    """One batch, with the days left and the value at risk — or why not."""
    rec = known.get(batch["sku_id"])
    remaining = int(batch["units_remaining"])
    expires = date.fromisoformat(batch["expires_on"])
    days_left = (expires - today).days
    closed = remaining <= 0
    state = "closed" if closed else ("expired" if days_left < 0 else "open")

    price = _price_of(rec)
    value: Optional[int]
    why: Optional[str]
    if price is None:
        value = None
        if rec is None:
            why = ("This product is no longer in the catalogue, so there is no "
                   "price to multiply by. The units are still counted.")
        else:
            why = ("This product has no price in the catalogue, so its value "
                   "cannot be stated. The units are still counted.")
    else:
        # THE ONLY MULTIPLICATION IN THIS FILE: integer units × integer paise.
        value = int(paise(remaining * price)) if not closed else 0
        why = None

    return {
        "batch_id": batch["batch_id"],
        "sku_id": batch["sku_id"],
        "name": (str(rec.get("name")) if rec is not None and rec.get("name")
                 else batch["name"]),
        "in_catalogue": rec is not None,
        "units": int(batch["units"]),
        "written_off_units": int(batch["written_off_units"]),
        "sold_units": int(batch["sold_units"]),
        "units_remaining": remaining,
        "expires_on": batch["expires_on"],
        "days_left": days_left,
        "state": state,
        "recorded_at": batch["recorded_at"],
        "note": batch["note"],
        "price_paise": price,
        "price_rupees": None if price is None else to_rupees_str(paise(price)),
        "value_at_risk_paise": value,
        "value_at_risk_rupees": (None if value is None
                                 else to_rupees_str(paise(value))),
        "value_why": why,
        "stock_in_recorded": bool(batch["stock_in_recorded"]),
        "stock_in_movement_id": batch["stock_in_movement_id"],
        "history": batch["history"],
    }


def _sort_key(row: dict[str, Any]) -> tuple:
    """Soonest first; the id is the tiebreak so two identical requests agree."""
    return (int(row["days_left"]), str(row["sku_id"]), str(row["batch_id"]))


def rows(today: Optional[date] = None) -> dict[str, Any]:
    """Every batch as a row, plus the chain state. One assembly behind every
    read endpoint, so the lists cannot drift apart."""
    known = _catalogue_skus()
    events, chain = read_events()
    batches, unreadable = batches_from(events)
    day = today if today is not None else _today()
    out = [_row(b, known, day) for b in batches.values()]
    out.sort(key=_sort_key)
    return {
        "rows": out,
        "known": known,
        "chain": chain,
        "unreadable_lines": unreadable,
        "today": day.isoformat(),
    }


def _sum_value(rows_: list[dict[str, Any]]) -> tuple[int, int]:
    """Integer addition of the priced rows, and how many rows had no price.

    The unpriced count is part of the answer. A total that silently omits three
    batches is a smaller number than the truth, shown with the same confidence.
    """
    total = 0
    unpriced = 0
    for r in rows_:
        v = r.get("value_at_risk_paise")
        if v is None:
            unpriced += 1
            continue
        total += int(paise(v))
    return int(paise(total)), unpriced


def _value_block(expired: list[dict[str, Any]],
                 soon: list[dict[str, Any]]) -> dict[str, Any]:
    exp_paise, exp_unpriced = _sum_value(expired)
    soon_paise, soon_unpriced = _sum_value(soon)
    return {
        "expired_paise": exp_paise,
        "expired_rupees": to_rupees_str(paise(exp_paise)),
        "expired_unpriced_batches": exp_unpriced,
        "soon_paise": soon_paise,
        "soon_rupees": to_rupees_str(paise(soon_paise)),
        "soon_unpriced_batches": soon_unpriced,
        "basis": ("units remaining on the batch × the marked price in the "
                  "catalogue, in integer paise"),
        "note": (
            "A description, not a charge. This is what the units would fetch "
            "at their marked price if every one of them sold. It moves no "
            "money, is on no bill, and is not a loss the books record. An "
            "offer running today would make the real figure lower, and a "
            "batch with no price is left out of the total and counted "
            "separately."),
    }


def _products(known: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The picker's list: every product, with its marked price where it has one."""
    out = []
    for sku_id, rec in known.items():
        price = _price_of(rec)
        out.append({
            "sku_id": sku_id,
            "name": str(rec.get("name") or sku_id),
            "price_paise": price,
            "price_rupees": None if price is None else to_rupees_str(paise(price)),
        })
    out.sort(key=lambda p: (str(p["name"]).lower(), str(p["sku_id"])))
    return out


# ------------------------------------------------------------ reading input --


async def _json_body(request: Request, *, allow_empty: bool = False
                     ) -> dict[str, Any]:
    raw = await request.body()
    if allow_empty and not raw.strip():
        return {}
    try:
        body = json.loads(raw)
    except Exception:  # noqa: BLE001 - a malformed body is a named refusal
        raise ExpiryRefused(
            R_BAD_BODY,
            'the body of this request is not JSON. It should look like '
            '{"sku_id": "amul_dahi", "units": 12, "expires_on": "2026-09-15"}.'
        ) from None
    if not isinstance(body, dict):
        raise ExpiryRefused(
            R_BAD_BODY,
            f"the body of this request is a {type(body).__name__}; it must be "
            f"a JSON object.")
    for key in PRICE_KEYS:
        if key in body:
            raise ExpiryRefused(
                R_CLIENT_PRICED,
                f"this request carries {key!r}. A batch is units and a date; "
                f"the value at risk is derived here from the catalogue and "
                f"nothing a page sends can set it. Nothing was recorded.")
    return body


def _sku_id(body: dict[str, Any]) -> str:
    raw = body.get("sku_id")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise ExpiryRefused(
            R_SKU_MISSING,
            'no "sku_id" in the body. A batch is a batch OF something.')
    if not isinstance(raw, str):
        raise ExpiryRefused(
            R_SKU_MISSING,
            f"sku_id={raw!r} is a {type(raw).__name__}; it must be the id of a "
            f"product in the catalogue.")
    return raw.strip()


def _whole_units(body: dict[str, Any], key: str = "units") -> int:
    """A whole number of packets, or a refusal that names which kind of wrong.

    The fractional case is separated from the rest for the reason stock.py
    gives: "2.5 is not whole" and "'2.5' is not a number" are different mistakes
    with different fixes, and 2.0 is a third — a whole value that arrived as a
    decimal means something upstream is doing arithmetic on packets in floating
    point, which is worth saying out loud.
    """
    if key not in body:
        raise ExpiryRefused(
            R_UNITS_MISSING, f'no "{key}" in the body. Send {{"{key}": 12}}.')
    raw = body[key]
    if isinstance(raw, float):
        if not math.isfinite(raw):
            raise ExpiryRefused(
                R_UNITS_NOT_INTEGER,
                f"{key}={raw!r} is not a number of packets at all. Nothing was "
                f"recorded.")
        if raw != int(raw):
            raise ExpiryRefused(
                R_UNITS_FRACTIONAL,
                f"{key}={raw!r} is a fraction of a packet. A shelf holds whole "
                f"packets and a date is printed on whole packets. Nothing was "
                f"recorded.")
        raise ExpiryRefused(
            R_UNITS_NOT_INTEGER,
            f"{key}={raw!r} arrived as a decimal. Packets are counted, not "
            f"measured — send {int(raw)}. Nothing was recorded.")
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ExpiryRefused(
            R_UNITS_NOT_INTEGER,
            f"{key}={raw!r} is not a whole number of packets. Nothing was "
            f"recorded.")
    units = int(raw)
    if units <= 0:
        raise ExpiryRefused(
            R_UNITS_NOT_POSITIVE,
            f"{key}={units}. A batch has at least one packet in it. Nothing "
            f"was recorded.")
    if units > MAX_BATCH_UNITS:
        raise ExpiryRefused(
            R_UNITS_TOO_LARGE,
            f"{key}={units} is over {MAX_BATCH_UNITS} in one batch. Nothing is "
            f"recorded: a number nobody typed on purpose must not appear on "
            f"the page as though somebody had.")
    return units


def _expires_on(body: dict[str, Any], today: date) -> str:
    """The date on the packet as YYYY-MM-DD, or a refusal naming what is wrong."""
    raw = body.get("expires_on")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise ExpiryRefused(
            R_DATE_MISSING,
            'no "expires_on" in the body. Send the date printed on the packet '
            'as YYYY-MM-DD, e.g. "2026-09-15".')
    if not isinstance(raw, str):
        raise ExpiryRefused(
            R_DATE_NOT_TEXT,
            f"expires_on={raw!r} is a {type(raw).__name__}. Send the date as "
            f"text, YYYY-MM-DD.")
    s = raw.strip()
    if not DATE_RE.match(s):
        raise ExpiryRefused(
            R_DATE_MALFORMED,
            f"expires_on={s!r} is not YYYY-MM-DD. Send the date printed on the "
            f"packet as four-digit year, two-digit month, two-digit day, e.g. "
            f"2026-09-15. Nothing was recorded.")
    try:
        d = date.fromisoformat(s)
    except ValueError:
        raise ExpiryRefused(
            R_DATE_IMPOSSIBLE,
            f"expires_on={s!r} is not a day on the calendar. Check the month "
            f"and the day. Nothing was recorded.") from None
    ahead = (d - today).days
    if ahead > MAX_DAYS_AHEAD:
        raise ExpiryRefused(
            R_DATE_TOO_FAR,
            f"expires_on={s} is {ahead} days away, more than {MAX_DAYS_AHEAD}. "
            f"That is usually a year typed wrong. Nothing was recorded.")
    if -ahead > MAX_DAYS_BEHIND:
        raise ExpiryRefused(
            R_DATE_TOO_OLD,
            f"expires_on={s} was {-ahead} days ago, more than {MAX_DAYS_BEHIND}. "
            f"That is usually a year typed wrong. Nothing was recorded.")
    return d.isoformat()


def _note(body: dict[str, Any]) -> Optional[str]:
    raw = body.get("note")
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ExpiryRefused(
            R_NOTE_NOT_TEXT,
            f"note={raw!r} is a {type(raw).__name__}. A note is free text, or "
            f"leave it out.")
    note = " ".join(raw.split())
    if not note:
        return None
    if len(note) > MAX_NOTE:
        raise ExpiryRefused(
            R_NOTE_TOO_LONG,
            f"the note is {len(note)} characters and the cap is {MAX_NOTE}. "
            f"Nothing was recorded.")
    return note


def _flag(body: dict[str, Any], key: str) -> bool:
    """A yes/no the page sends. Only a real JSON true counts as yes: a page
    that sent the string "false" and got a delivery booked in would be right
    to complain."""
    return body.get(key) is True


def _window(raw: Any) -> int:
    if raw is None or raw == "":
        return DEFAULT_WINDOW_DAYS
    try:
        days = int(str(raw))
    except (TypeError, ValueError):
        raise ExpiryRefused(
            R_BAD_DAYS,
            f"days={raw!r} is not a whole number. Leave it out for "
            f"{DEFAULT_WINDOW_DAYS}.") from None
    if days < 0 or days > MAX_WINDOW_DAYS:
        raise ExpiryRefused(
            R_BAD_DAYS,
            f"days={days} is outside 0 to {MAX_WINDOW_DAYS}. Zero means "
            f"'going off today'.")
    return days


def _valid_batch_id(batch_id: str) -> str:
    s = (batch_id or "").strip()
    if not BATCH_ID_RE.match(s):
        raise ExpiryRefused(
            R_BAD_BATCH_ID,
            f"{batch_id!r} is not a batch id from this shop. They look like "
            f"'bt_' followed by twelve hex characters.")
    return s


def _truthy(raw: Any) -> bool:
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


# ----------------------------------------------------- the stock module's path


def _stock_writer() -> Optional[tuple[Any, Any]]:
    """`(stock module, its append)` if stock.py exposes the path this module
    needs, else None.

    Looked up by name at call time, not imported at module scope: the whole
    point of the fallback is to survive that module changing shape, and an
    import that fails at startup would take this router down with it. Every
    name used below is checked, including that the two reasons this module
    writes are in the vocabulary the Stock screen labels — a line with a word
    that screen cannot label would still be counted there, but it would be
    listed as "no reason", and a shopkeeper reading the log deserves better.
    """
    try:
        from . import stock as _stock  # noqa: WPS433 - deliberately late
    except Exception:  # noqa: BLE001 - absent is a supported state
        return None
    append = getattr(_stock, "_append", None)
    ev_in = getattr(_stock, "EV_IN", None)
    ev_out = getattr(_stock, "EV_OUT", None)
    in_reasons = getattr(_stock, "IN_REASONS", None)
    out_reasons = getattr(_stock, "OUT_REASONS", None)
    if not callable(append) or not isinstance(ev_in, str) \
            or not isinstance(ev_out, str) \
            or not isinstance(in_reasons, dict) \
            or not isinstance(out_reasons, dict) \
            or STOCK_OUT_REASON not in out_reasons \
            or STOCK_IN_REASON not in in_reasons \
            or STOCK_REVERSAL_REASON not in in_reasons \
            or STOCK_REVERSAL_REASON not in out_reasons:
        return None
    return _stock, append


def stock_link() -> dict[str, Any]:
    """Whether a write-off here will move the figure on the Stock screen."""
    available = _stock_writer() is not None
    return {
        "available": available,
        "out_reason": STOCK_OUT_REASON,
        "detail": (
            "A write-off appends a stock OUT with reason 'expiry' to the stock "
            "log, so the on-hand figure on the Stock screen comes down by the "
            "same units."
            if available else
            "gawaah/stock.py does not expose the writer this module needs, so "
            "a write-off is recorded here only and the shelf has to be "
            "re-counted on the Stock screen."),
    }


def _stock_move(direction: str, sku_id: str, units: int, reason: str,
                note: str, name: str) -> tuple[Optional[str], Optional[str]]:
    """One line on the STOCK chain, through stock.py's own writer.

    Returns `(movement_id, None)` or `(None, why)`. Never raises: a stock line
    that could not be written is a fact the caller reports, not a crash, and
    which of the two it is decides whether the response says "the shelf figure
    has moved" or "count the shelf again".
    """
    writer = _stock_writer()
    if writer is None:
        return None, ("gawaah/stock.py does not expose its chain writer, so no "
                      "stock line was written")
    mod, append = writer
    event = mod.EV_IN if direction == "in" else mod.EV_OUT
    signed = units if direction == "in" else -units
    movement_id = "mv_" + secrets.token_hex(6)
    try:
        append(event, ts=_now_iso(), movement_id=movement_id, sku_id=sku_id,
               units=signed, reason=reason, note=note[:STOCK_MAX_NOTE],
               name=name)
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        detail = getattr(exc, "detail", None) or str(exc)
        return None, f"{type(exc).__name__}: {detail}"
    return movement_id, None


# --------------------------------------------------------------- the writes --


def _append(event: str, ts: str, **fields: Any) -> str:
    """One line on THIS module's chain, or a refusal. Never best-effort: the
    chain is the store, so an unappended line did not happen."""
    try:
        with _WRITE_LOCK:
            return Ledger(audit_path()).append(
                ts=ts, module="expiry", event=event, **fields)
    except Exception as exc:  # noqa: BLE001 - an unwritten line is a refusal
        raise ExpiryRefused(
            R_NOT_RECORDED,
            f"the line could not be appended to {audit_path()} "
            f"({type(exc).__name__}: {exc}). NOTHING WAS RECORDED here.") from None


def _find(batch_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The row for one batch and the assembled figures, or a 404 by name."""
    payload = rows()
    for r in payload["rows"]:
        if r["batch_id"] == batch_id:
            return r, payload
    raise ExpiryRefused(
        R_NO_BATCH,
        f"this shop has no batch {batch_id!r}. Nothing was changed.",
        status=404)


def _days_sentence(days_left: int) -> str:
    if days_left < -1:
        return f"went off {-days_left} days ago"
    if days_left == -1:
        return "went off yesterday"
    if days_left == 0:
        return "goes off today"
    if days_left == 1:
        return "goes off tomorrow"
    return f"goes off in {days_left} days"


# ----------------------------------------------------------------- the reads --
#
# `/expiry/batches`, `/expiry/soon` and `/expiry/expired` are literal paths and
# `/expiry/batch/{id}/...` is a different prefix, so there is no ordering trap
# here of the kind stock.py documents. They are still declared before the
# parameterised routes, because the next person to add `/expiry/{sku}` should
# find the literal ones already above it.


@router.get("/expiry")
def expiry_ep(days: str | None = None) -> JSONResponse:
    """The overview: what has gone off, what is about to, and what it is worth.

    `?days=` widens or narrows "about to"; the default is a week. Both lists
    are soonest first, and the expired list puts the LONGEST expired at the top,
    because that is the packet most likely to be sold to somebody by mistake.
    """
    try:
        window = _window(days)
        payload = rows()
        all_rows = payload["rows"]
        expired = [r for r in all_rows if r["state"] == "expired"]
        soon = [r for r in all_rows
                if r["state"] == "open" and r["days_left"] <= window]
        open_rows = [r for r in all_rows if r["state"] == "open"]
        closed = [r for r in all_rows if r["state"] == "closed"]
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "today": payload["today"],
            "window_days": window,
            "expired": expired,
            "soon": soon,
            "counts": {
                "batches": len(all_rows),
                "open": len(open_rows) + len(expired),
                "expired": len(expired),
                "soon": len(soon),
                "closed": len(closed),
            },
            "value_at_risk": _value_block(expired, soon),
            "products": _products(payload["known"]),
            "stock_link": stock_link(),
            "chain": payload["chain"],
            "unreadable_lines": payload["unreadable_lines"],
            "store_dir": str(shop_dir()),
            "note": (
                "The units on a batch are what you booked in, less what you "
                "have written off or said sold. This counter does not know "
                "which batch a sold packet came from, so a batch stays at its "
                "booked units until you say otherwise. Today is the date on "
                "this machine's own calendar."),
        })
    except ExpiryRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/expiry/soon")
def expiry_soon_ep(days: str | None = None) -> JSONResponse:
    """Batches going off within N days (today included), soonest first."""
    try:
        window = _window(days)
        payload = rows()
        soon = [r for r in payload["rows"]
                if r["state"] == "open" and r["days_left"] <= window]
        total, unpriced = _sum_value(soon)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "today": payload["today"],
            "window_days": window,
            "count": len(soon),
            "batches": soon,
            "value_at_risk_paise": total,
            "value_at_risk_rupees": to_rupees_str(paise(total)),
            "unpriced_batches": unpriced,
            "value_at_risk": _value_block([], soon),
            "chain": payload["chain"],
            "unreadable_lines": payload["unreadable_lines"],
        })
    except ExpiryRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/expiry/expired")
def expiry_expired_ep() -> JSONResponse:
    """Batches past their date with units still on them, longest expired first."""
    try:
        payload = rows()
        expired = [r for r in payload["rows"] if r["state"] == "expired"]
        total, unpriced = _sum_value(expired)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "today": payload["today"],
            "count": len(expired),
            "batches": expired,
            "value_at_risk_paise": total,
            "value_at_risk_rupees": to_rupees_str(paise(total)),
            "unpriced_batches": unpriced,
            "value_at_risk": _value_block(expired, []),
            "stock_link": stock_link(),
            "chain": payload["chain"],
            "unreadable_lines": payload["unreadable_lines"],
        })
    except ExpiryRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/expiry/batches")
def expiry_batches_ep(sku: str | None = None,
                      include_closed: str | None = None) -> JSONResponse:
    """Every batch, soonest first. `?sku=` narrows to one product.

    Closed batches — fully written off or sold through — are left out unless
    `?include_closed=1`, because a list a shopkeeper reads at a glance should
    not be mostly things that are finished with.
    """
    try:
        payload = rows()
        want_closed = _truthy(include_closed)
        out = [r for r in payload["rows"]
               if (sku is None or r["sku_id"] == sku)
               and (want_closed or r["state"] != "closed")]
        # Open and expired first, soonest first; closed at the end.
        out.sort(key=lambda r: (r["state"] == "closed", _sort_key(r)))
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "today": payload["today"],
            "sku": sku,
            "include_closed": want_closed,
            "count": len(out),
            "matched": len(out),
            "batches": out,
            "chain": payload["chain"],
            "unreadable_lines": payload["unreadable_lines"],
        })
    except ExpiryRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


# ---------------------------------------------------------------- the writes --


@router.post("/expiry/batch")
async def expiry_batch_ep(request: Request) -> JSONResponse:
    """Record a batch. Body: {sku_id, units, expires_on, note?, stock_in?}.

    `stock_in: true` ALSO books the units in as a delivery on the stock log,
    through stock.py's own writer, for the shopkeeper who is standing at the
    crate and does not want to type the same delivery on two screens. It is
    off by default, because a delivery that was already recorded on the Stock
    screen and is booked in again here would put twice the packets on the
    figure, and a page cannot tell which of the two the shopkeeper did.
    """
    try:
        body = await _json_body(request)
        sku_id = _sku_id(body)
        rec = _known_sku(sku_id)
        units = _whole_units(body)
        today = _today()
        expires_on = _expires_on(body, today)
        note = _note(body)
        book_in = _flag(body, "stock_in")
        name = str(rec.get("name") or sku_id)

        batch_id = "bt_" + secrets.token_hex(6)
        movement_id: Optional[str] = None
        stock_error: Optional[str] = None
        if book_in:
            movement_id, stock_error = _stock_move(
                "in", sku_id, units, STOCK_IN_REASON,
                f"expiry batch {batch_id}, dated {expires_on}"
                + (f" — {note}" if note else ""),
                name)

        at = _now_iso()
        try:
            head = _append(
                EV_BATCH, ts=at, batch_id=batch_id, sku_id=sku_id, name=name,
                units=units, expires_on=expires_on, note=note,
                stock_in_requested=book_in,
                stock_movement_id=movement_id,
                stock_recorded=movement_id is not None,
                stock_error=stock_error)
        except ExpiryRefused:
            # The stock line is already on the other chain. Reverse it, so the
            # Stock screen is not left showing a delivery this module has no
            # record of, then refuse the whole thing.
            if movement_id is not None:
                _stock_move("out", sku_id, units, STOCK_REVERSAL_REASON,
                            f"reversing {movement_id}: the expiry batch could "
                            f"not be recorded", name)
            raise

        row, _payload = _find(batch_id)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            **row,
            "chain_head": head,
            "stock_in_requested": book_in,
            "stock_in_recorded": movement_id is not None,
            "stock_in_error": stock_error,
            "stock_figure_needs_recount": bool(book_in and movement_id is None),
            "detail": (
                f"{units} × {name} booked, {_days_sentence(row['days_left'])} "
                f"({expires_on}). "
                + (f"A stock IN of {units} (delivery) is on the stock log as "
                   f"{movement_id}, so the shelf figure has gone up by {units}."
                   if movement_id is not None else
                   f"The stock log was not written ({stock_error}); the shelf "
                   f"figure on the Stock screen has not moved. Count that "
                   f"shelf again or record the delivery there."
                   if book_in else
                   "Nothing was written to the stock log: this is a note about "
                   "a date, not a delivery. Record the delivery on the Stock "
                   "screen if you have not.")),
        })
    except ExpiryRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(ExpiryRefused(
            R_CATALOGUE_UNAVAILABLE,
            f"a price in this shop's catalogue is not integer paise ({exc})."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


async def _take_units(request: Request, row: dict[str, Any], verb: str
                      ) -> tuple[int, Optional[str]]:
    """The units for a write-off or a sale: the batch's remainder unless the
    body says fewer. More than the remainder is refused by name, because a
    write-off of 12 against a batch with 8 left is 4 packets the shop never
    had — and a stock OUT for them would take 4 off a shelf that never held
    them."""
    body = await _json_body(request, allow_empty=True)
    remaining = int(row["units_remaining"])
    if row["state"] == "closed":
        raise ExpiryRefused(
            R_BATCH_CLOSED,
            f"batch {row['batch_id']} has nothing left on it: {row['units']} "
            f"booked, {row['written_off_units']} written off, "
            f"{row['sold_units']} sold. Nothing was recorded.")
    units = _whole_units(body) if "units" in body else remaining
    if units > remaining:
        raise ExpiryRefused(
            R_UNITS_OVER_REMAINING,
            f"{units} to be {verb}, but batch {row['batch_id']} has only "
            f"{remaining} left on it. Nothing was recorded.")
    return units, _note(body)


@router.post("/expiry/batch/{batch_id}/write-off")
async def expiry_write_off_ep(batch_id: str, request: Request) -> JSONResponse:
    """It went off. Body: {units?, note?} — units defaults to what is left.

    Appends a stock OUT with reason `expiry` through stock.py's own writer and
    THEN records the write-off here. If this module's own line then cannot be
    written, the stock line is reversed with a `correction` and the request is
    refused, so the two logs do not disagree about whether it happened.
    """
    try:
        bid = _valid_batch_id(batch_id)
        row, _payload = _find(bid)
        units, note = await _take_units(request, row, "written off")
        name = str(row["name"])

        movement_id, stock_error = _stock_move(
            "out", row["sku_id"], units, STOCK_OUT_REASON,
            f"expiry batch {bid}, dated {row['expires_on']}"
            + (f" — {note}" if note else ""),
            name)

        price = row["price_paise"]
        value: Optional[int] = (None if price is None
                                else int(paise(units * int(price))))

        at = _now_iso()
        try:
            head = _append(
                EV_WRITE_OFF, ts=at, batch_id=bid, sku_id=row["sku_id"],
                name=name, units=units, note=note,
                expires_on=row["expires_on"],
                stock_movement_id=movement_id,
                stock_recorded=movement_id is not None,
                stock_error=stock_error,
                value_paise=value)
        except ExpiryRefused:
            if movement_id is not None:
                _stock_move("in", row["sku_id"], units, STOCK_REVERSAL_REASON,
                            f"reversing {movement_id}: the expiry write-off "
                            f"could not be recorded", name)
            raise

        after, _payload = _find(bid)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            **after,
            "written_off_now": units,
            "written_off_value_paise": value,
            "written_off_value_rupees": (None if value is None
                                         else to_rupees_str(paise(value))),
            "chain_head": head,
            "stock_movement_id": movement_id,
            "stock_recorded": movement_id is not None,
            "stock_error": stock_error,
            "stock_figure_needs_recount": movement_id is None,
            "detail": (
                f"{units} × {name} written off as expired. "
                + (f"A stock OUT of {units} with reason 'expiry' is on the "
                   f"stock log as {movement_id}, so the shelf figure on the "
                   f"Stock screen has come down by {units}."
                   if movement_id is not None else
                   f"The stock log was NOT written ({stock_error}), so the "
                   f"shelf figure on the Stock screen has not moved: count "
                   f"that shelf again.")
                + (f" At its marked price that is "
                   f"{to_rupees_str(paise(value))} — a description of what "
                   f"the packets were marked at, not a charge and not a loss "
                   f"the books record."
                   if value is not None else
                   " This product has no price in the catalogue, so no value "
                   "is stated for it.")),
        })
    except ExpiryRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/expiry/batch/{batch_id}/sold")
async def expiry_sold_ep(batch_id: str, request: Request) -> JSONResponse:
    """It sold through before the date. Body: {units?, note?}.

    Nothing is written to the stock log, on purpose: a sale left through the
    counter and is already on the audit chain, and manage.py already subtracts
    it. Recording it here as a movement would take the same packet off the
    shelf twice. This line only takes the units off the BATCH, so the batch
    stops appearing on the expiring list.
    """
    try:
        bid = _valid_batch_id(batch_id)
        row, _payload = _find(bid)
        units, note = await _take_units(request, row, "marked sold")
        at = _now_iso()
        head = _append(
            EV_SOLD, ts=at, batch_id=bid, sku_id=row["sku_id"],
            name=row["name"], units=units, note=note,
            expires_on=row["expires_on"])
        after, _payload = _find(bid)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            **after,
            "sold_now": units,
            "chain_head": head,
            "stock_recorded": False,
            "stock_figure_needs_recount": False,
            "detail": (
                f"{units} × {row['name']} marked sold through before "
                f"{row['expires_on']}. Nothing was written to the stock log: "
                f"the sales are already on the audit chain and are subtracted "
                f"there. "
                + (f"{after['units_remaining']} left on this batch."
                   if after["units_remaining"] > 0 else
                   "This batch is closed.")),
        })
    except ExpiryRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


__all__ = [
    "ExpiryRefused",
    "audit_path",
    "batches_from",
    "read_events",
    "router",
    "rows",
    "shop_dir",
    "stock_link",
]
