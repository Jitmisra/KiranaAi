"""NAZAR — the embedder. A deterministic, model-free descriptor of an item crop.

WHY THIS FILE EXISTS
--------------------
``gawaah/identity.py`` has always taken an INJECTED ``embed_fn`` and has never
been given a real one. Every gallery in the repo is fed by a test double. This
is the function those doubles were standing in for::

    from gawaah.embedder import embed
    ident = Identifier(gallery, embed).identify(crop, long_edge_mm)

INVARIANT 3 — ZERO MODEL WEIGHTS, AND WHY THAT IS NOT AN EXCUSE
---------------------------------------------------------------
No checkpoint is downloaded, imported, bundled or cached. Everything below is
computed from cv2 primitives that already ship in this build: ``cvtColor``,
``calcHist``-equivalent binning, ``Sobel``, ``Canny``, ``moments`` and ``ORB``.
(``AKAZE`` is absent from this build and ``SIFT`` is not available on the JS
side, so ORB is the only descriptor that can exist on BOTH halves of the
system. That is the reason it is ORB, not a preference.)

This is a CLASSICAL descriptor and it is honest about its ceiling. For an
open-world "what is this object" question it would be a poor answer. The
question here is bounded and different: a shopkeeper enrols perhaps two dozen
kirana packets HIMSELF, on a printed mat, under his own light, and the metric
footprint has already thrown out everything that is the wrong SIZE before
appearance is consulted at all. Separating ~24 known wrappers, having already
been told the object is 71 mm long, is a job a colour-layout-texture descriptor
can actually do — and where it cannot, invariant 7 says abstain, and the
thresholds in identity.py do exactly that.

WHAT IT MUST SURVIVE
--------------------
The crop it is handed comes from ``Brain._crop``: the oriented, upright cut of
one placement out of the 840x1188 metric buffer. Three things still vary and
each one is handled explicitly, not hoped away:

  ROTATION.  ``minAreaRect`` fixes the angle only modulo 180 degrees, so the
             SAME packet can arrive upright or upside-down depending on which
             way the shopkeeper set it down. Rather than pick a canonical flip
             from a statistic that can flap between two nearly-tied poses, every
             spatially-aware block here is made EXACTLY 180-degree invariant by
             construction: cells are folded onto their 180-degree partners as an
             unordered ``(min, max)`` pair, and gradient orientation is taken
             modulo 180 degrees. Residual few-degree error is absorbed by coarse
             cells and circularly-smoothed orientation bins.
  SCALE.     The crop is resized to a canonical square before anything is
             measured, so crop tightness cannot change the vector's meaning.
             Aspect ratio is measured from the ORIGINAL crop and carried as its
             own small block, because resizing to a square destroys it.
  BRIGHTNESS. Nothing raw-intensity survives. The spatial value grid is a RATIO
             to the crop's own mean, and the intensity histogram is binned on
             that same ratio, so a global gain change cancels exactly. Colour is
             carried as chromaticity (r/(r+g+b)) and HSV hue, not as RGB.

GREYSCALE INPUT IS A FIRST-CLASS CASE, NOT A DEGRADATION BUG
-------------------------------------------------------------
``Brain._crop`` returns a 2-D GREY array, and ``SimSource`` renders grey frames
on purpose. So the colour blocks are frequently fed an image with no colour at
all. They do not crash and they do not fabricate: a block whose content is
constant collapses to zero when it is mean-centred, contributes no energy, and
the descriptor falls back on layout, edges, moments and ORB. That is the
correct behaviour and ``tests/test_embedder.py`` measures the separation in
grey as a separate number, because it is a different (harder) number.

THE ONE STRUCTURAL TRICK: MEAN-CENTRE EVERY BLOCK
-------------------------------------------------
Each block is mean-centred over its own dimensions, then L2-normalised, then
scaled by a weight, and only then concatenated and L2-normalised as a whole.
Centring is what makes cosine mean anything. An uncentred histogram block
carries a large CONSTANT direction (every histogram sums to 1, every grey
chromaticity is 1/3) shared by every product in the world; leaving it in floats
the similarity between two completely different packets up towards 0.9 and
there is no room left above phi=0.55 for a margin to exist in. Centred, a block
encodes only DEPARTURE from flat, unrelated products land near zero cosine, and
theta has somewhere to live. The per-block L2 then makes the weights mean what
they say: block b owns exactly w_b^2 / sum(w^2) of the final vector's energy,
regardless of how many dimensions it happens to have.

Nothing here touches money, so plain floats are correct and the no-float lint
does not (and must not) cover this file.
"""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np

__all__ = [
    "embed",
    "embed_batch",
    "blocks",
    "EMBED_DIM",
    "BLOCK_SPEC",
    "WEIGHTS",
    "EmbedderError",
]


class EmbedderError(ValueError):
    """A crop this function cannot describe. Distinct from a low similarity:
    a low similarity is a result, this is a bug in the caller."""


# ------------------------------------------------------------------ geometry

CANON = 64          # canonical square for colour / grid / gradient work
CANON_ORB = 128     # ORB needs room: a 31 px patch on a 64 px image is absurd
GRID = 4            # 4x4 spatial cells for colour and value layout
EDGE_GRID = 2       # 2x2 spatial cells for oriented-edge layout

HUE_BINS = 12
SAT_BINS = 8
VAL_BINS = 8
ORI_BINS = 12       # global gradient orientation, modulo 180 degrees
EDGE_ORI_BINS = 6   # per-cell gradient orientation
ASPECT_BINS = 8
ORB_BITS = 256

_EPS = 1e-12

#: Ordered (name, dimension) of every block, in concatenation order.
BLOCK_SPEC: tuple[tuple[str, int], ...] = (
    ("hue", HUE_BINS),
    ("sat", SAT_BINS),
    ("val", VAL_BINS),
    ("chroma_grid", GRID * GRID * 2),
    ("value_grid", GRID * GRID),
    ("orient", ORI_BINS),
    ("orient_grid", EDGE_GRID * EDGE_GRID * EDGE_ORI_BINS),
    ("moments", 7),
    ("aspect", ASPECT_BINS),
    ("stats", 4),
    ("orb", ORB_BITS),
)

#: Share of the final vector's ENERGY each block gets is w^2 / sum(w^2).
#: These are not decoration: they were set by measuring each block's
#: same-vs-different separation on the synthetic product set in
#: tests/test_embedder.py, in colour AND in grey, and keeping what separated.
WEIGHTS: dict[str, float] = {
    "hue": 1.00,
    "sat": 0.50,
    "val": 0.50,
    "chroma_grid": 1.10,
    "value_grid": 1.10,
    "orient": 0.90,
    "orient_grid": 1.00,
    "moments": 0.45,
    "aspect": 0.35,
    "stats": 0.35,
    "orb": 0.60,
}

EMBED_DIM = sum(d for _, d in BLOCK_SPEC)


# ------------------------------------------------------------------- helpers

def _as_bgr_u8(crop: Any) -> tuple[np.ndarray, int, int]:
    """Coerce any accepted crop to a contiguous (h, w, 3) uint8 BGR image.

    A 2-D array is REPLICATED to three channels rather than rejected, because
    that is exactly what ``Brain._crop`` hands us. Replication is the honest
    lift: hue and saturation of a grey image are genuinely zero, so the colour
    blocks correctly report "no colour evidence" instead of inventing some.
    """
    a = np.asarray(crop)
    if a.dtype == np.bool_ or a.dtype.kind not in "uif":
        raise EmbedderError(f"crop must be numeric, got dtype {a.dtype!r}")
    if a.ndim == 2:
        h, w = a.shape
        chans = 1
    elif a.ndim == 3:
        h, w, chans = a.shape
    else:
        raise EmbedderError(f"crop must be 2-D or 3-D, got shape {a.shape}")
    if h < 1 or w < 1:
        raise EmbedderError(f"crop is empty: shape {a.shape}")
    if chans not in (1, 3, 4):
        raise EmbedderError(f"crop must have 1, 3 or 4 channels, got {chans}")

    if a.dtype != np.uint8:
        if not np.all(np.isfinite(a)):
            raise EmbedderError("crop contains NaN or inf")
        a = np.clip(np.rint(np.asarray(a, dtype=np.float64)), 0.0, 255.0)
        a = a.astype(np.uint8)

    a = np.ascontiguousarray(a)
    if a.ndim == 2:
        bgr = cv2.cvtColor(a, cv2.COLOR_GRAY2BGR)
    elif chans == 1:
        bgr = cv2.cvtColor(a[:, :, 0], cv2.COLOR_GRAY2BGR)
    elif chans == 4:
        bgr = cv2.cvtColor(a, cv2.COLOR_BGRA2BGR)
    else:
        bgr = a
    return np.ascontiguousarray(bgr), int(h), int(w)


def _resize(img: np.ndarray, side: int) -> np.ndarray:
    """Deterministic canonical resize. INTER_AREA down, INTER_LINEAR up —
    both are fixed-kernel and neither consults a random state."""
    h, w = img.shape[:2]
    interp = cv2.INTER_AREA if (h >= side and w >= side) else cv2.INTER_LINEAR
    return cv2.resize(img, (side, side), interpolation=interp)


def _fold_pairs(n: int) -> tuple[tuple[int, int], ...]:
    """Index pairs folding an n x n grid onto its 180-degree rotation.

    Cell (i, j) maps to (n-1-i, n-1-j). For even n every cell has a distinct
    partner, so the folded feature has exactly as many numbers as the grid did:
    each pair contributes its unordered (min, max) instead of its ordered
    (first, second). That is what makes the block EXACTLY invariant to the
    packet being set down the other way round, with no canonical-flip decision
    that could flap between two nearly-tied poses.
    """
    pairs: list[tuple[int, int]] = []
    seen: set[int] = set()
    for i in range(n):
        for j in range(n):
            a = i * n + j
            b = (n - 1 - i) * n + (n - 1 - j)
            if a in seen or b in seen:
                continue
            seen.add(a)
            seen.add(b)
            pairs.append((a, b))
    return tuple(pairs)


_GRID_PAIRS = _fold_pairs(GRID)
_EDGE_PAIRS = _fold_pairs(EDGE_GRID)


def _fold(cells: np.ndarray, pairs: tuple[tuple[int, int], ...]) -> np.ndarray:
    """Fold a (n*n, k) per-cell feature block into a (n*n*k,) 180-invariant one."""
    out = np.empty((len(pairs), 2, cells.shape[1]), dtype=np.float64)
    for idx, (a, b) in enumerate(pairs):
        lo = np.minimum(cells[a], cells[b])
        hi = np.maximum(cells[a], cells[b])
        out[idx, 0] = lo
        out[idx, 1] = hi
    return out.reshape(-1)


def _cell_means(img: np.ndarray, n: int) -> np.ndarray:
    """Mean of each of n x n equal cells. Returns (n*n, channels)."""
    side = img.shape[0]
    step = side // n
    chans = 1 if img.ndim == 2 else img.shape[2]
    flat = img.reshape(side, side, chans) if img.ndim == 2 else img
    out = np.empty((n * n, chans), dtype=np.float64)
    for i in range(n):
        for j in range(n):
            y0, y1 = i * step, (i + 1) * step if i < n - 1 else side
            x0, x1 = j * step, (j + 1) * step if j < n - 1 else side
            out[i * n + j] = flat[y0:y1, x0:x1].reshape(-1, chans).mean(axis=0)
    return out


def _smooth_circular(h: np.ndarray) -> np.ndarray:
    """[0.25, 0.5, 0.25] wrapped. Buys tolerance to the few degrees of residual
    rotation the oriented crop does not remove, at the cost of resolution we do
    not have anyway with 12 bins."""
    return 0.25 * np.roll(h, 1) + 0.5 * h + 0.25 * np.roll(h, -1)


def _l1(v: np.ndarray) -> np.ndarray:
    s = float(v.sum())
    return v / s if s > _EPS else np.zeros_like(v)


def _centre_unit(v: np.ndarray) -> np.ndarray:
    """Mean-centre, then L2-normalise. A block with no variation at all — a
    grey image's hue histogram, a uniform packet's layout grid — becomes
    exactly zero here and contributes nothing rather than contributing a
    constant direction shared with every other product on earth."""
    c = v - float(v.mean())
    n = float(np.linalg.norm(c))
    return c / n if n > _EPS else np.zeros_like(c)


def _soft_hist(x: float, lo: float, hi: float, bins: int) -> np.ndarray:
    """Linear-interpolated one-value histogram, so a scalar feature crossing a
    bin edge moves smoothly instead of teleporting."""
    h = np.zeros(bins, dtype=np.float64)
    t = (float(x) - lo) / (hi - lo) * (bins - 1)
    t = min(max(t, 0.0), float(bins - 1))
    i = int(np.floor(t))
    if i >= bins - 1:
        h[bins - 1] = 1.0
        return h
    frac = t - i
    h[i] = 1.0 - frac
    h[i + 1] = frac
    return h


# ------------------------------------------------------------- the ORB engine

def _orb() -> Any:
    """A fresh ORB per call. ORB carries no RNG and no adaptive state, so a
    fresh instance and a shared one give identical bytes; constructing per call
    is simply the cheapest way to be certain no state can leak between crops
    and make the function non-deterministic in a long-running server."""
    return cv2.ORB_create(
        nfeatures=256,
        scaleFactor=1.2,
        nlevels=6,
        edgeThreshold=16,
        firstLevel=0,
        WTA_K=2,
        patchSize=16,
        fastThreshold=8,
    )


def _orb_bits(gray_orb: np.ndarray) -> np.ndarray:
    """Mean of each of the 256 BRIEF bits over all detected keypoints.

    Averaging over keypoints (rather than matching them) is deliberate: it is
    PERMUTATION-INVARIANT, so the vector cannot depend on the order the
    detector happened to emit corners in, and it is fixed-length, which a
    keypoint set is not. ORB's descriptors are orientation-compensated, so the
    average survives the packet being turned. A crop with no corners at all —
    a plain white salt packet — yields no keypoints and this block is zero,
    which is the honest report: ORB saw no texture evidence.
    """
    kps, des = _orb().detectAndCompute(gray_orb, None)
    if des is None or len(des) == 0:
        return np.zeros(ORB_BITS, dtype=np.float64)
    bits = np.unpackbits(np.ascontiguousarray(des, dtype=np.uint8), axis=1)
    return bits.mean(axis=0).astype(np.float64)


# ----------------------------------------------------------------- the blocks

def blocks(crop: Any) -> dict[str, np.ndarray]:
    """Every named block, already mean-centred and unit-L2, before weighting.

    Exposed so tests can ablate a block and MEASURE whether it earns its
    weight, instead of the weights being folklore.
    """
    bgr, src_h, src_w = _as_bgr_u8(crop)

    small = _resize(bgr, CANON)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0].astype(np.float64)          # 0..179
    sat = hsv[:, :, 1].astype(np.float64)          # 0..255
    val = hsv[:, :, 2].astype(np.float64)          # 0..255

    out: dict[str, np.ndarray] = {}

    # -- colour: what the packet is, globally ------------------------------
    # Hue is weighted by saturation AND value so that a grey or black region
    # cannot vote for a hue it does not really have (hue is meaningless at
    # zero saturation, and cvtColor reports 0 there, which reads as "red").
    hue_w = (sat / 255.0) * (val / 255.0)
    hue_bin = np.minimum((hue / 180.0 * HUE_BINS).astype(np.int64), HUE_BINS - 1)
    hh = np.bincount(hue_bin.reshape(-1), weights=hue_w.reshape(-1),
                     minlength=HUE_BINS).astype(np.float64)
    out["hue"] = _centre_unit(_smooth_circular(_l1(hh)))

    sat_bin = np.minimum((sat / 256.0 * SAT_BINS).astype(np.int64), SAT_BINS - 1)
    sh = np.bincount(sat_bin.reshape(-1), minlength=SAT_BINS).astype(np.float64)
    out["sat"] = _centre_unit(_l1(sh))

    # Intensity histogram binned on the RATIO to the crop's own mean, so a
    # global lighting gain cancels exactly instead of sliding every bin.
    vmean = float(val.mean())
    vr = val / vmean if vmean > _EPS else np.zeros_like(val)
    val_bin = np.clip((vr / 2.0 * VAL_BINS).astype(np.int64), 0, VAL_BINS - 1)
    vh = np.bincount(val_bin.reshape(-1), minlength=VAL_BINS).astype(np.float64)
    out["val"] = _centre_unit(_l1(vh))

    # -- colour LAYOUT: where on the packet the colour is -------------------
    # A global histogram cannot tell a red cap on a white tube from a white cap
    # on a red tube when the two areas happen to match. Chromaticity r/(r+g+b)
    # is used rather than raw BGR because it is invariant to a lighting gain.
    f = small.astype(np.float64)
    denom = f.sum(axis=2) + _EPS
    chroma = np.stack([f[:, :, 2] / denom, f[:, :, 1] / denom], axis=2)  # r, g
    out["chroma_grid"] = _centre_unit(_fold(_cell_means(chroma, GRID), _GRID_PAIRS))

    vgrid = _cell_means(val[:, :, None], GRID)
    vgrid = vgrid / vmean if vmean > _EPS else np.zeros_like(vgrid)
    out["value_grid"] = _centre_unit(_fold(vgrid, _GRID_PAIRS))

    # -- edges: print, ribs, text bands, stripes ----------------------------
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    # Orientation modulo pi: a gradient and its negation describe the SAME
    # edge, which is also precisely why this block survives a 180-degree flip.
    ang = np.mod(np.arctan2(gy, gx), np.pi)

    ori_bin = np.minimum((ang / np.pi * ORI_BINS).astype(np.int64), ORI_BINS - 1)
    oh = np.bincount(ori_bin.reshape(-1), weights=mag.reshape(-1),
                     minlength=ORI_BINS).astype(np.float64)
    out["orient"] = _centre_unit(_smooth_circular(_l1(oh)))

    eb = np.minimum((ang / np.pi * EDGE_ORI_BINS).astype(np.int64),
                    EDGE_ORI_BINS - 1)
    step = CANON // EDGE_GRID
    cells = np.zeros((EDGE_GRID * EDGE_GRID, EDGE_ORI_BINS), dtype=np.float64)
    for i in range(EDGE_GRID):
        for j in range(EDGE_GRID):
            y0, y1 = i * step, (i + 1) * step
            x0, x1 = j * step, (j + 1) * step
            sub_b = eb[y0:y1, x0:x1].reshape(-1)
            sub_m = mag[y0:y1, x0:x1].reshape(-1)
            hist = np.bincount(sub_b, weights=sub_m,
                               minlength=EDGE_ORI_BINS).astype(np.float64)
            cells[i * EDGE_GRID + j] = _smooth_circular(_l1(hist))
    out["orient_grid"] = _centre_unit(_fold(cells, _EDGE_PAIRS))

    # -- moments: how the ink mass is distributed ---------------------------
    # Normalised central moments, NOT Hu. Hu's rotation invariance is exactly
    # what we must not have here: horizontal stripes and vertical stripes have
    # identical Hu moments and are different products. Second order is already
    # 180-invariant; the odd third-order terms flip sign under a 180-degree
    # rotation, so their magnitude is taken.
    m = cv2.moments(gray.astype(np.float64))
    out["moments"] = _centre_unit(np.array([
        m["nu20"], m["nu11"], m["nu02"],
        abs(m["nu30"]), abs(m["nu21"]), abs(m["nu12"]), abs(m["nu03"]),
    ], dtype=np.float64))

    # -- aspect: measured on the ORIGINAL crop, which the resize destroyed ---
    long_px = float(max(src_h, src_w))
    short_px = float(min(src_h, src_w))
    aspect_ratio = short_px / long_px if long_px > 0.0 else 1.0
    out["aspect"] = _centre_unit(_soft_hist(aspect_ratio, 0.0, 1.0, ASPECT_BINS))

    # -- scalar texture statistics ------------------------------------------
    edges = cv2.Canny(gray, 60, 160)
    stats = np.array([
        float(np.count_nonzero(edges)) / float(edges.size),
        float(mag.mean()) / 255.0,
        float(val.std()) / (vmean + _EPS),
        float(np.count_nonzero(sat > 40.0)) / float(sat.size),
    ], dtype=np.float64)
    out["stats"] = _centre_unit(stats)

    # -- ORB: local texture, orientation-compensated ------------------------
    gray_orb = _resize(gray, CANON_ORB)
    out["orb"] = _centre_unit(_orb_bits(gray_orb))

    return out


# ------------------------------------------------------------------ the embed

def embed(crop_bgr: Any) -> np.ndarray:
    """Describe one item crop as an L2-normalised float32 vector of EMBED_DIM.

    ``crop_bgr`` is the oriented crop out of the rectified metric buffer. It may
    be (h, w, 3) BGR, (h, w, 4) BGRA or 2-D grey — ``Brain._crop`` returns grey,
    so grey is not an edge case, it is the common case.

    Deterministic: same pixels in, byte-identical vector out, in this process or
    any other. No RNG is consulted anywhere on this path.
    """
    parts = blocks(crop_bgr)
    chunks = [parts[name] * WEIGHTS[name] for name, _ in BLOCK_SPEC]
    v = np.concatenate(chunks)
    if v.shape[0] != EMBED_DIM:  # pragma: no cover - structural guard
        raise EmbedderError(f"assembled {v.shape[0]} dims, expected {EMBED_DIM}")
    n = float(np.linalg.norm(v))
    if n > _EPS:
        v = v / n
    return np.ascontiguousarray(v, dtype=np.float32)


def embed_batch(crops: Any) -> np.ndarray:
    """embed() over an iterable, stacked into (k, EMBED_DIM) float32.

    Convenience for enrolment, which captures several views of one packet.
    """
    rows = [embed(c) for c in crops]
    if not rows:
        raise EmbedderError("no crops supplied")
    return np.ascontiguousarray(np.vstack(rows), dtype=np.float32)
