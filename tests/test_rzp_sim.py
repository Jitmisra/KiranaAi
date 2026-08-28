"""S4b acceptance: the money path is exercisable with no Razorpay keys.

The headline assertions, in the order they matter:
  * a paid link emits exactly one correctly-signed webhook
  * the signature verifies, and FAILS if any single byte of the body changes
  * duplicate_webhook emits the same event twice with the same event id
  * wrong_amount emits a mismatching amount that passes every gate but the
    amount gate
  * ids are byte-identical across two separate processes

Plus a local re-implementation of the four-part green predicate (invariant 2),
used to prove that each injected failure blocks green for the *right* reason.
That predicate lives in `paisa` in production; here it exists only to hold the
fixture to account.
"""
from __future__ import annotations

import ast
import calendar
import hashlib
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from gawaah.clock import VirtualClock
from gawaah.ledger import Ledger, canonical, verify
from gawaah.money import MoneyError, to_rupees_str
from gawaah.rzp_sim import (
    EVENTS,
    ID_LEN,
    MIN_AMOUNT_PAISE,
    MIN_EXPIRY_S,
    MODES,
    SHORT_URL_PREFIX,
    SIM_BODY_MARKER,
    ConfigError,
    Delivery,
    RazorpayConfig,
    RazorpaySim,
    RazorpaySimError,
    RazorpaySimTimeout,
    build_client,
    iso_to_unix,
    serialize_body,
    sign_body,
    verify_webhook_signature,
)

ROOT = Path(__file__).resolve().parent.parent
SECRET = "whsec_gawaah_test_secret"


# ---------------------------------------------------------------- fixtures

def make_sim(secret: str = SECRET, *, seed: int = 0, step_ms: int = 100, **kw):
    """A simulator plus the list its sink appends to."""
    sink: list[Delivery] = []
    sim = RazorpaySim(
        webhook_secret=secret,
        clock=VirtualClock(step_ms=step_ms),
        seed=seed,
        sink=sink.append,
        **kw,
    )
    return sim, sink


def mint(sim, amount_paise: int = 21450, session_id: str = "sess_abc123") -> dict:
    return sim.create_payment_link(
        amount_paise,
        {"session_id": session_id, "audit_hash": "a" * 64, "catalog_version": "v1"},
        reference_id=session_id,
    )


# ------------------------------------------------- the green predicate (§2)
#
# Invariant 2 verbatim: valid HMAC-SHA256 X-Razorpay-Signature over RAW BYTES
# BEFORE any JSON parsing, AND event in the green set, AND notes.session_id
# matches an OPEN intent, AND amount == intent.amount_paise exactly.

GREEN_EVENTS = frozenset({"payment_link.paid"})


def green(delivery: Delivery, secret: str, intent: dict) -> tuple[bool, str]:
    """Returns (is_green, reason). Reason is always a named code, never a guess."""
    # gate 1 — raw bytes, before any parsing
    if not verify_webhook_signature(delivery.body, delivery.signature, secret):
        return False, "bad_signature"
    body = json.loads(delivery.body.decode("utf-8"))   # ONLY after gate 1
    # gate 2 — event in the green set
    if body["event"] not in GREEN_EVENTS:
        return False, "event_not_green"
    entity = body["payload"]["payment_link"]["entity"]
    # gate 3 — session id matches an OPEN intent
    if entity["notes"].get("session_id") != intent["session_id"]:
        return False, "session_mismatch"
    if intent["state"] != "OPEN":
        return False, "intent_not_open"
    # gate 4 — exact amount
    if entity["amount"] != intent["amount_paise"]:
        return False, "amount_mismatch"
    return True, "green"


# ---------------------------------------------------------------- minting

def test_create_payment_link_has_the_documented_shape():
    sim, sink = make_sim()
    link = mint(sim, 21450)
    assert link["id"].startswith("plink_")
    assert len(link["id"]) == len("plink_") + ID_LEN
    assert link["status"] == "created"
    assert link["amount"] == 21450
    assert link["currency"] == "INR"
    assert link["notes"]["session_id"] == "sess_abc123"
    assert link["entity"] == "payment_link"


def test_short_url_is_a_string_so_the_qr_renders_locally():
    """PRD 10.1: this is why Payment Links beat qr_codes. A string needs no
    remote image fetch, so a mint survives a dead network at the counter."""
    sim, _ = make_sim()
    link = mint(sim)
    assert isinstance(link["short_url"], str)
    assert link["short_url"].startswith(SHORT_URL_PREFIX)
    assert len(link["short_url"]) > len(SHORT_URL_PREFIX)
    # nothing about it requires I/O to render
    assert link["short_url"].isprintable()


def test_minting_never_emits_a_webhook():
    """Invariant 2: never green on mint."""
    sim, sink = make_sim()
    mint(sim)
    mint(sim, 500, "sess_two")
    assert sink == []
    assert sim.deliveries == ()


def test_amount_must_be_integer_paise():
    sim, _ = make_sim()
    for bad in (214.50, True, "21450", None):
        with pytest.raises(MoneyError):
            sim.create_payment_link(bad, {"session_id": "s"})


def test_amount_below_the_rupee_floor_is_rejected():
    sim, _ = make_sim()
    with pytest.raises(RazorpaySimError) as e:
        sim.create_payment_link(MIN_AMOUNT_PAISE - 1, {"session_id": "s"})
    assert e.value.code == "BAD_REQUEST_ERROR"


def test_notes_limits_match_the_real_api():
    sim, _ = make_sim()
    with pytest.raises(RazorpaySimError):
        sim.create_payment_link(500, {f"k{i}": "v" for i in range(16)})
    with pytest.raises(RazorpaySimError):
        sim.create_payment_link(500, {"k": "x" * 257})
    with pytest.raises(RazorpaySimError):
        sim.create_payment_link(500, {"k": 5})          # non-string value


def test_expire_by_below_the_fifteen_minute_floor_is_rejected():
    """SIX 229: the floor is real, and it is why closing an intent without
    cancelling leaves an orphaned-but-payable link."""
    sim, _ = make_sim()
    with pytest.raises(RazorpaySimError) as e:
        sim.create_payment_link(500, {"session_id": "s"}, expire_by=1)
    assert str(MIN_EXPIRY_S) in e.value.description


# ---------------------------------------------------- the headline webhook

def test_a_paid_link_emits_exactly_one_correctly_signed_webhook():
    sim, sink = make_sim()
    link = mint(sim, 21450)

    result = sim.pay_link(link["id"])

    assert len(sink) == 1, f"expected exactly one webhook, got {len(sink)}"
    assert len(result.deliveries) == 1
    d = sink[0]
    assert d.event == "payment_link.paid"
    assert d.headers["Content-Type"] == "application/json"
    assert verify_webhook_signature(d.body, d.signature, SECRET) is True
    body = d.json()
    assert body["event"] == "payment_link.paid"
    assert body["payload"]["payment_link"]["entity"]["amount"] == 21450
    assert body["payload"]["payment_link"]["entity"]["status"] == "paid"
    assert body["payload"]["payment"]["entity"]["status"] == "captured"
    assert sim.fetch_payment_link(link["id"])["status"] == "paid"


def test_the_signature_fails_if_any_single_byte_of_the_body_changes():
    """Every byte position, not a sample. This is the whole security property."""
    sim, sink = make_sim()
    sim.pay_link(mint(sim)["id"])
    d = sink[0]
    assert verify_webhook_signature(d.body, d.signature, SECRET) is True

    survivors = []
    for i in range(len(d.body)):
        tampered = bytearray(d.body)
        tampered[i] ^= 0x01
        if verify_webhook_signature(bytes(tampered), d.signature, SECRET):
            survivors.append(i)
    assert survivors == [], f"tampering survived at byte offsets {survivors}"
    assert len(d.body) > 500, "body suspiciously small; is it really the event?"


def test_the_signature_fails_under_the_wrong_secret():
    sim, sink = make_sim()
    sim.pay_link(mint(sim)["id"])
    d = sink[0]
    assert verify_webhook_signature(d.body, d.signature, SECRET + "x") is False
    assert verify_webhook_signature(d.body, "", SECRET) is False
    assert verify_webhook_signature(d.body, None, SECRET) is False
    assert verify_webhook_signature("not bytes", d.signature, SECRET) is False


def test_body_bytes_are_not_recoverable_by_reserialising():
    """The fixture is built so that parse-then-reserialise CANNOT verify.

    If the body were sorted-key canonical JSON, a receiver that did
    `verify(canonical(json.loads(body)))` would still pass, silently hiding
    exactly the bug invariant 2 exists to prevent. Here it fails, so
    "verify over raw bytes before JSON parsing" is enforced, not requested.
    """
    sim, sink = make_sim()
    sim.pay_link(mint(sim)["id"])
    d = sink[0]
    parsed = json.loads(d.body.decode("utf-8"))

    assert canonical(parsed) != d.body
    assert verify_webhook_signature(canonical(parsed), d.signature, SECRET) is False
    assert verify_webhook_signature(
        json.dumps(parsed).encode("utf-8"), d.signature, SECRET
    ) is False
    # ... and the real raw bytes still verify
    assert verify_webhook_signature(d.body, d.signature, SECRET) is True


def test_sign_body_refuses_a_parsed_object():
    with pytest.raises(TypeError):
        sign_body({"event": "payment_link.paid"}, SECRET)   # type: ignore[arg-type]


def test_the_full_green_predicate_passes_on_a_clean_webhook():
    sim, sink = make_sim()
    link = mint(sim, 21450, "sess_green")
    intent = {"session_id": "sess_green", "amount_paise": 21450, "state": "OPEN"}
    sim.pay_link(link["id"])
    assert green(sink[0], SECRET, intent) == (True, "green")


@pytest.mark.parametrize(
    "intent_patch,reason",
    [
        ({"session_id": "sess_other"}, "session_mismatch"),
        ({"state": "CLOSED"}, "intent_not_open"),
        ({"amount_paise": 21451}, "amount_mismatch"),
    ],
)
def test_each_green_gate_can_independently_refuse(intent_patch, reason):
    sim, sink = make_sim()
    link = mint(sim, 21450, "sess_green")
    sim.pay_link(link["id"])
    intent = {"session_id": "sess_green", "amount_paise": 21450, "state": "OPEN"}
    intent.update(intent_patch)
    assert green(sink[0], SECRET, intent) == (False, reason)


def test_payment_captured_alone_is_not_green():
    """It is a mirror row, not a green event (PRD 10.3)."""
    sim, sink = make_sim()
    link = mint(sim, 21450, "sess_green")
    sim.pay_link(link["id"], emit_captured=True)
    captured = [d for d in sink if d.event == "payment.captured"]
    assert len(captured) == 1
    intent = {"session_id": "sess_green", "amount_paise": 21450, "state": "OPEN"}
    assert green(captured[0], SECRET, intent) == (False, "event_not_green")


def test_emit_captured_produces_both_events_in_the_natural_order():
    sim, sink = make_sim()
    sim.pay_link(mint(sim)["id"], emit_captured=True)
    assert [d.event for d in sink] == ["payment.captured", "payment_link.paid"]
    assert all(verify_webhook_signature(d.body, d.signature, SECRET) for d in sink)
    assert len({d.event_id for d in sink}) == 2      # distinct events, distinct ids


# ------------------------------------------------------- failure injection

def test_set_mode_rejects_an_unknown_mode():
    sim, _ = make_sim()
    with pytest.raises(ValueError):
        sim.set_mode("banana")
    assert set(MODES) == {
        "normal", "timeout", "error",
        "duplicate_webhook", "out_of_order", "wrong_amount",
    }


def test_duplicate_webhook_emits_the_same_event_twice_with_the_same_event_id():
    sim, sink = make_sim()
    link = mint(sim, 21450)
    sim.set_mode("duplicate_webhook")
    sim.pay_link(link["id"])

    assert len(sink) == 2
    a, b = sink
    assert a.event == b.event == "payment_link.paid"
    assert a.event_id == b.event_id            # THE replay property
    assert a.body == b.body                    # byte-identical
    assert a.signature == b.signature
    assert a.body_sha256 == b.body_sha256
    assert a.seq != b.seq                      # two distinct pushes, honestly numbered
    # both verify: a replay is a *valid* webhook, which is why dedup must be
    # on the event id and not on signature validity
    assert all(verify_webhook_signature(d.body, d.signature, SECRET) for d in sink)
    intent = {"session_id": "sess_abc123", "amount_paise": 21450, "state": "OPEN"}
    assert [green(d, SECRET, intent) for d in sink] == [(True, "green")] * 2


def test_wrong_amount_emits_a_mismatching_amount():
    sim, sink = make_sim()
    link = mint(sim, 21450)
    sim.set_mode("wrong_amount")                    # default delta: one paisa
    sim.pay_link(link["id"])

    d = sink[0]
    body = d.json()
    entity = body["payload"]["payment_link"]["entity"]
    assert entity["amount"] == 21451
    assert entity["amount"] != 21450
    assert body["payload"]["payment"]["entity"]["amount"] == 21451
    assert body["payload"]["order"]["entity"]["amount"] == 21451

    # every other gate still passes; only the amount gate can catch it
    assert verify_webhook_signature(d.body, d.signature, SECRET) is True
    assert body["event"] in GREEN_EVENTS
    assert entity["notes"]["session_id"] == "sess_abc123"
    intent = {"session_id": "sess_abc123", "amount_paise": 21450, "state": "OPEN"}
    assert green(d, SECRET, intent) == (False, "amount_mismatch")

    # server-side truth is unchanged: the sim lied on the wire, not in its books
    assert sim.fetch_payment_link(link["id"])["amount"] == 21450


def test_wrong_amount_delta_is_configurable():
    sim, sink = make_sim()
    link = mint(sim, 21450)
    sim.set_mode("wrong_amount", wrong_amount_delta_paise=-10000)
    sim.pay_link(link["id"])
    entity = sink[0].json()["payload"]["payment_link"]["entity"]
    assert entity["amount"] == 11450
    with pytest.raises(ValueError):
        sim.set_mode("wrong_amount", wrong_amount_delta_paise=0)


def test_out_of_order_reverses_the_two_events():
    sim, sink = make_sim()
    sim.set_mode("out_of_order")
    sim.pay_link(mint(sim)["id"])
    assert [d.event for d in sink] == ["payment_link.paid", "payment.captured"]
    assert all(verify_webhook_signature(d.body, d.signature, SECRET) for d in sink)


def test_timeout_moves_the_money_but_delivers_nothing():
    """The dangerous case: the rupee landed and we never heard. This is the
    failure the poll fallback (`ledger_source: poll`) exists for."""
    sim, sink = make_sim()
    link = mint(sim, 21450)
    sim.set_mode("timeout")

    result = sim.pay_link(link["id"])

    assert sink == []                                  # nothing reached us
    assert len(result.deliveries) == 1                 # the server did produce it
    assert result.deliveries[0].delivered is False
    assert result.deliveries[0].error == "simulated network timeout"
    assert sim.delivered_to_sink == ()
    assert result.payment["status"] == "captured"      # the money moved

    sim.set_mode("normal")                             # network comes back
    polled = sim.fetch_payment_link(link["id"])
    assert polled["status"] == "paid"
    assert polled["amount_paid"] == 21450


def test_timeout_raises_on_every_api_call():
    sim, _ = make_sim()
    link = mint(sim)
    sim.set_mode("timeout")
    for call in (
        lambda: sim.create_payment_link(500, {"session_id": "s2"}),
        lambda: sim.fetch_payment_link(link["id"]),
        lambda: sim.fetch_payment_links(),
        lambda: sim.fetch_payments(),
        lambda: sim.cancel_payment_link(link["id"]),
    ):
        with pytest.raises(RazorpaySimTimeout) as e:
            call()
        assert e.value.code == "GATEWAY_TIMEOUT"


def test_error_mode_raises_a_razorpay_shaped_error():
    sim, _ = make_sim()
    sim.set_mode("error")
    with pytest.raises(RazorpaySimError) as e:
        sim.create_payment_link(500, {"session_id": "s"})
    env = e.value.as_dict()
    assert set(env["error"]) == {"code", "description"}
    assert env["error"]["code"] == "SERVER_ERROR"


def test_error_mode_makes_the_payment_fail_and_emits_nothing():
    sim, sink = make_sim()
    link = mint(sim, 21450)
    sim.set_mode("error")
    result = sim.pay_link(link["id"])
    assert result.payment["status"] == "failed"
    assert result.payment["captured"] is False
    assert result.deliveries == ()
    assert sink == []
    sim.set_mode("normal")
    assert sim.fetch_payment_link(link["id"])["status"] == "created"   # still payable


def test_a_sink_that_raises_is_recorded_not_propagated():
    def boom(_d):
        raise RuntimeError("endpoint down")

    sim = RazorpaySim(SECRET, VirtualClock(), sink=boom)
    link = sim.create_payment_link(500, {"session_id": "s"})
    result = sim.pay_link(link["id"])          # must not raise
    assert result.deliveries[0].delivered is False
    assert "endpoint down" in result.deliveries[0].error


# ------------------------------------------------------------ determinism

def test_ids_are_deterministic_within_a_process():
    def run():
        sim, _ = make_sim(seed=42)
        link = sim.create_payment_link(
            21450, {"session_id": "sess_det"}, reference_id="sess_det"
        )
        sim.pay_link(link["id"], emit_captured=True)
        return sim.transcript()

    a, b = run(), run()
    assert a == b
    assert "plink_" in a and "pay_" in a


def test_different_seeds_produce_different_ids():
    def run(seed):
        sim, _ = make_sim(seed=seed)
        sim.pay_link(mint(sim)["id"])
        return sim.transcript()

    assert run(1) != run(2)


_DETERMINISM_PROG = r"""
import sys
sys.path.insert(0, %r)
from gawaah.clock import VirtualClock
from gawaah.rzp_sim import RazorpaySim

sink = []
sim = RazorpaySim("whsec_gawaah_test_secret", VirtualClock(), seed=42, sink=sink.append)
for i in range(3):
    link = sim.create_payment_link(
        21450 + i, {"session_id": "sess_%%d" %% i}, reference_id="sess_%%d" %% i
    )
    sim.pay_link(link["id"], emit_captured=True)
sys.stdout.write(sim.transcript())
"""


def test_ids_are_deterministic_across_two_processes():
    """Two fresh interpreters, different PYTHONHASHSEED, byte-identical output.

    In-process determinism can hide a dependence on dict ordering or on a
    process-global RNG. This cannot."""
    prog = _DETERMINISM_PROG % (str(ROOT),)
    outs = []
    for hashseed in ("0", "1"):
        proc = subprocess.run(
            [sys.executable, "-c", prog],
            cwd=str(ROOT),
            capture_output=True,
            env={"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": hashseed},
            check=True,
        )
        outs.append(proc.stdout)
    assert outs[0] == outs[1]
    assert hashlib.sha256(outs[0]).hexdigest() == hashlib.sha256(outs[1]).hexdigest()
    assert outs[0].count(b"plink_") == 3
    # and it matches what this process produces
    sink: list[Delivery] = []
    sim = RazorpaySim(SECRET, VirtualClock(), seed=42, sink=sink.append)
    for i in range(3):
        link = sim.create_payment_link(
            21450 + i, {"session_id": f"sess_{i}"}, reference_id=f"sess_{i}"
        )
        sim.pay_link(link["id"], emit_captured=True)
    assert sim.transcript().encode("utf-8") == outs[0]


# -------------------------------------------------------- link lifecycle

def test_duplicate_reference_id_is_rejected_the_way_the_real_api_rejects_it():
    """PRD 11 claims a duplicate POST returns the existing link. It does not —
    the real API errors. A simulator kinder than production is a trap."""
    sim, _ = make_sim()
    mint(sim, 21450, "sess_dup")
    with pytest.raises(RazorpaySimError) as e:
        mint(sim, 21450, "sess_dup")
    assert "reference id" in e.value.description


def test_idempotent_create_returns_the_existing_link_not_a_second_charge():
    sim, _ = make_sim()
    first = mint(sim, 21450, "sess_idem")
    again = sim.create_payment_link(
        21450, {"session_id": "sess_idem"}, reference_id="sess_idem", idempotent=True
    )
    assert again["id"] == first["id"]
    assert again["short_url"] == first["short_url"]
    assert sim.fetch_payment_links()["count"] == 1


def test_a_paid_link_can_never_be_cancelled():
    """SIX 229 — cancellation works only from `issued`, so it can never void a
    completed payment. That is a safety property, not a limitation."""
    sim, _ = make_sim()
    link = mint(sim)
    sim.pay_link(link["id"])
    with pytest.raises(RazorpaySimError) as e:
        sim.cancel_payment_link(link["id"])
    assert "cannot be cancelled" in e.value.description
    assert sim.fetch_payment_link(link["id"])["status"] == "paid"


def test_a_created_link_cancels_and_is_then_unpayable():
    sim, sink = make_sim()
    link = mint(sim)
    assert sim.cancel_payment_link(link["id"])["status"] == "cancelled"
    with pytest.raises(RazorpaySimError):
        sim.pay_link(link["id"])
    assert sink == []


def test_paying_twice_is_refused():
    sim, sink = make_sim()
    link = mint(sim)
    sim.pay_link(link["id"])
    with pytest.raises(RazorpaySimError):
        sim.pay_link(link["id"])
    assert len(sink) == 1


def test_a_link_expires_and_stops_being_payable():
    sim, sink = make_sim(step_ms=600_000)        # 10 minutes per clock read
    link = mint(sim)
    assert link["status"] == "created"
    for _ in range(6):
        if sim.fetch_payment_link(link["id"])["status"] == "expired":
            break
    else:
        pytest.fail("link never expired past expire_by")
    with pytest.raises(RazorpaySimError):
        sim.pay_link(link["id"])
    assert sink == []


def test_fetch_payments_filters_by_link():
    sim, _ = make_sim()
    a = mint(sim, 21450, "sess_a")
    b = mint(sim, 500, "sess_b")
    sim.pay_link(a["id"])
    sim.pay_link(b["id"])

    all_pay = sim.fetch_payments()
    assert all_pay["entity"] == "collection" and all_pay["count"] == 2
    only_a = sim.fetch_payments(payment_link_id=a["id"])
    assert only_a["count"] == 1
    assert only_a["items"][0]["amount"] == 21450
    assert "_link_id" not in only_a["items"][0]       # internals never leak out


def test_fetch_payment_links_filters_by_reference_id():
    sim, _ = make_sim()
    mint(sim, 21450, "sess_a")
    mint(sim, 500, "sess_b")
    got = sim.fetch_payment_links(reference_id="sess_b")
    assert got["count"] == 1 and got["items"][0]["amount"] == 500


def test_fetching_an_unknown_link_errors():
    sim, _ = make_sim()
    with pytest.raises(RazorpaySimError):
        sim.fetch_payment_link("plink_doesnotexist")


def test_returned_entities_are_copies():
    sim, _ = make_sim()
    link = mint(sim)
    link["amount"] = 1
    link["notes"]["session_id"] = "tampered"
    fresh = sim.fetch_payment_link(link["id"])
    assert fresh["amount"] == 21450
    assert fresh["notes"]["session_id"] == "sess_abc123"


# ------------------------------------------------------------- invariants

def _code_string_literals(path: Path) -> list[str]:
    """Every string constant in a module that is NOT a docstring.

    Scanning raw source is wrong here: the module's own prose explains what it
    refuses to build, and prose is not a primitive. What matters is whether any
    *executable* literal could become part of a payload."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            if ast.get_docstring(node, clean=False) is not None:
                doc_nodes.add(id(node.body[0].value))
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) not in doc_nodes
    ]


def test_no_upi_payload_is_ever_constructed():
    """Invariant 6. Checked over the module's executable string literals AND
    over every byte the module actually emits."""
    for lit in _code_string_literals(ROOT / "gawaah" / "rzp_sim.py"):
        low = lit.lower()
        assert "upi:" not in low, f"UPI scheme in a code literal: {lit!r}"
        for param in ("pa=", "am=", "tn=", "mc=", "tr=", "cu="):
            assert param not in low, f"UPI payload parameter in a literal: {lit!r}"

    sim, sink = make_sim()
    link = mint(sim)
    sim.pay_link(link["id"], emit_captured=True)
    blob = link["short_url"].encode("utf-8") + b"".join(d.body for d in sink)
    for forbidden in (b"upi://", b"upi:/", b"?pa=", b"&pa=", b"pa=", b"&am=", b"&tn="):
        assert forbidden not in blob, f"forgery primitive {forbidden!r} in output"
    assert link["short_url"].startswith(SHORT_URL_PREFIX)


def test_rzp_sim_is_float_free():
    """rzp_sim is not on tools/lint_no_float.py's list, so run that tool's own
    AST visitor against it here rather than copying its rules."""
    spec = importlib.util.spec_from_file_location(
        "lint_no_float", ROOT / "tools" / "lint_no_float.py"
    )
    lint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lint)
    target = ROOT / "gawaah" / "rzp_sim.py"
    v = lint.V(str(target))
    v.visit(ast.parse(target.read_text(encoding="utf-8")))
    assert v.bad == [], f"float in the money path: {v.bad}"


def test_every_amount_on_the_wire_is_an_int():
    sim, sink = make_sim()
    sim.pay_link(mint(sim, 21450)["id"], emit_captured=True)
    for d in sink:
        body = d.json()
        for _, wrapper in body["payload"].items():
            e = wrapper["entity"]
            for key in ("amount", "amount_paid", "amount_due", "fee", "tax"):
                if key in e and e[key] is not None:
                    assert isinstance(e[key], int) and not isinstance(e[key], bool)
    assert b"21450.0" not in sink[0].body


def test_fee_and_tax_are_integer_paise():
    sim, _ = make_sim()
    link = mint(sim, 21450)
    pay = sim.pay_link(link["id"]).payment
    assert pay["fee"] == 21450 * 200 // 10000          # 2%
    assert pay["tax"] == pay["fee"] * 1800 // 10000    # 18% GST on the fee
    assert isinstance(pay["fee"], int) and isinstance(pay["tax"], int)


def test_every_emitted_body_is_labelled_simulated():
    """Honesty layer: no fixture from this module can be passed off as real."""
    sim, sink = make_sim()
    sim.pay_link(mint(sim)["id"], emit_captured=True)
    assert len(sink) == 2
    for d in sink:
        assert d.json()[SIM_BODY_MARKER] is True
        assert SIM_BODY_MARKER.encode("utf-8") in d.body


def test_the_secret_never_appears_in_a_repr_or_a_transcript():
    sim, sink = make_sim()
    sim.pay_link(mint(sim)["id"])
    assert SECRET not in repr(sim)
    assert "redacted" in repr(sim)
    assert SECRET not in sim.transcript()
    assert SECRET.encode("utf-8") not in sink[0].body
    cfg = RazorpayConfig(key_secret="rzp_secret_value", webhook_secret=SECRET)
    assert "rzp_secret_value" not in repr(cfg)
    assert SECRET not in repr(cfg)


def test_emitted_events_are_only_the_declared_ones():
    sim, sink = make_sim()
    sim.pay_link(mint(sim)["id"], emit_captured=True)
    assert {d.event for d in sink} <= set(EVENTS)


def test_iso_to_unix_never_returns_a_float():
    """Oracle is `calendar.timegm`, which is stdlib, integer-only, and shares no
    code with `iso_to_unix`. `datetime.timestamp()` is not used because it
    returns a float, which is the thing under test."""
    for iso in (
        "1970-01-01T00:00:00.000+00:00",
        "2026-08-29T00:00:00.000+00:00",
        "2026-08-29T00:00:01.500+00:00",     # must truncate, never round
        "2026-12-31T23:59:59.999+00:00",
    ):
        got = iso_to_unix(iso)
        expected = calendar.timegm(
            datetime.fromisoformat(iso).astimezone(timezone.utc).timetuple()
        )
        assert isinstance(got, int) and not isinstance(got, bool)
        assert got == expected, f"{iso}: {got} != {expected}"

    # truncation, stated explicitly
    assert iso_to_unix("2026-08-29T00:00:01.500+00:00") == iso_to_unix(
        "2026-08-29T00:00:01.000+00:00"
    )
    assert iso_to_unix("1970-01-01T00:00:00.000+00:00") == 0


def test_created_at_is_an_integer_unix_timestamp():
    sim, sink = make_sim()
    sim.pay_link(mint(sim)["id"])
    body = sink[0].json()
    assert isinstance(body["created_at"], int)
    assert body["created_at"] == iso_to_unix("2026-08-29T00:00:00.000+00:00")


# --------------------------------------------------------- sink contract

def test_delivery_is_the_documented_headers_body_mapping():
    sim, sink = make_sim()
    sim.pay_link(mint(sim)["id"])
    d = sink[0]
    as_dict = dict(d)
    assert set(as_dict) == {"headers", "body"}
    assert isinstance(as_dict["body"], bytes)
    assert as_dict["headers"]["X-Razorpay-Signature"] == d.signature
    assert as_dict["headers"]["X-Razorpay-Event-Id"] == d.event_id
    assert d["body"] is d.body
    with pytest.raises(KeyError):
        d["nope"]


def test_set_sink_can_be_attached_after_construction():
    sim = RazorpaySim(SECRET, VirtualClock())
    link = sim.create_payment_link(500, {"session_id": "s"})
    got: list[Delivery] = []
    sim.set_sink(got.append)
    sim.pay_link(link["id"])
    assert len(got) == 1


# ------------------------------------------------------------- the ledger

def test_each_emission_lands_one_verifiable_ledger_line(tmp_path):
    path = tmp_path / "audit.jsonl"
    led = Ledger(path)
    sim = RazorpaySim(SECRET, VirtualClock(), sink=None, ledger=led)
    link = sim.create_payment_link(21450, {"session_id": "s"})
    sim.pay_link(link["id"], emit_captured=True)

    ok, n, head, err = verify(path)
    assert ok and err is None
    assert n == 2
    rows = list(led.read())
    assert [r["event"] for r in rows] == ["payment.captured", "payment_link.paid"]
    assert all(r["simulated"] is True for r in rows)
    assert all(r["module"] == "rzp_sim" for r in rows)
    assert rows[-1]["hash"] == head
    assert SECRET not in path.read_text(encoding="utf-8")


# -------------------------------------------------------------- the config

def test_config_from_env_defaults_to_sim():
    cfg = RazorpayConfig.from_env({})
    assert cfg.mode == "sim"
    client = build_client(cfg, VirtualClock())
    assert isinstance(client, RazorpaySim)


def test_config_from_env_reads_real_key_names():
    cfg = RazorpayConfig.from_env(
        {
            "GAWAAH_RZP_MODE": "live",
            "RAZORPAY_KEY_ID": "rzp_live_abc",
            "RAZORPAY_KEY_SECRET": "shh",
            "RAZORPAY_WEBHOOK_SECRET": "whsec_real",
            "GAWAAH_RZP_SEED": "9",
        }
    )
    assert (cfg.mode, cfg.key_id, cfg.seed) == ("live", "rzp_live_abc", 9)
    assert cfg.webhook_secret == "whsec_real"


def test_live_mode_refuses_rather_than_pretending():
    cfg = RazorpayConfig(mode="live")
    with pytest.raises(ConfigError) as e:
        build_client(cfg, VirtualClock())
    assert "live_factory" in str(e.value)


def test_live_mode_uses_the_injected_factory_so_the_swap_is_config_only():
    sentinel = object()
    cfg = RazorpayConfig(mode="live")
    got = build_client(cfg, VirtualClock(), live_factory=lambda c: sentinel)
    assert got is sentinel


def test_unknown_mode_is_refused():
    with pytest.raises(ConfigError):
        build_client(RazorpayConfig(mode="banana"), VirtualClock())
    with pytest.raises(ConfigError):
        RazorpayConfig.from_env({"GAWAAH_RZP_SEED": "not-a-number"})


def test_empty_webhook_secret_is_refused():
    with pytest.raises(ConfigError):
        RazorpaySim("", VirtualClock())


# ------------------------------------------------------------- properties

@settings(max_examples=200, deadline=None)
@given(amount=st.integers(min_value=MIN_AMOUNT_PAISE, max_value=10_000_000))
def test_any_amount_round_trips_exactly_and_verifies(amount):
    sink: list[Delivery] = []
    sim = RazorpaySim(SECRET, VirtualClock(), sink=sink.append)
    link = sim.create_payment_link(amount, {"session_id": "sess_prop"})
    sim.pay_link(link["id"])
    d = sink[0]
    assert verify_webhook_signature(d.body, d.signature, SECRET)
    entity = d.json()["payload"]["payment_link"]["entity"]
    assert entity["amount"] == amount
    assert entity["amount_paid"] == amount
    # the rupee string a shopkeeper reads is exact, never a rounded float
    assert to_rupees_str(entity["amount"]) == to_rupees_str(amount)


@settings(max_examples=100, deadline=None)
@given(delta=st.integers(min_value=-50_000, max_value=50_000).filter(lambda d: d != 0))
def test_any_nonzero_wrong_amount_delta_is_caught_by_the_amount_gate(delta):
    sink: list[Delivery] = []
    sim = RazorpaySim(SECRET, VirtualClock(), sink=sink.append)
    link = sim.create_payment_link(
        100_000, {"session_id": "sess_prop"}, reference_id="sess_prop"
    )
    sim.set_mode("wrong_amount", wrong_amount_delta_paise=delta)
    sim.pay_link(link["id"])
    intent = {"session_id": "sess_prop", "amount_paise": 100_000, "state": "OPEN"}
    assert green(sink[0], SECRET, intent) == (False, "amount_mismatch")


@settings(max_examples=100, deadline=None)
@given(idx=st.integers(min_value=0), flip=st.integers(min_value=1, max_value=255))
def test_any_single_byte_flip_breaks_the_signature(idx, flip):
    sink: list[Delivery] = []
    sim = RazorpaySim(SECRET, VirtualClock(), sink=sink.append)
    link = sim.create_payment_link(21450, {"session_id": "s"})
    sim.pay_link(link["id"])
    d = sink[0]
    i = idx % len(d.body)
    tampered = bytearray(d.body)
    tampered[i] ^= flip
    assert verify_webhook_signature(bytes(tampered), d.signature, SECRET) is False


# ------------------------------------------------- integration with paisa
#
# `gawaah/webhook.py` (S4c, another module) is the real consumer of these
# fixtures. A simulator nobody can verify against is decoration, so prove the
# two agree. Guarded rather than assumed: that file is being written in
# parallel, and a skip here is honest where a hard import would be brittle.


def _green_predicate_or_skip():
    try:
        from gawaah import webhook as wh
    except Exception as exc:                       # pragma: no cover
        pytest.skip(f"gawaah.webhook not importable yet: {exc!r}")
    for name in ("GreenPredicate", "Intent", "GREEN_EVENTS"):
        if not hasattr(wh, name):                  # pragma: no cover
            pytest.skip(f"gawaah.webhook has no {name} yet")
    return wh


def test_a_simulated_webhook_turns_the_real_verifier_green():
    wh = _green_predicate_or_skip()
    sim, sink = make_sim()
    link = mint(sim, 21450, "sess_wire")
    sim.pay_link(link["id"])

    intent = wh.Intent(session_id="sess_wire", amount_paise=21450, state="OPEN")
    pred = wh.GreenPredicate(lambda s: intent if s == "sess_wire" else None)
    d = sink[0]
    v = pred.evaluate(d.body, d.signature, SECRET, header_event_id=d.event_id)
    assert v.green is True, f"verifier refused a clean webhook: {v.reason} {v.detail}"
    assert v.signature_valid is True
    assert v.amount_paise == 21450


def test_the_real_verifier_refuses_every_injected_failure():
    wh = _green_predicate_or_skip()
    intent = wh.Intent(session_id="sess_wire", amount_paise=21450, state="OPEN")

    def verdicts(mode):
        sim, sink = make_sim()
        link = mint(sim, 21450, "sess_wire")
        if mode:
            sim.set_mode(mode)
        sim.pay_link(link["id"])
        pred = wh.GreenPredicate(lambda s: intent if s == "sess_wire" else None)
        return [
            pred.evaluate(d.body, d.signature, SECRET, header_event_id=d.event_id)
            for d in sink
        ]

    # wrong amount: signature is fine, the amount gate is what stops it
    wrong = verdicts("wrong_amount")
    assert [v.green for v in wrong] == [False]
    assert wrong[0].signature_valid is True

    # replay: the first pays, the second must not pay twice
    dup = verdicts("duplicate_webhook")
    assert [v.green for v in dup] == [True, False]
    assert dup[1].signature_valid is True         # a valid signature, still refused

    # tampering: caught before the body is ever parsed
    sim, sink = make_sim()
    sim.pay_link(mint(sim, 21450, "sess_wire")["id"])
    pred = wh.GreenPredicate(lambda s: intent if s == "sess_wire" else None)
    tampered = bytearray(sink[0].body)
    tampered[len(tampered) // 2] ^= 0x01
    v = pred.evaluate(bytes(tampered), sink[0].signature, SECRET)
    assert v.green is False and v.signature_valid is False


def test_serialize_body_is_stable_and_compact():
    obj = {"b": 1, "a": 2, "z": {"y": 3}}
    out = serialize_body(obj)
    assert out == b'{"b":1,"a":2,"z":{"y":3}}'          # insertion order, no spaces
    assert out != canonical(obj)
    assert serialize_body(obj) == out
