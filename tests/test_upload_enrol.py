"""tools/upload_app.py — the PHOTO -> PRODUCT path.

A shopkeeper photographs an item, types a name and a price, and from then on the
counter recognises that item and prices it. These tests exist to make three
claims checkable, because each of them is a claim a demo can fake:

  1. TEACH THEN SHOW.  A product enrolled from one photograph is recognised in a
     DIFFERENT photograph — moved to another part of the mat and turned to
     another angle. The two photographs are asserted to be different images, so
     the test cannot pass by re-presenting the same pixels.

  2. ABSTAIN RATHER THAN GUESS.  An item that was never taught comes back with
     sku_id None and a NAMED reason, and is EXCLUDED from the total. The
     intruder is deliberately the same size as a taught product, so the metric
     tiebreak cannot refuse it and the descriptor has to.

  3. MONEY IS INTEGER PAISE.  A float price is refused at the API — both as a
     sub-paisa rupee string and as a genuine JSON float — and never rounded.

Nothing here settles money and nothing here can mark a session GREEN.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from gawaah.identity import (  # noqa: E402
    ABSTAIN_REASONS,
    DEFAULT_PHI,
    DEFAULT_THETA,
    cosine,
)
from tools import upload_app  # noqa: E402
from tools.upload_app import (  # noqa: E402
    HARD_PAIR_PRODUCT,
    INTRUDER_PRODUCT,
    LAYOUT_TWIN_PRODUCT,
    MAT_H_MM,
    MAT_W_MM,
    PRODUCTS_BY_ID,
    R_BAD_PRICE,
    R_COLLISION,
    R_EMPTY_GALLERY,
    R_FIELD_MISSING,
    R_NO_ITEM,
    R_UNKNOWN_SKU,
    SAMPLE_PRODUCTS,
    app,
    enrol_pose,
    price_to_paise,
    product_scene,
    scene_png,
    scene_png_and_reference,
)

PARLE = PRODUCTS_BY_ID["parle_g_biscuit"]
SOAP = PRODUCTS_BY_ID["lifebuoy_soap"]
SACHET = PRODUCTS_BY_ID["shampoo_sachet"]


def empty_mat_png() -> bytes:
    """A photograph of the EMPTY mat, from the same camera as every scene here.

    This is what the tool's SET EMPTY-MAT REFERENCE button takes, and supplying
    it is a step a real shopkeeper performs once. It matters: without it the
    background is SYNTHESISED from the printed design, which does not cancel
    perfectly, and the mat's own 20 mm scale patch and exit arrow segment as
    small blobs. Those blobs abstain honestly rather than being priced — there
    is a test below that pins exactly that — but they are not products, and a
    suite that left them in would be measuring an artefact.
    """
    import cv2
    _, empty = product_scene([], seed=11)
    ok, buf = cv2.imencode(".png", empty)
    assert ok
    return buf.tobytes()


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """A client over a catalog that lives and dies with the test.

    The store is a directory on disk, so without this every test would inherit
    whatever the last one taught and the suite's result would depend on its
    order.
    """
    upload_app.set_store_dir(tmp_path / "shop")
    c = TestClient(app)
    c.delete("/reference")
    assert c.post("/reference", content=empty_mat_png(),
                  headers={"content-type": "image/png"}).json()["ok"] is True
    return c


_UNSET = object()


def teach(client: TestClient, product, *, price=_UNSET, sku_id=_UNSET,
          seed: int = 11):
    """Enrol one product from a simulated photograph, over real multipart.

    price and sku_id use a sentinel rather than `or`, because "" is a value a
    test deliberately sends and `or` would silently replace it with the valid
    default — turning a refusal test into a pass.
    """
    png = scene_png(enrol_pose(product), seed=seed)
    return client.post(
        "/enrol",
        files={"image": ("enrol.png", png, "image/png")},
        data={"sku_id": product.sku_id if sku_id is _UNSET else sku_id,
              "name": product.name,
              "price_rupees": (product.price_rupees if price is _UNSET
                               else price)},
    )


def recognise(client: TestClient, poses, seed: int = 23):
    png = scene_png(poses, seed=seed)
    return client.post("/recognise",
                       files={"image": ("scene.png", png, "image/png")})


# ------------------------------------------------- 1. teach it, then show it

def test_a_taught_product_is_recognised_in_a_different_photograph(
        client: TestClient) -> None:
    """The whole product, in one test.

    Enrol from a photo of the item alone, square on, mid-mat. Then present a
    DIFFERENT photo: another position, another angle, different sensor noise.
    """
    assert teach(client, PARLE).json()["ok"] is True

    moved = [(PARLE, 85.0, 105.0, 24.0)]
    res = recognise(client, moved).json()
    assert res["ok"] is True and res["locked"] is True

    named = res["named"]
    assert len(named) == 1, res["items"]
    assert named[0]["sku_id"] == "parle_g_biscuit"
    assert named[0]["reason"] == "match"
    # Priced from the catalog, in integer paise, and that is the total.
    assert named[0]["price_paise"] == 1000
    assert res["total_paise"] == 1000
    assert res["total_rupees"] == "10.00"


def test_the_two_photographs_are_genuinely_different_images() -> None:
    """Guards the test above from passing for the wrong reason.

    If the enrolment frame and the recognition frame were the same pixels, a
    byte hash would 'recognise' the product and the result would mean nothing.
    """
    enrolled, _ = product_scene(enrol_pose(PARLE), seed=11)
    presented, _ = product_scene([(PARLE, 85.0, 105.0, 24.0)], seed=23)
    assert enrolled.shape == presented.shape
    assert not np.array_equal(enrolled, presented)
    # ...and different by a lot, not by a rounding artefact.
    assert float(np.abs(enrolled.astype(int) - presented.astype(int)).mean()) > 1.0


@pytest.mark.parametrize("rotation", [0.0, 24.0, 47.0, 90.0, 180.0, 300.0])
def test_a_taught_product_survives_being_turned_round(
        client: TestClient, rotation: float) -> None:
    """A packet on a mat gets put down at whatever angle the hand leaves it."""
    teach(client, SOAP)
    res = recognise(client, [(SOAP, 150.0, 200.0, rotation)], seed=37).json()
    assert [i["sku_id"] for i in res["named"]] == ["lifebuoy_soap"]
    assert res["total_paise"] == 3500


def test_three_products_are_taught_and_then_totalled_together(
        client: TestClient) -> None:
    for p in SAMPLE_PRODUCTS:
        assert teach(client, p).json()["ok"] is True
    assert client.get("/shop").json()["count"] == 3

    poses = [(PARLE, 85.0, 105.0, 24.0),
             (SOAP, 205.0, 118.0, -31.0),
             (SACHET, 96.0, 268.0, 47.0)]
    res = recognise(client, poses).json()
    assert res["counts"]["named"] == 3, res["items"]
    assert res["counts"]["amber"] == 0
    # 1000 + 3500 + 300, in integer paise, computed by summing ints.
    assert res["total_paise"] == 1000 + 3500 + 300
    assert res["total_rupees"] == "48.00"


# ------------------------------- 2. abstain rather than guess, and EXCLUDE it

def test_an_untaught_product_is_amber_by_name_and_left_out_of_the_total(
        client: TestClient) -> None:
    """The heart of invariant 7.

    The intruder is the SAME 95 mm footprint as the taught biscuit, so the
    metric tiebreak cannot rule it out — it survives into the appearance stage
    and must be refused there, on the evidence. An intruder of an unusual size
    would be caught by the tape measure alone and would prove nothing.
    """
    teach(client, PARLE)
    assert INTRUDER_PRODUCT.long_edge_mm == PARLE.long_edge_mm

    res = recognise(client, [(PARLE, 85.0, 105.0, 24.0),
                             (INTRUDER_PRODUCT, 208.0, 300.0, -12.0)]).json()

    intruder = min(res["items"],
                   key=lambda i: abs(i["measured"]["centre_mm"][0] - 208.0))
    assert intruder["sku_id"] is None, (
        "AN UNTAUGHT PRODUCT WAS NAMED AND PRICED.\n"
        f"  {INTRUDER_PRODUCT.sku_id} was never enrolled, yet it came back as "
        f"{intruder['sku_id']!r} at {intruder['price_paise']} paise.\n"
        f"  top1={intruder['top1']} against the phi={DEFAULT_PHI} similarity "
        f"gate — it cleared the gate by {intruder['top1'] - DEFAULT_PHI:+.4f}.\n"
        "  This is a confident WRONG PRICE, the outcome invariant 7 exists to "
        "prevent.\n"
        "  It is NOT a bug in tools/upload_app.py: the crop is correct and the "
        "exclusion logic is proven by\n"
        "  test_an_abstention_is_always_excluded_whatever_the_descriptor_says. "
        "The two products are a\n"
        "  yellow packet with a blue band and a purple box with an orange dot; "
        "gawaah/embedder.py scores\n"
        "  them ~0.56 apart-from-identical. The gate must NOT be moved to hide "
        "this — the descriptor must separate them.")
    assert intruder["reason"] in ABSTAIN_REASONS
    assert intruder["reason"] == "below_similarity"
    assert intruder["price_paise"] is None
    assert intruder["explain"]                  # a named reason carries advice
    # It was CONSIDERED — the footprint filter let it through — and then refused
    # on appearance, below the similarity gate.
    assert intruder["n_candidates"] == 1
    assert intruder["top1"] < DEFAULT_PHI

    # ...and it is not in the money.
    assert res["total_paise"] == 1000
    assert res["excluded_count"] == 1
    assert "below_similarity" in res["amber_reasons"]


def test_an_abstention_is_always_excluded_whatever_the_descriptor_says(
        client: TestClient) -> None:
    """The half of the contract tools/upload_app.py actually owns.

    Whether identity abstains is the DESCRIPTOR's job. What this file must
    guarantee is the other half: whenever identity abstains, the item is amber,
    carries its named reason, has no price, and contributes exactly nothing to
    the total. That is proven here with a stub embedder that forces an
    abstention, so the guarantee holds no matter how the real descriptor
    performs — including on the day it regresses.
    """
    teach(client, PARLE)
    real = upload_app.load_embedder()
    calls = {"n": 0}

    def stub(crop):
        # First call is the enrolment vector already stored; every query after
        # returns a vector orthogonal to everything, so top1 collapses to 0.
        calls["n"] += 1
        v = np.zeros(int(np.asarray(real(crop)).ravel().shape[0]))
        v[0] = 1.0
        return v

    upload_app._DEPS["embed"] = stub
    try:
        res = recognise(client, [(PARLE, 85.0, 105.0, 24.0)]).json()
    finally:
        upload_app._DEPS["embed"] = None

    assert res["counts"]["named"] == 0
    assert res["counts"]["amber"] == 1
    row = res["amber"][0]
    assert row["sku_id"] is None
    assert row["reason"] == "below_similarity"
    assert row["top1"] < DEFAULT_PHI
    assert row["price_paise"] is None and row["price_rupees"] is None
    assert row["explain"]
    # The money is untouched by an abstention. This is the load-bearing line.
    assert res["total_paise"] == 0
    assert res["excluded_count"] == 1


def test_the_total_is_exactly_the_sum_of_the_named_items(
        client: TestClient) -> None:
    """No amber item may contribute a single paisa, by construction."""
    for p in SAMPLE_PRODUCTS:
        teach(client, p)
    res = recognise(client, [(PARLE, 85.0, 105.0, 24.0),
                             (SOAP, 205.0, 118.0, -31.0),
                             (INTRUDER_PRODUCT, 208.0, 300.0, -12.0)]).json()
    assert res["total_paise"] == sum(i["price_paise"] for i in res["named"])
    assert all(i["price_paise"] is None for i in res["amber"])
    assert res["counts"]["named"] + res["counts"]["amber"] == res["counts"]["placements"]


def test_nothing_taught_yet_means_every_item_is_amber_not_free(
        client: TestClient) -> None:
    """An empty shop must not silently total zero as though the mat were empty."""
    res = recognise(client, [(PARLE, 85.0, 105.0, 24.0)]).json()
    assert res["counts"]["named"] == 0
    assert res["total_paise"] == 0
    assert res["amber"][0]["reason"] == R_EMPTY_GALLERY
    assert res["catalog_size"] == 0


def test_an_item_of_an_unseen_size_is_refused_by_the_tape_measure(
        client: TestClient) -> None:
    """The other abstention: nothing in the gallery is even the right SIZE."""
    teach(client, PARLE)                                   # 95 mm only
    res = recognise(client, [(SACHET, 150.0, 200.0, 0.0)]).json()  # 38 mm
    row = res["amber"][0]
    assert row["reason"] == "no_candidate_in_footprint"
    assert row["n_candidates"] == 0
    assert res["total_paise"] == 0


# ------------------------------------------- 3. money is integer paise, always

@pytest.mark.parametrize("bad", ["214.507", "abc", "", "-5", "1e3", "0",
                                 "10.5.5", "₹10", "10,00", " ", "0.001"])
def test_a_price_that_is_not_exact_integer_paise_is_refused_at_the_api(
        client: TestClient, bad: str) -> None:
    """Refused, never rounded. Rounding a price is how a shop loses money it
    can never account for."""
    r = teach(client, PARLE, price=bad, sku_id="probe")
    assert r.status_code == 400, (bad, r.json())
    assert r.json()["reason"] in (R_BAD_PRICE, R_FIELD_MISSING), (bad, r.json())
    assert client.get("/shop").json()["count"] == 0        # nothing was stored


def test_a_genuine_float_price_is_refused_as_a_float(client: TestClient) -> None:
    """The strongest form of the claim.

    A multipart field is always a string, so multipart alone could never show
    that float-is-not-money holds. The JSON path lets a real Python/JS float
    reach the boundary, and it is refused for BEING a float.
    """
    png = scene_png(enrol_pose(PARLE))
    r = client.post("/enrol", json={
        "image": base64.b64encode(png).decode(),
        "sku_id": "parle_g_biscuit", "name": "Parle-G",
        "price_rupees": 214.5,
    })
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_PRICE
    assert "float is not money" in r.json()["detail"]
    assert client.get("/shop").json()["count"] == 0


@pytest.mark.parametrize("good,expect", [("10.00", 1000), ("10", 1000),
                                         ("0.05", 5), ("214.50", 21450),
                                         ("1", 100), ("999.99", 99999)])
def test_an_exact_rupee_string_becomes_exact_paise(good: str, expect: int) -> None:
    assert price_to_paise(good) == expect
    assert isinstance(price_to_paise(good), int)


def test_the_stored_price_is_the_integer_paise_that_was_asked_for(
        client: TestClient) -> None:
    j = teach(client, PARLE, price="214.50").json()
    assert j["stored"]["price_paise"] == 21450
    assert j["stored"]["price_rupees"] == "214.50"
    row = client.get("/shop").json()["skus"][0]
    assert row["price_paise"] == 21450 and row["price_rupees"] == "214.50"


# ------------------------------------------------- the enrolment collision guard

def test_a_pair_the_pipeline_cannot_separate_is_refused_at_enrolment(
        client: TestClient) -> None:
    """The 180-degree twin.

    placement.angle_deg is reported in [0, 180), so a packet laid head-up and
    the same packet laid head-down measure the SAME angle and produce crops that
    differ by a 180 degree turn. A cap-at-top packet and a cap-at-bottom packet
    are therefore the same observation, and no descriptor may separate them
    without also reporting two identities for one product. The guard catches it
    at enrolment, while fixing it is still free.
    """
    assert teach(client, PARLE).json()["ok"] is True
    r = teach(client, HARD_PAIR_PRODUCT)
    assert r.status_code == 400
    body = r.json()
    assert body["reason"] == R_COLLISION
    assert "parle_g_biscuit" in body["detail"]
    # Refused means refused: the catalog still holds exactly one product.
    assert client.get("/shop").json()["count"] == 1


def test_the_collision_is_a_real_measurement_not_a_rule_about_names() -> None:
    """The twin really is indistinguishable, and the proof is that the taught
    product scores the same against ITSELF rotated 180 degrees."""
    from gawaah.embedder import embed
    from tools.upload_app import _rectify_and_place, decode_upload, oriented_crop_bgr

    def vec(product, rot):
        png, ref = scene_png_and_reference(
            [(product, MAT_W_MM / 2, MAT_H_MM / 2, rot)])
        bgr, _ = decode_upload(png)
        rect, placements, _ = _rectify_and_place(bgr, reference=ref)
        p = max((x for x in placements if x.measurable and x.area_mm2),
                key=lambda x: x.area_mm2)
        return embed(oriented_crop_bgr(rect, p))

    self_flipped = cosine(vec(PARLE, 0.0), vec(PARLE, 180.0))
    against_twin = cosine(vec(PARLE, 0.0), vec(HARD_PAIR_PRODUCT, 0.0))
    # The twin is no further away than the product's own 180 degree rotation.
    # That is not a weak descriptor; it is the same picture.
    assert self_flipped > 0.99
    assert against_twin > 0.99


def test_a_layout_twin_that_is_not_a_rotation_IS_separated(
        client: TestClient) -> None:
    """The other half of the honest answer.

    Same footprint, same two colours, layout that is NOT a rotation of the
    other. A global colour histogram would score these at essentially 1.0. Both
    enrol cleanly and both are then identified correctly, with two candidates
    inside the footprint window — so the metric tiebreak did NOT do the work.
    """
    assert teach(client, PARLE).json()["ok"] is True
    assert teach(client, LAYOUT_TWIN_PRODUCT).json()["ok"] is True

    res = recognise(client, [(PARLE, 80.0, 110.0, 15.0),
                             (LAYOUT_TWIN_PRODUCT, 200.0, 300.0, -25.0)],
                    seed=41).json()
    got = sorted(i["sku_id"] for i in res["named"])
    assert got == ["glucose_biscuit", "parle_g_biscuit"], res["items"]
    for item in res["named"]:
        assert item["n_candidates"] == 2      # both were genuinely in contention
        assert item["margin"] >= DEFAULT_THETA
    assert res["total_paise"] == 1000 + 1400


# --------------------------------------------------------------- the catalog

def test_the_catalog_shows_what_was_taught_and_removes_it_again(
        client: TestClient) -> None:
    teach(client, PARLE)
    teach(client, SOAP)
    shop = client.get("/shop").json()
    assert shop["count"] == 2 and shop["priced"] == 2
    by_id = {s["sku_id"]: s for s in shop["skus"]}
    row = by_id["lifebuoy_soap"]
    assert row["name"] == SOAP.name
    assert row["price_paise"] == 3500 and row["price_rupees"] == "35.00"
    assert abs(row["footprint_mm"] - SOAP.long_edge_mm) < 2.0
    assert row["n_views"] == 1 and row["vector_dim"] > 0
    # The enrolment photo was kept, so the page can show what was taught.
    assert row["thumb_png"] and len(base64.b64decode(row["thumb_png"])) > 100

    assert client.delete("/shop/lifebuoy_soap").json()["ok"] is True
    assert client.get("/shop").json()["count"] == 1
    # ...and a removed product stops being priced rather than becoming free.
    res = recognise(client, [(SOAP, 150.0, 200.0, 10.0)], seed=51).json()
    assert res["counts"]["named"] == 0
    assert res["total_paise"] == 0


def test_removing_something_that_was_never_taught_says_so(
        client: TestClient) -> None:
    r = client.delete("/shop/never_taught")
    assert r.status_code == 404
    assert r.json()["reason"] == R_UNKNOWN_SKU


def test_re_teaching_a_sku_replaces_it_and_never_doubles_it(
        client: TestClient) -> None:
    teach(client, PARLE, price="10.00")
    again = teach(client, PARLE, price="12.50").json()
    assert again["ok"] is True
    assert again["stored"]["replaced_existing"] is True
    assert again["stored"]["price_paise"] == 1250
    shop = client.get("/shop").json()
    assert shop["count"] == 1                      # replaced, not appended
    assert shop["skus"][0]["price_paise"] == 1250


def test_the_catalog_survives_a_restart(client: TestClient, tmp_path: Path) -> None:
    """A shopkeeper who taught twenty products must not lose them to a reboot."""
    teach(client, PARLE)
    upload_app.set_store_dir(tmp_path / "shop")     # drops the cached store
    fresh = TestClient(app)
    shop = fresh.get("/shop").json()
    assert shop["count"] == 1
    assert shop["skus"][0]["price_paise"] == 1000
    res = recognise(fresh, [(PARLE, 85.0, 105.0, 24.0)]).json()
    assert res["total_paise"] == 1000


# ------------------------------------------------------ refusals, by name

def test_enrolling_with_no_mat_lock_says_how_many_markers_were_found(
        client: TestClient) -> None:
    """The commonest real failure, and the message is the product."""
    import cv2
    png = scene_png(enrol_pose(PARLE))
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    img[: img.shape[0] // 2, :] = 235                  # wipe the top two markers
    ok, buf = cv2.imencode(".png", img)
    assert ok

    r = client.post("/enrol", files={"image": ("x.png", buf.tobytes(), "image/png")},
                    data={"sku_id": "p", "name": "p", "price_rupees": "10.00"})
    assert r.status_code == 400
    body = r.json()
    assert body["locked"] is False
    d = body["diagnosis"]
    assert d["markers_found"] < 4
    assert d["corners_missing"]
    assert d["fix"], "a refusal with no advice is not a refusal, it is a shrug"
    assert client.get("/shop").json()["count"] == 0


def test_an_empty_mat_cannot_be_enrolled_as_a_product(client: TestClient) -> None:
    png = scene_png([])
    r = client.post("/enrol", files={"image": ("e.png", png, "image/png")},
                    data={"sku_id": "ghost", "name": "ghost",
                          "price_rupees": "10.00"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_NO_ITEM
    assert client.get("/shop").json()["count"] == 0


def test_the_mats_own_printing_can_never_be_taught_as_a_product(
        client: TestClient) -> None:
    """A regression for a bug this suite actually caught.

    With no empty-mat photo the background is synthesised from the printed
    design and does not cancel exactly, so the mat's 20 mm scale patch and its
    exit arrow survive as small blobs. Enrolment takes the LARGEST blob — and on
    an empty mat that is the mat's own printing. Before this was fixed, POSTing
    a photo of a BARE mat to /enrol stored the scale patch as a 21.0 mm product
    and /recognise then billed it at the price the operator had typed: a
    confident wrong price, which is the one outcome the whole system exists to
    prevent. Teaching now refuses without an honest background.
    """
    client.delete("/reference")
    r = client.post("/enrol",
                    files={"image": ("bare.png", scene_png([]), "image/png")},
                    data={"sku_id": "ghost", "name": "ghost",
                          "price_rupees": "10.00"})
    assert r.status_code == 400
    assert r.json()["reason"] == "empty_mat_reference_required"
    assert client.get("/shop").json()["count"] == 0

    # A real product photo is refused for the same reason, and for the same
    # good cause: we cannot yet tell the product from the mat's own furniture.
    r2 = teach(client, PARLE)
    assert r2.status_code == 400
    assert r2.json()["reason"] == "empty_mat_reference_required"
    assert client.get("/shop").json()["count"] == 0


def test_recognition_without_a_reference_abstains_rather_than_misprices(
        client: TestClient) -> None:
    """Recognition is allowed to run on a synthesised background, because its
    mistakes are transient and abstain safely — but they must ABSTAIN.

    The printed residue shows up as extra placements. Every one of them must
    come back amber with a named reason and contribute nothing to the total.
    """
    teach(client, PARLE)                       # taught with a real reference
    client.delete("/reference")                # now take the reference away

    res = recognise(client, [(PARLE, 85.0, 105.0, 24.0)]).json()
    assert res["reference_source"] == "synthesised_from_printed_design"
    assert res["counts"]["placements"] > 1     # the residue really is there
    assert [i["sku_id"] for i in res["named"]] == ["parle_g_biscuit"]
    for row in res["amber"]:
        assert row["sku_id"] is None
        assert row["reason"] in ABSTAIN_REASONS
        assert row["price_paise"] is None
    # The residue changed the item list but not one paisa of the money.
    assert res["total_paise"] == 1000


def test_a_missing_field_is_named_rather_than_guessed(client: TestClient) -> None:
    png = scene_png(enrol_pose(PARLE))
    r = client.post("/enrol", files={"image": ("a.png", png, "image/png")},
                    data={"name": "no sku", "price_rupees": "10.00"})
    assert r.status_code == 400
    assert r.json()["reason"] == "sku_id_invalid"

    r2 = client.post("/enrol", data={"sku_id": "a", "name": "a",
                                     "price_rupees": "10.00"})
    assert r2.status_code == 400
    assert r2.json()["reason"] == R_FIELD_MISSING


@pytest.mark.parametrize("bad_sku", ["../etc/passwd", "a b", "x" * 65, "",
                                     "a/b", "sku;rm -rf"])
def test_a_sku_id_that_would_become_a_bad_filename_is_refused(
        client: TestClient, bad_sku: str) -> None:
    r = teach(client, PARLE, sku_id=bad_sku)
    assert r.status_code == 400
    assert r.json()["reason"] == "sku_id_invalid"


@pytest.mark.parametrize("endpoint", ["/enrol", "/recognise"])
@pytest.mark.parametrize("body", [b"", b"not an image at all", b"\x89PNG\r\n\x1a\n"])
def test_rubbish_never_produces_a_500(client: TestClient, endpoint: str,
                                      body: bytes) -> None:
    """A crash is the one answer that teaches the user nothing."""
    r = client.post(endpoint, files={"image": ("x.bin", body, "image/png")},
                    data={"sku_id": "a", "name": "a", "price_rupees": "10.00"})
    assert r.status_code == 400
    assert r.json()["reason"]
    assert r.json()["ok"] is False


@pytest.mark.parametrize("boundary", [
    "abc123def456",                                   # httpx: lower-case hex
    "AbC123XyZ",                                      # mixed case
    "----------------------------bdQl6HuAckTKkW7t6CMR5V",   # what curl sends
    "----WebKitFormBoundaryE19zNvXGzXaLvS5C",         # what a browser sends
])
def test_a_multipart_boundary_keeps_its_case(client: TestClient,
                                             boundary: str) -> None:
    """A bug that no ordinary test in this file could have caught.

    read_form lower-cased the Content-Type before handing it to the parser, so
    the separator was lower-cased too. Boundaries are CASE-SENSITIVE, so every
    part failed to match and the form arrived with all fields missing. httpx —
    which TestClient uses everywhere else here — builds its boundary out of
    lower-case hex, making the bug invisible to the whole suite while `curl -F`
    and every real browser upload silently lost their fields. So this test
    builds the body by hand and names the boundaries that actually occur.
    """
    png = scene_png(enrol_pose(PARLE))
    sep = ("--" + boundary).encode()
    body = b"".join([
        sep, b'\r\nContent-Disposition: form-data; name="sku_id"\r\n\r\n',
        b"parle_g_biscuit\r\n",
        sep, b'\r\nContent-Disposition: form-data; name="name"\r\n\r\n',
        b"Parle-G 100g\r\n",
        sep, b'\r\nContent-Disposition: form-data; name="price_rupees"\r\n\r\n',
        b"10.00\r\n",
        sep, b'\r\nContent-Disposition: form-data; name="image"; '
             b'filename="a.png"\r\nContent-Type: image/png\r\n\r\n', png,
        b"\r\n", sep, b"--\r\n",
    ])
    r = client.post("/enrol", content=body, headers={
        "content-type": "multipart/form-data; boundary=" + boundary})
    assert r.status_code == 200, (boundary, r.json())
    assert r.json()["stored"]["price_paise"] == 1000
    assert client.get("/shop").json()["count"] == 1


def test_a_form_that_is_not_multipart_is_named_not_crashed(
        client: TestClient) -> None:
    r = client.post("/recognise", content=b"raw bytes",
                    headers={"content-type": "text/plain"})
    assert r.status_code == 400
    assert r.json()["reason"] == "form_not_multipart"


# ------------------------------------------------------------- the invariants

def test_no_response_on_this_path_ever_settles_money(client: TestClient) -> None:
    """INVARIANT 2: recognition proposes; only a verified webhook disposes."""
    teach(client, PARLE)
    for r in (teach(client, SOAP).json(),
              recognise(client, [(PARLE, 85.0, 105.0, 24.0)]).json(),
              client.get("/shop").json()):
        assert r["settles_money"] is False
        assert "GREEN" in r["money_note"]
        assert "webhook" in r["money_note"]


def test_the_uploaded_photograph_is_never_echoed_back(client: TestClient) -> None:
    """INVARIANT 4: only the rectified 840x1188 metric buffer survives."""
    j = teach(client, PARLE).json()
    assert j["source_image_returned"] is False
    res = recognise(client, [(PARLE, 85.0, 105.0, 24.0)]).json()
    assert res["source_image_returned"] is False
    # What IS returned is the rectified buffer at half scale, not the upload.
    import cv2
    from gawaah.takhti import BUF_H, BUF_W
    img = cv2.imdecode(np.frombuffer(base64.b64decode(res["overlay_png"]),
                                     np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[:2] == (BUF_H // 2, BUF_W // 2)


def test_the_identity_gates_are_the_librarys_own_and_are_not_widened() -> None:
    """INVARIANT 7: the thresholds are never loosened to flatter a demo."""
    assert upload_app.THETA == DEFAULT_THETA
    assert upload_app.PHI == DEFAULT_PHI
    assert upload_app.TAU_MM == 4.0


def test_upgrading_the_embedder_under_an_old_catalog_refuses_by_name(
        client: TestClient) -> None:
    """Not hypothetical: the descriptor's dimension changed twice while this
    file was being written.

    Every vector in a stored catalog was produced by one particular embedder. If
    the embedder is changed, those vectors are meaningless — and the dangerous
    outcome is not a crash, it is a plausible-looking match at a wrong price.
    Both endpoints must refuse by name and the total must stay at zero.
    """
    teach(client, PARLE)
    real = upload_app.load_embedder()
    upload_app._DEPS["embed"] = lambda crop: np.concatenate(
        [np.asarray(real(crop), dtype=float), [0.5]])       # one extra dimension
    try:
        res = recognise(client, [(PARLE, 85.0, 105.0, 24.0)]).json()
        assert res["counts"]["named"] == 0
        assert res["total_paise"] == 0
        assert res["amber"][0]["reason"] == "identity_refused"
        assert "dimension" in res["amber"][0]["explain"]

        r = teach(client, SOAP)
        assert r.status_code == 400
        assert r.json()["reason"] == "identity_refused"
    finally:
        upload_app._DEPS["embed"] = None                    # re-resolve the real one


def test_health_reports_the_gates_and_that_there_are_no_model_weights(
        client: TestClient) -> None:
    h = client.get("/health").json()
    assert h["dependencies"]["embedder"]["available"] is True
    assert h["dependencies"]["shop_store"]["available"] is True
    assert h["identity_gates"]["phi"] == DEFAULT_PHI
    assert "none" in h["model_weights"]


# ------------------------------------------------------------------ the page

def test_the_pages_javascript_escapes_survive_into_the_browser() -> None:
    r"""A regression with no other symptom.

    PAGE carries JS regexes (\d) and escaped quotes (\'). If PAGE stops being a
    raw string, Python eats the backslashes, \' collapses to ' and the catalog's
    REMOVE button emits JavaScript that will not parse — a failure invisible to
    every Python test and fatal in a browser.
    """
    page = upload_app.PAGE
    assert r"removeSku(\'" in page
    assert r"/^\d+(\.\d{1,2})?$/" in page


# ------------------------------------------------------------- the demo path

def test_the_demo_teaches_and_then_recognises_end_to_end(
        client: TestClient) -> None:
    """The mouse-only round trip, for a visitor with no mat and no camera."""
    taught = client.post("/demo/teach").json()
    assert taught["ok"] is True
    assert taught["simulated"] is True
    assert [t["sku_id"] for t in taught["taught"] if t["ok"]] == [
        p.sku_id for p in SAMPLE_PRODUCTS]
    for row in taught["taught"]:
        assert row["err_long_mm"] <= 2.0, row      # measured against known truth

    res = client.post("/demo/recognise?intruder=1").json()
    assert res["simulated"] is True
    # The scene truth says which item was never taught, and that is the amber.
    untaught = [t["sku_id"] for t in res["scene_truth"] if not t["taught"]]
    assert untaught == [INTRUDER_PRODUCT.sku_id]

    # The demo marks its own homework; the mark must be clean.
    sc = res["scoring"]
    assert sc["correct"] == 3, sc["rows"]
    assert sc["honest"] is True, sc["headline"]
    assert sc["mis_named_untaught"] == 0
    assert sc["mis_priced_paise"] == 0

    assert res["counts"]["named"] == 3
    assert res["counts"]["amber"] == 1
    assert res["total_paise"] == 4800


def test_the_demo_hard_pair_shows_both_outcomes(client: TestClient) -> None:
    """One twin enrols, the other is refused. Both answers are correct."""
    taught = client.post("/demo/teach",
                         data={"hard_pair": "1"}).json()
    by_id = {t["sku_id"]: t for t in taught["taught"]}
    assert by_id["glucose_biscuit"]["ok"] is True
    assert by_id["jeera_biscuit"]["ok"] is False
    assert by_id["jeera_biscuit"]["reason"] == R_COLLISION


def test_the_demo_photo_is_a_real_image_and_is_labelled_simulated(
        client: TestClient) -> None:
    r = client.get("/demo/photo?sku=parle_g_biscuit")
    assert r.status_code == 200
    assert r.headers["x-gawaah-simulated"] == "true"
    import cv2
    img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None and img.ndim == 3
    assert client.get("/demo/photo?sku=nope").status_code == 404


# --------------------------------------------------------------------------
# THE PORTRAIT-PHOTO TRUNCATION, 2026-08-29.
#
# _oriented_crop_from_rect rotated inside the SOURCE frame and then clamped the
# crop to the frame's width. A tall packet photographed in portrait -- a phone
# photo, or any catalogue image -- has its long edge vertical, so turning it
# upright needs a canvas WIDER than the frame, and the clamp silently cut the
# product down to a slice of itself. On a real 1000x319 product photo the crop
# wanted 846 px of an 846 px long edge and got 319: 62% of the item thrown
# away. It matched itself perfectly, so nothing looked wrong until the same
# item at a 12-degree tilt scored 0.8478 and the counter abstained.
#
# Recall across 16 views of one product went 8/16 -> 15/16 when this was fixed.
# --------------------------------------------------------------------------


def _portrait_packet() -> np.ndarray:
    """A tall packet in a portrait frame: long edge VERTICAL, as a phone shoots."""
    import cv2

    img = np.full((1000, 319, 3), 255, np.uint8)
    cv2.rectangle(img, (20, 110), (299, 890), (52, 74, 196), -1)
    cv2.rectangle(img, (20, 110), (299, 300), (250, 250, 250), -1)
    cv2.circle(img, (160, 560), 78, (250, 250, 250), -1)
    cv2.rectangle(img, (60, 700), (260, 780), (30, 160, 90), -1)
    return img


def test_a_tall_packet_in_a_portrait_photo_is_not_cropped_down_to_a_slice() -> None:
    """The upright crop keeps the whole long edge even when the frame is narrower."""
    img = _portrait_packet()
    crop, ev = upload_app.plain_crop(img)
    long_px = float(ev["region_long_px"])

    # The long edge is ~780 px in a frame only 319 px wide. Before the fix the
    # crop came back 319 wide -- the frame, not the packet.
    assert long_px > img.shape[1], (
        "the fixture must actually exercise the bug: its long edge "
        f"({long_px:.0f} px) has to exceed the frame width ({img.shape[1]} px)")
    assert crop.shape[1] >= long_px * 0.95, (
        f"the crop is {crop.shape[1]} px wide but the packet's long edge is "
        f"{long_px:.0f} px. The product was truncated against the frame.")


def test_a_tilted_packet_still_matches_the_upright_one_it_was_taught_from() -> None:
    """Deskewing is what buys tilt tolerance -- so prove it survives a tilt."""
    import cv2

    from gawaah.embedder import embed
    from gawaah.identity import PHI_APPEARANCE_ONLY

    img = _portrait_packet()

    def rotated(deg: float) -> np.ndarray:
        h, w = img.shape[:2]
        c = (w / 2.0, h / 2.0)
        m = cv2.getRotationMatrix2D(c, deg, 1.0)
        cos, sin = abs(m[0, 0]), abs(m[0, 1])
        nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
        m[0, 2] += nw / 2.0 - c[0]
        m[1, 2] += nh / 2.0 - c[1]
        return cv2.warpAffine(img, m, (nw, nh), borderValue=(255, 255, 255))

    def unit(a: np.ndarray) -> np.ndarray:
        crop, _ = upload_app.plain_crop(a)
        v = np.asarray(embed(crop), dtype=np.float64).ravel()
        return v / np.linalg.norm(v)

    gallery = [unit(img)]
    gallery.append(unit(cv2.rotate(img, cv2.ROTATE_180)))

    for deg in (12.0, -20.0, 35.0):
        score = max(float(unit(rotated(deg)) @ g) for g in gallery)
        assert score >= PHI_APPEARANCE_ONLY, (
            f"a {deg:+.0f}-degree tilt of the very packet the gallery was built "
            f"from scores {score:.4f}, under the {PHI_APPEARANCE_ONLY} "
            "appearance-only bar, so the counter would abstain on its own product")


# --------------------------------------------------------------------------
# THE PRODUCT CODE PATH.
#
# A decoded QR is an IDENTIFIER, not a similarity, so when one is present it
# settles identity outright and the cosine gates are not consulted. That makes
# it the shortest route from a photograph to a price in this whole program, and
# the short routes are the ones worth guarding: an exact answer that is exactly
# wrong is worse than an abstention.
# --------------------------------------------------------------------------


def _png_bytes(img: np.ndarray) -> bytes:
    import cv2

    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def _qr_png(payload: str, px: int = 320) -> np.ndarray:
    import cv2

    enc = cv2.QRCodeEncoder.create()
    q = enc.encode(payload)
    q = (q * 255).astype(np.uint8) if q.max() <= 1 else q.astype(np.uint8)
    q = cv2.resize(q, (px, px), interpolation=cv2.INTER_NEAREST)
    return cv2.cvtColor(q, cv2.COLOR_GRAY2BGR)


def _scene_with_qr(payload: str, px: int = 320) -> np.ndarray:
    import cv2

    frame = np.full((720, 1280, 3), 208, np.uint8)
    cv2.rectangle(frame, (430, 170), (850, 560), (70, 90, 160), -1)
    q = _qr_png(payload, px)
    x, y = 640 - px // 2, 360 - px // 2
    frame[y:y + px, x:x + px] = q
    return frame


def test_a_product_code_names_the_sku_without_consulting_the_cosine_gates() -> None:
    sku, name, paise = "qr_taught", "QR taught product", 4250
    img = _scene_with_qr(f"{upload_app.QR_PREFIX}{sku}")
    crop, _ = upload_app.plain_crop(img)
    embed = upload_app.load_embedder()
    upload_app._ao_put(sku, name, paise,
                       [np.asarray(embed(crop), dtype=np.float64)
                        .ravel().tolist()], None)
    try:
        out = upload_app.do_recognise_plain(_png_bytes(img))
        assert out["ok"] is True
        assert out["identified_by"] == "qr_code"
        item = out["items"][0]
        assert item["sku_id"] == sku
        assert item["price_paise"] == paise, "the catalogue price must be used"
        assert item["gate"] == "product_code"
        assert item["top1"] is None, (
            "a code was read, not a likeness judged — reporting a cosine here "
            "would dress an identifier up as a similarity")
        assert out["total_paise"] == paise
    finally:
        upload_app._ao_remove(sku)


def test_a_product_code_for_an_untaught_sku_is_refused_not_guessed() -> None:
    """The sticker answered exactly, and the answer names nothing we know."""
    # SOMETHING has to be taught, or the empty-gallery refusal fires first and
    # this proves nothing about codes. What is taught is deliberately not the
    # sku the sticker names.
    upload_app._ao_put("qr_bystander", "Bystander", 100, [[0.0] * 8], None)
    try:
        img = _scene_with_qr(f"{upload_app.QR_PREFIX}never_taught_anything")
        with pytest.raises(upload_app.UploadRefused) as excinfo:
            upload_app.do_recognise_plain(_png_bytes(img))
        assert excinfo.value.reason == upload_app.R_QR_UNKNOWN
        assert "never_taught_anything" in str(excinfo.value.detail)
    finally:
        upload_app._ao_remove("qr_bystander")


def test_a_code_that_is_not_ours_does_not_name_anything() -> None:
    """A packet already carries other people's QR codes. None of them price it."""
    sku, paise = "qr_ignore", 1500
    img = _scene_with_qr("https://example.invalid/some-marketing-campaign")
    crop, _ = upload_app.plain_crop(img)
    embed = upload_app.load_embedder()
    upload_app._ao_put(sku, "unrelated", paise,
                       [np.asarray(embed(crop), dtype=np.float64)
                        .ravel().tolist()], None)
    try:
        out = upload_app.do_recognise_plain(_png_bytes(img))
        # It may or may not match on APPEARANCE — that is the appearance path's
        # business. What must not happen is a foreign code naming an sku.
        assert out.get("identified_by") != "qr_code"
        assert out["items"][0].get("gate") != "product_code"
    finally:
        upload_app._ao_remove(sku)


def test_the_printed_sticker_decodes_back_to_the_sku_it_was_made_for() -> None:
    """A sticker nobody can read is a sticker that does nothing."""
    import cv2

    sku, paise = "qr_roundtrip", 999
    upload_app._ao_put(sku, "Round trip", paise, [[0.0] * 8], None)
    try:
        client = TestClient(upload_app.app)
        r = client.get(f"/qr/{sku}")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "image/png"
        card = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
        decoded = cv2.QRCodeDetector().detectAndDecode(card)[0]
        assert decoded == f"{upload_app.QR_PREFIX}{sku}"
    finally:
        upload_app._ao_remove(sku)


def test_the_sticker_does_not_carry_a_price() -> None:
    """Two places for a price is one place for them to disagree."""
    sku, paise = "qr_nopricetag", 12345
    upload_app._ao_put(sku, "No price on me", paise, [[0.0] * 8], None)
    try:
        client = TestClient(upload_app.app)
        card = client.get(f"/qr/{sku}").content
        import cv2

        img = cv2.imdecode(np.frombuffer(card, np.uint8), cv2.IMREAD_COLOR)
        payload = cv2.QRCodeDetector().detectAndDecode(img)[0]
        assert "123.45" not in payload and str(paise) not in payload, (
            "the code must name the product only; the catalogue prices it")
    finally:
        upload_app._ao_remove(sku)


# --------------------------------------------------------------------------
# THE SUPERMARKET LANE: many printed codes, one frame, one total.
#
# A shopper puts a whole basket on the counter. Reading one code per frame
# would turn a supermarket into a queue of one, so the basket path reads every
# symbol in the picture and prices each. That makes it the shortest route from
# a photograph to a MULTI-LINE BILL in this program, and every one of those
# lines is money.
# --------------------------------------------------------------------------

_L = ['0001101', '0011001', '0010011', '0111101', '0100011',
      '0110001', '0101111', '0111011', '0110111', '0001011']
_G = ['0100111', '0110011', '0011011', '0100001', '0011101',
      '0111001', '0000101', '0010001', '0001001', '0010111']
_R = ['1110010', '1100110', '1101100', '1000010', '1011100',
      '1001110', '1010000', '1000100', '1001000', '1110100']
_PAR = {0: 'LLLLLL', 1: 'LLGLGG', 2: 'LLGGLG', 3: 'LLGGGL', 4: 'LGLLGG',
        5: 'LGGLLG', 6: 'LGGGLL', 7: 'LGLGLG', 8: 'LGLGGL', 9: 'LGGLGL'}


def _ean13(twelve: str) -> str:
    """A valid EAN-13 from twelve digits — check digit computed, not guessed."""
    s = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(twelve))
    return twelve + str((10 - s % 10) % 10)


def _ean_png(code: str, module_px: int = 4, bars_px: int = 120) -> np.ndarray:
    """The real EAN-13 symbol for `code`. Not a picture of stripes: a decoder
    has to be able to read it back, or the test proves nothing."""
    import cv2

    d = [int(c) for c in code]
    bits = '101'
    for i, p in enumerate(_PAR[d[0]]):
        bits += (_L if p == 'L' else _G)[d[i + 1]]
    bits += '01010'
    for i in range(7, 13):
        bits += _R[d[i]]
    bits += '101'
    quiet = 10
    w = (len(bits) + 2 * quiet) * module_px
    img = np.full((bars_px + 22, w), 255, np.uint8)
    x = quiet * module_px
    for ch in bits:
        if ch == '1':
            img[:bars_px, x:x + module_px] = 0
        x += module_px
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def _packet(code: str) -> np.ndarray:
    """One packet, photographed alone, its barcode readable."""
    card = np.full((420, 760, 3), 218, np.uint8)
    b = _ean_png(code)
    h, w = b.shape[:2]
    card[150:150 + h, 60:60 + w] = b
    return card


def _counter(codes: list[str]) -> np.ndarray:
    """Several packets on a counter at once, none painted over another.

    The packets are ALL drawn first and the codes on top, because an earlier
    version of this fixture drew each packet's body after the previous packet's
    barcode and silently erased it — the test then 'proved' that multi-read
    does not work.
    """
    import cv2

    frame = np.full((760, 1400, 3), 208, np.uint8)
    spots = [(40, 40), (720, 40), (40, 420), (720, 420)]
    for (x, y) in spots[:len(codes)]:
        cv2.rectangle(frame, (x - 16, y - 16), (x + 600, y + 300), (70, 92, 170), -1)
    for code, (x, y) in zip(codes, spots):
        b = _ean_png(code)
        h, w = b.shape[:2]
        frame[y:y + h, x:x + w] = b
    return cv2.GaussianBlur(frame, (3, 3), 0)


def _teach_by_code(client: TestClient, sku: str, name: str, rupees: str,
                   code: str):
    return client.post("/enrol",
                       files={"image": ("p.png", _png_bytes(_packet(code)), "image/png")},
                       data={"sku_id": sku, "name": name, "price_rupees": rupees,
                             "mode": "basket"})


def test_a_product_taught_by_its_barcode_is_priced_when_that_code_is_shown(
        client: TestClient) -> None:
    code = _ean13("890131430115")
    r = _teach_by_code(client, "tooth", "Colgate MaxFresh 150g", "99", code).json()
    assert r["ok"] is True, r
    assert r["stored"]["code"] == code
    assert r["stored"]["taught_with"] == "product_code_only"

    seen = client.post("/recognise",
                       files={"image": ("c.png", _png_bytes(_counter([code])), "image/png")},
                       data={"mode": "basket"}).json()
    assert seen["ok"] is True
    assert seen["total_paise"] == 9900
    item = seen["items"][0]
    assert item["sku_id"] == "tooth"
    assert item["gate"] == "product_code"
    assert item["top1"] is None, (
        "a code was READ, not a likeness judged — reporting a cosine would "
        "dress an identifier up as a similarity")


def test_a_whole_basket_is_read_in_one_frame_and_totalled_in_integer_paise(
        client: TestClient) -> None:
    """The point of the whole path: one photograph, several bills' worth."""
    spec = [("890131430115", "tooth", "Toothpaste", "99", 9900),
            ("890000012345", "bisc", "Biscuit", "10", 1000),
            ("890999988877", "soap", "Soap", "35", 3500)]
    codes = []
    for twelve, sku, name, rupees, _ in spec:
        code = _ean13(twelve)
        codes.append(code)
        assert _teach_by_code(client, sku, name, rupees, code).json()["ok"] is True

    res = client.post("/recognise",
                      files={"image": ("c.png", _png_bytes(_counter(codes)), "image/png")},
                      data={"mode": "basket"}).json()
    assert res["ok"] is True
    assert res["codes_found"] >= 3, res
    assert res["counts"]["named"] == 3, [i["code"] for i in res["items"]]
    want = sum(p for *_, p in spec)
    assert res["total_paise"] == want
    assert isinstance(res["total_paise"], int)
    assert res["total_rupees"] == "144.00"


def test_an_untaught_code_is_a_row_with_a_reason_not_a_silence(
        client: TestClient) -> None:
    """A short bill that looks complete is worse than one that says it is short."""
    taught = _ean13("890131430115")
    stranger = _ean13("890555544433")
    _teach_by_code(client, "tooth", "Toothpaste", "99", taught)

    res = client.post("/recognise",
                      files={"image": ("c.png",
                                       _png_bytes(_counter([taught, stranger])),
                                       "image/png")},
                      data={"mode": "basket"}).json()
    assert res["total_paise"] == 9900, "the untaught code must not be priced"
    codes = {i["code"]: i for i in res["items"]}
    assert stranger in codes, "the untaught code vanished from the bill entirely"
    row = codes[stranger]
    assert row["sku_id"] is None and row["price_paise"] is None
    assert row["reason"] == "code_not_taught"
    assert stranger in row["explain"]
    assert res["counts"]["amber"] == 1
    assert res["excluded_count"] == 1


def test_two_codes_in_one_teaching_photo_bind_neither(client: TestClient) -> None:
    """Ambiguity is refused by name. Binding the wrong one prices the wrong packet."""
    a, b = _ean13("890131430115"), _ean13("890000012345")
    r = client.post("/enrol",
                    files={"image": ("c.png", _png_bytes(_counter([a, b])), "image/png")},
                    data={"sku_id": "amb", "name": "Ambiguous", "price_rupees": "5",
                          "mode": "basket"})
    body = r.json()
    assert body["ok"] is False
    assert body["reason"] == "several_codes_in_frame"
    assert upload_app.resolve_code(a) is None
    assert upload_app.resolve_code(b) is None


def test_a_typed_code_beats_whatever_happens_to_be_in_the_photograph(
        client: TestClient) -> None:
    """A shopkeeper typing the number is stating a fact about the packet."""
    in_shot, typed = _ean13("890131430115"), _ean13("890777766655")
    r = client.post("/enrol",
                    files={"image": ("p.png", _png_bytes(_packet(in_shot)), "image/png")},
                    data={"sku_id": "typed", "name": "Typed", "price_rupees": "7",
                          "mode": "basket", "barcode": typed})
    assert r.json()["ok"] is True
    assert upload_app.resolve_code(typed) == "typed"
    assert upload_app.resolve_code(in_shot) is None, (
        "a code merely visible in the frame was bound over the one typed")


def test_teaching_by_code_alone_says_it_knows_nothing_about_the_product(
        client: TestClient) -> None:
    """The weakest path in the program must be the loudest about it."""
    code = _ean13("890131430115")
    r = _teach_by_code(client, "tooth", "Toothpaste", "99", code).json()
    assert r["stored"]["size_check"] == "none"
    assert r["stored"]["appearance_check"] == "none"
    assert r["stored"]["n_views"] == 0 and r["stored"]["vector_dim"] == 0
    w = r["warning"].lower()
    assert "knows" in w and "nothing" in w
    assert "wrong packet" in w
    assert r["better"], "the stronger path must be named, not merely implied"


def test_rebinding_a_code_to_another_product_is_reported_never_silent(
        client: TestClient) -> None:
    """A code that quietly changed what it prices is a wrong bill with a
    clean-looking audit trail."""
    code = _ean13("890131430115")
    _teach_by_code(client, "first", "First", "10", code)
    r = _teach_by_code(client, "second", "Second", "20", code).json()
    assert r["ok"] is True
    binding = r["codes"]["bound"][0]
    assert binding["action"] == "rebound"
    assert binding["previous_sku"] == "first"
    assert upload_app.resolve_code(code) == "second"


def test_our_own_sticker_needs_no_binding_but_a_foreign_code_does(
        client: TestClient) -> None:
    """`gawaah:` carries the sku itself; anything else means nothing until taught."""
    assert upload_app.resolve_code("gawaah:whatever") == "whatever"
    assert upload_app.resolve_code("8901314301154") is None
    assert upload_app.resolve_code("https://example.invalid/promo") is None
