"""S6 CHILLA acceptance tests.

Three groups:
  1. THE OPTICAL BUDGET   — the reference string is not in the signal, computed.
  2. SCREEN DETECTION     — deterministic CV on the rectified mat, no model.
  3. THE LEDGER MATCH     — composite key (amount, time), abstention-first, and
                            a MEASURED false-accept rate over a synthetic day.

Every number this file reports is produced by running code in this file.
"""
from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from gawaah.chilla import (
    AMBER_STALE, AMBIGUOUS, CHILLAR_SPACE, DEFAULT_WINDOW_S, HERO_AMOUNT,
    LIGHT_FOR_VERDICT, MATCHED, NEVER_READ, NO_MATCH, PLACEMENT_BOX_MM,
    REFERENCE_STRING, SCREEN_TIMESTAMP, VERDICTS, ChillaError, ChillaRefusal,
    LedgerMatcher, Mirror, MirrorRow, MmRect, ScreenDetection, ScreenFinder,
    any_collision_risk, buffer_to_mm, collision_risk, legibility,
    max_payments_for_risk, read_reference_string, read_screen_timestamp,
)
from gawaah.clock import VirtualClock
from gawaah.ledger import Ledger, verify
from gawaah.money import MoneyError
from gawaah.takhti import BUF_H, BUF_W, PX_PER_MM, PlaneEngine, mm_to_buffer
from tests.test_plane import synth_frame


# =============================================================================
# helpers — a photometric phantom of a phone on the mat
# =============================================================================

def empty_mat(exposure: float = 0.78, noise: float = 0.0, seed: int = 0) -> np.ndarray:
    """A REF_EMPTY_MAT buffer produced through the real plane pipeline.

    The printed mat is paper white; `exposure` scales it to what a counter
    camera actually returns (~198 grey), which is what leaves headroom for an
    emissive screen to be BRIGHTER than the mat.
    """
    frame, _ = synth_frame(tilt=(2, 1))
    eng = PlaneEngine()
    lock = eng.detect(frame)
    assert lock.locked, lock.reason
    rect = eng.rectify(frame, lock.H)
    out = rect.astype(np.float64) * exposure
    if noise:
        out = out + np.random.default_rng(seed).normal(0.0, noise, out.shape)
    return np.clip(out, 0, 255).astype(np.uint8)


def quad_mm(cx: float, cy: float, w: float, h: float, ang_deg: float) -> np.ndarray:
    """Corners TL,TR,BR,BL in mm for a rect centred at (cx,cy), long axis `ang`
    degrees off the mat's +y."""
    a = math.radians(ang_deg)
    v = np.array([math.sin(a), math.cos(a)])      # along the long side
    u = np.array([math.cos(a), -math.sin(a)])     # along the short side
    c = np.array([cx, cy], float)
    return np.array([c - u * w / 2 - v * h / 2, c + u * w / 2 - v * h / 2,
                     c + u * w / 2 + v * h / 2, c - u * w / 2 + v * h / 2])


def _phantom(wp: int, hp: int, luma: int, bar: int) -> np.ndarray:
    """A luminance phantom: a lit panel with three darker bars of UI furniture.

    Deliberately NOT a rendering of any payment UI and it carries no payment
    strings. CHILLA's detector is purely photometric and geometric, so anything
    more would be dressing a test, not testing the detector.
    """
    img = np.full((hp, wp), luma, np.uint8)
    y = int(hp * 0.28)
    for fw, ft in ((0.55, 0.10), (0.35, 0.045), (0.45, 0.035)):
        bw, bh = int(wp * fw), max(2, int(hp * ft))
        x0 = (wp - bw) // 2
        img[y:y + bh, x0:x0 + bw] = bar
        y += bh + int(hp * 0.05)
    return img


def place_phone(buf: np.ndarray, cx: float, cy: float, *, w: float = 70.0,
                h: float = 150.0, ang: float = 0.0, luma: int = 248,
                bar: int = 200) -> np.ndarray:
    """Composite the phantom onto a rectified buffer at a known mm pose."""
    q = mm_to_buffer(quad_mm(cx, cy, w, h, ang)).astype(np.float32)
    wp = max(8, int(round(w * PX_PER_MM)))
    hp = max(8, int(round(h * PX_PER_MM)))
    src = np.array([[0, 0], [wp, 0], [wp, hp], [0, hp]], np.float32)
    M = cv2.getPerspectiveTransform(src, q)
    p = _phantom(wp, hp, luma, bar)
    warped = cv2.warpPerspective(p, M, (BUF_W, BUF_H))
    mask = cv2.warpPerspective(np.full_like(p, 255), M, (BUF_W, BUF_H))
    out = buf.copy()
    out[mask > 128] = warped[mask > 128]
    return out


@pytest.fixture(scope="module")
def ref_mat() -> np.ndarray:
    return empty_mat()


@pytest.fixture()
def finder(ref_mat) -> ScreenFinder:
    return ScreenFinder(ref_mat)


# =============================================================================
# 1. THE OPTICAL BUDGET
# =============================================================================

def test_MEASURED_reference_string_is_below_nyquist():
    """The finding that drives the whole design, recomputed rather than quoted."""
    L = REFERENCE_STRING
    assert L.size_mm == pytest.approx(0.19)
    assert L.px_per_mm == pytest.approx(2.8284, abs=1e-3)
    assert L.size_px == pytest.approx(0.5374, abs=1e-3), L.explain()
    assert not L.readable
    assert L.shortfall_x == pytest.approx(3.721, abs=1e-2)
    print(f"\n[MEASURED] {L.explain()}")


def test_MEASURED_two_times_super_resolution_still_does_not_reach_the_floor():
    """2x SR gets the stroke to 1.07 px. The floor is 2 px. Nothing is recovered."""
    L = REFERENCE_STRING
    assert not L.readable_with_2x_sr
    doubled = L.size_px * 2.0
    assert doubled == pytest.approx(1.0748, abs=1e-3)
    print(f"[MEASURED] 2x super-resolution: {doubled:.4f} px, floor {L.nyquist_px} px")


def test_MEASURED_hero_amount_clears_the_floor():
    """The 40sp amount is the only field CHILLA is entitled to use."""
    L = HERO_AMOUNT
    assert L.readable
    assert L.size_px == pytest.approx(12.586, abs=1e-2), L.explain()
    print(f"[MEASURED] {L.explain()}")


def test_screen_timestamp_is_the_same_photon_problem():
    assert not SCREEN_TIMESTAMP.readable
    assert SCREEN_TIMESTAMP.size_px == pytest.approx(REFERENCE_STRING.size_px)


def test_legibility_rejects_impossible_geometry():
    with pytest.raises(ChillaError):
        legibility("nothing", 0.0)
    with pytest.raises(ChillaError):
        legibility("nothing", 1.0, px_per_mm=0.0)


def test_reading_the_reference_string_is_a_structural_refusal():
    """Reaching for the UTR must raise with the arithmetic attached, not return
    a plausible string. NO FORGERY, NO GUESSING."""
    with pytest.raises(ChillaRefusal) as e:
        read_reference_string(object())
    assert "not present in the signal" in str(e.value)
    assert "0.54 px" in str(e.value)
    with pytest.raises(ChillaRefusal):
        read_screen_timestamp(object())


def test_the_never_read_list_is_explicit():
    for field in ("reference_string", "utr", "rrn", "screen_timestamp"):
        assert field in NEVER_READ


# =============================================================================
# 2. SCREEN DETECTION ON THE RECTIFIED MAT
# =============================================================================

def test_no_reference_abstains_loudly(ref_mat):
    f = ScreenFinder()
    assert not f.has_reference
    d = f.detect(ref_mat)
    assert d.found is False and d.reason == "no_reference"


def test_reference_must_be_the_rectified_buffer():
    f = ScreenFinder()
    with pytest.raises(ChillaError) as e:
        f.set_reference(np.full((480, 640), 200, np.uint8))
    assert "rectified" in str(e.value)


def test_empty_mat_against_itself_finds_nothing(finder, ref_mat):
    d = finder.detect(ref_mat)
    assert d.found is False and d.reason == "no_bright_region"


def test_ACCEPTANCE_finds_the_screen_and_returns_its_mm_rect(finder, ref_mat):
    cur = place_phone(ref_mat, 148.5, 210.0, w=70.0, h=150.0)
    d = finder.detect(cur)
    assert d.found, d.reason
    assert d.reason == "screen_found"
    r = d.rect_mm
    assert isinstance(r, MmRect)
    assert r.cx_mm == pytest.approx(148.5, abs=1.0)
    assert r.cy_mm == pytest.approx(210.0, abs=1.0)
    assert r.w_mm == pytest.approx(70.0, abs=1.5)
    assert r.h_mm == pytest.approx(150.0, abs=1.5)
    assert r.area_mm2 == pytest.approx(70.0 * 150.0, rel=0.03)
    assert d.delta_luma > 30.0            # genuinely emissive
    assert d.rectangularity > 0.95
    assert d.in_placement_box is True


@pytest.mark.parametrize("ang", [-25.0, -8.0, 0.0, 12.0, 30.0])
def test_finds_the_screen_at_any_rotation(finder, ref_mat, ang):
    cur = place_phone(ref_mat, 148.5, 210.0, ang=ang)
    d = finder.detect(cur)
    assert d.found, f"{ang} deg: {d.reason}"
    assert d.rect_mm.angle_deg == pytest.approx(ang, abs=2.0)
    assert d.rect_mm.w_mm == pytest.approx(70.0, abs=1.5)
    assert d.rect_mm.h_mm == pytest.approx(150.0, abs=1.5)


def test_MEASURED_geometric_accuracy_over_a_placement_sweep(finder, ref_mat):
    """How well does the mm rect actually land? Reported, not assumed."""
    poses = [(cx, cy, w, h, a)
             for cx, cy in ((100.0, 160.0), (148.5, 210.0), (200.0, 260.0))
             for w, h in ((62.0, 134.0), (70.0, 150.0), (78.0, 166.0))
             for a in (0.0, 15.0, -20.0)]
    ctr_err, size_err, ang_err = [], [], []
    for cx, cy, w, h, a in poses:
        d = finder.detect(place_phone(ref_mat, cx, cy, w=w, h=h, ang=a))
        assert d.found, f"{(cx, cy, w, h, a)}: {d.reason}"
        r = d.rect_mm
        ctr_err.append(math.hypot(r.cx_mm - cx, r.cy_mm - cy))
        size_err.append(max(abs(r.w_mm - w), abs(r.h_mm - h)))
        ang_err.append(abs(r.angle_deg - a))
    print(f"\n[MEASURED] screen rect over {len(poses)} placements: "
          f"centre mean {np.mean(ctr_err):.3f} mm / max {np.max(ctr_err):.3f} mm; "
          f"side mean {np.mean(size_err):.3f} mm / max {np.max(size_err):.3f} mm; "
          f"angle max {np.max(ang_err):.3f} deg")
    assert np.max(ctr_err) < 1.5
    assert np.max(size_err) < 1.5
    assert np.max(ang_err) < 2.0


def test_a_dark_object_on_the_mat_is_not_a_screen(finder, ref_mat):
    """A phone-shaped dark slab differs from the reference just as much, but it
    is not emissive. absdiff alone would accept it; the photometry gate does not."""
    cur = place_phone(ref_mat, 148.5, 210.0, luma=40, bar=30)
    d = finder.detect(cur)
    assert d.found is False and d.reason == "not_emissive"
    assert d.delta_luma < 0.0


def test_two_screens_on_the_mat_abstain_rather_than_pick_one(finder, ref_mat):
    cur = place_phone(ref_mat, 90.0, 140.0)
    cur = place_phone(cur, 210.0, 290.0)
    d = finder.detect(cur)
    assert d.found is False and d.reason == "ambiguous_two_bright_quads"


def test_a_square_bright_patch_is_rejected_on_aspect(finder, ref_mat):
    cur = place_phone(ref_mat, 148.5, 210.0, w=140.0, h=150.0)
    d = finder.detect(cur)
    assert d.found is False and d.reason == "aspect_out_of_range"


def test_a_small_bright_object_is_rejected(finder, ref_mat):
    cur = place_phone(ref_mat, 148.5, 210.0, w=25.0, h=30.0)
    d = finder.detect(cur)
    assert d.found is False
    assert d.reason in {"no_bright_region", "all_regions_too_small", "too_small"}


def test_global_exposure_shift_forces_a_rebaseline_not_a_guess(finder, ref_mat):
    """takePhoto() mutes the track; AE/AWB reconverge; every pixel 'changed'.
    That is a re-baseline, and saying so is the only honest answer."""
    brighter = np.clip(ref_mat.astype(np.float64) * 1.2, 0, 255).astype(np.uint8)
    d = finder.detect(brighter)
    assert d.found is False and d.reason == "global_illumination_shift"


def test_clearing_the_reference_disarms_detection(finder, ref_mat):
    finder.clear_reference()
    assert not finder.has_reference
    d = finder.detect(place_phone(ref_mat, 148.5, 210.0))
    assert d.reason == "no_reference"


def test_survives_sensor_noise():
    ref = empty_mat(noise=4.0, seed=1)
    f = ScreenFinder(ref)
    cur = place_phone(empty_mat(noise=4.0, seed=2), 148.5, 210.0)
    d = f.detect(cur)
    assert d.found, d.reason
    assert d.rect_mm.w_mm == pytest.approx(70.0, abs=1.5)
    assert d.rect_mm.h_mm == pytest.approx(150.0, abs=1.5)


def test_placement_box_is_reported_but_never_gates(finder, ref_mat):
    x0, y0, x1, y1 = PLACEMENT_BOX_MM
    inside = finder.detect(place_phone(ref_mat, 148.5, 210.0))
    outside = finder.detect(place_phone(ref_mat, 60.0, 100.0))
    assert inside.found and inside.in_placement_box is True
    assert outside.found and outside.in_placement_box is False


def test_buffer_to_mm_inverts_mm_to_buffer():
    pts = np.array([[0.0, 0.0], [297.0, 420.0], [148.5, 210.0]])
    back = buffer_to_mm(mm_to_buffer(pts))
    assert back == pytest.approx(pts, abs=1e-9)


def test_PRIVACY_detection_returns_geometry_only_never_pixels(finder, ref_mat):
    """Invariant 4 / PAKKA privacy: a third party's screen contents must not be
    able to leave through this API. Every array on the result is 4x2 of corners."""
    d = finder.detect(place_phone(ref_mat, 148.5, 210.0))
    assert d.found
    for name, value in vars(d).items():
        if isinstance(value, np.ndarray):
            assert value.shape == (4, 2), f"{name} carries {value.shape}, not corners"
    audit = d.as_dict()
    assert not any(isinstance(v, np.ndarray) for v in audit.values())
    assert "crop" not in audit and "pixels" not in audit


def test_detection_of_a_wrong_sized_buffer_abstains(finder):
    d = finder.detect(np.full((100, 100), 200, np.uint8))
    assert d.found is False and d.reason == "buffer_shape_mismatch"


# =============================================================================
# 3. THE LEDGER MATCH
# =============================================================================

T0 = 1_772_000_000          # an arbitrary fixed unix second; nothing depends on it
AMOUNT = 21437              # Rs 214.37 — the last two paise are the CHILLAR nonce


def mirror_of(*rows: MirrorRow, fetched_at: int | None = T0) -> Mirror:
    return Mirror(rows, fetched_at=fetched_at)


def row(pid: str, amount: int, at: int, status: str = "captured") -> MirrorRow:
    return MirrorRow(pid, amount, at, status=status, session_id="sess_" + pid)


def test_ACCEPTANCE_exact_amount_in_window_matches():
    m = mirror_of(row("p1", AMOUNT, T0 - 30))
    res = LedgerMatcher(m, 180).match(AMOUNT, T0)
    assert res.verdict == MATCHED
    assert res.matched is True
    assert res.payment is not None and res.payment.payment_id == "p1"
    assert len(res.candidates) == 1
    assert res.light == "AMBER"          # corroboration is not settlement
    assert "exactly one" in res.reason


def test_ACCEPTANCE_off_by_one_paisa_is_no_match_and_amber():
    """CHILLAR's whole point: one paisa is a whole key, not a rounding."""
    m = mirror_of(row("p1", AMOUNT, T0 - 30))
    lm = LedgerMatcher(m, 180)
    for delta in (-1, +1):
        res = lm.match(AMOUNT + delta, T0)
        assert res.verdict == NO_MATCH, delta
        assert res.light == "AMBER"
        assert res.candidates == ()
        assert "not an accusation" in res.reason
        assert res.verdict not in ("FRAUD", "RED")


def test_ACCEPTANCE_two_identical_amounts_in_window_are_ambiguous():
    m = mirror_of(row("p1", AMOUNT, T0 - 30), row("p2", AMOUNT, T0 + 40))
    res = LedgerMatcher(m, 180).match(AMOUNT, T0)
    assert res.verdict == AMBIGUOUS
    assert res.matched is False
    assert res.payment is None
    assert {c.payment_id for c in res.candidates} == {"p1", "p2"}
    assert res.light == "AMBER"


def test_ACCEPTANCE_stale_mirror_is_amber_stale_even_when_a_match_exists():
    m = mirror_of(row("p1", AMOUNT, T0 - 30))
    lm = LedgerMatcher(m, 180, stale_threshold_s=60.0)
    fresh = lm.match(AMOUNT, T0, mirror_age_s=59.0)
    stale = lm.match(AMOUNT, T0, mirror_age_s=61.0)
    assert fresh.verdict == MATCHED
    assert stale.verdict == AMBER_STALE
    assert stale.light == "AMBER"
    assert "stale mirror cannot corroborate" in stale.reason


def test_a_never_fetched_mirror_is_infinitely_stale():
    m = Mirror((row("p1", AMOUNT, T0 - 30),), fetched_at=None)
    assert m.age_s(T0) == math.inf
    res = LedgerMatcher(m, 180).match(AMOUNT, T0, mirror_age_s=m.age_s(T0))
    assert res.verdict == AMBER_STALE
    assert res.as_dict()["mirror_age_ms"] == -1


def test_mirror_age_helper_is_never_negative():
    m = mirror_of(fetched_at=T0)
    assert m.age_s(T0 - 500) == 0.0
    assert m.age_s(T0 + 12) == 12.0


def test_INVARIANT_no_verdict_is_ever_green_or_red():
    """Invariant 2: nothing in CHILLA turns a light green, and rule 7: a screen
    we cannot corroborate is amber, never a fraud accusation."""
    assert set(LIGHT_FOR_VERDICT) == set(VERDICTS)
    assert set(LIGHT_FOR_VERDICT.values()) == {"AMBER"}
    m = mirror_of(row("p1", AMOUNT, T0 - 30), row("p2", AMOUNT, T0 + 10),
                  row("p3", AMOUNT + 100, T0 + 20))
    lm = LedgerMatcher(m, 180)
    seen = set()
    for amount, ts, age in ((AMOUNT, T0, 0.0), (AMOUNT + 7, T0, 0.0),
                            (AMOUNT, T0, 999.0), (AMOUNT, None, 0.0),
                            (AMOUNT + 100, T0, 0.0)):
        res = lm.match(amount, ts, mirror_age_s=age)
        seen.add(res.verdict)
        assert res.light == "AMBER"
        assert res.is_amber
        assert res.verdict in VERDICTS
    assert seen == set(VERDICTS), f"battery did not exercise every verdict: {seen}"


def test_unknown_capture_time_is_amber_not_a_guess():
    m = mirror_of(row("p1", AMOUNT, T0))
    res = LedgerMatcher(m, 180).match(AMOUNT, None)
    assert res.verdict == NO_MATCH
    assert res.screen_ts is None
    assert "below Nyquist" in res.reason


def test_a_failed_payment_never_corroborates():
    m = mirror_of(row("p1", AMOUNT, T0 - 5, status="failed"))
    res = LedgerMatcher(m, 180).match(AMOUNT, T0)
    assert res.verdict == NO_MATCH
    assert res.n_in_window == 0


@pytest.mark.parametrize("offset,expect", [
    (-180, MATCHED), (-179, MATCHED), (0, MATCHED), (180, MATCHED),
    (-181, NO_MATCH), (181, NO_MATCH), (10_000, NO_MATCH),
])
def test_the_window_is_symmetric_and_inclusive(offset, expect):
    m = mirror_of(row("p1", AMOUNT, T0 + offset))
    assert LedgerMatcher(m, 180).match(AMOUNT, T0).verdict == expect


def test_window_seconds_must_be_positive():
    with pytest.raises(ChillaError):
        LedgerMatcher(mirror_of(), 0)


def test_negative_mirror_age_is_rejected():
    with pytest.raises(ChillaError):
        LedgerMatcher(mirror_of(), 180).match(AMOUNT, T0, mirror_age_s=-1.0)


def test_MONEY_a_float_amount_can_never_enter_the_match():
    """Invariant 1. Rs 214.37 as a float is not money and never becomes money."""
    lm = LedgerMatcher(mirror_of(row("p1", AMOUNT, T0)), 180)
    for bad in (214.37, 21437.0, True, "21437", None):
        with pytest.raises(MoneyError):
            lm.match(bad, T0)
    with pytest.raises(MoneyError):
        MirrorRow("p", 214.37, T0)


def test_iso_timestamps_from_the_clock_are_accepted():
    clk = VirtualClock(start="2026-08-29T10:00:00.000+00:00", step_ms=1000)
    t_iso = clk.now_iso()
    m = Mirror((MirrorRow("p1", AMOUNT, t_iso),), fetched_at=t_iso)
    res = LedgerMatcher(m, 180).match(AMOUNT, clk.now_iso())
    assert res.verdict == MATCHED


def test_bad_timestamps_are_rejected_not_coerced():
    lm = LedgerMatcher(mirror_of(), 180)
    for bad in ("yesterday", "2026-13-99", float("nan")):
        with pytest.raises(ChillaError):
            lm.match(AMOUNT, bad)
    with pytest.raises(ChillaError):
        lm.match(AMOUNT, True)


def test_razorpay_payment_entity_adapts_into_a_mirror_row():
    """Shaped exactly as gawaah.rzp_sim's payment view emits it."""
    entity = {
        "id": "pay_00000000000001", "entity": "payment", "amount": AMOUNT,
        "currency": "INR", "status": "captured", "method": "upi",
        "notes": {"session_id": "sess_7", "counter_id": "c1"},
        "created_at": T0, "acquirer_data": {"rrn": "123456789012"},
    }
    r = MirrorRow.from_razorpay(entity)
    assert r.payment_id == "pay_00000000000001"
    assert r.amount_paise == AMOUNT and isinstance(r.amount_paise, int)
    assert r.session_id == "sess_7" and r.created_at == T0
    coll = {"entity": "collection", "count": 1, "items": [entity]}
    m = Mirror.from_razorpay_collection(coll, fetched_at=T0)
    assert len(m) == 1
    assert LedgerMatcher(m, 180).match(AMOUNT, T0).verdict == MATCHED
    # the rrn came along in the entity and CHILLA still does not touch it
    assert "rrn" not in r.as_dict()


def test_mirror_rows_are_sorted_and_junk_rows_are_rejected():
    m = Mirror([row("b", 100, T0 + 50), row("a", 100, T0)])
    assert [r.payment_id for r in m] == ["a", "b"]
    with pytest.raises(ChillaError):
        Mirror([{"nonsense": 1}])


# ------------------------------------------------------------ collision risk

def test_MEASURED_collision_risk_matches_a_monte_carlo():
    """`collision_risk(n)` is the worst case: every other payment in the window
    shares our rupee amount, so only the CHILLAR paise nonce separates them.
    Simulate exactly that and check the closed form."""
    rng = np.random.default_rng(20260829)
    trials = 200_000
    rows = []
    for n in (1, 2, 5, 10, 25):
        others = rng.integers(1, CHILLAR_SPACE + 1, size=(trials, max(0, n - 1)))
        mine = rng.integers(1, CHILLAR_SPACE + 1, size=(trials, 1))
        hit = float((others == mine).any(axis=1).mean()) if n > 1 else 0.0
        pred = collision_risk(n)
        rows.append((n, pred, hit))
        assert hit == pytest.approx(pred, abs=0.004), (n, pred, hit)
    print("\n[MEASURED] CHILLAR collision risk, k=99, "
          f"{trials} Monte-Carlo trials per n:")
    for n, pred, hit in rows:
        print(f"    n={n:>3} in window: closed form {pred*100:6.3f}%   "
              f"simulated {hit*100:6.3f}%")


def test_collision_risk_is_monotone_and_bounded():
    prev = -1.0
    for n in range(1, 60):
        r = collision_risk(n)
        assert 0.0 <= r < 1.0
        assert r > prev or n == 1
        prev = r
    assert collision_risk(1) == 0.0
    assert collision_risk(2) == pytest.approx(1.0 / CHILLAR_SPACE)
    with pytest.raises(ChillaError):
        collision_risk(5, key_space=0)


def test_MEASURED_birthday_form_and_the_window_occupancy_budget():
    """Two different questions, both reported: 'does anything collide with ME'
    and 'does anything in the window collide with anything'."""
    pairs = [(n, collision_risk(n), any_collision_risk(n)) for n in (2, 5, 10, 20)]
    print("\n[MEASURED] window occupancy vs collision, CHILLAR key space "
          f"k={CHILLAR_SPACE}:")
    for n, mine, anyc in pairs:
        print(f"    n={n:>3}: P(collides with mine) {mine*100:6.3f}%   "
              f"P(any pair collides) {anyc*100:6.3f}%")
    for n, mine, anyc in pairs:
        assert anyc >= mine
    budgets = {t: max_payments_for_risk(t) for t in (0.01, 0.02, 0.05, 0.10)}
    print(f"[MEASURED] max payments in window per risk budget: {budgets}")
    assert budgets[0.01] == 1 and budgets[0.05] == 6 and budgets[0.10] == 11
    assert collision_risk(budgets[0.05]) <= 0.05 < collision_risk(budgets[0.05] + 1)
    with pytest.raises(ChillaError):
        max_payments_for_risk(1.5)


def test_collision_risk_is_exposed_on_every_result():
    rows = [row(f"p{i}", 10_000 + i, T0 - 100 + i * 10) for i in range(8)]
    res = LedgerMatcher(mirror_of(*rows), 180).match(10_003, T0)
    assert res.verdict == MATCHED
    assert res.n_in_window == 8
    assert res.collision_risk == pytest.approx(collision_risk(8))
    assert res.key_space == CHILLAR_SPACE
    assert res.as_dict()["collision_risk"] == f"{collision_risk(8):.6f}"


# ------------------------------------------------------------------- ledger

def test_every_verdict_appends_one_verifiable_ledger_line(tmp_path):
    led = Ledger(tmp_path / "audit.jsonl")
    clk = VirtualClock(start="2026-08-29T12:00:00.000+00:00", step_ms=250)
    lm = LedgerMatcher(mirror_of(row("p1", AMOUNT, T0)), 180, ledger=led, clock=clk)
    lm.match(AMOUNT, T0)
    lm.match(AMOUNT + 1, T0)
    lm.match(AMOUNT, T0, mirror_age_s=900.0)
    assert led.count == 3
    ok, n, head, err = verify(tmp_path / "audit.jsonl")
    assert ok and n == 3 and err is None
    recs = list(led.read())
    assert [r["verdict"] for r in recs] == [MATCHED, NO_MATCH, AMBER_STALE]
    assert all(r["module"] == "chilla" for r in recs)
    assert all(r["light"] == "AMBER" for r in recs)


def test_the_ledger_line_cannot_leak_what_we_never_read(tmp_path):
    led = Ledger(tmp_path / "audit.jsonl")
    lm = LedgerMatcher(mirror_of(row("p1", AMOUNT, T0)), 180,
                       ledger=led, clock=VirtualClock())
    lm.match(AMOUNT, T0)
    rec = next(iter(led.read()))
    for banned in NEVER_READ:
        assert banned not in rec, f"{banned} must never reach the audit log"
    assert "crop" not in rec and "image" not in rec


def test_matching_without_a_ledger_is_silent():
    lm = LedgerMatcher(mirror_of(row("p1", AMOUNT, T0)), 180)
    assert lm.match(AMOUNT, T0).verdict == MATCHED   # no ledger, no exception


# =============================================================================
# THE MEASURED EXPERIMENT — false accepts over a synthetic trading day
# =============================================================================

DAY_OPEN = 1_772_155_800        # 09:00 local, fixed
DAY_SECONDS = 12 * 3600
N_PAYMENTS = 480
REPLAYS_PER_PAYMENT = 30
SEED = 20260829

# Realistic kirana totals: shopkeepers round, so multiples of Rs 5 from 20 to
# 500, weighted towards small baskets (weight proportional to 1/total).
PRICE_GRID = np.arange(20, 505, 5, dtype=np.int64)
PRICE_W = (1.0 / PRICE_GRID.astype(np.float64))
PRICE_W /= PRICE_W.sum()


def _synthetic_day(rng: np.random.Generator, *, chillar: bool):
    """One trading day of settled payments. The two arms share the same arrival
    times and the same RUPEE draws; they differ ONLY in the paise nonce, so the
    comparison isolates CHILLAR."""
    times = np.sort(rng.integers(DAY_OPEN, DAY_OPEN + DAY_SECONDS, size=N_PAYMENTS))
    rupees = rng.choice(PRICE_GRID, size=N_PAYMENTS, p=PRICE_W)
    nonce = (rng.integers(1, CHILLAR_SPACE + 1, size=N_PAYMENTS)
             if chillar else np.zeros(N_PAYMENTS, dtype=np.int64))
    amounts = rupees * 100 + nonce
    rows = [MirrorRow(f"pay_{i:05d}", int(a), int(t))
            for i, (a, t) in enumerate(zip(amounts, times))]
    return rows, amounts, times


def _run_arm(chillar: bool) -> dict:
    rng = np.random.default_rng(SEED)
    rows, amounts, times = _synthetic_day(rng, chillar=chillar)
    lm = LedgerMatcher(Mirror(rows, fetched_at=DAY_OPEN), DEFAULT_WINDOW_S)

    # --- genuine screens: the customer's phone, seconds after they paid ------
    genuine = {MATCHED: 0, AMBIGUOUS: 0, NO_MATCH: 0, AMBER_STALE: 0}
    for a, t in zip(amounts, times):
        delay = int(rng.integers(2, 61))
        genuine[lm.match(int(a), int(t) + delay).verdict] += 1

    # --- replayed screens: a real screenshot from earlier today, shown again
    #     at some other moment. Any MATCHED here is a FALSE ACCEPT.
    accepts = ambiguous = trials = 0
    occupancy = []
    for a, t in zip(amounts, times):
        for _ in range(REPLAYS_PER_PAYMENT):
            ts = int(rng.integers(DAY_OPEN, DAY_OPEN + DAY_SECONDS))
            if abs(ts - int(t)) <= DEFAULT_WINDOW_S:
                continue                     # that is the legitimate window
            res = lm.match(int(a), ts)
            trials += 1
            occupancy.append(res.n_in_window)
            if res.verdict == MATCHED:
                accepts += 1
            elif res.verdict == AMBIGUOUS:
                ambiguous += 1
    return {
        "genuine": genuine,
        "trials": trials,
        "false_accepts": accepts,
        "replay_ambiguous": ambiguous,
        "far": accepts / trials,
        "mean_occupancy": float(np.mean(occupancy)),
    }


def test_MEASURED_false_accept_rate_over_a_synthetic_day():
    """The number that matters: how often does a screenshot from earlier today,
    replayed at some other moment, get MATCHED?

    Two arms, identical except for the CHILLAR paise nonce, so the difference
    is attributable to the nonce and nothing else.
    """
    p_pair = float((PRICE_W ** 2).sum())      # P(two baskets share a rupee total)
    chillar = _run_arm(chillar=True)
    plain = _run_arm(chillar=False)

    n_others = chillar["mean_occupancy"] - 1.0
    pred_chillar = 1.0 - (1.0 - p_pair / CHILLAR_SPACE) ** n_others
    pred_plain = 1.0 - (1.0 - p_pair) ** n_others

    print("\n" + "=" * 74)
    print(f"[MEASURED] synthetic trading day: {N_PAYMENTS} settled payments over "
          f"{DAY_SECONDS // 3600} h, window +/-{DEFAULT_WINDOW_S} s")
    print(f"           rupee-total collision probability per pair "
          f"sum(p^2) = {p_pair:.5f}  ({len(PRICE_GRID)} price points)")
    print(f"           mean payments inside a window = "
          f"{chillar['mean_occupancy']:.3f}")
    for name, arm, pred in (("CHILLAR nonce 01-99", chillar, pred_chillar),
                            ("round rupees (no nonce)", plain, pred_plain)):
        g = arm["genuine"]
        print(f"  {name}")
        print(f"     genuine screens : MATCHED {g[MATCHED]}/{N_PAYMENTS} "
              f"({100*g[MATCHED]/N_PAYMENTS:.2f}%), AMBIGUOUS {g[AMBIGUOUS]}, "
              f"NO_MATCH {g[NO_MATCH]}")
        print(f"     replay attempts : {arm['trials']}, "
              f"FALSE ACCEPTS {arm['false_accepts']}  ->  "
              f"FAR = {100*arm['far']:.4f}%   (predicted {100*pred:.4f}%)")
        print(f"     replays absorbed by AMBIGUOUS: {arm['replay_ambiguous']}")
    print(f"  CHILLAR reduces the false-accept rate by "
          f"{plain['far'] / max(chillar['far'], 1e-12):.1f}x")
    print("=" * 74)

    # --- the assertions the number has to survive ---------------------------
    assert chillar["trials"] > 12_000
    assert chillar["far"] < 0.005, chillar
    assert plain["far"] > 20 * chillar["far"], (plain["far"], chillar["far"])
    # the closed-form model predicts the measurement within a factor of 3
    assert 0.3 * pred_chillar <= chillar["far"] <= 3.0 * pred_chillar
    assert 0.3 * pred_plain <= plain["far"] <= 3.0 * pred_plain
    # a genuine screen is corroborated almost always, and when it is not it is
    # AMBIGUOUS (amber) — never NO_MATCH, and never anything red
    assert chillar["genuine"][NO_MATCH] == 0
    assert chillar["genuine"][AMBER_STALE] == 0
    assert chillar["genuine"][MATCHED] >= int(0.98 * N_PAYMENTS)
    # and the nonce buys most of that: without it, many genuine screens go amber
    assert plain["genuine"][AMBIGUOUS] > 5 * chillar["genuine"][AMBIGUOUS]


def test_MEASURED_a_stale_mirror_accepts_nothing_at_all():
    """Rule: stale -> AMBER_STALE regardless. Re-run the whole replay arm with a
    stale mirror and confirm the accept count is exactly zero."""
    rng = np.random.default_rng(SEED)
    rows, amounts, times = _synthetic_day(rng, chillar=True)
    lm = LedgerMatcher(Mirror(rows, fetched_at=DAY_OPEN), DEFAULT_WINDOW_S)
    verdicts = {v: 0 for v in VERDICTS}
    for a, t in zip(amounts, times):
        verdicts[lm.match(int(a), int(t) + 5, mirror_age_s=120.0).verdict] += 1
    print(f"\n[MEASURED] {N_PAYMENTS} genuine screens against a 120 s stale "
          f"mirror (threshold 60 s): {verdicts}")
    assert verdicts[AMBER_STALE] == N_PAYMENTS
    assert verdicts[MATCHED] == 0
