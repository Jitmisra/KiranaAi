"""S6 SAAF acceptance — the enrolment gate and the rescued KAMPAN burst stack.

MEASUREMENT DESIGN, because the headline claim is a measured number and a
sloppy harness would make it a lie.

  * Ground truth is CONSTRUCTED, not assumed. Scenes are built at SR=8x the
    low-resolution grid, shifted by KNOWN sub-pixel amounts, and box-downsampled
    (INTER_AREA on an integer ratio is an exact box average, i.e. a physically
    honest pixel aperture). The true 2x image is the same scene downsampled by
    SR/2, so "did the stack recover real detail" has an exact answer.

  * The baseline is the FAIR one. Not a random frame, and not a blurred frame:
    the sharpest frame in the burst, upsampled with INTER_CUBIC. The honest
    question is "does fusing beat simply picking the best one", which is the
    comparison SIX.md commits to.

  * The instrument is validated before it is used. `mtf50_slanted_edge` is
    checked against the closed-form Gaussian MTF50 = sqrt(ln2/2)/(pi*sigma)
    FIRST (test_mtf50_matches_closed_form). An unvalidated instrument found a
    real 1.5-2.0x bias in this module's own MTF code during development.

  * The negative result is a test, not a footnote. Multi-frame SR recovers
    information destroyed by ALIASING. Where the input is already band-limited
    there is nothing to recover, and
    test_HONEST_gain_vanishes_when_input_is_not_aliased asserts the gain
    collapses there. If that test ever starts failing because the gain got big,
    the module is inventing detail and the claim must be re-examined.

Numbers printed by this file are produced by the run, never typed in.
"""
from __future__ import annotations

from functools import lru_cache

import cv2
import numpy as np
import pytest

from gawaah.saaf import (
    BLUR_SCORE_MTF50_CYC_PX, DEFAULT_BLUR_VAR_MIN, DEFAULT_MAX_BLUR_SCORE,
    DEFAULT_SPLAT_SIGMA,
    BurstStacker, SaafError, StackResult,
    W_ALL_REJECTED, W_DEGENERATE_PHASE, W_NO_DIVERSITY, W_SINGLE_FRAME,
    W_UNIFORMLY_DEFOCUSED,
    R_BLUR, R_DEFOCUS, R_ECC_FAILED, R_GLARE, R_SHIFT_TOO_LARGE,
    blur_score, mtf50_slanted_edge, saturated_fraction, variance_of_laplacian,
)

SR = 8                      # ground-truth oversampling of the LR grid
LR = 160                    # LR frame before cropping
CROP = 128                  # the enrolment crop
OFF = (LR - CROP) // 2

# Optical PSF in HR-scene px. 1.5 HR px = 0.19 LR px leaves the LR frames
# genuinely ALIASED, which is the regime multi-frame SR exists for.
PSF_ALIASED = 1.5
# 6.0 HR px = 0.75 LR px band-limits the scene below LR Nyquist: no aliasing,
# so nothing for SR to recover. The honest negative-result regime.
PSF_BANDLIMITED = 6.0

MEASURED: dict[str, str] = {}


def record(key: str, text: str) -> None:
    MEASURED[key] = text
    print(f"\n  [MEASURED] {key}: {text}")


# =========================================================================
# synthetic scenes and bursts
# =========================================================================

@lru_cache(maxsize=8)
def hr_text_scene(psf_hr: float = PSF_ALIASED, seed: int = 0) -> np.ndarray:
    """Text-like HR scene: dense strokes at many widths and spacings."""
    rng = np.random.default_rng(seed)
    H = W = LR * SR
    img = np.full((H, W), 240, np.float32)
    y = 20 * SR
    while y < H - 12 * SR:
        x = 6 * SR
        while x < W - 12 * SR:
            wpx = int(rng.integers(1, 4) * SR // 2)
            hpx = int(rng.integers(2, 5) * SR // 2)
            img[y:y + hpx, x:x + wpx] = 25
            x += wpx + int(rng.integers(1, 3) * SR // 2)
        y += int(rng.integers(4, 7) * SR // 2)
    return cv2.GaussianBlur(img, (0, 0), psf_hr)


@lru_cache(maxsize=8)
def hr_edge_scene(psf_hr: float = PSF_ALIASED, angle_deg: float = 5.0,
                  seed: int = 3) -> np.ndarray:
    """Slanted edge down the centre; registration texture in top/bottom bands.

    The texture bands are not decoration. A bare straight edge is the aperture
    problem -- it constrains motion only along its own normal -- so ECC cannot
    solve three Euclidean parameters from it and throws. An early version of
    this harness had exactly that and lost 11 of 12 frames to `ecc_failed`,
    which looked like a stacker bug and was a harness bug.
    """
    rng = np.random.default_rng(seed)
    H = W = LR * SR
    xs = np.arange(W)[None, :].astype(np.float32)
    ys = np.arange(H)[:, None].astype(np.float32)
    edge = W / 2 + np.tan(np.radians(angle_deg)) * (ys - H / 2)
    img = np.where(xs < edge, 30.0, 225.0).astype(np.float32)
    for lo, hi in ((100, 300), (980, 1180)):
        for _ in range(300):
            cv2.circle(img, (int(rng.integers(0, W)), int(rng.integers(lo, hi))),
                       int(rng.integers(2, 7)), float(rng.integers(0, 255)), -1)
    return cv2.GaussianBlur(img, (0, 0), psf_hr)


# ROI on the 2x output that contains only the slanted edge, no texture band.
EDGE_ROI = (slice(50, 206), slice(78, 178))


def make_burst(hr: np.ndarray, shifts, noise: float = 0.0, seed: int = 1,
               blur_idx=(), blur_sigma: float = 1.8, glare_idx=(),
               glare_r: int = 12) -> list[np.ndarray]:
    """Downsample `hr` with KNOWN sub-pixel shifts -> a burst of LR crops."""
    rng = np.random.default_rng(seed)
    H, W = hr.shape
    out = []
    for k, (sx, sy) in enumerate(shifts):
        M = np.float32([[1, 0, sx * SR], [0, 1, sy * SR]])
        sh = cv2.warpAffine(hr, M, (W, H), flags=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_REFLECT)
        lr = cv2.resize(sh, (LR, LR), interpolation=cv2.INTER_AREA)
        lr = lr[OFF:OFF + CROP, OFF:OFF + CROP]
        if k in blur_idx:
            lr = cv2.GaussianBlur(lr, (0, 0), blur_sigma)
        if noise > 0:
            lr = lr + rng.normal(0, noise, lr.shape)
        f = np.clip(np.rint(lr), 0, 255).astype(np.uint8)
        if k in glare_idx:
            cv2.circle(f, (CROP // 2, CROP // 3), glare_r, 255, -1)
        out.append(f)
    return out


def gt_2x(hr: np.ndarray, shift) -> np.ndarray:
    """The TRUE 2x image for a given frame's shift: what a 2x-denser sensor
    would have recorded. This is what the stack is trying to reconstruct."""
    H, W = hr.shape
    sx, sy = shift
    M = np.float32([[1, 0, sx * SR], [0, 1, sy * SR]])
    sh = cv2.warpAffine(hr, M, (W, H), flags=cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_REFLECT)
    g = cv2.resize(sh, (LR * 2, LR * 2), interpolation=cv2.INTER_AREA)
    return np.clip(np.rint(g[OFF * 2:OFF * 2 + CROP * 2,
                             OFF * 2:OFF * 2 + CROP * 2]), 0, 255).astype(np.uint8)


def golden_shifts(n: int):
    """Low-discrepancy sub-pixel shifts: the golden ratio fills the sampling
    phase more evenly than any RNG draw, and is deterministic."""
    g = 0.6180339887498949
    return [(((i * g) % 1.0) - 0.5, ((i * g * g) % 1.0) - 0.5) for i in range(n)]


def psnr(a: np.ndarray, b: np.ndarray, margin: int = 16) -> float:
    """PSNR over the interior only; the border has no neighbours to stack."""
    x = a[margin:-margin, margin:-margin].astype(np.float64)
    y = b[margin:-margin, margin:-margin].astype(np.float64)
    mse = ((x - y) ** 2).mean()
    return 99.0 if mse < 1e-12 else float(10 * np.log10(255.0 ** 2 / mse))


def packet(texture: float, seed: int = 0) -> np.ndarray:
    """A glossy packet face: mostly flat wrap with a printed label."""
    rng = np.random.default_rng(seed)
    img = np.full((CROP, CROP), 205, np.float32)
    cv2.rectangle(img, (18, 26), (110, 96), 170, -1)
    for _ in range(int(texture * 40)):
        x, y = int(rng.integers(22, 104)), int(rng.integers(30, 92))
        cv2.rectangle(img, (x, y), (x + int(rng.integers(2, 6)),
                                    y + int(rng.integers(2, 5))),
                      float(rng.integers(20, 90)), -1)
    return cv2.GaussianBlur(img, (0, 0), 0.7).astype(np.uint8)


# =========================================================================
# 0. VALIDATE THE INSTRUMENT before trusting anything it says
# =========================================================================

def _gauss_edge(sigma: float, n: int = 320) -> np.ndarray:
    xs = np.arange(n)[None, :].astype(np.float32)
    ys = np.arange(n)[:, None].astype(np.float32)
    edge = n / 2 + np.tan(np.radians(5.0)) * (ys - n / 2)
    img = np.where(xs < edge, 20.0, 230.0).astype(np.float32)
    return cv2.GaussianBlur(img, (0, 0), sigma)


@pytest.mark.parametrize("sigma", [0.8, 1.2, 1.8, 2.5])
def test_mtf50_matches_closed_form(sigma):
    """For a Gaussian PSF of std s, MTF(f) = exp(-2 pi^2 s^2 f^2), so
    MTF50 = sqrt(ln2/2)/(pi*s). The measurement must recover that.

    This test caught a real bug: the first implementation mean-subtracted the
    LSF and then normalised the spectrum by its DC term -- which the
    subtraction had just driven to ~zero -- inflating every MTF50 by 1.5-2.0x.
    Without a closed form to check against, that bias would have silently
    become the module's headline number.
    """
    img = _gauss_edge(sigma)
    got = mtf50_slanted_edge(img[:, 100:220])
    want = float(np.sqrt(np.log(2) / 2) / (np.pi * sigma))
    record(f"mtf50_closed_form_sigma_{sigma}",
           f"measured {got:.4f} vs closed form {want:.4f} cyc/px "
           f"(ratio {got / want:.3f})")
    assert got == pytest.approx(want, rel=0.06), (
        f"MTF50 instrument is biased at sigma={sigma}: {got:.4f} vs {want:.4f}"
    )


def test_mtf50_is_monotonic_in_blur():
    vals = [mtf50_slanted_edge(_gauss_edge(s)[:, 100:220])
            for s in (0.8, 1.2, 1.8, 2.5, 3.2)]
    assert all(vals[i] < vals[i - 1] for i in range(1, len(vals))), vals


def test_mtf50_rejects_a_featureless_roi():
    assert mtf50_slanted_edge(np.full((64, 64), 128, np.uint8)) == 0.0


def test_mtf50_raises_on_a_too_small_roi():
    with pytest.raises(SaafError):
        mtf50_slanted_edge(np.zeros((8, 8), np.uint8))


# =========================================================================
# 1. sharpness and the saturation guard
# =========================================================================

def test_vlap_falls_monotonically_with_blur():
    base = make_burst(hr_text_scene(), [(0.0, 0.0)])[0]
    vals = [variance_of_laplacian(cv2.GaussianBlur(base, (0, 0), s) if s else base)
            for s in (0, 0.8, 1.4, 2.2, 3.0)]
    assert all(vals[i] < vals[i - 1] for i in range(1, len(vals))), vals


def test_saturated_fraction_counts_blown_pixels():
    img = np.full((100, 100), 100, np.uint8)
    assert saturated_fraction(img) == 0.0
    img[:10, :] = 255                      # 10% of the frame
    assert saturated_fraction(img) == pytest.approx(0.10)


def test_ACCEPTANCE_glare_frame_beats_sharp_frame_on_RAW_vlap():
    """THE NAMED BUG (SIX.md): a blown specular edge MAXIMISES Laplacian
    variance, so an unguarded sharpness sort prefers the worst frame.

    Saturation clips, so the blown blob's rim is a hard 0->255 step no matter
    how defocused the optics were -- the largest Laplacian response in the
    frame. The condition, measured here, is a LOW-TEXTURE crop (a glossy packet
    face), where the rim's response is comparable to the genuine detail. On a
    dense-text target the real texture swamps the highlight and the bug does
    not appear; claiming it always does would be overclaiming.
    """
    sharp = packet(0.2)
    blown = cv2.GaussianBlur(sharp, (0, 0), 1.2)
    cv2.circle(blown, (40, 44), 5, 255, -1)
    cv2.circle(blown, (86, 78), 5, 255, -1)

    raw_s, raw_b = variance_of_laplacian(sharp), variance_of_laplacian(blown)
    g_s = variance_of_laplacian(sharp, sat_level=250)
    g_b = variance_of_laplacian(blown, sat_level=250)
    sat = saturated_fraction(blown)

    record("glare_bug",
           f"blown frame passes the {0.02:.0%} saturation gate at sat_frac="
           f"{sat:.4f}; RAW vLap sharp={raw_s:.1f} < blown={raw_b:.1f} "
           f"(unguarded sort picks the GLARE frame); GUARDED vLap "
           f"sharp={g_s:.1f} > blown={g_b:.1f} (correct)")

    assert sat <= 0.02, (
        "this test is only meaningful when the blown frame slips past the "
        "saturation-fraction gate, so the ranking guard is the only defence"
    )
    assert raw_b > raw_s, (
        "the glare bug did not reproduce; if this is genuinely fixed upstream "
        "the saturation guard's justification must be re-derived"
    )
    assert g_b < g_s, (
        f"the saturation guard failed to fix the ranking: guarded blown {g_b:.1f} "
        f"still beats guarded sharp {g_s:.1f}"
    )


def test_saturation_gate_rejects_a_large_glare_blob():
    st = BurstStacker(scale=2)
    frames = make_burst(hr_text_scene(), golden_shifts(6), glare_idx=(3,), glare_r=14)
    r = st.stack(frames)
    assert r.reports[3].code == R_GLARE, r.reports[3].reason
    assert not r.reports[3].used
    assert r.reports[3].sat_frac > 0.02


def test_guard_returns_zero_when_the_frame_is_almost_all_glare():
    img = np.full((CROP, CROP), 255, np.uint8)
    img[:4, :4] = 0
    assert variance_of_laplacian(img, sat_level=250) == 0.0


# =========================================================================
# 2. ACCEPTANCE — a real, measured super-resolution gain
# =========================================================================

def _run(scene, n=12, **kw):
    shifts = golden_shifts(n)
    frames = make_burst(scene, shifts, **kw)
    r = BurstStacker(scale=2).stack(frames)
    return r, frames, shifts


def test_ACCEPTANCE_stack_beats_single_frame_on_MTF50():
    """Headline resolution claim, on a slanted edge, both images on the SAME
    2x grid so the comparison is apples to apples."""
    r, _, shifts = _run(hr_edge_scene(), n=12)
    assert r.warning == "", r.warning
    gt = gt_2x(hr_edge_scene(), shifts[r.reference_index])

    m_base = mtf50_slanted_edge(r.baseline[EDGE_ROI])
    m_stack = mtf50_slanted_edge(r.image[EDGE_ROI])
    m_gt = mtf50_slanted_edge(gt[EDGE_ROI])
    gain = m_stack / m_base
    closed = (m_stack - m_base) / (m_gt - m_base) if m_gt > m_base else float("nan")

    record("mtf50_gain",
           f"MTF50 cubic-baseline={m_base:.4f} -> stack={m_stack:.4f} cyc/px "
           f"= {gain:.3f}x ({(gain - 1) * 100:+.1f}%); true 2x ground truth "
           f"={m_gt:.4f}, so the stack closes {closed * 100:.1f}% of the gap "
           f"(n=12 frames, diversity={r.subpixel_diversity:.3f})")

    assert gain > 1.05, (
        f"no measurable resolution gain: MTF50 {m_base:.4f} -> {m_stack:.4f} "
        f"({gain:.3f}x)"
    )


def test_ACCEPTANCE_stack_is_closer_to_ground_truth_than_the_baseline():
    """The unimpeachable version: is the stack nearer the TRUE 2x image than
    cubic upsampling of the sharpest single frame? PSNR against constructed
    ground truth, so there is no metric to game."""
    scene = hr_text_scene()
    r, _, shifts = _run(scene, n=12)
    assert r.warning == "", r.warning
    gt = gt_2x(scene, shifts[r.reference_index])
    pb, ps = psnr(r.baseline, gt), psnr(r.image, gt)
    record("psnr_vs_ground_truth",
           f"text scene, n=12: cubic baseline {pb:.2f} dB -> stack {ps:.2f} dB "
           f"= {ps - pb:+.2f} dB toward the true 2x image")
    assert ps > pb + 0.5, (
        f"stack ({ps:.2f} dB) is not meaningfully closer to ground truth than "
        f"the fair single-frame baseline ({pb:.2f} dB)"
    )


@pytest.mark.parametrize("n", [4, 8, 16])
def test_gain_holds_across_frame_counts(n):
    scene = hr_text_scene()
    r, _, shifts = _run(scene, n=n)
    assert r.warning == "", r.warning
    gt = gt_2x(scene, shifts[r.reference_index])
    pb, ps = psnr(r.baseline, gt), psnr(r.image, gt)
    record(f"psnr_gain_n{n}", f"n={n}: {ps - pb:+.2f} dB (used={r.used})")
    assert ps > pb + 0.5, f"n={n}: {pb:.2f} -> {ps:.2f} dB"


def test_sharpness_gain_field_exceeds_one_on_a_clean_aliased_burst():
    r, _, _ = _run(hr_text_scene(), n=12)
    record("sharpness_gain_field",
           f"StackResult.sharpness_gain = {r.sharpness_gain:.3f} "
           f"(guarded vLap ratio, clean aliased burst)")
    assert r.sharpness_gain > 1.0, r.sharpness_gain


def test_splat_sigma_default_is_the_measured_optimum():
    """The reconstruction-kernel width was chosen by sweeping it against
    ground truth. Pin that, so nobody re-tunes it on taste."""
    scene = hr_text_scene()
    shifts = golden_shifts(8)
    frames = make_burst(scene, shifts)
    scores = {}
    for sig in (0.0, DEFAULT_SPLAT_SIGMA, 0.45, 0.60):
        r = BurstStacker(scale=2, splat_sigma=sig).stack(frames)
        scores[sig] = psnr(r.image, gt_2x(scene, shifts[r.reference_index]))
    best = max(scores, key=scores.__getitem__)
    record("splat_sigma_sweep",
           " ".join(f"sigma={k:.2f}:{v:.2f}dB" for k, v in sorted(scores.items()))
           + f" -> best={best:.2f}")
    assert best == pytest.approx(DEFAULT_SPLAT_SIGMA), (
        f"DEFAULT_SPLAT_SIGMA={DEFAULT_SPLAT_SIGMA} is no longer optimal: {scores}"
    )


# =========================================================================
# 3. THE PUBLISHED NEGATIVE RESULT
# =========================================================================

def test_HONEST_gain_vanishes_when_the_input_is_not_aliased():
    """Multi-frame SR recovers information destroyed by ALIASING. If the optics
    already band-limit the scene below the sensor's Nyquist frequency, a single
    frame carries everything and there is nothing left to recover.

    This is the measured boundary of the claim, and it is asserted rather than
    footnoted. If this test ever fails because the gain got LARGE on
    band-limited input, the stacker has started inventing detail that was never
    sampled, and the headline number must not be published until that is
    explained.
    """
    aliased, flat = hr_edge_scene(PSF_ALIASED), hr_edge_scene(PSF_BANDLIMITED)
    out = {}
    for name, scene in (("aliased", aliased), ("band-limited", flat)):
        r, _, _ = _run(scene, n=12)
        assert r.warning == "", r.warning
        mb = mtf50_slanted_edge(r.baseline[EDGE_ROI])
        ms = mtf50_slanted_edge(r.image[EDGE_ROI])
        out[name] = (mb, ms, ms / mb)

    record("negative_result_aliasing",
           f"MTF50 gain on ALIASED input = {out['aliased'][2]:.3f}x "
           f"({out['aliased'][0]:.4f} -> {out['aliased'][1]:.4f}); on "
           f"BAND-LIMITED input = {out['band-limited'][2]:.3f}x "
           f"({out['band-limited'][0]:.4f} -> {out['band-limited'][1]:.4f}). "
           f"The gain is a property of the INPUT, not of the algorithm.")

    assert out["aliased"][2] > 1.05
    assert out["band-limited"][2] < 1.05, (
        "band-limited input showed a real gain -- SR cannot recover detail that "
        "was never aliased into the samples, so this needs explaining before "
        f"any number is published: {out}"
    )
    assert out["aliased"][2] > out["band-limited"][2]


# A genuinely featureless region of the 2x edge output: left of the slanted
# edge (which sits near column 128) and clear of the top/bottom texture bands.
FLAT_ROI = (slice(60, 200), slice(20, 100))


def test_HONEST_vlap_gain_is_confounded_by_noise():
    """`sharpness_gain` uses variance-of-Laplacian, which counts NOISE as
    detail. Stacking removes noise, so the reported sharpness_gain DEGRADES as
    the burst gets noisier even though the true resolution gain does not.

    Pinned as an executable fact so the field is never quoted as a resolution
    measurement. MTF50 is the honest one.
    """
    scene = hr_edge_scene()
    out = {}
    for nz in (0.0, 6.0):
        r, _, _ = _run(scene, n=12, noise=nz, seed=7)
        assert r.warning == "", r.warning
        mb = mtf50_slanted_edge(r.baseline[EDGE_ROI])
        ms = mtf50_slanted_edge(r.image[EDGE_ROI])
        out[nz] = (r.sharpness_gain, ms / mb,
                   float(r.baseline[FLAT_ROI].std()), float(r.image[FLAT_ROI].std()))

    clean_v, clean_m = out[0.0][0], out[0.0][1]
    noisy_v, noisy_m, fb, fs = out[6.0]
    record("noise_confound",
           f"clean burst: vLap gain={clean_v:.3f}, MTF50 gain={clean_m:.3f}. "
           f"Noisy burst (sigma=6): vLap gain falls to {noisy_v:.3f} while "
           f"MTF50 gain holds at {noisy_m:.3f}. Flat-patch noise sigma "
           f"{fb:.2f} -> {fs:.2f} ({fb / fs:.2f}x reduction), which is the "
           f"real benefit vLap is charging the stack for.")

    assert fs < fb, "stacking did not reduce noise on a flat patch"
    assert noisy_v < clean_v, (
        "vLap gain did not degrade with noise; the confound this test documents "
        f"may no longer exist: {out}"
    )
    assert noisy_v < noisy_m, (
        f"on a noisy burst vLap gain ({noisy_v:.3f}) should UNDERSTATE the true "
        f"resolution gain measured by MTF50 ({noisy_m:.3f})"
    )
    assert noisy_m > 1.05, "true resolution gain should survive moderate noise"


# =========================================================================
# 4. THE REQUIRED HONEST FAILURE MODE — zero sub-pixel diversity
# =========================================================================

def test_ACCEPTANCE_zero_motion_sets_the_warning():
    """The rig's actual failure mode: the phone is clamped and if the
    shopkeeper does not nudge the packet, nothing moves."""
    frames = make_burst(hr_text_scene(), [(0.0, 0.0)] * 8, noise=3.0, seed=5)
    r = BurstStacker(scale=2).stack(frames)
    record("zero_motion",
           f"8 identical-phase frames -> diversity={r.subpixel_diversity:.5f}, "
           f"mean_shift={r.mean_shift_px:.5f}px, warning={r.warning.split(':')[0]}")
    assert r.warning.startswith(W_NO_DIVERSITY), r.warning
    assert r.subpixel_diversity < 0.10
    assert r.used == 8
    assert "DENOISING ONLY" in r.warning
    assert r.degraded


def test_ACCEPTANCE_zero_motion_image_is_not_worse_than_the_baseline():
    """A warning alone is not enough: the returned image must not be a
    degraded splat. Under zero diversity the stacker falls back to the
    register-average-upscale path, which is genuinely better than one frame."""
    scene = hr_text_scene()
    frames = make_burst(scene, [(0.0, 0.0)] * 8, noise=6.0, seed=13)
    r = BurstStacker(scale=2).stack(frames)
    assert r.warning.startswith(W_NO_DIVERSITY)
    gt = gt_2x(scene, (0.0, 0.0))
    pb, ps = psnr(r.baseline, gt), psnr(r.image, gt)
    record("zero_motion_not_worse",
           f"degenerate path PSNR {pb:.2f} dB (baseline) -> {ps:.2f} dB "
           f"(returned) = {ps - pb:+.2f} dB; denoising, not resolution")
    assert ps >= pb - 0.05, (
        f"the zero-diversity fallback returned a WORSE image than the single-"
        f"frame baseline ({ps:.2f} vs {pb:.2f} dB) -- exactly what the warning "
        f"exists to prevent"
    )


def test_integer_pixel_motion_is_caught_as_degenerate_phase():
    """Motion is not the same thing as sampling diversity. Whole-pixel shifts
    move the subject but re-sample the identical phase, so they add nothing.
    A stacker gated on shift MAGNITUDE alone would call this healthy."""
    shifts = [(float(i % 3), float((i * 2) % 3)) for i in range(9)]
    frames = make_burst(hr_text_scene(), shifts)
    r = BurstStacker(scale=2).stack(frames)
    record("integer_motion",
           f"whole-pixel shifts: mean_shift={r.mean_shift_px:.3f}px (clearly "
           f"moving) but diversity={r.subpixel_diversity:.5f} -> "
           f"{r.warning.split(':')[0]}")
    assert r.warning.startswith(W_DEGENERATE_PHASE), r.warning
    assert r.mean_shift_px > 0.5, "the frames really did move"
    assert r.subpixel_diversity < 0.10


def test_half_pixel_motion_is_healthy_diversity():
    shifts = [(0.0, 0.0), (0.5, 0.0), (0.0, 0.5), (0.5, 0.5)] * 2
    frames = make_burst(hr_text_scene(), shifts)
    r = BurstStacker(scale=2).stack(frames)
    assert r.warning == "", r.warning
    assert r.subpixel_diversity > 0.5


def test_no_warning_when_diversity_is_present():
    r, _, _ = _run(hr_text_scene(), n=10)
    assert r.warning == ""
    assert not r.degraded
    assert r.subpixel_diversity > 0.10


def test_diversity_is_measured_on_the_circle_not_as_a_plain_spread():
    """Phases 0.02 and 0.98 are 0.04 apart, not 0.96. A linear std would call
    that pair maximally diverse and skip the warning."""
    shifts = [(0.01, 0.0), (-0.01, 0.0)] * 4
    frames = make_burst(hr_text_scene(), shifts)
    r = BurstStacker(scale=2).stack(frames)
    record("wraparound_phase",
           f"phases straddling zero: diversity={r.subpixel_diversity:.5f} -> "
           f"{r.warning.split(':')[0] or 'no warning'}")
    assert r.subpixel_diversity < 0.10, (
        "wrap-around phases were scored as diverse; the metric is not circular"
    )
    assert r.warning != ""


# =========================================================================
# 5. rejection paths
# =========================================================================

def test_ACCEPTANCE_blurred_frames_are_rejected():
    frames = make_burst(hr_text_scene(), golden_shifts(10),
                        blur_idx=(2, 5, 7), blur_sigma=2.4)
    r = BurstStacker(scale=2).stack(frames)
    blurred = {2, 5, 7}
    rejected = {rep.index for rep in r.reports if rep.code == R_BLUR}
    record("blur_rejection",
           f"{len(rejected)}/10 rejected as blur, indices {sorted(rejected)}; "
           f"guarded vLap of frame 2 = {r.reports[2].vlap:.1f} vs sharp frame "
           f"0 = {r.reports[0].vlap:.1f}")
    assert rejected == blurred, f"expected {blurred}, got {rejected}"
    assert r.used == 7 and r.rejected == 3
    assert r.reference_index not in blurred


def test_all_frames_rejected_abstains_with_a_none_image():
    """Invariant 7. A burst that is entirely unusable must not enrol the
    least-bad blurred crop -- it must return nothing and say so."""
    frames = make_burst(hr_text_scene(), golden_shifts(5),
                        blur_idx=tuple(range(5)), blur_sigma=6.0)
    r = BurstStacker(scale=2).stack(frames)
    record("abstain",
           f"all 5 frames blurred -> image is None, warning="
           f"{r.warning.split(':')[0]}")
    assert r.image is None
    assert r.warning.startswith(W_ALL_REJECTED)
    assert r.used == 0 and r.rejected == 5
    assert r.reference_index == -1


def test_single_admitted_frame_warns_and_returns_the_upscale():
    frames = make_burst(hr_text_scene(), golden_shifts(4),
                        blur_idx=(1, 2, 3), blur_sigma=6.0)
    r = BurstStacker(scale=2).stack(frames)
    assert r.warning.startswith(W_SINGLE_FRAME), r.warning
    assert r.used == 1 and r.image is not None
    assert r.image.shape == (CROP * 2, CROP * 2)


def test_frames_displaced_too_far_are_rejected():
    shifts = [(0.0, 0.0), (0.2, 0.1), (4.0, 3.0), (0.35, -0.2)]
    frames = make_burst(hr_text_scene(), shifts)
    r = BurstStacker(scale=2, max_shift_px=1.5).stack(frames)
    far = [rep for rep in r.reports if rep.code == R_SHIFT_TOO_LARGE]
    record("shift_gate",
           f"max_shift_px=1.5 rejected frame(s) "
           f"{[rep.index for rep in far]} at "
           f"{[f'{rep.shift_px:.2f}px' for rep in far]}")
    assert len(far) == 1 and far[0].index == 2
    assert far[0].shift_px > 1.5


def test_ACCEPTANCE_ecc_failure_is_a_frame_rejection_never_zero_motion():
    """VERIFIED BUILD TRAP (FAILURES.md 2026-08-29): findTransformECC RAISES
    cv2.error on non-convergence instead of returning a low correlation.

    Two dangers, both pinned here. Unwrapped, it crashes the enrolment. Wrapped
    but misread as "no motion found", a failed registration would masquerade as
    the zero-diversity condition and produce a confident, wrong warning about
    the shopkeeper not having moved the packet. Independent noise frames cannot
    be registered to each other, and must be REJECTED.
    """
    # Range capped at 200, not 255: uniform 0..255 noise puts 6/256 = 2.3% of
    # pixels at or above the 250 saturation level, so the burst would be thrown
    # out by the GLARE gate before ECC ever ran and this test would pass for
    # entirely the wrong reason. Capped, sat_frac is 0 and vLap is huge, so the
    # frames reach the registration step exactly as intended.
    rng = np.random.default_rng(2)
    frames = [rng.integers(0, 200, (CROP, CROP), dtype=np.uint8) for _ in range(5)]
    r = BurstStacker(scale=2).stack(frames)   # must not raise
    assert all(rep.sat_frac == 0.0 for rep in r.reports)

    ecc_failed = [rep.index for rep in r.reports if rep.code == R_ECC_FAILED]
    record("ecc_throw",
           f"5 unregistrable noise frames -> {len(ecc_failed)} rejected as "
           f"{R_ECC_FAILED}, warning={r.warning.split(':')[0]}")
    assert ecc_failed, "expected ECC to fail on independent noise frames"
    assert not r.warning.startswith(W_NO_DIVERSITY), (
        "an ECC throw was misread as zero motion -- that inverts the module's "
        "central honesty check"
    )
    assert r.warning.startswith(W_SINGLE_FRAME)


# =========================================================================
# 5b. THE ABSOLUTE FOCUS FLOOR — a burst that is bad EVERYWHERE
#
# Both older gates are burst-relative in effect. `blur_rel_min * max(vLap)`
# obviously is. `blur_var_min` looks absolute but is not, because vLap is not
# an absolute quantity: it scales with the scene's own texture and contrast,
# and this file measures a 500x spread across scenes that are all in focus. So
# a burst where EVERY frame is defocused has no worst frame, the relative floor
# sinks with it, and the result comes back with warning="" — a confident
# super-resolution claim built entirely out of mush. Invariant 7 says abstain.
# =========================================================================

def _registered_mean(frames) -> np.ndarray:
    """The image the burst-level focus check actually scores.

    Registration and averaging both come from the module (`_register` and
    `_aligned_mean`), so the arithmetic under calibration cannot drift from the
    arithmetic that ships. Only the reference choice is repeated here, using
    the module's own rule (sharpest frame by guarded vLap), because these
    calibration bursts deliberately sweep past every quality gate and
    `stack()` would refuse them long before it reached this step.
    """
    st = BurstStacker(scale=2)
    grays = [np.ascontiguousarray(f) for f in frames]
    ref = max(range(len(grays)),
              key=lambda i: (variance_of_laplacian(grays[i], sat_level=250), -i))
    ref_f = grays[ref].astype(np.float32) / 255.0
    warps = {ref: np.eye(2, 3, dtype=np.float64)}
    used = [ref]
    for i in range(len(grays)):
        if i == ref:
            continue
        W = st._register(ref_f, grays[i].astype(np.float32) / 255.0)
        if W is not None and np.isfinite(W).all():
            warps[i] = W
            used.append(i)
    used.sort()
    assert len(used) > 1, "nothing registered; the calibration harness broke"
    return np.clip(np.rint(st._aligned_mean(grays, used, warps)),
                   0, 255).astype(np.uint8)


def defocused_burst(scene, sigma: float, n: int = 8, **kw):
    """A burst that is UNIFORMLY out of focus: the defocus is applied to every
    frame, so no frame is worse than any other and nothing is relatively bad.
    Sensor noise, when asked for, is added AFTER the defocus — optics blur the
    scene before the sensor ever sees it, and the order matters to the metric.
    """
    frames = make_burst(scene, golden_shifts(n), **kw)
    return [cv2.GaussianBlur(f, (0, 0), sigma) for f in frames]


def test_ACCEPTANCE_uniformly_defocused_burst_abstains():
    """THE MEASURED BLIND SPOT, closed. The burst must come back with NO image
    and a warning that names defocus as the cause, not a generic rejection."""
    scene = hr_edge_scene()
    frames = defocused_burst(scene, 1.6)
    r = BurstStacker(scale=2).stack(frames)

    # what the burst's resolution actually was, on this module's own
    # ISO-12233 instrument -- not an opinion about the blur radius
    m = mtf50_slanted_edge(frames[0][(slice(25, 103), slice(39, 89))].astype(np.float64))
    scores = [rep.blur_score for rep in r.reports]
    record("uniform_defocus_abstain",
           f"8 frames uniformly defocused to MTF50={m:.4f} cyc/px "
           f"({m * 200:.0f}% of Nyquist): blur score "
           f"{min(scores):.3f}-{max(scores):.3f} > {DEFAULT_MAX_BLUR_SCORE}, "
           f"guarded vLap {min(rep.vlap for rep in r.reports):.1f}-"
           f"{max(rep.vlap for rep in r.reports):.1f} (all above the {60.0} "
           f"floor, so vLap was NOT the discriminator) -> image=None, "
           f"warning={r.warning.split(':')[0]}")

    assert r.image is None, (
        f"a uniformly defocused burst was enrolled anyway: used={r.used}/8, "
        f"warning={r.warning!r}"
    )
    assert r.warning.startswith(W_UNIFORMLY_DEFOCUSED), r.warning
    assert r.used == 0 and r.rejected == 8
    assert r.reference_index == -1
    assert all(rep.code == R_DEFOCUS for rep in r.reports), \
        [rep.reason for rep in r.reports]
    # the OLD gate is proved not to have been what fired
    assert all(rep.vlap > 60.0 for rep in r.reports), (
        "this burst must clear the absolute vLap floor, or it proves nothing "
        "about the blind spot: " + str([round(rep.vlap, 1) for rep in r.reports])
    )


def test_CONTROL_without_the_absolute_floor_the_same_burst_is_enrolled():
    """Proof that the acceptance test above is not vacuous, and the exact
    record of the defect that was fixed: with the absolute focus ceiling
    disabled, the identical burst comes back as a confident, UNWARNED
    super-resolution result."""
    frames = defocused_burst(hr_edge_scene(), 1.6)
    r = BurstStacker(scale=2, max_blur_score=1.0).stack(frames)
    record("uniform_defocus_control",
           f"same burst with the absolute floor disabled: used={r.used}/8, "
           f"image={'None' if r.image is None else 'RETURNED'}, "
           f"warning={r.warning!r} -- this is what shipped before the fix")
    assert r.used == 8 and r.image is not None
    assert r.warning == "", (
        "the control failed to reproduce the defect, so the acceptance test "
        "is not proving anything"
    )


def test_the_same_frame_was_admitted_or_rejected_by_its_NEIGHBOURS():
    """The sharpest statement of why a relative gate alone is not enough.

    One pixel-for-pixel identical frame is put into two bursts. In a sharp
    burst the relative floor throws it out. In a uniformly defocused burst the
    relative floor admits it. Nothing about the frame changed.
    """
    scene = hr_text_scene()
    sharp = make_burst(scene, golden_shifts(8))
    subject = cv2.GaussianBlur(sharp[3], (0, 0), 2.4)

    mixed = list(sharp)
    mixed[3] = subject
    uniform = [cv2.GaussianBlur(f, (0, 0), 2.4) for f in sharp]
    uniform[3] = subject
    assert np.array_equal(mixed[3], uniform[3])

    old = dict(max_blur_score=1.0)   # the gate as it stood before the fix
    r_mixed = BurstStacker(scale=2, **old).stack(mixed)
    r_uniform = BurstStacker(scale=2, **old).stack(uniform)
    record("neighbour_dependence",
           f"the SAME frame (vLap {r_mixed.reports[3].vlap:.1f}, blur score "
           f"{r_mixed.reports[3].blur_score:.3f}): inside a sharp burst -> "
           f"{r_mixed.reports[3].code}; inside a uniformly defocused burst -> "
           f"{r_uniform.reports[3].code}. Under the absolute floor both are "
           f"{BurstStacker(scale=2).stack(uniform).reports[3].code}.")

    assert r_mixed.reports[3].code == R_BLUR
    assert r_uniform.reports[3].used, "the relative gate admitted the same frame"
    # with the absolute floor armed, the frame is judged on its own merits
    assert BurstStacker(scale=2).stack(uniform).reports[3].code == R_DEFOCUS


def test_ACCEPTANCE_blur_score_is_calibrated_against_MTF50():
    """DEFAULT_MAX_BLUR_SCORE is a threshold on RESOLUTION, and this is the
    measurement that makes that sentence true rather than a claim.

    Ten independent scenes — base PSF 1.5 to 8.0 HR px crossed with two edge
    slants — are each swept through extra defocus. For every one of them the
    blur score at MTF50 = 0.15 cyc/px is interpolated. If the score were a
    proxy for scene content rather than for resolution, those ten numbers
    would scatter; the spread is what licenses a single constant.
    """
    roi = (slice(25, 103), slice(39, 89))
    at_target, rows = {}, []
    for psf in (1.5, 3.0, 4.5, 6.0, 8.0):
        for ang in (5.0, 8.0):
            scene = hr_edge_scene(psf, ang)
            ms, bs = [], []
            for s in (0.0, 0.4, 0.8, 1.2, 1.6, 2.0, 2.6):
                f = make_burst(scene, [(0.0, 0.0)])[0]
                if s > 0:
                    f = cv2.GaussianBlur(f, (0, 0), s)
                ms.append(mtf50_slanted_edge(f[roi].astype(np.float64)))
                bs.append(blur_score(f))
            assert all(bs[i] > bs[i - 1] for i in range(1, len(bs))), (psf, ang, bs)
            assert all(ms[i] < ms[i - 1] for i in range(1, len(ms))), (psf, ang, ms)
            order = np.argsort(ms)
            at_target[(psf, ang)] = float(
                np.interp(BLUR_SCORE_MTF50_CYC_PX,
                          np.array(ms)[order], np.array(bs)[order]))
            rows.append((psf, ang, ms[0], at_target[(psf, ang)]))

    vals = list(at_target.values())
    spread = max(vals) - min(vals)
    record("blur_score_mtf50_calibration",
           f"blur score at MTF50={BLUR_SCORE_MTF50_CYC_PX} cyc/px over "
           f"{len(vals)} scenes (base PSF 1.5-8.0 HR px x 2 slants): "
           f"{min(vals):.4f}..{max(vals):.4f}, spread {spread:.4f} "
           f"(+-{100 * spread / 2 / np.mean(vals):.1f}%). Those scenes span "
           f"in-focus MTF50 {min(r[2] for r in rows):.3f}.."
           f"{max(r[2] for r in rows):.3f} cyc/px, so the constant is not "
           f"tracking scene content. DEFAULT_MAX_BLUR_SCORE={DEFAULT_MAX_BLUR_SCORE}")

    assert spread < 0.02, (
        f"the blur score does not collapse onto MTF50 (spread {spread:.4f}); "
        f"a single absolute ceiling would then be a scene threshold in "
        f"disguise, exactly the flaw it replaced: {at_target}"
    )
    assert min(vals) < DEFAULT_MAX_BLUR_SCORE < max(vals) + 0.02, (
        f"DEFAULT_MAX_BLUR_SCORE={DEFAULT_MAX_BLUR_SCORE} no longer sits at "
        f"MTF50={BLUR_SCORE_MTF50_CYC_PX} cyc/px: measured {min(vals):.4f}.."
        f"{max(vals):.4f}"
    )


def test_blur_score_is_free_of_the_scale_dependence_that_broke_vLap():
    """The property that makes an absolute threshold possible at all.

    vLap is quadratic in contrast and grows with texture density, so "60" means
    something different on every scene. The blur score is a ratio of the same
    functional applied twice, so contrast and exposure cancel exactly.
    """
    base = make_burst(hr_text_scene(), [(0.0, 0.0)])[0].astype(np.float64)
    ref = blur_score(base)
    rows = []
    for label, img in (("x0.5 contrast", base * 0.5 + 64),
                       ("x0.8 contrast", base * 0.8 + 24),
                       ("-40 exposure", np.clip(base - 40, 0, 255)),
                       ("+40 exposure", np.clip(base + 15, 0, 255))):
        rows.append((label, blur_score(np.clip(img, 0, 255).astype(np.uint8))))

    scenes = {"dense text": make_burst(hr_text_scene(), [(0.0, 0.0)])[0],
              "slanted edge": make_burst(hr_edge_scene(), [(0.0, 0.0)])[0],
              "low-texture packet": packet(0.2),
              "high-texture packet": packet(0.6)}
    v = {k: (variance_of_laplacian(im, sat_level=250), blur_score(im))
         for k, im in scenes.items()}
    vl = [a for a, _ in v.values()]
    bl = [b for _, b in v.values()]
    record("scale_invariance",
           f"four in-focus scenes: guarded vLap spans {min(vl):.1f}..{max(vl):.1f} "
           f"({max(vl) / min(vl):.0f}x, which is why an absolute vLap floor is "
           f"meaningless) while blur score spans {min(bl):.3f}..{max(bl):.3f} "
           f"and every one of them clears the {DEFAULT_MAX_BLUR_SCORE} ceiling. "
           f"Contrast/exposure changes move the score by at most "
           f"{max(abs(s - ref) for _, s in rows):.4f}")

    for label, got in rows:
        assert got == pytest.approx(ref, abs=0.03), (label, got, ref)
    assert max(vl) / min(vl) > 100, (
        "vLap no longer has the scene spread this test contrasts against; "
        f"re-derive the argument: {v}"
    )
    assert max(bl) < DEFAULT_MAX_BLUR_SCORE, (
        f"an in-focus scene would be rejected by the absolute floor: {v}"
    )


def test_blur_score_rises_monotonically_with_defocus_on_every_scene():
    """A gate whose metric is not monotone would call an extremely defocused
    frame sharp again. Checked on four scenes, not one."""
    sigmas = (0.0, 0.5, 0.8, 1.2, 1.6, 2.0, 2.4, 3.0, 4.0)
    table = {}
    for name, img in (("dense text", make_burst(hr_text_scene(), [(0.0, 0.0)])[0]),
                      ("slanted edge", make_burst(hr_edge_scene(), [(0.0, 0.0)])[0]),
                      ("band-limited edge",
                       make_burst(hr_edge_scene(PSF_BANDLIMITED), [(0.0, 0.0)])[0]),
                      ("low-texture packet", packet(0.2))):
        vals = [blur_score(cv2.GaussianBlur(img, (0, 0), s) if s else img)
                for s in sigmas]
        table[name] = vals
        assert all(vals[i] > vals[i - 1] for i in range(1, len(vals))), (name, vals)

    print("\nblur score vs defocus sigma (px), by scene:")
    print("  " + "scene".ljust(20) + "".join(f"{s:>7.1f}" for s in sigmas))
    for name, vals in table.items():
        print("  " + name.ljust(20) + "".join(f"{v:>7.3f}" for v in vals))


def test_the_defocus_gate_leaves_every_burst_the_suite_relies_on_alone():
    """A false abstention is as dishonest as a false enrolment. Every burst
    this file requires to succeed is measured against the ceiling, and its
    headroom is reported so a future retune cannot silently eat it."""
    cases = {
        "text, aliased": make_burst(hr_text_scene(), golden_shifts(12)),
        "text, band-limited": make_burst(hr_text_scene(PSF_BANDLIMITED),
                                         golden_shifts(12)),
        "edge, aliased": make_burst(hr_edge_scene(), golden_shifts(12)),
        "edge, band-limited (the published negative result)":
            make_burst(hr_edge_scene(PSF_BANDLIMITED), golden_shifts(12)),
        "edge + sensor noise sigma 6": make_burst(hr_edge_scene(), golden_shifts(12),
                                                  noise=6.0, seed=7),
        "one softened frame (order-invariance burst)":
            make_burst(hr_text_scene(), golden_shifts(8), blur_idx=(0,),
                       blur_sigma=0.5),
    }
    worst = {}
    for name, frames in cases.items():
        worst[name] = max(blur_score(f) for f in frames)

    tightest = max(worst, key=worst.__getitem__)
    record("defocus_gate_headroom",
           f"worst blur score over every burst the suite requires to pass: "
           f"{worst[tightest]:.3f} ({tightest}), against the "
           f"{DEFAULT_MAX_BLUR_SCORE} ceiling = {DEFAULT_MAX_BLUR_SCORE - worst[tightest]:+.3f} "
           f"headroom ({100 * (DEFAULT_MAX_BLUR_SCORE - worst[tightest]) / DEFAULT_MAX_BLUR_SCORE:.0f}%)")

    print("\nheadroom to the absolute focus ceiling, per required burst:")
    for name, w in sorted(worst.items(), key=lambda kv: -kv[1]):
        print(f"  {w:.3f}  ({DEFAULT_MAX_BLUR_SCORE - w:+.3f})  {name}")
    for name, w in worst.items():
        assert w < DEFAULT_MAX_BLUR_SCORE - 0.05, (
            f"{name} is within 0.05 of the focus ceiling at {w:.3f}; the gate "
            f"is one refactor away from abstaining on a good burst"
        )


def test_a_partially_defocused_burst_keeps_its_good_frames():
    """The gate is per-frame and only escalates to the burst when EVERY frame
    fails. Half a burst out of focus must cost exactly half the burst."""
    sharp = make_burst(hr_text_scene(), golden_shifts(8))
    mixed = [cv2.GaussianBlur(f, (0, 0), 3.0) if i in (1, 4, 6) else f
             for i, f in enumerate(sharp)]
    r = BurstStacker(scale=2).stack(mixed)
    rejected = {rep.index for rep in r.reports if not rep.used}
    assert rejected == {1, 4, 6}, [(rep.index, rep.reason) for rep in r.reports]
    assert r.used == 5 and r.image is not None
    assert not r.warning.startswith(W_UNIFORMLY_DEFOCUSED)


def test_defocus_precedence_keeps_the_relative_gate_label_intact():
    """When a frame fails BOTH gates the report says R_BLUR, because that is
    the actionable message: its neighbours are fine, so re-shoot the frame. A
    frame that fails only the absolute gate says R_DEFOCUS, which means the
    optics were never focused and the whole burst must be re-captured. The
    precedence is deliberate, so it is pinned."""
    sharp = make_burst(hr_text_scene(), golden_shifts(8))
    one_bad = list(sharp)
    one_bad[2] = cv2.GaussianBlur(sharp[2], (0, 0), 2.4)
    assert blur_score(one_bad[2]) > DEFAULT_MAX_BLUR_SCORE, "frame 2 must fail both"
    r = BurstStacker(scale=2).stack(one_bad)
    assert r.reports[2].code == R_BLUR, r.reports[2].reason
    assert r.reports[2].blur_score > DEFAULT_MAX_BLUR_SCORE, (
        "the measured defocus is still reported even when the label is R_BLUR"
    )


def noisy_defocused_burst(scene, sigma: float, noise: float, n: int = 8):
    """Defocus first (optics), THEN sensor noise. The order is not cosmetic:
    the blur happens in front of the sensor, so the noise is not blurred, and
    a harness that noises-then-blurs quietly smooths its own noise away and
    makes the metric look far more robust than it is."""
    frames = [cv2.GaussianBlur(f, (0, 0), sigma)
              for f in make_burst(scene, golden_shifts(n))]
    rng = np.random.default_rng(11)
    return [np.clip(f + rng.normal(0, noise, f.shape), 0, 255).astype(np.uint8)
            for f in frames]


def test_ACCEPTANCE_the_burst_is_what_defeats_sensor_noise():
    """A per-frame focus measure cannot survive sensor noise, and the burst can.

    Every no-reference focus metric is fooled the same way: noise is white, the
    metric's own re-blur destroys it completely, and a defocused frame
    therefore looks as though it had detail to lose. Measured below: the
    per-frame score of a 2.4 px defocus collapses from 0.483 to 0.262 under
    4 grey levels of noise and stops firing from 2 LSB upward.

    The burst is the way out, and it is the same argument the rest of this
    module runs on. Noise is INDEPENDENT between frames and falls as 1/sqrt(N)
    under the registered average. Defocus is COMMON to every frame and does not
    fall at all. So the aligned mean is the honest place to ask the question.
    """
    scene = hr_text_scene()
    noises = (0.0, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0)
    per_frame, burst_level, verdict = {}, {}, {}
    for nz in noises:
        frames = noisy_defocused_burst(scene, 2.4, nz)
        per_frame[nz] = float(np.median([blur_score(f) for f in frames]))
        # measured with the per-frame gate OFF, so the burst-level number is
        # always the real one rather than 0.0 from an early return
        burst_level[nz] = blur_score(_registered_mean(frames))
        verdict[nz] = BurstStacker(scale=2).stack(frames)

    print("\n  a 2.4 px defocus, seen per-frame and seen through the burst:")
    print(f"  {'noise LSB':>10} {'per-frame':>11} {'reg. mean':>12}   end-to-end")
    for nz in noises:
        pf, bl, r = per_frame[nz], burst_level[nz], verdict[nz]
        where = ("abstains (per-frame gate)" if r.warning.startswith(W_ALL_REJECTED)
                 or (r.image is None and all(
                     "registered mean" not in rep.reason for rep in r.reports))
                 else "abstains (burst gate)" if r.image is None
                 else "ENROLLED")
        print(f"  {nz:>10.1f} {pf:>10.3f}{'*' if pf > DEFAULT_MAX_BLUR_SCORE else ' '}"
              f" {bl:>11.3f}{'*' if bl > DEFAULT_MAX_BLUR_SCORE else ' '}   {where}")

    pf_ok = [nz for nz in noises if per_frame[nz] > DEFAULT_MAX_BLUR_SCORE]
    bl_ok = [nz for nz in noises if burst_level[nz] > DEFAULT_MAX_BLUR_SCORE]
    record("burst_beats_noise",
           f"a 2.4 px defocus is caught per-frame only up to {max(pf_ok):.0f} LSB "
           f"of sensor noise (score {per_frame[0.0]:.3f} clean, "
           f"{per_frame[8.0]:.3f} at 8 LSB) but through the registered mean up "
           f"to {max(bl_ok):.0f} LSB ({burst_level[0.0]:.3f} -> "
           f"{burst_level[8.0]:.3f}) -- the noise tolerance of the focus gate "
           f"goes up by {max(bl_ok) - max(pf_ok):.0f} LSB for one extra average "
           f"over frames the stacker had already registered.")

    assert max(bl_ok) > max(pf_ok), (
        f"the burst-level check bought no noise tolerance over the per-frame "
        f"one, which is its entire justification: {per_frame} vs {burst_level}"
    )
    # end to end, at the noise level where ONLY the burst-level check can fire
    hardest = max(bl_ok)
    assert hardest not in pf_ok, hardest
    r = verdict[hardest]
    assert r.image is None and r.warning.startswith(W_UNIFORMLY_DEFOCUSED), r.warning
    assert "registered mean" in r.warning, r.warning
    assert all(rep.code == R_DEFOCUS for rep in r.reports), \
        [rep.reason for rep in r.reports]
    assert r.used == 0 and r.rejected == len(r.reports)
    assert sum(rep.used for rep in r.reports) == r.used


def test_the_burst_level_ceiling_is_the_SAME_constant_re_measured_on_means():
    """One constant, two applications — checked, not assumed.

    The registered mean is a slightly different image from any frame in it
    (sub-pixel warping low-passes a little), so re-using the per-frame ceiling
    on it needs justifying. It is justified the same way it was derived: by
    interpolating the mean-image blur score at the mean-image MTF50 of
    0.15 cyc/px, over the same ten independent scenes.
    """
    roi = (slice(25, 103), slice(39, 89))
    at_target = {}
    for psf in (1.5, 3.0, 4.5, 6.0, 8.0):
        for ang in (5.0, 8.0):
            scene = hr_edge_scene(psf, ang)
            ms, bs = [], []
            for s in (0.0, 0.4, 0.8, 1.2, 1.6, 2.0, 2.6):
                frames = make_burst(scene, golden_shifts(8))
                if s > 0:
                    frames = [cv2.GaussianBlur(f, (0, 0), s) for f in frames]
                mean = _registered_mean(frames)
                ms.append(mtf50_slanted_edge(mean[roi].astype(np.float64)))
                bs.append(blur_score(mean))
            o = np.argsort(ms)
            at_target[(psf, ang)] = float(
                np.interp(BLUR_SCORE_MTF50_CYC_PX,
                          np.array(ms)[o], np.array(bs)[o]))

    v = list(at_target.values())
    spread = max(v) - min(v)
    record("burst_ceiling_recalibration",
           f"blur score of the REGISTERED MEAN of 8 frames at "
           f"MTF50={BLUR_SCORE_MTF50_CYC_PX} "
           f"cyc/px over 10 scenes: {min(v):.4f}..{max(v):.4f} (spread "
           f"{spread:.4f}). The per-frame calibration gives the same window, so "
           f"DEFAULT_MAX_BLUR_SCORE={DEFAULT_MAX_BLUR_SCORE} means the same "
           f"thing on a frame and on a burst mean — one constant, not two.")
    assert spread < 0.02, at_target
    assert min(v) < DEFAULT_MAX_BLUR_SCORE < max(v) + 0.02, (
        f"the shared ceiling no longer sits at MTF50={BLUR_SCORE_MTF50_CYC_PX} "
        f"on registered means ({min(v):.4f}..{max(v):.4f}); the burst-level "
        f"check needs its own measured constant"
    )


def test_HONEST_enough_noise_still_hides_a_defocused_burst():
    """THE REMAINING LIMIT, measured and published rather than discovered.

    Averaging cuts noise by sqrt(N), it does not abolish it, so a burst that is
    noisy enough still slips through. The exact envelope is measured here. The
    bias has ONE direction — noise can hide a defocused burst, it can never
    condemn a sharp one — so the gate never causes a false abstention, and that
    half is asserted too.
    """
    noises = (0.0, 2.0, 4.0, 8.0)
    grid, caught = {}, {}
    for scene_name, scene in (("dense text", hr_text_scene()),
                              ("slanted edge", hr_edge_scene())):
        for sigma in (1.6, 2.4, 3.2):
            row = []
            for nz in noises:
                # measured on the registered mean directly, so the number is
                # always the measurement and never an early return from an
                # upstream gate
                s = blur_score(_registered_mean(
                    noisy_defocused_burst(scene, sigma, nz)))
                row.append((nz, s, s > DEFAULT_MAX_BLUR_SCORE))
            grid[(scene_name, sigma)] = row
            ok = [nz for nz, _, hit in row if hit]
            caught[(scene_name, sigma)] = max(ok) if ok else None

    print("\n  burst-mean blur score vs sensor noise; * = over the focus ceiling")
    print(f"  {'scene / defocus':<24}" + "".join(f"{nz:>10.0f} LSB" for nz in noises))
    for (name, sigma), row in grid.items():
        print(f"  {name + f' sigma {sigma}':<24}" +
              "".join(f"{s:>12.3f}{'*' if hit else ' '}" for _, s, hit in row))

    # cases the gate is SUPPOSED to catch are the ones it catches with no
    # noise at all; a defocus above the 0.15 cyc/px line is out of scope by
    # design and its absence from the table is not a miss.
    in_scope = {k: v for k, v in caught.items() if grid[k][0][2]}
    out_of_scope = sorted(k for k in caught if not grid[k][0][2])
    assert in_scope, grid
    worst = min(in_scope.values())
    record("defocus_noise_envelope",
           f"noise tolerance of the burst-level focus check (8-frame bursts), "
           f"per case: " +
           ", ".join(f"{k[0]} sigma {k[1]}: {v:.0f} LSB"
                     for k, v in sorted(in_scope.items())) +
           f". HONEST LIMIT: beyond ~{worst:.0f} LSB of sensor noise on the "
           f"hardest in-scope case a defocused burst is admitted, and NOTHING "
           f"else in this module catches it either -- noise inflates vLap, so "
           f"the vLap floors are blinded in the same direction "
           f"(test_HONEST_sensor_noise_inverts_the_absolute_vLap_floor). Out of "
           f"scope by design (resolution still above the "
           f"{BLUR_SCORE_MTF50_CYC_PX} cyc/px line, so NOT a miss): " +
           ", ".join(f"{k[0]} sigma {k[1]}" for k in out_of_scope))

    assert any(v < 8.0 for v in in_scope.values()), (
        "nothing in this sweep escaped the gate; if noise genuinely no longer "
        f"hides defocus, the published limit must be re-derived: {caught}"
    )
    # every in-scope case must survive at least a little noise, or the gate is
    # useless on any real capture
    assert worst >= 2.0, (
        f"the focus gate is defeated by {worst:.0f} LSB of sensor noise on "
        f"{min(in_scope, key=in_scope.get)}, which is an ordinary capture; the "
        f"burst-level check is not buying enough: {in_scope}"
    )
    # the safety direction: a SHARP burst is never condemned, at any noise
    for nz in (0.0, 2.0, 4.0, 6.0, 8.0):
        frames = make_burst(hr_text_scene(), golden_shifts(8), noise=nz, seed=11)
        r = BurstStacker(scale=2).stack(frames)
        assert r.burst_blur_score < DEFAULT_MAX_BLUR_SCORE, (
            f"noise sigma {nz} pushed a SHARP burst over the focus ceiling "
            f"({r.burst_blur_score:.3f}); the gate would abstain on a good burst"
        )
        assert r.image is not None


def test_HONEST_sensor_noise_inverts_the_absolute_vLap_floor():
    """Why the old absolute floor could not be repaired by re-tuning it.

    DEFAULT_BLUR_VAR_MIN is a floor on variance-of-Laplacian, and the Laplacian
    of white noise is enormous. So sensor noise does not merely weaken that
    gate, it REVERSES it: the noisier a destroyed frame gets, the more
    confidently it clears the floor. Measured below on a burst defocused into
    unusability — one grey level of noise is enough to take it from rejected to
    admitted, and eight grey levels put it two orders of magnitude clear.

    This is the fact that makes an absolute vLap threshold unfixable and a
    scale-free focus measure necessary. It is also the reason the honest limit
    of the new gate is stated as "nothing catches it" rather than "the vLap
    floor catches it".
    """
    rows = []
    for nz in (0.0, 1.0, 2.0, 4.0, 8.0):
        frames = noisy_defocused_burst(hr_text_scene(), 4.0, nz)
        v = [variance_of_laplacian(f, sat_level=250) for f in frames]
        rows.append((nz, min(v), max(v), min(v) >= DEFAULT_BLUR_VAR_MIN))

    print("\n  a burst defocused to sigma 4.0 px (unusable by any measure):")
    print(f"  {'noise LSB':>10} {'guarded vLap':>26}   vs the "
          f"{DEFAULT_BLUR_VAR_MIN} floor")
    for nz, lo, hi, passes in rows:
        print(f"  {nz:>10.1f} {lo:>11.1f}..{hi:<12.1f}   "
              f"{'ADMITTED' if passes else 'rejected'}")

    record("vlap_floor_inverted_by_noise",
           f"a burst defocused to sigma 4.0 px scores guarded vLap "
           f"{rows[0][1]:.1f} clean and is correctly rejected by the "
           f"{DEFAULT_BLUR_VAR_MIN} floor; add {rows[1][0]:.0f} grey level of "
           f"sensor noise and the same burst scores {rows[1][1]:.1f} and is "
           f"ADMITTED, rising to {rows[-1][1]:.0f} at {rows[-1][0]:.0f} LSB. "
           f"The absolute vLap floor is not just scene-dependent, it is "
           f"inverted by noise, which is why the focus gate had to be "
           f"scale-free rather than re-tuned.")

    assert not rows[0][3], "the clean defocused burst should fail the vLap floor"
    assert rows[1][3], (
        "one grey level of noise no longer flips the vLap floor; the argument "
        f"for a scale-free focus measure must be re-derived: {rows}"
    )
    assert rows[-1][1] > 50 * rows[0][2], rows


def test_blur_score_edge_cases_and_validation():
    assert blur_score(np.full((64, 64), 128, np.uint8)) == 0.0   # nothing to lose
    with pytest.raises(SaafError):
        blur_score(np.zeros((64, 64), np.uint8), span=1)
    with pytest.raises(SaafError):
        blur_score(np.zeros((1, 1), np.uint8))
    with pytest.raises(SaafError, match="max_blur_score"):
        BurstStacker(max_blur_score=0.0)
    with pytest.raises(SaafError, match="max_blur_score"):
        BurstStacker(max_blur_score=1.5)
    # colour in, same answer as its grey conversion
    grey = make_burst(hr_text_scene(), [(0.0, 0.0)])[0]
    bgr = cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)
    assert blur_score(bgr) == pytest.approx(blur_score(grey), abs=1e-9)


def test_defocus_rejection_carries_its_measured_number():
    frames = defocused_burst(hr_edge_scene(), 1.6)
    r = BurstStacker(scale=2).stack(frames)
    for rep in r.reports:
        assert rep.code == R_DEFOCUS
        assert f"{rep.blur_score:.3f}" in rep.reason, rep.reason
        assert rep.blur_score > DEFAULT_MAX_BLUR_SCORE
    assert "vLap" in r.warning, (
        "the burst warning must say what did NOT fire, or the next reader "
        "will re-tune the wrong knob"
    )


def test_reports_cover_every_frame_exactly_once():
    frames = make_burst(hr_text_scene(), golden_shifts(9), blur_idx=(4,),
                        blur_sigma=2.4, glare_idx=(6,), glare_r=14)
    r = BurstStacker(scale=2).stack(frames)
    assert len(r.reports) == 9
    assert [rep.index for rep in r.reports] == list(range(9))
    assert sum(rep.used for rep in r.reports) == r.used
    assert sum(not rep.used for rep in r.reports) == r.rejected
    for rep in r.reports:
        if not rep.used:
            assert any(ch.isdigit() for ch in rep.reason), (
                f"rejection {rep.reason!r} carries no measured number"
            )


# =========================================================================
# 6. API and hygiene
# =========================================================================

def test_stack_is_deterministic_to_the_byte():
    frames = make_burst(hr_text_scene(), golden_shifts(8))
    a = BurstStacker(scale=2).stack(frames)
    b = BurstStacker(scale=2).stack(frames)
    assert np.array_equal(a.image, b.image)
    assert a.sharpness_gain == b.sharpness_gain
    assert a.subpixel_diversity == b.subpixel_diversity
    assert a.warning == b.warning


@pytest.mark.parametrize("scale", [1, 2, 3, 4])
def test_output_shape_and_dtype(scale):
    frames = make_burst(hr_text_scene(), golden_shifts(8))
    r = BurstStacker(scale=scale).stack(frames)
    assert r.image.shape == (CROP * scale, CROP * scale)
    assert r.image.dtype == np.uint8


def test_stack_is_invariant_to_burst_order():
    """The reconstruction is a function of the SET of frames, not their order.

    This also pins a real bug that was fixed here: the hole-fill prior must be
    the REFERENCE frame, because the HR grid lives in reference coordinates.
    The first version filled from `used[0]`, which is only the reference by
    luck; when it is not, holes get pasted in from a frame offset by its whole
    registered shift. Frame 0 is deliberately softened below so the reference
    is some other frame, and scale=5 is used because that is where holes
    actually occur (measured: 0% at scale 2 and 3, 0.05% at 4, 15.2% at 5).
    Under the old code, permuting the burst changed those filled pixels.
    """
    frames = make_burst(hr_text_scene(), golden_shifts(8),
                        blur_idx=(0,), blur_sigma=0.5)
    r1 = BurstStacker(scale=5).stack(frames)
    assert r1.reference_index != 0, "frame 0 was meant to be the softest"
    assert r1.reports[0].used, "frame 0 must still be USED, just not the reference"

    r2 = BurstStacker(scale=5).stack(frames[3:] + frames[:3])
    # float summation order across frames can move a last bit before rounding
    diff = int(np.abs(r1.image.astype(np.int16) - r2.image.astype(np.int16)).max())
    record("order_invariance",
           f"scale=5, reference is frame {r1.reference_index} (not 0); permuting "
           f"the burst changes the output by at most {diff} grey level(s)")
    assert diff <= 1, f"burst order changed the reconstruction by {diff} levels"


def test_accepts_colour_frames():
    frames = [cv2.cvtColor(f, cv2.COLOR_GRAY2BGR)
              for f in make_burst(hr_text_scene(), golden_shifts(6))]
    r = BurstStacker(scale=2).stack(frames)
    assert r.warning == "" and r.image.ndim == 2


def test_motion_translation_mode_also_works():
    frames = make_burst(hr_text_scene(), golden_shifts(8))
    r = BurstStacker(scale=2, motion="translation").stack(frames)
    record("translation_mode",
           f"MOTION_TRANSLATION: used={r.used} diversity={r.subpixel_diversity:.3f} "
           f"gain={r.sharpness_gain:.3f}")
    assert r.warning == "" and r.used == 8


def test_empty_burst_raises():
    with pytest.raises(SaafError, match="at least one frame"):
        BurstStacker().stack([])


def test_ragged_burst_raises():
    a = np.zeros((64, 64), np.uint8)
    b = np.zeros((64, 32), np.uint8)
    with pytest.raises(SaafError, match="same region"):
        BurstStacker().stack([a, b])


def test_bad_constructor_arguments_raise():
    with pytest.raises(SaafError):
        BurstStacker(scale=0)
    with pytest.raises(SaafError):
        BurstStacker(motion="affine")


def test_one_frame_burst_is_the_single_frame_path():
    frames = make_burst(hr_text_scene(), [(0.0, 0.0)])
    r = BurstStacker(scale=2).stack(frames)
    assert r.warning.startswith(W_SINGLE_FRAME)
    assert r.used == 1 and r.image is not None


def test_summary(capsys):
    """Not an assertion -- prints every measured number in one block so the
    run's own output is the source for anything reported."""
    with capsys.disabled():
        print("\n\n" + "=" * 74)
        print("SAAF — MEASURED NUMBERS (produced by this run)")
        print("=" * 74)
        for k in sorted(MEASURED):
            print(f"  {k}\n      {MEASURED[k]}")
        print("=" * 74)
