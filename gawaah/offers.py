"""OFFERS — a discount the money service can see.

A shopkeeper writes "₹5 off Parle-G" on the shutter. This module is what makes
that sentence true at the till, on the customer's phone, and — the part that is
easy to get wrong and expensive to get wrong — inside `gawaah/paisa.py`, which
is the only process holding gateway credentials and the only one that decides
what is actually charged.

WHY THIS IS NOT A FIELD ON THE BILL
-----------------------------------
paisa does not take a total. It takes a basket, re-prices every line from its
OWN price book, sums it as integer paise, and refuses to mint if the number the
till is showing differs by even one paisa. That refusal is invariant 5 and it is
the reason this program can claim the till proposes and never decides.

So a discount applied in the browser is a bill paisa has never heard of, and a
discount applied in the till is a bill paisa disagrees with. Both end the same
way: `amount_disagreement`, nothing minted, a customer standing at a counter.
Weakening that check to let a discount through would trade the one guarantee
worth having for a feature.

The only place a discount can live, then, is INSIDE THE PRICE BOOK — the thing
paisa re-derives from. `OfferPriceBook` wraps whatever book a deployment plugs
in and answers the discounted price for every lookup, so:

    paisa asks its book for lifebuoy_soap  ->  3150, not 3500

and the sum paisa computes from its own tables IS the discounted sum. Nothing
in paisa.py changes. Invariant 5 is not bent; it is simply told the truth.
`gawaah/live_app.py` does the wrapping — that one line is the whole integration.

WHAT AN OFFER CAN AND CANNOT BE
-------------------------------
A price book answers one question: what does ONE unit of this sku cost. So an
offer is expressible here exactly when it can be written as a per-unit price:

  - flat off a sku          ₹5 off Parle-G          -> yes
  - percent off a sku       10% off Lifebuoy        -> yes
  - flat/percent off ALL    10% off everything      -> yes
  - "₹20 off over ₹500"                             -> NO, and deliberately

A whole-bill threshold is not a per-unit price. There is no honest way to push
it through a price book, and the dishonest ways all end with the till holding a
total paisa never derived. Rather than ship a discount that fails at the moment
money moves, this module refuses that kind by name and says why. The shopkeeper
gets a working discount instead of a broken one.

Because the discount is per unit, ₹5 off Parle-G is ₹5 off EACH packet. That is
also what a shopkeeper means by it, and it is the only reading a price book can
represent, so it is the reading this module documents and tests.

ROUNDING: THE DISCOUNT ROUNDS UP; THE SHOP ABSORBS THE PART-PAISA
-----------------------------------------------------------------
10% of ₹9.99 is 99.9 paise, which is not money. Somebody gets the extra paisa.

    off_paise = (base_paise * percent + 99) // 100        <- ceiling, no float

Rounding the discount DOWN would give 99 paise off ₹9.99, which is 9.90% — and
a shutter that says 10% off would be lying by a tenth of a percent. Rounding it
UP gives 100 paise, 10.01%, and the sign is true. One paisa is the cheapest
price this shop will ever pay for a sign that is not a lie, and the shop is the
party that wrote the sign, so the shop pays it.

Every arithmetic step here is integer. No float, no `/`, no round(). See
`gawaah/money.py` and `tools/lint_no_float.py`.

A DISCOUNT MAY NEVER MAKE SOMETHING FREE
----------------------------------------
₹200 off a ₹10 packet is a typing mistake, not a giveaway. It is caught three
ways, because one is not enough:

  - refused at creation, by name, against the price the product has today;
  - clamped at pricing time to MIN_PRICE_PAISE, because prices change AFTER an
    offer is created and yesterday's sane offer can be today's giveaway;
  - reported as `clamped` on the offers list, so a shopkeeper can see that an
    offer is being held back rather than wondering why the bill looks odd.

WHERE THE FILE LIVES
--------------------
`<shop>/offers.json` — the shopkeeper's own data, next to the catalogue, honou-
ring GAWAAH_SHOP_DIR. Both processes compute the path with `offers_path()` in
THIS module so there is one answer to the question and not two; the day the
till and the money service disagree about where the offers are is the day a
customer is charged full price by a shop that thinks it is running a sale.

A REFUSAL IS A RESULT
---------------------
Every failure below has a name in the response body, a 400, and no 500s.

The router carries no prefix; the paths are absolute. Mount it with
`app.include_router(offers.router)`.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .ledger import Ledger
from .money import MoneyError, from_rupees_str, paise, to_rupees_str

router = APIRouter()


# --------------------------------------------------------------- refusals --
#
# Each of these is a state this module can actually reach, written so the
# person reading it on a phone at a counter knows what to do next.

R_BAD_BODY = "offer_body_not_json"
R_BAD_KIND = "offer_kind_unknown"
R_BILL_KIND = "whole_bill_offer_not_supported"
R_BAD_SKU = "offer_sku_malformed"
R_UNKNOWN_SKU = "sku_not_in_this_shop"
R_BAD_VALUE = "offer_value_not_a_whole_number"
R_NOTHING_OFF = "offer_takes_nothing_off"
R_PERCENT_RANGE = "offer_percent_out_of_range"
R_EXCEEDS_PRICE = "offer_costs_more_than_the_product"
R_NO_OFFER = "no_such_offer"
R_BAD_ACTIVE = "offer_active_not_a_boolean"
R_TOO_MANY = "too_many_offers"
R_TOO_LONG = "field_too_long"
R_UNWRITABLE = "offers_file_unwritable"
R_NO_TILL = "till_module_unavailable"
R_NO_CATALOGUE = "catalogue_unavailable"
R_INTERNAL = "offers_internal_error"


# ----------------------------------------------------------------- shape --

KIND_FLAT = "flat"
KIND_PERCENT = "percent"
KINDS = (KIND_FLAT, KIND_PERCENT)

#: Kinds a shopkeeper might reasonably ask for that a per-unit price book
#: cannot represent. Named separately so the refusal can explain itself rather
#: than saying "unknown", which would read like a typo.
BILL_KINDS = ("bill", "threshold", "bill_threshold", "spend", "cart")

#: The floor a discounted price is clamped to. One paisa, not zero: paisa
#: refuses a zero total outright (`zero_total`), and a line that silently
#: becomes free is exactly the mispriced bill this program exists to prevent.
MIN_PRICE_PAISE = 1

#: A percentage is a whole number strictly between nothing and everything.
#: 0% takes nothing off and 100% makes the product free; both are typing
#: mistakes, and neither is a discount.
MIN_PERCENT = 1
MAX_PERCENT = 99

#: A sanity bound on a flat discount, matched to money.py's own sense of a
#: rupee amount this counter will handle. ₹10,00,000 off is not a sale.
MAX_OFF_PAISE = 100_000_000

MAX_OFFERS = 200
MAX_LABEL = 60

OFFERS_FILENAME = "offers.json"
OFFERS_AUDIT_FILENAME = "offers.audit.jsonl"
OFFERS_FORMAT = 1

OFFER_ID_RE = re.compile(r"^off_[0-9a-f]{12}$")

#: The scope token a browser may send for "every product". `None` means the
#: same thing and is what is stored; this exists because a form field cannot
#: send null and would otherwise send the empty string, which is ambiguous.
ALL_SKUS = "*"


class OfferRefused(Exception):
    """A named refusal with a reason a shopkeeper can act on."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def _refusal(exc: OfferRefused, status: int = 400) -> JSONResponse:
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


# ------------------------------------------------------------- where it is --
#
# Resolved PER CALL, never memoised at import. A test that sets GAWAAH_SHOP_DIR
# in a fixture must be able to change it between tests, and a module-level
# constant captured at import time silently ignores that — which is how a test
# harness once wrote over the live catalogue in results/.

_OVERRIDE: dict[str, Optional[Path]] = {"path": None}

from gawaah import till_ref as _till_ref

#: One definition, in `gawaah/till_ref.py`. This was sixteen copies of a tuple
#: that was missing `__main__`, and every one of them was wrong the same way.
_TILL_NAMES = _till_ref.TILL_NAMES


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def shop_dir() -> Path:
    """The shopkeeper's catalogue directory.

    If the till module is ALREADY LOADED — which it is in the till process, and
    is in any test that has touched it — its own `store_dir()` is authoritative,
    because `set_store_dir()` can move the catalogue without touching the
    environment and a second answer here would leave the offers behind.

    It is looked up in `sys.modules` rather than imported: the money service
    must start on a box with no camera, and importing the till would drag the
    whole vision stack into the process that holds the gateway keys.
    """
    for name in _TILL_NAMES:
        mod = sys.modules.get(name)
        store_dir_fn = getattr(mod, "store_dir", None) if mod is not None else None
        if store_dir_fn is not None:
            try:
                return Path(store_dir_fn())
            except Exception:  # noqa: BLE001 - fall through to the environment
                pass
    override = os.environ.get("GAWAAH_SHOP_DIR")
    if override:
        return Path(override)
    return _repo_root() / "results" / "shop"


def offers_path() -> Path:
    """The one file both processes read. There must not be a second answer.

    `GAWAAH_OFFERS_FILE` is an explicit escape hatch for a deployment that
    splits the till and the money service across machines and syncs one file
    between them; everything else follows the catalogue.
    """
    if _OVERRIDE["path"] is not None:
        return Path(_OVERRIDE["path"])
    explicit = os.environ.get("GAWAAH_OFFERS_FILE")
    if explicit:
        return Path(explicit)
    return shop_dir() / OFFERS_FILENAME


def set_offers_path(path: Any) -> None:
    """Point the offers file somewhere else. For tests and for nothing else."""
    _OVERRIDE["path"] = None if path is None else Path(path)


def audit_path() -> Path:
    """The offers' own hash-chained log.

    DELIBERATELY NOT `results/audit.jsonl`, for the reason `storefront.py`
    records at length: the money service holds that file open in another process
    and keeps the chain head in memory, so a second writer between two of its
    appends breaks `make verify-ledger` on the one log that must be beyond
    argument. Changing a price is worth auditing; corrupting the money ledger to
    do it is not.
    """
    return shop_dir() / OFFERS_AUDIT_FILENAME


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _audit(event: str, **fields: Any) -> Optional[str]:
    """Append one auditable line. Returns the new head, or None if it failed.

    Best effort, but never silent: an endpoint that gets None says so in its
    response rather than reporting a witnessed change that was not written.
    """
    try:
        return Ledger(audit_path()).append(
            ts=_now_iso(), module="offers", event=event, **fields)
    except Exception:  # noqa: BLE001 - a failed audit must not lose an offer
        return None


# ------------------------------------------------------------- the offer --


@dataclass(frozen=True)
class Offer:
    """One discount, exactly as it is stored.

    `value` is integer paise when `kind` is flat, and a whole percentage when
    `kind` is percent. It is deliberately ONE field with a kind beside it rather
    than two nullable ones, so there is no state in which an offer is both and
    no state in which it is neither.

    `sku_id is None` means every product in the shop.
    """

    offer_id: str
    sku_id: Optional[str]
    kind: str
    value: int
    active: bool
    created_at: str
    label: str = ""

    def applies_to(self, sku_id: str) -> bool:
        return self.active and (self.sku_id is None or self.sku_id == sku_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "offer_id": self.offer_id,
            "sku_id": self.sku_id,
            "kind": self.kind,
            "value": int(self.value),
            "active": bool(self.active),
            "created_at": self.created_at,
            "label": self.label,
        }

    def describe(self) -> str:
        """Plain English, the way it would be written on a shutter."""
        what = "everything" if self.sku_id is None else self.sku_id
        if self.kind == KIND_PERCENT:
            return f"{int(self.value)}% off {what}"
        return f"₹{to_rupees_str(int(paise(self.value)))} off {what}"


def _offer_from_record(rec: Any) -> Optional[Offer]:
    """One stored record -> an Offer, or None if it cannot be trusted.

    A record that does not parse is DROPPED rather than raised on. The file is
    read on the money path on every price lookup, and one malformed row must not
    turn a working till into one that refuses every basket. What it costs is
    that the dropped offer is not applied — the customer pays the full marked
    price, the till and paisa still agree, and nothing is mispriced.
    """
    if not isinstance(rec, dict):
        return None
    offer_id = rec.get("offer_id")
    kind = rec.get("kind")
    value = rec.get("value")
    sku_id = rec.get("sku_id")
    if not isinstance(offer_id, str) or not OFFER_ID_RE.match(offer_id):
        return None
    if kind not in KINDS:
        return None
    # bool is a subclass of int in Python, and an offer of `True` paise is not
    # an offer. money.paise() makes the same refusal for the same reason.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if sku_id is not None and (not isinstance(sku_id, str) or not sku_id.strip()):
        return None
    if kind == KIND_PERCENT and not (MIN_PERCENT <= value <= MAX_PERCENT):
        return None
    if kind == KIND_FLAT and not (0 < value <= MAX_OFF_PAISE):
        return None
    return Offer(
        offer_id=offer_id,
        sku_id=sku_id,
        kind=kind,
        value=int(value),
        active=bool(rec.get("active", True)),
        created_at=str(rec.get("created_at") or ""),
        label=str(rec.get("label") or "")[:MAX_LABEL],
    )


# ---------------------------------------------------------------- pricing --


@dataclass(frozen=True)
class Quote:
    """What one sku costs after offers, and which offer did it.

    `clamped` is true when the offer would have driven the price to nothing and
    was held at MIN_PRICE_PAISE instead. It is surfaced rather than swallowed:
    an offer quietly doing something other than what it says is worse than an
    offer visibly refusing to.
    """

    sku_id: str
    base_paise: int
    price_paise: int
    off_paise: int
    offer_id: Optional[str]
    clamped: bool


def discount_off_paise(base_paise: int, kind: str, value: int) -> int:
    """How much comes off ONE unit, in integer paise. Never a float.

    The percentage ceiling is `(a * pct + 99) // 100`: adding 99 before an
    integer floor-divide by 100 is the ceiling of a/100 for any non-negative
    integer, and it gets there with two integer operations and no float. See
    the module docstring for WHY the ceiling and not the floor — it costs the
    shop at most one paisa per unit and it keeps the advertised percentage true.
    """
    base = int(paise(base_paise))
    if kind == KIND_FLAT:
        return int(paise(value))
    if kind == KIND_PERCENT:
        return int(paise((base * int(value) + 99) // 100))
    raise OfferRefused(
        R_BAD_KIND,
        f"{kind!r} is not a kind of offer this counter knows how to price.")


def quote(sku_id: str, base_paise: int, offers: list[Offer]) -> Quote:
    """The price of one unit of `sku_id` after every offer that applies.

    AT MOST ONE OFFER APPLIES, and it is the one that leaves the customer
    paying least. Stacking is refused on purpose: two 60% offers compound to
    84% off, and a rule where adding an offer can silently make a product
    nearly free is a rule nobody can audit at a counter. Ties — two offers that
    land on the same price — go to the one created first, so the answer is the
    same on every machine that reads the same file.
    """
    base = int(paise(base_paise))
    best: Optional[Offer] = None
    best_price = base
    for off in offers:
        if not off.applies_to(sku_id):
            continue
        # A kind this version cannot price is SKIPPED, not raised on. `quote` is
        # called from inside paisa's price book on every lookup of every mint,
        # and an exception there is a 500 in the one process that must never
        # produce one. `load_offers` already refuses to build an Offer with an
        # unknown kind, so this is only reachable by a caller constructing one
        # by hand — but the money path does not get to rely on that.
        if off.kind not in KINDS:
            continue
        candidate = base - discount_off_paise(base, off.kind, off.value)
        if best is None or candidate < best_price:
            best, best_price = off, candidate
        elif candidate == best_price and (off.created_at, off.offer_id) < (
                best.created_at, best.offer_id):
            best = off
    if best is None:
        return Quote(sku_id, base, base, 0, None, False)

    # THE CLAMP. Reached when a flat offer is worth more than the product, which
    # happens without anyone making a new mistake: the shopkeeper drops a price
    # after creating the offer and yesterday's ₹5-off on a ₹40 packet is today's
    # ₹5-off on a ₹4 one. Creation-time validation cannot see the future, so the
    # floor is enforced here as well, every single lookup.
    clamped = best_price < MIN_PRICE_PAISE
    if clamped:
        best_price = MIN_PRICE_PAISE
    return Quote(sku_id, base, int(paise(best_price)),
                 int(paise(base - best_price)), best.offer_id, clamped)


# ------------------------------------------------------------- the store --


def _read_records(path: Path) -> list[Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(doc, list):          # tolerated: a bare list of offers
        return list(doc)
    if isinstance(doc, dict):
        rows = doc.get("offers")
        return list(rows) if isinstance(rows, list) else []
    return []


def load_offers(path: Optional[Path] = None) -> list[Offer]:
    """Every offer on disk, in file order. A missing file is an empty shop.

    Never raises for a bad file: see `_offer_from_record`. A shop with no offers
    and a shop whose offers file is unreadable both charge the marked price,
    which is the only outcome in which the till and the money service still
    agree with each other.
    """
    p = Path(path) if path is not None else offers_path()
    try:
        records = _read_records(p)
    except (OSError, ValueError):
        return []
    out: list[Offer] = []
    for rec in records:
        off = _offer_from_record(rec)
        if off is not None:
            out.append(off)
    return out


def active_offers(path: Optional[Path] = None) -> list[Offer]:
    return [o for o in load_offers(path) if o.active]


def save_offers(offers: list[Offer], path: Optional[Path] = None) -> Path:
    """Write the whole file, atomically. Raises OfferRefused on failure.

    ATOMIC IS NOT OPTIONAL HERE. The money service re-reads this file whenever
    its mtime moves, from another process, with no lock between them. A plain
    `write_text` leaves a window in which paisa reads a half-written document,
    finds no offers in it, and prices a basket at full price while the till is
    showing a discount — and the mint is refused at the counter. `os.replace` is
    atomic on POSIX, so paisa sees either the old file or the new one.
    """
    p = Path(path) if path is not None else offers_path()
    doc = {
        "format": OFFERS_FORMAT,
        "written_at": _now_iso(),
        "offers": [o.as_dict() for o in offers],
    }
    tmp = p.with_name(p.name + f".tmp.{secrets.token_hex(4)}")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(doc, indent=1, sort_keys=False) + "\n",
                       encoding="utf-8")
        os.replace(tmp, p)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise OfferRefused(
            R_UNWRITABLE,
            f"this counter could not write its offers to {p} ({exc}). No offer "
            f"was changed, because an offer the money service cannot read is "
            f"not a discount.") from None
    return p


# ------------------------------------------------------- the price book --


class OfferPriceBook:
    """Any `paisa.PriceBook`, with the shop's active offers applied on lookup.

    THIS IS THE WHOLE INTEGRATION. paisa re-prices every line of every basket
    through the book it was built with; wrapping that book is how a discount
    becomes something paisa DERIVED rather than something it was told. The till
    sends no total paisa has not computed itself, invariant 5 is untouched, and
    the mint goes through at the discounted amount.

    Reload is by mtime, checked per lookup, exactly as `live_app`'s
    `FileBackedPriceBook` reloads prices and for the same reason: the shopkeeper
    creates an offer at three in the afternoon and the money service was started
    at seven in the morning. A stat() per lookup costs nothing that matters; a
    cached stat on a timer buys a window in which the till shows a discount the
    money service has not read yet, and every mint in that window is refused.

    An sku the base book cannot price stays unpriceable. An offer is a discount,
    not a price: 10% off something this counter has never been taught is still
    nothing, and inventing a price here would turn an honest amber line into a
    confident wrong one.
    """

    def __init__(self, base: Any, path: Optional[Path] = None) -> None:
        self._base = base
        self._path = Path(path) if path is not None else None
        self._offers: list[Offer] = []
        self._mtime = None
        self._loaded = False

    def _file(self) -> Path:
        return self._path if self._path is not None else offers_path()

    @staticmethod
    def _stat(p: Path):
        """mtime_ns and size, or None. Integers: this file may not hold a float.

        `st_mtime` is a float and float is banned on the money path, so the
        nanosecond integer is used instead. Size is carried alongside because a
        filesystem with coarse timestamps can let a same-second rewrite through.
        """
        try:
            st = p.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _fresh(self) -> list[Offer]:
        p = self._file()
        stamp = self._stat(p)
        if stamp != self._mtime or not self._loaded:
            self._offers = active_offers(p)
            self._mtime = stamp
            self._loaded = True
        return self._offers

    def price_paise(self, item_id: str) -> Optional[int]:
        base = self._base.price_paise(item_id)
        if base is None:
            return None
        return quote(item_id, int(paise(base)), self._fresh()).price_paise

    def base_price_paise(self, item_id: str) -> Optional[int]:
        """The marked price, before any offer. For reporting, never for minting."""
        return self._base.price_paise(item_id)

    def __len__(self) -> int:
        try:
            return len(self._base)
        except TypeError:
            return 0


def priced_map(base: dict[str, int],
               offers: Optional[list[Offer]] = None) -> dict[str, Quote]:
    """`{sku: base paise}` -> `{sku: Quote}`. Pure; no file, no catalogue."""
    rows = offers if offers is not None else active_offers()
    return {sku: quote(sku, int(paise(v)), rows) for sku, v in base.items()}


# ------------------------------------------------------------- catalogue --
#
# The router needs the shop's marked prices to validate an offer and to show
# what it does. It reads them through the TILL, exactly as `storefront.py` does
# and for the same reason: a second copy of the prices is a second copy that can
# drift. Imported late, inside functions — the till mounts this router, so a
# module-scope import would be a cycle, and the till is expensive.


def _till() -> Any:
    for name in _TILL_NAMES:
        mod = sys.modules.get(name)
        if mod is not None and _till_ref.is_the_till(mod):
            return mod
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from tools import upload_app  # noqa: WPS433 - deliberately late
    except Exception as exc:  # noqa: BLE001 - a missing till is a named answer
        raise OfferRefused(
            R_NO_TILL,
            f"tools/upload_app.py is not importable ({type(exc).__name__}: "
            f"{exc}). Offers are checked against the shopkeeper's own prices "
            f"and this module will not keep a second copy of them.") from None
    return upload_app


def catalogue() -> dict[str, dict[str, Any]]:
    """`{sku_id: {name, price_paise}}` for everything this shop has priced."""
    till = _till()
    try:
        rows = till.priced_skus()
    except Exception as exc:  # noqa: BLE001 - a named answer, never a 500
        raise OfferRefused(
            R_NO_CATALOGUE,
            f"the catalogue could not be read ({type(exc).__name__}: {exc}). "
            f"An offer cannot be checked against a price this counter cannot "
            f"see.") from None
    out: dict[str, dict[str, Any]] = {}
    for sku_id, rec in (rows or {}).items():
        try:
            price = int(paise(rec["price_paise"]))
        except (KeyError, TypeError, ValueError, MoneyError):
            continue
        out[str(sku_id)] = {"name": str(rec.get("name") or sku_id),
                            "price_paise": price}
    return out


def _catalogue_or_empty() -> dict[str, dict[str, Any]]:
    """The catalogue, or nothing. For read-only screens that must still render.

    Listing the offers a shop has must not fail because the vision stack is
    unavailable. The prices become unknown; the offers are still shown.
    """
    try:
        return catalogue()
    except OfferRefused:
        return {}


# -------------------------------------------------------------- validation --


def _text(body: dict[str, Any], key: str, *, cap: int) -> str:
    raw = body.get(key)
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise OfferRefused(
            R_TOO_LONG, f"{key!r} must be text, not a {type(raw).__name__}.")
    value = raw.strip()
    if len(value) > cap:
        raise OfferRefused(
            R_TOO_LONG,
            f"{key!r} is {len(value)} characters; this counter stores at most "
            f"{cap}.")
    return value


def _read_kind(body: dict[str, Any]) -> str:
    raw = body.get("kind")
    if not isinstance(raw, str):
        raise OfferRefused(
            R_BAD_KIND,
            "an offer needs a kind: 'flat' for so many rupees off, 'percent' "
            "for a percentage off.")
    kind = raw.strip().lower()
    if kind in BILL_KINDS:
        raise OfferRefused(
            R_BILL_KIND,
            "a whole-bill offer such as '₹20 off over ₹500' cannot be "
            "priced by this counter. The money service re-prices every basket "
            "one product at a time from its own price book and refuses to mint "
            "a total it did not derive, so a discount that exists only on the "
            "bill would be refused at the moment of payment. Put the offer on "
            "the products instead.")
    if kind not in KINDS:
        raise OfferRefused(
            R_BAD_KIND,
            f"{raw!r} is not a kind of offer. This counter does 'flat' "
            f"(so many rupees off) and 'percent' (a percentage off).")
    return kind


def _read_sku(body: dict[str, Any], known: dict[str, dict[str, Any]]
              ) -> Optional[str]:
    """The sku this offer is on, or None for every product."""
    raw = body.get("sku_id", None)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise OfferRefused(
            R_BAD_SKU,
            f"'sku_id' must be a product id or null for every product, not a "
            f"{type(raw).__name__}.")
    sku = raw.strip()
    if not sku or sku == ALL_SKUS:
        return None
    if len(sku) > 128:
        raise OfferRefused(
            R_BAD_SKU, f"'sku_id' is {len(sku)} characters; that is not a product id.")
    if sku not in known:
        raise OfferRefused(
            R_UNKNOWN_SKU,
            f"{sku!r} is not something this shop has priced. Teach the product "
            f"first — an offer on a product the money service cannot price is a "
            f"discount on nothing.")
    return sku


def _read_whole_number(body: dict[str, Any], key: str) -> Optional[int]:
    """An integer field, or None if absent. A float is a named refusal."""
    if key not in body or body[key] is None:
        return None
    raw = body[key]
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise OfferRefused(
            R_BAD_VALUE,
            f"{key!r} must be a whole number, got {raw!r}. Money is integer "
            f"paise here and a fraction of a paisa is not money.")
    return int(raw)


def _read_off_paise(body: dict[str, Any]) -> int:
    """How many paise come off, from `off_paise` or the rupee STRING `off_rupees`.

    A rupee string is parsed by `money.from_rupees_str`, which never constructs
    a float — `float('5.10')` is already lossy before anything rounds it.
    """
    explicit = _read_whole_number(body, "off_paise")
    if explicit is not None:
        return explicit
    raw = body.get("off_rupees")
    if raw is None:
        raise OfferRefused(
            R_BAD_VALUE,
            "a flat offer needs an amount: send 'off_rupees' as text (\"5.00\") "
            "or 'off_paise' as a whole number of paise.")
    if not isinstance(raw, str):
        raise OfferRefused(
            R_BAD_VALUE,
            f"'off_rupees' must be text such as \"5.00\", not a "
            f"{type(raw).__name__}. A decimal sent as a number is already a "
            f"float by the time it arrives.")
    try:
        return int(from_rupees_str(raw))
    except MoneyError as exc:
        raise OfferRefused(R_BAD_VALUE, f"{raw!r} is not an amount: {exc}") from None


def _validate_value(kind: str, value: int, sku_id: Optional[str],
                    known: dict[str, dict[str, Any]]) -> None:
    """Refuse an offer that takes nothing off, or takes more off than there is."""
    if value <= 0:
        raise OfferRefused(
            R_NOTHING_OFF,
            f"an offer of {value} takes nothing off. If you meant to stop a "
            f"discount, turn the offer off instead of setting it to nothing.")
    if kind == KIND_PERCENT:
        if value < MIN_PERCENT or value > MAX_PERCENT:
            raise OfferRefused(
                R_PERCENT_RANGE,
                f"{value}% is not a discount this counter will apply. A "
                f"percentage runs from {MIN_PERCENT} to {MAX_PERCENT}; "
                f"{MAX_PERCENT + 1}% or more makes the product free, which is "
                f"a giveaway and not a sale.")
        return

    if value > MAX_OFF_PAISE:
        raise OfferRefused(
            R_BAD_VALUE,
            f"{value} paise off is beyond anything this counter will price.")

    # A FLAT OFFER IS CHECKED AGAINST WHAT IT WOULD APPLY TO, NOW.
    # ₹200 off a ₹10 packet is a data-entry mistake and the moment to say so is
    # while the shopkeeper is still looking at the form. For an offer on
    # everything, the product that matters is the CHEAPEST one on the shelf —
    # the ₹3 sachet is the one a blanket ₹200-off would give away.
    if sku_id is not None:
        rec = known.get(sku_id)
        if rec is not None and value >= int(rec["price_paise"]):
            raise OfferRefused(
                R_EXCEEDS_PRICE,
                f"{sku_id!r} is marked at "
                f"₹{to_rupees_str(int(paise(rec['price_paise'])))} and this "
                f"offer takes ₹{to_rupees_str(paise(value))} off it, which "
                f"would leave nothing to charge. A discount may not make a "
                f"product free.")
        return

    if not known:
        return
    cheapest_sku = min(known, key=lambda s: int(known[s]["price_paise"]))
    floor_paise = int(known[cheapest_sku]["price_paise"])
    if value >= floor_paise:
        raise OfferRefused(
            R_EXCEEDS_PRICE,
            f"this offer is on every product, and the cheapest one this shop "
            f"sells is {cheapest_sku!r} at "
            f"₹{to_rupees_str(paise(floor_paise))}. Taking "
            f"₹{to_rupees_str(paise(value))} off it would leave nothing to "
            f"charge. Put the offer on a product instead, or use a percentage.")


def _new_offer_id() -> str:
    return "off_" + secrets.token_hex(6)


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a named refusal
        raise OfferRefused(R_BAD_BODY, "this request's body is not JSON.") from None
    if not isinstance(body, dict):
        raise OfferRefused(
            R_BAD_BODY,
            f"this request's body is a {type(body).__name__}; it must be a "
            f"JSON object.")
    return body


# ----------------------------------------------------------------- views --


def _offer_view(off: Offer, known: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """One offer, with what it currently does to the price it applies to.

    For an offer on everything the effect is shown against the cheapest product,
    because that is the one where a clamp bites first and the one a shopkeeper
    needs to be warned about.
    """
    row = off.as_dict()
    row["says"] = off.describe()
    row["scope"] = "every product" if off.sku_id is None else off.sku_id
    row["off_rupees"] = (to_rupees_str(int(paise(off.value)))
                         if off.kind == KIND_FLAT else None)
    row["percent"] = int(off.value) if off.kind == KIND_PERCENT else None

    # DOES THE PRODUCT THIS OFFER NAMES STILL EXIST?
    #
    # Creating an offer for an unknown sku is refused. Leaving one behind by
    # DELETING the sku is not — the delete path unbinds codes, drops the
    # appearance sidecar and republishes the price map, and never touches
    # offers.json. The asymmetry is the bug, and its shape is worse than a
    # stale row: an offer scoped to `lifebuoy_soap` sits active and inert
    # until somebody teaches a NEW product under that same sku id, at which
    # point it starts taking 10% off on the till, the storefront and at the
    # gateway — all three agreeing, with nobody having chosen it.
    #
    # The delete path is being fixed to deactivate these. This flag is the
    # other half: it is what lets a screen SAY the product is gone instead of
    # printing "1 ACTIVE" beside an offer that applies to nothing. It is only
    # meaningful when the catalogue could actually be read — with no
    # catalogue every sku looks missing, which is a different sentence.
    row["product_missing"] = bool(
        off.sku_id is not None and known and off.sku_id not in known)

    sample = off.sku_id
    if sample is None and known:
        sample = min(known, key=lambda s: int(known[s]["price_paise"]))
    rec = known.get(sample) if sample is not None else None
    if rec is None:
        row["example"] = None
        row["clamped"] = False
        return row
    base = int(rec["price_paise"])
    # The example is what this offer ALONE does, so the shopkeeper reads the
    # offer in front of them rather than the outcome of some other offer that
    # happens to beat it. What actually gets charged is on /offers/prices.
    q = quote(sample, base, [Offer(off.offer_id, off.sku_id, off.kind,
                                   off.value, True, off.created_at, off.label)])
    row["example"] = {
        "sku_id": sample,
        "name": str(rec.get("name") or sample),
        "base_paise": q.base_paise,
        "base_rupees": to_rupees_str(paise(q.base_paise)),
        "price_paise": q.price_paise,
        "price_rupees": to_rupees_str(paise(q.price_paise)),
        "off_paise": q.off_paise,
        "off_rupees": to_rupees_str(paise(q.off_paise)),
    }
    row["clamped"] = q.clamped
    return row


# ---------------------------------------------------------------- routes --


@router.get("/offers")
def offers_ep() -> JSONResponse:
    """Every offer this shop has, newest first, and what each one does.

    Read-only, so it degrades rather than refuses: if the catalogue cannot be
    read the offers are still listed, with their effect on price unknown.
    """
    try:
        known = _catalogue_or_empty()
        rows = load_offers()
        views = [_offer_view(o, known) for o in rows]
        views.reverse()  # newest first; the file is append-ordered
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "count": len(views),
            "active": sum(1 for o in rows if o.active),
            "clamped": sum(1 for v in views if v.get("clamped")),
            "offers": views,
            "file": str(offers_path()),
            "catalogue_known": bool(known),
            "kinds": list(KINDS),
            "percent_range": [MIN_PERCENT, MAX_PERCENT],
            "max_offers": MAX_OFFERS,
        })
    except OfferRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/offers/prices")
def offers_prices_ep() -> JSONResponse:
    """What every product actually costs right now, marked price and after.

    THIS IS THE ENDPOINT A SCREEN SHOWING A DISCOUNTED LINE MUST READ. It is the
    same arithmetic, from the same file, that the money service applies inside
    its own price book — not a second implementation of it — so a till drawing
    `price_paise` from here is showing a number paisa will independently derive
    and agree with.
    """
    try:
        known = catalogue()
        base = {sku: int(rec["price_paise"]) for sku, rec in known.items()}
        rows = active_offers()
        quotes = priced_map(base, rows)
        items = []
        discounted = 0
        for sku in sorted(quotes):
            q = quotes[sku]
            if q.off_paise:
                discounted += 1
            items.append({
                "sku_id": sku,
                "name": str(known[sku].get("name") or sku),
                "base_paise": q.base_paise,
                "base_rupees": to_rupees_str(paise(q.base_paise)),
                "price_paise": q.price_paise,
                "price_rupees": to_rupees_str(paise(q.price_paise)),
                "off_paise": q.off_paise,
                "off_rupees": to_rupees_str(paise(q.off_paise)),
                "offer_id": q.offer_id,
                "clamped": q.clamped,
            })
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "count": len(items),
            "discounted": discounted,
            "active_offers": len(rows),
            "items": items,
        })
    except OfferRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/offers")
async def offers_create_ep(request: Request) -> JSONResponse:
    """Create an offer.

    The browser sends the offer's FIELDS — which product, which kind, how much.
    It never sends a price and it never sends a discounted total. Every number
    below is validated here and the resulting price is computed here, so the
    page is describing an intent, not asserting an outcome.
    """
    try:
        body = await _json_body(request)
        known = catalogue()
        kind = _read_kind(body)
        sku_id = _read_sku(body, known)
        label = _text(body, "label", cap=MAX_LABEL)

        if kind == KIND_PERCENT:
            pct = _read_whole_number(body, "percent")
            if pct is None:
                raise OfferRefused(
                    R_BAD_VALUE,
                    "a percentage offer needs 'percent' as a whole number, for "
                    "example 10 for ten percent off.")
            value = pct
        else:
            value = _read_off_paise(body)
        _validate_value(kind, value, sku_id, known)

        rows = load_offers()
        if len(rows) >= MAX_OFFERS:
            raise OfferRefused(
                R_TOO_MANY,
                f"this counter holds {MAX_OFFERS} offers. Delete one that has "
                f"finished before adding another.")

        off = Offer(
            offer_id=_new_offer_id(),
            sku_id=sku_id,
            kind=kind,
            value=int(value),
            active=bool(body.get("active", True)),
            created_at=_now_iso(),
            label=label,
        )
        rows.append(off)
        path = save_offers(rows)
        head = _audit("offer.created", offer_id=off.offer_id,
                      sku_id=off.sku_id or ALL_SKUS, kind=off.kind,
                      value=int(off.value), active=off.active)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "offer": _offer_view(off, known),
            "file": str(path),
            "audited": head is not None,
            "note": ("The money service reads this file and re-prices every "
                     "basket from it, so this discount is what gets charged."),
        })
    except OfferRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/offers/{offer_id}/active")
async def offers_active_ep(offer_id: str, request: Request) -> JSONResponse:
    """Turn one offer on or off. An offer that is off changes no price at all."""
    try:
        body = await _json_body(request)
        raw = body.get("active")
        if not isinstance(raw, bool):
            raise OfferRefused(
                R_BAD_ACTIVE,
                f"'active' must be true or false, got {raw!r}. There is no "
                f"third state for a discount.")
        rows = load_offers()
        found = next((o for o in rows if o.offer_id == offer_id), None)
        if found is None:
            raise OfferRefused(
                R_NO_OFFER, f"there is no offer {offer_id!r} on this counter.")
        updated = [
            Offer(o.offer_id, o.sku_id, o.kind, o.value, raw, o.created_at, o.label)
            if o.offer_id == offer_id else o
            for o in rows
        ]
        save_offers(updated)
        head = _audit("offer.active", offer_id=offer_id, active=raw)
        known = _catalogue_or_empty()
        changed = next(o for o in updated if o.offer_id == offer_id)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "offer": _offer_view(changed, known),
            "audited": head is not None,
        })
    except OfferRefused as exc:
        return _refusal(exc, status=404 if exc.reason == R_NO_OFFER else 400)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.delete("/offers/{offer_id}")
def offers_delete_ep(offer_id: str) -> JSONResponse:
    """Remove an offer. The product goes back to its marked price immediately."""
    try:
        rows = load_offers()
        found = next((o for o in rows if o.offer_id == offer_id), None)
        if found is None:
            raise OfferRefused(
                R_NO_OFFER, f"there is no offer {offer_id!r} on this counter.")
        save_offers([o for o in rows if o.offer_id != offer_id])
        head = _audit("offer.deleted", offer_id=offer_id,
                      sku_id=found.sku_id or ALL_SKUS, kind=found.kind,
                      value=int(found.value))
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "offer_id": offer_id,
            "removed": found.describe(),
            "audited": head is not None,
        })
    except OfferRefused as exc:
        return _refusal(exc, status=404 if exc.reason == R_NO_OFFER else 400)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/offers/health")
def offers_health_ep() -> JSONResponse:
    """Where the offers file is and whether it can be read.

    `file` is the single most useful line here. The till and the money service
    each compute it with `offers_path()`; if the two processes were started with
    different GAWAAH_SHOP_DIR values they will name different files, the till
    will show a discount the money service has never read, and every mint will
    be refused. Printing the resolved path turns that from a mystery into a
    two-second comparison.
    """
    p = offers_path()
    rows = load_offers(p)
    return JSONResponse({
        "ok": True,
        "module": "offers",
        "settles_money": False,
        "file": str(p),
        "exists": p.exists(),
        "offers": len(rows),
        "active": sum(1 for o in rows if o.active),
        "shop_dir": str(shop_dir()),
        "rounding": "the discount rounds up; the shop absorbs the part-paisa",
        "min_price_paise": MIN_PRICE_PAISE,
    })


__all__ = [
    "ALL_SKUS",
    "KINDS",
    "KIND_FLAT",
    "KIND_PERCENT",
    "MAX_OFFERS",
    "MAX_PERCENT",
    "MIN_PERCENT",
    "MIN_PRICE_PAISE",
    "Offer",
    "OfferPriceBook",
    "OfferRefused",
    "Quote",
    "active_offers",
    "audit_path",
    "catalogue",
    "discount_off_paise",
    "load_offers",
    "offers_path",
    "priced_map",
    "quote",
    "router",
    "save_offers",
    "set_offers_path",
    "shop_dir",
]
