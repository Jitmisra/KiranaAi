"""S4b — LOCAL RAZORPAY SIMULATOR.

There are no real Razorpay keys yet (gates G0/G1/G2 are blocked on a human
signup), so the entire money path has to be exercisable today and swapping in
real keys must be a configuration change, not a rewrite. This module plays the
role of the *remote server*: it mints payment links, simulates a customer
paying one, and pushes a signed webhook at a sink exactly the way Razorpay
pushes one at a `cloudflared` tunnel.

What is deliberately faithful
-----------------------------
* `create_payment_link` returns `{id: "plink_...", short_url: "https://rzp.io/i/...",
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
Every body this module emits carries a top-level `"_gawaah_sim": true` key.
Real Razorpay bodies do not have it. So a simulated green can always be told
apart from a real one in the ledger, and no fixture produced here can be passed
off as a genuine event. Invariant: honesty over convenience.

INVARIANT 6 — NO FORGERY PRIMITIVES. This module never constructs, parses or
regenerates a UPI payload. `short_url` is an opaque token *we* mint under
`https://rzp.io/i/`; nothing here can produce a `upi://` intent string, and
`test_no_upi_payload_is_ever_constructed` asserts that over every byte the
module emits.

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
EVENTS: tuple[str, ...] = ("payment_link.paid", "payment.captured")

#: Failure injections. "normal" is the absence of injection.
MODES: tuple[str, ...] = (
    "normal",
    "timeout",
    "error",
    "duplicate_webhook",
    "out_of_order",
    "wrong_amount",
)

SHORT_URL_PREFIX = "https://rzp.io/i/"
SHORT_URL_LEN = 7          # real ones look like https://rzp.io/i/nxrHnLJ
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
    ) -> dict:
        """POST /v1/payment_links.

        Returns the payment link entity, including the `short_url` **string**
        from which the counter renders the QR locally.

        `idempotent=True` implements the recover-then-return dance the caller
        must do against the real API when draining a retry outbox: a repeat of
        the same `reference_id` yields the existing link instead of an error.
        Default is False because the real API errors, and a simulator that is
        kinder than production is a trap.
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
            "accept_partial": False,
            "first_min_partial_amount": 0,
            "description": description,
            "reference_id": reference_id or "",
            "expire_by": expire_by if expire_by is not None else now + DEFAULT_EXPIRY_S,
            "expired_at": 0,
            "reminder_enable": False,
            "status": "created",
            "short_url": short,
            "upi_link": bool(upi_link),
            "user_id": "",
            "notes": clean_notes,
            "payments": [],
            "created_at": now,
            "updated_at": now,
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
    # the customer pays
    # ------------------------------------------------------------------

    def pay_link(
        self, link_id: str, *, method: str = "upi", emit_captured: bool = False
    ) -> PayResult:
        """Simulate the customer paying, then emit the webhook(s).

        Deliberately NOT gated by `_api_gate`: the customer's phone is not our
        HTTP client. In `timeout` mode the money still moves and the webhook
        simply never arrives, which is the failure the poll fallback exists for.

        By default exactly ONE webhook goes out — `payment_link.paid`, the event
        the green state depends on. Pass `emit_captured=True` for the fuller
        real sequence (`payment.captured` then `payment_link.paid`).
        """
        link = self._require_link(link_id)
        if link["status"] != "created":
            raise RazorpaySimError(
                "BAD_REQUEST_ERROR",
                f"Payment link in {link['status']} state is not payable",
            )

        now = self._now_unix()
        amt = int(link["amount"])
        order_id = self._mint_id("order", "order_")
        pay_id = self._mint_id("payment", "pay_")
        failed = self._mode == "error"

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
            "amount": amt,
            "amount_paid": 0 if failed else amt,
            "amount_due": amt if failed else 0,
            "currency": link["currency"],
            "receipt": link["reference_id"],
            "status": "attempted" if failed else "paid",
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

        link["status"] = "paid"
        link["amount_paid"] = amt
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

        events = ["payment_link.paid"]
        if emit_captured or self._mode == "out_of_order":
            events = ["payment.captured", "payment_link.paid"]
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
