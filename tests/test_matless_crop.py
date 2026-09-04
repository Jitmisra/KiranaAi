"""Which region of an ordinary photograph is the product?

THE DEFECT THESE PIN. A real camera frame — a PONDS jar held up in front of the
operator's face, pale wall behind, a wooden cupboard and a dark shirt at the
frame edge — produced a stored reference that was **61.6 % pure black**. The
old rule took the LARGEST connected component of "far from the border-median
colour", and on that frame the largest component is hair + face + hand + the
jar's rim fused into one blob. The jar's own pale label, the only part of the
picture that identifies the product, sat within 51 Lab units of the border
median, was scored as background, and was cut out.

Recognition by sight could never match that reference, and nothing said why.
The catalogue simply held a black silhouette of a face where a product should
have been.

`tests/fixtures_ponds_held.png` is that frame, at half size. It is committed
deliberately: this class of bug is invisible to every synthetic scene anyone
thought to write, and was only found because a real photograph was kept.
"""

from __future__ import annotations

import os
import sys
import time

import cv2
import numpy as np
import pytest

# `from tools import upload_app`, EXACTLY AS THE REST OF THE SUITE DOES.
#
# Importing it as a bare `upload_app` puts a SECOND, INDEPENDENT COPY in
# sys.modules: two `_DEPS` caches, two FastAPI apps, and a monkeypatch applied
# in another test file lands on an object the running app has never heard of.
# Measured: it turned 13 passing storefront tests red, and not one of them had
# anything to do with cropping.
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from tools import upload_app  # noqa: E402
from tools.upload_app import UploadRefused, plain_crop  # noqa: E402

HELD = os.path.join(os.path.dirname(__file__), "fixtures_ponds_held.png")
PHOTOS = os.path.join(os.path.dirname(__file__), "..", "results", "shop", "photos")
#: THE REGRESSION SET LIVED IN THE LIVE SHOP'S DIRECTORY, so resetting the shop
#: silently skipped it — the same way it silently skipped all 21 of
#: tests/test_detector.py. The three products below are also committed under
#: tests/fixtures_embed/, which no shop reset can empty. Fall back to those.
_FALLBACK = os.path.join(os.path.dirname(__file__), "fixtures_embed")


def _photo(name: str) -> str:
    """The taught photo for a product, from the shop or from the fixtures."""
    live = os.path.join(PHOTOS, f"{name}.png")
    return live if os.path.isfile(live) else os.path.join(_FALLBACK, f"ref_{name}.png")

#: Where the jar actually is in the half-size fixture, read off the image.
JAR_BOX = (140, 60, 168, 200)


def _iou(a, b) -> float:
    ix = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    i = ix * iy
    return i / float(a[2] * a[3] + b[2] * b[3] - i) if i > 0 else 0.0


def _black_frac(img: np.ndarray) -> float:
    return float((img.reshape(-1, img.shape[-1]).sum(axis=1) == 0).mean())


def _on_a_surface(path: str, pad: float = 1.1) -> tuple[np.ndarray, tuple]:
    """A tight product photo, placed on a plain surface the way a camera sees it."""
    img = cv2.imread(path)
    h, w = img.shape[:2]
    H, W = int(h * pad * 2), int(w * pad * 2)
    rng = np.random.default_rng(5)
    bg = np.zeros((H, W, 3), np.uint8)
    bg[:] = (120, 140, 160)
    bg = np.clip(bg.astype(np.int16) + rng.integers(-8, 8, (H, W, 3)), 0, 255).astype(np.uint8)
    y, x = (H - h) // 2, (W - w) // 2
    bg[y:y + h, x:x + w] = img
    return bg, (x, y, w, h)


held = pytest.mark.skipif(not os.path.isfile(HELD), reason="fixture missing")


# ------------------------------------------------- the failure it was built for

@held
def test_a_product_held_in_front_of_a_face_crops_to_the_product() -> None:
    """The whole point. Before this fix the crop was the FACE."""
    img = cv2.imread(HELD)
    crop, ev = plain_crop(img)
    box = tuple(ev["region_px"])
    assert _iou(JAR_BOX, box) > 0.55, (
        f"the chosen region {box} is not the jar {JAR_BOX} — "
        f"IoU {_iou(JAR_BOX, box):.3f}")


@held
def test_the_stored_reference_is_a_picture_and_not_a_silhouette() -> None:
    """61.6 % of the old crop was pure black, and the black was where the
    label had been. A descriptor built from that describes a hole."""
    crop, _ev = plain_crop(cv2.imread(HELD))
    assert _black_frac(crop) < 0.15, (
        f"{_black_frac(crop):.1%} of this crop is pure black; the defect this "
        f"replaces scored 61.6 %")


@held
def test_the_choice_is_stable_across_runs() -> None:
    """grabCut has a little internal nondeterminism. The chosen REGION must not
    move because of it — a reference that depends on which run taught it is a
    reference nobody can reason about."""
    img = cv2.imread(HELD)
    boxes = {tuple(plain_crop(img)[1]["region_px"]) for _ in range(3)}
    assert len(boxes) == 1, f"the region moved between runs: {boxes}"


@held
def test_it_keeps_up_with_the_scan_loop() -> None:
    """This runs on the by-look path, which polls every 240 ms."""
    img = cv2.imread(HELD)
    plain_crop(img)                                   # warm the model cache
    t0 = time.perf_counter()
    plain_crop(img)
    ms = (time.perf_counter() - t0) * 1000
    assert ms < 240.0, f"{ms:.0f} ms against a 240 ms poll"


# ---------------------------------------------- the products that already work

@pytest.mark.parametrize("name", ["lifebuoy_soap", "parle_g_biscuit", "shampoo_sachet"])
def test_a_product_on_a_plain_surface_is_still_found_tightly(name: str) -> None:
    """THE REGRESSION SET. A fix that rescues the hard case by wrecking the
    three products this shop already taught successfully is not a fix. These
    scored 0.984 / 0.988 / 0.991 before the change and must not fall."""
    path = _photo(name)
    if not os.path.isfile(path):
        pytest.skip("taught photo not on disk")
    scene, truth = _on_a_surface(path)
    crop, ev = plain_crop(scene)
    assert _iou(truth, tuple(ev["region_px"])) > 0.90


# ------------------------------------------------------- it must still refuse

@pytest.mark.parametrize("what,img", [
    ("a blank wall", np.full((480, 640, 3), 200, np.uint8)),
    ("pure black", np.zeros((480, 640, 3), np.uint8)),
    ("a 12 px image", np.full((12, 12, 3), 128, np.uint8)),
])
def test_it_refuses_what_it_cannot_honestly_crop(what: str, img: np.ndarray) -> None:
    """A segmenter that always returns something invents products. Every one of
    these refused before the change and must still refuse."""
    with pytest.raises(UploadRefused):
        plain_crop(img)


def test_a_refusal_still_carries_a_name_a_person_can_act_on() -> None:
    try:
        plain_crop(np.full((480, 640, 3), 200, np.uint8))
    except UploadRefused as exc:
        assert exc.args[0]
        assert len(str(exc)) > 40, "a refusal has to say what would help"
    else:
        pytest.fail("a blank wall was accepted")


# ------------------------------------------------------------- the contract --

@held
def test_the_evidence_still_says_where_it_looked() -> None:
    """Every key the response and the tests already depended on survives."""
    _crop, ev = plain_crop(cv2.imread(HELD))
    for k in ("frame_px", "frame_contrast_range", "region_px"):
        assert k in ev, f"evidence lost {k!r}"
    x, y, w, h = ev["region_px"]
    assert w > 0 and h > 0 and x >= 0 and y >= 0


@held
def test_the_crop_lies_inside_the_frame_it_came_from() -> None:
    img = cv2.imread(HELD)
    crop, _ev = plain_crop(img)
    assert crop.size > 0
    assert crop.shape[0] <= img.shape[0] and crop.shape[1] <= img.shape[1]
