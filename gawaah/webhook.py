"""S4c — webhook verification and the GREEN predicate. This is INVARIANT 2.

Green happens here or it does not happen. Nothing else in GAWAAH may emit green:
not a mint, not a render, not an OCR read, not a timer, not a confident model.

    GREEN  ==  valid HMAC-SHA256 over the RAW BYTES
           AND event in GREEN_EVENTS
           AND notes.session_id names an OPEN intent
           AND amount == intent.amount_paise   (exact integer compare)

Four properties this module is built to guarantee, each pinned by a test:

  1. The signature is checked over the bytes that arrived on the wire, BEFORE
     json.loads is ever called. A body that re-serialises to different bytes is
     a different body, and it fails. `verify_signature` is the first gate in
     `evaluate` and there is no parse above it.

  2. Every failure has its own machine-readable code from a closed vocabulary
     (REASON_CODES). A bare False tells an operator nothing at 11pm.

  3. The replay key is derived from the SIGNED bytes, never from the
     `X-Razorpay-Event-Id` header alone. The header is not covered by the HMAC,
     so an attacker replaying a captured body could simply vary it. The header
     id is checked as an *additional* key, never as a substitute.

  4. Only a GREEN verdict marks an event as seen. A webhook that fails for a
     transient reason (intent not yet visible, mirror stale) must still be able
     to succeed when Razorpay retries it — that is what retries are for.

Deliberately absent: any function that signs a body. This module verifies; it
never produces a signature, and it never constructs a payment payload.

No float appears anywhere in this file (INVARIANT 1). `json.loads` is called
with `parse_float=str` and a `parse_constant` that refuses NaN/Infinity, so a
float object is never even materialised from an attacker-supplied body.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

from .clock import Clock
from .ledger import Ledger
from .money import MoneyError, paise

# ---------------------------------------------------------------- vocabulary

GREEN_EVENTS: frozenset[str] = frozenset({"payment_link.paid", "payment.captured"})

#: The event envelope keys that may carry the money, and the status each one
#: must report. A `payment.captured` whose entity says "failed" is a
#: contradiction, not a payment.
_ENTITY_STATUS: dict[str, str] = {"payment": "captured", "payment_link": "paid"}

CURRENCY = "INR"

REASON_CODES: frozenset[str] = frozenset(
    {
        "green",
        "secret_not_configured",
        "bad_signature",
        "malformed_body",
        "replay",
        "missing_event",
        "event_not_green",
        "no_entity",
        "entity_status_not_paid",
        "wrong_currency",
        "missing_session_id",
        "session_id_conflict",
        "unknown_session",
        "intent_not_open",
        "amount_missing",
        "amount_not_integer",
        "amount_conflict",
        "amount_mismatch",
        "intent_amount_invalid",
    }
)

#: The only verdict that is allowed to be RED without a human in the loop.
#: A mismatch is a positive contradiction — we hold and a person resolves it.
#: Everything else is absence of evidence, which is AMBER (INVARIANT 7).
_RED_REASONS: frozenset[str] = frozenset({"amount_mismatch", "amount_conflict"})

GREEN, AMBER, RED = "GREEN", "AMBER", "RED"


class WebhookError(ValueError):
    """Raised for programmer error at the boundary, never for a bad payload."""


# ---------------------------------------------------------------- intents


@dataclass(frozen=True)
class Intent:
    """A payment target this counter minted. Supplied by `paisa`.

    `evaluate` also accepts any object exposing `.amount_paise` and `.state`,
    or a plain mapping with those keys, so the owning module is free to use its
    own type.
    """

    session_id: str
    amount_paise: int
    state: str = "OPEN"


def _intent_fields(intent: Any) -> tuple[Any, Optional[str]]:
    """Pull (amount_paise, state) out of a dataclass-ish or dict-ish intent."""
    if isinstance(intent, dict):
        amount = intent.get("amount_paise")
        state = intent.get("state", intent.get("status"))
    else:
        amount = getattr(intent, "amount_paise", None)
        state = getattr(intent, "state", getattr(intent, "status", None))
    return amount, (state if isinstance(state, str) else None)


# ---------------------------------------------------------------- signature


def verify_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """HMAC-SHA256 over the RAW BYTES, compared in constant time.

    This is the whole security boundary. It takes bytes, not a parsed object,
    because the thing Razorpay signed is the octet sequence — two JSON
    documents that are semantically identical are cryptographically different.

    Returns False rather than raising for every malformed *input from the
    network* (bad hex, wrong length, empty signature). Raises only when the
    caller has made a type error, which is a bug in our own code.
    """
    if isinstance(raw_body, (bytearray, memoryview)):
        raw_body = bytes(raw_body)
    if not isinstance(raw_body, bytes):
        raise WebhookError(
            f"raw_body must be bytes, got {type(raw_body).__name__}. "
            "The signature covers the bytes on the wire; a str has already "
            "been through a decode and is no longer the signed object."
        )

    key = _as_ascii_bytes(secret, encoding="utf-8")
    if not key:
        # An empty webhook secret makes the HMAC forgeable by anyone who knows
        # the algorithm. Refuse loudly-in-the-verdict rather than authenticate.
        return False

    provided = _as_ascii_bytes(signature, encoding="ascii")
    if not provided:
        return False

    expected = hmac.new(key=key, msg=raw_body, digestmod=hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode("ascii"), provided)


def _as_ascii_bytes(value: Any, *, encoding: str) -> bytes:
    """Normalise a str/bytes credential to bytes; b'' means 'unusable'."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str):
        try:
            return value.encode(encoding)
        except UnicodeEncodeError:
            # A hex digest is ASCII by construction. Anything else is not a
            # signature, and must not be allowed to raise inside the gate.
            return b""
    return b""


# ---------------------------------------------------------------- verdict


@dataclass(frozen=True)
class GreenVerdict:
    """The full, auditable outcome of one webhook delivery.

    `green` is the only field the state machine may act on. Everything else is
    there so that a failure can be explained without re-reading the payload.
    """

    green: bool
    reason: str
    severity: str
    detail: str = ""
    signature_valid: bool = False
    event: Optional[str] = None
    event_id: Optional[str] = None
    session_id: Optional[str] = None
    amount_paise: Optional[int] = None
    expected_paise: Optional[int] = None
    body_sha256: str = ""
    mirror_stale: bool = False
    downgraded_from_red: bool = False

    def __post_init__(self) -> None:
        if self.reason not in REASON_CODES:
            raise WebhookError(f"reason {self.reason!r} is outside REASON_CODES")
        if self.green and self.reason != "green":
            raise WebhookError("green verdict must carry reason 'green'")
        if self.green and self.severity != GREEN:
            raise WebhookError("green verdict must carry severity GREEN")
        if self.mirror_stale and self.severity == RED:
            raise WebhookError(
                "a stale mirror can never produce RED — see the stale-ledger rule"
            )


class SeenStore(Protocol):
    def __contains__(self, key: object) -> bool: ...
    def add(self, key: str) -> None: ...


# ---------------------------------------------------------------- predicate


class GreenPredicate:
    """The single place in GAWAAH where a session is allowed to turn green.

    Parameters
    ----------
    open_intents_lookup:
        ``session_id -> intent | None``. Must return None for a session it does
        not know about, and should return only OPEN intents; a returned intent
        whose state is not OPEN is rejected here as well, as belt and braces.
    seen:
        replay store. Anything with ``__contains__`` and ``add``. Defaults to an
        in-process set; pass a durable store in production so a restart does not
        forget which events have already paid.
    ledger / clock:
        optional. When both are given, every evaluation appends one audit line.
        The line never contains the secret, the signature, or the raw body —
        only the body's sha256, so a delivery can be matched without the ability
        to replay it.
    """

    def __init__(
        self,
        open_intents_lookup: Callable[[str], Any],
        *,
        seen: Optional[SeenStore] = None,
        ledger: Optional[Ledger] = None,
        clock: Optional[Clock] = None,
    ) -> None:
        if not callable(open_intents_lookup):
            raise WebhookError("open_intents_lookup must be callable")
        if (ledger is None) != (clock is None):
            raise WebhookError("ledger and clock must be supplied together")
        self._lookup = open_intents_lookup
        self._seen: SeenStore = seen if seen is not None else set()
        self._ledger = ledger
        self._clock = clock

    # -- public ---------------------------------------------------------

    @property
    def seen(self) -> SeenStore:
        return self._seen

    def evaluate(
        self,
        raw_body: bytes,
        signature: str,
        secret: str,
        *,
        header_event_id: Optional[str] = None,
        mirror_stale: bool = False,
    ) -> GreenVerdict:
        """Run the four-part green predicate over one webhook delivery.

        `header_event_id` is the untrusted `X-Razorpay-Event-Id` header. It is
        used only as an extra replay key; it can never make a body green and it
        can never change which body was verified.

        `mirror_stale` is the caller's report that our Razorpay mirror has not
        been refreshed recently. It never blocks green — a verified webhook is
        fresh evidence — but it downgrades any RED to AMBER, because a system
        that might be missing events is not entitled to contradict a customer.
        """
        verdict = self._evaluate(
            raw_body, signature, secret, header_event_id, bool(mirror_stale)
        )
        self._audit(verdict)
        return verdict

    # -- the gates, in order --------------------------------------------

    def _evaluate(
        self,
        raw_body: bytes,
        signature: str,
        secret: str,
        header_event_id: Optional[str],
        mirror_stale: bool,
    ) -> GreenVerdict:
        if isinstance(raw_body, (bytearray, memoryview)):
            raw_body = bytes(raw_body)
        if not isinstance(raw_body, bytes):
            raise WebhookError(
                f"raw_body must be bytes, got {type(raw_body).__name__}"
            )
        body_sha = hashlib.sha256(raw_body).hexdigest()

        def deny(reason: str, detail: str, **extra: Any) -> GreenVerdict:
            severity = RED if reason in _RED_REASONS else AMBER
            downgraded = severity == RED and mirror_stale
            if downgraded:
                severity = AMBER
            return GreenVerdict(
                green=False,
                reason=reason,
                severity=severity,
                detail=detail,
                body_sha256=body_sha,
                mirror_stale=mirror_stale,
                downgraded_from_red=downgraded,
                **extra,
            )

        # GATE 0 — a missing secret is a deployment error, not an authenticated
        # request. Checked separately so it can never be read as "bad payload".
        if not _as_ascii_bytes(secret, encoding="utf-8"):
            return deny(
                "secret_not_configured",
                "RAZORPAY_WEBHOOK_SECRET is empty; every signature would be forgeable",
            )

        # GATE 1 — signature over raw bytes. NOTHING is parsed above this line.
        if not verify_signature(raw_body, signature, secret):
            return deny(
                "bad_signature",
                "HMAC-SHA256 over the raw request body did not match "
                "X-Razorpay-Signature; body discarded unparsed",
            )

        # Only now is it safe to look inside.
        parsed = _parse_body(raw_body)
        if parsed is None:
            return deny(
                "malformed_body",
                "signature valid but body is not a JSON object "
                "(or contains NaN/Infinity)",
                signature_valid=True,
            )

        ok: dict[str, Any] = {"signature_valid": True}

        # GATE 2 — replay. Keyed on the signed bytes; the header id is an extra
        # key only, because the header is outside the HMAC.
        body_event_id = parsed.get("id")
        primary_id = body_event_id if isinstance(body_event_id, str) and body_event_id else body_sha
        keys = [primary_id]
        if isinstance(header_event_id, str) and header_event_id:
            keys.append(header_event_id)
        for k in keys:
            if k in self._seen:
                return deny(
                    "replay",
                    f"event id {k[:24]}… has already been settled; not re-greening",
                    event_id=primary_id,
                    event=parsed.get("event") if isinstance(parsed.get("event"), str) else None,
                    **ok,
                )

        ok["event_id"] = primary_id

        # GATE 3 — event type.
        event = parsed.get("event")
        if not isinstance(event, str) or not event:
            return deny("missing_event", "no 'event' string in the envelope", **ok)
        ok["event"] = event
        if event not in GREEN_EVENTS:
            return deny(
                "event_not_green",
                f"{event!r} is not in GREEN_EVENTS {sorted(GREEN_EVENTS)}",
                **ok,
            )

        payload = parsed.get("payload")
        entities = _entities(payload)
        if not entities:
            return deny(
                "no_entity",
                "payload carries neither payment.entity nor payment_link.entity",
                **ok,
            )

        # GATE 3b — the entity must agree that money moved, and in rupees.
        for key, ent in entities.items():
            status = ent.get("status")
            want = _ENTITY_STATUS[key]
            if isinstance(status, str) and status != want:
                return deny(
                    "entity_status_not_paid",
                    f"{key}.entity.status is {status!r}, expected {want!r}",
                    **ok,
                )
            currency = ent.get("currency")
            if currency is not None and currency != CURRENCY:
                return deny(
                    "wrong_currency",
                    f"{key}.entity.currency is {currency!r}, not {CURRENCY}; "
                    "an amount is only money once you know the unit",
                    **ok,
                )

        # GATE 4 — notes.session_id.
        session_ids = _collect(entities, lambda e: _note(e, "session_id"))
        if not session_ids:
            return deny(
                "missing_session_id",
                "no notes.session_id on any entity; this target was not minted "
                "by us, or notes did not propagate",
                **ok,
            )
        if len(set(session_ids.values())) > 1:
            return deny(
                "session_id_conflict",
                f"entities disagree on session_id: {session_ids}",
                **ok,
            )
        session_id = next(iter(session_ids.values()))
        ok["session_id"] = session_id

        try:
            intent = self._lookup(session_id)
        except Exception as exc:  # a broken lookup must never green, and never 500
            return deny(
                "unknown_session",
                f"open-intent lookup raised {type(exc).__name__}: {exc}",
                **ok,
            )
        if intent is None:
            return deny(
                "unknown_session",
                f"session {session_id!r} is not an open intent minted by this counter",
                **ok,
            )
        intent_amount, state = _intent_fields(intent)
        if state is not None and state.upper() != "OPEN":
            return deny(
                "intent_not_open",
                f"intent {session_id!r} is in state {state!r}, not OPEN",
                **ok,
            )
        try:
            expected = int(paise(intent_amount))
        except MoneyError as exc:
            return deny(
                "intent_amount_invalid",
                f"intent {session_id!r} does not hold integer paise: {exc}",
                **ok,
            )
        ok["expected_paise"] = expected

        # GATE 5 — exact integer amount.
        amounts = _collect(entities, lambda e: e.get("amount"))
        if not amounts:
            return deny("amount_missing", "no 'amount' field on any entity", **ok)
        typed: dict[str, int] = {}
        for key, value in amounts.items():
            try:
                typed[key] = int(paise(value))
            except MoneyError as exc:
                return deny(
                    "amount_not_integer",
                    f"{key}.entity.amount is not integer paise: {exc}",
                    **ok,
                )
        if len(set(typed.values())) > 1:
            # e.g. a partially-paid link whose payment is smaller than the link.
            return deny(
                "amount_conflict",
                f"entities disagree on amount: {typed}",
                **ok,
            )
        amount = next(iter(typed.values()))
        ok["amount_paise"] = amount

        if amount != expected:
            return deny(
                "amount_mismatch",
                f"webhook amount {amount} != intent.amount_paise {expected} "
                f"(off by {amount - expected} paise)",
                **ok,
            )

        # All four hold. This is the only construction of green in the system.
        for k in keys:
            self._seen.add(k)
        return GreenVerdict(
            green=True,
            reason="green",
            severity=GREEN,
            detail=(
                f"signature verified over {len(raw_body)} raw bytes; {event}; "
                f"session {session_id}; amount {amount} == intent {expected}"
            ),
            body_sha256=body_sha,
            mirror_stale=mirror_stale,
            **ok,
        )

    # -- audit ----------------------------------------------------------

    def _audit(self, v: GreenVerdict) -> None:
        if self._ledger is None or self._clock is None:
            return
        # Note what is NOT here: the secret, the signature, the raw body.
        self._ledger.append(
            ts=self._clock.now_iso(),
            module="webhook",
            green=v.green,
            reason=v.reason,
            severity=v.severity,
            signature_valid=v.signature_valid,
            event=v.event,
            event_id=v.event_id,
            session_id=v.session_id,
            amount_paise=v.amount_paise,
            expected_paise=v.expected_paise,
            body_sha256=v.body_sha256,
            mirror_stale=v.mirror_stale,
            downgraded_from_red=v.downgraded_from_red,
        )


# ---------------------------------------------------------------- helpers


def _reject_constant(name: str) -> Any:
    raise ValueError(f"JSON constant {name} is not acceptable in a money payload")


def _parse_body(raw_body: bytes) -> Optional[dict]:
    """Parse AFTER verification. Returns None for anything that is not an object.

    `parse_float=str` means no float object is ever constructed from the wire,
    so an amount of 21437.0 arrives as the string "21437.0" and is rejected by
    `money.paise` rather than silently truncated (INVARIANT 1).
    """
    try:
        obj = json.loads(
            raw_body.decode("utf-8"),
            parse_float=str,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _entities(payload: Any) -> dict[str, dict]:
    """Extract {'payment': {...}, 'payment_link': {...}} from the envelope."""
    if not isinstance(payload, dict):
        return {}
    out: dict[str, dict] = {}
    for key in _ENTITY_STATUS:
        holder = payload.get(key)
        if isinstance(holder, dict):
            entity = holder.get("entity")
            if isinstance(entity, dict):
                out[key] = entity
    return out


def _note(entity: dict, field: str) -> Any:
    notes = entity.get("notes")
    if isinstance(notes, dict):
        value = notes.get(field)
        if isinstance(value, str) and value:
            return value
    return None


def _collect(entities: dict[str, dict], get: Callable[[dict], Any]) -> dict[str, Any]:
    """Every non-None value the given accessor finds, keyed by entity name.

    Both entities are consulted on purpose: if a payment_link.paid carries both
    a link and a payment, they must agree. Trusting whichever one happens to be
    convenient is how a partial payment becomes a green counter.
    """
    found: dict[str, Any] = {}
    for key, entity in entities.items():
        value = get(entity)
        if value is not None:
            found[key] = value
    return found
