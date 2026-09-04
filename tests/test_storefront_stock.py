"""The shelf, as the storefront sells against it.

`gawaah/stock.py` records a count and an ONLINE FLOOR; `gawaah/storefront.py`
turns them into what a phone may buy:

    available online = on hand − units in orders not yet cancelled − floor

These tests exist because every term in that line is a way to sell a packet
the shop does not have, or to refuse one it does:

  1. THE FLOOR IS RESPECTED. Count 3, floor 2: one may be sold, and the
     second is refused by name.
  2. NULL NEVER BECOMES ZERO. A product nobody has counted has no figure, is
     never out of stock, and is sold as before.
  3. RESERVATION ARITHMETIC IS INTEGER. A placed order holds its packets from
     the next phone without decrementing anything; a cancellation releases
     them; a delivery keeps them held until the shelf is counted again.
  4. THE REFUSAL NAMES LINES. `not_enough_stock_for_these_lines` carries the
     sku, the name, what was asked and what there is — as structure — so a
     page can fix the basket rather than fail blind.
  5. NOTHING WRITES results/. Every path this suite touches is under tmp.

Nothing here talks to a gateway and nothing here can mark an order paid.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import manage, stock, storefront  # noqa: E402
from gawaah.ledger import verify  # noqa: E402
from gawaah.storefront import (  # noqa: E402
    CANCELLED,
    DELIVERED,
    OUT_FOR_DELIVERY,
    PREPARING,
    R_NOT_ENOUGH_STOCK,
)
from tools import upload_app  # noqa: E402

BISCUIT = ("parle_g_200g", "Parle-G 200g", 2145)
SOAP = ("lifebuoy_125g", "Lifebuoy 125g", 3950)

REPO_RESULTS = Path(__file__).resolve().parent.parent / "results"


@pytest.fixture()
def shop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A shop and a data directory that live and die with the test.

    BOTH variables are set, and the till's handle is moved too. The stock
    module reads the count through manage.py, which resolves GAWAAH_SHOP_DIR;
    the storefront reads the catalogue through the till, which caches a
    handle; and the billed-since figure comes off GAWAAH_DATA_DIR/audit.jsonl.
    A fixture that set two of the three would have the storefront selling one
    shop against another shop's count — silently.
    """
    data = tmp_path / "data"
    shop_dir = data / "shop"
    shop_dir.mkdir(parents=True)
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(data))
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(shop_dir))
    monkeypatch.delenv("GAWAAH_SCAN_DIR", raising=False)
    monkeypatch.delenv("GAWAAH_CODES_FILE", raising=False)
    upload_app.set_store_dir(shop_dir)
    manage._CHAIN_CACHE.clear()

    for i, (sku, name, price) in enumerate((BISCUIT, SOAP)):
        upload_app.do_enrol_code_only(b"", sku, name, price,
                                      typed=f"890123456789{i}")

    app = FastAPI()
    app.include_router(storefront.router)
    app.include_router(stock.router)
    client = TestClient(app)
    yield client
    manage._CHAIN_CACHE.clear()


def _order(client: TestClient, sku: str = BISCUIT[0], qty: int = 1, **over):
    body = {
        "items": [{"sku_id": sku, "qty": qty}],
        "name": "Rekha",
        "phone": "9876543210",
        "address": "12 MG Road, second floor, near the water tank",
    }
    body.update(over)
    return client.post("/store/order", json=body)


def _placed(client: TestClient, **kw) -> dict:
    r = _order(client, **kw)
    assert r.status_code == 200, r.text
    return r.json()


def _count(client: TestClient, sku: str, units: int) -> dict:
    r = client.post(f"/stock/{sku}/count", json={"units": units})
    assert r.status_code == 200, r.text
    return r.json()


def _floor(client: TestClient, sku: str, units) -> "object":
    return client.post(f"/stock/{sku}/floor", json={"units": units})


def _listing(client: TestClient) -> dict[str, dict]:
    r = client.get("/store")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stock"]["figures"] is True, body["stock"]
    return {i["sku_id"]: i for i in body["items"]}


def _move(client: TestClient, order_id: str, status: str) -> None:
    r = client.post(f"/orders/{order_id}/status", json={"status": status})
    assert r.status_code == 200, r.text


# ------------------------------------------------------- null is not zero --


def test_a_product_nobody_has_counted_has_no_figure_and_is_sold_as_before(
        shop: TestClient) -> None:
    items = _listing(shop)
    for sku in (BISCUIT[0], SOAP[0]):
        assert items[sku]["available_units"] is None
        assert items[sku]["out_of_stock"] is False
        assert "no stock figure" in items[sku]["stock_note"]
    # Fifty of something with no figure is not a refusal: the shop has not
    # said it has none, it has said nothing.
    body = _placed(shop, qty=50)
    assert body["status"] == "new"


def test_a_count_of_zero_is_out_of_stock_and_a_missing_count_is_not(
        shop: TestClient) -> None:
    _count(shop, BISCUIT[0], 0)
    items = _listing(shop)
    assert items[BISCUIT[0]]["available_units"] == 0
    assert items[BISCUIT[0]]["out_of_stock"] is True
    assert items[BISCUIT[0]]["stock_note"].startswith("out of stock")
    # The soap was never counted and must not catch it.
    assert items[SOAP[0]]["available_units"] is None
    assert items[SOAP[0]]["out_of_stock"] is False


# ------------------------------------------------------------ the floor --


def test_the_floor_is_respected_and_the_line_past_it_is_refused_by_name(
        shop: TestClient) -> None:
    _count(shop, BISCUIT[0], 3)
    r = _floor(shop, BISCUIT[0], 2)
    assert r.status_code == 200, r.text
    assert r.json()["online_floor"] == 2

    items = _listing(shop)
    assert items[BISCUIT[0]]["available_units"] == 1
    assert items[BISCUIT[0]]["out_of_stock"] is False

    # Two is one too many, and the refusal says by how much.
    r = _order(shop, qty=2)
    assert r.status_code == 400
    body = r.json()
    assert body["reason"] == R_NOT_ENOUGH_STOCK
    assert body["lines"] == [{
        "sku_id": BISCUIT[0], "name": BISCUIT[1],
        "asked": 2, "available": 1, "out_of_stock": False,
    }]
    assert BISCUIT[1] in body["detail"]
    assert "2 asked" in body["detail"] and "1 available" in body["detail"]
    assert shop.get("/orders").json()["count"] == 0

    # One is exactly what the floor allows.
    _placed(shop, qty=1)
    items = _listing(shop)
    assert items[BISCUIT[0]]["available_units"] == 0
    assert items[BISCUIT[0]]["out_of_stock"] is True

    r = _order(shop, qty=1)
    assert r.status_code == 400
    body = r.json()
    assert body["reason"] == R_NOT_ENOUGH_STOCK
    assert body["lines"][0]["out_of_stock"] is True
    assert body["lines"][0]["available"] == 0
    assert "out of stock" in body["detail"]


def test_the_floor_defaults_to_zero_and_zero_or_null_puts_it_back(
        shop: TestClient) -> None:
    _count(shop, BISCUIT[0], 1)
    assert _listing(shop)[BISCUIT[0]]["available_units"] == 1
    assert _floor(shop, BISCUIT[0], 1).status_code == 200
    assert _listing(shop)[BISCUIT[0]]["available_units"] == 0
    r = _floor(shop, BISCUIT[0], None)
    assert r.status_code == 200
    assert r.json()["online_floor"] == 0
    assert r.json()["is_default"] is True
    assert _listing(shop)[BISCUIT[0]]["available_units"] == 1


@pytest.mark.parametrize("units,reason", [
    (-1, stock.R_FLOOR_NEGATIVE),
    (2.5, stock.R_FLOOR_FRACTIONAL),
    (2.0, stock.R_FLOOR_NOT_INTEGER),
    ("2", stock.R_FLOOR_NOT_INTEGER),
    (True, stock.R_FLOOR_NOT_INTEGER),
    (stock.MAX_ONLINE_FLOOR + 1, stock.R_FLOOR_TOO_LARGE),
])
def test_a_floor_that_is_not_a_whole_count_is_refused_by_name(
        shop: TestClient, units: object, reason: str) -> None:
    before = stock.audit_path().read_text() if stock.audit_path().exists() else ""
    r = _floor(shop, BISCUIT[0], units)
    assert r.status_code == 400
    assert r.json()["reason"] == reason
    after = stock.audit_path().read_text() if stock.audit_path().exists() else ""
    assert before == after, "a refused floor must not touch the chain"


def test_a_floor_needs_a_body_and_a_product(shop: TestClient) -> None:
    assert shop.post(f"/stock/{BISCUIT[0]}/floor", json={}).json()["reason"] \
        == stock.R_FLOOR_MISSING
    r = _floor(shop, "not_a_product", 1)
    assert r.status_code == 404
    assert r.json()["reason"] == stock.R_UNKNOWN_SKU


def test_the_floor_is_on_the_stock_chain_and_the_chain_verifies(
        shop: TestClient) -> None:
    _floor(shop, BISCUIT[0], 2)
    _floor(shop, BISCUIT[0], 0)
    ok, lines, _head, err = verify(stock.audit_path())
    assert ok, err
    assert lines == 2
    events = [json.loads(ln) for ln in
              stock.audit_path().read_text().splitlines() if ln.strip()]
    assert [e["event"] for e in events] == [stock.EV_FLOOR, stock.EV_FLOOR]
    assert [e["floor_units"] for e in events] == [2, 0]
    # Replayed: last write wins, and a zero is written rather than dropped.
    row = next(r for r in shop.get("/stock").json()["items"]
               if r["sku_id"] == BISCUIT[0])
    assert row["online_floor"] == 0
    assert row["online_floor_set_at"] is not None


# ---------------------------------------------------------- reservation --


def test_a_placed_order_reserves_without_decrementing_the_count(
        shop: TestClient) -> None:
    _count(shop, BISCUIT[0], 5)
    _placed(shop, qty=2)
    # The storefront's figure moved; the shopkeeper's count did not.
    assert _listing(shop)[BISCUIT[0]]["available_units"] == 3
    row = next(r for r in shop.get("/stock").json()["items"]
               if r["sku_id"] == BISCUIT[0])
    assert row["on_hand_units"] == 5
    assert row["counted_units"] == 5
    # And the derivation is on the shopkeeper's side, in integers.
    fig = next(r for r in shop.get("/orders/stock").json()["items"]
               if r["sku_id"] == BISCUIT[0])
    assert fig["on_hand_units"] == 5
    assert fig["reserved_open_units"] == 2
    assert fig["reserved_delivered_units"] == 0
    assert fig["online_floor"] == 0
    assert fig["available_units"] == 3
    assert fig["shelf_after_orders"] == 3
    for key in ("on_hand_units", "reserved_open_units", "reserved_delivered_units",
                "reserved_units", "online_floor", "available_units",
                "shelf_after_orders"):
        assert type(fig[key]) is int, (key, fig[key])
    assert "5 on hand − 2 in open orders" in fig["why"]


def test_two_customers_cannot_both_buy_the_last_packet(shop: TestClient) -> None:
    _count(shop, BISCUIT[0], 1)
    first = _placed(shop, qty=1)
    assert first["status"] == "new"
    r = _order(shop, qty=1, name="Second", phone="9123456789")
    assert r.status_code == 400
    assert r.json()["reason"] == R_NOT_ENOUGH_STOCK
    assert r.json()["lines"][0]["available"] == 0
    assert shop.get("/orders").json()["count"] == 1


def test_cancelling_releases_the_packets_and_delivering_keeps_them_held(
        shop: TestClient) -> None:
    _count(shop, BISCUIT[0], 2)
    a = _placed(shop, qty=1)["order_id"]
    b = _placed(shop, qty=1, name="Second", phone="9123456789")["order_id"]
    assert _listing(shop)[BISCUIT[0]]["out_of_stock"] is True

    _move(shop, a, CANCELLED)
    assert _listing(shop)[BISCUIT[0]]["available_units"] == 1

    # Through the whole journey, the packet stays promised.
    for status in (PREPARING, OUT_FOR_DELIVERY):
        _move(shop, b, status)
        assert _listing(shop)[BISCUIT[0]]["available_units"] == 1

    # Delivered: the packet is GONE, and nothing billed it, so it is still
    # subtracted — until the shelf is counted again.
    _move(shop, b, DELIVERED)
    fig = next(r for r in shop.get("/orders/stock").json()["items"]
               if r["sku_id"] == BISCUIT[0])
    assert fig["reserved_open_units"] == 0
    assert fig["reserved_delivered_units"] == 1
    assert fig["available_units"] == 1

    # A recount supersedes the delivery: the shopkeeper's eyes saw the shelf
    # after the packet left.
    _count(shop, BISCUIT[0], 7)
    fig = next(r for r in shop.get("/orders/stock").json()["items"]
               if r["sku_id"] == BISCUIT[0])
    assert fig["reserved_delivered_units"] == 0
    assert fig["available_units"] == 7


def test_the_refusal_names_every_short_line_and_only_those(
        shop: TestClient) -> None:
    _count(shop, BISCUIT[0], 1)
    _count(shop, SOAP[0], 0)
    r = _order(shop, items=[
        {"sku_id": BISCUIT[0], "qty": 1},   # fine
        {"sku_id": SOAP[0], "qty": 2},      # out of stock
        {"sku_id": BISCUIT[0], "qty": 1},   # merged: now 2 of 1
    ])
    assert r.status_code == 400
    body = r.json()
    assert body["reason"] == R_NOT_ENOUGH_STOCK
    by_sku = {ln["sku_id"]: ln for ln in body["lines"]}
    assert set(by_sku) == {BISCUIT[0], SOAP[0]}
    assert by_sku[BISCUIT[0]] == {"sku_id": BISCUIT[0], "name": BISCUIT[1],
                                  "asked": 2, "available": 1,
                                  "out_of_stock": False}
    assert by_sku[SOAP[0]] == {"sku_id": SOAP[0], "name": SOAP[1],
                               "asked": 2, "available": 0,
                               "out_of_stock": True}
    assert shop.get("/orders").json()["count"] == 0


def test_the_stock_check_comes_after_the_basket_is_priced(shop: TestClient) -> None:
    """A basket that asserts a wrong total is refused for THAT, not for stock —
    the price refusal is the one the customer must not be able to hide."""
    _count(shop, BISCUIT[0], 0)
    r = _order(shop, qty=1, total_paise=1)
    assert r.status_code == 400
    assert r.json()["reason"] == storefront.R_TOTAL_DISAGREES


def test_the_catalogue_says_how_many_are_out_and_explains_the_arithmetic(
        shop: TestClient) -> None:
    _count(shop, BISCUIT[0], 0)
    body = shop.get("/store").json()
    assert body["stock"]["out_of_stock"] == 1
    assert body["stock"]["figures"] is True
    assert body["stock"]["error"] is None
    assert "reserves" in body["stock"]["note"]
    # The floor itself is not on the customer's wire.
    assert "online_floor" not in body["items"][0]


def test_a_customer_is_told_that_other_orders_hold_some_but_never_how_many(
        shop: TestClient) -> None:
    """The sentence is the customer's; the tally is the shop's.

    `/store` is open to anybody holding the shutter link. An exact count of
    what other orders are holding, polled through the day, is a reading of the
    shop's order book — so the number comes off the wire and the explanation
    stays. A customer looking at a shelf of four packets and allowed one still
    learns why.
    """
    _count(shop, BISCUIT[0], 4)
    _placed(shop, qty=3)
    item = _listing(shop)[BISCUIT[0]]
    assert item["available_units"] == 1
    assert "held for orders already placed" in item["stock_note"]
    # A flag, not a tally: never the real 3.
    assert item["reserved_units"] == 1
    assert "3" not in item["stock_note"]


# ------------------------------------------------------ nothing in results/


def test_every_path_this_suite_touches_is_under_tmp(shop: TestClient,
                                                    tmp_path: Path) -> None:
    _count(shop, BISCUIT[0], 1)
    _floor(shop, BISCUIT[0], 1)
    _placed(shop, sku=SOAP[0], qty=1)
    for p in (storefront.shop_dir(), storefront.orders_dir(),
              storefront.audit_path(), stock.audit_path(), stock.shop_dir(),
              manage.stock_path(), manage.ledger_path()):
        assert str(p).startswith(str(tmp_path)), p
        assert not str(p).startswith(str(REPO_RESULTS)), p
