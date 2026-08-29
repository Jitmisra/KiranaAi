"""S5 acceptance: a whole sale, end to end, with no camera and no network.

The harness renders the printed TAKHTI, composites textured goods onto it in
MILLIMETRES, projects the sheet into a tilted camera frame, and hands those
frames to `Brain.ingest_frame` one at a time. Everything downstream is the real
module: the real ArUco detector, the real homography, the real classical
segmenter, the real tracker, the real line zone, the real identifier, the real
session, the real sqlite kernel, the real Razorpay simulator with real
HMAC-SHA256 signatures, and the real four-part green predicate.

Two things about the harness are deliberate:

  * Goods are TEXTURED, not solid grey. A solid rectangle has no appearance to
    embed — every mean-subtracted descriptor of it is numerical noise — so a
    solid-object harness would "prove" identity works while actually proving
    only that the footprint tiebreak works. The textures are drawn from a
    seeded PRNG, so they are deterministic, and their grey range is chosen so
    that every interior pixel stays well clear of the placement detector's
    50 %-amplitude refit level (measured in
    test_HARNESS_texture_never_fragments_a_placement).

  * The gallery is ENROLLED FROM THE HARNESS, at a different mat position from
    the one the sale uses. Enrolling from the sale frames would make every
    identification a self-match and the phi/theta gates would never be tested.

Every number quoted in a docstring below was printed by running this file.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import cv2
import numpy as np
import pytest

from gawaah import kernel as _kernel
from gawaah.brain import (
    DEFAULT_PORT,
    Brain,
    BrainConfig,
    BrainError,
    LocalSettlement,
    MintResult,
    create_app,
)
from gawaah.clock import VirtualClock
from gawaah.identity import Gallery, Identifier
from gawaah.ledger import Ledger, verify as ledger_verify
from gawaah.money import from_rupees_str, to_rupees_str
from gawaah.placement import PlacementDetector
from gawaah.rzp_sim import RazorpaySim
from gawaah.sellevent import CentroidTracker, LineZone
from gawaah.session import State
from gawaah.takhti import (
    BUF_H,
    BUF_W,
    MAT_H_MM,
    PlaneEngine,
    render_takhti,
)

# --------------------------------------------------------------- harness

PX_PER_MM_MAT = 4.0        # resolution the printed sheet is rendered at
PAPER = 200                # what white A3 reads at under the demo's exposure
CAM_SIZE = (960, 1280)     # a 1280x960 camera frame (w, h) as (W, H)
TILT = (2.0, 1.5)          # degrees; inside the 8 deg mat-lock gate
SECRET = "whsec_gawaah_brain_test"

#: The sell line, inset from the far edge. The PRINTED arrow sits at 18 mm, but
#: an object whose CENTROID is 18 mm from the edge has half its body off the
#: sheet, so the detector correctly refuses it as TOUCHES_BORDER and there is
#: nothing left to count. 80 mm is the nearest the centroid of a real packet can
#: get to the customer while its whole footprint is still on the plane, which is
#: the geometry the measurement depends on. See
#: test_HARNESS_exit_line_inset_is_forced_by_footprint_geometry.
EXIT_INSET_MM = 80.0
EXIT_Y_MM = MAT_H_MM - EXIT_INSET_MM          # 340.0

Y_START = 180.0
Y_END = 352.0
Y_STEP = 12.0
SETTLE_FRAMES = 8
HOLD_FRAMES = 5

Y_ENROL = 150.0
X_ENROL = 148.0


def mat_paper() -> np.ndarray:
    """The printed sheet at a realistic exposure (paper at PAPER, not 255)."""
    sheet = render_takhti(PX_PER_MM_MAT).astype(np.float64)
    return np.clip(np.rint(sheet * (PAPER / 255.0) + 9.0), 0, 255).astype(np.uint8)


def tile_for(seed: int, rows: int = 4, cols: int = 6) -> np.ndarray:
    """A deterministic printed-wrapper texture.

    Two numbers here are load-bearing and both were chosen by measurement.

    RANGE [20, 70]. Dark enough that every interior pixel differs from the
    paper by at least 95 grey levels, which keeps the whole blob above the
    placement detector's per-blob 50 %-amplitude refit level and stops a
    textured item fragmenting into "components" and being refused as a merged
    contour. Asserted in test_HARNESS_texture_never_fragments_a_placement.

    RESOLUTION 4x6, upsampled SMOOTHLY (`paste_tile` uses INTER_LINEAR). The
    descriptor is an 8x8 thumbnail, so a texture whose blocks land near the
    descriptor's Nyquist frequency aliases: which pair of blocks falls into
    which output cell then depends on a sub-pixel crop offset. This is not
    hypothetical. The first version of this harness used a hard-edged 8x16
    tile, and MAGGI_70 — 16 tile columns across a 203 px crop, i.e. exactly two
    columns per descriptor cell — enrolled and then failed to identify itself,
    cosine -0.025, while PARLE and SALT matched at 0.93/0.95. With a 4x6
    smoothly interpolated tile the same measurement is +0.976. A descriptor and
    a texture have to be chosen against each other; that is a fact about
    thumbnails, and pretending otherwise would have shipped a harness that
    "proved" identity worked for two items out of three.
    """
    rng = np.random.default_rng(seed)
    return rng.integers(20, 71, (rows, cols)).astype(np.uint8)


def paste_tile(
    img: np.ndarray,
    cx_mm: float,
    cy_mm: float,
    long_mm: float,
    short_mm: float,
    tile: np.ndarray,
    ss: int = 4,
) -> None:
    """Composite one axis-aligned textured packet, in place, with 4x coverage AA.

    Coverage compositing rather than a hard rasteriser: a pixel is an area
    integral in a real sensor, and a hard fill quantises the truth to the pixel
    grid, putting a ~0.4 mm floor under every error the harness could measure.
    """
    x0 = (cx_mm - long_mm / 2.0) * PX_PER_MM_MAT
    x1 = (cx_mm + long_mm / 2.0) * PX_PER_MM_MAT
    y0 = (cy_mm - short_mm / 2.0) * PX_PER_MM_MAT
    y1 = (cy_mm + short_mm / 2.0) * PX_PER_MM_MAT
    ix0, iy0 = int(math.floor(x0)), int(math.floor(y0))
    ix1, iy1 = int(math.ceil(x1)), int(math.ceil(y1))
    w, h = ix1 - ix0, iy1 - iy0
    big = np.zeros((h * ss, w * ss), np.uint8)
    poly = (np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]) - [ix0, iy0]) * ss
    cv2.fillConvexPoly(big, np.rint(poly).astype(np.int32), 255)
    cov = cv2.resize(big, (w, h), interpolation=cv2.INTER_AREA).astype(np.float64) / 255.0
    # INTER_LINEAR, not INTER_NEAREST: see the resolution note in tile_for.
    patch = cv2.resize(tile, (w, h), interpolation=cv2.INTER_LINEAR).astype(np.float64)
    roi = img[iy0:iy1, ix0:ix1].astype(np.float64)
    img[iy0:iy1, ix0:ix1] = np.clip(
        np.rint(roi * (1.0 - cov) + patch * cov), 0, 255
    ).astype(np.uint8)


def _projection(mat_shape: tuple[int, int]) -> np.ndarray:
    """A fixed, mildly tilted camera pose. Same pinhole model as test_plane."""
    h, w = mat_shape
    W, H = CAM_SIZE[1], CAM_SIZE[0]
    ax, ay = math.radians(TILT[0]), math.radians(TILT[1])
    hw, hh = w / 2.0, h / 2.0
    pts = np.array(
        [[-hw, -hh, 0], [hw, -hh, 0], [hw, hh, 0], [-hw, hh, 0]], np.float64
    )
    Rx = np.array([[1, 0, 0], [0, math.cos(ax), -math.sin(ax)],
                   [0, math.sin(ax), math.cos(ax)]])
    Ry = np.array([[math.cos(ay), 0, math.sin(ay)], [0, 1, 0],
                   [-math.sin(ay), 0, math.cos(ay)]])
    pts = pts @ Rx.T @ Ry.T
    f = max(w, h) * 2.2
    dist = f * max(w / (0.82 * W), h / (0.82 * H))
    dst = np.array(
        [[f * X / (dist + Z) + W / 2.0, f * Y / (dist + Z) + H / 2.0]
         for X, Y, Z in pts], np.float32
    )
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)
    return cv2.getPerspectiveTransform(src, dst)


def to_camera_frame(mat_img: np.ndarray) -> np.ndarray:
    """Project the sheet into a camera frame on a grey benchtop."""
    M = _projection(mat_img.shape[:2])
    W, H = CAM_SIZE[1], CAM_SIZE[0]
    frame = np.full((H, W), 150, np.uint8)
    warped = cv2.warpPerspective(mat_img, M, (W, H), borderValue=150)
    mask = cv2.warpPerspective(np.full_like(mat_img, 255), M, (W, H), borderValue=0)
    frame[mask > 128] = warped[mask > 128]
    return frame


class Item:
    """One physical packet in the synthetic scene."""

    def __init__(self, name: str, long_mm: float, short_mm: float, x_mm: float,
                 seed: int) -> None:
        self.name = name
        self.long_mm = long_mm
        self.short_mm = short_mm
        self.x_mm = x_mm
        self.tile = tile_for(seed)


# The three goods of the happy path, laid out so that no footprint touches a
# printed marker, the scale patch or the exit arrow at ANY point of the run, and
# so that adjacent packets keep a >= 16 mm gap (the morphological CLOSE spans
# ~1.8 mm, so 16 mm is ten times the bridging distance).
PARLE = Item("PARLE_G_250", 44.0, 24.0, 66.0, seed=11)
SALT = Item("TATA_SALT_1K", 58.0, 26.0, 133.0, seed=22)
MAGGI = Item("MAGGI_70", 72.0, 28.0, 214.0, seed=33)
#: enrolled but never placed — its only job is to sit within tau_mm of PARLE's
#: footprint so that PARLE's identification has a real top-2 competitor and the
#: theta margin gate is actually exercised.
GOODDAY = Item("GOOD_DAY_44", 44.0, 22.0, X_ENROL, seed=55)
#: never enrolled: a 30 mm footprint is nowhere near 44/58/72, so identity
#: abstains with no_candidate_in_footprint. This is the AMBER path.
UNKNOWN = Item("UNKNOWN_SACHET", 30.0, 20.0, 200.0, seed=77)

PRICES = {
    "PARLE_G_250": int(from_rupees_str("10.00")),    # 1000
    "TATA_SALT_1K": int(from_rupees_str("28.50")),   # 2850
    "MAGGI_70": int(from_rupees_str("14.75")),       # 1475
    "GOOD_DAY_44": int(from_rupees_str("40.00")),
}
HAPPY_TOTAL_PAISE = 1000 + 2850 + 1475               # 5325 == Rs 53.25
AMBER_TOTAL_PAISE = 1000 + 2850                      # 3850 == Rs 38.50


def embed(crop: np.ndarray) -> np.ndarray:
    """A tiny, honest appearance descriptor: an 8x8 mean-subtracted thumbnail.

    Deliberately NOT a neural embedder. INVARIANT 3 says zero model weights in
    the browser; this module says the embedder is INJECTED, and a 64-float
    thumbnail is enough to show the injection seam works and the thresholds
    bite. A production gallery swaps in whatever it likes behind the same
    `embed_fn(crop) -> vector` contract.
    """
    small = cv2.resize(crop, (8, 8), interpolation=cv2.INTER_AREA).astype(np.float64)
    v = small.ravel() - float(small.mean())
    n = float(np.linalg.norm(v))
    if n == 0.0:
        return np.ones(64, np.float64) / 8.0
    return v / n


# ------------------------------------------------------- scene construction


def scene_frame(items_at: list[tuple[Item, float]]) -> np.ndarray:
    sheet = mat_paper()
    for item, y in items_at:
        paste_tile(sheet, item.x_mm, y, item.long_mm, item.short_mm, item.tile)
    return to_camera_frame(sheet)


def empty_frame() -> np.ndarray:
    return to_camera_frame(mat_paper())


def rectified_empty(plane: PlaneEngine) -> np.ndarray:
    """The reference the placement detector diffs against: a photograph of the
    EMPTY mat, rectified exactly the way every later frame will be."""
    frame = empty_frame()
    lock = plane.detect(frame)
    assert lock.locked, lock.reason
    return plane.rectify(frame, lock.H)


def y_track() -> list[float]:
    """The whole motion schedule: settle, walk to the exit, hold past it."""
    ys = [Y_START] * SETTLE_FRAMES
    y = Y_START
    while y < Y_END:
        y = min(Y_END, y + Y_STEP)
        ys.append(y)
    ys.extend([Y_END] * HOLD_FRAMES)
    return ys


# --------------------------------------------------------------- enrolment


def enrol_gallery(plane: PlaneEngine, reference: np.ndarray,
                  items: list[Item]) -> Gallery:
    """Measure each SKU on the mat and enrol it. This is what enrolment IS.

    Each item is enrolled at (X_ENROL, Y_ENROL), which is NOT where the sale
    puts it, so no later identification is a self-match.
    """
    gallery = Gallery()
    for item in items:
        detector = PlacementDetector(reference, diff_thresh=28)
        placement = None
        rect = None
        for _ in range(SETTLE_FRAMES):
            sheet = mat_paper()
            paste_tile(sheet, X_ENROL, Y_ENROL, item.long_mm, item.short_mm,
                       item.tile)
            frame = to_camera_frame(sheet)
            lock = plane.detect(frame)
            assert lock.locked, f"{item.name}: {lock.reason}"
            rect = plane.rectify(frame, lock.H)
            found = detector.update(rect)
            assert len(found) == 1, (
                f"{item.name}: enrolment scene must hold exactly one blob, "
                f"got {[(p.reason, p.long_edge_mm) for p in found]}"
            )
            placement = found[0]
        assert placement is not None and placement.stable, item.name
        crop = Brain._crop(rect, placement)
        gallery.enroll(item.name, [embed(crop)], float(placement.long_edge_mm))
    return gallery


# ------------------------------------------------------------------ rig


class Rig:
    """One fully wired counter, plus everything needed to drive it."""

    def __init__(self, tmp_path: Path, *, items: list[Item],
                 enrol: list[Item] | None = None, seed: int = 0,
                 clock_start: str = "2026-08-29T09:00:00.000+00:00") -> None:
        self.tmp = Path(tmp_path)
        self.clock = VirtualClock(clock_start, step_ms=100)
        self.ledger_path = self.tmp / "kaala_dabba.jsonl"
        self.ledger = Ledger(self.ledger_path)
        self.plane = PlaneEngine()
        self.reference = rectified_empty(self.plane)
        self.gallery = enrol_gallery(
            self.plane, self.reference, enrol if enrol is not None else items
        )
        self.identifier = Identifier(self.gallery, embed)
        self.kernel = _kernel.Kernel(
            str(self.tmp / "kernel.db"), self.clock, self.ledger
        )
        self.gateway = RazorpaySim(SECRET, self.clock, seed=seed,
                                   ledger=self.ledger)
        self.settlement = LocalSettlement(
            self.kernel, self.gateway, self.clock, self.ledger, SECRET
        )
        self.brain = Brain(
            BrainConfig(
                clock=self.clock,
                ledger=self.ledger,
                settlement=self.settlement,
                plane=self.plane,
                tracker=CentroidTracker(max_dist_mm=25.0, max_missing_frames=3),
                line=LineZone.mat_exit_line(EXIT_INSET_MM, min_crossing_frames=3),
                identifier=self.identifier,
                prices=PRICES,
                detector=PlacementDetector(self.reference, clock=self.clock),
            )
        )
        self.items = items

    def run_sale(self) -> None:
        """Settle, walk the goods over the sell line, hold."""
        for y in y_track():
            self.brain.ingest_frame(scene_frame([(i, y) for i in self.items]))

    def pay(self, *, mode: str | None = None, emit_captured: bool = False):
        """The customer pays the minted link; every webhook is delivered."""
        state = self.brain.state()
        assert state.nonce is not None, "nothing was minted"
        link_id = self.settlement.link_for(state.nonce)["id"]
        if mode is not None:
            self.gateway.set_mode(mode)
        result = self.gateway.pay_link(link_id, emit_captured=emit_captured)
        for delivery in result.deliveries:
            self.brain.on_webhook(
                delivery.body,
                delivery.signature,
                header_event_id=delivery.headers.get("X-Razorpay-Event-Id"),
            )
        return result

    def assert_ledger_verifies(self) -> int:
        ok, n, head, err = ledger_verify(self.ledger_path)
        assert ok, f"ledger broken: {err}"
        assert head == self.brain.state().ledger_head
        assert n > 0
        return n

    def close(self) -> None:
        self.brain.close()


@pytest.fixture(scope="module")
def _plane() -> PlaneEngine:
    return PlaneEngine()


# ============================================================== HARNESS


def test_HARNESS_empty_mat_locks_and_rectifies(_plane):
    lock = _plane.detect(empty_frame())
    assert lock.locked, lock.reason
    assert lock.ids_found == (0, 1, 2, 3)
    assert lock.reproj_rmse_px < 1.0
    rect = _plane.rectify(empty_frame(), lock.H)
    assert rect.shape[:2] == (BUF_H, BUF_W)


def test_HARNESS_texture_never_fragments_a_placement(_plane):
    """A textured packet must segment as ONE blob, not two.

    If the interior texture dipped below the 50 %-amplitude refit level the
    packet would split, the merged-contour gate would fire, and the harness
    would be testing the wrong refusal. Measured over all five scene items:
    every one comes back measurable, with components == 1.
    """
    reference = rectified_empty(_plane)
    for item in (PARLE, SALT, MAGGI, GOODDAY, UNKNOWN):
        detector = PlacementDetector(reference)
        placement = None
        for _ in range(SETTLE_FRAMES):
            sheet = mat_paper()
            paste_tile(sheet, X_ENROL, Y_ENROL, item.long_mm, item.short_mm,
                       item.tile)
            lock = _plane.detect(to_camera_frame(sheet))
            found = detector.update(_plane.rectify(to_camera_frame(sheet), lock.H))
            assert len(found) == 1, f"{item.name}: {found}"
            placement = found[0]
        assert placement.measurable, f"{item.name}: {placement.reason}"
        assert placement.components == 1, (
            f"{item.name} fragmented into {placement.components} components; "
            "the texture is too close to the paper"
        )
        assert placement.stable


def test_HARNESS_measured_millimetres_match_the_pasted_millimetres(_plane):
    """The whole bill rests on this: mm on the plane are real mm."""
    reference = rectified_empty(_plane)
    errors = []
    for item in (PARLE, SALT, MAGGI, UNKNOWN):
        detector = PlacementDetector(reference)
        placement = None
        for _ in range(SETTLE_FRAMES):
            sheet = mat_paper()
            paste_tile(sheet, X_ENROL, Y_ENROL, item.long_mm, item.short_mm,
                       item.tile)
            lock = _plane.detect(to_camera_frame(sheet))
            placement = detector.update(
                _plane.rectify(to_camera_frame(sheet), lock.H)
            )[0]
        errors.append(abs(placement.long_edge_mm - item.long_mm))
    worst = max(errors)
    assert worst < 1.5, f"worst long-edge error {worst:.3f} mm"


def test_HARNESS_exit_line_inset_is_forced_by_printed_furniture():
    """Why the sell line is at 80 mm and not on the printed 18 mm arrow.

    The far strip of the sheet is not blank paper. The two bottom ArUco markers
    span y 378..408 mm and the exit arrow is printed at y = 402 mm. A packet
    resting on top of black ink has almost no contrast against the reference
    EXACTLY where it is being measured, so the honest place to put a counting
    line in a synthetic scene is the nearest one at which the whole footprint
    still sits on blank paper. This documents that arithmetic rather than
    asserting a preference; the real mat's arrow is where a real shopkeeper's
    hand passes, not where a packet comes to rest.
    """
    def overlaps(a0, a1, b0, b1):
        return a0 < b1 and b0 < a1

    furniture_top = MAT_H_MM - 12.0 - 30.0          # MARGIN_MM + MARKER_MM
    furniture_bottom = MAT_H_MM - 12.0
    assert (furniture_top, furniture_bottom) == (378.0, 408.0)
    arrow_y = MAT_H_MM - 18.0
    assert arrow_y == 402.0

    half = max(i.short_mm for i in (PARLE, SALT, MAGGI, UNKNOWN)) / 2.0
    # a centroid resting on the printed arrow lands the packet on the ink
    assert overlaps(arrow_y - half, arrow_y + half, furniture_top, furniture_bottom)
    # the inset actually used does not
    assert not overlaps(Y_END - half, Y_END + half, furniture_top, furniture_bottom)
    assert Y_END + half < MAT_H_MM
    assert Y_END > EXIT_Y_MM + 1.0        # clear of the 1 mm dead band


# ============================================================== HAPPY PATH


def test_ACCEPTANCE_complete_sale_reaches_PAID_with_an_exact_total(tmp_path):
    """The whole product, in one test.

    Render a mat, paste three known goods, move them across the exit line,
    identify them, mint through the kernel and the Razorpay simulator, pay,
    verify the webhook signature, reach PAID, and assert the total is exactly
    right in paise with a ledger that verifies from genesis.
    """
    rig = Rig(tmp_path, items=[PARLE, SALT, MAGGI], enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        rig.run_sale()
        state = rig.brain.state()

        assert state.mat_lock.locked, state.mat_lock.reason
        assert state.session_state == State.BASKET_OPEN.value, state.session_state
        assert len(state.lines) == 3
        assert {li.sku_id for li in state.lines} == {
            "PARLE_G_250", "TATA_SALT_1K", "MAGGI_70"
        }
        assert all(li.committed for li in state.lines)
        assert state.amber_count == 0
        assert state.total_paise == HAPPY_TOTAL_PAISE
        assert to_rupees_str(state.total_paise) == "53.25"
        assert state.net_crossings == 3

        state = rig.brain.done()
        assert state.session_state == State.AWAITING_SETTLEMENT.value
        assert state.intent_amount_paise == HAPPY_TOTAL_PAISE
        assert state.nonce is not None
        assert state.short_url is not None and state.short_url.startswith(
            "https://rzp.io/i/"
        )
        assert not state.money_authorised

        rig.pay()
        state = rig.brain.state()
        assert state.session_state == State.PAID.value, state.last_webhook_reason
        assert state.money_authorised
        assert state.last_webhook_reason == "green"
        assert state.total_paise == HAPPY_TOTAL_PAISE
        assert state.settled_payment_id is not None
        assert state.settled_payment_id.startswith("pay_")

        intent = rig.kernel.get(state.nonce)
        assert intent.state == _kernel.SETTLED
        assert intent.amount_paise == HAPPY_TOTAL_PAISE
        assert intent.payment_id == state.settled_payment_id

        n = rig.assert_ledger_verifies()
        assert n == state.ledger_lines
    finally:
        rig.close()


def test_identity_actually_decided_each_line(tmp_path):
    """The footprint tiebreak shortlists; the embedding decides.

    PARLE (44 mm) has GOOD_DAY_44 (44 mm) inside tau_mm, so its shortlist is
    two and the theta margin gate is live. The other two are singletons — which
    is the metric tiebreak doing exactly its job, not a weakness.
    """
    rig = Rig(tmp_path, items=[PARLE, SALT, MAGGI],
              enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        rig.run_sale()
        seen = {}
        for line in rig.brain.state().lines:
            ident = rig.brain.identification(line.item_id)
            assert ident is not None
            assert ident.is_match, (line.item_id, ident.reason)
            seen[ident.sku_id] = ident
        assert set(seen) == {"PARLE_G_250", "TATA_SALT_1K", "MAGGI_70"}

        parle = seen["PARLE_G_250"]
        assert parle.n_candidates == 2, "GOOD_DAY_44 must be in the shortlist"
        assert parle.top2_sku == "GOOD_DAY_44"
        assert parle.top1 >= rig.identifier.phi
        assert parle.margin >= rig.identifier.theta
        assert parle.top1 > parle.top2
    finally:
        rig.close()


def test_amber_never_reaches_the_total(tmp_path):
    """B4. An unknown SKU crosses the line with two known ones.

    It is committed — goods really did leave the counter — it shows up in
    `amber_items`, and the total is the two known prices to the paisa.
    """
    rig = Rig(tmp_path, items=[PARLE, SALT, UNKNOWN],
              enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        rig.run_sale()
        state = rig.brain.state()

        assert len(state.lines) == 3
        amber = state.amber_items
        assert len(amber) == 1, [li.to_dict() for li in state.lines]
        assert amber[0].price_paise is None
        assert amber[0].sku_id is None
        assert amber[0].committed
        assert amber[0].reason == "no_candidate_in_footprint"

        ident = rig.brain.identification(amber[0].item_id)
        assert ident is not None and ident.sku_id is None
        assert ident.n_candidates == 0
        assert ident.reason == "no_candidate_in_footprint"

        assert state.total_paise == AMBER_TOTAL_PAISE
        assert to_rupees_str(state.total_paise) == "38.50"
        # the amber line is in the basket and NOT in the money
        assert sum(
            li.price_paise for li in state.lines if li.price_paise is not None
        ) == AMBER_TOTAL_PAISE

        state = rig.brain.done()
        assert state.intent_amount_paise == AMBER_TOTAL_PAISE
        rig.pay()
        state = rig.brain.state()
        assert state.session_state == State.PAID.value
        assert state.total_paise == AMBER_TOTAL_PAISE
        assert state.amber_count == 1
        rig.assert_ledger_verifies()
    finally:
        rig.close()


def test_amber_line_can_be_priced_by_a_tap_and_then_it_counts(tmp_path):
    """Warm enroll. The shopkeeper taps a price; the abstention becomes money."""
    rig = Rig(tmp_path, items=[PARLE, SALT, UNKNOWN],
              enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        rig.run_sale()
        amber = rig.brain.state().amber_items[0]
        state = rig.brain.price_tap(amber.item_id, 500)
        assert state.total_paise == AMBER_TOTAL_PAISE + 500
        assert state.amber_count == 0
        rig.assert_ledger_verifies()
    finally:
        rig.close()


# ============================================================== OFFLINE


def test_offline_path_bills_locally_and_authorises_nothing(tmp_path):
    """R6/B5. Offline, DONE closes the basket and mints NOTHING.

    Money is never authorised while the network is down, and when it comes back
    the one pending intent drains, is paid, and only THEN is PAID reachable.
    """
    rig = Rig(tmp_path, items=[PARLE, SALT, MAGGI],
              enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        rig.run_sale()
        rig.brain.set_online(False)
        state = rig.brain.done()

        assert state.session_state == State.PENDING_OFFLINE.value
        assert state.intent_amount_paise == HAPPY_TOTAL_PAISE
        assert state.nonce is None, "nothing may be minted while offline"
        assert state.short_url is None
        assert not state.money_authorised
        assert rig.kernel.count() == 0, "no intent row may exist yet"
        assert rig.gateway.deliveries == ()

        state = rig.brain.set_online(True)
        assert state.session_state == State.AWAITING_SETTLEMENT.value
        assert state.nonce is not None
        assert state.intent_amount_paise == HAPPY_TOTAL_PAISE
        assert not state.money_authorised

        rig.pay()
        state = rig.brain.state()
        assert state.session_state == State.PAID.value
        assert state.money_authorised
        assert state.total_paise == HAPPY_TOTAL_PAISE
        rig.assert_ledger_verifies()
    finally:
        rig.close()


def test_offline_mint_is_not_attempted_twice_when_the_network_flaps(tmp_path):
    rig = Rig(tmp_path, items=[PARLE, SALT, MAGGI],
              enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        rig.run_sale()
        rig.brain.set_online(False)
        rig.brain.done()
        first = rig.brain.set_online(True).nonce
        rig.brain.set_online(False)
        again = rig.brain.set_online(True).nonce
        assert first is not None and again == first
        assert rig.kernel.count() == 1
        assert len(rig.gateway.fetch_payment_links()["items"]) == 1
        rig.assert_ledger_verifies()
    finally:
        rig.close()


# ============================================================== WRONG AMOUNT


def test_wrong_amount_webhook_lands_in_AMOUNT_MISMATCH_and_never_PAID(tmp_path):
    """The RED hold. A signature-valid webhook for the wrong number of paise.

    Everything about the delivery is genuine — the HMAC verifies over the raw
    bytes, the event is in the green set, the session id matches an open intent
    — and it is still refused, because the amount is one paisa out. Money is
    never authorised on an amount we did not ask for, whatever else agrees.
    """
    rig = Rig(tmp_path, items=[PARLE, SALT, MAGGI],
              enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        rig.run_sale()
        rig.brain.done()
        rig.pay(mode="wrong_amount")

        state = rig.brain.state()
        assert state.session_state == State.AMOUNT_MISMATCH.value
        assert not state.money_authorised
        assert state.last_webhook_reason == "amount_mismatch"
        assert state.intent_amount_paise == HAPPY_TOTAL_PAISE
        assert state.settled_payment_id is None

        # the signature really did verify: this is not a forgery being caught
        line = _last_ledger(rig, module="brain", event="webhook")
        assert line["signature_valid"] is True
        assert line["green"] is False
        assert line["expected_paise"] == HAPPY_TOTAL_PAISE
        assert line["amount_paise"] == HAPPY_TOTAL_PAISE + 1

        # the kernel intent is untouched: no debit was ever recorded
        intent = rig.kernel.get(state.nonce)
        assert intent.state == _kernel.CALLING
        assert intent.payment_id is None
        rig.assert_ledger_verifies()
    finally:
        rig.close()


def test_a_forged_signature_is_discarded_and_changes_nothing(tmp_path):
    """B5. The signature is the whole boundary."""
    rig = Rig(tmp_path, items=[PARLE, SALT, MAGGI],
              enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        rig.run_sale()
        rig.brain.done()
        link_id = rig.settlement.link_for(rig.brain.state().nonce)["id"]
        result = rig.gateway.pay_link(link_id)
        delivery = result.deliveries[0]

        before = rig.brain.state().session_state
        state = rig.brain.on_webhook(delivery.body, "0" * 64)
        assert state.session_state == before == State.AWAITING_SETTLEMENT.value
        assert not state.money_authorised
        assert state.last_webhook_reason == "bad_signature"

        # and the genuine one still works afterwards
        state = rig.brain.on_webhook(delivery.body, delivery.signature)
        assert state.session_state == State.PAID.value
        rig.assert_ledger_verifies()
    finally:
        rig.close()


def test_duplicate_webhook_delivery_pays_once(tmp_path):
    rig = Rig(tmp_path, items=[PARLE, SALT, MAGGI],
              enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        rig.run_sale()
        rig.brain.done()
        rig.pay(mode="duplicate_webhook")
        state = rig.brain.state()
        assert state.session_state == State.PAID.value
        assert state.total_paise == HAPPY_TOTAL_PAISE
        assert rig.kernel.get(state.nonce).state == _kernel.SETTLED
        payments = rig.gateway.fetch_payments()["items"]
        assert len(payments) == 1, "one basket, one debit"
        rig.assert_ledger_verifies()
    finally:
        rig.close()


# ============================================================== INVARIANTS


def test_brain_never_holds_the_webhook_secret(tmp_path):
    """INVARIANT 5, checked on the object rather than promised in a comment."""
    rig = Rig(tmp_path, items=[PARLE], enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        blob = json.dumps(
            {k: repr(v) for k, v in vars(rig.brain).items()}, default=repr
        )
        assert SECRET not in blob
        assert not any("secret" in name.lower() for name in vars(rig.brain))
        assert SECRET not in repr(rig.settlement)
        assert SECRET not in repr(rig.gateway)
    finally:
        rig.close()


def test_only_the_rectified_crop_survives_a_frame_grab(tmp_path):
    """INVARIANT 4. Nothing the brain keeps has the camera frame's shape."""
    rig = Rig(tmp_path, items=[PARLE], enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        frame = scene_frame([(PARLE, Y_START)])
        rig.brain.ingest_frame(frame)
        cam_shape = frame.shape[:2]
        assert cam_shape == CAM_SIZE

        held = []

        def walk(obj, depth=0):
            if depth > 3:
                return
            for value in vars(obj).values():
                if isinstance(value, np.ndarray):
                    held.append(value.shape[:2])
                elif hasattr(value, "__dict__"):
                    walk(value, depth + 1)

        walk(rig.brain)
        assert held, "the detector's reference should have been found"
        assert cam_shape not in held, (
            f"a camera-sized buffer survived the grab: {held}"
        )
        assert (BUF_H, BUF_W) in held, "the rectified reference should be held"
        # The only other arrays reachable are the detector's 3x3 and 5x5
        # morphology kernels. Nothing the brain retains is bigger than the
        # rectified buffer, which is a stronger statement than "not the camera
        # shape": a crop of the frame would be caught by it too.
        biggest = max(h * w for h, w in held)
        assert biggest <= BUF_H * BUF_W, held
        assert biggest < cam_shape[0] * cam_shape[1]
    finally:
        rig.close()


def test_state_is_json_and_carries_no_pixels(tmp_path):
    rig = Rig(tmp_path, items=[PARLE, SALT, MAGGI],
              enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        rig.run_sale()
        blob = rig.brain.state().to_json()
        doc = json.loads(blob)
        assert doc["total_paise"] == HAPPY_TOTAL_PAISE
        assert isinstance(doc["total_paise"], int)
        assert doc["ledger_head"] == rig.ledger.head
        assert len(doc["ledger_head"]) == 64
        assert doc["session_state"] == State.BASKET_OPEN.value
        assert len(doc["lines"]) == 3
        assert doc["mat_lock"]["locked"] is True
        for banned in ("secret", "signature", "homography", "image", "pixels"):
            assert banned not in blob.lower()
    finally:
        rig.close()


def test_ledger_verifies_after_every_step_of_a_sale(tmp_path):
    """B3. One chain, checked after each transition, not just at the end."""
    rig = Rig(tmp_path, items=[PARLE, SALT, MAGGI],
              enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        counts = []
        for y in y_track():
            rig.brain.ingest_frame(scene_frame([(i, y) for i in rig.items]))
            counts.append(rig.assert_ledger_verifies())
        rig.brain.done()
        counts.append(rig.assert_ledger_verifies())
        rig.pay()
        counts.append(rig.assert_ledger_verifies())
        assert counts == sorted(counts), "the chain only ever grows"
        assert counts[-1] > counts[0]
    finally:
        rig.close()


# ============================================================== REPRODUCIBILITY


def _ledger_text(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")


#: Fields that move BECAUSE the gateway nonce moves, and for no other reason.
#: `body_sha256` and `event_id` are both digests of the signed webhook body, and
#: that body carries `reference_id = nonce`; the chain hashes then follow from
#: any change at all. Redacting them is redacting the nonce a second and third
#: time, not quietly widening the claim.
_NONCE_DERIVED = ("hash", "prev_hash", "body_sha256", "event_id")


def _redact(text: str, nonce: str) -> str:
    out = []
    for raw in text.splitlines():
        rec = json.loads(raw.replace(nonce, "<NONCE>"))
        for key in _NONCE_DERIVED:
            rec.pop(key, None)
        out.append(json.dumps(rec, sort_keys=True, ensure_ascii=False))
    return "\n".join(out)


def test_virtual_clock_run_is_byte_reproducible(tmp_path):
    """B2, stated exactly.

    PERCEPTION is byte-identical: two runs of the same frames through two fresh
    brains on a VirtualClock write two ledger files that are equal BYTE FOR
    BYTE, hashes and all, up to the moment money is asked for.

    MONEY is identical in every field except one. `kernel.new_nonce()` is 128
    bits from the OS CSPRNG by design — it is the gateway idempotency token, and
    a deterministic one would be a security bug, not a feature. So the second
    half of this test redacts that single token (and the chain hashes that
    necessarily change with it) and asserts everything else — every state, every
    reason code, every paisa, every event id, every timestamp — is identical.
    That is the honest form of the claim.
    """
    def run(root: Path):
        rig = Rig(root, items=[PARLE, SALT, MAGGI],
                  enrol=[PARLE, SALT, MAGGI, GOODDAY])
        rig.run_sale()
        perception = _ledger_text(rig.ledger_path)
        rig.brain.done()
        rig.pay()
        full = _ledger_text(rig.ledger_path)
        nonce = rig.brain.state().nonce
        total = rig.brain.state().total_paise
        rig.close()
        return perception, full, nonce, total

    a = run(tmp_path / "a")
    b = run(tmp_path / "b")

    assert a[0] == b[0], "the perception half is not byte-reproducible"
    assert len(a[0].splitlines()) > 20
    assert a[3] == b[3] == HAPPY_TOTAL_PAISE
    assert a[2] != b[2], "the gateway nonce is supposed to be unpredictable"
    assert _redact(a[1], a[2]) == _redact(b[1], b[2])


# ============================================================== ABSTENTIONS


def test_losing_the_mat_freezes_the_total_and_refuses_to_bill(tmp_path):
    """R5/INVARIANT 7. Perception outage: the total snapshots, nothing is lost."""
    rig = Rig(tmp_path, items=[PARLE, SALT, MAGGI],
              enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        rig.run_sale()
        frozen_at = rig.brain.state().total_paise
        assert frozen_at == HAPPY_TOTAL_PAISE

        blind = np.full((CAM_SIZE[0], CAM_SIZE[1]), 40, np.uint8)
        state = rig.brain.ingest_frame(blind)
        assert not state.mat_lock.locked
        assert state.session_state == State.MAT_LOST.value
        assert state.frozen
        assert state.total_paise == frozen_at
        assert any(e.code == "mat_lost" for e in state.exceptions)

        state = rig.brain.ingest_frame(
            scene_frame([(i, Y_END) for i in rig.items])
        )
        assert state.mat_lock.locked
        assert state.session_state == State.BASKET_OPEN.value
        assert not state.frozen
        assert state.total_paise == frozen_at
        rig.assert_ledger_verifies()
    finally:
        rig.close()


def test_two_packets_laid_touching_are_refused_not_undercharged(tmp_path):
    """INVARIANT 7 in the money direction.

    One contour is one price, so two goods segmented as one blob would be an
    UNDERCHARGE. The detector refuses it, the brain admits it as an AMBER line
    with the refusal on it, and the total does not silently gain one packet's
    worth of the pair.

    HONEST LIMIT, measured in this harness rather than assumed. Two packets laid
    PERFECTLY FLUSH — same y, 0.5 mm gap — are NOT refused here: they fill their
    joint oriented box (fill 0.996) and the 50 %-amplitude refit does not
    re-cut them into two components, so the pair comes back as one measurable
    88.7 mm object. placement.py's `components` signal exists exactly for that
    case and has its own tests with its own contrast assumptions; with this
    harness's smooth low-frequency textures it does not fire. What is asserted
    below is therefore the OFFSET pair, which is the arrangement a shopkeeper
    actually produces and which the fill_ratio signal catches cleanly.
    """
    pair_a = Item("A", 44.0, 24.0, 100.0, seed=11)
    pair_b = Item("B", 44.0, 24.0, 144.5, seed=22)
    rig = Rig(tmp_path, items=[pair_a, pair_b],
              enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        # 0.5 mm apart in x and 14 mm apart in y: touching, at an offset. The
        # union's oriented box is then mostly empty and the fill_ratio signal
        # fires. MEASURED in this harness: fill 0.635, well under the 0.75 gate.
        for _ in range(SETTLE_FRAMES + 2):
            rig.brain.ingest_frame(
                scene_frame([(pair_a, Y_START), (pair_b, Y_START + 14.0)])
            )
        state = rig.brain.state()
        refused = [p for p in state.placements if not p.measurable]
        assert refused, [p.to_dict() for p in state.placements]
        assert refused[0].reason == "MERGED_CONTOUR"
        assert refused[0].long_edge_mm is None

        assert state.total_paise == 0
        assert any(
            e.code == "placement_refused" for e in state.exceptions
        ), [e.to_dict() for e in state.exceptions]
        assert all(li.amber for li in state.lines)
        rig.assert_ledger_verifies()
    finally:
        rig.close()


def test_a_basket_of_only_amber_refuses_to_close(tmp_path):
    """There is nothing to charge for, so DONE refuses rather than minting 0."""
    rig = Rig(tmp_path, items=[UNKNOWN], enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        rig.run_sale()
        state = rig.brain.state()
        assert state.amber_count == 1
        assert state.total_paise == 0

        state = rig.brain.done()
        assert state.session_state == State.BASKET_OPEN.value
        assert state.intent_amount_paise is None
        assert state.nonce is None
        assert rig.kernel.count() == 0
        rig.assert_ledger_verifies()
    finally:
        rig.close()


def test_tap_to_revert_removes_a_line_and_records_the_human(tmp_path):
    rig = Rig(tmp_path, items=[PARLE, SALT, MAGGI],
              enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        rig.run_sale()
        target = next(
            li for li in rig.brain.state().lines if li.sku_id == "TATA_SALT_1K"
        )
        state = rig.brain.revert(target.item_id)
        assert state.total_paise == HAPPY_TOTAL_PAISE - 2850
        line = _last_ledger(rig, module="brain", event="revert")
        assert line["human_override"] is True
        assert line["item_id"] == target.item_id
        rig.assert_ledger_verifies()
    finally:
        rig.close()


# ============================================================== WEBSOCKET


def _ws_rig(tmp_path):
    return Rig(tmp_path, items=[PARLE, SALT, MAGGI],
               enrol=[PARLE, SALT, MAGGI, GOODDAY])


def test_websocket_sends_brain_state_as_json(tmp_path):
    from fastapi.testclient import TestClient

    rig = _ws_rig(tmp_path)
    try:
        client = TestClient(create_app(rig.brain))
        with client.websocket_connect("/ws") as socket:
            first = socket.receive_json()
            assert first["session_id"] == rig.brain.state().session_id
            assert first["total_paise"] == 0
            assert first["mat_lock"]["locked"] is False

            rig.brain.ingest_frame(scene_frame([(PARLE, Y_START)]))
            pushed = socket.receive_json()
            assert pushed["frame_index"] == 0
            assert pushed["mat_lock"]["locked"] is True
            assert pushed["ledger_head"] == rig.ledger.head
    finally:
        rig.close()


def test_websocket_is_served_where_the_pwa_actually_dials(tmp_path):
    """The counter and its screen have to meet at the same URL.

    `web/app.js` declares `WS_URL = 'ws://localhost:8787'` — the ROOT path. The
    server mounted only `/ws`, so every other WebSocket test in this file passed
    while the shipped PWA could not connect at all: a brain that streams
    perfectly to a client nobody ships. That is the islands-never-touch failure
    in miniature, and no test that picks its own path can catch it, so this one
    reads the constant out of app.js and dials exactly that.
    """
    from urllib.parse import urlsplit

    from fastapi.testclient import TestClient

    app_js = Path(__file__).resolve().parent.parent / "web" / "app.js"
    source = app_js.read_text(encoding="utf-8")
    match = re.search(r"WS_URL\s*=\s*['\"]([^'\"]+)['\"]", source)
    assert match, "web/app.js no longer declares WS_URL; this test must follow it"
    url = urlsplit(match.group(1))
    assert url.port == DEFAULT_PORT, (
        f"the PWA dials port {url.port}, the brain serves {DEFAULT_PORT}"
    )
    path = url.path or "/"

    rig = _ws_rig(tmp_path)
    try:
        client = TestClient(create_app(rig.brain))
        for dialled in {path, "/ws"}:
            with client.websocket_connect(dialled) as socket:
                first = socket.receive_json()
                assert first["session_id"] == rig.brain.state().session_id, dialled
    finally:
        rig.close()


def test_websocket_streams_a_whole_sale_and_ends_on_PAID(tmp_path):
    from fastapi.testclient import TestClient

    rig = _ws_rig(tmp_path)
    try:
        client = TestClient(create_app(rig.brain))
        with client.websocket_connect("/ws") as socket:
            socket.receive_json()          # the connect snapshot
            rig.run_sale()
            rig.brain.done()
            rig.pay()

            states = []
            expected = len(y_track()) + 1 + len(rig.gateway.deliveries)
            for _ in range(expected):
                states.append(socket.receive_json())

            assert states[-1]["session_state"] == State.PAID.value
            assert states[-1]["total_paise"] == HAPPY_TOTAL_PAISE
            assert states[-1]["money_authorised"] is True

            # The whole run-length-encoded lifecycle, as a PWA would see it.
            # MEASURING is deliberately absent: `Session.on_placement` emits
            # MEASURING and then the classification within one call, so it is an
            # intra-frame transition that the ledger records and no published
            # frame ever shows. The brain publishes once per frame, not once per
            # session transition.
            distinct = []
            for s in states:
                if not distinct or distinct[-1] != s["session_state"]:
                    distinct.append(s["session_state"])
            assert distinct == [
                State.IDLE.value,
                State.PRICED.value,
                State.BASKET_OPEN.value,
                State.AWAITING_SETTLEMENT.value,
                State.PAID.value,
            ], distinct

            totals = [s["total_paise"] for s in states]
            assert totals == sorted(totals), "the total only ever grows here"
            assert totals[-1] == HAPPY_TOTAL_PAISE
            # The audit head travels with the stream, and it moves only when
            # something was actually written. Most frames of a settled scene
            # append nothing — the brain audits EVENTS, not frames, which is why
            # 28 streamed states carry only 5 distinct heads.
            lines = [s["ledger_lines"] for s in states]
            assert lines == sorted(lines)
            assert lines[-1] > lines[0]
            heads = [s["ledger_head"] for s in states]
            assert len(set(heads)) == len(set(lines))
            assert heads[-1] == rig.ledger.head
    finally:
        rig.close()


def test_http_endpoints_expose_state_and_health(tmp_path):
    from fastapi.testclient import TestClient

    rig = _ws_rig(tmp_path)
    try:
        client = TestClient(create_app(rig.brain))
        rig.run_sale()
        health = client.get("/health").json()
        assert health["ok"] is True
        assert health["session_state"] == State.BASKET_OPEN.value
        state = client.get("/state").json()
        assert state["total_paise"] == HAPPY_TOTAL_PAISE
        assert state["ledger_head"] == rig.ledger.head
    finally:
        rig.close()


def test_serve_refuses_rather_than_pretending_to_speak_websocket(tmp_path):
    """Honest failure: uvicorn cannot do WebSockets without wsproto/websockets."""
    import importlib.util

    have = any(
        importlib.util.find_spec(m) is not None for m in ("websockets", "wsproto")
    )
    if have:                                            # pragma: no cover
        pytest.skip("a websocket implementation is installed; nothing to refuse")
    rig = _ws_rig(tmp_path)
    try:
        from gawaah.brain import serve

        with pytest.raises(BrainError, match="wsproto"):
            serve(rig.brain, port=8787)
    finally:
        rig.close()


# ============================================================== INJECTION


class _RefusingSettlement:
    """A settlement port that never mints. Proves the seam is real."""

    def __init__(self) -> None:
        self.calls = []

    def mint(self, session_id: str, amount_paise: int) -> MintResult:
        self.calls.append((session_id, amount_paise))
        return MintResult(False, "gateway_error", detail="injected")

    def adjudicate(self, raw_body, signature, *, header_event_id=None):
        raise AssertionError("must not be reached")


def test_settlement_port_is_injected(tmp_path):
    rig = Rig(tmp_path, items=[PARLE, SALT, MAGGI],
              enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        rig.run_sale()
        port = _RefusingSettlement()
        rig.brain.settlement = port
        state = rig.brain.done()
        assert port.calls == [(state.session_id, HAPPY_TOTAL_PAISE)]
        assert state.nonce is None
        assert state.short_url is None
        assert state.session_state == State.AWAITING_SETTLEMENT.value
        assert not state.money_authorised
        assert any(e.code == "mint_failed" for e in state.exceptions)
        rig.assert_ledger_verifies()
    finally:
        rig.close()


def test_local_settlement_refuses_an_empty_secret(tmp_path):
    clock = VirtualClock()
    ledger = Ledger(tmp_path / "l.jsonl")
    kernel = _kernel.Kernel(str(tmp_path / "k.db"), clock, ledger)
    try:
        with pytest.raises(BrainError, match="non-empty"):
            LocalSettlement(kernel, None, clock, ledger, "")
    finally:
        kernel.close()


def test_gateway_timeout_parks_the_intent_and_does_not_retry(tmp_path):
    """An indeterminate call is never a failure and never a blind retry."""
    rig = Rig(tmp_path, items=[PARLE, SALT, MAGGI],
              enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        rig.run_sale()
        rig.gateway.set_mode("timeout")
        state = rig.brain.done()

        assert state.nonce is None
        assert state.session_state == State.AWAITING_SETTLEMENT.value
        assert not state.money_authorised
        assert any(e.code == "mint_failed" for e in state.exceptions)
        intents = rig.kernel.all_intents()
        assert len(intents) == 1
        assert intents[0].state == _kernel.INDETERMINATE
        rig.assert_ledger_verifies()
    finally:
        rig.close()


class _AmnesiacTracker:
    """A tracker that loses its nerve about the last object it can see.

    Injected rather than staged in pixels on purpose. Making the REAL
    `CentroidTracker` abstain requires two objects to converge inside its
    ambiguity margin, and whether it abstains correctly there is a test of
    sellevent's association rule — it has one. What the BRAIN owes is a
    guarantee about WIRING: that an anonymous crossing reaches
    `Session.on_exit(None)` and freezes the total instead of quietly
    under-counting. That is what this exercises, and it is only possible to
    exercise because every collaborator is injected (B1).
    """

    def __init__(self, inner: CentroidTracker, from_frame: int) -> None:
        self.inner = inner
        self.from_frame = from_frame
        self.frame = -1

    def update(self, centroids):
        from gawaah.sellevent import (
            REASON_REID_AMBIGUOUS,
            AbstainedCentroid,
            TrackerUpdate,
        )

        self.frame += 1
        upd = self.inner.update(centroids)
        if self.frame < self.from_frame or not upd.tracks:
            return upd
        forgotten = max(upd.tracks)
        point = upd.tracks[forgotten]
        return TrackerUpdate(
            frame_index=upd.frame_index,
            tracks={k: v for k, v in upd.tracks.items() if k != forgotten},
            untracked=tuple(upd.untracked)
            + (
                AbstainedCentroid(
                    point,
                    code=REASON_REID_AMBIGUOUS,
                    detail="injected: two candidates were indistinguishable",
                    candidate_ids=(forgotten,),
                    gap_frames=2,
                ),
            ),
            lost=upd.lost,
            new_ids=upd.new_ids,
        )


def test_a_crossing_with_no_tracker_id_freezes_the_total(tmp_path):
    """Abstention 11. Goods left the counter and we cannot say which.

    The honest answer is not to guess and not to drop it: the total freezes at
    the value it had, the exception is on the ledger with its coordinates, and a
    human has to acknowledge before billing resumes.
    """
    rig = Rig(tmp_path, items=[PARLE, SALT, MAGGI],
              enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        # Let two items commit normally, then blind the tracker to the third
        # just before it reaches the line.
        frames = y_track()
        blind_from = len(frames) - HOLD_FRAMES - 2
        rig.brain.tracker = _AmnesiacTracker(rig.brain.tracker, blind_from)
        for y in frames:
            rig.brain.ingest_frame(scene_frame([(i, y) for i in rig.items]))

        state = rig.brain.state()
        assert state.session_state == State.FROZEN_TOTAL.value
        assert state.frozen
        assert state.total_paise == 1000 + 2850, (
            "the two items that DID commit are still billed; the third is not"
        )
        codes = {e.code for e in state.exceptions}
        assert "reidentification_ambiguous" in codes, codes

        # billing is refused while frozen
        state = rig.brain.done()
        assert state.session_state == State.FROZEN_TOTAL.value
        assert state.nonce is None
        assert rig.kernel.count() == 0

        # a human acknowledges and the counter resumes at the same total
        state = rig.brain.acknowledge()
        assert state.session_state == State.BASKET_OPEN.value
        assert not state.frozen
        assert state.total_paise == 1000 + 2850
        line = _last_ledger(rig, module="brain", event="acknowledge")
        assert line["human_override"] is True
        rig.assert_ledger_verifies()
    finally:
        rig.close()


class _PhantomTracker:
    """A tracker that reports one EXTRA, unnameable centroid past the sell line.

    Nothing is taken away — every real track keeps its id and behaves normally.
    That isolates the question this pair of tests is about: how much evidence
    does an anonymous centroid past the line have to produce before the total
    freezes? Staging it in pixels would answer a different question (whether the
    segmenter can be made to blink), and sellevent already owns that one.
    """

    def __init__(self, inner, frames: range, y_mm: float = 350.0,
                 x_mm: float = 260.0) -> None:
        self.inner = inner
        self.frames = frames
        self.y_mm = y_mm
        self.x_mm = x_mm
        self.frame = -1

    def update(self, centroids):
        from gawaah.sellevent import (
            REASON_NO_TRACKER_ID,
            AbstainedCentroid,
            TrackerUpdate,
        )

        self.frame += 1
        upd = self.inner.update(centroids)
        if self.frame not in self.frames:
            return upd
        return TrackerUpdate(
            frame_index=upd.frame_index,
            tracks=upd.tracks,
            untracked=tuple(upd.untracked) + (
                AbstainedCentroid(
                    (self.x_mm, self.y_mm),
                    code=REASON_NO_TRACKER_ID,
                    detail="injected: a blob past the line with no id",
                    candidate_ids=(),
                    gap_frames=0,
                ),
            ),
            lost=upd.lost,
            new_ids=upd.new_ids,
        )


def test_a_brief_anonymous_blip_is_logged_but_does_not_freeze(tmp_path):
    """The honest limit on abstention 11, stated as a test rather than a hope.

    It takes THREE consecutive frames past the line to COUNT a crossing, so it
    takes three to FREEZE for one. A counter that froze on a single unnamed
    centroid would freeze every time a hand swept over the line, and a till that
    stops on a hand wave is a till that gets switched off — at which point every
    abstention this product exists for stops being recorded at all.

    What is NOT lost: the blip is on the ledger and in `BrainState.exceptions`
    the instant it happens. Only the freeze waits for evidence.
    """
    rig = Rig(tmp_path, items=[PARLE, SALT], enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        hold = rig.brain._anon_hold_frames()
        assert hold == 3, "the line zone needs 3 frames to count; so must we"
        # two frames of phantom: one short of the evidence a count needs
        rig.brain.tracker = _PhantomTracker(rig.brain.tracker, range(2, 4))

        for y in y_track():
            rig.brain.ingest_frame(scene_frame([(i, y) for i in rig.items]))

        state = rig.brain.state()
        assert state.session_state == State.BASKET_OPEN.value
        assert not state.frozen
        assert state.total_paise == AMBER_TOTAL_PAISE
        anon = [e for e in state.exceptions
                if e.code == "crossed_without_tracker_id"]
        assert len(anon) == 2, "both blip frames are on the record"
        assert all(e.item_id is None for e in anon)
        assert not any(
            r.get("module") == "brain" and r.get("event") == "uncounted_crossing"
            for r in rig.ledger.read()
        ), "nothing was frozen, so nothing may claim it was"
        rig.assert_ledger_verifies()
    finally:
        rig.close()


def test_a_sustained_anonymous_crossing_freezes_once_not_every_frame(tmp_path):
    """Three frames freeze it; the fourth must not re-freeze it.

    The second half is the load-bearing half. `Session.on_exit(None)` while
    already FROZEN_TOTAL is refused harmlessly, so a brain that fired every
    frame would still LOOK correct here — until a shopkeeper taps acknowledge
    with the same unnamed blob still lying past the line, and the counter
    freezes again on the very next frame, forever. The freeze arms once per
    streak and re-arms only after a clean frame.
    """
    rig = Rig(tmp_path, items=[PARLE, SALT], enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        frames = y_track()
        phantom = range(2, len(frames))          # never stops
        rig.brain.tracker = _PhantomTracker(rig.brain.tracker, phantom)

        states = []
        for y in frames:
            states.append(
                rig.brain.ingest_frame(scene_frame([(i, y) for i in rig.items]))
            )

        froze_at = next(
            i for i, s in enumerate(states)
            if s.session_state == State.FROZEN_TOTAL.value
        )
        assert froze_at == 4, (
            f"phantom starts at frame 2 and needs 3 frames, so the freeze is at "
            f"frame 4; got {froze_at}"
        )
        assert states[3].session_state != State.FROZEN_TOTAL.value

        minted = [r for r in rig.ledger.read()
                  if r.get("module") == "brain"
                  and r.get("event") == "uncounted_crossing"]
        assert len(minted) == 1, (
            f"the freeze must be written once per streak, not once per frame; "
            f"found {len(minted)} lines"
        )
        assert minted[0]["held_frames"] == 3
        assert minted[0]["required_frames"] == 3
        assert minted[0]["code"] == "crossed_without_tracker_id"

        # the total froze before anything committed, and stayed there
        assert rig.brain.state().total_paise == 0
        assert rig.brain.state().frozen

        # a human acknowledges while the blob is STILL there: the counter must
        # come back and stay back.
        state = rig.brain.acknowledge()
        assert not state.frozen
        for y in [Y_END] * 4:
            state = rig.brain.ingest_frame(scene_frame([(i, y) for i in rig.items]))
        assert not state.frozen, (
            "acknowledge is useless if the same unbroken streak re-freezes"
        )
        rig.assert_ledger_verifies()
    finally:
        rig.close()


def _drive(rig: Rig, schedule: list[tuple[float | None, ...]]) -> None:
    """Run a per-item y schedule. `None` means the item is not in the scene."""
    for row in schedule:
        rig.brain.ingest_frame(
            scene_frame([(it, y) for it, y in zip(rig.items, row) if y is not None])
        )


def test_goods_that_vanish_mid_crossing_uncounted_freeze_the_total(tmp_path):
    """The named twin of abstention 11, and the more certain of the two.

    PARLE walks to 344 mm — four millimetres past the sell line, held for two
    frames, one short of the three a crossing needs — and then leaves the scene.
    `LineZone._retire` calls that what it is: a track last seen on the far side
    of the line, counted as still being on the near side. Goods reached the
    customer and were never billed.

    This used to be a `pass` with a comment saying the item "never crossed the
    sell line", which is the one thing `_retire` guarantees is false: it fires
    ONLY on a side change the debounce never confirmed. An anonymous version of
    this event froze the total while the named version, carrying strictly more
    evidence, did not.
    """
    rig = Rig(tmp_path, items=[PARLE, SALT], enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        sched: list[tuple[float | None, ...]] = [(Y_START, Y_START)] * SETTLE_FRAMES
        y = Y_START
        while y < 344.0:
            y = min(344.0, y + Y_STEP)
            sched.append((y, Y_START))
        sched.append((344.0, Y_START))            # second frame past the line
        sched.extend([(None, Y_START)] * 8)       # PARLE leaves the scene
        _drive(rig, sched)

        state = rig.brain.state()
        assert state.session_state == State.FROZEN_TOTAL.value
        assert state.frozen
        assert state.total_paise == 0, "PARLE was never committed, so it is not money"
        assert any(e.code == "detected_but_never_counted" for e in state.exceptions)

        line = _last_ledger(rig, module="brain", event="uncounted_crossing")
        assert line["code"] == "detected_but_never_counted"
        assert line["was_committed"] is False
        assert line["item_id"] == "t1"

        # acknowledge resumes where the freeze interrupted, which is PRICED:
        # SALT is measured and priced on the mat but nothing has committed, so
        # there is no basket to go back to.
        state = rig.brain.acknowledge()
        assert state.session_state == State.PRICED.value
        assert not state.frozen
        assert state.total_paise == 0
        rig.assert_ledger_verifies()
    finally:
        rig.close()


def test_a_committed_line_is_not_un_billed_when_its_track_vanishes(tmp_path):
    """The other half of the same rule, and the reason it is not symmetric.

    PARLE crosses properly and is billed. It then comes back over the line for
    two frames — one short of a confirmed return — and leaves the scene, so
    `_retire` fires the same `detected_but_never_counted` code as the test
    above. Here it must NOT move the money: an item can leave the frame for a
    dozen innocent reasons, and auto-refunding on an occlusion is a worse bug
    than the one it would fix. The exception is recorded and tap-to-revert is
    the instrument.
    """
    rig = Rig(tmp_path, items=[PARLE], enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        sched: list[tuple[float | None, ...]] = [(Y_START,)] * SETTLE_FRAMES
        y = Y_START
        while y < Y_END:
            y = min(Y_END, y + Y_STEP)
            sched.append((y,))
        sched.extend([(Y_END,)] * 3)              # commits here
        sched.extend([(336.0,), (324.0,)])        # comes back, two frames only
        sched.extend([(None,)] * 8)               # and leaves the scene
        _drive(rig, sched)

        state = rig.brain.state()
        assert any(e.code == "detected_but_never_counted" for e in state.exceptions)
        assert not state.frozen, "a committed line is not evidence of an under-count"
        assert state.session_state == State.BASKET_OPEN.value
        assert state.total_paise == 1000
        assert not any(
            r.get("module") == "brain" and r.get("event") == "uncounted_crossing"
            for r in rig.ledger.read()
        )

        # the shopkeeper's instrument, still available and still auditable
        state = rig.brain.revert("t1")
        assert state.total_paise == 0
        rig.assert_ledger_verifies()
    finally:
        rig.close()


def test_degraded_perf_disables_auto_commit(tmp_path):
    """Abstention 9. Over the p95 budget a crossing needs a human tap."""
    rig = Rig(tmp_path, items=[PARLE], enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        for _ in range(SETTLE_FRAMES):
            rig.brain.ingest_frame(scene_frame([(PARLE, Y_START)]))
        state = rig.brain.set_perf(400)
        assert state.session_state == State.DEGRADED.value

        for y in y_track()[SETTLE_FRAMES:]:
            rig.brain.ingest_frame(scene_frame([(PARLE, y)]))
        state = rig.brain.state()
        assert state.total_paise == 0, "auto-commit must be off"
        assert not any(li.committed for li in state.lines)

        state = rig.brain.set_perf(80)
        assert state.session_state == State.DEGRADED.value or not state.frozen
        rig.assert_ledger_verifies()
    finally:
        rig.close()


def test_reference_is_auto_seeded_and_the_ledger_says_so(tmp_path):
    """No empty-mat reference supplied. The brain seeds one and announces it."""
    clock = VirtualClock()
    ledger = Ledger(tmp_path / "l.jsonl")
    plane = PlaneEngine()
    kernel = _kernel.Kernel(str(tmp_path / "k.db"), clock, ledger)
    gateway = RazorpaySim(SECRET, clock, ledger=ledger)
    brain = Brain(
        BrainConfig(
            clock=clock,
            ledger=ledger,
            settlement=LocalSettlement(kernel, gateway, clock, ledger, SECRET),
            plane=plane,
            tracker=CentroidTracker(),
            line=LineZone.mat_exit_line(EXIT_INSET_MM),
            identifier=Identifier(Gallery(), embed),
            prices=PRICES,
        )
    )
    try:
        assert brain.detector is None
        brain.ingest_frame(empty_frame())
        assert brain.detector is not None
        assert brain.detector.reference.shape[:2] == (BUF_H, BUF_W)
        seeded = [
            r for r in ledger.read()
            if r.get("module") == "brain" and r.get("event") == "reference_seeded"
        ]
        assert len(seeded) == 1
        assert seeded[0]["source"] == "first_locked_frame"
        ok, _, _, err = ledger_verify(tmp_path / "l.jsonl")
        assert ok, err
    finally:
        brain.close()


def test_a_price_book_that_answers_with_a_float_yields_amber_not_money(tmp_path):
    """INVARIANT 1 at the brain's boundary. A float price is refused, and the
    line goes AMBER rather than being billed as some rounded guess."""
    rig = Rig(tmp_path, items=[PARLE], enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        rig.brain.prices = dict(PRICES)
        rig.brain.prices["PARLE_G_250"] = 10.0        # a float, not paise
        rig.run_sale()
        state = rig.brain.state()
        assert state.total_paise == 0
        assert state.amber_count == 1
        assert state.amber_items[0].sku_id == "PARLE_G_250"
        assert state.amber_items[0].price_paise is None
        assert state.amber_items[0].reason == "no_price_for_sku"
        assert any(e.code == "no_price_for_sku" for e in state.exceptions)
        rig.assert_ledger_verifies()
    finally:
        rig.close()


def test_a_tapped_price_that_is_not_paise_is_refused_loudly(tmp_path):
    """INVARIANT 1 on the HUMAN path into money, which is the dangerous one.

    The price-book path is covered next door, but a tap is worse: it writes an
    amount straight onto a line with `human_override=True`, so a float that got
    through here would be money the ledger swears a person authorised. Every
    non-paise form is refused before anything mutates — no line priced, no total
    moved, and not one ledger row, because a refusal that still wrote an audit
    line would be a record of a payment decision that never happened.
    """
    from gawaah.money import MoneyError

    rig = Rig(tmp_path, items=[UNKNOWN], enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        rig.run_sale()
        state = rig.brain.state()
        assert state.amber_count == 1
        item_id = state.amber_items[0].item_id
        head, lines = state.ledger_head, state.ledger_lines

        for bad in (12.5, 10.0, True, "1000", None):
            with pytest.raises(MoneyError):
                rig.brain.price_tap(item_id, bad)

        state = rig.brain.state()
        assert state.total_paise == 0
        assert state.amber_count == 1
        assert state.ledger_head == head, "a refused tap wrote to the ledger"
        assert state.ledger_lines == lines

        # the same tap in real paise does work, so the refusals above are the
        # type check biting and not a broken line.
        state = rig.brain.price_tap(item_id, 1575)
        assert state.total_paise == 1575
        assert state.amber_count == 0
        rig.assert_ledger_verifies()
    finally:
        rig.close()


def test_every_module_is_injected(tmp_path):
    """B1, as a property of the constructor rather than a claim in a docstring.

    Each collaborator on the config is the object the brain actually uses, and
    swapping any one of them swaps the behaviour. There is no hidden default.
    """
    rig = Rig(tmp_path, items=[PARLE], enrol=[PARLE, SALT, MAGGI, GOODDAY])
    try:
        cfg = rig.brain.config
        assert rig.brain.plane is cfg.plane is rig.plane
        assert rig.brain.tracker is cfg.tracker
        assert rig.brain.line is cfg.line
        assert rig.brain.identifier is cfg.identifier is rig.identifier
        assert rig.brain.settlement is cfg.settlement is rig.settlement
        assert rig.brain.ledger is cfg.ledger is rig.ledger
        assert rig.brain.clock is cfg.clock is rig.clock
        assert rig.brain.detector is cfg.detector
        assert rig.brain.session.ledger is rig.ledger
        assert rig.brain.session.clock is rig.clock
    finally:
        rig.close()


# ------------------------------------------------------------------ helpers


def _last_ledger(rig: Rig, *, module: str, event: str) -> dict:
    hit = None
    for rec in rig.ledger.read():
        if rec.get("module") == module and rec.get("event") == event:
            hit = rec
    assert hit is not None, f"no {module}/{event} line in the ledger"
    return hit
