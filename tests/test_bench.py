"""S7 acceptance: the bench harness itself.

A measurement tool nobody has measured is a rumour. Four things are load-bearing
and each has a test that would fail if it stopped being true:

  * the run reaches the end and writes both artefacts
  * metrics.json is deterministic across two runs with the same seeds
  * verify_claims catches a number that has drifted from metrics.json
  * a module that has not been written yet is reported NOT BUILT, and the rest
    of the run still happens

The last one is not hypothetical. Four modules landed in gawaah/ between two
runs of the bench while this file was being written.

WHAT THE FOUR ABOVE DO NOT COVER, AND WHY THE SECOND HALF OF THIS FILE EXISTS
----------------------------------------------------------------------------
All four are PLUMBING tests. Every one of them passes on a harness whose
arithmetic is wrong: they check that a number arrived, is stable, and is
anchored — never that it is the RIGHT number. A mutation pass over
`tools/bench.py` made that concrete: 44 semantic mutations inside the five
benchmark bodies and the statistics they publish through, and this file killed
ZERO of them. `worst` could have been the best sample, the RMSE could have been
a mean square, a footprint error could have been measured against the wrong
edge, and the suite would have stayed green while the README published the
result. Invariant 9 says every published number is produced by running code; it
is worth nothing if nothing checks what the code computes.

So the second half of this file is KNOWN-ANSWER tests. Each one builds a
stimulus whose correct metric value is derivable on paper — an RMSE over
corners displaced by a known vector, a footprint whose measurement error is
scripted to 0.3 mm, a ledger verified against a fake clock so lines-per-second
is exact arithmetic — runs the real benchmark body, and asserts the number.
`test_every_benchmark_has_a_known_answer_test` refuses to let a sixth benchmark
publish numbers without one.
"""
from __future__ import annotations

import importlib
import json
import math
import re
import sys
import textwrap
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bench  # noqa: E402


TINY = dict(scale=0.1)
SEEDS = [0, 1]


# --------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def run_dir(tmp_path_factory) -> Path:
    """One real, full end-to-end run, shared by every test that needs one."""
    out = tmp_path_factory.mktemp("bench-run")
    rc = bench.main(["--seeds", "0,1", "--scale", "0.1", "--out", str(out), "--quiet"])
    assert rc == 0, "the bench must exit 0 on a clean run"
    return out


@pytest.fixture(scope="module")
def metrics(run_dir: Path) -> dict:
    return json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))


@pytest.fixture()
def scratch(tmp_path: Path, run_dir: Path) -> tuple[Path, Path]:
    """A private, mutable copy of the generated pair, for drift injection."""
    md = tmp_path / "METRICS.md"
    js = tmp_path / "metrics.json"
    md.write_text((run_dir / "METRICS.md").read_text(encoding="utf-8"), encoding="utf-8")
    js.write_text((run_dir / "metrics.json").read_text(encoding="utf-8"), encoding="utf-8")
    return md, js


# --------------------------------------------------------------- discovery

def test_probe_finds_a_module_that_exists():
    p = bench.probe_module("gawaah.ledger")
    assert p.built and p.detail == ""


def test_probe_reports_a_module_nobody_has_written_without_raising():
    p = bench.probe_module("gawaah.definitely_not_a_module")
    assert not p.built
    assert "definitely_not_a_module" in p.detail


def test_probe_survives_a_module_that_explodes_on_import(tmp_path):
    """A half-saved file from another agent must not take the bench down."""
    pkg = tmp_path / "benchprobe"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "boom.py").write_text("raise RuntimeError('half written')\n", encoding="utf-8")
    (pkg / "syntax.py").write_text("def f(:\n", encoding="utf-8")
    (pkg / "exiter.py").write_text("import sys; sys.exit(3)\n", encoding="utf-8")
    (pkg / "fine.py").write_text("VALUE = 1\n", encoding="utf-8")

    sys.path.insert(0, str(tmp_path))
    importlib.invalidate_caches()
    try:
        boom = bench.probe_module("benchprobe.boom")
        syn = bench.probe_module("benchprobe.syntax")
        ex = bench.probe_module("benchprobe.exiter")
        fine = bench.probe_module("benchprobe.fine")
    finally:
        sys.path.remove(str(tmp_path))
        for m in list(sys.modules):
            if m.startswith("benchprobe"):
                del sys.modules[m]

    assert not boom.built and "RuntimeError" in boom.detail
    assert not syn.built and "SyntaxError" in syn.detail
    assert not ex.built and "sys.exit" in ex.detail
    assert fine.built, "a healthy sibling must still import"


def test_package_modules_enumerates_what_is_on_disk():
    found = bench.package_modules()
    assert "gawaah.ledger" in found and "gawaah.takhti" in found
    assert found == tuple(sorted(found)), "enumeration must be ordered"


def test_discover_never_raises_on_a_mixed_list():
    got = bench.discover(["gawaah.money", "gawaah.nope", "not_a_package.at_all"])
    assert got["gawaah.money"].built
    assert not got["gawaah.nope"].built
    assert not got["not_a_package.at_all"].built


# --------------------------------------------------------------- end to end

def test_run_writes_both_files(run_dir: Path):
    assert (run_dir / "metrics.json").is_file()
    assert (run_dir / "METRICS.md").is_file()
    assert (run_dir / "METRICS.md").read_text(encoding="utf-8").strip()


def test_metrics_json_has_the_agreed_schema(metrics: dict):
    for key in ("generated_at", "git_sha", "seeds", "benchmarks"):
        assert key in metrics, f"schema requires {key}"
    assert metrics["seeds"] == SEEDS
    assert isinstance(metrics["benchmarks"], dict) and metrics["benchmarks"]
    for name, b in metrics["benchmarks"].items():
        assert b["status"] in (bench.STATUS_OK, bench.STATUS_NOT_BUILT,
                               bench.STATUS_ERROR, bench.STATUS_SKIPPED,
                               bench.STATUS_NOT_MEASURED), name
        assert b["modules"] and b["unit"] and b["what"]


def test_every_benchmark_reports_mean_and_worst_not_just_best(metrics: dict):
    """A harness that quotes its best seed is a brochure."""
    for name, b in metrics["benchmarks"].items():
        if b["status"] != bench.STATUS_OK:
            continue
        for k in ("mean", "worst", "best", "median", "p95", "n"):
            assert k in b, f"{name} is missing {k}"
        assert b["n"] >= len(SEEDS)
        lo, hi = min(b["best"], b["worst"]), max(b["best"], b["worst"])
        assert lo <= b["mean"] <= hi, f"{name}: mean outside [best, worst]"
        if b["polarity"] == bench.LOWER_BETTER:
            assert b["worst"] >= b["best"]
        else:
            assert b["worst"] <= b["best"]


def test_no_benchmark_errored(metrics: dict):
    errored = {n: b.get("error") for n, b in metrics["benchmarks"].items()
               if b["status"] == bench.STATUS_ERROR}
    assert not errored, errored


def test_per_seed_breakdown_covers_every_committed_seed(metrics: dict):
    for name, b in metrics["benchmarks"].items():
        if b["status"] != bench.STATUS_OK:
            continue
        assert sorted(b["per_seed"]) == sorted(str(s) for s in SEEDS), name
        assert b["worst_seed"] in SEEDS, name


# ------------------------------------------------------------ determinism

def test_two_runs_with_the_same_seeds_agree(tmp_path):
    a = bench.run_benchmarks(SEEDS, scale=0.1)
    b = bench.run_benchmarks(SEEDS, scale=0.1)
    assert a["content_hash"] == b["content_hash"]
    assert bench.deterministic_view(a) == bench.deterministic_view(b)


def test_the_hash_ignores_the_clock_and_the_timings():
    a = bench.run_benchmarks(SEEDS, scale=0.1)
    b = json.loads(json.dumps(a))
    b["generated_at"] = "1999-01-01T00:00:00.000+00:00"
    b["git_sha"] = "deadbeef"
    for blk in b["benchmarks"].values():
        blk.pop("nondeterministic", None)
    assert bench.content_hash(b) == a["content_hash"]


def test_a_changed_measurement_does_change_the_hash():
    a = bench.run_benchmarks(SEEDS, scale=0.1)
    b = json.loads(json.dumps(a))
    victim = next(n for n, x in b["benchmarks"].items()
                  if x["status"] == bench.STATUS_OK and x["deterministic"])
    b["benchmarks"][victim]["mean"] = b["benchmarks"][victim]["mean"] + 1.0
    assert bench.content_hash(b) != a["content_hash"], (
        "the content hash must not be blind to the numbers it covers"
    )


def test_different_seeds_give_a_different_hash():
    a = bench.run_benchmarks([0, 1], scale=0.1)
    b = bench.run_benchmarks([2, 3], scale=0.1)
    assert a["content_hash"] != b["content_hash"]


def test_timing_only_benchmarks_are_excluded_from_the_hash():
    a = bench.run_benchmarks(SEEDS, scale=0.1)
    view = bench.deterministic_view(a)
    assert "ledger_verify_throughput" not in view["benchmarks"], (
        "a wall-clock timing must never be inside a reproducibility claim"
    )
    assert "plane_reproj_rmse_px" in view["benchmarks"]


# ---------------------------------------------------------------- NOT BUILT

def _fake_bench(name: str, modules: tuple[str, ...], fn=None) -> bench.Bench:
    def _ok(seeds, scale):
        s = bench.Samples()
        for i, seed in enumerate(seeds):
            s.per_seed[seed] = [1.0 + i]
        s.detail = {"note": "synthetic"}
        return s
    return bench.Bench(name, modules, "unit", bench.LOWER_BETTER, 3, True,
                       "a stand-in", fn or _ok)


def test_a_missing_module_is_NOT_BUILT_and_the_run_continues(monkeypatch):
    present = _fake_bench("present_bench", ("gawaah.ledger",))
    absent = _fake_bench("absent_bench", ("gawaah.not_written_yet",))
    monkeypatch.setattr(bench, "BENCHES", (absent, present))

    m = bench.run_benchmarks(SEEDS, scale=0.1)
    assert m["benchmarks"]["absent_bench"]["status"] == bench.STATUS_NOT_BUILT
    assert m["benchmarks"]["absent_bench"]["missing_modules"] == \
        ["gawaah.not_written_yet"]
    assert m["benchmarks"]["present_bench"]["status"] == bench.STATUS_OK, (
        "one absent module must not stop the benchmarks that can run"
    )


def test_not_built_survives_rendering_and_verification(monkeypatch, tmp_path):
    monkeypatch.setattr(bench, "BENCHES", (
        _fake_bench("absent_bench", ("gawaah.not_written_yet",)),
        _fake_bench("present_bench", ("gawaah.ledger",)),
    ))
    rc = bench.main(["--seeds", "0,1", "--scale", "0.1",
                     "--out", str(tmp_path), "--quiet"])
    assert rc == 0
    md = (tmp_path / "METRICS.md").read_text(encoding="utf-8")
    assert "NOT_BUILT" in md and "NOT BUILT" in md
    rep = bench.verify_claims(tmp_path / "METRICS.md", tmp_path / "metrics.json")
    assert rep.ok, rep.summary()


def test_a_benchmark_that_raises_is_contained(monkeypatch, tmp_path):
    def explode(seeds, scale):
        raise ZeroDivisionError("the benchmark itself is broken")

    monkeypatch.setattr(bench, "BENCHES", (
        _fake_bench("boom_bench", ("gawaah.ledger",), explode),
        _fake_bench("good_bench", ("gawaah.ledger",)),
    ))
    m = bench.run_benchmarks(SEEDS, scale=0.1)
    assert m["benchmarks"]["boom_bench"]["status"] == bench.STATUS_ERROR
    assert "ZeroDivisionError" in m["benchmarks"]["boom_bench"]["error"]
    assert m["benchmarks"]["good_bench"]["status"] == bench.STATUS_OK

    # ... but the CLI must still fail, so a broken benchmark cannot pass CI.
    monkeypatch.setattr(bench, "BENCHES", (
        _fake_bench("boom_bench", ("gawaah.ledger",), explode),
    ))
    assert bench.main(["--seeds", "0,1", "--out", str(tmp_path), "--quiet"]) == 1


# ------------------------------------------------------------ verify_claims

def test_the_generated_markdown_verifies_against_its_own_json(run_dir: Path):
    rep = bench.verify_claims(run_dir / "METRICS.md", run_dir / "metrics.json")
    assert rep.ok, rep.summary()
    assert rep.checked > 10, "verification must actually be checking things"
    assert not rep.drifted and not rep.unresolved and not rep.unanchored


def test_a_drifted_table_number_is_caught(scratch):
    md, js = scratch
    text = md.read_text(encoding="utf-8")
    row = next(ln for ln in text.splitlines() if "<!--@row bench:" in ln)
    # nudge the first number in the row: this is the hand-edited README case
    bad = re.sub(r"\d", lambda m: str((int(m.group()) + 1) % 10), row, count=1)
    md.write_text(text.replace(row, bad), encoding="utf-8")

    rep = bench.verify_claims(md, js)
    assert not rep.ok
    assert rep.drifted and rep.drifted[0].kind == "row"
    assert "DRIFT" in rep.summary()


def test_a_drifted_prose_number_is_caught(scratch):
    md, js = scratch
    metrics = json.loads(js.read_text(encoding="utf-8"))
    path = "plane_reproj_rmse_px.worst"
    truth = bench.resolve_path(metrics, path)
    md.write_text(f"the worst was `{truth + 1.0:.4f}` px <!--@ {path} -->\n",
                  encoding="utf-8")
    rep = bench.verify_claims(md, js)
    assert not rep.ok and len(rep.drifted) == 1
    assert rep.drifted[0].path == path


def test_a_number_written_to_fewer_decimals_still_verifies(scratch):
    md, js = scratch
    metrics = json.loads(js.read_text(encoding="utf-8"))
    truth = bench.resolve_path(metrics, "plane_reproj_rmse_px.worst")
    md.write_text(f"worst `{truth:.2f}` px <!--@ plane_reproj_rmse_px.worst -->\n",
                  encoding="utf-8")
    assert bench.verify_claims(md, js).ok, "rounding down is allowed"

    md.write_text(f"worst `{truth + 0.01:.2f}` px "
                  f"<!--@ plane_reproj_rmse_px.worst -->\n", encoding="utf-8")
    assert not bench.verify_claims(md, js).ok, "but not rounding to a wrong value"


def test_a_number_with_no_provenance_fails_strict_and_passes_lenient(scratch):
    md, js = scratch
    md.write_text("we measured 42 things and it was great.\n", encoding="utf-8")
    strict = bench.verify_claims(md, js, strict=True)
    assert not strict.ok
    assert [n for _ln, n, _t in strict.unanchored] == ["42"]
    assert "UNANCHORED" in strict.summary()
    assert bench.verify_claims(md, js, strict=False).ok


def test_an_anchor_naming_a_dead_path_is_unresolved(scratch):
    md, js = scratch
    md.write_text("`0.5` <!--@ plane_reproj_rmse_px.renamed_field -->\n",
                  encoding="utf-8")
    rep = bench.verify_claims(md, js)
    assert not rep.ok and len(rep.unresolved) == 1
    assert "UNRESOLVED" in rep.summary()


def test_an_unknown_row_kind_is_unresolved(scratch):
    md, js = scratch
    md.write_text("| a | b | <!--@row nonsense:thing -->\n", encoding="utf-8")
    rep = bench.verify_claims(md, js)
    assert not rep.ok and rep.unresolved


def test_a_row_naming_a_dead_benchmark_is_unresolved(scratch):
    md, js = scratch
    md.write_text("| a | b | <!--@row bench:no_such_bench -->\n", encoding="utf-8")
    rep = bench.verify_claims(md, js)
    assert not rep.ok and rep.unresolved


def test_fenced_code_is_exempt(scratch):
    md, js = scratch
    md.write_text(textwrap.dedent("""\
        prose with no digits at all.

        ```
        python tools/bench.py --seeds 5 --out results/
        x = 12345
        ```

        more prose.
        """), encoding="utf-8")
    rep = bench.verify_claims(md, js)
    assert rep.ok, rep.summary()


def test_an_ignore_comment_exempts_one_line(scratch):
    md, js = scratch
    md.write_text("a table header with p95 in it <!-- bench:ignore -->\n",
                  encoding="utf-8")
    assert bench.verify_claims(md, js).ok


def test_a_string_claim_drifts_too(scratch):
    md, js = scratch
    md.write_text("git sha `cafef00d` <!--@ git_sha -->\n", encoding="utf-8")
    rep = bench.verify_claims(md, js)
    assert not rep.ok and rep.drifted[0].path == "git_sha"


def test_the_seed_list_is_checked_element_by_element(scratch):
    md, js = scratch
    md.write_text("seeds `[0, 1]` <!--@ seeds -->\n", encoding="utf-8")
    assert bench.verify_claims(md, js).ok
    md.write_text("seeds `[0, 7]` <!--@ seeds -->\n", encoding="utf-8")
    assert not bench.verify_claims(md, js).ok
    md.write_text("seeds `[0, 1, 2]` <!--@ seeds -->\n", encoding="utf-8")
    assert not bench.verify_claims(md, js).ok, "an extra seed is drift"


def test_an_anchor_with_nothing_to_check_is_drift(scratch):
    md, js = scratch
    md.write_text("no number here <!--@ plane_reproj_rmse_px.worst -->\n",
                  encoding="utf-8")
    rep = bench.verify_claims(md, js)
    assert not rep.ok and rep.drifted


def test_a_second_number_beside_an_anchor_is_not_smuggled_through(scratch):
    """The anchor claims the number nearest it. Anything else on that segment
    still has to justify itself, or a sentence could hide a typed figure right
    next to a generated one."""
    md, js = scratch
    metrics = json.loads(js.read_text(encoding="utf-8"))
    truth = bench.resolve_path(metrics, "plane_reproj_rmse_px.worst")
    md.write_text(f"across 99 rigs the worst was `{truth:.4f}` px "
                  f"<!--@ plane_reproj_rmse_px.worst -->\n", encoding="utf-8")
    rep = bench.verify_claims(md, js)
    assert not rep.ok
    assert [n for _ln, n, _t in rep.unanchored] == ["99"]


def test_shorthand_and_full_paths_both_resolve(metrics):
    assert (bench.resolve_path(metrics, "plane_reproj_rmse_px.worst")
            == bench.resolve_path(metrics, "benchmarks.plane_reproj_rmse_px.worst"))
    assert bench.resolve_path(metrics, "seeds.0") == SEEDS[0]
    with pytest.raises(bench.ClaimError):
        bench.resolve_path(metrics, "nope.nope")


# ------------------------------------------------------------------- units

@pytest.mark.parametrize("polarity,worst,best", [
    (bench.LOWER_BETTER, 9.0, 1.0),
    (bench.HIGHER_BETTER, 1.0, 9.0),
])
def test_summarise_worst_follows_polarity(polarity, worst, best):
    s = bench.summarise([1.0, 5.0, 9.0], polarity)
    assert s["worst"] == worst and s["best"] == best
    assert s["mean"] == 5.0 and s["median"] == 5.0 and s["n"] == 3


def test_summarise_of_nothing_is_not_a_zero():
    assert bench.summarise([], bench.LOWER_BETTER) == {"n": 0}


def test_percentile_never_invents_a_value():
    vals = [1.0, 2.0, 3.0, 4.0, 100.0]
    assert bench._percentile(vals, 0.95) in vals
    assert bench._percentile(vals, 0.5) in vals


@pytest.mark.parametrize("spec,want", [
    ("5", [0, 1, 2, 3, 4]), ("1", [0]), ("0,3,7", [0, 3, 7]), (" 2 ", [0, 1]),
])
def test_parse_seeds(spec, want):
    assert bench.parse_seeds(spec) == want


def test_parse_seeds_refuses_nonsense():
    with pytest.raises(ValueError):
        bench.parse_seeds("0")
    with pytest.raises(ValueError):
        bench.parse_seeds("many")


def test_fmt_is_stable():
    assert bench._fmt(None, 3) == "n/a"
    assert bench._fmt(True, 3) == "true"
    assert bench._fmt(7, 3) == "7"
    assert bench._fmt(0.5, 3) == "0.500"
    assert bench._fmt(float("nan"), 2) == "nan"


# --------------------------------------------------------------------- CLI

def test_cli_only_filter_skips_the_rest(tmp_path):
    rc = bench.main(["--seeds", "0,1", "--scale", "0.1", "--out", str(tmp_path),
                     "--only", "ledger_verify_throughput", "--quiet"])
    assert rc == 0
    m = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert m["benchmarks"]["ledger_verify_throughput"]["status"] == bench.STATUS_OK
    assert m["benchmarks"]["plane_reproj_rmse_px"]["status"] == bench.STATUS_SKIPPED


def test_cli_rejects_an_unknown_benchmark_name(tmp_path):
    assert bench.main(["--out", str(tmp_path), "--only", "no_such_bench"]) == 2


def test_cli_verify_mode_exit_codes(run_dir, tmp_path):
    good = bench.main(["--verify", str(run_dir / "METRICS.md"),
                       "--metrics", str(run_dir / "metrics.json"), "--quiet"])
    assert good == 0

    bad = tmp_path / "drifted.md"
    bad.write_text("`999.0` px <!--@ plane_reproj_rmse_px.worst -->\n",
                   encoding="utf-8")
    assert bench.main(["--verify", str(bad),
                       "--metrics", str(run_dir / "metrics.json"), "--quiet"]) == 1

    assert bench.main(["--verify", str(bad), "--metrics",
                       str(tmp_path / "absent.json"), "--quiet"]) == 2


def test_the_cli_self_verifies_before_it_exits_zero(tmp_path, monkeypatch):
    """A run that emits a markdown its own json contradicts must not pass."""
    real = bench.render_markdown

    def sabotage(metrics):
        return real(metrics).replace("| OK |", "| OK |", 1) + \
            "\nsmuggled 12345\n"

    monkeypatch.setattr(bench, "render_markdown", sabotage)
    assert bench.main(["--seeds", "0,1", "--scale", "0.1",
                       "--out", str(tmp_path), "--quiet"]) == 1


# ----------------------------------------------------------------- content

def test_the_module_inventory_reflects_the_disk(metrics):
    assert metrics["modules"]["gawaah.ledger"]["built"] is True
    assert "gawaah.takhti" in metrics["modules"]


def test_markdown_carries_the_provenance_header(run_dir):
    md = (run_dir / "METRICS.md").read_text(encoding="utf-8")
    assert "GENERATED BY tools/bench.py" in md
    assert "Honest limits" in md
    assert "--verify" in md


def test_every_benchmark_declares_what_it_measures():
    names = [b.name for b in bench.BENCHES]
    assert len(names) == len(set(names)), "benchmark names must be unique"
    for b in bench.BENCHES:
        assert b.what and b.unit and b.modules
        assert b.polarity in (bench.LOWER_BETTER, bench.HIGHER_BETTER)
        assert b.decimals >= 0


# =====================================================================
# DEFECT 1 — worst_seed was unpinned.
#
# `_worst_seed` decides which seed the report blames. Inverting it to name the
# BEST seed changed the published output and nothing failed, which is the
# marketing-document failure mode the module docstring says it exists to
# prevent. Three things are pinned below: the direction, the per-seed reduction,
# and the tie-break.
# =====================================================================

def test_worst_seed_names_the_worst_seed_and_never_the_best():
    """Known answer, worked out by hand.

    seed 3 owns the largest single sample (0.95) and seed 7 owns the smallest
    (0.05), so for a lower-is-better metric the worst seed is 3 and for a
    higher-is-better metric it is 7. Any inversion swaps them.
    """
    s = bench.Samples(per_seed={3: [0.10, 0.95], 5: [0.90, 0.92], 7: [0.05, 0.30]})
    assert bench._worst_seed(s, bench.LOWER_BETTER) == 3
    assert bench._worst_seed(s, bench.HIGHER_BETTER) == 7


def test_worst_seed_reduces_each_seed_by_its_own_worst_sample():
    """seed 1 is the worst seed only if a seed is judged by its WORST sample.

    Judged by its best, seed 1 (best 0.01) would look better than seed 2 (best
    0.40) and the blame would land on the wrong seed.
    """
    s = bench.Samples(per_seed={1: [0.01, 9.00], 2: [0.40, 0.41]})
    assert bench._worst_seed(s, bench.LOWER_BETTER) == 1
    s2 = bench.Samples(per_seed={1: [0.99, 0.01], 2: [0.40, 0.41]})
    assert bench._worst_seed(s2, bench.HIGHER_BETTER) == 1


def test_worst_seed_breaks_a_tie_on_the_lowest_seed_number():
    """kernel_exactly_once scores 1.0 on every seed, so the published
    `worst seed` for it is decided ENTIRELY by the tie-break. Left to dict
    insertion order it is whatever order the seeds happened to arrive in."""
    s = bench.Samples(per_seed={9: [1.0], 2: [1.0], 5: [1.0]})
    assert bench._worst_seed(s, bench.HIGHER_BETTER) == 2
    assert bench._worst_seed(s, bench.LOWER_BETTER) == 2


def test_worst_seed_of_a_run_that_measured_nothing_is_none():
    s = bench.Samples(per_seed={1: [], 2: []})
    assert bench._worst_seed(s, bench.LOWER_BETTER) is None
    assert bench._worst_seed(s, bench.HIGHER_BETTER) is None


def test_worst_seed_skips_seeds_that_produced_no_samples():
    s = bench.Samples(per_seed={1: [], 2: [0.5], 3: []})
    assert bench._worst_seed(s, bench.LOWER_BETTER) == 2


def test_the_published_worst_seed_agrees_with_the_published_per_seed_table(metrics):
    """Cross-check between two things the README prints side by side."""
    for name, b in metrics["benchmarks"].items():
        if b["status"] != bench.STATUS_OK:
            continue
        per = {int(s): blk for s, blk in b["per_seed"].items() if blk.get("n")}
        assert per, name
        lower = b["polarity"] == bench.LOWER_BETTER
        want, key = None, None
        for s in sorted(per):
            k = per[s]["worst"] if lower else -per[s]["worst"]
            if key is None or k > key:
                key, want = k, s
        assert b["worst_seed"] == want, (
            f"{name}: table blames seed {b['worst_seed']}, but seed {want} owns "
            f"the worst per-seed figure in the very next table"
        )


# =====================================================================
# DEFECT 2 — a benchmark that measured NOTHING reported STATUS_OK.
#
# summarise([]) is {"n": 0}: no mean, no worst, nothing. The block still said
# OK, so a benchmark that refused every single frame published an OK row with
# `n/a` in every column and looked exactly like a benchmark that had simply not
# been selected. Invariant 7 says abstain with a NAME.
# =====================================================================

def _measures_nothing(seeds, scale):
    s = bench.Samples()
    for seed in seeds:
        s.per_seed[seed] = []      # every stimulus refused
    s.detail = {"note": "every stimulus was refused"}
    return s


def _returns_no_seeds_at_all(seeds, scale):
    s = bench.Samples()
    s.detail = {"note": "the body never populated per_seed"}
    return s


@pytest.mark.parametrize("fn", [_measures_nothing, _returns_no_seeds_at_all])
def test_a_benchmark_that_measured_nothing_is_NOT_MEASURED_not_OK(monkeypatch, fn):
    monkeypatch.setattr(bench, "BENCHES", (
        _fake_bench("empty_bench", ("gawaah.ledger",), fn),
        _fake_bench("real_bench", ("gawaah.ledger",)),
    ))
    m = bench.run_benchmarks(SEEDS, scale=0.1)
    blk = m["benchmarks"]["empty_bench"]
    assert blk["status"] == bench.STATUS_NOT_MEASURED
    assert blk["n"] == 0
    assert "mean" not in blk and "worst" not in blk, (
        "a run with no samples must not carry a summary statistic at all"
    )
    assert blk["worst_seed"] is None
    assert blk["detail"], "the reason the body gave must survive"
    assert m["benchmarks"]["real_bench"]["status"] == bench.STATUS_OK, (
        "one silent benchmark must not stop the rest of the run"
    )


def test_not_measured_is_a_distinct_status_from_not_built_and_skipped():
    assert bench.STATUS_NOT_MEASURED not in (
        bench.STATUS_OK, bench.STATUS_NOT_BUILT, bench.STATUS_ERROR,
        bench.STATUS_SKIPPED,
    )


def test_not_measured_says_so_in_the_markdown_and_still_verifies(monkeypatch, tmp_path):
    monkeypatch.setattr(bench, "BENCHES", (
        _fake_bench("empty_bench", ("gawaah.ledger",), _measures_nothing),
        _fake_bench("real_bench", ("gawaah.ledger",)),
    ))
    assert bench.main(["--seeds", "0,1", "--scale", "0.1",
                       "--out", str(tmp_path), "--quiet"]) == 0
    md = (tmp_path / "METRICS.md").read_text(encoding="utf-8")
    assert bench.STATUS_NOT_MEASURED in md
    assert "measured nothing" in md.lower(), (
        "the page must say it in words, not only in a status cell"
    )
    assert "empty_bench" in md
    rep = bench.verify_claims(tmp_path / "METRICS.md", tmp_path / "metrics.json")
    assert rep.ok, rep.summary()


def test_a_partly_empty_benchmark_is_still_OK_but_names_the_silent_seeds(monkeypatch):
    def half(seeds, scale):
        s = bench.Samples()
        for i, seed in enumerate(seeds):
            s.per_seed[seed] = [] if i else [1.0, 2.0]
        return s

    monkeypatch.setattr(bench, "BENCHES", (
        _fake_bench("half_bench", ("gawaah.ledger",), half),
    ))
    blk = bench.run_benchmarks(SEEDS, scale=0.1)["benchmarks"]["half_bench"]
    assert blk["status"] == bench.STATUS_OK
    assert blk["n"] == 2
    assert blk["seeds_with_no_samples"] == [SEEDS[1]]


# =====================================================================
# DEFECT 3 — the "p95" column was the p5 of every higher-is-better benchmark.
#
# summarise() computed `_percentile(s, 0.95) if lower_is_better else
# _percentile(s, 0.05)` and printed it under a header that said p95. For
# sellevent_recall, kernel_exactly_once and ledger_verify_throughput — three of
# the five — the number under "p95" was the FIFTH percentile. Both percentiles
# are now computed and named, and the row says which one is the tail that
# matters for that benchmark's direction.
# =====================================================================

def test_p95_is_the_ninetyfifth_percentile_whatever_the_direction():
    """Known answer: 20 samples 1.0 .. 20.0. Nearest rank, no interpolation:
    ceil(0.95 * 20) = 19 -> 19.0, and ceil(0.05 * 20) = 1 -> 1.0."""
    vals = [float(i) for i in range(1, 21)]
    lo = bench.summarise(vals, bench.LOWER_BETTER)
    hi = bench.summarise(vals, bench.HIGHER_BETTER)
    assert lo["p95"] == 19.0
    assert hi["p95"] == 19.0, "p95 must not silently become p5 for this direction"
    assert lo["p5"] == 1.0
    assert hi["p5"] == 1.0


def test_the_row_names_which_percentile_is_the_bad_tail():
    vals = [float(i) for i in range(1, 21)]
    assert bench.summarise(vals, bench.LOWER_BETTER)["tail_percentile"] == "p95"
    assert bench.summarise(vals, bench.HIGHER_BETTER)["tail_percentile"] == "p5"


def test_the_bench_table_has_both_percentile_columns_and_labels_the_tail(metrics):
    cols = list(bench._BENCH_COLUMNS)
    for want in ("p95", "p5", "bad tail"):
        assert want in cols, f"the results table must carry a {want!r} column"
    for name, b in metrics["benchmarks"].items():
        if b["status"] != bench.STATUS_OK:
            continue
        row = bench.bench_row(name, metrics)
        tail = row[cols.index("bad tail")]
        assert tail == ("p95" if b["polarity"] == bench.LOWER_BETTER else "p5"), name
        d = int(b["decimals"])
        assert row[cols.index("p95")] == bench._fmt(b["p95"], d)
        assert row[cols.index("p5")] == bench._fmt(b["p5"], d)


def test_the_published_percentiles_sit_where_percentiles_have_to_sit(metrics):
    """p5 <= median <= p95 for every row, and both live inside the observed
    range whichever end of it `best` and `worst` happen to name."""
    for name, b in metrics["benchmarks"].items():
        if b["status"] != bench.STATUS_OK or b["n"] < 3:
            continue
        assert b["p5"] <= b["median"] <= b["p95"], name
        lo, hi = min(b["best"], b["worst"]), max(b["best"], b["worst"])
        assert lo <= b["p5"] <= hi, name
        assert lo <= b["p95"] <= hi, name


def test_percentile_rounds_the_rank_up_so_it_never_undershoots():
    """Nearest-rank with ceil, on sample counts where the rank is FRACTIONAL.

    0.95 * 20 is exactly 19, so twenty samples cannot tell ceil from floor —
    a mutation pass caught this test passing over a floored percentile. Ten
    samples put the rank at 9.5 and seven put it at 6.65, where rounding down
    quietly reports a smaller tail than was actually measured.
    """
    ten = [float(i) for i in range(1, 11)]
    assert bench._percentile(ten, 0.95) == 10.0     # ceil(9.5) = 10 -> the 10th
    seven = [float(i) for i in range(1, 8)]
    assert bench._percentile(seven, 0.95) == 7.0    # ceil(6.65) = 7 -> the 7th
    assert bench._percentile(seven, 0.5) == 4.0     # ceil(3.5) = 4 -> the 4th

    twenty = [float(i) for i in range(1, 21)]
    assert bench._percentile(twenty, 0.95) == 19.0
    assert bench._percentile(twenty, 0.05) == 1.0
    assert bench._percentile([7.0], 0.95) == 7.0
    assert math.isnan(bench._percentile([], 0.95))


def test_summarise_percentiles_use_the_rounded_up_rank_too():
    """The same fractional-rank case, but through the published field rather
    than the private helper, so the column cannot drift from the definition."""
    ten = [float(i) for i in range(1, 11)]
    for polarity in (bench.LOWER_BETTER, bench.HIGHER_BETTER):
        s = bench.summarise(ten, polarity)
        assert s["p95"] == 10.0
        assert s["p5"] == 1.0


# =====================================================================
# KNOWN-ANSWER SUPPORT
# =====================================================================

class _ScriptedRng:
    """A stand-in for numpy's Generator that returns exactly what a test says.

    The benchmarks draw their stimulus from a seeded Generator, which makes them
    reproducible but not PREDICTABLE: nobody can say on paper what recall a
    given seed should produce. Replacing the draws with scripted constants turns
    each benchmark into a case whose right answer can be worked out by hand,
    which is the only way to assert the number rather than its stability.
    """

    def __init__(self, *, one_arg_ints=(0,), two_arg_int=1,
                 uniform=None, normal=0.0):
        self._one = list(one_arg_ints)
        self._i = 0
        self._two = two_arg_int
        self._uniform = uniform or (lambda a, b: (a + b) / 2.0)
        self._normal = normal
        self.calls: list[tuple] = []

    def integers(self, a, b=None, **kw):
        self.calls.append(("integers", a, b))
        if b is None:
            v = self._one[self._i % len(self._one)]
            self._i += 1
            return int(v)
        return int(self._two)

    def uniform(self, a, b):
        self.calls.append(("uniform", a, b))
        return float(self._uniform(a, b))

    def normal(self, mu, sigma, size=None):
        if size is None:
            return float(self._normal)
        import numpy as np
        return np.full(size, self._normal, dtype=np.float64)


def _script_numpy_rng(monkeypatch, **kw) -> _ScriptedRng:
    """Install one _ScriptedRng for every default_rng() the benchmark asks for."""
    import numpy as np
    rng = _ScriptedRng(**kw)
    monkeypatch.setattr(np.random, "default_rng", lambda *_a, **_k: rng)
    return rng


# =====================================================================
# KNOWN ANSWER — B1  plane_reproj_rmse_px
# =====================================================================

class _StubAruco:
    """Detector that hands back exactly the quads a test names."""

    def __init__(self, quads):
        self._det = self
        self._quads = quads

    def detectMarkers(self, frame):
        import numpy as np
        if self._quads is None:
            return (), None, ()
        ids = sorted(self._quads)
        return ([self._quads[i].reshape(1, 4, 2).astype(np.float64) for i in ids],
                np.array([[i] for i in ids], np.int32), ())


def test_expected_corners_are_the_printed_marker_square_in_buffer_pixels():
    """Known answer straight off the printed mat: each held-out quad is the
    30 mm ArUco square around its own centre, in TL, TR, BR, BL order."""
    import numpy as np
    from gawaah.takhti import (MARKER_IDS, MARKER_MM, PX_PER_MM_X, PX_PER_MM_Y,
                               marker_centres_mm, mm_to_buffer)

    got = bench._expected_corner_buffer_px()
    assert sorted(got) == sorted(MARKER_IDS)
    for i, (cx, cy) in zip(MARKER_IDS, marker_centres_mm()):
        q = np.asarray(got[i], dtype=np.float64)
        assert q.shape == (4, 2)
        centre = mm_to_buffer(np.array([[cx, cy]], np.float64))[0]
        assert q.mean(axis=0) == pytest.approx(centre, abs=1e-9)
        tl, tr, br, bl = q
        assert tl[0] < tr[0] and tl[1] == pytest.approx(tr[1])   # TL then TR
        assert br[0] == pytest.approx(tr[0]) and br[1] > tr[1]   # then BR
        assert bl[0] == pytest.approx(tl[0]) and bl[1] > tl[1]   # then BL
        assert (tr[0] - tl[0]) / PX_PER_MM_X == pytest.approx(MARKER_MM, abs=1e-9)
        assert (bl[1] - tl[1]) / PX_PER_MM_Y == pytest.approx(MARKER_MM, abs=1e-9)


def test_holdout_rmse_is_the_root_mean_square_of_a_known_corner_error():
    """Known answer: two markers, one landing exactly on its printed corners and
    one displaced by (3, 4) px on all four. Eight residuals of 0 px^2 and eight
    of 25 px^2, so the RMSE is sqrt(100/8) = sqrt(12.5) = 3.5355 px."""
    import numpy as np
    want = bench._expected_corner_buffer_px()
    a, b_ = sorted(want)[:2]
    eng = _StubAruco({a: np.asarray(want[a], np.float64).copy(),
                      b_: np.asarray(want[b_], np.float64) + np.array([3.0, 4.0])})
    got = bench._holdout_rmse_px(eng, np.zeros((8, 8), np.uint8),
                                 np.eye(3, dtype=np.float64))
    assert got == pytest.approx(math.sqrt(12.5), abs=1e-9)


def test_holdout_rmse_is_zero_when_every_corner_lands_on_its_printed_place():
    import numpy as np
    want = bench._expected_corner_buffer_px()
    eng = _StubAruco({i: np.asarray(q, np.float64).copy() for i, q in want.items()})
    got = bench._holdout_rmse_px(eng, np.zeros((8, 8), np.uint8),
                                 np.eye(3, dtype=np.float64))
    assert got == pytest.approx(0.0, abs=1e-12)


def test_holdout_rmse_abstains_when_there_is_nothing_held_out():
    import numpy as np
    frame, H = np.zeros((8, 8), np.uint8), np.eye(3, dtype=np.float64)
    assert bench._holdout_rmse_px(_StubAruco(None), frame, H) is None
    assert bench._holdout_rmse_px(_StubAruco({}), frame, H) is None
    stranger = np.zeros((4, 2), np.float64)
    assert bench._holdout_rmse_px(_StubAruco({97: stranger}), frame, H) is None


class _StubLock:
    def __init__(self, locked, H, persp, scale_err, fit):
        self.locked = locked
        self.H = H
        self.persp_index = persp
        self.scale_err = scale_err
        self.reproj_rmse_px = fit
        self.reason = "stub refusal"


def test_plane_bench_aggregates_a_scripted_sweep_into_known_numbers(monkeypatch):
    """Known answer for the WHOLE B1 body.

    18 frames (2 seeds x 9 tilts). Frames 2 and 11 refuse to lock; the other 16
    return a held-out RMSE of 0.10, 0.11, 0.12 ... in order. Every published
    figure is then plain arithmetic over that list, done here by hand.
    """
    import numpy as np
    import gawaah.takhti as takhti
    from gawaah.takhti import PX_PER_MM

    n_frames = 18
    refuse = {2, 11}
    rmse_of = {k: 0.10 + 0.01 * k for k in range(n_frames)}
    persp_of = {k: 0.001 * k for k in range(n_frames)}
    scale_of = {k: 0.002 * k for k in range(n_frames)}
    fit_of = {k: 1e-5 * k for k in range(n_frames)}
    BASELINE = 0.05

    rendered: list[dict] = []
    # _synth_frame is called once for the baseline and then once per sweep
    # frame, and it bumps the counter, so -2 lands the baseline on -1.
    state = {"frame": -2}

    def fake_synth(px_per_mm, tilt, size, noise, seed, fit):
        rendered.append({"tilt": tuple(tilt), "sigma": noise, "seed": seed})
        state["frame"] += 1
        return np.zeros((4, 4), np.uint8)

    class FakeEngine:
        def __init__(self):
            self._det = object()

        def detect(self, frame):
            k = state["frame"]
            if k < 0:
                return _StubLock(True, np.eye(3), 0.0, 0.0, 0.0)
            if k in refuse:
                return _StubLock(False, None, 0.0, 0.0, 0.0)
            return _StubLock(True, np.eye(3), persp_of[k], scale_of[k], fit_of[k])

    def fake_holdout(eng, frame, H):
        k = state["frame"]
        return BASELINE if k < 0 else rmse_of[k]

    monkeypatch.setattr(bench, "_synth_frame", fake_synth)
    monkeypatch.setattr(bench, "_holdout_rmse_px", fake_holdout)
    monkeypatch.setattr(takhti, "PlaneEngine", FakeEngine)

    out = bench.bench_plane_reproj([0, 1], 1.0)

    seed0 = [rmse_of[k] for k in range(0, 9) if k not in refuse]
    seed1 = [rmse_of[k] for k in range(9, 18) if k not in refuse]
    assert out.per_seed[0] == pytest.approx(seed0)
    assert out.per_seed[1] == pytest.approx(seed1)
    assert len(seed0) == 8 and len(seed1) == 8

    d = out.detail
    assert d["frames_attempted"] == 18
    assert d["frames_locked"] == 16
    assert d["lock_rate"] == pytest.approx(16 / 18)
    assert len(d["refusals"]) == 2
    assert d["worst_err_mm"] == pytest.approx(max(seed0 + seed1) / PX_PER_MM)
    assert d["baseline_untilted_noiseless_px"] == pytest.approx(BASELINE)
    kept = [k for k in range(n_frames) if k not in refuse]
    assert d["max_persp_index"] == pytest.approx(max(persp_of[k] for k in kept))
    assert d["max_scale_err"] == pytest.approx(max(scale_of[k] for k in kept))
    assert d["fit_residual_worst_px"] == pytest.approx(max(fit_of[k] for k in kept))
    assert d["held_out_points_per_frame"] == 16

    # 1 baseline render + 18 sweep renders, and the noise ladder is walked, not
    # parked on its first rung.
    assert len(rendered) == 19
    assert rendered[0]["sigma"] == 0.0
    for s in (0, 1):
        block = rendered[1 + 9 * s: 1 + 9 * (s + 1)]
        assert [f["sigma"] for f in block] == [
            bench._NOISE_LEVELS[i % len(bench._NOISE_LEVELS)] for i in range(9)]
        assert [f["tilt"] for f in block][:5] == list(bench._FIXED_TILTS)
        assert all(f["seed"] == s * 1000 + i for i, f in enumerate(block))


def test_plane_bench_reports_not_measured_when_every_frame_refuses(monkeypatch):
    import numpy as np
    import gawaah.takhti as takhti

    class AlwaysRefuses:
        def __init__(self):
            self._det = object()

        def detect(self, frame):
            return _StubLock(False, None, 0.0, 0.0, 0.0)

    monkeypatch.setattr(bench, "_synth_frame",
                        lambda *a, **k: np.zeros((4, 4), np.uint8))
    monkeypatch.setattr(takhti, "PlaneEngine", AlwaysRefuses)
    monkeypatch.setattr(bench, "BENCHES",
                        (bench.BENCH_BY_NAME["plane_reproj_rmse_px"],))

    m = bench.run_benchmarks([0, 1], scale=0.1)
    blk = m["benchmarks"]["plane_reproj_rmse_px"]
    assert blk["status"] == bench.STATUS_NOT_MEASURED
    assert blk["detail"]["frames_locked"] == 0
    assert blk["detail"]["lock_rate"] == 0.0


def test_the_plane_measurement_is_a_real_measurement_on_a_real_render():
    """Not a known answer — an anchor. The held-out corner error on an untilted
    noiseless render must be small but NOT zero: zero would mean the corners
    were in the fit after all, which is the degeneracy this benchmark was built
    to route around."""
    from gawaah.takhti import PlaneEngine
    eng = PlaneEngine()
    frame = bench._synth_frame(4.0, (0.0, 0.0), (960, 1280), 0.0, 0, 0.82)
    lock = eng.detect(frame)
    assert lock.locked and lock.H is not None
    rmse = bench._holdout_rmse_px(eng, frame, lock.H)
    assert rmse is not None
    assert 1e-4 < rmse < 3.0, rmse


# =====================================================================
# KNOWN ANSWER — B2  placement_footprint_err_mm
# =====================================================================

def test_paste_puts_exactly_the_stated_number_of_square_millimetres_down():
    """Known answer: the stimulus is the ground truth, so it has to BE the
    ground truth. Coverage-composited area, converted back to mm^2, must equal
    the size the caller asked for."""
    import numpy as np
    from gawaah.takhti import BUF_H, BUF_W, PX_PER_MM_X, PX_PER_MM_Y

    ref = np.full((BUF_H, BUF_W), 200, np.uint8)
    for (L, S), deg in (((210.0, 30.0), 0.0), ((120.0, 80.0), 37.0),
                        ((60.0, 40.0), 90.0)):
        out = bench._paste(ref, 148.5, 210.0, L, S, deg, 55)
        cov = (ref.astype(np.float64) - out.astype(np.float64)) / (200.0 - 55.0)
        area_mm2 = cov.sum() / (PX_PER_MM_X * PX_PER_MM_Y)
        assert area_mm2 == pytest.approx(L * S, rel=0.01), (L, S, deg)


def test_paste_at_zero_degrees_is_the_long_edge_across_the_mat():
    import numpy as np
    from gawaah.takhti import BUF_H, BUF_W, PX_PER_MM_X, PX_PER_MM_Y

    ref = np.full((BUF_H, BUF_W), 200, np.uint8)
    out = bench._paste(ref, 148.5, 210.0, 210.0, 30.0, 0.0, 55)
    ys, xs = np.where(out < 190)
    assert (xs.max() - xs.min() + 1) / PX_PER_MM_X == pytest.approx(210.0, abs=1.0)
    assert (ys.max() - ys.min() + 1) / PX_PER_MM_Y == pytest.approx(30.0, abs=1.0)


def _placement(long_mm, short_mm, centre, angle, *, measurable=True, reason="OK"):
    from gawaah.placement import Placement
    return Placement(
        id=1, centre_mm=centre,
        long_edge_mm=long_mm if measurable else None,
        short_edge_mm=short_mm if measurable else None,
        area_mm2=None, angle_deg=angle if measurable else None,
        stable=True, frames_seen=5, measurable=measurable, reason=reason,
    )


def test_placement_bench_turns_scripted_errors_into_known_millimetres(monkeypatch):
    """Known answer for the WHOLE B2 body.

    Every object is a 210 x 30 mm packet at 179 deg (the rng is scripted, so
    that is exact). The detector is scripted too, in a five-way cycle:

        k % 5 == 0  nothing detected                 -> a MISS
        k % 5 == 1  detected, not measurable         -> a MISS and unmeasurable
        k % 5 == 2  measurable but TOUCHES_BORDER    -> unmeasurable
        k % 5 == 3  long +0.3 mm, short -0.2 mm      -> footprint error 0.3
        k % 5 == 4  long -0.1 mm, short +0.4 mm      -> footprint error 0.4

    with the centre always 0.6 mm east and 0.8 mm south of truth (a 3-4-5
    triangle, so the centre error is exactly 1.0 mm) and the reported bearing
    always 1.5 deg round the 180 deg wrap from truth.
    """
    import numpy as np
    import gawaah.placement as placement
    from gawaah.takhti import MAT_H_MM, MAT_W_MM

    _script_numpy_rng(monkeypatch, one_arg_ints=(0,), uniform=lambda a, b: 179.0)
    monkeypatch.setattr(bench, "_empty_mat", lambda: np.zeros((4, 4), np.uint8))
    monkeypatch.setattr(bench, "_noisy", lambda img, sigma, seed: img)

    truth: list[tuple] = []

    def fake_paste(ref, cx, cy, long_mm, short_mm, deg, val):
        truth.append((long_mm, short_mm, deg, cx, cy))
        return ref

    monkeypatch.setattr(bench, "_paste", fake_paste)

    class ScriptedDetector:
        def __init__(self, ref, **kw):
            pass

        def update(self, frame):
            k = len(truth) - 1
            L, S, deg, cx, cy = truth[k]
            centre = (cx + 0.6, cy + 0.8)
            angle = (deg + 1.5) % 180.0
            phase = k % 5
            if phase == 0:
                return []
            if phase == 1:
                return [_placement(None, None, centre, None, measurable=False,
                                   reason="OK")]
            if phase == 2:
                return [_placement(L, S, centre, angle, reason="TOUCHES_BORDER")]
            if phase == 3:
                return [_placement(L + 0.3, S - 0.2, centre, angle)]
            return [_placement(L - 0.1, S + 0.4, centre, angle)]

    monkeypatch.setattr(placement, "PlacementDetector", ScriptedDetector)

    out = bench.bench_placement_footprint([0, 1], 1.0)

    assert len(truth) == 20                      # _n(10, 1.0, floor=2) x 2 seeds
    assert {t[:3] for t in truth} == {(210.0, 30.0, 179.0)}
    assert {t[3:] for t in truth} == {(MAT_W_MM / 2, MAT_H_MM / 2)}

    assert out.per_seed[0] == pytest.approx([0.3, 0.4, 0.3, 0.4])
    assert out.per_seed[1] == pytest.approx([0.3, 0.4, 0.3, 0.4])

    d = out.detail
    assert d["objects_measured"] == 8
    assert d["objects_missed"] == 8               # phases 0 and 1
    assert d["objects_unmeasurable"] == 8         # phases 1 and 2
    assert d["long_edge_worst_mm"] == pytest.approx(0.3)
    assert d["long_edge_mean_mm"] == pytest.approx(0.2)
    assert d["short_edge_worst_mm"] == pytest.approx(0.4)
    assert d["short_edge_mean_mm"] == pytest.approx(0.3)
    assert d["centre_worst_mm"] == pytest.approx(1.0)
    assert d["angle_worst_deg"] == pytest.approx(1.5), (
        "179 deg and 0.5 deg are 1.5 deg apart on a rectangle, not 178.5"
    )
    assert d["sizes_mm"] == [list(s) for s in bench._SIZES]


def test_placement_bench_reports_not_measured_when_nothing_is_measurable(monkeypatch):
    import numpy as np
    import gawaah.placement as placement

    monkeypatch.setattr(bench, "_empty_mat", lambda: np.zeros((4, 4), np.uint8))
    monkeypatch.setattr(bench, "_noisy", lambda img, sigma, seed: img)
    monkeypatch.setattr(bench, "_paste", lambda ref, *a: ref)

    class NeverDetects:
        def __init__(self, ref, **kw):
            pass

        def update(self, frame):
            return []

    monkeypatch.setattr(placement, "PlacementDetector", NeverDetects)
    monkeypatch.setattr(bench, "BENCHES",
                        (bench.BENCH_BY_NAME["placement_footprint_err_mm"],))
    m = bench.run_benchmarks([0, 1], scale=0.1)
    blk = m["benchmarks"]["placement_footprint_err_mm"]
    assert blk["status"] == bench.STATUS_NOT_MEASURED
    assert blk["detail"]["objects_measured"] == 0
    assert blk["detail"]["objects_missed"] == 4


def test_the_placement_measurement_is_real_on_a_real_paste():
    """Anchor, not a known answer: the real detector on the real stimulus must
    land within a millimetre of the size that was pasted, or the benchmark is
    reporting the harness rather than the module."""
    from gawaah.placement import REASON_OK, PlacementDetector
    from gawaah.takhti import MAT_H_MM, MAT_W_MM

    ref = bench._empty_mat()
    det = PlacementDetector(ref)
    frame = bench._paste(ref, MAT_W_MM / 2, MAT_H_MM / 2, 120.0, 80.0, 37.0, 55)
    got = det.update(frame)
    assert len(got) == 1 and got[0].measurable and got[0].reason == REASON_OK
    assert got[0].long_edge_mm == pytest.approx(120.0, abs=1.0)
    assert got[0].short_edge_mm == pytest.approx(80.0, abs=1.0)


# =====================================================================
# KNOWN ANSWER — B3  sellevent_recall
# =====================================================================

LINE_Y = 402.0          # MAT_H_MM - 18.0, the printed exit line


def test_arange_walks_to_its_endpoint_in_both_directions():
    assert bench._arange(0.0, 10.0, 3.0) == [0.0, 3.0, 6.0, 9.0, 10.0]
    assert bench._arange(10.0, 0.0, -3.0) == [10.0, 7.0, 4.0, 1.0, 0.0]
    assert bench._arange(0.0, 0.0, 1.0) == [0.0], (
        "a zero-length walk still has to arrive"
    )


@pytest.mark.parametrize("kind,truth", [
    ("sale", 1), ("return", 1), ("hover", 0), ("browse", 0),
])
def test_event_path_ground_truth_matches_the_geometry_it_draws(kind, truth):
    """The recall denominator is this number. If the script says a hover is a
    sale, recall is measured against a lie and no downstream check can tell."""
    import numpy as np
    for seed in range(30):
        path, got = bench._event_path(kind, np.random.default_rng(seed), LINE_Y)
        ys = [y for _x, y in path]
        assert got == truth, kind
        assert (max(ys) > LINE_Y) is bool(truth), (
            f"{kind}: ground truth {got} but the drawn path "
            f"{'does' if max(ys) > LINE_Y else 'does not'} cross the line"
        )
        assert ys[0] < LINE_Y, "every object starts on the shopkeeper's side"
        if kind == "return":
            assert ys[-1] < LINE_Y, "a return comes back"
        if kind == "hover":
            assert max(ys) < LINE_Y - 2.0, "a hover stops short of the line"
        if kind == "browse":
            assert len(set(ys)) == 1, "a browse drifts parallel to the line"


def _run_sellevent(monkeypatch, *, kind_idx, drop, drop_at, seeds=(0, 1),
                   scale=0.25):
    _script_numpy_rng(monkeypatch, one_arg_ints=(kind_idx, 0 if drop else 1),
                      two_arg_int=drop_at)
    return bench.bench_sellevent_recall(list(seeds), scale)


def test_sellevent_bench_scores_a_clean_scripted_sale_at_recall_one(monkeypatch):
    """Known answer. With the draws scripted the path is exactly

        y = 337, 346, ... 436, 437   (line at 402)

    so the object spends 8 frames inside and 5 frames past the line. The zone
    needs 3 consecutive frames on the far side, so it commits exactly one OUT
    crossing per event, and recall is 1.0 for every seed.
    """
    out = _run_sellevent(monkeypatch, kind_idx=0, drop=False, drop_at=1)
    assert out.per_seed == {0: [1.0], 1: [1.0]}
    d = out.detail
    assert d["events"] == 12 and d["events_per_seed"] == 6
    assert d["true_out_crossings"] == 12
    assert d["counted_out_crossings"] == 12
    assert d["sales_counted"] == 12
    assert d["recall_pooled"] == 1.0
    assert d["recall_no_dropout"] == 1.0
    assert d["recall_with_long_dropout"] is None
    assert d["false_positive_crossings"] == 0
    assert d["amber_events"] == 0
    assert d["misses_that_raised"] == 0
    assert d["misses_that_were_silent"] == 0
    assert d["entries_from_out"] == 0
    assert d["vanished_same_side"] == 12
    assert d["exception_codes"] == {}


def test_sellevent_bench_scores_a_sale_lost_across_the_line_as_a_miss(monkeypatch):
    """Known answer. Same 13-frame path, but the 5-frame dropout is placed at
    index 8 — which is exactly the first frame past the line — so the object is
    never once observed on the customer's side. No crossing can be committed,
    the tracker's own view is that it lived and died on the shopkeeper's side,
    so the miss is SILENT. Recall is 0.0 and every miss lands in the silent
    column, which is the finding the report publishes.
    """
    out = _run_sellevent(monkeypatch, kind_idx=0, drop=True, drop_at=8)
    assert out.per_seed == {0: [0.0], 1: [0.0]}
    d = out.detail
    assert d["true_out_crossings"] == 12
    assert d["counted_out_crossings"] == 0
    assert d["sales_counted"] == 0
    assert d["recall_pooled"] == 0.0
    assert d["recall_with_long_dropout"] == 0.0
    assert d["recall_no_dropout"] is None
    assert d["misses_that_were_silent"] == 12
    assert d["misses_that_raised"] == 0
    assert d["false_positive_crossings"] == 0, (
        "a miss is not a false positive; counting it as one would flatter "
        "recall and inflate the false-positive column at the same time"
    )


def test_sellevent_bench_reports_not_measured_when_nothing_was_a_sale(monkeypatch):
    """Known answer: an all-hover run stages no sales at all. Recall over zero
    sales is not 1.0 and it is not 0.0 — it does not exist."""
    out = _run_sellevent(monkeypatch, kind_idx=4, drop=False, drop_at=1)
    assert out.per_seed == {0: [], 1: []}
    d = out.detail
    assert d["events"] == 12
    assert d["true_out_crossings"] == 0
    assert d["counted_out_crossings"] == 0
    assert d["recall_pooled"] is None
    assert d["recall_no_dropout"] is None
    assert d["false_positive_crossings"] == 0

    monkeypatch.setattr(bench, "BENCHES",
                        (bench.BENCH_BY_NAME["sellevent_recall"],))
    m = bench.run_benchmarks([0, 1], scale=0.25)
    assert m["benchmarks"]["sellevent_recall"]["status"] == bench.STATUS_NOT_MEASURED


def test_sellevent_bench_counts_a_crossing_the_script_did_not_stage(monkeypatch):
    """Known answer for the false-positive column. Half the scripted events
    cross the line while claiming a ground truth of zero, so every one of them
    is one uninvited OUT crossing. Recall stays 1.0 — the sales that WERE staged
    were all counted — and the extra crossings show up where they belong
    instead of being absorbed into the recall numerator.
    """
    calls = {"n": 0}
    ys = [372.0, 377.0, 382.0, 387.0, 392.0, 397.0,
          407.0, 412.0, 417.0, 422.0, 427.0, 432.0]

    def crossing_path(kind, rng, line_y):
        calls["n"] += 1
        return [(148.5, y) for y in ys], calls["n"] % 2

    monkeypatch.setattr(bench, "_event_path", crossing_path)
    out = _run_sellevent(monkeypatch, kind_idx=0, drop=False, drop_at=1)

    d = out.detail
    assert d["events"] == 12
    assert d["true_out_crossings"] == 6
    assert d["counted_out_crossings"] == 12
    assert d["sales_counted"] == 6
    assert d["false_positive_crossings"] == 6
    assert d["recall_pooled"] == 1.0, (
        "recall must count matched sales, not raw crossings"
    )
    assert out.per_seed == {0: [1.0], 1: [1.0]}


# =====================================================================
# KNOWN ANSWER — B4  kernel_exactly_once
# =====================================================================

class _FakeIntent:
    def __init__(self, nonce):
        self.nonce = nonce


class _LeakyKernel:
    """A kernel with no idempotency at all: one intent per caller, and every
    caller is allowed to charge. Exactly the failure B4 exists to detect."""

    def __init__(self, db, clock, ledger):
        self._lock = threading.Lock()
        self._n = 0

    def create_intent(self, session_id, amount_paise, cycle=0):
        with self._lock:
            self._n += 1
            return _FakeIntent(f"leak-{self._n}")

    def mark_calling(self, nonce):
        return _FakeIntent(nonce)

    def count(self):
        return self._n


def test_kernel_bench_scores_a_correct_kernel_at_exactly_one():
    """Known answer against the real kernel: 4 racing threads, 1 round per
    seed, and the only correct outcome is one intent and one caller — so every
    sample is 1.0, both duplicate counters are 0, and the round writes exactly
    two audit lines (intent.created, intent.calling)."""
    out = bench.bench_kernel_exactly_once([0, 1], 0.1)
    d = out.detail
    assert d["threads_per_round"] == 4          # _n(24, 0.1, floor=4)
    assert d["rounds_per_seed"] == 1            # _n(2, 0.1, floor=1)
    assert d["rounds_total"] == 2
    assert out.per_seed == {0: [1.0], 1: [1.0]}
    assert d["duplicate_intents"] == 0
    assert d["extra_callers_admitted"] == 0
    assert d["ledger_chains_broken"] == 0
    assert d["audit_lines_written"] == 2 * d["rounds_total"]


def test_kernel_bench_scores_a_kernel_with_no_idempotency_at_zero(monkeypatch):
    """Known answer: 4 threads against a kernel that mints an intent each and
    refuses nobody. That is 4 intents where there should be 1 (3 duplicates) and
    4 callers admitted where there should be 1 (3 extra), and the round scores
    0.0. A benchmark that cannot see this is not measuring exactly-once."""
    import gawaah.kernel as kmod
    monkeypatch.setattr(kmod, "Kernel", _LeakyKernel)

    out = bench.bench_kernel_exactly_once([0], 0.1)
    assert out.per_seed == {0: [0.0]}
    assert out.detail["duplicate_intents"] == 3
    assert out.detail["extra_callers_admitted"] == 3
    assert out.detail["audit_lines_written"] == 0


def test_kernel_bench_notices_a_kernel_whose_intent_table_is_wrong(monkeypatch):
    """The source comment says this bit once compared a BOUND METHOD to an int
    and was silently False forever. Pin it: a kernel that races correctly but
    leaves the wrong number of rows behind must not score 1.0."""
    import gawaah.kernel as kmod
    real = kmod.Kernel

    class MiscountingKernel(real):
        def count(self):
            return super().count() + 1

    monkeypatch.setattr(kmod, "Kernel", MiscountingKernel)
    out = bench.bench_kernel_exactly_once([0], 0.1)
    assert out.per_seed == {0: [0.0]}
    assert out.detail["duplicate_intents"] == 0
    assert out.detail["extra_callers_admitted"] == 0


def test_kernel_bench_notices_a_broken_audit_chain(monkeypatch):
    import gawaah.ledger as lmod
    monkeypatch.setattr(lmod, "verify", lambda p: (False, 7, "x", "chain break"))
    out = bench.bench_kernel_exactly_once([0, 1], 0.1)
    assert out.detail["ledger_chains_broken"] == 2
    assert out.detail["audit_lines_written"] == 14


# =====================================================================
# KNOWN ANSWER — B5  ledger_verify_throughput
# =====================================================================

class _FakeClockModule:
    """perf_counter that advances by a fixed step per call, so lines-per-second
    stops being a wall-clock reading and becomes arithmetic a test can check."""

    def __init__(self, step):
        self.t = 0.0
        self.step = float(step)

    def perf_counter(self):
        v = self.t
        self.t += self.step
        return v


def test_ledger_bench_rate_is_lines_over_the_seconds_it_measured(monkeypatch):
    """Known answer: 400 lines per chain, every verify apparently taking exactly
    2.0 s, therefore 200.0 lines/s — twice, for each of two chains."""
    monkeypatch.setattr(bench, "time", _FakeClockModule(2.0))
    out = bench.bench_ledger_verify([0, 1], 0.1)
    d = out.detail
    assert d["lines_per_chain"] == 400          # _n(4000, 0.1, floor=200)
    assert d["repeats_per_chain"] == 2          # _n(3, 0.1, floor=2)
    assert d["chains"] == 2
    assert d["all_chains_verified"] is True
    assert out.per_seed == {0: [200.0, 200.0], 1: [200.0, 200.0]}


def test_ledger_bench_rate_tracks_the_measured_time(monkeypatch):
    """Halve the apparent time and the reported rate must double."""
    monkeypatch.setattr(bench, "time", _FakeClockModule(1.0))
    out = bench.bench_ledger_verify([0], 0.1)
    assert out.per_seed == {0: [400.0, 400.0]}


def test_ledger_bench_refuses_to_call_a_failed_chain_verified(monkeypatch):
    import gawaah.ledger as lmod
    monkeypatch.setattr(lmod, "verify", lambda p: (False, 400, "x", "chain break"))
    out = bench.bench_ledger_verify([0], 0.1)
    assert out.detail["all_chains_verified"] is False


def test_ledger_bench_notices_a_chain_of_the_wrong_length(monkeypatch):
    import gawaah.ledger as lmod
    monkeypatch.setattr(lmod, "verify", lambda p: (True, 399, "x", None))
    out = bench.bench_ledger_verify([0], 0.1)
    assert out.detail["all_chains_verified"] is False, (
        "a chain that verifies but is a line short has still lost a line"
    )


def test_ledger_bench_really_writes_and_really_verifies_the_lines_it_claims():
    """Anchor with the real clock: the chain length is deterministic even though
    the rate is not, and the rate must at least be finite and positive."""
    out = bench.bench_ledger_verify([0], 0.1)
    assert out.detail["lines_per_chain"] == 400
    assert out.detail["all_chains_verified"] is True
    assert len(out.per_seed[0]) == 2
    for v in out.per_seed[0]:
        assert 0.0 < v < float("inf")
    assert out.nondeterministic["bytes_written"] > 0


# =====================================================================
# The guard: no benchmark publishes numbers without a known-answer test.
# =====================================================================

KNOWN_ANSWER_TESTS: dict[str, tuple[str, ...]] = {
    "plane_reproj_rmse_px": (
        "test_expected_corners_are_the_printed_marker_square_in_buffer_pixels",
        "test_holdout_rmse_is_the_root_mean_square_of_a_known_corner_error",
        "test_plane_bench_aggregates_a_scripted_sweep_into_known_numbers",
    ),
    "placement_footprint_err_mm": (
        "test_paste_puts_exactly_the_stated_number_of_square_millimetres_down",
        "test_placement_bench_turns_scripted_errors_into_known_millimetres",
    ),
    "sellevent_recall": (
        "test_event_path_ground_truth_matches_the_geometry_it_draws",
        "test_sellevent_bench_scores_a_clean_scripted_sale_at_recall_one",
        "test_sellevent_bench_scores_a_sale_lost_across_the_line_as_a_miss",
        "test_sellevent_bench_counts_a_crossing_the_script_did_not_stage",
    ),
    "kernel_exactly_once": (
        "test_kernel_bench_scores_a_correct_kernel_at_exactly_one",
        "test_kernel_bench_scores_a_kernel_with_no_idempotency_at_zero",
    ),
    "ledger_verify_throughput": (
        "test_ledger_bench_rate_is_lines_over_the_seconds_it_measured",
        "test_ledger_bench_refuses_to_call_a_failed_chain_verified",
    ),
}


def test_every_benchmark_has_a_known_answer_test():
    """A benchmark whose number nobody can derive on paper is a number the
    README should not print. Adding a sixth benchmark fails here until it has
    a case with a worked answer, which is the loud version of saying so."""
    have = set(globals())
    for b in bench.BENCHES:
        names = KNOWN_ANSWER_TESTS.get(b.name)
        assert names, (
            f"{b.name} publishes a number with no known-answer test. Either "
            f"write one or take the benchmark out of the report."
        )
        for n in names:
            assert n in have, f"{b.name}: named known-answer test {n} is missing"


def test_the_known_answer_register_does_not_name_benchmarks_that_left():
    assert set(KNOWN_ANSWER_TESTS) == {b.name for b in bench.BENCHES}
