"""S4c acceptance: green happens only on a signature-verified webhook.

The load-bearing tests are the ones that prove BYTES are verified, not objects:
`test_reserialised_identical_body_fails` and `test_one_flipped_byte_fails`.
Everything else pins a distinct reason code so that a failure at 11pm on a shop
counter is diagnosable without a debugger.
"""
from __future__ import annotations

import ast
import hashlib
import hmac
import importlib.util
import inspect
import json
import random
import time
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from gawaah.clock import VirtualClock
from gawaah.ledger import Ledger, verify
from gawaah.webhook import (
    AMBER,
    GREEN,
    GREEN_EVENTS,
    RED,
    REASON_CODES,
    GreenPredicate,
    GreenVerdict,
    Intent,
    WebhookError,
    verify_signature,
)

SECRET = "whsec_gawaah_test_only_not_a_real_secret"
SESSION = "s_0042"
AMOUNT = 21437  # ₹214.37 — the last two paise are the CHILLAR nonce


# ---------------------------------------------------------------- fixtures
#
# `_sign` exists ONLY in the test file. Production code verifies signatures and
# never produces them; a signing helper sitting next to the verifier is how a
# forgery primitive gets written by accident.


def _sign(raw: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def payment_link_only(
    *,
    session_id: str = SESSION,
    amount: int = AMOUNT,
    amount_paid: int | None = None,
    **overrides,
) -> dict:
    """A `payment_link.paid` envelope carrying ONLY the link entity.

    This shape is real: `contains` is a list, and Razorpay is free to send
    `["payment_link"]` with no nested payment entity. It is also the shape in
    which the ask/settlement distinction has nothing to cross-check it —
    `amount` is what the link ASKED FOR and keeps reporting for ever;
    `amount_paid` is what actually arrived.
    """
    link = {
        "id": "plink_Fo48rl281ENAg9",
        "entity": "payment_link",
        "amount": amount,
        "amount_paid": amount if amount_paid is None else amount_paid,
        "currency": "INR",
        "status": "paid",
        "accept_partial": True,
        "first_min_partial_amount": 100,
        "notes": {"session_id": session_id},
        "short_url": "https://rzp.io/i/XQiMe4w",
    }
    link.update(overrides)
    return {
        "entity": "event",
        "account_id": "acc_H3kYHQ635sBwXG",
        "event": "payment_link.paid",
        "contains": ["payment_link"],
        "payload": {"payment_link": {"entity": link}},
        "created_at": 1602522351,
    }


def payment_captured(
    *, session_id: str = SESSION, amount: int = AMOUNT, **overrides
) -> dict:
    """A `payment.captured` envelope shaped like Razorpay's real one.

    Shape taken from reference/razorpay-python/tests/mocks/
    fake_payment_authorized_webhook.json, with notes.session_id added — which
    is the field the whole green rule hangs on (BUILD_PROMPT gate B5).
    """
    entity = {
        "id": "pay_6koWN7bvxujzxM",
        "entity": "payment",
        "amount": amount,
        "currency": "INR",
        "status": "captured",
        "order_id": "order_100000000order",
        "method": "upi",
        "amount_refunded": 0,
        "captured": True,
        "vpa": "gaurav.kumar@okhdfcbank",
        "notes": {"session_id": session_id},
        "created_at": 1479978483,
    }
    entity.update(overrides)
    return {
        "entity": "event",
        "account_id": "acc_H3kYHQ635sBwXG",
        "event": "payment.captured",
        "contains": ["payment"],
        "payload": {"payment": {"entity": entity}},
        "created_at": 1400826760,
    }


def payment_link_paid(
    *,
    session_id: str = SESSION,
    amount: int = AMOUNT,
    payment_amount: int | None = None,
) -> dict:
    """A `payment_link.paid` envelope: link entity AND payment entity."""
    link = {
        "id": "plink_Fo48rl281ENAg9",
        "entity": "payment_link",
        "amount": amount,
        "amount_paid": amount,
        "currency": "INR",
        "status": "paid",
        "accept_partial": False,
        "notes": {"session_id": session_id},
        "short_url": "https://rzp.io/i/XQiMe4w",
    }
    pay = {
        "id": "pay_Fo49sHbQ78PCMI",
        "entity": "payment",
        "amount": amount if payment_amount is None else payment_amount,
        "currency": "INR",
        "status": "captured",
        "method": "upi",
        "notes": {"session_id": session_id},
    }
    return {
        "entity": "event",
        "account_id": "acc_H3kYHQ635sBwXG",
        "event": "payment_link.paid",
        "contains": ["payment_link", "payment"],
        "payload": {"payment_link": {"entity": link}, "payment": {"entity": pay}},
        "created_at": 1602522351,
    }


def wire(obj: dict) -> bytes:
    """Serialise the way a server would: this is the byte sequence signed."""
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


def lookup_of(*intents: Intent):
    table = {i.session_id: i for i in intents}
    return lambda sid: table.get(sid)


@pytest.fixture
def predicate():
    return GreenPredicate(lookup_of(Intent(SESSION, AMOUNT)))


# ---------------------------------------------------------------- signature


def test_valid_signature_verifies():
    raw = wire(payment_captured())
    assert verify_signature(raw, _sign(raw), SECRET) is True


def test_one_flipped_byte_fails():
    """The whole point: the HMAC is over bytes, and one bit is enough."""
    raw = wire(payment_captured())
    sig = _sign(raw)
    flipped = 0
    for i in range(len(raw)):
        tampered = bytearray(raw)
        tampered[i] ^= 0x01
        if bytes(tampered) == raw:
            continue
        assert verify_signature(bytes(tampered), sig, SECRET) is False
        flipped += 1
    assert flipped == len(raw)
    print(f"\n[measured] one-byte flips rejected: {flipped}/{len(raw)} body bytes")


def test_wrong_secret_fails():
    raw = wire(payment_captured())
    assert verify_signature(raw, _sign(raw, "whsec_someone_elses"), SECRET) is False


@pytest.mark.parametrize(
    "sig", ["", None, 0, b"", "not-hex", "a" * 63, "a" * 65, "é" * 64, []]
)
def test_junk_signatures_return_false_and_never_raise(sig):
    raw = wire(payment_captured())
    assert verify_signature(raw, sig, SECRET) is False


def test_empty_secret_is_not_an_authenticator():
    """HMAC with a known-empty key is forgeable by anyone. Refuse it."""
    raw = wire(payment_captured())
    assert verify_signature(raw, _sign(raw, ""), "") is False


def test_str_body_is_rejected_at_the_boundary():
    """A str has already been through a decode; it is not the signed object."""
    raw = wire(payment_captured())
    with pytest.raises(WebhookError):
        verify_signature(raw.decode(), _sign(raw), SECRET)


def test_bytearray_and_memoryview_are_accepted():
    raw = wire(payment_captured())
    sig = _sign(raw)
    assert verify_signature(bytearray(raw), sig, SECRET) is True
    assert verify_signature(memoryview(raw), sig, SECRET) is True


def test_source_uses_compare_digest_and_not_equality():
    """Assert the constant-time comparison at the AST level, not by eyeball."""
    src = inspect.getsource(verify_signature)  # top-level def, already at col 0
    assert "compare_digest" in src
    tree = ast.parse(src)
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "compare_digest"
    ]
    assert len(calls) == 1, "expected exactly one compare_digest call"
    # and no `expected == provided` shortcut hiding underneath it
    eqs = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Compare) and any(isinstance(o, ast.Eq) for o in n.ops)
    ]
    assert eqs == [], "verify_signature must not use == on digest material"


# ---------------------------------------------------------------- the four gates


def test_valid_signature_greens(predicate):
    raw = wire(payment_captured())
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green is True
    assert v.reason == "green"
    assert v.severity == GREEN
    assert v.session_id == SESSION
    assert v.amount_paise == AMOUNT == v.expected_paise
    assert v.signature_valid is True


def test_payment_link_paid_also_greens(predicate):
    raw = wire(payment_link_paid())
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green and v.event == "payment_link.paid"


def test_bad_signature_never_greens(predicate):
    raw = wire(payment_captured())
    v = predicate.evaluate(raw, _sign(raw, "wrong"), SECRET)
    assert v.green is False and v.reason == "bad_signature"
    assert v.signature_valid is False


def test_flipped_body_byte_does_not_green(predicate):
    raw = bytearray(wire(payment_captured()))
    sig = _sign(bytes(raw))
    raw[10] ^= 0x20
    v = predicate.evaluate(bytes(raw), sig, SECRET)
    assert v.reason == "bad_signature"


def test_reserialised_identical_body_fails(predicate):
    """Semantically identical, cryptographically different. This is the test
    that proves we verify bytes rather than objects."""
    original = wire(payment_captured())
    sig = _sign(original)
    reserialised = json.dumps(json.loads(original), indent=2, sort_keys=True).encode()

    assert json.loads(reserialised) == json.loads(original)  # same object
    assert reserialised != original  # different bytes

    assert predicate.evaluate(original, sig, SECRET).green is True
    v = predicate.evaluate(reserialised, sig, SECRET)
    assert v.green is False and v.reason == "bad_signature"


def test_signature_is_checked_before_any_parse(predicate):
    """Invalid JSON + invalid signature must report the SIGNATURE, proving the
    parser was never reached."""
    junk = b"{not json at all"
    v = predicate.evaluate(junk, "deadbeef" * 8, SECRET)
    assert v.reason == "bad_signature"
    # with a valid signature over the same junk we get the parse error instead,
    # which is what makes the assertion above meaningful
    v2 = predicate.evaluate(junk, _sign(junk), SECRET)
    assert v2.reason == "malformed_body" and v2.signature_valid is True


def test_source_order_verify_then_parse():
    src = inspect.getsource(GreenPredicate._evaluate)
    assert src.index("verify_signature(") < src.index("_parse_body(")


@pytest.mark.parametrize(
    "event", ["payment.failed", "payment.authorized", "payment_link.partially_paid",
              "order.paid", "refund.processed", "payment_link.expired"]
)
def test_wrong_event_type_has_its_own_code(predicate, event):
    body = payment_captured()
    body["event"] = event
    raw = wire(body)
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green is False
    assert v.reason == "event_not_green"
    assert v.event == event
    assert v.signature_valid is True  # the signature was fine; the event was not


def test_missing_event_field_is_distinct(predicate):
    body = payment_captured()
    del body["event"]
    raw = wire(body)
    assert predicate.evaluate(raw, _sign(raw), SECRET).reason == "missing_event"


def test_unknown_session_fails(predicate):
    raw = wire(payment_captured(session_id="s_not_ours"))
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green is False
    assert v.reason == "unknown_session"
    assert v.severity == AMBER  # never RED: absence of evidence is not fraud


def test_missing_session_id_is_distinct(predicate):
    body = payment_captured()
    body["payload"]["payment"]["entity"]["notes"] = {"merchant_order_id": "x"}
    raw = wire(body)
    assert predicate.evaluate(raw, _sign(raw), SECRET).reason == "missing_session_id"


def test_intent_not_open_is_distinct():
    p = GreenPredicate(lookup_of(Intent(SESSION, AMOUNT, state="SETTLED")))
    raw = wire(payment_captured())
    v = p.evaluate(raw, _sign(raw), SECRET)
    assert v.reason == "intent_not_open" and not v.green


def test_a_lookup_that_explodes_never_greens():
    def boom(_sid):
        raise RuntimeError("mirror unreachable")

    p = GreenPredicate(boom)
    raw = wire(payment_captured())
    v = p.evaluate(raw, _sign(raw), SECRET)
    assert v.reason == "unknown_session" and v.severity == AMBER


@pytest.mark.parametrize("delta", [1, -1, 100, -100, 2143700])
def test_amount_off_by_one_paisa_fails(predicate, delta):
    raw = wire(payment_captured(amount=AMOUNT + delta))
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green is False
    assert v.reason == "amount_mismatch"
    assert v.severity == RED  # a contradiction, not an absence — hold for a human
    assert v.amount_paise == AMOUNT + delta and v.expected_paise == AMOUNT
    assert str(delta) in v.detail


def test_float_amount_is_rejected_not_truncated(predicate):
    """21437.0 must not become 21437. Money is integer paise (INVARIANT 1)."""
    raw = b'{"entity":"event","event":"payment.captured","payload":{"payment":{"entity":{"amount":21437.0,"currency":"INR","status":"captured","notes":{"session_id":"s_0042"}}}}}'
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green is False and v.reason == "amount_not_integer"


@pytest.mark.parametrize("bad", ['"21437"', "true", "null", "[21437]"])
def test_non_integer_amounts_are_rejected(predicate, bad):
    raw = (
        '{"entity":"event","event":"payment.captured","payload":{"payment":'
        '{"entity":{"amount":' + bad + ',"currency":"INR","status":"captured",'
        '"notes":{"session_id":"s_0042"}}}}}'
    ).encode()
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green is False
    assert v.reason in {"amount_not_integer", "amount_missing"}


def test_nan_amount_cannot_enter_the_money_path(predicate):
    """json.loads accepts NaN by default. A NaN amount must not parse at all."""
    raw = b'{"entity":"event","event":"payment.captured","payload":{"payment":{"entity":{"amount":NaN,"notes":{"session_id":"s_0042"}}}}}'
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.reason == "malformed_body"


def test_partial_payment_cannot_green_a_full_link(predicate):
    """Link says 21437, payment says 500. Trusting either one alone greens a
    counter that was not paid."""
    raw = wire(payment_link_paid(payment_amount=500))
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green is False and v.reason == "amount_conflict"


# ------------------------------------------------- DEFECT 1: ask vs settlement
#
# `test_partial_payment_cannot_green_a_full_link` above only holds because that
# envelope happens to carry BOTH entities, so the two disagree and the conflict
# gate catches it. Delete the payment entity — a shape Razorpay is free to send,
# since `contains` is a list — and there is nothing left to disagree with. The
# gate was comparing `payment_link.entity.amount`, which is the ASK and equals
# the intent no matter how little money arrived.

def test_a_part_paid_link_alone_cannot_green_a_full_intent(predicate):
    """DEFECT 1. ₹5.00 arrives against a ₹214.37 intent and the counter greens.

    The link entity reports `amount: 21437` (what we asked for, unchanged for
    ever) and `amount_paid: 500` (what actually settled). Comparing `amount`
    against `intent.amount_paise` proves only that we asked for the right
    number — it says nothing about money moving.
    """
    raw = wire(payment_link_only(amount=AMOUNT, amount_paid=500))
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green is False, "₹5.00 greened a ₹214.37 intent"
    assert v.reason == "partial_payment"
    assert "500" in v.detail and str(AMOUNT) in v.detail


def test_a_status_less_part_paid_link_alone_cannot_green(predicate):
    """The same attack with `status` omitted, which used to fail the gate OPEN."""
    body = payment_link_only(amount=AMOUNT, amount_paid=1)
    del body["payload"]["payment_link"]["entity"]["status"]
    raw = wire(body)
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green is False
    assert v.reason in {"entity_status_missing", "partial_payment"}


def test_a_link_that_never_reports_amount_paid_abstains(predicate):
    """Unknown settled amount -> AMBER, excluded, never green (INVARIANT 7)."""
    body = payment_link_only()
    del body["payload"]["payment_link"]["entity"]["amount_paid"]
    raw = wire(body)
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green is False
    assert v.reason == "amount_paid_missing"
    assert v.severity == AMBER  # absence of evidence is not fraud
    assert v.amount_paise is None


def test_a_link_paid_in_full_alone_still_greens(predicate):
    """The fix must not cost the honest case: one entity, fully settled."""
    raw = wire(payment_link_only(amount=AMOUNT, amount_paid=AMOUNT))
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green is True and v.amount_paise == AMOUNT


def test_a_link_paid_in_full_across_two_payments_still_greens(predicate):
    """accept_partial link, ₹5.00 then ₹209.37. `amount_paid` is the running
    total, so the final `payment_link.paid` settles in full and greens."""
    raw = wire(payment_link_only(amount=AMOUNT, amount_paid=AMOUNT))
    assert predicate.evaluate(raw, _sign(raw), SECRET).green is True


@pytest.mark.parametrize(
    "paid", [0, 1, 500, AMOUNT - 1, AMOUNT, AMOUNT + 1, AMOUNT * 2]
)
def test_only_a_link_settled_in_full_greens(paid):
    p = GreenPredicate(lookup_of(Intent(SESSION, AMOUNT)))
    raw = wire(payment_link_only(amount=AMOUNT, amount_paid=paid))
    v = p.evaluate(raw, _sign(raw), SECRET)
    assert v.green == (paid == AMOUNT)
    if not v.green:
        assert v.reason == "partial_payment"


def test_an_over_asking_link_cannot_green_on_a_coincidence(predicate):
    """The link asks 99999 and 21437 arrives. 21437 happens to equal the
    intent, but the ask does not — that is a contradiction, not a sale."""
    raw = wire(payment_link_only(amount=99999, amount_paid=AMOUNT))
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green is False and v.reason == "partial_payment"


def test_a_non_integer_amount_paid_is_rejected_not_coerced(predicate):
    raw = (
        '{"event":"payment_link.paid","payload":{"payment_link":{"entity":'
        '{"amount":21437,"amount_paid":214.37,"currency":"INR","status":"paid",'
        '"notes":{"session_id":"s_0042"}}}}}'
    ).encode()
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green is False and v.reason == "amount_not_integer"


def test_a_refunded_capture_is_not_a_settlement(predicate):
    """`amount` still says 21437; the money is back with the customer."""
    raw = wire(payment_captured(amount_refunded=AMOUNT))
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green is False and v.reason == "amount_mismatch"
    assert v.amount_paise == 0


def test_a_partly_refunded_capture_is_not_a_settlement(predicate):
    raw = wire(payment_captured(amount_refunded=37))
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green is False and v.reason == "amount_mismatch"
    assert v.amount_paise == AMOUNT - 37


def test_the_settled_field_is_not_the_ask_for_a_link():
    """Pin the mapping itself: nothing may quietly repoint a link at `amount`."""
    from gawaah.webhook import _SETTLED_FIELD

    assert _SETTLED_FIELD == {"payment": "amount", "payment_link": "amount_paid"}


def test_foreign_currency_cannot_green(predicate):
    """21437 USD-cents is not 21437 paise. An amount without a unit is not money."""
    raw = wire(payment_captured(currency="USD"))
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green is False and v.reason == "wrong_currency"


def test_entity_status_must_agree_with_the_event(predicate):
    raw = wire(payment_captured(status="failed"))
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green is False and v.reason == "entity_status_not_paid"


# ------------------------------------------------- the currency / status gates
#
# DECISION: both gates FAIL CLOSED. An absent field is not a passing field.
#
# The reasoning, pinned by the four tests below so it cannot drift:
#
#   * INVARIANT 7 says abstain rather than guess. `currency` absent means we do
#     not know the unit, and an amount without a unit is not money — 21437 is
#     ₹214.37 or $214.37 depending on a field we did not read. `status` absent
#     means the entity never asserted that money moved.
#   * Failing open makes an OMISSION strictly weaker than a WRONG VALUE:
#     `"currency":"USD"` is refused but no currency at all sails through. Any
#     gate with that shape is bypassed by deletion, which is the cheapest edit
#     there is.
#   * The cost of failing closed on genuine traffic is zero: every real
#     Razorpay payment/payment_link entity carries both fields, and so does
#     every body `rzp_sim` emits. If one ever does not, the verdict is AMBER —
#     Razorpay retries, and a shopkeeper sees "waiting", not a false green.
#   * The failure is safe in the right direction. Failing closed can only ever
#     withhold a green (recoverable: retry, or reconcile by hand). Failing open
#     hands out a green for money that may not exist (not recoverable: the
#     goods have left the shop).

def test_an_entity_with_no_currency_fails_closed(predicate):
    """Deleting the field must not be cheaper than getting it wrong."""
    body = payment_captured()
    del body["payload"]["payment"]["entity"]["currency"]
    raw = wire(body)
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green is False
    assert v.reason == "currency_missing"
    assert v.severity == AMBER  # unknown unit is an absence, not an accusation


def test_an_entity_with_no_status_fails_closed(predicate):
    body = payment_captured()
    del body["payload"]["payment"]["entity"]["status"]
    raw = wire(body)
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green is False
    assert v.reason == "entity_status_missing"
    assert v.severity == AMBER


@pytest.mark.parametrize("junk", [None, 1, True, ["INR"], {"code": "INR"}, ""])
def test_a_non_string_currency_fails_closed(predicate, junk):
    raw = wire(payment_captured(currency=junk))
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green is False
    assert v.reason in {"currency_missing", "wrong_currency"}


@pytest.mark.parametrize("junk", [None, 1, True, ["captured"], ""])
def test_a_non_string_status_fails_closed(predicate, junk):
    raw = wire(payment_captured(status=junk))
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green is False
    assert v.reason in {"entity_status_missing", "entity_status_not_paid"}


def test_deleting_a_gate_field_is_never_better_than_getting_it_wrong(predicate):
    """The general shape of the decision, asserted over every gated field:
    for each one, absence must be at least as fatal as a wrong value."""
    for field in ("currency", "status", "amount", "notes"):
        wrong = payment_captured(**{field: {"session_id": "s_nope"} if field == "notes" else "XXX"})
        absent = payment_captured()
        del absent["payload"]["payment"]["entity"][field]
        for body in (wrong, absent):
            raw = wire(body)
            v = predicate.evaluate(raw, _sign(raw), SECRET)
            assert v.green is False, f"{field}: {body} greened"


def test_no_entity_is_distinct(predicate):
    body = payment_captured()
    body["payload"] = {}
    raw = wire(body)
    assert predicate.evaluate(raw, _sign(raw), SECRET).reason == "no_entity"


def test_session_id_conflict_between_entities(predicate):
    body = payment_link_paid()
    body["payload"]["payment"]["entity"]["notes"]["session_id"] = "s_9999"
    raw = wire(body)
    assert predicate.evaluate(raw, _sign(raw), SECRET).reason == "session_id_conflict"


def test_missing_secret_is_its_own_code(predicate):
    raw = wire(payment_captured())
    v = predicate.evaluate(raw, _sign(raw, ""), "")
    assert v.reason == "secret_not_configured" and not v.green


def test_intent_holding_a_float_cannot_green():
    p = GreenPredicate(lambda sid: {"amount_paise": 214.37, "state": "OPEN"})
    raw = wire(payment_captured())
    v = p.evaluate(raw, _sign(raw), SECRET)
    assert v.reason == "intent_amount_invalid" and not v.green


def test_dict_shaped_intents_are_accepted():
    p = GreenPredicate(lambda sid: {"amount_paise": AMOUNT, "state": "OPEN"})
    raw = wire(payment_captured())
    assert p.evaluate(raw, _sign(raw), SECRET).green is True


# ---------------------------------------------------------------- replay


def test_duplicate_event_id_yields_replay(predicate):
    raw = wire(payment_captured())
    sig = _sign(raw)
    first = predicate.evaluate(raw, sig, SECRET)
    assert first.green is True

    second = predicate.evaluate(raw, sig, SECRET)
    assert second.green is False
    assert second.reason == "replay"
    assert second.signature_valid is True

    third = predicate.evaluate(raw, sig, SECRET)
    assert third.reason == "replay"


def test_replay_key_survives_a_changed_event_id_header(predicate):
    """X-Razorpay-Event-Id is NOT covered by the HMAC. If it were the dedupe
    key, replaying a captured body with a fresh header would double-green."""
    raw = wire(payment_captured())
    sig = _sign(raw)
    assert predicate.evaluate(raw, sig, SECRET, header_event_id="evt_1").green is True
    v = predicate.evaluate(raw, sig, SECRET, header_event_id="evt_totally_different")
    assert v.reason == "replay"


# ------------------------------------------- DEFECT 2: replay-store poisoning
#
# The previous version of this file asserted the opposite of the test below —
# "two different bodies sharing one header event id: the second is a dup" — and
# so pinned the defect in place. `X-Razorpay-Event-Id` is NOT covered by the
# HMAC. Any party that can touch the request in flight (a proxy, a sidecar, a
# TLS-terminating load balancer, an attacker on the path) can rewrite it while
# leaving the signed body byte-identical. Treating it as a replay key hands that
# party a write into the replay store, and a write into the replay store is a
# denial of green: the money lands and the counter never turns.

def test_the_untrusted_header_cannot_poison_the_replay_store():
    """DEFECT 2, end to end. Two genuine, correctly signed webhooks.

    Delivery 1 is a real ₹1.00 payment for some other session. On the way in,
    its `X-Razorpay-Event-Id` header is rewritten to the id of a webhook that
    has not happened yet — the header is outside the HMAC, so the body and its
    signature are untouched and still verify.

    Delivery 2 is the shopkeeper's real ₹214.37 sale, carrying that id inside
    its SIGNED envelope. It must green. If the poisoned header made it into the
    store, it is refused as a replay: the customer has paid, the goods are
    gone, and the counter never goes green.
    """
    p = GreenPredicate(lookup_of(Intent("s_attacker", 100), Intent(SESSION, AMOUNT)))

    attacker = payment_captured(session_id="s_attacker", amount=100)
    attacker["id"] = "evt_attackers_own_payment"
    raw_a = wire(attacker)
    first = p.evaluate(raw_a, _sign(raw_a), SECRET, header_event_id="evt_GUESSED")
    assert first.green is True  # a genuine payment; it is entitled to green

    genuine = payment_captured()
    genuine["id"] = "evt_GUESSED"
    raw_g = wire(genuine)
    v = p.evaluate(raw_g, _sign(raw_g), SECRET)
    assert v.green is True, (
        f"DENIAL OF GREEN: a genuine ₹214.37 webhook was refused as {v.reason!r} "
        "because an unauthenticated header pre-seeded the replay store"
    )
    assert v.event_id == "evt_GUESSED"


def test_a_rewriting_proxy_cannot_stop_every_later_green():
    """The same bug without an attacker: one broken intermediary that stamps a
    constant event id on every request bricks the counter after the first sale."""
    p = GreenPredicate(lookup_of(*[Intent(f"s_{i}", 10000 + i) for i in range(5)]))
    greens = 0
    for i in range(5):
        raw = wire(payment_captured(session_id=f"s_{i}", amount=10000 + i))
        if p.evaluate(raw, _sign(raw), SECRET, header_event_id="evt_same_every_time").green:
            greens += 1
    assert greens == 5, f"only {greens}/5 genuine sales greened"


def test_two_different_bodies_sharing_a_header_event_id_both_green(predicate):
    """The header is not a key. Different signed bodies are different events."""
    raw1 = wire(payment_captured())
    assert predicate.evaluate(raw1, _sign(raw1), SECRET, header_event_id="evt_9").green
    raw2 = wire(payment_link_paid())
    v = predicate.evaluate(raw2, _sign(raw2), SECRET, header_event_id="evt_9")
    assert v.green is True and v.reason == "green"


def test_the_replay_store_only_ever_holds_hmac_verified_keys():
    """Structural: whatever the header says, the store gains exactly one key and
    that key is derived from the SIGNED bytes."""
    store: set[str] = set()
    p = GreenPredicate(lookup_of(Intent(SESSION, AMOUNT)), seen=store)
    raw = wire(payment_captured())
    p.evaluate(raw, _sign(raw), SECRET, header_event_id="evt_attacker_chosen")
    assert store == {hashlib.sha256(raw).hexdigest()}

    # and when the SIGNED envelope names an id, that id is the key
    store2: set[str] = set()
    p2 = GreenPredicate(lookup_of(Intent(SESSION, AMOUNT)), seen=store2)
    body = payment_captured()
    body["id"] = "evt_inside_the_signature"
    raw2 = wire(body)
    p2.evaluate(raw2, _sign(raw2), SECRET, header_event_id="evt_attacker_chosen")
    assert store2 == {"evt_inside_the_signature"}


def test_the_header_is_recorded_but_never_trusted(predicate):
    """It is still useful forensics — a rewriting proxy shows up as a mismatch —
    so it is reported on the verdict under a name nobody can misread."""
    body = payment_captured()
    body["id"] = "evt_real"
    raw = wire(body)
    v = predicate.evaluate(raw, _sign(raw), SECRET, header_event_id="evt_rewritten")
    assert v.green is True
    assert v.event_id == "evt_real"
    assert v.untrusted_header_event_id == "evt_rewritten"


def test_no_header_value_can_change_any_verdict(predicate):
    """Sweep the header over hostile values; the verdict is byte-for-byte the
    same each time apart from the field that merely records what arrived."""
    raw = wire(payment_captured(session_id="s_not_ours"))
    sig = _sign(raw)
    baseline = predicate.evaluate(raw, sig, SECRET)
    for header in ("", "evt_1", "evt_2", hashlib.sha256(raw).hexdigest(), "x" * 400):
        v = predicate.evaluate(raw, sig, SECRET, header_event_id=header)
        assert (v.green, v.reason, v.severity, v.event_id, v.session_id) == (
            baseline.green,
            baseline.reason,
            baseline.severity,
            baseline.event_id,
            baseline.session_id,
        )


def test_the_replay_key_is_never_built_from_the_header_in_source():
    """Belt and braces at the AST level: inside `_evaluate`, no statement that
    reaches `self._seen` may mention ANY header-derived name.

    Matching on the substring "header" rather than the exact parameter name is
    deliberate — the obvious way to reintroduce this bug is to normalise the
    parameter into a local first and key on that.
    """
    src = inspect.getsource(GreenPredicate._evaluate)
    tree = ast.parse(src.replace("\n    ", "\n").lstrip())

    def names(node) -> set[str]:
        return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}

    def touches_seen(node) -> bool:
        return any(
            isinstance(n, ast.Attribute) and n.attr == "_seen" for n in ast.walk(node)
        )

    assert touches_seen(tree), "expected the replay store to be used somewhere"

    header_names = {n for n in names(tree) if "header" in n.lower()}
    assert header_names, "expected the header to be named at least once"

    offenders = [
        (stmt.lineno, sorted(names(stmt) & header_names))
        for stmt in ast.walk(tree)
        if isinstance(stmt, ast.stmt)
        and not isinstance(stmt, (ast.FunctionDef, ast.Return))
        and touches_seen(stmt)
        and names(stmt) & header_names
    ]
    assert offenders == [], (
        "the unauthenticated X-Razorpay-Event-Id header reaches the replay "
        f"store at {offenders} inside _evaluate"
    )


def test_envelope_id_is_used_as_the_replay_key_when_present(predicate):
    body = payment_captured()
    body["id"] = "evt_PbF0kPvPUAdCzC"
    raw = wire(body)
    v = predicate.evaluate(raw, _sign(raw), SECRET)
    assert v.green and v.event_id == "evt_PbF0kPvPUAdCzC"
    assert predicate.evaluate(raw, _sign(raw), SECRET).reason == "replay"


def test_a_failed_delivery_can_still_succeed_on_retry():
    """Razorpay retries. An event that failed because the intent was not yet
    visible must not be poisoned into a permanent 'replay'."""
    table: dict[str, Intent] = {}
    p = GreenPredicate(lambda sid: table.get(sid))
    raw = wire(payment_captured())
    sig = _sign(raw)
    assert p.evaluate(raw, sig, SECRET).reason == "unknown_session"
    table[SESSION] = Intent(SESSION, AMOUNT)
    assert p.evaluate(raw, sig, SECRET).green is True
    assert p.evaluate(raw, sig, SECRET).reason == "replay"


def test_unsigned_traffic_cannot_poison_the_replay_store(predicate):
    raw = wire(payment_captured())
    for _ in range(50):
        predicate.evaluate(raw, "0" * 64, SECRET)
    assert len(predicate.seen) == 0
    assert predicate.evaluate(raw, _sign(raw), SECRET).green is True


def test_a_second_session_greens_independently():
    p = GreenPredicate(lookup_of(Intent("s_1", 10001), Intent("s_2", 10001)))
    r1 = wire(payment_captured(session_id="s_1", amount=10001))
    r2 = wire(payment_captured(session_id="s_2", amount=10001))
    assert p.evaluate(r1, _sign(r1), SECRET).green is True
    assert p.evaluate(r2, _sign(r2), SECRET).green is True


def test_an_injected_seen_store_is_used():
    store: set[str] = set()
    p = GreenPredicate(lookup_of(Intent(SESSION, AMOUNT)), seen=store)
    raw = wire(payment_captured())
    p.evaluate(raw, _sign(raw), SECRET)
    assert len(store) == 1
    # a fresh predicate sharing the durable store still refuses the replay
    p2 = GreenPredicate(lookup_of(Intent(SESSION, AMOUNT)), seen=store)
    assert p2.evaluate(raw, _sign(raw), SECRET).reason == "replay"


# ---------------------------------------------------------------- stale mirror


def test_stale_mirror_downgrades_red_to_amber(predicate):
    raw = wire(payment_captured(amount=AMOUNT + 1))
    sig = _sign(raw)
    fresh = predicate.evaluate(raw, sig, SECRET)
    assert fresh.severity == RED and fresh.downgraded_from_red is False

    stale = predicate.evaluate(raw, sig, SECRET, mirror_stale=True)
    assert stale.severity == AMBER
    assert stale.downgraded_from_red is True
    assert stale.reason == "amount_mismatch"  # the reason is unchanged, only the colour


def test_no_verdict_is_ever_red_when_the_mirror_is_stale():
    """The system must be architecturally incapable of contradicting a paying
    customer while it might be missing events."""
    p = GreenPredicate(lookup_of(Intent(SESSION, AMOUNT, state="SETTLED")))
    bodies = [
        payment_captured(),
        payment_captured(amount=AMOUNT + 1),
        payment_captured(session_id="s_other"),
        payment_captured(currency="USD"),
        payment_link_paid(payment_amount=1),
    ]
    seen_reasons = set()
    for body in bodies:
        raw = wire(body)
        v = p.evaluate(raw, _sign(raw), SECRET, mirror_stale=True)
        assert v.severity != RED
        seen_reasons.add(v.reason)
    assert len(seen_reasons) >= 3


def test_stale_mirror_does_not_block_green(predicate):
    """A verified webhook is fresh evidence. Staleness only gags the negative."""
    raw = wire(payment_captured())
    v = predicate.evaluate(raw, _sign(raw), SECRET, mirror_stale=True)
    assert v.green is True and v.severity == GREEN and v.mirror_stale is True


def test_a_red_stale_verdict_cannot_be_constructed_at_all():
    with pytest.raises(WebhookError):
        GreenVerdict(
            green=False, reason="amount_mismatch", severity=RED, mirror_stale=True
        )


# ---------------------------------------------------------------- invariants


def test_no_verdict_object_can_lie():
    with pytest.raises(WebhookError):
        GreenVerdict(green=True, reason="bad_signature", severity=GREEN)
    with pytest.raises(WebhookError):
        GreenVerdict(green=True, reason="green", severity=AMBER)
    with pytest.raises(WebhookError):
        GreenVerdict(green=False, reason="not_a_real_code", severity=AMBER)


def test_green_events_is_exactly_the_two():
    assert GREEN_EVENTS == {"payment_link.paid", "payment.captured"}


# --------------------------------------------------- M12: parse_float=str pin
#
# The mutation testing run found that deleting `parse_float=str` from the
# json.loads call killed ZERO tests. It survived because every existing test
# that feeds a decimal asserts a REASON CODE, and `money.paise` rejects a float
# with the same code it uses for the string "21437.0". The behaviour is
# identical; what changes is that a float object now exists inside the process,
# holding a number that came off the wire and is one careless int() away from
# being money. These two tests assert the property directly, so the mutation is
# no longer silent.

def test_a_bare_decimal_never_becomes_a_float():
    """`214.50` in the body must arrive as the STRING "214.50" (INVARIANT 1)."""
    from gawaah.webhook import _parse_body

    parsed = _parse_body(b'{"amount":214.50,"amount_paid":0.1}')
    assert parsed is not None
    assert parsed["amount"] == "214.50", (
        "parse_float=str is load-bearing: the decimal was materialised as a "
        f"{type(parsed['amount']).__name__}"
    )
    assert isinstance(parsed["amount"], str)
    assert not isinstance(parsed["amount"], float)
    # exact, not 0.1 == 0.1 + 0.0 luck: the digits that arrived are preserved
    assert parsed["amount_paid"] == "0.1"


def test_no_float_object_is_constructed_anywhere_in_a_parsed_body():
    """Walk the whole parsed structure. Not one float, at any depth."""
    from gawaah.webhook import _parse_body

    raw = wire(
        {
            "event": "payment_link.paid",
            "payload": {"payment_link": {"entity": {"amount": 21437}}},
            "decimals": [1.5, {"deep": [[2.25]]}, 3e10, -0.0],
        }
    )
    parsed = _parse_body(raw)
    assert parsed is not None

    floats: list[object] = []

    def walk(node):
        if isinstance(node, float):
            floats.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v)

    walk(parsed)
    assert floats == [], f"float objects materialised from the wire: {floats}"
    print(f"\n[measured] floats materialised from a decimal-laden body: {len(floats)}")


def test_webhook_module_has_no_float_in_the_money_path():
    """Reuse the repo's own AST lint rather than a second, weaker copy of it."""
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "lint_no_float", root / "tools" / "lint_no_float.py"
    )
    lint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lint)
    target = root / "gawaah" / "webhook.py"
    v = lint.V("gawaah/webhook.py")
    v.visit(ast.parse(target.read_text()))
    assert v.bad == [], f"float in the money path: {v.bad}"


def test_no_function_in_the_module_can_hand_out_a_signature():
    """INVARIANT 6: this file verifies, it never emits. Name-based bans are
    theatre, so assert the real property at the AST level — no function returns
    an HMAC digest, and nothing constructs a payment payload."""
    src = (Path(__file__).resolve().parent.parent / "gawaah" / "webhook.py").read_text()
    tree = ast.parse(src)

    def is_hmac_digest(node: ast.AST) -> bool:
        return any(
            isinstance(c, ast.Call)
            and isinstance(c.func, ast.Attribute)
            and c.func.attr == "hexdigest"
            and isinstance(c.func.value, ast.Call)
            and isinstance(c.func.value.func, ast.Attribute)
            and c.func.value.func.attr == "new"
            and isinstance(c.func.value.func.value, ast.Name)
            and c.func.value.func.value.id == "hmac"
            for c in ast.walk(node)
        )

    escaping = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Return) and n.value is not None and is_hmac_digest(n.value)
    ]
    assert escaping == [], "an HMAC digest escapes a function — that is a signer"

    # the one HMAC that is computed goes straight into the constant-time compare
    hmacs = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "new"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "hmac"
    ]
    assert len(hmacs) == 1, f"expected exactly one hmac.new, found {len(hmacs)}"

    banned = ("upi://", "razorpay.com/v1", "requests.post", "httpx.post")
    for token in banned:
        assert token not in src, f"forgery/egress primitive in webhook.py: {token}"


def test_every_reason_code_is_reachable():
    """A closed vocabulary is only honest if every code in it can actually
    happen. This walks all 19 and asserts full coverage — no dead codes, and no
    failure mode that silently shares another one's label."""
    open_intent = GreenPredicate(lookup_of(Intent(SESSION, AMOUNT)))
    settled = GreenPredicate(lookup_of(Intent(SESSION, AMOUNT, state="SETTLED")))
    float_intent = GreenPredicate(lambda s: {"amount_paise": 214.37, "state": "OPEN"})

    no_amount = payment_captured()
    del no_amount["payload"]["payment"]["entity"]["amount"]

    no_notes = payment_captured()
    no_notes["payload"]["payment"]["entity"]["notes"] = {}

    no_event = payment_captured()
    del no_event["event"]

    empty_payload = payment_captured()
    empty_payload["payload"] = {}

    conflict = payment_link_paid()
    conflict["payload"]["payment"]["entity"]["notes"]["session_id"] = "s_other"

    good = wire(payment_captured())
    cases: list[tuple[str, GreenPredicate, bytes, str, str, dict]] = [
        ("green", open_intent, good, _sign(good), SECRET, {}),
        ("secret_not_configured", open_intent, good, "x" * 64, "", {}),
        ("bad_signature", open_intent, good, "0" * 64, SECRET, {}),
        ("malformed_body", open_intent, b"[]", _sign(b"[]"), SECRET, {}),
        ("missing_event", open_intent, wire(no_event), _sign(wire(no_event)), SECRET, {}),
        ("no_entity", open_intent, wire(empty_payload), _sign(wire(empty_payload)), SECRET, {}),
        ("missing_session_id", open_intent, wire(no_notes), _sign(wire(no_notes)), SECRET, {}),
        ("session_id_conflict", open_intent, wire(conflict), _sign(wire(conflict)), SECRET, {}),
        ("unknown_session", GreenPredicate(lookup_of()), good, _sign(good), SECRET, {}),
        ("intent_not_open", settled, good, _sign(good), SECRET, {}),
        ("intent_amount_invalid", float_intent, good, _sign(good), SECRET, {}),
        ("amount_missing", open_intent, wire(no_amount), _sign(wire(no_amount)), SECRET, {}),
    ]
    seen = set()
    for expected, pred, body, sig, sec, kw in cases:
        got = pred.evaluate(body, sig, sec, **kw).reason
        assert got == expected, f"expected {expected}, got {got}"
        seen.add(got)

    # the rest need their own little scenarios
    for event in ("payment.failed",):
        b = payment_captured()
        b["event"] = event
        seen.add(open_intent.evaluate(wire(b), _sign(wire(b)), SECRET).reason)
    b = wire(payment_captured(status="refunded"))
    seen.add(open_intent.evaluate(b, _sign(b), SECRET).reason)
    b = wire(payment_captured(currency="EUR"))
    seen.add(open_intent.evaluate(b, _sign(b), SECRET).reason)
    b = wire(payment_captured(amount=AMOUNT + 7))
    seen.add(open_intent.evaluate(b, _sign(b), SECRET).reason)
    b = wire(payment_link_paid(payment_amount=1))
    seen.add(open_intent.evaluate(b, _sign(b), SECRET).reason)

    # the four codes added by the ask-vs-settlement and fail-closed fixes
    no_status = payment_captured()
    del no_status["payload"]["payment"]["entity"]["status"]
    seen.add(open_intent.evaluate(wire(no_status), _sign(wire(no_status)), SECRET).reason)

    no_currency = payment_captured()
    del no_currency["payload"]["payment"]["entity"]["currency"]
    seen.add(
        open_intent.evaluate(wire(no_currency), _sign(wire(no_currency)), SECRET).reason
    )

    no_paid = payment_link_only()
    del no_paid["payload"]["payment_link"]["entity"]["amount_paid"]
    seen.add(open_intent.evaluate(wire(no_paid), _sign(wire(no_paid)), SECRET).reason)

    b = wire(payment_link_only(amount=AMOUNT, amount_paid=500))
    seen.add(open_intent.evaluate(b, _sign(b), SECRET).reason)
    b = b'{"event":"payment.captured","payload":{"payment":{"entity":{"amount":"21437","currency":"INR","status":"captured","notes":{"session_id":"s_0042"}}}}}'
    seen.add(open_intent.evaluate(b, _sign(b), SECRET).reason)
    seen.add(open_intent.evaluate(good, _sign(good), SECRET).reason)  # replay of case 1

    assert seen == REASON_CODES, f"unreached codes: {sorted(REASON_CODES - seen)}"
    print(f"\n[measured] reason codes reached by tests: {len(seen)}/{len(REASON_CODES)}")


def test_every_returned_reason_is_in_the_closed_vocabulary(predicate):
    """The bench fails on an unknown code, so the code set must be closed."""
    raw = wire(payment_captured())
    cases = [
        (raw, _sign(raw), SECRET, {}),
        (raw, "bad", SECRET, {}),
        (raw, _sign(raw, ""), "", {}),
        (b"{", _sign(b"{"), SECRET, {}),
    ]
    for body, sig, sec, kw in cases:
        assert predicate.evaluate(body, sig, sec, **kw).reason in REASON_CODES


# ---------------------------------------------------------------- audit trail


def test_every_evaluation_appends_one_verifiable_ledger_line(tmp_path):
    path = tmp_path / "audit.jsonl"
    led, clk = Ledger(path), VirtualClock()
    p = GreenPredicate(lookup_of(Intent(SESSION, AMOUNT)), ledger=led, clock=clk)

    raw = wire(payment_captured())
    p.evaluate(raw, _sign(raw), SECRET)
    p.evaluate(raw, "0" * 64, SECRET)
    bad = wire(payment_captured(amount=AMOUNT + 1))
    p.evaluate(bad, _sign(bad), SECRET)

    ok, n, head, err = verify(path)
    assert ok, err
    assert n == 3
    rows = list(led.read())
    assert [r["reason"] for r in rows] == ["green", "bad_signature", "amount_mismatch"]
    assert rows[0]["green"] is True and rows[1]["green"] is False


def test_the_ledger_never_records_the_secret_or_the_signature(tmp_path):
    path = tmp_path / "audit.jsonl"
    led, clk = Ledger(path), VirtualClock()
    p = GreenPredicate(lookup_of(Intent(SESSION, AMOUNT)), ledger=led, clock=clk)
    raw = wire(payment_captured())
    sig = _sign(raw)
    p.evaluate(raw, sig, SECRET)

    text = path.read_text()
    assert SECRET not in text
    assert sig not in text
    assert raw.decode() not in text
    assert hashlib.sha256(raw).hexdigest() in text  # the delivery is still identifiable


def test_ledger_without_a_clock_is_refused(tmp_path):
    with pytest.raises(WebhookError):
        GreenPredicate(lookup_of(), ledger=Ledger(tmp_path / "a.jsonl"))


def test_lookup_must_be_callable():
    with pytest.raises(WebhookError):
        GreenPredicate({"s_0042": Intent(SESSION, AMOUNT)})


# ---------------------------------------------------------------- adversarial


def test_ten_thousand_forged_signatures_produce_zero_greens():
    p = GreenPredicate(lookup_of(Intent(SESSION, AMOUNT)))
    raw = wire(payment_captured())
    rng = random.Random(20260829)
    greens = 0
    n = 10_000
    for _ in range(n):
        forged = "".join(rng.choice("0123456789abcdef") for _ in range(64))
        if p.evaluate(raw, forged, SECRET).green:
            greens += 1
    assert greens == 0
    assert len(p.seen) == 0
    print(f"\n[measured] forged signatures rejected: {n}/{n}, greens: {greens}")


def test_MEASURED_a_full_sweep_of_settlement_amounts_greens_exactly_once():
    """Every settlement from ₹0.00 to ₹428.74 against a ₹214.37 intent, in both
    envelope shapes. Exactly one value in each sweep may green: the exact one."""
    amounts = sorted(set(range(0, AMOUNT * 2, 7)) | {AMOUNT})
    greens: dict[str, list[int]] = {"link_only": [], "link_and_payment": []}
    for paid in amounts:
        p = GreenPredicate(lookup_of(Intent(SESSION, AMOUNT)))
        raw = wire(payment_link_only(amount=AMOUNT, amount_paid=paid))
        if p.evaluate(raw, _sign(raw), SECRET).green:
            greens["link_only"].append(paid)

        p2 = GreenPredicate(lookup_of(Intent(SESSION, AMOUNT)))
        raw2 = wire(payment_link_paid(amount=AMOUNT, payment_amount=paid))
        if p2.evaluate(raw2, _sign(raw2), SECRET).green:
            greens["link_and_payment"].append(paid)

    assert greens["link_only"] == [AMOUNT]
    assert greens["link_and_payment"] == [AMOUNT]
    print(
        f"\n[measured] settlement sweep: {len(amounts)} amounts x 2 envelope "
        f"shapes = {len(amounts) * 2} deliveries; greens: "
        f"{len(greens['link_only']) + len(greens['link_and_payment'])}, "
        f"both at exactly {AMOUNT}"
    )


def test_MEASURED_a_thousand_guessed_event_ids_block_zero_genuine_greens():
    """The denial-of-green attack at scale: an attacker who lands one genuine
    payment and rewrites its header 1000 times over, then 1000 genuine sales
    whose signed envelope ids are exactly the values guessed."""
    ids = [f"evt_{i:06d}" for i in range(1000)]
    intents = [Intent("s_attacker", 100)] + [
        Intent(f"s_{i}", 10000 + i) for i in range(len(ids))
    ]
    p = GreenPredicate(lookup_of(*intents))

    poisoned = 0
    for guess in ids:
        atk = payment_captured(session_id="s_attacker", amount=100)
        atk["id"] = f"evt_atk_{guess}"
        raw = wire(atk)
        if p.evaluate(raw, _sign(raw), SECRET, header_event_id=guess).green:
            poisoned += 1

    blocked = 0
    for i, guess in enumerate(ids):
        body = payment_captured(session_id=f"s_{i}", amount=10000 + i)
        body["id"] = guess
        raw = wire(body)
        if not p.evaluate(raw, _sign(raw), SECRET).green:
            blocked += 1

    assert blocked == 0, f"{blocked}/{len(ids)} genuine sales denied their green"
    print(
        f"\n[measured] denial-of-green: {poisoned} header-poisoning deliveries "
        f"accepted, {blocked}/{len(ids)} subsequent genuine sales blocked"
    )


@settings(max_examples=300, deadline=None)
@given(
    body=st.binary(min_size=0, max_size=400),
    sig=st.text(alphabet="0123456789abcdefABCDEF", min_size=0, max_size=80),
)
def test_no_arbitrary_body_and_signature_pair_ever_greens(body, sig):
    p = GreenPredicate(lookup_of(Intent(SESSION, AMOUNT)))
    v = p.evaluate(body, sig, SECRET)
    assert v.green is False
    assert v.reason in REASON_CODES
    assert v.severity in {AMBER, RED}


@settings(max_examples=200, deadline=None)
@given(amount=st.integers(min_value=1, max_value=10_000_000))
def test_only_the_exact_amount_greens(amount):
    p = GreenPredicate(lookup_of(Intent(SESSION, AMOUNT)))
    raw = wire(payment_captured(amount=amount))
    v = p.evaluate(raw, _sign(raw), SECRET)
    assert v.green == (amount == AMOUNT)
    if not v.green:
        assert v.reason == "amount_mismatch"


def test_evaluate_latency():
    """A number, produced by running it, not by guessing."""
    p = GreenPredicate(lambda sid: Intent(SESSION, AMOUNT))
    raw = wire(payment_captured())
    sig = _sign(raw)
    n = 2000
    t0 = time.perf_counter_ns()
    for _ in range(n):
        GreenPredicate(lambda sid: Intent(SESSION, AMOUNT)).evaluate(raw, sig, SECRET)
    dt = time.perf_counter_ns() - t0
    per_call_us = dt // n // 1000
    print(f"\n[measured] evaluate(): {per_call_us} us/call over {n} calls "
          f"on a {len(raw)}-byte body")
    assert per_call_us < 2000  # generous; this must never be a bottleneck
