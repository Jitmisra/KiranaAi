"""NAZAR acceptance: the descriptor is deterministic, robust and separating —
or it says so.

Every number this file asserts was MEASURED first and the floor set below the
measurement, never the other way round. Where the descriptor cannot separate
something, there is a test that says so out loud (see the "honest limits"
section at the bottom) rather than a threshold quietly widened until green.

The product set is synthetic on purpose. Photographs of real packets would be
a fixture nobody can regenerate, would drift with whatever camera took them,
and would let a failure hide behind "the lighting was bad that day". These are
flat colour fields, printed bands, stripes, dots and checks — the vocabulary an
actual kirana wrapper is built from — rendered deterministically, then put
through the perturbations the mat really applies: the packet turned, the crop
loose or tight, the shop light up or down, sensor noise.

TWO SETS, AND WHY
-----------------
TUNE products and TRAIN views are what gawaah/embedder.py's WEIGHTS were fitted
on. HOLD products and VAL views are a different generator and a different
perturbation ladder. Reporting only the first would be reporting a fit, not a
measurement, so the acceptance thresholds here are carried by BOTH.

WHAT "WORKS" MEANS HERE
-----------------------
Not "high cosine". The only decision that matters is the one identity.py makes,
so the headline test enrols a real Gallery, queries it through a real
Identifier at the shipped theta/phi, and counts WRONG matches. Invariant 7 says
a wrong price is worse than an amber line, so the bar on wrong matches is ZERO
and the bar on abstentions is merely "not most of them".
"""
from __future__ import annotations

import subprocess
import sys
import time
import zlib
from pathlib import Path

import cv2
import numpy as np
import pytest

from gawaah.embedder import (
    BLOCK_SPEC,
    COLOUR_BLOCKS,
    DETAIL_BLOCKS,
    EMBED_DIM,
    WEIGHTS,
    WEIGHT_FLOOR,
    EmbedderError,
    blocks,
    embed,
    embed_batch,
)
from gawaah.identity import Gallery, Identifier, cosine

REPO = Path(__file__).resolve().parent.parent

THETA = 0.10       # the shipped identity.py thresholds, not softened ones
PHI = 0.55
LONG_MM = 49.5     # every product below shares a long edge, so the footprint
                   # gate never helps: appearance must carry the whole load


# ===================================================================== pixels

# Palette kept at or below 210 so a x1.20 gain does not CLIP. Clipping destroys
# information; a test that let it happen would be measuring the sensor's
# highlight rolloff and calling it a descriptor weakness.
R = (35, 35, 195); W = (208, 208, 208); Y = (55, 185, 205); B = (185, 85, 40)
G = (65, 150, 65); K = (30, 26, 26); O = (35, 120, 205); P = (150, 62, 145)
GY = (150, 150, 150); BR = (40, 70, 105); TL = (170, 165, 60); PK = (170, 150, 205)

# BGR(0, 0, 196) and BGR(0, 100, 0) have the SAME BGR2GRAY luminance — 58.6 and
# 58.7. A packet built from only these two is a flat grey field to every
# luminance block in the descriptor, which is what makes them the scalpel for
# testing the colour blocks in isolation.
ISO_RED = (0, 0, 196)
ISO_GREEN = (0, 100, 0)

TALL = (140, 62)

SIZES: dict[str, tuple[int, int]] = {
    "parle_g": TALL, "maggi": TALL, "soap_red": TALL, "shampoo_blue": TALL,
    "tea_green": TALL, "salt_white": TALL, "chips_orange": TALL,
    "sachet_purple": TALL, "stripes_h": TALL, "stripes_v": TALL,
    "cap_red_on_white": TALL, "cap_white_on_red": TALL,
    "choco_brown": TALL, "milk_half": TALL, "surf_teal_dots": TALL,
    "paste_redband": TALL, "check_pink": TALL, "noodle_yellow": TALL,
    "spice_green_v": TALL,
    # SAME long edge, SAME artwork, wider packet: the footprint gate upstream
    # constrains only the long edge, so aspect is all that separates this from
    # parle_g.
    "wide_twin": (140, 105),
    # iso-luminance layout pair — see ISO_RED above
    "iso_lr": TALL, "iso_tb": TALL,
}

#: Products that differ from another product ONLY in hue. In a grey crop they
#: are genuinely the same picture, so they are excluded from the grey run: a
#: descriptor that "separated" them in grey would be reading noise.
GREY_DROP = frozenset({"cap_white_on_red", "iso_lr", "iso_tb"})

PRODUCTS = tuple(SIZES)
GREY_PRODUCTS = tuple(k for k in SIZES if k not in GREY_DROP)


def _seed(kind: str) -> int:
    """A STABLE per-product seed. Python's builtin hash() is randomised per
    interpreter, so using it here would render a different packet in every
    process — which would make the cross-process determinism test below assert
    something it was never testing."""
    return zlib.crc32(kind.encode("utf-8"))


def render(kind: str, h: int, w: int) -> np.ndarray:
    """One synthetic wrapper. Pure function of `kind` — no hidden state."""
    rng = np.random.default_rng(_seed(kind))
    img = np.zeros((h, w, 3), dtype=np.float64)

    def fill(sl, c):
        img[sl] = c

    if kind in ("parle_g", "wide_twin"):
        fill(np.s_[:, :], Y)
        fill(np.s_[int(h*0.38):int(h*0.62), :], B)
        for k in range(6):
            y = int(h * (0.05 + 0.05 * k))
            fill(np.s_[y:y+2, int(w*0.15):int(w*0.85)], K)
    elif kind == "maggi":
        fill(np.s_[:, :int(w*0.5)], R)
        fill(np.s_[:, int(w*0.5):], Y)
        fill(np.s_[int(h*0.45):int(h*0.55), :], K)
    elif kind == "soap_red":
        fill(np.s_[:, :], R)
        yy, xx = np.mgrid[0:h, 0:w]
        img[((yy / h + xx / w) > 0.85) & ((yy / h + xx / w) < 1.15)] = W
    elif kind == "shampoo_blue":
        fill(np.s_[:, :], B)
        fill(np.s_[:int(h*0.18), :], W)
        fill(np.s_[int(h*0.45):int(h*0.55), int(w*0.2):int(w*0.8)], W)
    elif kind == "tea_green":
        fill(np.s_[:, :], G)
        img[rng.random((h, w)) < 0.18] = K
    elif kind == "salt_white":
        fill(np.s_[:, :], W)
        fill(np.s_[int(h*0.30):int(h*0.40), int(w*0.10):int(w*0.90)], GY)
        fill(np.s_[int(h*0.60):int(h*0.70), int(w*0.10):int(w*0.90)], GY)
    elif kind == "chips_orange":
        fill(np.s_[:, :], O)
        fill(np.s_[int(h*0.20):int(h*0.34), :], K)
        fill(np.s_[int(h*0.66):int(h*0.80), :], K)
    elif kind == "sachet_purple":
        fill(np.s_[:, :], P)
        for k in range(5):
            x = int(w * (0.1 + 0.18 * k))
            fill(np.s_[:, x:x+3], W)
    elif kind == "stripes_h":
        fill(np.s_[:, :], W)
        for k in range(0, 8, 2):
            fill(np.s_[int(h*k/8):int(h*(k+1)/8), :], R)
    elif kind == "stripes_v":
        fill(np.s_[:, :], W)
        for k in range(0, 8, 2):
            fill(np.s_[:, int(w*k/8):int(w*(k+1)/8)], R)
    elif kind == "cap_red_on_white":
        fill(np.s_[:, :], W); fill(np.s_[:int(h*0.22), :], R)
    elif kind == "cap_white_on_red":
        fill(np.s_[:, :], R); fill(np.s_[:int(h*0.22), :], W)
    elif kind == "choco_brown":
        fill(np.s_[:, :], BR)
        yy, xx = np.mgrid[0:h, 0:w]
        img[(yy / h - xx / w + 0.15) % 0.5 < 0.12] = TL
    elif kind == "milk_half":
        fill(np.s_[:int(h*0.5), :], W); fill(np.s_[int(h*0.5):, :], B)
    elif kind == "surf_teal_dots":
        fill(np.s_[:, :], TL)
        for i in range(4):
            for j in range(3):
                cv2.circle(img, (int(w*(0.2+0.3*j)), int(h*(0.15+0.23*i))),
                           max(3, w // 14), W, -1)
    elif kind == "paste_redband":
        fill(np.s_[:, :], W)
        fill(np.s_[int(h*0.30):int(h*0.42), :], R)
    elif kind == "check_pink":
        cell = max(4, h // 10)
        yy, xx = np.mgrid[0:h, 0:w]
        fill(np.s_[:, :], PK)
        img[((yy // cell) + (xx // cell)) % 2 == 0] = K
    elif kind == "noodle_yellow":
        fill(np.s_[:, :], Y)
        for i in range(5):
            cv2.circle(img, (w // 2, int(h*(0.12+0.19*i))), max(3, w // 10), R, -1)
    elif kind == "spice_green_v":
        fill(np.s_[:, :], G)
        fill(np.s_[:, int(w*0.35):int(w*0.65)], Y)
        fill(np.s_[int(h*0.42):int(h*0.58), :], K)
    elif kind == "iso_lr":
        fill(np.s_[:, :int(w*0.5)], ISO_RED); fill(np.s_[:, int(w*0.5):], ISO_GREEN)
    elif kind == "iso_tb":
        fill(np.s_[:int(h*0.5), :], ISO_RED); fill(np.s_[int(h*0.5):, :], ISO_GREEN)
    else:
        raise AssertionError(f"no such product {kind!r}")

    img += rng.normal(0.0, 3.0, (h, w, 3))
    return np.clip(img, 0, 255).astype(np.uint8)


# -- held-out products: a DIFFERENT generator the weights never saw ----------

_HOLD_PAL = [(60, 40, 190), (200, 150, 40), (40, 190, 220), (100, 40, 130),
             (30, 140, 90), (210, 205, 195), (25, 25, 30), (150, 200, 120),
             (90, 110, 200), (180, 100, 180)]
HOLD_SIZES = {f"h{i:02d}": TALL for i in range(14)}
HOLD_SIZES["h14"] = (140, 88)
HOLD_SIZES["h15"] = (96, 62)
HOLD_PRODUCTS = tuple(HOLD_SIZES)


def render_hold(kind: str, h: int, w: int) -> np.ndarray:
    idx = int(kind[1:])
    rng = np.random.default_rng(1000 + idx)
    n_pal = len(_HOLD_PAL)
    bi = idx % n_pal
    fi = (idx * 3 + 4) % n_pal
    if fi == bi:
        fi = (fi + 5) % n_pal
    ai = (idx * 7 + 1) % n_pal
    if ai in (bi, fi):
        ai = (ai + 3) % n_pal
    bg, fg, ac = _HOLD_PAL[bi], _HOLD_PAL[fi], _HOLD_PAL[ai]
    img = np.zeros((h, w, 3), dtype=np.float64)
    img[:] = bg
    mode, n = idx % 6, 2 + (idx % 4)
    if mode == 0:
        for k in range(n):
            y = int(h * (0.1 + 0.8 * k / max(1, n)))
            img[y:y + max(3, h // 14), :] = fg
    elif mode == 1:
        for k in range(n):
            x = int(w * (0.1 + 0.8 * k / max(1, n)))
            img[:, x:x + max(3, w // 10)] = fg
    elif mode == 2:
        img[:int(h*0.30), :] = fg
        img[int(h*0.30):int(h*0.38), :] = ac
    elif mode == 3:
        yy, xx = np.mgrid[0:h, 0:w]
        img[((yy / h + xx / w) % 0.6) < 0.22] = fg
    elif mode == 4:
        for i in range(3):
            for j in range(2):
                cv2.circle(img, (int(w*(0.3+0.4*j)), int(h*(0.2+0.3*i))),
                           max(3, w // 12), fg, -1)
    else:
        cell = max(4, h // (6 + idx % 5))
        yy, xx = np.mgrid[0:h, 0:w]
        img[((yy // cell) + (xx // cell)) % 2 == 0] = fg
    img[int(h*0.46):int(h*0.54), int(w*0.1):int(w*0.9)] = ac
    img += rng.normal(0.0, 3.0, (h, w, 3))
    return np.clip(img, 0, 255).astype(np.uint8)


# ============================================================== perturbations

def rotate(img, deg):
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)


def rescale(img, f):
    h, w = img.shape[:2]
    return cv2.resize(img, (max(2, int(w*f)), max(2, int(h*f))),
                      interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_LINEAR)


def crop_jitter(img, frac):
    """Tighter (positive) or looser (negative) than the enrolled crop — the
    placement box never lands twice in exactly the same place."""
    h, w = img.shape[:2]
    dy, dx = int(h * abs(frac)), int(w * abs(frac))
    if frac >= 0:
        return img[dy:h-dy, dx:w-dx]
    return cv2.copyMakeBorder(img, dy, dy, dx, dx, cv2.BORDER_REPLICATE)


def brighten(img, gain):
    return np.clip(img.astype(np.float64) * gain, 0, 255).astype(np.uint8)


def sensor(img, seed, amp=6.0):
    rng = np.random.default_rng(seed)
    return np.clip(img.astype(np.float64) + rng.normal(0, amp, img.shape),
                   0, 255).astype(np.uint8)


TRAIN, VAL = "train", "val"


def make_views(base: np.ndarray, split: str, grey: bool) -> dict:
    if split == TRAIN:
        vs = {
            "ref": base,
            "rot+3": rotate(base, 3),
            "rot+8": rotate(base, 8),
            "rot-8": rotate(base, -8),
            "rot180": cv2.rotate(base, cv2.ROTATE_180),
            "scale0.7": rescale(base, 0.7),
            "scale1.4": rescale(base, 1.4),
            "tight+6%": crop_jitter(base, 0.06),
            "loose-6%": crop_jitter(base, -0.06),
            "dim0.60": brighten(base, 0.60),
            "bright1.20": brighten(base, 1.20),
            "noise": sensor(base, 11),
            "combo": sensor(brighten(rotate(crop_jitter(base, 0.04), 5), 0.8), 3),
        }
    else:
        vs = {
            "ref": base,
            "rot-4": rotate(base, -4),
            "rot+11": rotate(base, 11),
            "rot-11": rotate(base, -11),
            "rot180b": rotate(cv2.rotate(base, cv2.ROTATE_180), 2),
            "scale0.55": rescale(base, 0.55),
            "scale1.9": rescale(base, 1.9),
            "tight+9%": crop_jitter(base, 0.09),
            "loose-9%": crop_jitter(base, -0.09),
            "dim0.50": brighten(base, 0.50),
            "bright1.15": brighten(base, 1.15),
            "noise9": sensor(base, 4242, 9.0),
            "combo2": sensor(
                brighten(rotate(crop_jitter(base, -0.05), -7), 1.1), 77, 8.0),
        }
    if grey:
        vs = {k: cv2.cvtColor(v, cv2.COLOR_BGR2GRAY) for k, v in vs.items()}
    return vs


#: (tag, product ids, renderer, sizes) for the two independent product families.
FAMILIES = (
    ("tune", PRODUCTS, render, SIZES),
    ("hold", HOLD_PRODUCTS, render_hold, HOLD_SIZES),
)


@pytest.fixture(scope="module")
def embedded():
    """{(family, split, grey): {product: {view: vector}}}, computed once."""
    out = {}
    for tag, prods, fn, sizes in FAMILIES:
        for split in (TRAIN, VAL):
            for grey in (False, True):
                keys = [p for p in prods if not (grey and p in GREY_DROP)]
                out[(tag, split, grey)] = {
                    k: {n: embed(im) for n, im in
                        make_views(fn(k, *sizes[k]), split, grey).items()}
                    for k in keys
                }
    return out


def _cos(a, b):
    return float(np.dot(a, b))    # both are unit vectors by contract


# ================================================================== contract

def test_vector_contract():
    v = embed(render("parle_g", *TALL))
    assert isinstance(v, np.ndarray)
    assert v.dtype == np.float32
    assert v.shape == (EMBED_DIM,)
    assert np.all(np.isfinite(v))
    assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-5
    assert EMBED_DIM == sum(d for _, d in BLOCK_SPEC)


def test_block_spec_matches_reality():
    b = blocks(render("maggi", *TALL))
    assert set(b) == {n for n, _ in BLOCK_SPEC}
    for name, dim in BLOCK_SPEC:
        assert b[name].shape == (dim,), name
        n = float(np.linalg.norm(b[name]))
        # a block is either a unit direction, or exactly zero because it saw
        # nothing, or gated down — never something in between by accident
        assert n <= 1.0 + 1e-9, name
    assert set(WEIGHTS) == {n for n, _ in BLOCK_SPEC}
    assert min(WEIGHTS.values()) >= WEIGHT_FLOOR


def test_dimension_is_fixed_regardless_of_input_shape():
    for shape in ((4, 4), (7, 300), (300, 7), (1, 1), (140, 62)):
        img = np.full(shape + (3,), 120, dtype=np.uint8)
        assert embed(img).shape == (EMBED_DIM,)


def test_accepts_grey_bgr_bgra_and_float():
    """Brain._crop returns 2-D GREY. That is the common case, not an edge one."""
    bgr = render("soap_red", *TALL)
    grey = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    bgra = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
    for img in (bgr, grey, bgra, bgr.astype(np.float64)):
        v = embed(img)
        assert v.shape == (EMBED_DIM,)
        assert np.all(np.isfinite(v))
    # BGRA drops alpha, so it must equal the BGR answer exactly
    assert np.array_equal(embed(bgra), embed(bgr))
    # a float image holding the same integers is the same picture
    assert np.array_equal(embed(bgr.astype(np.float64)), embed(bgr))
    # a 3-D single-channel image is the same picture as its 2-D self
    assert np.array_equal(embed(grey[:, :, None]), embed(grey))


def test_rejects_what_it_cannot_describe():
    with pytest.raises(EmbedderError):
        embed(np.zeros((0, 5, 3), dtype=np.uint8))
    with pytest.raises(EmbedderError):
        embed(np.zeros((4, 4, 2), dtype=np.uint8))
    with pytest.raises(EmbedderError):
        embed(np.zeros((2, 2, 2, 3), dtype=np.uint8))
    with pytest.raises(EmbedderError):
        embed(np.array([["a", "b"], ["c", "d"]]))
    with pytest.raises(EmbedderError):
        embed(np.full((4, 4, 3), np.nan))
    with pytest.raises(EmbedderError):
        embed_batch([])


def test_embed_batch_stacks():
    m = embed_batch([render("maggi", *TALL), render("tea_green", *TALL)])
    assert m.shape == (2, EMBED_DIM)
    assert m.dtype == np.float32
    assert np.array_equal(m[0], embed(render("maggi", *TALL)))


def test_block_floors_damp_only_the_degenerate_case(capsys):
    """BLOCK_FLOOR must be a safety net, not a silent rescaling of everything.

    The floor turns _centre_unit into `c / max(||c||, floor)`, so a block whose
    output norm is below 1 is a block the floor DAMPED. On ordinary packets that
    should almost never happen — otherwise the weights no longer mean what
    WEIGHTS says they mean, because a damped block quietly gives up part of its
    energy share. Measured here on colour views of the tuning products, with the
    two deliberately-degenerate iso-luminance packets excluded.
    """
    norms: dict[str, list[float]] = {n: [] for n, _ in BLOCK_SPEC}
    for prod in PRODUCTS:
        if prod.startswith("iso_"):
            continue
        for name, im in make_views(render(prod, *SIZES[prod]), TRAIN, False).items():
            b = blocks(im)
            for n, _ in BLOCK_SPEC:
                norms[n].append(float(np.linalg.norm(b[n])))
    with capsys.disabled():
        print("\n  MEASURED block output norms (1.0 = the floor never bit):")
        for n, _ in BLOCK_SPEC:
            a = np.array(norms[n])
            print(f"    {n:14s} median={np.median(a):.4f} p05={np.percentile(a,5):.4f} "
                  f"damped={100*float(np.mean(a < 0.999)):5.1f}%")
    for n, _ in BLOCK_SPEC:
        assert float(np.median(norms[n])) >= 0.85, (
            f"{n}: the floor is damping the TYPICAL case, not just the degenerate one")


def test_flat_crop_is_representable_not_a_crash():
    """A uniform field has no layout, no edges and no corners. The gated blocks
    must be exactly zero and the vector must still be finite and unit."""
    v = embed(np.full((80, 40, 3), 130, dtype=np.uint8))
    assert np.all(np.isfinite(v))
    assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-5
    b = blocks(np.full((80, 40, 3), 130, dtype=np.uint8))
    for name in DETAIL_BLOCKS | COLOUR_BLOCKS:
        assert float(np.linalg.norm(b[name])) == 0.0, name


# =============================================================== determinism

def test_deterministic_within_process():
    img = render("chips_orange", *TALL)
    first = embed(img)
    for _ in range(5):
        assert np.array_equal(embed(img), first), "embed() is not a pure function"
    # and the input was not mutated on the way through
    assert np.array_equal(img, render("chips_orange", *TALL))


def test_deterministic_across_processes():
    """Byte-identical in a FRESH interpreter. A gallery enrolled on one run and
    queried on the next has to compare like with like, or every cosine in the
    ledger is meaningless."""
    script = (
        "import numpy as np, sys; sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
        "from test_embedder import render, TALL\n"
        "from gawaah.embedder import embed\n"
        "v = embed(render('sachet_purple', *TALL))\n"
        "sys.stdout.buffer.write(v.tobytes())\n"
        % (str(REPO), str(REPO / "tests"))
    )
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          cwd=str(REPO))
    assert proc.returncode == 0, proc.stderr.decode()
    other = np.frombuffer(proc.stdout, dtype=np.float32)
    here = embed(render("sachet_purple", *TALL))
    assert other.shape == here.shape
    assert other.tobytes() == here.tobytes(), (
        "two processes disagree on the same pixels")


def test_no_model_weights_anywhere():
    """INVARIANT 3. The module must not import a deep-learning runtime, must not
    read a checkpoint and must not reach the network."""
    src = (REPO / "gawaah" / "embedder.py").read_text(encoding="utf-8")
    for banned in ("torch", "onnx", "tensorflow", "urllib", "requests",
                   "httpx", "socket", "urlopen", "hub.load", "from_pretrained",
                   "open(", "np.load", "cv2.dnn"):
        assert banned not in src, f"embedder.py mentions {banned!r}"
    loaded = set(sys.modules)
    for banned in ("torch", "onnxruntime", "tensorflow"):
        assert banned not in loaded, f"{banned} got imported"


# ================================================================ robustness

#: perturbation -> floor on cosine against the enrolled view. Every floor sits a
#: little BELOW the worst value actually measured across both product families,
#: both view splits and both colour and grey — measurement first, floor second.
#:
#: The loosest floors are the crop-jitter ones, and the packet that sets them is
#: named in the printout: `chips_orange`, two dark bands on an orange ground,
#: laid out almost symmetrically about the centre. After the 180-degree fold its
#: value grid comes down to "which of the two inner rows is darker", the bands
#: sit right on a cell boundary, and re-cropping 9% tighter walks them across it
#: and flips the sign. Weighting the value grid down fixes that packet and
#: introduces WRONG matches elsewhere, which invariant 7 will not trade for. So
#: it stands as a measured worst case: an aggressively re-cropped near-symmetric
#: banded packet scores 0.51 against itself, lands under phi, and goes AMBER.
#: Amber is the correct outcome; a wrong price would not be.
ROBUST_FLOOR = {
    "rot+3": 0.72, "rot-4": 0.75,
    "rot+8": 0.52, "rot-8": 0.62, "rot+11": 0.46, "rot-11": 0.50,
    "rot180": 0.9999, "rot180b": 0.75,
    "scale0.7": 0.80, "scale1.4": 0.78, "scale0.55": 0.78, "scale1.9": 0.78,
    "tight+6%": 0.40, "loose-6%": 0.78, "tight+9%": 0.35, "loose-9%": 0.70,
    "dim0.60": 0.92, "bright1.20": 0.95, "dim0.50": 0.92, "bright1.15": 0.95,
    "noise": 0.80, "noise9": 0.70,
    "combo": 0.44, "combo2": 0.68,
}


def test_robust_to_every_perturbation(embedded, capsys):
    worst: dict[str, tuple[float, str]] = {}
    for key, fam in embedded.items():
        for prod, vs in fam.items():
            ref = vs["ref"]
            for name, v in vs.items():
                if name == "ref":
                    continue
                c = _cos(ref, v)
                tag = f"{key[0]}/{'grey' if key[2] else 'colour'}/{prod}"
                if name not in worst or c < worst[name][0]:
                    worst[name] = (c, tag)
    with capsys.disabled():
        print("\n  MEASURED robustness (worst case over both product families,"
              " both splits, colour and grey):")
        for name in sorted(worst):
            c, tag = worst[name]
            print(f"    {name:11s} min cos = {c:.4f}  floor {ROBUST_FLOOR[name]:.4f}"
                  f"   worst: {tag}")
    for name, (c, tag) in worst.items():
        assert c >= ROBUST_FLOOR[name], f"{name} fell to {c:.4f} on {tag}"


def test_180_degree_rotation_is_invariant_by_construction(embedded, capsys):
    """minAreaRect cannot tell a packet from the same packet turned around, so
    the descriptor must not either. Every block except ORB is invariant to the
    last bit of float precision — by folding, not by a canonical-flip heuristic
    that would flap on a near-symmetric packet.

    ORB is the one exception and it is cv2's, not ours: FAST's corner test and
    BRIEF's patch sampling are not perfectly 180-degree symmetric, so the
    keypoint set shifts by a hair. Measured, that costs under 2e-5 of the full
    vector. The honest claim is therefore "exact for twelve of thirteen blocks,
    and 0.99998 overall", not "exact".
    """
    worst_block, worst_full = (2.0, ""), (2.0, "")
    for key, fam in embedded.items():
        if key[1] != TRAIN:
            continue
        for prod, vs in fam.items():
            c = _cos(vs["ref"], vs["rot180"])
            if c < worst_full[0]:
                worst_full = (c, f"{key} {prod}")
    for tag, prods, fn, sizes in FAMILIES:
        for prod in prods:
            img = fn(prod, *sizes[prod])
            for grey in (False, True):
                x = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if grey else img
                b1 = blocks(x)
                b2 = blocks(cv2.rotate(x, cv2.ROTATE_180))
                for name, _ in BLOCK_SPEC:
                    n1 = float(np.linalg.norm(b1[name]))
                    n2 = float(np.linalg.norm(b2[name]))
                    if n1 < 1e-9 or n2 < 1e-9:
                        continue
                    # compare DIRECTIONS: a gated block's norm is its gate
                    d = float(np.dot(b1[name], b2[name])) / (n1 * n2)
                    if name == "orb":
                        assert d >= 0.99, f"orb {tag}/{prod}: {d:.6f}"
                        continue
                    assert d >= 1.0 - 1e-9, (
                        f"{name} is not 180-degree exact on {tag}/{prod}: {d:.12f}")
                    if d < worst_block[0]:
                        worst_block = (d, f"{name} {tag}/{prod}")
    with capsys.disabled():
        print(f"\n  MEASURED 180-degree invariance: worst non-ORB block direction "
              f"{worst_block[0]:.12f}, worst full vector {worst_full[0]:.8f}")
    assert worst_full[0] >= 1.0 - 1e-4


def test_brightness_gain_is_near_exactly_cancelled(embedded, capsys):
    """A global lighting gain must cancel, because every intensity feature is a
    RATIO to the crop's own mean. It cancels to better than 0.02 of cosine."""
    vals = []
    for key, fam in embedded.items():
        for prod, vs in fam.items():
            for name in ("dim0.60", "bright1.20", "dim0.50", "bright1.15"):
                if name in vs:
                    vals.append(_cos(vs["ref"], vs[name]))
    with capsys.disabled():
        print(f"\n  MEASURED brightness: n={len(vals)} min={min(vals):.4f} "
              f"mean={float(np.mean(vals)):.4f}")
    assert float(np.mean(vals)) >= 0.93


# ================================================================ separation

def _distributions(fam):
    """(same, different) cosine samples for one family/split/colour bucket.

    same = enrolled reference against another VIEW OF THE SAME product.
    different = enrolled reference against a view of ANOTHER product.
    Reference-against-view is the operational framing: the gallery holds what
    was enrolled and the query is whatever the camera just saw.
    """
    prods = list(fam)
    same, diff = [], []
    for k in prods:
        for name, q in fam[k].items():
            if name == "ref":
                continue
            same.append(_cos(fam[k]["ref"], q))
            diff.extend(_cos(fam[j]["ref"], q) for j in prods if j != k)
    return np.array(same), np.array(diff)


def test_same_and_different_distributions_are_separated(embedded, capsys):
    """The headline number. If these two distributions sat on top of each other
    the descriptor would be useless and this test would say so."""
    rows = []
    for key in sorted(embedded):
        same, diff = _distributions(embedded[key])
        rows.append((key, same, diff))
    with capsys.disabled():
        print("\n  MEASURED same-vs-different cosine distributions:")
        print(f"    {'bucket':22s} {'same mean':>9s} {'same p05':>9s} "
              f"{'diff mean':>9s} {'diff p95':>9s} {'gap p05-p95':>11s}")
        for key, same, diff in rows:
            tag = f"{key[0]}/{key[1]}/{'grey' if key[2] else 'colour'}"
            gap = float(np.percentile(same, 5) - np.percentile(diff, 95))
            print(f"    {tag:22s} {same.mean():9.4f} "
                  f"{np.percentile(same,5):9.4f} {diff.mean():9.4f} "
                  f"{np.percentile(diff,95):9.4f} {gap:+11.4f}")
    for key, same, diff in rows:
        assert same.mean() - diff.mean() >= 0.45, key
        assert float(np.percentile(same, 5)) > float(np.percentile(diff, 95)), key
        assert float(np.percentile(same, 5)) >= PHI, key


# ===================================== the decision identity.py actually makes

def _run_gallery(fam, prods, theta=THETA, phi=PHI):
    """Enrol every product's reference view into a real Gallery, then query the
    real Identifier with every perturbed view. Returns (matched, wrong,
    abstained, margins).

    Every product shares LONG_MM, so the footprint gate admits ALL of them every
    time. This is the hardest possible shortlist and it is deliberate: on a real
    mat the metric tiebreak would have thrown most of them out first.
    """
    gallery = Gallery()
    for k in prods:
        gallery.enroll(k, [fam[k]["ref"]], LONG_MM)
    # The embedder is not called here — the vectors already exist — so the
    # injected embed_fn is the identity on an already-computed vector.
    ident = Identifier(gallery, lambda v: v, theta=theta, phi=phi, tau_mm=4.0)
    matched = wrong = abstained = 0
    margins = []
    for k in prods:
        for name, q in fam[k].items():
            if name == "ref":
                continue
            r = ident.identify(q, LONG_MM)
            margins.append(r.margin)
            if r.sku_id is None:
                abstained += 1
            elif r.sku_id == k:
                matched += 1
            else:
                wrong += 1
    return matched, wrong, abstained, np.array(margins)


def test_identifier_never_prices_the_wrong_product(embedded, capsys):
    """INVARIANT 7, measured end to end through identity.py at the SHIPPED
    theta and phi. A wrong sku is a wrong price on a real customer's bill; an
    abstention is an amber line the shopkeeper taps. Wrong must be zero."""
    total_m = total_w = total_a = 0
    rows = []
    for key in sorted(embedded):
        fam = embedded[key]
        prods = list(fam)
        m, w, a, margins = _run_gallery(fam, prods)
        rows.append((key, m, w, a, len(prods), margins))
        total_m += m; total_w += w; total_a += a
    n = total_m + total_w + total_a
    with capsys.disabled():
        print("\n  MEASURED identify() outcomes at theta=0.10 phi=0.55, with "
              "EVERY product inside the footprint gate:")
        print(f"    {'bucket':22s} {'skus':>4s} {'matched':>8s} {'WRONG':>6s} "
              f"{'amber':>6s} {'margin p05':>10s}")
        for key, m, w, a, k, margins in rows:
            tag = f"{key[0]}/{key[1]}/{'grey' if key[2] else 'colour'}"
            print(f"    {tag:22s} {k:4d} {m:8d} {w:6d} {a:6d} "
                  f"{np.percentile(margins,5):10.4f}")
        print(f"    TOTAL matched={total_m}/{n} ({100*total_m/n:.1f}%)  "
              f"WRONG={total_w}  amber={total_a}")
    assert total_w == 0, "identity proposed a WRONG sku — invariant 7 breached"
    assert total_m / n >= 0.85, f"only {100*total_m/n:.1f}% matched"


def test_widening_the_gates_is_what_creates_wrong_answers(embedded):
    """A guard against a future 'improvement' that makes the demo look better.
    Doubling theta's tolerance and dropping phi to 0.30 turns abstentions into
    WRONG skus on this very data — which is the whole argument for not doing
    it. If this test ever stops finding wrong answers at the loosened gates,
    the shipped gates are no longer the tight ones."""
    fam = embedded[("hold", VAL, True)]
    prods = list(fam)
    _, tight_wrong, _, _ = _run_gallery(fam, prods, theta=THETA, phi=PHI)
    _, loose_wrong, _, _ = _run_gallery(fam, prods, theta=0.0, phi=0.30)
    assert tight_wrong == 0
    assert loose_wrong > tight_wrong


# ================================================================ hard pairs

def _pair(a_img, b_img, grey=False):
    if grey:
        a_img = cv2.cvtColor(a_img, cv2.COLOR_BGR2GRAY)
        b_img = cv2.cvtColor(b_img, cv2.COLOR_BGR2GRAY)
    return _cos(embed(a_img), embed(b_img))


def _pair_margins(a_img, b_img, grey=False):
    """The quantity identify() actually gates on, for a two-SKU gallery.

    For every perturbed view of each packet: cosine to its OWN enrolled vector
    minus cosine to the OTHER one. That difference is exactly the top1-top2
    margin the identifier compares against theta, so asserting on it is
    asserting on the real decision — unlike comparing a worst-case
    within-product cosine to a best-case across-product one, which pairs up two
    numbers that never meet in the same comparison.

    Returns (worst margin, worst top1) across every view of both packets.
    """
    if grey:
        a_img = cv2.cvtColor(a_img, cv2.COLOR_BGR2GRAY)
        b_img = cv2.cvtColor(b_img, cv2.COLOR_BGR2GRAY)
    ea, eb = embed(a_img), embed(b_img)
    margins, tops = [], []
    for own, other, img in ((ea, eb, a_img), (eb, ea, b_img)):
        for name, v in make_views(img, TRAIN, False).items():
            q = embed(v)
            margins.append(_cos(own, q) - _cos(other, q))
            tops.append(_cos(own, q))
    return min(margins), min(tops)


def test_hard_pair_same_size_same_colours_different_layout(capsys):
    """The brief's hard case, built to be as hard as it can be: identical
    footprint, identical two colours, identical 50/50 areas. ONLY the layout
    differs — one wrapper is striped across, the other along."""
    h = render("stripes_h", *TALL)
    v = render("stripes_v", *TALL)
    out = []
    for grey in (False, True):
        margin, top1 = _pair_margins(h, v, grey)
        out.append((grey, _pair(h, v, grey), margin, top1))
    with capsys.disabled():
        print("\n  MEASURED hard pair stripes_h vs stripes_v "
              "(same size, same colours, same 50/50 areas, layout turned 90 deg):")
        for grey, across, margin, top1 in out:
            print(f"    {'grey  ' if grey else 'colour'}: ref-to-ref cos={across:.4f}"
                  f"   worst top1={top1:.4f}   worst top1-top2 margin={margin:+.4f}")
    for grey, across, margin, top1 in out:
        assert margin >= THETA, f"grey={grey}: margin {margin:.4f} < theta"
        assert top1 >= PHI, f"grey={grey}: top1 {top1:.4f} < phi"


def test_hard_pair_red_cap_on_white_vs_white_cap_on_red(capsys):
    """The brief's other stated case. A global colour histogram alone cannot do
    this when the areas match; the spatial layout blocks can."""
    a = render("cap_red_on_white", *TALL)
    b = render("cap_white_on_red", *TALL)
    rows = []
    for grey in (False, True):
        margin, top1 = _pair_margins(a, b, grey)
        rows.append((grey, _pair(a, b, grey), margin, top1))
    with capsys.disabled():
        print("\n  MEASURED cap pair (red cap on white tube vs white cap on red):")
        for grey, across, margin, top1 in rows:
            print(f"    {'grey  ' if grey else 'colour'}: ref-to-ref cos={across:.4f}"
                  f"   worst top1={top1:.4f}   worst top1-top2 margin={margin:+.4f}")
    for grey, across, margin, top1 in rows:
        assert margin >= THETA
        assert top1 >= PHI


def _sandwich(outer, inner, h=140, w=62):
    rng = np.random.default_rng(5)
    im = np.zeros((h, w, 3), dtype=np.float64)
    im[:] = outer
    im[int(h*0.25):int(h*0.75), :] = inner
    im += rng.normal(0, 3.0, (h, w, 3))
    return np.clip(im, 0, 255).astype(np.uint8)


def test_spatial_colour_grid_is_the_only_block_that_can_do_this(capsys):
    """The scalpel for the spatial colour grid.

    Two wrappers built from ISO-LUMINANT red and green: red/green/red against
    green/red/green, 25/50/25. Identical hue histogram, identical saturation,
    identical shape, identical aspect, and a FLAT grey field so every luminance
    block is correctly gated to zero and ORB finds no corners. The colour
    boundaries run horizontally in both, so chroma_orient is identical too.

    Measured: every other block agrees to +1.0000 or is exactly 0, chroma_grid
    disagrees at -0.9998, and with chroma_grid removed the two vectors are
    identical to 1.0000. This is what the block is FOR, and it is the only
    evidence that it earns its weight — the end-to-end ablation puts its
    contribution at plus or minus one match in 1752, so without this test it
    would look like ballast.
    """
    a = _sandwich(ISO_RED, ISO_GREEN)
    b = _sandwich(ISO_GREEN, ISO_RED)
    ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    assert abs(float(ga.mean()) - float(gb.mean())) < 0.5
    assert float(ga.std()) < 3.0 and float(gb.std()) < 3.0   # flat in grey

    ba, bb = blocks(a), blocks(b)
    names = [n for n, _ in BLOCK_SPEC]
    agree = {n: float(np.dot(ba[n], bb[n])) for n in names}
    blind, mute = [], []
    for n in names:
        if n == "chroma_grid":
            continue
        if float(np.linalg.norm(ba[n])) == 0.0:
            mute.append(n)
            continue
        blind.append(n)
        assert agree[n] > 0.999, (
            f"{n} was expected to be blind to this pair, got {agree[n]:.4f}")
    assert agree["chroma_grid"] < -0.9

    def asm(b, w):
        v = np.concatenate([b[n] * w[n] for n in names])
        nn = float(np.linalg.norm(v))
        return v / nn if nn > 0 else v

    with_grid = float(np.dot(asm(ba, WEIGHTS), asm(bb, WEIGHTS)))
    without = float(np.dot(asm(ba, {**WEIGHTS, "chroma_grid": 0.0}),
                           asm(bb, {**WEIGHTS, "chroma_grid": 0.0})))
    margin, top1 = _pair_margins(a, b)
    with capsys.disabled():
        print("\n  MEASURED iso-luminance colour-layout pair "
              "(red/green/red vs green/red/green, identical in grey):")
        print(f"    blocks that AGREE at +1.0: {', '.join(blind)}")
        print(f"    blocks correctly gated to zero: {', '.join(mute)}")
        print(f"    chroma_grid disagrees at {agree['chroma_grid']:+.4f}")
        print(f"    with chroma_grid : ref-to-ref cos = {with_grid:.4f}")
        print(f"    chroma_grid = 0  : ref-to-ref cos = {without:.4f}"
              f"   <- indistinguishable")
        print(f"    worst top1={top1:.4f}   worst top1-top2 margin={margin:+.4f}")
    assert without > 0.999, "expected the pair to be identical without the grid"
    assert margin >= THETA
    assert top1 >= PHI


def test_aspect_leads_but_does_not_always_clear_theta_on_a_same_artwork_pair(capsys):
    """A LIMIT, and the reason it is a limit is the most important thing here.

    The footprint gate upstream constrains only the LONG edge, so two packets
    with the same long edge and the SAME PRINTING reach the embedder together
    and only aspect can part them. Aspect does lead — the correct sku is top1 on
    every view — but the margin lands around 0.08 against a theta of 0.10, so
    some views go amber.

    That is a deliberate choice and it was made the expensive way. Weighting
    `aspect` up until this pair cleared theta is exactly what an earlier version
    did, and because aspect is IDENTICAL for every pair of packets with the same
    shape, all that weight did was raise the similarity floor under every other
    comparison in the catalogue. The measurable result was an untaught purple
    box being named and priced as a yellow biscuit packet at 0.566, over a phi
    of 0.55. One forced match here bought one confidently wrong price there.

    Invariant 7 decides that trade in one direction only. So the assertion is
    the one that actually matters — the right sku always LEADS, and is never
    beaten — and the margin is reported rather than demanded.
    """
    a = render("parle_g", *SIZES["parle_g"])
    b = render("wide_twin", *SIZES["wide_twin"])
    margin, top1 = _pair_margins(a, b)
    with capsys.disabled():
        print("\n  MEASURED aspect pair (identical artwork, 62 px vs 105 px wide,"
              " same long edge so the footprint gate cannot help):")
        print(f"    ref-to-ref cos = {_pair(a, b):.4f}"
              f"   worst top1={top1:.4f}   worst top1-top2 margin={margin:+.4f}"
              f"   -> leads always, clears theta only sometimes: AMBER, not wrong")
    assert margin > 0.0, "the correct sku must at least LEAD on every view"
    assert top1 >= PHI


# ============================================================ honest limits

def test_grey_cannot_separate_a_colour_only_difference_and_says_so(capsys):
    """A LIMIT, asserted as a limit.

    Brain._crop hands the embedder a GREY crop. Two wrappers that differ only in
    hue are then the same picture, and there is no honest way to tell them
    apart. The descriptor must report them as similar — and identity.py must
    therefore ABSTAIN rather than pick one. A descriptor that "separated" them
    in grey would be reading sensor noise and would be confidently wrong about a
    price, which invariant 7 says is the worst outcome available.
    """
    # ISO_RED and ISO_GREEN are chosen to have the SAME BGR2GRAY luminance, so
    # the two packets are byte-for-byte the same picture once colour is gone.
    # Anything else would be testing a luminance difference and calling it hue.
    rng = np.random.default_rng(3)
    a = np.zeros((140, 62, 3), dtype=np.float64)
    b = np.zeros((140, 62, 3), dtype=np.float64)
    a[:] = ISO_RED
    b[:] = ISO_GREEN
    for im in (a, b):
        im[int(140*0.30):int(140*0.42), :] = (210, 210, 210)
        im += rng.normal(0, 3.0, (140, 62, 3))
    a = np.clip(a, 0, 255).astype(np.uint8)
    b = np.clip(b, 0, 255).astype(np.uint8)

    colour_cos = _pair(a, b, grey=False)
    grey_cos = _pair(a, b, grey=True)
    with capsys.disabled():
        print("\n  MEASURED honest limit — two packets differing ONLY in hue:")
        print(f"    in colour: cos = {colour_cos:.4f}  (separable)")
        print(f"    in grey  : cos = {grey_cos:.4f}  (NOT separable — correct)")

    margin_in_colour, top1_in_colour = _pair_margins(a, b, grey=False)
    assert margin_in_colour >= THETA, "colour should tell these apart"
    assert top1_in_colour >= PHI
    assert grey_cos > 0.95, "grey must NOT pretend to tell these apart"
    assert grey_cos - colour_cos > 0.25

    # ... and the consequence downstream is an abstention, never a price.
    g = Gallery()
    g.enroll("red_packet", [embed(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY))], LONG_MM)
    g.enroll("blue_packet", [embed(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY))], LONG_MM)
    ident = Identifier(g, lambda v: v, theta=THETA, phi=PHI)
    r = ident.identify(embed(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)), LONG_MM)
    assert r.sku_id is None
    assert r.reason in ("below_margin", "ambiguous_pair")


def test_featureless_crop_is_not_confidently_matched(capsys):
    """A blank packet has nothing to describe. Two DIFFERENT blank packets must
    not be separated, and the identifier must abstain instead of coin-flipping
    a price."""
    a = np.full((140, 62), 120, dtype=np.uint8)
    b = np.full((140, 62), 180, dtype=np.uint8)
    c = float(np.dot(embed(a), embed(b)))
    with capsys.disabled():
        print(f"\n  MEASURED two different blank packets: cos = {c:.4f} "
              "(high on purpose — there is nothing to tell apart)")
    g = Gallery()
    g.enroll("blank_a", [embed(a)], LONG_MM)
    g.enroll("blank_b", [embed(b)], LONG_MM)
    ident = Identifier(g, lambda v: v, theta=THETA, phi=PHI)
    assert ident.identify(embed(a), LONG_MM).sku_id is None


def test_enrolment_collision_guard_fires_on_an_indistinguishable_packet():
    """identity.py refuses to enrol what it could never later separate. With a
    REAL embedder that guard has to actually trigger, or a shopkeeper enrols two
    lookalikes and gets permanent amber at the counter with no explanation."""
    a = render("parle_g", *TALL)
    twin = sensor(a, 99, 2.0)          # the same packet, photographed again
    other = render("check_pink", *TALL)
    g = Gallery()
    g.enroll("parle_g", [embed(a)], LONG_MM)
    ident = Identifier(g, lambda v: v, theta=THETA, phi=PHI, tau_mm=4.0)

    c = ident.check_collision([embed(twin)], LONG_MM)
    assert c.collides and c.sku_id == "parle_g"
    assert not ident.check_collision([embed(other)], LONG_MM).collides


# ==================================================================== speed

def test_fast_enough_for_a_placement(capsys):
    """Per placement, not per frame. A shopkeeper sets down maybe three items a
    second; the budget is generous and the measurement is what matters."""
    img = render("check_pink", *TALL)
    embed(img)                                   # warm cv2's lazy allocations
    n = 200
    t0 = time.perf_counter()
    for _ in range(n):
        embed(img)
    per_ms = (time.perf_counter() - t0) / n * 1000.0

    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    embed(grey)
    t0 = time.perf_counter()
    for _ in range(n):
        embed(grey)
    grey_ms = (time.perf_counter() - t0) / n * 1000.0

    with capsys.disabled():
        print(f"\n  MEASURED speed: colour {per_ms:.3f} ms/crop, "
              f"grey {grey_ms:.3f} ms/crop ({EMBED_DIM} dims)")
    assert per_ms < 15.0, f"embed() took {per_ms:.2f} ms"


def test_ablation_records_what_each_block_is_worth(embedded, capsys):
    """Not a threshold — a LEDGER. Prints what dropping each block costs, so a
    block that has quietly stopped earning its weight is visible rather than
    inferred. Only the aggregate is asserted."""
    names = [n for n, _ in BLOCK_SPEC]
    per_block = {}
    fam_keys = [k for k in embedded if k[1] == VAL]

    # rebuild from blocks so weights can be varied without re-embedding
    raw = {}
    for tag, prods, fn, sizes in FAMILIES:
        for grey in (False, True):
            keys = [p for p in prods if not (grey and p in GREY_DROP)]
            raw[(tag, VAL, grey)] = {
                k: {n: blocks(im) for n, im in
                    make_views(fn(k, *sizes[k]), VAL, grey).items()}
                for k in keys
            }

    def assemble(b, w):
        v = np.concatenate([b[n] * w[n] for n in names])
        nn = float(np.linalg.norm(v))
        return (v / nn if nn > 0 else v).astype(np.float32)

    def score(w):
        m = tot = wrong = 0
        for key in fam_keys:
            fam = {k: {n: assemble(b, w) for n, b in vs.items()}
                   for k, vs in raw[key].items()}
            a, b_, _, _ = _run_gallery(fam, list(fam))
            m += a; wrong += b_
            tot += sum(len(v) - 1 for v in fam.values())
        return m, tot, wrong

    base_m, base_n, base_w = score(WEIGHTS)
    for n in names:
        m, _, w = score({**WEIGHTS, n: 0.0})
        per_block[n] = (m - base_m, w)
    with capsys.disabled():
        print(f"\n  MEASURED drop-one ablation on held-out views "
              f"(baseline matched={base_m}/{base_n}, wrong={base_w}):")
        for n, (d, w) in sorted(per_block.items(), key=lambda t: t[1][0]):
            print(f"    drop {n:14s} matched {d:+4d}   wrong {w:+d}")
    assert base_w == 0
    assert base_m / base_n >= 0.85
