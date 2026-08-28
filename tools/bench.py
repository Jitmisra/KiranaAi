#!/usr/bin/env python3
"""S7 — THE BENCH HARNESS.

INVARIANT 9: every number in the README is generated, never typed.

This file is the machine that makes that invariant enforceable rather than
aspirational. It does three things:

  1. DISCOVER   probe the gawaah package with importlib and find out which
                modules actually exist right now. Modules are built by
                different people at different times; a bench that crashes
                because gawaah.paisa is not written yet is a bench nobody runs.
                A missing module is reported as NOT_BUILT and the run continues.

  2. MEASURE    run every benchmark whose modules ARE present, across N
                committed seeds, and report the MEAN and the WORST CASE.
                Never the best. A harness that quotes its best seed is a
                marketing document, not a measurement.

  3. VERIFY     verify_claims() re-reads a markdown file and re-checks every
                number in it against results/metrics.json. A number that has
                drifted fails. A number with no provenance fails. That check is
                what converts "we don't type numbers by hand" from a promise
                into a build step.

Usage
-----
    python tools/bench.py --seeds 5 --out results/
    python tools/bench.py --verify results/METRICS.md
    python tools/bench.py --verify README.md --lenient      # drift only

Determinism
-----------
metrics.json carries a `content_hash` over the DETERMINISTIC view of the run:
the seed list, the config, and every benchmark that declares itself
deterministic, with wall-clock timings stripped out. Two runs with the same
seeds and the same code produce the same content_hash. Timings live under
`nondeterministic` and are excluded on purpose — pretending a wall-clock
measurement is reproducible would be the same class of lie this file exists to
prevent.

Money note: nothing in this file touches the money path. The kernel benchmark
passes integer paise straight through gawaah.money.paise(); no arithmetic on an
amount happens here.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BENCH_VERSION = "1"
DEFAULT_SEEDS = 5

STATUS_OK = "OK"
STATUS_NOT_BUILT = "NOT_BUILT"
STATUS_ERROR = "ERROR"
STATUS_SKIPPED = "SKIPPED"

LOWER_BETTER = "lower_is_better"
HIGHER_BETTER = "higher_is_better"

PACKAGE = "gawaah"


# ============================================================ canonical json

def _canonical(obj: Any) -> bytes:
    """Sorted-key compact JSON. Identical semantics to gawaah.ledger.canonical.

    Imported from the ledger when it exists so there is one definition; the
    inline fallback keeps this tool runnable in a tree where the ledger has not
    been written, which is the whole point of the discovery step.
    """
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


try:  # pragma: no cover - exercised implicitly whenever the ledger is present
    from gawaah.ledger import canonical as _canonical  # type: ignore[assignment]
except Exception:
    pass


# ============================================================ discovery

@dataclass(frozen=True)
class ModuleProbe:
    name: str
    built: bool
    detail: str = ""


def probe_module(name: str) -> ModuleProbe:
    """Ask importlib whether `name` is importable. NEVER raises.

    Both halves matter. find_spec answers "is there a file"; the import answers
    "does it actually load". A module being written in another window can be on
    disk and still be a SyntaxError, and a bench that dies on someone else's
    half-saved file is a bench that blocks the team.
    """
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError, AttributeError, ModuleNotFoundError) as exc:
        return ModuleProbe(name, False, f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # a parent package that explodes on import
        return ModuleProbe(name, False, f"{type(exc).__name__}: {exc}")
    if spec is None:
        return ModuleProbe(name, False, "no module named " + name)
    try:
        importlib.import_module(name)
    except Exception as exc:
        return ModuleProbe(name, False, f"import failed: {type(exc).__name__}: {exc}")
    except SystemExit as exc:  # a module that calls sys.exit at import time
        return ModuleProbe(name, False, f"import called sys.exit({exc.code})")
    return ModuleProbe(name, True)


def package_modules(package: str = PACKAGE) -> tuple[str, ...]:
    """Every submodule of `package` that is on disk RIGHT NOW.

    Enumerated rather than hard-coded. Four modules landed in this package
    between two runs of this file while it was being written, and a roster
    someone has to remember to update is a roster that goes stale.
    """
    try:
        pkg = importlib.import_module(package)
        import pkgutil
    except Exception:
        return ()
    paths = getattr(pkg, "__path__", None)
    if not paths:
        return ()
    try:
        return tuple(sorted(f"{package}.{m.name}" for m in pkgutil.iter_modules(paths)))
    except OSError:
        return ()


def discover(names: Sequence[str]) -> dict[str, ModuleProbe]:
    return {n: probe_module(n) for n in sorted(set(names))}


# ============================================================ benchmark plumbing

@dataclass
class Samples:
    """What one benchmark hands back.

    per_seed maps seed -> that seed's own samples, so the report can show that
    a bad worst case came from one seed rather than from everywhere.
    """
    per_seed: dict[int, list[float]] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)
    nondeterministic: dict[str, Any] = field(default_factory=dict)

    def all(self) -> list[float]:
        out: list[float] = []
        for seed in sorted(self.per_seed):
            out.extend(self.per_seed[seed])
        return out


BenchFn = Callable[[list[int], float], Samples]


@dataclass(frozen=True)
class Bench:
    name: str
    modules: tuple[str, ...]
    unit: str
    polarity: str
    decimals: int
    deterministic: bool
    what: str
    fn: BenchFn


def _round(v: float, nd: int = 9) -> float:
    """Round before serialising so the JSON is byte-stable across platforms."""
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return v
    if isinstance(v, int):
        return v
    if math.isnan(v) or math.isinf(v):
        return v
    return round(float(v), nd)


def _percentile(sorted_vals: list[float], q: float) -> float:
    """Nearest-rank percentile. No interpolation: an interpolated p95 invents a
    value that was never measured, and this file only reports measurements."""
    if not sorted_vals:
        return float("nan")
    k = max(1, math.ceil(q * len(sorted_vals)))
    return sorted_vals[min(k, len(sorted_vals)) - 1]


def summarise(samples: list[float], polarity: str) -> dict[str, Any]:
    if not samples:
        return {"n": 0}
    s = sorted(samples)
    hi = polarity == LOWER_BETTER
    return {
        "n": len(s),
        "mean": _round(statistics.fmean(s)),
        "worst": _round(s[-1] if hi else s[0]),
        "best": _round(s[0] if hi else s[-1]),
        "median": _round(statistics.median(s)),
        "p95": _round(_percentile(s, 0.95) if hi else _percentile(s, 0.05)),
        "stdev": _round(statistics.pstdev(s)),
    }


def _n(base: int, scale: float, floor: int = 1) -> int:
    return max(floor, int(round(base * scale)))


# ============================================================ B1 plane RMSE

def _synth_frame(px_per_mm: float, tilt: tuple[float, float],
                 size: tuple[int, int], noise: float, seed: int, fit: float):
    """Render the TAKHTI into a synthetic camera frame with a known pose.

    Deliberately self-contained rather than importing tests.test_plane: a
    measurement tool that stops working when a test file is mid-edit is a tool
    that gets deleted. The projection model is the same one the plane tests
    use — pinhole, mat rotated about its centre, focal length chosen so the mat
    fills `fit` of the sensor.
    """
    import cv2
    import numpy as np
    from gawaah.takhti import render_takhti

    mat = render_takhti(px_per_mm)
    h, w = mat.shape
    W, H = size
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)

    ax, ay = np.radians(tilt[0]), np.radians(tilt[1])
    half_w, half_h = w / 2, h / 2
    pts3d = np.array([[-half_w, -half_h, 0], [half_w, -half_h, 0],
                      [half_w, half_h, 0], [-half_w, half_h, 0]], np.float64)
    Rx = np.array([[1, 0, 0], [0, np.cos(ax), -np.sin(ax)], [0, np.sin(ax), np.cos(ax)]])
    Ry = np.array([[np.cos(ay), 0, np.sin(ay)], [0, 1, 0], [-np.sin(ay), 0, np.cos(ay)]])
    pts3d = pts3d @ Rx.T @ Ry.T

    f = max(w, h) * 2.2
    dist = f * max(w / (fit * W), h / (fit * H))
    proj = [[f * X / (dist + Z) + W / 2, f * Y / (dist + Z) + H / 2]
            for X, Y, Z in pts3d]
    dst = np.array(proj, np.float32)

    M = cv2.getPerspectiveTransform(src, dst)
    frame = np.full((H, W), 235, np.uint8)
    warped = cv2.warpPerspective(mat, M, (W, H), borderValue=235)
    mask = cv2.warpPerspective(np.full_like(mat, 255), M, (W, H), borderValue=0)
    frame[mask > 128] = warped[mask > 128]

    if noise > 0:
        rng = np.random.default_rng(seed)
        frame = np.clip(frame.astype(np.int16)
                        + rng.normal(0, noise, frame.shape), 0, 255).astype(np.uint8)
    return frame


# Fixed tilt grid, plus seed-drawn tilts. All inside the mat-lock gate
# (persp_index < 0.040, i.e. about 8 degrees) so a refusal here is a real
# failure and not the gate doing its job.
_FIXED_TILTS = ((0.0, 0.0), (3.0, 0.0), (0.0, 3.0), (4.0, 4.0), (-5.0, 2.0))
_NOISE_LEVELS = (0.0, 3.0, 6.0)


def _expected_corner_buffer_px():
    """Ideal buffer position of all 16 marker CORNERS, in ArUco's TL,TR,BR,BL
    order. Verified against a fronto-parallel render: the detector returns the
    marker's corners in that order in mat coordinates."""
    import numpy as np
    from gawaah.takhti import MARKER_IDS, MARKER_MM, marker_centres_mm, mm_to_buffer
    h = MARKER_MM / 2.0
    pts = []
    for cx, cy in marker_centres_mm():
        pts += [[cx - h, cy - h], [cx + h, cy - h], [cx + h, cy + h], [cx - h, cy + h]]
    return {i: mm_to_buffer(np.array(pts[4 * k:4 * k + 4], np.float64))
            for k, i in enumerate(MARKER_IDS)}


def _holdout_rmse_px(eng, frame, H) -> float | None:
    """RMSE of the 16 marker corners after transport through H.

    HELD OUT ON PURPOSE. MatLock.reproj_rmse_px fits a homography to four
    marker CENTRES and then measures those same four centres: four points
    determine a homography exactly, so that residual is ~2e-5 px at every tilt
    and every noise level and measures nothing but float round-off. Measured
    across the whole sweep its standard deviation was exactly 0.0, which is how
    the degeneracy was caught. The corners were not in the fit, so their error
    is a real statement about the plane.
    """
    import cv2
    import numpy as np
    corners, ids, _ = eng._det.detectMarkers(
        frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    if ids is None:
        return None
    expect = _expected_corner_buffer_px()
    sq: list[float] = []
    for i, quad in zip(ids.flatten(), corners):
        i = int(i)
        if i not in expect:
            continue
        got = cv2.perspectiveTransform(
            quad.reshape(-1, 1, 2).astype(np.float64), H).reshape(4, 2)
        sq.extend(((got - expect[i]) ** 2).sum(axis=1).tolist())
    if not sq:
        return None
    return float(np.sqrt(np.mean(sq)))


def bench_plane_reproj(seeds: list[int], scale: float) -> Samples:
    import numpy as np
    from gawaah.takhti import PX_PER_MM, PlaneEngine

    eng = PlaneEngine()
    out = Samples()
    refusals: list[str] = []
    persp: list[float] = []
    scale_errs: list[float] = []
    fit_resid: list[float] = []
    attempted = 0
    t0 = time.perf_counter()

    # The renderer rounds each marker onto the pixel grid, so even an untilted,
    # noiseless frame carries a small held-out error. Measure it rather than
    # subtract it, so the reported worst case is never flattered.
    base_frame = _synth_frame(4.0, (0.0, 0.0), (960, 1280), 0.0, 0, 0.82)
    base_lock = eng.detect(base_frame)
    baseline = (_holdout_rmse_px(eng, base_frame, base_lock.H)
                if base_lock.H is not None else None)

    n_random = _n(4, scale)
    for seed in seeds:
        rng = np.random.default_rng(seed)
        tilts = list(_FIXED_TILTS[: _n(len(_FIXED_TILTS), scale)])
        tilts += [(float(rng.uniform(-5, 5)), float(rng.uniform(-5, 5)))
                  for _ in range(n_random)]
        vals: list[float] = []
        for i, tilt in enumerate(tilts):
            sigma = _NOISE_LEVELS[i % len(_NOISE_LEVELS)]
            frame = _synth_frame(4.0, tilt, (960, 1280), sigma, seed * 1000 + i, 0.82)
            lock = eng.detect(frame)
            attempted += 1
            if not lock.locked or lock.H is None:
                refusals.append(f"seed={seed} tilt=({tilt[0]:.2f},{tilt[1]:.2f}) "
                                f"sigma={sigma}: {lock.reason}")
                continue
            rmse = _holdout_rmse_px(eng, frame, lock.H)
            if rmse is None:
                refusals.append(f"seed={seed} tilt={tilt}: no corners to hold out")
                continue
            vals.append(rmse)
            persp.append(float(lock.persp_index))
            scale_errs.append(float(lock.scale_err))
            fit_resid.append(float(lock.reproj_rmse_px))
        out.per_seed[seed] = vals

    locked = sum(len(v) for v in out.per_seed.values())
    allv = out.all()
    out.detail = {
        "frames_attempted": attempted,
        "frames_locked": locked,
        "lock_rate": _round(locked / attempted if attempted else 0.0),
        "refusals": refusals[:10],
        "held_out_points_per_frame": 16,
        "worst_err_mm": _round(max(allv) / PX_PER_MM) if allv else None,
        "baseline_untilted_noiseless_px": _round(baseline) if baseline else None,
        "fit_residual_worst_px": _round(max(fit_resid)) if fit_resid else None,
        "fit_residual_stdev_px": (_round(statistics.pstdev(fit_resid))
                                  if len(fit_resid) > 1 else None),
        "max_persp_index": _round(max(persp)) if persp else None,
        "max_scale_err": _round(max(scale_errs)) if scale_errs else None,
        "noise_sigmas": list(_NOISE_LEVELS),
    }
    out.nondeterministic = {"wall_s": _round(time.perf_counter() - t0, 3)}
    return out


# ============================================================ B2 placement mm

_SS = 4          # supersample factor for coverage compositing
_PAPER = 200     # white A3 under the demo's exposure
_DARK = 55
_BRIGHT = 245
_SIZES = ((210.0, 30.0), (150.0, 100.0), (120.0, 80.0), (60.0, 40.0), (25.0, 15.0))


def _empty_mat():
    import cv2
    import numpy as np
    from gawaah.takhti import BUF_H, BUF_W, render_takhti
    mat = render_takhti(4.0)
    buf = cv2.resize(mat, (BUF_W, BUF_H), interpolation=cv2.INTER_AREA)
    return np.clip(buf.astype(np.float32) * (_PAPER / 255.0) + 9.0,
                   0, 255).astype("uint8")


def _paste(ref, cx: float, cy: float, long_mm: float, short_mm: float,
           deg: float, val: int):
    """Composite an oriented rectangle of KNOWN mm size with 4x coverage AA.

    Coverage compositing, not fillConvexPoly: a hard rasteriser quantises the
    truth to the pixel grid and would put a ~0.4 mm floor under every error
    this benchmark reports, i.e. we would be measuring the harness.
    """
    import cv2
    import numpy as np
    from gawaah.takhti import BUF_H, BUF_W, mm_to_buffer

    t = np.radians(deg)
    R = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
    hl, hs = long_mm / 2.0, short_mm / 2.0
    local = np.array([[-hl, -hs], [hl, -hs], [hl, hs], [-hl, hs]], np.float64)
    poly = mm_to_buffer(local @ R.T + np.array([cx, cy]))

    big = np.zeros((BUF_H * _SS, BUF_W * _SS), np.uint8)
    cv2.fillConvexPoly(big, np.rint(poly * _SS).astype(np.int32), 255)
    cov = cv2.resize(big, (BUF_W, BUF_H),
                     interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    outf = ref.astype(np.float32) * (1.0 - cov) + float(val) * cov
    return np.clip(np.rint(outf), 0, 255).astype("uint8")


def _noisy(img, sigma: float, seed: int):
    import numpy as np
    if sigma <= 0:
        return img
    rng = np.random.default_rng(seed)
    return np.clip(img.astype(np.float32) + rng.normal(0, sigma, img.shape),
                   0, 255).astype("uint8")


def bench_placement_footprint(seeds: list[int], scale: float) -> Samples:
    import numpy as np
    from gawaah.placement import REASON_OK, PlacementDetector
    from gawaah.takhti import MAT_H_MM, MAT_W_MM

    ref = _empty_mat()
    centre = (MAT_W_MM / 2, MAT_H_MM / 2)
    out = Samples()
    long_err: list[float] = []
    short_err: list[float] = []
    centre_err: list[float] = []
    angle_err: list[float] = []
    unmeasurable = 0
    missed = 0
    t0 = time.perf_counter()

    per_seed_n = _n(10, scale, floor=2)
    for seed in seeds:
        rng = np.random.default_rng(10_000 + seed)
        vals: list[float] = []
        for i in range(per_seed_n):
            L, S = _SIZES[int(rng.integers(len(_SIZES)))]
            deg = float(rng.uniform(0.0, 180.0))
            val = _DARK if rng.integers(2) == 0 else _BRIGHT
            sigma = float((0.0, 3.0, 5.0)[i % 3])

            det = PlacementDetector(_noisy(ref, sigma, seed * 977 + 1))
            frame = _noisy(_paste(ref, centre[0], centre[1], L, S, deg, val),
                           sigma, seed * 131 + i)
            placements = det.update(frame)
            if len(placements) != 1 or not placements[0].measurable:
                missed += 1
                if placements and not placements[0].measurable:
                    unmeasurable += 1
                continue
            p = placements[0]
            if p.reason != REASON_OK:
                unmeasurable += 1
                continue
            dl = abs(p.long_edge_mm - L)
            ds = abs(p.short_edge_mm - S)
            long_err.append(dl)
            short_err.append(ds)
            centre_err.append(float(math.hypot(p.centre_mm[0] - centre[0],
                                               p.centre_mm[1] - centre[1])))
            if L != S:
                angle_err.append(abs((p.angle_deg - deg % 180.0 + 90.0) % 180.0 - 90.0))
            # The footprint error is the worse of the two edges. A packet whose
            # long edge is right and short edge is 2 mm out is still 2 mm out.
            vals.append(max(dl, ds))
        out.per_seed[seed] = vals

    out.detail = {
        "objects_measured": len(long_err),
        "objects_missed": missed,
        "objects_unmeasurable": unmeasurable,
        "long_edge_mean_mm": _round(statistics.fmean(long_err)) if long_err else None,
        "long_edge_worst_mm": _round(max(long_err)) if long_err else None,
        "short_edge_mean_mm": _round(statistics.fmean(short_err)) if short_err else None,
        "short_edge_worst_mm": _round(max(short_err)) if short_err else None,
        "centre_worst_mm": _round(max(centre_err)) if centre_err else None,
        "angle_worst_deg": _round(max(angle_err)) if angle_err else None,
        "sizes_mm": [list(s) for s in _SIZES],
    }
    out.nondeterministic = {"wall_s": _round(time.perf_counter() - t0, 3)}
    return out


# ============================================================ B3 sell recall

def _event_path(kind: str, rng, line_y: float) -> tuple[list[tuple[float, float]], int]:
    """Build one object's centroid path in mm, and its GROUND-TRUTH out-count.

    Ground truth is the script, not the detector's opinion of the script, which
    is the only way recall means anything.
    """
    x = float(rng.uniform(30.0, 267.0))
    step = float(rng.uniform(6.0, 12.0))
    start = line_y - float(rng.uniform(50.0, 80.0))

    if kind == "sale":
        end = line_y + float(rng.uniform(25.0, 45.0))
        ys = list(_arange(start, end, step))
        return [(x, y) for y in ys], 1
    if kind == "return":
        top = line_y + float(rng.uniform(20.0, 35.0))
        ys = list(_arange(start, top, step)) + list(_arange(top, start, -step))
        return [(x, y) for y in ys], 1
    if kind == "hover":
        # approaches, stops just short of the line, retreats. Not a sale.
        top = line_y - float(rng.uniform(2.5, 8.0))
        ys = list(_arange(start, top, step)) + list(_arange(top, start, -step))
        return [(x, y) for y in ys], 0
    # "browse": drifts parallel on the shopkeeper's side
    y = line_y - float(rng.uniform(20.0, 60.0))
    return [(x + k * step, y) for k in range(10)], 0


def _arange(a: float, b: float, step: float) -> list[float]:
    out, v = [], a
    if step > 0:
        while v < b:
            out.append(v)
            v += step
    else:
        while v > b:
            out.append(v)
            v += step
    out.append(b)
    return out


_EVENT_MIX = ("sale", "sale", "sale", "return", "hover", "browse")


def bench_sellevent_recall(seeds: list[int], scale: float) -> Samples:
    import numpy as np
    from gawaah.sellevent import CentroidTracker, LineZone
    from gawaah.takhti import MAT_H_MM

    out = Samples()
    line_y = MAT_H_MM - 18.0
    events_total = 0
    true_out_total = 0
    counted_total = 0
    false_positives = 0
    amber_events = 0
    clean_hits = clean_true = 0
    drop_hits = drop_true = 0
    loud_misses = silent_misses = 0
    entries_from_out = vanished_same_side = 0
    codes: dict[str, int] = {}
    t0 = time.perf_counter()

    per_seed_events = _n(24, scale, floor=6)
    for seed in seeds:
        rng = np.random.default_rng(20_000 + seed)
        tracker = CentroidTracker(max_dist_mm=25.0, max_missing_frames=3)
        zone = LineZone.mat_exit_line(
            min_crossing_frames=3, evict_after_frames=tracker.max_missing_frames + 1
        )
        seed_hits = seed_true = 0
        for _e in range(per_seed_events):
            kind = _EVENT_MIX[int(rng.integers(len(_EVENT_MIX)))]
            path, truth = _event_path(kind, rng, line_y)
            # One event in six loses the object for longer than the tracker
            # will hold it. That is a real failure mode, it is included on
            # purpose, and it is what drags recall below 1.0.
            long_drop = bool(rng.integers(6) == 0)
            drop_at = int(rng.integers(1, max(2, len(path) - 1)))
            drop_len = 5 if long_drop else 1

            before_out = zone.out_count
            before_exc = len(zone.exceptions)
            for fi, (px, py) in enumerate(path):
                if drop_at <= fi < drop_at + drop_len:
                    upd = tracker.update([])
                else:
                    jx = float(rng.normal(0.0, 0.6))
                    jy = float(rng.normal(0.0, 0.6))
                    upd = tracker.update([(px + jx, py + jy)])
                zone.update(upd.tracks, untracked=upd.untracked, lost=upd.lost)
            for _ in range(tracker.max_missing_frames + 2):  # let it be retired
                upd = tracker.update([])
                zone.update(upd.tracks, untracked=upd.untracked, lost=upd.lost)

            got = zone.out_count - before_out
            events_total += 1
            true_out_total += truth
            counted_total += got
            if got > truth:
                false_positives += got - truth
            new_exc = zone.exceptions[before_exc:]
            for x in new_exc:
                codes[x.code] = codes.get(x.code, 0) + 1
            if new_exc:
                amber_events += 1

            if truth:
                hit = 1.0 if got >= 1 else 0.0
                seed_hits += int(hit)
                seed_true += 1
                if not hit:
                    # A miss that raised is an abstention: the shopkeeper is
                    # told the total cannot be trusted. A miss that raised
                    # NOTHING is a sale that left the shop with no trace, which
                    # is a different and much worse animal. Counted apart.
                    if new_exc:
                        loud_misses += 1
                    else:
                        silent_misses += 1
                if long_drop:
                    drop_true += 1
                    drop_hits += int(hit)
                else:
                    clean_true += 1
                    clean_hits += int(hit)
        entries_from_out += zone.entries_from_out
        vanished_same_side += zone.vanished_same_side
        # One sample per seed: that seed's recall. A per-EVENT binary sample
        # would make `worst` degenerate to 0.0 the moment any single sale was
        # missed, which is a true statement that tells you nothing about how
        # bad the worst seed actually was.
        out.per_seed[seed] = [seed_hits / seed_true] if seed_true else []

    hits = clean_hits + drop_hits
    out.detail = {
        "events": events_total,
        "events_per_seed": per_seed_events,
        "sales_counted": hits,
        "recall_pooled": _round(hits / true_out_total) if true_out_total else None,
        "true_out_crossings": true_out_total,
        "counted_out_crossings": counted_total,
        "false_positive_crossings": false_positives,
        "amber_events": amber_events,
        "misses_that_raised": loud_misses,
        "misses_that_were_silent": silent_misses,
        "entries_from_out": entries_from_out,
        "vanished_same_side": vanished_same_side,
        "recall_no_dropout": _round(clean_hits / clean_true) if clean_true else None,
        "recall_with_long_dropout": _round(drop_hits / drop_true) if drop_true else None,
        "exception_codes": dict(sorted(codes.items())),
        "jitter_sigma_mm": 0.6,
    }
    out.nondeterministic = {"wall_s": _round(time.perf_counter() - t0, 3)}
    return out


# ============================================================ B4 kernel

def bench_kernel_exactly_once(seeds: list[int], scale: float) -> Samples:
    """Race N threads at one idempotency key and count how many debits appear.

    The sample is 1.0 for a round where exactly one intent existed and exactly
    one caller was allowed to charge, 0.0 otherwise. There is no partial credit:
    two debits for one basket is not 50% correct.
    """
    from gawaah.clock import VirtualClock
    from gawaah.kernel import IllegalTransition, Kernel
    from gawaah.ledger import Ledger, verify
    from gawaah.money import paise

    out = Samples()
    threads_per_round = _n(24, scale, floor=4)
    rounds_per_seed = _n(2, scale, floor=1)
    dup_intents = 0
    extra_winners = 0
    ledger_bad = 0
    audit_lines = 0
    t0 = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="gawaah-bench-kernel-") as tmp:
        for seed in seeds:
            db = Path(tmp) / f"k{seed}.sqlite3"
            lg_path = Path(tmp) / f"k{seed}.jsonl"
            kernel = Kernel(db, VirtualClock(step_ms=1), Ledger(lg_path))
            vals: list[float] = []
            for r in range(rounds_per_seed):
                session = f"bench-s{seed}-r{r}"
                amount = int(paise(19900 + seed * 100 + r))
                barrier = threading.Barrier(threads_per_round)
                lock = threading.Lock()
                nonces: list[str] = []
                errors: list[str] = []

                def create() -> None:
                    barrier.wait()
                    try:
                        it = kernel.create_intent(session, amount)
                        with lock:
                            nonces.append(it.nonce)
                    except Exception as exc:  # noqa: BLE001 - recorded, not hidden
                        with lock:
                            errors.append(f"{type(exc).__name__}: {exc}")

                ts = [threading.Thread(target=create) for _ in range(threads_per_round)]
                for t in ts:
                    t.start()
                for t in ts:
                    t.join()

                unique = set(nonces)
                dup_intents += max(0, len(unique) - 1)

                winners: list[str] = []
                if unique:
                    nonce = next(iter(unique))
                    barrier2 = threading.Barrier(threads_per_round)

                    def call() -> None:
                        barrier2.wait()
                        try:
                            kernel.mark_calling(nonce)
                            with lock:
                                winners.append(nonce)
                        except IllegalTransition:
                            pass
                        except Exception as exc:  # noqa: BLE001
                            with lock:
                                errors.append(f"{type(exc).__name__}: {exc}")

                    ts2 = [threading.Thread(target=call)
                           for _ in range(threads_per_round)]
                    for t in ts2:
                        t.start()
                    for t in ts2:
                        t.join()
                extra_winners += max(0, len(winners) - 1)

                # Kernel.count is a METHOD, not a property. Comparing the bound
                # method to an int is silently False forever, which is exactly
                # what this benchmark reported on its first run.
                ok_round = (len(unique) == 1 and len(winners) == 1
                            and not errors and kernel.count() == r + 1)
                vals.append(1.0 if ok_round else 0.0)

            ok, n_lines, _head, _err = verify(lg_path)
            audit_lines += n_lines
            if not ok:
                ledger_bad += 1
            out.per_seed[seed] = vals

    out.detail = {
        "threads_per_round": threads_per_round,
        "rounds_per_seed": rounds_per_seed,
        "rounds_total": rounds_per_seed * len(seeds),
        "duplicate_intents": dup_intents,
        "extra_callers_admitted": extra_winners,
        "audit_lines_written": audit_lines,
        "ledger_chains_broken": ledger_bad,
    }
    out.nondeterministic = {"wall_s": _round(time.perf_counter() - t0, 3)}
    return out


# ============================================================ B5 ledger verify

def bench_ledger_verify(seeds: list[int], scale: float) -> Samples:
    """Lines per second for a from-genesis chain re-verification.

    Timing, therefore declared non-deterministic and excluded from the content
    hash. The line COUNT is deterministic and is asserted by the tests.
    """
    from gawaah.clock import VirtualClock
    from gawaah.ledger import Ledger, verify

    out = Samples()
    lines = _n(4000, scale, floor=200)
    repeats = _n(3, scale, floor=2)
    build_s: list[float] = []
    bytes_total = 0
    all_ok = True
    t0 = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="gawaah-bench-ledger-") as tmp:
        for seed in seeds:
            path = Path(tmp) / f"chain{seed}.jsonl"
            clock = VirtualClock(step_ms=1)
            lg = Ledger(path)
            b0 = time.perf_counter()
            for i in range(lines):
                lg.append(ts=clock.now_iso(), module="bench", event="synthetic",
                          seq=i, seed=seed, amount_paise=100 + i)
            build_s.append(time.perf_counter() - b0)
            bytes_total += path.stat().st_size

            vals: list[float] = []
            for _ in range(repeats):
                v0 = time.perf_counter()
                ok, n, _head, err = verify(path)
                dt = time.perf_counter() - v0
                all_ok = all_ok and ok and n == lines and err is None
                vals.append(n / dt if dt > 0 else float("inf"))
            out.per_seed[seed] = vals

    out.detail = {
        "lines_per_chain": lines,
        "chains": len(seeds),
        "repeats_per_chain": repeats,
        "all_chains_verified": all_ok,
    }
    out.nondeterministic = {
        "wall_s": _round(time.perf_counter() - t0, 3),
        "mean_build_s": _round(statistics.fmean(build_s), 4) if build_s else None,
        "bytes_written": bytes_total,
    }
    return out


# ============================================================ registry

BENCHES: tuple[Bench, ...] = (
    Bench("plane_reproj_rmse_px", ("gawaah.takhti",), "px", LOWER_BETTER, 4, True,
          "marker reprojection RMSE across tilts and sensor noise",
          bench_plane_reproj),
    Bench("placement_footprint_err_mm", ("gawaah.placement", "gawaah.takhti"),
          "mm", LOWER_BETTER, 3, True,
          "worse of the two measured edges vs the pasted truth",
          bench_placement_footprint),
    Bench("sellevent_recall", ("gawaah.sellevent", "gawaah.takhti"),
          "fraction", HIGHER_BETTER, 4, True,
          "scripted OUT crossings the zone actually counted",
          bench_sellevent_recall),
    Bench("kernel_exactly_once", ("gawaah.kernel", "gawaah.ledger", "gawaah.clock",
                                  "gawaah.money"),
          "fraction", HIGHER_BETTER, 4, True,
          "rounds where racing threads produced exactly one debit",
          bench_kernel_exactly_once),
    Bench("ledger_verify_throughput", ("gawaah.ledger", "gawaah.clock"),
          "lines/s", HIGHER_BETTER, 0, False,
          "from-genesis chain re-verification rate",
          bench_ledger_verify),
)

BENCH_BY_NAME = {b.name: b for b in BENCHES}


# ============================================================ the run

def git_sha(root: Path = ROOT) -> str:
    try:
        sha = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if sha.returncode != 0:
            return "unknown"
        head = sha.stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return head + ("-dirty" if dirty.stdout.strip() else "")
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def run_benchmarks(seeds: list[int], *, scale: float = 1.0,
                   only: Sequence[str] | None = None,
                   clock: Any | None = None,
                   log: Callable[[str], None] = lambda _s: None) -> dict[str, Any]:
    """Run every benchmark whose modules are present. Never raises for a
    missing or broken module — that is the whole discovery contract."""
    # Everything on disk, plus everything a benchmark depends on. The second
    # half matters: a benchmark naming a module nobody has written yet must
    # report NOT BUILT, and a module that is absent cannot be enumerated.
    wanted = set(package_modules()) | {m for b in BENCHES for m in b.modules}
    probes = discover(sorted(wanted))

    results: dict[str, Any] = {}
    for b in BENCHES:
        block: dict[str, Any] = {
            "what": b.what,
            "modules": list(b.modules),
            "unit": b.unit,
            "polarity": b.polarity,
            "decimals": b.decimals,
            "deterministic": b.deterministic,
        }
        if only and b.name not in only:
            block["status"] = STATUS_SKIPPED
            block["detail"] = {"note": "not selected by --only"}
            results[b.name] = block
            continue

        missing = [m for m in b.modules if not probes[m].built]
        if missing:
            block["status"] = STATUS_NOT_BUILT
            block["missing_modules"] = missing
            block["detail"] = {m: probes[m].detail for m in missing}
            log(f"  {b.name:<28} NOT BUILT  (missing: {', '.join(missing)})")
            results[b.name] = block
            continue

        log(f"  {b.name:<28} running ...")
        try:
            samples = b.fn(list(seeds), scale)
        except Exception as exc:  # noqa: BLE001 - a broken bench must not kill the run
            block["status"] = STATUS_ERROR
            block["error"] = f"{type(exc).__name__}: {exc}"
            block["detail"] = {}
            log(f"  {b.name:<28} ERROR  {type(exc).__name__}: {exc}")
            results[b.name] = block
            continue

        block["status"] = STATUS_OK
        block.update(summarise(samples.all(), b.polarity))
        block["per_seed"] = {
            str(s): summarise(v, b.polarity) for s, v in sorted(samples.per_seed.items())
        }
        block["worst_seed"] = _worst_seed(samples, b.polarity)
        block["detail"] = samples.detail
        block["nondeterministic"] = samples.nondeterministic
        results[b.name] = block
        log(f"  {b.name:<28} OK  mean={_fmt(block.get('mean'), b.decimals)} "
            f"worst={_fmt(block.get('worst'), b.decimals)} {b.unit}")

    now = clock.now_iso() if clock is not None else _real_now()
    metrics: dict[str, Any] = {
        "generated_at": now,
        "git_sha": git_sha(),
        "seeds": list(seeds),
        "bench_version": BENCH_VERSION,
        "config": {"scale": scale, "only": sorted(only) if only else []},
        "modules": {n: {"built": p.built, "detail": p.detail}
                    for n, p in sorted(probes.items())},
        "benchmarks": results,
    }
    metrics["content_hash"] = content_hash(metrics)
    return metrics


def _real_now() -> str:
    from gawaah.clock import RealClock
    return RealClock().now_iso()


def _worst_seed(samples: Samples, polarity: str) -> int | None:
    best: tuple[float, int] | None = None
    for seed, vals in samples.per_seed.items():
        if not vals:
            continue
        v = max(vals) if polarity == LOWER_BETTER else min(vals)
        key = v if polarity == LOWER_BETTER else -v
        if best is None or key > best[0]:
            best = (key, seed)
    return None if best is None else best[1]


def deterministic_view(metrics: dict[str, Any]) -> dict[str, Any]:
    """Everything a rerun with the same seeds must reproduce exactly.

    Drops generated_at (wall clock), git_sha (a commit is not a measurement),
    the module inventory (another agent finishing a module is not drift in
    ours), and every `nondeterministic` block.
    """
    benches: dict[str, Any] = {}
    for name, block in metrics.get("benchmarks", {}).items():
        if not block.get("deterministic", False):
            continue
        benches[name] = {k: v for k, v in block.items() if k != "nondeterministic"}
    return {
        "seeds": metrics.get("seeds"),
        "bench_version": metrics.get("bench_version"),
        "config": metrics.get("config"),
        "benchmarks": benches,
    }


def content_hash(metrics: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(deterministic_view(metrics))).hexdigest()


# ============================================================ formatting

def _fmt(v: Any, decimals: int) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        if math.isinf(v):
            return "inf"
        return f"{v:.{decimals}f}"
    return str(v)


_BENCH_COLUMNS = ("benchmark", "status", "unit", "n", "mean", "worst", "best",
                  "median", "p95", "worst seed")
_SEED_COLUMNS_HEAD = ("benchmark", "status")
_MODULE_COLUMNS = ("module", "built")


def bench_row(name: str, metrics: dict[str, Any]) -> list[str]:
    b = metrics["benchmarks"][name]
    d = int(b.get("decimals", 4))
    status = b.get("status", STATUS_ERROR)
    if status != STATUS_OK:
        return [name, status, b.get("unit", "-"), "n/a", "n/a", "n/a", "n/a",
                "n/a", "n/a", "n/a"]
    return [
        name, status, b.get("unit", "-"), _fmt(b.get("n"), 0),
        _fmt(b.get("mean"), d), _fmt(b.get("worst"), d), _fmt(b.get("best"), d),
        _fmt(b.get("median"), d), _fmt(b.get("p95"), d),
        _fmt(b.get("worst_seed"), 0),
    ]


def seed_row(name: str, metrics: dict[str, Any]) -> list[str]:
    b = metrics["benchmarks"][name]
    d = int(b.get("decimals", 4))
    status = b.get("status", STATUS_ERROR)
    cells = [name, status]
    if status != STATUS_OK:
        cells += ["n/a" for _ in metrics.get("seeds", [])]
        return cells
    per = b.get("per_seed", {})
    for s in metrics.get("seeds", []):
        blk = per.get(str(s))
        cells.append("n/a" if not blk or blk.get("n", 0) == 0
                     else _fmt(blk.get("worst"), d))
    return cells


def module_row(name: str, metrics: dict[str, Any]) -> list[str]:
    m = metrics["modules"][name]
    return [f"`{name}`", "BUILT" if m["built"] else "NOT BUILT"]


ROW_BUILDERS: dict[str, Callable[[str, dict[str, Any]], list[str]]] = {
    "bench": bench_row,
    "seed": seed_row,
    "module": module_row,
}


def _md_row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _ok(metrics: dict[str, Any], name: str) -> bool:
    return metrics.get("benchmarks", {}).get(name, {}).get("status") == STATUS_OK


def _claim(metrics: dict[str, Any], path: str, decimals: int) -> str:
    """Render a value straight out of metrics.json with its own anchor attached.

    Prose numbers go through here so that a sentence cannot drift from the
    table above it; there is no code path in this file that lets a human type
    a digit into the markdown.
    """
    return f"`{_fmt(resolve_path(metrics, path), decimals)}` <!--@ {path} -->"


def render_markdown(metrics: dict[str, Any]) -> str:
    seeds = metrics.get("seeds", [])
    L: list[str] = []
    A = L.append

    A("# GAWAAH — measured metrics")
    A("")
    A("<!-- GENERATED BY tools/bench.py. Do not hand-edit. Every number below is")
    A("     re-checked against results/metrics.json by")
    A("     `python tools/bench.py --verify results/METRICS.md`, which fails on drift. -->")
    A("")
    A("Nothing on this page was typed by a person. Each row is rebuilt from")
    A("`results/metrics.json` during verification and compared character for")
    A("character, so a stale number is a build failure rather than a rounding")
    A("story.")
    A("")
    A(f"- generated at `{metrics.get('generated_at')}` <!--@ generated_at -->")
    A(f"- git sha `{metrics.get('git_sha')}` <!--@ git_sha -->")
    A(f"- seeds `{list(seeds)}` <!--@ seeds -->")
    A(f"- bench version `{metrics.get('bench_version')}` <!--@ bench_version -->")
    A(f"- deterministic content hash `{metrics.get('content_hash')}` <!--@ content_hash -->")
    A("")
    A("## Results")
    A("")
    A("`worst` is the worst single sample across every seed, never the best and")
    A("never only the mean.")
    A("")
    A(_md_row(_BENCH_COLUMNS) + " <!-- bench:ignore -->")   # "p95" is a header
    A(_md_row(["---"] * len(_BENCH_COLUMNS)))
    for name in metrics.get("benchmarks", {}):
        A(_md_row(bench_row(name, metrics)) + f" <!--@row bench:{name} -->")
    A("")
    A("## Worst case per seed")
    A("")
    A(_md_row(list(_SEED_COLUMNS_HEAD) + [f"seed {s}" for s in seeds])
      + " <!-- bench:ignore -->")
    A(_md_row(["---"] * (len(_SEED_COLUMNS_HEAD) + len(seeds))))
    for name in metrics.get("benchmarks", {}):
        A(_md_row(seed_row(name, metrics)) + f" <!--@row seed:{name} -->")
    A("")
    A("## What each benchmark measures")
    A("")
    for name, b in metrics.get("benchmarks", {}).items():
        A(f"- **{name}** — {b.get('what')} "
          f"(`{', '.join(b.get('modules', []))}`, {b.get('polarity')})")
    A("")
    A("## Module inventory")
    A("")
    A("A benchmark whose modules are absent is reported NOT BUILT and does not")
    A("stop the run. This table is the discovery step's own output.")
    A("")
    A(_md_row(_MODULE_COLUMNS))
    A(_md_row(["---"] * len(_MODULE_COLUMNS)))
    for name in metrics.get("modules", {}):
        A(_md_row(module_row(name, metrics)) + f" <!--@row module:{name} -->")
    A("")
    A("## Findings")
    A("")
    A("Things the run said that nobody asked it. Each number is anchored to")
    A("`results/metrics.json` like every other number on this page.")
    A("")
    found = False
    if _ok(metrics, "plane_reproj_rmse_px"):
        found = True
        A("- `MatLock.reproj_rmse_px` is an exact-fit residual, not a measurement."
          " Across every locked frame in this sweep its standard deviation was"
          f" {_claim(metrics, 'plane_reproj_rmse_px.detail.fit_residual_stdev_px', 4)}"
          " px, because a homography fitted to four marker centres reproduces"
          " those same four centres by construction. The figure in the table"
          " above is instead the error of the marker CORNERS, which were held"
          " out of the fit; the untilted noiseless floor for that measurement is"
          f" {_claim(metrics, 'plane_reproj_rmse_px.detail.baseline_untilted_noiseless_px', 4)}"
          " px of renderer and detector rounding.")
    if _ok(metrics, "sellevent_recall"):
        found = True
        A("- Of the scripted sales this run staged,"
          f" {_claim(metrics, 'sellevent_recall.detail.misses_that_were_silent', 0)}"
          " went uncounted WITHOUT the zone raising anything, against"
          f" {_claim(metrics, 'sellevent_recall.detail.misses_that_raised', 0)}"
          " that did raise. The silent ones are objects lost by the tracker"
          " mid-crossing and re-acquired under a fresh id already past the line:"
          " the zone books that as a wrong-way arrival, which is benign by its"
          " own rules. The abstain-loudly invariant wants those raised, so this"
          " is a gap in the sell-event module and not in the benchmark.")
    if _ok(metrics, "kernel_exactly_once"):
        found = True
        A("- The kernel was raced by"
          f" {_claim(metrics, 'kernel_exactly_once.detail.threads_per_round', 0)}"
          " threads per round over"
          f" {_claim(metrics, 'kernel_exactly_once.detail.rounds_total', 0)}"
          " rounds and admitted"
          f" {_claim(metrics, 'kernel_exactly_once.detail.duplicate_intents', 0)}"
          " duplicate intents and"
          f" {_claim(metrics, 'kernel_exactly_once.detail.extra_callers_admitted', 0)}"
          " extra callers.")
    if not found:
        A("- Nothing to report: no benchmark produced a result this run.")
    A("")
    A("## Honest limits")
    A("")
    A("- Every benchmark here runs against synthetic stimulus generated in")
    A("  `tools/bench.py`. They measure the code, not a shop counter. No claim")
    A("  on this page is a field measurement.")
    A("- `ledger_verify_throughput` is a wall-clock timing. It is excluded from")
    A("  the deterministic content hash and will differ between machines and")
    A("  between runs on the same machine.")
    A("- `sellevent_recall` deliberately includes events where the object is")
    A("  lost for longer than the tracker will hold it. Those are counted as")
    A("  misses. Some of them the module raises as exceptions and some it does")
    A("  not — see Findings above, and do not read a recall below one here as")
    A("  the abstention machinery working until that split is closed.")
    A("- A benchmark reported NOT BUILT has measured nothing at all. Its absence")
    A("  is not evidence of anything.")
    A("")
    return "\n".join(L) + "\n"


# ============================================================ verify_claims

_ANCHOR_RE = re.compile(r"<!--@\s*(?P<path>[^>]*?)\s*-->")
_ROW_ANCHOR_RE = re.compile(r"\s*<!--@row\s+(?P<kind>\w+):(?P<key>[^>]*?)\s*-->\s*$")
_IGNORE_RE = re.compile(r"<!--\s*bench:ignore\s*-->")
_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
_ORDERED_LIST_RE = re.compile(r"^\s*\d+\.\s")
_FENCE_RE = re.compile(r"^\s*```")


@dataclass(frozen=True)
class Claim:
    line_no: int
    kind: str          # "row" | "value"
    path: str
    written: str
    expected: str
    ok: bool
    note: str = ""

    def __str__(self) -> str:
        return (f"line {self.line_no}: {self.kind} {self.path}\n"
                f"    markdown says : {self.written}\n"
                f"    metrics.json  : {self.expected}"
                + (f"\n    {self.note}" if self.note else ""))


@dataclass(frozen=True)
class ClaimReport:
    ok: bool
    md_path: str
    metrics_path: str
    checked: int
    drifted: tuple[Claim, ...] = ()
    unresolved: tuple[Claim, ...] = ()
    unanchored: tuple[tuple[int, str, str], ...] = ()
    strict: bool = True

    def summary(self) -> str:
        L = [f"verify_claims({self.md_path}) against {self.metrics_path}",
             f"  claims checked : {self.checked}",
             f"  drifted        : {len(self.drifted)}",
             f"  unresolved     : {len(self.unresolved)}",
             f"  unanchored     : {len(self.unanchored)}"
             + ("" if self.strict else "  (not fatal: --lenient)")]
        for c in self.drifted:
            L.append("DRIFT " + str(c))
        for c in self.unresolved:
            L.append("UNRESOLVED " + str(c))
        for ln, num, text in self.unanchored:
            L.append(f"UNANCHORED line {ln}: {num!r} has no provenance\n"
                     f"    {text.strip()}")
        L.append("  RESULT: " + ("PASS" if self.ok else "FAIL"))
        return "\n".join(L)


class ClaimError(LookupError):
    """Raised when an anchor names a path metrics.json does not have."""


_MISSING = object()


def resolve_path(metrics: dict[str, Any], path: str) -> Any:
    """Dotted lookup, with `NAME.field` shorthand for `benchmarks.NAME.field`."""
    for candidate in (path, "benchmarks." + path):
        cur: Any = metrics
        for part in candidate.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            elif isinstance(cur, list) and part.lstrip("-").isdigit():
                idx = int(part)
                if -len(cur) <= idx < len(cur):
                    cur = cur[idx]
                else:
                    cur = _MISSING
                    break
            else:
                cur = _MISSING
                break
        if cur is not _MISSING:
            return cur
    raise ClaimError(path)


def _norm_number(text: str) -> str:
    t = text.strip().lstrip("+").replace(",", "")
    return t


def _number_matches(written: str, value: Any) -> bool:
    """Compare as WRITTEN, not as float.

    The markdown decides the precision; the check re-renders the stored value to
    that same precision and compares strings. Comparing floats would let
    0.04123 pass as "0.04" only sometimes, depending on the value.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    w = _norm_number(written)
    if isinstance(value, int) and "." not in w and "e" not in w.lower():
        try:
            return int(w) == value
        except ValueError:
            return False
    if "." in w:
        decimals = len(w.split(".")[1])
    else:
        decimals = 0
    try:
        return f"{float(value):.{decimals}f}" == f"{float(w):.{decimals}f}"
    except (ValueError, OverflowError):
        return False


def _check_segment(line_no: int, segment: str, path: str,
                   value: Any) -> tuple[list[Claim], list[str]]:
    """Validate one anchored text segment. Returns (claims, unclaimed_numbers)."""
    numbers = _NUM_RE.findall(segment)

    if isinstance(value, (list, tuple)):
        want = [v for v in value if isinstance(v, (int, float))
                and not isinstance(v, bool)]
        got_ok = len(numbers) == len(want) and all(
            _number_matches(n, w) for n, w in zip(numbers, want))
        return ([Claim(line_no, "value", path, "[" + ", ".join(numbers) + "]",
                       "[" + ", ".join(str(w) for w in want) + "]", got_ok)], [])

    if isinstance(value, str):
        ok = value in segment
        # a string value consumes any digits it contains
        leftovers = [n for n in _NUM_RE.findall(segment.replace(value, " "))]
        return ([Claim(line_no, "value", path, segment.strip(), value, ok)],
                leftovers if ok else [])

    if isinstance(value, bool):
        ok = ("true" if value else "false") in segment.lower()
        return ([Claim(line_no, "value", path, segment.strip(),
                       str(value).lower(), ok)], numbers)

    if not numbers:
        return ([Claim(line_no, "value", path, "(no number)", str(value), False,
                       "anchor found no number to check")], [])
    # The claimed number is the one nearest the anchor.
    written = numbers[-1]
    ok = _number_matches(written, value)
    return ([Claim(line_no, "value", path, written, repr(value), ok)],
            numbers[:-1])


def verify_claims(md_path: str | os.PathLike[str],
                  metrics_path: str | os.PathLike[str],
                  *, strict: bool = True) -> ClaimReport:
    """Re-check every number in a markdown file against metrics.json.

    Three kinds of finding, all of them failures under `strict`:

      DRIFT       an anchored number disagrees with metrics.json. This is the
                  one that catches a hand-edited README.
      UNRESOLVED  an anchor names a metric that no longer exists. A renamed
                  field must not silently stop being checked.
      UNANCHORED  a number with no provenance at all. Without this, "every
                  number is generated" would only mean "the generated ones are
                  generated", which is a tautology.

    Anchors are HTML comments, so they are invisible in rendered markdown:

        worst RMSE `0.0913` px <!--@ plane_reproj_rmse_px.worst -->
        | a | b | c | <!--@row bench:plane_reproj_rmse_px -->

    A whole line can opt out with `<!-- bench:ignore -->`, and fenced code
    blocks are skipped.
    """
    md_path = Path(md_path)
    metrics_path = Path(metrics_path)
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))

    drifted: list[Claim] = []
    unresolved: list[Claim] = []
    unanchored: list[tuple[int, str, str]] = []
    checked = 0
    in_fence = False

    for line_no, raw in enumerate(md_path.read_text(encoding="utf-8").splitlines(), 1):
        if _FENCE_RE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence or _IGNORE_RE.search(raw):
            continue

        m_row = _ROW_ANCHOR_RE.search(raw)
        if m_row:
            kind, key = m_row.group("kind"), m_row.group("key")
            written = raw[: m_row.start()].rstrip()
            checked += 1
            builder = ROW_BUILDERS.get(kind)
            if builder is None:
                unresolved.append(Claim(line_no, "row", f"{kind}:{key}", written,
                                        "(unknown row kind)", False))
                continue
            try:
                expected = _md_row(builder(key, metrics))
            except (KeyError, TypeError) as exc:
                unresolved.append(Claim(line_no, "row", f"{kind}:{key}", written,
                                        f"({type(exc).__name__}: {exc})", False))
                continue
            if written != expected:
                drifted.append(Claim(line_no, "row", f"{kind}:{key}", written,
                                     expected, False))
            continue

        anchors = list(_ANCHOR_RE.finditer(raw))
        if not anchors:
            _scan_unanchored(line_no, raw, unanchored)
            continue

        cursor = 0
        for a in anchors:
            segment = raw[cursor:a.start()]
            cursor = a.end()
            path = a.group("path")
            checked += 1
            try:
                value = resolve_path(metrics, path)
            except ClaimError:
                unresolved.append(Claim(line_no, "value", path, segment.strip(),
                                        "(no such path in metrics.json)", False))
                continue
            claims, leftovers = _check_segment(line_no, segment, path, value)
            for c in claims:
                if not c.ok:
                    drifted.append(c)
            for n in leftovers:
                unanchored.append((line_no, n, raw))
        _scan_unanchored(line_no, raw[cursor:], unanchored)

    ok = not drifted and not unresolved and (not strict or not unanchored)
    return ClaimReport(
        ok=ok, md_path=str(md_path), metrics_path=str(metrics_path),
        checked=checked, drifted=tuple(drifted), unresolved=tuple(unresolved),
        unanchored=tuple(unanchored), strict=strict,
    )


def _scan_unanchored(line_no: int, text: str,
                     sink: list[tuple[int, str, str]]) -> None:
    if _ORDERED_LIST_RE.match(text):
        text = _ORDERED_LIST_RE.sub("", text, count=1)
    stripped = _ANCHOR_RE.sub(" ", text)
    stripped = re.sub(r"^\s*\|?\s*(?:-{3,}\s*\|\s*)+-{0,}\s*\|?\s*$", " ", stripped)
    for n in _NUM_RE.findall(stripped):
        sink.append((line_no, n, text))


# ============================================================ CLI

def write_outputs(metrics: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jpath = out_dir / "metrics.json"
    mpath = out_dir / "METRICS.md"
    jpath.write_text(json.dumps(metrics, indent=2, sort_keys=True,
                                ensure_ascii=False) + "\n", encoding="utf-8")
    mpath.write_text(render_markdown(metrics), encoding="utf-8")
    return jpath, mpath


def parse_seeds(spec: str) -> list[int]:
    """`5` means seeds 0..4. `0,3,7` means exactly those. Both are committed."""
    spec = spec.strip()
    if "," in spec:
        return [int(x) for x in spec.split(",") if x.strip() != ""]
    n = int(spec)
    if n < 1:
        raise ValueError("--seeds must be >= 1")
    return list(range(n))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="bench", description="GAWAAH bench harness (invariant 9)")
    ap.add_argument("--seeds", default=str(DEFAULT_SEEDS),
                    help="seed count (5 -> 0..4) or explicit list (0,3,7)")
    ap.add_argument("--out", default="results/", help="output directory")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="scale every benchmark's workload (1.0 = full)")
    ap.add_argument("--only", default="", help="comma-separated benchmark names")
    ap.add_argument("--verify", default="",
                    help="verify a markdown file instead of running benchmarks")
    ap.add_argument("--metrics", default="",
                    help="metrics.json to verify against (default <out>/metrics.json)")
    ap.add_argument("--lenient", action="store_true",
                    help="verify drift only; do not fail on unanchored numbers")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    say = (lambda s: None) if args.quiet else (lambda s: print(s, flush=True))

    if args.verify:
        metrics_path = Path(args.metrics) if args.metrics else out_dir / "metrics.json"
        if not metrics_path.exists():
            print(f"no metrics at {metrics_path}; run the bench first", file=sys.stderr)
            return 2
        rep = verify_claims(args.verify, metrics_path, strict=not args.lenient)
        say(rep.summary())
        return 0 if rep.ok else 1

    seeds = parse_seeds(args.seeds)
    only = tuple(x.strip() for x in args.only.split(",") if x.strip()) or None
    if only:
        unknown = [x for x in only if x not in BENCH_BY_NAME]
        if unknown:
            print(f"unknown benchmark(s): {', '.join(unknown)}", file=sys.stderr)
            return 2

    say(f"GAWAAH bench — seeds={seeds} scale={args.scale}")
    t0 = time.perf_counter()
    metrics = run_benchmarks(seeds, scale=args.scale, only=only, log=say)
    jpath, mpath = write_outputs(metrics, out_dir)
    say(f"wrote {jpath}")
    say(f"wrote {mpath}")
    say(f"total {time.perf_counter() - t0:.1f}s   "
        f"content_hash {metrics['content_hash'][:16]}...")

    rep = verify_claims(mpath, jpath, strict=True)
    say("")
    say(rep.summary())
    if not rep.ok:
        return 1

    failed = [n for n, b in metrics["benchmarks"].items()
              if b.get("status") == STATUS_ERROR]
    if failed:
        say(f"benchmarks errored: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
