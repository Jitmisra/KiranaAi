"""S6 — the BRIDGE. The one process that makes the counter and its screen meet.

Eighteen modules exist. Five of them — MUDRA, PEEL (ident_sticker), CHILLA,
SAAF and the BRAIN itself — are fully tested Python with, until this file, no
way to reach a browser. `web/app.js` implements the billing loop and dials
``ws://localhost:8787``. Nothing answered. This module answers.

It is deliberately a BRIDGE and not a second brain. Every verdict on the wire
was computed by the module that owns it; this file decodes a frame, refuses it
if it is the wrong thing, hands it to the real modules, and serialises what
they said. It decides nothing about money and it holds no secret.


THE PROTOCOL
============

One WebSocket. JSON text frames. Served at ``/ws`` and also at ``/`` because
``web/app.js`` has ``export const WS_URL = 'ws://localhost:8787'`` — the ROOT
path — and a bridge that is not mounted where the client dials is not a bridge.

client -> server
----------------
``{"type": "frame", "rect": "<base64 PNG>", "ts": "<iso>"}``
    One RECTIFIED CROP. Base64 of a PNG whose pixel dimensions are exactly
    840x1188. See INVARIANT 4 below — this is the load-bearing message and the
    only one that can be refused on shape.

``{"type": "done"}``
    Close the basket and ask the settlement port to mint. Cannot authorise
    money; see INVARIANT 5.

``{"type": "revert", "item_id": "<id>"}``
    Tap-to-revert one basket line. ``itemId`` is accepted as a spelling.

``{"type": "ack"}``
    The shopkeeper accepted a frozen-total exception and resumed.

``{"type": "enrol_sticker", "name": "<name>"}``
    Stack the SAAF burst collected from the sticker ROI and enrol the result as
    the PEEL reference for ``name``. Emits a ``saaf`` message (what the stacker
    did) followed by a ``peel`` message (what the registry now holds).

``{"type": "select_panel", "id": "<panel>"}``
    Focus one of ``basket mudra peel chilla saaf ledger``. The server replies
    with a ``panel`` message and immediately REPLAYS that panel's last message,
    so a freshly-focused panel is never blank while it waits for a frame.

``{"type": "refresh"}``
    Re-send the current ``state`` and ``ledger``.

    Not a convenience. The brain can change WITHOUT a frame and without a
    client message: a signed webhook delivered to it out of band is exactly
    that, and it is the one event that can move a session to PAID. Nothing
    about that delivery passes through this socket, so without a refresh the
    browser would not learn about a settlement until the next frame happened to
    arrive. Whatever hands a webhook to the brain must call this afterwards;
    the ``--sim`` script does, right after the customer pays.

server -> client
----------------
``{"type": "state", ...}``
    The whole ``BrainState`` inlined at the top level, sent on every change.
    ``total_paise``, ``price_paise``, ``intent_amount_paise`` are INTEGER
    PAISE. There is no float anywhere in this message and no image in it.

``{"type": "mudra", "state", "solidity", "defects", "area_mm2", ...}``
``{"type": "peel", "name", "ignited_fraction", "verdict", "ecc_ok", ...}``
``{"type": "chilla", "verdict", "amount_paise", "candidates", "reason"}``
``{"type": "saaf", "used", "rejected", "sharpness_gain", "warning"}``
``{"type": "ledger", "head", "count"}``
``{"type": "refused", "reason", "detail", ...}``
    ``reason`` is one of ``REFUSAL_REASONS``, all upper case. A refusal is
    "your message was wrong"; it is never a verdict about the counter.
``{"type": "panel", "id", "known"}``
``{"type": "keepalive"}``

Every message also carries ``frame_index``, so a client can tell which frame a
panel is talking about and never paint a MUDRA reading beside a basket from a
different frame.

EVERY panel message carries ``ok`` and ``reason``. ``ok: false`` is the panel's
"I do not know", and ``reason`` names why (INVARIANT 7). A panel that has never
run says so; it does not send zeros that look like measurements.


THE INVARIANTS, AND WHERE THEY LIVE IN THIS FILE
================================================

INVARIANT 3 — zero model weights.
    By omission. Nothing here loads, fetches or ships a weight file, and the
    browser is sent JSON with millimetres, paise and named reasons in it.

INVARIANT 4 — the mask is applied AT FRAME GRAB.
    ``decode_rect()`` is the gate. A frame is accepted only if it base64-decodes,
    PNG-decodes, and measures exactly ``(BUF_H, BUF_W) == (1188, 840)``. A raw
    camera frame is a different shape and is REFUSED with
    ``RECT_WRONG_SHAPE``, logged at WARNING, and counted in
    ``BrainServer.refusals``. The brain is never handed the bytes.

    HONEST LIMIT, stated because the alternative is a false sense of safety:
    shape is NECESSARY, not SUFFICIENT. A caller that resized a raw frame to
    840x1188 before sending it would pass this gate. The real enforcement of
    invariant 4 is in the client, which masks at grab; this gate is the
    backstop that makes the common bug — "someone wired the raw canvas to the
    socket" — loud and immediate instead of silent. ``mat_evidence()`` reports
    whether the four TAKHTI markers are actually present at their rectified
    positions, which IS positive evidence that the buffer is the mat, and it
    rides along on every refusal so an operator can tell the two cases apart.

INVARIANT 5 — this server does not hold Razorpay secrets and does not mint.
    ``BrainServer`` has no secret attribute and no constructor argument for
    one. ``done()`` calls ``Brain.done()``, which calls the injected
    ``SettlementPort``; the port holds the secret. On top of that,
    ``_scrub()`` is an outbound filter that refuses to send any message
    containing a registered forbidden string or a key whose name is in
    ``FORBIDDEN_KEYS``, so a leak becomes a dropped message and a logged
    error rather than bytes on a wire.

INVARIANT 2 — no feature turns the counter green.
    MUDRA reveals, PEEL warns, CHILLA corroborates, SAAF selects frames. None
    of them touches ``Brain``. ``chilla`` messages carry ``light: "AMBER"`` for
    every verdict including ``MATCHED``, because that is what
    ``chilla.MatchResult`` says. The only field on the wire that can read as
    settled is ``state.session_state``, and only a signature-verified webhook
    moves it.

INVARIANT 7 — abstain rather than guess.
    Six panels, six abstentions, all reachable and all tested:
    ``mudra`` before a reference exists, ``peel`` for a name nobody enrolled,
    ``chilla`` with no screen and with no mirror, ``saaf`` with too short a
    burst, ``state`` before the mat locks, ``ledger`` never abstains because a
    hash chain either verifies or does not.


THREADING
=========
``BrainServer`` is a synchronous object guarded by one lock, and every test in
``tests/test_brain_server.py`` drives it directly or through Starlette's
in-process ``TestClient``. The ASGI layer in ``create_app()`` is a thin async
shell over it. Frame work (PNG decode, OpenCV) runs INLINE on the event loop:
this is a single-shopkeeper counter with one browser attached, and an executor
hop would buy throughput nobody needs at the cost of reordering the very
messages whose order is the protocol.


RUNNING IT
==========
``python -m gawaah.brain_server --sim`` serves ``web/`` and the socket on 8787.

HONEST LIMIT ON ``--sim`` IN THIS REPO: uvicorn cannot speak WebSocket without
``websockets`` or ``wsproto``, and neither is installed in this venv (network
installs are not permitted here). ``main()`` detects that and says so exactly,
rather than starting a server whose socket would fail every upgrade. Until one
of them is installed, ``--sim --dry-run`` drives the identical ASGI application
through the in-process test transport and prints every message the browser
would have received — the same code path, no socket.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import json
import logging
import math
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Mapping, Optional, Sequence

import cv2
import numpy as np

from gawaah import chilla as _chilla
from gawaah import ident_sticker as _peel
from gawaah import mudra as _mudra
from gawaah import saaf as _saaf
from gawaah.brain import Brain, BrainConfig, BrainState, LocalSettlement
from gawaah.clock import Clock, RealClock, VirtualClock
from gawaah.money import paise as _paise
from gawaah.takhti import BUF_H, BUF_W, MAT_H_MM, MAT_W_MM, MatLock, render_takhti

__all__ = [
    "MODULE",
    "DEFAULT_PORT",
    "RECT_SHAPE",
    "PANELS",
    "FORBIDDEN_KEYS",
    "BridgeError",
    "SecretLeak",
    "RectVerdict",
    "decode_rect",
    "mat_evidence",
    "ClientRectifiedPlane",
    "BrainServer",
    "create_app",
    "SimScript",
    "build_sim_server",
    "main",
]

log = logging.getLogger("gawaah.brain_server")

MODULE = "brain_server"

#: What web/app.js dials.
DEFAULT_PORT = 8787

#: The ONLY buffer shape this server will accept, as (rows, cols). This is the
#: rectified TAKHTI crop and nothing else. 840x1188 px over 297x420 mm.
RECT_SHAPE: tuple[int, int] = (BUF_H, BUF_W)

#: A 840x1188 PNG of real counter texture is ~600 KB; base64 inflates by 4/3.
#: 8 MB is roughly ten times the worst honest frame, which makes this a bound on
#: a runaway client rather than a limit a real one can hit.
MAX_FRAME_B64 = 8 * 1024 * 1024

PANELS: tuple[str, ...] = ("basket", "mudra", "peel", "chilla", "saaf", "ledger")

#: Every message type a client may send. Published so a refusal can tell the
#: client what it COULD have said, and so the tests can drive all of them.
CLIENT_VERBS: tuple[str, ...] = (
    "frame",
    "done",
    "revert",
    "ack",
    "enrol_sticker",
    "select_panel",
    "refresh",
)

#: Key names that must never appear anywhere in an outbound message. Checked by
#: substring on the lowercased key, so `webhook_secret` and `keySecret` both
#: trip. This is a structural guard, not cryptography: it turns "the bridge
#: started echoing a config dict" from a silent leak into a dropped message.
FORBIDDEN_KEYS: tuple[str, ...] = (
    "secret",
    "api_key",
    "apikey",
    "password",
    "passwd",
    "private_key",
    "privatekey",
    "authorization",
    "auth_token",
    "bearer",
    "credential",
)

#: A forbidden string shorter than this is refused at registration: an 8-char
#: floor stops someone registering "a" and blackholing every message.
MIN_FORBIDDEN_LEN = 8

# -- refusal / abstention reason codes ---------------------------------------
# Every one of these is emitted somewhere below and asserted in the tests. A
# reason code that cannot fire is a lie in a docstring, so the test suite walks
# this tuple and drives each one.

R_NOT_A_MESSAGE = "NOT_A_MESSAGE"
R_UNKNOWN_TYPE = "UNKNOWN_TYPE"
R_RECT_MISSING = "RECT_MISSING"
R_RECT_NOT_BASE64 = "RECT_NOT_BASE64"
R_RECT_TOO_LARGE = "RECT_TOO_LARGE"
R_RECT_NOT_AN_IMAGE = "RECT_NOT_AN_IMAGE"
R_RECT_WRONG_SHAPE = "RECT_WRONG_SHAPE"
R_UNKNOWN_PANEL = "UNKNOWN_PANEL"
R_UNKNOWN_ITEM = "UNKNOWN_ITEM"
R_BAD_ARGUMENT = "BAD_ARGUMENT"
R_BRAIN_REFUSED = "BRAIN_REFUSED"
R_OUTBOUND_REDACTED = "OUTBOUND_REDACTED"

REFUSAL_REASONS: tuple[str, ...] = (
    R_NOT_A_MESSAGE,
    R_UNKNOWN_TYPE,
    R_RECT_MISSING,
    R_RECT_NOT_BASE64,
    R_RECT_TOO_LARGE,
    R_RECT_NOT_AN_IMAGE,
    R_RECT_WRONG_SHAPE,
    R_UNKNOWN_PANEL,
    R_UNKNOWN_ITEM,
    R_BAD_ARGUMENT,
    R_BRAIN_REFUSED,
    R_OUTBOUND_REDACTED,
)

#: Panel abstentions. Distinct from refusals: a refusal is "your message was
#: wrong", an abstention is "your message was fine and I still do not know".
A_NO_REFERENCE = "no_reference"
A_NO_SCREEN = "no_screen"
A_NO_INTENT_AMOUNT = "no_intent_amount"
A_NO_MIRROR = "no_mirror"
A_BURST_TOO_SHORT = "burst_too_short"
A_NEVER_RUN = "never_run"
A_STACK_REFUSED = "stack_refused"
A_ENROLMENT_REFUSED = "enrolment_refused"

ABSTENTIONS: tuple[str, ...] = (
    A_NO_REFERENCE,
    A_NO_SCREEN,
    A_NO_INTENT_AMOUNT,
    A_NO_MIRROR,
    A_BURST_TOO_SHORT,
    A_NEVER_RUN,
    A_STACK_REFUSED,
    A_ENROLMENT_REFUSED,
)

#: The sticker ROI on the mat, (x_mm, y_mm, w_mm, h_mm). 70 mm square lands at
#: 198 px on this buffer, comfortably over ident_sticker.MIN_CROP_PX (64) with
#: room for the ECC border erosion. Placed top-left, clear of the exit line and
#: clear of the ArUco corners, so a printed UPI sticker there is never mistaken
#: for goods and never occludes the mat lock.
DEFAULT_STICKER_ROI_MM: tuple[float, float, float, float] = (30.0, 40.0, 70.0, 70.0)

#: Frames of sticker ROI kept for SAAF. Ten is the shot list's burst length; the
#: deque is bounded so a server left running for an hour does not hold an hour
#: of crops.
BURST_LEN = 10

#: SAAF runs at scale=1 for enrolment, deliberately. StickerRegistry._compare
#: resizes the FRESH crop to the reference's shape, so a 2x super-resolved
#: reference would force every later comparison through a 2x upscale, and an
#: upscaled crop loses enough high-frequency energy to trip R_FOCUS_MISMATCH
#: (sharpness_ratio < 0.55) on a sticker nobody touched. At scale=1 SAAF still
#: does the thing that matters here — reject the blurred, glared and misaligned
#: frames and average sqrt(N) of the noise out of the rest — and the reference
#: stays comparable to the single frames it will be compared against.
SAAF_SCALE = 1


class BridgeError(RuntimeError):
    """Programmer error at this module's boundary. Never a bad frame."""


class SecretLeak(BridgeError):
    """An outbound message contained something that must not leave."""


# ============================================================ INVARIANT 4 gate


@dataclass(frozen=True)
class RectVerdict:
    """The frame gate's answer. ``image`` is non-None exactly when ``ok``."""

    ok: bool
    reason: str
    detail: str = ""
    shape: Optional[tuple[int, ...]] = None
    image: Optional[np.ndarray] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "detail": self.detail,
            "shape": None if self.shape is None else list(self.shape),
            "expected_shape": [RECT_SHAPE[0], RECT_SHAPE[1]],
        }


def decode_rect(rect_b64: Any, *, max_b64: int = MAX_FRAME_B64) -> RectVerdict:
    """Decode ``{"rect": ...}`` into the rectified buffer, or refuse it.

    THIS IS THE INVARIANT 4 GATE. Read the four refusals as a single sentence:
    the server accepts a base64 PNG that is exactly the rectified crop, and
    nothing else reaches the brain.

    The shape test is on ``shape[:2]`` so a colour or alpha PNG of the right
    pixel dimensions is accepted — the client may send RGB and the modules
    grey it themselves — but a 1280x960 camera frame, a thumbnail, a
    transposed buffer and an empty string are all refused by name.
    """
    if rect_b64 is None:
        return RectVerdict(False, R_RECT_MISSING, "no `rect` field on the frame message")
    if not isinstance(rect_b64, str):
        return RectVerdict(
            False,
            R_RECT_MISSING,
            f"`rect` must be a base64 string, got {type(rect_b64).__name__}",
        )
    if not rect_b64:
        return RectVerdict(False, R_RECT_MISSING, "`rect` was empty")
    if len(rect_b64) > max_b64:
        return RectVerdict(
            False,
            R_RECT_TOO_LARGE,
            f"{len(rect_b64)} base64 chars exceeds the {max_b64} cap",
        )

    payload = rect_b64
    if payload.startswith("data:"):
        # A browser's canvas.toDataURL() prefix. Accepted because refusing it
        # would be a papercut with no safety value: the bytes after the comma
        # are the same PNG and get the same shape gate.
        _, _, payload = payload.partition(",")
    try:
        blob = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        return RectVerdict(False, R_RECT_NOT_BASE64, f"base64 decode failed: {exc}")
    if not blob:  # pragma: no cover - no non-empty valid base64 decodes to b""
        return RectVerdict(False, R_RECT_NOT_BASE64, "base64 decoded to zero bytes")

    buf = np.frombuffer(blob, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
    if img is None:
        return RectVerdict(
            False, R_RECT_NOT_AN_IMAGE, "the bytes are not a decodable image"
        )
    shape = tuple(int(v) for v in img.shape)
    if img.ndim not in (2, 3) or shape[:2] != RECT_SHAPE:
        return RectVerdict(
            False,
            R_RECT_WRONG_SHAPE,
            (
                f"expected the rectified {BUF_W}x{BUF_H} crop, got "
                f"{shape[1] if len(shape) > 1 else '?'}x{shape[0]}. "
                "Rectify and mask at frame grab (invariant 4); this server "
                "never accepts a raw camera frame."
            ),
            shape=shape,
        )
    return RectVerdict(True, "rectified", "", shape=shape, image=img)


def mat_evidence(rect: np.ndarray) -> dict[str, Any]:
    """Positive evidence that ``rect`` really is the rectified TAKHTI.

    Runs the same ArUco dictionary the plane engine uses over the buffer and
    reports which of the four mat markers are present. This is the honest
    complement to the shape gate: shape says "the right size", this says "the
    right thing". It is EVIDENCE, not a gate — a badly lit but genuinely
    rectified frame can lose a marker, and refusing it would abstain the whole
    counter on a shadow. It rides on refusals so an operator can tell
    "wrong-sized mat photo" from "somebody piped the raw canvas in".
    """
    out: dict[str, Any] = {"markers_found": [], "markers_expected": [0, 1, 2, 3]}
    try:
        gray = rect if rect.ndim == 2 else cv2.cvtColor(rect, cv2.COLOR_BGR2GRAY)
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())
        _corners, ids, _rej = detector.detectMarkers(gray)
    except Exception as exc:  # pragma: no cover - defensive; evidence never raises
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    if ids is not None:
        out["markers_found"] = sorted(int(i) for i in ids.ravel())
    return out


# =================================================== the client-rectified plane


class ClientRectifiedPlane:
    """A ``PlaneEngine``-shaped adapter for a buffer that is ALREADY rectified.

    ``Brain.ingest_frame`` takes a raw camera frame and does detect-then-
    rectify itself. This server's clients do the homography in the browser
    (``web/app.js`` carries the 3x3 maths in plain JS) and, per invariant 4,
    send ONLY the rectified crop. So the plane the brain is injected with is
    this: detect() confirms the buffer is the expected shape and hands back an
    identity homography, rectify() is the identity function.

    This is not a stub that pretends to lock. The gate that decides whether a
    frame is admitted at all is ``decode_rect``, which has already run before
    the brain sees anything; by the time detect() is called the buffer has been
    proven to be 840x1188. What detect() adds is the ability to carry the
    CLIENT'S OWN verdict: ``web/app.js`` runs ``adjudicateLock()`` and knows
    things this side cannot (marker count, reprojection error, scale error).
    ``push_client_lock()`` accepts that verdict for the next frame, so when the
    browser says it lost the mat, the brain hears "not locked" and abstains
    instead of billing against a plane nobody could see.
    """

    def __init__(self) -> None:
        self._pending: Optional[Mapping[str, Any]] = None

    def push_client_lock(self, lock: Optional[Mapping[str, Any]]) -> None:
        """Supply the browser's own lock adjudication for the next detect()."""
        self._pending = lock if isinstance(lock, Mapping) else None

    def detect(self, frame: np.ndarray) -> MatLock:
        claim = self._pending
        self._pending = None
        shape_ok = (
            isinstance(frame, np.ndarray)
            and frame.ndim in (2, 3)
            and tuple(frame.shape[:2]) == RECT_SHAPE
        )
        if not shape_ok:  # pragma: no cover - decode_rect already refused these
            return MatLock(False, "buffer_not_rectified")
        if claim is not None and claim.get("locked") is False:
            reason = str(claim.get("reason") or "client_reports_no_lock")
            return MatLock(False, reason)

        # EMPTY unless the client actually told us. This side ran no detector:
        # it was handed a buffer that was already rectified. Defaulting to
        # (0, 1, 2, 3) would put four markers this process never saw into a
        # ledger line and onto the UI, which is fabricating evidence — the one
        # thing invariant 7 exists to stop. Found by
        # test_the_plane_adapter_ignores_a_malformed_claim.
        ids: tuple[int, ...] = ()
        scale_err = persp = rmse = None
        if claim is not None:
            raw_ids = claim.get("ids_found")
            if isinstance(raw_ids, (list, tuple)):
                try:
                    ids = tuple(int(i) for i in raw_ids)
                except (TypeError, ValueError):
                    ids = ()
            scale_err = _as_float(claim.get("scale_err"))
            persp = _as_float(claim.get("persp_index"))
            rmse = _as_float(claim.get("reproj_rmse_px"))
        return MatLock(
            True,
            "client_rectified",
            H=np.eye(3, dtype=np.float64),
            ids_found=ids,
            scale_err=scale_err,
            persp_index=persp,
            reproj_rmse_px=rmse,
        )

    @staticmethod
    def rectify(frame: np.ndarray, H: np.ndarray) -> np.ndarray:
        """Identity. The client already did this, which is the whole point."""
        return frame


def _as_float(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _round(v: Any, places: int = 4) -> Optional[float]:
    f = _as_float(v)
    return None if f is None else round(f, places)


# ================================================================ the scrubber


def _walk_strings(obj: Any):
    """Yield every (key, value) string in a JSON-shaped object."""
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            yield ("key", str(k))
            yield from _walk_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_strings(v)
    elif isinstance(obj, str):
        yield ("value", obj)


def scrub(msg: Mapping[str, Any], forbidden: Sequence[str]) -> None:
    """Raise ``SecretLeak`` if ``msg`` carries anything that must not leave.

    Two independent checks, because they fail differently. The KEY check
    catches a whole config object being echoed — the name gives it away before
    anyone has to know the value. The VALUE check catches a specific known
    secret appearing somewhere unexpected, which is the case where the key is
    innocent (``"detail"``, ``"reason"``) and the payload is not.
    """
    for kind, s in _walk_strings(msg):
        if kind == "key":
            low = s.lower()
            for bad in FORBIDDEN_KEYS:
                if bad in low:
                    raise SecretLeak(f"outbound message carries key {s!r} ({bad!r})")
        else:
            for secret in forbidden:
                if secret and secret in s:
                    raise SecretLeak(
                        f"outbound message carries a registered forbidden string "
                        f"({len(secret)} chars) in a {kind}"
                    )


# ============================================================== panel snapshots


@dataclass
class PanelMessage:
    """One panel's latest word, kept so ``select_panel`` can replay it."""

    payload: dict[str, Any]

    def copy(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.payload))


# ==================================================================== the server


class BrainServer:
    """The bridge. Owns a ``Brain`` and the four side modules, speaks JSON.

    Synchronous on purpose: every method here can be called from a test with
    no event loop, and ``create_app`` is a thin shell that awaits nothing this
    class does. One ``RLock`` serialises the whole thing, because a second
    browser tab must not interleave a frame with a ``done``.
    """

    def __init__(
        self,
        brain: Brain,
        *,
        web_dir: Optional[Path] = None,
        sticker_dir: Optional[Path] = None,
        clock: Optional[Clock] = None,
        plane: Optional[ClientRectifiedPlane] = None,
        sticker_roi_mm: tuple[float, float, float, float] = DEFAULT_STICKER_ROI_MM,
        burst_len: int = BURST_LEN,
        forbidden_strings: Sequence[str] = (),
        sim: Optional["SimScript"] = None,
        mirror: Optional[_chilla.Mirror] = None,
    ) -> None:
        self.brain = brain
        self.clock: Clock = clock or getattr(brain, "clock", None) or RealClock()
        self.web_dir = Path(web_dir) if web_dir is not None else None
        self.plane = plane
        self.sim = sim
        self._lock = RLock()

        self._sticker_dir = Path(
            sticker_dir
            if sticker_dir is not None
            else Path(tempfile.mkdtemp(prefix="gawaah-stickers-"))
        )
        self.registry = _peel.StickerRegistry(self._sticker_dir)
        self.roi_mm = tuple(float(v) for v in sticker_roi_mm)
        self.burst_len = int(burst_len)
        if self.burst_len < 1:
            raise BridgeError("burst_len must be >= 1")

        self.forbidden: tuple[str, ...] = ()
        for s in forbidden_strings:
            self.add_forbidden(s)

        # -- side modules. None until a reference frame exists, which is the
        # -- honest state and the one every panel reports as `no_reference`.
        self.gesture: Optional[_mudra.OccluderGesture] = None
        self.screens = _chilla.ScreenFinder()
        self.mirror = mirror if mirror is not None else _chilla.Mirror()
        self.matcher = _chilla.LedgerMatcher(self.mirror)

        self._burst: list[np.ndarray] = []
        self._peel_name: Optional[str] = None
        self._selected: str = "basket"

        self.frames_accepted = 0
        self.refusals: dict[str, int] = {r: 0 for r in REFUSAL_REASONS}
        self.leaks_blocked = 0
        self._last: dict[str, PanelMessage] = {}

    # -- housekeeping ------------------------------------------------------

    def add_forbidden(self, s: str) -> None:
        """Register a string that must never appear in an outbound message."""
        if not isinstance(s, str) or len(s) < MIN_FORBIDDEN_LEN:
            raise BridgeError(
                f"a forbidden string must be at least {MIN_FORBIDDEN_LEN} chars; "
                f"a short one would blackhole every message"
            )
        if s not in self.forbidden:
            self.forbidden = self.forbidden + (s,)

    @property
    def sticker_dir(self) -> Path:
        return self._sticker_dir

    def set_mirror(self, mirror: _chilla.Mirror) -> None:
        """Replace the settlement mirror CHILLA matches against.

        In a deployment this is refreshed from paisa. This server does not
        fetch it and does not own it — an empty mirror with no ``fetched_at``
        is infinitely stale, which is why the honest default verdict is
        ``AMBER_STALE`` and not ``NO_MATCH``.
        """
        with self._lock:
            self.mirror = mirror
            self.matcher = _chilla.LedgerMatcher(
                mirror, self.matcher.window_seconds,
                stale_threshold_s=self.matcher.stale_threshold_s,
            )

    def last(self, panel: str) -> Optional[dict[str, Any]]:
        m = self._last.get(panel)
        return None if m is None else m.copy()

    def _remember(self, msg: dict[str, Any]) -> dict[str, Any]:
        # STAMPED BEFORE STORING, which is the whole point. `select_panel`
        # replays this copy later, and a stamp applied at replay time would
        # label a MUDRA reading taken on frame 5 as frame 12 — a measurement
        # attributed to a frame it was not taken from. Stamping here freezes
        # the frame the panel actually measured; `_stamp` uses setdefault so
        # the later pass leaves it alone.
        self._stamp(msg)
        t = str(msg.get("type"))
        panel = "basket" if t == "state" else t
        if panel in PANELS:
            self._last[panel] = PanelMessage(json.loads(json.dumps(msg)))
        return msg

    def _stamp(self, msg: dict[str, Any]) -> dict[str, Any]:
        msg.setdefault("frame_index", int(self.brain.frame_index))
        return msg

    # -- the outbound filter ----------------------------------------------

    def safe(self, msg: Mapping[str, Any]) -> dict[str, Any]:
        """Every message leaves through here. INVARIANT 5's last line."""
        try:
            scrub(msg, self.forbidden)
        except SecretLeak as leak:
            self.leaks_blocked += 1
            self.refusals[R_OUTBOUND_REDACTED] += 1
            log.error("REFUSED to send a message: %s", leak)
            return {
                "type": "refused",
                "reason": R_OUTBOUND_REDACTED,
                "detail": (
                    "an outbound message was dropped because it carried a "
                    "forbidden key or string; see the server log"
                ),
                "dropped_type": str(msg.get("type", "?")),
            }
        return dict(msg)

    # -- refusals ----------------------------------------------------------

    def _refuse(self, reason: str, detail: str, **extra: Any) -> dict[str, Any]:
        self.refusals[reason] = self.refusals.get(reason, 0) + 1
        log.warning("refused: %s — %s %s", reason, detail, extra if extra else "")
        return {"type": "refused", "reason": reason, "detail": detail, **extra}

    # ================================================== the message dispatcher

    def handle(self, raw: Any) -> list[dict[str, Any]]:
        """One client message in, zero or more server messages out.

        Returns messages already passed through ``safe()``. The order is the
        protocol: for a frame it is state, then the four panels, then ledger,
        so a client that renders in arrival order never shows a panel that
        disagrees with the basket beside it.
        """
        with self._lock:
            out = self._handle_locked(raw)
        return [self.safe(self._stamp(m)) for m in out]

    def _handle_locked(self, raw: Any) -> list[dict[str, Any]]:
        if isinstance(raw, (str, bytes, bytearray)):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                return [self._refuse(R_NOT_A_MESSAGE, f"not JSON: {exc}")]
        if not isinstance(raw, Mapping):
            return [
                self._refuse(
                    R_NOT_A_MESSAGE,
                    f"a message must be a JSON object, got {type(raw).__name__}",
                )
            ]
        kind = raw.get("type")
        if not isinstance(kind, str):
            return [self._refuse(R_NOT_A_MESSAGE, "message has no `type` string")]

        handler = {
            "frame": self._on_frame,
            "done": self._on_done,
            "revert": self._on_revert,
            "ack": self._on_ack,
            "enrol_sticker": self._on_enrol_sticker,
            "enroll_sticker": self._on_enrol_sticker,  # the other spelling
            "select_panel": self._on_select_panel,
            "refresh": self._on_refresh,
        }.get(kind)
        if handler is None:
            return [
                self._refuse(
                    R_UNKNOWN_TYPE, f"no handler for {kind!r}", known=list(CLIENT_VERBS)
                )
            ]
        return handler(raw)

    # -- frame -------------------------------------------------------------

    def _on_frame(self, msg: Mapping[str, Any]) -> list[dict[str, Any]]:
        verdict = decode_rect(msg.get("rect"))
        if not verdict.ok:
            extra = verdict.to_dict()
            extra.pop("ok", None)
            extra.pop("reason", None)
            detail = extra.pop("detail", "")
            return [self._refuse(verdict.reason, detail, **extra)]

        rect = verdict.image
        assert rect is not None  # decode_rect's contract
        gray = rect if rect.ndim == 2 else cv2.cvtColor(rect, cv2.COLOR_BGR2GRAY)
        gray = np.ascontiguousarray(gray)
        ts = msg.get("ts")
        ts = ts if isinstance(ts, str) else None

        if self.plane is not None:
            self.plane.push_client_lock(msg.get("lock"))

        # INVARIANT 4: from here on `rect` and `gray` are the only pixels in
        # play, and neither is stored beyond the sticker ROI burst (which is a
        # 198x198 crop of the mat, not a frame). Nothing is written to disk and
        # nothing is echoed to the client.
        try:
            state = self.brain.ingest_frame(rect, ts=ts)
        except Exception as exc:  # a bad frame must not kill the socket
            log.exception("brain refused a frame")
            return [
                self._refuse(
                    R_BRAIN_REFUSED, f"{type(exc).__name__}: {exc}"
                )
            ]

        self.frames_accepted += 1
        first_reference = self.gesture is None
        if first_reference:
            self._seed_references(gray)

        out: list[dict[str, Any]] = [self._state_msg(state)]
        out.append(self._mudra_msg(gray, seeded=first_reference))
        self._push_burst(gray)
        out.append(self._peel_msg(gray))
        out.append(self._chilla_msg(gray, state, seeded=first_reference))
        out.append(self._saaf_msg())
        out.append(self._ledger_msg(state))
        return [self._remember(m) for m in out]

    def _seed_references(self, gray: np.ndarray) -> None:
        """The first accepted frame becomes the empty-mat reference.

        ANNOUNCED, never silent — the same rule ``Brain._ensure_detector``
        follows and for the same reason. Whatever is on the mat in this frame
        becomes "background": MUDRA will not see it as an occluder and CHILLA
        will not see it as a screen, for the rest of the session. If a hand was
        in shot when the browser connected, every later gesture is measured
        against a plane with a hand in it. The ledger line says so.
        """
        self.gesture = _mudra.OccluderGesture(gray)
        self.screens.set_reference(gray)
        self.brain.ledger.append(
            ts=self.clock.now_iso(),
            module=MODULE,
            what="reference_seeded",
            source="first_accepted_rectified_frame",
            detail=(
                "MUDRA and CHILLA now measure against this frame; anything on "
                "the mat in it is background and is invisible to both"
            ),
        )

    # -- panels ------------------------------------------------------------

    def _state_msg(self, state: BrainState) -> dict[str, Any]:
        """The whole BrainState, inlined. Integer paise, no image, no secret."""
        msg: dict[str, Any] = {"type": "state"}
        msg.update(state.to_dict())
        msg["type"] = "state"  # to_dict has no `type`; be explicit anyway
        return msg

    def _mudra_msg(self, gray: np.ndarray, *, seeded: bool) -> dict[str, Any]:
        if self.gesture is None:  # pragma: no cover - seeded on first frame
            return {
                "type": "mudra",
                "ok": False,
                "state": None,
                "solidity": None,
                "defects": None,
                "compactness": None,
                "area_mm2": None,
                "reason": A_NO_REFERENCE,
                "detail": "no empty-mat reference yet",
            }
        if seeded:
            # The seeding frame IS the reference. Measuring it against itself
            # would report a guaranteed, meaningless NONE. Abstain instead.
            return {
                "type": "mudra",
                "ok": False,
                "state": None,
                "solidity": None,
                "defects": None,
                "compactness": None,
                "area_mm2": None,
                "reason": A_NO_REFERENCE,
                "detail": "this frame became the reference; nothing to compare it to",
            }
        g = self.gesture.update(gray)
        return {
            "type": "mudra",
            "ok": True,
            "state": g.state,
            "raw_state": g.raw_state,
            "solidity": _round(g.solidity),
            "defects": int(g.defects),
            "compactness": _round(g.compactness),
            "area_mm2": _round(g.area_mm2, 1),
            "frames_held": int(g.frames_held),
            "border_touching": bool(g.border_touching),
            "decided": bool(g.decided),
            "reason": g.reason or "",
        }

    def _crop_roi(self, gray: np.ndarray) -> np.ndarray:
        x_mm, y_mm, w_mm, h_mm = self.roi_mm
        px = BUF_W / MAT_W_MM
        py = BUF_H / MAT_H_MM
        x0 = max(0, min(BUF_W - 1, int(round(x_mm * px))))
        y0 = max(0, min(BUF_H - 1, int(round(y_mm * py))))
        x1 = max(x0 + 1, min(BUF_W, int(round((x_mm + w_mm) * px))))
        y1 = max(y0 + 1, min(BUF_H, int(round((y_mm + h_mm) * py))))
        return np.ascontiguousarray(gray[y0:y1, x0:x1])

    def _push_burst(self, gray: np.ndarray) -> None:
        self._burst.append(self._crop_roi(gray))
        if len(self._burst) > self.burst_len:
            del self._burst[0 : len(self._burst) - self.burst_len]

    def _peel_msg(self, gray: np.ndarray) -> dict[str, Any]:
        name = self._peel_name
        if name is None:
            return {
                "type": "peel",
                "ok": False,
                "name": None,
                "ignited_fraction": None,
                "verdict": _peel.UNREGISTERABLE,
                "ecc_ok": False,
                "registered": False,
                "reason": _peel.R_NOT_ENROLLED,
                "detail": "no sticker has been enrolled on this counter",
            }
        v = self.registry.compare(name, self._crop_roi(gray))
        return self._peel_from_verdict(v)

    @staticmethod
    def _peel_from_verdict(v: _peel.StickerVerdict) -> dict[str, Any]:
        return {
            "type": "peel",
            # UNREGISTERABLE is PEEL's abstention. It is not a warning about
            # the sticker; it is "I could not read this well enough to say".
            "ok": v.verdict != _peel.UNREGISTERABLE,
            "name": v.name,
            "ignited_fraction": _round(v.ignited_fraction, 6),
            "verdict": v.verdict,
            "ecc_ok": bool(v.ecc_ok),
            "registered": bool(v.registered),
            "reason": v.reason,
            "ecc_cc": _round(v.ecc_cc, 6),
            "sharpness_ratio": _round(v.sharpness_ratio),
            "blind_fraction": _round(v.blind_fraction, 6),
            "valid_fraction": _round(v.valid_fraction, 6),
        }

    def _chilla_msg(
        self, gray: np.ndarray, state: BrainState, *, seeded: bool
    ) -> dict[str, Any]:
        base: dict[str, Any] = {
            "type": "chilla",
            "ok": False,
            "verdict": None,
            "amount_paise": None,
            "candidates": [],
            "reason": A_NO_SCREEN,
            # INVARIANT 2, stated on every single message: CHILLA is amber
            # even when it matches. It corroborates; it never settles.
            "light": "AMBER",
        }
        if seeded or not self.screens.has_reference:
            base["reason"] = A_NO_REFERENCE
            base["detail"] = "no empty-mat reference for the screen finder yet"
            return base

        det = self.screens.detect(gray)
        base["screen"] = det.as_dict()
        if not det.found:
            base["detail"] = f"no emissive rectangle on the mat: {det.reason}"
            base["reason"] = f"{A_NO_SCREEN}:{det.reason}"
            return base

        # The amount to corroborate is the MINTED INTENT, never the running
        # basket total: the customer's screen shows what they paid, and what
        # they were asked to pay is the intent. Before DONE there is no intent
        # and therefore nothing a screen could corroborate.
        amount = state.intent_amount_paise
        if amount is None:
            base["reason"] = A_NO_INTENT_AMOUNT
            base["detail"] = (
                "a screen is on the mat but nothing has been minted, so there "
                "is no amount to corroborate it against"
            )
            return base
        amount = int(_paise(int(amount)))

        # `screen_ts` is OUR clock at grab time. chilla.py is explicit that the
        # timestamp on the customer's screen is below Nyquist on this rig and
        # is never read.
        now = self.clock.now_iso()
        age = self.mirror.age_s(now)
        result = self.matcher.match(amount, now, mirror_age_s=age)
        out = dict(base)
        out.update(
            {
                "ok": True,
                "verdict": result.verdict,
                "amount_paise": int(result.amount_paise),
                "candidates": [c.as_dict() for c in result.candidates],
                "reason": result.reason,
                "light": result.light,
                "n_in_window": int(result.n_in_window),
                "window_seconds": int(result.window_seconds),
                "collision_risk": _round(result.collision_risk, 6),
                "mirror_rows": len(self.mirror),
                "mirror_age_s": None if not math.isfinite(age) else round(age, 3),
            }
        )
        if not math.isfinite(age):
            out["detail"] = (
                "the settlement mirror has never been refreshed, so every "
                "verdict here is AMBER_STALE by construction"
            )
        return out

    def _saaf_msg(self, result: Optional[_saaf.StackResult] = None) -> dict[str, Any]:
        if result is None:
            n = len(self._burst)
            return {
                "type": "saaf",
                "ok": False,
                "used": 0,
                "rejected": 0,
                "sharpness_gain": None,
                "warning": "",
                "reason": A_BURST_TOO_SHORT if n < self.burst_len else A_NEVER_RUN,
                "detail": (
                    f"{n} of {self.burst_len} burst frames collected; SAAF runs "
                    f"on enrolment, not on every frame"
                ),
                "burst": n,
                "burst_target": self.burst_len,
            }
        return {
            "type": "saaf",
            "ok": result.image is not None,
            "used": int(result.used),
            "rejected": int(result.rejected),
            "sharpness_gain": _round(result.sharpness_gain, 4),
            "warning": str(result.warning),
            "reason": A_STACK_REFUSED if result.image is None else "stacked",
            "detail": (
                "every frame in the burst was rejected; nothing was enrolled"
                if result.image is None
                else f"stacked {result.used} frames at scale {SAAF_SCALE}"
            ),
            "mean_shift_px": _round(result.mean_shift_px, 3),
            "subpixel_diversity": _round(result.subpixel_diversity),
            "burst": len(self._burst),
            "burst_target": self.burst_len,
        }

    def _ledger_msg(self, state: BrainState) -> dict[str, Any]:
        return {
            "type": "ledger",
            "ok": True,
            "head": state.ledger_head,
            "count": int(state.ledger_lines),
            "reason": "chained",
        }

    # -- the non-frame verbs ----------------------------------------------

    def _after_brain(self, state: BrainState) -> list[dict[str, Any]]:
        return [
            self._remember(self._state_msg(state)),
            self._remember(self._ledger_msg(state)),
        ]

    def _on_done(self, msg: Mapping[str, Any]) -> list[dict[str, Any]]:
        """DONE. Records an amount; cannot authorise it (INVARIANT 5).

        This calls ``Brain.done()``, which calls the injected SettlementPort.
        The port holds the webhook secret. This server does not, has never been
        given one, and could not mint if it wanted to.
        """
        try:
            state = self.brain.done()
        except Exception as exc:
            log.exception("done refused")
            return [self._refuse(R_BRAIN_REFUSED, f"{type(exc).__name__}: {exc}")]
        return self._after_brain(state)

    def _on_revert(self, msg: Mapping[str, Any]) -> list[dict[str, Any]]:
        item = msg.get("item_id", msg.get("itemId"))
        if not isinstance(item, str) or not item:
            return [
                self._refuse(
                    R_BAD_ARGUMENT, "revert needs a non-empty `item_id` string"
                )
            ]
        # Checked HERE and not left to the session, because Session.on_revert
        # answers an unknown id with a REFUSED transition whose reason goes to
        # the ledger and not to the wire: `Brain.revert` still returns a state,
        # so the bridge would hand the browser an unchanged board and the
        # shopkeeper would watch a tap do nothing with no explanation. Naming
        # the refusal is the whole job of this layer.
        known = {li.item_id for li in self.brain.state().lines}
        if item not in known:
            return [
                self._refuse(
                    R_UNKNOWN_ITEM,
                    f"no line {item!r} on this bill",
                    item_id=item,
                    known_items=sorted(known),
                )
            ]
        try:
            state = self.brain.revert(item)
        except Exception as exc:
            return [self._refuse(R_BRAIN_REFUSED, f"{type(exc).__name__}: {exc}")]
        return self._after_brain(state)

    def _on_refresh(self, msg: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Re-publish the board. The door for out-of-band brain changes.

        A webhook is delivered to the BRAIN, not to this socket, so a
        settlement is invisible here until something asks. See the protocol
        note in the module docstring.
        """
        return self._after_brain(self.brain.state())

    def _on_ack(self, msg: Mapping[str, Any]) -> list[dict[str, Any]]:
        try:
            state = self.brain.acknowledge()
        except Exception as exc:
            return [self._refuse(R_BRAIN_REFUSED, f"{type(exc).__name__}: {exc}")]
        return self._after_brain(state)

    def _on_enrol_sticker(self, msg: Mapping[str, Any]) -> list[dict[str, Any]]:
        """SAAF stacks the burst, PEEL enrols the stack. Both answer."""
        name = msg.get("name")
        if not isinstance(name, str) or not name.strip():
            return [
                self._refuse(
                    R_BAD_ARGUMENT, "enrol_sticker needs a non-empty `name` string"
                )
            ]
        if not self._burst:
            return [
                self._remember(self._saaf_msg()),
                self._remember(
                    {
                        "type": "peel",
                        "ok": False,
                        "name": name,
                        "ignited_fraction": None,
                        "verdict": _peel.UNREGISTERABLE,
                        "ecc_ok": False,
                        "registered": False,
                        "reason": A_BURST_TOO_SHORT,
                        "detail": "no frames have arrived, so there is nothing to enrol",
                    }
                ),
            ]

        stacker = _saaf.BurstStacker(scale=SAAF_SCALE)
        try:
            result = stacker.stack(list(self._burst))
        except _saaf.SaafError as exc:
            return [self._refuse(R_BRAIN_REFUSED, f"SaafError: {exc}")]
        out = [self._remember(self._saaf_msg(result))]

        image = result.image
        if image is None:
            out.append(
                self._remember(
                    {
                        "type": "peel",
                        "ok": False,
                        "name": name,
                        "ignited_fraction": None,
                        "verdict": _peel.UNREGISTERABLE,
                        "ecc_ok": False,
                        "registered": False,
                        "reason": A_STACK_REFUSED,
                        "detail": (
                            "SAAF rejected every frame in the burst, so there is "
                            "no image good enough to enrol"
                        ),
                    }
                )
            )
            return out

        try:
            record = self.registry.enrol(name, image, clock=self.clock)
        except _peel.StickerError as exc:
            # A refused enrolment is a GOOD outcome, not an error: a bad
            # reference is silent forever and every later check inherits it.
            #
            # The reason is A_ENROLMENT_REFUSED and NOT a borrowed
            # ident_sticker code. An earlier draft reported R_CROP_FEATURELESS
            # here whatever the cause, so a crop refused for being too SMALL
            # was reported as having no structure — two different faults with
            # two different fixes, collapsed into one wrong label. The registry
            # raises with the real cause; it goes in `detail` verbatim.
            out.append(
                self._remember(
                    {
                        "type": "peel",
                        "ok": False,
                        "name": name,
                        "ignited_fraction": None,
                        "verdict": _peel.UNREGISTERABLE,
                        "ecc_ok": False,
                        "registered": False,
                        "reason": A_ENROLMENT_REFUSED,
                        "detail": f"enrolment refused: {exc}",
                    }
                )
            )
            return out

        self._peel_name = name
        self.brain.ledger.append(
            ts=self.clock.now_iso(),
            module=MODULE,
            what="sticker_enrolled",
            name=record.name,
            digest=record.digest,
            shape=list(record.shape),
            saaf_used=int(result.used),
            saaf_rejected=int(result.rejected),
            human_override=True,
        )
        out.append(
            self._remember(
                {
                    "type": "peel",
                    "ok": True,
                    "name": record.name,
                    "ignited_fraction": None,
                    "verdict": _peel.GENUINE,
                    "ecc_ok": False,
                    "registered": True,
                    "reason": "ENROLLED",
                    "detail": (
                        f"enrolled from {result.used} stacked frames; "
                        "comparisons start on the next frame"
                    ),
                    "digest": record.digest,
                    "shape": list(record.shape),
                }
            )
        )
        return out

    def _on_select_panel(self, msg: Mapping[str, Any]) -> list[dict[str, Any]]:
        pid = msg.get("id")
        if not isinstance(pid, str) or pid not in PANELS:
            return [
                self._refuse(
                    R_UNKNOWN_PANEL, f"no panel {pid!r}", known=list(PANELS)
                )
            ]
        self._selected = pid
        out: list[dict[str, Any]] = [{"type": "panel", "id": pid, "known": list(PANELS)}]
        replay = self.last(pid)
        if replay is not None:
            out.append(replay)
        else:
            out.append(
                {
                    "type": pid if pid != "basket" else "state",
                    "ok": False,
                    "reason": A_NEVER_RUN,
                    "detail": f"the {pid} panel has not run yet; no frame has arrived",
                }
            )
        return out

    # -- what a client gets the instant it connects ------------------------

    def hello(self) -> list[dict[str, Any]]:
        """The opening burst: current state, then every panel's last word.

        A client that connects mid-sale is never blank, and every panel it
        draws is either a real measurement or a named abstention.
        """
        with self._lock:
            state = self.brain.state()
            out: list[dict[str, Any]] = [
                self._remember(self._state_msg(state)),
                self._remember(self._ledger_msg(state)),
            ]
            for panel in ("mudra", "peel", "chilla", "saaf"):
                known = self.last(panel)
                out.append(
                    known
                    if known is not None
                    else {
                        "type": panel,
                        "ok": False,
                        "reason": A_NEVER_RUN,
                        "detail": "no frame has reached this panel yet",
                    }
                )
        return [self.safe(self._stamp(m)) for m in out]

    def health(self) -> dict[str, Any]:
        with self._lock:
            state = self.brain.state()
            return {
                "ok": True,
                "module": MODULE,
                "session_id": state.session_id,
                "session_state": state.session_state,
                "frame_index": int(state.frame_index),
                "frames_accepted": int(self.frames_accepted),
                "refusals": dict(self.refusals),
                "leaks_blocked": int(self.leaks_blocked),
                "ledger_head": state.ledger_head,
                "rect_shape": [RECT_SHAPE[0], RECT_SHAPE[1]],
                "sim": self.sim is not None,
                "panels": list(PANELS),
                "selected_panel": self._selected,
                "sticker_enrolled": self._peel_name,
            }

    def close(self) -> None:
        self.brain.close()


# ==================================================================== the ASGI app


def create_app(server: BrainServer, *, keepalive_s: float = 25.0) -> Any:
    """Mount ``server`` as one WebSocket, two GETs, and the static ``web/``.

    The WebSocket is registered at BOTH ``/ws`` and ``/`` — see the module
    docstring. The static mount goes on last: a Starlette ``WebSocketRoute``
    only matches scope type ``"websocket"``, so an HTTP GET for ``/`` still
    falls through to ``index.html`` while ``ws://host/`` still upgrades.
    """
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect

    app = FastAPI(title="GAWAAH bridge", version="1")
    app.state.server = server

    @app.get("/health")
    def health() -> dict[str, Any]:
        return server.health()

    @app.get("/state")
    def state() -> dict[str, Any]:
        return server.safe(server.brain.state().to_dict())

    async def ws(socket) -> None:
        await socket.accept()
        for msg in server.hello():
            await socket.send_json(msg)
        pump: Optional[asyncio.Task] = None
        if server.sim is not None:
            pump = asyncio.ensure_future(_sim_pump(server, socket))
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(
                        socket.receive_text(), timeout=keepalive_s
                    )
                except (asyncio.TimeoutError, TimeoutError):
                    await socket.send_json({"type": "keepalive"})
                    continue
                for msg in server.handle(raw):
                    await socket.send_json(msg)
        except WebSocketDisconnect:
            pass
        finally:
            if pump is not None:
                pump.cancel()

    # PEP 563 turns inline annotations into strings that FastAPI resolves
    # against MODULE globals; `WebSocket` is imported inside this function, so
    # the string never resolves and every connect is closed 1008 "field
    # required". Bind the real class. (gawaah/brain.py carries the same note
    # and the same fix; this is the second place it bites.)
    ws.__annotations__["socket"] = WebSocket
    for path in ("/ws", "/"):
        app.websocket(path)(ws)

    if server.web_dir is not None and server.web_dir.is_dir():
        from starlette.staticfiles import StaticFiles

        app.mount(
            "/", StaticFiles(directory=str(server.web_dir), html=True), name="web"
        )
    return app


async def _sim_pump(server: BrainServer, socket: Any) -> None:
    """Drive synthetic frames into a connected client. ``--sim`` only."""
    sim = server.sim
    assert sim is not None
    try:
        while True:
            frame = sim.next_frame()
            for msg in server.handle(
                {"type": "frame", "rect": encode_rect(frame), "ts": server.clock.now_iso()}
            ):
                await socket.send_json(msg)
            for msg in sim.drain_commands():
                for reply in server.handle(msg):
                    await socket.send_json(reply)
            await asyncio.sleep(sim.period_s)
    except asyncio.CancelledError:  # pragma: no cover - shutdown path
        raise
    except Exception:  # pragma: no cover - the sim must never kill the socket
        log.exception("sim pump stopped")


def encode_rect(rect: np.ndarray) -> str:
    """Encode a rectified buffer the way a client is required to send it."""
    if tuple(rect.shape[:2]) != RECT_SHAPE:
        raise BridgeError(
            f"refusing to encode a {rect.shape} buffer as a rectified crop; "
            f"expected {RECT_SHAPE}"
        )
    ok, buf = cv2.imencode(".png", rect)
    if not ok:  # pragma: no cover - PNG encode of a valid u8 array
        raise BridgeError("PNG encode failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")


# ======================================================================== the sim


PAPER = 200  # what white A3 reads at under the demo's counter lamp


def _rect_takhti() -> np.ndarray:
    """The printed mat, rendered and resampled to the rectified buffer.

    Rendered at 4 px/mm and resampled rather than rendered at 2.828 px/mm,
    because ``render_takhti`` rounds the marker side to whole pixels and the
    rounding error at 2.828 px/mm is large enough to soften the ArUco cells.
    """
    sheet = render_takhti(4.0)
    rect = cv2.resize(sheet, (BUF_W, BUF_H), interpolation=cv2.INTER_AREA)
    return np.clip(
        np.rint(rect.astype(np.float64) * (PAPER / 255.0) + 9.0), 0, 255
    ).astype(np.uint8)


def _mm_box(x_mm: float, y_mm: float, w_mm: float, h_mm: float) -> tuple[int, int, int, int]:
    px = BUF_W / MAT_W_MM
    py = BUF_H / MAT_H_MM
    x0 = int(round((x_mm - w_mm / 2.0) * px))
    y0 = int(round((y_mm - h_mm / 2.0) * py))
    x1 = int(round((x_mm + w_mm / 2.0) * px))
    y1 = int(round((y_mm + h_mm / 2.0) * py))
    return (max(0, x0), max(0, y0), min(BUF_W, x1), min(BUF_H, y1))


def _texture(seed: int, h: int, w: int) -> np.ndarray:
    """A deterministic printed-wrapper texture in [20, 70] grey.

    Same range as tests/test_brain.py's harness and for the same measured
    reason: every interior pixel differs from the paper by >= 95 levels, which
    keeps a textured packet above the placement detector's 50 %-amplitude refit
    level so it segments as ONE blob instead of fragmenting into "components"
    and being refused as a merged contour.
    """
    rng = np.random.default_rng(seed)
    tile = rng.integers(20, 71, size=(4, 6)).astype(np.uint8)
    return cv2.resize(tile, (max(1, w), max(1, h)), interpolation=cv2.INTER_LINEAR)


class SimScript:
    """Synthetic rectified frames, so the six panels are filmable with no rig.

    Every frame this yields is a real 840x1188 rectified TAKHTI buffer that
    goes through ``decode_rect`` like any other. The sim replaces the CAMERA
    and the BROWSER's homography, not the pipeline: the brain, the placement
    detector, the tracker, the line zone, MUDRA, PEEL, CHILLA and SAAF all run
    for real on these pixels.

    The script, in order, and what each phase is for on screen:

      ``settle``  8 frames of the bare mat with the printed sticker on it.
                  Frame 0 becomes the reference for MUDRA and CHILLA and for
                  the placement detector. Panels show their abstentions.
      ``goods``   a textured packet appears at y=180 mm, SITS STILL long enough
                  to be called stable and identified, then walks past the sell
                  line at y=340 mm and holds. The basket fills; the total is
                  integer paise; the ledger head moves. The settle sub-phase is
                  not decoration — ``placement.py`` needs STABLE_FRAMES of a
                  motionless blob before it will report ``stable``, and
                  ``Brain._register`` only identifies a stable placement. A
                  packet that walks in from off-mat is never registered, and
                  the crossing then freezes the total instead of billing it.
                  That is the correct behaviour and it is what this sim showed
                  the first time it ran; the schedule was fixed, not the gate.
      ``screen``  the basket is closed, the link is paid on the simulated
                  gateway, its signed webhook is delivered to the brain, our
                  mirror refreshes from the gateway, and a phone-shaped
                  emissive rectangle appears on the mat. CHILLA finds it,
                  matches it, and still reports AMBER — which is invariant 2
                  on screen: the counter is PAID because a signature-verified
                  webhook said so, and CHILLA's MATCHED had nothing to do
                  with it.
      ``hand``    an open-palm occluder enters. MUDRA reads OPEN/FIST/GOODS/
                  AMBIGUOUS off solidity, defect count and compactness — no
                  model, the hand is an OCCLUDER of a known plane.
      ``tamper``  a patch of the printed sticker is REPLACED BY DIFFERENT
                  STRUCTURE. PEEL's ignited fraction crosses the 3 % gate and
                  the verdict turns TAMPERED. Two measured facts sit behind
                  that patch and both are in ``_paint_sticker``.

    Then it holds on the last phase rather than looping, because a loop would
    re-seed nothing and silently replay a sale that already settled.

    AN OBSERVED COLLISION, WRITTEN DOWN RATHER THAN TUNED AWAY
    ---------------------------------------------------------
    A phone laid on the billing mat is also an object on the billing mat. The
    placement detector sees it, cannot identify it, and after
    ``brain.REFUSE_AFTER_FRAMES`` correctly admits it as an AMBER line — which
    moves the session to AMBER and clears the intent, and CHILLA then has no
    amount to corroborate. The corroboration window in this sim is therefore
    about five frames wide, and the same is true of the hand: MUDRA reads it as
    a gesture while the placement detector reads it as an unidentifiable object
    and ambers it.

    That is a real property of putting the corroboration surface and the
    measurement surface on the same plane. The fix is a coupling that does not
    exist yet — ``Brain`` would have to suppress registration while MUDRA holds
    a hand state — and it belongs in ``brain.py``, which this module does not
    own. The phase order below puts the sale and CHILLA first so the demo shows
    the corroboration before the collision, and the collision is left visible
    afterwards instead of being hidden by a shorter script.
    """

    #: (name, frame count). Tuned so each phase is long enough for the dwell
    #: filters downstream: MUDRA needs 4 frames to commit, the line zone needs
    #: 3 to count a crossing, the placement detector needs 5 motionless frames
    #: to call a blob stable.
    PHASES: tuple[tuple[str, int], ...] = (
        ("settle", 8),
        ("goods", 30),
        ("screen", 12),
        ("hand", 12),
        ("tamper", 10),
    )

    STICKER_SEED = 7
    TAMPER_SEED = 31
    GOODS_SEED = 11
    GOODS_LONG_MM = 70.0
    GOODS_SHORT_MM = 42.0
    GOODS_X_MM = 148.0
    GOODS_Y0_MM = 180.0
    GOODS_Y1_MM = 352.0
    GOODS_SETTLE = 8      # motionless frames before the walk; >= STABLE_FRAMES
    GOODS_STEP_MM = 12.0  # per-frame travel; << the tracker's 25 mm max_dist

    def __init__(
        self,
        *,
        period_s: float = 0.1,
        roi_mm: tuple[float, float, float, float] = DEFAULT_STICKER_ROI_MM,
        enrol_at: Optional[int] = 9,
        sticker_name: str = "counter-upi",
        jitter_px: float = 0.4,
    ) -> None:
        self.period_s = float(period_s)
        self.roi_mm = tuple(float(v) for v in roi_mm)
        self.enrol_at = enrol_at
        self.sticker_name = sticker_name
        #: Sub-pixel camera shake, applied to the STICKER ROI only.
        #:
        #: Without it every burst frame samples the identical sub-pixel phase
        #: and SAAF correctly reports NO_SUBPIXEL_DIVERSITY — "this result is
        #: DENOISING ONLY, not super-resolution". That warning is true of a
        #: rigidly mounted camera over a static sticker and it is worth seeing,
        #: but a sim that can only ever produce it never exercises the path
        #: SAAF exists for. 0.4 px is real counter shake and is above SAAF's
        #: 0.15 px min_shift floor.
        #:
        #: ROI ONLY, deliberately: jittering the whole buffer would move the
        #: ArUco corners and the placement detector's reference, and every
        #: high-contrast edge on the mat would light up as a spurious blob.
        self.jitter_px = float(jitter_px)
        self._base = _rect_takhti()
        self._paint_sticker(self._base)
        self._i = 0
        self._queued: list[dict[str, Any]] = []
        #: Installed by ``build_sim_server``: pay the minted link on the
        #: simulated gateway, deliver its webhooks, refresh our mirror. Left
        #: None when there is no gateway to pay, in which case CHILLA correctly
        #: reports AMBER_STALE against an empty mirror.
        self.on_pay: Optional[Callable[[], None]] = None

    # -- the schedule ------------------------------------------------------

    @property
    def done_at(self) -> int:
        """Frame at which DONE is tapped: the last frame of the goods phase."""
        return self._phase_start("goods") + dict(self.PHASES)["goods"] - 1

    @property
    def pay_at(self) -> int:
        """Frame at which the customer pays: one frame after DONE, so the mint
        has been published to the client and the screen phase — which is only
        about five frames wide before the phone ambers itself, see the class
        docstring — starts with a live intent to corroborate."""
        return self.done_at + 1

    def _phase_start(self, want: str) -> int:
        n = 0
        for name, count in self.PHASES:
            if name == want:
                return n
            n += count
        raise BridgeError(f"no phase {want!r}")

    def goods_y_mm(self, k: int) -> float:
        """The packet's centre for index ``k`` within the goods phase."""
        if k < self.GOODS_SETTLE:
            return self.GOODS_Y0_MM
        y = self.GOODS_Y0_MM + (k - self.GOODS_SETTLE + 1) * self.GOODS_STEP_MM
        return min(self.GOODS_Y1_MM, y)

    # -- geometry ----------------------------------------------------------

    def _paint_sticker(self, buf: np.ndarray, tampered: bool = False) -> None:
        """A high-contrast printed square in the ROI, so PEEL has something."""
        x_mm, y_mm, w_mm, h_mm = self.roi_mm
        x0, y0, x1, y1 = _mm_box(x_mm + w_mm / 2.0, y_mm + h_mm / 2.0, w_mm, h_mm)
        h, w = y1 - y0, x1 - x0
        rng = np.random.default_rng(self.STICKER_SEED)
        # A blocky QR-ish pattern: high contrast, plenty of structure for ECC,
        # nothing that resembles a real UPI handle (there is nothing to read).
        cells = rng.integers(0, 2, size=(16, 16)).astype(np.uint8) * 210 + 20
        if tampered:
            # One sixteenth of the sticker REPLACED WITH DIFFERENT MODULES.
            #
            # Not blanked. ident_sticker._blind_mask deliberately writes off
            # regions where structure was DESTROYED, because that is glare or a
            # thumb, not a substitution — a substituted QR still has modules,
            # it has different ones. A flat patch here measured 0.0000 ignited.
            #
            # And INVERTED, not re-randomised. A fresh random 4x4 agrees with
            # the original on about half its cells by chance, so a 6.25 %-area
            # patch ignited only 0.0262 — under the 3 % gate, which would have
            # shipped a "tamper" phase that reads GENUINE. Inverting guarantees
            # every module in the patch differs. Both numbers were measured,
            # not guessed.
            th, tw = 4, 4
            r0, c0 = 16 // 3, 16 // 3
            block = cells[r0 : r0 + th, c0 : c0 + tw]
            cells = cells.copy()
            cells[r0 : r0 + th, c0 : c0 + tw] = 250 - block
        patch = cv2.resize(cells, (w, h), interpolation=cv2.INTER_NEAREST)
        buf[y0:y1, x0:x1] = patch

    def _jitter_sticker(self, buf: np.ndarray, i: int) -> None:
        """Shift the sticker ROI by a deterministic sub-pixel offset.

        The offsets walk an irrational-ratio spiral so the sampling PHASE keeps
        changing rather than cycling — SAAF measures diversity as the circular
        variance of that phase, and a repeating offset scores zero however far
        the crop moved.
        """
        if self.jitter_px <= 0.0:
            return
        x_mm, y_mm, w_mm, h_mm = self.roi_mm
        x0, y0, x1, y1 = _mm_box(x_mm + w_mm / 2.0, y_mm + h_mm / 2.0, w_mm, h_mm)
        # Pad the source so the shift pulls in real neighbours, not a border
        # replicate that would fabricate an edge SAAF then tries to register.
        pad = 4
        sy0, sy1 = max(0, y0 - pad), min(BUF_H, y1 + pad)
        sx0, sx1 = max(0, x0 - pad), min(BUF_W, x1 + pad)
        src = buf[sy0:sy1, sx0:sx1]
        dx = self.jitter_px * math.cos(i * 2.39996)  # golden angle, radians
        dy = self.jitter_px * math.sin(i * 1.61803)
        m = np.array([[1.0, 0.0, dx], [0.0, 1.0, dy]], dtype=np.float32)
        moved = cv2.warpAffine(
            src, m, (src.shape[1], src.shape[0]),
            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE,
        )
        buf[y0:y1, x0:x1] = moved[y0 - sy0 : y0 - sy0 + (y1 - y0),
                                  x0 - sx0 : x0 - sx0 + (x1 - x0)]

    def _paste_goods(self, buf: np.ndarray, y_mm: float) -> None:
        x0, y0, x1, y1 = _mm_box(
            self.GOODS_X_MM, y_mm, self.GOODS_SHORT_MM, self.GOODS_LONG_MM
        )
        if x1 <= x0 or y1 <= y0:  # pragma: no cover - the schedule stays on-mat
            return
        buf[y0:y1, x0:x1] = _texture(self.GOODS_SEED, y1 - y0, x1 - x0)

    @staticmethod
    def _paste_hand(buf: np.ndarray, spread_deg: float = 20.0) -> None:
        """An open palm as a silhouette: a palm disc plus five finger capsules.

        Drawn in millimetres so its AREA is a real measurement — MUDRA gates on
        HAND_AREA_MM2 = (4000, 22000) and this lands inside it. The finger gaps
        are what produce the deep convexity defects OPEN requires.
        """
        px = BUF_W / MAT_W_MM
        py = BUF_H / MAT_H_MM
        cx_mm, cy_mm = 150.0, 230.0
        cx, cy = int(cx_mm * px), int(cy_mm * py)
        ink = 45
        cv2.circle(buf, (cx, cy), int(38.0 * px), ink, -1)
        for k in range(5):
            ang = np.deg2rad(-90.0 + (k - 2) * spread_deg)
            length = 70.0 if k != 0 else 52.0  # thumb is shorter
            tx = int((cx_mm + length * np.cos(ang)) * px)
            ty = int((cy_mm + length * np.sin(ang)) * py)
            cv2.line(buf, (cx, cy), (tx, ty), ink, int(15.0 * px))
            cv2.circle(buf, (tx, ty), int(7.5 * px), ink, -1)

    @staticmethod
    def _paste_screen(buf: np.ndarray) -> None:
        """A phone-shaped emissive rectangle: 65 x 130 mm, aspect 2.0.

        Inside chilla's gates by construction — 8450 mm2 sits between
        MIN_AREA_MM2 (2500) and MAX_AREA_MM2 (26000), and aspect 2.0 between
        MIN_ASPECT (1.15) and MAX_ASPECT (3.20).
        """
        x0, y0, x1, y1 = _mm_box(150.0, 250.0, 65.0, 130.0)
        cv2.rectangle(buf, (x0, y0), (x1, y1), 252, -1)
        # A dim border so the contour is a clean quad rather than a blown blob.
        cv2.rectangle(buf, (x0, y0), (x1 - 1, y1 - 1), 220, 2)

    # -- the script --------------------------------------------------------

    def phase_at(self, i: int) -> tuple[str, int]:
        """(phase name, index within the phase) for absolute frame ``i``."""
        n = 0
        for name, count in self.PHASES:
            if i < n + count:
                return name, i - n
            n += count
        last, count = self.PHASES[-1]
        return last, count - 1

    @property
    def total_frames(self) -> int:
        return sum(c for _, c in self.PHASES)

    def frame(self, i: int) -> np.ndarray:
        """The rectified buffer for absolute frame ``i``. Pure and repeatable."""
        name, k = self.phase_at(i)
        buf = self._base.copy()
        if name == "tamper":
            self._paint_sticker(buf, tampered=True)
        self._jitter_sticker(buf, i)
        if name == "goods":
            self._paste_goods(buf, self.goods_y_mm(k))
        elif name == "hand":
            self._paste_hand(buf)
        elif name == "screen":
            self._paste_screen(buf)
        return buf

    def next_frame(self) -> np.ndarray:
        """The next frame in the script. After the script ends it repeats the
        final frame, so a demo left running keeps a live socket showing the
        finished board rather than going silent or replaying a settled sale."""
        i = self._i
        self._i += 1
        for msg in self.commands_at(i):
            self._queued.append(msg)
        return self.frame(min(i, self.total_frames - 1))

    def commands_at(self, i: int) -> list[dict[str, Any]]:
        """Client messages the script taps at frame ``i``, and the side effect
        that belongs to the CUSTOMER rather than to the counter.

        ``on_pay`` is not a client message and is deliberately not one: paying
        a link happens on the gateway, not in the shopkeeper's browser, and
        modelling it as a UI tap would put the one action that can turn a light
        green inside the UI. Invariant 2 is a shape, not a comment.
        """
        out: list[dict[str, Any]] = []
        if self.enrol_at is not None and i == self.enrol_at:
            out.append({"type": "enrol_sticker", "name": self.sticker_name})
        if i == self.done_at:
            out.append({"type": "done"})
        if i == self.pay_at and self.on_pay is not None:
            self.on_pay()
            # The webhook landed on the BRAIN, not on the socket. Without this
            # the browser would keep showing AWAITING_SETTLEMENT until the next
            # frame happened to arrive — a settled sale that the shopkeeper
            # cannot see is the same as one that did not settle.
            out.append({"type": "refresh"})
        return out

    def drain_commands(self) -> list[dict[str, Any]]:
        """Client messages the script wants injected after the current frame."""
        out, self._queued = self._queued, []
        return out

    def reset(self) -> None:
        self._i = 0
        self._queued = []


# =========================================================== wiring a whole rig


def build_sim_server(
    workdir: Optional[Path] = None,
    *,
    clock: Optional[Clock] = None,
    web_dir: Optional[Path] = None,
    sim: Optional[SimScript] = None,
    with_sim: bool = True,
) -> BrainServer:
    """One fully wired counter with no camera and no network.

    Everything downstream of the frame is the real module: the real placement
    detector, the real tracker, the real line zone, the real identifier, the
    real session, the real sqlite kernel, the real Razorpay simulator with real
    HMAC-SHA256 signatures, and the real green predicate. What is simulated is
    the CAMERA and the GATEWAY, which are the two things a laptop does not have.

    INVARIANT 5 IS VISIBLE HERE: the webhook secret is passed to
    ``LocalSettlement`` — the port — and then registered with the server as a
    FORBIDDEN STRING. The server does not hold it; it holds a promise never to
    say it. If a future change starts echoing settlement config to the browser,
    ``scrub()`` drops the message and the test that greps every message for the
    secret fails.
    """
    from gawaah import kernel as _kernel
    from gawaah.identity import Gallery, Identifier
    from gawaah.ledger import Ledger
    from gawaah.placement import PlacementDetector
    from gawaah.rzp_sim import RazorpaySim
    from gawaah.sellevent import CentroidTracker, LineZone

    work = Path(workdir) if workdir is not None else Path(
        tempfile.mkdtemp(prefix="gawaah-sim-")
    )
    work.mkdir(parents=True, exist_ok=True)
    clock = clock or VirtualClock("2026-08-29T09:00:00.000+00:00", step_ms=100)
    script = sim if sim is not None else (SimScript() if with_sim else None)

    ledger = Ledger(work / "kaala_dabba.jsonl")
    kern = _kernel.Kernel(str(work / "kernel.db"), clock, ledger)
    secret = "whsec_gawaah_sim_bridge_0000"
    gateway = RazorpaySim(secret, clock, seed=0, ledger=ledger)
    settlement = LocalSettlement(kern, gateway, clock, ledger, secret)

    reference = (script or SimScript()).frame(0)
    plane = ClientRectifiedPlane()

    def embed(crop: np.ndarray) -> np.ndarray:
        """An 8x8 mean-subtracted thumbnail. Deliberately NOT a neural net —
        invariant 3 says zero model weights, and identity.py says the embedder
        is injected. 64 floats is enough to show the seam works."""
        small = cv2.resize(crop, (8, 8), interpolation=cv2.INTER_AREA)
        v = small.astype(np.float64).ravel()
        v = v - v.mean()
        n = float(np.linalg.norm(v))
        return np.ones(64, np.float64) / 8.0 if n == 0.0 else v / n

    gallery = Gallery()
    prices: dict[str, int] = {}
    if script is not None:
        # Enrol the sim's packet from a frame it is NOT sold in, so no later
        # identification is a self-match.
        enrol_buf = script.frame(0).copy()
        script._paste_goods(enrol_buf, 150.0)
        det = PlacementDetector(reference)
        found = ()
        for _ in range(6):
            found = det.update(enrol_buf)
        stable = [p for p in found if p.measurable and p.long_edge_mm]
        if stable:
            p = stable[0]
            gallery.enroll("PACKET", [embed(Brain._crop(enrol_buf, p))],
                           float(p.long_edge_mm))
            prices["PACKET"] = int(_paise(2850))

    brain = Brain(
        BrainConfig(
            clock=clock,
            ledger=ledger,
            settlement=settlement,
            plane=plane,
            tracker=CentroidTracker(max_dist_mm=25.0, max_missing_frames=3),
            line=LineZone.mat_exit_line(80.0, min_crossing_frames=3),
            identifier=Identifier(gallery, embed),
            prices=prices,
            detector=PlacementDetector(reference, clock=clock),
            reference=reference,
        )
    )
    server = BrainServer(
        brain,
        web_dir=web_dir,
        sticker_dir=work / "stickers",
        clock=clock,
        plane=plane,
        sim=script,
        forbidden_strings=(secret,),
    )

    if script is not None:
        def pay_and_refresh() -> None:
            """The CUSTOMER's half of the sale, simulated.

            Three separate things happen here and they are separate on purpose:

              1. the link is paid on the gateway — a real HMAC-SHA256 signed
                 delivery comes back, not a flag;
              2. every delivery is handed to ``Brain.on_webhook``, which runs
                 the real four-part green predicate. This is the ONLY thing in
                 this whole file that can move the session to PAID;
              3. our settlement mirror is refreshed from the gateway's captured
                 payments, which is what CHILLA matches against. Refreshing the
                 mirror does not and cannot settle anything — it just means
                 CHILLA now has something to corroborate with, and its answer
                 is still AMBER.
            """
            state = brain.state()
            if state.nonce is None:
                log.info("sim: nothing minted, nothing to pay")
                return
            link = settlement.link_for(state.nonce)
            if not link:  # pragma: no cover - mint always records a link
                return
            # ONE webhook — `payment_link.paid`, the event the green state
            # depends on. `emit_captured=True` also sends `payment.captured`,
            # whose delivery is correctly REFUSED as `unknown_session` once the
            # intent has settled; that replay refusal is real and is covered in
            # tests/test_brain.py, but in a demo it lands on the exceptions
            # panel looking like a fault, so the sim does not stage it.
            result = gateway.pay_link(link["id"], emit_captured=False)
            for delivery in result.deliveries:
                brain.on_webhook(
                    delivery.body,
                    delivery.signature,
                    header_event_id=delivery.headers.get("X-Razorpay-Event-Id"),
                )
            # The same call a real mirror refresh makes, against the same
            # collection shape. No status filtering here on purpose:
            # LedgerMatcher.matchable_statuses is ("captured",) and doing it
            # twice is how the two definitions drift apart.
            server.set_mirror(
                _chilla.Mirror.from_razorpay_collection(
                    gateway.fetch_payments(), fetched_at=clock.now_iso()
                )
            )

        script.on_pay = pay_and_refresh

    return server


# ============================================================================ CLI


def _dry_run(server: BrainServer, *, frames: int, verbose: bool) -> int:
    """Drive the real ASGI app in-process and print what the browser gets.

    Not a substitute for a socket — it IS the socket's code path, minus the
    socket. Exists because uvicorn in this venv cannot speak WebSocket (see the
    module docstring), and a `--sim` that could not be demonstrated at all
    would be a feature nobody can check.
    """
    from fastapi.testclient import TestClient

    sim = server.sim
    if sim is None:
        print("--dry-run needs --sim", file=sys.stderr)
        return 2
    # Drive the frames explicitly rather than letting the async pump race the
    # client; the pump and this loop call the SAME `server.handle`.
    server.sim = None
    app = create_app(server)
    counts: dict[str, int] = {}
    try:
        with TestClient(app) as client:
            with client.websocket_connect("/ws") as ws:
                for _ in range(len(server.hello())):
                    m = ws.receive_json()
                    counts[m["type"]] = counts.get(m["type"], 0) + 1
                for i in range(frames):
                    phase, _k = sim.phase_at(i)
                    msgs = server.handle(
                        {
                            "type": "frame",
                            "rect": encode_rect(sim.frame(i)),
                            "ts": server.clock.now_iso(),
                        }
                    )
                    for cmd in sim.commands_at(i):
                        msgs += server.handle(cmd)
                    for m in msgs:
                        counts[m["type"]] = counts.get(m["type"], 0) + 1
                        if verbose:
                            print(f"[{i:3d} {phase:7s}] {json.dumps(m)[:220]}")
                    if not verbose:
                        print(_one_line(i, phase, msgs))
    finally:
        server.sim = sim
    print("\nmessage counts:", json.dumps(counts, sort_keys=True))
    print("health:", json.dumps(server.health(), sort_keys=True))
    return 0


def _one_line(i: int, phase: str, msgs: list[dict[str, Any]]) -> str:
    by = {m["type"]: m for m in msgs}
    st = by.get("state", {})
    mu = by.get("mudra", {})
    pe = by.get("peel", {})
    ch = by.get("chilla", {})
    return (
        f"[{i:3d} {phase:7s}] "
        f"{str(st.get('session_state', '-')):<19.19} "
        f"total={str(st.get('total_paise', '-')):>6} "
        f"lines={len(st.get('lines', []))} "
        f"| mudra={mu.get('state') or mu.get('reason', '-'):<12.12} "
        f"| peel={pe.get('verdict') or '-':<15.15}{'' if pe.get('ok') else '*'} "
        f"| chilla={(ch.get('verdict') or ch.get('reason', '-')):<24.24}"
        f"{ch.get('light', ''):<6.6} "
        f"| head={st.get('ledger_head', '')[:8]}"
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gawaah.brain_server",
        description="GAWAAH bridge: one process, one WebSocket, the whole PWA.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--sim",
        action="store_true",
        help="drive synthetic rectified frames; no camera required",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run the sim through the in-process ASGI transport and print every "
        "message instead of opening a socket",
    )
    parser.add_argument("--frames", type=int, default=0, help="--dry-run frame count")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--web",
        default=str(Path(__file__).resolve().parent.parent / "web"),
        help="static directory to serve (default: the repo's web/)",
    )
    parser.add_argument("--work", default=None, help="where to put ledger/db/stickers")
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    web = Path(args.web)
    if not web.is_dir():
        print(f"no static directory at {web}", file=sys.stderr)
        return 2

    server = build_sim_server(
        Path(args.work) if args.work else None, web_dir=web, with_sim=args.sim
    )
    if args.dry_run:
        n = args.frames or (server.sim.total_frames if server.sim else 0)
        return _dry_run(server, frames=n, verbose=args.verbose)

    import importlib.util

    if not any(
        importlib.util.find_spec(m) is not None for m in ("websockets", "wsproto")
    ):
        print(
            "uvicorn cannot serve WebSockets without `websockets` or `wsproto`, "
            "and neither is installed in this environment.\n"
            "  fix:  pip install websockets\n"
            "  now:  python -m gawaah.brain_server --sim --dry-run\n"
            "        (identical ASGI app, in-process transport, every message "
            "printed)",
            file=sys.stderr,
        )
        return 2

    # pragma: no cover below - a blocking server cannot be a unit test. The
    # ASGI app it is handed is the same object every test in
    # tests/test_brain_server.py drives through the in-process transport.
    import uvicorn  # pragma: no cover

    uvicorn.run(  # pragma: no cover
        create_app(server), host=args.host, port=args.port, log_level="info"
    )
    return 0  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
