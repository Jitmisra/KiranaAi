"""S6 acceptance: the hand is read as an occluder, with no hand model.

Every shape here is synthesised deterministically with numpy/cv2 so that the
solidity numbers this file prints are reproducible on any machine, and so
that no number in the report was typed by hand (invariant 9).

The three research anchors this module was built against (SIX.md §"MUDRA"):

    fist ~0.73   ·   open palm ~0.92   ·   goods 0.96-1.00

test_MEASURED_solidity_of_each_synthetic_shape prints what the estimator
actually reads for each synthetic silhouette and asserts it lands on those
anchors. If a refactor moves a shape off its anchor, that test says so with
the number rather than merely going red.
"""
from __future__ import annotations

import math
import time

import cv2
import numpy as np
import pytest

from gawaah.mudra import (
    COMPACTNESS_DISC_CEILING,
    DWELL_FRAMES,
    HAND_AREA_MM2,
    MIN_DEFECT_DEPTH_MM,
    PX_PER_MM_ISO,
    REASONS,
    STATES,
    GestureState,
    MudraError,
    OccluderGesture,
    ShapeMetrics,
    measure_mask,
)
from gawaah.takhti import (
    BUF_H, BUF_W, MAT_H_MM, MAT_W_MM, PX_PER_MM_X, PX_PER_MM_Y, render_takhti,
)

OCCLUDER_GREY = 60          # a hand blocks ~76 % of the light off a white mat
CX_MM, CY_MM = 148.0, 250.0  # mat centre-ish, clear of all four markers


# ------------------------------------------------------------------ fixtures

def empty_mat() -> np.ndarray:
    """The empty-mat reference: a real rectified TAKHTI buffer, markers and all."""
    return cv2.resize(render_takhti(4.0), (BUF_W, BUF_H), interpolation=cv2.INTER_AREA)


def blank_mask() -> np.ndarray:
    return np.zeros((BUF_H, BUF_W), np.uint8)


def occlude(ref: np.ndarray, mask: np.ndarray, grey: int = OCCLUDER_GREY) -> np.ndarray:
    """Lay an occluder of uniform albedo over the reference."""
    f = ref.copy()
    f[mask > 0] = grey
    return f


# ------------------------------------------------------------ shape synthesis

def m_goods(w_mm=120.0, h_mm=80.0, r_mm=10.0) -> np.ndarray:
    """A filled rounded rectangle: a packet lying on the mat. High solidity,
    no deep defects, compact outline."""
    m = blank_mask()
    cx, cy = CX_MM * PX_PER_MM_X, CY_MM * PX_PER_MM_Y
    w, h, r = w_mm * PX_PER_MM_X, h_mm * PX_PER_MM_Y, r_mm * PX_PER_MM_ISO
    x0, y0 = cx - w / 2, cy - h / 2
    cv2.rectangle(m, (int(x0 + r), int(y0)), (int(x0 + w - r), int(y0 + h)), 255, -1)
    cv2.rectangle(m, (int(x0), int(y0 + r)), (int(x0 + w), int(y0 + h - r)), 255, -1)
    for px, py in ((x0 + r, y0 + r), (x0 + w - r, y0 + r),
                   (x0 + r, y0 + h - r), (x0 + w - r, y0 + h - r)):
        cv2.circle(m, (int(px), int(py)), int(r), 255, -1)
    return m


def m_open_palm(spread_deg=20.0, finger_mm=78.0, finger_w_mm=20.0,
                palm_w_mm=92.0, palm_h_mm=100.0) -> np.ndarray:
    """A splayed hand: elliptical palm plus four radiating fingers and a thumb.

    The four inter-finger gaps are the deep convexity defects; the long
    perimeter is what drives compactness down. This is the 'star/comb with
    four deep notches' the brief asks for, drawn anatomically so the numbers
    land on the real-hand anchors rather than on a convenient abstraction.
    """
    m = blank_mask()
    cx, cy = CX_MM * PX_PER_MM_X, CY_MM * PX_PER_MM_Y
    cv2.ellipse(m, (int(cx), int(cy)),
                (int(palm_w_mm / 2 * PX_PER_MM_X), int(palm_h_mm / 2 * PX_PER_MM_Y)),
                0, 0, 360, 255, -1)
    for a in (-1.5, -0.5, 0.5, 1.5):
        ang = math.radians(a * spread_deg) - math.pi / 2
        length = finger_mm * (1.0 if abs(a) < 1 else 0.88)
        x0 = cx + math.cos(ang) * (palm_h_mm * 0.30 * PX_PER_MM_ISO)
        y0 = cy + math.sin(ang) * (palm_h_mm * 0.30 * PX_PER_MM_ISO)
        x1 = cx + math.cos(ang) * (length * PX_PER_MM_ISO)
        y1 = cy + math.sin(ang) * (length * PX_PER_MM_ISO)
        cv2.line(m, (int(x0), int(y0)), (int(x1), int(y1)), 255,
                 int(finger_w_mm * PX_PER_MM_ISO))
    ang = math.radians(-55.0)
    cv2.line(m, (int(cx), int(cy)),
             (int(cx + math.cos(ang) * 62 * PX_PER_MM_ISO),
              int(cy + math.sin(ang) * 62 * PX_PER_MM_ISO)),
             255, int(23 * PX_PER_MM_ISO))
    return m


def m_crescent(r_mm=64.0, bite_frac=0.80, offset_frac=0.80, bite_deg=90.0) -> np.ndarray:
    """A blob with a single notch: a closed hand with the wrist crease.

    One parameter, ``offset_frac``, moves the notch in and out and so dials
    solidity continuously from ~0.65 to ~0.99 while the deep-defect count
    stays pinned at 1. That is exactly the family needed to walk solidity
    across a threshold without changing any other channel.
    """
    m = blank_mask()
    cx, cy = CX_MM * PX_PER_MM_X, CY_MM * PX_PER_MM_Y
    r = r_mm * PX_PER_MM_ISO
    cv2.circle(m, (int(cx), int(cy)), int(r), 255, -1)
    a = math.radians(bite_deg)
    cv2.circle(m, (int(cx + math.cos(a) * offset_frac * r),
                   int(cy + math.sin(a) * offset_frac * r)),
               int(bite_frac * r), 0, -1)
    return m


def m_fist() -> np.ndarray:
    return m_crescent(offset_frac=0.80)


def m_notched_disc(depth_mm: float, r_mm=60.0, half_width_mm=9.0) -> np.ndarray:
    """A disc with one rectangular notch of an exactly known depth, used to
    check that defect depth is reported in real millimetres."""
    m = blank_mask()
    cx, cy = CX_MM * PX_PER_MM_X, CY_MM * PX_PER_MM_Y
    cv2.circle(m, (int(cx), int(cy)), int(r_mm * PX_PER_MM_ISO), 255, -1)
    top = cy - r_mm * PX_PER_MM_ISO
    cv2.rectangle(m,
                  (int(cx - half_width_mm * PX_PER_MM_ISO), int(top - 4)),
                  (int(cx + half_width_mm * PX_PER_MM_ISO),
                   int(top + depth_mm * PX_PER_MM_ISO)), 0, -1)
    return m


def solidity_of(eng: OccluderGesture, ref: np.ndarray, mask: np.ndarray) -> float:
    """What the ENGINE measures, i.e. after its morphology, not what the raw
    mask would give. Bisection below must target the estimator, not an ideal."""
    return eng.update(occlude(ref, mask)).solidity


def crescent_at_solidity(eng: OccluderGesture, ref: np.ndarray, target: float,
                         tol: float = 0.002) -> tuple[np.ndarray, float]:
    """Bisect the crescent's notch offset until the engine measures `target`.

    Solidity is monotonically increasing in offset_frac, so plain bisection
    converges. Returns the mask and the solidity actually achieved, which is
    what the test reports -- never the target.
    """
    lo, hi = 0.55, 1.45
    mask, got = m_crescent(offset_frac=hi), 0.0
    for _ in range(40):
        mid = (lo + hi) / 2.0
        mask = m_crescent(offset_frac=mid)
        got = solidity_of(eng, ref, mask)
        if abs(got - target) <= tol:
            break
        if got < target:
            lo = mid
        else:
            hi = mid
    assert abs(got - target) <= tol, f"bisection failed: wanted {target}, got {got}"
    return mask, got


def run(eng: OccluderGesture, ref: np.ndarray, mask: np.ndarray,
        n: int = DWELL_FRAMES + 1) -> GestureState:
    """Hold one shape for n frames so the dwell filter can commit."""
    frame = occlude(ref, mask)
    st = eng.update(frame)
    for _ in range(n - 1):
        st = eng.update(frame)
    return st


@pytest.fixture(scope="module")
def ref() -> np.ndarray:
    return empty_mat()


def fresh(ref: np.ndarray, **kw) -> OccluderGesture:
    return OccluderGesture(ref, **kw)


# =================================================== INVARIANT 3: no weights

def test_INVARIANT_no_model_weights_anywhere():
    """MediaPipe is 7,819,105 bytes against a 4.8 MB budget. Pin its absence."""
    import pathlib
    import sys

    src = pathlib.Path(__file__).resolve().parent.parent / "gawaah" / "mudra.py"
    text = src.read_text()
    for banned in ("mediapipe", "onnxruntime", "tflite", "torch", "tensorflow",
                   "sklearn", "dnn.readNet", "cv2.dnn"):
        assert banned not in text, f"mudra.py must not reach for {banned}"
    for mod in ("mediapipe", "onnxruntime", "torch", "tensorflow"):
        assert mod not in sys.modules, f"importing mudra pulled in {mod}"


def test_INVARIANT_no_mint_and_no_forgery_surface():
    """Invariants 5 and 6. MUDRA reveals a pre-minted target; it never mints,
    never holds a secret, and never constructs a payment payload."""
    import pathlib

    src = pathlib.Path(__file__).resolve().parent.parent / "gawaah" / "mudra.py"
    text = src.read_text()
    for banned in ("hmac", "upi://", "key_secret", "webhook", "requests.", "httpx"):
        assert banned not in text, f"mudra.py must not contain {banned!r}"
    assert not hasattr(OccluderGesture, "mint")


# ============================================ THE MEASURED NUMBERS (reported)

def test_MEASURED_solidity_of_each_synthetic_shape(ref):
    """Report the measured shape scalars of every synthetic silhouette.

    Asserted against the real-hand anchors from prior research, with the
    tolerance stated per shape rather than a single loose band.
    """
    eng = fresh(ref)
    rows = []
    for name, mask in (("goods (rounded rect)", m_goods()),
                       ("open palm (4 notches)", m_open_palm()),
                       ("fist (one notch)", m_fist())):
        st = eng.update(occlude(ref, mask))
        eng.reset()
        rows.append((name, st))

    lines = ["", f"{'shape':<24}{'solidity':>10}{'defects':>9}"
                 f"{'compact':>10}{'area_mm2':>11}  raw"]
    for name, st in rows:
        lines.append(f"{name:<24}{st.solidity:>10.4f}{st.defects:>9d}"
                     f"{st.compactness:>10.4f}{st.area_mm2:>11.1f}  {st.raw_state}")
    print("\n".join(lines))

    by = {n.split()[0]: s for n, s in rows}
    # anchors: fist ~0.73, open palm ~0.92, goods 0.96-1.00
    assert by["fist"].solidity == pytest.approx(0.73, abs=0.04), by["fist"].solidity
    assert by["open"].solidity == pytest.approx(0.92, abs=0.04), by["open"].solidity
    assert 0.96 <= by["goods"].solidity <= 1.0, by["goods"].solidity
    # the separation the whole feature rests on
    assert by["open"].solidity - by["fist"].solidity > 0.08, (
        "open/fist solidity gap fell below the feature's own 0.08 kill bar"
    )
    # defect count is what separates OPEN from GOODS
    assert by["open"].defects >= 3
    assert by["goods"].defects == 0
    assert by["fist"].defects < 3
    # compactness corroborates
    assert by["open"].compactness < by["goods"].compactness


def test_MEASURED_throughput_ms_per_frame(ref):
    """Report the real per-frame cost on this machine. No claim is made about
    the browser; this is the Python reference implementation's cost."""
    eng = fresh(ref)
    frame = occlude(ref, m_open_palm())
    eng.update(frame)                       # warm the code paths
    n = 40
    t0 = time.perf_counter()
    for _ in range(n):
        eng.update(frame)
    ms = (time.perf_counter() - t0) * 1000.0 / n
    print(f"\nOccluderGesture.update: {ms:.3f} ms/frame "
          f"over {n} frames on a {BUF_W}x{BUF_H} buffer")
    assert ms < 60.0, f"{ms:.1f} ms/frame is too slow for a 10 fps loop"


# ================================================== classification of shapes

def test_goods_classifies_as_GOODS(ref):
    st = run(fresh(ref), ref, m_goods())
    assert st.state == "GOODS", (st.state, st.reason, st.solidity, st.defects)
    assert st.reason.startswith("inert_object")


def test_open_palm_classifies_as_OPEN(ref):
    st = run(fresh(ref), ref, m_open_palm())
    assert st.state == "OPEN", (st.state, st.reason, st.solidity, st.defects)
    assert st.defects >= 3


def test_fist_classifies_as_FIST(ref):
    st = run(fresh(ref), ref, m_fist())
    assert st.state == "FIST", (st.state, st.reason, st.solidity, st.defects)
    assert st.solidity < 0.80


def test_open_palm_holds_across_a_spread_range(ref):
    """The gesture must not require one exact hand pose.

    Reports the working envelope. Below ~16 deg of finger spread the notches
    get too shallow for the 6 mm depth floor and the estimator ABSTAINS --
    that is the intended failure mode, not a silent wrong answer, and
    test_narrow_spread_abstains_rather_than_guessing pins it.
    """
    seen = {}
    for spread in (16.0, 18.0, 20.0, 22.0, 24.0, 26.0):
        st = run(fresh(ref), ref, m_open_palm(spread_deg=spread))
        seen[spread] = (st.state, round(st.solidity, 4), st.defects,
                        round(st.compactness, 4))
    print("\nopen-palm envelope (spread_deg -> state, solidity, defects, compactness)")
    for k, v in seen.items():
        print(f"  {k:>5.1f} -> {v}")
    assert all(v[0] == "OPEN" for v in seen.values()), seen


def test_narrow_spread_abstains_rather_than_guessing(ref):
    """Fingers held together is outside the envelope. The right answer there
    is AMBIGUOUS, never a coin-flip between OPEN and FIST."""
    st = run(fresh(ref), ref, m_open_palm(spread_deg=12.0))
    assert st.state == "AMBIGUOUS", (st.state, st.solidity, st.defects, st.compactness)
    assert st.reason.split("|")[0] in REASONS


def test_empty_mat_is_NONE(ref):
    st = run(fresh(ref), ref, blank_mask())
    assert st.state == "NONE"
    assert st.reason.startswith("no_occluder")
    assert st.area_mm2 == 0.0


def test_speck_smaller_than_the_area_floor_is_NONE(ref):
    m = blank_mask()
    cv2.circle(m, (int(CX_MM * PX_PER_MM_X), int(CY_MM * PX_PER_MM_Y)),
               int(10 * PX_PER_MM_ISO), 255, -1)   # ~314 mm2, under the 1200 floor
    st = run(fresh(ref), ref, m)
    assert st.state == "NONE" and "no_occluder" in st.reason


def test_whole_mat_changed_refuses_rather_than_measures(ref):
    """A global light change is not an occluder. Refuse, do not classify."""
    frame = np.full((BUF_H, BUF_W), 40, np.uint8)
    eng = fresh(ref)
    st = eng.update(frame)
    for _ in range(DWELL_FRAMES):
        st = eng.update(frame)
    assert st.state == "NONE"
    assert "occluder_too_large" in st.reason


# ============================================== ABSTENTION IS FIRST CLASS

def test_in_between_shape_returns_AMBIGUOUS(ref):
    """A half-closed hand: mid solidity, but only one deep notch. The
    solidity channel says 'open band', the defect channel says 'not a palm'.
    The channels disagree, so the answer is AMBIGUOUS and not a guess."""
    eng = fresh(ref)
    mask = m_crescent(offset_frac=1.10)      # solidity lands mid-band
    st = run(eng, ref, mask)
    assert 0.80 <= st.solidity <= 0.95, st.solidity
    assert st.defects < 3, st.defects
    assert st.state == "AMBIGUOUS", (st.state, st.solidity, st.defects)
    assert "mid_solidity_too_few_defects" in st.reason


def test_ambiguous_always_carries_a_named_reason(ref):
    """Invariant 7: abstain loudly, with a named reason code, never silently."""
    eng = fresh(ref)
    seen = set()
    for off in (0.98, 1.02, 1.06, 1.10, 1.14, 1.18):
        eng.reset()
        st = run(eng, ref, m_crescent(offset_frac=off))
        if st.state == "AMBIGUOUS":
            head = st.reason.split("|")[0]
            assert head and head != "closed_hand"
            seen.add(head)
    assert seen, "no ambiguous case produced in the sweep"
    assert seen <= set(REASONS), f"unpublished reason code: {seen - set(REASONS)}"


def test_articulated_object_at_goods_solidity_abstains(ref):
    """High solidity but many deep notches is a contradiction, not goods."""
    eng = fresh(ref, min_defects_open=1)     # make 'articulated' easy to trip
    st = run(eng, ref, m_goods())
    # goods has zero defects, so it still reads GOODS even at min_defects_open=1
    assert st.state == "GOODS"
    eng2 = fresh(ref, min_defects_open=1)
    st2 = run(eng2, ref, m_crescent(offset_frac=1.55))
    assert st2.solidity > 0.95, st2.solidity
    assert st2.defects >= 1
    assert st2.state == "AMBIGUOUS"
    assert "goods_solidity_but_articulated" in st2.reason


def test_elongated_high_solidity_blob_abstains(ref):
    """A bare forearm is highly solid and un-notched -- it would read as GOODS
    on solidity alone. Compactness catches it and the answer is abstention."""
    m = blank_mask()
    cv2.line(m, (int(30 * PX_PER_MM_X), int(CY_MM * PX_PER_MM_Y)),
             (int(270 * PX_PER_MM_X), int(CY_MM * PX_PER_MM_Y)),
             255, int(22 * PX_PER_MM_ISO))
    st = run(fresh(ref), ref, m)
    assert st.solidity > 0.95, st.solidity
    assert st.compactness < 0.45, st.compactness
    assert st.state == "AMBIGUOUS"
    assert "goods_solidity_but_elongated" in st.reason


def test_hand_merged_with_goods_abstains_rather_than_false_cancelling(ref):
    """SIX.md §8.2, the load-bearing collision. The maintained empty-mat
    reference makes every item permanent foreground, and MUDRA fires exactly
    when the mat is most cluttered. A hand overlapping a goods blob yields a
    merged contour whose SOLIDITY still looks hand-like -- prior measurement
    put it at 0.859, inside the open band -- which would be a false cancel.

    The mat's answer is metric: the merged blob is not hand-SIZED. Abstain.
    """
    merged = cv2.bitwise_or(m_open_palm(), m_goods(w_mm=180.0, h_mm=120.0))
    st = run(fresh(ref), ref, merged)
    print(f"\nmerged hand+goods: state={st.state} reason={st.reason} "
          f"solidity={st.solidity:.4f} defects={st.defects} area={st.area_mm2:.0f} mm2")
    assert st.area_mm2 > HAND_AREA_MM2[1], st.area_mm2
    assert st.state != "OPEN", "a merged contour must never read as a cancel gesture"
    assert st.state == "AMBIGUOUS"
    assert st.reason.split("|")[0] in REASONS


def test_hand_area_gate_is_what_rejects_an_oversized_palm(ref):
    """Isolate the area channel: the same silhouette, scaled past the hand
    envelope, must stop reading OPEN even though its shape is unchanged."""
    small = run(fresh(ref), ref, m_open_palm())
    assert small.state == "OPEN" and small.area_mm2 < HAND_AREA_MM2[1]

    big = m_open_palm(finger_mm=140.0, finger_w_mm=36.0,
                      palm_w_mm=165.0, palm_h_mm=180.0)
    st = run(fresh(ref), ref, big)
    assert st.area_mm2 > HAND_AREA_MM2[1], st.area_mm2
    assert st.state != "OPEN", (st.state, st.reason, st.area_mm2)

    # and it is only the gate: widen the envelope and the same frame reads OPEN
    widened = run(fresh(ref, hand_area_mm2=(4000.0, 60000.0)), ref, big)
    assert widened.state == "OPEN", (widened.state, widened.reason, widened.area_mm2)


def test_configured_dead_band_produces_solidity_dead_band(ref):
    """A calibration run may leave an explicit UNKNOWN gap. Landing in it must
    abstain with that exact reason."""
    eng = fresh(ref, open_solidity=(0.86, 0.95), fist_solidity_max=0.70)
    st = run(eng, ref, m_crescent(offset_frac=0.95))
    assert 0.70 <= st.solidity < 0.86, st.solidity
    assert st.state == "AMBIGUOUS"
    assert "solidity_dead_band" in st.reason


# ============================================================== HYSTERESIS

def test_hysteresis_prevents_chatter_across_the_fist_threshold(ref):
    """Solidity oscillating +-0.025 about the 0.80 boundary must not chatter.

    The raw (unfiltered) verdict is deliberately published, so this test can
    prove the input really did oscillate rather than merely asserting that
    the output was quiet.
    """
    probe = fresh(ref)
    low_mask, low_sol = crescent_at_solidity(probe, ref, 0.775)
    high_mask, high_sol = crescent_at_solidity(probe, ref, 0.825)
    print(f"\nchatter input: solidity oscillates {low_sol:.4f} <-> {high_sol:.4f} "
          f"about the 0.80 fist boundary")
    assert low_sol < 0.80 < high_sol

    eng = fresh(ref)
    run(eng, ref, low_mask)                  # settle into FIST
    assert eng.committed == "FIST"

    raw_seq, com_seq = [], []
    for i in range(16):
        st = eng.update(occlude(ref, high_mask if i % 2 == 0 else low_mask))
        raw_seq.append(st.raw_state)
        com_seq.append(st.state)

    raw_changes = sum(1 for a, b in zip(raw_seq, raw_seq[1:]) if a != b)
    com_changes = sum(1 for a, b in zip(com_seq, com_seq[1:]) if a != b)
    print(f"raw verdict changed {raw_changes} times; committed changed {com_changes}")

    assert raw_changes >= 8, f"input did not actually chatter: {raw_seq}"
    assert com_changes == 0, f"committed state chattered: {com_seq}"
    assert set(com_seq) == {"FIST"}


def test_control_without_hysteresis_or_dwell_it_DOES_chatter(ref):
    """Proves the previous test is not vacuous: with the Schmitt band and the
    dwell both disabled, the same input chatters exactly as expected."""
    probe = fresh(ref)
    low_mask, _ = crescent_at_solidity(probe, ref, 0.775)
    high_mask, _ = crescent_at_solidity(probe, ref, 0.825)

    naive = fresh(ref, solidity_hysteresis=0.0, dwell_frames=1)
    seq = [naive.update(occlude(ref, high_mask if i % 2 == 0 else low_mask)).state
           for i in range(16)]
    changes = sum(1 for a, b in zip(seq, seq[1:]) if a != b)
    assert changes >= 8, f"control failed to chatter, test is vacuous: {seq}"


def test_dwell_alone_also_blocks_an_alternating_input(ref):
    """The two anti-chatter mechanisms are independent. With the Schmitt band
    off, the dwell counter alone must still refuse to commit an input that
    never holds still."""
    probe = fresh(ref)
    low_mask, _ = crescent_at_solidity(probe, ref, 0.775)
    high_mask, _ = crescent_at_solidity(probe, ref, 0.825)

    eng = fresh(ref, solidity_hysteresis=0.0, dwell_frames=4)
    seq = [eng.update(occlude(ref, high_mask if i % 2 == 0 else low_mask)).state
           for i in range(16)]
    assert set(seq) == {"NONE"}, f"alternating input committed something: {seq}"


def test_dwell_delays_commit_by_exactly_dwell_frames(ref):
    eng = fresh(ref, dwell_frames=4)
    frame = occlude(ref, m_open_palm())
    states = [eng.update(frame) for _ in range(6)]
    assert [s.raw_state for s in states] == ["OPEN"] * 6
    assert [s.state for s in states] == ["NONE", "NONE", "NONE", "OPEN", "OPEN", "OPEN"]
    assert "dwell_" in states[0].reason


def test_a_real_transition_still_gets_through(ref):
    """Hysteresis must damp noise without deadening the gesture: a genuine
    open -> fist change must commit within one dwell window."""
    eng = fresh(ref)
    run(eng, ref, m_open_palm())
    assert eng.committed == "OPEN"
    fist = occlude(ref, m_fist())
    for i in range(DWELL_FRAMES):
        eng.update(fist)
    assert eng.committed == "FIST"


def test_reset_clears_temporal_state(ref):
    eng = fresh(ref)
    run(eng, ref, m_open_palm())
    assert eng.committed == "OPEN"
    eng.reset()
    assert eng.committed == "NONE"
    st = eng.update(occlude(ref, m_open_palm()))
    assert st.state == "NONE" and st.raw_state == "OPEN"


def test_set_reference_resets_state(ref):
    eng = fresh(ref)
    run(eng, ref, m_open_palm())
    assert eng.committed == "OPEN"
    eng.set_reference(ref.copy())
    assert eng.committed == "NONE"


# ============================================== SHADOW SUPPRESSION (risk R6)

def test_soft_shadow_alone_is_suppressed(ref):
    """A 0.85x multiplicative attenuation is a shadow, not an occluder."""
    shadow = m_open_palm()
    frame = ref.copy()
    frame[shadow > 0] = (ref[shadow > 0].astype(np.float32) * 0.85).astype(np.uint8)

    on = fresh(ref, suppress_shadow=True)
    off = fresh(ref, suppress_shadow=False)
    st_on = on.update(frame)
    st_off = off.update(frame)
    assert st_on.raw_state == "NONE", (st_on.raw_state, st_on.area_mm2)
    assert st_off.raw_state != "NONE", "control: without suppression it must be seen"
    assert st_off.area_mm2 > 5000.0


def test_shadow_suppression_rescues_an_open_palm_from_its_own_penumbra(ref):
    """Risk R6 made executable. A penumbra around the hand fills the
    inter-finger gaps, collapsing the defect count and inflating solidity --
    an OPEN hand reading as something else. Suppressing the penumbra restores
    the correct verdict; the control shows what happens if you do not."""
    hand = m_open_palm()
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41))
    penumbra = cv2.dilate(hand, k)

    frame = ref.copy()
    sel = penumbra > 0
    frame[sel] = (ref[sel].astype(np.float32) * 0.85).astype(np.uint8)
    frame[hand > 0] = OCCLUDER_GREY

    on, off = fresh(ref, suppress_shadow=True), fresh(ref, suppress_shadow=False)
    st_on, st_off = on.update(frame), off.update(frame)
    assert st_on.raw_state == "OPEN", (st_on.raw_state, st_on.solidity, st_on.defects)
    assert st_off.raw_state != "OPEN", (
        "control: the inflated silhouette should NOT read as an open palm; "
        f"got {st_off.raw_state} sol={st_off.solidity:.4f} def={st_off.defects}"
    )
    assert st_off.area_mm2 > st_on.area_mm2


def test_a_dark_hand_is_never_mistaken_for_a_shadow(ref):
    """The suppressor's band stops at 0.70; a hand blocks far more light than
    that, so it survives suppression unchanged."""
    hand = m_open_palm()
    on = fresh(ref, suppress_shadow=True).update(occlude(ref, hand, OCCLUDER_GREY))
    off = fresh(ref, suppress_shadow=False).update(occlude(ref, hand, OCCLUDER_GREY))
    assert on.raw_state == off.raw_state == "OPEN"
    assert on.area_mm2 == pytest.approx(off.area_mm2, rel=1e-6)
    assert on.solidity == pytest.approx(off.solidity, rel=1e-6)


# =========================================================== ROI / metrology

def test_roi_crops_the_gesture_search(ref):
    """SIX.md §8.1 'Spatial': the pay panel lives in the merchant-side margin,
    so a hand outside it must not be seen at all."""
    panel = (10.0, 10.0, 100.0, 100.0)      # top-left corner of the mat, in mm
    eng = fresh(ref, roi_mm=panel)
    st = run(eng, ref, m_open_palm())       # drawn at the mat centre, outside
    assert st.state == "NONE" and "no_occluder" in st.reason

    whole = fresh(ref)
    assert run(whole, ref, m_open_palm()).state == "OPEN"


def test_roi_outside_the_mat_is_refused(ref):
    with pytest.raises(MudraError, match="outside"):
        fresh(ref, roi_mm=(0.0, 0.0, MAT_W_MM + 5.0, MAT_H_MM))
    with pytest.raises(MudraError, match="positive"):
        fresh(ref, roi_mm=(0.0, 0.0, 0.0, 10.0))


def test_area_is_reported_in_real_square_millimetres(ref):
    """A 120 x 80 mm packet with 10 mm corner rounds has a known area."""
    st = fresh(ref).update(occlude(ref, m_goods(120.0, 80.0, 10.0)))
    expect = 120.0 * 80.0 - (4.0 - math.pi) * 10.0 ** 2
    assert st.area_mm2 == pytest.approx(expect, rel=0.02), (st.area_mm2, expect)


def test_defect_depth_is_in_real_millimetres():
    """OpenCV reports defect depth as a fixed-point integer scaled by 256.
    Getting that wrong scales every threshold by 256, so pin it directly: a
    notch cut to a known depth must be counted by a threshold below it and
    ignored by one above it."""
    depth = 20.0
    mask = m_notched_disc(depth_mm=depth)
    under = measure_mask(mask, min_defect_depth_mm=depth * 0.6)
    over = measure_mask(mask, min_defect_depth_mm=depth * 1.6)
    assert under is not None and over is not None
    assert under.defects == 1, under.defects
    assert over.defects == 0, over.defects


def test_measure_mask_on_a_disc_is_a_disc(ref):
    """Sanity anchor with a closed form: a disc has solidity 1 and its area is
    pi r^2, so any scale bug shows up immediately."""
    m = blank_mask()
    cv2.circle(m, (BUF_W // 2, BUF_H // 2), 200, 255, -1)
    met = measure_mask(m)
    assert met is not None
    assert met.solidity == pytest.approx(1.0, abs=0.01)
    assert met.defects == 0
    assert met.area_mm2 == pytest.approx(math.pi * (200 ** 2) / (PX_PER_MM_X * PX_PER_MM_Y),
                                         rel=0.02)


def test_MEASURED_compactness_raster_ceiling_is_not_one():
    """A rasterised disc does NOT measure compactness 1.0, and pretending it
    does would put every compactness threshold in this module 11 % too high.

    cv2.arcLength walks a Freeman chain, so a curved raster boundary is
    ~5.5 % longer than the smooth curve it samples; compactness squares the
    perimeter, so it reads ~11 % low. Axis-aligned rectangles are exact,
    which is why the same estimator has no bias on a packet. Both halves are
    measured here so the asymmetry is a pinned fact.
    """
    print("\nraster compactness of a perfect disc (ideal = 1.0):")
    for r in (50, 100, 200, 300, 400):
        m = blank_mask()
        cv2.circle(m, (BUF_W // 2, BUF_H // 2), r, 255, -1)
        met = measure_mask(m)
        assert met is not None
        print(f"  r={r:>4} px -> {met.compactness:.4f}")
        assert met.compactness == pytest.approx(COMPACTNESS_DISC_CEILING, abs=0.01)
        assert met.compactness < 0.92, "if this ever reaches 1.0, retune the thresholds"

    print("axis-aligned rectangles are unbiased (measured vs closed form):")
    for w, h in ((300, 300), (400, 200), (600, 150), (700, 100)):
        m = blank_mask()
        cv2.rectangle(m, (BUF_W // 2 - w // 2, BUF_H // 2 - h // 2),
                      (BUF_W // 2 + w // 2, BUF_H // 2 + h // 2), 255, -1)
        met = measure_mask(m)
        assert met is not None
        ideal = 4 * math.pi * w * h / (2 * (w + h)) ** 2
        print(f"  {w}x{h} -> {met.compactness:.4f}  (closed form {ideal:.4f})")
        assert met.compactness == pytest.approx(ideal, abs=0.01)


def test_measure_mask_on_empty_is_None():
    assert measure_mask(blank_mask()) is None


def test_border_touching_is_reported(ref):
    m = blank_mask()
    cv2.rectangle(m, (0, 400), (300, 700), 255, -1)      # runs off the left edge
    met = measure_mask(m)
    assert met is not None and met.border_touching is True
    assert measure_mask(m_goods()).border_touching is False


# ========================================== INVARIANT 4: nothing survives

def test_INVARIANT_update_retains_no_frame_buffer(ref):
    """The rectified crop is the only buffer that survives a frame grab, and
    it survives only in the caller's hands. The engine keeps scalars."""
    eng = fresh(ref)
    frame = occlude(ref, m_open_palm())
    eng.update(frame)
    for name, val in vars(eng).items():
        if isinstance(val, np.ndarray):
            assert val is not frame, f"{name} aliases the input frame"
            assert not np.shares_memory(val, frame), f"{name} shares memory with the input"

    # and mutating the frame afterwards cannot change a past verdict
    before = eng.update(frame)
    frame[:] = 0
    assert before.solidity > 0.0 and before.area_mm2 > 0.0


def test_reference_is_copied_not_aliased(ref):
    """A caller reusing its reference buffer for the next frame grab must not
    silently move our baseline. If it did, every solidity after that would be
    measured against the wrong plane and nothing would say so."""
    caller_buffer = ref.copy()
    eng = fresh(caller_buffer)
    frame = occlude(ref, m_open_palm())
    before = eng.update(frame)
    assert before.raw_state == "OPEN"

    caller_buffer[:] = 0                       # the caller recycles its buffer
    after = eng.update(frame)
    assert after.raw_state == "OPEN"
    assert after.solidity == pytest.approx(before.solidity, rel=1e-9)
    assert after.area_mm2 == pytest.approx(before.area_mm2, rel=1e-9)

    for name, val in vars(eng).items():
        if isinstance(val, np.ndarray):
            assert not np.shares_memory(val, caller_buffer), f"{name} aliases the caller"


# =========================================================== input contracts

def test_rejects_a_buffer_that_is_not_the_rectified_takhti(ref):
    with pytest.raises(MudraError, match="rectified TAKHTI buffer"):
        OccluderGesture(np.zeros((480, 640), np.uint8))
    eng = fresh(ref)
    with pytest.raises(MudraError, match="rectified TAKHTI buffer"):
        eng.update(np.zeros((480, 640), np.uint8))


def test_rejects_non_uint8(ref):
    with pytest.raises(MudraError, match="uint8"):
        OccluderGesture(np.zeros((BUF_H, BUF_W), np.float32))


def test_accepts_a_three_channel_buffer(ref):
    bgr = cv2.cvtColor(ref, cv2.COLOR_GRAY2BGR)
    eng = OccluderGesture(bgr)
    st = eng.update(cv2.cvtColor(occlude(ref, m_open_palm()), cv2.COLOR_GRAY2BGR))
    assert st.raw_state == "OPEN"


@pytest.mark.parametrize("kw", [
    {"open_solidity": (0.95, 0.80)},
    {"open_solidity": (0.0, 0.5)},
    {"open_solidity": (0.5, 1.5)},
    {"fist_solidity_max": 0.0},
    {"fist_solidity_max": 1.4},
    {"min_defects_open": 0},
    {"dwell_frames": 0},
    {"solidity_hysteresis": -0.1},
    {"hand_area_mm2": (22000.0, 4000.0)},
    {"hand_area_mm2": (0.0, 4000.0)},
])
def test_rejects_incoherent_thresholds(ref, kw):
    with pytest.raises(MudraError):
        fresh(ref, **kw)


def test_measure_mask_rejects_a_colour_image():
    with pytest.raises(MudraError, match="2-D"):
        measure_mask(np.zeros((10, 10, 3), np.uint8))


# ================================================================= plumbing

def test_gesture_state_rejects_an_unknown_state():
    with pytest.raises(MudraError):
        GestureState("MAYBE", 0.9, 3, 0.5, 100.0)
    with pytest.raises(MudraError):
        GestureState("OPEN", 0.9, 3, 0.5, 100.0, raw_state="MAYBE")


def test_states_tuple_is_exactly_the_contract():
    assert set(STATES) == {"NONE", "OPEN", "FIST", "GOODS", "AMBIGUOUS"}


def test_every_reason_the_code_can_emit_is_published():
    """Completeness, checked against the source rather than against a sweep:
    an abstention reason that is not in REASONS cannot be aggregated by a
    caller, which quietly turns a published abstention rate into a lie."""
    import pathlib
    import re

    src = (pathlib.Path(__file__).resolve().parent.parent / "gawaah" / "mudra.py").read_text()
    # matches both `return "STATE", "reason"` and `raw, reason = "STATE", "reason"`
    emitted = set(re.findall(r'"(?:%s)"\s*,\s*"([a-z_]+)"' % "|".join(STATES), src))
    assert emitted, "regex found no reason literals; the check has rotted"
    assert emitted <= set(REASONS), f"unpublished reason codes: {emitted - set(REASONS)}"
    # and nothing is published that the code cannot produce
    assert set(REASONS) - emitted == set(), f"dead reason codes: {set(REASONS) - emitted}"


def test_no_reason_string_is_ever_empty(ref):
    """Every verdict, of every kind, names its cause."""
    eng = fresh(ref)
    shapes = [blank_mask(), m_goods(), m_open_palm(), m_fist(),
              m_crescent(offset_frac=1.10), m_crescent(offset_frac=1.55)]
    for m in shapes:
        eng.reset()
        for _ in range(DWELL_FRAMES + 1):
            st = eng.update(occlude(ref, m))
        assert st.reason, st
        assert st.reason.split("|")[0] in REASONS, st.reason


def test_decided_is_true_only_for_actionable_states():
    def g(s):
        return GestureState(s, 0.9, 3, 0.5, 100.0)
    assert [g(s).decided for s in STATES] == [False, True, True, True, False]


def test_evidence_is_json_serialisable_and_canonical(ref):
    import json

    from gawaah.ledger import canonical

    st = run(fresh(ref), ref, m_open_palm())
    ev = st.evidence()
    blob = canonical(ev)
    assert isinstance(blob, bytes)
    back = json.loads(blob)
    assert back["state"] == "OPEN"
    assert isinstance(back["solidity"], float)
    assert isinstance(back["defects"], int)
    assert back["reason"] == "open_palm"


def test_update_many_matches_a_manual_loop(ref):
    frames = [occlude(ref, m_open_palm())] * 6
    a = [s.state for s in fresh(ref).update_many(frames)]
    eng = fresh(ref)
    b = [eng.update(f).state for f in frames]
    assert a == b


def test_shape_metrics_is_immutable():
    m = ShapeMetrics(0.9, 3, 0.5, 100.0, False)
    with pytest.raises(Exception):
        m.solidity = 0.1        # type: ignore[misc]


def test_min_defect_depth_default_is_below_a_finger_gap(ref):
    """The default depth floor must be well under a real inter-finger notch,
    or the OPEN channel silently stops firing."""
    st = fresh(ref).update(occlude(ref, m_open_palm()))
    assert st.defects >= 3
    strict = fresh(ref, min_defect_depth_mm=MIN_DEFECT_DEPTH_MM)
    assert strict.update(occlude(ref, m_open_palm())).defects >= 3
