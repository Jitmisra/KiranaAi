"""TARAZU — loose goods sold by weight. Rice, dal, atta, sugar, from the sack.

A kirana sells as much by the scoop as by the packet, and until now this
counter could bill only the packet: "do kilo chawal" was proposed as two
PACKETS of rice with a caution attached, because a weight had nowhere to go.
This module gives it somewhere to go, honestly.

WHAT A WEIGHED PRODUCT IS
-------------------------
A product that is already in the shop's catalogue can be MARKED as sold by
weight, with a price per kilogram in integer paise. The mark lives in
`<shop>/weighed.json`, next to the catalogue, honouring GAWAAH_SHOP_DIR the
same way the offers do. It is a separate file on purpose: the catalogue's own
per-unit price is read by every other part of this program and by the money
service, and a second meaning for that one field would be a second thing that
can drift.

A weighed LINE is a weight in integer GRAMS and the price that follows from it.
A weight arrives as whole grams, or as a kilogram STRING ("2.5") that is read
digit by digit into grams — never through a float, for the same reason the
offers screen sends rupees as text.

THE ARITHMETIC, AND WHICH WAY THE ODD PAISA GOES
------------------------------------------------
    line_paise = price_per_kg_paise * grams // 1000

One integer multiplication and one integer floor-divide. The product of the two
is the exact price in THOUSANDTHS of a paisa; the floor-divide keeps the whole
paise and DROPS the remainder, which is always less than one paisa.

The remainder goes to the CUSTOMER. Rounding down means the shop never charges
for a fraction of a paisa the scale did not show, and the party that chose a
per-kilo price which does not divide into whole paise per gram — the shop —
is the party that absorbs the imprecision of its own price. That is the same
rule `gawaah/offers.py` applies to a percentage that does not come out even:
the shop pays for the sign being true. What it costs: at most one paisa per
line, and the response says exactly how much was dropped, in thousandths of a
paisa, as an integer. Round-half-up would sometimes charge a customer more than
the goods weighed, and there is no reading of "sold by weight" in which that is
right.

A line that comes to ZERO paise — a gram of something cheap — is refused by
name rather than added as a free line: the money service refuses a zero total
outright, and a line that costs nothing on a bill is the mispriced bill this
program exists to prevent.

WHAT THIS MODULE CANNOT DO, STATED RATHER THAN IMPLIED
------------------------------------------------------
It prices a weight and writes the line down. It does NOT put the line on a bill
and it does NOT make the line chargeable: the till owns the basket, and the
money service re-prices every basket from its own price book, which holds one
per-unit price per sku and knows nothing about grams. Until the money service
is taught to price a witness line that carries `grams` — through
`WeighedBook` and `line_paise()` below, so that both processes derive the same
integer from the same file — a weighed line can be priced, shown and recorded
here, and cannot be minted. Every response from this module says so in
`mintable` and `mint_note`, because a screen that showed a weighed line as
chargeable would be a screen that lied at the moment money moved.

A REFUSAL IS A RESULT
---------------------
Every failure below has a name in the response body and a 400. Nothing here
raises a 500. Money and stock changes go on this module's OWN hash chain,
`<shop>/weighed.audit.jsonl`, and never on `results/audit.jsonl`, which is held
open by the money service in another process.

The router carries NO prefix; the paths are absolute. Mount it with
`app.include_router(weighed.router)`.

    GET    /weighed                 every product sold by weight, priced at the presets
    GET    /weighed/health          where the file is, and the rule in words
    POST   /weighed/price           {sku_id, grams | kg} -> a priced line, nothing written
    POST   /weighed/line            the same, written down and audited, with a line_id
    GET    /weighed/line/{line_id}  read a written line back
    GET    /weighed/{sku_id}        one product's per-kilo price
    POST   /weighed/{sku_id}        mark it: {price_per_kg_paise} or {price_per_kg_rupees}
    DELETE /weighed/{sku_id}        unmark it
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
# person reading it at a counter knows what to change.

R_BAD_BODY = "weighed_body_not_json"
R_BAD_SKU = "weighed_sku_malformed"
R_RESERVED_SKU = "weighed_sku_is_a_reserved_word"
R_UNKNOWN_SKU = "sku_not_in_this_shop"
R_NOT_WEIGHED = "not_sold_by_weight"
R_NO_PRICE = "price_per_kg_missing"
R_BAD_PRICE = "price_per_kg_not_integer_paise"
R_PRICE_RANGE = "price_per_kg_out_of_range"
R_NO_WEIGHT = "weight_missing"
R_WEIGHT_TWICE = "weight_given_twice"
R_BAD_GRAMS = "grams_not_a_whole_number"
R_BAD_KG = "kilograms_not_a_decimal"
R_SUB_GRAM = "sub_gram_precision_not_a_weight"
R_WEIGHT_RANGE = "weight_out_of_range"
R_WORTH_NOTHING = "line_comes_to_no_paise"
R_TOO_MANY = "too_many_weighed_products"
R_BAD_LINE_ID = "line_id_malformed"
R_NO_LINE = "no_such_weighed_line"
R_UNWRITABLE = "weighed_file_unwritable"
R_NO_TILL = "till_module_unavailable"
R_NO_CATALOGUE = "catalogue_unavailable"
R_INTERNAL = "weighed_internal_error"

#: Refusals `grams_for()` can make when a spoken sentence is turned into grams.
R_NO_UNIT = "no_weight_unit_in_the_sentence"
R_VOLUME_UNIT = "unit_is_a_volume_not_a_weight"
R_FRACTION_OF_A_GRAM = "fraction_of_a_gram_is_not_a_weight"
R_FRACTION_AND_COUNT = "fraction_and_count_together"
R_UNKNOWN_FRACTION = "fraction_word_not_known"


# ----------------------------------------------------------------- shape --

GRAMS_PER_KG = 1000

#: The smallest and largest weight one line will price. One gram is the floor
#: because a shop scale reads to it; 100 kg is the ceiling because past that
#: the sale is a wholesale run and the counter refuses in the same spirit as
#: the storefront's MAX_QTY. What it costs when wrong: a genuine 100 kg sale
#: is billed as two lines.
MIN_GRAMS = 1
MAX_GRAMS = 100 * GRAMS_PER_KG

#: A per-kilo price is at least one paisa and at most ₹10,00,000 — the same
#: sanity bound money.py's callers use for any single amount.
MIN_PRICE_PER_KG_PAISE = 1
MAX_PRICE_PER_KG_PAISE = 100_000_000

MAX_WEIGHED = 500

#: The presets the screen draws and the list prices every product at, so a
#: shopkeeper sees "250 g → ₹11.25" from the server's own arithmetic.
PRESETS_GRAMS: tuple[int, ...] = (250, 500, 1000, 2000)

WEIGHED_FILENAME = "weighed.json"
WEIGHED_AUDIT_FILENAME = "weighed.audit.jsonl"
LINES_DIRNAME = "weighed"
WEIGHED_FORMAT = 1

LINE_ID_RE = re.compile(r"^wl_[0-9a-f]{12}$")
SKU_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")

#: Path segments this router already answers on. A sku called "price" would be
#: unreachable behind POST /weighed/price, so it is refused at marking time
#: rather than silently shadowed.
RESERVED_SKUS = frozenset({"health", "price", "line", "rule"})

RULE = ("line_paise = price_per_kg_paise × grams ÷ 1000, keeping whole paise "
        "and dropping the remainder. The remainder is always under one paisa "
        "and goes to the customer: the shop never charges for a fraction of a "
        "paisa the scale did not show, and the shop chose the per-kilo price, "
        "so the shop absorbs what it does not divide into.")

MINT_NOTE = ("A weighed line can be priced and written down here, but the "
             "money service prices a bill one product at a time from its own "
             "per-unit price book and does not yet price a weight. Until it "
             "does, a bill with a weighed line on it cannot be charged through "
             "the gateway; this counter says so rather than showing a charge "
             "that would be refused.")


class WeighedRefused(Exception):
    """A named refusal with a reason a shopkeeper can act on."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def _refusal(exc: WeighedRefused, status: int = 400) -> JSONResponse:
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
# Resolved PER CALL, never memoised at import, for the reason offers.py gives:
# a test that sets GAWAAH_SHOP_DIR in a fixture must be able to change it
# between tests, and a module-level constant captured at import silently
# ignores that.

_OVERRIDE: dict[str, Optional[Path]] = {"path": None}

from gawaah import till_ref as _till_ref

#: One definition, in `gawaah/till_ref.py`. This was sixteen copies of a tuple
#: that was missing `__main__`, and every one of them was wrong the same way.
_TILL_NAMES = _till_ref.TILL_NAMES


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def shop_dir() -> Path:
    """The shopkeeper's catalogue directory — the till's own answer first.

    If the till module is ALREADY LOADED, its `store_dir()` is authoritative,
    because `set_store_dir()` can move the catalogue without touching the
    environment and a second answer here would leave the weighed file behind.
    It is looked up in `sys.modules` rather than imported: the money service
    may one day read this file through `WeighedBook`, and it must start on a
    box with no camera without dragging the vision stack in.
    """
    for name in _TILL_NAMES:
        mod = sys.modules.get(name)
        fn = getattr(mod, "store_dir", None) if mod is not None else None
        if fn is not None:
            try:
                return Path(fn())
            except Exception:  # noqa: BLE001 - fall through to the environment
                pass
    override = os.environ.get("GAWAAH_SHOP_DIR")
    if override:
        return Path(override)
    return _repo_root().joinpath("results", "shop")


def weighed_path() -> Path:
    """The one file. `GAWAAH_WEIGHED_FILE` is the split-deployment escape hatch."""
    if _OVERRIDE["path"] is not None:
        return Path(_OVERRIDE["path"])
    explicit = os.environ.get("GAWAAH_WEIGHED_FILE")
    if explicit:
        return Path(explicit)
    return shop_dir().joinpath(WEIGHED_FILENAME)


def set_weighed_path(path: Any) -> None:
    """Point the weighed file somewhere else. For tests and for nothing else."""
    _OVERRIDE["path"] = None if path is None else Path(path)


def lines_dir() -> Path:
    """Written lines live NEXT TO the file they were priced from."""
    return weighed_path().parent.joinpath(LINES_DIRNAME)


def audit_path() -> Path:
    """This module's own hash-chained log.

    DELIBERATELY NOT `results/audit.jsonl`: the money service holds that file
    open in another process and keeps the chain head in memory, so a second
    writer between two of its appends breaks `make verify-ledger` on the one
    log that must be beyond argument.
    """
    return weighed_path().parent.joinpath(WEIGHED_AUDIT_FILENAME)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _audit(event: str, **fields: Any) -> Optional[str]:
    """Append one auditable line. Returns the new head, or None if it failed.

    Best effort, but never silent: an endpoint that gets None says so in its
    response rather than reporting a witnessed change that was not written.
    """
    try:
        return Ledger(audit_path()).append(
            ts=_now_iso(), module="weighed", event=event, **fields)
    except Exception:  # noqa: BLE001 - a failed audit must not lose a line
        return None


# ------------------------------------------------------------ arithmetic --
#
# Pure, integer, and importable by the money service without the rest of this
# file mattering. If paisa ever prices a weight it must call THESE, so that the
# till and the money service derive the same integer from the same inputs.


def _whole(value: Any, *, what: str, reason: str) -> int:
    """An int that is not a bool and not a float. Anything else is refused."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise WeighedRefused(
            reason, f"{what} is {value!r}, which is not a whole number.")
    return int(value)


def line_paise(price_per_kg_paise: int, grams: int) -> int:
    """The price of `grams` at `price_per_kg_paise`, in whole paise. Floor.

    `price * grams` is the exact price in thousandths of a paisa; `// 1000`
    keeps the whole paise. See the module docstring for why the remainder
    goes to the customer. No float, no `/`, no round().
    """
    per_kg = int(paise(_whole(price_per_kg_paise, what="the price per kilo",
                                reason=R_BAD_PRICE)))
    g = _whole(grams, what="the weight in grams", reason=R_BAD_GRAMS)
    if per_kg < 0 or g < 0:
        raise WeighedRefused(
            R_WEIGHT_RANGE, "a weight and a price are both at least zero.")
    return int(paise(per_kg * g // GRAMS_PER_KG))


def dropped_thousandths(price_per_kg_paise: int, grams: int) -> int:
    """How much of a paisa the floor dropped, in thousandths. 0 to 999.

    An integer, so the response can state the loss exactly without a float
    ever appearing: 467 means 0.467 of a paisa went to the customer.
    """
    per_kg = int(paise(_whole(price_per_kg_paise, what="the price per kilo",
                                reason=R_BAD_PRICE)))
    g = _whole(grams, what="the weight in grams", reason=R_BAD_GRAMS)
    return int(per_kg * g % GRAMS_PER_KG)


def grams_from_kg_str(text: Any) -> int:
    """'2.5' -> 2500 grams, read digit by digit. Never through a float.

    Accepts 0 to 3 decimal places — a gram is the finest a shop scale shows,
    and "2.0005 kg" is not a weight anybody weighed.
    """
    if not isinstance(text, str):
        raise WeighedRefused(
            R_BAD_KG, f"kilograms must be text such as '2.5', not a "
                      f"{type(text).__name__}.")
    s = text.strip()
    if not s:
        raise WeighedRefused(R_NO_WEIGHT, "no weight was given.")
    if s.startswith("-"):
        raise WeighedRefused(R_WEIGHT_RANGE, f"{s!r} is below zero.")
    whole, _, frac = s.partition(".")
    if whole and not whole.isdigit():
        raise WeighedRefused(R_BAD_KG, f"{s!r} is not a number of kilograms.")
    if frac and not frac.isdigit():
        raise WeighedRefused(R_BAD_KG, f"{s!r} is not a number of kilograms.")
    if not whole and not frac:
        raise WeighedRefused(R_BAD_KG, f"{s!r} is not a number of kilograms.")
    if len(frac) > 3:
        raise WeighedRefused(
            R_SUB_GRAM,
            f"{s!r} names a fraction of a gram. A shop scale reads to the gram, "
            f"so three decimal places is the most a kilogram figure can carry.")
    frac = (frac + "000")[:3]
    return int(whole or "0") * GRAMS_PER_KG + int(frac)


def describe_grams(grams: int) -> str:
    """2000 -> '2 kg', 250 -> '250 g', 1250 -> '1.25 kg'. Integer arithmetic."""
    g = _whole(grams, what="the weight in grams", reason=R_BAD_GRAMS)
    if g < GRAMS_PER_KG:
        return f"{g} g"
    kg, rem = g // GRAMS_PER_KG, g % GRAMS_PER_KG
    if rem == 0:
        return f"{kg} kg"
    return f"{kg}.{rem:03d}".rstrip("0") + " kg"


def _check_grams_range(g: int) -> int:
    if g < MIN_GRAMS:
        raise WeighedRefused(
            R_WEIGHT_RANGE,
            f"{g} g is below the {MIN_GRAMS} g this counter prices. Nothing "
            f"was added.")
    if g > MAX_GRAMS:
        raise WeighedRefused(
            R_WEIGHT_RANGE,
            f"{describe_grams(g)} is past the {describe_grams(MAX_GRAMS)} this "
            f"counter prices in one line. Bill it as more than one line.")
    return g


# ------------------------------------------- a spoken sentence, in grams --
#
# For the assistant. "do kilo chawal" arrives as qty=2, unit="kilo"; "aadha
# kilo daal" as fraction="aadha", unit="kilo". This turns those into grams and
# refuses, by name, the shapes that are not a weight at all.

KILO_WORDS = frozenset({"kilo", "kilos", "kg", "kgs", "kilogram", "kilograms"})
GRAM_WORDS = frozenset({"gram", "grams", "gm", "gms", "g"})
VOLUME_WORDS = frozenset({"litre", "litres", "liter", "liters", "ltr", "l", "ml"})

#: The Hindi fractions a kirana actually uses, in grams. `paune` on its own is
#: three-quarters; "paune do" (one and three-quarters) is a count and a
#: fraction together and is refused rather than guessed — see `grams_for`.
FRACTION_GRAMS: dict[str, int] = {
    "aadha": 500, "adha": 500, "aadhi": 500, "adhi": 500, "half": 500,
    "dedh": 1500, "derh": 1500, "dedh_kilo": 1500,
    "dhai": 2500, "dhaai": 2500,
    "sava": 1250, "savva": 1250, "sawa": 1250,
    "paune": 750, "pauna": 750,
    "pav": 250, "pao": 250, "quarter": 250,
}


def grams_for(qty: Optional[int], unit: Optional[str],
              fraction: Optional[str] = None) -> int:
    """(2, 'kilo') -> 2000; (None, 'kilo', 'aadha') -> 500; (250, 'gram') -> 250.

    Refuses a volume ('do litre') because a litre is not grams and pretending
    it is would price milk by a density this counter has never measured.
    """
    u = (unit or "").strip().lower()
    f = (fraction or "").strip().lower()
    if u in VOLUME_WORDS:
        raise WeighedRefused(
            R_VOLUME_UNIT,
            f"'{u}' is a volume. This counter weighs in grams and does not "
            f"turn a litre into a weight.")
    if f:
        if f not in FRACTION_GRAMS:
            raise WeighedRefused(
                R_UNKNOWN_FRACTION, f"'{f}' is not a fraction this counter "
                                    f"knows. Say the weight in grams.")
        if u in GRAM_WORDS:
            raise WeighedRefused(
                R_FRACTION_OF_A_GRAM,
                f"'{f} {u}' is a fraction of a gram, which no scale shows. Say "
                f"the weight in grams.")
        if u and u not in KILO_WORDS:
            raise WeighedRefused(
                R_NO_UNIT, f"'{u}' is not a weight. Say kilo or gram.")
        if qty is not None and _whole(qty, what="the count",
                                      reason=R_BAD_GRAMS) != 1:
            raise WeighedRefused(
                R_FRACTION_AND_COUNT,
                f"'{f}' together with a count of {qty} is ambiguous. Say the "
                f"weight in grams instead.")
        return _check_grams_range(FRACTION_GRAMS[f])
    n = 1 if qty is None else _whole(qty, what="the count", reason=R_BAD_GRAMS)
    if u in KILO_WORDS:
        return _check_grams_range(n * GRAMS_PER_KG)
    if u in GRAM_WORDS:
        return _check_grams_range(n)
    raise WeighedRefused(
        R_NO_UNIT, "no weight unit was said. Say kilo or gram, and the "
                   "counter will price the weight.")


# ------------------------------------------------------------- the store --


@dataclass(frozen=True)
class WeighedSku:
    """One product sold by weight, exactly as it is stored."""

    sku_id: str
    price_per_kg_paise: int
    since: str

    def as_dict(self) -> dict[str, Any]:
        return {"sku_id": self.sku_id,
                "price_per_kg_paise": int(self.price_per_kg_paise),
                "since": self.since}


def _from_record(rec: Any) -> Optional[WeighedSku]:
    """One stored record -> a WeighedSku, or None if it cannot be trusted.

    A record that does not parse is DROPPED, not raised on: the product is
    then simply not sold by weight until the shopkeeper marks it again, and
    nothing is mispriced.
    """
    if not isinstance(rec, dict):
        return None
    sku_id = rec.get("sku_id")
    price = rec.get("price_per_kg_paise")
    if not isinstance(sku_id, str) or not SKU_RE.match(sku_id):
        return None
    if isinstance(price, bool) or not isinstance(price, int):
        return None
    if not (MIN_PRICE_PER_KG_PAISE <= price <= MAX_PRICE_PER_KG_PAISE):
        return None
    return WeighedSku(sku_id=sku_id, price_per_kg_paise=int(price),
                      since=str(rec.get("since") or ""))


def load_weighed(path: Optional[Path] = None) -> dict[str, WeighedSku]:
    """Every weighed product on disk, by sku. A missing file is an empty shop."""
    p = Path(path) if path is not None else weighed_path()
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rows = doc.get("weighed") if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        return {}
    out: dict[str, WeighedSku] = {}
    for rec in rows:
        w = _from_record(rec)
        if w is not None:
            out[w.sku_id] = w
    return out


def save_weighed(rows: dict[str, WeighedSku],
                 path: Optional[Path] = None) -> Path:
    """Write the whole file, atomically. `os.replace` so a reader in another
    process — the money service, one day — sees the old file or the new one
    and never half of either."""
    p = Path(path) if path is not None else weighed_path()
    doc = {
        "format": WEIGHED_FORMAT,
        "written_at": _now_iso(),
        "rule": RULE,
        "weighed": [rows[k].as_dict() for k in sorted(rows)],
    }
    tmp = p.with_name(p.name + f".tmp.{secrets.token_hex(4)}")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, p)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise WeighedRefused(
            R_UNWRITABLE,
            f"this counter could not write {p} ({exc}). Nothing was changed, "
            f"because a mark the money service cannot read is not a price."
        ) from None
    return p


class WeighedBook:
    """Per-kilo prices, reloaded by mtime on every lookup.

    THE HANDLE THE MONEY SERVICE WOULD HOLD. Shaped like `offers.OfferPriceBook`
    for the same reason: paisa must derive a weighed line from the same file
    and the same function the till used, or the two disagree by a paisa and
    the mint is refused with a customer at the counter. `st_mtime_ns` and not
    `st_mtime`, because the latter is a float and this is the money path.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path is not None else None
        self._rows: dict[str, WeighedSku] = {}
        self._stamp: Optional[tuple[int, int]] = None
        self._loaded = False

    def _file(self) -> Path:
        return self._path if self._path is not None else weighed_path()

    @staticmethod
    def _stat(p: Path) -> Optional[tuple[int, int]]:
        try:
            st = p.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _fresh(self) -> dict[str, WeighedSku]:
        p = self._file()
        stamp = self._stat(p)
        if stamp != self._stamp or not self._loaded:
            self._rows = load_weighed(p)
            self._stamp = stamp
            self._loaded = True
        return self._rows

    def price_per_kg_paise(self, sku_id: str) -> Optional[int]:
        row = self._fresh().get(str(sku_id))
        return None if row is None else int(row.price_per_kg_paise)

    def line_paise(self, sku_id: str, grams: int) -> Optional[int]:
        """The line, or None when the sku is not sold by weight. Never a guess."""
        per_kg = self.price_per_kg_paise(sku_id)
        if per_kg is None:
            return None
        return line_paise(per_kg, grams)

    def __len__(self) -> int:
        return len(self._fresh())


# ------------------------------------------------------------- catalogue --
#
# Marking needs the shop's catalogue to know the product exists and what it
# is called. Read through the TILL, as storefront.py and offers.py do, so
# there is no second copy of the names. Imported late: the till mounts this
# router, and the till is expensive.


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
        raise WeighedRefused(
            R_NO_TILL,
            f"tools/upload_app.py is not importable ({type(exc).__name__}: "
            f"{exc}). Products sold by weight are looked up in the shop's own "
            f"catalogue and this module will not keep a second one.") from None
    return upload_app


def catalogue() -> dict[str, dict[str, Any]]:
    """`{sku_id: {name, price_paise}}` for everything this shop has priced.

    `priced_skus()` — the marked, undiscounted per-unit price — because this
    module only needs to know that the product exists and what it is called.
    Offers apply to packets; a per-kilo price is its own number.
    """
    till = _till()
    try:
        rows = till.priced_skus()
    except Exception as exc:  # noqa: BLE001 - a named answer, never a 500
        raise WeighedRefused(
            R_NO_CATALOGUE,
            f"the catalogue could not be read ({type(exc).__name__}: {exc}). "
            f"A product cannot be marked as sold by weight if this counter "
            f"cannot see it.") from None
    out: dict[str, dict[str, Any]] = {}
    for sku_id, rec in (rows or {}).items():
        try:
            unit = int(paise(rec["price_paise"]))
        except (KeyError, TypeError, ValueError, MoneyError):
            continue
        out[str(sku_id)] = {"name": str(rec.get("name") or sku_id),
                            "price_paise": unit}
    return out


def _catalogue_or_empty() -> dict[str, dict[str, Any]]:
    """The catalogue, or nothing. For the list, which must still render."""
    try:
        return catalogue()
    except WeighedRefused:
        return {}


# -------------------------------------------------------------- validation --


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a named refusal
        raise WeighedRefused(R_BAD_BODY, "this request's body is not JSON.") from None
    if not isinstance(body, dict):
        raise WeighedRefused(
            R_BAD_BODY,
            f"this request's body is a {type(body).__name__}; it must be a "
            f"JSON object.")
    return body


def _valid_sku(raw: Any) -> str:
    """Charset-checked BEFORE it is used as a key or joined to anything."""
    if not isinstance(raw, str) or not raw.strip():
        raise WeighedRefused(R_BAD_SKU, "no sku_id was given.")
    s = raw.strip()
    if not SKU_RE.match(s):
        raise WeighedRefused(
            R_BAD_SKU, f"{s!r} is not a product id this shop would have.")
    return s


def _read_price_per_kg(body: dict[str, Any]) -> int:
    """`price_per_kg_paise` as an int, or `price_per_kg_rupees` as a STRING.

    The rupee form is text on purpose: a decimal sent as a JSON number is a
    float by the time it is parsed, and float('45.10') is already lossy.
    """
    has_paise = body.get("price_per_kg_paise") is not None
    has_rupees = body.get("price_per_kg_rupees") is not None
    if not has_paise and not has_rupees:
        raise WeighedRefused(
            R_NO_PRICE,
            "a price per kilogram is needed: 'price_per_kg_paise' as a whole "
            "number, or 'price_per_kg_rupees' as text such as '45.00'.")
    if has_paise:
        raw = body.get("price_per_kg_paise")
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise WeighedRefused(
                R_BAD_PRICE,
                f"'price_per_kg_paise' is {raw!r}; it must be a whole number "
                f"of paise. 45.50 rupees is 4550.")
        per_kg = int(raw)
    else:
        raw = body.get("price_per_kg_rupees")
        if not isinstance(raw, str):
            raise WeighedRefused(
                R_BAD_PRICE,
                f"'price_per_kg_rupees' must be text such as '45.00', not a "
                f"{type(raw).__name__}.")
        try:
            per_kg = int(from_rupees_str(raw))
        except MoneyError as exc:
            raise WeighedRefused(
                R_BAD_PRICE, f"{raw!r} is not a rupee amount ({exc}).") from None
    if per_kg < MIN_PRICE_PER_KG_PAISE:
        raise WeighedRefused(
            R_PRICE_RANGE,
            f"{per_kg} paise per kilo is nothing. A product sold by weight "
            f"has a price.")
    if per_kg > MAX_PRICE_PER_KG_PAISE:
        raise WeighedRefused(
            R_PRICE_RANGE,
            f"₹{to_rupees_str(paise(per_kg))} per kilo is past the "
            f"₹{to_rupees_str(paise(MAX_PRICE_PER_KG_PAISE))} this counter "
            f"will price. Check the number.")
    return per_kg


def _read_grams(body: dict[str, Any]) -> int:
    """`grams` as a whole number, or `kg` as text. Exactly one of them."""
    has_g = body.get("grams") is not None
    has_kg = body.get("kg") is not None
    if has_g and has_kg:
        raise WeighedRefused(
            R_WEIGHT_TWICE,
            "both 'grams' and 'kg' were given. Send one weight, in one unit.")
    if not has_g and not has_kg:
        raise WeighedRefused(
            R_NO_WEIGHT,
            "no weight was given: send 'grams' as a whole number or 'kg' as "
            "text such as '2.5'.")
    if has_g:
        raw = body.get("grams")
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise WeighedRefused(
                R_BAD_GRAMS,
                f"'grams' is {raw!r}; a weight in grams is a whole number. For "
                f"a fraction of a kilo send 'kg' as text, such as '1.5'.")
        g = int(raw)
    else:
        g = grams_from_kg_str(body.get("kg"))
    return _check_grams_range(g)


# ------------------------------------------------------------- the line --


def _priced(sku_id: str, name: str, row: WeighedSku, grams: int) -> dict[str, Any]:
    """The priced line, with its arithmetic shown and its limit stated."""
    per_kg = int(row.price_per_kg_paise)
    line = line_paise(per_kg, grams)
    dropped = dropped_thousandths(per_kg, grams)
    if line <= 0:
        raise WeighedRefused(
            R_WORTH_NOTHING,
            f"{describe_grams(grams)} of {name} at "
            f"₹{to_rupees_str(paise(per_kg))} a kilo comes to less than one "
            f"paisa, and a line that costs nothing does not go on a bill. "
            f"Weigh more of it.")
    weight = describe_grams(grams)
    return {
        "sku_id": sku_id,
        "name": name,
        "grams": int(grams),
        "weight": weight,
        "price_per_kg_paise": per_kg,
        "price_per_kg_rupees": to_rupees_str(paise(per_kg)),
        "line_paise": line,
        "line_rupees": to_rupees_str(paise(line)),
        "exact_thousandths_of_a_paisa": int(per_kg * grams),
        "dropped_thousandths_of_a_paisa": dropped,
        "rule": RULE,
        "arithmetic": f"{per_kg} × {grams} // 1000 = {line}",
        # Shaped exactly like the till's BasketLine, so the till can put it on
        # the bill without inventing a field. qty is 1: this is ONE line for
        # ONE weighing, and a second scoop of the same rice is a second line.
        "basket_line": {
            "sku_id": sku_id,
            "name": f"{name} · {weight}",
            "price_paise": line,
            "qty": 1,
            "by": "weighed",
        },
        "mintable": False,
        "mint_note": MINT_NOTE,
    }


def _line_from(sku_id: str, grams: int) -> dict[str, Any]:
    rows = load_weighed()
    row = rows.get(sku_id)
    if row is None:
        known = sorted(rows)
        raise WeighedRefused(
            R_NOT_WEIGHED,
            f"{sku_id!r} is not sold by weight at this counter. Mark it with a "
            f"price per kilo first. Sold by weight: "
            f"{', '.join(known[:6]) or 'nothing yet'}"
            f"{'…' if len(known) > 6 else ''}.")
    cat = _catalogue_or_empty()
    name = str((cat.get(sku_id) or {}).get("name") or sku_id)
    return _priced(sku_id, name, row, grams)


def _valid_line_id(line_id: Any) -> str:
    s = (line_id or "").strip() if isinstance(line_id, str) else ""
    if not LINE_ID_RE.match(s):
        raise WeighedRefused(
            R_BAD_LINE_ID,
            f"{line_id!r} is not a weighed line id. They look like 'wl_' "
            f"followed by twelve hex characters.")
    return s


def _write_line(doc: dict[str, Any]) -> Path:
    d = lines_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d.joinpath(f"{doc['line_id']}.json")
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, sort_keys=True, indent=1) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)
    return p


def _read_line(line_id: str) -> dict[str, Any]:
    p = lines_dir().joinpath(f"{_valid_line_id(line_id)}.json")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise WeighedRefused(
            R_NO_LINE, f"this counter has no weighed line {line_id!r}.") from None
    except Exception as exc:  # noqa: BLE001 - a corrupt file is not a crash
        raise WeighedRefused(
            R_NO_LINE,
            f"weighed line {line_id!r} is on disk but could not be read "
            f"({type(exc).__name__}: {exc}).") from None
    if not isinstance(doc, dict) or doc.get("line_id") != line_id:
        raise WeighedRefused(
            R_NO_LINE, f"weighed line {line_id!r} is not a weighed line.")
    return doc


def _row_view(sku_id: str, row: WeighedSku,
              cat: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rec = cat.get(sku_id)
    name = str((rec or {}).get("name") or sku_id)
    per_kg = int(row.price_per_kg_paise)
    examples = []
    for g in PRESETS_GRAMS:
        lp = line_paise(per_kg, g)
        examples.append({"grams": g, "weight": describe_grams(g),
                         "line_paise": lp, "line_rupees": to_rupees_str(paise(lp)),
                         "dropped_thousandths_of_a_paisa":
                             dropped_thousandths(per_kg, g)})
    view: dict[str, Any] = {
        "sku_id": sku_id,
        "name": name,
        "in_catalogue": rec is not None,
        "price_per_kg_paise": per_kg,
        "price_per_kg_rupees": to_rupees_str(paise(per_kg)),
        "since": row.since,
        "examples": examples,
    }
    if rec is not None:
        # The per-packet price the catalogue still holds. Shown so a shopkeeper
        # can see the two numbers side by side rather than wonder which one a
        # packet read by the camera will be billed at (that one).
        view["catalogue_price_paise"] = int(rec["price_paise"])
        view["catalogue_price_rupees"] = to_rupees_str(int(paise(rec["price_paise"])))
    return view


# ----------------------------------------------------------------- routes --


@router.get("/weighed")
def weighed_list_ep() -> JSONResponse:
    """Every product sold by weight, with what the presets cost. Read-only."""
    try:
        rows = load_weighed()
        # Read once, and remember whether it was readable: an empty catalogue
        # and an unreadable one both price nothing, but the screen has to say
        # which, because they need different fixes.
        try:
            cat = catalogue()
            catalogue_known = True
        except WeighedRefused:
            cat = {}
            catalogue_known = False
        items = [_row_view(k, rows[k], cat) for k in sorted(rows)]
        # Products the shopkeeper COULD mark: priced, and not already weighed.
        markable = [{"sku_id": k, "name": v["name"],
                     "price_paise": int(v["price_paise"]),
                     "price_rupees": to_rupees_str(int(paise(v["price_paise"])))}
                    for k, v in sorted(cat.items()) if k not in rows]
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "count": len(items),
            "items": items,
            "markable": markable,
            "catalogue_known": catalogue_known,
            "presets_grams": list(PRESETS_GRAMS),
            "limits": {"min_grams": MIN_GRAMS, "max_grams": MAX_GRAMS,
                       "min_price_per_kg_paise": MIN_PRICE_PER_KG_PAISE,
                       "max_price_per_kg_paise": MAX_PRICE_PER_KG_PAISE,
                       "max_weighed": MAX_WEIGHED},
            "rule": RULE,
            "mintable": False,
            "mint_note": MINT_NOTE,
            "file": str(weighed_path()),
        })
    except WeighedRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/weighed/health")
def weighed_health_ep() -> JSONResponse:
    """Where the file is, whether it exists, and the rule in words."""
    try:
        p = weighed_path()
        rows = load_weighed(p)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "module": "weighed",
            "file": str(p),
            "exists": p.exists(),
            "lines_dir": str(lines_dir()),
            "audit": str(audit_path()),
            "shop_dir": str(shop_dir()),
            "count": len(rows),
            "rule": RULE,
            "presets_grams": list(PRESETS_GRAMS),
            "min_grams": MIN_GRAMS,
            "max_grams": MAX_GRAMS,
            "mintable": False,
            "mint_note": MINT_NOTE,
        })
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/weighed/price")
async def weighed_price_ep(request: Request) -> JSONResponse:
    """Price a weight. Nothing is written; this is what the screen shows live.

    Body: {sku_id, grams} or {sku_id, kg: "2.5"}. The browser names a product
    and a weight; every paisa in the answer is the server's.
    """
    try:
        body = await _json_body(request)
        sku_id = _valid_sku(body.get("sku_id"))
        grams = _read_grams(body)
        line = _line_from(sku_id, grams)
        return JSONResponse({"ok": True, "settles_money": False,
                             "written": False, **line})
    except WeighedRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(WeighedRefused(R_BAD_PRICE, str(exc)))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/weighed/line")
async def weighed_line_ep(request: Request) -> JSONResponse:
    """Price a weight AND write the line down, under an id, on this chain.

    The till adds the returned `basket_line` to its bill and keeps `line_id`;
    the line can be read back by id so nothing the browser holds has to be
    believed later. This does not bill anything and cannot be minted yet —
    see `mint_note`.
    """
    try:
        body = await _json_body(request)
        sku_id = _valid_sku(body.get("sku_id"))
        grams = _read_grams(body)
        line = _line_from(sku_id, grams)
        line_id = "wl_" + secrets.token_hex(6)
        now = _now_iso()
        doc = {"format": WEIGHED_FORMAT, "line_id": line_id, "at": now, **line}
        path = _write_line(doc)
        head = _audit("weighed.line", line_id=line_id, sku_id=sku_id,
                      grams=int(grams),
                      price_per_kg_paise=int(line["price_per_kg_paise"]),
                      line_paise=int(line["line_paise"]),
                      dropped_thousandths_of_a_paisa=int(
                          line["dropped_thousandths_of_a_paisa"]),
                      minted=False)
        return JSONResponse({
            "ok": True, "settles_money": False, "written": True,
            "line_id": line_id, "at": now, "file": str(path),
            "audited": head is not None,
            **line,
            "note": ("This line is priced and written down. Nothing is on a "
                     "bill until the till adds it, and nothing is charged "
                     "until the gateway's own signed callback says so."),
        })
    except WeighedRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(WeighedRefused(R_BAD_PRICE, str(exc)))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/weighed/line/{line_id}")
def weighed_line_read_ep(line_id: str) -> JSONResponse:
    """Read a written line back, as the server wrote it."""
    try:
        doc = _read_line(line_id)
        return JSONResponse({"ok": True, "settles_money": False, **doc})
    except WeighedRefused as exc:
        return _refusal(exc, status=404 if exc.reason == R_NO_LINE else 400)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/weighed/{sku_id}")
def weighed_get_ep(sku_id: str) -> JSONResponse:
    """One product's per-kilo price, or a named refusal if it has none."""
    try:
        sku = _valid_sku(sku_id)
        rows = load_weighed()
        row = rows.get(sku)
        if row is None:
            raise WeighedRefused(
                R_NOT_WEIGHED,
                f"{sku!r} is not sold by weight at this counter.")
        return JSONResponse({"ok": True, "settles_money": False,
                             **_row_view(sku, row, _catalogue_or_empty()),
                             "rule": RULE})
    except WeighedRefused as exc:
        return _refusal(exc, status=404 if exc.reason == R_NOT_WEIGHED else 400)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/weighed/{sku_id}")
async def weighed_mark_ep(sku_id: str, request: Request) -> JSONResponse:
    """Mark a product as sold by weight, or change its per-kilo price.

    Body: {"price_per_kg_paise": 4500} or {"price_per_kg_rupees": "45.00"}.
    A product must already be in the catalogue — a weight needs a name.
    Re-marking replaces the price and the audit line carries both numbers,
    because last week's bill has to stay explainable after the price moved.
    """
    try:
        sku = _valid_sku(sku_id)
        if sku in RESERVED_SKUS:
            raise WeighedRefused(
                R_RESERVED_SKU,
                f"{sku!r} is a word this counter's own routes use, so a product "
                f"with that id could not be reached. Give it another id.")
        body = await _json_body(request)
        per_kg = _read_price_per_kg(body)
        cat = catalogue()
        if sku not in cat:
            raise WeighedRefused(
                R_UNKNOWN_SKU,
                f"{sku!r} is not something this shop has taught. Teach the "
                f"product first; a weight needs a name to be billed under.")
        rows = load_weighed()
        was = rows.get(sku)
        if was is None and len(rows) >= MAX_WEIGHED:
            raise WeighedRefused(
                R_TOO_MANY,
                f"this counter already has {len(rows)} products sold by weight, "
                f"which is the cap. Unmark one first.")
        now = _now_iso()
        rows[sku] = WeighedSku(sku_id=sku, price_per_kg_paise=per_kg,
                               since=was.since if was else now)
        save_weighed(rows)
        head = _audit("weighed.marked", sku_id=sku,
                      price_per_kg_paise=per_kg,
                      was_price_per_kg_paise=(None if was is None
                                              else int(was.price_per_kg_paise)),
                      minted=False)
        return JSONResponse({
            "ok": True, "settles_money": False,
            "replaced": was is not None,
            "was_price_per_kg_paise": (None if was is None
                                       else int(was.price_per_kg_paise)),
            **_row_view(sku, rows[sku], cat),
            "audited": head is not None,
            "file": str(weighed_path()),
            "rule": RULE,
            "note": ("A packet of this product the camera reads is still "
                     "billed at its catalogue price. The per-kilo price "
                     "applies to a weight entered on the scale."),
        })
    except WeighedRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(WeighedRefused(R_BAD_PRICE, str(exc)))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.delete("/weighed/{sku_id}")
def weighed_unmark_ep(sku_id: str) -> JSONResponse:
    """Stop selling a product by weight. Lines already written stay written."""
    try:
        sku = _valid_sku(sku_id)
        rows = load_weighed()
        was = rows.pop(sku, None)
        if was is None:
            raise WeighedRefused(
                R_NOT_WEIGHED,
                f"{sku!r} is not sold by weight at this counter, so there is "
                f"nothing to unmark.")
        save_weighed(rows)
        head = _audit("weighed.unmarked", sku_id=sku,
                      was_price_per_kg_paise=int(was.price_per_kg_paise),
                      minted=False)
        return JSONResponse({
            "ok": True, "settles_money": False,
            "sku_id": sku, "removed": True,
            "was_price_per_kg_paise": int(was.price_per_kg_paise),
            "audited": head is not None,
            "remaining": len(rows),
        })
    except WeighedRefused as exc:
        return _refusal(exc, status=404 if exc.reason == R_NOT_WEIGHED else 400)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)
