"""S5b acceptance: the counter names the shopkeeper's OWN products, and prices
them — or it says, by name, why it will not.

Three layers, deliberately:

  1. WIRING, with a hand-written embed_fn that returns whatever vector the test
     needs. Every abstention reason is reachable this way and each one gets its
     own test, because the reason code is what the UI branches on.

  2. PIXELS, with a real product set rendered onto a real 840x1188 mat and
     pulled back out through the same rotate-then-getRectSubPix round trip
     `Brain._crop` performs. Six products, ALL AT THE SAME 60 x 36 mm
     footprint, so the metric tiebreak cannot do the work and appearance has to
     earn the answer. Enrolment is one pose; evaluation is six DIFFERENT poses
     (moved, rotated up to 90 deg, relit +/- 18%), and the enrolment pose is
     not among them.

  3. THE REAL PARTS, when they are installed: `gawaah.embedder.embed` is
     parametrised in beside the test's own descriptor, `gawaah.shop_store`
     is driven end to end, and the fixtures supply crops through the
     genuine PlaneEngine/PlacementDetector/Brain._crop optics.

No model weights, no network, no checkpoint. The pixel layers use only cv2
primitives that already ship.
"""
from __future__ import annotations

import itertools
import json
import time

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from gawaah.identity import (  # noqa: E402
    DEFAULT_PHI,
    DEFAULT_TAU_MM,
    DEFAULT_THETA,
    REASON_AMBIGUOUS,
    REASON_BELOW_MARGIN,
    REASON_BELOW_SIMILARITY,
    REASON_MATCH,
    REASON_NO_CANDIDATE,
    Gallery,
)
from gawaah.ledger import canonical  # noqa: E402
from gawaah.money import MoneyError  # noqa: E402
from gawaah.recogniser import (  # noqa: E402
    ALL_REASONS,
    RECOGNISER_ABSTAIN_REASONS,
    REASON_BAD_PRICE,
    REASON_EMBED_FAILED,
    REASON_NONPOSITIVE_PRICE,
    REASON_NO_FOOTPRINT,
    REASON_NO_GALLERY,
    REASON_NO_PRICE,
    MemoryStore,
    Recogniser,
    RecogniserError,
    Recognition,
    abstentions,
    basket_paise,
    billable,
)

DIM = 8
FP = 60.0          # every wiring-layer sku shares this footprint, in mm


# ====================================================================== layer 1
# WIRING. The embedder is a lookup table, so the only thing under test is what
# the Recogniser does with a score.

def _v(*xs: float) -> np.ndarray:
    a = np.zeros(DIM, dtype=np.float64)
    a[: len(xs)] = xs
    return a


def _at_cosine(base: np.ndarray, c: float) -> np.ndarray:
    """A unit vector at EXACTLY cosine `c` from `base`. Lets a test place a
    score on either side of a gate without hoping an embedder lands there."""
    b = base / np.linalg.norm(base)
    perp = np.zeros_like(b)
    perp[int(np.argmin(np.abs(b)))] = 1.0
    perp = perp - np.dot(perp, b) * b
    perp = perp / np.linalg.norm(perp)
    return c * b + float(np.sqrt(max(0.0, 1.0 - c * c))) * perp


def test_at_cosine_helper_is_honest():
    base = _v(1.0, 2.0, -1.0)
    for c in (0.0, 0.4, 0.56, 0.9, 1.0):
        got = _at_cosine(base, c)
        b = base / np.linalg.norm(base)
        assert float(np.dot(b, got / np.linalg.norm(got))) == pytest.approx(c, abs=1e-9)


class _Table:
    """embed_fn as a dict: crop token -> vector. Deterministic by construction."""

    def __init__(self, table: dict[str, np.ndarray]) -> None:
        self.table = table
        self.calls = 0

    def __call__(self, crop) -> np.ndarray:
        self.calls += 1
        key = str(np.asarray(crop).ravel()[0])
        try:
            return self.table[key]
        except KeyError:  # pragma: no cover - a test asked for an unknown crop
            raise AssertionError(f"no vector staged for crop {key!r}") from None


def _crop(token: float) -> np.ndarray:
    return np.full((4, 4), token, dtype=np.float64)


def _wired(vectors: dict[str, np.ndarray], prices: dict[str, int | None],
           crops: dict[str, np.ndarray], **kw) -> tuple[Recogniser, MemoryStore]:
    store = MemoryStore()
    for sku, vec in vectors.items():
        store.add(sku, [vec], FP, prices.get(sku))
    return Recogniser(store, _Table(crops), **kw), store


# -- the happy path ----------------------------------------------------------

def test_a_taught_product_is_named_and_priced():
    a = _v(1.0)
    r, _ = _wired({"CHAI": a}, {"CHAI": 4500}, {"1.0": a})
    got = r.identify(_crop(1.0), FP)
    assert (got.sku_id, got.price_paise, got.reason) == ("CHAI", 4500, REASON_MATCH)
    assert got.abstained is False and got.is_billable is True
    assert isinstance(got.price_paise, int)


def test_the_price_is_integer_paise_and_never_a_float():
    a = _v(1.0)
    r, _ = _wired({"CHAI": a}, {"CHAI": 4500}, {"1.0": a})
    got = r.identify(_crop(1.0), FP)
    assert type(got.price_paise) is int
    assert not isinstance(got.price_paise, float)


# -- every abstention reason, one test each ----------------------------------

def test_no_gallery_at_all_abstains_it_does_not_crash():
    r = Recogniser(MemoryStore(), lambda c: _v(1.0))
    got = r.identify(_crop(1.0), FP)
    assert got.reason == REASON_NO_GALLERY
    assert got.abstained and got.price_paise is None and got.sku_id is None
    assert got.n_skus == 0


@pytest.mark.parametrize("bad", [None, 0.0, -3.0, "60", True, float("nan"),
                                 float("inf")])
def test_a_missing_or_impossible_footprint_abstains_with_a_name(bad):
    a = _v(1.0)
    r, _ = _wired({"CHAI": a}, {"CHAI": 4500}, {"1.0": a})
    got = r.identify(_crop(1.0), bad)
    assert got.reason == REASON_NO_FOOTPRINT
    assert got.abstained and got.price_paise is None
    assert got.detail  # it says WHAT was wrong, not just that something was


def test_nothing_of_this_size_abstains_with_no_candidate_in_footprint():
    a = _v(1.0)
    r, _ = _wired({"CHAI": a}, {"CHAI": 4500}, {"1.0": a})
    got = r.identify(_crop(1.0), FP + DEFAULT_TAU_MM + 0.5)
    assert got.reason == REASON_NO_CANDIDATE
    assert got.abstained and got.price_paise is None
    assert got.n_candidates == 0


def test_nothing_that_looks_like_this_abstains_with_below_similarity():
    a = _v(1.0)
    q = _at_cosine(a, DEFAULT_PHI - 0.05)
    r, _ = _wired({"CHAI": a}, {"CHAI": 4500}, {"1.0": q})
    got = r.identify(_crop(1.0), FP)
    assert got.reason == REASON_BELOW_SIMILARITY
    assert got.abstained and got.price_paise is None
    assert got.top1 < DEFAULT_PHI
    # It still says what it half-thought, so the UI can offer enrolment.
    assert got.top1_sku == "CHAI"


def test_a_leader_that_does_not_lead_by_enough_abstains_with_below_margin():
    a = _v(1.0)
    b = _at_cosine(a, 0.95)
    q = _at_cosine(a, 0.90)
    r, _ = _wired({"CHAI": a, "KAAFI": b}, {"CHAI": 4500, "KAAFI": 4000},
                  {"1.0": q})
    got = r.identify(_crop(1.0), FP)
    assert got.reason == REASON_BELOW_MARGIN
    assert got.abstained and got.price_paise is None
    assert 0.0 < got.margin < DEFAULT_THETA
    assert got.top1_sku and got.top2_sku and got.top1_sku != got.top2_sku


def test_two_candidates_equally_close_abstains_with_ambiguous_pair():
    a = _v(1.0)
    r, _ = _wired({"CHAI": a, "KAAFI": a.copy()},
                  {"CHAI": 4500, "KAAFI": 4000}, {"1.0": a})
    got = r.identify(_crop(1.0), FP)
    assert got.reason == REASON_AMBIGUOUS
    assert got.abstained and got.price_paise is None
    assert got.margin == pytest.approx(0.0, abs=1e-9)
    assert {got.top1_sku, got.top2_sku} == {"CHAI", "KAAFI"}


def test_a_sku_with_no_price_abstains_rather_than_billing_zero():
    a = _v(1.0)
    r, _ = _wired({"CHAI": a}, {"CHAI": None}, {"1.0": a})
    got = r.identify(_crop(1.0), FP)
    assert got.reason == REASON_NO_PRICE
    assert got.price_paise is None
    assert got.price_paise != 0
    assert got.abstained is True and got.is_billable is False
    # The one abstention that still names the sku: we know WHAT it is, we do
    # not know what it COSTS, and saying so is a one-tap fix.
    assert got.sku_id == "CHAI"
    assert got.top1 >= DEFAULT_PHI


def test_a_priceless_sku_is_excluded_from_the_total_not_added_as_zero():
    a, b = _v(1.0), _v(0.0, 1.0)
    store = MemoryStore()
    store.add("CHAI", [a], FP, 4500)
    store.add("SABUN", [b], FP, None)
    r = Recogniser(store, _Table({"1.0": a, "2.0": b}))
    lines = r.identify_many([(_crop(1.0), FP), (_crop(2.0), FP)])
    assert basket_paise(lines) == 4500
    assert [x.sku_id for x in abstentions(lines)] == ["SABUN"]
    assert [x.sku_id for x in billable(lines)] == ["CHAI"]


class _FloatPriceStore(MemoryStore):
    """A catalog that answers with a float. Somebody hand-edited shop.json."""

    def __init__(self, value):
        super().__init__()
        self.value = value

    def price_paise(self, sku_id):
        return self.value if sku_id in self.skus() else None


@pytest.mark.parametrize("bad", [21.99, "4500", True, None.__class__,
                                 np.float64(45.0)])
def test_a_price_that_is_not_integer_paise_abstains_it_is_never_rounded(bad):
    a = _v(1.0)
    store = _FloatPriceStore(bad)
    store.add("CHAI", [a], FP, None)
    r = Recogniser(store, _Table({"1.0": a}))
    got = r.identify(_crop(1.0), FP)
    assert got.reason == REASON_BAD_PRICE
    assert got.abstained and got.price_paise is None
    assert got.sku_id == "CHAI"


@pytest.mark.parametrize("bad", [0, -1, -4500])
def test_a_zero_or_negative_price_abstains_rather_than_billing_it(bad):
    a = _v(1.0)
    store = _FloatPriceStore(bad)
    store.add("CHAI", [a], FP, None)
    r = Recogniser(store, _Table({"1.0": a}))
    got = r.identify(_crop(1.0), FP)
    assert got.reason == REASON_NONPOSITIVE_PRICE
    assert got.abstained and got.price_paise is None


class _ExplodingStore(MemoryStore):
    def price_paise(self, sku_id):
        raise RuntimeError("catalog file vanished")


def test_a_catalog_that_raises_on_lookup_is_amber_not_a_traceback():
    a = _v(1.0)
    store = _ExplodingStore()
    store.add("CHAI", [a], FP, None)
    r = Recogniser(store, _Table({"1.0": a}))
    got = r.identify(_crop(1.0), FP)
    assert got.reason == REASON_NO_PRICE
    assert "RuntimeError" in got.detail


def test_an_embedder_that_raises_abstains_with_embed_failed():
    a = _v(1.0)
    store = MemoryStore()
    store.add("CHAI", [a], FP, 4500)

    def boom(crop):
        raise ValueError("crop was 0 pixels wide")

    r = Recogniser(store, boom)
    got = r.identify(_crop(1.0), FP)
    assert got.reason == REASON_EMBED_FAILED
    assert got.abstained and got.price_paise is None
    assert "ValueError" in got.detail and "0 pixels" in got.detail


def test_an_embedder_of_the_wrong_dimension_abstains_it_does_not_bill():
    a = _v(1.0)
    store = MemoryStore()
    store.add("CHAI", [a], FP, 4500)
    r = Recogniser(store, lambda c: np.ones(DIM + 3))
    got = r.identify(_crop(1.0), FP)
    assert got.reason == REASON_EMBED_FAILED
    assert got.price_paise is None


def test_strict_mode_re_raises_instead_of_hiding_a_wiring_bug():
    a = _v(1.0)
    store = MemoryStore()
    store.add("CHAI", [a], FP, 4500)
    r = Recogniser(store, lambda c: np.ones(DIM + 3), strict=True)
    with pytest.raises(Exception):
        r.identify(_crop(1.0), FP)


def test_every_abstain_reason_in_the_module_is_reachable_and_named():
    """The named list is not decoration: everything the class can emit must be
    in it, or Recognition's own __post_init__ refuses to construct."""
    assert set(ALL_REASONS) == {REASON_MATCH} | set(RECOGNISER_ABSTAIN_REASONS)
    assert len(set(ALL_REASONS)) == len(ALL_REASONS)
    for reason in RECOGNISER_ABSTAIN_REASONS:
        Recognition(None, None, reason, 0.0, 0.0, 0.0, True)


# -- the object cannot lie about money ---------------------------------------

def test_a_recognition_cannot_be_abstained_and_priced_at_the_same_time():
    with pytest.raises(RecogniserError):
        Recognition("CHAI", 4500, REASON_MATCH, 1.0, 0.0, 1.0, True)


def test_a_recognition_cannot_be_decided_with_no_price():
    with pytest.raises(RecogniserError):
        Recognition("CHAI", None, REASON_MATCH, 1.0, 0.0, 1.0, False)


def test_a_price_cannot_ride_along_without_a_sku():
    with pytest.raises(RecogniserError):
        Recognition(None, 4500, REASON_MATCH, 1.0, 0.0, 1.0, False)


def test_a_priced_line_must_carry_the_match_reason():
    with pytest.raises(RecogniserError):
        Recognition("CHAI", 4500, REASON_BELOW_MARGIN, 1.0, 0.0, 1.0, False)


@pytest.mark.parametrize("bad", [0, -1, 45.0, True])
def test_a_recognition_refuses_a_price_that_is_not_positive_integer_paise(bad):
    with pytest.raises(RecogniserError):
        Recognition("CHAI", bad, REASON_MATCH, 1.0, 0.0, 1.0, False)


def test_an_unnamed_abstention_reason_is_refused():
    with pytest.raises(RecogniserError):
        Recognition(None, None, "vibes", 0.0, 0.0, 0.0, True)


def test_a_recognition_audits_to_plain_canonical_json():
    a = _v(1.0)
    r, _ = _wired({"CHAI": a}, {"CHAI": 4500}, {"1.0": a})
    audit = r.identify(_crop(1.0), FP).to_audit()
    round_tripped = json.loads(canonical(audit))
    assert round_tripped["sku_id"] == "CHAI"
    assert round_tripped["price_paise"] == 4500
    assert round_tripped["abstained"] is False


# -- stats -------------------------------------------------------------------

class _OverrideStore(MemoryStore):
    """A catalog whose prices can be anything a hand-edited file could hold."""

    def __init__(self, overrides: dict) -> None:
        super().__init__()
        self.overrides = overrides

    def price_paise(self, sku_id):
        if sku_id in self.overrides:
            return self.overrides[sku_id]
        return super().price_paise(sku_id)


def _drive_every_reason() -> list[Recogniser]:
    """Produce all eleven reasons for real, and hand back the driven objects.

    The vectors are laid out on distinct axes on purpose: e1 is the axis
    `_at_cosine` tilts along and e6 is the axis the below-phi probe tilts
    along, and NO sku sits on either, so a probe aimed at one gate cannot
    accidentally land on a different sku and test the wrong branch. That is not
    fussiness — the first draft of this helper silently exercised
    no_price_for_sku twice and below_similarity never.
    """
    e = [np.eye(DIM)[i] for i in range(DIM)]

    empty = Recogniser(MemoryStore(), lambda c: e[0])
    empty.identify(_crop(1.0), FP)                       # no_gallery

    boom = MemoryStore()
    boom.add("CHAI", [e[0]], FP, 4500)
    exploder = Recogniser(boom, lambda c: (_ for _ in ()).throw(ValueError("x")))
    exploder.identify(_crop(1.0), FP)                    # embed_failed

    store = _OverrideStore({"GALAT": 21.99, "SIFAR": 0})
    store.add("CHAI", [e[0]], FP, 4500)
    store.add("KAAFI", [_at_cosine(e[0], 0.95)], FP, 4000)
    store.add("TWIN", [e[0].copy()], FP + 1.0, 900)      # ties with CHAI
    store.add("MUFT", [e[2]], FP, None)                  # enrolled, never priced
    store.add("GALAT", [e[3]], FP, None)                 # priced 21.99 by the store
    store.add("SIFAR", [e[4]], FP, None)                 # priced 0 by the store
    store.add("SAAF", [e[5]], FP, 2500)                  # the clean match

    crops = {
        "1.0": e[0],                                     # CHAI vs TWIN, tied
        "2.0": 0.5 * e[0] + float(np.sqrt(0.75)) * e[6],  # under phi
        "3.0": _at_cosine(e[0], 0.90),                   # leads KAAFI by < theta
        "4.0": e[2],
        "5.0": e[3],
        "6.0": e[4],
        "7.0": e[5],
    }
    r = Recogniser(store, _Table(crops))
    assert r.identify(_crop(1.0), FP).reason == REASON_AMBIGUOUS
    assert r.identify(_crop(2.0), FP).reason == REASON_BELOW_SIMILARITY
    assert r.identify(_crop(3.0), FP).reason == REASON_BELOW_MARGIN
    assert r.identify(_crop(4.0), FP).reason == REASON_NO_PRICE
    assert r.identify(_crop(5.0), FP).reason == REASON_BAD_PRICE
    assert r.identify(_crop(6.0), FP).reason == REASON_NONPOSITIVE_PRICE
    assert r.identify(_crop(7.0), FP).reason == REASON_MATCH
    assert r.identify(_crop(1.0), 200.0).reason == REASON_NO_CANDIDATE
    assert r.identify(_crop(1.0), None).reason == REASON_NO_FOOTPRINT
    return [empty, exploder, r]


def test_stats_counts_every_reason_and_every_reason_is_reachable():
    """A reason code nobody can produce is documentation, not behaviour."""
    seen = {name: 0 for name in ALL_REASONS}
    for r in _drive_every_reason():
        s = r.stats()
        assert set(s["by_reason"]) == set(ALL_REASONS), (
            "the shape must not depend on what happened"
        )
        for name, count in s["by_reason"].items():
            seen[name] += count
        assert s["n"] == sum(s["by_reason"].values())
        assert s["decided"] + s["abstained"] == s["n"]
        assert s["decided"] == s["by_reason"][REASON_MATCH]
    for name in ALL_REASONS:
        assert seen[name] >= 1, f"{name} was never produced by any code path"


def test_stats_counts_each_reason_once_per_identify_call():
    r = _drive_every_reason()[2]
    by = r.stats()["by_reason"]
    for name in (REASON_MATCH, REASON_AMBIGUOUS, REASON_BELOW_SIMILARITY,
                 REASON_BELOW_MARGIN, REASON_NO_PRICE, REASON_BAD_PRICE,
                 REASON_NONPOSITIVE_PRICE, REASON_NO_CANDIDATE,
                 REASON_NO_FOOTPRINT):
        assert by[name] == 1, f"{name} counted {by[name]} times, expected 1"
    assert r.stats()["n"] == 9


def test_the_abstention_rate_is_published_beside_the_decisions():
    a, b = _v(1.0), _v(0.0, 1.0)
    store = MemoryStore()
    store.add("CHAI", [a], FP, 4500)
    store.add("MUFT", [b], FP, None)
    r = Recogniser(store, _Table({"1.0": a, "2.0": b}))
    for _ in range(3):
        r.identify(_crop(1.0), FP)
    r.identify(_crop(2.0), FP)
    s = r.stats()
    assert (s["n"], s["decided"], s["abstained"]) == (4, 3, 1)
    assert s["abstention_rate"] == pytest.approx(0.25)
    assert r.abstention_rate == pytest.approx(0.25)


def test_stats_publishes_the_gates_the_numbers_were_produced_under():
    a = _v(1.0)
    r, _ = _wired({"CHAI": a}, {"CHAI": 4500}, {"1.0": a})
    s = r.stats()
    assert s["gates"] == {"theta": DEFAULT_THETA, "phi": DEFAULT_PHI,
                          "tau_mm": DEFAULT_TAU_MM}
    assert s["gates_are_default"] is True

    loose, _ = _wired({"CHAI": a}, {"CHAI": 4500}, {"1.0": a}, theta=0.0, phi=0.0)
    assert loose.stats()["gates_are_default"] is False, (
        "a widened gate must be visible next to any accuracy it produced"
    )


def test_an_empty_recogniser_reports_a_zero_abstention_rate_not_a_crash():
    assert Recogniser(MemoryStore(), lambda c: _v(1.0)).stats()["abstention_rate"] == 0.0


def test_reset_stats_starts_a_fresh_run_and_is_never_implicit():
    a = _v(1.0)
    r, store = _wired({"CHAI": a}, {"CHAI": 4500}, {"1.0": a})
    r.identify(_crop(1.0), FP)
    assert r.stats()["n"] == 1
    r.reload()
    assert r.stats()["n"] == 1, "a reload is not a reason to forget the day"
    r.reset_stats()
    assert r.stats()["n"] == 0
    assert set(r.stats()["by_reason"]) == set(ALL_REASONS)


# -- reload ------------------------------------------------------------------

def test_reload_picks_up_a_new_sku_without_reconstructing_the_object():
    a, b = _v(1.0), _v(0.0, 1.0)
    store = MemoryStore()
    store.add("CHAI", [a], FP, 4500)
    r = Recogniser(store, _Table({"1.0": a, "2.0": b}))
    ident_before = id(r)

    assert r.identify(_crop(2.0), FP).reason == REASON_BELOW_SIMILARITY

    store.add("SABUN", [b], FP, 3200)
    assert r.identify(_crop(2.0), FP).reason == REASON_BELOW_SIMILARITY, (
        "an enrolment must not leak into a recogniser that has not reloaded"
    )

    result = r.reload()
    assert id(r) == ident_before
    assert result.added == ("SABUN",) and result.removed == ()
    assert result.n_skus == 2 and result.changed is True

    got = r.identify(_crop(2.0), FP)
    assert (got.sku_id, got.price_paise) == ("SABUN", 3200)
    assert r.skus() == ("CHAI", "SABUN")


def test_reload_notices_a_removal_too():
    a, b = _v(1.0), _v(0.0, 1.0)
    store = MemoryStore()
    store.add("CHAI", [a], FP, 4500)
    store.add("SABUN", [b], FP, 3200)
    r = Recogniser(store, _Table({"1.0": a, "2.0": b}))
    store.remove("SABUN")
    result = r.reload()
    assert result.removed == ("SABUN",) and result.added == ()
    assert r.identify(_crop(2.0), FP).abstained is True


def test_reload_on_an_unchanged_catalog_reports_no_change():
    a = _v(1.0)
    r, _ = _wired({"CHAI": a}, {"CHAI": 4500}, {"1.0": a})
    assert r.reload().changed is False


def test_reload_asks_the_store_to_re_read_itself_first():
    """The enrolment surface and the counter are different processes, so a
    reload that only re-snapshots local memory would never see a new product."""
    calls = []

    class _Deep(MemoryStore):
        def reload(self):
            calls.append(1)

    store = _Deep()
    store.add("CHAI", [_v(1.0)], FP, 4500)
    r = Recogniser(store, _Table({"1.0": _v(1.0)}))
    assert calls == [1], "construction must load through the store's own reader"
    r.reload()
    assert calls == [1, 1]
    r.reload(deep=False)
    assert calls == [1, 1]


def test_a_corrupt_catalog_fails_loudly_on_reload_it_does_not_go_quietly_empty():
    class _Corrupt(MemoryStore):
        def reload(self):
            raise ValueError("catalog.json is not JSON")

    store = _Corrupt()
    with pytest.raises(ValueError):
        Recogniser(store, lambda c: _v(1.0))


# -- construction ------------------------------------------------------------

def test_a_bare_gallery_is_refused_because_it_has_no_prices():
    with pytest.raises(RecogniserError, match="no prices"):
        Recogniser(Gallery(), lambda c: _v(1.0))


def test_a_bare_price_map_is_refused_because_it_has_no_vectors():
    with pytest.raises(RecogniserError, match="no enrolled"):
        Recogniser({"CHAI": 4500}, lambda c: _v(1.0))


def test_something_that_is_not_a_catalog_at_all_says_what_is_missing():
    with pytest.raises(RecogniserError, match="to_gallery"):
        Recogniser(object(), lambda c: _v(1.0))


def test_a_store_that_can_enrol_but_not_price_is_refused_at_construction():
    class _NoPrices:
        def to_gallery(self):
            return Gallery()

    with pytest.raises(RecogniserError, match="price_paise"):
        Recogniser(_NoPrices(), lambda c: _v(1.0))


def test_a_store_that_prices_through_a_price_book_is_accepted():
    """paisa.PriceBook is the shape the money service already speaks."""
    a = _v(1.0)
    inner = MemoryStore()
    inner.add("CHAI", [a], FP, 4500)

    class _ViaBook:
        def to_gallery(self):
            return inner.to_gallery()

        def price_book(self):
            return {"CHAI": 4500}

    r = Recogniser(_ViaBook(), _Table({"1.0": a}))
    assert r.identify(_crop(1.0), FP).price_paise == 4500


def test_a_missing_default_embedder_says_so_in_a_sentence(monkeypatch):
    """No embed_fn and no default embedder is a WIRING error with an
    explanation, not an ImportError traceback. The default is embedder2 now —
    the module this test blocks has to be the one the code actually imports."""
    import builtins

    real = builtins.__import__

    def _no_embedder(name, *a, **kw):
        if name == "gawaah.embedder2":
            raise ImportError("no module named gawaah.embedder2")
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _no_embedder)
    monkeypatch.delitem(__import__("sys").modules, "gawaah.embedder2", raising=False)
    with pytest.raises(RecogniserError, match="injected"):
        Recogniser(MemoryStore())


def test_a_non_callable_embedder_is_refused_at_construction():
    with pytest.raises(RecogniserError, match="callable"):
        Recogniser(MemoryStore(), "gawaah.embedder")


def test_the_memory_store_refuses_a_float_price_at_the_door():
    store = MemoryStore()
    with pytest.raises(MoneyError):
        store.add("CHAI", [_v(1.0)], FP, 44.99)


# -- the basket helpers ------------------------------------------------------

def test_the_total_is_integer_paise_over_decided_lines_only():
    a, b, c = _v(1.0), _v(0.0, 1.0), _v(0.0, 0.0, 1.0)
    store = MemoryStore()
    store.add("CHAI", [a], FP, 4500)
    store.add("SABUN", [b], FP, 3200)
    store.add("MUFT", [c], FP, None)
    r = Recogniser(store, _Table({"1.0": a, "2.0": b, "3.0": c}))
    lines = r.identify_many([(_crop(1.0), FP), (_crop(2.0), FP), (_crop(3.0), FP)])
    got = basket_paise(lines)
    assert got == 7700 and type(got) is int
    assert len(abstentions(lines)) == 1
    assert basket_paise([]) == 0


# ====================================================================== layer 2
# PIXELS. Six products at ONE footprint, so the metric tiebreak cannot help.

PX_PER_MM = 2.8283
MAT_W, MAT_H = 840, 1188
LONG_MM, SHORT_MM = 60.0, 36.0
LONG_PX = int(round(LONG_MM * PX_PER_MM))
SHORT_PX = int(round(SHORT_MM * PX_PER_MM))

#: bg BGR, fg BGR, layout, grain seed, price in integer paise.
#: LAL-SABUN and SAFED-SABUN are the deliberate hard pair: identical size,
#: identical two colours, opposite way round.
PRODUCTS: dict[str, tuple] = {
    "LAL-SABUN":   ((40, 40, 200),   (245, 245, 245), "hstripe", 11, 3200),
    "SAFED-SABUN": ((245, 245, 245), (40, 40, 200),   "hstripe", 12, 3400),
    "HARA-CHAI":   ((60, 150, 60),   (40, 220, 240),  "blob",    13, 4500),
    "NEELA-ATTA":  ((190, 90, 40),   (250, 250, 120), "check",   14, 6250),
    "PILA-DAL":    ((60, 220, 240),  (140, 40, 140),  "vstripe", 15, 8900),
    "BHURA-CHINI": ((70, 110, 150),  (230, 230, 230), "corner",  16, 5500),
}

#: Never enrolled, and genuinely unlike everything that is: black with a
#: magenta vertical stripe. Measured max cosine to any enrolled product is
#: 0.339 (this file's descriptor) and 0.273 (gawaah.embedder), both far under
#: phi = 0.55, so "abstain on an untaught item" is a claim about the RECOGNISER
#: and not a coin flip about the descriptor.
UNTAUGHT = ((20, 20, 20), (255, 0, 255), "vstripe", 91)

#: Never enrolled EITHER, and deliberately confusable: a black-and-yellow
#: checkerboard against NEELA-ATTA's blue-and-yellow checkerboard. This is the
#: open-set failure mode of a classical descriptor and it is measured, not
#: hidden — see the test named after it.
LOOKALIKE = ((20, 20, 20), (200, 200, 20), "check", 77)

ENROL_POSE = (120.0, 150.0, 0.0, 1.0)
#: Six evaluation poses, none of them the enrolment pose: moved across the mat,
#: rotated up to 90 degrees, and relit by -18% to +15%.
EVAL_POSES = [
    (80.0, 210.0, 12.0, 1.0),
    (200.0, 260.0, -17.0, 1.0),
    (150.0, 320.0, 33.0, 0.82),
    (100.0, 120.0, -45.0, 1.15),
    (170.0, 180.0, 90.0, 1.0),
    (60.0, 300.0, 6.0, 0.92),
]


def _panel(bg, fg, layout, seed, w=LONG_PX, h=SHORT_PX):
    img = np.zeros((h, w, 3), np.uint8)
    img[:, :] = bg
    if layout == "hstripe":
        img[h // 3:2 * h // 3, :] = fg
    elif layout == "vstripe":
        img[:, w // 3:2 * w // 3] = fg
    elif layout == "blob":
        cv2.circle(img, (w // 2, h // 2), min(w, h) // 3, fg, -1)
    elif layout == "check":
        s = max(4, min(w, h) // 6)
        for y in range(0, h, s):
            for x in range(0, w, s):
                if ((x // s) + (y // s)) % 2 == 0:
                    img[y:y + s, x:x + s] = fg
    elif layout == "corner":
        img[:h // 2, :w // 2] = fg
    # Printing grain, seeded: the same product is the same object every time,
    # so a failure is a descriptor failure and never a dice roll.
    grain = np.random.default_rng(seed).integers(-9, 10, size=(h, w, 1),
                                                 dtype=np.int16)
    return np.clip(img.astype(np.int16) + grain, 0, 255).astype(np.uint8)


def _scene(spec, x_mm, y_mm, angle_deg, light=1.0):
    """Paste the product onto a mat at a pose, then pull the ORIENTED crop back
    out through the same rotate-then-getRectSubPix round trip Brain._crop does.
    Two warps of resampling loss, so no evaluation crop is ever the enrolment
    crop's pixels."""
    mat = np.full((MAT_H, MAT_W, 3), 235, np.uint8)
    p = _panel(*spec[:4])
    h, w = p.shape[:2]
    cx, cy = x_mm * PX_PER_MM, y_mm * PX_PER_MM
    m = cv2.getRotationMatrix2D((w / 2, h / 2), -angle_deg, 1.0)
    m[0, 2] += cx - w / 2
    m[1, 2] += cy - h / 2
    cv2.warpAffine(p, m, (MAT_W, MAT_H), dst=mat, flags=cv2.INTER_LINEAR,
                   borderMode=cv2.BORDER_TRANSPARENT)
    if light != 1.0:
        mat = np.clip(mat.astype(np.float64) * light, 0, 255).astype(np.uint8)
    rm = cv2.getRotationMatrix2D((cx, cy), angle_deg, 1.0)
    rot = cv2.warpAffine(mat, rm, (MAT_W, MAT_H), flags=cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_REPLICATE)
    return cv2.getRectSubPix(rot, (w, h), (cx, cy))


CANON = 48


def local_descriptor(crop) -> np.ndarray:
    """A model-free descriptor built only from cv2 primitives that ship.

    Lives in the TEST rather than the package on purpose: this file must be
    able to prove the Recogniser works even if gawaah.embedder is absent,
    changed, or being rewritten in the next room. When gawaah.embedder IS
    importable it is parametrised in beside this one and both must pass.

    Each block is mean-centred and unit-normed BEFORE concatenation. Without
    that the near-constant DC of every block dominates and two unrelated
    products sit at cosine 0.98 -- measured, on this very product set.
    """
    a = np.asarray(crop)
    bgr = (cv2.cvtColor(a.astype(np.uint8), cv2.COLOR_GRAY2BGR)
           if a.ndim == 2 else a.astype(np.uint8))
    h, w = bgr.shape[:2]
    if h < 2 or w < 2:
        raise ValueError(f"crop too small to describe: {bgr.shape}")
    if h > w:                       # canonicalise: long edge horizontal
        bgr = cv2.rotate(bgr, cv2.ROTATE_90_CLOCKWISE)
        h, w = bgr.shape[:2]
    sq = cv2.resize(bgr, (CANON, CANON), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(sq, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(sq, cv2.COLOR_BGR2GRAY).astype(np.float64)
    parts = []

    for ch, bins, rng in ((0, 12, [0, 180]), (1, 8, [0, 256])):
        hist = cv2.calcHist([hsv], [ch], None, [bins], rng).ravel()
        s = hist.sum()
        parts.append(hist / s if s else hist)

    grid, step = [], CANON // 3     # 3x3 mean HSV: WHERE the colour is
    for gy in range(3):
        for gx in range(3):
            cell = hsv[gy * step:(gy + 1) * step, gx * step:(gx + 1) * step]
            grid.extend(cell.reshape(-1, 3).mean(axis=0) / 255.0)
    parts.append(np.asarray(grid))

    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    oh, _ = np.histogram(np.rad2deg(np.arctan2(gy, gx)) % 180.0,
                         bins=12, range=(0.0, 180.0), weights=np.hypot(gx, gy))
    s = oh.sum()
    parts.append(oh / s if s else oh)

    lay, step4 = [], CANON // 4
    for gy4 in range(4):
        for gx4 in range(4):
            cell = gray[gy4 * step4:(gy4 + 1) * step4, gx4 * step4:(gx4 + 1) * step4]
            lay.append(cell.mean() / 255.0)
            lay.append(cell.std() / 128.0)
    parts.append(np.asarray(lay))
    parts.append(np.asarray([min(h, w) / max(h, w)]))

    blocks = []
    for p in parts:
        b = np.asarray(p, dtype=np.float64).ravel()
        b = b - b.mean()
        n = np.linalg.norm(b)
        blocks.append(b / n if n else b)
    v = np.concatenate(blocks)
    n = np.linalg.norm(v)
    return (v / n if n else v).astype(np.float64)


def _embedders():
    """`local_descriptor` always; `gawaah.embedder.embed` when it is usable.

    Usable is PROVED, not assumed: the module is imported AND called on a real
    crop at collection time. This file owns neither that module nor its
    quality, so if it is missing or mid-rewrite the parametrisation is SKIPPED
    with the exact exception in the reason — visible under `-rs`, never a
    silent pass and never a red bar for a fault in somebody else's file.
    """
    fns = [pytest.param(local_descriptor, id="local")]
    try:
        from gawaah.embedder import embed
    except Exception as exc:                            # pragma: no cover
        return fns + [pytest.param(
            None, id="gawaah.embedder",
            marks=pytest.mark.skip(reason=f"gawaah.embedder will not import: {exc!r}"),
        )]
    try:
        probe = embed(np.full((36, 60, 3), 128, np.uint8))
        assert np.asarray(probe).ndim == 1
    except Exception as exc:                            # pragma: no cover
        return fns + [pytest.param(
            embed, id="gawaah.embedder",
            marks=pytest.mark.skip(
                reason=f"gawaah.embedder raises on a plain crop: {exc!r}"),
        )]
    fns.append(pytest.param(embed, id="gawaah.embedder"))
    return fns


EMBEDDERS = _embedders()


def _taught(embed_fn) -> tuple[Recogniser, MemoryStore]:
    store = MemoryStore()
    for sku, spec in PRODUCTS.items():
        store.add(sku, [embed_fn(_scene(spec, *ENROL_POSE))], LONG_MM, spec[4])
    return Recogniser(store, embed_fn), store


def test_the_descriptor_used_by_this_file_is_deterministic():
    crop = _scene(PRODUCTS["HARA-CHAI"], *ENROL_POSE)
    a, b = local_descriptor(crop), local_descriptor(crop.copy())
    assert a.tobytes() == b.tobytes()


def test_the_product_set_really_does_share_one_footprint():
    """If the sizes differed the metric tiebreak would answer for free and
    layer 2 would prove nothing about appearance."""
    store = MemoryStore()
    for sku, spec in PRODUCTS.items():
        store.add(sku, [local_descriptor(_scene(spec, *ENROL_POSE))], LONG_MM,
                  spec[4])
    g = store.to_gallery()
    assert len({g.footprint(s) for s in g.skus()}) == 1


def test_an_evaluation_crop_is_never_the_enrolment_crop():
    spec = PRODUCTS["NEELA-ATTA"]
    enrolled = _scene(spec, *ENROL_POSE)
    for pose in EVAL_POSES:
        assert not np.array_equal(enrolled, _scene(spec, *pose))


@pytest.mark.parametrize("embed_fn", EMBEDDERS)
def test_a_taught_product_is_recognised_across_position_and_rotation(embed_fn):
    r, _ = _taught(embed_fn)
    right = wrong = 0
    held: list[str] = []
    for sku, spec in PRODUCTS.items():
        for pose in EVAL_POSES:
            got = r.identify(_scene(spec, *pose), LONG_MM)
            if got.abstained:
                held.append(f"{sku}@{pose[2]}deg:{got.reason}")
            elif got.sku_id == sku:
                right += 1
                assert got.price_paise == spec[4]
            else:
                wrong += 1

    n = len(PRODUCTS) * len(EVAL_POSES)
    report = (f"{right}/{n} correct, {wrong} FALSE PRICES, "
              f"{len(held)} abstained {held}")
    assert wrong == 0, "a confidently wrong price is the error that costs money: " + report
    assert right >= 33, report
    if embed_fn is local_descriptor:
        # WAS `assert right == n` -- perfect recall. That is the wrong property
        # to demand of this system, and raising DEFAULT_PHI to 0.90 exposed it:
        # NEELA-ATTA at -45 degrees self-matches at 0.889 and now ABSTAINS.
        # The run was "35/36 correct, 0 FALSE PRICES, 1 abstained", which is the
        # doctrine working, not a regression. INVARIANT 7 ranks a wrong price
        # far above a missed one, so the assertion that must never soften is
        # `wrong == 0` above; recall gets a floor instead of a ceiling.
        # See gawaah/identity.py DEFAULT_PHI for the measured trade
        # (untaught-product false-price 61.1% -> 0.0% for +6pp abstention).
        assert right >= n - 1, report
    assert r.stats()["decided"] == right


@pytest.mark.parametrize("embed_fn", EMBEDDERS)
def test_an_untaught_product_abstains_with_a_named_reason(embed_fn):
    r, _ = _taught(embed_fn)
    for pose in EVAL_POSES:
        got = r.identify(_scene(UNTAUGHT, *pose), LONG_MM)
        assert got.abstained is True, f"an untaught item was priced {got}"
        assert got.price_paise is None
        assert got.reason in RECOGNISER_ABSTAIN_REASONS
    assert r.stats()["by_reason"][REASON_MATCH] == 0


@pytest.mark.parametrize("embed_fn", EMBEDDERS)
def test_an_item_of_an_unenrolled_size_abstains_on_the_metric_gate_first(embed_fn):
    """The mat is consulted before appearance, so a 100 mm packet is refused
    even when it looks exactly like an enrolled 60 mm one."""
    r, _ = _taught(embed_fn)
    got = r.identify(_scene(PRODUCTS["HARA-CHAI"], *EVAL_POSES[0]),
                     LONG_MM + DEFAULT_TAU_MM + 1.0)
    assert got.reason == REASON_NO_CANDIDATE and got.abstained


@pytest.mark.parametrize("embed_fn", EMBEDDERS)
def test_a_basket_of_taught_and_untaught_items_totals_only_what_it_knows(embed_fn):
    r, _ = _taught(embed_fn)
    lines = r.identify_many([
        (_scene(PRODUCTS["HARA-CHAI"], *EVAL_POSES[1]), LONG_MM),
        (_scene(UNTAUGHT, *EVAL_POSES[2]), LONG_MM),
        (_scene(PRODUCTS["PILA-DAL"], *EVAL_POSES[3]), LONG_MM),
    ])
    assert basket_paise(lines) == 4500 + 8900
    amber = abstentions(lines)
    assert len(amber) == 1 and amber[0].price_paise is None


@pytest.mark.parametrize("embed_fn", EMBEDDERS)
def test_a_lookalike_untaught_item_is_the_open_set_limit_and_phi_is_the_lever(
    embed_fn,
):
    """The honest one.

    Open-set rejection is where a classical descriptor is weakest: a NOVEL
    product that shares a layout with an enrolled one can clear both gates and
    be sold as it. Measured on this set at the default gates, LOOKALIKE (black
    and yellow check) is scored against NEELA-ATTA (blue and yellow check) at
    top1 = 0.827 by gawaah.embedder — a confident, wrong price — and at 0.254
    by this file's descriptor, which abstains.

    This test does NOT paper over that by widening or narrowing a gate to make
    a number look good. It asserts the two things that are actually the
    Recogniser's promises:

      1. whatever it decides, the evidence is on the record — the score, the
         runner-up and the margin come back with the answer, so the mistake is
         auditable rather than mysterious; and
      2. phi is a real lever: raised above the impostor's score, every one of
         these turns into a NAMED abstention, and no taught product is
         mis-priced at the tighter gate either. The cost of the tighter gate is
         paid in abstentions, which is the trade invariant 7 asks for.
    """
    r, store = _taught(embed_fn)
    scores = []
    for pose in EVAL_POSES:
        got = r.identify(_scene(LOOKALIKE, *pose), LONG_MM)
        assert got.top1_sku is not None and -1.0 <= got.top1 <= 1.0
        assert got.margin == pytest.approx(got.top1 - got.top2, abs=1e-9)
        assert got.n_candidates == len(PRODUCTS)
        scores.append(got.top1)
    worst = max(scores)

    tight = Recogniser(store, embed_fn, phi=worst + 0.01)
    assert tight.stats()["gates_are_default"] is False, (
        "a moved gate must be visible beside anything it produced"
    )
    for pose in EVAL_POSES:
        got = tight.identify(_scene(LOOKALIKE, *pose), LONG_MM)
        assert got.reason == REASON_BELOW_SIMILARITY
        assert got.abstained and got.price_paise is None

    kept = wrong = 0
    for sku, spec in PRODUCTS.items():
        for pose in EVAL_POSES:
            got = tight.identify(_scene(spec, *pose), LONG_MM)
            if got.sku_id == sku and not got.abstained:
                kept += 1
            elif not got.abstained:
                wrong += 1
    n = len(PRODUCTS) * len(EVAL_POSES)
    assert wrong == 0, f"tightening phi to {worst + 0.01:.3f} mis-priced {wrong}"
    assert kept >= 1, (
        f"phi={worst + 0.01:.3f} rejects the impostor but keeps only {kept}/{n} "
        "of the taught set — at that point the gate has eaten the product"
    )


@pytest.mark.parametrize("embed_fn", EMBEDDERS)
def test_the_hard_pair_is_either_separated_or_abstained_never_swapped(embed_fn):
    """LAL-SABUN and SAFED-SABUN are the same size and the same two colours the
    other way round. Getting them right is a bonus; swapping them is the one
    outcome that is not allowed."""
    r, _ = _taught(embed_fn)
    for sku in ("LAL-SABUN", "SAFED-SABUN"):
        other = "SAFED-SABUN" if sku == "LAL-SABUN" else "LAL-SABUN"
        for pose in EVAL_POSES:
            got = r.identify(_scene(PRODUCTS[sku], *pose), LONG_MM)
            assert got.sku_id != other or got.abstained, (
                f"{sku} was billed as {other} at {got.price_paise} paise"
            )


@pytest.mark.parametrize("embed_fn", EMBEDDERS)
def test_reload_teaches_the_running_counter_a_new_product(embed_fn):
    """The demonstration, end to end: it does not know this thing, you teach
    it, and without a restart it prices it."""
    r, store = _taught(embed_fn)
    crop = _scene(UNTAUGHT, *EVAL_POSES[0])

    before = r.identify(crop, LONG_MM)
    assert before.abstained and before.price_paise is None

    store.add("NAYA-MASALA", [embed_fn(_scene(UNTAUGHT, *ENROL_POSE))],
              LONG_MM, 1999)
    assert r.reload().added == ("NAYA-MASALA",)

    after = r.identify(crop, LONG_MM)
    assert (after.sku_id, after.price_paise) == ("NAYA-MASALA", 1999)
    assert after.abstained is False


def test_same_and_different_product_cosines_do_not_overlap():
    """The number that decides whether any of this can work at all."""
    enrolled = {s: local_descriptor(_scene(spec, *ENROL_POSE))
                for s, spec in PRODUCTS.items()}
    same, diff = [], []
    for sku, spec in PRODUCTS.items():
        for pose in EVAL_POSES:
            v = local_descriptor(_scene(spec, *pose))
            same.append(float(np.dot(enrolled[sku], v)))
            diff.extend(float(np.dot(enrolled[o], v))
                        for o in PRODUCTS if o != sku)
    lo, hi = min(same), max(diff)
    assert lo > hi, f"same-min {lo:.4f} <= diff-max {hi:.4f}: not separable"
    assert lo - hi >= DEFAULT_THETA, (
        f"separation {lo - hi:.4f} is under theta {DEFAULT_THETA}"
    )
    # NOT `lo >= DEFAULT_PHI`. That asserted every same-product view clears the
    # gate, which conflates two different claims: "the distributions separate"
    # (what this test is for, asserted above) and "the gate admits every view"
    # (a recall target). At phi=0.90 the hardest same-product view sits at
    # 0.8894 and abstains -- correctly. Keep the separation assertions, and
    # record where the gate falls relative to the same-product minimum instead
    # of requiring it to sit underneath.
    assert lo > hi, "separation is the claim under test"
    if lo < DEFAULT_PHI:
        print(f"    note: hardest same-product view {lo:.4f} is under "
              f"phi={DEFAULT_PHI} and will abstain -- by design, not a fault")


# ====================================================================== layer 3
# THE REAL PARTS.

def test_the_real_shop_store_drives_the_recogniser_end_to_end(tmp_path):
    shop_store = pytest.importorskip("gawaah.shop_store")
    embed_fn = local_descriptor
    store = shop_store.ShopStore(tmp_path / "shop")
    for sku, spec in PRODUCTS.items():
        store.add_sku(sku, sku.title(), spec[4],
                      [embed_fn(_scene(spec, *ENROL_POSE))], LONG_MM)

    r = Recogniser(store, embed_fn)
    assert r.n_skus == len(PRODUCTS)

    right = 0
    for sku, spec in PRODUCTS.items():
        got = r.identify(_scene(spec, *EVAL_POSES[0]), LONG_MM)
        assert got.sku_id in (sku, None), f"{sku} was billed as {got.sku_id}"
        if got.sku_id == sku:
            assert got.price_paise == spec[4]
            right += 1
    assert right == len(PRODUCTS)

    # A second process enrols; this recogniser picks it up on reload().
    other = shop_store.ShopStore(tmp_path / "shop")
    other.add_sku("NAYA-MASALA", "Naya Masala", 1999,
                  [embed_fn(_scene(UNTAUGHT, *ENROL_POSE))], LONG_MM)
    assert r.identify(_scene(UNTAUGHT, *EVAL_POSES[1]), LONG_MM).abstained
    assert r.reload().added == ("NAYA-MASALA",)
    got = r.identify(_scene(UNTAUGHT, *EVAL_POSES[1]), LONG_MM)
    assert (got.sku_id, got.price_paise) == ("NAYA-MASALA", 1999)


class _SimStore:
    """A store that is not MemoryStore and not ShopStore, to prove the protocol
    is a protocol. Prices come from the sim script, which leaves UNKNOWN-ITEM
    deliberately unpriced."""

    def __init__(self, gallery, prices):
        self._g, self._p = gallery, dict(prices)

    def to_gallery(self):
        return Gallery.from_dict(self._g.to_dict())

    def price_paise(self, sku_id):
        return self._p.get(sku_id)


def test_the_sim_unknown_item_is_unpriced_and_therefore_amber():
    """UNKNOWN-ITEM is in the sim script with price_paise=None. Even if it were
    somehow enrolled, an unpriced sku is an amber line, never a zero one."""
    sim_source = pytest.importorskip("gawaah.sim_source")
    from gawaah.brain import Brain

    sim = sim_source.SimSource(seed=20260829)
    gallery = Gallery()
    prices = sim.enrol_gallery(gallery, local_descriptor, Brain._crop)
    assert sim.unknown_sku.price_paise is None
    assert sim.unknown_sku.sku_id not in prices

    # Enrol it anyway, with no price, and watch the counter refuse to bill it.
    buf = sim.enrolment_frame("CHAI-250")
    gallery.enroll("UNKNOWN-ITEM",
                   [local_descriptor(np.full((40, 70, 3), 90, np.uint8))],
                   sim.unknown_sku.long_mm)
    r = Recogniser(_SimStore(gallery, prices), local_descriptor)
    got = r.identify(np.full((40, 70, 3), 90, np.uint8), sim.unknown_sku.long_mm)
    assert got.sku_id == "UNKNOWN-ITEM"
    assert got.reason == REASON_NO_PRICE
    assert got.price_paise is None and got.abstained is True
    assert basket_paise([got]) == 0
    assert buf is not None


def test_the_default_embedder_is_resolved_lazily_and_never_downloaded():
    """The default embed_fn is the repo's own embedder2, resolved at
    construction and never fetched from anywhere at request time. The gallery
    for this test must be built with THE SAME embedder the identify path will
    resolve — a gallery written by one embedder is noise to another, which is
    the entire reason tools/migrate_gallery.py exists."""
    embedder2 = pytest.importorskip("gawaah.embedder2")
    if not embedder2.MODEL_PATH.is_file():
        pytest.skip("model weights not on this checkout")
    store = MemoryStore()
    spec = PRODUCTS["HARA-CHAI"]
    store.add("HARA-CHAI", [embedder2.embed(_scene(spec, *ENROL_POSE))],
              LONG_MM, spec[4])
    r = Recogniser(store)                       # no embed_fn: resolve the default
    got = r.identify(_scene(spec, *EVAL_POSES[0]), LONG_MM)
    assert (got.sku_id, got.price_paise) == ("HARA-CHAI", spec[4])


def _module_imports() -> set[str]:
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parent.parent / "gawaah" / "recogniser.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_module_imports_no_model_and_touches_no_network():
    """Invariant 3, checked in the source rather than in a promise."""
    tops = {n.split(".")[0] for n in _module_imports()}
    banned = {"torch", "onnx", "onnxruntime", "tensorflow", "transformers",
              "open_clip", "requests", "urllib", "httpx", "socket", "aiohttp",
              "urllib3"}
    assert not (tops & banned), sorted(tops & banned)


def test_recognition_cannot_reach_the_money_path_at_all():
    """Invariant 2: GREEN comes only from a signature-verified webhook, so this
    module must not be able to mint, settle or sign anything. It imports the
    money TYPE and nothing that can move money."""
    mods = _module_imports()
    forbidden = {"gawaah.paisa", "gawaah.kernel", "gawaah.webhook",
                 "gawaah.rzp_live", "gawaah.rzp_sim", "gawaah.session",
                 "gawaah.brain"}
    assert not (mods & forbidden), sorted(mods & forbidden)
    assert "gawaah.money" in mods, "prices must still cross money.paise()"

    import gawaah.recogniser as mod
    surface = {n for n in dir(mod) if not n.startswith("_")}
    for verb in ("mint", "settle", "pay", "charge", "webhook", "gateway"):
        assert not any(verb in n.lower() for n in surface), verb


def test_the_same_crop_twice_gives_the_same_verdict():
    r, _ = _taught(local_descriptor)
    crop = _scene(PRODUCTS["NEELA-ATTA"], *EVAL_POSES[2])
    a = r.identify(crop, LONG_MM)
    b = r.identify(crop.copy(), LONG_MM)
    assert a.to_audit() == b.to_audit()


def test_identify_never_mutates_the_catalog():
    a = _v(1.0)
    store = MemoryStore()
    store.add("CHAI", [a], FP, 4500)
    before = canonical(store.to_gallery().to_dict())
    r = Recogniser(store, _Table({"1.0": a}))
    for _ in range(5):
        r.identify(_crop(1.0), FP)
    assert canonical(store.to_gallery().to_dict()) == before


def test_a_reload_during_a_sale_swaps_whole_catalogs_never_half_of_one():
    """The swap is a single attribute write, so a concurrent identify() sees
    either the old catalog entire or the new one entire."""
    import threading

    a, b = _v(1.0), _v(0.0, 1.0)
    store = MemoryStore()
    store.add("CHAI", [a], FP, 4500)
    r = Recogniser(store, _Table({"1.0": a, "2.0": b}))

    # Every newcomer lives in the subspace ORTHOGONAL to CHAI, so no enrolment
    # in this test can legitimately change CHAI's verdict. Any wobble would be
    # a torn read, not a real re-ranking.
    rng = np.random.default_rng(5)

    def _orthogonal_to_chai() -> np.ndarray:
        v = np.zeros(DIM, dtype=np.float64)
        v[1:] = rng.standard_normal(DIM - 1)
        return v / np.linalg.norm(v)

    seen: list[Recognition] = []
    errors: list[BaseException] = []
    stop = threading.Event()

    def hammer():
        try:
            while not stop.is_set():
                seen.append(r.identify(_crop(1.0), FP))
                time.sleep(0.0005)      # bounded: this test is about the swap,
        except BaseException as exc:    # not about throughput
            errors.append(exc)          # pragma: no cover

    t = threading.Thread(target=hammer)
    t.start()
    try:
        for i in range(20):
            store.add(f"NEW-{i}", [_orthogonal_to_chai()], FP, 100 + i)
            r.reload()
            waited = len(seen)
            while len(seen) == waited and not errors:     # observe THIS catalog
                time.sleep(0.001)
    finally:
        stop.set()
        t.join(timeout=10)

    assert not errors, errors
    assert len(seen) >= 20, f"the hammer thread only got {len(seen)} results"
    for got in seen:
        assert (got.sku_id, got.price_paise, got.reason) == ("CHAI", 4500,
                                                             REASON_MATCH)
    assert len({got.n_skus for got in seen}) >= 2, (
        "the reloads never landed during a query, so nothing was raced"
    )
    assert r.stats()["n"] == len(seen)
    assert r.n_skus == 21


def test_the_thresholds_are_handed_through_unchanged():
    """Nobody widens a gate on the way past. If a caller does, stats() says so."""
    a = _v(1.0)
    store = MemoryStore()
    store.add("CHAI", [a], FP, 4500)
    r = Recogniser(store, _Table({"1.0": a}), theta=0.2, phi=0.7, tau_mm=1.5)
    assert (r.identifier.theta, r.identifier.phi, r.identifier.tau_mm) == (0.2, 0.7, 1.5)
    r.reload()
    assert (r.identifier.theta, r.identifier.phi, r.identifier.tau_mm) == (0.2, 0.7, 1.5)
    assert r.stats()["gates_are_default"] is False


def test_no_pair_of_reasons_is_the_same_string_by_accident():
    names = list(ALL_REASONS)
    assert len(names) == len(set(names))
    for x, y in itertools.combinations(names, 2):
        assert x != y
