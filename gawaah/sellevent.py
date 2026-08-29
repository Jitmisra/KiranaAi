"""S3b — the DETERMINISTIC SELL EVENT.

A sale is a directional line crossing on the rectified metric plane. There is no
model here, no learned threshold, no confidence score: a tracked centroid either
stayed on the far side of a printed line for `min_crossing_frames` consecutive
frames or it did not. Thirty auditable lines decide that money moves.

Coordinates
-----------
Everything in this module is TAKHTI millimetres — the rectified plane's metric
coordinates, x to the right, y DOWN (buffer origin is top-left, so y increases
toward the mat's far edge, which is the customer's side). Nothing here imports
cv2 or numpy: given the same script of centroids it returns the same answer on
any machine, which is what makes the ledger line replayable.

Direction convention
--------------------
With the line directed p1 -> p2, the OUT half-plane is the one where

    cross(p2 - p1, p - p1) = vx*(py - p1y) - vy*(px - p1x)

is positive. For a left-to-right line across the mat (p1 = (0, y0),
p2 = (297, y0)) that is the larger-y side — the far edge, the customer. See
`LineZone.mat_exit_line`.

THE MONEY BUG IN A VISION BUG'S CLOTHES
---------------------------------------
`supervision.detection.line_zone.LineZone.trigger` (vendored at
reference/supervision/src/supervision/detection/line_zone.py:170 in this repo)
does this when the tracker gives it nothing:

    if detections.tracker_id is None:
        warnings.warn(...)
        return crossed_in, crossed_out       # <- silently uncounted

A `warnings.warn` is not an exception object; nothing downstream can see that a
sale went unrecorded. The same silence covers a track that is dropped halfway
across the line. Both are uncounted SALES, so this module surfaces both as
first-class `CrossingException` records on every `CrossingResult`, keeps running
totals, and latches `amber` forever once either has fired. Invariant 7: abstain
loudly with a named reason rather than guess.

Two deliberate deviations from upstream, both because upstream would cost money:

1. Upstream's deque test (`crossing_history.count(oldest) > 1`) can fire twice
   for one physical crossing when a centroid wobbles: with threshold 3 the
   history T,T,T,F,T,T,T passes through [F,T,T,T] a second time and counts a
   second OUT. We additionally require the committed side to actually CHANGE,
   so one physical crossing can only be counted once.
2. A crossing back only decrements a track that has an uncancelled OUT crossing
   to its name. An item that walks onto the mat from the customer's side is not
   a negative sale, and `net_count` can therefore never go below zero.

RE-IDENTIFICATION IS NOT ASSOCIATION
------------------------------------
Matching a detection to a track that was visible in the PREVIOUS frame is
supported by continuity: 60 ms ago the object was there, and objects resting on
a mat do not teleport. Matching a detection to a track that has been UNSEEN for
one or more frames is a different problem with none of that evidence — all you
have is a blob that turned up somewhere near where a different blob used to be.
Re-using the old id there is a guess, and it is a guess in the two directions
that both cost money:

  * it can MANUFACTURE a crossing, by splicing a post-gap OUT observation onto
    a pre-gap IN history so the debounce commits a sale nobody watched; and
  * it can MASK a second item, by feeding a genuinely new object into an old
    track, so the old track's disappearance is never judged and the new object
    is never counted.

So `CentroidTracker` splits the two cases. Across a gap it requires SOLE
OCCUPANCY of the gate (not merely the nearest candidate by some margin) and a
gap no longer than `reid_max_gap_frames`. When either fails it emits an
`AbstainedCentroid` carrying `REASON_REID_AMBIGUOUS` or
`REASON_REID_GAP_EXCEEDED`, which travels through `LineZone.update(untracked=)`
onto the existing `CrossingException` surface and latches amber. It never binds
the id, and it never invents a fresh one either — that is the same guess facing
the other way.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

Point = tuple[float, float]

# --- named reason codes; these strings land in the ledger --------------------
REASON_NO_TRACKER_ID = "crossed_without_tracker_id"
REASON_NEVER_COUNTED = "detected_but_never_counted"
#: Re-identification refused: more than one candidate inside the gate, so the
#: nearest is a coin flip dressed as a measurement.
REASON_REID_AMBIGUOUS = "reidentification_ambiguous"
#: Re-identification refused: the track had been unseen for longer than the
#: confidence window, so "it is probably the same item" is an assumption.
REASON_REID_GAP_EXCEEDED = "reidentification_gap_exceeded"
#: The subset of reason codes that mean "a re-identification was refused".
REID_REASONS = frozenset({REASON_REID_AMBIGUOUS, REASON_REID_GAP_EXCEEDED})

# --- the TAKHTI's metric size, millimetres -----------------------------------
# Duplicated from `gawaah.takhti` ON PURPOSE. takhti imports cv2 at module
# scope, and INVARIANT 5 requires paisa to re-run this predicate server-side on
# a machine that has never seen a camera; two floats are a cheaper dependency
# than OpenCV. `test_mat_dimensions_agree_with_takhti` fails the build if the
# two definitions ever drift apart.
MAT_W_MM = 297.0        # A3 short edge
MAT_H_MM = 420.0        # A3 long edge

# --- sides -------------------------------------------------------------------
SIDE_IN = -1        # the shopkeeper's side of the sell line
SIDE_OUT = +1       # the customer's side
SIDE_ON_LINE = 0    # inside the dead band: carries no evidence either way

# A centroid within this many mm of the line contributes NO side evidence.
# 1.0 mm is ~2.8 px in the rectified buffer (PX_PER_MM = 2*sqrt(2)), i.e. the
# scale below which contour-centroid noise, not motion, dominates.
DEAD_BAND_MM = 1.0


class UncountedCrossing(Exception):
    """Raised by `LineZone.raise_if_dirty()` for integrators that want a hard
    stop rather than an amber banner. The records are on the result either way;
    this is only an escape hatch, never the sole channel."""

    def __init__(self, exceptions: tuple["CrossingException", ...]) -> None:
        super().__init__(
            f"{len(exceptions)} uncounted crossing(s): "
            + "; ".join(str(e) for e in exceptions[:5])
        )
        self.exceptions = exceptions


class AbstainedCentroid(tuple):
    """A centroid the tracker REFUSED to name, carrying its reason with it.

    It *is* the `(x_mm, y_mm)` pair — a two-element tuple, equal to and hashable
    as one — so every caller that treats `TrackerUpdate.untracked` as a sequence
    of points keeps working unchanged, while `LineZone.update` can read `.code`
    off it and put the SPECIFIC named reason into the ledger row instead of a
    generic one.

    The alternative was a parallel list of reasons beside the points. A parallel
    list is precisely the sort of thing that gets dropped on the way to the
    exception surface, and a dropped abstention is the bug this module exists to
    prevent. Welding the reason to the coordinate makes that impossible.
    """

    def __new__(cls, point: Point, *, code: str, detail: str,
                frame_index: int = -1, candidate_ids: Sequence[int] = (),
                gap_frames: int = 0) -> "AbstainedCentroid":
        x, y = _as_point(point, "abstained centroid")
        self = super().__new__(cls, (x, y))
        self.code = str(code)
        self.detail = str(detail)
        self.frame_index = int(frame_index)
        self.candidate_ids = tuple(int(i) for i in candidate_ids)
        self.gap_frames = int(gap_frames)
        return self

    @property
    def x_mm(self) -> float:
        return self[0]

    @property
    def y_mm(self) -> float:
        return self[1]

    @property
    def is_reid(self) -> bool:
        return self.code in REID_REASONS

    def __repr__(self) -> str:      # pragma: no cover - diagnostics only
        return (f"AbstainedCentroid(({self[0]:.1f}, {self[1]:.1f}), "
                f"code={self.code!r}, candidate_ids={self.candidate_ids}, "
                f"gap_frames={self.gap_frames})")


def _as_anon(c: object) -> Point:
    """Validate an untracked centroid WITHOUT stripping its abstention record."""
    p = _as_point(c, "untracked centroid")
    return c if isinstance(c, AbstainedCentroid) else p


@dataclass(frozen=True)
class CrossingException:
    """One instance of a crossing the deterministic rule could not count.

    This is a RECORD, not a Python exception. It exists so that an uncounted
    sale has an object with a name, a place and a frame index attached to it,
    instead of a warning nobody reads.
    """

    code: str            # one of the REASON_* codes above
    detail: str
    frame_index: int
    x_mm: float
    y_mm: float
    track_id: int | None = None
    signed_dist_mm: float | None = None
    #: For a refused re-identification: the track ids that were plausible. It
    #: is deliberately NOT `track_id` — naming one would imply we bound it.
    candidate_ids: tuple[int, ...] = ()

    def __str__(self) -> str:
        who = "no-id" if self.track_id is None else f"track {self.track_id}"
        if self.candidate_ids:
            who += f" (candidates {','.join(str(i) for i in self.candidate_ids)})"
        return (f"[{self.code}] frame {self.frame_index} {who} "
                f"at ({self.x_mm:.1f}, {self.y_mm:.1f})mm: {self.detail}")


@dataclass(frozen=True)
class CrossingResult:
    """What one frame did to the count, and what it could not account for."""

    frame_index: int
    crossed_out: tuple[int, ...]          # track ids that committed OUT now
    crossed_back: tuple[int, ...]         # track ids that decremented now
    exceptions: tuple[CrossingException, ...]   # NEW this frame
    out_count: int                        # running
    back_count: int                       # running
    net_count: int                        # running, out - back, never < 0
    crossed_without_tracker_id: int       # running
    detected_but_never_counted: int       # running
    entries_from_out: int                 # running; wrong-way arrivals, not sales
    vanished_same_side: int               # running; benign, not an exception
    tracks_tracked: int                   # ids live in the zone right now
    amber: bool                           # latched: either counter ever fired
    #: Running BREAKDOWN of `crossed_without_tracker_id`: how many of those
    #: anonymous crossings were refused re-identifications. A subset, never an
    #: additional count, so the two can never disagree about the total.
    reid_abstained: int = 0

    @property
    def clean(self) -> bool:
        """True when this frame produced no uncounted-crossing record."""
        return not self.exceptions

    @property
    def total_is_trustworthy(self) -> bool:
        """The basket total may only be shown green while this is True."""
        return not self.amber


@dataclass(frozen=True)
class TrackerUpdate:
    """One frame of association.

    `untracked` holds centroids the tracker REFUSED to name because naming them
    would have been a coin flip. They are handed to `LineZone.update` as
    `untracked=` so that a crossing without a stable id becomes a loud
    exception instead of a silently dropped sale.

    Every entry of `untracked` is an `AbstainedCentroid`: it compares equal to
    the plain `(x_mm, y_mm)` tuple, and it also carries `.code`, `.detail`,
    `.candidate_ids` and `.gap_frames`, so the reason survives the trip to the
    exception surface without a parallel list to lose.
    """

    frame_index: int
    tracks: dict[int, Point]
    untracked: tuple[Point, ...]
    lost: tuple[int, ...]
    new_ids: tuple[int, ...]

    @property
    def reid_abstentions(self) -> tuple[AbstainedCentroid, ...]:
        """The subset of `untracked` that is a refused re-identification."""
        return tuple(p for p in self.untracked
                     if isinstance(p, AbstainedCentroid) and p.is_reid)


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _as_point(v: object, what: str) -> Point:
    if not isinstance(v, (tuple, list)) or len(v) != 2:
        raise ValueError(f"{what} must be an (x_mm, y_mm) pair, got {v!r}")
    x, y = v
    for c in (x, y):
        if isinstance(c, bool) or not isinstance(c, (int, float)):
            raise ValueError(f"{what} coordinates must be numbers, got {v!r}")
        if not math.isfinite(float(c)):
            raise ValueError(f"{what} coordinates must be finite, got {v!r}")
    return (float(x), float(y))


class CentroidTracker:
    """Nearest-neighbour association with an explicit abstention rule.

    Deliberately not a Kalman/ByteTrack: objects are AT REST ON THE PLANE
    between moves, motion between frames is small, and a deterministic matcher
    is auditable line by line. The part that matters for money is the refusal:
    when the best and second-best candidate are within `ambiguity_mm` of each
    other, the assignment is a coin flip, so no id is issued and the centroid
    is returned in `untracked`. Inventing an id there is exactly how a sale
    gets attributed to the wrong item.

    Two association problems, two rules
    -----------------------------------
    CONTINUATION (the track was visible last frame, gap == 0) is backed by
    continuity, so the ordinary `ambiguity_mm` margin decides it.

    RE-IDENTIFICATION (the track has been unseen for `gap >= 1` frames) has no
    such evidence, so it is held to a stricter standard, and failing it is an
    abstention rather than a nearest-neighbour guess:

      * the gap must not exceed `reid_max_gap_frames`, else
        `REASON_REID_GAP_EXCEEDED`; and
      * the pairing must have SOLE OCCUPANCY of the gate — no other unclaimed
        track within `max_dist_mm` of the detection, and no other unclaimed
        detection within `max_dist_mm` of the track — else
        `REASON_REID_AMBIGUOUS`. Being 15 mm nearer than the runner-up is not
        evidence about which item this is when nobody watched it move.

    A refused re-identification is NOT given a fresh id either. "This is a new
    item" is the same guess facing the other way, and it would double-count the
    item it actually is. The centroid stays anonymous until the stale track
    ages out at `max_missing_frames`, and while it is anonymous a crossing by it
    is an amber exception row, never a silent count.

    Args:
        max_dist_mm: association gate; further than this is never the same object.
        max_missing_frames: frames a track may coast unseen before it is lost.
        ambiguity_mm: two candidates whose distances differ by no more than this
            are indistinguishable at the plane's resolution. 0.5 mm is ~1.4 px.
        reid_max_gap_frames: the confidence window for re-identification. 1 is
            the default because a single dropped frame is a detector blink (the
            item cannot have gone anywhere in ~60 ms) while two or more is a
            real occlusion, during which the hand that caused it can also have
            removed, swapped or added an item. 0 disables re-identification
            entirely; anything >= `max_missing_frames` disables the window
            check, because the track is retired before it can trip.
    """

    def __init__(self, max_dist_mm: float = 25.0, max_missing_frames: int = 3,
                 ambiguity_mm: float = 0.5, reid_max_gap_frames: int = 1) -> None:
        if max_dist_mm <= 0:
            raise ValueError("max_dist_mm must be positive")
        if max_missing_frames < 0:
            raise ValueError("max_missing_frames must be >= 0")
        if ambiguity_mm < 0:
            raise ValueError("ambiguity_mm must be >= 0")
        if reid_max_gap_frames < 0:
            raise ValueError("reid_max_gap_frames must be >= 0")
        self.max_dist_mm = float(max_dist_mm)
        self.max_missing_frames = int(max_missing_frames)
        self.ambiguity_mm = float(ambiguity_mm)
        self.reid_max_gap_frames = int(reid_max_gap_frames)
        self._pos: dict[int, Point] = {}
        self._missing: dict[int, int] = {}
        self._next_id = 1
        self._frame = -1
        self.abstentions = 0          # running count of refused assignments
        self.reid_abstentions = 0     # subset: refused re-identifications

    @property
    def live_ids(self) -> tuple[int, ...]:
        return tuple(sorted(self._pos))

    def gap_frames(self, track_id: int) -> int:
        """Consecutive frames `track_id` has been unseen. 0 == seen last frame."""
        return int(self._missing.get(int(track_id), 0))

    def update(self, centroids: Sequence[Point]) -> TrackerUpdate:
        self._frame += 1
        dets = [_as_point(c, "centroid") for c in centroids]
        ids = list(self._pos)
        n_d, n_t = len(dets), len(ids)

        dm = [[_dist(dets[di], self._pos[ids[ti]]) for ti in range(n_t)]
              for di in range(n_d)]

        gaps = [self._missing.get(ids[ti], 0) for ti in range(n_t)]
        # A track past the confidence window may not be ASSIGNED at all. It is
        # still a rival below: an item that has been gone four frames is not a
        # name we can use, but it is very much an alternative explanation for a
        # blob that turns up where it was.
        eligible = {ti for ti in range(n_t)
                    if gaps[ti] <= self.reid_max_gap_frames}

        # CONTINUATION claims are settled first, RE-IDENTIFICATION claims
        # second, each round sorted by distance. Order matters: a track that is
        # simultaneously being claimed by its own centroid is not an
        # alternative explanation for somebody else's, and judging the rounds
        # together would refuse a 1 mm re-identification because a track 39 mm
        # away was nominally still inside the gate.
        rounds = [
            sorted((dm[di][ti], di, ti)
                   for di in range(n_d) for ti in range(n_t)
                   if dm[di][ti] <= self.max_dist_mm and ti in eligible
                   and (gaps[ti] == 0) == is_continuation)
            for is_continuation in (True, False)
        ]

        avail_d = set(range(n_d))
        avail_t = set(range(n_t))
        assign: dict[int, int] = {}
        untracked_idx: set[int] = set()
        # di -> (code, detail, candidate track ids, gap frames)
        reasons: dict[int, tuple[str, str, tuple[int, ...], int]] = {}

        def refuse(di: int, code: str, detail: str,
                   cands: Sequence[int], gap: int) -> None:
            untracked_idx.add(di)
            avail_d.discard(di)
            reasons.setdefault(di, (code, detail, tuple(cands), gap))

        # Greedy over each round's globally sorted pairs. Because the list is
        # sorted and we only ever look at still-available rows/columns, any
        # rival distance within the round is >= the current one, so `rival - d`
        # is the true margin.
        for d, di, ti in [p for r in rounds for p in r]:
            if di not in avail_d or ti not in avail_t:
                continue
            gap = gaps[ti]

            if gap > 0:
                # --- RE-IDENTIFICATION: no continuity evidence, strict rules --
                # sole occupancy of the gate, in BOTH directions
                other_t = [ids[t2] for t2 in sorted(avail_t)
                           if t2 != ti and dm[di][t2] <= self.max_dist_mm]
                other_d = [d2 for d2 in avail_d
                           if d2 != di and dm[d2][ti] <= self.max_dist_mm]
                if other_t or other_d:
                    cands = sorted({ids[ti], *other_t})
                    detail = (
                        f"after a {gap}-frame gap this centroid is inside the "
                        f"{self.max_dist_mm:.0f}mm gate of "
                        f"{len(cands)} track(s) {cands} and "
                        f"{1 + len(other_d)} centroid(s) contest track "
                        f"{ids[ti]}; nearest-neighbour would be a coin flip "
                        f"dressed as a measurement"
                    )
                    refuse(di, REASON_REID_AMBIGUOUS, detail, cands, gap)
                    for d2 in list(other_d):
                        refuse(d2, REASON_REID_AMBIGUOUS, detail, cands, gap)
                    if other_d:
                        avail_t.discard(ti)
                    continue
                assign[di] = ti
                avail_d.discard(di)
                avail_t.discard(ti)
                continue

            # --- CONTINUATION: the margin rule -------------------------------
            rival_t = [t2 for t2 in avail_t
                       if t2 != ti and dm[di][t2] - d <= self.ambiguity_mm
                       and dm[di][t2] <= self.max_dist_mm]
            rival_d = [d2 for d2 in avail_d
                       if d2 != di and dm[d2][ti] - d <= self.ambiguity_mm
                       and dm[d2][ti] <= self.max_dist_mm]
            if rival_t or rival_d:
                # Abstain. One centroid tied between two tracks loses only
                # itself; one track tied between two centroids loses the track
                # for this frame and every tied centroid with it, because there
                # is no evidence at all about which one it is.
                cands = sorted({ids[ti], *(ids[t2] for t2 in rival_t)})
                detail = (
                    f"centroid is within {self.ambiguity_mm}mm of a tie between "
                    f"{len(cands)} track(s) {cands}"
                    if rival_t else
                    f"{1 + len(rival_d)} centroids are tied for track {ids[ti]} "
                    f"to within {self.ambiguity_mm}mm"
                )
                refuse(di, REASON_NO_TRACKER_ID, detail, cands, 0)
                for d2 in rival_d:
                    refuse(d2, REASON_NO_TRACKER_ID, detail, cands, 0)
                if rival_d:
                    avail_t.discard(ti)
                continue
            assign[di] = ti
            avail_d.discard(di)
            avail_t.discard(ti)

        # --- second pass: leftovers sitting inside a STALE track's gate -------
        # Every live claim has now been settled, so anything still unclaimed is
        # judged against the tracks that were past the confidence window. A blob
        # that appears where an item vanished four frames ago gets neither that
        # item's id (it may be a different object) nor a fresh one (it may be
        # the same object, and a fresh id double-counts it). It gets a name for
        # its uncertainty instead.
        for di in sorted(avail_d):
            stale = sorted(
                (dm[di][ti], gaps[ti], ids[ti]) for ti in avail_t
                if ti not in eligible and dm[di][ti] <= self.max_dist_mm
            )
            if not stale:
                continue
            d0, gap0, tid0 = stale[0]
            cands = sorted(t for _, _, t in stale)
            refuse(di, REASON_REID_GAP_EXCEEDED,
                   f"nearest candidate track {tid0} was last seen {gap0} frames "
                   f"ago, {d0:.1f}mm away, beyond the "
                   f"{self.reid_max_gap_frames}-frame confidence window "
                   f"(candidates {cands}); an occlusion that long can hide a "
                   f"swap, a removal or an addition, so this centroid is "
                   f"neither re-identified nor renamed",
                   cands, gap0)

        self.abstentions += len(untracked_idx)
        self.reid_abstentions += sum(
            1 for di in untracked_idx if reasons[di][0] in REID_REASONS
        )

        tracks: dict[int, Point] = {}
        for di, ti in assign.items():
            tid = ids[ti]
            self._pos[tid] = dets[di]
            self._missing[tid] = 0
            tracks[tid] = dets[di]

        new_ids: list[int] = []
        for di in sorted(avail_d):           # matched nothing, and not ambiguous
            tid = self._next_id
            self._next_id += 1
            self._pos[tid] = dets[di]
            self._missing[tid] = 0
            tracks[tid] = dets[di]
            new_ids.append(tid)

        lost: list[int] = []
        matched_ids = {ids[ti] for ti in assign.values()}
        for tid in list(self._pos):
            if tid in matched_ids or tid in new_ids:
                continue
            self._missing[tid] = self._missing.get(tid, 0) + 1
            if self._missing[tid] > self.max_missing_frames:
                del self._pos[tid]
                del self._missing[tid]
                lost.append(tid)

        refused: list[AbstainedCentroid] = []
        for di in sorted(untracked_idx):
            code, detail, cands, gap = reasons[di]
            refused.append(AbstainedCentroid(
                dets[di], code=code, detail=detail, frame_index=self._frame,
                candidate_ids=cands, gap_frames=gap,
            ))

        return TrackerUpdate(
            frame_index=self._frame,
            tracks=tracks,
            untracked=tuple(refused),
            lost=tuple(sorted(lost)),
            new_ids=tuple(new_ids),
        )


@dataclass
class _TrackState:
    history: deque[bool]                 # True == observed on the OUT side
    settled_side: int = SIDE_ON_LINE     # the side we have committed to
    last_side: int = SIDE_ON_LINE        # last DEFINITE side observed
    absent: int = 0
    out_credits: int = 0                 # uncancelled OUT crossings
    ever_in_limits: bool = False
    first_frame: int = -1
    last_pos: Point = (0.0, 0.0)


class LineZone:
    """A directional sell line on the rectified plane.

    Args:
        p1_mm, p2_mm: the line segment's endpoints in mat millimetres. OUT is
            the positive-cross side of p1 -> p2 (see module docstring).
        min_crossing_frames: consecutive frames the centroid must be held on
            the far side before the crossing is committed. Upstream's default
            is 1, which counts a hand wobble; 3 is the pre-built answer to
            "what if it hesitates on the line".
        evict_after_frames: frames a track may be absent before the zone
            retires it and judges whether a sale went uncounted. Defaults to
            `min_crossing_frames + 1`, matching upstream's eviction window, so
            a one-frame detection gap does not reset a crossing in progress.
            Pass the tracker's `max_missing_frames + 1` when pairing the two.
        dead_band_mm: half-width of the band around the line in which a
            centroid is treated as ON the line and contributes no evidence.
        limits_pad_mm: how far beyond the segment's endpoints a centroid may
            be and still be considered inside the counting region.
    """

    def __init__(self, p1_mm: Point, p2_mm: Point, min_crossing_frames: int = 3,
                 *, evict_after_frames: int | None = None,
                 dead_band_mm: float = DEAD_BAND_MM,
                 limits_pad_mm: float = 0.0) -> None:
        self.p1 = _as_point(p1_mm, "p1_mm")
        self.p2 = _as_point(p2_mm, "p2_mm")
        self._vx = self.p2[0] - self.p1[0]
        self._vy = self.p2[1] - self.p1[1]
        self._len = math.hypot(self._vx, self._vy)
        if self._len <= 0:
            raise ValueError("p1_mm and p2_mm must be distinct points")
        if min_crossing_frames < 1:
            raise ValueError("min_crossing_frames must be >= 1")
        if dead_band_mm < 0:
            raise ValueError("dead_band_mm must be >= 0")
        self.min_crossing_frames = int(min_crossing_frames)
        self._hist_len = max(2, self.min_crossing_frames + 1)
        self.evict_after_frames = (
            self._hist_len if evict_after_frames is None else int(evict_after_frames)
        )
        self.dead_band_mm = float(dead_band_mm)
        self.limits_pad_mm = float(limits_pad_mm)

        self._state: dict[int, _TrackState] = {}
        self._frame = -1
        self.out_count = 0
        self.back_count = 0
        self.crossed_without_tracker_id = 0
        self.detected_but_never_counted = 0
        self.entries_from_out = 0
        self.vanished_same_side = 0
        self.frames_with_untracked_out = 0
        self.reid_abstained = 0
        self.exceptions: list[CrossingException] = []

    # ------------------------------------------------------------------ setup
    @classmethod
    def mat_exit_line(cls, inset_mm: float = 18.0, min_crossing_frames: int = 3,
                      **kw) -> "LineZone":
        """The TAKHTI's printed exit edge, OUT pointing at the customer.

        Uses this module's own `MAT_W_MM`/`MAT_H_MM` and imports nothing. It
        used to lazy-import `gawaah.takhti`, whose module scope does
        `import cv2`, which meant paisa's server-side re-run of the crossing
        predicate needed OpenCV installed to construct the line — a plain
        breach of INVARIANT 5, since the whole point is that the money service
        can re-decide the sale on a machine that has never seen a camera. A
        lazy import is not an optional dependency; it is the same dependency,
        deferred. Two float constants and a test that they still agree with
        takhti is the honest version.
        """
        y = MAT_H_MM - inset_mm
        return cls((0.0, y), (MAT_W_MM, y), min_crossing_frames, **kw)

    # ------------------------------------------------------------- properties
    @property
    def net_count(self) -> int:
        return self.out_count - self.back_count

    @property
    def amber(self) -> bool:
        """Latched the moment any uncounted-crossing counter fires. While this
        is True the basket total must not be shown as green.

        `reid_abstained` is a subset of `crossed_without_tracker_id` and cannot
        latch this on its own; it is named here anyway so that removing the
        subset relation later cannot quietly un-amber a refused
        re-identification."""
        return bool(self.crossed_without_tracker_id
                    or self.detected_but_never_counted
                    or self.reid_abstained)

    def raise_if_dirty(self) -> None:
        if self.exceptions:
            raise UncountedCrossing(tuple(self.exceptions))

    # ---------------------------------------------------------------- geometry
    def project(self, p: Point) -> tuple[float, float]:
        """(t, signed_distance_mm) where t is the along-line parameter in [0,1]
        for a point beside the segment."""
        x, y = p
        dx, dy = x - self.p1[0], y - self.p1[1]
        t = (dx * self._vx + dy * self._vy) / (self._len * self._len)
        d = (self._vx * dy - self._vy * dx) / self._len
        return t, d

    def signed_distance_mm(self, p: Point) -> float:
        return self.project(_as_point(p, "point"))[1]

    def side(self, p: Point) -> int:
        return self._side(self.signed_distance_mm(p))

    def in_limits(self, p: Point) -> bool:
        t, _ = self.project(_as_point(p, "point"))
        s = t * self._len
        return -self.limits_pad_mm <= s <= self._len + self.limits_pad_mm

    def _side(self, d: float) -> int:
        if d > self.dead_band_mm:
            return SIDE_OUT
        if d < -self.dead_band_mm:
            return SIDE_IN
        return SIDE_ON_LINE

    # ------------------------------------------------------------------ update
    def update(self, tracks: Mapping[int | None, Point], *,
               untracked: Sequence[Point] = (),
               lost: Iterable[int] = ()) -> CrossingResult:
        """Advance one frame.

        Args:
            tracks: {track_id: (x_mm, y_mm)} for every track VISIBLE this frame.
                A `None` key is accepted and routed to `untracked` rather than
                being dropped, because a dict cannot hold two of them and a
                silently dropped one is the exact bug this module exists for.
            untracked: centroids detected this frame with no stable id.
            lost: track ids the tracker has definitively given up on. They are
                retired immediately instead of waiting out `evict_after_frames`.
        """
        self._frame += 1
        new_exc: list[CrossingException] = []
        crossed_out: list[int] = []
        crossed_back: list[int] = []

        anon: list[Point] = [_as_anon(c) for c in untracked]
        clean_tracks: dict[int, Point] = {}
        for tid, pos in tracks.items():
            p = _as_point(pos, f"track {tid!r} position")
            if tid is None:
                anon.append(p)
                continue
            if isinstance(tid, bool) or not isinstance(tid, int):
                raise ValueError(f"track id must be an int or None, got {tid!r}")
            clean_tracks[tid] = p

        # --- (a) crossings with no stable id ---------------------------------
        fired_anon = False
        for p in anon:
            if not self.in_limits(p):
                continue
            d = self.project(p)[1]
            if d <= -self.dead_band_mm:
                continue    # clearly still on the shopkeeper's side: not a crossing
            where = ("past the line" if d > self.dead_band_mm
                     else "inside the dead band on the line")
            # The reason travels ON the centroid when it came from the tracker,
            # so a refused re-identification lands in the ledger under its own
            # name instead of the generic one.
            code = getattr(p, "code", REASON_NO_TRACKER_ID)
            why = getattr(p, "detail", "")
            cands = getattr(p, "candidate_ids", ())
            self.crossed_without_tracker_id += 1
            if code in REID_REASONS:
                self.reid_abstained += 1
            fired_anon = True
            new_exc.append(CrossingException(
                code=code,
                detail=(f"centroid {where} ({d:+.1f}mm) with no stable track id; "
                        f"the crossing predicate cannot be evaluated, so this "
                        f"item is neither counted nor denied"
                        + (f" -- {why}" if why else "")),
                frame_index=self._frame, x_mm=p[0], y_mm=p[1],
                track_id=None, signed_dist_mm=d, candidate_ids=tuple(cands),
            ))
        if fired_anon:
            self.frames_with_untracked_out += 1

        # --- tracked centroids ------------------------------------------------
        for tid, p in clean_tracks.items():
            st = self._state.get(tid)
            if st is None:
                st = _TrackState(history=deque(maxlen=self._hist_len),
                                 first_frame=self._frame)
                self._state[tid] = st
            st.absent = 0
            st.last_pos = p

            if not self.in_limits(p):
                continue
            st.ever_in_limits = True

            d = self.project(p)[1]
            side = self._side(d)
            if side == SIDE_ON_LINE:
                # No evidence. Deliberately does NOT append to the history: a
                # centroid parked on the line must not age the debounce window.
                continue
            st.last_side = side
            if st.settled_side == SIDE_ON_LINE:
                st.settled_side = side       # first definite sighting commits

            st.history.append(side == SIDE_OUT)
            if len(st.history) < self._hist_len:
                continue
            oldest = st.history[0]
            if st.history.count(oldest) > 1:
                continue                      # not yet held for min frames
            new_side = SIDE_OUT if st.history[-1] else SIDE_IN
            if new_side == st.settled_side:
                continue                      # deviation 1: already committed here

            st.settled_side = new_side
            if new_side == SIDE_OUT:
                st.out_credits += 1
                self.out_count += 1
                crossed_out.append(tid)
            elif st.out_credits > 0:
                st.out_credits -= 1
                self.back_count += 1
                crossed_back.append(tid)
            else:
                # deviation 2: arrived from the customer's side. Not a sale
                # going backwards; net_count must never go negative.
                self.entries_from_out += 1

        # --- (b) tracks that vanished -----------------------------------------
        lost_set = {int(i) for i in lost}
        for tid in list(self._state):
            if tid in clean_tracks:
                continue
            st = self._state[tid]
            st.absent += 1
            if tid in lost_set or st.absent >= self.evict_after_frames:
                exc = self._retire(tid, st)
                if exc is not None:
                    new_exc.append(exc)

        self.exceptions.extend(new_exc)
        return CrossingResult(
            frame_index=self._frame,
            crossed_out=tuple(sorted(crossed_out)),
            crossed_back=tuple(sorted(crossed_back)),
            exceptions=tuple(new_exc),
            out_count=self.out_count,
            back_count=self.back_count,
            net_count=self.net_count,
            crossed_without_tracker_id=self.crossed_without_tracker_id,
            detected_but_never_counted=self.detected_but_never_counted,
            entries_from_out=self.entries_from_out,
            vanished_same_side=self.vanished_same_side,
            tracks_tracked=len(self._state),
            amber=self.amber,
            reid_abstained=self.reid_abstained,
        )

    def flush(self) -> CrossingResult:
        """End of session: retire every live track and judge it.

        Without this, a track still mid-crossing when the camera stops would be
        thrown away with no record at all — the same silence, one frame later.
        """
        self._frame += 1
        new_exc: list[CrossingException] = []
        for tid in list(self._state):
            exc = self._retire(tid, self._state[tid])
            if exc is not None:
                new_exc.append(exc)
        self.exceptions.extend(new_exc)
        return CrossingResult(
            frame_index=self._frame,
            crossed_out=(), crossed_back=(), exceptions=tuple(new_exc),
            out_count=self.out_count, back_count=self.back_count,
            net_count=self.net_count,
            crossed_without_tracker_id=self.crossed_without_tracker_id,
            detected_but_never_counted=self.detected_but_never_counted,
            entries_from_out=self.entries_from_out,
            vanished_same_side=self.vanished_same_side,
            tracks_tracked=0, amber=self.amber,
            reid_abstained=self.reid_abstained,
        )

    def _retire(self, tid: int, st: _TrackState) -> CrossingException | None:
        """Judge a vanished track, then forget it.

        The test is a SIDE CHANGE THAT WAS NEVER COMMITTED: the track was last
        definitely seen on the opposite side of the line from the side it is
        counted as being on. That is a crossing the debounce never confirmed —
        i.e. an item that got to the customer and was never charged for, or a
        return that never refunded.

        Honest limit, pinned by a test: a track that lived and died entirely on
        one side does NOT fire. It never crossed, so charging for it would be
        worse than the silence we are fixing, and firing here would leave the
        banner permanently amber for ordinary restocking.
        """
        del self._state[tid]
        crossed_uncommitted = (
            st.settled_side != SIDE_ON_LINE
            and st.last_side != SIDE_ON_LINE
            and st.last_side != st.settled_side
        )
        if not crossed_uncommitted:
            if st.ever_in_limits:
                self.vanished_same_side += 1
            return None
        self.detected_but_never_counted += 1
        held = sum(1 for b in st.history if b == (st.last_side == SIDE_OUT))
        going = "out to the customer" if st.last_side == SIDE_OUT else "back inside"
        return CrossingException(
            code=REASON_NEVER_COUNTED,
            detail=(f"track vanished mid-crossing ({going}): held {held} of the "
                    f"{self.min_crossing_frames} frames required, so the crossing "
                    f"was never committed and the item is unaccounted for"),
            frame_index=self._frame,
            x_mm=st.last_pos[0], y_mm=st.last_pos[1],
            track_id=tid, signed_dist_mm=None,
        )
