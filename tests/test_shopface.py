"""The face of the shop — `gawaah/shopface.py` and the slug in `shopadmin`.

What is asserted, in the order it would hurt if wrong:

  1. THE SLUG IS STABLE. Saved twice, renamed, re-read: the same string, so a
     sticker printed on Monday still opens this shop on Friday.
  2. THE SLUG IS UNIQUE. Two shops with the same name differ; the suffix is
     drawn from the unambiguous alphabet and is the stated length.
  3. THE SLUG NEVER CARRIES THE PHONE, even when the shopkeeper typed their
     number into the name.
  4. `/store/shop` IS OPEN AND LEAKS NOTHING. With the lock on and no session
     it answers, and the body holds slug/name/address/photo and NOT the phone,
     the hours or a path. A foreign slug is refused BY NAME, not served.
  5. THE PHOTO ROUND-TRIPS AND IS CAPPED at the store's own two numbers; a
     removal removes; the served bytes are a PNG.
  6. THE QR IS NAVIGATION ONLY, carries the slug, and is refused if the string
     could be read as money.

Every test points the till at a scratch directory through BOTH the cached
handle and the environment variable, for the reason `test_shopadmin.py`
gives: a harness that honoured only one of them once destroyed the live shop.
"""
from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import auth, shopadmin, shopface  # noqa: E402
from gawaah.ledger import verify  # noqa: E402
from tools import upload_app  # noqa: E402

PROFILE = {
    "name": "Verma Kirana Store",
    "address": "Shop 4, Ganesh Nagar Market, Andheri East, Mumbai 400069",
    "phone": "9820114477",
    "hours": {"open": "07:00", "close": "22:00"},
}


@pytest.fixture()
def shop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A shop that lives and dies with the test — both routers, no guard."""
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "data"))
    upload_app.set_store_dir(tmp_path / "shop")
    app = FastAPI()
    app.include_router(shopadmin.router)
    app.include_router(shopface.router)
    return TestClient(app, base_url="http://192.168.1.7:8790")


@pytest.fixture()
def locked_till(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The REAL till with the lock on and nobody signed in."""
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
    upload_app.set_store_dir(tmp_path / "shop")
    return TestClient(upload_app.app)


def _save(client: TestClient, **over) -> dict:
    body = {**PROFILE, **over}
    res = client.put("/shop/profile", json=body)
    assert res.status_code == 200, res.text
    return res.json()["profile"]


def _photo_png_b64(side: int = 640) -> str:
    """A real PNG, larger than the cap's edge, so the ladder has to work."""
    import cv2

    rng = np.random.default_rng(3)
    img = rng.integers(0, 255, size=(side, side, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return base64.b64encode(buf.tobytes()).decode("ascii")


SLUG_SHAPE = re.compile(
    rf"^[a-z0-9][a-z0-9-]*-[{shopadmin.SLUG_SUFFIX_ALPHABET}]"
    rf"{{{shopadmin.SLUG_SUFFIX_LEN}}}$")


# ==========================================================================
# 1-3. THE SLUG
# ==========================================================================


def test_a_saved_shop_gets_a_slug_shaped_from_its_name(shop) -> None:
    doc = _save(shop)
    assert SLUG_SHAPE.match(doc["slug"]), doc["slug"]
    assert doc["slug"].startswith("verma-kirana-store-")


def test_the_slug_is_stable_across_saves_and_renames(shop) -> None:
    first = _save(shop)["slug"]
    again = _save(shop)["slug"]
    renamed = _save(shop, name="Verma Kirana & General Store")["slug"]
    assert first == again == renamed
    # And re-reading does not mint another.
    assert shop.get("/shop/profile").json()["profile"]["slug"] == first
    assert shop.get("/shop/link").json()["slug"] == first


def test_the_body_cannot_choose_the_slug(shop) -> None:
    doc = _save(shop, slug="anything-i-like")
    assert doc["slug"] != "anything-i-like"
    assert SLUG_SHAPE.match(doc["slug"])


def test_two_shops_with_the_same_name_get_different_slugs() -> None:
    seen = {shopadmin.make_slug("Verma Kirana Store") for _ in range(50)}
    assert len(seen) == 50
    for s in seen:
        assert SLUG_SHAPE.match(s), s
        assert s.startswith("verma-kirana-store-")


def test_the_slug_never_contains_the_phone(shop) -> None:
    """A shopkeeper who types their number into the shop's name still gets a
    link without it in — that string ends up in every customer's history."""
    doc = _save(shop, name="Verma Kirana 9820114477", phone="9820114477")
    assert "9820114477" not in doc["slug"]
    assert doc["slug"].startswith("verma-kirana-")
    # Short numbers in a name are a name, not a phone.
    assert shopadmin.slug_base("Shop 24x7") == "shop-24x7"


def test_a_name_in_another_script_still_gets_a_usable_slug() -> None:
    assert shopadmin.slug_base("वर्मा किराना") == "shop"
    assert SLUG_SHAPE.match(shopadmin.make_slug("वर्मा किराना"))


def test_a_long_name_is_cut_at_a_word_and_bounded() -> None:
    base = shopadmin.slug_base("Shri Ganesh Provision And General Merchants "
                               "Wholesale And Retail Andheri East")
    assert len(base) <= shopadmin.SLUG_BASE_MAX
    assert not base.endswith("-")
    assert base == "shri-ganesh-provision-and-general"


def test_a_profile_saved_before_slugs_existed_is_given_one_once(shop) -> None:
    """The live shop was saved by a build with no slug. The first shopkeeper
    read mints one and writes it back; the customer's read never does."""
    _save(shop)
    p = shopadmin.profile_path()
    doc = json.loads(p.read_text("utf-8"))
    del doc["slug"]
    p.write_text(json.dumps(doc), "utf-8")

    # The OPEN read does not write.
    public = shop.get("/store/shop").json()
    assert public["slug"] is None
    assert "slug" not in json.loads(p.read_text("utf-8"))

    # The shopkeeper's read does, once.
    minted = shop.get("/shop/profile").json()["profile"]["slug"]
    assert SLUG_SHAPE.match(minted)
    assert shop.get("/shop/profile").json()["profile"]["slug"] == minted
    entries = [json.loads(l) for l in
               shopadmin.audit_path().read_text("utf-8").splitlines()]
    assert any(e["event"] == "shop_slug_minted" for e in entries)


def test_renewing_the_link_is_a_press_and_is_on_the_chain(shop) -> None:
    before = _save(shop)["slug"]
    res = shop.post("/shop/link/renew").json()
    assert res["ok"] is True
    assert res["slug_before"] == before
    assert res["slug"] != before
    assert SLUG_SHAPE.match(res["slug"])
    assert before in res["warning"]
    assert shop.get("/shop/profile").json()["profile"]["slug"] == res["slug"]
    ok, _lines, _head, err = verify(shopadmin.audit_path())
    assert ok, err
    entries = [json.loads(l) for l in
               shopadmin.audit_path().read_text("utf-8").splitlines()]
    renewed = [e for e in entries if e["event"] == "shop_link_renewed"]
    assert renewed and renewed[-1]["slug_before"] == before
    assert renewed[-1]["slug_after"] == res["slug"]


def test_renewing_with_no_shop_is_refused_by_name(shop) -> None:
    res = shop.post("/shop/link/renew")
    assert res.status_code == 400
    assert res.json()["reason"] == shopface.R_NO_PROFILE


# ==========================================================================
# 4. THE OPEN HEADER
# ==========================================================================

PRIVATE_KEYS = ("phone", "phone_e164", "hours", "path", "updated_at", "format")


def test_store_shop_carries_the_shutter_and_nothing_private(shop) -> None:
    doc = _save(shop)
    body = shop.get("/store/shop", params={"s": doc["slug"]}).json()
    assert body["ok"] is True and body["settles_money"] is False
    assert body["configured"] is True
    assert body["slug"] == doc["slug"]
    assert body["name"] == PROFILE["name"]
    assert body["address"] == PROFILE["address"]
    assert body["photo_url"] is None
    assert body["matches"] is True and body["link"] == "own"
    for k in PRIVATE_KEYS:
        assert k not in body, k
    assert "9820114477" not in json.dumps(body)
    assert "/shop/" not in json.dumps(body).replace("/store/shop", "")


def test_a_link_for_another_shop_is_refused_by_name(shop) -> None:
    _save(shop)
    body = shop.get("/store/shop", params={"s": "raju-kirana-x2yz"}).json()
    assert body["matches"] is False
    assert body["link"] == "other"
    assert body["requested"] == "raju-kirana-x2yz"
    assert "raju-kirana-x2yz" in body["note"]
    assert PROFILE["name"] in body["note"]
    # The shop's own header still comes back, so the page can say which shop
    # the customer has actually reached.
    assert body["name"] == PROFILE["name"]


def test_a_link_with_no_slug_is_the_old_sticker_and_still_opens(shop) -> None:
    _save(shop)
    body = shop.get("/store/shop").json()
    assert body["matches"] is True and body["link"] == "none"
    assert body["requested"] is None


def test_a_mangled_slug_is_echoed_bounded_not_in_full(shop) -> None:
    _save(shop)
    body = shop.get("/store/shop", params={"s": "x" * 5000}).json()
    assert body["matches"] is False
    assert len(body["requested"]) == shopface.REQUESTED_ECHO_MAX


def test_an_unnamed_shop_answers_configured_false_not_a_refusal(shop) -> None:
    body = shop.get("/store/shop", params={"s": "anything-k7m2"}).json()
    assert body["ok"] is True
    assert body["configured"] is False
    assert body["slug"] is None and body["name"] is None
    assert body["matches"] is False


def test_store_shop_and_its_photo_are_open_on_the_locked_till(locked_till) -> None:
    """The customer has no account and never will."""
    for path in ("/store/shop", "/store/shop/photo"):
        assert path in auth.OPEN_PATHS
    r = locked_till.get("/store/shop", params={"s": "verma-kirana-store-k7m2"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    # No photo yet: a 404 by name, not a 401.
    r = locked_till.get("/store/shop/photo")
    assert r.status_code == 404
    assert r.json()["reason"] == shopface.R_NO_PHOTO


def test_everything_that_writes_stays_behind_the_lock(locked_till) -> None:
    for method, path in (("PUT", "/shop/photo"), ("POST", "/shop/link/renew"),
                         ("GET", "/shop/link"), ("GET", "/shop/link/qr")):
        r = locked_till.request(method, path, json={})
        assert r.status_code == 401, (method, path, r.status_code)
        assert path not in auth.OPEN_PATHS


# ==========================================================================
# 5. THE PHOTOGRAPH
# ==========================================================================


def test_the_shop_photo_budget_is_the_stores_own() -> None:
    from gawaah import shop_store

    assert shopface.SHOP_PHOTO_EDGE_PX == shop_store.PHOTO_EDGE_PX
    assert shopface.SHOP_PHOTO_CAP_BYTES == shop_store.PHOTO_CAP_BYTES


def test_the_photo_round_trips_downscaled_capped_and_removable(shop) -> None:
    import cv2

    _save(shop)
    res = shop.put("/shop/photo", json={"photo_b64": _photo_png_b64(640)})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["has_photo"] is True
    assert 0 < body["photo_bytes"] <= shopface.SHOP_PHOTO_CAP_BYTES
    assert body["photo_url"].startswith("/store/shop/photo?v=")
    assert shopface.photo_path().is_file()
    assert str(shopface.photo_path()).startswith(str(shopadmin.shop_dir()))

    # Served, as a PNG, no larger than the store's long edge.
    got = shop.get(body["photo_url"])
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/png"
    img = cv2.imdecode(np.frombuffer(got.content, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None
    assert max(img.shape[:2]) <= shopface.SHOP_PHOTO_EDGE_PX

    # The open header now points at it.
    assert shop.get("/store/shop").json()["photo_url"] == body["photo_url"]

    # A data: URL from a FileReader is accepted too.
    res2 = shop.put("/shop/photo", json={
        "photo_b64": "data:image/png;base64," + _photo_png_b64(300)})
    assert res2.status_code == 200, res2.text

    # Removal removes.
    gone = shop.put("/shop/photo", json={"photo_b64": ""}).json()
    assert gone["has_photo"] is False and gone["photo_url"] is None
    assert not shopface.photo_path().exists()
    assert shop.get("/store/shop/photo").status_code == 404
    assert shop.get("/store/shop").json()["photo_url"] is None

    ok, _lines, _head, err = verify(shopadmin.audit_path())
    assert ok, err
    events = [json.loads(l)["event"] for l in
              shopadmin.audit_path().read_text("utf-8").splitlines()]
    assert events.count("shop_photo_set") == 2
    assert events.count("shop_photo_cleared") == 1


def test_a_photo_that_is_not_an_image_is_refused_by_name(shop) -> None:
    res = shop.put("/shop/photo", json={
        "photo_b64": base64.b64encode(b"not a picture").decode("ascii")})
    assert res.status_code == 400
    assert res.json()["reason"] == shopadmin.R_BAD_PHOTO
    assert not shopface.photo_path().exists()


def test_an_oversized_upload_is_refused_before_it_is_decoded(shop) -> None:
    res = shop.put("/shop/photo", json={
        "photo_b64": "A" * (shopadmin.MAX_PHOTO_B64_CHARS + 4)})
    assert res.status_code == 400
    assert res.json()["reason"] == shopadmin.R_PHOTO_TOO_BIG


def test_a_body_with_no_picture_names_the_missing_field(shop) -> None:
    res = shop.put("/shop/photo", json={})
    assert res.status_code == 400
    assert res.json()["reason"] == shopadmin.R_NOTHING_TO_CHANGE


# ==========================================================================
# 6. THE LINK AND THE CODE
# ==========================================================================


def test_the_link_carries_the_slug_and_the_hash_route(shop) -> None:
    doc = _save(shop)
    body = shop.get("/shop/link").json()
    assert body["ok"] is True
    assert body["url"] == f"http://192.168.1.7:8790/#/shop?s={doc['slug']}"
    assert body["qr_url"] == "/shop/link/qr"
    assert body["reachable_from_a_phone"] is True
    assert body["unique"] is True


def test_the_loopback_warning_survives(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "data"))
    upload_app.set_store_dir(tmp_path / "shop")
    app = FastAPI()
    app.include_router(shopadmin.router)
    app.include_router(shopface.router)
    c = TestClient(app, base_url="http://127.0.0.1:8790")
    _save(c)
    body = c.get("/shop/link").json()
    assert body["reachable_from_a_phone"] is False
    assert "loopback" in body["note"]


def test_an_unnamed_shop_gets_the_plain_link_and_says_so(shop) -> None:
    body = shop.get("/shop/link").json()
    assert body["slug"] is None
    assert body["url"] == "http://192.168.1.7:8790/#/shop"
    assert body["unique"] is False
    assert body["unique_note"]


def test_the_qr_is_a_png_of_the_customer_link(shop) -> None:
    import cv2

    doc = _save(shop)
    r = shop.get("/shop/link/qr", params={"px": 420})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    url = r.headers["x-gawaah-storefront-url"]
    assert url.endswith(f"/#/shop?s={doc['slug']}")
    img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None
    # THE ARUCO-BACKED DETECTOR, NOT THE LEGACY ONE. Measured: whether the
    # legacy `cv2.QRCodeDetector` reads a slugged URL depends on the SLUG, not
    # on the size — `verma-kirana-store-k7m2` comes back '' at 420, 700 and
    # 1000 px, while `verma-kirana-store-75ku` and a 38-character slug decode
    # at every one of those sizes. The suffix below is minted at random, so an
    # assertion through the legacy detector would pass or fail by the draw. The
    # Aruco detector reads every one of them at every size, and so does a
    # phone; it is the instrument, not the encoder, that is being chosen here.
    det = cv2.QRCodeDetectorAruco()
    decoded, _pts, _straight = det.detectAndDecode(img)
    assert decoded == url, (decoded, url)
    assert "upi:" not in decoded.lower()
    assert not re.search(r"pa=|am=|razorpay|rzp\.", decoded)


def test_the_code_refuses_to_encode_money() -> None:
    with pytest.raises(shopadmin.AdminRefused) as e:
        shopface._qr_png("upi://pay?pa=x@y&am=1", 300)
    assert e.value.reason == shopface.R_REFUSED_LINK
    with pytest.raises(shopadmin.AdminRefused) as e2:
        shopface._qr_png("https://rzp.io/i/abc", 300)
    assert e2.value.reason == shopface.R_REFUSED_LINK


def test_a_bad_host_header_is_refused_not_guessed(shop) -> None:
    _save(shop)
    r = shop.get("/shop/link", headers={"host": "evil host/../"})
    assert r.status_code == 400
    assert r.json()["reason"] == shopface.R_NO_HOST


def test_this_module_never_settles_money(shop) -> None:
    _save(shop)
    for res in (shop.get("/shop/link"), shop.get("/store/shop"),
                shop.post("/shop/link/renew"),
                shop.put("/shop/photo", json={"photo_b64": ""})):
        assert res.json()["settles_money"] is False


def test_the_router_carries_no_prefix_of_its_own() -> None:
    paths = {r.path for r in shopface.router.routes}  # type: ignore[attr-defined]
    assert paths == {"/store/shop", "/store/shop/photo", "/shop/link",
                     "/shop/link/qr", "/shop/link/renew", "/shop/photo"}


def test_shopadmins_own_route_set_is_untouched() -> None:
    """The slug helpers went into shopadmin; no route did."""
    paths = {r.path for r in shopadmin.router.routes}  # type: ignore[attr-defined]
    assert paths == {"/shop", "/shop/{sku_id}", "/shop/{sku_id}/photo",
                     "/shop/{sku_id}/history", "/shop/profile", "/shop/nameplate"}


def test_everything_written_lands_under_the_scratch_dir(shop, tmp_path) -> None:
    _save(shop)
    shop.put("/shop/photo", json={"photo_b64": _photo_png_b64(64)})
    for p in (shopadmin.profile_path(), shopface.photo_path(),
              shopadmin.audit_path()):
        assert p.exists()
        assert str(p).startswith(str(tmp_path))
