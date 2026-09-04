"""Where are the things on this counter?

Every number asserted here was measured before it was written down, and the
ones that record a LIMIT are as important as the ones that record a success:
this module's job is to be honest about what it cannot separate, because a
region it fails to propose is a line that is silently missing from a bill.
"""

from __future__ import annotations

import glob
import os

import cv2
import numpy as np
import pytest

from gawaah.detector import (
    MAX_ASPECT,
    MAX_PROPOSALS,
    MIN_AREA_FRAC,
    _NOT_A_FACING,
    Proposal,
    describe,
    detect,
    merge,
    propose_contours,
    propose_yolo,
    reset_model_cache,
    yolo_available,
    yolo_rejections,
    yolo_status,
)

#: THE INSTRUMENT CAME UNPLUGGED, AND THAT LOOKED LIKE A PASSING SUITE.
#:
#: These photos used to be read only out of `results/shop/photos`, which is the
#: LIVE shop's directory — so resetting the shop (which is a normal thing to do)
#: emptied it, `PHOTOS` became `[]`, and the module-level skipif below silently
#: turned all 21 detector tests into skips. Measured: 4042 passed, 26 skipped,
#: of which 21 were this file. The detector's entire regression net was off, and
#: nothing said so louder than a dot.
#:
#: The three products the module docstring quotes measurements for are also
#: committed under `tests/fixtures_embed/ref_*.png`, which is a TEST fixture and
#: does not move when the shop is reset. Prefer the live shop's photos when they
#: are there (they are the real thing, and more of them), fall back to the
#: committed ones, and only skip when genuinely neither exists.
_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures_embed")
PHOTOS = sorted(glob.glob(
    os.path.join(os.path.dirname(__file__), "..", "results", "shop", "photos", "*.png")))
if not PHOTOS:
    PHOTOS = [p for p in (os.path.join(_FIXTURES, f"ref_{n}.png") for n in
                          ("lifebuoy_soap", "parle_g_biscuit", "shampoo_sachet"))
              if os.path.isfile(p)]


# ------------------------------------------------------------------ helpers --

def counter(gap: int = 40, n: int | None = None, width: int = 200,
            seed: int = 11) -> tuple[np.ndarray, list[tuple[int, int, int, int]]]:
    """A synthetic counter: real taught product photos on a wood-ish surface."""
    rng = np.random.default_rng(seed)
    h, w = 720, 1280
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = (120, 140, 160)
    img = np.clip(img.astype(np.int16) + rng.integers(-8, 8, (h, w, 3)),
                  0, 255).astype(np.uint8)
    truth: list[tuple[int, int, int, int]] = []
    x = 120
    for f in (PHOTOS if n is None else PHOTOS[:n]):
        p = cv2.imread(f)
        p = cv2.resize(p, (width, int(width * p.shape[0] / p.shape[1])))
        img[250:250 + p.shape[0], x:x + width] = p
        truth.append((x, 250, width, p.shape[0]))
        x += width + gap
    return img, truth


def iou(a, b) -> float:
    ix = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    i = ix * iy
    return i / float(a[2] * a[3] + b[2] * b[3] - i) if i > 0 else 0.0


def found(truth, props, bar: float = 0.5) -> int:
    return sum(1 for t in truth if any(iou(t, p.box) > bar for p in props))


def empty_counter() -> np.ndarray:
    return counter(n=0)[0]


def printed_carton(w: int, h: int, seed: int = 1) -> np.ndarray:
    """A white medicine carton: brand lockup, colour band, paragraph of print.

    The three high-contrast islands are the point. On a pale ground they are
    the ONLY parts of the carton that clear a global threshold, and each one
    used to come back as its own object.
    """
    rng = np.random.default_rng(seed + 7)
    c = np.full((h, w, 3), 0, np.uint8)
    c[:] = (244, 246, 247)
    y0 = int(h * 0.10)
    cv2.rectangle(c, (int(w * .12), y0), (int(w * .88), y0 + int(h * .075)),
                  (60, 52, 48), -1)                       # brand lockup
    cv2.rectangle(c, (int(w * .22), y0 + int(h * .10)),
                  (int(w * .78), y0 + int(h * .15)), (70, 60, 55), -1)
    b0 = int(h * 0.40)
    cv2.rectangle(c, (0, b0), (w, b0 + int(h * .13)), (168, 92, 24), -1)  # band
    yy = b0 + int(h * 0.20)
    while yy < h - int(h * .06):                          # small print
        ln = int(w * rng.uniform(0.45, 0.84))
        cv2.rectangle(c, (int(w * .08), yy), (int(w * .08) + ln,
                      yy + max(2, int(h * .014))), (74, 68, 64), -1)
        yy += max(5, int(h * .032))
    cv2.rectangle(c, (0, 0), (w - 1, h - 1), (206, 208, 210), 2)
    return np.clip(c.astype(np.int16) + rng.integers(-4, 5, c.shape),
                   0, 255).astype(np.uint8)


def pale_room(seed: int = 11) -> np.ndarray:
    """A daylit wall — the largest near-uniform surface, and BRIGHT.

    That is the condition the whole bug needs: the modal background is a pale
    wall, so a white carton in front of it is nearly background-coloured.
    """
    rng = np.random.default_rng(seed)
    img = np.zeros((720, 1280, 3), np.uint8)
    img[:] = (214, 217, 218)
    for y in range(720):
        img[y, :] = np.clip(img[y, :].astype(np.int16) + (10 - y * 16 // 720), 0, 255)
    return np.clip(img.astype(np.int16) + rng.integers(-7, 8, img.shape),
                   0, 255).astype(np.uint8)


def cartons_on_a_pale_wall() -> tuple[np.ndarray, list[tuple[int, int, int, int]]]:
    """Two identical white cartons, no person: the fragmentation case alone."""
    img = pale_room()
    truth = []
    for (x, y, w, h) in ((836, 134, 244, 436), (400, 180, 244, 436)):
        img[y:y + h, x:x + w] = cv2.resize(printed_carton(244, 436),
                                           (w, h), interpolation=cv2.INTER_AREA)
        truth.append((x, y, w, h))
    return img, truth


pytestmark = pytest.mark.skipif(not PHOTOS, reason="no taught product photos on disk")


# ------------------------------------------------------- what it does find --

def test_three_products_on_one_counter_are_three_regions():
    """The whole reason this module exists.

    `/recognise?mode=plain_photo` names ONE item, because a photo with no mat
    has one subject by construction. A customer puts four things down at once.
    """
    img, truth = counter(gap=40)
    props = detect(img)
    assert found(truth, props) == len(truth)


def test_the_boxes_are_tight_enough_to_crop_and_embed():
    """A loose box embeds the counter as well as the packet.

    Measured IoU 0.88-0.93 against the placed rectangles. The bar here is 0.7
    so a modest regression is caught before it reaches the recogniser, where it
    would show up as a mysterious drop in cosine rather than as a bad crop.
    """
    img, truth = counter(gap=40)
    props = detect(img)
    for t in truth:
        best = max((iou(t, p.box) for p in props), default=0.0)
        assert best > 0.70, f"box for {t} was only IoU {best:.2f}"


def test_it_finds_them_with_the_model_absent():
    """YOLO is OPTIONAL. The contour proposer is the workhorse, not the fallback."""
    img, truth = counter(gap=40)
    assert found(truth, propose_contours(img)) == len(truth)


def test_an_empty_counter_proposes_nothing():
    """No object, no region. A proposer that always finds something is noise."""
    assert detect(empty_counter()) == []


def test_one_product_alone_is_one_region():
    img, truth = counter(gap=40, n=1)
    props = detect(img)
    assert found(truth, props) == 1
    assert len(props) == 1


# --------------------------------------------------------- the stated limit --

def test_products_twenty_pixels_apart_are_still_separate():
    """The measured floor, pinned so it cannot regress silently.

    Swept kernel 3/5/7/9 against gap: 20 px separates, 15 px does not. The
    binding constraint is not the morphological close but the DILATED CANNY
    EDGES of two objects meeting — dilating twice instead of once took this
    case from 3 of 3 to 0 of 3.
    """
    img, truth = counter(gap=20)
    assert found(truth, detect(img)) == len(truth)


def test_touching_products_are_read_as_one_and_that_is_a_stated_limit():
    """AN HONEST FAILURE, ASSERTED AS SUCH.

    Below about 20 px of separation the two objects' masks genuinely fuse, and
    nothing in the mask can then separate them — there is no neck for the
    distance transform to cut. This is a limit of the method, not a setting.

    The test exists so the limit is DOCUMENTED AND MEASURED rather than
    discovered by a shopkeeper whose bill was short. If someone improves the
    splitter, this test failing is the good news, and its message says so.
    """
    img, truth = counter(gap=5)
    n = found(truth, detect(img))
    assert n < len(truth), (
        "touching packets separated — if this is a real improvement, update "
        "the limit stated on the till and in the README rather than deleting "
        "this test")


# ------------------------------------------------------------------ merging --

def test_one_box_around_several_products_never_suppresses_them():
    """THE BUG THIS RULE EXISTS FOR.

    NMS ranked by area descending, which is right WITHIN one object (the whole
    packet beats a corner of its label) and exactly wrong ACROSS objects: one
    sloppy box drawn around all three products suppressed the three precise
    boxes underneath it and the frame went from 3 items to 0.
    """
    tight = [Proposal(100, 100, 100, 100, "contour", 0.9),
             Proposal(300, 100, 100, 100, "contour", 0.9),
             Proposal(500, 100, 100, 100, "contour", 0.9)]
    swallower = Proposal(90, 90, 520, 120, "yolo", 0.5)
    kept = merge(tight + [swallower])
    assert swallower not in kept
    assert all(t in kept for t in tight)


def test_the_larger_box_still_wins_within_one_object():
    """Ranking is only disabled across objects, not inside one."""
    whole = Proposal(100, 100, 200, 200, "contour", 0.9)
    fragment = Proposal(140, 140, 120, 120, "yolo", 0.9)   # a bit of the label
    kept = merge([whole, fragment])
    assert kept == [whole]


def test_a_frame_where_everything_swallows_something_still_returns_boxes():
    """Photographed close enough that one object fills the frame.

    Returning nothing here would be worse than returning a loose box: the
    caller has its own gate and can refuse, but it cannot refuse what it was
    never given.
    """
    a = Proposal(0, 0, 100, 100, "contour", 0.9)
    b = Proposal(10, 10, 20, 20, "contour", 0.9)
    c = Proposal(60, 60, 20, 20, "contour", 0.9)
    assert merge([a, b, c])


def test_never_more_than_the_stated_maximum():
    props = [Proposal(i * 40, 0, 30, 30, "contour", 0.5) for i in range(40)]
    assert len(merge(props)) <= MAX_PROPOSALS


# ------------------------------------------------------------------- filters --

def test_a_speck_is_not_a_packet():
    img = empty_counter()
    cv2.rectangle(img, (600, 300), (606, 306), (10, 10, 220), -1)
    assert detect(img) == []


def test_a_region_filling_the_whole_frame_is_the_counter_not_a_product():
    img = empty_counter()
    cv2.rectangle(img, (0, 0), (1279, 719), (10, 200, 10), -1)
    assert all((p.w * p.h) / (1280 * 720) <= 0.45 for p in detect(img))


def test_a_long_thin_streak_is_a_seam_not_a_product():
    img = empty_counter()
    cv2.rectangle(img, (100, 350), (1180, 372), (10, 10, 220), -1)   # aspect ~49
    assert all(max(p.w, p.h) / max(1, min(p.w, p.h)) <= MAX_ASPECT for p in detect(img))


# -------------------------------------------------------------- the contract --

def test_a_proposal_is_never_a_claim_about_a_product():
    """This module may not name anything. The gate that does lives elsewhere."""
    d = describe()
    assert d["identifies_products"] is False
    assert d["settles_money"] is False
    assert set(Proposal.__dataclass_fields__) == {"x", "y", "w", "h", "source", "score"}


def test_the_yolo_readout_says_what_it_is_actually_used_for():
    """Anyone reading "YOLO" assumes it is naming products. It is not.

    Measured on this shop's own three photographs, a COCO YOLOv5n scores its
    best class as "person", "person" and "cell phone". The class head is never
    read; only objectness is, and only as a region proposal.
    """
    s = yolo_status()
    assert "class-agnostic" in s["used_for"]
    assert "ignored" in s["used_for"]


def test_a_missing_model_is_a_condition_and_never_an_error(monkeypatch, tmp_path):
    """The counter must work on a checkout that never downloaded the weights."""
    import gawaah.detector as det
    monkeypatch.setattr(det, "MODEL_PATH", tmp_path / "not-here.onnx")
    reset_model_cache()
    try:
        assert yolo_available() is False
        assert "no model file" in yolo_status()["why_not"]
        assert propose_yolo(empty_counter()) == []
        img, truth = counter(gap=40)
        assert found(truth, detect(img)) == len(truth)   # still works
    finally:
        reset_model_cache()


def test_a_corrupt_model_file_is_a_condition_and_never_an_error(monkeypatch, tmp_path):
    import gawaah.detector as det
    bad = tmp_path / "broken.onnx"
    bad.write_bytes(b"this is not an onnx graph")
    monkeypatch.setattr(det, "MODEL_PATH", bad)
    reset_model_cache()
    try:
        assert yolo_available() is False
        assert yolo_status()["present"] is True     # it IS there; it is unusable
        assert yolo_status()["why_not"]
        assert propose_yolo(empty_counter()) == []
    finally:
        reset_model_cache()


def test_boxes_are_always_inside_the_frame():
    """A crop outside the frame raises somewhere far away from here."""
    img, _ = counter(gap=40)
    h, w = img.shape[:2]
    for p in detect(img):
        assert 0 <= p.x and 0 <= p.y
        assert p.x + p.w <= w and p.y + p.h <= h
        assert p.crop(img).size > 0


def test_a_tiny_frame_does_not_raise():
    for side in (1, 4, 16, 31):
        img = np.zeros((side, side, 3), np.uint8)
        assert detect(img) == [] or all(isinstance(p, Proposal) for p in detect(img))


def test_min_area_is_a_fraction_so_it_survives_a_resolution_change():
    """A pixel count would silently change meaning on a different camera."""
    assert 0.0 < MIN_AREA_FRAC < 0.05


# ------------------------------- printing on a packet is not several packets --

def test_a_printed_carton_on_a_pale_wall_is_one_region_not_three():
    """THE BUG. Reported as 12 regions for 2 cartons on the Shelf screen.

    A white medicine carton photographed against a lit wall came back as three
    objects — the brand lockup, the saturated colour band, and the paragraph of
    small print — because those are the only parts of it that clear a global
    threshold when the carton's own body is the same colour as the wall.

    MEASURED CAUSE, and it is not the merge logic. The modal background is the
    wall; the carton's body sits 15 from it on the normalised distance scale;
    Otsu, split by the strongest contrast in the frame, puts the cut at 98. So
    the carton's body is background and only its ink survives — three islands,
    and NO PARENT BOX EVER PROPOSED. There was nothing for NMS to suppress.
    """
    img, truth = cartons_on_a_pale_wall()
    props = detect(img)
    assert found(truth, props) == len(truth), (
        f"expected both cartons, got boxes {[p.box for p in props]}")
    # and each one ONCE — three fragments would show up as extra regions
    for t in truth:
        hits = [p for p in props if iou(t, p.box) > 0.35]
        assert len(hits) == 1, f"carton {t} came back as {len(hits)} regions"


def test_the_fragments_are_grouped_only_when_product_lies_between_them():
    """The rule that makes the grouping safe, asserted from the other side.

    Two packets with COUNTER between them must never be grouped, at any gap,
    because the counter is the background colour by construction and so the
    material between them measures 1.0x the background level. Measured: 1.01
    at 40 px and 1.05-1.06 at 20 px, against 1.37-1.44 between fragments of one
    carton. If this ever fails, two products have become one line on a bill.
    """
    for gap in (20, 40, 60):
        img, truth = counter(gap=gap)
        assert found(truth, detect(img)) == len(truth), f"lost a product at {gap}px"


def test_grouping_never_reaches_across_the_frame():
    """A chain of "not background" must not walk from one object to another.

    Two cartons far apart with wall between them stay two, which is the same
    guard that stops the person and the wardrobe becoming one region.
    """
    img, truth = cartons_on_a_pale_wall()
    props = detect(img)
    for p in props:
        assert not (iou(truth[0], p.box) > 0.3 and iou(truth[1], p.box) > 0.3), (
            "one box swallowed both cartons")


# -------------------------------------------------- YOLO as a REJECTOR only --

def test_no_box_shaped_class_may_ever_veto_a_region():
    """THE REGRESSION THIS LIST COST A PRODUCT TO LEARN.

    The rejector's first list included the appliances, reasoning that a shop
    does not sell a fridge. Measured on the bench frame, a white medicine
    carton scored `laptop 0.423` with IoU 0.84 against the real packet — so
    that entry DELETES a product the shopkeeper is holding up. A printed carton
    is a white box, so every white-box class is one this model will sometimes
    give it, and none of them may vote.
    """
    banned = {"tv", "laptop", "microwave", "oven", "toaster", "refrigerator",
              "sink", "book", "cell phone", "remote", "keyboard", "mouse",
              "bottle", "cup", "bowl", "vase", "clock", "toothbrush",
              "suitcase", "handbag", "backpack", "teddy bear"}
    assert not (set(_NOT_A_FACING.values()) & banned), (
        "a class a packet can be mistaken for is allowed to veto one")


def test_a_thing_a_kirana_actually_sells_is_never_on_the_reject_list():
    """A bottle and a cup are COCO classes AND stock. They may not be vetoed."""
    assert 39 not in _NOT_A_FACING and 41 not in _NOT_A_FACING   # bottle, cup


def test_the_rejector_is_silent_without_the_model(monkeypatch, tmp_path):
    """Optional means optional: no weights, no vetoes, no behaviour change."""
    import gawaah.detector as det
    monkeypatch.setattr(det, "MODEL_PATH", tmp_path / "not-here.onnx")
    reset_model_cache()
    try:
        img, truth = cartons_on_a_pale_wall()
        assert yolo_rejections(img) == []
        assert found(truth, detect(img)) == len(truth)   # still finds them
    finally:
        reset_model_cache()


def test_a_veto_never_costs_a_real_product_a_region():
    """The veto may only DELETE, so it is the one place a line can go missing.

    On a counter of three taught products the rejector must take nothing: every
    box that survives without it must survive with it. Absence of a recognised
    class is never a reason to reject — only a confident, named class is.
    """
    img, truth = counter(gap=40)
    with_veto = detect(img)
    without = merge(list(propose_contours(img)) + list(propose_yolo(img)))
    assert {p.box for p in with_veto} == {p.box for p in without}, (
        "the rejector removed a region on a plain counter of taught products")
    assert found(truth, with_veto) == len(truth)


def test_a_veto_needs_more_confidence_than_a_proposal_does():
    """A proposal errs low because a missing box costs a line off the bill; a
    veto deletes a region and can cost the same line, so it errs high."""
    import gawaah.detector as det
    assert det.YOLO_REJECT_CONF > det.YOLO_OBJECTNESS_FLOOR
    assert all(r.score >= det.YOLO_REJECT_CONF
               for r in yolo_rejections(counter(gap=40)[0]))
