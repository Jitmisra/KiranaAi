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
"""
from __future__ import annotations

import importlib
import json
import re
import sys
import textwrap
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
                               bench.STATUS_ERROR, bench.STATUS_SKIPPED), name
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
