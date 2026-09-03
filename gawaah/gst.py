"""KAR — GST-ready records for a registered kirana.

WHAT THIS IS, AND WHAT IT IS NOT
================================
A registered shop has to know the tax split on every sale: which HSN heading a
product falls under, what rate it carries, and how much of each rupee taken at
the counter was tax. This module records the first two per product, derives
the third from the bills the counter already wrote, and lays a month out in
the shape of GSTR-1's B2C table — taxable value, CGST and SGST, by rate.

It does NOT file anything with the government. It does not generate an
e-invoice or an IRN. It is not tax advice. It produces GST-READY records and a
summary a shopkeeper can hand to whoever files the return, and every response
says so in a field called `is_filing`, which is always false.

Five decisions this file exists to hold, in the order they would hurt if
broken:

  1. IT DOES NOT OWN THE CATALOGUE. `gawaah/shop_store.py` owns `catalog.json`
     and the till owns its sidecars. HSN and rate live in a SIDECAR next to
     them, `gst.json`, and nothing here opens, rewrites or migrates the
     catalogue. Delete the sidecar and every product, price and taught vector
     is still there; only the tax filing is gone.

  2. INTEGER PAISE, AND THE ROUNDING RULE IS WRITTEN DOWN. Prices in a kirana
     are TAX-INCLUSIVE — the number on the shelf is what the customer pays. So
     for a line priced P at rate r:

         taxable = (P * 100) // (100 + r)        integer division, floored
         tax     = P - taxable                    the whole remainder

     The fraction of a paisa that falls between the two goes to TAX, never to
     the shop: the taxable value reported is never more than the exact value,
     and the tax is never less. Then the tax is split in two:

         cgst = tax // 2
         sgst = tax - cgst                        an odd paisa goes to SGST

     Both rules are stated in every response that carries a figure, and both
     are tested at the boundaries — a one-paisa line, a zero line, and every
     price up to fifty rupees at every slab. Nothing here is rounded to the
     nearest anything; `round()` and `/` do not appear in this file and
     `tools/lint_no_float.py` would fail the build if they did.

     Tax is worked out LINE BY LINE and summed. It is not recomputed on a bill
     or a month total, so the line figures always add up to the totals shown.

  3. THE BILLS COME FROM THE CHAIN, THROUGH `gawaah/manage.py`. This module
     keeps no bills table and folds no ledger of its own. It asks manage for
     the same bills the History screen shows — verified from genesis, stopping
     at the first broken link — and puts a rate against each line. A line
     whose product has no rate set is NOT taxed at a guess: it is listed as an
     exception, its money is reported as unrated, and the month says
     `complete: false` until the shopkeeper has set every rate.

  4. THE SUGGESTER IS A KEYWORD TABLE, NOT INFERENCE. `RULES` below maps words
     in a product's name to an HSN heading and, where the author was confident
     of the slab, a rate. It PROPOSES; a person accepts by posting the values
     to `POST /gst/products/{sku}`. The whole table is published by
     `GET /gst/rules` so a shopkeeper can see why a packet was proposed where
     it was, and each proposal names the keyword that matched.

  5. A REFUSAL IS A RESULT. Every failure has a name in `reason` and a
     sentence in `detail`, with a 400 (404 for a product or bill that does not
     exist). Nothing here raises a 500. Nothing here settles money.

THE SLABS
=========
The rates this module records are 0, 5, 12, 18 and 28 per cent. That is a
stated limit: goods taxed outside those — aerated drinks and tobacco were
moved to 40 per cent in September 2025 — cannot be recorded here. They stay
unrated and are listed as exceptions rather than summarised at a rate they
are not taxed at. Widening the set is one line (`SLABS`).

MOUNTING
========
The router carries NO prefix; the paths are absolute::

    GET    /gst/health                 what this is and is not, and the slabs
    GET    /gst/rules                  the keyword table the suggester uses
    GET    /gst/products               every priced product, its HSN and rate,
                                       and a proposal where none is set
    GET    /gst/products/{sku}         one product
    POST   /gst/products/{sku}         set HSN and rate  {"hsn": "3401", "rate": 5}
    DELETE /gst/products/{sku}         clear them
    GET    /gst/bill/{session_id}      the tax split of one closed bill
    GET    /gst/month?month=YYYY-MM    GSTR-1 B2C-shaped summary, with exceptions
    GET    /gst/month.csv?month=…      the same, as a file

    from gawaah import gst
    app.include_router(gst.router)
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .ledger import Ledger
from .money import MoneyError, paise, to_rupees_str

router = APIRouter()


# --------------------------------------------------------------- refusals --
#
# Every one of these is a state this module can actually reach. The reason
# names the state; the sentence that says what to change lives in `detail`.

R_NO_TILL = "till_module_unavailable"
R_NO_CATALOGUE = "catalogue_unavailable"
R_NO_BILL_BOOK = "bill_machinery_unavailable"
R_BAD_BODY = "gst_body_not_json"
R_UNKNOWN_SKU = "sku_not_in_this_shop"
R_HSN_MISSING = "hsn_missing"
R_BAD_HSN = "hsn_not_4_6_or_8_digits"
R_RATE_MISSING = "rate_missing"
R_BAD_RATE = "rate_not_a_whole_number"
R_NOT_A_SLAB = "rate_not_a_gst_slab"
R_NOT_SET = "no_rate_set_for_this_product"
R_UNKNOWN_SESSION = "session_not_in_the_ledger"
R_BILL_NOT_CLOSED = "bill_not_closed"
R_BAD_MONTH = "month_malformed"
R_BAD_BASIS = "basis_not_closed_or_settled"
R_BAD_PRICE = "price_not_integer_paise"
R_UNWRITABLE = "gst_file_unwritable"
R_INTERNAL = "gst_internal_error"


# ------------------------------------------------------------------ facts --

#: The rates this module will record. See THE SLABS in the module docstring
#: for what it costs when a product is taxed outside them.
SLABS: tuple[int, ...] = (0, 5, 12, 18, 28)

#: An HSN heading is 4 digits; 6 and 8 digit codes are the same heading with
#: sub-headings. Leading zeros are significant — 0401 is milk and 401 is not
#: a code — which is why an HSN is text and never a number.
HSN_RE = re.compile(r"^[0-9]{4}(?:[0-9]{2}){0,2}$")

GST_FILENAME = "gst.json"
GST_AUDIT_FILENAME = "gst.audit.jsonl"
GST_FORMAT = 1

BASIS_CLOSED = "closed"
BASIS_SETTLED = "settled"
BASES = (BASIS_CLOSED, BASIS_SETTLED)

#: How a rate came to be set: typed by the shopkeeper, or a proposal from the
#: keyword table that he accepted. Recorded so a later reader can tell which
#: rows a person thought about and which he waved through.
SOURCE_TYPED = "typed"
SOURCE_ACCEPTED = "accepted_suggestion"
SOURCES = (SOURCE_TYPED, SOURCE_ACCEPTED)

#: The largest single line this module will put a rate against. A line past
#: this is not a kirana sale, it is a data error, and it is refused by name
#: rather than taxed. Ten lakh rupees.
MAX_LINE_PAISE = 100_000_000

NOT_FILING = (
    "This produces GST-ready records and a summary in the shape of GSTR-1's "
    "B2C table. It does not file anything with the government, does not "
    "generate an e-invoice or an IRN, and is not tax advice. Take the summary "
    "to whoever files the return."
)

ROUNDING = {
    "prices_are": "tax-inclusive: the shelf price is what the customer pays",
    "taxable_value": (
        "taxable = (price * 100) // (100 + rate), integer division, floored. "
        "The fraction of a paisa between taxable value and tax goes to tax, "
        "never to the shop: the taxable value reported is never more than the "
        "exact value and the tax is never less."),
    "tax": "tax = price - taxable, so the two always add back to the price",
    "split": (
        "cgst = tax // 2 and sgst = tax - cgst, so an odd paisa of tax goes "
        "to SGST"),
    "per_line": (
        "worked out line by line and summed; never recomputed on a bill or "
        "month total, so the lines always add up to the totals shown"),
    "never": "nothing is rounded to the nearest paisa or rupee; there is no "
             "float and no division anywhere in this arithmetic",
}

SLAB_LIMIT = (
    "The slabs this screen records are 0, 5, 12, 18 and 28 per cent. A "
    "product taxed outside those — aerated drinks and tobacco were moved to "
    "40 per cent in September 2025 — cannot be recorded here; it stays "
    "unrated and is listed as an exception rather than summarised at a rate "
    "it is not taxed at."
)

STOREFRONT_NOTE = (
    "Only bills closed at this counter are in these figures. Orders placed "
    "from the storefront QR are on a separate chain and are not included."
)


class GstRefused(Exception):
    """A named refusal with a reason a human can act on."""

    def __init__(self, reason: str, detail: str, status: int = 400) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.status = status


def _refusal(exc: GstRefused) -> JSONResponse:
    """The shape every other endpoint in this program answers a refusal with."""
    return JSONResponse(
        {"ok": False, "reason": exc.reason, "detail": exc.detail,
         "settles_money": False, "is_filing": False},
        status_code=exc.status,
    )


def _crash(exc: BaseException) -> JSONResponse:
    """The last resort. A 500 teaches the reader nothing, so there are none."""
    return JSONResponse(
        {"ok": False, "reason": R_INTERNAL,
         "detail": f"{type(exc).__name__}: {exc}",
         "settles_money": False, "is_filing": False},
        status_code=400,
    )


# ---------------------------------------------------------- the arithmetic --


def split_inclusive(price_paise: int, rate: int) -> dict[str, int]:
    """The tax inside one tax-inclusive price, in integer paise.

    This is the whole of the arithmetic in this module and it is deliberately
    one small function so the rounding rule can be read in one place and tested
    at its edges. `paise()` refuses a float and a bool before anything is
    divided, so a catalogue holding 21.45 instead of 2145 stops here rather
    than becoming an approximate tax figure.
    """
    try:
        p = int(paise(price_paise))
    except MoneyError as exc:
        raise GstRefused(R_BAD_PRICE, f"{exc}. Nothing was worked out.") from None
    if p < 0:
        raise GstRefused(
            R_BAD_PRICE,
            f"{p} paise is negative. A sale is not a negative number and a "
            f"credit note is not something this screen produces.")
    if p > MAX_LINE_PAISE:
        raise GstRefused(
            R_BAD_PRICE,
            f"{p} paise on one line is past {MAX_LINE_PAISE}, which is not a "
            f"kirana sale. Nothing was worked out.")
    if isinstance(rate, bool) or not isinstance(rate, int) or rate not in SLABS:
        raise GstRefused(
            R_NOT_A_SLAB,
            f"{rate!r} is not one of the slabs this screen records "
            f"({', '.join(str(s) for s in SLABS)}).")
    taxable = (p * 100) // (100 + rate)
    tax = p - taxable
    cgst = tax // 2
    sgst = tax - cgst
    return {"price_paise": p, "taxable_paise": taxable, "tax_paise": tax,
            "cgst_paise": cgst, "sgst_paise": sgst}


def _rupee_fields(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Add a `<key>_rupees` beside each `<key>_paise` that is an int."""
    out = dict(row)
    for k in keys:
        v = row.get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            out[k[:-len("_paise")] + "_rupees"] = to_rupees_str(paise(v))
        else:
            out[k[:-len("_paise")] + "_rupees"] = None
    return out


MONEY_KEYS = ("price_paise", "taxable_paise", "tax_paise", "cgst_paise",
              "sgst_paise")
TOTAL_KEYS = ("gross_paise", "taxable_paise", "tax_paise", "cgst_paise",
              "sgst_paise")


def _zero_totals() -> dict[str, int]:
    return {"lines": 0, "gross_paise": 0, "taxable_paise": 0, "tax_paise": 0,
            "cgst_paise": 0, "sgst_paise": 0}


def _add_split(acc: dict[str, int], split: dict[str, int]) -> None:
    """Integer addition of one line into a running total. Nothing else."""
    acc["lines"] += 1
    acc["gross_paise"] += int(split["price_paise"])
    acc["taxable_paise"] += int(split["taxable_paise"])
    acc["tax_paise"] += int(split["tax_paise"])
    acc["cgst_paise"] += int(split["cgst_paise"])
    acc["sgst_paise"] += int(split["sgst_paise"])


# ------------------------------------------------------------- the till --
#
# Imported LATE, inside functions, and found in sys.modules FIRST — the same
# rule and the same reason as gawaah/storefront.py. `make serve` runs
# `uvicorn upload_app:app --app-dir tools`, which registers the module as
# `upload_app`; the test suite does `from tools import upload_app` and
# registers it as `tools.upload_app`. Importing the other spelling loads a
# SECOND copy of the file with its own catalogue handle, and a sidecar written
# next to a different shop than the one the till is serving.

from gawaah import till_ref as _till_ref

#: One definition, in `gawaah/till_ref.py`. This was sixteen copies of a tuple
#: that was missing `__main__`, and every one of them was wrong the same way.
_TILL_NAMES = _till_ref.TILL_NAMES


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _till() -> Any:
    """The already-loaded till module, or a named refusal."""
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
        raise GstRefused(
            R_NO_TILL,
            f"tools/upload_app.py is not importable ({type(exc).__name__}: "
            f"{exc}). Rates are filed against the shopkeeper's catalogue and "
            f"this module will not keep a second copy of it.") from None
    return upload_app


def _manage() -> Any:
    """The bill machinery, or a named refusal. Never re-derived here."""
    try:
        from . import manage  # noqa: WPS433 - late: manage loads the vision stack
    except Exception as exc:  # noqa: BLE001 - a missing bill book is a named answer
        raise GstRefused(
            R_NO_BILL_BOOK,
            f"gawaah/manage.py is not importable ({type(exc).__name__}: "
            f"{exc}). Bills are read through it and nothing here folds the "
            f"chain a second way.") from None
    return manage


def shop_dir() -> Path:
    """The shopkeeper's catalogue directory — the till's answer, not a second.

    `upload_app.store_dir()` honours GAWAAH_SHOP_DIR and `set_store_dir()` can
    move it without touching the environment. Deriving the path here would be
    a second answer to one question, and the day the catalogue moves and the
    rates stay behind is the day a summary describes a different shop.
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
    return _repo_root() / "results" / "shop"


def gst_path() -> Path:
    """The sidecar. NEXT TO `catalog.json`, and never `catalog.json` itself."""
    return shop_dir() / GST_FILENAME


def audit_path() -> Path:
    """This module's own hash-chained log.

    DELIBERATELY NOT `results/audit.jsonl`. The money service holds that file
    open in another process and keeps the chain head in memory; a second
    writer appending between two of its writes gives it a stale head and
    every line it writes afterwards fails `gawaah.ledger.verify`. Setting a
    rate is worth recording — it changes what a month's summary says — but
    not at the price of the money ledger. So it gets its own chain, in the
    shop directory, verifiable by exactly the same `verify()`.
    """
    return shop_dir() / GST_AUDIT_FILENAME


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _audit(event: str, **fields: Any) -> Optional[str]:
    """Append one auditable line. Returns the new head, or None if it failed.

    Best effort but never silent: the response says `audited: false` rather
    than reporting a witnessed change that was not.
    """
    try:
        return Ledger(audit_path()).append(
            ts=_now_iso(), module="gst", event=event, **fields)
    except Exception:  # noqa: BLE001 - a failed audit must not lose the rate
        return None


# ------------------------------------------------------------- catalogue --


def catalogue() -> dict[str, dict[str, Any]]:
    """{sku_id -> name, price_paise, how} for everything that has a price.

    `priced_skus()` and not the offer-aware one: a rate is set against the
    marked price the shopkeeper put on the shelf, and the split shown beside a
    product is at that price. A bill's lines carry the price actually charged,
    offer and all, and the tax on a bill is worked out from that.
    """
    up = _till()
    try:
        return dict(up.priced_skus())
    except GstRefused:
        raise
    except Exception as exc:  # noqa: BLE001 - the store may be unreadable
        reason = getattr(exc, "reason", None) or R_NO_CATALOGUE
        detail = getattr(exc, "detail", None) or (
            f"the catalogue could not be read ({type(exc).__name__}: {exc})")
        raise GstRefused(reason, detail) from None


# ------------------------------------------------------------ the sidecar --


def _load_json(path: Path) -> tuple[Any, Optional[str]]:
    """Parse a sidecar, or name why not. Never raises."""
    if not path.exists():
        return None, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as exc:  # noqa: BLE001 - a hand-edit is not an outage
        return None, f"{type(exc).__name__}: {exc}"


def read_rates() -> tuple[dict[str, dict[str, Any]], list[str]]:
    """{sku -> hsn, rate, set_at, source}, plus the rows it could not read.

    A row that does not validate is SKIPPED AND NAMED, never repaired. A rate
    of 18.0 or "18" on disk is a hand-edit; taxing a month at a rate somebody
    typed wrong, silently coerced, is the one thing this screen must not do.
    """
    raw, err = _load_json(gst_path())
    problems: list[str] = []
    if err:
        problems.append(f"{GST_FILENAME}: {err}")
    if raw is None or not isinstance(raw, dict):
        return {}, problems
    if raw.get("format") != GST_FORMAT or not isinstance(raw.get("skus"), dict):
        problems.append(
            f"{GST_FILENAME} is format {raw.get('format')!r}, not {GST_FORMAT}; "
            f"its rows were not used")
        return {}, problems
    out: dict[str, dict[str, Any]] = {}
    for sku, rec in raw["skus"].items():
        if not isinstance(rec, dict):
            problems.append(f"{sku}: row is not an object")
            continue
        hsn = rec.get("hsn")
        rate = rec.get("rate")
        if not isinstance(hsn, str) or not HSN_RE.match(hsn):
            problems.append(f"{sku}: hsn {hsn!r} is not 4, 6 or 8 digits")
            continue
        if isinstance(rate, bool) or not isinstance(rate, int) or rate not in SLABS:
            problems.append(f"{sku}: rate {rate!r} is not one of the slabs")
            continue
        source = rec.get("source")
        out[str(sku)] = {
            "hsn": hsn,
            "rate": rate,
            "set_at": rec.get("set_at") if isinstance(rec.get("set_at"), str) else None,
            "source": source if source in SOURCES else None,
        }
    return out, problems


def write_rates(rates: dict[str, dict[str, Any]]) -> None:
    """Atomic replace. A half-written file read by the next request would look
    like every rate had been cleared."""
    path = gst_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": GST_FORMAT,
        "note": (
            "HSN and GST rate per product, set by the shopkeeper. A sidecar "
            "beside the catalogue; the catalogue is never rewritten by the "
            "screen that writes this file. Rates are whole per-cent slabs; "
            "HSN is text because leading zeros are part of the code."),
        "skus": {sku: dict(rec) for sku, rec in sorted(rates.items())},
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, indent=1) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------- the suggester --
#
# A FIXED, ORDERED TABLE. It reads product NAMES and nothing else: no photo,
# no embedding, no model, and it learns nothing from what is accepted. The
# first rule whose keyword appears as a whole word in the name wins, so the
# order matters and is deliberate — "hair oil" is listed before any edible
# oil, "dairy milk" before milk, "milk powder" before milk, namkeen before
# pulses so "moong dal namkeen" is a snack and not a staple.
#
# HSN headings are stable for years. The RATE against each is what the author
# of this table believed applied from 22 September 2025, when most goods moved
# to 5 or 18 per cent; where the author was not confident the rule proposes
# the HSN and NO rate, and says so. Every entry is a proposal a person accepts.

RULES: tuple[tuple[str, str, Optional[int], tuple[str, ...]], ...] = (
    # --- things whose name would otherwise match a later, broader rule ---
    ("chocolate", "1806", 5,
     ("chocolate", "dairy milk", "kitkat", "kit kat", "cadbury", "5 star",
      "munch", "perk", "gems")),
    ("milk powder / condensed milk", "0402", None,
     ("milk powder", "condensed milk", "milkmaid", "dairy whitener")),
    ("ice cream", "2105", None, ("ice cream", "icecream", "kulfi")),
    ("biscuits / cookies", "1905", None,
     ("biscuit", "biscuits", "cookie", "cookies", "parle", "marie", "monaco",
      "krackjack", "hide and seek", "good day", "bourbon", "oreo",
      "britannia", "sunfeast", "cracker")),
    ("shampoo / hair oil", "3305", 5,
     ("shampoo", "hair oil", "parachute", "clinic plus", "sunsilk",
      "head and shoulders", "pantene")),
    ("rice bran oil", "1515", 5, ("rice bran",)),
    ("namkeen / bhujia / mixture", "2106", 5,
     ("namkeen", "bhujia", "mixture", "sev", "chivda", "haldiram",
      "haldirams")),
    ("instant coffee", "2101", 5, ("nescafe", "bru", "instant coffee")),
    # --- personal and household ---
    ("toilet soap", "3401", 5,
     ("soap", "sabun", "lifebuoy", "lux", "santoor", "cinthol", "dove",
      "pears", "medimix")),
    ("detergent / dishwash", "3402", 18,
     ("detergent", "surf", "rin", "nirma", "ariel", "tide", "washing powder",
      "wheel", "vim", "dishwash", "dish wash", "ghadi")),
    ("toothpaste", "3306", 5,
     ("toothpaste", "colgate", "pepsodent", "closeup", "close up",
      "sensodyne", "dabur red")),
    ("toothbrush", "9603", 5, ("toothbrush", "tooth brush")),
    ("talcum powder", "3304", 5, ("talc", "talcum", "ponds")),
    ("creams and lotions", "3304", None,
     ("cream", "nivea", "vaseline", "boroline", "boroplus", "lotion",
      "moisturiser", "moisturizer", "glow and lovely", "fair and lovely")),
    ("shaving cream / after shave", "3307", 5,
     ("shaving", "after shave", "aftershave")),
    ("razors and blades", "8212", None,
     ("razor", "blade", "blades", "gillette")),
    ("sanitary napkins", "9619", 0,
     ("sanitary", "whisper", "stayfree", "sofy")),
    ("diapers", "9619", 5,
     ("diaper", "diapers", "pampers", "huggies", "mamypoko")),
    ("agarbatti / dhoop", "3307", 5, ("agarbatti", "incense", "dhoop")),
    ("dry cell batteries", "8506", 18,
     ("battery", "batteries", "eveready", "duracell")),
    ("candles", "3406", None, ("candle", "candles", "mombatti")),
    ("matches", "3605", None, ("matchbox", "matches", "match box")),
    ("phenyl / toilet and floor cleaners", "3808", None,
     ("phenyl", "phenyle", "harpic", "lizol", "domex", "toilet cleaner",
      "floor cleaner", "disinfectant")),
    ("mosquito repellent", "3808", None,
     ("mosquito", "repellent", "good knight", "goodknight", "all out",
      "mortein", "odomos")),
    ("led bulbs", "8539", None, ("bulb", "bulbs", "led bulb", "cfl")),
    # --- dairy and eggs ---
    ("milk", "0401", 0, ("milk", "doodh", "dudh")),
    ("curd / lassi / buttermilk", "0403", None,
     ("curd", "dahi", "lassi", "chaas", "buttermilk", "yogurt", "yoghurt")),
    ("butter / ghee", "0405", 5, ("butter", "makhan", "ghee")),
    ("paneer", "0406", 0, ("paneer",)),
    ("cheese", "0406", 5, ("cheese",)),
    ("eggs", "0407", 0, ("egg", "eggs", "anda", "ande")),
    ("honey", "0409", 5, ("honey", "shahad")),
    # --- tea, coffee, spices ---
    ("tea", "0902", 5,
     ("tea", "chai", "chaipatti", "chai patti", "red label", "taj mahal",
      "tata tea", "wagh bakri")),
    ("coffee", "0901", 5, ("coffee",)),
    ("chilli / pepper", "0904", 5,
     ("mirch", "chilli", "chili", "pepper", "kali mirch")),
    ("cinnamon", "0906", 5, ("dalchini", "cinnamon")),
    ("cloves", "0907", 5, ("laung", "clove", "cloves")),
    ("cardamom / nutmeg", "0908", 5,
     ("elaichi", "cardamom", "jaiphal", "nutmeg")),
    ("cumin / coriander / fennel / ajwain seeds", "0909", 5,
     ("jeera", "cumin", "dhania", "coriander", "saunf", "fennel", "ajwain")),
    ("turmeric, ginger and mixed spices", "0910", 5,
     ("haldi", "turmeric", "masala", "garam masala", "adrak", "ginger",
      "methi", "hing", "kalonji", "everest", "mdh")),
    # --- edible oils, before rice so "rice bran" and "oil" names do not
    #     land on the grain ---
    ("sunflower / refined oil", "1512", 5,
     ("sunflower oil", "sunflower", "refined oil")),
    ("mustard oil", "1514", 5,
     ("mustard oil", "sarson", "sarson ka tel", "kachi ghani")),
    ("groundnut oil", "1508", 5, ("groundnut oil", "peanut oil")),
    ("soyabean oil", "1507", 5, ("soyabean oil", "soya oil", "soybean oil")),
    ("coconut oil", "1513", 5, ("coconut oil", "nariyal tel")),
    ("vanaspati", "1516", 5, ("vanaspati", "dalda")),
    # --- staples ---
    ("cornflakes / oats / puffed rice", "1904", 5,
     ("cornflakes", "corn flakes", "oats", "muesli", "murmura", "kurmura",
      "puffed rice", "chocos")),
    ("rice", "1006", 5,
     ("rice", "chawal", "basmati", "sona masoori", "kolam")),
    ("wheat flour / maida", "1101", 5,
     ("atta", "aata", "wheat flour", "maida", "chakki")),
    ("suji / rava / dalia", "1103", 5,
     ("suji", "sooji", "rava", "dalia", "daliya")),
    ("poha", "1104", 5, ("poha", "chura")),
    ("besan", "1106", 5, ("besan", "gram flour")),
    ("pulses / dal", "0713", 5,
     ("dal", "daal", "dahl", "toor", "arhar", "tur", "moong", "masoor",
      "chana", "rajma", "urad", "kabuli", "lobia", "matar")),
    ("sugar", "1701", 5, ("sugar", "cheeni", "shakkar")),
    ("jaggery", "1701", None, ("jaggery", "gur", "gud")),
    ("salt", "2501", 0, ("salt", "namak")),
    ("sabudana", "1903", 5, ("sabudana", "sago")),
    # --- packaged food ---
    ("noodles / pasta / vermicelli", "1902", 5,
     ("maggi", "noodles", "noodle", "pasta", "macaroni", "vermicelli",
      "sevai", "seviyan", "yippee")),
    ("bread", "1905", 0, ("bread", "pav", "double roti")),
    ("papad", "1905", 0, ("papad", "papadum")),
    ("rusk", "1905", 5, ("rusk", "toast")),
    ("toffee / candy / chewing gum", "1704", 5,
     ("toffee", "candy", "lollipop", "chewing gum", "eclairs", "kismi",
      "melody", "mentos", "polo")),
    ("potato chips", "2005", None,
     ("chips", "lays", "uncle chipps", "wafers", "bingo")),
    ("pickle", "2001", 5, ("pickle", "achar", "achaar")),
    ("jam", "2007", 5, ("jam", "marmalade")),
    ("ketchup / sauce / chutney", "2103", 5,
     ("ketchup", "sauce", "chutney", "mayonnaise", "mayo")),
    ("dry fruits: cashew", "0801", 5, ("cashew", "kaju")),
    ("dry fruits: almond / walnut / pistachio", "0802", 5,
     ("almond", "badam", "walnut", "akhrot", "pista", "pistachio")),
    ("raisins", "0806", 5, ("raisin", "raisins", "kishmish")),
    ("dates", "0804", 5, ("dates", "khajur")),
    # --- drinks. The rate on aerated drinks is outside the slabs this screen
    #     records (see SLAB_LIMIT), so the HSN is proposed and the rate is
    #     deliberately left for the person. ---
    ("aerated drinks", "2202", None,
     ("pepsi", "coke", "coca cola", "cola", "thums up", "thumbs up",
      "sprite", "fanta", "limca", "mirinda", "7up", "mountain dew", "soda",
      "aerated")),
    ("fruit drinks", "2202", None,
     ("maaza", "frooti", "slice", "appy")),
    ("fruit juice", "2009", None, ("juice", "tropicana")),
    ("packaged drinking water", "2201", None,
     ("water", "bisleri", "kinley", "aquafina", "mineral water")),
    ("malt drinks", "1901", None, ("horlicks", "boost", "complan")),
    # --- stationery ---
    ("notebooks / exercise books", "4820", 0,
     ("notebook", "notebooks", "exercise book", "register", "copy")),
    ("pencils / erasers / sharpeners", "9609", 0,
     ("pencil", "pencils", "eraser", "sharpener", "crayon", "crayons")),
    ("pens", "9608", None, ("pen", "pens", "ball pen", "gel pen", "reynolds")),
)

SCHEDULE_NOTE = (
    "HSN headings are stable for years. The rate beside each is what this "
    "table's author believed applied from 22 September 2025, when most goods "
    "moved to 5 or 18 per cent; where the author was not confident the table "
    "proposes the HSN and no rate. Every row is a proposal. The rate a "
    "product is actually taxed at is the shopkeeper's decision against the "
    "schedule in force, and this screen records what he chose."
)


def _words(name: str) -> str:
    """The name as a space-padded lowercase word string, for whole-word matching.

    Whole words, not substrings, and the padding is what makes the first and
    last word matchable. Without it "salt" matches "Salted Chips" and "pen"
    matches "Pepsodent".
    """
    return " " + re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip() + " "


def suggest_for_name(name: str) -> Optional[dict[str, Any]]:
    """{label, hsn, rate, keyword} for the first rule that matches, or None.

    Pure and stable: the same name always gets the same proposal, and nothing
    on disk is read or written.
    """
    padded = _words(name)
    for label, hsn, rate, keywords in RULES:
        for word in keywords:
            if f" {word} " in padded:
                return {"label": label, "hsn": hsn, "rate": rate,
                        "keyword": word,
                        "why": (f"'{word}' is in the name; the table files "
                                f"that under {label} (HSN {hsn})"
                                + (f" at {rate} per cent"
                                   if rate is not None else
                                   ". No rate is proposed for it; check the "
                                   "schedule."))}
    return None


# --------------------------------------------------------- product rows --


def _product_row(sku_id: str, rec: dict[str, Any],
                 rate_rec: Optional[dict[str, Any]]) -> dict[str, Any]:
    """One product as the screen shows it: the price, the rate, the split."""
    name = str(rec.get("name") or sku_id)
    price = rec.get("price_paise")
    row: dict[str, Any] = {
        "sku_id": sku_id,
        "name": name,
        "price_paise": price if isinstance(price, int) and not isinstance(price, bool) else None,
        "price_rupees": None,
        "taught_with": str(rec.get("how") or "unknown"),
        "set": rate_rec is not None,
        "hsn": None, "rate": None, "set_at": None, "source": None,
        "at_marked_price": None,
        "suggestion": None if rate_rec is not None else suggest_for_name(name),
    }
    if row["price_paise"] is not None:
        row["price_rupees"] = to_rupees_str(paise(row["price_paise"]))
    if rate_rec is not None:
        row.update({"hsn": rate_rec["hsn"], "rate": rate_rec["rate"],
                    "set_at": rate_rec.get("set_at"),
                    "source": rate_rec.get("source")})
        if row["price_paise"] is not None:
            try:
                row["at_marked_price"] = _rupee_fields(
                    split_inclusive(row["price_paise"], rate_rec["rate"]),
                    *MONEY_KEYS)
            except GstRefused as exc:
                # A price the arithmetic refuses is shown as a refusal beside
                # the row, not hidden and not approximated.
                row["at_marked_price"] = {"refused": exc.reason,
                                          "detail": exc.detail}
    return row


def _all_rows() -> dict[str, Any]:
    known = catalogue()
    rates, problems = read_rates()
    rows = [_product_row(sku, known[sku], rates.get(sku)) for sku in sorted(known)]
    gone = sorted(sku for sku in rates if sku not in known)
    return {
        "items": rows,
        "count": len(rows),
        "set_count": sum(1 for r in rows if r["set"]),
        "unset_count": sum(1 for r in rows if not r["set"]),
        "proposed_count": sum(1 for r in rows if r["suggestion"] is not None),
        "set_but_not_in_catalogue": [
            {"sku_id": sku, **rates[sku]} for sku in gone],
        "problems": problems,
    }


# ----------------------------------------------------------- the bills --


def _parse_ts(value: Any) -> Optional[_dt.datetime]:
    """An ISO-8601 stamp as the ledger writes them, or None. Naive is UTC."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def _local_tz() -> _dt.tzinfo:
    tz = _dt.datetime.now().astimezone().tzinfo
    return tz if tz is not None else _dt.timezone.utc


def month_bounds(month: Optional[str]) -> tuple[_dt.datetime, _dt.datetime, str]:
    """First instant of the month to the first instant of the next, in the
    COUNTER'S OWN timezone, and the `YYYY-MM` label.

    The chain stamps UTC. A shop's month does not end at 05:30 on the last
    evening — asking for August and being answered with a UTC window would
    move the last evening's sales into September. Same rule as `/manage/today`.
    """
    tz = _local_tz()
    if month:
        m = re.fullmatch(r"(\d{4})-(\d{2})", month.strip())
        if not m or not 1 <= int(m.group(2)) <= 12:
            raise GstRefused(
                R_BAD_MONTH,
                f"{month!r} is not a month. Write it as YYYY-MM, for example "
                f"2026-08.")
        year, mon = int(m.group(1)), int(m.group(2))
    else:
        now = _dt.datetime.now(tz)
        year, mon = now.year, now.month
    start = _dt.datetime(year, mon, 1, tzinfo=tz)
    end = (_dt.datetime(year + 1, 1, 1, tzinfo=tz) if mon == 12
           else _dt.datetime(year, mon + 1, 1, tzinfo=tz))
    return start, end, f"{year:04d}-{mon:02d}"


def _basis(raw: Optional[str]) -> str:
    if raw is None or raw == "":
        return BASIS_CLOSED
    b = raw.strip().lower()
    if b not in BASES:
        raise GstRefused(
            R_BAD_BASIS,
            f"basis={raw!r} is not something this screen knows. "
            f"'{BASIS_CLOSED}' counts every bill the counter closed; "
            f"'{BASIS_SETTLED}' counts only those a signature-verified webhook "
            f"turned PAID.")
    return b


def _bills() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Every bill the History screen would show, and the chain that carried
    them — through manage, never a second fold."""
    mg = _manage()
    records, chain = mg.read_chain()
    return mg.bills_from(records), chain


def _tax_bill(bill: dict[str, Any], rates: dict[str, dict[str, Any]],
              names: dict[str, str]) -> dict[str, Any]:
    """Put a rate against every priced line of one bill.

    Three kinds of line come out, and none is folded into another:

      lines      priced, and the product has a rate — taxed
      unrated    priced, and the product has NO rate — money reported, no tax
      excluded   amber: the counter refused to price it — no money at all

    A line whose stored price is not integer paise is counted in
    `unreadable_lines` and taxed at nothing; the chain wrote something that is
    not money and this screen will not launder it into a figure.
    """
    lines: list[dict[str, Any]] = []
    unrated: list[dict[str, Any]] = []
    by_rate: dict[int, dict[str, int]] = {}
    rated_total = _zero_totals()
    unrated_paise = 0
    unreadable = 0
    for item in bill["line_items"]:
        price = item.get("price_paise")
        if isinstance(price, bool) or not isinstance(price, int):
            unreadable += 1
            continue
        sku = item["sku_id"]
        base = {"item_id": item["item_id"], "sku_id": sku,
                "name": names.get(sku), "at": item.get("at"),
                "price_paise": price,
                "price_rupees": to_rupees_str(paise(price))}
        rate_rec = rates.get(sku)
        if rate_rec is None:
            unrated.append(base)
            unrated_paise += price
            continue
        split = split_inclusive(price, rate_rec["rate"])
        row = {**base, **_rupee_fields(split, *MONEY_KEYS),
               "hsn": rate_rec["hsn"], "rate": rate_rec["rate"]}
        lines.append(row)
        acc = by_rate.setdefault(rate_rec["rate"], _zero_totals())
        _add_split(acc, split)
        _add_split(rated_total, split)
    return {
        "lines": lines,
        "unrated": unrated,
        "unrated_paise": unrated_paise,
        "excluded": [{"item_id": e["item_id"], "sku_id": e["sku_id"],
                      "name": names.get(e["sku_id"]), "reason": e["reason"]}
                     for e in bill["excluded"]],
        "unreadable_lines": unreadable,
        "by_rate": by_rate,
        "rated": rated_total,
    }


def _rate_rows(by_rate: dict[int, dict[str, int]],
               bills_by_rate: Optional[dict[int, int]] = None) -> list[dict[str, Any]]:
    """The per-rate table, one row per slab that had a line, ascending."""
    rows = []
    for rate in sorted(by_rate):
        row = {"rate": rate, **_rupee_fields(by_rate[rate], *TOTAL_KEYS)}
        if bills_by_rate is not None:
            row["bills"] = bills_by_rate.get(rate, 0)
        rows.append(row)
    return rows


# ------------------------------------------------------------- the body --


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a named refusal
        raise GstRefused(
            R_BAD_BODY, 'the body must be JSON, like {"hsn": "3401", "rate": 5}.'
        ) from None
    if not isinstance(body, dict):
        raise GstRefused(
            R_BAD_BODY,
            f"the body must be a JSON object, not a {type(body).__name__}.")
    return body


def _require_hsn(body: dict[str, Any]) -> str:
    if "hsn" not in body or body["hsn"] is None:
        raise GstRefused(
            R_HSN_MISSING,
            'no "hsn" in the body. An HSN heading is 4 digits — soap is 3401, '
            'biscuits are 1905 — and the suggester on this screen proposes one.')
    hsn = body["hsn"]
    if isinstance(hsn, bool) or isinstance(hsn, int):
        raise GstRefused(
            R_BAD_HSN,
            f"hsn={hsn!r} was sent as a number. Send it as text: a leading "
            f"zero is part of the code (milk is 0401) and a number loses it.")
    if not isinstance(hsn, str):
        raise GstRefused(R_BAD_HSN, f"hsn must be text, not {type(hsn).__name__}.")
    hsn = hsn.strip().replace(" ", "")
    if not HSN_RE.match(hsn):
        raise GstRefused(
            R_BAD_HSN,
            f"{hsn!r} is not an HSN code. They are 4, 6 or 8 digits and "
            f"nothing else. Nothing was saved.")
    return hsn


def _require_rate(body: dict[str, Any]) -> int:
    if "rate" not in body or body["rate"] is None:
        raise GstRefused(
            R_RATE_MISSING,
            f'no "rate" in the body. Send one of {", ".join(str(s) for s in SLABS)}.')
    rate = body["rate"]
    if isinstance(rate, bool) or not isinstance(rate, int):
        # A rate typed as "5" or 5.0 is refused, not coerced: the one thing a
        # tax record must not do is quietly reinterpret what was typed.
        raise GstRefused(
            R_BAD_RATE,
            f"rate={rate!r} is not a whole number. Send 0, 5, 12, 18 or 28 as "
            f"a number, not text and not a decimal.")
    if rate not in SLABS:
        raise GstRefused(
            R_NOT_A_SLAB,
            f"{rate} per cent is not one of the slabs this screen records "
            f"({', '.join(str(s) for s in SLABS)}). {SLAB_LIMIT}")
    return rate


def _source(body: dict[str, Any]) -> str:
    return SOURCE_ACCEPTED if body.get("accepted_suggestion") is True else SOURCE_TYPED


# ----------------------------------------------------------------- routes --


@router.get("/gst/health")
def gst_health_ep() -> JSONResponse:
    """What this screen is, what it is not, and where its file lives."""
    try:
        rates, problems = read_rates()
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "is_filing": False,
            "produces": "GST-ready records and a GSTR-1 B2C-shaped month summary",
            "does_not": ["file a return", "generate an e-invoice or IRN",
                         "give tax advice"],
            "note": NOT_FILING,
            "slabs": list(SLABS),
            "slab_limit": SLAB_LIMIT,
            "rounding": ROUNDING,
            "prices_are_tax_inclusive": True,
            "sidecar": str(gst_path()),
            "owns_catalog_json": False,
            "audit": str(audit_path()),
            "rates_set": len(rates),
            "problems": problems,
            "suggester": "a fixed keyword table over product names; see /gst/rules",
        })
    except GstRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/gst/rules")
def gst_rules_ep() -> JSONResponse:
    """The whole keyword table, so a proposal can be checked against its cause."""
    try:
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "is_filing": False,
            "rules": [{"label": label, "hsn": hsn, "rate": rate,
                       "keywords": list(keywords)}
                      for label, hsn, rate, keywords in RULES],
            "count": len(RULES),
            "matching": ("whole words of the product name, lowercase, first "
                         "rule in this order wins"),
            "schedule_note": SCHEDULE_NOTE,
            "slabs": list(SLABS),
        })
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/gst/products")
def gst_products_ep() -> JSONResponse:
    """Every priced product with its HSN and rate, and a proposal where none
    is set. The proposal is a proposal: nothing is written by reading this."""
    try:
        payload = _all_rows()
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "is_filing": False,
            **payload,
            "slabs": list(SLABS),
            "rounding": ROUNDING,
            "note": NOT_FILING,
            "schedule_note": SCHEDULE_NOTE,
        })
    except GstRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/gst/products/{sku_id}")
def gst_product_ep(sku_id: str) -> JSONResponse:
    try:
        known = catalogue()
        rec = known.get(sku_id)
        if rec is None:
            raise GstRefused(
                R_UNKNOWN_SKU,
                f"{sku_id!r} is not a priced product in this shop. Teach it "
                f"and price it first; a rate against nothing taxes nothing.",
                status=404)
        rates, problems = read_rates()
        return JSONResponse({
            "ok": True, "settles_money": False, "is_filing": False,
            "product": _product_row(sku_id, rec, rates.get(sku_id)),
            "problems": problems,
            "slabs": list(SLABS),
        })
    except GstRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/gst/products/{sku_id}")
async def gst_set_ep(sku_id: str, request: Request) -> JSONResponse:
    """Set the HSN and rate for one product. A person's decision, recorded.

    Body: {"hsn": "3401", "rate": 5, "accepted_suggestion": false}. Both
    fields are required together — a rate with no heading, or a heading with
    no rate, is half a record and a half record is refused rather than kept.
    Setting the same values again is not an error and writes nothing.
    """
    try:
        body = await _json_body(request)
        hsn = _require_hsn(body)
        rate = _require_rate(body)
        source = _source(body)

        known = catalogue()
        rec = known.get(sku_id)
        if rec is None:
            raise GstRefused(
                R_UNKNOWN_SKU,
                f"{sku_id!r} is not a priced product in this shop. Nothing "
                f"was saved.", status=404)

        rates, _ = read_rates()
        previous = rates.get(sku_id)
        if previous is not None and previous["hsn"] == hsn and previous["rate"] == rate:
            return JSONResponse({
                "ok": True, "settles_money": False, "is_filing": False,
                "changed": False, "audited": None,
                "product": _product_row(sku_id, rec, previous),
                "detail": f"{sku_id} was already HSN {hsn} at {rate} per cent. "
                          f"Nothing was written.",
            })

        now = _now_iso()
        rates[sku_id] = {"hsn": hsn, "rate": rate, "set_at": now, "source": source}
        try:
            write_rates(rates)
        except OSError as exc:
            raise GstRefused(
                R_UNWRITABLE,
                f"the rate could not be written to {gst_path()} "
                f"({type(exc).__name__}: {exc}). Nothing was recorded.") from None
        head = _audit("gst.rate_set", sku_id=sku_id, hsn=hsn, rate=rate,
                      source=source,
                      previous_hsn=None if previous is None else previous["hsn"],
                      previous_rate=None if previous is None else previous["rate"],
                      minted=False)
        return JSONResponse({
            "ok": True, "settles_money": False, "is_filing": False,
            "changed": True, "audited": head is not None,
            "product": _product_row(sku_id, rec, rates[sku_id]),
            "previous": previous,
            "detail": (f"{sku_id} is now HSN {hsn} at {rate} per cent, "
                       f"{'accepted from the suggester' if source == SOURCE_ACCEPTED else 'as typed'}. "
                       f"Bills already closed are re-read with this rate; "
                       f"nothing about them was rewritten."),
        })
    except GstRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.delete("/gst/products/{sku_id}")
def gst_clear_ep(sku_id: str) -> JSONResponse:
    """Clear the HSN and rate for one product. The product itself is untouched."""
    try:
        rates, _ = read_rates()
        previous = rates.get(sku_id)
        if previous is None:
            raise GstRefused(
                R_NOT_SET,
                f"{sku_id!r} has no HSN or rate set, so there is nothing to "
                f"clear.")
        del rates[sku_id]
        try:
            write_rates(rates)
        except OSError as exc:
            raise GstRefused(
                R_UNWRITABLE,
                f"the file at {gst_path()} could not be written "
                f"({type(exc).__name__}: {exc}). The rate is still set.") from None
        head = _audit("gst.rate_cleared", sku_id=sku_id,
                      previous_hsn=previous["hsn"], previous_rate=previous["rate"],
                      minted=False)
        return JSONResponse({
            "ok": True, "settles_money": False, "is_filing": False,
            "sku_id": sku_id, "cleared": True, "previous": previous,
            "audited": head is not None,
            "detail": (f"{sku_id} no longer has a rate. Its lines on every bill "
                       f"now show as unrated until one is set again."),
        })
    except GstRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/gst/bill/{session_id}")
def gst_bill_ep(session_id: str) -> JSONResponse:
    """The tax inside one closed bill, line by line, from the chain.

    The lines are the ones `/manage/history/{session_id}` shows — same fold,
    same chain — with a rate put against each. Nothing about the bill is
    rewritten and nothing is invoiced: `is_invoice` is false.
    """
    try:
        bills, chain = _bills()
        bill = bills.get(session_id)
        if bill is None:
            raise GstRefused(
                R_UNKNOWN_SESSION,
                f"{session_id!r} does not appear anywhere in the audit chain at "
                f"{chain['path']}. Either it was never opened on this counter, "
                f"or it is on the far side of a chain break.", status=404)
        if not bill["closed"]:
            raise GstRefused(
                R_BILL_NOT_CLOSED,
                f"{session_id} never closed — the basket was opened and no "
                f"total was ever asked for. There is no sale here to split.")
        rates, problems = read_rates()
        known = catalogue()
        names = {sku: str(rec.get("name") or sku) for sku, rec in known.items()}
        taxed = _tax_bill(bill, rates, names)
        total = int(bill["total_paise"])
        lines_sum = taxed["rated"]["gross_paise"] + taxed["unrated_paise"]
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "is_filing": False,
            "is_invoice": False,
            "session_id": session_id,
            "at": bill["at"],
            "settled": bill["settled"],
            "settled_at": bill["settled_at"],
            "total_paise": total,
            "total_rupees": to_rupees_str(paise(total)),
            "lines_sum_paise": lines_sum,
            "total_agrees": lines_sum == total,
            "lines": taxed["lines"],
            "by_rate": _rate_rows(taxed["by_rate"]),
            "rated": _rupee_fields(taxed["rated"], *TOTAL_KEYS),
            "unrated": taxed["unrated"],
            "unrated_paise": taxed["unrated_paise"],
            "unrated_rupees": to_rupees_str(paise(taxed["unrated_paise"])),
            "excluded": taxed["excluded"],
            "unreadable_lines": taxed["unreadable_lines"],
            "complete": not taxed["unrated"] and taxed["unreadable_lines"] == 0,
            "rounding": ROUNDING,
            "problems": problems,
            "chain": chain,
            "note": NOT_FILING,
        })
    except GstRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


def _month_summary(month: Optional[str], basis_raw: Optional[str]) -> dict[str, Any]:
    """One month in the shape of GSTR-1's B2C table, with its exceptions.

    Bills are the counter's CLOSED bills by default — the sale happened when
    the basket closed, whether or not the gateway has confirmed the money —
    and `basis=settled` narrows it to bills a signature-verified webhook
    turned PAID. Both counts are in the response whichever was asked for.
    """
    basis = _basis(basis_raw)
    start, end, label = month_bounds(month)
    bills, chain = _bills()
    rates, problems = read_rates()
    known = catalogue()
    names = {sku: str(rec.get("name") or sku) for sku, rec in known.items()}

    by_rate: dict[int, dict[str, int]] = {}
    bills_by_rate: dict[int, int] = {}
    rated_total = _zero_totals()
    unrated_total = {"lines": 0, "bills": 0, "gross_paise": 0}
    unrated_skus: dict[str, dict[str, Any]] = {}
    exceptions: list[dict[str, Any]] = []
    n_bills = 0
    n_settled = 0
    n_closed_in_window = 0
    excluded_lines = 0
    unreadable = 0
    undated = 0
    months_seen: set[str] = set()

    for bill in bills.values():
        if not bill["closed"]:
            continue
        at = _parse_ts(bill["at"])
        if at is None:
            undated += 1
            continue
        local = at.astimezone(start.tzinfo)
        months_seen.add(f"{local.year:04d}-{local.month:02d}")
        if not (start <= at < end):
            continue
        n_closed_in_window += 1
        if bill["settled"]:
            n_settled += 1
        if basis == BASIS_SETTLED and not bill["settled"]:
            continue
        n_bills += 1
        taxed = _tax_bill(bill, rates, names)
        excluded_lines += len(taxed["excluded"])
        unreadable += taxed["unreadable_lines"]
        for rate, acc in taxed["by_rate"].items():
            tot = by_rate.setdefault(rate, _zero_totals())
            for k in acc:
                tot[k] += acc[k]
            bills_by_rate[rate] = bills_by_rate.get(rate, 0) + 1
        for k in rated_total:
            rated_total[k] += taxed["rated"][k]
        if taxed["unrated"]:
            unrated_total["lines"] += len(taxed["unrated"])
            unrated_total["bills"] += 1
            unrated_total["gross_paise"] += taxed["unrated_paise"]
            for ln in taxed["unrated"]:
                u = unrated_skus.setdefault(ln["sku_id"], {
                    "sku_id": ln["sku_id"], "name": ln["name"],
                    "in_catalogue": ln["sku_id"] in known,
                    "lines": 0, "gross_paise": 0})
                u["lines"] += 1
                u["gross_paise"] += int(ln["price_paise"])
            exceptions.append({
                "session_id": bill["session_id"],
                "at": bill["at"],
                "settled": bill["settled"],
                "total_paise": int(bill["total_paise"]),
                "total_rupees": to_rupees_str(int(paise(bill["total_paise"]))),
                "unrated_lines": [{"sku_id": ln["sku_id"], "name": ln["name"],
                                   "price_paise": ln["price_paise"],
                                   "price_rupees": ln["price_rupees"]}
                                  for ln in taxed["unrated"]],
                "unrated_paise": taxed["unrated_paise"],
                "unrated_rupees": to_rupees_str(paise(taxed["unrated_paise"])),
            })

    exceptions.sort(key=lambda e: (str(e["at"]), e["session_id"]))
    unrated_rows = sorted(unrated_skus.values(),
                          key=lambda u: (-u["gross_paise"], u["sku_id"]))
    for u in unrated_rows:
        u["gross_rupees"] = to_rupees_str(paise(u["gross_paise"]))

    gross_all = rated_total["gross_paise"] + unrated_total["gross_paise"]
    return {
        "month": label,
        "basis": basis,
        "window": {"start": start.isoformat(), "end": end.isoformat(),
                   "timezone": str(start.tzinfo),
                   "note": "the counter's own timezone, first instant of the "
                           "month to the first instant of the next"},
        "shape": "GSTR-1 B2C (table 7): taxable value, CGST and SGST by rate",
        "rows": _rate_rows(by_rate, bills_by_rate),
        "rated": _rupee_fields(rated_total, *TOTAL_KEYS),
        "unrated": {**unrated_total,
                    "gross_rupees": to_rupees_str(paise(unrated_total["gross_paise"])),
                    "by_sku": unrated_rows},
        "gross_paise": gross_all,
        "gross_rupees": to_rupees_str(paise(gross_all)),
        "bills": n_bills,
        "bills_closed_in_month": n_closed_in_window,
        "bills_settled_in_month": n_settled,
        "excluded_amber_lines": excluded_lines,
        "unreadable_lines": unreadable,
        "undated_bills": undated,
        "exceptions": exceptions,
        "complete": (not exceptions and unreadable == 0 and chain["ok"]),
        "months_with_bills": sorted(months_seen, reverse=True),
        "rounding": ROUNDING,
        "slab_limit": SLAB_LIMIT,
        "storefront_note": STOREFRONT_NOTE,
        "problems": problems,
        "chain": chain,
        "csv_url": f"/gst/month.csv?month={label}&basis={basis}",
        "note": NOT_FILING,
    }


@router.get("/gst/month")
def gst_month_ep(month: Optional[str] = None,
                 basis: Optional[str] = None) -> JSONResponse:
    """A month in the shape of GSTR-1's B2C table. `?month=YYYY-MM` reads any
    month; the current one when omitted. `?basis=settled` narrows to bills the
    gateway confirmed.

    `complete` is false while any bill in the month has a line whose product
    has no rate. Those bills are listed in `exceptions`, their money is in
    `unrated`, and none of it is taxed at a guess.
    """
    try:
        return JSONResponse({"ok": True, "settles_money": False,
                             "is_filing": False,
                             **_month_summary(month, basis)})
    except GstRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


CSV_COLUMNS = ("month", "basis", "rate_pct", "bills", "lines",
               "gross_rupees", "taxable_value_rupees", "cgst_rupees",
               "sgst_rupees", "total_tax_rupees",
               "gross_paise", "taxable_value_paise", "cgst_paise",
               "sgst_paise", "total_tax_paise")


def month_csv(summary: dict[str, Any]) -> str:
    """The per-rate table as CSV, one row per slab, then one `unrated` row.

    Rupee columns are the exact paise rendered as text, and the paise columns
    ride beside them so a spreadsheet that reformats the rupee strings can be
    checked against integers. The unrated row carries its gross and no tax
    figures: money that could not be classified shows up in the same table,
    with blank cells where the tax would be, rather than being left out.
    """
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    for row in summary["rows"]:
        w.writerow([
            summary["month"], summary["basis"], str(row["rate"]),
            str(row["bills"]), str(row["lines"]),
            row["gross_rupees"], row["taxable_rupees"], row["cgst_rupees"],
            row["sgst_rupees"], row["tax_rupees"],
            str(row["gross_paise"]), str(row["taxable_paise"]),
            str(row["cgst_paise"]), str(row["sgst_paise"]),
            str(row["tax_paise"]),
        ])
    u = summary["unrated"]
    w.writerow([
        summary["month"], summary["basis"], "unrated",
        str(u["bills"]), str(u["lines"]),
        u["gross_rupees"], "", "", "", "",
        str(u["gross_paise"]), "", "", "", "",
    ])
    return buf.getvalue()


@router.get("/gst/month.csv")
def gst_month_csv_ep(month: Optional[str] = None,
                     basis: Optional[str] = None):
    """The month summary as a file. A refusal here is still JSON with a 400 —
    a browser that asked for a file and got a refusal should be able to read
    why."""
    try:
        summary = _month_summary(month, basis)
        text = month_csv(summary)
        name = f"gst_b2c_{summary['month']}_{summary['basis']}.csv"
        return Response(
            text.encode("utf-8"), media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{name}"',
                "Cache-Control": "no-store",
                "X-Gawaah-Complete": "true" if summary["complete"] else "false",
                "X-Gawaah-Exceptions": str(len(summary["exceptions"])),
            })
    except GstRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


__all__ = [
    "GstRefused",
    "RULES",
    "SLABS",
    "audit_path",
    "catalogue",
    "gst_path",
    "month_bounds",
    "month_csv",
    "read_rates",
    "router",
    "shop_dir",
    "split_inclusive",
    "suggest_for_name",
    "write_rates",
]
