"""S4a acceptance: a debit for (session_id, cycle, amount_paise) happens once or never.

The fake gateway below is deliberately hostile: it can capture money and THEN
lose the response, which is the only failure mode that actually causes double
charges in the wild. Every test that claims "never double-charges" asserts on
the gateway's own captured ledger, not on the kernel's opinion of itself.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from gawaah.clock import VirtualClock
from gawaah.kernel import (
    CALLING,
    DEFAULT_MAX_RETRIEVE_ATTEMPTS,
    ESCALATED,
    FAILED,
    INDETERMINATE,
    NEW,
    RETRIEVE,
    SETTLED,
    GatewayResult,
    IllegalTransition,
    Kernel,
    KernelError,
    UnknownIntent,
    idem_key,
)
from gawaah.ledger import Ledger, verify
from gawaah.money import MoneyError, from_rupees_str

ROOT = Path(__file__).resolve().parent.parent
AMT = from_rupees_str("214.50")          # 21450 paise
SESSION = "sess_kirana_0001"


# ---------------------------------------------------------------- fakes

class GatewayTimeout(Exception):
    """The response never came back. We do not know if money moved."""


class FakeGateway:
    """Captures by nonce, idempotently, and can lose the response afterwards.

    `captures` is the gateway's own book. If the kernel ever double-charges,
    charge_calls goes to 2 for the same nonce and this shows up immediately.
    """

    def __init__(self) -> None:
        self.captures: dict[str, dict] = {}
        self.charge_calls = 0
        self.lookup_calls = 0
        self.lose_next_response = False
        self.lookup_raises = 0
        self.lock = threading.Lock()

    def charge(self, nonce: str, amount_paise: int) -> dict:
        with self.lock:
            self.charge_calls += 1
            if nonce not in self.captures:
                self.captures[nonce] = {
                    "payment_id": "pay_" + nonce[-8:],
                    "amount_paise": amount_paise,
                    "status": "captured",
                }
            rec = self.captures[nonce]
            lose = self.lose_next_response
            self.lose_next_response = False
        if lose:
            raise GatewayTimeout(nonce)
        return dict(rec)

    def lookup(self, nonce: str) -> dict:
        with self.lock:
            self.lookup_calls += 1
            if self.lookup_raises > 0:
                self.lookup_raises -= 1
                raise GatewayTimeout("lookup " + nonce)
            rec = self.captures.get(nonce)
        if rec is None:
            return {"found": False, "status": "not_found"}
        return {"found": True, **rec}

    @property
    def total_captured(self) -> int:
        return sum(int(r["amount_paise"]) for r in self.captures.values())


def mk(tmp_path: Path, name: str = "k", **kw) -> tuple[Kernel, Ledger, Path]:
    db = os.path.join(str(tmp_path), name + ".sqlite3")
    lp = tmp_path.joinpath(name + "_audit.jsonl")
    led = Ledger(lp)
    return Kernel(db, VirtualClock(), led, **kw), led, lp


def drive(kernel: Kernel, gw: FakeGateway, session: str = SESSION,
          amount: int = AMT, cycle: int = 0):
    """The real caller shape: write-ahead, release, call, record."""
    it = kernel.create_intent(session, amount, cycle)
    if it.is_terminal:
        return it
    kernel.mark_calling(it.nonce)                 # committed + connection closed
    try:
        res = gw.charge(it.nonce, amount)         # network, no DB held
    except GatewayTimeout:
        return kernel.mark_indeterminate(it.nonce, reason="timeout")
    return kernel.mark_settled(it.nonce, res["payment_id"])


def events(path: Path) -> list[str]:
    import json
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            out.append(json.loads(line)["event"])
    return out


# ---------------------------------------------------------------- lint gate

def test_kernel_passes_the_no_float_lint():
    """INVARIANT 1 is enforced by a tool, not by good intentions."""
    r = subprocess.run([sys.executable, "tools/lint_no_float.py"],
                       cwd=str(ROOT), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "gawaah/kernel.py" not in r.stdout


def test_in_memory_db_is_refused(tmp_path):
    """An in-memory DB cannot survive the crash the kernel exists to survive."""
    for bad in (":memory:", "", "file::memory:?cache=shared"):
        with pytest.raises(KernelError):
            Kernel(bad, VirtualClock(), Ledger(tmp_path.joinpath("a.jsonl")))


# ---------------------------------------------------------------- idempotency

def test_same_key_twice_returns_the_same_nonce_and_one_row(tmp_path):
    k, _, _ = mk(tmp_path)
    a = k.create_intent(SESSION, AMT, cycle=0)
    b = k.create_intent(SESSION, AMT, cycle=0)
    assert a.nonce == b.nonce
    assert a.state == b.state == NEW
    assert k.count() == 1
    assert len(k.all_intents()) == 1


def test_only_one_audit_line_for_a_repeated_create(tmp_path):
    k, _, lp = mk(tmp_path)
    for _ in range(10):
        k.create_intent(SESSION, AMT)
    assert events(lp) == ["intent.created"]


def test_a_different_cycle_is_a_different_debit(tmp_path):
    k, _, _ = mk(tmp_path)
    a = k.create_intent(SESSION, AMT, cycle=0)
    b = k.create_intent(SESSION, AMT, cycle=1)
    assert a.nonce != b.nonce and k.count() == 2


@pytest.mark.parametrize("session,amount,cycle", [
    ("other_sess", AMT, 0),
    (SESSION, AMT + 1, 0),
    (SESSION, AMT, 2),
])
def test_any_component_change_is_a_new_intent(tmp_path, session, amount, cycle):
    k, _, _ = mk(tmp_path)
    a = k.create_intent(SESSION, AMT, 0)
    b = k.create_intent(session, amount, cycle)
    assert a.nonce != b.nonce and k.count() == 2


def test_idem_key_is_stable_and_order_independent():
    assert idem_key(SESSION, 0, AMT) == idem_key(SESSION, 0, AMT)
    assert idem_key(SESSION, 0, AMT) != idem_key(SESSION, 1, AMT)
    assert len(idem_key(SESSION, 0, AMT)) == 64


def test_idempotency_survives_a_restart(tmp_path):
    k, led, lp = mk(tmp_path)
    a = k.create_intent(SESSION, AMT)
    del k
    k2 = Kernel(os.path.join(str(tmp_path), "k.sqlite3"), VirtualClock(), Ledger(lp))
    b = k2.create_intent(SESSION, AMT)
    assert a.nonce == b.nonce and k2.count() == 1


# ---------------------------------------------------------------- money gate

@pytest.mark.parametrize("bad", [214.50, "21450", True, None])
def test_float_and_friends_cannot_become_a_debit(tmp_path, bad):
    k, _, _ = mk(tmp_path)
    with pytest.raises(MoneyError):
        k.create_intent(SESSION, bad)


@pytest.mark.parametrize("bad", [0, -1])
def test_non_positive_debits_are_refused(tmp_path, bad):
    k, _, _ = mk(tmp_path)
    with pytest.raises(MoneyError):
        k.create_intent(SESSION, bad)


@pytest.mark.parametrize("bad_cycle", [-1, 1.0, "0", True])
def test_bad_cycle_is_refused(tmp_path, bad_cycle):
    k, _, _ = mk(tmp_path)
    with pytest.raises(KernelError):
        k.create_intent(SESSION, AMT, bad_cycle)


def test_amount_survives_the_round_trip_as_an_exact_integer(tmp_path):
    k, _, _ = mk(tmp_path)
    it = k.create_intent(SESSION, from_rupees_str("0.01"))
    assert k.get(it.nonce).amount_paise == 1
    assert isinstance(k.get(it.nonce).amount_paise, int)


# ---------------------------------------------------------------- concurrency

def test_fifty_concurrent_threads_produce_exactly_one_intent(tmp_path):
    k, _, lp = mk(tmp_path)
    n = 50
    seen: list[str] = []
    errs: list[BaseException] = []
    seen_lock = threading.Lock()
    gate = threading.Barrier(n)

    def worker() -> None:
        try:
            gate.wait()                       # maximise the collision window
            it = k.create_intent(SESSION, AMT, cycle=0)
            with seen_lock:
                seen.append(it.nonce)
        except BaseException as e:            # noqa: BLE001 - surfaced below
            errs.append(e)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errs, errs
    assert len(seen) == n
    assert len(set(seen)) == 1, f"{len(set(seen))} distinct nonces were handed out"
    assert k.count() == 1
    assert events(lp) == ["intent.created"]   # exactly one write-ahead line
    ok, _, _, err = verify(lp)
    assert ok, err


def test_fifty_concurrent_threads_charge_the_gateway_exactly_once(tmp_path):
    """The whole point, end to end: 50 racing callers, one capture."""
    k, _, _ = mk(tmp_path)
    gw = FakeGateway()
    n = 50
    gate = threading.Barrier(n)
    errs: list[BaseException] = []

    def worker() -> None:
        try:
            gate.wait()
            it = k.create_intent(SESSION, AMT)
            # No test-side lock: mark_calling IS the serialisation point.
            # Whoever wins NEW -> CALLING is the only caller allowed to charge.
            try:
                k.mark_calling(it.nonce)
            except IllegalTransition:
                return
            res = gw.charge(it.nonce, AMT)
            k.mark_settled(it.nonce, res["payment_id"])
        except BaseException as e:            # noqa: BLE001
            errs.append(e)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errs, errs
    assert gw.charge_calls == 1
    assert gw.total_captured == AMT
    assert k.count() == 1
    assert k.all_intents()[0].state == SETTLED


def test_eight_separate_processes_produce_exactly_one_intent(tmp_path):
    """No shared Python lock can explain this one: uniqueness is the DB's job.

    Eight OS processes, each with its own Kernel and its own connections, all
    racing on the same file. Exactly one row, one nonce.
    """
    db = os.path.join(str(tmp_path), "mp.sqlite3")
    prog = (
        "import sys, time\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from gawaah.kernel import Kernel\n"
        "from gawaah.clock import VirtualClock\n"
        "from gawaah.ledger import Ledger\n"
        f"k = Kernel({db!r}, VirtualClock(), Ledger(sys.argv[1]))\n"
        "time.sleep(max(0.0, float(sys.argv[2]) - time.time()))\n"
        f"print(k.create_intent({SESSION!r}, {AMT}).nonce)\n"
    )
    import time
    start_at = repr(time.time() + 2.0)
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", prog,
             os.path.join(str(tmp_path), f"mp_{i}.jsonl"), start_at],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for i in range(8)
    ]
    outs = []
    for p in procs:
        so, se = p.communicate(timeout=60)
        assert p.returncode == 0, se
        outs.append(so.strip())

    assert len(outs) == 8
    assert len(set(outs)) == 1, f"processes disagreed on the nonce: {set(outs)}"
    k = Kernel(db, VirtualClock(), Ledger(tmp_path.joinpath("check.jsonl")))
    assert k.count() == 1
    assert k.all_intents()[0].nonce == outs[0]


def test_concurrent_mark_calling_lets_exactly_one_caller_through(tmp_path):
    """No external lock this time: the DB transaction is the only guard."""
    k, _, _ = mk(tmp_path)
    it = k.create_intent(SESSION, AMT)
    n = 32
    gate = threading.Barrier(n)
    winners: list[str] = []
    losers: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        gate.wait()
        try:
            k.mark_calling(it.nonce)
            with lock:
                winners.append("w")
        except IllegalTransition:
            with lock:
                losers.append("l")

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(winners) == 1, f"{len(winners)} callers were allowed to charge"
    assert len(losers) == n - 1
    assert k.get(it.nonce).attempts == 1


# ---------------------------------------------------------------- ordering

def test_no_db_connection_is_held_across_the_gateway_call(tmp_path):
    """Proves the ordering claim rather than asserting it in a docstring.

    Both probes run INSIDE the network call, which is where a held transaction
    would still be open. A second, independent writer runs at the same moment:
    if the caller were holding a write tx, that writer would block or fail.
    """
    k, _, _ = mk(tmp_path)
    gw = FakeGateway()
    observed: list[tuple[str, int]] = []

    def probe(tag: str) -> None:
        observed.append((tag, k.open_connections))
        # a real write from the same process while the "call" is in flight
        k.create_intent("probe_" + tag, AMT)

    class ProbingGateway(FakeGateway):
        def charge(self, nonce: str, amount_paise: int) -> dict:
            probe("charge")
            return FakeGateway.charge(self, nonce, amount_paise)

        def lookup(self, nonce: str) -> dict:
            probe("lookup")
            return FakeGateway.lookup(self, nonce)

    gw = ProbingGateway()

    assert drive(k, gw).state == SETTLED          # probe fires inside charge

    gw.lose_next_response = True
    it2 = drive(k, gw, session="s2")              # probe fires inside charge
    assert it2.state == INDETERMINATE
    assert k.reconcile(it2.nonce, gw.lookup).state == SETTLED   # inside lookup

    assert observed == [("charge", 0), ("charge", 0), ("lookup", 0)], (
        f"a connection was held across a network call: {observed}")


def test_a_new_row_proves_the_gateway_was_never_called(tmp_path):
    """recover() deliberately leaves NEW alone: write-ahead ordering means the
    call is unreachable until CALLING is durable."""
    k, _, _ = mk(tmp_path)
    it = k.create_intent(SESSION, AMT)
    assert k.recover() == []
    assert k.get(it.nonce).state == NEW
    assert k.intents_needing_retrieve() == []


# ---------------------------------------------------------------- crash

def test_crash_between_commit_and_gateway_call_leaves_a_recoverable_row(tmp_path):
    db = os.path.join(str(tmp_path), "crash.sqlite3")
    lp = tmp_path.joinpath("crash.jsonl")

    k1 = Kernel(db, VirtualClock(), Ledger(lp))
    it = k1.create_intent(SESSION, AMT)
    k1.mark_calling(it.nonce)
    del k1                                       # <-- the process dies here

    k2 = Kernel(db, VirtualClock(), Ledger(lp))
    assert k2.get(it.nonce).state == CALLING     # survived the crash
    recovered = k2.recover()
    assert [r.nonce for r in recovered] == [it.nonce]
    assert recovered[0].state == INDETERMINATE
    assert recovered[0].reason == "crash_between_commit_and_result"
    assert [r.nonce for r in k2.intents_needing_retrieve()] == [it.nonce]
    ok, _, _, err = verify(lp)
    assert ok, err


def test_a_real_subprocess_crash_is_recoverable(tmp_path):
    """os._exit() from a child: no cleanup, no finally blocks, no mercy."""
    db = os.path.join(str(tmp_path), "hard.sqlite3")
    lp = os.path.join(str(tmp_path), "hard.jsonl")
    prog = (
        "import os, sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from gawaah.kernel import Kernel\n"
        "from gawaah.clock import VirtualClock\n"
        "from gawaah.ledger import Ledger\n"
        f"k = Kernel({db!r}, VirtualClock(), Ledger({lp!r}))\n"
        f"it = k.create_intent({SESSION!r}, {AMT})\n"
        "k.mark_calling(it.nonce)\n"
        "print(it.nonce)\n"
        "sys.stdout.flush()\n"
        "os._exit(9)\n"
    )
    r = subprocess.run([sys.executable, "-c", prog], capture_output=True, text=True)
    assert r.returncode == 9, r.stderr
    nonce = r.stdout.strip()
    assert nonce

    k = Kernel(db, VirtualClock(), Ledger(Path(lp)))
    assert k.get(nonce).state == CALLING
    assert [i.nonce for i in k.recover()] == [nonce]
    assert k.get(nonce).state == INDETERMINATE


def test_blind_retry_of_an_indeterminate_intent_is_impossible(tmp_path):
    """The safety property that makes double charges structurally unreachable."""
    k, _, _ = mk(tmp_path)
    gw = FakeGateway()
    gw.lose_next_response = True

    first = drive(k, gw)
    assert first.state == INDETERMINATE
    assert gw.charge_calls == 1
    assert gw.total_captured == AMT               # money DID move

    # a naive caller tries again with the same key
    again = k.create_intent(SESSION, AMT)
    assert again.nonce == first.nonce
    with pytest.raises(IllegalTransition):
        k.mark_calling(again.nonce)
    assert gw.charge_calls == 1                   # no second capture


# ---------------------------------------------------------------- reconcile

def test_reconcile_finds_the_settled_payment_and_never_double_charges(tmp_path):
    k, _, _ = mk(tmp_path)
    gw = FakeGateway()
    gw.lose_next_response = True

    it = drive(k, gw)
    assert it.state == INDETERMINATE

    pending = k.intents_needing_retrieve()
    assert [p.nonce for p in pending] == [it.nonce]

    done = k.reconcile(it.nonce, gw.lookup)
    assert done.state == SETTLED
    assert done.payment_id == gw.captures[it.nonce]["payment_id"]
    assert done.reason == "reconciled:captured"
    assert gw.charge_calls == 1
    assert gw.total_captured == AMT
    assert k.intents_needing_retrieve() == []


def test_reconcile_is_idempotent_under_repetition(tmp_path):
    k, _, lp = mk(tmp_path)
    gw = FakeGateway()
    gw.lose_next_response = True
    it = drive(k, gw)
    first = k.reconcile(it.nonce, gw.lookup)
    n_lines = len(events(lp))
    for _ in range(5):
        again = k.reconcile(it.nonce, gw.lookup)
        assert again.state == SETTLED
        assert again.payment_id == first.payment_id
    assert gw.charge_calls == 1
    assert gw.total_captured == AMT
    assert len(events(lp)) == n_lines            # terminal rows are not re-audited


def test_sweep_reconciles_everything_unknown(tmp_path):
    k, _, _ = mk(tmp_path)
    gw = FakeGateway()
    nonces = []
    for i in range(4):
        gw.lose_next_response = True
        nonces.append(drive(k, gw, session=f"s{i}").nonce)
    assert len(k.intents_needing_retrieve()) == 4
    out = k.sweep(gw.lookup)
    assert {i.state for i in out} == {SETTLED}
    assert gw.charge_calls == 4                   # one per distinct intent
    assert gw.total_captured == AMT * 4
    assert k.intents_needing_retrieve() == []


def test_gateway_that_never_saw_the_nonce_fails_the_intent(tmp_path):
    """The charge never landed. Money did not move, so FAILED is a fact."""
    k, _, _ = mk(tmp_path)
    gw = FakeGateway()
    it = k.create_intent(SESSION, AMT)
    k.mark_calling(it.nonce)
    k.mark_indeterminate(it.nonce, reason="connection_reset")   # never reached gw
    out = k.reconcile(it.nonce, gw.lookup)
    assert out.state == FAILED
    assert out.reason == "gateway_never_saw_nonce"
    assert gw.total_captured == 0


def test_lookup_failure_returns_to_indeterminate_and_is_swept_again(tmp_path):
    k, _, _ = mk(tmp_path)
    gw = FakeGateway()
    gw.lose_next_response = True
    it = drive(k, gw)
    gw.lookup_raises = 2

    a = k.reconcile(it.nonce, gw.lookup)
    assert a.state == INDETERMINATE and a.reason == "lookup_failed:GatewayTimeout"
    b = k.reconcile(it.nonce, gw.lookup)
    assert b.state == INDETERMINATE
    c = k.reconcile(it.nonce, gw.lookup)
    assert c.state == SETTLED
    assert c.retrieve_attempts == 3
    assert gw.charge_calls == 1


@pytest.mark.parametrize("status", ["authorized", "created", "pending", "processing"])
def test_pending_gateway_status_abstains(tmp_path, status):
    k, _, _ = mk(tmp_path)
    it = k.create_intent(SESSION, AMT)
    k.mark_calling(it.nonce)
    k.mark_indeterminate(it.nonce)
    out = k.reconcile(it.nonce, lambda n: {"found": True, "status": status,
                                           "payment_id": "pay_x",
                                           "amount_paise": AMT})
    assert out.state == INDETERMINATE
    assert out.reason == f"gateway_pending:{status}"
    assert [i.nonce for i in k.intents_needing_retrieve()] == [it.nonce]


@pytest.mark.parametrize("status", ["failed", "declined", "cancelled", "expired"])
def test_definite_gateway_failure_is_terminal(tmp_path, status):
    k, _, _ = mk(tmp_path)
    it = k.create_intent(SESSION, AMT)
    k.mark_calling(it.nonce)
    k.mark_indeterminate(it.nonce)
    out = k.reconcile(it.nonce, lambda n: {"found": True, "status": status})
    assert out.state == FAILED and out.reason == f"gateway_status:{status}"


def test_unknown_gateway_status_abstains_loudly(tmp_path):
    """INVARIANT 7: unknown is never a settlement."""
    k, _, _ = mk(tmp_path)
    it = k.create_intent(SESSION, AMT)
    k.mark_calling(it.nonce)
    k.mark_indeterminate(it.nonce)
    out = k.reconcile(it.nonce, lambda n: {"found": True, "status": "quantum",
                                           "payment_id": "p", "amount_paise": AMT})
    assert out.state == INDETERMINATE
    assert out.reason == "unknown_status:quantum"


def test_amount_mismatch_is_parked_for_a_human_not_settled(tmp_path):
    k, _, _ = mk(tmp_path)
    it = k.create_intent(SESSION, AMT)
    k.mark_calling(it.nonce)
    k.mark_indeterminate(it.nonce)
    out = k.reconcile(it.nonce, lambda n: {"found": True, "status": "captured",
                                           "payment_id": "pay_x",
                                           "amount_paise": AMT + 100})
    assert out.state != SETTLED
    assert out.needs_human is True
    assert out.reason.startswith("amount_mismatch:")
    assert out.payment_id is None
    assert k.intents_needing_retrieve() == []          # parked, not swept
    assert [i.nonce for i in k.intents_needing_human()] == [it.nonce]


def test_capture_with_no_payment_id_is_parked(tmp_path):
    k, _, _ = mk(tmp_path)
    it = k.create_intent(SESSION, AMT)
    k.mark_calling(it.nonce)
    k.mark_indeterminate(it.nonce)
    out = k.reconcile(it.nonce, lambda n: {"found": True, "status": "captured",
                                           "amount_paise": AMT})
    assert out.state != SETTLED and out.needs_human is True
    assert out.reason == "settled_without_payment_id"


def test_float_amount_from_the_gateway_is_rejected_not_rounded(tmp_path):
    k, _, _ = mk(tmp_path)
    it = k.create_intent(SESSION, AMT)
    k.mark_calling(it.nonce)
    k.mark_indeterminate(it.nonce)
    out = k.reconcile(it.nonce, lambda n: {"found": True, "status": "captured",
                                           "payment_id": "p", "amount": 214.50})
    assert out.state != SETTLED and out.needs_human is True
    assert out.reason.startswith("bad_lookup_response:")


def test_reconciling_a_live_call_is_refused(tmp_path):
    k, _, _ = mk(tmp_path)
    it = k.create_intent(SESSION, AMT)
    with pytest.raises(IllegalTransition):
        k.reconcile(it.nonce, lambda n: {"found": False})
    k.mark_calling(it.nonce)
    with pytest.raises(IllegalTransition):
        k.reconcile(it.nonce, lambda n: {"found": False})


def test_reconcile_of_a_settled_intent_does_not_even_look_up(tmp_path):
    k, _, _ = mk(tmp_path)
    gw = FakeGateway()
    it = drive(k, gw)
    assert it.state == SETTLED
    before = gw.lookup_calls
    out = k.reconcile(it.nonce, gw.lookup)
    assert out.state == SETTLED and gw.lookup_calls == before


def test_gateway_result_normalisation():
    assert GatewayResult.from_any(None).found is False
    r = GatewayResult.from_any({"id": "pay_1", "amount": 500, "status": "captured"})
    assert r.found and r.payment_id == "pay_1" and r.amount_paise == 500
    assert GatewayResult.from_any(GatewayResult(found=True)).found is True
    with pytest.raises(KernelError):
        GatewayResult.from_any(["nope"])


# ---------------------------------------------------------------- transitions

def test_illegal_transitions_are_refused(tmp_path):
    k, _, _ = mk(tmp_path)
    it = k.create_intent(SESSION, AMT)
    with pytest.raises(IllegalTransition):
        k.mark_settled(it.nonce, "pay_x")           # NEW -> SETTLED
    with pytest.raises(IllegalTransition):
        k.mark_indeterminate(it.nonce)              # NEW -> INDETERMINATE
    k.mark_calling(it.nonce)
    with pytest.raises(IllegalTransition):
        k.mark_calling(it.nonce)                    # CALLING -> CALLING
    k.mark_settled(it.nonce, "pay_x")
    with pytest.raises(IllegalTransition):
        k.mark_indeterminate(it.nonce)              # SETTLED is terminal


def test_a_second_payment_id_on_one_intent_is_refused(tmp_path):
    """If this ever succeeded, someone was charged twice."""
    k, _, _ = mk(tmp_path)
    it = k.create_intent(SESSION, AMT)
    k.mark_calling(it.nonce)
    k.mark_settled(it.nonce, "pay_first")
    with pytest.raises(IllegalTransition):
        k.mark_settled(it.nonce, "pay_second")
    assert k.get(it.nonce).payment_id == "pay_first"


def test_replaying_the_same_settlement_is_a_silent_noop(tmp_path):
    k, _, lp = mk(tmp_path)
    it = k.create_intent(SESSION, AMT)
    k.mark_calling(it.nonce)
    k.mark_settled(it.nonce, "pay_1")
    n = len(events(lp))
    for _ in range(4):
        assert k.mark_settled(it.nonce, "pay_1").state == SETTLED
    assert len(events(lp)) == n


def test_a_late_webhook_can_settle_an_indeterminate_intent(tmp_path):
    k, _, _ = mk(tmp_path)
    gw = FakeGateway()
    gw.lose_next_response = True
    it = drive(k, gw)
    assert it.state == INDETERMINATE
    out = k.mark_settled(it.nonce, gw.captures[it.nonce]["payment_id"])
    assert out.state == SETTLED and gw.charge_calls == 1


def test_settling_after_failed_raises_the_human_flag(tmp_path):
    k, _, _ = mk(tmp_path)
    it = k.create_intent(SESSION, AMT)
    k.mark_calling(it.nonce)
    k.mark_failed(it.nonce, reason="declined")
    out = k.mark_settled(it.nonce, "pay_late")
    assert out.state == SETTLED
    assert out.needs_human is True and out.reason == "settled_after_failed"


def test_unknown_nonce_raises(tmp_path):
    k, _, _ = mk(tmp_path)
    for fn in (k.get, k.mark_calling, k.mark_indeterminate):
        with pytest.raises(UnknownIntent):
            fn("gwn_deadbeef")


def test_attempts_counter_tracks_gateway_calls_only(tmp_path):
    k, _, _ = mk(tmp_path)
    gw = FakeGateway()
    gw.lose_next_response = True
    it = drive(k, gw)
    k.reconcile(it.nonce, gw.lookup)
    final = k.get(it.nonce)
    assert final.attempts == 1                      # one charge attempt, ever
    assert final.retrieve_attempts == 1


# ---------------------------------------------------------------- ledger

def test_the_ledger_records_every_transition(tmp_path):
    k, _, lp = mk(tmp_path)
    gw = FakeGateway()
    gw.lose_next_response = True

    it = drive(k, gw)                               # created, calling, indeterminate
    k.reconcile(it.nonce, gw.lookup)                # retrieve, settled

    assert events(lp) == [
        "intent.created",
        "intent.calling",
        "intent.indeterminate",
        "intent.retrieve",
        "intent.settled",
    ]
    ok, n, head, err = verify(lp)
    assert ok, err
    assert n == 5 and head


def test_every_audit_line_carries_the_state_edge_and_the_money(tmp_path):
    import json
    k, _, lp = mk(tmp_path)
    gw = FakeGateway()
    gw.lose_next_response = True
    it = drive(k, gw)
    k.reconcile(it.nonce, gw.lookup)

    recs = [json.loads(l) for l in lp.read_text().splitlines() if l.strip()]
    for r in recs:
        assert r["module"] == "kernel"
        assert r["nonce"] == it.nonce
        assert r["amount_paise"] == AMT
        assert isinstance(r["amount_paise"], int)
        assert r["session_id"] == SESSION
        assert r["to_state"] in {NEW, CALLING, INDETERMINATE, RETRIEVE, SETTLED, FAILED}
    assert [r["from_state"] for r in recs] == [
        None, NEW, CALLING, INDETERMINATE, RETRIEVE]
    assert recs[-1]["payment_id"] == gw.captures[it.nonce]["payment_id"]


def test_ledger_chain_survives_the_crash_and_recovery_path(tmp_path):
    db = os.path.join(str(tmp_path), "c.sqlite3")
    lp = tmp_path.joinpath("c.jsonl")
    gw = FakeGateway()

    k1 = Kernel(db, VirtualClock(), Ledger(lp))
    it = k1.create_intent(SESSION, AMT)
    k1.mark_calling(it.nonce)
    gw.charge(it.nonce, AMT)                        # gateway captured it
    del k1                                          # then we died

    k2 = Kernel(db, VirtualClock(), Ledger(lp))     # reopen: head is recovered
    k2.recover()
    out = k2.reconcile(it.nonce, gw.lookup)
    assert out.state == SETTLED
    assert gw.charge_calls == 1 and gw.total_captured == AMT
    assert events(lp) == [
        "intent.created", "intent.calling", "intent.indeterminate",
        "intent.retrieve", "intent.settled",
    ]
    ok, n, _, err = verify(lp)
    assert ok, err
    assert n == 5


def test_ledger_stays_consistent_under_concurrent_settlement(tmp_path):
    """Ledger keeps an in-memory head; the kernel must serialise its appends.

    Two threads that read the same prev_hash write two lines claiming the same
    parent, and verify() reports a chain break. The window is the file write
    inside Ledger.append, so it only opens up under real append pressure:
    synchronous=NORMAL here on purpose, because FULL's fsync serialises the
    threads and hides the very race this test exists to catch. Measured: with
    the kernel's audit lock removed this configuration breaks the chain in 5/5
    runs; at 24 threads x 1 intent it caught it only 1/5.
    """
    db = os.path.join(str(tmp_path), "race.sqlite3")
    lp = tmp_path.joinpath("race.jsonl")
    k = Kernel(db, VirtualClock(), Ledger(lp), synchronous="NORMAL")
    gw = FakeGateway()
    n, per_thread = 32, 8
    gate = threading.Barrier(n)
    errs: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            gate.wait()
            for j in range(per_thread):
                drive(k, gw, session=f"sess_{i}_{j}")
        except BaseException as e:                  # noqa: BLE001
            errs.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total = n * per_thread
    assert not errs, errs
    assert k.count() == total
    assert gw.charge_calls == total and gw.total_captured == AMT * total
    ok, lines, _, err = verify(lp)
    assert ok, err
    assert lines == total * 3                       # created + calling + settled


# ================================================================== GAP 1
# THE ABSTENTION LOOP MUST BE BOUNDED.
#
# INVARIANT 7 says abstain rather than guess, and reconcile() honours it: an
# unknown or pending gateway answer goes straight back to INDETERMINATE. What
# was missing is the other half — a loop that can abstain forever is not a
# safety property, it is a stuck till. These tests pin a finite retrieve budget,
# a terminal-for-machines ESCALATED state, an audit line, and the rule that
# escalation NEVER charges anything.
# ==========================================================================


def counting_lookup(result):
    """A gateway lookup that records every call so a budget can be asserted."""
    calls: list[str] = []

    def fn(nonce: str):
        calls.append(nonce)
        return dict(result) if isinstance(result, dict) else result

    fn.calls = calls          # type: ignore[attr-defined]
    return fn


PENDING = {"found": True, "status": "pending", "payment_id": "pay_x",
           "amount_paise": AMT}
QUANTUM = {"found": True, "status": "quantum", "payment_id": "pay_x",
           "amount_paise": AMT}


def stuck(tmp_path, max_retrieve_attempts=3):
    """An intent whose money moved but whose outcome the gateway will not state."""
    k, _, lp = mk(tmp_path, max_retrieve_attempts=max_retrieve_attempts)
    gw = FakeGateway()
    gw.lose_next_response = True
    it = drive(k, gw)
    assert it.state == INDETERMINATE
    assert gw.total_captured == AMT          # money really did move
    return k, gw, it, lp


def test_the_retrieve_budget_is_finite_and_escalates(tmp_path):
    """Sweep forever; the gateway is asked exactly `max` times and no more."""
    k, gw, it, lp = stuck(tmp_path, max_retrieve_attempts=3)
    look = counting_lookup(PENDING)

    for _ in range(50):
        k.sweep(look)

    final = k.get(it.nonce)
    assert final.state == ESCALATED, "the loop is unbounded: it is still being swept"
    assert final.needs_human is True
    assert final.retrieve_attempts == 3
    assert len(look.calls) == 3, f"{len(look.calls)} lookups for a budget of 3"
    assert k.intents_needing_retrieve() == []
    assert [i.nonce for i in k.intents_needing_human()] == [it.nonce]
    assert [i.nonce for i in k.escalated_intents()] == [it.nonce]
    # nothing auto-charged on the way out
    assert gw.charge_calls == 1 and gw.total_captured == AMT


def test_escalation_writes_an_audit_line_naming_the_budget(tmp_path):
    import json
    k, gw, it, lp = stuck(tmp_path, max_retrieve_attempts=2)
    for _ in range(10):
        k.sweep(counting_lookup(PENDING))

    assert events(lp) == [
        "intent.created", "intent.calling", "intent.indeterminate",
        "intent.retrieve", "intent.indeterminate",
        "intent.retrieve", "intent.indeterminate",
        "intent.escalated",
    ]
    rec = [json.loads(l) for l in lp.read_text().splitlines() if l.strip()][-1]
    assert rec["event"] == "intent.escalated"
    assert rec["to_state"] == ESCALATED
    assert rec["from_state"] == INDETERMINATE
    assert rec["needs_human"] is True
    assert rec["retrieve_attempts"] == 2
    assert rec["max_retrieve_attempts"] == 2
    assert rec["amount_paise"] == AMT and isinstance(rec["amount_paise"], int)
    assert rec["payment_id"] is None            # nothing was concluded
    ok, _, _, err = verify(lp)
    assert ok, err


def test_an_escalated_intent_is_never_touched_by_the_machine_again(tmp_path):
    """Terminal for the sweeper. Only a human moves it, and never by charging."""
    k, gw, it, lp = stuck(tmp_path, max_retrieve_attempts=1)
    k.sweep(counting_lookup(PENDING))
    k.sweep(counting_lookup(PENDING))
    assert k.get(it.nonce).state == ESCALATED

    after = counting_lookup(PENDING)
    for _ in range(5):
        assert k.reconcile(it.nonce, after).state == ESCALATED
        k.sweep(after)
    assert after.calls == [], "the gateway was polled for an escalated intent"
    assert gw.charge_calls == 1, "escalation triggered a charge"
    assert k.get(it.nonce).payment_id is None


def test_escalation_is_reached_from_any_abstaining_answer(tmp_path):
    """Pending, unknown-status and an unreachable gateway all burn the budget."""
    for i, answer in enumerate((PENDING, QUANTUM, "raise")):
        k, gw, it, _ = stuck(tmp_path.joinpath(f"a{i}"), max_retrieve_attempts=2)

        def look(nonce, _a=answer):
            if _a == "raise":
                raise GatewayTimeout(nonce)
            return dict(_a)

        for _ in range(20):
            k.sweep(look)
        assert k.get(it.nonce).state == ESCALATED, answer
        assert gw.charge_calls == 1


def test_a_human_can_resolve_an_escalated_intent_without_a_gateway(tmp_path):
    """`resolve_escalated` takes no gateway argument: it cannot charge by design."""
    k, gw, it, lp = stuck(tmp_path, max_retrieve_attempts=1)
    for _ in range(4):
        k.sweep(counting_lookup(PENDING))
    assert k.get(it.nonce).state == ESCALATED

    pid = gw.captures[it.nonce]["payment_id"]
    out = k.resolve_escalated(it.nonce, SETTLED, operator="anita@kirana",
                              payment_id=pid, note="found on the RZP dashboard")
    assert out.state == SETTLED
    assert out.payment_id == pid
    assert out.needs_human is True          # the flag stays: a person owned this
    assert gw.charge_calls == 1
    assert "intent.resolved" in events(lp)
    ok, _, _, err = verify(lp)
    assert ok, err


def test_human_resolution_refuses_a_guess(tmp_path):
    k, gw, it, _ = stuck(tmp_path, max_retrieve_attempts=1)
    for _ in range(3):
        k.sweep(counting_lookup(PENDING))
    assert k.get(it.nonce).state == ESCALATED

    with pytest.raises(KernelError):
        k.resolve_escalated(it.nonce, SETTLED, operator="", payment_id="pay_1")
    with pytest.raises(KernelError):
        k.resolve_escalated(it.nonce, SETTLED, operator="anita")   # no payment_id
    with pytest.raises(KernelError):
        k.resolve_escalated(it.nonce, INDETERMINATE, operator="anita")
    assert k.get(it.nonce).state == ESCALATED
    # FAILED needs no payment id: "no money moved" carries no reference
    assert k.resolve_escalated(it.nonce, FAILED, operator="anita",
                               note="refunded by hand").state == FAILED


def test_resolve_escalated_only_applies_to_escalated_rows(tmp_path):
    k, _, _ = mk(tmp_path)
    it = k.create_intent(SESSION, AMT)
    with pytest.raises(IllegalTransition):
        k.resolve_escalated(it.nonce, FAILED, operator="anita")


def test_a_late_authoritative_webhook_can_still_settle_an_escalated_intent(tmp_path):
    k, gw, it, _ = stuck(tmp_path, max_retrieve_attempts=1)
    for _ in range(3):
        k.sweep(counting_lookup(PENDING))
    assert k.get(it.nonce).state == ESCALATED
    out = k.mark_settled(it.nonce, gw.captures[it.nonce]["payment_id"])
    assert out.state == SETTLED and out.needs_human is True
    assert gw.charge_calls == 1


def test_the_budget_is_bounded_by_default(tmp_path):
    """No caller has to remember to pass a cap for the loop to terminate."""
    assert 0 < DEFAULT_MAX_RETRIEVE_ATTEMPTS < 100
    k, _, _ = mk(tmp_path, name="dflt")
    gw = FakeGateway()
    gw.lose_next_response = True
    it = drive(k, gw)
    look = counting_lookup(PENDING)
    for _ in range(DEFAULT_MAX_RETRIEVE_ATTEMPTS * 3 + 10):
        k.sweep(look)
    assert k.get(it.nonce).state == ESCALATED
    assert len(look.calls) == DEFAULT_MAX_RETRIEVE_ATTEMPTS


@pytest.mark.parametrize("bad", [0, -1, 1.0, "3", True, None])
def test_a_nonsense_retrieve_budget_is_refused_at_construction(tmp_path, bad):
    with pytest.raises(KernelError):
        Kernel(os.path.join(str(tmp_path), "b.sqlite3"), VirtualClock(),
               Ledger(tmp_path.joinpath("b.jsonl")), max_retrieve_attempts=bad)


def test_a_resolvable_answer_still_wins_before_the_budget_runs_out(tmp_path):
    """The cap must not turn a recoverable intent into a human ticket."""
    k, gw, it, _ = stuck(tmp_path, max_retrieve_attempts=5)
    gw.lookup_raises = 3
    for _ in range(5):
        k.sweep(gw.lookup)
    final = k.get(it.nonce)
    assert final.state == SETTLED
    assert final.needs_human is False
    assert final.retrieve_attempts == 4      # 3 failures then the answer


# ================================================================== GAP 2
# TWO PROCESSES, ONE LEDGER FILE.
#
# Ledger.append caches the chain head in memory. That is correct for one writer
# and silently wrong for two: the second process appends a line whose prev_hash
# is whatever the head was when IT opened the file, and verify() reports a chain
# break. The kernel is the writer, so the kernel takes a cross-process file lock
# and re-reads the true head under it before every append.
# ==========================================================================


def _appender_prog(db: str, lp: str, n: int, tag: str) -> str:
    return (
        "import sys, time\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from gawaah.kernel import Kernel\n"
        "from gawaah.clock import VirtualClock\n"
        "from gawaah.ledger import Ledger\n"
        f"k = Kernel({db!r}, VirtualClock(), Ledger({lp!r}))\n"
        "time.sleep(max(0.0, float(sys.argv[1]) - time.time()))\n"
        f"for i in range({n}):\n"
        f"    it = k.create_intent({tag!r} + str(i), {AMT})\n"
        "    k.mark_calling(it.nonce)\n"
        "    k.mark_settled(it.nonce, 'pay_' + it.nonce[-6:])\n"
        "print('ok')\n"
    )


def test_two_processes_sharing_one_ledger_keep_the_chain_intact(tmp_path):
    """The defect, and its fix, in one file: two writers, one verifiable chain.

    Each process gets its own kernel DB (uniqueness of intents is already the
    DB's job and is proved elsewhere) and they SHARE the ledger file, which is
    the thing that used to corrupt.
    """
    import time

    lp = os.path.join(str(tmp_path), "shared.jsonl")
    per_proc, procs_n = 12, 4
    start_at = repr(time.time() + 2.0)
    procs = [
        subprocess.Popen(
            [sys.executable, "-c",
             _appender_prog(os.path.join(str(tmp_path), f"p{i}.sqlite3"), lp,
                            per_proc, f"s{i}_"),
             start_at],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for i in range(procs_n)
    ]
    for p in procs:
        so, se = p.communicate(timeout=120)
        assert p.returncode == 0, se
        assert so.strip() == "ok"

    ok, lines, head, err = verify(Path(lp))
    assert ok, err
    assert lines == procs_n * per_proc * 3, lines
    assert head


def test_a_kernel_reports_whether_its_ledger_lock_is_cross_process(tmp_path):
    """An honest flag, so a deployment can tell what it actually has."""
    k, _, lp = mk(tmp_path, name="flag")
    assert isinstance(k.ledger_lock_is_cross_process, bool)
    assert k.ledger_lock_path == os.fspath(lp) + ".lock"
    assert os.path.exists(k.ledger_lock_path)


def test_a_second_process_appending_between_our_appends_is_not_overwritten(tmp_path):
    """The narrowest form of the bug: interleave by hand, then verify."""
    lp = tmp_path.joinpath("interleave.jsonl")
    k = Kernel(os.path.join(str(tmp_path), "a.sqlite3"), VirtualClock(), Ledger(lp))
    k.create_intent("sess_a", AMT)

    prog = _appender_prog(os.path.join(str(tmp_path), "b.sqlite3"),
                          os.fspath(lp), 3, "sess_b_")
    r = subprocess.run([sys.executable, "-c", prog, "0"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    # our in-memory head is now stale by 9 lines; the next append must notice
    k.create_intent("sess_c", AMT)
    ok, lines, _, err = verify(lp)
    assert ok, err
    assert lines == 1 + 9 + 1
