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
    SIDE_IN, SIDE_ON_LINE, SIDE_OUT,
    CentroidTracker, CrossingResult, LineZone, UncountedCrossing,
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
