#!/usr/bin/env python3
"""MUTATION TESTING — do the tests actually hold the money path down?

A green test suite proves that the code passes the tests. It does not prove
that the tests would notice if the code were wrong. Mutation testing closes
that gap the only honest way: break the code on purpose, one small edit at a
time, and see whether the suite screams.

    KILLED    — the mutant was introduced and at least one test failed.
                The suite is watching that line.
    SURVIVED  — the mutant was introduced and the whole suite still passed.
                Either the line is untested, or the mutation is EQUIVALENT
                (semantically identical to the original), or the assertion that
                covers it is too weak to see the difference.

A survivor on the GREEN predicate or on paise arithmetic is a genuine hole:
it means there is a one-token edit to the money path that no test would catch.

HOW IT WORKS
------------
1.  Parse the target module with `ast`.
2.  Index every node with a deterministic depth-first walk, so a mutation site
    has a stable id that does not depend on dict ordering or hash seeds.
3.  Ask every enabled operator which sites it can mutate. Collect candidates.
4.  For each candidate, re-walk the tree, apply exactly ONE mutation, and
    `ast.unparse` the result.
5.  Write the mutated source into a SANDBOX COPY of the repo — never into the
    working tree — and run that module's test file(s) in a subprocess.
6.  Exit code 0 => SURVIVED. Non-zero => KILLED. Timeout => KILLED (a mutant
    that hangs has been detected just as surely as one that asserts).

TWO THINGS THAT MAKE THE NUMBER TRUSTWORTHY
-------------------------------------------
* THE WORKING TREE IS NEVER TOUCHED. Mutants are written into a sandbox copy.
  Other agents edit this repo concurrently; a harness that mutated files in
  place would hand them a corrupted module the moment it crashed.
* A TIMEOUT KILLS THE WHOLE PROCESS TREE. A timeout is scored as a KILL, so a
  timeout caused by a busy machine is a fabricated kill. `test_kernel.py`
  launches eight OS subprocesses; the first version of this file used
  `subprocess.run(timeout=…)`, which killed pytest and orphaned all eight —
  still spinning, still holding an flock, half an hour later — after which
  later mutants "died" of a busy box. See `_kill_tree`.

THE NULL MUTANT
---------------
Before any real mutant runs, the harness writes `ast.unparse(ast.parse(src))`
back — the source with the *identity* mutation — and runs the tests. If that
fails, the harness itself is what broke the module (a comment-stripping or
formatting artefact), and every "kill" afterwards would be a lie. A failed null
mutant aborts the run for that module. INVARIANT 9: a number that a broken
measurement produced is not a number.

WHAT IS DELIBERATELY NOT MUTATED
--------------------------------
* Docstrings. Rewriting prose is not a defect.
* `import` statements, and statements at module scope. Deleting `GREEN_EVENTS
  = ...` is not a subtle bug, it is an ImportError; such mutants are killed by
  collection alone and would inflate the kill rate without testing anything.
  `stmt_delete` therefore only deletes SIMPLE statements inside a function body.
* Compound statements (`if` / `for` / `with` / `try`). Deleting a whole block
  is a large, uninteresting mutation; the interesting part of an `if` is its
  test, which `comparison_*`, `boolop_swap` and `not_remove` already reach.

USAGE
-----
    python tools/mutate.py --module gawaah/money.py --tests tests/test_money_ledger.py
    python tools/mutate.py --money-path --jobs 8 --out results/mutation.json
    python tools/mutate.py --money-path --jobs 8 --with-mutation-tests
    python tools/mutate.py --module gawaah/webhook.py --list      # no test runs

Exit code is 0 unless a run was aborted (null mutant failed / baseline red),
or `--min-kill-rate` was given and not met.
"""
from __future__ import annotations

import argparse
import ast
import concurrent.futures
import copy
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Directories copied into each sandbox. Everything the tests import.
SANDBOX_DIRS: tuple[str, ...] = ("gawaah", "tests", "tools")

#: Environment marker the runner sets in every child pytest. Tests that would
#: recursively launch a mutation campaign check for it and skip.
CHILD_ENV = "GAWAAH_MUTATION_CHILD"

KILLED = "KILLED"
SURVIVED = "SURVIVED"
TIMEOUT = "TIMEOUT"
ERROR = "ERROR"

#: module -> its own test file(s). The money-critical set from the brief.
MONEY_PATH_TARGETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gawaah/money.py", ("tests/test_money_ledger.py",)),
    ("gawaah/ledger.py", ("tests/test_money_ledger.py",)),
    ("gawaah/kernel.py", ("tests/test_kernel.py",)),
    ("gawaah/webhook.py", ("tests/test_webhook.py",)),
    ("gawaah/session.py", ("tests/test_session.py",)),
    ("gawaah/sellevent.py", ("tests/test_sellevent.py",)),
)

#: Appended to every target's test list when --with-mutation-tests is passed.
#: This is where the tests written to kill survivors live.
MUTATION_TEST_FILE = "tests/test_mutation.py"


# ====================================================================== model


@dataclass(frozen=True)
class Mutant:
    """One single-site edit to one module."""

    mid: str
    module: str
    operator: str
    lineno: int
    col_offset: int
    node_index: int
    slot: int
    before: str
    after: str
    context: str = ""

    @property
    def label(self) -> str:
        return f"{self.module}:{self.lineno} [{self.operator}] {self.before} -> {self.after}"


@dataclass
class MutantResult:
    mutant: Mutant
    status: str
    duration_s: float = 0.0
    detail: str = ""

    @property
    def killed(self) -> bool:
        # A hang is a detection: the suite did not sail past the bad code.
        return self.status in (KILLED, TIMEOUT)

    def to_dict(self) -> dict:
        d = asdict(self.mutant)
        d.update(
            status=self.status,
            killed=self.killed,
            duration_s=round(self.duration_s, 3),
            detail=self.detail,
        )
        return d


@dataclass
class ModuleReport:
    module: str
    tests: list[str]
    total: int = 0
    killed: int = 0
    survived: int = 0
    timeout: int = 0
    errored: int = 0
    results: list[MutantResult] = field(default_factory=list)
    baseline_s: float = 0.0
    #: how many attempts the UNMUTATED baseline needed. >1 means the suite is
    #: flaky under load, which is worth seeing next to the kill rate.
    baseline_attempts: int = 1
    #: non-empty when the run produced NO usable number and must not be quoted
    aborted: str = ""

    @property
    def scored(self) -> int:
        """Mutants that produced a usable verdict (ERROR ones are excluded)."""
        return self.killed + self.survived

    @property
    def kill_rate(self) -> float:
        return (self.killed / self.scored) if self.scored else 0.0

    def survivors(self) -> list[MutantResult]:
        return [r for r in self.results if r.status == SURVIVED]

    def to_dict(self) -> dict:
        return {
            "module": self.module,
            "tests": list(self.tests),
            "total_mutants": self.total,
            "killed": self.killed,
            "survived": self.survived,
            "timeout": self.timeout,
            "errored": self.errored,
            "scored": self.scored,
            "kill_rate": round(self.kill_rate, 4),
            "kill_rate_pct": round(self.kill_rate * 100, 2),
            "baseline_s": round(self.baseline_s, 3),
            "baseline_attempts": self.baseline_attempts,
            "aborted": self.aborted,
            "mutants": [r.to_dict() for r in self.results],
        }


# ================================================================== indexing


def index_nodes(tree: ast.AST) -> list[ast.AST]:
    """Depth-first, field-order walk assigning each node a stable index.

    `ast.walk` is breadth-first and uses `iter_child_nodes` per level; either
    order is deterministic, but DFS keeps a mutation site's index stable when
    an unrelated later function grows, which makes ids readable in diffs.
    """
    ordered: list[ast.AST] = []

    def visit(node: ast.AST) -> None:
        node._mut_index = len(ordered)  # type: ignore[attr-defined]
        ordered.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return ordered


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Indices of Expr/Constant nodes that are docstrings. Never mutated."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr):
                val = body[0].value
                if isinstance(val, ast.Constant) and isinstance(val.value, str):
                    out.add(body[0]._mut_index)  # type: ignore[attr-defined]
                    out.add(val._mut_index)  # type: ignore[attr-defined]
    return out


def _function_body_statements(tree: ast.AST) -> set[int]:
    """Indices of statements that sit directly in a function/method body.

    Includes nested bodies (if/for/while/try/with) *inside* a function, but
    excludes anything at module or class scope — see the module docstring.
    """
    out: set[int] = set()

    def walk_body(stmts: Sequence[ast.stmt]) -> None:
        for st in stmts:
            out.add(st._mut_index)  # type: ignore[attr-defined]
            for fname in ("body", "orelse", "finalbody"):
                sub = getattr(st, fname, None)
                if isinstance(sub, list) and sub and isinstance(sub[0], ast.stmt):
                    walk_body(sub)
            for handler in getattr(st, "handlers", []) or []:
                walk_body(handler.body)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            walk_body(node.body)
    return out


# ================================================================= operators
#
# Every operator is a function (node, ctx) -> list of (slot, before, after).
# `slot` disambiguates several mutations of the same kind on one node (e.g. a
# chained comparison has one slot per operator). The transformer re-derives the
# mutation from (node_index, operator, slot), so nothing is carried in closures.

_CMP_NEGATE: dict[type, type] = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE,
    ast.GtE: ast.Lt,
    ast.Gt: ast.LtE,
    ast.LtE: ast.Gt,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
}

_CMP_BOUNDARY: dict[type, type] = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
}

_CMP_SYMBOL: dict[type, str] = {
    ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=",
    ast.Gt: ">", ast.GtE: ">=", ast.Is: "is", ast.IsNot: "is not",
    ast.In: "in", ast.NotIn: "not in",
}

_ARITH_SWAP: dict[type, type] = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.FloorDiv,
    ast.FloorDiv: ast.Mult,
}

_ARITH_SYMBOL: dict[type, str] = {
    ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.FloorDiv: "//",
}


@dataclass(frozen=True)
class _Site:
    slot: int
    before: str
    after: str


class Operator:
    name = "abstract"

    def sites(self, node: ast.AST, ctx: "MutationContext") -> list[_Site]:
        raise NotImplementedError

    def apply(self, node: ast.AST, slot: int) -> Optional[ast.AST]:
        """Return the replacement node, or None to DELETE the node."""
        raise NotImplementedError


class ComparisonNegate(Operator):
    name = "comparison_negate"

    def sites(self, node, ctx):
        if not isinstance(node, ast.Compare):
            return []
        out = []
        for i, op in enumerate(node.ops):
            new = _CMP_NEGATE.get(type(op))
            if new is not None:
                out.append(_Site(i, _CMP_SYMBOL[type(op)], _CMP_SYMBOL[new]))
        return out

    def apply(self, node, slot):
        new_node = _clone(node)
        old = type(new_node.ops[slot])
        new_node.ops[slot] = _CMP_NEGATE[old]()
        return new_node


class ComparisonBoundary(Operator):
    """`<` <-> `<=`. The off-by-one that a threshold test with no boundary
    case cannot see."""

    name = "comparison_boundary"

    def sites(self, node, ctx):
        if not isinstance(node, ast.Compare):
            return []
        out = []
        for i, op in enumerate(node.ops):
            new = _CMP_BOUNDARY.get(type(op))
            if new is not None:
                out.append(_Site(i, _CMP_SYMBOL[type(op)], _CMP_SYMBOL[new]))
        return out

    def apply(self, node, slot):
        new_node = _clone(node)
        old = type(new_node.ops[slot])
        new_node.ops[slot] = _CMP_BOUNDARY[old]()
        return new_node


class BoolOpSwap(Operator):
    name = "boolop_swap"

    def sites(self, node, ctx):
        if not isinstance(node, ast.BoolOp):
            return []
        before = "and" if isinstance(node.op, ast.And) else "or"
        after = "or" if before == "and" else "and"
        return [_Site(0, before, after)]

    def apply(self, node, slot):
        new_node = _clone(node)
        new_node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        return new_node


class NotRemove(Operator):
    """`not X` -> `X`. Inverts a guard clause without touching its condition."""

    name = "not_remove"

    def sites(self, node, ctx):
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            inner = _short(node.operand)
            return [_Site(0, f"not {inner}", inner)]
        return []

    def apply(self, node, slot):
        return node.operand


class ConstantInt(Operator):
    """n -> n+1 and n -> n-1. The classic off-by-one."""

    name = "const_int"

    def sites(self, node, ctx):
        if not isinstance(node, ast.Constant):
            return []
        if isinstance(node.value, bool) or not isinstance(node.value, int):
            return []
        if node._mut_index in ctx.docstrings:  # type: ignore[attr-defined]
            return []
        v = node.value
        return [_Site(0, repr(v), repr(v + 1)), _Site(1, repr(v), repr(v - 1))]

    def apply(self, node, slot):
        return ast.Constant(value=node.value + (1 if slot == 0 else -1))


class BoolConst(Operator):
    name = "bool_const"

    def sites(self, node, ctx):
        if isinstance(node, ast.Constant) and isinstance(node.value, bool):
            return [_Site(0, repr(node.value), repr(not node.value))]
        return []

    def apply(self, node, slot):
        return ast.Constant(value=not node.value)


class ArithSwap(Operator):
    name = "arith_swap"

    def sites(self, node, ctx):
        if isinstance(node, ast.BinOp) and type(node.op) in _ARITH_SWAP:
            a, b = type(node.op), _ARITH_SWAP[type(node.op)]
            return [_Site(0, _ARITH_SYMBOL[a], _ARITH_SYMBOL[b])]
        if isinstance(node, ast.AugAssign) and type(node.op) in _ARITH_SWAP:
            a, b = type(node.op), _ARITH_SWAP[type(node.op)]
            return [_Site(0, _ARITH_SYMBOL[a] + "=", _ARITH_SYMBOL[b] + "=")]
        return []

    def apply(self, node, slot):
        new_node = _clone(node)
        new_node.op = _ARITH_SWAP[type(node.op)]()
        return new_node


#: Simple statements that may be deleted. Compound statements are excluded on
#: purpose (see module docstring).
_DELETABLE = (
    ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Expr,
    ast.Return, ast.Raise, ast.Assert, ast.Delete,
    ast.Break, ast.Continue,
)


class StatementDelete(Operator):
    name = "stmt_delete"

    def sites(self, node, ctx):
        idx = node._mut_index  # type: ignore[attr-defined]
        if idx not in ctx.function_statements or idx in ctx.docstrings:
            return []
        if not isinstance(node, _DELETABLE):
            return []
        if isinstance(node, ast.AnnAssign) and node.value is None:
            return []  # a bare annotation has no runtime effect
        return [_Site(0, _short(node), "<deleted>")]

    def apply(self, node, slot):
        return None  # NodeTransformer drops it; empty bodies get a `pass`


class ReturnConstant(Operator):
    """`return X` -> `return None` / `return True`.

    `return True` is the dangerous one: on a gate like `verify_signature` it is
    "authenticate everything". A suite that never asserts the False branch of a
    predicate will not notice.
    """

    name = "return_const"
    _VALUES = (None, True)

    def sites(self, node, ctx):
        if not isinstance(node, ast.Return):
            return []
        idx = node._mut_index  # type: ignore[attr-defined]
        if idx not in ctx.function_statements:
            return []
        before = _short(node)
        out = []
        for slot, val in enumerate(self._VALUES):
            if (
                node.value is None
                or (isinstance(node.value, ast.Constant) and node.value.value is val)
            ):
                continue
            out.append(_Site(slot, before, f"return {val!r}"))
        return out

    def apply(self, node, slot):
        return ast.Return(value=ast.Constant(value=self._VALUES[slot]))


ALL_OPERATORS: tuple[Operator, ...] = (
    ComparisonNegate(),
    ComparisonBoundary(),
    BoolOpSwap(),
    NotRemove(),
    ConstantInt(),
    BoolConst(),
    ArithSwap(),
    StatementDelete(),
    ReturnConstant(),
)

OPERATOR_NAMES: tuple[str, ...] = tuple(op.name for op in ALL_OPERATORS)


def _clone(node: ast.AST) -> ast.AST:
    """Shallow structural copy that keeps children shared (they are not
    mutated) but lets us replace an operator without touching the original.

    List-valued fields are re-listed, because `copy.copy` would share them and
    `new.ops[slot] = ...` would then rewrite the tree we are mutating FROM —
    every subsequent mutant of the same module would inherit the edit.
    """
    new = copy.copy(node)
    for fname in node._fields:
        value = getattr(node, fname, None)
        if isinstance(value, list):
            setattr(new, fname, list(value))
    ast.copy_location(new, node)
    return new


def _short(node: ast.AST, limit: int = 72) -> str:
    try:
        text = ast.unparse(node)
    except Exception:  # pragma: no cover - unparse is total for real trees
        text = type(node).__name__
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ================================================================ generation


@dataclass
class MutationContext:
    docstrings: set[int]
    function_statements: set[int]


def generate_mutants(
    source: str,
    module_name: str,
    operators: Sequence[Operator] = ALL_OPERATORS,
) -> list[Mutant]:
    """Every single-site mutant of `source`, in deterministic order."""
    tree = ast.parse(source)
    nodes = index_nodes(tree)
    ctx = MutationContext(
        docstrings=_docstring_nodes(tree),
        function_statements=_function_body_statements(tree),
    )
    src_lines = source.splitlines()
    out: list[Mutant] = []
    for node in nodes:
        idx = node._mut_index  # type: ignore[attr-defined]
        if idx in ctx.docstrings:
            continue
        for op in operators:
            for site in op.sites(node, ctx):
                lineno = getattr(node, "lineno", 0)
                col = getattr(node, "col_offset", 0)
                context = src_lines[lineno - 1].strip() if 0 < lineno <= len(src_lines) else ""
                out.append(
                    Mutant(
                        mid=f"{module_name}#{idx}:{op.name}:{site.slot}",
                        module=module_name,
                        operator=op.name,
                        lineno=lineno,
                        col_offset=col,
                        node_index=idx,
                        slot=site.slot,
                        before=site.before,
                        after=site.after,
                        context=context[:120],
                    )
                )
    out.sort(key=lambda m: (m.lineno, m.col_offset, m.operator, m.slot, m.node_index))
    return out


class _SingleSiteTransformer(ast.NodeTransformer):
    def __init__(self, node_index: int, op: Operator, slot: int) -> None:
        self.node_index = node_index
        self.op = op
        self.slot = slot
        self.applied = 0

    def visit(self, node: ast.AST) -> Any:
        if getattr(node, "_mut_index", None) == self.node_index:
            self.applied += 1
            return self.op.apply(node, self.slot)
        return super().generic_visit(node)


def _fix_empty_bodies(tree: ast.AST) -> None:
    """A deleted lone statement leaves `def f():` with nothing after it.
    `ast.unparse` cannot render that, so put a `pass` in."""
    for node in ast.walk(tree):
        for fname in ("body", "orelse", "finalbody"):
            body = getattr(node, fname, None)
            if isinstance(body, list) and not body and fname != "orelse":
                setattr(node, fname, [ast.Pass()])
            elif isinstance(body, list) and not body and fname == "orelse":
                pass  # an empty `orelse` is legal; it simply renders as nothing
        for handler in getattr(node, "handlers", []) or []:
            if not handler.body:
                handler.body = [ast.Pass()]


def apply_mutant(source: str, mutant: Mutant, operators: Sequence[Operator] = ALL_OPERATORS) -> str:
    """Return the source with exactly `mutant` applied."""
    op = next((o for o in operators if o.name == mutant.operator), None)
    if op is None:
        raise ValueError(f"unknown operator {mutant.operator!r}")
    tree = ast.parse(source)
    index_nodes(tree)
    tf = _SingleSiteTransformer(mutant.node_index, op, mutant.slot)
    new_tree = tf.visit(tree)
    if tf.applied != 1:
        raise ValueError(
            f"mutation site {mutant.mid} matched {tf.applied} nodes, expected 1"
        )
    _fix_empty_bodies(new_tree)
    ast.fix_missing_locations(new_tree)
    return ast.unparse(new_tree)


def null_mutant_source(source: str) -> str:
    """The identity mutation: parse and unparse, nothing else.

    If the tests fail on THIS, the harness is the bug, not the module."""
    return ast.unparse(ast.parse(source))


# =================================================================== running


class Sandbox:
    """An isolated copy of the repo that a mutant can be written into.

    The working tree is never touched. Other agents are editing this repo
    concurrently; a harness that mutated files in place would hand them a
    corrupted module the moment it crashed.
    """

    def __init__(self, root: Path, source_root: Path = REPO_ROOT) -> None:
        self.root = Path(root)
        self.source_root = Path(source_root)
        self._originals: dict[str, str] = {}

    def build(self, dirs: Iterable[str] = SANDBOX_DIRS) -> "Sandbox":
        self.root.mkdir(parents=True, exist_ok=True)
        ignore = shutil.ignore_patterns(
            "__pycache__", "*.pyc", ".pytest_cache", ".hypothesis", "*.jsonl"
        )
        for d in dirs:
            src = self.source_root / d
            if not src.exists():
                continue
            shutil.copytree(src, self.root / d, ignore=ignore, dirs_exist_ok=True)
        return self

    def snapshot(self, rel_path: str) -> None:
        p = self.root / rel_path
        self._originals[rel_path] = p.read_text(encoding="utf-8")

    def write(self, rel_path: str, source: str) -> None:
        if rel_path not in self._originals:
            self.snapshot(rel_path)
        (self.root / rel_path).write_text(source, encoding="utf-8")

    def restore(self, rel_path: str) -> None:
        if rel_path in self._originals:
            (self.root / rel_path).write_text(self._originals[rel_path], encoding="utf-8")

    def destroy(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def sandbox_dirs_for(module: str, tests: Sequence[str]) -> tuple[str, ...]:
    """Top-level directories a sandbox needs: the module's, the tests', and
    anything else the suite imports (`tools`, for this repo)."""
    wanted = {Path(module).parts[0]}
    wanted.update(Path(t).parts[0] for t in tests)
    wanted.update(d for d in SANDBOX_DIRS)
    return tuple(sorted(wanted))


def _kill_tree(proc: "subprocess.Popen[bytes]") -> None:
    """SIGKILL the child AND everything it spawned.

    This is not defensive tidiness, it is a correctness requirement, and it was
    written after watching the harness poison its own measurements.

    `tests/test_kernel.py` launches eight OS subprocesses to prove that
    exactly-once is the database's job and not a Python lock's. When a mutant
    made that test hang and `subprocess.run(timeout=…)` fired, it killed the
    pytest process and left those eight grandchildren orphaned — still holding
    an `flock` on a shared ledger, still spinning, still there twenty-eight
    minutes later. Every subsequent mutant then ran on a machine with a growing
    crowd of CPU-burning orphans, so mutants started timing out because the box
    was busy rather than because the code was broken, and a timeout is scored
    as a KILL. Left alone, the harness would have reported a kill rate that its
    own leaked processes manufactured.

    Putting the child in its own session means one `killpg` reaches the whole
    tree. INVARIANT 9: a number a broken measurement produced is not a number.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def run_tests(
    sandbox: Sandbox,
    tests: Sequence[str],
    *,
    python: str = sys.executable,
    timeout: float = 120.0,
    extra_args: Sequence[str] = (),
) -> tuple[int, float, str]:
    """Run the given test files inside the sandbox. Returns (rc, seconds, tail)."""
    cmd = [
        python, "-m", "pytest", *tests,
        "-x", "-q", "--no-header", "--tb=no",
        "-p", "no:cacheprovider",
        "-p", "no:randomly",
        *extra_args,
    ]
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    env[CHILD_ENV] = "1"
    env.pop("COVERAGE_PROCESS_START", None)
    env.pop("COVERAGE_PROCESS_CONFIG", None)
    t0 = time.monotonic()
    proc = subprocess.Popen(
        cmd, cwd=str(sandbox.root), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True,          # its own process group; see _kill_tree
    )
    try:
        out_bytes, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - unkillable child
            pass
        return -9, time.monotonic() - t0, "TIMEOUT"
    out = (out_bytes or b"").decode("utf-8", "replace")
    tail = "\n".join(out.strip().splitlines()[-3:])
    return proc.returncode, time.monotonic() - t0, tail


def _hypothesis_seed_args() -> list[str]:
    """`--hypothesis-seed=0` if the plugin is installed. Determinism matters:
    a property test that draws different examples per run would make a mutant
    KILLED on Tuesday and SURVIVED on Wednesday, and a kill rate you cannot
    reproduce is not a measurement."""
    try:
        import hypothesis  # noqa: F401
    except Exception:
        return []
    return ["--hypothesis-seed=0"]


class MutationRunner:
    def __init__(
        self,
        *,
        jobs: int = 4,
        # Generous on purpose. A mutant that times out is scored as KILLED, so
        # a timeout that only means "the box was busy" is a fabricated kill.
        timeout_factor: float = 10.0,
        min_timeout: float = 60.0,
        baseline_attempts: int = 3,
        python: str = sys.executable,
        source_root: Path = REPO_ROOT,
        workdir: Optional[Path] = None,
        progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.jobs = max(1, int(jobs))
        self.timeout_factor = timeout_factor
        self.min_timeout = min_timeout
        self.baseline_attempts = max(1, int(baseline_attempts))
        self.python = python
        self.source_root = Path(source_root)
        self.workdir = Path(workdir) if workdir else None
        self.progress = progress or (lambda msg: None)

    # -- one module ------------------------------------------------------

    def run_module(
        self,
        module: str,
        tests: Sequence[str],
        *,
        operators: Sequence[Operator] = ALL_OPERATORS,
        limit: Optional[int] = None,
    ) -> ModuleReport:
        src_path = self.source_root / module
        source = src_path.read_text(encoding="utf-8")
        mutants = generate_mutants(source, module, operators)
        if limit is not None:
            mutants = mutants[:limit]
        report = ModuleReport(module=module, tests=list(tests), total=len(mutants))

        tmp_parent = self.workdir or Path(tempfile.mkdtemp(prefix="gawaah-mut-"))
        tmp_parent.mkdir(parents=True, exist_ok=True)
        pool: "queue.Queue[Sandbox]" = queue.Queue()
        boxes: list[Sandbox] = []
        try:
            n_boxes = min(self.jobs, max(1, len(mutants))) if mutants else 1
            dirs = sandbox_dirs_for(module, tests)
            for i in range(n_boxes):
                box = Sandbox(tmp_parent / f"box{i}", self.source_root).build(dirs)
                box.snapshot(module)
                boxes.append(box)
                pool.put(box)

            probe = boxes[0]
            seed_args = _hypothesis_seed_args()

            # -- BASELINE: the untouched module must be green in the sandbox.
            #
            # Retried, and ONLY here. This is the harness's own precondition, not
            # a mutant verdict: tests/test_kernel.py synchronises eight OS
            # processes on a two-second wall-clock barrier, and a machine busy
            # building the next sandbox can miss it. A mutant is never retried —
            # that would turn a flaky test into a kill it did not earn — but
            # aborting a whole module because the unmutated suite blinked once
            # throws away a real measurement. The attempt count is reported.
            attempts = 0
            for attempt in range(1, self.baseline_attempts + 1):
                attempts = attempt
                rc, secs, tail = run_tests(
                    probe, tests, python=self.python, timeout=600.0,
                    extra_args=seed_args,
                )
                report.baseline_s = secs
                if rc == 0:
                    break
                self.progress(
                    f"  baseline attempt {attempt}/{self.baseline_attempts} "
                    f"FAILED (rc={rc}): {tail}"
                )
            report.baseline_attempts = attempts
            if rc != 0:
                report.aborted = (
                    f"baseline tests FAILED in sandbox {attempts} time(s) "
                    f"(rc={rc}): {tail}"
                )
                return report
            self.progress(
                f"  baseline green in {secs:.2f}s"
                + (f" (after {attempts} attempts)" if attempts > 1 else "")
            )

            # -- NULL MUTANT: unparse(parse(src)) must also be green.
            probe.write(module, null_mutant_source(source))
            rc, nsecs, tail = run_tests(
                probe, tests, python=self.python, timeout=600.0, extra_args=seed_args
            )
            probe.restore(module)
            if rc != 0:
                report.aborted = (
                    "NULL MUTANT FAILED — ast.unparse(ast.parse(src)) does not pass "
                    f"the tests, so every kill would be an artefact (rc={rc}): {tail}"
                )
                return report
            self.progress(f"  null mutant green in {nsecs:.2f}s")

            # Every mutant runs against the UNPARSED baseline, so formatting is
            # never the difference between killed and survived.
            timeout = max(self.min_timeout, secs * self.timeout_factor)

            done = 0

            def one(m: Mutant) -> MutantResult:
                nonlocal done
                box = pool.get()
                try:
                    try:
                        mutated = apply_mutant(source, m, operators)
                    except Exception as exc:
                        return MutantResult(m, ERROR, 0.0, f"apply failed: {exc}")
                    try:
                        compile(mutated, m.module, "exec")
                    except SyntaxError as exc:
                        return MutantResult(m, ERROR, 0.0, f"invalid mutant: {exc}")
                    box.write(m.module, mutated)
                    try:
                        rc, secs_, tail_ = run_tests(
                            box, tests, python=self.python,
                            timeout=timeout, extra_args=seed_args,
                        )
                    finally:
                        box.restore(m.module)
                    if rc == -9 and tail_ == "TIMEOUT":
                        return MutantResult(m, TIMEOUT, secs_, "exceeded timeout")
                    status = SURVIVED if rc == 0 else KILLED
                    return MutantResult(m, status, secs_, tail_)
                finally:
                    pool.put(box)
                    done += 1
                    if done % 25 == 0:
                        self.progress(f"  {done}/{len(mutants)} mutants run")

            with concurrent.futures.ThreadPoolExecutor(max_workers=n_boxes) as ex:
                results = list(ex.map(one, mutants))

            report.results = results
            for r in results:
                if r.status == KILLED:
                    report.killed += 1
                elif r.status == SURVIVED:
                    report.survived += 1
                elif r.status == TIMEOUT:
                    report.timeout += 1
                    report.killed += 1
                else:
                    report.errored += 1
            return report
        finally:
            for b in boxes:
                b.destroy()
            if self.workdir is None:
                shutil.rmtree(tmp_parent, ignore_errors=True)


# ==================================================================== report


def format_report(reports: Sequence[ModuleReport], *, show_survivors: int = 40) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("MUTATION TEST REPORT")
    lines.append("=" * 78)
    lines.append(f"{'module':<24}{'mutants':>9}{'killed':>8}{'survived':>10}{'kill rate':>12}")
    lines.append("-" * 78)
    tot_m = tot_k = tot_s = 0
    for r in reports:
        if r.aborted:
            lines.append(f"{r.module:<24}  ABORTED: {r.aborted[:60]}")
            continue
        tot_m += r.scored
        tot_k += r.killed
        tot_s += r.survived
        lines.append(
            f"{r.module:<24}{r.scored:>9}{r.killed:>8}{r.survived:>10}"
            f"{r.kill_rate * 100:>11.1f}%"
        )
    lines.append("-" * 78)
    rate = (tot_k / tot_m * 100) if tot_m else 0.0
    lines.append(f"{'TOTAL':<24}{tot_m:>9}{tot_k:>8}{tot_s:>10}{rate:>11.1f}%")
    lines.append("")
    for r in reports:
        surv = r.survivors()
        if not surv:
            continue
        lines.append(f"--- SURVIVORS in {r.module} ({len(surv)}) " + "-" * 20)
        for s in surv[:show_survivors]:
            lines.append(f"  L{s.mutant.lineno:<5} {s.mutant.operator:<20} "
                         f"{s.mutant.before}  ->  {s.mutant.after}")
            if s.mutant.context:
                lines.append(f"         | {s.mutant.context}")
        if len(surv) > show_survivors:
            lines.append(f"  ... and {len(surv) - show_survivors} more (see JSON)")
        lines.append("")
    return "\n".join(lines)


def write_json(reports: Sequence[ModuleReport], path: Path, meta: Optional[dict] = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    scored = sum(r.scored for r in reports if not r.aborted)
    killed = sum(r.killed for r in reports if not r.aborted)
    doc = {
        "tool": "tools/mutate.py",
        "generated_by": "running code (INVARIANT 9)",
        "operators": list(OPERATOR_NAMES),
        "totals": {
            "scored_mutants": scored,
            "killed": killed,
            "survived": sum(r.survived for r in reports if not r.aborted),
            "kill_rate": round(killed / scored, 4) if scored else 0.0,
            "kill_rate_pct": round(killed / scored * 100, 2) if scored else 0.0,
        },
        "modules": [r.to_dict() for r in reports],
    }
    if meta:
        doc["meta"] = meta
    path.write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n", encoding="utf-8")


# ======================================================================= cli


def _resolve_operators(names: Optional[str]) -> list[Operator]:
    if not names:
        return list(ALL_OPERATORS)
    want = [n.strip() for n in names.split(",") if n.strip()]
    unknown = [n for n in want if n not in OPERATOR_NAMES]
    if unknown:
        raise SystemExit(f"unknown operator(s): {unknown}; known: {list(OPERATOR_NAMES)}")
    return [o for o in ALL_OPERATORS if o.name in want]


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--module", action="append", default=[],
                    help="module to mutate, e.g. gawaah/money.py (repeatable)")
    ap.add_argument("--tests", action="append", default=[],
                    help="comma-separated test files for the matching --module")
    ap.add_argument("--money-path", action="store_true",
                    help="mutate the six money-critical modules with their own tests")
    ap.add_argument("--with-mutation-tests", action="store_true",
                    help=f"also run {MUTATION_TEST_FILE} against every module")
    ap.add_argument("--ops", default=None,
                    help=f"comma-separated subset of {list(OPERATOR_NAMES)}")
    ap.add_argument("--jobs", type=int, default=max(2, (os.cpu_count() or 4) - 1))
    ap.add_argument("--limit", type=int, default=None, help="first N mutants per module")
    ap.add_argument("--list", action="store_true", help="list mutants, run nothing")
    ap.add_argument("--out", default=None, help="write JSON report here")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--min-kill-rate", type=float, default=None,
                    help="exit non-zero if the overall kill rate is below this (0-1)")
    args = ap.parse_args(argv)

    targets: list[tuple[str, tuple[str, ...]]] = []
    if args.money_path:
        targets.extend(MONEY_PATH_TARGETS)
    for i, mod in enumerate(args.module):
        if i < len(args.tests):
            targets.append((mod, tuple(t.strip() for t in args.tests[i].split(","))))
        else:
            guess = f"tests/test_{Path(mod).stem}.py"
            targets.append((mod, (guess,)))
    if not targets:
        ap.error("nothing to do: pass --module/--tests or --money-path")

    if args.with_mutation_tests:
        targets = [
            (m, tuple(list(t) + [MUTATION_TEST_FILE]) if MUTATION_TEST_FILE not in t else t)
            for m, t in targets
        ]

    operators = _resolve_operators(args.ops)
    say = (lambda m: None) if args.quiet else (lambda m: print(m, flush=True))

    if args.list:
        for mod, _ in targets:
            src = (REPO_ROOT / mod).read_text(encoding="utf-8")
            ms = generate_mutants(src, mod, operators)
            say(f"{mod}: {len(ms)} mutants")
            for m in ms if args.limit is None else ms[: args.limit]:
                say(f"  L{m.lineno:<5} {m.operator:<20} {m.before} -> {m.after}")
        return 0

    runner = MutationRunner(jobs=args.jobs, progress=say)
    reports: list[ModuleReport] = []
    t0 = time.monotonic()
    for mod, tests in targets:
        say(f"\n>>> {mod}  (tests: {', '.join(tests)})")
        rep = runner.run_module(mod, tests, operators=operators, limit=args.limit)
        reports.append(rep)
        if rep.aborted:
            say(f"  ABORTED: {rep.aborted}")
        else:
            say(f"  {rep.killed}/{rep.scored} killed = {rep.kill_rate * 100:.1f}% "
                f"({rep.survived} survived, {rep.timeout} timeout, {rep.errored} error)")
    elapsed = time.monotonic() - t0

    say("")
    say(format_report(reports))
    say(f"wall clock: {elapsed:.1f}s  jobs: {args.jobs}")

    if args.out:
        write_json(reports, Path(args.out), meta={
            "wall_clock_s": round(elapsed, 1),
            "jobs": args.jobs,
            "with_mutation_tests": bool(args.with_mutation_tests),
            "python": sys.version.split()[0],
        })
        say(f"wrote {args.out}")

    if any(r.aborted for r in reports):
        return 2
    if args.min_kill_rate is not None:
        scored = sum(r.scored for r in reports)
        killed = sum(r.killed for r in reports)
        rate = killed / scored if scored else 0.0
        if rate < args.min_kill_rate:
            say(f"FAIL: kill rate {rate:.3f} < required {args.min_kill_rate:.3f}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
