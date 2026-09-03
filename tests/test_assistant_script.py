"""gawaah/assistant.py — the assistant when the sentence arrives in NATIVE SCRIPT.

WHY THIS FILE EXISTS. The counter's microphone runs the browser's speech
recognition at `hi-IN` (ui/src/lib/voice.ts, DEFAULT_LANG) and hands the settled
transcript straight to `/assistant/ask` (ui/src/routes/Assistant.tsx). A
shopkeeper who speaks Hindi therefore sends DEVANAGARI, not Hinglish — and for
as long as `_WORD` matched `[a-z0-9]+` only, every such sentence tokenised to
nothing and came back "nothing was said, so there is nothing to do." Typing
worked. Speaking did not, in two of the three languages this product is sold in.

`SCRIPT_ALIASES` is the fix and it is a SPELLING TABLE, not a second parser.
Four properties are asserted below, and the second is the one that makes the
first three safe to ship:

  1. A spoken Hindi or Bengali sentence reaches the SAME tool and the same
     arguments as the Hinglish sentence that means the same thing.
  2. PURE ASCII IS BYTE-FOR-BYTE UNTOUCHED. The pre-change tokeniser is copied
     into this file and run against every Hinglish sentence, and the two must
     agree exactly. A transliteration layer that quietly changed what a typed
     sentence means would be a far worse bug than the one it fixed.
  3. AN UNKNOWN NATIVE WORD IS STILL A REFUSAL. It passes through unchanged and
     is refused by name, exactly as an unknown Latin word is. The table never
     guesses at the nearest product.
  4. The Devanagari and Bengali DIGIT pass, which predates all of this, still
     works — including when the digits and the words are in different scripts.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import assistant  # noqa: E402
from gawaah.assistant import (  # noqa: E402
    SCRIPT_ALIASES,
    R_NO_SUCH_PRODUCT,
    R_NO_TEXT,
    TOOL_ADD,
    TOOL_FIND,
    TOOL_PRICE,
    TOOL_STOCK_ON_HAND,
    TOOL_TAKINGS,
    AssistantRefused,
    local_route,
    normalise,
)
from tools import upload_app  # noqa: E402

MAGGI = ("maggi_noodles_70g", "Maggi Noodles 70g", 1400)
SOAP = ("lifebuoy_125g", "Lifebuoy 125g", 3950)
MILK = ("amul_milk_500ml", "Amul Milk 500ml", 2750)
RICE = ("india_gate_rice_1kg", "India Gate Rice 1kg", 9925)
CATALOGUE = (MAGGI, SOAP, MILK, RICE)


def _forbidden_transport(url, headers, body, timeout):
    raise AssertionError(
        f"a test tried to reach {url} for real. The provider is always faked.")


@pytest.fixture()
def shop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A shop that lives and dies with the test.

    BOTH env vars are set, never one. `GAWAAH_SHOP_DIR` moves the catalogue and
    `GAWAAH_DATA_DIR` moves the audit chain; setting only the first leaves a
    test reading the live results/ directory, which has produced false failures
    on this repo before. `set_store_dir` moves the till's cached handle as well,
    and is put back afterwards.
    """
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("XAI_BASE_URL", raising=False)
    monkeypatch.delenv("XAI_MODEL", raising=False)
    was = upload_app._DEPS.get("store_dir")
    upload_app.set_store_dir(tmp_path / "shop")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    for i, (sku, name, price) in enumerate(CATALOGUE):
        upload_app.do_enrol_code_only(b"", sku, name, price,
                                      typed=f"890999888777{i}")
    assistant.set_transport(_forbidden_transport)
    app = FastAPI()
    app.include_router(assistant.router)
    client = TestClient(app)
    try:
        yield client
    finally:
        assistant.set_transport(None)
        upload_app._DEPS["store_dir"] = was
        upload_app._DEPS["store"] = None


def ask(client: TestClient, text: str, **over):
    body = {"text": text}
    body.update(over)
    return client.post("/assistant/ask", json=body)


# ------------------------------------------------------------------------
# 1. THE REPORTED BUG. Each of these came back "nothing was said".
# ------------------------------------------------------------------------


@pytest.mark.parametrize("said,tool", [
    ("aaj kitna hua", TOOL_TAKINGS),           # worked before, must keep working
    ("आज कितनी बिक्री हुई", TOOL_TAKINGS),      # spoken Hindi
    ("আজ কত বিক্রি হয়েছে", TOOL_TAKINGS),       # spoken Bengali
    ("दो किलो दूध", TOOL_ADD),                  # spoken Hindi, an order
])
def test_the_reported_sentences_tokenise_and_route(said, tool):
    """Not "produces something" — the RIGHT tool for what was said."""
    assert normalise(said), f"{said!r} still tokenises to nothing"
    assert local_route(said)[0] == tool


def test_the_reported_hindi_takings_question_answers_over_http(shop):
    body = ask(shop, "आज कितनी बिक्री हुई").json()
    assert body["ok"] is True, body
    assert body["tool"] == TOOL_TAKINGS, body


def test_the_reported_bengali_takings_question_answers_over_http(shop):
    body = ask(shop, "আজ কত বিক্রি হয়েছে").json()
    assert body["ok"] is True, body
    assert body["tool"] == TOOL_TAKINGS, body


def test_the_reported_hindi_order_reaches_the_milk_on_the_shelf(shop):
    body = ask(shop, "दो किलो दूध").json()
    assert body["ok"] is True, body
    lines = body["proposal"]["lines"]
    assert [(ln["sku_id"], ln["qty"]) for ln in lines] == [(MILK[0], 2)]


# ------------------------------------------------------------------------
# 2. THE SAFETY PROPERTY: pure ASCII is untouched.
# ------------------------------------------------------------------------

#: The tokeniser EXACTLY as it stood before SCRIPT_ALIASES existed. Copied, not
#: imported, so that a change to the shipped one cannot quietly change what this
#: test compares against.
_OLD_WORD = re.compile(r"[a-z0-9]+")


def _normalise_before_the_change(text: str) -> list[str]:
    return _OLD_WORD.findall((text or "").translate(assistant._DIGITS).lower())


#: Real sentences off a counter, in the shapes this parser is built for: orders,
#: questions, stock movements, money out, and the awkward ones the module's own
#: comments call out ("Maggi daal do", "at Maggi", a rupee figure said aloud).
HINGLISH = [
    "do Maggi add karo",
    "ek Lifebuoy aur do Maggi bill me daalo",
    "Maggi daal do",
    "at Maggi",
    "aadha kilo chawal",
    "dui ta Maggi ar ekta Lifebuoy dao",
    "250 gram cheeni",
    "aaj kitna hua",
    "aaj ki bikri kitni hui",
    "Maggi ka daam kya hai",
    "Lifebuoy ka rate bhaiya",
    "kaunsa maal khatam ho raha hai",
    "chini kitni bachi hai",
    "ek carton Maggi aaya",
    "do Maggi toota",
    "chai ka kharcha 50 rupaye likho",
    "golla me kitna cash hai",
    "aaj ka munafa kitna",
    "9876543210 ke points kitne hain",
    "kaun se offers chal rahe hain",
    "Maggi ka gst kitna hai",
    "kaunsa saman purana ho raha hai",
    "dudh milega kya",
    "sab suppliers dikhao",
    "aaj ka hisab band karo",
    "",
    "   ",
    "!!! ???",
]


@pytest.mark.parametrize("said", HINGLISH)
def test_pure_ascii_tokenises_exactly_as_it_did_before(said):
    """THE PROPERTY THAT MAKES THIS SAFE. Every alias key is non-ASCII and the
    two added character classes cannot match an ASCII byte, so a typed Hinglish
    sentence must come out of `normalise` unchanged, token for token."""
    assert normalise(said) == _normalise_before_the_change(said), said


def test_no_alias_key_is_ascii():
    """The mechanism behind the test above, asserted directly: if any key were
    ASCII it could rewrite a typed word and nobody would notice for months."""
    ascii_keys = sorted(k for k in SCRIPT_ALIASES if k.isascii())
    assert ascii_keys == []


@pytest.mark.parametrize("said,tokens", [
    ("do Maggi add karo", ["do", "maggi", "add", "karo"]),
    ("aaj kitna hua", ["aaj", "kitna", "hua"]),
    ("250 gram cheeni", ["250", "gram", "cheeni"]),
])
def test_a_few_ascii_sentences_pinned_by_hand(said, tokens):
    """Belt and braces: the comparison above would pass if BOTH tokenisers
    broke the same way. These three are written out."""
    assert normalise(said) == tokens


# ------------------------------------------------------------------------
# 3. COLLISIONS. Every value must be a word the parser already knows.
# ------------------------------------------------------------------------

#: "bottle" is the one deliberate exception and it is not a collision: no table
#: in the module contains it, in Latin either. A typed "bottle" is carried into
#: the product phrase, where a shelf full of "Coke 500ml bottle" wants it, and
#: the alias gives the spoken word exactly the same treatment.
PASSES_THROUGH = {"bottle"}


def _known_tokens() -> dict[str, list[str]]:
    """Every Latin token the parser's tables know -> which tables hold it."""
    out: dict[str, list[str]] = {}

    def add(name: str, words) -> None:
        for t in words:
            out.setdefault(t, []).append(name)

    for name in ("NUMBER_WORDS", "FRACTION_WORDS", "PACK_UNITS", "UNIT_WORDS",
                 "STOP_WORDS", "CONJUNCTIONS", "ALIASES", "ORDER_WORDS",
                 "STOCK_WORDS", "PRICE_WORDS", "TAKINGS_WORDS", "FIND_WORDS",
                 "EXPENSE_WORDS", "CASH_WORDS", "MARGIN_WORDS",
                 "SUPPLIER_WORDS", "CUSTOMER_WORDS", "CATEGORY_WORDS",
                 "DAY_CLOSE_WORDS", "OFFER_WORDS", "GST_WORDS",
                 "EXPIRY_WORDS", "LOYALTY_WORDS", "MOVEMENT_WORDS",
                 "REORDER_WORDS", "MOVEMENT_LOG_WORDS", "ADD_VERBS",
                 "QUESTION_WORDS", "RUPEE_WORDS"):
        add(name, getattr(assistant, name))
    add("VERB_BIGRAM_HEADS", assistant._VERB_BIGRAM_HEADS)
    for _category, words in assistant._EXPENSE_CATEGORY_WORDS:
        add("EXPENSE_CATEGORY", words)
    return out


def test_every_alias_value_is_a_word_the_parser_already_knows():
    """THE COLLISION GATE. A value the tables do not contain is a typo that
    would be dropped as a stray word — the silent kind of wrong, because the
    sentence would still route, just without the word that mattered."""
    known = _known_tokens()
    strays = sorted({v for v in SCRIPT_ALIASES.values()
                     if v not in known and v not in PASSES_THROUGH})
    assert strays == [], f"aliases pointing at nothing: {strays}"


def test_no_alias_value_is_a_one_letter_token():
    """The module leaves "g", "l" and the Bengali "o" out of its own tables
    because a one-letter token eats the tail of a brand name. An alias must not
    smuggle one back in."""
    short = sorted({v for v in SCRIPT_ALIASES.values() if len(v) < 2})
    assert short == []


def test_alias_values_are_plain_lowercase_ascii_words():
    """`normalise` promises lowercase alphanumeric tokens. A value with a space
    or a capital in it would break that promise from the inside."""
    bad = sorted({v for v in SCRIPT_ALIASES.values()
                  if not (v.isascii() and v.isalnum() and v == v.lower())})
    assert bad == []


def test_the_two_scripts_are_both_actually_covered():
    """A table that had grown only Devanagari would leave Bengali exactly as
    broken as it was, and every other test here would still pass."""
    dev = [k for k in SCRIPT_ALIASES if "ऀ" <= k[0] <= "ॿ"]
    ben = [k for k in SCRIPT_ALIASES if "ঀ" <= k[0] <= "৿"]
    assert len(dev) > 100, len(dev)
    assert len(ben) > 100, len(ben)
    assert len(dev) + len(ben) == len(SCRIPT_ALIASES)


# ------------------------------------------------------------------------
# 4. IT STILL REFUSES. An unknown native word is not a guess.
# ------------------------------------------------------------------------


def test_an_unknown_native_word_passes_through_unchanged():
    """The fallback, asserted directly: not in the table means not translated,
    which is what keeps the next test honest."""
    assert normalise("ফুলকপি") == ["ফুলকপি"]
    assert normalise("बैंगन") == ["बैंगन"]


def test_a_native_sentence_of_unknown_words_is_refused_not_guessed(shop):
    """Cauliflower, brinjal and okra are in no table and in no catalogue. The
    counter must refuse by name and echo back WHAT IT HEARD — it must not reach
    for the nearest sku, and it must not propose anything.

    The refusal does go on to list what the shop DOES sell, which is the point
    of a refusal a person can act on; what is asserted here is that nothing on
    that list was chosen.
    """
    resp = ask(shop, "ফুলকপি বেগুন ঢেঁড়স")
    body = resp.json()
    assert body["ok"] is False, body
    assert body["reason"] == R_NO_SUCH_PRODUCT, body
    assert body["settles_money"] is False
    assert "ফুলকপি বেগুন ঢেঁড়স" in body["detail"], body
    assert body.get("proposal") is None, body


def test_a_native_sentence_of_unknown_words_names_no_product_locally():
    """The same property one layer down: the product phrase handed on is the
    words that were actually said, not a translation of them."""
    tool, args = local_route("बैंगन गोभी")
    assert tool == TOOL_FIND
    assert args["product"] == "बैंगन गोभी"


def test_an_inflected_native_noun_is_a_known_limit_and_refuses_honestly():
    """WHAT STILL DOES NOT WORK, written down rather than hidden.

    A spelling table matches whole tokens. Bengali "চালের" is "of rice" with the
    genitive suffix fused onto the noun, and it is not "চাল" — so it passes
    through and the counter says it cannot find it. That is EXACTLY what the
    Latin path already does with "chaler dor koto", so this is parity and not a
    regression, and the behaviour is a refusal rather than a guess at rice.
    """
    assert normalise("চালের দর কত") == ["চালের", "dor", "koto"]
    spoken, typed = local_route("চালের দর কত"), local_route("chaler dor koto")
    # Same tool, and each carries the word it was actually given: neither one
    # has quietly decided the shopkeeper meant rice.
    assert spoken[0] == typed[0] == TOOL_PRICE
    assert spoken[1]["product"] == "চালের"
    assert typed[1]["product"] == "chaler"


def test_native_punctuation_alone_is_still_nothing_was_said():
    """The danda is a full stop in both scripts, not a word."""
    assert normalise("।। ॥") == []
    with pytest.raises(AssistantRefused) as caught:
        local_route("।। ॥")
    assert caught.value.reason == R_NO_TEXT


def test_an_unknown_native_word_does_not_become_a_bill_line(shop):
    """The dangerous shape: a count the parser DOES read in front of a product
    it does not. It must refuse rather than bill two of something else."""
    body = ask(shop, "दो बैंगन add karo").json()
    assert body["ok"] is False, body
    assert body["reason"] == R_NO_SUCH_PRODUCT, body
    assert "proposal" not in body or body.get("proposal") is None


# ------------------------------------------------------------------------
# 5. THE DIGIT PASS, which predates this and must not regress.
# ------------------------------------------------------------------------


@pytest.mark.parametrize("said,tokens", [
    ("২৫০ gram doodh", ["250", "gram", "doodh"]),
    ("२५० gram doodh", ["250", "gram", "doodh"]),
    ("५ Maggi", ["5", "maggi"]),
    ("১২ Maggi", ["12", "maggi"]),
    # Digits in one script and words in another, which is what a phone with two
    # keyboards installed actually produces.
    ("২ किलो चावल", ["2", "kilo", "chawal"]),
    ("२ প্যাকেট চিনি", ["2", "packet", "chini"]),
])
def test_script_digits_still_transliterate(said, tokens):
    assert normalise(said) == tokens


@pytest.mark.parametrize("digits,qty", [("৫", 5), ("५", 5), ("5", 5),
                                        ("১২", 12), ("१२", 12)])
def test_script_digits_still_reach_the_right_count(shop, digits, qty):
    body = ask(shop, f"{digits} Maggi").json()
    assert body["ok"] is True, body
    assert body["proposal"]["lines"][0]["qty"] == qty


# ------------------------------------------------------------------------
# 6. MIXED SCRIPT IN ONE SENTENCE, because that is what people say.
# ------------------------------------------------------------------------


def test_mixed_script_in_one_sentence_tokenises():
    assert normalise("2 किलो दूध add karo") == [
        "2", "kilo", "doodh", "add", "karo"]


def test_mixed_script_in_one_sentence_reaches_the_bill(shop):
    body = ask(shop, "2 किलो दूध add karo").json()
    assert body["ok"] is True, body
    lines = body["proposal"]["lines"]
    assert [(ln["sku_id"], ln["qty"]) for ln in lines] == [(MILK[0], 2)]


@pytest.mark.parametrize("said", [
    "do Maggi aur ek Lifebuoy add karo",          # all Latin
    "दो Maggi aur एक Lifebuoy add करो",           # Hindi grammar, Latin brands
    "দুটো Maggi আর একটা Lifebuoy দাও",             # Bengali grammar, Latin brands
    "२ Maggi और १ Lifebuoy",                      # Devanagari digits and words
])
def test_the_same_basket_in_any_mixture_of_scripts(shop, said):
    """THE HEADLINE CLAIM, now including the two scripts the microphone
    actually returns: the identical sku ids, counts and total."""
    body = ask(shop, said).json()
    assert body["ok"] is True, body
    prop = body["proposal"]
    assert [(ln["sku_id"], ln["qty"]) for ln in prop["lines"]] == [
        (MAGGI[0], 2), (SOAP[0], 1)]
    assert prop["total_paise"] == MAGGI[2] * 2 + SOAP[2]


# ------------------------------------------------------------------------
# 7. A SPOKEN SENTENCE ROUTES WHERE THE TYPED ONE DOES.
# ------------------------------------------------------------------------


@pytest.mark.parametrize("spoken,typed", [
    # questions about the day
    ("आज कितनी बिक्री हुई", "aaj kitni bikri hui"),
    ("আজ কত বিক্রি হয়েছে", "aaj koto bikri hoyeche"),
    ("आज कितना खर्च हुआ", "aaj kitna kharch hua"),
    ("आज का हिसाब", "aaj ka hisab"),
    # the shelf
    ("चीनी कितनी बची है", "chini kitni bachi hai"),
    ("दूध खत्म हो गया", "doodh khatam ho gaya"),
    ("চিনি শেষ", "chini sesh"),
    # a price
    ("दूध का दाम क्या है", "doodh ka daam kya hai"),
    ("চাল দর কত", "chal dor koto"),
    # money out, cash, margin
    ("चाय का खर्चा", "chai ka kharcha"),
    ("गल्ला कितना है", "galla kitna hai"),
    ("आज का मुनाफा", "aaj ka munafa"),
    # stock movements
    ("एक कार्टन दूध आया", "ek carton doodh aaya"),
    ("দুটো Maggi ভেঙেছে", "duto Maggi bhengeche"),
    # orders
    ("तीन किलो चावल", "teen kilo chawal"),
    ("আধা কিলো চিনি", "aadha kilo chini"),
    ("पाँच पैकेट Maggi add karo", "panch packet Maggi add karo"),
])
def test_spoken_native_script_routes_where_typed_hinglish_does(spoken, typed):
    """ONE PARSER, NOT TWO. The table is a spelling of the same vocabulary, so
    the tool and every argument must come out identical."""
    assert local_route(spoken) == local_route(typed), spoken


@pytest.mark.parametrize("spoken,tool", [
    ("दूध का दाम क्या है", TOOL_PRICE),
    ("चीनी कितनी बची है", TOOL_STOCK_ON_HAND),
    ("तीन किलो चावल", TOOL_ADD),
    ("Maggi आछे", TOOL_FIND),
])
def test_the_routes_above_are_the_tools_a_shopkeeper_meant(spoken, tool):
    """The pairing test would pass if BOTH sides routed somewhere wrong. These
    name the tool outright."""
    assert local_route(spoken)[0] == tool


@pytest.mark.parametrize("said,qty", [
    ("एक Maggi", 1), ("दो Maggi", 2), ("तीन Maggi", 3), ("चार Maggi", 4),
    ("पाँच Maggi", 5), ("छह Maggi", 6), ("सात Maggi", 7), ("आठ Maggi", 8),
    ("नौ Maggi", 9), ("दस Maggi", 10), ("ग्यारह Maggi", 11),
    ("बारह Maggi", 12), ("पंद्रह Maggi", 15), ("बीस Maggi", 20),
    ("पचास Maggi", 50), ("सौ Maggi", 100),
    ("এক Maggi", 1), ("দুই Maggi", 2), ("তিন Maggi", 3), ("চার Maggi", 4),
    ("পাঁচ Maggi", 5), ("ছয় Maggi", 6), ("সাত Maggi", 7), ("আট Maggi", 8),
    ("নয় Maggi", 9), ("দশ Maggi", 10), ("এগারো Maggi", 11),
    ("বারো Maggi", 12), ("পনেরো Maggi", 15), ("বিশ Maggi", 20),
    ("পঞ্চাশ Maggi", 50), ("একশো Maggi", 100),
])
def test_spoken_counting_words_reach_the_same_count(said, qty):
    """The counting words are what decide a quantity, and a quantity read wrong
    is money. Both scripts, one to twenty and the round numbers above it."""
    assert local_route(said)[1]["qty"] == qty


@pytest.mark.parametrize("said,fraction,canonical", [
    ("आधा किलो चावल", "aadha", "aadha"),
    ("डेढ़ किलो चावल", "dedh", "dedh"),
    ("सवा किलो चावल", "sava", "sava"),
    ("पौने किलो चावल", "paune", "paune"),
    ("पाव किलो चावल", "pav", "pav"),
    ("ढाई किलो चावल", "dhai", "dhai"),
    # The Bengali spellings stay themselves through the parser and are folded
    # onto the Hindi ones by `_canonical_fraction`, which is the spelling
    # weighed.py stores grams against. The aliases feed that same table.
    ("অর্ধেক কিলো চাল", "ordhek", "aadha"),
    ("পোয়া কিলো চাল", "poya", "pav"),
    ("দেড় কিলো চাল", "dedh", "dedh"),
])
def test_spoken_fractions_reach_the_same_fraction(said, fraction, canonical):
    assert local_route(said)[1]["fraction"] == fraction
    assert assistant._canonical_fraction(fraction) == canonical


# ------------------------------------------------------------------------
# 8. NO SENTENCE OF ANY SHAPE PRODUCES A 500.
# ------------------------------------------------------------------------


@pytest.mark.parametrize("said", [
    "।", "॥", "क", "ক", "ऀ", "ঀ", "‌‍", "आआआआआ",
    "दो", "दो दो दो", "किलो", "कितना", "কত কত", "आज", "আজ",
    "दूध" * 40, "२" * 30, "क्या क्या क्या क्या",
    "ऀঀ", "ﷺ", "🙏 दूध", "दूध‌चावल",
])
def test_no_native_sentence_of_any_shape_is_a_500(shop, said):
    """Claim 4 of the assistant's own test file, extended to the scripts that
    can now reach the parser: an answer or a NAMED refusal, never a crash."""
    resp = ask(shop, said)
    assert resp.status_code in (200, 400, 404), (said, resp.status_code,
                                                 resp.text)
    body = resp.json()
    if body["ok"] is False:
        assert isinstance(body["reason"], str) and body["reason"].strip()
        assert isinstance(body["detail"], str) and body["detail"].strip()
