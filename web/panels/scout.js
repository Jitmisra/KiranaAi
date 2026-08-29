/* SCOUT — live oriented boxes on the raw feed, drawn before anything is known.
 * ===========================================================================
 *
 * THE PROBLEM THIS SOLVES
 * Until the mat locks, the counter draws NOTHING. The camera is open, OpenCV is
 * loaded, the socket is up — and the screen is a black rectangle. A working rig
 * and a dead rig look identical, so the operator's only evidence that anything
 * is alive is the fps counter. SCOUT makes the first second of the app visible:
 * boxes snap onto whatever the camera can see, immediately, with a stable id.
 *
 * THE HONESTY PROBLEM, WHICH IS THE WHOLE DESIGN
 * The reference this is modelled on is an object detector: it prints a box, a
 * CLASS ("pothole") and a CONFIDENCE ("0.73"). We have neither.
 *
 *   - No class. There is no classifier here and there are no model weights in
 *     this browser (invariant 3). A contour is a closed edge, not a thing with
 *     a name. Printing "bottle" over a closed edge would be inventing a fact.
 *
 *   - No confidence. Nothing in this pipeline produces a calibrated posterior.
 *     Canny's hysteresis ratio and a contour's solidity are not probabilities,
 *     and dressing either up as "0.73" would be a number with no referent.
 *
 *   - No millimetres, until the mat locks. Scale on the raw feed is unknown:
 *     the same silhouette is a matchbox at 10 cm and a carton at 2 m. Only the
 *     mat's four markers fix the plane and therefore the scale. So a box drawn
 *     before the lock is a box whose SIZE WE CANNOT STATE.
 *
 * So the label is exactly what is known and nothing else:
 *
 *     unlocked   id:12  object  (size unknown)
 *     locked     id:12  58.4 x 31.2 mm
 *
 * and while unlocked a banner sits across the overlay saying that this is a
 * preview, that there is no measurement, and that none of it is billable.
 * `labelFor` can emit those two strings and no third one — scout.test.mjs
 * asserts that against two exact regexes over thousands of random tracks, so a
 * later edit cannot quietly grow a class name or a confidence number.
 *
 * WHAT IT ACTUALLY DOES
 *   grayscale -> GaussianBlur -> Canny (or adaptiveThreshold) -> morph CLOSE ->
 *   findContours(RETR_EXTERNAL) -> area/solidity/aspect filter -> minAreaRect
 *   -> IoU dedupe -> greedy centroid tracker -> oriented box + label
 * Classical CV, start to finish. Zero weights. Zero network.
 *
 * WHERE IT DRAWS, AND WHY THAT DIFFERS BY LOCK STATE
 *   UNLOCKED: on the RAW preview, via a transparent overlay canvas stacked over
 *     #raw. This is the point of the module — something happens immediately.
 *     Pixels are read, reduced to contours, and dropped inside one function;
 *     the only thing that outlives a scan is a list of numbers. Nothing raw is
 *     retained, encoded or sent, so invariant 4 is untouched: SCOUT has no wire,
 *     no encoder and no buffer.
 *   LOCKED: on the RECTIFIED crop, from the registry's onFrame payload — the
 *     only image a panel is ever handed. That buffer has a known scale, so the
 *     labels become real millimetres. SCOUT stands off the raw pane once the
 *     mat is locked: app.js owns that pane then (scrim, quad, price glyphs) and
 *     a box drawn there could not be measured anyway.
 *
 * WHAT IT IS NOT
 * SCOUT is an OBSERVER (invariant 2). It has no access to money, commits no
 * line, changes no chrome, and cannot turn anything green — there is no paise
 * in this file and no path from a contour to a rupee. A box is a box. The
 * counter still refuses to bill anything without a mat lock, and SCOUT drawing
 * a confident-looking rectangle over an object does not make it billable; the
 * banner says so out loud, which is why the banner is not optional.
 *
 * ABSTENTIONS ARE ADDED, NOT REMOVED (invariant 7). SCOUT reaches four
 * I-DO-NOT-KNOW states of its own — no camera, no OpenCV, nothing found, and
 * "seen but unmeasurable" — and the last of those is the normal state of the
 * app before the mat is in frame. CORE's own `mat_not_locked` abstention is
 * untouched: SCOUT never declares a status for CORE (the registry refuses that
 * anyway, by design) and never writes to #abstain-core or #why-core.
 *
 * ANYTHING SIMULATED SAYS SO. If the shell tells SCOUT the frames are synthetic
 * (`--sim`), the banner is prefixed SIMULATED FEED and stays up even when the
 * mat is locked, because a measurement of a synthetic frame is a measurement of
 * a drawing.
 *
 * Everything above `createPanel` is pure: no browser, no globals, no OpenCV.
 * The OpenCV bridge is one function (`contoursFrom`) and takes `cv` as an
 * argument rather than reaching for a global, so it is inspectable from node.
 *
 * This file imports nothing, like the other panels: it is loaded through a
 * data: URL by scout.test.mjs, and a data: URL cannot resolve a relative
 * specifier. Constants shared with app.js are re-declared here and the test
 * re-reads app.js to assert they still agree.
 */

export const SCOUT_ID = 'scout';
export const PANEL_TITLE = 'SCOUT — live boxes, before anything is known';

/** Where SCOUT mounts its own block when the shell does not name a host. */
export const SCOUT_HOST_ID = 'body-core';
export const SCOUT_ROOT_ID = 'scout-render';
export const SCOUT_OVERLAY_ID = 'scout-overlay';
export const SCOUT_RECT_OVERLAY_ID = 'scout-overlay-rect';
export const RAW_CANVAS_ID = 'raw';
export const RECT_CANVAS_ID = 'rect';

/**
 * The registry ids SCOUT will try, in order. 'scout' is the id this module
 * wants; app.js's PANEL_IDS does not carry it yet, so registration falls back
 * to 'core' — the panel whose live view these boxes are drawn over. Which one
 * was taken is reported on screen rather than assumed.
 */
export const REGISTRY_IDS = Object.freeze([SCOUT_ID, 'core']);

// ===========================================================================
// 1. MAT GEOMETRY. Mirrors web/app.js constant for constant; scout.test.mjs
//    re-reads app.js and asserts these are still the same numbers.
// ===========================================================================

export const MAT_W_MM = 297.0;
export const MAT_H_MM = 420.0;
export const BUF_W = 840;
export const BUF_H = 1188;
export const PX_PER_MM_X = BUF_W / MAT_W_MM;   // 2.82828...
export const PX_PER_MM_Y = BUF_H / MAT_H_MM;   // 2.82857...

// ===========================================================================
// 2. THE LABELS. Two forms, and there is deliberately no third.
// ===========================================================================

/** What an unlocked box is: a thing we can see and cannot measure. */
export const UNKNOWN_SIZE_LABEL = 'object  (size unknown)';

export const PREVIEW_BANNER =
  'PREVIEW - boxes only. No mat, so no measurement and nothing billable.';

export const SIMULATED_PREFIX = 'SIMULATED FEED - ';

export const SIMULATED_LOCKED_BANNER =
  'these millimetres are measured from synthetic frames, not from a camera. '
  + 'Nothing here is billable.';

/**
 * The two shapes `labelFor` may ever produce. Exported so the test can assert
 * against the same source of truth the renderer uses, and so a reviewer can see
 * the grammar of an honest label without reading the function.
 */
export const LABEL_UNLOCKED_RE = /^id:\d+ {2}object {2}\(size unknown\)$/;
export const LABEL_LOCKED_RE = /^id:\d+ {2}\d+\.\d x \d+\.\d mm$/;

/** SCOUT's own I-DO-NOT-KNOW states. All four stay reachable. */
export const S_NO_CAMERA = 'scout_no_camera_frame';
export const S_NO_OPENCV = 'scout_no_opencv';
export const S_NOTHING_FOUND = 'scout_no_contours_passed_the_filter';
export const S_SEEN_UNMEASURED = 'scout_seen_but_unmeasurable_no_mat_lock';
/** Prefix; the thrown message is appended, so this one is matched by prefix. */
export const S_CV_THREW = 'scout_cv_threw';

export const ABSTENTIONS = Object.freeze({
  [S_NO_CAMERA]: 'no frame has been read yet. The camera is not delivering '
    + 'pixels, so there is nothing to find edges in and SCOUT draws nothing.',
  [S_NO_OPENCV]: 'OpenCV is not loaded in this page. SCOUT is classical CV and '
    + 'nothing else, so with no cv there is no pipeline — and no fallback that '
    + 'guesses boxes, because a guessed box is worse than an empty overlay.',
  [S_NOTHING_FOUND]: 'frames are arriving and no closed contour passed the area '
    + 'and solidity filter. That is a finding, not a failure: an empty overlay '
    + 'is the honest picture of a blank surface.',
  [S_SEEN_UNMEASURED]: 'boxes are on screen and their SIZE IS UNKNOWN. Without '
    + 'the mat there is no scale, so the same silhouette could be a matchbox or '
    + 'a carton. SCOUT will not name a class and will not state a size, and none '
    + 'of these boxes is billable.',
  [S_CV_THREW]: 'a call into OpenCV threw part-way through the pipeline. That is '
    + 'NOT the same as finding nothing: an empty overlay would say the counter '
    + 'looked and saw no objects, and what actually happened is that it never '
    + 'finished looking. The thrown message is printed below.',
});

/** The explanation for a reason code, tolerating the `prefix: detail` form. */
export function explainReason(why) {
  if (typeof why !== 'string' || why === '') return '';
  return ABSTENTIONS[why] ?? ABSTENTIONS[why.split(':')[0]] ?? why;
}

/**
 * Colours. GREEN IS NOT AVAILABLE TO ANY PANEL (see the note at the top of
 * web/style.css): green means a signature-verified webhook settled the session,
 * and a contour is not a payment. Unlocked boxes are cyan because cyan means
 * nothing in this palette; locked boxes are plain ink; the banner is amber
 * because amber is the loudest thing on screen and abstention gets the weight.
 */
export const COLOURS = Object.freeze({
  boxUnlocked: '#5fd0e8',
  boxLocked: '#eef1f6',
  labelInk: '#eef1f6',
  labelBg: 'rgba(6,8,12,0.82)',
  bannerInk: '#e0a33c',
  bannerBg: 'rgba(224,163,60,0.16)',
  bannerRule: '#e0a33c',
  faint: 'rgba(95,208,232,0.35)',
});

// ===========================================================================
// 3. PURE GEOMETRY. No cv, no DOM. Everything below is exercised from node.
// ===========================================================================

/** Shoelace area of a closed polygon, unsigned. */
export function polygonArea(pts) {
  if (!Array.isArray(pts) || pts.length < 3) return 0;
  let s = 0;
  for (let i = 0, n = pts.length; i < n; i++) {
    const a = pts[i], b = pts[(i + 1) % n];
    s += a[0] * b[1] - b[0] * a[1];
  }
  return Math.abs(s) / 2;
}

/** Perimeter of a closed polygon. */
export function polygonPerimeter(pts) {
  if (!Array.isArray(pts) || pts.length < 2) return 0;
  let s = 0;
  for (let i = 0, n = pts.length; i < n; i++) {
    const a = pts[i], b = pts[(i + 1) % n];
    s += Math.hypot(b[0] - a[0], b[1] - a[1]);
  }
  return s;
}

/**
 * Convex hull, monotone chain, counter-clockwise in a y-down raster. Returns a
 * new array; duplicate and collinear points are dropped.
 */
export function convexHull(pts) {
  const p = (Array.isArray(pts) ? pts : [])
    .filter((q) => Array.isArray(q) && Number.isFinite(q[0]) && Number.isFinite(q[1]))
    .map((q) => [q[0], q[1]])
    .sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]));
  // de-duplicate exact repeats, which otherwise make the cross product zero
  const u = [];
  for (const q of p) {
    const last = u[u.length - 1];
    if (!last || last[0] !== q[0] || last[1] !== q[1]) u.push(q);
  }
  if (u.length < 3) return u;
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower = [];
  for (const q of u) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], q) <= 0) lower.pop();
    lower.push(q);
  }
  const upper = [];
  for (let i = u.length - 1; i >= 0; i--) {
    const q = u[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], q) <= 0) upper.pop();
    upper.push(q);
  }
  lower.pop(); upper.pop();
  return lower.concat(upper);
}

/** Normalise an edge direction to the half-open interval (-90, 90] degrees. */
export function normaliseAngleDeg(deg) {
  if (!Number.isFinite(deg)) return 0;
  let a = deg % 180;
  if (a > 90) a -= 180;
  if (a <= -90) a += 180;
  // -0 is a real value in JS and prints as "-0"; it is not a distinct angle.
  return a === 0 ? 0 : a;
}

/**
 * Minimum-area oriented bounding rectangle, by rotating calipers over the hull.
 * This is cv.minAreaRect's algorithm, written out so it is testable in node and
 * so the browser and the test agree on one implementation rather than two.
 *
 * Returns { cx, cy, longPx, shortPx, angleDeg, corners, areaPx, axis } where
 * `axis` is the unit vector of the LONG side and `angleDeg` is its bearing.
 */
export function minAreaRect(pts) {
  const hull = convexHull(pts);
  if (hull.length === 0) return null;
  if (hull.length === 1) {
    const [x, y] = hull[0];
    return {
      cx: x, cy: y, longPx: 0, shortPx: 0, angleDeg: 0, areaPx: 0,
      axis: [1, 0], corners: [[x, y], [x, y], [x, y], [x, y]],
    };
  }
  let best = null;
  const n = hull.length;
  // A 2-point hull is a segment: one edge is enough and the loop handles it.
  const edges = n === 2 ? 1 : n;
  for (let i = 0; i < edges; i++) {
    const a = hull[i], b = hull[(i + 1) % n];
    let ex = b[0] - a[0], ey = b[1] - a[1];
    const len = Math.hypot(ex, ey);
    if (!(len > 1e-12)) continue;
    ex /= len; ey /= len;
    let minU = Infinity, maxU = -Infinity, minV = Infinity, maxV = -Infinity;
    for (const q of hull) {
      const u = q[0] * ex + q[1] * ey;
      const v = -q[0] * ey + q[1] * ex;
      if (u < minU) minU = u;
      if (u > maxU) maxU = u;
      if (v < minV) minV = v;
      if (v > maxV) maxV = v;
    }
    const w = maxU - minU, h = maxV - minV;
    const area = w * h;
    if (best === null || area < best.area - 1e-9) {
      best = { area, ex, ey, minU, maxU, minV, maxV, w, h };
    }
  }
  if (best === null) return null;
  const { ex, ey, minU, maxU, minV, maxV } = best;
  const toXy = (u, v) => [u * ex - v * ey, u * ey + v * ex];
  const corners = [
    toXy(minU, minV), toXy(maxU, minV), toXy(maxU, maxV), toXy(minU, maxV),
  ];
  const centre = toXy((minU + maxU) / 2, (minV + maxV) / 2);
  const wideIsLong = best.w >= best.h;
  const axis = wideIsLong ? [ex, ey] : [-ey, ex];
  return {
    cx: centre[0],
    cy: centre[1],
    longPx: Math.max(best.w, best.h),
    shortPx: Math.min(best.w, best.h),
    angleDeg: normaliseAngleDeg(Math.atan2(axis[1], axis[0]) * 180 / Math.PI),
    areaPx: best.area,
    axis,
    corners,
  };
}

/** Axis-aligned bounds of a point list, as { x0, y0, x1, y1 }. */
export function boundsOf(pts) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const p of pts || []) {
    if (p[0] < x0) x0 = p[0];
    if (p[0] > x1) x1 = p[0];
    if (p[1] < y0) y0 = p[1];
    if (p[1] > y1) y1 = p[1];
  }
  if (!Number.isFinite(x0)) return { x0: 0, y0: 0, x1: 0, y1: 0 };
  return { x0, y0, x1, y1 };
}

/** Intersection-over-union of two axis-aligned bounds. */
export function iou(a, b) {
  const ix = Math.max(0, Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0));
  const iy = Math.max(0, Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0));
  const inter = ix * iy;
  const areaA = Math.max(0, a.x1 - a.x0) * Math.max(0, a.y1 - a.y0);
  const areaB = Math.max(0, b.x1 - b.x0) * Math.max(0, b.y1 - b.y0);
  const uni = areaA + areaB - inter;
  return uni > 0 ? inter / uni : 0;
}

// ===========================================================================
// 4. CONTOURS -> BOXES. The filter, written as one pure function.
// ===========================================================================

/**
 * The filter thresholds. These are shape gates, not confidences: a contour
 * either clears them or it does not, and nothing downstream is weighted by how
 * far it cleared them by. Fractions of frame area rather than pixel counts, so
 * the same numbers hold at 320x240 and at 1280x960.
 */
export const BOX_DEFAULTS = Object.freeze({
  minAreaFrac: 0.0015,   // below this it is texture, not an object
  maxAreaFrac: 0.55,     // above this it is the table, the wall, or the frame
  minSolidity: 0.55,     // area / hull area: rejects wiry, shattered edge webs
  minShortPx: 6,         // a box thinner than this is a line, not an object
  maxAspect: 14,         // a 14:1 sliver is a table edge or a cable
  dedupeIou: 0.62,       // Canny returns nested rings; keep the outer one
  maxBoxes: 20,          // the overlay stays legible; the largest survive
});

/**
 * Turn contours into oriented boxes.
 *
 * `contours` is an array of either point lists (`[[x,y], ...]`) or objects
 * `{ points, areaPx, hullAreaPx }`. The object form lets the OpenCV bridge
 * hand over cv-computed area and hull area — which are computed on the full
 * contour, before simplification — while this function stays the only place
 * that decides what survives.
 *
 * Pure: same input, same output, no clock, no globals.
 */
export function boxesFromContours(contours, opts = {}) {
  const o = { ...BOX_DEFAULTS, ...opts };
  const imageW = Number.isFinite(o.imageW) ? o.imageW : 0;
  const imageH = Number.isFinite(o.imageH) ? o.imageH : 0;
  const frameArea = imageW > 0 && imageH > 0 ? imageW * imageH : 0;
  const minArea = frameArea > 0 ? frameArea * o.minAreaFrac : (o.minAreaPx ?? 0);
  const maxArea = frameArea > 0 ? frameArea * o.maxAreaFrac : Infinity;
  const scale = Number.isFinite(o.scale) && o.scale > 0 ? o.scale : 1;

  const kept = [];
  for (const raw of Array.isArray(contours) ? contours : []) {
    const points = Array.isArray(raw) ? raw : (raw && Array.isArray(raw.points) ? raw.points : null);
    if (!points || points.length < 3) continue;

    const areaPx = Number.isFinite(raw && raw.areaPx) ? raw.areaPx : polygonArea(points);
    if (!(areaPx >= minArea) || areaPx > maxArea) continue;

    const rect = minAreaRect(points);
    if (rect === null) continue;
    if (!(rect.shortPx >= o.minShortPx)) continue;
    const aspect = rect.shortPx > 0 ? rect.longPx / rect.shortPx : Infinity;
    if (!(aspect <= o.maxAspect)) continue;

    const hullAreaPx = Number.isFinite(raw && raw.hullAreaPx)
      ? raw.hullAreaPx
      : polygonArea(convexHull(points));
    const solidity = hullAreaPx > 0 ? Math.min(1, areaPx / hullAreaPx) : 0;
    if (!(solidity >= o.minSolidity)) continue;

    // The oriented box, expressed in the coordinates of the SOURCE canvas: the
    // pipeline runs on a downscale and `scale` maps it back, so nothing
    // downstream has to remember that the detection was cheap.
    const corners = rect.corners.map((p) => [p[0] * scale, p[1] * scale]);
    kept.push({
      cx: rect.cx * scale,
      cy: rect.cy * scale,
      longPx: rect.longPx * scale,
      shortPx: rect.shortPx * scale,
      angleDeg: rect.angleDeg,
      corners,
      areaPx: areaPx * scale * scale,
      rectAreaPx: rect.areaPx * scale * scale,
      solidity,
      aspect,
      bounds: boundsOf(corners),
    });
  }

  // Canny traces the inside AND the outside of every edge, so one object can
  // arrive as two nearly identical rings. Keep the larger of any overlapping
  // pair rather than drawing a box on top of a box.
  kept.sort((a, b) => b.rectAreaPx - a.rectAreaPx);
  const out = [];
  for (const b of kept) {
    let swallowed = false;
    for (const k of out) {
      if (iou(k.bounds, b.bounds) >= o.dedupeIou) { swallowed = true; break; }
    }
    if (!swallowed) out.push(b);
    if (out.length >= o.maxBoxes) break;
  }
  return out;
}

// ===========================================================================
// 5. THE TRACKER. Stable ids, so a label says "id:36" for as long as the thing
//    is on screen instead of renumbering itself thirty times a second.
// ===========================================================================

export const TRACK_DEFAULTS = Object.freeze({
  maxDistPx: 120,      // a centroid cannot teleport further than this in a frame
  holdFrames: 6,       // keep drawing a box that blinked out, for this many frames
  minSizeRatio: 0.35,  // a match must be the same order of size, or it is not it
  maxSizeRatio: 2.9,
});

/** A fresh tracker state. Ids start at 1 so "id:0" never appears. */
export function newTrackState(nextId = 1) {
  return { tracks: [], nextId: Math.max(1, Math.floor(nextId) || 1) };
}

/**
 * Greedy nearest-centroid association. Pure: takes a state, returns a NEW state.
 *
 * Ties are broken by (distance, track id, box index) so two runs over the same
 * input produce the same ids — a tracker that renumbers non-deterministically
 * would make the id on screen meaningless, which is the failure this exists to
 * prevent.
 *
 * A track that is not matched is HELD, not deleted, for `holdFrames` frames: a
 * box that flickers off for one frame keeps its id when it comes back. A held
 * track carries `missing > 0` and the renderer draws it faintly, so "this is a
 * box I saw a moment ago" is distinguishable from "this is a box I see".
 */
export function trackBoxes(state, boxes, opts = {}) {
  const o = { ...TRACK_DEFAULTS, ...opts };
  const prev = state && Array.isArray(state.tracks) ? state.tracks : [];
  let nextId = state && Number.isFinite(state.nextId) ? state.nextId : 1;
  const list = Array.isArray(boxes) ? boxes : [];
  const ts = Number.isFinite(o.ts) ? o.ts : null;

  const pairs = [];
  for (let ti = 0; ti < prev.length; ti++) {
    const t = prev[ti];
    for (let bi = 0; bi < list.length; bi++) {
      const b = list[bi];
      const d = Math.hypot(t.cx - b.cx, t.cy - b.cy);
      if (!(d <= o.maxDistPx)) continue;
      const ratio = t.longPx > 0 && b.longPx > 0 ? b.longPx / t.longPx : 0;
      if (!(ratio >= o.minSizeRatio && ratio <= o.maxSizeRatio)) continue;
      pairs.push({ ti, bi, d, id: t.id });
    }
  }
  pairs.sort((p, q) => (p.d - q.d) || (p.id - q.id) || (p.bi - q.bi));

  const takenTrack = new Set();
  const takenBox = new Set();
  const matched = new Map();   // track index -> box index
  for (const p of pairs) {
    if (takenTrack.has(p.ti) || takenBox.has(p.bi)) continue;
    takenTrack.add(p.ti); takenBox.add(p.bi);
    matched.set(p.ti, p.bi);
  }

  const out = [];
  for (let ti = 0; ti < prev.length; ti++) {
    const t = prev[ti];
    if (matched.has(ti)) {
      const b = list[matched.get(ti)];
      out.push({
        ...b,
        id: t.id,
        age: (t.age || 0) + 1,
        missing: 0,
        firstSeen: t.firstSeen ?? ts,
        lastSeen: ts,
      });
    } else {
      const missing = (t.missing || 0) + 1;
      if (missing > o.holdFrames) continue;   // gone: the id retires with it
      out.push({ ...t, missing, age: (t.age || 0) + 1 });
    }
  }
  for (let bi = 0; bi < list.length; bi++) {
    if (takenBox.has(bi)) continue;
    out.push({
      ...list[bi],
      id: nextId++,
      age: 1,
      missing: 0,
      firstSeen: ts,
      lastSeen: ts,
    });
  }
  out.sort((a, b) => a.id - b.id);
  return { tracks: out, nextId };
}

// ===========================================================================
// 6. THE LABEL. Two forms and no third — this is the honesty seam.
// ===========================================================================

/**
 * Millimetres of a box's long and short side, in the mat plane.
 *
 * The rectified buffer is not square-scaled (2.82828 px/mm across, 2.82857
 * down), so a rotated box cannot be converted by dividing by one number. A
 * pixel vector (dx, dy) maps to (dx/pxx, dy/pxy) millimetres, so each side is
 * measured along its own direction. The difference is one part in ten thousand
 * and it costs three lines to be right instead of nearly right.
 *
 * Returns null when there is no usable scale — which is the unlocked case, and
 * is why the caller must not fall back to a number.
 */
export function sizeMm(box, pxPerMmX = PX_PER_MM_X, pxPerMmY = PX_PER_MM_Y) {
  if (!box || !Number.isFinite(box.longPx) || !Number.isFinite(box.shortPx)) return null;
  if (!Number.isFinite(pxPerMmX) || !Number.isFinite(pxPerMmY)) return null;
  if (!(pxPerMmX > 0) || !(pxPerMmY > 0)) return null;
  const ax = Array.isArray(box.axis) && Number.isFinite(box.axis[0]) ? box.axis[0] : 1;
  const ay = Array.isArray(box.axis) && Number.isFinite(box.axis[1]) ? box.axis[1] : 0;
  const n = Math.hypot(ax, ay) || 1;
  const ux = ax / n, uy = ay / n;
  const longMm = box.longPx * Math.hypot(ux / pxPerMmX, uy / pxPerMmY);
  const shortMm = box.shortPx * Math.hypot(uy / pxPerMmX, ux / pxPerMmY);
  if (!Number.isFinite(longMm) || !Number.isFinite(shortMm)) return null;
  return { longMm, shortMm };
}

/**
 * The label for one tracked box.
 *
 *   locked, with a scale     "id:12  58.4 x 31.2 mm"
 *   anything else            "id:12  object  (size unknown)"
 *
 * There is no class name because there is no classifier, and no confidence
 * because nothing here produces one. The fallback is deliberately the SAME
 * string as the unlocked case: if a scale is claimed but arrives non-finite,
 * SCOUT says it does not know the size rather than printing NaN or a zero.
 */
export function labelFor(track, opts = {}) {
  const rawId = track && Number.isFinite(track.id) ? Math.trunc(track.id) : null;
  const id = rawId !== null && rawId >= 0 ? `id:${rawId}` : 'id:?';
  const unknown = `${id}  ${UNKNOWN_SIZE_LABEL}`;
  if (opts.locked !== true) return unknown;
  // A scale that is ABSENT means "use the mat"; a scale that is PRESENT and
  // unusable means the caller has told us it does not know the scale. Those are
  // different statements. `??` collapses them, and so does a default parameter
  // on sizeMm — `sizeMm(box, undefined)` silently becomes the mat's px/mm. Both
  // would substitute a scale the caller explicitly disclaimed, so the check is
  // made HERE, before anything can default it back.
  const pxx = 'pxPerMmX' in opts ? opts.pxPerMmX : PX_PER_MM_X;
  const pxy = 'pxPerMmY' in opts ? opts.pxPerMmY : PX_PER_MM_Y;
  if (!Number.isFinite(pxx) || !Number.isFinite(pxy) || pxx <= 0 || pxy <= 0) return unknown;
  const mm = sizeMm(track, pxx, pxy);
  if (mm === null) return unknown;
  if (!(mm.longMm > 0) || !(mm.shortMm > 0)) return unknown;
  // Guard the format itself: a size that will not print as d.d mm (a 10 metre
  // "object", say) is a broken scale, and an absurd number is a worse answer
  // than admitting the size is unknown.
  if (mm.longMm >= 100000 || mm.shortMm >= 100000) return unknown;
  return `${id}  ${mm.longMm.toFixed(1)} x ${mm.shortMm.toFixed(1)} mm`;
}

/**
 * The banner. Present whenever a claim needs qualifying; null only when the
 * mat is locked AND the frames are real, which is the one case where the
 * millimetres on screen mean what they say.
 */
export function bannerFor(ctx = {}) {
  const locked = ctx.locked === true;
  const simulated = ctx.simulated === true;
  if (!locked) {
    return {
      kind: simulated ? 'preview-simulated' : 'preview',
      simulated,
      text: simulated ? SIMULATED_PREFIX + PREVIEW_BANNER : PREVIEW_BANNER,
    };
  }
  if (simulated) {
    return {
      kind: 'locked-simulated',
      simulated: true,
      text: SIMULATED_PREFIX + SIMULATED_LOCKED_BANNER,
    };
  }
  return null;
}

/**
 * What SCOUT currently knows, as one of its four abstentions or 'ok'.
 * `ok` is reached only when the mat is locked and boxes are measured; seeing
 * boxes without a mat is NOT ok, it is `scout_seen_but_unmeasurable_no_mat_lock`,
 * which is the honest description of a preview.
 */
export function scoutStatus(view = {}) {
  if (view.opencv === false) return { status: 'ABSTAIN', why: S_NO_OPENCV };
  // A crash is not a finding. "Nothing found" claims the counter looked and saw
  // no objects; a throw means it never finished looking, and reporting the
  // second as the first would turn a broken pipeline into a confident empty
  // overlay. The specific reason wins over every derived one below.
  if (typeof view.reason === 'string' && view.reason.startsWith(S_CV_THREW)) {
    return { status: 'ABSTAIN', why: view.reason };
  }
  if (!view.framesSeen) return { status: 'ABSTAIN', why: S_NO_CAMERA };
  const n = Number.isFinite(view.boxCount) ? view.boxCount : 0;
  if (n === 0) return { status: 'ABSTAIN', why: S_NOTHING_FOUND };
  if (view.locked !== true) return { status: 'ABSTAIN', why: S_SEEN_UNMEASURED };
  return { status: 'OK', why: null };
}

// ===========================================================================
// 7. THE OVERLAY. Takes a 2D-context-like object, so the drawing is asserted
//    in node against a recording stub rather than eyeballed in a screenshot.
// ===========================================================================

/**
 * Overlay typography, expressed at a 640px-wide backing store. The overlay
 * canvas matches the CAMERA's backing store — 1280x960 on a phone — but is laid
 * out at whatever width the pane happens to be, so a fixed 15px label renders
 * at seven CSS pixels and is unreadable. Everything here is therefore scaled by
 * drawScaleFor(width): the label is legible at the size the operator sees it,
 * which is the difference between a working feature and a screenshot of one.
 */
export const DRAW_DEFAULTS = Object.freeze({
  lineWidth: 2,
  labelFontPx: 15,
  bannerFontPx: 14,
  bannerHeight: 30,
  pad: 5,
});

/**
 * How much to multiply the reference type by on this canvas.
 *
 * When the caller knows the overlay's LAID-OUT width (`cssWidth`), that is the
 * honest answer: backing-store pixels per CSS pixel, so a 15px label is 15px on
 * the operator's screen whether the canvas is 840 wide in a 340px pane or 1280
 * wide in a 640px pane. Without it, fall back to guessing from the backing
 * store alone, which is right for a full-width preview and small for a
 * squeezed one.
 */
/**
 * The width the canvas CONTENT actually occupies under `object-fit: contain`.
 *
 * `clientWidth` is the element's box, not the picture inside it. The rectified
 * pane is a 840x1188 portrait buffer in a 4:3 landscape box, so it is
 * letterboxed to about half the element's width — and sizing type by
 * clientWidth there makes the label half the size it should be. This is the
 * same `min(w, h * aspect)` the browser uses.
 */
export function containedWidth(bufW, bufH, cssW, cssH) {
  const okAll = [bufW, bufH, cssW, cssH].every((v) => Number.isFinite(v) && v > 0);
  if (!okAll) return Number.isFinite(cssW) && cssW > 0 ? cssW : null;
  return Math.min(cssW, cssH * (bufW / bufH));
}

export function drawScaleFor(width, cssWidth) {
  if (Number.isFinite(width) && width > 0 && Number.isFinite(cssWidth) && cssWidth > 0) {
    return Math.max(0.6, Math.min(6, width / cssWidth));
  }
  if (!Number.isFinite(width) || width <= 0) return 1;
  return Math.max(1, Math.min(3.2, width / 640));
}

/**
 * Greedy word wrap against a measuring function.
 *
 * The banner is wrapped, never truncated. Shrinking it to fit one line ends at
 * a font nobody can read on a phone, and clipping it ends with the operator
 * seeing "PREVIEW - boxes only. No mat, so no measu" — a sentence whose second
 * half is the part that matters. Every word survives; the strip gets taller.
 *
 * A single word longer than the line is emitted on its own rather than dropped:
 * an overflowing word is still a readable word.
 */
export function wrapToWidth(measure, text, maxWidth) {
  const words = String(text ?? '').split(/\s+/).filter(Boolean);
  if (words.length === 0) return [''];
  if (typeof measure !== 'function' || !(maxWidth > 0)) return [words.join(' ')];
  const lines = [];
  let cur = '';
  for (const w of words) {
    const trial = cur ? `${cur} ${w}` : w;
    if (cur && measure(trial) > maxWidth) { lines.push(cur); cur = w; }
    else cur = trial;
  }
  if (cur) lines.push(cur);
  return lines;
}

/** Where the label sits: above the box's topmost corner, clamped into frame. */
export function labelAnchor(track, width, height, opts = {}) {
  const o = { ...DRAW_DEFAULTS, ...opts };
  // `topGuard` is the strip the banner occupies. A label placed under the
  // banner would be painted over by it, so a box near the top of the frame gets
  // its label BELOW itself instead of losing it.
  const guard = Number.isFinite(o.topGuard) ? o.topGuard : 0;
  const b = track.bounds || boundsOf(track.corners || []);
  const h = o.labelFontPx + o.pad * 2;
  let x = b.x0;
  let y = b.y0 - h - 2;
  if (y < guard) y = Math.min(Math.max(b.y1 + 2, guard), Math.max(guard, height - h));
  if (x < 0) x = 0;
  if (width > 0 && x > width - o.labelFontPx) x = Math.max(0, width - o.labelFontPx);
  return { x, y, h };
}

/**
 * Draw the oriented boxes and their labels onto a 2D context.
 *
 * Returns a summary of WHAT WAS DRAWN — box count, label strings, banner text —
 * so the caller can assert on the overlay's content without reading pixels
 * back, and so the panel block can show the same strings as the overlay.
 */
export function drawScout(ctx, tracks, opts = {}) {
  const base = { ...DRAW_DEFAULTS, ...opts };
  const width = Number.isFinite(base.width) ? base.width : 0;
  const height = Number.isFinite(base.height) ? base.height : 0;
  const k = base.scaleUi === false ? 1 : drawScaleFor(width, base.cssWidth);
  const o = {
    ...base,
    lineWidth: base.lineWidth * k,
    labelFontPx: Math.round(base.labelFontPx * k),
    bannerFontPx: Math.round(base.bannerFontPx * k),
    bannerHeight: Math.round(base.bannerHeight * k),
    pad: Math.round(base.pad * k),
  };
  const locked = o.locked === true;
  const banner = bannerFor({ locked, simulated: o.simulated === true });
  const labels = [];
  if (!ctx || typeof ctx.clearRect !== 'function') {
    return { drawn: 0, labels, banner: banner ? banner.text : null, cleared: false };
  }

  ctx.clearRect(0, 0, width, height);

  // The banner is FITTED to the canvas rather than clipped by it. An ellipsised
  // warning is a worse warning, and this one has to survive being read on a
  // phone: the sentence that says nothing is billable is the one sentence on
  // the overlay that must never be half-visible. Its height is settled here,
  // before the boxes, so labels can be kept out from under it.
  const bannerFont = (px) => `600 ${px}px ui-monospace, Menlo, monospace`;
  let bannerFs = o.bannerFontPx;
  let bannerLines = [];
  let bannerH = 0;
  if (banner) {
    const avail = Math.max(1, width - o.pad * 4);
    if (typeof ctx.measureText === 'function') {
      // Shrink toward a legibility FLOOR first — a little smaller is fine, an
      // unreadable warning is not — then wrap whatever still does not fit.
      const floor = Math.max(9, Math.round(width / 40));
      ctx.font = bannerFont(bannerFs);
      const oneLine = ctx.measureText(banner.text).width || 0;
      if (oneLine > avail) {
        bannerFs = Math.max(Math.min(bannerFs, floor), Math.floor(bannerFs * (avail / oneLine)));
      }
      ctx.font = bannerFont(bannerFs);
      bannerLines = wrapToWidth((s) => ctx.measureText(s).width, banner.text, avail);
    } else {
      bannerLines = [banner.text];
    }
    const lineH = Math.round(bannerFs * 1.35);
    bannerH = Math.max(o.bannerHeight, bannerLines.length * lineH + o.pad * 2);
  }
  o.topGuard = bannerH;

  const list = Array.isArray(tracks) ? tracks : [];
  for (const t of list) {
    const corners = Array.isArray(t.corners) && t.corners.length === 4 ? t.corners : null;
    if (!corners) continue;
    const held = (t.missing || 0) > 0;
    const stroke = held ? COLOURS.faint : (locked ? COLOURS.boxLocked : COLOURS.boxUnlocked);

    ctx.save();
    ctx.lineWidth = o.lineWidth;
    ctx.strokeStyle = stroke;
    ctx.beginPath();
    ctx.moveTo(corners[0][0], corners[0][1]);
    for (let i = 1; i < 4; i++) ctx.lineTo(corners[i][0], corners[i][1]);
    ctx.closePath();
    ctx.stroke();
    ctx.restore();

    // Forwarded faithfully, present-or-absent: see labelFor on why a disclaimed
    // scale must not be replaced by the mat's.
    const labelOpts = { locked };
    if ('pxPerMmX' in o) labelOpts.pxPerMmX = o.pxPerMmX;
    if ('pxPerMmY' in o) labelOpts.pxPerMmY = o.pxPerMmY;
    const label = labelFor(t, labelOpts);
    labels.push(label);

    const a = labelAnchor(t, width, height, o);
    ctx.save();
    ctx.font = `${o.labelFontPx}px ui-monospace, Menlo, monospace`;
    ctx.textBaseline = 'top';
    const w = typeof ctx.measureText === 'function'
      ? (ctx.measureText(label).width || label.length * o.labelFontPx * 0.6)
      : label.length * o.labelFontPx * 0.6;
    // The right-edge clamp needs the MEASURED width, so it happens here rather
    // than in labelAnchor. A label that runs off the edge loses its right-hand
    // half — which on an unlocked box is the "(size unknown)" half, the only
    // part of the label that qualifies the claim.
    const boxW = w + o.pad * 2;
    const lx = width > 0 ? Math.max(0, Math.min(a.x, width - boxW)) : a.x;
    ctx.fillStyle = COLOURS.labelBg;
    ctx.fillRect(lx, a.y, boxW, a.h);
    ctx.fillStyle = held ? COLOURS.faint : COLOURS.labelInk;
    ctx.fillText(label, lx + o.pad, a.y + o.pad);
    ctx.restore();
  }

  if (banner) {
    // Drawn LAST so it is never occluded by a box or a label.
    const rule = Math.max(2, Math.round(2 * k));
    ctx.save();
    ctx.fillStyle = COLOURS.bannerBg;
    ctx.fillRect(0, 0, width, bannerH);
    ctx.fillStyle = COLOURS.bannerRule;
    ctx.fillRect(0, bannerH - rule, width, rule);
    ctx.font = bannerFont(bannerFs);
    ctx.textBaseline = 'middle';
    ctx.fillStyle = COLOURS.bannerInk;
    const lineH = Math.round(bannerFs * 1.35);
    const top = (bannerH - rule - bannerLines.length * lineH) / 2;
    bannerLines.forEach((line, i) => {
      ctx.fillText(line, o.pad * 2, top + lineH * (i + 0.5));
    });
    ctx.restore();
  }

  // The banner text, as it was actually laid out. A caller that wants to prove
  // the whole warning reached the screen can join these and compare.
  const bannerDrawn = banner ? bannerLines.join(' ') : null;

  return {
    drawn: labels.length,
    labels,
    banner: banner ? banner.text : null,
    bannerDrawn,
    bannerLines,
    bannerFontPx: banner ? bannerFs : null,
    simulated: banner ? banner.simulated : false,
    cleared: true,
  };
}

// ===========================================================================
// 8. THE OPENCV BRIDGE. The only function here that touches pixels, and the
//    only place a raw frame is ever read. `cv` is an argument, never a global.
// ===========================================================================

export const CV_DEFAULTS = Object.freeze({
  targetWidth: 320,     // detect small, draw big; a phone cannot afford 1280
  blurKernel: 5,
  cannyLo: 40,
  cannyHi: 120,
  closeKernel: 5,
  adaptiveBlock: 25,
  adaptiveC: 7,
  minContourAreaPx: 24,   // in DOWNSCALED px; below this it is noise
  maxContours: 400,
  simplifyFrac: 0.008,
});

export const MODE_CANNY = 'canny';
export const MODE_ADAPTIVE = 'adaptive';
export const MODE_AUTO = 'auto';

/**
 * Read a canvas, return CONTOURS AND NOTHING ELSE.
 *
 * INVARIANT 4 lives in this function. Pixels enter, every Mat is deleted before
 * the return, and what comes back is a list of integer coordinates. There is no
 * ImageData, no canvas, no typed array and no data: URL in the result, and this
 * module has no encoder and no socket to put one on — so no frame, raw or
 * otherwise, can outlive this call. scout.test.mjs walks the returned object
 * and fails if anything image-shaped is reachable from it.
 *
 * `mode` AUTO runs Canny first and falls back to adaptiveThreshold when Canny
 * finds nothing, which is the low-contrast case (a pale box on a pale counter).
 * Both are classical; neither is a model.
 */
export function contoursFrom(cvv, source, opts = {}) {
  const o = { ...CV_DEFAULTS, ...opts };
  const fail = (reason) => ({ contours: [], width: 0, height: 0, scale: 1, mode: null, reason });
  if (!cvv || typeof cvv.imread !== 'function') return fail(S_NO_OPENCV);
  if (!source) return fail(S_NO_CAMERA);
  const sw = source.width | 0, sh = source.height | 0;
  if (!(sw > 0 && sh > 0)) return fail(S_NO_CAMERA);

  const wanted = o.mode === MODE_ADAPTIVE ? [MODE_ADAPTIVE]
    : o.mode === MODE_CANNY ? [MODE_CANNY]
      : [MODE_CANNY, MODE_ADAPTIVE];

  let src = null, small = null, gray = null, blur = null, edges = null,
    closed = null, kernel = null, contours = null, hierarchy = null;
  const drop = () => {
    for (const m of [src, small, gray, blur, edges, closed, kernel, contours, hierarchy]) {
      try { if (m && typeof m.delete === 'function') m.delete(); } catch (_) { /* already gone */ }
    }
    src = small = gray = blur = edges = closed = kernel = contours = hierarchy = null;
  };

  try {
    src = cvv.imread(source);
    const targetW = Math.max(64, Math.min(o.targetWidth, sw));
    const k = targetW / sw;
    const dw = Math.max(2, Math.round(sw * k));
    const dh = Math.max(2, Math.round(sh * k));
    small = new cvv.Mat();
    cvv.resize(src, small, new cvv.Size(dw, dh), 0, 0, cvv.INTER_AREA);
    src.delete(); src = null;

    gray = new cvv.Mat();
    cvv.cvtColor(small, gray, cvv.COLOR_RGBA2GRAY);
    small.delete(); small = null;

    blur = new cvv.Mat();
    const kb = o.blurKernel | 1;
    cvv.GaussianBlur(gray, blur, new cvv.Size(kb, kb), 0, 0, cvv.BORDER_DEFAULT);
    gray.delete(); gray = null;

    kernel = cvv.getStructuringElement(cvv.MORPH_RECT, new cvv.Size(o.closeKernel, o.closeKernel));

    let out = [];
    let usedMode = null;
    for (const mode of wanted) {
      edges = new cvv.Mat();
      if (mode === MODE_CANNY) {
        cvv.Canny(blur, edges, o.cannyLo, o.cannyHi, 3, false);
      } else {
        cvv.adaptiveThreshold(
          blur, edges, 255, cvv.ADAPTIVE_THRESH_GAUSSIAN_C,
          cvv.THRESH_BINARY_INV, o.adaptiveBlock | 1, o.adaptiveC,
        );
      }
      closed = new cvv.Mat();
      cvv.morphologyEx(edges, closed, cvv.MORPH_CLOSE, kernel);
      edges.delete(); edges = null;

      contours = new cvv.MatVector();
      hierarchy = new cvv.Mat();
      cvv.findContours(closed, contours, hierarchy, cvv.RETR_EXTERNAL, cvv.CHAIN_APPROX_SIMPLE);
      closed.delete(); closed = null;

      const n = Math.min(contours.size(), o.maxContours);
      const found = [];
      for (let i = 0; i < n; i++) {
        const cnt = contours.get(i);
        const areaPx = cvv.contourArea(cnt, false);
        if (!(areaPx >= o.minContourAreaPx)) { cnt.delete(); continue; }

        // Hull area from cv, on the FULL contour, before any simplification —
        // solidity is a shape statistic and simplifying first would inflate it.
        const hull = new cvv.Mat();
        cvv.convexHull(cnt, hull, false, true);
        const hullAreaPx = cvv.contourArea(hull, false);
        hull.delete();

        const approx = new cvv.Mat();
        const peri = cvv.arcLength(cnt, true);
        cvv.approxPolyDP(cnt, approx, Math.max(0.5, o.simplifyFrac * peri), true);
        const d = approx.data32S;
        const points = [];
        for (let j = 0; j + 1 < d.length; j += 2) points.push([d[j], d[j + 1]]);
        approx.delete();
        cnt.delete();

        if (points.length >= 3) found.push({ points, areaPx, hullAreaPx });
      }
      contours.delete(); contours = null;
      hierarchy.delete(); hierarchy = null;

      out = found;
      usedMode = mode;
      if (found.length > 0) break;
    }

    drop();
    return { contours: out, width: dw, height: dh, scale: sw / dw, mode: usedMode, reason: null };
  } catch (e) {
    drop();
    return fail(`scout_cv_threw: ${(e && e.message) || String(e)}`);
  }
}

/**
 * One full scan: pixels in, tracks out. The composition of contoursFrom,
 * boxesFromContours and trackBoxes, kept in one place so the browser path and
 * any future headless path cannot drift apart.
 */
export function scanSource(cvv, source, state, opts = {}) {
  const found = contoursFrom(cvv, source, opts);
  if (found.reason) {
    // A failed read must not silently retire every id. Age the tracks with an
    // empty box list so they decay through holdFrames exactly as they would on
    // a frame where nothing was found.
    const aged = trackBoxes(state, [], opts);
    return { ...aged, boxes: [], reason: found.reason, mode: null, detectW: 0, detectH: 0 };
  }
  const boxes = boxesFromContours(found.contours, {
    ...opts,
    imageW: found.width,
    imageH: found.height,
    scale: found.scale,
  });
  const next = trackBoxes(state, boxes, opts);
  return {
    ...next,
    boxes,
    reason: boxes.length === 0 ? S_NOTHING_FOUND : null,
    mode: found.mode,
    detectW: found.width,
    detectH: found.height,
  };
}

// ===========================================================================
// 9. THE DOM. Everything below this line touches a document.
// ===========================================================================

function mk(doc, tag, spec = {}) {
  const el = doc.createElement(tag);
  if (spec.class) el.className = spec.class;
  if (spec.id) el.id = spec.id;
  if (spec.text !== undefined) el.textContent = String(spec.text);
  if (spec.attrs) for (const [k, v] of Object.entries(spec.attrs)) el.setAttribute(k, String(v));
  if (spec.style && el.style) for (const [k, v] of Object.entries(spec.style)) el.style[k] = v;
  if (spec.data && el.dataset) for (const [k, v] of Object.entries(spec.data)) el.dataset[k] = String(v);
  for (const kid of spec.kids || []) if (kid) el.appendChild(kid);
  return el;
}

/**
 * SCOUT's own block: the banner, the abstention, and the table of what is on
 * screen right now. The table repeats the OVERLAY'S OWN label strings rather
 * than re-deriving them, so the panel cannot say one thing while the overlay
 * says another.
 */
export function renderScout(model, doc) {
  const st = scoutStatus(model);
  const banner = bannerFor(model);
  return mk(doc, 'section', {
    id: SCOUT_ROOT_ID,
    class: 'scout',
    data: {
      status: st.status,
      why: st.why || '',
      locked: String(model.locked === true),
      boxes: String(model.boxCount || 0),
      simulated: String(model.simulated === true),
    },
    kids: [
      mk(doc, 'h3', { class: 'panel-h3', text: 'SCOUT — live boxes' }),

      banner ? mk(doc, 'p', {
        class: 'scout-banner',
        data: { kind: banner.kind },
        text: banner.text,
      }) : null,

      mk(doc, 'p', {
        class: 'scout-sub',
        text: model.locked === true
          ? 'the mat is locked, so these are measurements in the mat plane: '
            + `${PX_PER_MM_X.toFixed(3)} px/mm across, ${PX_PER_MM_Y.toFixed(3)} down. `
            + (Number.isFinite(model.quantMm)
              ? `An edge cannot be placed better than one detection pixel, which is `
                + `${model.quantMm.toFixed(2)} mm at this scan size — so read the first `
                + `decimal as resolution, not as accuracy.`
              : '')
          : 'edges only — grayscale, blur, edge detect, close, contours, '
            + 'minimum-area rectangle. No classifier, no model weights, no '
            + 'network. A box means "a closed edge is here", nothing more.',
      }),

      mk(doc, 'ul', {
        class: 'scout-list',
        kids: (model.labels || []).map((text) => mk(doc, 'li', { class: 'scout-item', text })),
      }),

      (model.labels || []).length === 0
        ? mk(doc, 'p', { class: 'scout-empty', text: 'nothing on screen.' })
        : null,

      mk(doc, 'p', {
        class: 'scout-meta',
        text: `frames ${model.framesSeen || 0} · boxes ${model.boxCount || 0}`
          + ` · detect ${model.detectW || 0}x${model.detectH || 0}`
          + ` · ${model.mode || 'none'}`
          + ` · ${Number.isFinite(model.msPerScan) ? model.msPerScan.toFixed(1) : '?'} ms/scan`
          + ` · registered as ${model.registeredAs || 'nothing'}`,
      }),

      st.status === 'OK' ? null : mk(doc, 'div', {
        class: 'abstain',
        data: { why: st.why },
        kids: [
          mk(doc, 'div', { class: 'abstain-tag', text: 'I DO NOT KNOW' }),
          mk(doc, 'p', { text: explainReason(st.why) }),
          mk(doc, 'code', { class: 'abstain-why', text: st.why }),
        ],
      }),
    ],
  });
}

/** Inline style for the transparent overlay stacked over a preview canvas. */
export const OVERLAY_STYLE = Object.freeze({
  position: 'absolute',
  left: '0',
  top: '0',
  width: '100%',
  height: '100%',
  objectFit: 'contain',
  background: 'transparent',
  pointerEvents: 'none',
  zIndex: '2',
});

/**
 * Stack a transparent canvas over `target`, matching its backing-store size so
 * one overlay pixel is one target pixel and `object-fit: contain` lays the two
 * out identically. Idempotent: called every frame, creates the node once.
 */
export function ensureOverlay(doc, target, id) {
  if (!doc || !target || typeof doc.createElement !== 'function') return null;
  const parent = target.parentNode;
  if (!parent || typeof parent.insertBefore !== 'function') return null;
  let el = typeof doc.getElementById === 'function' ? doc.getElementById(id) : null;
  if (!el) {
    el = mk(doc, 'canvas', { id, class: 'scout-overlay', style: OVERLAY_STYLE });
    el.setAttribute('aria-hidden', 'true');
    // Right after the target, so a later sibling (the "no camera feed" shade)
    // still covers the overlay when there is no feed to overlay.
    if (target.nextSibling) parent.insertBefore(el, target.nextSibling);
    else parent.appendChild(el);
  }
  if (el.width !== target.width) el.width = target.width;
  if (el.height !== target.height) el.height = target.height;
  return el;
}

// ===========================================================================
// 10. THE PANEL.
// ===========================================================================

export function createPanel(opts = {}) {
  const doc = opts.doc ?? opts.document ?? globalThis.document;
  const cvOf = () => opts.cv ?? globalThis.cv ?? null;
  const now = typeof opts.now === 'function'
    ? opts.now
    : () => (globalThis.performance ? globalThis.performance.now() : Date.now());

  const cfg = {
    everyMs: Number.isFinite(opts.everyMs) ? opts.everyMs : 60,
    drawOnRaw: opts.drawOnRaw !== false,
    simulated: opts.simulated === true,
    rawId: opts.rawId ?? RAW_CANVAS_ID,
    rectId: opts.rectId ?? RECT_CANVAS_ID,
    hostId: opts.hostId ?? SCOUT_HOST_ID,
    // The preview runs small because it runs every frame and states no size.
    // The locked path runs 4x the pixels because it PRINTS A NUMBER, and the
    // number's honesty is bounded by the detection scale — see quantMm below.
    targetWidth: Number.isFinite(opts.targetWidth) ? opts.targetWidth : CV_DEFAULTS.targetWidth,
    rectTargetWidth: Number.isFinite(opts.rectTargetWidth) ? opts.rectTargetWidth : 640,
    ...opts.detect,
  };

  // Two independent trackers. The raw preview and the rectified crop are
  // different coordinate spaces at different scales; sharing one id space
  // between them would make an id mean two things at once.
  let rawState = newTrackState(1);
  let rectState = newTrackState(1);

  let model = {
    locked: false,
    simulated: cfg.simulated,
    framesSeen: 0,
    boxCount: 0,
    labels: [],
    detectW: 0,
    detectH: 0,
    mode: null,
    msPerScan: null,
    quantMm: null,
    opencv: null,
    registeredAs: null,
    reason: S_NO_CAMERA,
  };

  let root = opts.root ?? null;
  let host = opts.host ?? null;
  let lastScanMs = -Infinity;
  let rafId = null;
  let running = false;

  function resolveHost() {
    if (host) return host;
    if (doc && typeof doc.getElementById === 'function') host = doc.getElementById(cfg.hostId);
    return host;
  }

  let lastSig = null;
  let lastPaintMs = -Infinity;

  /**
   * Rebuild the SCOUT block. The overlay is repainted on every scan — that is
   * the point of it — but the block is a dozen DOM nodes and rebuilding it
   * sixteen times a second to change one frame counter is layout churn for no
   * reader. So it repaints when what it SAYS changes, and otherwise at most
   * twice a second to keep the counters moving. `force` is for the callers that
   * know something changed outside the signature.
   */
  function repaint(force = false) {
    if (!doc) return false;
    const h = resolveHost();
    if (!h || typeof h.appendChild !== 'function') return false;
    const t = now();
    const sig = [
      scoutStatus(model).why, model.locked, model.simulated,
      model.registeredAs, model.mode, (model.labels || []).join('|'),
    ].join('~');
    if (!force && sig === lastSig && t - lastPaintMs < 500) return false;
    const fresh = renderScout(model, doc);
    if (root && root.parentNode === h && typeof h.replaceChild === 'function') {
      h.replaceChild(fresh, root);
    } else {
      h.appendChild(fresh);
    }
    root = fresh;
    lastSig = sig;
    lastPaintMs = t;
    return true;
  }

  /**
   * One scan of one canvas. `space` picks which tracker and which scale.
   * Returns the draw summary, or null when nothing could be scanned.
   */
  function scanInto(source, overlayId, space) {
    const cvv = cvOf();
    model.opencv = !!(cvv && typeof cvv.imread === 'function');
    if (!model.opencv) { model.reason = S_NO_OPENCV; return null; }
    if (!source || !(source.width > 0) || !(source.height > 0)) {
      model.reason = S_NO_CAMERA; return null;
    }

    const t0 = now();
    const locked = space === 'rect';
    const state = locked ? rectState : rawState;
    const res = scanSource(cvv, source, state, {
      ...cfg,
      targetWidth: locked ? cfg.rectTargetWidth : cfg.targetWidth,
    });
    if (locked) rectState = { tracks: res.tracks, nextId: res.nextId };
    else rawState = { tracks: res.tracks, nextId: res.nextId };
    const dt = now() - t0;

    model.framesSeen += 1;
    model.msPerScan = model.msPerScan === null ? dt : model.msPerScan * 0.8 + dt * 0.2;
    model.detectW = res.detectW;
    model.detectH = res.detectH;
    model.mode = res.mode;
    model.locked = locked;
    model.reason = res.reason;
    // What one detection pixel is worth, in millimetres, on this scan. An edge
    // cannot be located better than this, so a label printed to 0.1 mm is only
    // meaningful if this number is small — and the panel prints it rather than
    // letting the decimal place imply a precision the pipeline does not have.
    model.quantMm = locked && res.detectW > 0
      ? (BUF_W / res.detectW) / PX_PER_MM_X
      : null;

    const overlay = ensureOverlay(doc, source, overlayId);
    const ctx = overlay && typeof overlay.getContext === 'function'
      ? overlay.getContext('2d') : null;
    const drawn = drawScout(ctx, res.tracks, {
      width: source.width,
      height: source.height,
      // The pane is laid out with object-fit: contain, so the denominator for
      // type size is the LETTERBOXED content width, not the element box.
      // Absent in a test DOM, where drawScaleFor falls back to the buffer.
      cssWidth: overlay
        ? (containedWidth(source.width, source.height, overlay.clientWidth, overlay.clientHeight)
          ?? undefined)
        : undefined,
      locked,
      simulated: cfg.simulated,
      pxPerMmX: locked ? PX_PER_MM_X * (source.width / BUF_W) : PX_PER_MM_X,
      pxPerMmY: locked ? PX_PER_MM_Y * (source.height / BUF_H) : PX_PER_MM_Y,
    });

    model.boxCount = res.tracks.filter((t) => (t.missing || 0) === 0).length;
    model.labels = drawn.labels;
    return drawn;
  }

  /** Clear an overlay without destroying it — used when SCOUT stands down. */
  function clearOverlay(source, overlayId) {
    const overlay = source ? ensureOverlay(doc, source, overlayId) : null;
    const ctx = overlay && typeof overlay.getContext === 'function' ? overlay.getContext('2d') : null;
    if (ctx && typeof ctx.clearRect === 'function') ctx.clearRect(0, 0, overlay.width, overlay.height);
  }

  const api = {
    id: SCOUT_ID,
    title: PANEL_TITLE,
    get model() { return model; },
    get rawTracks() { return rawState.tracks; },
    get rectTracks() { return rectState.tracks; },

    /** Tell SCOUT the frames are synthetic. The banner then says so, always. */
    setSimulated(flag) { cfg.simulated = flag === true; model.simulated = cfg.simulated; repaint(true); },

    /** Which registry id this panel actually took. Set by attach(), not guessed. */
    setRegisteredAs(id) { model.registeredAs = id; repaint(true); return id; },

    /** DRAW-ON-RAW: the switch the shell flips for the CORE panel. */
    setDrawOnRaw(flag) {
      cfg.drawOnRaw = flag !== false;
      if (!cfg.drawOnRaw && doc && typeof doc.getElementById === 'function') {
        clearOverlay(doc.getElementById(cfg.rawId), SCOUT_OVERLAY_ID);
      }
      return cfg.drawOnRaw;
    },
    get drawOnRaw() { return cfg.drawOnRaw; },

    /**
     * The registry's frame hook. Handed ONLY the rectified 840x1188 crop and
     * only while the mat is locked — so this is the path on which a label may
     * carry millimetres.
     */
    onFrame(frame) {
      if (!frame || frame.cropKind !== 'rectified_mat_crop' || !frame.crop) return false;
      model.locked = true;
      const drawn = scanInto(frame.crop, SCOUT_RECT_OVERLAY_ID, 'rect');
      // The raw pane belongs to app.js once the mat is locked: it draws the
      // scrim, the quad and the price glyphs there. A preview box over the top
      // of that could not be measured anyway, so SCOUT gets out of the way.
      if (doc && typeof doc.getElementById === 'function') {
        clearOverlay(doc.getElementById(cfg.rawId), SCOUT_OVERLAY_ID);
      }
      repaint();
      return drawn !== null;
    },

    /** The registry's state hook. Only the lock state matters to SCOUT. */
    onState(view) {
      if (view && typeof view === 'object') {
        const locked = view.matLocked === true;
        if (locked !== model.locked) {
          model.locked = locked;
          if (!locked) {
            // The mat went away: the millimetres went with it. Retire the
            // rectified ids rather than carry stale measurements forward.
            rectState = newTrackState(rectState.nextId);
            model.labels = [];
            model.boxCount = 0;
          }
        }
      }
      repaint();
      return true;
    },

    /** One tick of the unlocked preview loop. Exposed so a test can step it. */
    tick(ts) {
      const t = Number.isFinite(ts) ? ts : now();
      if (t - lastScanMs < cfg.everyMs) return null;
      lastScanMs = t;
      if (model.locked || !cfg.drawOnRaw) return null;
      if (!doc || typeof doc.getElementById !== 'function') return null;
      const src = doc.getElementById(cfg.rawId);
      if (!src) { model.reason = S_NO_CAMERA; return null; }
      const drawn = scanInto(src, SCOUT_OVERLAY_ID, 'raw');
      repaint();
      return drawn;
    },

    /** Start the preview loop. Idempotent. */
    start() {
      if (running) return false;
      running = true;
      const loop = (t) => {
        if (!running) return;
        try { api.tick(t); }
        catch (e) { model.reason = `scout_tick_threw: ${(e && e.message) || e}`; }
        rafId = globalThis.requestAnimationFrame
          ? globalThis.requestAnimationFrame(loop)
          : null;
      };
      rafId = globalThis.requestAnimationFrame ? globalThis.requestAnimationFrame(loop) : null;
      return true;
    },

    stop() {
      running = false;
      if (rafId !== null && globalThis.cancelAnimationFrame) globalThis.cancelAnimationFrame(rafId);
      rafId = null;
      return true;
    },
    get running() { return running; },

    repaint,
  };
  return api;
}

/**
 * Register on the seam. 'scout' is the id this module wants; app.js's PANEL_IDS
 * does not carry it, so the fallback is 'core' — the panel these boxes are
 * drawn over. The id that was actually taken goes on the model and is printed
 * in the block, because "which panel am I" is not a thing to assume.
 */
export function attach(register, opts = {}) {
  if (typeof register !== 'function') {
    throw new TypeError('attach(register): registerPanel must be a function');
  }
  const panel = opts.panel ?? createPanel(opts);
  const ids = Array.isArray(opts.registryIds) ? opts.registryIds : REGISTRY_IDS;
  const tried = [];
  let taken = null;
  for (const id of ids) {
    let r;
    try { r = register(id, { onState: panel.onState, onFrame: panel.onFrame }); }
    catch (e) { tried.push({ id, ok: false, reason: (e && e.message) || String(e) }); continue; }
    tried.push({ id, ok: !!(r && r.ok), reason: (r && r.reason) || null });
    if (r && r.ok) { taken = id; break; }
  }
  if (typeof panel.setRegisteredAs === 'function') panel.setRegisteredAs(taken);
  return { panel, registeredAs: taken, tried };
}

export function attachScoutPanel(opts = {}) {
  const panel = createPanel(opts);
  const register = typeof opts.register === 'function'
    ? opts.register
    : (typeof globalThis.registerPanel === 'function' ? globalThis.registerPanel : null);
  const out = register ? attach(register, { ...opts, panel }) : { panel, registeredAs: null, tried: [] };
  panel.start();
  return { ...out, registered: register !== null };
}

/** See the other panels: the shell may import `attach`, or drain GAWAAH_PANELS. */
export const DESCRIPTOR = {
  id: SCOUT_ID,
  title: PANEL_TITLE,
  createPanel,
  attach,
  attached: false,
  panel: null,
};

if (typeof globalThis !== 'undefined' && typeof globalThis.document !== 'undefined') {
  /**
   * Construct the panel and start drawing. Deliberately does NOT register:
   * the overlay is SCOUT's own node over app.js's canvas and needs no seam to
   * paint, which is the whole point — boxes appear the moment the camera opens,
   * before the brain, the mat or the registry exist. Idempotent.
   */
  const boot = () => {
    if (DESCRIPTOR.panel) return DESCRIPTOR.panel;
    const panel = createPanel({});
    DESCRIPTOR.panel = panel;
    panel.start();
    // The switch the shell flips, reachable from a plain script as well as a
    // module — app.js's window.GAWAAH is frozen, so SCOUT gets its own handle.
    globalThis.GAWAAH_SCOUT = {
      panel,
      setDrawOnRaw: (f) => panel.setDrawOnRaw(f),
      setSimulated: (f) => panel.setSimulated(f),
      tick: (t) => panel.tick(t),
      start: () => panel.start(),
      stop: () => panel.stop(),
      get drawOnRaw() { return panel.drawOnRaw; },
      get model() { return panel.model; },
    };
    return panel;
  };

  /** Register exactly once, whichever of the two load orders got here first. */
  const bootAndAttach = (register) => {
    const panel = boot();
    const fn = typeof register === 'function' ? register : globalThis.registerPanel;
    if (DESCRIPTOR.attached || typeof fn !== 'function') return { panel, registeredAs: null, tried: [] };
    const r = attach(fn, { panel });
    DESCRIPTOR.attached = r.registeredAs !== null;
    return r;
  };

  const d = globalThis.document;
  if (d.readyState === 'loading' && typeof d.addEventListener === 'function') {
    d.addEventListener('DOMContentLoaded', () => bootAndAttach());
  } else {
    bootAndAttach();
  }
  // Whoever drains the queue calls this; boot() has already run either way.
  DESCRIPTOR.attach = (register) => bootAndAttach(register);
  (globalThis.GAWAAH_PANELS ||= []).push(DESCRIPTOR);
}

export default {
  SCOUT_ID, createPanel, attach, boxesFromContours, trackBoxes, labelFor,
  bannerFor, drawScout, contoursFrom, scanSource, minAreaRect, convexHull,
};
