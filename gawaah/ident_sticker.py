"""S6 IDENT — "does this sticker still pay you?"

THE SAFETY CONSTRAINT THAT DEFINES THIS MODULE
----------------------------------------------
There is no QR encoder here. No QR decoder. No module-grid reconstruction, no
finder-pattern locator, no Reed-Solomon, no version table, and no code path that
can emit or reconstruct a UPI payload string. The original PEEL design did decode
and re-render payloads; it was deleted because a payload constructor living in a
public repo is a forgery primitive, and the shipped feature does not need one.

What survives compares IMAGES:

    enrolment   the merchant photographs each counter sticker ON the mat; we keep
                the rectified crop and nothing else
    check       a fresh rectified crop is re-registered to the stored one with
                cv2.findTransformECC, then absdiff -> threshold -> morphologyEx
                (OPEN); the IGNITED PIXEL FRACTION is the single scalar verdict

The module therefore cannot say WHO a sticker pays. It can only say whether the
rectangle in front of the camera is still the rectangle the merchant enrolled.
That is a weaker sentence than the one PEEL used to claim, and it is the only one
this mechanism can honestly make.

WHY findTransformECC IS THE WHOLE FEATURE
-----------------------------------------
A naive absdiff between two photographs of the SAME unchanged sticker is a
false-accusation machine. Every number below is printed by
tests/test_ident_sticker.py on each run; none of them is typed in.

    60 genuine re-lays       no ECC  mean 18.97 %  p95 28.50 %  100 % accused
                            with ECC mean  0.18 %  p95  0.68 %    0 % accused

    end to end on the mat, an unchanged sticker with the crop box 2 px off:
                             no ECC 22.41 % -> TAMPERED
                            with ECC  0.00 % -> GENUINE

Without the re-registration step every honest shopkeeper is accused. See
test_ACCEPTANCE_ecc_is_the_whole_feature and
test_END_TO_END_crop_box_jitter_is_what_ecc_absorbs -- the second matters more,
because it shows the residual ECC removes is CROP-BOX error, not plane error:
the TAKHTI homography itself lands inside 0.21 px across 0-4 degrees of tilt.

ABSTENTION (invariant 7: unknown -> amber, never red)
-----------------------------------------------------
Every way this comparison can be untrustworthy returns UNREGISTERABLE with a
named reason, never TAMPERED:

    NOT_ENROLLED          no enrolment under that name -- the case that produced
                          a public false accusation in the prior design
    ECC_NO_CONVERGENCE    findTransformECC threw; the crops cannot be registered
    ECC_LOW_CORRELATION   it converged onto a wrong optimum. Measured: a false
                          optimum lands at cc 0.057-0.190, a genuinely swapped
                          sticker at 0.328-0.416, a genuine re-lay at 0.910+
    FOCUS_MISMATCH        the fresh crop is softer than the enrolment. Measured
                          with the gate lifted, an UNCHANGED sticker at 2.5, 3.0
                          and 4.0 px of defocus reports 4.13 %, 10.46 % and
                          23.77 % ignited -- three false accusations
    OBSCURED              glare or an occluder destroyed the structure being
                          compared, rather than changing it
    INSUFFICIENT_OVERLAP  the registered crops barely overlap
    CROP_TOO_SMALL / CROP_UNREADABLE / ASPECT_MISMATCH / CROP_FEATURELESS

HONEST LIMITS, all of them measured rather than guessed
-------------------------------------------------------
  * ALL EVIDENCE HERE IS SYNTHETIC. The fixtures are random module grids under a
    modelled camera. No real counter sticker has been photographed. The doc's
    120-photo held-out set is not what produced these numbers and nothing here
    substitutes for it.
  * A substituted patch under about 2 % of the sticker is MISSED (recall 0/12 at
    1.9 %, 12/12 at 5.1 %). The feature detects patches, not pinpricks.
  * A Euclidean fit cannot remove sticker curl. Past about 3 px of non-rigid
    relief the genuine distribution collides with the tampered one (genuine max
    goes 0.53 % -> 8.36 % between 1.5 px and 3.0 px of relief). Flat stickers.
  * The ECC floor has 0.110 of headroom on the abstain side and only 0.028 on
    the accuse side, so a swapped sticker whose correlation dips under 0.30
    becomes amber rather than red. That is a miss, not a false accusation, and
    it is the direction the doctrine prefers -- but it is a miss.
  * IDENT cannot say WHO a sticker pays, only whether it is the rectangle that
    was enrolled. A perfect forgery of the enrolled image reads GENUINE.

Money never touches this file: IDENT produces a colour, and no feature turns a
light green (invariant 2).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .clock import Clock

# --- comparison constants, every one calibrated by a test in this repo -------
# See test_calibration_table_that_chose_every_constant, which re-derives the
# curve each of these sits on and fails if the chosen value stops being the
# right one.

DIFF_THRESHOLD = 40
"""Grey levels on the flat-fielded pair. ~21 % of the +-96 module contrast.
Lower ignites on registration ridges; higher hides the ECC finding entirely
(at 64 a 1 px misregistration reports 0.00 %, because MORPH_OPEN alone erases
a ridge that thin, and the naive-diff hazard becomes invisible)."""

OPEN_KERNEL = 3
"""MORPH_OPEN erases anything thinner than this in both axes: sensor speckle
and hairline registration ridges go, a substituted module (>=4 px here) stays."""

TAMPER_GATE = 0.03
"""Ignited fraction at or above which the crop is TAMPERED. 3 % is the kill-gate
value from the feature's own acceptance criterion. Measured margin on this rig,
n=30 each under identical imaging: genuine re-lays max 0.93 %, a 9.9 %
substituted patch min 6.35 % -- 6.8x, with the gate between them."""

MIN_ECC_CC = 0.30
"""ECC correlation floor. Below it the warp is a wrong optimum, not a fit."""

MIN_SHARPNESS_RATIO = 0.55
"""fresh sharpness / enrolled sharpness. Gain-invariant (high-band RMS over
mid-band RMS), so a dim photograph is not mistaken for a soft one."""

MAX_BLIND_FRACTION = 0.20
"""Fraction of the compared area where structure was destroyed rather than
changed. Above it there is nothing left to compare."""

MIN_VALID_FRACTION = 0.50
"""Fraction of the enrolment that the registered fresh crop must still cover."""

MIN_CROP_PX = 64
"""Below this the modules of any real counter sticker are under a pixel wide."""

MIN_ENROLMENT_CONTRAST = 8.0
"""p90 of |high-pass| in grey levels. Below it the crop carries no structure."""

MAX_ASPECT_DRIFT = 0.10
ERODE_BORDER = 11
FLATFIELD_SIGMA_DIVISOR = 12.0
BLIND_FLAT = 10.0
BLIND_STRUCTURED = 30.0
ECC_ITERATIONS = 200
ECC_EPS = 1e-6
ECC_GAUSS_FILT = 5

GENUINE = "GENUINE"
TAMPERED = "TAMPERED"
UNREGISTERABLE = "UNREGISTERABLE"

# Reason codes. UNREGISTERABLE always carries one of the abstentions.
R_COMPARED = "COMPARED"
R_NOT_ENROLLED = "NOT_ENROLLED"
R_CROP_TOO_SMALL = "CROP_TOO_SMALL"
R_CROP_UNREADABLE = "CROP_UNREADABLE"
R_CROP_FEATURELESS = "CROP_FEATURELESS"
R_ASPECT_MISMATCH = "ASPECT_MISMATCH"
R_ECC_NO_CONVERGENCE = "ECC_NO_CONVERGENCE"
R_ECC_LOW_CORRELATION = "ECC_LOW_CORRELATION"
R_FOCUS_MISMATCH = "FOCUS_MISMATCH"
R_OBSCURED = "OBSCURED"
R_INSUFFICIENT_OVERLAP = "INSUFFICIENT_OVERLAP"

ABSTENTIONS = frozenset({
    R_NOT_ENROLLED, R_CROP_TOO_SMALL, R_CROP_UNREADABLE, R_CROP_FEATURELESS,
    R_ASPECT_MISMATCH, R_ECC_NO_CONVERGENCE, R_ECC_LOW_CORRELATION,
    R_FOCUS_MISMATCH, R_OBSCURED, R_INSUFFICIENT_OVERLAP,
})


class StickerError(ValueError):
    """Raised on an enrolment that must not be stored."""


@dataclass(frozen=True)
class StickerVerdict:
    """One comparison. `ignited_fraction` is the scalar the feature publishes."""

    name: str
    ignited_fraction: float | None
    registered: bool
    verdict: str
    ecc_ok: bool
    reason: str = R_COMPARED
    ecc_cc: float | None = None
    ecc_shift_px: float | None = None
    ecc_rotation_deg: float | None = None
    blind_fraction: float | None = None
    sharpness_ratio: float | None = None
    valid_fraction: float | None = None

    @property
    def abstained(self) -> bool:
        return self.verdict == UNREGISTERABLE

    def evidence(self) -> dict[str, Any]:
        """The publishable numbers, shaped for the audit ledger (SIX.md §5)."""
        def r(v: float | None, n: int = 6) -> float | None:
            return None if v is None else round(float(v), n)
        return {
            "sticker": self.name,
            "verdict": self.verdict,
            "reason": self.reason,
            "registered": self.registered,
            "ecc_ok": self.ecc_ok,
            "ignited_fraction": r(self.ignited_fraction),
            "ecc_cc": r(self.ecc_cc),
            "ecc_shift_px": r(self.ecc_shift_px, 3),
            "ecc_rotation_deg": r(self.ecc_rotation_deg, 3),
            "blind_fraction": r(self.blind_fraction),
            "sharpness_ratio": r(self.sharpness_ratio, 4),
            "valid_fraction": r(self.valid_fraction),
        }


@dataclass(frozen=True)
class EnrolmentRecord:
    name: str
    shape: tuple[int, int]
    contrast: float
    sharpness: float
    digest: str
    enrolled_ts: str | None = None


# ------------------------------------------------------------------ imaging


def _as_gray(img: np.ndarray) -> np.ndarray:
    if img is None or not isinstance(img, np.ndarray):
        raise StickerError("crop must be a numpy array")
    if img.ndim == 3:
        if img.shape[2] == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        elif img.shape[2] == 4:
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
        else:
            raise StickerError(f"unsupported channel count {img.shape[2]}")
    elif img.ndim != 2:
        raise StickerError(f"unsupported crop shape {img.shape}")
    if img.dtype != np.uint8:
        img = np.clip(img, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(img)


def _highpass(gray: np.ndarray) -> np.ndarray:
    f = gray.astype(np.float32)
    sigma = max(gray.shape) / FLATFIELD_SIGMA_DIVISOR
    return f - cv2.GaussianBlur(f, (0, 0), sigma)


def contrast_of(gray: np.ndarray) -> float:
    """p90 of |high-pass|: how much module structure the crop actually carries."""
    return float(np.percentile(np.abs(_highpass(gray)), 90))


def sharpness_of(gray: np.ndarray) -> float:
    """High-band RMS over mid-band amplitude.

    Deliberately a RATIO: dividing by the crop's own mid-band makes it invariant
    to exposure, so a dim-but-sharp photograph is not gated out as defocused.
    An absolute amplitude was tried first and failed for exactly that reason --
    a gain of 0.8 looked like defocus.

    Measured on this rig: genuine re-lays 0.62-1.18 of the enrolment's value,
    a 2.0 px-sigma defocus 0.521, a 3.0 px-sigma defocus 0.409.
    """
    mid = contrast_of(gray)
    if mid < 1e-6:
        return 0.0
    lap = cv2.Laplacian(gray.astype(np.float32), cv2.CV_32F, ksize=3)
    return float(np.sqrt(float(np.mean(lap * lap))) / mid)


def flatfield(gray: np.ndarray) -> np.ndarray:
    """Remove the illumination field, keep the modules.

    The counter light is never uniform and the two photographs are minutes or
    weeks apart, so the pair is compared in a band-limited, contrast-normalised
    space rather than in raw grey levels. Output is centred on 128 with the
    module contrast scaled to about +-96.
    """
    hp = _highpass(gray)
    s = float(np.percentile(np.abs(hp), 90))
    if s < 1e-3:
        return np.full(gray.shape, 128, np.uint8)
    return np.clip(hp * (96.0 / s) + 128.0, 0, 255).astype(np.uint8)


def _local_std(img: np.ndarray, win: int) -> np.ndarray:
    f = img.astype(np.float32)
    m = cv2.boxFilter(f, cv2.CV_32F, (win, win))
    m2 = cv2.boxFilter(f * f, cv2.CV_32F, (win, win))
    return np.sqrt(np.maximum(m2 - m * m, 0.0))


def _std_window(shape: tuple[int, int]) -> int:
    w = min(shape) // 16
    w = max(5, w)
    return w if w % 2 else w + 1


def _blind_mask(ref_ff: np.ndarray, fresh_ff: np.ndarray) -> np.ndarray:
    """Where structure was DESTROYED rather than changed.

    Glare and occlusion leave a flat region where the enrolment had modules.
    A substituted patch leaves modules -- different ones, but still modules --
    so this mask must key on the ABSENCE of local structure in the fresh crop,
    not on brightness: a white substituted module looks exactly like a
    highlight. Both rules are run side by side in
    test_glare_does_not_hide_a_real_substitution, where the brightness-keyed
    version writes off enough of a real 9.9 % substitution to drop it from
    6.54 % ignited to 3.77 %, a hair above the 3 % gate.
    """
    win = _std_window(ref_ff.shape)
    lost = ((_local_std(fresh_ff, win) < BLIND_FLAT)
            & (_local_std(ref_ff, win) > BLIND_STRUCTURED))
    m = (lost.astype(np.uint8)) * 255
    return cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))


def _ecc_align(ref: np.ndarray, fresh: np.ndarray) -> tuple[float, np.ndarray]:
    """cv2.findTransformECC, MOTION_EUCLIDEAN.

    EUCLIDEAN, not AFFINE, and that is a safety choice: the crops both come off
    the same rectified metric plane, so scale and shear are already fixed, and
    every extra degree of freedom is a degree of freedom with which the fit can
    absorb real tampering. Three parameters can slide and rotate the crop; they
    cannot deform a substituted patch into agreement.

    Note for the browser port: the JS constants are cv.TermCriteria_COUNT /
    cv.TermCriteria_EPS. The OpenCV-4 names used here do not exist there, and
    passing them yields type=0 and an assertion throw inside ecc.cpp.
    """
    warp = np.eye(2, 3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
                ECC_ITERATIONS, ECC_EPS)
    cc, warp = cv2.findTransformECC(
        ref.astype(np.float32), fresh.astype(np.float32), warp,
        cv2.MOTION_EUCLIDEAN, criteria, None, ECC_GAUSS_FILT,
    )
    return float(cc), warp


def _apply_warp(fresh: np.ndarray, warp: np.ndarray,
                shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Bring the fresh crop into the enrolment's frame; also return its support."""
    h, w = shape
    warped = cv2.warpAffine(
        fresh, warp, (w, h),
        flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP,
        borderMode=cv2.BORDER_REPLICATE,
    )
    support = cv2.warpAffine(
        np.full(fresh.shape, 255, np.uint8), warp, (w, h),
        flags=cv2.INTER_NEAREST + cv2.WARP_INVERSE_MAP,
        borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )
    return warped, cv2.erode(support, np.ones((ERODE_BORDER, ERODE_BORDER), np.uint8))


@dataclass
class DiffResult:
    ignited_fraction: float
    blind_fraction: float
    valid_fraction: float
    ignited_mask: np.ndarray
    valid_mask: np.ndarray


def diff_ignited(ref_gray: np.ndarray, fresh_warped: np.ndarray,
                 support: np.ndarray) -> DiffResult:
    """absdiff -> threshold -> morphologyEx(OPEN) -> ignited pixel fraction.

    This is the entire verdict. It is deliberately one number.
    """
    a = flatfield(ref_gray)
    b = flatfield(fresh_warped)

    blind = _blind_mask(a, b)
    total = ref_gray.size
    support_n = int(np.count_nonzero(support))
    blind_n = int(np.count_nonzero(cv2.bitwise_and(blind, support)))
    valid = cv2.bitwise_and(support, cv2.bitwise_not(blind))
    valid_n = int(np.count_nonzero(valid))

    d = cv2.absdiff(a, b)
    _, hot = cv2.threshold(d, DIFF_THRESHOLD, 255, cv2.THRESH_BINARY)
    hot = cv2.morphologyEx(
        hot, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (OPEN_KERNEL, OPEN_KERNEL)),
    )
    hot = cv2.bitwise_and(hot, valid)

    frac = float(np.count_nonzero(hot) / valid_n) if valid_n else 1.0
    return DiffResult(
        ignited_fraction=frac,
        blind_fraction=float(blind_n / support_n) if support_n else 1.0,
        valid_fraction=float(valid_n / total),
        ignited_mask=hot,
        valid_mask=valid,
    )


# ------------------------------------------------------------------ registry


def _slug(name: str) -> str:
    """Filesystem-safe, collision-resistant stem. The human name lives in the
    sidecar JSON, so no sticker name can escape the registry directory."""
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:24]


class StickerRegistry:
    """The enrolled counter stickers, as images on disk and nothing else.

    Nothing in this class knows what a sticker means. It stores rectangles.
    """

    def __init__(self, directory: str | Path) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    # -- enrolment ----------------------------------------------------------

    def _paths(self, name: str) -> tuple[Path, Path]:
        s = _slug(name)
        return self.dir / f"{s}.png", self.dir / f"{s}.json"

    def enrol(self, name: str, rectified_crop: np.ndarray,
              clock: Clock | None = None) -> EnrolmentRecord:
        """Store one rectified crop as the reference for `name`.

        Raises rather than storing a crop that could never be compared: a bad
        enrolment is silent forever, and every later check inherits it.
        """
        if not isinstance(name, str) or not name.strip():
            raise StickerError("sticker name must be a non-empty string")
        gray = _as_gray(rectified_crop)
        if min(gray.shape) < MIN_CROP_PX:
            raise StickerError(
                f"enrolment crop {gray.shape} smaller than {MIN_CROP_PX}px"
            )
        contrast = contrast_of(gray)
        if contrast < MIN_ENROLMENT_CONTRAST:
            raise StickerError(
                f"enrolment crop carries no structure (contrast {contrast:.2f} "
                f"< {MIN_ENROLMENT_CONTRAST}); nothing could ever be compared"
            )

        png, meta = self._paths(name)
        ok, buf = cv2.imencode(".png", gray)
        if not ok:
            raise StickerError("could not encode the enrolment crop")
        blob = buf.tobytes()
        png.write_bytes(blob)
        rec = EnrolmentRecord(
            name=name,
            shape=(int(gray.shape[0]), int(gray.shape[1])),
            contrast=contrast,
            sharpness=sharpness_of(gray),
            digest=hashlib.sha256(blob).hexdigest(),
            enrolled_ts=clock.now_iso() if clock is not None else None,
        )
        meta.write_text(json.dumps({
            "name": rec.name, "shape": list(rec.shape),
            "contrast": rec.contrast, "sharpness": rec.sharpness,
            "digest": rec.digest, "enrolled_ts": rec.enrolled_ts,
            "file": png.name,
        }, sort_keys=True, ensure_ascii=False), encoding="utf-8")
        return rec

    def is_enrolled(self, name: str) -> bool:
        # Both files, deliberately. The PNG is written before the sidecar, so a
        # process killed between the two leaves a crop with no record -- and the
        # only safe reading of a half-written enrolment is that it never
        # happened, which then abstains as NOT_ENROLLED rather than comparing
        # against something nobody confirmed.
        png, meta = self._paths(name)
        return png.exists() and meta.exists()

    def record(self, name: str) -> EnrolmentRecord | None:
        _, meta = self._paths(name)
        if not meta.exists():
            return None
        d = json.loads(meta.read_text(encoding="utf-8"))
        return EnrolmentRecord(
            name=d["name"], shape=(int(d["shape"][0]), int(d["shape"][1])),
            contrast=float(d["contrast"]), sharpness=float(d["sharpness"]),
            digest=d["digest"], enrolled_ts=d.get("enrolled_ts"),
        )

    def reference(self, name: str) -> np.ndarray | None:
        """The stored enrolment crop, or None."""
        png, meta = self._paths(name)
        if not (png.exists() and meta.exists()):
            return None
        img = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
        return None if img is None else img

    def names(self) -> list[str]:
        out = []
        for meta in sorted(self.dir.glob("*.json")):
            try:
                out.append(json.loads(meta.read_text(encoding="utf-8"))["name"])
            except (json.JSONDecodeError, KeyError):
                continue
        return sorted(out)

    def forget(self, name: str) -> bool:
        png, meta = self._paths(name)
        gone = False
        for p in (png, meta):
            if p.exists():
                p.unlink()
                gone = True
        return gone

    # -- comparison ---------------------------------------------------------

    def compare(self, name: str, fresh_crop: np.ndarray) -> StickerVerdict:
        """Re-register the fresh crop to the enrolment, then diff it."""
        return self._compare(name, fresh_crop, use_ecc=True)

    def compare_without_ecc(self, name: str, fresh_crop: np.ndarray) -> StickerVerdict:
        """The same comparison with the re-registration step removed.

        Exists to be measured, not to be shipped: it is the control arm that
        makes the cost of skipping findTransformECC an executable number rather
        than an assertion. Anything wiring this into a live verdict is a bug.
        """
        return self._compare(name, fresh_crop, use_ecc=False)

    def _compare(self, name: str, fresh_crop: np.ndarray,
                 *, use_ecc: bool) -> StickerVerdict:
        def abstain(reason: str, **kw: Any) -> StickerVerdict:
            return StickerVerdict(
                name=name, ignited_fraction=None, registered=kw.pop("registered", True),
                verdict=UNREGISTERABLE, ecc_ok=kw.pop("ecc_ok", False),
                reason=reason, **kw,
            )

        ref = self.reference(name)
        if ref is None:
            # The prior design bound an unenrolled sticker to the nearest slot
            # and captioned it CODE SUBSTITUTED. There is no registry of
            # legitimate UPI handles anywhere in this system, so no accusation
            # is expressible here -- only "I was never shown this".
            return abstain(R_NOT_ENROLLED, registered=False)

        try:
            fresh = _as_gray(fresh_crop)
        except StickerError:
            return abstain(R_CROP_UNREADABLE)
        if min(fresh.shape) < MIN_CROP_PX:
            return abstain(R_CROP_TOO_SMALL)

        ar_ref = ref.shape[1] / ref.shape[0]
        ar_new = fresh.shape[1] / fresh.shape[0]
        if abs(ar_new / ar_ref - 1.0) > MAX_ASPECT_DRIFT:
            return abstain(R_ASPECT_MISMATCH)
        if fresh.shape != ref.shape:
            interp = cv2.INTER_AREA if fresh.size > ref.size else cv2.INTER_LINEAR
            fresh = cv2.resize(fresh, (ref.shape[1], ref.shape[0]), interpolation=interp)

        if contrast_of(fresh) < MIN_ENROLMENT_CONTRAST:
            return abstain(R_CROP_FEATURELESS)

        ref_sharp = sharpness_of(ref)
        sharp_ratio = (sharpness_of(fresh) / ref_sharp) if ref_sharp > 1e-9 else 0.0

        cc: float | None = None
        ecc_ok = False
        if use_ecc:
            try:
                cc, warp = _ecc_align(ref, fresh)
            except cv2.error:
                # ECC signals non-convergence by throwing, not by a low score.
                return abstain(R_ECC_NO_CONVERGENCE, sharpness_ratio=sharp_ratio)
            ecc_ok = True
            if not np.isfinite(cc) or cc < MIN_ECC_CC:
                return abstain(R_ECC_LOW_CORRELATION, ecc_ok=True, ecc_cc=cc,
                               sharpness_ratio=sharp_ratio)
        else:
            warp = np.eye(2, 3, dtype=np.float32)

        shift = float(np.hypot(warp[0, 2], warp[1, 2]))
        rot = float(np.degrees(np.arctan2(warp[1, 0], warp[0, 0])))
        common: dict[str, Any] = dict(
            ecc_ok=ecc_ok, ecc_cc=cc, sharpness_ratio=sharp_ratio,
            ecc_shift_px=shift if use_ecc else None,
            ecc_rotation_deg=rot if use_ecc else None,
        )

        if sharp_ratio < MIN_SHARPNESS_RATIO:
            # Defocus alone reaches 10.5 % ignited on an unchanged sticker.
            # It is refused BEFORE the diff so that number can never be read
            # as evidence of substitution.
            return abstain(R_FOCUS_MISMATCH, **common)

        warped, support = _apply_warp(fresh, warp, ref.shape)
        res = diff_ignited(ref, warped, support)
        common["blind_fraction"] = res.blind_fraction
        common["valid_fraction"] = res.valid_fraction

        if res.valid_fraction < MIN_VALID_FRACTION:
            return abstain(R_INSUFFICIENT_OVERLAP, **common)
        if res.blind_fraction > MAX_BLIND_FRACTION:
            return abstain(R_OBSCURED, **common)

        return StickerVerdict(
            name=name,
            ignited_fraction=res.ignited_fraction,
            registered=True,
            verdict=TAMPERED if res.ignited_fraction >= TAMPER_GATE else GENUINE,
            reason=R_COMPARED,
            **common,
        )
