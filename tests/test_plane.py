"""S2 acceptance: reprojection RMSE assertion on synthetic frames.

The whole product's metrology rests on this. If the plane is wrong, every
millimetre, every footprint tiebreak and every rupee downstream is wrong.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from gawaah.takhti import (
    BUF_H, BUF_W, MAT_H_MM, MAT_W_MM, PX_PER_MM_X, PX_PER_MM_Y,
    MatLock, PlaneEngine, marker_centres_mm, mm_to_buffer, render_takhti,
)


def synth_frame(px_per_mm=4.0, tilt=(0.0, 0.0), size=(960, 1280), noise=0.0, seed=0,
                fit=0.82):
    """Render the mat, then project it into a camera frame with a known pose.

    tilt is (x_deg, y_deg) of the mat plane away from fronto-parallel.
    Returns (frame, true_corners_in_frame).
    """
    mat = render_takhti(px_per_mm)
    h, w = mat.shape
    W, H = size

    # mat corners in its own pixel space
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)

    # build a destination quad by rotating the mat about its centre in 3D
    ax, ay = np.radians(tilt[0]), np.radians(tilt[1])
    half_w, half_h = w / 2, h / 2
    pts3d = np.array([[-half_w, -half_h, 0], [half_w, -half_h, 0],
                      [half_w, half_h, 0], [-half_w, half_h, 0]], np.float64)
    Rx = np.array([[1, 0, 0], [0, np.cos(ax), -np.sin(ax)], [0, np.sin(ax), np.cos(ax)]])
    Ry = np.array([[np.cos(ay), 0, np.sin(ay)], [0, 1, 0], [-np.sin(ay), 0, np.cos(ay)]])
    pts3d = pts3d @ Rx.T @ Ry.T

    # Choose focal length and distance so the mat actually FITS the frame.
    # Without this the mat projects far outside the sensor and no marker is
    # visible -- which is exactly the bug this harness had on first run.
    f = max(w, h) * 2.2                      # focal length, pixels
    dist = f * max(w / (fit * W), h / (fit * H))
    proj = []
    for X, Y, Z in pts3d:
        z = dist + Z
        proj.append([f * X / z + W / 2, f * Y / z + H / 2])
    dst = np.array(proj, np.float32)

    M = cv2.getPerspectiveTransform(src, dst)
    frame = np.full((H, W), 235, np.uint8)
    warped = cv2.warpPerspective(mat, M, (W, H), borderValue=235)
    mask = cv2.warpPerspective(np.full_like(mat, 255), M, (W, H), borderValue=0)
    frame[mask > 128] = warped[mask > 128]

    if noise > 0:
        rng = np.random.default_rng(seed)
        frame = np.clip(frame.astype(np.int16)
                        + rng.normal(0, noise, frame.shape), 0, 255).astype(np.uint8)
    return frame, dst


# ---------------------------------------------------------------- geometry

def test_scale_is_two_root_two_not_two():
    """The PRD said 2 px/mm. It is 2*sqrt(2). Guard the corrected constant."""
    assert PX_PER_MM_X == pytest.approx(2.8283, abs=1e-3)
    assert PX_PER_MM_Y == pytest.approx(2.8286, abs=1e-3)
    assert PX_PER_MM_X == pytest.approx(2 * np.sqrt(2), abs=2e-4)
    assert BUF_W / BUF_H == pytest.approx(MAT_W_MM / MAT_H_MM, rel=2e-3)


def test_mm_to_buffer_maps_mat_corners_to_buffer_corners():
    got = mm_to_buffer(np.array([[0, 0], [MAT_W_MM, MAT_H_MM]]))
    assert got[0] == pytest.approx([0, 0])
    assert got[1] == pytest.approx([BUF_W, BUF_H])


def test_render_has_four_findable_markers():
    mat = render_takhti(4.0)
    eng = PlaneEngine()
    lock = eng.detect(mat)
    assert lock.locked, lock.reason
    assert lock.ids_found == (0, 1, 2, 3)


# ---------------------------------------------------------------- ACCEPTANCE

@pytest.mark.parametrize("tilt", [(0, 0), (3, 0), (0, 3), (4, 4), (-5, 2)])
def test_ACCEPTANCE_reprojection_rmse_under_tilt(tilt):
    """S2 acceptance criterion: reprojection RMSE assertion passes."""
    frame, _ = synth_frame(tilt=tilt)
    lock = PlaneEngine().detect(frame)
    assert lock.locked, f"tilt={tilt}: {lock.reason}"
    assert lock.reproj_rmse_px is not None
    assert lock.reproj_rmse_px < 1.0, (
        f"tilt={tilt}: RMSE {lock.reproj_rmse_px:.3f}px exceeds 1.0px budget"
    )


def test_ACCEPTANCE_rectified_buffer_is_metric():
    """A known mat distance must measure correctly in the rectified buffer.

    This is the test that proves millimetres are real millimetres: the two
    top markers are a known distance apart on the printed sheet, so after
    rectification they must be that distance in buffer pixels.
    """
    frame, _ = synth_frame(tilt=(3, 2))
    eng = PlaneEngine()
    lock = eng.detect(frame)
    assert lock.locked, lock.reason

    rect = eng.rectify(frame, lock.H)
    assert rect.shape[:2] == (BUF_H, BUF_W)

    relock = eng.detect(rect)
    assert relock.locked, f"rectified buffer must re-lock: {relock.reason}"

    d2 = eng._det.detectMarkers(rect)
    corners, ids, _ = d2
    by_id = {int(i): c.reshape(4, 2).mean(axis=0)
             for i, c in zip(ids.flatten(), corners)}

    centres = marker_centres_mm()
    expect_mm = float(np.linalg.norm(centres[1] - centres[0]))
    got_px = float(np.linalg.norm(by_id[1] - by_id[0]))
    got_mm = got_px / PX_PER_MM_X

    assert got_mm == pytest.approx(expect_mm, rel=0.01), (
        f"metric error: expected {expect_mm:.1f}mm, measured {got_mm:.1f}mm"
    )


def test_rectification_is_idempotent():
    """Rectifying an already-rectified buffer must be ~identity."""
    frame, _ = synth_frame(tilt=(4, 3))
    eng = PlaneEngine()
    lock = eng.detect(frame)
    once = eng.rectify(frame, lock.H)
    l2 = eng.detect(once)
    assert l2.locked
    twice = eng.rectify(once, l2.H)
    diff = cv2.absdiff(once, twice).mean()
    assert diff < 6.0, f"double rectification drifted by {diff:.2f} grey levels"


# ---------------------------------------------------------------- refusals

def test_refuses_when_markers_missing():
    frame, _ = synth_frame()
    frame[:, : frame.shape[1] // 2] = 235      # obliterate the left markers
    lock = PlaneEngine().detect(frame)
    assert not lock.locked
    assert "missing markers" in lock.reason or "no markers" in lock.reason


def test_refuses_on_blank_frame():
    lock = PlaneEngine().detect(np.full((640, 480), 200, np.uint8))
    assert not lock.locked and lock.H is None


def test_survives_sensor_noise():
    frame, _ = synth_frame(tilt=(2, 2), noise=6.0, seed=3)
    lock = PlaneEngine().detect(frame)
    assert lock.locked, lock.reason
    assert lock.reproj_rmse_px < 1.5


# ------------------------------------------------- calibration of persp_index

def test_persp_index_is_monotonic_in_tilt():
    """Focal-INVARIANT property: more tilt must always mean a higher index.

    This is what the mat-lock gate actually relies on, and unlike the degrees
    conversion it holds for any lens.
    """
    tilts = [(0, 0), (2, 2), (5, 5), (8, 0), (12, 8), (20, 15)]
    idx = []
    for t in tilts:
        frame, _ = synth_frame(tilt=t, fit=0.72)
        lock = PlaneEngine().detect(frame)
        assert lock.H is not None, f"{t}: {lock.reason}"
        idx.append(lock.persp_index)
    true = [np.degrees(np.arctan(np.hypot(np.tan(np.radians(a)), np.tan(np.radians(b)))))
            for a, b in tilts]
    for i in range(1, len(idx)):
        assert idx[i] > idx[i - 1], (
            f"index not monotonic: {true[i-1]:.1f}deg -> {idx[i-1]:.4f}, "
            f"{true[i]:.1f}deg -> {idx[i]:.4f}"
        )
    # 1e-3 index == ~0.2 deg at the nominal lens; below that is subpixel
    # corner-detection noise, not measurable tilt.
    assert idx[0] < 1e-3, f"fronto-parallel must be ~0, got {idx[0]:.6f}"


@pytest.mark.parametrize("tilt", [(0, 0), (2, 2), (3, 0), (5, 5), (8, 0)])
def test_persp_to_deg_is_accurate_AT_ITS_CALIBRATION_GEOMETRY(tilt):
    """PERSP_K = 0.286 was fitted at fit=0.82. At that geometry it recovers
    true tilt within 0.5 deg.

    It does NOT transfer to other focal lengths -- verified by
    test_persp_to_deg_does_not_transfer_across_focal_length below. That is why
    the gate thresholds the raw index and why persp_to_deg is documented as
    approximate and needing per-rig calibration.
    """
    frame, _ = synth_frame(tilt=tilt, fit=0.82)
    lock = PlaneEngine().detect(frame)
    assert lock.H is not None, lock.reason
    true_deg = np.degrees(
        np.arctan(np.hypot(np.tan(np.radians(tilt[0])), np.tan(np.radians(tilt[1]))))
    )
    assert PlaneEngine.persp_to_deg(lock.persp_index) == pytest.approx(
        true_deg, abs=0.5
    )


def test_persp_to_deg_does_not_transfer_across_focal_length():
    """Pins the honest limitation as an executable fact, so nobody later
    reports persp_to_deg as a measured angle on real hardware."""
    per_fit = {}
    for fit in (0.82, 0.62):
        frame, _ = synth_frame(tilt=(8, 0), fit=fit)
        lock = PlaneEngine().detect(frame)
        assert lock.H is not None
        per_fit[fit] = PlaneEngine.persp_to_deg(lock.persp_index)
    assert abs(per_fit[0.82] - per_fit[0.62]) > 1.0, (
        "if this ever passes, persp_to_deg became focal-invariant and the "
        f"honesty note can be relaxed: {per_fit}"
    )


def test_scale_error_is_tilt_invariant():
    """The bug this replaced: the old estimator reported a 2.7% 'scale error'
    for a legitimate 2 deg tilt, because it measured perspective foreshortening
    in the RAW frame instead of scale on the RECTIFIED plane."""
    errs = []
    for tilt in [(0, 0), (2, 2), (5, 5), (8, 0), (12, 8)]:
        frame, _ = synth_frame(tilt=tilt, fit=0.72)
        lock = PlaneEngine().detect(frame)
        assert lock.scale_err is not None
        errs.append(lock.scale_err)
    assert max(errs) < 0.015, f"scale error grew with tilt: {errs}"
    assert max(errs) - min(errs) < 0.010, (
        f"scale error is not tilt-invariant: {errs}"
    )


def test_gate_rejects_excessive_tilt():
    frame, _ = synth_frame(tilt=(25, 20), fit=0.62)
    lock = PlaneEngine().detect(frame)
    if lock.locked:
        pytest.fail(f"should refuse at ~30deg, got persp={lock.persp_index:.4f}")
    assert any(k in lock.reason for k in ("perspective index", "scale error", "missing"))
