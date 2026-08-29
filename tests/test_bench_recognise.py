"""Tests for tools/bench_recognise.py — the bench that measures recognition.

A bench is a measuring instrument, and an instrument that flatters is worse than
no instrument, because the numbers it produces get quoted. So these tests are
mostly not about "does it run": they are about the ways a recognition benchmark
can quietly lie, and they try to make each one impossible.

The lies this file is built to catch:

  1. TRAIN ON TEST. The enrolment views leaking into the evaluation views is the
     oldest way to publish a good number. `disjointness()` is fed deliberately
     leaky data and must say so, and `run()` must REFUSE to publish rather than
     warn.
  2. DROP THE HARD ROWS. An item the segmenter would not measure, or a product
     the collision guard refused, is exactly the row a flattering bench loses.
     Both must stay in the denominator, and the arithmetic is checked against a
     hand-counted expectation.
  3. SCORE AN UNTAUGHT ITEM AS A WIN. Naming a packet that is not in the gallery
     is a false price, never a correct answer and never merely an abstention.
  4. REPORT ACCURACY WITHOUT ITS PRICE. Accuracy is meaningless without the
     abstention rate beside it, so `Metrics` must always carry all three.
  5. WIDEN THE GATES. The bench must run at `gawaah.identity`'s defaults, and
     its own gate audit must actually detect a widened gate when one exists —
     tested by giving it a tree that contains one.
  6. A PRODUCT SET THAT CANNOT FAIL. Twelve products of twelve different sizes
     measure the tape measure. The set is checked for the hard pairs the brief
     asks for, and for footprint families that force appearance to decide.

Nothing here settles money, and a test asserts that the bench cannot either.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.bench_recognise as B  # noqa: E402
from gawaah.identity import (  # noqa: E402
    DEFAULT_PHI,
    DEFAULT_TAU_MM,
    DEFAULT_THETA,
    REASON_BELOW_MARGIN,
    REASON_BELOW_SIMILARITY,
)


# --------------------------------------------------------------------- helpers

def _out(sku, bucket, predicted, *, view="V1", reason="match",
         top1=0.9, top2=0.5, margin=0.4):
    """One Outcome, judged by the bench's own rule so the tests exercise the
    judgement under test rather than a copy of it."""
    return B.Outcome(
        sku_id=sku, view_id=view, bucket=bucket, predicted=predicted,
        price_paise=100 if predicted else None,
        reason=reason if predicted else REASON_BELOW_SIMILARITY,
        top1=top1, top2=top2, margin=margin, top1_sku=predicted,
        n_candidates=2, latency_ms=1.0,
        verdict=B._judge(sku, bucket, predicted),
    )


ENROLLED = B.BUCKET_ENROLLED
REFUSED = B.BUCKET_REFUSED
NEVER = B.BUCKET_NEVER_TAUGHT


# ============================================================== the product set

def test_the_set_has_at_least_twelve_products_and_real_intruders():
    taught = [p for p in B.PRODUCTS if p.taught]
    untaught = [p for p in B.PRODUCTS if not p.taught]
    assert len(taught) >= 12, f"only {len(taught)} taught products"
    assert len(untaught) >= 1, "an open set of nothing is not an open set"
    assert len({p.sku_id for p in B.PRODUCTS}) == len(B.PRODUCTS)


def test_footprints_cluster_so_the_tape_measure_cannot_answer_alone():
    """If every product were a different size the footprint gate would decide
    every query on its own and the descriptor would never be tested."""
    fams: dict[float, list[str]] = {}
    for p in B.PRODUCTS:
        fams.setdefault(p.long_edge_mm, []).append(p.sku_id)
    big = max(fams.values(), key=len)
    assert len(big) >= 5, f"largest footprint family is only {len(big)}: {big}"
    # and the family members are inside tau of each other by construction
    n_in_family = sum(len(v) for v in fams.values() if len(v) > 1)
    assert n_in_family >= 12, (
        f"only {n_in_family} products share a footprint with anything; the "
        "metric tiebreak would answer nearly every query for free")


def test_the_brief_s_three_hard_pair_kinds_are_all_present():
    by = {p.sku_id: p for p in B.PRODUCTS}
    same_size_diff_colour = []
    same_colour_diff_layout = []
    must_collide = []
    for a, b, expect, _why in B.HARD_PAIRS:
        pa, pb = by[a], by[b]
        if expect == B.MUST_COLLIDE:
            must_collide.append((a, b))
            continue
        same_size = pa.long_edge_mm == pb.long_edge_mm
        same_col = (pa.body, pa.accent) == (pb.body, pb.accent)
        if same_size and not same_col and pa.layout == pb.layout:
            same_size_diff_colour.append((a, b))
        if same_col and pa.layout != pb.layout:
            same_colour_diff_layout.append((a, b))
    assert same_size_diff_colour, "no same-size, different-colour pair"
    assert same_colour_diff_layout, "no same-colour, different-layout pair"
    assert len(must_collide) == 1, "exactly one pair should be designed to collide"


def test_the_must_collide_pair_really_is_a_half_turn_of_the_same_packet():
    """The claim in the report is geometric, not statistical, so it is checked
    geometrically: rotate one 180 degrees and it IS the other."""
    a, b, expect, _ = next(hp for hp in B.HARD_PAIRS if hp[2] == B.MUST_COLLIDE)
    assert expect == B.MUST_COLLIDE
    pa, pb = B.PRODUCT_BY_ID[a], B.PRODUCT_BY_ID[b]
    assert (pa.w_mm, pa.h_mm) == (pb.w_mm, pb.h_mm)
    assert (pa.body, pa.accent) == (pb.body, pb.accent)
    ia = B.render_product(pa)
    ib = B.render_product(pb)
    assert np.array_equal(np.rot90(ia, 2), ib), (
        "the must-collide pair is not actually a 180-degree rotation, so the "
        "report's explanation of why it cannot be separated is wrong")


def test_every_layout_renders_and_no_two_layouts_render_the_same_thing():
    layouts = sorted({p.layout for p in B.PRODUCTS})
    seen: dict[bytes, str] = {}
    for lay in layouts:
        p = B.BenchProduct("x", "x", 60.0, 95.0, 100, (10, 200, 240),
                           (200, 30, 30), lay)
        img = B.render_product(p)
        assert img.shape == (380, 240, 3)
        key = img.tobytes()
        assert key not in seen, f"{lay} renders identically to {seen[key]}"
        seen[key] = lay
    assert len(layouts) >= 6


def test_an_unknown_layout_is_refused_not_silently_drawn_as_a_blank():
    p = B.BenchProduct("x", "x", 60.0, 95.0, 100, (1, 2, 3), (4, 5, 6),
                       "not_a_layout")
    with pytest.raises(B.BenchError):
        B.render_product(p)


def test_prices_are_integer_paise():
    for p in B.PRODUCTS:
        assert isinstance(p.price_paise, int)
        assert not isinstance(p.price_paise, bool)
        if p.taught:
            assert p.price_paise > 0


# ================================================================ disjointness

def test_the_eval_views_differ_from_the_enrolment_view_in_every_axis():
    e = B.ENROL_VIEW
    assert (e.rot_deg, e.dx_mm, e.dy_mm, e.gain_pct, e.crop_pct) == \
        (0.0, 0.0, 0.0, 100, 0)
    for v in B.EVAL_VIEWS:
        assert v.rot_deg != e.rot_deg, v.view_id
        assert (v.dx_mm, v.dy_mm) != (e.dx_mm, e.dy_mm), v.view_id
        assert v.noise_seed != e.noise_seed, v.view_id
        assert v.key() != e.key(), v.view_id
    assert len({v.view_id for v in B.EVAL_VIEWS}) == len(B.EVAL_VIEWS)
    assert len({v.key() for v in B.EVAL_VIEWS}) == len(B.EVAL_VIEWS)


def test_the_seed_ranges_are_disjoint_by_construction():
    assert 1000 <= B.ENROL_VIEW.noise_seed < 2000
    for v in B.EVAL_VIEWS:
        assert 2000 <= v.noise_seed < 3000, v.view_id


def _cap(sku, view, sha, *, enrol):
    z = np.zeros((4, 4, 3), np.uint8)
    v = np.ones(4, dtype=np.float64)
    return B.Capture(sku_id=sku, view_id=view, taught=True, is_enrol=enrol,
                     long_edge_mm=95.0, short_edge_mm=60.0, angle_deg=0.0,
                     centre_mm=(1.0, 1.0), crop_colour=z, crop_grey=z[:, :, 0],
                     vec_colour=v, vec_grey=v, crop_sha=sha)


def test_disjointness_catches_a_crop_that_was_both_enrolled_and_evaluated():
    """The check that cannot be argued with, argued with."""
    clean = B.disjointness([_cap("a", "E0", "aaa", enrol=True)],
                           [_cap("a", "V1", "bbb", enrol=False)])
    assert clean["ok"] is True
    assert clean["n_shared_crop_hashes"] == 0

    leaky = B.disjointness([_cap("a", "E0", "aaa", enrol=True)],
                           [_cap("a", "V1", "aaa", enrol=False)])
    assert leaky["ok"] is False
    assert leaky["n_shared_crop_hashes"] == 1
    assert leaky["checks"][
        "no_evaluated_crop_shares_bytes_with_an_enrolled_crop"] is False


def test_run_refuses_to_publish_when_the_views_are_not_disjoint(monkeypatch):
    """Not a warning. A bench that noticed the leak and printed the number
    anyway would be worse than one that never checked."""
    monkeypatch.setattr(B, "disjointness",
                        lambda e, v: {"ok": False, "checks": {"faked": False}})
    with pytest.raises(B.BenchError, match="NOT disjoint"):
        B.run(quick=True, with_sweep=False)


def test_the_real_captures_are_genuinely_disjoint_pixel_for_pixel():
    prods = [B.PRODUCT_BY_ID["parle_glucose"], B.PRODUCT_BY_ID["clinic_sachet"]]
    e, _, _ = B.capture_all(prods, [B.ENROL_VIEW], enrol=True)
    v, _, _ = B.capture_all(prods, list(B.EVAL_VIEWS[:2]), enrol=False)
    assert e and v
    d = B.disjointness(e, v)
    assert d["ok"] is True
    assert not ({c.crop_sha for c in e} & {c.crop_sha for c in v})
    # the vectors differ too — a byte-different crop that embedded identically
    # would still be a leak in everything that matters
    for ec in e:
        for vc in v:
            if ec.sku_id == vc.sku_id:
                assert not np.array_equal(ec.vec_colour, vc.vec_colour)


# ================================================================= the metrics

def test_the_three_numbers_are_always_reported_together():
    m = B.score([_out("a", ENROLLED, "a")], "x").as_dict()
    for k in ("top1_accuracy_on_decided", "abstention_rate",
              "false_price_rate"):
        assert k in m, f"{k} missing — accuracy without its price is a lie"


def test_score_arithmetic_against_a_hand_counted_case():
    rows = [
        _out("a", ENROLLED, "a"),          # correct
        _out("a", ENROLLED, "b"),          # wrong name on a taught product
        _out("a", ENROLLED, None),         # abstained
        _out("z", NEVER, "a"),             # untaught, named -> false price
        _out("z", NEVER, None),            # untaught, refused -> correct silence
    ]
    m = B.score(rows, "hand")
    assert (m.n_items, m.n_decided) == (5, 3)
    assert (m.n_correct, m.n_false_price, m.n_abstained) == (1, 2, 2)
    assert m.accuracy_on_decided_frac == pytest.approx(1 / 3)
    assert m.abstain_frac == pytest.approx(2 / 5)
    assert m.false_price_frac == pytest.approx(2 / 5)
    assert m.false_price_of_decided_frac == pytest.approx(2 / 3)


@pytest.mark.parametrize("bucket", [REFUSED, NEVER])
def test_naming_a_product_that_is_not_in_the_gallery_is_always_a_false_price(bucket):
    """Including naming it CORRECTLY by its own true id: the counter has no
    price for it, so a name is still money out of thin air."""
    for predicted in ("a", "some_other_sku"):
        o = _out("a", bucket, predicted)
        assert o.verdict == B.FALSE_PRICE
    assert _out("a", bucket, None).verdict == B.ABSTAINED


def test_every_outcome_lands_in_exactly_one_of_the_three_verdicts():
    rows = [_out(s, b, p)
            for s in ("a", "b")
            for b in (ENROLLED, REFUSED, NEVER)
            for p in ("a", "b", None)]
    m = B.score(rows, "x")
    assert m.n_correct + m.n_false_price + m.n_abstained == m.n_items
    assert m.n_decided + m.n_abstained == m.n_items


def test_an_abstention_is_never_scored_as_a_false_price_and_vice_versa():
    for b in (ENROLLED, REFUSED, NEVER):
        assert _out("a", b, None).verdict == B.ABSTAINED
        assert not _out("a", b, None).decided
        assert _out("a", b, "zzz").decided


def test_empty_input_does_not_divide_by_zero():
    m = B.score([], "nothing")
    assert (m.n_items, m.accuracy_on_decided_frac, m.abstain_frac,
            m.false_price_frac) == (0, 0.0, 0.0, 0.0)


def test_the_bucket_follows_the_gallery_not_the_designers_intention():
    """A product the guard refused was never taught, however much the product
    table wanted it to be."""
    assert B._bucket("parle_glucose", ["parle_glucose"]) == ENROLLED
    assert B._bucket("jeera_glucose", ["parle_glucose"]) == REFUSED
    assert B._bucket("intruder_masala", ["parle_glucose"]) == NEVER
    assert B.PRODUCT_BY_ID["jeera_glucose"].taught is True
    assert B.PRODUCT_BY_ID["intruder_masala"].taught is False


# ============================================== the rows a bench likes to lose

class _StubRec:
    """A Recogniser-shaped stub. Recognition itself is tested elsewhere; what
    is tested here is the accounting around it."""

    def __init__(self, skus, answer=None):
        self._skus = tuple(skus)
        self._answer = answer

    def skus(self):
        return self._skus

    def identify(self, crop, mm):
        from gawaah.recogniser import Recognition, REASON_MATCH
        if self._answer is None:
            return Recognition(None, None, REASON_BELOW_MARGIN, 0.9, 0.85,
                               0.05, True)
        return Recognition(self._answer, 100, REASON_MATCH, 0.99, 0.1, 0.89,
                           False, top1_sku=self._answer)


def test_an_item_the_segmenter_refused_stays_in_the_denominator():
    caps = [_cap("a", "V1", "s1", enrol=False)]
    un = [B.Unmeasured("b", "V1", True, "MERGED_CONTOUR")]
    outs = B.evaluate(_StubRec(["a", "b"], answer="a"), caps, un)
    assert len(outs) == 2, "the unmeasured item vanished from the denominator"
    lost = next(o for o in outs if o.sku_id == "b")
    assert lost.verdict == B.ABSTAINED
    assert lost.predicted is None
    assert lost.reason == f"{B.UNMEASURED}:MERGED_CONTOUR"
    m = B.score(outs, "x")
    assert m.n_items == 2 and m.abstain_frac == pytest.approx(0.5)


def test_a_segmenter_refusal_is_not_quietly_relabelled_a_recognition_abstention():
    """The reason string has to survive, or the report cannot tell a descriptor
    failure from an optics failure."""
    outs = B.evaluate(_StubRec(["a"]), [], [B.Unmeasured("a", "V1", True,
                                                         "TOUCHES_BORDER")])
    assert outs[0].reason.startswith(B.UNMEASURED)
    assert "TOUCHES_BORDER" in outs[0].reason


def test_capture_all_returns_its_failures_instead_of_swallowing_them():
    prods = [B.PRODUCT_BY_ID["parle_glucose"], B.PRODUCT_BY_ID["tata_salt"]]
    caps, un, stats = B.capture_all(prods, [B.EVAL_VIEWS[0]], enrol=False)
    assert len(caps) + len(un) == len(prods)
    assert stats["unmatched_placements"] == 0
    assert set(stats) >= {"products_not_measured", "unmeasured_reasons",
                          "unmeasured_rows", "worst_measure_err_mm",
                          "crop_source"}


# ================================================================== confusion

def test_confusion_matrix_rows_account_for_every_outcome():
    rows = [_out("a", ENROLLED, "a"), _out("a", ENROLLED, "b"),
            _out("a", ENROLLED, None), _out("z", NEVER, "a")]
    truths, cols, m = B.confusion(rows)
    assert truths == ["a", "z"]
    assert cols[-1] == B.ABSTAIN_COL
    assert sum(sum(r) for r in m) == len(rows)
    assert m[truths.index("a")][cols.index("a")] == 1
    assert m[truths.index("a")][cols.index("b")] == 1
    assert m[truths.index("a")][cols.index(B.ABSTAIN_COL)] == 1
    assert m[truths.index("z")][cols.index("a")] == 1


def test_confusion_diagonal_equals_the_correct_count():
    rows = [_out("a", ENROLLED, "a") for _ in range(3)]
    rows += [_out("b", ENROLLED, "a")]
    truths, cols, m = B.confusion(rows)
    diag = sum(m[i][cols.index(t)] for i, t in enumerate(truths)
               if t in cols)
    assert diag == B.score(rows, "x").n_correct


# =================================================== the cosine distributions

def test_overlap_reports_a_real_overlap_rather_than_only_percentiles():
    same = [0.9, 0.8, 0.4]          # one bad same-product pair
    diff = [0.1, 0.2, 0.5, 0.6]     # two different-product pairs above it
    o = B.overlap(same, diff)
    assert o["same_min"] == 0.4
    assert o["diff_max"] == 0.6
    assert o["separable_by_one_threshold"] is False
    assert o["n_different_at_or_above_worst_same"] == 2
    assert o["gap_min_same_minus_max_diff"] == pytest.approx(-0.2)
    assert 0.0 <= o["roc_auc"] <= 1.0


def test_overlap_auc_is_one_when_the_distributions_are_clean():
    o = B.overlap([0.9, 0.95], [0.1, 0.2, 0.3])
    assert o["roc_auc"] == 1.0
    assert o["separable_by_one_threshold"] is True
    assert o["n_different_at_or_above_worst_same"] == 0


def test_overlap_auc_counts_ties_as_half():
    o = B.overlap([0.5], [0.5])
    assert o["roc_auc"] == pytest.approx(0.5)


def test_cosine_split_uses_the_real_gallery_and_keeps_the_open_set_queries():
    """An untaught crop scoring high against a gallery entry is exactly the
    event that produces a false price; leaving it out of the 'different'
    distribution would hide the only overlap that costs money."""
    e = [_cap("a", "E0", "1", enrol=True), _cap("z", "E0", "2", enrol=True)]
    v = [_cap("a", "V1", "3", enrol=False), _cap("z", "V1", "4", enrol=False)]
    split = B.cosine_split(e, v, ["a"])          # only 'a' is in the gallery
    assert len(split["same"]) == 1               # a-vs-a
    assert len(split["different"]) == 1          # z-vs-a, the open-set query
    assert not B.cosine_split(e, v, [])["same"]


# ====================================================== hard pairs, impostors

def test_a_pair_with_an_open_set_member_is_judged_on_being_named_at_all():
    """Never billed as its partner is not the same as never billed. An untaught
    packet sold as some THIRD product is the same failure."""
    a, b = "intruder_lookalike", "parle_glucose"
    e = [_cap(a, "E0", "1", enrol=True), _cap(b, "E0", "2", enrol=True)]
    named_third = [_out(a, NEVER, "krack_jack")]
    rows = B.hard_pair_report(e, named_third, accepted=[b], refused=[])
    row = next(r for r in rows if {r["a"], r["b"]} == {a, b})
    assert row["has_open_set_member"] is True
    assert row["n_cross_billed"] == 0, "it was never billed as its partner"
    assert row["n_open_set_named"] == 1
    assert row["separated"] is False, (
        "an untaught packet that got a name and a price is not 'separated'")

    silent = [_out(a, NEVER, None)]
    row2 = next(r for r in B.hard_pair_report(e, silent, [b], [])
                if {r["a"], r["b"]} == {a, b})
    assert row2["separated"] is True


def test_a_must_collide_pair_is_never_reported_as_separated():
    a, b, _, _ = next(hp for hp in B.HARD_PAIRS if hp[2] == B.MUST_COLLIDE)
    e = [_cap(a, "E0", "1", enrol=True), _cap(b, "E0", "2", enrol=True)]
    rows = B.hard_pair_report(e, [], accepted=[a, b], refused=[])
    row = next(r for r in rows if {r["a"], r["b"]} == {a, b})
    assert row["separated"] is False


def test_top_impostors_is_one_row_per_pair_sorted_worst_first():
    e = [_cap("a", "E0", "1", enrol=True), _cap("b", "E0", "2", enrol=True)]
    e[1].vec_colour = np.array([1.0, 0.0, 0.0, 0.0])
    v = [_cap("a", vw, vw, enrol=False) for vw in ("V1", "V2", "V3")]
    rows = B.top_impostors(e, v, ["a", "b"])
    assert [(r["query"], r["gallery"]) for r in rows] == [("a", "b")], \
        "three views of one confusion is one confusion"
    assert rows == sorted(rows, key=lambda r: -r["cosine"])
    assert rows[0]["clears_phi"] in (True, False)


# ==================================================== the gates, and who moved

def test_the_bench_runs_at_the_shipped_gates_and_says_so():
    prov = B.gate_provenance()
    assert prov["bench_ran_at"] == {"theta": DEFAULT_THETA, "phi": DEFAULT_PHI,
                                    "tau_mm": DEFAULT_TAU_MM}
    assert prov["bench_used_defaults"] is True


def test_no_production_module_constructs_a_recogniser_with_a_widened_gate():
    """The audit run for real, over the real tree."""
    prov = B.gate_provenance()
    assert prov["files_scanned"] > 20
    assert prov["clean"] is True, (
        "a non-default gate outside tests/: " + repr(
            prov["non_default_in_production_code"]))


def test_the_gate_audit_actually_detects_a_widened_gate(tmp_path):
    """An audit that cannot fail is not an audit. Give it a tree with a
    deliberately loosened phi and check it finds it, with file and line."""
    for sub in ("gawaah", "tools", "tests"):
        (tmp_path / sub).mkdir()
    (tmp_path / "gawaah" / "guilty.py").write_text(
        "from gawaah.identity import Identifier\n"
        "def go(g, e):\n"
        "    return Identifier(g, e, phi=0.20, theta=0.01)\n",
        encoding="utf-8")
    (tmp_path / "tests" / "test_ok.py").write_text(
        "from gawaah.recogniser import Recogniser\n"
        "r = Recogniser(None, None, phi=0.99)\n", encoding="utf-8")
    (tmp_path / "tools" / "innocent.py").write_text(
        "from gawaah.identity import Identifier\n"
        f"x = Identifier(None, None, phi={DEFAULT_PHI}, theta={DEFAULT_THETA})\n",
        encoding="utf-8")

    prov = B.gate_provenance(tmp_path)
    assert prov["clean"] is False
    hits = {(h["file"], h["gate"], h["value"])
            for h in prov["non_default_in_production_code"]}
    assert ("gawaah/guilty.py", "phi", 0.20) in hits
    assert ("gawaah/guilty.py", "theta", 0.01) in hits
    assert not any(h["file"].startswith("tools/innocent")
                   for h in prov["non_default_gate_call_sites"]), \
        "a call at the shipped values was reported as a widening"
    assert any(h["file"].startswith("tests/")
               for h in prov["non_default_gate_call_sites"]), \
        "test call sites should be recorded, just not counted as findings"


def test_the_gate_audit_reports_an_unparseable_module_instead_of_skipping_it(tmp_path):
    for sub in ("gawaah", "tools", "tests"):
        (tmp_path / sub).mkdir()
    (tmp_path / "tools" / "broken.py").write_text("def (\n", encoding="utf-8")
    prov = B.gate_provenance(tmp_path)
    assert prov["clean"] is False
    assert any("unparseable" in h for h in prov["non_default_in_production_code"])


def test_the_shipped_gate_constants_have_never_been_edited_in_git():
    """The strongest available answer to 'did someone widen them for the demo?'.
    Skipped, never faked, if git is not usable here."""
    hist = B.gate_provenance()["gate_line_history"]
    if not hist.get("available"):
        pytest.skip(f"git history unavailable: {hist}")
    assert hist["n_commits_touching_the_gate_lines"] == 1, (
        "the gate constants have been edited since they were introduced: "
        + repr(hist["commits"]))


def test_the_sweep_reaches_well_past_the_shipped_values_in_both_directions():
    """A sweep that only wobbles around the default cannot say whether the
    default is on the frontier."""
    assert min(B.THETA_GRID) < DEFAULT_THETA < max(B.THETA_GRID)
    assert min(B.PHI_GRID) < DEFAULT_PHI < max(B.PHI_GRID)
    assert DEFAULT_THETA in B.THETA_GRID and DEFAULT_PHI in B.PHI_GRID


def _sweep_row(theta, phi, fp, abst, acc=1.0):
    return {"theta": theta, "phi": phi,
            "is_shipped_default": (theta == DEFAULT_THETA and phi == DEFAULT_PHI),
            "n_false_price": fp, "false_price_rate": fp / 100.0,
            "enrolled_accuracy_on_decided": acc,
            "enrolled_abstention_rate": abst}


def test_the_frontier_says_so_when_the_shipped_gate_is_dominated():
    sweep = [_sweep_row(DEFAULT_THETA, DEFAULT_PHI, 10, 0.05),
             _sweep_row(0.20, 0.55, 4, 0.05)]     # fewer errors, same cost
    fr = B.sweep_frontier(sweep)
    assert fr["shipped_strictly_beaten"] is True
    assert fr["best_alternatives"][0]["theta"] == 0.20
    assert fr["shipped_is_on_the_pareto_front"] is False


def test_the_frontier_does_not_call_a_worse_trade_an_improvement():
    sweep = [_sweep_row(DEFAULT_THETA, DEFAULT_PHI, 10, 0.02),
             _sweep_row(0.20, 0.55, 4, 0.30)]     # fewer errors, far more amber
    fr = B.sweep_frontier(sweep)
    assert fr["shipped_strictly_beaten"] is False
    assert fr["shipped_is_on_the_pareto_front"] is True
    assert {(r["theta"], r["phi"]) for r in fr["pareto_front"]} == \
        {(DEFAULT_THETA, DEFAULT_PHI), (0.20, 0.55)}


def test_the_frontier_ignores_a_setting_that_bought_it_by_losing_accuracy():
    sweep = [_sweep_row(DEFAULT_THETA, DEFAULT_PHI, 10, 0.05, acc=1.0),
             _sweep_row(0.20, 0.55, 1, 0.05, acc=0.5)]
    fr = B.sweep_frontier(sweep)
    assert fr["shipped_strictly_beaten"] is False
    assert all(r["enrolled_accuracy_on_decided"] >= 1.0
               for r in fr["pareto_front"])


# ============================================================== determinism

def test_the_capture_pipeline_is_deterministic_to_the_byte():
    prods = [B.PRODUCT_BY_ID["monaco_salted"]]
    a, _, _ = B.capture_all(prods, [B.EVAL_VIEWS[0]], enrol=False)
    b, _, _ = B.capture_all(prods, [B.EVAL_VIEWS[0]], enrol=False)
    assert [c.crop_sha for c in a] == [c.crop_sha for c in b]
    for x, y in zip(a, b):
        assert x.vec_colour.tobytes() == y.vec_colour.tobytes()
        assert x.long_edge_mm == y.long_edge_mm


def test_two_full_quick_runs_agree_on_every_decision():
    """Determinism is a claim the report makes on its own front page."""
    a = B.run(quick=True, with_sweep=False)
    b = B.run(quick=True, with_sweep=False)
    assert a.headline == b.headline
    assert a.confusion_matrix == b.confusion_matrix
    assert a.cosines_colour == b.cosines_colour
    assert [o.verdict for o in a.outcomes] == [o.verdict for o in b.outcomes]


# ============================================================ the real thing

@pytest.fixture(scope="module")
def quick_run():
    return B.run(quick=True, with_sweep=False)


def test_the_quick_run_measures_the_real_pipeline(quick_run):
    r = quick_run
    assert r.ok is True
    assert r.disjoint["ok"] is True
    assert r.capture["embed_dim"] == 461
    assert r.enrolment["dim"] == 461
    assert r.capture["eval"]["crop_source"].startswith("tools.upload_app"), \
        "the bench fell back to its own crop instead of the enrol desk's"
    assert r.headline["gates"] == {"theta": DEFAULT_THETA, "phi": DEFAULT_PHI,
                                   "tau_mm": DEFAULT_TAU_MM}


def test_the_catalog_really_went_to_disk_and_came_back(quick_run):
    """The shopkeeper teaches on one process and sells on another. An
    in-memory-only bench would be measuring a path nobody uses."""
    r = quick_run
    assert r.enrolment["catalog_bytes"] > 0
    assert sorted(r.enrolment["reopened_from_disk"]) == \
        sorted(r.enrolment["accepted"])
    assert r.enrolment["accepted"], "nothing was enrolled at all"


def test_the_denominator_holds_every_captured_and_every_refused_item(quick_run):
    r = quick_run
    expected = r.capture["n_eval_crops"] + r.capture["n_eval_unmeasured"]
    assert r.headline["all"]["n_items"] == expected
    buckets = sum(r.headline[k]["n_items"]
                  for k in ("enrolled", "refused", "never_taught"))
    assert buckets == expected, "the three buckets do not partition the items"
    assert r.headline["open_set"]["n_items"] == \
        r.headline["refused"]["n_items"] + r.headline["never_taught"]["n_items"]


def test_the_headline_matches_the_confusion_matrix(quick_run):
    r = quick_run
    assert sum(sum(row) for row in r.confusion_matrix) == \
        r.headline["all"]["n_items"]
    ab = r.confusion_cols.index(B.ABSTAIN_COL)
    assert sum(row[ab] for row in r.confusion_matrix) == \
        r.headline["all"]["n_abstained"]


def test_the_must_collide_pair_is_refused_at_enrolment_not_at_the_till(quick_run):
    a, b, _, _ = next(hp for hp in B.HARD_PAIRS if hp[2] == B.MUST_COLLIDE)
    r = quick_run
    refused = {x["sku_id"] for x in r.enrolment["refused"]}
    assert b in refused or a in refused, (
        "two packets that are 180-degree rotations of each other were BOTH "
        "enrolled; the collision guard did not fire")
    row = next(x for x in r.enrolment["refused"] if x["sku_id"] in (a, b))
    assert row["similarity"] >= 1.0 - DEFAULT_THETA
    assert row["footprint_delta_mm"] <= DEFAULT_TAU_MM


def test_forcing_the_colliding_pair_makes_both_permanently_amber(quick_run):
    """The counterfactual that justifies the guard. If forcing them produced
    confident answers the guard would be costing the shopkeeper products for
    nothing."""
    fc = quick_run.forced_collision
    assert fc["ran"] is True
    assert fc["n_views"] > 0
    assert fc["n_named"] == 0, (
        "a forced 180-degree twin was confidently named — that is a wrong "
        "price on a real sale")
    assert fc["reasons"] == [REASON_BELOW_MARGIN]


def test_no_product_in_the_gallery_is_billed_as_another_one(quick_run):
    """The load-bearing claim. Stated as a property, not as a rate, so it
    cannot be satisfied by an improved average."""
    wrong = [o for o in quick_run.outcomes
             if o.enrolled and o.verdict == B.FALSE_PRICE]
    assert wrong == [], [
        (o.sku_id, o.predicted, round(o.top1, 3)) for o in wrong]


def test_every_decision_carries_a_positive_integer_price(quick_run):
    for o in quick_run.outcomes:
        if o.decided:
            assert isinstance(o.price_paise, int)
            assert not isinstance(o.price_paise, bool)
            assert o.price_paise > 0
        else:
            assert o.price_paise is None


def test_the_grey_channel_is_measured_too_because_that_is_the_live_loop(quick_run):
    gh = quick_run.grey_headline
    assert gh["enrolled"]["n_items"] > 0
    assert set(gh) >= {"all", "enrolled", "open_set", "n_enrolled",
                       "refused_at_enrolment", "enrolled_skus"}


def test_the_abstention_reasons_are_names_identity_actually_uses(quick_run):
    from gawaah.recogniser import ALL_REASONS
    for o in quick_run.outcomes:
        if o.decided:
            continue
        base = o.reason.split(":", 1)[0]
        assert base in set(ALL_REASONS) | {B.UNMEASURED}, o.reason


def test_latency_is_reported_per_item_and_is_dominated_by_the_embedder(quick_run):
    lt = quick_run.latency
    assert lt["n"] > 0
    assert 0.0 < lt["identify_median_ms"] < 500.0
    assert lt["embed_median_ms"] is not None
    assert lt["embed_share_of_identify"] > 0.5, (
        "the gallery scan is suddenly a large share of an identify — that is a "
        "scaling problem worth knowing about")


def test_the_footprint_gate_did_not_answer_the_questions_for_us(quick_run):
    """The one number that says whether the product set is honest. If the
    metric filter routinely left a shortlist of one, the descriptor was never
    asked anything and the accuracy above is the mat's."""
    sl = quick_run.headline["shortlist"]
    assert sl["n"] > 0
    assert sl["median_shortlist"] >= 2.0, (
        "the footprint filter is leaving a shortlist of one; this product set "
        "measures the tape measure, not the embedder")
    assert sl["frac_answered_by_footprint_alone"] < 0.5


def test_shortlist_stats_arithmetic():
    rows = [_out("a", ENROLLED, "a") for _ in range(3)]
    object.__setattr__(rows[0], "n_candidates", 1)
    object.__setattr__(rows[1], "n_candidates", 4)
    object.__setattr__(rows[2], "n_candidates", 4)
    s = B.shortlist_stats(rows, 9)
    assert s["n"] == 3 and s["max_shortlist"] == 4
    assert s["median_shortlist"] == 4.0
    assert s["n_answered_by_footprint_alone"] == 1
    assert s["frac_answered_by_footprint_alone"] == pytest.approx(1 / 3, abs=1e-4)
    assert B.shortlist_stats([], 9) == {"n": 0}


# =========================================== can this instrument read a zero?

def test_the_bench_runs_its_own_negative_control_every_time(quick_run):
    """The control is part of the measurement, not a test-only fixture, so the
    published report always carries proof that the instrument can read a zero."""
    nc = quick_run.negative_control
    assert nc["ran"] is True
    assert nc["n_refused_by_the_guard"] >= 1
    assert nc["n_accepted"] < len(quick_run.enrolment["accepted"]), (
        "a blind descriptor enrolled as many products as the real one; the "
        "collision guard is not doing anything")


def test_a_blind_descriptor_is_caught_by_four_numbers_but_not_by_accuracy(quick_run):
    """THE FINDING THIS CONTROL PRODUCED, pinned so it cannot be forgotten.

    A constant descriptor still reads ~100% top-1 accuracy on its gallery. That
    is not a scoring bug: the guard refuses every colliding enrolment, the
    gallery collapses to one survivor per footprint family, the footprint filter
    then leaves a shortlist of one, and naming the only candidate is trivially
    right. Accuracy alone cannot see this. Four other numbers can, and the test
    asserts each of them moves in the right direction.
    """
    nc = quick_run.negative_control
    real = quick_run.headline

    # 1. accuracy on the gallery does NOT catch it — asserted, because the
    #    moment it does, the paragraph in the report explaining why is wrong.
    assert nc["enrolled"]["top1_accuracy_on_decided"] >= 0.99

    # 2..5 the numbers that do.
    assert nc["shortlist"]["median_shortlist"] == 1.0
    assert nc["shortlist"]["frac_answered_by_footprint_alone"] > 0.9
    assert nc["roc_auc"] == pytest.approx(0.5, abs=0.01)
    assert nc["all"]["false_price_rate"] > real["all"]["false_price_rate"] + 0.2
    assert nc["n_refused_by_the_guard"] > len(quick_run.enrolment["refused"])


def test_the_blind_control_never_names_a_packet_on_appearance(quick_run):
    """Every confident answer a blind descriptor gets away with must have come
    from a shortlist of exactly one. If one is named with siblings present, the
    margin gate is not doing its job."""
    nc = quick_run.negative_control
    assert nc["shortlist"]["max_shortlist"] == 1, (
        "a blind descriptor named something with more than one candidate in "
        "the shortlist")


def test_the_report_publishes_the_negative_control(quick_run):
    md = B.render_markdown(quick_run)
    assert "## Can this instrument read a zero?" in md
    assert "BLIND descriptor" in md
    assert "no eyes" in md


# ================================================================== the report

def test_the_report_carries_every_section_the_brief_asks_for(quick_run):
    md = B.render_markdown(quick_run)
    for needed in ("## The three numbers", "abstention rate",
                   "FALSE-PRICE RATE", "## Confusion matrix",
                   "## Same-product vs different-product cosines",
                   "## Hard pairs", "## Latency",
                   "## Held out: how disjointness is guaranteed",
                   "## Gate provenance", "## Where this loses"):
        assert needed in md, f"the report is missing {needed!r}"


def test_no_table_in_the_report_shows_accuracy_without_the_abstention_rate(quick_run):
    """The rule the brief asks for, enforced per TABLE rather than per line, so
    a transposed layout cannot slip an unaccompanied accuracy figure through."""
    md = B.render_markdown(quick_run)
    tables: list[list[str]] = []
    cur: list[str] = []
    for line in md.splitlines():
        if line.startswith("|"):
            cur.append(line.lower())
        elif cur:
            tables.append(cur)
            cur = []
    if cur:
        tables.append(cur)
    assert tables
    checked = 0
    for t in tables:
        blob = "\n".join(t)
        if "top-1 acc" not in blob:
            continue
        checked += 1
        assert "abstention" in blob or "abstain" in blob, (
            "a table reports accuracy with no abstention rate anywhere in it:\n"
            + blob)
    assert checked >= 3, "the accuracy tables were not found at all"


def test_the_report_states_that_the_products_are_not_photographs(quick_run):
    md = B.render_markdown(quick_run).lower()
    assert "not real" in md and "upper bound" in md
    assert "rendered" in md


def test_the_where_this_loses_section_names_conditions_not_platitudes(quick_run):
    md = B.render_markdown(quick_run)
    section = md.split("## Where this loses", 1)[1].split("## What is real")[0]
    assert len(section) > 1200
    for must in ("not in the gallery", "half-turn", "grey", "tau_mm"):
        assert must in section, f"'{must}' is not discussed"


def test_main_writes_the_report_and_the_json(tmp_path):
    out = tmp_path / "R.md"
    js = tmp_path / "r.json"
    rc = B.main(["--quick", "--no-sweep", "--out", str(out), "--json", str(js)])
    assert rc == 0
    # --quick deliberately writes no markdown: a 7-product subset must not be
    # able to overwrite the published report.
    assert not out.exists()
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert data["disjoint"]["ok"] is True


def test_a_full_main_run_writes_the_markdown(tmp_path):
    out = tmp_path / "RECOGNISE.md"
    rc = B.main(["--no-sweep", "--out", str(out)])
    assert rc == 0
    md = out.read_text(encoding="utf-8")
    assert md.startswith("# RECOGNISE")
    assert "FALSE-PRICE RATE" in md


def test_the_published_report_is_the_one_this_code_produces():
    """results/RECOGNISE.md must not drift into a hand-edited artefact."""
    p = ROOT / "results" / "RECOGNISE.md"
    if not p.exists():
        pytest.skip("results/RECOGNISE.md has not been generated yet")
    md = p.read_text(encoding="utf-8")
    assert md.startswith("# RECOGNISE")
    assert "tools/bench_recognise.py" in md
    assert "FALSE-PRICE RATE" in md
    assert f"theta={DEFAULT_THETA}, phi={DEFAULT_PHI}" in md


# ============================================== the html rendering of the same

def test_the_html_is_generated_from_the_markdown_not_written_twice():
    """One source of truth. Two hand-maintained renderings of a measurement is
    two places for a number to rot."""
    src = (ROOT / "tools" / "bench_recognise.py").read_text(encoding="utf-8")
    body = src.split("def render_html", 1)[1].split("\n# ---", 1)[0]
    assert "render_markdown(res)" in body, (
        "render_html no longer derives from render_markdown; the two can now "
        "disagree about a measured number")


def test_the_markdown_converter_handles_everything_the_report_emits():
    md = "\n".join([
        "# Title", "",
        "A paragraph with **bold**, `code` and a [link](#anchor).", "",
        "| a | b |", "|---|---|", "| **6** | !3! |", "",
        "- one", "  continued", "- two", "",
        "```", "cmd --flag", "```", "",
        "## Section", "", "0.0% and 70.8%",
    ])
    html = B._md_to_html(md)
    assert "<h1 id=\"title\">Title</h1>" in html
    assert "<strong>bold</strong>" in html and "<code>code</code>" in html
    assert '<a href="#anchor">link</a>' in html
    assert "<table>" in html and "<th>a</th>" in html
    assert '<span class="cell ok">6</span>' in html
    assert '<span class="cell bad">3</span>' in html
    assert "<li>one continued</li>" in html and "<li>two</li>" in html
    assert "<pre><code>cmd --flag</code></pre>" in html
    assert "**" not in html and "\n|" not in html


def test_a_zero_false_price_rate_and_a_bad_one_do_not_look_the_same():
    assert '<span class="cell ok">0.0%</span>' in B._inline("**0.0%**")
    assert '<span class="cell bad">70.8%</span>' in B._inline("**70.8%**")


def test_the_converter_escapes_html_it_is_given():
    assert "&lt;script&gt;" in B._inline("<script>")
    assert "<script>" not in B._inline("<script>")


def test_the_page_is_self_contained_and_theme_aware(quick_run):
    html = B.render_html(quick_run)
    for scheme in ("http://", "https://", "//cdn", "<img"):
        assert scheme not in html, f"the page reaches outside itself: {scheme}"
    assert "@media (prefers-color-scheme:dark)" in html
    assert ':root[data-theme="dark"]' in html
    assert ':root:not([data-theme="light"])' in html
    css = html.split("<style>", 1)[1].split("</style>", 1)[0]
    assert "background:var(--ground)" in css, (
        "body has no explicit background; it would borrow the host's theme")
    # every token used must be defined on the bare :root, not only in a
    # media/[data-theme] block, or it vanishes in the un-stamped default state
    base = css.split(":root{", 1)[1].split("}", 1)[0]
    for token in ("--ground", "--surface", "--ink", "--muted", "--rule",
                  "--accent", "--ok", "--amber", "--bad"):
        assert token in base, f"{token} is not defined on the bare :root"


def test_the_page_carries_the_measured_numbers_not_placeholders(quick_run):
    html = B.render_html(quick_run)
    h = quick_run.headline
    for frag in (B._pc(h["enrolled"]["top1_accuracy_on_decided"]),
                 B._pc(h["enrolled"]["abstention_rate"]),
                 B._pc(h["all"]["false_price_rate"]),
                 str(quick_run.capture["embed_dim"]),
                 str(quick_run.disjoint["n_eval_crops"])):
        assert frag in html, f"{frag} is missing from the page"
    assert "lorem" not in html.lower()


def test_main_can_write_the_page(tmp_path):
    out = tmp_path / "R.html"
    assert B.main(["--no-sweep", "--out", str(tmp_path / "R.md"),
                   "--html", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<title>")
    assert "FALSE-PRICE RATE" in html.upper()


# =========================================================== money, invariants

def test_the_bench_cannot_settle_money():
    """INVARIANT 2. Recognition proposes; only a signature-verified webhook
    disposes. A bench that could mint or settle would be a hole in that."""
    src = (ROOT / "tools" / "bench_recognise.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    forbidden = {"gawaah.kernel", "gawaah.webhook", "gawaah.rzp_live",
                 "gawaah.rzp_sim", "gawaah.paisa", "gawaah.session",
                 "gawaah.mudra", "gawaah.sellevent"}
    assert not (imported & forbidden), imported & forbidden
    for name in ("mint", "settle", "def pay", "webhook", "gateway", "signature"):
        assert f"def {name}" not in src


def test_the_no_float_lint_stays_green():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "lint_no_float.py")],
                       cwd=str(ROOT), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS" in r.stdout


def test_the_bench_uses_the_real_modules_not_a_local_copy():
    import gawaah.embedder as E
    import gawaah.identity as I
    assert B.embed is E.embed
    assert B.EMBED_DIM is E.EMBED_DIM
    assert (B.DEFAULT_THETA, B.DEFAULT_PHI, B.DEFAULT_TAU_MM) == \
        (I.DEFAULT_THETA, I.DEFAULT_PHI, I.DEFAULT_TAU_MM)
    colour, grey, src = B.crop_fns()
    from gawaah.brain import Brain
    assert grey is Brain._crop
    assert src.startswith("tools.upload_app")
