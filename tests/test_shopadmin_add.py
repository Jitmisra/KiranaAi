"""gawaah/shopadmin.py — putting a product on the shelf without a camera.

A shopkeeper could TEACH a product (photograph it) and CORRECT one (name, price,
code) and had no way at all to simply ADD one. The complaint that produced this
file was one sentence: "shopkeeper should be able to add product or edit stock,
product name, add photo, add price". Four of those five were reachable only
through a camera.

These tests exist to make six claims checkable, because each is a claim a demo
can fake:

  1. A PRODUCT ADDED WITH NO CAMERA IS PRICEABLE. It reaches `priced_skus()`,
     it reaches the merged price map the money service re-prices every bill
     from, and it reaches the customer's storefront catalogue. A product a
     shopkeeper can see and the till cannot price is worse than no product.

  2. IT IS INVISIBLE TO THE RECOGNISER, AND SAYS SO. Zero vectors means
     `taught_skus()` skips it. That is the truth about it, and the response
     states it in words rather than leaving it to be discovered at the counter.

  3. INTEGER PAISE, THROUGH THE CATALOGUE'S OWN MONEY DOOR. `"12.50"` is 1250
     paise; `"12.505"` is REFUSED rather than rounded; a float is refused
     because it is not a string at all.

  4. AN ADD IS ON THE CHAIN, with the price that was written — the same fields
     an edit writes, so one reader can walk the chain and explain a bill
     without special-casing the first line of a product's life.

  5. AN SKU ID IS NEVER SILENTLY REUSED. Re-adding an existing id would REPLACE
     a product — repricing something the shopkeeper never opened — so it is
     refused by name, and a derived id counts up instead of colliding.

  6. A PHOTOGRAPH CHANGES THE PICTURE AND NOTHING ELSE. Price, name, codes,
     descriptor vectors and footprint come back untouched — asserted against
     the raw files on disk, not through the code that wrote them.

Every byte written lands under a `GAWAAH_SHOP_DIR` and a `GAWAAH_DATA_DIR` that
die with the test. Nothing here talks to a gateway and nothing settles money.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import shopadmin  # noqa: E402
from gawaah.ledger import verify  # noqa: E402
from gawaah.shopadmin import (  # noqa: E402
    R_BAD_PHOTO,
    R_BAD_PRICE,
    R_BAD_SKU,
    R_CODE_TAKEN,
    R_NO_NAME,
    R_NO_SKU_FROM_NAME,
    R_NOTHING_TO_CHANGE,
    R_SKU_TAKEN,
    R_UNKNOWN_SKU,
)
from tools import upload_app  # noqa: E402

DIM = 24

#: The product that WAS photographed, so the photo endpoint can be tested
#: against the real catalogue as well as the sidecar. Deliberately not a round
#: price: a bug that divides or rounds shows in the second decimal or not at all.
TAUGHT = ("parle_g_200g", "Parle-G 200g", 2145)


def vec(seed: int) -> np.ndarray:
    """A reproducible, non-degenerate vector. Never a unit vector: nothing here
    may quietly assume a normalisation it was not given."""
    return np.random.default_rng(seed).normal(size=DIM) * 3.0 + 0.25


def a_photo(seed: int = 7, side: int = 48) -> np.ndarray:
    """A small uint8 picture — what the rectified crop hands the store."""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, size=(side, side, 3), dtype=np.uint8)


def a_png(seed: int = 21, side: int = 64) -> bytes:
    """Encoded PNG bytes — what a browser upload actually is."""
    import cv2

    ok, buf = cv2.imencode(".png", a_photo(seed, side))
    assert ok
    return bytes(buf)


@pytest.fixture()
def shop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A shop that lives and dies with the test.

    BOTH DIRECTORY VARIABLES, and they point at DIFFERENT places on purpose.
    `GAWAAH_SHOP_DIR` is the catalogue; `GAWAAH_DATA_DIR` is where the money
    service reads `shop.json`. `publish_price_map()` resolves the target the way
    the reader resolves it, and a harness that set only one of them would let a
    complete, correct price map be written to a path nothing ever opens — which
    is exactly the bug the comment on that function records.
    """
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "data"))
    upload_app.set_store_dir(tmp_path / "shop")

    store = upload_app.load_store()
    res = store.add_sku(TAUGHT[0], TAUGHT[1], TAUGHT[2], [vec(1), vec(2)],
                        118.4, photo_png=a_photo())
    assert res.ok, res.message
    upload_app.publish_price_map()

    app = FastAPI()
    app.include_router(shopadmin.router)
    return TestClient(app)


def _refused(res, reason: str) -> dict:
    """A refusal is a RESULT: a named reason, a 4xx, and never a 500."""
    assert res.status_code in (400, 404, 409), (res.status_code, res.text)
    body = res.json()
    assert body["ok"] is False, body
    assert body["reason"] == reason, body
    assert body["detail"], "a refusal with no detail teaches nobody anything"
    return body


def _add(client: TestClient, **body) -> dict:
    res = client.post("/shop", json=body)
    assert res.status_code == 201, res.text
    return res.json()


def _sidecar_raw() -> dict:
    """The appearance-only sidecar as it is on disk.

    An ABSENT file is an EMPTY sidecar, not an error: nothing writes it until
    the first camera-free product lands, so a test asserting that a refused add
    left no row behind would otherwise fail on a FileNotFoundError and read as
    a product-side bug.
    """
    p = upload_app.ao_path()
    if not p.is_file():
        return {"skus": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def _catalog_raw() -> dict:
    return json.loads(
        (upload_app.store_dir() / "catalog.json").read_text(encoding="utf-8"))


def _price_map() -> dict:
    import os

    return json.loads(
        (Path(os.environ["GAWAAH_DATA_DIR"]) / "shop.json")
        .read_text(encoding="utf-8"))


# ================================================ 1. it is genuinely priceable


def test_a_product_typed_in_reaches_the_money_services_price_map(
        shop: TestClient) -> None:
    """The whole point. A product the shopkeeper can see and the till cannot
    price falls out of every bill as amber, which this program calls
    disqualifying everywhere else."""
    body = _add(shop, name="Basmati rice 5kg", price_rupees="549.50")
    assert body["sku_id"] == "basmati_rice_5kg"
    assert body["price_paise"] == 54950
    assert body["price_rupees"] == "549.50"

    assert _price_map()["basmati_rice_5kg"] == 54950
    assert body["price_published"], "the response must name the file it wrote"

    priced = upload_app.priced_skus()
    assert priced["basmati_rice_5kg"]["price_paise"] == 54950


def test_it_appears_on_the_customers_storefront(shop: TestClient) -> None:
    from gawaah import storefront

    _add(shop, name="Toor dal 1kg", price_rupees="178")
    items = {i["sku_id"]: i for i in storefront.catalogue().values()}
    assert "toor_dal_1kg" in items
    assert items["toor_dal_1kg"]["price_paise"] == 17800


def test_it_is_listed_in_the_shopkeepers_catalogue(shop: TestClient) -> None:
    _add(shop, name="Aashirvaad atta 10kg", price_rupees="465")
    rows = {r["sku_id"]: r for r in upload_app.catalog()["skus"]}
    assert "aashirvaad_atta_10kg" in rows
    row = rows["aashirvaad_atta_10kg"]
    assert row["price_paise"] == 46500
    assert row["vector_dim"] == 0
    assert row["footprint_mm"] is None
    assert row["codes"] == []


# =========================================== 2. invisible to the recogniser


def test_it_has_no_descriptor_and_the_recogniser_cannot_see_it(
        shop: TestClient) -> None:
    body = _add(shop, name="Sugar 1kg", price_rupees="52")
    assert body["warning"].startswith("TYPED IN, NEVER SEEN")

    taught = {r.sku_id for r in upload_app.taught_skus()}
    assert "sugar_1kg" not in taught, (
        "a row with no vectors must not reach the gallery — comparing against "
        "an empty descriptor is how a counter produces confident noise")
    assert TAUGHT[0] in taught, "the photographed product must still be there"

    raw = _sidecar_raw()["skus"]["sugar_1kg"]
    assert raw["vectors"] == []
    assert raw["footprint_mm"] is None


def test_a_product_with_no_code_is_not_told_it_has_one(shop: TestClient) -> None:
    """`product_code_only` is the bucket for "priced, no descriptor", and a
    product typed in with no barcode lands in it too. Telling that shopkeeper
    the counter "cannot notice the code on the wrong packet" names a code they
    never bound."""
    _add(shop, name="Loose peanuts", price_rupees="20")
    _add(shop, name="Lifebuoy 125g", price_rupees="39.50", code="8901030510005")
    rows = {r["sku_id"]: r for r in upload_app.catalog()["skus"]}
    assert "no printed code" in rows["loose_peanuts"]["warning"]
    assert "printed code alone" in rows["lifebuoy_125g"]["warning"]


# ===================================================== 3. integer paise only


@pytest.mark.parametrize("typed,paise", [("12", 1200), ("12.50", 1250),
                                         ("0.05", 5), ("1999.99", 199999)])
def test_rupees_are_parsed_in_string_space(shop: TestClient, typed: str,
                                           paise: int) -> None:
    body = _add(shop, name=f"Item {typed}", price_rupees=typed)
    assert body["price_paise"] == paise
    assert isinstance(body["price_paise"], int)


@pytest.mark.parametrize("typed", ["12.505", "-5", "0", "", "  ", "twelve",
                                   "12,50", "1e3"])
def test_a_price_that_is_not_money_is_refused_not_rounded(
        shop: TestClient, typed: str) -> None:
    _refused(shop.post("/shop", json={"name": "X", "price_rupees": typed}),
             R_BAD_PRICE)
    assert "x" not in _sidecar_raw()["skus"], "a refused add left a row behind"


def test_a_float_price_is_refused_because_it_is_not_a_string(
        shop: TestClient) -> None:
    """`12.50` as JSON is a float that has already lost before any check could
    run. The body must carry rupees as TEXT."""
    res = shop.post("/shop", json={"name": "X", "price_rupees": 12.5})
    assert res.status_code == 400, res.text
    assert res.json()["ok"] is False


def test_a_product_with_no_price_is_refused(shop: TestClient) -> None:
    _refused(shop.post("/shop", json={"name": "Nameless price"}), R_BAD_PRICE)


def test_a_product_with_no_name_is_refused(shop: TestClient) -> None:
    _refused(shop.post("/shop", json={"price_rupees": "10"}), R_NO_NAME)


# ============================================================ 4. on the chain


def test_an_add_is_written_to_the_shops_own_verifiable_chain(
        shop: TestClient) -> None:
    body = _add(shop, name="Ghee 500ml", price_rupees="325")
    assert body["audit"] is not None, body["audit_note"]

    path = shopadmin.audit_path()
    assert path.parent == upload_app.store_dir(), (
        "catalogue edits must NOT be appended to results/audit.jsonl — the "
        "money service holds that file open in another process as its single "
        "writer")
    ok, lines, head, err = verify(path)
    assert ok, err
    assert lines >= 1

    entries = [r for r in shopadmin.Ledger(path).read()
               if r.get("sku_id") == "ghee_500ml"]
    assert len(entries) == 1
    rec = entries[0]
    assert rec["event"] == "sku_added"
    assert rec["price_paise_before"] is None
    assert rec["price_paise_after"] == 32500
    assert rec["price_rupees_after"] == "325.00"
    assert rec["price_published"] is True


def test_the_history_endpoint_shows_the_add_and_then_the_edit(
        shop: TestClient) -> None:
    """One reader, walking one chain, explaining a bill. The add and the
    correction that followed it must both be there, in order."""
    _add(shop, name="Maggi 70g", price_rupees="14")
    assert shop.patch("/shop/maggi_70g",
                      json={"price_rupees": "15"}).status_code == 200

    body = shop.get("/shop/maggi_70g/history").json()
    assert body["chain"]["verified"] is True
    events = [e["event"] for e in body["entries"]]
    assert events == ["sku_added", "sku_edited"]
    assert body["entries"][0]["price_paise_after"] == 1400
    assert body["entries"][1]["price_paise_before"] == 1400
    assert body["entries"][1]["price_paise_after"] == 1500


# =================================================== 5. an sku id is not reused


def test_re_adding_an_existing_id_is_refused_rather_than_replacing(
        shop: TestClient) -> None:
    """`_ao_put` REPLACES. Without this guard, adding "rice" twice would
    silently reprice the first one and the shopkeeper would never know."""
    _add(shop, name="Rice", price_rupees="60")
    body = _refused(shop.post("/shop", json={"sku_id": "rice", "name": "Rice",
                                             "price_rupees": "999"}),
                    R_SKU_TAKEN)
    assert "already names" in body["detail"]
    assert _sidecar_raw()["skus"]["rice"]["price_paise"] == 6000


def test_re_adding_a_photographed_product_is_refused_too(
        shop: TestClient) -> None:
    """The id may be taken in the REAL catalogue rather than the sidecar, and
    that one holds descriptor vectors an add would have thrown away."""
    _refused(shop.post("/shop", json={"sku_id": TAUGHT[0], "name": "Anything",
                                      "price_rupees": "1"}), R_SKU_TAKEN)
    assert upload_app.load_store().get(TAUGHT[0]).price_paise == TAUGHT[2]


def test_a_derived_id_counts_up_instead_of_colliding(shop: TestClient) -> None:
    first = _add(shop, name="Soap", price_rupees="10")
    second = _add(shop, name="Soap", price_rupees="12")
    assert first["sku_id"] == "soap"
    assert second["sku_id"] == "soap_2"
    assert second["sku_id_derived"] is True
    assert _sidecar_raw()["skus"]["soap"]["price_paise"] == 1000
    assert _sidecar_raw()["skus"]["soap_2"]["price_paise"] == 1200


def test_a_typed_id_that_is_not_a_filename_is_refused(shop: TestClient) -> None:
    _refused(shop.post("/shop", json={"sku_id": "rice/../etc", "name": "Rice",
                                      "price_rupees": "10"}), R_BAD_SKU)


def test_a_name_with_no_latin_letters_refuses_rather_than_invents_an_id(
        shop: TestClient) -> None:
    """The id is permanent, so a made-up "item_4" is worse than a refusal that
    says to type one."""
    body = _refused(shop.post("/shop", json={"name": "चावल ५ किलो",
                                             "price_rupees": "549"}),
                    R_NO_SKU_FROM_NAME)
    assert "Type one yourself" in body["detail"]
    # …and typing one works, with the name kept exactly as it was written.
    made = _add(shop, sku_id="chawal_5kg", name="चावल ५ किलो",
                price_rupees="549")
    assert made["name"] == "चावल ५ किलो"
    assert made["sku_id_derived"] is False


def test_a_code_that_already_names_another_product_is_refused(
        shop: TestClient) -> None:
    _add(shop, name="Lifebuoy 125g", price_rupees="39.50", code="8901030510005")
    _refused(shop.post("/shop", json={"name": "Lux 100g", "price_rupees": "35",
                                      "code": "8901030510005"}), R_CODE_TAKEN)
    assert "lux_100g" not in _sidecar_raw()["skus"], (
        "a refused code must not leave a half-made product behind")
    assert upload_app.resolve_code("8901030510005") == "lifebuoy_125g"


def test_a_bound_code_prices_the_typed_in_product(shop: TestClient) -> None:
    body = _add(shop, name="Colgate 100g", price_rupees="55",
                code="8901314601234")
    assert body["codes"] == ["8901314601234"]
    assert upload_app.resolve_code("8901314601234") == "colgate_100g"


# ============================================================ 6. a photograph


def test_a_photo_can_be_put_on_a_product_that_was_never_photographed(
        shop: TestClient) -> None:
    _add(shop, name="Tata salt 1kg", price_rupees="28")
    res = shop.put("/shop/tata_salt_1kg/photo",
                   json={"photo_b64": base64.b64encode(a_png()).decode()})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["has_photo"] is True
    assert body["photo_bytes"] > 0
    assert body["stored_in"] == ["appearance_only_sidecar"]

    stored = _sidecar_raw()["skus"]["tata_salt_1kg"]["photo"]
    assert isinstance(stored, str) and stored
    assert base64.b64decode(stored)[:8] == b"\x89PNG\r\n\x1a\n", (
        "the sidecar must hold a real PNG, re-encoded by the catalogue's own "
        "photo door — not whatever the browser happened to upload")

    # …and the shopkeeper's catalogue screen must SHOW it, which is the whole
    # reason the field exists: "what has no photo" is the question that screen
    # is there to answer.
    rows = {r["sku_id"]: r for r in upload_app.catalog()["skus"]}
    assert rows["tata_salt_1kg"]["thumb_png"] == stored


def test_a_data_url_from_the_browser_is_accepted(shop: TestClient) -> None:
    """A FileReader produces `data:image/png;base64,…`. Making the page strip
    its own prefix is one more thing for the page to get wrong."""
    _add(shop, name="Bourbon 150g", price_rupees="35")
    url = "data:image/png;base64," + base64.b64encode(a_png()).decode()
    res = shop.put("/shop/bourbon_150g/photo", json={"photo_b64": url})
    assert res.status_code == 200, res.text
    assert res.json()["has_photo"] is True


def test_a_photo_may_ride_along_with_the_add(shop: TestClient) -> None:
    body = _add(shop, name="Amul butter 100g", price_rupees="62",
                photo_b64=base64.b64encode(a_png()).decode())
    assert body["has_photo"] is True
    assert _sidecar_raw()["skus"]["amul_butter_100g"]["photo"]


def test_a_photo_change_touches_nothing_else(shop: TestClient) -> None:
    """The descriptor and the money are asserted against the RAW FILES, not
    re-read through the code that wrote them."""
    before_cat = _catalog_raw()
    before_vecs = before_cat["skus"][TAUGHT[0]]["vectors"]
    before_price = before_cat["skus"][TAUGHT[0]]["price_paise"]
    before_fp = before_cat["skus"][TAUGHT[0]]["footprint_mm"]

    res = shop.put(f"/shop/{TAUGHT[0]}/photo",
                   json={"photo_b64": base64.b64encode(a_png(99)).decode()})
    assert res.status_code == 200, res.text
    assert res.json()["stored_in"] == ["shop_store"]
    assert res.json()["on_storefront"] is True

    after = _catalog_raw()["skus"][TAUGHT[0]]
    assert after["vectors"] == before_vecs
    assert after["price_paise"] == before_price
    assert after["footprint_mm"] == before_fp
    assert after["name"] == TAUGHT[1]


def test_a_photo_can_be_taken_away(shop: TestClient) -> None:
    res = shop.put(f"/shop/{TAUGHT[0]}/photo", json={"photo_b64": ""})
    assert res.status_code == 200, res.text
    assert res.json()["has_photo"] is False
    assert res.json()["reason"] == "photo_cleared"
    assert upload_app.load_store().photo_bytes(TAUGHT[0]) is None
    assert _catalog_raw()["skus"][TAUGHT[0]]["price_paise"] == TAUGHT[2]


def test_a_body_with_no_photo_key_changes_nothing(shop: TestClient) -> None:
    """ABSENT AND EMPTY ARE DIFFERENT INSTRUCTIONS. A request that names no
    picture must not blank one."""
    _refused(shop.put(f"/shop/{TAUGHT[0]}/photo", json={}), R_NOTHING_TO_CHANGE)
    assert upload_app.load_store().photo_bytes(TAUGHT[0]) is not None


def test_a_file_that_is_not_an_image_is_refused_by_name(
        shop: TestClient) -> None:
    _add(shop, name="Notebook", price_rupees="45")
    _refused(shop.put("/shop/notebook/photo",
                      json={"photo_b64": base64.b64encode(
                          b"this is a text file, not a photograph").decode()}),
             R_BAD_PHOTO)
    assert _sidecar_raw()["skus"]["notebook"]["photo"] is None


def test_base64_with_rubbish_in_it_is_named_as_such(shop: TestClient) -> None:
    _add(shop, name="Pen", price_rupees="10")
    body = _refused(shop.put("/shop/pen/photo",
                             json={"photo_b64": "not!valid!base64!"}),
                    R_BAD_PHOTO)
    assert "base64" in body["detail"]


def test_a_photo_for_an_unknown_product_is_a_404_not_a_new_product(
        shop: TestClient) -> None:
    res = shop.put("/shop/never_existed/photo",
                   json={"photo_b64": base64.b64encode(a_png()).decode()})
    assert res.status_code == 404, res.text
    assert res.json()["reason"] == R_UNKNOWN_SKU


def test_the_sidecar_thumbnail_is_bounded(shop: TestClient) -> None:
    """A picture in the sidecar is charged to EVERY catalogue read, because it
    is base64 inside a JSON file that `/shop`, `/store` and the price map all
    parse in full. So it is capped, and the cap is a number."""
    _add(shop, name="Big picture", price_rupees="10")
    res = shop.put("/shop/big_picture/photo",
                   json={"photo_b64": base64.b64encode(
                       a_png(5, 1400)).decode()})
    assert res.status_code == 200, res.text
    assert res.json()["photo_bytes"] <= shopadmin.SIDECAR_PHOTO_CAP_BYTES

    import cv2

    stored = base64.b64decode(_sidecar_raw()["skus"]["big_picture"]["photo"])
    img = cv2.imdecode(np.frombuffer(stored, np.uint8), cv2.IMREAD_UNCHANGED)
    assert max(img.shape[:2]) <= shopadmin.SIDECAR_PHOTO_EDGE_PX


def test_a_photo_change_is_on_the_chain(shop: TestClient) -> None:
    _add(shop, name="Dettol 100ml", price_rupees="48")
    shop.put("/shop/dettol_100ml/photo",
             json={"photo_b64": base64.b64encode(a_png()).decode()})
    ok, _lines, _head, err = verify(shopadmin.audit_path())
    assert ok, err
    events = [e["event"] for e in shopadmin.Ledger(shopadmin.audit_path()).read()
              if e.get("sku_id") == "dettol_100ml"]
    assert events == ["sku_added", "sku_photo_set"]


# ================================================ the seam with the edit path


def test_a_typed_in_product_can_then_be_corrected_like_any_other(
        shop: TestClient) -> None:
    """The add and the edit are the same resource. A name typed wrong at
    eleven at night is corrected in the morning, and the sku id does not move —
    which is what every bill printed in between points at."""
    _add(shop, name="Basmatti rice", price_rupees="549")
    res = shop.patch("/shop/basmatti_rice",
                     json={"name": "Basmati rice 5kg", "price_rupees": "565"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert sorted(body["changed"]) == ["name", "price"]
    assert body["sku_id"] == "basmatti_rice", "the sku id must never move"
    assert body["after"]["price_paise"] == 56500
    assert _price_map()["basmatti_rice"] == 56500


def test_a_typed_in_product_can_be_forgotten(shop: TestClient) -> None:
    """`DELETE /shop/{sku_id}` is the till's, not this module's, but a product
    this door can add and that door cannot remove would be a trap."""
    _add(shop, name="Wrong item", price_rupees="10")
    assert upload_app._ao_remove("wrong_item") is True
    upload_app.publish_price_map()
    assert "wrong_item" not in _price_map()
