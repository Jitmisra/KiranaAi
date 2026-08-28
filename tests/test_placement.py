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
    BORDER_PX, MIN_AREA_MM2, REASON_BORDER, REASON_OK, STABLE_FRAMES,
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


def test_border_px_constant_is_a_real_margin_not_a_disguised_one():
    """Pins BORDER_PX small: a blanket margin would silently refuse legitimate
    placements near the edge and look like a border test."""
    assert 0 <= BORDER_PX <= 4
    assert BORDER_PX / PX_PER_MM_X < 2.0
