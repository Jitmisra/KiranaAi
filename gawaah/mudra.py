"""S6 MUDRA — the hand read as an OCCLUDER of a calibrated plane.

WHY THERE IS NO HAND MODEL IN THIS FILE
---------------------------------------
MediaPipe's ``hand_landmarker.task`` is 7,819,105 bytes against this rig's
4.8 MB cold-load budget, and invariant 3 says *zero model weights in the
browser*. A pinch detector here is not expensive, it is structurally
forbidden. So the hand is never recognised — it is **occulted**.

The method is borrowed wholesale from astronomical occultation photometry
and industrial shadowgraph metrology: you characterise a body you cannot
resolve by what it hides from a field whose photometry you already own. The
TAKHTI *is* that field. We keep an empty-mat reference of the rectified
buffer; anything that differs from it is an occluder; and an occluder is
described by three scalars that need no learned prior:

    solidity    = contourArea / convexHullArea
    compactness = 4*pi*area / perimeter^2          (1.0 == a disc)
    defects     = count of convexity defects deeper than MIN_DEFECT_DEPTH_MM

Reference values from prior research on real hands (SIX.md §"MUDRA"):

    fist  ~0.73  ·  open palm ~0.92  ·  goods 0.96-1.00

so an OPEN palm separates from GOODS mainly by *defect count and
compactness* (a splayed hand has four deep inter-finger notches and a long
perimeter; a packet has neither), and from a FIST mainly by *solidity*.

WHAT THIS MODULE REFUSES TO DO
------------------------------
- It never mints (invariant 5: only ``paisa.mint`` mints, server-side).
  MUDRA *reveals* a target that was already minted. A misread hand is
  therefore financially inert by construction.
- It never emits RED, and it never guesses. When the three channels
  disagree, the answer is ``AMBIGUOUS`` with a named reason code, which is
  a first-class outcome and not an error (invariant 7).
- It retains no input buffer. ``update()`` reads the rectified crop and
  keeps only scalars (invariant 4).

HONEST LIMITS, stated here so nobody has to discover them at the counter:
- The published thresholds are the *shape* of the decision, not calibrated
  constants for a given shop. SIX.md §6 step 8 requires fitting them live
  on 10 open palms and 10 fists under the shoot lighting, and refusing to
  arm the solidity channel if the measured p95(open)-p05(fist) gap is
  < 0.08. This module exposes every threshold as a constructor argument
  precisely so that calibration has somewhere to write.
- The shadow suppressor removes only *soft* multiplicative attenuation
  (see ``SHADOW_RATIO_LO``). A hard shadow is not suppressed and is
  absorbed by the abstention band instead. See ``_foreground``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import cv2
import numpy as np

from gawaah.takhti import BUF_H, BUF_W, MAT_H_MM, MAT_W_MM, PX_PER_MM_X, PX_PER_MM_Y

__all__ = [
    "STATES", "REASONS", "GestureState", "ShapeMetrics", "OccluderGesture",
    "MudraError", "measure_mask", "PX_PER_MM_ISO", "COMPACTNESS_DISC_CEILING",
]

# The buffer is very nearly isotropic (2.82828 vs 2.82857 px/mm, 0.010 %
# apart), so a single scalar is honest for *lengths*. Areas still use both
# axes, because a 0.01 % length error is a 0.02 % area error and free to
# carry correctly.
PX_PER_MM_ISO = (PX_PER_MM_X + PX_PER_MM_Y) / 2.0
PX2_PER_MM2 = PX_PER_MM_X * PX_PER_MM_Y

STATES = ("NONE", "OPEN", "FIST", "GOODS", "AMBIGUOUS")

# Every verdict cause this module can emit, named. Published so a caller can
# aggregate abstention rate *by cause* -- SIX.md makes abstention rate per
# feature a first-class number, and a rate without causes is not diagnosable.
#
# THIS TUPLE IS A CONTRACT, ENFORCED FROM BOTH ENDS.
#   soundness    GestureState.__post_init__ REFUSES to carry a reason whose
#                head is not in here, so an unpublished code cannot escape
#                this module at runtime even once.
#   completeness tests/test_mudra.py AST-walks this file, enumerates every
#                literal that can reach a reason position, and fails if the
#                two sets differ in either direction -- or if a reason
#                position holds an expression it cannot enumerate.
#   liveness     the same test drives a shape through every emission SITE and
#                line-traces mudra.py to prove each one is reachable.
# A regex over the source is NOT sufficient for the completeness half; the
# measured miss rate of the regex that used to stand here is 5 of 7 smuggling
# routes (test_the_reason_enumerator_sees_what_a_regex_cannot).
REASONS = (
    "no_occluder",
    "occluder_too_large",
    "closed_hand",
    "open_palm",
    "inert_object",
    "low_solidity_but_articulated",
    "mid_solidity_too_few_defects",
    "mid_solidity_outline_too_compact",
    "goods_solidity_but_articulated",
    "goods_solidity_but_elongated",
    "hand_area_implausible",
    "solidity_dead_band",
)

# MEASURED, not assumed. cv2.arcLength walks a Freeman chain, so a curved
# raster boundary is ~5.5 % longer than the smooth curve it samples, and
# compactness -- which squares the perimeter -- reads ~11 % low. A perfect
# rasterised disc measures 0.891-0.898 across radii 50-400 px on this buffer,
# never 1.000. Axis-aligned rectangles are unaffected (their chains are
# exact). EVERY compactness threshold below is on this raster scale.
COMPACTNESS_DISC_CEILING = 0.897

# --- foreground extraction ---------------------------------------------------
DIFF_THRESH = 30          # grey levels; below this is sensor noise, not an occluder
MORPH_MM = 2.0            # speckle smaller than 2 mm is not a hand
SHADOW_RATIO_LO = 0.70    # see _foreground: only *soft* attenuation is a shadow
SHADOW_RATIO_HI = 0.97

# --- contour admission -------------------------------------------------------
MIN_AREA_MM2 = 1200.0     # ~35x35 mm. Smaller than any hand or saleable packet.
MAX_AREA_FRAC = 0.55      # more than half the mat changed == a light change,
                          # not an occluder. Refuse rather than measure it.

# --- shape thresholds --------------------------------------------------------
MIN_DEFECT_DEPTH_MM = 6.0     # inter-finger notches measure 10-40 mm; 6 mm
                              # rejects contour ripple without touching them
GOODS_COMPACTNESS_MIN = 0.45  # a 4:1 rectangle measures 0.503 and a 5:1 one
                              # 0.436, so 0.45 admits packets up to ~4.8:1 and
                              # rejects a bare forearm, which is ~10:1
OPEN_COMPACTNESS_MAX = 0.75   # measured ceiling for a five-digit silhouette
                              # across 12-26 deg of finger spread is 0.717;
                              # 0.75 keeps a margin while still excluding a
                              # near-circular blob (raster disc = 0.897)
SOLIDITY_HYSTERESIS = 0.03    # Schmitt half-width on the solidity boundaries
DWELL_FRAMES = 4              # == the 400 ms dwell in the shot list, at 10 fps

# A hand is a known physical size, and the mat exists precisely so that size
# is measurable. This is the metric answer to SIX.md §8.2: when an open hand
# merges with a goods blob the CONTOUR still reads a plausible solidity, but
# its AREA does not -- 63 mm to 148 mm across is a hand, 200 mm across is a
# hand holding a packet, and the honest response to the second is abstention.
HAND_AREA_MM2 = (4000.0, 22000.0)


class MudraError(ValueError):
    """Bad input to the occluder engine. Never raised for an ambiguous hand —
    ambiguity is a GestureState, not an exception."""


@dataclass(frozen=True)
class ShapeMetrics:
    """The three scalars an occulted body is allowed to be described by."""
    solidity: float
    defects: int
    compactness: float
    area_mm2: float
    border_touching: bool


@dataclass(frozen=True)
class GestureState:
    """One frame's verdict.

    ``state`` is the *committed* (hysteresis-filtered) answer — the one a
    caller may act on. ``raw_state`` is this frame's instantaneous
    classification, published so that chatter is visible in the audit trail
    rather than hidden by the filter.
    """
    state: str
    solidity: float
    defects: int
    compactness: float
    area_mm2: float
    reason: str = ""
    raw_state: str = "NONE"
    frames_held: int = 0
    border_touching: bool = False

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise MudraError(f"state {self.state!r} not in {STATES}")
        if self.raw_state not in STATES:
            raise MudraError(f"raw_state {self.raw_state!r} not in {STATES}")
        # SOUNDNESS half of the REASONS contract. A reason code that is not
        # published cannot be aggregated by a caller, which silently turns a
        # published abstention-rate-by-cause into a lie. Refuse to construct
        # the record at all rather than let one escape. The head is the part
        # before the "|" because update() appends dwell telemetry.
        head = self.reason.split("|", 1)[0]
        if head and head not in REASONS:
            raise MudraError(
                f"reason {head!r} is not published in REASONS; an unpublished "
                f"cause cannot be aggregated, so it must not escape this module"
            )

    @property
    def decided(self) -> bool:
        """True only for a state a caller may act on. NONE and AMBIGUOUS are
        both non-actions, for different reasons."""
        return self.state in ("OPEN", "FIST", "GOODS")

    def evidence(self) -> dict:
        """JSON-safe evidence block for the audit ledger (SIX.md §5.4). Every
        number here was measured this frame; none is a configured constant."""
        return {
            "state": self.state,
            "raw_state": self.raw_state,
            "reason": self.reason,
            "solidity": round(self.solidity, 4),
            "defects": int(self.defects),
            "compactness": round(self.compactness, 4),
            "area_mm2": round(self.area_mm2, 1),
            "border_touching": bool(self.border_touching),
            "frames_held": int(self.frames_held),
        }


def _largest_contour(mask: np.ndarray) -> np.ndarray | None:
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    return max(cnts, key=cv2.contourArea)


def _count_deep_defects(contour: np.ndarray, min_depth_mm: float) -> int:
    """Convexity defects deeper than min_depth_mm, in real millimetres.

    OpenCV reports defect depth as a fixed-point integer scaled by 256; that
    factor is undocumented in the Python binding and getting it wrong scales
    every threshold by 256, so it is spelled out rather than folded away.
    """
    if len(contour) < 4:
        return 0
    hull_idx = cv2.convexHull(contour, returnPoints=False)
    if hull_idx is None or len(hull_idx) < 3:
        return 0
    try:
        defects = cv2.convexityDefects(contour, hull_idx)
    except cv2.error:
        return 0
    if defects is None:
        return 0
    depths_mm = (defects.reshape(-1, 4)[:, 3].astype(np.float64) / 256.0) / PX_PER_MM_ISO
    return int((depths_mm >= min_depth_mm).sum())


def measure_mask(
    mask: np.ndarray,
    *,
    min_defect_depth_mm: float = MIN_DEFECT_DEPTH_MM,
) -> ShapeMetrics | None:
    """Measure the largest blob of a binary mask. None if there is no blob.

    Public because live calibration (SIX.md §6 step 8) needs to measure real
    hands with exactly the estimator that will judge them, and because tests
    must be able to synthesise a shape of a *chosen* solidity.
    """
    if mask.ndim != 2:
        raise MudraError(f"mask must be 2-D, got shape {mask.shape}")
    m = mask if mask.dtype == np.uint8 else mask.astype(np.uint8)
    contour = _largest_contour(m)
    if contour is None:
        return None
    area_px = float(cv2.contourArea(contour))
    if area_px <= 0.0:
        return None
    hull_area = float(cv2.contourArea(cv2.convexHull(contour)))
    perim = float(cv2.arcLength(contour, True))
    if hull_area <= 0.0 or perim <= 0.0:
        return None

    h, w = m.shape
    x, y, bw, bh = cv2.boundingRect(contour)
    border = bool(x <= 0 or y <= 0 or x + bw >= w or y + bh >= h)

    return ShapeMetrics(
        solidity=area_px / hull_area,
        defects=_count_deep_defects(contour, min_defect_depth_mm),
        compactness=4.0 * math.pi * area_px / (perim * perim),
        area_mm2=area_px / PX2_PER_MM2,
        border_touching=border,
    )


def _as_gray(img: np.ndarray, what: str) -> np.ndarray:
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if img.ndim != 2:
        raise MudraError(f"{what} must be 2-D grey or 3-channel BGR, got {img.shape}")
    if img.dtype != np.uint8:
        raise MudraError(f"{what} must be uint8, got {img.dtype}")
    return img


class OccluderGesture:
    """Reads a hand as an occluder of the rectified TAKHTI buffer.

    The constructor's first four arguments are the module's published API and
    are exactly the knobs live calibration is expected to overwrite. The
    keyword-only arguments are the rest of the mechanism, exposed so that a
    calibration run can pin them too rather than reaching into globals.

    Args:
        ref_frame: the empty-mat rectified buffer, (BUF_H, BUF_W).
        open_solidity: (lo, hi) solidity band for an open palm.
        fist_solidity_max: solidity strictly below this reads as closed.
        min_defects_open: deep convexity defects an open palm must show.
    """

    def __init__(
        self,
        ref_frame: np.ndarray,
        open_solidity: tuple[float, float] = (0.80, 0.95),
        fist_solidity_max: float = 0.80,
        min_defects_open: int = 3,
        *,
        goods_solidity_min: float | None = None,
        goods_compactness_min: float = GOODS_COMPACTNESS_MIN,
        open_compactness_max: float = OPEN_COMPACTNESS_MAX,
        min_defect_depth_mm: float = MIN_DEFECT_DEPTH_MM,
        hand_area_mm2: tuple[float, float] = HAND_AREA_MM2,
        min_area_mm2: float = MIN_AREA_MM2,
        max_area_frac: float = MAX_AREA_FRAC,
        diff_thresh: int = DIFF_THRESH,
        solidity_hysteresis: float = SOLIDITY_HYSTERESIS,
        dwell_frames: int = DWELL_FRAMES,
        suppress_shadow: bool = True,
        roi_mm: tuple[float, float, float, float] | None = None,
    ) -> None:
        lo, hi = (float(open_solidity[0]), float(open_solidity[1]))
        if not (0.0 < lo < hi <= 1.0):
            raise MudraError(f"open_solidity must satisfy 0 < lo < hi <= 1, got {open_solidity}")
        if not (0.0 < fist_solidity_max <= 1.0):
            raise MudraError(f"fist_solidity_max out of range: {fist_solidity_max}")
        if int(min_defects_open) < 1:
            raise MudraError("min_defects_open must be >= 1; an open palm has notches")
        if int(dwell_frames) < 1:
            raise MudraError("dwell_frames must be >= 1")
        if solidity_hysteresis < 0.0:
            raise MudraError("solidity_hysteresis must be >= 0")
        if not (0.0 < hand_area_mm2[0] < hand_area_mm2[1]):
            raise MudraError(f"hand_area_mm2 must be an increasing positive pair, "
                             f"got {hand_area_mm2}")

        self._open_lo, self._open_hi = lo, hi
        self._fist_max = float(fist_solidity_max)
        # Default: the top of the open band *is* the bottom of goods, so the
        # solidity axis is fully partitioned and abstention comes from the
        # channels DISAGREEING rather than from a hand-tuned dead zone.
        self._goods_min = float(hi if goods_solidity_min is None else goods_solidity_min)
        self._min_defects_open = int(min_defects_open)
        self._goods_comp_min = float(goods_compactness_min)
        self._open_comp_max = float(open_compactness_max)
        self._min_defect_depth_mm = float(min_defect_depth_mm)
        self._hand_area = (float(hand_area_mm2[0]), float(hand_area_mm2[1]))
        self._min_area_mm2 = float(min_area_mm2)
        self._max_area_frac = float(max_area_frac)
        self._diff_thresh = int(diff_thresh)
        self._hyst = float(solidity_hysteresis)
        self._dwell = int(dwell_frames)
        self._suppress_shadow = bool(suppress_shadow)

        # COPIED, not aliased: a caller that reuses its reference buffer for
        # the next frame grab would otherwise silently move our baseline, and
        # every solidity after that would be measured against the wrong plane.
        self._ref = self._check_buffer(ref_frame, "ref_frame").copy()
        self._roi = self._roi_mask(roi_mm)
        k = max(3, int(round(MORPH_MM * PX_PER_MM_ISO)) | 1)
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        self._mat_area_mm2 = MAT_W_MM * MAT_H_MM

        self.reset()

    # ------------------------------------------------------------ lifecycle

    def reset(self) -> None:
        """Clear all temporal state. Called between sessions so one shopper's
        gesture can never dwell into the next shopper's session."""
        self._committed: str = "NONE"
        self._pending: str = "NONE"
        self._pending_count: int = 0
        self._frames_held: int = 0

    def set_reference(self, ref_frame: np.ndarray) -> None:
        """Replace the empty-mat reference. Resets temporal state, because a
        committed state measured against a different plane is meaningless."""
        self._ref = self._check_buffer(ref_frame, "ref_frame").copy()
        self.reset()

    @property
    def committed(self) -> str:
        return self._committed

    # ------------------------------------------------------------- the loop

    def update(self, rectified: np.ndarray) -> GestureState:
        """Classify one rectified frame.

        The input is read and released: nothing derived from it larger than a
        scalar is stored on the instance (invariant 4).
        """
        cur = self._check_buffer(rectified, "rectified")
        mask = self._foreground(cur)
        met = measure_mask(mask, min_defect_depth_mm=self._min_defect_depth_mm)

        if met is None or met.area_mm2 < self._min_area_mm2:
            raw, reason = "NONE", "no_occluder"
            schmitt = "NONE"
            met = met or ShapeMetrics(0.0, 0, 0.0, 0.0, False)
        elif met.area_mm2 > self._max_area_frac * self._mat_area_mm2:
            # A blob this large is a lighting change or a lost mat lock, not a
            # hand. Refuse loudly instead of measuring the room.
            raw, reason = "NONE", "occluder_too_large"
            schmitt = "NONE"
        else:
            # raw_state is the UNFILTERED verdict, published so that chatter is
            # visible in the audit trail. Only the Schmitt-widened verdict is
            # allowed to move the committed state.
            raw, reason = self._classify(met, self._neutral_bands())
            schmitt, _ = self._classify(met, self._bands())

        state = self._commit(schmitt)
        return GestureState(
            state=state,
            solidity=met.solidity,
            defects=met.defects,
            compactness=met.compactness,
            area_mm2=met.area_mm2,
            reason=reason if state == raw else f"{reason}|dwell_{self._pending_count}/{self._dwell}",
            raw_state=raw,
            frames_held=self._frames_held,
            border_touching=met.border_touching,
        )

    def update_many(self, frames: Iterable[np.ndarray]) -> list[GestureState]:
        """Convenience for replay and calibration; identical to looping."""
        return [self.update(f) for f in frames]

    # ------------------------------------------------------------ internals

    @staticmethod
    def _check_buffer(img: np.ndarray, what: str) -> np.ndarray:
        g = _as_gray(np.asarray(img), what)
        if g.shape != (BUF_H, BUF_W):
            raise MudraError(
                f"{what} must be the rectified TAKHTI buffer {(BUF_H, BUF_W)}, "
                f"got {g.shape}. Millimetres are only real on that buffer."
            )
        return g

    @staticmethod
    def _roi_mask(roi_mm: tuple[float, float, float, float] | None) -> np.ndarray | None:
        """Crop the gesture search to the pay panel (SIX.md §8.1, 'Spatial').

        Restricting to the merchant-side margin is what keeps the hand that
        places goods from being read as the hand that makes the gesture.
        """
        if roi_mm is None:
            return None
        x, y, w, h = (float(v) for v in roi_mm)
        if w <= 0 or h <= 0:
            raise MudraError(f"roi_mm width/height must be positive, got {roi_mm}")
        if x < 0 or y < 0 or x + w > MAT_W_MM + 1e-6 or y + h > MAT_H_MM + 1e-6:
            raise MudraError(f"roi_mm {roi_mm} falls outside the {MAT_W_MM}x{MAT_H_MM} mm mat")
        m = np.zeros((BUF_H, BUF_W), np.uint8)
        x0, x1 = int(round(x * PX_PER_MM_X)), int(round((x + w) * PX_PER_MM_X))
        y0, y1 = int(round(y * PX_PER_MM_Y)), int(round((y + h) * PX_PER_MM_Y))
        m[y0:y1, x0:x1] = 255
        return m

    def _foreground(self, cur: np.ndarray) -> np.ndarray:
        """Occluder mask: absdiff against the empty-mat reference.

        SHADOW SUPPRESSION (risk R6). A shadow *attenuates* the reference
        multiplicatively — the mat's own texture survives, scaled. An
        occluder *replaces* it. So a pixel that is merely darker than the
        reference by a bounded factor is charged to the shadow, not to the
        hand.

        The band is deliberately narrow: only 3-30 % attenuation is treated
        as shadow. That is a soft penumbra. A hard shadow attenuates far more
        and is NOT suppressed by this test — it inflates the silhouette, and
        the honest defence against that is the abstention band plus the ±45°
        two-LED lighting doctrine, not a cleverer threshold. Stated so the
        limit is a known property rather than a surprise at the counter.
        """
        diff = cv2.absdiff(cur, self._ref)
        fg = (diff >= self._diff_thresh).astype(np.uint8) * 255

        if self._suppress_shadow:
            ref = self._ref.astype(np.float32) + 1.0
            ratio = cur.astype(np.float32) / ref
            shadow = (
                (cur < self._ref)
                & (ratio >= SHADOW_RATIO_LO)
                & (ratio <= SHADOW_RATIO_HI)
            )
            fg[shadow] = 0

        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, self._kernel)
        fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, self._kernel)
        if self._roi is not None:
            fg = cv2.bitwise_and(fg, self._roi)
        return fg

    def _neutral_bands(self) -> tuple[float, float, float, float]:
        """The configured cut points, with no hysteresis applied."""
        return self._fist_max, self._open_lo, self._open_hi, self._goods_min

    def _bands(self) -> tuple[float, float, float, float]:
        """Solidity cut points, widened for whichever state is committed.

        This is the Schmitt half of the anti-chatter design: the band you are
        already in is 2*hysteresis wider than the band you are not in, so a
        solidity sitting on a boundary cannot flip on sensor noise alone.
        """
        fist_max, open_lo, open_hi, goods_min = self._neutral_bands()
        h = self._hyst
        if self._committed == "FIST":
            fist_max, open_lo = fist_max + h, open_lo + h
        elif self._committed == "OPEN":
            fist_max, open_lo = fist_max - h, open_lo - h
            open_hi, goods_min = open_hi + h, goods_min + h
        elif self._committed == "GOODS":
            open_hi, goods_min = open_hi - h, goods_min - h
        return fist_max, open_lo, open_hi, goods_min

    def _classify(self, m: ShapeMetrics,
                  bands: tuple[float, float, float, float]) -> tuple[str, str]:
        """Instantaneous verdict from three channels that must agree.

        Every path where the channels contradict each other returns
        AMBIGUOUS with a reason naming the contradiction. That is invariant 7
        made executable: the alternative to a named abstention is a guess,
        and a guess here is a wrong cancel or a wrong reveal.
        """
        fist_max, open_lo, open_hi, goods_min = bands
        enough_defects = m.defects >= self._min_defects_open
        # A hand verdict additionally has to be hand-SIZED. The mat makes that
        # a measurement rather than an opinion.
        hand_sized = self._hand_area[0] <= m.area_mm2 <= self._hand_area[1]

        if m.solidity < fist_max:
            if enough_defects:
                return "AMBIGUOUS", "low_solidity_but_articulated"
            if not hand_sized:
                return "AMBIGUOUS", "hand_area_implausible"
            return "FIST", "closed_hand"

        if open_lo <= m.solidity <= open_hi:
            if not enough_defects:
                return "AMBIGUOUS", "mid_solidity_too_few_defects"
            if m.compactness > self._open_comp_max:
                return "AMBIGUOUS", "mid_solidity_outline_too_compact"
            if not hand_sized:
                return "AMBIGUOUS", "hand_area_implausible"
            return "OPEN", "open_palm"

        if m.solidity > goods_min:
            if enough_defects:
                return "AMBIGUOUS", "goods_solidity_but_articulated"
            if m.compactness < self._goods_comp_min:
                return "AMBIGUOUS", "goods_solidity_but_elongated"
            return "GOODS", "inert_object"

        # Only reachable when a calibration run leaves a gap between the
        # bands on purpose. Falling in it is exactly what the gap is for.
        return "AMBIGUOUS", "solidity_dead_band"

    def _commit(self, raw: str) -> str:
        """Dwell half of the anti-chatter design.

        A new verdict must survive ``dwell_frames`` consecutive frames before
        it replaces the committed one. At the shoot's 10 fps the default of 4
        is the 400 ms dwell in the shot list. Any interruption resets the
        count, so an alternating raw sequence never commits at all.
        """
        if raw == self._committed:
            self._pending, self._pending_count = raw, 0
            self._frames_held += 1
            return self._committed

        if raw == self._pending:
            self._pending_count += 1
        else:
            self._pending, self._pending_count = raw, 1

        if self._pending_count >= self._dwell:
            self._committed = raw
            self._pending_count = 0
            self._frames_held = 1
        else:
            self._frames_held += 1
        return self._committed
