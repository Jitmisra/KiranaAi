"""SIM SOURCE — a whole counter session, with no camera and no printed mat.

WHY THIS FILE EXISTS
--------------------
Every capability panel in GAWAAH was sitting on an honest abstention:

    MUDRA   mudra_no_reference_frame     "no reference frame has been taken"
    PEEL    peel_no_sticker_enrolled     "nothing has been enrolled"
    CHILLA  chilla_no_screen_found       "no screen found"
    SAAF    saaf_no_burst_captured       "no burst captured"
    CORE    no mat lock

Those abstentions are CORRECT and this module does not remove a single one of
them — ``SimSource`` starts on an empty mat and the abstentions are exactly
what a consumer sees until the beat that earns the answer arrives. What was
missing was any path by which a person with no webcam, no A3 printer and no
second phone could ever watch them stop applying. This is that path.

WHAT IT IS NOT
--------------
It is not a fixture library and it does not fake a single verdict. What it
produces is PIXELS: real 840x1188 rectified TAKHTI buffers, rendered from the
same ``takhti.render_takhti`` that generates the print artwork, with objects
composited onto the plane in MILLIMETRES. Every verdict downstream is then
computed by the real module from those real pixels —

    ``PlaneEngine``  really re-detects the four ArUco markers and really locks
    ``PlacementDetector``  really segments and really measures
    ``Identifier``   really embeds and really abstains
    ``OccluderGesture``  really measures solidity, defects and compactness
    ``StickerRegistry``  really runs ECC and really counts ignited pixels
    ``ScreenFinder`` really finds the quad
    ``BurstStacker`` really registers, really rejects and really measures gain

What is simulated is the CAMERA and the SHOPKEEPER'S HANDS, which are the two
things a laptop does not have. Nothing else.

INVARIANT 2 — WHY NO SIMULATED FRAME CAN EVER GO GREEN
------------------------------------------------------
This module has no settlement port, no gateway, no secret, no webhook and no
way to reach one. It cannot mint, it cannot sign and it cannot pay; it emits an
image, a timestamp and a label. GREEN still requires an HMAC over raw bytes
before any JSON parse, an event in the green set, a ``notes.session_id`` that
matches an OPEN intent, and an amount equal to that intent — none of which is
reachable from a picture. ``tests/test_sim_source.py`` asserts the absence of
that surface rather than trusting this paragraph.

INVARIANT 7 — LABELLING
-----------------------
Every frame carries a ``SimNote`` whose ``simulated`` field is ``True`` and is
not settable to anything else, and ``script()`` marks every beat the same way,
so a UI has no excuse for showing a simulated reading unbadged.

DETERMINISM
-----------
``SimSource(seed=k).frame(i)`` is pure: same seed, byte-identical array. All
randomness is drawn from ``numpy.random.default_rng`` seeded per-object from
``seed``, never from a shared stream whose position depends on call order.

COLOUR, AND WHY IT IS GREY
--------------------------
``frames()`` yields a 3-channel BGR array (that is what a camera client sends),
but its three channels are identical. A tint would look nicer and would be a
lie: every module downstream calls ``cvtColor(BGR2GRAY)``, whose weights are
0.114/0.587/0.299, so a coloured wrapper measures a DIFFERENT luminance than
the one composited and the "within 3 mm" claim would be measuring the tint.
The sim does not get to flatter itself. Pass ``colour=False`` for the 2-D
buffer if the consumer wants to skip the replication.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Iterator, Optional

import cv2
import numpy as np

from gawaah.clock import Clock, VirtualClock
from gawaah.takhti import (
    BUF_H,
    BUF_W,
    MARKER_IDS,
    MARKER_MM,
    MAT_H_MM,
    MAT_W_MM,
    PX_PER_MM_X,
    PX_PER_MM_Y,
    marker_centres_mm,
    render_takhti,
)

__all__ = [
    "SimSource",
    "SimBeat",
    "SimNote",
    "BeatCursor",
    "SkuSpec",
    "SimError",
    "PAPER",
    "STICKER_ROI_MM",
    "SELL_LINE_INSET_MM",
    "SELL_LINE_Y_MM",
    "SCREEN_CENTRE_MM",
    "SCREEN_W_MM",
    "SCREEN_H_MM",
    "SKUS",
    "KNOWN_SKUS",
    "UNKNOWN_SKU",
]


class SimError(ValueError):
    """The sim was asked for something outside its script."""


# --------------------------------------------------------------- the plane

#: What white A3 reads under the demo's counter lamp. Not 255: a real sheet
#: under a tube light is not blown out, and a blown-out reference would put
#: SAAF's saturation gate on the wrong side of every frame.
PAPER = 200

#: Ink floor for printed features after the paper scaling, so the printed mat
#: has the same dynamic range a photograph of it would.
INK = 9

#: The PEEL subject: a printed sticker on the mat, x0,y0,w,h in millimetres.
#:
#: Chosen to clear EVERY other printed feature, which the previous sim's ROI
#: did not: at (30, 40, 70, 70) the sticker's top-left corner overlaps marker 0
#: (which occupies 12..42 mm on both axes) by a 12 x 2 mm sliver. That is
#: invisible when the plane is the identity function, and fatal the moment a
#: real ``PlaneEngine`` has to re-detect the markers in the frame — which is
#: exactly what this source is for. 46..106 mm clears the marker by 4 mm, the
#: 20 mm scale patch by 32 mm, and the goods lanes by 59 mm.
STICKER_ROI_MM: tuple[float, float, float, float] = (46.0, 46.0, 60.0, 60.0)

#: Panels that ``web/app.js`` and ``gawaah/brain_server.py`` name IDENTICALLY,
#: and which a ``select_panel`` tap can therefore safely focus.
#:
#: MEASURED DISAGREEMENT, reported rather than papered over:
#:     web/app.js  PANEL_IDS = ('core', 'mudra', 'peel', 'chilla', 'saaf',
#:                              'ledger')
#:     brain_server PANELS   = ('basket', 'mudra', 'peel', 'chilla', 'saaf',
#:                              'ledger')
#: The two sides call the basket panel different things. Sending 'core' gets a
#: logged ``UNKNOWN_PANEL`` refusal from the server and puts "brain refused:
#: UNKNOWN_PANEL" in front of the shopkeeper; sending 'basket' is a name the
#: client's own registry does not know. So the sim sends NO focus tap for that
#: one panel and every beat still names its panel in the beat metadata, which
#: a UI can act on directly. Reconciling the two lists is a change to two files
#: this module does not own.
FOCUSABLE_PANELS: frozenset[str] = frozenset(
    {"mudra", "peel", "chilla", "saaf", "ledger"}
)

#: Distance of the sell line from the far edge, matching the LineZone the brain
#: is wired with (``LineZone.mat_exit_line(80.0)`` -> y = 340 mm).
SELL_LINE_INSET_MM = 80.0
SELL_LINE_Y_MM = MAT_H_MM - SELL_LINE_INSET_MM

#: A MEASURED GEOMETRIC LIMIT, written down rather than tuned around.
#:
#: The four corner markers occupy x 12..42 and x 255..285 mm, and their lower
#: pair occupies y 378..408 mm. An object PARKED past the sell line (y = 340)
#: has its centre below 340, so with a long edge of L its lower end reaches
#: 340 + L/2 — which is inside that marker band for anything over ~76 mm. So
#: every lane has to stay inside x 44..253, and the usable width is 209 mm.
#:
#: Three ordinary packets fit across that with ~20 mm of clear paper between
#: them (>10x the 1.8 mm morphological CLOSE that would otherwise weld two
#: packets into one contour, and one contour is one price). A FOURTH does not.
#: The first draft of this script tried to park four abreast and clipped the
#: bottom-left and bottom-right markers, and the mat then correctly refused to
#: lock with a 13.3 % scale error for the last 71 frames of the session. The
#: script was changed, not the gate: the customer BAGS the three before the
#: unknown item goes across, which is also what actually happens at a counter.
LANE_X_MM: tuple[float, float, float] = (92.0, 148.0, 204.0)
UNKNOWN_X_MM = 148.0

REST_Y_MM = 210.0        # where goods are set down
PARK_Y_MM = 358.0        # where the knowns come to rest past the sell line
UNKNOWN_PARK_Y_MM = 352.0  # the unknown is longer; this keeps it off the
                           # printed exit arrow at y = 402, whose black ink
                           # would punch a hole in its own difference mask
STEP_MM = 12.0           # per-frame travel; << the tracker's 25 mm gate


# ------------------------------------------------------------------- SKUs


@dataclass(frozen=True)
class SkuSpec:
    """One item the sim can put on the mat.

    ``price_paise`` is an int and only ever an int (invariant 1). The three
    KNOWN specs are what a consumer enrols into its gallery; the UNKNOWN one is
    deliberately never enrolled and its ``price_paise`` is None, because an
    unidentified item has no price — not a price of zero.
    """

    sku_id: str
    label: str
    long_mm: float
    short_mm: float
    angle_deg: float
    seed: int
    x_mm: float
    price_paise: Optional[int] = None

    @property
    def known(self) -> bool:
        return self.price_paise is not None


#: Long edges are spaced 12+ mm apart so that ``Identifier.candidates`` — which
#: shortlists on footprint within tau_mm (4 mm by default) — hands ``identify``
#: a shortlist of exactly one for each known SKU, and an EMPTY shortlist for
#: the unknown. The unknown's 90 mm long edge is 14 mm clear of the nearest
#: enrolled footprint, so its abstention is ``no_candidate_in_footprint``: not
#: a weak score, but nothing of that size ever having been enrolled at all.
#:
#: The rotations are small (<= 6 deg) for a geometric reason, not a cosmetic
#: one: a rotated rectangle's x-extent is short*cos + long*sin, so a 20 deg
#: packet is half as wide again as it looks, and three of them no longer fit
#: between the corner markers. See LANE_X_MM.
SKUS: tuple[SkuSpec, ...] = (
    SkuSpec("CHAI-250", "chai patti 250g", 64.0, 34.0, 0.0, 101, LANE_X_MM[0], 4500),
    SkuSpec("SABUN-BAR", "nahaane ka sabun", 50.0, 30.0, 4.0, 202, LANE_X_MM[1], 3200),
    SkuSpec("ATTA-1K", "atta 1 kg", 76.0, 40.0, -4.0, 303, LANE_X_MM[2], 6250),
    SkuSpec("UNKNOWN-ITEM", "koi anjaan cheez", 90.0, 36.0, 6.0, 404,
            UNKNOWN_X_MM, None),
)

KNOWN_SKUS: tuple[SkuSpec, ...] = tuple(s for s in SKUS if s.known)
UNKNOWN_SKU: SkuSpec = next(s for s in SKUS if not s.known)


# ------------------------------------------------------------------ beats


@dataclass(frozen=True)
class SimBeat:
    """One scripted beat, and what a viewer is supposed to be watching.

    ``panel`` is the capability the beat exists to light up; ``expects`` is
    plain-language copy for the UI ticker. ``simulated`` is True and cannot be
    anything else — see ``__post_init__``.
    """

    name: str
    frames: int
    panel: str
    title: str
    expects: str
    start: int = 0
    simulated: bool = True

    def __post_init__(self) -> None:
        if self.frames <= 0:
            raise SimError(f"beat {self.name!r} must have at least one frame")
        if self.simulated is not True:
            raise SimError("a sim beat is simulated; that field is not a dial")

    @property
    def stop(self) -> int:
        """One past the last frame index of this beat."""
        return self.start + self.frames

    def __str__(self) -> str:
        """The beat NAME.

        Not cosmetic. ``brain_server``'s adapter is duck-typed and accepts
        either a mapping or a ``(name, index)`` pair from ``beat_at``; it takes
        ``str(pair[0])`` for the name and looks it up in its label table. A
        default dataclass repr would put ``SimBeat(name='goods', frames=26,
        ...)`` on the shopkeeper's screen. One line here is the whole cost of
        that seam.
        """
        return self.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "frames": self.frames,
            "start": self.start,
            "stop": self.stop,
            "panel": self.panel,
            "title": self.title,
            "expects": self.expects,
            "simulated": True,
        }


class BeatCursor(Mapping):
    """Where in the script one frame sits: a beat plus an offset into it.

    A real ``collections.abc.Mapping`` — ``name``, ``index``, ``of`` and the
    beat's copy fields — so a consumer can read it as data, plus ``.beat`` and
    ``.index`` for callers that want the typed objects. It is a Mapping rather
    than a tuple because the consumer across the module seam reads it both
    ways, and the tuple form made its dry-run printer raise.
    """

    __slots__ = ("beat", "index", "_d")

    def __init__(self, beat: SimBeat, index: int) -> None:
        self.beat = beat
        self.index = int(index)
        # `label`/`detail` are aliases of `title`/`expects`. Both spellings are
        # published because the consumer across the seam reads the first pair
        # and this module's own UI copy reads the second; a KeyError on a demo
        # ticker is a blank screen, and one extra dict entry is cheaper than
        # arguing about whose name is better.
        self._d: dict[str, Any] = {
            "name": beat.name,
            "index": self.index,
            "of": beat.frames,
            "panel": beat.panel,
            "title": beat.title,
            "label": beat.title,
            "expects": beat.expects,
            "detail": beat.expects,
            "start": beat.start,
            "stop": beat.stop,
            "simulated": True,
        }

    def __getitem__(self, key: str) -> Any:
        return self._d[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._d)

    def __len__(self) -> int:
        return len(self._d)

    def __repr__(self) -> str:
        return f"BeatCursor({self.beat.name!r}, {self.index})"


@dataclass(frozen=True)
class SimNote:
    """What accompanies one frame. Mapping-ish on purpose: consumers written
    against a plain dict keep working, and ``to_dict()`` is what goes on the
    wire.

    Anything the brain later derives from a frame carrying this note must carry
    ``simulated: true`` onward. That is invariant 7's labelling half, and it is
    why this is a required field rather than an option.
    """

    frame_index: int
    beat: str
    beat_index: int
    panel: str
    title: str
    expects: str
    label: str
    commands: tuple[Mapping[str, Any], ...] = ()
    burst_member: bool = False
    burst_blurred: bool = False
    simulated: bool = True

    def __post_init__(self) -> None:
        if self.simulated is not True:
            raise SimError("a sim note is simulated; that field is not a dial")

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_index": self.frame_index,
            "beat": self.beat,
            "beat_index": self.beat_index,
            "panel": self.panel,
            "title": self.title,
            "expects": self.expects,
            "label": self.label,
            "commands": [dict(c) for c in self.commands],
            "burst_member": self.burst_member,
            "burst_blurred": self.burst_blurred,
            "simulated": True,
        }

    def __getitem__(self, key: str) -> Any:
        try:
            return self.to_dict()[key]
        except KeyError:
            raise KeyError(key) from None

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)


# ------------------------------------------------------------- primitives


#: Counter-lamp falloff, as a fraction of full brightness at the far corner.
#: Not decoration: CHILLA's reflective-vs-emissive test correlates the patch
#: against the ILLUMINATION GRADIENT underneath it, and on perfectly flat paper
#: there is no gradient to correlate against, so it correctly reports
#: ``coupling_measurable: False`` and can say nothing about whether the bright
#: rectangle is a screen or a piece of foil. A real counter has a lamp. This
#: puts one in, so the test can actually run.
LAMP_FALLOFF = 0.93
LAMP_CENTRE_MM = (105.0, 150.0)


def _lamp_field() -> np.ndarray:
    """A smooth multiplicative illumination field, 1.0 under the lamp.

    Quadratic falloff with distance, normalised so the FARTHEST corner of the
    mat sits at LAMP_FALLOFF. Deterministic and parameter-free: it is a
    function of the buffer geometry, so it does not consume a seed and cannot
    drift between runs.
    """
    ys, xs = np.mgrid[0:BUF_H, 0:BUF_W].astype(np.float64)
    cx = LAMP_CENTRE_MM[0] * PX_PER_MM_X
    cy = LAMP_CENTRE_MM[1] * PX_PER_MM_Y
    d2 = (xs - cx) ** 2 + (ys - cy) ** 2
    far = max(
        (0.0 - cx) ** 2 + (0.0 - cy) ** 2,
        (BUF_W - cx) ** 2 + (0.0 - cy) ** 2,
        (0.0 - cx) ** 2 + (BUF_H - cy) ** 2,
        (BUF_W - cx) ** 2 + (BUF_H - cy) ** 2,
    )
    return 1.0 - (1.0 - LAMP_FALLOFF) * (d2 / far)


def _rect_takhti() -> np.ndarray:
    """The printed mat, rendered and resampled to the rectified buffer.

    Rendered at 4 px/mm and resampled rather than rendered straight at
    2.828 px/mm, because ``render_takhti`` rounds the marker side to whole
    pixels and the rounding error at 2.828 px/mm softens the ArUco cells
    enough to cost the lock on a real detection.
    """
    sheet = render_takhti(4.0)
    rect = cv2.resize(sheet, (BUF_W, BUF_H), interpolation=cv2.INTER_AREA)
    lit = rect.astype(np.float64) * (PAPER / 255.0) * _lamp_field() + INK
    return np.clip(np.rint(lit), 0, 255).astype(np.uint8)


def _mm_box(x_mm: float, y_mm: float, w_mm: float, h_mm: float) -> tuple[int, int, int, int]:
    """Centre + size in mm -> (x0, y0, x1, y1) in buffer pixels, clipped."""
    x0 = int(round((x_mm - w_mm / 2.0) * PX_PER_MM_X))
    y0 = int(round((y_mm - h_mm / 2.0) * PX_PER_MM_Y))
    x1 = int(round((x_mm + w_mm / 2.0) * PX_PER_MM_X))
    y1 = int(round((y_mm + h_mm / 2.0) * PX_PER_MM_Y))
    return (max(0, x0), max(0, y0), min(BUF_W, x1), min(BUF_H, y1))


def _wrapper_texture(seed: int, h: int, w: int) -> np.ndarray:
    """A deterministic printed-wrapper texture, OBJECT-LOCAL.

    Two properties are load-bearing and neither is decoration:

    LOCAL, not buffer-fixed. The texture is generated at the object's own size
    and travels with it. A texture painted in buffer coordinates would show a
    different patch of itself every time the object moved, so the oriented crop
    the identifier embeds would change from frame to frame and a correctly
    enrolled SKU would stop matching itself halfway across the mat. That is a
    sim bug that reads exactly like an identity bug.

    Grey range [20, 70] against PAPER=200. Every interior pixel is therefore
    >=130 levels from the paper, which keeps a textured packet above the
    placement detector's 50 %-amplitude refit level so it segments as ONE blob
    instead of shattering into "components" and being refused as a merged
    contour.
    """
    rng = np.random.default_rng(seed)
    tile = rng.integers(20, 71, size=(5, 4)).astype(np.uint8)
    return cv2.resize(
        tile, (max(1, int(w)), max(1, int(h))), interpolation=cv2.INTER_LINEAR
    )


def _paste_oriented(
    buf: np.ndarray,
    centre_mm: tuple[float, float],
    long_mm: float,
    short_mm: float,
    angle_deg: float,
    patch: np.ndarray,
) -> None:
    """Composite an upright ``patch`` as an ORIENTED rectangle on the plane.

    The patch is pasted into a square scratch canvas, rotated with
    INTER_NEAREST, and stencilled through a mask rotated the same way. Nearest
    neighbour, not linear: a linear rotation feathers the object's edge over
    ~1.5 px, the placement detector's 50 %-amplitude refit then lands half a
    pixel inside that ramp, and the measured long edge shrinks by ~0.4 mm for
    no reason other than the sim's own resampling. The whole claim of this file
    is that the millimetres are real, so the sim must not spend any of the
    budget on itself.

    At ``angle_deg == 0`` the rotation is skipped entirely and the paste is
    exact.
    """
    long_px = int(round(long_mm * PX_PER_MM_Y))
    short_px = int(round(short_mm * PX_PER_MM_X))
    if long_px < 2 or short_px < 2:
        raise SimError(f"object too small to composite: {long_mm} x {short_mm} mm")
    patch = cv2.resize(patch, (short_px, long_px), interpolation=cv2.INTER_NEAREST)

    cx = centre_mm[0] * PX_PER_MM_X
    cy = centre_mm[1] * PX_PER_MM_Y

    if abs(angle_deg) < 1e-9:
        local, lmask = patch, np.full(patch.shape, 255, np.uint8)
    else:
        side = int(math.ceil(math.hypot(long_px, short_px))) + 4
        side += (side + 1) % 2  # odd, so the rotation centre is a pixel centre
        local = np.zeros((side, side), np.uint8)
        lmask = np.zeros((side, side), np.uint8)
        oy = (side - long_px) // 2
        ox = (side - short_px) // 2
        local[oy:oy + long_px, ox:ox + short_px] = patch
        lmask[oy:oy + long_px, ox:ox + short_px] = 255
        m = cv2.getRotationMatrix2D((side / 2.0 - 0.5, side / 2.0 - 0.5),
                                    float(angle_deg), 1.0)
        local = cv2.warpAffine(local, m, (side, side), flags=cv2.INTER_NEAREST)
        lmask = cv2.warpAffine(lmask, m, (side, side), flags=cv2.INTER_NEAREST)

    h, w = local.shape
    x0 = int(round(cx - (w - 1) / 2.0))
    y0 = int(round(cy - (h - 1) / 2.0))
    sx0, sy0 = max(0, -x0), max(0, -y0)
    dx0, dy0 = max(0, x0), max(0, y0)
    dx1, dy1 = min(BUF_W, x0 + w), min(BUF_H, y0 + h)
    if dx1 <= dx0 or dy1 <= dy0:
        raise SimError("object composited entirely off the mat")
    sub = local[sy0:sy0 + (dy1 - dy0), sx0:sx0 + (dx1 - dx0)]
    sm = lmask[sy0:sy0 + (dy1 - dy0), sx0:sx0 + (dx1 - dx0)]
    dst = buf[dy0:dy1, dx0:dx1]
    np.copyto(dst, sub, where=sm.astype(bool))


# ---------------------------------------------------------- hand silhouettes
#
# MUDRA's bands, READ OFF gawaah/mudra.py rather than assumed:
#
#     solidity <  0.80                       -> FIST   (needs <3 deep defects)
#     0.80 <= solidity <= 0.95, >=3 defects  -> OPEN   (needs compactness<=0.75)
#     solidity >  0.95                       -> GOODS
#     any two channels contradicting         -> AMBIGUOUS, with the cause named
#
# and either hand verdict additionally has to be hand-SIZED, 4000..22000 mm2,
# which is a MEASUREMENT the mat makes possible rather than an opinion.
#
# Every number in the three docstrings below is what the engine ACTUALLY
# measured on these exact silhouettes, printed by a run and then written down.
# The first draft guessed and got two of the three backwards — it assumed an
# open palm was the high-solidity shape and a fist the compact one, which is
# the opposite of how MUDRA's axis runs. tests/test_sim_source.py re-measures
# all of it, so a later edit that moves a shape out of its corner fails rather
# than quietly showing the wrong gesture.

MUDRA_CENTRE_MM = (148.0, 232.0)


def _paste_open_palm(buf: np.ndarray, ink: int = 42,
                     spread_deg: float = 22.0) -> None:
    """A five-digit splayed hand. MEASURED: solidity 0.808, 6 deep defects,
    compactness 0.288, area 6083 mm2 -> OPEN, reason ``open_palm``.

    Drawn the way a person would draw a hand: a palm disc plus five finger
    capsules, the thumb shorter than the rest. The finger gaps are what produce
    the deep convexity defects OPEN requires, and they are also what keeps
    compactness (0.288) far under the 0.75 ceiling that separates a hand from a
    near-circular blob.

    OBSERVED, AND LEFT VISIBLE: the same silhouette makes the PLACEMENT
    detector raise ``MERGED_CONTOUR`` — a five-fingered blob fills only ~0.5 of
    its oriented box, which is exactly the signature of two goods touching. It
    refuses to measure it and says so: "measured nothing, billed nothing". Two
    modules looking at one hand and reaching two different correct conclusions
    is what happens when the corroboration surface and the measurement surface
    are the same sheet of paper, and the exception it raises is the honest
    record of that. It is not tuned away here.
    """
    cx_mm, cy_mm = 148.0, 236.0
    cx = int(round(cx_mm * PX_PER_MM_X))
    cy = int(round(cy_mm * PX_PER_MM_Y))
    cv2.circle(buf, (cx, cy), int(round(34.0 * PX_PER_MM_X)), ink, -1)
    for k in range(5):
        ang = math.radians(-90.0 + (k - 2) * spread_deg)
        length = 52.0 if k == 0 else 66.0   # the thumb is shorter
        tx = int(round((cx_mm + length * math.cos(ang)) * PX_PER_MM_X))
        ty = int(round((cy_mm + length * math.sin(ang)) * PX_PER_MM_Y))
        cv2.line(buf, (cx, cy), (tx, ty), ink, int(round(14.0 * PX_PER_MM_X)))
        cv2.circle(buf, (tx, ty), int(round(7.0 * PX_PER_MM_X)), ink, -1)


def _paste_fist(buf: np.ndarray, ink: int = 42, r_mm: float = 58.0,
                bite_frac: float = 0.80, offset_frac: float = 0.78) -> None:
    """A closed hand with the wrist crease. MEASURED: solidity 0.732, 1 deep
    defect, compactness 0.511, area 6585 mm2 -> FIST, reason ``closed_hand``.

    The crescent family — a disc with one big bite out of one side — is the
    same one gawaah's own mudra tests use for a fist, and for the same reason:
    ``offset_frac`` slides solidity continuously from ~0.69 to ~0.81 while the
    deep-defect count stays pinned at 1, so it walks ONE channel across ONE
    threshold and leaves the others alone. 0.78 puts the measurement 0.068
    under the 0.80 ceiling, which is more than twice MUDRA's 0.03 hysteresis,
    so the verdict cannot chatter across the boundary.

    Sweep actually run, r=58 mm: offset 0.70 -> 0.693 FIST; 0.78 -> 0.732 FIST;
    0.85 -> 0.763 FIST; 0.95 -> 0.807 AMBIGUOUS(mid_solidity_too_few_defects).
    """
    cx_mm, cy_mm = MUDRA_CENTRE_MM
    px_iso = (PX_PER_MM_X + PX_PER_MM_Y) / 2.0
    cx, cy = cx_mm * PX_PER_MM_X, cy_mm * PX_PER_MM_Y
    r = r_mm * px_iso
    cv2.circle(buf, (int(cx), int(cy)), int(r), ink, -1)
    a = math.radians(90.0)
    cv2.circle(buf,
               (int(cx + math.cos(a) * offset_frac * r),
                int(cy + math.sin(a) * offset_frac * r)),
               int(bite_frac * r), PAPER, -1)


def _paste_unsure(buf: np.ndarray, ink: int = 42) -> None:
    """A notched disc. MEASURED: solidity 0.860, 1 deep defect, compactness
    0.613, area 5109 mm2 -> AMBIGUOUS, reason ``mid_solidity_too_few_defects``.

    A palm disc with four narrow slits cut in from the rim. Each slit is ~26 mm
    deep, well past MIN_DEFECT_DEPTH_MM, but they sit close together on one arc
    and the engine's morphology plus the convex hull resolve them as a SINGLE
    deep defect. So the shape lands in the open-palm solidity band while
    showing one articulation where three are required.

    This is the corner worth putting on screen. MUDRA is not confused about
    what it sees; it has two channels that disagree and it names which one —
    "mid solidity, too few defects" — instead of picking the likelier gesture.
    A cancel or a reveal fired off a guess here is a wrong cancel or a wrong
    reveal, and abstention is the product.
    """
    cx_mm, cy_mm = MUDRA_CENTRE_MM
    cx, cy = int(round(cx_mm * PX_PER_MM_X)), int(round(cy_mm * PX_PER_MM_Y))
    r = int(round(45.0 * PX_PER_MM_X))
    cv2.circle(buf, (cx, cy), r, ink, -1)
    slit_half = max(1, int(round(3.0 * PX_PER_MM_X)))
    slit_len = int(round(26.0 * PX_PER_MM_X))
    for k in range(4):
        ang = math.radians(-118.0 + k * 22.0)
        tip_x = cx + (r - slit_len) * math.cos(ang)
        tip_y = cy + (r - slit_len) * math.sin(ang)
        rim_x = cx + (r + 4) * math.cos(ang)
        rim_y = cy + (r + 4) * math.sin(ang)
        cv2.line(buf, (int(round(rim_x)), int(round(rim_y))),
                 (int(round(tip_x)), int(round(tip_y))),
                 PAPER, slit_half * 2, lineType=cv2.LINE_8)


#: The phone, in millimetres. Public so a consumer (and a test) can compare
#: what CHILLA measured against what was actually composited.
SCREEN_CENTRE_MM = (148.0, 240.0)
SCREEN_W_MM = 65.0
SCREEN_H_MM = 130.0


def _paste_screen(buf: np.ndarray) -> None:
    """A phone-shaped emissive rectangle laid on the mat, for CHILLA.

    65 x 130 mm, aspect 2.0, centred at (148, 240) mm. Inside chilla's gates by
    construction: 8450 mm2 sits between its MIN_AREA_MM2 (2500) and
    MAX_AREA_MM2 (26000); aspect 2.0 between MIN_ASPECT (1.15) and MAX_ASPECT
    (3.20); and the quad lands inside PLACEMENT_BOX_MM (68.5, 105, 228.5, 315)
    with 10 mm to spare at the bottom — a centre of 250 mm would have put the
    lower edge exactly ON that boundary, which is a coin toss, not a demo.

    BRIGHTER than the paper, deliberately: a screen is emissive and chilla
    requires a POSITIVE brightness delta of at least 18 levels. That is also
    what keeps the dark goods parked further down the mat from ever being
    mistaken for a screen.
    """
    x0, y0, x1, y1 = _mm_box(SCREEN_CENTRE_MM[0], SCREEN_CENTRE_MM[1],
                             SCREEN_W_MM, SCREEN_H_MM)
    cv2.rectangle(buf, (x0, y0), (x1, y1), 252, -1)
    cv2.rectangle(buf, (x0, y0), (x1 - 1, y1 - 1), 214, 2)
    # Panel content: five dimmer bars. Their job is to give the patch some
    # VARIANCE, without which the Pearson correlation against the lamp gradient
    # underneath is undefined and CHILLA reports it as unmeasurable — a uniform
    # rectangle cannot be told from a piece of white card by that test.
    #
    # Abstract bars, deliberately NOT glyphs. chilla.read_reference_string and
    # read_screen_timestamp are DOCUMENTED REFUSALS: at 0.19 mm stroke width on
    # this plane the type is under Nyquist and no amount of stacking recovers
    # it. Painting readable text here would be an invitation to try, and the
    # sim would then be arguing against its own module.
    bar_h = max(2, int(round(3.0 * PX_PER_MM_Y)))
    for k in range(5):
        by = y0 + int(round((22.0 + k * 18.0) * PX_PER_MM_Y))
        bx1 = x1 - int(round((8.0 + 6.0 * (k % 3)) * PX_PER_MM_X))
        cv2.rectangle(buf, (x0 + int(round(8.0 * PX_PER_MM_X)), by),
                      (bx1, by + bar_h), 226, -1)


# ============================================================== the source


class SimSource:
    """A scripted counter session as a stream of real rectified frames.

    ::

        src = SimSource(seed=7)
        for frame_bgr, ts, note in src.frames():
            ...                       # feed the real pipeline

    ``script()`` returns the beat list so a UI can name what is on screen.
    """

    #: (name, frames, panel, title, expects). Frame counts are set by the DWELL
    #: FILTERS DOWNSTREAM, not by taste:
    #:   placement.STABLE_FRAMES = 5  motionless frames before a blob is stable
    #:   brain._register           identifies only a STABLE placement, so an
    #:                             object that walks in from off-mat is never
    #:                             registered and its crossing freezes the
    #:                             total instead of billing it
    #:   sellevent min_crossing_frames = 3
    #:   mudra.DWELL_FRAMES = 4       consecutive frames to commit a gesture
    #:   saaf needs >= 3 usable burst members to say anything about a stack
    BEAT_PLAN: tuple[tuple[str, int, str, str, str], ...] = (
        (
            "settle", 8, "core", "khaali takhti",
            "the four markers are found, the mat LOCKS, CORE goes OK — and "
            "every other panel is still honestly abstaining",
        ),
        (
            "burst", 10, "saaf", "enrolment burst",
            "ten grabs of the sticker with real sub-pixel shake and one "
            "deliberately defocused frame; SAAF stacks, names the rejects and "
            "reports a MEASURED sharpness gain",
        ),
        (
            "enrol", 4, "peel", "sticker enrol",
            "PEEL stops saying 'nothing has been enrolled' and starts saying "
            "GENUINE, against the frame it was enrolled from",
        ),
        (
            "goods", 26, "core", "teen cheezein",
            "three known SKUs are set down, measured in millimetres, "
            "identified, and slid across the exit line; the total climbs",
        ),
        (
            "bag", 6, "core", "grahak saamaan le gaya",
            "the customer bags the three; the mat empties and the basket keeps "
            "every rupee it counted",
        ),
        (
            "unknown", 25, "core", "anjaan cheez",
            "a fourth item nothing in the gallery matches goes across: AMBER, "
            "named, and EXCLUDED from the total — the total does not move",
        ),
        (
            "lift", 5, "core", "anjaan cheez hatai gayi",
            "the unknown is taken off the mat and DONE is tapped. The amber "
            "line stays on the bill: an item you could not name does not stop "
            "having crossed",
        ),
        (
            "ledger", 5, "ledger", "kaala dabba",
            "the hash-chained audit head has moved on every mat lock, every "
            "identification and every crossing; LEDGER stops saying 'no audit "
            "head received' and shows the head and the line count",
        ),
        (
            "screen", 10, "chilla", "phone par parchi",
            "a bright phone-shaped rectangle on the mat; CHILLA finds the quad "
            "and corroborates against the mirror — and is still AMBER, because "
            "corroboration is not settlement",
        ),
        (
            "palm", 7, "mudra", "khuli hatheli",
            "five splayed digits: solidity 0.81 in the open band, six deep "
            "defects, compactness 0.29 — MUDRA commits OPEN",
        ),
        (
            "fist", 7, "mudra", "mutthi",
            "a closed hand with the wrist crease: solidity 0.73, one defect — "
            "MUDRA commits FIST",
        ),
        (
            "unsure", 7, "mudra", "pata nahi",
            "a notched disc that reads open-palm solidity with only one "
            "articulation: the channels disagree, so MUDRA names the "
            "contradiction and abstains — AMBIGUOUS",
        ),
        (
            "tamper", 8, "peel", "sticker badla gaya",
            "one sixteenth of the sticker replaced with different modules; "
            "PEEL's ignited fraction crosses the 3 % gate and the verdict "
            "turns TAMPERED",
        ),
    )

    #: Which burst frame is the ruined one. Index within the ``burst`` beat.
    BLURRED_BURST_INDEX = 6
    BURST_BLUR_SIGMA = 2.6

    #: Sub-pixel camera shake, applied to the STICKER ROI only.
    #:
    #: Without it every burst frame samples the identical sub-pixel phase and
    #: SAAF correctly reports NO_SUBPIXEL_DIVERSITY — "this result is DENOISING
    #: ONLY, not super-resolution". That warning is TRUE of a rigidly clamped
    #: camera over a static sticker and it is worth being able to see, but a
    #: sim that can only ever produce it never exercises the path SAAF exists
    #: for. 0.45 px is ordinary counter shake and clears SAAF's 0.15 px floor.
    #:
    #: ROI ONLY, deliberately: jittering the whole buffer would move the ArUco
    #: corners (and with them the mat lock this file has to prove) and would
    #: light up every printed edge on the mat as a spurious placement.
    JITTER_PX = 0.45

    GOODS_DWELL = 9         # motionless frames before the walk, >= STABLE_FRAMES
    GOODS_HOLD = 4           # frames parked past the line before the beat ends

    def __init__(
        self,
        seed: int = 20260829,
        clock: Optional[Clock] = None,
        *,
        period_s: float = 0.1,
        colour: bool = True,
        sticker_roi_mm: tuple[float, float, float, float] = STICKER_ROI_MM,
    ) -> None:
        self.seed = int(seed)
        self.clock: Clock = clock if clock is not None else VirtualClock(
            "2026-08-29T09:00:00.000+00:00", step_ms=int(round(period_s * 1000))
        )
        self.period_s = float(period_s)
        self.colour = bool(colour)
        self.roi_mm = tuple(float(v) for v in sticker_roi_mm)
        if len(self.roi_mm) != 4:
            raise SimError("sticker_roi_mm must be (x0, y0, w, h) in millimetres")
        self._check_roi_clears_the_markers()

        self._beats: tuple[SimBeat, ...] = self._lay_out()
        self._index_by_name = {b.name: b for b in self._beats}

        self._mat = _rect_takhti()
        self._base = self._mat.copy()
        self._paint_sticker(self._base, tampered=False)
        self._base.flags.writeable = False

        self._tampered = self._mat.copy()
        self._paint_sticker(self._tampered, tampered=True)
        self._tampered.flags.writeable = False

        self._i = 0

    # -------------------------------------------------------------- script

    #: The panel ids web/app.js will accept in a ``select_panel`` message.
    #: Checked at construction so a typo is a loud failure here rather than a
    #: silently ignored message and a panel that never lights up.
    PANEL_IDS: frozenset[str] = frozenset(
        {"core", "mudra", "peel", "chilla", "saaf", "ledger"}
    )

    def _check_roi_clears_the_markers(self) -> None:
        """Refuse a sticker ROI that would paint over an ArUco marker.

        This is a LOUD failure on purpose, because the quiet version is very
        expensive: painting a 12 x 2 mm sliver of marker 0 costs the mat lock,
        the mat lock costs every placement, and the whole session then abstains
        for a reason that has nothing to do with anything a viewer is looking
        at. A consumer that passes its own ROI — brain_server tries its
        DEFAULT_STICKER_ROI_MM first — gets told exactly which marker it hit
        and falls back, instead of shipping a sim whose CORE panel never lights.
        """
        x0, y0, w, h = self.roi_mm
        if w <= 0.0 or h <= 0.0:
            raise SimError(f"sticker ROI must have positive size, got {w}x{h} mm")
        x1, y1 = x0 + w, y0 + h
        if x0 < 0.0 or y0 < 0.0 or x1 > MAT_W_MM or y1 > MAT_H_MM:
            raise SimError(
                f"sticker ROI {self.roi_mm} runs off the {MAT_W_MM:.0f}x"
                f"{MAT_H_MM:.0f} mm mat"
            )
        for idx, (cx, cy) in zip(MARKER_IDS, marker_centres_mm()):
            mx0, my0 = cx - MARKER_MM / 2.0, cy - MARKER_MM / 2.0
            mx1, my1 = cx + MARKER_MM / 2.0, cy + MARKER_MM / 2.0
            if x0 < mx1 and mx0 < x1 and y0 < my1 and my0 < y1:
                raise SimError(
                    f"sticker ROI {self.roi_mm} overlaps ArUco marker {idx} at "
                    f"({mx0:.0f}..{mx1:.0f}, {my0:.0f}..{my1:.0f}) mm; the mat "
                    f"would stop locking and every panel would abstain for the "
                    f"wrong reason"
                )

    def _lay_out(self) -> tuple[SimBeat, ...]:
        out: list[SimBeat] = []
        n = 0
        seen: set[str] = set()
        for name, frames, panel, title, expects in self.BEAT_PLAN:
            if panel not in self.PANEL_IDS:
                raise SimError(
                    f"beat {name!r} targets panel {panel!r}, which is not one "
                    f"of {sorted(self.PANEL_IDS)}"
                )
            if name in seen:
                raise SimError(f"duplicate beat name {name!r}")
            seen.add(name)
            out.append(SimBeat(name, frames, panel, title, expects, start=n))
            n += frames
        missing = self.PANEL_IDS - {b.panel for b in out}
        if missing:
            raise SimError(
                f"no beat drives {sorted(missing)}; every panel's abstention "
                f"must have a scripted way out or this source has not done its "
                f"job"
            )
        return tuple(out)

    def script(self) -> tuple[SimBeat, ...]:
        """The beat list, in order, with absolute start/stop frame indices."""
        return self._beats

    def script_dicts(self) -> list[dict[str, Any]]:
        """``script()`` as JSON-ready dicts, for a UI ticker."""
        return [b.to_dict() for b in self._beats]

    @property
    def total_frames(self) -> int:
        return self._beats[-1].stop

    def beat_at(self, i: int) -> "BeatCursor":
        """Which beat frame ``i`` is in, and how far into it.

        Returns a ``BeatCursor``: a Mapping of ``name``/``index``/``of`` plus
        the ``SimBeat`` itself on ``.beat``. A Mapping rather than a plain
        ``(beat, k)`` tuple because that is what the consumer on the other side
        of the seam reads — ``brain_server``'s driver does
        ``isinstance(got, Mapping)`` and its dry-run printer does
        ``beat["name"]`` — and a tuple made the second one raise
        ``TypeError: tuple indices must be integers``. Use ``.beat`` and
        ``.index`` when you want the typed objects.

        Past the end of the script this holds on the final beat rather than
        looping: a loop would replay a settled sale with no reseed, and a
        replayed sale is the one thing a counter must never show.
        """
        if i < 0:
            raise SimError(f"frame index must be >= 0, got {i}")
        for b in self._beats:
            if i < b.stop:
                return BeatCursor(b, i - b.start)
        last = self._beats[-1]
        return BeatCursor(last, last.frames - 1)

    def beat(self, name: str) -> SimBeat:
        try:
            return self._index_by_name[name]
        except KeyError:
            raise SimError(f"no beat named {name!r}") from None

    # ---------------------------------------------------------- the ledger
    #                                                            of what is on
    #                                                            the mat when

    def _goods_y_mm(self, k: int, park_y_mm: float) -> float:
        """Lane y for index ``k`` within a place-settle-slide-park beat.

        The SETTLE half is not padding. ``placement.py`` needs STABLE_FRAMES of
        a motionless blob before it will call one stable, and ``Brain._register``
        only identifies a STABLE placement — so a packet that walks in from off
        the mat is never registered at all, and its crossing then FREEZES the
        total instead of billing it. That is the correct behaviour and it is
        what the first run of this script demonstrated; the schedule was fixed,
        not the gate.
        """
        if k < self.GOODS_DWELL:
            return REST_Y_MM
        y = REST_Y_MM + (k - self.GOODS_DWELL + 1) * STEP_MM
        return min(park_y_mm, y)

    def _skus_on_mat(self, name: str, k: int) -> list[tuple[SkuSpec, float]]:
        """Which SKUs are on the plane in this beat, and at what y (mm).

        A deliberate collision that is NOT tuned away: the phone in the
        ``screen`` beat and the hand in the MUDRA beats are also objects on the
        billing plane. The placement detector sees them, cannot identify them,
        and after ``brain.REFUSE_AFTER_FRAMES`` correctly ambers them. That is
        a real property of putting the corroboration surface and the
        measurement surface on the same sheet of paper. The fix is a coupling
        between MUDRA and registration that does not exist yet and belongs in
        brain.py, which this module does not own. The beat order puts the sale
        first so the ledger is already written when it happens, and the
        collision is then left visible rather than hidden behind a shorter
        script.
        """
        if name == "goods":
            return [(s, self._goods_y_mm(k, PARK_Y_MM)) for s in KNOWN_SKUS]
        if name == "bag":
            # Bagged one at a time, right to left, so the tracker sees three
            # separate disappearances rather than one impossible instant.
            keep = len(KNOWN_SKUS) - (k + 1) // 2
            return [(s, PARK_Y_MM) for s in KNOWN_SKUS[:max(0, keep)]]
        if name == "unknown":
            return [(UNKNOWN_SKU, self._goods_y_mm(k, UNKNOWN_PARK_Y_MM))]
        return []

    # ------------------------------------------------------------- painting

    def _paint_sticker(self, buf: np.ndarray, tampered: bool) -> None:
        """The printed sticker: a blocky 16x16 module pattern in the ROI.

        High contrast so ECC has something to lock onto, and structured so
        ``ident_sticker._blind_mask`` — which writes off regions where
        structure was DESTROYED, because that is glare or a thumb rather than a
        substitution — has no reason to blind any of it.

        The tamper REPLACES one sixteenth of the modules and does two specific
        things, both of which are measured in tests/test_sim_source.py:

          * it does not BLANK the patch. A flat patch is destroyed structure,
            which is exactly what the blind mask excuses, and it measures a
            0.0 ignited fraction — a "tamper" that reads GENUINE.
          * it INVERTS the modules rather than re-randomising them. A fresh
            random 4x4 agrees with the original on about half its cells by
            chance, so a 6.25 %-area patch ignites only ~2.6 % — under the 3 %
            gate. Inverting guarantees every module in the patch differs.
        """
        x_mm, y_mm, w_mm, h_mm = self.roi_mm
        x0, y0, x1, y1 = _mm_box(x_mm + w_mm / 2.0, y_mm + h_mm / 2.0, w_mm, h_mm)
        h, w = y1 - y0, x1 - x0
        rng = np.random.default_rng(self.seed ^ 0x5715)
        cells = rng.integers(0, 2, size=(16, 16)).astype(np.uint8) * 190 + 20
        if tampered:
            cells = cells.copy()
            r0, c0 = 5, 5
            block = cells[r0:r0 + 4, c0:c0 + 4]
            cells[r0:r0 + 4, c0:c0 + 4] = 230 - block
        buf[y0:y1, x0:x1] = cv2.resize(cells, (w, h),
                                       interpolation=cv2.INTER_NEAREST)

    def _jitter_sticker(self, buf: np.ndarray, i: int) -> None:
        """Shift the sticker ROI by a deterministic sub-pixel offset.

        The offsets walk an irrational-ratio spiral so the sampling PHASE keeps
        changing rather than cycling: SAAF measures diversity as the circular
        variance of that phase, and a repeating offset scores zero however far
        the crop actually moved.
        """
        if self.JITTER_PX <= 0.0:
            return
        x_mm, y_mm, w_mm, h_mm = self.roi_mm
        x0, y0, x1, y1 = _mm_box(x_mm + w_mm / 2.0, y_mm + h_mm / 2.0, w_mm, h_mm)
        pad = 5
        sy0, sy1 = max(0, y0 - pad), min(BUF_H, y1 + pad)
        sx0, sx1 = max(0, x0 - pad), min(BUF_W, x1 + pad)
        src = buf[sy0:sy1, sx0:sx1]
        dx = self.JITTER_PX * math.cos(i * 2.39996)   # golden angle, radians
        dy = self.JITTER_PX * math.sin(i * 1.61803)
        m = np.array([[1.0, 0.0, dx], [0.0, 1.0, dy]], dtype=np.float32)
        moved = cv2.warpAffine(
            src, m, (src.shape[1], src.shape[0]),
            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE,
        )
        buf[y0:y1, x0:x1] = moved[y0 - sy0:y0 - sy0 + (y1 - y0),
                                  x0 - sx0:x0 - sx0 + (x1 - x0)]

    def _blur_sticker(self, buf: np.ndarray) -> None:
        """Ruin one burst frame the way a real one gets ruined: motion during
        the exposure. Applied to a PADDED region so the blur pulls in genuine
        neighbours rather than a replicated border, which would leave a hard
        artificial edge for SAAF to register against."""
        x_mm, y_mm, w_mm, h_mm = self.roi_mm
        x0, y0, x1, y1 = _mm_box(x_mm + w_mm / 2.0, y_mm + h_mm / 2.0, w_mm, h_mm)
        pad = 12
        sy0, sy1 = max(0, y0 - pad), min(BUF_H, y1 + pad)
        sx0, sx1 = max(0, x0 - pad), min(BUF_W, x1 + pad)
        region = cv2.GaussianBlur(buf[sy0:sy1, sx0:sx1], (0, 0),
                                  self.BURST_BLUR_SIGMA)
        buf[y0:y1, x0:x1] = region[y0 - sy0:y0 - sy0 + (y1 - y0),
                                   x0 - sx0:x0 - sx0 + (x1 - x0)]

    # --------------------------------------------------------------- frames

    def frame(self, i: int) -> np.ndarray:
        """The rectified buffer for absolute frame ``i``. Pure and repeatable.

        Returns the 2-D grey buffer when ``colour=False``, otherwise a BGR
        array whose three channels are identical (see the module docstring for
        why they are identical rather than tinted).
        """
        buf = self._grey_frame(i)
        if not self.colour:
            return buf
        return cv2.cvtColor(buf, cv2.COLOR_GRAY2BGR)

    def _grey_frame(self, i: int) -> np.ndarray:
        cur = self.beat_at(i)
        beat, k = cur.beat, cur.index
        name = beat.name
        buf = (self._tampered if name == "tamper" else self._base).copy()

        if name == "burst":
            self._jitter_sticker(buf, i)
            if k == self.BLURRED_BURST_INDEX:
                self._blur_sticker(buf)

        for spec, y_mm in self._skus_on_mat(name, k):
            _paste_oriented(
                buf,
                (spec.x_mm, y_mm),
                spec.long_mm,
                spec.short_mm,
                spec.angle_deg,
                _wrapper_texture(self.seed ^ spec.seed,
                                 int(round(spec.long_mm * PX_PER_MM_Y)),
                                 int(round(spec.short_mm * PX_PER_MM_X))),
            )

        if name == "screen":
            _paste_screen(buf)
        elif name == "palm":
            _paste_open_palm(buf)
        elif name == "fist":
            _paste_fist(buf)
        elif name == "unsure":
            _paste_unsure(buf)

        return buf

    def note_at(self, i: int) -> SimNote:
        cur = self.beat_at(i)
        beat, k = cur.beat, cur.index
        burst = beat.name == "burst"
        return SimNote(
            frame_index=i,
            beat=beat.name,
            beat_index=k,
            panel=beat.panel,
            title=beat.title,
            expects=beat.expects,
            label=f"SIMULATED — {beat.title} ({k + 1}/{beat.frames})",
            commands=tuple(self.commands_at(i)),
            burst_member=burst,
            burst_blurred=burst and k == self.BLURRED_BURST_INDEX,
        )

    def frames(self) -> Iterator[tuple[np.ndarray, str, SimNote]]:
        """Yield ``(frame_bgr, ts, note)`` for the whole script, once.

        Stops at the end rather than looping. A consumer that wants to hold the
        last board on screen should repeat the final frame itself, so that the
        decision to stop moving is visibly the CONSUMER's and not a silent
        replay of a sale that already settled.
        """
        for i in range(self.total_frames):
            self._i = i
            yield self.frame(i), self.clock.now_iso(), self.note_at(i)

    def next_frame(self) -> tuple[np.ndarray, str, SimNote]:
        """One frame, statefully, for a pump that cannot hold a generator."""
        i = self._i
        self._i += 1
        j = min(i, self.total_frames - 1)
        return self.frame(j), self.clock.now_iso(), self.note_at(j)

    def reset(self) -> None:
        self._i = 0

    # ------------------------------------------------------------ commands

    def commands_at(self, i: int) -> list[dict[str, Any]]:
        """Client messages the script taps at frame ``i``.

        These are the SHOPKEEPER's taps and nothing else. There is deliberately
        no message here that pays, mints, settles or signs: the only thing that
        can turn a light green is a signature-verified webhook arriving at the
        brain, and putting a "pay" tap in the UI stream would put that power in
        the browser. Invariant 2 is a shape, not a comment.

        The focus tap is only sent for a panel BOTH sides name the same way —
        see FOCUSABLE_PANELS. A tap the server refuses puts "brain refused:
        UNKNOWN_PANEL" on the shopkeeper's screen, and a demo that shouts a
        refusal nobody caused is worse than a demo that does not change panel.
        """
        cur = self.beat_at(i)
        beat, k = cur.beat, cur.index
        out: list[dict[str, Any]] = []
        if k == 0 and beat.panel in FOCUSABLE_PANELS:
            out.append({"type": "select_panel", "id": beat.panel,
                        "simulated": True})
        if beat.name == "enrol" and k == 0:
            out.append({"type": "enrol_sticker", "name": self.sticker_name,
                        "simulated": True})
        if beat.name == "lift" and k == beat.frames - 1:
            out.append({"type": "done", "simulated": True})
        return out

    sticker_name = "counter-upi"

    # -------------------------------------------------- consumer conveniences

    def reference_frame(self) -> np.ndarray:
        """The empty-mat reference: frame 0 of the ``settle`` beat.

        This is the honest reference — a picture of the mat with nothing on it
        — and it is what PlacementDetector, OccluderGesture and ScreenFinder
        should all be seeded with.
        """
        return self.frame(0)

    def enrolment_frame(self, sku_id: str) -> np.ndarray:
        """A frame with one known SKU alone on the mat, in its ENROLMENT pose.

        Deliberately a different y from anywhere it is sold, so that a later
        identification is a genuine appearance match and not a self-match
        against a pixel-identical crop.
        """
        spec = self.sku(sku_id)
        buf = self._base.copy()
        _paste_oriented(
            buf,
            (spec.x_mm, 150.0),
            spec.long_mm, spec.short_mm, spec.angle_deg,
            _wrapper_texture(self.seed ^ spec.seed,
                             int(round(spec.long_mm * PX_PER_MM_Y)),
                             int(round(spec.short_mm * PX_PER_MM_X))),
        )
        return buf if not self.colour else cv2.cvtColor(buf, cv2.COLOR_GRAY2BGR)

    def _paste_goods(self, buf: np.ndarray, y_mm: float,
                     sku_id: str = "CHAI-250") -> None:
        """Paint one known SKU onto ``buf`` at ``y_mm``.

        Named with a leading underscore ON PURPOSE, against the usual rule.
        ``brain_server.build_sim_server`` probes its frame source for exactly
        this attribute to decide whether it can build a gallery:

            paste = getattr(script, "_paste_goods", None)

        and a source that does not have it "simply ships an empty gallery and
        the goods land as AMBER — an honest 'I do not know what this is' rather
        than a price this file made up". That fallback is correct, and an
        all-amber demo is a much weaker one, so this method exists to satisfy
        the probe.

        ``enrol_gallery`` below is the better door and enrols all THREE SKUs
        with their own prices; this one only covers the single-packet shape the
        probe was written for.
        """
        spec = self.sku(sku_id)
        _paste_oriented(
            buf, (spec.x_mm, float(y_mm)), spec.long_mm, spec.short_mm,
            spec.angle_deg,
            _wrapper_texture(self.seed ^ spec.seed,
                             int(round(spec.long_mm * PX_PER_MM_Y)),
                             int(round(spec.short_mm * PX_PER_MM_X))),
        )

    def enrol_gallery(self, gallery: Any, embed_fn: Any,
                      crop_fn: Any) -> dict[str, int]:
        """Enrol every KNOWN SKU into ``gallery`` and return the price book.

        The one call a consumer needs to make the money half of the script
        work. ``crop_fn(frame, placement)`` is the oriented-crop function the
        identifier will later be fed — pass ``Brain._crop`` — so what is
        enrolled and what is matched go through the same optics.

        Enrolment uses ``enrolment_frame``, which puts the item at y = 150 mm,
        a pose it is never SOLD in. That matters: enrolling from a frame the
        item is later identified in would make every match a pixel-identical
        self-match, and the identifier would look like it worked when it had
        done nothing.

        Raises rather than half-enrolling. A gallery missing one SKU prices two
        thirds of a basket and ambers the rest, which looks like a detection
        failure and is actually a wiring failure.
        """
        # Local: keeps sim_source importable by anything that only wants
        # frames, and keeps placement.py's cv2 cost off that path.
        from gawaah.placement import PlacementDetector

        ref = self.reference_frame()
        prices: dict[str, int] = {}
        for spec in KNOWN_SKUS:
            buf = self.enrolment_frame(spec.sku_id)
            det = PlacementDetector(ref)
            found: Any = ()
            for _ in range(6):
                found = det.update(buf)
            usable = [p for p in found if p.measurable and p.long_edge_mm]
            if len(usable) != 1:
                raise SimError(
                    f"enrolment frame for {spec.sku_id} segmented into "
                    f"{len(usable)} measurable objects, expected 1"
                )
            p = usable[0]
            gallery.enroll(spec.sku_id, [embed_fn(crop_fn(buf, p))],
                           float(p.long_edge_mm))
            prices[spec.sku_id] = int(spec.price_paise)
        return prices

    def sku(self, sku_id: str) -> SkuSpec:
        for s in SKUS:
            if s.sku_id == sku_id:
                return s
        raise SimError(f"no SKU {sku_id!r} in the sim script")

    @property
    def skus(self) -> tuple[SkuSpec, ...]:
        return SKUS

    @property
    def known_skus(self) -> tuple[SkuSpec, ...]:
        return KNOWN_SKUS

    @property
    def unknown_sku(self) -> SkuSpec:
        return UNKNOWN_SKU

    def prices(self) -> dict[str, int]:
        """The price book for the KNOWN SKUs, integer paise (invariant 1).

        The unknown SKU is absent, not zero-priced. A missing key is what makes
        the exclusion visible; a zero would make it silent.
        """
        return {s.sku_id: int(s.price_paise) for s in KNOWN_SKUS
                if s.price_paise is not None}

    def expected_total_paise(self) -> int:
        """What the three known items come to, in paise.

        A LABEL, not an authorisation. Nothing in this module can move money;
        this exists so a UI can say "the total should read this" next to a
        total the brain computed independently, and so a test can tell a
        correct total from a coincidence.
        """
        return sum(int(s.price_paise) for s in KNOWN_SKUS
                   if s.price_paise is not None)

    # -------------------------------------------------------------- crops

    def sticker_crop(self, frame: np.ndarray) -> np.ndarray:
        """The sticker ROI out of a frame, for PEEL and for SAAF."""
        x_mm, y_mm, w_mm, h_mm = self.roi_mm
        x0, y0, x1, y1 = _mm_box(x_mm + w_mm / 2.0, y_mm + h_mm / 2.0, w_mm, h_mm)
        if frame.shape[0] != BUF_H or frame.shape[1] != BUF_W:
            raise SimError(
                f"sticker_crop wants the {BUF_W}x{BUF_H} rectified buffer, "
                f"got {frame.shape}"
            )
        return frame[y0:y1, x0:x1].copy()

    def screen_rect_mm(self) -> tuple[float, float, float, float]:
        """What the phone actually is, in millimetres: (cx, cy, w, h).

        The number to hold CHILLA's measurement against. It is published so a
        consumer can show "composited 65.0 x 130.0, measured 63.3 x 128.3"
        side by side rather than being asked to trust the measurement.
        """
        return (SCREEN_CENTRE_MM[0], SCREEN_CENTRE_MM[1], SCREEN_W_MM, SCREEN_H_MM)

    def burst_frame_indices(self) -> tuple[int, ...]:
        b = self.beat("burst")
        return tuple(range(b.start, b.stop))

    def saaf_burst_crops(self) -> list[np.ndarray]:
        """The burst as SAAF should receive it: the sticker ROI from each frame
        of the ``burst`` beat, in order, blurred frame included."""
        return [self.sticker_crop(self._grey_frame(i))
                for i in self.burst_frame_indices()]

    def stack_burst(self, stacker: Any = None) -> Any:
        """Run the REAL ``BurstStacker`` over the real burst and hand back its
        ``StackResult``.

        Imported lazily so that a consumer who only wants frames does not pay
        for saaf's import, and so this module's own dependency surface stays
        readable.
        """
        if stacker is None:
            from gawaah.saaf import BurstStacker
            stacker = BurstStacker()
        return stacker.stack(self.saaf_burst_crops())

    # ------------------------------------------------------------- summary

    def describe(self) -> str:
        """One human-readable page of what this session will do. Every line is
        prefixed SIMULATED, because every line is."""
        lines = [
            f"SIMULATED counter session  seed={self.seed}  "
            f"{self.total_frames} frames @ {self.period_s:.2f}s",
            f"SIMULATED mat: {BUF_W}x{BUF_H} rectified TAKHTI, "
            f"{MAT_W_MM:.0f}x{MAT_H_MM:.0f} mm",
            f"SIMULATED sell line at y={SELL_LINE_Y_MM:.0f} mm",
        ]
        for b in self._beats:
            lines.append(
                f"SIMULATED  [{b.start:3d}..{b.stop - 1:3d}] "
                f"{b.name:<8} {b.panel:<6} {b.title} — {b.expects}"
            )
        return "\n".join(lines)
