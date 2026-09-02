"""Tests for gawaah.weighed — loose goods by weight, in integer paise.

Four properties, in the order they would cost money if broken:

  1. INTEGER PAISE, ONE RULE. `line_paise` is `price * grams // 1000`, the
     remainder is dropped and goes to the customer, and the drop is never a
     whole paisa. Pinned at the boundaries and as a property over the whole
     range. A float anywhere is a named refusal, never a rounding.
  2. THE BROWSER NAMES A WEIGHT, THE SERVER PRICES IT. Grams as a whole number
     or kilograms as TEXT; never a float, never a price from the client.
  3. A WEIGHED LINE IS WRITTEN DOWN, on this module's OWN chain, in the shop
     directory the till points at — and it says it cannot be minted yet.
  4. A REFUSAL IS A RESULT. Every named refusal has a test and none is a 500.

Nothing here may see, let alone write, `results/`. The environment AND the
till's cached handle are both redirected for every test.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from gawaah import weighed  # noqa: E402
from gawaah.ledger import verify  # noqa: E402
from gawaah.weighed import (  # noqa: E402
    GRAMS_PER_KG,
    MAX_GRAMS,
    MAX_PRICE_PER_KG_PAISE,
    MIN_GRAMS,
    PRESETS_GRAMS,
    WeighedBook,
    WeighedRefused,
    WeighedSku,
    describe_grams,
    dropped_thousandths,
    grams_for,
    grams_from_kg_str,
    line_paise,
    load_weighed,
    save_weighed,
    weighed_path,
)
from tools import upload_app  # noqa: E402

# The shop these tests weigh out of. Prices chosen so the remainder is
# reachable with real-looking numbers: ₹45.99 a kilo does not divide into
# whole paise per gram.
RICE = ("basmati_rice", "Basmati rice", 4500)        # a 1 kg packet, ₹45.00
DAL = ("toor_dal", "Toor dal", 12000)                # a packet, ₹120.00
SUGAR = ("sugar_loose", "Sugar", 4200)
CATALOGUE = (RICE, DAL, SUGAR)

PER_KG_RICE = 4599      # ₹45.99 a kilo — the awkward one
PER_KG_DAL = 14000      # ₹140.00 a kilo — divides evenly


# ------------------------------------------------------------------ rigging


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A shop that lives and dies with the test. Never `results/`."""
    shop = tmp_path / "shop"
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(shop))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAWAAH_WEIGHED_FILE", raising=False)
    monkeypatch.delenv("GAWAAH_SCAN_DIR", raising=False)
    monkeypatch.delenv("GAWAAH_CODES_FILE", raising=False)
    upload_app.set_store_dir(shop)
    weighed.set_weighed_path(None)
    yield
    weighed.set_weighed_path(None)


@pytest.fixture()
def shop() -> TestClient:
    """The router mounted the way the orchestrator will mount it: bare."""
    for i, (sku, name, price) in enumerate(CATALOGUE):
        upload_app.do_enrol_code_only(b"", sku, name, price,
                                      typed=f"89012345678{i:02d}")
    app = FastAPI()
    app.include_router(weighed.router)
    return TestClient(app)


def _refused(r, reason: str | None = None, status: int = 400) -> dict:
    assert r.status_code == status, r.text
    doc = r.json()
    assert doc["ok"] is False
    assert doc["settles_money"] is False
    assert isinstance(doc["reason"], str) and doc["reason"]
    assert isinstance(doc["detail"], str) and doc["detail"]
    if reason is not None:
        assert doc["reason"] == reason, doc
    return doc


def _mark(client: TestClient, sku: str, **body) -> dict:
    r = client.post(f"/weighed/{sku}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ----------------------------------------------------- 1. the arithmetic


def test_exact_division_has_no_remainder():
    assert line_paise(4500, 2000) == 9000
    assert dropped_thousandths(4500, 2000) == 0


def test_remainder_is_dropped_and_goes_to_the_customer():
    # 4599 × 333 = 1531467 thousandths of a paisa = 1531.467 paise.
    assert line_paise(4599, 333) == 1531
    assert dropped_thousandths(4599, 333) == 467
    # The customer pays 1531, not 1532: the shop absorbs the part-paisa.


def test_floor_not_round_half_up():
    # 1500 paise/kg × 1 g = 1.5 paise. Half-up would say 2; the rule says 1.
    assert line_paise(1500, 1) == 1
    assert dropped_thousandths(1500, 1) == 500
    # 1999 × 1 g = 1.999 paise: the drop is 0.999, and still not a paisa.
    assert line_paise(1999, 1) == 1
    assert dropped_thousandths(1999, 1) == 999


def test_the_presets_at_the_awkward_price():
    # ₹45.99/kg. 250 g = 1149.75 → 1149; 500 g = 2299.5 → 2299;
    # 1 kg = 4599 exactly; 2 kg = 9198 exactly.
    assert [line_paise(PER_KG_RICE, g) for g in PRESETS_GRAMS] == [1149, 2299, 4599, 9198]
    assert [dropped_thousandths(PER_KG_RICE, g) for g in PRESETS_GRAMS] == [750, 500, 0, 0]


@settings(max_examples=400, deadline=None)
@given(per_kg=st.integers(min_value=1, max_value=MAX_PRICE_PER_KG_PAISE),
       grams=st.integers(min_value=MIN_GRAMS, max_value=MAX_GRAMS))
def test_property_the_drop_is_always_under_one_paisa(per_kg: int, grams: int):
    lp = line_paise(per_kg, grams)
    dropped = dropped_thousandths(per_kg, grams)
    exact = per_kg * grams                        # thousandths of a paisa
    assert isinstance(lp, int) and not isinstance(lp, bool)
    assert lp * GRAMS_PER_KG + dropped == exact
    assert 0 <= dropped < GRAMS_PER_KG            # never a whole paisa lost
    assert lp * GRAMS_PER_KG <= exact < (lp + 1) * GRAMS_PER_KG


def test_a_float_is_a_refusal_not_a_rounding():
    for bad in ((4500.0, 2000), (4500, 2000.0), (45.99, 1000)):
        with pytest.raises((WeighedRefused, Exception)):
            line_paise(*bad)
    with pytest.raises(WeighedRefused):
        line_paise(True, 1000)
    with pytest.raises(WeighedRefused):
        line_paise(4500, True)


def test_kilogram_text_is_read_digit_by_digit():
    assert grams_from_kg_str("2") == 2000
    assert grams_from_kg_str("2.5") == 2500
    assert grams_from_kg_str("0.25") == 250
    assert grams_from_kg_str(".25") == 250
    assert grams_from_kg_str("1.005") == 1005
    assert grams_from_kg_str(" 2.50 ") == 2500


def test_kilogram_text_refusals_are_named():
    with pytest.raises(WeighedRefused) as e:
        grams_from_kg_str("2.0005")
    assert e.value.reason == weighed.R_SUB_GRAM
    with pytest.raises(WeighedRefused) as e:
        grams_from_kg_str("two")
    assert e.value.reason == weighed.R_BAD_KG
    with pytest.raises(WeighedRefused) as e:
        grams_from_kg_str("-1")
    assert e.value.reason == weighed.R_WEIGHT_RANGE
    with pytest.raises(WeighedRefused) as e:
        grams_from_kg_str(2.5)             # a float is not text
    assert e.value.reason == weighed.R_BAD_KG
    with pytest.raises(WeighedRefused) as e:
        grams_from_kg_str("")
    assert e.value.reason == weighed.R_NO_WEIGHT


def test_describe_grams_uses_integer_arithmetic():
    assert describe_grams(250) == "250 g"
    assert describe_grams(1000) == "1 kg"
    assert describe_grams(1250) == "1.25 kg"
    assert describe_grams(1500) == "1.5 kg"
    assert describe_grams(2005) == "2.005 kg"
    assert describe_grams(100_000) == "100 kg"


# ------------------------------------------------- the spoken sentence


def test_grams_for_do_kilo_chawal():
    assert grams_for(2, "kilo") == 2000
    assert grams_for(1, "kg") == 1000
    assert grams_for(None, "kilo") == 1000
    assert grams_for(250, "gram") == 250
    assert grams_for(500, "gm") == 500


def test_grams_for_the_hindi_fractions():
    assert grams_for(None, "kilo", "aadha") == 500
    assert grams_for(None, "kilo", "dedh") == 1500
    assert grams_for(None, "kilo", "sava") == 1250
    assert grams_for(None, "kilo", "dhai") == 2500
    assert grams_for(None, "kilo", "paune") == 750
    assert grams_for(None, None, "pav") == 250
    assert grams_for(1, "kilo", "aadha") == 500     # "ek aadha kilo" is half a kilo


def test_grams_for_refuses_what_is_not_a_weight():
    with pytest.raises(WeighedRefused) as e:
        grams_for(2, "litre")
    assert e.value.reason == weighed.R_VOLUME_UNIT
    with pytest.raises(WeighedRefused) as e:
        grams_for(2, None)
    assert e.value.reason == weighed.R_NO_UNIT
    with pytest.raises(WeighedRefused) as e:
        grams_for(2, "packet")
    assert e.value.reason == weighed.R_NO_UNIT
    with pytest.raises(WeighedRefused) as e:
        grams_for(None, "gram", "aadha")
    assert e.value.reason == weighed.R_FRACTION_OF_A_GRAM
    with pytest.raises(WeighedRefused) as e:
        grams_for(2, "kilo", "paune")
    assert e.value.reason == weighed.R_FRACTION_AND_COUNT
    with pytest.raises(WeighedRefused) as e:
        grams_for(None, "kilo", "thoda")
    assert e.value.reason == weighed.R_UNKNOWN_FRACTION
    with pytest.raises(WeighedRefused) as e:
        grams_for(200, "kilo")
    assert e.value.reason == weighed.R_WEIGHT_RANGE


# ---------------------------------------------------- 2. marking a product


def test_mark_writes_the_file_in_the_tills_shop_dir(shop: TestClient, tmp_path: Path):
    doc = _mark(shop, RICE[0], price_per_kg_paise=PER_KG_RICE)
    assert doc["ok"] is True and doc["settles_money"] is False
    assert doc["replaced"] is False
    assert doc["price_per_kg_paise"] == PER_KG_RICE
    assert doc["price_per_kg_rupees"] == "45.99"
    assert doc["name"] == RICE[1]
    assert doc["in_catalogue"] is True
    assert doc["catalogue_price_paise"] == RICE[2]
    assert doc["audited"] is True
    # The file is next to the catalogue the till points at, never results/.
    p = weighed_path()
    assert p == tmp_path / "shop" / "weighed.json"
    assert p.exists()
    assert "results" not in p.parts
    on_disk = json.loads(p.read_text())
    assert on_disk["weighed"] == [{"sku_id": RICE[0], "price_per_kg_paise": PER_KG_RICE,
                                   "since": doc["since"]}]


def test_mark_prices_the_presets_from_the_server(shop: TestClient):
    doc = _mark(shop, RICE[0], price_per_kg_paise=PER_KG_RICE)
    ex = {e["grams"]: e for e in doc["examples"]}
    assert list(ex) == list(PRESETS_GRAMS)
    assert ex[250]["line_paise"] == 1149 and ex[250]["line_rupees"] == "11.49"
    assert ex[250]["dropped_thousandths_of_a_paisa"] == 750
    assert ex[2000]["line_paise"] == 9198 and ex[2000]["weight"] == "2 kg"


def test_mark_accepts_rupees_as_text_never_as_a_number(shop: TestClient):
    doc = _mark(shop, DAL[0], price_per_kg_rupees="140.00")
    assert doc["price_per_kg_paise"] == PER_KG_DAL
    # A JSON number for rupees is a float by the time it arrives: refused.
    _refused(shop.post(f"/weighed/{SUGAR[0]}", json={"price_per_kg_rupees": 42.0}),
             weighed.R_BAD_PRICE)
    _refused(shop.post(f"/weighed/{SUGAR[0]}", json={"price_per_kg_paise": 4200.0}),
             weighed.R_BAD_PRICE)
    _refused(shop.post(f"/weighed/{SUGAR[0]}", json={"price_per_kg_paise": True}),
             weighed.R_BAD_PRICE)
    _refused(shop.post(f"/weighed/{SUGAR[0]}", json={"price_per_kg_rupees": "42.005"}),
             weighed.R_BAD_PRICE)


def test_remark_replaces_the_price_and_audits_both_numbers(shop: TestClient):
    first = _mark(shop, RICE[0], price_per_kg_paise=4500)
    second = _mark(shop, RICE[0], price_per_kg_paise=PER_KG_RICE)
    assert second["replaced"] is True
    assert second["was_price_per_kg_paise"] == 4500
    assert second["since"] == first["since"]          # marked once, repriced since
    rows = [json.loads(l) for l in weighed.audit_path().read_text().splitlines()]
    marks = [r for r in rows if r["event"] == "weighed.marked"]
    assert marks[-1]["price_per_kg_paise"] == PER_KG_RICE
    assert marks[-1]["was_price_per_kg_paise"] == 4500
    assert marks[0]["was_price_per_kg_paise"] is None


def test_mark_refusals_are_named(shop: TestClient):
    _refused(shop.post("/weighed/not_a_product", json={"price_per_kg_paise": 100}),
             weighed.R_UNKNOWN_SKU)
    _refused(shop.post(f"/weighed/{RICE[0]}", json={}), weighed.R_NO_PRICE)
    _refused(shop.post(f"/weighed/{RICE[0]}", json={"price_per_kg_paise": 0}),
             weighed.R_PRICE_RANGE)
    _refused(shop.post(f"/weighed/{RICE[0]}",
                       json={"price_per_kg_paise": MAX_PRICE_PER_KG_PAISE + 1}),
             weighed.R_PRICE_RANGE)
    _refused(shop.post(f"/weighed/{RICE[0]}", content=b"not json",
                       headers={"Content-Type": "application/json"}),
             weighed.R_BAD_BODY)
    _refused(shop.post(f"/weighed/{RICE[0]}", json=[1, 2]), weighed.R_BAD_BODY)
    # A sku that reaches the handler but is not a product id. (A slash in the
    # path never reaches it at all: the framework answers 404 before this code.)
    _refused(shop.post("/weighed/!not-a-sku!", json={"price_per_kg_paise": 1}),
             weighed.R_BAD_SKU)
    _refused(shop.post("/weighed/-leading-dash", json={"price_per_kg_paise": 1}),
             weighed.R_BAD_SKU)


def test_a_reserved_word_cannot_be_a_weighed_sku(shop: TestClient):
    # 'price' would be unreachable behind POST /weighed/price. Said, not shadowed.
    upload_app.do_enrol_code_only(b"", "health", "Health drink", 100, typed="8901234567999")
    _refused(shop.post("/weighed/health", json={"price_per_kg_paise": 100}),
             weighed.R_RESERVED_SKU)


def test_unmark_removes_and_audits(shop: TestClient):
    _mark(shop, RICE[0], price_per_kg_paise=PER_KG_RICE)
    r = shop.delete(f"/weighed/{RICE[0]}")
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["removed"] is True and doc["was_price_per_kg_paise"] == PER_KG_RICE
    assert doc["remaining"] == 0
    assert load_weighed() == {}
    _refused(shop.delete(f"/weighed/{RICE[0]}"), weighed.R_NOT_WEIGHED, status=404)
    events = [json.loads(l)["event"] for l in weighed.audit_path().read_text().splitlines()]
    assert events == ["weighed.marked", "weighed.unmarked"]


def test_get_one_and_list(shop: TestClient):
    _refused(shop.get(f"/weighed/{RICE[0]}"), weighed.R_NOT_WEIGHED, status=404)
    _mark(shop, RICE[0], price_per_kg_paise=PER_KG_RICE)
    _mark(shop, DAL[0], price_per_kg_paise=PER_KG_DAL)
    one = shop.get(f"/weighed/{RICE[0]}").json()
    assert one["ok"] and one["price_per_kg_paise"] == PER_KG_RICE
    lst = shop.get("/weighed").json()
    assert lst["ok"] is True and lst["settles_money"] is False
    assert lst["count"] == 2
    assert [i["sku_id"] for i in lst["items"]] == [RICE[0], DAL[0]]
    assert lst["catalogue_known"] is True
    assert [m["sku_id"] for m in lst["markable"]] == [SUGAR[0]]
    assert lst["presets_grams"] == list(PRESETS_GRAMS)
    assert lst["mintable"] is False and lst["mint_note"]
    assert "customer" in lst["rule"]


def test_list_still_answers_with_no_file(shop: TestClient):
    lst = shop.get("/weighed").json()
    assert lst["ok"] and lst["count"] == 0 and lst["items"] == []
    assert len(lst["markable"]) == len(CATALOGUE)


def test_health_names_the_file_and_the_rule(shop: TestClient, tmp_path: Path):
    h = shop.get("/weighed/health").json()
    assert h["ok"] and h["module"] == "weighed"
    assert h["file"] == str(tmp_path / "shop" / "weighed.json")
    assert h["exists"] is False
    assert h["shop_dir"] == str(tmp_path / "shop")
    assert h["max_grams"] == MAX_GRAMS
    assert "customer" in h["rule"]
    _mark(shop, RICE[0], price_per_kg_paise=PER_KG_RICE)
    assert shop.get("/weighed/health").json()["exists"] is True


def test_the_cap_on_weighed_products(shop: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(weighed, "MAX_WEIGHED", 1)
    _mark(shop, RICE[0], price_per_kg_paise=PER_KG_RICE)
    _refused(shop.post(f"/weighed/{DAL[0]}", json={"price_per_kg_paise": PER_KG_DAL}),
             weighed.R_TOO_MANY)
    # Re-marking one already there is not a new one and is not capped.
    _mark(shop, RICE[0], price_per_kg_paise=4600)


# ------------------------------------------------ 3. pricing a weight


def test_price_a_weight_in_grams(shop: TestClient):
    _mark(shop, RICE[0], price_per_kg_paise=PER_KG_RICE)
    r = shop.post("/weighed/price", json={"sku_id": RICE[0], "grams": 333})
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["ok"] is True and doc["settles_money"] is False
    assert doc["written"] is False
    assert doc["grams"] == 333 and doc["weight"] == "333 g"
    assert doc["line_paise"] == 1531 and doc["line_rupees"] == "15.31"
    assert doc["exact_thousandths_of_a_paisa"] == 4599 * 333
    assert doc["dropped_thousandths_of_a_paisa"] == 467
    assert doc["arithmetic"] == "4599 × 333 // 1000 = 1531"
    assert doc["basket_line"] == {"sku_id": RICE[0], "name": "Basmati rice · 333 g",
                                  "price_paise": 1531, "qty": 1, "by": "weighed"}
    assert doc["mintable"] is False and "money service" in doc["mint_note"]
    # Nothing was written by a quote.
    assert not weighed.lines_dir().exists()


def test_price_a_weight_in_kilograms_as_text(shop: TestClient):
    _mark(shop, RICE[0], price_per_kg_paise=PER_KG_RICE)
    doc = shop.post("/weighed/price", json={"sku_id": RICE[0], "kg": "2"}).json()
    assert doc["grams"] == 2000 and doc["weight"] == "2 kg"
    assert doc["line_paise"] == 9198
    doc = shop.post("/weighed/price", json={"sku_id": RICE[0], "kg": "1.5"}).json()
    assert doc["grams"] == 1500 and doc["line_paise"] == 6898   # 6898.5 → 6898
    assert doc["dropped_thousandths_of_a_paisa"] == 500


def test_do_kilo_chawal_end_to_end(shop: TestClient):
    """The sentence the assistant used to refuse, priced: 2000 g × the per-kilo."""
    _mark(shop, RICE[0], price_per_kg_paise=PER_KG_RICE)
    grams = grams_for(2, "kilo")
    doc = shop.post("/weighed/price", json={"sku_id": RICE[0], "grams": grams}).json()
    assert doc["ok"] and doc["grams"] == 2000
    assert doc["line_paise"] == line_paise(PER_KG_RICE, 2000) == 9198


def test_the_client_cannot_author_a_price(shop: TestClient):
    """A price in the body is neither used nor an error: the server derives."""
    _mark(shop, RICE[0], price_per_kg_paise=PER_KG_RICE)
    doc = shop.post("/weighed/price", json={"sku_id": RICE[0], "grams": 1000,
                                            "price_paise": 1, "line_paise": 1}).json()
    assert doc["line_paise"] == PER_KG_RICE


def test_price_refusals_are_named(shop: TestClient):
    _refused(shop.post("/weighed/price", json={"sku_id": RICE[0], "grams": 500}),
             weighed.R_NOT_WEIGHED)
    _mark(shop, RICE[0], price_per_kg_paise=PER_KG_RICE)
    _refused(shop.post("/weighed/price", json={"sku_id": RICE[0]}), weighed.R_NO_WEIGHT)
    _refused(shop.post("/weighed/price", json={"sku_id": RICE[0], "grams": 500, "kg": "1"}),
             weighed.R_WEIGHT_TWICE)
    _refused(shop.post("/weighed/price", json={"sku_id": RICE[0], "grams": 500.5}),
             weighed.R_BAD_GRAMS)
    _refused(shop.post("/weighed/price", json={"sku_id": RICE[0], "grams": "500"}),
             weighed.R_BAD_GRAMS)
    _refused(shop.post("/weighed/price", json={"sku_id": RICE[0], "grams": 0}),
             weighed.R_WEIGHT_RANGE)
    _refused(shop.post("/weighed/price", json={"sku_id": RICE[0], "grams": MAX_GRAMS + 1}),
             weighed.R_WEIGHT_RANGE)
    _refused(shop.post("/weighed/price", json={"sku_id": RICE[0], "kg": "1.2345"}),
             weighed.R_SUB_GRAM)
    _refused(shop.post("/weighed/price", json={"sku_id": RICE[0], "kg": 1.5}),
             weighed.R_BAD_KG)
    _refused(shop.post("/weighed/price", json={"grams": 500}), weighed.R_BAD_SKU)
    _refused(shop.post("/weighed/price", json="nope"), weighed.R_BAD_BODY)


def test_a_line_worth_no_paise_is_refused_not_free(shop: TestClient):
    # ₹4.20 a kilo is 0.42 paise a gram: one gram comes to nothing.
    _mark(shop, SUGAR[0], price_per_kg_paise=420)
    _refused(shop.post("/weighed/price", json={"sku_id": SUGAR[0], "grams": 1}),
             weighed.R_WORTH_NOTHING)
    # Three grams is 1.26 paise → 1 paisa, and that is a line.
    doc = shop.post("/weighed/price", json={"sku_id": SUGAR[0], "grams": 3}).json()
    assert doc["ok"] and doc["line_paise"] == 1


# ---------------------------------------------- 4. the written line


def test_line_is_written_and_chained(shop: TestClient, tmp_path: Path):
    _mark(shop, RICE[0], price_per_kg_paise=PER_KG_RICE)
    r = shop.post("/weighed/line", json={"sku_id": RICE[0], "kg": "2"})
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["written"] is True and doc["audited"] is True
    assert doc["line_id"].startswith("wl_") and len(doc["line_id"]) == 15
    assert doc["line_paise"] == 9198
    assert doc["mintable"] is False
    assert doc["file"] == str(tmp_path / "shop" / "weighed" / f"{doc['line_id']}.json")
    on_disk = json.loads(Path(doc["file"]).read_text())
    assert on_disk["line_id"] == doc["line_id"]
    assert on_disk["line_paise"] == 9198 and on_disk["grams"] == 2000

    # Read back by id: the server's record, not the browser's memory.
    back = shop.get(f"/weighed/line/{doc['line_id']}").json()
    assert back["ok"] and back["line_paise"] == 9198 and back["basket_line"]["qty"] == 1

    # On THIS module's chain, in the shop directory, and it verifies.
    chain = weighed.audit_path()
    assert chain == tmp_path / "shop" / "weighed.audit.jsonl"
    ok, n, _, err = verify(chain)
    assert ok and err is None and n == 2
    rows = [json.loads(l) for l in chain.read_text().splitlines()]
    assert rows[-1]["event"] == "weighed.line"
    assert rows[-1]["line_paise"] == 9198 and rows[-1]["grams"] == 2000
    assert rows[-1]["minted"] is False
    assert rows[-1]["module"] == "weighed"
    # And never on the money service's log.
    assert not (Path(REPO) / "results" / "audit.jsonl").exists() or \
        doc["line_id"] not in (Path(REPO) / "results" / "audit.jsonl").read_text()


def test_two_weighings_are_two_lines(shop: TestClient):
    """Two scoops of the same rice must not merge into '2 × the first scoop'."""
    _mark(shop, RICE[0], price_per_kg_paise=PER_KG_RICE)
    a = shop.post("/weighed/line", json={"sku_id": RICE[0], "grams": 500}).json()
    b = shop.post("/weighed/line", json={"sku_id": RICE[0], "grams": 750}).json()
    assert a["line_id"] != b["line_id"]
    assert a["line_paise"] == 2299 and b["line_paise"] == 3449
    assert a["basket_line"]["sku_id"] == b["basket_line"]["sku_id"] == RICE[0]
    assert a["basket_line"]["name"] != b["basket_line"]["name"]


def test_line_read_refusals(shop: TestClient):
    _refused(shop.get("/weighed/line/wl_000000000000"), weighed.R_NO_LINE, status=404)
    _refused(shop.get("/weighed/line/wl_..weighed"), weighed.R_BAD_LINE_ID)
    _refused(shop.get("/weighed/line/prop_abcdefabcdef"), weighed.R_BAD_LINE_ID)
    _refused(shop.get("/weighed/line/wl_ABCDEFABCDEF"), weighed.R_BAD_LINE_ID)


def test_line_refuses_before_writing(shop: TestClient):
    _refused(shop.post("/weighed/line", json={"sku_id": RICE[0], "grams": 500}),
             weighed.R_NOT_WEIGHED)
    assert not weighed.lines_dir().exists()
    assert not weighed.audit_path().exists()


# ------------------------------------------------ the store and the book


def test_load_drops_a_record_it_cannot_trust(tmp_path: Path):
    p = tmp_path / "w.json"
    p.write_text(json.dumps({"weighed": [
        {"sku_id": "ok_one", "price_per_kg_paise": 100, "since": "x"},
        {"sku_id": "float_price", "price_per_kg_paise": 100.0},
        {"sku_id": "bool_price", "price_per_kg_paise": True},
        {"sku_id": "zero", "price_per_kg_paise": 0},
        {"sku_id": "../escape", "price_per_kg_paise": 100},
        "not a record",
    ]}))
    rows = load_weighed(p)
    assert list(rows) == ["ok_one"]
    assert (tmp_path / "missing.json").exists() is False
    assert load_weighed(tmp_path / "missing.json") == {}
    (tmp_path / "bad.json").write_text("{not json")
    assert load_weighed(tmp_path / "bad.json") == {}


def test_save_is_atomic_and_round_trips(tmp_path: Path):
    p = tmp_path / "deep" / "w.json"
    rows = {"a": WeighedSku("a", 4599, "t0"), "b": WeighedSku("b", 100, "t1")}
    save_weighed(rows, p)
    assert load_weighed(p) == rows
    assert not list(p.parent.glob("*.tmp.*"))
    doc = json.loads(p.read_text())
    assert doc["format"] == weighed.WEIGHED_FORMAT and doc["rule"] == weighed.RULE


def test_the_book_reloads_when_the_file_changes(tmp_path: Path):
    p = tmp_path / "w.json"
    book = WeighedBook(p)
    assert book.price_per_kg_paise("basmati_rice") is None
    assert book.line_paise("basmati_rice", 2000) is None      # not a guess
    assert len(book) == 0
    save_weighed({"basmati_rice": WeighedSku("basmati_rice", PER_KG_RICE, "t")}, p)
    assert book.price_per_kg_paise("basmati_rice") == PER_KG_RICE
    assert book.line_paise("basmati_rice", 2000) == 9198
    assert book.line_paise("basmati_rice", 333) == 1531
    # The book and the endpoint use one function, so they cannot disagree.
    assert book.line_paise("basmati_rice", 333) == line_paise(PER_KG_RICE, 333)


def test_env_override_and_explicit_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    explicit = tmp_path / "elsewhere" / "w.json"
    monkeypatch.setenv("GAWAAH_WEIGHED_FILE", str(explicit))
    assert weighed_path() == explicit
    weighed.set_weighed_path(tmp_path / "override.json")
    assert weighed_path() == tmp_path / "override.json"
    weighed.set_weighed_path(None)
    assert weighed_path() == explicit


def test_no_float_reaches_money_in_this_module():
    """The lint's strict rule, applied to this file even though it is not on
    the lint's whole-file list: no float literal, no float(), no `/`."""
    import ast

    src = (Path(REPO) / "gawaah" / "weighed.py").read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            pytest.fail(f"float literal at line {node.lineno}")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            pytest.fail(f"true division at line {node.lineno}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in ("float", "round"):
            pytest.fail(f"{node.func.id}() at line {node.lineno}")
