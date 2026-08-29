"""S6 CHILLA — the rescued PAKKA-PARCHI.

Corroborate a customer's "PAYMENT SUCCESSFUL" screen against our own settlement
mirror **without ever reading the reference string**.

WHY THE REFERENCE STRING IS GONE — this is a photon budget, not a model gap
--------------------------------------------------------------------------
A UPI reference / UTR line renders at 12sp. Its digit stroke is about 0.19 mm.
This rig's rectified plane is 2*sqrt(2) = 2.8284 px/mm (840/297; the brief's
"2 px/mm" is arithmetically wrong). So the stroke lands at

    0.19 mm * 2.8284 px/mm = 0.54 px

against a Nyquist floor of 2 px per stroke. That is short by ~3.7x. It is not
hard to read. **It is not present in the signal.** No recogniser and no
super-resolution recovers information the sensor never sampled (2x SR gets you
to 1.1 px, still under the floor). The same argument kills the on-screen
timestamp, the payer name and the bank last-4.

The hero AMOUNT is 40sp — about 4.45 mm cap height, 12.6 px — and that IS above
the floor. So CHILLA matches on a COMPOSITE KEY of

    (amount_paise, our own capture time)

and never on a string. `legibility()` below computes these numbers at import
time so nobody can quietly type a different one into a slide.

TWO HONEST NOTES ON THE KEY
---------------------------
1. `screen_ts` is *our* clock at the moment we grabbed the frame. It is NOT a
   timestamp read off the customer's screen — that text is below Nyquist too.
2. The amount alone is a weak key. It is only a usable primary key because
   CHILLAR mints every intent at a odd-paise nonce (01..99, never 00), which
   multiplies the key space by 99. `collision_risk()` quantifies exactly what
   that buys, and the test suite measures it against a synthetic trading day.

WHAT THIS MODULE MAY NOT DO
---------------------------
* It never returns GREEN. Invariant 2: only a valid-HMAC webhook against an open
  intent turns a light green. CHILLA corroborates; it does not decide.
* It never returns RED or "FRAUD". A screen we cannot corroborate is AMBER.
  A stale mirror is AMBER. Two matching payments is AMBER (AMBIGUOUS).
* It never returns pixels. `ScreenDetection` carries geometry and scalars only,
  so the customer's screen contents cannot leak through this API.

WHAT THE SCREEN DETECTOR CAN AND CANNOT TELL APART
--------------------------------------------------
`DETECTION_REASONS` is the complete list of things `ScreenFinder.detect()` can
say, `REASON_NOTES` explains each one, and `LIMITATIONS` states where the gates
stop working. The test suite parses this module's AST and fails unless the
published list is exactly the set of reasons the code emits AND every one of
them is produced by a real frame — so an abstention reason cannot be advertised
here unless it can actually fire.

The reflective/emissive question is answered by illumination coupling (see
MAX_ILLUM_COUPLING), not by brightness. Brightness alone is measured by
MIN_BRIGHTNESS_DELTA, which is named for what it computes because a sheet of
white paper under the counter lamp clears it.
"""
from __future__ import annotations

import bisect as _bisect
import datetime as _dt
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, NoReturn, Sequence

import cv2
import numpy as np

from gawaah.money import MoneyError, paise
from gawaah.takhti import BUF_H, BUF_W, MAT_H_MM, MAT_W_MM, PX_PER_MM, PX_PER_MM_X, PX_PER_MM_Y


class ChillaError(ValueError):
    """Bad input to CHILLA."""


class ChillaRefusal(NotImplementedError):
    """Raised when a caller asks CHILLA to read something that is not in the
    signal. Carries the arithmetic, so the refusal is auditable."""


# =============================================================================
# 1. THE OPTICAL BUDGET — computed, never typed
# =============================================================================

NYQUIST_PX = 2.0                    # two samples per stroke, the hard floor
REFERENCE_STRING_STROKE_MM = 0.19   # 12sp UPI reference/UTR digit stroke
SCREEN_TIMESTAMP_STROKE_MM = 0.19   # same type size, same verdict
HERO_AMOUNT_CAP_MM = 4.45           # 40sp hero amount cap height
SUPER_RES_FACTOR = 2.0              # the best any 2x SR could pretend to add


@dataclass(frozen=True)
class Legibility:
    """Can this feature size survive this sampling rate? Arithmetic only."""

    feature: str
    size_mm: float
    px_per_mm: float
    size_px: float
    nyquist_px: float
    readable: bool
    shortfall_x: float          # how many times short of the floor (1.0 == at it)
    readable_with_2x_sr: bool

    def explain(self) -> str:
        verb = "clears" if self.readable else "is short of"
        return (
            f"{self.feature}: {self.size_mm:.2f} mm at {self.px_per_mm:.4f} px/mm "
            f"= {self.size_px:.2f} px, which {verb} the {self.nyquist_px:.1f} px "
            f"Nyquist floor by {self.shortfall_x:.2f}x. "
            + (
                "It is not hard to read; it is not present in the signal."
                if not self.readable
                else "It is above the floor and may be verified."
            )
        )


def legibility(
    feature: str, size_mm: float, px_per_mm: float = PX_PER_MM
) -> Legibility:
    """Sampling-limit arithmetic for one printed/rendered feature."""
    if size_mm <= 0 or px_per_mm <= 0:
        raise ChillaError(f"non-positive geometry: {size_mm=} {px_per_mm=}")
    size_px = size_mm * px_per_mm
    return Legibility(
        feature=feature,
        size_mm=size_mm,
        px_per_mm=px_per_mm,
        size_px=size_px,
        nyquist_px=NYQUIST_PX,
        readable=size_px >= NYQUIST_PX,
        shortfall_x=NYQUIST_PX / size_px if size_px < NYQUIST_PX else size_px / NYQUIST_PX,
        readable_with_2x_sr=size_px * SUPER_RES_FACTOR >= NYQUIST_PX,
    )


REFERENCE_STRING = legibility("UPI reference string (12sp)", REFERENCE_STRING_STROKE_MM)
SCREEN_TIMESTAMP = legibility("on-screen timestamp (12sp)", SCREEN_TIMESTAMP_STROKE_MM)
HERO_AMOUNT = legibility("hero amount (40sp)", HERO_AMOUNT_CAP_MM)

#: Fields CHILLA structurally refuses to read. Not a policy — an optics result.
NEVER_READ: tuple[str, ...] = (
    "reference_string",
    "utr",
    "rrn",
    "screen_timestamp",
    "payer_name",
    "payer_vpa",
    "bank_last4",
)


def read_reference_string(*_a: Any, **_k: Any) -> NoReturn:
    """Structural refusal. Present so that any caller reaching for the UTR gets
    the arithmetic in the traceback instead of a plausible-looking string."""
    raise ChillaRefusal(REFERENCE_STRING.explain())


def read_screen_timestamp(*_a: Any, **_k: Any) -> NoReturn:
    """Same refusal for the on-screen clock. CHILLA windows on OUR capture time."""
    raise ChillaRefusal(SCREEN_TIMESTAMP.explain())


# =============================================================================
# 2. SCREEN DETECTION ON THE RECTIFIED MAT — no model, deterministic CV
# =============================================================================

# All thresholds are named so a refusal can quote the one that fired.
DELTA_FLOOR = 25          # grey levels; below this a "difference" is just noise
MIN_AREA_MM2 = 2500.0     # ~50x50 mm; smaller is a coin, a note corner, a shadow
MAX_AREA_MM2 = 26000.0    # ~125x208 mm; larger is a tablet or the whole mat
MIN_RECTANGULARITY = 0.80  # contour area / minAreaRect area
MIN_ASPECT = 1.15         # long/short. A phone is not square and not a pencil.
MAX_ASPECT = 3.20
EDGE_MARGIN_MM = 2.0      # quad must sit fully on the mat, not half off it
MAX_MASK_FRACTION = 0.35  # more changed than this is a re-baseline, not a phone
MAX_MEDIAN_SHIFT = 12.0   # grey levels of global AE/AWB drift we tolerate
CLOSE_MM = 4.0            # morphological close, in mm, to bridge glyph gaps
AMBIGUITY_RATIO = 0.70    # second candidate this close in area -> abstain

#: Noise floor for the contour pre-filter, and NOT the same number as
#: MIN_AREA_MM2. It used to be: detect() dropped every contour below
#: MIN_AREA_MM2 before _evaluate() ran, and since a minAreaRect can never be
#: smaller than the contour it encloses, _evaluate()'s own `too_small` branch was
#: unsatisfiable — a published abstention reason that could not fire. The
#: pre-filter's real job is only to throw away specks, so it now says so, and the
#: size decision is taken where the measured rect can be reported with it.
MIN_CONTOUR_AREA_MM2 = 100.0   # ~10x10 mm; below this it is a highlight, not an object

# -- photometry ---------------------------------------------------------------
#: Mean (quad - reference) grey levels. THIS MEASURES BRIGHTNESS, NOT EMISSION.
#: It was once called MIN_EMISSIVE_DELTA, which was a claim the arithmetic does
#: not support: a sheet of white paper under the counter lamp clears 18 grey
#: levels easily. The name now states what is computed; the emission question is
#: asked separately, below.
MIN_BRIGHTNESS_DELTA = 18.0

#: The actual reflective/emissive discriminator, and it is physics rather than
#: threshold-picking. A diffuse reflector's luminance is MULTIPLICATIVE in the
#: light field: paper of albedo a under illumination I(x,y) returns a*I(x,y),
#: and the mat beneath it returns a_mat*I(x,y). So the two carry the SAME
#: spatial gradient and the patch correlates with the reference at r ~ +1. An
#: emissive panel sets its own luminance from its backlight; it does not know
#: where the lamp is, so it correlates at r ~ 0. We pool to ~10 mm cells first so
#: that UI furniture and printed mat texture average away and only the
#: low-frequency illumination survives.
POOL_MM = 10.0                 # side of one pooling cell, millimetres
MIN_COUPLING_CELLS = 12        # fewer cells than this and the correlation is noise
MIN_COUPLING_CONTRAST = 0.60   # pooled reference std, grey levels, to call it measurable
MAX_ILLUM_COUPLING = 0.60      # r at or above this -> reflective, abstain

#: Every reason `ScreenFinder.detect()` can return. The test suite reads the
#: module's own AST and refuses to pass unless this tuple is exactly the set of
#: reasons the code emits AND every one of them is produced by a real frame.
DETECTION_REASONS: tuple[str, ...] = (
    "no_reference",
    "buffer_shape_mismatch",
    "global_illumination_shift",
    "no_bright_region",
    "diff_saturated",
    "all_regions_too_small",
    "too_small",
    "too_large",
    "not_rectangular",
    "aspect_out_of_range",
    "touches_mat_edge",
    "not_brighter_than_mat",
    "reflective_not_emissive",
    "ambiguous_two_bright_quads",
    "screen_found",
)

#: The abstention list proper: everything except the one success reason.
ABSTENTION_REASONS: tuple[str, ...] = tuple(
    r for r in DETECTION_REASONS if r != "screen_found"
)

REASON_NOTES: dict[str, str] = {
    "no_reference": "no REF_EMPTY_MAT has been pushed, or it was cleared after "
                    "takePhoto() muted the track and AE/AWB reconverged.",
    "buffer_shape_mismatch": "the frame is not the rectified mat buffer; CHILLA "
                             "only ever sees the rectified crop (invariant 4).",
    "global_illumination_shift": "the whole scene changed brightness, so every "
                                 "pixel 'differs'. That is a re-baseline, not a phone.",
    "no_bright_region": "less changed than the noise floor "
                        f"({MIN_CONTOUR_AREA_MM2:.0f} mm2). Nothing was put down.",
    "diff_saturated": f"more than {MAX_MASK_FRACTION:.0%} of the mat changed. "
                      "Re-baseline; a phone does not cover a third of an A3 sheet.",
    "all_regions_too_small": "the mat changed, but every individual blob is under "
                             "the noise floor: specks, highlights, foil.",
    "too_small": f"a real candidate whose measured rect is under {MIN_AREA_MM2:.0f} "
                 "mm2. Reported WITH the rect, so the operator sees the miss.",
    "too_large": f"measured rect over {MAX_AREA_MM2:.0f} mm2: a tablet, a sheet, "
                 "or the mat itself.",
    "not_rectangular": f"fill ratio under {MIN_RECTANGULARITY}: the bright region "
                       "is not a quadrilateral, so its rect is a fiction.",
    "aspect_out_of_range": f"long/short outside [{MIN_ASPECT}, {MAX_ASPECT}]: not "
                           "a handset silhouette.",
    "touches_mat_edge": f"within {EDGE_MARGIN_MM} mm of the mat border, so the "
                        "quad may be clipped and its size cannot be trusted.",
    "not_brighter_than_mat": f"mean brightness delta under {MIN_BRIGHTNESS_DELTA} "
                             "grey levels. NOTE: this gate measures BRIGHTNESS "
                             "ONLY. Passing it does not establish emission — see "
                             "reflective_not_emissive and LIMITATIONS.",
    "reflective_not_emissive": f"illumination coupling r >= {MAX_ILLUM_COUPLING}: "
                               "the patch tracks the reference's own light "
                               "gradient, which is what a diffuse reflector does "
                               "and an emissive panel does not.",
    "ambiguous_two_bright_quads": "two plausible screens on the mat; abstain "
                                  "rather than pick one (invariant 7).",
    "screen_found": "a phone-sized, phone-shaped, brighter-than-mat quad that "
                    "does not track the lamp. Geometry only — never pixels.",
}

#: Published next to the reasons, because a gate whose limits are only in a
#: designer's head is a claim, not a measurement.
LIMITATIONS = """\
1. BRIGHTNESS IS NOT EMISSION. `min_brightness_delta` measures exactly one
   thing: how many grey levels brighter than the empty mat the quad is. A sheet
   of white paper under the counter lamp passes it. It is a necessary condition,
   never a sufficient one, and it is named for what it measures.
2. THE COUPLING TEST NEEDS A LIGHT GRADIENT. The reflective/emissive
   discriminator works by correlating the patch against the reference's own
   illumination falloff. Under a FLAT, uniform light field there is no gradient
   to correlate with, the correlation is undefined, and CHILLA reports
   `coupling_measurable=False` rather than pretending the test ran. The honest
   cost is explicit: under uniform light, paper is NOT rejected. The synthetic
   flat-field mat in the test suite is exactly this case, and the suite asserts
   the limitation instead of hiding it.
3. THE FAILURE DIRECTION IS ABSTENTION. A spurious coupling makes CHILLA refuse
   a real screen (amber), never accept a fake one. A glossy phone reflecting the
   room, or a phone lying in the lamp's own specular lobe, can be refused this
   way. Under invariant 7 that is the correct direction to be wrong in.
4. NONE OF THIS IS ANTI-SPOOF. A second phone playing a video of a payment
   screen is emissive, phone-shaped and uncorrelated with the lamp. CHILLA does
   not claim to detect it; that is what the ledger match and the webhook
   predicate are for. CHILLA corroborates, it never decides.
"""

#: The printed placement box (ROKO). Advisory only: reported, never gating.
PLACEMENT_BOX_MM = (68.5, 105.0, 228.5, 315.0)   # x0, y0, x1, y1


@dataclass(frozen=True)
class MmRect:
    """A rotated rectangle on the mat, in real millimetres."""

    cx_mm: float
    cy_mm: float
    w_mm: float        # short side
    h_mm: float        # long side
    angle_deg: float   # long axis vs the mat's +y (down-mat), in (-90, 90]

    @property
    def area_mm2(self) -> float:
        return self.w_mm * self.h_mm

    @property
    def aspect(self) -> float:
        return self.h_mm / self.w_mm if self.w_mm > 0 else math.inf

    def as_dict(self) -> dict:
        return {
            "cx_mm": round(self.cx_mm, 3),
            "cy_mm": round(self.cy_mm, 3),
            "w_mm": round(self.w_mm, 3),
            "h_mm": round(self.h_mm, 3),
            "angle_deg": round(self.angle_deg, 3),
        }


@dataclass(eq=False)
class ScreenDetection:
    """Result of looking for an emissive screen on the rectified mat.

    PRIVACY: this object holds geometry and scalars ONLY. `quad_mm` and
    `quad_buf` are (4,2) coordinate arrays. No crop of the customer's screen is
    returned, stored or logged by this module (invariant 4 / PAKKA privacy).
    """

    found: bool
    reason: str
    rect_mm: MmRect | None = None
    quad_mm: np.ndarray | None = None     # (4,2) TL,TR,BR,BL in mm
    quad_buf: np.ndarray | None = None    # (4,2) same, in rectified buffer px
    mean_luma: float = 0.0                # mean grey inside the quad, current frame
    delta_luma: float = 0.0               # mean(current - reference) inside the quad
    rectangularity: float = 0.0
    area_mm2: float = 0.0
    mask_fraction: float = 0.0            # fraction of the mat that changed
    threshold_used: int = 0
    n_candidates: int = 0
    in_placement_box: bool = False
    #: Pearson r between the pooled quad and the pooled reference beneath it.
    #: ~+1 = the patch obeys the lamp (diffuse reflector); ~0 = it does not
    #: (emissive panel). None when the correlation is undefined.
    illum_coupling: float | None = None
    #: Pooled reference std inside the quad, grey levels: how much illumination
    #: gradient there was to correlate against in the first place.
    ref_contrast: float = 0.0
    #: False means the light field was too flat to run the test at all. It does
    #: NOT mean the object is emissive — see LIMITATIONS.
    coupling_measurable: bool = False

    def as_dict(self) -> dict:
        """Auditable summary. Deliberately contains no pixels."""
        return {
            "found": self.found,
            "reason": self.reason,
            "rect_mm": self.rect_mm.as_dict() if self.rect_mm else None,
            "mean_luma": round(self.mean_luma, 2),
            "delta_luma": round(self.delta_luma, 2),
            "rectangularity": round(self.rectangularity, 4),
            "area_mm2": round(self.area_mm2, 1),
            "mask_fraction": round(self.mask_fraction, 5),
            "threshold_used": int(self.threshold_used),
            "n_candidates": int(self.n_candidates),
            "in_placement_box": bool(self.in_placement_box),
            "illum_coupling": (None if self.illum_coupling is None
                               else round(self.illum_coupling, 4)),
            "ref_contrast": round(self.ref_contrast, 4),
            "coupling_measurable": bool(self.coupling_measurable),
        }


def _gray(img: np.ndarray) -> np.ndarray:
    a = np.asarray(img)
    if a.ndim == 3:
        a = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    if a.dtype != np.uint8:
        a = np.clip(a, 0, 255).astype(np.uint8)
    return a


def buffer_to_mm(pts_buf: np.ndarray) -> np.ndarray:
    """Rectified buffer pixels -> mat millimetres. Inverse of takhti.mm_to_buffer."""
    out = np.asarray(pts_buf, dtype=np.float64).copy().reshape(-1, 2)
    out[:, 0] /= PX_PER_MM_X
    out[:, 1] /= PX_PER_MM_Y
    return out


def _order_quad(pts: np.ndarray) -> np.ndarray:
    """Order 4 points TL, TR, BR, BL by the standard sum/difference trick."""
    p = np.asarray(pts, dtype=np.float64).reshape(4, 2)
    s = p.sum(axis=1)
    d = p[:, 0] - p[:, 1]
    return np.array([p[np.argmin(s)], p[np.argmax(d)],
                     p[np.argmax(s)], p[np.argmin(d)]], dtype=np.float64)


def _rect_from_quad_mm(q: np.ndarray) -> MmRect:
    """Build the metric rect from the ordered mm quad.

    Sides are measured in mm from the mm corners rather than converted from the
    px rect, because px/mm differs by 0.01% between the axes and a rotated rect
    would otherwise inherit that anisotropy as a fake size error.
    """
    tl, tr, br, bl = q
    side_a = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2.0   # "width"
    side_b = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2.0   # "height"
    if side_b >= side_a:
        short, long_ = float(side_a), float(side_b)
        long_vec = ((bl - tl) + (br - tr)) / 2.0
    else:
        short, long_ = float(side_b), float(side_a)
        long_vec = ((tr - tl) + (br - bl)) / 2.0
    ang = math.degrees(math.atan2(float(long_vec[0]), float(long_vec[1])))
    while ang > 90.0:
        ang -= 180.0
    while ang <= -90.0:
        ang += 180.0
    cx, cy = q.mean(axis=0)
    return MmRect(float(cx), float(cy), short, long_, ang)


def _illumination_coupling(
    cur: np.ndarray, ref: np.ndarray, quad_buf: np.ndarray
) -> tuple[float | None, float, int]:
    """Does this patch obey the reference's light field?

    Returns ``(r, ref_contrast, n_cells)``.

    Both images are mean-pooled into ~POOL_MM cells inside the quad, which
    destroys UI furniture and printed mat texture (both high frequency) and keeps
    the illumination falloff (low frequency). Then:

      * diffuse reflector  ->  patch = albedo * I(x,y), ref = albedo_mat * I(x,y)
                               => r ~ +1
      * emissive panel     ->  patch = backlight, independent of I(x,y)
                               => r ~ 0

    `r` is None when the correlation is undefined: either side flat, or too few
    whole cells inside the quad. `ref_contrast` reports how much gradient there
    was to test against, so a caller can tell "emissive" from "unmeasurable".
    """
    x0, y0 = np.floor(quad_buf.min(axis=0)).astype(int)
    x1, y1 = np.ceil(quad_buf.max(axis=0)).astype(int)
    x0, y0 = max(int(x0), 0), max(int(y0), 0)
    x1, y1 = min(int(x1), ref.shape[1]), min(int(y1), ref.shape[0])
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None, 0.0, 0

    m = np.zeros(ref.shape, np.uint8)
    cv2.fillConvexPoly(m, quad_buf.astype(np.int32), 255)
    cell_px = POOL_MM * PX_PER_MM
    gw = max(2, int(round((x1 - x0) / cell_px)))
    gh = max(2, int(round((y1 - y0) / cell_px)))

    def pool(a: np.ndarray) -> np.ndarray:
        return cv2.resize(a[y0:y1, x0:x1].astype(np.float64), (gw, gh),
                          interpolation=cv2.INTER_AREA)

    # keep only cells lying wholly inside the quad, so the mat outside a rotated
    # phone cannot leak its own gradient into the correlation
    keep = pool(m) >= 250.0
    a, b = pool(cur)[keep], pool(ref)[keep]
    n_cells = int(a.size)
    if n_cells < MIN_COUPLING_CELLS:
        return None, 0.0, n_cells
    contrast = float(b.std())
    if contrast < 1e-6 or float(a.std()) < 1e-6:
        return None, contrast, n_cells
    return float(np.corrcoef(a, b)[0, 1]), contrast, n_cells


def _in_placement_box(q: np.ndarray) -> bool:
    x0, y0, x1, y1 = PLACEMENT_BOX_MM
    return bool(
        q[:, 0].min() >= x0 and q[:, 0].max() <= x1
        and q[:, 1].min() >= y0 and q[:, 1].max() <= y1
    )


class ScreenFinder:
    """Finds the emissive rectangle on the rectified mat by differencing against
    a reference capture of the empty mat (REF_EMPTY_MAT).

    Zero model weights. `absdiff` -> threshold -> largest bright quad -> mm rect.
    Every rejection returns a NAMED reason rather than a guess (invariant 7).
    """

    def __init__(
        self,
        reference: np.ndarray | None = None,
        *,
        min_area_mm2: float = MIN_AREA_MM2,
        max_area_mm2: float = MAX_AREA_MM2,
        min_contour_area_mm2: float = MIN_CONTOUR_AREA_MM2,
        min_rectangularity: float = MIN_RECTANGULARITY,
        min_aspect: float = MIN_ASPECT,
        max_aspect: float = MAX_ASPECT,
        min_brightness_delta: float = MIN_BRIGHTNESS_DELTA,
        max_illum_coupling: float = MAX_ILLUM_COUPLING,
        delta_floor: int = DELTA_FLOOR,
    ) -> None:
        self._ref: np.ndarray | None = None
        if reference is not None:
            self.set_reference(reference)
        self.min_area_mm2 = float(min_area_mm2)
        self.max_area_mm2 = float(max_area_mm2)
        # the speck floor, deliberately far below min_area_mm2 so that the size
        # gate in _evaluate() is reachable and can report the rect that failed it
        self.min_contour_area_mm2 = float(min_contour_area_mm2)
        if self.min_contour_area_mm2 > self.min_area_mm2:
            raise ChillaError(
                f"min_contour_area_mm2 ({self.min_contour_area_mm2}) must not "
                f"exceed min_area_mm2 ({self.min_area_mm2}); a pre-filter above "
                "the size gate makes the 'too_small' reason unreachable"
            )
        self.min_rectangularity = float(min_rectangularity)
        self.min_aspect = float(min_aspect)
        self.max_aspect = float(max_aspect)
        self.min_brightness_delta = float(min_brightness_delta)
        self.max_illum_coupling = float(max_illum_coupling)
        self.delta_floor = int(delta_floor)

    # -- reference ---------------------------------------------------------
    def set_reference(self, rect_buffer: np.ndarray) -> None:
        """Push REF_EMPTY_MAT. Must be a rectified buffer, not a raw frame."""
        g = _gray(rect_buffer)
        if g.shape != (BUF_H, BUF_W):
            raise ChillaError(
                f"reference must be the rectified {BUF_W}x{BUF_H} buffer, got "
                f"{g.shape[1]}x{g.shape[0]}. Rectify first (invariant 4)."
            )
        self._ref = g.copy()

    @property
    def has_reference(self) -> bool:
        return self._ref is not None

    def clear_reference(self) -> None:
        """Called when takePhoto() mutes the track and AE/AWB reconverge."""
        self._ref = None

    # -- detection ---------------------------------------------------------
    def detect(self, rect_buffer: np.ndarray) -> ScreenDetection:
        if self._ref is None:
            return ScreenDetection(False, "no_reference")
        cur = _gray(rect_buffer)
        if cur.shape != self._ref.shape:
            return ScreenDetection(False, "buffer_shape_mismatch")

        ref = self._ref
        med_shift = float(np.median(cur.astype(np.float64))
                          - np.median(ref.astype(np.float64)))
        if abs(med_shift) > MAX_MEDIAN_SHIFT:
            # AE/AWB moved under us. Every pixel "changed"; nothing is a phone.
            return ScreenDetection(False, "global_illumination_shift")

        diff = cv2.absdiff(cur, ref)
        otsu, _ = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        thr = int(max(self.delta_floor, int(round(otsu))))
        mask = np.where(diff >= thr, np.uint8(255), np.uint8(0))

        frac = float(np.count_nonzero(mask)) / float(mask.size)
        # NOISE FLOOR, not the size gate. Using min_area_mm2 here is what made
        # _evaluate()'s `too_small` branch unreachable: minAreaRect area is never
        # smaller than the contour it encloses, so anything that survived a
        # min_area_mm2 pre-filter necessarily passed the min_area_mm2 gate too.
        floor_px = self.min_contour_area_mm2 * PX_PER_MM_X * PX_PER_MM_Y
        if np.count_nonzero(mask) < floor_px:
            return ScreenDetection(False, "no_bright_region", mask_fraction=frac,
                                   threshold_used=thr)
        if frac > MAX_MASK_FRACTION:
            return ScreenDetection(False, "diff_saturated", mask_fraction=frac,
                                   threshold_used=thr)

        k = max(3, int(round(CLOSE_MM * PX_PER_MM)) | 1)
        mask = cv2.morphologyEx(
            mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
        )

        # No `no_contours` branch: morphological closing is extensive (the
        # structuring element contains its anchor), so the mask can only grow,
        # and the check above already proved it has nonzero pixels. findContours
        # therefore cannot come back empty. That reason was published but
        # unreachable, so it is gone rather than left as a false claim.
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        sized = sorted(
            ((float(cv2.contourArea(c)), c) for c in contours),
            key=lambda t: t[0], reverse=True,
        )
        sized = [(a, c) for a, c in sized if a >= floor_px]
        if not sized:
            # the mat changed by more than the floor in total, but no single blob
            # did: scattered specks, highlights, foil. Distinct from "nothing".
            return ScreenDetection(False, "all_regions_too_small",
                                   mask_fraction=frac, threshold_used=thr,
                                   n_candidates=len(contours))

        accepted: list[ScreenDetection] = []
        first_rejection: ScreenDetection | None = None
        for area_px, cnt in sized[:6]:
            d = self._evaluate(cnt, area_px, cur, ref, frac, thr, len(sized))
            if d.found:
                accepted.append(d)
            elif first_rejection is None:
                first_rejection = d

        if not accepted:
            assert first_rejection is not None
            return first_rejection

        best = accepted[0]
        if len(accepted) > 1 and accepted[1].area_mm2 >= AMBIGUITY_RATIO * best.area_mm2:
            # Two plausible screens on the mat. Abstain rather than pick one.
            return ScreenDetection(False, "ambiguous_two_bright_quads",
                                   mask_fraction=frac, threshold_used=thr,
                                   n_candidates=len(accepted))
        return best

    def _evaluate(
        self,
        cnt: np.ndarray,
        area_px: float,
        cur: np.ndarray,
        ref: np.ndarray,
        frac: float,
        thr: int,
        n_cand: int,
    ) -> ScreenDetection:
        box = cv2.boxPoints(cv2.minAreaRect(cnt)).astype(np.float64)
        quad_buf = _order_quad(box)
        quad_mm = buffer_to_mm(quad_buf)
        rect = _rect_from_quad_mm(quad_mm)

        rect_area_px = float(cv2.contourArea(box.astype(np.float32)))
        rectangularity = area_px / rect_area_px if rect_area_px > 0 else 0.0

        common = dict(mask_fraction=frac, threshold_used=thr, n_candidates=n_cand,
                      rect_mm=rect, quad_mm=quad_mm, quad_buf=quad_buf,
                      rectangularity=rectangularity, area_mm2=rect.area_mm2,
                      in_placement_box=_in_placement_box(quad_mm))

        if rect.area_mm2 < self.min_area_mm2:
            return ScreenDetection(False, "too_small", **common)
        if rect.area_mm2 > self.max_area_mm2:
            return ScreenDetection(False, "too_large", **common)
        if rectangularity < self.min_rectangularity:
            return ScreenDetection(False, "not_rectangular", **common)
        if not (self.min_aspect <= rect.aspect <= self.max_aspect):
            return ScreenDetection(False, "aspect_out_of_range", **common)
        if (quad_mm[:, 0].min() < EDGE_MARGIN_MM
                or quad_mm[:, 1].min() < EDGE_MARGIN_MM
                or quad_mm[:, 0].max() > MAT_W_MM - EDGE_MARGIN_MM
                or quad_mm[:, 1].max() > MAT_H_MM - EDGE_MARGIN_MM):
            return ScreenDetection(False, "touches_mat_edge", **common)

        # Photometry, measured inside the filled quad only.
        # No `empty_quad` branch: the size and aspect gates above already
        # guarantee a rect of >= min_area_mm2 with sides in a bounded ratio, so
        # its shortest side is tens of pixels and fillConvexPoly cannot come back
        # empty. That reason was published but unreachable, so it is gone.
        m = np.zeros(cur.shape, np.uint8)
        cv2.fillConvexPoly(m, quad_buf.astype(np.int32), 255)
        inside = m > 0
        mean_luma = float(cur[inside].mean())
        delta = float(cur[inside].astype(np.float64).mean()
                      - ref[inside].astype(np.float64).mean())
        common["mean_luma"] = mean_luma
        common["delta_luma"] = delta
        if delta < self.min_brightness_delta:
            # darker than, or the same as, the empty mat: an object, not a screen.
            # NOTE the name: this is a BRIGHTNESS test. Passing it proves the quad
            # is bright, not that it emits. See LIMITATIONS.
            return ScreenDetection(False, "not_brighter_than_mat", **common)

        # Emission proper: does this patch obey the reference's light field?
        r, contrast, _n = _illumination_coupling(cur, ref, quad_buf)
        measurable = r is not None and contrast >= MIN_COUPLING_CONTRAST
        common["illum_coupling"] = r
        common["ref_contrast"] = contrast
        common["coupling_measurable"] = measurable
        if measurable and r >= self.max_illum_coupling:
            # tracks the lamp -> a diffuse reflector (paper, a card, a wrapper)
            return ScreenDetection(False, "reflective_not_emissive", **common)
        return ScreenDetection(True, "screen_found", **common)


# =============================================================================
# 3. THE LEDGER MATCH — composite key, abstention-first
# =============================================================================

MATCHED = "MATCHED"
NO_MATCH = "NO_MATCH"
AMBIGUOUS = "AMBIGUOUS"
AMBER_STALE = "AMBER_STALE"

VERDICTS: tuple[str, ...] = (MATCHED, NO_MATCH, AMBIGUOUS, AMBER_STALE)

#: Invariant 2. CHILLA corroborates; the webhook decides. Nothing here is GREEN,
#: and a screen we cannot corroborate is never RED and never "fraud".
LIGHT_FOR_VERDICT: dict[str, str] = {v: "AMBER" for v in VERDICTS}

DEFAULT_WINDOW_S = 180
DEFAULT_STALE_THRESHOLD_S = 60.0
MATCHABLE_STATUSES: tuple[str, ...] = ("captured",)

#: CHILLAR key space: the paise nonce is uniform over 01..99, never 00.
CHILLAR_SPACE = 99


def collision_risk(n_in_window: int, key_space: int = CHILLAR_SPACE) -> float:
    """P(at least one OTHER payment in the window carries this exact amount).

    Worst case by construction: assume every other payment in the window has the
    same RUPEE part as ours (same shop, similar baskets), so the only thing
    separating them is the CHILLAR paise nonce, uniform over `key_space` values.
    Then for n payments in the window, n-1 of them are "others" and

        P(collision) = 1 - ((k-1)/k) ** (n-1)

    This is the number the AMBIGUOUS verdict exists to absorb. Reported on every
    MatchResult so the operator sees the key's strength at that moment.
    """
    if key_space < 1:
        raise ChillaError(f"key_space must be >= 1, got {key_space}")
    n = int(n_in_window)
    if n <= 1:
        return 0.0
    return 1.0 - ((key_space - 1) / key_space) ** (n - 1)


def any_collision_risk(n_in_window: int, key_space: int = CHILLAR_SPACE) -> float:
    """Birthday form: P(ANY two payments in the window share an amount)."""
    if key_space < 1:
        raise ChillaError(f"key_space must be >= 1, got {key_space}")
    n = int(n_in_window)
    if n <= 1:
        return 0.0
    if n > key_space:
        return 1.0
    p_distinct = 1.0
    for i in range(n):
        p_distinct *= (key_space - i) / key_space
    return 1.0 - p_distinct


def max_payments_for_risk(
    target_risk: float, key_space: int = CHILLAR_SPACE
) -> int:
    """Largest window occupancy whose collision_risk stays within `target_risk`."""
    if not 0.0 < target_risk < 1.0:
        raise ChillaError(f"target_risk must be in (0,1), got {target_risk}")
    n = 1
    while collision_risk(n + 1, key_space) <= target_risk:
        n += 1
        if n > 10 * key_space:
            break
    return n


@dataclass(frozen=True)
class MirrorRow:
    """One settled payment as our mirror of Razorpay sees it."""

    payment_id: str
    amount_paise: int
    created_at: int                 # unix seconds
    status: str = "captured"
    session_id: str | None = None
    method: str = "upi"

    def __post_init__(self) -> None:
        # money invariant: a float amount is not money and never becomes one
        object.__setattr__(self, "amount_paise", int(paise(self.amount_paise)))
        object.__setattr__(self, "created_at", _to_unix(self.created_at))

    @staticmethod
    def from_razorpay(d: Mapping[str, Any]) -> "MirrorRow":
        """Adapt a Razorpay payment entity (as gawaah.rzp_sim emits it)."""
        notes = d.get("notes") or {}
        return MirrorRow(
            payment_id=str(d["id"]),
            amount_paise=int(d["amount"]),
            created_at=int(d["created_at"]),
            status=str(d.get("status", "captured")),
            session_id=(str(notes["session_id"]) if "session_id" in notes else None),
            method=str(d.get("method", "upi")),
        )

    def as_dict(self) -> dict:
        return {
            "payment_id": self.payment_id,
            "amount_paise": self.amount_paise,
            "created_at": self.created_at,
            "status": self.status,
            "session_id": self.session_id,
            "method": self.method,
        }


@dataclass
class Mirror:
    """A local, read-only mirror of settled payments.

    `fetched_at` is when we last successfully refreshed it. The matcher does not
    read a clock: staleness is always supplied by the caller, so replays are
    byte-identical (see gawaah.clock).
    """

    rows: tuple[MirrorRow, ...] = ()
    fetched_at: int | None = None
    _times: tuple[int, ...] = field(default=(), init=False, repr=False)

    def __post_init__(self) -> None:
        # kept sorted by created_at so a window lookup is a bisect, not a scan:
        # the false-accept experiment runs tens of thousands of matches.
        self.rows = tuple(sorted((_coerce_row(r) for r in self.rows),
                                 key=lambda r: r.created_at))
        self._times = tuple(r.created_at for r in self.rows)
        if self.fetched_at is not None:
            self.fetched_at = _to_unix(self.fetched_at)

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self):
        return iter(self.rows)

    def age_s(self, now: Any) -> float:
        """Seconds since the last successful refresh. Never negative."""
        if self.fetched_at is None:
            return math.inf
        return max(0.0, float(_to_unix(now) - self.fetched_at))

    @staticmethod
    def from_razorpay_collection(
        coll: Mapping[str, Any], *, fetched_at: Any | None = None
    ) -> "Mirror":
        items = coll.get("items", ())
        return Mirror(tuple(MirrorRow.from_razorpay(i) for i in items),
                      fetched_at=fetched_at)


def _coerce_row(r: Any) -> MirrorRow:
    if isinstance(r, MirrorRow):
        return r
    if isinstance(r, Mapping):
        if "payment_id" in r:
            return MirrorRow(**dict(r))
        if "id" in r and "amount" in r:
            return MirrorRow.from_razorpay(r)
    raise ChillaError(f"cannot read a mirror row from {r!r}")


def _to_unix(ts: Any) -> int:
    """Accept unix seconds, a datetime, or an ISO-8601 string (the Clock format)."""
    if isinstance(ts, bool):
        raise ChillaError(f"bool is not a timestamp: {ts!r}")
    if isinstance(ts, int):
        return ts
    if isinstance(ts, float):
        if not math.isfinite(ts):
            raise ChillaError(f"non-finite timestamp: {ts!r}")
        return int(ts)
    if isinstance(ts, _dt.datetime):
        d = ts if ts.tzinfo else ts.replace(tzinfo=_dt.timezone.utc)
        return int(d.timestamp())
    if isinstance(ts, str):
        try:
            d = _dt.datetime.fromisoformat(ts)
        except ValueError as e:
            raise ChillaError(f"not an ISO-8601 timestamp: {ts!r} ({e})") from e
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return int(d.timestamp())
    raise ChillaError(f"unsupported timestamp type {type(ts).__name__}: {ts!r}")


@dataclass(frozen=True)
class MatchResult:
    """What CHILLA is willing to say about one screen.

    `light` is AMBER for every verdict, including MATCHED. A corroborated screen
    is still not a settlement — only the webhook predicate turns a light green.
    """

    verdict: str
    candidates: tuple[MirrorRow, ...]
    reason: str
    amount_paise: int
    screen_ts: int | None
    window_seconds: int
    mirror_age_s: float
    n_in_window: int
    collision_risk: float
    key_space: int
    light: str = "AMBER"

    @property
    def matched(self) -> bool:
        return self.verdict == MATCHED

    @property
    def is_amber(self) -> bool:
        return self.light == "AMBER"

    @property
    def payment(self) -> MirrorRow | None:
        """The single corroborating payment, or None unless verdict is MATCHED."""
        return self.candidates[0] if self.verdict == MATCHED else None

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "light": self.light,
            "amount_paise": self.amount_paise,
            "screen_ts": self.screen_ts,
            "window_seconds": self.window_seconds,
            "mirror_age_ms": int(round(self.mirror_age_s * 1000))
            if math.isfinite(self.mirror_age_s) else -1,
            "n_in_window": self.n_in_window,
            "n_candidates": len(self.candidates),
            "candidate_ids": [c.payment_id for c in self.candidates],
            "collision_risk": f"{self.collision_risk:.6f}",
            "key_space": self.key_space,
        }


class LedgerMatcher:
    """Match a screen against the settlement mirror on (amount, time window).

    The window is SYMMETRIC: a mirror row matches if
        |row.created_at - screen_ts| <= window_seconds
    so the total span considered is 2 * window_seconds. Symmetric because the
    customer's handset clock and ours are not synchronised and a webhook can
    land either side of the moment we grab the frame.

    Verdict rules, in order:
      * mirror stale             -> AMBER_STALE, even if a match exists
      * screen timestamp unknown -> NO_MATCH (amber; invariant 7)
      * exactly one exact-amount payment in window -> MATCHED
      * two or more                                -> AMBIGUOUS
      * none                                       -> NO_MATCH
    NO_MATCH is amber. It is never RED and it is never a fraud accusation: the
    likeliest cause is a slow webhook, not a liar.
    """

    def __init__(
        self,
        mirror: Mirror | Sequence[Any],
        window_seconds: int = DEFAULT_WINDOW_S,
        *,
        stale_threshold_s: float = DEFAULT_STALE_THRESHOLD_S,
        matchable_statuses: Iterable[str] = MATCHABLE_STATUSES,
        key_space: int = CHILLAR_SPACE,
        ledger: Any | None = None,
        clock: Any | None = None,
    ) -> None:
        self.mirror = mirror if isinstance(mirror, Mirror) else Mirror(tuple(mirror))
        if int(window_seconds) <= 0:
            raise ChillaError(f"window_seconds must be positive, got {window_seconds}")
        self.window_seconds = int(window_seconds)
        self.stale_threshold_s = float(stale_threshold_s)
        self.matchable_statuses = frozenset(str(s) for s in matchable_statuses)
        self.key_space = int(key_space)
        self._ledger = ledger
        self._clock = clock

    # -- the predicate -----------------------------------------------------
    def rows_in_window(self, screen_ts: Any) -> list[MirrorRow]:
        """Matchable-status rows whose created_at is inside the symmetric window.

        Returned in created_at order.
        """
        t = _to_unix(screen_ts)
        w = self.window_seconds
        times = self.mirror._times
        lo = _bisect.bisect_left(times, t - w)
        hi = _bisect.bisect_right(times, t + w)
        return [r for r in self.mirror.rows[lo:hi]
                if r.status in self.matchable_statuses]

    def match(
        self,
        amount_paise: Any,
        screen_ts: Any,
        *,
        mirror_age_s: float = 0.0,
    ) -> MatchResult:
        amount = int(paise(amount_paise))   # MoneyError on float/bool/str
        age = float(mirror_age_s)
        if age < 0:
            raise ChillaError(f"mirror_age_s cannot be negative: {mirror_age_s!r}")

        ts: int | None
        if screen_ts is None:
            ts = None
            in_window: list[MirrorRow] = []
        else:
            ts = _to_unix(screen_ts)
            in_window = self.rows_in_window(ts)

        n = len(in_window)
        risk = collision_risk(n, self.key_space)
        exact = tuple(r for r in in_window if r.amount_paise == amount)

        def build(verdict: str, cands: tuple[MirrorRow, ...], reason: str) -> MatchResult:
            res = MatchResult(
                verdict=verdict, candidates=cands, reason=reason,
                amount_paise=amount, screen_ts=ts,
                window_seconds=self.window_seconds, mirror_age_s=age,
                n_in_window=n, collision_risk=risk, key_space=self.key_space,
                light=LIGHT_FOR_VERDICT[verdict],
            )
            self._audit(res)
            return res

        # 1. staleness dominates everything, match or no match
        if age > self.stale_threshold_s:
            return build(
                AMBER_STALE, exact,
                f"mirror {age:.1f}s old > {self.stale_threshold_s:.1f}s threshold; "
                "a stale mirror cannot corroborate and can never accuse",
            )
        # 2. no capture time -> nothing to window on
        if ts is None:
            return build(
                NO_MATCH, (),
                "screen capture time unknown; the composite key needs a time and "
                "the on-screen clock is below Nyquist",
            )
        # 3. the composite-key predicate
        if len(exact) == 1:
            return build(
                MATCHED, exact,
                f"exactly one captured payment of {amount} paise within "
                f"+/-{self.window_seconds}s",
            )
        if len(exact) > 1:
            return build(
                AMBIGUOUS, exact,
                f"{len(exact)} captured payments of {amount} paise within "
                f"+/-{self.window_seconds}s; the key does not separate them",
            )
        return build(
            NO_MATCH, (),
            f"no captured payment of {amount} paise within "
            f"+/-{self.window_seconds}s ({n} payment(s) in window); "
            "amber, not an accusation — the webhook may simply be late",
        )

    # -- audit -------------------------------------------------------------
    def _audit(self, res: MatchResult) -> None:
        """Append one auditable line. No pixels, no payer identity, no strings
        read off the screen — because none were read."""
        if self._ledger is None:
            return
        ts = self._clock.now_iso() if self._clock is not None else "unknown"
        self._ledger.append(ts=ts, module="chilla", **res.as_dict())
