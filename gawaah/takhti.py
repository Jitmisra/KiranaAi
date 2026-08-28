"""The TAKHTI — the printed A3 mat, and the plane engine built on it.

The mat does five jobs with one sheet (PRD §3):
  1. fiducial          — 4 ArUco markers, DICT_4X4_50, ids 0..3
  2. metric plane      — homography to a fixed rectified buffer
  3. measurement       — objects are measured AT REST ON THE PLANE, so the
                         parallax term is zero and mm are real mm
  4. privacy quad      — the rectified crop is the only buffer that survives;
                         at 45cm nadir a standing person cannot be inside it
  5. sell-event boundary — a directional exit across the mat's far edge

Scale, stated exactly because a prior document got it wrong:
    A3 is 297 x 420 mm. The buffer is 840 x 1188 px.
    840/297  = 2.82828...
    1188/420 = 2.82857...
    i.e. 2*sqrt(2) = 2.828427. The PRD's "2 px/mm" is arithmetically wrong.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# --- A3 mat geometry, millimetres -------------------------------------------
MAT_W_MM = 297.0
MAT_H_MM = 420.0

# --- rectified buffer, pixels -----------------------------------------------
BUF_W = 840
BUF_H = 1188
PX_PER_MM_X = BUF_W / MAT_W_MM   # 2.82828
PX_PER_MM_Y = BUF_H / MAT_H_MM   # 2.82857
PX_PER_MM = (PX_PER_MM_X + PX_PER_MM_Y) / 2.0

# --- printed features --------------------------------------------------------
MARKER_MM = 30.0          # ArUco square side
MARGIN_MM = 12.0          # from sheet edge to marker outer corner
SCALE_PATCH_MM = 20.0     # the printed verification square

ARUCO_DICT = cv2.aruco.DICT_4X4_50
MARKER_IDS = (0, 1, 2, 3)  # TL, TR, BR, BL

# mat-lock thresholds (PRD §7.1)
MAX_SCALE_ERR = 0.015     # 1.5 % worst marker side, measured on the rectified plane
PERSP_K = 0.286           # persp_index ~= PERSP_K * tan(tilt); see _persp_index
MAX_PERSP_INDEX = 0.040   # == PERSP_K * tan(8 deg), i.e. the 8 deg gate


def marker_centres_mm() -> np.ndarray:
    """Centres of the four markers in mat coordinates (mm), TL,TR,BR,BL."""
    c = MARGIN_MM + MARKER_MM / 2.0
    return np.array(
        [[c, c], [MAT_W_MM - c, c], [MAT_W_MM - c, MAT_H_MM - c], [c, MAT_H_MM - c]],
        dtype=np.float64,
    )


def mm_to_buffer(pts_mm: np.ndarray) -> np.ndarray:
    """Mat millimetres -> rectified buffer pixels."""
    out = np.asarray(pts_mm, dtype=np.float64).copy()
    out[:, 0] *= PX_PER_MM_X
    out[:, 1] *= PX_PER_MM_Y
    return out


def render_takhti(px_per_mm: float = 4.0) -> np.ndarray:
    """Render a printable/synthetic TAKHTI, white with black markers.

    Used both to produce the actual print artwork and to generate synthetic
    frames for the plane tests.
    """
    w = int(round(MAT_W_MM * px_per_mm))
    h = int(round(MAT_H_MM * px_per_mm))
    img = np.full((h, w), 255, np.uint8)
    d = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
    side = int(round(MARKER_MM * px_per_mm))

    for idx, (cx, cy) in zip(MARKER_IDS, marker_centres_mm()):
        m = cv2.aruco.generateImageMarker(d, idx, side)
        x0 = int(round(cx * px_per_mm - side / 2))
        y0 = int(round(cy * px_per_mm - side / 2))
        img[y0:y0 + side, x0:x0 + side] = m

    # the 20 mm scale-verification patch, centred horizontally near the top
    s = int(round(SCALE_PATCH_MM * px_per_mm))
    sx = int(round((MAT_W_MM / 2 - SCALE_PATCH_MM / 2) * px_per_mm))
    sy = int(round(70 * px_per_mm))
    img[sy:sy + s, sx:sx + s] = 0

    # exit-edge arrow along the far (bottom) edge
    ay = int(round((MAT_H_MM - 18) * px_per_mm))
    cv2.arrowedLine(img, (w // 2 - int(30 * px_per_mm), ay),
                    (w // 2 + int(30 * px_per_mm), ay), 0,
                    max(2, int(px_per_mm)), tipLength=0.3)
    return img


@dataclass
class MatLock:
    """Result of trying to lock onto the mat in a frame."""
    locked: bool
    reason: str
    H: np.ndarray | None = None          # frame -> rectified buffer
    ids_found: tuple[int, ...] = ()
    scale_err: float | None = None       # measured vs printed 20 mm patch
    persp_index: float | None = None
    reproj_rmse_px: float | None = None


class PlaneEngine:
    """Detects the TAKHTI and produces the rectified metric buffer."""

    def __init__(self) -> None:
        self._dict = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
        params = cv2.aruco.DetectorParameters()
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        self._det = cv2.aruco.ArucoDetector(self._dict, params)

    def detect(self, frame: np.ndarray) -> MatLock:
        gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = self._det.detectMarkers(gray)
        if ids is None:
            return MatLock(False, "no markers detected")
        found = {int(i) for i in ids.flatten()}
        if not set(MARKER_IDS).issubset(found):
            missing = sorted(set(MARKER_IDS) - found)
            return MatLock(False, f"missing markers {missing}",
                           ids_found=tuple(sorted(found)))

        # centre of each expected marker, in frame pixels
        by_id = {int(i): c.reshape(4, 2) for i, c in zip(ids.flatten(), corners)}
        src = np.array([by_id[i].mean(axis=0) for i in MARKER_IDS], np.float64)
        dst = mm_to_buffer(marker_centres_mm())

        H, _ = cv2.findHomography(src, dst, method=0)
        if H is None:
            return MatLock(False, "homography failed", ids_found=tuple(sorted(found)))

        proj = cv2.perspectiveTransform(src.reshape(-1, 1, 2), H).reshape(-1, 2)
        rmse = float(np.sqrt(((proj - dst) ** 2).sum(axis=1).mean()))

        persp = self._persp_index(H)
        scale_err = self._scale_error(by_id, H)

        if scale_err > MAX_SCALE_ERR:
            return MatLock(False, f"scale error {scale_err:.3%} > {MAX_SCALE_ERR:.1%}",
                           H, tuple(sorted(found)), scale_err, persp, rmse)
        if persp > MAX_PERSP_INDEX:
            return MatLock(False,
                           f"perspective index {persp:.4f} > {MAX_PERSP_INDEX} "
                           f"(~{self.persp_to_deg(persp):.1f} deg)",
                           H, tuple(sorted(found)), scale_err, persp, rmse)

        return MatLock(True, "locked", H, tuple(sorted(found)), scale_err, persp, rmse)

    @staticmethod
    def persp_to_deg(index: float) -> float:
        """Approximate degrees from the perspective index. See _persp_index for
        why this is approximate and must not be reported as a measured angle."""
        return float(np.degrees(np.arctan(index / PERSP_K)))

    def rectify(self, frame: np.ndarray, H: np.ndarray) -> np.ndarray:
        """Warp to the fixed metric buffer. THIS is the only buffer that
        survives the call — invariant 4."""
        return cv2.warpPerspective(frame, H, (BUF_W, BUF_H))

    @staticmethod
    def _persp_index(H: np.ndarray) -> float:
        """Dimensionless perspective index. 0.0 == fronto-parallel (nadir).

        Taken from the last row of the buffer->frame homography, which is
        exactly the term that vanishes for an affine (untilted) view, scaled by
        the buffer's characteristic length to make it dimensionless.

        CALIBRATION, measured against synthetic ground truth in
        tests/test_plane.py::test_persp_index_tracks_true_tilt:

            persp_index ~= PERSP_K * tan(tilt)      PERSP_K = 0.286

        fitting true tilt to within 0.3 deg over 0-25 deg.

        HONESTY NOTE: PERSP_K absorbs the camera's focal length, so the
        degrees conversion is only exact for the nominal lens used in the
        synthetic harness. The GATE thresholds on the index itself, which is
        monotonic in tilt regardless of focal length. Per-device calibration
        would be needed to report true degrees on real hardware, and until
        that is measured this must not be presented as a measured angle.
        """
        try:
            Hi = np.linalg.inv(H)
        except np.linalg.LinAlgError:
            return float("inf")
        if Hi[2, 2] == 0:
            return float("inf")
        Hi = Hi / Hi[2, 2]
        return float(np.linalg.norm(Hi[2, :2]) * max(BUF_W, BUF_H))

    @staticmethod
    def _scale_error(by_id: dict[int, np.ndarray], H: np.ndarray) -> float:
        """Worst marker-side error, measured ON THE RECTIFIED PLANE, vs 30 mm.

        This is the self-verification step: the markers are a known physical
        size, so after rectification they must measure that size. Measuring in
        the RAW frame instead conflates genuine scale error with ordinary
        perspective foreshortening -- which is the bug this replaced, where a
        legitimate 2 deg tilt reported a 2.7% "scale error" and refused to lock.

        Corners are pushed through H rather than warping the whole image, so
        this costs four perspectiveTransform calls, not a full remap.
        """
        errs = []
        for i in MARKER_IDS:
            q = cv2.perspectiveTransform(
                by_id[i].reshape(-1, 1, 2).astype(np.float64), H
            ).reshape(4, 2)
            for a, b in ((0, 1), (1, 2), (2, 3), (3, 0)):
                side_mm = float(np.linalg.norm(q[a] - q[b])) / PX_PER_MM
                errs.append(abs(side_mm - MARKER_MM) / MARKER_MM)
        return float(max(errs))
