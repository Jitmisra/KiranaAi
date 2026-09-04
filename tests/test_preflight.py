"""The preflight check must never be able to forge a payment.

INVARIANT 6, restated for this file: nothing in this repository constructs or
signs a payment callback. The preflight probes reachability by POSTing to the
webhook with a signature that CANNOT verify, and reads the resulting 400 as its
proof. That asymmetry is the whole safety argument, so it is pinned here rather
than left to whoever edits the script next.

A reachability probe that could turn a bill green would be a forgery primitive,
and a forgery primitive in this codebase is disqualifying.
"""
from __future__ import annotations

import ast
import pathlib

PREFLIGHT = pathlib.Path(__file__).resolve().parent.parent / "tools" / "preflight.py"
SOURCE = PREFLIGHT.read_text()
TREE = ast.parse(SOURCE)


def test_the_probe_signature_cannot_verify() -> None:
    """The probe's signature is a fixed non-hex string, so HMAC comparison fails."""
    assert "preflight-probe-cannot-verify" in SOURCE
    # A real Razorpay signature is 64 lowercase hex characters. This is not one,
    # and cannot become one: it is a literal, not a computation.
    probe = "preflight-probe-cannot-verify"
    assert len(probe) != 64
    assert not all(c in "0123456789abcdef" for c in probe)


def test_no_hmac_anywhere_in_the_preflight() -> None:
    """It may not import the tools for signing, let alone use them."""
    imported = set()
    for node in ast.walk(TREE):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for banned in ("hmac", "hashlib"):
        assert banned not in imported, f"preflight imports {banned}; it must not be able to sign"


def test_a_green_answer_to_the_probe_is_a_failure() -> None:
    """If the money service ever accepts the unsigned probe, the check must FAIL.

    This is the one outcome that would mean the counter can be robbed, so it is
    reported as a hard failure and never as a pass or a warning.
    """
    i = SOURCE.index('elif body.get("green"):')
    following = SOURCE[i:i + 400]
    assert "bad(" in following, "an accepted forgery must call bad(), not meh() or say()"
    assert "must never happen" in following


def test_it_does_not_write_to_the_audit_chain() -> None:
    """`gawaah/kernel.py` is the sole writer of results/audit.jsonl."""
    assert "audit.jsonl" not in SOURCE
    assert "open(" not in SOURCE.replace("urlopen(", "")


def test_it_never_prints_a_secret() -> None:
    """Secrets are reported as present or absent, never echoed."""
    assert "key_secret_configured" in SOURCE
    assert "webhook_secret_configured" in SOURCE
    # The key id is masked to its prefix wherever it is shown.
    assert "key[:13]" in SOURCE or "key[:9]" in SOURCE
    for leak in ("KEY_SECRET", "WEBHOOK_SECRET", "os.environ['RAZORPAY"):
        assert leak not in SOURCE
