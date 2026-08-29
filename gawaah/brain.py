"""S5 — the BRAIN. The process that owns the pipeline on the laptop.

Seventeen modules existed as islands. This is the one that wires them into a
counter:

    frame
      -> PlaneEngine.detect          (is the mat there, and where?)
      -> PlaneEngine.rectify         (the ONLY buffer that survives — inv. 4)
      -> PlacementDetector.update    (oriented millimetres on the plane)
      -> CentroidTracker.update      (a stable id per physical object)
      -> LineZone.update             (did it cross the sell edge, outward?)
      -> Identifier.identify         (which SKU, or an honest abstention)
      -> Session                     (what state, and what is the total)
      -> SettlementPort.mint         (Kernel intent + gateway payment link)
      -> SettlementPort.adjudicate   (GreenPredicate over the raw bytes)
      -> Session.on_webhook          (the one door to PAID)

Design rules this module is built to, each of which has a test in
tests/test_brain.py:

  B1  EVERY collaborator is injected through `BrainConfig`. There is no
      `import cv2`-and-open-a-camera anywhere in here; `ingest_frame` takes a
      frame array. That is what makes a whole sale testable without hardware.

  B2  Time comes from an injected `Clock`. With a `VirtualClock` the perception
      half of a run is BYTE-reproducible: two runs write two identical ledger
      files. (The money half carries one deliberate exception, the kernel's
      128-bit CSPRNG gateway nonce. See tests/test_brain.py::
      test_virtual_clock_run_is_byte_reproducible for exactly what is and is
      not identical, and why.)

  B3  ONE `Ledger` threads through session, kernel, gateway and predicate, so
      `ledger.verify()` is a single statement about the whole counter. Every
      scenario test ends by asserting it.

  B4  AMBER never reaches the total. The brain does not compute a total at all
      — it reads `Session.total_paise`, which recomputes from committed,
      non-amber lines on every read. An abstention is a line on the mat and a
      row in `BrainState.amber_items`, and it is not money.

  B5  The ONLY path to PAID is a signature-verified webhook that
      `GreenPredicate` greened. `Brain` has no webhook secret — not as an
      attribute, not as a constructor argument. It holds a `SettlementPort`,
      and the port holds the secret (INVARIANT 5: paisa is the sole secret
      holder). `LocalSettlement` is the in-process implementation used by the
      demo and the tests; a deployment swaps in an HTTP client to paisa and the
      brain does not change.

  B6  NO FORGERY PRIMITIVES (INVARIANT 6). Nothing here constructs, mutates or
      regenerates a payment payload. `_payment_id_from_verified_body` READS an
      id out of bytes whose HMAC has already been verified, and that is the
      full extent of this module's contact with a gateway document.

  B7  Abstain rather than guess (INVARIANT 7). A refused placement
      (TOUCHES_BORDER / MERGED_CONTOUR), a refused identification, a refused
      re-identification and a crossing with no tracker id all surface as
      `BrainState.exceptions` and, where goods actually left the counter, as an
      AMBER line or a frozen total — never as a silent count.

      The freeze rule is stated once, here, because getting it subtly wrong is
      invisible: goods walk out, the ledger logs it, and the total keeps
      counting as if nothing happened. Goods left the counter uncounted, and so
      the total freezes, in exactly two situations:

        * an UNNAMED centroid sits past the sell line for as many consecutive
          frames as `LineZone` needs to COUNT a crossing. The discriminator is
          `CrossingException.track_id is None`, never a list of reason codes —
          sellevent reports the specific abstention that produced the anonymity
          (`crossed_without_tracker_id`, `reidentification_ambiguous`,
          `reidentification_gap_exceeded`, and whatever it adds next), and
          matching on one of them let the others walk out. The frame count is
          the same one a count needs because it must take exactly as much
          evidence to freeze a total as to add to it.

        * a NAMED track vanishes mid-crossing (`detected_but_never_counted`)
          while its line is still uncommitted. `LineZone._retire` fires that
          only for a track last seen on the far side of the line and counted as
          being on the near side, which is an under-count with a name on it.

      It does NOT freeze when the vanished track's line was already committed:
      the money is on the bill, and un-billing on an occlusion is a worse bug
      than the one it fixes. Tap-to-revert is the instrument there.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
It does not decide green, it does not hold a secret, and it does not add up
money. Those three live in `webhook`, in the settlement port and in `session`
respectively. The brain's whole job is ORDER: calling the right module with the
right argument at the right moment, and writing down what happened.
"""
from __future__ import annotations

import asyncio
import json
import math
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, Sequence

import numpy as np

from . import kernel as _kernel
from .clock import Clock
from .identity import Identification, Identifier
from .ledger import Ledger
from .money import MoneyError
from .money import paise as make_paise
from .placement import Placement as DetectedPlacement
from .placement import PlacementDetector
from .sellevent import (
    REASON_NEVER_COUNTED,
    CentroidTracker,
    CrossingException,
    CrossingResult,
    LineZone,
    TrackerUpdate,
)
from .session import Placement as SessionPlacement
from .session import Session, State, Verdict
from .takhti import BUF_H, BUF_W, PX_PER_MM_X, PX_PER_MM_Y, MatLock
from .webhook import GreenPredicate, GreenVerdict
from .webhook import Intent as WebhookIntent

__all__ = [
    "MODULE",
    "BrainError",
    "MintResult",
    "SettlementResult",
    "SettlementPort",
    "LocalSettlement",
    "MatLockView",
    "PlacementView",
    "BasketLine",
    "BrainException",
    "BrainState",
    "BrainConfig",
    "Brain",
    "create_app",
    "serve",
    "DEFAULT_PORT",
    "EVENT_UNCOUNTED",
]

MODULE = "brain"

#: The PWA's port. 8787 is what web/app.js dials.
DEFAULT_PORT = 8787

#: Kernel states in which an intent is still awaiting money. The green
#: predicate's vocabulary is OPEN; translating here means a SETTLED intent is
#: simply invisible to the predicate, so a replayed webhook for a paid session
#: cannot re-green it.
OPEN_STATES = frozenset({_kernel.NEW, _kernel.CALLING})

#: Consecutive frames a REFUSED placement must persist before it is admitted to
#: the basket as an amber line. A refusal that lasts one frame is a segmentation
#: blink; one that lasts five is a shopkeeper who really did lay two packets
#: touching. Matches placement.STABLE_FRAMES so the two gates agree.
REFUSE_AFTER_FRAMES = 5

#: Reason codes this module puts on a line or an exception.
REASON_UNKNOWN_SKU = "unknown_sku"
REASON_NO_PRICE = "no_price_for_sku"
REASON_PLACEMENT_REFUSED = "placement_refused"
REASON_MAT_LOST = "mat_lost"
REASON_MINT_FAILED = "mint_failed"
REASON_WEBHOOK_REFUSED = "webhook_refused"

#: Ledger event name for "goods left the counter and the total froze for it".
#: One name for the two ways that happens — an unnamed centroid past the line,
#: and a named track that vanished mid-crossing — so a reconciler greps once.
#: There is deliberately no local copy of sellevent's REASON_* strings here: a
#: second definition of a reason code is how the two drift apart.
EVENT_UNCOUNTED = "uncounted_crossing"


class BrainError(RuntimeError):
    """Programmer error at the brain's boundary. Never a bad frame."""


# --------------------------------------------------------------- settlement


@dataclass(frozen=True)
class MintResult:
    """What asking for money produced. `minted` False is never a retry signal —
    an indeterminate gateway call is parked by the kernel, not re-attempted."""

    minted: bool
    reason: str
    nonce: Optional[str] = None
    short_url: Optional[str] = None
    payment_link_id: Optional[str] = None
    amount_paise: Optional[int] = None
    detail: str = ""
    replayed: bool = False


@dataclass(frozen=True)
class SettlementResult:
    """One adjudicated webhook delivery, plus what the kernel did about it."""

    verdict: GreenVerdict
    settled_nonce: Optional[str] = None
    payment_id: Optional[str] = None


class SettlementPort(Protocol):
    """The money boundary, as the brain sees it.

    Deliberately two methods and no secret in the signature. The brain hands
    over an amount and, later, the raw bytes of a webhook; everything that needs
    a key happens on the other side of this interface. In the demo that is
    `LocalSettlement` in this process; in a deployment it is an HTTP client to
    the paisa service, and `Brain` is unchanged.
    """

    def mint(self, session_id: str, amount_paise: int) -> MintResult: ...

    def adjudicate(
        self,
        raw_body: bytes,
        signature: str,
        *,
        header_event_id: Optional[str] = None,
    ) -> SettlementResult: ...


def _payment_id_from_verified_body(raw: bytes) -> Optional[str]:
    """Read `payload.payment.entity.id` out of an ALREADY-VERIFIED body.

    Called only inside the green branch, i.e. only after
    `GreenPredicate.evaluate` verified the HMAC over these exact bytes. It
    parses; it never constructs. INVARIANT 6 is about not being able to MAKE a
    payload, and reading an id out of one the gateway signed is the opposite of
    that.
    """
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    payload = doc.get("payload")
    if not isinstance(payload, dict):
        return None
    for key in ("payment", "payment_link"):
        node = payload.get(key)
        if isinstance(node, dict):
            entity = node.get("entity")
            if isinstance(entity, dict):
                pid = entity.get("id")
                if isinstance(pid, str) and pid:
                    return pid
    return None


class LocalSettlement:
    """In-process `SettlementPort`: kernel + gateway + green predicate.

    The webhook secret is stored under a name-mangled attribute and is never
    returned, logged or exposed on a property. That is not security against
    someone with the process — it is a structural statement that the BRAIN does
    not have the secret, so a brain bug cannot leak it and a brain change cannot
    start using it.
    """

    def __init__(
        self,
        kernel: _kernel.Kernel,
        gateway: Any,
        clock: Clock,
        ledger: Ledger,
        webhook_secret: str,
        *,
        seen: Optional[Any] = None,
        description: str = "GAWAAH counter",
    ) -> None:
        if not isinstance(webhook_secret, str) or not webhook_secret:
            raise BrainError(
                "webhook_secret must be a non-empty string; an empty secret "
                "makes every signature forgeable"
            )
        self.kernel = kernel
        self.gateway = gateway
        self.clock = clock
        self.ledger = ledger
        self.description = description
        self.__secret = webhook_secret
        self.predicate = GreenPredicate(
            self._open_intent_for_webhook, seen=seen, ledger=ledger, clock=clock
        )
        self._links: dict[str, Mapping[str, Any]] = {}

    def __repr__(self) -> str:  # never leak the secret into a traceback
        return (
            f"LocalSettlement(links={len(self._links)}, "
            f"webhook_secret=<{len(self.__secret)} chars redacted>)"
        )

    # -- kernel adapters -------------------------------------------------

    def _open_kernel_intent(self, session_id: str) -> Optional[_kernel.Intent]:
        for it in self.kernel.all_intents():
            if it.session_id == session_id and it.state in OPEN_STATES:
                return it
        return None

    def _open_intent_for_webhook(self, session_id: str) -> Optional[WebhookIntent]:
        it = self._open_kernel_intent(session_id)
        if it is None:
            return None
        return WebhookIntent(
            session_id=it.session_id, amount_paise=int(it.amount_paise), state="OPEN"
        )

    # -- mint ------------------------------------------------------------

    def mint(self, session_id: str, amount_paise: int) -> MintResult:
        try:
            amount = int(make_paise(amount_paise))
        except MoneyError as exc:
            return MintResult(False, "bad_amount", detail=str(exc))
        if amount <= 0:
            return MintResult(
                False,
                "bad_amount",
                amount_paise=amount,
                detail=f"a debit must be positive, got {amount} paise",
            )

        intent = self.kernel.create_intent(session_id, amount)
        if intent.state == _kernel.NEW:
            # Commit CALLING, close the DB, THEN call out. The kernel's rule.
            intent = self.kernel.mark_calling(intent.nonce)

        link = self._links.get(intent.nonce)
        replayed = link is not None
        if link is None:
            notes = {
                "session_id": session_id,
                "nonce": intent.nonce,
                "cycle": str(intent.cycle),
                "gawaah": "v1",
            }
            try:
                link = self.gateway.create_payment_link(
                    amount_paise=amount,
                    notes=notes,
                    reference_id=intent.nonce,
                    description=self.description,
                    idempotent=True,
                )
            except Exception as exc:
                # An indeterminate call is NEVER a failure. Park it for the
                # kernel's retrieve sweep rather than blind-retrying a debit.
                self.kernel.mark_indeterminate(
                    intent.nonce, reason=type(exc).__name__
                )
                return MintResult(
                    False,
                    "gateway_error",
                    nonce=intent.nonce,
                    amount_paise=amount,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            if not isinstance(link, Mapping):
                return MintResult(
                    False,
                    "gateway_error",
                    nonce=intent.nonce,
                    amount_paise=amount,
                    detail=f"gateway returned {type(link).__name__}, not a document",
                )
            self._links[intent.nonce] = link

        return MintResult(
            True,
            "minted",
            nonce=intent.nonce,
            short_url=link.get("short_url"),
            payment_link_id=link.get("id"),
            amount_paise=amount,
            replayed=replayed,
        )

    def link_for(self, nonce: str) -> Optional[Mapping[str, Any]]:
        return self._links.get(nonce)

    # -- adjudicate ------------------------------------------------------

    def adjudicate(
        self,
        raw_body: bytes,
        signature: str,
        *,
        header_event_id: Optional[str] = None,
    ) -> SettlementResult:
        """Verify, then settle. The four-part predicate runs FIRST and alone."""
        verdict = self.predicate.evaluate(
            raw_body,
            signature,
            self.__secret,
            header_event_id=header_event_id,
        )
        nonce: Optional[str] = None
        payment_id: Optional[str] = None
        if verdict.green and verdict.session_id:
            payment_id = _payment_id_from_verified_body(raw_body) or verdict.event_id
            it = self._open_kernel_intent(verdict.session_id)
            if it is not None and payment_id:
                try:
                    nonce = self.kernel.mark_settled(it.nonce, payment_id).nonce
                except _kernel.KernelError as exc:
                    self.kernel.audit_append(
                        MODULE,
                        event="settle_refused",
                        session_id=verdict.session_id,
                        nonce=it.nonce,
                        error=type(exc).__name__,
                        detail=str(exc),
                    )
        return SettlementResult(verdict, nonce, payment_id)

    def close(self) -> None:
        self.kernel.close()


# ------------------------------------------------------------------- views


def _r(value: Optional[float], places: int = 3) -> Optional[float]:
    """Round for the wire. None survives as None — there is no sentinel."""
    if value is None:
        return None
    v = float(value)
    if not math.isfinite(v):
        return None
    return round(v, places)


@dataclass(frozen=True)
class MatLockView:
    locked: bool
    reason: str
    ids_found: tuple[int, ...] = ()
    scale_err: Optional[float] = None
    persp_index: Optional[float] = None
    reproj_rmse_px: Optional[float] = None

    @classmethod
    def of(cls, lock: MatLock) -> "MatLockView":
        return cls(
            locked=bool(lock.locked),
            reason=str(lock.reason),
            ids_found=tuple(int(i) for i in lock.ids_found),
            scale_err=_r(lock.scale_err, 6),
            persp_index=_r(lock.persp_index, 6),
            reproj_rmse_px=_r(lock.reproj_rmse_px, 4),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "locked": self.locked,
            "reason": self.reason,
            "ids_found": list(self.ids_found),
            "scale_err": self.scale_err,
            "persp_index": self.persp_index,
            "reproj_rmse_px": self.reproj_rmse_px,
        }


@dataclass(frozen=True)
class PlacementView:
    """One object on the plane, as the PWA needs to draw it."""

    item_id: Optional[str]
    detector_id: int
    centre_mm: tuple[float, float]
    long_edge_mm: Optional[float]
    short_edge_mm: Optional[float]
    area_mm2: Optional[float]
    angle_deg: Optional[float]
    stable: bool
    measurable: bool
    reason: str
    frames_seen: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "detector_id": self.detector_id,
            "centre_mm": [self.centre_mm[0], self.centre_mm[1]],
            "long_edge_mm": self.long_edge_mm,
            "short_edge_mm": self.short_edge_mm,
            "area_mm2": self.area_mm2,
            "angle_deg": self.angle_deg,
            "stable": self.stable,
            "measurable": self.measurable,
            "reason": self.reason,
            "frames_seen": self.frames_seen,
        }


@dataclass(frozen=True)
class BasketLine:
    """One line of the bill. `price_paise` is None exactly when the line is
    AMBER, and an amber line is excluded from `BrainState.total_paise`."""

    item_id: str
    sku_id: Optional[str]
    name: Optional[str]
    price_paise: Optional[int]
    amber: bool
    committed: bool
    reverted: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "sku_id": self.sku_id,
            "name": self.name,
            "price_paise": self.price_paise,
            "amber": self.amber,
            "committed": self.committed,
            "reverted": self.reverted,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class BrainException:
    """Something the pipeline refused to guess about, with a place and a time."""

    code: str
    detail: str
    frame_index: int
    item_id: Optional[str] = None
    x_mm: Optional[float] = None
    y_mm: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "frame_index": self.frame_index,
            "item_id": self.item_id,
            "x_mm": self.x_mm,
            "y_mm": self.y_mm,
        }


@dataclass(frozen=True)
class BrainState:
    """Everything the counter knows, in one JSON-serialisable object.

    This is what goes down the WebSocket. Note what is NOT in it: no image, no
    crop, no homography, no secret, no gateway document. A frame is looked at
    and dropped (INVARIANT 4); what survives is millimetres and paise.
    """

    frame_index: int
    ts: str
    session_id: str
    session_state: str
    mat_lock: MatLockView
    placements: tuple[PlacementView, ...]
    lines: tuple[BasketLine, ...]
    total_paise: int
    amber_items: tuple[BasketLine, ...]
    exceptions: tuple[BrainException, ...]
    ledger_head: str
    ledger_lines: int
    net_crossings: int
    crossings_amber: bool
    frozen: bool
    online: bool
    money_authorised: bool
    intent_amount_paise: Optional[int] = None
    nonce: Optional[str] = None
    short_url: Optional[str] = None
    settled_payment_id: Optional[str] = None
    last_webhook_reason: Optional[str] = None

    @property
    def amber_count(self) -> int:
        return len(self.amber_items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_index": self.frame_index,
            "ts": self.ts,
            "session_id": self.session_id,
            "session_state": self.session_state,
            "mat_lock": self.mat_lock.to_dict(),
            "placements": [p.to_dict() for p in self.placements],
            "lines": [li.to_dict() for li in self.lines],
            "total_paise": self.total_paise,
            "amber_items": [li.to_dict() for li in self.amber_items],
            "amber_count": self.amber_count,
            "exceptions": [e.to_dict() for e in self.exceptions],
            "ledger_head": self.ledger_head,
            "ledger_lines": self.ledger_lines,
            "net_crossings": self.net_crossings,
            "crossings_amber": self.crossings_amber,
            "frozen": self.frozen,
            "online": self.online,
            "money_authorised": self.money_authorised,
            "intent_amount_paise": self.intent_amount_paise,
            "nonce": self.nonce,
            "short_url": self.short_url,
            "settled_payment_id": self.settled_payment_id,
            "last_webhook_reason": self.last_webhook_reason,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


# ------------------------------------------------------------------ config


@dataclass
class BrainConfig:
    """Every collaborator, injected. Nothing here is constructed by default
    except the `Session`, which is only ever `clock` + `ledger` anyway.

    `reference` is the rectified EMPTY-mat buffer. Supply it (the honest thing:
    you photographed an empty mat) or leave `detector` None and the brain seeds
    the reference from the first locked frame, writing a ledger line that says
    so — an auto-seeded reference that had goods on it would make those goods
    invisible for the rest of the session, which is why it is announced rather
    than silent.
    """

    clock: Clock
    ledger: Ledger
    settlement: SettlementPort
    plane: Any
    tracker: CentroidTracker
    line: LineZone
    identifier: Identifier
    prices: Mapping[str, int] = field(default_factory=dict)
    detector: Any = None
    reference: Optional[np.ndarray] = None
    session: Optional[Session] = None
    refuse_after_frames: int = REFUSE_AFTER_FRAMES
    max_exceptions: int = 256
    #: Consecutive frames an UNNAMED centroid must sit past the sell line before
    #: the total freezes. `None` means "however many the line zone needs to
    #: COUNT a crossing" — see `Brain._anon_hold_frames` for why that symmetry is
    #: the point rather than a coincidence.
    anon_hold_frames: Optional[int] = None


# ---------------------------------------------------------------- websocket


class _Subscriber:
    """One WebSocket client. Fed from the ingest thread, drained on the loop.

    The queue is bounded and COALESCES nothing: a UI that skips a state skips
    an exception row, and an exception nobody sees is the failure mode this
    whole product is built against. When the bound is hit the OLDEST state is
    dropped (a stale frame is the least valuable thing in the queue) and the
    drop is counted so it is visible rather than silent.
    """

    __slots__ = ("loop", "event", "queue", "dropped")

    def __init__(self, loop: "asyncio.AbstractEventLoop", maxlen: int = 256) -> None:
        self.loop = loop
        self.event = asyncio.Event()
        self.queue: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self.dropped = 0

    def offer(self, payload: dict[str, Any]) -> None:
        if len(self.queue) == self.queue.maxlen:
            self.dropped += 1
        self.queue.append(payload)
        try:
            self.loop.call_soon_threadsafe(self.event.set)
        except RuntimeError:  # pragma: no cover - loop already closed
            pass

    def drain(self) -> list[dict[str, Any]]:
        out = list(self.queue)
        self.queue.clear()
        return out


# -------------------------------------------------------------------- brain


class Brain:
    """The counter's pipeline. One instance per laptop, one session at a time."""

    def __init__(self, config: BrainConfig) -> None:
        self.config = config
        self.clock = config.clock
        self.ledger = config.ledger
        self.settlement = config.settlement
        self.plane = config.plane
        self.tracker = config.tracker
        self.line = config.line
        self.identifier = config.identifier
        self.prices = dict(config.prices)
        self.session = config.session or Session(config.clock, config.ledger)

        self._detector = config.detector
        if self._detector is None and config.reference is not None:
            self._detector = PlacementDetector(config.reference, clock=config.clock)

        self._lock = threading.RLock()
        self._frame_index = -1
        self._last_lock = MatLockView(False, "no frame yet")
        self._placements: tuple[PlacementView, ...] = ()
        self._exceptions: list[BrainException] = []
        self._identity: dict[str, Identification] = {}
        self._sku: dict[str, Optional[str]] = {}
        self._registered: set[str] = set()
        self._mint: Optional[MintResult] = None
        self._settled_payment_id: Optional[str] = None
        self._last_webhook_reason: Optional[str] = None
        self._crossings: Optional[CrossingResult] = None
        self._anon_frames = 0
        self._anon_fired = False
        self._state: Optional[BrainState] = None

        self._subs: set[_Subscriber] = set()
        self._subs_lock = threading.Lock()

        # INVARIANT 5, asserted structurally rather than promised in prose: the
        # brain must not be able to name the secret even if a later edit wanted
        # to. If this ever fires, someone has moved the key to the wrong side.
        for name in vars(self):
            if "secret" in name.lower():  # pragma: no cover - guard
                raise BrainError(
                    f"Brain must not hold a secret; found attribute {name!r}. "
                    "The settlement port is the secret holder (INVARIANT 5)."
                )

        self._state = self._snapshot()

    # ------------------------------------------------------------- readouts

    @property
    def frame_index(self) -> int:
        return self._frame_index

    @property
    def detector(self) -> Any:
        return self._detector

    @property
    def exceptions(self) -> tuple[BrainException, ...]:
        return tuple(self._exceptions)

    @property
    def total_paise(self) -> int:
        return int(self.session.total_paise)

    def state(self) -> BrainState:
        with self._lock:
            if self._state is None:  # pragma: no cover - set in __init__
                self._state = self._snapshot()
            return self._state

    def identification(self, item_id: str) -> Optional[Identification]:
        """What identity actually decided for a line. Evidence, not a guess."""
        return self._identity.get(item_id)

    # ---------------------------------------------------------------- audit

    def _audit(self, what: str, **fields: Any) -> str:
        """One ledger line. The parameter is `what`, not `event`, so a caller
        can still audit a field genuinely called `event` — a Razorpay event name
        is one, and the collision was a real TypeError before it was a comment."""
        return self.ledger.append(
            ts=self.clock.now_iso(),
            module=MODULE,
            event=what,
            session_id=self.session.session_id,
            frame_index=self._frame_index,
            **fields,
        )

    def _except(
        self,
        code: str,
        detail: str,
        *,
        item_id: Optional[str] = None,
        x_mm: Optional[float] = None,
        y_mm: Optional[float] = None,
    ) -> BrainException:
        exc = BrainException(
            code=code,
            detail=detail,
            frame_index=self._frame_index,
            item_id=item_id,
            x_mm=_r(x_mm, 2),
            y_mm=_r(y_mm, 2),
        )
        self._exceptions.append(exc)
        cap = int(self.config.max_exceptions)
        if cap > 0 and len(self._exceptions) > cap:
            del self._exceptions[: len(self._exceptions) - cap]
        return exc

    # ---------------------------------------------------------------- frame

    def ingest_frame(self, frame: np.ndarray, ts: Optional[str] = None) -> BrainState:
        """Consume one camera frame. Returns the counter's whole state.

        `ts` is accepted for callers that already stamped the grab; it is
        recorded, never used for ordering. Ordering is the Clock's job.
        """
        with self._lock:
            self._frame_index += 1
            lock = self.plane.detect(frame)
            view = MatLockView.of(lock)

            if not lock.locked or lock.H is None:
                self._on_mat(False, view, grab_ts=ts)
                self._placements = ()
                return self._publish()

            rect = self.plane.rectify(frame, lock.H)
            # INVARIANT 4. From here down `frame` is unreachable from this
            # object: only `rect`, the 840x1188 metric crop, is ever stored or
            # measured. The local name is dropped so a later edit cannot
            # casually stash it on self.
            frame = None  # noqa: F841 - deliberate, see INVARIANT 4
            self._on_mat(True, view, grab_ts=ts)

            detector = self._ensure_detector(rect)
            placements = sorted(detector.update(rect), key=lambda p: p.id)

            update = self.tracker.update([p.centre_mm for p in placements])
            by_tid = self._associate(update, placements)
            self._register(by_tid, rect)
            self._placements = self._views(placements, by_tid)

            crossings = self.line.update(
                update.tracks, untracked=update.untracked, lost=update.lost
            )
            self._crossings = crossings
            self._apply_crossings(crossings)
            self._note_reid(update)

            return self._publish()

    # -- mat ---------------------------------------------------------------

    def _on_mat(
        self, locked: bool, view: MatLockView, *, grab_ts: Optional[str]
    ) -> None:
        was = self._last_lock.locked
        self._last_lock = view
        if locked == was:
            return
        self._audit(
            "mat_lock",
            locked=locked,
            reason=view.reason,
            ids_found=list(view.ids_found),
            scale_err=view.scale_err,
            persp_index=view.persp_index,
            reproj_rmse_px=view.reproj_rmse_px,
            grab_ts=grab_ts,
        )
        self.session.on_mat_lock(bool(locked))
        if not locked:
            self._except(REASON_MAT_LOST, view.reason)

    def _ensure_detector(self, rect: np.ndarray) -> Any:
        if self._detector is not None:
            return self._detector
        # Announced, never silent: whatever is on the mat right now becomes
        # "background" and stops being goods.
        self._detector = PlacementDetector(rect, clock=self.clock)
        self._audit(
            "reference_seeded",
            source="first_locked_frame",
            detail=(
                "no empty-mat reference was supplied; the first locked frame is "
                "now the background and anything on it is invisible to billing"
            ),
        )
        return self._detector

    # -- association -------------------------------------------------------

    @staticmethod
    def _associate(
        update: TrackerUpdate, placements: Sequence[DetectedPlacement]
    ) -> dict[int, DetectedPlacement]:
        """Tracker id -> the placement whose centroid it was built from.

        The tracker was handed these exact centroids this frame, so the nearest
        one is the same object by construction. Nearest rather than equality
        only so a future tracker that smooths a centroid does not break this.
        """
        out: dict[int, DetectedPlacement] = {}
        if not placements:
            return out
        for tid, point in update.tracks.items():
            best = min(
                placements,
                key=lambda p: math.hypot(
                    p.centre_mm[0] - point[0], p.centre_mm[1] - point[1]
                ),
            )
            out[int(tid)] = best
        return out

    @staticmethod
    def item_id_for(track_id: int) -> str:
        """The session's item id for a tracker id. One physical placement, one
        id, stable for as long as the tracker holds it."""
        return f"t{int(track_id)}"

    # -- identity ----------------------------------------------------------

    def _register(
        self, by_tid: Mapping[int, DetectedPlacement], rect: np.ndarray
    ) -> None:
        for tid in sorted(by_tid):
            placement = by_tid[tid]
            item_id = self.item_id_for(tid)
            if item_id in self._registered:
                continue

            if not placement.measurable:
                # A refusal that has persisted is a shopkeeper action, not a
                # blink. It enters the basket AMBER: goods are on the mat and
                # we will not price them.
                if placement.frames_seen < int(self.config.refuse_after_frames):
                    continue
                self._admit(
                    item_id,
                    sku_id=None,
                    price_paise=None,
                    reason=placement.reason,
                    placement=placement,
                )
                self._except(
                    REASON_PLACEMENT_REFUSED,
                    f"{placement.reason}: measured nothing, billed nothing",
                    item_id=item_id,
                    x_mm=placement.centre_mm[0],
                    y_mm=placement.centre_mm[1],
                )
                continue

            if not placement.stable or placement.long_edge_mm is None:
                continue

            crop = self._crop(rect, placement)
            ident = self.identifier.identify(crop, placement.long_edge_mm)
            self._identity[item_id] = ident
            self._audit(
                "identify",
                item_id=item_id,
                track_id=int(tid),
                detector_id=int(placement.id),
                **ident.to_audit(),
            )

            if ident.sku_id is None:
                self._admit(
                    item_id,
                    sku_id=None,
                    price_paise=None,
                    reason=ident.reason or REASON_UNKNOWN_SKU,
                    placement=placement,
                )
                continue

            price = self._price_for(ident.sku_id)
            if price is None:
                # Identified, but nobody ever told us what it costs. Guessing a
                # price is worse than an amber line a shopkeeper can tap.
                self._admit(
                    item_id,
                    sku_id=ident.sku_id,
                    price_paise=None,
                    reason=REASON_NO_PRICE,
                    placement=placement,
                )
                continue
            self._admit(
                item_id,
                sku_id=ident.sku_id,
                price_paise=price,
                reason="priced_from_gallery",
                placement=placement,
            )

    def _price_for(self, sku_id: str) -> Optional[int]:
        raw = self.prices.get(sku_id)
        if raw is None:
            return None
        try:
            return int(make_paise(raw))
        except MoneyError as exc:
            # A price book that answers with a float is a bug in the price
            # book, and billing it would be a bug in the money.
            self._except(
                REASON_NO_PRICE,
                f"price book answered for {sku_id!r} with non-integer paise: {exc}",
            )
            return None

    def _admit(
        self,
        item_id: str,
        *,
        sku_id: Optional[str],
        price_paise: Optional[int],
        reason: str,
        placement: DetectedPlacement,
    ) -> None:
        self.session.on_placement(
            SessionPlacement(
                item_id=item_id,
                name=sku_id,
                price_paise=price_paise,
                reason="" if price_paise is not None else reason,
            )
        )
        self._registered.add(item_id)
        self._sku[item_id] = sku_id
        self._audit(
            "admitted",
            item_id=item_id,
            sku_id=sku_id,
            price_paise=price_paise,
            amber=price_paise is None,
            reason=reason,
            long_edge_mm=_r(placement.long_edge_mm),
            short_edge_mm=_r(placement.short_edge_mm),
        )

    @staticmethod
    def _crop(rect: np.ndarray, placement: DetectedPlacement) -> np.ndarray:
        """The ORIENTED crop of one placement, upright, from the metric buffer.

        Axis-aligning the crop instead would hand the embedder a picture that is
        mostly mat for anything laid at an angle — the same AABB-is-not-a-
        footprint error placement.py refuses to make about area, made about
        appearance instead.
        """
        import cv2  # local: keeps the crop's dependency next to its only use

        cx = float(placement.centre_mm[0]) * PX_PER_MM_X
        cy = float(placement.centre_mm[1]) * PX_PER_MM_Y
        long_px = float(placement.long_edge_mm or 0.0) * PX_PER_MM_X
        short_px = float(placement.short_edge_mm or 0.0) * PX_PER_MM_Y
        w = max(2, int(round(long_px)))
        h = max(2, int(round(short_px)))
        angle = float(placement.angle_deg or 0.0)

        gray = rect if rect.ndim == 2 else cv2.cvtColor(rect, cv2.COLOR_BGR2GRAY)
        if abs(angle) < 1e-6 or abs(angle - 180.0) < 1e-6:
            rot = gray
        else:
            m = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)
            rot = cv2.warpAffine(
                gray, m, (BUF_W, BUF_H), flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE,
            )
        w = min(w, BUF_W)
        h = min(h, BUF_H)
        return cv2.getRectSubPix(rot, (w, h), (cx, cy))

    # -- crossings ---------------------------------------------------------

    def _anon_hold_frames(self) -> int:
        """How many consecutive frames of an unnamed centroid past the sell line
        it takes to freeze the total.

        Defaults to the line zone's own `min_crossing_frames`, and that symmetry
        is the whole argument: it must take exactly as much evidence to FREEZE
        the total as it takes to ADD to it. A single frame is not evidence —
        a hand sweeping over the line, a shadow, one blink of the segmenter all
        produce one unnamed centroid past the line, and a counter that froze on
        each of them would be a counter nobody switches on.
        """
        override = self.config.anon_hold_frames
        if override is not None:
            return max(1, int(override))
        return max(1, int(getattr(self.line, "min_crossing_frames", 1)))

    def _apply_crossings(self, result: CrossingResult) -> None:
        for tid in result.crossed_out:
            item_id = self.item_id_for(tid)
            transition = self.session.on_exit(item_id)
            self._audit(
                "crossing",
                direction="out",
                item_id=item_id,
                track_id=int(tid),
                sku_id=self._sku.get(item_id),
                session_reason=transition.reason,
                to_state=transition.to.value,
                total_paise=int(self.session.total_paise),
            )
        for tid in result.crossed_back:
            item_id = self.item_id_for(tid)
            transition = self.session.on_revert(item_id)
            self._audit(
                "crossing",
                direction="back",
                item_id=item_id,
                track_id=int(tid),
                session_reason=transition.reason,
                to_state=transition.to.value,
                total_paise=int(self.session.total_paise),
            )
        anonymous: list[CrossingException] = []
        for exc in result.exceptions:
            self._except(
                exc.code,
                str(exc),
                item_id=None if exc.track_id is None else self.item_id_for(exc.track_id),
                x_mm=exc.x_mm,
                y_mm=exc.y_mm,
            )
            self._audit(
                "crossing_exception",
                code=exc.code,
                detail=str(exc),
                track_id=exc.track_id,
                x_mm=_r(exc.x_mm, 2),
                y_mm=_r(exc.y_mm, 2),
            )
            if exc.track_id is None:
                # Goods left the counter and we cannot say which. Abstention 11.
                #
                # The discriminator is `track_id is None`, NOT a list of reason
                # codes. `LineZone` emits an anonymous crossing under whatever
                # code rode in on the centroid — the generic
                # `crossed_without_tracker_id` when the tracker simply had no
                # name for it, but `reidentification_ambiguous` or
                # `reidentification_gap_exceeded` when the tracker refused to
                # re-bind one, because sellevent deliberately reports the
                # SPECIFIC abstention rather than flattening it. Matching on the
                # generic code alone therefore let every refused
                # re-identification walk out of the shop uncounted while the
                # ledger dutifully logged it: found by
                # test_a_crossing_with_no_tracker_id_freezes_the_total, which
                # sends a `reidentification_ambiguous` crossing and used to end
                # BASKET_OPEN. A reason code is a name for WHY we abstained; the
                # missing id is the FACT that we did.
                anonymous.append(exc)
            elif exc.code == REASON_NEVER_COUNTED:
                self._never_counted(exc)
        self._apply_anonymous(anonymous)

    def _apply_anonymous(self, anonymous: Sequence[CrossingException]) -> None:
        """Freeze the total once an unnamed crossing has held long enough.

        Fires ONCE per streak, on the exact frame the streak reaches the hold,
        and re-arms only after a clean frame. Firing every frame would be
        harmless while frozen (the session's billing guard refuses and memoises
        the refusal) but it would make `acknowledge` useless: the shopkeeper
        would tap, the same unnamed blob would still be lying past the line, and
        the counter would freeze again on the very next frame forever.
        """
        if not anonymous:
            self._anon_frames = 0
            self._anon_fired = False
            return
        self._anon_frames += 1
        if self._anon_fired or self._anon_frames < self._anon_hold_frames():
            return
        self._anon_fired = True
        worst = anonymous[0]
        transition = self.session.on_exit(None)
        self._audit(
            EVENT_UNCOUNTED,
            code=worst.code,
            held_frames=self._anon_frames,
            required_frames=self._anon_hold_frames(),
            candidate_ids=list(worst.candidate_ids),
            x_mm=_r(worst.x_mm, 2),
            y_mm=_r(worst.y_mm, 2),
            session_reason=transition.reason,
            to_state=transition.to.value,
            total_paise=int(self.session.total_paise),
        )

    def _never_counted(self, exc: CrossingException) -> None:
        """A TRACKED object vanished mid-crossing. Sometimes that is money.

        `LineZone._retire` fires this only when a track was last definitely seen
        on the opposite side of the sell line from the side it is counted as
        being on — i.e. it really did change sides and the debounce never
        confirmed it. Two cases, and they are not the same:

          * the line was never committed. We measured goods, they moved to the
            customer's side, and they are gone. That is an UNDER-count, exactly
            the harm the anonymous branch above freezes for, and it freezes for
            the same reason. It is not less serious for having had a name.

          * the line was already committed. The money is on the bill; what
            vanished mid-move was a return the debounce never confirmed. We do
            not un-bill on that evidence — a packet can leave the frame for a
            dozen innocent reasons — so it stays an exception row and the
            shopkeeper's tap-to-revert is the instrument. Charging a customer
            for something they put back is the failure this leaves open, and
            leaving it open beats auto-refunding on an occlusion.
        """
        item_id = self.item_id_for(exc.track_id)
        line = next(
            (li for li in self.session.line_items if li.item_id == item_id), None
        )
        if line is None or line.committed or line.reverted:
            return
        transition = self.session.on_exit(None)
        self._audit(
            EVENT_UNCOUNTED,
            code=exc.code,
            item_id=item_id,
            track_id=int(exc.track_id),
            was_committed=False,
            x_mm=_r(exc.x_mm, 2),
            y_mm=_r(exc.y_mm, 2),
            session_reason=transition.reason,
            to_state=transition.to.value,
            total_paise=int(self.session.total_paise),
        )

    def _note_reid(self, update: TrackerUpdate) -> None:
        for point in update.reid_abstentions:
            self._except(
                point.code,
                point.detail,
                x_mm=point.x_mm,
                y_mm=point.y_mm,
            )

    # ------------------------------------------------------------- shopkeeper

    def price_tap(self, item_id: str, amount_paise: int) -> BrainState:
        """The shopkeeper tapped a price onto an amber line. Warm enroll."""
        with self._lock:
            transition = self.session.on_price(item_id, amount_paise)
            self._audit(
                "price_tap",
                item_id=item_id,
                price_paise=int(make_paise(amount_paise)),
                session_reason=transition.reason,
                human_override=True,
            )
            return self._publish()

    def revert(self, item_id: str) -> BrainState:
        """Tap-to-revert. The line leaves the bill and the ledger says who did it."""
        with self._lock:
            transition = self.session.on_revert(item_id)
            self._audit(
                "revert",
                item_id=item_id,
                session_reason=transition.reason,
                human_override=True,
                total_paise=int(self.session.total_paise),
            )
            return self._publish()

    def acknowledge(self) -> BrainState:
        """The shopkeeper accepted a frozen-total exception and resumed."""
        with self._lock:
            transition = self.session.on_acknowledge()
            self._audit(
                "acknowledge",
                session_reason=transition.reason,
                to_state=transition.to.value,
                human_override=True,
            )
            return self._publish()

    def set_online(self, up: bool) -> BrainState:
        """Network up/down. Down means billing continues and nothing is
        authorised; up drains the one pending intent."""
        with self._lock:
            transition = self.session.on_network(bool(up))
            self._audit(
                "network",
                online=bool(up),
                session_reason=transition.reason,
                to_state=transition.to.value,
            )
            if (
                up
                and self.session.state is State.AWAITING_SETTLEMENT
                and self._mint is None
            ):
                self._do_mint()
            return self._publish()

    def set_perf(self, p95_ms: int) -> BrainState:
        """Frame-time report. Over the budget, auto-commit is disabled."""
        with self._lock:
            transition = self.session.on_perf(int(p95_ms))
            self._audit(
                "perf",
                p95_ms=int(p95_ms),
                session_reason=transition.reason,
                to_state=transition.to.value,
            )
            return self._publish()

    # ---------------------------------------------------------------- money

    def done(self) -> BrainState:
        """DONE tap. Closes the basket, then asks the settlement port to mint.

        Nothing about this call can authorise money. It records an AMOUNT; the
        only thing that can turn that into PAID is a signed webhook.
        """
        with self._lock:
            transition = self.session.on_done()
            self._audit(
                "done",
                session_reason=transition.reason,
                to_state=transition.to.value,
                total_paise=int(self.session.total_paise),
                intent_amount_paise=self.session.intent_amount_paise,
                amber_count=self.session.amber_count,
            )
            if self.session.state is State.AWAITING_SETTLEMENT:
                self._do_mint()
            return self._publish()

    def _do_mint(self) -> None:
        amount = self.session.intent_amount_paise
        if amount is None:  # pragma: no cover - guarded by the state check
            return
        result = self.settlement.mint(self.session.session_id, int(amount))
        self._audit(
            "mint",
            minted=result.minted,
            reason=result.reason,
            nonce=result.nonce,
            payment_link_id=result.payment_link_id,
            amount_paise=result.amount_paise,
            replayed=result.replayed,
            detail=result.detail,
        )
        if result.minted:
            self._mint = result
        else:
            self._mint = None
            self._except(
                REASON_MINT_FAILED,
                f"{result.reason}: {result.detail}",
            )

    def on_webhook(
        self,
        raw_body: bytes,
        signature: str,
        *,
        header_event_id: Optional[str] = None,
    ) -> BrainState:
        """The only door to PAID.

        Takes the RAW BYTES off the wire. Nothing in this method parses them:
        the settlement port verifies the HMAC first and the session is only ever
        shown the adjudicated verdict.
        """
        with self._lock:
            outcome = self.settlement.adjudicate(
                raw_body, signature, header_event_id=header_event_id
            )
            verdict = outcome.verdict
            self._last_webhook_reason = verdict.reason
            if outcome.payment_id and verdict.green:
                self._settled_payment_id = outcome.payment_id

            # An UNAUTHENTICATED delivery is never shown to the session.
            #
            # This is not squeamishness, it is the same denial-of-service hole
            # `webhook.py` closes at GATE 2, one layer up. `Session.on_webhook`
            # memoises by `event_id` and replays the memo for a repeat. A
            # delivery that fails the HMAC has no authenticated envelope id, so
            # the only name available for it is the sha256 of its bytes — and
            # those bytes can be a COPY of the genuine delivery. Feeding it in
            # would let anyone who can see one real webhook re-post it with a
            # junk signature, burn that id in the session's memo, and have the
            # genuine delivery come back as "already handled, bad signature".
            # Money in, counter never green. Found by running
            # test_a_forged_signature_is_discarded_and_changes_nothing, which
            # replays the same bytes twice — forged, then genuine — and asserts
            # the genuine one still pays.
            #
            # Nothing is lost by refusing: the discard is on the ledger twice
            # already, once from the predicate's own audit line and once from
            # the `brain/webhook` line written just below.
            session_reason = "unauthenticated_never_reached_the_session"
            if verdict.signature_valid:
                transition = self.session.on_webhook(
                    Verdict(
                        # No envelope id means the sha256 of the SIGNED bytes
                        # names the delivery. That is HMAC-covered content, so
                        # it is a fact about the delivery rather than an
                        # identity we invented for it.
                        event_id=verdict.event_id or verdict.body_sha256,
                        event=verdict.event or "",
                        session_id=verdict.session_id or "",
                        amount_paise=verdict.amount_paise,
                        green=bool(verdict.green),
                        signature_valid=True,
                        reason=verdict.reason,
                    )
                )
                session_reason = transition.reason
            self._audit(
                "webhook",
                green=bool(verdict.green),
                reason=verdict.reason,
                severity=verdict.severity,
                signature_valid=bool(verdict.signature_valid),
                razorpay_event=verdict.event,
                event_id=verdict.event_id,
                body_sha256=verdict.body_sha256,
                amount_paise=verdict.amount_paise,
                expected_paise=verdict.expected_paise,
                settled_nonce=outcome.settled_nonce,
                payment_id=outcome.payment_id,
                session_reason=session_reason,
                to_state=self.session.state.value,
            )
            if not verdict.green:
                self._except(
                    REASON_WEBHOOK_REFUSED,
                    f"{verdict.reason}: {verdict.detail}",
                )
            return self._publish()

    # ----------------------------------------------------------- state / pub

    def _views(
        self,
        placements: Sequence[DetectedPlacement],
        by_tid: Mapping[int, DetectedPlacement],
    ) -> tuple[PlacementView, ...]:
        owner = {id(p): tid for tid, p in by_tid.items()}
        out = []
        for p in placements:
            tid = owner.get(id(p))
            out.append(
                PlacementView(
                    item_id=None if tid is None else self.item_id_for(tid),
                    detector_id=int(p.id),
                    centre_mm=(_r(p.centre_mm[0], 2) or 0.0, _r(p.centre_mm[1], 2) or 0.0),
                    long_edge_mm=_r(p.long_edge_mm),
                    short_edge_mm=_r(p.short_edge_mm),
                    area_mm2=_r(p.area_mm2, 1),
                    angle_deg=_r(p.angle_deg, 2),
                    stable=bool(p.stable),
                    measurable=bool(p.measurable),
                    reason=p.reason,
                    frames_seen=int(p.frames_seen),
                )
            )
        return tuple(out)

    def _lines(self) -> tuple[BasketLine, ...]:
        out = []
        for li in self.session.line_items:
            out.append(
                BasketLine(
                    item_id=li.item_id,
                    sku_id=self._sku.get(li.item_id),
                    name=li.name,
                    price_paise=li.price_paise,
                    amber=li.amber,
                    committed=li.committed,
                    reverted=li.reverted,
                    reason=li.reason,
                )
            )
        return tuple(out)

    def _snapshot(self) -> BrainState:
        lines = self._lines()
        crossings = self._crossings
        return BrainState(
            frame_index=self._frame_index,
            ts=self.clock.now_iso(),
            session_id=self.session.session_id,
            session_state=self.session.state.value,
            mat_lock=self._last_lock,
            placements=self._placements,
            lines=lines,
            total_paise=int(self.session.total_paise),
            amber_items=tuple(
                li for li in lines if li.amber and li.committed and not li.reverted
            ),
            exceptions=tuple(self._exceptions),
            ledger_head=self.ledger.head,
            ledger_lines=int(self.ledger.count),
            net_crossings=0 if crossings is None else int(crossings.net_count),
            crossings_amber=False if crossings is None else bool(crossings.amber),
            frozen=bool(self.session.frozen),
            online=bool(self.session.online),
            money_authorised=bool(self.session.money_authorised),
            intent_amount_paise=self.session.intent_amount_paise,
            nonce=None if self._mint is None else self._mint.nonce,
            short_url=None if self._mint is None else self._mint.short_url,
            settled_payment_id=self._settled_payment_id,
            last_webhook_reason=self._last_webhook_reason,
        )

    def _publish(self) -> BrainState:
        state = self._snapshot()
        self._state = state
        payload = state.to_dict()
        with self._subs_lock:
            subs = list(self._subs)
        for sub in subs:
            sub.offer(payload)
        return state

    # -- subscription (used by the WebSocket endpoint) ----------------------

    def subscribe(self, loop: "asyncio.AbstractEventLoop") -> _Subscriber:
        sub = _Subscriber(loop)
        with self._subs_lock:
            self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: _Subscriber) -> None:
        with self._subs_lock:
            self._subs.discard(sub)

    @property
    def subscriber_count(self) -> int:
        with self._subs_lock:
            return len(self._subs)

    def close(self) -> None:
        closer = getattr(self.settlement, "close", None)
        if callable(closer):
            closer()


# ------------------------------------------------------------------- server


def create_app(brain: Brain, *, keepalive_s: float = 25.0) -> Any:
    """The PWA's server: one WebSocket, two read-only GETs, no model weights.

    INVARIANT 3 lives here by omission — the browser is sent JSON with
    millimetres and paise in it and nothing else. There is nothing to download
    and nothing to run: every decision was already made on this side.
    """
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect

    app = FastAPI(title="GAWAAH brain", version="1")

    @app.get("/health")
    def health() -> dict[str, Any]:
        state = brain.state()
        return {
            "ok": True,
            "module": MODULE,
            "session_id": state.session_id,
            "session_state": state.session_state,
            "frame_index": state.frame_index,
            "ledger_head": state.ledger_head,
            "subscribers": brain.subscriber_count,
        }

    @app.get("/state")
    def state() -> dict[str, Any]:
        return brain.state().to_dict()

    async def ws(socket) -> None:
        await socket.accept()
        sub = brain.subscribe(asyncio.get_running_loop())
        try:
            # The current state first, so a client that connects mid-sale is not
            # blank until the next frame.
            await socket.send_json(brain.state().to_dict())
            while True:
                try:
                    await asyncio.wait_for(sub.event.wait(), timeout=keepalive_s)
                except (asyncio.TimeoutError, TimeoutError):
                    await socket.send_json({"type": "keepalive"})
                    continue
                sub.event.clear()
                for payload in sub.drain():
                    await socket.send_json(payload)
        except WebSocketDisconnect:
            pass
        finally:
            brain.unsubscribe(sub)

    # The annotation is attached here rather than written inline because this
    # module runs under PEP 563 (`from __future__ import annotations`), which
    # turns every inline annotation into a STRING, and FastAPI resolves those
    # strings against the function's MODULE globals. `WebSocket` is imported
    # inside this function, so the string never resolves, and FastAPI falls
    # back to treating `socket` as a query parameter — every connect is then
    # closed with 1008 "field required". Binding the real class defeats the
    # string round-trip and keeps fastapi a lazy import.
    ws.__annotations__["socket"] = WebSocket
    # BOTH paths, deliberately, and this duplicate route is not dead code.
    #
    # `/ws` is the conventional name and what a human types. `/` is what the
    # SHIPPED PWA actually dials: web/app.js has
    #     export const WS_URL = 'ws://localhost:8787';
    # which is the ROOT path, not /ws. Mounting only /ws meant the brain served
    # a WebSocket nobody in this repo connects to — the counter and its screen
    # would both work perfectly and never meet, which is exactly the
    # islands-that-never-touch failure this module exists to end. Found by
    # opening web/app.js instead of trusting the docstring that claimed it
    # dialled 8787; it does, at a path that was not there.
    #
    # Pinned by test_websocket_is_served_where_the_pwa_actually_dials. If that
    # constant in app.js ever moves to '/ws', delete the root route, not the
    # test.
    for path in ("/ws", "/"):
        app.websocket(path)(ws)

    return app


def serve(
    brain: Brain,
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    log_level: str = "info",
) -> None:  # pragma: no cover - a blocking server is not a unit test
    """Run the brain's WebSocket server. Blocks.

    HONEST NOTE ON DEPENDENCIES: uvicorn needs `websockets` or `wsproto` to
    speak the WebSocket protocol over a real socket. Neither is installed in
    this repo's venv, so this function raises a clear error instead of silently
    serving an app whose /ws endpoint would 500 on every connect. The endpoint
    itself is exercised end to end through starlette's TestClient, which drives
    the same ASGI code path without a network socket — see
    tests/test_brain.py::test_websocket_*.
    """
    import importlib.util

    if not any(
        importlib.util.find_spec(m) is not None for m in ("websockets", "wsproto")
    ):
        raise BrainError(
            "uvicorn cannot serve WebSockets without `websockets` or `wsproto` "
            "installed. Install one of them, or drive create_app(brain) with "
            "any ASGI server that has WebSocket support."
        )
    import uvicorn

    uvicorn.run(create_app(brain), host=host, port=port, log_level=log_level)
