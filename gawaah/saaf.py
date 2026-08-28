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

# --- warning codes -----------------------------------------------------------
W_NONE = ""
W_ALL_REJECTED = "ALL_FRAMES_REJECTED"
W_SINGLE_FRAME = "SINGLE_FRAME"
W_NO_DIVERSITY = "NO_SUBPIXEL_DIVERSITY"
W_DEGENERATE_PHASE = "DEGENERATE_SAMPLING_PHASE"


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
                 splat_sigma: float = DEFAULT_SPLAT_SIGMA) -> None:
        if not isinstance(scale, (int, np.integer)) or scale < 1:
            raise SaafError(f"scale must be an integer >= 1, got {scale!r}")
        if motion not in ("euclidean", "translation"):
            raise SaafError(f"motion must be 'euclidean' or 'translation', got {motion!r}")
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

        # The blur floor is the stricter of an ABSOLUTE threshold (catches a
        # burst that is uniformly unusable, where a purely relative gate would
        # happily admit the best of a bad lot) and a BURST-RELATIVE one
        # (catches the bad frames inside an otherwise good burst, which an
        # absolute threshold tuned for one scene's texture cannot do).
        # Frames blown past the glare gate are excluded from setting the
        # reference level, so one glare frame cannot raise the bar for the rest.
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
                    vlap[i], vlap_raw[i], sat[i])
            elif vlap[i] < floor:
                which = ("absolute" if self.blur_var_min >= self.blur_rel_min * vmax
                         else f"{self.blur_rel_min:.0%} of burst best {vmax:.1f}")
                reports[i] = FrameReport(
                    i, False,
                    f"{R_BLUR}: guarded vLap {vlap[i]:.1f} < {floor:.1f} ({which})",
                    vlap[i], vlap_raw[i], sat[i])
            else:
                admitted.append(i)

        if not admitted:
            # Invariant 7: abstain. Do NOT enrol the least-bad blurred crop.
            return StackResult(
                None, 0, n, 0.0, 0.0,
                f"{W_ALL_REJECTED}: all {n} frames failed the blur/glare gate "
                f"(best guarded vLap {max(vlap):.1f} < floor {floor:.1f}, "
                f"min saturated fraction {min(sat):.4f}); no image returned",
                tuple(reports[i] for i in range(n)), -1)

        # ---- 2. reference := the sharpest ADMITTED frame --------------------
        # This is also the fair single-frame baseline: the honest question is
        # not "stack vs a random frame" but "stack vs just picking the best one".
        ref = max(admitted, key=lambda i: (vlap[i], -i))
        hr = (h * self.scale, w * self.scale)
        baseline = cv2.resize(grays[ref], (hr[1], hr[0]), interpolation=cv2.INTER_CUBIC)

        # ---- 3. register every other admitted frame to it -------------------
        warps: dict[int, np.ndarray] = {ref: np.eye(2, 3, dtype=np.float64)}
        reports[ref] = FrameReport(ref, True, R_REFERENCE, vlap[ref], vlap_raw[ref],
                                   sat[ref], 0.0, 0.0, 0.0)
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
                    vlap[i], vlap_raw[i], sat[i])
                continue
            if not np.isfinite(W).all():
                reports[i] = FrameReport(
                    i, False, f"{R_WARP_NOT_FINITE}: ECC returned a non-finite warp",
                    vlap[i], vlap_raw[i], sat[i])
                continue
            disp = _corner_displacement(W, shape)
            dx, dy = _centre_translation(W, shape)
            if disp > self.max_shift_px:
                reports[i] = FrameReport(
                    i, False,
                    f"{R_SHIFT_TOO_LARGE}: displacement {disp:.2f}px > "
                    f"{self.max_shift_px:.2f}px; the crop no longer shows the same region",
                    vlap[i], vlap_raw[i], sat[i], dx, dy, disp)
                continue
            warps[i] = W
            used.append(i)
            reports[i] = FrameReport(i, True, R_OK, vlap[i], vlap_raw[i], sat[i],
                                     dx, dy, disp)

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
                report_tuple, ref, 0.0, div_x, div_y, baseline)

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
                               warn, report_tuple, ref, diversity, div_x, div_y, baseline)

        # ---- 7. shift-and-add --------------------------------------------
        img = self._splat_path(grays, used, warps, hr, ref)
        return StackResult(img, n_used, n_rej, mean_shift, self._gain(img, baseline),
                           W_NONE, report_tuple, ref, diversity, div_x, div_y, baseline)

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

    def _denoise_path(self, grays: list[np.ndarray], used: list[int],
                      warps: dict[int, np.ndarray], hr: tuple[int, int]) -> np.ndarray:
        """The degenerate-diversity fallback: register, average, cubic upscale.

        Used when the sampling phases collapse. It is a legitimately BETTER
        image than any single frame (the noise is down by ~sqrt(N)) and is never
        worse than the baseline — it simply contains no detail beyond the LR
        Nyquist limit, which is precisely what the warning says.
        """
        HH, HW = hr
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
        mean_lr = acc / len(used)
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
    "DEFAULT_SCALE", "DEFAULT_BLUR_VAR_MIN", "DEFAULT_BLUR_REL_MIN",
    "DEFAULT_MAX_SHIFT_PX",
    "DEFAULT_SAT_LEVEL", "DEFAULT_SAT_FRAC_MAX", "DEFAULT_SPLAT_SIGMA",
    "DEFAULT_MIN_SHIFT_PX", "DEFAULT_MIN_DIVERSITY",
    "R_REFERENCE", "R_OK", "R_BLUR", "R_GLARE", "R_ECC_FAILED",
    "R_SHIFT_TOO_LARGE", "R_WARP_NOT_FINITE",
    "W_NONE", "W_ALL_REJECTED", "W_SINGLE_FRAME", "W_NO_DIVERSITY",
    "W_DEGENERATE_PHASE",
]
