"""SUDHAAR — correcting what the counter was taught, and naming the shop.

Two gaps this file closes, and they are the same gap seen twice: a shopkeeper
could CREATE and DESTROY but never CORRECT.

  1. A TAUGHT PRODUCT COULD NOT BE EDITED. A mistyped price or a wrong name
     meant FORGET and photograph the packet again — throwing away every taught
     view, every millimetre and the photograph, to fix two characters. So this
     module has one endpoint that changes a product's NAME, its PRICE and its
     BOUND CODE and touches nothing else: not one descriptor vector, not one
     pixel of the enrolment photograph, and above all not the sku id.

  2. THE SHOP HAD NO IDENTITY. The storefront a customer reaches by scanning
     the shutter QR showed a catalogue belonging to nobody: no name, no
     address, no phone, no opening hours. A printed QR pointing at an anonymous
     list of prices is not a shop.

Five rules this file exists to keep, in the order they would hurt if broken:

  1. THE SKU ID IS NEVER RENAMED. It is what the code bindings, the orders, the
     witnesses and every historical bill refer to. Changing it would not rename
     a product; it would orphan a record and create a new one that looks like
     it. A body that tries to set `sku_id` is REFUSED BY NAME rather than
     ignored, because silently dropping the field would leave an operator
     believing the id had changed.

  2. INTEGER PAISE. A price arrives as a rupee STRING typed by a person and
     goes through `gawaah/shop_store.py`'s `price_from_rupees`, which parses in
     string space via `gawaah/money.py`. No float is constructed anywhere on
     this path and nothing is ever divided by 100.

  3. A PRICE CHANGE IS A MONEY CHANGE, SO IT IS WRITTEN DOWN. Every edit
     appends one hash-chained line carrying the old value and the new one. A
     bill from last week priced a packet at a number the catalogue no longer
     holds, and without this line there is no way to explain it — only to
     disbelieve it.

  4. ONE CODE NAMES ONE PRODUCT. Rebinding a printed code that already names a
     DIFFERENT product is refused by name. Allowing it would leave one barcode
     pricing two things, and at the till there is no way to choose.

  5. THE BROWSER IS NEVER AN AUTHOR. The page sends text fields. This module
     validates them, converts the money, decides what changed, and writes. The
     page cannot send paise, cannot send a vector, and cannot name a storage
     location.

WHERE THE CATALOGUE LIVES — AND WHY THERE ARE TWO OF THEM
=========================================================
`gawaah/shop_store.py` is the real catalogue: name, price, VECTORS, footprint,
photograph, all in one record. Beside it, `tools/upload_app.py` keeps an
appearance-only sidecar (`appearance_only.json`) for products with no
millimetres, and that same sidecar is where a product taught from a printed
code alone lives — with zero vectors, by definition. `taught_skus()` merges the
two with the store SHADOWING the sidecar.

An edit therefore has to land wherever the product actually is, and if it is in
both places it has to land in BOTH — a stale shadowed row is invisible right up
to the moment the store entry is removed, and then it resurrects an old price.
`_locations()` answers that question once and every write follows it.

MOUNTING
========
An ``APIRouter`` with NO prefix; the paths below are absolute::

    from gawaah import shopadmin
    app.include_router(shopadmin.router)

    POST  /shop                     add a product WITHOUT a camera
    PATCH /shop/{sku_id}            change name, price, bound code
    PUT   /shop/{sku_id}/photo      give it a picture, or take one away
    GET   /shop/{sku_id}/history    what this product's price has been
    GET   /shop/profile             the shop's own name, address, phone, hours
    PUT   /shop/profile             set them

The shop's SLUG (its unique customer link), its printable QR, its photograph
and the open header the storefront reads live in `gawaah/shopface.py`; the
slug helpers are here because the profile is the document that carries it.

These deliberately mirror the till's own `GET /shop` and
`DELETE /shop/{sku_id}`: one resource — add it, correct it, or forget it.

`POST /shop` IS THE WEAK PATH AND SAYS SO. A product added there has no
descriptor: the counter knows its name and its price and nothing about what it
looks like, so it cannot be recognised by the camera at all. It exists because
a shopkeeper putting a sack of rice on the shelf at eleven at night should not
have to find the mat and the light first, and because `/enrol`'s own code-only
mode demanded an image it then never looked at. Every response from it says, in
words, what the product cannot do.

THIS FILE NEVER SETTLES MONEY
=============================
It holds no gateway and mints nothing. It changes what a price IS, which is why
every change is on a chain — but no request here moves a rupee.
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .ledger import Ledger, verify
from .money import MoneyError, paise, to_rupees_str

router = APIRouter()


# --------------------------------------------------------------- refusals --
#
# Every one of these is a state this module can actually reach, and each is
# phrased so a shopkeeper standing at the counter can act on it.

R_NO_TILL = "till_module_unavailable"
R_BAD_BODY = "edit_body_not_json"
R_UNKNOWN_SKU = "sku_not_in_this_shop"
R_NOTHING_TO_CHANGE = "nothing_to_change"
R_SKU_RENAME = "sku_id_cannot_change"
R_NO_NAME = "product_name_missing"
R_BAD_PRICE = "price_is_not_money"
R_BAD_CODE = "code_is_not_a_printed_code"
R_CODE_TAKEN = "code_already_names_another_product"
R_COLLISION = "would_be_indistinguishable_from_another_product"
R_WRITE_FAILED = "catalogue_write_failed"

R_NO_SHOP_NAME = "shop_name_missing"
R_NO_ADDRESS = "shop_address_missing"
R_SHORT_ADDRESS = "shop_address_too_short"
R_NO_PHONE = "shop_phone_missing"
R_BAD_PHONE = "shop_phone_not_an_indian_mobile"
R_BAD_HOURS = "opening_hours_not_a_time"
R_SAME_HOURS = "opening_and_closing_time_are_the_same"
R_NO_DAYS = "shop_open_on_no_day"
R_BAD_DAY = "unknown_day_of_week"

R_TOO_LONG = "field_too_long"
R_INTERNAL = "shopadmin_internal_error"

#: Putting a product on the shelf without photographing it, and putting a
#: photograph on a product that was never photographed.
R_SKU_TAKEN = "sku_already_in_this_shop"
R_BAD_SKU = "sku_id_is_not_a_filename"
R_NO_SKU_FROM_NAME = "no_sku_id_could_be_made_from_this_name"
R_BAD_PHOTO = "photo_is_not_an_image"
R_PHOTO_TOO_BIG = "photo_is_too_large"


#: Caps. Each bounds a thing that ends up on disk or on a printed sheet.
#: MAX_NAME matches ``gawaah.shop_store.NAME_MAX_CHARS`` — the store refuses
#: anything longer with a ShopError, and this is the door that can say so in
#: words. It is checked HERE as well because the appearance-only sidecar has no
#: validator of its own, so for a product living there this is the only check.
MAX_NAME = 96
MAX_CODE = 64
MAX_SHOP_NAME = 80
MAX_SHOP_ADDRESS = 240
MIN_SHOP_ADDRESS = 8
MAX_PHONE_CHARS = 24

#: In week order, not alphabetical. A shopkeeper reading back "fri, mon, sat"
#: has to sort it in his head to see that Tuesday is missing.
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_NAMES = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday",
             "thu": "Thursday", "fri": "Friday", "sat": "Saturday",
             "sun": "Sunday"}

PROFILE_NAME = "shop_profile.json"
PROFILE_FORMAT = 1

#: THE SHOP'S SLUG — the one part of its identity a customer's phone carries.
#:
#: The shutter QR used to encode `<origin>/#/shop`, which is the same string
#: for every counter on earth: a sticker peeled off one shop and stuck on
#: another would open the second shop and call it the first. So the profile
#: carries a slug — `verma-kirana-store-k7m2` — and the customer link becomes
#: `<origin>/#/shop?s=<slug>`. The storefront checks the slug against its own
#: (`gawaah/shopface.py`, `GET /store/shop`) and says plainly when a link was
#: printed for a different shop, rather than serving it as if it were this one.
#:
#: MINTED ONCE, NEVER REGENERATED ON RENAME. The slug is printed on a sticker
#: and pasted into WhatsApp threads; a shopkeeper fixing a typo in the name
#: must not silently invalidate every code already on their shutter. It changes
#: only when they ask (`POST /shop/link/renew`), and that is on the chain.
#:
#: The suffix is four characters from an alphabet with no 0/O/1/l/I, because
#: this string gets read aloud over a counter and typed into a phone. Two
#: Verma stores on the same street differ by it.
SLUG_BASE_MAX = 40
SLUG_SUFFIX_LEN = 4
SLUG_SUFFIX_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
#: A run of six or more digits inside a shop NAME is a phone number, a PIN
#: code or an account number, and none of those belongs in a URL that ends up
#: in every customer's browser history. Dropped from the base before slugging.
_SLUG_DIGIT_RUN = re.compile(r"[0-9]{6,}")

#: An sku id becomes a filename (``photos/<sku>.png``) and a ledger key, so the
#: alphabet is the one ``gawaah.shop_store.SKU_RE`` already enforces. Repeated
#: here rather than imported at module scope because importing shop_store pulls
#: in numpy, and this file is imported by a router that must load without it.
SKU_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: How long a derived sku id may be before the uniquifying suffix. Short of the
#: 64-character cap on purpose, so `_derive_sku_id` always has room to append
#: `_2`, `_3` … without truncating the part a person would recognise.
MAX_DERIVED_SKU = 48

#: THE SIDECAR'S PHOTO BUDGET, and it is deliberately smaller than the store's.
#:
#: `gawaah/shop_store.py` keeps a photograph as a FILE beside the catalogue and
#: can afford 256 px / 128 KiB. A product with no descriptor lives in the till's
#: `appearance_only.json`, and a picture stored there is base64 INSIDE a JSON
#: file that every catalogue read — `/shop`, `/store`, the price map, the till's
#: own gallery load — parses in full. So it is charged to every request, and
#: 192 px is the compromise: twice the 96 px the camera path already stores as a
#: thumbnail, and about 40 KB of base64 rather than 175 KB.
SIDECAR_PHOTO_EDGE_PX = 192
SIDECAR_PHOTO_CAP_BYTES = 48 * 1024

#: Refused BEFORE base64 is decoded. `shop_store` caps the decoded bytes at
#: 8 MiB; this is that cap plus the ~4/3 base64 expansion, so an oversized
#: upload is named at the door instead of allocating first.
MAX_PHOTO_B64_CHARS = 12 * 1024 * 1024

#: A printed code is what a scanner hands back: no spaces, no control
#: characters. Bounded because it becomes a JSON key in the binding table.
CODE_RE = re.compile(r"^[\x21-\x7e]{1,64}$")
TIME_RE = re.compile(r"^([0-9]{1,2}):([0-5][0-9])$")

#: An Indian mobile is ten digits beginning 6, 7, 8 or 9, optionally carrying
#: the country code or a trunk 0. A LANDLINE IS REFUSED: the number on the
#: shutter is the one a customer rings about an order that has not arrived.
#:
#: A STATED LIMIT, because pretending otherwise would be worse than the gap.
#: A metro landline dialled properly — 080 2345 6789 — is eleven digits
#: starting with a trunk 0, and so is a mobile dialled the same way. Strip the
#: 0 from either and what is left is ten digits starting 6-9. The two are not
#: separable here without a table of STD codes, so a Bangalore or Delhi
#: landline typed with its area code IS accepted. What that costs: a customer
#: rings a shop's landline instead of a mobile, which is a working phone call.
#: What refusing the trunk 0 outright would cost: every shopkeeper who types
#: their own mobile the way it is printed on their signboard is turned away.
MOBILE_RE = re.compile(r"^[6-9][0-9]{9}$")


class AdminRefused(Exception):
    """A named refusal with a reason a human can act on."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def _refusal(exc: AdminRefused, status: int = 400) -> JSONResponse:
    """The shape every other endpoint in this program answers a refusal with."""
    return JSONResponse(
        {"ok": False, "reason": exc.reason, "detail": exc.detail,
         "settles_money": False},
        status_code=status,
    )


def _crash(exc: BaseException) -> JSONResponse:
    """The last resort. A 500 teaches the reader nothing, so there are none."""
    return JSONResponse(
        {"ok": False, "reason": R_INTERNAL,
         "detail": f"{type(exc).__name__}: {exc}", "settles_money": False},
        status_code=400,
    )


# ----------------------------------------------------------------- the till --

from gawaah import till_ref as _till_ref

#: One definition, in `gawaah/till_ref.py`. This was sixteen copies of a tuple
#: that was missing `__main__`, and every one of them was wrong the same way.
_TILL_NAMES = _till_ref.TILL_NAMES


def _till() -> Any:
    """The already-loaded till module, or a named refusal.

    LOOK IN sys.modules FIRST. `make serve` runs `uvicorn upload_app:app
    --app-dir tools`, so the module is registered under the bare name
    `upload_app`; the test suite does `from tools import upload_app` and
    registers it as `tools.upload_app`. Importing the other spelling loads a
    SECOND copy of the file with its own `_DEPS` cache — a second store handle
    and a second catalogue directory — so an edit would be written to a shop
    nobody is serving, and nothing anywhere would say so. `gawaah/storefront.py`
    carries the same guard and the full account of how it was found.
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
        raise AdminRefused(
            R_NO_TILL,
            f"tools/upload_app.py is not importable ({type(exc).__name__}: "
            f"{exc}). The catalogue is reached through it and this module will "
            f"not keep a second copy of it.") from None
    return upload_app


def shop_dir() -> Path:
    """Where the catalogue lives — the till's own answer, never a second one.

    This is what honours `GAWAAH_SHOP_DIR`: `upload_app.store_dir()` reads that
    variable and `upload_app.set_store_dir()` redirects it for a test. Reading
    the environment again here would be a second answer to one question, and a
    harness that moved the catalogue while this file stayed behind is how a
    live shop gets overwritten.
    """
    return Path(_till().store_dir())


def audit_path() -> Path:
    """This module's own hash-chained log.

    DELIBERATELY NOT `results/audit.jsonl`. That file is held open by the money
    service in a DIFFERENT PROCESS, which keeps the chain head in memory and
    computes `prev_hash` from it. A second process appending between two of its
    writes hands it a stale head, and every line paisa writes afterwards fails
    `gawaah.ledger.verify` — the money audit trail, the one thing here that
    must be beyond argument, would be the casualty of a shopkeeper fixing a
    typo. So catalogue edits get their own chain, in the shop directory, with
    one writer, verifiable by exactly the same `verify()`.

    `gawaah/storefront.py` made the same call for orders and documents the
    trade: there are now three chains to walk instead of one, and a reader who
    checks only `results/audit.jsonl` will not see this. That is a
    documentation problem. The alternative was a corrupted money ledger.
    """
    return shop_dir() / "catalogue.audit.jsonl"


def profile_path() -> Path:
    """The shop's identity, NEXT TO the catalogue it belongs to.

    Not in `results/` and not in the environment: a scratch `GAWAAH_SHOP_DIR`
    has to get a scratch shop, name and all, or a test run renames the real
    shop.
    """
    return shop_dir() / PROFILE_NAME


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _audit(event: str, **fields: Any) -> Optional[dict[str, Any]]:
    """Append one auditable line. Returns {head, line}, or None if it failed.

    Best effort, but never silent: a caller that gets None says so in its
    response rather than reporting a witnessed price change that was not
    witnessed at all.
    """
    try:
        chain = Ledger(audit_path())
        head = chain.append(ts=_now_iso(), module="shopadmin", event=event,
                            **fields)
        return {"head": head, "line": chain.count}
    except Exception:  # noqa: BLE001 - a failed audit must not lose the edit
        return None


# ------------------------------------------------------- reading the fields --


def _body_dict(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AdminRefused(
            R_BAD_BODY,
            f"the request body must be a JSON object, not "
            f"{type(raw).__name__}. Nothing was changed.")
    return raw


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        raw = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a named answer
        raise AdminRefused(
            R_BAD_BODY,
            "the request body was not readable as JSON. Nothing was changed."
        ) from None
    return _body_dict(raw)


def _text(body: dict[str, Any], key: str, *, cap: int, keep_lines: bool = False
          ) -> Optional[str]:
    """One text field, or None when the key is ABSENT.

    Absent and empty are different instructions and this is the only place that
    distinction is drawn: on an edit, an absent key means "leave this alone"
    and an empty one means "clear it". Collapsing the two would make it
    impossible to change a price without also restating the name.
    """
    if key not in body:
        return None
    raw = body[key]
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise AdminRefused(
            R_BAD_BODY,
            f"{key!r} must be text, not {type(raw).__name__}. Nothing was "
            f"changed.")
    s = raw.strip() if keep_lines else " ".join(raw.split())
    if len(s) > cap:
        raise AdminRefused(
            R_TOO_LONG,
            f"{key} is {len(s)} characters and the cap is {cap}. Nothing was "
            f"changed.")
    return s


def _price_paise_from_rupees(text: str) -> int:
    """A rupee string a person typed -> integer paise, or a named refusal.

    Goes through the catalogue's OWN money door rather than parsing here, so
    the price an edit writes is validated by exactly the code that validated
    the price the product was taught with. `price_from_rupees` parses in string
    space and refuses sub-paisa precision, zero and negatives; no float is
    constructed at any point and nothing is divided by a hundred.
    """
    try:
        from .shop_store import price_from_rupees  # noqa: WPS433 - numpy is heavy
    except Exception as exc:  # noqa: BLE001
        raise AdminRefused(
            R_INTERNAL,
            f"gawaah.shop_store is not importable ({type(exc).__name__}: "
            f"{exc}), so a price cannot be validated. Nothing was changed."
        ) from None
    try:
        return int(price_from_rupees(text))
    except MoneyError as exc:
        raise AdminRefused(
            R_BAD_PRICE,
            f"{text!r} is not a price: {exc}. Type rupees, like 12 or 12.50 — "
            f"it is stored as whole paise and a fraction of a paisa is refused "
            f"rather than rounded.") from None
    except Exception as exc:  # noqa: BLE001 - a bad type is still a refusal
        raise AdminRefused(
            R_BAD_PRICE,
            f"{text!r} is not a price ({type(exc).__name__}: {exc}).") from None


def _clean_code(code: str) -> str:
    if not CODE_RE.match(code):
        raise AdminRefused(
            R_BAD_CODE,
            f"{code!r} is not a printed code. A code is what a scanner hands "
            f"back — up to {MAX_CODE} characters with no spaces in them. Type "
            f"the digits under the bars, or leave the field alone to keep the "
            f"code this product already has.")
    return code


# ----------------------------------------------------- where a product lives --


def _sidecar(up: Any) -> dict[str, Any]:
    """The appearance-only sidecar as it is on disk, skus keyed by id."""
    data = up._ao_load()
    skus = data.get("skus")
    return data if isinstance(skus, dict) else {"format": data.get("format"),
                                                "skus": {}}


def _locations(up: Any, sku_id: str) -> tuple[bool, bool]:
    """(in the real store, in the appearance-only sidecar).

    BOTH can be true. `taught_skus()` lets the store shadow the sidecar, so a
    row in both places is invisible until the store entry is removed — and then
    the old name and the old price come back. An edit that landed in only one
    of them would be a correction with a fuse on it.
    """
    try:
        in_store = sku_id in up.load_store()
    except Exception:  # noqa: BLE001 - an unreadable store is "not there"
        in_store = False
    return bool(in_store), sku_id in _sidecar(up)["skus"]


def _current(up: Any, sku_id: str) -> dict[str, Any]:
    """Name, price and bound codes as they stand, from wherever it lives."""
    in_store, in_sidecar = _locations(up, sku_id)
    if not (in_store or in_sidecar):
        raise AdminRefused(R_UNKNOWN_SKU, _not_here(up, sku_id))
    name = ""
    price_before: Optional[int] = None
    if in_sidecar:
        rec = _sidecar(up)["skus"].get(sku_id) or {}
        name = str(rec.get("name") or sku_id)
        try:
            price_before = int(rec["price_paise"])
        except Exception:  # noqa: BLE001 - a row with no price has none
            price_before = None
    if in_store:
        rec2 = up.load_store().get(sku_id)
        if rec2 is not None:
            name = str(rec2.name)
            price_before = int(rec2.price_paise)
    return {
        "sku_id": sku_id,
        "name": name,
        "price_paise": price_before,
        "codes": _codes_of(up, sku_id),
        "in_store": in_store,
        "in_sidecar": in_sidecar,
    }


def _not_here(up: Any, sku_id: str) -> str:
    """Why this id names nothing — including the case that reads as data loss."""
    try:
        stale = up.ao_superseded()
        if any(r.get("sku_id") == sku_id for r in stale.get("skus", ())):
            return (
                f"{sku_id!r} is on disk under an older descriptor format, so "
                f"this build cannot read it and cannot edit it. Its name and "
                f"price are recoverable — teach it again from the same "
                f"photograph. Nothing was changed.")
    except Exception:  # noqa: BLE001 - the better message is optional
        pass
    return (f"{sku_id!r} is not in this shop's catalogue, so there is nothing "
            f"to change. Nothing was changed.")


def _codes_of(up: Any, sku_id: str) -> list[str]:
    return sorted(c for c, s in up._codes_load().items() if s == sku_id)


# ------------------------------------------------------------- the two writes --


def _write_sidecar_fields(up: Any, sku_id: str, name: str, price_paise: int
                          ) -> None:
    """Change a name and a price in the appearance-only sidecar, and NOTHING else.

    Read the file, replace two values, write it back. Deliberately not
    `_ao_put`, which rebuilds every vector through numpy on its way out: an
    edit that touches a name and a number has no business rewriting the
    descriptor rows, and the fewest bytes changed is the fewest bytes that can
    go wrong. The thumbnail, the vectors, `taught_with` and `footprint_mm` are
    not read, not re-serialised and not written.
    """
    data = up._ao_load()
    rec = dict(data["skus"][sku_id])
    rec["name"] = name
    rec["price_paise"] = int(paise(price_paise))
    data["skus"][sku_id] = rec
    up._ao_save(data)


def _write_store_fields(up: Any, sku_id: str, name: str, price_paise: int
                        ) -> None:
    """Change a name and a price in the real catalogue, keeping identity.

    `add_sku` with the record's OWN vectors and its OWN footprint, and
    `photo_png=None` — which the store documents as RETAINING the existing
    photograph rather than blanking it. The product is replaced by a record
    that differs in exactly two fields, and the sku id is passed back
    unchanged, so the code bindings and every historical bill still point at
    something.
    """
    store = up.load_store()
    rec = store.get(sku_id)
    if rec is None:  # pragma: no cover - _locations already answered this
        raise AdminRefused(R_UNKNOWN_SKU, _not_here(up, sku_id))
    try:
        result = store.add_sku(sku_id, name, int(paise(price_paise)),
                               rec.vectors, rec.footprint_mm, photo_png=None)
    except AdminRefused:
        raise
    except Exception as exc:  # noqa: BLE001 - the store's own diagnosis, named
        # ShopError, MoneyError and IdentityError are allowed straight through
        # by the store so the caller sees the real reason. Wrapped here rather
        # than swallowed: the message is the useful part, and a generic
        # internal error would hide which of the three refused.
        raise AdminRefused(
            R_WRITE_FAILED,
            f"the catalogue refused this edit ({type(exc).__name__}: {exc}). "
            f"Nothing was changed.") from None
    if not result.ok:
        # The only way an edit reaches this is a catalogue that was already
        # holding two products the identifier cannot separate. Say which.
        raise AdminRefused(
            R_COLLISION if result.collides_with else R_WRITE_FAILED,
            f"{result.message or result.reason}"
            + (f" (against {result.collides_with!r})"
               if result.collides_with else "")
            + " Nothing was changed.")


def _plan_code(up: Any, sku_id: str, code: str) -> dict[str, Any]:
    """Decide what a code change would do — and refuse — WITHOUT writing.

    ONE CODE NAMES ONE PRODUCT. If the code already names a different sku this
    refuses BY NAME: a barcode bound to two products leaves the till with no
    way to choose which packet is on the counter, and it would silently bill
    the wrong one.

    Split from the write on purpose. This is the only step of an edit that can
    be refused because of somebody ELSE's data, so it is decided before the
    price is written and applied after — otherwise a shop could be left with a
    new price, an old code and a 400 saying nothing happened.

    An empty string clears every binding this product has, which is the honest
    reading of an operator emptying the field. What it costs when that is
    wrong: a code that used to price this product now prices nothing and the
    till says so — recoverable in one edit, and every dropped code is named in
    the response.
    """
    codes = up._codes_load()
    had = sorted(c for c, s in codes.items() if s == sku_id)
    if not code:
        return {"code": None, "bound": None, "unbound": had,
                "action": "cleared" if had else "unchanged"}
    owner = codes.get(code)
    if owner is not None and owner != sku_id:
        raise AdminRefused(
            R_CODE_TAKEN,
            f"{code!r} already names {owner!r} in this shop. One code prices "
            f"one product — bound to two, the till has no way to choose which "
            f"packet is on the counter and would silently bill the wrong one. "
            f"Nothing was changed. Forget {owner!r} first, or use the code "
            f"actually printed on this packet.")
    if had == [code]:
        return {"code": code, "bound": None, "unbound": [],
                "action": "unchanged"}
    # Replace rather than accumulate. A code fixed after a typo must not leave
    # the typo behind still pricing this product: that mistyped string belongs
    # to some other manufacturer's packet, and the next time one is scanned it
    # would price this one.
    return {"code": code, "bound": code,
            "unbound": [c for c in had if c != code],
            "action": "rebound" if had else "bound"}


def _apply_code(up: Any, sku_id: str, plan: dict[str, Any]) -> None:
    """Carry out the plan `_plan_code` already refused or approved."""
    if plan["action"] == "unchanged":
        return
    if plan["unbound"]:
        up.unbind_sku(sku_id)
    if plan["bound"]:
        up.bind_code(str(plan["bound"]), sku_id)


# ============================================================ A PHOTOGRAPH ==
#
# Two stores again, and the difference is bigger here than it is for a name.
#
#   THE REAL CATALOGUE keeps a PNG as a file beside `catalog.json` and records
#   its path. `add_sku(..., photo_png=<bytes>)` replaces it; `clear_photo()`
#   removes it. The customer's storefront serves it from `/store/photo/<sku>`.
#
#   THE APPEARANCE-ONLY SIDECAR keeps a small base64 thumbnail inside its JSON,
#   because a product that lives only there has no directory of its own.
#
# WHAT THIS CANNOT DO, SAID PLAINLY RATHER THAN DISCOVERED: a photograph put on
# a product with NO DESCRIPTOR — one typed in at the counter, or taught from a
# printed code — is stored and shown on the shopkeeper's own screens, and does
# NOT yet reach the customer's storefront. `gawaah/storefront.py` looks for
# pictures through `taught_skus()`, which drops zero-vector rows by design, so
# the sidecar thumbnail of such a product is invisible to it. That is a gap in
# `_skus_with_a_photo`/`_photo_png` over there, not something this module may
# reach across and fix. Every response that stores such a photo says so.


def _photo_upload(body: dict[str, Any], key: str = "photo_b64"
                  ) -> Optional[bytes]:
    """The uploaded image, b"" for "remove it", or None when the key is ABSENT.

    The same three-way distinction `_text` draws, for the same reason: on an
    edit, a key nobody sent must not blank a picture.

    Accepts a bare base64 string or the `data:image/png;base64,…` a browser's
    FileReader produces, because making the page strip its own prefix is one
    more thing for the page to get wrong.
    """
    if key not in body:
        return None
    raw = body[key]
    if raw is None:
        return b""
    if not isinstance(raw, str):
        raise AdminRefused(
            R_BAD_BODY,
            f"{key!r} must be base64 text, not {type(raw).__name__}. Nothing "
            f"was changed.")
    s = raw.strip()
    if not s:
        return b""
    if len(s) > MAX_PHOTO_B64_CHARS:
        raise AdminRefused(
            R_PHOTO_TOO_BIG,
            f"that picture is {len(s)} characters of base64 and the cap is "
            f"{MAX_PHOTO_B64_CHARS}. Refused before it was decoded. Take the "
            f"photograph again at a smaller size. Nothing was changed.")
    if s.startswith("data:"):
        marker = ";base64,"
        cut = s.find(marker)
        if cut == -1:
            raise AdminRefused(
                R_BAD_PHOTO,
                "that data: URL is not base64-encoded, so there are no image "
                "bytes in it. Nothing was changed.")
        s = s[cut + len(marker):]
    # `validate=True`: base64 with rubbish in it decodes to rubbish silently
    # otherwise, and the failure would surface as "did not decode as an image",
    # sending the reader to look at their camera instead of their upload.
    try:
        data = base64.b64decode(s, validate=True)
    except Exception:  # noqa: BLE001 - a bad upload is a named answer
        raise AdminRefused(
            R_BAD_PHOTO,
            "that picture did not arrive as valid base64, so nothing could be "
            "decoded from it. Nothing was changed.") from None
    if not data:
        return b""
    return data


def _encoded_photo(data: bytes, *, edge_px: int, cap_bytes: int) -> bytes:
    """Bytes a browser sent -> a bounded PNG, through the catalogue's own door.

    `gawaah/shop_store.py` owns the photo policy — decode, downscale down a
    ladder, refuse rather than store unbounded — and this calls it rather than
    re-implementing it, exactly as `_price_paise_from_rupees` calls that
    module's money door rather than parsing rupees here.
    """
    try:
        from .shop_store import ShopError, encode_photo_png  # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        raise AdminRefused(
            R_INTERNAL,
            f"gawaah.shop_store is not importable ({type(exc).__name__}: "
            f"{exc}), so a photograph cannot be checked. Nothing was changed."
        ) from None
    try:
        return encode_photo_png(data, edge_px=edge_px, cap_bytes=cap_bytes)
    except ShopError as exc:
        raise AdminRefused(R_BAD_PHOTO, f"{exc}. Nothing was changed.") from None
    except Exception as exc:  # noqa: BLE001 - a bad image is still a refusal
        raise AdminRefused(
            R_BAD_PHOTO,
            f"that file could not be read as a picture ({type(exc).__name__}: "
            f"{exc}). Nothing was changed.") from None


def _sidecar_thumb(data: bytes) -> str:
    """A shopkeeper's upload as the base64 thumbnail the sidecar holds."""
    png = _encoded_photo(data, edge_px=SIDECAR_PHOTO_EDGE_PX,
                         cap_bytes=SIDECAR_PHOTO_CAP_BYTES)
    return base64.b64encode(png).decode("ascii")


def _write_sidecar_photo(up: Any, sku_id: str, thumb: Optional[str]) -> None:
    """Set or clear the sidecar thumbnail, and NOTHING else.

    Read the file, replace one value, write it back — deliberately not
    `_ao_put`, for the reason `_write_sidecar_fields` gives: that function
    rebuilds every descriptor row through numpy on its way out, and a picture
    has no business rewriting a vector.
    """
    data = up._ao_load()
    rec = dict(data["skus"][sku_id])
    rec["photo"] = thumb
    data["skus"][sku_id] = rec
    up._ao_save(data)


def _write_store_photo(up: Any, sku_id: str, png: Optional[bytes]) -> None:
    """Set or clear the real catalogue's photograph, keeping identity.

    `png=None` CLEARS. Note that this is the opposite of `add_sku`'s own
    convention, where `photo_png=None` means "retain" — which is why clearing
    goes through `clear_photo()` and never through a None handed to `add_sku`.
    The name and price passed back are the record's own, so nothing but the
    picture moves.
    """
    store = up.load_store()
    rec = store.get(sku_id)
    if rec is None:  # pragma: no cover - _locations already answered this
        raise AdminRefused(R_UNKNOWN_SKU, _not_here(up, sku_id))
    if png is None:
        store.clear_photo(sku_id)
        return
    try:
        result = store.add_sku(sku_id, rec.name, int(rec.price_paise),
                               rec.vectors, rec.footprint_mm, photo_png=png)
    except AdminRefused:
        raise
    except Exception as exc:  # noqa: BLE001 - the store's own diagnosis, named
        raise AdminRefused(
            R_WRITE_FAILED,
            f"the catalogue refused this photograph ({type(exc).__name__}: "
            f"{exc}). Nothing was changed.") from None
    if not result.ok:
        raise AdminRefused(
            R_WRITE_FAILED,
            f"{result.message or result.reason} Nothing was changed.")


# ---------------------------------------------------- PUT a photograph on it --


@router.put("/shop/{sku_id}/photo")
async def set_photo_ep(sku_id: str, request: Request) -> JSONResponse:
    """Give a product a picture, or take its picture away. Nothing else moves.

    THE PRICE, THE NAME, THE CODES AND THE DESCRIPTOR ARE NOT IN THIS BODY and
    cannot be reached from it. A photograph was never part of this counter's
    identity check — the vectors are — so replacing one changes no decision the
    till makes, and this endpoint is the proof of that rather than a promise.
    """
    try:
        body = await _json_body(request)
        return JSONResponse(set_photo(sku_id, body))
    except AdminRefused as exc:
        return _refusal(exc, status=404 if exc.reason == R_UNKNOWN_SKU else 400)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


def set_photo(sku_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """The photo change itself, callable without HTTP so a test can read it."""
    up = _till()
    body = _body_dict(body)
    before = _current(up, sku_id)

    data = _photo_upload(body)
    if data is None:
        raise AdminRefused(
            R_NOTHING_TO_CHANGE,
            "this request carries no picture. Send `photo_b64` with an image, "
            "or an empty string to remove the one this product has.")

    wrote: list[str] = []
    clearing = data == b""
    stored_bytes = 0
    if before["in_sidecar"]:
        thumb = None if clearing else _sidecar_thumb(data)
        # Sized before the write so the response can state it. `thumb` is
        # base64, so the PNG is three quarters of its length, rounded down past
        # the padding — stated as the decoded size because that is the number
        # the cap above is written in.
        stored_bytes = 0 if thumb is None else len(base64.b64decode(thumb))
        _write_sidecar_photo(up, sku_id, thumb)
        wrote.append("appearance_only_sidecar")
    if before["in_store"]:
        png = None if clearing else _encoded_photo(
            data, edge_px=up.load_store().photo_edge_px,
            cap_bytes=up.load_store().photo_cap_bytes)
        stored_bytes = max(stored_bytes, 0 if png is None else len(png))
        _write_store_photo(up, sku_id, png)
        wrote.append("shop_store")

    entry = _audit("sku_photo_cleared" if clearing else "sku_photo_set",
                   sku_id=sku_id, stored_in=wrote, photo_bytes=stored_bytes)

    # WHETHER THE CUSTOMER WILL SEE IT — asked, not assumed. A product with a
    # descriptor has its picture in the real catalogue and the storefront reads
    # it from there. A product with none has it only in the sidecar, which the
    # storefront cannot currently see (the block at the top of this section
    # names exactly where). A shopkeeper who just uploaded a photograph is owed
    # that difference before they go looking for it on their own shutter QR.
    on_storefront = bool(before["in_store"]) and not clearing
    return {
        "ok": True,
        "settles_money": False,
        "reason": "photo_cleared" if clearing else "photo_stored",
        "sku_id": sku_id,
        "has_photo": not clearing,
        "photo_bytes": stored_bytes,
        "stored_in": wrote,
        "on_storefront": on_storefront,
        "storefront_note": (
            None if clearing or on_storefront else
            "Stored, and it shows on your own screens. The customer's "
            "storefront cannot show it yet: it looks for pictures only among "
            "products that have been photographed for recognition, and this "
            "one has not. Teach it from a photo on the Products screen and the "
            "picture reaches the storefront too."),
        "audit": entry,
        "audit_note": ("written to the shop's own catalogue chain"
                       if entry is not None else
                       "THE PICTURE WAS CHANGED BUT COULD NOT BE WRITTEN TO "
                       "THE AUDIT CHAIN"),
        "untouched": (
            "The name, the price, the printed codes, the descriptor vectors "
            "and the footprint were not read and were not written. A "
            "photograph is not part of this counter's identity check, so no "
            "decision the till makes has changed."),
    }


# =========================================================== A NEW PRODUCT ==
#
# TEACHING BY CAMERA IS THE BETTER PATH AND THIS IS NOT IT.
#
# `/enrol` photographs a packet, measures it against the TAKHTI mat, embeds a
# descriptor and stores what the product LOOKS LIKE, so the counter can name it
# across the counter and refuse when it is unsure. Nothing here does any of
# that, and a product added through this door cannot be recognised by sight at
# all: it is a name, a price, and — if the shopkeeper typed one — a printed
# code. It appears on the storefront, it can be put on a bill by hand, and a
# scanner will price it if it has a code. That is all.
#
# It exists because a shopkeeper closing up at eleven at night, putting a new
# sack of rice on the shelf, should not have to find the mat and the light and
# photograph it before the shop can sell it. A catalogue that exists is worth
# more than one that was too much work to build. `do_enrol_code_only` in the
# till makes the same argument and is the same weak thing — this is that path
# with the camera taken out of it, because it required an image it never looked
# at when a code was typed.
#
# The refusal to be quiet about it is deliberate: every response says, in
# words, what this product cannot do.


def _valid_new_sku(raw: str) -> str:
    """A shopkeeper-supplied sku id, or a refusal naming the alphabet."""
    s = raw.strip()
    if not SKU_RE.match(s):
        raise AdminRefused(
            R_BAD_SKU,
            f"{s!r} cannot be an sku id. It becomes a filename and a ledger "
            f"key, so it must start with a letter or a digit and hold only "
            f"letters, digits, '.', '-' and '_', up to 64 characters. Leave "
            f"the field empty and one will be made from the product's name.")
    return s


def _derive_sku_id(up: Any, name: str) -> str:
    """An sku id made from the product's name, unique in this shop.

    A SHOPKEEPER SHOULD NOT HAVE TO INVENT ONE. The id is a machine's handle:
    it is never printed on a bill, never shown to a customer, and — because it
    can never be changed afterwards — it is the single worst field to ask
    somebody to type in a hurry. So it is derived, shown back, and the screen
    says it is permanent.

    The suffix counts UP rather than appending a timestamp or a random string:
    `basmati_rice_5kg_2` is a thing a shopkeeper can recognise in a list three
    months later, and `basmati_rice_5kg_a3f91c` is not.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:MAX_DERIVED_SKU]
    slug = slug.strip("_")
    if not slug or not slug[0].isalnum():
        # Reachable with a name that is entirely punctuation or entirely
        # non-Latin script — Devanagari and Bengali both survive `_valid_name`
        # and both slugify to nothing here. Refused rather than given a made-up
        # id, because the id is permanent and "item_4" is not recognisable.
        raise AdminRefused(
            R_NO_SKU_FROM_NAME,
            f"no sku id could be made out of {name!r}: an sku id is letters "
            f"and digits, and after removing everything else there is nothing "
            f"left. Type one yourself — it is only a machine's handle, so "
            f"something like 'rice_5kg' is right. Nothing was added.")
    in_store, in_sidecar = _locations(up, slug)
    if not (in_store or in_sidecar):
        return slug
    for n in range(2, 1000):
        cand = f"{slug}_{n}"
        s, c = _locations(up, cand)
        if not (s or c):
            return cand
    raise AdminRefused(  # pragma: no cover - 998 products of one name
        R_SKU_TAKEN,
        f"this shop already holds a thousand products called {name!r}. "
        f"Nothing was added.")


@router.post("/shop")
async def add_sku_ep(request: Request) -> JSONResponse:
    """Put a product on the shelf without photographing it.

    Deliberately the same resource the till's `GET /shop` lists and its
    `DELETE /shop/{sku_id}` removes: add it, correct it, forget it.
    """
    try:
        body = await _json_body(request)
        return JSONResponse(add_sku(body), status_code=201)
    except AdminRefused as exc:
        return _refusal(exc, status=409 if exc.reason == R_SKU_TAKEN else 400)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


def add_sku(body: dict[str, Any]) -> dict[str, Any]:
    """The add itself, callable without HTTP so a test can read the result."""
    up = _till()
    body = _body_dict(body)

    name = _text(body, "name", cap=MAX_NAME)
    if not name:
        raise AdminRefused(
            R_NO_NAME,
            "a product needs a name — it is what the shopkeeper reads on the "
            "bill and what the customer reads on the storefront. Nothing was "
            "added.")

    rupees = _text(body, "price_rupees", cap=32)
    if not rupees:
        raise AdminRefused(
            R_BAD_PRICE,
            "a product needs a price. An empty field parses as zero and would "
            "put something on the shelf that bills nothing. Type rupees, like "
            "12 or 12.50. Nothing was added.")
    price_paise = _price_paise_from_rupees(rupees)

    # THE ID IS DERIVED UNLESS ONE IS TYPED, and either way it is checked for
    # collision BEFORE anything is written. Re-using an existing id would not
    # add a product; `_ao_put` would REPLACE one, silently repricing something
    # the shopkeeper did not open.
    typed_id = _text(body, "sku_id", cap=64)
    if typed_id:
        sku_id = _valid_new_sku(typed_id)
        in_store, in_sidecar = _locations(up, sku_id)
        if in_store or in_sidecar:
            current = _current(up, sku_id)
            raise AdminRefused(
                R_SKU_TAKEN,
                f"{sku_id!r} already names {current['name']!r} in this shop. "
                f"Adding it again would REPLACE that product's price, not add "
                f"a second one — and every bill already printed points at this "
                f"id. Correct that product instead, or choose another id. "
                f"Nothing was added.")
    else:
        sku_id = _derive_sku_id(up, name)

    code = _text(body, "code", cap=MAX_CODE)
    if code:
        code = _clean_code(code)
        # Decided before the write for the reason `edit_sku` gives: this is the
        # only step that can be refused because of ANOTHER product's data, so a
        # taken code must not leave a half-made product behind.
        owner = up._codes_load().get(code)
        if owner is not None and owner != sku_id:
            raise AdminRefused(
                R_CODE_TAKEN,
                f"{code!r} already names {owner!r} in this shop. One code "
                f"prices one product — bound to two, the till has no way to "
                f"choose which packet is on the counter and would silently "
                f"bill the wrong one. Nothing was added.")

    photo = _photo_upload(body)
    thumb = None
    if photo:
        thumb = _sidecar_thumb(photo)

    # THE WRITE. `_ao_put` with an EMPTY vector list is what "no descriptor"
    # is: `taught_skus()` skips zero-vector rows, so this product is invisible
    # to every comparison the recogniser makes — which is the truth about it.
    # `priced_skus()` still carries it, so it prices at the till and appears on
    # the storefront.
    replaced = up._ao_put(sku_id, name, int(paise(price_paise)), [], thumb)
    if replaced:  # pragma: no cover - the collision check above precedes this
        raise AdminRefused(
            R_SKU_TAKEN,
            f"{sku_id!r} was already in the catalogue and has been replaced. "
            f"Check its price.")
    if code:
        up.bind_code(code, sku_id)

    # PAISA PRICES FROM THE PUBLISHED MAP, NOT FROM THE CATALOGUE. Without
    # this the product is on the shelf, on the storefront and in the till's
    # list, and falls out of every bill as amber because the money service
    # cannot find a price for it. Best effort, never silent.
    try:
        published = up.publish_price_map()
    except Exception:  # noqa: BLE001 - publishing is subordinate to the add
        published = None

    entry = _audit(
        "sku_added", sku_id=sku_id, name=name,
        # The same field names `sku_edited` writes, so one reader can walk the
        # chain and explain a bill without special-casing the first line of a
        # product's life. `before` is None because there was no price.
        changed=["name", "price"] + (["code"] if code else []),
        stored_in=["appearance_only_sidecar"],
        name_before=None, name_after=name,
        price_paise_before=None, price_paise_after=int(price_paise),
        price_rupees_before=None,
        price_rupees_after=to_rupees_str(int(paise(price_paise))),
        price_published=published is not None,
        codes_before=[], codes_after=([code] if code else []),
        taught_with="typed_at_the_counter", has_photo=thumb is not None)

    return {
        "ok": True,
        "settles_money": False,
        "reason": "sku_added",
        "sku_id": sku_id,
        "sku_id_derived": not typed_id,
        "name": name,
        "price_paise": int(price_paise),
        "price_rupees": to_rupees_str(int(paise(price_paise))),
        "codes": [code] if code else [],
        "has_photo": thumb is not None,
        "stored_in": ["appearance_only_sidecar"],
        "price_published": None if published is None else str(published),
        "price_map_warning": (
            None if published is not None else
            "The merged price map could not be written, so the money service "
            "cannot price this product yet and it will fall out of a bill as "
            "amber until it can."),
        "audit": entry,
        "audit_note": ("written to the shop's own catalogue chain"
                       if entry is not None else
                       "THE PRODUCT WAS ADDED BUT COULD NOT BE WRITTEN TO THE "
                       "AUDIT CHAIN"),
        # SAID EVERY TIME, not once in a help page. A shopkeeper who does not
        # know they added the weak kind cannot choose to fix it.
        "warning": (
            f"TYPED IN, NEVER SEEN. This counter now knows that {name!r} costs "
            f"{to_rupees_str(int(paise(price_paise)))} and NOTHING about what "
            f"it looks like. It cannot be recognised by the camera"
            + (f"; showing {code!r} to a scanner will price it."
               if code else ", and it has no printed code, so it can only be "
                            "added to a bill by hand or from the storefront.")),
        "better": (
            "Photograph it on the Products screen when there is time. Teaching "
            "by appearance is what lets the counter name it across the counter "
            "— and, on the mat, measure it as well."),
        "permanent": (
            f"The sku id {sku_id!r} can never change: it is what the code "
            f"bindings, the orders and every bill printed from now on refer "
            f"to. The NAME is what a person reads and can be corrected any "
            f"time."),
    }


# ------------------------------------------------------------ PATCH a product --


@router.patch("/shop/{sku_id}")
async def edit_sku_ep(sku_id: str, request: Request) -> JSONResponse:
    """Change a taught product's name, price and bound code. Nothing else.

    The vectors, the footprint and the photograph are not read from the request
    and cannot be reached from it. There is no field in this body that names a
    descriptor, and the sku id comes from the PATH, so the one identifier every
    historical record depends on cannot be moved by anything the page sends.
    """
    try:
        body = await _json_body(request)
        return JSONResponse(edit_sku(sku_id, body))
    except AdminRefused as exc:
        return _refusal(exc, status=404 if exc.reason == R_UNKNOWN_SKU else 400)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


def edit_sku(sku_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """The edit itself, callable without HTTP so a test can read the result."""
    up = _till()
    body = _body_dict(body)

    # THE ID IS NOT A FIELD. Refused loudly rather than ignored: an operator
    # who sent it believes the rename happened, and every code binding, order
    # line and past bill points at the old string.
    if "sku_id" in body and str(body.get("sku_id") or "") != sku_id:
        raise AdminRefused(
            R_SKU_RENAME,
            f"this would rename {sku_id!r} to {body.get('sku_id')!r}, and an "
            f"sku id is not a label — it is what the code bindings, the orders "
            f"and every bill already printed refer to. Renaming it would orphan "
            f"all of them and create a lookalike product with no history. "
            f"Change the NAME instead; it is what a person reads. Nothing was "
            f"changed.")

    before = _current(up, sku_id)

    name = _text(body, "name", cap=MAX_NAME)
    if name is not None and not name:
        raise AdminRefused(
            R_NO_NAME,
            "a product with no name cannot be read back off a bill, so it "
            "cannot be corrected either. Nothing was changed.")

    rupees = _text(body, "price_rupees", cap=32)
    price_after: Optional[int] = None
    if rupees is not None:
        if not rupees:
            raise AdminRefused(
                R_BAD_PRICE,
                "the price field is empty. An empty field parses as zero and "
                "would silently bill nothing; to stop selling this product, "
                "forget it. Nothing was changed.")
        price_after = _price_paise_from_rupees(rupees)

    code = _text(body, "code", cap=MAX_CODE)
    if code:
        code = _clean_code(code)

    if name is None and price_after is None and code is None:
        raise AdminRefused(
            R_NOTHING_TO_CHANGE,
            "this request names no field to change. Send a name, a price in "
            "rupees, or a code.")

    price_before = before["price_paise"]
    new_name = name if name is not None else str(before["name"])
    new_price: Optional[int] = (price_after if price_after is not None
                                else price_before)
    if new_price is None:
        # Only reachable for a sidecar row whose price could not be read at
        # all. Writing a name over it would leave a product that still cannot
        # be sold, and pretending otherwise helps nobody.
        raise AdminRefused(
            R_BAD_PRICE,
            f"{sku_id!r} has no readable price in the catalogue, so a name "
            f"change alone would leave it unsellable. Send a price in rupees "
            f"with this edit. Nothing was changed.")

    changed: list[str] = []
    if name is not None and new_name != before["name"]:
        changed.append("name")
    if price_after is not None and price_after != price_before:
        changed.append("price")

    # DECIDE THE CODE BEFORE WRITING ANYTHING, APPLY IT AFTER. It is the only
    # step that can be refused because of another product's data, so deciding
    # it first means a rejected code leaves the price alone; applying it last
    # means a catalogue write that fails leaves the bindings alone. An edit
    # that half-lands is worse than one that does not land at all.
    codes_result: dict[str, Any] = {"code": None, "bound": None,
                                    "unbound": [], "action": "untouched"}
    if code is not None:
        codes_result = _plan_code(up, sku_id, code)
        if codes_result["action"] != "unchanged":
            changed.append("code")

    wrote: list[str] = []
    if changed and ("name" in changed or "price" in changed):
        # BOTH PLACES WHEN IT LIVES IN BOTH. The store shadows the sidecar, so
        # a stale sidecar row is invisible right up to the day the store entry
        # is removed — and then the old price comes back.
        if before["in_sidecar"]:
            _write_sidecar_fields(up, sku_id, new_name, int(new_price))
            wrote.append("appearance_only_sidecar")
        if before["in_store"]:
            _write_store_fields(up, sku_id, new_name, int(new_price))
            wrote.append("shop_store")

    if "code" in changed:
        _apply_code(up, sku_id, codes_result)

    # PAISA PRICES FROM THE PUBLISHED MAP, NOT FROM THE CATALOGUE. Skipping
    # this would leave the money service quoting the old price with the
    # catalogue, the till and the storefront all showing the new one — a
    # disagreement no screen would report. Best effort, never silent.
    published = None
    if "price" in changed:
        try:
            published = up.publish_price_map()
        except Exception:  # noqa: BLE001 - publishing is subordinate to the edit
            published = None

    entry = None
    if changed:
        fields: dict[str, Any] = {
            "sku_id": sku_id,
            "changed": sorted(changed),
            "stored_in": wrote or ["codes_only"],
        }
        if "name" in changed:
            fields["name_before"] = before["name"]
            fields["name_after"] = new_name
        if "price" in changed:
            # BOTH VALUES, ALWAYS. A bill from last week priced this packet at
            # `price_paise_before`; without the pair on one line there is no
            # way to explain that bill, only to disbelieve it.
            fields["price_paise_before"] = (None if price_before is None
                                            else int(price_before))
            fields["price_paise_after"] = int(new_price)
            fields["price_rupees_before"] = (
                None if price_before is None
                else to_rupees_str(int(paise(price_before))))
            fields["price_rupees_after"] = to_rupees_str(int(paise(new_price)))
            fields["price_published"] = published is not None
        if "code" in changed:
            fields["codes_before"] = list(before["codes"])
            fields["codes_after"] = _codes_of(up, sku_id)
        entry = _audit("sku_edited", **fields)

    after = _current(up, sku_id)
    return {
        "ok": True,
        "settles_money": False,
        "reason": "sku_edited" if changed else "nothing_changed",
        "sku_id": sku_id,
        "changed": sorted(changed),
        "stored_in": wrote,
        "before": {"name": before["name"],
                   "price_paise": before["price_paise"],
                   "price_rupees": (None if before["price_paise"] is None
                                    else to_rupees_str(
                                        int(paise(before["price_paise"])))),
                   "codes": before["codes"]},
        "after": {"name": after["name"],
                  "price_paise": after["price_paise"],
                  "price_rupees": (None if after["price_paise"] is None
                                   else to_rupees_str(
                                       int(paise(after["price_paise"])))),
                  "codes": after["codes"]},
        "codes": codes_result,
        "price_published": None if published is None else str(published),
        # Named rather than assumed: a shopkeeper who changed a price is owed
        # the fact that the change is on a chain, or the fact that it is not.
        "audit": entry,
        "audit_note": (
            "written to the shop's own catalogue chain"
            if entry is not None else
            ("nothing to record" if not changed else
             "THE CHANGE WAS MADE BUT COULD NOT BE WRITTEN TO THE AUDIT CHAIN")),
        "untouched": (
            "The descriptor vectors, the footprint in millimetres and the "
            "enrolment photograph were not read and were not written. The sku "
            "id cannot change, so orders and past bills still point at this "
            "product."),
    }


# --------------------------------------------------------- what a price was --


@router.get("/shop/{sku_id}/history")
def sku_history_ep(sku_id: str) -> JSONResponse:
    """Every recorded change to one product, newest last.

    Deliberately does NOT refuse an unknown sku. The reason this chain exists
    is to explain a bill for a packet whose price has since changed — and the
    product may have been forgotten altogether since. An empty list is the
    honest answer for a product that was never edited.
    """
    try:
        path = audit_path()
        ok, lines, head, err = verify(path)
        entries = []
        try:
            for rec in Ledger(path).read():
                if rec.get("sku_id") == sku_id:
                    entries.append(rec)
        except Exception:  # noqa: BLE001 - an unreadable chain is empty + said
            entries = []
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "sku_id": sku_id,
            "count": len(entries),
            "entries": entries,
            # The chain's own verdict travels with the rows it produced. A
            # broken chain read as a clean one is the only way this file could
            # mislead anybody.
            "chain": {"verified": bool(ok), "lines": lines,
                      "head": head, "error": err,
                      "path": str(path)},
        })
    except AdminRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


# ------------------------------------------------------------- the shop itself --


def _shop_name(body: dict[str, Any]) -> str:
    name = _text(body, "name", cap=MAX_SHOP_NAME) or ""
    if not name:
        raise AdminRefused(
            R_NO_SHOP_NAME,
            "the shop needs a name. It is the first thing a customer sees "
            "after scanning the shutter QR, and an unnamed list of prices is "
            "not a shop. Nothing was saved.")
    return name


def _shop_address(body: dict[str, Any]) -> str:
    address = _text(body, "address", cap=MAX_SHOP_ADDRESS, keep_lines=True) or ""
    if not address:
        raise AdminRefused(
            R_NO_ADDRESS,
            "the shop needs an address. A customer ordering for delivery has "
            "to know which shop they are ordering from. Nothing was saved.")
    if len(address) < MIN_SHOP_ADDRESS:
        raise AdminRefused(
            R_SHORT_ADDRESS,
            f"{address!r} is {len(address)} characters. Nobody can find a door "
            f"from that — the shortest usable address here is "
            f"{MIN_SHOP_ADDRESS}. Nothing was saved.")
    return address


def _shop_phone(body: dict[str, Any]) -> dict[str, str]:
    raw = _text(body, "phone", cap=MAX_PHONE_CHARS) or ""
    if not raw:
        raise AdminRefused(
            R_NO_PHONE,
            "the shop needs a phone number. It is how a customer asks where "
            "their order is, and how a wrong address gets fixed. Nothing was "
            "saved.")
    digits = re.sub(r"\D", "", raw)
    # Peel the country code or the trunk 0 off, then insist on the ten digits
    # underneath. Stored bare so two shopkeepers typing +91 and 0 in front of
    # the same number do not end up looking like two different shops.
    for prefix in ("0091", "091", "91", "0"):
        if len(digits) > 10 and digits.startswith(prefix):
            digits = digits[len(prefix):]
            break
    if not MOBILE_RE.match(digits):
        raise AdminRefused(
            R_BAD_PHONE,
            f"{raw!r} is not an Indian mobile number. That is ten digits "
            f"starting 6, 7, 8 or 9 — optionally with +91 in front. A landline "
            f"is refused on purpose: this is the number a customer rings about "
            f"an order that has not arrived. Nothing was saved.")
    return {"phone": digits, "phone_e164": f"+91{digits}"}


def _clock(value: Any, which: str) -> str:
    if not isinstance(value, str):
        raise AdminRefused(
            R_BAD_HOURS,
            f"the {which} time must be text like 07:30, not "
            f"{type(value).__name__}. Nothing was saved.")
    m = TIME_RE.match(value.strip())
    if not m:
        raise AdminRefused(
            R_BAD_HOURS,
            f"{value!r} is not a time. Use a 24-hour clock, like 07:30 or "
            f"21:00. Nothing was saved.")
    hour = int(m.group(1))
    if hour > 23:
        raise AdminRefused(
            R_BAD_HOURS,
            f"{value!r} is not a time — there is no hour {hour}. Nothing was "
            f"saved.")
    return f"{hour:02d}:{m.group(2)}"


def _hours(body: dict[str, Any]) -> dict[str, Any]:
    """Open, close, and which days. Kept simple on purpose.

    One pair of times and a set of days is what a kirana actually has, and it
    is what fits on a printed sheet. A per-day schedule with breaks would be a
    small calendar application, and the shopkeeper would have to maintain it
    for the two afternoons a year it was right.
    """
    raw = body.get("hours")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise AdminRefused(
            R_BAD_HOURS,
            f"'hours' must be an object with an open time, a close time and "
            f"the days, not {type(raw).__name__}. Nothing was saved.")
    opens = _clock(raw.get("open", "07:00"), "opening")
    closes = _clock(raw.get("close", "21:00"), "closing")
    if opens == closes:
        raise AdminRefused(
            R_SAME_HOURS,
            f"the shop opens and closes at {opens}, which describes neither an "
            f"open shop nor a shut one. If it is open around the clock, say "
            f"00:00 to 23:59. Nothing was saved.")

    days_raw = raw.get("days")
    if days_raw is None:
        # The common case, chosen by the server rather than by the page: a
        # kirana is open every day, and a blank form should not accidentally
        # advertise a shop that is shut all week.
        days = list(DAYS)
    else:
        if not isinstance(days_raw, list):
            raise AdminRefused(
                R_BAD_DAY,
                f"'days' must be a list like [\"mon\",\"tue\"], not "
                f"{type(days_raw).__name__}. Nothing was saved.")
        seen = set()
        for d in days_raw:
            key = str(d).strip().lower()[:3]
            if key not in DAYS:
                raise AdminRefused(
                    R_BAD_DAY,
                    f"{d!r} is not a day of the week. Use "
                    f"{', '.join(DAYS)}. Nothing was saved.")
            seen.add(key)
        if not seen:
            raise AdminRefused(
                R_NO_DAYS,
                "the shop is open on no day of the week, which is not opening "
                "hours — it is a closed shop. Tick at least one day, or leave "
                "the days out to mean every day. Nothing was saved.")
        days = [d for d in DAYS if d in seen]

    return {
        "open": opens,
        "close": closes,
        "days": days,
        # Stated rather than left for a reader to work out: a dhaba that shuts
        # at 01:00 is not a shop whose hours were entered backwards.
        "crosses_midnight": closes < opens,
        "days_label": _days_label(days),
        "label": f"{opens}–{closes}",
    }


def _days_label(days: list[str]) -> str:
    """The days as a shopkeeper would say them."""
    if len(days) == len(DAYS):
        return "every day"
    if len(days) == 1:
        return DAY_NAMES[days[0]] + " only"
    shut = [DAY_NAMES[d] for d in DAYS if d not in days]
    if len(shut) == 1:
        return f"every day except {shut[0]}"
    return ", ".join(DAY_NAMES[d] for d in days)


def read_profile() -> Optional[dict[str, Any]]:
    """The stored identity, or None. A corrupt file reads as None, not a crash."""
    p = profile_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - an unreadable profile is an unset one
        return None
    if not isinstance(data, dict) or data.get("format") != PROFILE_FORMAT:
        return None
    return data


def _write_profile(doc: dict[str, Any]) -> None:
    p = profile_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=1)
                 + "\n", encoding="utf-8")


# ------------------------------------------------------------------ the slug --


def slug_base(name: str) -> str:
    """The readable half of a slug: the shop's name, made safe for a URL.

    Lower-cased, every run of anything but a-z/0-9 folded to one hyphen, cut to
    `SLUG_BASE_MAX` at a hyphen so the sticker never ends mid-word. A run of
    six or more digits is removed first (see `_SLUG_DIGIT_RUN`). A name that is
    entirely Devanagari or Bengali survives `_shop_name` and slugs to nothing —
    that shop gets `shop` as its base and is told apart by the suffix alone,
    which is honest: a URL cannot carry that script readably anyway.
    """
    cleaned = _SLUG_DIGIT_RUN.sub(" ", name or "")
    base = re.sub(r"[^a-z0-9]+", "-", cleaned.lower()).strip("-")
    if len(base) > SLUG_BASE_MAX:
        base = base[:SLUG_BASE_MAX]
        cut = base.rfind("-")
        if cut > 0:
            base = base[:cut]
        base = base.strip("-")
    return base or "shop"


def _mint_suffix() -> str:
    import secrets

    return "".join(secrets.choice(SLUG_SUFFIX_ALPHABET)
                   for _ in range(SLUG_SUFFIX_LEN))


def make_slug(name: str) -> str:
    """A fresh slug for this name. Every call is a new suffix — call it once."""
    return f"{slug_base(name)}-{_mint_suffix()}"


def valid_slug(value: Any) -> Optional[str]:
    """`value` as a slug this program would accept, or None."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s if SLUG_RE.match(s) else None


def ensure_slug() -> Optional[str]:
    """This shop's slug, minting one for a profile saved before slugs existed.

    A profile written by an earlier build has a name and no slug. The first
    time the SHOPKEEPER'S side reads it — `/shop/profile`, `/shop/link` — one is
    minted and written back, once. The open `/store/shop` deliberately never
    calls this: a stranger's GET must not be the thing that writes a shop's
    identity, even idempotently. None when there is no profile at all.
    """
    doc = read_profile()
    if doc is None:
        return None
    slug = valid_slug(doc.get("slug"))
    if slug is not None:
        return slug
    slug = make_slug(str(doc.get("name") or ""))
    doc["slug"] = slug
    _write_profile(doc)
    _audit("shop_slug_minted", slug=slug, reason="profile_predates_slugs")
    return slug


@router.get("/shop/profile")
def shop_profile_ep() -> JSONResponse:
    """The shop's own name, address, phone and opening hours.

    A shop that has not been named yet answers `configured: false` with a
    reason, NOT a refusal. Nothing is wrong: the shopkeeper has not filled it
    in. The storefront needs to be able to tell those two apart to decide
    between showing a header and showing a prompt.
    """
    try:
        # A profile saved before slugs existed gets one here, once, so the
        # shopkeeper's own screen never shows a link without it.
        ensure_slug()
        doc = read_profile()
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "configured": doc is not None,
            "profile": doc,
            "path": str(profile_path()),
            "hint": (None if doc is not None else
                     "This shop has no name yet. A customer who scans the "
                     "shutter QR sees a list of prices belonging to nobody."),
            "days": list(DAYS),
        })
    except AdminRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/shop/nameplate")
def shop_nameplate_ep() -> JSONResponse:
    """The name over the door, and nothing else. Readable without signing in.

    WHY THIS EXISTS SEPARATELY FROM `/shop/profile`. With the lock on, every
    shopkeeper route answers 401 — correctly — including the profile. But the
    ONE page a locked-out shopkeeper can reach is the sign-in screen, and that
    screen draws the shop's name above the form. So a counter with the lock on
    greeted its own owner with "This counter has no shop name yet", which is
    not a refusal, it is a false statement: the counter knows the name
    perfectly well and simply would not say it.

    `/shop/profile` cannot be the endpoint that fixes that, because it also
    takes a PUT. Opening that path would let a stranger on the shop's wifi
    rename the shop. This is GET-only and returns two fields.

    NEITHER FIELD IS A SECRET. They are the name and address painted on the
    shutter, which every customer who scans the QR is being invited to read.
    The phone number, the opening hours and the file path stay behind the lock.
    """
    try:
        doc = read_profile() or {}
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "configured": bool(doc),
            "name": doc.get("name") or None,
            "address": doc.get("address") or None,
        })
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.put("/shop/profile")
async def set_shop_profile_ep(request: Request) -> JSONResponse:
    """Set the shop's identity. The whole document, validated here.

    A PUT and not a PATCH: this is one short form a shopkeeper fills in, the
    screen loads the current values into it first, and a partial write would
    make "the address is blank" and "the address was not sent this time"
    indistinguishable.
    """
    try:
        body = await _json_body(request)
        return JSONResponse(set_profile(body))
    except AdminRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


def set_profile(body: dict[str, Any]) -> dict[str, Any]:
    """Validate and store the shop identity. Callable without HTTP."""
    body = _body_dict(body)
    previous = read_profile()

    phone = _shop_phone(body)
    name = _shop_name(body)
    # THE SLUG IS CARRIED, NOT RECOMPUTED. A rename keeps the slug the stickers
    # already carry; only a shop with none yet gets one minted here. The body
    # cannot set it: a slug is not a field a page sends, it is a fact the
    # counter keeps, and letting the form choose it would let two saves of the
    # same shop print two different links.
    slug = valid_slug((previous or {}).get("slug")) or make_slug(name)
    doc: dict[str, Any] = {
        "format": PROFILE_FORMAT,
        "name": name,
        "address": _shop_address(body),
        "phone": phone["phone"],
        "phone_e164": phone["phone_e164"],
        "hours": _hours(body),
        "slug": slug,
        "updated_at": _now_iso(),
    }

    changed = sorted(
        k for k in ("name", "address", "phone", "hours")
        if (previous or {}).get(k) != doc[k]
    )
    _write_profile(doc)

    # THE VALUES ARE NOT ON THE CHAIN, ONLY THE FIELD NAMES. This is the
    # shopkeeper's own live phone number and street address; an audit log is
    # the file most likely to end up pasted into a bug report. What changed and
    # when is enough to explain a storefront that started showing something
    # different.
    entry = _audit("shop_profile_set", changed=changed,
                   first_time=previous is None)

    return {
        "ok": True,
        "settles_money": False,
        "reason": "shop_profile_saved",
        "changed": changed,
        "profile": doc,
        "path": str(profile_path()),
        "audit": entry,
        "note": ("This is what a customer sees after scanning the shutter QR, "
                 "and what a printed sheet carries."),
    }
