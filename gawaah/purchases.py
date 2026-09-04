"""KHAREED — who the shop buys from, what it paid, and what it therefore makes.

The till knows what a packet SELLS for. Until this file existed it did not know
what the shopkeeper PAID for it, so the one number he actually runs the shop on
— the margin — could not be shown at all. A counter that reports ten thousand
rupees of takings and cannot say whether nine thousand of it was somebody else's
stock is a cash register, not a book.

So there are two records here and one derived answer:

    a SUPPLIER   a name, a phone, and notes. Nothing is paid through this
                 program and no supplier is ever contacted by it.
    a PURCHASE   a supplier, a date, an optional invoice number, and lines of
                 (sku, units, cost per unit). The TOTAL IS THE SERVER'S; a
                 client that asserts one is refused, not believed.
    the MARGIN   selling price (from the shop's own catalogue) minus the most
                 recent recorded cost price, per SKU — and, on the day view,
                 the margin on what was actually billed, taken off the same
                 hash-chained audit log the history screen walks.

THE UNKNOWN MARGIN IS THE WHOLE POINT
=====================================
Where no cost price has been recorded for a product, its margin is NOT zero and
it is NOT the selling price. It is UNKNOWN, and every response here says so by
name: `cost_known: false`, a null margin, and the SKU listed in `unknown`. A day
total is split into what is covered by a recorded cost and what is not, and
`margin_is_partial` is true whenever anything is uncovered.

That matters because the tempting bug is to treat a missing cost as nought,
which reports a shop making 100% on everything it has never entered a bill for —
a number that is both wrong and flattering, which is the worst combination
available. Nothing here sums an unknown into a total.

WHAT THE COST NUMBERS ARE, AND ARE NOT
======================================
The cost used for a sale is the LAST COST RECORDED ON OR BEFORE THAT DAY. It is
not lot-level FIFO: this file does not track which physical packet came from
which invoice, so a shop that bought the same item twice in a week at two rates
gets the later of the two applied to everything sold after it. Stated rather
than implied, because the difference is real money on a fast-moving line.

Recording a purchase also does NOT adjust the opening stock a shopkeeper typed
on the inventory screen. That sidecar belongs to `gawaah/manage.py` and this
module does not write it. The purchase chain carries units per SKU, so stock-in
is derivable from it by whoever owns that screen.

RULES THIS FILE KEEPS
=====================
  1. INTEGER PAISE. Costs arrive as integer paise or as a decimal rupee STRING
     parsed by `gawaah.money.from_rupees_str`; no float and no `/` touches a
     cost, a total or a margin. Percentages are integer tenths, floored, so a
     loss is never shown smaller than it is.
  2. A REFUSAL IS A RESULT. Every failure below has a name in the body and a 400
     (404 for an id that does not exist). Nothing here raises a 500.
  3. THE BROWSER IS NEVER AN AUTHOR OF A TOTAL. It may state what it paid per
     unit — that fact is on the invoice and lives nowhere else — but line totals
     and the grand total are computed here, and an asserted one is compared and
     refused rather than quietly ignored.
  4. NO FORGERY PRIMITIVES. Nothing here mints, pays, or constructs a payable
     string. `settles_money` is false on every response, as a fact about the
     code.
  5. GAWAAH_SHOP_DIR IS HONOURED, through the till's own `store_dir()` and never
     through a second reading of the environment.
  6. ITS OWN CHAIN. Purchases append to `<shop>/purchases.audit.jsonl`, not to
     `results/audit.jsonl`, which the money service holds open as sole writer.

MOUNTING
========
The router carries NO prefix; the paths below are already absolute::

    from gawaah import purchases
    app.include_router(purchases.router)
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .ledger import Ledger
from .money import MoneyError, from_rupees_str, paise, to_rupees_str

router = APIRouter()


# --------------------------------------------------------------- refusals --
#
# Every one of these is a state this module can actually reach, named for the
# STATE and not for the fix. The sentence that says what to do goes in `detail`.

R_NO_TILL = "till_module_unavailable"
R_NO_CATALOGUE = "catalogue_unavailable"
R_BAD_BODY = "purchase_body_not_json"

R_NO_SUPPLIER_NAME = "supplier_name_missing"
R_NO_SUPPLIER_PHONE = "supplier_phone_missing"
R_BAD_SUPPLIER_PHONE = "supplier_phone_not_a_number"
R_DUPLICATE_SUPPLIER = "supplier_already_recorded"
R_BAD_SUPPLIER_ID = "supplier_id_malformed"
R_NO_SUPPLIER = "no_such_supplier"
R_TOO_LONG = "field_too_long"

R_NO_LINES = "purchase_has_no_lines"
R_TOO_MANY_LINES = "too_many_lines_in_one_purchase"
R_UNKNOWN_SKU = "sku_not_in_this_shop"
R_BAD_UNITS = "units_not_a_whole_number"
R_UNITS_TOO_LARGE = "units_beyond_this_counter"
R_BAD_COST = "cost_not_positive_integer_paise"
R_COST_DISAGREES = "cost_paise_and_cost_rupees_disagree"
R_COST_TOO_LARGE = "cost_beyond_this_counter"
R_LINE_TOTAL_DISAGREES = "client_line_total_disagrees"
R_TOTAL_DISAGREES = "client_total_disagrees"

R_BAD_DATE = "date_not_a_calendar_day"
R_FUTURE_DATE = "date_is_in_the_future"
R_BAD_INVOICE = "invoice_number_malformed"
R_DUPLICATE_INVOICE = "invoice_already_recorded"

R_BAD_PURCHASE_ID = "purchase_id_malformed"
R_NO_PURCHASE = "no_such_purchase"
R_ALREADY_VOID = "purchase_already_void"
R_NO_VOID_REASON = "void_reason_missing"

R_NO_BILLS = "audit_chain_unavailable"
R_INTERNAL = "purchases_internal_error"


# ----------------------------------------------------------------- limits --
#
# Each bounds something that ends up on disk or in a total. What it costs when
# they are wrong: a genuine wholesale run is refused and the shopkeeper splits
# the invoice. That is a nuisance. An unbounded write is not.

#: A case of shampoo sachets is a few hundred; ten thousand of one line on one
#: invoice is a typed zero too many, not a kirana purchase.
MAX_UNITS = 10_000

#: One lakh rupees for ONE UNIT. A gas cylinder is about eleven hundred. This
#: cap exists to catch a rupee figure typed into a paise field.
MAX_COST_PAISE = 10_000_000

#: A wholesaler's bill runs long. Two hundred distinct lines is generous.
MAX_LINES = 200

MAX_NAME = 80
MAX_PHONE = 24
MAX_NOTES = 400
MAX_INVOICE = 40
MAX_VOID_REASON = 200

SUPPLIER_FORMAT = 1
PURCHASE_FORMAT = 1

SUPPLIER_ID_RE = re.compile(r"^sup_[0-9a-f]{12}$")
PURCHASE_ID_RE = re.compile(r"^pur_[0-9a-f]{12}$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: An invoice number is whatever the wholesaler printed on it. Letters, digits,
#: and the four separators that actually appear on Indian invoices. The charset
#: is checked because this string is echoed back into a page and into the chain.
INVOICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 /\-_.]*$")

SUPPLIERS_SIDECAR = "suppliers.json"
PURCHASES_SUBDIR = "purchases"
AUDIT_SIDECAR = "purchases.audit.jsonl"


class PurchaseRefused(Exception):
    """A named refusal with a sentence a shopkeeper can act on."""

    def __init__(self, reason: str, detail: str, status: int = 400) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.status = status


def _refusal(exc: PurchaseRefused) -> JSONResponse:
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


# ------------------------------------------------------------- where things live --

from gawaah import till_ref as _till_ref

#: One definition, in `gawaah/till_ref.py`. This was sixteen copies of a tuple
#: that was missing `__main__`, and every one of them was wrong the same way.
_TILL_NAMES = _till_ref.TILL_NAMES


def _till() -> Any:
    """The already-loaded till module, or a named refusal.

    LOOK IN sys.modules FIRST. `make serve` runs `uvicorn upload_app:app
    --app-dir tools`, so the module is registered under the bare name
    `upload_app`; the test suite does `from tools import upload_app` and
    registers `tools.upload_app`. Importing the other spelling loads a SECOND
    copy of the file with its own cached store handle — a second catalogue
    directory, and a `set_store_dir` in a test that silently does not reach the
    copy serving requests. The symptom would be purchases filed against a
    different shop than the till is selling from, with nothing saying so.

    Not imported at module scope: the till mounts this router, so that would be
    a cycle, and the till drags in the whole vision stack.
    """
    import sys

    for name in _TILL_NAMES:
        mod = sys.modules.get(name)
        if mod is not None and _till_ref.is_the_till(mod):
            return mod
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from tools import upload_app  # noqa: WPS433 - deliberately late
    except Exception as exc:  # noqa: BLE001 - a missing till is a named answer
        raise PurchaseRefused(
            R_NO_TILL,
            f"tools/upload_app.py is not importable ({type(exc).__name__}: "
            f"{exc}). Purchases are filed beside the catalogue they price "
            f"against, and this module will not keep a second copy of it."
        ) from None
    return upload_app


def shop_dir() -> Path:
    """Where the catalogue lives — the till's own answer, never a second one.

    This is what honours `GAWAAH_SHOP_DIR`: `upload_app.store_dir()` reads that
    variable and `set_store_dir()` redirects it for a test. Reading the
    environment here would be a second answer to one question, and a harness
    that got a different answer from the till once destroyed a live catalogue.
    """
    return Path(_till().store_dir())


def purchases_dir() -> Path:
    """Purchases live NEXT TO the catalogue they are matched against."""
    return shop_dir() / PURCHASES_SUBDIR


def suppliers_path() -> Path:
    return purchases_dir() / SUPPLIERS_SIDECAR


def audit_path() -> Path:
    """This module's own hash-chained log.

    DELIBERATELY NOT `results/audit.jsonl`. That file is held open by the money
    service in a DIFFERENT PROCESS, which keeps the chain head in memory and
    computes `prev_hash` from it. A second process appending between two of its
    writes gives it a stale head, every line paisa writes afterwards fails
    `gawaah.ledger.verify`, and the money audit trail is the casualty.

    What it costs when this is right: there are two chains to walk, and a reader
    who checks only the money chain will not see the purchases. That is a
    documentation problem. The alternative was a corrupted money ledger.
    """
    return shop_dir() / AUDIT_SIDECAR


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _audit(event: str, **fields: Any) -> Optional[str]:
    """Append one auditable line. Returns the new head, or None if it failed.

    THE SUPPLIER'S PHONE NEVER REACHES THIS FILE, and neither do the notes. The
    supplier's NAME does, because a purchase record that cannot say who was paid
    is not an audit of anything; the phone rides as a digest so a changed number
    is provable without the number being in the file most likely to be pasted
    into a bug report.

    Best effort, but never silent: a caller that gets None says so in its
    response rather than reporting a witnessed purchase that was not.
    """
    try:
        return Ledger(audit_path()).append(
            ts=_now_iso(), module="purchases", event=event, **fields)
    except Exception:  # noqa: BLE001 - a failed audit must not lose a purchase
        return None


def _write_json(path: Path, doc: Any) -> None:
    """Write via a temp file and rename, so a reader never sees half a record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, sort_keys=True, indent=1) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a named refusal
        raise PurchaseRefused(
            R_BAD_BODY, "this request's body is not JSON.") from None
    if not isinstance(body, dict):
        raise PurchaseRefused(
            R_BAD_BODY,
            f"this request's body is a {type(body).__name__}; it must be a "
            f"JSON object.")
    return body


def _text(body: dict[str, Any], key: str, *, cap: int, keep_lines: bool = False
          ) -> str:
    raw = body.get(key)
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise PurchaseRefused(
            R_BAD_BODY, f"{key!r} must be text, not {type(raw).__name__}.")
    s = raw.strip() if keep_lines else " ".join(raw.split())
    if len(s) > cap:
        raise PurchaseRefused(
            R_TOO_LONG,
            f"{key} is {len(s)} characters and the cap is {cap}. Nothing was "
            f"saved.")
    return s


# ------------------------------------------------------------------ days --


def _today_label() -> str:
    """Today in the COUNTER'S OWN timezone.

    The chain stamps UTC. A shopkeeper's day does not start at 05:30, and
    answering "what did I make today" with a UTC window quietly moves last
    evening into tomorrow.
    """
    return _dt.datetime.now().astimezone().strftime("%Y-%m-%d")


def _valid_day(day: Any) -> str:
    if not isinstance(day, str) or not DAY_RE.match(day.strip()):
        raise PurchaseRefused(
            R_BAD_DATE,
            f"{day!r} is not a calendar day. Write it as YYYY-MM-DD, for "
            f"example 2026-09-01.")
    day = day.strip()
    try:
        _dt.date.fromisoformat(day)
    except ValueError:
        raise PurchaseRefused(
            R_BAD_DATE,
            f"{day!r} is not a day that exists. Write it as YYYY-MM-DD.") from None
    return day


def _day_bounds(label: str) -> tuple[_dt.datetime, _dt.datetime]:
    """Midnight to midnight, in the counter's own timezone."""
    tz = _dt.datetime.now().astimezone().tzinfo
    start = _dt.datetime.strptime(label, "%Y-%m-%d").replace(tzinfo=tz)
    return start, start + _dt.timedelta(days=1)


def _parse_ts(value: Any) -> Optional[_dt.datetime]:
    """An ISO-8601 stamp as the ledger writes them, or None.

    Written here rather than borrowed from `gawaah/manage.py` so that this
    module depends on two public functions of that file and nothing private.
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


# ------------------------------------------------------------- catalogue --


def catalogue() -> dict[str, dict[str, Any]]:
    """{sku_id -> name, price_paise, ...} for everything this shop can sell.

    `offer_priced_skus()` and not `priced_skus()`, because the margin that
    matters is the margin on what the customer is ACTUALLY CHARGED. A shop
    running 10% off on soap does not make the shelf-edge margin on soap that
    day, and reporting the shelf-edge figure would overstate the takings by
    exactly the discount the shopkeeper chose to give. The marked price rides
    alongside as `marked_paise` so both are visible.
    """
    up = _till()
    try:
        fn = getattr(up, "offer_priced_skus", None) or up.priced_skus
        return dict(fn())
    except PurchaseRefused:
        raise
    except Exception as exc:  # noqa: BLE001 - the store may be unreadable
        reason = getattr(exc, "reason", None) or R_NO_CATALOGUE
        detail = getattr(exc, "detail", None) or (
            f"the catalogue could not be read ({type(exc).__name__}: {exc})")
        raise PurchaseRefused(reason, detail) from None


# ------------------------------------------------------------- suppliers --


def _load_suppliers() -> dict[str, dict[str, Any]]:
    """Every supplier, keyed by id. An unreadable sidecar is empty, not fatal.

    A management record that raises on a hand-edited file shows the shopkeeper
    nothing at the one moment he needs to look at it. Nothing here is money.
    """
    try:
        doc = json.loads(suppliers_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:  # noqa: BLE001 - a corrupt sidecar is "none yet"
        return {}
    if not isinstance(doc, dict):
        return {}
    rows = doc.get("suppliers")
    if not isinstance(rows, dict):
        return {}
    return {k: v for k, v in rows.items()
            if isinstance(k, str) and isinstance(v, dict)}


def _save_suppliers(rows: dict[str, dict[str, Any]]) -> None:
    _write_json(suppliers_path(),
                {"format": SUPPLIER_FORMAT, "suppliers": rows})


def _valid_supplier_id(supplier_id: Any) -> str:
    s = (supplier_id or "").strip() if isinstance(supplier_id, str) else ""
    if not SUPPLIER_ID_RE.match(s):
        raise PurchaseRefused(
            R_BAD_SUPPLIER_ID,
            f"{supplier_id!r} is not a supplier id from this shop. They look "
            f"like 'sup_' followed by twelve hex characters.")
    return s


def _supplier(rows: dict[str, dict[str, Any]], supplier_id: Any
              ) -> dict[str, Any]:
    sid = _valid_supplier_id(supplier_id)
    rec = rows.get(sid)
    if not isinstance(rec, dict):
        raise PurchaseRefused(
            R_NO_SUPPLIER,
            f"this shop has no supplier {sid}. Nothing was changed.",
            status=404)
    return rec


def _supplier_fields(body: dict[str, Any], *, existing: Optional[dict] = None
                     ) -> dict[str, str]:
    """Name, phone and notes, checked.

    THE PHONE IS REQUIRED. A supplier a shopkeeper cannot ring is not a supplier
    he can chase a short delivery with, and the number is the only thing that
    reliably distinguishes two wholesalers with the same family name. What it
    costs when this is wrong: a walk-in cash supplier with no number cannot be
    recorded and the purchase has to go against somebody else.
    """
    # A field the caller did not mention keeps what is on file. A field the
    # caller sent EMPTY is an attempt to blank it, and blanking a required
    # field is refused rather than quietly ignored — silently keeping the old
    # value would show the shopkeeper a save that did not do what he asked.
    if "name" in body or existing is None:
        name = _text(body, "name", cap=MAX_NAME)
    else:
        name = str(existing.get("name") or "")
    if not name:
        raise PurchaseRefused(
            R_NO_SUPPLIER_NAME,
            "a supplier needs a name — it is what the shopkeeper will look for "
            "on the list. Nothing was saved.")

    if "phone" in body or existing is None:
        phone = _text(body, "phone", cap=MAX_PHONE)
    else:
        phone = str(existing.get("phone") or "")
    if not phone:
        raise PurchaseRefused(
            R_NO_SUPPLIER_PHONE,
            "a supplier needs a phone number. It is how a short delivery gets "
            "chased. Nothing was saved.")
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 7:
        raise PurchaseRefused(
            R_BAD_SUPPLIER_PHONE,
            f"{phone!r} has {len(digits)} digits in it. A number that can be "
            f"dialled has at least seven.")

    if "notes" in body or existing is None:
        notes = _text(body, "notes", cap=MAX_NOTES, keep_lines=True)
    else:
        notes = str(existing.get("notes") or "")
    return {"name": name, "phone": phone, "notes": notes}


def _same_supplier(a: str, b: str) -> bool:
    """Two supplier names that a human would call the same one.

    Case and inner spacing only. Deliberately NOT fuzzy: refusing 'Sharma
    Traders' because 'Sharma Trading' exists would block a real second supplier,
    and this check exists to catch the same name typed twice, not to guess.
    """
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def _supplier_view(sid: str, rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "supplier_id": sid,
        "name": rec.get("name"),
        "phone": rec.get("phone"),
        "notes": rec.get("notes") or "",
        "at": rec.get("at"),
        "updated_at": rec.get("updated_at"),
    }


# ------------------------------------------------------------- purchases --


def _valid_purchase_id(purchase_id: Any) -> str:
    """Checked against a strict charset BEFORE it is ever joined to a path.

    The id becomes a filename. A shape check here is what stops a request for
    `../../catalog` reading the shopkeeper's price list.
    """
    s = (purchase_id or "").strip() if isinstance(purchase_id, str) else ""
    if not PURCHASE_ID_RE.match(s):
        raise PurchaseRefused(
            R_BAD_PURCHASE_ID,
            f"{purchase_id!r} is not a purchase id from this shop. They look "
            f"like 'pur_' followed by twelve hex characters.")
    return s


def _purchase_path(purchase_id: str) -> Path:
    return purchases_dir() / f"{_valid_purchase_id(purchase_id)}.json"


def _read_purchase(purchase_id: str) -> dict[str, Any]:
    p = _purchase_path(purchase_id)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PurchaseRefused(
            R_NO_PURCHASE,
            f"this shop has no purchase {purchase_id!r}. Nothing was changed.",
            status=404) from None
    except Exception as exc:  # noqa: BLE001 - a corrupt file is not a crash
        raise PurchaseRefused(
            R_NO_PURCHASE,
            f"purchase {purchase_id!r} is on disk but could not be read "
            f"({type(exc).__name__}: {exc}). Nothing was changed.",
            status=404) from None
    if not isinstance(doc, dict):
        raise PurchaseRefused(
            R_NO_PURCHASE,
            f"purchase {purchase_id!r} is not a purchase document.", status=404)
    return doc


def _all_purchases() -> list[dict[str, Any]]:
    """Every purchase, newest first. An unreadable file is skipped, not fatal.

    Sorted on (date, at, id): the DATE is the shopkeeper's word about when the
    stock arrived and `at` is when he typed it in, so an invoice entered late
    still files under the day it belongs to. The id is the tiebreak so two
    purchases recorded in the same microsecond have a stable order rather than
    a filesystem-dependent one.
    """
    d = purchases_dir()
    out: list[dict[str, Any]] = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("pur_*.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - one bad file must not hide the rest
            continue
        if isinstance(doc, dict) and doc.get("purchase_id"):
            out.append(doc)
    out.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("at") or ""),
                            str(r.get("purchase_id") or "")), reverse=True)
    return out


def _live(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The purchases that still count. A voided one is kept but not counted."""
    return [r for r in rows if not r.get("void")]


def _cost_history() -> dict[str, list[dict[str, Any]]]:
    """{sku_id -> what it cost, oldest first}, from the purchase files alone.

    Derived on every read rather than kept as a running index, for the reason
    `gawaah/manage.py` gives about bills: a second store is a second truth, and
    the first time it disagreed with the files there would be no way to tell
    which one was lying. A kirana's purchase count is in the hundreds.

    Within ONE purchase document the same SKU may appear twice — a wholesaler's
    invoice can carry two lots of the same item at two rates — and both are
    kept, in document order, so the later line is the later cost.
    """
    hist: dict[str, list[dict[str, Any]]] = {}
    for doc in reversed(_live(_all_purchases())):     # oldest first
        lines = doc.get("lines")
        if not isinstance(lines, list):
            continue
        for ln in lines:
            if not isinstance(ln, dict):
                continue
            sku = ln.get("sku_id")
            cost = ln.get("cost_paise")
            if not isinstance(sku, str) or not sku:
                continue
            if isinstance(cost, bool) or not isinstance(cost, int):
                continue
            hist.setdefault(sku, []).append({
                "cost_paise": int(cost),
                "cost_rupees": to_rupees_str(int(cost)),
                "units": int(ln.get("units") or 0),
                "date": str(doc.get("date") or ""),
                "at": str(doc.get("at") or ""),
                "purchase_id": str(doc.get("purchase_id") or ""),
                "supplier_id": doc.get("supplier_id"),
                "supplier_name": doc.get("supplier_name"),
                "invoice_no": doc.get("invoice_no"),
            })
    return hist


def _cost_as_of(hist: dict[str, list[dict[str, Any]]], sku: str,
                day: Optional[str]) -> Optional[dict[str, Any]]:
    """The last cost recorded on or before `day`, or the last one ever.

    NOT lot-level FIFO — see the module docstring. `day` is a YYYY-MM-DD label
    and the stored dates are the same shape, so a string comparison IS a date
    comparison here and needs no timezone.
    """
    rows = hist.get(sku) or []
    if day is None:
        return rows[-1] if rows else None
    keep = [r for r in rows if str(r.get("date") or "") <= day]
    return keep[-1] if keep else None


# ---------------------------------------------------------------- margin --
#
# Everything below is integer arithmetic. A percentage is expressed in TENTHS of
# a percent as an int and rendered by `_tenths`, because the alternative — a
# float — is banned in this program for a reason that applies here too: nobody
# needs a margin quoted to fourteen decimal places, and the moment a float is in
# the room somebody divides by a hundred.


def _tenths(value: int) -> str:
    """An integer number of tenths as a plain string: 332 -> '33.2'."""
    sign = "-" if value < 0 else ""
    value = abs(int(value))
    return f"{sign}{value // 10}.{value % 10}"


def _margin_block(sell: Optional[int], cost: Optional[int]) -> dict[str, Any]:
    """The margin on one unit, or an honest statement that it is not known.

    THE UNKNOWN CASE IS NOT ZERO. Where there is no recorded cost, every figure
    below is null and `cost_known` is false. Treating a missing cost as nought
    would report the shop making its entire selling price as profit on every
    product it has never entered a bill for.

    Both percentages name their base. "Margin" and "markup" are different
    numbers off the same two figures — 25 on a 100 sale is a 25% margin and a
    33.3% markup — and a bare `margin_pct` would leave a shopkeeper reading
    whichever one he happened to expect.
    """
    if cost is None or sell is None:
        return {
            "cost_known": cost is not None,
            "margin_paise": None,
            "margin_rupees": None,
            "margin_pct_of_price": None,
            "markup_pct_of_cost": None,
            "below_cost": None,
            "note": ("no cost price has been recorded for this product, so "
                     "what it earns is not known. It is not zero and it is not "
                     "the whole selling price."
                     if cost is None else
                     "this product has no selling price in the catalogue, so "
                     "what it earns is not known."),
        }
    margin = int(paise(sell)) - int(paise(cost))
    # Floored, and floored deliberately: for a loss that means the percentage
    # shown is never smaller than the loss actually is.
    pct_sale = ((margin * 1000) // sell) if sell > 0 else None
    pct_cost = ((margin * 1000) // cost) if cost > 0 else None
    return {
        "cost_known": True,
        "margin_paise": margin,
        "margin_rupees": to_rupees_str(paise(margin)),
        "margin_pct_of_price": None if pct_sale is None else _tenths(pct_sale),
        "markup_pct_of_cost": None if pct_cost is None else _tenths(pct_cost),
        "below_cost": margin < 0,
        "note": ("this product is being sold for less than the last price the "
                 "shop paid for it" if margin < 0 else None),
    }


def _margin_row(sku_id: str, rec: Optional[dict[str, Any]],
                cost_row: Optional[dict[str, Any]]) -> dict[str, Any]:
    """One product's line on the margin screen."""
    sell: Optional[int] = None
    if rec is not None:
        sell = int(paise(rec["price_paise"]))
    cost = None if cost_row is None else int(cost_row["cost_paise"])
    row: dict[str, Any] = {
        "sku_id": sku_id,
        "name": str((rec or {}).get("name") or sku_id),
        "still_in_catalogue": rec is not None,
        "sell_paise": sell,
        "sell_rupees": None if sell is None else to_rupees_str(paise(sell)),
        "cost_paise": cost,
        "cost_rupees": None if cost is None else to_rupees_str(paise(cost)),
        "cost_recorded_on": None if cost_row is None else cost_row.get("date"),
        "cost_from": None if cost_row is None else {
            "purchase_id": cost_row.get("purchase_id"),
            "supplier_id": cost_row.get("supplier_id"),
            "supplier_name": cost_row.get("supplier_name"),
            "invoice_no": cost_row.get("invoice_no"),
        },
    }
    # An offer changes what is actually charged, so it changes the margin. Show
    # the shelf-edge price beside it rather than letting the two look like a
    # pricing mistake.
    marked = (rec or {}).get("marked_paise")
    if not isinstance(marked, bool) and isinstance(marked, int) \
            and sell is not None and marked > sell:
        row["marked_paise"] = int(paise(marked))
        row["marked_rupees"] = to_rupees_str(int(paise(marked)))
        row["on_offer"] = True
    row.update(_margin_block(sell, cost))
    return row


# ---------------------------------------------------------------- reading --


def _one_line(raw: Any, known: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """One line of an invoice, validated and priced by the SERVER.

    The client states the SKU, the count and what it paid per unit. That last
    one is a fact off a piece of paper the shopkeeper is holding and it exists
    nowhere else in this program, so it is accepted as input — but the line
    total and the invoice total are computed here, and an asserted one is
    compared and refused.
    """
    if not isinstance(raw, dict):
        raise PurchaseRefused(
            R_BAD_BODY,
            f"every purchase line must be an object with a sku_id, units and a "
            f"cost; found {type(raw).__name__}.")

    sku_id = raw.get("sku_id")
    if not isinstance(sku_id, str) or not sku_id.strip():
        raise PurchaseRefused(
            R_BAD_BODY, "a purchase line arrived with no sku_id.")
    sku_id = sku_id.strip()
    rec = known.get(sku_id)
    if rec is None:
        raise PurchaseRefused(
            R_UNKNOWN_SKU,
            f"{sku_id!r} is not a product this shop has been taught, so there "
            f"is nothing to compare a cost against. Teach it on the Products "
            f"screen first. Nothing was saved. This shop knows: "
            f"{', '.join(sorted(known)[:6]) or 'nothing yet'}"
            f"{'…' if len(known) > 6 else ''}.")

    units = raw.get("units")
    # bool first: True is an int in Python and one unit of True is not a thing
    # anybody meant.
    if isinstance(units, bool) or not isinstance(units, int):
        raise PurchaseRefused(
            R_BAD_UNITS,
            f"the units for {sku_id!r} are {units!r}. A count of packets is a "
            f"whole number.")
    if units <= 0:
        raise PurchaseRefused(
            R_BAD_UNITS,
            f"the units for {sku_id!r} are {units}. To leave a product off an "
            f"invoice, leave the line out.")
    if units > MAX_UNITS:
        raise PurchaseRefused(
            R_UNITS_TOO_LARGE,
            f"{units} of {sku_id!r} is past the {MAX_UNITS} this counter "
            f"records on one line. Split the invoice.")

    cost = _cost_of(raw, sku_id)
    line = cost * units

    claimed = raw.get("line_paise")
    if claimed is not None:
        if isinstance(claimed, bool) or not isinstance(claimed, int) \
                or claimed != line:
            raise PurchaseRefused(
                R_LINE_TOTAL_DISAGREES,
                f"this line says {sku_id!r} comes to {claimed!r}; "
                f"{units} × {cost} paise is {line}. Nothing was saved.")

    return {
        "sku_id": sku_id,
        "name": str(rec.get("name") or sku_id),
        "units": units,
        "cost_paise": cost,
        "cost_rupees": to_rupees_str(paise(cost)),
        "line_paise": line,
        "line_rupees": to_rupees_str(paise(line)),
    }


def _cost_of(raw: dict[str, Any], sku_id: str) -> int:
    """What one unit cost, as integer paise, from whichever field was sent.

    Two spellings because two callers exist: a form where a shopkeeper types
    '21.50' sends `cost_rupees` as a STRING — never a float, `float('21.50')` is
    already lossy — and a machine sends `cost_paise` as an int. If both arrive
    and they disagree, that is refused rather than one of them being picked:
    picking would mean the number stored is not the one somebody checked.
    """
    p = raw.get("cost_paise")
    r = raw.get("cost_rupees")

    from_int: Optional[int] = None
    if p is not None:
        if isinstance(p, bool) or not isinstance(p, int):
            raise PurchaseRefused(
                R_BAD_COST,
                f"the cost for {sku_id!r} is {p!r}. Money here is integer "
                f"paise: 21.45 rupees is 2145, and a fraction of a paisa is "
                f"not a price anyone can be charged.")
        from_int = int(paise(p))

    from_str: Optional[int] = None
    if r is not None:
        if not isinstance(r, str):
            raise PurchaseRefused(
                R_BAD_COST,
                f"'cost_rupees' for {sku_id!r} must be a string like '21.50', "
                f"not {type(r).__name__}. A rupee is not a float.")
        try:
            from_str = int(from_rupees_str(r))
        except MoneyError as exc:
            raise PurchaseRefused(
                R_BAD_COST,
                f"the cost for {sku_id!r} could not be read as rupees "
                f"({exc}).") from None

    if from_int is not None and from_str is not None and from_int != from_str:
        raise PurchaseRefused(
            R_COST_DISAGREES,
            f"this line gives {sku_id!r} both {from_int} paise and "
            f"{r!r} rupees, which are different numbers. Send one of them.")

    cost = from_int if from_int is not None else from_str
    if cost is None:
        raise PurchaseRefused(
            R_BAD_COST,
            f"there is no cost on the line for {sku_id!r}. Send 'cost_paise' "
            f"as whole paise or 'cost_rupees' as a string like '21.50'.")
    if cost <= 0:
        raise PurchaseRefused(
            R_BAD_COST,
            f"the cost for {sku_id!r} is {cost} paise. A cost of nothing would "
            f"make this product look like pure profit for as long as it is the "
            f"most recent one recorded. Free stock belongs on the invoice at "
            f"the rate printed on it, or off the record entirely.")
    if cost > MAX_COST_PAISE:
        raise PurchaseRefused(
            R_COST_TOO_LARGE,
            f"{cost} paise for one {sku_id!r} is past the "
            f"{MAX_COST_PAISE} this counter records. If that was rupees, it "
            f"belongs in 'cost_rupees' as a string.")
    return cost


def _sum_paise(lines: list[dict[str, Any]]) -> int:
    """Integer addition, and nothing else. No float, no division, no rounding."""
    out = 0
    for ln in lines:
        out += int(paise(ln["line_paise"]))
    return int(paise(out))


def _invoice_no(body: dict[str, Any]) -> str:
    raw = _text(body, "invoice_no", cap=MAX_INVOICE)
    if not raw:
        return ""
    if not INVOICE_RE.match(raw):
        raise PurchaseRefused(
            R_BAD_INVOICE,
            f"{raw!r} is not an invoice number this counter will file. Letters, "
            f"digits, spaces and / - _ . only, starting with a letter or a "
            f"digit.")
    return raw


# ----------------------------------------------------------------- routes --
#
# ORDER MATTERS. FastAPI matches in declaration order, so every fixed path under
# /purchases is declared BEFORE /purchases/{purchase_id}; otherwise a request
# for /purchases/margin arrives at the id handler and is refused as a malformed
# id, which is a true sentence about the wrong thing.


@router.get("/purchases/suppliers")
def suppliers_ep() -> JSONResponse:
    """Everyone this shop buys from, and what has been bought from each."""
    try:
        rows = _load_suppliers()
        spend: dict[str, int] = {}
        counts: dict[str, int] = {}
        for doc in _live(_all_purchases()):
            sid = str(doc.get("supplier_id") or "")
            if not sid:
                continue
            spend[sid] = spend.get(sid, 0) + int(doc.get("total_paise") or 0)
            counts[sid] = counts.get(sid, 0) + 1
        out = []
        for sid in sorted(rows, key=lambda k: str(rows[k].get("name") or "")):
            view = _supplier_view(sid, rows[sid])
            bought = int(paise(spend.get(sid, 0)))
            view["purchases"] = counts.get(sid, 0)
            view["bought_paise"] = bought
            view["bought_rupees"] = to_rupees_str(paise(bought))
            out.append(view)
        return JSONResponse({
            "ok": True, "settles_money": False,
            "count": len(out), "suppliers": out,
            "note": ("What was bought is the sum of purchases recorded here. "
                     "Nothing in this program pays a supplier or knows whether "
                     "one has been paid."),
        })
    except PurchaseRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


def add_supplier(body: dict[str, Any]) -> dict[str, Any]:
    """THE supplier writer. Body: {name, phone, notes?}. Raises PurchaseRefused.

    Lifted out of the endpoint so that `gawaah/parchi.py` — which reads a
    supplier's name off a photographed invoice — files the supplier through
    this one function rather than growing a second copy of the sidecar write.
    Returns {"supplier": view, "audited": bool}.
    """
    fields = _supplier_fields(body)
    rows = _load_suppliers()
    for sid, rec in rows.items():
        if _same_supplier(str(rec.get("name") or ""), fields["name"]):
            raise PurchaseRefused(
                R_DUPLICATE_SUPPLIER,
                f"this shop already has a supplier called "
                f"{rec.get('name')!r} ({sid}). Two rows with one name make "
                f"the purchase list unreadable — edit that one, or give "
                f"this one a name that tells them apart.")

    now = _now_iso()
    sid = "sup_" + secrets.token_hex(6)
    rows[sid] = {**fields, "supplier_id": sid, "at": now,
                 "updated_at": now}
    _save_suppliers(rows)
    head = _audit("supplier.added", supplier_id=sid, name=fields["name"],
                  phone_sha256=hashlib.sha256(
                      fields["phone"].encode("utf-8")).hexdigest(),
                  minted=False)
    return {"supplier": _supplier_view(sid, rows[sid]),
            "audited": head is not None}


def find_supplier(name: str) -> Optional[dict[str, Any]]:
    """The supplier on file whose name a human would call the same as `name`
    (case and inner spacing only — the rule `_same_supplier` keeps), as its
    view, or None. Read-only; used by the photographed-invoice flow to say
    "this bill is from somebody already on the list" without guessing."""
    for sid, rec in _load_suppliers().items():
        if _same_supplier(str(rec.get("name") or ""), name):
            return _supplier_view(sid, rec)
    return None


@router.post("/purchases/suppliers")
async def supplier_add_ep(request: Request) -> JSONResponse:
    """Add a supplier. Body: {name, phone, notes?}."""
    try:
        body = await _json_body(request)
        out = add_supplier(body)
        return JSONResponse({"ok": True, "settles_money": False, **out})
    except PurchaseRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/purchases/suppliers/{supplier_id}")
def supplier_one_ep(supplier_id: str) -> JSONResponse:
    """One supplier, and every purchase filed against them."""
    try:
        rows = _load_suppliers()
        sid = _valid_supplier_id(supplier_id)
        rec = _supplier(rows, sid)
        mine = [d for d in _all_purchases() if d.get("supplier_id") == sid]
        live = _live(mine)
        bought = 0
        for d in live:
            bought += int(paise(d.get("total_paise") or 0))
        return JSONResponse({
            "ok": True, "settles_money": False,
            "supplier": _supplier_view(sid, rec),
            "purchases": mine,
            "count": len(mine),
            "void_count": len(mine) - len(live),
            "bought_paise": bought,
            "bought_rupees": to_rupees_str(paise(bought)),
        })
    except PurchaseRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/purchases/suppliers/{supplier_id}")
async def supplier_edit_ep(supplier_id: str, request: Request) -> JSONResponse:
    """Correct a supplier's name, phone or notes.

    Purchases keep the supplier NAME as it stood when they were filed, so an
    edit does not rewrite history. The id is what joins them.
    """
    try:
        body = await _json_body(request)
        rows = _load_suppliers()
        sid = _valid_supplier_id(supplier_id)
        rec = _supplier(rows, sid)
        fields = _supplier_fields(body, existing=rec)
        for other_id, other in rows.items():
            if other_id == sid:
                continue
            if _same_supplier(str(other.get("name") or ""), fields["name"]):
                raise PurchaseRefused(
                    R_DUPLICATE_SUPPLIER,
                    f"this shop already has a supplier called "
                    f"{other.get('name')!r} ({other_id}). Nothing was changed.")

        was_name = str(rec.get("name") or "")
        rows[sid] = {**rec, **fields, "supplier_id": sid,
                     "updated_at": _now_iso()}
        _save_suppliers(rows)
        head = _audit("supplier.updated", supplier_id=sid,
                      name=fields["name"], was_name=was_name,
                      phone_sha256=hashlib.sha256(
                          fields["phone"].encode("utf-8")).hexdigest(),
                      minted=False)
        return JSONResponse({
            "ok": True, "settles_money": False,
            "supplier": _supplier_view(sid, rows[sid]),
            "audited": head is not None,
            "note": ("Purchases already filed keep the name they were filed "
                     "under. They are joined to this supplier by id."),
        })
    except PurchaseRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/purchases/margin")
def margin_ep(day: str | None = None) -> JSONResponse:
    """What every product in the catalogue earns, per unit.

    Selling price from the shop's own catalogue, cost from the most recent
    purchase — on or before `?day=YYYY-MM-DD` if one is given. A product with
    no recorded cost is listed with a null margin and named in `unknown`; it is
    never counted as earning its whole selling price.

    There is deliberately no grand total on this response. Summing a per-unit
    margin across products would add rupees-per-packet of biscuits to
    rupees-per-bottle of oil and call the result money. The day view below is
    where margins are summed, because there the units are known.
    """
    try:
        label = None if day is None else _valid_day(day)
        known = catalogue()
        hist = _cost_history()

        rows = []
        unknown: list[str] = []
        below: list[str] = []
        for sku_id in sorted(known):
            cost_row = _cost_as_of(hist, sku_id, label)
            row = _margin_row(sku_id, known[sku_id], cost_row)
            if not row["cost_known"]:
                unknown.append(sku_id)
            elif row["below_cost"]:
                below.append(sku_id)
            rows.append(row)

        # A product bought but no longer sold still has a cost on file. It is
        # reported separately rather than mixed in, because it is not something
        # the shop can earn on today.
        no_longer_sold = sorted(set(hist) - set(known))

        return JSONResponse({
            "ok": True, "settles_money": False,
            "as_of": label or "the latest cost on file",
            "count": len(rows),
            "with_a_cost": len(rows) - len(unknown),
            "without_a_cost": len(unknown),
            "margin_known_for_every_product": not unknown,
            "unknown": unknown,
            "below_cost": below,
            "bought_but_not_in_the_catalogue": no_longer_sold,
            "items": rows,
            "derived_from": ("selling prices come from this shop's catalogue "
                             "with today's offers applied; costs come from the "
                             "purchases recorded here. Where no purchase has "
                             "been recorded the margin is unknown, not zero."),
        })
    except PurchaseRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(PurchaseRefused(
            R_NO_CATALOGUE,
            f"a price in this shop's catalogue is not integer paise ({exc}), "
            f"so no margin can be derived from it."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/purchases/margin/today")
def margin_today_ep(day: str | None = None) -> JSONResponse:
    """The margin on what was actually billed today.

    Revenue is not asked for and not estimated: it is counted off the same
    hash-chained audit log the history screen walks, one line per packet that
    left the counter, at the price it was charged at.

    The answer is SPLIT. Lines whose product has a recorded cost are `covered`
    and carry a margin. Lines whose product does not are `uncovered`: their
    revenue is reported, their margin is not, and `margin_is_partial` says so.
    A shopkeeper reading a margin that silently excluded half his sales would be
    reading a number worse than none.
    """
    try:
        label = _today_label() if day is None else _valid_day(day)
        start, end = _day_bounds(label)

        try:
            from . import manage as _manage  # noqa: WPS433 - deliberately late
        except Exception as exc:  # noqa: BLE001
            raise PurchaseRefused(
                R_NO_BILLS,
                f"the bill book could not be read ({type(exc).__name__}: "
                f"{exc}). Today's margin is derived from the audit chain and "
                f"this module will not guess at it.") from None

        records, chain = _manage.read_chain()
        bills = _manage.bills_from(records)

        units: dict[str, int] = {}
        revenue: dict[str, int] = {}
        n_bills = 0
        unpriced_lines = 0
        for bill in bills.values():
            if not bill.get("closed"):
                continue
            at = _parse_ts(bill.get("at"))
            if at is None or not (start <= at < end):
                continue
            n_bills += 1
            for line in bill.get("line_items") or []:
                sku = str(line.get("sku_id") or "")
                price = line.get("price_paise")
                if not sku or isinstance(price, bool) or not isinstance(price, int):
                    unpriced_lines += 1
                    continue
                units[sku] = units.get(sku, 0) + 1
                revenue[sku] = revenue.get(sku, 0) + int(paise(price))

        known = catalogue()
        hist = _cost_history()

        rows = []
        cov_units = cov_revenue = cov_cost = 0
        unc_units = unc_revenue = 0
        uncovered_skus: list[str] = []
        for sku in sorted(units):
            n = units[sku]
            took = int(paise(revenue.get(sku, 0)))
            cost_row = _cost_as_of(hist, sku, label)
            row: dict[str, Any] = {
                "sku_id": sku,
                "name": str((known.get(sku) or {}).get("name") or sku),
                "units": n,
                "revenue_paise": took,
                "revenue_rupees": to_rupees_str(paise(took)),
                "still_in_catalogue": sku in known,
            }
            if cost_row is None:
                unc_units += n
                unc_revenue += took
                uncovered_skus.append(sku)
                row.update({
                    "cost_known": False,
                    "cost_paise": None,
                    "cost_total_paise": None,
                    "margin_paise": None,
                    "margin_rupees": None,
                    "margin_pct_of_price": None,
                    "note": ("nothing has been recorded about what this shop "
                             "paid for this product, so what it earned today "
                             "is not known. It is not zero."),
                })
            else:
                unit_cost = int(paise(cost_row["cost_paise"]))
                spent = unit_cost * n
                made = took - spent
                cov_units += n
                cov_revenue += took
                cov_cost += spent
                pct_sale = ((made * 1000) // took) if took > 0 else None
                row.update({
                    "cost_known": True,
                    "cost_paise": unit_cost,
                    "cost_rupees": to_rupees_str(paise(unit_cost)),
                    "cost_total_paise": spent,
                    "cost_total_rupees": to_rupees_str(paise(spent)),
                    "cost_recorded_on": cost_row.get("date"),
                    "margin_paise": made,
                    "margin_rupees": to_rupees_str(paise(made)),
                    "margin_pct_of_price": (None if pct_sale is None
                                            else _tenths(pct_sale)),
                    "below_cost": made < 0,
                })
            rows.append(row)

        made_total = cov_revenue - cov_cost
        pct_covered = ((made_total * 1000) // cov_revenue) if cov_revenue > 0 else None
        rows.sort(key=lambda r: (-(r["revenue_paise"]), r["sku_id"]))

        return JSONResponse({
            "ok": True, "settles_money": False,
            "date": label,
            "bills": n_bills,
            "revenue_paise": cov_revenue + unc_revenue,
            "revenue_rupees": to_rupees_str(paise(cov_revenue + unc_revenue)),
            "covered": {
                "skus": len(rows) - len(uncovered_skus),
                "units": cov_units,
                "revenue_paise": cov_revenue,
                "revenue_rupees": to_rupees_str(paise(cov_revenue)),
                "cost_paise": cov_cost,
                "cost_rupees": to_rupees_str(paise(cov_cost)),
                "margin_paise": made_total,
                "margin_rupees": to_rupees_str(paise(made_total)),
                "margin_pct_of_price": (None if pct_covered is None
                                        else _tenths(pct_covered)),
            },
            "uncovered": {
                "skus": uncovered_skus,
                "units": unc_units,
                "revenue_paise": unc_revenue,
                "revenue_rupees": to_rupees_str(paise(unc_revenue)),
            },
            "margin_is_partial": bool(uncovered_skus),
            "lines_without_a_price": unpriced_lines,
            "items": rows,
            "chain": chain,
            "derived_from": ("revenue is counted off the hash-chained audit log "
                             "for this calendar day in the counter's own "
                             "timezone; cost is the last price recorded on or "
                             "before that day, not lot-level FIFO. Products "
                             "with no recorded cost are excluded from the "
                             "margin and reported separately."),
        })
    except PurchaseRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(PurchaseRefused(
            R_NO_BILLS,
            f"a figure in the bill book is not integer paise ({exc}), so no "
            f"margin can be derived from it."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/purchases/sku/{sku_id}")
def sku_ep(sku_id: str) -> JSONResponse:
    """One product: what it sells for, every price ever paid for it, the margin.

    A product the shop has never bought answers with an empty history and an
    unknown margin — that is an answer, not a refusal, and it is the answer a
    shopkeeper wanting to know what he is missing should get.
    """
    try:
        known = catalogue()
        hist = _cost_history()
        rec = known.get(sku_id)
        rows = hist.get(sku_id) or []
        if rec is None and not rows:
            raise PurchaseRefused(
                R_UNKNOWN_SKU,
                f"{sku_id!r} is neither a product this shop sells nor one it "
                f"has ever recorded buying.", status=404)
        row = _margin_row(sku_id, rec, rows[-1] if rows else None)
        row["cost_history"] = rows
        row["times_bought"] = len(rows)
        units = 0
        for r in rows:
            units += int(r.get("units") or 0)
        row["units_bought"] = units
        return JSONResponse({"ok": True, "settles_money": False, **row})
    except PurchaseRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(PurchaseRefused(
            R_NO_CATALOGUE,
            f"a price for {sku_id!r} is not integer paise ({exc})."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/purchases")
def purchases_ep(supplier_id: str | None = None) -> JSONResponse:
    """Every purchase, newest first. `?supplier_id=` narrows it to one."""
    try:
        rows = _all_purchases()
        if supplier_id is not None:
            sid = _valid_supplier_id(supplier_id)
            rows = [r for r in rows if r.get("supplier_id") == sid]
        live = _live(rows)
        spent = 0
        for r in live:
            spent += int(paise(r.get("total_paise") or 0))
        return JSONResponse({
            "ok": True, "settles_money": False,
            "count": len(rows),
            "void_count": len(rows) - len(live),
            "spent_paise": spent,
            "spent_rupees": to_rupees_str(paise(spent)),
            "purchases": rows,
            "note": ("Voided purchases are still listed, marked void, and are "
                     "counted in nothing. Nothing here is ever deleted."),
        })
    except PurchaseRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


def record_purchase(body: dict[str, Any], *, source: Optional[dict[str, Any]] = None
                    ) -> dict[str, Any]:
    """THE purchase writer. Raises PurchaseRefused; returns {purchase, audited}.

    Body: {supplier_id, lines: [{sku_id, units, cost_paise | cost_rupees}],
    date?, invoice_no?}. An asserted `line_paise` or `total_paise` is compared
    against this counter's own arithmetic and refused on disagreement — never
    believed, and never quietly ignored, because a shopkeeper looking at one
    number while the book holds another is the failure that makes a book
    useless.

    Lifted out of the endpoint so that a purchase read off a PHOTOGRAPH
    (`gawaah/parchi.py`) is filed by exactly this code: the same refusals, the
    same integer arithmetic, the same file and the same chain. `source` is an
    optional {parchi_id, image_sha256} that rides on the document and the
    audit line so a cost can be traced back to the bill it was read from.
    """
    suppliers = _load_suppliers()
    supplier = _supplier(suppliers, body.get("supplier_id"))
    sid = str(supplier["supplier_id"])

    raw_lines = body.get("lines")
    if raw_lines is None or (isinstance(raw_lines, list) and not raw_lines):
        raise PurchaseRefused(
            R_NO_LINES,
            "this purchase has no lines, so there is nothing to record.")
    if not isinstance(raw_lines, list):
        raise PurchaseRefused(
            R_BAD_BODY,
            f"'lines' must be a list of {{sku_id, units, cost}}, not "
            f"{type(raw_lines).__name__}.")
    if len(raw_lines) > MAX_LINES:
        raise PurchaseRefused(
            R_TOO_MANY_LINES,
            f"this invoice has {len(raw_lines)} lines and the cap is "
            f"{MAX_LINES}. Split it.")

    known = catalogue()
    lines = [_one_line(raw, known) for raw in raw_lines]
    total = _sum_paise(lines)

    claimed = body.get("total_paise")
    if claimed is not None:
        if isinstance(claimed, bool) or not isinstance(claimed, int) \
                or claimed != total:
            raise PurchaseRefused(
                R_TOTAL_DISAGREES,
                f"this invoice says it comes to {claimed!r}; the lines add "
                f"up to {total} paise. Nothing was saved.")

    today = _today_label()
    raw_date = body.get("date")
    date = today if raw_date is None else _valid_day(raw_date)
    if date > today:
        raise PurchaseRefused(
            R_FUTURE_DATE,
            f"{date} has not happened yet; today is {today}. Stock cannot "
            f"have arrived on a day that has not come.")

    invoice = _invoice_no(body)
    if invoice:
        for doc in _live(_all_purchases()):
            if doc.get("supplier_id") == sid and \
                    str(doc.get("invoice_no") or "").casefold() == invoice.casefold():
                raise PurchaseRefused(
                    R_DUPLICATE_INVOICE,
                    f"invoice {invoice!r} from {supplier.get('name')!r} is "
                    f"already recorded as {doc.get('purchase_id')}. "
                    f"Entering it twice would double this shop's costs and "
                    f"halve its margin. Nothing was saved.")

    now = _now_iso()
    purchase_id = "pur_" + secrets.token_hex(6)
    doc = {
        "format": PURCHASE_FORMAT,
        "purchase_id": purchase_id,
        "at": now,
        "date": date,
        # The name is COPIED, not referenced. A supplier renamed next year
        # must not silently rewrite what last year's invoices say.
        "supplier_id": sid,
        "supplier_name": supplier.get("name"),
        "invoice_no": invoice or None,
        "lines": lines,
        "units": sum(int(ln["units"]) for ln in lines),
        "total_paise": total,
        "total_rupees": to_rupees_str(paise(total)),
        "void": False,
    }
    if source:
        doc["source"] = dict(source)
    _write_json(purchases_dir() / f"{purchase_id}.json", doc)
    head = _audit(
        "purchase.recorded",
        purchase_id=purchase_id,
        supplier_id=sid,
        supplier_name=supplier.get("name"),
        date=date,
        invoice_no=invoice or None,
        total_paise=total,
        lines=[{"sku_id": ln["sku_id"], "units": ln["units"],
                "cost_paise": ln["cost_paise"]} for ln in lines],
        source=dict(source) if source else None,
        minted=False,
    )
    return {"purchase": doc, "audited": head is not None}


def cost_coverage() -> dict[str, int]:
    """How many catalogue products have a recorded cost, right now.

    {count, with_a_cost, without_a_cost} — the three figures the margin screen
    heads with, computed the same way `margin_ep` computes them, so a caller
    that says "cost known for A -> B products" is quoting this screen and not
    a second opinion.
    """
    known = catalogue()
    hist = _cost_history()
    with_cost = sum(1 for sku in known if _cost_as_of(hist, sku, None) is not None)
    return {"count": len(known), "with_a_cost": with_cost,
            "without_a_cost": len(known) - with_cost}


@router.post("/purchases")
async def purchase_add_ep(request: Request) -> JSONResponse:
    """Record what the shop bought. The server totals it. See `record_purchase`."""
    try:
        body = await _json_body(request)
        out = record_purchase(body)
        return JSONResponse({
            "ok": True, "settles_money": False,
            **out,
            "note": ("This is a record of what was bought and what it cost. "
                     "Nothing was paid and nothing here can pay a supplier."),
        })
    except PurchaseRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(PurchaseRefused(
            R_BAD_COST,
            f"a cost on this invoice is not integer paise ({exc}). Nothing was "
            f"saved."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/purchases/{purchase_id}")
def purchase_one_ep(purchase_id: str) -> JSONResponse:
    """One purchase in full, with the margin each line would earn today."""
    try:
        doc = _read_purchase(purchase_id)
        known = catalogue()
        lines = []
        for ln in doc.get("lines") or []:
            if not isinstance(ln, dict):
                continue
            sku = str(ln.get("sku_id") or "")
            rec = known.get(sku)
            sell = None if rec is None else int(paise(rec["price_paise"]))
            cost = ln.get("cost_paise")
            cost = None if isinstance(cost, bool) or not isinstance(cost, int) \
                else int(cost)
            lines.append({**ln,
                          "sell_paise": sell,
                          "sell_rupees": (None if sell is None
                                          else to_rupees_str(paise(sell))),
                          "still_in_catalogue": rec is not None,
                          **_margin_block(sell, cost)})
        return JSONResponse({
            "ok": True, "settles_money": False,
            "purchase": doc,
            "lines_against_todays_prices": lines,
            "note": ("The margin beside each line is what that product would "
                     "earn at today's selling price, not what it earned when "
                     "it was sold."),
        })
    except PurchaseRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(PurchaseRefused(
            R_NO_CATALOGUE,
            f"a price in this shop's catalogue is not integer paise ({exc})."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/purchases/{purchase_id}/void")
async def purchase_void_ep(purchase_id: str, request: Request) -> JSONResponse:
    """Strike a purchase out. Body: {"reason": "..."}.

    NOTHING IS DELETED. A mistyped cost — a rupee figure into a paise field, an
    invoice entered twice — poisons every margin that product will ever show,
    so there has to be a way to correct it; and a file that can be quietly
    removed is not a book. So the record stays, marked void with the reason and
    the time, is excluded from every total and from the cost history, and the
    voiding is its own line in the chain.
    """
    try:
        body = await _json_body(request)
        reason = _text(body, "reason", cap=MAX_VOID_REASON)
        if not reason:
            raise PurchaseRefused(
                R_NO_VOID_REASON,
                "say why this purchase is being struck out. A voided record "
                "with no reason is indistinguishable from a deleted one.")
        doc = _read_purchase(purchase_id)
        if doc.get("void"):
            raise PurchaseRefused(
                R_ALREADY_VOID,
                f"purchase {doc.get('purchase_id')} was already voided on "
                f"{doc.get('voided_at')}. Nothing was changed.")
        now = _now_iso()
        doc["void"] = True
        doc["voided_at"] = now
        doc["void_reason"] = reason
        _write_json(purchases_dir() / f"{doc['purchase_id']}.json", doc)
        head = _audit("purchase.voided", purchase_id=doc["purchase_id"],
                      supplier_id=doc.get("supplier_id"),
                      total_paise=int(doc.get("total_paise") or 0),
                      reason=reason, minted=False)
        return JSONResponse({
            "ok": True, "settles_money": False,
            "purchase": doc,
            "audited": head is not None,
            "note": ("This purchase is struck out, not deleted. It is counted "
                     "in no total and sets no cost price."),
        })
    except PurchaseRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)
