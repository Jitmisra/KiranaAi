"""ORDER PARCHI — the purchase order the shelf writes for itself.

A kirana runs out of things in a particular way: not all at once, and not
visibly. Six packets of one biscuit go in a morning, a soap sits for a fortnight,
and the shopkeeper finds out which is which when a customer asks for the thing
that is not there. What he does about it is a phone call to a wholesaler with a
list read off the top of his head.

THIS FILE WRITES THAT LIST DOWN, AND IT INVENTS NOTHING TO DO IT.

Three questions have to be answered to put one line on a purchase order, and
every one of them is already answered somewhere else in this program:

    what is running out   ->  gawaah/stock.py     (the low-stock derivation)
    who it comes from     ->  gawaah/purchases.py (the supplier on the last
                              purchase that recorded a cost for it)
    what it will cost     ->  gawaah/purchases.py (that same purchase's cost
                              per unit)

None of the three is re-derived here. This module reads `stock.stock_low_ep()`
and uses its answer as it stands — the same list, in the same order, that the
Stock screen shows — and it reads the cost history `purchases.py` folds out of
the purchase documents. If either module changes its mind about what "low" means
or what a product last cost, this file changes with it and cannot drift.

WHAT IS UNKNOWN STAYS UNKNOWN
=============================
A product that has never been bought through this counter has no recorded cost,
so the money it will take to reorder it is NOT KNOWN. It is not zero and it is
not guessed from the selling price. The line shows the units to order and the
word UNKNOWN where the rupees go; the order's expected spend covers the lines
that have a cost and SAYS how many it does not cover. A purchase order that
totals ₹0.00 for four items nobody has ever bought is the kind of confident,
wrong number this whole program exists not to print.

The same restraint applies one level up. A product with a reorder level but no
count is not low and is not "0 on hand" — nobody has looked at the shelf. It is
listed separately, with the reason, and it is not ordered.

WHERE THE UNKNOWNS COLLECT, AND WHY IT IS NOT A COINCIDENCE
===========================================================
The supplier and the cost are read off the SAME purchase record, so a product
with a supplier always has a cost. A supplier's own group is therefore fully
priced, and everything unknown collects in the one group that has no supplier —
which cannot be confirmed anyway, because there is nobody to hand the paper to.
That group is the shopkeeper's to-do list: record one purchase against each of
those products and they move to a supplier and become orderable. The partial
arithmetic below is still general, because a purchase document edited by hand
can lose its supplier and keep its cost, and a total that quietly dropped that
line would be short by real money.

UNITS TO ORDER, AND THE LIMIT IN IT
===================================
    units to order = reorder level - what is on hand,   floored at nought

which treats the reorder level as the TARGET the shelf is brought back up to.
A stated limit, because it is a real one: a product sitting exactly at its level
has a shortfall of nought, so it appears on the draft with nothing to order
rather than being quietly rounded up to one. Raising the level on the Stock
screen is what changes that, and it is the shopkeeper's judgement, not this
file's. This module proposes no reorder level of its own.

CONFIRMING A PURCHASE ORDER DOES NOT RECEIVE STOCK
==================================================
It is a record of what was asked for. The packets arrive days later, some of
them short, and THAT is when the shelf figure moves — through
`POST /stock/{sku}/in` on the Stock screen, when a human has looked in the box.
Booking stock in at the moment an order is placed would put packets on the
shelf figure that are still on a lorry, and every figure derived from it would
be wrong until they arrived. The response says so, the printed page says so, and
the screen says so.

Nothing here is paid, either. There is no gateway credential in this module, no
mint, no payable string and no `upi:` payload — a purchase order is a piece of
paper you hand a wholesaler, and what happens to the money afterwards happens
between two people. `settles_money` is false on every response as a fact about
the code.

WHAT THE BROWSER MAY SAY
========================
Which supplier to order from, and which of that supplier's lines to leave out.
That is the whole of it. Units and rupees are derived here from the two modules
above; a body that asserts either is REFUSED BY NAME rather than ignored,
because silently dropping an asserted quantity would leave a shopkeeper looking
at an order for a number he did not send.

THE STORE
=========
One document per order at `<shop>/po/po_*.json`, and one line per confirmed
order on `<shop>/po.audit.jsonl` — this module's OWN hash chain, written by
`gawaah/ledger.py`. Deliberately not `results/audit.jsonl`: the money service
holds that file open in another process and keeps the chain head in memory, so a
second writer between two of its appends breaks `make verify-ledger` on the one
log that must be beyond argument. The same note is in `storefront.py`,
`offers.py`, `purchases.py` and `stock.py`.

The chain is the record. A document that could not be chained is deleted and the
request is refused: an order this counter cannot prove it wrote must not be
listed as though it had been.

A confirmed order is never edited and never deleted. If it was wrong, confirm
another one — the wholesaler has the first piece of paper either way, and a
record that can be rewritten is not a record.

A REFUSAL IS A RESULT
=====================
Every failure below has a name in the body and a 400 (404 for an id that does
not exist). Nothing here raises a 500.

MOUNTING
========
The router carries NO prefix; the paths below are already absolute::

    from gawaah import po
    app.include_router(po.router)
"""
from __future__ import annotations

import datetime as _dt
import html
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import purchases as _purchases
from . import stock as _stock
from .ledger import Ledger, verify
from .money import MoneyError, paise, to_rupees_str

router = APIRouter()


# --------------------------------------------------------------- refusals --
#
# Every one of these is a state this module can actually reach, named for the
# STATE rather than for the fix. The sentence saying what to do goes in `detail`.

#: Passed through from `purchases.py` with its own name intact, because "the
#: till module is not loaded" is the actual problem and renaming it to something
#: about orders would send a reader looking in the wrong file. Declared here so
#: the whole set of names a caller may have to handle is in one place.
R_NO_TILL = "till_module_unavailable"

R_LOW_UNAVAILABLE = "low_stock_list_unavailable"
R_COSTS_UNAVAILABLE = "cost_records_unavailable"

R_BAD_BODY = "po_body_not_json"
R_NO_SUPPLIER_ID = "supplier_id_missing"
R_BAD_SUPPLIER_ID = "supplier_id_malformed"
R_NO_SUPPLIER = "no_such_supplier"
R_NOTHING_TO_ORDER = "nothing_to_order_from_this_supplier"
R_BAD_SKUS = "skus_not_a_list_of_product_ids"
R_SKU_NOT_ON_DRAFT = "sku_not_on_this_draft"
R_EMPTY_SELECTION = "no_lines_selected"
R_CLIENT_UNITS = "client_sent_units"
R_CLIENT_MONEY = "client_sent_money"
R_TOO_MANY_LINES = "too_many_lines_in_one_order"
R_TOO_LONG = "field_too_long"
R_BAD_PO_ID = "po_id_malformed"
R_NO_PO = "no_such_po"
R_NOT_WRITTEN = "purchase_order_not_written"
R_INTERNAL = "po_internal_error"


#: An order is a phone call to a wholesaler, not a warehouse transfer. The cap
#: bounds what ends up on one piece of paper and on one chain line. It is
#: refused by name and never silently truncated: an order half of which was
#: dropped is worse than an order that was not placed.
MAX_LINES = 200

MAX_NOTE = 400

PO_FORMAT = 1
PO_ID_RE = re.compile(r"^po_[0-9a-f]{12}$")

PO_SUBDIR = "po"
AUDIT_SIDECAR = "po.audit.jsonl"

EV_CONFIRMED = "po_confirmed"

#: The keys a page is not allowed to send. Units come from the shelf and the
#: level; rupees come from the last purchase. A body carrying either is a page
#: trying to author what this module derives.
UNIT_KEYS = ("units", "units_to_order", "qty", "quantity")
MONEY_KEYS = ("cost_paise", "cost_rupees", "expected_paise", "expected_rupees",
              "line_paise", "total_paise", "amount_paise", "price_paise")


class PoRefused(Exception):
    """A named refusal with a sentence a shopkeeper can act on."""

    def __init__(self, reason: str, detail: str, status: int = 400) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.status = status


def _refusal(exc: PoRefused) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "reason": exc.reason, "detail": exc.detail,
         "settles_money": False},
        status_code=exc.status,
    )


def _passthrough(exc: Any) -> PoRefused:
    """A refusal from `purchases.py`, kept under ITS OWN name.

    Renaming "the till module is not loaded" into something about orders would
    send whoever reads it looking in this file for a problem that is two files
    away. The names it can carry are declared at the top of this module so a
    caller can still see the whole set in one place.
    """
    return PoRefused(
        str(getattr(exc, "reason", None) or R_INTERNAL),
        str(getattr(exc, "detail", None) or exc),
        int(getattr(exc, "status", 400) or 400),
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
# Resolved per call and never memoised at import: a test that moves
# GAWAAH_SHOP_DIR between two tests must be able to, and a module-level constant
# captured at import time ignores it silently — which is how a harness once
# wrote over a live catalogue.


def shop_dir() -> Path:
    """The shopkeeper's directory — PURCHASES' answer, which is the till's.

    `purchases.shop_dir()` is `upload_app.store_dir()`, the one reader of
    GAWAAH_SHOP_DIR in this program. Reading the environment here would be a
    second answer to one question, and an order filed in one shop against a
    catalogue read from another is a wrong piece of paper with nothing on it to
    say so.
    """
    return Path(_purchases.shop_dir())


def po_dir() -> Path:
    """Orders live next to the purchases they will one day become."""
    return shop_dir() / PO_SUBDIR


def audit_path() -> Path:
    """This module's own hash chain. See the module docstring on why it is not
    `results/audit.jsonl`."""
    return shop_dir() / AUDIT_SIDECAR


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _today_label() -> str:
    """Today in the counter's own timezone, through purchases' own function.

    A purchase order raised today has to file under the same day a purchase
    recorded today files under, or the pair cannot be matched when the delivery
    arrives.
    """
    return str(_purchases._today_label())


def _write_json(path: Path, doc: Any) -> None:
    """Write via a temp file and rename, so a reader never sees half a record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=1)
                   + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _chain_block() -> dict[str, Any]:
    """The state of this module's chain, re-walked from genesis.

    On every response that shows an order, because a list of orders read out of
    files whose chain does not verify is a list that has to say so.
    """
    p = audit_path()
    if not p.exists():
        return {"exists": False, "ok": True, "lines": 0, "head": None,
                "error": None, "path": str(p)}
    ok, lines, head, err = verify(p)
    return {"exists": True, "ok": bool(ok), "lines": int(lines), "head": head,
            "error": err, "path": str(p)}


# ------------------------------------------------- what the other two say --


def _low_rows() -> dict[str, Any]:
    """STOCK.PY'S OWN low-stock answer, decoded, and never a second one.

    This calls the endpoint function rather than reassembling the lists from
    `stock_rows()`, and that is deliberate. The definition of "low" (at or under
    the level the shopkeeper set), the three-way split (low / level-set-but-
    never-counted / below-zero) and the worst-first ordering all live in
    `stock.stock_low_ep`. Re-implementing any of them here would produce a
    purchase order that disagreed with the screen it was raised from, and there
    would be nothing on either page to say which was right.

    A refusal from stock is passed through with ITS reason inside the detail, so
    a shopkeeper reading this page is told the actual problem — the inventory
    derivation could not be read — and not merely that an order could not be
    drafted.
    """
    try:
        response = _stock.stock_low_ep()
        body = json.loads(bytes(response.body))
    except Exception as exc:  # noqa: BLE001 - a broken read is a named answer
        raise PoRefused(
            R_LOW_UNAVAILABLE,
            f"the low-stock list in gawaah/stock.py could not be read "
            f"({type(exc).__name__}: {exc}). No order is drafted rather than "
            f"one drafted from half a shelf.") from None
    if not isinstance(body, dict) or body.get("ok") is not True:
        reason = (body or {}).get("reason") if isinstance(body, dict) else None
        detail = (body or {}).get("detail") if isinstance(body, dict) else None
        raise PoRefused(
            R_LOW_UNAVAILABLE,
            f"the Stock screen refused to say what is running out "
            f"({reason or 'no reason given'}): {detail or ''}".strip())
    return body


def _cost_history() -> dict[str, list[dict[str, Any]]]:
    """{sku -> every cost ever recorded, oldest first} — PURCHASES' derivation.

    Looked up by both spellings so that the day the orchestrator promotes the
    helper to a public name, this module follows it without an edit. If NEITHER
    exists — purchases.py renamed or refactored — that is a named refusal and
    not an AttributeError, because "no cost is known for anything" and "the
    module the costs come from moved" must not look the same on a page a
    shopkeeper is about to spend money from.
    """
    fn = (getattr(_purchases, "cost_history", None)
          or getattr(_purchases, "_cost_history", None))
    if fn is None:
        raise PoRefused(
            R_COSTS_UNAVAILABLE,
            "gawaah/purchases.py no longer exposes the cost history this order "
            "reads its supplier and its last-paid price from. Nothing is "
            "drafted: an order with no costs behind it would be a list of "
            "names with invented rupees beside them.")
    try:
        return dict(fn())
    except Exception as exc:  # noqa: BLE001
        raise PoRefused(
            R_COSTS_UNAVAILABLE,
            f"the cost history in gawaah/purchases.py could not be read "
            f"({type(exc).__name__}: {exc}).") from None


def _suppliers() -> dict[str, dict[str, Any]]:
    """Every supplier on file, keyed by id. An absent sidecar is no suppliers.

    Same two-spelling lookup as the cost history, and for the same reason. This
    is what puts a phone number on the printed page; the supplier NAME on a line
    comes off the purchase document itself, so an order can still be grouped
    correctly for a wholesaler whose record was later deleted.
    """
    fn = (getattr(_purchases, "load_suppliers", None)
          or getattr(_purchases, "_load_suppliers", None))
    if fn is None:
        return {}
    try:
        return dict(fn())
    except Exception:  # noqa: BLE001 - an unreadable sidecar is "none on file"
        return {}


def _last_cost(hist: dict[str, list[dict[str, Any]]],
               sku_id: str) -> Optional[dict[str, Any]]:
    """The most recent cost recorded for one product, through purchases' own
    `_cost_as_of`, or None where nothing was ever recorded.

    NOT lot-level FIFO — purchases.py states that limit and it is inherited
    here: a shop that bought the same item twice in a week at two rates orders
    at the later of the two.
    """
    fn = (getattr(_purchases, "cost_as_of", None)
          or getattr(_purchases, "_cost_as_of", None))
    if fn is None:
        rows = hist.get(sku_id) or []
        return rows[-1] if rows else None
    row = fn(hist, sku_id, None)
    return row if isinstance(row, dict) else None


# ------------------------------------------------------------- the draft --


def _line(row: dict[str, Any], cost_row: Optional[dict[str, Any]]
          ) -> dict[str, Any]:
    """One product on an order: how many, from whom, and at what — if known.

    `units_to_order` is the shortfall against the shopkeeper's own level, and it
    is floored at nought here rather than anywhere else so there is one place
    that can never emit a negative quantity.
    """
    level = row.get("reorder_level")
    on_hand = row.get("on_hand_units")
    units = 0
    if isinstance(level, int) and not isinstance(level, bool) \
            and isinstance(on_hand, int) and not isinstance(on_hand, bool):
        units = level - on_hand
        if units < 0:
            units = 0

    cost: Optional[int] = None
    if cost_row is not None:
        raw = cost_row.get("cost_paise")
        if isinstance(raw, int) and not isinstance(raw, bool):
            cost = int(paise(raw))

    line_cost: Optional[int] = None if cost is None else int(
        paise(cost * units))

    return {
        "sku_id": row.get("sku_id"),
        "name": row.get("name"),
        # --- what the shelf says, straight from stock.py
        "on_hand_units": on_hand,
        "reorder_level": level,
        "units_to_order": units,
        "days_of_cover": row.get("days_of_cover"),
        # --- what the purchase book says, straight from purchases.py
        "cost_known": cost is not None,
        "cost_paise": cost,
        "cost_rupees": None if cost is None else to_rupees_str(paise(cost)),
        "cost_recorded_on": None if cost_row is None else cost_row.get("date"),
        "cost_from": None if cost_row is None else {
            "purchase_id": cost_row.get("purchase_id"),
            "invoice_no": cost_row.get("invoice_no"),
        },
        "line_paise": line_cost,
        "line_rupees": (None if line_cost is None
                        else to_rupees_str(paise(line_cost))),
        "why_no_cost": (None if cost is not None else
                        "This shop has never recorded buying this product, so "
                        "what it costs is not known. It is not zero."),
    }


def _spend(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """What the known lines add up to, and how much of the order they cover.

    THE UNKNOWN IS NEVER SUMMED IN AS NOUGHT. Where no line has a cost the
    expected spend is null and not ₹0.00, because an order for four things
    nobody has ever bought does not cost nothing — it costs an amount this
    counter cannot state.
    """
    known = [ln for ln in lines if ln["cost_known"]]
    unknown = [ln for ln in lines if not ln["cost_known"]]
    covered = 0
    for ln in known:
        covered += int(paise(ln["line_paise"]))
    expected: Optional[int] = int(paise(covered)) if known else None

    if not lines:
        note = "Nothing to order."
    elif not unknown:
        note = (f"Every line has a cost on record. This is what the last "
                f"recorded prices come to, not a quotation from the supplier.")
    elif not known:
        note = (f"None of these {len(unknown)} products has a recorded cost, so "
                f"what this order will come to is not known. Record a purchase "
                f"against them once and the figure fills in.")
    else:
        note = (f"This covers {len(known)} of {len(lines)} lines. "
                f"{len(unknown)} ha{'s' if len(unknown) == 1 else 've'} no cost "
                f"on record, so the order will come to more than this.")

    return {
        "expected_paise": expected,
        "expected_rupees": (None if expected is None
                            else to_rupees_str(paise(expected))),
        "expected_is_partial": bool(known and unknown),
        "lines_priced": len(known),
        "lines_with_no_cost": len(unknown),
        "expected_note": note,
    }


def _units_of(lines: list[dict[str, Any]]) -> int:
    n = 0
    for ln in lines:
        n += int(ln["units_to_order"])
    return n


def draft() -> dict[str, Any]:
    """Everything that is under its level, grouped by who it is bought from.

    The grouping key is the SUPPLIER ON THE LAST PURCHASE THAT RECORDED A COST
    for that product. That is the only link between a product and a wholesaler
    this program has, and it is a real one — it is who the shopkeeper actually
    bought it from last time. A product with no purchase behind it belongs to no
    supplier, is grouped under `supplier_id: null`, and CANNOT be confirmed:
    there is nobody to hand the paper to.
    """
    low = _low_rows()
    hist = _cost_history()
    suppliers = _suppliers()

    groups: dict[Optional[str], dict[str, Any]] = {}
    at_level: list[dict[str, Any]] = []

    for row in low.get("low") or []:
        if not isinstance(row, dict):
            continue
        cost_row = _last_cost(hist, str(row.get("sku_id")))
        line = _line(row, cost_row)

        if line["units_to_order"] <= 0:
            # Sitting exactly ON the level. It is low — that is why stock.py
            # listed it — but the shortfall is nought and an order line for no
            # packets is not an order line. Rounding it up to one would be this
            # file inventing a quantity.
            at_level.append({
                "sku_id": line["sku_id"], "name": line["name"],
                "on_hand_units": line["on_hand_units"],
                "reorder_level": line["reorder_level"],
                "why": ("The shelf is exactly at the level you set, so the "
                        "shortfall is nought. Raise the level on the Stock "
                        "screen if you want this ordered."),
            })
            continue

        sid = None
        name = None
        if cost_row is not None:
            raw_sid = cost_row.get("supplier_id")
            if isinstance(raw_sid, str) and raw_sid:
                sid = raw_sid
                name = cost_row.get("supplier_name")

        group = groups.get(sid)
        if group is None:
            rec = suppliers.get(sid or "") if sid else None
            group = {
                "supplier_id": sid,
                # The name off the PURCHASE unless the supplier is still on
                # file, in which case the file is the newer of the two.
                "supplier_name": (str((rec or {}).get("name") or name or "")
                                  or None),
                "supplier_phone": (rec or {}).get("phone"),
                "supplier_on_file": rec is not None,
                "can_confirm": sid is not None,
                "why_not": (None if sid is not None else
                            "These products have never been bought through "
                            "this counter, so there is no supplier to send an "
                            "order to. Record one purchase against a supplier "
                            "and they will group under that supplier here."),
                "lines": [],
            }
            groups[sid] = group
        group["lines"].append(line)

    out: list[dict[str, Any]] = []
    for sid, group in groups.items():
        lines = group["lines"]
        lines.sort(key=lambda ln: (-int(ln["units_to_order"]),
                                   str(ln["sku_id"])))
        out.append({**group, "line_count": len(lines),
                    "units_total": _units_of(lines), **_spend(lines)})

    # Suppliers first, worst shortfall first; the unassigned group last, because
    # it is the one nothing can be done about from this screen.
    out.sort(key=lambda g: (g["supplier_id"] is None,
                            -int(g["units_total"]),
                            str(g["supplier_name"] or "")))

    return {
        "groups": out,
        "at_level_nothing_to_order": at_level,
        # Stock's own two honest lists, passed through rather than restated.
        "level_set_but_never_counted": low.get("unknown") or [],
        "needs_recount": [
            {"sku_id": r.get("sku_id"), "name": r.get("name"),
             "on_hand_units": r.get("on_hand_units"),
             "reorder_level": r.get("reorder_level")}
            for r in (low.get("needs_recount") or []) if isinstance(r, dict)
        ],
        "skus_with_a_level": low.get("skus_with_a_level"),
        "skus_without_a_level": low.get("skus_without_a_level"),
        "stock_chain": low.get("chain"),
        "now": low.get("now"),
    }


# ----------------------------------------------------------- reading input --


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a named refusal
        raise PoRefused(
            R_BAD_BODY,
            'this request\'s body is not JSON. It should look like '
            '{"supplier_id": "sup_0123456789ab"}.') from None
    if not isinstance(body, dict):
        raise PoRefused(
            R_BAD_BODY,
            f"this request's body is a {type(body).__name__}; it must be a "
            f"JSON object.")
    return body


def _reject_authored(body: dict[str, Any]) -> None:
    """INVARIANT 3, enforced by name rather than by ignoring the field.

    A page that sends a quantity or a rupee figure is asking this module to file
    a number it did not derive. Ignoring it silently would leave the shopkeeper
    looking at an order for something other than what his screen showed him, so
    the whole request is refused and the sentence says where the number he
    wanted actually comes from.
    """
    for key in UNIT_KEYS:
        if key in body:
            raise PoRefused(
                R_CLIENT_UNITS,
                f"this request carries {key!r}. How many to order is worked out "
                f"here — your reorder level minus what is on the shelf — and "
                f"cannot be sent in. Change the reorder level on the Stock "
                f"screen to change the quantity. Nothing was ordered.")
    for key in MONEY_KEYS:
        if key in body:
            raise PoRefused(
                R_CLIENT_MONEY,
                f"this request carries {key!r}. Every rupee on an order comes "
                f"from the last purchase recorded for that product, and a "
                f"price sent from a browser is not one this counter has ever "
                f"seen. Nothing was ordered.")


def _valid_po_id(po_id: Any) -> str:
    """Checked against a strict charset BEFORE it is ever joined to a path.

    The id becomes a filename. This shape check is what stops a request for
    `../../catalog` reading the shopkeeper's price list.
    """
    s = (po_id or "").strip() if isinstance(po_id, str) else ""
    if not PO_ID_RE.match(s):
        raise PoRefused(
            R_BAD_PO_ID,
            f"{po_id!r} is not an order id from this shop. They look like "
            f"'po_' followed by twelve hex characters.")
    return s


def _po_path(po_id: str) -> Path:
    return po_dir() / f"{_valid_po_id(po_id)}.json"


def _read_po(po_id: str) -> dict[str, Any]:
    path = _po_path(po_id)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PoRefused(
            R_NO_PO, f"this shop has no order {po_id}.", status=404) from None
    except Exception as exc:  # noqa: BLE001
        raise PoRefused(
            R_NO_PO,
            f"order {po_id} is on disk but could not be read "
            f"({type(exc).__name__}: {exc}).", status=404) from None
    if not isinstance(doc, dict) or doc.get("po_id") != po_id:
        raise PoRefused(
            R_NO_PO,
            f"the file for {po_id} does not contain that order.", status=404)
    return doc


def _all_pos() -> list[dict[str, Any]]:
    """Every order, newest first. An unreadable file is skipped, not fatal.

    One bad file must not hide the rest: a shopkeeper looking for last week's
    order needs the ones that ARE readable more than he needs a stack trace.
    """
    d = po_dir()
    out: list[dict[str, Any]] = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("po_*.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(doc, dict) and doc.get("po_id"):
            out.append(doc)
    out.sort(key=lambda r: (str(r.get("at") or ""), str(r.get("po_id") or "")),
             reverse=True)
    return out


def _summary(doc: dict[str, Any]) -> dict[str, Any]:
    """One order as a row on the list. Money only where it is known."""
    return {
        "po_id": doc.get("po_id"),
        "at": doc.get("at"),
        "date": doc.get("date"),
        "supplier_id": doc.get("supplier_id"),
        "supplier_name": doc.get("supplier_name"),
        "line_count": len(doc.get("lines") or []),
        "units_total": doc.get("units_total"),
        "expected_paise": doc.get("expected_paise"),
        "expected_rupees": doc.get("expected_rupees"),
        "expected_is_partial": doc.get("expected_is_partial"),
        "lines_with_no_cost": doc.get("lines_with_no_cost"),
        "stock_received": False,
        "chain_head": doc.get("chain_head"),
    }


# --------------------------------------------------- the message and the page


def _share_text(doc: dict[str, Any]) -> str:
    """The order as plain text, for `gawaah/share.py` to carry to WhatsApp.

    PLAIN TEXT AND NOTHING ELSE. No link, no payable string, no `upi:` payload —
    invariant 4 is not softened because the destination is a chat window, and a
    wholesaler is not paid through this program. What travels is the list, the
    expected spend where it is known, and the sentence saying the money is not
    settled here.

    The rupee figure is `money.to_rupees_str`, so the text carries exactly the
    integer paise the record holds and no reformatting of it.
    """
    lines = doc.get("lines") or []
    out: list[str] = []
    shop = doc.get("shop_name")
    out.append(f"Order from {shop}" if shop else "Order")
    supplier = doc.get("supplier_name") or doc.get("supplier_id")
    out.append(f"To: {supplier}")
    out.append(f"{doc.get('date')}  ({doc.get('po_id')})")
    out.append("")
    for i, ln in enumerate(lines, 1):
        rupees = ln.get("line_rupees")
        money = f" - Rs {rupees}" if rupees else " - cost not on record"
        out.append(f"{i}. {ln.get('name') or ln.get('sku_id')}"
                   f"  x{ln.get('units_to_order')}{money}")
    out.append("")
    expected = doc.get("expected_rupees")
    if expected is None:
        out.append("Expected total: not known — none of these has a cost "
                   "recorded in this shop's book.")
    elif doc.get("expected_is_partial"):
        out.append(f"Expected total: Rs {expected} for "
                   f"{doc.get('lines_priced')} of {len(lines)} items. The rest "
                   f"have no cost recorded, so it will come to more.")
    else:
        out.append(f"Expected total: Rs {expected}, at the prices last "
                   f"recorded in this shop's book.")
    out.append("This is an order, not a payment. Nothing is paid through this "
               "counter.")
    note = doc.get("note")
    if note:
        out.append("")
        out.append(str(note))
    return "\n".join(out)


_PRINT_CSS = """
@page{size:A4;margin:14mm}
*{box-sizing:border-box}
body{margin:0;font:13px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',
Roboto,'Helvetica Neue',Arial,sans-serif;color:#15171E;background:#fff;
font-variant-numeric:tabular-nums}
.wrap{max-width:190mm;margin:0 auto;padding:10mm}
h1{font-size:20px;margin:0 0 2px;letter-spacing:-.015em}
h2{font-size:13px;margin:18px 0 6px;letter-spacing:.08em;text-transform:uppercase;
color:#555B6C;font-weight:700}
.top{display:flex;justify-content:space-between;gap:16px;
border-bottom:2px solid #15171E;padding-bottom:10px}
.muted{color:#555B6C}
.id{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px;color:#555B6C}
table{width:100%;border-collapse:collapse;margin-top:6px}
th{text-align:left;font-size:11px;letter-spacing:.06em;text-transform:uppercase;
color:#555B6C;border-bottom:1px solid #D3CEC2;padding:6px 8px 6px 0}
td{padding:7px 8px 7px 0;border-bottom:1px solid #E8E4DC;vertical-align:top}
th.n,td.n{text-align:right;padding-right:0;white-space:nowrap}
/* The row number needs a gap the .n rule takes away: without it the figure and
   the product name print as "1Fortune Sunflower". */
th.i,td.i{text-align:right;width:8mm;padding-right:6px;color:#555B6C}
tfoot td{border-bottom:none;border-top:2px solid #15171E;font-weight:700;
padding-top:9px}
.unk{color:#8A5A10;font-weight:600}
.note{margin-top:14px;padding:10px 12px;border:1px solid #D3CEC2;
border-radius:6px;background:#F7F5F0}
.note b{display:block;margin-bottom:2px}
.sign{margin-top:26px;display:flex;gap:28px}
.sign div{flex:1;border-top:1px solid #8F94A4;padding-top:6px;font-size:11px;
color:#555B6C}
@media print{.wrap{padding:0}}
"""


def _print_html(doc: dict[str, Any]) -> str:
    """The order as one self-contained page: inline CSS, no script, no assets.

    Self-contained because it is printed and because it may be saved: a page
    that fetches a stylesheet is a page that prints unstyled in the back of a
    shop with no connection. There is no script tag, so nothing on it can
    change after it left this function.

    UNKNOWN IS PRINTED AS THE WORD. A dash or a blank in the rupee column would
    be read as nought by anyone holding the paper, which is the one reading this
    program refuses to allow.
    """
    esc = html.escape

    def money_cell(ln: dict[str, Any]) -> str:
        if not ln.get("cost_known"):
            return '<span class="unk">unknown</span>'
        return f"₹{esc(str(ln.get('line_rupees')))}"

    rows = []
    for i, ln in enumerate(doc.get("lines") or [], 1):
        per = ("<span class=\"unk\">not on record</span>"
               if not ln.get("cost_known")
               else f"₹{esc(str(ln.get('cost_rupees')))}")
        rows.append(
            f"<tr><td class=\"i\">{i}</td>"
            f"<td>{esc(str(ln.get('name') or ln.get('sku_id') or ''))}"
            f"<br><span class=\"id\">{esc(str(ln.get('sku_id') or ''))}</span></td>"
            f"<td class=\"n\">{esc(str(ln.get('on_hand_units')))}</td>"
            f"<td class=\"n\">{esc(str(ln.get('reorder_level')))}</td>"
            f"<td class=\"n\"><b>{esc(str(ln.get('units_to_order')))}</b></td>"
            f"<td class=\"n\">{per}</td>"
            f"<td class=\"n\">{money_cell(ln)}</td></tr>")

    expected = doc.get("expected_rupees")
    if expected is None:
        foot = '<span class="unk">not known</span>'
    elif doc.get("expected_is_partial"):
        foot = (f"₹{esc(str(expected))} "
                f"<span class=\"unk\">+ unknown</span>")
    else:
        foot = f"₹{esc(str(expected))}"

    shop = doc.get("shop_name")
    head_left = (f"<h1>{esc(str(shop))}</h1>" if shop else
                 "<h1>Purchase order</h1>"
                 "<div class=\"muted\">This shop has not been named yet — "
                 "set it on Your shop.</div>")
    if shop:
        bits = [esc(str(doc.get("shop_address") or "")),
                esc(str(doc.get("shop_phone") or ""))]
        head_left += ('<div class="muted">'
                      + "<br>".join(b for b in bits if b) + "</div>")

    supplier = doc.get("supplier_name") or doc.get("supplier_id") or ""
    phone = doc.get("supplier_phone")
    unknown_n = int(doc.get("lines_with_no_cost") or 0)
    spend_note = esc(str(doc.get("expected_note") or ""))

    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Purchase order {esc(str(doc.get('po_id')))}</title>"
        f"<style>{_PRINT_CSS}</style></head><body><div class=\"wrap\">"
        f'<div class="top"><div>{head_left}</div>'
        f'<div style="text-align:right">'
        f'<h1>Order</h1>'
        f'<div class="id">{esc(str(doc.get("po_id")))}</div>'
        f'<div class="muted">{esc(str(doc.get("date")))}</div></div></div>'
        f"<h2>To</h2><div><b>{esc(str(supplier))}</b>"
        + (f'<div class="muted">{esc(str(phone))}</div>' if phone else "")
        + "</div>"
        # The shopkeeper's own sentence, ABOVE the list. A delivery instruction
        # printed under a signature block is one nobody reads until afterwards.
        + (f'<h2>Note</h2><div>{esc(str(doc.get("note")))}</div>'
           if doc.get("note") else "")
        + "<h2>What to send</h2>"
        "<table><thead><tr><th class=\"i\">#</th><th>Product</th>"
        "<th class=\"n\">On hand</th><th class=\"n\">Level</th>"
        "<th class=\"n\">Order</th><th class=\"n\">Last cost</th>"
        "<th class=\"n\">Line</th></tr></thead><tbody>"
        + "".join(rows) +
        "</tbody><tfoot><tr><td colspan=\"4\"></td>"
        f"<td class=\"n\">{esc(str(doc.get('units_total')))}</td>"
        f"<td class=\"n\">Expected</td><td class=\"n\">{foot}</td>"
        "</tr></tfoot></table>"
        f'<div class="note"><b>About the money on this page</b>{spend_note}'
        + (f" {unknown_n} line(s) show unknown: this shop has never recorded "
           f"buying them, so what they cost is not known and is not nought."
           if unknown_n else "")
        + "</div>"
        '<div class="note"><b>This order has not changed the shelf</b>'
        "Confirming an order does not receive stock. When the delivery arrives, "
        "count what is in the box and book it in on the Stock screen. Nothing "
        "is paid through this counter."
        "</div>"
        '<div class="sign"><div>Sent by</div><div>Received by</div>'
        '<div>Date</div></div>'
        f'<div class="note id">Written {esc(str(doc.get("at")))} · '
        f'chain {esc(str(doc.get("chain_head") or "not chained"))}</div>'
        "</div></body></html>")


# ------------------------------------------------------------- the routes --


@router.get("/po/draft")
def po_draft_ep() -> JSONResponse:
    """What is running out, grouped by who it is bought from. Nothing is saved.

    A draft is a READ. It is recomputed on every request from the shelf and the
    purchase book, so it changes the moment a delivery is booked in or a level
    is moved, and there is no saved draft anywhere to go stale.
    """
    try:
        payload = draft()
        groups = payload["groups"]
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "count": len(groups),
            "orderable_groups": sum(1 for g in groups if g["can_confirm"]),
            "lines_total": sum(int(g["line_count"]) for g in groups),
            "chain": _chain_block(),
            "note": (
                "Everything here is at or under the reorder level you set, on "
                "figures that cannot see anything leaving the shelf unbilled "
                "and unrecorded. The rupees are the last price this shop "
                "recorded paying, not a quotation. Confirming an order does "
                "not receive stock — book that in on the Stock screen when the "
                "delivery arrives."),
            **payload,
        })
    except PoRefused as exc:
        return _refusal(exc)
    except _purchases.PurchaseRefused as exc:
        return _refusal(_passthrough(exc))
    except MoneyError as exc:
        return _refusal(PoRefused(
            R_COSTS_UNAVAILABLE,
            f"a recorded cost is not integer paise ({exc}). No order is "
            f"drafted from a figure that is not money."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/po/confirm")
async def po_confirm_ep(request: Request) -> JSONResponse:
    """Write one supplier's order down. Body: {"supplier_id": "sup_…"}.

    Optional: `skus`, to leave lines out — a shopkeeper who does not want the
    soap this week — and `note`, which is printed on the page and carried in the
    message. Quantities and rupees are NOT accepted; see `_reject_authored`.

    The draft is recomputed inside this call rather than taken from the page, so
    an order confirmed off a screen that has been open since this morning is an
    order for what the shelf needs NOW. If that means the numbers differ from
    what was on screen, the response carries the ones that were written, and
    they are the ones on the paper.
    """
    try:
        body = await _json_body(request)
        _reject_authored(body)

        raw_sid = body.get("supplier_id")
        if raw_sid is None or (isinstance(raw_sid, str) and not raw_sid.strip()):
            raise PoRefused(
                R_NO_SUPPLIER_ID,
                'no "supplier_id" in the body. An order needs somebody to send '
                'it to. Products this shop has never bought have no supplier '
                'on file and cannot be ordered from this screen — record one '
                'purchase against a supplier first.')
        sid = _valid_supplier_id(raw_sid)

        note = _note(body)
        wanted = _wanted_skus(body)

        payload = draft()
        group = next((g for g in payload["groups"]
                      if g["supplier_id"] == sid), None)
        if group is None:
            # Two different problems wear the same shape here, and a shopkeeper
            # needs to be told which. A supplier who is not on file at all is a
            # mistyped id; a supplier on file with nothing under its level is a
            # shelf that does not need anything yet. The group is checked FIRST
            # so a supplier whose record was later deleted can still be ordered
            # from — the purchases behind the products are what group them.
            if sid not in _suppliers():
                raise PoRefused(
                    R_NO_SUPPLIER,
                    f"this shop has no supplier {sid}, and nothing under its "
                    f"reorder level was bought from one. Nothing was written.",
                    status=404)
            raise PoRefused(
                R_NOTHING_TO_ORDER,
                f"nothing bought from {sid} is under its reorder level, so "
                f"there is no order to place. Nothing was written.")

        lines = list(group["lines"])
        if wanted is not None:
            known = {str(ln["sku_id"]) for ln in lines}
            for sku_id in wanted:
                if sku_id not in known:
                    raise PoRefused(
                        R_SKU_NOT_ON_DRAFT,
                        f"{sku_id!r} is not on this supplier's draft. Only the "
                        f"products under their reorder level and bought from "
                        f"this supplier can be ordered. Nothing was written.")
            lines = [ln for ln in lines if str(ln["sku_id"]) in wanted]
            if not lines:
                raise PoRefused(
                    R_EMPTY_SELECTION,
                    "every line was left out, so there is nothing to order. "
                    "Nothing was written.")
        if len(lines) > MAX_LINES:
            raise PoRefused(
                R_TOO_MANY_LINES,
                f"this order has {len(lines)} lines and the cap is "
                f"{MAX_LINES}. Order some of it and the rest afterwards; "
                f"nothing was written.")

        profile = _profile()
        po_id = f"po_{secrets.token_hex(6)}"
        doc: dict[str, Any] = {
            "format": PO_FORMAT,
            "po_id": po_id,
            "at": _now_iso(),
            "date": _today_label(),
            "supplier_id": sid,
            "supplier_name": group["supplier_name"],
            "supplier_phone": group["supplier_phone"],
            "supplier_on_file": group["supplier_on_file"],
            "shop_name": (profile or {}).get("name"),
            "shop_address": (profile or {}).get("address"),
            "shop_phone": (profile or {}).get("phone"),
            "lines": lines,
            "line_count": len(lines),
            "units_total": _units_of(lines),
            "note": note or None,
            # A fact about this record, written into it rather than left to be
            # inferred: an order is not a delivery.
            "stock_received": False,
            **_spend(lines),
        }

        _write_json(_po_path(po_id), doc)
        head = _append(doc)
        if head is None:
            # THE CHAIN IS THE RECORD. An order that could not be chained did
            # not happen and must not be listed as though it had, so the
            # document goes with it.
            try:
                _po_path(po_id).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001 - the refusal is what matters
                pass
            raise PoRefused(
                R_NOT_WRITTEN,
                f"the order could not be appended to {audit_path().name}, so "
                f"nothing was recorded. Check the shop directory is writable "
                f"and place the order again.")
        doc["chain_head"] = head
        _write_json(_po_path(po_id), doc)

        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "po": doc,
            "share_text": _share_text(doc),
            "print_url": f"/po/{po_id}/print",
            "print_html": _print_html(doc),
            "chain": _chain_block(),
            "stock_received": False,
            "detail": (
                f"Order {po_id} is written for "
                f"{doc['supplier_name'] or sid}: {doc['line_count']} line(s), "
                f"{doc['units_total']} packet(s). Nothing has been paid and no "
                f"stock has been received — book the delivery in on the Stock "
                f"screen when it arrives."),
        })
    except PoRefused as exc:
        return _refusal(exc)
    except _purchases.PurchaseRefused as exc:
        return _refusal(_passthrough(exc))
    except MoneyError as exc:
        return _refusal(PoRefused(
            R_COSTS_UNAVAILABLE,
            f"a recorded cost is not integer paise ({exc}). Nothing was "
            f"written."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/po")
def po_list_ep() -> JSONResponse:
    """Every order this counter has written, newest first."""
    try:
        rows = [_summary(d) for d in _all_pos()]
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "count": len(rows),
            "orders": rows,
            "chain": _chain_block(),
            "note": ("An order is a record of what was asked for. It is never "
                     "edited and never deleted, and none of it has been "
                     "received or paid."),
        })
    except PoRefused as exc:
        return _refusal(exc)
    except _purchases.PurchaseRefused as exc:
        return _refusal(_passthrough(exc))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/po/{po_id}")
def po_one_ep(po_id: str) -> JSONResponse:
    """One order in full, with the message and the address of its page."""
    try:
        doc = _read_po(_valid_po_id(po_id))
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "po": doc,
            "share_text": _share_text(doc),
            "print_url": f"/po/{doc['po_id']}/print",
            "chain": _chain_block(),
            "stock_received": False,
        })
    except PoRefused as exc:
        return _refusal(exc)
    except _purchases.PurchaseRefused as exc:
        return _refusal(_passthrough(exc))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/po/{po_id}/print")
def po_print_ep(po_id: str) -> Any:
    """The order as one printable page, exactly as `confirm` returned it.

    Same bytes as `print_html` on the confirm response: rendered from the stored
    document by the same function, so what is printed a week later is what was
    agreed on the day. A refusal here is still JSON — a browser that asked for a
    page it cannot have should be told why in the shape every other route uses.
    """
    try:
        doc = _read_po(_valid_po_id(po_id))
        return HTMLResponse(
            _print_html(doc),
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition":
                    f'inline; filename="{doc["po_id"]}.html"',
            })
    except PoRefused as exc:
        return _refusal(exc)
    except _purchases.PurchaseRefused as exc:
        return _refusal(_passthrough(exc))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


# ----------------------------------------------------------------- helpers --
#
# Below the routes because they are plumbing; the reading order that matters is
# draft, confirm, list, one.


def _valid_supplier_id(raw: Any) -> str:
    """Purchases' own id check, so the two modules agree on what an id is."""
    fn = (getattr(_purchases, "valid_supplier_id", None)
          or getattr(_purchases, "_valid_supplier_id", None))
    if fn is None:
        s = (raw or "").strip() if isinstance(raw, str) else ""
        if not re.match(r"^sup_[0-9a-f]{12}$", s):
            raise PoRefused(
                R_BAD_SUPPLIER_ID,
                f"{raw!r} is not a supplier id from this shop.")
        return s
    try:
        return str(fn(raw))
    except Exception as exc:  # noqa: BLE001 - purchases' refusal, our shape
        raise PoRefused(
            R_BAD_SUPPLIER_ID,
            str(getattr(exc, "detail", None) or exc)) from None


def _wanted_skus(body: dict[str, Any]) -> Optional[set[str]]:
    """The subset of the draft to order, or None for all of it."""
    raw = body.get("skus")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise PoRefused(
            R_BAD_SKUS,
            f'"skus" is a {type(raw).__name__}; it must be a list of product '
            f'ids, or absent to order everything on the draft.')
    out: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise PoRefused(
                R_BAD_SKUS,
                f'"skus" contains {item!r}, which is not a product id.')
        out.add(item.strip())
    if not out:
        raise PoRefused(
            R_EMPTY_SELECTION,
            '"skus" is empty, so there is nothing to order. Leave it out to '
            'order everything on the draft.')
    return out


def _note(body: dict[str, Any]) -> str:
    raw = body.get("note")
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise PoRefused(
            R_BAD_BODY, f"'note' must be text, not {type(raw).__name__}.")
    s = " ".join(raw.split())
    if len(s) > MAX_NOTE:
        raise PoRefused(
            R_TOO_LONG,
            f"the note is {len(s)} characters and the cap is {MAX_NOTE}. "
            f"Nothing was written.")
    return s


def _profile() -> Optional[dict[str, Any]]:
    """The shop's own name and address for the printed page, or None.

    An unset profile is not an error: the page prints under "Purchase order"
    and says the shop has not been named. A wholesaler can still read the list.
    """
    try:
        from . import shopadmin as _shopadmin

        doc = _shopadmin.read_profile()
        return doc if isinstance(doc, dict) else None
    except Exception:  # noqa: BLE001 - an unreadable profile is an unset one
        return None


def _append(doc: dict[str, Any]) -> Optional[str]:
    """One line on this module's chain. Returns the head, or None if it failed.

    WHAT GOES ON THE CHAIN is the order's shape and its money, not its whole
    body: the ids, the counts, the expected spend and how much of the order that
    figure covers. The supplier's phone stays out — it is in the document and on
    the printed page, and a chain line is the thing most likely to be pasted
    into a bug report.
    """
    try:
        return Ledger(audit_path()).append(
            ts=str(doc["at"]),
            module="po",
            event=EV_CONFIRMED,
            po_id=doc["po_id"],
            date=doc["date"],
            supplier_id=doc["supplier_id"],
            supplier_name=doc["supplier_name"],
            lines=doc["line_count"],
            units=doc["units_total"],
            sku_ids=[str(ln["sku_id"]) for ln in doc["lines"]],
            expected_paise=doc["expected_paise"],
            expected_is_partial=doc["expected_is_partial"],
            lines_with_no_cost=doc["lines_with_no_cost"],
            stock_received=False,
            settles_money=False,
        )
    except Exception:  # noqa: BLE001 - the caller turns None into a refusal
        return None


__all__ = [
    "PoRefused",
    "audit_path",
    "draft",
    "po_dir",
    "router",
    "shop_dir",
]
