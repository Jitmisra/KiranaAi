"""DUKAAN — the shop seen from a customer's phone.

The counter in `tools/upload_app.py` is the shopkeeper's side of the glass: a
camera, a catalogue and a till. This module is the OTHER side. The shopkeeper
prints one QR and sticks it on the shutter; a customer photographs it with the
phone already in their hand, browses the same catalogue the till prices from,
puts things in a basket, types where they live, and pays. The shopkeeper sees
the order arrive.

Nothing here is a second catalogue and nothing here is a second price. Every
name and every rupee is read from `gawaah/shop_store.py` through the till's own
`priced_skus()`, so a product taught this morning is on sale this afternoon and
a price changed in one place changes in both.

Four rules this file exists to keep, in the order they would hurt if broken:

  1. NO FORGERY PRIMITIVES. There is no code here that builds a `upi:` payload
     or a payment URL. The only payable string a customer is ever shown is the
     opaque `short_url` the gateway itself minted, fetched back from the money
     service, and checked against the same host allowlist `/qr/link` uses
     before it is rendered as something tappable. The storefront QR encodes
     THIS SERVER'S OWN ADDRESS and is refused if it ever looks like anything
     else.

  2. INTEGER PAISE. Prices are read as ints, multiplied by an integer quantity
     and summed as ints. `gawaah/money.py` validates every one at the boundary
     and renders the rupee string; no float and no division touches money.

  3. THE BROWSER IS NEVER AN AUTHOR. The phone sends sku ids and whole-number
     quantities. It cannot name a price. If it sends one anyway — per line or
     as a total — the server recomputes from the catalogue and REFUSES on
     disagreement rather than believing it. Refusing is deliberate: silently
     ignoring an asserted price would leave the customer looking at a number
     the shop never agreed to.

  4. A REFUSAL IS A RESULT. Every failure this module can honestly reach has a
     name, and the name is in the response body. Nothing here raises a 500.

AND THE SHELF, WHICH THE PHONE CANNOT SEE. `gawaah/stock.py` keeps the
shopkeeper's count and a per-product ONLINE FLOOR ("stop selling online below
N"); this module turns those into what a phone may buy:

    sellable online = on hand − units in orders not yet cancelled − floor

clamped at zero. AN ORDER DOES NOT DECREMENT STOCK — it is not a sale until the
shopkeeper packs it — but from the moment it is placed it RESERVES, so two
phones cannot both buy the last packet. A product nobody has counted has no
figure and is sold as before: null is never rounded to zero. The catalogue
carries the figure so the page can grey a card out, and `/store/order` refuses
a basket that asks for more, naming every short line and how many there are —
the greyed card is a courtesy, the refusal is the rule.

The router carries NO prefix: the paths below are already absolute and are what
a phone types. Mount it with `app.include_router(storefront.router)`.
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import os
import re
import secrets
import threading
import time
# Module scope rather than inside a function, unlike `_post_intent`'s local
# imports: `_NoRedirect` below subclasses `urllib.request.HTTPRedirectHandler`
# at class-definition time, which happens on import.
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .ledger import Ledger
from .money import MoneyError, paise, to_rupees_str

router = APIRouter()


# --------------------------------------------------------------- refusals --
#
# Every one of these is a state this module can actually reach. None is a
# guess, and each is written so a shopkeeper or a customer can act on it.

R_NO_TILL = "till_module_unavailable"
R_NO_CATALOGUE = "catalogue_unavailable"
R_BAD_BODY = "order_body_not_json"
R_EMPTY_CART = "cart_is_empty"
R_UNKNOWN_SKU = "sku_not_in_this_shop"
R_BAD_QTY = "quantity_not_a_whole_number"
R_QTY_TOO_LARGE = "quantity_beyond_this_counter"
R_TOO_MANY_LINES = "too_many_lines_in_one_order"
R_NO_NAME = "customer_name_missing"
R_NO_PHONE = "customer_phone_missing"
R_BAD_PHONE = "customer_phone_not_a_number"
R_NO_ADDRESS = "delivery_address_missing"
R_SHORT_ADDRESS = "delivery_address_too_short"
R_TOO_LONG = "field_too_long"
R_TOTAL_DISAGREES = "client_total_disagrees"
R_LINE_PRICE_DISAGREES = "client_line_price_disagrees"
R_BAD_ORDER_ID = "order_id_malformed"
R_NO_ORDER = "no_such_order"
R_NO_PHOTO = "no_photo_for_this_product"
R_BAD_STATUS = "unknown_status"
R_ILLEGAL_TRANSITION = "illegal_status_change"
R_ORDER_CLOSED = "order_is_closed"
R_NOT_PAYABLE = "order_cannot_be_paid"
R_ORDER_NOT_MONEY = "order_total_is_not_integer_paise"
R_PRICE_MOVED = "order_price_no_longer_agrees"
R_GONE_FROM_SHOP = "order_line_no_longer_on_sale"
R_WITNESS_UNWRITABLE = "order_witness_could_not_be_written"
R_WITNESS_UNSEEN = "order_witness_not_visible_to_the_money_service"
R_REFUSED_LINK = "refused_to_show_this_string"
R_NO_HOST = "cannot_tell_this_shops_address"
R_MONEY_UNREACHABLE = "money_service_unreachable"
R_LINK_DEAD = "payment_link_the_gateway_does_not_serve"
R_LINK_IS_ALIVE = "payment_link_is_still_live"
R_SHOPKEEPER_PREVIEW = "shopkeeper_cannot_order_from_their_own_shop"
R_NOT_SIGNED_IN = "customer_not_signed_in"
R_UNPROVEN_NUMBER = "customer_has_not_proved_this_number"
R_NOT_ENOUGH_STOCK = "not_enough_stock_for_these_lines"
R_INTERNAL = "storefront_internal_error"


# ----------------------------------------------------------- the lifecycle --

NEW = "new"
PREPARING = "preparing"
OUT_FOR_DELIVERY = "out_for_delivery"
DELIVERED = "delivered"
CANCELLED = "cancelled"

STATUSES = (NEW, PREPARING, OUT_FOR_DELIVERY, DELIVERED, CANCELLED)

#: An order that is still the shop's to fulfil. These hold their packets back
#: from the storefront — see `availability()`. `orders_still_wanting` uses the
#: same set for the same reason and reads it from here.
OPEN_STATUSES = (NEW, PREPARING, OUT_FOR_DELIVERY)

#: What may follow what. `cancelled` is reachable from every state that is not
#: already finished — including `out_for_delivery`, because a customer who
#: refuses at the door is a real thing that happens and pretending otherwise
#: would leave the order stuck in a state the shopkeeper cannot clear. What it
#: costs when that is wrong: an order can be cancelled after the rider left, so
#: `cancelled` alone does not mean "nothing moved". The history on the order
#: records when it was cancelled and from where, which is what settles that.
NEXT_STATUS: dict[str, tuple[str, ...]] = {
    NEW: (PREPARING, CANCELLED),
    PREPARING: (OUT_FOR_DELIVERY, CANCELLED),
    OUT_FOR_DELIVERY: (DELIVERED, CANCELLED),
    DELIVERED: (),
    CANCELLED: (),
}

#: Caps. A kirana order is a basket, not a wholesale run, and every one of these
#: bounds a thing that ends up on disk or in a total. What it costs when they
#: are wrong: a genuine bulk order is refused and the shopkeeper has to split
#: it. That is a nuisance; an unbounded write from the open internet is not.
MAX_QTY = 99
MAX_LINES = 40
MAX_NAME = 80
MAX_PHONE = 24
MAX_ADDRESS = 400
MIN_ADDRESS = 8
#: The shortest thing this shop will call a phone number. Named rather than
#: written twice: `_customer` enforces it when an order is placed and
#: `/store/customer/signin` enforces it when somebody identifies themselves, and
#: a shop that accepted a number in one place and refused it in the other would
#: let a customer sign in under a number they could never order from.
MIN_PHONE_DIGITS = 7

ORDER_FORMAT = 1
ORDER_ID_RE = re.compile(r"^ord_[0-9a-f]{12}$")


class StorefrontRefused(Exception):
    """A named refusal with a reason a human can act on.

    `extra` is STRUCTURE the page can act on beside the sentence — the short
    lines of a basket, say — merged into the refusal body under its own keys.
    The sentence is still complete on its own; `extra` exists so a page can fix
    a basket without parsing prose.
    """

    def __init__(self, reason: str, detail: str,
                 extra: Optional[dict[str, Any]] = None) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.extra = dict(extra or {})


def _refusal(exc: StorefrontRefused, status: int = 400) -> JSONResponse:
    """The shape every other endpoint in this program answers a refusal with."""
    body = {**exc.extra, "ok": False, "reason": exc.reason,
            "detail": exc.detail, "settles_money": False}
    return JSONResponse(body, status_code=status)


def _crash(exc: BaseException) -> JSONResponse:
    """The last resort. A 500 teaches the reader nothing, so there are none."""
    return JSONResponse(
        {"ok": False, "reason": R_INTERNAL,
         "detail": f"{type(exc).__name__}: {exc}", "settles_money": False},
        status_code=400,
    )


# ------------------------------------------------------------- the till --
#
# This module reads the shopkeeper's catalogue rather than keeping one, so it
# needs the till module. Imported LATE, inside functions, for two reasons:
#
#   - the till mounts this router, so importing it at module scope would be a
#     cycle;
#   - the till module is expensive (cv2, numpy, the whole vision stack) and a
#     storefront that never serves a request should not pay for it.

from gawaah import till_ref as _till_ref

#: One definition, in `gawaah/till_ref.py`. This was sixteen copies of a tuple
#: that was missing `__main__`, and every one of them was wrong the same way.
_TILL_NAMES = _till_ref.TILL_NAMES


def _till() -> Any:
    """The already-loaded till module, or a named refusal.

    LOOK IN sys.modules FIRST, AND DO NOT SKIP THAT STEP. `make serve` runs
    `uvicorn upload_app:app --app-dir tools`, so the module is registered under
    the bare name `upload_app`; the test suite does `from tools import
    upload_app` and registers it as `tools.upload_app`. Importing the other
    spelling loads a SECOND copy of the file with its own `_DEPS` cache — a
    second store handle, a second catalogue directory, and a `set_store_dir`
    in a test that silently does not reach the copy serving requests. The
    symptom would be a storefront reading a different shop from the till it is
    mounted in, with nothing anywhere saying so.

    THAT IS EXACTLY WHAT HAPPENED, and this list is why. `python
    tools/upload_app.py` -- which the file's own `__main__` block invites --
    registers the module as `__main__`, which was in neither name. So the
    storefront imported a second copy, served a catalogue of six products from
    it, and the money service could price only the three the real one held. A
    customer built a basket, pressed PAY, and got `amber_in_basket` on a line
    the shop was openly advertising.

    `__main__` is now in the list, checked by SHAPE rather than by name, since
    `__main__` is whatever process happened to start.
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
        raise StorefrontRefused(
            R_NO_TILL,
            f"tools/upload_app.py is not importable ({type(exc).__name__}: "
            f"{exc}). The storefront reads the shopkeeper's catalogue through "
            f"it and will not keep a second copy of the prices.") from None
    return upload_app


def shop_dir() -> Path:
    """Where the catalogue lives — the till's own answer, never a second one.

    This is what honours `GAWAAH_SHOP_DIR`: `upload_app.store_dir()` reads that
    environment variable, and `upload_app.set_store_dir()` redirects it for a
    test. Deriving the path here from the environment ourselves would be a
    second answer to one question, and the day a test moved the catalogue and
    the orders stayed behind is the day a harness overwrites a live shop.
    """
    return Path(_till().store_dir())


def orders_dir() -> Path:
    """Orders live NEXT TO the catalogue they were priced from."""
    return shop_dir() / "orders"


def audit_path() -> Path:
    """The storefront's own hash-chained log.

    DELIBERATELY NOT `results/audit.jsonl`. That file is held open by the money
    service in a DIFFERENT PROCESS, which keeps the chain head in memory and
    computes `prev_hash` from it. A second process appending between two of its
    writes gives it a stale head, and every line paisa writes afterwards fails
    `gawaah.ledger.verify` — `make verify-ledger` goes red and the money audit
    trail, the one thing in this program that must be beyond argument, is the
    casualty. So the orders get their own chain, in the shop directory, written
    by the one process that owns it and verifiable by exactly the same
    `verify()`.

    What it costs when this is wrong: there are two chains to walk instead of
    one, and a reader who checks only `results/audit.jsonl` will not see the
    orders. That is a documentation problem. The alternative was a corrupted
    money ledger.
    """
    return shop_dir() / "orders.audit.jsonl"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _audit(event: str, **fields: Any) -> Optional[str]:
    """Append one auditable line. Returns the new head, or None if it failed.

    NO PII EVER REACHES THIS FILE. The customer's name, phone and address stay
    in the order document, which is the shopkeeper's own record; the chain gets
    the order id, the money, the skus and a digest of the address. An audit log
    is the file most likely to be copied into a bug report.

    Best effort, but never silent: a caller that gets None says so in its
    response rather than reporting a fully-witnessed order that was not.
    """
    try:
        return Ledger(audit_path()).append(
            ts=_now_iso(), module="storefront", event=event, **fields)
    except Exception:  # noqa: BLE001 - a failed audit must not lose an order
        return None


# ------------------------------------------------------------- catalogue --


def catalogue() -> dict[str, dict[str, Any]]:
    """{sku_id -> name, price_paise, how} for everything this shop can sell.

    `priced_skus()` and not `taught_skus()`, for the reason that function
    documents: a product taught from a printed code alone has no descriptor and
    is invisible to the vector list, but it has a name and a price and a
    shopkeeper who entered it expects to be able to sell it.

    AND `offer_priced_skus()`, WHICH IS THE OFFER-AWARE ONE. paisa re-prices
    every basket through its own book, and that book applies today's offers. A
    storefront quoting the marked price therefore takes an order paisa will not
    mint — measured, the moment an offer was switched on: the customer's order
    totalled 3500 paise, paisa derived 3150, and the payment was refused with
    `scan_total_disagreement`.

    That refusal is invariant 5 working: the browser proposed a total the money
    service had not derived, and the money service declined rather than charge
    it. The fix is not to relax the check. It is for the shop to quote the price
    it is actually going to charge.
    """
    up = _till()
    try:
        return dict(up.offer_priced_skus())
    except StorefrontRefused:
        raise
    except Exception as exc:  # noqa: BLE001 - the store may be unreadable
        reason = getattr(exc, "reason", None) or R_NO_CATALOGUE
        detail = getattr(exc, "detail", None) or (
            f"the catalogue could not be read ({type(exc).__name__}: {exc})")
        raise StorefrontRefused(reason, detail) from None


def _skus_with_a_photo() -> set[str]:
    """Which products have a picture, without reading any of the pixels.

    Two places one can live, because the catalogue has two: the store keeps a
    PNG on disk and records its path, and a product taught from an ordinary
    photograph with no mat lives in the till's sidecar with a small base64
    thumbnail. Both count as "there is something to show".
    """
    up = _till()
    have: set[str] = set()
    try:
        for rec in up.load_store().all():
            if getattr(rec, "photo", None):
                have.add(rec.sku_id)
    except Exception:  # noqa: BLE001 - no store is "no photos", not an error
        pass
    try:
        for rec in up.taught_skus():
            if rec.thumb:
                have.add(rec.sku_id)
    except Exception:  # noqa: BLE001
        pass
    return have


def _photo_png(sku_id: str) -> Optional[bytes]:
    """The taught photograph of one product as PNG bytes, or None."""
    up = _till()
    try:
        data = up.load_store().photo_bytes(sku_id)
        if data:
            return bytes(data)
    except Exception:  # noqa: BLE001 - fall through to the sidecar
        pass
    try:
        for rec in up.taught_skus():
            if rec.sku_id == sku_id and rec.thumb:
                return base64.b64decode(rec.thumb)
    except Exception:  # noqa: BLE001
        return None
    return None


# ----------------------------------------------------------------- orders --


def _valid_order_id(order_id: str) -> str:
    """Checked against a strict charset BEFORE it is ever joined to a path.

    The id becomes a filename. A shape check here is what stops a request for
    `../../catalog` reading the shopkeeper's price list, and it is cheaper than
    trusting every caller downstream to remember.
    """
    s = (order_id or "").strip()
    if not ORDER_ID_RE.match(s):
        raise StorefrontRefused(
            R_BAD_ORDER_ID,
            f"{order_id!r} is not an order id from this shop. They look like "
            f"'ord_' followed by twelve hex characters.")
    return s


def _order_path(order_id: str) -> Path:
    return orders_dir() / f"{_valid_order_id(order_id)}.json"


def _read_order(order_id: str) -> dict[str, Any]:
    p = _order_path(order_id)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise StorefrontRefused(
            R_NO_ORDER,
            f"this shop has no order {order_id!r}. Nothing was changed."
        ) from None
    except Exception as exc:  # noqa: BLE001 - a corrupt file is not a crash
        raise StorefrontRefused(
            R_NO_ORDER,
            f"order {order_id!r} is on disk but could not be read "
            f"({type(exc).__name__}: {exc}). Nothing was changed.") from None
    if not isinstance(doc, dict):
        raise StorefrontRefused(
            R_NO_ORDER, f"order {order_id!r} is not an order document.")
    return doc


def _write_order(doc: dict[str, Any]) -> None:
    """Write via a temp file and rename, so a reader never sees half an order."""
    d = orders_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{doc['order_id']}.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, sort_keys=True, indent=1) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)


def _all_orders() -> list[dict[str, Any]]:
    """Every order, newest first. An unreadable file is skipped, not fatal."""
    d = orders_dir()
    out: list[dict[str, Any]] = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("ord_*.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - one bad file must not hide the rest
            continue
        if isinstance(doc, dict) and doc.get("order_id"):
            out.append(doc)
    # `at` is an ISO-8601 UTC string, so lexical order IS chronological order.
    # The id is the tiebreak so two orders placed in the same microsecond have
    # a stable order rather than a filesystem-dependent one.
    out.sort(key=lambda d: (str(d.get("at") or ""), str(d.get("order_id"))),
             reverse=True)
    return out


# ------------------------------------------------------- reading the cart --


def _text(body: dict[str, Any], key: str, *, cap: int) -> str:
    raw = body.get(key)
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise StorefrontRefused(
            R_BAD_BODY, f"{key!r} must be text, not {type(raw).__name__}.")
    s = " ".join(raw.split()) if key != "address" else raw.strip()
    if len(s) > cap:
        raise StorefrontRefused(
            R_TOO_LONG,
            f"{key} is {len(s)} characters and the cap is {cap}. Nothing was "
            f"saved.")
    return s


def _customer(body: dict[str, Any]) -> dict[str, str]:
    """Who the order is for and where it goes.

    Name and phone are REQUIRED, not only the address. An order a shopkeeper
    cannot ring back about is not an order: the one thing that reliably rescues
    a wrong flat number is a phone call. What it costs when this is wrong: a
    customer who will not give a number cannot order, and has to walk in.
    """
    name = _text(body, "name", cap=MAX_NAME)
    if not name:
        raise StorefrontRefused(
            R_NO_NAME, "a name is required — the shopkeeper has to know who is "
                       "at the door.")

    phone = _text(body, "phone", cap=MAX_PHONE)
    if not phone:
        raise StorefrontRefused(
            R_NO_PHONE, "a phone number is required. It is how a wrong address "
                        "gets fixed.")
    digits = re.sub(r"\D", "", phone)
    if len(digits) < MIN_PHONE_DIGITS:
        raise StorefrontRefused(
            R_BAD_PHONE,
            f"{phone!r} has {len(digits)} digits in it. A number that can be "
            f"dialled has at least {MIN_PHONE_DIGITS}.")

    address = _text(body, "address", cap=MAX_ADDRESS)
    if not address:
        raise StorefrontRefused(
            R_NO_ADDRESS,
            "a delivery address is required. Nothing was ordered.")
    if len(address) < MIN_ADDRESS:
        raise StorefrontRefused(
            R_SHORT_ADDRESS,
            f"{address!r} is {len(address)} characters. That is not enough for "
            f"anyone to find the door — the shortest usable address here is "
            f"{MIN_ADDRESS}.")
    return {"name": name, "phone": phone, "address": address}


def _lines(body: dict[str, Any], known: dict[str, dict[str, Any]]
           ) -> list[dict[str, Any]]:
    """The cart, re-priced from the shop's own catalogue.

    THE PHONE NAMES PRODUCTS AND COUNTS THEM. It does not price them. Every
    `price_paise` below is read out of `known`, which came from the catalogue,
    and the only use ever made of a price the client sent is to compare it and
    refuse.
    """
    items = body.get("items")
    if items is None or (isinstance(items, list) and not items):
        raise StorefrontRefused(
            R_EMPTY_CART, "there is nothing in this basket, so there is nothing "
                          "to order.")
    if not isinstance(items, list):
        raise StorefrontRefused(
            R_BAD_BODY,
            f"'items' must be a list of {{sku_id, qty}}, not "
            f"{type(items).__name__}.")

    # Merge repeats rather than refusing them: a phone that sends the same sku
    # twice means two of it, and the merged lines are echoed back in the
    # response, so nothing is combined out of sight.
    merged: dict[str, int] = {}
    order: list[str] = []
    for raw in items:
        if not isinstance(raw, dict):
            raise StorefrontRefused(
                R_BAD_BODY,
                f"every basket line must be an object with a sku_id and a qty; "
                f"found {type(raw).__name__}.")
        sku_id = raw.get("sku_id")
        if not isinstance(sku_id, str) or not sku_id.strip():
            raise StorefrontRefused(
                R_BAD_BODY, "a basket line arrived with no sku_id.")
        sku_id = sku_id.strip()
        rec = known.get(sku_id)
        if rec is None:
            raise StorefrontRefused(
                R_UNKNOWN_SKU,
                f"{sku_id!r} is not something this shop sells. Nothing was "
                f"ordered. On sale: {', '.join(sorted(known)[:6]) or 'nothing yet'}"
                f"{'…' if len(known) > 6 else ''}.")

        qty = raw.get("qty", 1)
        # bool first: True is an int in Python and a quantity of True is not a
        # thing anybody meant.
        if isinstance(qty, bool) or not isinstance(qty, int):
            raise StorefrontRefused(
                R_BAD_QTY,
                f"the quantity for {sku_id!r} is {qty!r}. A count is a whole "
                f"number — half a packet is not something a shop can hand over.")
        if qty <= 0:
            raise StorefrontRefused(
                R_BAD_QTY,
                f"the quantity for {sku_id!r} is {qty}. To remove a line, leave "
                f"it out of the basket.")
        if qty > MAX_QTY:
            raise StorefrontRefused(
                R_QTY_TOO_LARGE,
                f"{qty} of {sku_id!r} is past the {MAX_QTY} this counter takes "
                f"in one order. Call the shop for a bulk order.")

        # An asserted price is CHECKED, never used. Ignoring it quietly would
        # let a phone show one number and the shop charge another.
        claimed = raw.get("price_paise")
        if claimed is not None:
            if isinstance(claimed, bool) or not isinstance(claimed, int) \
                    or claimed != int(rec["price_paise"]):
                raise StorefrontRefused(
                    R_LINE_PRICE_DISAGREES,
                    f"this basket says {sku_id!r} costs {claimed!r}; the shop's "
                    f"catalogue says {int(rec['price_paise'])} paise. Nothing "
                    f"was ordered.")

        if sku_id not in merged:
            order.append(sku_id)
        merged[sku_id] = merged.get(sku_id, 0) + qty

    if len(order) > MAX_LINES:
        raise StorefrontRefused(
            R_TOO_MANY_LINES,
            f"this basket has {len(order)} different products and the cap is "
            f"{MAX_LINES}.")

    lines: list[dict[str, Any]] = []
    for sku_id in order:
        qty = merged[sku_id]
        if qty > MAX_QTY:
            raise StorefrontRefused(
                R_QTY_TOO_LARGE,
                f"the repeated lines for {sku_id!r} add up to {qty}, past the "
                f"{MAX_QTY} this counter takes in one order.")
        rec = known[sku_id]
        # money.paise() rejects a float, a bool and anything non-integral. If a
        # catalogue on disk ever held 21.45 instead of 2145, this is where the
        # order stops rather than where a rupee becomes approximate.
        unit = int(paise(rec["price_paise"]))
        line = unit * qty
        lines.append({
            "sku_id": sku_id,
            "name": str(rec.get("name") or sku_id),
            "qty": qty,
            "unit_paise": unit,
            "unit_rupees": to_rupees_str(paise(unit)),
            "line_paise": line,
            "line_rupees": to_rupees_str(paise(line)),
            "taught_with": str(rec.get("how") or "unknown"),
        })
    return lines


def _sum_paise(lines: list[dict[str, Any]]) -> int:
    """Integer addition, and nothing else. No float, no division, no rounding."""
    out = 0
    for ln in lines:
        out += int(paise(ln["line_paise"]))
    return int(paise(out))


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a named refusal
        raise StorefrontRefused(
            R_BAD_BODY, "this request's body is not JSON.") from None
    if not isinstance(body, dict):
        raise StorefrontRefused(
            R_BAD_BODY,
            f"this request's body is a {type(body).__name__}; it must be a "
            f"JSON object.")
    return body


def _signed_in_shopkeeper(request: Request) -> Optional[dict[str, Any]]:
    """The SHOPKEEPER on this request, if one is signed in. Never raises.

    Imported lazily, in the shape `_till()` uses, so that this module stays
    importable in a test that never wires auth up — and so that the storefront
    never becomes a reason `gawaah/auth.py` has to import back.

    `current_shopkeeper` returns None for a stranger's phone, which is every
    customer: the shutter QR carries no cookie. So the only request this can
    identify is one made from a browser that signed in AT THE COUNTER, which is
    exactly the case Defect 3 is about.
    """
    try:
        from . import auth as _auth  # noqa: WPS433 - lazy, see above

        return _auth.current_shopkeeper(request)
    except Exception:  # noqa: BLE001 - "nobody" is the answer, not an error
        return None


def _customer_view(doc: dict[str, Any]) -> dict[str, Any]:
    """What the CUSTOMER is shown about their own order.

    No address and no phone, even though the customer typed them: this response
    is reachable by anyone holding the order id, and an id in a shared browser
    history should not read back a home address.
    """
    pay = doc.get("payment") or {}
    view = {
        "ok": True,
        "settles_money": False,
        "order_id": doc.get("order_id"),
        "at": doc.get("at"),
        "status": doc.get("status"),
        "status_changed_at": doc.get("status_changed_at"),
        "lines": doc.get("lines") or [],
        "total_paise": int(doc.get("total_paise") or 0),
        "total_rupees": to_rupees_str(paise(doc.get("total_paise") or 0)),
        "paid": bool(pay.get("paid")),
        "payment_state": pay.get("state"),
        "short_url": pay.get("short_url"),
        "name": doc.get("customer", {}).get("name"),
    }
    # WHETHER THE LINK IS SOMETHING TO SHOW, not merely whether one is stored.
    # The page used to render `short_url` as a PAY button on its presence alone,
    # which is how a customer was handed a link the gateway had never issued.
    # The verdict is cached IN MEMORY and keyed on the URL — never on the order,
    # because this function is called from a GET the tracking screen polls every
    # four seconds and a read must not write (see `_LINK_VERDICTS`). So a `live`
    # link costs one probe for the life of the process and a `dead` one costs a
    # probe per `LINK_RECHECK_S`, not one per poll.
    if not view["paid"] and view["short_url"]:
        view.update(_link_health(doc))
        if view.get("link_state") in UNPAYABLE_STATES:
            view["payment_note"] = _unpayable_words(doc, view.get("link_state"))
            view["can_relink"] = True
    return view


# ----------------------------------------------------------------- routes --


@router.get("/store")
def store_ep() -> JSONResponse:
    """The public catalogue. Names, integer paise, and whether there is a photo.

    The same rows the till prices from. A product with no price is not here,
    because a shop cannot sell something it has not put a number on.
    """
    try:
        known = catalogue()
        photos = _skus_with_a_photo()
        shelf = availability()
        items = []
        for sku_id in sorted(known):
            rec = known[sku_id]
            price = int(paise(rec["price_paise"]))
            item = {
                "sku_id": sku_id,
                "name": str(rec.get("name") or sku_id),
                "price_paise": price,
                "price_rupees": to_rupees_str(paise(price)),
                "has_photo": sku_id in photos,
                "photo_url": (f"/store/photo/{sku_id}" if sku_id in photos
                              else None),
                "taught_with": str(rec.get("how") or "unknown"),
                # THE SHELF, ON EVERY CARD. An item at or under its floor is
                # still listed — a customer should see what the shop normally
                # carries — and says it is out; the page greys it and the
                # order route refuses it. `available_units` is None, never 0,
                # for a product nobody has counted.
                **_customer_stock(shelf["items"].get(sku_id)),
            }
            # AN OFFER THE CUSTOMER CANNOT SEE IS AN OFFER THE SHOP IS NOT
            # MAKING. `offer_priced_skus()` already put the discounted price in
            # `price_paise` — that is the number paisa will charge — and rode
            # the shelf-edge price alongside it as `marked_paise` with the
            # difference as `off_paise`. Pass all three through so the page can
            # show the strike-through instead of a price that is quietly lower
            # than the shelf. Guarded the same way every money field here is:
            # `paise()` refuses a float or a bool, and a row whose marked price
            # is not actually above the charged one carries no discount.
            off = rec.get("off_paise")
            marked = rec.get("marked_paise")
            if not isinstance(off, bool) and isinstance(off, int) and off > 0 \
                    and not isinstance(marked, bool) and isinstance(marked, int):
                marked_p = int(paise(marked))
                if marked_p > price:
                    item["marked_paise"] = marked_p
                    item["marked_rupees"] = to_rupees_str(paise(marked_p))
                    item["off_paise"] = int(paise(off))
            items.append(item)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "count": len(items),
            "items": items,
            "delivery": {
                "statuses": list(STATUSES),
                "max_qty_per_line": MAX_QTY,
                "max_lines": MAX_LINES,
            },
            # Whether the figures above mean anything. `figures: false` is a
            # stock module that could not answer, and every card then says
            # "no stock figure" — not a shop with nothing on the shelf.
            "stock": {
                "figures": bool(shelf["figures"]),
                "error": shelf["error"],
                "out_of_stock": sum(1 for i in items if i["out_of_stock"]),
                "note": STOCK_NOTE,
            },
        })
    except StorefrontRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        # The same refusal placing an order already gives, for the same fault
        # one step earlier. `int(paise(x))` refuses a float price here now,
        # where `paise(int(x))` used to truncate it and put a wrong rupee on a
        # shelf — so this is the branch that swap made reachable, and it needs
        # a name rather than `storefront_internal_error`, which would blame
        # this module for a number in the catalogue.
        return _refusal(StorefrontRefused(
            R_NO_CATALOGUE,
            f"a price in this shop's catalogue is not integer paise ({exc}). "
            f"Nothing is listed, because a shop that cannot say what something "
            f"costs must not put it on a shelf."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/store/photo/{sku_id}")
def store_photo_ep(sku_id: str):
    """The photograph taught for one product, if there is one.

    Cached for an hour on purpose. The picture only changes when the shopkeeper
    re-teaches the product, and a phone on a shop's wifi re-downloading the
    whole catalogue's images on every scroll is the difference between a page
    that feels like a shop and one that feels like a form.
    """
    try:
        known = catalogue()
        if sku_id not in known:
            raise StorefrontRefused(
                R_UNKNOWN_SKU, f"{sku_id!r} is not something this shop sells.")
        data = _photo_png(sku_id)
        if not data:
            raise StorefrontRefused(
                R_NO_PHOTO,
                f"{sku_id!r} was taught without a photograph — from its printed "
                f"code alone — so there is no picture to show.")
        return Response(data, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"})
    except StorefrontRefused as exc:
        return _refusal(exc, status=404 if exc.reason in
                        (R_UNKNOWN_SKU, R_NO_PHOTO) else 400)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/store/order")
async def store_order_ep(request: Request) -> JSONResponse:
    """Place an order. The server prices it; the phone only names things.

    Body: {items: [{sku_id, qty}], name, phone, address}. An optional
    `total_paise` is compared against the shop's own arithmetic and refused on
    disagreement — never believed, and never quietly ignored.
    """
    try:
        # DEFECT 3, AND IT IS REFUSED HERE RATHER THAN HIDDEN IN THE PAGE.
        #
        # A shopkeeper on `/#/shop` is looking at their own shop front to see
        # what a customer sees. They are not a customer, and an order they place
        # is not a sale: it writes a real order file, mints a real payment link
        # against the shop's own gateway account, and puts a line in the books
        # for goods that were never sold to anybody. The shop then owes itself
        # money, and the day's takings are wrong by however many times somebody
        # pressed the button while demonstrating the product.
        #
        # The button is hidden on the page as well, because a control that
        # cannot work should not be offered. But hiding a button is a courtesy
        # and this is the rule: the page is one client, `curl` is another, and a
        # rule that only exists in the client is not a rule. Checked FIRST, so a
        # preview order is refused before the basket is even priced.
        who = _signed_in_shopkeeper(request)
        if who is not None:
            raise StorefrontRefused(
                R_SHOPKEEPER_PREVIEW,
                f"this browser is signed in to the counter as "
                f"{who.get('name') or 'the shopkeeper'}, so this is a preview of "
                f"what a customer sees and not a customer's order. Nothing was "
                f"ordered and nothing was minted. To take an order over the "
                f"counter, use the till; to try the storefront as a customer "
                f"does, open it in a private window or sign out first.")

        body = await _json_body(request)
        known = catalogue()
        lines = _lines(body, known)
        customer = _customer(body)
        total = _sum_paise(lines)

        claimed_total = body.get("total_paise")
        if claimed_total is not None:
            if isinstance(claimed_total, bool) or \
                    not isinstance(claimed_total, int) or \
                    claimed_total != total:
                raise StorefrontRefused(
                    R_TOTAL_DISAGREES,
                    f"this basket says it comes to {claimed_total!r}; the shop "
                    f"re-priced it from its own catalogue at {total} paise. "
                    f"Nothing was ordered.")

        now = _now_iso()
        order_id = "ord_" + secrets.token_hex(6)
        doc = {
            "format": ORDER_FORMAT,
            "order_id": order_id,
            "at": now,
            "status": NEW,
            "status_changed_at": now,
            "history": [{"at": now, "from": None, "to": NEW,
                         "by": "customer"}],
            "customer": customer,
            "lines": lines,
            "total_paise": total,
            "total_rupees": to_rupees_str(paise(total)),
            # One session id per ORDER, not one per press of PAY. paisa keys
            # its intents on (session_id, cycle, amount) and hands that nonce
            # to the gateway, so a retry under the same id replays the link
            # that already exists instead of minting a second live one.
            "payment": {"session_id": f"shop_{order_id}", "paid": False,
                        "state": None, "short_url": None, "minted_at": None},
        }
        # THE SHELF IS CHECKED AND THE ORDER WRITTEN AS ONE STEP. The check
        # reads the orders already on disk as reservations, so with the lock
        # held the second of two phones asking for the last packet sees the
        # first one's order and is refused by name. Nothing awaits inside.
        # This is the rule; the greyed-out card on the page is the courtesy.
        with _ORDER_LOCK:
            _check_stock(lines)
            _write_order(doc)
        head = _audit(
            "order.placed",
            order_id=order_id,
            total_paise=total,
            lines=[{"sku_id": ln["sku_id"], "qty": ln["qty"],
                    "unit_paise": ln["unit_paise"]} for ln in lines],
            # The chain gets a digest, not a doorstep. See _audit.
            address_sha256=hashlib.sha256(
                customer["address"].encode("utf-8")).hexdigest(),
            status=NEW,
            minted=False,
        )
        return JSONResponse({
            **_customer_view(doc),
            "audited": head is not None,
            "note": ("This order is a request to the shop. Nothing has been "
                     "charged and nothing settles until the shopkeeper's "
                     "gateway says it did."),
        })
    except StorefrontRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(StorefrontRefused(
            R_NO_CATALOGUE,
            f"a price in this shop's catalogue is not integer paise ({exc}). "
            f"Nothing was ordered."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/store/order/{order_id}")
def store_order_view_ep(order_id: str) -> JSONResponse:
    """The customer's view of their own order: where it is, and what it costs.

    If a payment link was minted and has not settled yet, the money service is
    asked once, here, whether it has. THE ANSWER COMES FROM PAISA AND NOWHERE
    ELSE — this route can record that a payment happened, never decide it, and
    paisa itself only says so on a signature-verified webhook.
    """
    try:
        doc = _read_order(order_id)
        doc = _refresh_payment(doc)
        return JSONResponse(_customer_view(doc))
    except StorefrontRefused as exc:
        return _refusal(exc, status=404 if exc.reason == R_NO_ORDER else 400)
    except MoneyError as exc:
        # An order whose stored total is not integer paise cannot be rendered
        # as rupees, and the honest answer is to say so rather than to round it
        # into something readable. Named here rather than falling through to
        # `storefront_internal_error`, which would describe a bug in this
        # module instead of a fact about that order.
        return _refusal(StorefrontRefused(
            R_ORDER_NOT_MONEY,
            f"order {order_id} has a total this shop cannot read as money "
            f"({exc}). Nothing is shown for it, and it cannot be paid."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


def orders_still_wanting(sku_id: str) -> list[dict[str, Any]]:
    """Orders that are not finished and still contain `sku_id`.

    WHY THIS IS NEEDED WHERE IT IS CALLED. Deleting a product removes every
    place that could price it -- which is right, and thorough -- but an order
    a customer already placed keeps its lines. The money service re-derives
    every rupee from its own book at mint time and will not find that sku, so
    the order is refused with `amber_in_basket` and can never be paid.

    That happened. Four orders were left holding three products that had been
    cleared out of the catalogue, and the customer pressing PAY on a 431.50
    basket got a refusal naming a line the shop had been openly selling an
    hour earlier. The refusal was correct. The silence at delete time was not.

    Finished orders are excluded: a delivered order is a record, not a
    liability, and nobody is going to pay it again.
    """
    live = set(OPEN_STATUSES)
    out: list[dict[str, Any]] = []
    for o in _all_orders():
        if o.get("status") not in live:
            continue
        for line in (o.get("lines") or []):
            if isinstance(line, dict) and line.get("sku_id") == sku_id:
                out.append({"order_id": o.get("order_id"),
                            "status": o.get("status"),
                            "qty": line.get("qty")})
                break
    return out


# ------------------------------------------------------------- the shelf --
#
# What a phone may buy, derived from three things none of which this module
# owns: the shopkeeper's count and floor (gawaah/stock.py, which reads the
# count through gawaah/manage.py), and the orders in this directory. Nothing
# here is stored; it is recomputed on every read from the files that are.

#: Placing an order and checking the shelf happen under one lock, so two
#: phones asking for the last packet in the same instant cannot both be told
#: yes. Per-process, like every other lock here: one till serves one shop.
_ORDER_LOCK = threading.Lock()

STOCK_NOTE = (
    "Available online is what is on the shelf, minus what other customers "
    "have already ordered and the shop has not yet delivered or cancelled, "
    "minus any packets the shop keeps back for the counter. An order reserves "
    "its packets the moment it is placed; nothing leaves the count until the "
    "shop packs it.")


def _parse_iso(value: Any) -> Optional[_dt.datetime]:
    """An ISO-8601 stamp as this program writes them, or None. A naive stamp
    is read as UTC, the same assumption manage.py and stock.py make."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def _stock_figures() -> tuple[dict[str, dict[str, Any]], Optional[str]]:
    """stock.py's rows keyed by sku, or an empty map and the reason why.

    Imported late for the same reason `_till()` is: a storefront that never
    serves a request should not load the inventory derivation, and a test that
    mounts this router alone must still import.

    A stock module that cannot answer is NOT an empty shelf. It is reported as
    "no figures", every product is sold as it was before the floor existed,
    and the reason travels with the catalogue so the page can say so. Turning
    a refusal into `0 available` would close the shop over a broken sidecar.
    """
    try:
        from . import stock as _stock  # noqa: WPS433 - deliberately late

        rows = _stock.stock_rows()["items"]
    except Exception as exc:  # noqa: BLE001 - "no figures" is the answer
        reason = getattr(exc, "reason", None) or type(exc).__name__
        detail = getattr(exc, "detail", None) or str(exc)
        return {}, f"{reason}: {detail}"
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        sku_id = row.get("sku_id")
        if isinstance(sku_id, str) and sku_id:
            out[sku_id] = row
    return out, None


def _whole(value: Any) -> Optional[int]:
    """An int that is not a bool, or None. Never a float rounded."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _reservations(figures: dict[str, dict[str, Any]]) -> dict[str, dict[str, int]]:
    """{sku -> units held back from the storefront by orders}, in two piles.

    `open`       new, preparing, out for delivery. The packets are still on the
                 shelf and were counted, and they are promised to somebody.
    `delivered`  delivered AFTER the shelf was last counted. The packets are
                 gone, but nothing subtracted them: a delivery is not a bill,
                 so manage.py's "billed since count" never sees it. Kept here
                 until the next count, which supersedes it the way a count
                 supersedes a movement in stock.py. A delivery from BEFORE the
                 count is not held: the count already saw the gap.

    Cancelled orders hold nothing. An order with no readable status, or a
    delivery with no readable timestamp, is held rather than released — the
    direction that under-sells is the one that does not sell the same packet
    twice.

    ALL INTEGERS. Quantities are read with `_whole`, so a hand-edited order
    file carrying 2.0 contributes nothing rather than a float.
    """
    out: dict[str, dict[str, int]] = {}
    open_set = set(OPEN_STATUSES)
    for o in _all_orders():
        status = o.get("status")
        if status == CANCELLED:
            continue
        done_at = _parse_iso(o.get("status_changed_at")) if status == DELIVERED else None
        for line in (o.get("lines") or []):
            if not isinstance(line, dict):
                continue
            sku_id = line.get("sku_id")
            qty = _whole(line.get("qty"))
            if not isinstance(sku_id, str) or not sku_id or qty is None or qty <= 0:
                continue
            pile = out.setdefault(sku_id, {"open": 0, "delivered": 0})
            if status in open_set:
                pile["open"] += qty
            elif status == DELIVERED:
                row = figures.get(sku_id) or {}
                counted_at = _parse_iso(row.get("counted_at"))
                if counted_at is None or done_at is None or done_at > counted_at:
                    pile["delivered"] += qty
            else:
                # A status this module does not know. Held, not released.
                pile["open"] += qty
    return out


def availability() -> dict[str, Any]:
    """What each product's shelf allows a phone to buy, and why.

    Returns {"items": {sku -> figure}, "figures": bool, "error": str|None}.
    Every figure carries the whole derivation so a shopkeeper can check it by
    hand: on hand, the two reservation piles, the floor, and the result.

        available = max(0, on_hand − open − delivered_since_count − floor)

    `available` is None — not 0 — for a product nobody has counted, and such a
    product is never out of stock. That is the difference between "we have
    none" and "we do not know", and the storefront must not collapse it.
    """
    figures, error = _stock_figures()
    reserved = _reservations(figures)
    items: dict[str, dict[str, Any]] = {}
    for sku_id, row in figures.items():
        on_hand = _whole(row.get("on_hand_units"))
        floor = _whole(row.get("online_floor"))
        if floor is None or floor < 0:
            floor = 0
        pile = reserved.get(sku_id, {"open": 0, "delivered": 0})
        held = pile["open"] + pile["delivered"]
        if on_hand is None:
            available: Optional[int] = None
            after_orders: Optional[int] = None
            out = False
            why = ("Nothing has been counted for this product, so there is no "
                   "figure to sell against. It is sold as before; a zero here "
                   "would be a claim.")
        else:
            after_orders = on_hand - held
            room = after_orders - floor
            available = room if room > 0 else 0
            out = available == 0
            why = (f"{on_hand} on hand − {pile['open']} in open orders − "
                   f"{pile['delivered']} delivered since the last count − "
                   f"{floor} kept back for the counter = {room}"
                   + (", so none can be sold online." if out else
                      f", so {available} can be sold online."))
        items[sku_id] = {
            "sku_id": sku_id,
            "name": row.get("name"),
            "on_hand_units": on_hand,
            "counted_at": row.get("counted_at"),
            "basis": row.get("basis"),
            "reserved_open_units": pile["open"],
            "reserved_delivered_units": pile["delivered"],
            "reserved_units": held,
            "shelf_after_orders": after_orders,
            "online_floor": floor,
            "available_units": available,
            "out_of_stock": out,
            "why": why,
        }
    # Orders can hold a product the stock module has no row for — deleted from
    # the catalogue, say. Reported so the reservation list adds up to the
    # orders on disk, and so a re-taught product does not start at zero held.
    for sku_id, pile in reserved.items():
        if sku_id in items:
            continue
        items[sku_id] = {
            "sku_id": sku_id, "name": None, "on_hand_units": None,
            "counted_at": None, "basis": None,
            "reserved_open_units": pile["open"],
            "reserved_delivered_units": pile["delivered"],
            "reserved_units": pile["open"] + pile["delivered"],
            "shelf_after_orders": None, "online_floor": 0,
            "available_units": None, "out_of_stock": False,
            "why": ("Orders hold this product but the stock module has no row "
                    "for it — it is not in the catalogue, or the figures could "
                    "not be read."),
        }
    return {"items": items, "figures": error is None, "error": error,
            "note": STOCK_NOTE}


def _customer_stock(fig: Optional[dict[str, Any]]) -> dict[str, Any]:
    """The part of a figure a stranger's phone is shown.

    The floor itself stays off the wire: it is the shopkeeper's arrangement
    with the counter, and `available_units` already has it subtracted. What
    goes is the number that decides the button and whether the card is out.

    THE COUNT OF WHAT OTHER CUSTOMERS ARE HOLDING DOES NOT GO, AND USED TO.
    The reasoning for sending it was sound as far as it went: a customer
    standing in front of a shelf with ten packets on it, told they may have
    three, deserves to know why. But `/store` is open to anybody with the
    shutter link, and an exact reservation count is a business figure — poll
    it through the day and you have read the shop's order book. So the
    SENTENCE stays and the NUMBER goes: "some are held for orders already
    placed" answers the customer's question and counts nothing for anyone
    else. `reserved_units` is kept on the wire as a 0/1 flag rather than a
    tally, because the page uses it only to decide whether to say the
    sentence.
    """
    if fig is None:
        return {"available_units": None, "out_of_stock": False,
                "reserved_units": 0,
                "stock_note": "no stock figure — the shop has not counted this"}
    available = fig["available_units"]
    if available is None:
        note = "no stock figure — the shop has not counted this"
    elif fig["out_of_stock"]:
        note = "out of stock"
    else:
        note = f"{available} available"
    held = int(fig["reserved_open_units"])
    if held > 0 and available is not None:
        note += " · some are held for orders already placed"
    return {"available_units": available,
            "out_of_stock": bool(fig["out_of_stock"]),
            # A FLAG, NOT A TALLY. See the note above: the page needs to know
            # whether to explain, not how many to count.
            "reserved_units": 1 if held > 0 else 0,
            "stock_note": note}


def _check_stock(lines: list[dict[str, Any]]) -> None:
    """Refuse a basket that asks for more than the shelf allows, BY LINE.

    Every short line is named with what was asked and what there is, in the
    sentence and again as structure under `lines`, so the page can set the
    basket to the numbers the shop will accept instead of guessing. Lines that
    are fine are not mentioned; a product with no figure is never short.

    Called under `_ORDER_LOCK`, with the order write, so the check and the
    write are one step: the second of two phones asking for the last packet
    reads the first one's order as a reservation.
    """
    figures = availability()["items"]
    short: list[dict[str, Any]] = []
    for ln in lines:
        fig = figures.get(ln["sku_id"])
        if fig is None or fig["available_units"] is None:
            continue
        available = int(fig["available_units"])
        asked = int(ln["qty"])
        if asked > available:
            short.append({
                "sku_id": ln["sku_id"],
                "name": ln["name"],
                "asked": asked,
                "available": available,
                "out_of_stock": available == 0,
            })
    if not short:
        return
    said = "; ".join(
        f"{s['name']}: {s['asked']} asked, "
        + ("out of stock" if s["out_of_stock"] else f"{s['available']} available")
        for s in short)
    raise StorefrontRefused(
        R_NOT_ENOUGH_STOCK,
        f"{said}. Nothing was ordered. Available means on the shelf minus "
        f"what other customers have already ordered; change the basket to "
        f"these numbers and send it again.",
        extra={"lines": short, "figures": True})


@router.get("/orders")
def orders_ep() -> JSONResponse:
    """The shopkeeper's list, newest first. This side sees the address."""
    try:
        rows = _all_orders()
        counts: dict[str, int] = {s: 0 for s in STATUSES}
        for d in rows:
            s = str(d.get("status") or "")
            if s in counts:
                counts[s] += 1
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "count": len(rows),
            "counts": counts,
            "orders": rows,
            "statuses": list(STATUSES),
            "next_status": {k: list(v) for k, v in NEXT_STATUS.items()},
        })
    except StorefrontRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/orders/stock")
def orders_stock_ep() -> JSONResponse:
    """The shopkeeper's view of the shelf as the storefront sees it.

    One row per product with the whole derivation — on hand, the two
    reservation piles, the floor, and what a phone may buy — so the Products
    screen can put "online: 3 available (2 in open orders)" beside the count
    it edits. Under `/orders` rather than `/store` because the floor and the
    open-order totals are the shop's books, and `/store*` is open to strangers.

    Declared before `/orders/{order_id}/status` by position; the two cannot
    collide anyway (GET against POST, two segments against three), but the
    order is kept so nobody has to prove that when the next route is added.
    """
    try:
        shelf = availability()
        rows = sorted(shelf["items"].values(), key=lambda r: str(r["sku_id"]))
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "count": len(rows),
            "figures": bool(shelf["figures"]),
            "error": shelf["error"],
            "out_of_stock": sum(1 for r in rows if r["out_of_stock"]),
            "reserved_open_units": sum(int(r["reserved_open_units"]) for r in rows),
            "items": rows,
            "note": STOCK_NOTE,
            "open_statuses": list(OPEN_STATUSES),
        })
    except StorefrontRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/orders/{order_id}/status")
async def orders_status_ep(order_id: str, request: Request) -> JSONResponse:
    """Move one order along. An illegal move is refused by name, never applied.

    Body: {"status": "preparing"}. The legal moves are published by GET /orders
    so the shopkeeper's page draws only the buttons that will work.
    """
    try:
        body = await _json_body(request)
        want = body.get("status")
        if not isinstance(want, str) or not want.strip():
            raise StorefrontRefused(
                R_BAD_STATUS,
                f"no status was given. This shop knows: {', '.join(STATUSES)}.")
        want = want.strip()
        if want not in STATUSES:
            raise StorefrontRefused(
                R_BAD_STATUS,
                f"{want!r} is not a status this shop knows. It knows: "
                f"{', '.join(STATUSES)}.")

        doc = _read_order(order_id)
        was = str(doc.get("status") or NEW)
        if was not in NEXT_STATUS:
            raise StorefrontRefused(
                R_BAD_STATUS,
                f"order {order_id} is in state {was!r}, which this shop does "
                f"not know how to move on from.")
        allowed = NEXT_STATUS[was]
        if want == was:
            # Not an error and not a change. Saying so beats writing a second
            # history line that records nothing happening.
            raise StorefrontRefused(
                R_ILLEGAL_TRANSITION,
                f"order {order_id} is already {was!r}. Nothing was changed.")
        if not allowed:
            raise StorefrontRefused(
                R_ORDER_CLOSED,
                f"order {order_id} is {was!r} and that is the end of it. "
                f"Nothing was changed.")
        if want not in allowed:
            raise StorefrontRefused(
                R_ILLEGAL_TRANSITION,
                f"an order that is {was!r} cannot become {want!r}. From here it "
                f"can only become {' or '.join(allowed)}. Nothing was changed.")

        now = _now_iso()
        doc["status"] = want
        doc["status_changed_at"] = now
        history = doc.get("history")
        if not isinstance(history, list):
            history = []
        history.append({"at": now, "from": was, "to": want,
                        "by": "shopkeeper"})
        doc["history"] = history
        _write_order(doc)
        head = _audit("order.status", order_id=doc["order_id"],
                      **{"from": was, "to": want},
                      total_paise=int(doc.get("total_paise") or 0),
                      minted=False)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "order_id": doc["order_id"],
            "was": was,
            "status": want,
            "next_status": list(NEXT_STATUS[want]),
            "audited": head is not None,
            "order": doc,
        })
    except StorefrontRefused as exc:
        return _refusal(exc, status=404 if exc.reason == R_NO_ORDER else 400)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


# ------------------------------------------------------------------ money --
#
# INVARIANT 6, spelled out because this is the file where it would be easiest to
# break. There is no UPI payload here, no payment URL template and no second
# gateway client. The storefront writes down what the customer asked for, hands
# the money service an ID and a total, and paisa RE-PRICES the whole basket from
# its own price book before it calls the gateway. If the two disagree by one
# paisa, nothing is minted. The only payable string that ever reaches a phone is
# `short_url`, which the gateway issued and this program merely repeats.


def _paisa_base() -> str:
    """Where the money service lives — the till's own constant, not a new one."""
    return str(getattr(_till(), "PAISA_BASE",
                       os.environ.get("GAWAAH_PAISA_URL",
                                      "http://127.0.0.1:8788")))


def witness_dir() -> Path:
    """Where the MONEY SERVICE reads witnesses. Asked of the reader, not guessed.

    THE BUG THIS FUNCTION IS THE FIX FOR, AND IT WAS THE WHOLE PAYMENT PATH.
    This module used to write the order's witness to `upload_app.scans_dir()`,
    which is `store_dir().parent / "scans"` — a fact about the TILL'S directory
    layout, in which the catalogue happens to sit one level below the data
    directory. paisa does not know that layout and never agreed to it. It reads
    `GAWAAH_SCAN_DIR`, or `<GAWAAH_DATA_DIR>/scans`, and nothing else.

    The two answers coincide only when the shop directory is a child of the data
    directory. Point them anywhere else — a scratch shop with both variables set
    to one tmp_path, which is what this project's own house rule for tests
    prescribes — and the till writes the witness one level ABOVE the directory
    paisa searches. Measured, end to end, on a shop with one product in it:

        POST /store/order            -> 200  ord_d0aa4191c87d  "20.00"  new
        POST /store/order/.../pay    -> 400
             {"reason": "scan_not_found",
              "detail": "no scan witness 'orde349f24e2cdc452e1a' on this counter"}

    The record had been written correctly, in the right format, half a second
    earlier. It was simply not where the only program that reads it looks. The
    customer's PAY button was a dead end and the refusal named the witness
    rather than the misconfiguration, so it read like the order was at fault.

    So this asks paisa. `SCAN_DIR_ENV` and `DEFAULT_SCAN_DIR` are imported from
    the module that does the loading, and the precedence below is a transcription
    of `load_scan_witness`, not a second opinion about it. The till's own
    directory is the LAST resort rather than the first, and it is kept because
    with neither variable set paisa falls back to the RELATIVE path
    `results/scans` — resolved against whatever directory the money service was
    started from — while the till knows the absolute one. That is the shipped
    `make serve` / `make serve-money` layout, and it must not move.
    """
    from . import paisa as _paisa

    env = os.environ.get(_paisa.SCAN_DIR_ENV)
    if env:
        return Path(env)
    data_dir = os.environ.get("GAWAAH_DATA_DIR")
    if data_dir:
        return Path(data_dir) / "scans"
    try:
        return Path(_till().scans_dir())
    except Exception:  # noqa: BLE001 - a till that cannot say still has a default
        return Path(_paisa.DEFAULT_SCAN_DIR)


#: Bumped when the shape below changes in a way a reader must notice.
WITNESS_FORMAT = 2


def _write_witness(doc: dict[str, Any]) -> str:
    """An ORDER WITNESS: these lines came from an order this server priced.

    A DISTINCT KIND OF RECORD, AND IT SAYS SO IN ITS OWN FIELDS. A scan witness
    is testimony about a photograph — `POST /scan` decoded these payloads out of
    these pixels at this moment, and `frame_sha256` says which pixels. A phone
    is not the counter's camera and never will be, so this record claims nothing
    of the sort. `kind` is `"order"`, `read_by` is `"storefront"`, the id is
    prefixed `ord` rather than `scn_`, and it carries the order it was derived
    from, when that order was placed, and the quantity behind each line. A
    shopkeeper reading the scans directory can tell at a glance which records
    are photographs and which are orders, and nothing here pretends a camera saw
    anything.

    WHAT IT DOES NOT DO IS BYPASS A SINGLE CHECK. It is deliberately loadable by
    `paisa.load_scan_witness` and re-priced by `paisa.rerun_scan`, because there
    must not be a second mint path — FAILURES.md records why at length ("Two
    mint paths would be two places for money to behave differently"). So every
    guard that stands between a camera basket and a rupee stands here too:

      * every line is re-resolved through paisa's own binding table, and the
        `sku_id` written below is COMPARED against it, never trusted — a
        binding changed between the order and the charge refuses with
        `code_names_a_different_product`;
      * every line is re-priced from paisa's OWN price book. The `price_paise`
        written below is provenance for a human reading the file. `rerun_scan`
        does not read it and cannot be made to;
      * a line paisa cannot price BLOCKS the mint as `amber_in_basket`. It is
        not dropped, and the total is never quietly short;
      * the sum is compared against the order's total in integer paise and one
        paisa of disagreement refuses with `scan_total_disagreement`.

    Invariant 5 is therefore untouched: the money service still re-derives every
    rupee from its own book, and this record gives it names and counts to derive
    from — never an amount to accept.

    ON FRESHNESS. `rerun_scan` refuses a witness older than 900s, and this one is
    written at the moment PAY is pressed, so that gate never fires for an order.
    That is the same rule the counter follows and not a way around it: the till's
    CHARGE photographs the counter when you press it rather than minting the
    basket it accumulated earlier, precisely so the evidence is contemporaneous
    with the charge. An order witness minted at rest — written when the order was
    placed and spent an hour later — would be the permanent charge voucher this
    file's history already paid for once.

    One line per unit, because `rerun_scan` prices a line at a time; three
    packets of biscuits is three lines, exactly as it would be on the counter.
    """
    up = _till()
    prefix = str(getattr(up, "QR_PREFIX", "gawaah:"))
    lines: list[dict[str, Any]] = []
    witnessed = 0
    i = 0
    for ln in doc["lines"]:
        for _ in range(int(ln["qty"])):
            lines.append({
                "id": i,
                "code": f"{prefix}{ln['sku_id']}",
                "format": "STOREFRONT",
                "box": None,
                "read_by": "storefront",
                "sku_id": ln["sku_id"],
                "name": ln["name"],
                "price_paise": int(ln["unit_paise"]),
                "qty_on_the_order": int(ln["qty"]),
                "reason": "ordered_by_sku",
            })
            witnessed += int(ln["unit_paise"])
            i += 1
    scan_id = "ord" + secrets.token_hex(9)
    witness = {
        "scan_id": scan_id,
        "at": _now_iso(),
        # WHAT THIS RECORD IS. Not a photograph, and it does not claim to be.
        "kind": "order",
        "witness_format": WITNESS_FORMAT,
        "source": "storefront",
        "read_by": "storefront",
        "evidence": ("an order placed on this shop's storefront and priced by "
                     "this server from its own catalogue; no camera was "
                     "involved and no frame was decoded"),
        "frame_sha256": None,
        "order_id": doc["order_id"],
        "order_placed_at": doc.get("at"),
        "order_total_paise": int(doc.get("total_paise") or 0),
        "codes_found": len(lines),
        "distinct_codes": len({ln["code"] for ln in lines}),
        "lines": lines,
        "witnessed_paise": witnessed,
    }
    d = witness_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{scan_id}.json").write_text(
            json.dumps(witness, sort_keys=True, separators=(",", ":")),
            encoding="utf-8")
    except OSError as exc:
        raise StorefrontRefused(
            R_WITNESS_UNWRITABLE,
            f"this order's witness could not be written to {d} ({exc.strerror or exc}). "
            f"Nothing was minted, because there would have been nothing for the "
            f"money service to re-price.") from None
    return scan_id


def _confirm_witness_is_readable(scan_id: str) -> None:
    """Read the record back the way paisa will, before asking paisa to mint.

    One file read, and it buys the difference between a refusal that names the
    misconfiguration and one that names the customer's order. `scan_not_found`
    is a true statement about a witness id and a useless one to everybody who
    reads it: the shopkeeper cannot tell whether their customer did something
    wrong, and the customer is told their order does not exist.

    Called through paisa's OWN loader so this cannot drift into a second
    definition of "readable" — the same charset gate, the same directory rule,
    the same JSON. When the money service runs in another process with another
    `GAWAAH_DATA_DIR` this check still passes and the mint still refuses; that
    case is caught on the way back instead, in the pay route.

    THE DATA DIRECTORY IS DERIVED FROM `witness_dir()`, NOT READ FROM THE
    ENVIRONMENT AGAIN. Reading `GAWAAH_DATA_DIR` here would make this function
    the second place that decides where witnesses live, and it would be wrong
    exactly when the variable is unset — `load_scan_witness` then falls back to
    the RELATIVE `results/scans`, so a scratch shop whose witness had been
    written correctly to its own tmp directory would fail its own readback and
    refuse a mint that was about to succeed. Every branch of `witness_dir()`
    ends in a `scans` directory whose parent is the data directory paisa would
    be given, so handing back that parent reproduces the loader's own answer.
    """
    from . import paisa as _paisa

    if _paisa.load_scan_witness(scan_id, str(witness_dir().parent)) is None:
        raise StorefrontRefused(R_WITNESS_UNSEEN, _witness_unseen_detail(scan_id))


def _witness_unseen_detail(scan_id: str) -> str:
    """Both paths, in the refusal, because the fix is to make them one path."""
    return (
        f"this order's witness {scan_id!r} was written to {witness_dir()}, and "
        f"the money service cannot read it back. The two services disagree about "
        f"where witnesses live: the storefront writes where GAWAAH_SCAN_DIR — or "
        f"failing that GAWAAH_DATA_DIR/scans — points, and the money service "
        f"must be started with the SAME GAWAAH_DATA_DIR. Nothing was minted, "
        f"and nothing about this order is wrong."
    )


def _post_intent(session_id: str, amount_paise: int,
                 scan_id: str) -> tuple[int, dict[str, Any]]:
    """The one call to the money service. Three fields, none of them evidence.

    The same forward the till's `/api/money/mint` makes, built field by field
    for the same reason: this process adds nothing to the body and is not a step
    in the decision. It cannot be reused as a function — it is a route handler
    over a Request — so the three lines exist twice rather than the till and the
    storefront reaching the gateway two different ways.
    """
    import urllib.error
    import urllib.request

    payload = json.dumps({
        "session_id": str(session_id),
        "amount_paise": int(amount_paise),
        "scan": {"scan_id": str(scan_id)},
    }).encode()
    req = urllib.request.Request(
        f"{_paisa_base()}/intent", data=payload, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:  # noqa: BLE001 - error bodies are not always JSON
            return e.code, {"error": f"paisa_http_{e.code}"}
    except Exception as exc:  # noqa: BLE001 - urllib raises a wide family
        return 503, {"error": R_MONEY_UNREACHABLE,
                     "detail": f"The money service did not answer at "
                               f"{_paisa_base()} ({type(exc).__name__}). "
                               f"Nothing was minted."}


def _checked_link(url: Any) -> str:
    """The gateway's own short_url, or a refusal. Never anything we composed.

    The same allowlist `/qr/link` enforces before it encodes a QR, applied here
    before the string is handed to a phone as something tappable. A link this
    program would refuse to draw is a link it must also refuse to hand over;
    doing one and not the other would be a rule with a door in it.
    """
    up = _till()
    if not isinstance(url, str) or not url.strip():
        raise StorefrontRefused(
            R_NOT_PAYABLE,
            "the money service minted nothing payable for this order.")
    url = url.strip()
    if up._looks_like_upi(url):
        raise StorefrontRefused(
            R_REFUSED_LINK,
            "that string is a UPI payload, not a gateway link. This shop shows "
            "customers payment targets the gateway issued, and nothing else.")
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https"):
        raise StorefrontRefused(
            R_REFUSED_LINK,
            f"a payable link must be http or https, not {parts.scheme!r}. "
            f"Nothing was shown.")
    if not re.fullmatch(r"[a-z0-9.-]+", host):
        raise StorefrontRefused(
            R_REFUSED_LINK,
            "that link's host is not a plain hostname, so where it actually "
            "points cannot be agreed on. Nothing was shown.")
    hosts = tuple(getattr(up, "LINK_HOSTS", ("rzp.io", "razorpay.com")))
    if not any(host == h or host.endswith("." + h) for h in hosts):
        raise StorefrontRefused(
            R_REFUSED_LINK,
            f"that link points at {host!r}, which is not one of the gateway "
            f"hosts a payable link may live on ({', '.join(hosts)}). Nothing "
            f"was shown.")
    return url


#: How long to wait for the gateway to say whether a short code exists. Short on
#: purpose: this sits between a customer pressing PAY and a page appearing, and
#: "cannot tell" is a safe answer here (see `_gateway_serves`) while a six-second
#: stall in front of the money path is not.
LINK_PROBE_TIMEOUT_S = 4

#: How long a `dead` or `unknown` verdict stands before the gateway is asked
#: again. A `live` verdict is never re-asked — see `_link_health`.
LINK_RECHECK_S = 120

#: Cap on the verdict cache below, so a long-lived counter does not grow one
#: entry per payment link it has ever minted.
MAX_LINK_VERDICTS = 2000


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Stop at the first response so its STATUS is the measurement.

    A live short code answers 302 and the redirect target is a different host
    with a different meaning; following it would replace the one fact this probe
    wants — did the gateway recognise this code — with a fact about whatever
    razorpay.com served afterwards. Returning None makes urllib raise the 3xx as
    an HTTPError, which `_gateway_serves` reads as the success it is.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def _gateway_serves(url: str) -> Optional[bool]:
    """Does the gateway still serve this short code? None means "cannot tell".

    THIS IS THE CHECK THAT WAS MISSING, AND ITS ABSENCE PUT `{}` IN FRONT OF A
    CUSTOMER. Order ord_eabcde66be86 stored a short link under the gateway's own
    short-link host and the order page rendered it as a green PAY button for
    Rs 1,600.00. Pressing it fetched 404, `application/json`, two bytes: `{}`.
    The gateway's short-link host answers a code it never issued with a bare
    empty object, and a browser shows that as `{}` on a white page.

    The code had never existed. `GET /v1/payment_links/plink_kqD9HyAzA1nf4R` on
    the very key this shop is configured with answers "The id provided does not
    exist". The link had been minted by `gawaah/rzp_sim.py`, which composes its
    own short link under a hard-coded prefix on the gateway's REAL domain — a
    payment address the gateway never issued.

    `_checked_link` could not catch that and still cannot: it checks the SHAPE of
    a link — scheme, hostname, host allowlist — and a fabricated link on the
    right host passes every one of those. Shape is what a forgery gets right. So
    this asks the gateway itself, which is the only party that can answer.

    Measured against all three cases before this was written, by short code:

        real and payable   rzp/ykXAkfX  -> 302 -> 200 text/html
        real and expired   rzp/t4kt40C  -> 302 -> 200 text/html
        never issued       i/BjQNyPd    -> 404 application/json, 2 bytes

    Note the middle row. An EXPIRED link still resolves, and the page the
    gateway serves for it says so in the gateway's own words. That is a fine
    screen for a customer to land on, so "does the gateway serve this" is the
    question here and "is it still payable" is not — this must not condemn a
    link the gateway is willing to explain for itself.

    Three-valued, and the third value is the important one. A timeout, a DNS
    failure or a 5xx means the shop could not reach the gateway, which is a fact
    about the shop's wifi and NOT evidence against the link. Only a definite 404
    or 410 — the gateway saying it has no such code — is treated as dead, because
    the cost of being wrong runs one way: a link wrongly called dead is a working
    payment refused, and this program would rather show a customer a link that
    turns out to be stale than refuse one that would have taken their money.
    """
    try:
        req = urllib.request.Request(url, method="GET")
        # Razorpay's short-link host varies its answer by client; ask as the
        # browser that will actually follow this link, so the status measured
        # here is the status the customer would have got.
        req.add_header("User-Agent", "Mozilla/5.0 (compatible; GAWAAH-link-check)")
        opener = urllib.request.build_opener(_NoRedirect)
        with opener.open(req, timeout=LINK_PROBE_TIMEOUT_S) as r:
            return 200 <= int(r.status) < 400
    except urllib.error.HTTPError as e:
        code = int(e.code)
        if 300 <= code < 400:
            return True
        if code in (404, 410):
            return False
        # 401/403/429/5xx are the gateway declining to answer, not a verdict on
        # the code. Unknown, and unknown is not dead.
        return None
    except Exception:  # noqa: BLE001 - urllib raises a wide family; all unknown
        return None


#: The verdict cache: short_url -> (state, monotonic seconds when asked).
#:
#: IN MEMORY, AND NOT ON THE ORDER DOCUMENT. The first version of this cached
#: `link_state` onto the order and wrote it there — which made GET
#: /store/order/{id} a route that MODIFIES an order file. The customer's screen
#: polls that route every four seconds, so an idle tracking page rewrote the
#: order on disk, and running the test suite against a real shop mutated live
#: order documents under `results/` just by reading them. A read must not write.
#:
#: Keyed on the URL rather than the order because it is a fact about the LINK:
#: two orders that somehow share a link share its fate, and a cache that lives
#: and dies with the process cannot go stale across a restart.
_LINK_VERDICTS: dict[str, tuple[str, float]] = {}


def _link_health(doc: dict[str, Any]) -> dict[str, Any]:
    """Classify this order's stored link, asking the gateway at most once.

    A `live` answer is never re-asked, because it is MONOTONE in the direction
    that matters: a code the gateway serves goes on being served — an expired
    link still resolves, measured in `_gateway_serves` — so nothing this shop
    does can turn a served code back into an unserved one. `dead` and `unknown`
    ARE re-asked, but no more often than `LINK_RECHECK_S`: the cost of holding a
    wrong `dead` is a payment permanently refused, and the cost of re-asking is
    one request a couple of minutes.

    Returns the fields the customer's screen needs. `payable` is the one the
    page keys on, and it is false only when the gateway has actually denied the
    code — never merely because this shop could not reach the gateway.
    """
    pay = doc.get("payment") or {}
    url = pay.get("short_url")
    if not isinstance(url, str) or not url.strip():
        return {"link_state": None, "payable": False}

    # SHAPE BEFORE NETWORK, AND THE ORDER IS THE POINT.
    #
    # `_checked_link` — scheme, hostname, host allowlist — used to run only in
    # the /pay response, which left the READ path with no shape check at all.
    # Measured: an order document holding `http://127.0.0.1:8788/health` came
    # back from GET /store/order/{id} as `link_state: live, payable: true`, and
    # that route is the one the customer's screen renders its PAY button from.
    # A liveness check was added here without the shape check beside it, so the
    # page went back to trusting a stored string — the same defect this module
    # set out to fix, one field along.
    #
    # It also stopped an unvalidated URL being fetched: `_gateway_serves` opens
    # whatever it is handed, so probing first meant this server issued a GET to
    # any address sitting in an order file before anything checked where it
    # pointed. Nothing is asked of the network until the string is one this shop
    # would be willing to show.
    try:
        _checked_link(url)
    except StorefrontRefused:
        # Not `dead`: the gateway has said nothing about this string, and
        # `_dead_link_words` would put words in its mouth. This shop is the one
        # refusing, because the link is not on the gateway at all.
        return {"link_state": "refused", "payable": False}

    now = time.monotonic()
    cached = _LINK_VERDICTS.get(url)
    if cached is not None:
        state, asked_at = cached
        if state == "live" or (now - asked_at) < LINK_RECHECK_S:
            return {"link_state": state, "payable": state != "dead"}

    state = {True: "live", False: "dead", None: "unknown"}[_gateway_serves(url)]
    if len(_LINK_VERDICTS) >= MAX_LINK_VERDICTS:
        # A shop that has run for months has minted a lot of links. Oldest asked
        # first: dropping one costs a single re-probe, and an unbounded dict on
        # a long-lived server costs memory nobody is watching.
        for stale in sorted(_LINK_VERDICTS, key=lambda k: _LINK_VERDICTS[k][1]
                            )[:len(_LINK_VERDICTS) // 4 or 1]:
            _LINK_VERDICTS.pop(stale, None)
    _LINK_VERDICTS[url] = (state, now)
    return {
        "link_state": state,
        # Unknown is payable. See `_gateway_serves`: not reaching the gateway is
        # not evidence against the link, and the customer's own browser is about
        # to ask the same question for real.
        "payable": state != "dead",
    }


def _dead_link_words(doc: dict[str, Any]) -> str:
    """What a customer is told when the link they were given is denied.

    Plain words and no jargon: the customer did nothing wrong, nothing has been
    charged, and there is one thing they can do next. The order id is included
    because it is what the shopkeeper needs if they ring the shop.
    """
    return (
        f"The payment page for order {doc.get('order_id')} is no longer there. "
        f"The shop's payment gateway does not recognise the link this order was "
        f"given, so there is nothing to pay on at that address — nothing has "
        f"been charged and nothing is owed twice. Ask for a new payment link "
        f"below, or pay the delivery person at the door.")


#: The two verdicts that mean "do not send a customer to this string". They are
#: kept apart because they are different facts — `dead` is the GATEWAY's answer,
#: `refused` is THIS SHOP's — and a customer told the wrong one is told a lie
#: about who said no. They earn the same offer, which is a fresh link.
UNPAYABLE_STATES = ("dead", "refused")


def _unpayable_words(doc: dict[str, Any], state: Optional[str]) -> str:
    """Plain words for whichever of the two refusals this is.

    Same shape as `_dead_link_words` and the same three things a customer needs:
    nothing was charged, it is not their fault, here is the one next step.
    """
    if state != "refused":
        return _dead_link_words(doc)
    return (
        f"The payment link stored against order {doc.get('order_id')} is not "
        f"one this shop is willing to send you to — it does not point at the "
        f"payment gateway. Nothing has been charged and nothing is owed. Ask "
        f"for a new payment link below, or pay the delivery person at the door.")


def _refresh_payment(doc: dict[str, Any]) -> dict[str, Any]:
    """Ask paisa whether this order's session has settled. Never decide it here.

    Only asked when there IS a session and it is not already known to be paid,
    so a delivered order costs no network. A money service that does not answer
    leaves the order exactly as it was: not paid is the safe direction to be
    wrong in, and the customer sees the link is still open rather than a green
    screen this shop invented.
    """
    pay = doc.get("payment") or {}
    if pay.get("paid") or not pay.get("minted_at"):
        return doc
    session_id = str(pay.get("session_id") or "")
    if not session_id:
        return doc
    try:
        from urllib.parse import quote

        status, body = _till()._paisa_get("/session/" + quote(session_id, safe=""))
    except Exception:  # noqa: BLE001 - a lookup failure is not a payment fact
        return doc
    if status != 200 or not isinstance(body, dict):
        return doc
    state = body.get("state")
    state = None if state is None else str(state)
    settled = bool(body.get("paid"))
    # Write only when something actually changed. A customer's phone polls this
    # route every couple of seconds while it waits, and rewriting the order file
    # on every poll would put a disk write behind a screen that is doing nothing
    # but waiting.
    if state == pay.get("state") and not settled:
        return doc
    pay["state"] = state
    if settled:
        pay["paid"] = True
        pay["paid_at"] = _now_iso()
    doc["payment"] = pay
    _write_order(doc)
    if settled:
        _audit("order.paid", order_id=doc["order_id"],
               session_id=session_id,
               total_paise=int(doc.get("total_paise") or 0),
               minted=False)
    return doc


def _reprice_from_the_shops_own_book(doc: dict[str, Any]) -> int:
    """INVARIANT 5, the storefront's half: re-derive this order's total NOW.

    An order is a record of what a basket cost WHEN IT WAS PLACED. Between then
    and PAY the shopkeeper may have changed a price, started an offer, or
    deleted the product outright — and the stored total is then a number this
    shop no longer stands behind. paisa will catch it, because paisa re-prices
    every witness from its own book and refuses on a paisa of disagreement. That
    check is the one that matters and NOTHING HERE REPLACES IT: the mint below
    still goes through it, and this function cannot let an order past it.

    What this adds is a legible refusal. Left to paisa alone, a customer who
    pressed PAY on a basket whose price moved got `scan_total_disagreement`
    quoting two numbers and a counter they have never heard of, or
    `amber_in_basket` naming a product the shop had been openly selling an hour
    earlier. Both are correct and neither is actionable by the person reading
    them. Re-priced here first, the same facts arrive as "this line was ₹10.00
    when you ordered and is ₹12.00 now" — and, critically, the order is still
    refused rather than charged at either number.

    Read through `catalogue()`, which is `offer_priced_skus()` — the offer-aware
    book, the same number paisa's `OfferPriceBook` derives. Reading the marked
    price here would recreate the bug FAILURES.md records under
    `scan_total_disagreement`: a shop quoting a price it was not going to charge.
    """
    known = catalogue()
    stored = int(paise(doc.get("total_paise") or 0))
    derived = 0
    for ln in (doc.get("lines") or []):
        sku = str(ln.get("sku_id") or "")
        qty = int(ln.get("qty") or 0)
        rec = known.get(sku)
        if rec is None:
            raise StorefrontRefused(
                R_GONE_FROM_SHOP,
                f"{str(ln.get('name') or sku)!r} was on this order and the shop "
                f"no longer sells it, so there is no price to charge for it. "
                f"Nothing was minted. The shopkeeper can put it back on sale, or "
                f"cancel this order and the customer can order what is left.")
        was = int(paise(ln.get("unit_paise") or 0))
        now = int(paise(rec["price_paise"]))
        if now != was:
            raise StorefrontRefused(
                R_PRICE_MOVED,
                f"{str(ln.get('name') or sku)!r} was "
                f"{to_rupees_str(paise(was))} when this order was placed and is "
                f"{to_rupees_str(paise(now))} now. Nothing was minted, because "
                f"this shop will not charge a number it has stopped agreeing "
                f"with. Order it again at the new price.")
        derived += now * qty
    derived = int(paise(derived))
    if derived != stored:
        # Reachable when the order file itself disagrees with its own lines —
        # a hand-edited order, or a line list that lost a row. The lines are the
        # evidence and the total is a summary, so the summary is what loses.
        raise StorefrontRefused(
            R_PRICE_MOVED,
            f"this order says it comes to {to_rupees_str(paise(stored))} and "
            f"re-adding its lines from the shop's own catalogue gives "
            f"{to_rupees_str(paise(derived))}. Nothing was minted.")
    return derived


def _record_mint_refusal(doc: Optional[dict[str, Any]],
                         reason: str, detail: str) -> None:
    """Write onto the ORDER that a mint was attempted and refused, and why.

    THE SILENCE WAS THE BUG, NOT THE REFUSAL. A refused mint used to exist only
    in the HTTP response to the phone that asked for it. Nobody kept that. The
    order document was left exactly as it had been — no `minted_at`, no
    `short_url`, nothing — which is byte-for-byte indistinguishable from an
    order where the customer simply never pressed PAY. The shopkeeper's screen
    reads `payment.minted_at`, finds it null, and prints PAY AT THE DOOR: a
    payment method this product does not have, offered for two live orders that
    had each been refused for a real reason.

        shop_ord_2e6b134bc97b  amber_in_basket          Rs 431.50  out_for_delivery
        shop_ord_bb81bceda709  scan_total_disagreement  Rs  35.00  delivered

    Goods handed over, nothing owed recorded, and the screen said the customer
    would pay at the door. This module already had the diagnosis written down
    one function away, about deleting a product out from under an open order:
    "The refusal was correct. The silence at delete time was not."

    THE MONEY SERVICE'S OWN WORDS, NOT A PARAPHRASE. `reason` and `detail` are
    stored exactly as they were refused, for the same reason the pay route
    forwards them verbatim: `amber_in_basket` names a product the shopkeeper can
    go and fix, and a summary of it does not.

    Best-effort, and deliberately so. This runs on the way out of a refusal that
    is already being returned, and a failure to write the note must not turn a
    named refusal into a 500 — the customer's answer is correct either way, and
    the note is what the shopkeeper reads afterwards.
    """
    if not isinstance(doc, dict) or not doc.get("order_id"):
        return
    try:
        pay = doc.get("payment") or {}
        if pay.get("paid"):
            # Money arrived. A later refusal is not a fact about this order's
            # payment, and hanging one on a paid order would be a false alarm.
            return
        pay["last_refusal"] = {"reason": str(reason), "detail": str(detail),
                               "at": _now_iso()}
        doc["payment"] = pay
        _write_order(doc)
        _audit("order.mint_refused", order_id=doc["order_id"],
               reason=str(reason),
               total_paise=int(doc.get("total_paise") or 0), minted=False)
    except Exception:  # noqa: BLE001 - see the note above; never mask a refusal
        return


@router.post("/store/order/{order_id}/pay")
def store_order_pay_ep(order_id: str) -> JSONResponse:
    """Mint a payment link for one order, through the money service.

    The phone sends an order id and NOTHING ELSE. The amount comes off the order
    the server wrote; the witness is written here from the shop's own catalogue;
    paisa re-prices every line from its own price book and refuses the mint if
    it disagrees by a paisa. This route cannot make a payment happen and cannot
    mark one as having happened.

    Called twice, it returns the link it already has rather than minting a
    second one — see the note on the session id in `store_order_ep`.
    """
    # Bound BEFORE the try so the refusal handlers below can write the reason
    # onto the order. It stays None when the id itself is the problem, and
    # `_record_mint_refusal` declines to invent an order to hang a note on.
    doc: Optional[dict[str, Any]] = None
    try:
        doc = _read_order(order_id)
        status = str(doc.get("status") or NEW)
        if status == CANCELLED:
            raise StorefrontRefused(
                R_NOT_PAYABLE,
                f"order {order_id} was cancelled. Nothing was minted.")
        pay = doc.get("payment") or {}
        if pay.get("paid"):
            raise StorefrontRefused(
                R_NOT_PAYABLE,
                f"order {order_id} is already paid. Nothing was minted.")
        if pay.get("short_url"):
            # The link that exists is the link to show. Minting again would put
            # a second live payment link on one basket.
            #
            # BUT ONLY IF THE GATEWAY STILL SERVES IT. Replaying a stored link
            # unconditionally is what put `{}` in front of a customer: the link
            # had been fabricated by the simulator, the gateway had never heard
            # of it, and this branch handed it over on every press because a
            # string was present. The health check's verdict is cached in memory
            # and keyed on the URL — NOT on the order, which is what an earlier
            # version did and which turned a read into a write (see
            # `_LINK_VERDICTS`) — so a live link is probed once for the life of
            # the process and never again.
            health = _link_health(doc)
            body = {
                "ok": True, "settles_money": False,
                "order_id": doc["order_id"],
                "session_id": pay.get("session_id"),
                "amount_paise": int(doc.get("total_paise") or 0),
                "amount_rupees": to_rupees_str(
                    paise(doc.get("total_paise") or 0)),
                "short_url": _checked_link(pay.get("short_url")),
                "qr_url": f"/qr/link/{pay.get('session_id')}",
                "replayed": True,
                **health,
                "note": "This link was already minted for this order.",
            }
            if health.get("link_state") == "dead":
                # Still returned, deliberately: the caller is told exactly which
                # string was refused and why, rather than being handed a null it
                # has to guess about. `payable: false` is what the page keys on,
                # and `can_relink` is the offer this order is entitled to.
                body["can_relink"] = True
                body["note"] = _dead_link_words(doc)
                # Audited HERE and not in `_link_health`, because this is a
                # press of PAY — a customer actually tried to pay and could not.
                # The health check itself runs on a GET the tracking screen
                # polls, and putting a chain write behind that would append a
                # line every few seconds for as long as the page stayed open.
                _audit("order.link_dead", order_id=doc["order_id"],
                       session_id=str(pay.get("session_id") or ""),
                       payment_link_id=pay.get("payment_link_id"),
                       total_paise=int(doc.get("total_paise") or 0),
                       minted=False)
            return JSONResponse(body)

        total = int(paise(doc.get("total_paise") or 0))
        if total <= 0:
            raise StorefrontRefused(
                R_NOT_PAYABLE,
                f"order {order_id} comes to {total} paise, and a debit must be "
                f"positive. Nothing was minted.")

        # Re-derived from the shop's own catalogue at mint time, and it must
        # equal the order. paisa does this again, independently, from ITS book.
        total = _reprice_from_the_shops_own_book(doc)

        scan_id = _write_witness(doc)
        _confirm_witness_is_readable(scan_id)
        session_id = str(pay.get("session_id") or f"shop_{doc['order_id']}")
        code, body = _post_intent(session_id, total, scan_id)
        if code != 200 or not isinstance(body, dict) or not body.get("short_url"):
            # A witness this route wrote and read back a line ago cannot be
            # missing because the order is wrong; it is missing because the money
            # service is looking somewhere else. Say THAT, with both paths.
            if str((body or {}).get("error") or "") == "scan_not_found":
                raise StorefrontRefused(
                    R_WITNESS_UNSEEN, _witness_unseen_detail(scan_id))
            # paisa's own words, verbatim. It refuses for reasons this module
            # must not paraphrase — `amber_in_basket` means a product this shop
            # sells is not in the money service's price book, and that is a real
            # thing for a shopkeeper to go and fix.
            reason = str((body or {}).get("error")
                         or (body or {}).get("reason")
                         or f"the money service answered HTTP {code}")
            raise StorefrontRefused(
                reason,
                str((body or {}).get("detail")
                    or "Nothing was minted for this order."))

        link = _checked_link(body.get("short_url"))
        pay.update({
            "session_id": session_id,
            "short_url": link,
            "state": str(body.get("state") or ""),
            "minted_at": _now_iso(),
            "scan_id": scan_id,
            "payment_link_id": body.get("payment_link_id"),
            "replayed": bool(body.get("replayed")),
        })
        # A link exists now, so an earlier refusal is history and not the state
        # of this order. Leaving it would put a stale amber note beside a live
        # payment link, which is the same lie in the other direction.
        pay.pop("last_refusal", None)
        doc["payment"] = pay
        _write_order(doc)
        head = _audit("order.link_minted", order_id=doc["order_id"],
                      session_id=session_id, scan_id=scan_id,
                      total_paise=total,
                      payment_link_id=body.get("payment_link_id"),
                      minted=True)
        # Asked of the gateway about the link it just issued, before the customer
        # is sent to it. A mint that "succeeded" against a simulator produces a
        # string of exactly the right shape that resolves to nothing, and the
        # only moment that is cheap to discover is this one — while the customer
        # is still on this shop's page rather than looking at `{}`.
        out = dict(_link_health(doc))
        if out.get("link_state") == "dead":
            out["can_relink"] = True
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "order_id": doc["order_id"],
            "session_id": session_id,
            "amount_paise": total,
            "amount_rupees": to_rupees_str(paise(total)),
            "short_url": link,
            "qr_url": f"/qr/link/{session_id}",
            "replayed": bool(body.get("replayed")),
            "audited": head is not None,
            **out,
            "note": (_dead_link_words(doc) if out.get("link_state") == "dead"
                     else "This link was issued by the payment gateway. This "
                          "shop did not build it, and nothing turns green here "
                          "until the gateway's own signed callback says the "
                          "money arrived."),
        })
    except StorefrontRefused as exc:
        _record_mint_refusal(doc, exc.reason, exc.detail)
        return _refusal(exc, status=404 if exc.reason == R_NO_ORDER else 400)
    except MoneyError as exc:
        refused = StorefrontRefused(
            R_NOT_PAYABLE, f"this order's total is not integer paise ({exc}). "
                           f"Nothing was minted.")
        _record_mint_refusal(doc, refused.reason, refused.detail)
        return _refusal(refused)
    except Exception as exc:  # noqa: BLE001 - never a 500
        # A crash is a refusal the shopkeeper needs on the order too, for the
        # same reason: PAY was pressed, nothing was minted, and the screen must
        # not go on offering a door that does not exist.
        _record_mint_refusal(doc, R_INTERNAL, f"{type(exc).__name__}: {exc}")
        return _crash(exc)


@router.post("/store/order/{order_id}/relink")
def store_order_relink_ep(order_id: str) -> JSONResponse:
    """Replace a payment link the GATEWAY ITSELF has denied. Nothing else.

    THE ONE ROUTE IN THIS MODULE THAT CAN PUT A SECOND PAYMENT LINK ON ONE
    BASKET, so its precondition is the whole point of it. A customer whose link
    is dead is stuck: `/pay` correctly refuses to mint a second link for an order
    that already has one, and the one it has resolves to `{}`. Without this route
    the honest answer to that customer is "pay at the door", forever.

    IT REFUSES UNLESS THE LINK IS PROVEN DEAD, and proven means the gateway
    answered 404 or 410 for it — not that this shop could not reach the gateway,
    and not that the link merely looks old. The asymmetry is deliberate and it
    runs the safe way: minting beside a link that is actually live is how one
    basket takes two payments, and that is worse than the dead link this route
    exists to clear. So `unknown` refuses, exactly like `live` does.

    A NEW SESSION ID, because that is what makes the new link a new link. paisa
    keys its intents on the session id and hands that nonce to the gateway as
    `reference_id`, so re-minting under the SAME id replays the same dead link
    and this route would do nothing at all. The supersession is recorded on the
    order — `superseded` keeps every dead link this order has had, so the trail
    from the first link to the one that was finally paid is on the document
    rather than only in the chain.
    """
    doc: Optional[dict[str, Any]] = None
    try:
        doc = _read_order(order_id)
        pay = doc.get("payment") or {}
        if pay.get("paid"):
            raise StorefrontRefused(
                R_NOT_PAYABLE,
                f"order {order_id} is already paid. Nothing was minted.")
        if str(doc.get("status") or NEW) == CANCELLED:
            raise StorefrontRefused(
                R_NOT_PAYABLE,
                f"order {order_id} was cancelled. Nothing was minted.")
        old = pay.get("short_url")
        if not old:
            raise StorefrontRefused(
                R_NOT_PAYABLE,
                f"order {order_id} has no payment link to replace. Press PAY to "
                f"ask for one.")

        # `refused` counts alongside `dead`, and it is the SAFER of the two to
        # act on: a link this shop will not show has never been offered to
        # anybody as payable, so there is no live link to mint beside. The
        # asymmetry that matters — `unknown` refuses, exactly like `live` —
        # is unchanged.
        state = _link_health(doc).get("link_state")
        if state not in UNPAYABLE_STATES:
            raise StorefrontRefused(
                R_LINK_IS_ALIVE,
                f"the payment gateway still serves this order's link"
                + ("" if state == "live" else
                   " as far as this shop can tell — it could not get an answer "
                   "from the gateway just now")
                + ". A second link was NOT minted, because two live links on one "
                  "order is how a customer gets charged twice. Try the link this "
                  "order already has.")

        # Proven unpayable. Retire it and mint under a fresh session id, then let the
        # ordinary pay route do the minting — same re-pricing, same witness, same
        # refusals. Nothing about a re-mint may skip a check the first mint made.
        history = list(pay.get("superseded") or [])
        history.append({
            "short_url": old,
            "payment_link_id": pay.get("payment_link_id"),
            "session_id": pay.get("session_id"),
            "minted_at": pay.get("minted_at"),
            "retired_at": _now_iso(),
            # Which of the two refusals this was, because the trail should say
            # who said no. "the gateway does not serve this link" is the
            # gateway's answer and must not be recorded for a link the gateway
            # was never asked about.
            "why": ("the gateway does not serve this link" if state == "dead"
                    else "this link does not point at the payment gateway"),
        })
        pay["superseded"] = history
        pay["session_id"] = f"shop_{doc['order_id']}_r{len(history)}"
        for gone in ("short_url", "state", "minted_at", "payment_link_id",
                     "replayed"):
            pay.pop(gone, None)
        # Forget the verdict for the retired link. It is about to stop being
        # this order's link, and leaving it in the cache would keep answering
        # for a URL nothing points at any more.
        _LINK_VERDICTS.pop(str(old), None)
        doc["payment"] = pay
        _write_order(doc)
        _audit("order.link_superseded", order_id=doc["order_id"],
               session_id=str(pay["session_id"]),
               total_paise=int(doc.get("total_paise") or 0), minted=False)
    except StorefrontRefused as exc:
        return _refusal(exc, status=404 if exc.reason == R_NO_ORDER else 400)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)
    # Outside the try: a refusal raised by the mint is the MINT's refusal and is
    # already handled there, with its own note written onto the order. Wrapping
    # it again here would record it twice and blame this route for it.
    return store_order_pay_ep(order_id)


# -------------------------------------------------------- the shutter code --


def _own_origin(request: Request) -> str:
    """This server's address as the browser reached it, for the shutter QR.

    Taken from the Host header rather than a configured value, because the whole
    point is a laptop on a shop's wifi: the shopkeeper opens the till at
    `http://192.168.1.7:8790`, so that is the address a phone can reach, and
    `127.0.0.1` — which is what a configured default would say — is the one
    address that is guaranteed not to work from another device.

    The Host header is client-controlled, so it is charset-checked and the
    result is refused if it resolves to anything but a plain host and port.
    Somebody who can set the Host header on the shopkeeper's own request is
    already inside the shop; this stops the printed sticker being the thing that
    lets them in.
    """
    host = (request.headers.get("host") or "").strip().lower()
    if not host or not re.fullmatch(r"[a-z0-9.\-]+(:[0-9]{1,5})?", host):
        raise StorefrontRefused(
            R_NO_HOST,
            f"this server cannot tell what address it was reached on "
            f"({host!r}), so it will not print a code pointing at a guess.")
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme
             or "http").strip().lower()
    if proto not in ("http", "https"):
        proto = "http"
    return f"{proto}://{host}"


@router.get("/store/qr")
def store_qr_ep(request: Request, px: int = 700):
    """A printable code that opens THIS SHOP on a customer's phone.

    A NAVIGATION QR AND NOTHING ELSE. It encodes this server's own origin and
    the storefront's route — no amount, no order, no payment target — and it is
    refused outright if the string ever looks like a UPI payload or points at a
    gateway host. Those two checks cannot fire on the code as written; they are
    here because the day somebody makes this endpoint take a parameter is the
    day invariant 6 needs a guard that was already in place.
    """
    try:
        import cv2
        import numpy as np

        up = _till()
        # THE SAME LINK "YOUR SHOP" PRINTS. The slug is READ here, never
        # minted: this is an open route and a stranger's request must not
        # write a shop identity. `read_profile()` returns the stored slug or
        # nothing, and `customer_url` falls back to the plain address until
        # the shopkeeper's side has minted one.
        from . import shopadmin as _sa, shopface as _sf  # noqa: WPS433 - lazy, like _till()
        url = _sf.customer_url(_own_origin(request), (_sa.read_profile() or {}).get("slug"))
        if up._looks_like_upi(url):
            raise StorefrontRefused(
                R_REFUSED_LINK,
                "that string is a UPI payload. This code opens a shop; it does "
                "not carry money.")
        from urllib.parse import urlsplit

        host = (urlsplit(url).hostname or "").lower()
        hosts = tuple(getattr(up, "LINK_HOSTS", ()))
        if any(host == h or host.endswith("." + h) for h in hosts):
            raise StorefrontRefused(
                R_REFUSED_LINK,
                f"this code would point at {host!r}, a payment gateway host. A "
                f"shutter sticker points at the shop, never at money.")

        enc = cv2.QRCodeEncoder.create()
        q = enc.encode(url)
        q = (q * 255).astype(np.uint8) if q.max() <= 1 else q.astype(np.uint8)
        side = max(200, min(int(px), 1600))
        q = cv2.resize(q, (side, side), interpolation=cv2.INTER_NEAREST)
        pad = side // 12
        card = np.full((side + 2 * pad, side + 2 * pad), 255, np.uint8)
        card[pad:pad + side, pad:pad + side] = q
        ok, buf = cv2.imencode(".png", cv2.cvtColor(card, cv2.COLOR_GRAY2BGR))
        if not ok:
            raise StorefrontRefused(R_INTERNAL, "the code would not encode")
        return Response(buf.tobytes(), media_type="image/png",
                        headers={"Cache-Control": "no-store",
                                 "X-Gawaah-Storefront-Url": url,
                                 "Content-Disposition":
                                     'inline; filename="gawaah_shop_qr.png"'})
    except StorefrontRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/store/link")
def store_link_ep(request: Request) -> JSONResponse:
    """The address the shutter code carries, as text.

    Separate from the image so the shopkeeper's page can WARN when the answer is
    a loopback address: a QR reading `http://127.0.0.1:8790` is a perfectly good
    QR that no phone on earth can open, and that failure is silent unless
    somebody says it out loud.
    """
    try:
        origin = _own_origin(request)
        # Same rule as /store/qr above: the stored slug, read-only.
        from . import shopadmin as _sa, shopface as _sf  # noqa: WPS433 - lazy
        url = _sf.customer_url(origin, (_sa.read_profile() or {}).get("slug"))
        from urllib.parse import urlsplit

        host = (urlsplit(origin).hostname or "").lower()
        loopback = host in ("127.0.0.1", "localhost", "::1", "0.0.0.0")
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "url": url,
            "qr_url": "/store/qr",
            "reachable_from_a_phone": not loopback,
            "note": (
                "This address is the loopback interface, which means it points "
                "at whatever device opens it. A phone scanning this code will "
                "try to reach itself and fail. Open this till at the laptop's "
                "address on the shop's wifi and print the code from there."
                if loopback else
                "A phone on the same network can open this address."),
        })
    except StorefrontRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


# ------------------------------------------------- the customer's identity --
#
# DEFECT 2, AND THE DESIGN DECISION IT TURNS ON.
#
# `gawaah/auth.py` already has accounts, sessions, scrypt-hashed passwords and
# an invite system. The obvious move is to add a "customer" role to it. This
# module deliberately does NOT do that, for three reasons, in the order they
# would hurt:
#
#   1. ONE STORE, ONE BUG, AND THE COUNTER IS OPEN. `auth.py`'s account is the
#      thing `require_shopkeeper` lets through to the till, the books and the
#      day's takings. Put customers in the same store and the only thing between
#      a stranger who ordered a bar of soap and the shop's own accounts is a
#      role string being checked correctly in every place that matters, forever.
#      Two separate stores cannot leak into one another by forgetting a check.
#      A customer session minted here is not an account in `auth.py` and cannot
#      become one: `current_shopkeeper` will never return anything for it.
#
#   2. A KIRANA CUSTOMER WILL NOT MAKE A PASSWORD. `auth.py` requires one, and
#      is right to — it guards a shopkeeper's livelihood. Demanding one from
#      somebody who wants two hundred grams of soap loses the customer, and the
#      passwords that did get made would be the phone number typed twice.
#      `auth.py` even refuses that specific thing (`auth_password_is_the_phone_
#      number`), which is the correct rule for a shopkeeper and an argument
#      against reusing it here rather than for it.
#
#   3. SIGNUP IS INVITE-GATED, AND A SHOP FRONT IS THE OPPOSITE OF THAT. An
#      invite is how a shopkeeper adds a second shopkeeper. A storefront faces
#      the street by definition.
#
# WHAT A CUSTOMER IDENTITY IS HERE: a phone number, a name, and an opaque
# server-side session token. No password, because there is nothing to protect
# that a password is the right instrument for.
#
# WHAT PROVES THE NUMBER IS THEIRS: an order id that number placed. That is the
# "phone number + an order token" answer, and it is honest about what it can and
# cannot do. Typing a phone number proves nothing — anybody can type anybody's —
# so a session that has only been TOLD a number is `verified: false` and can do
# exactly one thing: carry the name, phone and address onto orders the customer
# then places, so they are not typed again. To READ BACK the orders a number has
# placed, the session must be `verified: true`, and the only way to become that
# is to present an order id belonging to that number. An order id is 48 bits
# from `secrets.token_hex(6)`, so knowing one is evidence of having placed it.
#
# WHAT THIS DOES NOT DEFEND AGAINST, stated rather than papered over: somebody
# holding a customer's phone, or a printed receipt with the order id on it, can
# become that customer here. The defence against that is an OTP to the number,
# which needs an SMS gateway this project does not have. What is NOT possible is
# enumerating a stranger's order history from their phone number alone, which is
# the thing a phone-only sign-in would have given away.

#: The customer's cookie. A DIFFERENT NAME from `auth.SESSION_COOKIE`, so that a
#: browser signed in at the counter and a browser holding a customer session are
#: never confused for one another — including the shopkeeper's own browser,
#: which can legitimately hold both at once.
CUSTOMER_COOKIE = "gawaah_customer"

#: A customer session outlives a shopkeeper's twelve hours on purpose: it guards
#: a delivery address rather than a till, and a customer who has to identify
#: themselves again to see where their order has got to will simply not look.
CUSTOMER_SESSION_DAYS = 30

#: Cap on stored sessions, so a shop that has served a thousand customers does
#: not carry a thousand live tokens. Oldest out first.
MAX_CUSTOMER_SESSIONS = 500


def customer_sessions_path() -> Path:
    """Where customer sessions live. Beside the orders, under the shop dir."""
    return shop_dir() / "customer_sessions.json"


def _load_customer_sessions() -> dict[str, Any]:
    try:
        raw = json.loads(customer_sessions_path().read_text("utf-8"))
    except FileNotFoundError:
        return {"format": 1, "sessions": {}}
    except Exception:  # noqa: BLE001 - a corrupt file is an empty one, not a 500
        return {"format": 1, "sessions": {}}
    if not isinstance(raw, dict) or not isinstance(raw.get("sessions"), dict):
        return {"format": 1, "sessions": {}}
    return raw


def _save_customer_sessions(doc: dict[str, Any]) -> None:
    path = customer_sessions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True), "utf-8")
    tmp.replace(path)


def _customer_token_id(token: str) -> str:
    """The stored form of a token. THE TOKEN ITSELF IS NEVER WRITTEN TO DISK.

    Same reasoning as `auth._token_id`: the file is the shop's, but a token in
    it would be a live credential sitting in a backup. A hash is enough to
    recognise one that is presented and useless to somebody reading the file.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalise_customer_phone(raw: Any) -> str:
    """The digits of a phone number, or "". Borrowed from customers.py's rule.

    Read through `gawaah/customers.py` when it is importable so that the shop's
    derived customer record and a customer's own session agree on what one
    phone number is — two normalisations would make "the orders for this number"
    mean two different sets. Falls back to a digit filter, which is that
    module's rule for the shapes a storefront actually receives.
    """
    try:
        from . import customers as _cust  # noqa: WPS433 - lazy, like _till()

        return str(_cust.normalise_phone(raw) or "")
    except Exception:  # noqa: BLE001 - the fallback is the same rule, inlined
        return "".join(ch for ch in str(raw or "") if ch.isdigit())


def _prune_customer_sessions(doc: dict[str, Any], now: float) -> None:
    live = {}
    for tid, rec in (doc.get("sessions") or {}).items():
        if not isinstance(rec, dict):
            continue
        if float(rec.get("expires_at") or 0) <= now:
            continue
        live[tid] = rec
    if len(live) > MAX_CUSTOMER_SESSIONS:
        ordered = sorted(live.items(), key=lambda kv: float(kv[1].get("created_at") or 0))
        live = dict(ordered[-MAX_CUSTOMER_SESSIONS:])
    doc["sessions"] = live


def _mint_customer_session(name: str, phone: str, *, verified: bool,
                           by_order: Optional[str]) -> tuple[str, dict[str, Any]]:
    """A new customer session. Returns (token, record); the token is not stored."""
    token = secrets.token_urlsafe(32)
    now = _dt.datetime.now(_dt.timezone.utc).timestamp()
    rec = {
        "name": name,
        "phone": phone,
        "verified": bool(verified),
        "verified_by_order": by_order,
        "created_at": now,
        "expires_at": now + CUSTOMER_SESSION_DAYS * 24 * 3600,
    }
    doc = _load_customer_sessions()
    _prune_customer_sessions(doc, now)
    doc["sessions"][_customer_token_id(token)] = rec
    _save_customer_sessions(doc)
    return token, rec


def current_customer(request: Request) -> Optional[dict[str, Any]]:
    """Who is shopping on this request, or None. NEVER RAISES.

    Returns the stored record plus nothing that could identify the token. A
    caller that wants to know whether the number was proved reads `verified`.
    """
    token = (request.cookies.get(CUSTOMER_COOKIE) or "").strip()
    if not token:
        return None
    try:
        doc = _load_customer_sessions()
        rec = (doc.get("sessions") or {}).get(_customer_token_id(token))
        if not isinstance(rec, dict):
            return None
        now = _dt.datetime.now(_dt.timezone.utc).timestamp()
        if float(rec.get("expires_at") or 0) <= now:
            return None
        return {
            "name": str(rec.get("name") or ""),
            "phone": str(rec.get("phone") or ""),
            "verified": bool(rec.get("verified")),
        }
    except Exception:  # noqa: BLE001 - "nobody" is the answer, not an error
        return None


def _orders_for_phone(phone: str) -> list[dict[str, Any]]:
    """Every order this number placed, newest first. Derived, never stored."""
    want = _normalise_customer_phone(phone)
    if not want:
        return []
    out = []
    for doc in _all_orders():
        got = _normalise_customer_phone((doc.get("customer") or {}).get("phone"))
        if got and got == want:
            out.append(doc)
    out.sort(key=lambda d: str(d.get("at") or ""), reverse=True)
    return out


def _set_customer_cookie(resp: JSONResponse, request: Request, token: str) -> None:
    """Same header discipline as `auth._set_cookie`, and for the same reasons.

    `secure` only over https, because a shop's till runs on plain http on the
    shop's own wifi and a Secure cookie set over http is silently dropped — the
    customer would appear to sign in and be a stranger again on the next screen.
    """
    proto = (request.headers.get("x-forwarded-proto")
             or request.url.scheme or "http").strip().lower()
    resp.set_cookie(CUSTOMER_COOKIE, token,
                    max_age=CUSTOMER_SESSION_DAYS * 24 * 3600, path="/",
                    httponly=True, samesite="lax", secure=(proto == "https"))


# ------------------------------------------- a link for ONE named customer --

#: An unclaimed invite is short-lived on purpose. It is a bearer credential
#: sitting in a URL — whoever opens it becomes that customer — so it is worth
#: far less if it stops working in a week than if it works forever.
CUSTOMER_INVITE_DAYS = 7

R_INVITE_UNKNOWN = "customer_link_not_recognised"
R_INVITE_USED = "customer_link_already_used"
R_INVITE_EXPIRED = "customer_link_expired"


def customer_invites_path() -> Path:
    return shop_dir() / "customer_invites.json"


def _load_invites() -> dict[str, Any]:
    try:
        raw = json.loads(customer_invites_path().read_text("utf-8"))
    except FileNotFoundError:
        return {"format": 1, "invites": {}}
    except Exception:  # noqa: BLE001 - a corrupt file is an empty one
        return {"format": 1, "invites": {}}
    if not isinstance(raw, dict) or not isinstance(raw.get("invites"), dict):
        return {"format": 1, "invites": {}}
    return raw


def _save_invites(doc: dict[str, Any]) -> None:
    path = customer_invites_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True), "utf-8")
    tmp.replace(path)


@router.post("/shop/customer-link")
async def shop_customer_link_ep(request: Request) -> JSONResponse:
    """A shutter code is one sticker that everybody scans. This is one link for
    one person.

    IT LIVES UNDER `/shop`, NOT `/store`, AND THAT IS THE WHOLE OF ITS SECURITY.
    It was `/store/link/for` first. `/store` is an OPEN PREFIX — it has to be,
    so a stranger with the shutter QR can reach the shop without an account —
    so the guard skipped it and any phone on the shop's wifi could mint a
    customer identity for any number it liked. Measured, not reasoned about: a
    request with no session got HTTP 200 and a working link.

    `/shop` is the shopkeeper's namespace and carries the guard. Minting is the
    shopkeeper's act; claiming is the customer's, and that one stays under
    `/store` where it can be reached with no session, which is the point of it.

    WHAT IT IS FOR. A regular who orders every week should not have to type
    their name and number into the storefront every time. The shopkeeper mints
    a link, sends it on WhatsApp, and when that customer opens it their phone
    is already the shop's idea of who they are.

    WHAT IS IN THE URL. An opaque random token and nothing else. NOT the phone
    number, NOT the name — a URL is the one string a browser writes into
    history, hands to the next page in `Referer`, and prints in a server log,
    and a customer's number has no business in any of those.

    WHAT IT CANNOT DO. It grants a CUSTOMER identity and never a shopkeeper's.
    The session it creates is UNVERIFIED, exactly like typing your own name
    into the storefront, so it cannot read the order history for that number —
    `/store/customer/orders` still demands an order id the holder can name.
    That is deliberate: a link forwarded to the wrong person must not hand over
    somebody's past orders.

    SINGLE USE, AND IT EXPIRES. The first browser to open it claims it and the
    token dies. A bearer credential in a URL that works forever, for anybody
    who ever sees it, is a different and much worse thing.

    This route is NOT in auth's open list, so with the lock on only a
    signed-in shopkeeper can mint one.
    """
    try:
        body = await _json_body(request)
        name = _text(body, "name", cap=MAX_NAME)
        phone = _normalise_customer_phone(_text(body, "phone", cap=MAX_PHONE))
        if not phone:
            raise StorefrontRefused(
                "customer_phone_not_a_number",
                "a link is made out for one person, and a phone number is how "
                "this shop tells one person from another. Nothing was made.")

        token = secrets.token_urlsafe(24)
        now = _dt.datetime.now(_dt.timezone.utc).timestamp()
        doc = _load_invites()
        # Drop the dead ones whenever we write, so the file cannot grow forever.
        doc["invites"] = {
            k: v for k, v in (doc.get("invites") or {}).items()
            if isinstance(v, dict) and float(v.get("expires_at") or 0) > now
            and not v.get("claimed_at")
        }
        doc["invites"][_customer_token_id(token)] = {
            "name": name,
            "phone": phone,
            "created_at": now,
            "expires_at": now + CUSTOMER_INVITE_DAYS * 24 * 3600,
            "claimed_at": None,
        }
        _save_invites(doc)

        origin = _own_origin(request)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "url": f"{origin}/#/shop?k={token}",
            "for": {"name": name, "phone": phone},
            "expires_in_days": CUSTOMER_INVITE_DAYS,
            "single_use": True,
            "note": ("Whoever opens this link becomes this customer on their own "
                     "phone, so send it to them and nobody else. It works once "
                     f"and stops working after {CUSTOMER_INVITE_DAYS} days. It "
                     "cannot read their past orders and it carries no payment."),
        })
    except StorefrontRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/store/customer/claim")
async def store_customer_claim_ep(request: Request) -> JSONResponse:
    """Open a link made out to one person. Body: {token}.

    Consumes the token and sets this browser's customer cookie. Every refusal
    is named, because "that link has already been used" and "that link is not
    one of ours" are different sentences for the person holding it.
    """
    try:
        body = await _json_body(request)
        token = str(body.get("token") or "").strip()
        if not token:
            raise StorefrontRefused(R_INVITE_UNKNOWN, "no link was given.")

        doc = _load_invites()
        key = _customer_token_id(token)
        rec = (doc.get("invites") or {}).get(key)
        now = _dt.datetime.now(_dt.timezone.utc).timestamp()
        if not isinstance(rec, dict):
            raise StorefrontRefused(
                R_INVITE_UNKNOWN,
                "this shop does not recognise that link. Ask for a new one, or "
                "open the shop and put your name and number in yourself.")
        if rec.get("claimed_at"):
            raise StorefrontRefused(
                R_INVITE_USED,
                "that link has already been opened once, and it only works "
                "once. Ask the shop for a new one.")
        if float(rec.get("expires_at") or 0) <= now:
            raise StorefrontRefused(
                R_INVITE_EXPIRED,
                f"that link is more than {CUSTOMER_INVITE_DAYS} days old and "
                f"has stopped working. Ask the shop for a new one.")

        rec["claimed_at"] = now
        _save_invites(doc)

        # UNVERIFIED, exactly like typing the name in by hand. See the note on
        # `/store/link/for`: a forwarded link must not open somebody's history.
        session, saved = _mint_customer_session(
            str(rec.get("name") or ""), str(rec.get("phone") or ""),
            verified=False, by_order=None)
        resp = JSONResponse({
            "ok": True,
            "settles_money": False,
            "customer": {"name": saved["name"], "phone": saved["phone"],
                         "verified": False},
            "note": ("This phone is now known to the shop as this customer. To "
                     "see past orders you still have to name one of them."),
        })
        _set_customer_cookie(resp, request, session)
        return resp
    except StorefrontRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/store/customer/signin")
async def store_customer_signin_ep(request: Request) -> JSONResponse:
    """Identify as a CUSTOMER. Body: {name, phone, order_id?}.

    With `order_id`, and only with it, the session is `verified` and can read
    back this number's orders. Without it the session is a convenience: it
    remembers who you are so the delivery form is not typed again, and it can
    read nothing.

    A WRONG `order_id` IS A REFUSAL, NOT A QUIET DOWNGRADE. Somebody who typed
    an order id meant to prove something; silently handing them an unverified
    session and an empty order list would look exactly like "you have never
    ordered here", which is a different and wrong answer.
    """
    try:
        body = await _json_body(request)
        name = _text(body, "name", cap=MAX_NAME)
        if not name:
            raise StorefrontRefused(
                R_NO_NAME, "a name is needed so the shop knows who is ordering.")
        raw_phone = _text(body, "phone", cap=MAX_PHONE)
        if not raw_phone:
            raise StorefrontRefused(
                R_NO_PHONE, "a phone number is needed: it is how the shop "
                            "reaches you about your order.")
        phone = _normalise_customer_phone(raw_phone)
        if len(phone) < MIN_PHONE_DIGITS:
            raise StorefrontRefused(
                R_BAD_PHONE,
                f"{raw_phone!r} is not a phone number this shop can ring.")

        proof = str(body.get("order_id") or "").strip()
        verified = False
        if proof:
            try:
                doc = _read_order(_valid_order_id(proof))
            except StorefrontRefused:
                doc = None
            got = _normalise_customer_phone(
                ((doc or {}).get("customer") or {}).get("phone"))
            if doc is None or not got or got != phone:
                # Deliberately one refusal for "no such order" and "that order
                # belongs to somebody else". Telling them apart would turn this
                # route into an oracle for which order ids exist.
                raise StorefrontRefused(
                    R_UNPROVEN_NUMBER,
                    f"order {proof!r} was not placed from this number, so it "
                    f"does not show that this number is yours. Nothing was "
                    f"signed in. Check the order id on your order screen.")
            verified = True

        token, rec = _mint_customer_session(name, phone, verified=verified,
                                            by_order=proof or None)
        _audit("customer.signed_in", verified=verified, minted=False,
               # THE NUMBER ITSELF NEVER GOES IN THE CHAIN. Same rule the order
               # route follows for a doorstep: a digest is enough to tell two
               # customers apart and useless for ringing one of them.
               phone_sha256=hashlib.sha256(phone.encode("utf-8")).hexdigest())
        resp = JSONResponse({
            "ok": True,
            "settles_money": False,
            "customer": {"name": rec["name"], "phone": rec["phone"],
                         "verified": verified},
            "note": ("This shop knows you by your phone number. You are signed "
                     "in as a customer, which is not an account at the counter "
                     "and can never become one."
                     if verified else
                     "Your name and number are remembered on this phone so you "
                     "do not type them again. To see the orders this number has "
                     "placed, sign in again with an order id from one of them."),
        })
        _set_customer_cookie(resp, request, token)
        return resp
    except StorefrontRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/store/customer/me")
def store_customer_me_ep(request: Request) -> JSONResponse:
    """Who this browser is, to the shop. Answers for nobody without refusing.

    Also reports whether a SHOPKEEPER is signed in on this browser, because that
    is what the storefront needs in order to say "this is a preview" before the
    customer fills in a delivery address and finds out at the end.
    """
    try:
        who = current_customer(request)
        keeper = _signed_in_shopkeeper(request)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "customer": who,
            "signed_in": who is not None,
            # Named `previewing` rather than `is_shopkeeper` because that is the
            # fact the page acts on, and it is true of a person, not a browser.
            "previewing": keeper is not None,
            "shopkeeper_name": (keeper or {}).get("name") if keeper else None,
        })
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/store/customer/signout")
def store_customer_signout_ep(request: Request) -> JSONResponse:
    """Forget this browser. Drops the stored session as well as the cookie.

    Both, because clearing only the cookie leaves a live token on disk that a
    copy of the cookie would still open — "sign out" has to mean the session is
    gone, not that this browser stopped presenting it.
    """
    try:
        token = (request.cookies.get(CUSTOMER_COOKIE) or "").strip()
        dropped = False
        if token:
            doc = _load_customer_sessions()
            if (doc.get("sessions") or {}).pop(_customer_token_id(token), None):
                _save_customer_sessions(doc)
                dropped = True
        resp = JSONResponse({"ok": True, "settles_money": False,
                             "signed_out": dropped})
        resp.delete_cookie(CUSTOMER_COOKIE, path="/")
        return resp
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/store/customer/orders")
def store_customer_orders_ep(request: Request) -> JSONResponse:
    """The orders this number has placed. VERIFIED SESSIONS ONLY.

    The whole reason `verified` exists. Without this gate, a phone number typed
    into a box would read back a stranger's order history — every item, every
    total and every delivery status — and a phone number is not a secret.
    """
    try:
        who = current_customer(request)
        if who is None:
            raise StorefrontRefused(
                R_NOT_SIGNED_IN,
                "this phone is not signed in as a customer, so there is no "
                "number to look orders up for.")
        if not who.get("verified"):
            raise StorefrontRefused(
                R_UNPROVEN_NUMBER,
                "this phone has been told a number but has not shown that the "
                "number is yours, and a phone number is not a secret. Sign in "
                "again with the order id from any order you placed, and this "
                "shop will show every order that number has made.")
        docs = _orders_for_phone(who["phone"])
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "count": len(docs),
            # The customer's own view of each: no address, no phone, exactly the
            # shape the order screen already renders.
            "orders": [_customer_view(d) for d in docs],
        })
    except StorefrontRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)
