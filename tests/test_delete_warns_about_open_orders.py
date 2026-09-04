"""Deleting a product must say who is still waiting for it.

A shopkeeper cleared their catalogue and re-taught three products. Four orders
were left holding items that no longer existed, and the customer pressing PAY
on a Rs 431.50 basket was refused with `amber_in_basket` naming a line the shop
had been selling an hour earlier.

The refusal is invariant 5 working: the money service re-derives every rupee
from its own book and will not charge for a sku it cannot price. The defect was
that DELETE said nothing, so nobody knew the orders had been stranded until a
customer hit it.

Deleting is still allowed. A shopkeeper who has stopped stocking something has
stopped stocking it, and an order cannot veto that. It just may not be silent.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tools import upload_app
from gawaah import storefront


@pytest.fixture()
def shop(tmp_path, monkeypatch):
    """A scratch shop. BOTH variables — setting one and not the other reads the
    live catalogue, which has produced false failures on this repo before."""
    d = tmp_path / "shop"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(d))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path))
    upload_app.set_store_dir(d)
    return d


def _an_order(shop_dir, order_id: str, sku: str, status: str) -> None:
    od = storefront.orders_dir()
    od.mkdir(parents=True, exist_ok=True)
    (od / f"{order_id}.json").write_text(json.dumps({
        "order_id": order_id,
        "at": "2026-09-03T00:00:00+00:00",
        "status": status,
        "lines": [{"sku_id": sku, "qty": 2, "unit_paise": 3150, "line_paise": 6300}],
        "total_paise": 6300,
    }), encoding="utf-8")


def test_an_open_order_holding_the_sku_is_reported(shop) -> None:
    _an_order(shop, "ord_open1", "lifebuoy_soap", "new")
    _an_order(shop, "ord_open2", "lifebuoy_soap", "out_for_delivery")
    waiting = storefront.orders_still_wanting("lifebuoy_soap")
    assert {w["order_id"] for w in waiting} == {"ord_open1", "ord_open2"}


def test_a_finished_order_is_not_a_liability(shop) -> None:
    """A delivered order is a record. Nobody is going to pay it again."""
    _an_order(shop, "ord_done", "lifebuoy_soap", "delivered")
    _an_order(shop, "ord_gone", "lifebuoy_soap", "cancelled")
    assert storefront.orders_still_wanting("lifebuoy_soap") == []


def test_another_product_is_not_reported(shop) -> None:
    _an_order(shop, "ord_open1", "parle_g_biscuit", "new")
    assert storefront.orders_still_wanting("lifebuoy_soap") == []


def test_no_orders_at_all_is_not_an_error(shop) -> None:
    assert storefront.orders_still_wanting("anything") == []


def test_delete_still_removes_and_says_who_was_waiting(shop) -> None:
    client = TestClient(upload_app.app)
    upload_app._ao_save({"format": upload_app.AO_FORMAT, "skus": {
        "lifebuoy_soap": {"name": "Lifebuoy soap 125g", "price_paise": 3150, "vectors": []},
    }})
    _an_order(shop, "ord_open1", "lifebuoy_soap", "new")

    r = client.delete("/shop/lifebuoy_soap")
    body = r.json()
    assert body["ok"] is True, body
    # The delete is NOT blocked by the order.
    assert "lifebuoy_soap" not in upload_app.priced_skus()
    # But it is not silent about it.
    assert body["stranded_orders"][0]["order_id"] == "ord_open1"
    assert "amber_in_basket" in body["stranded_warning"]
    assert "lifebuoy_soap" in body["stranded_warning"]


def test_deleting_something_nobody_ordered_carries_no_warning(shop) -> None:
    client = TestClient(upload_app.app)
    upload_app._ao_save({"format": upload_app.AO_FORMAT, "skus": {
        "quiet": {"name": "Quiet", "price_paise": 100, "vectors": []},
    }})
    body = client.delete("/shop/quiet").json()
    assert body["ok"] is True
    assert "stranded_orders" not in body
    assert "stranded_warning" not in body
