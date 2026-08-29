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
      -> 50 %-amplitude refit, floored so it cannot lose the object
      -> 5-frame stability gate
      -> millimetres on the plane

The two morphology stages are not decoration and are not taken on faith: they
are held in `self._morph` as an ordered list so a test can delete exactly one of
them, and tests/test_placement.py::test_MORPHOLOGY_* runs one counter scene four
ways and fails if either deletion leaves the bill unchanged. Measured there —
delete OPEN and a trail of crumbs welds two packets into one contour; delete
CLOSE and one sachet is billed as two halves.

Three refusals are wired in rather than guessed around:

  TOUCHES_BORDER   a contour running into the edge of the rectified buffer has
                   been cropped by the mat, so its true extent is unknown. It
                   is flagged and NEVER measured — the "poora rakhiye" case.
                   Every measured field comes back None and `stable` is forced
                   False so no downstream stage can mistake it for a reading.

  MERGED_CONTOUR   two goods touching each other segment as ONE blob, and one
                   blob is one price. That is a MONEY bug in the undercharging
                   direction, so it is refused rather than measured — the
                   "alag alag rakhiye" case. See _is_merged for the two pieces
                   of evidence and tests/test_placement.py for what each one
                   catches and what it cannot.

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
REFINE_MIN_KEEP = 0.70    # the refit adjusts an EDGE; it may not lose the item.
                          # Measured over 484 single objects (84 clean, 400
                          # randomly posed with sigma up to 5), a healthy refit
                          # lands at 0.93..1.06 of the coarse oriented-box area.
                          # One case in 400 — a pale 55 x 23 mm strip at 128 deg
                          # under sigma=3 — shattered to 0.27 and was published
                          # as a confident 20.2 x 17.3 mm, a 35 mm undercharge.
                          # Below this floor the coarse fit is kept instead.

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
REASON_MERGED = "MERGED_CONTOUR"      # UI copy: "alag alag rakhiye"

# --- two-object refusal ------------------------------------------------------
# One contour is one price, so a contour that is actually TWO goods undercharges
# — the failure direction money must never take. Two independent signals are
# taken, because neither one alone sees both ways objects merge. Both are
# published on the Placement so the refusal is auditable.
#
#   fill_ratio      contour area / oriented-box area. Two packets meeting at an
#                   angle or an offset leave the box mostly empty. Measured on
#                   the synthetic sweep: 84 single rectangles never read below
#                   0.965, a round tin (the worst-filling SINGLE item there is,
#                   pi/4 = 0.785 in theory) reads 0.781..0.792, and six merged
#                   pairs read 0.550..0.686. The gate goes under the tin and
#                   over the merges. It CANNOT see two packets laid flush, which
#                   fill a rectangle perfectly (measured: 1.000) — that is what
#                   the second signal is for.
#
#   components      how many goods-sized pieces the 50 %-amplitude refit finds
#                   inside one coarse blob. Two flush packets are joined only by
#                   the morphological CLOSE and by the blurred rim between them;
#                   re-cut at half their own amplitude they fall apart again.
#                   Measured: 1 for every one of 496 single objects — the whole
#                   size/angle/polarity sweep, 400 randomly posed objects at up
#                   to sigma=5, and round items — and 2 for a flush or
#                   end-to-end pair.
#
# "Goods-sized" is MIN_AREA_MM2 and nothing else. A relative floor was tried
# first — a piece had to be some fraction of the whole blob — and it was deleted
# because it bought nothing (0 false refusals either way over those 496 objects)
# while blinding the signal to the unequal case: a small packet flush against a
# large one is exactly the merge a shopkeeper makes, and the small one is still
# a price.
MERGED_MIN_FILL = 0.75

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

    fill_ratio and components are EVIDENCE, not measurements, so they survive a
    MERGED_CONTOUR refusal: they are the two numbers that caused it, and the
    ledger has to be able to show which one fired. On a measured placement they
    describe the refined contour that was actually measured; on a refusal they
    describe the coarse blob that was refused. TOUCHES_BORDER publishes neither,
    because a cropped contour's area is as unknown as its edges.
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
    components: Optional[int] = None
    stable_run: int = 0
    t_iso: Optional[str] = None


def _box_area_px(rect) -> float:
    """Oriented-box area of a cv2.minAreaRect, in buffer pixels."""
    (_, _), (w, h), _ = rect
    return float(w) * float(h)


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
        # The mask cleanup, as an ORDERED list rather than two inlined calls.
        # Order is load-bearing: OPEN first kills the speckle that CLOSE would
        # otherwise weld into bridges, and a bridge merges two prices into one.
        # It is a list so a test can delete exactly one stage and measure what
        # changes — see tests/test_placement.py::test_MORPHOLOGY_*.
        self._morph: tuple[tuple[int, np.ndarray], ...] = (
            (cv2.MORPH_OPEN, self._k_open),
            (cv2.MORPH_CLOSE, self._k_close),
        )
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
        for op, kernel in self._morph:
            mask = cv2.morphologyEx(mask, op, kernel)

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
            _, _, _, c_box = self._measure_box(coarse)
            if c_box < self._min_area_mm2:
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
                    "components": None,
                })
                continue

            rect, refined, parts = self._refine(diff, cnt)

            # The two-object test is taken on the COARSE contour, before the
            # refit, because the refit picks the LARGEST piece of whatever it is
            # handed: on two packets laid flush it silently returns one of them
            # and the pair is billed as a single item. What arrived connected is
            # the fact that has to be judged.
            coarse_area_mm2 = self._contour_area_mm2(cnt)
            coarse_fill = coarse_area_mm2 / c_box if c_box > 0 else None
            if self._is_merged(coarse_fill, c_box, parts):
                obs.append({
                    "centre_mm": (float(coarse[0][0]) / PX_PER_MM_X,
                                  float(coarse[0][1]) / PX_PER_MM_Y),
                    "measurable": False,
                    "reason": REASON_MERGED,
                    "long_mm": None, "short_mm": None, "area_mm2": None,
                    "angle_deg": None,
                    "cnt_area_mm2": coarse_area_mm2,
                    "fill": coarse_fill,
                    "components": parts,
                })
                continue

            long_v, long_mm, short_mm, box_area_mm2 = self._measure_box(rect)
            if box_area_mm2 < self._min_area_mm2:
                rejected += 1
                continue

            cnt_area_mm2 = self._contour_area_mm2(refined)
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
                "components": parts,
            })

        self.last_rejected_small = rejected
        return mask, obs

    @staticmethod
    def _contour_area_mm2(cnt: np.ndarray) -> float:
        """Pixel-count area of a contour, in mm^2, via Pick's theorem.

        #lattice points = A + B/2 + 1. Exact for axis-aligned lattice polygons,
        approximate on diagonals. It is EVIDENCE, never the reading: the
        load-bearing number is long_edge_mm from minAreaRect.
        """
        a_px = (float(cv2.contourArea(cnt))
                + float(cv2.arcLength(cnt, True)) / 2.0 + 1.0)
        return a_px / (PX_PER_MM_X * PX_PER_MM_Y)

    def _is_merged(self, fill: Optional[float], box_area_mm2: float,
                   parts: int) -> bool:
        """Is this one blob actually two goods? Either signal is sufficient.

        The fill test is skipped on anything too small to BE two admissible
        items: under 2 x MIN_AREA_MM2 a low fill is a ragged little blob, not a
        pair, and refusing it would cost an abstention for nothing.
        """
        if parts >= 2:
            return True
        if box_area_mm2 < 2.0 * self._min_area_mm2:
            return False
        return fill is not None and fill < MERGED_MIN_FILL

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

        Returns (minAreaRect, contour, components) in full-buffer coordinates,
        falling back to the coarse fit whenever the refit would be degenerate.
        It never wanders: the refit is masked by a dilation of the coarse blob,
        so it can only adjust an edge by a pixel or two, never merge with a
        neighbour and never conjure a new object out of noise. REFINE_MIN_KEEP
        is what makes the "a pixel or two" half of that true in both directions
        — without it a low-contrast blob can shatter under the refit and its
        largest crumb is published as the item.

        `components` is how many goods-sized pieces the refit fell into. It is
        reported rather than silently discarded: returning max(cs) alone is how
        two packets laid flush used to be billed as one. A piece counts when it
        is over MIN_AREA_MM2 — the same floor that decides whether anything on
        the mat is goods at all, so a crumb is a crumb here too.
        """
        x, y, w, h = cv2.boundingRect(cnt)
        x0 = max(0, x - REFINE_MARGIN_PX)
        y0 = max(0, y - REFINE_MARGIN_PX)
        x1 = min(BUF_W, x + w + REFINE_MARGIN_PX)
        y1 = min(BUF_H, y + h + REFINE_MARGIN_PX)
        roi = diff[y0:y1, x0:x1]
        if roi.size == 0:
            return cv2.minAreaRect(cnt), cnt, 1

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
            return cv2.minAreaRect(cnt), cnt, 1

        lvl = max(float(REFINE_MIN_LEVEL),
                  float(np.percentile(vals, REFINE_PCTL)) / 2.0)
        _, m2 = cv2.threshold(roi, lvl, 255, cv2.THRESH_BINARY)
        m2 = cv2.bitwise_and(m2, cv2.dilate(blob, self._k_close))
        cs, _ = cv2.findContours(m2, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cs:
            return cv2.minAreaRect(cnt), cnt, 1

        areas = [float(cv2.contourArea(c)) for c in cs]
        floor_px = self._min_area_mm2 * PX_PER_MM_X * PX_PER_MM_Y
        parts = max(1, sum(1 for a in areas if a >= floor_px))

        c2 = cs[int(np.argmax(areas))]
        if len(c2) < 3:
            return cv2.minAreaRect(cnt), cnt, parts
        c2 = c2 + np.array([[[x0, y0]]], np.int32)

        coarse_rect = cv2.minAreaRect(cnt)
        fine_rect = cv2.minAreaRect(c2)
        # An oriented-box area ratio is scale-only, so pixels and millimetres
        # give the same number here (an affine scaling changes area by its
        # determinant whatever the rotation).
        if _box_area_px(fine_rect) < REFINE_MIN_KEEP * _box_area_px(coarse_rect):
            # The refit was meant to move an edge by a pixel and lost the object
            # instead. Publishing its largest crumb would be a confident
            # measurement of a quarter of a packet, so the coarse fit stands.
            return coarse_rect, cnt, parts
        return fine_rect, c2, parts

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
                components=o["components"],
                stable_run=t.stable_run,
                t_iso=stamp,
            ))
        out.sort(key=lambda p: p.id)
        return out
