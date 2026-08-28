"""S5a acceptance: identity proposes, thresholds dispose, amber is a result.

Every test here injects a DETERMINISTIC FAKE EMBEDDER. No model is downloaded,
none is imported, and nothing touches the network. The fake is a pure function
of the crop's pixel bytes — sha256 -> seeded PCG64 -> unit gaussian — which is
the same contract a real embedder has (same pixels in, same vector out) minus
the weights.

Where a test needs a vector at a KNOWN similarity to the query it uses
at_cosine(), which places a unit vector at an exactly specified cosine from
another. That helper is itself tested first, because a lying helper would make
every threshold assertion below meaningless.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings, strategies as st

from gawaah.identity import (
    ABSTAIN_REASONS,
    REASON_AMBIGUOUS,
    REASON_BELOW_MARGIN,
    REASON_BELOW_SIMILARITY,
    REASON_MATCH,
    REASON_NO_CANDIDATE,
    Collision,
    CollisionError,
    Gallery,
    Identification,
    Identifier,
    IdentityError,
    cosine,
)
from gawaah.ledger import canonical

DIM = 24
THETA = 0.10
PHI = 0.55
TAU = 4.0


# ------------------------------------------------------- the injected fake

def _seed(token: str) -> int:
    return int.from_bytes(hashlib.sha256(token.encode("utf-8")).digest()[:8], "big")


def crop_for(token: str, h: int = 32, w: int = 24) -> np.ndarray:
    """A distinct, reproducible 'crop'. Pixels only — no label rides along."""
    rng = np.random.default_rng(_seed(token))
    return rng.integers(0, 256, size=(h, w), dtype=np.uint8)


def hash_embed(crop: np.ndarray, dim: int = DIM) -> np.ndarray:
    """The fake embedder: a pure function of pixel bytes. Deterministic across
    processes and runs; PCG64 and sha256 are both specified algorithms."""
    digest = hashlib.sha256(np.ascontiguousarray(crop).tobytes()).digest()
    rng = np.random.default_rng(int.from_bytes(digest[:8], "big"))
    v = rng.standard_normal(dim)
    return v / np.linalg.norm(v)


def at_cosine(a: np.ndarray, c: float, seed: int = 0) -> np.ndarray:
    """A unit vector whose cosine with `a` is exactly c.

    Gram-Schmidt a random direction against `a`, then take c along `a` and
    sqrt(1-c^2) along the orthogonal remainder.
    """
    a = np.asarray(a, dtype=np.float64)
    a = a / np.linalg.norm(a)
    rng = np.random.default_rng(seed)
    r = rng.standard_normal(a.shape[0])
    r = r - np.dot(r, a) * a
    r = r / np.linalg.norm(r)
    return c * a + math.sqrt(max(0.0, 1.0 - c * c)) * r


def make(*entries, theta=THETA, phi=PHI, tau_mm=TAU) -> Identifier:
    """Build an Identifier from (sku, vector, footprint_mm) triples, bypassing
    the collision guard so tests can construct deliberately nasty galleries."""
    g = Gallery()
    for sku, vec, fp in entries:
        g.enroll(sku, [vec], fp)
    return Identifier(g, hash_embed, theta=theta, phi=phi, tau_mm=tau_mm)


# ------------------------------------------------------- helper self-checks

def test_the_fake_embedder_is_deterministic_and_unit_norm():
    a, b = hash_embed(crop_for("parle-g")), hash_embed(crop_for("parle-g"))
    assert np.array_equal(a, b)
    assert a.shape == (DIM,)
    assert abs(float(np.linalg.norm(a)) - 1.0) < 1e-12
    # different pixels -> different direction, and not by luck
    assert cosine(a, hash_embed(crop_for("britannia"))) < 0.9


@pytest.mark.parametrize("c", [-1.0, -0.5, 0.0, 0.3, 0.55, 0.9, 0.999, 1.0])
def test_at_cosine_helper_is_exact(c):
    """If this helper is wrong every threshold test below is theatre."""
    q = hash_embed(crop_for("anchor"))
    v = at_cosine(q, c, seed=7)
    assert cosine(q, v) == pytest.approx(c, abs=1e-12)
    assert float(np.linalg.norm(v)) == pytest.approx(1.0, abs=1e-12)


# -------------------------------------------------------------- cosine

def test_cosine_matches_hand_computation():
    assert cosine([1.0, 0.0], [1.0, 1.0]) == pytest.approx(1 / math.sqrt(2), abs=1e-15)
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0, abs=1e-15)
    assert cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0, abs=1e-15)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0, abs=1e-15)
    assert cosine([3.0, 4.0], [4.0, 3.0]) == pytest.approx(24 / 25, abs=1e-15)


def test_cosine_agrees_with_an_independent_formula():
    rng = np.random.default_rng(11)
    for _ in range(200):
        a = rng.standard_normal(DIM)
        b = rng.standard_normal(DIM)
        ref = sum(x * y for x, y in zip(a, b)) / (
            math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
        )
        assert cosine(a, b) == pytest.approx(ref, abs=1e-12)


def test_cosine_is_symmetric_and_scale_invariant():
    rng = np.random.default_rng(3)
    a, b = rng.standard_normal(DIM), rng.standard_normal(DIM)
    assert cosine(a, b) == pytest.approx(cosine(b, a), abs=1e-15)
    assert cosine(a, b) == pytest.approx(cosine(a * 1000.0, b * 0.001), abs=1e-12)


def test_cosine_never_leaves_the_unit_interval():
    a = np.ones(DIM)
    assert -1.0 <= cosine(a, a) <= 1.0
    assert cosine(a, a * 1e12) <= 1.0


def test_cosine_of_a_zero_vector_is_zero_not_nan():
    z = np.zeros(DIM)
    assert cosine(z, np.ones(DIM)) == 0.0
    assert not math.isnan(cosine(z, z))


def test_cosine_rejects_bad_input():
    with pytest.raises(IdentityError):
        cosine([1.0, 0.0], [1.0, 0.0, 0.0])          # dimension mismatch
    with pytest.raises(IdentityError):
        cosine([[1.0, 0.0]], [[1.0, 0.0]])           # not 1-D
    with pytest.raises(IdentityError):
        cosine([1.0, float("nan")], [1.0, 0.0])      # NaN


# ------------------------------------------------------------- clear match

def test_a_clear_match_returns_the_sku():
    crop = crop_for("parle-g")
    q = hash_embed(crop)
    ident = make(
        ("parle-g", q, 118.0),
        ("britannia", at_cosine(q, 0.20, seed=1), 118.0),
    )
    r = ident.identify(crop, 118.0)
    assert r.sku_id == "parle-g"
    assert r.reason == REASON_MATCH
    assert r.is_match and not r.is_amber
    assert r.top1 == pytest.approx(1.0, abs=1e-12)
    assert r.top2 == pytest.approx(0.20, abs=1e-12)
    assert r.margin == pytest.approx(0.80, abs=1e-12)
    assert r.n_candidates == 2


def test_match_uses_the_best_enrolled_view_not_the_average():
    """Four views enrolled; the query matches one of them. Averaging the four
    would bury it below phi — best-of must not."""
    crop = crop_for("maggi")
    q = hash_embed(crop)
    g = Gallery()
    g.enroll(
        "maggi",
        [at_cosine(q, 0.05, seed=s) for s in (1, 2, 3)] + [q],
        95.0,
    )
    ident = Identifier(g, hash_embed, theta=THETA, phi=PHI, tau_mm=TAU)
    r = ident.identify(crop, 95.0)
    assert r.sku_id == "maggi"
    assert r.top1 == pytest.approx(1.0, abs=1e-12)
    mean_view = np.mean(g.get("maggi").vectors, axis=0)
    assert cosine(q, mean_view) < PHI      # the averaging trap, demonstrated


# ------------------------------------------------------------- below_margin

def test_two_near_identical_vectors_abstain_below_margin():
    crop = crop_for("shampoo-sachet")
    q = hash_embed(crop)
    ident = make(
        ("clinic-plus", at_cosine(q, 0.900, seed=1), 60.0),
        ("head-shoulders", at_cosine(q, 0.895, seed=2), 60.0),
    )
    r = ident.identify(crop, 60.0)
    assert r.sku_id is None
    assert r.is_amber
    assert r.reason == REASON_BELOW_MARGIN
    assert r.top1 == pytest.approx(0.900, abs=1e-12)
    assert r.top2 == pytest.approx(0.895, abs=1e-12)
    assert r.margin == pytest.approx(0.005, abs=1e-12)
    # it still PROPOSES, so the UI can ask "is this Clinic Plus?"
    assert r.top1_sku == "clinic-plus"
    assert r.top2_sku == "head-shoulders"


def test_margin_threshold_is_the_thing_that_decides():
    """Same top1, same everything, only the runner-up moves. theta disposes."""
    crop = crop_for("boundary")
    q = hash_embed(crop)

    below = make(("a", at_cosine(q, 0.90, seed=1), 70.0),
                 ("b", at_cosine(q, 0.90 - THETA + 1e-6, seed=2), 70.0))
    assert below.identify(crop, 70.0).reason == REASON_BELOW_MARGIN

    above = make(("a", at_cosine(q, 0.90, seed=1), 70.0),
                 ("b", at_cosine(q, 0.90 - THETA - 1e-6, seed=2), 70.0))
    assert above.identify(crop, 70.0).sku_id == "a"


# ---------------------------------------------------------- below_similarity

def test_nothing_looks_like_it_abstains_below_similarity():
    crop = crop_for("unknown-item")
    q = hash_embed(crop)
    ident = make(
        ("a", at_cosine(q, 0.40, seed=1), 80.0),
        ("b", at_cosine(q, 0.05, seed=2), 80.0),
    )
    r = ident.identify(crop, 80.0)
    assert r.sku_id is None
    assert r.reason == REASON_BELOW_SIMILARITY
    assert r.top1 == pytest.approx(0.40, abs=1e-12)
    assert r.margin > THETA          # the margin was fine; phi still refused


def test_below_similarity_beats_below_margin_when_both_fail():
    """A wide margin over a runner-up nobody believes in is not evidence."""
    crop = crop_for("mystery")
    q = hash_embed(crop)
    ident = make(("a", at_cosine(q, 0.50, seed=1), 80.0),
                 ("b", at_cosine(q, 0.49, seed=2), 80.0))
    assert ident.identify(crop, 80.0).reason == REASON_BELOW_SIMILARITY


def test_a_lone_anti_correlated_candidate_still_abstains():
    """Pinned because the property test found it: with one candidate top2 is the
    stand-in 0.0, so a negative top1 makes margin negative. That is odd-looking
    but harmless — phi rejects it long before margin is consulted. The number is
    reported honestly rather than clamped into looking sensible."""
    crop = crop_for("opposite")
    q = hash_embed(crop)
    ident = make(("only", -q, 100.0))
    r = ident.identify(crop, 100.0)
    assert r.top1 == pytest.approx(-1.0, abs=1e-12)
    assert r.top2 == 0.0 and r.top2_sku is None
    assert r.margin == pytest.approx(-1.0, abs=1e-12)
    assert r.sku_id is None and r.reason == REASON_BELOW_SIMILARITY


def test_a_lone_candidate_still_has_to_clear_phi():
    crop = crop_for("lonely")
    q = hash_embed(crop)
    ident = make(("only", at_cosine(q, 0.30, seed=1), 90.0))
    r = ident.identify(crop, 90.0)
    assert r.sku_id is None and r.reason == REASON_BELOW_SIMILARITY
    assert r.n_candidates == 1 and r.top2 == 0.0 and r.top2_sku is None


# ----------------------------------------------------------- ambiguous_pair

def test_an_exact_tie_is_an_ambiguous_pair_not_a_coin_flip():
    crop = crop_for("twins")
    q = hash_embed(crop)
    v = at_cosine(q, 0.97, seed=5)
    ident = make(("twin-a", v, 75.0), ("twin-b", v.copy(), 75.0))
    r = ident.identify(crop, 75.0)
    assert r.sku_id is None
    assert r.reason == REASON_AMBIGUOUS
    assert r.margin == 0.0
    assert {r.top1_sku, r.top2_sku} == {"twin-a", "twin-b"}   # UI names BOTH


def test_a_tie_below_phi_is_reported_as_below_similarity():
    crop = crop_for("tied-and-unlike")
    q = hash_embed(crop)
    v = at_cosine(q, 0.30, seed=6)
    ident = make(("x", v, 75.0), ("y", v.copy(), 75.0))
    assert ident.identify(crop, 75.0).reason == REASON_BELOW_SIMILARITY


# ------------------------------------------------ the metric tiebreak (mm)

def test_footprint_excludes_a_same_looking_but_wrong_sized_item():
    """The 500 ml / 1 L problem. Identical appearance, 30 mm apart on the mat.
    The mat decides, and the SAME crop yields different SKUs at different sizes.
    """
    crop = crop_for("cola-bottle")
    q = hash_embed(crop)
    ident = make(("cola-500", q, 100.0), ("cola-1000", q.copy(), 130.0))

    small = ident.identify(crop, 100.0)
    assert small.sku_id == "cola-500" and small.n_candidates == 1

    large = ident.identify(crop, 130.0)
    assert large.sku_id == "cola-1000" and large.n_candidates == 1

    # and without the footprint filter these two would have been a dead tie
    wide = make(("cola-500", q, 100.0), ("cola-1000", q.copy(), 130.0), tau_mm=1000.0)
    assert wide.identify(crop, 100.0).reason == REASON_AMBIGUOUS


def test_no_candidate_in_footprint_when_nothing_is_the_right_size():
    crop = crop_for("cola-bottle")
    q = hash_embed(crop)
    ident = make(("cola-500", q, 100.0), ("cola-1000", q.copy(), 130.0))
    r = ident.identify(crop, 220.0)
    assert r.sku_id is None
    assert r.reason == REASON_NO_CANDIDATE
    assert r.n_candidates == 0 and r.top1 == 0.0 and r.top1_sku is None
    assert r.long_edge_mm == 220.0


def test_empty_gallery_abstains_rather_than_erroring():
    ident = Identifier(Gallery(), hash_embed)
    r = ident.identify(crop_for("anything"), 100.0)
    assert r.sku_id is None and r.reason == REASON_NO_CANDIDATE


def test_footprint_tolerance_is_inclusive_at_tau():
    crop = crop_for("edge")
    q = hash_embed(crop)
    ident = make(("a", q, 100.0), tau_mm=4.0)
    assert ident.candidates(104.0) != ()          # exactly tau -> in
    assert ident.candidates(96.0) != ()
    assert ident.candidates(104.001) == ()        # a hair past tau -> out
    assert ident.identify(crop, 104.0).sku_id == "a"
    assert ident.identify(crop, 104.5).reason == REASON_NO_CANDIDATE


def test_identity_is_never_attempted_without_a_metric_footprint():
    ident = make(("a", hash_embed(crop_for("a")), 100.0))
    for bad in (None, 0.0, -5.0, "100", float("nan"), float("inf"), True):
        with pytest.raises(IdentityError):
            ident.identify(crop_for("a"), bad)


# ------------------------------------------------------ collision guard

def test_collision_guard_fires_on_an_identical_pair():
    q = hash_embed(crop_for("bru-instant"))
    ident = make(("bru-instant", q, 82.0))
    c = ident.check_collision([q.copy()], 82.0)
    assert c.collides
    assert c.sku_id == "bru-instant"
    assert c.colliding == ("bru-instant",)
    assert c.similarity == pytest.approx(1.0, abs=1e-12)
    assert c.footprint_delta_mm == pytest.approx(0.0, abs=1e-12)
    assert "disambiguation" in c.message


def test_collision_guard_needs_BOTH_appearance_and_footprint():
    q = hash_embed(crop_for("base"))
    ident = make(("base", q, 100.0))

    # same appearance, different size -> not a collision, the mat separates them
    assert not ident.check_collision([q.copy()], 100.0 + TAU + 0.5).collides
    # same size, different appearance -> not a collision, the vector separates them
    assert not ident.check_collision([at_cosine(q, 0.50, seed=1)], 100.0).collides
    # both -> collision
    assert ident.check_collision([at_cosine(q, 0.95, seed=1)], 102.0).collides


def test_collision_appearance_bar_is_one_minus_theta():
    q = hash_embed(crop_for("bar"))
    ident = make(("base", q, 100.0), theta=0.10)
    assert ident.check_collision([at_cosine(q, 0.9 + 1e-6, seed=1)], 100.0).collides
    assert not ident.check_collision([at_cosine(q, 0.9 - 1e-6, seed=1)], 100.0).collides


def test_collision_reports_every_offender_and_the_worst_one_first():
    q = hash_embed(crop_for("crowd"))
    g = Gallery()
    g.enroll("near", [at_cosine(q, 0.93, seed=1)], 100.0)
    g.enroll("nearer", [at_cosine(q, 0.99, seed=2)], 100.0)
    g.enroll("far", [at_cosine(q, 0.10, seed=3)], 100.0)
    ident = Identifier(g, hash_embed)
    c = ident.check_collision([q], 100.0)
    assert c.sku_id == "nearer"
    assert c.colliding == ("near", "nearer")


def test_guarded_enrolment_refuses_and_leaves_the_gallery_untouched():
    q = hash_embed(crop_for("colgate-100g"))
    g = Gallery()
    g.enroll("colgate-100g", [q], 96.0)
    ident = Identifier(g, hash_embed)
    with pytest.raises(CollisionError) as ei:
        ident.enroll("colgate-100g-refill", [q.copy()], 96.0)
    assert ei.value.collision.sku_id == "colgate-100g"
    assert g.skus() == ("colgate-100g",)          # nothing was written


def test_guarded_enrolment_allows_a_genuinely_different_item():
    g = Gallery()
    ident = Identifier(g, hash_embed)
    ident.enroll("a", [hash_embed(crop_for("a"))], 100.0)
    ident.enroll("b", [hash_embed(crop_for("b"))], 100.0)
    assert g.skus() == ("a", "b")


def test_an_item_does_not_collide_with_its_own_outgoing_entry():
    q = hash_embed(crop_for("re-enrol"))
    g = Gallery()
    g.enroll("same-sku", [q], 100.0)
    ident = Identifier(g, hash_embed)
    ident.enroll("same-sku", [q.copy(), at_cosine(q, 0.8, seed=4)], 100.0, replace=True)
    assert g.get("same-sku").n_views == 2


def test_forced_collision_is_permanently_amber():
    """The guard's whole justification, executed: force a colliding pair in and
    identify() can never separate them again."""
    q = hash_embed(crop_for("forced"))
    g = Gallery()
    g.enroll("a", [q], 100.0)
    ident = Identifier(g, hash_embed)
    ident.enroll("b", [at_cosine(q, 0.95, seed=1)], 101.0, force=True)
    r = ident.identify(crop_for("forced"), 100.5)
    assert r.sku_id is None
    assert r.reason in ABSTAIN_REASONS
    assert r.margin < THETA


def test_collision_guard_on_an_empty_gallery_is_clear():
    ident = Identifier(Gallery(), hash_embed)
    c = ident.check_collision([hash_embed(crop_for("first"))], 100.0)
    assert isinstance(c, Collision) and not c.collides and c.reason == "clear"


# ---------------------------------------------------------------- determinism

def test_repeated_identification_is_bit_identical():
    crop = crop_for("repeat")
    q = hash_embed(crop)
    ident = make(("a", at_cosine(q, 0.9, seed=1), 100.0),
                 ("b", at_cosine(q, 0.5, seed=2), 100.0))
    first = ident.identify(crop, 100.0)
    for _ in range(20):
        assert ident.identify(crop, 100.0) == first


def test_result_does_not_depend_on_enrolment_order():
    crop = crop_for("order")
    q = hash_embed(crop)
    triples = [
        ("alpha", at_cosine(q, 0.91, seed=1), 100.0),
        ("beta", at_cosine(q, 0.40, seed=2), 101.0),
        ("gamma", at_cosine(q, 0.70, seed=3), 99.0),
        ("delta", at_cosine(q, 0.10, seed=4), 102.0),
    ]
    forward = make(*triples).identify(crop, 100.0)
    backward = make(*reversed(triples)).identify(crop, 100.0)
    assert forward == backward
    assert forward.sku_id == "alpha"


def test_a_tie_does_not_depend_on_enrolment_order_either():
    crop = crop_for("tie-order")
    q = hash_embed(crop)
    v = at_cosine(q, 0.98, seed=9)
    a = make(("zeta", v, 100.0), ("aardvark", v.copy(), 100.0)).identify(crop, 100.0)
    b = make(("aardvark", v.copy(), 100.0), ("zeta", v, 100.0)).identify(crop, 100.0)
    assert a == b and a.reason == REASON_AMBIGUOUS


# ---------------------------------------------------------------- gallery I/O

def test_gallery_roundtrips_through_json_and_behaves_identically(tmp_path):
    crop = crop_for("roundtrip")
    q = hash_embed(crop)
    g = Gallery()
    g.enroll("a", [q, at_cosine(q, 0.6, seed=1)], 100.0)
    g.enroll("b", [at_cosine(q, 0.3, seed=2)], 103.0)
    p = g.save(tmp_path / "gallery.json")

    back = Gallery.load(p)
    assert back.skus() == g.skus()
    assert back.dim == g.dim == DIM
    for sku in g.skus():
        assert np.array_equal(back.get(sku).vectors, g.get(sku).vectors)
        assert back.get(sku).footprint_mm == g.get(sku).footprint_mm

    before = Identifier(g, hash_embed).identify(crop, 100.0)
    after = Identifier(back, hash_embed).identify(crop, 100.0)
    assert before == after and after.sku_id == "a"


def test_saved_gallery_is_byte_identical_regardless_of_enrolment_order(tmp_path):
    q = hash_embed(crop_for("bytes"))
    vecs = {"a": at_cosine(q, 0.9, seed=1), "b": at_cosine(q, 0.5, seed=2)}

    g1 = Gallery()
    g1.enroll("a", [vecs["a"]], 100.0)
    g1.enroll("b", [vecs["b"]], 101.0)
    g2 = Gallery()
    g2.enroll("b", [vecs["b"]], 101.0)
    g2.enroll("a", [vecs["a"]], 100.0)

    assert g1.save(tmp_path / "1.json").read_bytes() == \
           g2.save(tmp_path / "2.json").read_bytes()
    assert json.loads((tmp_path / "1.json").read_text())["dim"] == DIM


def test_gallery_load_rejects_an_unknown_format(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"format": 99, "dim": 3, "entries": {}}))
    with pytest.raises(IdentityError):
        Gallery.load(p)


# ------------------------------------------------------------- gallery rules

def test_gallery_refuses_to_silently_overwrite():
    g = Gallery()
    v = hash_embed(crop_for("dup"))
    g.enroll("a", [v], 100.0)
    with pytest.raises(IdentityError):
        g.enroll("a", [v], 100.0)
    g.enroll("a", [v], 101.0, replace=True)
    assert g.footprint("a") == 101.0


@pytest.mark.parametrize(
    "sku,vectors,fp",
    [
        ("a", [np.zeros(DIM)], 100.0),                        # no direction
        ("a", [], 100.0),                                     # no views
        ("a", [np.ones(DIM)], 0.0),                           # no size
        ("a", [np.ones(DIM)], -3.0),                          # negative size
        ("a", [np.ones(DIM), np.ones(DIM + 1)], 100.0),       # mixed dims
        ("a", [np.full(DIM, np.nan)], 100.0),                 # NaN
        ("", [np.ones(DIM)], 100.0),                          # no sku id
    ],
)
def test_gallery_rejects_garbage(sku, vectors, fp):
    with pytest.raises(IdentityError):
        Gallery().enroll(sku, vectors, fp)


def test_gallery_enforces_one_embedding_dimension():
    g = Gallery()
    g.enroll("a", [np.ones(DIM)], 100.0)
    with pytest.raises(IdentityError):
        g.enroll("b", [np.ones(DIM + 4)], 100.0)


def test_identifier_rejects_a_query_of_the_wrong_dimension():
    g = Gallery()
    g.enroll("a", [np.ones(DIM)], 100.0)
    ident = Identifier(g, lambda crop: np.ones(DIM + 1))
    with pytest.raises(IdentityError):
        ident.identify(crop_for("x"), 100.0)


def test_the_embedder_must_actually_be_injected():
    with pytest.raises(IdentityError):
        Identifier(Gallery(), "not-a-callable")


def test_gallery_lookups_of_unknown_skus_raise():
    g = Gallery()
    assert len(g) == 0 and g.dim is None and "a" not in g
    with pytest.raises(IdentityError):
        g.get("a")
    with pytest.raises(IdentityError):
        g.remove("a")


# -------------------------------------------------------------- audit shape

def test_every_abstention_reason_is_declared_and_amber():
    """One scenario per reason. All four must be reachable, all four amber."""
    crop = crop_for("audit")
    q = hash_embed(crop)
    v = at_cosine(q, 0.97, seed=1)
    scenarios = {
        REASON_NO_CANDIDATE: (make(("a", q, 100.0)), 300.0),
        REASON_BELOW_SIMILARITY: (make(("a", at_cosine(q, 0.2, seed=2), 100.0)), 100.0),
        REASON_BELOW_MARGIN: (
            make(("a", at_cosine(q, 0.90, seed=3), 100.0),
                 ("b", at_cosine(q, 0.87, seed=4), 100.0)), 100.0),
        REASON_AMBIGUOUS: (make(("a", v, 100.0), ("b", v.copy(), 100.0)), 100.0),
    }
    seen = set()
    for expected, (ident, mm) in scenarios.items():
        r = ident.identify(crop, mm)
        assert r.reason == expected
        assert r.sku_id is None and r.is_amber and not r.is_match
        assert r.reason in ABSTAIN_REASONS
        seen.add(r.reason)
    assert seen == set(ABSTAIN_REASONS)


def test_identification_serialises_into_a_ledger_line():
    crop = crop_for("ledger")
    q = hash_embed(crop)
    r = make(("a", q, 100.0)).identify(crop, 100.0)
    fields = r.to_audit()
    assert json.loads(canonical(fields).decode("utf-8")) == fields
    assert fields["sku_id"] == "a" and fields["reason"] == REASON_MATCH
    amber = make(("a", at_cosine(q, 0.1, seed=1), 100.0)).identify(crop, 100.0)
    assert amber.to_audit()["sku_id"] is None
    c = make(("a", q, 100.0)).check_collision([q.copy()], 100.0)
    assert json.loads(canonical(c.to_audit()).decode("utf-8"))["collides"] is True


# -------------------------------------------------- invariant 3: no weights

def test_the_module_imports_no_model_and_no_network():
    """Invariant 3 lives on the phone, but it starts here: this module must not
    be able to acquire a model even if someone wanted it to."""
    src = Path(__file__).resolve().parent.parent / "gawaah" / "identity.py"
    roots = set()
    for node in ast.walk(ast.parse(src.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    assert roots <= {"__future__", "json", "dataclasses", "pathlib", "typing",
                     "numpy", "gawaah"}, f"unexpected imports: {sorted(roots)}"
    banned = {"torch", "tensorflow", "onnxruntime", "urllib", "requests",
              "http", "socket", "ftplib", "cv2", "transformers"}
    assert not (roots & banned)


# ----------------------------------------------------------------- property

@settings(max_examples=250, deadline=None)
@given(
    specs=st.lists(
        st.tuples(
            st.floats(min_value=-1.0, max_value=1.0, allow_nan=False,
                      allow_infinity=False),
            st.floats(min_value=90.0, max_value=115.0, allow_nan=False,
                      allow_infinity=False),
        ),
        min_size=0,
        max_size=6,
    ),
    query_mm=st.floats(min_value=95.0, max_value=110.0, allow_nan=False,
                       allow_infinity=False),
)
def test_the_contract_holds_for_any_gallery(specs, query_mm):
    """Whatever the gallery, a returned SKU always cleared BOTH bars, and an
    abstention always carries a declared reason. This is the promise the rest
    of GAWAAH is allowed to rely on."""
    crop = crop_for("property")
    q = hash_embed(crop)
    g = Gallery()
    for i, (c, fp) in enumerate(specs):
        g.enroll(f"sku_{i:02d}", [at_cosine(q, c, seed=i)], fp)
    ident = Identifier(g, hash_embed, theta=THETA, phi=PHI, tau_mm=TAU)
    r = ident.identify(crop, query_mm)

    assert isinstance(r, Identification)
    if r.top2_sku is not None:
        assert r.top1 >= r.top2 - 1e-15        # a real competitor never outranks
    assert r.margin == pytest.approx(r.top1 - r.top2, abs=1e-15)
    assert r.n_candidates == len(ident.candidates(query_mm))
    if r.is_match:
        assert r.reason == REASON_MATCH
        assert r.sku_id == r.top1_sku
        assert r.top1 >= PHI
        assert r.margin >= THETA
        assert abs(g.footprint(r.sku_id) - query_mm) <= TAU
    else:
        assert r.sku_id is None
        assert r.reason in ABSTAIN_REASONS
        assert r.top1 < PHI or r.margin < THETA
    assert ident.identify(crop, query_mm) == r      # and it is repeatable
