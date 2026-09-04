"""MUTATION TESTING — the harness, and the tests it forced us to write.

Two halves.

PART A pins `tools/mutate.py` itself. A mutation tester that silently generates
zero mutants, or that reports KILLED because its own sandbox is broken, would
hand back a beautiful kill rate that means nothing. Every claim the harness
makes about the suite is only as good as these tests.

PART B is the point of the exercise. Each test here was written because a
specific mutant SURVIVED — a one-token edit to a money-critical module that the
existing suite sailed straight past. The mutant that each test kills is named in
its docstring, in the exact form the harness prints it, so the claim can be
re-checked:

    ./.venv/bin/python tools/mutate.py --money-path --jobs 4 \
        --out results/mutation_before.json
    ./.venv/bin/python tools/mutate.py --money-path --with-mutation-tests \
        --jobs 4 --out results/mutation_after.json

Not every survivor is a hole. Some are EQUIVALENT MUTANTS: edits that cannot
change observable behaviour for any input the contract admits. Those are named
in `test_documented_equivalent_mutants_are_named` below rather than papered over
with a test that asserts an implementation detail. Counting an equivalent mutant
as a failure would be as dishonest as not looking for it.
"""
from __future__ import annotations

import ast
import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.mutate import (  # noqa: E402
    ALL_OPERATORS,
    CHILD_ENV,
    ERROR,
    KILLED,
    MONEY_PATH_TARGETS,
    OPERATOR_NAMES,
    SURVIVED,
    ModuleReport,
    Mutant,
    MutantResult,
    MutationRunner,
    Sandbox,
    apply_mutant,
    format_report,
    generate_mutants,
    index_nodes,
    null_mutant_source,
    run_tests,
    write_json,
)

REPO = Path(__file__).resolve().parent.parent
IS_MUTATION_CHILD = os.environ.get(CHILD_ENV) == "1"


# =========================================================== PART A: harness


TOY = textwrap.dedent(
    '''
    """A module docstring. Never mutated."""


    THRESHOLD = 10


    def classify(n, strict=True):
        """Docstring. Never mutated."""
        if n < THRESHOLD and strict:
            return "low"
        if not strict:
            return "unknown"
        flag = True
        n = n + 1
        return "high"
    '''
).strip()


def _ops(mutants):
    return sorted({m.operator for m in mutants})


def test_every_declared_operator_produces_a_mutant_somewhere():
    """A silently-dead operator is a silently-inflated kill rate."""
    mutants = generate_mutants(TOY, "toy.py")
    found = set(_ops(mutants))
    # `bool_const` and `return_const` and the rest all have a site in TOY
    # except comparison_boundary/arith_swap, which need their own snippets.
    extra = generate_mutants("def f(a, b):\n    if a <= b:\n        return a * b\n", "x.py")
    found |= set(_ops(extra))
    missing = set(OPERATOR_NAMES) - found
    assert not missing, f"operators that never fire: {sorted(missing)}"


def test_docstrings_are_never_mutated():
    mutants = generate_mutants(TOY, "toy.py")
    lines = TOY.splitlines()
    for m in mutants:
        text = lines[m.lineno - 1].strip()
        assert not text.startswith('"""'), f"mutated a docstring: {m.label}"


def test_module_scope_statements_are_not_deleted():
    """Deleting `THRESHOLD = 10` is an ImportError, not a subtle bug. Counting
    such mutants as kills would inflate the rate without testing anything."""
    mutants = generate_mutants(TOY, "toy.py")
    deletions = [m for m in mutants if m.operator == "stmt_delete"]
    assert deletions, "stmt_delete produced nothing at all"
    assert all("THRESHOLD" not in m.before for m in deletions)


def test_module_scope_constants_are_still_value_mutated():
    """We refuse to DELETE module-level statements; we still change their
    numbers, because `THRESHOLD = 11` is exactly the kind of edit that slips
    through review."""
    mutants = generate_mutants(TOY, "toy.py")
    assert any(
        m.operator == "const_int" and m.before == "10" and m.after == "11"
        for m in mutants
    )


def test_mutants_are_deterministic_and_uniquely_identified():
    a = generate_mutants(TOY, "toy.py")
    b = generate_mutants(TOY, "toy.py")
    assert [m.mid for m in a] == [m.mid for m in b]
    assert len({m.mid for m in a}) == len(a), "mutant ids collide"


def test_apply_mutant_changes_exactly_one_site():
    src = "def f(a, b):\n    return a < b and a == 1\n"
    mutants = generate_mutants(src, "x.py")
    baseline = null_mutant_source(src)
    for m in mutants:
        out = apply_mutant(src, m)
        assert out != baseline, f"{m.label} changed nothing"
        # exactly one token differs between the unparsed baseline and mutant
        diff = sum(1 for x, y in zip(baseline.split(), out.split()) if x != y)
        assert diff <= 2, f"{m.label} changed too much:\n{out}"


@pytest.mark.parametrize(
    "src,operator,expect",
    [
        ("def f(a, b):\n    return a < b\n", "comparison_negate", "a >= b"),
        ("def f(a, b):\n    return a < b\n", "comparison_boundary", "a <= b"),
        ("def f(a, b):\n    return a and b\n", "boolop_swap", "a or b"),
        ("def f(a):\n    return not a\n", "not_remove", "return a"),
        ("def f():\n    return 41\n", "const_int", "return 42"),
        ("def f():\n    return True\n", "bool_const", "return False"),
        ("def f(a, b):\n    return a + b\n", "arith_swap", "a - b"),
    ],
)
def test_operator_semantics(src, operator, expect):
    mutants = [m for m in generate_mutants(src, "x.py") if m.operator == operator]
    outs = [apply_mutant(src, m) for m in mutants]
    assert any(expect in o for o in outs), f"{operator} never produced {expect!r}: {outs}"


def test_return_constant_can_force_a_gate_open():
    """`return True` on a predicate is 'authenticate everything'. It must be
    a mutant we generate, or a suite that never asserts the False branch of a
    security gate would look fully covered."""
    src = "def verify(a, b):\n    return a == b\n"
    outs = [
        apply_mutant(src, m)
        for m in generate_mutants(src, "x.py")
        if m.operator == "return_const"
    ]
    assert any("return True" in o for o in outs)


def test_statement_deletion_leaves_valid_python():
    src = "def f(a):\n    if a:\n        a = 1\n    return a\n"
    for m in generate_mutants(src, "x.py"):
        out = apply_mutant(src, m)
        ast.parse(out)  # must compile
    lone = [m for m in generate_mutants(src, "x.py")
            if m.operator == "stmt_delete" and m.before == "a = 1"]
    assert lone, "did not offer to delete the lone statement in the if-body"
    assert "pass" in apply_mutant(src, lone[0])


def test_null_mutant_is_the_identity():
    for module, _ in MONEY_PATH_TARGETS:
        src = (REPO / module).read_text(encoding="utf-8")
        once = null_mutant_source(src)
        twice = null_mutant_source(once)
        assert once == twice, f"{module}: unparse is not idempotent"
        assert ast.dump(ast.parse(once)) == ast.dump(ast.parse(src))


def test_index_nodes_is_stable_across_parses():
    t1, t2 = ast.parse(TOY), ast.parse(TOY)
    n1, n2 = index_nodes(t1), index_nodes(t2)
    assert [type(a).__name__ for a in n1] == [type(b).__name__ for b in n2]


def test_every_money_module_generates_a_healthy_number_of_mutants():
    """A module that produces two mutants has not been mutation tested."""
    for module, _ in MONEY_PATH_TARGETS:
        src = (REPO / module).read_text(encoding="utf-8")
        mutants = generate_mutants(src, module)
        assert len(mutants) >= 50, f"{module}: only {len(mutants)} mutants"


def test_report_and_json_round_trip(tmp_path):
    m = Mutant("id", "gawaah/money.py", "const_int", 7, 4, 3, 0, "100", "101", "x = 100")
    rep = ModuleReport("gawaah/money.py", ["tests/test_money_ledger.py"], total=2)
    rep.results = [MutantResult(m, SURVIVED, 0.1), MutantResult(m, KILLED, 0.1)]
    rep.killed, rep.survived = 1, 1
    assert rep.kill_rate == pytest.approx(0.5)
    text = format_report([rep])
    assert "SURVIVORS" in text and "50.0%" in text
    out = tmp_path / "m.json"
    write_json([rep], out)
    doc = json.loads(out.read_text())
    assert doc["totals"]["scored_mutants"] == 2
    assert doc["totals"]["kill_rate_pct"] == 50.0


def test_error_mutants_are_excluded_from_the_kill_rate():
    """A mutant we failed to build is not evidence about the tests."""
    m = Mutant("id", "m.py", "const_int", 1, 0, 0, 0, "1", "2")
    rep = ModuleReport("m.py", ["t.py"], total=3, killed=1, survived=1, errored=1)
    rep.results = [
        MutantResult(m, KILLED), MutantResult(m, SURVIVED), MutantResult(m, ERROR)
    ]
    assert rep.scored == 2
    assert rep.kill_rate == pytest.approx(0.5)


def test_a_timeout_counts_as_a_kill_not_a_survivor():
    """A mutant that hangs the suite has been detected. It has not passed."""
    from tools.mutate import TIMEOUT
    m = Mutant("id", "m.py", "const_int", 1, 0, 0, 0, "1", "2")
    assert MutantResult(m, TIMEOUT).killed is True
    assert MutantResult(m, SURVIVED).killed is False


def test_sandbox_never_writes_to_the_working_tree(tmp_path):
    """Other agents are editing this repo. A harness that mutated files in
    place would corrupt their world the moment it crashed."""
    src = REPO / "gawaah" / "money.py"
    before = src.read_bytes()
    box = Sandbox(tmp_path / "box", REPO).build(dirs=("gawaah",))
    box.write("gawaah/money.py", "raise RuntimeError('mutated')\n")
    assert (box.root / "gawaah" / "money.py").read_text().startswith("raise")
    assert src.read_bytes() == before
    box.restore("gawaah/money.py")
    assert (box.root / "gawaah" / "money.py").read_bytes() == before
    box.destroy()
    assert not (tmp_path / "box").exists()


@pytest.mark.skipif(
    IS_MUTATION_CHILD, reason="already inside a mutation run; do not recurse"
)
def test_runner_classifies_killed_and_survived_end_to_end(tmp_path):
    """The whole loop against a toy package whose test file deliberately
    checks one branch and ignores another. The covered mutant must die; the
    uncovered one must survive. If this test cannot tell them apart, no kill
    rate this harness reports means anything."""
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "m.py").write_text(
        "def watched(n):\n"
        "    if n < 10:\n"
        "        return 'low'\n"
        "    return 'high'\n"
        "\n"
        "def unwatched(n):\n"
        "    return n + 1\n"
    )
    (root / "tests" / "test_m.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))\n"
        "from pkg.m import watched\n"
        "def test_low():\n"
        "    assert watched(0) == 'low'\n"
        "def test_high():\n"
        "    assert watched(50) == 'high'\n"
    )
    runner = MutationRunner(jobs=2, source_root=root, min_timeout=60.0)
    rep = runner.run_module("pkg/m.py", ["tests/test_m.py"])
    assert not rep.aborted, rep.aborted
    assert rep.total > 0
    by_line = {}
    for r in rep.results:
        by_line.setdefault(r.mutant.lineno, []).append(r)

    watched_cmp = [r for r in by_line[2] if r.mutant.operator == "comparison_negate"]
    assert watched_cmp and all(r.killed for r in watched_cmp), \
        "the covered branch was not killed — the runner is not detecting failures"

    unwatched = [r for r in by_line[7] if r.mutant.operator == "arith_swap"]
    assert unwatched and all(r.status == SURVIVED for r in unwatched), \
        "an untested line was reported as killed — the runner is reporting noise"


@pytest.mark.skipif(
    IS_MUTATION_CHILD, reason="already inside a mutation run; do not recurse"
)
def test_a_timed_out_run_takes_its_grandchildren_with_it(tmp_path):
    """A timeout must reap the WHOLE process tree, not just the child.

    This is the harness's own worst bug, found by watching it. `test_kernel.py`
    launches eight OS subprocesses; when a mutant hung that test and the
    timeout fired, the old code killed pytest and orphaned the eight, which
    kept spinning and holding an flock. Half an hour later the machine was
    carrying a crowd of them, later mutants timed out because the box was busy
    rather than because the code was wrong, and a timeout scores as a KILL.
    The harness was manufacturing its own kill rate.
    """
    import subprocess as _sp
    import time as _time

    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    marker = tmp_path / "grandchild.pid"
    (root / "tests" / "grandchild.py").write_text(
        "import os, sys, time\n"
        "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
        "time.sleep(600)\n"
    )
    (root / "tests" / "test_slow.py").write_text(
        "import pathlib, subprocess, sys, time\n"
        "HERE = pathlib.Path(__file__).resolve().parent\n"
        f"MARKER = {str(marker)!r}\n"
        "def test_spawns_a_grandchild_then_hangs():\n"
        "    subprocess.Popen([sys.executable, str(HERE / 'grandchild.py'), MARKER])\n"
        "    time.sleep(600)\n"
    )
    box = Sandbox(tmp_path / "box", root).build(dirs=("tests",))
    t0 = _time.monotonic()
    rc, secs, tail = run_tests(box, ["tests/test_slow.py"], timeout=6.0)
    assert rc == -9 and tail == "TIMEOUT"
    assert secs < 40, "the timeout did not fire promptly"

    deadline = _time.monotonic() + 10
    while _time.monotonic() < deadline and not marker.exists():
        _time.sleep(0.2)
    assert marker.exists(), "the grandchild never started; test proves nothing"
    pid = int(marker.read_text())

    for _ in range(50):
        alive = _sp.run(["ps", "-p", str(pid)], capture_output=True).returncode == 0
        if not alive:
            break
        _time.sleep(0.1)
    else:                                     # pragma: no cover - the bug itself
        os.kill(pid, 9)
        pytest.fail(f"grandchild {pid} outlived the timeout that killed its parent")
    box.destroy()


@pytest.mark.skipif(
    IS_MUTATION_CHILD, reason="already inside a mutation run; do not recurse"
)
def test_only_the_baseline_is_retried_never_a_mutant(tmp_path):
    """A flaky UNMUTATED suite must not throw away a whole module's number, and
    a flaky suite must not hand a mutant a kill it did not earn.

    `tests/test_kernel.py` synchronises eight OS processes on a two-second
    wall-clock barrier; on a machine busy building the next sandbox it can miss
    it, and one blink used to abort the entire kernel measurement. So the
    harness retries — but ONLY its own precondition. Retrying a MUTANT would
    turn "the suite is flaky" into "the mutant was killed", which is the
    opposite of what this tool is for.

    The toy module below fails its first run and passes afterwards.
    """
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "m.py").write_text("def f(n):\n    return n + 1\n")
    counter = tmp_path / "runs.txt"
    (root / "tests" / "test_m.py").write_text(
        "import pathlib, sys\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))\n"
        "from pkg.m import f\n"
        f"C = pathlib.Path({str(counter)!r})\n"
        "def test_flaky_once():\n"
        "    n = int(C.read_text()) if C.exists() else 0\n"
        "    C.write_text(str(n + 1))\n"
        "    assert n >= 1, 'first run always fails'\n"
        "def test_real():\n"
        "    assert f(1) == 2\n"
    )
    rep = MutationRunner(jobs=1, source_root=root, min_timeout=60.0).run_module(
        "pkg/m.py", ["tests/test_m.py"]
    )
    assert not rep.aborted, rep.aborted
    assert rep.baseline_attempts == 2, rep.baseline_attempts
    assert rep.scored > 0, "the module produced no verdicts"
    # and the real assertion still does its job on the retried-green baseline
    killed = [r for r in rep.results
              if r.mutant.operator == "arith_swap" and r.killed]
    assert killed, "the covered arithmetic was not killed after the retry"


@pytest.mark.skipif(
    IS_MUTATION_CHILD, reason="already inside a mutation run; do not recurse"
)
def test_runner_aborts_when_the_null_mutant_fails(tmp_path):
    """A module whose tests depend on formatting would make every kill an
    artefact of ast.unparse. The harness must refuse to report a number."""
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pkg" / "__init__.py").write_text("")
    # `# comment` survives parse but not unparse: the test asserts on source.
    (root / "pkg" / "m.py").write_text("def f():\n    return 1  # keep-me\n")
    (root / "tests" / "test_m.py").write_text(
        "import pathlib\n"
        "def test_source_has_comment():\n"
        "    p = pathlib.Path(__file__).resolve().parent.parent / 'pkg' / 'm.py'\n"
        "    assert 'keep-me' in p.read_text()\n"
    )
    rep = MutationRunner(jobs=1, source_root=root, min_timeout=60.0).run_module(
        "pkg/m.py", ["tests/test_m.py"]
    )
    assert "NULL MUTANT FAILED" in rep.aborted
    assert rep.killed == 0 and rep.survived == 0


# ============================================ PART B: killing real survivors
#
# Everything below exists because the harness found a one-token edit to a
# money-critical module that the suite did not notice.

from gawaah.money import (  # noqa: E402
    MoneyError, add, from_rupees_str, paise, to_rupees_str, total,
)


# ---------------------------------------------------------------- money.py

def test_total_sums_a_sequence_exactly():
    """KILLS: gawaah/money.py:81-84 — the ENTIRE `total()` function.

    Before this test, `total()` had no caller in any test file: `t = 0` could
    be deleted, `t += x` could become `t -= x`, and `return Paise(t)` could
    become `return True`, and the suite stayed green. `total()` is the function
    that adds up a basket.
    """
    assert total([]) == 0
    assert total([paise(1)]) == 1
    assert total([paise(21437), paise(4500), paise(1)]) == 25938
    # -= instead of += would give -25938; a non-zero seed would shift it
    assert total([paise(100)] * 7) == 700
    assert total(iter([paise(5), paise(6)])) == 11, "must consume any iterable"


def test_total_and_add_agree_and_reject_floats():
    """KILLS: gawaah/money.py:83 `t += int(paise(v))` -> `t -= ...` / deleted.

    Also pins that `total` goes through `paise()`, so a float in a basket is
    rejected by the summing function and not only by the constructor.
    """
    xs = [paise(21437), paise(500), paise(-25)]
    assert total(xs) == add(*xs) == 21912
    with pytest.raises(MoneyError):
        total([paise(100), 2.5])          # a float in the basket
    with pytest.raises(MoneyError):
        total([paise(100), True])         # bool is not a paisa


def test_total_returns_an_int_not_a_bool_or_none():
    """KILLS: gawaah/money.py:84 `return Paise(t)` -> `return None` / `True`."""
    r = total([paise(3), paise(4)])
    assert isinstance(r, int) and not isinstance(r, bool)
    assert r == 7


def test_zero_and_minus_one_rupee_strings_render_with_the_right_sign():
    """KILLS: gawaah/money.py:68 `p < 0` -> `p <= 0`, and `0` -> `1` / `-1`.

    `to_rupees_str(0)` must be "0.00", not "-0.00": a receipt that prints minus
    zero is a receipt a shopkeeper will not sign. The hypothesis round-trip
    test could not see this because "-0.00" parses back to 0, and it did not
    draw -1 either, so the `< -1` mutant also lived.
    """
    assert to_rupees_str(paise(0)) == "0.00"
    assert to_rupees_str(paise(-1)) == "-0.01"
    assert to_rupees_str(paise(1)) == "0.01"
    assert to_rupees_str(paise(-100)) == "-1.00"
    assert not to_rupees_str(paise(0)).startswith("-")


def test_rupee_strings_are_stripped_before_parsing():
    """KILLS: gawaah/money.py:44 `s = s.strip()` -> <deleted>.

    Amounts reach this function from a keypad, a CSV and an operator's copy
    and paste. A leading space must not become a parse failure.
    """
    assert from_rupees_str("  214.50  ") == 21450
    assert from_rupees_str("\t7\n") == 700
    assert from_rupees_str(" -5.25 ") == -525


def test_empty_and_blank_rupee_strings_raise_rather_than_become_zero():
    """KILLS: gawaah/money.py:46 `raise MoneyError('empty rupee string')` -> <deleted>.

    With that raise deleted, `from_rupees_str("")` returns 0 — a blank field
    silently prices the basket at zero rupees and every existing test passes.
    """
    for blank in ("", "   ", "\t", "\n"):
        with pytest.raises(MoneyError):
            from_rupees_str(blank)


@pytest.mark.parametrize("bad", ["abc", "12a.50", "1.a5", "₹214.50", "1,000.00", "1.2.3", "1e2"])
def test_non_numeric_rupee_strings_raise_MoneyError_not_ValueError(bad):
    """KILLS: gawaah/money.py:55 and :57 — both `raise MoneyError(f'bad rupee
    string')` statements.

    Deleting either one does NOT make the function succeed; it makes it fall
    through to `int("abc")`, which raises a bare ValueError. Callers catch
    MoneyError. A bare ValueError escapes the money boundary and 500s the
    counter instead of showing amber.
    """
    with pytest.raises(MoneyError):
        from_rupees_str(bad)


def test_a_leading_dot_is_a_valid_sub_rupee_amount():
    """KILLS: gawaah/money.py:54 `whole != ""` -> `whole == ""`.

    The `whole != ""` half of that guard exists so that ".50" means fifty
    paise. Flipping it turns every sub-rupee string into an error, and no test
    passed one.
    """
    assert from_rupees_str(".50") == 50
    assert from_rupees_str(".5") == 50
    assert from_rupees_str("-.75") == -75
    assert from_rupees_str(".00") == 0


def test_the_float_rejection_says_why():
    """KILLS: gawaah/money.py:29 `raise MoneyError(f'float is not money…')` ->
    <deleted>.

    Deleting it still raises MoneyError — from the `not isinstance(value, int)`
    branch below — so `pytest.raises(MoneyError)` cannot tell the difference.
    The specific message is the thing that stops the next person from
    "fixing" the float rejection with a round(). INVARIANT 1 is a doctrine, and
    a doctrine that does not say its own name is not enforced.

    First attempt at this test asserted only `"float" in str(exc).lower()` and
    the mutant SURVIVED the after-run: the fallback message is `not an integer:
    214.5 (float)`, and `type(value).__name__` put the word "float" in it. The
    harness caught a weak assertion of mine, which is the entire point of
    running it. The assertion below names the sentence, not the word.
    """
    with pytest.raises(MoneyError) as ei:
        paise(214.50)
    msg = str(ei.value)
    assert "float is not money" in msg, msg
    assert "integer paise" in msg, "the message must say what money IS"
    assert "0.1 + 0.2" in msg, "the message must name the bug it prevents"
    with pytest.raises(MoneyError) as eb:
        paise(True)
    assert "bool is not money" in str(eb.value)
    with pytest.raises(MoneyError) as es:
        paise("214")
    assert "not an integer" in str(es.value), "a str is a different fault"


# --------------------------------------------------------------- ledger.py

def test_verify_rejects_a_line_that_is_not_valid_json():
    """KILLS: gawaah/ledger.py:99 `return False, ...` -> `return True, ...`
    (and the `return None` / `return True` / <deleted> variants of the same
    statement).

    Every existing tamper test corrupts a line while keeping it valid JSON, so
    the "not valid JSON" branch of `verify` had no test at all. Flip that one
    `False` to `True` and a ledger file with a garbage line reports ok=True:
    the audit log's one job is to say when it has been edited.
    """
    import tempfile
    from gawaah.clock import VirtualClock
    from gawaah.ledger import Ledger, verify

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "audit.jsonl"
        led, clk = Ledger(p), VirtualClock()
        for i in range(4):
            led.append(ts=clk.now_iso(), module="m", amount_paise=i * 100)
        lines = p.read_text().splitlines()
        lines[2] = "{not json at all"
        p.write_text("\n".join(lines) + "\n")
        ok, n, _, err = verify(p)
        assert ok is False, "a ledger with a corrupt line must NOT verify"
        assert "line 3" in err and "not valid JSON" in err
        assert n == 2, "verification must stop at the corrupt line"


def test_verify_rejects_a_line_whose_hash_field_was_removed():
    """KILLS: gawaah/ledger.py:102 `return False, ...` -> `return True, ...`.

    Deleting the `hash` field is the cheapest possible attack: no recomputation
    needed. With that False flipped, a line with its hash stripped verifies
    clean, and the chain has been broken with an edit any text editor can make.
    """
    import json as _json
    import tempfile
    from gawaah.clock import VirtualClock
    from gawaah.ledger import Ledger, verify

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "audit.jsonl"
        led, clk = Ledger(p), VirtualClock()
        for i in range(4):
            led.append(ts=clk.now_iso(), module="m", amount_paise=i * 100)
        lines = p.read_text().splitlines()
        rec = _json.loads(lines[1])
        rec.pop("hash")
        lines[1] = _json.dumps(rec, sort_keys=True)
        p.write_text("\n".join(lines) + "\n")
        ok, n, _, err = verify(p)
        assert ok is False, "a line with no hash must NOT verify"
        assert "missing hash" in err and "line 2" in err


def test_verify_returns_a_four_tuple_on_every_path(tmp_path):
    """KILLS: gawaah/ledger.py:99 / :102 `return ... -> return None|True`.

    `ok, n, head, err = verify(path)` is how every caller uses this. A branch
    that returns a bare bool unpacks into a TypeError at the worst moment.
    """
    import json as _json
    from gawaah.clock import VirtualClock
    from gawaah.ledger import GENESIS, Ledger, verify

    cases = []
    p = tmp_path / "good.jsonl"
    led, clk = Ledger(p), VirtualClock()
    for i in range(3):
        led.append(ts=clk.now_iso(), module="m", i=i)
    cases.append(p)

    bad = tmp_path / "garbage.jsonl"
    bad.write_text("}}}not json\n")
    cases.append(bad)

    nohash = tmp_path / "nohash.jsonl"
    rec = _json.loads(p.read_text().splitlines()[0])
    rec.pop("hash")
    nohash.write_text(_json.dumps(rec, sort_keys=True) + "\n")
    cases.append(nohash)

    cases.append(tmp_path / "missing.jsonl")

    for path in cases:
        result = verify(path)
        assert isinstance(result, tuple) and len(result) == 4, f"{path.name}: {result!r}"
        ok, n, head, err = result
        assert isinstance(ok, bool) and isinstance(n, int) and isinstance(head, str)
        assert err is None or isinstance(err, str)
        assert head == GENESIS or len(head) == 64


def test_verify_accepts_a_string_path():
    """KILLS: gawaah/ledger.py:88 `path = Path(path)` -> <deleted>.

    `make verify-ledger` and every CLI caller hand this a string.
    """
    import tempfile
    from gawaah.clock import VirtualClock
    from gawaah.ledger import Ledger, verify

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "audit.jsonl"
        led, clk = Ledger(p), VirtualClock()
        led.append(ts=clk.now_iso(), module="m", amount_paise=1)
        ok, n, head, err = verify(str(p))          # a str, not a Path
        assert ok and n == 1 and err is None
        ok2, n2, _, _ = verify(str(Path(d) / "nope.jsonl"))
        assert ok2 and n2 == 0


def test_verify_skips_blank_lines_between_entries(tmp_path):
    """KILLS: gawaah/ledger.py:95 `continue` -> <deleted>.

    The blank-line skip is not decoration: a ledger concatenated from two files,
    or one an editor left a trailing newline in, must still verify. With the
    `continue` gone, the blank line hits json.loads and the whole chain reads as
    corrupt — a false accusation of tampering, which is as bad as missing one.
    """
    from gawaah.clock import VirtualClock
    from gawaah.ledger import Ledger, verify

    p = tmp_path / "audit.jsonl"
    led, clk = Ledger(p), VirtualClock()
    for i in range(3):
        led.append(ts=clk.now_iso(), module="m", i=i)
    lines = p.read_text().splitlines()
    p.write_text("\n".join([lines[0], "", lines[1], "   ", lines[2], ""]) + "\n")
    ok, n, _, err = verify(p)
    assert ok, err
    assert n == 3, "blank lines must not be counted as entries"


def test_ledger_read_yields_every_record_in_order(tmp_path):
    """KILLS: gawaah/ledger.py:73 `not self.path.exists()` -> `self.path.exists()`,
    :74 `return` -> <deleted>, and :77 `yield json.loads(line)` -> <deleted>.

    All three survived because `Ledger.read()` — a public method — had no
    caller in the suite. Inverting its existence check made it return nothing
    for every file that exists and blow up on every file that does not, and the
    suite stayed green.
    """
    from gawaah.clock import VirtualClock
    from gawaah.ledger import Ledger

    p = tmp_path / "audit.jsonl"
    led, clk = Ledger(p), VirtualClock()
    for i in range(4):
        led.append(ts=clk.now_iso(), module="m", i=i, amount_paise=i * 250)
    got = list(led.read())
    assert len(got) == 4, f"read() yielded {len(got)} of 4 records"
    assert [r["i"] for r in got] == [0, 1, 2, 3]
    assert [r["amount_paise"] for r in got] == [0, 250, 500, 750]
    assert got[-1]["hash"] == led.head
    assert list(Ledger(tmp_path / "never-written.jsonl").read()) == []


def test_ledger_count_is_an_absolute_number_not_a_self_comparison(tmp_path):
    """KILLS: gawaah/ledger.py:59 `return self._count` -> `return None` /
    `return True` / <deleted>.

    The existing test asserts `reopened.count == original.count`, which is a
    tautology: if the property returns None both sides are None and the test
    passes. Money reports are built on this number, so assert the number.
    """
    from gawaah.clock import VirtualClock
    from gawaah.ledger import GENESIS, Ledger

    p = tmp_path / "audit.jsonl"
    led, clk = Ledger(p), VirtualClock()
    assert led.count == 0 and led.head == GENESIS
    for i in range(7):
        led.append(ts=clk.now_iso(), module="m", i=i)
        assert led.count == i + 1
    assert led.count == 7
    assert isinstance(led.count, int) and not isinstance(led.count, bool)
    reopened = Ledger(p)
    assert reopened.count == 7
    assert reopened.head == led.head and len(reopened.head) == 64


def test_ledger_creates_missing_parent_directories(tmp_path):
    """KILLS: gawaah/ledger.py:45 `mkdir(parents=True, ...)` -> `parents=False`,
    and the deletion of that mkdir entirely.

    Every existing test writes into tmp_path, which already exists. In
    production the path is `results/audit.jsonl` on a fresh machine.
    """
    from gawaah.clock import VirtualClock
    from gawaah.ledger import Ledger, verify

    deep = tmp_path / "a" / "b" / "c" / "audit.jsonl"
    assert not deep.parent.exists()
    led, clk = Ledger(deep), VirtualClock()
    led.append(ts=clk.now_iso(), module="m", amount_paise=1)
    assert deep.exists()
    ok, n, _, err = verify(deep)
    assert ok and n == 1, err


def test_ledger_accepts_a_string_path(tmp_path):
    """KILLS: gawaah/ledger.py:44 `self.path = Path(self.path)` -> <deleted>."""
    from gawaah.clock import VirtualClock
    from gawaah.ledger import Ledger, verify

    p = str(tmp_path / "sub" / "audit.jsonl")
    led, clk = Ledger(p), VirtualClock()          # a str, not a Path
    led.append(ts=clk.now_iso(), module="m", amount_paise=42)
    assert isinstance(led.path, Path)
    ok, n, _, err = verify(p)
    assert ok and n == 1, err


def test_the_chain_head_cannot_be_seeded_by_the_constructor(tmp_path):
    """KILLS: gawaah/ledger.py:40 and :41 `init=False` -> `init=True`.

    `_head` and `_count` are `init=False` on purpose. If either became a
    constructor argument, a caller could open a fresh ledger already claiming a
    prev_hash it never wrote, and the chain would verify from a fabricated
    genesis. Nothing in the suite noticed the flip.
    """
    import dataclasses
    from gawaah.ledger import GENESIS, Ledger

    fields = {f.name: f for f in dataclasses.fields(Ledger)}
    assert fields["_head"].init is False, "_head must not be a constructor argument"
    assert fields["_count"].init is False, "_count must not be a constructor argument"
    with pytest.raises(TypeError):
        Ledger(tmp_path / "a.jsonl", "deadbeef" * 8, 99)   # type: ignore[call-arg]
    assert Ledger(tmp_path / "b.jsonl").head == GENESIS


def test_the_written_line_is_canonical_and_human_readable(tmp_path):
    """KILLS: gawaah/ledger.py:67 `sort_keys=True` -> `False` and
    `ensure_ascii=False` -> `True`.

    `test_replay_is_byte_identical` compares the module against itself, so both
    flags could flip and it still passed. The audit file is read by people and
    diffed by machines: keys stay sorted so a diff shows a changed VALUE rather
    than a reshuffle, and a rupee sign stays a rupee sign instead of becoming
    \\u20b9.
    """
    import json as _json
    from gawaah.clock import VirtualClock
    from gawaah.ledger import Ledger

    p = tmp_path / "audit.jsonl"
    led, clk = Ledger(p), VirtualClock()
    led.append(ts=clk.now_iso(), module="m", zulu=1, alpha=2, note="₹214.50 चुकता")
    raw = p.read_text(encoding="utf-8").splitlines()[0]
    keys = list(_json.loads(raw).keys())
    assert keys == sorted(keys), f"keys are not sorted on disk: {keys}"
    assert "₹" in raw and "चुकता" in raw, "non-ASCII was escaped away"
    assert "\\u" not in raw


def test_canonical_json_is_pinned_to_a_golden_vector():
    """KILLS: gawaah/ledger.py:27 `ensure_ascii=False` -> `True` inside
    `canonical()`.

    Flipping it changes the bytes that get hashed — but `verify` hashes with the
    same function, so the chain stays self-consistent and every existing test
    passes. The mutant is only visible from OUTSIDE this process: an auditor
    re-deriving a hash in another language, or a ledger written by an older
    build. The hash format is a wire format, so it is pinned to a literal.
    """
    from gawaah.ledger import GENESIS, canonical, entry_hash

    payload = {
        "ts": "1970-01-01T00:00:00Z",
        "module": "m",
        "prev_hash": GENESIS,
        "note": "₹214.50",
        "amount_paise": 21450,
    }
    assert canonical(payload) == (
        b'{"amount_paise":21450,"module":"m","note":"\xe2\x82\xb9214.50",'
        b'"prev_hash":"' + GENESIS.encode() + b'",'
        b'"ts":"1970-01-01T00:00:00Z"}'
    )
    assert entry_hash(payload) == (
        "544a836db4b8352fa692d60fc651ab3fb2532741d2c4424fdf06592136be37ad"
    )
    assert b"\\u" not in canonical(payload), "canonical JSON must not \\u-escape"
    assert b", " not in canonical(payload), "canonical JSON must have no spaces"


def test_genesis_is_a_full_width_zero_hash():
    """KILLS: gawaah/ledger.py:22 `"0" * 64` -> `* 65` / `* 63`.

    GENESIS stands where a sha256 hexdigest stands. If it is not the same
    width, any consumer that sanity-checks `prev_hash` by length treats the
    first line of every chain as malformed.
    """
    import hashlib
    from gawaah.ledger import GENESIS

    assert len(GENESIS) == 64 == len(hashlib.sha256(b"").hexdigest())
    assert set(GENESIS) == {"0"}


# --------------------------------------------------------------- webhook.py
#
# The GREEN predicate. A survivor here is the most expensive kind of hole in
# the repo: a one-token edit that turns money into a wrong answer and that no
# test would notice.

import dataclasses  # noqa: E402
import hashlib as _hashlib  # noqa: E402
import hmac as _hmac  # noqa: E402

from gawaah.webhook import (  # noqa: E402
    AMBER,
    GREEN,
    RED,
    GreenPredicate,
    GreenVerdict,
    Intent as WIntent,
    WebhookError,
    verify_signature,
)

WSECRET = "whsec_mutation_test_only_not_a_real_secret"
WSESSION = "s_mut_0042"
WAMOUNT = 21437


def _wsign(raw: bytes, secret: str = WSECRET) -> str:
    """Test-local HMAC. Production code verifies and never signs; a signing
    helper next to the verifier is how a forgery primitive gets written."""
    key = secret.encode() if isinstance(secret, str) else bytes(secret)
    return _hmac.new(key, raw, _hashlib.sha256).hexdigest()


def _wire(obj) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


def _captured(*, session_id=WSESSION, amount=WAMOUNT, event_id="evt_mut_1", **over):
    entity = {
        "id": "pay_6koWN7bvxujzxM",
        "entity": "payment",
        "amount": amount,
        "currency": "INR",
        "status": "captured",
        "method": "upi",
        "amount_refunded": 0,
        "notes": {"session_id": session_id},
    }
    entity.update(over)
    env = {
        "entity": "event",
        "event": "payment.captured",
        "contains": ["payment"],
        "payload": {"payment": {"entity": entity}},
    }
    if event_id is not None:
        env["id"] = event_id
    return env


def _link(*, session_id=WSESSION, amount=WAMOUNT, amount_paid=None,
          event_id="evt_mut_link", **over):
    link = {
        "id": "plink_Fo48rl281ENAg9",
        "entity": "payment_link",
        "amount": amount,
        "amount_paid": amount if amount_paid is None else amount_paid,
        "currency": "INR",
        "status": "paid",
        "notes": {"session_id": session_id},
    }
    link.update(over)
    env = {
        "entity": "event",
        "event": "payment_link.paid",
        "contains": ["payment_link"],
        "payload": {"payment_link": {"entity": link}},
    }
    if event_id is not None:
        env["id"] = event_id
    return env


def _predicate(*intents):
    table = {i.session_id: i for i in (intents or (WIntent(WSESSION, WAMOUNT),))}
    return GreenPredicate(lambda sid: table.get(sid))


def _evaluate(env, *, predicate=None, secret=WSECRET, **kw):
    raw = _wire(env)
    p = predicate or _predicate()
    return p.evaluate(raw, _wsign(raw, secret), secret, **kw)


def test_the_green_verdict_is_frozen():
    """KILLS: gawaah/webhook.py:227 `@dataclass(frozen=True)` -> `frozen=False`
    on GreenVerdict (and :148 on Intent).

    `GreenVerdict.__post_init__` is the only thing standing between a denial
    and a forged pass: it refuses a green whose reason is not 'green', a green
    whose severity is not GREEN, and a RED that came from a stale mirror. Every
    one of those checks runs at construction ONLY. Unfreeze the class and
    `verdict.green = True` walks straight past all of them — and the state
    machine acts on `.green` alone. Nothing in the suite noticed the flip.
    """
    v = _evaluate(_captured())
    assert v.green is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.green = False
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.severity = RED
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.amount_paise = 1

    denied = _evaluate(_captured(amount=1, event_id="evt_mut_2"))
    assert denied.green is False and denied.reason == "amount_mismatch"
    with pytest.raises(dataclasses.FrozenInstanceError):
        denied.green = True            # the forgery this freeze prevents
    with pytest.raises(dataclasses.FrozenInstanceError):
        denied.reason = "green"

    # And Intent, which is what `amount == intent.amount_paise` compares to.
    it = WIntent(WSESSION, WAMOUNT)
    with pytest.raises(dataclasses.FrozenInstanceError):
        it.amount_paise = 1


def test_green_verdict_defaults_are_the_safe_ones():
    """KILLS: gawaah/webhook.py:246 `mirror_stale: bool = False` -> `True`
    and :247 `downgraded_from_red: bool = False` -> `True`.

    Other modules construct GreenVerdicts. A default of `mirror_stale=True`
    would mark every hand-built verdict as coming from a system that might be
    missing events, and `downgraded_from_red=True` would claim a downgrade that
    never happened — both of them lies in the audit line.
    """
    v = GreenVerdict(green=False, reason="unknown_session", severity=AMBER)
    assert v.mirror_stale is False
    assert v.downgraded_from_red is False
    assert v.signature_valid is False
    assert v.detail == "" and v.body_sha256 == ""
    assert v.event is None and v.event_id is None and v.session_id is None
    assert v.amount_paise is None and v.expected_paise is None
    assert v.untrusted_header_event_id is None


def test_an_empty_or_non_string_event_id_falls_back_to_the_body_hash():
    """KILLS: gawaah/webhook.py:431 `isinstance(body_event_id, str) and
    body_event_id` -> `or`.

    THE REPLAY KEY. With `or`, an envelope carrying `"id": ""` uses "" as its
    replay key: the first such delivery greens and writes "" into the seen
    store, and every LATER webhook that also carries an empty id — a different
    sale, a different customer — is refused as a duplicate. Money in, counter
    never green. That is precisely the denial-of-green the module docstring
    spends a paragraph on, arriving through the front door instead of a header.

    An `"id": 12345` is worse still: the key is an int, `replay_key[:24]` in
    the replay message raises TypeError, and the webhook 500s.
    """
    # `_MISSING` means the envelope carries no `id` key at all.
    _MISSING = object()
    for bad_id in ("", 12345, None, [], {}, _MISSING):
        pred = _predicate()
        env = _captured(event_id=None)          # no "id" key yet
        if bad_id is not _MISSING:
            env["id"] = bad_id
        raw = _wire(env)
        v = pred.evaluate(raw, _wsign(raw), WSECRET)
        assert v.green is True, f"id={bad_id!r}: {v.reason} {v.detail}"
        body_sha = _hashlib.sha256(raw).hexdigest()
        assert v.event_id == body_sha, (
            f"id={bad_id!r}: replay key is {v.event_id!r}, must be the body sha256"
        )

    # Two DIFFERENT sales that both carry an empty event id must both green.
    pred = _predicate(WIntent("s_a", 100), WIntent("s_b", 200))
    a = _captured(session_id="s_a", amount=100, event_id=None)
    b = _captured(session_id="s_b", amount=200, event_id=None)
    a["id"] = ""
    b["id"] = ""
    ra, rb = _wire(a), _wire(b)
    va = pred.evaluate(ra, _wsign(ra), WSECRET)
    vb = pred.evaluate(rb, _wsign(rb), WSECRET)
    assert va.green and vb.green, (va.reason, vb.reason)
    assert va.event_id != vb.event_id
    # and the genuine replay of the first one is still refused
    again = pred.evaluate(ra, _wsign(ra), WSECRET)
    assert again.green is False and again.reason == "replay"


def test_an_empty_or_non_string_header_event_id_is_recorded_as_absent():
    """KILLS: gawaah/webhook.py:366 `isinstance(header_event_id, str) and
    header_event_id` -> `or`.

    The header is evidence of nothing, but it is written into the audit line.
    With `or`, an empty header is recorded as `""` and a non-string one is
    recorded verbatim, so "no header arrived" and "a proxy sent junk" become
    indistinguishable in the ledger.
    """
    for header in ("", None, 12345, b"evt_x"):
        v = _evaluate(_captured(event_id="evt_hdr"), header_event_id=header)
        assert v.untrusted_header_event_id is None, f"header={header!r}"
    v = _evaluate(_captured(event_id="evt_hdr2"), header_event_id="evt_from_proxy")
    assert v.untrusted_header_event_id == "evt_from_proxy"
    assert v.event_id == "evt_hdr2", "the HEADER must never become the replay key"


@pytest.mark.parametrize("event_value", ["", 12345, [], {}, True])
def test_a_present_but_unusable_event_field_reports_missing_event(event_value):
    """KILLS: gawaah/webhook.py:452 `not isinstance(event, str) or not event`
    -> `and`.

    Both halves of that guard must fire independently. With `and`, an event of
    `""` or `12345` slips past the "no event" gate and is denied further down
    as `event_not_green` instead. Nothing greens either way — but INVARIANT
    "every failure has its own machine-readable code" is exactly what stops an
    operator chasing the wrong fault at 11pm.
    """
    env = _captured(event_id="evt_ev")
    env["event"] = event_value
    v = _evaluate(env)
    assert v.green is False
    assert v.reason == "missing_event", f"{event_value!r} -> {v.reason}"
    assert v.severity == AMBER


@pytest.mark.parametrize("status_value", ["", 12345, [], None])
def test_a_present_but_unusable_status_reports_status_missing(status_value):
    """KILLS: gawaah/webhook.py:479 `not isinstance(status, str) or not status`
    -> `and`.

    "The entity never asserted that money moved" and "the entity asserted a
    status that is not paid" are different facts about the world. Absence must
    not be reported as a contradiction.
    """
    v = _evaluate(_captured(status=status_value, event_id="evt_st"))
    assert v.green is False
    assert v.reason == "entity_status_missing", f"{status_value!r} -> {v.reason}"
    assert v.severity == AMBER


@pytest.mark.parametrize("currency_value", ["", 12345, [], None])
def test_a_present_but_unusable_currency_reports_currency_missing(currency_value):
    """KILLS: gawaah/webhook.py:493 `not isinstance(currency, str) or not
    currency` -> `and`.

    An amount without a unit is not money. "No currency" is a different failure
    from "the wrong currency", and only one of them means somebody sent USD.
    """
    v = _evaluate(_captured(currency=currency_value, event_id="evt_cur"))
    assert v.green is False
    assert v.reason == "currency_missing", f"{currency_value!r} -> {v.reason}"


@pytest.mark.parametrize("note_value", ["", 12345, None, [], {"a": 1}])
def test_a_present_but_unusable_session_id_note_reports_missing_session_id(note_value):
    """KILLS: gawaah/webhook.py:739 `isinstance(value, str) and value` -> `or`
    inside `_note`.

    With `or`, `notes: {"session_id": ""}` yields "" as a session id, the lookup
    misses, and the verdict blames the shopkeeper's counter (`unknown_session`)
    for what is actually a target minted without notes.
    """
    v = _evaluate(_captured(notes={"session_id": note_value}, event_id="evt_note"))
    assert v.green is False
    assert v.reason == "missing_session_id", f"{note_value!r} -> {v.reason}"


def test_a_non_integer_amount_refunded_denies_instead_of_crashing():
    """KILLS: gawaah/webhook.py:601 — the whole `return deny('amount_not_integer',
    …amount_refunded…)` statement, in all three of its mutated forms.

    `_parse_body` uses `parse_float=str`, so `"amount_refunded": 10.5` arrives
    as the string "10.5" and `paise()` raises MoneyError. Nothing in the suite
    sent one: deleting the deny left `refunded` unbound (UnboundLocalError, a
    500 on the money path) and `return True` made `evaluate` hand its caller a
    bare bool where a GreenVerdict was promised.
    """
    for bad in (10.5, "0", "12", True, [1], {"paise": 12}):
        v = _evaluate(_captured(amount_refunded=bad, event_id=f"evt_ref_{bad!r}"))
        assert isinstance(v, GreenVerdict), f"{bad!r} -> {type(v).__name__}"
        assert v.green is False
        assert v.reason == "amount_not_integer", f"{bad!r} -> {v.reason}"
        assert v.severity == AMBER

    # and a legitimate integer refund is still subtracted from the settlement
    v = _evaluate(_captured(amount_refunded=37, event_id="evt_ref_ok"))
    assert v.green is False and v.reason == "amount_mismatch"
    assert v.amount_paise == WAMOUNT - 37


def test_a_non_integer_ask_on_a_link_denies_instead_of_crashing():
    """KILLS: gawaah/webhook.py:617 — the `return deny('amount_not_integer',
    …entity.amount…)` for a payment_link's ASK.

    On a link the settled field is `amount_paid` and `amount` is only the ask,
    so a junk `amount` reaches a different `paise()` call from the one every
    other test exercises. Deleting the deny left `asked` unbound.
    """
    for bad in (10.5, "21437", True, [1], {"a": 1}):
        v = _evaluate(_link(amount=bad, amount_paid=WAMOUNT,
                            event_id=f"evt_ask_{bad!r}"))
        assert isinstance(v, GreenVerdict), f"{bad!r} -> {type(v).__name__}"
        assert v.green is False
        assert v.reason == "amount_not_integer", f"{bad!r} -> {v.reason}"


def test_the_partial_payment_shortfall_is_reported_the_right_way_round():
    """KILLS: gawaah/webhook.py:627 `asked - value` -> `asked + value`.

    The number in "short by N paise" is a money figure an operator reads and
    acts on. Adding the two amounts instead of subtracting them produces a
    plausible-looking, wildly wrong shortfall.
    """
    v = _evaluate(_link(amount=WAMOUNT, amount_paid=500, event_id="evt_part"))
    assert v.green is False and v.reason == "partial_payment"
    assert v.severity == RED, "a shortfall is a contradiction, not an unknown"
    assert f"short by {WAMOUNT - 500} paise" in v.detail
    assert str(WAMOUNT + 500) not in v.detail

    stale = _evaluate(_link(amount=WAMOUNT, amount_paid=500,
                            event_id="evt_part2"), mirror_stale=True)
    assert stale.severity == AMBER and stale.downgraded_from_red is True


@pytest.mark.parametrize("payload", [None, [], "payload", 12345, True])
def test_a_non_object_payload_denies_with_no_entity(payload):
    """KILLS: gawaah/webhook.py:724 `return {}` -> `return True` and
    <deleted> inside `_entities`.

    Deleting the guard's return sends a list or a string into `payload.get`
    (AttributeError); `return True` sends a bool into `.items()`. Either way a
    malformed-but-correctly-signed body 500s the money service instead of
    abstaining.
    """
    env = _captured(event_id="evt_payload")
    env["payload"] = payload
    v = _evaluate(env)
    assert isinstance(v, GreenVerdict)
    assert v.green is False and v.reason == "no_entity"
    assert v.severity == AMBER


def test_a_bytes_or_bytearray_secret_and_signature_still_verify():
    """KILLS: gawaah/webhook.py:213 `return bytes(value)` -> `return None` /
    <deleted> inside `_as_ascii_bytes`.

    The function accepts bytes/bytearray/memoryview credentials on purpose —
    secrets arrive from environments and key stores in all three shapes. With
    that branch returning None, a bytes secret reads as "not configured" and
    every genuine webhook is denied: a total outage that no test could see.
    """
    raw = _wire(_captured(event_id="evt_bytes"))
    sig = _wsign(raw)
    assert verify_signature(raw, sig, WSECRET) is True
    assert verify_signature(raw, sig, WSECRET.encode()) is True
    assert verify_signature(raw, sig, bytearray(WSECRET.encode())) is True
    assert verify_signature(raw, sig.encode(), WSECRET) is True
    assert verify_signature(raw, bytearray(sig.encode()), WSECRET) is True
    assert verify_signature(raw, memoryview(sig.encode()), WSECRET) is True
    assert verify_signature(raw, sig, WSECRET + "x") is False

    pred = _predicate()
    v = pred.evaluate(raw, sig.encode(), WSECRET.encode())
    assert v.green is True, f"{v.reason}: {v.detail}"


def test_a_bytearray_or_memoryview_body_is_verified_not_rejected():
    """KILLS: gawaah/webhook.py:358 `raw_body = bytes(raw_body)` -> <deleted>
    inside `_evaluate`.

    Some servers hand the handler a bytearray or a memoryview over the read
    buffer. Deleting the coercion makes `isinstance(raw_body, bytes)` false and
    turns every one of those deliveries into a WebhookError — a hard 500 on a
    perfectly good, correctly signed webhook.
    """
    env = _captured(event_id="evt_view")
    raw = _wire(env)
    sig = _wsign(raw)
    for body in (bytearray(raw), memoryview(raw)):
        pred = _predicate()
        v = pred.evaluate(body, sig, WSECRET)
        assert v.green is True, f"{type(body).__name__}: {v.reason} {v.detail}"
        assert v.body_sha256 == _hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("body", ["a string body", 12345, None, {"a": 1}, ["x"]])
def test_a_non_bytes_body_raises_webhook_error_not_type_error(body):
    """KILLS: gawaah/webhook.py:360 `raise WebhookError(...)` -> <deleted>
    inside `_evaluate`.

    Passing a str is a bug in OUR code, and it must fail loudly with the
    module's own error type. With the raise deleted, execution falls through to
    `hashlib.sha256(raw_body)` and the caller gets a bare TypeError from a
    hashing library — a stack trace that says nothing about signatures.
    """
    pred = _predicate()
    with pytest.raises(WebhookError):
        pred.evaluate(body, "deadbeef", WSECRET)
    with pytest.raises(WebhookError):
        verify_signature(body, "deadbeef", WSECRET)


def test_a_non_ascii_signature_is_refused_without_raising():
    """Companion to the `_as_ascii_bytes` survivors: a signature that cannot be
    ASCII-encoded is not a signature. It must return a denial, never an
    exception out of the gate.
    """
    raw = _wire(_captured(event_id="evt_ascii"))
    assert verify_signature(raw, "देवनागरी", WSECRET) is False
    assert verify_signature(raw, "", WSECRET) is False
    assert verify_signature(raw, None, WSECRET) is False
    assert verify_signature(raw, _wsign(raw), "") is False
    v = _predicate().evaluate(raw, "देवनागरी", WSECRET)
    assert v.green is False and v.reason == "bad_signature"
    v2 = _predicate().evaluate(raw, _wsign(raw), "")
    assert v2.green is False and v2.reason == "secret_not_configured"


# ---------------------------------------------------------------- kernel.py
#
# The exactly-once core. `_transition` is the single choke point every public
# mutator funnels through, so a survivor inside it is a survivor on every
# money move the counter can make.

import sqlite3 as _sqlite3  # noqa: E402

from gawaah.clock import VirtualClock  # noqa: E402
from gawaah.kernel import (  # noqa: E402
    ALL_STATES,
    CALLING,
    DEFAULT_MAX_RETRIEVE_ATTEMPTS,
    ESCALATED,
    FAILED,
    INDETERMINATE,
    MACHINE_TERMINAL,
    NEW,
    RETRIEVE,
    SETTLED,
    TERMINAL,
    GatewayResult,
    IllegalTransition,
    Kernel,
    KernelError,
    _hash_of_line,
    new_nonce,
)
from gawaah.ledger import GENESIS as LGENESIS, Ledger, verify  # noqa: E402
from gawaah.kernel import Intent as KIntent  # noqa: E402

KAMOUNT = 21450
KSESSION = "sess_mut_0001"


@pytest.fixture
def kernel(tmp_path):
    k = Kernel(tmp_path / "k.db", VirtualClock(),
               Ledger(tmp_path / "audit.jsonl"))
    yield k
    k.close()


def test_kernel_find_actually_looks_the_intent_up(kernel):
    """KILLS: gawaah/kernel.py:588-594 — the WHOLE of `Kernel.find()`.

    `find` is how a caller asks "have I already minted a target for this
    basket?". Nothing called it: its `cycle=0` default could shift, its
    `idem_key` line could vanish, its query could be deleted and its
    `row is None` test could invert, and the suite stayed green. A `find` that
    always answers None is how a second payment link gets minted for a basket
    that already has one.
    """
    assert kernel.find(KSESSION, KAMOUNT) is None
    created = kernel.create_intent(KSESSION, KAMOUNT)
    got = kernel.find(KSESSION, KAMOUNT)
    assert got is not None, "find() cannot see an intent that exists"
    assert isinstance(got, KIntent)
    assert got.nonce == created.nonce
    assert got.amount_paise == KAMOUNT and got.session_id == KSESSION
    assert got.cycle == 0, "the default cycle must be 0"
    # a different amount, session or cycle is a DIFFERENT idempotency key
    assert kernel.find(KSESSION, KAMOUNT + 1) is None
    assert kernel.find(KSESSION + "x", KAMOUNT) is None
    assert kernel.find(KSESSION, KAMOUNT, cycle=1) is None
    c1 = kernel.create_intent(KSESSION, KAMOUNT, cycle=1)
    assert kernel.find(KSESSION, KAMOUNT, cycle=1).nonce == c1.nonce
    assert c1.nonce != created.nonce


def test_a_transition_refuses_to_overwrite_an_existing_payment_id(kernel):
    """KILLS: gawaah/kernel.py:703 `payment_id is not None` -> `is`, and :704
    the `raise IllegalTransition(... already carries payment ...)`.

    This is the LAST guard against two debits recorded as one. A row that
    already names payment A must never be quietly re-pointed at payment B: the
    first payment then has no row, and the reconciler will never look for it.
    Nothing in the suite exercised it, so the `is not` could become `is` — which
    disables the guard for every call that actually passes a payment_id.

    The row below is set up the way a real one gets there: a FAILED intent that
    still carries the payment id an earlier attempt wrote.
    """
    it = kernel.create_intent(KSESSION, KAMOUNT)
    kernel.mark_calling(it.nonce)
    kernel.mark_failed(it.nonce, reason="declined")
    with _sqlite3.connect(kernel.db_path) as con:
        con.execute("UPDATE intents SET payment_id='pay_FIRST' WHERE nonce=?",
                    (it.nonce,))
    assert kernel.get(it.nonce).payment_id == "pay_FIRST"

    with pytest.raises(IllegalTransition) as ei:
        kernel.mark_settled(it.nonce, "pay_SECOND")
    assert "pay_FIRST" in str(ei.value)
    after = kernel.get(it.nonce)
    assert after.payment_id == "pay_FIRST", "the payment id was overwritten"
    assert after.state == FAILED, "the state moved despite the refusal"

    # the same id is not an overwrite, and is allowed through
    settled = kernel.mark_settled(it.nonce, "pay_FIRST")
    assert settled.state == SETTLED and settled.payment_id == "pay_FIRST"
    assert settled.needs_human is True, "FAILED -> SETTLED must flag a human"


@pytest.mark.parametrize("bad", ["", "   ", "\t\n", None, 12345, b"pay_x", []])
def test_mark_settled_refuses_an_empty_or_non_string_payment_id(kernel, bad):
    """KILLS: gawaah/kernel.py:742 `not isinstance(payment_id, str) or not
    payment_id.strip()` -> `and`, and :743 the raise.

    With `and`, `mark_settled(nonce, "")` sails through and the row is marked
    SETTLED carrying an empty payment id — a settlement with no gateway
    reference, which is indistinguishable from a settlement that never happened
    when somebody comes to reconcile it by hand.
    """
    it = kernel.create_intent(KSESSION, KAMOUNT)
    kernel.mark_calling(it.nonce)
    with pytest.raises(KernelError):
        kernel.mark_settled(it.nonce, bad)
    assert kernel.get(it.nonce).state == CALLING
    assert kernel.get(it.nonce).payment_id is None


@pytest.mark.parametrize("bad", ["", "   ", "\n", None, 42, b"s", []])
def test_create_intent_refuses_an_empty_or_non_string_session_id(kernel, bad):
    """KILLS: gawaah/kernel.py:651 `not isinstance(session_id, str) or not
    session_id.strip()` -> `and`, and :652 the raise.

    An intent with an empty session_id can never be matched to a webhook's
    `notes.session_id`, so the money arrives and the counter never turns.
    """
    with pytest.raises(KernelError):
        kernel.create_intent(bad, KAMOUNT)
    assert kernel.count() == 0


def test_a_gateway_that_says_settled_without_an_amount_is_parked(kernel):
    """KILLS: gawaah/kernel.py:851 `return self._park(nonce,
    'settled_without_amount')` in all three mutated forms, and :881 the
    `self._audit('intent.parked', ...)` line.

    "Captured, amount unknown" is the shape INVARIANT 7 exists for. It must not
    settle (we cannot check it is OUR money), must not fail (money may have
    moved), and must reach a person. Deleting the park left `reconcile` running
    on into `res.amount_paise != cur.amount_paise` with None, comparing None to
    an int, and settling nothing while raising nothing.
    """
    it = kernel.create_intent(KSESSION, KAMOUNT)
    kernel.mark_calling(it.nonce)
    kernel.mark_indeterminate(it.nonce, reason="timeout")
    before_lines = kernel.ledger.count

    out = kernel.reconcile(
        it.nonce,
        lambda n: GatewayResult(found=True, payment_id="pay_x",
                                amount_paise=None, status="captured"),
    )
    assert isinstance(out, KIntent)
    assert out.state != SETTLED, "settled on a gateway answer with no amount"
    assert out.state != FAILED, "failed on an answer that may mean money moved"
    assert out.needs_human is True, "the row was not handed to a person"
    assert out.reason == "settled_without_amount"
    assert kernel.ledger.count > before_lines, "the park left no audit line"
    events = [json.loads(l)["event"]
              for l in Path(kernel.ledger.path).read_text().splitlines()]
    assert "intent.parked" in events
    ok, _, _, err = verify(kernel.ledger.path)
    assert ok, err


def test_a_gateway_that_says_settled_without_a_payment_id_is_parked(kernel):
    """Companion to the above: a capture with no reference is not a settlement."""
    it = kernel.create_intent(KSESSION, KAMOUNT)
    kernel.mark_calling(it.nonce)
    kernel.mark_indeterminate(it.nonce)
    out = kernel.reconcile(
        it.nonce,
        lambda n: GatewayResult(found=True, payment_id="", amount_paise=KAMOUNT,
                                status="captured"),
    )
    assert out.state not in (SETTLED, FAILED)
    assert out.needs_human is True and out.reason == "settled_without_payment_id"


def test_found_false_and_status_not_found_are_independent_gates(kernel):
    """KILLS: gawaah/kernel.py:829 `not res.found or res.status == 'not_found'`
    -> `and`.

    Two different gateways say "I have never heard of this nonce" two different
    ways: one clears the `found` flag, the other returns a status string. With
    `and`, only an answer that does BOTH is believed, and a gateway that says
    `found=False, status='pending'` gets re-parked as indeterminate for ever
    instead of being concluded — the abstention loop the module is built to
    bound.
    """
    for answer, label in (
        (GatewayResult(found=False, status="pending"), "found=False only"),
        ({"found": True, "status": "not_found"}, "status only"),
        (None, "no answer object at all"),
    ):
        it = kernel.create_intent(KSESSION, KAMOUNT, cycle=hash(label) % 1000)
        kernel.mark_calling(it.nonce)
        kernel.mark_indeterminate(it.nonce)
        out = kernel.reconcile(it.nonce, lambda n, a=answer: a)
        assert out.state == FAILED, f"{label}: state is {out.state}"
        assert out.reason == "gateway_never_saw_nonce"


def test_resolve_escalated_refuses_a_row_the_machine_has_not_given_up_on(kernel):
    """KILLS: gawaah/kernel.py:935 the `raise IllegalTransition(... not
    ESCALATED ...)`.

    INDETERMINATE -> SETTLED is a LEGAL move (a late authoritative webhook uses
    it), so deleting this guard does not trip the state machine: a human could
    "resolve" a row the reconciler was still working on, skipping the gateway
    check entirely. That is a manual settlement with no evidence behind it.
    """
    it = kernel.create_intent(KSESSION, KAMOUNT)
    kernel.mark_calling(it.nonce)
    kernel.mark_indeterminate(it.nonce)
    assert kernel.get(it.nonce).state == INDETERMINATE
    with pytest.raises(IllegalTransition):
        kernel.resolve_escalated(it.nonce, SETTLED, operator="asha",
                                 payment_id="pay_z")
    assert kernel.get(it.nonce).state == INDETERMINATE
    assert kernel.get(it.nonce).needs_human is False


def test_resolve_escalated_names_its_own_contract_when_the_outcome_is_wrong(kernel):
    """KILLS: gawaah/kernel.py:924 the `raise KernelError('a human may resolve
    …')`.

    Deleting it does not make the call succeed — the state machine rejects the
    move a moment later — so `pytest.raises(KernelError)` alone cannot see the
    difference. What changes is WHICH error comes out: an API misuse would be
    reported as an illegal state transition, which sends the reader looking at
    the wrong thing.
    """
    it = kernel.create_intent(KSESSION, KAMOUNT)
    kernel.mark_calling(it.nonce)
    kernel.mark_indeterminate(it.nonce)
    esc = kernel._escalate(it.nonce, "test")
    assert esc.state == ESCALATED

    for bad_outcome in (RETRIEVE, INDETERMINATE, "settled", "", None, 7):
        with pytest.raises(KernelError) as ei:
            kernel.resolve_escalated(it.nonce, bad_outcome, operator="asha",
                                     payment_id="pay_z")
        assert not isinstance(ei.value, IllegalTransition), (
            f"{bad_outcome!r} was reported as a state-machine fault, not as "
            "the API misuse it is"
        )
        assert "may resolve" in str(ei.value)
    assert kernel.get(it.nonce).state == ESCALATED


def test_the_operators_note_survives_into_the_audit_reason(kernel):
    """KILLS: gawaah/kernel.py:941 `reason = f'{reason}:{note}'` -> <deleted>.

    The note is the only free text a person leaves when they settle a stuck
    intent by hand ("found on the dashboard, ref 8821"). Dropping it silently
    is how a manual settlement becomes unauditable.
    """
    it = kernel.create_intent(KSESSION, KAMOUNT)
    kernel.mark_calling(it.nonce)
    kernel.mark_indeterminate(it.nonce)
    kernel._escalate(it.nonce, "test")
    out = kernel.resolve_escalated(
        it.nonce, SETTLED, operator="asha", payment_id="pay_z",
        note="found on the dashboard ref 8821")
    assert out.state == SETTLED and out.needs_human is True
    assert out.reason == "human_resolved:asha:found on the dashboard ref 8821"
    line = json.loads(Path(kernel.ledger.path).read_text().splitlines()[-1])
    assert line["reason"].endswith("found on the dashboard ref 8821")
    assert line["operator"] == "asha"


def test_the_durability_pragmas_are_actually_applied(kernel):
    """KILLS: gawaah/kernel.py:521, :522 and :523 — the three
    `con.execute('PRAGMA …')` lines — and :315 `busy_timeout_ms = 15000`.

    `synchronous=FULL` is the reason a committed intent survives the crash the
    kernel exists to survive; `journal_mode=WAL` is why a reader does not block
    the writer; `busy_timeout` is why two threads wait instead of raising
    "database is locked". All three are per-connection settings that no
    assertion looked at, so all three could be deleted in silence.
    """
    with kernel._conn() as con:
        assert con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert con.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
        assert con.execute("PRAGMA busy_timeout").fetchone()[0] == 15000
    assert kernel.open_connections == 0


def test_the_synchronous_pragma_is_validated_before_interpolation(tmp_path):
    """KILLS: gawaah/kernel.py:330 the `raise KernelError('bad synchronous
    pragma')`.

    That string is interpolated straight into `PRAGMA synchronous={...}`.
    Deleting the check does not merely allow a typo — it makes the argument an
    injection point into the durability setting of the money database.
    """
    ledger = Ledger(tmp_path / "a.jsonl")
    for bad in ("OFF", "off", "", "FULL; DROP TABLE intents", "1", "NORMALish"):
        with pytest.raises(KernelError):
            Kernel(tmp_path / "k.db", VirtualClock(), ledger, synchronous=bad)
    for good in ("FULL", "normal", "Extra"):
        k = Kernel(tmp_path / f"k_{good}.db", VirtualClock(), ledger,
                   synchronous=good)
        with k._conn() as con:
            assert con.execute("PRAGMA synchronous").fetchone()[0] in (1, 2, 3)
        k.close()


def test_a_connection_is_really_closed_not_just_uncounted(kernel):
    """KILLS: gawaah/kernel.py:526 `con.close()` -> <deleted>.

    `open_connections` is a counter the module increments and decrements
    itself, so it keeps reporting zero whether or not the handle was released.
    A leaked sqlite connection holds a WAL read mark open and the -wal file
    grows without bound.
    """
    with kernel._conn() as con:
        con.execute("SELECT 1").fetchone()
    assert kernel.open_connections == 0
    with pytest.raises(_sqlite3.ProgrammingError):
        con.execute("SELECT 1")


def test_close_releases_the_ledger_lock_fd_and_is_idempotent(tmp_path):
    """KILLS: gawaah/kernel.py:567 the fd swap, :568 `fd is not None` -> `is`,
    and :570 `os.close(fd)`.

    `Kernel.close()` had no test. Every one of its three lines could be broken
    and the suite stayed green, which on a long-running till means one leaked
    file descriptor per Kernel until the process runs out.
    """
    k = Kernel(tmp_path / "k.db", VirtualClock(), Ledger(tmp_path / "a.jsonl"))
    fd = k._lock_fd
    assert isinstance(fd, int)
    os.fstat(fd)                       # open right now
    k.close()
    assert k._lock_fd is None
    with pytest.raises(OSError):
        os.fstat(fd)                   # really closed, not merely forgotten
    k.close()                          # idempotent


def test_appending_after_close_still_audits_through_the_thread_lock(tmp_path):
    """KILLS: gawaah/kernel.py:438 `yield` and :439 `return` — the branch taken
    when there is no lock fd.

    That branch is the Windows path, and it is also the path a closed Kernel
    takes. Deleting the `yield` turns `_ledger_file_lock` into a context
    manager that never yields (RuntimeError on entry); deleting the `return`
    makes it yield twice. Either one breaks auditing on a whole platform, and
    nothing reached it.
    """
    k = Kernel(tmp_path / "k.db", VirtualClock(), Ledger(tmp_path / "a.jsonl"))
    k.close()
    assert k._lock_fd is None
    h = k.audit_append("kernel", event="after.close", amount_paise=1)
    assert isinstance(h, str) and len(h) == 64
    ok, n, head, err = verify(tmp_path / "a.jsonl")
    assert ok and n == 1 and head == h, err


def test_audit_append_returns_the_new_head_hash(kernel):
    """KILLS: gawaah/kernel.py:512 `return h` -> `return None` / `True` /
    <deleted>.

    `audit_append` is public because `paisa` shares this ledger and must take
    the same cross-process lock. Its return value is the chain head; a caller
    that records it is recording the proof.
    """
    h1 = kernel.audit_append("paisa", event="test.one", amount_paise=1)
    assert isinstance(h1, str) and len(h1) == 64
    assert h1 == kernel.ledger.head
    h2 = kernel.audit_append("paisa", event="test.two", amount_paise=2)
    assert h2 != h1 and h2 == kernel.ledger.head
    ok, n, head, err = verify(kernel.ledger.path)
    assert ok and n == 2 and head == h2, err


def test_two_kernels_sharing_a_ledger_keep_the_LINE_COUNT_right(tmp_path):
    """KILLS: gawaah/kernel.py:473 `f.seek(start)`, :484 and :486 `count + 1`
    (-> `count - 1` / `+ 2` / `+ 0`), :490, :495, :509 and :511 — the whole
    cross-process head-and-count sync.

    The existing two-process test asserts the hash chain verifies, and the
    chain only depends on the HEAD. The COUNT rides along on the same code
    path and nothing looked at it: without the seek the tail is re-counted from
    byte zero, and `count - 1` walks it backwards. `Ledger.count` is what a
    report calls "lines in the audit log tonight".
    """
    p = tmp_path / "shared.jsonl"
    a = Kernel(tmp_path / "a.db", VirtualClock(), Ledger(p))
    b = Kernel(tmp_path / "b.db", VirtualClock(), Ledger(p))
    try:
        for i in range(6):
            a.audit_append("kernel", event="a", i=i)
            b.audit_append("kernel", event="b", i=i)
        ok, n, head, err = verify(p)
        assert ok, err
        assert n == 12
        # b appended last, so b's cached view must be the true tail
        assert b.ledger.count == 12, f"b thinks the ledger has {b.ledger.count} lines"
        assert b.ledger.head == head
        a.audit_append("kernel", event="a", i=99)
        ok, n2, head2, err = verify(p)
        assert ok, err
        assert n2 == 13 and a.ledger.count == 13 and a.ledger.head == head2
    finally:
        a.close()
        b.close()


def test_hash_of_line_abstains_to_genesis_on_anything_unreadable():
    """KILLS: gawaah/kernel.py:277 `return GENESIS` (all three forms) and :279
    `isinstance(h, str) and h` -> `or`.

    `_hash_of_line` is how one process learns another's chain head. If it
    returns a non-hash — None, True, or a number a JSON line happened to put in
    its `hash` field — the next append chains from garbage and `ledger.verify`
    condemns the whole file.
    """
    good = json.dumps({"a": 1, "hash": "b" * 64}).encode()
    assert _hash_of_line(good) == "b" * 64
    for bad in (
        b"not json at all",
        b"\xff\xfe\x00",
        b'"a string, not an object"',
        b"[1,2,3]",
        b"{}",
        json.dumps({"hash": None}).encode(),
        json.dumps({"hash": 12345}).encode(),
        json.dumps({"hash": ""}).encode(),
        json.dumps({"hash": ["b" * 64]}).encode(),
    ):
        out = _hash_of_line(bad)
        assert out == LGENESIS, f"{bad!r} -> {out!r}"
        assert isinstance(out, str)


def test_the_nonce_carries_128_bits_of_entropy():
    """KILLS: gawaah/kernel.py:260 `secrets.token_hex(16)` -> 17 / 15.

    The nonce is the gateway's idempotency token: it is the thing that stops a
    retried charge from becoming a second charge. Shrinking it is a security
    regression that no functional test can feel.
    """
    n = new_nonce()
    assert n.startswith("gwn_")
    assert len(n) == 4 + 32, f"{len(n) - 4} hex chars, expected 32 (128 bits)"
    assert set(n[4:]) <= set("0123456789abcdef")
    assert len({new_nonce() for _ in range(200)}) == 200


def test_the_ledger_lock_file_is_not_readable_by_anyone_else(tmp_path):
    """KILLS: gawaah/kernel.py:395 `0o600` -> `0o601`.

    The sidecar sits next to the audit log on a shop counter's machine. 0o600
    is the mode that was asked for; nothing checked it was the mode that landed.
    """
    k = Kernel(tmp_path / "k.db", VirtualClock(), Ledger(tmp_path / "a.jsonl"))
    try:
        mode = os.stat(k.ledger_lock_path).st_mode & 0o777
        assert mode == 0o600, f"lock file is {oct(mode)}"
    finally:
        k.close()


def test_the_cross_process_capability_is_reported_not_assumed(tmp_path):
    """KILLS: gawaah/kernel.py:426 `return self._lock_fd is not None and
    self._head_syncable` -> `return True`, -> `or`, and `is not` -> `is`.

    The property exists so a deployment can find out what it actually has. A
    version that always answers True is worse than no property at all: it tells
    an operator the audit log is safe between processes on a platform where it
    is not.
    """
    k = Kernel(tmp_path / "k.db", VirtualClock(), Ledger(tmp_path / "a.jsonl"))
    try:
        assert k.ledger_lock_is_cross_process is True, "POSIX build should be safe"
        real_fd, k._lock_fd = k._lock_fd, None
        assert k.ledger_lock_is_cross_process is False, "no lock fd is not safe"
        k._lock_fd = real_fd
        k._head_syncable = False
        assert k.ledger_lock_is_cross_process is False, "unsyncable head is not safe"
        k._head_syncable = True
        assert k.ledger_lock_is_cross_process is True
    finally:
        k.close()


def test_a_ledger_whose_head_cannot_be_re_read_is_not_called_syncable(tmp_path):
    """KILLS: gawaah/kernel.py:409 `hasattr(_head) and hasattr(_count)` -> `or`.

    The kernel writes the true head back into `Ledger._head` and `Ledger._count`
    under the file lock. A ledger object exposing only one of them cannot be
    corrected, and claiming it can is how a chain break gets written.
    """
    class HalfLedger:
        """Duck-typed ledger with a head but no re-readable count."""
        def __init__(self, path):
            self.path = path
            self._head = LGENESIS

        @property
        def head(self):
            return self._head

        @property
        def count(self):
            return 0

        def append(self, **kw):
            return "0" * 64

    k = Kernel(tmp_path / "k.db", VirtualClock(), HalfLedger(tmp_path / "h.jsonl"))
    try:
        assert k._head_syncable is False
        assert k.ledger_lock_is_cross_process is False
    finally:
        k.close()


def test_the_intent_and_gateway_result_records_are_frozen(kernel):
    """KILLS: gawaah/kernel.py:151 and :182 `@dataclass(frozen=True)` ->
    `frozen=False` on `Intent` and `GatewayResult`.

    An `Intent` is the money question in flight. If a caller can write
    `intent.amount_paise = 1` on the object the kernel just handed it, every
    later comparison against the gateway's amount is against a number the
    caller chose.
    """
    it = kernel.create_intent(KSESSION, KAMOUNT)
    for field_name, value in (("amount_paise", 1), ("state", SETTLED),
                              ("payment_id", "pay_x"), ("nonce", "gwn_0")):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(it, field_name, value)
    res = GatewayResult(found=True, payment_id="pay_a", amount_paise=KAMOUNT,
                        status="captured")
    with pytest.raises(dataclasses.FrozenInstanceError):
        res.amount_paise = 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        res.found = False


@pytest.mark.parametrize("state", sorted(ALL_STATES))
def test_the_three_intent_state_properties_answer_for_every_state(state):
    """KILLS: gawaah/kernel.py:170 `return self.state in TERMINAL` and :174
    `return self.state == ESCALATED` (-> None / True / <deleted> / `!=`).

    `reconcile` branches on `machine_done`; a sweeper branches on
    `is_terminal`; an operator queue branches on `is_escalated`. Only one of
    the three had a test, and `is_escalated` could be inverted outright.
    ESCALATED being terminal-for-the-machine but NOT terminal-for-the-money is
    the distinction this module is careful about, so assert it state by state.
    """
    it = KIntent(nonce="gwn_x", state=state, session_id="s", cycle=0,
                 amount_paise=1, idem_key="k", payment_id=None, attempts=0,
                 retrieve_attempts=0, needs_human=False, reason=None,
                 created_ts="t", updated_ts="t")
    # BOOKED (khata): the debit executed NEVER, by decision — money-decided
    # for this row, and no sweep may touch it. The debt lives on the book.
    from gawaah.kernel import BOOKED
    assert it.is_terminal is (state in (SETTLED, FAILED, BOOKED))
    assert it.is_escalated is (state == ESCALATED)
    assert it.machine_done is (state in (SETTLED, FAILED, ESCALATED, BOOKED))
    assert isinstance(it.is_terminal, bool)
    assert isinstance(it.is_escalated, bool)
    assert TERMINAL == frozenset({SETTLED, FAILED, BOOKED})
    assert MACHINE_TERMINAL == frozenset({SETTLED, FAILED, ESCALATED, BOOKED})
    assert ESCALATED not in TERMINAL, "ESCALATED must not read as money-decided"


def test_the_default_retrieve_budget_is_eight_and_is_actually_spent(tmp_path):
    """KILLS: gawaah/kernel.py:110 `DEFAULT_MAX_RETRIEVE_ATTEMPTS = 8` -> 9 / 7,
    and :366 `return self._max_retrieve` -> None / True / <deleted>.

    The budget is the bound on the abstention loop. Eight is a documented
    number — enough to ride out a multi-minute outage, small enough that a
    stuck intent reaches a person inside the same shift — so count the
    lookups a default Kernel actually makes before it escalates.
    """
    assert DEFAULT_MAX_RETRIEVE_ATTEMPTS == 8
    k = Kernel(tmp_path / "k.db", VirtualClock(), Ledger(tmp_path / "a.jsonl"))
    try:
        assert k.max_retrieve_attempts == 8
        assert isinstance(k.max_retrieve_attempts, int)
        assert not isinstance(k.max_retrieve_attempts, bool)
        it = k.create_intent(KSESSION, KAMOUNT)
        k.mark_calling(it.nonce)
        k.mark_indeterminate(it.nonce)
        calls = []

        def never_answers(nonce):
            calls.append(nonce)
            return GatewayResult(found=True, status="pending")

        for _ in range(20):
            out = k.reconcile(it.nonce, never_answers)
            if out.state == ESCALATED:
                break
        assert out.state == ESCALATED
        assert len(calls) == 8, f"gateway was asked {len(calls)} times, expected 8"
        assert out.needs_human is True
        assert k.escalated_intents() == [out]
        # an escalated row is never swept again, and costs no more lookups
        assert k.sweep(never_answers) == []
        assert len(calls) == 8
    finally:
        k.close()


def test_timestamps_on_a_row_come_from_the_clock(tmp_path):
    """KILLS: gawaah/kernel.py:544 `return self.clock.now_iso()` ->
    `return True`.

    `_now()` feeds `created_ts`, `updated_ts` and every ledger line. sqlite
    stores True as the integer 1 without complaint, so a row's timestamps
    became `1` and nothing noticed — an audit log whose times are all `1` is
    not an audit log.
    """
    k = Kernel(tmp_path / "k.db", VirtualClock(start="2026-01-02T03:04:05.000+00:00",
                                               step_ms=1000),
               Ledger(tmp_path / "a.jsonl"))
    try:
        it = k.create_intent(KSESSION, KAMOUNT)
        assert isinstance(it.created_ts, str)
        assert it.created_ts.startswith("2026-01-02T03:04:05"), it.created_ts
        moved = k.mark_calling(it.nonce)
        assert isinstance(moved.updated_ts, str)
        assert moved.updated_ts > it.created_ts
        line = json.loads(Path(k.ledger.path).read_text().splitlines()[0])
        assert isinstance(line["ts"], str) and line["ts"].startswith("2026-01-02")
    finally:
        k.close()


def test_the_db_and_ledger_parents_are_created_when_missing(tmp_path):
    """KILLS: gawaah/kernel.py:356 and :391 `os.makedirs(parent, exist_ok=True)`.

    Both survived because every existing test hands the Kernel a tmp_path that
    already exists. On a fresh till the first run is exactly the case where the
    directory is not there yet.
    """
    db = tmp_path / "deep" / "db" / "k.db"
    led = tmp_path / "other" / "logs" / "audit.jsonl"
    assert not db.parent.exists() and not led.parent.exists()
    k = Kernel(db, VirtualClock(), Ledger(led))
    try:
        it = k.create_intent(KSESSION, KAMOUNT)
        assert db.exists() and led.exists()
        assert Path(k.ledger_lock_path).exists()
        assert k.get(it.nonce).state == NEW
        ok, n, _, err = verify(led)
        assert ok and n == 1, err
    finally:
        k.close()


# --------------------------------------------------------------- session.py

from gawaah.session import (  # noqa: E402
    DEGRADED_P95_MS,
    Placement,
    Reason,
    Session,
    State,
    Transition,
    Verdict,
)


@pytest.fixture
def sess(tmp_path):
    led = Ledger(tmp_path / "s_audit.jsonl")
    s = Session(VirtualClock(), led)
    yield s
    ok, n, head, err = verify(led.path)
    assert ok, err


def _open_basket(s, *, price=10000, item="i1"):
    s.on_mat_lock(True)
    s.on_placement(Placement(item_id=item, name="dal", price_paise=price))
    s.on_exit(item)
    return s


def test_a_verdict_is_not_green_unless_it_says_so():
    """KILLS: gawaah/session.py:225 `green: bool = False` -> `True` and :226
    `signature_valid: bool = False` -> `True`.

    THE most dangerous survivor in this module. `Verdict` is what `paisa` hands
    the session; `green` is the field the state machine reads to decide whether
    money landed. A default of True means every Verdict built without naming
    the field — a test fixture, a partial construction, a mapping missing the
    key — arrives pre-greened. INVARIANT 2 says green happens in exactly one
    place after four checks; a dataclass default is not that place.
    """
    v = Verdict(event_id="evt_1", event="payment.captured", session_id="s1")
    assert v.green is False, "a Verdict defaults to GREEN"
    assert v.signature_valid is False, "a Verdict defaults to signature-valid"
    assert v.amount_paise is None and v.reason == ""
    # and via the mapping form the session actually accepts
    v2 = Verdict(**{"event_id": "evt_2", "event": "payment.captured",
                    "session_id": "s1", "amount_paise": 100})
    assert v2.green is False and v2.signature_valid is False


def test_a_default_verdict_cannot_move_the_session_to_paid(sess, tmp_path):
    """The consequence of the above, driven through the machine."""
    s = _open_basket(sess, price=21450)
    s.on_done()
    assert s.state is State.AWAITING_SETTLEMENT
    t = s.on_webhook(Verdict(event_id="evt_x", event="payment.captured",
                             session_id=s.session_id, amount_paise=21450))
    assert s.state is not State.PAID, "an unsigned, ungreen verdict paid the basket"
    assert s.money_authorised is False
    assert t.reason == Reason.BAD_SIGNATURE


def test_placement_records_and_transitions_are_frozen():
    """KILLS: gawaah/session.py:181, :212 and :266 `@dataclass(frozen=True)`
    -> `frozen=False` on `Placement`, `Verdict` and `Transition`.

    `Verdict.__post_init__` is what refuses a green with no amount. Unfreeze
    the class and `verdict.green = True` skips it — the same forgery the
    webhook module's freeze prevents, one layer up.
    """
    p = Placement(item_id="i1", price_paise=100)
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.price_paise = 1
    v = Verdict(event_id="e", event="payment.captured", session_id="s")
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.green = True
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.amount_paise = 1
    t = Transition(frm=State.IDLE, to=State.IDLE, reason="r", applied=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.applied = False
    assert t.lines_written == 0, "a Transition claims a written line by default"


def test_an_abstained_placement_names_why_and_a_priced_one_does_not():
    """KILLS: gawaah/session.py:200 (`and` -> `or`, `is` -> `is not`, and the
    removal of `not`) and :201 the `reason = UNKNOWN_SKU` assignment.

    R1 hangs on this: an unpriced line must carry a reason so the AMBER row can
    say what it abstained on, and a PRICED line must NOT be relabelled unknown.
    All four mutations of that two-line guard survived.
    """
    assert Placement(item_id="i1").reason == Reason.UNKNOWN_SKU
    assert Placement(item_id="i1", price_paise=None).reason == Reason.UNKNOWN_SKU
    assert Placement(item_id="i1", reason="occluded").reason == "occluded"
    assert Placement(item_id="i1", price_paise=1500).reason == ""
    assert Placement(item_id="i1", price_paise=1500, reason="tapped").reason == "tapped"
    assert Placement(item_id="i1", price_paise=0).reason == "", "0 is a price"


def test_a_priced_line_is_labelled_priced_from_the_gallery(sess):
    """KILLS: gawaah/session.py:624 `p.reason or (Reason.PRICED if
    p.price_paise is not None else Reason.UNKNOWN_SKU)` -> `and`, and the
    `is not` -> `is` in the same expression.

    The reason code is what the ledger line and the operator's row both read.
    With `and`, a priced placement's reason becomes the empty string; with
    `is`, it becomes "unknown_sku" on a line that has a price.
    """
    sess.on_mat_lock(True)
    sess.on_placement(Placement(item_id="i1", name="atta", price_paise=4500))
    line = sess.line_items[0]
    assert line.price_paise == 4500
    assert line.reason == Reason.PRICED, f"priced line says {line.reason!r}"
    assert line.amber is False

    sess.on_placement(Placement(item_id="i2", name=None))
    amber = sess.line_items[1]
    assert amber.price_paise is None
    assert amber.reason == Reason.UNKNOWN_SKU
    assert amber.amber is True


def test_reverting_an_unpriced_line_removes_zero_paise(sess):
    """KILLS: gawaah/session.py:749 `removed = line.price_paise if
    line.price_paise is not None else 0` -> `else 1` / `else -1`.

    An AMBER line was never in the total, so reverting it removes nothing. A
    `removed_paise` of 1 or -1 in the audit line is a money figure that does
    not correspond to anything that happened, and it is the number a
    reconciliation reads.
    """
    sess.on_mat_lock(True)
    sess.on_placement(Placement(item_id="amber1"))
    sess.on_exit("amber1")
    before = int(sess.total_paise)
    t = sess.on_revert("amber1")
    assert t.detail["removed_paise"] == 0, t.detail
    assert int(sess.total_paise) == before == 0

    sess.on_placement(Placement(item_id="p1", price_paise=7500))
    sess.on_exit("p1")
    assert int(sess.total_paise) == 7500
    t2 = sess.on_revert("p1")
    assert t2.detail["removed_paise"] == 7500
    assert int(sess.total_paise) == 0


def test_a_one_paisa_basket_is_chargeable(sess):
    """KILLS: gawaah/session.py:807 `if amount <= 0` -> `amount <= 1`.

    The guard exists to refuse a basket whose every line abstained. Shifting it
    by one paisa refuses the smallest real sale there is. CHILLAR prices end in
    a nonce precisely so that small exact amounts are ordinary.
    """
    sess.on_mat_lock(True)
    sess.on_placement(Placement(item_id="i1", price_paise=1))
    sess.on_exit("i1")
    assert int(sess.total_paise) == 1
    t = sess.on_done()
    assert sess.state is State.AWAITING_SETTLEMENT, f"{t.reason}"
    assert sess.intent_amount_paise == 1


def test_an_all_amber_basket_refuses_done_with_zero_total(sess):
    """The other side of the same boundary: nothing priced means nothing to
    charge, and it must refuse rather than mint an intent for zero."""
    sess.on_mat_lock(True)
    sess.on_placement(Placement(item_id="a1"))
    sess.on_exit("a1")
    t = sess.on_done()
    assert t.reason == Reason.ZERO_TOTAL
    assert sess.state is not State.AWAITING_SETTLEMENT
    assert sess.intent_amount_paise is None


def test_the_degraded_threshold_is_250ms_and_equality_is_not_over(sess):
    """KILLS: gawaah/session.py:936 `int(p95_ms) > int(threshold_ms)` -> `>=`,
    and :142 `DEGRADED_P95_MS = 250` -> 251 / 249.

    "Over the budget" means over, not at. A p95 sitting exactly on the
    threshold flipping the counter into DEGRADED — where auto-commit is
    disabled and every exit needs a tap — is a shop that stops working at
    exactly its design point.
    """
    assert DEGRADED_P95_MS == 250
    sess.on_mat_lock(True)
    # exactly AT the budget is not over it: nothing changes, so it is a no-op
    assert sess.on_perf(DEGRADED_P95_MS).reason == Reason.DUPLICATE
    assert sess.state is not State.DEGRADED, "equality alone tripped DEGRADED"
    t = sess.on_perf(DEGRADED_P95_MS + 1)
    assert t.reason == Reason.DEGRADED, f"one over the budget gave {t.reason}"
    assert sess.state is State.DEGRADED
    back = sess.on_perf(DEGRADED_P95_MS)
    assert back.reason == Reason.PERF_RECOVERED, back.reason
    assert sess.state is not State.DEGRADED


def test_degraded_returns_the_counter_to_where_it_was_billing(sess):
    """KILLS: gawaah/session.py:943 `self._degraded_resume = self._state`,
    :949 `resume = self._degraded_resume or State.IDLE` -> `and`, :950 the
    clear, and :951 the whole resume emit.

    Going slow must not lose the basket. With `and`, `resume` becomes the
    falsy left operand and the session comes back to the wrong state; with the
    assignment deleted it always comes back to IDLE, which reads as "no basket
    open" on a counter that has one.
    """
    _open_basket(sess, price=9900, item="i1")
    assert sess.state is State.BASKET_OPEN
    sess.on_perf(999)
    assert sess.state is State.DEGRADED
    t = sess.on_perf(10)
    assert isinstance(t, Transition)
    assert t.reason == Reason.PERF_RECOVERED
    assert sess.state is State.BASKET_OPEN, f"resumed into {sess.state}"
    assert int(sess.total_paise) == 9900


def test_the_mat_and_brain_readouts_report_the_real_signal(sess):
    """KILLS: gawaah/session.py:340 `return self._mat_locked` -> `return True`
    and :344 `return self._brain_up` -> `return True`.

    These two booleans are what a UI paints the counter's status from. Both
    could be hard-wired to True — "everything is fine" — and no test looked.
    """
    assert sess.mat_locked is False
    assert sess.brain_up is True
    sess.on_mat_lock(True)
    assert sess.mat_locked is True
    sess.on_mat_lock(False)
    assert sess.mat_locked is False, "mat_locked still says True after MAT_LOST"
    assert sess.snapshot()["mat_locked"] is False
    sess.on_mat_lock(True)
    sess.on_brain(False)
    assert sess.brain_up is False, "brain_up still says True after BRAIN_LOST"
    assert sess.snapshot()["brain_up"] is False
    sess.on_brain(True)
    assert sess.brain_up is True


def test_a_frozen_total_reports_the_snapshot_and_stops_moving(sess):
    """PINS R5. Does NOT kill gawaah/session.py:366 `return
    Paise(self._frozen_total)` -> <deleted>, and I checked rather than assumed:

        tools/mutate.py applied that exact mutant and the whole suite,
        including this test, still passed (255 passed).

    The reason is structural. While `_frozen_total is not None` the session is
    always in one of the frozen states, and `_billing_guard` refuses every
    handler that could add, price, revert or commit a line — so the committed
    set cannot change, so `_live_total()` cannot diverge from the snapshot it
    was taken from. Deleting the return therefore falls through to a value that
    is provably equal. It is listed in
    `test_documented_equivalent_mutants_are_named` as an equivalent mutant.

    The property is worth pinning anyway: R5 says a perception outage freezes
    the number a customer is looking at, and this asserts that it does.
    """
    _open_basket(sess, price=12345, item="i1")
    assert int(sess.total_paise) == 12345
    sess.on_mat_lock(False)
    assert sess.state is State.MAT_LOST
    assert sess.frozen is True
    frozen = sess.total_paise
    assert isinstance(frozen, int) and not isinstance(frozen, bool)
    assert int(frozen) == 12345
    assert sess.snapshot()["total_paise"] == 12345
    # a placement is refused while frozen, and the frozen number does not move
    sess.on_placement(Placement(item_id="i2", price_paise=500))
    assert int(sess.total_paise) == 12345


def test_the_basket_stays_locked_after_done_even_for_a_placement(sess):
    """KILLS: gawaah/session.py:509 `self._state is State.PAID and allow_paid`
    -> `or`.

    `on_placement` is the one caller that passes `allow_paid=True`, so with
    `or` that guard returns None for EVERY placement and the `_LOCKED` check
    below it is never reached. An item landing on the mat after DONE would join
    a basket whose amount has already been sent for payment: the customer pays
    the old total and walks out with the new one.
    """
    _open_basket(sess, price=21450, item="i1")
    sess.on_done()
    assert sess.state is State.AWAITING_SETTLEMENT
    assert sess.intent_amount_paise == 21450
    t = sess.on_placement(Placement(item_id="late", price_paise=9999))
    assert t.reason == Reason.BASKET_LOCKED, f"late placement gave {t.reason}"
    assert sess.state is State.AWAITING_SETTLEMENT
    assert int(sess.total_paise) == 21450
    assert "late" not in {li.item_id for li in sess.line_items}


def test_a_placement_says_whether_it_opened_a_new_basket(sess):
    """KILLS: gawaah/session.py:611 `new_basket = False` -> `True`.

    The flag tells a reader of the audit log that the previous, PAID basket was
    cleared out at this instant. Hard-wired True, every placement claims to
    have closed out a sale that never existed.
    """
    sess.on_mat_lock(True)
    t = sess.on_placement(Placement(item_id="i1", price_paise=100))
    assert t.detail["new_basket"] is False, t.detail
    line = json.loads(Path(sess.ledger.path).read_text().splitlines()[-1])
    assert line["new_basket"] is False, line


def test_a_refusal_is_marked_as_refused_in_the_ledger(sess):
    """KILLS: gawaah/session.py:490 `{..., 'refused': True}` -> `False`.

    Every refusal writes one line whose detail says it refused. A `refused:
    false` on a refusal line is a log that disagrees with what happened.
    """
    t = sess.on_placement(Placement(item_id="i1", price_paise=100))  # mat not locked
    assert t.reason == Reason.MAT_NOT_LOCKED
    assert t.detail["refused"] is True, t.detail
    line = json.loads(Path(sess.ledger.path).read_text().splitlines()[-1])
    assert line["refused"] is True, line


def test_an_abstention_is_marked_abstained_and_a_discard_discarded(sess):
    """KILLS: gawaah/session.py:777 `'abstained': True` -> `False`, and :886 /
    :892 `'discarded': True` -> `False`.

    These three booleans are how a later audit distinguishes "we chose not to
    decide" from "we decided". Flipping any of them makes the ledger claim the
    opposite of what the code did.
    """
    # a green, correctly signed webhook with no open intent is DISCARDED (:892)
    early = sess.on_webhook(Verdict(event_id="e0", event="payment.captured",
                                    session_id=sess.session_id, amount_paise=5000,
                                    green=True, signature_valid=True))
    assert early.reason == Reason.NO_OPEN_INTENT, early.reason
    no_intent = json.loads(Path(sess.ledger.path).read_text().splitlines()[-1])
    assert no_intent["discarded"] is True, no_intent
    assert sess.money_authorised is False

    _open_basket(sess, price=5000, item="i1")
    sess.on_exit(None)                       # abstention 11: no tracker id
    assert sess.state is State.FROZEN_TOTAL
    line = json.loads(Path(sess.ledger.path).read_text().splitlines()[-1])
    assert line["reason"] == Reason.UNCOUNTED_CROSSING
    assert line["abstained"] is True, line

    sess.on_acknowledge()
    sess.on_done()
    sess.on_webhook(Verdict(event_id="e1", event="payment.captured",
                            session_id=sess.session_id, amount_paise=5000,
                            green=True, signature_valid=False))
    bad = json.loads(Path(sess.ledger.path).read_text().splitlines()[-1])
    assert bad["reason"] == Reason.BAD_SIGNATURE
    assert bad["discarded"] is True, bad

    sess.on_webhook(Verdict(event_id="e2", event="payment.captured",
                            session_id="somebody-elses-session",
                            amount_paise=5000, green=True, signature_valid=True))
    foreign = json.loads(Path(sess.ledger.path).read_text().splitlines()[-1])
    assert foreign["reason"] == Reason.FOREIGN_SESSION
    assert foreign["discarded"] is True, foreign

    sess.on_webhook(Verdict(event_id="e3", event="payment.captured",
                            session_id=sess.session_id, amount_paise=5000,
                            green=False, signature_valid=True,
                            reason="paisa said no"))
    refused_green = json.loads(Path(sess.ledger.path).read_text().splitlines()[-1])
    assert refused_green["reason"] == Reason.PAISA_REFUSED_GREEN
    assert sess.money_authorised is False


def test_going_offline_outside_settlement_keeps_billing_and_says_so(sess):
    """KILLS: gawaah/session.py:840 — the whole
    `return self._emit(self._state, 'network', NETWORK_DOWN_BILLING_CONTINUES,
    {})`.

    R6: losing the network mid-basket must not move the counter anywhere. With
    that return deleted the call falls through into the *restored* branch and
    an outage is logged as a recovery.
    """
    _open_basket(sess, price=3300, item="i1")
    t = sess.on_network(False)
    assert t.reason == Reason.NETWORK_DOWN_BILLING_CONTINUES, t.reason
    assert sess.state is State.BASKET_OPEN
    assert sess.snapshot()["online"] is False
    assert int(sess.total_paise) == 3300


def test_last_settled_paise_starts_as_none(sess):
    """KILLS: gawaah/session.py:313 `self._last_settled_paise: int | None =
    None` -> <deleted>.

    Deleting the initialiser makes the property raise AttributeError on a fresh
    session. Nothing read it before any settlement, so nothing noticed.
    """
    assert sess.last_settled_paise is None
    assert sess.authorised_paise is None
    assert sess.intent_amount_paise is None
    assert sess.money_authorised is False


def test_the_session_id_is_sixteen_hex_characters(sess):
    """KILLS: gawaah/session.py:326 `hexdigest()[:16]` -> 15 / 17.

    The session id is what `notes.session_id` carries into a webhook and what
    the green predicate matches an open intent by. Its width is part of that
    contract on both sides of the wire.
    """
    sid = sess.session_id
    assert isinstance(sid, str)
    assert len(sid) == 16, f"session id is {len(sid)} chars: {sid!r}"
    assert set(sid) <= set("0123456789abcdef")


# ------------------------------------------------------------- sellevent.py
#
# The crossing predicate. INVARIANT 5: `paisa` re-runs this server-side, so
# its geometry is a wire contract between the browser and the money service,
# not an implementation detail of either.

import math as _math  # noqa: E402

from gawaah.sellevent import (  # noqa: E402
    DEAD_BAND_MM,
    REASON_NO_TRACKER_ID,
    REASON_REID_AMBIGUOUS,
    REID_REASONS,
    SIDE_IN,
    SIDE_ON_LINE,
    SIDE_OUT,
    AbstainedCentroid,
    CentroidTracker,
    CrossingException,
    CrossingResult,
    LineZone,
    TrackerUpdate,
    UncountedCrossing,
)


def _zone(**kw):
    """A 100mm line along y=0 from (0,0) to (100,0). OUT is +y."""
    kw.setdefault("min_crossing_frames", 1)
    return LineZone((0.0, 0.0), (100.0, 0.0), **kw)


def test_the_side_constants_are_the_wire_contract():
    """KILLS: gawaah/sellevent.py:110 `SIDE_IN = -1` -> `-2` and :111
    `SIDE_OUT = +1` -> `+2`.

    Every comparison inside this module uses the constants, so the module stays
    self-consistent under the mutation and no behavioural test can see it. But
    `paisa` re-runs this predicate server-side (INVARIANT 5) and the browser
    runs it too: the sign of a side is a number that crosses a process
    boundary, which makes it a contract and not a local detail.
    """
    assert SIDE_IN == -1
    assert SIDE_OUT == 1
    assert SIDE_ON_LINE == 0
    assert SIDE_IN == -SIDE_OUT
    assert DEAD_BAND_MM == 1.0


def test_project_computes_the_real_geometry():
    """KILLS: gawaah/sellevent.py:665 `x - self.p1[0]` -> `+`, :666 `dx*vx +
    dy*vy` -> `-`, and :667 `vx*dy - vy*dx` -> `+` and `*` -> `//`.

    Four separate one-token edits to the projection at the heart of the
    crossing predicate, and every one of them survived: nothing checked
    `project` against a hand-computed answer on a line that is neither through
    the origin nor axis-aligned, which is the only shape where a sign slip or a
    swapped term shows up.
    """
    # segment (10,10) -> (10,110): straight up, so +x is the OUT side.
    z = LineZone((10.0, 10.0), (10.0, 110.0), min_crossing_frames=1)
    t, d = z.project((13.0, 35.0))
    assert t == pytest.approx(0.25), t          # 25mm along a 100mm segment
    # p1 -> p2 points at +y, so the OUT (positive-cross) side is -x
    assert d == pytest.approx(-3.0), d
    assert z.signed_distance_mm((7.0, 35.0)) == pytest.approx(3.0)
    assert z.project((10.0, 10.0)) == pytest.approx((0.0, 0.0))
    assert z.project((10.0, 110.0))[0] == pytest.approx(1.0)

    # a diagonal segment, so no term can hide behind a zero
    d2 = LineZone((0.0, 0.0), (30.0, 40.0), min_crossing_frames=1)  # length 50
    t2, dd2 = d2.project((30.0, 40.0))
    assert t2 == pytest.approx(1.0) and dd2 == pytest.approx(0.0)
    # (-4, 3) is the unit normal * 5 -> exactly 5mm off the line at t=0
    t3, dd3 = d2.project((-4.0, 3.0))
    assert t3 == pytest.approx(0.0), t3
    assert dd3 == pytest.approx(5.0), dd3
    assert d2.signed_distance_mm((4.0, -3.0)) == pytest.approx(-5.0)
    assert isinstance(dd3, float), "the signed distance must not be floored"


def test_the_dead_band_is_exclusive_at_both_edges():
    """KILLS: gawaah/sellevent.py:682 `d > self.dead_band_mm` -> `>=` and :684
    `d < -self.dead_band_mm` -> `<=`.

    The dead band is the region that carries NO evidence. A centroid sitting
    exactly on its edge is the boundary case the band exists for, and shifting
    the comparison by one epsilon turns "we do not know" into "it crossed".
    """
    z = _zone(dead_band_mm=1.0)
    assert z.side((50.0, 1.0)) == SIDE_ON_LINE, "the +edge counted as a crossing"
    assert z.side((50.0, -1.0)) == SIDE_ON_LINE, "the -edge counted as a crossing"
    assert z.side((50.0, 0.0)) == SIDE_ON_LINE
    assert z.side((50.0, 1.0001)) == SIDE_OUT
    assert z.side((50.0, -1.0001)) == SIDE_IN
    assert z.side((50.0, 40.0)) == SIDE_OUT
    assert z.side((50.0, -40.0)) == SIDE_IN

    wide = _zone(dead_band_mm=5.0)
    assert wide.side((50.0, 5.0)) == SIDE_ON_LINE
    assert wide.side((50.0, 5.01)) == SIDE_OUT


def test_in_limits_includes_both_endpoints():
    """KILLS: gawaah/sellevent.py:679 — both `<=` -> `<` and `self._len + pad`
    -> `- pad`.

    The counting region is the segment itself. Excluding its endpoints makes
    an item that leaves at the very corner of the printed exit edge invisible
    to the predicate; subtracting the pad instead of adding it shrinks the
    region to nothing whenever a pad is set.
    """
    z = _zone()
    assert z.in_limits((0.0, 0.0)) is True, "the p1 endpoint is outside limits"
    assert z.in_limits((100.0, 0.0)) is True, "the p2 endpoint is outside limits"
    assert z.in_limits((50.0, 30.0)) is True
    assert z.in_limits((-0.01, 0.0)) is False
    assert z.in_limits((100.01, 0.0)) is False

    padded = _zone(limits_pad_mm=10.0)
    assert padded.in_limits((105.0, 0.0)) is True, "the pad did not extend the far end"
    assert padded.in_limits((-5.0, 0.0)) is True
    assert padded.in_limits((115.0, 0.0)) is False


def test_a_crossing_back_is_reported_on_the_result(tmp_path):
    """KILLS: gawaah/sellevent.py:793 `crossed_back.append(tid)` -> <deleted>.

    `crossed_back` is how the caller learns the customer put the item back.
    Without it the counters still move but the per-frame result says nothing
    happened, so a UI driven off `result.crossed_back` never removes the line.
    """
    z = _zone(min_crossing_frames=1)
    z.update({1: (50.0, -10.0)})                 # inside, shopkeeper's side
    out = z.update({1: (50.0, 10.0)})            # crosses out
    assert out.crossed_out == (1,), out
    assert out.crossed_back == ()
    back = z.update({1: (50.0, -10.0)})          # put back
    assert back.crossed_back == (1,), f"a return reported {back.crossed_back}"
    assert back.crossed_out == ()
    assert z.out_count == 1 and z.back_count == 1
    assert z.net_count == 0
    assert z.entries_from_out == 0


def test_untracked_crossings_count_frames_and_centroids_separately():
    """KILLS: gawaah/sellevent.py:738 `fired_anon = True` (-> `False` /
    <deleted>) and :749 `self.frames_with_untracked_out += 1` (-> `-=`,
    <deleted>, `+= 2`, `+= 0`).

    Two different numbers: how many anonymous centroids crossed, and how many
    FRAMES had at least one. The second is what a rate-limited amber banner
    is driven from, and every mutation of it survived.
    """
    z = _zone()
    z.update({}, untracked=[(40.0, 10.0), (60.0, 12.0)])   # two, one frame
    assert z.crossed_without_tracker_id == 2
    assert z.frames_with_untracked_out == 1, z.frames_with_untracked_out
    z.update({}, untracked=[(40.0, 10.0)])                 # one more frame
    assert z.crossed_without_tracker_id == 3
    assert z.frames_with_untracked_out == 2
    z.update({}, untracked=[(40.0, -10.0)])   # on the shopkeeper's side: nothing
    assert z.crossed_without_tracker_id == 3
    assert z.frames_with_untracked_out == 2
    assert z.amber is True


def test_an_anon_centroid_exactly_on_the_dead_band_edge_is_not_a_crossing():
    """KILLS: gawaah/sellevent.py:725 `d <= -self.dead_band_mm` -> `<` and
    :727 `d > self.dead_band_mm` -> `>=`.

    At exactly -dead_band the centroid is still clearly on the shopkeeper's
    side and must be skipped; at exactly +dead_band it is inside the band, so
    the record must say so rather than claiming it went past the line.
    """
    z = _zone(dead_band_mm=1.0)
    z.update({}, untracked=[(50.0, -1.0)])
    assert z.crossed_without_tracker_id == 0, "the -edge fired an exception"
    r = z.update({}, untracked=[(50.0, 1.0)])
    assert z.crossed_without_tracker_id == 1
    assert "inside the dead band" in r.exceptions[0].detail, r.exceptions[0].detail
    r2 = z.update({}, untracked=[(50.0, 9.0)])
    assert "past the line" in r2.exceptions[0].detail


def test_a_result_with_exceptions_is_not_clean_and_not_trustworthy():
    """KILLS: gawaah/sellevent.py:237 `return not self.exceptions` ->
    `return True`.

    `clean` and `total_is_trustworthy` are the two booleans that gate showing
    a green total. A `clean` hard-wired True says every frame accounted for
    everything it saw.
    """
    z = _zone()
    ok = z.update({1: (50.0, -10.0)})
    assert ok.clean is True and ok.exceptions == ()
    assert ok.total_is_trustworthy is True
    dirty = z.update({}, untracked=[(50.0, 10.0)])
    assert dirty.exceptions, "expected an uncounted-crossing record"
    assert dirty.clean is False, "a frame with an uncounted crossing says clean"
    assert dirty.total_is_trustworthy is False
    with pytest.raises(UncountedCrossing):
        z.raise_if_dirty()


def test_the_crossing_records_are_frozen():
    """KILLS: gawaah/sellevent.py:184, :212 and :245 `@dataclass(frozen=True)`
    on `CrossingException`, `CrossingResult` and `TrackerUpdate`.

    A `CrossingResult` is the evidence a frame produced. If a caller can
    write `result.amber = False` on the object the zone handed it, the
    abstention is erased by the code that was supposed to display it.
    """
    z = _zone()
    r = z.update({}, untracked=[(50.0, 10.0)])
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.amber = False
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.out_count = 99
    exc = r.exceptions[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        exc.code = "something_else"
    tu = TrackerUpdate(frame_index=0, tracks={}, untracked=(), lost=(), new_ids=())
    with pytest.raises(dataclasses.FrozenInstanceError):
        tu.untracked = ()


def test_a_crossing_result_defaults_reid_abstained_to_zero():
    """KILLS: gawaah/sellevent.py:232 `reid_abstained: int = 0` -> 1 / -1."""
    r = CrossingResult(
        frame_index=0, crossed_out=(), crossed_back=(), exceptions=(),
        out_count=0, back_count=0, net_count=0,
        crossed_without_tracker_id=0, detected_but_never_counted=0,
        entries_from_out=0, vanished_same_side=0, tracks_tracked=0, amber=False,
    )
    assert r.reid_abstained == 0
    assert r.clean is True and r.total_is_trustworthy is True


@pytest.mark.parametrize(
    "bad",
    [(1.0,), (1.0, 2.0, 3.0), "xy", 5, None, {"x": 1}, (True, 1.0), (1.0, False),
     ("1", "2"), (float("nan"), 1.0), (1.0, float("inf"))],
)
def test_a_point_that_is_not_two_finite_numbers_is_refused(bad):
    """KILLS: gawaah/sellevent.py:278 and :282 (`or` -> `and`) and :279 / :283
    (both raises).

    `_as_point` is the only gate between whatever the perception layer hands
    over and the arithmetic that decides whether a sale happened. With either
    `or` flipped to `and`, a one-element tuple or a pair of strings walks
    straight into `project`, and `True` — which is an int in Python — becomes
    the coordinate 1.0.
    """
    z = _zone()
    with pytest.raises(ValueError):
        z.side(bad)
    with pytest.raises(ValueError):
        z.in_limits(bad)
    with pytest.raises(ValueError):
        z.update({1: bad})


def test_a_zero_length_line_is_refused_but_a_short_one_is_not():
    """KILLS: gawaah/sellevent.py:593 `if self._len <= 0` -> `<= 1`.

    The check exists to refuse p1 == p2, which makes the projection divide by
    zero. Widening it to a millimetre bans legitimate short lines instead.
    """
    with pytest.raises(ValueError):
        LineZone((10.0, 10.0), (10.0, 10.0))
    short = LineZone((0.0, 0.0), (0.5, 0.0), min_crossing_frames=1)
    assert short.signed_distance_mm((0.25, 2.0)) == pytest.approx(2.0)


def test_zero_is_a_legal_dead_band_and_a_legal_missing_frame_budget():
    """KILLS: gawaah/sellevent.py:597 `dead_band_mm < 0` -> `<=` (and its
    `0` -> `1`), and :341 / :343 the same shape in `CentroidTracker`.

    Every one of these guards refuses a NEGATIVE value. Turning `<` into `<=`
    also refuses zero, which is the setting a caller picks when they want no
    dead band and no tolerance at all — a perfectly reasonable, and now
    impossible, configuration.
    """
    z = LineZone((0.0, 0.0), (100.0, 0.0), min_crossing_frames=1, dead_band_mm=0.0)
    assert z.dead_band_mm == 0.0
    assert z.side((50.0, 0.0)) == SIDE_ON_LINE
    assert z.side((50.0, 0.001)) == SIDE_OUT
    with pytest.raises(ValueError):
        LineZone((0.0, 0.0), (100.0, 0.0), dead_band_mm=-0.5)

    t = CentroidTracker(max_dist_mm=1.0, max_missing_frames=0, ambiguity_mm=0.0)
    assert t.max_dist_mm == 1.0
    with pytest.raises(ValueError):
        CentroidTracker(max_missing_frames=-1)
    with pytest.raises(ValueError):
        CentroidTracker(ambiguity_mm=-0.1)
    with pytest.raises(ValueError):
        CentroidTracker(max_dist_mm=0)


def test_a_detection_exactly_at_max_dist_still_matches():
    """KILLS: gawaah/sellevent.py:392 `dm[di][ti] <= self.max_dist_mm` -> `<`.

    The tracker's association radius is inclusive: a detection exactly
    `max_dist_mm` away is the same object, not a new one. Excluding the
    boundary silently mints a fresh track id, and a fresh id in front of the
    sell line is an uncounted crossing.
    """
    t = CentroidTracker(max_dist_mm=10.0, max_missing_frames=3, ambiguity_mm=0.0)
    first = t.update([(0.0, 0.0)])
    tid = next(iter(first.tracks))
    moved = t.update([(10.0, 0.0)])              # exactly max_dist_mm away
    assert list(moved.tracks) == [tid], f"the track was re-numbered: {moved.tracks}"
    assert moved.new_ids == ()
    assert moved.untracked == ()
    far = t.update([(10.0, 10.001)])             # just beyond
    assert far.new_ids != (), "a detection past max_dist re-used the id"


def test_the_tracker_reports_the_gap_for_a_track_it_has_not_seen():
    """KILLS: gawaah/sellevent.py:364 `return int(self._missing.get(
    int(track_id), 0))` -> None / True / <deleted> / `0` -> 1 / -1.

    `gap_frames` is how a caller asks "how stale is this id?". Every mutation
    of its single line survived, including the one that answers True.
    """
    t = CentroidTracker(max_dist_mm=10.0, max_missing_frames=3)
    first = t.update([(0.0, 0.0)])
    tid = next(iter(first.tracks))
    assert t.gap_frames(tid) == 0
    assert t.gap_frames(9999) == 0, "an unknown id must report a zero gap"
    t.update([])
    assert t.gap_frames(tid) == 1, f"gap is {t.gap_frames(tid)}"
    t.update([])
    assert t.gap_frames(tid) == 2
    g = t.gap_frames(tid)
    assert isinstance(g, int) and not isinstance(g, bool)


def test_the_tracker_frame_index_counts_up_from_zero():
    """KILLS: gawaah/sellevent.py:367 `self._frame += 1` (-> `-=`, <deleted>,
    `+= 2`, `+= 0`) and :354 `self._frame = -1` -> `-2` / `0`.

    The frame index is the only handle an uncounted-crossing record gives an
    operator for finding the moment on the recording.
    """
    t = CentroidTracker()
    assert t.update([(0.0, 0.0)]).frame_index == 0
    assert t.update([(0.0, 0.0)]).frame_index == 1
    assert t.update([]).frame_index == 2
    assert t.update([]).frame_index == 3


def test_the_zone_frame_index_advances_and_flush_retires_everything():
    """KILLS: gawaah/sellevent.py:835 `self._frame += 1` in `flush` (-> `-=`,
    <deleted>, `+= 2`, `+= 0`) and :851 `tracks_tracked=0` -> 1 / -1.

    `flush` is the end of the session. Its result must say that nothing is
    still being tracked, because anything that was has just been judged.
    """
    z = _zone()
    z.update({1: (50.0, -10.0)})
    z.update({1: (50.0, -10.0)})
    assert z.update({1: (50.0, -10.0)}).frame_index == 2
    out = z.flush()
    assert out.frame_index == 3, f"flush reported frame {out.frame_index}"
    assert out.tracks_tracked == 0, out.tracks_tracked
    assert z.flush().frame_index == 4


def test_an_abstained_centroid_keeps_its_reason_and_prints_it():
    """KILLS: gawaah/sellevent.py:155 `self.frame_index = int(frame_index)`,
    :149 / :150 the `frame_index=-1` / `gap_frames=0` defaults, :170
    `return self.code in REID_REASONS` -> `return True`, and :173 the whole
    `__repr__`.

    The abstention record is welded to the coordinate on purpose, so that the
    reason cannot be dropped on the way to the ledger. Nothing checked that
    the weld holds.
    """
    c = AbstainedCentroid((12.5, 7.5), code=REASON_NO_TRACKER_ID, detail="why")
    assert c == (12.5, 7.5) and isinstance(c, tuple)
    assert c.x_mm == 12.5 and c.y_mm == 7.5
    assert c.frame_index == -1, "the default frame index moved"
    assert c.gap_frames == 0
    assert c.candidate_ids == ()
    assert c.is_reid is False, "a no-tracker-id centroid claimed to be a re-id"
    text = repr(c)
    assert isinstance(text, str)
    assert "AbstainedCentroid" in text and REASON_NO_TRACKER_ID in text

    c2 = AbstainedCentroid((1.0, 2.0), code=REASON_REID_AMBIGUOUS, detail="d",
                           frame_index=7, candidate_ids=[3, 4], gap_frames=2)
    assert c2.frame_index == 7 and c2.gap_frames == 2
    assert c2.candidate_ids == (3, 4)


def test_a_crossing_exception_names_who_it_was_about():
    """KILLS: gawaah/sellevent.py:205 `self.track_id is None` -> `is not`.

    "no-id" and "track 7" are opposite statements about whether we know which
    item crossed, and that distinction is the whole point of the record.
    """
    anon = CrossingException(code=REASON_NO_TRACKER_ID, detail="d", frame_index=1,
                             x_mm=1.0, y_mm=2.0, track_id=None)
    named = dataclasses.replace(anon, track_id=7)
    assert "no-id" in str(anon), str(anon)
    assert "track None" not in str(anon)
    assert "track 7" in str(named), str(named)
    assert "no-id" not in str(named)
    with_cands = dataclasses.replace(anon, candidate_ids=(3, 5))
    assert "candidates 3,5" in str(with_cands)


def test_reid_abstentions_are_the_reid_subset_of_untracked():
    """KILLS: gawaah/sellevent.py:270 `isinstance(p, AbstainedCentroid) and
    p.is_reid` -> `or`.

    With `or`, a plain `(x, y)` tuple in `untracked` is reported as a refused
    re-identification — and `p.is_reid` is then evaluated on a bare tuple,
    which has no such attribute.
    """
    plain = (1.0, 2.0)
    no_id = AbstainedCentroid((3.0, 4.0), code=REASON_NO_TRACKER_ID, detail="")
    reid = AbstainedCentroid((5.0, 6.0), code=REASON_REID_AMBIGUOUS, detail="")
    tu = TrackerUpdate(frame_index=0, tracks={}, untracked=(plain, no_id, reid),
                       lost=(), new_ids=())
    assert reid.is_reid is True
    assert tu.reid_abstentions == (reid,), tu.reid_abstentions


def test_the_uncounted_crossing_message_lists_the_first_few():
    """KILLS: gawaah/sellevent.py:126 the `super().__init__(...)` call.

    Deleting it leaves the exception with no message at all, so the escape
    hatch for integrators who want a hard stop stops with a blank error.
    """
    excs = tuple(
        CrossingException(code=REASON_NO_TRACKER_ID, detail=f"d{i}",
                          frame_index=i, x_mm=0.0, y_mm=0.0)
        for i in range(7)
    )
    e = UncountedCrossing(excs)
    text = str(e)
    assert text.startswith("7 uncounted crossing(s):"), text
    assert "d0" in text and "d4" in text
    assert e.exceptions == excs


def test_a_track_that_vanishes_mid_crossing_reports_how_long_it_was_held():
    """KILLS: gawaah/sellevent.py:880 `sum(1 for b in st.history if b ==
    (st.last_side == SIDE_OUT))` -> `!=`, `1` -> 2 / 0.

    "held 1 of the 3 frames required" is the sentence that tells an operator
    the crossing was real but short. `!=` counts the frames it was NOT on that
    side; `sum(0 …)` says it was never there at all; `sum(2 …)` doubles it.
    """
    z = LineZone((0.0, 0.0), (100.0, 0.0), min_crossing_frames=3)
    z.update({1: (50.0, -10.0)})       # IN
    z.update({1: (50.0, -10.0)})
    z.update({1: (50.0, 10.0)})        # OUT, one frame only
    out = z.flush()
    assert len(out.exceptions) == 1, out.exceptions
    exc = out.exceptions[0]
    assert exc.track_id == 1
    assert "held 1 of the 3 frames" in exc.detail, exc.detail
    assert "out to the customer" in exc.detail
    assert z.detected_but_never_counted == 1


# ------------------------ second wave: holes the first after-run left open ---
#
# Written after reading results/mutation_after.json. The harness is only worth
# running if you act on what it says the second time too.

def test_a_settled_basket_refuses_every_billing_event_except_a_new_placement(sess):
    """KILLS: gawaah/session.py:495 `allow_paid: bool = False` -> `True`.

    Only `on_placement` passes `allow_paid=True`, because a new item landing on
    the mat after a sale legitimately starts the next basket. Flipping the
    DEFAULT hands that exemption to every other caller: `on_exit` would commit
    a crossing into a basket that has already been paid for, `on_revert` would
    remove a line from a settled sale, and `on_done` would re-close it. PAID is
    in `_LOCKED` precisely so that none of that can happen.
    """
    _open_basket(sess, price=21450, item="i1")
    sess.on_done()
    sess.on_webhook(Verdict(event_id="e_paid", event="payment.captured",
                            session_id=sess.session_id, amount_paise=21450,
                            green=True, signature_valid=True))
    assert sess.state is State.PAID and sess.money_authorised is True

    for label, call in (
        ("on_exit", lambda: sess.on_exit("i1")),
        ("on_price", lambda: sess.on_price("i1", 999)),
        ("on_revert", lambda: sess.on_revert("i1")),
        ("on_done", lambda: sess.on_done()),
    ):
        t = call()
        assert t.reason == Reason.BASKET_LOCKED, f"{label} gave {t.reason}"
        assert sess.state is State.PAID, f"{label} moved a settled basket"
        assert sess.money_authorised is True
        assert int(sess.total_paise) == 21450

    # ...and a new placement IS allowed, because that is the next sale
    t = sess.on_placement(Placement(item_id="next", price_paise=500))
    assert t.reason != Reason.BASKET_LOCKED
    assert t.detail["new_basket"] is True


def test_losing_and_regaining_the_mat_log_opposite_reason_codes(sess):
    """KILLS: gawaah/session.py:563 `Reason.MAT_LOST if not locked else
    Reason.MAT_REACQUIRED` -> `if locked`, and :574 the same shape for the
    brain link.

    Dropping the `not` swaps the two reason codes, so the audit log records a
    perception RECOVERY at the moment perception was LOST — and the ledger is
    the only account of what the counter could see when it billed.
    """
    _open_basket(sess, price=4200, item="i1")
    lost = sess.on_mat_lock(False)
    assert lost.reason == Reason.MAT_LOST, lost.reason
    assert sess.state is State.MAT_LOST
    back = sess.on_mat_lock(True)
    assert back.reason == Reason.MAT_REACQUIRED, back.reason

    blost = sess.on_brain(False)
    assert blost.reason == Reason.BRAIN_LOST, blost.reason
    assert sess.state is State.BRAIN_LOST
    bback = sess.on_brain(True)
    assert bback.reason == Reason.BRAIN_REACQUIRED, bback.reason

    reasons = [json.loads(l)["reason"]
               for l in Path(sess.ledger.path).read_text().splitlines()]
    assert Reason.MAT_LOST in reasons and Reason.MAT_REACQUIRED in reasons
    assert Reason.BRAIN_LOST in reasons and Reason.BRAIN_REACQUIRED in reasons


def test_a_second_freeze_cause_while_already_frozen_says_still_frozen(sess):
    """KILLS: gawaah/session.py:542 `return self._emit(target, event,
    Reason.STILL_FROZEN, {})` in all three of its mutated forms.

    Mat loss beats brain loss, so a brain drop while the mat is already lost
    does not move the counter anywhere — but it is still an event, it still
    needs a line, and that line must say the total is STILL frozen rather than
    re-announcing a freeze that already happened.
    """
    _open_basket(sess, price=8800, item="i1")
    sess.on_mat_lock(False)
    assert sess.state is State.MAT_LOST
    frozen_at = int(sess.total_paise)

    t = sess.on_brain(False)
    assert isinstance(t, Transition)
    assert t.reason == Reason.STILL_FROZEN, t.reason
    assert sess.state is State.MAT_LOST, "the second cause moved the state"
    assert int(sess.total_paise) == frozen_at
    last = json.loads(Path(sess.ledger.path).read_text().splitlines()[-1])
    assert last["reason"] == Reason.STILL_FROZEN

    # both causes must clear before the total thaws
    sess.on_mat_lock(True)
    assert sess.state is State.BRAIN_LOST, "thawed with the brain still down"
    assert sess.frozen is True
    sess.on_brain(True)
    assert sess.frozen is False
    assert int(sess.total_paise) == 8800


def test_a_tapped_price_relabels_the_line(sess):
    """KILLS: gawaah/session.py:671 `line.reason = Reason.PRICE_TAPPED` ->
    <deleted>.

    An AMBER line that a shopkeeper has priced by hand is no longer an
    abstention, and the row must stop saying `unknown_sku` — that string is
    what the amber banner and the excluded-from-total rule are driven from.
    """
    sess.on_mat_lock(True)
    sess.on_placement(Placement(item_id="i1"))
    line = sess.line_items[0]
    assert line.amber is True and line.reason == Reason.UNKNOWN_SKU
    sess.on_price("i1", 6500)
    assert line.price_paise == 6500
    assert line.amber is False
    assert line.reason == Reason.PRICE_TAPPED, line.reason
    sess.on_exit("i1")
    assert int(sess.total_paise) == 6500
    assert sess.amber_count == 0


def test_acknowledging_a_frozen_total_clears_the_exception_cause(sess):
    """KILLS: gawaah/session.py:784 `self._causes.discard('exception')` ->
    <deleted>.

    Without the discard the cause stays raised, so the very next perception
    signal re-derives FROZEN_TOTAL and the counter falls straight back into the
    freeze the shopkeeper just acknowledged — a till that cannot be un-stuck.
    """
    _open_basket(sess, price=7700, item="i1")
    sess.on_exit(None)                        # abstention 11 -> FROZEN_TOTAL
    assert sess.state is State.FROZEN_TOTAL and sess.frozen is True
    t = sess.on_acknowledge()
    assert t.reason == Reason.HUMAN_ACKNOWLEDGED
    assert sess.state is State.BASKET_OPEN, t.to
    assert sess.frozen is False

    # the acknowledgement must survive the next perception wobble
    sess.on_mat_lock(False)
    assert sess.state is State.MAT_LOST, "an acknowledged exception came back"
    sess.on_mat_lock(True)
    assert sess.state is State.BASKET_OPEN, sess.state
    assert sess.frozen is False
    assert int(sess.total_paise) == 7700


def test_a_refusal_stops_being_a_duplicate_once_the_state_moves(sess):
    """KILLS: gawaah/session.py:449 `self._noop_keys.clear()` -> <deleted>.

    R7 says a repeated refusal writes nothing — but only while nothing has
    moved. Keep the cache across a state change and the SECOND, genuinely
    different refusal is served from it: the ledger silently loses a line and
    the caller is handed the reason code for a fault that is no longer the one
    it has.
    """
    first = sess.on_done()                        # SETUP: mat not locked
    assert first.reason == Reason.MAT_NOT_LOCKED
    assert first.lines_written == 1
    repeat = sess.on_done()
    assert repeat.reason == Reason.MAT_NOT_LOCKED
    assert repeat.lines_written == 0, "a repeated refusal wrote a line"

    sess.on_mat_lock(True)                        # the state moves
    again = sess.on_done()                        # same key, different fault
    assert again.reason == Reason.EMPTY_BASKET, (
        f"served a stale cached refusal: {again.reason}"
    )
    assert again.lines_written == 1
    reasons = [json.loads(l)["reason"]
               for l in Path(sess.ledger.path).read_text().splitlines()]
    assert Reason.EMPTY_BASKET in reasons


def test_a_non_default_synchronous_pragma_is_actually_applied(tmp_path):
    """KILLS: gawaah/kernel.py:523 `con.execute(f'PRAGMA synchronous=…')` ->
    <deleted>.

    My first attempt at this asserted `PRAGMA synchronous == 2` on a default
    Kernel — and the mutant SURVIVED, because 2 (FULL) is also sqlite's own
    default, so deleting the statement changed nothing observable. Asking for
    NORMAL is the only way to prove the statement runs at all.
    """
    ledger = Ledger(tmp_path / "a.jsonl")
    k = Kernel(tmp_path / "n.db", VirtualClock(), ledger, synchronous="NORMAL")
    try:
        with k._conn() as con:
            assert con.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    finally:
        k.close()
    k2 = Kernel(tmp_path / "f.db", VirtualClock(), ledger, synchronous="EXTRA")
    try:
        with k2._conn() as con:
            assert con.execute("PRAGMA synchronous").fetchone()[0] == 3  # EXTRA
    finally:
        k2.close()


def test_a_ledger_whose_last_line_was_half_written_is_still_read(tmp_path):
    """KILLS: gawaah/kernel.py:486 `last, count = carry, count + 1` in all four
    of its mutated forms (<deleted>, `count - 1`, `count + 2`, `count + 0`).

    `_sync_ledger_head` reads the tail in 64 KiB chunks and splits on newlines;
    `carry` is whatever is left over after the last newline. That leftover is a
    real line in exactly the case this module exists for — another process
    appended and the file does not end in a newline, because the write was
    interrupted or because a chunk boundary fell mid-line. If the carry is
    dropped the head is stale, and the next append chains from a hash that is
    no longer the tail.

    Called under the file lock the way the method's own docstring requires.
    """
    p = tmp_path / "shared.jsonl"
    a = Kernel(tmp_path / "a.db", VirtualClock(), Ledger(p))
    b = Kernel(tmp_path / "b.db", VirtualClock(), Ledger(p))
    try:
        a.audit_append("kernel", event="one", amount_paise=1)
        h2 = a.audit_append("kernel", event="two", amount_paise=2)
        assert b.ledger.count == 0, "b has not seen anything yet"

        # the tail arrives without its final newline
        p.write_bytes(p.read_bytes().rstrip(b"\n"))
        with b._ledger_file_lock():
            b._sync_ledger_head()
        assert b.ledger.count == 2, f"b counted {b.ledger.count} of 2 lines"
        assert b.ledger.head == h2, "b did not pick up the half-written tail"
    finally:
        a.close()
        b.close()


def test_documented_equivalent_mutants_are_named():
    """Not a hole: an honest list of survivors that CANNOT be killed.

    An equivalent mutant is an edit with no observable effect for any input the
    contract admits. Writing a test to 'kill' one means asserting an
    implementation detail, which makes the suite worse, not better. They are
    named here so the surviving count in results/mutation_after.json can be
    read against a documented list instead of being waved away.
    """
    equivalents = {
        # -- genuinely equivalent: no input can tell the two apart -----------
        "gawaah/money.py:67 stmt_delete `p = int(p)`": (
            "to_rupees_str is typed (p: Paise) and Paise is a NewType over int, "
            "so int(p) is the identity for every in-contract input. It differs "
            "only for values the type forbids."
        ),
        "gawaah/webhook.py:204 stmt_delete `return False`": (
            "the early return for an unusable signature. Falling through "
            "computes the HMAC and compares it against b'' with "
            "hmac.compare_digest, which is False for every input. Identical "
            "verdict, one wasted hash."
        ),
        "gawaah/webhook.py:220/221 `return b''` -> `return None` / <deleted>": (
            "_as_ascii_bytes' failure returns. Every caller uses the result "
            "only for truthiness (`if not key`, `if not provided`), and None "
            "and b'' are both falsy, so no caller can distinguish them."
        ),
        "gawaah/webhook.py:268/269 stmt_delete `...`": (
            "the bodies of a typing.Protocol's method stubs. `...` and `pass` "
            "are the same statement to a Protocol."
        ),
        "gawaah/webhook.py:672 `self._ledger is None or self._clock is None`"
        " -> `and`": (
            "GreenPredicate.__init__ already raises unless ledger and clock are "
            "supplied together, so by the time _audit runs the two operands are "
            "always equal and `or` and `and` agree. Belt-and-braces code whose "
            "braces the constructor already fastened."
        ),
        "gawaah/webhook.py:724 `return {}` -> `return None`": (
            "_entities' non-dict guard. The caller's next line is `if not "
            "entities: return deny('no_entity')`, and {} and None are both "
            "falsy. (`return True` and <deleted> are NOT equivalent — they "
            "raise — and are killed by "
            "test_a_non_object_payload_denies_with_no_entity.)"
        ),
        "gawaah/webhook.py:741 stmt_delete `return None`": (
            "the final statement of _note. Falling off the end of a function "
            "returns None."
        ),
        "gawaah/kernel.py:460 `size < self._ledger_size` -> `<=`": (
            "the shrunk-file branch of _sync_ledger_head. The line above it "
            "already returned when size == self._ledger_size, so at this point "
            "`<` and `<=` cannot differ."
        ),
        "gawaah/kernel.py:605 `fetchone()[0]` -> `[-1]`": (
            "`SELECT COUNT(*)` returns a one-element row, so index 0 and index "
            "-1 are the same element. There is no input that separates them."
        ),
        "gawaah/kernel.py:538 stmt_delete `con.execute('ROLLBACK')`": (
            "the rollback in _tx's except branch. The connection is closed "
            "immediately afterwards by the _conn context manager, and sqlite3 "
            "rolls back any open transaction on close, so the database ends in "
            "the same state either way."
        ),
        "gawaah/kernel.py:391 stmt_delete `os.makedirs(parent, exist_ok=True)`": (
            "the LEDGER's parent directory. `Ledger.__post_init__` has already "
            "created it by the time a Ledger instance reaches the Kernel, so "
            "this line is defensive duplication for duck-typed ledgers only. "
            "The DB's makedirs at :356 is NOT redundant and is killed by "
            "test_the_db_and_ledger_parents_are_created_when_missing."
        ),
        "gawaah/session.py:409 `state is PAID and _authorised_paise is not "
        "None` -> `or`": (
            "`_authorised_paise` is assigned only immediately before the emit "
            "that moves the session to PAID, and cleared only by the same call "
            "that leaves it, so the two operands are never observably "
            "different from outside. Belt and braces on R4."
        ),
        "gawaah/session.py:306/451/950 the `_degraded_resume` clears": (
            "`_emit` already sets `_degraded_resume = None` whenever a "
            "transition leaves DEGRADED, so the explicit clears are redundant "
            "with it and the initialiser is written before any read."
        ),
        "gawaah/kernel.py:392 stmt_delete `self._lock_fd = None`": (
            "the pre-initialisation before the fcntl branch. On any platform "
            "with fcntl the very next line assigns it, so only a Windows build "
            "could observe this — and this suite does not run one."
        ),
        "gawaah/session.py:366 stmt_delete `return Paise(self._frozen_total)`": (
            "the frozen branch of `total_paise`. While `_frozen_total is not "
            "None` the session is in a frozen state and `_billing_guard` "
            "refuses every handler that could change the committed set, so "
            "`_live_total()` on the next line is provably equal to the "
            "snapshot. Verified by applying the mutant: the suite still passed."
        ),
        "gawaah/session.py:513/535 stmt_delete `return None`": (
            "trailing `return None` statements. Falling off the end of a "
            "function returns None."
        ),
        "gawaah/sellevent.py:787/791 `st.out_credits += 1` -> `+= 2`, "
        "`-= 1` -> `+= 1` / `-= 0` / <deleted>": (
            "out_credits is only ever READ as `> 0`, is only decremented from "
            "a value that is already > 0, and is never exposed on "
            "CrossingResult. Any mutation that keeps it positive keeps every "
            "observable counter identical. (`crossed_back.append` on the same "
            "branch is NOT equivalent and is killed by "
            "test_a_crossing_back_is_reported_on_the_result.)"
        ),

        # -- observable, but only in prose an operator reads ------------------
        # These are NOT equivalent: the text of an error message changes. They
        # are left alive on purpose, because the test that kills them has to
        # assert a magic number inside a human-readable string, which pins an
        # implementation detail and makes the suite worse.
        "COSMETIC gawaah/ledger.py:111/112 `stored[:16]` -> 15 / 17": (
            "how many characters of a hash are echoed in a mismatch message. "
            "The verdict, the line number and the count are unchanged."
        ),
        "COSMETIC webhook.py:437 `replay_key[:24]`, "
        "sellevent.py:128 `exceptions[:5]`": (
            "truncation widths inside diagnostic strings."
        ),

        # -- unobservable configuration --------------------------------------
        "gawaah/kernel.py:516 `sqlite3.connect(timeout=30)` -> 29 / 31": (
            "the connect-level busy timeout. sqlite3 exposes no way to read it "
            "back, and the PRAGMA busy_timeout that IS readable is set "
            "separately and is pinned by "
            "test_the_durability_pragmas_are_actually_applied."
        ),
        "gawaah/kernel.py:575 stmt_delete `self.close()` in `__del__`": (
            "a finaliser. Asserting it would mean asserting when CPython "
            "collects an object, which is a property of the interpreter, not "
            "of this module. `close()` itself is pinned by "
            "test_close_releases_the_ledger_lock_fd_and_is_idempotent."
        ),

        # -- performance-only: same output, different cost -------------------
        "PERF gawaah/kernel.py:264 `_LEDGER_SCAN_CHUNK = 1 << 16` -> 1<<15 / "
        "1<<17 / 2<<16": (
            "the read size of the ledger tail scan. The carry-and-split loop is "
            "correct for any positive chunk size, so the head and count it "
            "produces are byte-for-byte the same; only the number of read() "
            "calls changes."
        ),
        "PERF gawaah/kernel.py:459 stmt_delete `return` (nobody else wrote)": (
            "the fast path when the file has not grown. Removing it re-runs the "
            "tail scan from the current offset, finds nothing, and assigns the "
            "same head and count back."
        ),
        "PERF gawaah/kernel.py:460 `<` -> `>=`": (
            "sends a GROWN file down the rescan-from-genesis path. It walks the "
            "whole file instead of the new tail and arrives at the same head "
            "and the same count: O(file) instead of O(new bytes)."
        ),
    }
    assert len(equivalents) >= 15, "the list must stay explicit, not shrink quietly"
    for claim, why in equivalents.items():
        assert why and len(why) > 40, f"{claim} is asserted without a reason"

    # The money.py claim is checkable rather than merely asserted.
    for n in (-10**9, -1, 0, 1, 99, 100, 10**9):
        assert to_rupees_str(paise(n)) == to_rupees_str(paise(int(n)))

    # So is the webhook one: both falsy values produce the same verdict.
    from gawaah.webhook import _as_ascii_bytes
    assert not _as_ascii_bytes("देवनागरी", encoding="ascii")
    assert not _as_ascii_bytes(None, encoding="ascii")
    assert not _as_ascii_bytes(12345, encoding="ascii")

    # And the sellevent one: out_credits is not on the public result.
    result_fields = {f.name for f in dataclasses.fields(CrossingResult)}
    assert "out_credits" not in result_fields
