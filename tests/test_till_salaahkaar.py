"""tests/test_till_salaahkaar.py — what the till's "Say the order" card sends,
and what comes back.

The card (ui/src/components/VoiceBar.tsx) no longer parses products in the
browser. It classifies a sentence as an order or a question and sends the
order to `/assistant/ask` as exactly `{"text", "source"}` — the request these
tests make. So this file is the server half of that card's contract:

  1. THE QUANTITY WORDS a counter actually says — Hindi, English, Bengali,
     Devanagari and Bengali script, a dozen, a half kilo — reach the same
     integer count and the same integer paise.
  2. A PRODUCT SHE CANNOT NAME IS REFUSED BY NAME, never guessed. "Pepsi" in a
     shop with no Pepsi is a refusal that says "pepsi", with no proposal.
  3. "PARLE JI" IS PARLE-G. The honorific is how the product is said, in
     Latin and in Devanagari, on the local parser's path and on the model's.
  4. A QUESTION PROPOSES NOTHING, and a proposal settles nothing: the response
     to an order carries no payment field of any kind, because the money
     service is not on this path and the page has no way to reach it from here
     (invariant 6, the server half; ui/src/lib/voice.test.ts holds the page
     half).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import assistant  # noqa: E402
from gawaah import weighed as _weighed  # noqa: E402
from gawaah.assistant import (  # noqa: E402
    R_NO_SUCH_PRODUCT,
    TOOL_ADD,
    TOOL_FIND,
    TOOL_PRICE,
    resolve_product,
)
from tools import upload_app  # noqa: E402

# Not round numbers, so a bug that divides shows in the second decimal place.
MAGGI = ("maggi_noodles_70g", "Maggi 2-Minute Noodles 70 g (मैगी नूडल्स)", 1400)
PARLE = ("parle_g_biscuit", "Parle-G biscuit 100g", 1000)
SOAP = ("lifebuoy_soap_125g", "Lifebuoy Total Soap 125 g", 3800)
RICE = ("basmati_rice_1kg", "Basmati Rice 1kg", 9900)
RICE_PER_KG = 9900
CATALOGUE = (MAGGI, PARLE, SOAP, RICE)

FAKE_KEY = "xai-this-string-must-never-appear-in-a-response"


def _forbidden_transport(url, headers, body, timeout):
    raise AssertionError(
        f"a test tried to reach {url} for real. The provider is always faked.")


class Fake:
    """A transport that answers to order — the model, without the network."""

    def __init__(self, answer):
        self.answer = answer

    def __call__(self, url, headers, body, timeout):
        return 200, self.answer


def tool_call(name: str, args: dict) -> dict:
    return {"choices": [{"message": {"role": "assistant", "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": name, "arguments": json.dumps(args)}}]}}]}


@pytest.fixture()
def till(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A shop that lives and dies with the test, with loose rice.

    Redirected three ways, as tests/test_assistant.py explains: the store
    handle, GAWAAH_SHOP_DIR for anything that re-reads the environment, and
    GAWAAH_DATA_DIR so the assistant's audit chain does not land in results/.
    XAI_API_KEY is deleted; the local parser is the default brain.
    """
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "data"))
    for var in ("XAI_API_KEY", "XAI_BASE_URL", "XAI_MODEL",
                "GAWAAH_WEIGHED_FILE", "GAWAAH_SCAN_DIR", "GAWAAH_CODES_FILE"):
        monkeypatch.delenv(var, raising=False)
    was = upload_app._DEPS.get("store_dir")
    upload_app.set_store_dir(tmp_path / "shop")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    for i, (sku, name, price) in enumerate(CATALOGUE):
        upload_app.do_enrol_code_only(b"", sku, name, price,
                                      typed=f"89012345678{i}1")
    _weighed.save_weighed({RICE[0]: _weighed.WeighedSku(
        sku_id=RICE[0], price_per_kg_paise=RICE_PER_KG,
        since="2026-09-01T00:00:00+00:00")})
    assistant.set_transport(_forbidden_transport)
    app = FastAPI()
    app.include_router(assistant.router)
    client = TestClient(app)
    try:
        yield client
    finally:
        assistant.set_transport(None)
        _weighed.set_weighed_path(None)
        upload_app._DEPS["store_dir"] = was
        upload_app._DEPS["store"] = None


def say(client: TestClient, text: str, source: str = "voice"):
    """Exactly the body the till's card sends. Two fields."""
    return client.post("/assistant/ask", json={"text": text, "source": source})


# ------------------------------------------------------------------------
# 1. The quantity words.
# ------------------------------------------------------------------------


def test_the_demo_sentence_proposes_two_lines_in_integer_paise(till):
    body = say(till, "do Maggi aur ek Parle-G").json()
    assert body["ok"] is True, body
    assert body["tool"] == TOOL_ADD
    assert body["settles_money"] is False
    p = body["proposal"]
    assert p["kind"] == "bill"
    assert p["accepted"] is False
    assert [(ln["sku_id"], ln["qty"], ln["unit_paise"], ln["line_paise"])
            for ln in p["lines"]] == [
        (MAGGI[0], 2, 1400, 2800), (PARLE[0], 1, 1000, 1000)]
    assert p["total_paise"] == 3800
    assert p["total_rupees"] == "38.00"
    for ln in p["lines"]:
        assert isinstance(ln["line_paise"], int)
        assert not isinstance(ln["line_paise"], bool)


@pytest.mark.parametrize("said,qty", [
    ("ek Maggi", 1), ("do Maggi", 2), ("teen Maggi", 3), ("2 Maggi", 2),
    ("two Maggi", 2), ("दो Maggi", 2), ("दो मैगी", 2),
    ("duto Maggi", 2), ("dui Maggi", 2), ("tin Maggi", 3), ("দুটো Maggi", 2),
    ("ek dozen Maggi", 12), ("a dozen Maggi", 12), ("ek darjan Maggi", 12),
    ("एक दर्जन Maggi", 12),
])
def test_a_count_in_any_of_the_three_languages_is_the_same_count(till, said, qty):
    body = say(till, said).json()
    assert body["ok"] is True, body
    assert body["tool"] == TOOL_ADD
    line = body["proposal"]["lines"][0]
    assert line["sku_id"] == MAGGI[0]
    assert line["qty"] == qty
    assert line["line_paise"] == 1400 * qty


def test_a_dozen_is_said_out_loud_as_a_caution(till):
    p = say(till, "ek dozen Maggi").json()["proposal"]
    assert p["lines"][0]["qty"] == 12
    assert "dozen" in p["caution"]
    assert "12" in p["caution"]


@pytest.mark.parametrize("said,grams", [
    ("aadha kilo rice", 500), ("half kilo rice", 500), ("ordhek kilo rice", 500),
    ("आधा किलो rice", 500), ("pav kilo rice", 250), ("dedh kilo rice", 1500),
])
def test_a_weight_is_priced_by_weighed_py_not_rounded_to_a_packet(till, said, grams):
    body = say(till, said).json()
    assert body["ok"] is True, body
    line = body["proposal"]["lines"][0]
    assert line["by"] == "weighed"
    assert line["sku_id"] == RICE[0]
    assert line["grams"] == grams
    assert line["line_paise"] == _weighed.line_paise(RICE_PER_KG, grams)
    assert line["qty"] == 1
    assert line["unit_paise"] == line["line_paise"]


def test_a_weight_of_a_packet_product_is_refused_not_rounded(till):
    body = say(till, "aadha kilo Maggi").json()
    assert body["ok"] is False
    assert body["proposal"] is None if "proposal" in body else True
    assert "weight" in body["detail"] or "packet" in body["detail"]


# ------------------------------------------------------------------------
# 2. A product she cannot name.
# ------------------------------------------------------------------------


def test_a_product_the_shop_does_not_sell_is_refused_by_name(till):
    body = say(till, "do Maggi aur ek Pepsi").json()
    assert body["ok"] is False
    assert body["reason"] == R_NO_SUCH_PRODUCT
    assert "pepsi" in body["detail"].lower()
    # Whole or not at all: the Maggi that resolved is not proposed either.
    assert body.get("proposal") is None
    assert "NONE of it was proposed" in body["detail"]


# ------------------------------------------------------------------------
# 3. "Parle ji" is Parle-G.
# ------------------------------------------------------------------------


@pytest.mark.parametrize("said", ["ek Parle ji", "एक पारले जी", "ek parle g",
                                  "एक पारले", "do Parle-G please"])
def test_parle_said_with_the_honorific_reaches_parle_g(till, said):
    body = say(till, said).json()
    assert body["ok"] is True, body
    assert body["tool"] == TOOL_ADD
    assert body["proposal"]["lines"][0]["sku_id"] == PARLE[0]


def test_the_honorific_is_dropped_on_the_models_path_too(till, monkeypatch):
    """The model hands the phrase over AS SAID — "parle ji" — and the local
    parser's stopwords never touch it. resolve_product has to drop it itself."""
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(tool_call(TOOL_ADD, {"product": "parle ji", "qty": 1})))
    body = say(till, "ek Parle ji").json()
    assert body["ok"] is True, body
    assert body["proposal"]["lines"][0]["sku_id"] == PARLE[0]
    assert FAKE_KEY not in json.dumps(body)


def test_a_phrase_that_is_only_an_honorific_is_refused_not_emptied(till):
    known = assistant.catalogue()
    with pytest.raises(assistant.AssistantRefused) as exc:
        resolve_product("ji", known)
    assert exc.value.reason == R_NO_SUCH_PRODUCT
    assert "ji" in exc.value.detail


def test_parle_ji_ka_daam_is_a_price_question_about_parle_g(till):
    body = say(till, "Parle ji ka daam").json()
    assert body["ok"] is True, body
    assert body["tool"] == TOOL_PRICE
    assert body["data"]["sku_id"] == PARLE[0]
    assert body["data"]["price_paise"] == 1000


# ------------------------------------------------------------------------
# 4. A question proposes nothing; a proposal settles nothing.
# ------------------------------------------------------------------------


def test_a_price_question_proposes_nothing(till):
    body = say(till, "Parle-G ka daam kya hai").json()
    assert body["ok"] is True, body
    assert body["tool"] == TOOL_PRICE
    assert body["proposal"] is None
    assert body["data"]["price_paise"] == 1000


def test_one_bare_product_name_is_a_question_and_two_are_an_order(till):
    """The page's classifier keeps this exact rule; if the server's changes,
    the two drift and the screen starts saying "she re-read it" every time."""
    assert say(till, "Maggi").json()["tool"] == TOOL_FIND
    body = say(till, "Maggi aur Parle-G").json()
    assert body["tool"] == TOOL_ADD
    assert [ln["qty"] for ln in body["proposal"]["lines"]] == [1, 1]


def test_an_order_carries_no_payment_field_of_any_kind(till):
    """INVARIANT 6, the server half. The page sends a sentence; what comes
    back is a proposal with `accepted: false` and nothing that could be shown
    to a customer as something to pay."""
    body = say(till, "do Maggi aur ek Parle-G").json()
    flat = json.dumps(body).lower()
    for forbidden in ("short_url", "upi", "payment_link", "qr", "intent",
                      "razorpay", "plink_"):
        assert forbidden not in flat, forbidden
    assert body["settles_money"] is False
    assert body["proposal"]["accepted"] is False


def test_the_page_may_not_author_a_line(till):
    """A body that carries a sku, a price or lines is refused whole — the
    card sends two fields and this is what happens to a page that sends more."""
    r = till.post("/assistant/ask", json={
        "text": "do Maggi", "source": "voice",
        "lines": [{"sku_id": MAGGI[0], "qty": 2, "price_paise": 1}]})
    body = r.json()
    assert body["ok"] is False
    assert body["reason"] == assistant.R_CLIENT_AUTHORED
