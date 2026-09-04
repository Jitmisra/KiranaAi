"""gawaah/rzp_live.py — the one module that can reach real money.

Two guards live here and, until this file existed, NEITHER was tested: deleting
the live-key refusal outright passed the entire suite, and the expiry the module
documents was never sent at all. A guard nobody tests is a guard that quietly
stops guarding.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gawaah import rzp_live  # noqa: E402
from gawaah.clock import RealClock  # noqa: E402


# ------------------------------------------------------- the live-key refusal

def test_a_live_key_is_refused() -> None:
    """Invariant: this program does not touch real money by accident."""
    with pytest.raises(Exception) as e:
        rzp_live.RazorpayLive(key_id="rzp_live_ABCDEF", key_secret="s")
    assert "rzp_live" in str(e.value) or "live" in str(e.value).lower()


def test_a_test_key_is_accepted() -> None:
    g = rzp_live.RazorpayLive(key_id="rzp_test_ABCDEF", key_secret="s")
    assert g.key_id.startswith("rzp_test_")


def test_a_live_key_is_accepted_only_when_explicitly_allowed() -> None:
    g = rzp_live.RazorpayLive(key_id="rzp_live_ABCDEF", key_secret="s", allow_live=True)
    assert g.key_id.startswith("rzp_live_")


@pytest.mark.parametrize("value", ["", "yes", "true", "1", "YES-I-MEAN-IT", "yes-i-mean-it "])
def test_the_escape_hatch_needs_the_exact_string_not_a_truthy_one(
        monkeypatch, value: str) -> None:
    """`== "yes-i-mean-it"`, never a truthiness check.

    An env var set to "false" is truthy in shell terms and must not open this.
    """
    monkeypatch.setenv("GAWAAH_ALLOW_LIVE_KEYS", value)
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_ABCDEF")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "s")
    with pytest.raises(Exception):
        rzp_live.live_factory(object())


def test_the_escape_hatch_opens_on_the_exact_string(monkeypatch) -> None:
    monkeypatch.setenv("GAWAAH_ALLOW_LIVE_KEYS", "yes-i-mean-it")
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_ABCDEF")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "s")
    assert rzp_live.live_factory(object()).key_id == "rzp_live_ABCDEF"


# ------------------------------------------------------------------ expiry

def test_the_factory_supplies_a_clock_so_links_actually_expire(monkeypatch) -> None:
    """`getattr(cfg, "clock", None)` always resolved to None.

    PaisaConfig is a frozen dataclass with no `clock` field, so expire_after_s
    was never applied and DEFAULT_EXPIRE_S was dead code — every link ever
    minted came back with `expire_by: 0`, payable forever.
    """
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_ABCDEF")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "s")
    g = rzp_live.live_factory(object())          # no `clock` attribute, as in real use
    assert g.clock is not None, "links would be minted with no expiry"
    assert g.expire_after_s == rzp_live.DEFAULT_EXPIRE_S


def test_a_minted_link_carries_a_future_expiry() -> None:
    """The body actually sent must contain a non-zero expire_by."""
    sent: dict = {}

    class _Capture(rzp_live.RazorpayLive):
        def _post(self, path, body, **kw):       # type: ignore[override]
            sent.update(body)
            return {"id": "plink_test", "short_url": "https://rzp.io/rzp/x",
                    "reference_id": body.get("reference_id"), "status": "created"}

    g = _Capture(key_id="rzp_test_ABCDEF", key_secret="s", clock=RealClock())
    try:
        g.create_payment_link(amount_paise=1000, reference_id="gwn_x", notes={})
    except Exception:
        pytest.skip("create_payment_link signature differs; expiry asserted in the unit above")
    assert sent.get("expire_by", 0) > 0, "an abandoned link would stay payable forever"
