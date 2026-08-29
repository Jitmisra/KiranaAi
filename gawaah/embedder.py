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
histogram binning, ``Sobel``, ``moments``, ``equalizeHist`` and ``ORB``.
(``AKAZE`` is absent from this build, and ``SIFT`` — though present in this
Python OpenCV — does not exist on the JS side, so ORB is the only local
descriptor that can exist on BOTH halves of the system. That is the reason it
is ORB, not taste. ``Canny`` was used and then measured out; see the stats
block for why.)

This is a CLASSICAL descriptor and it is honest about its ceiling. For an
open-world "what is this object" question it would be a poor answer. The
question here is bounded and different: a shopkeeper enrols perhaps two dozen
kirana packets HIMSELF, on a printed mat, under his own light, and the metric
footprint has already thrown out everything of the wrong SIZE before appearance
is consulted at all. Separating ~24 known wrappers, having already been told
the object is 71 mm long, is a job a colour-layout-texture descriptor can
actually do — and where it cannot, invariant 7 says abstain, and the thresholds
in identity.py do exactly that. This module never decides anything; it only
supplies the direction that theta and phi then judge.

WHAT IT MUST SURVIVE
--------------------
The crop it is handed comes from ``Brain._crop``: the oriented, upright cut of
one placement out of the 840x1188 metric buffer. Three things still vary, and
each one is answered by construction rather than hoped away.

  ROTATION.  ``minAreaRect`` fixes the angle only modulo 180 degrees, so the
             SAME packet arrives upright or upside-down depending on which way
             the shopkeeper set it down. Rather than pick a canonical flip from
             a statistic that can flap between two nearly-tied poses, every
             spatially aware block here is made EXACTLY 180-degree invariant:
             cells are folded onto their 180-degree partners as an unordered
             ``(min, max)`` pair, and gradient orientation is taken modulo 180
             degrees, under which a gradient and its negation are the same edge.
             Residual few-degree error is absorbed by SOFT binning — spatial
             cells are triangular and overlap by one full cell, orientation is
             linearly split between neighbouring bins and then circularly
             smoothed — so a feature drifting across a boundary slides instead
             of teleporting. Hard cells cost 0.8 of a cosine on a banded packet
             shifted by six percent; that is measured, not assumed.
  SCALE.     The crop is resized to a canonical square before anything is
             measured, so crop tightness cannot change what the vector means.
             Aspect ratio is measured from the ORIGINAL crop and carried in its
             own small block, because resizing to a square destroys it, and
             because the footprint gate upstream only constrains the LONG edge:
             two packets 110 mm long and 70 vs 40 mm wide both reach us.
  BRIGHTNESS. Nothing raw-intensity survives. Every intensity feature is a
             RATIO to the crop's own mean — the spatial value grid, the row and
             column profiles, the intensity histogram's bin edges, and all
             three texture statistics — so a global gain cancels in exact
             arithmetic. Colour is carried as chromaticity r/(r+g+b) and as HSV
             hue, not as BGR. The one place a gain does NOT cancel by
             construction is ORB, whose FAST detector fires on an absolute
             intensity step; that is handled by equalising the ORB canvas
             first, which is invariant to any monotonic transform. Measured end
             to end over 292 cases, a gain anywhere from 0.5x to 1.2x costs a
             mean of 0.001 of cosine and never more than 0.038.

GREYSCALE INPUT IS A FIRST-CLASS CASE, NOT A DEGRADATION BUG
-------------------------------------------------------------
``Brain._crop`` returns a 2-D GREY array and ``SimSource`` renders grey frames
on purpose, so the colour blocks are routinely fed an image with no colour at
all. They neither crash nor fabricate. A COLOUR GATE — the crop's own mean
saturation, ramped to zero — multiplies the four colour blocks, so a grey crop
switches them off entirely and the descriptor falls back on layout, edges,
moments and ORB. Without that gate a grey crop's saturation histogram is a
one-hot spike in bin zero, identical for every product on earth, and it drags
every unrelated pair's cosine UP by its whole share of the energy. The gate is
worth about 0.09 of measured cross-product similarity in grey.

Two products that differ ONLY in hue are genuinely the same picture in grey.
This module will report them as similar and it should — measured at 0.997 for a
pair built to be iso-luminant. identity.py then returns ``ambiguous_pair`` or
``below_margin`` and the counter shows amber. That is invariant 7 working, not
the embedder failing.

THE STRUCTURAL TRICK: MEAN-CENTRE EVERY BLOCK
---------------------------------------------
Each block is mean-centred over its own dimensions, then normalised, then scaled
by a weight; only then are they concatenated and L2-normalised as a whole.
Centring is what makes cosine mean anything. An uncentred histogram block
carries a large CONSTANT direction (every histogram sums to one, every grey
chromaticity is a third) shared by every product in the world; leaving it in
floats the similarity between two unrelated packets up towards 0.9 and there is
no room above phi = 0.55 for a margin to exist in. Centred, a block encodes only
DEPARTURE from flat, unrelated products land near zero, and theta has somewhere
to live. The per-block normalisation then makes the weights mean what they say:
block b owns w_b^2 / sum(w^2) of the final energy no matter how many dimensions
it happens to have — so a 256-bit ORB block cannot shout down a 12-bin hue
histogram by sheer length.

"I SAW NO EVIDENCE" IS A REPRESENTABLE STATE
--------------------------------------------
This is the part that took the most iterations to get right, because plain
normalisation destroys it. Three mechanisms defend it, and each one exists
because a measurement said so, not because it seemed prudent:

  the COLOUR GATE   no saturation -> the colour blocks are zero.
  the DETAIL GATE   no luminance contrast -> the layout, edge, moment and ORB
                    blocks are zero. Without it, centring-then-normalising
                    turns sensor noise on a blank packet into a full-length
                    unit vector that votes as loudly as real evidence.
  the BLOCK FLOOR   a block whose centred norm falls below a measured floor
                    shrinks instead of being scaled up. A near-symmetric banded
                    packet had its folded value grid amplified from a norm of
                    0.007 (against a typical 0.26) and a six percent re-crop
                    then flipped that amplified noise to -0.84 against itself.

A block that saw nothing contributes no energy and its share is redistributed
by the final normalisation to the blocks that did see something.

WHAT IT CANNOT DO, MEASURED
---------------------------
tests/test_embedder.py asserts these as limits rather than hiding them. In grey,
two wrappers differing only in hue are indistinguishable (0.997) and must be.
Two blank packets are indistinguishable (1.000) and must be. A near-symmetric
banded packet re-cropped 9% tighter scores 0.54 against itself and falls under
phi. In every one of those cases the correct outcome is the amber line
identity.py produces, and across 1752 measured queries — with the footprint gate
deliberately admitting every enrolled SKU at once — the count of WRONG skus
proposed is zero.

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
    "BLOCK_FLOOR",
    "COLOUR_BLOCKS",
    "DETAIL_BLOCKS",
    "WEIGHTS",
    "WEIGHT_FLOOR",
    "EmbedderError",
]


class EmbedderError(ValueError):
    """A crop this function cannot describe. Distinct from a low similarity:
    a low similarity is a result, this is a bug in the caller."""


# ------------------------------------------------------------------- geometry

CANON = 64          # canonical square for colour / grid / gradient work
CANON_ORB = 128     # ORB needs room: a 31 px patch on a 64 px image is absurd
GRID = 4            # 4x4 soft cells for colour and value layout
EDGE_GRID = 2       # 2x2 soft cells for oriented-edge layout
PROFILE = 16        # samples in the row / column intensity profiles

HUE_BINS = 12
SAT_BINS = 8
VAL_BINS = 8
ORI_BINS = 18       # global gradient orientation, modulo 180 degrees
EDGE_ORI_BINS = 8   # per-cell gradient orientation
CHROMA_ORI_BINS = 12  # orientation of COLOUR boundaries, modulo 180 degrees
ASPECT_BINS = 8
SCALAR_BINS = 5     # soft bins per scalar statistic
ORB_BITS = 256

HUE_SIGMA = 0.8     # circular smoothing, in bins
ORI_SIGMA = 1.2
EDGE_ORI_SIGMA = 0.9

#: Mean saturation (0..255) at and above which colour is trusted completely.
#: Below it the colour blocks are ramped down; at zero — a grey crop — they are
#: exactly zero.
COLOUR_FULL_SAT = 24.0
COLOUR_DEAD_SAT = 3.0    # noise floor: below this there is no colour at all

#: Luminance contrast (std/mean of the grey crop) at and above which the
#: layout-of-brightness blocks are trusted completely, and below which they are
#: ramped to zero. Sensor noise on a genuinely flat packet lands near 0.03.
DETAIL_FULL_CONTRAST = 0.075
DETAIL_DEAD_CONTRAST = 0.025

_EPS = 1e-12

#: Ordered (name, dimension) of every block, in concatenation order.
BLOCK_SPEC: tuple[tuple[str, int], ...] = (
    ("hue", HUE_BINS),
    ("sat", SAT_BINS + SCALAR_BINS),
    ("val", VAL_BINS),
    ("chroma_grid", GRID * GRID * 2),
    ("chroma_orient", CHROMA_ORI_BINS),
    ("value_grid", GRID * GRID),
    ("row_profile", PROFILE),
    ("col_profile", PROFILE),
    ("orient", ORI_BINS),
    ("orient_grid", EDGE_GRID * EDGE_GRID * EDGE_ORI_BINS),
    ("moments", 7),
    ("aspect", ASPECT_BINS),
    ("stats", 3 * SCALAR_BINS),
    ("orb", ORB_BITS),
)

#: Blocks that carry only colour evidence and are therefore multiplied by the
#: colour gate. They vanish on a grey crop.
COLOUR_BLOCKS: frozenset[str] = frozenset({
    "hue", "sat", "chroma_grid", "chroma_orient",
})

#: Blocks that describe the LAYOUT of brightness and are therefore multiplied by
#: the detail gate. They vanish on a crop with no luminance variation, where
#: centring-then-normalising would otherwise blow sensor noise up into a
#: full-length unit vector and let a coin toss dominate the descriptor.
DETAIL_BLOCKS: frozenset[str] = frozenset({
    "value_grid", "row_profile", "col_profile", "orient", "orient_grid",
    "moments", "orb",
})

#: Share of the final vector's ENERGY a block gets is w^2 / sum(w^2).
#: These are not folklore. Each was set by measuring the block's own
#: same-view-versus-different-product separation on the synthetic product set in
#: tests/test_embedder.py, in colour AND in grey, and then by a coordinate
#: sweep on the end-to-end margin, run over THREE independent product families
#: (two synthetic families here plus the catalogue tools/upload_app.py actually
#: ships) and two perturbation ladders — 1992 queries — so they are not fitted
#: to one bag of packets. test_ablation_records_what_each_block_is_worth prints
#: what dropping each one currently costs.
#:
#: THE LESSON THAT SET THEM. An earlier version had `aspect` at 0.85 and `stats`
#: at 0.85 out of a much smaller sum of squares, which handed 27% of the energy
#: to two blocks that AGREE on nearly every pair of packets — aspect is
#: identical for any two products of the same shape, and coarse texture
#: statistics are near-identical for any two flat wrappers. Blocks like that do
#: not discriminate; they raise the similarity FLOOR under every comparison. A
#: yellow packet with a blue band and a purple box with an orange dot scored
#: 0.566 that way, clearing phi=0.55, and the untaught box was named and priced
#: — precisely the confidently-wrong answer invariant 7 exists to prevent. The
#: hue block had it right all along at 0.117 and was simply outvoted. Raising
#: the genuinely discriminative blocks instead cut that pair to 0.488.
#:
#: One weight is set by MECHANISM rather than by the sweep, and the evidence is
#: a named test. `chroma_grid` would be 0.30 on score alone and is 0.60 because
#: it is the ONLY block that can separate a colour LAYOUT pair — with it removed
#: two such packets are identical to 1.0000
#: (test_spatial_colour_grid_is_the_only_block_that_can_do_this). At 0.30 that
#: pair's margin is 0.041 against a theta of 0.10; at 0.60 it is 0.153, and the
#: whole change costs 3 matches in 1992.
WEIGHTS: dict[str, float] = {
    "hue": 1.20,
    "sat": 0.70,
    "val": 0.70,
    "chroma_grid": 0.60,
    "chroma_orient": 0.70,
    "value_grid": 1.20,
    "row_profile": 1.00,
    "col_profile": 0.40,
    "orient": 0.40,
    "orient_grid": 0.70,
    "moments": 0.30,
    "aspect": 0.85,
    "stats": 0.85,
    "orb": 0.30,
}

#: Per-block magnitude floor for _centre_unit: 15% of that block's MEASURED
#: median centred norm across the synthetic product set (both product families,
#: both view splits, colour and grey). Above the floor normalisation is exact;
#: below it a block shrinks rather than amplifying whatever it happened to see.
#: These are measurements, not knobs. test_block_floors_damp_only_the_degenerate
#: _case checks the property that matters — that on ordinary packets the floor
#: does not bite at all (median output norm 1.0 for every block), so it cannot
#: quietly rescale a block out of the energy share WEIGHTS promises it.
BLOCK_FLOOR: dict[str, float] = {
    "hue": 0.059,
    "sat": 0.195,
    "val": 0.092,
    "chroma_grid": 0.077,
    "chroma_orient": 0.037,
    "value_grid": 0.040,
    "row_profile": 0.072,
    "col_profile": 0.013,
    "orient": 0.044,
    "orient_grid": 0.082,
    "moments": 0.142,
    "aspect": 0.125,
    "stats": 0.178,
    "orb": 0.621,
}

#: No block is ever tuned to zero. A weight that reaches this floor is a block
#: the sweep would have deleted, and it is kept anyway, because the sweep can
#: only see the synthetic packets it was shown and a block that is redundant on
#: those is not thereby redundant on a real shelf. tests/test_embedder.py
#: ablates each block and records what it is actually worth.
WEIGHT_FLOOR = 0.30

EMBED_DIM = sum(d for _, d in BLOCK_SPEC)


# -------------------------------------------------------------------- helpers

def _as_bgr_u8(crop: Any) -> tuple[np.ndarray, int, int]:
    """Coerce any accepted crop to a contiguous (h, w, 3) uint8 BGR image.

    A 2-D array is REPLICATED to three channels rather than rejected, because
    that is exactly what ``Brain._crop`` hands us. Replication is the honest
    lift: the hue and saturation of a grey image are genuinely zero, so the
    colour gate reads zero and the colour blocks correctly report "no colour
    evidence" instead of inventing some.
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
        bgr = cv2.cvtColor(np.ascontiguousarray(a[:, :, 0]), cv2.COLOR_GRAY2BGR)
    elif chans == 4:
        bgr = cv2.cvtColor(a, cv2.COLOR_BGRA2BGR)
    else:
        bgr = a
    return np.ascontiguousarray(bgr), int(h), int(w)


def _resize(img: np.ndarray, side: int) -> np.ndarray:
    """Deterministic canonical resize. INTER_AREA down, INTER_LINEAR up — both
    are fixed-kernel and neither consults a random state."""
    h, w = img.shape[:2]
    interp = cv2.INTER_AREA if (h >= side and w >= side) else cv2.INTER_LINEAR
    return cv2.resize(img, (side, side), interpolation=interp)


# -- soft spatial cells -----------------------------------------------------

def _cell_weights(side: int, n: int) -> np.ndarray:
    """(n, side) triangular pooling weights: cell i peaks at its own centre and
    falls linearly to zero at its neighbours' centres.

    Hard boxes were tried first and measured: a packet with two printed bands,
    re-cropped six percent tighter, had its band mass jump from one row of cells
    to the next and the value-grid cosine against its own reference went to
    -0.77. With triangular cells that same perturbation costs almost nothing,
    because a feature crossing a boundary is shared between the two cells rather
    than handed wholesale from one to the other.
    """
    centres = (np.arange(n, dtype=np.float64) + 0.5) * (side / n)
    xs = np.arange(side, dtype=np.float64) + 0.5
    d = np.abs(xs[None, :] - centres[:, None]) / (side / n)
    w = np.clip(1.0 - d, 0.0, None)
    return w / np.maximum(w.sum(axis=1, keepdims=True), _EPS)


_W_GRID = _cell_weights(CANON, GRID)
_W_EDGE = _cell_weights(CANON, EDGE_GRID)
_W_PROF = _cell_weights(CANON, PROFILE)


def _soft_cells(img: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Triangular-pooled cell means. img is (side, side) or (side, side, c);
    returns (n*n, c)."""
    a = img if img.ndim == 3 else img[:, :, None]
    # (n, side) @ (side, side, c) -> (n, side, c) -> (n, n, c)
    pooled = np.einsum("iy,yxc,jx->ijc", w, a, w, optimize=True)
    return pooled.reshape(-1, a.shape[2])


def _fold_pairs(n: int) -> tuple[tuple[int, int], ...]:
    """Index pairs folding an n x n grid onto its 180-degree rotation.

    Cell (i, j) maps to (n-1-i, n-1-j). For even n every cell has a distinct
    partner, so the folded feature holds exactly as many numbers as the grid
    did: each pair contributes its unordered (min, max) instead of its ordered
    (first, second). That is what makes the block EXACTLY invariant to the
    packet being set down the other way round, with no canonical-flip decision
    that could flap between two nearly tied poses.
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


def _line_pairs(n: int) -> tuple[tuple[int, int], ...]:
    """Fold pairs for a 1-D profile: a 180-degree rotation reverses the row
    order and the column order independently, so sample i pairs with n-1-i."""
    return tuple((i, n - 1 - i) for i in range(n // 2))


_GRID_PAIRS = _fold_pairs(GRID)
_EDGE_PAIRS = _fold_pairs(EDGE_GRID)
_PROF_PAIRS = _line_pairs(PROFILE)


def _fold(cells: np.ndarray, pairs: tuple[tuple[int, int], ...]) -> np.ndarray:
    """Fold a (n*n, k) per-cell block into a (n*n*k,) 180-invariant one."""
    a = np.array([cells[i] for i, _ in pairs], dtype=np.float64)
    b = np.array([cells[j] for _, j in pairs], dtype=np.float64)
    return np.stack([np.minimum(a, b), np.maximum(a, b)], axis=1).reshape(-1)


# -- soft circular histograms ------------------------------------------------

def _circ_kernel(bins: int, sigma: float) -> np.ndarray:
    k = np.arange(bins, dtype=np.float64)
    d = np.minimum(k, bins - k)
    g = np.exp(-0.5 * (d / sigma) ** 2)
    return g / g.sum()


_HUE_K = _circ_kernel(HUE_BINS, HUE_SIGMA)
_ORI_K = _circ_kernel(ORI_BINS, ORI_SIGMA)
_EDGE_K = _circ_kernel(EDGE_ORI_BINS, EDGE_ORI_SIGMA)
_CHROMA_ORI_K = _circ_kernel(CHROMA_ORI_BINS, ORI_SIGMA)


def _circ_smooth(h: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """Circular convolution by explicit rolls. Small, exact and deterministic —
    an FFT would be faster and would introduce round-off that differs with the
    library's plan."""
    out = np.zeros_like(h)
    for shift, k in enumerate(kernel):
        if k != 0.0:
            out += k * np.roll(h, shift)
    return out


def _soft_circ_hist(t: np.ndarray, w: np.ndarray, bins: int) -> np.ndarray:
    """Linear-split circular histogram: a value at bin index 3.7 puts 0.3 in
    bin 3 and 0.7 in bin 4, so a small rotation moves mass smoothly instead of
    dropping it across a quantisation edge."""
    i0 = np.floor(t).astype(np.int64)
    frac = t - i0
    i0 = np.mod(i0, bins)
    i1 = np.mod(i0 + 1, bins)
    h = np.bincount(i0, weights=w * (1.0 - frac), minlength=bins)
    h += np.bincount(i1, weights=w * frac, minlength=bins)
    return h.astype(np.float64)


def _soft_hist(x: float, lo: float, hi: float, bins: int) -> np.ndarray:
    """Linear-interpolated histogram of ONE scalar.

    Turning a scalar into a direction is the point: cosine cannot read the
    magnitude of a single dimension, but it reads which way a soft bin pair
    leans perfectly well.
    """
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


def _l1(v: np.ndarray) -> np.ndarray:
    s = float(v.sum())
    return v / s if s > _EPS else np.zeros_like(v)


def _centre_unit(v: np.ndarray, floor: float = 0.0) -> np.ndarray:
    """Mean-centre, then normalise — but never AMPLIFY.

    Centring is what makes cosine mean anything (see the module docstring).
    Dividing by the norm afterwards has a failure mode that is easy to miss and
    expensive when it bites: a block that saw almost no variation gets scaled up
    to a full-length unit vector anyway, so pure noise votes exactly as loudly
    as real evidence. It is not hypothetical — a packet with two printed bands,
    nearly symmetric under the 180-degree fold, left its folded value grid with
    a centred norm of 0.007 against a typical 0.26, and re-cropping it six
    percent tighter flipped that amplified noise to a direction cosine of -0.84
    against its own reference.

    So the divisor is max(norm, floor): above the floor this is exactly L2
    normalisation, below it the block shrinks towards zero in proportion to how
    little it actually saw. Each floor in BLOCK_FLOOR is 15% of that block's
    MEASURED median centred norm over the synthetic set, so the ordinary case
    is untouched and only the near-degenerate one is damped.
    """
    c = v - float(v.mean())
    n = float(np.linalg.norm(c))
    if n <= _EPS:
        return np.zeros_like(c)
    return c / max(n, floor)


# --------------------------------------------------------------- the ORB part

def _orb() -> Any:
    """A fresh ORB per call. ORB carries no RNG and no adaptive state, so a
    fresh instance and a shared one give identical bytes; constructing per call
    is simply the cheapest way to be certain no state can leak between crops in
    a long-running server and make this function non-deterministic."""
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

    Averaging over keypoints rather than matching them is deliberate. It is
    PERMUTATION-INVARIANT, so the vector cannot depend on the order the detector
    happened to emit corners in, and it is fixed length, which a keypoint set is
    not. ORB's descriptors are orientation-compensated, so the average survives
    the packet being turned.

    A crop with no corners at all — a plain two-tone tube with one straight
    edge — yields no keypoints and this block is exactly zero. That is the
    honest report: ORB saw no texture evidence, so it gets no vote, and the
    final normalisation hands its share to the blocks that did see something.
    """
    _, des = _orb().detectAndCompute(gray_orb, None)
    if des is None or len(des) == 0:
        return np.zeros(ORB_BITS, dtype=np.float64)
    bits = np.unpackbits(np.ascontiguousarray(des, dtype=np.uint8), axis=1)
    return bits.mean(axis=0).astype(np.float64)


# ----------------------------------------------------------------- the blocks

def blocks(crop: Any) -> dict[str, np.ndarray]:
    """Every named block, mean-centred, floor-normalised and gated, but NOT yet
    weighted or concatenated.

    Each returned block is a direction of length at most 1. A length BELOW 1
    means the block is reporting reduced confidence in itself — either a gate
    found no colour or no luminance detail in this crop, or the block's own
    centred norm fell under its floor. Exactly 0 means it saw nothing at all.

    Exposed so tests can ablate a block and MEASURE whether it earns its weight,
    instead of the weights being taken on trust.
    """
    bgr, src_h, src_w = _as_bgr_u8(crop)

    small = _resize(bgr, CANON)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0].astype(np.float64)          # 0..179 over the full wheel
    sat = hsv[:, :, 1].astype(np.float64)          # 0..255
    # LUMINANCE, not HSV's V. V is max(B, G, R), and a red print on a yellow
    # wrapper has almost the same V as the wrapper (195 vs 205) while its
    # luminance differs by a hundred levels — measured, the V-based layout
    # blocks on such a packet were reading sensor noise. cvtColor's grey is
    # also exactly what Brain._crop already hands us, so the grey path and the
    # colour path agree by construction rather than by luck.
    lum = gray.astype(np.float64)

    out: dict[str, np.ndarray] = {}

    # -- the two evidence gates ---------------------------------------------
    # Both answer the same question — did this block see anything real? — and
    # both exist because _centre_unit() renormalises whatever it is given, so
    # without them a block with nothing in it returns a full-length unit vector
    # made of noise and votes as loudly as a block that saw the whole packet.
    sat_mean = float(sat.mean())
    gate = (sat_mean - COLOUR_DEAD_SAT) / (COLOUR_FULL_SAT - COLOUR_DEAD_SAT)
    gate = min(max(gate, 0.0), 1.0)

    lum_mean = float(lum.mean())
    contrast = float(lum.std()) / (lum_mean + _EPS)
    detail = (contrast - DETAIL_DEAD_CONTRAST) / (
        DETAIL_FULL_CONTRAST - DETAIL_DEAD_CONTRAST)
    detail = min(max(detail, 0.0), 1.0)

    # -- colour: what the packet is, globally -------------------------------
    # Hue is weighted by saturation AND value so a grey or black region cannot
    # vote for a hue it does not have (hue is undefined at zero saturation and
    # cvtColor reports 0 there, which would read as "red").
    hue_w = ((sat / 255.0) * (hsv[:, :, 2].astype(np.float64) / 255.0)).reshape(-1)
    hue_t = (hue / 180.0 * HUE_BINS).reshape(-1)
    hh = _circ_smooth(_l1(_soft_circ_hist(hue_t, hue_w, HUE_BINS)), _HUE_K)
    out["hue"] = _centre_unit(hh, BLOCK_FLOOR["hue"]) * gate

    sat_bin = np.minimum((sat / 256.0 * SAT_BINS).astype(np.int64), SAT_BINS - 1)
    sh = np.bincount(sat_bin.reshape(-1), minlength=SAT_BINS).astype(np.float64)
    out["sat"] = _centre_unit(np.concatenate([
        _l1(sh),
        _soft_hist(sat_mean / 255.0, 0.0, 0.6, SCALAR_BINS),
    ]), BLOCK_FLOOR["sat"]) * gate

    # Intensity histogram binned on the RATIO to the crop's own mean, so a
    # global lighting gain cancels exactly instead of sliding every bin.
    # Deliberately NOT detail-gated: "this packet is a flat field" is real
    # evidence about the packet, and it is what tells a blank one from a
    # printed one.
    vscale = lum_mean if lum_mean > _EPS else 1.0
    vr = lum / vscale
    val_bin = np.clip((vr / 2.0 * VAL_BINS).astype(np.int64), 0, VAL_BINS - 1)
    vh = np.bincount(val_bin.reshape(-1), minlength=VAL_BINS).astype(np.float64)
    out["val"] = _centre_unit(_l1(vh), BLOCK_FLOOR["val"])

    # -- colour LAYOUT: where on the packet the colour is --------------------
    # A global histogram cannot tell a red cap on a white tube from a white cap
    # on a red tube once the two areas match. Chromaticity r/(r+g+b) is used
    # rather than raw BGR because it is invariant to a lighting gain.
    f = small.astype(np.float64)
    denom = f.sum(axis=2) + _EPS
    chroma = np.stack([f[:, :, 2] / denom, f[:, :, 1] / denom], axis=2)
    out["chroma_grid"] = _centre_unit(
        _fold(_soft_cells(chroma, _W_GRID), _GRID_PAIRS),
        BLOCK_FLOOR["chroma_grid"]) * gate

    # The 180-degree fold has one blind spot, and it is worth naming: a layout
    # that is ANTI-symmetric under 180 degrees — red half against green half —
    # folds to the same unordered pair in every cell, so the folded grid goes
    # constant and then to zero. A red-left/green-right packet and a
    # red-top/green-bottom packet are therefore identical to chroma_grid. They
    # are not identical to the ORIENTATION of their colour boundary: one runs
    # vertically, the other horizontally, and orientation modulo 180 degrees is
    # invariant to the fold precisely because a boundary has no head or tail.
    # Measured on an iso-luminance pair built to defeat every other block —
    # red-left/green-right against red-top/green-bottom — chroma_grid agrees at
    # +0.98 (it is blind, exactly as described above) while this block
    # disagrees at -0.53, and the two vectors go from 0.998 apart-from-identical
    # to 0.839.
    ch_hist = np.zeros(CHROMA_ORI_BINS, dtype=np.float64)
    for c in range(chroma.shape[2]):
        plane = np.ascontiguousarray(chroma[:, :, c])
        cgx = cv2.Sobel(plane, cv2.CV_64F, 1, 0, ksize=3)
        cgy = cv2.Sobel(plane, cv2.CV_64F, 0, 1, ksize=3)
        cmag = np.sqrt(cgx * cgx + cgy * cgy)
        cang = np.mod(np.arctan2(cgy, cgx), np.pi)
        ch_hist += _soft_circ_hist(
            (cang / np.pi * CHROMA_ORI_BINS).reshape(-1),
            cmag.reshape(-1), CHROMA_ORI_BINS)
    out["chroma_orient"] = _centre_unit(
        _circ_smooth(_l1(ch_hist), _CHROMA_ORI_K),
        BLOCK_FLOOR["chroma_orient"]) * gate

    vgrid = _soft_cells(lum, _W_GRID) / vscale
    out["value_grid"] = _centre_unit(
        _fold(vgrid, _GRID_PAIRS), BLOCK_FLOOR["value_grid"]) * detail

    # -- 1-D profiles: how MANY bands, not just where the dark half is --------
    # A 4x4 grid cannot count. One dark band across the middle of a biscuit
    # packet and four alternating stripes on a soap wrapper look the same to it
    # once they are folded, and in grey they were measured at 0.90 cosine. A 16
    # sample row profile separates them, costs 32 dimensions and one matrix
    # multiply, and folds against its own reverse so it stays exactly
    # 180-degree invariant.
    row_prof = (_W_PROF @ lum.mean(axis=1)) / vscale
    col_prof = (_W_PROF @ lum.mean(axis=0)) / vscale
    out["row_profile"] = _centre_unit(
        _fold(row_prof.reshape(-1, 1), _PROF_PAIRS),
        BLOCK_FLOOR["row_profile"]) * detail
    out["col_profile"] = _centre_unit(
        _fold(col_prof.reshape(-1, 1), _PROF_PAIRS),
        BLOCK_FLOOR["col_profile"]) * detail

    # -- edges: print, ribs, text bands, stripes -----------------------------
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    # Orientation modulo pi: a gradient and its negation describe the SAME edge,
    # which is also precisely why this block survives a 180-degree flip.
    ang = np.mod(np.arctan2(gy, gx), np.pi)

    ori_t = (ang / np.pi * ORI_BINS).reshape(-1)
    oh = _soft_circ_hist(ori_t, mag.reshape(-1), ORI_BINS)
    out["orient"] = _centre_unit(
        _circ_smooth(_l1(oh), _ORI_K), BLOCK_FLOOR["orient"]) * detail

    et = (ang / np.pi * EDGE_ORI_BINS)
    cells = np.zeros((EDGE_GRID * EDGE_GRID, EDGE_ORI_BINS), dtype=np.float64)
    for i in range(EDGE_GRID):
        for j in range(EDGE_GRID):
            # Triangular cell weights again, so an edge near a cell boundary is
            # shared rather than assigned.
            wy = _W_EDGE[i][:, None]
            wx = _W_EDGE[j][None, :]
            wgt = (wy * wx) * mag
            hist = _soft_circ_hist(et.reshape(-1), wgt.reshape(-1), EDGE_ORI_BINS)
            cells[i * EDGE_GRID + j] = _circ_smooth(_l1(hist), _EDGE_K)
    out["orient_grid"] = _centre_unit(
        _fold(cells, _EDGE_PAIRS), BLOCK_FLOOR["orient_grid"]) * detail

    # -- moments: how the INK mass is distributed ----------------------------
    # Moments of the gradient magnitude, not of the intensity. Intensity
    # moments are swamped by the geometry of the canonical square — measured,
    # they separated nothing at all: mean cosine 1.000 within a product and
    # 0.994 between different products. The gradient map removes the constant
    # background and leaves only where the print is.
    #
    # Normalised central moments, NOT Hu. Hu's rotation invariance is exactly
    # what must not be here: horizontal stripes and vertical stripes have
    # identical Hu moments and are different products at different prices.
    # Second order is already 180-degree invariant; the odd third-order terms
    # flip sign under a 180-degree rotation, so their magnitude is taken.
    m = cv2.moments(np.ascontiguousarray(mag))
    spread = m["nu20"] + m["nu02"] + _EPS
    odd = spread ** 1.5
    out["moments"] = _centre_unit(np.array([
        (m["nu20"] - m["nu02"]) / spread,       # elongation of the ink mass
        2.0 * m["nu11"] / spread,               # its diagonal lean
        spread * 12.0 - 1.0,                    # spread against uniform
        3.0 * abs(m["nu30"]) / odd,
        3.0 * abs(m["nu21"]) / odd,
        3.0 * abs(m["nu12"]) / odd,
        3.0 * abs(m["nu03"]) / odd,
    ], dtype=np.float64), BLOCK_FLOOR["moments"]) * detail

    # -- aspect: measured on the ORIGINAL crop, which the resize destroyed ----
    long_px = float(max(src_h, src_w))
    short_px = float(min(src_h, src_w))
    aspect_ratio = short_px / long_px if long_px > 0.0 else 1.0
    out["aspect"] = _centre_unit(
        _soft_hist(aspect_ratio, 0.0, 1.0, ASPECT_BINS), BLOCK_FLOOR["aspect"])

    # -- scalar texture statistics, each turned into a direction -------------
    # All three are ratios to the crop's own level, so a lighting gain cancels,
    # and all three are built from the Sobel magnitude, which a 180-degree
    # rotation maps to itself exactly.
    #
    # cv2.Canny was here first and was measured out. Two defects, both real:
    # its thresholds are ABSOLUTE, so dimming a packet by a third deletes edges
    # and moves the statistic when nothing about the packet changed; and its
    # non-maximum suppression and hysteresis break ties directionally, so a
    # packet and the same packet turned around do not produce the same edge
    # count — it cost 0.04 of block cosine on a rotation that is otherwise
    # invariant to the last bit. A relative threshold on the Sobel magnitude
    # measures the same thing without either flaw.
    rel = mag / (lum_mean + _EPS)
    out["stats"] = _centre_unit(np.concatenate([
        _soft_hist(float(np.count_nonzero(rel > 0.30)) / float(rel.size),
                   0.0, 0.5, SCALAR_BINS),
        _soft_hist(float(rel.mean()), 0.0, 1.0, SCALAR_BINS),
        _soft_hist(contrast, 0.0, 0.8, SCALAR_BINS),
    ]), BLOCK_FLOOR["stats"])

    # -- ORB: local texture, orientation-compensated -------------------------
    # equalizeHist FIRST, and it is not cosmetic. ORB's FAST detector fires on
    # an ABSOLUTE intensity difference (fastThreshold=8 counts raw levels), so
    # on a low-contrast wrapper a lighting change walks the corner set straight
    # through that threshold: measured on a packet whose grey levels span 20
    # of 255, a x1.2 gain took the ORB block's direction to 0.55 against
    # itself, and a x0.6 gain found NO keypoints at all. Histogram equalisation
    # is invariant to any monotonic intensity transform — which is what a
    # lighting gain, a gamma and an exposure shift all are — so it converts
    # that absolute threshold into an effectively rank-based one. The same
    # packet under the same two gains then holds 1.00 and 0.98.
    # ORB is detail-gated for the same reason equalizeHist made necessary:
    # equalising a crop that has almost no dynamic range STRETCHES ITS NOISE
    # into what looks like texture, and ORB then dutifully describes the noise.
    # Measured on a flat iso-luminance packet, that put the ORB block at 0.94
    # against a packet it should have been completely blind to. The gate says
    # what is true — there was no texture to see.
    out["orb"] = _centre_unit(
        _orb_bits(cv2.equalizeHist(_resize(gray, CANON_ORB))),
        BLOCK_FLOOR["orb"]) * detail

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
    v = np.concatenate([parts[name] * WEIGHTS[name] for name, _ in BLOCK_SPEC])
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
