"""gawaah/purchases.py — suppliers, cost prices, and the margin that follows.

Five claims, because each one is a claim a demo can fake:

  1. A MISSING COST IS UNKNOWN, NOT ZERO. The tempting bug is to treat a
     product with no purchase behind it as costing nothing, which reports a
     shop making 100% on it. Every test below that touches an unrecorded
     product asserts a NULL margin, the sku named in `unknown`, and the day
     total flagged partial.

  2. THE SERVER TOTALS THE INVOICE. A client may state what it paid per unit —
     that fact is on a piece of paper and lives nowhere else — but a line total
     or a grand total sent along with it is compared and REFUSED, never
     believed and never quietly ignored.

  3. INTEGER PAISE. Every figure asserted here is an int or a rupee string.
     The fixtures use 21.45 and 39.50 on purpose: a bug that divides or rounds
     shows up in the second decimal place or not at all.

  4. EVERY REFUSAL HAS A NAME. There is one test per named refusal in the
     module, and a test that walks the module's own R_* constants to prove
     none was added without one.

  5. NOTHING HERE SETTLES MONEY. No gateway, no mint, no payable string, and
     `settles_money` false on every response.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import purchases  # noqa: E402
from gawaah.ledger import Ledger, verify  # noqa: E402
from gawaah.purchases import (  # noqa: E402
    MAX_COST_PAISE,
    MAX_LINES,
    MAX_UNITS,
    R_ALREADY_VOID,
    R_BAD_BODY,
    R_BAD_COST,
    R_BAD_DATE,
    R_BAD_INVOICE,
    R_BAD_PURCHASE_ID,
    R_BAD_SUPPLIER_ID,
    R_BAD_SUPPLIER_PHONE,
    R_BAD_UNITS,
    R_COST_DISAGREES,
    R_COST_TOO_LARGE,
    R_DUPLICATE_INVOICE,
    R_DUPLICATE_SUPPLIER,
    R_FUTURE_DATE,
    R_INTERNAL,
    R_LINE_TOTAL_DISAGREES,
    R_NO_BILLS,
    R_NO_CATALOGUE,
    R_NO_LINES,
    R_NO_PURCHASE,
    R_NO_SUPPLIER,
    R_NO_SUPPLIER_NAME,
    R_NO_SUPPLIER_PHONE,
    R_NO_TILL,
    R_NO_VOID_REASON,
    R_TOO_LONG,
    R_TOO_MANY_LINES,
    R_TOTAL_DISAGREES,
    R_UNITS_TOO_LARGE,
    R_UNKNOWN_SKU,
)
from tools import upload_app  # noqa: E402

# Sells for 21.45, bought for 14.00 -> 7.45 a packet.
BISCUIT = ("parle_g_200g", "Parle-G 200g", 2145)
# Sells for 39.50. Never bought, on purpose: this is the unknown-margin sku.
SOAP = ("lifebuoy_125g", "Lifebuoy 125g", 3950)


def _tz():
    return datetime.now().astimezone().tzinfo


def _stamp(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


@pytest.fixture()
def shop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A shop that lives and dies with the test.

    THREE knobs, not one. `set_store_dir` moves the till's cached handle;
    GAWAAH_SHOP_DIR covers anything that re-reads the environment; and
    GAWAAH_DATA_DIR moves the audit chain, which lives beside the shop and not
    in it. A harness that honoured only the first once destroyed a live
    catalogue, and one that forgot the third asserted against the day's real
    trading in results/.
    """
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("GAWAAH_SCAN_DIR", raising=False)
    monkeypatch.delenv("GAWAAH_CODES_FILE", raising=False)
    upload_app.set_store_dir(tmp_path / "shop")

    for i, (sku, name, price) in enumerate((BISCUIT, SOAP)):
        upload_app.do_enrol_code_only(b"", sku, name, price,
                                      typed=f"890123456789{i}")

    app = FastAPI()
    app.include_router(purchases.router)
    return TestClient(app)


def _supplier(client: TestClient, **over) -> dict:
    body = {"name": "Sharma Traders", "phone": "9876543210",
            "notes": "delivers Tuesdays"}
    body.update(over)
    r = client.post("/purchases/suppliers", json=body)
    assert r.status_code == 200, r.text
    return r.json()["supplier"]


def _buy(client: TestClient, sid: str, **over):
    body = {
        "supplier_id": sid,
        "lines": [{"sku_id": BISCUIT[0], "units": 10, "cost_paise": 1400}],
    }
    body.update(over)
    return client.post("/purchases", json=body)


def _bought(client: TestClient, sid: str, **over) -> dict:
    r = _buy(client, sid, **over)
    assert r.status_code == 200, r.text
    return r.json()["purchase"]


def _bill(led: Ledger, session: str, at: datetime,
          lines: list[tuple[str, int]]) -> None:
    """One closed bill in the chain, one ledger line per packet sold."""
    total = 0
    for i, (sku, price) in enumerate(lines):
        led.append(ts=_stamp(at), module="session", event="exit",
                   session_id=session, item_id=f"{sku}#{i}",
                   reason="exit_crossing_committed", price_paise=price)
        total += price
    led.append(ts=_stamp(at), module="session", event="done",
               session_id=session, from_state="BASKET_OPEN",
               total_paise=total, lines=len(lines))


# ------------------------------------------------------------- suppliers --


def test_a_supplier_is_recorded_and_listed(shop: TestClient) -> None:
    sup = _supplier(shop)
    assert sup["supplier_id"].startswith("sup_")
    assert sup["name"] == "Sharma Traders"

    body = shop.get("/purchases/suppliers").json()
    assert body["ok"] is True
    assert body["settles_money"] is False
    assert body["count"] == 1
    assert body["suppliers"][0]["bought_paise"] == 0


def test_a_supplier_with_no_name_is_refused(shop: TestClient) -> None:
    r = shop.post("/purchases/suppliers", json={"phone": "9876543210"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_NO_SUPPLIER_NAME


def test_a_supplier_with_no_phone_is_refused(shop: TestClient) -> None:
    r = shop.post("/purchases/suppliers", json={"name": "Sharma"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_NO_SUPPLIER_PHONE


def test_a_phone_that_cannot_be_dialled_is_refused(shop: TestClient) -> None:
    r = shop.post("/purchases/suppliers",
                  json={"name": "Sharma", "phone": "call me"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_SUPPLIER_PHONE


def test_the_same_supplier_typed_twice_is_refused(shop: TestClient) -> None:
    _supplier(shop)
    r = shop.post("/purchases/suppliers",
                  json={"name": "  sharma   TRADERS ", "phone": "9812345678"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_DUPLICATE_SUPPLIER


def test_an_over_long_field_is_refused_by_name(shop: TestClient) -> None:
    r = shop.post("/purchases/suppliers",
                  json={"name": "x" * 500, "phone": "9876543210"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_TOO_LONG


def test_a_supplier_id_that_is_not_one_is_refused(shop: TestClient) -> None:
    r = shop.get("/purchases/suppliers/../../catalog")
    assert r.status_code in (400, 404)
    if r.status_code == 400:
        assert r.json()["reason"] == R_BAD_SUPPLIER_ID

    r = shop.get("/purchases/suppliers/sup_nothex")
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_SUPPLIER_ID


def test_a_supplier_this_shop_does_not_have_is_a_404(shop: TestClient) -> None:
    r = shop.get("/purchases/suppliers/sup_0123456789ab")
    assert r.status_code == 404
    assert r.json()["reason"] == R_NO_SUPPLIER


def test_editing_a_supplier_keeps_the_name_on_old_invoices(
        shop: TestClient) -> None:
    sup = _supplier(shop)
    doc = _bought(shop, sup["supplier_id"])
    assert doc["supplier_name"] == "Sharma Traders"

    r = shop.post(f"/purchases/suppliers/{sup['supplier_id']}",
                  json={"name": "Sharma Traders Pvt Ltd"})
    assert r.status_code == 200, r.text
    assert r.json()["supplier"]["name"] == "Sharma Traders Pvt Ltd"
    # The phone was not sent, so it is not blanked.
    assert r.json()["supplier"]["phone"] == "9876543210"

    again = shop.get(f"/purchases/{doc['purchase_id']}").json()
    assert again["purchase"]["supplier_name"] == "Sharma Traders"


def test_editing_a_supplier_onto_another_ones_name_is_refused(
        shop: TestClient) -> None:
    first = _supplier(shop)
    second = _supplier(shop, name="Verma Stores", phone="9812345678")
    r = shop.post(f"/purchases/suppliers/{second['supplier_id']}",
                  json={"name": "Sharma Traders"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_DUPLICATE_SUPPLIER
    assert first["name"] == "Sharma Traders"


# ------------------------------------------------------------- purchases --


def test_the_server_totals_the_invoice(shop: TestClient) -> None:
    sup = _supplier(shop)
    doc = _bought(shop, sup["supplier_id"])
    assert doc["total_paise"] == 14000
    assert doc["total_rupees"] == "140.00"
    assert doc["lines"][0]["line_paise"] == 14000
    assert doc["lines"][0]["cost_rupees"] == "14.00"
    assert doc["units"] == 10
    assert doc["void"] is False


def test_a_cost_may_be_typed_in_rupees_as_a_string(shop: TestClient) -> None:
    sup = _supplier(shop)
    doc = _bought(shop, sup["supplier_id"], lines=[
        {"sku_id": BISCUIT[0], "units": 3, "cost_rupees": "14.05"}])
    assert doc["lines"][0]["cost_paise"] == 1405
    assert doc["total_paise"] == 4215


def test_one_invoice_may_carry_the_same_product_twice_at_two_rates(
        shop: TestClient) -> None:
    """A wholesaler's bill really does carry two lots of one item at two
    prices. Both lines are kept, both are totalled, and the later one is the
    later cost — merging them would invent a rate nobody was charged."""
    sup = _supplier(shop)
    doc = _bought(shop, sup["supplier_id"], lines=[
        {"sku_id": BISCUIT[0], "units": 6, "cost_paise": 1400},
        {"sku_id": BISCUIT[0], "units": 4, "cost_paise": 1450}])
    assert len(doc["lines"]) == 2
    assert doc["total_paise"] == 6 * 1400 + 4 * 1450
    row = {i["sku_id"]: i for i in
           shop.get("/purchases/margin").json()["items"]}[BISCUIT[0]]
    assert row["cost_paise"] == 1450


def test_a_client_total_that_disagrees_is_refused(shop: TestClient) -> None:
    sup = _supplier(shop)
    r = _buy(shop, sup["supplier_id"], total_paise=13999)
    assert r.status_code == 400
    assert r.json()["reason"] == R_TOTAL_DISAGREES
    assert shop.get("/purchases").json()["count"] == 0


def test_a_client_line_total_that_disagrees_is_refused(shop: TestClient) -> None:
    sup = _supplier(shop)
    r = _buy(shop, sup["supplier_id"], lines=[
        {"sku_id": BISCUIT[0], "units": 10, "cost_paise": 1400,
         "line_paise": 1400}])
    assert r.status_code == 400
    assert r.json()["reason"] == R_LINE_TOTAL_DISAGREES


def test_a_client_total_that_agrees_is_accepted(shop: TestClient) -> None:
    """The check is a comparison, not a ban: a page that shows a running total
    must be able to say what it showed."""
    sup = _supplier(shop)
    doc = _bought(shop, sup["supplier_id"], total_paise=14000)
    assert doc["total_paise"] == 14000


def test_two_spellings_of_the_same_cost_that_disagree_are_refused(
        shop: TestClient) -> None:
    sup = _supplier(shop)
    r = _buy(shop, sup["supplier_id"], lines=[
        {"sku_id": BISCUIT[0], "units": 1, "cost_paise": 1400,
         "cost_rupees": "14.50"}])
    assert r.status_code == 400
    assert r.json()["reason"] == R_COST_DISAGREES


def test_a_purchase_with_no_lines_is_refused(shop: TestClient) -> None:
    sup = _supplier(shop)
    r = _buy(shop, sup["supplier_id"], lines=[])
    assert r.status_code == 400
    assert r.json()["reason"] == R_NO_LINES


def test_too_many_lines_on_one_invoice_is_refused(shop: TestClient) -> None:
    sup = _supplier(shop)
    line = {"sku_id": BISCUIT[0], "units": 1, "cost_paise": 100}
    r = _buy(shop, sup["supplier_id"], lines=[line] * (MAX_LINES + 1))
    assert r.status_code == 400
    assert r.json()["reason"] == R_TOO_MANY_LINES


def test_buying_something_the_shop_was_never_taught_is_refused(
        shop: TestClient) -> None:
    sup = _supplier(shop)
    r = _buy(shop, sup["supplier_id"], lines=[
        {"sku_id": "ghost_500g", "units": 1, "cost_paise": 100}])
    assert r.status_code == 400
    assert r.json()["reason"] == R_UNKNOWN_SKU


@pytest.mark.parametrize("units", [0, -3, "ten", 1.5, True, None])
def test_units_that_are_not_a_count_are_refused(shop: TestClient, units) -> None:
    sup = _supplier(shop)
    r = _buy(shop, sup["supplier_id"], lines=[
        {"sku_id": BISCUIT[0], "units": units, "cost_paise": 100}])
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_UNITS


def test_an_implausible_number_of_units_is_refused(shop: TestClient) -> None:
    sup = _supplier(shop)
    r = _buy(shop, sup["supplier_id"], lines=[
        {"sku_id": BISCUIT[0], "units": MAX_UNITS + 1, "cost_paise": 100}])
    assert r.status_code == 400
    assert r.json()["reason"] == R_UNITS_TOO_LARGE


@pytest.mark.parametrize("cost", [0, -100, 14.5, True, "1400"])
def test_a_cost_that_is_not_positive_integer_paise_is_refused(
        shop: TestClient, cost) -> None:
    """A float is refused, and so is zero: a cost of nothing would make the
    product look like pure profit for as long as it stayed the latest one."""
    sup = _supplier(shop)
    r = _buy(shop, sup["supplier_id"], lines=[
        {"sku_id": BISCUIT[0], "units": 1, "cost_paise": cost}])
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_COST


def test_a_line_with_no_cost_at_all_is_refused(shop: TestClient) -> None:
    sup = _supplier(shop)
    r = _buy(shop, sup["supplier_id"], lines=[
        {"sku_id": BISCUIT[0], "units": 1}])
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_COST


def test_a_rupee_figure_typed_into_the_paise_field_is_caught(
        shop: TestClient) -> None:
    sup = _supplier(shop)
    r = _buy(shop, sup["supplier_id"], lines=[
        {"sku_id": BISCUIT[0], "units": 1,
         "cost_paise": MAX_COST_PAISE + 1}])
    assert r.status_code == 400
    assert r.json()["reason"] == R_COST_TOO_LARGE


def test_a_body_that_is_not_a_json_object_is_refused(shop: TestClient) -> None:
    r = shop.post("/purchases", content=b"not json",
                  headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_BODY

    r = shop.post("/purchases", json=[1, 2, 3])
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_BODY


def test_a_purchase_dated_tomorrow_is_refused(shop: TestClient) -> None:
    sup = _supplier(shop)
    tomorrow = (datetime.now(_tz()) + timedelta(days=1)).strftime("%Y-%m-%d")
    r = _buy(shop, sup["supplier_id"], date=tomorrow)
    assert r.status_code == 400
    assert r.json()["reason"] == R_FUTURE_DATE


@pytest.mark.parametrize("day", ["yesterday", "2026-13-01", "01-09-2026", 7])
def test_a_date_that_is_not_a_calendar_day_is_refused(
        shop: TestClient, day) -> None:
    sup = _supplier(shop)
    r = _buy(shop, sup["supplier_id"], date=day)
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_DATE


def test_an_invoice_number_with_odd_characters_is_refused(
        shop: TestClient) -> None:
    sup = _supplier(shop)
    r = _buy(shop, sup["supplier_id"], invoice_no="<script>x</script>")
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_INVOICE


def test_the_same_invoice_entered_twice_is_refused(shop: TestClient) -> None:
    """The classic double entry. Left alone it doubles the shop's costs and
    halves its margin, and nothing on the screen would say so."""
    sup = _supplier(shop)
    _bought(shop, sup["supplier_id"], invoice_no="ST/2026/114")
    r = _buy(shop, sup["supplier_id"], invoice_no="st/2026/114")
    assert r.status_code == 400
    assert r.json()["reason"] == R_DUPLICATE_INVOICE
    assert shop.get("/purchases").json()["count"] == 1


def test_the_same_invoice_number_from_a_different_supplier_is_fine(
        shop: TestClient) -> None:
    a = _supplier(shop)
    b = _supplier(shop, name="Verma Stores", phone="9812345678")
    _bought(shop, a["supplier_id"], invoice_no="114")
    _bought(shop, b["supplier_id"], invoice_no="114")
    assert shop.get("/purchases").json()["count"] == 2


def test_a_purchase_id_that_is_not_one_is_refused(shop: TestClient) -> None:
    r = shop.get("/purchases/not_an_id")
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_PURCHASE_ID


def test_a_purchase_this_shop_does_not_have_is_a_404(shop: TestClient) -> None:
    r = shop.get("/purchases/pur_0123456789ab")
    assert r.status_code == 404
    assert r.json()["reason"] == R_NO_PURCHASE


def test_purchases_are_listed_newest_first_with_a_total(
        shop: TestClient) -> None:
    sup = _supplier(shop)
    _bought(shop, sup["supplier_id"], date="2026-01-05")
    _bought(shop, sup["supplier_id"], date="2026-02-05")
    body = shop.get("/purchases").json()
    assert body["count"] == 2
    assert body["purchases"][0]["date"] == "2026-02-05"
    assert body["spent_paise"] == 28000
    assert body["spent_rupees"] == "280.00"


def test_the_supplier_page_shows_what_was_bought_from_them(
        shop: TestClient) -> None:
    a = _supplier(shop)
    b = _supplier(shop, name="Verma Stores", phone="9812345678")
    _bought(shop, a["supplier_id"])
    body = shop.get(f"/purchases/suppliers/{a['supplier_id']}").json()
    assert body["count"] == 1
    assert body["bought_paise"] == 14000
    assert shop.get(
        f"/purchases/suppliers/{b['supplier_id']}").json()["bought_paise"] == 0


def test_the_purchase_list_can_be_narrowed_to_one_supplier(
        shop: TestClient) -> None:
    a = _supplier(shop)
    b = _supplier(shop, name="Verma Stores", phone="9812345678")
    _bought(shop, a["supplier_id"])
    _bought(shop, b["supplier_id"])
    body = shop.get("/purchases",
                    params={"supplier_id": a["supplier_id"]}).json()
    assert body["count"] == 1


# ------------------------------------------------------------------ void --


def test_a_voided_purchase_is_kept_but_counted_in_nothing(
        shop: TestClient) -> None:
    sup = _supplier(shop)
    doc = _bought(shop, sup["supplier_id"])
    r = shop.post(f"/purchases/{doc['purchase_id']}/void",
                  json={"reason": "entered twice"})
    assert r.status_code == 200, r.text
    assert r.json()["purchase"]["void"] is True

    body = shop.get("/purchases").json()
    assert body["count"] == 1          # still there
    assert body["void_count"] == 1
    assert body["spent_paise"] == 0    # counted in nothing

    margin = shop.get("/purchases/margin").json()
    by_id = {i["sku_id"]: i for i in margin["items"]}
    assert by_id[BISCUIT[0]]["cost_known"] is False


def test_voiding_without_a_reason_is_refused(shop: TestClient) -> None:
    sup = _supplier(shop)
    doc = _bought(shop, sup["supplier_id"])
    r = shop.post(f"/purchases/{doc['purchase_id']}/void", json={})
    assert r.status_code == 400
    assert r.json()["reason"] == R_NO_VOID_REASON


def test_voiding_twice_is_refused(shop: TestClient) -> None:
    sup = _supplier(shop)
    doc = _bought(shop, sup["supplier_id"])
    shop.post(f"/purchases/{doc['purchase_id']}/void",
              json={"reason": "typo"})
    r = shop.post(f"/purchases/{doc['purchase_id']}/void",
                  json={"reason": "typo again"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_ALREADY_VOID


# ---------------------------------------------------------------- margin --


def test_margin_is_selling_price_minus_the_latest_cost(
        shop: TestClient) -> None:
    sup = _supplier(shop)
    _bought(shop, sup["supplier_id"])
    body = shop.get("/purchases/margin").json()
    row = {i["sku_id"]: i for i in body["items"]}[BISCUIT[0]]
    assert row["sell_paise"] == 2145
    assert row["cost_paise"] == 1400
    assert row["margin_paise"] == 745
    assert row["margin_rupees"] == "7.45"
    assert row["cost_known"] is True
    assert row["below_cost"] is False


def test_a_product_never_bought_has_an_unknown_margin_not_a_zero_one(
        shop: TestClient) -> None:
    """THE CENTRAL CLAIM. Soap is taught and priced and has never been bought.
    Its margin is null, it is named in `unknown`, and nothing anywhere says the
    shop makes 39.50 on it."""
    sup = _supplier(shop)
    _bought(shop, sup["supplier_id"])
    body = shop.get("/purchases/margin").json()
    row = {i["sku_id"]: i for i in body["items"]}[SOAP[0]]
    assert row["cost_known"] is False
    assert row["cost_paise"] is None
    assert row["margin_paise"] is None
    assert row["margin_pct_of_price"] is None
    assert row["markup_pct_of_cost"] is None
    assert "not zero" in row["note"]
    assert SOAP[0] in body["unknown"]
    assert body["without_a_cost"] == 1
    assert body["margin_known_for_every_product"] is False


def test_the_margin_screen_publishes_no_grand_total(shop: TestClient) -> None:
    """Summing per-unit margins across products would add rupees-per-packet to
    rupees-per-bottle and call it money."""
    body = shop.get("/purchases/margin").json()
    assert "margin_paise" not in body
    assert "total_paise" not in body


def test_both_percentages_name_their_base(shop: TestClient) -> None:
    """25 on a 100 sale is a 25% margin and a 33.3% markup. A bare
    `margin_pct` would let a shopkeeper read whichever he expected."""
    sup = _supplier(shop)
    _bought(shop, sup["supplier_id"], lines=[
        {"sku_id": BISCUIT[0], "units": 1, "cost_rupees": "16.09"}])
    row = {i["sku_id"]: i for i in
           shop.get("/purchases/margin").json()["items"]}[BISCUIT[0]]
    assert row["margin_paise"] == 536          # 2145 - 1609
    assert row["margin_pct_of_price"] == "24.9"
    assert row["markup_pct_of_cost"] == "33.3"


def test_selling_below_cost_is_reported_and_not_clamped(
        shop: TestClient) -> None:
    sup = _supplier(shop)
    _bought(shop, sup["supplier_id"], lines=[
        {"sku_id": BISCUIT[0], "units": 5, "cost_paise": 2500}])
    body = shop.get("/purchases/margin").json()
    row = {i["sku_id"]: i for i in body["items"]}[BISCUIT[0]]
    assert row["margin_paise"] == -355
    assert row["margin_rupees"] == "-3.55"
    assert row["below_cost"] is True
    assert BISCUIT[0] in body["below_cost"]


def test_the_latest_cost_wins_and_the_history_is_kept(
        shop: TestClient) -> None:
    sup = _supplier(shop)
    _bought(shop, sup["supplier_id"], date="2026-01-10", lines=[
        {"sku_id": BISCUIT[0], "units": 10, "cost_paise": 1400}])
    _bought(shop, sup["supplier_id"], date="2026-03-10", lines=[
        {"sku_id": BISCUIT[0], "units": 10, "cost_paise": 1550}])

    body = shop.get(f"/purchases/sku/{BISCUIT[0]}").json()
    assert body["cost_paise"] == 1550
    assert body["margin_paise"] == 595
    assert body["times_bought"] == 2
    assert body["units_bought"] == 20
    assert [h["cost_paise"] for h in body["cost_history"]] == [1400, 1550]


def test_a_margin_can_be_asked_for_as_it_stood_on_a_past_day(
        shop: TestClient) -> None:
    """The cost used is the last one recorded ON OR BEFORE the day asked
    about, so a rise in February does not rewrite January."""
    sup = _supplier(shop)
    _bought(shop, sup["supplier_id"], date="2026-01-10", lines=[
        {"sku_id": BISCUIT[0], "units": 10, "cost_paise": 1400}])
    _bought(shop, sup["supplier_id"], date="2026-03-10", lines=[
        {"sku_id": BISCUIT[0], "units": 10, "cost_paise": 1550}])

    jan = shop.get("/purchases/margin", params={"day": "2026-02-01"}).json()
    assert {i["sku_id"]: i for i in jan["items"]}[BISCUIT[0]]["cost_paise"] == 1400
    now = shop.get("/purchases/margin", params={"day": "2026-04-01"}).json()
    assert {i["sku_id"]: i for i in now["items"]}[BISCUIT[0]]["cost_paise"] == 1550
    before = shop.get("/purchases/margin", params={"day": "2025-12-31"}).json()
    assert {i["sku_id"]: i
            for i in before["items"]}[BISCUIT[0]]["cost_known"] is False


def test_a_malformed_day_on_the_margin_screen_is_refused(
        shop: TestClient) -> None:
    r = shop.get("/purchases/margin", params={"day": "last tuesday"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_DATE


def test_one_product_that_was_never_taught_and_never_bought_is_a_404(
        shop: TestClient) -> None:
    r = shop.get("/purchases/sku/ghost_500g")
    assert r.status_code == 404
    assert r.json()["reason"] == R_UNKNOWN_SKU


def test_a_product_bought_and_then_delisted_is_reported_separately(
        shop: TestClient) -> None:
    """It has a cost on file and no selling price. The margin is unknown for
    the OTHER reason, and the response says which."""
    sup = _supplier(shop)
    _bought(shop, sup["supplier_id"])
    assert upload_app._ao_remove(BISCUIT[0]) is True

    body = shop.get("/purchases/margin").json()
    assert BISCUIT[0] in body["bought_but_not_in_the_catalogue"]
    assert BISCUIT[0] not in {i["sku_id"] for i in body["items"]}

    one = shop.get(f"/purchases/sku/{BISCUIT[0]}").json()
    assert one["still_in_catalogue"] is False
    assert one["sell_paise"] is None
    assert one["cost_paise"] == 1400          # what was paid is still known
    assert one["margin_paise"] is None
    assert "no selling price" in one["note"]


def test_a_purchase_shows_what_each_line_would_earn_today(
        shop: TestClient) -> None:
    sup = _supplier(shop)
    doc = _bought(shop, sup["supplier_id"])
    body = shop.get(f"/purchases/{doc['purchase_id']}").json()
    line = body["lines_against_todays_prices"][0]
    assert line["sell_paise"] == 2145
    assert line["margin_paise"] == 745
    assert line["still_in_catalogue"] is True


# ---------------------------------------------------------- today's margin --


@pytest.fixture()
def sold(shop: TestClient, tmp_path: Path) -> TestClient:
    """Three packets of biscuits and one soap, billed today, in the chain."""
    led = Ledger(tmp_path / "data" / "audit.jsonl")
    noon = datetime.now(_tz()).replace(hour=12, minute=0, second=0,
                                       microsecond=0)
    _bill(led, "s_1", noon, [(BISCUIT[0], 2145), (BISCUIT[0], 2145)])
    _bill(led, "s_2", noon + timedelta(hours=1),
          [(BISCUIT[0], 2145), (SOAP[0], 3950)])
    _bill(led, "s_yesterday", noon - timedelta(days=1), [(BISCUIT[0], 2145)])
    return shop


def test_todays_margin_is_counted_off_the_chain(sold: TestClient) -> None:
    sup = _supplier(sold)
    _bought(sold, sup["supplier_id"])          # biscuits at 14.00
    body = sold.get("/purchases/margin/today").json()

    assert body["ok"] is True
    assert body["bills"] == 2
    assert body["revenue_paise"] == 2145 * 3 + 3950
    # Three biscuits sold at 21.45, bought at 14.00 -> 22.35 earned.
    assert body["covered"]["units"] == 3
    assert body["covered"]["revenue_paise"] == 6435
    assert body["covered"]["cost_paise"] == 4200
    assert body["covered"]["margin_paise"] == 2235
    assert body["covered"]["margin_rupees"] == "22.35"


def test_todays_margin_says_when_it_is_only_part_of_the_story(
        sold: TestClient) -> None:
    """Soap sold today and was never bought. Its revenue is reported, its
    margin is not, and the response says the figure is partial rather than
    quietly counting a 39.50 sale as 39.50 of profit."""
    sup = _supplier(sold)
    _bought(sold, sup["supplier_id"])
    body = sold.get("/purchases/margin/today").json()

    assert body["margin_is_partial"] is True
    assert body["uncovered"]["skus"] == [SOAP[0]]
    assert body["uncovered"]["units"] == 1
    assert body["uncovered"]["revenue_paise"] == 3950
    row = {i["sku_id"]: i for i in body["items"]}[SOAP[0]]
    assert row["cost_known"] is False
    assert row["margin_paise"] is None
    assert "not zero" in row["note"]


def test_todays_margin_is_whole_when_everything_has_a_cost(
        sold: TestClient) -> None:
    sup = _supplier(sold)
    _bought(sold, sup["supplier_id"], lines=[
        {"sku_id": BISCUIT[0], "units": 10, "cost_paise": 1400},
        {"sku_id": SOAP[0], "units": 4, "cost_paise": 2900}])
    body = sold.get("/purchases/margin/today").json()
    assert body["margin_is_partial"] is False
    assert body["uncovered"]["skus"] == []
    assert body["covered"]["margin_paise"] == 2235 + (3950 - 2900)
    assert body["covered"]["margin_pct_of_price"] == "31.6"


def test_yesterday_is_a_different_day(sold: TestClient) -> None:
    sup = _supplier(sold)
    yesterday = (datetime.now(_tz()) - timedelta(days=1)).strftime("%Y-%m-%d")
    _bought(sold, sup["supplier_id"],
            date=(datetime.now(_tz()) - timedelta(days=3)).strftime("%Y-%m-%d"))
    body = sold.get("/purchases/margin/today",
                    params={"day": yesterday}).json()
    assert body["bills"] == 1
    assert body["covered"]["units"] == 1
    assert body["covered"]["margin_paise"] == 745


def test_a_sale_made_before_any_cost_was_known_has_no_margin(
        sold: TestClient) -> None:
    """A purchase entered TODAY does not retrospectively price yesterday's
    sales. The shop did not know that cost then and this module will not
    pretend it did — the day comes back uncovered, not silently margined."""
    sup = _supplier(sold)
    _bought(sold, sup["supplier_id"])          # dated today by default
    yesterday = (datetime.now(_tz()) - timedelta(days=1)).strftime("%Y-%m-%d")
    body = sold.get("/purchases/margin/today",
                    params={"day": yesterday}).json()
    assert body["bills"] == 1
    assert body["revenue_paise"] == 2145
    assert body["covered"]["units"] == 0
    assert body["margin_is_partial"] is True
    assert body["uncovered"]["skus"] == [BISCUIT[0]]


def test_a_day_with_no_bills_reports_zero_and_not_a_refusal(
        sold: TestClient) -> None:
    body = sold.get("/purchases/margin/today",
                    params={"day": "2020-01-01"}).json()
    assert body["ok"] is True
    assert body["bills"] == 0
    assert body["revenue_paise"] == 0
    assert body["covered"]["margin_paise"] == 0
    assert body["margin_is_partial"] is False


def test_a_malformed_day_on_the_day_view_is_refused(sold: TestClient) -> None:
    r = sold.get("/purchases/margin/today", params={"day": "2026-02-30"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_DATE


def test_a_product_sold_today_but_no_longer_in_the_catalogue_still_counts(
        sold: TestClient, tmp_path: Path) -> None:
    """Revenue comes off the chain, so a delisted product's takings are not
    lost — it is flagged instead of dropped."""
    sup = _supplier(sold)
    _bought(sold, sup["supplier_id"])
    led = Ledger(tmp_path / "data" / "audit.jsonl")
    _bill(led, "s_3", datetime.now(_tz()).replace(hour=13, minute=0, second=0,
                                                  microsecond=0),
          [("delisted_item", 1000)])
    body = sold.get("/purchases/margin/today").json()
    row = {i["sku_id"]: i for i in body["items"]}["delisted_item"]
    assert row["still_in_catalogue"] is False
    assert row["revenue_paise"] == 1000
    assert row["cost_known"] is False


# ------------------------------------------------------------- the chain --


def test_a_purchase_appends_to_its_own_verifiable_chain(
        shop: TestClient, tmp_path: Path) -> None:
    sup = _supplier(shop)
    doc = _bought(shop, sup["supplier_id"])
    shop.post(f"/purchases/{doc['purchase_id']}/void",
              json={"reason": "duplicate"})

    path = tmp_path / "shop" / "purchases.audit.jsonl"
    ok, lines, head, error = verify(path)
    assert ok is True, error
    assert lines == 3          # supplier.added, purchase.recorded, voided
    events = [json.loads(x)["event"] for x in
              path.read_text(encoding="utf-8").splitlines()]
    assert events == ["supplier.added", "purchase.recorded", "purchase.voided"]


def test_the_chain_is_not_the_money_chain(shop: TestClient,
                                          tmp_path: Path) -> None:
    """results/audit.jsonl is held open by the money service as sole writer.
    A second appender there gives it a stale head and breaks every line paisa
    writes afterwards."""
    sup = _supplier(shop)
    _bought(shop, sup["supplier_id"])
    assert (tmp_path / "shop" / "purchases.audit.jsonl").exists()
    assert not (tmp_path / "data" / "audit.jsonl").exists()


def test_no_supplier_phone_reaches_the_chain(shop: TestClient,
                                             tmp_path: Path) -> None:
    """An audit log is the file most likely to be pasted into a bug report."""
    _supplier(shop, phone="9812345678", notes="cousin of the landlord")
    raw = (tmp_path / "shop" / "purchases.audit.jsonl").read_text(
        encoding="utf-8")
    assert "9812345678" not in raw
    assert "landlord" not in raw
    assert "Sharma Traders" in raw          # who was paid IS in the chain


def test_a_purchase_says_whether_it_was_witnessed(shop: TestClient) -> None:
    sup = _supplier(shop)
    r = _buy(shop, sup["supplier_id"])
    assert r.json()["audited"] is True


# ------------------------------------------------- refusals and invariants --


def test_nothing_here_settles_money(shop: TestClient) -> None:
    sup = _supplier(shop)
    _bought(shop, sup["supplier_id"])
    for path in ("/purchases", "/purchases/suppliers", "/purchases/margin",
                 "/purchases/margin/today", f"/purchases/sku/{BISCUIT[0]}"):
        assert shop.get(path).json()["settles_money"] is False
    assert shop.get("/purchases/nope").json()["settles_money"] is False


def test_no_forgery_primitive_exists_in_this_module() -> None:
    """Invariant 6, asserted against the source rather than promised in prose."""
    src = Path(purchases.__file__).read_text(encoding="utf-8")
    for forbidden in ("upi:", "pa=", "razorpay", "short_url", "payment_link"):
        assert forbidden not in src.lower(), forbidden


def test_no_float_or_division_touches_a_number_in_this_module() -> None:
    """Invariant 1, at the level tools/lint_no_float.py cannot reach: this
    module must contain no float literal, no float() cast and no true
    division anywhere at all, money-named or not."""
    import ast

    tree = ast.parse(Path(purchases.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            raise AssertionError(f"float literal at line {node.lineno}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "float":
            raise AssertionError(f"float() cast at line {node.lineno}")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            # Path joins are the only '/' allowed, and they are not arithmetic.
            src = ast.unparse(node)
            assert "dir()" in src or "path" in src.lower() or "Path" in src, \
                f"true division at line {node.lineno}: {src}"


def test_every_refusal_this_module_names_is_covered_by_a_test() -> None:
    """A refusal added without a test is a refusal nobody has seen fire."""
    named = {k for k, v in vars(purchases).items()
             if k.startswith("R_") and isinstance(v, str)}
    body = Path(__file__).read_text(encoding="utf-8")
    # Not merely mentioned — COMPARED AGAINST. Every assertion in this file
    # reads `...["reason"] == R_SOMETHING`, so requiring the comparison rules
    # out a constant that is imported and never fired.
    missing = {r for r in named if f"== {r}" not in body}
    assert not missing, f"named but never asserted to fire: {sorted(missing)}"
    assert len(named) >= 25


def test_no_input_of_any_shape_produces_a_500(shop: TestClient) -> None:
    sup = _supplier(shop)
    shapes = [
        {"supplier_id": sup["supplier_id"], "lines": "biscuits"},
        {"supplier_id": sup["supplier_id"], "lines": [None]},
        {"supplier_id": sup["supplier_id"], "lines": [{"sku_id": 7}]},
        {"supplier_id": 12, "lines": []},
        {"lines": [{"sku_id": BISCUIT[0], "units": 1, "cost_paise": 1}]},
        {"supplier_id": sup["supplier_id"],
         "lines": [{"sku_id": BISCUIT[0], "units": 1, "cost_paise": 1}],
         "invoice_no": {"a": 1}},
        {"supplier_id": sup["supplier_id"],
         "lines": [{"sku_id": BISCUIT[0], "units": 1, "cost_rupees": "1.2345"}]},
    ]
    for shape in shapes:
        r = shop.post("/purchases", json=shape)
        assert r.status_code == 400, (shape, r.status_code)
        body = r.json()
        assert body["ok"] is False
        assert body["reason"] and body["detail"], shape


def test_an_unreadable_catalogue_is_a_named_refusal_not_a_crash(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom():
        raise RuntimeError("the catalogue is on fire")

    monkeypatch.setattr(upload_app, "offer_priced_skus", boom)
    monkeypatch.setattr(upload_app, "priced_skus", boom)
    r = shop.get("/purchases/margin")
    assert r.status_code == 400
    assert r.json()["reason"] == R_NO_CATALOGUE
    assert "on fire" in r.json()["detail"]


def test_a_missing_till_is_a_named_refusal(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The catalogue is read through the till and this module will not keep a
    second copy of the prices."""
    monkeypatch.setattr(purchases, "_TILL_NAMES", ())
    monkeypatch.setitem(sys.modules, "tools.upload_app", None)
    monkeypatch.setattr(purchases, "_till", _raise_no_till)
    r = shop.get("/purchases/margin")
    assert r.status_code == 400
    assert r.json()["reason"] == R_NO_TILL


def _raise_no_till():
    raise purchases.PurchaseRefused(R_NO_TILL, "tools/upload_app.py is gone.")


def test_a_missing_bill_book_is_a_named_refusal(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Today's margin is derived from the audit chain; without it the answer is
    a refusal, not a guess and not a zero."""
    import gawaah

    # Both halves are needed. `from . import manage` returns the attribute off
    # the already-imported package without consulting sys.modules at all, so
    # poisoning sys.modules alone proves nothing.
    monkeypatch.delattr(gawaah, "manage", raising=False)
    monkeypatch.setitem(sys.modules, "gawaah.manage", None)
    r = shop.get("/purchases/margin/today")
    assert r.status_code == 400
    assert r.json()["reason"] == R_NO_BILLS


def test_an_unexpected_failure_is_a_400_with_a_name(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> dict:
        raise ZeroDivisionError("nobody expects this")

    monkeypatch.setattr(purchases, "_load_suppliers", boom)
    r = shop.get("/purchases/suppliers")
    assert r.status_code == 400
    assert r.json()["reason"] == R_INTERNAL
    assert "ZeroDivisionError" in r.json()["detail"]


def test_the_router_carries_no_prefix_and_absolute_paths() -> None:
    """The orchestrator mounts this bare, so the paths here are what a browser
    types. A prefix added at mount time would double them."""
    paths = sorted({r.path for r in purchases.router.routes})
    assert paths == [
        "/purchases",
        "/purchases/margin",
        "/purchases/margin/today",
        "/purchases/sku/{sku_id}",
        "/purchases/suppliers",
        "/purchases/suppliers/{supplier_id}",
        "/purchases/{purchase_id}",
        "/purchases/{purchase_id}/void",
    ]
    assert all(p.startswith("/purchases") for p in paths)


def test_the_fixed_paths_are_declared_before_the_wildcard_one() -> None:
    """FastAPI matches in declaration order. /purchases/margin declared after
    /purchases/{purchase_id} would be answered by the id handler and refused
    as a malformed id — a true sentence about the wrong thing."""
    order = [r.path for r in purchases.router.routes]
    assert order.index("/purchases/margin") < order.index("/purchases/{purchase_id}")
    assert order.index("/purchases/suppliers") < order.index("/purchases/{purchase_id}")
    assert order.index("/purchases/sku/{sku_id}") < order.index("/purchases/{purchase_id}")


def test_the_shop_directory_is_honoured_and_results_is_untouched(
        shop: TestClient, tmp_path: Path) -> None:
    """A harness once destroyed the live catalogue. Everything this module
    writes goes under GAWAAH_SHOP_DIR and nowhere else.

    The claim is "this test wrote nothing into results/", so the live
    directory is SNAPSHOTTED before the writes and compared after. It used to
    assert the live purchase book was EMPTY, which is a claim about the
    shopkeeper's trading and not about this test — the first real invoice
    booked on the live till turned it red for a module that had done nothing
    wrong.
    """
    live = Path(__file__).resolve().parent.parent / "results" / "shop" / "purchases"

    def snapshot() -> set[tuple[str, int]]:
        if not live.exists():
            return set()
        return {(p.name, p.stat().st_mtime_ns) for p in live.glob("*")}

    before = snapshot()
    sup = _supplier(shop)
    _bought(shop, sup["supplier_id"])
    assert (tmp_path / "shop" / "purchases" / "suppliers.json").exists()
    assert list((tmp_path / "shop" / "purchases").glob("pur_*.json"))
    assert snapshot() == before, "this test wrote into the live shop"
