"""gawaah/storefront.py — the customer's side of the shop.

A shopkeeper prints a code and sticks it on the shutter. A customer photographs
it with their own phone, browses the catalogue the till was taught, orders, and
pays. These tests exist to make four claims checkable, because each of them is a
claim a demo can fake:

  1. THE PHONE CANNOT NAME A PRICE. Every total below is computed from the
     shop's own catalogue. A basket that asserts what it costs — per line or as
     a total — is REFUSED rather than believed, and refused rather than quietly
     re-priced, because a customer looking at one number while the shop charges
     another is the worst outcome available here.

  2. EVERY REFUSAL HAS A NAME. Empty cart, unknown sku, a fractional quantity, a
     missing address, an illegal status change — each answers with its own
     reason string, and no input of any shape produces a 500.

  3. THE MONEY PATH IS THE EXISTING ONE. The storefront writes a witness in the
     till's own format and paisa RE-PRICES it from its own book. The test below
     runs paisa's real `rerun_scan` over a real storefront order and asserts it
     agrees — and asserts it refuses when the two books disagree by one paisa.

  4. NO FORGERY PRIMITIVE. There is no code here that builds a UPI payload or a
     payment URL, and the module's own source is asserted against it.

Nothing in this file talks to a gateway and nothing here can mark an order paid.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import storefront  # noqa: E402
from gawaah.ledger import verify  # noqa: E402
from gawaah.storefront import (  # noqa: E402
    CANCELLED,
    DELIVERED,
    MAX_LINES,
    MAX_QTY,
    NEW,
    OUT_FOR_DELIVERY,
    PREPARING,
    R_BAD_BODY,
    R_BAD_ORDER_ID,
    R_BAD_PHONE,
    R_BAD_QTY,
    R_BAD_STATUS,
    R_EMPTY_CART,
    R_ILLEGAL_TRANSITION,
    R_LINE_PRICE_DISAGREES,
    R_NO_ADDRESS,
    R_NO_NAME,
    R_NO_ORDER,
    R_NO_PHONE,
    R_NO_PHOTO,
    R_NOT_PAYABLE,
    R_ORDER_CLOSED,
    R_QTY_TOO_LARGE,
    R_REFUSED_LINK,
    R_SHORT_ADDRESS,
    R_TOO_LONG,
    R_TOO_MANY_LINES,
    R_TOTAL_DISAGREES,
    R_UNKNOWN_SKU,
)
from tools import upload_app  # noqa: E402

# One rupee ninety and two-fifty. Deliberately not round numbers: a bug that
# divides or rounds shows up in the second decimal place or not at all.
BISCUIT = ("parle_g_200g", "Parle-G 200g", 2145)
SOAP = ("lifebuoy_125g", "Lifebuoy 125g", 3950)


def _tiny_png_b64() -> str:
    """A real 1x1 PNG, base64, as the till's sidecar stores a thumbnail."""
    return (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ"
        "DwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )


@pytest.fixture()
def shop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A shop that lives and dies with the test.

    THE CATALOGUE IS REDIRECTED TWO WAYS ON PURPOSE. `set_store_dir` moves the
    till's cached handle; `GAWAAH_SHOP_DIR` covers the path any code that
    re-reads the environment would take. A harness that honoured only one of
    them once destroyed the live catalogue in results/, and that is a mistake
    with no undo.
    """
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.delenv("GAWAAH_SCAN_DIR", raising=False)
    monkeypatch.delenv("GAWAAH_CODES_FILE", raising=False)
    upload_app.set_store_dir(tmp_path / "shop")

    for i, (sku, name, price) in enumerate((BISCUIT, SOAP)):
        upload_app.do_enrol_code_only(b"", sku, name, price,
                                      typed=f"890123456789{i}")

    app = FastAPI()
    app.include_router(storefront.router)
    return TestClient(app)


def _order(client: TestClient, **over):
    body = {
        "items": [{"sku_id": BISCUIT[0], "qty": 2}],
        "name": "Rekha",
        "phone": "9876543210",
        "address": "12 MG Road, second floor, near the water tank",
    }
    body.update(over)
    return client.post("/store/order", json=body)


def _placed(client: TestClient, **over) -> dict:
    r = _order(client, **over)
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------- catalogue --


def test_the_storefront_lists_what_the_shopkeeper_taught(shop: TestClient) -> None:
    r = shop.get("/store")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 2
    by_id = {i["sku_id"]: i for i in body["items"]}
    assert by_id[BISCUIT[0]]["name"] == BISCUIT[1]
    assert by_id[BISCUIT[0]]["price_paise"] == BISCUIT[2]
    # Integer paise rendered without ever touching a float.
    assert by_id[BISCUIT[0]]["price_rupees"] == "21.45"
    assert by_id[SOAP[0]]["price_rupees"] == "39.50"
    assert all(isinstance(i["price_paise"], int) for i in body["items"])


def test_a_product_taught_from_a_code_alone_has_no_photograph(shop: TestClient) -> None:
    body = shop.get("/store").json()
    assert all(i["has_photo"] is False for i in body["items"])
    assert all(i["photo_url"] is None for i in body["items"])

    r = shop.get(f"/store/photo/{BISCUIT[0]}")
    assert r.status_code == 404
    assert r.json()["reason"] == R_NO_PHOTO


def test_a_product_taught_from_a_photograph_serves_that_photograph(
        shop: TestClient) -> None:
    upload_app._ao_put("kurkure_90g", "Kurkure 90g", 2000,
                       [[0.5] * 8], _tiny_png_b64())

    listing = {i["sku_id"]: i for i in shop.get("/store").json()["items"]}
    assert listing["kurkure_90g"]["has_photo"] is True
    assert listing["kurkure_90g"]["photo_url"] == "/store/photo/kurkure_90g"

    r = shop.get("/store/photo/kurkure_90g")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG")


def test_a_photograph_of_something_this_shop_does_not_sell_is_refused(
        shop: TestClient) -> None:
    r = shop.get("/store/photo/not_a_product")
    assert r.status_code == 404
    assert r.json()["reason"] == R_UNKNOWN_SKU


# ------------------------------------------------------------- the order --


def test_an_order_is_priced_by_the_shop_and_carries_an_id(shop: TestClient) -> None:
    body = _placed(shop)
    assert body["ok"] is True
    assert body["order_id"].startswith("ord_")
    assert body["status"] == NEW
    assert body["total_paise"] == BISCUIT[2] * 2 == 4290
    assert body["total_rupees"] == "42.90"
    assert body["lines"][0]["qty"] == 2
    assert body["lines"][0]["line_paise"] == 4290


def test_the_phone_may_not_assert_a_total(shop: TestClient) -> None:
    """The whole point. A cart that claims to be cheaper is not believed."""
    r = _order(shop, total_paise=1)
    assert r.status_code == 400
    assert r.json()["reason"] == R_TOTAL_DISAGREES
    assert "4290" in r.json()["detail"]
    # And nothing was written: a refused order is not an order.
    assert shop.get("/orders").json()["count"] == 0


def test_a_total_that_agrees_is_allowed_through(shop: TestClient) -> None:
    """Agreeing with the server is not the same act as deciding."""
    body = _placed(shop, total_paise=4290)
    assert body["total_paise"] == 4290


def test_the_phone_may_not_assert_a_line_price(shop: TestClient) -> None:
    r = _order(shop, items=[{"sku_id": BISCUIT[0], "qty": 1, "price_paise": 1}])
    assert r.status_code == 400
    assert r.json()["reason"] == R_LINE_PRICE_DISAGREES


def test_an_empty_basket_is_refused_by_name(shop: TestClient) -> None:
    assert _order(shop, items=[]).json()["reason"] == R_EMPTY_CART
    assert _order(shop, items=None).json()["reason"] == R_EMPTY_CART


def test_a_product_this_shop_does_not_sell_is_refused_by_name(
        shop: TestClient) -> None:
    r = _order(shop, items=[{"sku_id": "maggi_70g", "qty": 1}])
    assert r.status_code == 400
    assert r.json()["reason"] == R_UNKNOWN_SKU
    assert "maggi_70g" in r.json()["detail"]


@pytest.mark.parametrize("qty", [0, -1, -99])
def test_a_quantity_that_is_not_positive_is_refused_by_name(
        shop: TestClient, qty: int) -> None:
    r = _order(shop, items=[{"sku_id": BISCUIT[0], "qty": qty}])
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_QTY


@pytest.mark.parametrize("qty", [1.5, 2.0, "2", True, None, [2]])
def test_a_quantity_that_is_not_a_whole_number_is_refused(
        shop: TestClient, qty: object) -> None:
    """2.0 is refused as hard as 1.5.

    A float quantity multiplied by an integer price is a float, and a float that
    happens to be whole today is a float that is 4289.999... tomorrow. `True` is
    refused separately because bool is an int in Python and a quantity of True
    is not something anybody meant.
    """
    r = _order(shop, items=[{"sku_id": BISCUIT[0], "qty": qty}])
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_QTY


def test_a_quantity_past_the_counters_cap_is_refused_by_name(
        shop: TestClient) -> None:
    r = _order(shop, items=[{"sku_id": BISCUIT[0], "qty": MAX_QTY + 1}])
    assert r.status_code == 400
    assert r.json()["reason"] == R_QTY_TOO_LARGE


def test_repeated_lines_are_merged_and_still_capped(shop: TestClient) -> None:
    body = _placed(shop, items=[{"sku_id": BISCUIT[0], "qty": 1},
                                {"sku_id": BISCUIT[0], "qty": 2}])
    assert len(body["lines"]) == 1
    assert body["lines"][0]["qty"] == 3
    assert body["total_paise"] == BISCUIT[2] * 3

    r = _order(shop, items=[{"sku_id": BISCUIT[0], "qty": MAX_QTY},
                            {"sku_id": BISCUIT[0], "qty": 1}])
    assert r.json()["reason"] == R_QTY_TOO_LARGE


def test_too_many_different_products_in_one_basket_is_refused(
        shop: TestClient) -> None:
    for i in range(MAX_LINES + 1):
        upload_app._ao_put(f"bulk_{i:03d}", f"Bulk {i}", 100, [], None)
    items = [{"sku_id": f"bulk_{i:03d}", "qty": 1} for i in range(MAX_LINES + 1)]
    r = _order(shop, items=items)
    assert r.status_code == 400
    assert r.json()["reason"] == R_TOO_MANY_LINES


def test_a_missing_delivery_address_is_refused_by_name(shop: TestClient) -> None:
    assert _order(shop, address="").json()["reason"] == R_NO_ADDRESS
    assert _order(shop, address="   ").json()["reason"] == R_NO_ADDRESS
    # Absent entirely, not merely blank — the commonest shape of the mistake.
    no_address = {"items": [{"sku_id": BISCUIT[0], "qty": 1}],
                  "name": "Rekha", "phone": "9876543210"}
    r = shop.post("/store/order", json=no_address)
    assert r.status_code == 400
    assert r.json()["reason"] == R_NO_ADDRESS


def test_an_address_too_short_to_find_is_refused_by_name(shop: TestClient) -> None:
    r = _order(shop, address="12 MG")
    assert r.status_code == 400
    assert r.json()["reason"] == R_SHORT_ADDRESS


def test_a_missing_name_and_a_missing_phone_are_each_refused_by_name(
        shop: TestClient) -> None:
    assert _order(shop, name="").json()["reason"] == R_NO_NAME
    assert _order(shop, phone="").json()["reason"] == R_NO_PHONE
    assert _order(shop, phone="call me").json()["reason"] == R_BAD_PHONE


def test_a_field_longer_than_the_cap_is_refused_rather_than_truncated(
        shop: TestClient) -> None:
    r = _order(shop, name="x" * 500)
    assert r.status_code == 400
    assert r.json()["reason"] == R_TOO_LONG


def test_a_body_that_is_not_an_order_is_refused_by_name(shop: TestClient) -> None:
    r = shop.post("/store/order", content=b"not json",
                  headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_BODY

    r = shop.post("/store/order", json=[1, 2, 3])
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_BODY

    r = shop.post("/store/order", json={"items": "everything", "name": "R",
                                        "phone": "9876543210",
                                        "address": "12 MG Road, near the tank"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_BODY


def test_the_price_the_customer_pays_is_the_price_the_shop_holds_now(
        shop: TestClient) -> None:
    """Re-priced at order time, not remembered from the listing.

    A customer with the page open while the shopkeeper changes a price gets the
    new one. What that costs when it is wrong: the phone can briefly show a
    stale number. The alternative — honouring a price the browser is holding —
    would make the browser an author, which is the thing this product refuses.
    """
    upload_app._ao_put(BISCUIT[0], BISCUIT[1], 2500, [], None)
    body = _placed(shop, items=[{"sku_id": BISCUIT[0], "qty": 2}])
    assert body["total_paise"] == 5000


# -------------------------------------------------- the customer's own view --


def test_a_customer_can_read_back_their_own_order(shop: TestClient) -> None:
    placed = _placed(shop)
    r = shop.get(f"/store/order/{placed['order_id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["order_id"] == placed["order_id"]
    assert body["status"] == NEW
    assert body["total_paise"] == 4290
    assert body["paid"] is False


def test_the_customers_view_carries_no_address_and_no_phone(
        shop: TestClient) -> None:
    """An order id ends up in a shared browser history. A doorstep must not."""
    placed = _placed(shop)
    raw = shop.get(f"/store/order/{placed['order_id']}").text
    assert "MG Road" not in raw
    assert "9876543210" not in raw
    # The shopkeeper's side is the side that gets to see it.
    assert "MG Road" in shop.get("/orders").text


def test_an_order_that_does_not_exist_is_refused_by_name(shop: TestClient) -> None:
    r = shop.get("/store/order/ord_000000000000")
    assert r.status_code == 404
    assert r.json()["reason"] == R_NO_ORDER


@pytest.mark.parametrize("bad", ["nonsense", "ord_zzz", "ord_00000000000",
                                 "ord_ABCDEF123456", "ord_../../catalog"])
def test_an_order_id_that_is_not_one_never_reaches_the_filesystem(
        shop: TestClient, bad: str) -> None:
    """The id becomes a filename, so it is shape-checked before it is joined.

    Two right answers here and both are acceptable: this module's own named
    refusal, or the router declining to match the path at all. What is not
    acceptable is a read.
    """
    r = shop.get(f"/store/order/{bad}")
    assert r.status_code in (400, 404)
    if r.status_code == 400:
        assert r.json()["reason"] == R_BAD_ORDER_ID


# --------------------------------------------------- the shopkeeper's list --


def test_the_shopkeeper_sees_orders_newest_first(shop: TestClient) -> None:
    a = _placed(shop)["order_id"]
    b = _placed(shop, name="Imran")["order_id"]
    body = shop.get("/orders").json()
    assert body["count"] == 2
    assert [o["order_id"] for o in body["orders"]][0] in (a, b)
    ats = [o["at"] for o in body["orders"]]
    assert ats == sorted(ats, reverse=True)
    assert body["counts"][NEW] == 2


def test_the_shopkeepers_list_is_empty_before_anyone_orders(
        shop: TestClient) -> None:
    body = shop.get("/orders").json()
    assert body["ok"] is True
    assert body["count"] == 0
    assert body["orders"] == []


# ------------------------------------------------------------ the journey --


def test_an_order_walks_the_whole_journey(shop: TestClient) -> None:
    oid = _placed(shop)["order_id"]
    for want in (PREPARING, OUT_FOR_DELIVERY, DELIVERED):
        r = shop.post(f"/orders/{oid}/status", json={"status": want})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == want
    doc = shop.get("/orders").json()["orders"][0]
    assert doc["status"] == DELIVERED
    assert [h["to"] for h in doc["history"]] == [
        NEW, PREPARING, OUT_FOR_DELIVERY, DELIVERED]


@pytest.mark.parametrize("start,want", [
    (NEW, OUT_FOR_DELIVERY),
    (NEW, DELIVERED),
    (PREPARING, NEW),
    (PREPARING, DELIVERED),
    (OUT_FOR_DELIVERY, NEW),
    (OUT_FOR_DELIVERY, PREPARING),
])
def test_every_illegal_move_is_refused_by_name_and_changes_nothing(
        shop: TestClient, start: str, want: str) -> None:
    """Skipping a step is refused, and so is going backwards.

    An order that can jump from `new` to `delivered` is an order the shopkeeper
    can mark delivered without anyone having packed it, which makes the status
    field decoration rather than a record.
    """
    oid = _placed(shop)["order_id"]
    walk = {NEW: [], PREPARING: [PREPARING],
            OUT_FOR_DELIVERY: [PREPARING, OUT_FOR_DELIVERY]}[start]
    for step in walk:
        assert shop.post(f"/orders/{oid}/status",
                         json={"status": step}).status_code == 200

    r = shop.post(f"/orders/{oid}/status", json={"status": want})
    assert r.status_code == 400
    assert r.json()["reason"] == R_ILLEGAL_TRANSITION
    assert shop.get(f"/store/order/{oid}").json()["status"] == start


@pytest.mark.parametrize("start", [NEW, PREPARING, OUT_FOR_DELIVERY])
def test_an_order_can_be_cancelled_from_any_state_that_is_not_finished(
        shop: TestClient, start: str) -> None:
    oid = _placed(shop)["order_id"]
    walk = {NEW: [], PREPARING: [PREPARING],
            OUT_FOR_DELIVERY: [PREPARING, OUT_FOR_DELIVERY]}[start]
    for step in walk:
        shop.post(f"/orders/{oid}/status", json={"status": step})
    r = shop.post(f"/orders/{oid}/status", json={"status": CANCELLED})
    assert r.status_code == 200
    assert r.json()["status"] == CANCELLED
    assert r.json()["was"] == start


@pytest.mark.parametrize("closed", [DELIVERED, CANCELLED])
def test_a_finished_order_cannot_be_moved_again(shop: TestClient,
                                                closed: str) -> None:
    oid = _placed(shop)["order_id"]
    if closed == DELIVERED:
        for step in (PREPARING, OUT_FOR_DELIVERY, DELIVERED):
            shop.post(f"/orders/{oid}/status", json={"status": step})
    else:
        shop.post(f"/orders/{oid}/status", json={"status": CANCELLED})

    for want in (NEW, PREPARING, OUT_FOR_DELIVERY, DELIVERED, CANCELLED):
        r = shop.post(f"/orders/{oid}/status", json={"status": want})
        assert r.status_code == 400
        assert r.json()["reason"] in (R_ORDER_CLOSED, R_ILLEGAL_TRANSITION)
    assert shop.get(f"/store/order/{oid}").json()["status"] == closed


def test_moving_an_order_to_the_state_it_is_already_in_is_refused(
        shop: TestClient) -> None:
    """Not an error and not a change. Writing a history line for it would
    record that nothing happened, which is worse than saying so."""
    oid = _placed(shop)["order_id"]
    r = shop.post(f"/orders/{oid}/status", json={"status": NEW})
    assert r.status_code == 400
    assert r.json()["reason"] == R_ILLEGAL_TRANSITION
    assert len(shop.get("/orders").json()["orders"][0]["history"]) == 1


@pytest.mark.parametrize("bad", ["shipped", "", "  ", "NEW", 7, None])
def test_a_status_this_shop_does_not_know_is_refused_by_name(
        shop: TestClient, bad: object) -> None:
    oid = _placed(shop)["order_id"]
    r = shop.post(f"/orders/{oid}/status", json={"status": bad})
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_STATUS


def test_moving_an_order_that_does_not_exist_is_refused_by_name(
        shop: TestClient) -> None:
    r = shop.post("/orders/ord_000000000000/status", json={"status": PREPARING})
    assert r.status_code == 404
    assert r.json()["reason"] == R_NO_ORDER


# ------------------------------------------------------------ persistence --


def test_orders_live_beside_the_catalogue_and_honour_the_override(
        shop: TestClient, tmp_path: Path) -> None:
    """The override is the whole guard against a test destroying a real shop."""
    oid = _placed(shop)["order_id"]
    path = tmp_path / "shop" / "orders" / f"{oid}.json"
    assert path.exists()
    doc = json.loads(path.read_text())
    assert doc["order_id"] == oid
    assert doc["total_paise"] == 4290
    assert doc["customer"]["address"].startswith("12 MG Road")
    # And nothing was written where the real shop lives.
    assert storefront.shop_dir() == tmp_path / "shop"
    assert not (Path(__file__).resolve().parent.parent / "results" / "shop"
                / "orders" / f"{oid}.json").exists()


def test_an_order_survives_a_restart(shop: TestClient, tmp_path: Path) -> None:
    oid = _placed(shop)["order_id"]
    upload_app.set_store_dir(tmp_path / "shop")     # drops every cached handle
    app = FastAPI()
    app.include_router(storefront.router)
    fresh = TestClient(app)
    assert fresh.get(f"/store/order/{oid}").json()["total_paise"] == 4290


def test_every_order_and_every_status_change_is_on_the_hash_chain(
        shop: TestClient, tmp_path: Path) -> None:
    oid = _placed(shop)["order_id"]
    shop.post(f"/orders/{oid}/status", json={"status": PREPARING})
    shop.post(f"/orders/{oid}/status", json={"status": CANCELLED})

    chain = tmp_path / "shop" / "orders.audit.jsonl"
    ok, n, head, err = verify(chain)
    assert ok, err
    assert n == 3
    events = [json.loads(li)["event"]
              for li in chain.read_text().splitlines() if li.strip()]
    assert events == ["order.placed", "order.status", "order.status"]
    assert head != "0" * 64


def test_the_audit_chain_carries_the_money_but_never_the_doorstep(
        shop: TestClient, tmp_path: Path) -> None:
    """An audit log is the file most likely to end up in a bug report."""
    _placed(shop)
    raw = (tmp_path / "shop" / "orders.audit.jsonl").read_text()
    assert "MG Road" not in raw
    assert "9876543210" not in raw
    assert "Rekha" not in raw
    line = json.loads(raw.splitlines()[0])
    assert line["total_paise"] == 4290
    assert len(line["address_sha256"]) == 64


def test_the_storefront_does_not_append_to_the_money_services_ledger(
        shop: TestClient, tmp_path: Path) -> None:
    """Two processes with independent in-memory chain heads would break it.

    paisa holds `results/audit.jsonl` open and computes prev_hash from a head it
    keeps in memory. A second process appending between two of its writes makes
    every subsequent line unverifiable — `make verify-ledger` goes red and the
    casualty is the money trail. So the orders get their own chain.
    """
    _placed(shop)
    assert not (tmp_path / "audit.jsonl").exists()
    assert not (tmp_path / "shop" / "audit.jsonl").exists()
    assert (tmp_path / "shop" / "orders.audit.jsonl").exists()


# ----------------------------------------------------------------- money --


def test_the_witness_this_order_writes_is_re_priced_by_paisas_own_rules(
        shop: TestClient, tmp_path: Path) -> None:
    """The real re-derivation, run by the real money code, with no gateway.

    `rerun_scan` is what paisa runs before a rupee is minted: it loads the
    witness by id, re-resolves every payload through its own binding table and
    re-prices every line from its OWN price book. If the storefront's arithmetic
    and paisa's disagree by a paisa, nothing mints — so this is the test that
    says the payment path actually works.
    """
    from gawaah.paisa import DictPriceBook, IntentRequest, rerun_scan

    placed = _placed(shop, items=[{"sku_id": BISCUIT[0], "qty": 2},
                                  {"sku_id": SOAP[0], "qty": 1}])
    doc = json.loads((tmp_path / "shop" / "orders"
                      / f"{placed['order_id']}.json").read_text())
    scan_id = storefront._write_witness(doc)

    witness = json.loads((tmp_path / "scans" / f"{scan_id}.json").read_text())
    assert len(witness["lines"]) == 3            # one line per unit
    assert witness["lines"][0]["code"] == f"gawaah:{BISCUIT[0]}"

    book = DictPriceBook({BISCUIT[0]: BISCUIT[2], SOAP[0]: SOAP[2]})
    total = BISCUIT[2] * 2 + SOAP[2]
    assert placed["total_paise"] == total

    req = IntentRequest(session_id="shop_test", amount_paise=total,
                        scan={"scan_id": scan_id})
    verdict = rerun_scan(req, book, data_dir=str(tmp_path))
    assert verdict.agrees, verdict.detail
    assert verdict.server_total_paise == total


def test_paisa_refuses_when_its_own_book_disagrees_by_one_paisa(
        shop: TestClient, tmp_path: Path) -> None:
    from gawaah.paisa import DictPriceBook, IntentRequest, rerun_scan

    placed = _placed(shop, items=[{"sku_id": BISCUIT[0], "qty": 1}])
    doc = json.loads((tmp_path / "shop" / "orders"
                      / f"{placed['order_id']}.json").read_text())
    scan_id = storefront._write_witness(doc)

    book = DictPriceBook({BISCUIT[0]: BISCUIT[2] - 1})
    req = IntentRequest(session_id="shop_test", amount_paise=BISCUIT[2],
                        scan={"scan_id": scan_id})
    verdict = rerun_scan(req, book, data_dir=str(tmp_path))
    assert verdict.agrees is False
    assert verdict.reason == "scan_total_disagreement"


def test_paying_says_so_by_name_when_the_money_service_is_not_there(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dead money service is a named refusal, never a crash and never green."""
    monkeypatch.setenv("GAWAAH_PAISA_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(upload_app, "PAISA_BASE", "http://127.0.0.1:1")
    oid = _placed(shop)["order_id"]
    r = shop.post(f"/store/order/{oid}/pay")
    assert r.status_code == 400
    assert r.json()["ok"] is False
    assert r.json()["reason"]
    assert shop.get(f"/store/order/{oid}").json()["paid"] is False


def test_a_minted_link_is_stored_and_replayed_rather_than_minted_twice(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """One basket, one live payment link.

    paisa keys its intents on (session_id, cycle, amount), so a second mint
    under a fresh id would put a SECOND payable link on one order. The counter
    learned this the expensive way; the storefront inherits the rule.
    """
    calls: list[tuple] = []

    def fake_intent(session_id, amount_paise, scan_id):
        calls.append((session_id, amount_paise, scan_id))
        return 200, {"session_id": session_id, "amount_paise": amount_paise,
                     "short_url": "https://rzp.io/i/abc123", "state": "CALLING",
                     "payment_link_id": "plink_x", "replayed": False}

    monkeypatch.setattr(storefront, "_post_intent", fake_intent)
    oid = _placed(shop)["order_id"]

    first = shop.post(f"/store/order/{oid}/pay").json()
    assert first["ok"] is True
    assert first["short_url"] == "https://rzp.io/i/abc123"
    assert first["amount_paise"] == 4290
    assert first["qr_url"] == f"/qr/link/shop_{oid}"

    second = shop.post(f"/store/order/{oid}/pay").json()
    assert second["short_url"] == first["short_url"]
    assert second["replayed"] is True
    assert len(calls) == 1, "a second mint would be a second live payment link"
    assert calls[0][1] == 4290


@pytest.mark.parametrize("forged", [
    "upi://pay?pa=shop@upi&am=42.90",
    "\tupi://pay?pa=shop@upi",
    "https://evil.example/pay",
    "https://evil.com#.rzp.io/i/x",
    "javascript:alert(1)",
])
def test_a_payable_string_the_gateway_did_not_issue_is_never_shown(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch,
        forged: str) -> None:
    """INVARIANT 6, at the one boundary where a string reaches a phone.

    The same allowlist `/qr/link` enforces before it draws a QR. A link this
    program would refuse to encode is one it must also refuse to hand over as
    something tappable — a rule with a door in it is not a rule.
    """
    monkeypatch.setattr(storefront, "_post_intent",
                        lambda s, a, k: (200, {"short_url": forged}))
    oid = _placed(shop)["order_id"]
    r = shop.post(f"/store/order/{oid}/pay")
    assert r.status_code == 400
    assert r.json()["reason"] == R_REFUSED_LINK
    assert forged not in r.text
    assert shop.get(f"/store/order/{oid}").json()["short_url"] is None


def test_a_cancelled_order_cannot_be_paid(shop: TestClient) -> None:
    oid = _placed(shop)["order_id"]
    shop.post(f"/orders/{oid}/status", json={"status": CANCELLED})
    r = shop.post(f"/store/order/{oid}/pay")
    assert r.status_code == 400
    assert r.json()["reason"] == R_NOT_PAYABLE


def test_paying_an_order_that_does_not_exist_is_refused_by_name(
        shop: TestClient) -> None:
    r = shop.post("/store/order/ord_000000000000/pay")
    assert r.status_code == 404
    assert r.json()["reason"] == R_NO_ORDER


def test_this_module_contains_no_forgery_primitive(shop: TestClient) -> None:
    """Read the source and say so.

    A test that greps its own module looks like theatre until you consider what
    it catches: somebody adding a "helpful" fallback that builds a UPI string
    when the gateway is down. That is the one change that would make everything
    else in this file pass while breaking the invariant the product is built on.
    """
    src = (Path(__file__).resolve().parent.parent / "gawaah"
           / "storefront.py").read_text()
    code = "\n".join(li for li in src.splitlines()
                     if not li.strip().startswith("#"))
    assert "upi://" not in code
    assert "pa=" not in code
    assert "&am=" not in code
    # Nothing composes a gateway URL either: the only rzp host reference is the
    # allowlist borrowed from the till.
    assert "rzp.io/" not in code
    assert "https://api.razorpay" not in code


# ------------------------------------------------------ the shutter code --


def test_the_shutter_code_points_at_this_shop_and_says_so(
        shop: TestClient) -> None:
    r = shop.get("/store/link", headers={"host": "192.168.1.7:8790"})
    assert r.status_code == 200
    body = r.json()
    assert body["url"] == "http://192.168.1.7:8790/#/shop"
    assert body["reachable_from_a_phone"] is True

    png = shop.get("/store/qr", headers={"host": "192.168.1.7:8790"})
    assert png.status_code == 200
    assert png.headers["content-type"] == "image/png"
    assert png.headers["X-Gawaah-Storefront-Url"] == "http://192.168.1.7:8790/#/shop"


def test_a_loopback_address_is_reported_as_unreachable_from_a_phone(
        shop: TestClient) -> None:
    """A QR reading 127.0.0.1 is a perfectly good QR that no phone can open."""
    body = shop.get("/store/link", headers={"host": "127.0.0.1:8790"}).json()
    assert body["reachable_from_a_phone"] is False
    assert "loopback" in body["note"]


def test_the_shutter_code_refuses_a_host_that_is_not_a_host(
        shop: TestClient) -> None:
    r = shop.get("/store/link", headers={"host": "evil.com/@rzp.io"})
    assert r.status_code == 400
    assert r.json()["ok"] is False


# ------------------------------------------------------------ no crashes --


@pytest.mark.parametrize("path,method,body", [
    ("/store/order", "post", {}),
    ("/store/order", "post", {"items": [{}]}),
    ("/store/order", "post", {"items": [None]}),
    ("/store/order", "post", {"items": [{"sku_id": 7, "qty": 1}]}),
    ("/store/order", "post", {"items": {"a": 1}}),
    ("/store/order", "post", {"name": 5}),
    ("/orders/ord_aaaaaaaaaaaa/status", "post", {}),
    ("/orders/ord_aaaaaaaaaaaa/status", "post", {"status": {"a": 1}}),
])
def test_no_input_of_any_shape_produces_a_crash(shop: TestClient, path: str,
                                                method: str, body: object) -> None:
    """A 500 is the one answer that teaches the reader nothing."""
    r = getattr(shop, method)(path, json=body)
    assert r.status_code in (400, 404), r.text
    assert r.json()["ok"] is False
    assert isinstance(r.json()["reason"], str) and r.json()["reason"]
    assert isinstance(r.json()["detail"], str)


def test_the_catalogue_answers_by_name_when_the_shop_cannot_be_read(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom():
        raise RuntimeError("the disk is on fire")

    monkeypatch.setattr(upload_app, "priced_skus", boom)
    r = shop.get("/store")
    assert r.status_code == 400
    assert r.json()["ok"] is False
    assert "the disk is on fire" in r.json()["detail"]


# ---------------------------------------------------------------- offers --


def test_an_active_offer_reaches_the_customer_as_a_visible_discount(
        shop: TestClient, tmp_path: Path) -> None:
    """/store passes `marked_paise`, `marked_rupees` and `off_paise` through.

    `offer_priced_skus()` already quotes the discounted price — that is the
    number paisa will charge — but a page shown only the lower number cannot
    say WHY it is lower. The shelf-edge price and the saving must ride along,
    or the shop gives a discount and never shows it.
    """
    from gawaah import offers

    offers.set_offers_path(tmp_path / "shop" / "offers.json")
    try:
        offers.save_offers([offers.Offer(
            offer_id="off_00000000000a", sku_id=SOAP[0],
            kind=offers.KIND_PERCENT, value=10, label="10% off", active=True,
            created_at="2026-09-01T00:00:00+00:00",
        )])
        body = shop.get("/store").json()
        assert body["ok"] is True
        by_id = {i["sku_id"]: i for i in body["items"]}

        row = by_id[SOAP[0]]
        # The charged price is the discounted one — the same number paisa
        # derives — and the shelf edge and the saving are named beside it.
        assert row["price_paise"] == 3555
        assert row["price_rupees"] == "35.55"
        assert row["marked_paise"] == 3950
        assert row["marked_rupees"] == "39.50"
        assert row["off_paise"] == 395
        assert row["marked_paise"] - row["off_paise"] == row["price_paise"]
        assert isinstance(row["marked_paise"], int)
        assert isinstance(row["off_paise"], int)

        # A product with no offer carries no discount fields at all: absent,
        # not null and not zero, so a page can test for presence.
        plain = by_id[BISCUIT[0]]
        assert plain["price_paise"] == BISCUIT[2]
        assert "marked_paise" not in plain
        assert "marked_rupees" not in plain
        assert "off_paise" not in plain
    finally:
        offers.set_offers_path(None)
