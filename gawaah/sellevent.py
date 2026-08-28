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


@dataclass(frozen=True)
class CrossingException:
    """One instance of a crossing the deterministic rule could not count.

    This is a RECORD, not a Python exception. It exists so that an uncounted
    sale has an object with a name, a place and a frame index attached to it,
    instead of a warning nobody reads.
    """

    code: str            # REASON_NO_TRACKER_ID | REASON_NEVER_COUNTED
    detail: str
    frame_index: int
    x_mm: float
    y_mm: float
    track_id: int | None = None
    signed_dist_mm: float | None = None

    def __str__(self) -> str:
        who = "no-id" if self.track_id is None else f"track {self.track_id}"
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
    """

    frame_index: int
    tracks: dict[int, Point]
    untracked: tuple[Point, ...]
    lost: tuple[int, ...]
    new_ids: tuple[int, ...]


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

    Args:
        max_dist_mm: association gate; further than this is never the same object.
        max_missing_frames: frames a track may coast unseen before it is lost.
        ambiguity_mm: two candidates whose distances differ by no more than this
            are indistinguishable at the plane's resolution. 0.5 mm is ~1.4 px.
    """

    def __init__(self, max_dist_mm: float = 25.0, max_missing_frames: int = 3,
                 ambiguity_mm: float = 0.5) -> None:
        if max_dist_mm <= 0:
            raise ValueError("max_dist_mm must be positive")
        if max_missing_frames < 0:
            raise ValueError("max_missing_frames must be >= 0")
        if ambiguity_mm < 0:
            raise ValueError("ambiguity_mm must be >= 0")
        self.max_dist_mm = float(max_dist_mm)
        self.max_missing_frames = int(max_missing_frames)
        self.ambiguity_mm = float(ambiguity_mm)
        self._pos: dict[int, Point] = {}
        self._missing: dict[int, int] = {}
        self._next_id = 1
        self._frame = -1
        self.abstentions = 0          # running count of refused assignments

    @property
    def live_ids(self) -> tuple[int, ...]:
        return tuple(sorted(self._pos))

    def update(self, centroids: Sequence[Point]) -> TrackerUpdate:
        self._frame += 1
        dets = [_as_point(c, "centroid") for c in centroids]
        ids = list(self._pos)
        n_d, n_t = len(dets), len(ids)

        dm = [[_dist(dets[di], self._pos[ids[ti]]) for ti in range(n_t)]
              for di in range(n_d)]

        pairs = sorted(
            (dm[di][ti], di, ti)
            for di in range(n_d) for ti in range(n_t)
            if dm[di][ti] <= self.max_dist_mm
        )

        avail_d = set(range(n_d))
        avail_t = set(range(n_t))
        assign: dict[int, int] = {}
        untracked_idx: set[int] = set()

        # Greedy over globally sorted pairs. Because the list is sorted and we
        # only ever look at still-available rows/columns, any rival distance is
        # >= the current one, so `rival - d` is the true margin.
        for d, di, ti in pairs:
            if di not in avail_d or ti not in avail_t:
                continue
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
                untracked_idx.add(di)
                avail_d.discard(di)
                for d2 in rival_d:
                    untracked_idx.add(d2)
                    avail_d.discard(d2)
                if rival_d:
                    avail_t.discard(ti)
                continue
            assign[di] = ti
            avail_d.discard(di)
            avail_t.discard(ti)

        self.abstentions += len(untracked_idx)

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

        return TrackerUpdate(
            frame_index=self._frame,
            tracks=tracks,
            untracked=tuple(dets[di] for di in sorted(untracked_idx)),
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
        self.exceptions: list[CrossingException] = []

    # ------------------------------------------------------------------ setup
    @classmethod
    def mat_exit_line(cls, inset_mm: float = 18.0, min_crossing_frames: int = 3,
                      **kw) -> "LineZone":
        """The TAKHTI's printed exit edge, OUT pointing at the customer.

        takhti is imported lazily so this module stays free of cv2/numpy: the
        crossing predicate must run identically on a server that has no camera
        stack installed (invariant 5 re-runs it server-side).
        """
        from gawaah.takhti import MAT_H_MM, MAT_W_MM
        y = MAT_H_MM - inset_mm
        return cls((0.0, y), (MAT_W_MM, y), min_crossing_frames, **kw)

    # ------------------------------------------------------------- properties
    @property
    def net_count(self) -> int:
        return self.out_count - self.back_count

    @property
    def amber(self) -> bool:
        """Latched the moment either uncounted-crossing counter fires. While
        this is True the basket total must not be shown as green."""
        return bool(self.crossed_without_tracker_id
                    or self.detected_but_never_counted)

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

        anon: list[Point] = [_as_point(c, "untracked centroid") for c in untracked]
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
            self.crossed_without_tracker_id += 1
            fired_anon = True
            new_exc.append(CrossingException(
                code=REASON_NO_TRACKER_ID,
                detail=(f"centroid {where} ({d:+.1f}mm) with no stable track id; "
                        f"the crossing predicate cannot be evaluated, so this "
                        f"item is neither counted nor denied"),
                frame_index=self._frame, x_mm=p[0], y_mm=p[1],
                track_id=None, signed_dist_mm=d,
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
