#!/usr/bin/env python3
"""GAWAAH end-to-end scenario driver — the whole counter, wired by hand.

WHY THIS FILE EXISTS, AND WHY IT DOES NOT IMPORT ``gawaah.brain``
================================================================
``tests/test_end_to_end.py`` is the suite that has to catch a regression in the
integration itself. If it drove the system through the production orchestrator
it would be testing that orchestrator against itself: a brain that stopped
calling ``session.on_exit`` would still pass, because the suite would simply
stop expecting the call. So this module composes the SEVENTEEN REAL MODULES
independently —

    takhti -> placement -> identity -> sellevent -> session -> kernel
           -> rzp_sim -> webhook

— with its own wiring. Nothing here is mocked. Every millimetre comes out of a
homography fitted to real ArUco detections on a rendered A3 sheet; every rupee
comes out of ``money.total`` over integer paise; every green comes out of an
HMAC-SHA256 computed over the raw bytes a simulated Razorpay put on the wire.

WHAT IS SYNTHETIC, STATED PLAINLY
---------------------------------
1. The CAMERA. Frames are rendered: the printed mat is drawn at 4 px/mm, goods
   are composited onto it with 4x supersampled coverage (a pixel is an area
   integral, which is what a sensor computes), the sheet is projected through a
   pinhole at a fixed 3.0/2.0 degree tilt, and Gaussian sensor noise is added
   per frame. Everything downstream of that image is production code.
2. The EMBEDDER. ``block_embed`` is a mean-centred 8x8 block descriptor — an
   honest, weightless, deterministic function of the crop, injected into the
   real ``Identifier``. Goods carry a printed 4x4 two-level pattern, which is
   what makes different SKUs separable and what makes the un-enrolled item
   genuinely unrecognisable rather than rigged to fail.
3. TIME. ``VirtualClock`` everywhere, so a replay is byte-identical.
4. The GATEWAY. ``RazorpaySim``, which signs with a real HMAC and can be told
   to time out, error, duplicate, reorder or shade the amount.

TWO LEDGERS, ON PURPOSE
-----------------------
``Ledger`` caches its chain head in memory. That is correct for one writer and
silently wrong for two: the crash scenario restarts the kernel, and a fresh
``Ledger`` object over a file another live object still thinks it owns would
break the chain at the next append. The kernel is a separate process in the
real deployment, so it gets its own chain (``kernel.jsonl``) and the counter
keeps ``counter.jsonl``. BOTH are verified from genesis at the end of every
scenario; see ``ScenarioResult.ledger_ok``.

ONE SCENARIO USES A LOCKED LEDGER, AND SAYS SO
----------------------------------------------
``Ledger.append`` is not thread-safe — it reads ``_head``, writes, and only
then stores the new head, so concurrent appends fork the chain. Eleven of the
twelve scenarios use the real, unlocked ``Ledger``. ``scenario_webhook_storm``
uses ``SerialisedLedger``, because its subject is the KERNEL's exactly-once
guarantee under contention and it would otherwise be blocked by an unrelated
defect in the audit writer. That is documented on the class, not hidden.

INVARIANT 5 IS EXERCISED, NOT ASSUMED
-------------------------------------
``MoneyService`` is the only object that holds the webhook secret (it is
name-mangled and never returned), and ``open_intent`` REPLAYS the crossing
predicate server-side from the raw millimetre track before it will mint
anything. ``sellevent`` imports no cv2, so that replay really does run on a
machine that has never seen a camera. ``scenario_happy_path`` also ships a
doctored track through the same door and records the refusal.

Run ``python tools/e2e_scenarios.py`` to execute all scenarios and print the
structured results.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:            # `python tools/e2e_scenarios.py`
    sys.path.insert(0, str(ROOT))

import cv2
import numpy as np

from gawaah.clock import VirtualClock
from gawaah.identity import Gallery, Identifier
from gawaah.kernel import (
    ESCALATED, FAILED, SETTLED, GatewayResult, IllegalTransition, Kernel,
)
from gawaah.ledger import Ledger, verify as ledger_verify
from gawaah.money import from_rupees_str, to_rupees_str
from gawaah.money import total as sum_paise
from gawaah.placement import PlacementDetector
from gawaah.rzp_sim import Delivery, RazorpaySim, RazorpaySimError, RazorpaySimTimeout
from gawaah.sellevent import CentroidTracker, LineZone
from gawaah.session import Placement as SessionPlacement
from gawaah.session import Session, State
from gawaah.session import Verdict as SessionVerdict
from gawaah.takhti import (
    BUF_H, BUF_W, MAT_H_MM, PX_PER_MM_X, PX_PER_MM_Y, PlaneEngine, render_takhti,
)
from gawaah.webhook import GreenPredicate, GreenVerdict
from gawaah.webhook import Intent as WebhookIntent

__all__ = [
    "Sku", "CATALOGUE", "UNENROLLED", "Observation", "CrossingEvidence",
    "CrossingReport", "MintResult", "Adjudication", "ScenarioResult",
    "Rig", "MoneyService", "Counter", "SerialisedLedger",
    "block_embed", "replay_crossing", "build_rig",
    "scenario_happy_path", "scenario_amber_excluded", "scenario_revert",
    "scenario_wrong_amount", "scenario_tampered_webhook", "scenario_replay",
    "scenario_offline", "scenario_crash", "scenario_crash_before_gateway",
    "scenario_mat_lost", "scenario_concurrency", "scenario_webhook_storm",
    "ProcessDied", "SCENARIOS", "run_all",
]

# ------------------------------------------------------------------ constants

#: Rendering scale of the printed sheet, px/mm. 4 is comfortably above the
#: 2.828 px/mm of the rectified buffer, so rectification never up-samples.
SHEET_PX_PER_MM = 4.0
#: Supersample factor for coverage compositing of goods onto the sheet.
SS = 4
#: What white A3 reads at under the demo exposure. Deliberately not 255: a
#: saturated reference could not show a pale object at all.
PAPER_LEVEL = 200
#: The two ink levels of a printed wrapper. Both sit far below PAPER_LEVEL, so
#: the whole packet segments as one blob and the 50%-amplitude refit inside
#: placement.py never shatters it into "two goods".
INK_DARK = 40
INK_LIGHT = 80
#: Camera pose. A real counter phone is never exactly nadir.
TILT_DEG = (3.0, 2.0)
FRAME_W, FRAME_H = 960, 1280
FIT = 0.82
#: Per-frame Gaussian sensor noise, grey levels.
NOISE_SIGMA = 1.2

#: Frames a resting item is observed for before it is called stable. One more
#: than placement.STABLE_FRAMES so the stability gate actually closes.
REST_FRAMES = 6
#: Millimetres the shopkeeper's hand moves the item per frame on the way out.
CARRY_STEP_MM = 5.0
#: How far past the mat's far edge the carry continues.
CARRY_OVERSHOOT_MM = 26.0
#: Frames of empty mat appended after a carry, so the tracker declares the
#: track lost and the zone retires it under its own rules.
CARRY_TAIL_FRAMES = 5

#: Crop inset. The descriptor is taken strictly INSIDE the packet: a border
#: ring of paper is common to every SKU and swamps the cosine (measured: it
#: lifted inter-SKU similarity from 0.02 to 0.71).
CROP_INSET_MM = 3.0

WEBHOOK_SECRET = "gawaah_e2e_whsec_not_a_real_key"


# ------------------------------------------------------------------ catalogue

@dataclass(frozen=True)
class Sku:
    """One enrollable good. `pattern` is the printed 4x4 wrapper artwork."""

    sku_id: str
    name: str
    long_mm: float
    short_mm: float
    price_paise: int
    pattern: tuple[tuple[int, ...], ...]

    @property
    def cells(self) -> np.ndarray:
        return np.array(self.pattern, dtype=np.float32)


_CHECKER = ((1, 0, 1, 0), (0, 1, 0, 1), (1, 0, 1, 0), (0, 1, 0, 1))
_QUADS = ((1, 1, 0, 0), (1, 1, 0, 0), (0, 0, 1, 1), (0, 0, 1, 1))
_BANDS = ((1, 1, 1, 1), (0, 0, 0, 0), (1, 1, 1, 1), (0, 0, 0, 0))
_RING = ((0, 1, 1, 0), (1, 0, 0, 1), (1, 0, 0, 1), (0, 1, 1, 0))

#: The enrolled catalogue. PARLE_G and MAGGI are deliberately within
#: Identifier.tau_mm (4 mm) of each other, so the footprint tiebreak leaves a
#: real two-way shortlist and the cosine margin has to do actual work.
CATALOGUE: dict[str, Sku] = {
    "PARLE_G": Sku("PARLE_G", "Parle-G 100g", 70.0, 40.0,
                   int(from_rupees_str("10.00")), _CHECKER),
    "MAGGI": Sku("MAGGI", "Maggi 70g", 72.0, 42.0,
                 int(from_rupees_str("14.00")), _QUADS),
    "SURF": Sku("SURF", "Surf Excel 500g", 110.0, 50.0,
                int(from_rupees_str("35.50")), _BANDS),
}

#: Physically identical in footprint to PARLE_G, different artwork, and NEVER
#: enrolled. This is the item the counter must abstain on.
UNENROLLED = Sku("LOCAL_SOAP", "unknown", 70.0, 40.0,
                 int(from_rupees_str("0.00")), _RING)

ALL_SKUS: dict[str, Sku] = {**CATALOGUE, UNENROLLED.sku_id: UNENROLLED}

#: Where the three known goods sit during warm enrolment. Deliberately NOT the
#: positions any scenario uses, so identification is never scored against the
#: very crop it was enrolled from.
ENROL_LAYOUT: tuple[tuple[str, float, float], ...] = (
    ("PARLE_G", 75.0, 120.0),
    ("MAGGI", 215.0, 120.0),
    ("SURF", 148.0, 250.0),
)

#: Three lanes down the middle of the mat, far enough apart that no two
#: contours ever touch and an item carried out never passes over another.
LANE_X = 148.0
ROW_Y = (130.0, 220.0, 310.0)


# ------------------------------------------------------- the synthetic camera

def _base_sheet() -> np.ndarray:
    """The printed TAKHTI at SHEET_PX_PER_MM, exposed to PAPER_LEVEL."""
    mat = render_takhti(SHEET_PX_PER_MM)
    return np.clip(mat.astype(np.float32) * (PAPER_LEVEL / 255.0) + 9.0,
                   0, 255).astype(np.uint8)


_SHEET_CACHE: np.ndarray | None = None


def mat_sheet() -> np.ndarray:
    """A fresh copy of the printed sheet, ready to have goods pasted on it."""
    global _SHEET_CACHE
    if _SHEET_CACHE is None:
        _SHEET_CACHE = _base_sheet()
    return _SHEET_CACHE.copy()


def paste_item(sheet: np.ndarray, sku: Sku, cx_mm: float, cy_mm: float) -> None:
    """Composite one good onto the sheet IN PLACE, with 4x coverage AA.

    Only the item's own bounding box is supersampled. Supersampling the whole
    A3 sheet allocates 128 MB per frame and was the single slowest thing in an
    earlier draft of this harness.
    """
    h, w = sheet.shape
    x0 = (cx_mm - sku.long_mm / 2.0) * SHEET_PX_PER_MM
    y0 = (cy_mm - sku.short_mm / 2.0) * SHEET_PX_PER_MM
    x1 = (cx_mm + sku.long_mm / 2.0) * SHEET_PX_PER_MM
    y1 = (cy_mm + sku.short_mm / 2.0) * SHEET_PX_PER_MM

    rx0, ry0 = int(np.floor(x0)) - 1, int(np.floor(y0)) - 1
    rx1, ry1 = int(np.ceil(x1)) + 1, int(np.ceil(y1)) + 1
    cx0, cy0 = max(rx0, 0), max(ry0, 0)
    cx1, cy1 = min(rx1, w), min(ry1, h)
    if cx1 <= cx0 or cy1 <= cy0:
        return

    rw, rh = (cx1 - cx0) * SS, (cy1 - cy0) * SS
    cover = np.zeros((rh, rw), np.float32)
    ink = np.zeros((rh, rw), np.float32)
    cells = sku.cells
    nr, nc = cells.shape
    for r in range(nr):
        for c in range(nc):
            gx0 = x0 + (x1 - x0) * c / nc
            gx1 = x0 + (x1 - x0) * (c + 1) / nc
            gy0 = y0 + (y1 - y0) * r / nr
            gy1 = y0 + (y1 - y0) * (r + 1) / nr
            pt0 = (int(round((gx0 - cx0) * SS)), int(round((gy0 - cy0) * SS)))
            pt1 = (int(round((gx1 - cx0) * SS)) - 1,
                   int(round((gy1 - cy0) * SS)) - 1)
            level = INK_LIGHT if cells[r, c] > 0 else INK_DARK
            cv2.rectangle(cover, pt0, pt1, 1.0, -1)
            cv2.rectangle(ink, pt0, pt1, float(level), -1)

    cov = cv2.resize(cover, (cx1 - cx0, cy1 - cy0), interpolation=cv2.INTER_AREA)
    val = cv2.resize(ink, (cx1 - cx0, cy1 - cy0), interpolation=cv2.INTER_AREA)
    roi = sheet[cy0:cy1, cx0:cx1].astype(np.float32)
    sheet[cy0:cy1, cx0:cx1] = np.clip(
        np.rint(roi * (1.0 - cov) + val), 0, 255).astype(np.uint8)


def occlude(sheet: np.ndarray, y0_mm: float, y1_mm: float) -> None:
    """Lay something opaque across the sheet IN PLACE. Used to hide markers."""
    h, w = sheet.shape
    a = max(int(round(y0_mm * SHEET_PX_PER_MM)), 0)
    b = min(int(round(y1_mm * SHEET_PX_PER_MM)), h)
    sheet[a:b, :] = 30


def _projection() -> tuple[np.ndarray, np.ndarray]:
    """Homography sheet -> camera frame, plus the sheet's silhouette."""
    sheet = mat_sheet()
    h, w = sheet.shape
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)
    ax, ay = np.radians(TILT_DEG[0]), np.radians(TILT_DEG[1])
    hw, hh = w / 2.0, h / 2.0
    pts = np.array([[-hw, -hh, 0], [hw, -hh, 0], [hw, hh, 0], [-hw, hh, 0]],
                   np.float64)
    rx = np.array([[1, 0, 0], [0, np.cos(ax), -np.sin(ax)],
                   [0, np.sin(ax), np.cos(ax)]])
    ry = np.array([[np.cos(ay), 0, np.sin(ay)], [0, 1, 0],
                   [-np.sin(ay), 0, np.cos(ay)]])
    pts = pts @ rx.T @ ry.T
    f = max(w, h) * 2.2
    dist = f * max(w / (FIT * FRAME_W), h / (FIT * FRAME_H))
    dst = np.array([[f * x / (dist + z) + FRAME_W / 2.0,
                     f * y / (dist + z) + FRAME_H / 2.0] for x, y, z in pts],
                   np.float32)
    m = cv2.getPerspectiveTransform(src, dst)
    silhouette = cv2.warpPerspective(np.full_like(sheet, 255), m,
                                     (FRAME_W, FRAME_H), borderValue=0)
    return m, silhouette


_PROJ_CACHE: tuple[np.ndarray, np.ndarray] | None = None


def camera_frame(sheet: np.ndarray, *, seed: int = 0,
                 sigma: float = NOISE_SIGMA) -> np.ndarray:
    """One camera exposure of the sheet: project, matte, add sensor noise."""
    global _PROJ_CACHE
    if _PROJ_CACHE is None:
        _PROJ_CACHE = _projection()
    m, silhouette = _PROJ_CACHE
    frame = np.full((FRAME_H, FRAME_W), 235, np.uint8)
    warped = cv2.warpPerspective(sheet, m, (FRAME_W, FRAME_H), borderValue=235)
    frame[silhouette > 128] = warped[silhouette > 128]
    if sigma > 0.0:
        rng = np.random.default_rng(seed)
        frame = np.clip(frame.astype(np.float32)
                        + rng.normal(0.0, sigma, frame.shape),
                        0, 255).astype(np.uint8)
    return frame


# --------------------------------------------------------------- the embedder

def block_embed(crop: np.ndarray, n: int = 8) -> np.ndarray:
    """Mean-centred n x n block descriptor of a rectified crop.

    Weightless and deterministic — INVARIANT 3 is about not shipping model
    weights, and this ships none. Mean-centring is what makes it a descriptor
    of the PATTERN rather than of the exposure: two uniform patches of
    different brightness are identical after centring, which is exactly right,
    because "this packet is darker today" is a lighting fact, not an identity.
    """
    if crop.size == 0:
        return np.zeros(n * n, np.float64)
    g = cv2.resize(crop.astype(np.float32), (n, n), interpolation=cv2.INTER_AREA)
    v = g.ravel().astype(np.float64)
    v = v - v.mean()
    norm = float(np.linalg.norm(v))
    if norm < 1e-9:
        # A featureless crop. Return a fixed direction rather than a zero
        # vector, so cosine() has something defined to work with; it will score
        # ~0 against every patterned entry and the caller will abstain.
        return np.full(n * n, 1.0 / np.sqrt(n * n), np.float64)
    return v / norm


def crop_of(buffer: np.ndarray, centre_mm: tuple[float, float],
            long_edge_mm: float, short_edge_mm: float,
            inset_mm: float = CROP_INSET_MM) -> np.ndarray:
    """The rectified crop of one placement, inset to exclude the paper rim."""
    cx, cy = centre_mm
    hl = max(long_edge_mm / 2.0 - inset_mm, 2.0)
    hs = max(short_edge_mm / 2.0 - inset_mm, 2.0)
    x0 = max(int(round((cx - hl) * PX_PER_MM_X)), 0)
    x1 = min(int(round((cx + hl) * PX_PER_MM_X)), BUF_W)
    y0 = max(int(round((cy - hs) * PX_PER_MM_Y)), 0)
    y1 = min(int(round((cy + hs) * PX_PER_MM_Y)), BUF_H)
    return buffer[y0:y1, x0:x1]


# ------------------------------------------------------- perception artefacts

@dataclass(frozen=True)
class Observation:
    """One good, measured on the plane and either named or abstained on."""

    label: str                   # harness ground truth: which SKU was pasted
    item_id: str                 # what the session knows it as
    centre_mm: tuple[float, float]
    long_edge_mm: float | None
    short_edge_mm: float | None
    stable: bool
    placement_reason: str
    sku_id: str | None
    name: str | None
    price_paise: int | None
    identity_reason: str
    top1: float
    margin: float
    n_candidates: int

    @property
    def amber(self) -> bool:
        return self.price_paise is None


@dataclass(frozen=True)
class CrossingEvidence:
    """The raw millimetre track of one basket, shippable to the money service.

    This is the whole of INVARIANT 5's payload: no image, no crop, no model
    output — just where each centroid was on the plane in each frame. paisa can
    re-derive the sale from it without OpenCV and without trusting the phone.
    """

    frames: tuple[tuple[tuple[float, float], ...], ...]
    claimed_net: int

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps([[list(p) for p in f] for f in self.frames],
                       separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class CrossingReport:
    """What one carry-out did, as the sell-event module judged it."""

    label: str
    item_id: str | None
    frames: int
    committed: bool
    net_count: int
    out_count: int
    back_count: int
    amber: bool
    exceptions: tuple[str, ...]
    session_state: str
    total_paise: int


# ---------------------------------------------------------- money-side result

@dataclass(frozen=True)
class MintResult:
    minted: bool
    reason: str
    nonce: str | None = None
    link_id: str | None = None
    short_url: str | None = None
    amount_paise: int | None = None
    replay_net: int | None = None
    replay_amber: bool = False
    kernel_state: str | None = None


@dataclass(frozen=True)
class Adjudication:
    """One webhook delivery, as paisa judged it."""

    green: bool
    reason: str
    severity: str
    event_id: str
    signature_valid: bool
    amount_paise: int | None
    expected_paise: int | None
    payment_id: str | None
    session_state_before: str
    session_state_after: str
    session_reason: str
    kernel_state: str | None


# ------------------------------------------------------------- scenario shape

@dataclass
class ScenarioResult:
    """Everything a test needs to judge one scenario, and nothing it must guess."""

    name: str
    final_state: str
    total_paise: int
    expected_paise: int
    money_authorised: bool
    authorised_paise: int | None
    observations: tuple[Observation, ...] = ()
    crossings: tuple[CrossingReport, ...] = ()
    mints: tuple[MintResult, ...] = ()
    adjudications: tuple[Adjudication, ...] = ()
    amber_item_ids: tuple[str, ...] = ()
    committed_item_ids: tuple[str, ...] = ()
    #: kernel rows, as plain dicts: nonce, state, amount_paise, payment_id...
    intents: tuple[dict[str, Any], ...] = ()
    settled_intents: int = 0
    gateway_links: int = 0
    gateway_payments: int = 0
    gateway_deliveries: int = 0
    ledger_ok: bool = False
    counter_ledger: dict[str, Any] = field(default_factory=dict)
    kernel_ledger: dict[str, Any] = field(default_factory=dict)
    #: every ledger line either chain wrote, counter first. Read-only evidence.
    ledger_lines: tuple[dict[str, Any], ...] = ()
    frames_rendered: int = 0
    notes: dict[str, Any] = field(default_factory=dict)

    def ledger_reasons(self, module: str = "session") -> list[str]:
        return [r.get("reason", "") for r in self.ledger_lines
                if r.get("module") == module]

    def lines_where(self, **match: Any) -> list[dict[str, Any]]:
        return [r for r in self.ledger_lines
                if all(r.get(k) == v for k, v in match.items())]

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ledger_lines"] = len(self.ledger_lines)
        return d


# --------------------------------------------------------------------- replay

def replay_crossing(evidence: CrossingEvidence, *,
                    max_dist_mm: float = 25.0,
                    max_missing_frames: int = 3,
                    min_crossing_frames: int = 3) -> tuple[int, bool, tuple[str, ...]]:
    """Re-derive the sale from the raw millimetre track. NO OpenCV involved.

    This is what makes INVARIANT 5 a property rather than a promise: the money
    service builds its own tracker and its own line zone from scratch and
    reaches its own verdict, so a phone that lies about how many items crossed
    is contradicted by arithmetic rather than trusted.
    """
    tracker = CentroidTracker(max_dist_mm=max_dist_mm,
                              max_missing_frames=max_missing_frames)
    zone = LineZone.mat_exit_line(min_crossing_frames=min_crossing_frames,
                                  evict_after_frames=max_missing_frames + 1)
    for centroids in evidence.frames:
        upd = tracker.update(list(centroids))
        zone.update(upd.tracks, untracked=upd.untracked, lost=upd.lost)
    final = zone.flush()
    # `flush()` reports only the exceptions IT raised; the running list on the
    # zone is every uncounted crossing in the basket. Returning the flush's
    # slice would report `amber=True` beside an empty reason list, which is the
    # one shape an abstention must never take.
    return final.net_count, final.amber, tuple(str(e) for e in zone.exceptions)


# ------------------------------------------------------------------- the rig

@dataclass
class Rig:
    """One counter's worth of durable state, on a real temporary filesystem."""

    root: Path
    clock: VirtualClock
    ledger: Ledger              # the counter's chain: session, webhook, gateway
    kernel_ledger: Ledger       # the money daemon's chain
    kernel: Kernel
    sim: RazorpaySim
    session: Session
    money: "MoneyService"
    engine: PlaneEngine
    detector: PlacementDetector
    identifier: Identifier
    reference: np.ndarray

    def close(self) -> None:
        self.kernel.close()

    def destroy(self) -> None:
        self.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def verify_ledgers(self) -> tuple[bool, dict[str, Any], dict[str, Any]]:
        a_ok, a_n, a_head, a_err = ledger_verify(self.ledger.path)
        b_ok, b_n, b_head, b_err = ledger_verify(self.kernel_ledger.path)
        return (
            bool(a_ok and b_ok),
            {"ok": bool(a_ok), "lines": a_n, "head": a_head, "error": a_err,
             "path": str(self.ledger.path)},
            {"ok": bool(b_ok), "lines": b_n, "head": b_head, "error": b_err,
             "path": str(self.kernel_ledger.path)},
        )

    def all_lines(self) -> tuple[dict[str, Any], ...]:
        return tuple(list(self.ledger.read()) + list(self.kernel_ledger.read()))


# ------------------------------------------------------------- the enrolment

_ENROL_CACHE: tuple[np.ndarray, Gallery, dict[str, float]] | None = None


def _build_reference_and_gallery() -> tuple[np.ndarray, Gallery, dict[str, float]]:
    """Rectify an empty mat, then WARM-ENROL the catalogue from real crops.

    The footprint stored in the gallery is the one the plane MEASURED, not the
    one the harness pasted. That is the honest thing to enrol, and it is what
    makes the 4 mm footprint tiebreak meaningful downstream.
    """
    engine = PlaneEngine()
    empty = camera_frame(mat_sheet(), seed=1)
    lock = engine.detect(empty)
    if not lock.locked:
        raise RuntimeError(f"harness cannot lock an empty mat: {lock.reason}")
    reference = engine.rectify(empty, lock.H)

    sheet = mat_sheet()
    for sku_id, cx, cy in ENROL_LAYOUT:
        paste_item(sheet, CATALOGUE[sku_id], cx, cy)

    detector = PlacementDetector(reference)
    placements: list = []
    buffer = reference
    for i in range(REST_FRAMES):
        frame = camera_frame(sheet, seed=100 + i)
        flock = engine.detect(frame)
        if not flock.locked:
            raise RuntimeError(f"enrolment frame {i} lost the mat: {flock.reason}")
        buffer = engine.rectify(frame, flock.H)
        placements = detector.update(buffer)

    gallery = Gallery()
    footprints: dict[str, float] = {}
    for sku_id, cx, cy in ENROL_LAYOUT:
        match = _nearest(placements, cx, cy)
        if match is None or not match.measurable or not match.stable:
            raise RuntimeError(f"enrolment: {sku_id} did not settle at ({cx},{cy})")
        vec = block_embed(crop_of(buffer, match.centre_mm, match.long_edge_mm,
                                  match.short_edge_mm))
        gallery.enroll(sku_id, [vec], match.long_edge_mm)
        footprints[sku_id] = match.long_edge_mm
    return reference, gallery, footprints


def enrolled() -> tuple[np.ndarray, Gallery, dict[str, float]]:
    """The empty-mat reference and the warm-enrolled gallery, built once.

    Cached because the camera pose and the printed sheet are fixed constants,
    so every rig would rebuild an identical pair; `test_end_to_end` asserts the
    cached reference still locks in a fresh rig.
    """
    global _ENROL_CACHE
    if _ENROL_CACHE is None:
        _ENROL_CACHE = _build_reference_and_gallery()
    ref, gallery, footprints = _ENROL_CACHE
    return ref.copy(), gallery, dict(footprints)


def _nearest(placements: Sequence[Any], cx: float, cy: float,
             tol_mm: float = 9.0):
    best, best_d = None, tol_mm
    for p in placements:
        d = float(np.hypot(p.centre_mm[0] - cx, p.centre_mm[1] - cy))
        if d <= best_d:
            best, best_d = p, d
    return best


# ------------------------------------------------------------- money service

class MoneyService:
    """The paisa role. Sole holder of the webhook secret.

    Three jobs, and it refuses to do anything else:

      1. RE-RUN THE CROSSING PREDICATE (invariant 5). It will not mint against
         a number the phone reports; it rebuilds a `CentroidTracker` and a
         `LineZone` from the raw millimetre track and reaches its own count.
      2. WRITE-AHEAD THEN CALL (kernel ordering). create_intent, mark_calling,
         close the connection, and only then touch the gateway.
      3. ADJUDICATE WEBHOOKS. HMAC-SHA256 over the raw bytes BEFORE any JSON
         parse, then the other three legs of the green predicate.

    There is no method here that constructs a UPI payload, and none that can be
    made to (INVARIANT 6). The gateway mints its own link; we read a string.
    """

    def __init__(self, *, secret: str, clock: VirtualClock, kernel: Kernel,
                 sim: RazorpaySim, ledger: Ledger) -> None:
        # Name-mangled, never returned, never logged, never put in a ledger row.
        self.__secret = secret
        self._clock = clock
        self.kernel = kernel
        self.sim = sim
        self.predicate = GreenPredicate(self._open_intent, ledger=ledger,
                                        clock=clock)
        self.refusals: list[str] = []

    # -- invariant 5 ---------------------------------------------------------

    def _open_intent(self, session_id: str) -> WebhookIntent | None:
        """session_id -> the one intent still awaiting an answer, or None."""
        found = None
        for it in self.kernel.all_intents():
            if it.session_id != session_id:
                continue
            if it.state in (SETTLED, FAILED, ESCALATED):
                continue
            found = it
        if found is None:
            return None
        return WebhookIntent(session_id=session_id,
                             amount_paise=found.amount_paise, state="OPEN")

    def open_intent(self, session_id: str, claimed_paise: int,
                    evidence: CrossingEvidence, *, cycle: int = 0,
                    crash_hook: Callable[[str], None] | None = None) -> MintResult:
        """Mint a payment target, but only if the server-side replay agrees.

        `crash_hook` is the SIGKILL seam. It is invoked at exactly one point —
        after the write-ahead row is durably CALLING and before a single byte
        goes to the gateway — and a hook that raises leaves the process dead
        there, with no cleanup and no mark_indeterminate, which is what a real
        kill -9 leaves behind.

        The seam deliberately lives HERE rather than in the scenario, so it is
        pinned to this method's own ordering: if somebody ever reordered this to
        call the gateway before `mark_calling`, the hook would fire too late and
        `scenario_crash_before_gateway` would find a link at the gateway it has
        asserted cannot exist.
        """
        net, amber, exceptions = replay_crossing(evidence)
        if amber:
            why = f"server_side_replay_amber:{len(exceptions)}_exception(s)"
            self.refusals.append(why)
            return MintResult(False, why, replay_net=net, replay_amber=True)
        if net != evidence.claimed_net:
            why = (f"server_side_replay_disagrees:counter_claimed="
                   f"{evidence.claimed_net}:paisa_counted={net}")
            self.refusals.append(why)
            return MintResult(False, why, replay_net=net)

        intent = self.kernel.create_intent(session_id, claimed_paise, cycle)
        try:
            intent = self.kernel.mark_calling(intent.nonce)
        except IllegalTransition:
            # The idempotency key already had a row, and that row is past NEW.
            # Somebody else is calling the gateway for it, or already did. The
            # only safe move is to make no call at all.
            state = self.kernel.get(intent.nonce).state
            why = f"intent_already_{state}:only_one_gateway_call_per_intent"
            self.refusals.append(why)
            return MintResult(False, why, nonce=intent.nonce,
                              replay_net=net, kernel_state=state)

        # ---- no DB connection is held here; this is the gateway call ----
        if crash_hook is not None:
            crash_hook(intent.nonce)     # the process may not return from this
        try:
            link = self.sim.create_payment_link(
                claimed_paise,
                notes={"session_id": session_id, "nonce": intent.nonce},
                reference_id=intent.nonce,
                description="GAWAAH counter",
                idempotent=True,
            )
        except RazorpaySimTimeout as exc:
            self.kernel.mark_indeterminate(intent.nonce, reason=f"timeout:{exc}")
            why = "gateway_timeout"
            self.refusals.append(why)
            return MintResult(False, why, nonce=intent.nonce, replay_net=net,
                              kernel_state=self.kernel.get(intent.nonce).state)
        except RazorpaySimError as exc:
            self.kernel.mark_failed(intent.nonce, reason=f"gateway:{exc.code}")
            why = f"gateway_error:{exc.code}"
            self.refusals.append(why)
            return MintResult(False, why, nonce=intent.nonce, replay_net=net,
                              kernel_state=self.kernel.get(intent.nonce).state)

        return MintResult(
            True, "minted", nonce=intent.nonce, link_id=link["id"],
            short_url=link["short_url"], amount_paise=int(link["amount"]),
            replay_net=net, kernel_state=self.kernel.get(intent.nonce).state,
        )

    # -- the green predicate -------------------------------------------------

    def adjudicate(self, delivery: Mapping[str, Any]) -> tuple[GreenVerdict, str | None, str | None]:
        """Verify, then (and only then) read. Returns (verdict, payment_id, nonce)."""
        raw = delivery["body"]
        headers = delivery["headers"]
        verdict = self.predicate.evaluate(
            raw, headers.get("X-Razorpay-Signature", ""), self.__secret,
            header_event_id=headers.get("X-Razorpay-Event-Id"),
        )
        if not verdict.green:
            return verdict, None, None
        # The HMAC has passed. Only now is this bytes-blob a document.
        body = json.loads(raw.decode("utf-8"))
        entity = body["payload"]["payment"]["entity"]
        return verdict, str(entity["id"]), str(entity["notes"].get("nonce", "")) or None

    def confirm(self, nonce: str, payment_id: str):
        """Record the settlement in the durable store. Idempotent by contract."""
        return self.kernel.mark_settled(nonce, payment_id)

    # -- reconciliation ------------------------------------------------------

    def gateway_lookup(self, nonce: str) -> GatewayResult:
        """Read-only: what does the gateway say happened to this nonce?

        There is no charge path in here. It is the only thing `reconcile` is
        allowed to call, which is why running it a hundred times is free.
        """
        found = self.sim.fetch_payment_links(reference_id=nonce)
        if not found["items"]:
            return GatewayResult(found=False, status="not_found")
        link = found["items"][0]
        if link["status"] != "paid" or not link["payments"]:
            return GatewayResult(found=True, status=str(link["status"]),
                                 amount_paise=int(link["amount"]))
        pay = link["payments"][0]
        return GatewayResult(found=True, payment_id=str(pay["payment_id"]),
                             amount_paise=int(link["amount_paid"]),
                             status=str(pay["status"]))

    def __repr__(self) -> str:      # never leak the secret into a traceback
        return (f"MoneyService(intents={self.kernel.count()}, "
                f"secret=<{len(self.__secret)} chars redacted>)")


# ----------------------------------------------------------------- the counter

class Counter:
    """The wiring a brain would do, done here by hand so a brain cannot hide.

    Holds one plane engine, one placement detector, one identifier, one
    centroid tracker, one line zone, one session — and talks to `MoneyService`
    across the same boundary the real phone would: it hands over a millimetre
    track and a claimed total, and it never sees the secret.
    """

    def __init__(self, rig: Rig) -> None:
        self.rig = rig
        self.session = rig.session
        self.money = rig.money
        self.tracker = CentroidTracker(max_dist_mm=25.0, max_missing_frames=3)
        self.zone = LineZone.mat_exit_line(min_crossing_frames=3,
                                           evict_after_frames=4)
        self.layout: dict[str, tuple[Sku, float, float]] = {}
        self.rest_mm: dict[str, tuple[float, float]] = {}
        self.item_id: dict[str, str] = {}
        self.observations: list[Observation] = []
        self.crossings: list[CrossingReport] = []
        self.mints: list[MintResult] = []
        self.adjudications: list[Adjudication] = []
        self.frames: tuple[tuple[tuple[float, float], ...], ...] = ()
        self._frame_log: list[tuple[tuple[float, float], ...]] = []
        self.frames_rendered = 0
        self.nonce: str | None = None
        self._seed = 1000
        self.mat_locked = False
        rig.sim.set_sink(self._on_delivery)

    # -- helpers -------------------------------------------------------------

    def _next_seed(self) -> int:
        self._seed += 1
        return self._seed

    def _render(self, *, occlude_mm: tuple[float, float] | None = None,
                extra: Mapping[str, tuple[Sku, float, float]] | None = None
                ) -> np.ndarray:
        sheet = mat_sheet()
        for sku, cx, cy in list(self.layout.values()) + list((extra or {}).values()):
            paste_item(sheet, sku, cx, cy)
        if occlude_mm is not None:
            occlude(sheet, occlude_mm[0], occlude_mm[1])
        self.frames_rendered += 1
        return camera_frame(sheet, seed=self._next_seed())

    def _rectify(self, frame: np.ndarray):
        lock = self.rig.engine.detect(frame)
        if not lock.locked:
            return lock, None
        return lock, self.rig.engine.rectify(frame, lock.H)

    # -- mat -----------------------------------------------------------------

    def acquire_mat(self):
        """Detect the printed mat for real, then tell the session."""
        lock, _ = self._rectify(self._render())
        self.mat_locked = bool(lock.locked)
        return lock, self.session.on_mat_lock(bool(lock.locked))

    def lose_mat(self, y0_mm: float = 0.0, y1_mm: float = 62.0):
        """Occlude the top of the sheet so two ArUco markers vanish."""
        lock, _ = self._rectify(self._render(occlude_mm=(y0_mm, y1_mm)))
        if lock.locked:
            raise RuntimeError("occlusion failed to break the mat lock")
        self.mat_locked = False
        return lock, self.session.on_mat_lock(False)

    # -- placement + identity ------------------------------------------------

    def arrive(self, items: Mapping[str, tuple[str, float, float]],
               frames: int = REST_FRAMES) -> list[Observation]:
        """Put goods on the mat, watch them settle, price what we recognise."""
        for label, (sku_id, cx, cy) in items.items():
            self.layout[label] = (ALL_SKUS[sku_id], cx, cy)

        placements: list = []
        buffer: np.ndarray | None = None
        for _ in range(frames):
            frame = self._render()
            lock, rect = self._rectify(frame)
            if rect is None:
                raise RuntimeError(f"arrive() lost the mat: {lock.reason}")
            placements = self.rig.detector.update(rect)
            buffer = rect

        out: list[Observation] = []
        for label in items:
            _, cx, cy = self.layout[label]
            p = _nearest(placements, cx, cy)
            if p is None:
                raise RuntimeError(f"arrive(): nothing detected at {label} "
                                   f"({cx},{cy})")
            item_id = f"item{p.id}"
            self.item_id[label] = item_id
            self.rest_mm[label] = p.centre_mm

            if p.measurable and p.long_edge_mm is not None:
                ident = self.rig.identifier.identify(
                    block_embed(crop_of(buffer, p.centre_mm, p.long_edge_mm,
                                        p.short_edge_mm)),
                    p.long_edge_mm)
                sku_id = ident.sku_id
                reason = ident.reason
                top1, margin, ncand = ident.top1, ident.margin, ident.n_candidates
            else:
                sku_id, reason = None, p.reason
                top1, margin, ncand = 0.0, 0.0, 0
            sku = CATALOGUE.get(sku_id) if sku_id else None
            obs = Observation(
                label=label, item_id=item_id, centre_mm=p.centre_mm,
                long_edge_mm=p.long_edge_mm, short_edge_mm=p.short_edge_mm,
                stable=bool(p.stable), placement_reason=p.reason,
                sku_id=sku_id, name=sku.name if sku else None,
                price_paise=sku.price_paise if sku else None,
                identity_reason=reason, top1=top1, margin=margin,
                n_candidates=ncand,
            )
            self.observations.append(obs)
            out.append(obs)
            self.session.on_placement(SessionPlacement(
                item_id=item_id, name=obs.name, price_paise=obs.price_paise,
                reason=reason,
            ))
        return out

    # -- the sell event ------------------------------------------------------

    def _visible_centroid(self, label: str, centre_y: float) -> tuple[float, float]:
        """Where the CROPPED blob's centroid sits once the item overhangs the mat.

        Modelled, not guessed: it is the same clamp the imaged path measures,
        because a contour that runs off the buffer only publishes the part that
        is still on it. Without this the synthetic track would sail smoothly
        past the exit line while the imaged one decelerates, and the two would
        stop testing the same thing.
        """
        sku, cx, _ = self.layout[label]
        half = sku.short_mm / 2.0
        top = centre_y - half
        bottom = min(centre_y + half, MAT_H_MM)
        return (cx, (top + bottom) / 2.0)

    def _step_zone(self, centroids: Sequence[tuple[float, float]]):
        upd = self.tracker.update(list(centroids))
        res = self.zone.update(upd.tracks, untracked=upd.untracked,
                               lost=upd.lost)
        self._frame_log.append(tuple((float(x), float(y)) for x, y in centroids))
        return upd, res

    def _tid_for(self, upd, point: tuple[float, float]) -> int | None:
        for tid, pos in upd.tracks.items():
            if abs(pos[0] - point[0]) < 1e-9 and abs(pos[1] - point[1]) < 1e-9:
                return tid
        return None

    def carry_out(self, label: str, *, imaged: bool = False) -> CrossingReport:
        """Slide one good off the far edge of the mat, through the real zone."""
        sku, cx, cy0 = self.layout[label]
        others = [l for l in self.layout if l != label]
        committed = False
        n_frames = 0
        exceptions: list[str] = []
        item_id = self.item_id.get(label)

        y = cy0
        while y <= MAT_H_MM + CARRY_OVERSHOOT_MM:
            if imaged:
                self.layout[label] = (sku, cx, y)
                frame = self._render()
                lock, rect = self._rectify(frame)
                if rect is None:
                    raise RuntimeError(f"carry_out lost the mat: {lock.reason}")
                placements = self.rig.detector.update(rect)
                centroids = [p.centre_mm for p in placements]
                moving = _nearest(placements, cx, min(y, MAT_H_MM), tol_mm=30.0)
                moving_pt = moving.centre_mm if moving is not None else None
            else:
                centroids = [self.rest_mm[l] for l in others]
                moving_pt = self._visible_centroid(label, y)
                centroids.append(moving_pt)

            upd, res = self._step_zone(centroids)
            n_frames += 1
            exceptions.extend(str(e) for e in res.exceptions)
            tid = self._tid_for(upd, moving_pt) if moving_pt is not None else None
            if tid is not None and tid in res.crossed_out and not committed:
                committed = True
                self.session.on_exit(item_id)
            y += CARRY_STEP_MM

        # The good is off the mat now, whichever way it got there.
        self.layout.pop(label, None)
        self.rest_mm.pop(label, None)

        for _ in range(CARRY_TAIL_FRAMES):
            if imaged:
                frame = self._render()
                lock, rect = self._rectify(frame)
                if rect is None:
                    raise RuntimeError(f"carry tail lost the mat: {lock.reason}")
                centroids = [p.centre_mm for p in self.rig.detector.update(rect)]
            else:
                centroids = [self.rest_mm[l] for l in self.layout]
            _, res = self._step_zone(centroids)
            n_frames += 1
            exceptions.extend(str(e) for e in res.exceptions)

        report = CrossingReport(
            label=label, item_id=item_id, frames=n_frames, committed=committed,
            net_count=self.zone.net_count, out_count=self.zone.out_count,
            back_count=self.zone.back_count, amber=self.zone.amber,
            exceptions=tuple(exceptions),
            session_state=self.session.state.value,
            total_paise=int(self.session.total_paise),
        )
        self.crossings.append(report)
        return report

    def evidence(self, claimed_net: int | None = None) -> CrossingEvidence:
        """The millimetre track, as shipped to paisa."""
        net = self.zone.net_count if claimed_net is None else claimed_net
        return CrossingEvidence(frames=tuple(self._frame_log), claimed_net=net)

    # -- settlement ----------------------------------------------------------

    def done(self, *, claimed_net: int | None = None, cycle: int = 0,
             crash_hook: Callable[[str], None] | None = None
             ) -> tuple[Any, MintResult | None]:
        """DONE tap: lock the basket, then ask paisa for a payment target."""
        transition = self.session.on_done()
        if self.session.state is State.PENDING_OFFLINE:
            # R6: billing happened, authorisation cannot. Nothing is minted.
            return transition, None
        if self.session.intent_amount_paise is None:
            return transition, None
        mint = self.money.open_intent(
            self.session.session_id, self.session.intent_amount_paise,
            self.evidence(claimed_net), cycle=cycle, crash_hook=crash_hook)
        self.mints.append(mint)
        if mint.minted:
            self.nonce = mint.nonce
        return transition, mint

    def retry_mint(self, cycle: int) -> MintResult:
        """Ask paisa for a payment target again, on a fresh idempotency cycle.

        This is what a shopkeeper tapping RETRY does after a mint died: the
        basket is unchanged, so the amount is unchanged, and only the cycle
        moves — which is exactly what makes the new nonce a DIFFERENT key
        rather than a second attempt at a decided one.
        """
        mint = self.money.open_intent(
            self.session.session_id, self.session.intent_amount_paise,
            self.evidence(), cycle=cycle)
        self.mints.append(mint)
        if mint.minted:
            self.nonce = mint.nonce
        return mint

    def reconnect(self) -> tuple[Any, MintResult | None]:
        """Network back. PENDING_OFFLINE drains into a real intent."""
        transition = self.session.on_network(True)
        if self.session.state is not State.AWAITING_SETTLEMENT:
            return transition, None
        mint = self.money.open_intent(
            self.session.session_id, self.session.intent_amount_paise,
            self.evidence())
        self.mints.append(mint)
        if mint.minted:
            self.nonce = mint.nonce
        return transition, mint

    def pay(self, link_id: str, **kw) -> Any:
        """The customer pays. The webhook comes back through the sink."""
        return self.rig.sim.pay_link(link_id, **kw)

    def _on_delivery(self, delivery: Delivery) -> None:
        """The gateway's webhook lands. paisa adjudicates; the session obeys."""
        self.deliver(dict(delivery))

    def deliver(self, delivery: Mapping[str, Any]) -> Adjudication:
        """Push one raw delivery through paisa and then the session."""
        before = self.session.state.value
        verdict, payment_id, nonce = self.money.adjudicate(delivery)
        session_verdict = SessionVerdict(
            event_id=verdict.event_id or verdict.body_sha256,
            event=verdict.event or "",
            session_id=verdict.session_id or "",
            amount_paise=verdict.amount_paise,
            green=bool(verdict.green),
            signature_valid=bool(verdict.signature_valid),
            reason=verdict.reason,
        )
        transition = self.session.on_webhook(session_verdict)
        kernel_state = None
        if verdict.green and payment_id and (nonce or self.nonce):
            intent = self.money.confirm(nonce or self.nonce, payment_id)
            kernel_state = intent.state
        adj = Adjudication(
            green=bool(verdict.green), reason=verdict.reason,
            severity=verdict.severity,
            event_id=session_verdict.event_id,
            signature_valid=bool(verdict.signature_valid),
            amount_paise=verdict.amount_paise,
            expected_paise=verdict.expected_paise,
            payment_id=payment_id, session_state_before=before,
            session_state_after=self.session.state.value,
            session_reason=transition.reason, kernel_state=kernel_state,
        )
        self.adjudications.append(adj)
        return adj


# ------------------------------------------------------------------ rig build

class SerialisedLedger(Ledger):
    """A `Ledger` with the mutual exclusion the product's `Ledger` lacks.

    USED BY EXACTLY ONE SCENARIO, AND HERE IS WHY
    ---------------------------------------------
    `Ledger.append` reads `self._head`, hashes it into the new line, writes,
    and only then stores the new head. Two threads inside that window both read
    the same head and both emit a line claiming the same `prev_hash`, so the
    chain forks and `verify()` fails at the second line. Reproduced standalone,
    with nothing but this module involved:

        40 threads -> 40 lines on disk
        verify -> ok=False, "line 2: chain break — prev_hash '000…0'
                             != expected '4e828563…'"

    That is a genuine defect in `gawaah/ledger.py`, which this file does not
    own and must not edit. `scenario_webhook_storm` exists to test the KERNEL's
    exactly-once guarantee under contention; without this wrapper it would
    instead be blocked by an unrelated bug in the audit writer, and would report
    a broken chain that says nothing about double charging.

    So the lock is supplied HERE, in the harness, and only for the storm. Every
    other scenario in this file uses the real, unlocked `Ledger`.
    """

    def __post_init__(self) -> None:
        super().__post_init__()
        self._lock = threading.Lock()

    def append(self, **fields: Any) -> str:
        with self._lock:
            return super().append(**fields)


def build_rig(root: Path | str | None = None, *,
              secret: str = WEBHOOK_SECRET,
              start: str = "2026-08-29T09:00:00.000+00:00",
              step_ms: int = 20,
              seed: int = 7,
              ledger_factory: Callable[[Path], Ledger] = Ledger) -> Rig:
    """Everything a counter needs, on a real filesystem, with a virtual clock."""
    path = Path(root) if root is not None else Path(tempfile.mkdtemp(prefix="gawaah-e2e-"))
    path.mkdir(parents=True, exist_ok=True)
    clock = VirtualClock(start=start, step_ms=step_ms)
    ledger = ledger_factory(path / "counter.jsonl")
    kernel_ledger = ledger_factory(path / "kernel.jsonl")
    kernel = Kernel(str(path / "intents.db"), clock, kernel_ledger)
    sim = RazorpaySim(secret, clock, seed=seed, ledger=ledger)
    session = Session(clock, ledger)
    money = MoneyService(secret=secret, clock=clock, kernel=kernel, sim=sim,
                         ledger=ledger)
    reference, gallery, _ = enrolled()
    return Rig(
        root=path, clock=clock, ledger=ledger, kernel_ledger=kernel_ledger,
        kernel=kernel, sim=sim, session=session, money=money,
        engine=PlaneEngine(), detector=PlacementDetector(reference),
        identifier=Identifier(gallery, lambda v: v), reference=reference,
    )


def _finish(name: str, rig: Rig, counter: Counter, expected_paise: int,
            **notes: Any) -> ScenarioResult:
    """Close the books: snapshot the kernel, verify both chains, report."""
    intents = tuple(
        {"nonce": it.nonce, "state": it.state, "amount_paise": it.amount_paise,
         "payment_id": it.payment_id, "attempts": it.attempts,
         "retrieve_attempts": it.retrieve_attempts,
         "needs_human": it.needs_human, "reason": it.reason,
         "cycle": it.cycle, "session_id": it.session_id}
        for it in rig.kernel.all_intents()
    )
    ok, counter_led, kernel_led = rig.verify_ledgers()
    session = rig.session
    return ScenarioResult(
        name=name,
        final_state=session.state.value,
        total_paise=int(session.total_paise),
        expected_paise=expected_paise,
        money_authorised=session.money_authorised,
        authorised_paise=session.authorised_paise,
        observations=tuple(counter.observations),
        crossings=tuple(counter.crossings),
        mints=tuple(counter.mints),
        adjudications=tuple(counter.adjudications),
        amber_item_ids=tuple(li.item_id for li in session.amber_items),
        committed_item_ids=tuple(li.item_id for li in session.committed_items),
        intents=intents,
        settled_intents=sum(1 for i in intents if i["state"] == SETTLED),
        gateway_links=len(rig.sim.fetch_payment_links()["items"]),
        gateway_payments=len(rig.sim.fetch_payments()["items"]),
        gateway_deliveries=len(rig.sim.deliveries),
        ledger_ok=ok,
        counter_ledger=counter_led,
        kernel_ledger=kernel_led,
        ledger_lines=rig.all_lines(),
        frames_rendered=counter.frames_rendered,
        notes=notes,
    )


def _standard_basket(counter: Counter,
                     labels: Sequence[str] = ("SURF", "MAGGI", "PARLE_G"),
                     ) -> dict[str, str]:
    """Three known goods, one per lane, nearest the exit last."""
    items = {label: (label, LANE_X, ROW_Y[i]) for i, label in enumerate(labels)}
    counter.arrive(items)
    return {label: counter.item_id[label] for label in labels}


def _expected_total(labels: Iterable[str]) -> int:
    return int(sum_paise([CATALOGUE[l].price_paise for l in labels]))


# ==========================================================================
# 1. HAPPY PATH
# ==========================================================================

def scenario_happy_path(root: Path | str | None = None) -> ScenarioResult:
    """Three known goods cross the printed exit line. DONE, mint, pay, PAID.

    The exit is IMAGED: every frame of every carry is rendered, projected,
    ArUco-detected, rectified and segmented, so the crossing is decided by
    contour centroids on the metric plane rather than by numbers this file
    made up. It is the slowest scenario here and the only one that proves the
    image-to-rupee chain in one unbroken line.
    """
    rig = build_rig(root)
    counter = Counter(rig)
    counter.acquire_mat()
    _standard_basket(counter)
    for label in ("PARLE_G", "MAGGI", "SURF"):     # nearest the exit first
        counter.carry_out(label, imaged=True)

    expected = _expected_total(("PARLE_G", "MAGGI", "SURF"))
    _, mint = counter.done()
    paid = None
    if mint is not None and mint.minted:
        paid = counter.pay(mint.link_id)

    # INVARIANT 5, shown rather than claimed: the same door, a doctored count.
    doctored = CrossingEvidence(frames=counter.evidence().frames, claimed_net=4)
    forged = rig.money.open_intent(rig.session.session_id, expected, doctored,
                                   cycle=1)

    result = _finish(
        "happy_path", rig, counter, expected,
        rupees=to_rupees_str(rig.session.total_paise),
        short_url=mint.short_url if mint else None,
        payment_id=paid.payment["id"] if paid else None,
        replay_net=mint.replay_net if mint else None,
        forged_mint_refused=not forged.minted,
        forged_mint_reason=forged.reason,
        imaged_frames=counter.frames_rendered,
    )
    rig.destroy()
    return result


# ==========================================================================
# 2. AMBER — the unknown item is excluded
# ==========================================================================

def scenario_amber_excluded(root: Path | str | None = None) -> ScenarioResult:
    """An un-enrolled good crosses with two known ones. It is not billed.

    The abstention is REAL: LOCAL_SOAP has exactly PARLE_G's footprint, so the
    metric tiebreak cannot save us and the cosine has to do the refusing.
    """
    rig = build_rig(root)
    counter = Counter(rig)
    counter.acquire_mat()
    counter.arrive({
        "SURF": ("SURF", LANE_X, ROW_Y[0]),
        "PARLE_G": ("PARLE_G", LANE_X, ROW_Y[1]),
        "SOAP": ("LOCAL_SOAP", LANE_X, ROW_Y[2]),
    })
    for label in ("SOAP", "PARLE_G", "SURF"):
        counter.carry_out(label)

    expected = _expected_total(("PARLE_G", "SURF"))
    _, mint = counter.done()
    if mint is not None and mint.minted:
        counter.pay(mint.link_id)

    soap = next(o for o in counter.observations if o.label == "SOAP")
    known = [o for o in counter.observations if o.label != "SOAP"]
    parle = next(o for o in known if o.label == "PARLE_G")
    result = _finish(
        "amber_excluded", rig, counter, expected,
        rupees=to_rupees_str(rig.session.total_paise),
        unknown_item_id=counter.item_id["SOAP"],
        unknown_identity_reason=soap.identity_reason,
        unknown_top1=soap.top1,
        unknown_long_edge_mm=soap.long_edge_mm,
        unknown_n_candidates=soap.n_candidates,
        # The footprint tiebreak could NOT have saved us: the un-enrolled good
        # measures the same long edge as a real SKU, so the cosine did the
        # refusing on its own.
        footprint_delta_vs_parle_mm=abs(soap.long_edge_mm - parle.long_edge_mm),
        known_priced=[(o.label, o.sku_id, o.price_paise) for o in known],
        sum_of_known_paise=expected,
        crossed_lines=len(counter.crossings),
    )
    rig.destroy()
    return result


# ==========================================================================
# 3. REVERT
# ==========================================================================

def scenario_revert(root: Path | str | None = None) -> ScenarioResult:
    """An item crosses, the shopkeeper taps it back. The total decrements."""
    rig = build_rig(root)
    counter = Counter(rig)
    counter.acquire_mat()
    ids = _standard_basket(counter)
    for label in ("PARLE_G", "MAGGI", "SURF"):
        counter.carry_out(label)

    before = int(rig.session.total_paise)
    reverted = ids["MAGGI"]
    transition = rig.session.on_revert(reverted)
    after = int(rig.session.total_paise)

    expected = _expected_total(("PARLE_G", "SURF"))
    _, mint = counter.done()
    if mint is not None and mint.minted:
        counter.pay(mint.link_id)

    result = _finish(
        "revert", rig, counter, expected,
        total_before_revert=before,
        total_after_revert=after,
        decrement=before - after,
        reverted_item_id=reverted,
        revert_reason=transition.reason,
        revert_detail=dict(transition.detail),
        rupees=to_rupees_str(rig.session.total_paise),
    )
    rig.destroy()
    return result


# ==========================================================================
# 4. WRONG AMOUNT
# ==========================================================================

def scenario_wrong_amount(root: Path | str | None = None) -> ScenarioResult:
    """The webhook claims one paisa more than the intent. Never PAID.

    Everything else about the delivery is impeccable: valid HMAC, green event,
    matching session id. Only the amount gate can catch this, which is the
    point of having it.
    """
    rig = build_rig(root)
    counter = Counter(rig)
    counter.acquire_mat()
    _standard_basket(counter)
    for label in ("PARLE_G", "MAGGI", "SURF"):
        counter.carry_out(label)

    expected = _expected_total(("PARLE_G", "MAGGI", "SURF"))
    _, mint = counter.done()
    rig.sim.set_mode("wrong_amount", wrong_amount_delta_paise=1)
    paid = counter.pay(mint.link_id)

    adj = counter.adjudications[-1] if counter.adjudications else None
    result = _finish(
        "wrong_amount", rig, counter, expected,
        intent_paise=mint.amount_paise,
        webhook_paise=adj.amount_paise if adj else None,
        delta_paise=(adj.amount_paise - expected) if adj and adj.amount_paise else None,
        paisa_reason=adj.reason if adj else None,
        paisa_severity=adj.severity if adj else None,
        session_reason=adj.session_reason if adj else None,
        gateway_captured_paise=int(paid.payment["amount"]),
    )
    rig.destroy()
    return result


# ==========================================================================
# 5. TAMPERED WEBHOOK
# ==========================================================================

def _flip_amount_byte(raw: bytes, marker: bytes = b'"amount_paid":') -> tuple[bytes, str]:
    """Change one byte of the signed body — the last digit of `amount_paid`.

    That field is not chosen for convenience. `webhook._SETTLED_FIELD` reads
    `payment_link.entity.amount_paid` as THE settled number, so this is the
    single byte an attacker would most want to move, it keeps the body the same
    length and still valid JSON, and if the HMAC were computed after parsing —
    or not at all — the counter would settle against the tampered figure.
    """
    i = raw.find(marker)
    if i < 0:
        raise RuntimeError(f"no {marker!r} field to tamper with")
    j = i + len(marker)
    while j < len(raw) and raw[j:j + 1].isdigit():
        j += 1
    if j == i + len(marker):
        raise RuntimeError(f"{marker!r} is not followed by digits")
    pos = j - 1
    digit = raw[pos:pos + 1].decode()
    new = b"9" if digit != "9" else b"8"
    return raw[:pos] + new + raw[pos + 1:], f"byte {pos}: {digit!r} -> {new.decode()!r}"


def scenario_tampered_webhook(root: Path | str | None = None) -> ScenarioResult:
    """One byte of the signed body is flipped. The HMAC catches it, unparsed."""
    rig = build_rig(root)
    counter = Counter(rig)
    counter.acquire_mat()
    _standard_basket(counter)
    for label in ("PARLE_G", "MAGGI", "SURF"):
        counter.carry_out(label)

    expected = _expected_total(("PARLE_G", "MAGGI", "SURF"))
    _, mint = counter.done()

    # Intercept the delivery instead of letting the sink have it, tamper, resend.
    rig.sim.set_sink(None)
    paid = counter.pay(mint.link_id)
    original = paid.deliveries[0]
    tampered, what = _flip_amount_byte(original.body)
    adj = counter.deliver({"headers": dict(original.headers), "body": tampered})

    # What the tampered body WOULD have settled for, had anyone parsed it.
    # This number is the whole reason gate 1 comes before the JSON decoder.
    try:
        claimed = json.loads(tampered.decode("utf-8"))
        claimed_paise = int(claimed["payload"]["payment_link"]["entity"]["amount_paid"])
        parses = True
    except (ValueError, KeyError):
        claimed_paise, parses = None, False

    result = _finish(
        "tampered_webhook", rig, counter, expected,
        tamper=what,
        same_length=len(tampered) == len(original.body),
        bytes_differ=sum(1 for a, b in zip(tampered, original.body) if a != b),
        still_valid_json=parses,
        paisa_reason=adj.reason,
        paisa_severity=adj.severity,
        signature_valid=adj.signature_valid,
        session_reason=adj.session_reason,
        tampered_claim_paise=claimed_paise,
        honest_claim_paise=expected,
        original_sha256=hashlib.sha256(original.body).hexdigest(),
        tampered_sha256=hashlib.sha256(tampered).hexdigest(),
    )
    rig.destroy()
    return result


# ==========================================================================
# 6. REPLAY
# ==========================================================================

def scenario_replay(root: Path | str | None = None) -> ScenarioResult:
    """The gateway delivers the identical signed body twice. One settlement.

    Three independent defences have to agree here, and the scenario records
    all three: the green predicate's replay store, the session's per-event
    memo, and the kernel's idempotent mark_settled.
    """
    rig = build_rig(root)
    counter = Counter(rig)
    counter.acquire_mat()
    _standard_basket(counter)
    for label in ("PARLE_G", "MAGGI", "SURF"):
        counter.carry_out(label)

    expected = _expected_total(("PARLE_G", "MAGGI", "SURF"))
    _, mint = counter.done()
    rig.sim.set_mode("duplicate_webhook")
    paid = counter.pay(mint.link_id)

    bodies = [d.body for d in paid.deliveries]
    settled_lines = [r for r in rig.kernel_ledger.read()
                     if r.get("event") == "intent.settled"]
    result = _finish(
        "replay", rig, counter, expected,
        deliveries=len(paid.deliveries),
        identical_bytes=len(set(bodies)) == 1,
        identical_event_ids=len({d.event_id for d in paid.deliveries}) == 1,
        verdicts=[(a.green, a.reason) for a in counter.adjudications],
        session_settled_lines=len([
            r for r in rig.ledger.read()
            if r.get("module") == "session" and r.get("reason") == "settled_green"]),
        kernel_settled_lines=len(settled_lines),
        rupees=to_rupees_str(rig.session.total_paise),
    )
    rig.destroy()
    return result


# ==========================================================================
# 7. OFFLINE
# ==========================================================================

def scenario_offline(root: Path | str | None = None) -> ScenarioResult:
    """Network down at DONE. Billing continues, nothing is authorised."""
    rig = build_rig(root)
    counter = Counter(rig)
    counter.acquire_mat()
    counter.arrive({"SURF": ("SURF", LANE_X, ROW_Y[0]),
                    "MAGGI": ("MAGGI", LANE_X, ROW_Y[1])})
    counter.carry_out("MAGGI")
    total_before_outage = int(rig.session.total_paise)

    rig.session.on_network(False)
    state_offline = rig.session.state.value

    # Billing continues while the line is down: another good is measured,
    # identified and committed with no network anywhere in the path.
    counter.arrive({"PARLE_G": ("PARLE_G", LANE_X, ROW_Y[2])})
    counter.carry_out("PARLE_G")
    counter.carry_out("SURF")
    total_while_offline = int(rig.session.total_paise)

    _, mint_offline = counter.done()
    state_after_done = rig.session.state.value
    intents_while_offline = rig.kernel.count()
    authorised_while_offline = rig.session.money_authorised

    transition, mint = counter.reconnect()
    paid = None
    if mint is not None and mint.minted:
        paid = counter.pay(mint.link_id)

    expected = _expected_total(("PARLE_G", "MAGGI", "SURF"))
    result = _finish(
        "offline", rig, counter, expected,
        total_before_outage=total_before_outage,
        total_while_offline=total_while_offline,
        billing_continued=total_while_offline > total_before_outage,
        state_offline=state_offline,
        state_after_done=state_after_done,
        minted_while_offline=mint_offline is not None,
        intents_while_offline=intents_while_offline,
        authorised_while_offline=authorised_while_offline,
        reconnect_reason=transition.reason,
        drained_nonce=mint.nonce if mint else None,
        payment_id=paid.payment["id"] if paid else None,
    )
    rig.destroy()
    return result


# ==========================================================================
# 8. CRASH
# ==========================================================================

def scenario_crash(root: Path | str | None = None, *,
                   webhook_arrives: bool = True) -> ScenarioResult:
    """Kill the money daemon between intent-commit and result. One charge.

    The sequence, exactly:
      create_intent (NEW, committed) -> mark_calling (CALLING, committed)
      -> the gateway IS reached and the customer DOES pay
      -> the webhook is swallowed by the network (`timeout` mode)
      -> the process dies before it records anything
      -> restart on the same sqlite file: recover() converts CALLING to
         INDETERMINATE, which is the honest name for "the money may have moved".

    With `webhook_arrives=True` Razorpay retries the delivery and the counter
    settles from the webhook; a later reconcile then finds the row already
    machine-done and never touches the gateway at all.

    With `webhook_arrives=False` nothing ever arrives and only reconcile()
    resolves it. The kernel settles; the SESSION DOES NOT GO PAID, because
    green has exactly one door and a poll is not it. That divergence is the
    honest outcome, and it is asserted rather than papered over.
    """
    rig = build_rig(root)
    counter = Counter(rig)
    counter.acquire_mat()
    _standard_basket(counter)
    for label in ("PARLE_G", "MAGGI", "SURF"):
        counter.carry_out(label)

    expected = _expected_total(("PARLE_G", "MAGGI", "SURF"))
    _, mint = counter.done()

    # The webhook never makes it back: money moves, we do not hear about it.
    rig.sim.set_sink(None)
    rig.sim.set_mode("timeout")
    paid = counter.pay(mint.link_id)
    delivery = paid.deliveries[0]
    state_at_crash = rig.kernel.get(mint.nonce).state

    # ---------------- the process dies here ----------------
    rig.kernel.close()
    restarted_ledger = Ledger(rig.kernel_ledger.path)
    rig.kernel = Kernel(str(rig.root / "intents.db"), rig.clock, restarted_ledger)
    rig.kernel_ledger = restarted_ledger
    rig.money.kernel = rig.kernel

    recovered = rig.kernel.recover()
    state_after_recover = rig.kernel.get(mint.nonce).state

    rig.sim.set_mode(None)
    settled_by = None
    if webhook_arrives:
        counter.deliver({"headers": dict(delivery.headers), "body": delivery.body})
        settled_by = "webhook"
    reconciled = rig.kernel.reconcile(mint.nonce, rig.money.gateway_lookup)
    if settled_by is None and reconciled.state == SETTLED:
        settled_by = "reconcile"

    # A retry of the whole mint after recovery must not buy a second charge.
    retry = rig.money.open_intent(rig.session.session_id, expected,
                                  counter.evidence())
    counter.mints.append(retry)

    result = _finish(
        "crash" + ("" if webhook_arrives else "_webhook_lost"),
        rig, counter, expected,
        state_at_crash=state_at_crash,
        recovered_rows=len(recovered),
        state_after_recover=state_after_recover,
        state_after_reconcile=reconciled.state,
        settled_by=settled_by,
        payment_id=reconciled.payment_id,
        gateway_payment_id=paid.payment["id"],
        retry_minted=retry.minted,
        retry_reason=retry.reason,
        webhook_arrives=webhook_arrives,
        kernel_settled_audit_lines=len([
            r for r in rig.kernel_ledger.read()
            if r.get("event") == "intent.settled"]),
    )
    rig.destroy()
    return result


# ==========================================================================
# 8b. CRASH BEFORE THE GATEWAY WAS EVER REACHED
# ==========================================================================

class ProcessDied(RuntimeError):
    """What a kill -9 looks like from inside the process that took it."""


def scenario_crash_before_gateway(root: Path | str | None = None) -> ScenarioResult:
    """Kill the money daemon AFTER the write-ahead commit, BEFORE the call.

    `scenario_crash` covers the hard half of the indeterminate window: the
    gateway WAS reached, the customer DID pay, and we simply never heard. This
    covers the other half, and it is a genuinely different branch of
    `Kernel.reconcile` — the one that ends in FAILED rather than SETTLED.

        create_intent   -> NEW, committed
        mark_calling    -> CALLING, committed
        *** kill -9 ***    nothing has gone to the gateway
        restart, recover() -> INDETERMINATE   ("the money MAY have moved")
        reconcile()        -> FAILED           ("gateway_never_saw_nonce")

    The point of the write-ahead ordering is precisely that this is decidable.
    Because the row was committed BEFORE the call, the survivor knows a call
    might have happened and must ask; because the gateway has no record of the
    nonce, the answer is knowable and is "no". Nothing is guessed, and — the
    thing that actually matters — ZERO money moved.

    Then the shopkeeper taps RETRY. The basket has not changed, so the amount
    has not changed, and only the CYCLE moves. That is what makes the retry a
    new idempotency key rather than a second attempt at a decided one, and it
    is why "exactly one charge" survives: one FAILED intent that never reached
    the gateway, one SETTLED intent that did, one link, one payment.
    """
    rig = build_rig(root)
    counter = Counter(rig)
    counter.acquire_mat()
    _standard_basket(counter)
    for label in ("PARLE_G", "MAGGI", "SURF"):
        counter.carry_out(label)

    expected = _expected_total(("PARLE_G", "MAGGI", "SURF"))

    # ---------------- the process dies mid-mint ----------------
    dead_nonce: list[str] = []

    def die(nonce: str) -> None:
        dead_nonce.append(nonce)
        raise ProcessDied("kill -9 between the write-ahead and the gateway")

    died = False
    try:
        counter.done(crash_hook=die)
    except ProcessDied:
        died = True
    nonce = dead_nonce[0]
    state_at_crash = rig.kernel.get(nonce).state
    # The whole claim of this scenario, measured at the moment of death.
    links_at_crash = len(rig.sim.fetch_payment_links()["items"])

    # ---------------- restart on the same sqlite file ----------------
    rig.kernel.close()
    restarted_ledger = Ledger(rig.kernel_ledger.path)
    rig.kernel = Kernel(str(rig.root / "intents.db"), rig.clock, restarted_ledger)
    rig.kernel_ledger = restarted_ledger
    rig.money.kernel = rig.kernel

    recovered = rig.kernel.recover()
    state_after_recover = rig.kernel.get(nonce).state
    reconciled = rig.kernel.reconcile(nonce, rig.money.gateway_lookup)
    links_after_reconcile = len(rig.sim.fetch_payment_links()["items"])

    # A retry on the SAME cycle must not resurrect a decided row.
    same_cycle = rig.money.open_intent(rig.session.session_id, expected,
                                       counter.evidence(), cycle=0)
    counter.mints.append(same_cycle)

    # A retry on a NEW cycle is a legitimate, single charge.
    retry = counter.retry_mint(cycle=1)
    paid = None
    if retry.minted:
        paid = counter.pay(retry.link_id)

    result = _finish(
        "crash_before_gateway", rig, counter, expected,
        died=died,
        dead_nonce=nonce,
        state_at_crash=state_at_crash,
        links_at_crash=links_at_crash,
        recovered_rows=len(recovered),
        state_after_recover=state_after_recover,
        state_after_reconcile=reconciled.state,
        reconcile_reason=reconciled.reason,
        links_after_reconcile=links_after_reconcile,
        same_cycle_retry_minted=same_cycle.minted,
        same_cycle_retry_reason=same_cycle.reason,
        retry_nonce=retry.nonce,
        retry_minted=retry.minted,
        nonces_differ=retry.nonce != nonce,
        payment_id=paid.payment["id"] if paid else None,
        rupees=to_rupees_str(rig.session.total_paise),
    )
    rig.destroy()
    return result


# ==========================================================================
# 9. MAT LOST
# ==========================================================================

def scenario_mat_lost(root: Path | str | None = None) -> ScenarioResult:
    """The mat is occluded mid-basket. The total freezes and stays frozen.

    The loss is DETECTED, not declared: two ArUco markers are covered on the
    rendered sheet and `PlaneEngine.detect` refuses to lock. Everything after
    that is the session refusing to bill.
    """
    rig = build_rig(root)
    counter = Counter(rig)
    counter.acquire_mat()
    counter.arrive({"SURF": ("SURF", LANE_X, ROW_Y[0]),
                    "MAGGI": ("MAGGI", LANE_X, ROW_Y[1]),
                    "PARLE_G": ("PARLE_G", LANE_X, ROW_Y[2])})
    counter.carry_out("PARLE_G")
    counter.carry_out("MAGGI")
    frozen_at = int(rig.session.total_paise)

    lock, transition = counter.lose_mat()

    # Every billing door is tried while the mat is gone.
    attempts = {
        "exit": rig.session.on_exit(counter.item_id["SURF"]),
        "placement": rig.session.on_placement(SessionPlacement(
            item_id="ghost", name="Parle-G 100g",
            price_paise=CATALOGUE["PARLE_G"].price_paise)),
        "revert": rig.session.on_revert(counter.item_id["PARLE_G"]),
        "price": rig.session.on_price(counter.item_id["SURF"], 9999),
        "done": rig.session.on_done(),
    }
    after = int(rig.session.total_paise)

    refusals = {k: v.reason for k, v in attempts.items()}
    expected = _expected_total(("PARLE_G", "MAGGI"))
    result = _finish(
        "mat_lost", rig, counter, expected,
        markers_found=list(lock.ids_found),
        lock_reason=lock.reason,
        transition_reason=transition.reason,
        frozen_total_paise=frozen_at,
        total_after_attempts=after,
        refusals=refusals,
        every_billing_door_refused=all(r.startswith("refused_")
                                       for r in refusals.values()),
        ghost_line_created="ghost" in [li.item_id for li in
                                       rig.session.line_items],
        live_total_paise=int(rig.session.live_total_paise),
        state=rig.session.state.value,
    )
    rig.destroy()
    return result


# ==========================================================================
# 10. CONCURRENCY
# ==========================================================================

def scenario_concurrency(root: Path | str | None = None, *,
                         threads: int = 50) -> ScenarioResult:
    """Fifty threads race DONE. Exactly one intent, exactly one charge.

    Where the guarantee actually lives, stated honestly:

      * `Session` is single-writer UI state and does not claim thread safety,
        so the driver serialises it behind one lock — which is what a phone
        with one UI thread does anyway.
      * `Kernel.create_intent` and `Kernel.mark_calling` are raced COMPLETELY
        UNGUARDED. That is where exactly-once is claimed (a UNIQUE index on
        the idempotency key, and a state machine in which NEW -> CALLING is
        legal exactly once), and that is where it is tested.
      * The gateway call sits behind `mark_calling`, so only the thread that
        won a durable, committed transition can reach it. One link, one charge.
    """
    rig = build_rig(root)
    counter = Counter(rig)
    counter.acquire_mat()
    _standard_basket(counter)
    for label in ("PARLE_G", "MAGGI", "SURF"):
        counter.carry_out(label)

    expected = _expected_total(("PARLE_G", "MAGGI", "SURF"))
    evidence = counter.evidence()
    session_id = rig.session.session_id
    ui_lock = threading.Lock()
    gate = threading.Barrier(threads)
    outcomes: list[tuple[str, str | None]] = []
    outcome_lock = threading.Lock()
    done_applied: list[int] = []

    def racer() -> None:
        gate.wait()
        with ui_lock:
            transition = rig.session.on_done()
            if transition.applied:
                done_applied.append(1)
            amount = rig.session.intent_amount_paise
        mint = rig.money.open_intent(session_id, amount, evidence)
        with outcome_lock:
            outcomes.append((mint.reason, mint.link_id))

    workers = [threading.Thread(target=racer, name=f"done-{i}")
               for i in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    minted = [o for o in outcomes if o[0] == "minted"]
    link_ids = {o[1] for o in outcomes if o[1]}
    paid = None
    if link_ids:
        paid = counter.pay(next(iter(link_ids)))

    intent_requested = [
        r for r in rig.ledger.read()
        if r.get("module") == "session" and r.get("reason") == "intent_requested"]
    created = [r for r in rig.kernel_ledger.read()
               if r.get("event") == "intent.created"]
    calling = [r for r in rig.kernel_ledger.read()
               if r.get("event") == "intent.calling"]

    result = _finish(
        "concurrency", rig, counter, expected,
        threads=threads,
        done_applied=len(done_applied),
        mint_winners=len(minted),
        distinct_link_ids=len(link_ids),
        refusal_reasons=sorted({o[0] for o in outcomes if o[0] != "minted"}),
        intent_requested_lines=len(intent_requested),
        intent_created_lines=len(created),
        intent_calling_lines=len(calling),
        payment_id=paid.payment["id"] if paid else None,
        rupees=to_rupees_str(rig.session.total_paise),
    )
    rig.destroy()
    return result


# ==========================================================================
# 10b. WEBHOOK STORM — the replay defence, under actual contention
# ==========================================================================

def scenario_webhook_storm(root: Path | str | None = None, *,
                           threads: int = 40) -> ScenarioResult:
    """N threads deliver the IDENTICAL signed body at once. One charge.

    `scenario_replay` delivers the same body twice, sequentially, and the green
    predicate's replay store catches the second. This asks the harder question,
    which is the one `paisa` actually faces: FastAPI serves webhook POSTs
    concurrently, so a gateway that retries a delivery while the first is still
    in flight races the predicate rather than following it.

    WHAT IS RACED, AND WHAT IS NOT
    ------------------------------
    The full `adjudicate -> confirm` path — the green predicate AND the kernel
    write — runs COMPLETELY UNGUARDED in every thread. That is paisa's real hot
    path and it is where exactly-once has to hold.

    `Session` is NOT in the race at all, and that is a modelling decision worth
    stating, because an earlier draft got it wrong and flaked about once in a
    hundred runs. paisa is a multi-threaded server; the phone holding the
    session is one device receiving one outcome. Driving the session from
    inside the racing threads modelled neither: a thread whose verdict came
    back `replay` could win the lock ahead of the thread that had actually gone
    green, and since every delivery carries the SAME event id, the session
    memoised the replay and then correctly ignored the settlement behind it.
    The kernel still settled — the money was never at risk — but the session
    sat in AWAITING_SETTLEMENT.

    That was a causality violation invented by the harness (a `replay` verdict
    can only exist BECAUSE a green one already finished, so it can never
    legitimately be observed first), not a defect in `Session`. So the race now
    covers paisa only, and the phone is handed the winning verdict once,
    afterwards, in order — which is what actually happens.

    WHAT THIS MEASURED, STATED PLAINLY
    ----------------------------------
    `green_verdicts` is RECORDED RATHER THAN ASSERTED TO BE 1, because it is
    not 1: `GreenPredicate` gates on `replay_key in self._seen` and only adds to
    that set at the end of a long evaluate(), so under contention every thread
    walks through a window that a sequential replay never opens. Measured: 40
    of 40 threads went green. See `notes["predicate_replay_gate_held"]`.

    `confirm_errors` is recorded for the same reason. `Kernel.mark_settled`
    short-circuits when the row is ALREADY SETTLED with the same payment id, so
    it is idempotent when called in sequence — that is what `scenario_replay`
    exercises. Under contention it is not: the `get()` and the `_transition()`
    are two statements, so the losing threads reach a row that has changed under
    them and raise `IllegalTransition: SETTLED -> SETTLED`. That is LOUD AND
    FAIL-CLOSED rather than dangerous — the loser gets an exception, not a
    second debit, and in `paisa` it becomes a 500 that the gateway retries onto
    the idempotent sequential path — but it is a real rough edge and it is named
    here rather than swallowed.

    None of that is a double charge, and drawing exactly that distinction is
    the job of this scenario. `Kernel._transition` is guarded by a sqlite
    transaction over a row that can leave NEW/CALLING/INDETERMINATE exactly
    once, so N green verdicts still collapse to ONE settled intent, ONE gateway
    payment and ONE `intent.settled` line in the audit chain. The money
    invariant is load-bearing and holds; the two layers above it leak.
    """
    rig = build_rig(root, ledger_factory=SerialisedLedger)
    counter = Counter(rig)
    counter.acquire_mat()
    _standard_basket(counter)
    for label in ("PARLE_G", "MAGGI", "SURF"):
        counter.carry_out(label)

    expected = _expected_total(("PARLE_G", "MAGGI", "SURF"))
    _, mint = counter.done()

    # Take the delivery off the wire so we can hand the same bytes to N threads.
    rig.sim.set_sink(None)
    paid = counter.pay(mint.link_id)
    delivery = paid.deliveries[0]
    payload = {"headers": dict(delivery.headers), "body": delivery.body}

    tally_lock = threading.Lock()
    gate = threading.Barrier(threads)
    verdicts: list[tuple[bool, str]] = []
    winners: list[GreenVerdict] = []
    confirmed: list[str] = []
    confirm_errors: list[str] = []

    def racer() -> None:
        gate.wait()
        verdict, payment_id, nonce = rig.money.adjudicate(payload)   # unguarded
        with tally_lock:
            verdicts.append((bool(verdict.green), verdict.reason))
            if verdict.green:
                winners.append(verdict)
        if verdict.green and payment_id:
            try:
                intent = rig.money.confirm(nonce or mint.nonce, payment_id)
            except IllegalTransition as exc:
                # Fail-closed: the row moved under this thread. An exception is
                # not a debit, and refusing is the correct thing to do here.
                with tally_lock:
                    confirm_errors.append(type(exc).__name__)
            else:
                with tally_lock:
                    confirmed.append(intent.state)

    workers = [threading.Thread(target=racer, name=f"hook-{i}")
               for i in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    green = sum(1 for g, _ in verdicts if g)

    # ---- the storm is over; now the phone is told, once, as a phone is ----
    # At least one thread must have gone green: the first to reach the replay
    # gate finds an empty store. Anything else is a real failure and the
    # scenario should surface it rather than paper over it.
    if not winners:
        raise RuntimeError("webhook storm produced no green verdict at all")
    won = winners[0]
    rig.session.on_webhook(SessionVerdict(
        event_id=won.event_id or won.body_sha256,
        event=won.event or "",
        session_id=won.session_id or "",
        amount_paise=won.amount_paise,
        green=True,
        signature_valid=bool(won.signature_valid),
        reason=won.reason,
    ))
    settled_lines = [r for r in rig.kernel_ledger.read()
                     if r.get("event") == "intent.settled"]
    session_settled = [r for r in rig.ledger.read()
                       if r.get("module") == "session"
                       and r.get("reason") == "settled_green"]

    result = _finish(
        "webhook_storm", rig, counter, expected,
        threads=threads,
        green_verdicts=green,
        refused_verdicts=len(verdicts) - green,
        refusal_reasons=sorted({r for g, r in verdicts if not g}),
        # The honest headline: the predicate's set leaked, the kernel did not.
        predicate_replay_gate_held=(green == 1),
        confirm_calls=len(confirmed),
        confirm_states=sorted(set(confirmed)),
        confirm_errors=len(confirm_errors),
        confirm_error_types=sorted(set(confirm_errors)),
        settled_exactly_once=(len(settled_lines) == 1),
        kernel_settled_lines=len(settled_lines),
        session_settled_lines=len(session_settled),
        payment_id=paid.payment["id"],
        rupees=to_rupees_str(rig.session.total_paise),
    )
    rig.destroy()
    return result


# ------------------------------------------------------------------- registry

SCENARIOS: dict[str, Callable[..., ScenarioResult]] = {
    "happy_path": scenario_happy_path,
    "amber_excluded": scenario_amber_excluded,
    "revert": scenario_revert,
    "wrong_amount": scenario_wrong_amount,
    "tampered_webhook": scenario_tampered_webhook,
    "replay": scenario_replay,
    "offline": scenario_offline,
    "crash": scenario_crash,
    "crash_before_gateway": scenario_crash_before_gateway,
    "mat_lost": scenario_mat_lost,
    "concurrency": scenario_concurrency,
    "webhook_storm": scenario_webhook_storm,
}


def run_all() -> dict[str, ScenarioResult]:
    return {name: fn() for name, fn in SCENARIOS.items()}


def _main() -> int:
    import time
    failures = 0
    for name, fn in SCENARIOS.items():
        t0 = time.perf_counter()
        r = fn()
        dt = time.perf_counter() - t0
        flag = "ok " if r.ledger_ok else "LEDGER-BROKEN"
        print(f"[{flag}] {name:18s} state={r.final_state:20s} "
              f"total={r.total_paise:7d}p expected={r.expected_paise:7d}p "
              f"authorised={str(r.money_authorised):5s} "
              f"chains={r.counter_ledger['lines']}+{r.kernel_ledger['lines']} "
              f"frames={r.frames_rendered:4d} {dt:5.2f}s")
        for key, value in sorted(r.notes.items()):
            print(f"      {key} = {value!r}")
        if not r.ledger_ok:
            failures += 1
    print(f"\n{len(SCENARIOS)} scenarios, {failures} with a broken chain")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
