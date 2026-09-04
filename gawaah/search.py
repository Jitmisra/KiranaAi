"""KHOJ — one box that finds anything this counter already knows.

A shopkeeper looking for something does not know which screen it is on. The
packet is in Products, the customer who rang about it is in Orders, the bill
they paid is in History, and "everything I taught from a photograph" is not on
any screen at all. This module answers all four from one query string, ranks
the answers together, and hands each one the hash route that opens it.

WHAT IS SEARCHED, AND WITH WHAT TOLERANCE
-----------------------------------------

  products   name, sku id, and every printed code bound to it.
             The NAME is typo-tolerant; the sku id and the codes are not.
             A barcode that is one digit out is a DIFFERENT barcode, and
             offering the wrong packet for a mistyped EAN-13 is how a bill
             goes wrong quietly. Ids and codes match exactly, by prefix, or
             not at all.

  orders     order id, the customer's name (typo-tolerant), their phone
             number, and the order total.
             NOT the delivery address. Search runs on every keystroke, and a
             box that spills somebody's doorstep onto the screen for a
             two-letter query is a different product from the one asked for.
             An address is visible on the order itself, where the shopkeeper
             went looking for it deliberately.

  bills      session id and amount. Not the line items: `gawaah/manage.py`
             already rebuilds a bill line by line, and a second, weaker
             re-derivation here would be a second answer to a question that
             already has one.

  categories DERIVED FACETS, NOT A FIELD ANYONE TYPED. This shop's catalogue
             has no category column — nothing in `gawaah/shop_store.py`
             stores one — so inventing "Snacks" and "Soaps" would be this
             program guessing and printing the guess. What IS true of every
             product is HOW it was taught, whether an offer is on it, and
             whether a code is bound to it; what is true of orders and bills
             is their state. Those are real groups with real counts, so those
             are the categories, and each one says what it was derived from.

TYPO TOLERANCE, EXACTLY
-----------------------

`edit_distance` below is Damerau-Levenshtein with the optimal-string-alignment
rule — insert, delete, substitute, and swap two neighbours — bounded, so it
abandons a comparison the moment no path can come in under the budget. It is
forty lines and it depends on nothing. The budget is by word length:

    up to 3 letters   0 edits   "dal" and "dahi" are different products
    4 to 6 letters    1 edit    "maggi" finds "maggie"
    7 or more         2 edits   "colgatte" finds "Colgate"

Three letters get no tolerance on purpose: at that length one edit reaches
most of the short words in any catalogue, and a search that returns everything
has answered nothing.

NATIVE SCRIPT, AND THE ONE THING IT COSTS
-----------------------------------------

The counter's microphone runs at `hi-IN` and hands back Devanagari, so a
shopkeeper who says the latin brand name printed on the packet gets "पॉन्ड्स"
where the catalogue says "ponds". No table can hold that pairing — the shop
invented the name — so `romanise` below spells the word out letter by letter
and the ordinary passes run again on the result. It is a SECOND pass and never
a replacement: a query is scored as typed first, and the romanised one is
looked at only for a candidate that scored nothing at all, damped by
`W_ROMANISED` so a name that IS what was typed always wins. Every `why` says
so, in the shopkeeper's words and with the latin spelling that got there.

WHAT THIS COSTS, AND WHAT IT WOULD COST LATER
---------------------------------------------

A query is a LINEAR SCAN. Every product, order, bill and category is read off
disk, built into a candidate and scored; there is no index and nothing is
cached between requests. Measured end to end through the HTTP handler on this
machine, 1000 products in the till's own sidecar, median of nine runs:

    9.6 ms   one word that matches every product
    11.4 ms  two words that match nothing
    13.2 ms  one misspelt word — the fuzzy pass runs on every name
    15.6 ms  two words, one of them matching — the worst case measured

So this shop's 42 SKUs answer in well under a millisecond of scan, and even a
600-SKU kirana stays inside the 100 ms this is budgeted at. **At 10,000 SKUs
the same code costs about 150 ms per keystroke** — ten times the worst case
above — which is past the budget and would be felt as lag while somebody is
waiting to pay.

THE FIX AT THAT SIZE IS NOT A FASTER LOOP. It is an inverted token index built
when the catalogue is written and invalidated by the same write, with the
edit-distance pass run only over tokens sharing a prefix with the query. That
is a different module and it is deliberately not written: an index maintained
for a catalogue of forty-two products is a cache that can go stale for no
benefit. `tests/test_search.py` measures the figure above on every run and
fails if it drifts past the stated ceiling, and `/search/health` prints it, so
the day this catalogue outgrows the design it says so rather than just feeling
slow.

WHAT THIS MODULE NEVER DOES
---------------------------

  - It never writes. No catalogue, no order, no bill, no chain. Nothing here
    changes money or stock, so nothing here appends to an audit log either: a
    hash-chained record of every keystroke a shopkeeper types would be a
    privacy problem and a write amplification problem, and it would witness
    nothing, because a search decides nothing.
  - It never invents a group, a price, or a match it cannot explain. Every
    result carries `why` — the field that matched and how — in a shopkeeper's
    words.
  - It never raises a 500. Every failure has a name, and a source that cannot
    be read is reported as unavailable BY NAME while the others still answer,
    rather than turning one unreadable file into an empty search box.

The router carries NO prefix. Mount it bare:
`app.include_router(search.router)`.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .money import MoneyError, from_rupees_str, paise, to_rupees_str

router = APIRouter()


# --------------------------------------------------------------- refusals --
#
# Every one of these is a state this module can actually reach, and every one
# is covered by a test in tests/test_search.py. None is decorative.

R_NO_QUERY = "search_query_missing"
R_QUERY_TOO_LONG = "search_query_too_long"
R_BAD_LIMIT = "limit_not_a_whole_number"
R_LIMIT_RANGE = "limit_out_of_range"
R_BAD_KIND = "unknown_kind_to_search"
R_NOTHING_TO_SEARCH = "nothing_could_be_searched"
R_NO_TILL = "till_module_unavailable"
R_CATALOGUE_UNREADABLE = "catalogue_unreadable"
R_ORDERS_UNREADABLE = "orders_unreadable"
R_BILLS_UNREADABLE = "bills_unreadable"
R_INTERNAL = "search_internal_error"


# ------------------------------------------------------------------ caps --
#
# Every one of these bounds work done for a request that arrives on every
# keystroke. What it costs when they are wrong: a shopkeeper pasting a long
# string gets a refusal instead of an answer. That is a nuisance; an unbounded
# fuzzy scan on every keypress is a counter that stutters while somebody is
# waiting to pay.

MAX_QUERY = 120
DEFAULT_LIMIT = 12
MAX_LIMIT = 50
DEFAULT_RECENT = 8
MAX_RECENT = 50

#: How many of each kind `by_kind` carries. A busy counter rings up far more
#: bills than it takes orders, so a strictly newest-first list on a real shop
#: is eight till sessions and nothing else — true, and useless as a palette.
#: `items` stays strictly newest-first because that is what was asked for;
#: `by_kind` is the same rows bucketed, so a UI can show three of each without
#: re-deriving recency itself and getting a different answer.
RECENT_PER_KIND = 3

#: What the whole scan is budgeted at, end to end, on this catalogue.
BUDGET_MS = 100

#: Measured, not guessed: microseconds to read, build and score 1000 products,
#: end to end through the handler, for the worst query shape found — two words,
#: one of them fuzzy-matching, so the edit-distance pass runs on every name.
#: See the module docstring for the whole table.
#:
#: The CEILING is the bar `tests/test_search.py` enforces on every run. It is
#: four times the measurement on purpose: this is a wall-clock number on a
#: machine that may be building a UI in another window, and a timing test that
#: goes red on a busy laptop is a test people learn to ignore. Four times is
#: still far below the point where the design stops being right.
MEASURED_US_PER_1000_PRODUCTS = 15_600
CEILING_US_PER_1000_PRODUCTS = 62_400


# ------------------------------------------------------------- the scores --
#
# Integers, all of them, and integer arithmetic all the way to the sort. A
# score is not money, but the same reason applies: two runs of the same query
# on the same shop must rank identically, and a float mean of four field
# scores is a thing that can differ in the last bit between machines.

S_EXACT = 1000
S_PREFIX = 820
S_SUBSTRING = 620
S_TOKEN_EXACT = 700
S_TOKEN_PREFIX = 560
S_TOKEN_SUB = 420
S_FUZZY_1 = 300
S_FUZZY_2 = 200
S_AMOUNT_EXACT = 900
S_AMOUNT_PREFIX = 520

#: Per-field weights, as percentages. A name and a bound code are what a
#: shopkeeper actually types; an id is what a machine typed, so it ranks a
#: shade below on an equal match rather than being excluded.
W_NAME = 100
W_SKU = 92
W_CODE = 100
W_ORDER_ID = 96
W_CUSTOMER = 100
W_PHONE = 100
W_SESSION = 96
W_CATEGORY = 88

KIND_PRODUCT = "product"
KIND_ORDER = "order"
KIND_BILL = "bill"
KIND_CATEGORY = "category"
KINDS: tuple[str, ...] = (KIND_PRODUCT, KIND_ORDER, KIND_BILL, KIND_CATEGORY)

#: Ties only. Two things that scored the same are shown catalogue-first,
#: because a shopkeeper searching mid-sale is usually looking for a packet.
KIND_RANK = {KIND_PRODUCT: 0, KIND_ORDER: 1, KIND_BILL: 2, KIND_CATEGORY: 3}

#: How a product came to be known, in a shopkeeper's words. The keys are the
#: strings the till and the store already write; an unknown one is shown as
#: itself rather than relabelled into something that sounds tidier.
TAUGHT_LABELS = {
    "mat_measured": "on the printed mat",
    "appearance_only": "from a photograph",
    "appearance": "from a photograph",
    "product_code_only": "by its printed code",
}


class SearchRefused(Exception):
    """A named refusal with a reason a human can act on."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def _refusal(exc: SearchRefused, status: int = 400) -> JSONResponse:
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


# ============================================================================
# TEXT
# ============================================================================


_SPLIT = re.compile(r"[\W_]+", re.UNICODE)


def _norm(value: Any) -> str:
    """Lowercased, whitespace-collapsed. Nothing is transliterated.

    Devanagari survives this untouched, which is the point: a shop that taught
    "मैगी" must be able to find it by typing it. The romanised SECOND pass is
    built separately in `romanise_text` and only runs after this one has come
    back with nothing, so it can never take a native-script name away from the
    native-script query that names it exactly.
    """
    return " ".join(str(value or "").lower().split())


def _tokens(value: Any) -> list[str]:
    """Words. Splits on punctuation AND underscore, so `parle_g_200g` is three
    tokens and a search for `200g` finds it."""
    return [t for t in _SPLIT.split(_norm(value)) if t]


def _max_edits(token: str) -> int:
    """The typo budget for one word. See the module docstring for why 3 gets 0."""
    n = len(token)
    if n <= 3:
        return 0
    if n <= 6:
        return 1
    return 2


def edit_distance(a: str, b: str, max_d: int) -> int:
    """Damerau-Levenshtein (optimal string alignment), bounded by `max_d`.

    Returns the true distance when it is <= max_d, and `max_d + 1` when it is
    not — the caller only ever asks "is this within budget", so computing the
    exact distance of two unrelated words is work nobody reads.

    Bounded three ways, because this runs once per (query word, candidate
    word) pair and that product is the whole cost of a search:
      - a length difference over the budget cannot be closed by any path;
      - only the diagonal band within max_d of the leading edge is filled;
      - a row whose every cell is already over budget ends it.

    A transposition costs 1, not 2. `magig` for `maggi` is one slip of two
    fingers, and charging it two edits puts the most common real typo outside
    a one-edit budget.
    """
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0 or lb == 0:
        return min(max(la, lb), max_d + 1)
    if abs(la - lb) > max_d:
        return max_d + 1
    if max_d <= 0:
        return 1

    over = max_d + 1
    prev2: Optional[list[int]] = None
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [over] * lb
        lo = max(1, i - max_d)
        hi = min(lb, i + max_d)
        for j in range(lo, hi + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if (prev2 is not None and i > 1 and j > 1
                    and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]):
                v = min(v, prev2[j - 2] + 1)
            cur[j] = v
        if min(cur) > max_d:
            return over
        prev2, prev = prev, cur
    return prev[lb] if prev[lb] <= max_d else over


# ============================================================================
# ROMANISATION — a native-script WORD spelt in latin letters
# ============================================================================
#
# WHY THIS IS NOT `SCRIPT_ALIASES` IN gawaah/assistant.py. That table is a
# SPELLING TABLE of the words a shopkeeper SAYS — question words, counts, units,
# the movement verbs. It is finite and it is hand-checked, and it deliberately
# holds no brand names, because a brand name is a catalogue lookup and not a
# translation.
#
# But the browser's speech recogniser runs at hi-IN and returns Devanagari, so a
# shopkeeper who says the latin brand name on the packet gets it back written in
# Devanagari: "ponds" comes back as "पॉन्ड्स". That word can never be in any
# fixed table, because the shop invented the catalogue name after this file was
# written. The only thing that can be done with it is to spell it out, letter by
# letter, in latin — which is what this does.
#
# WHAT IT IS. A character-level transliteration of the two abugidas the mic can
# return. It knows nothing about words, meanings, products or this shop. Give it
# `पॉन्ड्स` and it answers `ponds`; give it `बैंगन` and it answers `baingan`,
# which is a perfectly good spelling of a word this shop does not sell, and the
# lookup that called it then refuses by name. IT NEVER PICKS A PRODUCT. It hands
# back a string, and the caller compares that string with `edit_distance` above
# under the same budget every typed query already gets.
#
# HOW AN ABUGIDA WORKS, since the loop below is meaningless without it. A
# consonant letter carries an unwritten vowel of its own — "a" in Devanagari,
# "o" in Bengali. A vowel SIGN (a matra) written on that consonant replaces it;
# the virama (्, ্) deletes it and leaves a bare consonant. So पॉन्ड्स is
# प+ॉ (po) न+् (n) ड+् (d) स (sa) = "pondsa", and Hindi then drops the last
# unwritten vowel when it speaks the word, which is why the packet says "ponds".
# Both spellings are returned, unwritten-vowel-dropped first.
#
# WHAT IT CANNOT DO, written down rather than discovered later:
#
#   - It transliterates SOUND, so it cannot reproduce a doubled latin letter
#     that the native spelling does not have: मैगी is "maigi", not "maggi", and
#     आटा is "ata", not "atta". The typo budget covers the first — one edit on a
#     five-letter word — and NOT the second, because a three-letter word gets no
#     tolerance at all. A word that short, and any brand name with two such
#     differences in it, is not found and is refused by name.
#   - Only the WORD-FINAL unwritten vowel is dropped. Hindi also drops medial
#     ones ("नमकीन" is namkeen, this says "namakin"), and that rule needs a
#     pronunciation dictionary, which is exactly the kind of guess this module
#     does not make.
#   - Devanagari and Bengali only. Tamil, Telugu, Gurmukhi and Gujarati come
#     back from the same recogniser at a different `lang` and are not handled;
#     a word in one of those passes through unchanged and is refused by name.
#   - There is no reverse pass. A catalogue taught in Devanagari is found by
#     typing Devanagari — `_norm` leaves it alone, and that is deliberate — but
#     it is not found by typing latin.

#: The vowel every consonant carries when nothing is written on it. Different
#: per script because the sound is different: दश is "dash" and দশ is "dosh",
#: and it is the second one every Bengali romanisation in this repo uses.
_DEV_INHERENT = "a"
_BEN_INHERENT = "o"

#: Devanagari. The values are the spellings this repo's own Hinglish already
#: uses (ज़ -> z as in "pyaz", ड़ -> d as in "jhadu"), so a romanised word lands
#: in the same alphabet the catalogue names and `ALIASES` are written in.
_DEV_CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ng",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n", "ऩ": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ऱ": "r", "ल": "l", "ळ": "l", "ऴ": "l",
    "व": "v", "श": "sh", "ष": "sh", "स": "s", "ह": "h",
    "ॹ": "z", "ॺ": "y", "ॻ": "g", "ॼ": "j", "ॾ": "d", "ॿ": "b",
}
_DEV_NUKTA = {"क": "k", "ख": "kh", "ग": "g", "ज": "z", "ड": "d", "ढ": "dh",
              "फ": "f", "य": "y", "र": "r", "न": "n"}
_DEV_VOWELS = {
    "ऄ": "a", "अ": "a", "आ": "a", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u",
    "ऋ": "ri", "ऌ": "li", "ऍ": "e", "ऎ": "e", "ए": "e", "ऐ": "ai",
    "ऑ": "o", "ऒ": "o", "ओ": "o", "औ": "au", "ॠ": "ri", "ॡ": "li",
    "ॲ": "a", "ॳ": "a", "ॴ": "a", "ॵ": "o", "ॶ": "u", "ॷ": "u",
}
_DEV_MATRAS = {
    "ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "ृ": "ri", "ॄ": "ri",
    "ॅ": "e", "ॆ": "e", "े": "e", "ै": "ai", "ॉ": "o", "ॊ": "o", "ो": "o",
    "ौ": "au", "ॎ": "e", "ॏ": "au", "ॢ": "li", "ॣ": "li",
}
#: Anusvara and candrabindu are a nasal; visarga is an "h". They sit AFTER the
#: vowel they colour, so they are emitted as their own piece rather than folded
#: into one.
_DEV_SIGNS = {"ँ": "n", "ं": "n", "ः": "h", "ॐ": "om", "ऽ": ""}

#: Bengali. Same shape, and the same rule about matching this repo's spellings
#: (য় -> y as in "hoyeche", ড় -> d as in "jhadu").
_BEN_CONSONANTS = {
    "ক": "k", "খ": "kh", "গ": "g", "ঘ": "gh", "ঙ": "ng",
    "চ": "ch", "ছ": "chh", "জ": "j", "ঝ": "jh", "ঞ": "n",
    "ট": "t", "ঠ": "th", "ড": "d", "ঢ": "dh", "ণ": "n",
    "ত": "t", "থ": "th", "দ": "d", "ধ": "dh", "ন": "n",
    "প": "p", "ফ": "ph", "ব": "b", "ভ": "bh", "ম": "m",
    "য": "j", "র": "r", "ল": "l",
    "শ": "sh", "ষ": "sh", "স": "s", "হ": "h", "ৰ": "r", "ৱ": "w",
}
_BEN_NUKTA = {"ড": "d", "ঢ": "dh", "য": "y", "ব": "b", "র": "r"}
_BEN_VOWELS = {
    "অ": "o", "আ": "a", "ই": "i", "ঈ": "i", "উ": "u", "ঊ": "u",
    "ঋ": "ri", "ঌ": "li", "এ": "e", "ঐ": "oi", "ও": "o", "ঔ": "ou",
    "ৠ": "ri", "ৡ": "li",
}
_BEN_MATRAS = {
    "া": "a", "ি": "i", "ী": "i", "ু": "u", "ূ": "u", "ৃ": "ri", "ৄ": "ri",
    "ে": "e", "ৈ": "oi", "ো": "o", "ৌ": "ou", "ৢ": "li", "ৣ": "li",
}
_BEN_SIGNS = {"ঁ": "n", "ং": "ng", "ঃ": "h", "ৎ": "t", "ঽ": ""}

#: A consonant that is spelt differently when it is the SECOND half of a
#: conjunct. Bengali য is "j" on its own (যা, "ja") and "y" when it hangs off
#: the letter before it (প্যাকেট, "pyaket") — the ya-phala, which nobody writes
#: with a j. Devanagari has no letter that changes this way, so its table is
#: empty and the rule costs it nothing.
_DEV_POST_VIRAMA: dict[str, str] = {}
_BEN_POST_VIRAMA = {"য": "y"}

_DEV_VIRAMA = "्"
_DEV_NUKTA_MARK = "़"
_BEN_VIRAMA = "্"
_BEN_NUKTA_MARK = "়"

#: (inherent, consonants, nukta forms, post-virama forms, independent vowels,
#: matras, signs, virama, nukta mark). Two entries, and the block a character
#: falls in picks one — there is no language detection and nothing to get wrong.
_ALPHABETS = (
    ("ऀ", "ॿ", (_DEV_INHERENT, _DEV_CONSONANTS, _DEV_NUKTA, _DEV_POST_VIRAMA,
                _DEV_VOWELS, _DEV_MATRAS, _DEV_SIGNS,
                _DEV_VIRAMA, _DEV_NUKTA_MARK)),
    ("ঀ", "৿", (_BEN_INHERENT, _BEN_CONSONANTS, _BEN_NUKTA, _BEN_POST_VIRAMA,
                _BEN_VOWELS, _BEN_MATRAS, _BEN_SIGNS,
                _BEN_VIRAMA, _BEN_NUKTA_MARK)),
)

_NATIVE = re.compile(r"[ऀ-ॿঀ-৿]")

#: `_SPLIT` above is `[\W_]+`, and a Devanagari virama is not a `\w` character —
#: so it shreds "पॉन्ड्स" into four letters. That is fine for the token pass,
#: which is not what finds a native-script name, but it is useless here: a word
#: has to arrive at `romanise` WHOLE or the abugida cannot be read. This is the
#: same rule `normalise` in gawaah/assistant.py tokenises with.
_ROMAN_SPLIT = re.compile(r"[A-Za-z0-9]+|[ऀ-ॿ]+|[ঀ-৿]+")


def _alphabet(word: str):
    """Which of the two scripts this word is in, or None if it is neither.

    The FIRST native character decides. A word that mixes the two blocks is not
    a word anybody typed; taking the first letter's script gives a deterministic
    answer for it rather than a half-transliterated one.
    """
    for ch in word:
        for lo, hi, alpha in _ALPHABETS:
            if lo <= ch <= hi:
                return alpha
    return None


def romanise(word: str) -> tuple[str, ...]:
    """Every latin spelling of one native-script word, best first.

    Returns () for a word with no Devanagari or Bengali in it — INCLUDING every
    pure-ASCII word, which is the property that lets a caller run this pass
    without changing what a typed latin query does.

    One or two spellings come back. The first has the word-final unwritten vowel
    dropped, the way the word is actually said (पॉन्ड्स -> "ponds"); the second
    keeps it (-> "pondsa"), for the names where it is really pronounced
    ("योग"/"yoga"). Both are transliterations of the same letters. Neither is a
    guess about which product was meant — that decision belongs to the caller,
    and it makes it by comparing these strings with the catalogue.
    """
    # NFC first, for the same reason `SCRIPT_ALIASES` is NFC: a nukta letter has
    # a precomposed form (क़ U+0958) and a decomposed one (क + ़), they look
    # identical on screen, and which one arrives depends on the keyboard. NFC
    # DECOMPOSES them — they are Unicode composition exclusions — so the loop
    # below only ever has to know the two-character spelling. The zero-width
    # joiners a phone keyboard writes inside a conjunct go too: they are
    # invisible, they carry no sound, and left in they sit between a virama and
    # the letter it belongs to and break the pair apart.
    word = unicodedata.normalize("NFC", word or "").replace(
        "‌", "").replace("‍", "")
    alpha = _alphabet(word)
    if alpha is None:
        return ()
    (inherent, cons, nukta, post_virama, vowels, matras, signs, virama,
     nukta_mark) = alpha

    # Each unit is [letters, vowel, the vowel is the unwritten one].
    units: list[list[Any]] = []
    conjunct = False
    i, n = 0, len(word)
    while i < n:
        ch = word[i]
        nukta_next = i + 1 < n and word[i + 1] == nukta_mark
        letter = (nukta.get(ch) if nukta_next else None) or (
            post_virama.get(ch) if conjunct else None) or cons.get(ch)
        conjunct = False
        if letter is not None:
            units.append([letter, inherent, True])
            i += 2 if nukta_next and ch in nukta else 1
            continue
        if ch == virama:
            if units:
                units[-1][1] = ""
                units[-1][2] = False
            conjunct = True
            i += 1
            continue
        if ch in matras:
            if units:
                units[-1][1] = matras[ch]
                units[-1][2] = False
            else:
                units.append(["", matras[ch], False])
            i += 1
            continue
        if ch in vowels:
            units.append(["", vowels[ch], False])
            i += 1
            continue
        if ch in signs:
            units.append([signs[ch], "", False])
            i += 1
            continue
        # A stray combining mark, a digit, an accent, a latin letter inside a
        # native word: carried through if it is alphanumeric, dropped if not.
        # Dropping something unrecognised is right here — the alternative is a
        # spelling with a character no catalogue name can contain.
        if ch.isascii() and ch.isalnum():
            units.append([ch.lower(), "", False])
        i += 1

    if not units:
        return ()
    kept = "".join(f"{u[0]}{u[1]}" for u in units)
    dropped = kept
    if units[-1][2]:
        dropped = "".join(f"{u[0]}{u[1]}" for u in units[:-1]) + units[-1][0]
    # Order kept, duplicates and empties gone: a word whose last letter carries
    # a written vowel has one spelling, not the same one twice.
    return tuple(dict.fromkeys(s for s in (dropped, kept) if s))


def romanise_text(text: str) -> str:
    """`text` with every native-script word respelt in latin, or "" when there
    was no native-script word in it at all.

    THE EMPTY STRING IS THE CONTRACT. A caller runs its ordinary pass, and only
    if that found nothing does it run a second one on this — so a query with no
    Devanagari or Bengali in it never reaches a line of the code above, and
    cannot be scored, ranked or matched one bit differently than before this
    existed.
    """
    if not text or not _NATIVE.search(text):
        return ""
    out: list[str] = []
    for part in _ROMAN_SPLIT.findall(text):
        spellings = romanise(part)
        out.append(spellings[0] if spellings else part.lower())
    return " ".join(p for p in out if p)


#: How much a match found only by transliteration is damped, as a percentage. A
#: product whose name IS what was typed must always outrank one that had to be
#: respelt to get there, even when both landed on the same kind of match.
W_ROMANISED = 90


def _score_text(qn: str, q_tokens: list[str], text: Any, *, fuzzy: bool
                ) -> tuple[int, str]:
    """How well one field answers the query, and — in words — why.

    EVERY WORD OF THE QUERY HAS TO LAND SOMEWHERE. "parle biscuit" does not
    match a product called "Parle-G" unless "biscuit" is in it too. Scoring the
    best word and ignoring the rest is what makes a search box feel like it is
    guessing, and the returned score is the MEAN of the per-word bests so that
    a field where every word matched weakly can still lose to one where the
    whole phrase matched exactly.
    """
    tn = _norm(text)
    if not tn or not qn:
        return 0, ""
    if tn == qn:
        return S_EXACT, "is exactly what you typed"
    if tn.startswith(qn):
        return S_PREFIX, "starts with what you typed"
    if len(qn) >= 3 and qn in tn:
        return S_SUBSTRING, "contains what you typed"

    t_tokens = _tokens(tn)
    if not t_tokens or not q_tokens:
        return 0, ""

    points = 0
    weakest = S_EXACT + 1
    weakest_why = ""
    for qt in q_tokens:
        best = 0
        best_why = ""
        cap = _max_edits(qt) if fuzzy else 0
        for tt in t_tokens:
            if tt == qt:
                s, why = S_TOKEN_EXACT, "matches a word in it"
            elif len(qt) >= 2 and tt.startswith(qt):
                s, why = S_TOKEN_PREFIX, "has a word starting with that"
            elif len(qt) >= 3 and qt in tt:
                s, why = S_TOKEN_SUB, "has a word containing that"
            elif cap > 0:
                d = edit_distance(qt, tt, cap)
                if d > cap:
                    continue
                s = S_FUZZY_1 if d == 1 else S_FUZZY_2
                why = ("is one letter off what you typed" if d == 1
                       else f"is {d} letters off what you typed")
            else:
                continue
            if s > best:
                best, best_why = s, why
        if best == 0:
            return 0, ""
        points += best
        if best < weakest:
            weakest, weakest_why = best, best_why
    return points // len(q_tokens), weakest_why


_AMOUNT_RE = re.compile(r"^(?:rs\.?|inr|₹)?\s*([0-9]{1,7})(?:\.([0-9]{1,2}))?$")


def _query_amount(q: str) -> tuple[Optional[int], str]:
    """The query read as a rupee amount, in PAISE, plus the digits as typed.

    `from_rupees_str` and never a float: '139.50' is 13950 paise exactly, and
    float('139.50') is already the wrong number before anything is compared.
    Returns (None, "") when the query is not an amount at all.
    """
    m = _AMOUNT_RE.match(q.strip().lower().replace(",", ""))
    if m is None:
        return None, ""
    whole, frac = m.group(1), m.group(2)
    as_text = whole if frac is None else f"{whole}.{frac}"
    try:
        return int(from_rupees_str(as_text)), as_text
    except MoneyError:
        return None, ""


# ============================================================================
# CANDIDATES
# ============================================================================


@dataclass
class Candidate:
    """One thing that could be found, and everything it can be found by.

    `doc` is what goes back to the browser. `fields` is what is searched and
    never leaves this module, so a phone number is matched without a query
    that missed it being echoed anything.
    """

    kind: str
    ident: str
    doc: dict[str, Any]
    #: (label, text, weight, fuzzy_allowed)
    fields: list[tuple[str, str, int, bool]] = field(default_factory=list)
    #: Integer paise, when this thing has an amount. Compared numerically.
    amount_paise: Optional[int] = None
    #: ISO-8601, for newest-first tie-breaking and for /search/recent.
    at: str = ""
    #: 0 for the ordinary case, 1 for something a shopkeeper probably did not
    #: mean (a session that never became a bill). Ties only, never a filter.
    tier: int = 0


def _match(cand: Candidate, qn: str, q_tokens: list[str],
           want_paise: Optional[int], digits: str,
           rn: str = "", r_tokens: Optional[list[str]] = None
           ) -> tuple[int, str]:
    """The best score any of this candidate's fields gives, and why.

    `rn` is the query respelt in latin letters and is "" for every query that
    had no Devanagari or Bengali in it — see `romanise_text`. It is scored ONLY
    when the query as typed scored nothing at all, so a catalogue taught in
    native script still answers its own script first and a latin query never
    reaches this code at all.
    """
    best = 0
    why = ""
    for label, text, weight, fuzzy in cand.fields:
        s, reason = _score_text(qn, q_tokens, text, fuzzy=fuzzy)
        if s <= 0:
            continue
        s = (s * weight) // 100
        if s > best:
            best, why = s, f"the {label} {reason}"

    if best == 0 and rn:
        for label, text, weight, fuzzy in cand.fields:
            s, reason = _score_text(rn, r_tokens or [], text, fuzzy=fuzzy)
            if s <= 0:
                continue
            s = ((s * weight) // 100 * W_ROMANISED) // 100
            if s > best:
                best, why = s, (f"the {label} {reason} — {qn!r} spelt in "
                                f"latin letters is {rn!r}")

    if cand.amount_paise is not None and want_paise is not None:
        shown = to_rupees_str(int(paise(cand.amount_paise)))
        if int(cand.amount_paise) == want_paise:
            if S_AMOUNT_EXACT > best:
                best, why = S_AMOUNT_EXACT, f"it comes to exactly Rs {shown}"
        elif len(digits) >= 2 and shown.startswith(digits):
            if S_AMOUNT_PREFIX > best:
                best, why = S_AMOUNT_PREFIX, f"it comes to Rs {shown}"
    return best, why


def _route(screen: str, **params: str) -> str:
    """The hash route that opens this thing.

    `#/products?sku=parle_g_200g`, and the shell's own `routeFromHash` splits
    on the first '?' — so the SCREEN is always reached even by a build that
    does not read the parameter yet. The parameter is a request to the screen,
    not a promise from this module: `screen` is returned beside `route` so a
    caller can navigate without one.
    """
    if not params:
        return f"#/{screen}"
    q = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}"
                 for k, v in sorted(params.items()))
    return f"#/{screen}?{q}"


# ============================================================================
# WHERE THE DATA COMES FROM
# ============================================================================
#
# Four sources, each loaded independently and each allowed to fail on its own.
# A corrupt orders file must not empty the search box for products — but it
# must never be silent either, so an unreadable source is reported BY NAME in
# `sources` and the answer is flagged `partial`.


@dataclass
class SourceState:
    name: str
    available: bool = True
    #: Readable, but not all of it. A hash chain that stops verifying half way
    #: is the case this exists for: what came back is true, and it is not
    #: everything, and those are different claims from "unavailable".
    complete: bool = True
    reason: Optional[str] = None
    detail: Optional[str] = None
    scanned: int = 0

    def json(self) -> dict[str, Any]:
        return {"available": self.available, "complete": self.complete,
                "reason": self.reason, "detail": self.detail,
                "scanned": self.scanned}


def _down(name: str, reason: str, detail: str) -> SourceState:
    return SourceState(name=name, available=False, reason=reason, detail=detail)


def _partial(states: dict[str, SourceState]) -> bool:
    """Is this answer short of what the shop actually holds?

    Two ways it can be, and both count: a source that could not be read at all,
    and one that was read only as far as it verified. A page that showed the
    second as a complete answer would be a shopkeeper told there are no bills
    matching, when the matching one is on the far side of a chain break.
    """
    return not all(s.available and s.complete for s in states.values())


from gawaah import till_ref as _till_ref

#: One definition, in `gawaah/till_ref.py`. This was sixteen copies of a tuple
#: that was missing `__main__`, and every one of them was wrong the same way.
_TILL_NAMES = _till_ref.TILL_NAMES


def _till() -> Any:
    """The already-loaded till module, or a named refusal.

    LOOK IN sys.modules FIRST, AND DO NOT SKIP THAT STEP — the same reason
    `gawaah/storefront.py` gives at length. `make serve` runs
    `uvicorn upload_app:app --app-dir tools`, so the module is registered under
    the bare name `upload_app`; the tests do `from tools import upload_app` and
    register it as `tools.upload_app`. Importing the other spelling loads a
    SECOND copy of the file with its own store handle, and a `set_store_dir` in
    a test would then silently not reach the copy serving requests. The symptom
    would be a search reading a different shop from the till it is mounted in,
    with nothing anywhere saying so.
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
        raise SearchRefused(
            R_NO_TILL,
            f"tools/upload_app.py is not importable ({type(exc).__name__}: "
            f"{exc}). Search reads the shopkeeper's own catalogue through it "
            f"and will not keep a second copy of it.") from None
    return upload_app


def shop_dir() -> Path:
    """Where the catalogue lives — the till's own answer, never a second one.

    This is what honours `GAWAAH_SHOP_DIR`: `upload_app.store_dir()` reads that
    environment variable and `upload_app.set_store_dir()` redirects it for a
    test. Deriving it here from the environment would be a second answer to one
    question, and a harness that answered it differently once destroyed the
    live catalogue in results/.
    """
    return Path(_till().store_dir())


def _codes_by_sku() -> dict[str, list[str]]:
    """{sku id -> the printed codes bound to it}.

    Through the till's own reader when it has one: that function honours
    `store_dir()` and treats a corrupt bindings file as no bindings rather
    than an outage, which is exactly the behaviour wanted here. The direct
    read is the fallback for the day it is renamed — same file, same format
    check, so the two cannot disagree about what a binding is.
    """
    try:
        up = _till()
    except SearchRefused:
        return {}
    raw: dict[str, str] = {}
    reader = getattr(up, "_codes_load", None)
    if callable(reader):
        try:
            raw = dict(reader())
        except Exception:  # noqa: BLE001 - a bindings file is not the search
            raw = {}
    if not raw:
        try:
            data = json.loads(
                (Path(up.store_dir()) / "product_codes.json").read_text(
                    encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("codes"), dict):
                raw = {str(k): str(v) for k, v in data["codes"].items()
                       if k and v}
        except Exception:  # noqa: BLE001 - absent or unreadable is "no codes"
            raw = {}
    by_sku: dict[str, list[str]] = {}
    for code, sku in raw.items():
        by_sku.setdefault(str(sku), []).append(str(code))
    for bound in by_sku.values():
        bound.sort()
    return by_sku


def load_products() -> tuple[list[Candidate], SourceState]:
    """Everything this shop can sell, as candidates.

    `offer_priced_skus()` and not `priced_skus()`, for the reason
    `gawaah/storefront.py` documents: paisa re-prices every basket through the
    offer book, so a screen quoting the marked price quotes a number the money
    service will refuse to mint. A search result showing Rs 35.00 for a packet
    that will ring up at Rs 31.50 is the same lie in a smaller box, so the
    charged price is what is shown and the marked one is shown beside it.
    """
    try:
        up = _till()
        rows = dict(up.offer_priced_skus())
    except SearchRefused as exc:
        return [], _down("products", exc.reason, exc.detail)
    except Exception as exc:  # noqa: BLE001 - one unreadable file, named
        return [], _down(
            "products", R_CATALOGUE_UNREADABLE,
            f"the catalogue could not be read ({type(exc).__name__}: {exc}). "
            f"Orders and bills below are unaffected.")

    codes = _codes_by_sku()
    out: list[Candidate] = []
    for sku_id, row in sorted(rows.items()):
        name = str(row.get("name") or sku_id)
        bound = codes.get(sku_id, [])
        how = str(row.get("how") or "unknown")
        try:
            price_paise = int(paise(row["price_paise"]))
        except (KeyError, TypeError, ValueError, MoneyError):
            # A row whose price is not integer paise is NOT rendered with a
            # guessed price. It stays findable — the shopkeeper needs to reach
            # it to fix it — and says it has no usable price.
            price_paise = None

        marked = row.get("marked_paise")
        off = row.get("off_paise")
        on_offer = isinstance(off, int) and not isinstance(off, bool) and off > 0

        bits = []
        if price_paise is None:
            bits.append("no usable price")
        else:
            bits.append(f"Rs {to_rupees_str(paise(price_paise))}")
        if on_offer and isinstance(marked, int):
            bits.append(f"was Rs {to_rupees_str(int(paise(marked)))}")
        bits.append(f"taught {TAUGHT_LABELS.get(how, how)}")
        if bound:
            bits.append(f"code {bound[0]}"
                        + (f" and {len(bound) - 1} more" if len(bound) > 1
                           else ""))

        doc: dict[str, Any] = {
            "type": KIND_PRODUCT,
            "id": sku_id,
            "title": name,
            "subtitle": " · ".join(bits),
            "sku_id": sku_id,
            "name": name,
            "price_paise": price_paise,
            "price_rupees": (None if price_paise is None
                             else to_rupees_str(paise(price_paise))),
            "on_offer": bool(on_offer),
            "taught_by": how,
            "taught_label": TAUGHT_LABELS.get(how, how),
            "codes": list(bound),
            "screen": "products",
            "route": _route("products", sku=sku_id),
        }
        cand = Candidate(kind=KIND_PRODUCT, ident=sku_id, doc=doc,
                         amount_paise=price_paise)
        cand.fields.append(("name", name, W_NAME, True))
        cand.fields.append(("sku id", sku_id, W_SKU, False))
        for code in bound:
            cand.fields.append(("bound code", code, W_CODE, False))
        out.append(cand)
    return out, SourceState("products", scanned=len(out))


def load_orders() -> tuple[list[Candidate], SourceState]:
    """Every order a customer placed, as candidates.

    Read through `gawaah/storefront.py`, which owns the orders directory and
    already skips a half-written file without losing the rest. A second reader
    here would be a second opinion about what an order is.
    """
    try:
        from . import storefront as _sf  # noqa: WPS433 - late: cv2 lives below
        rows = _sf._all_orders()
    except Exception as exc:  # noqa: BLE001 - named, and the rest still answers
        return [], _down(
            "orders", R_ORDERS_UNREADABLE,
            f"the orders could not be read ({type(exc).__name__}: {exc}). "
            f"Products and bills below are unaffected.")

    out: list[Candidate] = []
    for doc_in in rows:
        order_id = str(doc_in.get("order_id") or "")
        if not order_id:
            continue
        customer = doc_in.get("customer")
        customer = customer if isinstance(customer, dict) else {}
        who = str(customer.get("name") or "")
        phone = str(customer.get("phone") or "")
        status = str(doc_in.get("status") or "")
        at = str(doc_in.get("at") or "")
        raw_total = doc_in.get("total_paise")
        try:
            total_paise = int(paise(raw_total))
        except (TypeError, ValueError, MoneyError):
            total_paise = None

        lines = doc_in.get("lines")
        n_lines = len(lines) if isinstance(lines, list) else 0
        bits = [who or "no name on this order"]
        if total_paise is not None:
            bits.append(f"Rs {to_rupees_str(paise(total_paise))}")
        bits.append(f"{n_lines} line{'' if n_lines == 1 else 's'}")
        bits.append(status.replace("_", " ") or "no status")

        doc: dict[str, Any] = {
            "type": KIND_ORDER,
            "id": order_id,
            "title": f"Order for {who}" if who else f"Order {order_id}",
            "subtitle": " · ".join(bits),
            "order_id": order_id,
            "customer_name": who,
            "status": status,
            "at": at,
            "total_paise": total_paise,
            "total_rupees": (None if total_paise is None
                             else to_rupees_str(paise(total_paise))),
            "paid": bool((doc_in.get("payment") or {}).get("paid"))
            if isinstance(doc_in.get("payment"), dict) else False,
            "screen": "orders",
            "route": _route("orders", order=order_id),
        }
        cand = Candidate(kind=KIND_ORDER, ident=order_id, doc=doc,
                         amount_paise=total_paise, at=at)
        cand.fields.append(("order id", order_id, W_ORDER_ID, False))
        if who:
            cand.fields.append(("customer's name", who, W_CUSTOMER, True))
        if phone:
            # Both spellings. A shopkeeper types '98765 43210' as often as
            # '9876543210', and the digits-only form is what makes either one
            # find the other.
            cand.fields.append(("phone number", phone, W_PHONE, False))
            digits = re.sub(r"\D", "", phone)
            if digits and digits != phone:
                cand.fields.append(("phone number", digits, W_PHONE, False))
        out.append(cand)
    return out, SourceState("orders", scanned=len(out))


def load_bills() -> tuple[list[Candidate], SourceState]:
    """Every session in the audit chain, as candidates.

    Through `gawaah/manage.py`, which rebuilds bills from the hash-chained log
    and stops at the first broken link. Sessions that never closed are INCLUDED
    and marked — a shopkeeper holding a session id off a customer's screenshot
    is owed the answer "that one never became a bill" rather than silence — but
    they tie-break below real bills, because they are almost never what was
    meant.
    """
    try:
        from . import manage as _mg  # noqa: WPS433 - late, same as the rest
        records, chain = _mg.read_chain()
        bills = _mg.bills_from(records)
    except Exception as exc:  # noqa: BLE001 - named, and the rest still answers
        return [], _down(
            "bills", R_BILLS_UNREADABLE,
            f"the audit chain could not be read ({type(exc).__name__}: {exc}). "
            f"Products and orders above are unaffected.")

    out: list[Candidate] = []
    for session_id, bill in bills.items():
        closed = bool(bill.get("closed"))
        at = str(bill.get("at") or bill.get("opened_at") or "")
        try:
            total_paise = int(paise(bill.get("total_paise") or 0))
        except (TypeError, ValueError, MoneyError):
            total_paise = None

        settled = bool(bill.get("settled"))
        bits = []
        if total_paise is not None:
            bits.append(f"Rs {to_rupees_str(paise(total_paise))}")
        n_lines = len(bill.get("line_items") or [])
        bits.append(f"{n_lines} line{'' if n_lines == 1 else 's'}")
        excluded = len(bill.get("excluded") or [])
        if excluded:
            bits.append(f"{excluded} excluded")
        if not closed:
            bits.append("never became a bill")
        else:
            bits.append("paid" if settled else "not paid")

        doc: dict[str, Any] = {
            "type": KIND_BILL,
            "id": session_id,
            "title": f"Bill {session_id}" if closed else f"Session {session_id}",
            "subtitle": " · ".join(bits),
            "session_id": session_id,
            "at": at,
            "total_paise": total_paise,
            "total_rupees": (None if total_paise is None
                             else to_rupees_str(paise(total_paise))),
            "closed": closed,
            "settled": settled,
            "state": bill.get("state"),
            "screen": "history",
            "route": _route("history", session=session_id),
        }
        cand = Candidate(kind=KIND_BILL, ident=session_id, doc=doc,
                         amount_paise=total_paise, at=at,
                         tier=0 if closed else 1)
        cand.fields.append(("session id", session_id, W_SESSION, False))
        out.append(cand)

    state = SourceState("bills", scanned=len(out))
    if not chain.get("ok", True):
        # The chain verified short. manage.py already truncated the records at
        # the break, so what is searchable here is only what stood up — and
        # saying so is the difference between a short answer and a wrong one.
        state.complete = False
        state.detail = (
            f"the audit chain does not verify past line "
            f"{chain.get('lines_verified')}: {chain.get('error')}. Only "
            f"sessions before the break are searchable.")
    return out, state


def build_categories(products: list[Candidate], orders: list[Candidate],
                     bills: list[Candidate]) -> list[Candidate]:
    """The groups that are TRUE of this shop, counted from what was just read.

    Not a taxonomy. This catalogue has no category field, so these are facets
    derived from data that exists: how a product was taught, whether an offer
    is on it, whether a code is bound to it, what state an order is in, and
    whether a bill was paid. Each one says what it was derived from, and one
    with nothing in it is not shown — a group of zero is a dead end, not an
    answer.
    """
    taught: dict[str, int] = {}
    on_offer = 0
    no_code = 0
    for c in products:
        taught[str(c.doc["taught_by"])] = taught.get(str(c.doc["taught_by"]), 0) + 1
        if c.doc.get("on_offer"):
            on_offer += 1
        if not c.doc.get("codes"):
            no_code += 1

    by_status: dict[str, int] = {}
    for c in orders:
        by_status[str(c.doc["status"])] = by_status.get(str(c.doc["status"]), 0) + 1

    paid = sum(1 for c in bills if c.doc.get("settled"))
    unpaid = sum(1 for c in bills if c.doc.get("closed") and not c.doc.get("settled"))

    specs: list[tuple[str, str, int, str, str, dict[str, str], str]] = []
    for how, count in sorted(taught.items()):
        label = TAUGHT_LABELS.get(how, how)
        specs.append((
            f"taught:{how}", f"Products taught {label}", count,
            f"{count} product{'' if count == 1 else 's'}",
            "products", {"taught": how},
            "how each product was taught, which the catalogue records per sku",
        ))
    specs.append((
        "offer:on", "Products on offer today", on_offer,
        f"{on_offer} product{'' if on_offer == 1 else 's'} discounted now",
        "offers", {},
        "the offer book, applied to the marked price",
    ))
    specs.append((
        "product:no_code", "Products with no printed code", no_code,
        f"{no_code} product{'' if no_code == 1 else 's'} nothing can scan",
        "products", {"code": "none"},
        "the code bindings file",
    ))
    for status, count in sorted(by_status.items()):
        pretty = status.replace("_", " ")
        specs.append((
            f"order:{status}", f"Orders {pretty}", count,
            f"{count} order{'' if count == 1 else 's'}",
            "orders", {"status": status},
            "the status on each order document",
        ))
    specs.append((
        "bill:paid", "Bills that were paid", paid,
        f"{paid} bill{'' if paid == 1 else 's'} settled",
        "history", {"settled": "yes"},
        "a signature-verified settlement on the audit chain",
    ))
    specs.append((
        "bill:unpaid", "Bills not paid", unpaid,
        f"{unpaid} bill{'' if unpaid == 1 else 's'} still open",
        "history", {"settled": "no"},
        "the absence of a settlement on the audit chain",
    ))

    out: list[Candidate] = []
    for key, label, count, subtitle, screen, params, derived in specs:
        if count <= 0:
            continue
        doc = {
            "type": KIND_CATEGORY,
            "id": key,
            "title": label,
            "subtitle": subtitle,
            "count": count,
            "derived_from": derived,
            "screen": screen,
            "route": _route(screen, **params),
        }
        cand = Candidate(kind=KIND_CATEGORY, ident=key, doc=doc)
        cand.fields.append(("group", label, W_CATEGORY, True))
        for word in _CATEGORY_WORDS.get(key, ()):
            cand.fields.append(("group", word, W_CATEGORY, True))
        out.append(cand)
    return out


#: The words a shopkeeper is likely to type for a group whose real label is
#: longer. Deliberately small: every synonym is a way for the wrong group to
#: come first, so only the ones somebody would actually type are here.
_CATEGORY_WORDS: dict[str, tuple[str, ...]] = {
    "taught:mat_measured": ("mat", "takhti", "measured", "millimetres"),
    "taught:appearance_only": ("photo", "photograph", "appearance", "by look"),
    "taught:appearance": ("photo", "photograph", "appearance", "by look"),
    "taught:product_code_only": ("code", "barcode", "ean", "code only"),
    "offer:on": ("offer", "offers", "discount", "sale", "deal"),
    "product:no_code": ("no code", "unbound", "no barcode"),
    "order:new": ("new orders", "just placed"),
    "order:preparing": ("preparing", "packing"),
    "order:out_for_delivery": ("out for delivery", "delivery", "rider"),
    "order:delivered": ("delivered", "done"),
    "order:cancelled": ("cancelled", "canceled"),
    "bill:paid": ("paid", "settled", "green"),
    "bill:unpaid": ("unpaid", "not paid", "pending", "open"),
}


# ============================================================================
# READING THE REQUEST
# ============================================================================


def _require_query(raw: Any) -> str:
    q = " ".join(str(raw or "").split())
    if not q:
        raise SearchRefused(
            R_NO_QUERY,
            "there is nothing to search for. Type a product name, a printed "
            "code, an order id, a customer's name, a phone number or an "
            "amount.")
    if len(q) > MAX_QUERY:
        raise SearchRefused(
            R_QUERY_TOO_LONG,
            f"that is {len(q)} characters and the cap is {MAX_QUERY}. Nothing "
            f"in this shop has a name that long — try the first few words.")
    return q


def _require_limit(raw: Any, *, default: int, ceiling: int) -> int:
    if raw is None or raw == "":
        return default
    try:
        want = int(str(raw))
    except (TypeError, ValueError):
        raise SearchRefused(
            R_BAD_LIMIT,
            f"limit={raw!r} is not a whole number. Leave it out for "
            f"{default}.") from None
    if want < 1:
        raise SearchRefused(
            R_LIMIT_RANGE,
            f"limit={want} asks for no results at all. The smallest useful "
            f"limit is 1.")
    if want > ceiling:
        raise SearchRefused(
            R_LIMIT_RANGE,
            f"limit={want} is over the ceiling of {ceiling}. A search box that "
            f"returns more than that has not narrowed anything down — type "
            f"more of the name instead.")
    return want


def _require_kinds(raw: Any) -> tuple[str, ...]:
    if raw is None or str(raw).strip() == "":
        return KINDS
    wanted: list[str] = []
    for part in str(raw).split(","):
        k = part.strip().lower()
        if not k:
            continue
        if k not in KINDS:
            raise SearchRefused(
                R_BAD_KIND,
                f"{part.strip()!r} is not something this counter can search. "
                f"The kinds are: {', '.join(KINDS)}.")
        if k not in wanted:
            wanted.append(k)
    if not wanted:
        return KINDS
    return tuple(wanted)


# ============================================================================
# THE SCAN
# ============================================================================


def _gather(kinds: tuple[str, ...]) -> tuple[list[Candidate],
                                             dict[str, SourceState]]:
    """Load exactly the sources the request needs, each allowed to fail alone.

    Categories are counted from the other three, so asking for categories
    alone still reads all three — and says so in `scanned`, rather than
    reporting a cost it did not pay.
    """
    want_cats = KIND_CATEGORY in kinds
    states: dict[str, SourceState] = {}
    pool: list[Candidate] = []

    products: list[Candidate] = []
    orders: list[Candidate] = []
    bills: list[Candidate] = []

    if KIND_PRODUCT in kinds or want_cats:
        products, state = load_products()
        states["products"] = state
    if KIND_ORDER in kinds or want_cats:
        orders, state = load_orders()
        states["orders"] = state
    if KIND_BILL in kinds or want_cats:
        bills, state = load_bills()
        states["bills"] = state

    if KIND_PRODUCT in kinds:
        pool.extend(products)
    if KIND_ORDER in kinds:
        pool.extend(orders)
    if KIND_BILL in kinds:
        pool.extend(bills)

    if want_cats:
        if any(s.available for s in states.values()):
            cats = build_categories(products, orders, bills)
            pool.extend(cats)
            states["categories"] = SourceState("categories", scanned=len(cats))
        else:
            states["categories"] = _down(
                "categories", R_NOTHING_TO_SEARCH,
                "the groups are counted from the products, orders and bills, "
                "and none of those could be read.")
    return pool, states


def rank(pool: list[Candidate], q: str) -> list[dict[str, Any]]:
    """Score every candidate and order them, deterministically.

    Four stable passes rather than one clever key. The comparison a reader has
    to trust is "same shop, same query, same order every time", and sorting by
    id, then by time newest-first, then by kind, then by score gives exactly
    that — Python's sort is stable, so each pass only breaks the ties the next
    one left.
    """
    qn = _norm(q)
    q_tokens = _tokens(qn)
    want_paise, digits = _query_amount(q)
    # The same query with every Devanagari or Bengali word spelt out in latin,
    # so "पॉन्ड्स" can reach a product the shop called "ponds". Computed once for
    # the whole scan, and "" — costing nothing and reaching nothing — whenever
    # the query has no native-script word in it.
    rn = _norm(romanise_text(qn))
    r_tokens = _tokens(rn) if rn else []

    hits: list[dict[str, Any]] = []
    for cand in pool:
        score, why = _match(cand, qn, q_tokens, want_paise, digits,
                            rn, r_tokens)
        if score <= 0:
            continue
        row = dict(cand.doc)
        row["score"] = score
        row["why"] = why
        row["_tier"] = cand.tier
        row["_rank"] = KIND_RANK.get(cand.kind, len(KIND_RANK))
        row["_at"] = cand.at
        row["_id"] = cand.ident
        hits.append(row)

    hits.sort(key=lambda r: r["_id"])
    hits.sort(key=lambda r: r["_at"], reverse=True)
    hits.sort(key=lambda r: (-int(r["score"]), int(r["_tier"]), int(r["_rank"])))
    for row in hits:
        for k in ("_tier", "_rank", "_at", "_id"):
            row.pop(k, None)
    return hits


# ============================================================================
# RECENCY
# ============================================================================


def _product_touch_times() -> tuple[dict[str, str], Optional[str]]:
    """When each product was last edited, from the catalogue's own audit chain.

    THE CATALOGUE ITSELF CARRIES NO PER-PRODUCT TIMESTAMP. `shop_store.py`
    stores a name, a price, vectors and a footprint, and nothing about when.
    The one dated record of a product changing is the chain
    `gawaah/shopadmin.py` writes when a shopkeeper edits one, so that is what
    is read — and a shop where nothing has been edited through that screen
    simply has no recent products, which is reported as a note rather than
    filled in with the catalogue's file order pretending to be a history.
    """
    try:
        path = shop_dir() / "catalogue.audit.jsonl"
    except SearchRefused as exc:
        return {}, exc.detail
    if not path.is_file():
        return {}, (
            "no product has been edited through the Products screen on this "
            "counter, and the catalogue keeps no per-product date, so no "
            "product can honestly be called recent.")
    seen: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            sku_id = rec.get("sku_id")
            ts = rec.get("ts")
            if isinstance(sku_id, str) and isinstance(ts, str):
                seen[sku_id] = ts
    except OSError as exc:
        return {}, f"the catalogue's edit history could not be read ({exc})."
    return seen, None


def _ago(then: str) -> str:
    """'four minutes ago', in whole units. Integer arithmetic, no float."""
    try:
        when = _dt.datetime.fromisoformat(then)
    except (TypeError, ValueError):
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    delta = _dt.datetime.now(_dt.timezone.utc) - when
    seconds = delta.days * 86400 + delta.seconds
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'' if minutes == 1 else 's'} ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hour{'' if hours == 1 else 's'} ago"
    return f"{hours // 24} days ago"


# ============================================================================
# THE ROUTES
# ============================================================================


def _cost(started_ns: int, states: dict[str, SourceState]) -> dict[str, Any]:
    """What this query actually cost, and what the same code costs at 10,000.

    The extrapolation is linear because the scan is linear — every candidate is
    built and scored on every request, with no index and no cache. Stating it
    here rather than in a README is the point: the number moves when the code
    does.
    """
    took_us = (time.perf_counter_ns() - started_ns) // 1000
    products = states.get("products")
    n_products = products.scanned if products else 0
    at_10k = (MEASURED_US_PER_1000_PRODUCTS * 10) // 1000
    return {
        "took_us": took_us,
        "took_ms": took_us // 1000,
        "budget_ms": BUDGET_MS,
        "within_budget": took_us <= BUDGET_MS * 1000,
        "scanned": {k: v.scanned for k, v in states.items()},
        "measured_us_per_1000_products": MEASURED_US_PER_1000_PRODUCTS,
        "note": (
            f"Every query is a linear scan: no index, nothing cached between "
            f"requests. Measured at {MEASURED_US_PER_1000_PRODUCTS} "
            f"microseconds per 1000 products, so 10,000 SKUs would cost about "
            f"{at_10k} ms per keystroke — past the {BUDGET_MS} ms budget. At "
            f"that size this needs an inverted token index built when the "
            f"catalogue is written, not a faster loop. This shop has "
            f"{n_products} products."),
    }


@router.get("/search")
def search_ep(q: str | None = None, limit: str | None = None,
              kind: str | None = None) -> JSONResponse:
    """Everything that matches one query, ranked, across the whole counter.

    `?q=` is required, `?limit=` defaults to twelve, and `?kind=` narrows to
    any comma-separated subset of product, order, bill, category.

    A source that cannot be read does not empty the box: the others answer,
    `partial` is true, and `sources` names what failed and why. Only when
    NOTHING could be read is this a refusal, because a search over nothing is
    not a short answer, it is a wrong one.
    """
    started_ns = time.perf_counter_ns()
    try:
        query = _require_query(q)
        want = _require_limit(limit, default=DEFAULT_LIMIT, ceiling=MAX_LIMIT)
        kinds = _require_kinds(kind)

        pool, states = _gather(kinds)
        if not any(s.available for s in states.values()):
            raise SearchRefused(
                R_NOTHING_TO_SEARCH,
                "nothing on this counter could be read, so there is nothing to "
                "search. " + " ".join(
                    f"{name}: {s.detail}" for name, s in sorted(states.items())
                    if s.detail))

        hits = rank(pool, query)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "q": query,
            "kinds": list(kinds),
            "limit": want,
            "matched": len(hits),
            "count": min(want, len(hits)),
            "truncated": len(hits) > want,
            "results": hits[:want],
            "partial": _partial(states),
            "sources": {k: v.json() for k, v in states.items()},
            "cost": _cost(started_ns, states),
        })
    except SearchRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/search/recent")
def search_recent_ep(limit: str | None = None) -> JSONResponse:
    """The last things touched, so the command palette opens with something.

    Newest first, across orders and bills — and products too, but ONLY the ones
    the catalogue's edit chain can actually date. A product taught and never
    edited has no timestamp anywhere in this program, and putting it here in
    catalogue order would be presenting alphabetical order as history. What is
    missing, and why, is in `notes`.

    `by_kind` is the same rows bucketed three to a kind. `items` answers "what
    happened last" literally; on a counter that has rung up two hundred bills
    and taken three orders, that literal answer is eight till sessions and
    nothing else, so the bucketed view is there for a palette that wants to
    show a bit of each without inventing its own idea of recent.

    `categories` comes back beside both so a counter installed this morning —
    no orders, no bills, nothing edited — still opens the palette with
    somewhere to go.
    """
    started_ns = time.perf_counter_ns()
    try:
        want = _require_limit(limit, default=DEFAULT_RECENT, ceiling=MAX_RECENT)
        pool, states = _gather(KINDS)
        if not any(s.available for s in states.values()):
            raise SearchRefused(
                R_NOTHING_TO_SEARCH,
                "nothing on this counter could be read, so there is nothing "
                "recent to show. " + " ".join(
                    f"{name}: {s.detail}" for name, s in sorted(states.items())
                    if s.detail))

        notes: list[str] = []
        touched, why_not = _product_touch_times()
        if why_not:
            notes.append(why_not)

        rows: list[dict[str, Any]] = []
        for cand in pool:
            if cand.kind == KIND_CATEGORY:
                continue
            at = cand.at
            if cand.kind == KIND_PRODUCT:
                at = touched.get(cand.ident, "")
                if not at:
                    continue
            if not at:
                continue
            row = dict(cand.doc)
            row["at"] = at
            row["when"] = _ago(at)
            row["why"] = {
                KIND_PRODUCT: "edited " + (_ago(at) or "recently"),
                KIND_ORDER: "placed " + (_ago(at) or "recently"),
                KIND_BILL: "rung up " + (_ago(at) or "recently"),
            }.get(cand.kind, "")
            row["_id"] = cand.ident
            rows.append(row)

        rows.sort(key=lambda r: r["_id"])
        rows.sort(key=lambda r: str(r["at"]), reverse=True)
        for row in rows:
            row.pop("_id", None)

        by_kind: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            bucket = by_kind.setdefault(str(row["type"]), [])
            if len(bucket) < RECENT_PER_KIND:
                bucket.append(row)

        cats = sorted(
            (dict(c.doc) for c in pool if c.kind == KIND_CATEGORY),
            key=lambda d: (-int(d.get("count") or 0), str(d.get("id"))))

        if not rows and not notes:
            notes.append("nothing has been sold, ordered or edited on this "
                         "counter yet.")

        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "limit": want,
            "matched": len(rows),
            "count": min(want, len(rows)),
            "items": rows[:want],
            "by_kind": by_kind,
            "per_kind": RECENT_PER_KIND,
            "categories": cats[:6],
            "notes": notes,
            "partial": _partial(states),
            "sources": {k: v.json() for k, v in states.items()},
            "cost": _cost(started_ns, states),
        })
    except SearchRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/search/health")
def search_health_ep() -> JSONResponse:
    """What search can see, what it costs, and what it deliberately does not do.

    A diagnostic, and the honest place for the limits: the fields that are NOT
    searched, and the catalogue size past which this design stops being the
    right one.
    """
    started_ns = time.perf_counter_ns()
    try:
        pool, states = _gather(KINDS)
        where = None
        try:
            where = str(shop_dir())
        except SearchRefused:
            where = None
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "shop_dir": where,
            "kinds": list(KINDS),
            "sources": {k: v.json() for k, v in states.items()},
            "searchable": len(pool),
            "limits": {
                "max_query_chars": MAX_QUERY,
                "default_results": DEFAULT_LIMIT,
                "max_results": MAX_LIMIT,
                "typo_budget": {"up to 3 letters": 0, "4 to 6 letters": 1,
                                "7 or more": 2},
            },
            "not_searched": [
                "delivery addresses — a search runs on every keystroke and a "
                "doorstep is not something to spill for a two-letter query. "
                "The address is on the order itself.",
                "the line items inside a bill — gawaah/manage.py already "
                "rebuilds those from the chain, and a second derivation here "
                "could disagree with it.",
                "product ids and printed codes are matched exactly or by "
                "prefix, never fuzzily. A barcode one digit out is a "
                "different barcode.",
            ],
            "categories_are_derived": (
                "This catalogue has no category field, so the groups are "
                "facets computed from what is stored: how each product was "
                "taught, whether an offer is on it, whether a code is bound, "
                "the status of each order and whether a bill settled."),
            "cost": _cost(started_ns, states),
        })
    except SearchRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)
