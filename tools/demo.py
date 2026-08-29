#!/usr/bin/env python3
"""GAWAAH — the demo runner. The thing a judge runs first.

    python tools/demo.py                     # the happy path, GREEN
    python tools/demo.py --scenario amber    # the counter abstains
    python tools/demo.py --scenario offline  # billing continues, nothing authorised
    python tools/demo.py --scenario mismatch # money landed, wrong number -> RED hold
    python tools/demo.py --scenario attack   # five forgeries, five refusals
    python tools/demo.py --slow              # paced for filming
    python tools/demo.py --json              # machine-readable, for CI

It runs on a clean clone with NO camera, NO credentials and NO network. Every
number it prints is produced by running the real modules:

  * the mat lock is a real `takhti.PlaneEngine.detect` on a synthetic camera
    frame we render and warp ourselves — real ArUco detection, real homography,
    real scale/perspective gates;
  * the millimetres are real `placement.PlacementDetector` measurements on the
    real rectified buffer;
  * the SKU decisions (and the abstentions) are real `identity.Identifier`
    verdicts over descriptors computed from those pixels;
  * the exit crossings are real `sellevent.CentroidTracker` + `LineZone`;
  * the money is real `paisa` over real HTTP, which re-runs the crossing
    predicate server-side before it mints anything (INVARIANT 5);
  * the webhook is signed by `rzp_sim` with HMAC-SHA256 over the raw bytes and
    adjudicated by the one and only `webhook.GreenPredicate` (INVARIANT 2);
  * every step appends to one `ledger.Ledger` hash chain, and the run ends by
    re-verifying that chain from genesis with the standalone verifier.

What it does NOT prove is written down, at length, in tools/README_DEMO.md.
The one-sentence version: there is no camera and no Razorpay here. The frames
are drawn by this file and the webhooks are signed by a simulator that marks
every body it emits with `_gawaah_sim: true`.

INVARIANT 1 note: this file is inside `tools/`, which `tools/lint_no_float.py`
scans for floats reaching money-named identifiers. Millimetres, pixels and
seconds are floats here and are meant to be. Money never is: every rupee in
this file arrives as an int from `money.paise` and leaves through
`money.to_rupees_str`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Sequence

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from gawaah import kernel as _kernel                                # noqa: E402
from gawaah.clock import VirtualClock                               # noqa: E402
from gawaah.kernel import Kernel                                    # noqa: E402
from gawaah.ledger import Ledger, verify as verify_ledger           # noqa: E402
from gawaah.money import paise, to_rupees_str                       # noqa: E402
from gawaah.paisa import (                                          # noqa: E402
    PaisaConfig,
    PaisaRefusal,
    PaisaService,
    create_app,
)
from gawaah.rzp_sim import RazorpaySim, serialize_body              # noqa: E402
from gawaah.sellevent import (                                      # noqa: E402
    MAT_H_MM,
    MAT_W_MM,
    CentroidTracker,
    LineZone,
)
from gawaah.session import Placement as LinePlacement               # noqa: E402
from gawaah.session import Session, State, Verdict                  # noqa: E402

SCENARIOS = ("happy", "amber", "offline", "mismatch", "attack")

#: Where the demo's synthetic counter session is expected to end up. The run
#: exits non-zero if it lands anywhere else, so this file is also a test.
EXPECTED_STATE = {
    "happy": "PAID",
    "amber": "FROZEN_TOTAL",
    "offline": "PAID",
    "mismatch": "AMOUNT_MISMATCH",
    "attack": "PAID",
}

EXIT_OK = 0
EXIT_WRONG_STATE = 1
EXIT_LEDGER_BROKEN = 2

WEBHOOK_SECRET = "whsec_gawaah_demo_only_never_a_real_secret"
KEY_SECRET = "rzpsecret_gawaah_demo_only_never_a_real_secret"

WIDTH = 78


# =============================================================================
# 0.  Determinism
# =============================================================================
#
# `kernel.new_nonce` draws 128 bits from the OS CSPRNG, which is exactly right
# in production and fatal to a byte-reproducible demo: the nonce lands in the
# audit lines, so the chain head would differ on every run and `--seed` would
# mean nothing. The demo replaces it with a seeded, stated substitute. This is
# the ONLY behaviour the demo changes, it is announced in the header of every
# run, and `--json` reports it as `deterministic_nonce: true`.


def install_seeded_nonce(seed: int) -> Callable[[], str]:
    """Swap the kernel's CSPRNG nonce for a seeded one. Returns the original."""
    original = _kernel.new_nonce
    counter = {"n": 0}

    def seeded_nonce() -> str:
        counter["n"] += 1
        material = f"gawaah-demo|{seed}|{counter['n']}".encode("utf-8")
        return "gwn_" + hashlib.sha256(material).hexdigest()[:32]

    _kernel.new_nonce = seeded_nonce
    return original


# =============================================================================
# 1.  Terminal rendering
# =============================================================================

_ANSI = {
    "reset": "\x1b[0m",
    "bold": "\x1b[1m",
    "dim": "\x1b[2m",
    "red": "\x1b[31m",
    "green": "\x1b[32m",
    "amber": "\x1b[33m",
    "blue": "\x1b[34m",
    "cyan": "\x1b[36m",
    "grey": "\x1b[90m",
    "bgreen": "\x1b[1;32m",
    "bred": "\x1b[1;31m",
    "bamber": "\x1b[1;33m",
    "bcyan": "\x1b[1;36m",
    "rgreen": "\x1b[7;32m",
    "rred": "\x1b[7;31m",
    "ramber": "\x1b[7;33m",
}

_UNI = {
    "tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "h": "─", "v": "│",
    "dtl": "╔", "dtr": "╗", "dbl": "╚", "dbr": "╝", "dh": "═", "dv": "║",
    "lt": "├", "rt": "┤", "dot": "·", "rupee": "₹", "arrow": "→",
    "block": "█", "half": "▒", "up": "▀", "down": "▄", "mark": "▪",
    "tick": "✓", "cross": "✗", "bullet": "•",
}
_ASCII = {
    "tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|",
    "dtl": "+", "dtr": "+", "dbl": "+", "dbr": "+", "dh": "=", "dv": "|",
    "lt": "+", "rt": "+", "dot": ".", "rupee": "Rs", "arrow": "->",
    "block": "#", "half": ":", "up": '"', "down": "_", "mark": "*",
    "tick": "y", "cross": "x", "bullet": "*",
}

Seg = tuple[str, Optional[str]]

#: Transliteration used when the output stream cannot carry the characters this
#: file would rather use. Applied at SEGMENT CONSTRUCTION, before any width is
#: measured, because folding after the padding is computed is how a box ends up
#: one column wider than its own frame.
_FOLD = str.maketrans(
    {
        "·": ".", "—": "-", "–": "-", "…": "...", "→": "->", "₹": "Rs",
        "▪": "*", "✓": "y", "✗": "x", "≈": "~", "×": "x", "≥": ">=", "≤": "<=",
        "’": "'", "‘": "'", "“": '"', "”": '"', " ": " ",
    }
)

#: Set once by `Term.__init__`. A module global rather than a parameter because
#: `s()` is called several hundred times and threading a terminal through every
#: one of them would be noise around a single boolean.
_ASCII_ONLY = False


def fold(text: str) -> str:
    """ASCII-fold when the stream demands it. A no-op otherwise."""
    if not _ASCII_ONLY:
        return text
    return text.translate(_FOLD).encode("ascii", "replace").decode("ascii")


def s(text: Any, style: Optional[str] = None) -> Seg:
    """One styled run of text. Width is measured on the text, never the codes."""
    return (fold(str(text)), style)


def seg_width(segs: Sequence[Seg]) -> int:
    return sum(len(t) for t, _ in segs)


class Term:
    """The renderer. Box drawing and colour on a TTY, plain text everywhere else."""

    def __init__(
        self,
        stream: Any,
        *,
        colour: bool,
        unicode_ok: bool,
        pace_s: float = 0.0,
        silent: bool = False,
    ) -> None:
        global _ASCII_ONLY
        _ASCII_ONLY = not unicode_ok
        self.stream = stream
        self.colour = colour
        self.g = _UNI if unicode_ok else _ASCII
        self.pace_s = pace_s
        self.silent = silent

    # -- primitives ------------------------------------------------------

    def paint(self, text: str, style: Optional[str]) -> str:
        if not self.colour or not style:
            return text
        codes = "".join(_ANSI[p] for p in style.split() if p in _ANSI)
        return f"{codes}{text}{_ANSI['reset']}" if codes else text

    def emit(self, segs: Sequence[Seg] = ()) -> None:
        if self.silent:
            return
        self.stream.write("".join(self.paint(t, st) for t, st in segs) + "\n")
        self.stream.flush()
        if self.pace_s > 0.0:
            time.sleep(self.pace_s)

    @staticmethod
    def clip(segs: Sequence[Seg], width: int) -> list[Seg]:
        """Trim a row to `width` visible characters. A box that breaks its own
        frame is worse than a truncated sentence."""
        ell = fold("…")
        out: list[Seg] = []
        left = width
        for text, style in segs:
            if left <= 0:
                break
            if len(text) <= left:
                out.append((text, style))
                left -= len(text)
            else:
                out.append((text[: max(0, left - len(ell))] + ell, style))
                left = 0
        return out

    def blank(self) -> None:
        self.emit()

    def pause(self, factor: float = 1.0) -> None:
        if self.pace_s > 0.0 and not self.silent:
            time.sleep(self.pace_s * 12.0 * factor)

    # -- boxes -----------------------------------------------------------

    def banner(self, left: str, right: str) -> None:
        g = self.g
        inner = WIDTH - 2
        self.emit([s(g["dtl"] + g["dh"] * inner + g["dtr"], "bcyan")])
        pad = inner - 2 - len(left) - len(right)
        self.emit(
            [
                s(g["dv"], "bcyan"),
                s(" "),
                s(left, "bold"),
                s(" " * max(1, pad)),
                s(right, "cyan"),
                s(" "),
                s(g["dv"], "bcyan"),
            ]
        )
        self.emit([s(g["dbl"] + g["dh"] * inner + g["dbr"], "bcyan")])

    def card_open(self, title: str, style: str = "bcyan") -> None:
        g = self.g
        head = f"{g['tl']}{g['h']} {title} "
        self.emit([s(head + g["h"] * max(0, WIDTH - len(head) - 1) + g["tr"], style)])

    def card_close(self, style: str = "bcyan") -> None:
        g = self.g
        self.emit([s(g["bl"] + g["h"] * (WIDTH - 2) + g["br"], style)])

    def card_rule(self, style: str = "grey") -> None:
        g = self.g
        self.emit(
            [
                s(g["v"], "bcyan"),
                s(" " + g["h"] * (WIDTH - 4) + " ", style),
                s(g["v"], "bcyan"),
            ]
        )

    def row(self, segs: Sequence[Seg] = (), style: str = "bcyan") -> None:
        g = self.g
        inner = WIDTH - 4
        if seg_width(segs) > inner:
            segs = self.clip(segs, inner)
        pad = max(0, inner - seg_width(segs))
        self.emit(
            [s(g["v"], style), s("  ")] + list(segs) + [s(" " * pad), s(g["v"], style)]
        )

    def kv(
        self,
        label: str,
        value: Sequence[Seg],
        *,
        leader: int = 26,
        note: str = "",
    ) -> None:
        dots = max(1, leader - len(label) - 1)
        segs: list[Seg] = [
            s(label),
            s(" " + self.g["dot"] * dots + " ", "grey"),
        ]
        segs.extend(value)
        if note:
            segs.append(s("   " + note, "grey"))
        self.row(segs)

    def note(self, text: str, style: str = "grey") -> None:
        for chunk in _wrap(text, WIDTH - 6):
            self.row([s(chunk, style)])

    def foot(self, segs: Sequence[Seg]) -> None:
        """The dim line under a card that carries the audit-chain head."""
        self.emit([s("   ")] + list(segs))


def _wrap(text: str, width: int) -> list[str]:
    raw = fold(text).split()
    words: list[str] = []
    for w in raw:
        while len(w) > width:          # an unbreakable token (a path, a hash)
            words.append(w[:width])
            w = w[width:]
        words.append(w)
    lines, cur = [], ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur = cur + " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def money_seg(amount_paise: int, style: str = "bold") -> list[Seg]:
    """Rupees, right-aligned in a fixed field so columns line up."""
    return [s(f"{to_rupees_str(int(amount_paise)):>10}", style)]


# =============================================================================
# 2.  The synthetic counter — pixels
# =============================================================================
#
# Everything here draws an image. It is the ONLY part of the demo that is
# make-believe: the camera. The modules that consume these pixels are the real
# ones, and they are not told the frame is synthetic.

PX_PER_MM_PRINT = 4.0
FRAME_W, FRAME_H = 1280, 1600
TILT = 16.0


@dataclass(frozen=True)
class Good:
    """One physical thing on the counter, as this file draws it."""

    key: str
    label: str
    sku: Optional[str]                  # None == never enrolled
    centre_mm: tuple[float, float]
    size_mm: tuple[float, float]        # (short edge, long edge)
    pattern: str
    crosses: bool


#: Positions chosen so no two contours come within a morphological close of one
#: another and nothing overlaps a printed marker — otherwise the demo would be
#: exercising a merged-contour refusal it did not mean to.
GOODS: tuple[Good, ...] = (
    Good("rice", "rice 5 kg", "sku-rice-5kg", (75.0, 160.0), (110.0, 200.0), "h", True),
    Good("dal", "dal 1 kg", "sku-dal-1kg", (210.0, 130.0), (90.0, 140.0), "v", True),
    Good("soap", "soap bar", "sku-soap", (250.0, 310.0), (60.0, 95.0), "c", False),
    Good("sachet", "unknown sachet", None, (160.0, 322.0), (40.0, 95.0), "d", True),
)

#: The server-side price book, keyed by SKU. paisa owns it; the counter keeps a
#: read-only mirror and may only ever AGREE with it.
PRICES: dict[str, int] = {
    "sku-rice-5kg": 21450,      # Rs 214.50
    "sku-dal-1kg": 9900,        # Rs  99.00
    "sku-soap": 4500,           # Rs  45.00
}

#: Line ids are `<sku>#<n>`: unique per physical placement, so two bags of the
#: same rice are two lines, while the price is still a property of the SKU.
LINE_SEP = "#"
UNKNOWN_SKU_ID = "unidentified"


class SkuPriceBook:
    """A `paisa.PriceBook` that prices the SKU inside a per-placement line id.

    `sku-rice-5kg#1` and `sku-rice-5kg#2` are two lines and one price; the
    abstained line `unidentified#4` has no SKU and therefore no price, which is
    AMBER and is excluded from the total. `None` here is the whole of
    INVARIANT 7 at the money boundary: it is never a guess and never zero.
    """

    def __init__(self, prices: dict[str, int]) -> None:
        self._by_sku = {str(k): int(paise(v)) for k, v in prices.items()}

    def price_paise(self, item_id: str) -> Optional[int]:
        sku = str(item_id).split(LINE_SEP, 1)[0]
        return self._by_sku.get(sku)

    def __len__(self) -> int:
        return len(self._by_sku)


def _import_cv() -> tuple[Any, Any]:
    import cv2
    import numpy as np

    return cv2, np


def _tile(w_px: int, h_px: int, pattern: str, ppm: float) -> Any:
    """The printed face of one good. Distinct patterns, so a descriptor computed
    from the pixels actually carries information about which item it is."""
    cv2, np = _import_cv()
    t = np.full((max(1, h_px), max(1, w_px)), 235, np.uint8)
    step = max(3, int(round(6.0 * ppm / 4.0)))
    if pattern == "h":
        for y in range(0, h_px, step * 2):
            t[y:min(y + step, h_px), :] = 40
    elif pattern == "v":
        for x in range(0, w_px, step * 2):
            t[:, x:min(x + step, w_px)] = 40
    elif pattern == "c":
        for yi, y in enumerate(range(0, h_px, step)):
            for xi, x in enumerate(range(0, w_px, step)):
                if (xi + yi) % 2 == 0:
                    t[y:min(y + step, h_px), x:min(x + step, w_px)] = 40
    elif pattern == "d":
        for k in range(-h_px, w_px, step * 2):
            cv2.line(t, (k, 0), (k + h_px, h_px), 40, step)
    cv2.rectangle(t, (0, 0), (w_px - 1, h_px - 1), 20, 2)
    return t


def _mat_image(goods: Iterable[Good], ppm: float = PX_PER_MM_PRINT) -> Any:
    """The printed TAKHTI with these goods resting on it."""
    from gawaah.takhti import render_takhti

    img = render_takhti(ppm).copy()
    for gd in goods:
        cx, cy = gd.centre_mm
        w, h = gd.size_mm
        x0, x1 = int(round((cx - w / 2) * ppm)), int(round((cx + w / 2) * ppm))
        y0, y1 = int(round((cy - h / 2) * ppm)), int(round((cy + h / 2) * ppm))
        img[y0:y1, x0:x1] = _tile(x1 - x0, y1 - y0, gd.pattern, ppm)
    return img


def _camera_frame(mat_img: Any, tilt: float = TILT) -> Any:
    """Put the sheet on a counter and photograph it slightly off-nadir."""
    cv2, np = _import_cv()
    h, w = mat_img.shape
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)
    dst = np.array(
        [
            [180.0 + tilt, 120.0 + tilt * 0.4],
            [FRAME_W - 180.0 + tilt * 0.3, 120.0 - tilt * 0.2],
            [FRAME_W - 180.0 - tilt * 0.5, FRAME_H - 120.0 + tilt * 0.1],
            [180.0 - tilt * 0.2, FRAME_H - 120.0 - tilt * 0.3],
        ],
        np.float32,
    )
    m = cv2.getPerspectiveTransform(src, dst)
    warp = cv2.warpPerspective(
        mat_img, m, (FRAME_W, FRAME_H), flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=200,
    )
    mask = cv2.warpPerspective(
        np.full((h, w), 255, np.uint8), m, (FRAME_W, FRAME_H),
        flags=cv2.INTER_NEAREST,
    )
    counter = np.full((FRAME_H, FRAME_W), 200, np.uint8)
    return np.where(mask > 0, warp, counter).astype(np.uint8)


def _crop_mm(
    rectified: Any, centre_mm: Sequence[float], long_mm: float, short_mm: float
) -> Any:
    """The axis-aligned crop of one placement, in the rectified metric buffer."""
    from gawaah.takhti import PX_PER_MM_X, PX_PER_MM_Y

    cx = centre_mm[0] * PX_PER_MM_X
    cy = centre_mm[1] * PX_PER_MM_Y
    hw = short_mm * PX_PER_MM_X / 2.0
    hh = long_mm * PX_PER_MM_Y / 2.0
    x0 = max(0, int(round(cx - hw)))
    x1 = min(rectified.shape[1], int(round(cx + hw)))
    y0 = max(0, int(round(cy - hh)))
    y1 = min(rectified.shape[0], int(round(cy + hh)))
    return rectified[y0:y1, x0:x1]


def descriptor(crop: Any) -> Any:
    """An 8x8 mean-removed intensity descriptor, L2-normalised.

    Crude on purpose and stated as such: it is a real function of real pixels,
    it runs in microseconds with no model weights anywhere, and the point of
    the demo is what `identity.Identifier` does with a score — not how the
    score was produced. A production embedder is injected the same way.
    """
    cv2, np = _import_cv()
    small = cv2.resize(
        np.asarray(crop, np.float32), (8, 8), interpolation=cv2.INTER_AREA
    )
    v = small.flatten()
    v = v - v.mean()
    n = float(np.linalg.norm(v))
    return v if n == 0.0 else v / n


# =============================================================================
# 3.  The synthetic counter — motion
# =============================================================================

APPROACH_FRAMES = 14
OUT_YS = (406.0, 409.0, 412.0, 415.0)
N_FRAMES = APPROACH_FRAMES + len(OUT_YS)
EXIT_Y_MM = MAT_H_MM - 18.0


def crossing_track(x_mm: float, y0_mm: float) -> list[Optional[tuple[float, float]]]:
    """A centroid slid out to the customer: it approaches, then holds past the
    line long enough for the debounce to commit it."""
    ys = [
        y0_mm + (396.0 - y0_mm) * i / (APPROACH_FRAMES - 1)
        for i in range(APPROACH_FRAMES)
    ]
    return [(x_mm, y) for y in ys] + [(x_mm, y) for y in OUT_YS]


def resting_track(x_mm: float, y_mm: float) -> list[Optional[tuple[float, float]]]:
    return [(x_mm, y_mm)] * N_FRAMES


def occlude(
    track: list[Optional[tuple[float, float]]], hidden: Sequence[int]
) -> list[Optional[tuple[float, float]]]:
    """A hand passes over the item for a couple of frames. Nothing else changes."""
    return [None if i in set(hidden) else p for i, p in enumerate(track)]


# =============================================================================
# 4.  Results carried between beats
# =============================================================================


@dataclass
class Seen:
    """What the counter believes about one good, after perception."""

    good: Good
    slot: int = 0
    track_id: Optional[int] = None
    centre_mm: tuple[float, float] = (0.0, 0.0)
    long_edge_mm: Optional[float] = None
    short_edge_mm: Optional[float] = None
    area_mm2: Optional[float] = None
    sku: Optional[str] = None
    top1: float = 0.0
    margin: float = 0.0
    ident_reason: str = ""
    price_paise: Optional[int] = None
    committed: bool = False
    commit_frame: Optional[int] = None
    strip: str = ""
    path_mm: list[tuple[float, float]] = field(default_factory=list)

    @property
    def amber(self) -> bool:
        return self.price_paise is None

    @property
    def item_id(self) -> str:
        """Unique per physical placement, and it carries its own SKU."""
        return f"{self.sku or UNKNOWN_SKU_ID}{LINE_SEP}{self.slot}"


@dataclass
class Step:
    n: int
    name: str
    head: str
    lines: int
    detail: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# 5.  The demo
# =============================================================================


class Demo:
    def __init__(self, args: argparse.Namespace, term: Term) -> None:
        self.args = args
        self.t = term
        self.scenario: str = args.scenario
        self.seed: int = args.seed
        self.steps: list[Step] = []
        self.notes: list[str] = []
        self.refusals: list[dict[str, Any]] = []
        self.seen: list[Seen] = []
        self.mat: dict[str, Any] = {}
        self.mint: dict[str, Any] = {}
        self.green: dict[str, Any] = {}
        self.crossing: dict[str, Any] = {}
        #: Scenario-specific evidence that is rendered in the terminal and would
        #: otherwise be invisible to --json (and therefore unassertable by CI).
        self.episode: dict[str, Any] = {}
        self.step_n = 0
        self.card_no = 0
        self.geometry_h: list[list[float]] = []
        self.geometry_corners: list[list[float]] = []
        self.untracked_by_frame: list[list[tuple[float, float]]] = []
        self.vision_ok = True
        self.vision_note = ""
        self._pending_foot: Optional[int] = None

        # ---- the counter's own audit chain, shared with paisa ----
        self.out_dir: str = args.out
        if os.path.isdir(self.out_dir):
            shutil.rmtree(self.out_dir)
        os.makedirs(self.out_dir, exist_ok=True)
        self.ledger_path = os.path.join(self.out_dir, "audit.jsonl")

        self.clock = VirtualClock(step_ms=40)
        self.ledger = Ledger(self.ledger_path)
        self.kernel = Kernel(
            os.path.join(self.out_dir, "kernel.db"), self.clock, self.ledger
        )
        self.config = PaisaConfig(
            mode="sim",
            key_id="rzp_test_GAWAAHDEMO",
            key_secret=KEY_SECRET,
            webhook_secret=WEBHOOK_SECRET,
            seed=self.seed,
        )
        self.sim = RazorpaySim(
            webhook_secret=self.config.effective_webhook_secret,
            clock=self.clock,
            seed=self.seed,
        )
        self.svc = PaisaService(
            clock=self.clock,
            ledger=self.ledger,
            kernel=self.kernel,
            gateway=self.sim,
            config=self.config,
            price_book=SkuPriceBook(dict(PRICES)),
        )
        self.http, self.transport = _make_client(self.svc)

        # the counter's own state machine. It never sees a secret.
        self.session = Session(self.clock, self.ledger)
        self.session_id = self.session.session_id

    # -- plumbing --------------------------------------------------------

    def close(self) -> None:
        try:
            self.kernel.close()
        except Exception:
            pass

    def step(self, name: str, **detail: Any) -> Step:
        self.step_n += 1
        st = Step(self.step_n, name, self.ledger.head, self.ledger.count, detail)
        self.steps.append(st)
        return st

    def card(
        self, title: str, style: str = "bcyan", *, step: bool = True, suffix: str = ""
    ) -> None:
        """Open a numbered beat card. Numbering is derived, never hand-written,
        so a scenario that inserts a beat cannot end up counting 6, 7, 5, 6, 8."""
        if step:
            self.card_no += 1
        self.t.card_open(f"{self.card_no}{suffix}  {self.t.g['bullet']}  {title}", style)

    def end_card(self, style: str = "bcyan") -> None:
        """Close the card a `deliver()` was written inside, then stamp the chain."""
        self.t.card_close(style)
        pending, self._pending_foot = self._pending_foot, None
        if pending is None:
            self.t.blank()
        else:
            self.chain_foot(pending)

    def chain_foot(self, before_lines: int) -> None:
        added = self.ledger.count - before_lines
        g = self.t.g
        self.t.foot(
            [
                s(g["bl"] + g["h"] + " ", "grey"),
                s("audit chain  ", "grey"),
                s(f"+{added} line{'' if added == 1 else 's'}", "cyan"),
                s(f"  ({self.ledger.count} total)  head ", "grey"),
                s(self.ledger.head[:16], "bcyan"),
                s(self.t.g["dot"] * 3, "grey"),
            ]
        )
        self.t.blank()

    def counter_price(self, sku: Optional[str]) -> Optional[int]:
        """The counter's mirror of the price book. paisa's copy is the one that
        decides; a disagreement is a 409, never a charge."""
        if sku is None:
            return None
        value = PRICES.get(sku)
        return None if value is None else int(paise(value))

    # ================================================================
    # BEAT 0 — the header
    # ================================================================

    def beat_header(self) -> None:
        t = self.t
        t.blank()
        t.banner(
            "GAWAAH  ·  a camera-native kirana counter",
            f"{self.scenario.upper()}  seed {self.seed}",
        )
        t.emit(
            [
                s("  no camera", "grey"),
                s("  ·  ", "grey"),
                s("no credentials", "grey"),
                s("  ·  ", "grey"),
                s("no network", "grey"),
                s("  ·  ", "grey"),
                s("every number below is computed", "grey"),
            ]
        )
        t.blank()
        t.card_open("WHAT THIS RUN IS", "grey")
        t.note(SCENARIO_BLURB[self.scenario], "bold")
        t.card_rule()
        t.note(
            "The camera frame is drawn by tools/demo.py and warped off-nadir. "
            "Everything downstream of the pixels is the shipped module. The "
            "webhooks are signed by gawaah/rzp_sim.py, which stamps every body "
            "it emits with _gawaah_sim: true so a simulated green can never be "
            "mistaken for a real one."
        )
        t.note(
            "kernel.new_nonce is seeded from --seed for this run so the audit "
            "chain is byte-reproducible; in production it is 128 bits of CSPRNG."
        )
        t.card_close("grey")
        t.blank()
        t.pause()

    # ================================================================
    # BEAT 1 — the mat locks
    # ================================================================

    def beat_mat(self) -> None:
        t = self.t
        before = self.ledger.count
        self.card("TAKHTI — the mat locks")

        try:
            from gawaah.takhti import (
                BUF_H,
                BUF_W,
                MARKER_IDS,
                MAX_PERSP_INDEX,
                MAX_SCALE_ERR,
                PX_PER_MM,
                PlaneEngine,
            )
            cv2, np = _import_cv()
        except Exception as exc:                       # pragma: no cover
            self.vision_ok = False
            self.vision_note = f"{type(exc).__name__}: {exc}"
            t.row([s("OpenCV/numpy unavailable — the camera stage is skipped.", "amber")])
            t.note(
                "paisa does not import cv2, so the money path below still runs "
                "in full; the homography is submitted as the identity matrix and "
                "the corner check is skipped exactly as it is on a server with "
                "no camera stack."
            )
            self.geometry_h = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
            self.geometry_corners = []
            self.mat = {"available": False, "reason": self.vision_note}
            self.session.on_mat_lock(True)
            t.card_close()
            self.chain_foot(before)
            self.step("mat_lock", available=False)
            return

        engine = PlaneEngine()
        empty_frame = _camera_frame(_mat_image([]))
        lock = engine.detect(empty_frame)
        if not lock.locked:                            # pragma: no cover
            raise SystemExit(f"demo: the synthetic mat failed to lock: {lock.reason}")

        self.engine = engine
        self.empty_rectified = engine.rectify(empty_frame, lock.H)
        self.geometry_h = [[float(v) for v in row] for row in lock.H.tolist()]
        detector = cv2.aruco.ArucoDetector(
            cv2.aruco.getPredefinedDictionary(
                __import__("gawaah.takhti", fromlist=["ARUCO_DICT"]).ARUCO_DICT
            ),
            _refined_params(cv2),
        )
        corners, ids, _ = detector.detectMarkers(empty_frame)
        by_id = {int(i): c.reshape(4, 2) for i, c in zip(ids.flatten(), corners)}
        self.geometry_corners = [
            [float(v) for v in by_id[i].mean(axis=0).tolist()] for i in MARKER_IDS
        ]

        deg = PlaneEngine.persp_to_deg(lock.persp_index)
        t.kv("mat lock", [s("LOCKED", "rgreen"),
                          s(f"   markers {','.join(str(i) for i in lock.ids_found)}"
                            f"  ({len(lock.ids_found)}/4)", "grey")])
        t.kv(
            "scale error",
            [s(f"{lock.scale_err * 100:7.3f} %", "green")],
            note=f"gate {MAX_SCALE_ERR * 100:.1f} % · 30 mm markers, on the plane",
        )
        t.kv(
            "perspective index",
            [s(f"{lock.persp_index:7.4f}", "green")],
            note=f"gate {MAX_PERSP_INDEX:.4f} · ~{deg:.1f} deg, approximate",
        )
        t.kv("reprojection rmse", [s(f"{lock.reproj_rmse_px:7.4f} px", "green")])
        t.kv(
            "rectified buffer",
            [s(f"{BUF_W} x {BUF_H} px", "cyan")],
            note=f"{PX_PER_MM:.4f} px/mm · A3 {MAT_W_MM:.0f} x {MAT_H_MM:.0f} mm",
        )
        t.card_rule()
        t.note(
            "INVARIANT 4: the rectified crop is the only buffer that survives "
            "this frame grab. At 45 cm nadir a standing person cannot be inside "
            "it, which is the privacy property the mat is shaped to give.",
        )
        t.card_close()

        self.session.on_mat_lock(True)
        self.mat = {
            "available": True,
            "locked": True,
            "markers": list(lock.ids_found),
            "scale_err": round(float(lock.scale_err), 8),
            "persp_index": round(float(lock.persp_index), 8),
            "persp_deg_approx": round(float(deg), 4),
            "reproj_rmse_px": round(float(lock.reproj_rmse_px), 8),
            "buffer_px": [BUF_W, BUF_H],
        }
        self.chain_foot(before)
        self.step("mat_lock", **self.mat)
        t.pause()

    # ================================================================
    # BEAT 2 — goods land, are measured, and are identified (or not)
    # ================================================================

    def beat_placement(self) -> None:
        t = self.t
        before = self.ledger.count
        self.card("PLACEMENT — measured in millimetres, then named")

        if not self.vision_ok:
            for slot, gd in enumerate(GOODS, 1):
                sk = Seen(good=gd, slot=slot, sku=gd.sku, centre_mm=gd.centre_mm)
                sk.long_edge_mm = gd.size_mm[1]
                sk.short_edge_mm = gd.size_mm[0]
                sk.price_paise = self.counter_price(gd.sku)
                sk.ident_reason = "match" if gd.sku else "below_similarity"
                self.seen.append(sk)
            self._placement_table()
            t.card_close()
            self.chain_foot(before)
            self.step("placement", vision=False)
            return

        from gawaah.identity import Gallery, Identifier
        from gawaah.placement import PlacementDetector

        # -- enrolment: one capture per enrolled SKU, through the same pipeline
        gallery = Gallery()
        for gd in GOODS:
            if gd.sku is None:
                continue
            solo = Good(gd.key, gd.label, gd.sku, (150.0, 200.0), gd.size_mm,
                        gd.pattern, gd.crosses)
            frame = _camera_frame(_mat_image([solo]))
            lk = self.engine.detect(frame)
            rect = self.engine.rectify(frame, lk.H)
            crop = _crop_mm(rect, solo.centre_mm, gd.size_mm[1], gd.size_mm[0])
            gallery.enroll(gd.sku, [descriptor(crop)], footprint_mm=gd.size_mm[1])

        identifier = Identifier(gallery, descriptor)

        # -- the live frame
        loaded = _camera_frame(_mat_image(GOODS))
        lock = self.engine.detect(loaded)
        rectified = self.engine.rectify(loaded, lock.H)
        det = PlacementDetector(self.empty_rectified)
        placements: list[Any] = []
        for _ in range(6):                 # STABLE_FRAMES is 5; the mat is still
            placements = det.update(rectified)

        # bind measured blobs back to the goods this file drew
        for slot, gd in enumerate(GOODS, 1):
            best, best_d = None, 1e9
            for p in placements:
                d = abs(p.centre_mm[0] - gd.centre_mm[0]) + abs(
                    p.centre_mm[1] - gd.centre_mm[1]
                )
                if d < best_d:
                    best, best_d = p, d
            sk = Seen(good=gd, slot=slot)
            if best is None or not best.measurable:   # pragma: no cover
                sk.ident_reason = "unmeasurable"
                self.seen.append(sk)
                continue
            sk.centre_mm = (float(best.centre_mm[0]), float(best.centre_mm[1]))
            sk.long_edge_mm = float(best.long_edge_mm)
            sk.short_edge_mm = float(best.short_edge_mm)
            sk.area_mm2 = float(best.area_mm2)
            crop = _crop_mm(rectified, sk.centre_mm, sk.long_edge_mm, sk.short_edge_mm)
            ident = identifier.identify(crop, sk.long_edge_mm)
            sk.sku = ident.sku_id
            sk.top1 = float(ident.top1)
            sk.margin = float(ident.margin)
            sk.ident_reason = ident.reason
            sk.price_paise = self.counter_price(ident.sku_id)
            self.ledger.append(
                ts=self.clock.now_iso(), module="identity",
                item=sk.item_id, **ident.to_audit(),
            )
            self.seen.append(sk)

        self._placement_table()
        t.card_rule()
        amber = [sk for sk in self.seen if sk.amber]
        if amber:
            t.note(
                f"INVARIANT 7: {len(amber)} item abstained. "
                f"{amber[0].good.label!r} scored {amber[0].top1:.3f} against the "
                f"only SKU its {amber[0].long_edge_mm:.1f} mm footprint admits, "
                f"below the 0.550 similarity floor. It is shown, it is logged, "
                f"and it is excluded from the total. It is never guessed at.",
                "amber",
            )
        t.card_close()
        self._mat_map(title="on the mat")
        self.chain_foot(before)
        self.step("placement", items=[sk.item_id for sk in self.seen])
        t.pause()

    def _placement_table(self) -> None:
        t = self.t
        t.row(
            [
                s(f"{'item':<16}", "grey"),
                s(f"{'long mm':>9}", "grey"),
                s(f"{'area mm2':>10}", "grey"),
                s(f"  {'sku':<13}", "grey"),
                s(f"{'cos':>7}", "grey"),
                s("   verdict", "grey"),
            ]
        )
        for sk in self.seen:
            named = sk.sku is not None
            st = "green" if named else "amber"
            t.row(
                [
                    s(f"{sk.good.label:<16}"),
                    s(f"{sk.long_edge_mm:9.2f}" if sk.long_edge_mm else f"{'—':>9}",
                      "cyan"),
                    s(f"{sk.area_mm2:10.1f}" if sk.area_mm2 else f"{'—':>10}", "cyan"),
                    s(f"  {(sk.sku or '—'):<13}", st),
                    s(f"{sk.top1:7.3f}", st),
                    s("   " + ("match" if named else "ABSTAIN"),
                      "green" if named else "bamber"),
                ]
            )

    # ================================================================
    # BEAT 3 — the exit crossings
    # ================================================================

    def beat_exit(self) -> None:
        t = self.t
        before = self.ledger.count
        self.card("SELL EVENT — the exit edge decides")

        occluded_key = "dal" if self.scenario == "amber" else None
        tracks: dict[str, list[Optional[tuple[float, float]]]] = {}
        for sk in self.seen:
            gd = sk.good
            x = sk.centre_mm[0] if sk.centre_mm != (0.0, 0.0) else gd.centre_mm[0]
            y = sk.centre_mm[1] if sk.centre_mm != (0.0, 0.0) else gd.centre_mm[1]
            path = crossing_track(x, y) if gd.crosses else resting_track(x, y)
            if gd.key == occluded_key:
                path = occlude(path, (14, 15))
            tracks[gd.key] = path

        tracker = CentroidTracker()
        zone = LineZone.mat_exit_line(min_crossing_frames=3)
        by_key = {sk.good.key: sk for sk in self.seen}
        strips: dict[str, list[str]] = {k: [] for k in tracks}
        untracked_frames: list[list[tuple[float, float]]] = []
        anon_reasons: list[str] = []

        for i in range(N_FRAMES):
            visible = [(k, tracks[k][i]) for k in tracks if tracks[k][i] is not None]
            upd = tracker.update([p for _, p in visible])
            for k, p in visible:
                sk = by_key[k]
                if sk.track_id is None:
                    for tid, tp in upd.tracks.items():
                        if tuple(tp) == tuple(p):
                            sk.track_id = int(tid)
                            break
                if sk.track_id is not None and sk.track_id in upd.tracks:
                    sk.path_mm.append((float(p[0]), float(p[1])))
            res = zone.update(upd.tracks, untracked=upd.untracked, lost=upd.lost)
            untracked_frames.append([(float(p[0]), float(p[1])) for p in upd.untracked])
            for exc in res.exceptions:
                anon_reasons.append(exc.code)
            id_to_key = {sk.track_id: k for k, sk in by_key.items()}
            for tid in res.crossed_out:
                key = id_to_key.get(int(tid))
                if key is not None:
                    by_key[key].committed = True
                    by_key[key].commit_frame = i
            for k in tracks:
                p = tracks[k][i]
                if p is None:
                    strips[k].append("?")
                    continue
                d = zone.signed_distance_mm(p)
                strips[k].append("." if d < -1.0 else (">" if d > 1.0 else "|"))
        final = zone.flush()
        for exc in final.exceptions:
            anon_reasons.append(exc.code)

        for k, cells in strips.items():
            by_key[k].strip = "".join(cells)
        self.untracked_by_frame = untracked_frames

        # ---- render
        t.row(
            [
                s(f"{'item':<17}", "grey"),
                s("trk", "grey"),
                s(f"  {'frames 0..' + str(N_FRAMES - 1):<{N_FRAMES}}", "grey"),
                s("   result", "grey"),
            ]
        )
        for sk in self.seen:
            cells: list[Seg] = []
            for ch in sk.strip:
                if ch == ".":
                    cells.append(s(self.t.g["dot"], "grey"))
                elif ch == ">":
                    cells.append(s(self.t.g["block"], "green" if not sk.amber else "amber"))
                elif ch == "?":
                    cells.append(s("?", "bred"))
                else:
                    cells.append(s("|", "cyan"))
            if sk.committed:
                verdict = s("  CROSSED  frame " + str(sk.commit_frame),
                            "green" if not sk.amber else "amber")
            elif sk.good.crosses:
                verdict = s("  UNATTRIBUTED", "bred")
            else:
                verdict = s("  stayed on the mat", "grey")
            t.row(
                [s(f"{sk.good.label:<17}"), s(f"{sk.track_id if sk.track_id else '—':>3}", "cyan"),
                 s("  ")] + cells + [verdict]
            )
        t.card_rule()
        t.kv(
            "exit line",
            [s(f"y = {EXIT_Y_MM:.0f} mm", "cyan")],
            note="1 mm dead band · 3-frame debounce",
        )
        t.kv("committed OUT", [s(f"{zone.out_count}", "bold")])
        t.kv(
            "uncounted crossings",
            [s(f"{zone.crossed_without_tracker_id + zone.detected_but_never_counted}",
               "bred" if zone.amber else "green")],
        )
        if zone.exceptions:
            t.card_rule()
            for exc in zone.exceptions[:2]:
                t.note(f"[{exc.code}] {exc.detail}", "bamber")
        t.card_close()
        self._mat_map(title="after the exit crossings", crossed=True)

        # ---- the counter's own state machine
        for sk in self.seen:
            self.session.on_placement(
                LinePlacement(
                    item_id=sk.item_id,
                    name=sk.good.label,
                    price_paise=sk.price_paise,
                )
            )
        for sk in self.seen:
            if sk.committed:
                self.session.on_exit(sk.item_id)
        if zone.crossed_without_tracker_id or zone.detected_but_never_counted:
            # abstention 11: goods left the counter and we cannot say which.
            self.session.on_exit(None)

        self.crossing = {
            "frames": N_FRAMES,
            "out_count": zone.out_count,
            "crossed_without_tracker_id": zone.crossed_without_tracker_id,
            "detected_but_never_counted": zone.detected_but_never_counted,
            "amber": bool(zone.amber),
            "exception_codes": sorted(set(anon_reasons)),
        }
        self.chain_foot(before)
        self.step("exit", **self.crossing)
        t.pause()

    # ================================================================
    # BEAT 4 — the basket
    # ================================================================

    def beat_basket(self) -> None:
        t = self.t
        self.card("BASKET — what the shopkeeper sees")
        t.row(
            [
                s(f"{'line':<26}", "grey"),
                s(f"{'rupees':>10}", "grey"),
                s(f"{'paise':>9}", "grey"),
                s("   state", "grey"),
            ]
        )
        for sk in self.seen:
            if not sk.committed:
                continue
            if sk.amber:
                t.row(
                    [
                        s(f"{sk.good.label:<26}", "amber"),
                        s(f"{'—':>10}", "amber"),
                        s(f"{'—':>9}", "amber"),
                        s("   AMBER  excluded from the total", "bamber"),
                    ]
                )
            else:
                t.row(
                    [
                        s(f"{sk.good.label:<26}"),
                    ]
                    + money_seg(sk.price_paise)
                    + [
                        s(f"{sk.price_paise:>9}", "cyan"),
                        s("   priced from the gallery", "grey"),
                    ]
                )
        t.card_rule()
        billable = int(self.session.total_paise)
        state_style = "bamber" if self.session.state.value == "FROZEN_TOTAL" else "bold"
        t.row(
            [s(f"{'TOTAL':<26}", "bold")]
            + money_seg(billable, "bgreen")
            + [
                s(f"{billable:>9}", "bcyan"),
                s(f"   {self.session.amber_count} amber excluded", "grey"),
            ]
        )
        t.row(
            [
                s(f"{'counter state':<26}", "grey"),
                s(f"{self.session.state.value:>10}", state_style),
            ]
        )
        t.card_close()
        self.step(
            "basket",
            total_paise=billable,
            amber=self.session.amber_count,
            state=self.session.state.value,
        )
        self.t.blank()
        t.pause()

    # ================================================================
    # BEAT 5 — the mint (or the refusal)
    # ================================================================

    def intent_body(
        self,
        *,
        amount_paise: Optional[int] = None,
        lie: Optional[str] = None,
    ) -> dict[str, Any]:
        """The geometry the phone submits. `lie` bends exactly one thing."""
        crossings: list[dict[str, Any]] = []
        for sk in self.seen:
            if sk.track_id is None or not sk.path_mm:
                continue
            entry: dict[str, Any] = {
                "item_id": sk.item_id,
                "track_id": int(sk.track_id),
                "path_mm": [[float(x), float(y)] for x, y in sk.path_mm],
                "committed": bool(sk.committed),
                "name": sk.good.label,
            }
            if lie == "crossing" and sk.good.key == "soap":
                entry["committed"] = True
            if lie == "price" and sk.good.key == "rice":
                entry["price_paise"] = 100
            crossings.append(entry)
        total = amount_paise
        if total is None:
            total = int(self.session.total_paise)
        if lie == "crossing":
            total = int(total) + int(PRICES["sku-soap"])
        return {
            "session_id": self.session_id,
            "amount_paise": int(total),
            "geometry": {
                "H": self.geometry_h,
                "corners": self.geometry_corners,
                "crossings": crossings,
                "untracked": [
                    [[float(x), float(y)] for x, y in frame]
                    for frame in self.untracked_by_frame
                ],
                "min_crossing_frames": 3,
            },
        }

    def post_intent(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return self.http.post_intent(body)

    def beat_mint(self, *, expect_ok: bool = True, step: bool = True,
                  suffix: str = "") -> bool:
        t = self.t
        before = self.ledger.count
        self.card("MINT — paisa re-runs the crossing predicate",
                  step=step, suffix=suffix)
        t.note(
            "INVARIANT 5. The phone submits a homography, the four marker "
            "centres it saw, and its centroid tracks. paisa believes none of "
            "it: it replays those tracks through the same sellevent.LineZone on "
            "a machine that has never seen a camera, reprices from its own book, "
            "and only then mints.",
        )
        t.card_rule()

        status, body = self.post_intent(self.intent_body())
        ok = status == 200
        t.kv("POST /intent", [s(str(status), "green" if ok else "bred"),
                              s("   " + self.transport, "grey")])
        if not ok:
            t.kv("refusal", [s(str(body.get("error")), "bred")])
            for chunk in _wrap(str(body.get("detail", "")), WIDTH - 6):
                t.row([s(chunk, "amber")])
            geo = body.get("geometry") or {}
            if geo:
                t.card_rule()
                t.kv("server re-ran", [s(f"{geo.get('frames')} frames", "cyan")])
                t.kv("server says crossed", [s(str(geo.get("server_committed")), "cyan")])
                t.kv("phone claimed", [s(str(geo.get("client_committed")), "cyan")])
                if geo.get("server_total_paise"):
                    t.kv("server repriced at",
                         [s(f"{geo.get('server_total_paise')} paise", "cyan")])
            t.card_close()
            self.refusals.append(
                {"where": "POST /intent", "status": status,
                 "error": body.get("error"), "detail": body.get("detail")}
            )
            self.chain_foot(before)
            self.step("mint_refused", status=status, error=body.get("error"))
            t.pause()
            return False

        geo = body.get("geometry") or {}
        self.mint = {
            "nonce": body["nonce"],
            "payment_link_id": body["payment_link_id"],
            "short_url": body["short_url"],
            "amount_paise": int(body["amount_paise"]),
            "amount_rupees": body["amount_rupees"],
            "priced_items": list(body.get("priced_items") or []),
            "amber_items": list(body.get("amber_items") or []),
            "server_total_paise": geo.get("server_total_paise"),
            "frames": geo.get("frames"),
        }
        t.kv("server re-ran", [s(f"{geo.get('frames')} frames of the predicate", "cyan")])
        t.kv(
            "server agrees",
            [s("YES", "rgreen"),
             s(f"   {len(self.mint['priced_items'])} priced, "
               f"{len(self.mint['amber_items'])} amber", "grey")],
        )
        t.kv(
            "homography",
            [s("accepted", "green")],
            note=_slack_note(geo.get("homography_slack_pxw")),
        )
        t.card_rule()
        t.kv("payment link", [s(body["payment_link_id"], "bcyan")])
        t.kv("short_url", [s(body["short_url"], "bcyan")])
        t.kv("amount", money_seg(int(body["amount_paise"]), "bgreen")
             + [s(f"   {body['amount_paise']} paise", "grey")])
        t.kv("notes.session_id", [s(self.session_id, "cyan")])
        t.kv("kernel intent", [s(body["state"], "cyan"), s("  nonce " + body["nonce"][:20], "grey")])
        t.card_close()
        self._qr_panel(body["short_url"])
        self.chain_foot(before)
        self.step("mint", **self.mint)
        t.pause()
        return True

    def _qr_panel(self, short_url: str) -> None:
        """A visual fingerprint of the minted string, labelled as exactly that.

        It is NOT a scannable QR and is not presented as one. The real code is
        rendered on the counter plane from `short_url`; nothing is fetched, and
        GAWAAH never constructs a upi:// payload (INVARIANT 6).
        """
        t = self.t
        g = t.g
        digest = hashlib.sha256(short_url.encode("utf-8")).digest()
        bits = "".join(f"{byte:08b}" for byte in digest)
        size = 15
        rows: list[str] = []
        for r in range(size):
            row = ""
            for c in range(size):
                edge = r in (0, size - 1) or c in (0, size - 1)
                idx = (r * size + c) % len(bits)
                row += "1" if (edge or bits[idx] == "1") else "0"
            rows.append(row)
        t.emit([s("   " + g["tl"] + g["h"] * (size * 2 + 2) + g["tr"], "grey")])
        for r in range(0, size, 2):
            top = rows[r]
            bot = rows[r + 1] if r + 1 < size else "0" * size
            cells = ""
            for c in range(size):
                a, b = top[c] == "1", bot[c] == "1"
                cells += (g["block"] if a and b else
                          g["up"] if a else
                          g["down"] if b else " ") * 2
            t.emit([s("   " + g["v"], "grey"), s(" " + cells + " ", "bcyan"),
                    s(g["v"], "grey")])
        t.emit([s("   " + g["bl"] + g["h"] * (size * 2 + 2) + g["br"], "grey")])
        t.emit([s("   sha256 fingerprint of short_url — NOT a scannable code. The "
                  "real QR is", "grey")])
        t.emit([s("   rendered on the counter plane from the string above; nothing "
                  "is fetched.", "grey")])
        t.blank()

    # ================================================================
    # BEAT 6 — the customer pays, the webhook arrives
    # ================================================================

    def beat_pay(self, *, mode: Optional[str] = None) -> Any:
        t = self.t
        self.card("THE CUSTOMER PAYS")
        if mode:
            self.sim.set_mode(mode)
        result = self.sim.pay_link(self.mint["payment_link_id"])
        t.kv("payment method", [s("upi", "cyan")])
        t.kv("link status", [s(result.payment_link["status"], "green")])
        t.kv(
            "amount_paid",
            money_seg(int(result.payment_link["amount_paid"]), "bold")
            + [s(f"   {result.payment_link['amount_paid']} paise", "grey")],
        )
        t.kv("simulator mode", [s(self.sim.mode, "amber" if mode else "grey")])
        t.kv("webhooks pushed", [s(str(len(result.deliveries)), "cyan")])
        t.card_rule()
        t.note(
            "rzp_sim serialises the body in insertion order, not sorted-key "
            "canonical JSON. A receiver that parses and re-serialises before "
            "checking the HMAC therefore FAILS, which is how INVARIANT 2 is "
            "enforced by the fixture rather than merely requested."
        )
        t.card_close()
        self.t.blank()
        t.pause()
        return result

    def deliver(
        self,
        delivery: Any,
        *,
        raw: Optional[bytes] = None,
        signature: Optional[str] = None,
        header_event_id: Optional[str] = None,
        label: str = "genuine delivery",
        expect_green: bool = True,
    ) -> tuple[int, dict[str, Any]]:
        """POST one webhook at paisa, exactly as Razorpay would."""
        t = self.t
        before = self.ledger.count
        headers = dict(delivery.headers)
        if signature is not None:
            headers["X-Razorpay-Signature"] = signature
        if header_event_id is not None:
            headers["X-Razorpay-Event-Id"] = header_event_id
        payload = raw if raw is not None else delivery.body

        status, body = self.http.post_webhook(payload, headers)
        green = bool(body.get("green"))
        sev = body.get("severity")
        sev_style = {"GREEN": "rgreen", "RED": "rred"}.get(sev, "ramber")

        t.kv("POST /webhook", [s(str(status), "green" if status == 200 else "bred"),
                               s(f"   {label}", "grey")])
        t.kv("raw bytes", [s(f"{len(payload)}", "cyan"),
                           s(f"   sha256 {body.get('body_sha256', '')[:16]}"
                             + self.t.g["dot"] * 3, "grey")])
        t.kv("signature", [s("VALID" if body.get("signature_valid") else "INVALID",
                             "green" if body.get("signature_valid") else "bred")])
        t.kv("verdict", [s(f" {sev} ", sev_style),
                         s("   " + str(body.get("reason")), "bold")])
        if body.get("amount_paise") is not None:
            match = body.get("amount_paise") == body.get("expected_paise")
            t.kv(
                "amount",
                [s(f"{body.get('amount_paise')}", "green" if match else "bred"),
                 s(f" paise  vs intent {body.get('expected_paise')}", "grey")],
            )
        if body.get("detail"):
            for chunk in _wrap(str(body["detail"]), WIDTH - 6):
                t.row([s(chunk, "grey" if green else "amber")])
        if body.get("session_state"):
            t.kv("paisa session", [s(str(body["session_state"]),
                                     "green" if green else "amber")])

        # the counter's own state machine adjudicates the verdict it was handed.
        # It holds no secret; it trusts paisa for the HMAC and re-checks the
        # three legs it can check on its own (event class, session, amount).
        if body.get("signature_valid") and body.get("event_id"):
            tr = self.session.on_webhook(
                Verdict(
                    event_id=str(body["event_id"]),
                    event=str(body.get("event") or ""),
                    session_id=str(body.get("session_id") or ""),
                    amount_paise=body.get("amount_paise"),
                    green=green,
                    signature_valid=True,
                    reason=str(body.get("reason")),
                )
            )
            t.kv("counter session", [s(self.session.state.value,
                                       "rgreen" if self.session.state is State.PAID
                                       else "ramber"),
                                     s("   " + tr.reason
                                       + ("" if tr.applied
                                          else "  (duplicate: no new transition, "
                                               "no new ledger line)"), "grey")])
        if not green:
            self.refusals.append(
                {"where": "POST /webhook", "label": label, "status": status,
                 "reason": body.get("reason"), "severity": sev}
            )
        if expect_green or green:
            self.green = {
                "green": green,
                "reason": body.get("reason"),
                "severity": sev,
                "event": body.get("event"),
                "amount_paise": body.get("amount_paise"),
                "expected_paise": body.get("expected_paise"),
                "settled_nonce": body.get("settled_nonce"),
            }
        self._pending_foot = before
        self.step("webhook", label=label, green=green, reason=body.get("reason"))
        return status, body

    # ================================================================
    # the mat map
    # ================================================================

    def _mat_map(self, *, title: str, crossed: bool = False) -> None:
        """A plan view of the mat. Columns are millimetres, not pixels."""
        t = self.t
        g = t.g
        cols, rows = 46, 26
        span_mm = 442.0            # the mat plus a strip of the customer's side
        grid = [[" "] * cols for _ in range(rows)]
        style = [[None] * cols for _ in range(rows)]

        def to_col(x_mm: float) -> int:
            return max(0, min(cols - 1, int(x_mm * cols / MAT_W_MM)))

        def to_row(y_mm: float) -> int:
            return max(0, min(rows - 1, int(y_mm * rows / span_mm)))

        exit_row = to_row(EXIT_Y_MM)
        for c in range(cols):
            grid[exit_row][c] = g["dh"]
            style[exit_row][c] = "grey"
        tag = " EXIT "
        start = (cols - len(tag)) // 2
        for i, ch in enumerate(tag):
            grid[exit_row][start + i] = ch
            style[exit_row][start + i] = "bold"

        for sk in self.seen:
            cx, cy = sk.centre_mm if sk.centre_mm != (0.0, 0.0) else sk.good.centre_mm
            w = sk.short_edge_mm or sk.good.size_mm[0]
            h = sk.long_edge_mm or sk.good.size_mm[1]
            st = "bamber" if sk.amber else "green"
            if crossed and sk.good.crosses:
                cy, w, h = 415.0, min(w, 62.0), 10.0
                if not sk.committed:
                    st = "bred"
            c0, c1 = to_col(cx - w / 2), to_col(cx + w / 2)
            r0, r1 = to_row(cy - h / 2), to_row(cy + h / 2)
            for r in range(r0, r1 + 1):
                for c in range(c0, c1 + 1):
                    grid[r][c] = g["half"]
                    style[r][c] = st
            name = sk.good.key[: max(1, c1 - c0 - 1)]
            r = (r0 + r1) // 2
            c = max(0, (c0 + c1 - len(name)) // 2)
            for i, ch in enumerate(name):
                if c + i < cols:
                    grid[r][c + i] = ch
                    style[r][c + i] = st

        t.emit([s("   " + title, "grey")])
        t.emit([s("   " + g["mark"] + "0" + g["tl"] + g["h"] * cols + g["tr"] + "1"
                  + g["mark"], "grey")])
        for r in range(rows):
            segs: list[Seg] = [s("     " + g["v"], "grey")]
            run, run_style = "", style[r][0]
            for c in range(cols):
                if style[r][c] != run_style:
                    segs.append(s(run, run_style))
                    run, run_style = "", style[r][c]
                run += grid[r][c]
            segs.append(s(run, run_style))
            segs.append(s(g["v"], "grey"))
            t.emit(segs)
        t.emit([s("   " + g["mark"] + "3" + g["bl"] + g["h"] * cols + g["br"] + "2"
                  + g["mark"], "grey")])
        t.emit([s(f"   {MAT_W_MM:.0f} x {MAT_H_MM:.0f} mm  ·  exit edge at "
                  f"y = {EXIT_Y_MM:.0f} mm", "grey")])
        t.blank()

    # ================================================================
    # FINAL
    # ================================================================

    def finish(self) -> int:
        t = self.t
        ok_chain, n_lines, head, err = verify_ledger(self.ledger_path)
        state = self.session.state.value
        expected = EXPECTED_STATE[self.scenario]
        state_ok = state == expected

        authorised = self.session.authorised_paise
        billable = int(self.session.total_paise)

        code = EXIT_OK
        if not ok_chain:
            code = EXIT_LEDGER_BROKEN
        elif not state_ok:
            code = EXIT_WRONG_STATE

        t.card_open("FINAL", "bgreen" if code == EXIT_OK else "bred")
        t.kv("scenario", [s(self.scenario, "bold"),
                          s(f"   seed {self.seed}", "grey")])
        t.kv("counter state", [s(state, "rgreen" if state == "PAID" else "ramber"),
                               s(f"   expected {expected}", "grey")])
        t.kv(
            "basket total",
            [s(f"{billable:>10}", "bcyan"), s(" paise", "grey"),
             s(f"   {t.g['rupee']} {to_rupees_str(billable)}", "bgreen")],
        )
        t.kv(
            "money authorised",
            [s(f"{authorised if authorised is not None else 0:>10}", "bcyan"),
             s(" paise", "grey"),
             s("   " + ("YES" if self.session.money_authorised else "NO"),
               "bgreen" if self.session.money_authorised else "bamber")],
        )
        t.kv("amber excluded", [s(f"{self.session.amber_count:>10}", "bamber"),
                                s(" line(s)", "grey")])
        t.kv("refusals logged", [s(f"{len(self.refusals):>10}", "cyan")])
        t.card_rule()
        t.kv(
            "ledger verify",
            [s(" VERIFIED " if ok_chain else " BROKEN ", "rgreen" if ok_chain else "rred"),
             s(f"   {n_lines} lines from genesis", "grey")],
        )
        t.row([s("chain head", "grey")])
        t.row([s(head, "bcyan")])
        if err:
            t.note(str(err), "bred")
        t.kv("ledger file", [s(_short_path(self.ledger_path), "grey")])
        t.card_close("bgreen" if code == EXIT_OK else "bred")
        t.blank()

        summary = self.summary_line(state, billable, authorised, ok_chain, n_lines, head)
        t.emit([s("  " + summary, "bgreen" if code == EXIT_OK else "bred")])
        t.blank()
        t.emit([s("  re-verify this chain yourself, with code that did not write it:",
                  "grey")])
        t.emit([s("    python -c \"from gawaah.ledger import verify; "
                  f"print(verify('{_short_path(self.ledger_path)}'))\"", "cyan")])
        t.blank()

        self.result = {
            "demo": "gawaah",
            "scenario": self.scenario,
            "seed": self.seed,
            "deterministic_nonce": True,
            "ok": code == EXIT_OK,
            "exit_code": code,
            "final_state": state,
            "expected_final_state": expected,
            "total_paise": billable,
            "total_rupees": to_rupees_str(billable),
            "authorised_paise": authorised,
            "money_authorised": self.session.money_authorised,
            "amber_count": self.session.amber_count,
            "amber_items": [sk.item_id for sk in self.seen if sk.amber and sk.committed],
            "line_items": [
                {
                    "item_id": sk.item_id,
                    "label": sk.good.label,
                    "sku": sk.sku,
                    "long_edge_mm": sk.long_edge_mm,
                    "identity_reason": sk.ident_reason,
                    "price_paise": sk.price_paise,
                    "committed": sk.committed,
                    "amber": sk.amber,
                }
                for sk in self.seen
            ],
            "mat_lock": self.mat,
            "crossing": self.crossing,
            "mint": self.mint,
            "green": self.green,
            "episode": self.episode,
            "refusals": self.refusals,
            "steps": [
                {"n": st.n, "name": st.name, "ledger_lines": st.lines,
                 "ledger_head": st.head, **st.detail}
                for st in self.steps
            ],
            "ledger": {
                "path": self.ledger_path,
                "lines": n_lines,
                "head": head,
                "verified": bool(ok_chain),
                "error": err,
            },
            "summary": summary,
        }
        return code

    def summary_line(
        self,
        state: str,
        billable: int,
        authorised: Optional[int],
        ok_chain: bool,
        n_lines: int,
        head: str,
    ) -> str:
        return (
            f"GAWAAH {self.scenario}/seed {self.seed} {self.t.g['arrow']} {state} · "
            f"total {billable} paise (Rs {to_rupees_str(billable)}) · "
            f"authorised {authorised if authorised is not None else 0} paise · "
            f"{self.session.amber_count} amber excluded · "
            f"{len(self.refusals)} refusal(s) · "
            f"ledger {'VERIFIED' if ok_chain else 'BROKEN'} "
            f"{n_lines} lines head {head[:12]}"
        )

    # ================================================================
    # scenarios
    # ================================================================

    def run(self) -> int:
        self.beat_header()
        self.beat_mat()
        self.beat_placement()
        self.beat_exit()
        self.beat_basket()
        getattr(self, f"scenario_{self.scenario}")()
        return self.finish()

    # -- happy ------------------------------------------------------

    def scenario_happy(self) -> None:
        done = self.session.on_done()
        self.card("DONE tap")
        self.t.kv("basket", [s("locked", "cyan"), s("   " + done.reason, "grey")])
        self.t.kv("intent amount", money_seg(int(self.session.intent_amount_paise or 0),
                                             "bgreen"))
        self.t.card_close()
        self.t.blank()
        if not self.beat_mint():
            return
        result = self.beat_pay()
        self.card("WEBHOOK — the four-part green predicate")
        self.t.note(
            "GREEN requires ALL FOUR: a valid HMAC-SHA256 over the raw bytes "
            "BEFORE any JSON parse, AND an event in the green set, AND "
            "notes.session_id naming an OPEN intent, AND the SETTLED amount "
            "equal to the intent to the paisa."
        )
        self.t.card_rule()
        self.deliver(result.deliveries[0])
        self.end_card()

    # -- amber ------------------------------------------------------

    def scenario_amber(self) -> None:
        t = self.t
        self.card("DONE tap — and the refusal it earns", "bamber")
        done = self.session.on_done()
        t.kv("counter state", [s(self.session.state.value, "ramber")])
        t.kv("reason", [s(done.reason, "bamber")])
        t.note(
            "A hand covered the dal for two frames and it reappeared past the "
            "exit line. The tracker refused to re-identify it: an occlusion "
            "that long can hide a swap, a removal or an addition, so naming it "
            "would be a coin flip dressed as a measurement. Goods left the "
            "counter and this counter cannot say which. The total is frozen.",
            "amber",
        )
        t.card_close("bamber")
        t.blank()
        # the phone asks anyway; the server refuses on its own re-run
        self.beat_mint(expect_ok=False)
        self.card("WHAT DID NOT HAPPEN", "bamber")
        t.kv("payment link minted", [s("NO", "bred")])
        t.kv("rupees moved", [s("0", "bred")])
        t.kv("silent under-count", [s("NO", "rgreen")])
        t.note(
            "Both sides abstained independently and for the same reason. The "
            "counter froze its own total; paisa, replaying the submitted tracks "
            "with no camera and no trust, refused to mint against a basket it "
            "knows to be incomplete. The shopkeeper resolves this with a person, "
            "not with a guess.",
        )
        t.card_close("bamber")
        t.blank()

    # -- offline ----------------------------------------------------

    def scenario_offline(self) -> None:
        t = self.t
        self.card("THE LINK GOES DOWN", "bamber")
        self.session.on_network(False)
        done = self.session.on_done()
        t.kv("network", [s("DOWN", "rred")])
        t.kv("DONE tap", [s(self.session.state.value, "ramber"),
                          s("   " + done.reason, "grey")])
        self.episode["offline_state"] = self.session.state.value
        self.episode["offline_reason"] = done.reason
        self.episode["offline_authorised"] = self.session.money_authorised
        self.episode["offline_billable_paise"] = int(self.session.total_paise)
        t.kv("basket total", money_seg(int(self.session.total_paise), "bcyan"))
        t.kv("money authorised", [s("NO", "bamber")])
        t.note(
            "R6: billing continues locally and nothing is authorised. The "
            "counter does not even ask paisa to mint — a payment target it "
            "cannot show is worse than no payment target, and a retry queue "
            "that mints twice is worse than both.",
            "amber",
        )
        t.card_close("bamber")
        t.blank()
        t.pause()

        self.card("THE LINK COMES BACK", "bcyan")
        restored = self.session.on_network(True)
        t.kv("network", [s("UP", "rgreen")])
        t.kv("counter state", [s(self.session.state.value, "cyan"),
                               s("   " + restored.reason, "grey")])
        t.kv("queue depth", [s("1", "cyan"), s("   exactly one intent to drain", "grey")])
        self.episode["restored_state"] = self.session.state.value
        self.episode["restored_reason"] = restored.reason
        self.episode["queue_depth"] = 1
        t.card_close("bcyan")
        t.blank()
        if not self.beat_mint():
            return
        result = self.beat_pay()
        self.card("WEBHOOK", "bcyan")
        self.deliver(result.deliveries[0])
        self.end_card("bcyan")

    # -- mismatch ---------------------------------------------------

    def scenario_mismatch(self) -> None:
        t = self.t
        self.session.on_done()
        self.card("DONE tap")
        t.kv("intent amount", money_seg(int(self.session.intent_amount_paise or 0),
                                        "bgreen"))
        t.card_close()
        t.blank()
        if not self.beat_mint():
            return
        result = self.beat_pay(mode="wrong_amount")
        self.card("THE AMOUNT GATE", "bred")
        t.note(
            "The signature is valid. The event is in the green set. "
            "notes.session_id names an open intent. Only the fourth leg fails: "
            "the amount that settled is one paisa off. Three of four is not "
            "green, and never rounds to it.",
            "amber",
        )
        t.card_rule()
        self.deliver(result.deliveries[0], label="wrong-amount delivery",
                     expect_green=False)
        t.card_rule()
        t.kv("outcome", [s(" RED HOLD ", "rred")])
        t.note(
            "AMOUNT_MISMATCH is one of the three verdicts allowed to be RED "
            "without a human first: money moved and it is the wrong money, "
            "which is a positive contradiction rather than an absence of "
            "evidence. The counter holds and a person resolves it. Nothing is "
            "authorised and nothing is refunded by a machine.",
        )
        self.end_card("bred")

    # -- attack -----------------------------------------------------

    def scenario_attack(self) -> None:
        t = self.t
        self.card("THE PHONE LIES ABOUT A PRICE", "bred", suffix="a")
        status, body = self.post_intent(self.intent_body(lie="price"))
        t.kv("POST /intent", [s(str(status), "bred")])
        t.kv("refusal", [s(str(body.get("error")), "bred")])
        for chunk in _wrap(str(body.get("detail", "")), WIDTH - 6):
            t.row([s(chunk, "amber")])
        self.refusals.append({"where": "POST /intent", "label": "price forgery",
                              "status": status, "error": body.get("error")})
        t.card_close("bred")
        t.blank()
        t.pause()

        self.card("THE PHONE LIES ABOUT A CROSSING", "bred", step=False, suffix="b")
        status, body = self.post_intent(self.intent_body(lie="crossing"))
        t.kv("POST /intent", [s(str(status), "bred")])
        t.kv("refusal", [s(str(body.get("error")), "bred")])
        for chunk in _wrap(str(body.get("detail", "")), WIDTH - 6):
            t.row([s(chunk, "amber")])
        geo = body.get("geometry") or {}
        t.kv("server re-ran", [s(f"{geo.get('frames')} frames", "cyan")])
        t.kv("server says", [s(str(geo.get("server_committed")), "cyan")])
        t.kv("phone claimed", [s(str(geo.get("client_committed")), "cyan")])
        self.refusals.append({"where": "POST /intent", "label": "crossing forgery",
                              "status": status, "error": body.get("error")})
        t.card_close("bred")
        t.blank()
        t.pause()

        self.session.on_done()
        if not self.beat_mint(step=False, suffix="c"):
            return
        result = self.beat_pay()
        genuine = result.deliveries[0]

        self.card("A TAMPERED BODY, THE GENUINE SIGNATURE", "bred", suffix="a")
        tampered = genuine.body.replace(
            f'"amount":{self.mint["amount_paise"]}'.encode("utf-8"),
            f'"amount":{self.mint["amount_paise"] * 10}'.encode("utf-8"),
        )
        t.kv("edit", [s(f"amount x10  ({len(genuine.body)} "
                        f"{self.t.g['arrow']} {len(tampered)} bytes)", "grey")])
        t.note(
            "The verdict below is AMBER, not RED. A body whose HMAC does not "
            "verify is an absence of evidence, and INVARIANT 7 says absence "
            "abstains; the refusal is the 400. RED is reserved for a positive "
            "contradiction — money that moved, for the wrong number.",
        )
        self.deliver(genuine, raw=tampered, label="tampered body",
                     expect_green=False)
        self.end_card("bred")
        t.pause()

        self.card("THE SAME JSON, RE-SERIALISED", "bred", step=False, suffix="b")
        reserialised = json.dumps(
            json.loads(genuine.body.decode("utf-8")),
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        t.note(
            "Semantically identical. Cryptographically a different object. A "
            "receiver that parsed first and hashed the re-serialised bytes "
            "would green this; GAWAAH hashes what arrived, before any parse.",
        )
        self.deliver(genuine, raw=reserialised, label="re-serialised body",
                     expect_green=False)
        self.end_card("bred")
        t.pause()

        self.card("A REWRITTEN X-Razorpay-Event-Id HEADER", "bamber", step=False, suffix="c")
        t.note(
            "The header is outside the HMAC, so anything on the request path "
            "can choose it. If it were a replay key, seeding it with the id of "
            "a webhook that has not arrived yet would refuse the genuine "
            "delivery as a duplicate: money in, counter never green. It is "
            "recorded and it decides nothing.",
        )
        t.card_rule()
        self.deliver(genuine, header_event_id="evt_ATTACKER_CHOSEN_VALUE",
                     label="genuine body, spoofed header")
        self.end_card("bamber")
        t.pause()

        self.card("THE SAME GENUINE DELIVERY, REPLAYED", "bamber", step=False, suffix="d")
        self.deliver(genuine, label="byte-identical replay", expect_green=False)
        t.kv("authorised", money_seg(int(self.session.authorised_paise or 0), "bgreen")
             + [s("   once, and only once", "grey")])
        self.end_card("bamber")


SCENARIO_BLURB = {
    "happy": "A full sale: the mat locks, four goods land, one cannot be named "
             "and is excluded, two cross the exit edge, paisa re-runs the "
             "crossing predicate and mints, the customer pays, and the counter "
             "turns GREEN on a signed webhook.",
    "amber": "The counter abstains. A hand occludes an item mid-crossing and it "
             "reappears past the exit line. Nobody guesses: the total freezes, "
             "paisa refuses to mint, and no rupee moves.",
    "offline": "The network dies before settlement. Billing continues locally "
               "and nothing is authorised; when the link returns, exactly one "
               "intent drains and the counter settles.",
    "mismatch": "Money lands, for the wrong number. Signature valid, event "
                "green, session known — and one paisa off. The fourth leg of "
                "the predicate fails and the counter goes to a RED hold.",
    "attack": "Five forgeries in a row: a lying price, a lying crossing, a "
              "tampered body, a re-serialised body, a rewritten header, and a "
              "replay. Every one is refused, and the honest sale still lands.",
}


# =============================================================================
# 6.  HTTP transport
# =============================================================================


class _DirectTransport:
    """Calls PaisaService in-process. Used when httpx/TestClient is unavailable."""

    name = "in-process (PaisaService)"

    def __init__(self, svc: PaisaService) -> None:
        self.svc = svc

    def post_intent(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        from gawaah.paisa import IntentRequest

        try:
            return 200, self.svc.create_intent(IntentRequest(**body))
        except PaisaRefusal as exc:
            return exc.status, exc.body()

    def post_webhook(
        self, raw: bytes, headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        return self.svc.handle_webhook(
            raw,
            headers.get("X-Razorpay-Signature", ""),
            header_event_id=headers.get("X-Razorpay-Event-Id"),
        )


class _HttpTransport:
    """Real ASGI round trips through the real FastAPI routes."""

    name = "HTTP via fastapi.testclient"

    def __init__(self, svc: PaisaService) -> None:
        import warnings

        with warnings.catch_warnings():
            # starlette nags about httpx versions. Not this demo's business, and
            # not something a judge should have to read past.
            warnings.simplefilter("ignore")
            from fastapi.testclient import TestClient

            self.client = TestClient(create_app(svc))

    def post_intent(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        r = self.client.post("/intent", json=body)
        return r.status_code, r.json()

    def post_webhook(
        self, raw: bytes, headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        r = self.client.post("/webhook", content=raw, headers=dict(headers))
        return r.status_code, r.json()


def _make_client(svc: PaisaService) -> tuple[Any, str]:
    try:
        tr = _HttpTransport(svc)
    except Exception:                                  # pragma: no cover
        tr = _DirectTransport(svc)
    return tr, tr.name


def _refined_params(cv2: Any) -> Any:
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    return params


def _slack_note(slack: Any) -> str:
    """paisa may not divide, so its homography residual is a slack in px*|w|,
    not a pixel distance. Reported as what it is."""
    if slack is None:
        return "corner check skipped (no takhti on this box)"
    return f"tolerance slack {float(slack):.3f} px*|w|"


def _short_path(path: str) -> str:
    try:
        rel = os.path.relpath(path, REPO)
    except ValueError:                                 # pragma: no cover
        return path
    return rel if not rel.startswith("..") else path


# =============================================================================
# 7.  Self-test
# =============================================================================
#
# `--selftest` is the demo's own test. It shells out to THIS file the way a
# judge would — a real subprocess, a real argv, a real exit code, no in-process
# shortcuts — three times per scenario:
#
#   1. `--json`, whose document every assertion below is made against;
#   2. plain, captured, to prove nothing ANSI leaks when stdout is a pipe;
#   3. plain again, byte-compared against (2) to prove `--seed` means what the
#      flag says it means.
#
# The assertions are not decoration. Each one is a property the counter would
# be unsafe without, and each is checked against numbers the run computed
# rather than numbers this section supplies.


def _cv2_available() -> bool:
    """Whether THIS machine can run the camera stage at all. The demo degrades
    gracefully without OpenCV — paisa never imports it — but a green self-test
    on a machine with no OpenCV proves only the money path, and must say so."""
    try:
        import importlib.util
        return importlib.util.find_spec("cv2") is not None
    except (ImportError, ValueError):
        return False


_HAVE_CV2 = _cv2_available()


def _is_sha256(x: Any) -> bool:
    return (isinstance(x, str) and len(x) == 64
            and all(c in "0123456789abcdef" for c in x))


def _priced_total(r: dict[str, Any]) -> int:
    """What the basket SHOULD total: every line that was committed across the
    exit edge and carries a confident SKU. Amber lines contribute nothing."""
    return sum(li["price_paise"] for li in r["line_items"]
               if li["committed"] and not li["amber"])


def _authorised(r: dict[str, Any]) -> int:
    return int(r["authorised_paise"] or 0)


#: Properties that must hold for EVERY scenario, however it ends.
UNIVERSAL_CHECKS: tuple[tuple[str, Callable[..., bool]], ...] = (
    ("exit code is 0",
     lambda r, code, txt, txt2: code == EXIT_OK),
    ("counter lands in the expected state",
     lambda r, code, txt, txt2: r["final_state"] == EXPECTED_STATE[r["scenario"]]),
    ("the run reports itself ok",
     lambda r, code, txt, txt2: r["ok"] is True),
    ("ledger verifies from genesis, with no error",
     lambda r, code, txt, txt2: r["ledger"]["verified"] is True
     and r["ledger"]["error"] is None),
    ("ledger is non-empty and its head is a sha256",
     lambda r, code, txt, txt2: r["ledger"]["lines"] > 0
     and _is_sha256(r["ledger"]["head"])),
    ("every step recorded a chain head",
     lambda r, code, txt, txt2: len(r["steps"]) > 0
     and all(_is_sha256(st["ledger_head"]) for st in r["steps"])),
    ("the chain only ever grows",
     lambda r, code, txt, txt2: all(
         b["ledger_lines"] >= a["ledger_lines"]
         for a, b in zip(r["steps"], r["steps"][1:]))),
    ("INVARIANT 1: the total is whole paise, never a float",
     lambda r, code, txt, txt2: isinstance(r["total_paise"], int)
     and not isinstance(r["total_paise"], bool)),
    ("INVARIANT 1: every price is whole paise",
     lambda r, code, txt, txt2: all(
         isinstance(li["price_paise"], int)
         for li in r["line_items"] if li["price_paise"] is not None)),
    ("INVARIANT 7: at least one line abstained",
     lambda r, code, txt, txt2: r["amber_count"] >= 1),
    ("INVARIANT 7: amber lines carry no price at all",
     lambda r, code, txt, txt2: all(
         li["price_paise"] is None and li["sku"] is None
         for li in r["line_items"] if li["amber"])),
    ("INVARIANT 7: amber lines are excluded from the total",
     lambda r, code, txt, txt2: _priced_total(r) == r["total_paise"]),
    ("money authorised never exceeds the basket",
     lambda r, code, txt, txt2: _authorised(r) <= r["total_paise"]),
    ("the summary line names the state and the paise total",
     lambda r, code, txt, txt2: r["final_state"] in r["summary"]
     and str(r["total_paise"]) in r["summary"]),
    ("no ANSI escapes when stdout is a pipe",
     lambda r, code, txt, txt2: "\x1b" not in txt),
    # Conditional on purpose. A server with no camera stack is a legitimate
    # deployment — paisa never imports cv2 — so a missing OpenCV must not fail
    # the run. But on a machine that HAS OpenCV, a mat that quietly stops
    # locking would otherwise sail through every other assertion here.
    ("the mat locked (when OpenCV is installed)",
     lambda r, code, txt, txt2: (r["mat_lock"].get("available") is True
                                 and r["mat_lock"].get("locked") is True)
     if _HAVE_CV2 else True),
    ("the terminal render is byte-identical for a fixed seed",
     lambda r, code, txt, txt2: txt == txt2),
    ("the terminal render prints the same head the JSON reports",
     lambda r, code, txt, txt2: r["ledger"]["head"] in txt),
)

#: Properties specific to what each scenario is supposed to PROVE.
SCENARIO_CHECKS: dict[str, tuple[tuple[str, Callable[[dict], bool]], ...]] = {
    "happy": (
        ("the webhook was adjudicated GREEN",
         lambda r: r["green"].get("green") is True
         and r["green"].get("severity") == "GREEN"),
        ("money authorised, and exactly the basket",
         lambda r: r["money_authorised"] is True
         and _authorised(r) == r["total_paise"]),
        ("INVARIANT 2: the settled amount equalled the intent",
         lambda r: r["green"]["amount_paise"] == r["green"]["expected_paise"]),
        ("nothing had to be refused",
         lambda r: r["refusals"] == []),
    ),
    "amber": (
        ("not one paisa was authorised",
         lambda r: r["money_authorised"] is False and _authorised(r) == 0),
        ("paisa refused to mint against an ambiguous crossing",
         lambda r: len(r["refusals"]) >= 1),
        ("the counter never went green",
         lambda r: r["green"].get("green") is not True),
        ("the total froze below what a full basket would have been",
         lambda r: r["total_paise"] < 31350),
    ),
    "offline": (
        ("billing continued locally while the link was down",
         lambda r: r["episode"]["offline_state"] == "PENDING_OFFLINE"
         and r["episode"]["offline_billable_paise"] > 0),
        ("nothing was authorised while offline",
         lambda r: r["episode"]["offline_authorised"] is False),
        ("exactly one intent was queued to drain",
         lambda r: r["episode"]["queue_depth"] == 1),
        ("settlement happened only after the link returned",
         lambda r: r["episode"]["restored_state"] == "AWAITING_SETTLEMENT"),
        ("the drained intent settled for exactly the basket",
         lambda r: r["money_authorised"] is True
         and _authorised(r) == r["total_paise"]),
    ),
    "mismatch": (
        ("the counter held RED instead of going green",
         lambda r: r["final_state"] == "AMOUNT_MISMATCH"),
        ("no money was authorised on the wrong number",
         lambda r: r["money_authorised"] is False and _authorised(r) == 0),
        ("INVARIANT 2: the refusal was the amount leg, at RED",
         lambda r: any(x.get("reason") == "amount_mismatch"
                       and x.get("severity") == "RED" for x in r["refusals"])),
        ("a valid signature was NOT sufficient to go green",
         lambda r: r["green"].get("green") is not True),
    ),
    "attack": (
        ("five distinct forgeries were refused",
         lambda r: len(r["refusals"]) >= 5),
        ("every refusal carries a machine-readable cause",
         lambda r: all(x.get("reason") or x.get("error") for x in r["refusals"])),
        ("INVARIANT 5: paisa refused a lying price server-side",
         lambda r: any(x.get("error") == "price_disagreement"
                       for x in r["refusals"])),
        ("INVARIANT 5: paisa refused a lying crossing server-side",
         lambda r: any(x.get("error") == "crossing_set_mismatch"
                       for x in r["refusals"])),
        ("INVARIANT 2: a tampered body failed the HMAC",
         lambda r: any(x.get("reason") == "bad_signature"
                       for x in r["refusals"])),
        ("INVARIANT 2: re-serialising the body also failed the HMAC",
         lambda r: sum(1 for x in r["refusals"]
                       if x.get("reason") == "bad_signature") >= 2),
        ("a byte-identical replay did not re-green the counter",
         lambda r: any(x.get("reason") == "replay" for x in r["refusals"])),
        ("the one genuine delivery still settled, exactly once",
         lambda r: r["money_authorised"] is True
         and _authorised(r) == r["total_paise"]),
    ),
}


def _run_demo(argv: list[str]) -> tuple[int, str]:
    """Run this file as a subprocess. Returns (exit code, stdout)."""
    import subprocess
    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__), *argv],
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout


def selftest(scenarios: Sequence[str], seed: int, as_json: bool) -> int:
    """Run every scenario end to end and assert what it was built to prove."""
    tty = bool(getattr(sys.stdout, "isatty", lambda: False)()) and not as_json
    use_colour = tty and not os.environ.get("NO_COLOR")

    def paint(text: str, style: str) -> str:
        if not use_colour:
            return text
        return _ANSI.get(style, "") + text + _ANSI["reset"]

    def out(line: str = "") -> None:
        if not as_json:
            sys.stdout.write(line + "\n")

    results: list[dict[str, Any]] = []
    vision: list[bool] = []
    n_pass = n_fail = 0
    out()
    out(paint("  GAWAAH demo self-test", "bold")
        + f"  ·  {len(scenarios)} scenario(s)  ·  seed {seed}")
    out(f"  {sys.executable}")
    out()

    for name in scenarios:
        base = ["--scenario", name, "--seed", str(seed)]
        code, raw = _run_demo(base + ["--json"])
        # Two plain runs: one to scan for ANSI, both to compare byte for byte.
        code_a, txt_a = _run_demo(base)
        _, txt_b = _run_demo(base)

        parsed: Optional[dict[str, Any]] = None
        parse_err = ""
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:            # pragma: no cover
            parse_err = f"--json did not emit parseable JSON: {exc}"

        checks: list[dict[str, Any]] = []
        if parsed is None:
            checks.append({"check": parse_err or "unparseable --json",
                           "ok": False})
        else:
            pairs: list[tuple[str, Callable[[], bool]]] = [
                (label, (lambda f=fn: bool(f(parsed, code_a, txt_a, txt_b))))
                for label, fn in UNIVERSAL_CHECKS
            ]
            pairs += [
                (label, (lambda f=fn: bool(f(parsed))))
                for label, fn in SCENARIO_CHECKS.get(name, ())
            ]
            for label, thunk in pairs:
                try:
                    ok = thunk()
                    detail = ""
                except Exception as exc:               # a check that blew up failed
                    ok, detail = False, f"{type(exc).__name__}: {exc}"
                checks.append({"check": label, "ok": ok, **({"detail": detail}
                                                           if detail else {})})

        failed = [c for c in checks if not c["ok"]]
        n_pass += len(checks) - len(failed)
        n_fail += len(failed)

        head = (parsed or {}).get("ledger", {}).get("head", "")
        state = (parsed or {}).get("final_state", "?")
        saw_mat = bool((parsed or {}).get("mat_lock", {}).get("available"))
        vision.append(saw_mat)
        mark = paint(" PASS ", "rgreen") if not failed else paint(" FAIL ", "rred")
        out(f"  {mark} {name:<9} exit {code:<2} {state:<16} "
            f"{len(checks) - len(failed):>2}/{len(checks)} checks"
            + (f"  {'camera' if saw_mat else 'NO-CAM'}")
            + (f"  head {head[:12]}" if head else ""))
        for c in failed:
            out("         " + paint("x " + c["check"], "red")
                + (("  — " + c["detail"]) if c.get("detail") else ""))

        results.append({
            "scenario": name,
            "exit_code": code,
            "final_state": state,
            "expected_final_state": EXPECTED_STATE[name],
            "ledger_head": head,
            "camera_stage_ran": saw_mat,
            "checks_run": len(checks),
            "checks_failed": len(failed),
            "failures": [c["check"] for c in failed],
            "ok": not failed,
        })

    ok = n_fail == 0
    camera_ran = all(vision) and bool(vision)
    out()
    verdict = paint(" ALL PASS ", "rgreen") if ok else paint(" FAILED ", "rred")
    out(f"  {verdict}  {n_pass} assertion(s) passed, {n_fail} failed, "
        f"across {len(scenarios)} scenario(s)")
    if not camera_ran:
        # A pass here is real but partial. Say so loudly rather than let a
        # green table imply the vision path was exercised when it was not.
        out()
        out("  " + paint(" NOTE ", "ramber")
            + "  OpenCV is not importable on this machine, so the camera stage")
        out("          was skipped. The money path, the ledger and the green")
        out("          predicate were fully exercised; the mat lock, the")
        out("          millimetre measurements and the crossings were NOT.")
    out()
    if as_json:
        sys.stdout.write(json.dumps(
            {"demo": "gawaah", "selftest": True, "seed": seed,
             "scenarios": results, "assertions_passed": n_pass,
             "assertions_failed": n_fail, "camera_stage_ran": camera_ran,
             "opencv_available": _HAVE_CV2, "ok": ok},
            indent=2, sort_keys=True) + "\n")
    return EXIT_OK if ok else EXIT_WRONG_STATE


# =============================================================================
# 8.  CLI
# =============================================================================


def default_out_dir(scenario: str, seed: int) -> str:
    """Deterministic per (scenario, seed) so two runs write the same path and
    therefore print byte-identical output."""
    return os.path.join(tempfile.gettempdir(), f"gawaah-demo-{scenario}-{seed}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tools/demo.py",
        description="GAWAAH — drive a whole synthetic counter session with the "
                    "real modules. No camera, no credentials, no network.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="scenarios:\n" + "\n".join(
            f"  {k:<9} {v}" for k, v in (
                (k, " ".join(_wrap(SCENARIO_BLURB[k], 60)[:1]) + " …")
                for k in SCENARIOS
            )
        ),
    )
    p.add_argument("--scenario", choices=SCENARIOS, default="happy")
    p.add_argument("--seed", type=int, default=7,
                   help="byte-reproducible: same seed, same bytes (default 7)")
    p.add_argument("--slow", action="store_true", help="pace it for filming")
    p.add_argument("--json", action="store_true",
                   help="emit one machine-readable JSON document and nothing else")
    p.add_argument("--no-color", action="store_true", help="never emit ANSI")
    p.add_argument("--ascii", action="store_true", help="never emit non-ASCII")
    p.add_argument("--out", default=None,
                   help="artefact directory (wiped first). Default is a stable "
                        "per-(scenario, seed) path under the system temp dir.")
    p.add_argument("--all", action="store_true",
                   help="run every scenario in order; exit non-zero if any fails")
    p.add_argument("--selftest", action="store_true",
                   help="run every scenario as a subprocess and assert exit 0, "
                        "the expected final state, and the invariants each "
                        "scenario exists to prove. Exits non-zero on any failure.")
    return p


def _stream_supports_unicode(stream: Any) -> bool:
    enc = getattr(stream, "encoding", None) or "ascii"
    try:
        "─▒█�ví".encode(enc)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def run_one(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    is_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    colour = is_tty and not args.no_color and not args.json and not os.environ.get(
        "NO_COLOR"
    )
    unicode_ok = (not args.ascii) and _stream_supports_unicode(sys.stdout)
    pace_s = 0.045 if args.slow else 0.0
    term = Term(
        sys.stdout,
        colour=colour,
        unicode_ok=unicode_ok,
        pace_s=pace_s,
        silent=bool(args.json),
    )
    if args.out is None:
        args.out = default_out_dir(args.scenario, args.seed)

    original_nonce = install_seeded_nonce(args.seed)
    demo = Demo(args, term)
    try:
        code = demo.run()
        return code, demo.result
    finally:
        demo.close()
        _kernel.new_nonce = original_nonce


def _scenario_given(argv: Optional[Sequence[str]]) -> bool:
    """True when the caller named a scenario explicitly. `--selftest` covers
    every scenario unless the caller narrowed it to one."""
    tokens = list(argv) if argv is not None else sys.argv[1:]
    return any(t == "--scenario" or t.startswith("--scenario=") for t in tokens)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.selftest:
        names = [args.scenario] if _scenario_given(argv) else list(SCENARIOS)
        return selftest(names, args.seed, bool(args.json))
    if args.all:
        results, worst = [], EXIT_OK
        base_out = args.out
        for name in SCENARIOS:
            one = argparse.Namespace(**vars(args))
            one.scenario = name
            one.all = False
            one.out = (os.path.join(base_out, name) if base_out else None)
            code, result = run_one(one)
            results.append(result)
            worst = worst or code
        if args.json:
            sys.stdout.write(json.dumps(
                {"demo": "gawaah", "scenarios": results,
                 "ok": all(r["ok"] for r in results)},
                indent=2, sort_keys=True) + "\n")
        else:
            sys.stdout.write("\n")
            for r in results:
                sys.stdout.write("  " + r["summary"] + "\n")
            sys.stdout.write("\n")
        return worst
    code, result = run_one(args)
    if args.json:
        sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
