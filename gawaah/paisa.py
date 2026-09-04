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

import datetime as _dt

import json
import math
import os
import re
import threading
from dataclasses import dataclass
from collections import Counter
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
from .webhook import CollectionPredicate, CollectionVerdict, GreenPredicate, GreenVerdict, SeenStore
from .webhook import Intent as WebhookIntent
from .webhook import RefundPredicate, RefundVerdict

MODULE = "paisa"

#: Kernel states in which an intent is still awaiting an answer from Razorpay.
#: These are the only states that may be presented to the green predicate as
#: an OPEN intent. BOOKED is deliberately absent: a webhook naming a booked
#: bill's session finds no open intent and is refused `unknown_session`, so
#: nothing that arrives for the collection link can ever green the bill.
OPEN_STATES = frozenset({_kernel.NEW, _kernel.CALLING})

# ---------------------------------------------------------------- khata
#
# A book id as `gawaah/khata.py` mints them. The till keeps the customer (name,
# phone) behind this id in the shop directory; this process sees the id and
# nothing else, and the audit chain carries the id and nothing else.
BOOK_ID_RE = re.compile(r"^bk_[0-9a-f]{8,64}$")
COLLECTION_ID_RE = re.compile(r"^col_[0-9a-f]{8,64}$")
#: How long a collection link stays payable, and reminded about, by default.
#: A week: long enough for Razorpay's reminder cadence to do its work, short
#: enough that a stale balance is re-collected on a fresh link with a fresh
#: amount rather than paid against last month's.
COLLECTION_EXPIRE_S = 7 * 24 * 3600
#: The gateway's own floor on `expire_by`: fifteen minutes.
COLLECTION_MIN_EXPIRE_S = 15 * 60
#: The first instalment must be at least this, or a quarter of the balance,
#: whichever is larger — rounded down to whole rupees. A Rs 5 "part payment"
#: against Rs 650 is a reminder that has not worked, not a collection.
MIN_PARTIAL_FLOOR_PAISE = 100 * 100
_SECONDS = _dt.timedelta(seconds=1)
_EPOCH = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)


def _unix_seconds(iso: str) -> int:
    """ISO-8601 -> whole unix seconds, without a float: timedelta // timedelta."""
    dt = _dt.datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return (dt - _EPOCH) // _SECONDS


def first_min_partial_paise(outstanding: int) -> int:
    """The floor on a customer's first instalment, in whole rupees."""
    quarter = (outstanding // 4) // 100 * 100
    floor = max(MIN_PARTIAL_FLOOR_PAISE, quarter)
    return min(outstanding, floor)

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
        # KHATA
        "bad_book_id",
        "bill_already_minted",
        "bill_on_the_book",
        "bill_booked_elsewhere",
        "nothing_outstanding",
        "outstanding_disagreement",
        "collection_link_already_open",
        "unknown_collection",
        "not_a_simulator",
        # MILAN
        "recon_unavailable",
        "bad_day",
        "unknown_nonce",
        "nothing_to_settle",
        "not_reconcilable",
        # WAAPSI
        "bill_not_settled",
        "item_not_on_this_bill",
        "already_refunded",
        "amount_disagrees",
        "refund_exceeds_bill",
        "line_unpriced",
        "unknown_refund",
        "chain_unavailable",
        "refund_not_requested",
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


class ScanRef(BaseModel):
    """A REFERENCE to a witness the till already wrote down. Not the evidence.

    The browser sends an id and nothing else it could have authored: no
    payloads, no sku ids, no prices. paisa loads the witness from disk, then
    re-resolves every payload through its OWN binding table and re-prices every
    line through its OWN price book before a rupee is minted. That is invariant
    5 in the form that matters here — the server re-derives the bill from its
    own tables — and it is stronger than re-reading bytes the browser handed
    over, because the browser is removed from authorship entirely.

    Deliberately NOT re-decoding the image inside paisa: this module imports
    the vision stack lazily so the money service starts on a box with no
    camera, and making a decoder a precondition of minting would turn an outage
    in the vision stack into an outage in payments.
    """

    model_config = ConfigDict(extra="forbid")

    scan_id: StrictStr


class IntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: StrictStr
    #: StrictInt, so 21450.0 is a 422 at the boundary and never becomes money.
    amount_paise: StrictInt
    #: EXACTLY ONE of these. `geometry` is the mat: a homography, four marker
    #: centres and the tracks that crossed the exit line. `scan` names a
    #: witness of printed product codes. While geometry was REQUIRED a basket
    #: of barcodes could not be minted at all, which is why no page in this
    #: program had ever posted a mint.
    geometry: Optional[Geometry] = None
    scan: Optional[ScanRef] = None


class BookRequest(BaseModel):
    """KHATA: close a bill onto a customer's book instead of onto a link.

    The same evidence rule as a mint — a scan witness this counter wrote, re-
    derived here from its own tables — because a bill on the book is a debt
    the customer will be asked to pay, and a debt the browser authored is a
    number nobody witnessed. `book_id` is the till's opaque handle for the
    customer; no name and no phone reach this process for a booking.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: StrictStr
    amount_paise: StrictInt
    scan: ScanRef
    book_id: StrictStr


class CollectCustomer(BaseModel):
    """Who Razorpay should remind. Passed to the gateway and NOT kept: the
    entity that comes back is scrubbed by `strip_pii` like every other."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[StrictStr] = None
    contact: Optional[StrictStr] = None


class CollectRequest(BaseModel):
    """KHATA: mint ONE Payment Link for a book's whole outstanding balance.

    `amount_paise` is the till's figure and is compared, never used: the link
    is minted for what THIS kernel's own rows say is outstanding, and a
    disagreement is a refusal (`outstanding_disagreement`), the same way a
    basket total that disagrees with the witness is.
    """

    model_config = ConfigDict(extra="forbid")

    book_id: StrictStr
    amount_paise: StrictInt
    customer: Optional[CollectCustomer] = None
    #: Seconds until the link expires. Default a week; floor the gateway's.
    expire_in_s: Optional[StrictInt] = None


class SimPayRequest(BaseModel):
    """Simulator only: the customer pays some of a link. Refused in live mode."""

    model_config = ConfigDict(extra="forbid")

    payment_link_id: StrictStr
    amount_paise: Optional[StrictInt] = None
    #: MILAN. `false` is the dead tunnel: the simulator moves the money and
    #: signs the webhook, and the webhook is NOT pushed through this process
    #: — the same thing the simulator's `timeout` mode does, reachable per
    #: payment so a demo can show one customer paying while nobody was
    #: listening and the settlement match finding it the next morning.
    #: Nothing is constructed or forged here; a delivery is simply withheld.
    webhook: Optional[StrictBool] = None


class RefundRequest(BaseModel):
    """WAAPSI: send one line of a settled bill back through the gateway.

    The till names the bill, the line and the paise it believes were charged.
    None of it is trusted: paisa finds the SETTLED intent itself, re-reads
    the line and its charged price off the hash chain (never the catalogue —
    the customer paid the offer price on the day, and that is what goes
    back), and refuses by name on any disagreement. `amount_paise` is
    compared, never used.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: StrictStr
    item_id: StrictStr
    sku_id: StrictStr
    amount_paise: StrictInt


class SimRefundRequest(BaseModel):
    """Simulator only: the gateway's back office processes (or fails) a
    refund it was asked for, and pushes the signed callback. Refused in live
    mode, where the gateway does this on its own clock."""

    model_config = ConfigDict(extra="forbid")

    refund_key: StrictStr
    outcome: StrictStr = "processed"


class ReconSettleRequest(BaseModel):
    """MILAN: settle ONE intent from the gateway's own record of its nonce.

    A nonce and nothing else. There is no amount here on purpose: the figure
    the intent settles for is the one the kernel row already holds, checked
    by `kernel.reconcile` against what the gateway's read-only lookup says
    landed, and a disagreement parks the row for a person.
    """

    model_config = ConfigDict(extra="forbid")

    nonce: StrictStr


# ---------------------------------------------------------------- geometry


@dataclass(frozen=True)
class ScanVerdict:
    """The server's own answer about a scan witness, re-derived from its tables."""

    agrees: bool
    reason: str
    detail: str
    server_lines: tuple[str, ...]
    priced_items: tuple[str, ...]
    amber_items: tuple[str, ...]
    server_total_paise: int
    witnessed_paise: int
    declared_paise: int
    codes_found: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "agrees": self.agrees,
            "reason": self.reason,
            "detail": self.detail,
            "server_lines": list(self.server_lines),
            "priced_items": list(self.priced_items),
            "amber_items": list(self.amber_items),
            "server_total_paise": int(self.server_total_paise),
            "witnessed_paise": int(self.witnessed_paise),
            "declared_paise": int(self.declared_paise),
            "codes_found": int(self.codes_found),
        }


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


SCAN_DIR_ENV = "GAWAAH_SCAN_DIR"
DEFAULT_SCAN_DIR = "results/scans"


def load_scan_witness(scan_id: str, data_dir: Optional[str] = None) -> Optional[dict]:
    """The witness the till wrote, loaded BY ID. None if there is no such scan.

    A path is built from a sanitised id and nothing else: the id is checked
    against a strict charset before it is joined, so no caller can walk out of
    the scan directory with dots or slashes.
    """
    import os
    import re as _re

    if not _re.match(r"^[A-Za-z0-9_-]{6,64}$", scan_id or ""):
        return None
    base = os.environ.get(SCAN_DIR_ENV) or (
        os.path.join(data_dir, "scans") if data_dir else DEFAULT_SCAN_DIR)
    path = os.path.join(base, f"{scan_id}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.loads(fh.read())
    except Exception:  # noqa: BLE001 - a missing or unreadable witness is "no witness"
        return None


def load_code_bindings(data_dir: Optional[str] = None) -> dict[str, str]:
    """paisa's OWN copy of {printed code -> sku id}. Read fresh, every mint.

    The witness names a sku; this is what decides whether that name is true.
    Read at mint time rather than cached, because a binding changed between the
    scan and the charge must refuse the mint, not price the old answer.
    """
    import os

    base = os.environ.get("GAWAAH_CODES_FILE")
    if not base:
        base = os.path.join(data_dir or ".", "shop", "product_codes.json")
    try:
        with open(base, encoding="utf-8") as fh:
            data = json.loads(fh.read())
        codes = data.get("codes") if isinstance(data, dict) else None
        return {str(k): str(v) for k, v in (codes or {}).items()}
    except Exception:  # noqa: BLE001 - no table means nothing resolves
        return {}


def _witness_age_s(doc: dict) -> Optional[int]:
    """Seconds since the counter wrote this witness down, or None if unknowable.

    Returns None rather than 0 when the timestamp is missing or unparseable:
    the caller treats None as stale, because an age that cannot be established
    is not the same thing as an age of zero.
    """
    raw = doc.get("at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        seen = _dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=_dt.timezone.utc)
    delta = _dt.datetime.now(_dt.timezone.utc) - seen
    # int() first: seconds are a count, and nothing downstream may see a float.
    return int(delta.days) * 86400 + int(delta.seconds)


def rerun_scan(req: "IntentRequest", price_book: "PriceBook",
               *, data_dir: Optional[str] = None,
               max_age_s: int = 900) -> ScanVerdict:
    """INVARIANT 5 for the code path: re-derive the bill from paisa's own tables.

    The client sends a scan id and an amount. Everything else is recomputed
    here: the witness is loaded by id, every payload is re-resolved through
    paisa's own binding table (the witness's claimed sku is compared, never
    trusted), every line is re-priced through paisa's own price book, and the
    total is summed as Python ints. A one-paisa disagreement refuses.

    An AMBER line — a code that decoded but resolves to nothing priced — does
    not silently drop out of the total. It BLOCKS the mint, because a bill that
    is short by silence is the worst thing this program can produce.

    A line the counter named BY APPEARANCE carries no payload to re-resolve, so
    it is re-priced by sku through paisa's own book and its recorded evidence is
    checked against the gate the counter applied. See the branch below, which
    says plainly what that does and does not prove.
    """
    scan = req.scan
    assert scan is not None                      # caller checks; this is the shape
    doc = load_scan_witness(scan.scan_id, data_dir)
    if doc is None:
        return ScanVerdict(False, "scan_not_found",
                           f"no scan witness {scan.scan_id!r} on this counter",
                           (), (), (), 0, 0, 0, 0)

    # AGE IS COMPUTED HERE, FROM THE TIMESTAMP THE COUNTER WROTE DOWN.
    #
    # It used to read a field called `age_s` off the witness. The only writer of
    # that field set it to the literal 0 at the moment of writing, so `age >
    # max_age_s` was never true and this gate had never once fired: every scan
    # witness ever written was a permanent charge voucher, mintable a day later.
    # The test that "proved" the branch injected an age_s of its own and wrote
    # no timestamp at all, so it exercised a path the counter never takes.
    #
    # FAIL CLOSED. A witness with no readable timestamp cannot be shown to be
    # fresh, and an unprovable age on the money path is a refusal, not a pass.
    age = _witness_age_s(doc)
    if age is None:
        return ScanVerdict(False, "stale_witness",
                           "that scan carries no readable timestamp, so its age "
                           "cannot be established", (), (), (), 0, 0, 0, 0)
    if age > max_age_s:
        return ScanVerdict(False, "stale_witness",
                           f"that scan is {age}s old; a basket must be minted "
                           f"within {max_age_s}s of being seen", (), (), (), 0, 0, 0, 0)

    bindings = load_code_bindings(data_dir)
    lines = doc.get("lines") or []
    priced: list[str] = []
    amber: list[str] = []
    names: list[str] = []
    witnessed = 0

    for ln in lines:
        payload = str((ln or {}).get("code") or "")
        claimed = (ln or {}).get("sku_id")
        named_by = str((ln or {}).get("named_by") or ("code" if payload else ""))

        if named_by == "appearance" and not payload:
            # A LINE THE COUNTER NAMED BY LOOKING, NOT BY READING.
            #
            # 34 of 36 products in a seeded shop carry no printed label, and a
            # shopkeeper who teaches from a photograph creates more of them. For
            # the whole life of this function such a line had no payload, missed
            # the binding table, and fell into `amber` — so every appearance-only
            # bill refused with `amber_in_basket` and the till could not take
            # money for anything it recognised by camera.
            #
            # WHAT PAISA STILL DOES ITSELF, AND WHAT IT CANNOT.
            # The PRICE is still paisa's alone: the sku is re-priced through its
            # own book and the till's figure is never read. What paisa cannot do
            # is re-derive the IDENTITY — it has no camera and no gallery, and
            # re-running an embedder here would put the shop's catalogue inside
            # the one process that holds gateway keys. So it checks the EVIDENCE
            # instead: the counter recorded the similarity it measured and the
            # gate it applied, and a line that does not clear its own stated gate
            # is refused rather than priced. That is weaker than re-resolving a
            # payload and it is written down here as weaker.
            #
            # The browser is still not an author. This witness was written
            # server-side by the till under an id; the page sends the id.
            sku = str(claimed) if claimed else None
            if sku is None:
                amber.append("?")
                continue
            # BASIS POINTS, AS INTEGERS. This module holds no float — the
            # lint fails the build on one — so the counter sends the similarity
            # it measured and the gate it applied as ints, and they are compared
            # as ints. A missing or unreadable pair is a refusal, not a pass.
            top1_bp = (ln or {}).get("top1_bp")
            gate_bp = (ln or {}).get("phi_bp")
            cleared = False
            if isinstance(top1_bp, int) and isinstance(gate_bp, int):
                cleared = top1_bp >= gate_bp
            if not cleared:
                return ScanVerdict(
                    False, "appearance_evidence_missing",
                    f"the counter recorded {sku!r} as recognised by appearance "
                    f"but the similarity it measured ({top1_bp!r} bp) does not "
                    f"clear the gate it applied ({gate_bp!r} bp). Nothing is "
                    f"minted on a likeness the counter cannot show its working "
                    f"for.",
                    (), (), (), 0, 0, 0, len(lines))
            price = book_price_paise(price_book, sku)
            if price is None:
                amber.append(sku)
                continue
            witnessed += int(price)
            priced.append(sku)
            names.append(sku)
            continue

        sku = bindings.get(payload)
        if sku is None and isinstance(payload, str) and payload.lower().startswith("gawaah:"):
            sku = payload[len("gawaah:"):].strip() or None
        if sku is None:
            amber.append(payload or "?")
            continue
        if claimed is not None and str(claimed) != sku:
            return ScanVerdict(
                False, "code_names_a_different_product",
                f"the till recorded {payload!r} as {claimed!r}; this counter's "
                f"table says {sku!r}. The binding changed between the scan and "
                f"the charge, so nothing is minted.",
                (), (), (), 0, 0, 0, len(lines))
        price = book_price_paise(price_book, sku)
        if price is None:
            amber.append(payload)
            continue
        witnessed += int(price)
        priced.append(sku)
        names.append(sku)

    if amber:
        return ScanVerdict(
            False, "amber_in_basket",
            f"{len(amber)} line(s) on this counter cannot be priced "
            f"({', '.join(amber[:4])}{'…' if len(amber) > 4 else ''}). They are "
            f"NOT dropped from the total — the mint is refused until each one is "
            f"taught or removed deliberately, because a bill that is short by "
            f"silence looks exactly like a complete one.",
            tuple(names), tuple(priced), tuple(amber), witnessed, witnessed, 0, len(lines))

    if not priced:
        return ScanVerdict(False, "zero_total",
                           "nothing on this counter could be priced; nothing is minted",
                           (), (), (), 0, 0, 0, len(lines))

    total = witnessed
    if int(req.amount_paise) != total:
        return ScanVerdict(
            False, "scan_total_disagreement",
            f"this counter re-priced the basket at {total} paise from its own "
            f"price book; the till asked for {int(req.amount_paise)}. Nothing is "
            f"minted.",
            tuple(names), tuple(priced), (), total, witnessed, 0, len(lines))

    return ScanVerdict(True, "agrees",
                       f"{len(priced)} line(s) re-priced from this counter's own "
                       f"tables and they agree",
                       tuple(names), tuple(priced), (), total, witnessed, 0,
                       int(doc.get("codes_found") or len(lines)))


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
        data_dir: str | None = None,
    ) -> None:
        config.assert_ready()
        #: Where this counter keeps its own tables — the scan witnesses it
        #: loads by id and the code bindings it re-resolves through. Held so a
        #: mint never has to be told where the truth lives.
        self._data_dir = data_dir
        self.clock = clock
        self.ledger = ledger
        self.kernel = kernel
        self.gateway = gateway
        self.config = config
        self.price_book: PriceBook = price_book or DictPriceBook({})
        self._lock = threading.RLock()
        #: WHEN THIS COUNTER LAST HEARD FROM THE GATEWAY AT ALL.
        #:
        #: A pay screen that only knows "not green yet" cannot tell a customer
        #: who has not paid from a tunnel that has been dead for two days —
        #: and it showed the identical spinner for both. It spun for 78 s on a
        #: payment that HAD settled, because cloudflared's quick tunnel had been
        #: revoked and was looping on "Unauthorized: Tunnel not found", so
        #: Razorpay's callback had nowhere to land.
        #:
        #: This records every webhook that reaches the door, INCLUDING the ones
        #: rejected for a bad signature: the question it answers is "can anything
        #: get here at all", and a forged POST proves reachability just as well
        #: as a genuine one. It is a liveness fact, never an authorisation.
        self._last_webhook_iso: Optional[str] = None
        self._last_webhook_green_iso: Optional[str] = None
        # PROCESS-LIFETIME, and every reader must say so. This is a plain
        # attribute: it is never persisted and never reloaded, so it returns
        # to zero on every restart while the hash chain — which IS the record
        # of what has ever happened — still holds the settlements. `manage.py`
        # once turned `seen <= 0` into the word "ever" and raised a red alarm
        # on a counter that had settled a bill. Anything derived from this
        # number may only speak about the current process.
        self._webhooks_seen: int = 0
        self._sessions: dict[str, Session] = {}
        self._links: dict[str, dict] = {}
        #: KHATA: collection_id -> the scrubbed link entity. Process-lifetime,
        #: like `_links`; the durable record is the kernel's collections row.
        self._collection_links: dict[str, dict] = {}
        self.predicate = GreenPredicate(
            self._open_intent_for_webhook, seen=seen, ledger=ledger, clock=clock
        )
        #: KHATA's own predicate. It shares the signature gate and nothing
        #: else with the green one — see webhook.py, "collections".
        self.collection_predicate = CollectionPredicate(self._collection_known)
        #: WAAPSI's own predicate, same discipline — see webhook.py, "refunds".
        self.refund_predicate = RefundPredicate(self._refund_lookup)

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

            # EXACTLY ONE KIND OF EVIDENCE. Both absent means nothing was
            # witnessed; both present means two stories about one basket, and
            # picking either would be choosing which to believe.
            if (req.geometry is None) == (req.scan is None):
                raise PaisaRefusal(
                    422, "one_evidence_required",
                    "an intent carries EITHER mat geometry OR a scan witness, "
                    "and this one carries "
                    + ("neither" if req.geometry is None else "both") + ".")

            # THE CODE PATH. The client sent an id and an amount; everything
            # else is re-derived here from this counter's own binding table and
            # its own price book, before the kernel is touched.
            if req.scan is not None:
                sess, sv = self._scan_session(req, amount)
                return self._mint(req, amount, sess,
                                  evidence={"scan": sv.as_dict()})

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

            return self._mint(req, amount, sess,
                              evidence={"geometry": verdict.as_dict(),
                                        "amber_items": list(verdict.amber_items),
                                        "priced_items": list(verdict.priced_items),
                                        "frames": verdict.frames})

    def _scan_session(self, req: "IntentRequest | BookRequest", amount: int
                      ) -> tuple[Session, ScanVerdict]:
        """Re-derive a scanned basket from this counter's own tables and close
        the session on it. Shared by a MINT and a BOOKING: the evidence a
        bill needs is the same whether the customer pays now or later, and
        two re-derivations would be two places for the witness to be read
        differently.

        Raises PaisaRefusal (audited) on any disagreement. Returns the closed
        session and the scan verdict; the caller decides what the bill
        becomes.
        """
        sv = rerun_scan(req, self.price_book, data_dir=self._data_dir)
        if not sv.agrees:
            self._audit(
                "intent.refused",
                session_id=req.session_id,
                reason=sv.reason,
                requested_paise=amount,
                server_total_paise=sv.server_total_paise,
                amber_items=list(sv.amber_items),
                priced_items=list(sv.priced_items),
                evidence="scan",
                minted=False,
            )
            raise PaisaRefusal(
                409, sv.reason, sv.detail,
                session_id=req.session_id,
                requested_paise=amount,
                scan=sv.as_dict(),
            )
        sess = self._session(req.session_id)
        if not sess.mat_locked:
            # The session's "lock" means an instrument was in a fit
            # state to observe this basket. For the mat that is a
            # homography explaining four printed markers; for the code
            # path it is a WITNESS this counter wrote itself and has
            # just re-derived from its own tables. Same role, different
            # instrument — and without it the billing guard refuses
            # every placement with MAT_NOT_LOCKED and the session
            # totals zero.
            sess.on_mat_lock(True)
        known_ids = {li.item_id for li in sess.line_items}
        for i, sku in enumerate(sv.server_lines):
            line_id = f"{sku}#{i}"
            if line_id in known_ids:
                continue
            sess.on_placement(Placement(
                item_id=line_id, name=sku,
                price_paise=self._server_price(sku)))
            sess.on_exit(line_id)
        done = sess.on_done()
        session_total = int(sess.total_paise)
        if session_total != amount:
            self._audit("intent.refused", session_id=req.session_id,
                        reason="session_total_disagreement",
                        requested_paise=amount,
                        session_total_paise=session_total,
                        evidence="scan", minted=False)
            raise PaisaRefusal(
                409, "session_total_disagreement",
                f"the session's own total is {session_total} paise, not "
                f"{amount}; nothing is minted.",
                session_id=req.session_id,
                session_total_paise=session_total)
        if sess.intent_amount_paise is None:
            raise PaisaRefusal(
                409, "basket_locked",
                f"the session refused to close its basket ({done.reason}); "
                f"nothing is minted.", session_id=req.session_id)
        return sess, sv

    def _mint(self, req: "IntentRequest", amount: int, sess,
              *, evidence: dict) -> dict[str, Any]:
        """The one gateway call, shared by both kinds of evidence.

        Whatever re-derived the basket — mat geometry or a scan witness — the
        money below this line is identical: one kernel row, CALLING committed
        and the connection closed before the gateway is touched, an
        indeterminate call parked rather than retried, PII stripped at the one
        boundary a gateway document enters this process, and one ledger line.
        Two mint paths would be two places for money to behave differently.
        """
        intent = self.kernel.create_intent(req.session_id, amount)
        if intent.state == _kernel.BOOKED:
            # KHATA. `create_intent` is idempotent per (session, cycle,
            # amount), so a CHARGE pressed after ON THE BOOK finds the booked
            # row. Minting a link for it would be two ways of being paid for
            # one basket; the customer settles this one through the book.
            raise PaisaRefusal(
                409, "bill_on_the_book",
                f"bill {req.session_id} is already on book {intent.book_id}; it "
                "is collected through the khata, not charged again.",
                session_id=req.session_id, nonce=intent.nonce,
                book_id=intent.book_id)
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
            **{k: v for k, v in evidence.items()},
        }
        self._audit(
            "intent.minted",
            session_id=req.session_id,
            nonce=intent.nonce,
            amount_paise=amount,
            payment_link_id=link.get("id"),
            priced_items=list(evidence.get("priced_items") or []),
            amber_items=list(evidence.get("amber_items") or []),
            replayed=replayed,
            minted=True,
        )
        return body

    # -- KHATA: POST /book -----------------------------------------------

    def book_bill(self, req: BookRequest) -> dict[str, Any]:
        """Close a bill onto a customer's book. NO GATEWAY, NO COLOUR.

        The evidence bar is a mint's: the witness is re-derived from this
        counter's own tables and the session closes on it. Then the kernel row
        goes NEW -> BOOKED, which is the honest state: a debit that executed
        never, with the book id on it. It is not SETTLED (no money moved) and
        not CALLING (no link exists), and `mark_settled` can never reach it.
        """
        with self._lock:
            try:
                amount = int(paise(req.amount_paise))
            except MoneyError as exc:
                raise PaisaRefusal(422, "bad_amount", str(exc)) from exc
            if amount <= 0:
                raise PaisaRefusal(
                    422, "bad_amount", f"a booking must be positive, got {amount} paise")
            if not BOOK_ID_RE.match(req.book_id):
                raise PaisaRefusal(
                    422, "bad_book_id",
                    f"{req.book_id!r} is not a khata book id (bk_ and hex).")
            sess, sv = self._scan_session(req, amount)
            intent = self.kernel.create_intent(req.session_id, amount)
            replayed = intent.state == _kernel.BOOKED and intent.book_id == req.book_id
            if intent.state == _kernel.BOOKED and not replayed:
                raise PaisaRefusal(
                    409, "bill_booked_elsewhere",
                    f"bill {req.session_id} is already on book {intent.book_id}; "
                    "a debt is not moved between customers by a retry.",
                    session_id=req.session_id, nonce=intent.nonce,
                    book_id=intent.book_id)
            if intent.state != _kernel.NEW and not replayed:
                raise PaisaRefusal(
                    409, "bill_already_minted",
                    f"bill {req.session_id} is {intent.state}: a payment link was "
                    "already asked for. A bill that has a link is paid through "
                    "the link or cancelled there; it does not also go on the book.",
                    session_id=req.session_id, nonce=intent.nonce,
                    state=intent.state)
            if not replayed:
                intent = self.kernel.mark_booked(intent.nonce, req.book_id)
            body = {
                "booked": True,
                "session_id": req.session_id,
                "nonce": intent.nonce,
                "state": intent.state,
                "amount_paise": amount,
                "amount_rupees": to_rupees_str(amount),
                "book_id": req.book_id,
                "replayed": replayed,
                "minted": False,
                "settles_money": False,
                "session_state": sess.state.value,
                "scan": sv.as_dict(),
                "outstanding_paise": self.kernel.outstanding_paise(req.book_id),
            }
            self._audit(
                "bill.booked",
                session_id=req.session_id,
                nonce=intent.nonce,
                amount_paise=amount,
                book_id=req.book_id,
                priced_items=list(sv.priced_items),
                replayed=replayed,
                minted=False,
                booked=True,
            )
            return body

    # -- KHATA: POST /collect --------------------------------------------

    def collect(self, req: CollectRequest) -> dict[str, Any]:
        """Mint ONE Payment Link for a book's outstanding balance.

        accept_partial on, a floor on the first instalment, reminders on and
        SMS to the customer's number — all of it the gateway's, none of it
        this process's. The amount is THIS kernel's own outstanding; the
        till's figure is compared and refused if it disagrees. A live
        collection on the book refuses a second by name.
        """
        with self._lock:
            if not BOOK_ID_RE.match(req.book_id):
                raise PaisaRefusal(
                    422, "bad_book_id",
                    f"{req.book_id!r} is not a khata book id (bk_ and hex).")
            try:
                asked = int(paise(req.amount_paise))
            except MoneyError as exc:
                raise PaisaRefusal(422, "bad_amount", str(exc)) from exc
            outstanding = self.kernel.outstanding_paise(req.book_id)
            if outstanding <= 0:
                raise PaisaRefusal(
                    409, "nothing_outstanding",
                    f"book {req.book_id} has {outstanding} paise outstanding by "
                    "this counter's own rows; there is nothing to collect.",
                    book_id=req.book_id, outstanding_paise=outstanding)
            if asked != outstanding:
                raise PaisaRefusal(
                    409, "outstanding_disagreement",
                    f"the till asked to collect {asked} paise; this counter's own "
                    f"rows put the balance at {outstanding}. Nothing is minted "
                    "against a figure the two sides do not agree on.",
                    book_id=req.book_id, requested_paise=asked,
                    outstanding_paise=outstanding)
            now_unix = _unix_seconds(self.clock.now_iso())
            live = self.kernel.live_collection_for(req.book_id)
            if live is not None:
                expired = (live.state == _kernel.COL_OPEN and live.expire_by is not None
                           and now_unix > int(live.expire_by))
                if expired:
                    # The gateway will not take a payment on it any more, so a
                    # fresh link for the current balance is the honest next
                    # step. A capture that arrives late for it still credits
                    # (COL_CREDITABLE includes EXPIRED).
                    self.kernel.close_collection(
                        live.collection_id, _kernel.COL_EXPIRED, "expire_by_passed")
                else:
                    raise PaisaRefusal(
                        409, "collection_link_already_open",
                        f"book {req.book_id} already has collection "
                        f"{live.collection_id} in state {live.state}"
                        + (f" for {to_rupees_str(live.amount_paise)}" if live.amount_paise else "")
                        + ". One link per balance: let the customer pay this one, "
                        "or wait for it to expire.",
                        book_id=req.book_id,
                        collection_id=live.collection_id,
                        state=live.state,
                        short_url=live.short_url,
                        payment_link_id=live.payment_link_id,
                        amount_paise=live.amount_paise,
                        captured_paise=live.captured_paise,
                        expire_by=live.expire_by)
            expire_in = int(req.expire_in_s) if req.expire_in_s else COLLECTION_EXPIRE_S
            if expire_in < COLLECTION_MIN_EXPIRE_S:
                expire_in = COLLECTION_MIN_EXPIRE_S
            expire_by = now_unix + expire_in
            min_partial = first_min_partial_paise(outstanding)
            contact = (req.customer.contact or "").strip() if req.customer else ""
            name = (req.customer.name or "").strip() if req.customer else ""

            try:
                col = self.kernel.create_collection(req.book_id, outstanding)
            except _kernel.CollectionOpen as exc:
                raise PaisaRefusal(
                    409, "collection_link_already_open", str(exc),
                    book_id=req.book_id, collection_id=exc.existing.collection_id,
                    state=exc.existing.state) from exc
            col = self.kernel.mark_collection_calling(col.collection_id)
            notes = {
                "collection_id": col.collection_id,
                "book_id": req.book_id,
                "gawaah": "khata",
            }
            customer: Optional[dict[str, str]] = None
            if contact:
                customer = {"contact": contact}
                if name:
                    customer["name"] = name
            try:
                link = self.gateway.create_payment_link(
                    amount_paise=outstanding,
                    notes=notes,
                    reference_id=col.collection_id,
                    description="GAWAAH khata — udhaar collection",
                    idempotent=True,
                    accept_partial=True,
                    first_min_partial_amount=min_partial,
                    reminder_enable=True,
                    notify={"sms": bool(contact), "email": False},
                    customer=customer,
                    expire_by=expire_by,
                )
            except Exception as exc:
                self.kernel.mark_collection_indeterminate(
                    col.collection_id, reason=type(exc).__name__)
                self._audit("collection.gateway_error", book_id=req.book_id,
                            collection_id=col.collection_id,
                            amount_paise=outstanding, error=type(exc).__name__,
                            minted=False)
                raise PaisaRefusal(
                    502, "gateway_error",
                    f"the gateway call did not complete ({type(exc).__name__}); "
                    "the collection is parked as INDETERMINATE, not retried.",
                    book_id=req.book_id, collection_id=col.collection_id) from exc
            if not isinstance(link, Mapping):
                self.kernel.mark_collection_indeterminate(
                    col.collection_id, reason="not_a_document")
                raise PaisaRefusal(
                    502, "gateway_error",
                    f"the gateway returned {type(link).__name__}, not a payment-link "
                    "document; nothing is minted.",
                    book_id=req.book_id, collection_id=col.collection_id)
            # PRD 9, same boundary as a bill link: the customer's contact went
            # OUT to the gateway so it can remind them; it does not come back
            # into this process's memory or its chain.
            link = strip_pii(link)
            self._collection_links[col.collection_id] = link
            link_id = link.get("id")
            short_url = link.get("short_url")
            got_expire = link.get("expire_by")
            col = self.kernel.mark_collection_open(
                col.collection_id,
                payment_link_id=str(link_id) if link_id else col.collection_id,
                short_url=str(short_url) if isinstance(short_url, str) else None,
                expire_by=int(got_expire) if isinstance(got_expire, int)
                and not isinstance(got_expire, bool) else expire_by,
            )
            self._audit(
                "collection.minted",
                book_id=req.book_id,
                collection_id=col.collection_id,
                amount_paise=outstanding,
                payment_link_id=col.payment_link_id,
                accept_partial=True,
                first_min_partial_amount=min_partial,
                reminder_enable=True,
                notify_sms=bool(contact),
                expire_by=col.expire_by,
                minted=True,
            )
            return self._collection_body(col)

    def _collection_body(self, col: _kernel.Collection) -> dict[str, Any]:
        link = self._collection_links.get(col.collection_id) or {}
        caps = self.kernel.captures_for(col.collection_id)
        return {
            "collection_id": col.collection_id,
            "book_id": col.book_id,
            "state": col.state,
            "amount_paise": int(col.amount_paise),
            "amount_rupees": to_rupees_str(int(col.amount_paise)),
            "captured_paise": int(col.captured_paise),
            "captured_rupees": to_rupees_str(int(col.captured_paise)),
            "still_due_paise": int(col.amount_paise) - int(col.captured_paise),
            "still_due_rupees": to_rupees_str(int(col.amount_paise) - int(col.captured_paise)),
            "payment_link_id": col.payment_link_id,
            "short_url": col.short_url,
            "expire_by": col.expire_by,
            "needs_human": bool(col.needs_human),
            "reason": col.reason,
            "accept_partial": bool(link.get("accept_partial", True)),
            "first_min_partial_amount": link.get("first_min_partial_amount"),
            "reminder_enable": bool(link.get("reminder_enable", True)),
            "captures": [
                {
                    "event_id": c.event_id,
                    "state": c.state,
                    "amount_paise": int(c.amount_paise),
                    "amount_rupees": to_rupees_str(int(c.amount_paise)),
                    "payment_id": c.payment_id,
                    "link_amount_paid": c.link_amount_paid,
                    "event": c.event,
                    "reason": c.reason,
                    "at": c.created_ts,
                }
                for c in caps
            ],
            "created_ts": col.created_ts,
            "updated_ts": col.updated_ts,
            "minted": col.payment_link_id is not None,
            "settles_money": False,
        }

    def collection_view(self, collection_id: str) -> dict[str, Any]:
        with self._lock:
            if not COLLECTION_ID_RE.match(collection_id or ""):
                raise PaisaRefusal(404, "unknown_collection",
                                   f"{collection_id!r} is not a collection id")
            try:
                col = self.kernel.get_collection(collection_id)
            except _kernel.UnknownCollection:
                raise PaisaRefusal(
                    404, "unknown_collection",
                    f"no collection {collection_id!r} on this counter",
                    collection_id=collection_id) from None
            return self._collection_body(col)

    def book_view(self, book_id: str) -> dict[str, Any]:
        """The kernel's whole account of one book, in integers."""
        with self._lock:
            if not BOOK_ID_RE.match(book_id or ""):
                raise PaisaRefusal(422, "bad_book_id",
                                   f"{book_id!r} is not a khata book id")
            v = self.kernel.book_view(book_id)
            live = v["live_collection"]
            return {
                "book_id": book_id,
                "booked_paise": int(v["booked_paise"]),
                "captured_paise": int(v["captured_paise"]),
                "parked_paise": int(v["parked_paise"]),
                "outstanding_paise": int(v["outstanding_paise"]),
                "outstanding_rupees": to_rupees_str(int(v["outstanding_paise"])),
                "bills": [
                    {"session_id": b.session_id, "nonce": b.nonce,
                     "amount_paise": int(b.amount_paise),
                     "amount_rupees": to_rupees_str(int(b.amount_paise)),
                     "state": b.state, "at": b.updated_ts}
                    for b in v["booked"]
                ],
                "collections": [self._collection_body(c) for c in v["collections"]],
                "live_collection_id": live.collection_id if live else None,
                "settles_money": False,
            }

    def _collection_known(self, collection_id: str) -> Optional[_kernel.Collection]:
        """Adapter for the collection predicate: a row, or None."""
        try:
            return self.kernel.get_collection(collection_id)
        except _kernel.UnknownCollection:
            return None

    # -- simulator only: POST /sim/pay -------------------------------------

    def sim_pay(self, req: SimPayRequest) -> dict[str, Any]:
        """The customer pays (part of) a link, IN THE SIMULATOR ONLY.

        Refused by name in live mode: there is no code path here that can
        make a real payment happen, and there must not be. In sim mode the
        simulator moves its own money and produces the signed webhook(s),
        which are then pushed through `handle_webhook` exactly as a tunnel
        would push them — the same bytes, the same signature, the same gates.
        """
        if self.config.mode != "sim":
            raise PaisaRefusal(
                409, "not_a_simulator",
                "this counter is on the live gateway; a payment is made by the "
                "customer on their own phone and nowhere else.")
        pay = getattr(self.gateway, "pay_link", None)
        if not callable(pay):
            raise PaisaRefusal(409, "not_a_simulator",
                               "the gateway in use cannot simulate a payment.")
        kwargs: dict[str, Any] = {}
        if req.amount_paise is not None:
            try:
                kwargs["amount_paise"] = int(paise(req.amount_paise))
            except MoneyError as exc:
                raise PaisaRefusal(422, "bad_amount", str(exc)) from exc
        try:
            result = pay(req.payment_link_id, **kwargs)
        except Exception as exc:  # the simulator's own BAD_REQUEST envelope
            raise PaisaRefusal(
                409, "gateway_error", f"{type(exc).__name__}: {exc}",
                payment_link_id=req.payment_link_id) from exc
        handled = []
        # MILAN: `webhook: false` withholds the delivery — see SimPayRequest.
        deliveries = () if req.webhook is False else getattr(result, "deliveries", ())
        for d in deliveries:
            headers = dict(d.headers)
            status, body = self.handle_webhook(
                d.body, headers.get("X-Razorpay-Signature", ""),
                header_event_id=headers.get("X-Razorpay-Event-Id"))
            handled.append({"event": d.event, "status": status,
                            "green": body.get("green"),
                            "reason": body.get("reason"),
                            "collection": body.get("collection")})
        link = strip_pii(getattr(result, "payment_link", {}) or {})
        return {
            "ok": True,
            "simulated": True,
            "payment_link_id": req.payment_link_id,
            "link_status": link.get("status"),
            "amount_paid": link.get("amount_paid"),
            "amount": link.get("amount"),
            "webhooks": handled,
            "webhook_withheld": req.webhook is False,
            "settles_money": False,
        }

    # -- MILAN: GET /recon, POST /recon/settle, POST /sim/settle ---------
    #
    # The gateway's settlement report, read here because this is the only
    # process with a key, and handed out scrubbed. Nothing in this section
    # mints, charges, refunds or signs. The one state change — `settle_from_
    # recon` — is the kernel's EXISTING `reconcile()` path, whose only contact
    # with the gateway is a read-only lookup of one nonce, run on a row the
    # settlement report says was paid while no webhook reached this counter.

    def gateway_lookup(self, nonce: str) -> _kernel.GatewayResult:
        """Read-only: what does the gateway say happened to this nonce?

        The only thing `kernel.reconcile` is allowed to call. There is no
        charge path in here, so running it a hundred times cannot move money
        twice. The link is found by `reference_id`, which `_mint` set to the
        nonce — so the answer is about THIS basket and no other.
        """
        fetch = getattr(self.gateway, "fetch_payment_links", None)
        if not callable(fetch):
            raise PaisaRefusal(
                409, "recon_unavailable",
                "this gateway adapter cannot look a payment link up by "
                "reference id, so nothing can be reconciled from its record.")
        found = fetch(reference_id=nonce)
        items = list((found or {}).get("items") or [])
        if not items:
            return _kernel.GatewayResult(found=False, status="not_found")
        link = items[0]
        payments = list(link.get("payments") or [])
        if link.get("status") != "paid" or not payments:
            return _kernel.GatewayResult(
                found=True, status=str(link.get("status") or "unknown"),
                amount_paise=int(paise(link.get("amount"))))
        pay = payments[0]
        # `amount_paid`, not `amount`: the money that ARRIVED, for the same
        # reason webhook.py compares the settled field and never the ask.
        return _kernel.GatewayResult(
            found=True, payment_id=str(pay.get("payment_id")),
            amount_paise=int(paise(link.get("amount_paid"))),
            status=str(pay.get("status") or "unknown"))

    @staticmethod
    def _recon_day(day: Optional[str], now_iso: str) -> tuple[int, int, int, str]:
        """(year, month, day, label). Default: yesterday on the gateway's IST
        calendar, because UPI settles T+1 and the report for today is empty
        until tomorrow."""
        if day is None or not str(day).strip():
            now = _dt.datetime.fromisoformat(now_iso)
            if now.tzinfo is None:
                now = now.replace(tzinfo=_dt.timezone.utc)
            ist = now.astimezone(_dt.timezone(_dt.timedelta(hours=5, minutes=30)))
            d = (ist - _dt.timedelta(days=1)).date()
        else:
            try:
                d = _dt.date.fromisoformat(str(day).strip())
            except ValueError as exc:
                raise PaisaRefusal(
                    422, "bad_day",
                    f"{day!r} is not a calendar day; write it as YYYY-MM-DD.") from exc
        return d.year, d.month, d.day, d.isoformat()

    def _intent_summary(self, nonce: Any) -> Optional[dict[str, Any]]:
        """What this kernel holds for a nonce a settlement row names, or None."""
        if not isinstance(nonce, str) or not nonce:
            return None
        try:
            it = self.kernel.get(nonce)
        except _kernel.KernelError:
            return None
        return {
            "nonce": it.nonce,
            "session_id": it.session_id,
            "state": it.state,
            "amount_paise": int(it.amount_paise),
            "amount_rupees": to_rupees_str(int(it.amount_paise)),
            "payment_id": it.payment_id,
            "needs_human": bool(it.needs_human),
            "reason": it.reason,
        }

    def recon_view(self, day: Optional[str] = None) -> dict[str, Any]:
        """The gateway's settlement rows for one day, scrubbed and annotated.

        Read-only. Each row is returned as the gateway filed it, less PII,
        plus `counter_intent`: what THIS kernel holds for the nonce the row's
        notes name — so the caller can tell a payment this counter settled
        from one it minted and never heard back about, without a second
        process ever opening kernel.db.
        """
        recon = getattr(self.gateway, "settlements_recon", None)
        if not callable(recon):
            raise PaisaRefusal(
                409, "recon_unavailable",
                "this gateway adapter has no settlement recon, so there is no "
                "report to match the counter's books against.")
        with self._lock:
            now_iso = self.clock.now_iso()
        year, month, dnum, label = self._recon_day(day, now_iso)
        try:
            raw = recon(year=year, month=month, day=dnum)
        except Exception as exc:  # noqa: BLE001 - a gateway error is a named answer
            raise PaisaRefusal(
                502, "gateway_error",
                f"the settlement recon call did not complete "
                f"({type(exc).__name__}); nothing was matched.") from exc
        items = list((raw or {}).get("items") or []) if isinstance(raw, Mapping) else []
        rows: list[dict[str, Any]] = []
        simulated = False
        with self._lock:
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                row = strip_pii(dict(item))
                if row.get("_gawaah_sim") is True:
                    simulated = True
                notes = row.get("notes") if isinstance(row.get("notes"), Mapping) else {}
                row["counter_intent"] = self._intent_summary(notes.get("nonce"))
                rows.append(row)
        return {
            "ok": True,
            "read_only": True,
            "settles_money": False,
            "mode": self.config.mode,
            "simulated": simulated,
            "day": label,
            "settlement_cycle": "T+1",
            "count": len(rows),
            "rows": rows,
            "fetched_at": now_iso,
            "source": "the gateway's settlement recon for that day, as filed",
        }

    def settle_from_recon(self, req: ReconSettleRequest) -> dict[str, Any]:
        """Run the kernel's reconcile path for one nonce. Never mints, never
        charges: the kernel asks the gateway what happened to the link it
        already minted and records the answer, or parks the row.

        A CALLING row is first marked INDETERMINATE. That is the honest
        state: the link went out, no webhook came back, and this counter does
        not know what happened to the call — which is the definition the
        kernel gives INDETERMINATE and the same move `recover()` makes at
        startup. NEW rows (no link was ever minted) and BOOKED rows (the debt
        lives on a customer's book) are refused by name; SETTLED, FAILED and
        ESCALATED are returned as they are, because `reconcile` will not
        even look those up.
        """
        with self._lock:
            try:
                before = self.kernel.get(req.nonce)
            except _kernel.KernelError:
                raise PaisaRefusal(
                    404, "unknown_nonce",
                    f"no intent {req.nonce!r} on this counter; nothing was "
                    f"looked up.", nonce=req.nonce) from None
            if before.state == _kernel.NEW:
                raise PaisaRefusal(
                    409, "nothing_to_settle",
                    f"intent {req.nonce} is NEW: no payment link was ever minted "
                    f"for it, so the gateway cannot hold money for it.",
                    nonce=req.nonce, session_id=before.session_id)
            if before.state == _kernel.BOOKED:
                raise PaisaRefusal(
                    409, "not_reconcilable",
                    f"intent {req.nonce} is on book {before.book_id}; a booked "
                    f"bill is collected through the khata, never settled here.",
                    nonce=req.nonce, session_id=before.session_id)
            if before.state == _kernel.CALLING:
                self.kernel.mark_indeterminate(
                    req.nonce, reason="settlement_recon_found_payment")
            try:
                after = self.kernel.reconcile(req.nonce, self.gateway_lookup)
            except _kernel.KernelError as exc:
                raise PaisaRefusal(
                    409, "not_reconcilable",
                    f"{type(exc).__name__}: {exc}", nonce=req.nonce,
                    session_id=before.session_id) from exc
            self._audit(
                "recon.settle",
                session_id=after.session_id, nonce=after.nonce,
                from_state=before.state, to_state=after.state,
                payment_id=after.payment_id, reason=after.reason,
                needs_human=bool(after.needs_human),
                minted=False, charged=False,
            )
        return {
            "ok": True,
            "nonce": after.nonce,
            "session_id": after.session_id,
            "amount_paise": int(after.amount_paise),
            "amount_rupees": to_rupees_str(int(after.amount_paise)),
            "state_before": before.state,
            "state": after.state,
            "settled": after.state == _kernel.SETTLED,
            "payment_id": after.payment_id,
            "reason": after.reason,
            "needs_human": bool(after.needs_human),
            "changed": after.state != before.state,
            "minted": False,
            "charged": False,
            "settles_money": False,
            "how": ("the kernel's reconcile path: a read-only lookup of the "
                    "link minted under this nonce, settled only if the gateway "
                    "reports it paid for exactly the intent's amount"),
        }

    def sim_settle(self) -> dict[str, Any]:
        """Simulator only: run the settlement batch now. Refused in live mode.

        Razorpay pays out on its own cycle and nothing here can hurry a real
        settlement; this exists so a demo on a process started this morning
        can show a report with rows in it. Amounts are untouched — the
        simulator only moves the day a captured payment is filed under.
        """
        if self.config.mode != "sim":
            raise PaisaRefusal(
                409, "not_a_simulator",
                "this counter is on the live gateway; settlements happen on "
                "Razorpay's cycle and cannot be run from here.")
        sweep = getattr(self.gateway, "create_ondemand_settlement", None)
        if not callable(sweep):
            raise PaisaRefusal(409, "not_a_simulator",
                               "the gateway in use cannot simulate a settlement.")
        try:
            batch = sweep(settle_full_balance=True)
        except Exception as exc:  # the simulator's own envelope
            raise PaisaRefusal(409, "gateway_error",
                               f"{type(exc).__name__}: {exc}") from exc
        return {
            "ok": True,
            "simulated": True,
            "settlement_id": batch.get("id"),
            "amount_settled": int(paise(batch.get("amount_settled") or 0)),
            "payments": len(batch.get("payment_ids") or []),
            "settles_money": False,
        }

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
            # Stamped BEFORE adjudication and regardless of outcome — see
            # `_last_webhook_iso`. Reachability is not authorisation.
            self._last_webhook_iso = self.clock.now_iso()
            self._webhooks_seen += 1

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
                self._last_webhook_green_iso = self._last_webhook_iso
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

            # KHATA. A delivery the green predicate did not green is offered to
            # the collection predicate, which re-verifies the signature over
            # the same raw bytes and asks its own question. The two are
            # disjoint by construction (a link carries a session_id OR a
            # collection_id, never both), so no body can both green a bill and
            # credit a book; and a green verdict never reaches here.
            collection_block: Optional[dict[str, Any]] = None
            # WAAPSI. A `refund.*` event is neither a bill nor a collection;
            # it is offered to the refund predicate instead, which re-verifies
            # the same raw bytes and asks which refund this counter asked for
            # it names. The branch is on the event name the green predicate
            # already read out of the verified body — never on anything
            # parsed here — and a green verdict never reaches either branch.
            refund_block: Optional[dict[str, Any]] = None
            if not verdict.green and verdict.reason not in (
                    "bad_signature", "secret_not_configured", "malformed_body"):
                if isinstance(verdict.event, str) and verdict.event.startswith("refund."):
                    refund_block = self._handle_refund(raw_body, signature)
                else:
                    collection_block = self._handle_collection(raw_body, signature)

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
                "collection": collection_block,
                "refund": refund_block,
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
                collection_id=(collection_block or {}).get("collection_id"),
                capture_reason=(collection_block or {}).get("reason"),
                captured=(collection_block or {}).get("credited"),
                refund_key=(refund_block or {}).get("refund_key"),
                refund_reason=(refund_block or {}).get("reason"),
                refund_state=(refund_block or {}).get("refund_state"),
            )
            return status, body

    def _handle_collection(self, raw_body: bytes, signature: str) -> dict[str, Any]:
        """Run the collection predicate and, on a capture, the kernel.

        Everything the kernel is told comes off the SIGNED body: the event
        key, the collection id, this payment's amount, the link's running
        total. `record_capture` is exactly-once on the event key, parks an
        amount that does not reconcile, and closes the collection on a final
        event. The bill behind the book is untouched: BOOKED has no legal
        move to SETTLED, and nothing here calls mark_settled.
        """
        cv: CollectionVerdict = self.collection_predicate.evaluate(
            raw_body, signature, self.config.effective_webhook_secret)
        out: dict[str, Any] = {
            "reason": cv.reason,
            "event": cv.event,
            "event_id": cv.event_id,
            "collection_id": cv.collection_id,
            "amount_paise": cv.amount_paise,
            "link_amount_paid": cv.link_amount_paid,
            "final": cv.final,
            "credited": False,
            "replayed": False,
            "detail": cv.detail,
        }
        if cv.closes and cv.collection_id:
            try:
                col = self.kernel.close_collection(
                    cv.collection_id, cv.closes, f"gateway:{cv.event}")
                out["collection_state"] = col.state
            except _kernel.KernelError as exc:
                out["detail"] = f"{cv.detail}; not closed: {exc}"
            return out
        if not cv.capture or cv.collection_id is None or cv.amount_paise is None:
            return out
        try:
            cap = self.kernel.record_capture(
                event_id=cv.event_id or cv.body_sha256,
                collection_id=cv.collection_id,
                amount_paise=cv.amount_paise,
                payment_id=cv.payment_id,
                link_amount_paid=cv.link_amount_paid,
                event=cv.event,
                final=cv.final,
            )
        except _kernel.KernelError as exc:
            self._audit("capture.refused", collection_id=cv.collection_id,
                        event_id=cv.event_id, error=type(exc).__name__,
                        detail=str(exc))
            out["detail"] = f"{cv.detail}; kernel refused: {exc}"
            return out
        col = self.kernel.get_collection(cv.collection_id)
        out.update({
            # "credited" is what THIS delivery did. A replay finds the row it
            # already wrote and credits nothing, whatever that row's state.
            "credited": cap.credited and not cap.replayed,
            "replayed": cap.replayed,
            "capture_state": cap.state,
            "capture_reason": cap.reason,
            "book_id": cap.book_id,
            "captured_paise": int(col.captured_paise),
            "outstanding_paise": int(cap.outstanding_paise),
            "outstanding_rupees": to_rupees_str(int(cap.outstanding_paise)),
            "collection_state": col.state,
            "needs_human": bool(col.needs_human),
        })
        return out

    # -- WAAPSI: POST /refund ----------------------------------------------
    #
    # A return by camera, refunded by Razorpay. The till proposes (bill, line,
    # paise); this service re-derives all three from its own tables — the
    # kernel for "did this bill settle and under which payment", the hash
    # chain for "was this line on it and what was CHARGED" — and only then
    # asks the gateway. The refund is REQUESTED on the gateway's answer and
    # REFUNDED on nothing but its signed refund.processed.

    def _refund_lookup(self, refund_key: Optional[str],
                       gateway_refund_id: Optional[str]) -> Optional[_kernel.Refund]:
        """Adapter for the refund predicate: the row, by our key first and
        the gateway's id second, or None."""
        if refund_key:
            try:
                return self.kernel.get_refund(refund_key)
            except _kernel.UnknownRefund:
                pass
        if gateway_refund_id:
            return self.kernel.refund_by_gateway_id(gateway_refund_id)
        return None

    def _line_on_bill(self, session_id: str, item_id: str, sku_id: str
                      ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        """(the line as the chain recorded it, the bill) — or (None, bill).

        Read through `gawaah.manage`, the one derivation of a bill in this
        program, so a receipt, the history screen and a refund all answer
        "what was charged for this packet" with the same integer. It is
        imported lazily: manage pulls in the vision constants, and the money
        service must still start on a box without them — it just cannot
        refund there, and says so by name (`chain_unavailable`).
        """
        try:
            from . import manage  # noqa: WPS433 - deliberately late
        except Exception as exc:  # noqa: BLE001 - a named limit, not a crash
            raise PaisaRefusal(
                503, "chain_unavailable",
                f"gawaah/manage.py is not importable here ({type(exc).__name__}: "
                f"{exc}), and it is the module that reads what a bill charged. "
                "No refund is asked for on a figure this service cannot derive.")
        try:
            records, chain = manage.read_chain()
            bill = manage.bills_from(records).get(session_id)
        except Exception as exc:  # noqa: BLE001
            raise PaisaRefusal(
                503, "chain_unavailable",
                f"the audit chain could not be read ({type(exc).__name__}: {exc}). "
                "No refund is asked for on a figure this service cannot derive.")
        if bill is None:
            return None, None
        for line in bill.get("line_items") or []:
            if line.get("item_id") == item_id and line.get("sku_id") == sku_id:
                return dict(line), bill
        return None, bill

    def refund(self, req: RefundRequest) -> dict[str, Any]:
        """One line of one settled bill goes back. A person pressed.

        Refusals, by name and in this order:
          bill_not_settled       no SETTLED intent with a payment id for this
                                 session — no signed webhook ever settled it.
          item_not_on_this_bill  the chain has no committed line with this
                                 item id and sku on this bill.
          line_unpriced          the line is on the bill with no integer
                                 price; nothing can be sent back for it.
          amount_disagrees       the till's figure is not the CHARGED price
                                 the chain recorded for that line.
          already_refunded       the kernel already holds a refund for this
                                 line that is not FAILED — pressed twice.
          refund_exceeds_bill    this line plus every refund already
                                 committed on the payment would pass what
                                 settled.
        Then, and only then, the kernel row goes NEW -> CALLING, the
        connection is closed, and the gateway is asked. Its answer makes the
        refund REQUESTED; a timeout parks it INDETERMINATE for a person.
        """
        with self._lock:
            try:
                asked = int(paise(req.amount_paise))
            except MoneyError as exc:
                raise PaisaRefusal(422, "bad_amount", str(exc)) from exc
            if asked <= 0:
                raise PaisaRefusal(
                    422, "bad_amount", f"a refund must be positive, got {asked} paise")

            it = self.kernel.settled_intent_for(req.session_id)
            if it is None:
                states = sorted({x.state for x in self.kernel.all_intents()
                                 if x.session_id == req.session_id})
                raise PaisaRefusal(
                    409, "bill_not_settled",
                    f"bill {req.session_id} was never settled by a signed webhook"
                    + (f" (its intents are {', '.join(states)})" if states else
                       " (no intent was ever minted for it)")
                    + ". There is no money to send back.",
                    session_id=req.session_id, states=states)

            line, bill = self._line_on_bill(req.session_id, req.item_id, req.sku_id)
            if line is None:
                on_bill = sorted({str(l.get("sku_id")) for l in
                                  ((bill or {}).get("line_items") or [])})
                raise PaisaRefusal(
                    409, "item_not_on_this_bill",
                    f"{req.sku_id} ({req.item_id}) is not a line on bill "
                    f"{req.session_id}"
                    + (f"; that bill carries {', '.join(on_bill)}" if on_bill else
                       "; the chain has no lines for that bill")
                    + ". Nothing is refunded for a packet this bill did not sell.",
                    session_id=req.session_id, sku_id=req.sku_id,
                    item_id=req.item_id, on_bill=on_bill)
            charged = line.get("price_paise")
            if not isinstance(charged, int) or isinstance(charged, bool):
                raise PaisaRefusal(
                    409, "line_unpriced",
                    f"{req.item_id} is on bill {req.session_id} with no integer "
                    "price on the chain; nothing can be sent back for it.",
                    session_id=req.session_id, item_id=req.item_id)
            if asked != charged:
                raise PaisaRefusal(
                    409, "amount_disagrees",
                    f"the till asked to refund {asked} paise; the chain says "
                    f"{charged} paise were charged for {req.item_id} on this bill. "
                    "What goes back is what was charged, and nothing is asked for "
                    "on a figure the two sides do not agree on.",
                    session_id=req.session_id, item_id=req.item_id,
                    requested_paise=asked, charged_paise=charged)

            try:
                rf = self.kernel.create_refund(
                    it.nonce, item_id=req.item_id, sku_id=req.sku_id,
                    amount_paise=charged)
            except _kernel.RefundRefused as exc:
                raise PaisaRefusal(409, exc.code, exc.detail, **exc.extra) from exc
            if rf.replayed and rf.state != _kernel.RF_FAILED:
                raise PaisaRefusal(
                    409, "already_refunded",
                    f"{req.sku_id} on bill {req.session_id} already has refund "
                    f"{rf.refund_key} in state {rf.state}"
                    + (f" ({to_rupees_str(rf.amount_paise)})" if rf.amount_paise else "")
                    + ". One line goes back once.",
                    session_id=req.session_id, item_id=req.item_id,
                    refund=self._refund_body(rf))
            if rf.state == _kernel.RF_NEW:
                # commit CALLING, close the DB, THEN call out. The kernel's rule.
                rf = self.kernel.mark_refund_calling(rf.refund_key)

            receipt = f"{req.session_id}/{it.cycle}"[:40]
            notes = {
                "refund_key": rf.refund_key,
                "session_id": req.session_id,
                "item_id": req.item_id,
                "sku_id": req.sku_id,
                "gawaah": "waapsi",
            }
            try:
                entity = self.gateway.refund(
                    it.payment_id, charged, speed="optimum",
                    receipt=receipt, notes=notes)
            except Exception as exc:
                # An indeterminate call is NEVER a failure and NEVER retried:
                # the retry could refund the line twice. Parked for a person;
                # a late signed webhook resolves it on its own.
                self.kernel.mark_refund_indeterminate(
                    rf.refund_key, reason=type(exc).__name__)
                self._audit("refund.gateway_error", session_id=req.session_id,
                            refund_key=rf.refund_key, amount_paise=charged,
                            error=type(exc).__name__, minted=False)
                raise PaisaRefusal(
                    502, "gateway_error",
                    f"the refund call did not complete ({type(exc).__name__}); "
                    "the refund is parked as INDETERMINATE for a person, not "
                    "retried.",
                    session_id=req.session_id, refund_key=rf.refund_key) from exc
            if not isinstance(entity, Mapping):
                self.kernel.mark_refund_indeterminate(rf.refund_key, reason="not_a_document")
                raise PaisaRefusal(
                    502, "gateway_error",
                    f"the gateway returned {type(entity).__name__}, not a refund "
                    "document; the refund is parked for a person.",
                    session_id=req.session_id, refund_key=rf.refund_key)
            # PRD 9: the one boundary where a gateway document enters here.
            entity = strip_pii(entity)
            gw_id = entity.get("id")
            gw_amount = entity.get("amount")
            if not isinstance(gw_id, str) or not gw_id:
                self.kernel.mark_refund_indeterminate(rf.refund_key, reason="no_refund_id")
                raise PaisaRefusal(
                    502, "gateway_error",
                    "the gateway's refund document carries no id; the refund is "
                    "parked for a person.",
                    session_id=req.session_id, refund_key=rf.refund_key)
            if gw_amount != charged:
                # The gateway took a DIFFERENT figure from the one asked for.
                # That is a contradiction about money, so it is neither netted
                # nor corrected nor called "requested": the row is parked
                # INDETERMINATE with the gateway's id on it (so its signed
                # callback can still find it) and named for a person.
                rf = self.kernel.mark_refund_indeterminate(
                    rf.refund_key,
                    reason=f"amount_disagrees:gateway={gw_amount}:asked={charged}",
                    gateway_refund_id=gw_id)
                self._audit("refund.parked", session_id=req.session_id,
                            refund_key=rf.refund_key, asked_paise=charged,
                            gateway_amount=gw_amount, gateway_refund_id=gw_id,
                            minted=False, refunded=False)
                raise PaisaRefusal(
                    409, "amount_disagrees",
                    f"this counter asked the gateway to refund {charged} paise "
                    f"and the gateway answered with {gw_amount!r}. The refund is "
                    "parked for a person; nothing is netted.",
                    session_id=req.session_id, refund_key=rf.refund_key,
                    refund=self._refund_body(rf))
            rf = self.kernel.mark_refund_requested(
                rf.refund_key, gateway_refund_id=gw_id, receipt=receipt)
            self._audit(
                "refund.requested", session_id=req.session_id,
                refund_key=rf.refund_key, nonce=it.nonce, payment_id=it.payment_id,
                item_id=req.item_id, sku_id=req.sku_id, amount_paise=charged,
                gateway_refund_id=gw_id, gateway_status=entity.get("status"),
                speed_requested=entity.get("speed_requested"),
                receipt=receipt, minted=False, refunded=False,
            )
            return {**self._refund_body(rf),
                    "gateway_status": entity.get("status"),
                    "speed_requested": entity.get("speed_requested"),
                    "speed_processed": entity.get("speed_processed")}

    def _refund_body(self, rf: _kernel.Refund) -> dict[str, Any]:
        events = self.kernel.refund_events_for(rf.refund_key)
        bill_amount = None
        try:
            bill_amount = int(self.kernel.get(rf.nonce).amount_paise)
        except _kernel.UnknownIntent:
            pass
        refunded = self.kernel.refunded_paise(rf.nonce)
        committed = self.kernel.committed_refund_paise(rf.nonce)
        return {
            "refund_key": rf.refund_key,
            "state": rf.state,
            "refunded": rf.state == _kernel.RF_PROCESSED,
            "session_id": rf.session_id,
            "nonce": rf.nonce,
            "cycle": rf.cycle,
            "payment_id": rf.payment_id,
            "item_id": rf.item_id,
            "sku_id": rf.sku_id,
            "amount_paise": int(rf.amount_paise),
            "amount_rupees": to_rupees_str(int(rf.amount_paise)),
            "attempt": rf.attempt,
            "gateway_refund_id": rf.gateway_refund_id,
            "receipt": rf.receipt,
            "needs_human": bool(rf.needs_human),
            "reason": rf.reason,
            "created_ts": rf.created_ts,
            "updated_ts": rf.updated_ts,
            "requested_ts": rf.requested_ts,
            "processed_ts": rf.processed_ts,
            "processed_event_id": rf.processed_event_id,
            "bill_amount_paise": bill_amount,
            "bill_amount_rupees": (None if bill_amount is None
                                   else to_rupees_str(bill_amount)),
            "refunded_paise": int(refunded),
            "refunded_rupees": to_rupees_str(int(refunded)),
            "committed_paise": int(committed),
            "events": [
                {"event_id": e.event_id, "event": e.event, "state": e.state,
                 "amount_paise": e.amount_paise, "gateway_refund_id": e.gateway_refund_id,
                 "reason": e.reason, "at": e.created_ts}
                for e in events
            ],
            # The screen shows REQUESTED with an age against these, so a
            # refund that takes minutes on the gateway looks like waiting
            # rather than like a page that is broken.
            "webhooks_seen": self._webhooks_seen,
            "last_webhook_at": self._last_webhook_iso,
            "now": self.clock.now_iso(),
            "minted": False,
            "settles_money": False,
        }

    def refund_view(self, refund_key: str) -> dict[str, Any]:
        with self._lock:
            if not re.match(r"^rf_[0-9a-f]{8,64}$", refund_key or ""):
                raise PaisaRefusal(404, "unknown_refund",
                                   f"{refund_key!r} is not a refund key")
            try:
                rf = self.kernel.get_refund(refund_key)
            except _kernel.UnknownRefund:
                raise PaisaRefusal(404, "unknown_refund",
                                   f"no refund {refund_key!r} on this counter",
                                   refund_key=refund_key) from None
            return self._refund_body(rf)

    def refunds_for_session(self, session_id: str) -> dict[str, Any]:
        """Every refund on one bill, and where the bill's money stands."""
        with self._lock:
            rows = self.kernel.refunds_for_session(session_id)
            it = self.kernel.settled_intent_for(session_id)
            bill_amount = int(it.amount_paise) if it else None
            refunded = self.kernel.refunded_paise(it.nonce) if it else 0
            committed = self.kernel.committed_refund_paise(it.nonce) if it else 0
            return {
                "session_id": session_id,
                "settled": it is not None,
                "payment_id": it.payment_id if it else None,
                "nonce": it.nonce if it else None,
                "bill_amount_paise": bill_amount,
                "bill_amount_rupees": (None if bill_amount is None
                                       else to_rupees_str(bill_amount)),
                "refunded_paise": int(refunded),
                "refunded_rupees": to_rupees_str(int(refunded)),
                "requested_paise": int(committed) - int(refunded),
                "committed_paise": int(committed),
                "refunds": [self._refund_body(r) for r in rows],
                "settles_money": False,
            }

    def sim_refund(self, req: SimRefundRequest) -> dict[str, Any]:
        """The gateway's back office, IN THE SIMULATOR ONLY: process (or fail)
        a refund it was asked for and push the signed callback through
        `handle_webhook` — the same bytes, signature and gates a tunnel would
        deliver. Refused by name in live mode."""
        if self.config.mode != "sim":
            raise PaisaRefusal(
                409, "not_a_simulator",
                "this counter is on the live gateway; a refund is processed by "
                "the gateway on its own clock and nowhere else.")
        with self._lock:
            try:
                rf = self.kernel.get_refund(req.refund_key)
            except _kernel.UnknownRefund:
                raise PaisaRefusal(404, "unknown_refund",
                                   f"no refund {req.refund_key!r} on this counter") from None
            if not rf.gateway_refund_id:
                raise PaisaRefusal(
                    409, "refund_not_requested",
                    f"refund {rf.refund_key} is {rf.state} and the gateway never "
                    "gave it an id; there is nothing for the back office to process.",
                    refund_key=rf.refund_key, state=rf.state)
            fn = getattr(self.gateway, "fail_refund" if req.outcome == "failed"
                         else "process_refund", None)
            if not callable(fn):
                raise PaisaRefusal(409, "not_a_simulator",
                                   "the gateway in use cannot simulate a refund.")
            try:
                result = fn(rf.gateway_refund_id)
            except Exception as exc:  # the simulator's own envelope
                raise PaisaRefusal(409, "gateway_error", f"{type(exc).__name__}: {exc}",
                                   refund_key=rf.refund_key) from exc
        handled = []
        for d in getattr(result, "deliveries", ()):
            headers = dict(d.headers)
            status, body = self.handle_webhook(
                d.body, headers.get("X-Razorpay-Signature", ""),
                header_event_id=headers.get("X-Razorpay-Event-Id"))
            handled.append({"event": d.event, "status": status,
                            "green": body.get("green"), "reason": body.get("reason"),
                            "refund": body.get("refund")})
        with self._lock:
            rf = self.kernel.get_refund(req.refund_key)
            return {
                "ok": True,
                "simulated": True,
                "outcome": req.outcome,
                "webhooks": handled,
                **self._refund_body(rf),
            }

    def _handle_refund(self, raw_body: bytes, signature: str) -> dict[str, Any]:
        """Run the refund predicate and, on a known refund, the kernel.

        Everything the kernel is told comes off the SIGNED body: the event
        key, the refund it names, the paise the refund entity carries, the
        gateway's id and status. `record_refund_event` is exactly-once on the
        event key, parks an amount that does not reconcile, and moves the
        refund to PROCESSED only on `refund.processed`. The bill's intent is
        untouched: nothing here calls mark_settled or writes the intents
        table.
        """
        rv: RefundVerdict = self.refund_predicate.evaluate(
            raw_body, signature, self.config.effective_webhook_secret)
        out: dict[str, Any] = {
            "reason": rv.reason,
            "event": rv.event,
            "event_id": rv.event_id,
            "refund_key": rv.refund_key,
            "gateway_refund_id": rv.gateway_refund_id,
            "payment_id": rv.payment_id,
            "amount_paise": rv.amount_paise,
            "outcome": rv.outcome,
            "applied": False,
            "replayed": False,
            "detail": rv.detail,
        }
        if not rv.known or rv.refund_key is None or rv.event is None:
            return out
        try:
            ev, rf = self.kernel.record_refund_event(
                event_id=rv.event_id or rv.body_sha256,
                event=rv.event,
                refund_key=rv.refund_key,
                amount_paise=rv.amount_paise,
                gateway_refund_id=rv.gateway_refund_id,
                status=rv.status,
            )
        except _kernel.KernelError as exc:
            self._audit("refund.event_refused", refund_key=rv.refund_key,
                        event_id=rv.event_id, error=type(exc).__name__,
                        detail=str(exc))
            out["detail"] = f"{rv.detail}; kernel refused: {exc}"
            return out
        out.update({
            # "applied" is what THIS delivery did. A replay finds the row it
            # already wrote and moves nothing, whatever that row's state.
            "applied": ev.state == _kernel.RFE_APPLIED and not ev.replayed,
            "replayed": ev.replayed,
            "event_state": ev.state,
            "event_reason": ev.reason,
            "refund_state": rf.state,
            "refunded": rf.state == _kernel.RF_PROCESSED,
            "needs_human": bool(rf.needs_human),
            "session_id": rf.session_id,
            "item_id": rf.item_id,
            "sku_id": rf.sku_id,
            "refund_amount_paise": int(rf.amount_paise),
            "refunded_paise": int(self.kernel.refunded_paise(rf.nonce)),
        })
        return out

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
                # The pay screen polls THIS, not /health — so the liveness fact
                # has to be here or it cannot be shown where it matters.
                "webhooks_seen": self._webhooks_seen,
                "last_webhook_at": self._last_webhook_iso,
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
                # The full state histogram, because the two counters above are
                # not the whole story: an INDETERMINATE intent — the gateway
                # was called and the outcome is unknown, so money MAY have
                # moved — sat invisible behind "nothing escalated" for 28
                # hours. Nothing in this service escalates on its own (the
                # kernel's sweep is never invoked here), so a health readout
                # that only counts escalations is a tautology, not a
                # measurement.
                "intents_by_state": dict(sorted(Counter(
                    it.state for it in self.kernel.all_intents()).items())),
                "payment_links": len(self._links),
                # KHATA. Parked captures are a person's job, like escalations.
                "collections": len(self.kernel.all_collections()),
                "captures_parked": len(self.kernel.parked_captures()),
                # WAAPSI. A parked refund — signed amount disagreeing with the
                # paise asked for, or a refund call that never answered — is a
                # person's job, like a parked capture.
                "refunds": len(self.kernel.all_refunds()),
                "refunds_parked": len(self.kernel.parked_refunds()),
                "ledger_lines": self.ledger.count,
                "ledger_head": self.ledger.head,
                # Liveness of the inbound path, so a screen can say WHY it is
                # still waiting instead of spinning identically for "not paid
                # yet" and "nothing has been able to reach this counter since
                # Saturday".
                "webhooks_seen": self._webhooks_seen,
                "last_webhook_at": self._last_webhook_iso,
                "last_green_webhook_at": self._last_webhook_green_iso,
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
        data_dir=data_dir,
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

    # -- KHATA -----------------------------------------------------------
    # A booking mints nothing; a collection mints one link; the two reads
    # are the kernel's own rows. None of these takes a name or a phone
    # except /collect, which hands the contact to the gateway for reminders
    # and keeps nothing.

    @app.post("/book")
    async def post_book(req: BookRequest) -> dict[str, Any]:
        return svc.book_bill(req)

    @app.post("/collect")
    async def post_collect(req: CollectRequest) -> dict[str, Any]:
        return svc.collect(req)

    @app.get("/collection/{collection_id}")
    async def get_collection(collection_id: str) -> dict[str, Any]:
        return svc.collection_view(collection_id)

    @app.get("/khata/{book_id}")
    async def get_book(book_id: str) -> dict[str, Any]:
        return svc.book_view(book_id)

    @app.post("/sim/pay")
    async def post_sim_pay(req: SimPayRequest) -> dict[str, Any]:
        return svc.sim_pay(req)

    # -- WAAPSI ----------------------------------------------------------
    # One write — a refund the person at the counter pressed for, on one
    # line of one settled bill — and two reads of the kernel's own refund
    # rows. The refund is REQUESTED here and REFUNDED only on the gateway's
    # signed refund.processed through /webhook, never on this route's
    # answer. The simulator-only route is the gateway's back office.

    @app.post("/refund")
    async def post_refund(req: RefundRequest) -> dict[str, Any]:
        return svc.refund(req)

    @app.get("/refund/{refund_key}")
    async def get_refund(refund_key: str) -> dict[str, Any]:
        return svc.refund_view(refund_key)

    @app.get("/refunds/{session_id}")
    async def get_refunds(session_id: str) -> dict[str, Any]:
        return svc.refunds_for_session(session_id)

    @app.post("/sim/refund")
    async def post_sim_refund(req: SimRefundRequest) -> dict[str, Any]:
        return svc.sim_refund(req)

    # -- MILAN -----------------------------------------------------------
    # One read of the gateway's settlement report; one run of the kernel's
    # existing reconcile path for a nonce that report named; one simulator-
    # only sweep. None of these takes an amount.

    @app.get("/recon")
    async def get_recon(day: str | None = None) -> dict[str, Any]:
        return svc.recon_view(day)

    @app.post("/recon/settle")
    async def post_recon_settle(req: ReconSettleRequest) -> dict[str, Any]:
        return svc.settle_from_recon(req)

    @app.post("/sim/settle")
    async def post_sim_settle() -> dict[str, Any]:
        return svc.sim_settle()

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
