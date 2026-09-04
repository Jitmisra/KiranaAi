"""Deleting a product switches off the offers that priced it.

THE ASYMMETRY THAT WAS THE BUG. `gawaah/offers.py` refuses to CREATE an offer
for a sku this shop does not price. It had no opinion about a sku that stops
being priced afterwards, so `DELETE /shop/{sku_id}` left this behind:

    $ curl -s localhost:8790/offers | jq '.offers[1] | {says, active}'
    {"says": "10% off lifebuoy_soap", "active": true}     # lifebuoy_soap: gone

Invisible, because the product was gone from every screen. Armed, because the
day somebody re-teaches that sku id — the same string, a different packet, a
different price — the discount comes back on. And UNNOTICEABLE, because offers
are applied by wrapping the money service's own price book: the till, the
storefront and the gateway all agree on the discounted number, so there is no
disagreement anywhere for anybody to catch. Nobody chose it.

There is a second, quieter reason this matters here rather than in `offers.py`
alone: an offer is a money rule, and this is the only route in the program that
can strand one.

Nothing in this file writes outside `tmp_path` — both `GAWAAH_SHOP_DIR` and
`GAWAAH_DATA_DIR` are redirected — and nothing appends to `results/audit.jsonl`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from gawaah import offers as _offers  # noqa: E402
from gawaah.money import MoneyError, paise  # noqa: E402
from tools import upload_app  # noqa: E402

SKU = "lifebuoy_soap"
OTHER = "parle_g_biscuit"

#: A 1x1 PNG. The code path stores no descriptor, so the pixels are never read;
#: the endpoint wants a file part and inventing a photograph would be teaching
#: an appearance nobody photographed.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d4944415478da63fcffff3f0300050001000d0a2dba"
    "0000000049454e44ae426082")


@pytest.fixture(autouse=True)
def _leave_no_trace(monkeypatch: pytest.MonkeyPatch):
    previous = upload_app._DEPS.get("store_dir")
    monkeypatch.delenv("GAWAAH_REQUIRE_AUTH", raising=False)
    yield
    upload_app._DEPS["store_dir"] = previous
    upload_app._DEPS["store"] = None
    _offers.set_offers_path(None)


@pytest.fixture()
def till(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Both variables, always. One alone reads the live catalogue."""
    shop = tmp_path / "shop"
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(shop))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "data"))
    upload_app.set_store_dir(shop)
    shop.mkdir(parents=True, exist_ok=True)
    _offers.set_offers_path(shop / "offers.json")
    return TestClient(upload_app.app)


def _teach(c: TestClient, sku: str, rupees: str = "10") -> None:
    r = c.post("/enrol", files={"image": ("code.png", PNG, "image/png")},
               data={"sku_id": sku, "name": sku.replace("_", " ").title(),
                     "price_rupees": rupees, "mode": "basket",
                     "barcode": f"gawaah:{sku}"})
    assert r.json().get("ok") is True, r.text


def _offer(c: TestClient, sku: str | None, percent: int = 10) -> str:
    r = c.post("/offers", json={"kind": "percent", "percent": percent,
                                "sku_id": sku})
    assert r.status_code == 200, r.text
    return r.json()["offer"]["offer_id"]


def test_deleting_a_product_deactivates_the_offers_that_priced_it(
        till: TestClient) -> None:
    _teach(till, SKU)
    offer_id = _offer(till, SKU)
    assert [o for o in _offers.active_offers() if o.sku_id == SKU]

    r = till.delete(f"/shop/{SKU}")
    assert r.status_code == 200, r.text
    assert r.json()["offers_deactivated"] == 1

    live = [o for o in _offers.active_offers() if o.sku_id == SKU]
    assert live == [], "an offer for a product that no longer exists is armed"
    # DEACTIVATED, NOT DELETED. The shopkeeper has to be able to see what was
    # switched off, and switch it back on if they re-teach deliberately.
    kept = [o for o in _offers.load_offers() if o.offer_id == offer_id]
    assert len(kept) == 1
    assert kept[0].active is False


def test_a_shop_wide_offer_survives_one_product_being_deleted(
        till: TestClient) -> None:
    """`sku_id is None` was never about this product. Ending a shop-wide sale
    because one packet was discontinued would be a price change nobody asked
    for — the same species of bug, pointing the other way."""
    _teach(till, SKU)
    _teach(till, OTHER)
    every = _offer(till, None)

    r = till.delete(f"/shop/{SKU}")
    assert r.status_code == 200
    assert r.json()["offers_deactivated"] == 0
    still = [o for o in _offers.active_offers() if o.offer_id == every]
    assert len(still) == 1 and still[0].active is True


def test_another_products_offer_is_untouched(till: TestClient) -> None:
    _teach(till, SKU)
    _teach(till, OTHER)
    mine = _offer(till, SKU)
    theirs = _offer(till, OTHER, percent=5)

    assert till.delete(f"/shop/{SKU}").json()["offers_deactivated"] == 1
    by_id = {o.offer_id: o for o in _offers.load_offers()}
    assert by_id[mine].active is False
    assert by_id[theirs].active is True


def test_the_count_is_reported_the_way_codes_unbound_already_is(
        till: TestClient) -> None:
    """A number in the response, not a silent repair. The delete route already
    reports `codes_unbound` for exactly this reason."""
    _teach(till, SKU)
    _offer(till, SKU)
    _offer(till, SKU, percent=20)
    body = till.delete(f"/shop/{SKU}").json()
    assert body["offers_deactivated"] == 2
    assert "codes_unbound" in body
    assert body["settles_money"] is False


def test_deleting_a_product_with_no_offers_reports_zero_and_not_an_error(
        till: TestClient) -> None:
    _teach(till, SKU)
    body = till.delete(f"/shop/{SKU}").json()
    assert body["ok"] is True
    assert body["offers_deactivated"] == 0


def test_an_unwritable_offers_file_does_not_block_the_delete(
        till: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A product a shopkeeper has stopped selling comes out of the catalogue
    whether or not the offers file can be written. Best-effort, and the count
    says zero rather than the delete becoming a 400."""
    _teach(till, SKU)
    _offer(till, SKU)

    def _boom(*_a, **_k):
        raise OSError("the disk went away")

    monkeypatch.setattr(_offers, "save_offers", _boom)
    r = till.delete(f"/shop/{SKU}")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["offers_deactivated"] == 0
    assert SKU not in upload_app.priced_skus()


def test_re_teaching_the_sku_does_not_bring_the_discount_back(
        till: TestClient) -> None:
    """THE MEASURED CONSEQUENCE. Same sku id, different packet, different
    price — and before this, ten percent off it, chosen by nobody."""
    _teach(till, SKU, rupees="30")
    _offer(till, SKU)
    till.delete(f"/shop/{SKU}")
    _teach(till, SKU, rupees="45")

    # `/offers/prices` is the endpoint a screen showing a discounted line reads,
    # and it runs the same arithmetic paisa applies inside its own price book —
    # so a wrong answer here is a wrong answer at the gateway too.
    prices = till.get("/offers/prices").json()
    row = next((r for r in prices["items"] if r["sku_id"] == SKU), None)
    assert row is not None, prices
    assert row["base_paise"] == 4500
    assert row["price_paise"] == 4500, "a discount nobody chose came back"
    assert row["off_paise"] == 0
    assert row["offer_id"] is None
    assert prices["discounted"] == 0


# ==========================================================================
# The other door into the price map: a float that never reaches the bill
# ==========================================================================


def test_a_float_price_in_the_sidecar_never_reaches_the_price_map(
        till: TestClient) -> None:
    """`appearance_only.json` is a file on disk and this was the one read of it
    with no `paise()` guard, carried straight into `results/shop.json` — the
    file paisa re-prices every bill from. `gawaah/manage.py` refuses even to
    RENDER such a row, on the grounds that showing it would launder it into a
    number the shopkeeper believes. This one was laundering it into the bill.

    The row is SKIPPED, not rounded: an unpriceable product falls out as amber,
    which is what this program does everywhere else with a price it cannot
    derive exactly.
    """
    _teach(till, SKU, rupees="30")
    sidecar = upload_app.store_dir() / "appearance_only.json"
    doc = json.loads(sidecar.read_text(encoding="utf-8"))
    assert SKU in doc["skus"], doc
    doc["skus"][SKU]["price_paise"] = 1050.7
    sidecar.write_text(json.dumps(doc), encoding="utf-8")
    upload_app._DEPS["store"] = None

    assert SKU not in upload_app.priced_skus(), (
        "a float price reached the catalogue")
    published = upload_app.publish_price_map()
    assert published is not None
    assert SKU not in json.loads(published.read_text(encoding="utf-8"))


def test_the_guard_is_paise_first_and_int_second(till: TestClient) -> None:
    """The order is the whole guard, and it is the easy one to get backwards.

    `int(paise(x))` refuses a float and then narrows the type. `paise(int(x))`
    truncates 1050.7 to 1050 BEFORE paise can refuse it — a silently wrong
    price that passes every check downstream.
    """
    with pytest.raises(MoneyError):
        int(paise(1050.7))
    # The inverted idiom, which is what a reader must not find at that site.
    assert paise(int(1050.7)) == 1050
    src = Path(upload_app.__file__).read_text(encoding="utf-8")
    assert "int(paise(rec[\"price_paise\"]))" in src
