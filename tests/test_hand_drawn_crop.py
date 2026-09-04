"""A rectangle a HUMAN drew around a product, and what the server may conclude.

THE DEFECT THESE PIN. The teach screen tells the operator "DRAW A BOX AROUND
THE PRODUCT — only the box is checked and taught". They draw a tight box round
a medicine carton, press TEACH THIS PRODUCT, and are refused:

    matless_region_touches_every_border
    The object runs off all four edges of this photograph, so I cannot see
    where it ends and I would be describing a piece of it.

The refusal is correct for a photograph nobody has cropped: there, "the region
fills the frame" means the segmenter could not find an edge and might be
describing the wall. It is WRONG for a rectangle a person deliberately drew,
because a tight box means the product fills it — doing exactly what the screen
asked is what triggered the refusal.

So `region=user_drawn` takes the rectangle AS the segmentation and does not
re-derive it. What these tests hold down is that this is a narrowing of ONE
question and not a general softening:

  * the four-border refusal still fires, unchanged, on the uncropped path
    (`test_the_border_refusal_is_untouched_on_the_uncropped_path`), which is
    load-bearing — FAILURES.md records a stored reference that was 58.5% pure
    black because segmentation went wrong and nothing said so;
  * a flat, empty or too-small box is still refused BY NAME on the hand-drawn
    path, so a person cannot draw a rectangle around a wall and be believed;
  * and the flag has to be asked for: a request that does not send it gets the
    careful path.

Both env vars are set on every test that writes: a harness with the live
catalogue in reach has destroyed it once already.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

# `from tools import upload_app`, EXACTLY AS THE REST OF THE SUITE DOES — a
# bare `import upload_app` puts a second, independent copy in sys.modules.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from tools import upload_app  # noqa: E402
from tools.upload_app import (  # noqa: E402
    R_MATLESS_CROPPED,
    R_MATLESS_FLAT,
    R_MATLESS_TINY,
    REGION_USER_DRAWN,
    UploadRefused,
    app,
    hand_drawn_crop,
    plain_crop,
    read_region,
)


# --------------------------------------------------------------- the scenes --
#
# THE FIRST THREE SCENES I WROTE DID NOT REPRODUCE THE BUG, AND LOOKED AS IF
# THEY DID. A white carton with a coloured band top and bottom, a two-tone
# split, a busy wrapper: `plain_crop` found a region in every one of them and
# the four-border refusal never fired, so a test asserting the refusal would
# have been asserting nothing. The scene below was arrived at by measuring —
# what a tight crop of a REAL product actually looks like is a lit surface with
# printing on it and no plain background anywhere, because the background was
# cropped away. `fixtures_ponds_held.png` cropped inside the jar does the same
# thing, and is used as the second witness precisely because it is not
# synthetic.


def _lit(h: int, w: int, dark, pale, seed: int = 2, amp: int = 6) -> np.ndarray:
    """A surface under a light that comes from one side, plus sensor noise.

    The gradient is the load-bearing part. A flat swatch is refused for low
    contrast, which is a different refusal and would not exercise this at all.
    """
    yy, xx = np.mgrid[0:h, 0:w]
    t = ((xx / w) * 0.6 + (yy / h) * 0.4)[..., None]
    img = np.asarray(dark) * (1 - t) + np.asarray(pale) * t
    rng = np.random.default_rng(seed)
    return np.clip(img + rng.integers(-amp, amp + 1, (h, w, 3)), 0, 255).astype(np.uint8)


def tight_carton() -> np.ndarray:
    """A tall medicine carton, filling the picture edge to edge.

    This IS the operator's crop: they drew the box on the carton, so the carton
    is the whole image. Measured: `plain_crop` refuses it with
    `matless_region_touches_every_border`, which is the reported bug.
    """
    img = _lit(600, 420, (90, 120, 170), (230, 235, 240))
    cv2.putText(img, "500 mg", (50, 330), cv2.FONT_HERSHEY_SIMPLEX,
                1.6, (40, 40, 40), 4)
    return img


def carton_on_a_counter() -> np.ndarray:
    """The same carton with a margin of plain surface all round it — the photo
    the uncropped path is built for, and the one it must keep segmenting."""
    c = tight_carton()
    h, w = c.shape[:2]
    rng = np.random.default_rng(3)
    scene = np.clip(np.full((h + 260, w + 260, 3), 148, np.int16)
                    + rng.integers(-7, 8, (h + 260, w + 260, 3)), 0, 255).astype(np.uint8)
    scene[130:130 + h, 130:130 + w] = c
    return scene


#: A REAL photograph, cropped tight inside the product the way an operator
#: draws a box on it. `fixtures_ponds_held.png` is a PONDS jar held up to the
#: camera; JAR_BOX is where the jar is in it, read off the image, and 70% of
#: that box is entirely jar.
HELD = Path(__file__).with_name("fixtures_ponds_held.png")
JAR_BOX = (140, 60, 168, 200)
held = pytest.mark.skipif(not HELD.is_file(), reason="fixture missing")


def held_jar_tight(frac: float = 0.7) -> np.ndarray:
    img = cv2.imread(str(HELD))
    x, y, w, h = JAR_BOX
    cx, cy = x + w / 2, y + h / 2
    ww, hh = w * frac, h * frac
    sub = img[int(cy - hh / 2):int(cy + hh / 2), int(cx - ww / 2):int(cx + ww / 2)]
    return cv2.resize(sub, (sub.shape[1] * 3, sub.shape[0] * 3),
                      interpolation=cv2.INTER_CUBIC)


def _png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A catalogue that lives and dies with the test, redirected three ways."""
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    was = upload_app._DEPS.get("store_dir")
    upload_app.set_store_dir(tmp_path / "shop")
    try:
        yield TestClient(app)
    finally:
        upload_app._DEPS["store_dir"] = was
        upload_app._DEPS["store"] = None


# ------------------------------------------------------- the reported failure --

def test_the_uncropped_path_refuses_a_tight_box_and_that_is_the_bug() -> None:
    """The complaint, reproduced at the level it actually happens.

    Kept as a test, not deleted: it is the reason the second path exists, and
    if `plain_crop` ever stopped refusing this the whole change would be moot
    and nobody would know.
    """
    with pytest.raises(UploadRefused) as exc:
        plain_crop(tight_carton())
    assert exc.value.reason == R_MATLESS_CROPPED


def test_a_hand_drawn_box_is_taken_as_the_region() -> None:
    img = tight_carton()
    crop, ev = hand_drawn_crop(img)
    assert crop.shape == img.shape
    # Byte for byte the operator's rectangle: not re-cropped, not re-oriented,
    # and not background-suppressed.
    assert np.array_equal(crop, img)
    assert ev["region_source"] == "operator_rectangle"
    assert ev["background"] == "kept"
    assert ev["region_px"] == [0, 0, img.shape[1], img.shape[0]]


def test_the_evidence_admits_the_border_gate_was_skipped() -> None:
    """Honesty, not silence. The stored SKU can be traced to which path made
    it, and the record says all four borders ARE touched and why that was
    allowed to stand."""
    _crop, ev = hand_drawn_crop(tight_carton())
    assert ev["touches_borders"] == ["top", "bottom", "left", "right"]
    assert "not applied" in ev["border_gate"]
    assert "person drew this region" in ev["border_gate"]


# ------------------------------------------- what is still refused, by name --

def test_a_box_drawn_over_a_blank_wall_is_still_refused() -> None:
    """Drawing a rectangle says WHERE the item is. It cannot put one there."""
    rng = np.random.default_rng(1)
    wall = np.clip(np.full((400, 300, 3), 188, np.int16)
                   + rng.integers(-3, 4, (400, 300, 3)), 0, 255).astype(np.uint8)
    with pytest.raises(UploadRefused) as exc:
        hand_drawn_crop(wall)
    assert exc.value.reason == R_MATLESS_FLAT


def test_a_box_of_one_flat_colour_is_still_refused() -> None:
    with pytest.raises(UploadRefused) as exc:
        hand_drawn_crop(np.full((400, 300, 3), 200, np.uint8))
    assert exc.value.reason == R_MATLESS_FLAT


def test_a_box_too_small_to_describe_is_still_refused() -> None:
    tiny = cv2.resize(tight_carton(), (40, 58), interpolation=cv2.INTER_AREA)
    with pytest.raises(UploadRefused) as exc:
        hand_drawn_crop(tiny)
    assert exc.value.reason == R_MATLESS_TINY
    assert "64 px" in exc.value.detail


def test_a_greyscale_or_broken_buffer_is_still_refused() -> None:
    with pytest.raises(UploadRefused) as exc:
        hand_drawn_crop(np.zeros((80, 80), np.uint8))
    assert exc.value.reason == R_MATLESS_FLAT


# ------------------------------------ the gate that must NOT have been moved --

@held
def test_a_real_photograph_cropped_tight_is_refused_the_same_way() -> None:
    """The second witness, and not a synthetic one.

    A PONDS jar photographed on a counter, then cropped the way an operator
    draws a box on it: inside the jar, so the jar is the whole picture. The
    uncropped path refuses it; the hand-drawn path takes it.
    """
    jar = held_jar_tight()
    with pytest.raises(UploadRefused) as exc:
        plain_crop(jar)
    assert exc.value.reason == R_MATLESS_CROPPED
    crop, ev = hand_drawn_crop(jar)
    assert crop.shape == jar.shape
    assert ev["region_source"] == "operator_rectangle"


@held
def test_the_border_refusal_is_untouched_on_the_uncropped_path() -> None:
    """The load-bearing half, stated as the thing that must NOT change.

    The same real photograph UNCROPPED still goes through the segmenter and
    still gets a bounded region out of it. The refusal was never widened, it
    was routed around for one named caller.
    """
    whole = cv2.imread(str(HELD))
    _crop, ev = plain_crop(whole)
    assert ev["region_source"].startswith("proposal:")
    assert len(ev["touches_borders"]) < 4
    assert ev["background"] == "suppressed"


def test_a_photo_with_a_margin_still_goes_through_the_segmenter() -> None:
    """The good path is unchanged: a carton on a counter is still FOUND, not
    taken whole, and the crop is smaller than the picture."""
    scene = carton_on_a_counter()
    crop, ev = plain_crop(scene)
    assert ev["region_source"].startswith("proposal:")
    assert crop.shape[0] < scene.shape[0]


def test_the_flag_has_to_be_asked_for() -> None:
    """A page that forgets the field gets the careful path, never the trusting
    one. Silence is not consent here."""
    def form(**kw):
        return {"_kind": "json", **kw}
    assert read_region(form()) == ""
    assert read_region(form(region="")) == ""
    assert read_region(form(region="whatever")) == ""
    assert read_region(form(region="user_drawn")) == REGION_USER_DRAWN
    assert read_region(form(region="  USER_DRAWN ")) == REGION_USER_DRAWN


# ------------------------------------------------------------- end to end --

def _teach(client: TestClient, img: np.ndarray, sku: str, **extra) -> dict:
    data = {"sku_id": sku, "name": "Paracetamol 500mg strip",
            "price_rupees": "42", "mode": "plain_photo"}
    data.update(extra)
    return client.post("/enrol",
                       files={"image": ("teach.png", _png(img), "image/png")},
                       data=data).json()


def test_enrol_refuses_the_tight_box_without_the_flag(client: TestClient) -> None:
    """The shipped behaviour the operator hit, over real multipart."""
    r = _teach(client, tight_carton(), "medicine_carton")
    assert r["ok"] is False
    assert r["reason"] == R_MATLESS_CROPPED


def test_enrol_teaches_the_tight_box_when_a_human_drew_it(client: TestClient) -> None:
    r = _teach(client, tight_carton(), "medicine_carton", region="user_drawn")
    assert r["ok"] is True, r
    assert r["stored"]["sku_id"] == "medicine_carton"
    # Integer paise, never a float, on every path.
    assert r["stored"]["price_paise"] == 4200
    assert isinstance(r["stored"]["price_paise"], int)
    # The audit record names which path taught it.
    assert r["measured"]["region_source"] == "operator_rectangle"
    assert r["measured"]["touches_borders"] == ["top", "bottom", "left", "right"]
    assert r["stored"]["footprint_mm"] is None


def test_enrol_still_refuses_a_hand_drawn_box_of_nothing(client: TestClient) -> None:
    """The flag is provenance, not permission. A rectangle round a blank wall
    is refused with the same words whoever drew it."""
    wall = np.full((400, 300, 3), 200, np.uint8)
    r = _teach(client, wall, "nothing_at_all", region="user_drawn")
    assert r["ok"] is False
    assert r["reason"] == R_MATLESS_FLAT


def test_a_product_taught_from_a_hand_drawn_box_is_recognised(client: TestClient) -> None:
    """The round trip. A reference is worth nothing if the till cannot match it.

    The photo shown to the till is the ordinary uncropped one — carton on a
    counter, segmented by the server — so this also checks the two paths
    produce descriptors that can be compared with each other.
    """
    taught = _teach(client, tight_carton(), "medicine_carton", region="user_drawn")
    assert taught["ok"] is True, taught

    seen = client.post("/recognise",
                       files={"image": ("till.png",
                                        _png(carton_on_a_counter()), "image/png")},
                       data={"mode": "plain_photo"}).json()
    assert seen["ok"] is True, seen
    rows = seen.get("items") or []
    assert rows, seen
    assert rows[0]["sku_id"] == "medicine_carton", rows[0]
    assert rows[0]["price_paise"] == 4200
