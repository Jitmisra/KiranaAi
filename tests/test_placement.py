"""S3a acceptance: the classical placement detector, measured in millimetres.

The harness pastes rectangles of KNOWN mm size onto a rectified mat and asks
what the detector measures back. Two things about it are deliberate:

  * Objects are composited with SUPERSAMPLED COVERAGE (4x, INTER_AREA), not a
    hard fillConvexPoly. A hard rasteriser is generous by up to a pixel and
    quantises the truth to the pixel grid, so it would put a ~0.4 mm floor
    under every error we report and we would be measuring the harness. Coverage
    compositing is also what a real sensor does — a pixel is an area integral.

  * Objects are pasted both DARKER and BRIGHTER than the paper. A single global
    threshold measures those two cases differently; catching that was what
    forced the per-blob 50 %-amplitude refit in placement.py.

Every number in the docstrings below was produced by running this file.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from gawaah.clock import VirtualClock
from gawaah.placement import (
    BORDER_PX, CLOSE_PX, MERGED_MIN_FILL, MIN_AREA_MM2, OPEN_PX,
    REASON_BORDER, REASON_MERGED, REASON_OK, STABLE_FRAMES,
    PlacementDetector, PlacementError, px_to_mm,
)
from gawaah.takhti import (
    BUF_H, BUF_W, MAT_H_MM, MAT_W_MM, PX_PER_MM_X, PX_PER_MM_Y,
    PlaneEngine, mm_to_buffer, render_takhti,
)
from tests.test_plane import synth_frame

SS = 4                 # supersample factor for coverage compositing
PAPER = 200            # what white A3 reads at under the demo's exposure
DARK = 55              # a dark object
BRIGHT = 245           # a pale object, only 45 grey levels off the paper
CENTRE = (MAT_W_MM / 2, MAT_H_MM / 2)


# ------------------------------------------------------------------ harness

def empty_mat() -> np.ndarray:
    """An ideal nadir rectified buffer: the printed mat at 840x1188, exposed so
    the paper sits at PAPER rather than a saturated 255 (a saturated reference
    could not show a BRIGHT object at all)."""
    mat = render_takhti(4.0)
    buf = cv2.resize(mat, (BUF_W, BUF_H), interpolation=cv2.INTER_AREA)
    return np.clip(buf.astype(np.float32) * (PAPER / 255.0) + 9.0,
                   0, 255).astype(np.uint8)


def box_mm(cx: float, cy: float, long_mm: float, short_mm: float,
           deg: float) -> np.ndarray:
    """Corners of an oriented rectangle, in mat millimetres."""
    t = np.radians(deg)
    R = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
    hl, hs = long_mm / 2.0, short_mm / 2.0
    local = np.array([[-hl, -hs], [hl, -hs], [hl, hs], [-hl, hs]], np.float64)
    return local @ R.T + np.array([cx, cy])


def paste(ref: np.ndarray, cx: float, cy: float, long_mm: float,
          short_mm: float, deg: float = 0.0, val: int = DARK) -> np.ndarray:
    """Composite an oriented rectangle of known mm size with 4x coverage AA."""
    poly = mm_to_buffer(box_mm(cx, cy, long_mm, short_mm, deg))
    big = np.zeros((BUF_H * SS, BUF_W * SS), np.uint8)
    cv2.fillConvexPoly(big, np.rint(poly * SS).astype(np.int32), 255)
    cov = cv2.resize(big, (BUF_W, BUF_H),
                     interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    out = ref.astype(np.float32) * (1.0 - cov) + float(val) * cov
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def noisy(img: np.ndarray, sigma: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.clip(img.astype(np.float32) + rng.normal(0, sigma, img.shape),
                   0, 255).astype(np.uint8)


def paste_disc(ref: np.ndarray, cx: float, cy: float, d_mm: float,
               val: int = DARK) -> np.ndarray:
    """A round item — a tin of ghee, a jar seen from above — with the same
    coverage compositing. It matters because pi/4 = 0.785 is the worst oriented-
    box fill any SINGLE object can have, so it is what bounds the merged-contour
    gate from below."""
    c = mm_to_buffer(np.array([[cx, cy]], np.float64))[0]
    big = np.zeros((BUF_H * SS, BUF_W * SS), np.uint8)
    cv2.ellipse(big, (int(round(c[0] * SS)), int(round(c[1] * SS))),
                (int(round(d_mm / 2 * PX_PER_MM_X * SS)),
                 int(round(d_mm / 2 * PX_PER_MM_Y * SS))), 0, 0, 360, 255, -1)
    cov = cv2.resize(big, (BUF_W, BUF_H),
                     interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    out = ref.astype(np.float32) * (1.0 - cov) + float(val) * cov
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def salt_pepper(img: np.ndarray, density: float, seed: int) -> np.ndarray:
    """Impulse noise: single pixels slammed to 0 or 255. NOT gaussian grain —
    this is the dust, crumb and specular-pinhole population that morphological
    opening exists for, and a gaussian model does not produce it."""
    rng = np.random.default_rng(seed)
    out = img.copy()
    m = rng.random(img.shape)
    out[m < density / 2.0] = 0
    out[m > 1.0 - density / 2.0] = 255
    return out


def crumb_trail(img: np.ndarray, x0_mm: float, x1_mm: float, y_mm: float,
                step_mm: float = 0.7) -> np.ndarray:
    """A line of isolated dark pixels — spilled dal, a thread — between two
    objects. Each speck is one pixel and could never be goods on its own."""
    out = img.copy()
    n = int(round((x1_mm - x0_mm) / step_mm)) + 1
    xs = np.linspace(x0_mm, x1_mm, n)
    pts = np.rint(mm_to_buffer(np.stack([xs, np.full(n, y_mm)], 1))).astype(int)
    for x, y in pts:
        out[y, x] = 0
    return out


def scratch(img: np.ndarray, x0_mm: float, x1_mm: float, y_mm: float,
            val: int) -> np.ndarray:
    """A one-pixel-wide faint mark on the mat: a pencil line, a crease shadow."""
    out = img.copy()
    p = np.rint(mm_to_buffer(np.array([[x0_mm, y_mm], [x1_mm, y_mm]]))).astype(int)
    cv2.line(out, tuple(p[0]), tuple(p[1]), int(val), 1)
    return out


def detector_without(ref: np.ndarray, *, opening: bool = True,
                     closing: bool = True) -> PlacementDetector:
    """A detector with a named morphology stage DELETED from the mask cleanup.

    PlacementDetector keeps that cleanup as an ordered list precisely so this is
    a deletion and not a simulation of one: the stage is removed from the
    pipeline, nothing else moves, and `_refine`'s own kernels are untouched so
    the only variable is the segmentation step under test.
    """
    det = PlacementDetector(ref)
    drop = set()
    if not opening:
        drop.add(cv2.MORPH_OPEN)
    if not closing:
        drop.add(cv2.MORPH_CLOSE)
    det._morph = tuple((op, k) for op, k in det._morph if op not in drop)
    return det


def one(det: PlacementDetector, frame: np.ndarray):
    ps = det.update(frame)
    assert len(ps) == 1, f"expected exactly one placement, got {len(ps)}: {ps}"
    return ps[0]


# ------------------------------------------------------------------ guards

def test_refuses_anything_that_is_not_the_metric_buffer():
    """Millimetres are only millimetres in the rectified buffer. Refuse loudly
    rather than silently reporting the wrong scale."""
    with pytest.raises(PlacementError):
        PlacementDetector(np.zeros((480, 640), np.uint8))
    det = PlacementDetector(empty_mat())
    with pytest.raises(PlacementError):
        det.update(np.zeros((600, 400), np.uint8))


def test_empty_mat_yields_no_placements():
    ref = empty_mat()
    det = PlacementDetector(ref)
    assert det.update(ref) == []
    assert det.update(ref.copy()) == []


def test_sensor_noise_alone_yields_no_placements():
    """Grain must not become goods. 4 grey levels of noise on an empty mat."""
    ref = empty_mat()
    det = PlacementDetector(ref)
    for i in range(20):
        assert det.update(noisy(ref, 4.0, seed=i)) == [], f"frame {i}"


def test_px_to_mm_inverts_mm_to_buffer():
    pts = np.array([[0.0, 0.0], [MAT_W_MM, MAT_H_MM], [100.0, 250.0]])
    assert px_to_mm(mm_to_buffer(pts.copy())) == pytest.approx(pts)


# -------------------------------------------------------------- ACCEPTANCE

# (long_mm, short_mm) — a shampoo sachet strip, a biscuit pack, a soap bar,
# a matchbox, a large carton, and a square.
SIZES = [(210.0, 30.0), (150.0, 100.0), (120.0, 80.0), (60.0, 40.0),
         (40.0, 40.0), (25.0, 15.0)]
ANGLES = [0.0, 12.0, 30.0, 45.0, 67.0, 90.0, 123.0]


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("deg", ANGLES)
@pytest.mark.parametrize("val", [DARK, BRIGHT])
def test_ACCEPTANCE_long_edge_within_3mm(size, deg, val):
    """S3a acceptance: measured long edge within 3 mm of truth, across sizes,
    rotations and both contrast polarities."""
    L, S = size
    ref = empty_mat()
    det = PlacementDetector(ref)
    p = one(det, paste(ref, *CENTRE, L, S, deg, val))
    assert p.measurable and p.reason == REASON_OK
    assert abs(p.long_edge_mm - L) < 3.0, (
        f"{L}x{S}mm @ {deg}deg val={val}: measured {p.long_edge_mm:.3f}mm"
    )
    assert abs(p.short_edge_mm - S) < 3.0, (
        f"{L}x{S}mm @ {deg}deg val={val}: short {p.short_edge_mm:.3f}mm"
    )


def test_ACCEPTANCE_measured_error_across_sizes_and_rotations(capsys):
    """The reported error number. Sweeps 6 sizes x 7 angles x 2 polarities = 84
    placements and asserts the WORST long-edge error, not the mean.

    Measured on this run: see the MEASURED block printed with -s.
    """
    ref = empty_mat()
    eL, eS, eA, ang_err, centre_err = [], [], [], [], []
    for L, S in SIZES:
        for deg in ANGLES:
            for val in (DARK, BRIGHT):
                det = PlacementDetector(ref)
                p = one(det, paste(ref, *CENTRE, L, S, deg, val))
                eL.append(p.long_edge_mm - L)
                eS.append(p.short_edge_mm - S)
                eA.append((p.area_mm2 - L * S) / (L * S))
                centre_err.append(float(np.hypot(p.centre_mm[0] - CENTRE[0],
                                                 p.centre_mm[1] - CENTRE[1])))
                if L != S:   # a square has no defined long edge
                    ang_err.append(abs((p.angle_deg - deg % 180.0 + 90.0)
                                       % 180.0 - 90.0))

    eL, eS, eA = np.array(eL), np.array(eS), np.array(eA)
    worst = float(np.abs(eL).max())
    print(f"\nMEASURED n={eL.size} placements")
    print(f"  long edge   : max |err| {worst:.3f} mm   "
          f"mean {eL.mean():+.3f} mm   rms {np.sqrt((eL**2).mean()):.3f} mm")
    print(f"  short edge  : max |err| {np.abs(eS).max():.3f} mm   "
          f"mean {eS.mean():+.3f} mm")
    print(f"  box area    : max |err| {100*np.abs(eA).max():.2f} %")
    print(f"  angle       : max |err| {max(ang_err):.3f} deg  (n={len(ang_err)})")
    print(f"  centre      : max |err| {max(centre_err):.3f} mm")

    assert worst < 3.0, f"worst long-edge error {worst:.3f} mm exceeds the 3 mm budget"
    assert float(np.abs(eS).max()) < 3.0
    assert max(centre_err) < 1.0
    assert max(ang_err) < 2.0


def test_ACCEPTANCE_survives_sensor_noise():
    """Same budget with 5 grey levels of gaussian sensor noise on every frame."""
    ref = empty_mat()
    worst = 0.0
    for i, (L, S) in enumerate(SIZES):
        for deg in (0.0, 37.0, 45.0):
            det = PlacementDetector(noisy(ref, 5.0, seed=900 + i))
            f = noisy(paste(ref, *CENTRE, L, S, deg, DARK), 5.0, seed=i * 7 + 1)
            p = one(det, f)
            worst = max(worst, abs(p.long_edge_mm - L))
    print(f"\nMEASURED noisy(sigma=5) worst long-edge error {worst:.3f} mm")
    assert worst < 3.0


# ------------------------------------------ THE MORPHOLOGY IS LOAD-BEARING
# PRD 3.4 names morphologyEx OPEN and CLOSE by hand. Naming a step is not the
# same as needing it, so this section builds ONE counter scene in which each
# step is the only thing standing between the pipeline and a wrong bill, and
# runs it four ways: shipped, OPEN deleted, CLOSE deleted, both deleted.

# Two 90 x 55 mm packets 15 mm apart, with a trail of single-pixel crumbs and a
# 1 px scratch across the gap; and a pale sachet whose printed band reads within
# 2 grey levels of the paper, so the threshold cuts the sachet in half.
PACK_A = (75.0, 130.0)
PACK_B = (180.0, 130.0)
PACK_MM = (90.0, 55.0)
SACHET = (148.5, 300.0)
SACHET_MM = (140.0, 70.0)
SACHET_VAL = 240          # a pale wrapper: 40 grey levels off the paper
SACHET_BAND_VAL = 226     # its printed band: 26 off the paper, under DIFF_THRESH
SACHET_BAND_MM = 1.0


def counter_scene() -> np.ndarray:
    """Two packets bridged by crumbs, plus a glossy sachet, plus impulse noise."""
    ref = empty_mat()
    img = paste(ref, *PACK_A, *PACK_MM, 0.0, DARK)
    img = paste(img, *PACK_B, *PACK_MM, 0.0, DARK)
    img = paste(img, *SACHET, *SACHET_MM, 0.0, SACHET_VAL)
    img = paste(img, *SACHET, SACHET_MM[1], SACHET_BAND_MM, 90.0, SACHET_BAND_VAL)
    img = crumb_trail(img, 121.0, 134.0, PACK_A[1])
    img = scratch(img, 118.0, 137.0, PACK_A[1], 115)
    return salt_pepper(img, 0.004, seed=7)


def _by_position(ps):
    return sorted(ps, key=lambda p: (round(p.centre_mm[1] / 50.0), p.centre_mm[0]))


def test_MORPHOLOGY_open_and_close_both_change_the_measured_answer(capsys):
    """Delete either morphology stage and the till is wrong. Measured, not asserted.

    shipped        three items, all measured, all within 1.6 mm of truth
    OPEN deleted   the crumbs and the scratch survive, CLOSE welds them into a
                   bridge, and the two packets arrive as ONE contour — two
                   prices collapsed into one refusal
    CLOSE deleted  the sachet's printed band is never healed, so one wrapper
                   arrives as TWO measurable halves and is billed twice
    both deleted   both failures at once

    Every row of the table below is printed by this test.
    """
    ref = empty_mat()
    img = counter_scene()
    rows = {}
    for label, kw in (("shipped", {}),
                      ("no OPEN", {"opening": False}),
                      ("no CLOSE", {"closing": False}),
                      ("neither", {"opening": False, "closing": False})):
        rows[label] = _by_position(detector_without(ref, **kw).update(img))

    print("\nMEASURED morphology ablation on one counter scene")
    for label, ps in rows.items():
        print(f"  {label:>9}: {len(ps)} placements  " + "  ".join(
            f"[{p.reason} "
            + ("-" if p.long_edge_mm is None else f"{p.long_edge_mm:.1f}x{p.short_edge_mm:.1f}mm")
            + f" fill={'-' if p.fill_ratio is None else format(p.fill_ratio, '.3f')}]"
            for p in ps))

    # --- shipped: three separate goods, all measured -------------------------
    ship = rows["shipped"]
    assert len(ship) == 3, [p.reason for p in ship]
    assert all(p.measurable and p.reason == REASON_OK for p in ship)
    truth = [PACK_MM[0], PACK_MM[0], SACHET_MM[0]]
    errs = [abs(p.long_edge_mm - t) for p, t in zip(ship, truth)]
    print(f"  shipped long-edge errors {[round(e, 2) for e in errs]} mm")
    assert max(errs) < 3.0, errs
    assert ship[0].centre_mm[0] < ship[1].centre_mm[0]   # the two packets
    # The impulse noise is really there, and none of it became goods. Measured
    # on this scene: OPEN leaves 33 contours of which 30 are dropped as grain;
    # without OPEN there are 1542, of which 1540 are dropped. The min-area floor
    # is what stops a speck being priced; OPEN is what stops it being GLUED to
    # something that is.
    det = PlacementDetector(ref)
    det.update(img)
    speckle, _ = cv2.findContours(det.last_mask, cv2.RETR_EXTERNAL,
                                  cv2.CHAIN_APPROX_SIMPLE)
    raw = detector_without(ref, opening=False)
    raw.update(img)
    unopened, _ = cv2.findContours(raw.last_mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    print(f"  contours in the mask: {len(speckle)} with OPEN, {len(unopened)} "
          f"without; grain dropped {det.last_rejected_small} / "
          f"{raw.last_rejected_small}")
    assert len(unopened) > 10 * len(speckle), (
        "the scene has stopped carrying impulse noise, so OPEN is not under test"
    )
    assert det.last_rejected_small >= len(speckle) - len(ship)

    # --- OPEN deleted: the two packets stop being two ------------------------
    no_open = rows["no OPEN"]
    assert len(no_open) == 2, (
        "deleting morphologyEx OPEN left the crumb bridge alone; this scene no "
        "longer exercises the step it exists to test"
    )
    welded = [p for p in no_open if p.centre_mm[1] < 200.0]
    assert len(welded) == 1, [p.centre_mm for p in no_open]
    assert not welded[0].measurable and welded[0].reason == REASON_MERGED, (
        f"two packets welded by speckle were reported as one measurable item: "
        f"{welded[0]}"
    )
    # and the sachet next to it is untouched by the ablation
    assert [p.reason for p in no_open].count(REASON_OK) == 1

    # --- CLOSE deleted: one sachet becomes two billable halves ---------------
    no_close = rows["no CLOSE"]
    assert len(no_close) == 4, [p.reason for p in no_close]
    halves = [p for p in no_close if p.centre_mm[1] > 200.0]
    assert len(halves) == 2 and all(p.measurable for p in halves), halves
    assert all(abs(p.long_edge_mm - SACHET_MM[1]) < 3.0 for p in halves), halves
    over = sum(p.area_mm2 for p in halves) / (SACHET_MM[0] * SACHET_MM[1])
    print(f"  CLOSE deleted: one {SACHET_MM[0]:.0f}x{SACHET_MM[1]:.0f} mm sachet "
          f"billed as {len(halves)} items covering {100 * over:.0f}% of its footprint")
    assert 0.85 < over < 1.15, over

    # --- both deleted: both failures ----------------------------------------
    both = rows["neither"]
    assert len(both) == 3
    assert sum(p.reason == REASON_MERGED for p in both) == 1
    assert sum(p.measurable for p in both) == 2

    # the shipped answer is the only one of the four that is right
    assert [p.reason for p in ship] != [p.reason for p in no_open]
    assert [round(p.long_edge_mm or -1, 1) for p in ship] != \
           [round(p.long_edge_mm or -1, 1) for p in no_close]


def test_MORPHOLOGY_kernel_reach_measured_in_millimetres(capsys):
    """What the two kernels actually do to a mask, in mm on the plane.

    Sizes in pixels mean nothing on their own; what matters is the smallest
    real thing OPEN destroys and the largest real gap CLOSE welds shut. Both are
    measured here by feeding the module's OWN structuring elements synthetic
    masks, so the numbers move if the constants do.
    """
    det = PlacementDetector(empty_mat())
    k_open = [k for op, k in det._morph if op == cv2.MORPH_OPEN][0]
    k_close = [k for op, k in det._morph if op == cv2.MORPH_CLOSE][0]

    survives = []
    for w in range(1, 9):
        m = np.zeros((60, 60), np.uint8)
        m[10:50, 20:20 + w] = 255
        if cv2.morphologyEx(m, cv2.MORPH_OPEN, k_open).any():
            survives.append(w)
    thinnest = min(survives)

    bridged = []
    for g in range(0, 9):
        m = np.zeros((60, 80), np.uint8)
        m[10:50, 5:30] = 255
        m[10:50, 30 + g:70] = 255
        cs, _ = cv2.findContours(cv2.morphologyEx(m, cv2.MORPH_CLOSE, k_close),
                                 cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if len(cs) == 1:
            bridged.append(g)
    widest = max(bridged)

    print(f"\nMEASURED kernel reach: OPEN erases anything under {thinnest} px "
          f"({thinnest / PX_PER_MM_X:.2f} mm) thick; CLOSE welds gaps up to "
          f"{widest} px ({widest / PX_PER_MM_X:.2f} mm)")

    # OPEN has to destroy a crumb trail and keep a packet's corner.
    assert thinnest / PX_PER_MM_X < 1.5, (
        f"opening now eats {thinnest / PX_PER_MM_X:.2f} mm of a real object"
    )
    assert thinnest >= 2, "an opening that erases nothing is not an opening"
    # CLOSE has to heal a wrapper and NOT weld two packets a couple of mm apart.
    assert widest >= thinnest, (
        "a closing that cannot span what the opening erased heals nothing"
    )
    assert widest / PX_PER_MM_X < 2.0, (
        f"closing now welds a {widest / PX_PER_MM_X:.2f} mm gap; two packets "
        f"laid that far apart would become one price"
    )
    # ...and that mm claim is the same one test_objects_placed_apart_are_two_prices
    # relies on when it puts 2 mm of daylight between two packets.
    assert OPEN_PX <= CLOSE_PX


def test_MORPHOLOGY_impulse_noise_alone_is_never_goods():
    """Salt and pepper on an empty mat, with and without OPEN. Neither may
    produce a placement — OPEN is for what the noise does NEXT TO an object,
    and the min-area floor is what stops it being goods on its own."""
    ref = empty_mat()
    for density in (0.002, 0.004, 0.01):
        img = salt_pepper(ref, density, seed=int(density * 10000))
        assert PlacementDetector(ref).update(img) == [], density
        assert detector_without(ref, opening=False).update(img) == [], density


def test_the_refit_may_correct_an_edge_but_may_not_lose_the_object(capsys):
    """_refine promises a sub-pixel correction. It has to keep that promise.

    Found by sweeping 400 randomly posed single objects: a pale 55 x 23 mm strip
    at 128 deg under sigma=3 sensor noise segments as ONE coarse blob of the
    right area, and then the 50 %-amplitude refit shatters it and returns its
    largest crumb — 20.2 x 17.3 mm, published as a confident OK. That is a 35 mm
    undermeasurement on a correctly detected object, in the undercharging
    direction, with no refusal attached.

    The refit is allowed to move an edge. It is not allowed to return a quarter
    of the object. Measured on the same sweep, a healthy refit lands between
    0.93 and 1.06 of the coarse oriented-box area; this case landed at 0.27.
    """
    ref = empty_mat()
    cases = [
        # (cx, cy, long, short, deg, val, sigma, ref_seed, img_seed)
        (130.4, 92.6, 55.1, 22.5, 127.7, BRIGHT, 3.0, 1, 0),
        (130.4, 92.6, 55.1, 22.5, 127.7, BRIGHT, 3.0, 5000, 0),
    ]
    print("\nMEASURED the refit's floor")
    for cx, cy, L, S, deg, val, sigma, rs, ims in cases:
        det = PlacementDetector(noisy(ref, sigma, rs))
        p = one(det, noisy(paste(ref, cx, cy, L, S, deg, val), sigma, ims))
        print(f"  {L}x{S}mm @{deg}deg val={val} sigma={sigma} ref_seed={rs} -> "
              f"{p.reason} {p.long_edge_mm:.1f}x{p.short_edge_mm:.1f}mm")
        assert p.measurable, p.reason
        assert abs(p.long_edge_mm - L) < 3.0, (
            f"the refit lost the object: measured {p.long_edge_mm:.1f} mm of a "
            f"{L} mm strip"
        )
        assert abs(p.short_edge_mm - S) < 3.0, p.short_edge_mm


# --------------------------------------------------- ORIENTED, NOT AXIS-ALIGNED

def test_rotation_does_not_inflate_area():
    """The AABB-is-not-a-footprint bug, as an executable fact.

    A 210x30 mm strip turned 45 deg has an axis-aligned bounding box of about
    170x170 mm — 4.5x the true footprint. The oriented box must not move.
    """
    ref = empty_mat()
    truth = 210.0 * 30.0
    areas, aabbs = {}, {}
    for deg in (0.0, 45.0, 90.0):
        det = PlacementDetector(ref)
        p = one(det, paste(ref, *CENTRE, 210.0, 30.0, deg, DARK))
        areas[deg] = p.area_mm2

        cnts, _ = cv2.findContours(det.last_mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
        x, y, w, h = cv2.boundingRect(max(cnts, key=cv2.contourArea))
        aabbs[deg] = (w / PX_PER_MM_X) * (h / PX_PER_MM_Y)

    print(f"\nMEASURED oriented area {areas}  vs AABB area {aabbs}  truth {truth}")
    for deg, a in areas.items():
        assert abs(a - truth) / truth < 0.05, f"{deg}deg: {a:.0f} vs {truth:.0f}"
    # spread of the ORIENTED area across rotations
    spread = (max(areas.values()) - min(areas.values())) / truth
    assert spread < 0.03, f"oriented area varies {100*spread:.1f}% with rotation"
    # and the axis-aligned answer really would have been catastrophic
    assert aabbs[45.0] > 4.0 * areas[45.0], (
        f"AABB at 45deg was only {aabbs[45.0]/areas[45.0]:.2f}x the oriented "
        "area — the test no longer demonstrates the bug it guards"
    )


@pytest.mark.parametrize("deg", [0.0, 17.0, 45.0, 88.0, 121.0, 160.0])
def test_angle_is_recovered(deg):
    ref = empty_mat()
    det = PlacementDetector(ref)
    p = one(det, paste(ref, *CENTRE, 180.0, 45.0, deg, DARK))
    err = abs((p.angle_deg - deg % 180.0 + 90.0) % 180.0 - 90.0)
    assert err < 2.0, f"truth {deg%180:.1f}deg, measured {p.angle_deg:.2f}deg"


def test_long_and_short_are_ordered():
    ref = empty_mat()
    det = PlacementDetector(ref)
    p = one(det, paste(ref, *CENTRE, 210.0, 30.0, 33.0, DARK))
    assert p.long_edge_mm > p.short_edge_mm
    assert p.area_mm2 == pytest.approx(p.long_edge_mm * p.short_edge_mm)
    assert 0.0 <= p.angle_deg < 180.0
    assert 0.85 < p.fill_ratio <= 1.05, p.fill_ratio


# ---------------------------------------------------------- STABILITY GATE

def test_stability_gate_needs_exactly_five_frames():
    """Not four. Not a timer. Five consecutive agreeing frames."""
    ref = empty_mat()
    det = PlacementDetector(ref)
    f = paste(ref, *CENTRE, 120.0, 80.0, 20.0, DARK)
    seen = []
    for i in range(1, 8):
        p = one(det, f)
        seen.append((i, p.frames_seen, p.stable_run, p.stable))
    print(f"\nMEASURED stability ladder {seen}")
    assert STABLE_FRAMES == 5
    for i, frames_seen, run, stable in seen:
        assert frames_seen == i
        assert stable is (i >= 5), f"frame {i}: stable={stable}, run={run}"


def test_stability_gate_holds_under_sensor_noise():
    """A gate that only latches on byte-identical frames is a gate that never
    latches on real hardware. Fresh noise every frame, must still latch at 5."""
    ref = empty_mat()
    clean = paste(ref, *CENTRE, 150.0, 100.0, 31.0, DARK)
    det = PlacementDetector(noisy(ref, 5.0, seed=77))
    jitter_c, jitter_a, first_stable = [], [], None
    prev = None
    for i in range(1, 10):
        p = one(det, noisy(clean, 5.0, seed=1000 + i))
        if prev is not None:
            jitter_c.append(float(np.hypot(p.centre_mm[0] - prev.centre_mm[0],
                                           p.centre_mm[1] - prev.centre_mm[1])))
            jitter_a.append(abs(p.area_mm2 - prev.area_mm2) / prev.area_mm2)
        prev = p
        if p.stable and first_stable is None:
            first_stable = i
    print(f"\nMEASURED noisy jitter: centre max {max(jitter_c):.4f} mm, "
          f"area max {100*max(jitter_a):.3f} %, first stable at frame {first_stable}")
    assert first_stable == 5, f"latched at frame {first_stable}, expected 5"


def test_movement_breaks_the_run_and_it_must_be_re_earned():
    """A moving object is never stable, and after it settles the gate starts
    from one again — it does not resume where it left off."""
    ref = empty_mat()
    det = PlacementDetector(ref)
    here = paste(ref, 120.0, 200.0, 120.0, 80.0, 10.0, DARK)
    there = paste(ref, 170.0, 240.0, 120.0, 80.0, 10.0, DARK)

    for _ in range(6):
        p = one(det, here)
    assert p.stable

    p = one(det, there)
    assert not p.stable, "a jump of ~64 mm must break stability immediately"
    assert p.stable_run == 1

    for i in range(2, 5):
        p = one(det, there)
        assert not p.stable, f"only {i} frames at the new pose"
    p = one(det, there)
    assert p.stable, "5 frames at the new pose must re-earn stability"


def test_slow_creep_never_counts_as_stable():
    """The reason stability is checked against a run ANCHOR and not against the
    previous frame: 12 steps of 0.5 mm are each 'stable' pairwise, but the
    object has walked 6 mm."""
    ref = empty_mat()
    det = PlacementDetector(ref)
    stables = []
    for i in range(14):
        f = paste(ref, 120.0 + 0.5 * i, 200.0, 120.0, 80.0, 0.0, DARK)
        p = one(det, f)
        stables.append(p.stable)
    print(f"\nMEASURED creep(0.5mm/frame) stable flags {stables}")
    assert not any(stables), "0.5 mm/frame creep was reported as stable"


def test_area_tolerance_actually_bites():
    """The centre is not the only gate. An object held in place while its
    footprint pulses — a hand still resting on it, a bag settling — must not be
    called stable. AREA_TOL_FRAC is 5 %; this pulses ~11 %."""
    ref = empty_mat()
    det = PlacementDetector(ref)
    stables = []
    for i in range(14):
        S = 80.0 if i % 2 == 0 else 89.0        # +11 % area, centre unmoved
        p = one(det, paste(ref, *CENTRE, 120.0, S, 0.0, DARK))
        stables.append(p.stable)
    print(f"\nMEASURED area-pulse(+11%) stable flags {stables}")
    assert not any(stables), "an object whose footprint pulses went stable"


def test_stability_needs_the_object_present_every_frame():
    ref = empty_mat()
    det = PlacementDetector(ref)
    f = paste(ref, *CENTRE, 120.0, 80.0, 0.0, DARK)
    for _ in range(4):
        one(det, f)
    assert det.update(ref) == []          # a dropped frame
    p = one(det, f)
    assert not p.stable and p.stable_run == 1


# ------------------------------------------------------- BORDER REFUSAL

@pytest.mark.parametrize("where,cx,cy", [
    ("left",   8.0,             MAT_H_MM / 2),
    ("right",  MAT_W_MM - 8.0,  MAT_H_MM / 2),
    ("top",    MAT_W_MM / 2,    8.0),
    ("bottom", MAT_W_MM / 2,    MAT_H_MM - 8.0),
])
def test_border_touching_object_is_refused_not_measured(where, cx, cy):
    """"poora rakhiye" — the mat cropped this object, so its extent is unknown.
    Flag it, never measure it, and never let it go stable."""
    ref = empty_mat()
    det = PlacementDetector(ref)
    p = one(det, paste(ref, cx, cy, 90.0, 60.0, 0.0, DARK))
    assert p.measurable is False, where
    assert p.reason == REASON_BORDER
    for field in ("long_edge_mm", "short_edge_mm", "area_mm2", "angle_deg",
                  "contour_area_mm2", "fill_ratio"):
        assert getattr(p, field) is None, f"{where}: {field} was measured anyway"
    assert p.centre_mm is not None       # enough to draw the hint on, no more


def test_border_object_never_becomes_stable_however_long_it_sits():
    ref = empty_mat()
    det = PlacementDetector(ref)
    f = paste(ref, 8.0, MAT_H_MM / 2, 90.0, 60.0, 0.0, DARK)
    for i in range(15):
        p = one(det, f)
        assert not p.stable, f"frame {i}: an unmeasurable blob went stable"
        assert p.stable_run == 0
    assert p.frames_seen == 15           # still tracked, just never trusted


def test_object_just_inside_the_border_is_measured():
    """The refusal must be a real edge test, not a blanket margin. BORDER_PX is
    ~0.7 mm, so an object clearing the edge by a few mm is fine."""
    ref = empty_mat()
    det = PlacementDetector(ref)
    L, S = 90.0, 60.0
    p = one(det, paste(ref, S / 2 + 5.0, MAT_H_MM / 2, L, S, 90.0, DARK))
    assert p.measurable, p.reason
    assert abs(p.short_edge_mm - S) < 3.0


def test_a_measurable_and_a_refused_object_coexist():
    """One bad placement must not poison the good one next to it."""
    ref = empty_mat()
    img = paste(ref, 150.0, 300.0, 100.0, 60.0, 25.0, DARK)
    img = paste(img, 6.0, 90.0, 80.0, 50.0, 0.0, DARK)
    det = PlacementDetector(ref)
    ps = det.update(img)
    assert len(ps) == 2, ps
    good = [p for p in ps if p.measurable]
    bad = [p for p in ps if not p.measurable]
    assert len(good) == 1 and len(bad) == 1
    assert abs(good[0].long_edge_mm - 100.0) < 3.0
    assert bad[0].reason == REASON_BORDER


# ------------------------------------------- MERGED CONTOUR: "alag alag rakhiye"
# One contour is one price. Two goods that touch segment as ONE contour, so
# unless the detector refuses them the shopkeeper is billed for one item and the
# till is short. This is the only refusal in the module whose absence LOSES
# MONEY rather than reporting an unknown, so it is measured hardest.

# (label, packet A, packet B) with each packet (cx, cy, long, short, deg).
MERGES = [
    ("flush side by side",  (148.5, 200.0, 100.0, 60.0, 0.0),
                            (148.5, 261.0, 100.0, 60.0, 0.0)),
    ("end to end",          (148.5, 180.0, 120.0, 50.0, 0.0),
                            (148.5, 231.0, 120.0, 50.0, 0.0)),
    ("corner to corner",    (110.0, 200.0, 100.0, 60.0, 0.0),
                            (190.0, 261.0, 100.0, 60.0, 0.0)),
    ("L",                   (110.0, 200.0, 100.0, 60.0, 0.0),
                            (190.0, 230.0, 60.0, 100.0, 0.0)),
    ("T",                   (148.5, 200.0, 140.0, 50.0, 0.0),
                            (148.5, 285.0, 50.0, 120.0, 0.0)),
    ("crossed",             (148.5, 240.0, 140.0, 45.0, 0.0),
                            (148.5, 240.0, 140.0, 45.0, 90.0)),
    ("X at 30 deg",         (148.5, 240.0, 150.0, 45.0, 30.0),
                            (148.5, 240.0, 150.0, 45.0, -30.0)),
    ("shoulder to shoulder", (110.0, 200.0, 90.0, 60.0, 0.0),
                             (180.0, 245.0, 90.0, 60.0, 0.0)),
    ("half stacked",        (120.0, 200.0, 120.0, 60.0, 0.0),
                            (180.0, 258.0, 120.0, 60.0, 0.0)),
]


def merged_scene(a, b, val: int = DARK) -> np.ndarray:
    ref = empty_mat()
    img = paste(ref, a[0], a[1], a[2], a[3], a[4], val)
    return paste(img, b[0], b[1], b[2], b[3], b[4], val)


@pytest.mark.parametrize("case", MERGES, ids=[m[0] for m in MERGES])
def test_ACCEPTANCE_two_touching_objects_are_refused_not_billed_as_one(case):
    """Nine ways to put two packets down touching. None of them may produce a
    measurable placement, because a measurable placement is a price."""
    label, a, b = case
    ref = empty_mat()
    ps = PlacementDetector(ref).update(merged_scene(a, b))
    assert len(ps) == 1, (
        f"{label}: the segmentation separated them after all, so this case no "
        f"longer tests the merge it was chosen for ({len(ps)} contours)"
    )
    p = ps[0]
    assert p.measurable is False, (
        f"{label}: two goods were billed as one item of "
        f"{p.long_edge_mm:.0f}x{p.short_edge_mm:.0f} mm"
    )
    assert p.reason == REASON_MERGED
    for field in ("long_edge_mm", "short_edge_mm", "area_mm2", "angle_deg"):
        assert getattr(p, field) is None, f"{label}: {field} was measured anyway"
    # the evidence that caused the refusal is published, not swallowed
    assert p.fill_ratio is not None and p.components is not None
    assert (p.components >= 2) or (p.fill_ratio < MERGED_MIN_FILL), (
        f"{label}: refused with neither signal tripped "
        f"(fill {p.fill_ratio:.3f}, components {p.components})"
    )
    assert p.centre_mm is not None       # enough to draw the hint on, no more


def test_ACCEPTANCE_the_money_the_merge_refusal_saves(monkeypatch, capsys):
    """What the till would say with the refusal lifted. Two goods, one price.

    Every abstention costs the shopkeeper a re-place, so the gate has to earn
    its keep with a number. The nine scenes are run three ways:

      shipped        refused, nothing billed
      no merge gate  ONE measurable item where TWO goods are on the mat, with a
                     footprint 2 % to 83 % wrong
      no merge gate  the same, and with REFINE_MIN_KEEP lifted too: the refit
      and no refit   keeps its largest crumb, so a flush pair is billed as ONE
      floor          packet of exactly one packet's size — the item is not just
                     mispriced, it is gone

    The item COUNT is the bug in every row. The footprint error is how badly the
    one surviving item is priced.
    """
    import gawaah.placement as mod
    ref = empty_mat()
    truths = {label: (a[2] * a[3] + b[2] * b[3], max(a[2], b[2]))
              for label, a, b in MERGES}

    def bill(label, a, b):
        ps = PlacementDetector(ref).update(merged_scene(a, b))
        assert len(ps) == 1, (label, [p.reason for p in ps])
        assert ps[0].measurable, (label, ps[0].reason)
        return ps[0]

    monkeypatch.setattr(PlacementDetector, "_is_merged",
                        lambda self, fill, box, parts: False)
    lifted = {label: bill(label, a, b) for label, a, b in MERGES}
    monkeypatch.setattr(mod, "REFINE_MIN_KEEP", 0.0)
    no_floor = {label: bill(label, a, b) for label, a, b in MERGES}

    print("\nMEASURED what a merged contour is billed as when the gate is lifted")
    print(f"  {'placement':>21} {'goods':>6} {'billed':>7} {'footprint':>10} "
          f"{'vs truth':>9} {'long edge':>10}   without the refit floor")
    for label, a, b in MERGES:
        truth_area, longest = truths[label]
        p, q = lifted[label], no_floor[label]
        print(f"  {label:>21} {2:>6} {1:>7} {p.area_mm2:8.0f}mm2 "
              f"{100 * (p.area_mm2 - truth_area) / truth_area:+8.0f}% "
              f"{p.long_edge_mm:8.0f}mm   "
              f"{q.long_edge_mm:.0f}x{q.short_edge_mm:.0f}mm "
              f"({100 * (q.area_mm2 - truth_area) / truth_area:+.0f}%)")

    # 1. The count. Two goods went down, one price came back, every time.
    assert len(lifted) == len(MERGES)

    # 2. The price. At least one merge is billed grossly oversized...
    errs = [(lifted[l].area_mm2 - truths[l][0]) / truths[l][0] for l, _, _ in MERGES]
    assert max(errs) > 0.25, (
        f"no merge overcharged by more than {100 * max(errs):.0f}%; the angled "
        f"cases have stopped merging and this scene needs re-choosing"
    )
    # ...and with the refit floor also lifted, at least one is billed as a
    # single packet — the second item simply vanishes from the till.
    gone = [l for l, _, _ in MERGES
            if (no_floor[l].area_mm2 - truths[l][0]) / truths[l][0] < -0.40]
    assert gone, (
        "no merge undercharged even with REFINE_MIN_KEEP lifted; the flush "
        "cases have stopped merging"
    )
    print(f"  worst overcharge {100 * max(errs):+.0f}%; billed as one packet "
          f"instead of two on {gone}")

    # 3. With the gate back in place not one of them is billed at all.
    monkeypatch.undo()
    for label, a, b in MERGES:
        ps = PlacementDetector(ref).update(merged_scene(a, b))
        assert not any(p.measurable for p in ps), label


def test_the_two_merge_signals_each_catch_what_the_other_cannot(capsys):
    """Why there are two. fill_ratio cannot see a flush pair — two rectangles
    laid edge to edge fill their oriented box perfectly — and the component
    count cannot see a pair that stays connected at half amplitude. Measured,
    per scene, so neither signal can be quietly deleted."""
    ref = empty_mat()
    rows = []
    for label, a, b in MERGES:
        p = PlacementDetector(ref).update(merged_scene(a, b))[0]
        rows.append((label, p.fill_ratio, p.components,
                     p.fill_ratio < MERGED_MIN_FILL, p.components >= 2))
    print("\nMEASURED which signal fires on which merge")
    print(f"  {'placement':>21} {'fill':>7} {'parts':>6} {'by fill':>8} {'by parts':>9}")
    for label, fill, parts, by_fill, by_parts in rows:
        print(f"  {label:>21} {fill:7.3f} {parts:6d} {str(by_fill):>8} "
              f"{str(by_parts):>9}")

    fill_only = [r[0] for r in rows if r[3] and not r[4]]
    parts_only = [r[0] for r in rows if r[4] and not r[3]]
    assert fill_only, (
        "every merge is now caught by the component count, so MERGED_MIN_FILL "
        "is dead weight and should be deleted rather than documented"
    )
    assert parts_only, (
        "every merge is now caught by fill_ratio, so the component count is "
        "dead weight and should be deleted rather than documented"
    )
    assert all(r[3] or r[4] for r in rows)
    # the flush pair is specifically the one fill cannot see
    flush = [r for r in rows if r[0] == "flush side by side"][0]
    assert flush[1] > 0.95, (
        f"two packets laid flush filled only {flush[1]:.3f} of their box; the "
        f"stated reason for the second signal has changed"
    )


def test_merged_gate_is_bracketed_by_measurement(capsys):
    """MERGED_MIN_FILL has to sit under the worst-filling SINGLE object and over
    the best-filling MERGE. Both populations are re-derived here, so the
    constant cannot drift away from the evidence that chose it.

    The binding case on the single side is a ROUND item: pi/4 = 0.785 is the
    least any one convex object can fill its own oriented box, and refusing a
    tin of ghee is a false abstention that costs a re-place for nothing.
    """
    ref = empty_mat()
    singles = []
    for L, S in SIZES:
        for deg in (0.0, 12.0, 45.0, 67.0):
            for val in (DARK, BRIGHT):
                singles.append(one(PlacementDetector(ref),
                                   paste(ref, *CENTRE, L, S, deg, val)).fill_ratio)
    round_items = []
    for d in (30.0, 45.0, 60.0, 80.0, 100.0, 130.0):
        for val in (DARK, BRIGHT):
            p = one(PlacementDetector(ref), paste_disc(ref, *CENTRE, d, val))
            assert p.measurable, f"a {d:.0f} mm round item was refused: {p.reason}"
            round_items.append(p.fill_ratio)
    merges = [PlacementDetector(ref).update(merged_scene(a, b))[0].fill_ratio
              for _, a, b in MERGES]
    by_fill = [f for f in merges if f < MERGED_MIN_FILL]

    print(f"\nMEASURED fill_ratio populations (gate {MERGED_MIN_FILL})")
    print(f"  rectangles n={len(singles):<3} {min(singles):.3f} .. {max(singles):.3f}"
          "   -> measure")
    print(f"  round items n={len(round_items):<3} {min(round_items):.3f} .. "
          f"{max(round_items):.3f}   -> measure")
    print(f"  merges caught by fill n={len(by_fill):<3} {min(by_fill):.3f} .. "
          f"{max(by_fill):.3f}   -> refuse")
    print(f"  headroom above the gate {min(round_items) - MERGED_MIN_FILL:+.3f} "
          f"(round items), below it {MERGED_MIN_FILL - max(by_fill):+.3f} (merges)")

    assert max(by_fill) < MERGED_MIN_FILL < min(round_items) < min(singles)
    assert min(round_items) - MERGED_MIN_FILL < 0.10, (
        "the round-item margin is documented as thin (a tin fills 0.785 of its "
        "box and the gate is 0.75); if it has widened, say so with the new "
        "number instead of leaving the old caveat standing"
    )


@pytest.mark.parametrize("chip_mm", [12.0, 15.0, 20.0, 30.0, 45.0])
def test_a_small_item_flush_against_a_big_one_is_still_two_prices(chip_mm):
    """The unequal merge, which is the one a counter actually produces: a sachet
    leaning on a carton. It must be caught down to the smallest thing this
    detector will admit as goods at all.

    This is why "goods-sized" in _refine is MIN_AREA_MM2 and not a fraction of
    the blob. Measured on the 12 mm chip: the refit pieces are 77815 px and 1088
    px, so an absolute floor of 800 px (100 mm^2) sees two components, while a
    20 %-of-the-blob floor sits at 15781 px and sees one — the sachet is free.
    """
    ref = empty_mat()
    img = paste(ref, 148.5, 200.0, 140.0, 70.0, 0.0, DARK)       # y 165..235
    img = paste(img, 148.5, 235.5 + chip_mm / 2, chip_mm, chip_mm, 0.0, DARK)
    ps = PlacementDetector(ref).update(img)
    assert len(ps) == 1, [p.reason for p in ps]
    assert chip_mm * chip_mm > MIN_AREA_MM2, "the chip must be admissible goods"
    assert ps[0].reason == REASON_MERGED, (
        f"a {chip_mm:.0f} mm item flush against a 140 mm carton was billed as "
        f"one {ps[0].long_edge_mm:.0f} mm item"
    )
    assert ps[0].components >= 2


def test_merged_refusal_never_fires_on_a_single_object():
    """The false-abstention control. Every single object in the acceptance sweep,
    with and without sensor noise, must still be MEASURED — an abstention is
    cheap for the doctrine and expensive for the shopkeeper."""
    ref = empty_mat()
    for L, S in SIZES:
        for deg in ANGLES:
            for val in (DARK, BRIGHT):
                p = one(PlacementDetector(ref), paste(ref, *CENTRE, L, S, deg, val))
                assert p.reason == REASON_OK, f"{L}x{S}@{deg} val={val}: {p.reason}"
                assert p.components == 1, (L, S, deg, val, p.components)
    for i, (L, S) in enumerate(SIZES):
        for deg in (0.0, 37.0, 45.0):
            det = PlacementDetector(noisy(ref, 5.0, seed=900 + i))
            p = one(det, noisy(paste(ref, *CENTRE, L, S, deg, DARK), 5.0,
                               seed=i * 7 + 1))
            assert p.reason == REASON_OK, f"noisy {L}x{S}@{deg}: {p.reason}"


def test_objects_placed_apart_are_two_prices():
    """The other half of "alag alag rakhiye": once they ARE apart, both are
    measured. 2 mm of daylight is enough."""
    ref = empty_mat()
    img = merged_scene((148.5, 200.0, 100.0, 60.0, 0.0),
                       (148.5, 263.0, 100.0, 60.0, 0.0))
    ps = PlacementDetector(ref).update(img)
    assert len(ps) == 2, [p.reason for p in ps]
    assert all(p.measurable and p.reason == REASON_OK for p in ps)
    assert all(abs(p.long_edge_mm - 100.0) < 3.0 for p in ps), ps


def test_a_merged_blob_never_becomes_stable():
    """Same rule as the border refusal: an item we will not measure can never
    accumulate the stability a downstream stage prices on."""
    ref = empty_mat()
    det = PlacementDetector(ref)
    img = merged_scene(*MERGES[0][1:])
    for i in range(15):
        p = one(det, img)
        assert p.reason == REASON_MERGED
        assert not p.stable and p.stable_run == 0, f"frame {i}"
    assert p.frames_seen == 15           # still tracked, just never trusted


def test_HONEST_LIMIT_a_wrapper_the_threshold_cuts_in_two_is_also_refused(capsys):
    """The cost of the merge refusal, stated rather than hidden.

    A specular band across a single pack severs its blob exactly the way a
    flush pair does, and nothing in a silhouette can tell the two apart. The
    module abstains — an amber card and a re-place — instead of guessing. This
    test exists so that cost is a measured fact and not a surprise in the shop.
    """
    ref = empty_mat()
    img = paste(ref, *CENTRE, 140.0, 70.0, 0.0, DARK)
    img = paste(img, CENTRE[0], CENTRE[1], 70.0, 1.4, 90.0, PAPER)  # glare band
    p = one(PlacementDetector(ref), img)
    print(f"\nMEASURED one 140x70 mm pack with a 1.4 mm specular band across it: "
          f"{p.reason}, fill {p.fill_ratio:.3f}, components {p.components}")
    assert p.reason == REASON_MERGED and not p.measurable
    assert p.components >= 2, (
        "the band no longer severs the blob, so this limit has stopped being "
        "real and the docstring must be rewritten"
    )
    # the direction matters: an abstention, never a reading, never a red
    assert p.long_edge_mm is None


# --------------------------------------------------- REFERENCE MAINTENANCE

def test_reference_absorbs_slow_illumination_drift():
    """A dimming tube light must not become an object. 40 grey levels over 200
    empty frames; the maintained reference tracks it, a frozen one does not."""
    ref = empty_mat()
    live = PlacementDetector(ref)
    frozen = PlacementDetector(ref, ref_alpha=0.0)

    last = None
    for i in range(200):
        f = np.clip(ref.astype(np.float32) + 40.0 * (i + 1) / 200.0,
                    0, 255).astype(np.uint8)
        assert live.update(f) == [], f"maintained reference broke at frame {i}"
        last = f

    drift = float(np.mean(np.abs(live.reference.astype(np.float32)
                                 - last.astype(np.float32))))
    print(f"\nMEASURED reference lag after +40 levels over 200 frames: "
          f"{drift:.2f} grey levels ({live.ref_updates} updates)")
    assert drift < 28.0

    spurious = frozen.update(last)
    assert spurious, "frozen reference should have hallucinated a blob at +40"
    assert frozen.ref_updates == 0


def test_reference_is_not_updated_while_an_object_sits_on_the_mat():
    """If the reference blended while an object was present, the object would
    fade into the mat and stop being detected. 80 frames is far past the
    ~35-frame half-life of the blend."""
    ref = empty_mat()
    det = PlacementDetector(ref)
    f = paste(ref, *CENTRE, 150.0, 100.0, 15.0, DARK)
    first = one(det, f)
    for _ in range(79):
        p = one(det, f)
    assert det.ref_updates == 0, "reference learned an object"
    assert p.long_edge_mm == pytest.approx(first.long_edge_mm, abs=1e-9)
    assert np.array_equal(det.reference, ref)


def test_reset_reference_reseats_the_mat():
    ref = empty_mat()
    det = PlacementDetector(ref)
    shifted = np.clip(ref.astype(np.int16) + 60, 0, 255).astype(np.uint8)
    assert det.update(shifted), "a 60-level step must be visible"
    det.reset_reference(shifted)
    assert det.update(shifted) == []


# ------------------------------------------------------- TRACKING / IDENTITY

def test_two_objects_get_distinct_stable_ids():
    ref = empty_mat()
    img = paste(ref, 90.0, 140.0, 100.0, 60.0, 0.0, DARK)
    img = paste(img, 200.0, 300.0, 70.0, 45.0, 40.0, BRIGHT)
    det = PlacementDetector(ref)

    ids = None
    for i in range(6):
        ps = det.update(img)
        assert len(ps) == 2, f"frame {i}: {len(ps)} blobs"
        if ids is None:
            ids = [p.id for p in ps]
        assert [p.id for p in ps] == ids, "ids changed between frames"
    assert len(set(ids)) == 2
    assert all(p.stable for p in ps)

    by_long = sorted(ps, key=lambda p: -p.long_edge_mm)
    assert abs(by_long[0].long_edge_mm - 100.0) < 3.0
    assert abs(by_long[1].long_edge_mm - 70.0) < 3.0


def test_removing_an_object_frees_nothing_and_a_new_one_gets_a_new_id():
    ref = empty_mat()
    det = PlacementDetector(ref)
    a = paste(ref, 90.0, 140.0, 100.0, 60.0, 0.0, DARK)
    first = one(det, a).id
    for _ in range(6):
        det.update(ref)                 # object gone, track ages out
    b = paste(ref, 200.0, 320.0, 100.0, 60.0, 0.0, DARK)
    second = one(det, b).id
    assert second != first, "a fresh placement reused a retired id"


def test_min_area_drops_grain_and_reports_the_drop():
    """A 6x6 mm speck is 36 mm^2, below MIN_AREA_MM2 = 100."""
    ref = empty_mat()
    det = PlacementDetector(ref)
    ps = det.update(paste(ref, *CENTRE, 6.0, 6.0, 0.0, DARK))
    assert ps == []
    assert det.last_rejected_small >= 1
    assert MIN_AREA_MM2 == 100.0


def test_is_deterministic():
    """Same bytes in, same numbers out — the ledger replays on this."""
    ref = empty_mat()
    f = paste(ref, 130.0, 260.0, 140.0, 90.0, 22.0, DARK)
    a = PlacementDetector(ref).update(f)[0]
    b = PlacementDetector(ref).update(f)[0]
    assert (a.long_edge_mm, a.short_edge_mm, a.angle_deg, a.centre_mm) == \
           (b.long_edge_mm, b.short_edge_mm, b.angle_deg, b.centre_mm)


def test_accepts_a_colour_buffer():
    ref = empty_mat()
    f = paste(ref, *CENTRE, 140.0, 90.0, 18.0, DARK)
    mono = PlacementDetector(ref).update(f)[0]
    colour = PlacementDetector(cv2.cvtColor(ref, cv2.COLOR_GRAY2BGR)).update(
        cv2.cvtColor(f, cv2.COLOR_GRAY2BGR))[0]
    assert colour.long_edge_mm == pytest.approx(mono.long_edge_mm, abs=0.01)


def test_throughput_is_real_time(capsys):
    """No model, so this should be a few milliseconds. The bound is loose on
    purpose — this is a smoke test against an accidental O(n^2), not a
    benchmark, and it must not go red because CI was busy."""
    import time
    ref = empty_mat()
    img = paste(ref, 100.0, 150.0, 120.0, 80.0, 20.0, DARK)
    img = paste(img, 200.0, 320.0, 90.0, 55.0, 60.0, BRIGHT)
    det = PlacementDetector(ref)
    det.update(img)                              # warm caches
    t0 = time.perf_counter()
    N = 30
    for _ in range(N):
        det.update(img)
    ms = (time.perf_counter() - t0) * 1000.0 / N
    print(f"\nMEASURED {ms:.2f} ms/frame on the 840x1188 buffer, 2 objects "
          f"({1000.0/ms:.0f} fps single-threaded)")
    assert ms < 200.0


def test_clock_is_injected_never_read_from_the_wall():
    ref = empty_mat()
    det = PlacementDetector(ref, clock=VirtualClock(step_ms=100))
    f = paste(ref, *CENTRE, 100.0, 60.0, 0.0, DARK)
    t1 = one(det, f).t_iso
    t2 = one(det, f).t_iso
    assert t1 == "2026-08-29T00:00:00.000+00:00"
    assert t2 == "2026-08-29T00:00:00.100+00:00"
    assert PlacementDetector(ref).update(f)[0].t_iso is None


# ------------------------------------------- END TO END THROUGH THE REAL PLANE

def test_ACCEPTANCE_end_to_end_through_a_tilted_camera():
    """The whole chain: a tilted synthetic camera -> ArUco -> homography ->
    rectify -> placement. Nothing is measured in an idealised buffer here; the
    object goes through the same resampling every real frame does.
    """
    tilt = (3.0, 2.0)
    frame_empty, dst = synth_frame(px_per_mm=4.0, tilt=tilt)

    mat = render_takhti(4.0)
    h, w = mat.shape
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)
    M = cv2.getPerspectiveTransform(src, dst)

    eng = PlaneEngine()
    lock_e = eng.detect(frame_empty)
    assert lock_e.locked, lock_e.reason
    ref = eng.rectify(frame_empty, lock_e.H)
    det = PlacementDetector(ref)
    assert det.update(ref) == [], "the empty rectified mat must be empty"

    worst = 0.0
    for L, S, deg in [(210.0, 30.0, 0.0), (150.0, 100.0, 45.0),
                      (120.0, 80.0, 30.0), (60.0, 40.0, 70.0)]:
        # draw the object in MAT pixel space (4 px/mm), then project it through
        # exactly the geometry synth_frame used
        obj_mat = mat.copy()
        poly = (box_mm(*CENTRE, L, S, deg) * 4.0)
        cv2.fillConvexPoly(obj_mat, np.rint(poly).astype(np.int32), 40)
        frame = frame_empty.copy()
        warped = cv2.warpPerspective(obj_mat, M, frame.shape[::-1], borderValue=235)
        mask = cv2.warpPerspective(np.full_like(mat, 255), M,
                                   frame.shape[::-1], borderValue=0)
        frame[mask > 128] = warped[mask > 128]

        lock = eng.detect(frame)
        assert lock.locked, lock.reason
        p = one(PlacementDetector(ref), eng.rectify(frame, lock.H))
        assert p.measurable, p.reason
        err = abs(p.long_edge_mm - L)
        worst = max(worst, err)
        assert err < 3.0, (
            f"{L}x{S}mm @ {deg}deg through tilt {tilt}: "
            f"measured {p.long_edge_mm:.2f}mm (err {err:+.2f}mm)"
        )
    print(f"\nMEASURED end-to-end through tilt {tilt}: "
          f"worst long-edge error {worst:.3f} mm")


def test_border_refusal_survives_the_real_rectification():
    """An object hanging off the mat edge, seen through a tilted camera, is
    still refused rather than measured short."""
    frame_empty, dst = synth_frame(px_per_mm=4.0, tilt=(3.0, 2.0))
    mat = render_takhti(4.0)
    h, w = mat.shape
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)
    M = cv2.getPerspectiveTransform(src, dst)

    eng = PlaneEngine()
    lock = eng.detect(frame_empty)
    ref = eng.rectify(frame_empty, lock.H)

    obj_mat = mat.copy()
    poly = box_mm(6.0, MAT_H_MM / 2, 100.0, 70.0, 0.0) * 4.0
    cv2.fillConvexPoly(obj_mat, np.rint(poly).astype(np.int32), 40)
    frame = frame_empty.copy()
    warped = cv2.warpPerspective(obj_mat, M, frame.shape[::-1], borderValue=235)
    m = cv2.warpPerspective(np.full_like(mat, 255), M, frame.shape[::-1],
                            borderValue=0)
    frame[m > 128] = warped[m > 128]

    lock2 = eng.detect(frame)
    assert lock2.locked, lock2.reason
    ps = PlacementDetector(ref).update(eng.rectify(frame, lock2.H))
    assert ps, "the overhanging object vanished entirely"
    assert any(p.reason == REASON_BORDER and p.long_edge_mm is None for p in ps), \
        [(p.reason, p.long_edge_mm) for p in ps]


def test_border_refusal_is_a_real_edge_test_not_a_blanket_margin(capsys):
    """BEHAVIOUR, not the constant. Nothing here reads BORDER_PX to decide what
    to expect, so inflating the constant into a fat no-go strip fails the test
    instead of moving its goalposts.

    Two facts, both in absolute millimetres:

      straddling   an object hanging off the mat is REFUSED, and the reading it
                   would otherwise have produced is measured here so the size of
                   the averted mistake is on the record: 5 / 10 / 20 / 30 mm of
                   overhang read 55.2 / 50.2 / 40.0 / 30.1 mm across a 60 mm
                   packet — an undercharge of up to half the item.

      just inside  an object clearing the buffer edge by 1.4 mm is MEASURED, to
                   0.11 mm. A margin wide enough to be comfortable would have
                   refused it, and refusing a legitimately placed packet is the
                   failure this test exists to make impossible.
    """
    ref = empty_mat()
    x_mm, y_mm = 60.0, 90.0          # 60 mm across the border, 90 mm along it

    print("\nMEASURED border behaviour, 60 mm packet against the left edge")
    for hang_mm in (5.0, 10.0, 20.0, 30.0):
        det = PlacementDetector(ref)
        p = one(det, paste(ref, -hang_mm + x_mm / 2, MAT_H_MM / 2,
                           y_mm, x_mm, 90.0, DARK))
        cnts, _ = cv2.findContours(det.last_mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
        w, h = cv2.minAreaRect(max(cnts, key=cv2.contourArea))[1]
        would_be = min(w, h) / PX_PER_MM_X
        print(f"  overhang {hang_mm:>4.0f} mm -> {p.reason:<15} "
              f"(a measurement here would have read {would_be:5.2f} mm "
              f"of a {x_mm:.0f} mm packet)")
        assert p.measurable is False, f"a clipped object was measured at {hang_mm} mm"
        assert p.reason == REASON_BORDER
        assert p.long_edge_mm is None and p.area_mm2 is None
        assert would_be < x_mm - 3.0, (
            "the clipped blob no longer reads short, so this scene has stopped "
            "demonstrating the error the refusal prevents"
        )

    # Walk the object in from the edge one buffer pixel at a time and record
    # where the answer flips. `edge_px` is the left-most column the mask
    # actually occupies, read off last_mask — a fact about the image, not about
    # the constant.
    ladder = []
    for left_px in range(0, 13):
        det = PlacementDetector(ref)
        p = one(det, paste(ref, left_px / PX_PER_MM_X + x_mm / 2, MAT_H_MM / 2,
                           y_mm, x_mm, 90.0, DARK))
        cnts, _ = cv2.findContours(det.last_mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
        big = max(cnts, key=cv2.contourArea).reshape(-1, 2)
        ladder.append((left_px, int(big[:, 0].min()), p.measurable,
                       p.short_edge_mm))
    print("  (left edge px, mask starts at col, measured, mm across)")
    for row in ladder:
        print(f"    {row[0]:>3}  {row[1]:>3}  {str(row[2]):>5}  "
              + ("-" if row[3] is None else f"{row[3]:.2f}"))

    refused = [r for r in ladder if not r[2]]
    measured = [r for r in ladder if r[2]]
    assert refused and measured, "the ladder never crossed the refusal boundary"

    # 1. Anything the mat clipped — the mask running into column 0 — is refused.
    assert all(not r[2] for r in ladder if r[1] == 0), (
        "an object whose contour reaches the buffer edge was measured"
    )
    # 2. Once the mask clears the edge at all, the object is measured. The first
    #    such sample sits 3 px (1.06 mm) in, so the refusal cannot be a margin
    #    any wider than that.
    first = measured[0]
    assert first[1] <= 3, (
        f"the nearest measured object had to clear the edge by {first[1]} px "
        f"({first[1] / PX_PER_MM_X:.2f} mm); that is a blanket margin, not a "
        f"border test"
    )
    assert abs(first[3] - x_mm) < 3.0, (
        f"the object nearest the edge measured {first[3]:.2f} mm, not {x_mm}"
    )
    # 3. And the flip happens once, in the right direction.
    assert [r[2] for r in ladder] == sorted(r[2] for r in ladder), \
        "measurability is not monotone as the object moves inward"

    # The constant may only take values this behaviour can actually justify.
    assert 0 <= BORDER_PX <= first[1], (
        f"BORDER_PX={BORDER_PX} is outside the window the ladder pins it to"
    )
