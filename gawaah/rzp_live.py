"""The live Razorpay gateway adapter.

paisa deliberately ships NO hard-coded HTTP client -- `build_gateway` raises
unless a live factory is injected. That is invariant 5 holding: the gateway is a
Protocol, so the money service can be exercised end to end against
`rzp_sim.RazorpaySim` with no credentials, and going live is an injection rather
than a rewrite.

This module is that injection, and nothing more. The `Gateway` Protocol is
`create_payment_link(amount_paise, notes, **kwargs)`, and paisa really does pass
`reference_id`, `description` and `idempotent` through that `**kwargs`. This
adapter NAMES those fields rather than swallowing them, so a caller that passes
something we do not honour fails loudly at the boundary instead of having it
silently dropped on the way to Razorpay.

`reference_id` carries the kernel nonce, which is what makes the mint idempotent
at the gateway: Razorpay itself rejects a duplicate reference_id per merchant,
so a retried mint cannot create a second payable link for one basket.

VERIFIED AGAINST THE LIVE TEST API on 2026-08-29 (see FAILURES.md, gate G1):
  - Payment Links mint in test mode: HTTP 200
  - odd paise round-trip exactly: asked 21437, returned 21437
  - `notes.session_id` SURVIVES onto the entity, which condition 3 of the
    four-part green predicate depends on
  - `upi_link` comes back False in test mode -- it is Live-Mode and Android
    only. Nothing here depends on it; a standard link returns the same
    `short_url`, and the QR is rendered locally from that string.

WHAT THIS MODULE DOES NOT DO, deliberately:
  - it never constructs a UPI payload (invariant 6, disqualifying). `short_url`
    is an opaque token minted by Razorpay; we render a QR of that string and
    nothing else.
  - it never logs a secret. The key id is masked in every error path.
  - it never touches a live key: `RazorpayLive` refuses any key id that does not
    start `rzp_test_` unless `allow_live=True` is passed explicitly, so the
    default configuration cannot move real money.
"""
from __future__ import annotations

import base64
import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

API_BASE = "https://api.razorpay.com/v1"
DEFAULT_TIMEOUT_S = 25
DEFAULT_EXPIRE_S = 30 * 60  # a counter session that has not settled in 30 min is dead


class RazorpayLiveError(RuntimeError):
    """A gateway call failed. Never carries a secret."""


def _mask(key_id: str) -> str:
    if len(key_id) <= 14:
        return key_id[:9] + "…"
    return f"{key_id[:13]}…{key_id[-4:]}"


@dataclass
class RazorpayLive:
    """Live Razorpay gateway. Satisfies `paisa.Gateway`.

    Args:
        key_id:      rzp_test_... (a live key is refused unless allow_live)
        key_secret:  the paired secret. Never logged, never echoed.
        clock:       supplies `now_iso()`; taken so expiry is testable.
        allow_live:  must be set explicitly to accept a non-test key.
        timeout_s:   per-request timeout.
    """

    key_id: str
    key_secret: str
    clock: Any = None
    allow_live: bool = False
    timeout_s: int = DEFAULT_TIMEOUT_S
    expire_after_s: int = DEFAULT_EXPIRE_S
    description: str = "GAWAAH counter session"

    def __post_init__(self) -> None:
        if not self.key_id:
            raise RazorpayLiveError("no key id configured")
        if not self.key_secret:
            raise RazorpayLiveError(
                f"no key secret configured for {_mask(self.key_id)}"
            )
        if not self.key_id.startswith("rzp_test_") and not self.allow_live:
            raise RazorpayLiveError(
                f"{_mask(self.key_id)} is not a test key and allow_live is False. "
                "Refusing: this build must not be able to move real money."
            )
        self._auth = base64.b64encode(
            f"{self.key_id}:{self.key_secret}".encode()
        ).decode()

    # ------------------------------------------------------------------ http

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            f"{API_BASE}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
        )
        req.add_header("Authorization", f"Basic {self._auth}")
        req.add_header("Content-Type", "application/json")
        ctx = ssl.create_default_context()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s, context=ctx) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                payload = json.loads(e.read().decode())
                detail = (payload.get("error") or {}).get("description", "")
            except Exception:  # noqa: BLE001 - error bodies are not guaranteed JSON
                detail = ""
            raise RazorpayLiveError(
                f"{method} {path} -> HTTP {e.code} for {_mask(self.key_id)}"
                + (f": {detail}" if detail else "")
            ) from None
        except Exception as e:  # noqa: BLE001 - urllib raises a wide family
            raise RazorpayLiveError(
                f"{method} {path} failed for {_mask(self.key_id)}: {type(e).__name__}"
            ) from None

    # --------------------------------------------------------------- Gateway

    def create_payment_link(
        self,
        amount_paise: int,
        notes: dict,
        *,
        reference_id: str | None = None,
        description: str | None = None,
        expire_by: int | None = None,
        currency: str = "INR",
        upi_link: bool = False,
        idempotent: bool = False,
        accept_partial: bool = False,
        first_min_partial_amount: int | None = None,
        reminder_enable: bool = False,
        notify: Mapping[str, Any] | None = None,
        customer: Mapping[str, Any] | None = None,
    ) -> dict:
        """Mint a real Payment Link. Returns the Razorpay entity verbatim.

        `amount_paise` must be an int: a float here would be a money bug, and
        the whole point of the paise discipline is that it cannot be one.

        `notes` carries `session_id`, which the green predicate matches on.
        G1 verified it survives round-trip; we re-assert that here rather than
        trusting it, because if Razorpay ever stopped propagating notes the
        failure would otherwise be a silent never-green.

        KHATA. `accept_partial` was hard-coded False here, which was right for
        a counter bill (the green predicate demands the exact ask) and wrong
        for a collection link, which is minted for a whole outstanding balance
        the customer pays down in pieces. These are the real API's own field
        names, passed through as named kwargs so a misspelling fails loudly
        here rather than being swallowed and silently minting a link nobody
        can pay in instalments. `reminder_enable` + `notify.sms` + `customer.
        contact` is the whole of "Razorpay sends the reminders": this process
        sends no message and stores no contact — the entity is scrubbed at the
        boundary by paisa.
        """
        if isinstance(amount_paise, bool) or not isinstance(amount_paise, int):
            raise RazorpayLiveError(
                f"amount must be integer paise, got {type(amount_paise).__name__}"
            )
        if amount_paise < 100:
            raise RazorpayLiveError(
                f"Razorpay rejects amounts under 100 paise; got {amount_paise}"
            )

        body = {
            "amount": amount_paise,
            "currency": currency,
            "accept_partial": bool(accept_partial),
            "description": description or self.description,
            "notes": dict(notes or {}),
        }
        if first_min_partial_amount is not None:
            if not accept_partial:
                raise RazorpayLiveError(
                    "first_min_partial_amount needs accept_partial=True")
            if isinstance(first_min_partial_amount, bool) or not isinstance(
                    first_min_partial_amount, int):
                raise RazorpayLiveError("first_min_partial_amount must be integer paise")
            if not (100 <= first_min_partial_amount <= amount_paise):
                raise RazorpayLiveError(
                    f"first_min_partial_amount must be between 100 and "
                    f"{amount_paise} paise, got {first_min_partial_amount}")
            body["first_min_partial_amount"] = int(first_min_partial_amount)
        if reminder_enable:
            body["reminder_enable"] = True
        if notify:
            body["notify"] = {str(k): bool(v) for k, v in dict(notify).items()
                              if k in ("sms", "email")}
        if customer:
            body["customer"] = {str(k): str(v) for k, v in dict(customer).items()
                                if k in ("name", "contact", "email") and v}
        if reference_id:
            # reference_id is unique per merchant account, so Razorpay itself
            # rejects a second link carrying one we have already used. That is
            # the idempotency mechanism: paisa passes the kernel nonce here, so
            # a retried mint cannot create a second payable link for one basket.
            body["reference_id"] = reference_id
        if expire_by:
            body["expire_by"] = int(expire_by)
        # upi_link is Live-Mode and Android only; verified False in test mode
        # (gate G1). Sending it in test mode is accepted but inert, so we only
        # send it when explicitly asked for.
        if upi_link:
            body["upi_link"] = True
        # AN EXPLICIT expire_by WINS. This used to be unconditional and ran
        # AFTER the caller's value was written, so every explicit expiry was
        # silently overwritten with "30 minutes from now" — harmless for a
        # counter bill, fatal for a collection link that has to stay payable
        # for the week Razorpay spends reminding the customer about it.
        if not expire_by and self.clock is not None and self.expire_after_s:
            # expire_by is a unix timestamp; Razorpay enforces a 15-minute floor.
            import datetime as _dt

            now = _dt.datetime.fromisoformat(self.clock.now_iso())
            body["expire_by"] = int(now.timestamp()) + self.expire_after_s

        try:
            link = self._call("POST", "/payment_links", body)
        except RazorpayLiveError as exc:
            # Idempotent replay: a duplicate reference_id means THIS basket was
            # already minted. Fetch the existing link rather than minting a
            # second payable target for one sale -- that is the exactly-once
            # guarantee reaching all the way to the gateway.
            if idempotent and reference_id and "already exists" in str(exc).lower():
                found = self._call(
                    "GET", f"/payment_links?reference_id={reference_id}"
                )
                items = found.get("payment_links") or found.get("items") or []
                if items:
                    return items[0]
            raise

        got = link.get("amount")
        if got != amount_paise:
            raise RazorpayLiveError(
                f"amount did not round-trip: asked {amount_paise}, got {got}"
            )
        # session_id for a bill, collection_id for a khata collection: each is
        # the key its predicate matches on, and a link that lost it can never
        # settle anything.
        for key in ("session_id", "collection_id"):
            want = (notes or {}).get(key)
            if want is not None:
                back = (link.get("notes") or {}).get(key)
                if back != want:
                    raise RazorpayLiveError(
                        f"notes.{key} did not survive the round-trip. The "
                        "predicate matches on it, so minting would produce a "
                        "link that can never settle. Refusing to proceed."
                    )
        return link

    # ------------------------------------------------------- read-only extras

    def fetch_payment_link(self, link_id: str) -> dict:
        return self._call("GET", f"/payment_links/{link_id}")

    def fetch_payments(self, count: int = 100) -> dict:
        return self._call("GET", f"/payments?count={int(count)}")

    def fetch_payment_links(self, *, reference_id: str | None = None) -> dict:
        """GET /v1/payment_links?reference_id= — the read the kernel's
        reconcile path asks for: what happened to the link minted under this
        nonce? A read and nothing else; the answer is returned verbatim."""
        query = f"?reference_id={_query_token(reference_id)}" if reference_id else ""
        found = self._call("GET", f"/payment_links{query}")
        # The live collection is keyed `payment_links`; the simulator's, like
        # every other Razorpay collection, `items`. Both are honoured so the
        # caller reads one shape.
        items = found.get("payment_links") or found.get("items") or []
        return {"entity": "collection", "count": len(items), "items": list(items)}

    # ------------------------------------------------ MILAN: settlements
    #
    # Three reads of the gateway's own settlement record. None of them takes
    # an amount, none of them can move money, and none of them is on any
    # write path: `milan` asks paisa, paisa asks these, and the answer is the
    # gateway's rows verbatim after `strip_pii`.
    #
    # Paths are as Razorpay's Settlements API documents them; they have NOT
    # yet been exercised against the live test API (no settlement has been
    # produced on the test account in this build), so a shape difference on
    # first contact is a named RazorpayLiveError, never a guessed row.

    def fetch_settlements(self, *, count: int = 100, skip: int = 0) -> dict:
        """GET /v1/settlements — every batch the gateway has paid out."""
        return self._call("GET", f"/settlements?count={int(count)}&skip={int(skip)}")

    def fetch_settlement(self, settlement_id: str) -> dict:
        """GET /v1/settlements/{id} — one batch."""
        return self._call("GET", f"/settlements/{_query_token(settlement_id)}")

    def settlements_recon(self, *, year: int, month: int, day: int,
                          count: int = 1000, skip: int = 0) -> dict:
        """GET /v1/settlements/recon/combined?year&month&day — the rows.

        The gateway files a row per payment, refund and adjustment that
        settled on that IST day, each carrying `entity_id`, `type`, `amount`,
        `fee`, `tax`, `credit`, `debit`, `settlement_id`, `settled_at` and the
        payment's `notes` — which is how a row finds its way back to a nonce.
        """
        y, m, d = int(year), int(month), int(day)
        if not (2000 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31):
            raise RazorpayLiveError(f"not a calendar day: {y:04d}-{m:02d}-{d:02d}")
        return self._call(
            "GET",
            f"/settlements/recon/combined?year={y}&month={m:02d}&day={d:02d}"
            f"&count={int(count)}&skip={int(skip)}",
        )

    # ------------------------------------------------- WAAPSI: refunds
    #
    # ONE write, two reads. `refund` is the only method in this module besides
    # `create_payment_link` that can cause money to move, and it moves it the
    # other way: from the merchant back to the customer, against a payment
    # the gateway itself captured. It is reached only from paisa's refund
    # route, after the kernel has committed a CALLING row for the line, so a
    # crash between this call and its answer leaves an INDETERMINATE refund
    # that is never retried blind.
    #
    # Paths and fields are as Razorpay's Refunds API documents them. They
    # have NOT yet been exercised against the live test API in this build; a
    # shape difference on first contact is a named RazorpayLiveError, never a
    # guessed entity.

    def refund(
        self, payment_id: str, amount_paise: int, *, speed: str = "optimum",
        receipt: str | None = None, notes: Mapping[str, Any] | None = None,
    ) -> dict:
        """POST /v1/payments/{id}/refund. Returns the refund entity verbatim.

        `amount_paise` is an int or it is refused: a float here would be a
        money bug. `speed: optimum` asks for an instant refund where the rails
        allow it and falls back to normal; the entity says which it got.
        `receipt` is this counter's reference; `notes` carries the kernel's
        refund key so the signed callback can name its row.
        """
        if isinstance(amount_paise, bool) or not isinstance(amount_paise, int):
            raise RazorpayLiveError(
                f"refund amount must be integer paise, got {type(amount_paise).__name__}")
        if amount_paise < 100:
            raise RazorpayLiveError(
                f"Razorpay rejects refunds under 100 paise; got {amount_paise}")
        if speed not in ("normal", "optimum"):
            raise RazorpayLiveError(f"refund speed must be normal or optimum, got {speed!r}")
        body: dict[str, Any] = {"amount": int(amount_paise), "speed": speed}
        if receipt:
            body["receipt"] = str(receipt)[:40]
        if notes:
            body["notes"] = {str(k): str(v) for k, v in dict(notes).items()}
        entity = self._call("POST", f"/payments/{_query_token(payment_id)}/refund", body)
        got = entity.get("amount")
        if got != amount_paise:
            raise RazorpayLiveError(
                f"refund amount did not round-trip: asked {amount_paise}, got {got}")
        if entity.get("payment_id") not in (None, payment_id):
            raise RazorpayLiveError(
                f"refund came back against {entity.get('payment_id')!r}, "
                f"not {payment_id!r}")
        for key in ("refund_key",):
            want = (notes or {}).get(key)
            if want is not None and (entity.get("notes") or {}).get(key) != want:
                raise RazorpayLiveError(
                    f"notes.{key} did not survive the round-trip; the signed "
                    "refund.processed could not name its row. Refusing to "
                    "proceed on the answer.")
        return entity

    def fetch_refund(self, refund_id: str) -> dict:
        """GET /v1/refunds/{id} — one refund, verbatim."""
        return self._call("GET", f"/refunds/{_query_token(refund_id)}")

    def fetch_refunds(self, *, payment_id: str) -> dict:
        """GET /v1/payments/{id}/refunds — every refund on one payment."""
        found = self._call("GET", f"/payments/{_query_token(payment_id)}/refunds")
        items = found.get("items") or []
        return {"entity": "collection", "count": len(items), "items": list(items)}


def _query_token(value: str | None) -> str:
    """An id or reference on a URL: the gateway's own charset and nothing
    else. Anything wider is refused rather than escaped, because an id that
    needs escaping is not an id this program ever minted."""
    import re

    token = str(value or "")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", token):
        raise RazorpayLiveError(f"refusing to put {token!r} on a gateway URL")
    return token


def live_factory(cfg) -> RazorpayLive:
    """The injection point `paisa.build_gateway(live_factory=...)` expects."""
    import os

    # THE CLOCK IS NOT OPTIONAL HERE.
    #
    # `getattr(cfg, "clock", None)` looked defensive and was fatal: PaisaConfig
    # is a frozen dataclass with no `clock` field, so this always resolved to
    # None, `expire_after_s` was never applied, and DEFAULT_EXPIRE_S was dead
    # code. Every payment link this build has ever minted came back with
    # `expire_by: 0` — an abandoned link stays payable forever, and CANCEL on
    # the till abandons one every time it is pressed.
    #
    # Fall back to the real clock rather than to None, so the expiry the module
    # documents is the expiry it actually sends.
    from .clock import RealClock

    return RazorpayLive(
        key_id=os.environ.get("RAZORPAY_KEY_ID", ""),
        key_secret=os.environ.get("RAZORPAY_KEY_SECRET", ""),
        clock=getattr(cfg, "clock", None) or RealClock(),
        allow_live=os.environ.get("GAWAAH_ALLOW_LIVE_KEYS") == "yes-i-mean-it",
    )
