"""S6 IDENT acceptance: sticker tamper detection with no QR library at all.

The fixtures here are RANDOM BLACK/WHITE MODULE GRIDS drawn with numpy. They are
not QR codes, they contain no finder patterns, no format information, no error
correction and no payload, and nothing in this file or in the module under test
can turn one back into a string. That is deliberate: the feature this replaced
decoded and re-rendered UPI payloads, which is a forgery primitive, and the whole
point of the rescue is that comparing rectangles needs none of it.

The headline measurement is test_ACCEPTANCE_ecc_is_the_whole_feature, which runs
the same genuine, unchanged stickers through the pipeline twice -- once with
cv2.findTransformECC and once without -- and prints both ignited fractions.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import cv2
import numpy as np
import pytest

from gawaah.clock import VirtualClock
from gawaah.ident_sticker import (
    ABSTENTIONS, DIFF_THRESHOLD, GENUINE, MAX_BLIND_FRACTION, MIN_CROP_PX,
    MIN_ECC_CC, MIN_SHARPNESS_RATIO, MIN_VALID_FRACTION, OPEN_KERNEL,
    R_ASPECT_MISMATCH, R_CROP_FEATURELESS, R_CROP_TOO_SMALL, R_CROP_UNREADABLE,
    R_ECC_LOW_CORRELATION, R_ECC_NO_CONVERGENCE, R_FOCUS_MISMATCH,
    R_INSUFFICIENT_OVERLAP, R_NOT_ENROLLED, R_OBSCURED, TAMPER_GATE, TAMPERED,
    UNREGISTERABLE, StickerError, StickerRegistry, StickerVerdict,
    _apply_warp, _as_gray, diff_ignited, flatfield, sharpness_of,
)
from gawaah.takhti import BUF_H, BUF_W, PlaneEngine, mm_to_buffer, render_takhti

# ============================================================== the fixtures
# A "sticker" here is a square of random 1-bit modules with a white quiet zone,
# drawn with np.kron. 29 modules at 6 px is ~ the sampling a 58 mm counter
# sticker gets in the 2.83 px/mm rectified buffer.

N_MODULES = 29
PX_PER_MODULE = 6
QUIET_MODULES = 4


def synth_sticker(seed: int, n_modules: int = N_MODULES,
                  px_per_module: int = PX_PER_MODULE,
                  quiet: int = QUIET_MODULES) -> np.ndarray:
    """A random module-like grid. NOT a QR code -- no structure, no payload."""
    rng = np.random.default_rng(seed)
    grid = rng.integers(0, 2, size=(n_modules, n_modules), dtype=np.uint8) * 255
    body = np.kron(grid, np.ones((px_per_module, px_per_module), np.uint8))
    q = quiet * px_per_module
    out = np.full((body.shape[0] + 2 * q, body.shape[1] + 2 * q), 255, np.uint8)
    out[q:q + body.shape[0], q:q + body.shape[1]] = body
    return out


def _illumination(shape: tuple[int, int], rng: np.random.Generator,
                  amp: float) -> np.ndarray:
    """A smooth, non-uniform light field. The counter lamp is never flat."""
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    ax, ay = rng.uniform(-1, 1, 2)
    lin = (ax * (xx / w - 0.5) + ay * (yy / h - 0.5)) * 2.0
    cx, cy = rng.uniform(0.2, 0.8, 2)
    rad = np.exp(-(((xx / w - cx) ** 2 + (yy / h - cy) ** 2)) / 0.25)
    return 1.0 + amp * (0.6 * lin + 0.6 * (rad - 0.5))


def photograph(ideal: np.ndarray, dx: float = 0.0, dy: float = 0.0,
               angle: float = 0.0, blur: float = 1.0, noise: float = 3.0,
               gain: float = 1.0, bias: float = 0.0, illum: float = 0.30,
               seed: int = 0) -> np.ndarray:
    """Imaging model for one rectified crop: pose, optics, light, sensor."""
    rng = np.random.default_rng(seed)
    h, w = ideal.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    M[0, 2] += dx
    M[1, 2] += dy
    img = cv2.warpAffine(ideal, M, (w, h), flags=cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_REPLICATE)
    if blur > 0:
        img = cv2.GaussianBlur(img, (0, 0), blur)
    f = img.astype(np.float32)
    if illum:
        f = f * _illumination((h, w), rng, illum)
    f = f * gain + bias
    if noise:
        f = f + rng.normal(0, noise, f.shape)
    return np.clip(f, 0, 255).astype(np.uint8)


def curl(img: np.ndarray, amp: float, seed: int) -> np.ndarray:
    """Low-frequency non-rigid relief: vinyl on a counter is never flat, and a
    Euclidean ECC fit cannot remove it. This is what keeps the genuine ignited
    fraction honestly non-zero."""
    if amp <= 0:
        return img
    rng = np.random.default_rng(seed)
    h, w = img.shape
    d = cv2.resize(rng.normal(0, 1, (4, 4, 2)).astype(np.float32), (w, h),
                   interpolation=cv2.INTER_CUBIC)
    d = cv2.GaussianBlur(d, (0, 0), max(w, h) / 16.0)
    peak = float(np.abs(d).max()) or 1.0
    d *= amp / peak
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    return cv2.remap(img, xx + d[..., 0], yy + d[..., 1], cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REPLICATE)


def substitute_patch(ideal: np.ndarray, area_fraction: float,
                     seed: int) -> tuple[np.ndarray, float]:
    """Replace a square of the sticker with different modules. Returns the real
    replaced-area fraction alongside the image, so the test reports a measured
    number rather than the one it asked for."""
    rng = np.random.default_rng(seed)
    out = ideal.copy()
    h, w = ideal.shape
    side = int(round(np.sqrt(area_fraction * h * w)))
    y0 = (h - side) // 2 - 10
    x0 = (w - side) // 2 + 10
    n = side // PX_PER_MODULE + 1
    p = rng.integers(0, 2, size=(n, n), dtype=np.uint8) * 255
    out[y0:y0 + side, x0:x0 + side] = np.kron(
        p, np.ones((PX_PER_MODULE, PX_PER_MODULE), np.uint8))[:side, :side]
    return out, float(side * side) / float(h * w)


def relay(ideal: np.ndarray, k: int, rng: np.random.Generator,
          curl_px: float = 1.5) -> np.ndarray:
    """One genuine re-lay: same physical sticker, photographed again."""
    kw = dict(dx=rng.uniform(-3, 3), dy=rng.uniform(-3, 3),
              angle=rng.uniform(-1, 1), blur=rng.uniform(0.8, 1.5),
              gain=rng.uniform(0.80, 1.15), bias=rng.uniform(-20, 20),
              noise=rng.uniform(3, 7), seed=100 + k)
    return photograph(curl(ideal, curl_px, 500 + k), **kw)


ENROL_SEED = 1


@pytest.fixture
def reg(tmp_path: Path) -> StickerRegistry:
    return StickerRegistry(tmp_path / "stickers")


@pytest.fixture
def enrolled(reg: StickerRegistry):
    """One enrolled sticker plus the ideal it was drawn from."""
    ideal = synth_sticker(ENROL_SEED)
    reg.enrol("sticker:counter-1", photograph(ideal, seed=0))
    return reg, ideal, "sticker:counter-1"


# ================================================== THE SAFETY CONSTRAINT
# These run first because if they ever fail the module has grown the capability
# the rescue existed to delete.

MODULE_SRC = Path(__file__).resolve().parent.parent / "gawaah" / "ident_sticker.py"

FORBIDDEN_TOKENS = [
    "QRCodeDetector", "QRCodeEncoder", "wechat_qrcode", "qrcode", "pyzbar",
    "zxing", "zbar", "segno", "upi://", "reedsolomon", "reed_solomon",
    "galois", "bitstream", "alphanumeric_mode", "finder_pattern",
    "module_grid", "version_table", "format_info", "generate_payload",
    "encode_payload", "build_payload", "vpa=",
]


def _executable_surface(path: Path) -> tuple[set[str], set[str], set[str]]:
    """Every identifier, string literal and imported name in a module, with
    docstrings and comments removed.

    Prose is excluded on purpose: this module's own docstring has to be able to
    say the words "QR decoder" in order to state that there is not one. What
    must be absent is the CAPABILITY, which lives in code.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d is not None:
                docstrings.add(d)

    idents: set[str] = set()
    strings: set[str] = set()
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            idents.add(node.id)
        elif isinstance(node, ast.Attribute):
            idents.add(node.attr)
        elif isinstance(node, ast.arg):
            idents.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            idents.add(node.name)
        elif isinstance(node, ast.Import):
            imports.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module or "")
            imports.update(a.name for a in node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value not in docstrings:
                strings.add(node.value)
    return idents, strings, imports


def test_SAFETY_module_contains_no_forgery_primitive():
    """No encoder, no decoder, no module-grid reconstruction, no payload path.

    The prior design decoded a sticker's payload and re-rendered it. A payload
    constructor in a public repo is a forgery primitive regardless of intent,
    so its absence is asserted here, not assumed.
    """
    idents, strings, imports = _executable_surface(MODULE_SRC)
    surface = "\n".join(sorted(idents | strings | imports)).lower()
    hits = [t for t in FORBIDDEN_TOKENS if t.lower() in surface]
    assert hits == [], f"forgery primitive in ident_sticker.py code: {hits}"

    def words(ident: str) -> set[str]:
        """snake_case and camelCase split into whole words, so `sqrt` does not
        read as `qr`."""
        return {w.lower() for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+",
                                              ident)}

    for word in ("qr", "payload", "vpa", "upi", "zbar", "zxing"):
        bad = sorted(i for i in idents if word in words(i))
        assert bad == [], f"{word!r} appears in executable identifiers: {bad}"

    # Three allowances, each a byte move on data the module already holds:
    # cv2.imencode writes the enrolment PNG, str.encode is UTF-8 for the name
    # hash, json.JSONDecodeError guards the sidecar read. Nothing else in this
    # module may encode or decode anything -- in particular nothing may turn an
    # image into a string.
    allowed_codecs = {"imencode", "encode", "JSONDecodeError"}
    for verb in ("decode", "encode"):
        bad = sorted(i for i in idents
                     if verb in i.lower() and i not in allowed_codecs)
        assert bad == [], f"{verb} path present: {bad}"

    assert not any(re.search(r"qr|zbar|zxing|segno|solomon", m, re.I)
                   for m in imports), imports


def test_SAFETY_module_namespace_exposes_no_qr_capability():
    """Runtime check, so a re-export through another module is caught too."""
    import gawaah.ident_sticker as mod
    bad = [n for n in dir(mod) if "qr" in n.lower() or "payload" in n.lower()]
    assert bad == [], f"QR/payload capability exposed: {bad}"
    assert not any(
        isinstance(getattr(mod, n, None), type)
        and "QR" in type(getattr(mod, n)).__name__
        for n in dir(mod)
    )


def test_SAFETY_fixtures_are_random_grids_not_codes():
    """The fixtures must be structureless noise at module scale: no finder
    squares, no fixed corners, ~50 % fill. If someone later swaps in a real QR
    generator this fails."""
    a = synth_sticker(1)
    b = synth_sticker(2)
    q = QUIET_MODULES * PX_PER_MODULE
    body = a[q:-q, q:-q]
    mods = body[::PX_PER_MODULE, ::PX_PER_MODULE]
    assert mods.shape == (N_MODULES, N_MODULES)
    fill = float((mods > 127).mean())
    assert 0.40 < fill < 0.60, f"not a balanced random grid: fill={fill}"
    # two different seeds must disagree on about half the modules
    other = b[q:-q, q:-q][::PX_PER_MODULE, ::PX_PER_MODULE]
    disagree = float(((mods > 127) != (other > 127)).mean())
    assert 0.40 < disagree < 0.60, f"seeds are correlated: {disagree}"
    # a QR code's three 7x7 finder corners would be identical across seeds
    assert not np.array_equal(mods[:7, :7], other[:7, :7])


# ============================================================ enrolment


def test_enrol_roundtrips_the_crop_and_the_record(reg: StickerRegistry):
    crop = photograph(synth_sticker(1), seed=0)
    rec = reg.enrol("sticker:counter-1", crop)
    assert rec.name == "sticker:counter-1"
    assert rec.shape == crop.shape
    assert reg.is_enrolled("sticker:counter-1")
    back = reg.reference("sticker:counter-1")
    assert back is not None
    assert np.array_equal(back, crop), "PNG storage must be lossless"
    assert reg.record("sticker:counter-1").digest == rec.digest


def test_enrol_survives_a_new_registry_object(tmp_path: Path):
    d = tmp_path / "reg"
    crop = photograph(synth_sticker(1), seed=0)
    StickerRegistry(d).enrol("sticker:a", crop)
    fresh = StickerRegistry(d)
    assert fresh.names() == ["sticker:a"]
    assert np.array_equal(fresh.reference("sticker:a"), crop)


def test_enrol_records_the_clock_it_was_given(reg: StickerRegistry):
    clk = VirtualClock(start="2026-08-29T09:00:00.000+00:00", step_ms=100)
    rec = reg.enrol("sticker:a", photograph(synth_sticker(1), seed=0), clock=clk)
    assert rec.enrolled_ts == "2026-08-29T09:00:00.000+00:00"
    assert reg.record("sticker:a").enrolled_ts == rec.enrolled_ts


def test_enrol_refuses_a_crop_that_could_never_be_compared(reg: StickerRegistry):
    with pytest.raises(StickerError):
        reg.enrol("sticker:flat", np.full((200, 200), 128, np.uint8))
    with pytest.raises(StickerError):
        reg.enrol("sticker:tiny", synth_sticker(1)[:32, :32])
    with pytest.raises(StickerError):
        reg.enrol("", photograph(synth_sticker(1), seed=0))
    assert reg.names() == [], "a refused enrolment must leave nothing behind"


def test_names_are_hashed_so_they_cannot_escape_the_directory(reg: StickerRegistry):
    reg.enrol("../../etc/passwd", photograph(synth_sticker(1), seed=0))
    assert reg.names() == ["../../etc/passwd"]
    assert all(p.parent == reg.dir for p in reg.dir.iterdir())
    assert reg.reference("../../etc/passwd") is not None


def test_forget_removes_both_files(enrolled):
    reg, _, name = enrolled
    assert reg.forget(name) is True
    assert reg.names() == []
    assert reg.reference(name) is None
    assert reg.forget(name) is False


# ============================== THE REQUIRED ABSTENTION: NEVER ACCUSE A STRANGER


def test_ACCEPTANCE_unregistered_name_is_unregisterable_never_tampered(enrolled):
    """The prior design bound an unenrolled sticker to the nearest slot at 703 px
    error and captioned it CODE SUBSTITUTED. There is no registry of legitimate
    UPI handles in this system, so no accusation is expressible."""
    reg, ideal, _ = enrolled
    for probe in (photograph(ideal, seed=5),                 # a genuine sticker
                  photograph(synth_sticker(42), seed=5),     # a stranger
                  np.full((200, 200), 128, np.uint8)):       # nothing at all
        v = reg.compare("sticker:never-seen", probe)
        assert v.verdict == UNREGISTERABLE
        assert v.reason == R_NOT_ENROLLED
        assert v.registered is False
        assert v.ignited_fraction is None
        assert v.verdict != TAMPERED

    v = reg.compare_without_ecc("sticker:never-seen", photograph(ideal, seed=5))
    assert v.verdict == UNREGISTERABLE and v.reason == R_NOT_ENROLLED


# ==================================================== THE HEADLINE MEASUREMENT


def test_ACCEPTANCE_ecc_is_the_whole_feature(enrolled, capsys):
    """1 px and 3 px re-photographs, with and without findTransformECC.

    Both numbers are printed. The claim under test is that a naive absdiff is a
    false-accusation machine: at the feature's own 3 % gate, a genuine unchanged
    sticker photographed a few pixels off is called TAMPERED without the
    re-registration step, and GENUINE with it.
    """
    reg, ideal, name = enrolled
    rows = []
    for shift_px in (1, 3):
        for curl_px, label in ((0.0, "rigid"), (1.5, "with 1.5px relief")):
            fresh = photograph(curl(ideal, curl_px, 77), dx=shift_px, dy=0,
                               blur=1.1, noise=4.0, seed=11)
            with_ecc = reg.compare(name, fresh)
            without = reg.compare_without_ecc(name, fresh)
            assert with_ecc.ecc_ok is True
            assert without.ecc_ok is False
            rows.append((shift_px, label, without.ignited_fraction,
                         with_ecc.ignited_fraction, without.verdict,
                         with_ecc.verdict, with_ecc.ecc_cc,
                         with_ecc.ecc_shift_px))

    print("\n  IDENT / findTransformECC delta -- genuine, unchanged sticker")
    print(f"  {'shift':>6} {'condition':>18} {'no ECC':>9} {'with ECC':>9} "
          f"{'no ECC':>14} {'with ECC':>10} {'cc':>7} {'recovered':>10}")
    for s, lab, f0, f1, v0, v1, cc, rec in rows:
        print(f"  {s:>4}px {lab:>18} {f0 * 100:8.2f}% {f1 * 100:8.2f}% "
              f"{v0:>14} {v1:>10} {cc:7.4f} {rec:9.2f}px")

    for shift_px, label, f0, f1, v0, v1, cc, rec in rows:
        assert v1 == GENUINE, (
            f"{shift_px}px/{label}: ECC must clear an unchanged sticker, "
            f"got {v1} at {f1 * 100:.2f}%"
        )
        assert f1 < TAMPER_GATE
        assert f0 > f1, (
            f"{shift_px}px/{label}: ECC did not reduce the ignited fraction "
            f"({f0 * 100:.2f}% -> {f1 * 100:.2f}%)"
        )
        assert rec == pytest.approx(shift_px, abs=0.6), (
            f"ECC must recover the {shift_px}px offset, got {rec:.2f}px"
        )

    # the 3 px case is a false accusation without ECC under every condition
    for s, lab, f0, f1, v0, v1, cc, rec in rows:
        if s == 3:
            assert f0 > 0.15, f"3px naive diff should be huge, got {f0 * 100:.2f}%"
            assert v0 == TAMPERED, "naive diff must falsely accuse at 3px"
            assert f0 / max(f1, 1e-6) > 50, (
                f"3px: ECC must cut the diff by >50x, got {f0 / max(f1, 1e-6):.1f}x"
            )

    # the 1 px case with realistic relief is ALSO a false accusation without ECC
    one_px_real = [r for r in rows if r[0] == 1 and r[1] != "rigid"][0]
    assert one_px_real[2] > TAMPER_GATE, (
        f"1px naive diff {one_px_real[2] * 100:.2f}% should clear the 3% gate"
    )
    assert one_px_real[4] == TAMPERED
    assert one_px_real[5] == GENUINE


def test_ACCEPTANCE_genuine_relay_distribution(enrolled):
    """60 genuine re-lays of an unchanged sticker, ECC on and ECC off.

    This is the feature's own kill gate: p95 of the genuine ignited fraction
    must be under 3 %, or the feature can only falsely accuse a shopkeeper.
    """
    reg, ideal, name = enrolled
    rng = np.random.default_rng(4)
    with_ecc, without, verdicts0, verdicts1 = [], [], [], []
    for k in range(60):
        fresh = relay(ideal, k, rng)
        a = reg.compare(name, fresh)
        b = reg.compare_without_ecc(name, fresh)
        assert a.verdict != UNREGISTERABLE, f"abstained on a good crop: {a.reason}"
        assert b.verdict != UNREGISTERABLE, f"abstained on a good crop: {b.reason}"
        with_ecc.append(a.ignited_fraction)
        without.append(b.ignited_fraction)
        verdicts1.append(a.verdict)
        verdicts0.append(b.verdict)

    e = np.array(with_ecc) * 100
    n = np.array(without) * 100
    false_accuse_1 = verdicts1.count(TAMPERED) / len(verdicts1)
    false_accuse_0 = verdicts0.count(TAMPERED) / len(verdicts0)

    print("\n  IDENT / 60 genuine re-lays of an unchanged sticker")
    print(f"  {'':>10} {'mean':>8} {'p95':>8} {'max':>8} {'falsely accused':>17}")
    print(f"  {'no ECC':>10} {n.mean():7.2f}% {np.percentile(n, 95):7.2f}% "
          f"{n.max():7.2f}% {false_accuse_0 * 100:16.0f}%")
    print(f"  {'with ECC':>10} {e.mean():7.2f}% {np.percentile(e, 95):7.2f}% "
          f"{e.max():7.2f}% {false_accuse_1 * 100:16.0f}%")

    assert np.percentile(e, 95) < TAMPER_GATE * 100, (
        f"kill gate: genuine p95 {np.percentile(e, 95):.2f}% must be under 3%"
    )
    assert false_accuse_1 == 0.0, (
        f"with ECC the feature falsely accused {false_accuse_1 * 100:.0f}% of "
        f"genuine re-lays"
    )
    assert false_accuse_0 > 0.90, (
        f"without ECC only {false_accuse_0 * 100:.0f}% were falsely accused; the "
        f"naive-diff hazard has stopped being real and this doc claim needs "
        f"rewriting"
    )
    assert n.mean() > 10 * e.mean()


# =========================================================== SEPARABILITY


def test_ACCEPTANCE_ten_percent_patch_is_separable_from_genuine(enrolled):
    """A tenth of the sticker replaced with different modules, against the
    genuine distribution, under identical imaging conditions."""
    reg, ideal, name = enrolled
    gen, tam, areas = [], [], []
    tam_verdicts = []
    for k in range(30):
        rng = np.random.default_rng(4000 + k)
        g = reg.compare(name, relay(ideal, k, rng))
        assert g.verdict == GENUINE, f"{g.verdict}/{g.reason}"
        gen.append(g.ignited_fraction)

        sub, area = substitute_patch(ideal, 0.10, seed=200 + k)
        areas.append(area)
        rng = np.random.default_rng(4000 + k)
        t = reg.compare(name, relay(sub, k, rng))
        assert t.verdict != UNREGISTERABLE, t.reason
        tam.append(t.ignited_fraction)
        tam_verdicts.append(t.verdict)

    g = np.array(gen) * 100
    t = np.array(tam) * 100
    print(f"\n  IDENT / {np.mean(areas) * 100:.1f}% of the sticker replaced, n=30")
    print(f"  {'genuine':>10} mean {g.mean():6.3f}%  max {g.max():6.3f}%")
    print(f"  {'tampered':>10} mean {t.mean():6.3f}%  min {t.min():6.3f}%")
    print(f"  {'':>10} separation {t.min() / max(g.max(), 1e-9):.1f}x, "
          f"gate {TAMPER_GATE * 100:.0f}% sits between them")

    assert t.min() > g.max(), "the two distributions overlap"
    assert t.min() > 2 * TAMPER_GATE * 100, (
        f"tampered min {t.min():.2f}% has no margin over the {TAMPER_GATE * 100:.0f}% gate"
    )
    assert g.max() < TAMPER_GATE * 100 / 3
    assert all(v == TAMPERED for v in tam_verdicts)
    assert t.min() / max(g.max(), 1e-9) > 5.0


def test_smallest_patch_the_feature_can_honestly_claim(enrolled):
    """Report the recall curve instead of claiming a floor that was never
    measured. The 2 % patch is expected to be MISSED -- that is the honest
    limit, and it is asserted so nobody widens the claim later."""
    reg, ideal, name = enrolled
    rows = []
    for frac in (0.02, 0.05, 0.10, 0.20):
        caught, vals, area = 0, [], 0.0
        for k in range(12):
            sub, area = substitute_patch(ideal, frac, seed=900 + k)
            rng = np.random.default_rng(7000 + k)
            v = reg.compare(name, relay(sub, k, rng))
            vals.append(v.ignited_fraction)
            caught += int(v.verdict == TAMPERED)
        rows.append((area, caught / 12.0, float(np.mean(vals)) * 100))
    print("\n  IDENT / substituted-area recall")
    for area, rec, mean in rows:
        print(f"    replaced {area * 100:5.1f}%  ignited {mean:6.2f}%  "
              f"recall {rec * 100:5.0f}%")
    by_area = {round(a * 100): (rec, mean) for a, rec, mean in rows}
    assert by_area[10][0] == 1.0, "10% substitution must be caught every time"
    assert by_area[20][0] == 1.0
    assert by_area[2][0] < 0.5, (
        "if a 2% patch is now reliably caught the documented floor moved and "
        "the honest-limits section must be rewritten"
    )
    # monotone in replaced area
    means = [m for _, _, m in rows]
    assert means == sorted(means)


def test_whole_sticker_substitution_is_caught_or_abstained_never_cleared(enrolled):
    """A completely different sticker in the same slot. TAMPERED is the wanted
    answer; UNREGISTERABLE is acceptable (amber). GENUINE never is."""
    reg, ideal, name = enrolled
    outcomes = []
    for s in range(2, 22):
        fresh = photograph(synth_sticker(s), dx=1, dy=1, seed=5)
        v = reg.compare(name, fresh)
        outcomes.append(v)
        assert v.verdict != GENUINE, (
            f"seed {s}: cleared a substituted sticker at "
            f"{(v.ignited_fraction or 0) * 100:.2f}%"
        )
    caught = sum(v.verdict == TAMPERED for v in outcomes)
    ign = [v.ignited_fraction for v in outcomes if v.ignited_fraction is not None]
    ccs = [v.ecc_cc for v in outcomes if v.ecc_cc is not None]
    print(f"\n  IDENT / whole-sticker substitution, n={len(outcomes)}")
    print(f"    TAMPERED {caught}, abstained {len(outcomes) - caught}")
    print(f"    ignited mean {np.mean(ign) * 100:.2f}%  ECC cc "
          f"{min(ccs):.3f}..{max(ccs):.3f}")
    assert caught >= 0.8 * len(outcomes), f"only {caught}/{len(outcomes)} caught"


# ========================================================= THE ABSTENTIONS
# Every gate below exists because it was measured to cause a FALSE ACCUSATION
# when it was absent. The numbers in each docstring came from this rig.


def test_defocus_abstains_instead_of_accusing(enrolled):
    """Measured without the focus gate: a 3 px-sigma defocus of an UNCHANGED
    sticker reaches 10.46 % ignited -- three times the gate, on no tampering
    at all."""
    reg, ideal, name = enrolled
    rows = []
    for blur in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
        v = reg.compare(name, photograph(ideal, blur=blur, seed=5))
        rows.append((blur, v.verdict, v.reason, v.sharpness_ratio))
        assert v.verdict != TAMPERED, (
            f"blur sigma {blur} produced a false accusation at "
            f"{(v.ignited_fraction or 0) * 100:.2f}%"
        )
    print("\n  IDENT / defocus")
    for b, verdict, reason, sr in rows:
        print(f"    sigma {b:>4}  sharpness_ratio {sr:5.3f}  {verdict}/{reason}")
    assert rows[0][1] == GENUINE, "the enrolment's own focus must pass"
    assert any(r[1] == UNREGISTERABLE and r[2] == R_FOCUS_MISMATCH for r in rows)
    assert rows[-1][2] == R_FOCUS_MISMATCH
    # the gate must be crossed monotonically, not at random
    ratios = [r[3] for r in rows]
    assert ratios[0] > ratios[-1]


def test_glare_and_occlusion_abstain_instead_of_accusing(enrolled):
    """Structure DESTROYED is not structure CHANGED. Without the blind-region
    rule a 70 px specular blob reported 26 % ignited on an unchanged sticker."""
    reg, ideal, name = enrolled
    base = reg.reference(name)
    h, w = base.shape
    print("\n  IDENT / glare and occlusion")
    seen_obscured = False
    for value, label in ((255, "glare"), (0, "occluder")):
        for radius in (40, 70, 100):
            img = base.copy()
            cv2.circle(img, (w // 2, h // 2), radius, value, -1)
            v = reg.compare(name, img)
            area = np.pi * radius * radius / (h * w)
            blind = ("  n/a" if v.blind_fraction is None
                     else f"{v.blind_fraction * 100:4.1f}%")
            print(f"    {label:>9} r={radius:<4} covers {area * 100:4.1f}%  "
                  f"blind {blind}  {v.verdict}/{v.reason}")
            assert v.verdict != TAMPERED, (
                f"{label} r={radius} produced a false accusation"
            )
            if v.reason == R_OBSCURED:
                seen_obscured = True
                assert v.blind_fraction > MAX_BLIND_FRACTION
    assert seen_obscured, "the OBSCURED gate never fired"


def test_defocus_number_that_justifies_the_focus_gate(enrolled):
    """The gate's own evidence, measured by lifting the gate.

    Every abstention costs the shopkeeper a re-shoot, so a gate has to earn its
    place with the false accusation it prevents. This runs the pipeline with
    MIN_SHARPNESS_RATIO removed and reports what an UNCHANGED sticker scores
    when it is merely out of focus.
    """
    import gawaah.ident_sticker as mod
    reg, ideal, name = enrolled
    original = mod.MIN_SHARPNESS_RATIO
    rows = []
    try:
        mod.MIN_SHARPNESS_RATIO = 0.0
        for blur in (1.5, 2.0, 2.5, 3.0, 4.0):
            v = reg.compare(name, photograph(ideal, blur=blur, seed=5))
            rows.append((blur, v.sharpness_ratio, v.ignited_fraction, v.verdict))
    finally:
        mod.MIN_SHARPNESS_RATIO = original

    print("\n  IDENT / what the focus gate prevents (gate lifted, sticker UNCHANGED)")
    for blur, sr, ign, verdict in rows:
        ig = "n/a" if ign is None else f"{ign * 100:6.2f}%"
        print(f"    defocus sigma {blur:>4}  sharpness_ratio {sr:5.3f}  "
              f"ignited {ig}  {verdict}")

    accused = [(b, i) for b, _, i, v in rows if v == TAMPERED]
    assert accused, (
        "no amount of defocus produced a false accusation; MIN_SHARPNESS_RATIO "
        "is now dead weight and should be deleted rather than documented"
    )
    worst = max(i for _, i in accused)
    assert worst > 3 * TAMPER_GATE, (
        f"worst defocus false accusation is only {worst * 100:.2f}%"
    )
    # and the shipped gate must actually refuse every one of those
    for blur, _ in accused:
        v = reg.compare(name, photograph(ideal, blur=blur, seed=5))
        assert v.reason == R_FOCUS_MISMATCH, f"sigma {blur} slipped through"


def test_glare_does_not_hide_a_real_substitution(enrolled, monkeypatch):
    """The blind-region rule must not become an attacker's cloak.

    A white substituted module and a specular highlight are the same brightness,
    so a blind rule keyed on BRIGHTNESS writes off half a real substitution. The
    shipped rule keys on LOST STRUCTURE instead. Both are run here and the gap
    between them is measured, so the design choice is a number and not a story.
    """
    import gawaah.ident_sticker as mod
    reg, ideal, name = enrolled
    sub, area = substitute_patch(ideal, 0.10, seed=201)
    fresh = relay(sub, 3, np.random.default_rng(8123))

    shipped = reg.compare(name, fresh)
    assert shipped.verdict == TAMPERED, f"{shipped.verdict}/{shipped.reason}"
    assert shipped.blind_fraction < 0.05, (
        f"a substituted patch was written off as blind: "
        f"{shipped.blind_fraction * 100:.1f}%"
    )

    def brightness_keyed(ref_ff: np.ndarray, fresh_ff: np.ndarray) -> np.ndarray:
        """The rejected rule: 'blown out in the fresh crop where the enrolment
        was dark, or crushed where it was light'. Thresholds are on the
        flat-fielded scale, where a module runs about 32..224."""
        lost = (((fresh_ff > 200) & (ref_ff < 100))
                | ((fresh_ff < 56) & (ref_ff > 156)))
        return cv2.morphologyEx(lost.astype(np.uint8) * 255, cv2.MORPH_CLOSE,
                                np.ones((5, 5), np.uint8))

    monkeypatch.setattr(mod, "_blind_mask", brightness_keyed)
    rejected = reg.compare(name, fresh)

    print(f"\n  IDENT / blind rule, on a real {area * 100:.1f}% substitution")
    print(f"    keyed on lost structure (shipped)  ignited "
          f"{shipped.ignited_fraction * 100:5.2f}%  {shipped.verdict}")
    print(f"    keyed on brightness    (rejected)  ignited "
          f"{rejected.ignited_fraction * 100:5.2f}%  {rejected.verdict}")
    assert rejected.ignited_fraction < shipped.ignited_fraction, (
        "the brightness-keyed rule no longer swallows substitution evidence; "
        "the rationale for the structure-keyed rule needs re-measuring"
    )
    assert shipped.ignited_fraction / rejected.ignited_fraction > 1.5


def test_gross_misregistration_abstains_on_the_ecc_correlation(enrolled):
    """When ECC lands on a wrong optimum the diff is meaningless. Measured:
    gross misregistration converges at cc 0.06-0.21, a genuinely substituted
    sticker at 0.35-0.40, a genuine re-lay at 0.88+. The floor sits between."""
    reg, ideal, name = enrolled
    ccs = []
    for d in (30, 40, 50, 60):
        for seed in (5, 6, 7):
            v = reg.compare(name, photograph(ideal, dx=d, dy=d, seed=seed))
            assert v.verdict != TAMPERED, (
                f"a {d}px crop-box error was called TAMPERED at "
                f"{(v.ignited_fraction or 0) * 100:.1f}%"
            )
            assert v.reason in (R_ECC_LOW_CORRELATION, R_ECC_NO_CONVERGENCE,
                                R_INSUFFICIENT_OVERLAP)
            if v.ecc_cc is not None:
                ccs.append(v.ecc_cc)
    print(f"\n  IDENT / gross misregistration: cc {min(ccs):.3f}..{max(ccs):.3f} "
          f"(floor {MIN_ECC_CC})")
    assert max(ccs) < MIN_ECC_CC


def test_the_ecc_correlation_floor_is_bracketed_by_measurement(enrolled):
    """MIN_ECC_CC has to separate two populations that both look 'wrong'.

    Below it: ECC landed on a false optimum and the diff means nothing, so the
    only honest answer is amber. Above it: the fit is real and the crops truly
    differ, which is the accusation the feature exists to make. The margin on
    the accusation side is thin and is reported rather than smoothed over --
    when a substitution's correlation dips under the floor the module abstains,
    which is a miss, not a false accusation.
    """
    reg, ideal, name = enrolled
    lost = [reg.compare(name, photograph(ideal, dx=d, dy=d, seed=s)).ecc_cc
            for d in (30, 40, 50, 60) for s in (5, 6, 7)]
    lost = [c for c in lost if c is not None]
    swapped = [reg.compare(name, photograph(synth_sticker(s), dx=1, dy=1, seed=5)).ecc_cc
               for s in range(2, 22)]
    swapped = [c for c in swapped if c is not None]
    rng = np.random.default_rng(4)
    good = [reg.compare(name, relay(ideal, k, rng)).ecc_cc for k in range(20)]

    print(f"\n  IDENT / ECC correlation populations (floor {MIN_ECC_CC})")
    print(f"    false optimum   {min(lost):.3f} .. {max(lost):.3f}   -> abstain")
    print(f"    real swap       {min(swapped):.3f} .. {max(swapped):.3f}   -> accuse")
    print(f"    genuine re-lay  {min(good):.3f} .. {max(good):.3f}   -> clear")
    print(f"    headroom below the floor {MIN_ECC_CC - max(lost):+.3f}, "
          f"above it {min(swapped) - MIN_ECC_CC:+.3f}")
    assert max(lost) < MIN_ECC_CC < min(swapped) < min(good)
    assert min(swapped) - MIN_ECC_CC < 0.10, (
        "the accusation-side margin is documented as thin; if it has widened, "
        "say so with the new number instead of leaving the old caveat standing"
    )


def test_featureless_and_unalignable_crops_abstain(enrolled):
    reg, ideal, name = enrolled
    flat = reg.compare(name, np.full(reg.reference(name).shape, 128, np.uint8))
    assert flat.verdict == UNREGISTERABLE
    assert flat.reason == R_CROP_FEATURELESS

    rng = np.random.default_rng(0)
    noise = np.clip(rng.normal(128, 40, reg.reference(name).shape),
                    0, 255).astype(np.uint8)
    v = reg.compare(name, noise)
    assert v.verdict == UNREGISTERABLE, f"{v.verdict} on pure noise"
    assert v.reason in (R_ECC_NO_CONVERGENCE, R_ECC_LOW_CORRELATION,
                        R_FOCUS_MISMATCH)

    tiny = reg.compare(name, np.zeros((MIN_CROP_PX - 1, MIN_CROP_PX - 1), np.uint8))
    assert tiny.reason == R_CROP_TOO_SMALL

    for junk in ("not an image", None, np.zeros((80, 80, 2), np.uint8)):
        j = reg.compare(name, junk)
        assert j.verdict == UNREGISTERABLE and j.reason == R_CROP_UNREADABLE

    ref = reg.reference(name)
    squashed = cv2.resize(ref, (ref.shape[1], ref.shape[0] // 2))
    assert reg.compare(name, squashed).reason == R_ASPECT_MISMATCH


def test_insufficient_overlap_abstains():
    """Exercised directly: a warp that slides the crop mostly off the enrolment
    must reduce the valid fraction below the floor rather than diff a sliver."""
    ref = photograph(synth_sticker(1), seed=0)
    fresh = photograph(synth_sticker(1), seed=1)
    warp = np.eye(2, 3, dtype=np.float32)
    warp[0, 2] = ref.shape[1] * 0.75
    warped, support = _apply_warp(fresh, warp, ref.shape)
    res = diff_ignited(ref, warped, support)
    assert res.valid_fraction < MIN_VALID_FRACTION, res.valid_fraction

    warp[0, 2] = 2.0
    warped, support = _apply_warp(fresh, warp, ref.shape)
    assert diff_ignited(ref, warped, support).valid_fraction > MIN_VALID_FRACTION


def test_every_abstention_reason_is_a_named_code(enrolled):
    reg, ideal, name = enrolled
    probes = [
        reg.compare("sticker:nope", photograph(ideal, seed=1)),
        reg.compare(name, np.full(reg.reference(name).shape, 128, np.uint8)),
        reg.compare(name, np.zeros((10, 10), np.uint8)),
        reg.compare(name, photograph(ideal, blur=4.0, seed=1)),
    ]
    for v in probes:
        assert v.verdict == UNREGISTERABLE
        assert v.reason in ABSTENTIONS, f"unnamed abstention {v.reason!r}"
        assert v.ignited_fraction is None, "an abstention must publish no scalar"


# ============================================================ housekeeping


def test_verdict_is_only_ever_one_of_three_words(enrolled):
    reg, ideal, name = enrolled
    seen = set()
    sub, _ = substitute_patch(ideal, 0.20, seed=3)
    for probe, who in ((photograph(ideal, seed=9), name),
                       (photograph(sub, seed=9), name),
                       (photograph(ideal, seed=9), "sticker:absent")):
        v = reg.compare(who, probe)
        seen.add(v.verdict)
        assert isinstance(v, StickerVerdict)
    assert seen == {GENUINE, TAMPERED, UNREGISTERABLE}
    assert seen <= {GENUINE, TAMPERED, UNREGISTERABLE}


def test_evidence_is_json_serialisable_for_the_ledger(enrolled):
    reg, ideal, name = enrolled
    for v in (reg.compare(name, photograph(ideal, seed=2)),
              reg.compare("sticker:absent", photograph(ideal, seed=2))):
        blob = json.dumps(v.evidence(), sort_keys=True)
        back = json.loads(blob)
        assert back["verdict"] == v.verdict
        assert back["reason"] == v.reason
        assert set(back) >= {"sticker", "verdict", "reason", "ignited_fraction",
                             "ecc_ok", "registered"}


def test_compare_is_deterministic(enrolled):
    reg, ideal, name = enrolled
    fresh = relay(ideal, 3, np.random.default_rng(31))
    a = reg.compare(name, fresh)
    b = reg.compare(name, fresh)
    assert a == b, "the same pair must give byte-identical evidence"


def test_a_resized_crop_is_still_genuine(enrolled):
    """The operator's crop box will not be the same pixel size twice."""
    reg, ideal, name = enrolled
    fresh = photograph(ideal, dx=1, dy=-1, seed=13)
    for scale in (0.85, 1.0, 1.3):
        h, w = fresh.shape
        r = cv2.resize(fresh, (int(w * scale), int(h * scale)))
        v = reg.compare(name, r)
        assert v.verdict == GENUINE, f"scale {scale}: {v.verdict}/{v.reason}"


def test_flatfield_is_invariant_to_the_counter_lamp():
    """The normalisation, on its own: same sticker, wildly different light."""
    ideal = synth_sticker(1)
    a = flatfield(photograph(ideal, gain=1.0, bias=0, illum=0.0, noise=0, seed=0))
    b = flatfield(photograph(ideal, gain=0.5, bias=60, illum=0.45, noise=0, seed=0))
    assert float(cv2.absdiff(a, b).mean()) < 8.0
    assert sharpness_of(a) == pytest.approx(sharpness_of(b), rel=0.25)


def test_as_gray_accepts_colour_and_rejects_nonsense():
    ideal = synth_sticker(1)
    bgr = cv2.cvtColor(ideal, cv2.COLOR_GRAY2BGR)
    assert np.array_equal(_as_gray(bgr), ideal)
    assert _as_gray(cv2.cvtColor(ideal, cv2.COLOR_GRAY2BGRA)).shape == ideal.shape
    with pytest.raises(StickerError):
        _as_gray("not an image")
    with pytest.raises(StickerError):
        _as_gray(np.zeros((4, 4, 2), np.uint8))


# ============================== the calibration that chose the constants


def test_calibration_table_that_chose_every_constant(enrolled):
    """Re-derive the DIFF_THRESHOLD curve and prove the shipped value is the
    right pick, so the constant cannot drift away from its evidence.

    The pressure is two-sided. Too high and MORPH_OPEN alone erases a hairline
    registration ridge, the 1 px naive-diff hazard reports 0.00 %, and the
    reason findTransformECC is in the build becomes invisible. Too low and
    genuine relief starts igniting.
    """
    import gawaah.ident_sticker as mod
    reg, ideal, name = enrolled
    fresh1 = photograph(curl(ideal, 1.5, 77), dx=1, dy=0, blur=1.1, noise=4.0, seed=11)
    sub, _ = substitute_patch(ideal, 0.10, seed=201)
    tampered = relay(sub, 3, np.random.default_rng(8123))

    rows = []
    original = mod.DIFF_THRESHOLD
    try:
        for th in (24, 32, 40, 48, 56, 64):
            mod.DIFF_THRESHOLD = th
            naive = reg.compare_without_ecc(name, fresh1).ignited_fraction
            clean = reg.compare(name, fresh1).ignited_fraction
            rng = np.random.default_rng(4)
            gen = [reg.compare(name, relay(ideal, k, rng)).ignited_fraction
                   for k in range(20)]
            tam = reg.compare(name, tampered).ignited_fraction
            rows.append((th, naive * 100, clean * 100, max(gen) * 100, tam * 100))
    finally:
        mod.DIFF_THRESHOLD = original

    print("\n  IDENT / DIFF_THRESHOLD calibration")
    print(f"  {'thresh':>7} {'1px naive':>10} {'1px ECC':>9} {'genuine max':>12} "
          f"{'tampered':>9} {'margin':>8}")
    for th, naive, clean, gmax, tam in rows:
        margin = f"{tam / gmax:6.1f}x" if gmax > 1e-6 else "    inf"
        print(f"  {th:>7} {naive:9.2f}% {clean:8.2f}% {gmax:11.2f}% {tam:8.2f}% "
              f"{margin:>8}")

    chosen = [r for r in rows if r[0] == DIFF_THRESHOLD]
    assert chosen, f"DIFF_THRESHOLD {DIFF_THRESHOLD} is off the calibrated curve"
    th, naive, clean, gmax, tam = chosen[0]
    assert naive > TAMPER_GATE * 100, (
        f"at the shipped threshold the 1px naive diff is only {naive:.2f}% -- the "
        f"finding this feature exists to demonstrate would be invisible"
    )
    assert gmax < TAMPER_GATE * 100, f"genuine max {gmax:.2f}% crosses the gate"
    assert tam > 2 * TAMPER_GATE * 100
    # the naive 1px signal must vanish at high thresholds -- that is the trap
    assert rows[-1][1] < TAMPER_GATE * 100, (
        "at threshold 64 the 1px hazard should be masked by MORPH_OPEN; if it "
        "is not, this rationale has changed"
    )
    assert OPEN_KERNEL == 3 and MIN_SHARPNESS_RATIO < 1.0


def test_honest_limit_non_rigid_relief_breaks_the_feature(enrolled):
    """Named, measured failure mode: a Euclidean fit cannot remove sticker curl,
    so past ~3 px of relief the genuine distribution collides with the tampered
    one. Asserted so the envelope is a fact and not a footnote."""
    reg, ideal, name = enrolled
    rows = []
    for amp in (0.0, 1.5, 3.0, 4.5):
        rng = np.random.default_rng(4)
        vals = [reg.compare(name, relay(ideal, k, rng, curl_px=amp)).ignited_fraction
                for k in range(20)]
        vals = [v for v in vals if v is not None]
        rows.append((amp, float(np.mean(vals)) * 100, float(np.max(vals)) * 100))
    print("\n  IDENT / honest limit: non-rigid relief")
    for amp, mean, mx in rows:
        print(f"    curl {amp:>4}px  genuine ignited mean {mean:6.2f}% max {mx:6.2f}%")
    assert rows[1][2] < TAMPER_GATE * 100, "1.5px relief must stay under the gate"
    assert rows[-1][2] > TAMPER_GATE * 100, (
        "if 4.5px of relief no longer crosses the gate the documented envelope "
        "has widened and must be re-measured, not assumed"
    )
    means = [m for _, m, _ in rows]
    assert means == sorted(means)


# =================================================== END TO END ON THE MAT


STICKER_MM = (60.0, 200.0, 70.0)  # x, y, side -- clear of markers and the patch


def _mat_with_sticker(sticker: np.ndarray, px_per_mm: float = 4.0) -> np.ndarray:
    mat = render_takhti(px_per_mm)
    x, y, side = STICKER_MM
    s = int(round(side * px_per_mm))
    tile = cv2.resize(sticker, (s, s), interpolation=cv2.INTER_NEAREST)
    x0, y0 = int(round(x * px_per_mm)), int(round(y * px_per_mm))
    out = mat.copy()
    out[y0:y0 + s, x0:x0 + s] = tile
    return out


def _project(mat: np.ndarray, tilt: tuple[float, float],
             size: tuple[int, int] = (960, 1280), fit: float = 0.82,
             noise: float = 2.0, seed: int = 0) -> np.ndarray:
    """Mirrors tests/test_plane.py::synth_frame, but for a mat we composed."""
    h, w = mat.shape
    W, H = size
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)
    ax, ay = np.radians(tilt[0]), np.radians(tilt[1])
    hw, hh = w / 2, h / 2
    pts = np.array([[-hw, -hh, 0], [hw, -hh, 0], [hw, hh, 0], [-hw, hh, 0]], np.float64)
    Rx = np.array([[1, 0, 0], [0, np.cos(ax), -np.sin(ax)], [0, np.sin(ax), np.cos(ax)]])
    Ry = np.array([[np.cos(ay), 0, np.sin(ay)], [0, 1, 0], [-np.sin(ay), 0, np.cos(ay)]])
    pts = pts @ Rx.T @ Ry.T
    f = max(w, h) * 2.2
    dist = f * max(w / (fit * W), h / (fit * H))
    dst = np.array([[f * X / (dist + Z) + W / 2, f * Y / (dist + Z) + H / 2]
                    for X, Y, Z in pts], np.float32)
    M = cv2.getPerspectiveTransform(src, dst)
    frame = np.full((H, W), 235, np.uint8)
    warped = cv2.warpPerspective(mat, M, (W, H), borderValue=235)
    mask = cv2.warpPerspective(np.full_like(mat, 255), M, (W, H), borderValue=0)
    frame[mask > 128] = warped[mask > 128]
    rng = np.random.default_rng(seed)
    return np.clip(frame.astype(np.int16) + rng.normal(0, noise, frame.shape),
                   0, 255).astype(np.uint8)


def _sticker_crop(sticker: np.ndarray, tilt: tuple[float, float], seed: int,
                  box_jitter: tuple[int, int] = (0, 0)) -> np.ndarray:
    """Full path: compose the mat, photograph it, lock the plane, rectify, crop.

    `box_jitter` displaces the crop box in buffer pixels. In the product the box
    comes from locating the sticker rectangle in the buffer, not from a constant,
    so a few pixels of box error is the realistic case -- and it is the error
    findTransformECC actually has to absorb.
    """
    frame = _project(_mat_with_sticker(sticker), tilt=tilt, seed=seed)
    eng = PlaneEngine()
    lock = eng.detect(frame)
    assert lock.locked, lock.reason
    buf = eng.rectify(frame, lock.H)
    assert buf.shape[:2] == (BUF_H, BUF_W)
    x, y, side = STICKER_MM
    box = mm_to_buffer(np.array([[x, y], [x + side, y + side]], np.float64))
    jx, jy = box_jitter
    x0, y0 = int(round(box[0, 0])) + jx, int(round(box[0, 1])) + jy
    x1, y1 = int(round(box[1, 0])) + jx, int(round(box[1, 1])) + jy
    return buf[y0:y1, x0:x1].copy()


def test_END_TO_END_through_the_real_plane_engine(reg: StickerRegistry):
    """Enrol from one camera pose, check from another, both through the actual
    TAKHTI plane engine -- ArUco lock, homography, rectified metric buffer,
    fixed mm crop box. No pixel shifts are injected by hand; the misregistration
    is whatever two real rectifications disagree by."""
    ideal = synth_sticker(7)
    enrol_crop = _sticker_crop(ideal, tilt=(0.0, 0.0), seed=1)
    assert min(enrol_crop.shape) >= MIN_CROP_PX, enrol_crop.shape
    rec = reg.enrol("sticker:on-the-mat", enrol_crop)
    print(f"\n  IDENT / end-to-end: crop {rec.shape[1]}x{rec.shape[0]} px "
          f"for a {STICKER_MM[2]:.0f}mm sticker "
          f"({rec.shape[1] / (N_MODULES + 2 * QUIET_MODULES):.2f} px/module)")

    genuine, residuals = [], []
    for tilt, seed in (((2.0, 1.0), 2), ((-3.0, 2.0), 3), ((0.0, -4.0), 4),
                       ((4.0, 4.0), 5)):
        v = reg.compare("sticker:on-the-mat", _sticker_crop(ideal, tilt, seed))
        print(f"    genuine  tilt={tilt}  {v.verdict}/{v.reason}  "
              f"ignited {v.ignited_fraction * 100:5.2f}%  cc {v.ecc_cc:.4f}  "
              f"ECC recovered {v.ecc_shift_px:.2f}px")
        assert v.verdict == GENUINE, f"tilt {tilt}: {v.verdict}/{v.reason}"
        genuine.append(v.ignited_fraction)
        residuals.append(v.ecc_shift_px)

    # THE PLANE ENGINE IS NOT THE SOURCE OF MISREGISTRATION. Across four tilts
    # the residual the ECC has to remove is sub-pixel, so the homography leaves
    # it nothing to do. The error ECC exists to absorb comes from the CROP BOX,
    # which in the product is found by locating the sticker rectangle, not by a
    # constant -- see test_END_TO_END_crop_box_jitter_is_what_ecc_absorbs.
    assert max(genuine) < TAMPER_GATE
    assert max(residuals) < 1.0, (
        f"rectification residual grew to {max(residuals):.2f}px; the claim that "
        f"crop-box error dominates would need re-checking"
    )

    sub, area = substitute_patch(ideal, 0.10, seed=5)
    tam = []
    for tilt, seed in (((2.0, 1.0), 2), ((-3.0, 2.0), 3)):
        v = reg.compare("sticker:on-the-mat", _sticker_crop(sub, tilt, seed))
        print(f"    tampered tilt={tilt}  {v.verdict}/{v.reason}  "
              f"ignited {v.ignited_fraction * 100:5.2f}%")
        assert v.verdict == TAMPERED, f"tilt {tilt}: {v.verdict}/{v.reason}"
        tam.append(v.ignited_fraction)

    print(f"    genuine max {max(genuine) * 100:.2f}%  vs  tampered min "
          f"{min(tam) * 100:.2f}%  ({area * 100:.1f}% replaced)")
    assert min(tam) > max(genuine)


def test_END_TO_END_crop_box_jitter_is_what_ecc_absorbs(reg: StickerRegistry):
    """Same real rectified crops, with the crop box displaced by a few buffer
    pixels -- the error a sticker-rectangle locator really makes.

    This is the naive-diff hazard on the full pipeline rather than on injected
    shifts: an unchanged sticker on a correctly locked mat, accused because the
    box moved.
    """
    ideal = synth_sticker(7)
    reg.enrol("sticker:on-the-mat", _sticker_crop(ideal, (0.0, 0.0), 1))
    print("\n  IDENT / end-to-end, crop-box jitter on a genuine sticker")
    print(f"  {'jitter':>10} {'no ECC':>9} {'with ECC':>9} {'no ECC':>12} "
          f"{'with ECC':>10} {'recovered':>10}")
    worst_naive = 0.0
    for jitter in ((0, 0), (1, 0), (2, 2), (3, -3), (5, 4)):
        crop = _sticker_crop(ideal, (2.0, 1.0), 2, box_jitter=jitter)
        a = reg.compare("sticker:on-the-mat", crop)
        b = reg.compare_without_ecc("sticker:on-the-mat", crop)
        print(f"  {str(jitter):>10} {b.ignited_fraction * 100:8.2f}% "
              f"{a.ignited_fraction * 100:8.2f}% {b.verdict:>12} "
              f"{a.verdict:>10} {a.ecc_shift_px:9.2f}px")
        assert a.verdict == GENUINE, (
            f"jitter {jitter}: ECC must absorb a {jitter} px box error, got "
            f"{a.verdict}/{a.reason} at {a.ignited_fraction * 100:.2f}%"
        )
        assert a.ecc_shift_px == pytest.approx(float(np.hypot(*jitter)), abs=0.6)
        worst_naive = max(worst_naive, b.ignited_fraction)
    assert worst_naive > TAMPER_GATE, (
        f"crop-box jitter should falsely accuse without ECC; worst was only "
        f"{worst_naive * 100:.2f}%"
    )


def test_END_TO_END_unenrolled_sticker_on_the_mat_is_grey(reg: StickerRegistry):
    """The 2:20 beat of the demo: an unenrolled sticker enters the plane. Grey
    card, no red."""
    reg.enrol("sticker:slot-a", _sticker_crop(synth_sticker(7), (0.0, 0.0), 1))
    stranger = _sticker_crop(synth_sticker(88), (1.0, 1.0), 6)
    v = reg.compare("sticker:slot-b", stranger)
    assert v.verdict == UNREGISTERABLE and v.reason == R_NOT_ENROLLED
    assert v.registered is False
