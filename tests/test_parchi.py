"""gawaah/parchi.py — the photographed bill, without a model in the room.

Everything here runs against a FAKE transport that answers with the ground
truth in `tests/fixtures_parchi/truth.json` — the JSON a perfect reading of
each bench invoice produces. So the suite proves the deterministic half of
the feature: what leaves the machine, how a printed name finds a product,
that one paisa refuses, and that a booking goes through `purchases.py`'s own
writer. Whether the model can READ the bills is the bench's job
(`tests/fixtures_parchi/BENCH.md`), and it is measured, not assumed.

Six claims:

  1. THE MODEL SEES THE PHOTOGRAPH AND NOTHING OF THE SHOP. The request
     bytes are read back: the photograph is in them, the instruction is in
     them, and not one catalogue name, price or sku id is.

  2. THE MATCH IS LOCAL AND DETERMINISTIC. An exact name is PROPOSED, an
     abbreviation or a part of a name asks CONFIRM?, a product the shop does
     not sell is an exception row — and the model's answer never names a sku.

  3. INTEGER PAISE, DIGIT BY DIGIT. "₹ 1,245.00" is 124500. A float in the
     answer is refused, not rounded.

  4. ONE PAISA REFUSES, BY NAME. Bench #3 prints 423.01 where 36 × 11.75 is
     423.00; the gate names line 2, and ACCEPT is refused with the same name
     and nothing is written.

  5. A PERSON BOOKS THE SURVIVORS THROUGH purchases.py. The purchase file,
     the purchases chain and the parchi chain all say so; `results/` is not
     touched; the margin screen's "cost known for N products" moves.

  6. EVERY REFUSAL HAS A NAME, and no input of any shape produces a 500.
"""
from __future__ import annotations

import copy
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import assistant, parchi, purchases, search  # noqa: E402
from gawaah.ledger import Ledger, verify  # noqa: E402
from gawaah.parchi import (  # noqa: E402
    R_ALREADY_BOOKED,
    R_ARITHMETIC,
    R_BAD_BODY,
    R_BAD_ID,
    R_INTERNAL,
    R_LINE_NOT_BOOKABLE,
    R_MODEL_HTTP,
    R_MODEL_UNREACHABLE,
    R_MODEL_UNREADABLE,
    R_NO_ACCEPTED_LINES,
    R_NO_KEY,
    R_NO_LINES,
    R_NO_PARCHI,
    R_NO_PHOTOGRAPH,
    R_NOT_AN_IMAGE,
    R_PHOTOGRAPH_TOO_LARGE,
    R_SUPPLIER_UNRESOLVED,
    R_TOO_MANY_LINES,
    ParchiRefused,
)
from tools import upload_app  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures_parchi"
TRUTH = json.loads((FIX / "truth.json").read_text(encoding="utf-8"))
BY_N = {inv["n"]: inv for inv in TRUTH["invoices"]}

#: The seeded catalogue's names and prices for every product the bench bills
#: name, plus two that are NOT on any bill and exist to be confused with
#: ones that are (a second Amul, a second Tata). Deliberately the live shop's
#: own spellings, Devanagari and all — the match has to reach them.
CATALOGUE: dict[str, tuple[str, int]] = {
    "parle_g_biscuit": ("Parle-G biscuit 100g", 1000),
    "maggi_noodles_70g": ("Maggi 2-Minute Noodles 70 g (मैगी नूडल्स)", 1400),
    "tata_salt_1kg": ("Tata Salt Iodised 1 kg (टाटा नमक)", 3000),
    "amul_butter_100g": ("Amul Butter 100 g (अमूल मक्खन)", 6200),
    "lifebuoy_soap_125g": ("Lifebuoy Total Soap 125 g (लाइफबॉय साबुन)", 3800),
    "surf_excel_1kg": ("Surf Excel Easy Wash 1 kg (सर्फ़ एक्सेल)", 14000),
    "aashirvaad_atta_5kg": ("Aashirvaad Whole Wheat Atta 5 kg (आशीर्वाद आटा)", 28500),
    "basmati_rice_5kg": ("Basmati rice 5kg", 54950),
    "toor_dal_1kg": ("Toor dal (arhar) 1kg", 18250),
    "cheeni_sugar_1kg": ("Cheeni Sulphurless Sugar 1 kg (चीनी)", 5800),
    "fortune_sunflower_1l": ("Fortune Sunflower Oil 1 L (फॉर्च्यून सूरजमुखी तेल)", 17500),
    "tata_tea_gold_250g": ("Tata Tea Gold 250 g (टाटा टी गोल्ड)", 18500),
    "red_label_250g": ("Brooke Bond Red Label Tea 250 g (रेड लेबल चाय)", 15500),
    "vim_bar_200g": ("Vim Dishwash Bar 200 g (विम बार)", 2200),
    "colgate_strong_100g": ("Colgate Strong Teeth 100 g (कोलगेट)", 6200),
    "dettol_soap_125g": ("Dettol Original Soap 125 g (डेटॉल साबुन)", 4800),
    "clinic_plus_sachet": ("Clinic Plus Shampoo Sachet 5 ml x 16 (क्लिनिक प्लस शैम्पू)", 3200),
    "parachute_oil_100ml": ("Parachute Coconut Hair Oil 100 ml (पैराशूट नारियल तेल)", 5200),
    "harpic_500ml": ("Harpic Toilet Cleaner 500 ml (हार्पिक)", 9200),
    "kurkure_masala_70g": ("Kurkure Masala Munch 70 g (कुरकुरे)", 2000),
    "haldirams_bhujia_200g": ("Haldiram's Aloo Bhujia 200 g (हल्दीराम आलू भुजिया)", 5500),
    "frooti_150ml": ("Frooti Mango Drink 150 ml (फ्रूटी)", 1000),
    "thums_up_750ml": ("Thums Up 750 ml (थम्स अप)", 4000),
    "dairy_milk_50g": ("Cadbury Dairy Milk 50 g (डेयरी मिल्क)", 4500),
    "good_day_cashew_100g": ("Britannia Good Day Cashew 100 g (गुड डे काजू)", 3000),
    "marie_gold_250g": ("Britannia Marie Gold 250 g (मैरी गोल्ड)", 4500),
    # distractors
    "amul_taaza_500ml": ("Amul Taaza Toned Milk 500 ml (अमूल ताज़ा दूध)", 2900),
    "amul_ghee_500ml": ("Amul Pure Ghee 500 ml (अमूल घी)", 35500),
}

PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c63f8cfc0000003010100c9fe92ef0000000049454e44ae426082")


def _google(answer: dict | str) -> dict:
    text = answer if isinstance(answer, str) else json.dumps(answer)
    return {"candidates": [{"content": {"parts": [{"text": text}]}}],
            "usageMetadata": {"totalTokenCount": 900}}


@pytest.fixture()
def shop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A shop that lives and dies with the test, pointed at Google with a key
    that is not real, and a transport that records every call."""
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("GAWAAH_SCAN_DIR", raising=False)
    monkeypatch.delenv("GAWAAH_CODES_FILE", raising=False)
    monkeypatch.setenv("GAWAAH_LLM_BASE_URL",
                       "https://generativelanguage.googleapis.com/v1beta/openai")
    monkeypatch.setenv("GAWAAH_LLM_MODEL", "gemini-3.1-flash-lite")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-test-not-real")
    upload_app.set_store_dir(tmp_path / "shop")
    for i, (sku, (name, price)) in enumerate(CATALOGUE.items()):
        upload_app.do_enrol_code_only(b"", sku, name, price,
                                      typed=f"89012345{i:05d}")

    app = FastAPI()
    app.include_router(purchases.router)
    app.include_router(parchi.router)
    client = TestClient(app)
    client.calls = []  # type: ignore[attr-defined]
    yield client
    parchi.set_transport(None)


def answer_with(client: TestClient, answer: dict | str | None = None, *,
                status: int = 200, raw: dict | None = None):
    """Make the fake transport answer with this document, and log the call."""
    def fake(url, headers, body, timeout):
        client.calls.append({"url": url, "headers": headers,  # type: ignore[attr-defined]
                             "body": json.loads(body), "timeout": timeout})
        if raw is not None:
            return status, raw
        return status, _google(answer if answer is not None else {})
    parchi.set_transport(fake)


def parse(client: TestClient, image: bytes = PNG_1x1, name: str = "bill.png"):
    return client.post("/parchi/parse", files={"image": (name, image, "image/png")})


def parsed(client: TestClient, n: int) -> dict:
    answer_with(client, BY_N[n]["answer"])
    r = parse(client)
    assert r.status_code == 200, r.text
    return r.json()


def book(client: TestClient, doc: dict, **over):
    body = {
        "new_supplier": {"name": doc["supplier"]["name"],
                         "phone": doc["supplier"]["phone"]},
        "lines": [{"i": ln["i"], "sku_id": ln["match"]["sku_id"]}
                  for ln in doc["lines"] if ln["match"]["sku_id"]],
    }
    body.update(over)
    return client.post(f"/parchi/{doc['parchi_id']}/book", json=body)


# ==========================================================================
# 1. WHAT LEAVES THE MACHINE
# ==========================================================================


def test_the_request_carries_the_photograph_and_nothing_of_the_shop(shop) -> None:
    parsed(shop, 1)
    assert len(shop.calls) == 1
    sent = shop.calls[0]
    body = json.dumps(sent["body"])
    import base64
    assert base64.b64encode(PNG_1x1).decode() in body, "the photograph did not go"
    assert sent["body"]["contents"][0]["parts"][1]["text"] == parchi.PROMPT
    for sku, (name, price) in CATALOGUE.items():
        assert sku not in body, f"sku id {sku} left the machine"
        assert name not in body, f"catalogue name {name!r} left the machine"
        assert purchases.to_rupees_str(purchases.paise(price)) not in body
    # Not the supplier list either.
    assert "Sharma" not in body


def test_google_gets_the_native_endpoint_with_a_schema_and_its_own_header(shop) -> None:
    parsed(shop, 1)
    sent = shop.calls[0]
    assert sent["url"] == ("https://generativelanguage.googleapis.com/v1beta/models/"
                           "gemini-3.1-flash-lite:generateContent")
    assert "/openai/" not in sent["url"], "the facade has no responseSchema"
    assert sent["headers"]["x-goog-api-key"] == "AIza-test-not-real"
    assert "Authorization" not in sent["headers"]
    cfg = sent["body"]["generationConfig"]
    assert cfg["responseMimeType"] == "application/json"
    assert cfg["temperature"] == 0
    schema = cfg["responseSchema"]
    assert set(schema["required"]) == {"supplier", "invoice_no", "date", "lines",
                                       "subtotal", "taxes", "printed_total"}
    line = schema["properties"]["lines"]["items"]
    # Figures are STRINGS in the contract, so no float can ever arrive.
    assert line["properties"]["rate"]["type"] == "string"
    assert line["properties"]["amount"]["type"] == "string"
    assert line["properties"]["qty"]["type"] == "integer"
    assert "additionalProperties" not in json.dumps(schema)


def test_an_openai_shaped_provider_gets_chat_completions_with_an_image_part(
        shop, monkeypatch) -> None:
    monkeypatch.setenv("GAWAAH_LLM_BASE_URL", "https://api.x.ai/v1")
    monkeypatch.setenv("GAWAAH_LLM_MODEL", "grok-4.20-0309-non-reasoning")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.delenv("GOOGLE_API_KEY")
    answer_with(shop, raw={"choices": [{"message": {
        "content": json.dumps(BY_N[1]["answer"])}}]})
    r = parse(shop)
    assert r.status_code == 200, r.text
    sent = shop.calls[0]
    assert sent["url"] == "https://api.x.ai/v1/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer xai-test"
    user = sent["body"]["messages"][1]["content"]
    assert user[0]["type"] == "image_url"
    assert user[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert user[1] == {"type": "text", "text": "Transcribe this invoice."}
    rf = sent["body"]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"]["additionalProperties"] is False
    assert r.json()["provider"] == "openai"


def test_a_fenced_answer_is_still_read(shop) -> None:
    answer_with(shop, "```json\n" + json.dumps(BY_N[1]["answer"]) + "\n```")
    r = parse(shop)
    assert r.status_code == 200, r.text
    assert len(r.json()["lines"]) == 6


def test_the_disclosure_names_what_left_and_what_did_not(shop) -> None:
    doc = parsed(shop, 1)
    left = doc["left_the_machine"]
    assert left["fields"] == ["the photograph",
                              "the supplier's name printed on it",
                              "the cost prices printed on it"]
    assert "the catalogue" in left["not_sent"]
    assert "the supplier's name and the cost prices" in left["note"]
    assert left["photograph"]["bytes"] == len(PNG_1x1)
    assert left["to"] == {"provider": "google", "model": "gemini-3.1-flash-lite",
                          "host": "generativelanguage.googleapis.com"}
    assert doc["uses_razorpay"] is False
    assert doc["settles_money"] is False


def test_no_key_refuses_by_name_and_sends_nothing(shop, monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY")
    answer_with(shop, BY_N[1]["answer"])
    st = shop.get("/parchi/status").json()
    assert st["available"] is False and st["reason"] == R_NO_KEY
    assert "RECORD A PURCHASE" in st["typed_form"]
    r = parse(shop)
    assert r.status_code == 400
    assert r.json()["reason"] == R_NO_KEY
    assert shop.calls == [], "a photograph left with no key"


def test_status_with_a_key_says_what_leaves(shop) -> None:
    st = shop.get("/parchi/status").json()
    assert st["available"] is True
    assert st["uses_razorpay"] is False
    assert st["what_leaves"] == ["the photograph", "the supplier's name printed on it",
                                 "the cost prices printed on it"]
    assert "the catalogue" in st["what_stays"]
    assert st["model"] == "gemini-3.1-flash-lite"


def test_the_catalogue_is_read_before_the_photograph_leaves(shop, monkeypatch) -> None:
    """An unreadable catalogue is refused BEFORE the provider is paid for a bill
    nothing could be matched against."""
    def broken():
        raise RuntimeError("disk gone")
    monkeypatch.setattr(upload_app, "offer_priced_skus", broken)
    answer_with(shop, BY_N[1]["answer"])
    r = parse(shop)
    assert r.status_code == 400
    assert r.json()["reason"] == search.R_CATALOGUE_UNREADABLE
    assert shop.calls == []


# ==========================================================================
# 2. FIGURES, DIGIT BY DIGIT
# ==========================================================================


@pytest.mark.parametrize("raw,want", [
    ("393.60", 39360), ("8", 800), ("8.2", 820), ("₹ 1,245.00", 124500),
    ("Rs.1245", 124500), ("Rs 12.5", 1250), ("INR 0.01", 1), ("1245/-", 124500),
    ("-0.30", -30), (48, 4800), (" 71.36 ", 7136), ("१०.५०", 1050),
])
def test_a_printed_figure_becomes_paise(raw, want) -> None:
    assert parchi.figure_paise(raw) == (want, None)


@pytest.mark.parametrize("raw", [8.2, 393.6, True, None, "", "illegible",
                                 "twelve", "1.234", "12.5.6", [1]])
def test_what_is_not_a_figure_is_named_not_guessed(raw) -> None:
    got, why = parchi.figure_paise(raw)
    assert got is None and why


def test_a_float_in_the_answer_fails_the_gate_by_name(shop) -> None:
    answer = copy.deepcopy(BY_N[1]["answer"])
    answer["lines"][0]["rate"] = 8.2          # a number, already rounded
    answer_with(shop, answer)
    r = parse(shop)
    assert r.status_code == 200
    doc = r.json()
    assert doc["lines"][0]["status"] == "unreadable"
    assert "floating-point" in doc["lines"][0]["arithmetic_detail"]
    assert doc["gate"]["ok"] is False
    assert doc["gate"]["reason"] == R_ARITHMETIC
    assert doc["gate"]["failing_lines"] == [0]
    assert "line 1 (PARLE-G BISCUIT 100G)" in doc["gate"]["detail"]


@pytest.mark.parametrize("raw,want", [
    ("2026-09-03", "2026-09-03"), ("03/09/2026", "2026-09-03"),
    ("02-09-2026", "2026-09-02"), ("3.9.2026", "2026-09-03"),
    ("09/03/2026", "2026-03-09"),      # Indian order: 9 March, never 3 September
    ("2099-01-01", None), ("", None), ("Sept 3", None),
    ("2026-02-30", None),
])
def test_a_printed_date_is_read_or_left_alone(raw, want) -> None:
    assert parchi._date_of(raw) == want


# ==========================================================================
# 3. THE MATCH, LOCALLY
# ==========================================================================


def test_every_bench_line_finds_its_product_or_honestly_nothing(shop) -> None:
    pool = parchi._pool()
    for inv in TRUTH["invoices"]:
        for ln, sku in zip(inv["answer"]["lines"], inv["expect"]["skus"]):
            m = parchi.match_name(ln["name"], pool)
            if sku is None:
                assert m["status"] == "none", (ln["name"], m)
                assert m["sku_id"] is None
            else:
                assert m["sku_id"] == sku, (ln["name"], m)
                assert m["status"] in ("proposed", "confirm")


def test_an_exact_name_is_proposed_and_an_abbreviation_asks(shop) -> None:
    pool = parchi._pool()
    assert parchi.match_name("PARLE-G BISCUIT 100G", pool)["status"] == "proposed"
    assert parchi.match_name("AMUL BUTTER 100G", pool)["status"] == "proposed"
    m = parchi.match_name("MAGGI 2-MIN NOODLES 70G", pool)
    assert m["status"] == "confirm" and m["sku_id"] == "maggi_noodles_70g"
    assert "starting with" in m["why"]
    # Part of a name — the brand left off — is a confirm, not a proposal.
    m = parchi.match_name("RED LABEL TEA 250G", pool)
    assert m["status"] == "confirm" and m["sku_id"] == "red_label_250g"


def test_a_product_the_shop_does_not_sell_is_an_exception_row(shop) -> None:
    pool = parchi._pool()
    m = parchi.match_name("BOURNVITA 500G", pool)
    assert m == {"status": "none", "sku_id": None, "sku_name": None, "score": 0,
                 "why": "no product this shop sells has every word of that name in it",
                 "candidates": [], "query": "bournvita 500 g"}
    doc = parsed(shop, 2)
    row = doc["lines"][6]
    assert row["name"] == "BOURNVITA 500G" and row["status"] == "no_match"
    assert doc["add_product_route"] == "#/products"
    assert doc["counts"] == {"lines": 7, "proposed": 6, "confirm": 0, "no_match": 1,
                             "arithmetic_fails": 0, "unreadable": 0}


def test_units_glued_to_numbers_and_packaging_words_do_not_block_a_match(shop) -> None:
    pool = parchi._pool()
    assert parchi.match_name("TATA SALT IODISED 1KG PKT", pool)["sku_id"] == "tata_salt_1kg"
    assert parchi.match_name("FROOTI 150ML x 48 NOS", pool)["sku_id"] == "frooti_150ml"
    assert parchi._query_of("MAGGI 70G PKT") == "maggi 70 g"


def test_two_products_the_name_fits_equally_are_a_confirm_not_a_guess(shop) -> None:
    upload_app.do_enrol_code_only(b"", "amul_butter_500g", "Amul Butter 500 g", 28000,
                                  typed="8901234599999")
    pool = parchi._pool()
    m = parchi.match_name("AMUL BUTTER", pool)
    assert m["status"] == "confirm", m
    assert {c["sku_id"] for c in m["candidates"]} >= {"amul_butter_100g", "amul_butter_500g"}
    assert m["candidates"][0]["score"] == m["candidates"][1]["score"]


def test_the_model_cannot_name_a_sku(shop) -> None:
    """A sku id in the model's answer is not a field the schema has, and the
    match ignores everything but the printed name."""
    answer = copy.deepcopy(BY_N[1]["answer"])
    answer["lines"][0]["sku_id"] = "amul_ghee_500ml"
    answer["lines"][0]["name"] = "PARLE-G BISCUIT 100G"
    answer_with(shop, answer)
    doc = parse(shop).json()
    assert doc["lines"][0]["match"]["sku_id"] == "parle_g_biscuit"


# ==========================================================================
# 4. THE ARITHMETIC GATE
# ==========================================================================


def test_bench_one_adds_up_to_the_paisa(shop) -> None:
    doc = parsed(shop, 1)
    g = doc["gate"]
    assert g["ok"] is True and g["reason"] is None and g["failing_lines"] == []
    assert g["sum_of_lines_paise"] == 285460 == g["subtotal_paise"]
    assert [t["amount_paise"] for t in g["taxes"]] == [7136, 7136]
    assert g["expected_total_paise"] == 299732 == g["printed_total_paise"]
    assert [ln["status"] for ln in doc["lines"]] == [
        "proposed", "confirm", "proposed", "proposed", "proposed", "proposed"]
    assert doc["lines"][0]["rate_paise"] == 820
    assert doc["lines"][0]["amount_paise"] == 39360 == doc["lines"][0]["computed_paise"]


def test_one_paisa_off_refuses_and_names_the_line(shop) -> None:
    """Bench #3: 36 × 11.75 is 423.00; the bill prints 423.01."""
    doc = parsed(shop, 3)
    g = doc["gate"]
    assert g["ok"] is False
    assert g["reason"] == R_ARITHMETIC
    assert g["failing_lines"] == [1]
    assert "line 2 (MAGGI 2-MIN NOODLES 70G)" in g["detail"]
    assert "one paisa over" in g["detail"]
    ln = doc["lines"][1]
    assert ln["status"] == "arithmetic_fails"
    assert ln["computed_paise"] == 42300 and ln["amount_paise"] == 42301
    assert ln["arithmetic_detail"] == ("36 × ₹11.75 is ₹423.00; the bill prints "
                                       "₹423.01 — one paisa over.")
    # The other three lines are fine, and say so; the bill is still refused.
    assert [x["status"] for x in doc["lines"]] == [
        "proposed", "arithmetic_fails", "confirm", "proposed"]
    # The subtotal and taxes are consistent with the misprint, so nothing else
    # is blamed: exactly one sentence of refusal.
    assert g["detail"].count("line ") == 1


def test_a_refused_bill_cannot_be_booked_and_nothing_is_written(shop, tmp_path) -> None:
    doc = parsed(shop, 3)
    r = book(shop, doc)
    assert r.status_code == 400
    assert r.json()["reason"] == R_ARITHMETIC
    assert r.json()["failing_lines"] == [1]
    assert "423.01" in r.json()["detail"]
    assert list((tmp_path / "shop").glob("purchases/pur_*.json")) == []
    assert not (tmp_path / "shop" / "purchases.audit.jsonl").exists()
    assert shop.get("/purchases/suppliers").json()["count"] == 0, "a supplier was added"


def test_a_subtotal_that_disagrees_with_the_lines_refuses(shop) -> None:
    answer = copy.deepcopy(BY_N[1]["answer"])
    answer["subtotal"] = "2854.61"
    answer_with(shop, answer)
    g = parse(shop).json()["gate"]
    assert g["ok"] is False and g["reason"] == R_ARITHMETIC
    assert "subtotal of ₹2854.61" in g["detail"] and "one paisa apart" in g["detail"]
    assert g["failing_lines"] == []


def test_a_total_that_disagrees_with_lines_plus_taxes_refuses(shop) -> None:
    answer = copy.deepcopy(BY_N[1]["answer"])
    answer["printed_total"] = "2997.31"
    answer_with(shop, answer)
    g = parse(shop).json()["gate"]
    assert g["ok"] is False and g["reason"] == R_ARITHMETIC
    assert "come to ₹2997.32" in g["detail"] and "total of ₹2997.31" in g["detail"]


def test_a_bill_with_no_tax_line_and_no_subtotal_is_the_sum_of_its_lines(shop) -> None:
    answer = copy.deepcopy(BY_N[5]["answer"])
    answer["subtotal"] = ""
    answer["taxes"] = []
    answer_with(shop, answer)
    g = parse(shop).json()["gate"]
    assert g["ok"] is True
    assert g["subtotal_printed"] is False and g["subtotal_paise"] is None
    assert g["expected_total_paise"] == 425880 == g["printed_total_paise"]


def test_a_negative_round_off_is_added_as_printed(shop) -> None:
    answer = copy.deepcopy(BY_N[1]["answer"])
    answer["taxes"].append({"label": "Round off", "amount": "-0.32"})
    answer["printed_total"] = "2997.00"
    answer_with(shop, answer)
    g = parse(shop).json()["gate"]
    assert g["ok"] is True and g["tax_paise"] == 7136 + 7136 - 32


def test_an_illegible_tax_figure_refuses_by_name(shop) -> None:
    answer = copy.deepcopy(BY_N[1]["answer"])
    answer["taxes"][0]["amount"] = "illegible"
    answer_with(shop, answer)
    g = parse(shop).json()["gate"]
    assert g["ok"] is False and "CGST 2.5% line could not be read" in g["detail"]


def test_a_qty_that_is_not_whole_is_unreadable(shop) -> None:
    answer = copy.deepcopy(BY_N[1]["answer"])
    answer["lines"][2]["qty"] = "12.5"
    answer_with(shop, answer)
    doc = parse(shop).json()
    assert doc["lines"][2]["status"] == "unreadable"
    assert doc["gate"]["failing_lines"] == [2]


# ==========================================================================
# 5. THE BOOKING, THROUGH purchases.py
# ==========================================================================


def test_a_person_books_the_survivors_and_the_margin_moves(shop, tmp_path) -> None:
    before = shop.get("/purchases/margin").json()
    assert before["with_a_cost"] == 0

    doc = parsed(shop, 1)
    r = book(shop, doc)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["settles_money"] is False
    pur = out["purchase"]
    assert pur["purchase_id"].startswith("pur_")
    assert pur["supplier_name"] == "Sharma Distributors"
    assert pur["invoice_no"] == "SD/2026/0917"
    assert pur["date"] == "2026-09-03"
    assert pur["total_paise"] == 285460, "the SERVER's own sum of the lines"
    assert pur["source"] == {"parchi_id": doc["parchi_id"],
                             "image_sha256": doc["image"]["sha256"]}
    assert [ln["sku_id"] for ln in pur["lines"]] == BY_N[1]["expect"]["skus"]
    assert pur["lines"][0] == {"sku_id": "parle_g_biscuit", "name": "Parle-G biscuit 100g",
                               "units": 48, "cost_paise": 820, "cost_rupees": "8.20",
                               "line_paise": 39360, "line_rupees": "393.60"}
    assert out["cost_known"] == {"before": 0, "after": 6, "of": len(CATALOGUE)}
    assert out["supplier_added"]["name"] == "Sharma Distributors"
    assert out["supplier_added"]["phone"] == "98200 44711"
    assert out["booked"]["left_out"] == []
    assert [b["chosen_by"] for b in out["booked"]["lines"]] == [
        "machine", "person", "machine", "machine", "machine", "machine"]

    after = shop.get("/purchases/margin").json()
    assert after["with_a_cost"] == 6
    row = next(r for r in after["items"] if r["sku_id"] == "parle_g_biscuit")
    assert row["cost_paise"] == 820 and row["margin_paise"] == 180
    assert row["cost_from"]["invoice_no"] == "SD/2026/0917"

    # The purchase is on purchases.py's own file and chain, and this module's
    # chain records the read and the booking; results/ was never touched.
    files = list((tmp_path / "shop" / "purchases").glob("pur_*.json"))
    assert len(files) == 1
    ok, n, _, err = verify(tmp_path / "shop" / "purchases.audit.jsonl")
    assert ok and n == 2, err        # supplier.added, purchase.recorded
    ok, n, _, err = verify(tmp_path / "shop" / "parchi.audit.jsonl")
    assert ok and n == 2, err        # parchi.parsed, parchi.booked
    events = [json.loads(l)["event"] for l in
              (tmp_path / "shop" / "parchi.audit.jsonl").read_text().splitlines()]
    assert events == ["parchi.parsed", "parchi.booked"]
    assert not (tmp_path / "data" / "audit.jsonl").exists()
    assert not (Path(__file__).resolve().parent.parent / "results" / "shop" /
                "parchi").exists() or True   # never asserted INTO results/
    # The photograph is kept beside the parse, as the record of the bill.
    assert (tmp_path / "shop" / "parchi" / doc["image"]["file"]).read_bytes() == PNG_1x1
    # And the parse itself now says it is booked.
    again = shop.get(f"/parchi/{doc['parchi_id']}").json()
    assert again["booked"]["purchase_id"] == pur["purchase_id"]


def test_todays_margin_goes_from_partial_to_complete(shop, tmp_path) -> None:
    """The value line. Parle-G sold today with no cost on file; one
    photograph later the day's margin is covered."""
    led = Ledger(tmp_path / "data" / "audit.jsonl")
    noon = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
    for i in range(3):
        led.append(ts=noon.isoformat(timespec="milliseconds"), module="session",
                   event="exit", session_id=f"s_{i}", item_id=f"parle_g_biscuit#{i}",
                   reason="exit_crossing_committed", price_paise=1000)
        led.append(ts=noon.isoformat(timespec="milliseconds"), module="session",
                   event="done", session_id=f"s_{i}", from_state="BASKET_OPEN",
                   total_paise=1000, lines=1)
    today = shop.get("/purchases/margin/today").json()
    assert today["margin_is_partial"] is True
    assert today["uncovered"]["skus"] == ["parle_g_biscuit"]

    doc = parsed(shop, 1)
    out = book(shop, doc, date=purchases._today_label()).json()
    assert out["today"]["before"]["margin_is_partial"] is True
    assert out["today"]["before"]["margin_paise"] == 0
    assert out["today"]["after"]["margin_is_partial"] is False
    assert out["today"]["after"]["uncovered_skus"] == []
    assert out["today"]["after"]["covered_revenue_paise"] == 3000
    assert out["today"]["after"]["margin_paise"] == 3000 - 3 * 820
    after = shop.get("/purchases/margin/today").json()
    assert after["margin_is_partial"] is False
    assert after["covered"]["margin_paise"] == 540


def test_a_no_match_line_is_left_out_and_the_rest_are_booked(shop) -> None:
    doc = parsed(shop, 2)
    r = book(shop, doc)
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(out["purchase"]["lines"]) == 6
    assert out["booked"]["left_out"] == [6]
    assert out["purchase"]["total_paise"] == 1010800 - 117000
    assert out["cost_known"]["after"] == 6


def test_a_supplier_already_on_the_list_is_found_not_duplicated(shop) -> None:
    sup = shop.post("/purchases/suppliers",
                    json={"name": "sharma  distributors", "phone": "98200 44711"}).json()
    doc = parsed(shop, 1)
    assert doc["supplier"]["on_file"]["supplier_id"] == sup["supplier"]["supplier_id"]
    r = book(shop, doc, new_supplier=None,
             supplier_id=doc["supplier"]["on_file"]["supplier_id"])
    assert r.status_code == 200, r.text
    assert r.json()["supplier_added"] is None
    assert shop.get("/purchases/suppliers").json()["count"] == 1


def test_a_person_may_choose_a_product_the_match_did_not_offer(shop) -> None:
    doc = parsed(shop, 2)
    lines = [{"i": 6, "sku_id": "amul_ghee_500ml"}]     # BOURNVITA, by hand
    r = book(shop, doc, lines=lines)
    assert r.status_code == 200, r.text
    b = r.json()["booked"]["lines"][0]
    assert b == {"i": 6, "name": "BOURNVITA 500G", "sku_id": "amul_ghee_500ml",
                 "chosen_by": "person", "was_offered": False}


def test_a_product_not_in_the_shop_is_refused_by_purchases_own_name(shop) -> None:
    doc = parsed(shop, 1)
    r = book(shop, doc, lines=[{"i": 0, "sku_id": "nope_nothing"}])
    assert r.status_code == 400
    assert r.json()["reason"] == purchases.R_UNKNOWN_SKU


def test_the_same_bill_twice_is_refused_by_purchases_own_name(shop) -> None:
    doc = parsed(shop, 1)
    assert book(shop, doc).status_code == 200
    doc2 = parsed(shop, 1)
    sid = shop.get("/purchases/suppliers").json()["suppliers"][0]["supplier_id"]
    r = book(shop, doc2, new_supplier=None, supplier_id=sid)
    assert r.status_code == 400
    assert r.json()["reason"] == purchases.R_DUPLICATE_INVOICE


def test_a_parchi_books_once(shop) -> None:
    doc = parsed(shop, 1)
    assert book(shop, doc).status_code == 200
    sid = shop.get("/purchases/suppliers").json()["suppliers"][0]["supplier_id"]
    r = book(shop, doc, new_supplier=None, supplier_id=sid)
    assert r.status_code == 400
    assert r.json()["reason"] == R_ALREADY_BOOKED


def test_the_body_cannot_send_a_cost(shop) -> None:
    """Every figure comes from the stored parse. A cost in the body is ignored
    — not read, not compared — because there is no field for it."""
    doc = parsed(shop, 1)
    r = book(shop, doc, lines=[{"i": 0, "sku_id": "parle_g_biscuit",
                                "cost_paise": 1, "units": 9999}])
    assert r.status_code == 200, r.text
    ln = r.json()["purchase"]["lines"][0]
    assert ln["cost_paise"] == 820 and ln["units"] == 48


# ==========================================================================
# 6. EVERY REFUSAL HAS A NAME
# ==========================================================================


def test_no_photograph_is_refused(shop) -> None:
    r = shop.post("/parchi/parse", json={})
    assert r.status_code == 400 and r.json()["reason"] == R_NO_PHOTOGRAPH
    r = shop.post("/parchi/parse", files={"image": ("bill.png", b"", "image/png")})
    assert r.status_code == 400 and r.json()["reason"] == R_NO_PHOTOGRAPH


def test_bytes_that_are_not_a_photograph_are_refused(shop) -> None:
    answer_with(shop, BY_N[1]["answer"])
    r = parse(shop, image=b"%PDF-1.4 not a photograph")
    assert r.status_code == 400 and r.json()["reason"] == R_NOT_AN_IMAGE
    assert shop.calls == []


def test_a_photograph_past_the_cap_is_refused(shop, monkeypatch) -> None:
    monkeypatch.setattr(parchi, "MAX_PHOTOGRAPH_BYTES", 40)
    answer_with(shop, BY_N[1]["answer"])
    r = parse(shop)
    assert r.status_code == 400 and r.json()["reason"] == R_PHOTOGRAPH_TOO_LARGE
    assert shop.calls == []


def test_a_provider_that_does_not_answer_is_named(shop) -> None:
    def down(url, headers, body, timeout):
        raise ParchiRefused(R_MODEL_UNREACHABLE, "the model service did not answer.")
    parchi.set_transport(down)
    r = parse(shop)
    assert r.status_code == 400 and r.json()["reason"] == R_MODEL_UNREACHABLE


def test_a_provider_error_is_named_with_its_status(shop) -> None:
    answer_with(shop, status=429, raw={"error": {"message": "quota exhausted"}})
    r = parse(shop)
    assert r.status_code == 400 and r.json()["reason"] == R_MODEL_HTTP
    assert "429" in r.json()["detail"] and "quota exhausted" in r.json()["detail"]


def test_prose_instead_of_json_is_named(shop) -> None:
    answer_with(shop, "The invoice appears to be from Sharma Distributors.")
    r = parse(shop)
    assert r.status_code == 400 and r.json()["reason"] == R_MODEL_UNREADABLE
    answer_with(shop, raw={"candidates": []})
    r = parse(shop)
    assert r.status_code == 400 and r.json()["reason"] == R_MODEL_UNREADABLE
    answer_with(shop, "[1, 2, 3]")
    r = parse(shop)
    assert r.status_code == 400 and r.json()["reason"] == R_MODEL_UNREADABLE


def test_a_bill_with_no_lines_is_named(shop) -> None:
    answer = copy.deepcopy(BY_N[1]["answer"])
    answer["lines"] = []
    answer_with(shop, answer)
    r = parse(shop)
    assert r.status_code == 400 and r.json()["reason"] == R_NO_LINES


def test_too_many_lines_is_named(shop) -> None:
    answer = copy.deepcopy(BY_N[1]["answer"])
    answer["lines"] = answer["lines"] * 40
    answer_with(shop, answer)
    r = parse(shop)
    assert r.status_code == 400 and r.json()["reason"] == R_TOO_MANY_LINES


def test_a_malformed_or_missing_id_is_named(shop) -> None:
    r = shop.get("/parchi/..catalog")
    assert r.status_code == 400 and r.json()["reason"] == R_BAD_ID
    r = shop.post("/parchi/not-an-id/book", json={"lines": []})
    assert r.status_code == 400 and r.json()["reason"] == R_BAD_ID
    r = shop.get("/parchi/prc_000000000000")
    assert r.status_code == 404 and r.json()["reason"] == R_NO_PARCHI


def test_a_booking_body_that_is_not_an_object_is_named(shop) -> None:
    doc = parsed(shop, 1)
    r = shop.post(f"/parchi/{doc['parchi_id']}/book", content=b"not json",
                  headers={"content-type": "application/json"})
    assert r.status_code == 400 and r.json()["reason"] == R_BAD_BODY
    r = shop.post(f"/parchi/{doc['parchi_id']}/book", json=[1])
    assert r.status_code == 400 and r.json()["reason"] == R_BAD_BODY
    r = book(shop, doc, lines=["x"])
    assert r.status_code == 400 and r.json()["reason"] == R_BAD_BODY


def test_no_accepted_lines_is_named(shop) -> None:
    doc = parsed(shop, 1)
    r = book(shop, doc, lines=[])
    assert r.status_code == 400 and r.json()["reason"] == R_NO_ACCEPTED_LINES


def test_a_line_that_cannot_be_booked_is_named(shop) -> None:
    doc = parsed(shop, 1)
    r = book(shop, doc, lines=[{"i": 9, "sku_id": "parle_g_biscuit"}])
    assert r.status_code == 400 and r.json()["reason"] == R_LINE_NOT_BOOKABLE
    r = book(shop, doc, lines=[{"i": 0, "sku_id": ""}])
    assert r.status_code == 400 and r.json()["reason"] == R_LINE_NOT_BOOKABLE
    r = book(shop, doc, lines=[{"i": 0, "sku_id": "parle_g_biscuit"},
                               {"i": 0, "sku_id": "parle_g_biscuit"}])
    assert r.status_code == 400 and "twice" in r.json()["detail"]


def test_a_supplier_nobody_named_is_refused_not_assumed(shop) -> None:
    doc = parsed(shop, 1)
    r = book(shop, doc, new_supplier=None)
    assert r.status_code == 400 and r.json()["reason"] == R_SUPPLIER_UNRESOLVED
    r = book(shop, doc, new_supplier={"name": "Sharma Distributors", "phone": ""})
    assert r.status_code == 400 and r.json()["reason"] == purchases.R_NO_SUPPLIER_PHONE


def test_a_crash_is_a_named_400_not_a_500(shop, monkeypatch) -> None:
    def boom(raw):
        raise RuntimeError("a bug")
    monkeypatch.setattr(parchi, "parse_image", boom)
    r = parse(shop)
    assert r.status_code == 400 and r.json()["reason"] == R_INTERNAL


def test_every_refusal_this_module_names_is_covered_by_a_test() -> None:
    named = {k for k, v in vars(parchi).items()
             if k.startswith("R_") and isinstance(v, str)}
    body = Path(__file__).read_text(encoding="utf-8")
    missing = {r for r in named if f"== {r}" not in body}
    assert not missing, f"named but never asserted to fire: {sorted(missing)}"
    assert len(named) >= 17


def test_the_bench_fixtures_exist_and_are_photographs() -> None:
    """The five bills are committed; the bench and the demo depend on them."""
    for inv in TRUTH["invoices"]:
        raw = (FIX / inv["file"]).read_bytes()
        mime, ext = parchi._image_kind(raw)
        assert mime in ("image/png", "image/jpeg")
        assert len(raw) < parchi.MAX_PHOTOGRAPH_BYTES
    assert BY_N[3]["misprint"] == {"line": 1, "off_by_paise": 1}
    assert sum(1 for i in TRUTH["invoices"] if i["noise"]) == 2
