"""gawaah/shopadmin.py — correcting a taught product, and naming the shop.

Until this module existed a shopkeeper could create a product and destroy one,
and nothing in between. A mistyped price meant FORGET and photograph the packet
again, throwing away every taught view to fix two characters. These tests exist
to make five claims checkable, because each of them is a claim a demo can fake:

  1. AN EDIT TOUCHES THREE FIELDS AND NOTHING ELSE. The descriptor vectors come
     back BIT-IDENTICAL and the enrolment photograph comes back BYTE-IDENTICAL
     after a name and a price change — asserted against the raw files on disk,
     not against a re-read through the same code that wrote them.

  2. THE SKU ID CANNOT MOVE. It is what the code bindings, the orders and every
     bill already printed refer to. A body that tries to rename it is refused
     BY NAME rather than ignored.

  3. A PRICE CHANGE IS ON A CHAIN. Old value and new value, on a hash-chained
     line that `gawaah.ledger.verify` re-walks from genesis — and NOT on
     `results/audit.jsonl`, which the money service holds open in another
     process as its single writer.

  4. ONE CODE NAMES ONE PRODUCT. Rebinding a code that already names something
     else is refused, and the refusal leaves the price alone.

  5. EVERY REFUSAL HAS A NAME AND NOTHING RAISES A 500. Empty name, bad money,
     a landline, an hour that does not exist, a day that is not a day.

THE CATALOGUE LIVES IN TWO PLACES and both are exercised: the real
`gawaah/shop_store.py` catalogue, and the appearance-only sidecar in
`tools/upload_app.py` where products with no millimetres — and products taught
from a printed code alone — live. A product in BOTH gets both written.

Nothing here talks to a gateway, and every byte written lands under a
`GAWAAH_SHOP_DIR` that dies with the test.
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
    R_BAD_BODY,
    R_BAD_CODE,
    R_BAD_DAY,
    R_BAD_HOURS,
    R_BAD_PHONE,
    R_BAD_PRICE,
    R_CODE_TAKEN,
    R_NO_ADDRESS,
    R_NO_DAYS,
    R_NO_NAME,
    R_NO_PHONE,
    R_NO_SHOP_NAME,
    R_NOTHING_TO_CHANGE,
    R_SAME_HOURS,
    R_SHORT_ADDRESS,
    R_SKU_RENAME,
    R_TOO_LONG,
    R_UNKNOWN_SKU,
)
from tools import upload_app  # noqa: E402

DIM = 24

#: Deliberately not round numbers. A bug that divides or rounds shows up in the
#: second decimal place or not at all.
MAT = ("parle_g_200g", "Parle-G 200g", 2145)          # the real catalogue
CODE_ONLY = ("lifebuoy_125g", "Lifebuoy 125g", 3950)  # the sidecar, no vectors
PHOTO_ONLY = ("shampoo_sachet", "Clinic Plus sachet", 300)   # sidecar + vectors


def vec(seed: int) -> np.ndarray:
    """A reproducible, non-degenerate vector. Never a unit vector: nothing here
    may quietly assume a normalisation it was not given."""
    return np.random.default_rng(seed).normal(size=DIM) * 3.0 + 0.25


def a_photo() -> np.ndarray:
    """A small uint8 picture — what the rectified crop hands the store."""
    rng = np.random.default_rng(7)
    return rng.integers(0, 255, size=(48, 48, 3), dtype=np.uint8)


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
    upload_app.set_store_dir(tmp_path / "shop")

    # 1. The real catalogue: vectors, a footprint in millimetres, a photograph.
    store = upload_app.load_store()
    res = store.add_sku(MAT[0], MAT[1], MAT[2], [vec(1), vec(2)], 118.4,
                        photo_png=a_photo())
    assert res.ok, res.message
    upload_app.bind_code("8901063093157", MAT[0])

    # 2. The sidecar, taught from a printed code alone: no descriptor at all.
    upload_app.do_enrol_code_only(b"", CODE_ONLY[0], CODE_ONLY[1], CODE_ONLY[2],
                                  typed="8901030510005")

    # 3. The sidecar, taught from a plain photograph: vectors, no millimetres.
    upload_app._ao_put(PHOTO_ONLY[0], PHOTO_ONLY[1], PHOTO_ONLY[2],
                       [vec(11), vec(12)], _tiny_png_b64())

    upload_app.publish_price_map()

    app = FastAPI()
    app.include_router(shopadmin.router)
    return TestClient(app)


def _tiny_png_b64() -> str:
    """A real 1x1 PNG, base64, as the till's sidecar stores a thumbnail."""
    return (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ"
        "DwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )


def _patch(client: TestClient, sku: str, body: dict):
    return client.patch(f"/shop/{sku}", json=body)


def _refused(res, reason: str) -> dict:
    """A refusal is a RESULT: a named reason, a 4xx, and never a 500."""
    assert res.status_code in (400, 404), (res.status_code, res.text)
    body = res.json()
    assert body["ok"] is False, body
    assert body["reason"] == reason, body
    assert body["detail"], "a refusal with no detail teaches nobody anything"
    return body


def _sidecar_raw() -> dict:
    return json.loads(upload_app.ao_path().read_text(encoding="utf-8"))


def _catalog_raw() -> dict:
    return json.loads(
        (upload_app.store_dir() / "catalog.json").read_text(encoding="utf-8"))


# ============================================================ the happy edits


def test_a_name_is_corrected_in_the_real_catalogue(shop: TestClient) -> None:
    res = _patch(shop, MAT[0], {"name": "Parle-G Gold 200g"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["changed"] == ["name"]
    assert "shop_store" in body["stored_in"]
    assert body["after"]["name"] == "Parle-G Gold 200g"
    assert upload_app.load_store().get(MAT[0]).name == "Parle-G Gold 200g"


def test_a_price_is_corrected_in_the_real_catalogue(shop: TestClient) -> None:
    body = _patch(shop, MAT[0], {"price_rupees": "23.50"}).json()
    assert body["changed"] == ["price"]
    assert body["after"]["price_paise"] == 2350
    assert body["after"]["price_rupees"] == "23.50"
    assert upload_app.load_store().get(MAT[0]).price_paise == 2350


def test_a_code_only_product_is_edited_in_the_sidecar(shop: TestClient) -> None:
    """The OTHER store. A product taught from a printed code has no descriptor,
    so it is invisible to `taught_skus` and lives only in the sidecar."""
    body = _patch(shop, CODE_ONLY[0],
                  {"name": "Lifebuoy Total 125g", "price_rupees": "41"}).json()
    assert body["stored_in"] == ["appearance_only_sidecar"]
    assert sorted(body["changed"]) == ["name", "price"]
    rec = _sidecar_raw()["skus"][CODE_ONLY[0]]
    assert rec["name"] == "Lifebuoy Total 125g"
    assert rec["price_paise"] == 4100


def test_an_appearance_only_product_is_edited_in_the_sidecar(shop) -> None:
    body = _patch(shop, PHOTO_ONLY[0], {"price_rupees": "3.50"}).json()
    assert body["stored_in"] == ["appearance_only_sidecar"]
    assert _sidecar_raw()["skus"][PHOTO_ONLY[0]]["price_paise"] == 350


def test_an_edit_lands_in_both_stores_when_a_product_is_in_both(shop) -> None:
    """The store SHADOWS the sidecar, so a stale row there is invisible right
    up to the day the store entry is removed — and then the old price is back."""
    store = upload_app.load_store()
    assert store.add_sku(PHOTO_ONLY[0], PHOTO_ONLY[1], PHOTO_ONLY[2],
                         [vec(11), vec(12)], None).ok
    body = _patch(shop, PHOTO_ONLY[0], {"price_rupees": "9.25"}).json()
    assert sorted(body["stored_in"]) == ["appearance_only_sidecar", "shop_store"]
    assert _sidecar_raw()["skus"][PHOTO_ONLY[0]]["price_paise"] == 925
    assert upload_app.load_store().get(PHOTO_ONLY[0]).price_paise == 925


def test_rupees_become_integer_paise_and_never_a_float(shop: TestClient) -> None:
    body = _patch(shop, MAT[0], {"price_rupees": "214.07"}).json()
    stored = upload_app.load_store().get(MAT[0]).price_paise
    assert stored == 21407
    assert isinstance(stored, int) and not isinstance(stored, bool)
    assert body["after"]["price_rupees"] == "214.07"


def test_the_published_price_map_follows_a_price_edit(shop: TestClient) -> None:
    """paisa prices from the published map, not from the catalogue. Without
    this the money service quotes the old price while every screen shows the
    new one, and nothing anywhere reports the disagreement."""
    _patch(shop, MAT[0], {"price_rupees": "77"})
    published = json.loads(
        (upload_app.store_dir().parent / "shop.json").read_text("utf-8"))
    assert published[MAT[0]] == 7700


# ================================================= what an edit must not touch


def test_editing_does_not_disturb_the_taught_vectors_in_the_store(shop) -> None:
    before = _catalog_raw()["skus"][MAT[0]]["vectors"]
    _patch(shop, MAT[0], {"name": "Something else", "price_rupees": "99.99"})
    after = _catalog_raw()["skus"][MAT[0]]["vectors"]
    # BIT-IDENTICAL, compared as the JSON that is actually on disk. A vector
    # that round-trips to within 1e-16 is a vector whose cosine moved.
    assert after == before


def test_editing_does_not_disturb_the_taught_vectors_in_the_sidecar(shop) -> None:
    before = _sidecar_raw()["skus"][PHOTO_ONLY[0]]["vectors"]
    _patch(shop, PHOTO_ONLY[0], {"name": "Renamed", "price_rupees": "4"})
    after = _sidecar_raw()["skus"][PHOTO_ONLY[0]]["vectors"]
    assert after == before
    assert len(after) == 2


def test_editing_does_not_disturb_the_photograph(shop: TestClient) -> None:
    store = upload_app.load_store()
    before = store.photo_bytes(MAT[0])
    assert before, "the fixture must have stored a photograph to be worth testing"
    _patch(shop, MAT[0], {"name": "Renamed", "price_rupees": "12"})
    assert upload_app.load_store().photo_bytes(MAT[0]) == before


def test_editing_does_not_disturb_the_sidecar_thumbnail(shop: TestClient) -> None:
    _patch(shop, PHOTO_ONLY[0], {"name": "Renamed"})
    assert _sidecar_raw()["skus"][PHOTO_ONLY[0]]["photo"] == _tiny_png_b64()


def test_editing_does_not_disturb_the_footprint(shop: TestClient) -> None:
    _patch(shop, MAT[0], {"price_rupees": "5"})
    assert upload_app.load_store().get(MAT[0]).footprint_mm == pytest.approx(118.4)


def test_editing_does_not_change_the_number_of_products(shop: TestClient) -> None:
    before = set(upload_app.priced_skus())
    _patch(shop, MAT[0], {"name": "A", "price_rupees": "1"})
    _patch(shop, CODE_ONLY[0], {"name": "B", "price_rupees": "2"})
    assert set(upload_app.priced_skus()) == before


def test_renaming_never_moves_the_sku_id(shop: TestClient) -> None:
    _patch(shop, MAT[0], {"name": "A completely different name"})
    assert MAT[0] in upload_app.load_store()
    assert upload_app.resolve_code("8901063093157") == MAT[0]


# ============================================================ named refusals


def test_a_body_that_renames_the_sku_id_is_refused_by_name(shop) -> None:
    body = _refused(_patch(shop, MAT[0], {"sku_id": "parle_g_gold",
                                          "name": "Parle-G Gold"}),
                    R_SKU_RENAME)
    assert "orphan" in body["detail"]
    # ...and it changed nothing on the way to refusing.
    assert upload_app.load_store().get(MAT[0]).name == MAT[1]


def test_an_unknown_sku_is_a_404_by_name(shop: TestClient) -> None:
    res = _patch(shop, "never_taught", {"price_rupees": "10"})
    _refused(res, R_UNKNOWN_SKU)
    assert res.status_code == 404


def test_an_empty_name_is_refused_by_name(shop: TestClient) -> None:
    _refused(_patch(shop, MAT[0], {"name": "   "}), R_NO_NAME)
    assert upload_app.load_store().get(MAT[0]).name == MAT[1]


def test_a_name_over_the_cap_is_refused_by_name(shop: TestClient) -> None:
    _refused(_patch(shop, MAT[0], {"name": "x" * 200}), R_TOO_LONG)


def test_a_request_that_changes_nothing_is_refused_by_name(shop) -> None:
    _refused(_patch(shop, MAT[0], {}), R_NOTHING_TO_CHANGE)


def test_a_body_that_is_not_an_object_is_refused_by_name(shop) -> None:
    _refused(shop.patch(f"/shop/{MAT[0]}", json=[1, 2, 3]), R_BAD_BODY)


def test_a_body_that_is_not_json_is_refused_by_name(shop) -> None:
    _refused(shop.patch(f"/shop/{MAT[0]}", content=b"{not json"), R_BAD_BODY)


def test_a_name_that_is_not_text_is_refused_by_name(shop) -> None:
    _refused(_patch(shop, MAT[0], {"name": 42}), R_BAD_BODY)


@pytest.mark.parametrize("bad", ["12.505", "abc", "", "-5", "0", "0.00",
                                 "1,200", "₹40"])
def test_every_bad_price_is_refused_by_name_and_stores_nothing(shop, bad) -> None:
    """THE MONEY DOOR. A float, a sub-paisa string, a zero and a negative are
    each refused here — this is the last place `214.507` can still be rejected
    instead of silently becoming 214 paise."""
    _refused(_patch(shop, MAT[0], {"price_rupees": bad}), R_BAD_PRICE)
    assert upload_app.load_store().get(MAT[0]).price_paise == MAT[2]


def test_a_price_that_is_not_text_is_refused_by_name(shop) -> None:
    """A float sent as JSON is refused as a BODY error, never parsed. 12.5 has
    already lost precision before any check here could run."""
    _refused(_patch(shop, MAT[0], {"price_rupees": 12.5}), R_BAD_BODY)


# ================================================================ the codes


def test_a_code_is_rebound(shop: TestClient) -> None:
    body = _patch(shop, MAT[0], {"code": "8901063093999"}).json()
    assert body["codes"]["action"] == "rebound"
    assert body["codes"]["unbound"] == ["8901063093157"]
    assert upload_app.resolve_code("8901063093999") == MAT[0]
    # The typo must not be left behind still pricing this product: that string
    # belongs to somebody else's packet.
    assert upload_app.resolve_code("8901063093157") is None


def test_rebinding_a_code_that_names_another_product_is_refused(shop) -> None:
    body = _refused(_patch(shop, MAT[0], {"code": "8901030510005",
                                          "price_rupees": "99"}),
                    R_CODE_TAKEN)
    assert CODE_ONLY[0] in body["detail"]
    # AND THE PRICE IS UNTOUCHED. A refused code must not leave a shop with a
    # new price, an old code and a 400 saying nothing happened.
    assert upload_app.load_store().get(MAT[0]).price_paise == MAT[2]
    assert upload_app.resolve_code("8901030510005") == CODE_ONLY[0]


def test_binding_the_code_it_already_has_changes_nothing(shop) -> None:
    body = _patch(shop, MAT[0], {"code": "8901063093157"}).json()
    assert body["codes"]["action"] == "unchanged"
    assert body["changed"] == []
    assert body["reason"] == "nothing_changed"


def test_an_empty_code_clears_the_binding_and_says_which(shop) -> None:
    body = _patch(shop, MAT[0], {"code": ""}).json()
    assert body["codes"]["action"] == "cleared"
    assert body["codes"]["unbound"] == ["8901063093157"]
    assert upload_app.resolve_code("8901063093157") is None


@pytest.mark.parametrize("bad", ["89010 63093", "x" * 65, "line\nbreak"])
def test_a_code_that_is_not_a_printed_code_is_refused_by_name(shop, bad) -> None:
    res = _patch(shop, MAT[0], {"code": bad})
    assert _refused(res, R_TOO_LONG if len(bad) > 64 else R_BAD_CODE)


def test_a_code_edit_alone_does_not_rewrite_the_catalogue(shop) -> None:
    before = _catalog_raw()
    body = _patch(shop, MAT[0], {"code": "8901063093999"}).json()
    assert body["stored_in"] == []
    assert _catalog_raw() == before


# ============================================================== the chain


def test_a_price_change_lands_on_the_audit_chain_with_both_values(shop) -> None:
    """A bill from last week priced this packet at the OLD number. Without the
    pair on one line there is no way to explain that bill, only to disbelieve
    it."""
    body = _patch(shop, MAT[0], {"price_rupees": "23.50"}).json()
    assert body["audit"] is not None, body["audit_note"]
    lines = [json.loads(x) for x in
             shopadmin.audit_path().read_text("utf-8").splitlines() if x.strip()]
    entry = lines[-1]
    assert entry["event"] == "sku_edited"
    assert entry["sku_id"] == MAT[0]
    assert entry["price_paise_before"] == 2145
    assert entry["price_paise_after"] == 2350
    assert entry["price_rupees_before"] == "21.45"
    assert entry["price_rupees_after"] == "23.50"


def test_the_chain_verifies_from_genesis_after_several_edits(shop) -> None:
    _patch(shop, MAT[0], {"price_rupees": "10"})
    _patch(shop, MAT[0], {"name": "Renamed"})
    _patch(shop, CODE_ONLY[0], {"price_rupees": "44.50"})
    ok, lines, _head, err = verify(shopadmin.audit_path())
    assert ok, err
    assert lines == 3


def test_the_chain_is_not_the_money_ledger(shop: TestClient) -> None:
    """`results/audit.jsonl` is held open by the money service in ANOTHER
    process, which computes prev_hash from a head it keeps in memory. A second
    writer there breaks every line paisa writes afterwards."""
    path = shopadmin.audit_path()
    assert path.name != "audit.jsonl"
    assert path.parent == upload_app.store_dir()
    _patch(shop, MAT[0], {"price_rupees": "10"})
    assert path.exists()
    assert not (Path(__file__).resolve().parent.parent
                / "results" / "audit.jsonl.shopadmin").exists()


def test_a_name_change_is_recorded_with_both_values(shop) -> None:
    _patch(shop, MAT[0], {"name": "Parle-G Gold 200g"})
    entry = [json.loads(x) for x in
             shopadmin.audit_path().read_text("utf-8").splitlines()][-1]
    assert entry["name_before"] == MAT[1]
    assert entry["name_after"] == "Parle-G Gold 200g"
    assert "price_paise_after" not in entry


def test_a_code_change_is_recorded(shop: TestClient) -> None:
    _patch(shop, MAT[0], {"code": "8901063093999"})
    entry = [json.loads(x) for x in
             shopadmin.audit_path().read_text("utf-8").splitlines()][-1]
    assert entry["codes_before"] == ["8901063093157"]
    assert entry["codes_after"] == ["8901063093999"]


def test_nothing_is_written_to_the_chain_when_nothing_changed(shop) -> None:
    _patch(shop, MAT[0], {"name": MAT[1]})
    assert not shopadmin.audit_path().exists()


def test_the_history_of_one_product_reads_back(shop: TestClient) -> None:
    _patch(shop, MAT[0], {"price_rupees": "10"})
    _patch(shop, CODE_ONLY[0], {"price_rupees": "20"})
    _patch(shop, MAT[0], {"price_rupees": "30"})
    body = shop.get(f"/shop/{MAT[0]}/history").json()
    assert body["ok"] is True
    assert body["count"] == 2
    assert [e["price_paise_after"] for e in body["entries"]] == [1000, 3000]
    assert body["chain"]["verified"] is True


def test_the_history_of_a_forgotten_product_is_not_a_refusal(shop) -> None:
    """The reason this chain exists is to explain a bill for a packet whose
    price has changed — and the product may since have been forgotten."""
    body = shop.get("/shop/never_taught/history").json()
    assert body["ok"] is True
    assert body["entries"] == []


# ========================================================= the shop identity


PROFILE = {
    "name": "Sharma Kirana Store",
    "address": "12 MG Road, near the water tank, Indiranagar",
    "phone": "9876543210",
    "hours": {"open": "07:30", "close": "21:00",
              "days": ["mon", "tue", "wed", "thu", "fri", "sat"]},
}


def _profile(client: TestClient, **over):
    body = {**PROFILE, **over}
    return client.put("/shop/profile", json=body)


def test_a_shop_with_no_name_yet_is_a_fact_not_a_refusal(shop) -> None:
    body = shop.get("/shop/profile").json()
    assert body["ok"] is True
    assert body["configured"] is False
    assert body["profile"] is None
    assert body["hint"]


def test_the_shop_identity_is_saved_and_read_back(shop: TestClient) -> None:
    res = _profile(shop)
    assert res.status_code == 200, res.text
    saved = res.json()["profile"]
    assert saved["name"] == "Sharma Kirana Store"
    assert saved["phone"] == "9876543210"
    assert saved["hours"]["open"] == "07:30"

    back = shop.get("/shop/profile").json()
    assert back["configured"] is True
    assert back["profile"] == saved


def test_the_identity_is_persisted_next_to_the_catalogue(shop) -> None:
    _profile(shop)
    p = upload_app.store_dir() / "shop_profile.json"
    assert p.exists()
    assert json.loads(p.read_text("utf-8"))["name"] == "Sharma Kirana Store"


def test_an_empty_shop_name_is_refused_by_name(shop: TestClient) -> None:
    _refused(_profile(shop, name="   "), R_NO_SHOP_NAME)
    assert not (upload_app.store_dir() / "shop_profile.json").exists()


def test_a_missing_shop_name_is_refused_by_name(shop: TestClient) -> None:
    body = {k: v for k, v in PROFILE.items() if k != "name"}
    _refused(shop.put("/shop/profile", json=body), R_NO_SHOP_NAME)


def test_a_shop_name_over_the_cap_is_refused_by_name(shop) -> None:
    _refused(_profile(shop, name="x" * 200), R_TOO_LONG)


@pytest.mark.parametrize("bad", ["12345", "5876543210", "987654321",
                                 "0402345678", "+1 415 555 0100",
                                 "1800123456", "abcdefghij",
                                 "98765432109876"])
def test_a_phone_that_is_not_an_indian_mobile_is_refused(shop, bad) -> None:
    """Ten digits starting 6-9, or it is not a mobile. A landline written flat,
    a toll-free line, a foreign number and a nine-digit typo are each refused."""
    _refused(_profile(shop, phone=bad), R_BAD_PHONE)


def test_a_landline_dialled_with_its_std_code_is_a_stated_limit(shop) -> None:
    """THE GAP IS ASSERTED RATHER THAN HIDDEN.

    `080 2345 6789` is a Bangalore landline and `0 8023456789` is a mobile
    dialled with the trunk prefix. Both are eleven digits starting with 0, and
    stripping the 0 leaves ten digits starting 6-9 in each case. Nothing here
    can separate them without a table of STD codes, so the landline is
    ACCEPTED. Refusing the trunk 0 instead would turn away every shopkeeper who
    types their own mobile the way it is printed on their signboard.
    """
    saved = _profile(shop, phone="080 2345 6789").json()["profile"]
    assert saved["phone"] == "8023456789"


@pytest.mark.parametrize("good", ["9876543210", "+91 98765 43210",
                                  "09876543210", "+919876543210",
                                  "0091-9876543210"])
def test_a_mobile_is_stored_bare_however_it_was_typed(shop, good) -> None:
    """Two shopkeepers typing +91 and 0 in front of the same number must not
    end up looking like two different shops."""
    saved = _profile(shop, phone=good).json()["profile"]
    assert saved["phone"] == "9876543210"
    assert saved["phone_e164"] == "+919876543210"


def test_a_missing_phone_is_refused_by_name(shop: TestClient) -> None:
    body = {k: v for k, v in PROFILE.items() if k != "phone"}
    _refused(shop.put("/shop/profile", json=body), R_NO_PHONE)


def test_a_missing_address_is_refused_by_name(shop: TestClient) -> None:
    body = {k: v for k, v in PROFILE.items() if k != "address"}
    _refused(shop.put("/shop/profile", json=body), R_NO_ADDRESS)


def test_an_address_nobody_could_find_is_refused_by_name(shop) -> None:
    _refused(_profile(shop, address="MG Rd"), R_SHORT_ADDRESS)


def test_an_address_over_the_cap_is_refused_by_name(shop) -> None:
    _refused(_profile(shop, address="x" * 400), R_TOO_LONG)


@pytest.mark.parametrize("bad", [{"open": "25:00", "close": "21:00"},
                                 {"open": "half seven", "close": "21:00"},
                                 {"open": "07:30", "close": "9pm"},
                                 {"open": "07:70", "close": "21:00"},
                                 {"open": 730, "close": "21:00"}])
def test_an_hour_that_does_not_exist_is_refused_by_name(shop, bad) -> None:
    _refused(_profile(shop, hours=bad), R_BAD_HOURS)


def test_opening_and_closing_at_the_same_minute_is_refused(shop) -> None:
    _refused(_profile(shop, hours={"open": "09:00", "close": "09:00"}),
             R_SAME_HOURS)


def test_a_day_that_is_not_a_day_is_refused_by_name(shop) -> None:
    _refused(_profile(shop, hours={"open": "07:00", "close": "21:00",
                                   "days": ["mon", "funday"]}), R_BAD_DAY)


def test_a_shop_open_on_no_day_is_refused_by_name(shop) -> None:
    _refused(_profile(shop, hours={"open": "07:00", "close": "21:00",
                                   "days": []}), R_NO_DAYS)


def test_days_left_out_mean_every_day(shop: TestClient) -> None:
    saved = _profile(shop, hours={"open": "07:00", "close": "21:00"}
                     ).json()["profile"]
    assert saved["hours"]["days"] == list(shopadmin.DAYS)
    assert saved["hours"]["days_label"] == "every day"


def test_days_come_back_in_week_order_not_alphabetical(shop) -> None:
    saved = _profile(shop, hours={"open": "07:00", "close": "21:00",
                                  "days": ["sun", "wed", "mon"]}
                     ).json()["profile"]
    assert saved["hours"]["days"] == ["mon", "wed", "sun"]


def test_a_shop_that_shuts_after_midnight_says_so(shop) -> None:
    """Not a shop whose hours were entered backwards, and the difference is
    stated rather than left for a reader to work out."""
    saved = _profile(shop, hours={"open": "18:00", "close": "01:00"}
                     ).json()["profile"]
    assert saved["hours"]["crosses_midnight"] is True


def test_the_identity_change_is_on_the_chain_without_the_values(shop) -> None:
    """The field names, not the fields. This is the shopkeeper's own live phone
    number, and an audit log is the file most likely to be pasted into a bug
    report."""
    _profile(shop)
    text = shopadmin.audit_path().read_text("utf-8")
    entry = json.loads(text.splitlines()[-1])
    assert entry["event"] == "shop_profile_set"
    assert entry["first_time"] is True
    assert sorted(entry["changed"]) == ["address", "hours", "name", "phone"]
    assert "9876543210" not in text
    assert "MG Road" not in text


def test_saving_the_same_identity_again_records_no_change(shop) -> None:
    _profile(shop)
    body = _profile(shop).json()
    assert body["changed"] == []


# ==================================================== nothing raises a 500


@pytest.mark.parametrize("body", [
    {"name": None},
    {"price_rupees": None},
    {"code": None},
    {"name": {"a": 1}},
    {"price_rupees": ["10"]},
    {"code": 8901063093157},
    {"sku_id": None},
    {"name": "x", "price_rupees": True},
])
def test_no_shaped_body_can_produce_a_500_on_an_edit(shop, body) -> None:
    res = shop.patch(f"/shop/{MAT[0]}", json=body)
    assert res.status_code in (200, 400, 404), (res.status_code, res.text)
    assert "reason" in res.json() or res.json().get("ok") is True


@pytest.mark.parametrize("body", [
    {}, [], "text", {"name": "x"}, {"name": "x", "phone": "9876543210"},
    {"name": "x", "phone": "9876543210", "address": "a" * 20, "hours": []},
    {"name": "x", "phone": "9876543210", "address": "a" * 20,
     "hours": {"days": "mon"}},
])
def test_no_shaped_body_can_produce_a_500_on_the_identity(shop, body) -> None:
    res = shop.put("/shop/profile", json=body)
    assert res.status_code == 400, (res.status_code, res.text)
    assert res.json()["reason"], res.text


# ====================================== the scratch directory is honoured


def test_everything_written_lands_under_gawaah_shop_dir(shop, tmp_path) -> None:
    """A harness that ignored this once destroyed the live catalogue, and that
    is a mistake with no undo."""
    _patch(shop, MAT[0], {"price_rupees": "31.50"})
    _profile(shop)
    assert shopadmin.shop_dir() == tmp_path / "shop"
    for p in (shopadmin.audit_path(), shopadmin.profile_path()):
        assert p.exists()
        assert str(p).startswith(str(tmp_path))


def test_the_router_carries_no_prefix_of_its_own() -> None:
    """The orchestrator mounts this with `include_router(shopadmin.router)`.
    A prefix added here as well would yield /shop/shop/{sku_id}."""
    paths = {r.path for r in shopadmin.router.routes}  # type: ignore[attr-defined]
    assert paths == {"/shop", "/shop/{sku_id}", "/shop/{sku_id}/photo",
                     "/shop/{sku_id}/history", "/shop/profile", "/shop/nameplate"}


def test_this_module_never_settles_money(shop: TestClient) -> None:
    for res in (_patch(shop, MAT[0], {"price_rupees": "12"}),
                shop.get("/shop/profile"),
                _profile(shop),
                shop.get(f"/shop/{MAT[0]}/history")):
        assert res.json()["settles_money"] is False


def test_no_float_and_no_division_reaches_a_price_in_this_module() -> None:
    """INVARIANT 1, asserted against this file's own source rather than trusted.

    The repo-wide lint checks floats reaching money-named identifiers; this
    checks the narrower thing that matters here — that the module never
    constructs a float or divides at all on the path a price takes.
    """
    import ast

    src = (Path(shopadmin.__file__)).read_text("utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            raise AssertionError(f"float literal at line {node.lineno}")
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "float"):
            raise AssertionError(f"float() cast at line {node.lineno}")


def test_a_catalogue_that_refuses_the_write_is_named_not_a_crash(shop) -> None:
    """The store raises ShopError / MoneyError / IdentityError straight at its
    caller so the real diagnosis is visible. It must arrive as a named refusal
    with that diagnosis inside it, never as a bare internal error."""
    store = upload_app.load_store()
    real = store.add_sku

    def refuse(*_a, **_k):
        raise ValueError("the catalog on disk is not writable")

    store.add_sku = refuse  # type: ignore[method-assign]
    try:
        body = _refused(_patch(shop, MAT[0], {"price_rupees": "12"}),
                        "catalogue_write_failed")
        assert "not writable" in body["detail"]
    finally:
        store.add_sku = real  # type: ignore[method-assign]


def test_a_thumbnail_survives_a_round_trip_through_base64(shop) -> None:
    """Guards the sidecar edit path: the thumbnail is a base64 STRING and must
    not be re-encoded, re-decoded or re-compressed by an edit."""
    _patch(shop, PHOTO_ONLY[0], {"price_rupees": "7.50"})
    stored = _sidecar_raw()["skus"][PHOTO_ONLY[0]]["photo"]
    assert base64.b64decode(stored).startswith(b"\x89PNG")


# ------------------------------------------------- the name over the door --

def test_the_nameplate_says_the_name_and_nothing_else(shop: TestClient) -> None:
    """`GET /shop/nameplate` is the one shop fact readable without a session.

    It exists because turning the lock on made the sign-in screen — the only
    screen a locked-out shopkeeper can reach — report "This counter has no shop
    name yet" for a counter that knew its name perfectly well and would not say
    it. That is not a refusal, it is a false statement, and this program's whole
    argument is that it does not make those.

    It must carry the name and the address, which are painted on the shutter,
    and NOT the phone, the hours or the path on disk.
    """
    _profile(shop)
    r = shop.get("/shop/nameplate")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["configured"] is True
    assert body["settles_money"] is False
    assert body["name"]
    assert set(body) == {"ok", "settles_money", "configured", "name", "address"}
    for leaked in ("phone", "hours", "path", "days", "profile"):
        assert leaked not in body, f"the nameplate leaked {leaked!r}"


def test_an_unnamed_shop_says_so_rather_than_inventing_a_name(shop: TestClient) -> None:
    body = shop.get("/shop/nameplate").json()
    assert body["ok"] is True
    assert body["configured"] is False
    assert body["name"] is None and body["address"] is None


def test_the_nameplate_is_open_but_the_profile_is_not() -> None:
    """The pair that makes this safe: the readable one takes no writes.

    `/shop/profile` also accepts a PUT, so opening THAT path would let anybody
    on the shop's wifi rename the shop. The nameplate is GET-only, and this
    pins both halves — that it is in auth's open list, and that the profile is
    not.
    """
    from gawaah import auth

    assert "/shop/nameplate" in auth.OPEN_PATHS
    assert "/shop/profile" not in auth.OPEN_PATHS
    methods = {m for r in shopadmin.router.routes            # type: ignore[attr-defined]
               if getattr(r, "path", None) == "/shop/nameplate"
               for m in getattr(r, "methods", set())}
    assert methods <= {"GET", "HEAD"}, f"the nameplate accepts {methods}"
