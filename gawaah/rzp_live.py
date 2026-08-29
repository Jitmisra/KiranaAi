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
from typing import Any

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
    ) -> dict:
        """Mint a real Payment Link. Returns the Razorpay entity verbatim.

        `amount_paise` must be an int: a float here would be a money bug, and
        the whole point of the paise discipline is that it cannot be one.

        `notes` carries `session_id`, which the green predicate matches on.
        G1 verified it survives round-trip; we re-assert that here rather than
        trusting it, because if Razorpay ever stopped propagating notes the
        failure would otherwise be a silent never-green.
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
            "accept_partial": False,
            "description": description or self.description,
            "notes": dict(notes or {}),
        }
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
        if self.clock is not None and self.expire_after_s:
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
        want_sid = (notes or {}).get("session_id")
        if want_sid is not None:
            back = (link.get("notes") or {}).get("session_id")
            if back != want_sid:
                raise RazorpayLiveError(
                    "notes.session_id did not survive the round-trip. The green "
                    "predicate matches on it, so minting would produce a link "
                    "that can never turn the counter green. Refusing to proceed."
                )
        return link

    # ------------------------------------------------------- read-only extras

    def fetch_payment_link(self, link_id: str) -> dict:
        return self._call("GET", f"/payment_links/{link_id}")

    def fetch_payments(self, count: int = 100) -> dict:
        return self._call("GET", f"/payments?count={int(count)}")


def live_factory(cfg) -> RazorpayLive:
    """The injection point `paisa.build_gateway(live_factory=...)` expects."""
    import os

    return RazorpayLive(
        key_id=os.environ.get("RAZORPAY_KEY_ID", ""),
        key_secret=os.environ.get("RAZORPAY_KEY_SECRET", ""),
        clock=getattr(cfg, "clock", None),
        allow_live=os.environ.get("GAWAAH_ALLOW_LIVE_KEYS") == "yes-i-mean-it",
    )
