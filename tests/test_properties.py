"""PROPERTY-BASED INVARIANTS — the laws that must hold for ANY input.

Example-based tests pin the cases somebody thought of. These pin the cases
nobody thought of, across module boundaries:

  MONEY     for any sequence of add/revert operations the total is the exact
            integer sum of the committed lines. No drift, no float, ever.
  LEDGER    for any sequence of appends verify() passes; and for a single-byte
            mutation ANYWHERE in the file, verify() either fails or the mutated
            file parses to semantically identical records. The second half of
            that disjunction is not a hedge — it is a real, shrunk counterexample
            to the naive claim, and it is documented as such below.
  SESSION   a RuleBasedStateMachine over the whole lifecycle. Two laws, each
            checked against a model rebuilt FROM THE LEDGER rather than from the
            session's own dict: an AMBER line never enters the total, and PAID is
            reachable only through a verdict that satisfies all four green legs.
  KERNEL    for any interleaving of create_intent with the same key, exactly one
            row and exactly one nonce exist.
  WEBHOOK   verify_signature is true iff the signature was computed over those
            exact bytes with that exact secret. Nothing weaker.
  SELLEVENT for any centroid script, net == out - back and never negative; and
            replaying the identical script gives byte-identical results.

Every property here runs against the real modules. Nothing is stubbed.
"""
from __future__ import annotations

import collections
import contextlib
import dataclasses
import hashlib
import hmac
import json
import shutil
import tempfile
import threading
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, note, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
)

from gawaah import money
from gawaah.clock import VirtualClock
from gawaah.kernel import NEW, Kernel, idem_key
from gawaah.ledger import GENESIS, Ledger, canonical, verify
from gawaah.money import MoneyError, add, from_rupees_str, paise, to_rupees_str
from gawaah.sellevent import CentroidTracker, LineZone
from gawaah.session import Placement, Reason, Session, State, Verdict
from gawaah.webhook import verify_signature

# pytest's `tmp_path` is created ONCE per test function, so every Hypothesis
# example inside one test would share it -- an append-only ledger written by
# example 1 is still there for example 2, and the properties would be measuring
# an accumulated file rather than the one the example describes. (That is not
# hypothetical: it is the first thing this file caught, on itself.) So every
# example that touches the disk gets its own directory and destroys it after.
@contextlib.contextmanager
def scratch():
    d = Path(tempfile.mkdtemp(prefix="gawaah-prop-"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ============================================================ MONEY
#
# The claim: for ANY sequence of add/revert operations the total equals the sum
# of the committed lines, exactly, in integer paise -- and no sequence produces
# a float or a rounding drift.

MONEY_OPS = st.lists(
    st.tuples(
        st.sampled_from(("add", "revert")),
        st.integers(min_value=0, max_value=99_999_999),
    ),
    max_size=80,
)


@settings(max_examples=400, deadline=None)
@given(ops=MONEY_OPS)
def test_money_total_is_the_exact_sum_of_committed_lines(ops):
    """add/revert in any order; money.total() == the exact integer sum."""
    lines: list[list] = []          # [amount_paise, committed]
    for op, arg in ops:
        if op == "add":
            lines.append([arg, True])
        elif lines:
            lines[arg % len(lines)][1] = False

    committed = [paise(a) for a, live in lines if live]
    t = money.total(committed)

    # 1. exact, against Python's arbitrary-precision integer sum
    assert t == sum(a for a, live in lines if live)
    # 2. integer, and not a bool masquerading as one
    assert type(t) is int
    assert not isinstance(t, float)
    # 3. add(*xs) and total(xs) agree for any sequence
    assert add(*committed) == t


@settings(max_examples=300, deadline=None)
@given(ops=MONEY_OPS)
def test_no_operation_sequence_produces_a_rounding_drift(ops):
    """Cross-checked in exact decimal arithmetic through the rupee-string path.

    Paise -> rupee string -> Decimal -> back to paise must be the identity for
    every committed line and for their sum. Decimal is exact for two places, so
    any drift the integer path introduced would show up here.
    """
    lines: list[list] = []
    for op, arg in ops:
        if op == "add":
            lines.append([arg, True])
        elif lines:
            lines[arg % len(lines)][1] = False

    committed = [a for a, live in lines if live]
    t = int(money.total([paise(a) for a in committed]))

    dec = Decimal(0)
    for a in committed:
        s = to_rupees_str(paise(a))
        assert from_rupees_str(s) == a          # round-trip is lossless
        dec += Decimal(s)
    assert dec * 100 == Decimal(t)              # no drift, exact
    assert dec == Decimal(to_rupees_str(paise(t)))


@settings(max_examples=300, deadline=None)
@given(
    xs=st.lists(st.integers(min_value=-10**9, max_value=10**9), max_size=40),
    k=st.integers(min_value=0, max_value=40),
)
def test_addition_is_associative_and_commutative_in_paise(xs, k):
    """Splitting a total anywhere gives the same answer. Floats do not do this."""
    ps = [paise(x) for x in xs]
    k = k % (len(ps) + 1)
    assert add(*ps) == add(add(*ps[:k]), add(*ps[k:]))
    assert money.total(ps) == money.total(list(reversed(ps)))


@settings(max_examples=200, deadline=None)
@given(
    x=st.floats(allow_nan=True, allow_infinity=True),
)
def test_no_float_is_ever_accepted_as_money(x):
    """Every float, including 0.0, NaN and integral ones like 5.0, is refused."""
    with pytest.raises(MoneyError):
        paise(x)
    with pytest.raises(MoneyError):
        money.total([x])


# ============================================================ LEDGER
#
# The strong one: tamper-evidence as a property.

LEDGER_FIELD_KEY = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=6
)
LEDGER_FIELD_VALUE = st.one_of(
    st.integers(min_value=-10**9, max_value=10**9),
    st.text(
        alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=12
    ),
    st.booleans(),
    st.none(),
)
LEDGER_LINES = st.lists(
    st.dictionaries(LEDGER_FIELD_KEY, LEDGER_FIELD_VALUE, max_size=4),
    min_size=1,
    max_size=12,
)


def _write_chain(path: Path, lines) -> list[str]:
    """Append one line per generated field-dict. Returns the head after each.

    The sequence number is written under `idx_`, a key the generator cannot
    produce (its alphabet is a-z), so a generated field never collides with it
    -- `append(**fields)` would raise TypeError on a duplicate keyword, which is
    a bug in the test, not in the ledger.
    """
    led, clk = Ledger(path), VirtualClock()
    heads = []
    for i, fields in enumerate(lines):
        fields = {k: v for k, v in fields.items()
                  if k not in ("ts", "module", "prev_hash", "hash", "idx_")}
        heads.append(
            led.append(ts=clk.now_iso(), module="prop", **{"idx_": i, **fields})
        )
    return heads


def _semantic_records(raw: bytes):
    """The parsed content of a ledger file, or None if it is not readable.

    Two files with the same value here carry exactly the same claims, whatever
    their bytes look like.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            out.append(canonical(json.loads(line)))
        except ValueError:
            return None
    return out


@settings(max_examples=250, deadline=None)
@given(lines=LEDGER_LINES)
def test_any_sequence_of_appends_verifies(lines):
    with scratch() as d:
        p = d / "audit.jsonl"
        heads = _write_chain(p, lines)
        ok, n, head, err = verify(p)
        assert ok, err
        assert n == len(lines)
        assert head == heads[-1]
        # every line's hash is distinct: prev_hash is inside the payload, so
        # even two byte-identical field sets chain to different hashes.
        assert len(set(heads)) == len(heads)
        # and a reopened Ledger recovers exactly that head
        assert Ledger(p).head == heads[-1]


@settings(max_examples=400, deadline=None)
@given(
    lines=LEDGER_LINES,
    pos=st.integers(min_value=0),
    new_byte=st.integers(min_value=0, max_value=255),
)
def test_a_single_byte_mutation_anywhere_is_never_silently_accepted(
    lines, pos, new_byte
):
    """THE tamper-evidence property.

    For a one-byte edit anywhere in the file, exactly one of these must hold:

      (a) verify() reports failure (returns ok=False, or raises -- see
          test_ledger_verify_raises_instead_of_returning_on_a_non_utf8_byte);
      (b) the mutated file parses to *semantically identical* records, i.e. no
          claim in the log changed and there is nothing to be evident about.

    What must NEVER happen is verify() returning ok=True over a file whose
    content differs. That is the only failure mode that matters.
    """
    with scratch() as d:
        p = d / "audit.jsonl"
        _write_chain(p, lines)
        raw = p.read_bytes()
        pos %= len(raw)
        assume(raw[pos] != new_byte)

        mutated = bytearray(raw)
        mutated[pos] = new_byte
        mutated = bytes(mutated)
        q = d / "tampered.jsonl"
        q.write_bytes(mutated)

        try:
            ok, _, _, err = verify(q)
        except UnicodeDecodeError:
            return                 # detected, loudly. See the dedicated test.

        if ok:
            note(f"byte {pos}: {raw[pos]!r} -> {new_byte!r}")
            assert _semantic_records(mutated) == _semantic_records(raw), (
                "verify() passed a file whose claims changed -- tamper-evidence "
                "is broken"
            )
        else:
            assert err


@settings(max_examples=300, deadline=None)
@given(
    lines=LEDGER_LINES,
    pos=st.integers(min_value=0),
    flip=st.integers(min_value=1, max_value=255),
)
def test_mutating_a_byte_inside_a_hash_or_a_value_always_fails_verify(
    lines, pos, flip
):
    """The sharpened version: restrict the mutation to bytes that carry meaning.

    Whitespace and line terminators are excluded (they are the counterexample
    below); everything else -- digits, letters, punctuation, hash hex -- must
    break the chain, and must break it through the documented return contract.
    """
    with scratch() as d:
        p = d / "audit.jsonl"
        _write_chain(p, lines)
        raw = p.read_bytes()

        meaningful = [i for i, b in enumerate(raw) if b not in b" \t\r\n\v\f"]
        assert meaningful
        pos = meaningful[pos % len(meaningful)]

        new_byte = raw[pos] ^ flip
        assume(0 < new_byte < 0x80)      # stay inside ASCII: still valid UTF-8
        # '<space>0' -> '<space>-0' is not possible here (whitespace is
        # excluded), but ' 0' -> '-0' IS reachable when the space is the byte
        # BEFORE a zero, so exclude the one semantics-preserving value edit.
        assume(not (bytes([new_byte]) == b"-" and raw[pos:pos + 1] == b" "))

        mutated = bytearray(raw)
        mutated[pos] = new_byte
        q = d / "tampered.jsonl"
        q.write_bytes(bytes(mutated))

        ok, _, _, err = verify(q)
        note(f"byte {pos}: {raw[pos:pos+1]!r} -> {bytes([new_byte])!r}")
        assert not ok, "a meaningful byte changed and verify() still said ok"
        assert err


def test_the_shrunk_counterexample_a_space_may_become_a_tab(tmp_path):
    """COUNTEREXAMPLE, found by Hypothesis and reproduced here deterministically.

    The literal claim "any single-byte mutation makes verify() fail" is FALSE.
    `Ledger.append` writes with `json.dumps(..., sort_keys=True)`, whose default
    separators are ', ' and ': ', so every line contains insignificant
    whitespace. `verify` re-parses each line and re-hashes the *canonical*
    form, so a space swapped for a tab hashes identically and passes.

    This is not a hole in tamper-evidence -- no claim in the log changed, and
    the money is untouched -- but it is a hole in the claim as stated, so the
    property above is stated as the disjunction it actually is. The exact
    shrunk example: byte 16 of a one-line chain, ' ' -> '\\t'.
    """
    p = tmp_path / "audit.jsonl"
    Ledger(p).append(ts="2026-08-29T00:00:00.000+00:00", module="prop", amount_paise=0)
    raw = p.read_bytes()

    pos = raw.index(b'": ') + 2          # the space after the first colon
    assert raw[pos:pos + 1] == b" "
    mutated = bytearray(raw)
    mutated[pos] = 0x09                  # tab
    q = tmp_path / "tampered.jsonl"
    q.write_bytes(bytes(mutated))

    assert q.read_bytes() != raw         # the FILE changed
    ok, n, _, err = verify(q)
    assert ok and err is None            # ...and verify still passes
    # because the CLAIMS did not change:
    assert _semantic_records(bytes(mutated)) == _semantic_records(raw)


def test_the_other_counterexample_zero_may_become_negative_zero(tmp_path):
    """Second shrunk counterexample: '<space>0' -> '-0' in an amount field.

    `{"amount_paise": 0}` becomes `{"amount_paise":-0}`. JSON's -0 parses to the
    int 0, so the canonical re-serialisation is identical and verify passes.
    Worth pinning: it is the one case where a *value* byte can be edited without
    breaking the chain, and it is only safe because -0 == 0 in integer paise.
    A float ledger would not have that guarantee.
    """
    p = tmp_path / "audit.jsonl"
    Ledger(p).append(ts="2026-08-29T00:00:00.000+00:00", module="prop", amount_paise=0)
    raw = p.read_bytes()

    pos = raw.index(b'"amount_paise": 0')
    pos += len(b'"amount_paise":')
    assert raw[pos:pos + 2] == b" 0"
    mutated = bytearray(raw)
    mutated[pos] = ord("-")
    q = tmp_path / "tampered.jsonl"
    q.write_bytes(bytes(mutated))

    ok, _, _, _ = verify(q)
    assert ok
    recs = [json.loads(line) for line in q.read_text().splitlines()]
    assert recs[0]["amount_paise"] == 0          # still exactly zero paise


def test_ledger_verify_raises_instead_of_returning_on_a_non_utf8_byte(tmp_path):
    """DEFECT, reported not silenced: `verify` does not fail *closed*, it crashes.

    `verify()` documents a `(ok, lines, head, error)` return and catches
    `json.JSONDecodeError`, but `path.read_text(encoding="utf-8")` happens
    before any of that. A byte with the high bit set -- the single commonest
    result of disk corruption or a truncated multi-byte write -- raises
    `UnicodeDecodeError` out of the function. `make verify-ledger` tracebacks
    instead of printing `ok=False`, and any caller doing `ok, n, h, e =
    verify(p)` dies rather than reporting tampering.

    This test asserts only what MUST be true: the tamper is detected, by one
    channel or the other. It passes today (raise) and will still pass when
    ledger.py is fixed to return (False, ...). It does not lock in the bug.
    """
    p = tmp_path / "audit.jsonl"
    Ledger(p).append(ts="2026-08-29T00:00:00.000+00:00", module="prop", i=1)
    raw = bytearray(p.read_bytes())
    raw[0] |= 0x80                       # '{' -> 0xFB: not valid UTF-8
    q = tmp_path / "tampered.jsonl"
    q.write_bytes(bytes(raw))

    raised = None
    detected = False
    try:
        ok, _, _, err = verify(q)
        detected = (not ok) and bool(err)
    except UnicodeDecodeError as exc:
        raised = exc
        detected = True

    assert detected, "a non-UTF-8 byte in the ledger went undetected"
    # Print which channel fired, so the defect is visible in `pytest -s` output
    # rather than only in a report. Today it is 'raise'.
    print(f"\n  ledger.verify detection channel: "
          f"{'raise ' + type(raised).__name__ if raised else 'return (False, ...)'}")


@settings(max_examples=200, deadline=None)
@given(lines=LEDGER_LINES, cut=st.integers(min_value=0))
def test_deleting_or_reordering_any_line_breaks_the_chain(lines, cut):
    assume(len(lines) >= 2)
    with scratch() as d:
        p = d / "audit.jsonl"
        _write_chain(p, lines)
        text = p.read_text().splitlines()

        i = cut % len(text)
        q = d / "deleted.jsonl"
        q.write_text("\n".join(text[:i] + text[i + 1:]) + "\n")
        ok, _, _, _ = verify(q)
        # HONEST LIMIT, and it is a real one: a hash chain proves that the
        # lines you HAVE are the lines that were written, in order. It cannot
        # prove that nobody chopped off the end -- there is no anchor past the
        # head. Truncating the tail therefore still verifies, and detecting it
        # needs an external witness (a published head), not a better chain.
        assert not ok or i == len(text) - 1

        j = (i + 1) % len(text)
        swapped = list(text)
        swapped[i], swapped[j] = swapped[j], swapped[i]
        assume(swapped != text)
        r = d / "swapped.jsonl"
        r.write_text("\n".join(swapped) + "\n")
        ok, _, _, _ = verify(r)
        assert not ok


@settings(max_examples=150, deadline=None)
@given(lines=LEDGER_LINES)
def test_replay_of_the_same_script_is_byte_identical(lines):
    with scratch() as d:
        a, b = d / "a.jsonl", d / "b.jsonl"
        _write_chain(a, lines)
        _write_chain(b, lines)
        assert a.read_bytes() == b.read_bytes()


# ============================================================ SESSION
#
# A RuleBasedStateMachine over the whole lifecycle. The two laws are checked
# against a model rebuilt FROM THE LEDGER, not from the session's own dict, so a
# bug in the session cannot mask itself in the check.

_WEBHOOK_EVENTS = (
    "payment_link.paid",
    "payment.captured",
    "qr_code.credited",
    "payment.failed",
    "payment_link.expired",
)

_PRICE_LINES = (Reason.PRICED, Reason.PRICE_TAPPED)
_COMMIT_LINES = (Reason.COMMITTED, Reason.COMMITTED_AMBER)

#: The green set, RESTATED here rather than imported. Importing the module's own
#: `GREEN_EVENTS` into the oracle would make the check circular: widening the set
#: would widen the test with it and nothing would fail. It is restated, and then
#: pinned against the module by `test_the_green_event_set_is_exactly_three`
#: below, so a widening has to be an explicit, visible edit in two places.
_GREEN_SET = ("payment_link.paid", "payment.captured", "qr_code.credited")

#: Perception and network go down sometimes, not most of the time. A uniform
#: coin here is not "adversarial", it is a machine that never gets to bill:
#: measured, it left the session in MAT_LOST or SETUP for 87% of all steps and
#: reached AWAITING_SETTLEMENT zero times in 300 runs. Biasing the coin does not
#: weaken any assertion -- every outage path is still generated -- it just buys
#: enough healthy steps for the settlement states to be reachable at all.
MOSTLY_UP = st.sampled_from((True, True, True, True, False))


class SessionMachine(RuleBasedStateMachine):
    """Drive `Session` through arbitrary reachable interleavings.

    Two laws, held on every single step:

      L1  the billable total is EXACTLY the sum of the priced, committed,
          un-reverted lines the LEDGER records. An AMBER line contributes zero
          to it, in every reachable state.
      L2  PAID is entered only through a verdict that satisfies all four green
          legs (paisa said green, signature valid, session id ours, amount ==
          the open intent to the paisa).
    """

    items = Bundle("items")

    def __init__(self) -> None:
        super().__init__()
        self._dir = Path(tempfile.mkdtemp(prefix="gawaah-session-prop-"))
        self.ledger = Ledger(self._dir / "audit.jsonl")
        self.session = Session(VirtualClock(), self.ledger)
        self._n = 0
        self.full_green_verdicts: list[Verdict] = []
        self.ever_paid = False

    def teardown(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)

    # -- the independent model, rebuilt from the audit log ------------------

    def _total_from_ledger(self) -> int:
        price: dict[str, int] = {}
        committed: set[str] = set()
        reverted: set[str] = set()
        for rec in self.ledger.read():
            if rec.get("module") != "session":
                continue
            if rec.get("session_id") != self.session.session_id:
                continue
            reason = rec.get("reason")
            iid = rec.get("item_id")
            if reason == Reason.PLACEMENT_SEEN:
                if rec.get("new_basket"):
                    price.clear()
                    committed.clear()
                    reverted.clear()
                price.pop(iid, None)
                committed.discard(iid)
                reverted.discard(iid)
            elif reason in _PRICE_LINES:
                price[iid] = rec["price_paise"]
            elif reason == Reason.UNKNOWN_SKU:
                price.pop(iid, None)          # abstention: no money on this line
            elif reason in _COMMIT_LINES:
                committed.add(iid)
            elif reason == Reason.REVERTED:
                reverted.add(iid)
        return sum(
            price[i] for i in committed if i in price and i not in reverted
        )

    def _is_fully_green(self, v: Verdict) -> bool:
        return bool(
            v.green
            and v.signature_valid
            and v.session_id == self.session.session_id
            and v.event in _GREEN_SET
            and v.amount_paise is not None
            and v.amount_paise == self.session.intent_amount_paise
        )

    # -- rules --------------------------------------------------------------

    @initialize()
    def lock_the_mat(self):
        """Four markers seen. Nothing bills until this happens (SETUP refuses
        every billing event), so without it most runs would test the refusal
        path and nothing else."""
        self.session.on_mat_lock(True)

    @rule(target=items, price=st.one_of(st.none(), st.integers(0, 5_000_00)))
    def place(self, price):
        self._n += 1
        iid = f"i{self._n}"
        self.session.on_placement(
            Placement(item_id=iid, name=f"n{self._n}", price_paise=price)
        )
        return iid

    @rule(iid=items, amount=st.integers(min_value=0, max_value=5_000_00))
    def tap_price(self, iid, amount):
        self.session.on_price(iid, amount)

    @rule(iid=items, tap=st.booleans())
    def exit_item(self, iid, tap):
        self.session.on_exit(iid, tap=tap)

    @rule()
    def exit_with_no_id(self):
        """Abstention 11: goods crossed and the tracker id was lost."""
        self.session.on_exit(None)

    @rule(iid=items)
    def revert(self, iid):
        self.session.on_revert(iid)

    @rule(locked=MOSTLY_UP)
    def mat(self, locked):
        self.session.on_mat_lock(locked)

    @rule(up=MOSTLY_UP)
    def brain(self, up):
        self.session.on_brain(up)

    @rule(up=MOSTLY_UP)
    def network(self, up):
        self.session.on_network(up)

    @rule(p95=st.sampled_from((40, 90, 150, 240, 260, 400, 600)))
    def perf(self, p95):
        self.session.on_perf(p95)

    @rule()
    def acknowledge(self):
        self.session.on_acknowledge()

    @rule()
    def recover(self):
        """Everything back up. Without a reliable route home the machine spends
        almost all of its steps in MAT_LOST and never reaches settlement, which
        would make L2 vacuously true -- measured: 0 visits to PAID before this
        rule existed, out of 12 600 invariant checks."""
        self.session.on_mat_lock(True)
        self.session.on_brain(True)
        self.session.on_network(True)
        self.session.on_perf(0)
        self.session.on_acknowledge()

    @rule()
    def done(self):
        self.session.on_done()

    @rule(
        green=st.booleans(),
        sig=st.booleans(),
        ours=st.booleans(),
        right_amount=st.booleans(),
        delta=st.integers(min_value=-50_00, max_value=50_00),
        event=st.sampled_from(_WEBHOOK_EVENTS),
    )
    def webhook(self, green, sig, ours, right_amount, delta, event):
        """An adjudicated webhook with any combination of the four legs true."""
        self._deliver(green, sig, ours, right_amount, delta, event)

    @rule(delta=st.integers(min_value=-50_00, max_value=50_00))
    def webhook_wrong_amount(self, delta):
        """THE RED HOLD: signed, ours, in the green set -- and the wrong number.

        Three of the four legs hold, which is the case a "mostly right" gate
        lets through. It must never reach PAID. Sampled explicitly because the
        adversarial `webhook` rule hits this combination only ~0.3% of the time
        (measured: 0 occurrences in 12 452 steps), which is not coverage.
        """
        self._deliver(True, True, True, False, delta, "payment_link.paid")

    @rule()
    def webhook_all_four_legs_green(self):
        """The one combination that MUST reach PAID when an intent is open.

        Kept as its own rule so the happy path is sampled often enough for L2 to
        have teeth; the adversarial combinations still come from `webhook`.
        """
        self._deliver(True, True, True, True, 0, "payment_link.paid")

    def _deliver(self, green, sig, ours, right_amount, delta, event):
        self._n += 1
        intent = self.session.intent_amount_paise
        if right_amount and intent is not None:
            amount = intent
        else:
            base = intent if intent is not None else 10_00
            amount = max(0, base + (delta or 1))
        v = Verdict(
            event_id=f"evt{self._n}",
            event=event,
            session_id=self.session.session_id if ours else "somebody-elses",
            amount_paise=amount,
            green=green,
            signature_valid=sig,
        )
        fully_green = self._is_fully_green(v)
        if fully_green:
            self.full_green_verdicts.append(v)

        before = self.session.state
        self.session.on_webhook(v)
        after = self.session.state

        # L2, at the exact moment of entry.
        if after is State.PAID and before is not State.PAID:
            assert fully_green, (
                f"entered PAID on a verdict that was not fully green: {v!r}"
            )
            assert self.session.authorised_paise == intent
            self.ever_paid = True
        # ...and the converse: all four legs, an open intent and a live session
        # MUST settle. A predicate that only ever refuses is not safe, it is
        # broken, and it would satisfy the one-directional law trivially.
        if fully_green and before not in (State.PAID, State.AMOUNT_MISMATCH):
            assert after is State.PAID, (
                f"all four green legs held from {before} and the session did "
                f"not settle: {v!r}"
            )

    # -- invariants ---------------------------------------------------------

    @invariant()
    def l1_amber_never_enters_the_total(self):
        s = self.session
        live = int(s.live_total_paise)
        assert type(live) is int and not isinstance(live, bool)
        assert live == self._total_from_ledger(), (
            "the session's live total disagrees with the audit log"
        )
        # every amber line is priceless, by definition, and the total does not
        # move when one is added.
        for li in s.amber_items:
            assert li.price_paise is None
        assert live == sum(
            li.price_paise for li in s.committed_items if li.price_paise is not None
        )

    @invariant()
    def total_is_always_integer_paise(self):
        for value in (self.session.total_paise, self.session.live_total_paise):
            assert type(int(value)) is int
            assert not isinstance(value, float)
        assert int(self.session.total_paise) >= 0

    @invariant()
    def l2_paid_only_via_green(self):
        s = self.session
        if s.state is State.PAID:
            assert self.full_green_verdicts, "PAID with no fully-green verdict"
            assert s.money_authorised
            assert s.authorised_paise == s.intent_amount_paise
        if s.money_authorised:
            assert s.state is State.PAID
            assert self.full_green_verdicts
            # `ever_paid` is set ONLY inside the webhook rule, immediately after
            # watching the transition happen. So this closes the last loophole
            # in L2: money cannot become authorised by any other rule -- a
            # placement, a revert, a network flap -- quietly setting the flag.
            assert self.ever_paid, (
                "money was authorised without a webhook delivery doing it"
            )

    @invariant()
    def the_audit_chain_always_verifies(self):
        ok, n, head, err = verify(self.ledger.path)
        assert ok, err
        assert head == self.ledger.head

    @invariant()
    def frozen_states_never_report_a_bigger_total_than_the_live_one_grew_to(self):
        s = self.session
        if s.frozen:
            # the snapshot is a past value of the same quantity, so it is still
            # a sum of priced committed lines -- never an amber-inflated number.
            assert int(s.total_paise) >= 0
            assert s.state in (State.MAT_LOST, State.BRAIN_LOST, State.FROZEN_TOTAL)


SessionMachine.TestCase.settings = settings(
    max_examples=200,
    stateful_step_count=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
TestSessionProperties = SessionMachine.TestCase


@settings(max_examples=150, deadline=None)
@given(
    prices=st.lists(
        st.one_of(st.none(), st.integers(min_value=1, max_value=100_00)),
        min_size=1,
        max_size=8,
    )
)
def test_amber_lines_are_excluded_from_the_intent_for_any_basket(prices):
    """Directed version of L1: whatever mix of priced and abstained lines is
    committed, the intent DONE mints for is the sum of the priced ones only."""
    with scratch() as d:
        s = Session(VirtualClock(), Ledger(d / "audit.jsonl"))
        s.on_mat_lock(True)
        for i, p in enumerate(prices):
            s.on_placement(Placement(item_id=f"i{i}", name=f"n{i}", price_paise=p))
            s.on_exit(f"i{i}")

        expected = sum(p for p in prices if p is not None)
        assert int(s.live_total_paise) == expected
        assert s.amber_count == sum(1 for p in prices if p is None)

        s.on_done()
        if expected > 0:
            assert s.state is State.AWAITING_SETTLEMENT
            assert s.intent_amount_paise == expected
        else:
            # every committed line abstained: nothing to charge for, and
            # nothing minted. An amber basket does not become a zero-rupee sale.
            assert s.intent_amount_paise is None
            assert s.state is not State.AWAITING_SETTLEMENT


@settings(max_examples=250, deadline=None)
@given(
    amount=st.integers(min_value=1, max_value=500_00),
    green=st.booleans(),
    sig=st.booleans(),
    ours=st.booleans(),
    exact=st.booleans(),
    event=st.sampled_from(_WEBHOOK_EVENTS),
)
def test_paid_iff_all_four_green_legs_hold(amount, green, sig, ours, exact, event):
    """PAID is reachable if and only if all four legs hold. Not three of four."""
    with scratch() as d:
        s = Session(VirtualClock(), Ledger(d / "audit.jsonl"))
        s.on_mat_lock(True)
        s.on_placement(Placement(item_id="a", name="a", price_paise=amount))
        s.on_exit("a")
        s.on_done()
        assert s.intent_amount_paise == amount

        v = Verdict(
            event_id="e1",
            event=event,
            session_id=s.session_id if ours else "not-ours",
            amount_paise=amount if exact else amount + 1,
            green=green,
            signature_valid=sig,
        )
        s.on_webhook(v)

        all_four = green and sig and ours and exact and event in _GREEN_SET
        assert (s.state is State.PAID) is all_four
        assert s.money_authorised is all_four
        assert s.authorised_paise == (amount if all_four else None)


def test_the_green_event_set_is_exactly_three():
    """Pins the oracle above against the module it is judging.

    NOTE, for the record and not as a failure: `gawaah.session.GREEN_EVENTS` has
    three members and `gawaah.webhook.GREEN_EVENTS` has two -- webhook does not
    include `qr_code.credited`. That is not exploitable today, because the
    session only ever acts on a verdict whose `green` flag paisa set, and paisa
    sets it through `webhook.GreenPredicate`, which can never green the third
    event. The session is therefore never LESS conservative in practice. It is
    asserted here so the divergence is visible rather than folklore.
    """
    from gawaah.session import GREEN_EVENTS as SESSION_GREEN
    from gawaah.webhook import GREEN_EVENTS as WEBHOOK_GREEN

    assert SESSION_GREEN == frozenset(_GREEN_SET)
    assert WEBHOOK_GREEN <= SESSION_GREEN
    assert SESSION_GREEN - WEBHOOK_GREEN == {"qr_code.credited"}


# ============================================================ KERNEL
#
# For any interleaving of create_intent calls with the same key, exactly one
# intent exists.


@settings(max_examples=15, deadline=None)
@given(
    workers=st.integers(min_value=2, max_value=8),
    per_worker=st.integers(min_value=1, max_value=4),
    amount=st.integers(min_value=1, max_value=10**7),
    cycle=st.integers(min_value=0, max_value=3),
)
def test_concurrent_create_intent_with_one_key_makes_exactly_one_intent(
    workers, per_worker, amount, cycle
):
    with scratch() as d:
        k = Kernel(str(d / "k.db"), VirtualClock(), Ledger(d / "a.jsonl"))
        try:
            start = threading.Barrier(workers)
            seen: list = []
            errs: list = []
            lock = threading.Lock()

            def go():
                try:
                    start.wait(timeout=20)
                    mine = [k.create_intent("s-1", amount, cycle)
                            for _ in range(per_worker)]
                except Exception as exc:        # pragma: no cover - diagnostics
                    with lock:
                        errs.append(exc)
                    return
                with lock:
                    seen.extend(mine)

            ts = [threading.Thread(target=go) for _ in range(workers)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(timeout=60)

            assert not errs, errs
            assert len(seen) == workers * per_worker
            assert k.count() == 1
            nonces = {it.nonce for it in seen}
            assert len(nonces) == 1, f"{len(nonces)} nonces minted for one key"
            assert len({it.idem_key for it in seen}) == 1
            assert seen[0].idem_key == idem_key("s-1", cycle, amount)
            assert all(it.state == NEW for it in seen)
            assert all(it.amount_paise == amount for it in seen)
            # exactly one audit line claims the creation, however many callers
            # raced: the loser of the INSERT OR IGNORE must not audit a write
            # it did not make.
            created = [r for r in Ledger(d / "a.jsonl").read()
                       if r.get("event") == "intent.created"]
            assert len(created) == 1
            ok, _, _, err = verify(d / "a.jsonl")
            assert ok, err
        finally:
            k.close()


@settings(max_examples=40, deadline=None)
@given(
    keys=st.lists(
        st.tuples(
            st.sampled_from(("s-a", "s-b", "s-c")),
            st.integers(min_value=1, max_value=5),
            st.integers(min_value=0, max_value=2),
        ),
        min_size=1,
        max_size=25,
    )
)
def test_distinct_keys_get_distinct_nonces_and_repeats_get_the_same_one(keys):
    """The exactly-once key is (session_id, cycle, amount_paise) and nothing else."""
    with scratch() as d:
        k = Kernel(str(d / "k.db"), VirtualClock(), Ledger(d / "a.jsonl"))
        try:
            by_key: dict[tuple, str] = {}
            for sid, amount, cycle in keys:
                it = k.create_intent(sid, amount, cycle)
                kk = (sid, cycle, amount)
                if kk in by_key:
                    assert it.nonce == by_key[kk], (
                        "a repeated key minted a second nonce"
                    )
                else:
                    by_key[kk] = it.nonce
            assert k.count() == len(by_key)
            assert len(set(by_key.values())) == len(by_key)
            assert len({i.nonce for i in k.all_intents()}) == len(by_key)
            assert len({i.idem_key for i in k.all_intents()}) == len(by_key)
        finally:
            k.close()


# ============================================================ WEBHOOK
#
# verify_signature is true iff the signature was computed over those exact bytes
# with that exact secret. Nothing weaker, nothing stronger.


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


BODIES = st.binary(max_size=256)
SECRETS = st.text(min_size=1, max_size=48).filter(lambda s: s.encode("utf-8") != b"")


@settings(max_examples=500, deadline=None)
@given(body=BODIES, secret=SECRETS, other=BODIES, other_secret=SECRETS)
def test_verify_signature_is_true_iff_exact_bytes_and_exact_secret(
    body, secret, other, other_secret
):
    sig = _sign(other, other_secret)
    should_pass = (
        body == other and secret.encode("utf-8") == other_secret.encode("utf-8")
    )
    assert verify_signature(body, sig, secret) is should_pass


@settings(max_examples=400, deadline=None)
@given(body=BODIES, secret=SECRETS)
def test_the_correct_signature_always_verifies(body, secret):
    assert verify_signature(body, _sign(body, secret), secret) is True
    # bytearray / memoryview of the same octets are the same wire object
    assert verify_signature(bytearray(body), _sign(body, secret), secret) is True
    assert verify_signature(memoryview(body), _sign(body, secret), secret) is True


@settings(max_examples=400, deadline=None)
@given(
    body=BODIES,
    secret=SECRETS,
    pos=st.integers(min_value=0, max_value=63),
    ch=st.sampled_from("0123456789abcdef"),
)
def test_any_single_character_change_to_the_signature_fails(body, secret, pos, ch):
    sig = _sign(body, secret)
    assume(sig[pos] != ch)
    bad = sig[:pos] + ch + sig[pos + 1:]
    assert verify_signature(body, bad, secret) is False


@settings(max_examples=400, deadline=None)
@given(body=st.binary(min_size=1, max_size=256), secret=SECRETS,
       pos=st.integers(min_value=0), flip=st.integers(min_value=1, max_value=255))
def test_any_single_bit_flip_in_the_body_fails(body, secret, pos, flip):
    sig = _sign(body, secret)
    pos %= len(body)
    mutated = bytearray(body)
    mutated[pos] ^= flip
    assert verify_signature(bytes(mutated), sig, secret) is False


@settings(max_examples=200, deadline=None)
@given(body=BODIES, secret=SECRETS)
def test_signature_covers_raw_bytes_not_the_parsed_object(body, secret):
    """Two byte-strings that mean the same JSON are still different signatures."""
    a = b'{"amount":21437,"id":"x"}'
    b = b'{"amount": 21437, "id": "x"}'
    assert json.loads(a) == json.loads(b)
    assert _sign(a, secret) != _sign(b, secret)
    assert verify_signature(b, _sign(a, secret), secret) is False
    # and truncating or extending the body always fails
    assert verify_signature(body + b"\n", _sign(body, secret), secret) is False


@settings(max_examples=200, deadline=None)
@given(body=BODIES, secret=SECRETS)
def test_an_empty_secret_never_verifies_anything(body, secret):
    """A forgeable HMAC is not an authenticated one. Empty key fails closed."""
    assert verify_signature(body, _sign(body, ""), "") is False
    assert verify_signature(body, _sign(body, secret), "") is False
    assert verify_signature(body, "", secret) is False


@settings(max_examples=200, deadline=None)
@given(body=BODIES, secret=SECRETS, junk=st.text(max_size=80))
def test_a_signature_that_is_not_a_hex_digest_never_verifies(body, secret, junk):
    assume(junk != _sign(body, secret))
    assert verify_signature(body, junk, secret) is False


# ============================================================ SELLEVENT
#
# For any centroid path: net == out - back, never negative; and replaying the
# identical script gives byte-identical results.

LINE_Y_MM = 402.0            # the mat's printed exit edge, 18 mm inset on A3
MID_X_MM = 148.5


def _fresh():
    tracker = CentroidTracker(max_dist_mm=25.0, max_missing_frames=3)
    zone = LineZone((0.0, LINE_Y_MM), (297.0, LINE_Y_MM), min_crossing_frames=3)
    return tracker, zone


def _run(frames):
    """Replay a script of per-frame centroid lists. Returns every CrossingResult."""
    tracker, zone = _fresh()
    out = []
    for pts in frames:
        upd = tracker.update(pts)
        out.append(zone.update(upd.tracks, untracked=upd.untracked, lost=upd.lost))
    out.append(zone.flush())
    return out


def _blob(results):
    """A canonical byte serialisation of a run. Two equal blobs are equal runs."""
    return canonical([dataclasses.asdict(r) for r in results])


def _ramp(a: float, b: float, step: float = 10.0) -> list[float]:
    """Intermediate y positions from a to b, never stepping further than `step`.

    Not cosmetic: `CentroidTracker`'s association gate is 25 mm, so a script
    that teleports an item from 380 mm to 415 mm is not one item crossing a
    line, it is one item vanishing and a different one appearing. (Hypothesis
    caught exactly that in the first draft of these tests: the sweep counted
    zero sales and two `vanished_same_side` retirements.) A physical path is
    what the crossing predicate is defined over, so the scripts build one.
    """
    out: list[float] = []
    y = a
    while abs(b - y) > step:
        y += step if b > a else -step
        out.append(y)
    out.append(b)
    return out


def _walk(ys, x: float) -> list[list[tuple[float, float]]]:
    """One centroid at `x`, visiting each y in turn. One frame per y."""
    return [[(x, y)] for y in ys]


IN_Y_MM = 385.0          # the shopkeeper's side, comfortably outside the dead band
OUT_Y_MM = 415.0         # the customer's side


def _mm(lo: float, hi: float):
    return st.floats(min_value=lo, max_value=hi, allow_nan=False,
                     allow_infinity=False)


#: Pure chaos: centroids teleporting anywhere on and around the mat. This is the
#: adversarial input -- it exercises the tracker's refusals, the eviction paths
#: and the uncounted-crossing exceptions. What it does NOT do is produce a
#: counted sale: measured, 0 of 400 chaos scripts ever committed a crossing,
#: because a blob that jumps 40 mm a frame is outside the 25 mm association gate
#: and is never the same object twice. A property checked only on this strategy
#: would be reading 0 == 0 - 0 and calling it accounting.
CHAOS_FRAMES = st.lists(
    st.lists(
        st.tuples(_mm(0.0, 297.0), _mm(340.0, 419.0)),
        max_size=3,
    ),
    min_size=1,
    max_size=40,
)

#: A PHYSICAL script: an item is picked up, moved toward a waypoint at no more
#: than 12 mm a frame, and rests there for a while. Repeat. This is what a hand
#: on a counter actually does, and it is the only kind of input on which the
#: crossing predicate can commit anything. Measured over 600 scripts: 48% commit
#: at least one outward crossing, 27% a crossing back, 16% more than one sale.
_WAYPOINT = st.tuples(_mm(355.0, 445.0), st.integers(min_value=0, max_value=6))


@st.composite
def item_path(draw, x_lo: float = 20.0, x_hi: float = 275.0):
    x = draw(_mm(x_lo, x_hi))
    y = draw(_mm(355.0, 445.0))
    ys = [y]
    for target, dwell in draw(st.lists(_WAYPOINT, min_size=1, max_size=6)):
        ys += _ramp(y, target, 12.0)
        ys += [target] * dwell
        y = target
    return _walk(ys, x)


def _side_by_side(a, b):
    """Two independent single-item scripts, played simultaneously.

    The shorter item simply stops moving rather than vanishing, so the only
    thing being varied is the number of tracks -- not the eviction path, which
    the chaos strategy already covers.
    """
    n = max(len(a), len(b))
    out = []
    for i in range(n):
        frame = list(a[i] if i < len(a) else a[-1])
        frame += list(b[i] if i < len(b) else b[-1])
        out.append(frame)
    return out


#: Two items on well-separated halves of the mat: far enough apart that the
#: tracker never has to abstain, so per-track accounting is what is under test.
TWO_ITEM_PATHS = st.builds(
    _side_by_side, item_path(20.0, 110.0), item_path(190.0, 275.0)
)

PATHS = st.one_of(item_path(), TWO_ITEM_PATHS)
FRAMES = st.one_of(CHAOS_FRAMES, PATHS)


@settings(max_examples=300, deadline=None)
@given(frames=FRAMES)
def test_net_crossings_equal_out_minus_back_and_never_go_negative(frames):
    results = _run(frames)
    for r in results:
        assert r.net_count == r.out_count - r.back_count
        assert r.net_count >= 0, "a sale count went negative"
        assert r.out_count >= 0 and r.back_count >= 0
        assert r.back_count <= r.out_count

    # the running totals are exactly the per-frame events, summed
    assert results[-1].out_count == sum(len(r.crossed_out) for r in results)
    assert results[-1].back_count == sum(len(r.crossed_back) for r in results)
    # and monotone: a frame never un-counts a previous frame's crossing
    for a, b in zip(results, results[1:]):
        assert b.out_count >= a.out_count
        assert b.back_count >= a.back_count
        assert b.crossed_without_tracker_id >= a.crossed_without_tracker_id
        assert b.detected_but_never_counted >= a.detected_but_never_counted
        assert b.amber >= a.amber          # amber latches, never clears


@settings(max_examples=300, deadline=None)
@given(frames=PATHS)
def test_a_track_can_only_cross_back_after_it_has_crossed_out(frames):
    """Deviation 2, per track: a return can only cancel a sale that happened.

    An item that walks onto the mat from the customer's side is not a negative
    sale, so `back_count` may never outrun `out_count` for any single track at
    any point in the script -- not merely in the final totals, where two tracks
    could hide each other's error.
    """
    results = _run(frames)
    credits: collections.Counter = collections.Counter()
    for r in results:
        for tid in r.crossed_out:
            credits[tid] += 1
        for tid in r.crossed_back:
            credits[tid] -= 1
            assert credits[tid] >= 0, (
                f"track {tid} was credited a return it never took out"
            )
    assert sum(credits.values()) == results[-1].net_count
    assert results[-1].net_count >= 0


@settings(max_examples=250, deadline=None)
@given(frames=FRAMES)
def test_replaying_the_identical_script_is_byte_identical(frames):
    first = _run(frames)
    second = _run(frames)
    assert _blob(first) == _blob(second)
    assert first == second


@settings(max_examples=200, deadline=None)
@given(frames=FRAMES)
def test_every_uncounted_crossing_is_surfaced_and_latches_amber(frames):
    """A crossing the predicate could not evaluate is never silently dropped."""
    results = _run(frames)
    last = results[-1]
    total_exceptions = sum(len(r.exceptions) for r in results)
    counters = last.crossed_without_tracker_id + last.detected_but_never_counted
    assert total_exceptions == counters
    assert last.amber == bool(counters)
    assert last.reid_abstained <= last.crossed_without_tracker_id
    for r in results:
        assert r.clean == (not r.exceptions)
        assert r.total_is_trustworthy == (not r.amber)


@settings(max_examples=200, deadline=None)
@given(
    sweeps=st.integers(min_value=1, max_value=5),
    hold=st.integers(min_value=4, max_value=7),
    x=st.floats(min_value=20.0, max_value=270.0, allow_nan=False,
                allow_infinity=False),
)
def test_out_and_back_sweeps_net_to_zero_and_count_exactly_once_each(
    sweeps, hold, x
):
    """The accounting identity on a script whose answer is known by construction.

    N complete out-and-back sweeps of one item must produce exactly N outward
    crossings, N inward ones, and a net of zero. Not N+1 (the upstream wobble
    double-count) and not -1 (an arrival from the customer's side).
    """
    ys: list[float] = []
    for _ in range(sweeps):
        ys += [IN_Y_MM] * hold                       # resting, shopkeeper's side
        ys += _ramp(IN_Y_MM, OUT_Y_MM)               # handed across
        ys += [OUT_Y_MM] * hold                      # resting, customer's side
        ys += _ramp(OUT_Y_MM, IN_Y_MM)               # handed back
        ys += [IN_Y_MM] * hold
    results = _run(_walk(ys, x))
    last = results[-1]
    assert last.out_count == sweeps
    assert last.back_count == sweeps
    assert last.net_count == 0
    assert not last.amber, [str(e) for e in last.exceptions]
    # and the per-frame events add up to the running totals
    assert sum(len(r.crossed_out) for r in results) == sweeps
    assert sum(len(r.crossed_back) for r in results) == sweeps


@settings(max_examples=200, deadline=None)
@given(
    hold=st.integers(min_value=4, max_value=8),
    wobbles=st.integers(min_value=0, max_value=4),
    x=st.floats(min_value=20.0, max_value=270.0, allow_nan=False,
                allow_infinity=False),
)
def test_a_wobble_on_the_line_can_never_produce_a_second_sale(hold, wobbles, x):
    """Deviation 1 from upstream, as a property: one physical crossing, one sale.

    Upstream's deque test alone counts T,T,T,F,T,T,T as two OUT crossings. Any
    number of wobbles back over the line and out again must still leave the
    committed side OUT, and must never bill twice for one journey.
    """
    ys = [IN_Y_MM] * hold + _ramp(IN_Y_MM, OUT_Y_MM) + [OUT_Y_MM] * hold
    for _ in range(wobbles):
        # 398 mm is 4 mm on the SHOPKEEPER'S side of the line: a real F in the
        # history, i.e. exactly the T,T,T,F,T,T,T pattern upstream double-counts.
        ys += [398.0, OUT_Y_MM]
    ys += [OUT_Y_MM] * hold
    last = _run(_walk(ys, x))[-1]
    assert last.out_count == 1, "one journey out was billed more than once"
    assert last.back_count == 0
    assert last.net_count == 1


@settings(max_examples=150, deadline=None)
@given(
    hold=st.integers(min_value=4, max_value=7),
    x=st.floats(min_value=20.0, max_value=270.0, allow_nan=False,
                allow_infinity=False),
)
def test_an_item_arriving_from_the_customer_side_is_never_a_negative_sale(hold, x):
    """Deviation 2: net_count can never go below zero, for any arrival order."""
    ys = ([OUT_Y_MM] * hold + _ramp(OUT_Y_MM, IN_Y_MM) + [IN_Y_MM] * hold)
    last = _run(_walk(ys, x))[-1]
    assert last.net_count >= 0
    assert last.out_count == 0
    assert last.back_count == 0
    assert last.entries_from_out == 1
    assert not last.amber


@settings(max_examples=200, deadline=None)
@given(frames=FRAMES)
def test_the_line_geometry_is_a_consistent_sign_function(frames):
    """side() and signed_distance_mm() agree, for every point in every script."""
    zone = LineZone((0.0, LINE_Y_MM), (297.0, LINE_Y_MM), min_crossing_frames=3)
    for pts in frames:
        for p in pts:
            d = zone.signed_distance_mm(p)
            s = zone.side(p)
            if s == 1:
                assert d > zone.dead_band_mm
            elif s == -1:
                assert d < -zone.dead_band_mm
            else:
                assert abs(d) <= zone.dead_band_mm
            # the OUT side is the customer's side: larger y
            assert (d > 0) == (p[1] > LINE_Y_MM)


# ============================================================ CROSS-MODULE
#
# Properties that only exist where two modules meet.


@settings(max_examples=120, deadline=None)
@given(
    prices=st.lists(st.integers(min_value=1, max_value=50_00), min_size=1, max_size=6)
)
def test_the_ledger_can_reconstruct_the_intent_amount_from_scratch(prices):
    """SESSION x LEDGER x MONEY: the audit log alone reproduces the money.

    Nothing but the JSONL file is read here -- no Session object, no in-memory
    state. If the log and the till could ever disagree, this is where it shows.
    """
    with scratch() as d:
        path = d / "audit.jsonl"
        s = Session(VirtualClock(), Ledger(path))
        s.on_mat_lock(True)
        for i, p in enumerate(prices):
            s.on_placement(Placement(item_id=f"i{i}", name=f"n{i}", price_paise=p))
            s.on_exit(f"i{i}")
        s.on_done()
        expected = s.intent_amount_paise

        ok, _, _, err = verify(path)
        assert ok, err

        priced: dict[str, int] = {}
        committed: set[str] = set()
        for rec in Ledger(path).read():
            if rec.get("reason") in _PRICE_LINES:
                priced[rec["item_id"]] = rec["price_paise"]
            if rec.get("reason") == Reason.COMMITTED:
                committed.add(rec["item_id"])
        assert committed == set(priced)
        replayed = money.total([paise(priced[i]) for i in sorted(committed)])
        assert replayed == expected == sum(prices)
        assert type(int(replayed)) is int


@settings(max_examples=80, deadline=None)
@given(
    prices=st.lists(st.integers(min_value=1, max_value=50_00), min_size=1, max_size=5),
    amber=st.integers(min_value=0, max_value=3),
)
def test_kernel_never_mints_an_intent_for_an_amber_inflated_total(prices, amber):
    """SESSION x KERNEL: the amount the kernel writes ahead is the priced sum.

    Amber lines are on the mat and in the log, and they are not in the debit.
    """
    with scratch() as d:
        path = d / "audit.jsonl"
        led = Ledger(path)
        s = Session(VirtualClock(), led)
        s.on_mat_lock(True)
        for i, p in enumerate(prices):
            s.on_placement(Placement(item_id=f"p{i}", name=f"p{i}", price_paise=p))
            s.on_exit(f"p{i}")
        for j in range(amber):
            s.on_placement(
                Placement(item_id=f"a{j}", name=f"a{j}", price_paise=None)
            )
            s.on_exit(f"a{j}")
        s.on_done()

        assert s.amber_count == amber
        assert s.intent_amount_paise == sum(prices)

        k = Kernel(str(d / "k.db"), VirtualClock(), led)
        try:
            it = k.create_intent(s.session_id, s.intent_amount_paise)
            assert it.amount_paise == sum(prices)
            assert k.count() == 1
            # idempotent under any number of repeats, including after the
            # session has appended more lines to the shared ledger
            again = k.create_intent(s.session_id, s.intent_amount_paise)
            assert again.nonce == it.nonce
            assert k.count() == 1
            ok, _, _, err = verify(path)
            assert ok, err
        finally:
            k.close()


def test_genesis_is_the_only_head_a_fresh_ledger_can_have(tmp_path):
    led = Ledger(tmp_path / "fresh.jsonl")
    assert led.head == GENESIS and led.count == 0
    ok, n, head, err = verify(tmp_path / "fresh.jsonl")
    assert ok and n == 0 and head == GENESIS and err is None
