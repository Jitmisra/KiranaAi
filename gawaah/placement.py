"""S3a — the PLACEMENT DETECTOR. No neural detector, by choice.

On a clamped nadir camera over a static printed plane, a learned object
detector is the wrong tool: it costs ~30 MB of download, an AGPL question and
an ORT-Web op-coverage risk, and it hands back an AXIS-ALIGNED box. The
metrology needs an ORIENTED one — a 210 x 30 mm packet turned 45 deg has an
axis-aligned bounding box of roughly 170 x 170 mm, an area inflation of ~4.6x.
That is the AABB-is-not-a-footprint bug, and it is a measurement error, not a
cosmetic one.

So the pipeline is classical and fully determined (PRD 3.4):

    absdiff vs a maintained empty-mat reference
      -> GaussianBlur (sensor grain, not signal)
      -> threshold
      -> morphologyEx OPEN  (kill speckle)
      -> morphologyEx CLOSE (heal the inside of a glossy wrapper)
      -> findContours
      -> minAreaRect            <- ORIENTED, never cv2.boundingRect
      -> 5-frame stability gate
      -> millimetres on the plane

Two refusals are wired in rather than guessed around:

  TOUCHES_BORDER   a contour running into the edge of the rectified buffer has
                   been cropped by the mat, so its true extent is unknown. It
                   is flagged and NEVER measured — the "poora rakhiye" case.
                   Every measured field comes back None and `stable` is forced
                   False so no downstream stage can mistake it for a reading.

  (too small)      blobs under MIN_AREA_MM2 are grain, not goods, and are
                   dropped rather than reported. The count is exposed as
                   `last_rejected_small` so the drop is observable.

Millimetres are real millimetres because the input is the RECTIFIED buffer and
the object is AT REST ON THE PLANE, so the parallax term is zero. Conversion is
anisotropic — PX_PER_MM_X and PX_PER_MM_Y differ in the 4th decimal and are
applied separately rather than through one averaged constant.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from gawaah.clock import Clock
from gawaah.takhti import BUF_H, BUF_W, PX_PER_MM_X, PX_PER_MM_Y

# --- segmentation ------------------------------------------------------------
DIFF_THRESH = 28          # grey levels. Fixed, not Otsu: Otsu on an EMPTY mat
                          # has no bimodal signal and hallucinates a split.
BLUR_KSIZE = 3            # px, on the difference image only
OPEN_PX = 3               # ~1.06 mm — removes speckle, keeps corners
CLOSE_PX = 5              # ~1.77 mm — heals specular holes in a wrapper
MIN_AREA_MM2 = 100.0      # a 10 x 10 mm object is the smallest thing we admit

# minAreaRect fits the tight box of the CONTOUR POINTS, which sit on the centres
# of the boundary pixels. A blob spanning columns c0..c1 therefore measures
# (c1 - c0) px where its true extent is (c1 - c0 + 1) px. One pixel back, split
# half per side. Measured residual after this correction is reported by
# tests/test_placement.py::test_ACCEPTANCE_measurement_error_across_sizes_and_rotations.
SUBPIX_PAD_PX = 1.0

# A single global threshold FINDS blobs well but MEASURES them badly: on a
# blurred edge the DIFF_THRESH crossing sits at DIFF_THRESH/amplitude of the way
# across, so a high-contrast object reads systematically larger than a
# low-contrast one. Measured on the synthetic sweep, that polarity split was
# ~0.4 mm. So each accepted blob is re-thresholded at HALF ITS OWN amplitude —
# the standard 50 % edge-crossing rule — inside its own ROI before minAreaRect.
REFINE_PCTL = 90          # percentile of in-blob diff taken as the amplitude
REFINE_MARGIN_PX = 6      # ROI slack around the coarse box
REFINE_MIN_LEVEL = 10     # floor on the refit level. The refit is confined to a
                          # dilation of the coarse blob, so dropping below
                          # DIFF_THRESH here cannot invent a new object.

# --- stability gate ----------------------------------------------------------
STABLE_FRAMES = 5         # consecutive agreeing frames before STABLE
CENTRE_TOL_MM = 1.5       # centre drift budget across the whole run
AREA_TOL_FRAC = 0.05      # oriented-box area drift budget across the whole run

# --- tracking ----------------------------------------------------------------
MATCH_MAX_MM = 25.0       # nearest-centre association gate
MAX_MISSES = 3            # frames a track may vanish for before it is dropped

# --- refusals ----------------------------------------------------------------
BORDER_PX = 2             # ~0.7 mm; a contour this close to the edge is cropped
REASON_OK = "OK"
REASON_BORDER = "TOUCHES_BORDER"      # UI copy: "poora rakhiye"

# --- reference maintenance ---------------------------------------------------
REF_ALPHA = 0.02          # per-empty-frame blend. ~35 frames to absorb 50% of a
                          # lighting step; fast enough for a dimming tube light,
                          # far too slow to swallow a placed object.


class PlacementError(ValueError):
    """Raised when the detector is handed something that is not the rectified
    metric buffer. Refusing here is cheaper than reporting millimetres that are
    silently in the wrong scale."""


@dataclass(frozen=True)
class Placement:
    """One blob resting on the plane, in MILLIMETRES on that plane.

    Every measured field is Optional and is None exactly when `measurable` is
    False. There is no sentinel value and no best guess.

    angle_deg is the bearing of the LONG edge, in [0, 180). A rectangle has
    180 deg symmetry, so any wider range would be a distinction without a
    difference.
    """
    id: int
    centre_mm: tuple[float, float]
    long_edge_mm: Optional[float]
    short_edge_mm: Optional[float]
    area_mm2: Optional[float]
    angle_deg: Optional[float]
    stable: bool
    frames_seen: int
    measurable: bool = True
    reason: str = REASON_OK
    contour_area_mm2: Optional[float] = None
    fill_ratio: Optional[float] = None
    stable_run: int = 0
    t_iso: Optional[str] = None


def px_to_mm(pts_px: np.ndarray) -> np.ndarray:
    """Rectified buffer pixels -> mat millimetres. Inverse of takhti.mm_to_buffer.

    Anisotropic on purpose: 840/297 != 1188/420.
    """
    out = np.asarray(pts_px, dtype=np.float64).reshape(-1, 2).copy()
    out[:, 0] /= PX_PER_MM_X
    out[:, 1] /= PX_PER_MM_Y
    return out


class _Track:
    """Association + stability state for one object across frames."""

    __slots__ = ("id", "centre_mm", "area_mm2", "frames_seen", "stable_run",
                 "anchor_centre", "anchor_area", "missed", "measurable")

    def __init__(self, tid: int, centre: tuple[float, float],
                 area: Optional[float], measurable: bool) -> None:
        self.id = tid
        self.centre_mm = centre
        self.area_mm2 = area
        self.frames_seen = 1
        self.stable_run = 1 if measurable else 0
        self.anchor_centre = centre
        self.anchor_area = area
        self.missed = 0
        self.measurable = measurable

    def observe(self, centre: tuple[float, float], area: Optional[float],
                measurable: bool) -> None:
        self.frames_seen += 1
        self.missed = 0
        self.centre_mm = centre
        self.area_mm2 = area

        if not measurable or area is None:
            # An object we refuse to measure can never accumulate stability.
            self.stable_run = 0
            self.anchor_centre = centre
            self.anchor_area = area
            self.measurable = measurable
            return

        # Agreement is checked against the ANCHOR — the first frame of the
        # current run — not against the previous frame. Frame-to-frame checking
        # lets an object creep: 20 steps of 0.4 mm each are all "stable" but the
        # object has moved 8 mm.
        agrees = (
            self.measurable
            and self.anchor_area is not None
            and float(np.hypot(centre[0] - self.anchor_centre[0],
                               centre[1] - self.anchor_centre[1])) <= CENTRE_TOL_MM
            and abs(area - self.anchor_area) <= AREA_TOL_FRAC * self.anchor_area
        )
        if agrees:
            self.stable_run += 1
        else:
            self.stable_run = 1
            self.anchor_centre = centre
            self.anchor_area = area
        self.measurable = measurable

    @property
    def stable(self) -> bool:
        return self.measurable and self.stable_run >= STABLE_FRAMES


class PlacementDetector:
    """Detects objects resting on the rectified TAKHTI buffer.

    Usage:
        det = PlacementDetector(empty_rectified_buffer)
        for rect in stream:
            for p in det.update(rect):
                ...
    """

    def __init__(self, ref_frame: np.ndarray, *,
                 diff_thresh: int = DIFF_THRESH,
                 min_area_mm2: float = MIN_AREA_MM2,
                 ref_alpha: float = REF_ALPHA,
                 clock: Clock | None = None) -> None:
        ref = self._as_buffer_gray(ref_frame)
        self._ref_f32 = ref.astype(np.float32)
        self._diff_thresh = int(diff_thresh)
        self._min_area_mm2 = float(min_area_mm2)
        self._ref_alpha = float(ref_alpha)
        self._clock = clock

        self._k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                                 (OPEN_PX, OPEN_PX))
        self._k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                                  (CLOSE_PX, CLOSE_PX))
        self._tracks: list[_Track] = []
        self._next_id = 1

        # observability, not state: the last frame's mask and drop count
        self.last_mask: np.ndarray | None = None
        self.last_rejected_small: int = 0
        self.ref_updates: int = 0
        self.frames: int = 0

    # ------------------------------------------------------------------ public

    @property
    def reference(self) -> np.ndarray:
        """The maintained empty-mat reference, as uint8."""
        return np.clip(self._ref_f32, 0, 255).astype(np.uint8)

    def reset_reference(self, frame: np.ndarray) -> None:
        """Hard-replace the reference, e.g. after the mat is re-seated."""
        self._ref_f32 = self._as_buffer_gray(frame).astype(np.float32)

    def update(self, rectified: np.ndarray) -> list[Placement]:
        """Consume one rectified buffer, return this frame's placements."""
        gray = self._as_buffer_gray(rectified)
        self.frames += 1
        stamp = self._clock.now_iso() if self._clock is not None else None

        mask, obs = self._segment(gray)
        self.last_mask = mask

        self._associate(obs)
        placements = self._emit(obs, stamp)

        # Slow reference maintenance, EMPTY MAT ONLY. Blending while an object
        # sits on the plane would teach the reference that the object is part of
        # the mat, and the object would fade out of its own detection.
        if not obs:
            cv2.accumulateWeighted(gray.astype(np.float32), self._ref_f32,
                                   self._ref_alpha)
            if self._ref_alpha > 0.0:
                self.ref_updates += 1

        return placements

    # ----------------------------------------------------------------- private

    @staticmethod
    def _as_buffer_gray(frame: np.ndarray) -> np.ndarray:
        if frame is None:
            raise PlacementError("frame is None")
        g = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if g.shape[:2] != (BUF_H, BUF_W):
            raise PlacementError(
                f"expected the rectified metric buffer {BUF_W}x{BUF_H}, "
                f"got {g.shape[1]}x{g.shape[0]} — millimetres would be wrong"
            )
        if g.dtype != np.uint8:
            g = np.clip(g, 0, 255).astype(np.uint8)
        return np.ascontiguousarray(g)

    def _segment(self, gray: np.ndarray) -> tuple[np.ndarray, list[dict]]:
        diff = cv2.absdiff(gray, self.reference)
        diff = cv2.GaussianBlur(diff, (BLUR_KSIZE, BLUR_KSIZE), 0)
        _, mask = cv2.threshold(diff, self._diff_thresh, 255, cv2.THRESH_BINARY)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._k_open)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._k_close)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)

        obs: list[dict] = []
        rejected = 0
        for cnt in contours:
            if len(cnt) < 3:
                rejected += 1
                continue
            pts = cnt.reshape(-1, 2)

            coarse = cv2.minAreaRect(cnt)
            _, c_long, c_short, _ = self._measure_box(coarse)
            if c_long * c_short < self._min_area_mm2:
                rejected += 1
                continue

            # The border test uses the COARSE contour in full-buffer coordinates:
            # whether the mat cropped the object is a fact about the raw blob,
            # and must not depend on a refinement we are about to refuse to run.
            touches = bool(
                pts[:, 0].min() <= BORDER_PX
                or pts[:, 1].min() <= BORDER_PX
                or pts[:, 0].max() >= BUF_W - 1 - BORDER_PX
                or pts[:, 1].max() >= BUF_H - 1 - BORDER_PX
            )
            if touches:
                obs.append({
                    "centre_mm": (float(coarse[0][0]) / PX_PER_MM_X,
                                  float(coarse[0][1]) / PX_PER_MM_Y),
                    "measurable": False,
                    "reason": REASON_BORDER,
                    "long_mm": None, "short_mm": None, "area_mm2": None,
                    "angle_deg": None, "cnt_area_mm2": None, "fill": None,
                })
                continue

            rect, cnt = self._refine(diff, cnt)
            long_v, long_mm, short_mm, box_area_mm2 = self._measure_box(rect)
            if box_area_mm2 < self._min_area_mm2:
                rejected += 1
                continue

            # Pixel-count area from the polygon area, via Pick's theorem:
            # #lattice points = A + B/2 + 1. Exact for axis-aligned lattice
            # polygons, approximate on diagonals. Diagnostic only — the
            # load-bearing number is long_edge_mm from minAreaRect.
            a_px = (float(cv2.contourArea(cnt))
                    + float(cv2.arcLength(cnt, True)) / 2.0 + 1.0)
            cnt_area_mm2 = a_px / (PX_PER_MM_X * PX_PER_MM_Y)

            obs.append({
                "centre_mm": (float(rect[0][0]) / PX_PER_MM_X,
                              float(rect[0][1]) / PX_PER_MM_Y),
                "measurable": True,
                "reason": REASON_OK,
                "long_mm": long_mm,
                "short_mm": short_mm,
                "area_mm2": box_area_mm2,
                "angle_deg": float(np.degrees(np.arctan2(long_v[1], long_v[0]))) % 180.0,
                "cnt_area_mm2": cnt_area_mm2,
                "fill": cnt_area_mm2 / box_area_mm2 if box_area_mm2 > 0 else None,
            })

        self.last_rejected_small = rejected
        return mask, obs

    @staticmethod
    def _measure_box(rect) -> tuple[np.ndarray, float, float, float]:
        """minAreaRect (buffer px) -> (long edge vector mm, long mm, short mm, mm^2).

        The subpixel pad is applied in the RECT's OWN axes so the correction
        survives rotation, and the corners are converted to mm individually so
        the x/y scale difference is honoured.
        """
        (cx, cy), (w, h), ang = rect
        padded = ((cx, cy), (w + SUBPIX_PAD_PX, h + SUBPIX_PAD_PX), ang)
        box_mm = px_to_mm(cv2.boxPoints(padded))
        e0 = box_mm[1] - box_mm[0]
        e1 = box_mm[2] - box_mm[1]
        l0 = float(np.linalg.norm(e0))
        l1 = float(np.linalg.norm(e1))
        long_v, long_mm, short_mm = (e0, l0, l1) if l0 >= l1 else (e1, l1, l0)
        return long_v, long_mm, short_mm, long_mm * short_mm

    def _refine(self, diff: np.ndarray, cnt: np.ndarray):
        """Re-cut this one blob at 50 % of its OWN amplitude, inside its own ROI.

        Returns (minAreaRect, contour) in full-buffer coordinates, falling back
        to the coarse fit whenever the refit would be degenerate. It never
        wanders: the refit is masked by a dilation of the coarse blob, so it can
        only adjust an edge by a pixel or two, never merge with a neighbour and
        never conjure a new object out of noise.
        """
        x, y, w, h = cv2.boundingRect(cnt)
        x0 = max(0, x - REFINE_MARGIN_PX)
        y0 = max(0, y - REFINE_MARGIN_PX)
        x1 = min(BUF_W, x + w + REFINE_MARGIN_PX)
        y1 = min(BUF_H, y + h + REFINE_MARGIN_PX)
        roi = diff[y0:y1, x0:x1]
        if roi.size == 0:
            return cv2.minAreaRect(cnt), cnt

        blob = np.zeros(roi.shape, np.uint8)
        cv2.drawContours(blob, [cnt - np.array([[x0, y0]], np.int32)], -1, 255,
                         cv2.FILLED)
        # The amplitude must come from the blob's INTERIOR: the blurred rim would
        # otherwise drag the percentile toward the background and shrink the cut.
        core = cv2.erode(blob, self._k_close)
        if cv2.countNonZero(core) < 8:
            core = blob
        vals = roi[core > 0]
        if vals.size == 0:
            return cv2.minAreaRect(cnt), cnt

        lvl = max(float(REFINE_MIN_LEVEL),
                  float(np.percentile(vals, REFINE_PCTL)) / 2.0)
        _, m2 = cv2.threshold(roi, lvl, 255, cv2.THRESH_BINARY)
        m2 = cv2.bitwise_and(m2, cv2.dilate(blob, self._k_close))
        cs, _ = cv2.findContours(m2, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cs:
            return cv2.minAreaRect(cnt), cnt
        c2 = max(cs, key=cv2.contourArea)
        if len(c2) < 3:
            return cv2.minAreaRect(cnt), cnt
        c2 = c2 + np.array([[[x0, y0]]], np.int32)
        return cv2.minAreaRect(c2), c2

    def _associate(self, obs: list[dict]) -> None:
        """Greedy nearest-centre association, closest pair first."""
        # Snapshot BEFORE any new track is appended: only tracks that already
        # existed can be said to have missed this frame. Ageing a track that was
        # born this frame zeroes its stability run and the gate never latches.
        existing = list(self._tracks)
        pairs = []
        for oi, o in enumerate(obs):
            for ti, t in enumerate(self._tracks):
                d = float(np.hypot(o["centre_mm"][0] - t.centre_mm[0],
                                   o["centre_mm"][1] - t.centre_mm[1]))
                if d <= MATCH_MAX_MM:
                    pairs.append((d, oi, ti))
        pairs.sort()

        used_o: set[int] = set()
        used_t: set[int] = set()
        for _, oi, ti in pairs:
            if oi in used_o or ti in used_t:
                continue
            used_o.add(oi)
            used_t.add(ti)
            t = self._tracks[ti]
            t.observe(obs[oi]["centre_mm"], obs[oi]["area_mm2"],
                      obs[oi]["measurable"])
            obs[oi]["track"] = t

        for oi, o in enumerate(obs):
            if oi in used_o:
                continue
            t = _Track(self._next_id, o["centre_mm"], o["area_mm2"],
                       o["measurable"])
            self._next_id += 1
            self._tracks.append(t)
            o["track"] = t

        for ti, t in enumerate(existing):
            if ti not in used_t:
                t.missed += 1
                t.stable_run = 0        # a frame without the object breaks the run
        self._tracks = [t for t in self._tracks if t.missed <= MAX_MISSES]

    @staticmethod
    def _emit(obs: list[dict], stamp: Optional[str]) -> list[Placement]:
        out = []
        for o in obs:
            t: _Track = o["track"]
            out.append(Placement(
                id=t.id,
                centre_mm=o["centre_mm"],
                long_edge_mm=o["long_mm"],
                short_edge_mm=o["short_mm"],
                area_mm2=o["area_mm2"],
                angle_deg=o["angle_deg"],
                stable=t.stable,
                frames_seen=t.frames_seen,
                measurable=o["measurable"],
                reason=o["reason"],
                contour_area_mm2=o["cnt_area_mm2"],
                fill_ratio=o["fill"],
                stable_run=t.stable_run,
                t_iso=stamp,
            ))
        out.sort(key=lambda p: p.id)
        return out
