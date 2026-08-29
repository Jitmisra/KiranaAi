#!/usr/bin/env python3
"""BENCH: a held-out evaluation of the photo-enrol -> recognise loop.

    ./.venv/bin/python tools/bench_recognise.py            # full run, writes results/RECOGNISE.md
    ./.venv/bin/python tools/bench_recognise.py --quick    # small run, no file written
    ./.venv/bin/python tools/bench_recognise.py --json results/recognise.json

WHAT THIS MEASURES, AND WHY IT IS SHAPED LIKE THIS
==================================================

The product claim is: the shopkeeper photographs a packet once, types a name and
a price, and from then on the counter names and prices that packet. This file
tries to find out whether that is true, and it is written to make a flattering
answer hard to produce.

It drives the REAL loop, not a re-implementation of it:

    render a mat scene -> warp it through a tilted camera -> add sensor noise
      -> gawaah.takhti.PlaneEngine.detect / .rectify      (the 840x1188 buffer)
      -> gawaah.placement.PlacementDetector.update        (millimetres)
      -> tools.upload_app.oriented_crop_bgr               (the enrol desk's crop)
      -> gawaah.embedder.embed                            (the classical descriptor)
      -> gawaah.shop_store.ShopStore                      (written to disk, reopened)
      -> gawaah.recogniser.Recogniser                     (Identifier + the price)

Only the SCENE is synthetic. Everything from the lock onwards is the shipped
code path at the shipped thresholds.

THE THREE NUMBERS, AND WHY THEY ARE THREE
-----------------------------------------
A single "accuracy" figure is the easiest thing in this repo to lie with,
because a recogniser can buy accuracy by abstaining on everything it finds hard,
and it can buy it a second time by never being asked about products it was not
taught. So the headline is three numbers that cannot be traded against each
other without the trade being visible:

  top-1 accuracy on DECIDED items   correct names / items it agreed to name
  abstention rate                   items it refused to name / all items
  FALSE-PRICE RATE                  items it named WRONGLY / all items

The third is the one that costs a shopkeeper money, so it is reported on its own
and it is reported even when it is bad. A false price is any confident answer
that is not the truth, and that INCLUDES naming a product that was never taught:
an untaught packet billed as Parle-G is exactly the failure invariant 7 exists
to prevent, so untaught items are evaluated alongside taught ones rather than
left out of the denominator.

HELD OUT: HOW DISJOINTNESS IS GUARANTEED
----------------------------------------
Enrolment sees exactly ONE view of each product. Evaluation sees six OTHER
views. The disjointness is structural, and `disjointness()` re-proves all five
of these on every run rather than trusting the design:

  1. the enrolment view is the only view with rot_deg == 0, dx == dy == 0,
     gain == 100% and crop error == 0. Every evaluation view differs from it in
     rotation AND position AND illumination-or-crop.
  2. the noise seeds live in disjoint integer ranges (enrol 1000-1999,
     eval 2000-2999), so no evaluation frame can share an exposure with an
     enrolment frame.
  3. the enrolment scene holds ONE product; evaluation scenes hold two, so the
     mat content differs even before the pose does.
  4. the set of view parameter tuples is checked for intersection.
  5. the strongest one: every crop's bytes are SHA-256'd, and the enrolment
     hash set is checked to be disjoint from the evaluation hash set. Not one
     evaluated pixel buffer is a buffer the gallery was built from.

WHAT THIS IS NOT
----------------
The products are RENDERED, not photographed. Real packaging has specular
highlights, crushed corners, printed fine text, shadows and motion blur, and
none of that is here. Every number below is an UPPER BOUND on real-shelf
behaviour, not a prediction of it. The mat lock, the millimetres, the
descriptor, the gates, the store and the totals are real; the packets are not.

Nothing in this file settles money (invariant 2). It reads prices out of a
catalog to check that the right integer paise came back with the right name, and
it mints nothing, signs nothing and pays nothing.

A note on the `_frac` field names: tools/lint_no_float.py refuses floats
reaching money-named identifiers, and "false_price_rate" matches its money-name
rule. `_frac` is the lint's own documented suffix for a dimensional quantity, so
`false_price_frac` is the honest spelling of "a fraction, not a sum of money".
The reports print it as "false-price rate".
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gawaah.embedder import EMBED_DIM, embed  # noqa: E402
from gawaah.identity import (  # noqa: E402
    DEFAULT_PHI,
    DEFAULT_TAU_MM,
    DEFAULT_THETA,
    Gallery,
    Identifier,
    REASON_MATCH,
)
from gawaah.placement import PlacementDetector  # noqa: E402
from gawaah.recogniser import Recogniser  # noqa: E402
from gawaah.shop_store import ShopStore  # noqa: E402
from gawaah.takhti import (  # noqa: E402
    BUF_H,
    BUF_W,
    MAT_H_MM,
    MAT_W_MM,
    PlaneEngine,
    render_takhti,
)

__all__ = [
    "BenchProduct",
    "View",
    "Capture",
    "Outcome",
    "Metrics",
    "BenchResult",
    "PRODUCTS",
    "HARD_PAIRS",
    "ENROL_VIEW",
    "EVAL_VIEWS",
    "RENDER_PX_PER_MM",
    "render_product",
    "scene_frames",
    "capture_all",
    "disjointness",
    "enrol",
    "evaluate",
    "score",
    "confusion",
    "cosine_split",
    "overlap",
    "hard_pair_report",
    "gate_sweep",
    "gate_provenance",
    "run",
    "render_markdown",
    "render_html",
    "main",
]


class BenchError(RuntimeError):
    """The bench could not measure what it was asked to measure. Distinct from
    a bad score: a bad score is a result, this is a broken harness."""


# --------------------------------------------------------------------- optics

RENDER_PX_PER_MM = 4.0      # scene render scale, downsampled to 2.83 by rectify
TILT_FRAC = 0.02            # inside takhti's 8-degree perspective gate
NOISE_SIGMA = 4.0           # sensor noise, grey levels
SETTLE_FRAMES = 6           # PlacementDetector frames before a measurement is read
MATCH_TOL_MM = 8.0          # how close a measured centre must be to its truth

#: Where products are put down. Chosen so that (a) no product's bounding circle
#: can touch a printed ArUco marker, which would break the lock, and (b) the two
#: slots are 178 mm apart, further than the sum of the two largest bounding
#: radii (71 + 71), so two items can never merge into one contour.
EVAL_SLOTS: tuple[tuple[float, float], ...] = ((95.0, 130.0), (148.0, 300.0))

#: One product, alone, in the middle of the mat: what the enrol desk asks for.
ENROL_SLOT: tuple[float, float] = (MAT_W_MM / 2.0, MAT_H_MM / 2.0)


# ------------------------------------------------------------------- products

@dataclass(frozen=True)
class BenchProduct:
    """One synthetic packet. `taught` False means it is deliberately never
    enrolled — the open set, which is where recognisers actually fail."""

    sku_id: str
    name: str
    w_mm: float
    h_mm: float
    price_paise: int
    body: tuple[int, int, int]        # BGR
    accent: tuple[int, int, int]      # BGR
    layout: str
    taught: bool = True
    note: str = ""

    @property
    def long_edge_mm(self) -> float:
        return max(self.w_mm, self.h_mm)

    @property
    def short_edge_mm(self) -> float:
        return min(self.w_mm, self.h_mm)


#: 15 taught products in three footprint families plus two singletons, and 3
#: untaught intruders. The families exist so the metric tiebreak CANNOT do the
#: work: five products share a 95 mm long edge, four share 70 mm and four share
#: 38 mm, all well inside tau_mm = 4.0 of each other, so within a family the
#: footprint filter admits every sibling and appearance has to decide.
_TAUGHT: tuple[BenchProduct, ...] = (
    # ---- family A: 60 x 95 mm ------------------------------------------------
    BenchProduct("parle_glucose", "Parle-G glucose 100g", 60.0, 95.0, 1000,
                 (60, 190, 235), (110, 60, 35), "cap_top",
                 note="family A reference packet"),
    BenchProduct("jeera_glucose", "Jeera glucose 100g", 60.0, 95.0, 1200,
                 (60, 190, 235), (110, 60, 35), "cap_bottom",
                 note="180-degree twin of parle_glucose; MUST collide"),
    BenchProduct("krack_jack", "Krack Jack 100g", 60.0, 95.0, 1500,
                 (60, 190, 235), (110, 60, 35), "dot",
                 note="same size, same two colours as parle_glucose, other layout"),
    BenchProduct("monaco_salted", "Monaco salted 100g", 60.0, 95.0, 1400,
                 (55, 55, 200), (240, 240, 240), "cap_top",
                 note="same size and same layout as parle_glucose, other colour"),
    BenchProduct("hide_seek", "Hide & Seek 100g", 60.0, 95.0, 3000,
                 (45, 40, 60), (30, 140, 240), "band_diag",
                 note="family A, dark body"),
    # ---- family B: 45 x 70 mm ------------------------------------------------
    BenchProduct("lifebuoy_red", "Lifebuoy soap 125g", 45.0, 70.0, 3500,
                 (55, 55, 200), (240, 240, 240), "band_diag"),
    BenchProduct("lux_rose", "Lux rose soap 125g", 45.0, 70.0, 4000,
                 (150, 110, 225), (240, 240, 240), "band_diag",
                 note="same size and layout as lifebuoy_red, other colour"),
    BenchProduct("medimix_green", "Medimix soap 125g", 45.0, 70.0, 4500,
                 (85, 140, 60), (240, 240, 240), "band_diag"),
    BenchProduct("chandrika_bar", "Chandrika soap 125g", 45.0, 70.0, 3800,
                 (85, 140, 60), (240, 240, 240), "vstripe",
                 note="same size and same colours as medimix_green, other layout"),
    # ---- family C: 38 x 38 mm sachets ---------------------------------------
    BenchProduct("clinic_sachet", "Clinic shampoo sachet", 38.0, 38.0, 300,
                 (85, 160, 65), (245, 245, 245), "dot"),
    BenchProduct("sunsilk_sachet", "Sunsilk shampoo sachet", 38.0, 38.0, 300,
                 (60, 205, 240), (245, 245, 245), "dot",
                 note="same size and layout as clinic_sachet, other colour"),
    BenchProduct("chik_sachet", "Chik shampoo sachet", 38.0, 38.0, 200,
                 (40, 40, 45), (40, 200, 235), "ring"),
    BenchProduct("vatika_sachet", "Vatika shampoo sachet", 38.0, 38.0, 400,
                 (85, 160, 65), (245, 245, 245), "ring",
                 note="same size and same colours as clinic_sachet, other layout"),
    # ---- singletons: sizes nothing else shares ------------------------------
    BenchProduct("maggi_noodles", "Maggi noodles 70g", 105.0, 80.0, 1400,
                 (40, 190, 235), (40, 40, 190), "checks",
                 note="only product at 105 mm"),
    BenchProduct("tata_salt", "Tata salt 1kg", 75.0, 120.0, 2800,
                 (235, 235, 235), (150, 60, 40), "band_mid",
                 note="only product at 120 mm"),
)

#: NEVER enrolled. Two of them share a footprint with a taught family on
#: purpose: an intruder of an unusual size is refused by the tape measure alone
#: and proves nothing about recognition.
_UNTAUGHT: tuple[BenchProduct, ...] = (
    BenchProduct("intruder_masala", "Chai masala box (never taught)",
                 60.0, 95.0, 0, (150, 60, 130), (40, 170, 240), "dot",
                 taught=False, note="family A footprint, unrelated palette"),
    BenchProduct("intruder_lookalike", "Unknown yellow packet (never taught)",
                 60.0, 95.0, 0, (60, 190, 235), (110, 60, 35), "band_mid",
                 taught=False,
                 note="family A footprint AND family A palette: the open-set case"),
    BenchProduct("intruder_sachet", "Unknown green sachet (never taught)",
                 38.0, 38.0, 0, (85, 160, 65), (245, 245, 245), "vstripe",
                 taught=False, note="sachet footprint AND clinic_sachet palette"),
)

PRODUCTS: tuple[BenchProduct, ...] = _TAUGHT + _UNTAUGHT
PRODUCT_BY_ID: dict[str, BenchProduct] = {p.sku_id: p for p in PRODUCTS}

MUST_COLLIDE = "must_collide"
SHOULD_SEPARATE = "should_separate"

#: The pairs this bench exists to be honest about. `expect` is what the DESIGN
#: says should happen, not what the descriptor is hoped to do; the report states
#: the measured outcome next to it either way.
HARD_PAIRS: tuple[tuple[str, str, str, str], ...] = (
    ("parle_glucose", "jeera_glucose", MUST_COLLIDE,
     "180-degree twin: same size, same palette, cap at the other end. Placement "
     "reports angle in [0,180), so these two produce the same crop turned "
     "half a turn. A descriptor that separated them would report a different "
     "product depending on which way up the packet was put down."),
    ("parle_glucose", "krack_jack", SHOULD_SEPARATE,
     "same size, same two colours, different layout (cap vs centre dot)"),
    ("parle_glucose", "monaco_salted", SHOULD_SEPARATE,
     "same size, same layout, different colour (yellow/navy vs red/white)"),
    ("lifebuoy_red", "lux_rose", SHOULD_SEPARATE,
     "same size, same layout, different colour (red vs rose, both with a white "
     "diagonal)"),
    ("medimix_green", "chandrika_bar", SHOULD_SEPARATE,
     "same size, same two colours, diagonal band vs vertical stripe"),
    ("clinic_sachet", "sunsilk_sachet", SHOULD_SEPARATE,
     "same size, same layout, different colour (green vs yellow sachet)"),
    ("clinic_sachet", "vatika_sachet", SHOULD_SEPARATE,
     "same size, same two colours, dot vs ring"),
    ("intruder_lookalike", "parle_glucose", SHOULD_SEPARATE,
     "an UNTAUGHT packet with family A's exact palette and footprint: the open "
     "set. Separation here means the untaught one is refused, not named."),
)


# -------------------------------------------------------------------- renderer

def _rect(img: np.ndarray, x0: float, y0: float, x1: float, y1: float,
          colour: tuple[int, int, int]) -> None:
    h, w = img.shape[:2]
    img[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)] = colour


def _cap_rows(h: int) -> int:
    return int(h * 0.28)


def render_product(p: BenchProduct, px_per_mm: float = RENDER_PX_PER_MM) -> np.ndarray:
    """One product as a flat BGR patch of its true millimetre size.

    Features are deliberately CHUNKY. The scene is rendered at 4 px/mm, warped
    through a camera, noised, and rectified back to 2.83 px/mm; fine print does
    not survive that round trip, and a descriptor scored on detail the pipeline
    destroys would look excellent here and fail on a shelf.
    """
    w = max(4, int(round(p.w_mm * px_per_mm)))
    h = max(4, int(round(p.h_mm * px_per_mm)))
    img = np.zeros((h, w, 3), np.uint8)
    img[:, :] = p.body
    a = p.accent
    short = min(w, h)
    lay = p.layout

    if lay == "cap_top":
        img[:_cap_rows(h), :] = a
    elif lay == "cap_bottom":
        # h - rows, not int(0.72 * h). Those differ by one row on a 380 px
        # patch, and one row is enough that cap_bottom is not EXACTLY cap_top
        # turned upside down — which would quietly falsify the whole
        # must-collide argument, since that argument is geometric.
        img[h - _cap_rows(h):, :] = a
    elif lay == "band_mid":
        _rect(img, 0.0, 0.38, 1.0, 0.62, a)
    elif lay == "vstripe":
        _rect(img, 0.38, 0.0, 0.62, 1.0, a)
    elif lay == "band_diag":
        cv2.line(img, (0, h), (w, 0), a, max(3, int(short * 0.22)), cv2.LINE_AA)
    elif lay == "dot":
        cv2.circle(img, (w // 2, h // 2), max(3, int(short * 0.30)), a, -1,
                   cv2.LINE_AA)
    elif lay == "ring":
        cv2.circle(img, (w // 2, h // 2), max(4, int(short * 0.34)), a,
                   max(2, int(short * 0.13)), cv2.LINE_AA)
    elif lay == "checks":
        n = 4
        for iy in range(n):
            for ix in range(n):
                if (ix + iy) % 2 == 0:
                    continue
                _rect(img, ix / n, iy / n, (ix + 1) / n, (iy + 1) / n, a)
    else:  # pragma: no cover - guarded by test_every_layout_renders_something
        raise BenchError(f"unknown layout {lay!r}")

    # A dark rim. Real packets have an edge, and it gives the segmenter a clean
    # boundary so the measured millimetres are the packet's, not a halo's.
    cv2.rectangle(img, (0, 0), (w - 1, h - 1), (35, 35, 40),
                  max(1, int(px_per_mm)))
    return img


def _paste_rotated(scene: np.ndarray, patch: np.ndarray,
                   cx_px: float, cy_px: float, rot_deg: float) -> None:
    """Paste `patch` into `scene` centred at (cx, cy), rotated, in place."""
    h, w = patch.shape[:2]
    side = int(np.ceil(np.hypot(w, h))) + 4
    canvas = np.zeros((side, side, 3), np.uint8)
    mask = np.zeros((side, side), np.uint8)
    y0, x0 = (side - h) // 2, (side - w) // 2
    canvas[y0:y0 + h, x0:x0 + w] = patch
    mask[y0:y0 + h, x0:x0 + w] = 255

    m = cv2.getRotationMatrix2D((side / 2.0, side / 2.0), rot_deg, 1.0)
    canvas = cv2.warpAffine(canvas, m, (side, side), flags=cv2.INTER_LINEAR)
    mask = cv2.warpAffine(mask, m, (side, side), flags=cv2.INTER_NEAREST)

    tx = int(round(cx_px - side / 2.0))
    ty = int(round(cy_px - side / 2.0))
    sx0, sy0 = max(0, tx), max(0, ty)
    sx1 = min(scene.shape[1], tx + side)
    sy1 = min(scene.shape[0], ty + side)
    if sx1 <= sx0 or sy1 <= sy0:  # pragma: no cover - slots keep items on the mat
        raise BenchError("a product was placed entirely off the mat")
    sub = canvas[sy0 - ty:sy1 - ty, sx0 - tx:sx1 - tx]
    sub_m = mask[sy0 - ty:sy1 - ty, sx0 - tx:sx1 - tx].astype(bool)
    scene[sy0:sy1, sx0:sx1][sub_m] = sub[sub_m]


def _warp_like_a_camera(mat: np.ndarray, tilt: float = TILT_FRAC) -> np.ndarray:
    h, w = mat.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    d = w * tilt
    dst = np.float32([[d, d * 0.6], [w - d * 0.4, 0], [w, h - d * 0.5], [d * 0.3, h]])
    return cv2.warpPerspective(mat, cv2.getPerspectiveTransform(src, dst), (w, h),
                               borderValue=(235, 235, 235))


_BASE_MAT: Optional[np.ndarray] = None


def _base_mat() -> np.ndarray:
    global _BASE_MAT
    if _BASE_MAT is None:
        _BASE_MAT = cv2.cvtColor(render_takhti(RENDER_PX_PER_MM),
                                 cv2.COLOR_GRAY2BGR)
    return _BASE_MAT


def _shoot(img: np.ndarray, seed: int, gain_pct: int) -> np.ndarray:
    """One exposure: tilt, illuminate, noise. Separate seed = separate frame."""
    out = _warp_like_a_camera(img).astype(np.float32)
    if gain_pct != 100:
        out = out * (gain_pct / 100.0)
    noise = np.random.default_rng(int(seed) & 0xFFFFFFFF).normal(
        0.0, NOISE_SIGMA, out.shape)
    return np.clip(out + noise, 0.0, 255.0).astype(np.uint8)


# ----------------------------------------------------------------------- views

@dataclass(frozen=True)
class View:
    """One way of putting a product down and photographing it.

    Every generative parameter is here, and `key()` returns all of them. Two
    views are the same view exactly when their keys are equal, so disjointness
    is a set operation and not an opinion.
    """

    view_id: str
    rot_deg: float
    dx_mm: float
    dy_mm: float
    gain_pct: int         # illumination, 100 = as enrolled
    crop_pct: int         # measurement/crop error, 0 = none
    noise_seed: int
    stress: str

    def key(self) -> tuple:
        return (self.rot_deg, self.dx_mm, self.dy_mm, self.gain_pct,
                self.crop_pct, self.noise_seed)


#: The one view the gallery is built from. Alone, mid-mat, square on, nominal
#: light, no measurement error: exactly what tools/upload_app.py's enrol page
#: asks a shopkeeper for.
ENROL_VIEW = View("E0", 0.0, 0.0, 0.0, 100, 0, 1001,
                  "enrolment: alone, mid-mat, square on, nominal light")

#: Six held-out views. Each differs from ENROL_VIEW in rotation AND position AND
#: exposure seed, and most also in illumination or crop.
EVAL_VIEWS: tuple[View, ...] = (
    View("V1", 23.0, -9.0, 11.0, 100, 0, 2001, "moved, rotated +23 deg"),
    View("V2", -37.0, 14.0, -8.0, 100, 0, 2002, "moved, rotated -37 deg"),
    View("V3", 61.0, 6.0, 17.0, 78, 0, 2003, "rotated +61 deg, dim light 0.78x"),
    View("V4", -14.0, -12.0, -15.0, 122, 0, 2004, "rotated -14 deg, bright 1.22x"),
    View("V5", 90.0, 10.0, 9.0, 100, 3, 2005, "rotated 90 deg, crop 3% loose"),
    View("V6", 45.0, -15.0, -11.0, 90, -3, 2006,
         "rotated 45 deg, dim 0.90x, crop 3% tight"),
)


def scene_frames(items: Sequence[tuple[BenchProduct, float, float, float]],
                 view: View) -> tuple[np.ndarray, np.ndarray]:
    """(loaded, empty) — two SEPARATE exposures of the same mat under the same
    light, one with the products on it and one without.

    The empty frame is a separate exposure with its own noise, never a copy of
    the loaded one with the objects erased: sharing the noise would hand the
    segmenter a reference more perfect than any photograph, and hide exactly the
    sensor noise it has to eat. It is shot at the SAME illumination as the
    loaded frame because that is what PlacementDetector's slow reference
    maintenance converges to on a counter whose lamp changed — a reference from
    a different exposure would report the whole mat as one enormous object.
    """
    base = _base_mat()
    loaded = base.copy()
    px = RENDER_PX_PER_MM
    for p, cx_mm, cy_mm, rot in items:
        _paste_rotated(loaded, render_product(p), cx_mm * px, cy_mm * px, rot)
    return (_shoot(loaded, view.noise_seed, view.gain_pct),
            _shoot(base, view.noise_seed + 500, view.gain_pct))


# --------------------------------------------------------------------- capture

@dataclass
class _Scaled:
    """A placement whose measured edges are off by crop_pct.

    A real segmenter's box is never exact, and the error must move BOTH the
    crop and the millimetres that are reported to the footprint gate — a crop
    that is 3% loose while the reported measurement stays perfect would be a
    stress test of nothing.
    """

    centre_mm: tuple[float, float]
    long_edge_mm: float
    short_edge_mm: float
    angle_deg: float


def _scaled(placement: Any, crop_pct: int) -> Any:
    if crop_pct == 0:
        return placement
    k = 1.0 + crop_pct / 100.0
    return _Scaled(
        centre_mm=(float(placement.centre_mm[0]), float(placement.centre_mm[1])),
        long_edge_mm=float(placement.long_edge_mm) * k,
        short_edge_mm=float(placement.short_edge_mm) * k,
        angle_deg=float(placement.angle_deg),
    )


def _local_oriented_crop_bgr(rect: np.ndarray, placement: Any) -> np.ndarray:
    """Brain._crop's geometry with the colour kept. Kept here as a fallback so
    a broken tools/upload_app.py cannot silently stop this bench measuring; the
    report always names which of the two was used."""
    from gawaah.takhti import PX_PER_MM_X, PX_PER_MM_Y

    cx = float(placement.centre_mm[0]) * PX_PER_MM_X
    cy = float(placement.centre_mm[1]) * PX_PER_MM_Y
    w = max(2, int(round(float(placement.long_edge_mm or 0.0) * PX_PER_MM_X)))
    h = max(2, int(round(float(placement.short_edge_mm or 0.0) * PX_PER_MM_Y)))
    angle = float(placement.angle_deg or 0.0)
    src = rect if rect.ndim == 3 else cv2.cvtColor(rect, cv2.COLOR_GRAY2BGR)
    if abs(angle) < 1e-6 or abs(angle - 180.0) < 1e-6:
        rot = src
    else:
        m = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)
        rot = cv2.warpAffine(src, m, (BUF_W, BUF_H), flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REPLICATE)
    return cv2.getRectSubPix(rot, (min(w, BUF_W), min(h, BUF_H)), (cx, cy))


def crop_fns() -> tuple[Callable[..., np.ndarray], Callable[..., np.ndarray], str]:
    """(colour_crop, grey_crop, provenance).

    The colour crop is the enrol desk's own `oriented_crop_bgr`; the grey crop is
    `Brain._crop`, which is what the LIVE loop feeds the embedder today. Both are
    imported, never copied, so this bench cannot drift from the shipped path —
    with a named fallback if the desk module fails to import.
    """
    src = "tools.upload_app.oriented_crop_bgr"
    try:
        from tools.upload_app import oriented_crop_bgr as colour
    except Exception as exc:  # pragma: no cover - upload_app is healthy in CI
        colour = _local_oriented_crop_bgr
        src = f"local fallback ({type(exc).__name__}: {exc})"
    from gawaah.brain import Brain

    return colour, Brain._crop, src


@dataclass
class Capture:
    """One product, seen once, all the way to a vector."""

    sku_id: str
    view_id: str
    taught: bool
    is_enrol: bool
    long_edge_mm: float
    short_edge_mm: float
    angle_deg: float
    centre_mm: tuple[float, float]
    crop_colour: np.ndarray = field(repr=False)
    crop_grey: np.ndarray = field(repr=False)
    vec_colour: np.ndarray = field(repr=False)
    vec_grey: np.ndarray = field(repr=False)
    crop_sha: str = ""
    embed_ms: float = 0.0

    @property
    def truth_long_edge_mm(self) -> float:
        return PRODUCT_BY_ID[self.sku_id].long_edge_mm


def _pair_up(products: Sequence[BenchProduct]) -> list[list[BenchProduct]]:
    return [list(products[i:i + len(EVAL_SLOTS)])
            for i in range(0, len(products), len(EVAL_SLOTS))]


def _measure_scene(loaded: np.ndarray, empty: np.ndarray
                   ) -> tuple[np.ndarray, list[Any], dict[str, Any]]:
    eng = PlaneEngine()
    lock = eng.detect(loaded)
    if not lock.locked:
        raise BenchError(f"the mat did not lock on a rendered scene: {lock.reason}")
    rect = eng.rectify(loaded, lock.H)
    elock = eng.detect(empty)
    if not elock.locked:
        raise BenchError(f"the empty reference did not lock: {elock.reason}")
    ref = eng.rectify(empty, elock.H)
    det = PlacementDetector(ref)
    placements: list[Any] = []
    for _ in range(SETTLE_FRAMES):
        placements = det.update(rect)
    # Unmeasurable placements are RETURNED, not filtered here: the caller has to
    # be able to tell "the segmenter refused this blob" from "there was nothing
    # there", and only one of those two is an honest reason to lose a row.
    return rect, placements, {
        "scale_err_pct": (None if lock.scale_err is None
                          else round(lock.scale_err * 100.0, 4)),
        "persp_index": (None if lock.persp_index is None
                        else round(lock.persp_index, 5)),
    }


#: An item the segmenter would not measure. It is NOT a recognition failure and
#: it is NOT a free pass: the counter cannot price what it cannot measure, so it
#: shows amber. It therefore stays in the denominator as an abstention. Dropping
#: these rows would improve every number in this file for no reason but that
#: they were inconvenient.
UNMEASURED = "placement_unmeasurable"


@dataclass(frozen=True)
class Unmeasured:
    sku_id: str
    view_id: str
    taught: bool
    reason: str


def capture_all(products: Sequence[BenchProduct], views: Sequence[View],
                *, enrol: bool) -> tuple[list[Capture], list[Unmeasured],
                                         dict[str, Any]]:
    """Render, lock, measure, crop and embed every (product, view).

    `enrol=True` puts one product alone in the middle of the mat; `enrol=False`
    puts two products on it at the fixed slots. A placement is bound to a
    product by nearest measured centre within MATCH_TOL_MM.

    Three things can go wrong and all three are RETURNED rather than swallowed:
    a placement that matched no product (`unmatched_placements`), a product the
    segmenter refused to measure (returned as an `Unmeasured`, which becomes an
    abstention downstream), and a product it never saw at all.
    """
    colour_crop, grey_crop, crop_src = crop_fns()
    caps: list[Capture] = []
    unmeasured: list[Unmeasured] = []
    unmatched = 0
    lock_stats: list[dict[str, Any]] = []
    measured_err_mm: list[float] = []

    groups = ([[p] for p in products] if enrol else _pair_up(products))
    slots = [ENROL_SLOT] if enrol else list(EVAL_SLOTS)

    for view in views:
        for group in groups:
            items = []
            for idx, p in enumerate(group):
                sx, sy = slots[idx]
                items.append((p, sx + view.dx_mm, sy + view.dy_mm, view.rot_deg))
            loaded, empty = scene_frames(items, view)
            rect, all_placements, stats = _measure_scene(loaded, empty)
            lock_stats.append(stats)
            placements = [p for p in all_placements if p.measurable]
            refused = [p for p in all_placements if not p.measurable]

            used: set[int] = set()
            for (p, cx_mm, cy_mm, _rot) in items:
                best = None
                best_d = MATCH_TOL_MM
                for i, pl in enumerate(placements):
                    if i in used:
                        continue
                    d = float(np.hypot(pl.centre_mm[0] - cx_mm,
                                       pl.centre_mm[1] - cy_mm))
                    if d < best_d:
                        best, best_d = i, d
                if best is None:
                    why = "not_detected"
                    for pl in refused:
                        d = float(np.hypot(pl.centre_mm[0] - cx_mm,
                                           pl.centre_mm[1] - cy_mm))
                        if d < MATCH_TOL_MM:
                            why = str(pl.reason)
                            break
                    unmeasured.append(
                        Unmeasured(p.sku_id, view.view_id, p.taught, why))
                    continue
                used.add(best)
                pl = _scaled(placements[best], view.crop_pct)
                measured_err_mm.append(
                    abs(float(placements[best].long_edge_mm) - p.long_edge_mm))
                cc = colour_crop(rect, pl)
                gc = grey_crop(rect, pl)
                t0 = time.perf_counter()
                vc = np.asarray(embed(cc), dtype=np.float64)
                embed_ms = (time.perf_counter() - t0) * 1000.0
                vg = np.asarray(embed(gc), dtype=np.float64)
                caps.append(Capture(
                    sku_id=p.sku_id, view_id=view.view_id, taught=p.taught,
                    is_enrol=enrol,
                    long_edge_mm=float(pl.long_edge_mm),
                    short_edge_mm=float(pl.short_edge_mm),
                    angle_deg=float(pl.angle_deg),
                    centre_mm=(float(pl.centre_mm[0]), float(pl.centre_mm[1])),
                    crop_colour=cc, crop_grey=gc, vec_colour=vc, vec_grey=vg,
                    crop_sha=hashlib.sha256(
                        np.ascontiguousarray(cc).tobytes()).hexdigest(),
                    embed_ms=embed_ms,
                ))
            unmatched += len(placements) - len(used)

    scale_errs = [s["scale_err_pct"] for s in lock_stats
                  if s["scale_err_pct"] is not None]
    return caps, unmeasured, {
        "crop_source": crop_src,
        "n_scenes": len(lock_stats),
        "unmatched_placements": unmatched,
        "products_not_measured": len(unmeasured),
        "unmeasured_reasons": sorted({u.reason for u in unmeasured}),
        "unmeasured_rows": [{"sku_id": u.sku_id, "view_id": u.view_id,
                             "reason": u.reason} for u in unmeasured],
        "worst_measure_err_mm": (round(max(measured_err_mm), 3)
                                 if measured_err_mm else None),
        "mean_measure_err_mm": (round(float(np.mean(measured_err_mm)), 3)
                                if measured_err_mm else None),
        "worst_scale_err_pct": round(max(scale_errs), 4) if scale_errs else None,
    }


# ---------------------------------------------------------------- disjointness

def disjointness(enrol_caps: Sequence[Capture],
                 eval_caps: Sequence[Capture]) -> dict[str, Any]:
    """Re-prove, on this run's actual data, that nothing evaluated was enrolled.

    Five independent checks. `ok` is the AND of all five; if any one fails the
    run's accuracy is meaningless and `run()` refuses to publish it.
    """
    e_keys = {ENROL_VIEW.key()}
    v_keys = {v.key() for v in EVAL_VIEWS}
    e_sha = {c.crop_sha for c in enrol_caps}
    v_sha = {c.crop_sha for c in eval_caps}
    e_seeds = {ENROL_VIEW.noise_seed}
    v_seeds = {v.noise_seed for v in EVAL_VIEWS}

    pose_differs = all(
        v.rot_deg != ENROL_VIEW.rot_deg
        and (v.dx_mm, v.dy_mm) != (ENROL_VIEW.dx_mm, ENROL_VIEW.dy_mm)
        and v.noise_seed != ENROL_VIEW.noise_seed
        for v in EVAL_VIEWS
    )
    checks = {
        "view_parameter_tuples_disjoint": not (e_keys & v_keys),
        "noise_seed_ranges_disjoint": (
            not (e_seeds & v_seeds)
            and all(1000 <= s < 2000 for s in e_seeds)
            and all(2000 <= s < 3000 for s in v_seeds)
        ),
        "every_eval_view_differs_in_pose_and_exposure": pose_differs,
        "enrol_scene_holds_one_item_eval_holds_two": len(EVAL_SLOTS) == 2,
        "no_evaluated_crop_shares_bytes_with_an_enrolled_crop":
            not (e_sha & v_sha),
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "n_enrol_crops": len(enrol_caps),
        "n_eval_crops": len(eval_caps),
        "n_shared_crop_hashes": len(e_sha & v_sha),
        "enrol_view": ENROL_VIEW.view_id,
        "eval_views": [v.view_id for v in EVAL_VIEWS],
    }


# ------------------------------------------------------------------ enrolment

@dataclass
class EnrolReport:
    accepted: tuple[str, ...]
    refused: tuple[tuple[str, str, float, Optional[float]], ...]
    store_dir: str
    dim: int
    catalog_bytes: int
    reopened_skus: tuple[str, ...]


def enrol(enrol_caps: Sequence[Capture], directory: Path, *,
          channel: str = "colour") -> tuple[ShopStore, EnrolReport]:
    """Teach a ShopStore from the enrolment captures, then REOPEN it from disk.

    The reopen is not decoration. The shopkeeper teaches on one process (the
    enrol desk, port 8790) and sells on another (the counter, 8787), so a bench
    that evaluated the in-memory store would be measuring a path nobody uses.
    Everything after this function reads a catalog that came off a disk.
    """
    directory.mkdir(parents=True, exist_ok=True)
    store = ShopStore(directory)
    accepted: list[str] = []
    refused: list[tuple[str, str, float, Optional[float]]] = []
    for c in enrol_caps:
        p = PRODUCT_BY_ID[c.sku_id]
        if not p.taught:
            continue
        vec = c.vec_colour if channel == "colour" else c.vec_grey
        res = store.add_sku(p.sku_id, p.name, p.price_paise, [vec],
                            c.long_edge_mm, photo_png=c.crop_colour)
        if res.ok:
            accepted.append(p.sku_id)
        else:
            refused.append((p.sku_id, res.collides_with or "?",
                            float(res.similarity),
                            None if res.footprint_delta_mm is None
                            else float(res.footprint_delta_mm)))
    reopened = ShopStore(directory)
    return reopened, EnrolReport(
        accepted=tuple(accepted),
        refused=tuple(refused),
        store_dir=str(directory),
        dim=int(reopened.dim or 0),
        catalog_bytes=(reopened.catalog_path.stat().st_size
                       if reopened.catalog_path.exists() else 0),
        reopened_skus=reopened.skus(),
    )


# ------------------------------------------------------------------ evaluation

CORRECT = "correct"
FALSE_PRICE = "false_price"
ABSTAINED = "abstained"


#: The three buckets an evaluated item can belong to. They are decided by what
#: is IN THE GALLERY, not by what the product set intended: a product the
#: collision guard refused is a product the counter was never taught, however
#: hard the shopkeeper tried, and scoring it as taught would blame recognition
#: for an enrolment refusal.
BUCKET_ENROLLED = "enrolled"
BUCKET_REFUSED = "refused_at_enrolment"
BUCKET_NEVER_TAUGHT = "never_taught"


@dataclass(frozen=True)
class Outcome:
    """One evaluated crop, judged."""

    sku_id: str            # the truth
    view_id: str
    bucket: str
    predicted: Optional[str]
    price_paise: Optional[int]
    reason: str
    top1: float
    top2: float
    margin: float
    top1_sku: Optional[str]
    n_candidates: int
    latency_ms: float
    verdict: str
    embed_ms: float = 0.0

    @property
    def decided(self) -> bool:
        return self.predicted is not None

    @property
    def enrolled(self) -> bool:
        return self.bucket == BUCKET_ENROLLED

    @property
    def open_set(self) -> bool:
        return self.bucket != BUCKET_ENROLLED


def _bucket(sku_id: str, enrolled: Sequence[str]) -> str:
    if sku_id in enrolled:
        return BUCKET_ENROLLED
    return (BUCKET_NEVER_TAUGHT if not PRODUCT_BY_ID[sku_id].taught
            else BUCKET_REFUSED)


def _judge(sku_id: str, bucket: str, predicted: Optional[str]) -> str:
    if predicted is None:
        return ABSTAINED
    if bucket != BUCKET_ENROLLED:
        # Any name at all is a false price: this packet is not in the gallery,
        # so there is no correct name to return. Naming it bills the customer
        # for a product they are not holding.
        return FALSE_PRICE
    return CORRECT if predicted == sku_id else FALSE_PRICE


def evaluate(rec: Recogniser, eval_caps: Sequence[Capture],
             unmeasured: Sequence[Unmeasured] = (),
             *, channel: str = "colour",
             time_embed: bool = False) -> list[Outcome]:
    """Every captured crop, plus every item the segmenter refused to measure.

    The refusals are included as abstentions on purpose. An item with no
    millimetres is an item the counter cannot price, which is amber — a real
    outcome for a real shopper, and leaving it out of the denominator would
    quietly improve every rate in this file.
    """
    enrolled = rec.skus()
    out: list[Outcome] = []
    for c in eval_caps:
        crop = c.crop_colour if channel == "colour" else c.crop_grey
        # Timed back to back on the SAME crop in the SAME loop, so the two
        # numbers are comparable: identify() is embed() plus the gallery scan,
        # and timing them in different phases would make the scan look free or
        # even negative.
        embed_ms = 0.0
        if time_embed:
            t0 = time.perf_counter()
            embed(crop)
            embed_ms = (time.perf_counter() - t0) * 1000.0
        t0 = time.perf_counter()
        r = rec.identify(crop, c.long_edge_mm)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        bucket = _bucket(c.sku_id, enrolled)
        out.append(Outcome(
            sku_id=c.sku_id, view_id=c.view_id, bucket=bucket,
            predicted=r.sku_id, price_paise=r.price_paise, reason=r.reason,
            top1=r.top1, top2=r.top2, margin=r.margin, top1_sku=r.top1_sku,
            n_candidates=r.n_candidates, latency_ms=latency_ms,
            verdict=_judge(c.sku_id, bucket, r.sku_id), embed_ms=embed_ms,
        ))
    for u in unmeasured:
        bucket = _bucket(u.sku_id, enrolled)
        out.append(Outcome(
            sku_id=u.sku_id, view_id=u.view_id, bucket=bucket,
            predicted=None, price_paise=None,
            reason=f"{UNMEASURED}:{u.reason}",
            top1=0.0, top2=0.0, margin=0.0, top1_sku=None, n_candidates=0,
            latency_ms=0.0, verdict=ABSTAINED,
        ))
    return out


# --------------------------------------------------------------------- metrics

def _frac(num: int, den: int) -> float:
    return 0.0 if den == 0 else num / den


@dataclass(frozen=True)
class Metrics:
    """The headline. Three numbers, never one.

    `false_price_frac` is the false-price RATE. It is spelled with the `_frac`
    suffix because tools/lint_no_float.py treats any identifier containing
    "price" as money and refuses floats reaching it; `_frac` is that lint's own
    documented suffix for a dimensionless quantity.
    """

    label: str
    n_items: int
    n_decided: int
    n_correct: int
    n_false_price: int
    n_abstained: int
    accuracy_on_decided_frac: float
    abstain_frac: float
    false_price_frac: float
    false_price_of_decided_frac: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "n_items": self.n_items,
            "n_decided": self.n_decided,
            "n_correct": self.n_correct,
            "n_false_price": self.n_false_price,
            "n_abstained": self.n_abstained,
            "top1_accuracy_on_decided": round(self.accuracy_on_decided_frac, 4),
            "abstention_rate": round(self.abstain_frac, 4),
            "false_price_rate": round(self.false_price_frac, 4),
            "false_price_rate_of_decided": round(self.false_price_of_decided_frac, 4),
        }


def score(outcomes: Iterable[Outcome], label: str = "all") -> Metrics:
    rows = list(outcomes)
    n = len(rows)
    dec = sum(1 for o in rows if o.decided)
    cor = sum(1 for o in rows if o.verdict == CORRECT)
    fp = sum(1 for o in rows if o.verdict == FALSE_PRICE)
    ab = sum(1 for o in rows if o.verdict == ABSTAINED)
    return Metrics(
        label=label, n_items=n, n_decided=dec, n_correct=cor,
        n_false_price=fp, n_abstained=ab,
        accuracy_on_decided_frac=_frac(cor, dec),
        abstain_frac=_frac(ab, n),
        false_price_frac=_frac(fp, n),
        false_price_of_decided_frac=_frac(fp, dec),
    )


def shortlist_stats(outcomes: Iterable[Outcome], n_skus: int) -> dict[str, Any]:
    """How much of the answer the TAPE MEASURE gave away for free.

    identity.py filters by footprint BEFORE appearance is consulted. If that
    filter routinely leaves a shortlist of one, the descriptor was never asked a
    question and the accuracy above is the mat's, not the embedder's. This is
    the single number that says whether the product set is honest.
    """
    n = [o.n_candidates for o in outcomes if o.n_candidates > 0]
    if not n:
        return {"n": 0}
    a = np.asarray(n, dtype=np.float64)
    alone = int((a == 1).sum())
    return {
        "n": int(a.size),
        "n_skus_in_gallery": int(n_skus),
        "median_shortlist": float(np.median(a)),
        "mean_shortlist": round(float(a.mean()), 2),
        "max_shortlist": int(a.max()),
        "n_answered_by_footprint_alone": alone,
        "frac_answered_by_footprint_alone": round(_frac(alone, int(a.size)), 4),
    }


ABSTAIN_COL = "(abstain)"


def confusion(outcomes: Iterable[Outcome]) -> tuple[list[str], list[str],
                                                    list[list[int]]]:
    """rows = truth (every evaluated product), cols = what was returned."""
    rows = list(outcomes)
    truths = sorted({o.sku_id for o in rows})
    preds = sorted({o.predicted for o in rows if o.predicted is not None})
    cols = preds + [ABSTAIN_COL]
    idx_r = {t: i for i, t in enumerate(truths)}
    idx_c = {c: i for i, c in enumerate(cols)}
    m = [[0] * len(cols) for _ in truths]
    for o in rows:
        m[idx_r[o.sku_id]][idx_c[o.predicted if o.predicted else ABSTAIN_COL]] += 1
    return truths, cols, m


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.clip(float(np.dot(a, b)) / (na * nb), -1.0, 1.0))


def cosine_split(enrol_caps: Sequence[Capture], eval_caps: Sequence[Capture],
                 enrolled: Sequence[str], *,
                 channel: str = "colour") -> dict[str, list[float]]:
    """Every (eval crop, gallery entry) cosine, split by whether they are the
    same product. This is the descriptor's own separation, measured underneath
    the gates rather than through them.

    The gallery is the ACTUAL gallery — the skus that survived enrolment — and
    the queries are ALL evaluated crops including the untaught ones, because an
    untaught crop scoring high against a gallery entry is exactly the event that
    produces a false price, and leaving it out of the "different" distribution
    would hide the only overlap that costs money.
    """
    key = "vec_colour" if channel == "colour" else "vec_grey"
    gal = {c.sku_id: getattr(c, key) for c in enrol_caps
           if c.sku_id in set(enrolled)}
    same: list[float] = []
    diff: list[float] = []
    for c in eval_caps:
        q = getattr(c, key)
        for sku, v in gal.items():
            s = _cos(q, v)
            (same if sku == c.sku_id else diff).append(s)
    return {"same": same, "different": diff}


def _pct(xs: Sequence[float], q: float) -> float:
    return float(np.percentile(np.asarray(xs, dtype=np.float64), q)) if xs else 0.0


def overlap(same: Sequence[float], diff: Sequence[float]) -> dict[str, Any]:
    """How much the two distributions actually overlap.

    Three ways, because each hides something different: the percentile gap is
    what a summary table usually shows and it is the easiest to flatter; the
    raw min/max gap is what a single bad pair does to you; and the count of
    different-product pairs that outscore the WORST same-product pair is the
    number that decides whether any single threshold can separate them at all.
    """
    if not same or not diff:
        return {"n_same": len(same), "n_different": len(diff), "separable": None}
    s = np.asarray(same, dtype=np.float64)
    d = np.asarray(diff, dtype=np.float64)
    s_min, d_max = float(s.min()), float(d.max())
    above = int((d >= s_min).sum())
    below = int((s <= d_max).sum())
    # ROC AUC by rank, ties counted as half.
    order = np.argsort(np.concatenate([s, d]), kind="mergesort")
    ranks = np.empty(order.size, dtype=np.float64)
    ranks[order] = np.arange(1, order.size + 1, dtype=np.float64)
    vals = np.concatenate([s, d])
    for v in np.unique(vals):
        m = vals == v
        if m.sum() > 1:
            ranks[m] = ranks[m].mean()
    auc = float((ranks[:s.size].sum() - s.size * (s.size + 1) / 2.0)
                / (s.size * d.size))
    return {
        "n_same": int(s.size), "n_different": int(d.size),
        "same_min": round(s_min, 4), "same_p05": round(_pct(same, 5), 4),
        "same_median": round(float(np.median(s)), 4),
        "same_mean": round(float(s.mean()), 4), "same_max": round(float(s.max()), 4),
        "diff_min": round(float(d.min()), 4), "diff_median": round(float(np.median(d)), 4),
        "diff_mean": round(float(d.mean()), 4), "diff_p95": round(_pct(diff, 95), 4),
        "diff_max": round(d_max, 4),
        "gap_p05_same_minus_p95_diff": round(_pct(same, 5) - _pct(diff, 95), 4),
        "gap_min_same_minus_max_diff": round(s_min - d_max, 4),
        "n_different_at_or_above_worst_same": above,
        "frac_different_above_worst_same": round(_frac(above, int(d.size)), 5),
        "n_same_at_or_below_best_different": below,
        "separable_by_one_threshold": bool(s_min > d_max),
        "roc_auc": round(auc, 5),
        "phi_used": DEFAULT_PHI,
        "n_different_above_phi": int((d >= DEFAULT_PHI).sum()),
        "n_same_below_phi": int((s < DEFAULT_PHI).sum()),
    }


def top_impostors(enrol_caps: Sequence[Capture], eval_caps: Sequence[Capture],
                  enrolled: Sequence[str], *, channel: str = "colour",
                  n: int = 12) -> list[dict[str, Any]]:
    """The highest DIFFERENT-product cosines in the run, named.

    A distribution summary can hide the only thing that matters — which two
    packets the descriptor thinks are the same one. This names them, worst
    first, and says whether each one cleared phi.
    """
    key = "vec_colour" if channel == "colour" else "vec_grey"
    fp = {c.sku_id: c.long_edge_mm for c in enrol_caps}
    gal = [(c.sku_id, getattr(c, key)) for c in enrol_caps
           if c.sku_id in set(enrolled)]
    rows: list[dict[str, Any]] = []
    for c in eval_caps:
        q = getattr(c, key)
        for sku, v in gal:
            if sku == c.sku_id:
                continue
            s = _cos(q, v)
            rows.append({
                "query": c.sku_id, "view": c.view_id, "gallery": sku,
                "cosine": round(s, 4),
                "footprint_delta_mm": round(abs(fp[c.sku_id] - fp[sku]), 2),
                "in_footprint_gate":
                    bool(abs(fp[c.sku_id] - fp[sku]) <= DEFAULT_TAU_MM),
                "clears_phi": bool(s >= DEFAULT_PHI),
            })
    rows.sort(key=lambda r: (-r["cosine"], r["query"], r["gallery"]))
    # One row per (query, gallery) pair: six views of the same confusion is one
    # confusion, and listing it six times would pad the table.
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        best.setdefault((r["query"], r["gallery"]), r)
    return sorted(best.values(), key=lambda r: -r["cosine"])[:n]


def hard_pair_report(enrol_caps: Sequence[Capture],
                     outcomes: Sequence[Outcome],
                     accepted: Sequence[str],
                     refused: Sequence[tuple[str, str, float, Optional[float]]],
                     *, channel: str = "colour") -> list[dict[str, Any]]:
    """For each designed hard pair: the enrolment cosine, whether the guard
    refused it, and whether either member was ever billed as the other."""
    key = "vec_colour" if channel == "colour" else "vec_grey"
    vec = {c.sku_id: getattr(c, key) for c in enrol_caps}
    fp_mm = {c.sku_id: c.long_edge_mm for c in enrol_caps}
    refused_by = {r[0]: r for r in refused}
    rows: list[dict[str, Any]] = []
    for a, b, expect, why in HARD_PAIRS:
        if a not in vec or b not in vec:  # pragma: no cover - quick subsets
            continue
        sim = _cos(vec[a], vec[b])
        delta = abs(fp_mm[a] - fp_mm[b])
        cross = [o for o in outcomes
                 if (o.sku_id == a and o.predicted == b)
                 or (o.sku_id == b and o.predicted == a)]
        seen = [o for o in outcomes if o.sku_id in (a, b)]
        amber = [o for o in seen if not o.decided]
        both_enrolled = a in accepted and b in accepted
        # A pair with an OPEN-SET member is not separated just because it was
        # never billed as its partner: an untaught packet billed as any THIRD
        # product is the same failure. So the verdict for those pairs is
        # "the untaught member was never named at all".
        open_named = [o for o in outcomes
                      if o.sku_id in (a, b) and o.open_set and o.decided]
        has_open = any(x not in accepted for x in (a, b))
        if expect == MUST_COLLIDE:
            separated = False
        elif has_open:
            separated = bool(len(open_named) == 0)
        else:
            separated = bool(len(cross) == 0 and sim < 1.0 - DEFAULT_THETA)
        rows.append({
            "a": a, "b": b, "expect": expect, "why": why,
            "enrol_cosine": round(sim, 4),
            "footprint_delta_mm": round(delta, 2),
            "inside_footprint_gate": bool(delta <= DEFAULT_TAU_MM),
            "above_collision_bar": bool(sim >= 1.0 - DEFAULT_THETA),
            "guard_refused": a in refused_by or b in refused_by,
            "guard_refused_detail": (refused_by.get(a) or refused_by.get(b) or None),
            "both_enrolled": both_enrolled,
            "has_open_set_member": has_open,
            "n_open_set_named": len(open_named),
            "open_set_named_as": sorted({o.predicted for o in open_named
                                         if o.predicted}),
            "n_views_seen": len(seen),
            "n_cross_billed": len(cross),
            "n_amber": len(amber),
            "separated": separated,
        })
    return rows


# ------------------------------------------------------------------ gate sweep

class _MemoEmbed:
    """embed(), memoised on the crop's bytes. Only for the gate sweep, where
    the same crops are re-identified 25 times and the embedder's cost would
    otherwise dominate a measurement that is not about the embedder."""

    def __init__(self) -> None:
        self._c: dict[str, np.ndarray] = {}
        self.calls = 0
        self.misses = 0

    def __call__(self, crop: np.ndarray) -> np.ndarray:
        self.calls += 1
        k = hashlib.sha256(np.ascontiguousarray(crop).tobytes()).hexdigest()
        v = self._c.get(k)
        if v is None:
            self.misses += 1
            v = np.asarray(embed(crop), dtype=np.float64)
            self._c[k] = v
        return v


#: The grids reach WELL past the shipped values in both directions. A sweep that
#: only wobbles around the default cannot tell you whether the default is on the
#: frontier, and that is the question the sweep is here to answer.
THETA_GRID = (0.00, 0.05, 0.10, 0.20, 0.30, 0.40)
# Extended past 0.90 when DEFAULT_PHI was raised there: a sweep that stops at
# the shipped value cannot show whether the shipped value is a peak or a slope.
PHI_GRID = (0.40, 0.55, 0.70, 0.80, 0.90, 0.94, 0.97)


def gate_sweep(store: ShopStore, eval_caps: Sequence[Capture],
               unmeasured: Sequence[Unmeasured] = (),
               *, channel: str = "colour",
               thetas: Sequence[float] = THETA_GRID,
               phis: Sequence[float] = PHI_GRID) -> list[dict[str, Any]]:
    """What every other choice of gate would have scored.

    This exists to make widening visible. If the shipped (0.10, 0.55) is not on
    the frontier the sweep says so; and if a looser gate buys accuracy, the
    table shows exactly what it costs in false prices.
    """
    memo = _MemoEmbed()
    rows: list[dict[str, Any]] = []
    for th in thetas:
        for ph in phis:
            rec = Recogniser(store, memo, theta=th, phi=ph,
                             tau_mm=DEFAULT_TAU_MM, strict=True)
            outs = evaluate(rec, eval_caps, unmeasured, channel=channel)
            m = score(outs, f"theta={th:.2f} phi={ph:.2f}")
            enr = score([o for o in outs if o.enrolled], "enrolled")
            rows.append({
                "theta": th, "phi": ph,
                "is_shipped_default": (th == DEFAULT_THETA and ph == DEFAULT_PHI),
                "enrolled_accuracy_on_decided":
                    round(enr.accuracy_on_decided_frac, 4),
                "enrolled_abstention_rate": round(enr.abstain_frac, 4),
                **m.as_dict(),
            })
    return rows


def sweep_frontier(sweep: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Does any other gate setting STRICTLY beat the shipped one here?

    "Strictly beats" means: fewer false prices overall, and no worse accuracy on
    the enrolled products, and no more abstentions on them. If such a setting
    exists, the shipped default is not on the frontier FOR THIS DATA, and saying
    so is the whole point of running the sweep. It is not a licence to change
    the shipped value — a threshold tuned to eighteen rendered rectangles is
    exactly the overfit `gawaah/embedder.py` documents itself having made once
    already — but it is a fact the reader is entitled to.
    """
    ship = next((r for r in sweep if r["is_shipped_default"]), None)
    if ship is None:
        return {"available": False}
    better = [
        r for r in sweep
        if not r["is_shipped_default"]
        and r["n_false_price"] < ship["n_false_price"]
        and r["enrolled_accuracy_on_decided"] >= ship["enrolled_accuracy_on_decided"]
        and r["enrolled_abstention_rate"] <= ship["enrolled_abstention_rate"]
    ]
    better.sort(key=lambda r: (r["n_false_price"], r["enrolled_abstention_rate"],
                               r["theta"], r["phi"]))

    # The real shape of the choice is a trade curve, not a winner. A setting is
    # on the PARETO FRONT when nothing else in the grid gets both fewer false
    # prices and fewer gallery abstentions.
    keep = [r for r in sweep
            if r["enrolled_accuracy_on_decided"]
            >= ship["enrolled_accuracy_on_decided"]]
    front = []
    for r in keep:
        dominated = any(
            (o["n_false_price"] <= r["n_false_price"])
            and (o["enrolled_abstention_rate"] <= r["enrolled_abstention_rate"])
            and (o["n_false_price"] < r["n_false_price"]
                 or o["enrolled_abstention_rate"] < r["enrolled_abstention_rate"])
            for o in keep)
        if not dominated:
            front.append(r)
    front.sort(key=lambda r: (r["enrolled_abstention_rate"], r["n_false_price"]))
    seen: set[tuple[int, float]] = set()
    front_u = []
    for r in front:
        k = (r["n_false_price"], r["enrolled_abstention_rate"])
        if k in seen:
            continue
        seen.add(k)
        front_u.append(r)

    def _slim(r: dict[str, Any]) -> dict[str, Any]:
        return {k: r[k] for k in ("theta", "phi", "n_false_price",
                                  "false_price_rate",
                                  "enrolled_accuracy_on_decided",
                                  "enrolled_abstention_rate")}

    # Which gate is doing the work: hold one at its default and move the other.
    phi_only = [r for r in sweep if r["theta"] == DEFAULT_THETA]
    theta_only = [r for r in sweep if r["phi"] == DEFAULT_PHI]
    return {
        "available": True,
        "shipped": _slim(ship),
        "shipped_strictly_beaten": bool(better),
        "n_settings_that_strictly_beat_shipped": len(better),
        "best_alternatives": [_slim(r) for r in better[:5]],
        "pareto_front": [_slim(r) for r in front_u],
        "shipped_is_on_the_pareto_front": any(
            r["theta"] == ship["theta"] and r["phi"] == ship["phi"]
            for r in front),
        "phi_alone_span_false_prices": (
            [min(r["n_false_price"] for r in phi_only),
             max(r["n_false_price"] for r in phi_only)] if phi_only else None),
        "phi_alone_span_gallery_abstention": (
            [min(r["enrolled_abstention_rate"] for r in phi_only),
             max(r["enrolled_abstention_rate"] for r in phi_only)]
            if phi_only else None),
        "theta_alone_span_false_prices": (
            [min(r["n_false_price"] for r in theta_only),
             max(r["n_false_price"] for r in theta_only)] if theta_only else None),
        "theta_alone_span_gallery_abstention": (
            [min(r["enrolled_abstention_rate"] for r in theta_only),
             max(r["enrolled_abstention_rate"] for r in theta_only)]
            if theta_only else None),
        "gallery_accuracy_is_constant": len(
            {r["enrolled_accuracy_on_decided"] for r in sweep}) == 1,
        "gallery_accuracy_value": ship["enrolled_accuracy_on_decided"],
    }


# ------------------------------------------------------------- gate provenance

_GATE_DEFAULTS = {"theta": DEFAULT_THETA, "phi": DEFAULT_PHI,
                  "tau_mm": DEFAULT_TAU_MM}
_GATE_CONSTRUCTORS = ("Identifier", "Recogniser", "ShopStore")


class _GateVisitor(ast.NodeVisitor):
    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.hits: list[dict[str, Any]] = []

    def visit_Call(self, node: ast.Call) -> None:
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else (
            fn.attr if isinstance(fn, ast.Attribute) else None)
        if name in _GATE_CONSTRUCTORS:
            for kw in node.keywords:
                if kw.arg in _GATE_DEFAULTS and isinstance(kw.value, ast.Constant):
                    v = kw.value.value
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        if float(v) != _GATE_DEFAULTS[kw.arg]:
                            self.hits.append({
                                "file": self.rel, "line": kw.value.lineno,
                                "constructor": name, "gate": kw.arg,
                                "value": float(v),
                                "default": _GATE_DEFAULTS[kw.arg],
                            })
        self.generic_visit(node)


def gate_provenance(root: Path = ROOT) -> dict[str, Any]:
    """Did anybody widen the gates to make this look better?

    Three independent answers:
      1. the values this bench ran at, compared to gawaah.identity's defaults;
      2. the git history of the four lines that define them;
      3. an AST scan of every gawaah/, tools/ and tests/ module for a call that
         constructs an Identifier, Recogniser or ShopStore with a NON-default
         gate. Tests are allowed to (one of them exists precisely to show that
         phi is a real lever); anything in gawaah/ or tools/ would be a finding.
    """
    hits: list[dict[str, Any]] = []
    scanned = 0
    for sub in ("gawaah", "tools", "tests"):
        for p in sorted((root / sub).glob("*.py")):
            scanned += 1
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                hits.append({"file": str(p.relative_to(root)), "line": exc.lineno,
                             "constructor": "?", "gate": "?", "value": None,
                             "default": None, "unparseable": str(exc)})
                continue
            v = _GateVisitor(str(p.relative_to(root)))
            v.visit(tree)
            hits.extend(v.hits)
    prod_hits = [h for h in hits if not h["file"].startswith("tests/")
                 and h["file"] != "tools/bench_recognise.py"]

    history: Any
    try:
        out = subprocess.run(
            ["git", "log", "-L", "57,60:gawaah/identity.py", "--format=%h %s"],
            cwd=str(root), capture_output=True, text=True, timeout=20)
        commits = [ln.strip() for ln in out.stdout.splitlines()
                   if ln and not ln.startswith(("diff ", "--- ", "+++ ", "@@", "+",
                                                "-", " "))]
        history = {"available": out.returncode == 0,
                   "n_commits_touching_the_gate_lines": len(commits),
                   "commits": commits[:10]}
    except Exception as exc:  # pragma: no cover - git may be absent
        history = {"available": False, "error": f"{type(exc).__name__}: {exc}"}

    return {
        "bench_ran_at": dict(_GATE_DEFAULTS),
        "identity_defaults": {"theta": DEFAULT_THETA, "phi": DEFAULT_PHI,
                              "tau_mm": DEFAULT_TAU_MM},
        "bench_used_defaults": True,
        "files_scanned": scanned,
        "non_default_gate_call_sites": hits,
        "non_default_in_production_code": prod_hits,
        "clean": not prod_hits,
        "gate_line_history": history,
    }


# ------------------------------------------------------------------- the run

@dataclass
class BenchResult:
    ok: bool
    products: list[dict[str, Any]]
    capture: dict[str, Any]
    disjoint: dict[str, Any]
    enrolment: dict[str, Any]
    headline: dict[str, Any]
    per_view: list[dict[str, Any]]
    per_product: list[dict[str, Any]]
    confusion_rows: list[str]
    confusion_cols: list[str]
    confusion_matrix: list[list[int]]
    cosines_colour: dict[str, Any]
    cosines_grey: dict[str, Any]
    impostors: list[dict[str, Any]]
    hard_pairs: list[dict[str, Any]]
    grey_headline: dict[str, Any]
    forced_collision: dict[str, Any]
    negative_control: dict[str, Any]
    latency: dict[str, Any]
    sweep: list[dict[str, Any]]
    frontier: dict[str, Any]
    provenance: dict[str, Any]
    environment: dict[str, Any]
    outcomes: list[Outcome] = field(default_factory=list, repr=False)

    def as_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "outcomes"}
        return d


def _latency_stats(outcomes: Sequence[Outcome]) -> dict[str, Any]:
    rows = [o for o in outcomes if o.latency_ms > 0.0]
    lat = np.asarray([o.latency_ms for o in rows], dtype=np.float64)
    emb = np.asarray([o.embed_ms for o in rows if o.embed_ms > 0.0],
                     dtype=np.float64)
    if lat.size == 0:  # pragma: no cover
        return {"n": 0}
    return {
        "n": int(lat.size),
        "identify_median_ms": round(float(np.median(lat)), 3),
        "identify_mean_ms": round(float(lat.mean()), 3),
        "identify_p95_ms": round(float(np.percentile(lat, 95)), 3),
        "identify_max_ms": round(float(lat.max()), 3),
        "embed_median_ms": round(float(np.median(emb)), 3) if emb.size else None,
        "embed_p95_ms": (round(float(np.percentile(emb, 95)), 3)
                         if emb.size else None),
        "embed_share_of_identify": (
            round(float(np.median(emb)) / float(np.median(lat)), 3)
            if emb.size else None),
        "gallery_scan_ms": (
            round(float(np.median(lat)) - float(np.median(emb)), 3)
            if emb.size else None),
        "note": "per ITEM, not per frame. identify() = embed() + the gallery "
                "scan; both timed back to back on the same crop in the same "
                "loop. Scene rendering, the mat lock and the segmenter are NOT "
                "in here — they are per frame and are a simulation artefact "
                "besides.",
    }


def _forced_collision(enrol_caps: Sequence[Capture],
                      eval_caps: Sequence[Capture]) -> dict[str, Any]:
    """The counterfactual the collision guard exists to prevent.

    Force the must-collide pair into one gallery and see what the counter does.
    The honest answer is that BOTH members go permanently amber — which is why
    the guard refuses at enrolment, when the shopkeeper is still holding the
    packet, instead of at the till with a customer waiting.
    """
    pair = next((hp for hp in HARD_PAIRS if hp[2] == MUST_COLLIDE), None)
    if pair is None:  # pragma: no cover
        return {"ran": False}
    a, b = pair[0], pair[1]
    caps = {c.sku_id: c for c in enrol_caps}
    if a not in caps or b not in caps:
        return {"ran": False, "why": "pair not captured in this subset"}
    g = Gallery()
    for sku in (a, b):
        g.enroll(sku, [caps[sku].vec_colour], caps[sku].long_edge_mm)
    idf = Identifier(g, embed)
    rows = []
    for c in eval_caps:
        if c.sku_id not in (a, b):
            continue
        r = idf.identify(c.crop_colour, c.long_edge_mm)
        rows.append({"truth": c.sku_id, "view": c.view_id, "sku": r.sku_id,
                     "reason": r.reason, "top1": round(r.top1, 4),
                     "margin": round(r.margin, 4)})
    named = [r for r in rows if r["sku"] is not None]
    return {
        "ran": True, "pair": [a, b], "n_views": len(rows),
        "n_named": len(named),
        "n_amber": len(rows) - len(named),
        "reasons": sorted({r["reason"] for r in rows}),
        "worst_margin": (round(min(r["margin"] for r in rows), 4)
                         if rows else None),
        "rows": rows,
    }


def negative_control(enrol_caps: Sequence[Capture],
                     eval_caps: Sequence[Capture],
                     unmeasured: Sequence[Unmeasured],
                     directory: Path) -> dict[str, Any]:
    """CAN THIS INSTRUMENT READ A ZERO?

    Re-run the whole enrol-then-recognise loop on the SAME crops with a
    deliberately blind descriptor — a constant vector, so every packet in the
    world embeds identically — and publish what the bench then reports.

    A bench that cannot detect a broken recogniser is not evidence of a working
    one. And this particular control turned up something the headline table
    needed: with a blind descriptor, top-1 accuracy ON THE GALLERY reads 100%
    with zero abstentions. It is not a bug in the scoring. The collision guard
    correctly refuses every colliding enrolment, which thins the gallery down to
    one survivor per footprint family; the footprint filter then leaves a
    shortlist of exactly one for every query, and naming the only candidate is
    trivially right. The numbers that DO catch it are the enrolment refusals,
    the shortlist size, the open-set false-price rate and the ROC AUC — which is
    the whole argument for reporting more than one number.
    """
    const = np.ones(EMBED_DIM, dtype=np.float64)

    def blind(_crop: Any) -> np.ndarray:
        return const.copy()

    store = ShopStore(directory)
    accepted: list[str] = []
    refused: list[str] = []
    for c in enrol_caps:
        p = PRODUCT_BY_ID[c.sku_id]
        if not p.taught:
            continue
        res = store.add_sku(p.sku_id, p.name, p.price_paise, [const.copy()],
                            c.long_edge_mm)
        (accepted if res.ok else refused).append(p.sku_id)
    rec = Recogniser(ShopStore(directory), blind, theta=DEFAULT_THETA,
                     phi=DEFAULT_PHI, tau_mm=DEFAULT_TAU_MM, strict=True)
    outs = evaluate(rec, eval_caps, unmeasured, channel="colour")

    # The AUC is COMPUTED from blinded copies of the same captures, not
    # asserted to be 0.5. A shadow copy, because a blind descriptor's cosine
    # distributions are a fact about the descriptor and have to be measured the
    # same way the real ones were.
    def _blank(c: Capture) -> Capture:
        d = Capture(**{**c.__dict__, "vec_colour": const.copy(),
                       "vec_grey": const.copy()})
        return d

    b_enrol = [_blank(c) for c in enrol_caps]
    b_eval = [_blank(c) for c in eval_caps]
    b_split = cosine_split(b_enrol, b_eval, accepted)
    return {
        "ran": True,
        "descriptor": "a constant vector: every packet embeds identically",
        "n_accepted": len(accepted),
        "n_refused_by_the_guard": len(refused),
        "refused": refused,
        "enrolled": score([o for o in outs if o.enrolled],
                          "blind, in the gallery").as_dict(),
        "all": score(outs, "blind, everything").as_dict(),
        "shortlist": shortlist_stats(outs, len(accepted)),
        "roc_auc": overlap(b_split["same"], b_split["different"]).get("roc_auc"),
    }


def run(*, quick: bool = False, store_dir: Optional[Path] = None,
        with_sweep: bool = True) -> BenchResult:
    """The whole measurement. Deterministic: every seed is fixed."""
    t_start = time.perf_counter()
    products = list(PRODUCTS)
    views = list(EVAL_VIEWS)
    if quick:
        products = [p for p in PRODUCTS
                    if p.sku_id in ("parle_glucose", "jeera_glucose",
                                    "krack_jack", "monaco_salted",
                                    "clinic_sachet", "vatika_sachet",
                                    "intruder_lookalike")]
        views = list(EVAL_VIEWS[:2])

    enrol_caps, enrol_unmeasured, cap_stats_e = capture_all(
        products, [ENROL_VIEW], enrol=True)
    eval_caps, eval_unmeasured, cap_stats_v = capture_all(
        products, views, enrol=False)
    dis = disjointness(enrol_caps, eval_caps)
    if not dis["ok"]:
        raise BenchError(
            "the evaluation views are NOT disjoint from the enrolment views: "
            f"{dis['checks']} — no accuracy number from this run means anything"
        )

    tmp: Optional[tempfile.TemporaryDirectory] = None
    if store_dir is None:
        tmp = tempfile.TemporaryDirectory(prefix="gawaah-bench-")
        directory = Path(tmp.name) / "shop"
    else:
        directory = Path(store_dir)

    try:
        store, er = enrol(enrol_caps, directory)
        rec = Recogniser(store, embed, theta=DEFAULT_THETA, phi=DEFAULT_PHI,
                         tau_mm=DEFAULT_TAU_MM, strict=True)
        if (rec.theta, rec.phi, rec.tau_mm) != (DEFAULT_THETA, DEFAULT_PHI,
                                                DEFAULT_TAU_MM):  # pragma: no cover
            raise BenchError("the bench is not running at the shipped gates")

        outcomes = evaluate(rec, eval_caps, eval_unmeasured, channel="colour",
                            time_embed=True)
        by_bucket = {b: [o for o in outcomes if o.bucket == b]
                     for b in (BUCKET_ENROLLED, BUCKET_REFUSED,
                               BUCKET_NEVER_TAUGHT)}
        open_set = [o for o in outcomes if o.open_set]

        headline = {
            "all": score(outcomes, "all evaluated items").as_dict(),
            "enrolled": score(by_bucket[BUCKET_ENROLLED],
                              "products in the gallery").as_dict(),
            "refused": score(by_bucket[BUCKET_REFUSED],
                             "refused by the collision guard").as_dict(),
            "never_taught": score(by_bucket[BUCKET_NEVER_TAUGHT],
                                  "never taught").as_dict(),
            "open_set": score(open_set, "open set (refused + never taught)").as_dict(),
            "gates": {"theta": rec.theta, "phi": rec.phi, "tau_mm": rec.tau_mm},
            "gates_are_shipped_defaults": True,
            "recogniser_stats": rec.stats(),
            "shortlist": shortlist_stats(outcomes, len(er.accepted)),
        }

        per_view = []
        for v in views:
            rows = [o for o in outcomes if o.view_id == v.view_id]
            enr = [o for o in rows if o.enrolled]
            m_enr = score(enr, v.view_id)
            per_view.append({
                "view_id": v.view_id, "stress": v.stress,
                **score(rows, v.view_id).as_dict(),
                "enrolled_n_items": m_enr.n_items,
                "enrolled_accuracy_on_decided":
                    round(m_enr.accuracy_on_decided_frac, 4),
                "enrolled_abstention_rate": round(m_enr.abstain_frac, 4),
                "enrolled_false_price_rate": round(m_enr.false_price_frac, 4),
                "enrolled_worst_margin": (
                    round(min((o.margin for o in enr if o.decided),
                              default=0.0), 4)),
                "enrolled_worst_top1": (
                    round(min((o.top1 for o in enr if o.decided),
                              default=0.0), 4)),
            })
        per_product = []
        for p in products:
            rows = [o for o in outcomes if o.sku_id == p.sku_id]
            if not rows:
                continue
            per_product.append({
                "sku_id": p.sku_id, "name": p.name,
                "bucket": _bucket(p.sku_id, er.accepted),
                "long_edge_mm": p.long_edge_mm,
                **score(rows, p.sku_id).as_dict(),
                "wrong_names": sorted({o.predicted for o in rows
                                       if o.verdict == FALSE_PRICE
                                       and o.predicted}),
                "abstain_reasons": sorted({o.reason for o in rows
                                           if not o.decided}),
            })
        rows_c, cols_c, mat = confusion(outcomes)

        cs_col = cosine_split(enrol_caps, eval_caps, er.accepted,
                              channel="colour")
        imps = top_impostors(enrol_caps, eval_caps, er.accepted,
                             channel="colour")

        hp = hard_pair_report(enrol_caps, outcomes, er.accepted, er.refused)

        # The LIVE loop: Brain._crop hands the embedder a GREY crop.
        grey_dir = directory.parent / "shop_grey"
        gstore, ger = enrol(enrol_caps, grey_dir, channel="grey")
        grec = Recogniser(gstore, embed, theta=DEFAULT_THETA, phi=DEFAULT_PHI,
                          tau_mm=DEFAULT_TAU_MM, strict=True)
        gouts = evaluate(grec, eval_caps, eval_unmeasured, channel="grey")
        cs_grey = cosine_split(enrol_caps, eval_caps, ger.accepted,
                               channel="grey")
        grey_headline = {
            "all": score(gouts, "grey, all").as_dict(),
            "enrolled": score([o for o in gouts if o.enrolled],
                              "grey, in the gallery").as_dict(),
            "open_set": score([o for o in gouts if o.open_set],
                              "grey, open set").as_dict(),
            "n_enrolled": len(ger.accepted),
            "enrolled_skus": list(ger.accepted),
            "refused_at_enrolment": [r[0] for r in ger.refused],
        }

        neg = negative_control(enrol_caps, eval_caps, eval_unmeasured,
                               directory.parent / "shop_blind")
        forced = _forced_collision(enrol_caps, eval_caps)
        lat = _latency_stats(outcomes)
        sweep = (gate_sweep(store, eval_caps, eval_unmeasured)
                 if with_sweep else [])
        prov = gate_provenance()

        result = BenchResult(
            ok=True,
            products=[{"sku_id": p.sku_id, "name": p.name, "taught": p.taught,
                       "w_mm": p.w_mm, "h_mm": p.h_mm,
                       "long_edge_mm": p.long_edge_mm,
                       "layout": p.layout, "price_paise": p.price_paise,
                       "note": p.note} for p in products],
            capture={"enrol": cap_stats_e, "eval": cap_stats_v,
                     "n_enrol_crops": len(enrol_caps),
                     "n_eval_crops": len(eval_caps),
                     "n_eval_unmeasured": len(eval_unmeasured),
                     "n_enrol_unmeasured": len(enrol_unmeasured),
                     "embed_dim": EMBED_DIM,
                     "quick": quick,
                     "wall_s": None},
            disjoint=dis,
            enrolment={
                "accepted": list(er.accepted),
                "refused": [{"sku_id": r[0], "collides_with": r[1],
                             "similarity": round(r[2], 4),
                             "footprint_delta_mm": (None if r[3] is None
                                                    else round(r[3], 2))}
                            for r in er.refused],
                "dim": er.dim, "catalog_bytes": er.catalog_bytes,
                "reopened_from_disk": list(er.reopened_skus),
                "store_dir": er.store_dir,
            },
            headline=headline,
            per_view=per_view,
            per_product=per_product,
            confusion_rows=rows_c, confusion_cols=cols_c, confusion_matrix=mat,
            cosines_colour=overlap(cs_col["same"], cs_col["different"]),
            cosines_grey=overlap(cs_grey["same"], cs_grey["different"]),
            impostors=imps,
            hard_pairs=hp,
            grey_headline=grey_headline,
            forced_collision=forced,
            negative_control=neg,
            latency=lat,
            sweep=sweep,
            frontier=sweep_frontier(sweep) if sweep else {"available": False},
            provenance=prov,
            environment={
                "python": sys.version.split()[0],
                "opencv": cv2.__version__,
                "numpy": np.__version__,
                "embed_dim": EMBED_DIM,
                "buffer": f"{BUF_W}x{BUF_H}",
                "render_px_per_mm": RENDER_PX_PER_MM,
                "noise_sigma": NOISE_SIGMA,
                "tilt_frac": TILT_FRAC,
            },
            outcomes=outcomes,
        )
        result.capture["wall_s"] = round(time.perf_counter() - t_start, 2)
        return result
    finally:
        if tmp is not None:
            tmp.cleanup()


# -------------------------------------------------------------------- report

def _md_table(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    out = ["| " + " | ".join(str(h) for h in header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def _pc(x: float) -> str:
    return f"{x * 100:.1f}%"


def _short(sku: str) -> str:
    return sku[:14]


def render_markdown(res: BenchResult) -> str:
    h = res.headline
    L: list[str] = []
    A = L.append

    A("# RECOGNISE — what the camera actually knows")
    A("")
    A("A held-out measurement of the photo-enrol -> recognise loop: teach one "
      "photo of a packet, then ask the counter to name and price it from views "
      "it has never seen.")
    A("")
    A(f"Generated by `tools/bench_recognise.py` in {res.capture['wall_s']} s. "
      f"Deterministic — every seed is fixed, so re-running reproduces these "
      f"numbers exactly.")
    A("")
    A("```")
    A("./.venv/bin/python tools/bench_recognise.py")
    A("```")
    A("")

    A("## The three numbers")
    A("")
    A("Accuracy alone is the easiest thing here to lie with: a recogniser buys "
      "it by abstaining on everything hard, and buys it again by never being "
      "asked about products it was not taught. So all three are reported "
      "together, over the same items, and the untaught packets are IN the "
      "denominator.")
    A("")
    rows = []
    for key, label in (("enrolled", "IN THE GALLERY"),
                       ("refused", "refused by the collision guard"),
                       ("never_taught", "never taught (intruders)"),
                       ("open_set", "open set = refused + never taught"),
                       ("all", "everything evaluated")):
        m = h[key]
        if not m["n_items"]:
            continue
        rows.append([label, m["n_items"], m["n_decided"],
                     _pc(m["top1_accuracy_on_decided"]),
                     _pc(m["abstention_rate"]),
                     f"**{_pc(m['false_price_rate'])}**",
                     m["n_false_price"]])
    A(_md_table(["set", "items", "decided", "top-1 acc (decided)",
                 "abstention rate", "FALSE-PRICE RATE", "n wrong"], rows))
    A("")
    g = h["gates"]
    A(f"Gates: **theta={g['theta']}, phi={g['phi']}, tau_mm={g['tau_mm']}** — "
      f"`gawaah.identity`'s shipped defaults, unmodified. "
      f"See [Gate provenance](#gate-provenance).")
    A("")
    A("A **false price** is any confident answer that is not the truth. That "
      "includes naming a packet that is not in the gallery: an untaught item "
      "billed as Parle-G is exactly the failure invariant 7 exists to prevent, "
      "so it is counted as an error and not excluded as out of scope.")
    A("")
    A("The buckets are decided by what is IN THE GALLERY, not by what the "
      "product set intended. A product the collision guard refused was never "
      "taught, however hard the shopkeeper tried, and scoring it as taught "
      "would blame recognition for an enrolment refusal.")
    A("")
    A(f"Items the SEGMENTER refused to measure are in the denominator too, as "
      f"abstentions: {res.capture['eval']['products_not_measured']} of "
      f"{h['all']['n_items']} "
      f"({', '.join(res.capture['eval']['unmeasured_reasons']) or 'none'}). "
      f"The counter cannot price what it cannot measure, so that is amber for a "
      f"real shopper, and dropping those rows would improve every rate on this "
      f"page for no reason but that they were inconvenient.")
    A("")

    sl = h.get("shortlist") or {}
    if sl.get("n"):
        A("### How much of that the tape measure gave away")
        A("")
        A(f"`identity.py` filters by FOOTPRINT before appearance is consulted. "
          f"If that filter routinely left a shortlist of one, the descriptor "
          f"would never have been asked a question and the accuracy above would "
          f"be the mat's rather than the embedder's. Measured over "
          f"{sl['n']} queries against a gallery of "
          f"{sl['n_skus_in_gallery']}: median shortlist "
          f"**{sl['median_shortlist']:.0f}**, mean {sl['mean_shortlist']}, max "
          f"{sl['max_shortlist']}; the footprint gate answered alone "
          f"(shortlist of exactly 1) on "
          f"**{_pc(sl['frac_answered_by_footprint_alone'])}** of them "
          f"({sl['n_answered_by_footprint_alone']} queries). "
          f"Of {h['all']['n_abstained']} abstentions in the run, "
          f"{h['recogniser_stats']['by_reason']['no_candidate_in_footprint']} "
          f"were `no_candidate_in_footprint` — so the footprint gate did the "
          f"SHORTLISTING and appearance did the DECIDING.")
        A("")

    A("## The product set")
    A("")
    A(f"{sum(1 for p in res.products if p['taught'])} taught products and "
      f"{sum(1 for p in res.products if not p['taught'])} untaught intruders. "
      "The footprints CLUSTER on purpose: five products share a 95 mm long "
      "edge, four share 70 mm, four share 38 mm — all far inside tau_mm = 4.0 "
      "of each other. Within a family the metric tiebreak admits every sibling, "
      "so appearance has to do the work. A bench where every product is a "
      "different size measures the tape measure, not the descriptor.")
    A("")
    A(_md_table(
        ["sku", "mm", "layout", "paise", "taught", "why it is here"],
        [[p["sku_id"], f"{p['w_mm']:.0f}x{p['h_mm']:.0f}", p["layout"],
          p["price_paise"], "yes" if p["taught"] else "NO",
          p["note"] or ""] for p in res.products]))
    A("")

    A("## Held out: how disjointness is guaranteed")
    A("")
    A(f"One enrolment view (`{res.disjoint['enrol_view']}`) per product; "
      f"{len(res.disjoint['eval_views'])} evaluation views "
      f"({', '.join(res.disjoint['eval_views'])}). "
      f"{res.disjoint['n_enrol_crops']} enrolled crops, "
      f"{res.disjoint['n_eval_crops']} evaluated crops, "
      f"{res.disjoint['n_shared_crop_hashes']} shared between them.")
    A("")
    A(_md_table(["check", "result"],
                [[k, "PASS" if v else "**FAIL**"]
                 for k, v in res.disjoint["checks"].items()]))
    A("")
    A("The last one is the check that cannot be argued with: every crop's bytes "
      "are SHA-256'd and the two hash sets are intersected. Not one evaluated "
      "pixel buffer is a buffer the gallery was built from. `run()` raises "
      "rather than publish an accuracy number if any of the five fails.")
    A("")
    A(_md_table(["view", "rot", "offset mm", "light", "crop err", "seed", "stress"],
                [[ENROL_VIEW.view_id, f"{ENROL_VIEW.rot_deg:+.0f}",
                  f"({ENROL_VIEW.dx_mm:+.0f},{ENROL_VIEW.dy_mm:+.0f})",
                  f"{ENROL_VIEW.gain_pct}%", f"{ENROL_VIEW.crop_pct:+d}%",
                  ENROL_VIEW.noise_seed, ENROL_VIEW.stress]]
                + [[v.view_id, f"{v.rot_deg:+.0f}",
                    f"({v.dx_mm:+.0f},{v.dy_mm:+.0f})", f"{v.gain_pct}%",
                    f"{v.crop_pct:+d}%", v.noise_seed, v.stress]
                   for v in EVAL_VIEWS]))
    A("")

    A("## Enrolment")
    A("")
    en = res.enrolment
    A(f"{len(en['accepted'])} accepted, {len(en['refused'])} refused by the "
      f"collision guard. The catalog was written to disk "
      f"({en['catalog_bytes']} bytes, dim {en['dim']}) and REOPENED from disk "
      f"before a single item was evaluated — the shopkeeper teaches on one "
      f"process and sells on another, so an in-memory store would be measuring "
      f"a path nobody uses.")
    A("")
    if en["refused"]:
        A(_md_table(["refused sku", "collides with", "cosine", "footprint delta mm"],
                    [[r["sku_id"], r["collides_with"], r["similarity"],
                      r["footprint_delta_mm"]] for r in en["refused"]]))
        A("")

    A("## Per view — which stress costs what")
    A("")
    A("Two blocks. The first is the gallery products only, which is what "
      "\"does it recognise my stock?\" means. The second includes the open set, "
      "which is dominated by the same handful of intruders in every view and "
      "so barely moves — that flatness is itself the finding: the open-set "
      "failures are a property of the CATALOG, not of the pose.")
    A("")
    A(_md_table(["view", "stress", "items", "top-1 acc (decided)", "abstention",
                 "false-price", "worst top1", "worst margin"],
                [[r["view_id"], r["stress"], r["enrolled_n_items"],
                  _pc(r["enrolled_accuracy_on_decided"]),
                  _pc(r["enrolled_abstention_rate"]),
                  _pc(r["enrolled_false_price_rate"]),
                  r["enrolled_worst_top1"], r["enrolled_worst_margin"]]
                 for r in res.per_view]))
    A("")
    A("Everything evaluated, same views:")
    A("")
    A(_md_table(["view", "items", "top-1 acc (decided)",
                 "abstention", "false-price rate"],
                [[r["view_id"], r["n_items"],
                  _pc(r["top1_accuracy_on_decided"]),
                  _pc(r["abstention_rate"]), _pc(r["false_price_rate"])]
                 for r in res.per_view]))
    A("")

    A("## Per product")
    A("")
    A(_md_table(["sku", "bucket", "views", "correct", "wrong",
                 "amber", "wrong names", "abstain reasons"],
                [[r["sku_id"], r["bucket"], r["n_items"],
                  r["n_correct"], r["n_false_price"], r["n_abstained"],
                  ", ".join(r["wrong_names"]) or "-",
                  ", ".join(r["abstain_reasons"]) or "-"]
                 for r in res.per_product]))
    A("")

    A("## Confusion matrix")
    A("")
    A("Rows are the truth, columns are what came back. `(abstain)` is a correct "
      "outcome for an untaught row and a miss for a taught one.")
    A("")
    hdr = ["truth \\ returned"] + [_short(c) for c in res.confusion_cols]
    body = []
    for i, r in enumerate(res.confusion_rows):
        cells = []
        for j, n in enumerate(res.confusion_matrix[i]):
            if n == 0:
                cells.append(".")
            elif res.confusion_cols[j] == r:
                cells.append(f"**{n}**")
            elif res.confusion_cols[j] == ABSTAIN_COL:
                cells.append(str(n))
            else:
                cells.append(f"!{n}!")
        body.append([_short(r)] + cells)
    A(_md_table(hdr, body))
    A("")
    A("`**n**` correct, `n` abstained, `!n!` a confident WRONG name.")
    A("")

    A("## Same-product vs different-product cosines")
    A("")
    A("Measured underneath the gates: every evaluated crop against every "
      "enrolled crop, split by whether they are the same product. This is the "
      "descriptor's own separation, before any threshold has had a chance to "
      "hide an overlap.")
    A("")
    rows = []
    for label, c in (("colour crop (the enrol desk's path)", res.cosines_colour),
                     ("grey crop (what Brain._crop feeds today)", res.cosines_grey)):
        rows.append([label, c["n_same"], f"{c['same_min']} / {c['same_p05']} / "
                     f"{c['same_median']}", c["n_different"],
                     f"{c['diff_median']} / {c['diff_p95']} / {c['diff_max']}",
                     c["gap_min_same_minus_max_diff"], c["roc_auc"]])
    A(_md_table(["channel", "n same", "same min/p05/median", "n diff",
                 "diff median/p95/max", "min(same)-max(diff)", "ROC AUC"], rows))
    A("")
    for label, c in (("colour", res.cosines_colour), ("grey", res.cosines_grey)):
        A(f"- **{label}**: "
          f"{c['n_different_at_or_above_worst_same']} of {c['n_different']} "
          f"different-product pairs "
          f"({_pc(c['frac_different_above_worst_same'])}) score at or above the "
          f"WORST same-product pair — that is the overlap, and it is the reason "
          f"no single threshold separates the two distributions "
          f"(`separable_by_one_threshold` = "
          f"{c['separable_by_one_threshold']}). At the shipped phi={c['phi_used']}: "
          f"{c['n_different_above_phi']} different-product pairs clear it "
          f"(each one a chance to be confidently wrong) and "
          f"{c['n_same_below_phi']} same-product pairs fall under it "
          f"(each one an abstention).")
    A("")

    A("### The worst impostors, named")
    A("")
    A("A distribution summary can hide the only thing that matters: which two "
      "packets the descriptor thinks are one packet. Highest different-product "
      "cosines in the run, worst first, one row per pair.")
    A("")
    A(_md_table(["query (truth)", "scored against", "cosine",
                 "fp delta mm", "in footprint gate", "clears phi"],
                [[r["query"], r["gallery"], r["cosine"],
                  r["footprint_delta_mm"],
                  "yes" if r["in_footprint_gate"] else "no",
                  "**YES**" if r["clears_phi"] else "no"]
                 for r in res.impostors]))
    A("")
    A("A pair that clears phi is only stopped by theta — the gap to the "
      "runner-up — and theta is the thinner of the two defences.")
    A("")

    A("## Hard pairs")
    A("")
    A("The pairs this set exists to be honest about. `expect` is what the "
      "DESIGN says, written down before anything was measured.")
    A("")
    A(_md_table(
        ["pair", "expect", "enrol cosine", "fp delta mm", "guard refused",
         "cross-billed", "amber", "verdict"],
        [[f"{r['a']} / {r['b']}", r["expect"], r["enrol_cosine"],
          r["footprint_delta_mm"], "yes" if r["guard_refused"] else "no",
          r["n_cross_billed"], r["n_amber"],
          ("COLLIDES (as designed)" if r["expect"] == MUST_COLLIDE
           else ("SEPARATED" if r["separated"] else "**NOT SEPARATED**"))]
         for r in res.hard_pairs]))
    A("")
    for r in res.hard_pairs:
        A(f"- **{r['a']} / {r['b']}** — {r['why']}")
        if r["has_open_set_member"] and r["expect"] != MUST_COLLIDE:
            A(f"  Judged differently: one member is not in the gallery, so "
              f"\"separated\" here means it was never named at ALL, not merely "
              f"that it was never named as its partner — an untaught packet "
              f"billed as some THIRD product is the same failure. Measured: "
              f"{r['n_open_set_named']} of its crops were named"
              + (f" (as {', '.join(r['open_set_named_as'])})."
                 if r["open_set_named_as"] else "."))
    A("")

    fc = res.forced_collision
    if fc.get("ran"):
        A("### The must-collide pair, forced")
        A("")
        A(f"The guard refuses `{fc['pair'][1]}`. Forcing both into one gallery "
          f"anyway and running the counter over {fc['n_views']} held-out views "
          f"gives {fc['n_named']} names and {fc['n_amber']} ambers "
          f"(reasons: {', '.join(fc['reasons'])}), worst margin "
          f"{fc['worst_margin']} against theta={DEFAULT_THETA}. That is the "
          f"trade the guard is making on the shopkeeper's behalf: refuse the "
          f"enrolment while he is still holding the packet, rather than sell "
          f"him a permanent amber at the till.")
        A("")

    nc = res.negative_control
    if nc.get("ran"):
        A("## Can this instrument read a zero?")
        A("")
        A("A bench that cannot detect a broken recogniser is not evidence of a "
          "working one. So the whole loop is re-run on the SAME crops with a "
          "deliberately blind descriptor — a constant vector, so every packet "
          "in the world embeds identically — and what the bench then reports is "
          "printed here.")
        A("")
        A(_md_table(["", "real descriptor", "BLIND descriptor"],
                    [["products enrolled", len(res.enrolment["accepted"]),
                      nc["n_accepted"]],
                     ["enrolments refused by the guard",
                      len(res.enrolment["refused"]),
                      nc["n_refused_by_the_guard"]],
                     ["top-1 accuracy on the gallery (decided)",
                      _pc(h["enrolled"]["top1_accuracy_on_decided"]),
                      _pc(nc["enrolled"]["top1_accuracy_on_decided"])],
                     ["abstention on the gallery",
                      _pc(h["enrolled"]["abstention_rate"]),
                      _pc(nc["enrolled"]["abstention_rate"])],
                     ["FALSE-PRICE RATE, everything",
                      _pc(h["all"]["false_price_rate"]),
                      _pc(nc["all"]["false_price_rate"])],
                     ["median shortlist after the footprint filter",
                      (h.get("shortlist") or {}).get("median_shortlist"),
                      nc["shortlist"].get("median_shortlist")],
                     ["queries answered by footprint alone",
                      _pc((h.get("shortlist") or {})
                          .get("frac_answered_by_footprint_alone", 0.0)),
                      _pc(nc["shortlist"]
                          .get("frac_answered_by_footprint_alone", 0.0))],
                     ["ROC AUC, same vs different",
                      res.cosines_colour["roc_auc"], nc["roc_auc"]]]))
        A("")
        A(f"**Read the third row twice.** A descriptor that cannot see anything "
          f"at all still scores "
          f"{_pc(nc['enrolled']['top1_accuracy_on_decided'])} top-1 accuracy on "
          f"its gallery, with "
          f"{_pc(nc['enrolled']['abstention_rate'])} abstentions. That is not a "
          f"bug in the scoring, it is the system behaving correctly: the "
          f"collision guard refuses every colliding enrolment, which thins the "
          f"gallery from {len(res.enrolment['accepted'])} products to "
          f"{nc['n_accepted']}; the footprint filter then leaves a shortlist of "
          f"exactly one for "
          f"{_pc(nc['shortlist'].get('frac_answered_by_footprint_alone', 0.0))} "
          f"of queries, and naming the only candidate is trivially right.")
        A("")
        A("**This is the argument for reporting more than one number.** "
          "\"Top-1 accuracy\" on its own is satisfied by a recogniser with no "
          "eyes. What catches the blind descriptor is the count of refused "
          "enrolments, the shortlist size, the AUC, and above all the "
          "false-price rate over EVERYTHING — which goes from "
          f"{_pc(h['all']['false_price_rate'])} to "
          f"{_pc(nc['all']['false_price_rate'])}.")
        A("")

    A("## Latency")
    A("")
    lt = res.latency
    A(_md_table(["measurement", "median ms", "p95 ms", "max ms", "n"],
                [["Recogniser.identify() per item (includes the embed)",
                  lt["identify_median_ms"], lt["identify_p95_ms"],
                  lt["identify_max_ms"], lt["n"]],
                 ["embed() alone, same crop, same loop",
                  lt["embed_median_ms"], lt["embed_p95_ms"], "-", lt["n"]]]))
    A("")
    scan = lt.get("gallery_scan_ms")
    A(f"Subtracting one from the other leaves {scan} ms for the linear scan "
      f"over {len(res.enrolment['accepted'])} gallery entries — at or below "
      f"this timer's own noise, which is the honest way to say it is free. The "
      f"descriptor is the entire cost. {lt['note']}")
    A("")

    A("## The live loop feeds GREY")
    A("")
    gh = res.grey_headline
    A("`gawaah/brain.py`'s `_crop` greyscales the rectified buffer before the "
      "embedder sees it, while the enrol desk's `oriented_crop_bgr` keeps the "
      "colour. Both were measured on identical crops from identical scenes.")
    A("")
    A(_md_table(["channel", "set", "items", "top-1 acc (decided)", "abstention",
                 "false-price rate"],
                [[ch, nm, m["n_items"], _pc(m["top1_accuracy_on_decided"]),
                  _pc(m["abstention_rate"]), _pc(m["false_price_rate"])]
                 for ch, nm, m in (
                     ("colour", "in the gallery", h["enrolled"]),
                     ("colour", "open set", h["open_set"]),
                     ("GREY", "in the gallery", gh["enrolled"]),
                     ("GREY", "open set", gh["open_set"]))]))
    A("")
    A(f"In grey the collision guard refused "
      f"{len(gh['refused_at_enrolment'])} enrolment(s) "
      f"({', '.join(gh['refused_at_enrolment']) or 'none'}) against "
      f"{len(res.enrolment['refused'])} in colour, and "
      f"{gh['n_enrolled']} products made it into the gallery against "
      f"{len(res.enrolment['accepted'])}. That difference is itself the "
      f"finding: in grey, packets that differ only in hue ARE the same "
      f"picture, so the guard has to refuse more of them.")
    A("")

    if res.sweep:
        A("## Gate sweep — was anything widened to flatter this?")
        A("")
        A("What every other choice of gate would have scored on the same items. "
          "The shipped default is marked. Read it as the price list: every "
          "step that buys fewer false prices is paid for in abstentions on "
          "real stock, and every step the other way is paid for in false "
          "prices.")
        A("")
        A(_md_table(["theta", "phi", "gallery acc (decided)",
                     "gallery abstention", "FALSE-PRICE RATE (all)", "n wrong"],
                    [[f"{r['theta']:.2f}"
                      + (" **<- shipped**" if r["is_shipped_default"] else ""),
                      f"{r['phi']:.2f}",
                      _pc(r["enrolled_accuracy_on_decided"]),
                      _pc(r["enrolled_abstention_rate"]),
                      _pc(r["false_price_rate"]), r["n_false_price"]]
                     for r in res.sweep]))
        A("")
        fr = res.frontier
        if fr.get("available"):
            sh = fr["shipped"]
            if fr["gallery_accuracy_is_constant"]:
                A(f"**Read the first column first: accuracy on the gallery is "
                  f"{_pc(fr['gallery_accuracy_value'])} at EVERY setting in the "
                  f"grid.** No gate anywhere in this range makes the counter "
                  f"confuse one taught packet for another. Every false price in "
                  f"the table is an OPEN-SET error — something not in the "
                  f"gallery being given a name — and the gates only trade those "
                  f"against abstentions on real stock.")
                A("")
            if fr["shipped_strictly_beaten"]:
                b = fr["best_alternatives"][0]
                A(f"**The shipped default is dominated on this data.** "
                  f"{fr['n_settings_that_strictly_beat_shipped']} setting(s) get "
                  f"fewer false prices with no loss of gallery accuracy and no "
                  f"extra gallery abstentions; the best is theta="
                  f"{b['theta']:.2f}, phi={b['phi']:.2f} at "
                  f"{b['n_false_price']} false prices against "
                  f"{sh['n_false_price']}.")
            else:
                A(f"**Nothing in the grid strictly beats the shipped default**: "
                  f"every setting with fewer false prices pays for them with "
                  f"more abstentions on real stock. theta={sh['theta']}, "
                  f"phi={sh['phi']} sits "
                  f"{'ON' if fr['shipped_is_on_the_pareto_front'] else 'OFF'} "
                  f"the Pareto front of (false prices, gallery abstention).")
            A("")
            A("The trade curve — nothing in the grid gets both fewer false "
              "prices and fewer gallery abstentions than these:")
            A("")
            A(_md_table(["theta", "phi", "false prices", "gallery abstention"],
                        [[f"{r['theta']:.2f}", f"{r['phi']:.2f}",
                          r["n_false_price"],
                          _pc(r["enrolled_abstention_rate"])
                          + (" **<- shipped**"
                             if (r["theta"] == sh["theta"]
                                 and r["phi"] == sh["phi"]) else "")]
                         for r in fr["pareto_front"]]))
            A("")
            pa = fr["phi_alone_span_false_prices"]
            ta = fr["theta_alone_span_false_prices"]
            pab = fr["phi_alone_span_gallery_abstention"]
            tab = fr["theta_alone_span_gallery_abstention"]
            A(f"**Both gates are real levers, and they are not "
              f"interchangeable.** Holding theta at its default and moving phi "
              f"across the grid moves the false-price count from {pa[1]} down "
              f"to {pa[0]}, at a gallery abstention cost of "
              f"{_pc(pab[0])} -> {_pc(pab[1])}. Holding phi at its default and "
              f"moving theta moves it from {ta[1]} down to {ta[0]}, costing "
              f"{_pc(tab[0])} -> {_pc(tab[1])}. phi only starts to bite ABOVE "
              f"0.8, because the impostors here score 0.7-0.9 — far above any "
              f"phi anyone would ship — and the worst genuine same-product "
              f"cosine is {res.cosines_colour['same_min']}, so a phi high "
              f"enough to reject the impostors is within touching distance of "
              f"rejecting real views of real stock. **This bench changed "
              f"nothing.** A threshold fitted to eighteen rendered rectangles "
              f"is exactly the overfit `gawaah/embedder.py` documents itself "
              f"having made once already, and this file does not own "
              f"`gawaah/identity.py`. The numbers are here so the next person "
              f"can decide with evidence instead of taste.")
            A("")

    A("## Gate provenance")
    A("")
    pv = res.provenance
    hist = pv["gate_line_history"]
    A(f"- The bench ran at theta={pv['bench_ran_at']['theta']}, "
      f"phi={pv['bench_ran_at']['phi']}, tau_mm={pv['bench_ran_at']['tau_mm']}, "
      f"which are `gawaah.identity`'s `DEFAULT_*` values read at import time — "
      f"not copies.")
    if hist.get("available"):
        A(f"- `git log -L` on the four lines that define the gates: "
          f"**{hist['n_commits_touching_the_gate_lines']} commit(s) have ever "
          f"touched them** — {'; '.join(hist['commits']) or 'none'}. They have "
          f"the value they were born with.")
    else:
        A("- git history was unavailable, so that check did not run.")
    A(f"- {pv['files_scanned']} modules in `gawaah/`, `tools/` and `tests/` were "
      f"AST-scanned for a call constructing an `Identifier`, `Recogniser` or "
      f"`ShopStore` with a non-default gate. "
      f"{len(pv['non_default_in_production_code'])} found outside `tests/`.")
    for hitrow in pv["non_default_in_production_code"]:
        A(f"  - **{hitrow['file']}:{hitrow['line']}** "
          f"{hitrow['constructor']}({hitrow['gate']}={hitrow['value']}) vs "
          f"default {hitrow['default']}")
    n_test_hits = len(pv["non_default_gate_call_sites"]) - len(
        pv["non_default_in_production_code"])
    A(f"- {n_test_hits} non-default gate(s) appear inside `tests/`, which is "
      f"where they belong: a test that shows phi is a real lever has to move "
      f"it.")
    A("")

    A("## Where this loses")
    A("")
    A(_where_this_loses(res))
    A("")

    A("## What is real here and what is not")
    A("")
    A("**Real:** the ArUco mat lock, the 840x1188 metric buffer at 2.83 px/mm, "
      "the millimetre measurement, `gawaah.embedder`'s classical descriptor, "
      "`gawaah.identity`'s footprint-then-appearance gates at their shipped "
      "values, `gawaah.shop_store` written to and reopened from disk, and "
      "`gawaah.recogniser`'s pricing. Zero model weights, zero network.")
    A("")
    A(f"**Not real:** the packets. They are flat rendered rectangles with "
      f"chunky colour blocks, warped through a synthetic {TILT_FRAC * 100:.0f}% "
      f"tilt with sigma={NOISE_SIGMA:.0f} Gaussian sensor noise. There is no "
      f"specular highlight, no shadow, no crushed corner, no crumpled foil, no "
      f"printed fine text and no motion blur. Real packaging is harder in every "
      f"one of those directions. **Every number above is an upper bound on "
      f"real-shelf behaviour, not a prediction of it.**")
    A("")
    A(f"Measurement fidelity in this run: worst long-edge error "
      f"{res.capture['eval']['worst_measure_err_mm']} mm, mean "
      f"{res.capture['eval']['mean_measure_err_mm']} mm against known truth; "
      f"{res.capture['eval']['unmatched_placements']} unmatched placement(s) "
      f"and {res.capture['eval']['products_not_measured']} item(s) the "
      f"segmenter refused to measure, across "
      f"{res.capture['eval']['n_scenes']} scenes.")
    A("")
    A(f"Crop provenance: `{res.capture['eval']['crop_source']}`.")
    A("")
    A("Nothing in this bench settles money. It reads integer paise out of a "
      "catalog to check that the right price came back with the right name, and "
      "it mints nothing, signs nothing and pays nothing (invariant 2).")
    A("")
    env = res.environment
    A(f"_Python {env['python']}, OpenCV {env['opencv']}, NumPy "
      f"{env['numpy']}, embedding dim {env['embed_dim']}._")
    A("")
    return "\n".join(L)


def _open_set_lever_note(res: BenchResult) -> str:
    """What the sweep says can actually be done about the open set — read off
    the measured trade curve rather than asserted."""
    fr = res.frontier
    if not fr.get("available"):
        return ("The gates are the only lever, and this run did not sweep them, "
                "so what they would buy is unmeasured here.")
    best = min(fr["pareto_front"], key=lambda r: r["n_false_price"])
    ship = fr["shipped"]
    mid = [r for r in fr["pareto_front"]
           if r["n_false_price"] < ship["n_false_price"]
           and r["enrolled_abstention_rate"] <= 0.10]
    m = min(mid, key=lambda r: r["n_false_price"]) if mid else None
    txt = (f"The gates ARE a lever and the sweep prices it: the shipped "
           f"theta={ship['theta']}/phi={ship['phi']} costs "
           f"{ship['n_false_price']} false prices at "
           f"{_pc(ship['enrolled_abstention_rate'])} abstention on real stock")
    if m is not None:
        txt += (f", and theta={m['theta']:.2f}/phi={m['phi']:.2f} would cut "
                f"that to {m['n_false_price']} for "
                f"{_pc(m['enrolled_abstention_rate'])}")
    txt += (f"; driving it to {best['n_false_price']} needs "
            f"theta={best['theta']:.2f}/phi={best['phi']:.2f} and "
            f"{_pc(best['enrolled_abstention_rate'])} abstention, which is a "
            f"different product.")
    return txt


def _where_this_loses(res: BenchResult) -> str:
    """Named failure conditions, built from THIS run's numbers rather than
    from a list of things that sound plausible."""
    out: list[str] = []
    h = res.headline

    # 1. the open set, always the headline weakness.
    u = h["open_set"]
    if u["n_items"]:
        if u["n_false_price"]:
            worst = sorted([o for o in res.outcomes
                            if o.open_set and o.verdict == FALSE_PRICE],
                           key=lambda o: -o.top1)
            names = []
            seen: set[str] = set()
            for o in worst:
                k = f"{o.sku_id} -> {o.predicted}"
                if k in seen:
                    continue
                seen.add(k)
                names.append(f"{k} (top1 {o.top1:.3f}, margin {o.margin:.3f})")
            out.append(
                f"**1. Packets that are not in the gallery.** This is the "
                f"failure that costs money and it is present here: "
                f"{u['n_false_price']} of {u['n_items']} open-set crops "
                f"({_pc(u['false_price_rate'])}) were confidently named and "
                f"priced. Worst cases: " + "; ".join(names[:5]) + ". "
                + _open_set_lever_note(res) +
                " But a gallery cannot know what it has never been shown, and "
                "on a real shelf the open set is everything the shopkeeper has "
                "not got round to teaching yet — which on day one is the whole "
                "shop.")
        else:
            out.append(
                f"**1. Packets that are not in the gallery.** On THIS set all "
                f"{u['n_items']} open-set crops abstained "
                f"({_pc(u['abstention_rate'])} abstention, "
                f"{_pc(u['false_price_rate'])} false price) — including "
                f"`intruder_lookalike`, which was built with family A's exact "
                f"palette and footprint. That is a result about a handful of "
                f"intruders, not a guarantee: open-set rejection has no bound "
                f"here, only a measurement, and an intruder drawn closer to a "
                f"taught packet would eventually clear phi.")

    # 2. the must-collide pair.
    mc = [r for r in res.hard_pairs if r["expect"] == MUST_COLLIDE]
    if mc:
        r = mc[0]
        out.append(
            f"**2. Products that differ only by a half-turn.** `{r['a']}` and "
            f"`{r['b']}` are the same packet with the print at the other end. "
            f"Placement reports angle in [0,180), and the descriptor folds "
            f"180-degree partners on purpose, so the two are the same picture: "
            f"enrolment cosine {r['enrol_cosine']}. The guard refused the "
            f"second enrolment "
            f"({'yes' if r['guard_refused'] else 'NO — and that is a bug'}), "
            f"which is the only safe answer, but it means a real shopkeeper "
            f"with two flavours in identical wrappers cannot teach both. He is "
            f"told at enrolment, not at the till.")

    # 3. the worst view, judged on the GALLERY, because pose is about stock.
    if res.per_view:
        most_amber = max(res.per_view,
                         key=lambda r: r["enrolled_abstention_rate"])
        thinnest = min(res.per_view, key=lambda r: r["enrolled_worst_top1"])
        narrow = min(res.per_view, key=lambda r: r["enrolled_worst_margin"])
        out.append(
            f"**3. Pose and light — the margin thins before the answer "
            f"breaks.** No view lost a gallery product to a WRONG name, so the "
            f"damage shows up as abstentions and as a shrinking margin rather "
            f"than as errors. Most abstentions: `{most_amber['view_id']}` "
            f"({most_amber['stress']}) at "
            f"{_pc(most_amber['enrolled_abstention_rate'])}. Weakest "
            f"same-product score: `{thinnest['view_id']}` "
            f"({thinnest['stress']}) bottoming out at top1 "
            f"{thinnest['enrolled_worst_top1']} against phi={DEFAULT_PHI} — "
            f"{round(thinnest['enrolled_worst_top1'] - DEFAULT_PHI, 3)} of "
            f"headroom left. Narrowest decision: `{narrow['view_id']}` at "
            f"margin {narrow['enrolled_worst_margin']} against "
            f"theta={DEFAULT_THETA}. The pipeline de-rotates through "
            f"minAreaRect and the descriptor folds the half-turn away, so what "
            f"is left is interpolation loss and the crop error — and a 3% "
            f"measurement error is already a 2.9 mm move against a 4.0 mm "
            f"footprint gate, i.e. the next size of crop error stops being an "
            f"appearance problem and becomes a no_candidate_in_footprint "
            f"abstention.")

    # 4. grey.
    gh = res.grey_headline
    ct, gt = h["enrolled"], gh["enrolled"]
    lost = [s for s in res.enrolment["accepted"]
            if s not in gh["enrolled_skus"]]
    if lost or gt["abstention_rate"] > ct["abstention_rate"] + 1e-9 \
            or gt["top1_accuracy_on_decided"] < ct["top1_accuracy_on_decided"] - 1e-9 \
            or gt["false_price_rate"] > ct["false_price_rate"] + 1e-9:
        out.append(
            f"**4. The live loop throws the colour away, and it costs a whole "
            f"product.** `Brain._crop` (`gawaah/brain.py`) greyscales the "
            f"rectified buffer before the embedder sees it; the enrol desk's "
            f"`oriented_crop_bgr` keeps the colour. The clearest consequence "
            f"is not a rate, it is a refusal: in grey the collision guard "
            f"rejects "
            f"{len(gh['refused_at_enrolment'])} enrolment(s) "
            f"({', '.join(gh['refused_at_enrolment'])}) against "
            f"{len(res.enrolment['refused'])} in colour"
            + (f", so `{', '.join(lost)}` cannot be taught at all — it is the "
               f"same size and the same layout as a packet already in the "
               f"gallery and differs only in hue, which in grey is not a "
               f"difference. " if lost else ". ") +
            f"On what survives: accuracy on decided items "
            f"{_pc(ct['top1_accuracy_on_decided'])} -> "
            f"{_pc(gt['top1_accuracy_on_decided'])}, abstention "
            f"{_pc(ct['abstention_rate'])} -> {_pc(gt['abstention_rate'])}, "
            f"and the descriptor's own separation falls (ROC AUC "
            f"{res.cosines_colour['roc_auc']} -> {res.cosines_grey['roc_auc']}, "
            f"different-product pairs above the worst same-product pair "
            f"{_pc(res.cosines_colour['frac_different_above_worst_same'])} -> "
            f"{_pc(res.cosines_grey['frac_different_above_worst_same'])}). "
            f"Whoever owns `gawaah/brain.py` should hand the embedder the BGR "
            f"crop; this file does not own it and did not change it.")
    else:
        out.append(
            f"**4. The live loop throws the colour away.** `Brain._crop` "
            f"greyscales the buffer before the embedder sees it. On this set "
            f"grey did not cost accuracy "
            f"({_pc(gt['top1_accuracy_on_decided'])} vs "
            f"{_pc(ct['top1_accuracy_on_decided'])}), but the cosine table "
            f"above shows the grey distributions overlap more "
            f"(ROC AUC {res.cosines_grey['roc_auc']} vs "
            f"{res.cosines_colour['roc_auc']}), so the margin it is deciding "
            f"on is thinner and the next same-shape different-colour pair is "
            f"where it goes.")

    # 5. the footprint gate doing the work.
    fam = {}
    for p in res.products:
        fam.setdefault(round(p["long_edge_mm"], 1), []).append(p["sku_id"])
    biggest = max(fam.items(), key=lambda kv: len(kv[1]))
    sl = h.get("shortlist") or {}
    out.append(
        f"**5. The tape measure carries part of this, and the share is "
        f"measured.** tau_mm = {DEFAULT_TAU_MM} shortlists by size BEFORE "
        f"appearance is consulted. On a catalog where every packet is a "
        f"different size that filter answers nearly every query on its own and "
        f"the descriptor is never tested. This set fights that — the largest "
        f"footprint family is {len(biggest[1])} products at {biggest[0]} mm "
        f"({', '.join(biggest[1])}) — and the measurement says it worked: "
        f"median shortlist {sl.get('median_shortlist')} of "
        f"{sl.get('n_skus_in_gallery')} gallery entries, with only "
        f"{_pc(sl.get('frac_answered_by_footprint_alone', 0.0))} of queries "
        f"answered by size alone. But that number is a property of THIS "
        f"catalog: a shop whose sizes happen to be distinct will read far "
        f"better here than its descriptor deserves, and a shop with twenty "
        f"38 mm sachets will read far worse.")

    # 6. scale.
    out.append(
        f"**6. Scale, and what is untested.** {len(res.products)} products, "
        f"one enrolment view each, "
        f"{res.disjoint['n_eval_crops']} evaluated crops. The gallery is "
        f"scanned linearly per identify (measured "
        f"{res.latency['identify_median_ms']} ms median, dominated by the "
        f"embed) — fine at 24 SKUs, untested at thousands. Multi-view "
        f"enrolment is untested: `Gallery.score` is best-of over views, so a "
        f"second photograph should help and nothing here proves by how much. "
        f"And every packet is rendered, so none of this is evidence about a "
        f"real shelf under a real lamp.")
    return "\n\n".join(out)


# --------------------------------------------------------------- html report
#
# The HTML is generated FROM the markdown string, not written a second time.
# Two hand-maintained renderings of a measurement is two places for a number to
# rot, and a measurement document that disagrees with itself is worth nothing.
# So there is one source of truth — render_markdown() — and one converter.

_HTML_ESCAPES = ((["&", "&amp;"]), (["<", "&lt;"]), ([">", "&gt;"]))


def _esc(s: str) -> str:
    for a, b in _HTML_ESCAPES:
        s = s.replace(a, b)
    return s


def _inline(s: str) -> str:
    """`code`, **bold**, and the confusion matrix's own two markers.

    `**7**` is a correct count and `!7!` is a confidently wrong name; in the
    markdown those are typographic, here they become semantic colour, which is
    the one thing an HTML rendering can add that a text one cannot.
    """
    out = _esc(s)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', out)
    out = re.sub(r"!(\d+)!", r'<span class="cell bad">\1</span>', out)
    out = re.sub(r"\*\*(\d+(?:\.\d+)?)\*\*",
                 r'<span class="cell ok">\1</span>', out)
    # A bolded percentage in these tables is always a FALSE-PRICE RATE, and a
    # false-price rate of 0% and one of 100% must not read the same at a
    # glance. Zero is the only good value, so zero is the only green one.
    out = re.sub(
        r"\*\*(\d+(?:\.\d+)?)%\*\*",
        lambda m: (f'<span class="cell {"ok" if float(m.group(1)) == 0.0 else "bad"}">'
                   f"{m.group(1)}%</span>"),
        out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    return out


def _md_to_html(md: str) -> str:
    lines = md.split("\n")
    html: list[str] = []
    i = 0
    para: list[str] = []

    def flush() -> None:
        if para:
            html.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            flush()
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(_esc(lines[i]))
                i += 1
            html.append("<pre><code>" + "\n".join(buf) + "</code></pre>")
            i += 1
            continue
        if line.startswith("|"):
            flush()
            block = []
            while i < len(lines) and lines[i].startswith("|"):
                block.append(lines[i])
                i += 1
            cells = [[c.strip() for c in row.strip().strip("|").split("|")]
                     for row in block]
            head = cells[0]
            body = cells[2:] if len(cells) > 1 else []
            t = ['<div class="tw"><table><thead><tr>']
            t += [f"<th>{_inline(c)}</th>" for c in head]
            t.append("</tr></thead><tbody>")
            for row in body:
                t.append("<tr>" + "".join(
                    f"<td>{_inline(c)}</td>" for c in row) + "</tr>")
            t.append("</tbody></table></div>")
            html.append("".join(t))
            continue
        if line.startswith("- "):
            flush()
            items: list[str] = []
            while i < len(lines) and (lines[i].startswith("- ")
                                      or (lines[i].startswith("  ")
                                          and lines[i].strip() and items)):
                if lines[i].startswith("- "):
                    items.append(lines[i][2:])
                else:
                    items[-1] += " " + lines[i].strip()
                i += 1
            html.append("<ul>" + "".join(f"<li>{_inline(x)}</li>"
                                         for x in items) + "</ul>")
            continue
        m = re.match(r"^(#{1,3}) (.*)$", line)
        if m:
            flush()
            lvl = len(m.group(1))
            slug = re.sub(r"[^a-z0-9]+", "-", m.group(2).lower()).strip("-")
            html.append(f'<h{lvl} id="{slug}">{_inline(m.group(2))}</h{lvl}>')
            i += 1
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        para.append(line.strip())
        i += 1
    flush()
    return "\n".join(html)


_CSS = """
:root{
  --ground:#f6f7f9; --surface:#fff; --raise:#fbfcfd;
  --ink:#161b22; --muted:#5a6472; --rule:#e1e5ea;
  --accent:#2b5a78; --accent-soft:#e8eef3;
  --ok:#1b6e52; --amber:#8f5b06; --bad:#a62a21;
  --mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;
  --serif:ui-serif,Charter,"Iowan Old Style",Georgia,serif;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --ground:#0e1116; --surface:#161a21; --raise:#1b2029;
    --ink:#e7ebf1; --muted:#93a0b0; --rule:#262c36;
    --accent:#83b6d6; --accent-soft:#1c2732;
    --ok:#4fbf92; --amber:#dfa33c; --bad:#f08a7e;
  }
}
:root[data-theme="dark"]{
  --ground:#0e1116; --surface:#161a21; --raise:#1b2029;
  --ink:#e7ebf1; --muted:#93a0b0; --rule:#262c36;
  --accent:#83b6d6; --accent-soft:#1c2732;
  --ok:#4fbf92; --amber:#dfa33c; --bad:#f08a7e;
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--ground); color:var(--ink);
  font-family:var(--serif); font-size:17px; line-height:1.62;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1060px; margin:0 auto; padding:clamp(24px,5vw,64px) clamp(16px,4vw,40px) 96px;
      display:flex; flex-direction:column; gap:28px}
.wrap>*{margin:0}
p,ul{max-width:70ch}
h1{
  font-family:var(--mono); font-size:clamp(24px,3.6vw,38px); line-height:1.18;
  font-weight:600; letter-spacing:-.02em; text-wrap:balance; max-width:22ch;
}
h2{
  font-family:var(--mono); font-size:13px; font-weight:600;
  letter-spacing:.14em; text-transform:uppercase; color:var(--accent);
  padding-top:26px; border-top:1px solid var(--rule); margin-top:18px;
  text-wrap:balance;
}
h3{font-family:var(--mono); font-size:15px; font-weight:600; letter-spacing:.01em;
   color:var(--ink); text-wrap:balance}
strong{font-weight:600}
code{font-family:var(--mono); font-size:.86em; background:var(--accent-soft);
     padding:.12em .38em; border-radius:3px; color:var(--ink)}
pre{background:var(--surface); border:1px solid var(--rule); border-radius:6px;
    padding:14px 16px; overflow-x:auto}
pre code{background:none; padding:0; font-size:13px}
ul{padding-left:1.1em; display:flex; flex-direction:column; gap:.5em}
li::marker{color:var(--muted)}
.tw{overflow-x:auto; border:1px solid var(--rule); border-radius:8px;
    background:var(--surface)}
table{border-collapse:collapse; width:100%; font-family:var(--mono);
      font-size:12.5px; font-variant-numeric:tabular-nums; line-height:1.45}
th,td{padding:8px 12px; text-align:left; border-bottom:1px solid var(--rule);
      white-space:nowrap; vertical-align:top}
thead th{background:var(--raise); color:var(--muted); font-weight:600;
         letter-spacing:.05em; text-transform:uppercase; font-size:10.5px;
         position:sticky; top:0}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover{background:var(--accent-soft)}
td:first-child,th:first-child{color:var(--ink)}
.cell{font-weight:700; font-variant-numeric:tabular-nums}
.cell.ok{color:var(--ok)}
.cell.bad{color:var(--bad)}
.readout{display:grid; gap:14px; grid-template-columns:repeat(auto-fit,minmax(210px,1fr))}
.tile{background:var(--surface); border:1px solid var(--rule); border-radius:10px;
      padding:18px 20px; display:flex; flex-direction:column; gap:6px}
.tile.flag{border-color:color-mix(in srgb,var(--bad) 45%,var(--rule))}
.tile .lab{font-family:var(--mono); font-size:10.5px; letter-spacing:.13em;
           text-transform:uppercase; color:var(--muted)}
.tile .val{font-family:var(--mono); font-size:34px; font-weight:600;
           letter-spacing:-.02em; line-height:1; font-variant-numeric:tabular-nums}
.tile .sub{font-family:var(--mono); font-size:11.5px; color:var(--muted)}
.tile.good .val{color:var(--ok)}
.tile.warn .val{color:var(--amber)}
.tile.flag .val{color:var(--bad)}
.gates{font-family:var(--mono); font-size:12px; color:var(--muted);
       display:flex; flex-wrap:wrap; gap:8px 18px}
.gates b{color:var(--ink); font-weight:600}
.lede{font-size:19px; line-height:1.55; color:var(--ink); max-width:62ch}
.foot{font-family:var(--mono); font-size:11.5px; color:var(--muted);
      border-top:1px solid var(--rule); padding-top:18px; max-width:none}
a{color:var(--accent)}
:focus-visible{outline:2px solid var(--accent); outline-offset:2px}
@media (prefers-reduced-motion:reduce){*{transition:none!important;
  animation:none!important}}
"""


def _tile(label: str, value: str, sub: str, kind: str = "") -> str:
    return (f'<div class="tile {kind}"><div class="lab">{_esc(label)}</div>'
            f'<div class="val">{_esc(value)}</div>'
            f'<div class="sub">{_esc(sub)}</div></div>')


def render_html(res: BenchResult) -> str:
    """The same report, rendered for a browser. Body converted from the very
    string `render_markdown()` produces; only the readout at the top is built
    separately, and it is built from the same `headline` dict."""
    md = render_markdown(res)
    # The h1 and the opening lede are re-composed above the readout; everything
    # from "## The three numbers" onward is the markdown verbatim.
    body = md.split("## The three numbers", 1)[1]
    h = res.headline
    g = res.headline["gates"]
    e, o, a = h["enrolled"], h["open_set"], h["all"]

    readout = (
        '<div class="readout">'
        + _tile("top-1 accuracy, decided",
                _pc(e["top1_accuracy_on_decided"]),
                f"products in the gallery, n={e['n_decided']}", "good")
        + _tile("abstention rate", _pc(e["abstention_rate"]),
                f"{e['n_abstained']} of {e['n_items']} said \"I don't know\"",
                "warn")
        + _tile("false-price rate, all",
                _pc(a["false_price_rate"]),
                f"{a['n_false_price']} of {a['n_items']} confidently wrong",
                "flag")
        + _tile("open set, never taught", _pc(o["false_price_rate"]),
                f"{o['n_false_price']} of {o['n_items']} given a name anyway",
                "flag")
        + "</div>")

    gates = (
        '<div class="gates">'
        f"<span>theta <b>{g['theta']}</b></span>"
        f"<span>phi <b>{g['phi']}</b></span>"
        f"<span>tau_mm <b>{g['tau_mm']}</b></span>"
        f"<span>gallery <b>{len(res.enrolment['accepted'])}</b> skus</span>"
        f"<span>held-out crops <b>{res.disjoint['n_eval_crops']}</b></span>"
        f"<span>shared with enrolment "
        f"<b>{res.disjoint['n_shared_crop_hashes']}</b></span>"
        f"<span>embed dim <b>{res.capture['embed_dim']}</b></span>"
        f"<span>{res.capture['wall_s']} s</span>"
        "</div>")

    lede = (
        '<p class="lede">Teach the counter one photograph of a packet, then ask '
        'it to name and price that packet from views it has never seen. These '
        'are the three numbers that follow, measured through the shipped code '
        'path at the shipped thresholds — and the fourth one, the open set, '
        'that the first three would otherwise hide.</p>')

    return (
        f"<title>RECOGNISE — what the camera actually knows</title>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<style>{_CSS}</style>"
        f'<div class="wrap">'
        f"<h1>RECOGNISE<br>what the camera actually knows</h1>"
        f"{lede}{readout}{gates}"
        f"<h2>The three numbers</h2>"
        f"{_md_to_html(body)}"
        f'<p class="foot">Generated by <code>tools/bench_recognise.py</code>. '
        f"Deterministic: every seed is fixed. Nothing on this page settles "
        f"money.</p>"
        f"</div>")


# ------------------------------------------------------------------------ cli

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Held-out evaluation of the photo-enrol -> recognise loop.")
    ap.add_argument("--quick", action="store_true",
                    help="a 7-product, 2-view subset; writes no report")
    ap.add_argument("--out", default=str(ROOT / "results" / "RECOGNISE.md"),
                    help="where to write the markdown report")
    ap.add_argument("--json", default=None,
                    help="also write the raw measurements here")
    ap.add_argument("--html", default=None,
                    help="also write the report as a self-contained page")
    ap.add_argument("--no-sweep", action="store_true",
                    help="skip the theta/phi sweep")
    ap.add_argument("--store-dir", default=None,
                    help="keep the enrolled catalog here instead of a temp dir")
    args = ap.parse_args(list(argv) if argv is not None else None)

    res = run(quick=args.quick, with_sweep=not args.no_sweep,
              store_dir=Path(args.store_dir) if args.store_dir else None)

    h = res.headline
    print(f"products      : {len(res.products)} "
          f"({sum(1 for p in res.products if p['taught'])} taught, "
          f"{sum(1 for p in res.products if not p['taught'])} untaught)")
    print(f"enrolled      : {len(res.enrolment['accepted'])} accepted, "
          f"{len(res.enrolment['refused'])} refused by the collision guard")
    print(f"crops         : {res.disjoint['n_enrol_crops']} enrol / "
          f"{res.disjoint['n_eval_crops']} eval, "
          f"{res.disjoint['n_shared_crop_hashes']} shared "
          f"(disjoint={res.disjoint['ok']})")
    print(f"gates         : theta={h['gates']['theta']} phi={h['gates']['phi']} "
          f"tau_mm={h['gates']['tau_mm']} (shipped defaults)")
    for k in ("enrolled", "refused", "never_taught", "open_set", "all"):
        m = h[k]
        print(f"{k:<14}: acc(decided) {_pc(m['top1_accuracy_on_decided'])}  "
              f"abstain {_pc(m['abstention_rate'])}  "
              f"FALSE-PRICE {_pc(m['false_price_rate'])} "
              f"({m['n_false_price']}/{m['n_items']})")
    print(f"grey (live)   : enrolled acc "
          f"{_pc(res.grey_headline['enrolled']['top1_accuracy_on_decided'])}  "
          f"abstain "
          f"{_pc(res.grey_headline['enrolled']['abstention_rate'])}  "
          f"FALSE-PRICE "
          f"{_pc(res.grey_headline['enrolled']['false_price_rate'])}")
    print(f"unmeasured    : "
          f"{res.capture['eval']['products_not_measured']} item(s) the "
          f"segmenter refused "
          f"({', '.join(res.capture['eval']['unmeasured_reasons']) or 'none'})"
          f" — counted as abstentions")
    print(f"cosines       : colour AUC {res.cosines_colour['roc_auc']}, "
          f"grey AUC {res.cosines_grey['roc_auc']}")
    print(f"latency       : {res.latency['identify_median_ms']} ms median "
          f"per item")
    print(f"gate audit    : {'clean' if res.provenance['clean'] else 'FINDINGS'}")
    print(f"wall          : {res.capture['wall_s']} s")

    if not args.quick:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_markdown(res), encoding="utf-8")
        print(f"wrote         : {out}")
    if args.html:
        hp = Path(args.html)
        hp.parent.mkdir(parents=True, exist_ok=True)
        hp.write_text(render_html(res), encoding="utf-8")
        print(f"wrote         : {hp}")
    if args.json:
        j = Path(args.json)
        j.parent.mkdir(parents=True, exist_ok=True)
        j.write_text(json.dumps(res.as_dict(), indent=2, sort_keys=True,
                                default=str), encoding="utf-8")
        print(f"wrote         : {j}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
