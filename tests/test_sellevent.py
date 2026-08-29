"""S3b acceptance: the deterministic sell event.

The money question this file answers is not "did the CV work" but "when the CV
did NOT work, did anyone find out". Every uncounted crossing must arrive as an
object with a name on it.
"""
from __future__ import annotations

import random
import time
from collections import deque

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from gawaah.sellevent import (
    DEAD_BAND_MM, REASON_NEVER_COUNTED, REASON_NO_TRACKER_ID,
    REASON_REID_AMBIGUOUS, REASON_REID_GAP_EXCEEDED, REID_REASONS,
    SIDE_IN, SIDE_ON_LINE, SIDE_OUT,
    AbstainedCentroid, CentroidTracker, CrossingResult, LineZone,
    UncountedCrossing,
)

LINE_Y = 400.0


def zone(**kw) -> LineZone:
    """Full-width sell line across the mat; OUT is the larger-y (customer) side."""
    return LineZone((0.0, LINE_Y), (297.0, LINE_Y), **kw)


def run(z: LineZone, ys, x: float = 150.0, tid: int = 1) -> list[CrossingResult]:
    return [z.update({tid: (x, y)}) for y in ys]


# --------------------------------------------------------------- geometry

def test_out_is_the_far_edge_side():
    z = zone()
    assert z.signed_distance_mm((150.0, 410.0)) == pytest.approx(10.0)
    assert z.signed_distance_mm((150.0, 390.0)) == pytest.approx(-10.0)
    assert z.side((150.0, 410.0)) == SIDE_OUT
    assert z.side((150.0, 390.0)) == SIDE_IN
    assert z.side((150.0, LINE_Y + DEAD_BAND_MM / 2)) == SIDE_ON_LINE


def test_mat_exit_line_points_at_the_customer():
    from gawaah.takhti import MAT_H_MM, MAT_W_MM
    z = LineZone.mat_exit_line()
    # the mat's far edge is +y, so anything nearer the bottom edge is OUT
    assert z.side((MAT_W_MM / 2, MAT_H_MM - 2.0)) == SIDE_OUT
    assert z.side((MAT_W_MM / 2, MAT_H_MM / 2)) == SIDE_IN


def test_limits_exclude_centroids_beside_the_segment():
    z = zone()
    assert z.in_limits((150.0, 410.0))
    assert not z.in_limits((350.0, 410.0))
    assert not z.in_limits((-5.0, 410.0))


def test_degenerate_line_is_refused():
    with pytest.raises(ValueError):
        LineZone((10.0, 10.0), (10.0, 10.0))
    with pytest.raises(ValueError):
        zone(min_crossing_frames=0)


# ------------------------------------------------- the four scripted paths

def test_scripted_out_crossing_is_counted_exactly_once():
    z = zone(min_crossing_frames=3)
    ys = [380.0] * 4 + [420.0] * 12          # in, then held far out
    res = run(z, ys)
    assert z.out_count == 1, [r.crossed_out for r in res]
    assert z.net_count == 1
    assert z.back_count == 0
    assert not z.exceptions
    assert not z.amber
    fired = [r.frame_index for r in res if r.crossed_out]
    assert fired == [6], f"expected commit on the 3rd out frame, got {fired}"


def test_crossing_needs_min_crossing_frames_not_one():
    """Two frames past the line is a hand wobble, not a sale."""
    z = zone(min_crossing_frames=3)
    run(z, [380.0] * 4 + [420.0] * 2)
    assert z.out_count == 0
    z2 = zone(min_crossing_frames=3)
    run(z2, [380.0] * 4 + [420.0] * 3)
    assert z2.out_count == 1


def test_out_then_back_nets_zero():
    z = zone(min_crossing_frames=3)
    run(z, [380.0] * 4 + [420.0] * 4 + [380.0] * 4)
    assert z.out_count == 1
    assert z.back_count == 1
    assert z.net_count == 0
    assert not z.amber


def test_out_back_out_nets_one():
    z = zone(min_crossing_frames=3)
    run(z, [380.0] * 4 + [420.0] * 4 + [380.0] * 4 + [420.0] * 4)
    assert (z.out_count, z.back_count, z.net_count) == (2, 1, 1)


def test_wrong_way_crossing_never_counts():
    """An item arriving FROM the customer's side is not a negative sale."""
    z = zone(min_crossing_frames=3)
    run(z, [420.0] * 4 + [380.0] * 6)
    assert z.out_count == 0
    assert z.back_count == 0
    assert z.net_count == 0
    assert z.entries_from_out == 1
    assert not z.amber


def test_net_count_can_never_go_negative():
    z = zone(min_crossing_frames=3)
    for _ in range(4):
        run(z, [420.0] * 5 + [380.0] * 5)
    assert z.net_count == 0
    assert z.net_count >= 0


# ------------------------------------------------------------- the debounce

def test_jitter_exactly_on_the_line_never_counts():
    """A centroid oscillating across the line, WELL outside the dead band so
    the dead band is not what saves us, must produce no counts at all."""
    z = zone(min_crossing_frames=3)
    amp = 3.0
    assert amp > DEAD_BAND_MM
    ys = [LINE_Y + (amp if i % 2 else -amp) for i in range(40)]
    run(z, ys)
    assert (z.out_count, z.back_count, z.net_count) == (0, 0, 0)
    assert not z.exceptions


def test_sub_noise_drift_across_the_line_is_not_a_sale():
    """Without a dead band, 0.6 mm of contour-centroid noise — a fifth of a
    pixel of drift in the rectified buffer — commits a full crossing and
    charges the customer. The band is what makes that impossible."""
    z = zone(min_crossing_frames=3)
    run(z, [LINE_Y - 0.3] * 5 + [LINE_Y + 0.3] * 8)
    assert z.out_count == 0
    assert z.net_count == 0
    # and the same path with a real 5 mm push does count
    z2 = zone(min_crossing_frames=3)
    run(z2, [LINE_Y - 5.0] * 5 + [LINE_Y + 5.0] * 8)
    assert z2.out_count == 1


def test_centroid_parked_in_the_dead_band_never_counts():
    z = zone(min_crossing_frames=3)
    ys = [380.0] * 4 + [LINE_Y + 0.2 * (1 if i % 2 else -1) for i in range(40)]
    run(z, ys)
    assert z.out_count == 0


def test_wobble_after_a_crossing_does_not_recount_UPSTREAM_BUG():
    """The upstream deque predicate double-counts one physical crossing.

    supervision's LineZone counts whenever the oldest entry of its history is
    unique. After a committed crossing, the sequence T,T,T,F,T,T,T walks the
    window back through [F,T,T,T] and fires a SECOND out-count for the same
    item. We additionally require the committed side to change. This test
    asserts both halves: that upstream's predicate really would fire twice on
    this sequence, and that ours fires once.
    """
    ys = [380.0] * 4 + [420.0] * 5 + [380.0] + [420.0] * 6
    z = zone(min_crossing_frames=3)
    run(z, ys)
    assert z.out_count == 1, "one physical crossing must count once"
    assert z.back_count == 0
    assert z.net_count == 1

    # replay the identical boolean sequence through upstream's predicate
    hist: deque[bool] = deque(maxlen=max(2, 3 + 1))
    upstream_out = 0
    for y in ys:
        hist.append(y > LINE_Y)
        if len(hist) < hist.maxlen:
            continue
        oldest = hist[0]
        if hist.count(oldest) > 1:
            continue
        if hist[-1]:
            upstream_out += 1
    assert upstream_out == 2, (
        "if this ever equals 1, upstream fixed the double-count and the "
        f"settled-side guard can be revisited (got {upstream_out})"
    )


# ------------------------- (b) detected_but_never_counted: the dropped track

def test_track_vanishing_mid_cross_raises_detected_but_never_counted():
    z = zone(min_crossing_frames=3)
    run(z, [380.0] * 4 + [420.0] * 2)        # 2 of the 3 frames needed
    assert z.out_count == 0
    assert not z.amber

    res = None
    for _ in range(z.evict_after_frames):
        res = z.update({})                    # the track is simply gone
    assert res is not None
    assert res.exceptions, "a track dropped mid-crossing must not vanish silently"
    (exc,) = res.exceptions
    assert exc.code == REASON_NEVER_COUNTED
    assert exc.track_id == 1
    assert "mid-crossing" in exc.detail
    assert z.detected_but_never_counted == 1
    assert z.amber is True
    assert res.total_is_trustworthy is False
    assert z.out_count == 0, "it must NOT be counted; it must be flagged"


def test_lost_id_from_the_tracker_retires_immediately():
    z = zone(min_crossing_frames=3)
    run(z, [380.0] * 4 + [420.0] * 2)
    res = z.update({}, lost=[1])
    assert res.exceptions and res.exceptions[0].code == REASON_NEVER_COUNTED
    assert res.frame_index == 6


def test_track_dropped_mid_RETURN_also_fires():
    """A decrement that never happened is just as expensive as a sale that
    never happened. The item was counted OUT, started coming back, and died."""
    z = zone(min_crossing_frames=3)
    run(z, [380.0] * 4 + [420.0] * 4 + [380.0] * 2)
    assert z.out_count == 1 and z.back_count == 0
    res = z.update({}, lost=[1])
    assert res.exceptions[0].code == REASON_NEVER_COUNTED
    assert "back inside" in res.exceptions[0].detail
    assert z.net_count == 1 and z.amber


def test_completed_crossing_then_vanishing_is_clean():
    """The normal sale: item crosses, is committed, then leaves the mat."""
    z = zone(min_crossing_frames=3)
    run(z, [380.0] * 4 + [420.0] * 5)
    for _ in range(10):
        z.update({})
    assert z.out_count == 1
    assert z.detected_but_never_counted == 0
    assert not z.amber


def test_track_that_lived_and_died_on_one_side_does_not_fire():
    """HONEST LIMIT, pinned executably: an item put on the mat and picked back
    up never crossed. Firing here would leave the banner permanently amber for
    ordinary restocking, which is worse than the silence we are fixing."""
    z = zone(min_crossing_frames=3)
    run(z, [380.0, 370.0, 360.0, 350.0, 340.0])
    for _ in range(10):
        z.update({})
    assert z.detected_but_never_counted == 0
    assert z.vanished_same_side == 1
    assert not z.amber


def test_flush_judges_tracks_still_live_at_session_end():
    z = zone(min_crossing_frames=3)
    run(z, [380.0] * 4 + [420.0] * 2)
    res = z.flush()
    assert res.exceptions[0].code == REASON_NEVER_COUNTED
    assert z.detected_but_never_counted == 1


def test_one_frame_detection_gap_does_not_reset_a_crossing():
    z = zone(min_crossing_frames=3)
    run(z, [380.0] * 4 + [420.0])
    z.update({})                              # coasting gap
    run(z, [420.0, 420.0])
    assert z.out_count == 1
    assert z.detected_but_never_counted == 0


# ------------------------ (a) crossed_without_tracker_id: the anonymous blob

def test_untracked_centroid_past_the_line_raises_crossed_without_tracker_id():
    z = zone(min_crossing_frames=3)
    res = z.update({}, untracked=[(150.0, 415.0)])
    assert res.crossed_without_tracker_id == 1
    (exc,) = res.exceptions
    assert exc.code == REASON_NO_TRACKER_ID
    assert exc.track_id is None
    assert exc.signed_dist_mm == pytest.approx(15.0)
    assert z.amber is True
    assert z.out_count == 0, "abstain: neither counted nor denied"


def test_untracked_centroid_on_the_line_also_raises():
    z = zone(min_crossing_frames=3)
    res = z.update({}, untracked=[(150.0, LINE_Y)])
    assert res.crossed_without_tracker_id == 1
    assert "dead band" in res.exceptions[0].detail


def test_untracked_centroid_still_inside_is_clean():
    z = zone(min_crossing_frames=3)
    res = z.update({}, untracked=[(150.0, 300.0)])
    assert res.crossed_without_tracker_id == 0
    assert res.clean and not z.amber


def test_untracked_centroid_outside_the_segment_is_clean():
    z = zone(min_crossing_frames=3)
    res = z.update({}, untracked=[(400.0, 415.0)])
    assert res.crossed_without_tracker_id == 0
    assert res.clean


def test_a_None_track_id_is_routed_not_dropped():
    """A dict cannot hold two None keys, so a caller passing {None: ...} is
    already losing data. Accepting it as an untracked centroid is the only
    behaviour that does not silently discard a crossing."""
    z = zone(min_crossing_frames=3)
    res = z.update({None: (150.0, 415.0)})
    assert res.crossed_without_tracker_id == 1
    assert res.exceptions[0].code == REASON_NO_TRACKER_ID


def test_reason_codes_are_the_exact_agreed_strings():
    assert REASON_NO_TRACKER_ID == "crossed_without_tracker_id"
    assert REASON_NEVER_COUNTED == "detected_but_never_counted"


def test_exceptions_are_never_silently_dropped():
    z = zone(min_crossing_frames=3)
    z.update({}, untracked=[(150.0, 415.0)])
    with pytest.raises(UncountedCrossing) as ei:
        z.raise_if_dirty()
    assert REASON_NO_TRACKER_ID in str(ei.value)
    assert len(ei.value.exceptions) == 1


def test_bad_inputs_are_refused_loudly():
    z = zone()
    with pytest.raises(ValueError):
        z.update({1: (150.0,)})
    with pytest.raises(ValueError):
        z.update({1: (150.0, float("nan"))})
    with pytest.raises(ValueError):
        z.update({"a": (150.0, 400.0)})


# ------------------------------------------------------------ CentroidTracker

def test_ids_are_stable_along_two_separated_paths():
    tr = CentroidTracker(max_dist_mm=25.0, max_missing_frames=2)
    first = tr.update([(50.0, 100.0), (200.0, 100.0)])
    assert sorted(first.tracks) == [1, 2]
    seen = []
    for k in range(1, 12):
        u = tr.update([(50.0, 100.0 + 8 * k), (200.0, 100.0 + 8 * k)])
        assert not u.untracked and not u.new_ids
        seen.append(sorted(u.tracks))
    assert all(s == [1, 2] for s in seen)
    assert tr.abstentions == 0


def test_new_object_gets_a_new_id_and_a_gone_one_is_lost():
    tr = CentroidTracker(max_dist_mm=25.0, max_missing_frames=2)
    tr.update([(50.0, 100.0)])
    u = tr.update([(52.0, 104.0), (250.0, 300.0)])
    assert u.new_ids == (2,)
    for _ in range(tr.max_missing_frames):
        u = tr.update([(250.0, 300.0)])
        assert u.lost == ()
    u = tr.update([(250.0, 300.0)])
    assert u.lost == (1,)
    assert tr.live_ids == (2,)


def test_a_jump_further_than_max_dist_is_a_new_object_not_a_teleport():
    tr = CentroidTracker(max_dist_mm=20.0, max_missing_frames=5)
    tr.update([(50.0, 100.0)])
    u = tr.update([(50.0, 200.0)])
    assert u.new_ids == (2,)
    assert 1 not in u.tracks


def test_tracker_abstains_when_one_centroid_is_tied_between_two_tracks():
    tr = CentroidTracker(max_dist_mm=80.0, max_missing_frames=5, ambiguity_mm=0.5)
    tr.update([(100.0, 100.0), (200.0, 100.0)])
    u = tr.update([(150.0, 100.0)])
    assert u.tracks == {}, "naming it would be a coin flip"
    assert u.untracked == ((150.0, 100.0),)
    assert u.new_ids == ()
    assert tr.abstentions == 1


def test_tracker_abstains_when_two_centroids_are_tied_to_one_track():
    tr = CentroidTracker(max_dist_mm=80.0, max_missing_frames=5, ambiguity_mm=0.5)
    tr.update([(150.0, 100.0)])
    u = tr.update([(140.0, 100.0), (160.0, 100.0)])
    assert u.tracks == {}
    assert set(u.untracked) == {(140.0, 100.0), (160.0, 100.0)}
    assert u.new_ids == ()


def test_a_clean_match_is_not_poisoned_by_a_distant_tie():
    """The abstention must be local. A far-away ambiguous pair may not cost a
    nearby unambiguous track its id."""
    tr = CentroidTracker(max_dist_mm=80.0, max_missing_frames=5, ambiguity_mm=0.5)
    tr.update([(10.0, 10.0), (100.0, 300.0), (160.0, 300.0)])
    u = tr.update([(11.0, 11.0), (130.0, 300.0)])
    assert u.tracks == {1: (11.0, 11.0)}
    assert u.untracked == ((130.0, 300.0),)


def test_tracker_input_validation():
    tr = CentroidTracker()
    with pytest.raises(ValueError):
        tr.update([(1.0, 2.0, 3.0)])
    with pytest.raises(ValueError):
        CentroidTracker(max_dist_mm=0.0)


# ------------------------------------- RE-IDENTIFICATION: the abstention that
# ------------------------------------- was missing (invariant 7)
#
# Association across a DETECTION GAP is not the same problem as association
# between two consecutive frames. Frame to frame there is continuity evidence:
# the object was there 60 ms ago and objects on a mat do not teleport. Across a
# blackout there is no evidence at all — only a blob that turned up somewhere
# near where a different blob used to be. Re-using the old id there is a guess,
# and it is a guess in the two directions that both cost money:
#
#   * it can MANUFACTURE a crossing, by splicing a post-gap OUT observation
#     onto a pre-gap IN history and letting the debounce commit a sale nobody
#     ever watched happen; and
#   * it can MASK a second item, by feeding a genuinely new object into an old
#     track so that the old track's disappearance is never reported and the new
#     object is never counted.
#
# Both must abstain with a name.


def test_reid_after_a_long_gap_does_not_silently_bind():
    """A track unseen for longer than the confidence window may not be re-bound
    to a blob that merely turns up nearby."""
    tr = CentroidTracker(max_dist_mm=40.0, max_missing_frames=4,
                         reid_max_gap_frames=1)
    tr.update([(150.0, 380.0)])                 # id 1
    tr.update([])                               # gap 1
    tr.update([])                               # gap 2 — past the window
    u = tr.update([(150.0, 412.0)])             # 32 mm away, inside the gate

    assert 1 not in u.tracks, "a 2-frame blackout is not evidence of identity"
    assert u.tracks == {}
    assert u.new_ids == (), "nor may it be renamed: that is the opposite guess"
    assert u.untracked == ((150.0, 412.0),)


def test_reid_with_two_candidates_in_the_gate_does_not_silently_bind():
    """Nearest-neighbour is not a tie-break when the evidence is missing: with
    two coasting tracks inside the gate, the nearer one is a guess, not a fact.
    Note the margin here is 25 mm — fifty times `ambiguity_mm`, so the ordinary
    tie rule does not fire and the old code binds happily."""
    tr = CentroidTracker(max_dist_mm=40.0, max_missing_frames=4,
                         reid_max_gap_frames=2, ambiguity_mm=0.5)
    tr.update([(100.0, 400.0), (135.0, 400.0)])   # ids 1, 2
    tr.update([])                                  # both coast, gap 1
    u = tr.update([(110.0, 400.0)])                # 10 mm from 1, 25 mm from 2

    assert u.tracks == {}, "two candidates in the gate is a coin flip"
    assert u.new_ids == ()
    assert u.untracked == ((110.0, 400.0),)


def test_MONEY_silent_reid_manufactures_a_crossing_never_observed():
    """THE MONEY BUG. An item sits on the shopkeeper's side and is then hidden
    by the hand. Three frames later a blob — a different item, the hand's own
    contour, anything — appears past the line where the first one used to be.
    Splicing the two together hands the debounce a complete IN->OUT history and
    charges the customer for a crossing no camera ever saw.
    """
    tr = CentroidTracker(max_dist_mm=40.0, max_missing_frames=6,
                         reid_max_gap_frames=1)
    z = zone(min_crossing_frames=3, evict_after_frames=tr.max_missing_frames + 1)

    def step(cs):
        u = tr.update(cs)
        return z.update(u.tracks, untracked=u.untracked, lost=u.lost)

    for _ in range(4):
        step([(150.0, 380.0)])        # id 1, held on the shopkeeper's side
    for _ in range(3):
        step([])                      # occluded: no detection at all
    for _ in range(3):
        res = step([(150.0, 412.0)])  # something reappears past the line

    assert z.out_count == 0, (
        "a crossing spliced across a 3-frame blackout is a manufactured sale")
    assert z.net_count == 0
    assert z.amber, "and the refusal must be visible, not silent"
    assert res.total_is_trustworthy is False


def test_MONEY_silent_reid_masks_a_genuine_second_item():
    """The other direction. Item 1 is lifted off the mat entirely; item 2 is
    put down nearby a few frames later. Re-using id 1 means item 1's departure
    is never judged and item 2 inherits a history it never earned."""
    tr = CentroidTracker(max_dist_mm=40.0, max_missing_frames=6,
                         reid_max_gap_frames=1)
    tr.update([(120.0, 300.0)])
    tr.update([(120.0, 300.0)])
    for _ in range(3):
        tr.update([])                       # item 1 taken away
    u = tr.update([(148.0, 300.0)])          # item 2 placed 28 mm away

    assert u.tracks == {}, "item 2 is not item 1 just because it is close"
    assert u.untracked == ((148.0, 300.0),)


def test_the_refused_reid_is_NAMED_not_merely_dropped():
    """An abstention with no name is indistinguishable from a bug. The gap
    refusal must arrive as a reason code, the candidates it declined to pick
    between, and the size of the gap that disqualified it."""
    tr = CentroidTracker(max_dist_mm=40.0, max_missing_frames=4,
                         reid_max_gap_frames=1)
    tr.update([(150.0, 380.0)])
    tr.update([])
    tr.update([])
    u = tr.update([(150.0, 412.0)])

    (a,) = u.untracked
    assert a.code == REASON_REID_GAP_EXCEEDED
    assert a.candidate_ids == (1,)
    assert a.gap_frames == 2
    assert a.is_reid
    assert "confidence window" in a.detail
    assert u.reid_abstentions == (a,)
    assert tr.reid_abstentions == 1
    assert tr.abstentions == 1


def test_the_ambiguous_reid_names_every_candidate_it_declined():
    tr = CentroidTracker(max_dist_mm=40.0, max_missing_frames=4,
                         reid_max_gap_frames=2, ambiguity_mm=0.5)
    tr.update([(100.0, 400.0), (135.0, 400.0)])
    tr.update([])
    u = tr.update([(110.0, 400.0)])

    (a,) = u.untracked
    assert a.code == REASON_REID_AMBIGUOUS
    assert a.candidate_ids == (1, 2), "both, not just the nearer one"
    assert a.gap_frames == 1
    assert tr.reid_abstentions == 1


def test_an_abstained_centroid_is_still_an_ordinary_point():
    """The reason rides ON the coordinate, so no caller has to know it exists.
    If this ever stops being true, every existing consumer silently loses the
    centroid, which is a worse bug than the one being fixed."""
    a = AbstainedCentroid((12.5, 34.0), code=REASON_REID_AMBIGUOUS,
                          detail="d", candidate_ids=[3, 1], gap_frames=2)
    assert a == (12.5, 34.0)
    assert tuple(a) == (12.5, 34.0)
    assert len(a) == 2
    x, y = a
    assert (x, y) == (12.5, 34.0)
    assert hash(a) == hash((12.5, 34.0))
    assert {a} == {(12.5, 34.0)}
    assert (a.x_mm, a.y_mm) == (12.5, 34.0)
    assert a.candidate_ids == (3, 1)
    # and it validates like any other point
    with pytest.raises(ValueError):
        AbstainedCentroid((1.0, float("inf")), code="x", detail="d")


def test_a_refused_reid_becomes_an_amber_exception_row():
    """INVARIANT 7, end to end: the refusal is routed to the SAME exception
    surface an uncounted crossing uses, under its own name, and the total stops
    being trustworthy."""
    tr = CentroidTracker(max_dist_mm=40.0, max_missing_frames=6,
                         reid_max_gap_frames=1)
    z = zone(min_crossing_frames=3, evict_after_frames=tr.max_missing_frames + 1)

    tr.update([(150.0, 380.0)])
    tr.update([])
    tr.update([])
    u = tr.update([(150.0, 412.0)])
    res = z.update(u.tracks, untracked=u.untracked, lost=u.lost)

    (exc,) = res.exceptions
    assert exc.code == REASON_REID_GAP_EXCEEDED
    assert exc.track_id is None, "naming one would imply we bound it"
    assert exc.candidate_ids == (1,)
    assert exc.signed_dist_mm == pytest.approx(12.0)
    assert "confidence window" in exc.detail
    assert "candidates 1" in str(exc)
    assert res.amber and not res.total_is_trustworthy
    assert z.out_count == 0


def test_reid_abstained_is_a_subset_of_the_anonymous_crossing_count():
    """Two counters that could disagree about the same event are two chances to
    publish a wrong number. `reid_abstained` is a BREAKDOWN, never an addition."""
    tr = CentroidTracker(max_dist_mm=40.0, max_missing_frames=6,
                         reid_max_gap_frames=1)
    z = zone(min_crossing_frames=3)

    tr.update([(150.0, 380.0)])
    tr.update([])
    tr.update([])
    for _ in range(3):
        u = tr.update([(150.0, 412.0)])
        res = z.update(u.tracks, untracked=u.untracked, lost=u.lost)

    assert z.reid_abstained == 3
    assert z.crossed_without_tracker_id == 3
    assert z.reid_abstained <= z.crossed_without_tracker_id
    assert res.reid_abstained == 3
    # a plain anonymous blob still lands in the generic bucket only
    z.update({}, untracked=[(150.0, 415.0)])
    assert z.reid_abstained == 3
    assert z.crossed_without_tracker_id == 4


def test_reid_max_gap_frames_zero_refuses_every_re_identification():
    tr = CentroidTracker(max_dist_mm=40.0, max_missing_frames=4,
                         reid_max_gap_frames=0)
    tr.update([(150.0, 300.0)])
    tr.update([])
    u = tr.update([(151.0, 301.0)])
    assert u.tracks == {}
    assert u.untracked[0].code == REASON_REID_GAP_EXCEEDED


def test_reid_window_is_validated():
    with pytest.raises(ValueError):
        CentroidTracker(reid_max_gap_frames=-1)


def test_a_stale_track_may_not_steal_a_live_tracks_detection():
    """Order of judgement matters. A track that is still being seen every frame
    must keep its id even when a stale track's last position happens to be
    nearer to the new detection than its own."""
    tr = CentroidTracker(max_dist_mm=40.0, max_missing_frames=6,
                         reid_max_gap_frames=1)
    tr.update([(100.0, 100.0), (300.0, 300.0)])     # ids 1 (stale soon), 2
    for _ in range(3):
        tr.update([(300.0, 300.0)])                  # id 1 coasts, id 2 lives
    u = tr.update([(300.0, 300.0), (99.0, 100.0)])

    assert u.tracks == {2: (300.0, 300.0)}, "the live track keeps its id"
    assert u.untracked[0].code == REASON_REID_GAP_EXCEEDED
    assert u.new_ids == ()


def test_a_reid_is_not_blocked_by_a_track_another_centroid_already_explains():
    """Strictness must not become superstition. Track 2 is 39 mm from the
    re-identified blob — inside the 40 mm gate, so nominally a rival — but track
    2 is simultaneously being claimed by its own centroid 5 mm away. A rival
    that is already accounted for is not an alternative explanation, and
    refusing here would cost an id for no gain in honesty.

    Continuation claims therefore settle FIRST, and only what is left over is
    put to the strict re-identification test.
    """
    tr = CentroidTracker(max_dist_mm=40.0, max_missing_frames=6,
                         reid_max_gap_frames=1)
    tr.update([(100.0, 100.0), (140.0, 100.0)])      # ids 1, 2
    tr.update([(140.0, 100.0)])                       # id 1 blinks; id 2 lives
    u = tr.update([(101.0, 100.0), (145.0, 100.0)])

    assert u.tracks == {1: (101.0, 100.0), 2: (145.0, 100.0)}
    assert u.untracked == ()
    assert u.new_ids == ()
    assert tr.reid_abstentions == 0


def test_the_abstention_resolves_once_the_stale_track_is_retired():
    """The refusal must be a WINDOW, not a deadlock: once the old track ages
    out, the blob is an honest new object and gets a fresh id."""
    tr = CentroidTracker(max_dist_mm=40.0, max_missing_frames=3,
                         reid_max_gap_frames=1)
    tr.update([(150.0, 300.0)])
    tr.update([])
    tr.update([])                                    # gap 2

    u = tr.update([(160.0, 300.0)])                  # gap 2: refused
    assert u.tracks == {} and u.untracked and u.lost == ()

    u = tr.update([(160.0, 300.0)])                  # gap 3 -> aged out here
    assert u.tracks == {} and u.untracked
    assert u.lost == (1,), "the stale track is retired, and reported"

    u = tr.update([(160.0, 300.0)])                  # nothing left to confuse it
    assert u.lost == ()
    assert u.new_ids == (2,)
    assert u.tracks == {2: (160.0, 300.0)}
    assert u.untracked == ()
    # the window cost exactly the frames the stale track was alive for
    assert tr.reid_abstentions == 2


def test_mat_dimensions_agree_with_takhti():
    """The two floats `mat_exit_line` no longer imports cv2 to obtain must keep
    agreeing with the module they were copied from."""
    from gawaah import sellevent
    from gawaah import takhti
    assert sellevent.MAT_W_MM == takhti.MAT_W_MM
    assert sellevent.MAT_H_MM == takhti.MAT_H_MM
    z = LineZone.mat_exit_line()
    assert z.p1 == (0.0, takhti.MAT_H_MM - 18.0)
    assert z.p2 == (takhti.MAT_W_MM, takhti.MAT_H_MM - 18.0)


def test_a_one_frame_blink_is_still_re_identified():
    """The guard must not become a blanket refusal to track. One dropped frame
    with a single uncontested candidate inside the gate is still an id."""
    tr = CentroidTracker(max_dist_mm=40.0, max_missing_frames=3,
                         reid_max_gap_frames=1)
    tr.update([(150.0, 300.0)])
    tr.update([])                            # one-frame detector blink
    u = tr.update([(153.0, 306.0)])
    assert u.tracks == {1: (153.0, 306.0)}
    assert u.untracked == ()
    assert u.new_ids == ()


# ------------------------------------------- INVARIANT 5: no OpenCV server-side

_BLOCK_TEMPLATE = """
import sys

BLOCKED = {blocked!r}


class _Deny:
    def find_spec(self, name, path=None, target=None):
        if name.partition('.')[0] in BLOCKED:
            raise ImportError("blocked by the invariant-5 test: " + name)
        return None


for _m in [m for m in sys.modules if m.partition('.')[0] in BLOCKED]:
    del sys.modules[_m]
sys.meta_path.insert(0, _Deny())

for _b in BLOCKED:
    try:
        __import__(_b)
    except ImportError:
        pass
    else:
        raise SystemExit("blocker did not work: %s was importable" % _b)
"""


def _run_without(blocked, body: str):
    """Run `body` in a fresh interpreter with `blocked` un-importable."""
    import pathlib
    import subprocess
    import sys as _sys

    root = pathlib.Path(__file__).resolve().parents[1]
    src = _BLOCK_TEMPLATE.format(blocked=tuple(blocked)) + body
    return subprocess.run([_sys.executable, "-c", src], cwd=str(root),
                          capture_output=True, text=True, timeout=180)


def test_INVARIANT5_crossing_predicate_runs_with_cv2_blocked():
    """The predicate that decides money must run on a machine with no camera
    stack. `mat_exit_line` used to reach for `gawaah.takhti`, which imports
    cv2 at module scope, so the server-side re-run needed OpenCV installed."""
    proc = _run_without(("cv2", "numpy"), """
import sys
from gawaah.sellevent import LineZone

z = LineZone.mat_exit_line(min_crossing_frames=3)
for y in [380.0] * 4 + [415.0] * 4:
    z.update({1: (150.0, y)})
assert z.out_count == 1, z.out_count
assert "cv2" not in sys.modules
assert "numpy" not in sys.modules
assert "gawaah.takhti" not in sys.modules
print("OK", z.out_count, z.net_count)
""")
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert proc.stdout.strip().endswith("OK 1 1"), proc.stdout


def test_INVARIANT5_paisa_replays_the_predicate_with_cv2_blocked():
    """paisa is the server-side re-run. If it cannot import without OpenCV,
    invariant 5 is decorative."""
    proc = _run_without(("cv2",), """
import sys
from gawaah.paisa import Crossing, replay_crossings

path = [(150.0, 380.0)] * 4 + [(150.0, 415.0)] * 4
r = replay_crossings(
    [Crossing(item_id="parle-g", track_id=1, path_mm=path, committed=True)],
    min_crossing_frames=3,
)
assert r.committed == (1,), r
assert r.uncounted == 0, r
assert "cv2" not in sys.modules
print("OK", r.committed, r.uncounted)
""")
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert proc.stdout.strip().endswith("OK (1,) 0"), proc.stdout


# ------------------------------------------------------- tracker + zone wired

def test_ambiguous_centroid_past_the_line_becomes_a_loud_uncounted_sale():
    """END TO END: the tracker refuses to name a centroid, the zone refuses to
    count it, and the refusal arrives as an object instead of a warning."""
    tr = CentroidTracker(max_dist_mm=80.0, max_missing_frames=5, ambiguity_mm=0.5)
    z = zone(min_crossing_frames=3, evict_after_frames=tr.max_missing_frames + 1)

    tr.update([(100.0, 410.0), (200.0, 410.0)])
    u = tr.update([(150.0, 410.0)])
    assert u.untracked, "precondition: the tracker must have abstained"
    res = z.update(u.tracks, untracked=u.untracked, lost=u.lost)

    assert res.crossed_without_tracker_id == 1
    assert res.exceptions[0].code == REASON_NO_TRACKER_ID
    assert res.amber and not res.total_is_trustworthy


def test_full_pipeline_counts_a_placement_and_stays_clean():
    tr = CentroidTracker(max_dist_mm=25.0, max_missing_frames=3)
    z = zone(min_crossing_frames=3, evict_after_frames=tr.max_missing_frames + 1)
    ys = [340.0, 348.0, 356.0, 364.0, 372.0, 380.0, 388.0, 396.0,
          404.0, 412.0, 420.0, 428.0, 436.0]
    for y in ys:
        u = tr.update([(150.0, y), (280.0, 120.0)])   # item + a static distractor
        z.update(u.tracks, untracked=u.untracked, lost=u.lost)
    for _ in range(6):
        u = tr.update([(280.0, 120.0)])
        z.update(u.tracks, untracked=u.untracked, lost=u.lost)
    z.flush()
    assert z.out_count == 1 and z.net_count == 1
    assert not z.amber, [str(e) for e in z.exceptions]


# ------------------------------------------------------------- determinism

def test_identical_scripts_give_byte_identical_results():
    def go():
        z = zone(min_crossing_frames=3)
        out = []
        for y in [380.0] * 4 + [420.0] * 4 + [380.0] * 6:
            r = z.update({1: (150.0, y)})
            out.append((r.crossed_out, r.crossed_back, r.net_count,
                        tuple(str(e) for e in r.exceptions)))
        out.append(tuple(str(e) for e in z.flush().exceptions))
        return out
    assert go() == go()


# -------------------------------------------------------------- properties

@settings(max_examples=200, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    n_in=st.integers(min_value=4, max_value=12),
    n_out=st.integers(min_value=3, max_value=12),
    depth=st.floats(min_value=2.0, max_value=100.0),
    x=st.floats(min_value=1.0, max_value=296.0),
    thr=st.integers(min_value=1, max_value=5),
)
def test_property_monotone_crossing_counts_exactly_once(n_in, n_out, depth, x, thr):
    z = zone(min_crossing_frames=thr)
    ys = [LINE_Y - depth] * max(n_in, thr + 1) + [LINE_Y + depth] * max(n_out, thr)
    run(z, ys, x=x)
    assert z.out_count == 1
    assert z.net_count == 1
    assert not z.exceptions


@settings(max_examples=200, deadline=None)
@given(offsets=st.lists(st.floats(min_value=-120.0, max_value=-1.5),
                        min_size=1, max_size=40))
def test_property_a_track_that_never_leaves_the_mat_never_counts(offsets):
    z = zone(min_crossing_frames=3)
    run(z, [LINE_Y + o for o in offsets])
    assert z.out_count == 0 and z.back_count == 0
    z.flush()
    assert z.detected_but_never_counted == 0


# ------------------------------------------------------------- ACCEPTANCE

def _scripted_session(rng: random.Random, n_items: int, noise_mm: float,
                      min_frames: int = 3):
    """One counter session: n_items placed and pushed across the sell line one
    at a time, with a static distractor sitting on the mat throughout."""
    tr = CentroidTracker(max_dist_mm=20.0, max_missing_frames=3)
    z = zone(min_crossing_frames=min_frames,
             evict_after_frames=tr.max_missing_frames + 1)
    distractor = (275.0, 120.0)

    def jit(p):
        return (p[0] + rng.gauss(0, noise_mm), p[1] + rng.gauss(0, noise_mm))

    for _ in range(n_items):
        x = rng.uniform(60.0, 230.0)
        y = LINE_Y - rng.uniform(60.0, 110.0)
        step = rng.uniform(6.0, 11.0)
        while y < LINE_Y + 40.0:
            u = tr.update([jit((x, y)), jit(distractor)])
            z.update(u.tracks, untracked=u.untracked, lost=u.lost)
            y += step
        for _ in range(6):                    # item removed from the mat
            u = tr.update([jit(distractor)])
            z.update(u.tracks, untracked=u.untracked, lost=u.lost)
    z.flush()
    return z


def test_ACCEPTANCE_sell_event_recall_over_60_scripted_placements():
    """BUILD_PROMPT S3 acceptance: 60 scripted placements, recall >= 0.98,
    uncounted crossings logged not swallowed."""
    rng = random.Random(20260829)
    sessions, per_session = 20, 3
    counted = 0
    amber_sessions = 0
    exceptions = []
    for _ in range(sessions):
        z = _scripted_session(rng, per_session, noise_mm=1.0)
        counted += z.out_count
        amber_sessions += int(z.amber)
        exceptions.extend(z.exceptions)
        assert z.back_count == 0, "no placement moved backwards"

    placements = sessions * per_session
    assert placements == 60
    recall = counted / placements
    print(f"\nsell-event recall            {counted}/{placements} = {recall:.4f}")
    print(f"amber sessions               {amber_sessions}/{sessions}")
    print(f"uncounted-crossing records   {len(exceptions)}")
    assert counted <= placements, f"phantom sales: {counted} > {placements}"
    assert recall >= 0.98, f"recall {recall:.4f} < 0.98"
    assert amber_sessions == 0, [str(e) for e in exceptions[:5]]


def test_ACCEPTANCE_dropped_tracks_are_all_reported_never_swallowed():
    """Inject a mid-crossing dropout into every one of 30 placements and assert
    the count of reports equals the count of injected losses exactly."""
    rng = random.Random(7)
    injected = 0
    reported = 0
    for _ in range(30):
        z = zone(min_crossing_frames=3)
        x = rng.uniform(40.0, 260.0)
        run(z, [LINE_Y - 30.0] * 4 + [LINE_Y + 5.0] * rng.randint(1, 2), x=x)
        injected += 1
        z.update({}, lost=[1])
        reported += z.detected_but_never_counted
        assert z.out_count == 0
    print(f"\ninjected dropouts            {injected}")
    print(f"detected_but_never_counted   {reported}")
    assert reported == injected


def _occluded_handover(rng: random.Random, gap: int, window: int):
    """One scripted occlusion: an item is held on the shopkeeper's side, the
    hand covers it for `gap` frames, and a blob then appears past the line.

    Nothing observed the transit. Whether that becomes a charge depends
    entirely on whether re-identification is allowed to guess.
    """
    tr = CentroidTracker(max_dist_mm=45.0, max_missing_frames=8,
                         reid_max_gap_frames=window)
    z = zone(min_crossing_frames=3, evict_after_frames=tr.max_missing_frames + 1)
    x = rng.uniform(40.0, 260.0)

    def step(cs):
        u = tr.update(cs)
        z.update(u.tracks, untracked=u.untracked, lost=u.lost)

    for _ in range(4):
        step([(x, LINE_Y - 12.0)])
    for _ in range(gap):
        step([])
    for _ in range(4):
        step([(x, LINE_Y + 18.0)])
    z.flush()
    return z


def test_ACCEPTANCE_MEASURED_reid_guard_prevents_manufactured_sales():
    """The A/B that produces the published number. Same 40 scripted occlusions,
    twice: once with the confidence window opened wide enough to permit the old
    silent re-bind, once at the shipped default. Every sale the guard removes
    is replaced by a NAMED amber row — none of them just disappear."""
    n = 40
    permissive = [_occluded_handover(random.Random(1000 + i), gap=3, window=99)
                  for i in range(n)]
    shipped = [_occluded_handover(random.Random(1000 + i), gap=3, window=1)
               for i in range(n)]

    manufactured = sum(z.out_count for z in permissive)
    silent = sum(1 for z in permissive if z.out_count and not z.amber)
    still_counted = sum(z.out_count for z in shipped)
    named = sum(z.reid_abstained for z in shipped)
    ambers = sum(1 for z in shipped if z.amber)
    codes = {e.code for z in shipped for e in z.exceptions}

    print(f"\nocclusion scenarios              {n}")
    print(f"sales manufactured, window open  {manufactured} "
          f"({silent} of them SILENT: no exception, green total)")
    print(f"sales manufactured, window=1     {still_counted}")
    print(f"named re-id abstentions          {named}")
    print(f"sessions correctly amber         {ambers}/{n}")

    assert manufactured == n, "precondition: the permissive path really guesses"
    assert silent == n, "precondition: and it guesses without saying so"
    assert still_counted == 0, "the guard must remove every manufactured sale"
    assert named >= n, "and replace each with at least one named abstention"
    assert ambers == n
    assert codes <= REID_REASONS | {REASON_NO_TRACKER_ID, REASON_NEVER_COUNTED}
    assert REASON_REID_GAP_EXCEEDED in codes


def test_HONEST_LIMIT_a_genuine_occluded_crossing_is_refused_too():
    """Pinned executably, because it is the price of the guard.

    Nothing in the pixels distinguishes a real item crossing behind the hand
    from a swap behind the hand. So a genuine sale that happens entirely inside
    the occlusion is NOT counted either: it becomes an amber row for a human to
    clear. The policy trades recall for never issuing a wrong charge, and
    `reid_max_gap_frames` is the documented knob that trades it back — here the
    same script counts once when the window is widened past the gap.
    """
    strict = _occluded_handover(random.Random(5), gap=2, window=1)
    wide = _occluded_handover(random.Random(5), gap=2, window=3)

    assert (strict.out_count, strict.amber) == (0, True)
    assert strict.reid_abstained > 0
    assert (wide.out_count, wide.amber) == (1, False)


def test_ACCEPTANCE_the_reid_guard_costs_the_clean_path_nothing():
    """The guard is only allowed to fire where the evidence is actually
    missing. Re-run the 60-placement acceptance session with the tracker's
    re-identification window at its shipped default and assert the recall and
    the amber count are untouched."""
    rng = random.Random(20260829)
    sessions, per_session = 20, 3
    counted = 0
    ambers = 0
    for _ in range(sessions):
        z = _scripted_session(rng, per_session, noise_mm=1.0)
        counted += z.out_count
        ambers += int(z.amber)
    placements = sessions * per_session
    print(f"\nclean-path recall with the guard {counted}/{placements}")
    print(f"amber sessions                   {ambers}/{sessions}")
    assert counted == placements
    assert ambers == 0


def test_determinism_holds_through_the_abstention_path():
    def go():
        out = []
        tr = CentroidTracker(max_dist_mm=45.0, max_missing_frames=6,
                             reid_max_gap_frames=1)
        z = zone(min_crossing_frames=3)
        script = ([[(150.0, 380.0)]] * 4 + [[]] * 3
                  + [[(150.0, 412.0), (170.0, 412.0)]] * 3)
        for cs in script:
            u = tr.update(cs)
            r = z.update(u.tracks, untracked=u.untracked, lost=u.lost)
            out.append((sorted(u.tracks), [(p.code, p.candidate_ids, p.gap_frames)
                                           for p in u.untracked],
                        r.net_count, r.reid_abstained,
                        tuple(str(e) for e in r.exceptions)))
        out.append(tuple(str(e) for e in z.flush().exceptions))
        return out
    a, b = go(), go()
    assert a == b


def test_MEASURED_update_latency():
    """The PRD budgets 0.05 ms for LineZone.trigger. Measure ours, do not
    quote theirs."""
    z = zone(min_crossing_frames=3)
    tracks = {1: (100.0, 380.0), 2: (150.0, 405.0), 3: (200.0, 300.0)}
    n = 20000
    t0 = time.perf_counter()
    for _ in range(n):
        z.update(tracks)
    dt = time.perf_counter() - t0
    us = dt * 1e6 / n
    print(f"\nLineZone.update  {us:.2f} us/frame @ 3 tracks ({n} frames)")

    tr = CentroidTracker(max_dist_mm=25.0)
    pts = [(100.0, 380.0), (150.0, 405.0), (200.0, 300.0)]
    m = 20000
    t0 = time.perf_counter()
    for _ in range(m):
        tr.update(pts)
    dt2 = time.perf_counter() - t0
    print(f"CentroidTracker.update  {dt2 * 1e6 / m:.2f} us/frame @ 3 centroids")
    assert us < 1000.0, f"{us:.1f} us/frame is not a real-time budget"
