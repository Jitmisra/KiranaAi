"""S6 SAAF — the enrolment frame gate, and the rescued KAMPAN burst stack.

WHY THIS MODULE EXISTS, AND WHAT WAS WRONG WITH ITS ANCESTOR
------------------------------------------------------------
KAMPAN's premise was "your shaking hand is the sensor" — hand tremor supplies
the sub-pixel sampling diversity that multi-frame super-resolution needs.
**On this rig that premise is dead.** The phone is clamped to a gooseneck. There
is no hand tremor. The camera is the most stationary object on the counter.

The rescue is to move the shake to the other side of the lens. During SKU
enrolment the shopkeeper is already asked to nudge and rotate the packet
("thoda ghumaiye"), so the SUBJECT supplies the sampling diversity instead of
the camera. Same mathematics, opposite frame of reference, and unlike the
original it is actually true of the hardware.

WHAT THIS BUYS, STATED HONESTLY
-------------------------------
Two separate things, and they must not be conflated:

1. THE GATE (the part that ships). A saturation-guarded variance-of-Laplacian
   score that rejects blurred and glare-blown crops so one bad capture cannot
   permanently poison a gallery entry. The saturation guard is the load-bearing
   part: **a blown specular highlight MAXIMISES Laplacian variance**, so an
   unguarded sharpness sort actively prefers the worst frame in the burst.
   `test_saaf.py::test_glare_frame_beats_sharp_frame_on_RAW_vlap` pins that
   failure as an executable fact.

   Sitting under it, an ABSOLUTE focus floor, because the vLap gate has a blind
   spot it cannot see out of. Both of its halves are burst-relative in effect:
   `blur_rel_min * max(vLap)` obviously is, and `blur_var_min` only looks
   absolute — vLap is quadratic in contrast and grows with texture density, so
   across four in-focus synthetic scenes it spans a factor of 538 and one
   number cannot mean "in focus" on all of them. A burst where EVERY frame is
   defocused therefore has no worst frame, the relative floor sinks with it,
   and the result comes back with `warning=""` — a confident super-resolution
   claim built entirely out of mush. `blur_score` is scale-free and closes
   that: see its docstring, and section 5b of `stack`.

2. THE STACK (measured, reported with its number whatever it says). Shift-and-add
   super-resolution: register each admitted frame to the sharpest one with
   `cv2.findTransformECC`, splat every low-resolution sample into a `scale`x grid
   at its sub-pixel-correct position, normalise by accumulated weight.

THE HONEST FAILURE MODE, WHICH IS REQUIRED AND NOT OPTIONAL
-----------------------------------------------------------
Super-resolution needs the frames to sample the scene at DIFFERENT sub-pixel
phases. If the subject did not move, every frame samples the identical phase,
the extra frames add no new information, and shift-and-add degenerates to plain
denoising. A stacker that quietly returns a splat result in that condition is
returning a WORSE image than the frame it started with and calling it better.

So `stack()` measures the sub-pixel phase diversity it actually achieved
(circular variance of the sampling phases, not merely the shift magnitude — a
burst displaced by exactly 1.0 px has motion but zero new phase) and:
  - sets a named, loud `warning` describing precisely which degeneracy occurred,
  - and falls back to the denoise path, so `.image` is never worse than the
    single-frame baseline.

Never silently return a degraded image. Abstain loudly (invariant 7).

WHAT THIS MODULE DOES NOT DO
----------------------------
No PSF deconvolution. The sensor's own aperture/anti-alias response is left in
the result, so the recovered MTF is bounded above by that response — the stack
recovers sampling resolution, not lens resolution. No learned prior, no model
weights, no sharpening filter (an unsharp mask would inflate every sharpness
metric here without adding a single bit of real information, which would make
the reported gain a lie).

BUILD TRAP, VERIFIED IN THIS REPO (FAILURES.md, 2026-08-29)
-----------------------------------------------------------
`cv2.findTransformECC` THROWS `cv2.error` when it fails to converge — it does
not return a low correlation coefficient. Two unrelated noise images raise at
`ecc.cpp:597`. Every call here is wrapped, and a throw is treated as FRAME
REJECTED, never as "no motion detected". Reading a throw as zero motion would
invert this module's central honesty check.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

import cv2
import numpy as np

# --- gate defaults ----------------------------------------------------------
DEFAULT_SCALE = 2
DEFAULT_BLUR_VAR_MIN = 60.0    # guarded vLap; below this the crop is not enrollable
# Relative blur gate: reject frames below this fraction of the SHARPEST frame
# in the same burst. An absolute vLap threshold alone is close to meaningless,
# because vLap scales with the scene's own texture and contrast -- measured on
# these synthetic targets a sharp dense-text crop scores ~3.2e5 while a sharp
# low-texture packet crop scores ~6.1e2, a factor of 500 for two frames that
# are both perfectly in focus. Within one burst the subject is fixed, so a
# relative gate is the meaningful one. Measured separation: frames in a clean
# burst hold >= 0.83 of the burst maximum, while a Gaussian blur of sigma 0.8
# drops to 0.045. 0.35 sits in that gap with room on both sides.
DEFAULT_BLUR_REL_MIN = 0.35
DEFAULT_MAX_SHIFT_PX = 10.0    # beyond this the crop shows different content, not the same region
DEFAULT_SAT_LEVEL = 250        # 8-bit level counted as "blown"
DEFAULT_SAT_FRAC_MAX = 0.02    # >2% blown pixels == specular glare, reject

# --- the ABSOLUTE focus floor (see blur_score) -------------------------------
# THE MEASURED BLIND SPOT THIS CLOSES. Both gates above are burst-RELATIVE in
# effect: `blur_rel_min * max(vLap)` sinks with the burst, and `blur_var_min` is
# an absolute number on a quantity (vLap) that is not absolute at all -- a sharp
# dense-text crop scores 3.3e5 and a sharp low-texture packet crop 6.1e2, so one
# threshold cannot mean the same thing on both. Measured consequence: a text
# burst uniformly defocused to sigma 2.4 px keeps vLap at 97, sails past the
# 60.0 floor, has no worst frame for the relative floor to find, and is returned
# with warning="" and used=8/8. The identical frame inside a SHARP burst is
# rejected. Same image, opposite verdict, decided by its neighbours.
#
# blur_score is scale-free by construction and does not have that problem.
BLUR_SCORE_SPAN = 9            # Crete's published low-pass width, in px
DEFAULT_MAX_BLUR_SCORE = 0.46  # above this the crop is out of focus, full stop

# What that number MEANS, measured against this module's own ISO-12233
# instrument (test_ACCEPTANCE_blur_score_is_calibrated_against_MTF50): across
# 10 independent scenes -- base PSF 1.5 to 8.0 HR px x 2 edge slants, spanning
# in-focus MTF50 from 0.179 to 0.513 cyc/px -- the blur score at
# MTF50 = 0.15 cyc/px lands in 0.4573..0.4684, a spread of 0.0112 (+-1.2 %).
# Re-measured on REGISTERED MEANS rather than single frames it lands in
# 0.4594..0.4682, the same window, which is what licenses one constant for
# both applications. So the ceiling is a threshold on RESOLUTION, not texture:
BLUR_SCORE_MTF50_CYC_PX = 0.15   # == 30 % of the low-resolution Nyquist limit
                                 # == a total Gaussian PSF wider than ~1.25 px

# Reconstruction-kernel width, HR px. Swept against ground truth, not guessed;
# see BurstStacker._splat_path.
DEFAULT_SPLAT_SIGMA = 0.30

# --- sub-pixel diversity thresholds ----------------------------------------
DEFAULT_MIN_SHIFT_PX = 0.15    # below this the subject effectively did not move
DEFAULT_MIN_DIVERSITY = 0.10   # circular variance of sampling phase, 0..1

# --- ECC ---------------------------------------------------------------------
ECC_MAX_ITERS = 200
ECC_EPS = 1e-7
ECC_GAUSS_FILT = 5

# --- frame reason codes ------------------------------------------------------
R_REFERENCE = "reference"
R_OK = "ok"
R_BLUR = "blur"
R_GLARE = "glare"
R_ECC_FAILED = "ecc_failed"
R_SHIFT_TOO_LARGE = "shift_too_large"
R_WARP_NOT_FINITE = "warp_not_finite"
R_DEFOCUS = "defocus"

# --- warning codes -----------------------------------------------------------
W_NONE = ""
W_ALL_REJECTED = "ALL_FRAMES_REJECTED"
W_SINGLE_FRAME = "SINGLE_FRAME"
W_NO_DIVERSITY = "NO_SUBPIXEL_DIVERSITY"
W_DEGENERATE_PHASE = "DEGENERATE_SAMPLING_PHASE"
W_UNIFORMLY_DEFOCUSED = "BURST_UNIFORMLY_DEFOCUSED"


class SaafError(ValueError):
    """Raised for programming errors (empty burst, ragged shapes), never for
    an image that merely fails a quality gate — those produce a report."""


# ============================================================================
# sharpness / saturation primitives
# ============================================================================

def _saturation_mask(gray: np.ndarray, level: int) -> np.ndarray:
    """Pixels at or above `level`, dilated by the Laplacian's support.

    Dilation is the point. A blown highlight's damage is not in the flat
    saturated interior (whose Laplacian is ~0) but at its CLIFF EDGE, where a
    hard 0->255 step produces the largest Laplacian response in the frame.
    Masking only the exactly-saturated pixels would leave that cliff in.
    """
    m = (gray >= level).astype(np.uint8)
    if not m.any():
        return m.astype(bool)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    return cv2.dilate(m, k, iterations=1).astype(bool)


def saturated_fraction(gray: np.ndarray, level: int = DEFAULT_SAT_LEVEL) -> float:
    """Fraction of pixels at or above `level`. The glare gate's input."""
    g = _as_gray_u8(gray)
    return float((g >= level).mean())


def variance_of_laplacian(gray: np.ndarray, sat_level: int | None = None) -> float:
    """Variance of the 3x3 Laplacian — the standard blur score.

    With `sat_level` set, the Laplacian is measured only OUTSIDE the dilated
    saturated region. That is the saturation guard, and it is the whole reason
    this function takes the argument: raw vLap ranks a glare-blown frame ABOVE
    a genuinely sharp one, so an unguarded ranking picks the worst frame in the
    burst to enroll. See `test_glare_frame_beats_sharp_frame_on_RAW_vlap`.

    Returns 0.0 when the guard leaves too little of the frame to score, which
    is itself a rejection: a crop that is nearly all glare has no measurable
    sharpness and must not be enrolled.
    """
    g = _as_gray_u8(gray)
    lap = cv2.Laplacian(g.astype(np.float64), cv2.CV_64F, ksize=3)
    if sat_level is None:
        return float(lap.var())
    mask = _saturation_mask(g, sat_level)
    keep = lap[~mask]
    if keep.size < max(64, lap.size // 20):
        return 0.0
    return float(keep.var())


def blur_score(gray: np.ndarray, span: int = BLUR_SCORE_SPAN) -> float:
    """No-reference DEFOCUS score in [0, 1]. Higher is blurrier. Scale-free.

    Crete et al. 2007, "The blur effect: perception and estimation with a new
    no-reference perceptual blur metric". Blur the image again along one axis
    and ask how much neighbour-to-neighbour variation that SECOND blur was able
    to destroy. A sharp image has a lot left to lose; an already-defocused one
    has almost nothing, because its high frequencies are gone already.

        D_f = |first difference of the frame|
        D_b = |first difference of the re-blurred frame|
        lost = max(0, D_f - D_b)              # variation the re-blur removed
        score = (sum(D_f) - sum(lost)) / sum(D_f)

    WHY THIS AND NOT AN ABSOLUTE vLap THRESHOLD, which is what stood here
    before. The score is a RATIO of the same functional applied twice, so every
    multiplicative property of the scene cancels: contrast, exposure, albedo,
    and the sheer amount of texture. Measured on the synthetic targets, vLap
    spans 6.1e2 to 3.3e5 across scenes that are all perfectly in focus -- a
    factor of 500, which is why a single absolute vLap number cannot mean
    "in focus" -- while blur_score holds 0.11..0.30 across the same set and
    rises monotonically with defocus on every one of them.

    It is calibrated, not guessed: across 10 independent scenes the score at
    MTF50 = 0.15 cycles/px lands in 0.457..0.469 (spread 0.012), so
    DEFAULT_MAX_BLUR_SCORE = 0.46 is a threshold on RESOLUTION.

    Two honest limits, both pinned by tests rather than left to be discovered:

    - ADDITIVE NOISE MAKES A BLURRED FRAME LOOK SHARP. Noise is white, the
      re-blur destroys it completely, so `lost` is large and the score falls.
      Measured per-frame: a burst defocused to 2.4 px scores 0.476 clean and
      0.264 with 4 grey levels of sensor noise, and the per-frame gate stops
      firing from 1 LSB upward. That is why the check is ALSO asked of the
      registered mean (stack step 5b), where noise is down by sqrt(N) and the
      defocus is untouched: the same question, asked of an image that can
      answer it. Tolerance measured there is 2-8 LSB depending on the scene.
      Beyond that envelope the burst is admitted and NOTHING in this module
      catches it -- noise inflates vLap too, so the vLap floors are blinded in
      the same direction, and one grey level is enough to take a 4.0 px
      defocus from vLap 19 (rejected) to 105 (admitted). Both halves are
      pinned: test_HONEST_enough_noise_still_hides_a_defocused_burst and
      test_HONEST_sensor_noise_inverts_the_absolute_vLap_floor. The bias has
      ONE direction -- noise can hide a bad burst, it can never condemn a good
      one -- so the gate never causes a false abstention.
    - A FEATURELESS frame scores 0.0, i.e. "sharp". Nothing here can tell a
      blank wall from perfect focus. That case is caught upstream by the
      absolute vLap floor, which a flat frame fails outright.

    Returns the worse (higher) of the two axes: defocus is isotropic, so a
    frame that is sharp along one axis only is motion-smeared, not focused,
    and the smeared axis is the honest one to report.
    """
    if int(span) < 2:
        raise SaafError(f"blur_score span must be >= 2 px, got {span!r}")
    g = _as_gray_u8(gray).astype(np.float64)
    if g.shape[0] < 2 or g.shape[1] < 2:
        raise SaafError(f"blur_score needs at least 2x2 px, got {g.shape}")
    worst = 0.0
    # cv2 Size is (width, height): (1, span) averages down a column, which is
    # the axis-0 difference; (span, 1) averages along a row, i.e. axis 1.
    for axis, ksize in ((0, (1, int(span))), (1, (int(span), 1))):
        b = cv2.blur(g, ksize, borderType=cv2.BORDER_REPLICATE)
        d_f = np.abs(np.diff(g, axis=axis))
        d_b = np.abs(np.diff(b, axis=axis))
        # NOT named `total`: tools/lint_no_float.py reads that as a money
        # identifier, and invariant 1 says money is integer paise. This is
        # summed edge energy in grey levels and has nothing to do with money.
        edge_energy = float(d_f.sum())
        if edge_energy <= 1e-12:
            continue        # no variation along this axis: it has no opinion
        lost = float(np.maximum(0.0, d_f - d_b).sum())
        worst = max(worst, (edge_energy - lost) / edge_energy)
    return float(worst)


def mtf50_slanted_edge(roi: np.ndarray, bin_width: float = 0.25,
                       half_window: float = 20.0) -> float:
    """MTF50 in CYCLES PER PIXEL of `roi`, ISO-12233-style slanted edge.

    `roi` must contain exactly one near-vertical light/dark edge spanning the
    full height, slanted a few degrees so that successive rows sample the edge
    at different sub-pixel phases (that slant is what supersamples the ESF).
    It must also be at least ~2*half_window wide and free of other features.

    ACCURACY, measured against the closed-form Gaussian MTF50 in
    `test_mtf50_matches_closed_form`: within 2% for MTF50 >= 0.10 cycles/px at
    the default half_window, degrading to ~8% at MTF50 = 0.054 where the LSF
    tails no longer fit the window. Every MTF50 this module reports sits in the
    validated band.

    Why this and not variance-of-Laplacian for the headline number: vLap rises
    with NOISE as well as with detail, and stacking removes noise. A vLap
    comparison therefore penalises the stack for the very thing it does well,
    and would be measuring the wrong quantity. MTF50 measures resolution.

    Returns 0.0 if no edge with usable contrast is found.
    """
    a = np.asarray(roi, dtype=np.float64)
    if a.ndim != 2 or a.shape[0] < 16 or a.shape[1] < 16:
        raise SaafError(f"mtf50 roi must be a 2-D image at least 16x16, got {a.shape}")
    h, w = a.shape

    # --- per-row sub-pixel edge location, by centroid of |d/dx| --------------
    d = np.abs(np.diff(a, axis=1))
    xs = np.arange(w - 1, dtype=np.float64) + 0.5
    denom = d.sum(axis=1)
    good = denom > 1e-9
    if good.sum() < 8:
        return 0.0
    cent = np.full(h, np.nan)
    cent[good] = (d[good] * xs).sum(axis=1) / denom[good]

    # --- fit edge_x = m*row + b (the slant) ---------------------------------
    rows = np.arange(h, dtype=np.float64)
    A = np.column_stack([rows[good], np.ones(good.sum())])
    m, b = np.linalg.lstsq(A, cent[good], rcond=None)[0]

    # --- project every pixel onto the edge normal -> supersampled ESF -------
    rr, cc = np.mgrid[0:h, 0:w]
    dist = cc - (m * rr + b)
    R = min(half_window, (w - 2) / 2.0)
    sel = np.abs(dist) < R
    if sel.sum() < 64:
        return 0.0

    edges = np.arange(-R, R + bin_width, bin_width)
    idx = np.digitize(dist[sel], edges) - 1
    nb = len(edges) - 1
    ok = (idx >= 0) & (idx < nb)
    cnt = np.bincount(idx[ok], minlength=nb).astype(np.float64)
    tot = np.bincount(idx[ok], weights=a[sel][ok], minlength=nb)
    filled = cnt > 0
    if filled.sum() < 16:
        return 0.0
    esf = np.empty(nb)
    esf[filled] = tot[filled] / cnt[filled]
    # linear-interpolate any bin the slant did not reach
    if not filled.all():
        centres = np.arange(nb, dtype=np.float64)
        esf = np.interp(centres, centres[filled], esf[filled])

    # --- LSF -> MTF ----------------------------------------------------------
    lsf = np.diff(esf)
    if np.ptp(lsf) < 1e-9:
        return 0.0
    # Do NOT mean-subtract the LSF. Its integral IS the DC term (the edge
    # height) and the MTF is normalised by exactly that. Removing the mean
    # first drives DC to ~0 and then dividing by it inflates MTF50 by 1.5-2.0x
    # -- measured against the closed-form Gaussian MTF50 = sqrt(ln2/2)/(pi*s),
    # which is what caught this. Pinned by test_mtf50_matches_closed_form.
    lsf = lsf * np.hamming(len(lsf))
    spec = np.abs(np.fft.rfft(lsf))
    if spec[0] <= 1e-12:
        return 0.0
    mtf = spec / spec[0]
    freq = np.fft.rfftfreq(len(lsf), d=bin_width)   # cycles per pixel

    # undo the response of the finite-difference operator used to make the LSF
    corr = np.sinc(freq * bin_width)
    safe = corr > 0.2
    mtf = np.where(safe, mtf / np.where(safe, corr, 1.0), mtf)

    # --- first downward crossing of 0.5, linearly interpolated ---------------
    for i in range(1, len(mtf)):
        if mtf[i] < 0.5 <= mtf[i - 1]:
            span = mtf[i - 1] - mtf[i]
            t = 0.0 if span <= 0 else (mtf[i - 1] - 0.5) / span
            return float(freq[i - 1] + t * (freq[i] - freq[i - 1]))
    return float(freq[-1])


# ============================================================================
# results
# ============================================================================

@dataclass(frozen=True)
class FrameReport:
    """Per-frame verdict. Every rejection carries its measured number, so a
    mistuned gate is diagnosable from the report alone."""
    index: int
    used: bool
    reason: str
    vlap: float           # saturation-GUARDED variance of Laplacian
    vlap_raw: float       # unguarded, kept to make the glare bug visible
    sat_frac: float
    dx: float | None = None          # translation at ROI centre, frame px
    dy: float | None = None
    shift_px: float | None = None    # mean corner displacement (captures rotation)
    blur_score: float = 0.0          # absolute, scale-free defocus score, 0..1

    @property
    def code(self) -> str:
        """Reason code without the measured detail, for counting."""
        return self.reason.split(":", 1)[0]


@dataclass(frozen=True)
class StackResult:
    """
    image             upscaled result, uint8, (h*scale, w*scale).
                      **None when every frame was rejected** — SAAF abstains
                      rather than enrol a crop it could not verify.
    used / rejected   frame counts.
    mean_shift_px     mean inter-frame displacement vs the reference, frame px.
    sharpness_gain    guarded vLap(image) / guarded vLap(cubic-upscaled sharpest
                      single frame). 1.0 == no gain. NOTE the confound: vLap
                      also counts noise, and stacking removes noise, so on a
                      noisy burst this UNDERSTATES the true resolution gain.
                      `mtf50_slanted_edge` is the honest resolution measure.
    warning           "" or "CODE: explanation". Non-empty means the result is
                      not what a caller asking for super-resolution wanted.
    """
    image: np.ndarray | None
    used: int
    rejected: int
    mean_shift_px: float
    sharpness_gain: float
    warning: str
    reports: tuple[FrameReport, ...] = ()
    reference_index: int = -1
    subpixel_diversity: float = 0.0     # 0..1, circular variance of phase
    diversity_x: float = 0.0
    diversity_y: float = 0.0
    baseline: np.ndarray | None = None  # the fair single-frame comparison
    # blur_score of the REGISTERED MEAN: the burst's absolute focus, measured
    # on an image with sqrt(N) less noise than any single frame. 0.0 when the
    # burst never got far enough to have one.
    burst_blur_score: float = 0.0

    @property
    def degraded(self) -> bool:
        return bool(self.warning)


# ============================================================================
# helpers
# ============================================================================

def _as_gray_u8(f: np.ndarray) -> np.ndarray:
    if not isinstance(f, np.ndarray):
        raise SaafError(f"frame must be a numpy array, got {type(f).__name__}")
    if f.ndim == 3:
        if f.shape[2] not in (3, 4):
            raise SaafError(f"unsupported channel count {f.shape[2]}")
        code = cv2.COLOR_BGRA2GRAY if f.shape[2] == 4 else cv2.COLOR_BGR2GRAY
        f = cv2.cvtColor(f, code)
    elif f.ndim != 2:
        raise SaafError(f"frame must be 2-D or 3-D, got {f.ndim}-D")
    if f.dtype != np.uint8:
        f = np.clip(f, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(f)


def _phase_diversity(vals: np.ndarray) -> float:
    """Circular variance of the sub-pixel SAMPLING PHASE, in [0, 1].

    THE PHASE IS frac(shift) IN LOW-RES PIXELS, AND IT DOES NOT DEPEND ON scale.
    Derivation, because getting this wrong is subtle and I did get it wrong:

        a sample at LR reference coordinate x = k + d (k integer, d the
        sub-pixel shift) lands on the HR grid at
            X = (x + 0.5)*s - 0.5 = k*s + d*s + (s-1)/2

    The reference frame's own samples already tile the lattice {k*s}, so what
    distinguishes one frame from another is d*s modulo s -- which is frac(d),
    independent of s.

    The earlier version used frac(d*s), and that is WRONG: at scale 2 a shift
    of exactly 0.5 LR px lands one whole HR cell over, which is precisely the
    interleave 2x super-resolution wants, yet frac(0.5*2) = 0 declared it
    degenerate and would have fired the no-diversity warning on the single most
    favourable burst possible. Caught by
    test_half_pixel_motion_is_healthy_diversity.

        all frames same phase    -> R = 1 -> 0.0  (no new information)
        two frames, phases 0,0.5 -> R = 0 -> 1.0  (maximal new information)

    Using the circle rather than a plain std is not pedantry: phases 0.02 and
    0.98 are 0.04 apart, and a linear std would call them maximally spread.
    """
    if vals.size < 2:
        return 0.0
    theta = 2.0 * np.pi * np.mod(vals, 1.0)
    r = np.abs(np.exp(1j * theta).mean())
    return float(max(0.0, 1.0 - r))


def _corner_displacement(W: np.ndarray, shape: tuple[int, int]) -> float:
    """Mean displacement of the four ROI corners under warp `W`.

    Measured on corners rather than on the translation column so that a pure
    rotation, which has a near-zero translation term, is still counted as
    motion — which it is, and which is exactly the motion the shopkeeper's
    "thoda ghumaiye" nudge produces.
    """
    h, w = shape
    pts = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], np.float64)
    hom = np.column_stack([pts, np.ones(4)])
    mapped = (W @ hom.T).T
    return float(np.linalg.norm(mapped - pts, axis=1).mean())


def _centre_translation(W: np.ndarray, shape: tuple[int, int]) -> tuple[float, float]:
    """Local translation at the ROI centre — the term that sets sampling phase."""
    h, w = shape
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    mx, my = W @ np.array([cx, cy, 1.0])
    return float(mx - cx), float(my - cy)


# ============================================================================
# the stacker
# ============================================================================

class BurstStacker:
    """Deterministic, model-free multi-frame stacker for enrolment bursts.

    Fully deterministic: no RNG, no learned prior. The same burst always
    produces the same bytes, which is what lets an enrolment be replayed.
    """

    def __init__(self,
                 scale: int = DEFAULT_SCALE,
                 blur_var_min: float = DEFAULT_BLUR_VAR_MIN,
                 blur_rel_min: float = DEFAULT_BLUR_REL_MIN,
                 max_shift_px: float = DEFAULT_MAX_SHIFT_PX,
                 sat_level: int = DEFAULT_SAT_LEVEL,
                 sat_frac_max: float = DEFAULT_SAT_FRAC_MAX,
                 min_shift_px: float = DEFAULT_MIN_SHIFT_PX,
                 min_diversity: float = DEFAULT_MIN_DIVERSITY,
                 motion: str = "euclidean",
                 splat_sigma: float = DEFAULT_SPLAT_SIGMA,
                 max_blur_score: float = DEFAULT_MAX_BLUR_SCORE) -> None:
        if not isinstance(scale, (int, np.integer)) or scale < 1:
            raise SaafError(f"scale must be an integer >= 1, got {scale!r}")
        if motion not in ("euclidean", "translation"):
            raise SaafError(f"motion must be 'euclidean' or 'translation', got {motion!r}")
        if not (0.0 < float(max_blur_score) <= 1.0):
            raise SaafError(
                f"max_blur_score must be in (0, 1]; got {max_blur_score!r}. "
                f"1.0 disables the absolute focus gate, which is only ever a "
                f"control condition -- see the uniformly-defocused burst tests"
            )
        self.max_blur_score = float(max_blur_score)
        self.splat_sigma = float(splat_sigma)
        self.scale = int(scale)
        self.blur_var_min = float(blur_var_min)
        self.blur_rel_min = float(blur_rel_min)
        self.max_shift_px = float(max_shift_px)
        self.sat_level = int(sat_level)
        self.sat_frac_max = float(sat_frac_max)
        self.min_shift_px = float(min_shift_px)
        self.min_diversity = float(min_diversity)
        self.motion = motion
        self._motion_flag = (cv2.MOTION_EUCLIDEAN if motion == "euclidean"
                             else cv2.MOTION_TRANSLATION)

    # ---------------------------------------------------------------- public
    def stack(self, frames: Sequence[np.ndarray] | Iterable[np.ndarray]) -> StackResult:
        grays = [_as_gray_u8(f) for f in frames]
        n = len(grays)
        if n == 0:
            raise SaafError("stack() needs at least one frame")
        shape = grays[0].shape
        for i, g in enumerate(grays):
            if g.shape != shape:
                raise SaafError(
                    f"frame {i} has shape {g.shape}, expected {shape}; the burst "
                    "must be crops of the same region at the same size"
                )
        h, w = shape
        if h < 8 or w < 8:
            raise SaafError(f"frames too small to register: {shape}")

        # ---- 1. quality gate, per frame ------------------------------------
        sat = [saturated_fraction(g, self.sat_level) for g in grays]
        vlap_raw = [variance_of_laplacian(g) for g in grays]
        vlap = [variance_of_laplacian(g, sat_level=self.sat_level) for g in grays]
        bscore = [blur_score(g) for g in grays]

        # The vLap floor is the stricter of a nominally absolute threshold and
        # a BURST-RELATIVE one (which catches the bad frames inside an
        # otherwise good burst). Frames blown past the glare gate are excluded
        # from setting the reference level, so one glare frame cannot raise the
        # bar for the rest.
        clean = [vlap[i] for i in range(n) if sat[i] <= self.sat_frac_max]
        vmax = max(clean) if clean else 0.0
        floor = max(self.blur_var_min, self.blur_rel_min * vmax)

        reports: dict[int, FrameReport] = {}
        admitted: list[int] = []
        for i in range(n):
            if sat[i] > self.sat_frac_max:
                reports[i] = FrameReport(
                    i, False,
                    f"{R_GLARE}: saturated fraction {sat[i]:.4f} > {self.sat_frac_max:.4f}",
                    vlap[i], vlap_raw[i], sat[i], blur_score=bscore[i])
            elif vlap[i] < floor:
                which = ("absolute" if self.blur_var_min >= self.blur_rel_min * vmax
                         else f"{self.blur_rel_min:.0%} of burst best {vmax:.1f}")
                reports[i] = FrameReport(
                    i, False,
                    f"{R_BLUR}: guarded vLap {vlap[i]:.1f} < {floor:.1f} ({which})",
                    vlap[i], vlap_raw[i], sat[i], blur_score=bscore[i])
            elif bscore[i] > self.max_blur_score:
                # THE ABSOLUTE FOCUS FLOOR. Ordered AFTER the vLap gate on
                # purpose: when a frame fails both, R_BLUR is the more
                # actionable label (its neighbours are fine, re-shoot that
                # frame) while R_DEFOCUS means the optics were never in focus
                # and the whole burst has to be re-captured. The escalation to
                # the burst level happens below, when EVERY frame lands here.
                reports[i] = FrameReport(
                    i, False,
                    f"{R_DEFOCUS}: blur score {bscore[i]:.3f} > "
                    f"{self.max_blur_score:.3f}, i.e. MTF50 below about "
                    f"{BLUR_SCORE_MTF50_CYC_PX:.2f} cyc/px "
                    f"({BLUR_SCORE_MTF50_CYC_PX * 200:.0f}% of Nyquist). Out of "
                    f"focus in absolute terms, not merely worse than its "
                    f"neighbours",
                    vlap[i], vlap_raw[i], sat[i], blur_score=bscore[i])
            else:
                admitted.append(i)

        if not admitted:
            # Invariant 7: abstain. Do NOT enrol the least-bad blurred crop.
            report_all = tuple(reports[i] for i in range(n))
            if all(rep.code == R_DEFOCUS for rep in report_all):
                # Named separately from W_ALL_REJECTED because the cause and
                # the remedy are different, and because this is precisely the
                # case a burst-relative floor cannot see: with every frame
                # equally bad, the relative floor sinks with the burst and
                # rejects nothing.
                warn = (
                    f"{W_UNIFORMLY_DEFOCUSED}: all {n} frames are out of focus "
                    f"(blur score {min(bscore):.3f}-{max(bscore):.3f}, all above "
                    f"the {self.max_blur_score:.2f} absolute ceiling; guarded vLap "
                    f"{min(vlap):.1f}-{max(vlap):.1f} was NOT the discriminator "
                    f"and would have admitted every one of them). No image "
                    f"returned: the camera was not focused, so re-focus and "
                    f"re-capture rather than re-shoot one frame"
                )
            else:
                warn = (
                    f"{W_ALL_REJECTED}: all {n} frames failed the blur/glare/focus "
                    f"gate (best guarded vLap {max(vlap):.1f} vs floor {floor:.1f}, "
                    f"best blur score {min(bscore):.3f} vs ceiling "
                    f"{self.max_blur_score:.2f}, min saturated fraction "
                    f"{min(sat):.4f}); no image returned"
                )
            return StackResult(None, 0, n, 0.0, 0.0, warn, report_all, -1)

        # ---- 2. reference := the sharpest ADMITTED frame --------------------
        # This is also the fair single-frame baseline: the honest question is
        # not "stack vs a random frame" but "stack vs just picking the best one".
        ref = max(admitted, key=lambda i: (vlap[i], -i))
        hr = (h * self.scale, w * self.scale)
        baseline = cv2.resize(grays[ref], (hr[1], hr[0]), interpolation=cv2.INTER_CUBIC)

        # ---- 3. register every other admitted frame to it -------------------
        warps: dict[int, np.ndarray] = {ref: np.eye(2, 3, dtype=np.float64)}
        reports[ref] = FrameReport(ref, True, R_REFERENCE, vlap[ref], vlap_raw[ref],
                                   sat[ref], 0.0, 0.0, 0.0, bscore[ref])
        used = [ref]
        ref_f = grays[ref].astype(np.float32) / 255.0

        for i in admitted:
            if i == ref:
                continue
            W = self._register(ref_f, grays[i].astype(np.float32) / 255.0)
            if W is None:
                reports[i] = FrameReport(
                    i, False,
                    f"{R_ECC_FAILED}: findTransformECC did not converge "
                    f"(treated as frame rejected, NOT as zero motion)",
                    vlap[i], vlap_raw[i], sat[i], blur_score=bscore[i])
                continue
            if not np.isfinite(W).all():
                reports[i] = FrameReport(
                    i, False, f"{R_WARP_NOT_FINITE}: ECC returned a non-finite warp",
                    vlap[i], vlap_raw[i], sat[i], blur_score=bscore[i])
                continue
            disp = _corner_displacement(W, shape)
            dx, dy = _centre_translation(W, shape)
            if disp > self.max_shift_px:
                reports[i] = FrameReport(
                    i, False,
                    f"{R_SHIFT_TOO_LARGE}: displacement {disp:.2f}px > "
                    f"{self.max_shift_px:.2f}px; the crop no longer shows the same region",
                    vlap[i], vlap_raw[i], sat[i], dx, dy, disp, bscore[i])
                continue
            warps[i] = W
            used.append(i)
            reports[i] = FrameReport(i, True, R_OK, vlap[i], vlap_raw[i], sat[i],
                                     dx, dy, disp, bscore[i])

        used.sort()
        report_tuple = tuple(reports[i] for i in range(n))
        n_used, n_rej = len(used), n - len(used)

        # ---- 4. how much sub-pixel diversity did we actually get? -----------
        dxs = np.array([reports[i].dx for i in used], dtype=np.float64)
        dys = np.array([reports[i].dy for i in used], dtype=np.float64)
        shifts = np.array([reports[i].shift_px for i in used], dtype=np.float64)
        nonref = shifts[np.array(used) != ref]
        mean_shift = float(nonref.mean()) if nonref.size else 0.0
        div_x = _phase_diversity(dxs)
        div_y = _phase_diversity(dys)
        # max(), not min(): diversity along ONE axis genuinely buys resolution
        # along that axis. The result is anisotropic, not absent.
        diversity = max(div_x, div_y)

        # ---- 5. single admitted frame: nothing to stack ---------------------
        if n_used == 1:
            return StackResult(
                baseline, 1, n_rej, 0.0,
                self._gain(baseline, baseline),
                f"{W_SINGLE_FRAME}: only 1 of {n} frames passed the gate, so no "
                f"stacking was possible; returned the cubic upscale of frame {ref}",
                report_tuple, ref, 0.0, div_x, div_y, baseline,
                burst_blur_score=bscore[ref])

        # ---- 5b. THE ABSOLUTE FOCUS FLOOR, RE-ASKED OF THE WHOLE BURST ------
        # The per-frame focus gate above is blinded by sensor noise: noise is
        # white, blur_score's own re-blur destroys it completely, and the frame
        # therefore looks as though it had detail to lose. Measured on a text
        # burst defocused to 2.4 px, the per-frame score falls 0.476 clean ->
        # 0.264 under 4 grey levels of noise, and the per-frame gate stops
        # firing at 1 LSB (0.436, already under the ceiling).
        #
        # The burst itself is the way out, and it is the same argument the rest
        # of this module runs on. Noise is INDEPENDENT between frames and falls
        # as 1/sqrt(N) under the registered average; defocus is COMMON to every
        # frame and does not fall at all. So the aligned mean carries the same
        # optical resolution with a fraction of the noise, and the identical
        # ceiling means the identical thing on it -- verified, not assumed:
        # calibrated against MTF50 on registered means the ceiling comes out at
        # 0.4594..0.4682 (spread 0.009), the same window as the per-frame
        # calibration. One constant, two applications.
        #
        # Measured gain: at the same ceiling the burst-level check catches a
        # 2.4 px defocus up to 3 LSB of sensor noise, where the per-frame gate
        # manages it only at 0 LSB. Full envelope in
        # test_HONEST_enough_noise_still_hides_a_defocused_burst: 2 to 8 LSB
        # depending on the scene and the depth of the defocus.
        burst_focus = blur_score(self._aligned_mean(grays, used, warps))
        if burst_focus > self.max_blur_score:
            # The per-frame verdicts were not wrong, they were under-powered.
            # Revise them explicitly rather than leave reports that say "ok"
            # attached to a burst that returned nothing.
            revised = tuple(
                rep if not rep.used else FrameReport(
                    rep.index, False,
                    f"{R_DEFOCUS}: admitted per-frame at blur score "
                    f"{rep.blur_score:.3f}, then rejected with the burst: the "
                    f"registered mean of {n_used} frames scores {burst_focus:.3f} "
                    f"> {self.max_blur_score:.3f}, and averaging removes noise "
                    f"but not defocus",
                    rep.vlap, rep.vlap_raw, rep.sat_frac, rep.dx, rep.dy,
                    rep.shift_px, rep.blur_score)
                for rep in report_tuple)
            return StackResult(
                None, 0, n, mean_shift, 0.0,
                f"{W_UNIFORMLY_DEFOCUSED}: the burst is out of focus. Per-frame "
                f"blur scores {min(bscore):.3f}-{max(bscore):.3f} were not "
                f"conclusive, but the registered mean of {n_used} frames -- which "
                f"has {n_used ** 0.5:.1f}x less noise and exactly the same "
                f"defocus -- scores {burst_focus:.3f} > {self.max_blur_score:.3f}, "
                f"i.e. MTF50 below about {BLUR_SCORE_MTF50_CYC_PX:.2f} cyc/px. "
                f"Guarded vLap {min(vlap):.1f}-{max(vlap):.1f} was NOT the "
                f"discriminator. No image returned: re-focus and re-capture.",
                revised, -1, diversity, div_x, div_y, None,
                burst_blur_score=burst_focus)

        # ---- 6. THE HONEST CHECK -------------------------------------------
        if diversity < self.min_diversity:
            img = self._denoise_path(grays, used, warps, hr)
            if mean_shift < self.min_shift_px:
                warn = (f"{W_NO_DIVERSITY}: mean inter-frame shift {mean_shift:.4f}px "
                        f"< {self.min_shift_px:.2f}px — the subject did not move, so "
                        f"every frame sampled the same sub-pixel phase. This result is "
                        f"DENOISING ONLY, not super-resolution; no new detail exists "
                        f"in it. Ask for the nudge and re-capture.")
            else:
                warn = (f"{W_DEGENERATE_PHASE}: frames moved (mean {mean_shift:.3f}px) "
                        f"but all landed on the same sub-pixel phase (diversity "
                        f"{diversity:.4f} < {self.min_diversity:.2f}), e.g. a whole-pixel "
                        f"shift. DENOISING ONLY, not super-resolution.")
            return StackResult(img, n_used, n_rej, mean_shift, self._gain(img, baseline),
                               warn, report_tuple, ref, diversity, div_x, div_y, baseline,
                               burst_blur_score=burst_focus)

        # ---- 7. shift-and-add --------------------------------------------
        img = self._splat_path(grays, used, warps, hr, ref)
        return StackResult(img, n_used, n_rej, mean_shift, self._gain(img, baseline),
                           W_NONE, report_tuple, ref, diversity, div_x, div_y, baseline,
                           burst_blur_score=burst_focus)

    # --------------------------------------------------------------- private
    def _register(self, template_f: np.ndarray, input_f: np.ndarray) -> np.ndarray | None:
        """ECC registration. Returns W (template coords -> input coords), or None.

        VERIFIED BUILD TRAP (FAILURES.md): findTransformECC RAISES cv2.error on
        non-convergence rather than returning a low correlation. An unwrapped
        call turns an ordinary registration failure into a crash, and — far
        worse for this module — catching it as "no motion" would make a failed
        registration masquerade as the zero-diversity condition. A throw means
        REJECT THE FRAME.
        """
        W = np.eye(2, 3, dtype=np.float32)
        crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, ECC_MAX_ITERS, ECC_EPS)
        try:
            _cc, W = cv2.findTransformECC(template_f, input_f, W, self._motion_flag,
                                          crit, None, ECC_GAUSS_FILT)
        except cv2.error:
            return None
        return W.astype(np.float64)

    def _splat_path(self, grays: list[np.ndarray], used: list[int],
                    warps: dict[int, np.ndarray], hr: tuple[int, int],
                    ref: int) -> np.ndarray:
        """Shift-and-add: place every LR sample at its true sub-pixel position
        in the HR grid, with bilinear splatting, then normalise by weight.

        Splatting (scatter), not interpolation (gather), is the point. Gathering
        would resample each frame through an interpolation kernel, low-pass
        filtering away the very high frequencies the burst exists to recover.
        Scattering deposits each sample at the position it was actually taken
        from, so the union of N differently-phased frames is a denser sampling
        of the scene than any one of them.

        The reconstruction kernel is a Gaussian of `splat_sigma` HR px (or a
        bilinear tent when splat_sigma <= 0). Its width is the whole
        sharpness/robustness tradeoff and it was CHOSEN BY MEASUREMENT, not by
        taste: sweeping it against the true 2x ground truth over frame counts
        3..20 and noise sigma 0..6 put the optimum at 0.30 in every single cell
        (+1.9 to +2.5 dB over the cubic baseline; 0.60 gives only +0.9).
        See test_splat_sigma_default_is_the_measured_optimum.

        At scale=2 the LR sample lattice has 2 HR-px spacing and the radius-1
        footprint covers 3x3 HR px, so one frame alone already tiles the HR grid
        with no holes. The hole fill matters only at scale >= 4.
        """
        HH, HW = hr
        acc = np.zeros(HH * HW, dtype=np.float64)
        wacc = np.zeros(HH * HW, dtype=np.float64)
        h, w = grays[used[0]].shape
        ys, xs = np.mgrid[0:h, 0:w]
        ones = np.ones(h * w)
        P = np.vstack([xs.ravel().astype(np.float64), ys.ravel().astype(np.float64), ones])
        s = float(self.scale)
        sig = self.splat_sigma
        rad = 1 if sig <= 0 else max(1, int(np.ceil(2.5 * sig)))
        offs = ([(0, 0), (1, 0), (0, 1), (1, 1)] if sig <= 0 else
                [(dx, dy) for dy in range(-rad, rad + 1) for dx in range(-rad, rad + 1)])

        for i in used:
            W3 = np.vstack([warps[i], [0.0, 0.0, 1.0]])
            # W maps ref -> frame_i, so frame_i -> ref is its inverse
            try:
                Winv = np.linalg.inv(W3)
            except np.linalg.LinAlgError:
                continue
            Rp = Winv @ P
            # LR reference coords -> HR grid (OpenCV pixel-centre convention)
            X = (Rp[0] + 0.5) * s - 0.5
            Y = (Rp[1] + 0.5) * s - 0.5
            x0 = np.floor(X).astype(np.int64)
            y0 = np.floor(Y).astype(np.int64)
            fx, fy = X - x0, Y - y0
            v = grays[i].ravel().astype(np.float64)
            for dxi, dyi in offs:
                xi, yi = x0 + dxi, y0 + dyi
                if sig <= 0:
                    ww = (fx if dxi else 1.0 - fx) * (fy if dyi else 1.0 - fy)
                else:
                    ww = np.exp(-((X - xi) ** 2 + (Y - yi) ** 2) / (2.0 * sig * sig))
                m = (xi >= 0) & (xi < HW) & (yi >= 0) & (yi < HH) & (ww > 1e-6)
                if not m.any():
                    continue
                flat = yi[m] * HW + xi[m]
                acc += np.bincount(flat, weights=v[m] * ww[m], minlength=HH * HW)
                wacc += np.bincount(flat, weights=ww[m], minlength=HH * HW)

        acc = acc.reshape(HH, HW)
        wacc = wacc.reshape(HH, HW)
        filled = wacc > 1e-9
        out = np.zeros((HH, HW), np.float64)
        out[filled] = acc[filled] / wacc[filled]
        if not filled.all():
            # scale>=4 only. Fill from the cubic prior rather than inventing
            # detail; a hole is missing information and must not be guessed at.
            # The prior MUST come from the reference frame: the HR grid is in
            # reference coordinates, and any other frame is offset from it by
            # its registered shift, so filling from used[0] would paste in
            # misaligned pixels wherever used[0] is not itself the reference.
            prior = cv2.resize(grays[ref], (HW, HH),
                               interpolation=cv2.INTER_CUBIC).astype(np.float64)
            out[~filled] = prior[~filled]
        return np.clip(np.rint(out), 0, 255).astype(np.uint8)

    @staticmethod
    def _aligned_mean(grays: list[np.ndarray], used: list[int],
                      warps: dict[int, np.ndarray]) -> np.ndarray:
        """Registered average of the used frames, at LOW resolution, float64.

        Two callers want exactly this image for two different reasons, and it
        is the same image: the degenerate-diversity fallback wants it because
        averaging N registered frames is genuinely better than any one of them,
        and the burst-level focus check wants it because averaging attenuates
        the NOISE that blinds a no-reference focus metric while leaving the
        DEFOCUS, which is common to every frame, completely untouched.
        """
        h, w = grays[used[0]].shape
        acc = np.zeros((h, w), np.float64)
        for i in used:
            if np.allclose(warps[i], np.eye(2, 3)):
                acc += grays[i].astype(np.float64)   # the reference: no resample
                continue
            acc += cv2.warpAffine(
                grays[i].astype(np.float32), warps[i].astype(np.float32), (w, h),
                flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                borderMode=cv2.BORDER_REFLECT).astype(np.float64)
        return acc / len(used)

    def _denoise_path(self, grays: list[np.ndarray], used: list[int],
                      warps: dict[int, np.ndarray], hr: tuple[int, int]) -> np.ndarray:
        """The degenerate-diversity fallback: register, average, cubic upscale.

        Used when the sampling phases collapse. It is a legitimately BETTER
        image than any single frame (the noise is down by ~sqrt(N)) and is never
        worse than the baseline — it simply contains no detail beyond the LR
        Nyquist limit, which is precisely what the warning says.
        """
        HH, HW = hr
        mean_lr = self._aligned_mean(grays, used, warps)
        out = cv2.resize(mean_lr, (HW, HH), interpolation=cv2.INTER_CUBIC)
        return np.clip(np.rint(out), 0, 255).astype(np.uint8)

    def _gain(self, img: np.ndarray, baseline: np.ndarray) -> float:
        b = variance_of_laplacian(baseline, sat_level=self.sat_level)
        if b <= 1e-9:
            return 0.0
        return variance_of_laplacian(img, sat_level=self.sat_level) / b


__all__ = [
    "BurstStacker", "StackResult", "FrameReport", "SaafError",
    "variance_of_laplacian", "saturated_fraction", "mtf50_slanted_edge",
    "blur_score",
    "DEFAULT_SCALE", "DEFAULT_BLUR_VAR_MIN", "DEFAULT_BLUR_REL_MIN",
    "DEFAULT_MAX_SHIFT_PX",
    "DEFAULT_SAT_LEVEL", "DEFAULT_SAT_FRAC_MAX", "DEFAULT_SPLAT_SIGMA",
    "DEFAULT_MIN_SHIFT_PX", "DEFAULT_MIN_DIVERSITY",
    "DEFAULT_MAX_BLUR_SCORE", "BLUR_SCORE_SPAN", "BLUR_SCORE_MTF50_CYC_PX",
    "R_REFERENCE", "R_OK", "R_BLUR", "R_GLARE", "R_ECC_FAILED",
    "R_SHIFT_TOO_LARGE", "R_WARP_NOT_FINITE", "R_DEFOCUS",
    "W_NONE", "W_ALL_REJECTED", "W_SINGLE_FRAME", "W_NO_DIVERSITY",
    "W_DEGENERATE_PHASE", "W_UNIFORMLY_DEFOCUSED",
]
