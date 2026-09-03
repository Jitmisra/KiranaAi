"""gawaah/search.py's romaniser, and what gawaah/assistant.py does with it.

WHY THIS FILE EXISTS. `tests/test_assistant_script.py` fixed the sentence: the
mic runs at `hi-IN`, a shopkeeper who speaks Hindi sends Devanagari, and
`SCRIPT_ALIASES` spells the words he SAYS — "kitna", "kilo", "khatam" — into the
Latin the parser's tables are written in. It fixed the grammar and left the
nouns, and it says so in its own comment: brand names "are not here and cannot
be", because a brand name is a catalogue lookup and no fixed table can hold a
name the shop invented last week.

So this shop, which sells `derma`, `manmatter` and `ponds`, still could not be
spoken to. Two real failures off the counter:

  1. "ponds" came back from the recogniser as `पॉन्ड्स` and the counter said it
     did not know it.
  2. "derma ka daam kya hai" came back as `धर्म का क्या दाम है` and the counter
     said this shop has nothing called `धर्म`.

THOSE ARE TWO DIFFERENT BUGS AND THIS FILE KEEPS THEM APART.

  (a) `पॉन्ड्स` IS "ponds". It is the same word, written in the other script,
      and spelling it out letter by letter gets there. That is `romanise`, and
      §1-§3 below pin it.
  (b) `धर्म` is NOT "derma". It is dharma, a different Hindi word, and the
      speech service picked it because it sounds alike. Matching it would be
      this counter deciding which product a person meant. §5 pins what happens
      instead: a refusal, by name, that NAMES `derma` as a thing he might have
      meant and waits for him to say so.

And §4 is the property that makes the whole thing safe to ship, in the shape
`test_assistant_script.py` established: the pre-change lookup is COPIED into
this file and run beside the shipped one, and for a sentence with no native
letter in it the two must agree — sku for sku, refusal for refusal, and word for
word in the refusal's text.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import assistant, search, storefront  # noqa: E402
from gawaah.assistant import (  # noqa: E402
    R_AMBIGUOUS,
    R_EMPTY_CATALOGUE,
    R_NO_PRODUCT_NAMED,
    R_NO_SUCH_PRODUCT,
    TOOL_ADD,
    TOOL_PRICE,
    AssistantRefused,
    normalise,
    resolve_product,
)
from gawaah.search import romanise, romanise_text  # noqa: E402
from tools import upload_app  # noqa: E402

#: THE SHOP AS IT ACTUALLY IS. Three products, latin sku ids and latin names,
#: and the prices in results/shop.json to the paisa — because a test that
#: invented rounder numbers would not notice a rounding bug.
DERMA = ("derma", "derma", 40000)
MANMATTER = ("manmatter", "manmatter", 70000)
PONDS = ("ponds", "ponds", 30000)
CATALOGUE = (DERMA, MANMATTER, PONDS)


def _forbidden_transport(url, headers, body, timeout):
    raise AssertionError(
        f"a test tried to reach {url} for real. The provider is always faked.")


@pytest.fixture()
def shop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A shop that lives and dies with the test.

    BOTH env vars, never one. `GAWAAH_SHOP_DIR` moves the catalogue and
    `GAWAAH_DATA_DIR` moves the audit chain; setting only the first leaves a
    test reading the live results/ directory, which has produced false failures
    on this repo before and once destroyed the real catalogue.
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
                                      typed=f"890111222333{i}")
    assistant.set_transport(_forbidden_transport)
    app = FastAPI()
    app.include_router(assistant.router)
    app.include_router(search.router)
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


#: The catalogue as `resolve_product` is handed it, without an HTTP round trip.
KNOWN = {sku: {"name": name, "price_paise": price}
         for sku, name, price in CATALOGUE}


# ------------------------------------------------------------------------
# 1. THE TWO REPORTED SENTENCES, over HTTP, on the real shop.
# ------------------------------------------------------------------------


def test_the_reported_ponds_price_question_finds_ponds(shop):
    """Failure 1. `पॉन्ड्स` spells out as "ponds", which IS the sku id."""
    body = ask(shop, "पॉन्ड्स ka daam kya hai").json()
    assert body["ok"] is True, body
    assert body["tool"] == TOOL_PRICE, body
    assert body["data"]["sku_id"] == "ponds", body
    assert body["data"]["price_paise"] == 30000, body


def test_the_reported_bare_product_name_finds_ponds(shop):
    """The voice bar's shape: a bare product name, no question around it."""
    body = ask(shop, "पॉन्ड्स").json()
    assert body["ok"] is True, body
    assert body["data"]["sku_id"] == "ponds", body


def test_the_derma_question_as_the_recogniser_should_have_heard_it(shop):
    """`डर्मा` is what "derma" sounds like written down, and it spells out as
    "darma" — one letter off, which is the tolerance a typed query already
    gets."""
    body = ask(shop, "डर्मा ka daam kya hai").json()
    assert body["ok"] is True, body
    assert body["tool"] == TOOL_PRICE, body
    assert body["data"]["sku_id"] == "derma", body


def test_the_whole_question_in_hindi_still_finds_the_product(shop):
    """The grammar through `SCRIPT_ALIASES`, the noun through `romanise`, in
    one sentence and one parser."""
    body = ask(shop, "पॉन्ड्स का दाम क्या है").json()
    assert body["ok"] is True, body
    assert body["tool"] == TOOL_PRICE, body
    assert body["data"]["sku_id"] == "ponds", body


def test_a_bengali_speaker_reaches_the_same_product(shop):
    """The other script the mic can return. `পন্ডস` spells out as "pondos"."""
    body = ask(shop, "পন্ডস").json()
    assert body["ok"] is True, body
    assert body["data"]["sku_id"] == "ponds", body


# ------------------------------------------------------------------------
# 2. THE TRANSLITERATOR ITSELF. Letters in, letters out, no shop involved.
# ------------------------------------------------------------------------


@pytest.mark.parametrize("word,spellings", [
    # The reported one, letter by letter: प+ॉ (po) न+् (n) ड+् (d) स (sa).
    ("पॉन्ड्स", ("ponds", "pondsa")),
    ("डर्मा", ("darma",)),          # the final ा is written, so nothing is cut
    ("धर्म", ("dharm", "dharma")),  # dharma. A word, not a brand.
    # The abugida rules on their own.
    ("दूध", ("dudh", "dudha")),     # matra replaces the unwritten vowel
    ("चावल", ("chaval", "chavala")),
    ("साबुन", ("sabun", "sabuna")),
    ("बैंगन", ("baingan", "baingana")),   # anusvara is a nasal, not a vowel
    ("आम", ("am", "ama")),                # an independent vowel starts a word
    # Bengali. Its unwritten vowel is "o", which is why দশ is "dosh".
    ("দুধ", ("dudh", "dudho")),
    ("চিনি", ("chini",)),
    ("দশ", ("dosh", "dosho")),
    ("পন্ডস", ("pondos", "pondoso")),
    ("ঝাড়ু", ("jhadu",)),            # nukta: ড় is a d in this repo's Hinglish
    ("প্যাকেট", ("pyaket", "pyaketo")),  # ya-phala is a y, never a j
])
def test_the_transliterator_spells_a_word_out(word, spellings):
    assert romanise(word) == spellings


def test_the_word_final_unwritten_vowel_is_dropped_first_and_kept_second():
    """The one rule that is a decision rather than a lookup. Hindi does not
    pronounce the last inherent vowel, which is why the packet says "ponds" and
    not "pondsa" — but "yoga" is a real name, so the undropped spelling is
    offered too and neither is called the right one."""
    assert romanise("पॉन्ड्स")[0] == "ponds"
    assert romanise("पॉन्ड्स")[1] == "pondsa"
    assert romanise("योग") == ("yog", "yoga")


def test_a_nukta_letter_spells_the_same_however_the_keyboard_wrote_it():
    """क़ has a precomposed form and a decomposed one. They look identical on
    screen and a phone can send either."""
    assert romanise("क़िताब") == romanise("क़िताब")
    assert romanise("क़िताब")[0] == "kitab"


def test_an_invisible_joiner_inside_a_word_does_not_split_it():
    """ZWJ and ZWNJ are how some keyboards write a conjunct. A word with one
    buried in it looks the same and must spell out the same."""
    assert romanise("पॉन्‌ड्स") == romanise("पॉन्ड्स")


@pytest.mark.parametrize("word", ["ponds", "derma", "maggi 70g", "", "   ",
                                  "123", "!!!", "😀"])
def test_the_transliterator_declines_anything_that_is_not_native_script(word):
    """THE MECHANISM BEHIND §4, asserted directly. Nothing latin, numeric or
    punctuation has a spelling here, so the passes that use it cannot run."""
    assert romanise(word) == ()
    assert romanise_text(word) == ""


def test_a_word_in_a_script_this_does_not_know_is_left_alone():
    """Tamil and Gurmukhi come off the same recogniser at a different `lang`.
    They are not handled, and the honest form of that is nothing at all —
    which sends the word down the refusal path with its own letters intact."""
    assert romanise("பொண்ட்ஸ") == ()
    assert romanise("ਪੌਂਡਸ") == ()


def test_romanise_text_leaves_the_latin_words_in_a_mixed_sentence_alone():
    assert romanise_text("पॉन्ड्स ka daam kya hai") == "ponds ka daam kya hai"
    assert romanise_text("2 किलो दूध") == "2 kilo dudh"


# ------------------------------------------------------------------------
# 3. IT REACHES A PRODUCT THE TABLES COULD NEVER HOLD.
# ------------------------------------------------------------------------


@pytest.mark.parametrize("said,sku", [
    ("पॉन्ड्स", "ponds"),
    ("पॉंड्स", "ponds"),        # anusvara instead of the conjunct n
    ("पोंड्स", "ponds"),
    ("डर्मा", "derma"),
    ("পন্ডস", "ponds"),         # the Bengali spelling of the same brand
])
def test_a_latin_brand_name_said_in_native_script_finds_its_sku(said, sku):
    assert resolve_product(said, KNOWN) == sku


def test_the_word_as_said_still_wins_over_its_romanisation():
    """ORDER IS THE DESIGN. A shop that taught a Devanagari name is found by
    that name first; the romanised pass only ever runs on what the passes above
    it could not place."""
    taught_native = {"मैगी": {"name": "मैगी", "price_paise": 1400},
                     "maigi": {"name": "maigi", "price_paise": 9900}}
    assert resolve_product("मैगी", taught_native) == "मैगी"


def test_the_alias_table_is_still_consulted_before_any_spelling_out():
    """"दूध" is in `SCRIPT_ALIASES` as "doodh" and `ALIASES` widens that to
    "milk". That path must keep working and must not be overtaken by "dudh"."""
    assert resolve_product(
        "दूध", {"milk": {"name": "Milk 500ml", "price_paise": 2750}}) == "milk"


# ------------------------------------------------------------------------
# 4. THE SAFETY PROPERTY: A LATIN SENTENCE IS UNTOUCHED.
# ------------------------------------------------------------------------

#: The product lookup EXACTLY as it stood before the romanised passes existed:
#: the words as said, then the same four passes with `ALIASES` widening them.
#: Copied, not imported, so a change to the shipped one cannot quietly change
#: what this compares against. It returns instead of raising so that the refusal
#: TEXT is compared too — a suggestion appearing in a latin refusal would be a
#: change to what a shopkeeper reads, and would fail here.


def _lookup_before_the_change(phrase, known):
    q = normalise(phrase)
    if not q:
        return (R_NO_PRODUCT_NAMED,
                "no product was named, so there is nothing to look up.")
    if not known:
        return (R_EMPTY_CATALOGUE,
                "this counter has not been taught any product with a price "
                "yet, so there is nothing to match against. Teach one on the "
                "Products screen first.")
    hits = assistant._match(q, known)
    if not hits:
        widened = [assistant.ALIASES.get(t, t) for t in q]
        if widened != q:
            hits = assistant._match(widened, known)
    if len(hits) == 1:
        return ("sku", hits[0])
    if len(hits) > 1:
        shown = ", ".join(f"{known[s].get('name') or s} ({s})"
                          for s in hits[:assistant.MAX_MATCHES_LISTED])
        return (R_AMBIGUOUS,
                f"{' '.join(q)!r} matches {len(hits)} products in this shop — "
                f"{shown}. Say which one; nothing was added.")
    on_sale = ", ".join(sorted(str(v.get("name") or k)
                               for k, v in known.items())[:6])
    return (R_NO_SUCH_PRODUCT,
            f"this shop has nothing called {' '.join(q)!r}. It sells: "
            f"{on_sale}{'…' if len(known) > 6 else ''}. Teach the product "
            f"first, or say it the way it is written in the catalogue.")


def _lookup_now(phrase, known):
    try:
        return ("sku", resolve_product(phrase, known))
    except AssistantRefused as exc:
        return (exc.reason, exc.detail)


#: Real sentences off a counter: orders, questions, stock movements, money out,
#: and the awkward shapes the module's own comments call out. Every one of them
#: is pure ASCII, and none may read one bit differently than it did before.
HINGLISH = [
    "do ponds add karo",
    "ek derma aur do ponds bill me daalo",
    "ponds daal do",
    "at ponds",
    "aadha kilo chawal",
    "dui ta ponds ar ekta derma dao",
    "250 gram cheeni",
    "aaj kitna hua",
    "aaj ki bikri kitni hui",
    "ponds ka daam kya hai",
    "derma ka rate bhaiya",
    "kaunsa maal khatam ho raha hai",
    "chini kitni bachi hai",
    "ek carton ponds aaya",
    "do ponds toota",
    "chai ka kharcha 50 rupaye likho",
    "golla me kitna cash hai",
    "aaj ka munafa kitna",
    "9876543210 ke points kitne hain",
    "kaun se offers chal rahe hain",
    "ponds ka gst kitna hai",
    "kaunsa saman purana ho raha hai",
    "dudh milega kya",
    "sab suppliers dikhao",
    "aaj ka hisab band karo",
    "manmatter",
    "colgate",
    "parle g biscuit",
    "",
    "   ",
    "!!! ???",
]


@pytest.mark.parametrize("said", HINGLISH)
def test_a_latin_sentence_cannot_reach_the_romanised_passes(said):
    """THE MECHANISM. Every token of a latin sentence romanises to nothing, so
    `_romanised` is empty, so passes 6 and 7 and the suggestion never run. This
    is asserted before the behaviour below, because it is the reason."""
    assert assistant._romanised(normalise(said)) == []
    assert romanise_text(said) == ""


@pytest.mark.parametrize("said", HINGLISH)
def test_a_latin_sentence_resolves_exactly_as_it_did_before(said):
    """THE BEHAVIOUR. Same sku, or the same refusal with the same words in it."""
    assert _lookup_now(said, KNOWN) == _lookup_before_the_change(said, KNOWN)


@pytest.mark.parametrize("said", HINGLISH)
def test_a_latin_sentence_resolves_the_same_against_a_hinglish_catalogue(said):
    """The same property against a catalogue whose names are the words the
    alias tables know, which is where pass 5 actually does something."""
    hinglish_shop = {
        "amul_milk_500ml": {"name": "Amul Milk 500ml", "price_paise": 2750},
        "india_gate_rice_1kg": {"name": "India Gate Rice 1kg",
                                "price_paise": 9925},
        "parle_g_200g": {"name": "Parle-G 200g", "price_paise": 2145},
        "colgate_100g": {"name": "Colgate Toothpaste 100g",
                         "price_paise": 5500},
    }
    assert (_lookup_now(said, hinglish_shop)
            == _lookup_before_the_change(said, hinglish_shop))


def test_the_hinglish_sentences_are_really_all_ascii():
    """Belt and braces: the two tests above prove nothing about a sentence that
    had a native letter hiding in it."""
    assert [s for s in HINGLISH if not s.isascii()] == []


# ------------------------------------------------------------------------
# 5. THE MISHEARD WORD. Named, never chosen.
# ------------------------------------------------------------------------


def test_the_reported_dharma_sentence_is_refused_and_not_resolved(shop):
    """Failure 2, and the decision this module is built around.

    `धर्म` is dharma. The speech service returned a DIFFERENT WORD that sounds
    like "derma", and resolving it would be this counter choosing a product the
    shopkeeper did not name. It refuses — and because the shopkeeper probably
    did say the right thing, the refusal names what it might have been.
    """
    body = ask(shop, "धर्म का क्या दाम है").json()
    assert body["ok"] is False, body
    assert body["reason"] == R_NO_SUCH_PRODUCT, body
    assert body["settles_money"] is False, body
    assert body.get("proposal") is None, body
    # It still echoes what was HEARD, in the letters it was heard in.
    assert "धर्म" in body["detail"], body
    # And it names the product as a question, not as an answer.
    assert "did you mean" in body["detail"], body
    assert "derma" in body["detail"], body
    assert "dharm" in body["detail"], body


def test_the_misheard_word_never_becomes_a_bill_line(shop):
    """The dangerous shape: a count the parser DOES read in front of a word it
    does not. Two of something must not appear on a proposal."""
    body = ask(shop, "दो धर्म add karo").json()
    assert body["ok"] is False, body
    assert body["reason"] == R_NO_SUCH_PRODUCT, body
    assert body.get("proposal") is None, body


def test_a_suggestion_is_only_ever_text_and_never_a_sku():
    """The property under the sentence: `resolve_product` RAISES for `धर्म`. A
    caller cannot accidentally treat the suggestion as a result, because there
    is no result to treat."""
    with pytest.raises(AssistantRefused) as caught:
        resolve_product("धर्म", KNOWN)
    assert caught.value.reason == R_NO_SUCH_PRODUCT
    assert "derma" in caught.value.detail


def test_the_suggestion_budget_is_wider_than_the_one_it_resolves_on():
    """WHY THERE ARE TWO NUMBERS. "dharma" is two edits from "derma"; the
    budget `resolve_product` resolves a six-letter word on is one. So the word
    is suggested and not taken — and if the two numbers were ever made equal,
    this fails."""
    assert search._max_edits("dharma") == 1
    assert search.edit_distance("dharma", "derma", 3) == 2
    assert assistant.SUGGEST_EDITS > 0
    assert search._max_edits("dharma") + assistant.SUGGEST_EDITS >= 2


def test_no_suggestion_is_offered_when_nothing_is_near():
    """A refusal with a made-up suggestion in it is worse than a plain one."""
    with pytest.raises(AssistantRefused) as caught:
        resolve_product("बैंगन", KNOWN)
    assert caught.value.reason == R_NO_SUCH_PRODUCT
    assert "did you mean" not in caught.value.detail
    assert "बैंगन" in caught.value.detail


def test_two_products_within_reach_of_one_spoken_word_is_a_refusal():
    """Not a pick, and not a silent first-alphabetically. Both are named and
    nothing is added."""
    two_close = {"derma": {"name": "derma", "price_paise": 40000},
                 "dorma": {"name": "dorma", "price_paise": 50000}}
    with pytest.raises(AssistantRefused) as caught:
        resolve_product("डर्मा", two_close)
    assert caught.value.reason == R_AMBIGUOUS
    assert "derma" in caught.value.detail
    assert "dorma" in caught.value.detail


# ------------------------------------------------------------------------
# 6. IT STILL REFUSES. The property test_assistant_script.py established.
# ------------------------------------------------------------------------


def test_a_native_sentence_of_unknown_words_is_refused_not_guessed(shop):
    """Cauliflower, brinjal and okra are in no table and in no catalogue. They
    spell out perfectly well — and spelling out is not finding."""
    resp = ask(shop, "ফুলকপি বেগুন ঢেঁড়স")
    body = resp.json()
    assert body["ok"] is False, body
    assert body["reason"] == R_NO_SUCH_PRODUCT, body
    assert body["settles_money"] is False
    assert "ফুলকপি বেগুন ঢেঁড়স" in body["detail"], body
    assert body.get("proposal") is None, body


#: None of these is in `SCRIPT_ALIASES` — a word that IS in it arrives at the
#: lookup already spelt in Latin, which is that table's job and not this one's.
@pytest.mark.parametrize("said", ["बैंगन", "गोभी", "ফুলকপি", "ঢেঁড়স",
                                  "कद्दू", "শসা"])
def test_a_native_word_matching_nothing_is_still_refused_by_name(said):
    with pytest.raises(AssistantRefused) as caught:
        resolve_product(said, KNOWN)
    assert caught.value.reason == R_NO_SUCH_PRODUCT
    assert said in caught.value.detail


def test_the_refusal_still_quotes_the_letters_that_were_heard():
    """A shopkeeper has to be able to see WHAT the counter thought he said. The
    romanised spelling is extra; it never replaces the word itself."""
    with pytest.raises(AssistantRefused) as caught:
        resolve_product("धर्म", KNOWN)
    assert "'धर्म'" in caught.value.detail


@pytest.mark.parametrize("said", [
    "।", "॥", "क", "ক", "ऀ", "ঀ", "‌‍", "आआआआआ",
    "दो", "दो दो दो", "किलो", "कितना", "কত কত", "आज", "আজ",
    "दूध" * 40, "२" * 30, "क्या क्या क्या क्या",
    "ऀঀ", "ﷺ", "🙏 दूध", "दूध‌चावल", "पॉ", "ॉॉॉ", "्", "়",
])
def test_no_native_sentence_of_any_shape_is_a_500(shop, said):
    """An answer or a NAMED refusal, never a crash — including the shapes that
    exercise the transliterator's own edges: a bare matra, a bare virama, a
    bare nukta, and a word that is nothing but marks."""
    resp = ask(shop, said)
    assert resp.status_code in (200, 400, 404), (said, resp.status_code,
                                                 resp.text)
    body = resp.json()
    if body["ok"] is False:
        assert isinstance(body["reason"], str) and body["reason"].strip()
        assert isinstance(body["detail"], str) and body["detail"].strip()


# ------------------------------------------------------------------------
# 7. MONEY. The romanised path prices from the catalogue and nothing else.
# ------------------------------------------------------------------------


def test_a_spoken_native_order_is_priced_from_the_catalogue_in_paise(shop):
    body = ask(shop, "दो पॉन्ड्स add karo").json()
    assert body["ok"] is True, body
    assert body["tool"] == TOOL_ADD, body
    lines = body["proposal"]["lines"]
    assert [(ln["sku_id"], ln["qty"]) for ln in lines] == [("ponds", 2)]
    assert lines[0]["unit_paise"] == PONDS[2]
    assert lines[0]["line_paise"] == PONDS[2] * 2
    assert body["proposal"]["total_paise"] == 60000
    assert isinstance(body["proposal"]["total_paise"], int)


def test_the_spoken_and_the_typed_order_come_to_the_identical_total(shop):
    """The headline claim. Same basket, two scripts, one number."""
    spoken = ask(shop, "दो पॉन्ड्स add karo").json()
    typed = ask(shop, "do ponds add karo").json()
    assert spoken["ok"] is typed["ok"] is True
    assert (spoken["proposal"]["total_paise"]
            == typed["proposal"]["total_paise"] == 60000)


def test_a_proposal_is_still_only_a_proposal(shop):
    body = ask(shop, "दो पॉन्ड्स add karo").json()
    assert body["settles_money"] is False
    assert body["proposal"]["kind"] == "bill"


# ------------------------------------------------------------------------
# 8. THE SEARCH BOX gets the same second pass, and only as a second pass.
# ------------------------------------------------------------------------


def _hits(client: TestClient, q: str):
    body = client.get("/search", params={"q": q}).json()
    assert body["ok"] is True, body
    return body["results"]


def test_the_search_box_finds_a_latin_product_typed_in_devanagari(shop):
    rows = [r for r in _hits(shop, "पॉन्ड्स") if r["type"] == "product"]
    assert [r["sku_id"] for r in rows] == ["ponds"]
    assert "latin letters" in rows[0]["why"], rows[0]


def test_the_search_box_says_it_had_to_respell_the_query(shop):
    """`why` is the promise this module makes about every row it returns. A
    match that only happened because the query was transliterated has to say
    so, and say what the query was read as."""
    rows = [r for r in _hits(shop, "पॉन्ड्स") if r["type"] == "product"]
    assert "'ponds'" in rows[0]["why"], rows[0]["why"]


def test_a_latin_query_is_scored_exactly_as_before(shop):
    rows = [r for r in _hits(shop, "ponds") if r["type"] == "product"]
    assert rows[0]["sku_id"] == "ponds"
    assert rows[0]["why"] == "the name is exactly what you typed"
    assert rows[0]["score"] == search.S_EXACT


def test_a_transliterated_match_ranks_below_a_direct_one(shop):
    """W_ROMANISED, asserted rather than asserted about. The same shop, the
    same product, once by its own name and once by a respelling — and the
    respelling must score lower, or a native query could displace an exact
    latin one somewhere else in the shop."""
    direct = [r for r in _hits(shop, "ponds") if r["type"] == "product"][0]
    respelt = [r for r in _hits(shop, "पॉन्ड्स") if r["type"] == "product"][0]
    assert direct["sku_id"] == respelt["sku_id"] == "ponds"
    assert respelt["score"] < direct["score"]


def test_a_native_query_that_matches_nothing_still_finds_nothing(shop):
    body = shop.get("/search", params={"q": "বেগুন"}).json()
    assert body["ok"] is True, body
    assert [r for r in body["results"] if r["type"] == "product"] == []
