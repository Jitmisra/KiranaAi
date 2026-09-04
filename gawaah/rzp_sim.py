"""S4b — LOCAL RAZORPAY SIMULATOR.

There are no real Razorpay keys yet (gates G0/G1/G2 are blocked on a human
signup), so the entire money path has to be exercisable today and swapping in
real keys must be a configuration change, not a rewrite. This module plays the
role of the *remote server*: it mints payment links, simulates a customer
paying one, and pushes a signed webhook at a sink exactly the way Razorpay
pushes one at a `cloudflared` tunnel.

What is deliberately faithful
-----------------------------
* `create_payment_link` returns `{id: "plink_...", short_url: "<simulated host>/l/...",
  status: "created"}`. `short_url` is a **string**, which is the whole reason
  Payment Links beat `qr_codes` for us (PRD 10.1): the QR is rendered locally on
  the counter plane from that string, with no remote image fetch.
* The webhook body is signed with `HMAC-SHA256(raw_body_bytes, webhook_secret)`
  and delivered in `X-Razorpay-Signature`, over the exact bytes on the wire.
* The body is serialised with **unsorted, insertion-ordered keys**. This is not
  an accident. If it were sorted-key canonical JSON, a receiver that parsed the
  body and re-serialised it would still verify, silently hiding the bug that
  invariant 2 exists to prevent. Here, parse-then-reserialise **fails**, so
  "verify over raw bytes before JSON parsing" is enforced by the fixture rather
  than merely requested. `test_body_bytes_are_not_recoverable_by_reserialising`
  pins this.
* A duplicate `reference_id` raises `BAD_REQUEST_ERROR`, because that is what
  the real API does. Note this **contradicts PRD 11**, which claims "a duplicate
  POST returns the existing link rather than a second charge". It does not. The
  caller must catch the duplicate and fetch by `reference_id` — which is exactly
  what `idempotent=True` does for you, in one call, on both sim and real.
* `cancel_payment_link` works only from `created`. A paid link can never be
  cancelled. That is the safety property behind the MUDRA death beat (SIX 229).
* Fees are integer paise: 2% + 18% GST via basis points and `//`. No float.

What is deliberately *not* faithful, and is labelled
----------------------------------------------------
Webhook bodies and payment links carry a top-level `"_gawaah_sim": true` key.
Real Razorpay bodies do not have it, so a simulated green can be told apart
from a real one in the ledger and no fixture produced here can be passed off as
a genuine event. (Payment *views* do not carry it: `_payment_view` strips every
`_`-prefixed key. This paragraph used to say "every body", which was not true
of either payments or links — the link is now fixed, the payment view is a
deliberate exception, and saying so is cheaper than a reader discovering it.)

INVARIANT 6 — NO FORGERY PRIMITIVES. This module never constructs, parses or
regenerates a UPI payload, and `test_no_upi_payload_is_ever_constructed`
asserts that over every byte it emits.

It also must never mint an address on a domain the gateway owns, which it did
for most of this project's life — see `SHORT_URL_PREFIX`. A forged short_url is
not a lesser sin than a forged UPI string: it is the same sin with a longer
fuse, because it looks right until a customer presses it.

INVARIANT 1 — money is integer paise. There is no float, no `float()` and no
`/` in this file; `test_rzp_sim_is_float_free` runs `tools/lint_no_float.py`'s
own AST visitor against it even though it is not on that tool's list.

INVARIANT: determinism. Ids come from a seeded counter hashed with SHA-256,
never from `random` or `uuid`, so two runs of the same script are byte-identical
and a replay is diffable.
"""
from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import hmac
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol

from gawaah.clock import Clock
from gawaah.ledger import Ledger
from gawaah.money import paise

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------

#: Events this simulator can emit. The *green set* is deliberately not defined
#: here — it belongs to `paisa`, the only holder of secrets, which re-runs the
#: predicate server-side.
EVENTS: tuple[str, ...] = (
    "payment_link.paid", "payment.captured", "payment_link.partially_paid",
    # WAAPSI. The refund lifecycle as the real gateway reports it: created
    # when the request is taken, processed when the money has gone back,
    # failed when it could not. Only `refund.processed` moves a refund in the
    # kernel; the other two are acknowledged and recorded.
    "refund.created", "refund.processed", "refund.failed",
)

#: WAAPSI. The `speed` values the Refunds API accepts. `optimum` asks for an
#: instant refund where the rails allow and falls back to normal; the entity
#: reports which one it got in `speed_processed`.
REFUND_SPEEDS: tuple[str, ...] = ("normal", "optimum")

#: Failure injections. "normal" is the absence of injection.
MODES: tuple[str, ...] = (
    "normal",
    "timeout",
    "error",
    "duplicate_webhook",
    "out_of_order",
    "wrong_amount",
)

#: THIS MUST NEVER BE A DOMAIN THE GATEWAY OWNS, AND IT USED TO BE.
#:
#: It was `https://rzp.io/i/`. That is Razorpay's real short-link host, and this
#: module was minting seven-character codes on it that Razorpay had never
#: issued. One of them — `https://rzp.io/i/BjQNyPd` — was stored on a real
#: customer order, rendered under a green PAY Rs 1,600.00 button, and pressed.
#: The gateway answered `404 {}` because it had never heard of the code. The
#: customer was shown an empty page where a payment should have been.
#:
#: The old docstring argued this was fine because a short_url is not a `upi://`
#: string. That reasoning is wrong. Composing a payment address on the payment
#: processor's own domain and putting it in front of a customer is forgery of
#: exactly the kind invariant 6 exists to forbid; the UPI payload is one
#: instance of the class, not the definition of it.
#:
#: `.invalid` is reserved by RFC 2606 and can never resolve, so a simulated link
#: now fails as a name rather than as a lie, and `storefront._link_health`'s
#: shape gate refuses it before anything is fetched.
SHORT_URL_PREFIX = "https://pay.gawaah-sim.invalid/l/"
SHORT_URL_LEN = 7          # real Razorpay ones look like https://rzp.io/i/nxrHnLJ
ID_LEN = 14                # real ones look like plink_ExjpAUN3gVHrPJ

MIN_AMOUNT_PAISE = 100     # Payment Links floor: Rs 1
DEFAULT_EXPIRY_S = 900     # PRD 10.1: now + 15 min
MIN_EXPIRY_S = 900         # SIX 229: expire_by has a 15-minute floor. Closing an
                           # intent locally without cancelling therefore leaves an
                           # orphaned-but-payable link for up to 15 minutes.
MAX_NOTES = 15             # Razorpay: max 15 kv pairs
MAX_NOTE_LEN = 256         # ... 256 chars each

FEE_BPS = 200              # 2% platform fee, in basis points
GST_BPS = 1800             # 18% GST on the fee, in basis points
BPS_DEN = 10000

# --------------------------------------------------------------- MILAN
#
# SETTLEMENT. Razorpay pays a captured UPI payment into the merchant's bank
# on the next settlement cycle — T+1 by default — and files the rows under
# the day the batch went out, in IST. The simulator derives the same report
# from the payments it already holds: a payment captured at unix `t` is due
# in the batch of IST day `ist_day(t) + SETTLEMENT_T_PLUS_DAYS`, filed at
# 00:00 IST of that day, with the fee and tax `pay_link` already computed in
# basis points. Nothing below is typed in by hand; every figure is a fold
# over `_payments`, which is what lets `tests/test_milan.py` check the report
# against the payments collection row by row.
#
# The on-demand batch (`create_ondemand_settlement`) is the simulator's
# analogue of Razorpay's `POST /v1/settlements/ondemand`: everything captured
# and not yet settled goes out NOW. It exists because this process holds its
# payments in memory and a demo cannot wait a calendar day for a T+1 batch.
SETTLEMENT_T_PLUS_DAYS = 1
IST_OFFSET_S = 5 * 3600 + 30 * 60      # the gateway's clock is Asia/Kolkata
_DAY_S = 86400
_UNIX_EPOCH_ORDINAL = _dt.date(1970, 1, 1).toordinal()

#: Present on every body this module emits. Real Razorpay bodies lack it.
SIM_BODY_MARKER = "_gawaah_sim"

#: Fixed, obviously synthetic customer identifiers. They exist only so that
#: `paisa`'s PII-stripping path (PRD 9: vpa/email/contact/rrn are dropped on
#: receipt) has something real to strip. None of these is routable, and none of
#: them is assembled into a payment payload.
SIM_VPA = "simulated.customer@rzpsim"
SIM_EMAIL = "simulated.customer@example.invalid"
SIM_CONTACT = "+910000000000"    # not a valid Indian mobile: does not start 6-9

_B62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_EPOCH = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)
_ONE_SECOND = _dt.timedelta(seconds=1)


# --------------------------------------------------------------------------
# errors — shaped like the real API envelope
# --------------------------------------------------------------------------

class RazorpaySimError(RuntimeError):
    """Mirrors `{"error": {"code": ..., "description": ...}}`."""

    def __init__(self, code: str, description: str) -> None:
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description

    def as_dict(self) -> dict:
        return {"error": {"code": self.code, "description": self.description}}


class RazorpaySimTimeout(RazorpaySimError):
    """Injected network timeout. The caller must treat this as 'unknown', not
    as 'failed' — the mint may or may not have happened on the far side."""

    def __init__(self, description: str = "connection timed out") -> None:
        super().__init__("GATEWAY_TIMEOUT", description)


class ConfigError(RuntimeError):
    """Raised when the client configuration cannot be honoured."""


# --------------------------------------------------------------------------
# pure helpers — no clock, no state
# --------------------------------------------------------------------------

def _b62(n: int, length: int) -> str:
    """Base-62 encode the low `length` digits of a non-negative integer."""
    out = []
    for _ in range(length):
        n, r = divmod(n, 62)
        out.append(_B62[r])
    return "".join(reversed(out))


def serialize_body(obj: Mapping[str, Any]) -> bytes:
    """Serialise a webhook body the way a server does: insertion order, not
    sorted. See the module docstring — this is what makes raw-byte verification
    testable rather than merely requested."""
    return json.dumps(
        obj, ensure_ascii=False, separators=(",", ":"), sort_keys=False
    ).encode("utf-8")


def sign_body(body: bytes, secret: str) -> str:
    """`X-Razorpay-Signature` = HMAC-SHA256 over the RAW BYTES."""
    if not isinstance(body, (bytes, bytearray)):
        raise TypeError("sign_body takes raw bytes, never a parsed object")
    return hmac.new(secret.encode("utf-8"), bytes(body), hashlib.sha256).hexdigest()


def verify_webhook_signature(body: bytes, signature: Any, secret: str) -> bool:
    """Constant-time verification over raw bytes. Never raises on bad input.

    Provided here so tests can prove the fixture is self-consistent. In
    production `paisa` owns the secret and runs its own check (invariant 5)."""
    if not isinstance(body, (bytes, bytearray)):
        return False
    if not isinstance(signature, str):
        return False
    return hmac.compare_digest(sign_body(bytes(body), secret), signature)


def iso_to_unix(iso: str) -> int:
    """ISO-8601 -> integer unix seconds, without ever touching a float.

    `datetime.timestamp()` returns a float, which is banned in this file.
    `timedelta // timedelta` returns an int, which is not."""
    dt = _dt.datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return (dt - _EPOCH) // _ONE_SECOND


def _validate_notes(notes: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(notes, Mapping):
        raise RazorpaySimError(
            "BAD_REQUEST_ERROR", f"notes must be an object, got {type(notes).__name__}"
        )
    if len(notes) > MAX_NOTES:
        raise RazorpaySimError(
            "BAD_REQUEST_ERROR",
            f"notes supports a maximum of {MAX_NOTES} key-value pairs, got {len(notes)}",
        )
    out: dict[str, str] = {}
    for k, v in notes.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise RazorpaySimError(
                "BAD_REQUEST_ERROR",
                f"notes keys and values must be strings: {k!r} -> {v!r}",
            )
        if len(k) > MAX_NOTE_LEN or len(v) > MAX_NOTE_LEN:
            raise RazorpaySimError(
                "BAD_REQUEST_ERROR",
                f"notes entries are capped at {MAX_NOTE_LEN} characters: {k!r}",
            )
        out[k] = v
    return out


# --------------------------------------------------------------------------
# delivery
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Delivery:
    """One webhook push. `dict(delivery)` is exactly `{"headers": ..., "body": ...}`,
    the shape the sink contract specifies; the extra attributes are for tests
    and for the audit line."""

    headers: Mapping[str, str]
    body: bytes
    event: str
    event_id: str
    seq: int
    delivered: bool = True
    error: str | None = None

    # mapping access so `dict(d)` and `d["body"]` both work
    def keys(self) -> tuple[str, str]:
        return ("headers", "body")

    def __getitem__(self, key: str) -> Any:
        if key == "headers":
            return self.headers
        if key == "body":
            return self.body
        raise KeyError(key)

    @property
    def signature(self) -> str:
        return self.headers["X-Razorpay-Signature"]

    @property
    def body_sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    def json(self) -> dict:
        """Parse the body. Only ever call this AFTER verifying the signature."""
        return json.loads(self.body.decode("utf-8"))


@dataclass(frozen=True)
class _Spec:
    """A webhook that has been built and signed but not yet pushed. Kept
    separate from `Delivery` so a Delivery is only ever constructed once the
    outcome of the push is known."""

    event: str
    event_id: str
    headers: Mapping[str, str]
    body: bytes

    def as_delivery(
        self, seq: int, *, delivered: bool = True, error: str | None = None
    ) -> Delivery:
        return Delivery(
            headers=self.headers,
            body=self.body,
            event=self.event,
            event_id=self.event_id,
            seq=seq,
            delivered=delivered,
            error=error,
        )


@dataclass(frozen=True)
class PayResult:
    """What `pay_link` produced: the server-side truth plus what went on the wire."""

    payment: dict
    payment_link: dict
    deliveries: tuple[Delivery, ...]


@dataclass(frozen=True)
class RefundResult:
    """WAAPSI. What `process_refund` / `fail_refund` produced: the refund
    entity as the server now holds it, plus what went on the wire."""

    refund: dict
    payment: dict
    deliveries: tuple[Delivery, ...]


# --------------------------------------------------------------------------
# config — the "swap in real keys is a config change" seam
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class RazorpayConfig:
    mode: str = "sim"                 # "sim" | "live"
    key_id: str = "rzp_test_SIMULATED"
    key_secret: str = ""
    webhook_secret: str = "whsec_simulated"
    seed: int = 0
    account_id: str = "acc_GAWAAHSIM00"

    @staticmethod
    def from_env(env: Mapping[str, str]) -> "RazorpayConfig":
        seed_raw = env.get("GAWAAH_RZP_SEED", "0")
        if not seed_raw.lstrip("-").isdigit():
            raise ConfigError(f"GAWAAH_RZP_SEED must be an integer, got {seed_raw!r}")
        d = RazorpayConfig()
        return RazorpayConfig(
            mode=env.get("GAWAAH_RZP_MODE", d.mode),
            key_id=env.get("RAZORPAY_KEY_ID", d.key_id),
            key_secret=env.get("RAZORPAY_KEY_SECRET", d.key_secret),
            webhook_secret=env.get("RAZORPAY_WEBHOOK_SECRET", d.webhook_secret),
            seed=int(seed_raw),
            account_id=env.get("RAZORPAY_ACCOUNT_ID", d.account_id),
        )

    def __repr__(self) -> str:   # never leak secrets into a traceback or a log
        return (
            f"RazorpayConfig(mode={self.mode!r}, key_id={self.key_id!r}, "
            f"key_secret=<{len(self.key_secret)} chars redacted>, "
            f"webhook_secret=<{len(self.webhook_secret)} chars redacted>, "
            f"seed={self.seed}, account_id={self.account_id!r})"
        )


class RazorpayClient(Protocol):
    """The surface `paisa` is allowed to depend on. The simulator implements it
    today; the real client implements it when keys land. Nothing else changes."""

    def create_payment_link(
        self, amount_paise: int, notes: Mapping[str, str], **kwargs: Any
    ) -> dict: ...
    def fetch_payment_link(self, link_id: str) -> dict: ...
    def fetch_payments(self, **kwargs: Any) -> dict: ...
    def cancel_payment_link(self, link_id: str) -> dict: ...


def build_client(
    cfg: RazorpayConfig,
    clock: Clock,
    *,
    sink: Callable[[Delivery], Any] | None = None,
    live_factory: Callable[[RazorpayConfig], RazorpayClient] | None = None,
) -> RazorpayClient:
    """Config-only swap between the simulator and the real thing.

    `live_factory` is the injection point for the real Razorpay client, which
    lives in `paisa` because `paisa` is the only process allowed to hold
    `RAZORPAY_KEY_SECRET` (invariant 5). This module never opens a socket, and
    refuses loudly rather than pretending to be live."""
    if cfg.mode == "sim":
        return RazorpaySim(
            webhook_secret=cfg.webhook_secret,
            clock=clock,
            seed=cfg.seed,
            sink=sink,
            account_id=cfg.account_id,
        )
    if cfg.mode == "live":
        if live_factory is None:
            raise ConfigError(
                "GAWAAH_RZP_MODE=live but no live_factory was injected. "
                "rzp_sim never talks to the network; the real client belongs to paisa."
            )
        return live_factory(cfg)
    raise ConfigError(f"unknown GAWAAH_RZP_MODE {cfg.mode!r}, expected 'sim' or 'live'")


# --------------------------------------------------------------------------
# the simulator
# --------------------------------------------------------------------------

class RazorpaySim:
    """A local stand-in for the Razorpay subset GAWAAH uses.

    Ids derive from a seeded counter, never from `random`, so two runs produce
    byte-identical transcripts. Time comes from an injected `Clock`, never from
    `datetime.now()`.
    """

    def __init__(
        self,
        webhook_secret: str,
        clock: Clock,
        *,
        seed: int = 0,
        sink: Callable[[Delivery], Any] | None = None,
        account_id: str = "acc_GAWAAHSIM00",
        ledger: Ledger | None = None,
    ) -> None:
        if not isinstance(webhook_secret, str) or not webhook_secret:
            raise ConfigError("webhook_secret must be a non-empty string")
        self._secret = webhook_secret
        self._clock = clock
        self._seed = int(seed)
        self._sink = sink
        self._account_id = account_id
        self._ledger = ledger

        self._mode = "normal"
        self._wrong_amount_delta = 1

        self._counters: dict[str, int] = {}
        self._links: dict[str, dict] = {}
        self._payments: dict[str, dict] = {}
        self._orders: dict[str, dict] = {}
        self._by_reference: dict[str, str] = {}
        self._deliveries: list[Delivery] = []
        #: MILAN. payment id -> {settlement_id, settled_at} for payments swept
        #: into an on-demand batch ahead of their T+1 day. A payment not in
        #: here settles on its scheduled day; see `_settlement_of`.
        self._ondemand: dict[str, dict] = {}
        #: WAAPSI. refund id -> the refund entity, by the real entity's field
        #: names (`id`, `payment_id`, `amount`, `status`, `created_at`), which
        #: is the shape MILAN's `_refund_rows_of` reads for the recon report.
        self._refunds: dict[str, dict] = {}

    # -- never leak the secret ------------------------------------------
    def __repr__(self) -> str:
        return (
            f"RazorpaySim(account_id={self._account_id!r}, seed={self._seed}, "
            f"mode={self._mode!r}, links={len(self._links)}, "
            f"payments={len(self._payments)}, deliveries={len(self._deliveries)}, "
            f"webhook_secret=<{len(self._secret)} chars redacted>)"
        )

    # ------------------------------------------------------------------
    # configuration
    # ------------------------------------------------------------------

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str | None, *, wrong_amount_delta_paise: int = 1) -> None:
        """Inject a failure. `None` and "normal" both clear the injection.

        timeout            -- every API call raises RazorpaySimTimeout. `pay_link`
                              still moves the money server-side but the webhook
                              never reaches the sink. This is the dangerous case:
                              the rupee landed and we did not hear about it, which
                              is what the poll fallback (`ledger_source: poll`)
                              exists for.
        error              -- every API call raises BAD_REQUEST_ERROR; `pay_link`
                              records a FAILED payment and emits nothing.
        duplicate_webhook  -- every delivery is pushed twice, same event id, same
                              bytes, same signature. Exercises replay-safety.
        out_of_order       -- forces both events and reverses them, so
                              `payment_link.paid` arrives before `payment.captured`.
                              Razorpay guarantees no ordering.
        wrong_amount       -- the emitted body carries amount + delta (default one
                              paisa). Signature stays valid, session id stays
                              valid, event stays in the green set: only the amount
                              gate can catch it.
        """
        if mode is None:
            mode = "normal"
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}, expected one of {MODES}")
        delta = int(paise(wrong_amount_delta_paise))
        if mode == "wrong_amount" and delta == 0:
            raise ValueError("wrong_amount with a zero delta is not a wrong amount")
        self._mode = mode
        self._wrong_amount_delta = delta

    def set_sink(self, sink: Callable[[Delivery], Any] | None) -> None:
        self._sink = sink

    # ------------------------------------------------------------------
    # deterministic identity
    # ------------------------------------------------------------------

    def _next(self, kind: str) -> int:
        n = self._counters.get(kind, 0) + 1
        self._counters[kind] = n
        return n

    def _token(self, kind: str, n: int, length: int) -> str:
        material = json.dumps(
            {"ns": self._account_id, "seed": self._seed, "kind": kind, "n": n},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return _b62(int.from_bytes(hashlib.sha256(material).digest(), "big"), length)

    def _mint_id(self, kind: str, prefix: str, length: int = ID_LEN) -> str:
        return prefix + self._token(kind, self._next(kind), length)

    def _now_unix(self) -> int:
        return iso_to_unix(self._clock.now_iso())

    # ------------------------------------------------------------------
    # failure injection gates
    # ------------------------------------------------------------------

    def _api_gate(self, what: str) -> None:
        if self._mode == "timeout":
            raise RazorpaySimTimeout(f"{what}: connection timed out (injected)")
        if self._mode == "error":
            raise RazorpaySimError(
                "SERVER_ERROR", f"{what}: the server encountered an error (injected)"
            )

    # ------------------------------------------------------------------
    # payment links
    # ------------------------------------------------------------------

    def create_payment_link(
        self,
        amount_paise: int,
        notes: Mapping[str, str],
        *,
        reference_id: str | None = None,
        description: str = "GAWAAH counter session",
        expire_by: int | None = None,
        currency: str = "INR",
        upi_link: bool = True,
        idempotent: bool = False,
        accept_partial: bool = False,
        first_min_partial_amount: int | None = None,
        reminder_enable: bool = False,
        notify: Mapping[str, Any] | None = None,
        customer: Mapping[str, Any] | None = None,
    ) -> dict:
        """POST /v1/payment_links.

        Returns the payment link entity, including the `short_url` **string**
        from which the counter renders the QR locally.

        `idempotent=True` implements the recover-then-return dance the caller
        must do against the real API when draining a retry outbox: a repeat of
        the same `reference_id` yields the existing link instead of an error.
        Default is False because the real API errors, and a simulator that is
        kinder than production is a trap.

        KHATA fields, faithful to the real API's names: `accept_partial` lets
        the customer pay in instalments and `first_min_partial_amount` floors
        the first one; `reminder_enable` with `notify.sms` and a `customer.
        contact` is what makes RAZORPAY send the reminders — this counter
        sends no message of its own, ever. The simulator records them on the
        entity and enforces the partial rules in `pay_link`; it sends nothing,
        because it is a simulator.
        """
        self._api_gate("create_payment_link")

        amt = int(paise(amount_paise))
        if amt < MIN_AMOUNT_PAISE:
            raise RazorpaySimError(
                "BAD_REQUEST_ERROR",
                f"amount must be at least {MIN_AMOUNT_PAISE} paise, got {amt}",
            )
        if currency != "INR":
            raise RazorpaySimError(
                "BAD_REQUEST_ERROR", f"only INR is supported, got {currency!r}"
            )
        clean_notes = _validate_notes(notes)
        if not isinstance(accept_partial, bool):
            raise RazorpaySimError(
                "BAD_REQUEST_ERROR", "accept_partial must be a boolean")
        min_partial = 0
        if first_min_partial_amount is not None:
            min_partial = int(paise(first_min_partial_amount))
            if not accept_partial:
                raise RazorpaySimError(
                    "BAD_REQUEST_ERROR",
                    "first_min_partial_amount needs accept_partial to be true")
            if min_partial < MIN_AMOUNT_PAISE or min_partial > amt:
                raise RazorpaySimError(
                    "BAD_REQUEST_ERROR",
                    f"first_min_partial_amount must be between {MIN_AMOUNT_PAISE} "
                    f"and the amount ({amt}), got {min_partial}")
        clean_notify = {"sms": False, "email": False}
        if notify is not None:
            if not isinstance(notify, Mapping):
                raise RazorpaySimError("BAD_REQUEST_ERROR", "notify must be an object")
            for k, v in notify.items():
                if k not in clean_notify or not isinstance(v, bool):
                    raise RazorpaySimError(
                        "BAD_REQUEST_ERROR", f"notify.{k} must be a boolean sms/email flag")
                clean_notify[k] = v
        clean_customer: dict[str, str] = {}
        if customer is not None:
            if not isinstance(customer, Mapping):
                raise RazorpaySimError("BAD_REQUEST_ERROR", "customer must be an object")
            for k in ("name", "contact", "email"):
                v = customer.get(k)
                if v is None:
                    continue
                if not isinstance(v, str):
                    raise RazorpaySimError(
                        "BAD_REQUEST_ERROR", f"customer.{k} must be a string")
                clean_customer[k] = v
        if clean_notify["sms"] and not clean_customer.get("contact"):
            raise RazorpaySimError(
                "BAD_REQUEST_ERROR",
                "notify.sms needs customer.contact: there is nobody to send it to")

        if reference_id is not None:
            if not isinstance(reference_id, str) or not reference_id:
                raise RazorpaySimError(
                    "BAD_REQUEST_ERROR", "reference_id must be a non-empty string"
                )
            existing = self._by_reference.get(reference_id)
            if existing is not None:
                if idempotent:
                    return self._link_view(self._links[existing])
                raise RazorpaySimError(
                    "BAD_REQUEST_ERROR",
                    "The reference id has already been used, "
                    "please provide a unique reference id",
                )

        now = self._now_unix()
        if expire_by is not None:
            expire_by = int(expire_by)
            if expire_by < now + MIN_EXPIRY_S:
                raise RazorpaySimError(
                    "BAD_REQUEST_ERROR",
                    f"expire_by should be at least {MIN_EXPIRY_S} seconds from now",
                )
        link_id = self._mint_id("payment_link", "plink_")
        short = SHORT_URL_PREFIX + self._token(
            "short_url", self._next("short_url"), SHORT_URL_LEN
        )
        link = {
            "id": link_id,
            "entity": "payment_link",
            "amount": amt,
            "amount_paid": 0,
            "currency": currency,
            "accept_partial": accept_partial,
            "first_min_partial_amount": min_partial,
            "description": description,
            "reference_id": reference_id or "",
            "expire_by": expire_by if expire_by is not None else now + DEFAULT_EXPIRY_S,
            "expired_at": 0,
            "reminder_enable": bool(reminder_enable),
            "notify": clean_notify,
            "customer": clean_customer,
            "status": "created",
            "short_url": short,
            "upi_link": bool(upi_link),
            "user_id": "",
            "notes": clean_notes,
            "payments": [],
            "created_at": now,
            "updated_at": now,
            # THE MARKER, ON THE ONE BODY THAT GETS STORED. The module docstring
            # has always promised this key on every body it emits; the payment
            # link — the only body that is written onto an order and shown to a
            # customer — was the one that did not carry it, so a simulated link
            # and a real one were byte-indistinguishable once saved.
            SIM_BODY_MARKER: True,
        }
        self._links[link_id] = link
        if reference_id:
            self._by_reference[reference_id] = link_id
        return self._link_view(link)

    def fetch_payment_link(self, link_id: str) -> dict:
        """GET /v1/payment_links/{id} — the reconnect reconciliation poll."""
        self._api_gate("fetch_payment_link")
        return self._link_view(self._require_link(link_id))

    def fetch_payment_links(
        self, *, reference_id: str | None = None, payment_id: str | None = None
    ) -> dict:
        """GET /v1/payment_links — collection, optionally filtered."""
        self._api_gate("fetch_payment_links")
        items = []
        for link in self._links.values():
            self._refresh(link)
            if reference_id is not None and link["reference_id"] != reference_id:
                continue
            if payment_id is not None and not any(
                p["payment_id"] == payment_id for p in link["payments"]
            ):
                continue
            items.append(self._link_view(link))
        return {"entity": "collection", "count": len(items), "items": items}

    def cancel_payment_link(self, link_id: str) -> dict:
        """POST /v1/payment_links/{id}/cancel.

        Only valid from `created`. A paid link can never be cancelled — that is
        the safety property, not a limitation."""
        self._api_gate("cancel_payment_link")
        link = self._require_link(link_id)
        if link["status"] != "created":
            raise RazorpaySimError(
                "BAD_REQUEST_ERROR",
                f"Payment link in {link['status']} state cannot be cancelled",
            )
        link["status"] = "cancelled"
        link["updated_at"] = self._now_unix()
        return self._link_view(link)

    def fetch_payments(
        self, *, payment_link_id: str | None = None, payment_id: str | None = None
    ) -> dict:
        """Payments collection, optionally filtered to one link."""
        self._api_gate("fetch_payments")
        items = []
        for pay in self._payments.values():
            if payment_id is not None and pay["id"] != payment_id:
                continue
            if payment_link_id is not None and pay["_link_id"] != payment_link_id:
                continue
            items.append(self._payment_view(pay))
        return {"entity": "collection", "count": len(items), "items": items}

    # ------------------------------------------------------------------
    # MILAN — settlements, derived from the payments above
    # ------------------------------------------------------------------
    #
    # Every method here is a READ over `_payments` except the on-demand batch,
    # which records WHEN a payment went out and never changes an amount. No
    # method here can capture, refund or mint anything.

    @staticmethod
    def _ist_day_index(unix: int) -> int:
        """Days since the epoch, counted on the gateway's IST calendar."""
        return (int(unix) + IST_OFFSET_S) // _DAY_S

    @staticmethod
    def _day_index_of(year: int, month: int, day: int) -> int:
        return _dt.date(int(year), int(month), int(day)).toordinal() - _UNIX_EPOCH_ORDINAL

    @staticmethod
    def _day_label(day_index: int) -> str:
        return _dt.date.fromordinal(int(day_index) + _UNIX_EPOCH_ORDINAL).isoformat()

    def _settlement_of(self, payment: dict) -> dict | None:
        """Where and when this payment settles, or None if it never will.

        Scheduled: the IST day it was captured plus T+1, filed at 00:00 IST,
        under a settlement id that is a pure function of that day (two runs of
        one script file identical batches, like every other id here). An
        on-demand sweep overrides the schedule for the payments it swept.
        """
        if payment.get("status") != "captured":
            return None
        pid = str(payment["id"])
        swept = self._ondemand.get(pid)
        if swept is not None:
            return dict(swept)
        due_index = self._ist_day_index(int(payment["created_at"])) + SETTLEMENT_T_PLUS_DAYS
        return {
            "settlement_id": "setl_" + self._token("settlement_day", due_index, ID_LEN),
            "settled_at": due_index * _DAY_S - IST_OFFSET_S,
            "day_index": due_index,
            "ondemand": False,
        }

    def _utr_of(self, settlement_id: str) -> str:
        digest = hashlib.sha256(settlement_id.encode("utf-8")).hexdigest()
        return "UTRSIM" + digest[:10].upper()

    def _refund_rows_of(self, payment: dict) -> list[dict]:
        """Refund rows for one payment, from whatever refund state exists.

        WAAPSI is adding refund entities to this simulator in the same build.
        If a `_refunds` table is present its entries are read by the real
        entity's field names (`id`, `payment_id`, `amount`, `created_at`);
        otherwise the payment's own `amount_refunded` — a field every payment
        here has carried from the start — is reported as one debit. Either
        way the figure is the simulator's, not this method's.
        """
        refunds = getattr(self, "_refunds", None)
        rows: list[dict] = []
        if isinstance(refunds, dict):
            for ref in refunds.values():
                if not isinstance(ref, dict) or ref.get("payment_id") != payment["id"]:
                    continue
                amt = ref.get("amount")
                if not isinstance(amt, int) or isinstance(amt, bool):
                    continue
                rows.append({
                    "entity_id": str(ref.get("id")),
                    "amount": int(amt),
                    "created_at": int(ref.get("created_at") or payment["created_at"]),
                })
            if rows:
                return rows
        refunded = payment.get("amount_refunded")
        if isinstance(refunded, int) and not isinstance(refunded, bool) and refunded > 0:
            rows.append({
                "entity_id": "rfnd_" + self._token("refund_of", int.from_bytes(
                    hashlib.sha256(str(payment["id"]).encode("utf-8")).digest()[:6], "big"),
                    ID_LEN),
                "amount": int(refunded),
                "created_at": int(payment["created_at"]),
            })
        return rows

    def _recon_rows(self) -> list[dict]:
        """Every settlement row this simulator can stand behind, undated."""
        out: list[dict] = []
        for pay in self._payments.values():
            setl = self._settlement_of(pay)
            if setl is None:
                continue
            amount = int(pay["amount"])
            fee = int(pay["fee"])
            tax = int(pay["tax"])
            base = {
                "settlement_id": setl["settlement_id"],
                "settled_at": int(setl["settled_at"]),
                "_day_index": int(setl["day_index"]),
                "settlement_utr": self._utr_of(setl["settlement_id"]),
                "settled": True,
                "on_hold": False,
                "currency": pay["currency"],
                "method": pay["method"],
                "order_id": pay["order_id"],
                "order_receipt": (self._links.get(pay["_link_id"]) or {}).get("reference_id"),
                "description": pay["description"],
                "notes": dict(pay["notes"]),
            }
            out.append({
                "entity_id": pay["id"],
                "type": "payment",
                "debit": 0,
                # The simulator's own convention: `fee` is the platform fee and
                # `tax` the GST on it, so both come off the top. (The real
                # entity folds tax INTO fee; `milan` therefore reads `credit`
                # rather than re-deriving it from either convention.)
                "credit": amount - fee - tax,
                "amount": amount,
                "fee": fee,
                "tax": tax,
                "created_at": int(pay["created_at"]),
                "payment_id": None,
                SIM_BODY_MARKER: True,
                **base,
            })
            for ref in self._refund_rows_of(pay):
                out.append({
                    "entity_id": ref["entity_id"],
                    "type": "refund",
                    "debit": int(ref["amount"]),
                    "credit": 0,
                    "amount": int(ref["amount"]),
                    "fee": 0,
                    "tax": 0,
                    "created_at": int(ref["created_at"]),
                    "payment_id": pay["id"],
                    SIM_BODY_MARKER: True,
                    **base,
                })
        return out

    def settlements_recon(self, *, year: int, month: int, day: int,
                          count: int = 1000, skip: int = 0) -> dict:
        """GET /v1/settlements/recon/combined?year&month&day — one IST day.

        Only batches that have GONE OUT by the simulator's clock are reported:
        a payment captured today is due tomorrow and is in nobody's report
        yet, which is exactly the T+1 gap `milan` names as
        `settled_not_yet_in_recon`.
        """
        self._api_gate("settlements_recon")
        wanted = self._day_index_of(year, month, day)
        now_index = self._ist_day_index(self._now_unix())
        items = []
        for row in self._recon_rows():
            if row["_day_index"] != wanted or row["_day_index"] > now_index:
                continue
            items.append({k: copy.deepcopy(v) for k, v in row.items() if k != "_day_index"})
        items.sort(key=lambda r: (r["settled_at"], r["entity_id"]))
        page = items[int(skip):int(skip) + int(count)]
        return {"entity": "collection", "count": len(page), "items": page}

    def fetch_settlements(self, *, count: int = 100, skip: int = 0) -> dict:
        """GET /v1/settlements — one entity per batch that has gone out."""
        self._api_gate("fetch_settlements")
        now_index = self._ist_day_index(self._now_unix())
        batches: dict[str, dict] = {}
        for row in self._recon_rows():
            if row["_day_index"] > now_index:
                continue
            b = batches.get(row["settlement_id"])
            if b is None:
                b = batches[row["settlement_id"]] = {
                    "id": row["settlement_id"],
                    "entity": "settlement",
                    "amount": 0,
                    "status": "processed",
                    "fees": 0,
                    "tax": 0,
                    "utr": row["settlement_utr"],
                    "created_at": int(row["settled_at"]),
                    SIM_BODY_MARKER: True,
                }
            b["amount"] += int(row["credit"]) - int(row["debit"])
            b["fees"] += int(row["fee"])
            b["tax"] += int(row["tax"])
        items = sorted(batches.values(), key=lambda b: (b["created_at"], b["id"]))
        page = items[int(skip):int(skip) + int(count)]
        return {"entity": "collection", "count": len(page), "items": page}

    def fetch_settlement(self, settlement_id: str) -> dict:
        """GET /v1/settlements/{id}."""
        self._api_gate("fetch_settlement")
        for b in self.fetch_settlements(count=10 ** 6)["items"]:
            if b["id"] == settlement_id:
                return b
        raise RazorpaySimError("BAD_REQUEST_ERROR",
                               f"settlement {settlement_id!r} does not exist")

    def create_ondemand_settlement(self, *, settle_full_balance: bool = True) -> dict:
        """POST /v1/settlements/ondemand — sweep what is captured and unsettled.

        Amounts are never touched: the sweep only moves the batch a payment
        belongs to from its T+1 day to now. Payments already in a batch that
        has gone out stay where they are; a sweep with nothing to sweep is an
        empty settlement, reported as such rather than refused.
        """
        self._api_gate("create_ondemand_settlement")
        if not settle_full_balance:
            raise RazorpaySimError("BAD_REQUEST_ERROR",
                                   "this simulator settles the full balance only")
        now = self._now_unix()
        now_index = self._ist_day_index(now)
        setl_id = self._mint_id("settlement", "setl_")
        swept: list[str] = []
        amount = fees = tax = 0
        for pay in self._payments.values():
            setl = self._settlement_of(pay)
            if setl is None or setl["day_index"] <= now_index:
                continue
            self._ondemand[str(pay["id"])] = {
                "settlement_id": setl_id, "settled_at": now,
                "day_index": now_index, "ondemand": True,
            }
            swept.append(str(pay["id"]))
            amount += int(pay["amount"]) - int(pay["fee"]) - int(pay["tax"])
            fees += int(pay["fee"])
            tax += int(pay["tax"])
        return {
            "id": setl_id,
            "entity": "settlement.ondemand",
            "amount_requested": amount,
            "amount_settled": amount,
            "amount_pending": 0,
            "fees": fees,
            "tax": tax,
            "currency": "INR",
            "settle_full_balance": True,
            "status": "processed",
            "created_at": now,
            "payment_ids": swept,
            SIM_BODY_MARKER: True,
        }

    # ------------------------------------------------------------------
    # the customer pays
    # ------------------------------------------------------------------

    def pay_link(
        self, link_id: str, *, method: str = "upi", emit_captured: bool = False,
        amount_paise: int | None = None,
    ) -> PayResult:
        """Simulate the customer paying, then emit the webhook(s).

        Deliberately NOT gated by `_api_gate`: the customer's phone is not our
        HTTP client. In `timeout` mode the money still moves and the webhook
        simply never arrives, which is the failure the poll fallback exists for.

        By default exactly ONE webhook goes out — `payment_link.paid`, the event
        the green state depends on. Pass `emit_captured=True` for the fuller
        real sequence (`payment.captured` then `payment_link.paid`).

        `amount_paise` is a PARTIAL payment, allowed only on a link minted with
        `accept_partial`, floored on the first instalment by
        `first_min_partial_amount` and capped at what is still due — the real
        API's rules. It emits `payment_link.partially_paid` while something
        remains and `payment_link.paid` on the instalment that clears it. As on
        the real entity, `amount` on the link is the ask for ever and
        `amount_paid` is the running total; the PAYMENT entity carries only
        what this instalment moved.
        """
        link = self._require_link(link_id)
        if link["status"] not in ("created", "partially_paid"):
            raise RazorpaySimError(
                "BAD_REQUEST_ERROR",
                f"Payment link in {link['status']} state is not payable",
            )

        now = self._now_unix()
        ask = int(link["amount"])
        already = int(link["amount_paid"])
        due = ask - already
        if amount_paise is None:
            amt = due
        else:
            amt = int(paise(amount_paise))
            if amt < MIN_AMOUNT_PAISE:
                raise RazorpaySimError(
                    "BAD_REQUEST_ERROR",
                    f"amount must be at least {MIN_AMOUNT_PAISE} paise, got {amt}")
            if amt > due:
                raise RazorpaySimError(
                    "BAD_REQUEST_ERROR",
                    f"amount {amt} exceeds the {due} paise still due on this link")
            if amt != due and not link["accept_partial"]:
                raise RazorpaySimError(
                    "BAD_REQUEST_ERROR",
                    "This payment link does not accept partial payments")
            if already == 0 and amt < int(link["first_min_partial_amount"]):
                raise RazorpaySimError(
                    "BAD_REQUEST_ERROR",
                    f"the first payment must be at least "
                    f"{link['first_min_partial_amount']} paise, got {amt}")
        order_id = self._mint_id("order", "order_")
        pay_id = self._mint_id("payment", "pay_")
        failed = self._mode == "error"
        paid_after = already if failed else already + amt

        fee = amt * FEE_BPS // BPS_DEN
        tax = fee * GST_BPS // BPS_DEN
        rrn = str(
            int.from_bytes(
                hashlib.sha256(pay_id.encode("utf-8")).digest()[:8], "big"
            ) % 1000000000000
        ).zfill(12)

        order = {
            "id": order_id,
            "entity": "order",
            "amount": ask,
            "amount_paid": paid_after,
            "amount_due": ask - paid_after,
            "currency": link["currency"],
            "receipt": link["reference_id"],
            "status": "attempted" if failed else ("paid" if paid_after == ask else "attempted"),
            "attempts": 1,
            "notes": dict(link["notes"]),
            "created_at": now,
        }
        payment = {
            "id": pay_id,
            "entity": "payment",
            "amount": amt,
            "currency": link["currency"],
            "status": "failed" if failed else "captured",
            "order_id": order_id,
            "invoice_id": None,
            "international": False,
            "method": method,
            "amount_refunded": 0,
            "refund_status": None,
            "captured": not failed,
            "description": link["description"],
            "card_id": None,
            "bank": None,
            "wallet": None,
            "vpa": SIM_VPA,
            "email": SIM_EMAIL,
            "contact": SIM_CONTACT,
            "notes": dict(link["notes"]),
            "fee": 0 if failed else fee,
            "tax": 0 if failed else tax,
            "error_code": "BAD_REQUEST_ERROR" if failed else None,
            "error_description": "payment failed (injected)" if failed else None,
            "acquirer_data": {"rrn": rrn},
            "created_at": now,
            "_link_id": link_id,
        }
        self._orders[order_id] = order
        self._payments[pay_id] = payment

        if failed:
            # link stays payable; nothing goes on the wire
            return PayResult(self._payment_view(payment), self._link_view(link), ())

        link["amount_paid"] = paid_after
        link["status"] = "paid" if paid_after == ask else "partially_paid"
        link["updated_at"] = now
        link["payments"].append(
            {
                "payment_id": pay_id,
                "created_at": now,
                "method": method,
                "amount": amt,
                "status": "captured",
            }
        )

        link_event = "payment_link.paid" if link["status"] == "paid" else "payment_link.partially_paid"
        events = [link_event]
        if emit_captured or self._mode == "out_of_order":
            events = ["payment.captured", link_event]
        if self._mode == "out_of_order":
            events.reverse()

        specs = [self._build(ev, link, payment, order, now) for ev in events]
        if self._mode == "duplicate_webhook":
            # same event id, same bytes, same signature — a true replay, not a
            # second event that happens to look similar
            specs = [s for s in specs for _ in (0, 1)]

        sent = tuple(self._push(s) for s in specs)
        return PayResult(self._payment_view(payment), self._link_view(link), sent)

    # ------------------------------------------------------------------
    # WAAPSI: refunds
    # ------------------------------------------------------------------
    #
    # `refund` is the API call — POST /v1/payments/{id}/refund — and, like
    # `create_payment_link`, it emits NOTHING: the entity comes back
    # `pending` and the money is not shown to have moved. `process_refund`
    # is the far side doing the work minutes later and pushing the signed
    # `refund.processed` at the sink; `fail_refund` is the other outcome.
    # Splitting them is what lets the demo show a refund sitting in
    # "requested" — the state a real test-mode refund sits in for minutes —
    # and then turning REFUNDED on the callback and on nothing else.

    def refund(
        self, payment_id: str, amount_paise: int | None = None, *,
        speed: str = "normal", receipt: str | None = None,
        notes: Mapping[str, str] | None = None,
    ) -> dict:
        """POST /v1/payments/{id}/refund. Returns the refund entity, `pending`.

        The real API's rules: the payment must be captured; the amount is
        integer paise, at least Rs 1, and no more than what is still
        refundable on the payment (`amount - amount_refunded`); `speed` is
        `normal` or `optimum`; `notes` is capped like every other notes
        object. The payment's own `amount_refunded` / `refund_status` move
        HERE, at request time, exactly as the real entity's do.
        """
        self._api_gate("refund")
        pay = self._payments.get(payment_id)
        if pay is None:
            raise RazorpaySimError(
                "BAD_REQUEST_ERROR", f"The id provided does not exist: {payment_id!r}")
        if pay["status"] != "captured":
            raise RazorpaySimError(
                "BAD_REQUEST_ERROR",
                f"payment {payment_id} is {pay['status']!r}; only a captured "
                "payment can be refunded")
        refundable = int(pay["amount"]) - int(pay["amount_refunded"])
        amt = refundable if amount_paise is None else int(paise(amount_paise))
        if amt < MIN_AMOUNT_PAISE:
            raise RazorpaySimError(
                "BAD_REQUEST_ERROR",
                f"amount must be at least {MIN_AMOUNT_PAISE} paise, got {amt}")
        if amt > refundable:
            raise RazorpaySimError(
                "BAD_REQUEST_ERROR",
                f"The refund amount provided ({amt}) is greater than the amount "
                f"still refundable on this payment ({refundable})")
        if speed not in REFUND_SPEEDS:
            raise RazorpaySimError(
                "BAD_REQUEST_ERROR", f"speed must be one of {REFUND_SPEEDS}, got {speed!r}")
        if receipt is not None and (not isinstance(receipt, str) or len(receipt) > 40):
            raise RazorpaySimError(
                "BAD_REQUEST_ERROR", "receipt must be a string of at most 40 characters")
        clean_notes = _validate_notes(notes or {})

        now = self._now_unix()
        rid = self._mint_id("refund", "rfnd_")
        refund = {
            "id": rid,
            "entity": "refund",
            "amount": amt,
            "currency": pay["currency"],
            "payment_id": payment_id,
            "notes": clean_notes,
            "receipt": receipt,
            "acquirer_data": {"arn": None},
            "created_at": now,
            "batch_id": None,
            "status": "pending",
            "speed_processed": None,
            "speed_requested": speed,
            SIM_BODY_MARKER: True,
        }
        pay["amount_refunded"] = int(pay["amount_refunded"]) + amt
        pay["refund_status"] = (
            "full" if pay["amount_refunded"] == int(pay["amount"]) else "partial")
        self._refunds[rid] = refund
        return self._refund_view(refund)

    def fetch_refund(self, refund_id: str) -> dict:
        """GET /v1/refunds/{id}."""
        self._api_gate("fetch_refund")
        return self._refund_view(self._require_refund(refund_id))

    def fetch_refunds(self, *, payment_id: str | None = None) -> dict:
        """GET /v1/refunds, or /v1/payments/{id}/refunds when narrowed."""
        self._api_gate("fetch_refunds")
        items = [self._refund_view(r) for r in self._refunds.values()
                 if payment_id is None or r["payment_id"] == payment_id]
        return {"entity": "collection", "count": len(items), "items": items}

    def process_refund(self, refund_id: str, *, emit_created: bool = False
                       ) -> RefundResult:
        """The far side does the refund and pushes the SIGNED `refund.processed`.

        Deliberately NOT gated by `_api_gate`: the gateway's back office is
        not our HTTP client. `wrong_amount` mode puts the delta on the refund
        entity's amount so only the kernel's amount gate can catch it;
        `duplicate_webhook` pushes the same signed bytes twice.
        """
        ref = self._require_refund(refund_id)
        if ref["status"] != "pending":
            raise RazorpaySimError(
                "BAD_REQUEST_ERROR",
                f"refund {refund_id} is {ref['status']!r}, not pending")
        now = self._now_unix()
        ref["status"] = "processed"
        ref["speed_processed"] = "instant" if ref["speed_requested"] == "optimum" else "normal"
        ref["processed_at"] = now
        pay = self._payments[ref["payment_id"]]
        events = ["refund.created", "refund.processed"] if emit_created else ["refund.processed"]
        specs = [self._build_refund(ev, ref, pay, now) for ev in events]
        if self._mode == "duplicate_webhook":
            specs = [s for s in specs for _ in (0, 1)]
        sent = tuple(self._push(s) for s in specs)
        return RefundResult(self._refund_view(ref), self._payment_view(pay), sent)

    def fail_refund(self, refund_id: str) -> RefundResult:
        """The far side could not refund: the money stays with us and the
        payment's `amount_refunded` is given back, then a SIGNED
        `refund.failed` goes out."""
        ref = self._require_refund(refund_id)
        if ref["status"] != "pending":
            raise RazorpaySimError(
                "BAD_REQUEST_ERROR",
                f"refund {refund_id} is {ref['status']!r}, not pending")
        now = self._now_unix()
        ref["status"] = "failed"
        pay = self._payments[ref["payment_id"]]
        pay["amount_refunded"] = int(pay["amount_refunded"]) - int(ref["amount"])
        pay["refund_status"] = (
            None if pay["amount_refunded"] == 0
            else "full" if pay["amount_refunded"] == int(pay["amount"]) else "partial")
        spec = self._build_refund("refund.failed", ref, pay, now)
        sent = (self._push(spec),)
        return RefundResult(self._refund_view(ref), self._payment_view(pay), sent)

    def _build_refund(self, event: str, refund: dict, payment: dict, now: int
                      ) -> "_Spec":
        """A signed refund event, in the real envelope: `contains` names the
        refund and the payment, `payload.refund.entity` is the refund."""
        if event not in EVENTS or not event.startswith("refund."):
            raise ValueError(f"{event!r} is not a refund event this simulator emits")
        delta = self._wrong_amount_delta if self._mode == "wrong_amount" else 0
        ref_body = {k: v for k, v in refund.items() if not k.startswith("_")}
        ref_body["amount"] = int(ref_body["amount"]) + delta
        pay_body = {k: v for k, v in payment.items() if not k.startswith("_")}
        body_obj = {
            "entity": "event",
            "account_id": self._account_id,
            "event": event,
            "contains": ["refund", "payment"],
            "payload": {
                "refund": {"entity": ref_body},
                "payment": {"entity": pay_body},
            },
            "created_at": now,
            SIM_BODY_MARKER: True,
        }
        body = serialize_body(body_obj)
        event_id = self._token("event", self._next("event"), ID_LEN)
        headers = MappingProxyType(
            {
                "Content-Type": "application/json",
                "X-Razorpay-Event-Id": event_id,
                "X-Razorpay-Signature": sign_body(body, self._secret),
            }
        )
        return _Spec(event=event, event_id=event_id, headers=headers, body=body)

    def _require_refund(self, refund_id: str) -> dict:
        ref = self._refunds.get(refund_id)
        if ref is None:
            raise RazorpaySimError(
                "BAD_REQUEST_ERROR", f"refund {refund_id!r} does not exist")
        return ref

    def _refund_view(self, refund: dict) -> dict:
        return {k: copy.deepcopy(v) for k, v in refund.items() if not k.startswith("_")}

    # ------------------------------------------------------------------
    # webhook construction
    # ------------------------------------------------------------------

    def _build(
        self, event: str, link: dict, payment: dict, order: dict, now: int
    ) -> "_Spec":
        if event not in EVENTS:
            raise ValueError(f"{event!r} is not an event this simulator emits")

        delta = self._wrong_amount_delta if self._mode == "wrong_amount" else 0

        pay_body = {k: v for k, v in payment.items() if not k.startswith("_")}
        pay_body["amount"] = int(pay_body["amount"]) + delta

        if event == "payment.captured":
            contains = ["payment"]
            payload: dict[str, Any] = {"payment": {"entity": pay_body}}
        else:
            link_body = {k: v for k, v in link.items() if k != "payments"}
            link_body["amount"] = int(link_body["amount"]) + delta
            link_body["amount_paid"] = int(link_body["amount_paid"]) + delta
            order_body = dict(order)
            order_body["amount"] = int(order_body["amount"]) + delta
            order_body["amount_paid"] = int(order_body["amount_paid"]) + delta
            contains = ["payment_link", "payment", "order"]
            payload = {
                "payment_link": {"entity": link_body},
                "payment": {"entity": pay_body},
                "order": {"entity": order_body},
            }

        # Insertion order is the real Razorpay field order and is NOT sorted.
        body_obj = {
            "entity": "event",
            "account_id": self._account_id,
            "event": event,
            "contains": contains,
            "payload": payload,
            "created_at": now,
            SIM_BODY_MARKER: True,
        }
        body = serialize_body(body_obj)
        event_id = self._token("event", self._next("event"), ID_LEN)
        headers = MappingProxyType(
            {
                "Content-Type": "application/json",
                "X-Razorpay-Event-Id": event_id,
                "X-Razorpay-Signature": sign_body(body, self._secret),
            }
        )
        return _Spec(event=event, event_id=event_id, headers=headers, body=body)

    def _push(self, spec: "_Spec") -> Delivery:
        """Attempt the push, then record what actually happened. The Delivery is
        built once, after the fact, so it is never a lie about its own state."""
        delivered = True
        error: str | None = None
        if self._mode == "timeout":
            delivered, error = False, "simulated network timeout"
        elif self._sink is not None:
            try:
                self._sink(spec.as_delivery(len(self._deliveries)))
            except Exception as exc:
                # a bad endpoint is not a server crash: Razorpay records the
                # failure and moves on. We do not retry.
                delivered, error = False, repr(exc)

        d = spec.as_delivery(len(self._deliveries), delivered=delivered, error=error)
        self._deliveries.append(d)
        if self._ledger is not None:
            self._ledger.append(
                ts=self._clock.now_iso(),
                module="rzp_sim",
                event=d.event,
                event_id=d.event_id,
                body_sha256=d.body_sha256,
                delivered=d.delivered,
                mode=self._mode,
                simulated=True,
            )
        return d

    # ------------------------------------------------------------------
    # observation
    # ------------------------------------------------------------------

    @property
    def deliveries(self) -> tuple[Delivery, ...]:
        """Every webhook the simulated server produced, delivered or not."""
        return tuple(self._deliveries)

    @property
    def delivered_to_sink(self) -> tuple[Delivery, ...]:
        return tuple(d for d in self._deliveries if d.delivered)

    def transcript(self) -> str:
        """A stable, diffable rendering of everything this instance produced.

        Two runs of the same script must produce identical transcripts; that is
        what `test_ids_are_deterministic_across_two_processes` compares."""
        lines = []
        for d in self._deliveries:
            lines.append(
                "|".join(
                    [
                        str(d.seq),
                        d.event,
                        d.event_id,
                        d.signature,
                        d.body_sha256,
                        "1" if d.delivered else "0",
                    ]
                )
            )
        for lid in sorted(self._links):
            link = self._links[lid]
            lines.append(
                "|".join(
                    [
                        "link",
                        lid,
                        link["short_url"],
                        link["status"],
                        str(link["amount"]),
                        str(link["amount_paid"]),
                    ]
                )
            )
        for pid in sorted(self._payments):
            p = self._payments[pid]
            lines.append("|".join(["pay", pid, p["status"], str(p["amount"])]))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _require_link(self, link_id: str) -> dict:
        link = self._links.get(link_id)
        if link is None:
            raise RazorpaySimError(
                "BAD_REQUEST_ERROR", f"payment link {link_id!r} does not exist"
            )
        self._refresh(link)
        return link

    def _refresh(self, link: dict) -> None:
        """Lazily flip created -> expired once `expire_by` has passed."""
        if link["status"] != "created":
            return
        now = self._now_unix()
        if now > int(link["expire_by"]):
            link["status"] = "expired"
            link["expired_at"] = now
            link["updated_at"] = now

    def _link_view(self, link: dict) -> dict:
        return copy.deepcopy(link)

    def _payment_view(self, payment: dict) -> dict:
        return {k: copy.deepcopy(v) for k, v in payment.items() if not k.startswith("_")}
