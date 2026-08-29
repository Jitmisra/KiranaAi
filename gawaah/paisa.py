"""S4e — PAISA, the money service. FastAPI. The sole holder of secrets.

INVARIANT 5 lives here: *paisa re-runs the crossing predicate server-side*.
The phone submits geometry — a homography, the four marker centres it saw, and
the centroid tracks it believes crossed the exit line. paisa believes none of
it. It replays those tracks through the same deterministic `sellevent.LineZone`
the phone claims to have used, recomputes which items crossed, reprices them
from a server-side price book, and only then mints. A phone that lies about a
crossing, a price, or a total gets a 409 and an audit line, and no rupee moves.

Four rules this module is built around
--------------------------------------
1. INVARIANT 1 — integer paise. This file is on `tools/lint_no_float.py`'s
   money path, so it contains no float literal, no `float()` cast and no `/`.
   That is why the homography check is written as a cross-multiplied
   comparison (`|a - X*w| <= tol*|w|`) instead of a perspective divide, and why
   the reported homography residual is a *slack* in px·|w| rather than px: a
   module that may not divide cannot honestly report a pixel distance.

2. INVARIANT 2 — GREEN happens in `gawaah.webhook.GreenPredicate` and nowhere
   else. `/webhook` reads the raw bytes with `await request.body()` and hands
   them to the predicate BEFORE anything parses them. paisa never signs a body
   and never constructs a payment payload (INVARIANT 6).

3. INVARIANT 5 — secrets come from the environment only, are never logged,
   never appear in a response, and are redacted in every `repr`. `assert_ready`
   refuses to start in live mode without them.

4. INVARIANT 7 — an item the price book does not know is AMBER: it is recorded,
   excluded from the total, and never guessed at.

5. PRD 9 — the customer's identity is not ours to keep. A Razorpay payment
   carries a vpa, an email, a contact, an rrn and (on a card payment) a whole
   card object. GAWAAH needs none of them to know that the right amount arrived
   for the right session, so `strip_pii` drops them at the one boundary where a
   gateway document enters this process, before it is stored, audited or
   returned. The webhook path never stores the body at all: only its sha256
   reaches the ledger, which identifies a delivery without preserving it.

Running it
----------
    RZP_MODE=sim uvicorn --factory gawaah.paisa:create_app

`create_app` is a factory on purpose: importing this module must not create a
database, a ledger or a gateway client as a side effect.
"""
from __future__ import annotations

import json
import math
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from . import kernel as _kernel
from .clock import Clock, RealClock
from .ledger import Ledger
from .money import MoneyError, paise, to_rupees_str
from .sellevent import LineZone
from .session import Placement, Session, Verdict
from .webhook import GreenPredicate, GreenVerdict, SeenStore
from .webhook import Intent as WebhookIntent

MODULE = "paisa"

#: Kernel states in which an intent is still awaiting an answer from Razorpay.
#: These are the only states that may be presented to the green predicate as
#: an OPEN intent.
OPEN_STATES = frozenset({_kernel.NEW, _kernel.CALLING})

#: How far (in rectified buffer px, scaled by the homogeneous denominator) a
#: submitted marker centre may land from the printed centre before the
#: homography is refused. 8 px is ~2.8 mm on the TAKHTI.
H_TOL_PX = 8

#: A determinant at or below this is a degenerate homography. Written as a
#: power of ten rather than 1e-9 because a float literal is banned in this file.
DET_EPS = 10 ** -9

#: Used only when RZP_MODE=sim and no webhook secret is configured. An empty
#: secret would make every signature forgeable, so the simulator gets a named
#: placeholder and `/health` reports `webhook_secret_configured: false`.
SIM_FALLBACK_WEBHOOK_SECRET = "whsec_simulated"

#: Closed vocabulary of refusal codes. A bare 409 tells an operator nothing.
REFUSAL_CODES = frozenset(
    {
        "duplicate_item_id",
        "duplicate_track_id",
        "homography_rejected",
        "crossing_set_mismatch",
        "uncounted_crossing",
        "price_disagreement",
        "amount_disagreement",
        "zero_total",
        "session_total_disagreement",
        "basket_locked",
        "gateway_error",
        "bad_amount",
        "bad_price_book",
    }
)

#: Keys that carry the customer rather than the transaction. Dropped from every
#: gateway document before it is stored, audited or returned.
#:
#: `name` is here because the only names a payment document carries are the
#: cardholder's and the customer's; GAWAAH's own item names travel in the
#: request, never in a gateway response. `acquirer_data` is here because it
#: holds the rrn/UTR, which is a lookup key into the customer's bank statement.
PII_FIELDS: frozenset[str] = frozenset(
    {
        "vpa",
        "email",
        "contact",
        "phone",
        "mobile",
        "name",
        "customer",
        "customer_id",
        "customer_details",
        "card",
        "card_id",
        "bank_transaction_id",
        "acquirer_data",
        "rrn",
        "utr",
        "upi",
        "upi_transaction_id",
        "billing_address",
        "shipping_address",
        "ip",
        "user_agent",
    }
)

#: Marker left behind so an operator can see the scrub ran. Field NAMES only —
#: "this document had an email on it" is not itself an email.
PII_DROPPED_KEY = "_pii_dropped"


def strip_pii(document: Any) -> Any:
    """Return a copy of `document` with every PII-bearing key removed.

    Recursive, because Razorpay nests: `payment_links[].payments[].email`,
    `payment.acquirer_data.rrn`, `payment.card.last4`. Non-destructive, because
    the caller may still be holding the gateway's own object.

    This is a drop, not a redaction: a redacted placeholder still records that a
    particular customer transacted here, and the point is that this process has
    nothing to lose in the first place.
    """
    dropped: set[str] = set()
    cleaned = _scrub(document, dropped)
    if dropped and isinstance(cleaned, dict):
        cleaned[PII_DROPPED_KEY] = sorted(dropped)
    return cleaned


def _scrub(node: Any, dropped: set[str]) -> Any:
    if isinstance(node, Mapping):
        out: dict[Any, Any] = {}
        for key, value in node.items():
            if isinstance(key, str) and key.lower() in PII_FIELDS:
                dropped.add(key.lower())
                continue
            out[key] = _scrub(value, dropped)
        return out
    if isinstance(node, (list, tuple)):
        return [_scrub(v, dropped) for v in node]
    return node


class PaisaConfigError(RuntimeError):
    """Deployment is misconfigured. Raised at startup, never mid-request."""


class PaisaRefusal(Exception):
    """A request paisa declines to act on. Carries a status and an audit body."""

    def __init__(self, status: int, code: str, detail: str, **extra: Any) -> None:
        super().__init__(f"{code}: {detail}")
        self.status = int(status)
        self.code = code
        self.detail = detail
        self.extra = dict(extra)

    def body(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "detail": self.detail,
            "minted": False,
            "module": MODULE,
            **self.extra,
        }


# ---------------------------------------------------------------- injection


class Gateway(Protocol):
    """The narrow slice of Razorpay paisa is allowed to depend on.

    `gawaah.rzp_sim.RazorpaySim` satisfies it today; the real client satisfies
    it when keys land. Nothing above this line changes when they do.
    """

    def create_payment_link(
        self, amount_paise: int, notes: Mapping[str, str], **kwargs: Any
    ) -> dict: ...


class PriceBook(Protocol):
    """Server-side prices. The phone never sets a price; it may only agree.

    `None` means "this counter cannot price that item", which is AMBER, which
    is excluded from the total (INVARIANT 7). It is never a guess and never 0.
    """

    def price_paise(self, item_id: str) -> Optional[int]: ...


class DictPriceBook:
    """A price book backed by a mapping. Rejects a float price at the door.

    The `paise()` calls below are the whole of INVARIANT 1 at this boundary and
    they are load-bearing, not decorative. Without them `int(214.507)` is 214
    paise: two rupees fourteen for a two-hundred-fourteen rupee bag of rice, a
    99% discount that no wire model, no lint rule and no downstream check would
    catch, because by the time the number is in the book it IS an int.
    See tests/test_paisa.py::test_a_truncating_price_book_would_be_caught_not_silently_billed
    """

    def __init__(self, prices: Mapping[str, int] | None = None) -> None:
        self._prices: dict[str, int] = {}
        for k, v in dict(prices or {}).items():
            self._prices[str(k)] = int(paise(v))

    def price_paise(self, item_id: str) -> Optional[int]:
        return self._prices.get(item_id)

    def set_price(self, item_id: str, amount_paise: int) -> None:
        self._prices[str(item_id)] = int(paise(amount_paise))

    def __len__(self) -> int:
        return len(self._prices)


def book_price_paise(book: PriceBook, item_id: str) -> Optional[int]:
    """Ask a price book for a price, and insist the answer is integer paise.

    `PriceBook` is a Protocol, so the implementation is whatever a deployment
    plugs in: a CSV loader, a spreadsheet export, an HTTP call whose JSON
    numbers arrive as floats. `DictPriceBook` guards its own door; this guards
    every other door, so a float from a third-party book is refused with a named
    code instead of truncated into a discount.
    """
    price = book.price_paise(item_id)
    if price is None:
        return None
    return int(paise(price))


# ---------------------------------------------------------------- config


@dataclass(frozen=True, repr=False)
class PaisaConfig:
    """Secrets and mode. Read from the environment, never from a file we ship."""

    mode: str = "sim"
    key_id: str = "rzp_test_SIMULATED"
    key_secret: str = ""
    webhook_secret: str = ""
    seed: int = 0
    account_id: str = "acc_GAWAAHSIM00"

    @staticmethod
    def from_env(env: Mapping[str, str] | None = None) -> "PaisaConfig":
        """Build from the environment and validate before anything else starts."""
        src: Mapping[str, str] = os.environ if env is None else env
        mode = (src.get("RZP_MODE") or src.get("GAWAAH_RZP_MODE") or "sim").strip().lower()
        seed_raw = (src.get("GAWAAH_RZP_SEED") or "0").strip()
        if not seed_raw.lstrip("-").isdigit():
            raise PaisaConfigError(f"GAWAAH_RZP_SEED must be an integer, got {seed_raw!r}")
        cfg = PaisaConfig(
            mode=mode,
            key_id=src.get("RAZORPAY_KEY_ID", "rzp_test_SIMULATED"),
            key_secret=src.get("RAZORPAY_KEY_SECRET", ""),
            webhook_secret=src.get("RAZORPAY_WEBHOOK_SECRET", ""),
            seed=int(seed_raw),
            account_id=src.get("RAZORPAY_ACCOUNT_ID", "acc_GAWAAHSIM00"),
        )
        cfg.assert_ready()
        return cfg

    # -- never leak a secret into a traceback, a log or a repr ------------
    def __repr__(self) -> str:
        return (
            f"PaisaConfig(mode={self.mode!r}, key_id={self.key_id!r}, "
            f"key_secret=<{len(self.key_secret)} chars redacted>, "
            f"webhook_secret=<{len(self.webhook_secret)} chars redacted>, "
            f"seed={self.seed}, account_id={self.account_id!r})"
        )

    __str__ = __repr__

    @property
    def key_secret_configured(self) -> bool:
        return bool(self.key_secret)

    @property
    def webhook_secret_configured(self) -> bool:
        return bool(self.webhook_secret)

    @property
    def effective_webhook_secret(self) -> str:
        """The secret the predicate verifies against.

        In sim mode an unset secret falls back to a named placeholder so the
        simulator and the verifier agree; `/health` still reports the secret as
        unconfigured. In live mode there is no fallback — `assert_ready` has
        already refused to start.
        """
        if self.webhook_secret:
            return self.webhook_secret
        if self.mode == "sim":
            return SIM_FALLBACK_WEBHOOK_SECRET
        return ""

    def assert_ready(self) -> None:
        if self.mode not in ("sim", "live"):
            raise PaisaConfigError(
                f"RZP_MODE must be 'sim' or 'live', got {self.mode!r}"
            )
        if self.mode == "live":
            missing = [
                name
                for name, value in (
                    ("RAZORPAY_KEY_SECRET", self.key_secret),
                    ("RAZORPAY_WEBHOOK_SECRET", self.webhook_secret),
                )
                if not value
            ]
            if missing:
                # the names, never the values
                raise PaisaConfigError(
                    "RZP_MODE=live but these are empty in the environment: "
                    + ", ".join(missing)
                    + ". An empty webhook secret makes every signature forgeable."
                )


def build_gateway(
    cfg: PaisaConfig,
    clock: Clock,
    *,
    sink: Callable[[Any], Any] | None = None,
    live_factory: Callable[[PaisaConfig], Gateway] | None = None,
) -> Gateway:
    """Config-only swap between the simulator and the real client.

    `rzp_sim` is imported lazily so a live deployment never loads a module whose
    whole job is to fabricate payments.
    """
    if cfg.mode == "sim":
        from .rzp_sim import RazorpaySim

        return RazorpaySim(
            webhook_secret=cfg.effective_webhook_secret,
            clock=clock,
            seed=cfg.seed,
            sink=sink,
            account_id=cfg.account_id,
        )
    if cfg.mode == "live":
        if live_factory is None:
            raise PaisaConfigError(
                "RZP_MODE=live but no live_factory was injected. paisa does not "
                "ship a hard-coded HTTP client for the gateway."
            )
        return live_factory(cfg)
    raise PaisaConfigError(f"unknown RZP_MODE {cfg.mode!r}")


# ---------------------------------------------------------------- wire models


class Crossing(BaseModel):
    """One exit-line crossing the phone claims to have observed.

    `path_mm` is the centroid track in TAKHTI millimetres, one entry per frame,
    which is exactly what the server needs to re-run the predicate without a
    camera. `committed` is the phone's CLAIM; paisa recomputes it.
    """

    model_config = ConfigDict(extra="forbid")

    item_id: StrictStr
    track_id: StrictInt
    path_mm: list[tuple[float, float]]
    committed: StrictBool
    name: Optional[StrictStr] = None
    #: Optional. If present it must equal the server's price exactly; the phone
    #: may agree with the price book, never set it.
    price_paise: Optional[StrictInt] = None


class Geometry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: 3x3 homography, FRAME pixels -> rectified buffer pixels.
    H: list[list[float]]
    #: The four ArUco marker centres in FRAME pixels, TL, TR, BR, BL.
    corners: list[tuple[float, float]]
    crossings: list[Crossing]
    #: Per-frame centroids the tracker refused to name. A centroid that reaches
    #: the far side of the line without an id is an uncounted sale.
    untracked: list[list[tuple[float, float]]] = Field(default_factory=list)
    min_crossing_frames: StrictInt = 3


class IntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: StrictStr
    #: StrictInt, so 21450.0 is a 422 at the boundary and never becomes money.
    amount_paise: StrictInt
    geometry: Geometry


# ---------------------------------------------------------------- geometry


@dataclass(frozen=True)
class GeometryVerdict:
    """The server's own answer, and how it differs from the phone's."""

    agrees: bool
    reason: str
    detail: str
    server_committed: tuple[str, ...]
    client_committed: tuple[str, ...]
    amber_items: tuple[str, ...]
    priced_items: tuple[str, ...]
    uncounted: int
    server_total_paise: int
    frames: int
    homography_ok: bool
    #: Worst-case tolerance slack of the marker-centre check, in px·|w|. It is
    #: not px: paisa may not divide, so the homogeneous denominator stays in.
    #: Negative means at least one submitted corner failed the tolerance.
    homography_slack_pxw: Optional[float]
    homography_note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "agrees": self.agrees,
            "reason": self.reason,
            "detail": self.detail,
            "server_committed": list(self.server_committed),
            "client_committed": list(self.client_committed),
            "amber_items": list(self.amber_items),
            "priced_items": list(self.priced_items),
            "uncounted": self.uncounted,
            "server_total_paise": self.server_total_paise,
            "frames": self.frames,
            "homography_ok": self.homography_ok,
            "homography_slack_pxw": self.homography_slack_pxw,
            "homography_note": self.homography_note,
        }


_EXPECTED_BUFFER_POINTS: list[tuple[float, float]] | None = None
_EXPECTED_NOTE = ""


def expected_marker_points() -> tuple[list[tuple[float, float]] | None, str]:
    """The printed marker centres in rectified buffer px, TL, TR, BR, BL.

    `takhti` is imported lazily because it pulls in cv2: the money service must
    start on a box with no camera stack, it just cannot check corners there.
    """
    global _EXPECTED_BUFFER_POINTS, _EXPECTED_NOTE
    if _EXPECTED_BUFFER_POINTS is not None or _EXPECTED_NOTE:
        return _EXPECTED_BUFFER_POINTS, _EXPECTED_NOTE
    try:
        from .takhti import marker_centres_mm, mm_to_buffer
    except Exception as exc:  # pragma: no cover - exercised only without cv2
        _EXPECTED_NOTE = f"corner check skipped: takhti unavailable ({exc!r})"
        return None, _EXPECTED_NOTE
    pts = mm_to_buffer(marker_centres_mm()).tolist()
    _EXPECTED_BUFFER_POINTS = [(row[0], row[1]) for row in pts]
    _EXPECTED_NOTE = "corner check against printed TAKHTI marker centres"
    return _EXPECTED_BUFFER_POINTS, _EXPECTED_NOTE


def _finite(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def check_homography(
    h: Sequence[Sequence[float]], corners: Sequence[Sequence[float]]
) -> tuple[bool, str, Optional[float], str]:
    """Is this a sane frame->buffer homography, and does it explain the corners?

    Returns (ok, detail, worst_slack_pxw, note). The tolerance test is written
    cross-multiplied — `|a - X*w| <= tol*|w|` — so that no division happens in
    the money path. That is also why the slack it reports is in px·|w|.
    """
    if len(h) != 3 or any(len(row) != 3 for row in h):
        return False, f"H must be 3x3, got {len(h)} rows", None, ""
    flat = [v for row in h for v in row]
    if not all(_finite(v) for v in flat):
        return False, "H contains a non-finite or non-numeric entry", None, ""

    a, b, c = h[0][0], h[0][1], h[0][2]
    d, e, f = h[1][0], h[1][1], h[1][2]
    g, i, j = h[2][0], h[2][1], h[2][2]
    det = a * (e * j - f * i) - b * (d * j - f * g) + c * (d * i - e * g)
    if abs(det) <= DET_EPS:
        return False, f"H is singular (det={det!r}); it maps the plane to a line", None, ""

    expected, note = expected_marker_points()
    if expected is None:
        return True, "H is non-singular; corners not checked", None, note

    if len(corners) != 4:
        return False, f"corners must be 4 marker centres, got {len(corners)}", None, note
    for p in corners:
        if len(p) != 2 or not all(_finite(v) for v in p):
            return False, f"corner {p!r} is not a finite (x, y) pair", None, note

    worst: Optional[float] = None
    for (px, py), (bx, by) in zip(corners, expected):
        u = a * px + b * py + c
        v = d * px + e * py + f
        w = g * px + i * py + j
        if not (math.isfinite(u) and math.isfinite(v) and math.isfinite(w)):
            return False, "a corner maps to a non-finite point under H", worst, note
        aw = abs(w)
        if aw <= DET_EPS:
            return False, "a corner maps to the line at infinity under H", worst, note
        budget = H_TOL_PX * aw
        slack = min(budget - abs(u - bx * w), budget - abs(v - by * w))
        worst = slack if worst is None else min(worst, slack)

    if worst is not None and worst < 0:
        return (
            False,
            f"submitted corners do not land on the printed marker centres under "
            f"the submitted H: worst tolerance slack {worst!r} px*|w| "
            f"(budget {H_TOL_PX} px)",
            worst,
            note,
        )
    return True, "H is non-singular and explains the four marker centres", worst, note


@dataclass(frozen=True)
class ReplayResult:
    """The output of re-running the crossing predicate on submitted geometry."""

    committed: tuple[int, ...]
    frames: int
    uncounted: int
    without_tracker_id: int
    never_counted: int
    exceptions: tuple[str, ...]


def replay_crossings(
    crossings: Sequence[Crossing],
    *,
    min_crossing_frames: int = 3,
    untracked: Sequence[Sequence[Sequence[float]]] = (),
    zone: LineZone | None = None,
) -> ReplayResult:
    """Re-run the deterministic exit-crossing predicate over submitted tracks.

    This is the whole of INVARIANT 5's teeth: the same `sellevent.LineZone` the
    phone claims to have run, driven off the phone's own centroid tracks, on a
    machine that has never seen the camera. Nothing here is learned, sampled or
    thresholded at runtime, so the server's answer is reproducible line by line.

    A track only counts as sold when its OUT crossings outnumber its crossings
    back, which is what `net > 0` means below.
    """
    z = zone if zone is not None else LineZone.mat_exit_line(
        min_crossing_frames=int(min_crossing_frames)
    )
    frames = 0
    for c in crossings:
        frames = max(frames, len(c.path_mm))
    frames = max(frames, len(untracked))

    net: dict[int, int] = {int(c.track_id): 0 for c in crossings}
    for idx in range(frames):
        tracks: dict[int, tuple[float, float]] = {}
        for c in crossings:
            if idx < len(c.path_mm):
                point = c.path_mm[idx]
                tracks[int(c.track_id)] = (point[0], point[1])
        anon: list[tuple[float, float]] = []
        if idx < len(untracked):
            anon = [(p[0], p[1]) for p in untracked[idx]]
        result = z.update(tracks, untracked=tuple(anon))
        for tid in result.crossed_out:
            net[tid] = net.get(tid, 0) + 1
        for tid in result.crossed_back:
            net[tid] = net.get(tid, 0) - 1
    # retire everything still live: a track that vanished mid-crossing is an
    # uncounted sale, and silence about it is the bug this system exists for.
    z.flush()

    return ReplayResult(
        committed=tuple(sorted(tid for tid, n in net.items() if n > 0)),
        frames=frames,
        uncounted=z.crossed_without_tracker_id + z.detected_but_never_counted,
        without_tracker_id=z.crossed_without_tracker_id,
        never_counted=z.detected_but_never_counted,
        exceptions=tuple(str(e) for e in z.exceptions),
    )


def rerun_geometry(
    req: IntentRequest, book: PriceBook, *, zone: LineZone | None = None
) -> GeometryVerdict:
    """Recompute, server-side, everything the phone asserted. Pure function.

    Returns a verdict; it never mints, never touches the kernel and never talks
    to a gateway, so it is safe to call from a test with no I/O at all.
    """
    geo = req.geometry
    empty: tuple[str, ...] = ()

    def refuse(
        reason: str,
        detail: str,
        *,
        server: tuple[str, ...] = empty,
        client: tuple[str, ...] = empty,
        amber: tuple[str, ...] = empty,
        priced: tuple[str, ...] = empty,
        uncounted: int = 0,
        total: int = 0,
        frames: int = 0,
        h_ok: bool = True,
        slack: Optional[float] = None,
        note: str = "",
    ) -> GeometryVerdict:
        return GeometryVerdict(
            agrees=False,
            reason=reason,
            detail=detail,
            server_committed=server,
            client_committed=client,
            amber_items=amber,
            priced_items=priced,
            uncounted=uncounted,
            server_total_paise=total,
            frames=frames,
            homography_ok=h_ok,
            homography_slack_pxw=slack,
            homography_note=note,
        )

    seen_items: set[str] = set()
    seen_tracks: set[int] = set()
    for c in geo.crossings:
        if c.item_id in seen_items:
            return refuse(
                "duplicate_item_id",
                f"item_id {c.item_id!r} appears twice; one physical item is one line",
            )
        if int(c.track_id) in seen_tracks:
            return refuse(
                "duplicate_track_id",
                f"track_id {c.track_id} appears twice; two items cannot share a track",
            )
        seen_items.add(c.item_id)
        seen_tracks.add(int(c.track_id))

    h_ok, h_detail, slack, note = check_homography(geo.H, geo.corners)
    if not h_ok:
        return refuse("homography_rejected", h_detail, slack=slack, note=note, h_ok=False)

    replay = replay_crossings(
        geo.crossings,
        min_crossing_frames=int(geo.min_crossing_frames),
        untracked=geo.untracked,
        zone=zone,
    )
    by_track = {int(c.track_id): c for c in geo.crossings}
    server_committed = tuple(
        by_track[tid].item_id for tid in replay.committed if tid in by_track
    )
    client_committed = tuple(c.item_id for c in geo.crossings if c.committed)

    common = dict(
        server=server_committed,
        client=client_committed,
        uncounted=replay.uncounted,
        frames=replay.frames,
        slack=slack,
        note=note,
    )

    if set(server_committed) != set(client_committed):
        only_client = sorted(set(client_committed) - set(server_committed))
        only_server = sorted(set(server_committed) - set(client_committed))
        return refuse(
            "crossing_set_mismatch",
            "the server-side re-run of the crossing predicate disagrees with the "
            f"phone: claimed-but-not-observed={only_client}, "
            f"observed-but-not-claimed={only_server}",
            **common,
        )

    if replay.uncounted:
        return refuse(
            "uncounted_crossing",
            f"{replay.uncounted} crossing(s) could not be attributed "
            f"({replay.without_tracker_id} without a tracker id, "
            f"{replay.never_counted} never committed). Goods left the counter "
            "and the server cannot say which; nothing is minted against a total "
            "that is known to be incomplete.",
            **common,
        )

    priced: list[str] = []
    amber: list[str] = []
    total = 0
    for c in geo.crossings:
        if c.item_id not in set(server_committed):
            continue
        try:
            price = book_price_paise(book, c.item_id)
        except MoneyError as exc:
            # INVARIANT 1 at the price-book boundary. A book that answers with
            # 214.507 is refused whole; there is no partial total to report,
            # because the number that would anchor it is not money.
            return refuse(
                "bad_price_book",
                f"the price book answered for {c.item_id!r} with a value that is "
                f"not integer paise ({exc}); this counter will not truncate a "
                "price into a discount",
                amber=tuple(amber),
                priced=tuple(priced),
                total=0,
                **common,
            )
        if price is None:
            amber.append(c.item_id)
            if c.price_paise is not None:
                return refuse(
                    "price_disagreement",
                    f"the phone priced {c.item_id!r} at {c.price_paise} paise but "
                    "this counter cannot price it at all; a phone may agree with "
                    "the price book, never write to it",
                    amber=tuple(amber),
                    priced=tuple(priced),
                    total=total,
                    **common,
                )
            continue
        server_price = int(price)  # already through paise() in book_price_paise
        if c.price_paise is not None and int(c.price_paise) != server_price:
            return refuse(
                "price_disagreement",
                f"the phone priced {c.item_id!r} at {c.price_paise} paise; the "
                f"server's price book says {server_price} paise",
                amber=tuple(amber),
                priced=tuple(priced),
                total=total,
                **common,
            )
        priced.append(c.item_id)
        total += server_price

    if total <= 0:
        return refuse(
            "zero_total",
            f"every committed line abstained ({len(amber)} amber); there is "
            "nothing to charge for",
            amber=tuple(amber),
            priced=tuple(priced),
            total=total,
            **common,
        )
    if total != int(req.amount_paise):
        return refuse(
            "amount_disagreement",
            f"the phone asked to mint {int(req.amount_paise)} paise; the server "
            f"reprices the same crossings at {total} paise "
            f"(off by {int(req.amount_paise) - total})",
            amber=tuple(amber),
            priced=tuple(priced),
            total=total,
            **common,
        )

    return GeometryVerdict(
        agrees=True,
        reason="agreed",
        detail=(
            f"server re-ran {replay.frames} frames of the crossing predicate and "
            f"agrees: {len(priced)} priced line(s), {len(amber)} amber, "
            f"{total} paise"
        ),
        server_committed=server_committed,
        client_committed=client_committed,
        amber_items=tuple(amber),
        priced_items=tuple(priced),
        uncounted=replay.uncounted,
        server_total_paise=total,
        frames=replay.frames,
        homography_ok=True,
        homography_slack_pxw=slack,
        homography_note=note,
    )


# ---------------------------------------------------------------- service


def _payment_id_from_verified_body(raw: bytes) -> Optional[str]:
    """Pull the payment id out of a body whose signature has ALREADY passed.

    `parse_float=str` for the same reason `webhook` does it: no float object is
    ever materialised from an attacker-supplied document.
    """
    try:
        obj = json.loads(raw.decode("utf-8"), parse_float=str)
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return None
    for key in ("payment", "payment_link"):
        holder = payload.get(key)
        if isinstance(holder, dict):
            entity = holder.get("entity")
            if isinstance(entity, dict):
                pid = entity.get("id")
                if isinstance(pid, str) and pid:
                    return pid
    return None


class PaisaService:
    """The money service, without HTTP. `create_app` wraps it in FastAPI.

    Everything mutable is guarded by one re-entrant lock: an intent that
    re-prices a session and a webhook that settles it must not interleave.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        ledger: Ledger,
        kernel: _kernel.Kernel,
        gateway: Gateway,
        config: PaisaConfig,
        price_book: PriceBook | None = None,
        seen: SeenStore | None = None,
    ) -> None:
        config.assert_ready()
        self.clock = clock
        self.ledger = ledger
        self.kernel = kernel
        self.gateway = gateway
        self.config = config
        self.price_book: PriceBook = price_book or DictPriceBook({})
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}
        self._links: dict[str, dict] = {}
        self.predicate = GreenPredicate(
            self._open_intent_for_webhook, seen=seen, ledger=ledger, clock=clock
        )

    def __repr__(self) -> str:
        # config redacts itself; nothing else here holds a secret
        return (
            f"PaisaService(mode={self.config.mode!r}, "
            f"sessions={len(self._sessions)}, links={len(self._links)})"
        )

    # -- audit ----------------------------------------------------------

    def _audit(self, event: str, **fields: Any) -> str:
        """One ledger line. Never the secret, the signature or the raw body.

        Routed through the kernel when they share a ledger, because the kernel
        holds the cross-process file lock: `Ledger` caches its chain head in
        memory, so a service that appended around that lock would break the
        chain the moment a second process (a reconcile worker, a second uvicorn
        worker) touched the same file. Falls back to a direct append only when
        the two are demonstrably not the same ledger and clock.
        """
        kern = getattr(self, "kernel", None)
        if (
            kern is not None
            and getattr(kern, "ledger", None) is self.ledger
            and getattr(kern, "clock", None) is self.clock
            and hasattr(kern, "audit_append")
        ):
            return kern.audit_append(MODULE, event=event, **fields)
        return self.ledger.append(
            ts=self.clock.now_iso(), module=MODULE, event=event, **fields
        )

    # -- gateway documents ------------------------------------------------

    def stored_link(self, nonce: str) -> dict[str, Any]:
        """The minted link as this counter kept it: scrubbed, never the original.

        Exposed so the PII guarantee is testable from outside the class. If this
        ever returns a vpa, an email or a card, the scrub at the gateway
        boundary has regressed.
        """
        with self._lock:
            return json.loads(json.dumps(self._links.get(nonce) or {}))

    def _server_price(self, item_id: str) -> Optional[int]:
        """Price one item, insisting on integer paise (INVARIANT 1)."""
        try:
            return book_price_paise(self.price_book, item_id)
        except MoneyError as exc:
            raise PaisaRefusal(
                409,
                "bad_price_book",
                f"the price book answered for {item_id!r} with a value that is "
                f"not integer paise ({exc})",
                item_id=item_id,
            ) from exc

    # -- kernel adapters -------------------------------------------------

    def _open_kernel_intent(self, session_id: str) -> Optional[_kernel.Intent]:
        for it in self.kernel.all_intents():
            if it.session_id == session_id and it.state in OPEN_STATES:
                return it
        return None

    def _open_intent_for_webhook(self, session_id: str) -> Optional[WebhookIntent]:
        """Adapter for the green predicate.

        The kernel's vocabulary is NEW/CALLING/SETTLED/...; the predicate's is
        OPEN. Translating here means a SETTLED intent is simply invisible to the
        predicate, so a second webhook for a paid session can never re-green it.
        """
        it = self._open_kernel_intent(session_id)
        if it is None:
            return None
        return WebhookIntent(
            session_id=it.session_id, amount_paise=int(it.amount_paise), state="OPEN"
        )

    # -- sessions --------------------------------------------------------

    def _session(self, session_id: str) -> Session:
        sess = self._sessions.get(session_id)
        if sess is None:
            sess = Session(self.clock, self.ledger, session_id=session_id)
            self._sessions[session_id] = sess
        return sess

    def has_session(self, session_id: str) -> bool:
        return session_id in self._sessions

    # -- POST /intent ----------------------------------------------------

    def create_intent(self, req: IntentRequest) -> dict[str, Any]:
        with self._lock:
            try:
                amount = int(paise(req.amount_paise))
            except MoneyError as exc:
                raise PaisaRefusal(422, "bad_amount", str(exc)) from exc
            if amount <= 0:
                raise PaisaRefusal(
                    422, "bad_amount", f"a debit must be positive, got {amount} paise"
                )

            # INVARIANT 5. Re-run everything the phone asserted, BEFORE the
            # kernel is touched and long before a gateway is called.
            verdict = rerun_geometry(req, self.price_book)
            if not verdict.agrees:
                self._audit(
                    "intent.refused",
                    session_id=req.session_id,
                    reason=verdict.reason,
                    requested_paise=amount,
                    server_total_paise=verdict.server_total_paise,
                    server_committed=list(verdict.server_committed),
                    client_committed=list(verdict.client_committed),
                    uncounted=verdict.uncounted,
                    frames=verdict.frames,
                    homography_ok=verdict.homography_ok,
                    minted=False,
                )
                raise PaisaRefusal(
                    409,
                    verdict.reason,
                    verdict.detail,
                    session_id=req.session_id,
                    requested_paise=amount,
                    geometry=verdict.as_dict(),
                )

            sess = self._session(req.session_id)
            if not sess.mat_locked:
                # a homography that explains the four printed markers IS the lock
                sess.on_mat_lock(True)

            known = {li.item_id for li in sess.line_items}
            committed_now = set(verdict.server_committed)
            for c in req.geometry.crossings:
                if c.item_id not in committed_now or c.item_id in known:
                    continue
                sess.on_placement(
                    Placement(
                        item_id=c.item_id,
                        name=c.name,
                        price_paise=self._server_price(c.item_id),
                    )
                )
                sess.on_exit(c.item_id)

            done = sess.on_done()
            session_total = int(sess.total_paise)
            if session_total != amount:
                self._audit(
                    "intent.refused",
                    session_id=req.session_id,
                    reason="session_total_disagreement",
                    requested_paise=amount,
                    session_total_paise=session_total,
                    session_state=sess.state.value,
                    minted=False,
                )
                raise PaisaRefusal(
                    409,
                    "session_total_disagreement",
                    f"the session's own total is {session_total} paise, not "
                    f"{amount}. The basket on this counter is not the basket the "
                    "phone described; nothing is minted.",
                    session_id=req.session_id,
                    session_state=sess.state.value,
                    session_total_paise=session_total,
                )
            if sess.intent_amount_paise is None:
                raise PaisaRefusal(
                    409,
                    "basket_locked",
                    f"the session refused to close its basket "
                    f"({done.reason}); nothing is minted.",
                    session_id=req.session_id,
                    session_state=sess.state.value,
                )

            intent = self.kernel.create_intent(req.session_id, amount)
            if intent.state == _kernel.NEW:
                # commit CALLING, close the DB, THEN call out. The kernel's rule.
                intent = self.kernel.mark_calling(intent.nonce)

            link = self._links.get(intent.nonce)
            replayed = link is not None
            if link is None:
                notes = {
                    "session_id": req.session_id,
                    "nonce": intent.nonce,
                    "cycle": str(intent.cycle),
                    "gawaah": "v1",
                }
                try:
                    link = self.gateway.create_payment_link(
                        amount_paise=amount,
                        notes=notes,
                        reference_id=intent.nonce,
                        description="GAWAAH counter",
                        idempotent=True,
                    )
                except Exception as exc:
                    # An indeterminate call is NEVER a failure; park it for the
                    # kernel's retrieve sweep rather than blind-retrying a debit.
                    self.kernel.mark_indeterminate(
                        intent.nonce, reason=type(exc).__name__
                    )
                    self._audit(
                        "intent.gateway_error",
                        session_id=req.session_id,
                        nonce=intent.nonce,
                        amount_paise=amount,
                        error=type(exc).__name__,
                        minted=False,
                    )
                    raise PaisaRefusal(
                        502,
                        "gateway_error",
                        f"the gateway call did not complete ({type(exc).__name__}); "
                        "the intent is parked as INDETERMINATE for reconciliation, "
                        "not retried.",
                        session_id=req.session_id,
                        nonce=intent.nonce,
                    ) from exc
                if not isinstance(link, Mapping):
                    raise PaisaRefusal(
                        502,
                        "gateway_error",
                        f"the gateway returned {type(link).__name__}, not a "
                        "payment-link document; nothing is minted.",
                        session_id=req.session_id,
                        nonce=intent.nonce,
                    )
                # PRD 9. The ONE boundary where a gateway document enters this
                # process. Everything below stores, audits or returns `link`, so
                # the customer is dropped here or not at all.
                link = strip_pii(link)
                self._links[intent.nonce] = link

            body = {
                "session_id": req.session_id,
                "nonce": intent.nonce,
                "state": intent.state,
                "amount_paise": amount,
                "amount_rupees": to_rupees_str(amount),
                "payment_link_id": link.get("id"),
                "short_url": link.get("short_url"),
                "reference_id": link.get("reference_id"),
                "replayed": replayed,
                "session_state": sess.state.value,
                "amber_items": list(verdict.amber_items),
                "priced_items": list(verdict.priced_items),
                "geometry": verdict.as_dict(),
            }
            self._audit(
                "intent.minted",
                session_id=req.session_id,
                nonce=intent.nonce,
                amount_paise=amount,
                payment_link_id=link.get("id"),
                priced_items=list(verdict.priced_items),
                amber_items=list(verdict.amber_items),
                frames=verdict.frames,
                replayed=replayed,
                minted=True,
            )
            return body

    # -- POST /webhook ---------------------------------------------------

    def handle_webhook(
        self,
        raw_body: bytes,
        signature: str,
        *,
        header_event_id: Optional[str] = None,
        mirror_stale: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """Verify, adjudicate, settle. Returns (http_status, body).

        The caller must hand this the RAW BYTES off the wire. Nothing above
        `GreenPredicate.evaluate` parses them.
        """
        with self._lock:
            verdict: GreenVerdict = self.predicate.evaluate(
                raw_body,
                signature,
                self.config.effective_webhook_secret,
                header_event_id=header_event_id,
                mirror_stale=mirror_stale,
            )

            settled_nonce: Optional[str] = None
            payment_id: Optional[str] = None
            if verdict.green and verdict.session_id is not None:
                payment_id = _payment_id_from_verified_body(raw_body) or verdict.event_id
                it = self._open_kernel_intent(verdict.session_id)
                if it is not None and payment_id:
                    try:
                        settled = self.kernel.mark_settled(it.nonce, payment_id)
                        settled_nonce = settled.nonce
                    except _kernel.KernelError as exc:
                        self._audit(
                            "webhook.settle_refused",
                            session_id=verdict.session_id,
                            nonce=it.nonce,
                            error=type(exc).__name__,
                            detail=str(exc),
                        )

            session_state: Optional[str] = None
            transition_reason: Optional[str] = None
            sid = verdict.session_id
            sess = self._sessions.get(sid) if sid else None
            if sess is not None and verdict.signature_valid and verdict.event_id:
                t = sess.on_webhook(
                    Verdict(
                        event_id=verdict.event_id,
                        event=verdict.event or "",
                        session_id=sid or "",
                        amount_paise=verdict.amount_paise,
                        green=bool(verdict.green),
                        signature_valid=True,
                        reason=verdict.reason,
                    )
                )
                session_state = sess.state.value
                transition_reason = t.reason

            if verdict.reason == "bad_signature":
                status = 400
            elif verdict.reason == "secret_not_configured":
                status = 503
            else:
                status = 200

            body = {
                "green": bool(verdict.green),
                "reason": verdict.reason,
                "severity": verdict.severity,
                "signature_valid": bool(verdict.signature_valid),
                "event": verdict.event,
                "event_id": verdict.event_id,
                "session_id": verdict.session_id,
                "amount_paise": verdict.amount_paise,
                "expected_paise": verdict.expected_paise,
                "body_sha256": verdict.body_sha256,
                "settled_nonce": settled_nonce,
                "payment_id": payment_id,
                "session_state": session_state,
                "session_reason": transition_reason,
                "detail": verdict.detail,
            }
            self._audit(
                "webhook.handled",
                green=bool(verdict.green),
                reason=verdict.reason,
                status=status,
                session_id=verdict.session_id,
                settled_nonce=settled_nonce,
                session_state=session_state,
                body_sha256=verdict.body_sha256,
            )
            return status, body

    # -- GET /session/{id} -----------------------------------------------

    def session_view(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None:
                raise PaisaRefusal(
                    404,
                    "unknown_session",
                    f"no session {session_id!r} on this counter",
                    session_id=session_id,
                )
            snap = sess.snapshot()
            intents = [
                {
                    "nonce": it.nonce,
                    "state": it.state,
                    "amount_paise": int(it.amount_paise),
                    "amount_rupees": to_rupees_str(int(it.amount_paise)),
                    "payment_id": it.payment_id,
                    "attempts": it.attempts,
                    "needs_human": bool(it.needs_human),
                    "short_url": (self._links.get(it.nonce) or {}).get("short_url"),
                    "payment_link_id": (self._links.get(it.nonce) or {}).get("id"),
                }
                for it in self.kernel.all_intents()
                if it.session_id == session_id
            ]
            total = int(sess.total_paise)
            return {
                **snap,
                "total_rupees": to_rupees_str(total),
                "paid": snap["state"] == "PAID",
                "line_items": [
                    {
                        "item_id": li.item_id,
                        "name": li.name,
                        "price_paise": li.price_paise,
                        "reason": li.reason,
                        "committed": li.committed,
                        "reverted": li.reverted,
                        "amber": li.amber,
                        "counts": li.counts,
                    }
                    for li in sess.line_items
                ],
                "intents": intents,
            }

    # -- GET /health -----------------------------------------------------

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "ok": True,
                "module": MODULE,
                "mode": self.config.mode,
                "key_id": self.config.key_id,
                # booleans. Never the values, never a prefix, never a length.
                "key_secret_configured": self.config.key_secret_configured,
                "webhook_secret_configured": self.config.webhook_secret_configured,
                "sessions": len(self._sessions),
                "intents": self.kernel.count(),
                # An escalated intent is a person's job, so it has to be visible
                # to a person. A number nobody can see is not an escalation.
                "intents_needing_human": len(self.kernel.intents_needing_human()),
                "intents_escalated": len(self.kernel.escalated_intents()),
                "payment_links": len(self._links),
                "ledger_lines": self.ledger.count,
                "ledger_head": self.ledger.head,
                "price_book_entries": (
                    len(self.price_book) if hasattr(self.price_book, "__len__") else None
                ),
            }


# ---------------------------------------------------------------- assembly


def build_service(
    *,
    data_dir: str,
    clock: Clock | None = None,
    config: PaisaConfig | None = None,
    price_book: PriceBook | None = None,
    gateway: Gateway | None = None,
    live_factory: Callable[[PaisaConfig], Gateway] | None = None,
) -> PaisaService:
    """Wire a service against a directory. Every dependency stays injectable."""
    cfg = config if config is not None else PaisaConfig.from_env()
    cfg.assert_ready()
    clk = clock if clock is not None else RealClock()
    os.makedirs(data_dir, exist_ok=True)
    ledger = Ledger(os.path.join(data_dir, "audit.jsonl"))
    kern = _kernel.Kernel(os.path.join(data_dir, "kernel.db"), clk, ledger)
    kern.recover()  # a CALLING row at startup is indeterminate, not a retry
    gw = gateway if gateway is not None else build_gateway(
        cfg, clk, live_factory=live_factory
    )
    return PaisaService(
        clock=clk,
        ledger=ledger,
        kernel=kern,
        gateway=gw,
        config=cfg,
        price_book=price_book,
    )


def create_app(service: PaisaService | None = None) -> FastAPI:
    """Build the ASGI app. Import-time side effects: none."""
    svc = service if service is not None else build_service(
        data_dir=os.environ.get("GAWAAH_DATA_DIR", "results")
    )
    app = FastAPI(
        title="GAWAAH paisa",
        summary="The money service. Sole holder of secrets.",
        version="0.1.0",
    )
    app.state.service = svc

    @app.exception_handler(PaisaRefusal)
    async def _refusal(_: Request, exc: PaisaRefusal) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=exc.body())

    @app.post("/intent")
    async def post_intent(req: IntentRequest) -> dict[str, Any]:
        return svc.create_intent(req)

    @app.post("/webhook")
    async def post_webhook(request: Request) -> JSONResponse:
        # RAW BYTES FIRST. FastAPI is given no body model for this route, so
        # nothing has parsed, coerced or validated the payload above this line.
        raw = await request.body()
        signature = request.headers.get("X-Razorpay-Signature", "")
        event_id = request.headers.get("X-Razorpay-Event-Id")
        stale = request.headers.get("X-Gawaah-Mirror-Stale", "").lower() in (
            "1",
            "true",
            "yes",
        )
        status, body = svc.handle_webhook(
            raw, signature, header_event_id=event_id, mirror_stale=stale
        )
        return JSONResponse(status_code=status, content=body)

    @app.get("/session/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        return svc.session_view(session_id)

    @app.get("/health")
    async def get_health() -> dict[str, Any]:
        return svc.health()

    return app


__all__ = [
    "Crossing",
    "DictPriceBook",
    "Gateway",
    "Geometry",
    "GeometryVerdict",
    "IntentRequest",
    "PaisaConfig",
    "PaisaConfigError",
    "PaisaRefusal",
    "PaisaService",
    "PII_DROPPED_KEY",
    "PII_FIELDS",
    "PriceBook",
    "ReplayResult",
    "REFUSAL_CODES",
    "book_price_paise",
    "build_gateway",
    "build_service",
    "check_homography",
    "create_app",
    "expected_marker_points",
    "replay_crossings",
    "rerun_geometry",
    "strip_pii",
]
