"""SHRENI — categories and tags for a catalogue that has outgrown one list.

A counter with four hundred SKUs cannot show them as one alphabetical column.
This module gives the shopkeeper somewhere to put things: a small set of
categories, one optional level of nesting, and free tags on any product.

Four decisions this file exists to hold, in the order they would hurt if broken:

  1. IT DOES NOT OWN THE CATALOGUE. `gawaah/shop_store.py` owns `catalog.json`
     and validates every row in it. Categories live in a SIDECAR next to it,
     `categories.json`, and nothing here opens, rewrites or migrates the
     catalogue. A shopkeeper who deletes this file loses the grouping and keeps
     every product, every price and every taught vector.

  2. DELETING A CATEGORY DELETES NOTHING ELSE. The products in it become
     uncategorised and the response says how many did. A child category is
     promoted to the top level and the response says how many were. Anything
     else would make a filing decision destructive, and a shopkeeper tidying a
     menu is not asking to lose stock.

  3. ONE LEVEL OF NESTING, AND THAT IS DELIBERATE. Household > Cleaning is
     useful. Household > Cleaning > Floor > Liquid is a tree nobody at a counter
     will ever fill in, and it turns every read into a recursion with cycles to
     guard. A category may have a parent; a category that has a parent may not
     be a parent. The refusal is named `category_nesting_only_one_level` rather
     than silently flattening, because quietly re-parenting somebody's menu is
     worse than saying no.

  4. THE SUGGESTION IS A KEYWORD LIST, NOT INFERENCE. `GET /categories/suggest`
     reads product NAMES and matches them against the fixed, ordered table in
     `RULES` below. It does not look at a photograph, it does not embed
     anything, it has no model and it learns nothing from what you accept. It
     proposes; a person accepts by posting the ones they agree with to
     `POST /categories/assign`. The whole table is returned in the response so
     the shopkeeper can see exactly why a packet was filed where it was.

No money moves here. Prices appear in `GET /categories/products` only because a
product list is unreadable without them, and every one of them is read from the
catalogue as integer paise and rendered by `gawaah/money.py`.

The router carries NO prefix: the paths below are absolute. Mount it with
`app.include_router(categories.router)`.
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
from .money import MoneyError, paise, to_rupees_str

router = APIRouter()


# --------------------------------------------------------------- refusals --
#
# Every one of these is a state this module can actually reach, and each is
# written so a shopkeeper can act on it without reading this file.

R_NO_TILL = "till_module_unavailable"
R_NO_CATALOGUE = "catalogue_unavailable"
R_BAD_BODY = "category_body_not_json"
R_NO_NAME = "category_name_missing"
R_TOO_LONG = "field_too_long"
R_NAME_TAKEN = "category_name_already_used"
R_NO_CATEGORY = "no_such_category"
R_NO_PARENT = "no_such_parent_category"
R_NESTING_TOO_DEEP = "category_nesting_only_one_level"
R_SELF_PARENT = "category_cannot_be_its_own_parent"
R_BAD_SORT = "sort_order_not_a_whole_number"
R_TOO_MANY = "too_many_categories"
R_UNKNOWN_SKU = "sku_not_in_this_shop"
R_BAD_TAG = "tag_not_usable"
R_TOO_MANY_TAGS = "too_many_tags_on_one_product"
R_NOTHING_TO_CHANGE = "nothing_to_change"
R_BAD_ASSIGNMENT = "assignment_list_malformed"
R_TOO_MANY_ASSIGNMENTS = "too_many_assignments_in_one_request"
R_UNWRITABLE = "categories_file_unwritable"
R_INTERNAL = "categories_internal_error"


# ------------------------------------------------------------------ caps --
#
# Every one of these bounds something that ends up on disk or in a dropdown.
# What they cost when they are wrong: a shopkeeper with a genuinely larger shop
# has to be told the number and it has to be changed here. That is a nuisance;
# an unbounded write is not.

#: Sixty is about as far as a shopkeeper will scroll a filing menu before it
#: stops being a filter and starts being the flat list it replaced.
MAX_CATEGORIES = 60
MAX_NAME = 40
MAX_TAG = 24
MAX_TAGS_PER_SKU = 12
#: One accept of a whole four-hundred-SKU catalogue, with room over it.
MAX_ASSIGN = 500
MAX_SORT = 9999
#: The gap between two default sort orders, so a shopkeeper can put something
#: between them later without renumbering the menu.
SORT_STEP = 10

CATEGORIES_FILENAME = "categories.json"
CATEGORIES_AUDIT_FILENAME = "categories.audit.jsonl"
CATEGORIES_FORMAT = 1

CATEGORY_ID_RE = re.compile(r"^cat_[0-9a-f]{8}$")
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9 _-]*$")

#: What `?category=` means when a caller wants the products that are in nothing.
NO_CATEGORY = "none"


class CategoryRefused(Exception):
    """A named refusal with a reason a human can act on."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def _refusal(exc: CategoryRefused, status: int = 400) -> JSONResponse:
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
# in a fixture has to be able to change it between tests, and a module-level
# constant captured at import time silently ignores that — which is how a
# harness once wrote over the live catalogue in results/.

_OVERRIDE: dict[str, Optional[Path]] = {"path": None}

from gawaah import till_ref as _till_ref

#: One definition, in `gawaah/till_ref.py`. This was sixteen copies of a tuple
#: that was missing `__main__`, and every one of them was wrong the same way.
_TILL_NAMES = _till_ref.TILL_NAMES


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _till() -> Any:
    """The already-loaded till module, or a named refusal.

    LOOK IN sys.modules FIRST. `make serve` runs `uvicorn upload_app:app
    --app-dir tools`, so the module is registered as `upload_app`; the test
    suite does `from tools import upload_app` and registers it as
    `tools.upload_app`. Importing the other spelling loads a SECOND copy of the
    file with its own cached store handle, so a `set_store_dir` in a test would
    not reach the copy serving requests and this module would file products
    against a different shop than the one it is mounted in.
    """
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
        raise CategoryRefused(
            R_NO_TILL,
            f"tools/upload_app.py is not importable ({type(exc).__name__}: "
            f"{exc}). Categories are filed against the shopkeeper's catalogue "
            f"and this module will not keep a second copy of it.") from None
    return upload_app


def shop_dir() -> Path:
    """The shopkeeper's catalogue directory — the till's answer, not a second.

    `upload_app.store_dir()` is what honours GAWAAH_SHOP_DIR, and
    `upload_app.set_store_dir()` can move the catalogue without touching the
    environment. Deriving the path here would be a second answer to one
    question, and the day the catalogue moves and the categories stay behind is
    the day a shopkeeper's filing quietly describes a different shop.
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


def categories_path() -> Path:
    """The sidecar. NEXT TO `catalog.json`, and never `catalog.json` itself."""
    if _OVERRIDE["path"] is not None:
        return Path(_OVERRIDE["path"])
    explicit = os.environ.get("GAWAAH_CATEGORIES_FILE")
    if explicit:
        return Path(explicit)
    return shop_dir() / CATEGORIES_FILENAME


def set_categories_path(path: Any) -> None:
    """Point the sidecar somewhere else. For tests and for nothing else."""
    _OVERRIDE["path"] = None if path is None else Path(path)


def audit_path() -> Path:
    """This module's own hash-chained log.

    DELIBERATELY NOT `results/audit.jsonl`. The money service holds that file
    open in another process and keeps the chain head in memory; a second writer
    appending between two of its writes gives it a stale head, every line it
    writes afterwards fails `gawaah.ledger.verify`, and `make verify-ledger`
    goes red on the one log that must be beyond argument. Filing a packet under
    Snacks is worth recording; corrupting the money ledger to record it is not.

    What this costs: there are several chains to walk instead of one, and a
    reader who checks only `results/audit.jsonl` will not see the filing. That
    is a documentation problem. The alternative was a broken money audit trail.
    """
    return shop_dir() / CATEGORIES_AUDIT_FILENAME


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _audit(event: str, **fields: Any) -> Optional[str]:
    """Append one auditable line. Returns the new head, or None if it failed.

    Best effort, but never silent: every caller reports `audited` so a response
    never claims a witnessed change that was not written down.
    """
    try:
        return Ledger(audit_path()).append(
            ts=_now_iso(), module="categories", event=event, **fields)
    except Exception:  # noqa: BLE001 - a failed audit must not lose a change
        return None


# --------------------------------------------------------- the category --


@dataclass(frozen=True)
class Category:
    """One shelf label, exactly as it is stored.

    `category_id` is random rather than derived from the name, so renaming
    Snacks to Namkeen keeps every product filed under it. `parent_id is None`
    means top level, and a category whose parent is set may not itself be a
    parent — see the module docstring on why one level is the whole tree.
    """

    category_id: str
    name: str
    parent_id: Optional[str]
    sort_order: int
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "category_id": self.category_id,
            "name": self.name,
            "parent_id": self.parent_id,
            "sort_order": int(self.sort_order),
            "created_at": self.created_at,
        }


def _new_category_id() -> str:
    return "cat_" + secrets.token_hex(4)


def _clean_int(value: Any) -> Optional[int]:
    """A whole number, or None. bool is not a number and float is not whole."""
    if isinstance(value, bool) or isinstance(value, float):
        return None
    if not isinstance(value, int):
        return None
    return int(value)


def _category_from_record(rec: Any) -> Optional[Category]:
    """One stored record -> a Category, or None if it cannot be trusted.

    A row that does not parse is DROPPED rather than raised on. This file is
    read on every catalogue page load, and one malformed row left by a hand-edit
    must not turn a working Products screen into an error page. What it costs is
    that the dropped row's products read as uncategorised, which is visible and
    fixable, unlike a screen that will not open.
    """
    if not isinstance(rec, dict):
        return None
    cid = rec.get("category_id")
    if not isinstance(cid, str) or not CATEGORY_ID_RE.match(cid):
        return None
    name = rec.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    name = " ".join(name.split())[:MAX_NAME]
    parent = rec.get("parent_id")
    if not isinstance(parent, str) or not CATEGORY_ID_RE.match(parent):
        parent = None
    order = _clean_int(rec.get("sort_order"))
    if order is None or order < 0:
        order = 0
    if order > MAX_SORT:
        order = MAX_SORT
    created = rec.get("created_at")
    if not isinstance(created, str):
        created = ""
    return Category(cid, name, parent, order, created)


def clean_tag(value: Any) -> str:
    """One tag, normalised, or a named refusal.

    Lowercase and single-spaced so that `Daily`, `daily` and `daily ` are one
    tag rather than three that a shopkeeper has to notice are the same.
    """
    if not isinstance(value, str):
        raise CategoryRefused(
            R_BAD_TAG,
            f"a tag must be text, not {type(value).__name__}. Nothing was "
            f"saved.")
    tag = " ".join(value.split()).lower()
    if not tag:
        raise CategoryRefused(
            R_BAD_TAG, "an empty tag is not a tag. To remove tags, send an "
                       "empty list.")
    if len(tag) > MAX_TAG:
        raise CategoryRefused(
            R_TOO_LONG,
            f"the tag {tag!r} is {len(tag)} characters and the cap is "
            f"{MAX_TAG}. Nothing was saved.")
    if not TAG_RE.match(tag):
        raise CategoryRefused(
            R_BAD_TAG,
            f"{tag!r} has characters a tag cannot carry. A tag is letters, "
            f"digits, spaces, '-' and '_', and it starts with a letter or a "
            f"digit.")
    return tag


def _clean_tags(values: Any) -> list[str]:
    """A whole tag list: deduplicated, sorted, capped.

    Sorted rather than kept in the order they were typed, so the file on disk is
    the same file whichever order two shopkeepers typed the same tags in.
    """
    if values is None:
        return []
    if not isinstance(values, list):
        raise CategoryRefused(
            R_BAD_TAG,
            f"'tags' must be a list of words, not {type(values).__name__}.")
    seen: set[str] = set()
    for raw in values:
        seen.add(clean_tag(raw))
    if len(seen) > MAX_TAGS_PER_SKU:
        raise CategoryRefused(
            R_TOO_MANY_TAGS,
            f"that is {len(seen)} tags on one product and the cap is "
            f"{MAX_TAGS_PER_SKU}. Nothing was saved.")
    return sorted(seen)


# ------------------------------------------------------------- the store --


def _read_doc(path: Path) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return doc if isinstance(doc, dict) else {}


def load_book(path: Optional[Path] = None
              ) -> tuple[list[Category], dict[str, dict[str, Any]]]:
    """Everything on disk: the categories, and what each SKU is filed under.

    Never raises for a bad file. A shop with no sidecar and a shop whose sidecar
    is unreadable both read as "nothing is categorised yet", which is the only
    answer that still shows the shopkeeper their whole catalogue.

    Two repairs happen HERE rather than at write time, because a file can also
    be hand-edited: a category whose parent does not exist, and a category whose
    parent is itself a child, are both read as top level. That keeps the one
    level of nesting true of what is read as well as of what is written.
    """
    p = Path(path) if path is not None else categories_path()
    try:
        doc = _read_doc(p)
    except (OSError, ValueError):
        return [], {}

    rows = doc.get("categories")
    cats: list[Category] = []
    seen_ids: set[str] = set()
    for rec in rows if isinstance(rows, list) else []:
        cat = _category_from_record(rec)
        if cat is None or cat.category_id in seen_ids:
            continue
        seen_ids.add(cat.category_id)
        cats.append(cat)

    known = {c.category_id: c for c in cats}
    parented = {c.category_id for c in cats if c.parent_id is not None}
    repaired: list[Category] = []
    for c in cats:
        parent = c.parent_id
        if parent is not None and (parent == c.category_id
                                   or parent not in known
                                   or parent in parented):
            parent = None
        repaired.append(Category(c.category_id, c.name, parent, c.sort_order,
                                 c.created_at))

    skus_raw = doc.get("skus")
    skus: dict[str, dict[str, Any]] = {}
    for sku_id, rec in (skus_raw if isinstance(skus_raw, dict) else {}).items():
        if not isinstance(sku_id, str) or not sku_id.strip():
            continue
        if not isinstance(rec, dict):
            continue
        cid = rec.get("category_id")
        if not isinstance(cid, str) or cid not in known:
            cid = None
        tags: list[str] = []
        for raw in rec.get("tags") or []:
            try:
                tags.append(clean_tag(raw))
            except CategoryRefused:
                continue
        if cid is None and not tags:
            continue
        skus[sku_id.strip()] = {"category_id": cid,
                                "tags": sorted(set(tags))[:MAX_TAGS_PER_SKU]}
    return repaired, skus


def save_book(cats: list[Category], skus: dict[str, dict[str, Any]],
              path: Optional[Path] = None) -> Path:
    """Write the whole sidecar, atomically. Raises CategoryRefused on failure.

    Temp file and rename, so a Products screen loading while the shopkeeper
    saves reads either the old file or the new one and never half of one.
    """
    p = Path(path) if path is not None else categories_path()
    doc = {
        "format": CATEGORIES_FORMAT,
        "written_at": _now_iso(),
        "categories": [c.as_dict() for c in cats],
        "skus": {k: {"category_id": v.get("category_id"),
                     "tags": list(v.get("tags") or [])}
                 for k, v in sorted(skus.items())},
        "note": ("A sidecar. gawaah/shop_store.py owns catalog.json; deleting "
                 "this file loses the grouping and no products."),
    }
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(doc, sort_keys=True, indent=1) + "\n",
                       encoding="utf-8")
        os.replace(tmp, p)
    except OSError as exc:
        raise CategoryRefused(
            R_UNWRITABLE,
            f"{str(p)!r} could not be written ({type(exc).__name__}: {exc}). "
            f"Nothing was changed.") from None
    return p


# ------------------------------------------------------------- catalogue --


def catalogue() -> dict[str, dict[str, Any]]:
    """{sku_id -> name, price_paise, how} for everything this shop can sell.

    `offer_priced_skus()` where the till has it, so a product list shows the
    price that would actually be charged today rather than the shelf-edge one.
    Falls back to `priced_skus()` on a build without offers.

    A LIMIT WORTH STATING: this is the PRICED catalogue, so a product that was
    taught without a price is not in it and cannot be filed. Put a price on it
    first. Deriving a second definition of "a product" here would give the
    Products screen two catalogues that disagree.
    """
    up = _till()
    for name in ("offer_priced_skus", "priced_skus"):
        fn = getattr(up, name, None)
        if fn is None:
            continue
        try:
            return dict(fn())
        except Exception as exc:  # noqa: BLE001 - the store may be unreadable
            reason = getattr(exc, "reason", None) or R_NO_CATALOGUE
            detail = getattr(exc, "detail", None) or (
                f"the catalogue could not be read ({type(exc).__name__}: "
                f"{exc})")
            raise CategoryRefused(reason, detail) from None
    raise CategoryRefused(
        R_NO_CATALOGUE,
        "the till module exposes no priced catalogue, so there is nothing to "
        "file.")


# ------------------------------------------------- the suggestion, spelled out --
#
# THIS IS A KEYWORD LIST. It reads the product's NAME, lowercases it, and looks
# for whole words from the table below. There is no model, no embedding, no
# learning and no memory of what was accepted last time: the same catalogue
# produces the same proposals on every machine, forever, and the whole table is
# returned in the response so a shopkeeper can see why a packet was proposed
# where it was.
#
# THE ORDER IS THE TIE-BREAK, and it is the only one. The first rule with a
# matching keyword wins, so a name carrying two keywords is decided by which
# rule is written higher. "Baby soap" is Baby rather than Household because Baby
# is first; "Parachute Hair Oil" is Personal care rather than Staples because
# `hair oil` is checked before `oil`.
#
# WORDS THAT ARE HONESTLY AMBIGUOUS ARE LEFT OUT rather than guessed at.
# "powder" is chilli powder and it is talcum powder; "cream" is fresh cream and
# it is cold cream. Neither appears below, so those packets come back unmatched
# and a person files them. An unmatched product is a small nuisance; a
# confidently wrong one is a shopkeeper's menu quietly filling up with mistakes.
#
# A KNOWN MISS, STATED RATHER THAN HIDDEN: "soap" is under Household, so a bar
# of bathing soap is proposed as Household. That is the trade the keyword makes
# — detergent bars and bathing bars share the word — and it is exactly why this
# endpoint proposes and a person accepts.

RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Baby", ("baby", "diaper", "diapers", "lactogen", "cerelac", "nan pro")),
    ("Dairy", ("milk", "doodh", "curd", "dahi", "paneer", "butter", "makhan",
               "cheese", "ghee", "lassi", "buttermilk", "chaas", "khoya")),
    ("Beverages", ("tea", "chai", "coffee", "juice", "cola", "pepsi", "coke",
                   "thums up", "sprite", "fanta", "maaza", "frooti", "soda",
                   "squash", "sharbat", "horlicks", "bournvita", "boost",
                   "water")),
    ("Snacks", ("biscuit", "biscuits", "cookie", "cookies", "namkeen", "chips",
                "kurkure", "bhujia", "mixture", "wafer", "wafers", "rusk",
                "papad", "mathri", "khari", "parle", "oreo", "marie", "monaco",
                "chocolate", "toffee", "candy")),
    ("Ready to eat", ("maggi", "noodles", "pasta", "vermicelli", "sevai",
                      "soup", "ketchup", "sauce", "jam", "pickle", "achar",
                      "chutney", "honey")),
    ("Spices", ("masala", "haldi", "turmeric", "mirch", "chilli", "chili",
                "jeera", "cumin", "dhania", "coriander", "elaichi", "cardamom",
                "laung", "clove", "hing", "ajwain", "methi", "saunf",
                "kalonji")),
    ("Personal care", ("shampoo", "toothpaste", "colgate", "pepsodent",
                       "toothbrush", "hair oil", "deodorant", "talc", "razor",
                       "blade", "shaving", "sanitary", "comb", "face wash",
                       "lotion", "vaseline", "boroline", "kajal", "mehndi",
                       "henna")),
    ("Staples", ("atta", "flour", "maida", "suji", "sooji", "rava", "rice",
                 "chawal", "basmati", "dal", "daal", "toor", "arhar", "moong",
                 "masoor", "chana", "rajma", "urad", "poha", "sabudana",
                 "sugar", "cheeni", "salt", "namak", "oil", "tel", "besan")),
    ("Household", ("soap", "detergent", "surf", "rin", "vim", "harpic",
                   "phenyl", "phenyle", "cleaner", "bleach", "lizol", "colin",
                   "dishwash", "broom", "jhadu", "tissue", "naphthalene",
                   "agarbatti", "dhoop", "matchbox", "matches", "candle",
                   "mosquito", "repellent", "bulb", "battery")),
    ("Stationery", ("pen", "pencil", "notebook", "eraser", "sharpener",
                    "register", "glue", "stapler", "envelope")),
)

#: Every category name the table can propose, in the order it proposes them.
SUGGESTED_NAMES: tuple[str, ...] = tuple(name for name, _ in RULES)


def _words(name: str) -> str:
    """The name as a space-padded lowercase word string, for whole-word matching.

    Whole words, not substrings, and the padding is what makes the first and
    last word matchable. Without it "salt" matches "Salted Chips" and "pen"
    matches "Pepsodent", and a shopkeeper would rightly stop reading the
    proposals after the second one of those.
    """
    return " " + re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip() + " "


def suggest_for_name(name: str) -> Optional[tuple[str, str]]:
    """(category name, the keyword that matched), or None. Pure, and stable."""
    padded = _words(name)
    for category, keywords in RULES:
        for word in keywords:
            if f" {word} " in padded:
                return category, word
    return None


# ---------------------------------------------------------------- reading --


def _by_id(cats: list[Category]) -> dict[str, Category]:
    return {c.category_id: c for c in cats}


def _sorted_categories(cats: list[Category]) -> list[Category]:
    """Parents in menu order, each followed by its own children in menu order.

    Sorted by (sort_order, name) at both levels so two categories a shopkeeper
    left at the same number still come out in the same order every time.
    """
    tops = sorted((c for c in cats if c.parent_id is None),
                  key=lambda c: (c.sort_order, c.name.lower(), c.category_id))
    out: list[Category] = []
    for top in tops:
        out.append(top)
        kids = sorted((c for c in cats if c.parent_id == top.category_id),
                      key=lambda c: (c.sort_order, c.name.lower(),
                                     c.category_id))
        out.extend(kids)
    # A child whose parent vanished between the read and here cannot exist —
    # load_book() promotes it — but a caller passing a hand-built list can hit
    # it, and dropping a category out of a menu silently is not a thing this
    # file does.
    seen = {c.category_id for c in out}
    out.extend(c for c in cats if c.category_id not in seen)
    return out


def _counts(skus: dict[str, dict[str, Any]], known_skus: set[str]
            ) -> dict[str, int]:
    """How many PRESENT products are filed under each category id."""
    out: dict[str, int] = {}
    for sku_id, rec in skus.items():
        if sku_id not in known_skus:
            continue
        cid = rec.get("category_id")
        if isinstance(cid, str):
            out[cid] = out.get(cid, 0) + 1
    return out


def _counts_after_a_write(skus: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Counts for a response whose write has ALREADY HAPPENED.

    An unreadable catalogue here must not turn a change that was saved into a
    refusal that says it was not. So the catalogue is consulted if it answers,
    and every filed sku is counted if it does not — a count that is slightly
    generous beats a response that lies about whether the write landed.
    """
    try:
        return _counts(skus, set(catalogue()))
    except CategoryRefused:
        return _counts(skus, set(skus))


def _category_view(cat: Category, cats: list[Category],
                   counts: dict[str, int]) -> dict[str, Any]:
    known = _by_id(cats)
    children = [c.category_id for c in cats if c.parent_id == cat.category_id]
    own = counts.get(cat.category_id, 0)
    with_children = own
    for kid in children:
        with_children += counts.get(kid, 0)
    parent = known.get(cat.parent_id) if cat.parent_id else None
    return {
        **cat.as_dict(),
        "depth": 0 if cat.parent_id is None else 1,
        "parent_name": parent.name if parent else None,
        "children": children,
        "products": own,
        "products_including_children": with_children,
    }


def _tag_counts(skus: dict[str, dict[str, Any]], known_skus: set[str]
                ) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for sku_id, rec in skus.items():
        if sku_id not in known_skus:
            continue
        for tag in rec.get("tags") or []:
            counts[tag] = counts.get(tag, 0) + 1
    return [{"tag": t, "products": counts[t]}
            for t in sorted(counts, key=lambda t: (-counts[t], t))]


def _find_category(cats: list[Category], category_id: Any) -> Category:
    if isinstance(category_id, str):
        for c in cats:
            if c.category_id == category_id:
                return c
    raise CategoryRefused(
        R_NO_CATEGORY,
        f"this shop has no category {category_id!r}. Nothing was changed.")


def _name_taken(cats: list[Category], name: str,
                *, except_id: str = "") -> bool:
    """Names are unique across the whole shop, not only within one parent.

    Two categories called Snacks in different parents are two lines that read
    the same in a dropdown, and a shopkeeper picking the wrong one has no way to
    tell. The cost is that Household > Cleaning and Kitchen > Cleaning cannot
    both exist; they have to be named for what they are.
    """
    low = name.lower()
    return any(c.name.lower() == low and c.category_id != except_id
               for c in cats)


def _read_name(body: dict[str, Any], *, required: bool) -> Optional[str]:
    raw = body.get("name")
    if raw is None:
        if required:
            raise CategoryRefused(
                R_NO_NAME,
                "a category needs a name — it is the only part of it a "
                "shopkeeper reads.")
        return None
    if not isinstance(raw, str):
        raise CategoryRefused(
            R_NO_NAME, f"a category name must be text, not "
                       f"{type(raw).__name__}.")
    name = " ".join(raw.split())
    if not name:
        raise CategoryRefused(
            R_NO_NAME,
            "a category needs a name — it is the only part of it a shopkeeper "
            "reads.")
    if len(name) > MAX_NAME:
        raise CategoryRefused(
            R_TOO_LONG,
            f"that name is {len(name)} characters and the cap is {MAX_NAME}. "
            f"Nothing was saved.")
    return name


def _read_sort(body: dict[str, Any]) -> Optional[int]:
    if "sort_order" not in body or body.get("sort_order") is None:
        return None
    order = _clean_int(body.get("sort_order"))
    if order is None:
        raise CategoryRefused(
            R_BAD_SORT,
            f"the sort order {body.get('sort_order')!r} is not a whole number. "
            f"It is the position in a menu, so it counts in ones.")
    if order < 0 or order > MAX_SORT:
        raise CategoryRefused(
            R_BAD_SORT,
            f"the sort order {order} is outside 0 to {MAX_SORT}.")
    return order


def _next_sort(cats: list[Category], parent_id: Optional[str]) -> int:
    """Put a new category at the end of its own level, with room before it."""
    peers = [c.sort_order for c in cats if c.parent_id == parent_id]
    if not peers:
        return SORT_STEP
    nxt = max(peers) + SORT_STEP
    return nxt if nxt <= MAX_SORT else MAX_SORT


def _read_parent(body: dict[str, Any], cats: list[Category],
                 *, self_id: str = "") -> Optional[str]:
    """The parent id from a body, checked against the one level of nesting."""
    raw = body.get("parent_id")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if not isinstance(raw, str):
        raise CategoryRefused(
            R_NO_PARENT,
            f"a parent has to be named by its category id, not by a "
            f"{type(raw).__name__}.")
    parent_id = raw.strip()
    if self_id and parent_id == self_id:
        raise CategoryRefused(
            R_SELF_PARENT,
            "a category cannot be filed inside itself. Nothing was changed.")
    parent = next((c for c in cats if c.category_id == parent_id), None)
    if parent is None:
        raise CategoryRefused(
            R_NO_PARENT,
            f"this shop has no category {parent_id!r} to file that under. "
            f"Nothing was changed.")
    if parent.parent_id is not None:
        raise CategoryRefused(
            R_NESTING_TOO_DEEP,
            f"{parent.name!r} is already inside another category, and this "
            f"counter keeps one level of nesting. File it under a top-level "
            f"category instead.")
    if self_id and any(c.parent_id == self_id for c in cats):
        raise CategoryRefused(
            R_NESTING_TOO_DEEP,
            "that category has categories inside it, so it cannot also go "
            "inside one. Move its children out first.")
    return parent_id


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a named refusal
        raise CategoryRefused(
            R_BAD_BODY, "this request's body is not JSON.") from None
    if not isinstance(body, dict):
        raise CategoryRefused(
            R_BAD_BODY,
            f"this request's body is a {type(body).__name__}; it must be a "
            f"JSON object.")
    return body


def _known_sku(sku_id: Any, known: dict[str, dict[str, Any]]) -> str:
    if not isinstance(sku_id, str) or not sku_id.strip():
        raise CategoryRefused(
            R_UNKNOWN_SKU, "a product has to be named by its sku id.")
    sku = sku_id.strip()
    if sku not in known:
        raise CategoryRefused(
            R_UNKNOWN_SKU,
            f"{sku!r} is not a product this shop has priced. Nothing was "
            f"changed. Teach it, or put a price on it, first.")
    return sku


def _price_fields(rec: dict[str, Any]) -> dict[str, Any]:
    """The money on a product row: integer paise in, rupee string out.

    `paise()` refuses a float or a bool, so a catalogue that ever held 21.45
    instead of 2145 stops here rather than becoming an approximate rupee on a
    screen.
    """
    price = int(paise(rec["price_paise"]))
    out: dict[str, Any] = {"price_paise": price,
                           "price_rupees": to_rupees_str(paise(price))}
    marked = rec.get("marked_paise")
    off = rec.get("off_paise")
    if not isinstance(marked, bool) and isinstance(marked, int) \
            and not isinstance(off, bool) and isinstance(off, int) and off > 0:
        marked_p = int(paise(marked))
        if marked_p > price:
            out["marked_paise"] = marked_p
            out["marked_rupees"] = to_rupees_str(paise(marked_p))
            out["off_paise"] = int(paise(off))
    return out


def _product_view(sku_id: str, rec: dict[str, Any],
                  filed: dict[str, Any], known_cats: dict[str, Category]
                  ) -> dict[str, Any]:
    cid = filed.get("category_id")
    cat = known_cats.get(cid) if isinstance(cid, str) else None
    parent = known_cats.get(cat.parent_id) if cat and cat.parent_id else None
    return {
        "sku_id": sku_id,
        "name": str(rec.get("name") or sku_id),
        **_price_fields(rec),
        "taught_with": str(rec.get("how") or "unknown"),
        "category_id": cat.category_id if cat else None,
        "category_name": cat.name if cat else None,
        "parent_name": parent.name if parent else None,
        "tags": list(filed.get("tags") or []),
    }


# ----------------------------------------------------------------- routes --
#
# The static paths are declared BEFORE `/categories/{category_id}`. Nothing
# below actually collides today — the parameterised routes are PATCH and DELETE
# and the static ones are GET and POST — but a GET added to the parameterised
# path later would start swallowing `/categories/suggest`, and the failure would
# look like the suggestion endpoint returning "no such category cat_suggest".


@router.get("/categories")
def categories_ep() -> JSONResponse:
    """The whole filing menu: categories in menu order, with product counts.

    `uncategorised` is the count that matters on a first run — it is every
    priced product that has not been filed yet, and it is the number a
    shopkeeper is trying to drive down.
    """
    try:
        cats, skus = load_book()
        known = catalogue()
        known_ids = set(known)
        counts = _counts(skus, known_ids)
        rows = [_category_view(c, cats, counts) for c in _sorted_categories(cats)]
        filed = sum(1 for s, rec in skus.items()
                    if s in known_ids and rec.get("category_id"))
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "count": len(rows),
            "categories": rows,
            "products": len(known_ids),
            "categorised": filed,
            "uncategorised": len(known_ids) - filed,
            "tags": _tag_counts(skus, known_ids),
            "limits": {
                "max_categories": MAX_CATEGORIES,
                "max_name": MAX_NAME,
                "max_tags_per_sku": MAX_TAGS_PER_SKU,
                "max_tag": MAX_TAG,
                "nesting": "one level: a category may have a parent, and a "
                           "category that has a parent may not be a parent",
            },
            "file": str(categories_path()),
        })
    except CategoryRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/categories/suggest")
def categories_suggest_ep(include_assigned: bool = False) -> JSONResponse:
    """Propose a category per product FROM ITS NAME. It changes nothing.

    A KEYWORD LIST, NOT INFERENCE. The whole table is in the `rules` field of
    this response: the product's name is lowercased, split into words, and the
    first rule with a matching word wins. There is no model here and nothing is
    learned from what you accept — the same catalogue proposes the same things
    tomorrow.

    A proposal is only `ready` when the shop already has a category by that
    name. The names this table can propose but this shop has not created are
    listed in `missing_categories`, so the shopkeeper can make the ones they
    want and leave the rest.

    Products that are already filed are left alone unless `include_assigned=1`,
    because re-proposing over a decision a person already made is how a
    suggestion turns into a nuisance.

    Nothing here is applied. Post the ones you agree with to
    `POST /categories/assign`.
    """
    try:
        cats, skus = load_book()
        known = catalogue()
        by_name = {c.name.lower(): c for c in cats}

        proposals: list[dict[str, Any]] = []
        unmatched: list[dict[str, Any]] = []
        skipped = 0
        wanted: set[str] = set()
        for sku_id in sorted(known):
            filed = skus.get(sku_id) or {}
            already = filed.get("category_id")
            if already and not include_assigned:
                skipped += 1
                continue
            name = str(known[sku_id].get("name") or sku_id)
            hit = suggest_for_name(name)
            if hit is None:
                unmatched.append({"sku_id": sku_id, "name": name})
                continue
            category_name, word = hit
            cat = by_name.get(category_name.lower())
            if cat is None:
                wanted.add(category_name)
            proposals.append({
                "sku_id": sku_id,
                "name": name,
                "suggested_name": category_name,
                "category_id": cat.category_id if cat else None,
                "matched_keyword": word,
                "ready": cat is not None,
                "currently": already or None,
            })

        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "method": "keyword list, not inference",
            "how": ("The product's name is lowercased and split into words. "
                    "The first rule below with a matching whole word wins, so "
                    "the order of the rules is the tie-break. Nothing looks at "
                    "a photograph and nothing is learned from what you "
                    "accept."),
            "count": len(proposals),
            "proposals": proposals,
            "unmatched": unmatched,
            "already_categorised": skipped,
            "missing_categories": sorted(wanted),
            "rules": [{"category": name, "keywords": list(words)}
                      for name, words in RULES],
            "accept_with": "POST /categories/assign",
            "changed_nothing": True,
            "note": ("These are proposals. Nothing has been filed. Known "
                     "misses: 'soap' is listed under Household, so a bathing "
                     "bar is proposed as Household, and genuinely ambiguous "
                     "words such as 'powder' and 'cream' are in no rule at all "
                     "and come back unmatched."),
        })
    except CategoryRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/categories/products")
def categories_products_ep(category: str = "", tag: str = "",
                           q: str = "") -> JSONResponse:
    """The catalogue with its filing attached, optionally filtered.

    `category=<id>` narrows to one category AND the categories inside it, which
    is what a shopkeeper tapping "Household" means. `category=none` is every
    product that has not been filed. `tag=` matches one tag exactly; `q=`
    matches text anywhere in the product's name.

    Not paginated. Four hundred rows is about sixty kilobytes and one screen's
    worth of scrolling with the filters above; adding pages before that hurts
    would be a second thing to get wrong.
    """
    try:
        cats, skus = load_book()
        known = catalogue()
        known_cats = _by_id(cats)

        wanted_ids: Optional[set[str]] = None
        included_children: list[str] = []
        uncategorised_only = False
        if category:
            if category == NO_CATEGORY:
                uncategorised_only = True
            else:
                cat = _find_category(cats, category)
                included_children = [c.category_id for c in cats
                                     if c.parent_id == cat.category_id]
                wanted_ids = {cat.category_id, *included_children}

        want_tag = clean_tag(tag) if tag else ""
        needle = " ".join((q or "").split()).lower()

        rows: list[dict[str, Any]] = []
        for sku_id in sorted(known):
            filed = skus.get(sku_id) or {"category_id": None, "tags": []}
            cid = filed.get("category_id")
            if uncategorised_only and cid:
                continue
            if wanted_ids is not None and cid not in wanted_ids:
                continue
            if want_tag and want_tag not in (filed.get("tags") or []):
                continue
            row = _product_view(sku_id, known[sku_id], filed, known_cats)
            if needle and needle not in row["name"].lower():
                continue
            rows.append(row)

        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "count": len(rows),
            "products": rows,
            "filter": {"category": category or None, "tag": want_tag or None,
                       "q": needle or None,
                       "included_children": included_children},
            "catalogue_size": len(known),
            "paginated": False,
        })
    except CategoryRefused as exc:
        return _refusal(exc, status=404 if exc.reason == R_NO_CATEGORY else 400)
    except MoneyError as exc:
        return _refusal(CategoryRefused(
            R_NO_CATALOGUE,
            f"a price in this shop's catalogue is not integer paise ({exc}). "
            f"The product list stops rather than rounding it."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/categories/health")
def categories_health_ep() -> JSONResponse:
    """Where the sidecar is and what is in it.

    `file` is the useful line: if the till was started with a different
    GAWAAH_SHOP_DIR than the one the catalogue lives in, the categories will be
    filed beside a shop that is not this one, and printing the resolved path
    turns that from a mystery into a comparison.

    `orphans` counts filings for SKUs that are no longer in the catalogue. They
    are kept rather than swept: a product re-taught under the same sku id gets
    its shelf back, and a sweep that ran on a temporarily unreadable catalogue
    would throw away a shopkeeper's whole afternoon of filing.
    """
    try:
        cats, skus = load_book()
        try:
            known_ids = set(catalogue())
            readable = True
        except CategoryRefused:
            known_ids, readable = set(), False
        p = categories_path()
        return JSONResponse({
            "ok": True,
            "module": "categories",
            "settles_money": False,
            "file": str(p),
            "exists": p.exists(),
            "audit_file": str(audit_path()),
            "shop_dir": str(shop_dir()),
            "catalogue_readable": readable,
            "categories": len(cats),
            "top_level": sum(1 for c in cats if c.parent_id is None),
            "nested": sum(1 for c in cats if c.parent_id is not None),
            "filed": sum(1 for s, r in skus.items()
                         if s in known_ids and r.get("category_id")),
            "orphans": sum(1 for s in skus if s not in known_ids),
            "owns_catalog_json": False,
        })
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/categories")
async def categories_create_ep(request: Request) -> JSONResponse:
    """Make a category. Body: {name, parent_id?, sort_order?}."""
    try:
        body = await _json_body(request)
        cats, skus = load_book()
        if len(cats) >= MAX_CATEGORIES:
            raise CategoryRefused(
                R_TOO_MANY,
                f"this counter holds {MAX_CATEGORIES} categories and there are "
                f"already {len(cats)}. Delete one first — deleting a category "
                f"keeps its products.")
        name = _read_name(body, required=True) or ""
        if _name_taken(cats, name):
            raise CategoryRefused(
                R_NAME_TAKEN,
                f"this shop already has a category called {name!r}. Two lines "
                f"reading the same in a menu cannot be told apart.")
        parent_id = _read_parent(body, cats)
        order = _read_sort(body)
        if order is None:
            order = _next_sort(cats, parent_id)

        cat = Category(_new_category_id(), name, parent_id, order, _now_iso())
        cats.append(cat)
        save_book(cats, skus)
        head = _audit("category.created", category_id=cat.category_id,
                      name=cat.name, parent_id=cat.parent_id,
                      sort_order=int(cat.sort_order))
        # A category that did not exist a moment ago holds no products and has
        # no children, so every count in its view is zero without asking the
        # catalogue for them.
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "category": _category_view(cat, cats, {}),
            "audited": head is not None,
        })
    except CategoryRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/categories/assign")
async def categories_assign_ep(request: Request) -> JSONResponse:
    """File several products at once. This is where an accepted proposal lands.

    Body: {"assign": [{"sku_id": "...", "category_id": "..." or null}, ...]}.
    A null category id takes a product out of its category without touching the
    product.

    ALL OR NOTHING. Every line is checked before any line is written, so a list
    with one unknown sku in it changes nothing rather than filing the first
    half. A partial accept is the worst outcome here: the shopkeeper cannot see
    where it stopped.
    """
    try:
        body = await _json_body(request)
        rows = body.get("assign")
        if not isinstance(rows, list) or not rows:
            raise CategoryRefused(
                R_BAD_ASSIGNMENT,
                "'assign' must be a non-empty list of {sku_id, category_id}. "
                "Nothing was changed.")
        if len(rows) > MAX_ASSIGN:
            raise CategoryRefused(
                R_TOO_MANY_ASSIGNMENTS,
                f"that is {len(rows)} products in one request and the cap is "
                f"{MAX_ASSIGN}. Nothing was changed.")

        cats, skus = load_book()
        known = catalogue()

        planned: list[tuple[str, Optional[str]]] = []
        for raw in rows:
            if not isinstance(raw, dict):
                raise CategoryRefused(
                    R_BAD_ASSIGNMENT,
                    f"every line must be an object with a sku_id; found "
                    f"{type(raw).__name__}. Nothing was changed.")
            sku = _known_sku(raw.get("sku_id"), known)
            cid = raw.get("category_id")
            if cid is None or (isinstance(cid, str) and not cid.strip()):
                planned.append((sku, None))
                continue
            planned.append((sku, _find_category(cats, cid).category_id))

        changed = 0
        unchanged = 0
        removed = 0
        for sku, cid in planned:
            rec = dict(skus.get(sku) or {"category_id": None, "tags": []})
            if rec.get("category_id") == cid:
                unchanged += 1
                continue
            rec["category_id"] = cid
            skus[sku] = rec
            changed += 1
            if cid is None:
                removed += 1
        # A product with no category and no tags carries no filing at all, so it
        # is dropped from the sidecar rather than written as an empty row.
        for sku in [s for s, r in skus.items()
                    if not r.get("category_id") and not r.get("tags")]:
            skus.pop(sku, None)

        save_book(cats, skus)
        head = _audit("categories.assigned", products=len(planned),
                      changed=changed, uncategorised=removed)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "considered": len(planned),
            "changed": changed,
            "unchanged": unchanged,
            "uncategorised": removed,
            "audited": head is not None,
            "note": ("Filing only. No product, price or taught vector was "
                     "touched."),
        })
    except CategoryRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.put("/categories/sku/{sku_id}")
async def categories_sku_ep(sku_id: str, request: Request) -> JSONResponse:
    """File ONE product and set its tags. Body: {category_id?, tags?}.

    Send `category_id: null` to take it out of its category. Send `tags: []` to
    clear its tags. A body with neither key is refused rather than treated as
    "clear everything", because an empty body is far more often a bug in the
    page than an instruction.
    """
    try:
        body = await _json_body(request)
        has_category = "category_id" in body
        has_tags = "tags" in body
        if not has_category and not has_tags:
            raise CategoryRefused(
                R_NOTHING_TO_CHANGE,
                "this request names neither a category nor tags, so there is "
                "nothing to change. Nothing was saved.")

        cats, skus = load_book()
        known = catalogue()
        sku = _known_sku(sku_id, known)
        rec = dict(skus.get(sku) or {"category_id": None, "tags": []})
        was_category = rec.get("category_id")
        was_tags = list(rec.get("tags") or [])

        if has_category:
            cid = body.get("category_id")
            if cid is None or (isinstance(cid, str) and not cid.strip()):
                rec["category_id"] = None
            else:
                rec["category_id"] = _find_category(cats, cid).category_id
        if has_tags:
            rec["tags"] = _clean_tags(body.get("tags"))

        if rec.get("category_id") == was_category \
                and list(rec.get("tags") or []) == was_tags:
            raise CategoryRefused(
                R_NOTHING_TO_CHANGE,
                f"{sku!r} is already filed exactly that way. Nothing was "
                f"changed.")

        if rec.get("category_id") or rec.get("tags"):
            skus[sku] = rec
        else:
            skus.pop(sku, None)
        save_book(cats, skus)
        head = _audit("sku.filed", sku_id=sku,
                      **{"from": was_category, "to": rec.get("category_id")},
                      tags=list(rec.get("tags") or []))

        known_cats = _by_id(cats)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "product": _product_view(sku, known[sku], rec, known_cats),
            "was_category_id": was_category,
            "audited": head is not None,
        })
    except CategoryRefused as exc:
        return _refusal(
            exc, status=404 if exc.reason in (R_UNKNOWN_SKU, R_NO_CATEGORY)
            else 400)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.patch("/categories/{category_id}")
async def categories_edit_ep(category_id: str, request: Request) -> JSONResponse:
    """Rename a category, move it, or change its place in the menu.

    Body: any of {name, parent_id, sort_order}. The id never changes, so every
    product filed under it stays filed under it — that is the whole reason the
    id is not derived from the name.
    """
    try:
        body = await _json_body(request)
        cats, skus = load_book()
        cat = _find_category(cats, category_id)

        name = _read_name(body, required=False)
        order = _read_sort(body)
        parent: Optional[str] = cat.parent_id
        if "parent_id" in body:
            parent = _read_parent(body, cats, self_id=cat.category_id)

        if name is None and order is None and "parent_id" not in body:
            raise CategoryRefused(
                R_NOTHING_TO_CHANGE,
                "this request changes nothing. Send a name, a parent_id or a "
                "sort_order.")
        if name is not None and _name_taken(cats, name,
                                            except_id=cat.category_id):
            raise CategoryRefused(
                R_NAME_TAKEN,
                f"this shop already has a category called {name!r}. Nothing "
                f"was changed.")

        updated = Category(
            cat.category_id,
            name if name is not None else cat.name,
            parent,
            order if order is not None else cat.sort_order,
            cat.created_at,
        )
        if updated == cat:
            raise CategoryRefused(
                R_NOTHING_TO_CHANGE,
                f"{cat.name!r} is already exactly that. Nothing was changed.")
        cats = [updated if c.category_id == cat.category_id else c
                for c in cats]
        save_book(cats, skus)
        head = _audit("category.edited", category_id=cat.category_id,
                      **{"from": cat.name, "to": updated.name},
                      parent_id=updated.parent_id,
                      sort_order=int(updated.sort_order))

        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "category": _category_view(updated, cats,
                                       _counts_after_a_write(skus)),
            "was": cat.as_dict(),
            "audited": head is not None,
        })
    except CategoryRefused as exc:
        return _refusal(exc, status=404 if exc.reason == R_NO_CATEGORY else 400)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.delete("/categories/{category_id}")
def categories_delete_ep(category_id: str) -> JSONResponse:
    """Remove a category. NO PRODUCT IS DELETED WITH IT.

    Everything filed under it becomes uncategorised, and the response says how
    many did. Any category inside it is promoted to the top level, and the
    response says how many were — losing a shelf label must never lose the
    shelves under it, and it must never lose stock.

    Tags are untouched: they belong to the product, not to the category.
    """
    try:
        cats, skus = load_book()
        cat = _find_category(cats, category_id)

        children = [c for c in cats if c.parent_id == cat.category_id]
        promoted = [Category(c.category_id, c.name, None, c.sort_order,
                             c.created_at) for c in children]
        promoted_by_id = {c.category_id: c for c in promoted}
        kept: list[Category] = []
        for c in cats:
            if c.category_id == cat.category_id:
                continue
            kept.append(promoted_by_id.get(c.category_id, c))

        emptied = 0
        for sku_id in list(skus):
            rec = skus[sku_id]
            if rec.get("category_id") != cat.category_id:
                continue
            emptied += 1
            if rec.get("tags"):
                skus[sku_id] = {"category_id": None,
                                "tags": list(rec.get("tags") or [])}
            else:
                skus.pop(sku_id, None)

        save_book(kept, skus)
        head = _audit("category.deleted", category_id=cat.category_id,
                      name=cat.name, uncategorised=emptied,
                      children_promoted=len(promoted))
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "category_id": cat.category_id,
            "removed": cat.name,
            "uncategorised": emptied,
            "children_promoted": len(promoted),
            "promoted": [c.category_id for c in promoted],
            "products_deleted": 0,
            "audited": head is not None,
            "note": (f"{emptied} product(s) are now uncategorised and every one "
                     f"of them is still in the catalogue at the same price. "
                     f"Deleting a category deletes filing, not stock."),
        })
    except CategoryRefused as exc:
        return _refusal(exc, status=404 if exc.reason == R_NO_CATEGORY else 400)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


__all__ = [
    "CATEGORIES_FILENAME",
    "CATEGORIES_FORMAT",
    "CATEGORY_ID_RE",
    "Category",
    "CategoryRefused",
    "MAX_ASSIGN",
    "MAX_CATEGORIES",
    "MAX_NAME",
    "MAX_TAG",
    "MAX_TAGS_PER_SKU",
    "NO_CATEGORY",
    "RULES",
    "SUGGESTED_NAMES",
    "audit_path",
    "catalogue",
    "categories_path",
    "clean_tag",
    "load_book",
    "router",
    "save_book",
    "set_categories_path",
    "shop_dir",
    "suggest_for_name",
]
