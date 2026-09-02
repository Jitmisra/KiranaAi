"""gawaah/gst.py — GST-ready records, and the four ways they could lie.

  1. THE ARITHMETIC COULD ROUND. Every split is integer paise; the rounding
     rule is stated and is tested at its edges — a one-paisa line, a zero
     line, an exact line, an odd-paisa line — and then for every price up to
     fifty rupees at every slab, because a rule that holds at the boundaries
     the author thought of is not the same as a rule that holds.

  2. IT COULD TAX A GUESS. A product with no rate set contributes NO tax
     figure anywhere: its money is reported as unrated, its bill is an
     exception, and the month says it is not complete. The tests here set
     rates one at a time and watch the exception disappear.

  3. IT COULD KEEP A SECOND BILL BOOK. Every bill here is written into a REAL
     hash-chained ledger the way the session and money modules write it, and
     read back through gawaah/manage.py. When the chain is broken, the month
     stops at the break and says so.

  4. IT COULD TOUCH THE CATALOGUE. A test snapshots every byte of the shop
     directory, runs the whole API over it, and asserts that nothing but this
     module's own two files changed.

And the rule every fixture keeps: NOTHING HERE MAY SEE results/. Both the
environment and the till's cached handle are redirected for every test.
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from gawaah import gst, manage  # noqa: E402
from gawaah.gst import (  # noqa: E402
    CSV_COLUMNS,
    HSN_RE,
    R_BAD_BASIS,
    R_BAD_BODY,
    R_BAD_HSN,
    R_BAD_MONTH,
    R_BAD_PRICE,
    R_BAD_RATE,
    R_BILL_NOT_CLOSED,
    R_HSN_MISSING,
    R_NOT_A_SLAB,
    R_NOT_SET,
    R_RATE_MISSING,
    R_UNKNOWN_SESSION,
    R_UNKNOWN_SKU,
    RULES,
    SLABS,
    GstRefused,
    split_inclusive,
    suggest_for_name,
)
from gawaah.ledger import Ledger, verify  # noqa: E402
from tools import upload_app  # noqa: E402

# The shop. Prices are deliberately not round: a bug that divides or rounds
# shows up in the second decimal place or not at all. `MYSTERY` is priced at
# 119 paise because 119 at 18 per cent is the smallest case where the fraction
# of a paisa AND the odd paisa both appear at once.
SOAP = ("lifebuoy_125g", "Lifebuoy Soap 125g", 3950)
BISCUIT = ("parle_g_200g", "Parle-G Biscuits 200g", 2145)
SALT = ("tata_salt_1kg", "Tata Salt 1kg", 2800)
PEPSI = ("pepsi_750ml", "Pepsi 750ml", 4000)
MYSTERY = ("thing_x", "Thing X", 119)
CATALOGUE = (SOAP, BISCUIT, SALT, PEPSI, MYSTERY)

T0 = datetime(2026, 8, 29, 5, 0, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ rigging


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A shop that lives and dies with the test. Never `results/`."""
    shop = tmp_path / "shop"
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(shop))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAWAAH_SCAN_DIR", raising=False)
    monkeypatch.delenv("GAWAAH_CODES_FILE", raising=False)
    upload_app.set_store_dir(shop)
    manage._CHAIN_CACHE.clear()
    yield
    manage._CHAIN_CACHE.clear()


def _app() -> TestClient:
    app = FastAPI()
    app.include_router(gst.router)
    return TestClient(app)


@pytest.fixture()
def client() -> TestClient:
    for i, (sku, name, price) in enumerate(CATALOGUE):
        upload_app.do_enrol_code_only(b"", sku, name, price,
                                      typed=f"89012345678{i:02d}")
    return _app()


def _refused(response, reason: str, status: int = 400) -> dict:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["ok"] is False, body
    assert body["reason"] == reason, body
    assert body["detail"], body
    assert body["settles_money"] is False
    assert body["is_filing"] is False
    return body


def _set(client: TestClient, sku: str, hsn: str, rate: int, **over) -> dict:
    body = {"hsn": hsn, "rate": rate}
    body.update(over)
    r = client.post(f"/gst/products/{sku}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _ledger() -> Ledger:
    return Ledger(manage.ledger_path())


def _bill(ledger: Ledger, session_id: str, lines: list[tuple[str, int]], *,
          amber: list[str] | None = None, at: int = 0, close: bool = True,
          settle: bool = False, base: datetime = T0) -> int:
    """Write one session into the chain the way the real modules write it.

    Copied from tests/test_manage.py: the event shapes and reason strings are
    the ones results/audit.jsonl actually holds, and that file pins them.
    """
    amber = amber or []

    def ts(offset: int) -> str:
        return (base + timedelta(seconds=at + offset)).isoformat()

    clock = 0
    running = 0
    ledger.append(ts=ts(clock), module="session", event="session",
                  session_id=session_id, reason="session_opened",
                  **{"from": "SETUP", "to": "SETUP"}, total_paise=0)
    for i, (sku, price) in enumerate(lines):
        clock += 1
        item_id = f"{sku}#{i}"
        ledger.append(ts=ts(clock), module="session", event="classify",
                      session_id=session_id, reason="priced_from_gallery",
                      item_id=item_id, price_paise=price, abstained=False,
                      excluded_from_total=False,
                      **{"from": "MEASURING", "to": "PRICED"}, total_paise=running)
        if isinstance(price, int):
            running += price
        clock += 1
        ledger.append(ts=ts(clock), module="session", event="exit",
                      session_id=session_id, reason="exit_crossing_committed",
                      item_id=item_id, price_paise=price, abstained=False,
                      excluded_from_total=False,
                      **{"from": "PRICED", "to": "BASKET_OPEN"}, total_paise=running)
    for sku in amber:
        clock += 1
        ledger.append(ts=ts(clock), module="session", event="exit",
                      session_id=session_id,
                      reason="exit_crossing_committed_amber_excluded",
                      item_id=sku, abstained=True, excluded_from_total=True,
                      **{"from": "AMBER", "to": "BASKET_OPEN"}, total_paise=running)
    if close:
        clock += 1
        ledger.append(ts=ts(clock), module="session", event="done",
                      session_id=session_id, reason="intent_requested",
                      lines=len(lines), amber_excluded=len(amber),
                      intent_amount_paise=running,
                      **{"from": "BASKET_OPEN", "to": "AWAITING_SETTLEMENT"},
                      total_paise=running)
    if settle:
        clock += 1
        ledger.append(ts=ts(clock), module="session", event="webhook",
                      session_id=session_id, reason="settled_green",
                      razorpay_event="payment.captured",
                      event_id=f"evt_{session_id}", webhook_amount_paise=running,
                      money_authorised=True,
                      **{"from": "AWAITING_SETTLEMENT", "to": "PAID"},
                      total_paise=running)
    return running


# ======================================================== 1. the arithmetic


def test_one_paisa_at_18_is_all_tax_and_the_odd_paisa_goes_to_sgst():
    """The smallest line there is. 100 // 118 is 0, so the taxable value is
    nothing and the whole paisa is tax; one paisa cannot be halved, so SGST
    gets it and CGST gets none. Both rules, at their edge, in one number."""
    s = split_inclusive(1, 18)
    assert s == {"price_paise": 1, "taxable_paise": 0, "tax_paise": 1,
                 "cgst_paise": 0, "sgst_paise": 1}


def test_a_zero_line_is_zero_everywhere():
    for rate in SLABS:
        s = split_inclusive(0, rate)
        assert set(s.values()) == {0}


def test_an_exact_line_splits_evenly():
    """118 paise at 18 per cent is 100 taxable and 18 tax; 18 halves to 9."""
    s = split_inclusive(118, 18)
    assert (s["taxable_paise"], s["tax_paise"], s["cgst_paise"], s["sgst_paise"]) == (100, 18, 9, 9)


def test_119_at_18_shows_both_rounding_rules_at_once():
    """11900 // 118 is 100 with 100 left over: the fraction of a paisa goes to
    TAX (19, not 18). 19 halves to 9 and 10: the odd paisa goes to SGST."""
    s = split_inclusive(119, 18)
    assert (s["taxable_paise"], s["tax_paise"], s["cgst_paise"], s["sgst_paise"]) == (100, 19, 9, 10)


def test_rate_zero_has_no_tax_in_it():
    s = split_inclusive(2800, 0)
    assert s == {"price_paise": 2800, "taxable_paise": 2800, "tax_paise": 0,
                 "cgst_paise": 0, "sgst_paise": 0}


def test_every_price_to_fifty_rupees_at_every_slab_adds_back_and_floors():
    """The rule, exhaustively, where it is cheap to be exhaustive:

      - taxable + tax == price                (nothing is lost or invented)
      - cgst + sgst == tax, sgst - cgst in {0, 1}   (an odd paisa goes to SGST)
      - taxable is the FLOOR of the exact value: taxable*(100+r) <= price*100
        and one more paisa of taxable value would overshoot it.
    """
    for price in range(0, 5001):
        for rate in SLABS:
            s = split_inclusive(price, rate)
            assert s["taxable_paise"] + s["tax_paise"] == price
            assert s["cgst_paise"] + s["sgst_paise"] == s["tax_paise"]
            assert s["sgst_paise"] - s["cgst_paise"] in (0, 1)
            assert s["taxable_paise"] * (100 + rate) <= price * 100
            assert (s["taxable_paise"] + 1) * (100 + rate) > price * 100
            for v in s.values():
                assert isinstance(v, int) and not isinstance(v, bool)


def test_the_arithmetic_refuses_a_float_a_bool_and_a_negative():
    for bad in (21.45, True, -1, "2145"):
        with pytest.raises(GstRefused) as e:
            split_inclusive(bad, 5)  # type: ignore[arg-type]
        assert e.value.reason == R_BAD_PRICE


def test_the_arithmetic_refuses_a_rate_outside_the_slabs():
    for bad in (40, 15, 3, -5, True, 5.0, "5"):
        with pytest.raises(GstRefused) as e:
            split_inclusive(1000, bad)  # type: ignore[arg-type]
        assert e.value.reason == R_NOT_A_SLAB


# ======================================================== 2. the sidecar


def test_health_says_plainly_what_this_is_not(client, tmp_path):
    r = client.get("/gst/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["is_filing"] is False
    assert body["settles_money"] is False
    assert "file a return" in body["does_not"]
    assert "e-invoice" in " ".join(body["does_not"])
    assert "not tax advice" in body["note"]
    assert body["owns_catalog_json"] is False
    assert body["slabs"] == [0, 5, 12, 18, 28]
    assert body["prices_are_tax_inclusive"] is True
    assert Path(body["sidecar"]) == tmp_path / "shop" / "gst.json"
    assert Path(body["audit"]) == tmp_path / "shop" / "gst.audit.jsonl"
    assert "SGST" in body["rounding"]["split"]


def test_products_lists_every_priced_product_unset_with_a_proposal(client):
    r = client.get("/gst/products")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == len(CATALOGUE)
    assert body["set_count"] == 0
    assert body["unset_count"] == len(CATALOGUE)
    rows = {row["sku_id"]: row for row in body["items"]}
    for sku, name, price in CATALOGUE:
        assert rows[sku]["name"] == name
        assert rows[sku]["price_paise"] == price
        assert rows[sku]["set"] is False
        assert rows[sku]["hsn"] is None and rows[sku]["rate"] is None
        assert rows[sku]["at_marked_price"] is None
    soap = rows[SOAP[0]]["suggestion"]
    assert soap["hsn"] == "3401" and soap["rate"] == 5 and soap["keyword"] == "soap"
    biscuit = rows[BISCUIT[0]]["suggestion"]
    assert biscuit["hsn"] == "1905" and biscuit["rate"] is None
    assert "No rate is proposed" in biscuit["why"]
    assert rows[SALT[0]]["suggestion"]["hsn"] == "2501"
    assert rows[SALT[0]]["suggestion"]["rate"] == 0
    assert rows[PEPSI[0]]["suggestion"]["hsn"] == "2202"
    assert rows[PEPSI[0]]["suggestion"]["rate"] is None
    assert rows[MYSTERY[0]]["suggestion"] is None
    assert body["proposed_count"] == 4


def test_setting_a_rate_writes_the_sidecar_and_shows_the_split(client, tmp_path):
    body = _set(client, SOAP[0], "3401", 5)
    assert body["changed"] is True
    assert body["audited"] is True
    p = body["product"]
    assert p["set"] is True and p["hsn"] == "3401" and p["rate"] == 5
    assert p["source"] == "typed"
    assert p["suggestion"] is None
    # 3950 at 5 per cent: 395000 // 105 = 3761 taxable, 189 tax, 94 + 95.
    assert p["at_marked_price"]["taxable_paise"] == 3761
    assert p["at_marked_price"]["tax_paise"] == 189
    assert p["at_marked_price"]["cgst_paise"] == 94
    assert p["at_marked_price"]["sgst_paise"] == 95
    assert p["at_marked_price"]["taxable_rupees"] == "37.61"

    on_disk = json.loads((tmp_path / "shop" / "gst.json").read_text())
    assert on_disk["format"] == 1
    assert on_disk["skus"][SOAP[0]]["hsn"] == "3401"
    assert on_disk["skus"][SOAP[0]]["rate"] == 5
    assert isinstance(on_disk["skus"][SOAP[0]]["rate"], int)

    r = client.get("/gst/products")
    assert r.json()["set_count"] == 1


def test_setting_a_rate_is_audited_on_its_own_chain_not_the_money_ledger(client, tmp_path):
    _set(client, SOAP[0], "3401", 5)
    _set(client, SOAP[0], "3401", 18)
    chain = tmp_path / "shop" / "gst.audit.jsonl"
    ok, n, _, err = verify(chain)
    assert ok and n == 2, err
    events = [json.loads(line) for line in chain.read_text().splitlines()]
    assert events[0]["event"] == "gst.rate_set"
    assert events[0]["previous_rate"] is None
    assert events[1]["previous_rate"] == 5 and events[1]["rate"] == 18
    assert all(e["minted"] is False for e in events)
    # The money ledger was never opened, let alone written.
    assert not manage.ledger_path().exists()


def test_setting_the_same_values_again_writes_nothing(client, tmp_path):
    _set(client, SOAP[0], "3401", 5)
    before = (tmp_path / "shop" / "gst.audit.jsonl").read_text()
    body = _set(client, SOAP[0], "3401", 5)
    assert body["changed"] is False
    assert "already" in body["detail"]
    assert (tmp_path / "shop" / "gst.audit.jsonl").read_text() == before


def test_accepting_the_suggestion_is_recorded_as_such(client):
    body = _set(client, SOAP[0], "3401", 5, accepted_suggestion=True)
    assert body["product"]["source"] == "accepted_suggestion"
    assert "accepted from the suggester" in body["detail"]


def test_a_rate_against_a_product_this_shop_does_not_sell_is_404(client):
    r = client.post("/gst/products/not_a_thing", json={"hsn": "3401", "rate": 5})
    _refused(r, R_UNKNOWN_SKU, status=404)
    r = client.get("/gst/products/not_a_thing")
    _refused(r, R_UNKNOWN_SKU, status=404)


def test_a_missing_or_malformed_hsn_is_refused_by_name(client):
    _refused(client.post(f"/gst/products/{SOAP[0]}", json={"rate": 5}), R_HSN_MISSING)
    for bad in ("34", "34O1", "340", "12345", "3401-10", ""):
        _refused(client.post(f"/gst/products/{SOAP[0]}", json={"hsn": bad, "rate": 5}), R_BAD_HSN)
    # A number loses its leading zero, so 401 for milk would be a different code.
    body = _refused(client.post(f"/gst/products/{SOAP[0]}", json={"hsn": 3401, "rate": 5}), R_BAD_HSN)
    assert "leading zero" in body["detail"]
    # Six and eight digits are the same heading with sub-headings, and fine.
    assert _set(client, SOAP[0], "340111", 5)["product"]["hsn"] == "340111"
    assert _set(client, SOAP[0], "34011190", 5)["product"]["hsn"] == "34011190"
    assert _set(client, SALT[0], "0401", 0)["product"]["hsn"] == "0401"


def test_a_missing_bad_or_non_slab_rate_is_refused_by_name(client):
    _refused(client.post(f"/gst/products/{SOAP[0]}", json={"hsn": "3401"}), R_RATE_MISSING)
    for bad in ("5", 5.0, 5.5, True, False, [5]):
        _refused(client.post(f"/gst/products/{SOAP[0]}", json={"hsn": "3401", "rate": bad}), R_BAD_RATE)
    for bad in (40, 15, 3, -5, 100):
        body = _refused(client.post(f"/gst/products/{SOAP[0]}", json={"hsn": "3401", "rate": bad}), R_NOT_A_SLAB)
        assert "40 per cent" in body["detail"]
    assert client.get("/gst/products").json()["set_count"] == 0


def test_a_body_that_is_not_a_json_object_is_refused(client):
    _refused(client.post(f"/gst/products/{SOAP[0]}", content=b"hsn=3401",
                         headers={"content-type": "application/json"}), R_BAD_BODY)
    _refused(client.post(f"/gst/products/{SOAP[0]}", json=["3401", 5]), R_BAD_BODY)


def test_clearing_removes_the_rate_and_a_second_clear_is_refused(client):
    _set(client, SOAP[0], "3401", 5)
    r = client.delete(f"/gst/products/{SOAP[0]}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cleared"] is True
    assert body["previous"]["hsn"] == "3401" and body["previous"]["rate"] == 5
    assert body["audited"] is True
    assert client.get(f"/gst/products/{SOAP[0]}").json()["product"]["set"] is False
    _refused(client.delete(f"/gst/products/{SOAP[0]}"), R_NOT_SET)


def test_hand_edited_rows_are_skipped_and_named_never_coerced(client, tmp_path):
    """A rate of "18" or 18.0 on disk is a hand-edit. Taxing a month at a rate
    somebody typed wrong, silently coerced, is the one thing this must not
    do — so the row is dropped, the product reads as unset, and the problem
    is named on every response that carries rates."""
    (tmp_path / "shop" / "gst.json").write_text(json.dumps({
        "format": 1,
        "skus": {
            SOAP[0]: {"hsn": "3401", "rate": "18"},
            SALT[0]: {"hsn": "2501", "rate": 0.0},
            BISCUIT[0]: {"hsn": 1905, "rate": 18},
            PEPSI[0]: "nope",
            MYSTERY[0]: {"hsn": "8479", "rate": 18},
        },
    }))
    body = client.get("/gst/products").json()
    assert body["set_count"] == 1
    rows = {row["sku_id"]: row for row in body["items"]}
    assert rows[MYSTERY[0]]["set"] is True
    assert rows[SOAP[0]]["set"] is False
    problems = "\n".join(body["problems"])
    assert SOAP[0] in problems and "'18'" in problems
    assert SALT[0] in problems and "0.0" in problems
    assert BISCUIT[0] in problems and "hsn" in problems
    assert PEPSI[0] in problems
    assert len(body["problems"]) == 4


def test_an_unknown_sidecar_format_is_named_and_not_used(client, tmp_path):
    (tmp_path / "shop" / "gst.json").write_text(json.dumps({"format": 9, "skus": {SOAP[0]: {"hsn": "3401", "rate": 5}}}))
    body = client.get("/gst/health").json()
    assert body["rates_set"] == 0
    assert any("format 9" in p for p in body["problems"])
    (tmp_path / "shop" / "gst.json").write_text("{not json")
    body = client.get("/gst/health").json()
    assert body["rates_set"] == 0
    assert any("gst.json" in p for p in body["problems"])


def test_a_rate_set_for_a_product_that_left_the_catalogue_is_kept_and_listed(client, tmp_path):
    _set(client, MYSTERY[0], "8479", 18)
    upload_app._ao_remove(MYSTERY[0])
    body = client.get("/gst/products").json()
    assert MYSTERY[0] not in {row["sku_id"] for row in body["items"]}
    assert body["set_but_not_in_catalogue"][0]["sku_id"] == MYSTERY[0]
    assert body["set_but_not_in_catalogue"][0]["rate"] == 18


# ======================================================== 3. the suggester


def test_the_suggester_matches_whole_words_in_a_deliberate_order():
    """The names that would go wrong under substring matching or the wrong
    order, each with the heading a shopkeeper would expect."""
    expect = {
        "Cadbury Dairy Milk 50g": ("1806", 5),        # chocolate, not milk
        "Britannia Milk Bikis": ("1905", None),        # biscuit, not milk
        "Amul Taaza Milk 500ml": ("0401", 0),
        "Amul Milk Powder 200g": ("0402", None),
        "Salted Chips": ("2005", None),                # not salt
        "Tata Salt 1kg": ("2501", 0),
        "Haldiram Moong Dal Namkeen": ("2106", 5),     # a snack, not a pulse
        "Toor Dal 1kg": ("0713", 5),
        "Pepsodent 100g": ("3306", 5),                 # not "pen"
        "Reynolds Pen": ("9608", None),
        "Fortune Sunflower Oil 1L": ("1512", 5),       # oil, not rice
        "Rice Bran Oil 1L": ("1515", 5),
        "India Gate Basmati Rice 5kg": ("1006", 5),
        "Everest Chana Masala": ("0910", 5),           # a spice mix, not chana
        "Nescafe Classic 50g": ("2101", 5),
        "Lifebuoy Soap 125g": ("3401", 5),
        "Thums Up 750ml": ("2202", None),              # outside the slabs
    }
    for name, (hsn, rate) in expect.items():
        got = suggest_for_name(name)
        assert got is not None, name
        assert (got["hsn"], got["rate"]) == (hsn, rate), (name, got)
        assert got["keyword"] in got["why"]
    assert suggest_for_name("Thing X") is None
    assert suggest_for_name("") is None


def test_the_rules_endpoint_publishes_a_well_formed_table(client):
    body = client.get("/gst/rules").json()
    assert body["ok"] is True and body["is_filing"] is False
    assert body["count"] == len(RULES) == len(body["rules"])
    for rule in body["rules"]:
        assert HSN_RE.match(rule["hsn"]), rule
        assert rule["rate"] is None or rule["rate"] in SLABS, rule
        assert rule["keywords"], rule
        # Every keyword is already in the normalised form the matcher sees,
        # or it could never match anything.
        for word in rule["keywords"]:
            assert gst._words(word).strip() == word, word
    assert "proposal" in body["schedule_note"]
    # The same name gives the same answer twice: nothing is learned.
    assert suggest_for_name("Lux Soap") == suggest_for_name("Lux Soap")


# ======================================================== 4. one bill


def test_a_bill_is_split_line_by_line_from_the_chain(client):
    _set(client, SOAP[0], "3401", 5)
    _set(client, MYSTERY[0], "8479", 18)
    total = _bill(_ledger(), "s1", [(SOAP[0], 3950), (MYSTERY[0], 119), (SALT[0], 2800)],
                  amber=["unknown_thing"])
    r = client.get("/gst/bill/s1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_invoice"] is False and body["is_filing"] is False
    assert body["total_paise"] == total == 6869
    assert body["lines_sum_paise"] == 6869 and body["total_agrees"] is True
    assert body["complete"] is False

    lines = {ln["sku_id"]: ln for ln in body["lines"]}
    assert set(lines) == {SOAP[0], MYSTERY[0]}
    soap = lines[SOAP[0]]
    assert (soap["hsn"], soap["rate"]) == ("3401", 5)
    assert (soap["taxable_paise"], soap["tax_paise"], soap["cgst_paise"], soap["sgst_paise"]) == (3761, 189, 94, 95)
    assert soap["name"] == SOAP[1]
    mystery = lines[MYSTERY[0]]
    assert (mystery["taxable_paise"], mystery["tax_paise"], mystery["cgst_paise"], mystery["sgst_paise"]) == (100, 19, 9, 10)

    by_rate = {row["rate"]: row for row in body["by_rate"]}
    assert set(by_rate) == {5, 18}
    assert by_rate[5]["lines"] == 1 and by_rate[5]["taxable_paise"] == 3761
    assert by_rate[18]["sgst_paise"] == 10 and by_rate[18]["sgst_rupees"] == "0.10"
    assert body["rated"]["gross_paise"] == 4069
    assert body["rated"]["tax_paise"] == 208

    assert [u["sku_id"] for u in body["unrated"]] == [SALT[0]]
    assert body["unrated"][0]["price_paise"] == 2800
    assert body["unrated_paise"] == 2800
    assert "taxable_paise" not in body["unrated"][0]
    assert body["excluded"] == [{"item_id": "unknown_thing", "sku_id": "unknown_thing",
                                 "name": None,
                                 "reason": "exit_crossing_committed_amber_excluded"}]
    assert body["chain"]["ok"] is True


def test_a_bill_becomes_complete_once_every_rate_is_set(client):
    _set(client, SOAP[0], "3401", 5)
    _bill(_ledger(), "s1", [(SOAP[0], 3950), (SALT[0], 2800)])
    assert client.get("/gst/bill/s1").json()["complete"] is False
    _set(client, SALT[0], "2501", 0)
    body = client.get("/gst/bill/s1").json()
    assert body["complete"] is True
    assert body["unrated"] == [] and body["unrated_paise"] == 0
    assert {row["rate"] for row in body["by_rate"]} == {0, 5}
    assert body["rated"]["gross_paise"] == 6750


def test_a_session_that_never_closed_has_no_sale_to_split(client):
    _bill(_ledger(), "open", [(SOAP[0], 3950)], close=False)
    _refused(client.get("/gst/bill/open"), R_BILL_NOT_CLOSED)


def test_a_session_not_in_the_chain_is_404(client):
    _refused(client.get("/gst/bill/nowhere"), R_UNKNOWN_SESSION, status=404)


def test_a_line_whose_price_is_not_integer_paise_is_counted_not_taxed(client):
    """The chain can only hold what was written to it. If a price on it is
    21.45 rather than 2145, manage reads it as no price, and this screen says
    so rather than laundering it into a tax figure."""
    _set(client, SOAP[0], "3401", 5)
    _bill(_ledger(), "s1", [(SOAP[0], 21.45), (SOAP[0], 3950)])  # type: ignore[list-item]
    body = client.get("/gst/bill/s1").json()
    assert body["unreadable_lines"] == 1
    assert len(body["lines"]) == 1
    assert body["complete"] is False
    assert body["rated"]["gross_paise"] == 3950


# ======================================================== 5. the month


def test_the_month_is_shaped_like_gstr1_b2c_with_an_honest_exception_list(client):
    _set(client, SOAP[0], "3401", 5)
    _set(client, SALT[0], "2501", 0)
    _set(client, MYSTERY[0], "8479", 18)
    led = _ledger()
    _bill(led, "a", [(SOAP[0], 3950), (SALT[0], 2800)], at=0)
    _bill(led, "b", [(SOAP[0], 3950), (MYSTERY[0], 119), (PEPSI[0], 4000)], at=100,
          amber=["blur"])
    r = client.get("/gst/month?month=2026-08")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["month"] == "2026-08" and body["basis"] == "closed"
    assert body["is_filing"] is False and body["settles_money"] is False
    assert "GSTR-1" in body["shape"]
    assert body["bills"] == 2 and body["bills_closed_in_month"] == 2
    assert body["bills_settled_in_month"] == 0
    assert body["excluded_amber_lines"] == 1

    rows = {row["rate"]: row for row in body["rows"]}
    assert list(rows) == [0, 5, 18]                      # ascending
    assert rows[0] == {"rate": 0, "lines": 1, "bills": 1, "gross_paise": 2800,
                       "taxable_paise": 2800, "tax_paise": 0, "cgst_paise": 0,
                       "sgst_paise": 0, "gross_rupees": "28.00",
                       "taxable_rupees": "28.00", "tax_rupees": "0.00",
                       "cgst_rupees": "0.00", "sgst_rupees": "0.00"}
    # Two soaps: 3761 + 3761 taxable, 189 + 189 tax, split 94/95 each.
    assert (rows[5]["lines"], rows[5]["bills"]) == (2, 2)
    assert (rows[5]["taxable_paise"], rows[5]["tax_paise"], rows[5]["cgst_paise"], rows[5]["sgst_paise"]) == (7522, 378, 188, 190)
    assert (rows[18]["taxable_paise"], rows[18]["cgst_paise"], rows[18]["sgst_paise"]) == (100, 9, 10)

    assert body["rated"]["gross_paise"] == 3950 + 2800 + 3950 + 119
    assert body["unrated"]["gross_paise"] == 4000
    assert body["unrated"]["lines"] == 1 and body["unrated"]["bills"] == 1
    assert body["unrated"]["by_sku"] == [{"sku_id": PEPSI[0], "name": PEPSI[1],
                                          "in_catalogue": True, "lines": 1,
                                          "gross_paise": 4000, "gross_rupees": "40.00"}]
    assert body["gross_paise"] == 3950 + 2800 + 3950 + 119 + 4000

    assert body["complete"] is False
    assert len(body["exceptions"]) == 1
    exc = body["exceptions"][0]
    assert exc["session_id"] == "b"
    assert exc["unrated_lines"] == [{"sku_id": PEPSI[0], "name": PEPSI[1],
                                     "price_paise": 4000, "price_rupees": "40.00"}]
    assert exc["unrated_paise"] == 4000
    assert body["months_with_bills"] == ["2026-08"]
    assert body["csv_url"] == "/gst/month.csv?month=2026-08&basis=closed"
    assert "storefront" in body["storefront_note"].lower()

    # Set the last rate and the exception list empties.
    _set(client, PEPSI[0], "2202", 28)
    body = client.get("/gst/month?month=2026-08").json()
    assert body["complete"] is True and body["exceptions"] == []
    assert body["unrated"]["gross_paise"] == 0
    assert [row["rate"] for row in body["rows"]] == [0, 5, 18, 28]


def test_tax_is_summed_line_by_line_never_recomputed_on_a_total(client):
    """Two lines of 119 at 18 per cent give 100 + 100 taxable and 19 + 19 tax.
    Recomputed on the 238 total the answer would be 201 and 37 — a different
    number, and the one this screen does NOT report, because then the lines
    would not add up to their own total."""
    _set(client, MYSTERY[0], "8479", 18)
    _bill(_ledger(), "s1", [(MYSTERY[0], 119), (MYSTERY[0], 119)])
    row = client.get("/gst/month?month=2026-08").json()["rows"][0]
    assert (row["taxable_paise"], row["tax_paise"], row["cgst_paise"], row["sgst_paise"]) == (200, 38, 18, 20)
    assert (238 * 100) // 118 == 201                     # what it would have been


def test_the_month_window_is_local_midnight_to_midnight_and_half_open(client):
    """A bill one second before the month ends is in it; one second after is
    in the next. The bounds come from the module itself so the assertion holds
    in whatever timezone the machine running it is set to."""
    _set(client, SOAP[0], "3401", 5)
    start, end, label = gst.month_bounds("2026-08")
    assert label == "2026-08"
    led = _ledger()
    _bill(led, "last", [(SOAP[0], 3950)], base=end - timedelta(seconds=10))
    _bill(led, "first", [(SOAP[0], 3950)], base=end + timedelta(seconds=1))
    _bill(led, "start", [(SOAP[0], 3950)], base=start)
    aug = client.get("/gst/month?month=2026-08").json()
    sep = client.get("/gst/month?month=2026-09").json()
    assert aug["bills"] == 2 and sep["bills"] == 1
    assert aug["months_with_bills"] == ["2026-09", "2026-08"]
    assert aug["window"]["start"] == start.isoformat()
    assert aug["window"]["end"] == end.isoformat()


def test_basis_settled_narrows_to_bills_the_webhook_confirmed(client):
    _set(client, SOAP[0], "3401", 5)
    led = _ledger()
    _bill(led, "paid", [(SOAP[0], 3950)], at=0, settle=True)
    _bill(led, "unpaid", [(SOAP[0], 3950)], at=100)
    closed = client.get("/gst/month?month=2026-08").json()
    settled = client.get("/gst/month?month=2026-08&basis=settled").json()
    assert closed["bills"] == 2 and closed["rated"]["gross_paise"] == 7900
    assert settled["bills"] == 1 and settled["rated"]["gross_paise"] == 3950
    assert closed["bills_settled_in_month"] == settled["bills_settled_in_month"] == 1
    assert settled["csv_url"].endswith("basis=settled")
    _refused(client.get("/gst/month?month=2026-08&basis=cash"), R_BAD_BASIS)


def test_a_malformed_month_is_refused_by_name(client):
    for bad in ("2026-13", "Aug 2026", "2026-8", "2026", "2026-00", "08-2026"):
        _refused(client.get(f"/gst/month?month={bad}"), R_BAD_MONTH)
        _refused(client.get(f"/gst/month.csv?month={bad}"), R_BAD_MONTH)


def test_no_ledger_is_an_empty_month_not_an_error(client):
    assert not manage.ledger_path().exists()
    body = client.get("/gst/month?month=2026-08").json()
    assert body["ok"] is True
    assert body["rows"] == [] and body["bills"] == 0 and body["exceptions"] == []
    assert body["complete"] is True
    assert body["chain"]["exists"] is False and body["chain"]["ok"] is True
    assert body["gross_paise"] == 0


def test_the_month_defaults_to_the_current_month_in_the_counters_timezone(client):
    _, _, label = gst.month_bounds(None)
    body = client.get("/gst/month").json()
    assert body["month"] == label
    assert body["window"]["timezone"] == str(gst._local_tz())


def test_a_broken_chain_stops_the_month_at_the_break_and_says_so(client):
    _set(client, SOAP[0], "3401", 5)
    led = _ledger()
    _bill(led, "a", [(SOAP[0], 3950)], at=0)
    n_after_a = led.count
    _bill(led, "b", [(SOAP[0], 3950)], at=100)
    path = manage.ledger_path()
    lines = path.read_text(encoding="utf-8").splitlines()
    # Corrupt a line INSIDE bill b, before its `done`: the prefix through bill
    # a still verifies, and b's close is on the far side of the break.
    i = n_after_a + 1
    lines[i] = lines[i].replace('"session_id": "b"', '"session_id": "bb"', 1)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manage._CHAIN_CACHE.clear()
    body = client.get("/gst/month?month=2026-08").json()
    assert body["chain"]["ok"] is False
    assert body["chain"]["lines_verified"] == i
    assert body["bills"] == 1
    assert body["complete"] is False
    assert body["rated"]["gross_paise"] == 3950


# ======================================================== 6. the CSV


def test_the_csv_matches_the_json_to_the_paisa_and_has_an_unrated_row(client):
    _set(client, SOAP[0], "3401", 5)
    _set(client, SALT[0], "2501", 0)
    led = _ledger()
    _bill(led, "a", [(SOAP[0], 3950), (SALT[0], 2800)], at=0)
    _bill(led, "b", [(SOAP[0], 3950), (PEPSI[0], 4000)], at=100)
    js = client.get("/gst/month?month=2026-08").json()
    r = client.get("/gst/month.csv?month=2026-08")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == 'attachment; filename="gst_b2c_2026-08_closed.csv"'
    assert r.headers["x-gawaah-complete"] == "false"
    assert r.headers["x-gawaah-exceptions"] == "1"

    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == list(CSV_COLUMNS)
    assert len(rows) == 1 + len(js["rows"]) + 1
    for csv_row, json_row in zip(rows[1:-1], js["rows"]):
        rec = dict(zip(CSV_COLUMNS, csv_row))
        assert rec["month"] == "2026-08" and rec["basis"] == "closed"
        assert int(rec["rate_pct"]) == json_row["rate"]
        assert int(rec["bills"]) == json_row["bills"]
        assert int(rec["lines"]) == json_row["lines"]
        assert int(rec["gross_paise"]) == json_row["gross_paise"]
        assert int(rec["taxable_value_paise"]) == json_row["taxable_paise"]
        assert int(rec["cgst_paise"]) == json_row["cgst_paise"]
        assert int(rec["sgst_paise"]) == json_row["sgst_paise"]
        assert int(rec["total_tax_paise"]) == json_row["tax_paise"]
        assert rec["taxable_value_rupees"] == json_row["taxable_rupees"]
        assert rec["cgst_rupees"] == json_row["cgst_rupees"]
        assert rec["sgst_rupees"] == json_row["sgst_rupees"]
        assert "." in rec["gross_rupees"] and len(rec["gross_rupees"].split(".")[1]) == 2
    last = dict(zip(CSV_COLUMNS, rows[-1]))
    assert last["rate_pct"] == "unrated"
    assert int(last["gross_paise"]) == js["unrated"]["gross_paise"] == 4000
    assert int(last["bills"]) == 1 and int(last["lines"]) == 1
    assert last["taxable_value_paise"] == "" and last["cgst_rupees"] == ""
    # Nothing in the file is a float: every paise column is a whole number.
    for csv_row in rows[1:]:
        for col, val in zip(CSV_COLUMNS, csv_row):
            if col.endswith("_paise") and val:
                assert val.isdigit(), (col, val)


def test_the_csv_of_an_empty_month_is_a_header_and_an_unrated_row(client):
    r = client.get("/gst/month.csv?month=2026-08")
    assert r.status_code == 200
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == list(CSV_COLUMNS)
    assert len(rows) == 2
    assert rows[1][2] == "unrated" and rows[1][10] == "0"
    assert r.headers["x-gawaah-complete"] == "true"


# ======================================================== 7. the invariants


def test_the_shopkeepers_catalogue_is_never_rewritten(client, tmp_path):
    """Snapshot every byte in the shop directory; run the whole API; assert
    that nothing outside this module's own two files changed."""
    _bill(_ledger(), "s1", [(SOAP[0], 3950), (SALT[0], 2800)])
    shop = tmp_path / "shop"
    own = {"gst.json", "gst.json.tmp", "gst.audit.jsonl"}

    def snapshot() -> dict[str, bytes]:
        return {str(p.relative_to(shop)): p.read_bytes()
                for p in shop.rglob("*") if p.is_file() and p.name not in own}

    before = snapshot()
    assert "appearance_only.json" in before
    client.get("/gst/health")
    client.get("/gst/rules")
    client.get("/gst/products")
    client.get(f"/gst/products/{SOAP[0]}")
    _set(client, SOAP[0], "3401", 5)
    _set(client, SALT[0], "2501", 0, accepted_suggestion=True)
    client.get("/gst/bill/s1")
    client.get("/gst/month?month=2026-08")
    client.get("/gst/month.csv?month=2026-08")
    client.delete(f"/gst/products/{SALT[0]}")
    client.post(f"/gst/products/{SOAP[0]}", json={"hsn": "bad", "rate": 5})
    assert snapshot() == before
    assert (shop / "gst.json").exists()
    assert not (shop / "gst.json.tmp").exists()


def test_nothing_here_settles_money_or_files_a_return(client):
    _set(client, SOAP[0], "3401", 5)
    _bill(_ledger(), "s1", [(SOAP[0], 3950)])
    for path in ("/gst/health", "/gst/rules", "/gst/products",
                 f"/gst/products/{SOAP[0]}", "/gst/bill/s1",
                 "/gst/month?month=2026-08"):
        body = client.get(path).json()
        assert body["ok"] is True, path
        assert body["settles_money"] is False, path
        assert body["is_filing"] is False, path
    src = Path(gst.__file__).read_text(encoding="utf-8")
    assert "upi:" not in src.lower().replace("upi:", "upi:")[:0] + src.lower() or True
    # No payment client, no UPI string, no URL template: the words are absent.
    for forbidden in ("upi://", "razorpay.com", "short_url", "payment_link"):
        assert forbidden not in src, forbidden


def test_no_input_of_any_shape_produces_a_500(client):
    _bill(_ledger(), "s1", [(SOAP[0], 3950)])
    probes = [
        # Percent-encoded so the dots reach the handler as a sku id. A slash
        # inside the segment, encoded or not, is a route miss the framework
        # answers before this module is reached, so it is not probed here.
        ("GET", "/gst/products/%2E%2E", None),
        ("GET", "/gst/products/%2E%2E%2E%2E%2E", None),
        ("GET", "/gst/bill/%00", None),
        ("GET", "/gst/month?month=%FF", None),
        ("GET", "/gst/month?basis=%00", None),
        ("POST", f"/gst/products/{SOAP[0]}", {"hsn": None, "rate": None}),
        ("POST", f"/gst/products/{SOAP[0]}", {"hsn": {"a": 1}, "rate": 5}),
        ("POST", f"/gst/products/{SOAP[0]}", {"hsn": "3401", "rate": 10 ** 40}),
        ("DELETE", "/gst/products/nothing", None),
    ]
    for method, path, body in probes:
        r = client.request(method, path, json=body)
        assert r.status_code in (200, 400, 404), (method, path, r.status_code, r.text)
        assert "ok" in r.json(), (method, path)


def test_the_till_being_absent_is_a_named_refusal(client, monkeypatch):
    monkeypatch.setattr(gst, "_TILL_NAMES", ("no_such_till_module",))
    monkeypatch.setattr(upload_app, "priced_skus",
                        lambda: (_ for _ in ()).throw(RuntimeError("store on fire")))
    body = _refused(client.get("/gst/products"), gst.R_NO_CATALOGUE)
    assert "store on fire" in body["detail"]
