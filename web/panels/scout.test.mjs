/* GAWAAH — SCOUT self-test. No browser, no bundler, no network.
 *
 *   cd /Users/agnik/Desktop/razor && node web/panels/scout.test.mjs
 *
 * web/ has no package.json, so node would load a bare `.js` as CommonJS and
 * choke on `export`. scout.js is therefore loaded through a data: URL, the same
 * trick web/selftest.mjs uses for app.js — which is also why scout.js imports
 * nothing: a data: URL cannot resolve a relative specifier.
 *
 * Three things are proved here that a screenshot cannot prove:
 *
 *   1. THE LABEL HAS EXACTLY TWO FORMS. `labelFor` is run over thousands of
 *      random tracks and every single output is asserted against one of two
 *      exact regexes. There is no path — no scale, no id, no degenerate box —
 *      by which a class name or a confidence number can appear on screen.
 *
 *   2. THE OPENCV BRIDGE LEAKS NOTHING. A fake `cv` counts every Mat it hands
 *      out and every Mat that comes back; contoursFrom must return with the
 *      count at zero, on the happy path, on the fallback path and on the throw
 *      path. The same fake proves the returned object is integers only — no
 *      ImageData, no canvas, no typed array — so invariant 4 holds by
 *      construction rather than by promise.
 *
 *   3. IDS ARE STABLE. A box is walked across a synthetic scene for 40 frames,
 *      blinked out, and brought back; its id must survive all of it, and a
 *      genuinely new object must never inherit a retired id.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SCOUT_PATH = join(HERE, 'scout.js');
const SCOUT_SRC = readFileSync(SCOUT_PATH, 'utf8');
const load = (src) => import('data:text/javascript;charset=utf-8;base64,'
  + Buffer.from(src, 'utf8').toString('base64'));
const S = await load(SCOUT_SRC);

const readOr = (p) => { try { return readFileSync(p, 'utf8'); } catch { return null; } };
const APP_SRC = readOr(join(HERE, '..', 'app.js'));
const HTML_SRC = readOr(join(HERE, '..', 'index.html'));

// ---------------------------------------------------------------- harness --
let pass = 0, fail = 0, group = '';
const failures = [];
const measured = {};
function T(name, fn) {
  try { fn(); pass++; }
  catch (e) { fail++; failures.push(`${group} :: ${name}\n      ${e.message}`); }
}
function G(name) { group = name; console.log(`\n── ${name}`); }
function ok(cond, msg) { if (!cond) throw new Error(msg || 'assertion failed'); }
function eq(a, b, msg) {
  if (!Object.is(a, b)) throw new Error(`${msg || 'eq'}: got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
}
function near(a, b, tol, msg) {
  if (!(Math.abs(a - b) <= tol)) {
    throw new Error(`${msg || 'near'}: |${a} - ${b}| = ${Math.abs(a - b)} > ${tol}`);
  }
}
function includes(hay, needle, msg) {
  if (!String(hay).includes(needle)) {
    throw new Error(`${msg || 'includes'}: ${JSON.stringify(needle)} not in ${JSON.stringify(String(hay).slice(0, 300))}`);
  }
}
function excludes(hay, needle, msg) {
  if (String(hay).includes(needle)) {
    throw new Error(`${msg || 'excludes'}: ${JSON.stringify(needle)} IS present`);
  }
}
function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
/** Strip comments; string literals survive so UI copy is still linted. */
function stripComments(src) {
  let out = '';
  for (let i = 0; i < src.length;) {
    const c = src[i], d = src[i + 1];
    if (c === '/' && d === '/') { while (i < src.length && src[i] !== '\n') i++; continue; }
    if (c === '/' && d === '*') {
      i += 2;
      while (i < src.length && !(src[i] === '*' && src[i + 1] === '/')) i++;
      i += 2; continue;
    }
    if (c === '"' || c === "'" || c === '`') {
      out += c; i++;
      while (i < src.length && src[i] !== c) { out += src[i]; if (src[i] === '\\') { out += src[i + 1] ?? ''; i++; } i++; }
      out += c; i++; continue;
    }
    out += c; i++;
  }
  return out;
}

// ------------------------------------------------------------- generators --
/** Corner list of an axis-aligned then rotated rectangle, as a contour. */
function rectContour(cx, cy, w, h, deg, n = 4) {
  const r = deg * Math.PI / 180;
  const c = Math.cos(r), s = Math.sin(r);
  const base = [[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]];
  const quad = base.map(([x, y]) => [cx + x * c - y * s, cy + x * s + y * c]);
  if (n <= 4) return quad;
  // densify the edges, which is what a real contour tracer returns
  const out = [];
  for (let i = 0; i < 4; i++) {
    const a = quad[i], b = quad[(i + 1) % 4];
    const k = Math.max(1, Math.round(n / 4));
    for (let j = 0; j < k; j++) out.push([a[0] + (b[0] - a[0]) * j / k, a[1] + (b[1] - a[1]) * j / k]);
  }
  return out;
}

/** A star: same bounding box as a blob, far lower solidity. Must be rejected. */
function starContour(cx, cy, rOuter, rInner, points = 7) {
  const out = [];
  for (let i = 0; i < points * 2; i++) {
    const r = i % 2 === 0 ? rOuter : rInner;
    const a = (i / (points * 2)) * Math.PI * 2;
    out.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
  }
  return out;
}

// ================================================================ 1. GEOMETRY
G('pure geometry — hull, area, minAreaRect');

T('polygonArea is the shoelace area and ignores winding', () => {
  const sq = [[0, 0], [10, 0], [10, 10], [0, 10]];
  eq(S.polygonArea(sq), 100, 'ccw square');
  eq(S.polygonArea([...sq].reverse()), 100, 'cw square');
  eq(S.polygonArea([[0, 0], [1, 1]]), 0, 'a segment has no area');
  eq(S.polygonArea(null), 0, 'null');
});

T('convexHull drops interior points and duplicates', () => {
  const pts = [[0, 0], [10, 0], [10, 10], [0, 10], [5, 5], [5, 5], [2, 3], [9, 1]];
  const h = S.convexHull(pts);
  eq(h.length, 4, `hull of a square with junk inside: ${JSON.stringify(h)}`);
  near(S.polygonArea(h), 100, 1e-9, 'hull area');
  ok(!h.some(([x, y]) => x === 5 && y === 5), 'an interior point survived the hull');
});

T('convexHull of a star is the outer points only', () => {
  const h = S.convexHull(starContour(0, 0, 20, 8, 7));
  eq(h.length, 7, 'seven outer spikes');
});

T('minAreaRect of an axis-aligned rectangle is that rectangle', () => {
  const r = S.minAreaRect(rectContour(100, 50, 40, 20, 0));
  near(r.cx, 100, 1e-6, 'cx'); near(r.cy, 50, 1e-6, 'cy');
  near(r.longPx, 40, 1e-6, 'long'); near(r.shortPx, 20, 1e-6, 'short');
  eq(r.angleDeg, 0, 'angle');
  near(r.areaPx, 800, 1e-6, 'area');
  eq(r.corners.length, 4, 'four corners');
});

T('minAreaRect recovers the rotation of a rotated rectangle', () => {
  for (const deg of [0, 12, 30, 45, 67, 89, -30, -75]) {
    const r = S.minAreaRect(rectContour(200, 300, 60, 25, deg, 40));
    near(r.longPx, 60, 0.75, `long at ${deg}deg`);
    near(r.shortPx, 25, 0.75, `short at ${deg}deg`);
    const want = S.normaliseAngleDeg(deg);
    const got = r.angleDeg;
    const diff = Math.min(Math.abs(got - want), 180 - Math.abs(got - want));
    ok(diff < 1.2, `angle at ${deg}deg: got ${got}, want ${want}`);
    near(r.cx, 200, 0.6, `cx at ${deg}`);
    near(r.cy, 300, 0.6, `cy at ${deg}`);
  }
});

T('minAreaRect never returns a larger area than the axis-aligned box', () => {
  const rnd = mulberry32(7);
  let checked = 0;
  for (let i = 0; i < 400; i++) {
    const pts = [];
    for (let j = 0; j < 3 + Math.floor(rnd() * 12); j++) pts.push([rnd() * 500, rnd() * 500]);
    const r = S.minAreaRect(pts);
    if (!r) continue;
    const b = S.boundsOf(pts);
    const aabb = (b.x1 - b.x0) * (b.y1 - b.y0);
    ok(r.areaPx <= aabb + 1e-6, `minAreaRect ${r.areaPx} > aabb ${aabb}`);
    // and it must contain every input point
    for (const p of pts) {
      const u = p[0] * r.axis[0] + p[1] * r.axis[1];
      const cu = r.cx * r.axis[0] + r.cy * r.axis[1];
      ok(Math.abs(u - cu) <= r.longPx / 2 + 1e-6, 'a point fell outside the long axis');
    }
    checked++;
  }
  measured.minarearect_random_hulls = String(checked);
  ok(checked > 350, 'too few random hulls survived');
});

T('minAreaRect degenerates honestly', () => {
  eq(S.minAreaRect([]), null, 'empty');
  const one = S.minAreaRect([[5, 5]]);
  eq(one.longPx, 0, 'a single point has no extent');
  const seg = S.minAreaRect([[0, 0], [10, 0]]);
  near(seg.longPx, 10, 1e-9, 'segment length');
  eq(seg.shortPx, 0, 'segment has no width');
});

T('normaliseAngleDeg maps every bearing into (-90, 90]', () => {
  for (let d = -720; d <= 720; d += 1) {
    const a = S.normaliseAngleDeg(d);
    ok(a > -90 && a <= 90, `${d} -> ${a}`);
    const same = ((d - a) % 180 + 180) % 180;
    ok(same < 1e-9 || Math.abs(same - 180) < 1e-9, `${d} -> ${a} is not the same line`);
  }
  eq(S.normaliseAngleDeg(0), 0, 'no negative zero');
  eq(Object.is(S.normaliseAngleDeg(180), 0), true, '180 is 0, not -0');
});

T('iou is 1 for identical bounds and 0 for disjoint', () => {
  const a = { x0: 0, y0: 0, x1: 10, y1: 10 };
  eq(S.iou(a, a), 1, 'identical');
  eq(S.iou(a, { x0: 20, y0: 20, x1: 30, y1: 30 }), 0, 'disjoint');
  near(S.iou(a, { x0: 5, y0: 0, x1: 15, y1: 10 }), 50 / 150, 1e-9, 'half overlap');
});

// ========================================================== 2. THE BOX FILTER
G('boxesFromContours — the shape gate');

const FRAME = { imageW: 320, imageH: 240 };

T('a plain rectangle passes and comes back oriented', () => {
  const boxes = S.boxesFromContours([rectContour(160, 120, 60, 30, 20, 40)], FRAME);
  eq(boxes.length, 1, 'one box');
  near(boxes[0].longPx, 60, 1, 'long');
  near(boxes[0].shortPx, 30, 1, 'short');
  near(boxes[0].solidity, 1, 0.02, 'a rectangle is solid');
  eq(boxes[0].corners.length, 4, 'oriented, four corners');
});

T('a contour below the area floor is rejected as texture', () => {
  const tiny = rectContour(50, 50, 6, 6, 0);        // 36 px in a 76800 px frame
  eq(S.boxesFromContours([tiny], FRAME).length, 0, 'a speck became a box');
  const big = rectContour(50, 50, 40, 40, 0);
  eq(S.boxesFromContours([big], FRAME).length, 1, 'a real object was dropped');
});

T('a contour above the area ceiling is rejected as the table', () => {
  const whole = rectContour(160, 120, 310, 230, 0);  // 92% of the frame
  eq(S.boxesFromContours([whole], FRAME).length, 0, 'the whole frame became a box');
});

T('a low-solidity edge web is rejected', () => {
  const star = starContour(160, 120, 45, 9, 9);
  const sol = S.polygonArea(star) / S.polygonArea(S.convexHull(star));
  measured.star_solidity = sol.toFixed(3);
  ok(sol < S.BOX_DEFAULTS.minSolidity, `test star is not spiky enough: ${sol}`);
  eq(S.boxesFromContours([star], FRAME).length, 0, 'a shattered edge web became a box');
});

T('a 20:1 sliver is rejected as a table edge', () => {
  const sliver = rectContour(160, 120, 300, 12, 0);
  const boxes = S.boxesFromContours([sliver], { ...FRAME, maxAreaFrac: 0.99 });
  eq(boxes.length, 0, `a ${(300 / 12).toFixed(0)}:1 sliver became a box`);
});

T('a box thinner than minShortPx is rejected as a line', () => {
  const line = rectContour(160, 120, 90, 3, 0);
  eq(S.boxesFromContours([line], { ...FRAME, minAreaFrac: 0.0001 }).length, 0, 'a line became a box');
});

T('nested Canny rings collapse to one box', () => {
  const outer = rectContour(160, 120, 62, 32, 10, 40);
  const inner = rectContour(160, 120, 58, 28, 10, 40);
  const boxes = S.boxesFromContours([outer, inner], FRAME);
  eq(boxes.length, 1, 'the double edge drew two boxes');
  near(boxes[0].longPx, 62, 1.5, 'the OUTER ring survived');
});

T('two genuinely separate objects both survive', () => {
  const a = rectContour(80, 120, 50, 30, 0, 24);
  const b = rectContour(240, 120, 50, 30, 0, 24);
  eq(S.boxesFromContours([a, b], FRAME).length, 2, 'two objects, two boxes');
});

T('maxBoxes caps the overlay and keeps the biggest', () => {
  const many = [];
  for (let i = 0; i < 40; i++) many.push(rectContour(20 + (i % 8) * 36, 20 + Math.floor(i / 8) * 44, 20 + i, 18 + i * 0.4, 0, 12));
  const boxes = S.boxesFromContours(many, { ...FRAME, maxBoxes: 5, dedupeIou: 0.99 });
  eq(boxes.length, 5, 'the cap did not hold');
  for (let i = 1; i < boxes.length; i++) {
    ok(boxes[i - 1].rectAreaPx >= boxes[i].rectAreaPx, 'not sorted largest-first');
  }
});

T('cv-supplied area and hull area are used in preference to the polygon', () => {
  // the same points, declared to be a low-solidity blob by the bridge
  const pts = rectContour(160, 120, 60, 30, 0);
  const passed = S.boxesFromContours([{ points: pts, areaPx: 1800, hullAreaPx: 1800 }], FRAME);
  eq(passed.length, 1, 'solid: should pass');
  const failed = S.boxesFromContours([{ points: pts, areaPx: 500, hullAreaPx: 1800 }], FRAME);
  eq(failed.length, 0, `solidity ${(500 / 1800).toFixed(2)} should have been rejected`);
});

T('scale maps detection coordinates back to source coordinates', () => {
  const pts = rectContour(160, 120, 60, 30, 0, 24);
  const at1 = S.boxesFromContours([pts], FRAME)[0];
  const at4 = S.boxesFromContours([pts], { ...FRAME, scale: 4 })[0];
  near(at4.longPx, at1.longPx * 4, 1e-6, 'long side scaled');
  near(at4.cx, at1.cx * 4, 1e-6, 'centre scaled');
  eq(at4.angleDeg, at1.angleDeg, 'scaling must not rotate the box');
});

T('boxesFromContours is pure — same input, same output, twice', () => {
  const cs = [rectContour(80, 90, 50, 30, 17, 20), rectContour(220, 150, 44, 40, -8, 20)];
  const a = JSON.stringify(S.boxesFromContours(cs, FRAME));
  const b = JSON.stringify(S.boxesFromContours(cs, FRAME));
  eq(a, b, 'two runs differed');
  eq(cs[0].length, 20, 'the input was mutated');
});

T('garbage in, empty out — never a throw', () => {
  for (const junk of [null, undefined, 42, 'x', [null], [[]], [[[1, 2]]], [{ points: 'no' }]]) {
    const r = S.boxesFromContours(junk, FRAME);
    ok(Array.isArray(r), `threw or returned non-array for ${JSON.stringify(junk)}`);
    eq(r.length, 0, `invented a box from ${JSON.stringify(junk)}`);
  }
});

// ============================================================== 3. THE TRACKER
G('trackBoxes — a stable id, which is the whole point of a tracker');

function boxAt(cx, cy, w = 40, h = 20) {
  return S.boxesFromContours([rectContour(cx, cy, w, h, 0, 16)], FRAME)[0];
}

T('the first frame numbers boxes from 1', () => {
  const r = S.trackBoxes(S.newTrackState(), [boxAt(80, 80), boxAt(200, 80)]);
  eq(r.tracks.length, 2, 'two tracks');
  eq(r.tracks[0].id, 1, 'first id');
  eq(r.tracks[1].id, 2, 'second id');
  eq(r.nextId, 3, 'nextId');
  ok(!r.tracks.some((t) => t.id === 0), 'id:0 appeared');
});

T('an object walked across 40 frames keeps one id', () => {
  let st = S.newTrackState();
  const ids = new Set();
  for (let f = 0; f < 40; f++) {
    st = S.trackBoxes(st, [boxAt(40 + f * 6, 120)], { ts: f });
    eq(st.tracks.length, 1, `frame ${f}: track count`);
    ids.add(st.tracks[0].id);
  }
  eq(ids.size, 1, `the id flickered: ${[...ids].join(',')}`);
  eq(st.tracks[0].id, 1, 'and it is id 1');
  eq(st.tracks[0].age, 40, 'age counted every frame');
  eq(st.nextId, 2, 'no ids were burned');
});

T('a one-frame dropout does not renumber the box', () => {
  let st = S.newTrackState();
  st = S.trackBoxes(st, [boxAt(100, 100)]);
  const id = st.tracks[0].id;
  st = S.trackBoxes(st, []);                       // blink
  eq(st.tracks.length, 1, 'the held track was dropped immediately');
  eq(st.tracks[0].missing, 1, 'missing not counted');
  st = S.trackBoxes(st, [boxAt(104, 101)]);        // back
  eq(st.tracks.length, 1, 'a duplicate track appeared');
  eq(st.tracks[0].id, id, 'the id changed after a blink');
  eq(st.tracks[0].missing, 0, 'still marked missing after returning');
});

T('a track that stays gone past holdFrames retires, and its id is not reused', () => {
  let st = S.newTrackState();
  st = S.trackBoxes(st, [boxAt(100, 100)]);
  const gone = st.tracks[0].id;
  for (let i = 0; i <= S.TRACK_DEFAULTS.holdFrames; i++) st = S.trackBoxes(st, []);
  eq(st.tracks.length, 0, 'a track outlived holdFrames');
  st = S.trackBoxes(st, [boxAt(100, 100)]);
  ok(st.tracks[0].id !== gone, 'a NEW object inherited a retired id');
  eq(st.tracks[0].id, gone + 1, 'ids must keep counting up');
});

T('a teleport past maxDistPx is a new object, not the same one moving', () => {
  let st = S.newTrackState();
  st = S.trackBoxes(st, [boxAt(40, 40)]);
  const first = st.tracks[0].id;
  st = S.trackBoxes(st, [boxAt(300, 220)], { maxDistPx: 50 });
  const ids = st.tracks.map((t) => t.id).sort((a, b) => a - b);
  eq(ids.length, 2, 'the far box was matched to the near track');
  eq(ids[0], first, 'the original id vanished');
});

T('a size change beyond the ratio gate breaks the match', () => {
  let st = S.newTrackState();
  st = S.trackBoxes(st, [boxAt(160, 120, 30, 20)]);
  const first = st.tracks[0].id;
  st = S.trackBoxes(st, [boxAt(160, 120, 200, 120)]);
  ok(st.tracks.some((t) => t.id !== first), 'a 6x size jump was accepted as the same object');
});

T('two objects crossing keep their own ids', () => {
  let st = S.newTrackState();
  const path = [];
  for (let f = 0; f < 12; f++) path.push([60 + f * 8, 260 - f * 8]);
  for (const [a, b] of path) st = S.trackBoxes(st, [boxAt(a, 100), boxAt(b, 160)]);
  eq(st.tracks.length, 2, 'two tracks survive');
  eq(st.tracks.map((t) => t.id).join(','), '1,2', 'ids changed while crossing');
});

T('the tracker is deterministic — two identical runs give identical ids', () => {
  const rnd1 = mulberry32(99), rnd2 = mulberry32(99);
  let a = S.newTrackState(), b = S.newTrackState();
  for (let f = 0; f < 30; f++) {
    const boxesA = [boxAt(50 + rnd1() * 200, 50 + rnd1() * 150), boxAt(50 + rnd1() * 200, 50 + rnd1() * 150)];
    const boxesB = [boxAt(50 + rnd2() * 200, 50 + rnd2() * 150), boxAt(50 + rnd2() * 200, 50 + rnd2() * 150)];
    a = S.trackBoxes(a, boxesA); b = S.trackBoxes(b, boxesB);
  }
  eq(a.tracks.map((t) => t.id).join(','), b.tracks.map((t) => t.id).join(','), 'ids diverged');
  eq(a.nextId, b.nextId, 'nextId diverged');
});

T('trackBoxes is pure — the state passed in is not mutated', () => {
  const st = S.trackBoxes(S.newTrackState(), [boxAt(100, 100)]);
  const before = JSON.stringify(st);
  S.trackBoxes(st, [boxAt(180, 180)]);
  eq(JSON.stringify(st), before, 'the previous state was mutated');
});

T('tracks come back sorted by id, so the overlay draw order is stable', () => {
  let st = S.newTrackState();
  st = S.trackBoxes(st, [boxAt(60, 60), boxAt(260, 200)]);
  st = S.trackBoxes(st, [boxAt(260, 200), boxAt(60, 60)]);   // reported in the other order
  eq(st.tracks.map((t) => t.id).join(','), '1,2', 'draw order followed the detector, not the ids');
});

// ================================================= 4. THE LABEL — THE HONESTY
G('labelFor — exactly two forms, and no third');

T('the unlocked label is the exact agreed string', () => {
  eq(S.labelFor({ id: 12, longPx: 100, shortPx: 50 }, { locked: false }),
    'id:12  object  (size unknown)', 'unlocked label');
});

T('the locked label is a real measurement in millimetres', () => {
  // a 60mm x 32mm object in the rectified buffer, axis-aligned
  const t = {
    id: 12,
    longPx: 60 * S.PX_PER_MM_X,
    shortPx: 32 * S.PX_PER_MM_Y,
    axis: [1, 0],
  };
  eq(S.labelFor(t, { locked: true }), 'id:12  60.0 x 32.0 mm', 'locked label');
});

T('a rotated box is measured along its own axes, not by one divisor', () => {
  const deg = 37, r = deg * Math.PI / 180;
  const axis = [Math.cos(r), Math.sin(r)];
  const longMm = 58.4, shortMm = 31.2;
  const longPx = longMm / Math.hypot(axis[0] / S.PX_PER_MM_X, axis[1] / S.PX_PER_MM_Y);
  const shortPx = shortMm / Math.hypot(axis[1] / S.PX_PER_MM_X, axis[0] / S.PX_PER_MM_Y);
  eq(S.labelFor({ id: 12, longPx, shortPx, axis }, { locked: true }),
    'id:12  58.4 x 31.2 mm', 'the exact label from the spec');
});

T('a locked box with no usable scale says the size is unknown, never NaN', () => {
  const t = { id: 4, longPx: 100, shortPx: 40, axis: [1, 0] };
  for (const bad of [NaN, 0, -1, Infinity, null, undefined, 'x']) {
    const s = S.labelFor(t, { locked: true, pxPerMmX: bad, pxPerMmY: bad });
    eq(s, 'id:4  object  (size unknown)', `pxPerMm=${String(bad)} produced ${s}`);
    excludes(s, 'NaN', 'NaN reached the label');
    excludes(s, 'Infinity', 'Infinity reached the label');
  }
});

T('a degenerate box says the size is unknown rather than printing 0.0 x 0.0', () => {
  eq(S.labelFor({ id: 9, longPx: 0, shortPx: 0, axis: [1, 0] }, { locked: true }),
    'id:9  object  (size unknown)', 'zero-size box');
  eq(S.labelFor({ id: 9, longPx: NaN, shortPx: 4, axis: [1, 0] }, { locked: true }),
    'id:9  object  (size unknown)', 'NaN-size box');
});

T('an absurd size is refused rather than printed', () => {
  const s = S.labelFor({ id: 3, longPx: 1e9, shortPx: 1e9, axis: [1, 0] }, { locked: true });
  eq(s, 'id:3  object  (size unknown)', 'a ten-kilometre object was printed as fact');
});

T('a missing id degrades to id:? and never to id:undefined', () => {
  const s = S.labelFor({ longPx: 100, shortPx: 40 }, { locked: false });
  eq(s, 'id:?  object  (size unknown)', 'missing id');
  excludes(s, 'undefined', 'undefined reached the label');
  excludes(s, 'NaN', 'NaN reached the label');
});

T('*** 6000 random tracks produce ONLY the two agreed label forms ***', () => {
  const rnd = mulberry32(20260829);
  let unlocked = 0, locked = 0;
  for (let i = 0; i < 6000; i++) {
    const deg = rnd() * 360 - 180;
    const r = deg * Math.PI / 180;
    const t = {
      id: Math.floor(rnd() * 100000),
      longPx: rnd() * 900 + 0.1,
      shortPx: rnd() * 400 + 0.1,
      axis: [Math.cos(r), Math.sin(r)],
    };
    if (t.shortPx > t.longPx) { const x = t.longPx; t.longPx = t.shortPx; t.shortPx = x; }
    const isLocked = rnd() < 0.5;
    const s = S.labelFor(t, { locked: isLocked });
    const okUnlocked = S.LABEL_UNLOCKED_RE.test(s);
    const okLocked = S.LABEL_LOCKED_RE.test(s);
    ok(okUnlocked || okLocked, `a third label form appeared: ${JSON.stringify(s)}`);
    ok(!(okUnlocked && okLocked), 'the two regexes are not disjoint');
    if (isLocked) { ok(okLocked, `locked track produced ${JSON.stringify(s)}`); locked++; }
    else { ok(okUnlocked, `unlocked track produced ${JSON.stringify(s)}`); unlocked++; }
    for (const forbidden of ['%', 'conf', 'class', 'prob', 'score', 'pothole', 'bottle', 'detect']) {
      excludes(s.toLowerCase(), forbidden, `label leaked "${forbidden}": ${s}`);
    }
  }
  measured.labels_checked = String(unlocked + locked);
  measured.labels_unlocked = String(unlocked);
  measured.labels_locked = String(locked);
});

T('no number in a label can be read as a confidence', () => {
  // a confidence is a bare 0..1 decimal. Every number in a locked label is
  // followed by " mm" or is part of the id; there is no naked float anywhere.
  const s = S.labelFor({ id: 7, longPx: 0.9 * S.PX_PER_MM_X, shortPx: 0.7 * S.PX_PER_MM_Y, axis: [1, 0] }, { locked: true });
  eq(s, 'id:7  0.9 x 0.7 mm', 'sub-millimetre label');
  ok(/mm$/.test(s), 'a locked label must end in a unit');
});

// ================================================================ 5. BANNER
G('bannerFor — the claim that must be qualified');

T('unlocked always carries the preview banner', () => {
  const b = S.bannerFor({ locked: false });
  eq(b.text, 'PREVIEW - boxes only. No mat, so no measurement and nothing billable.',
    'the exact banner');
  eq(b.kind, 'preview', 'kind');
  eq(b.simulated, false, 'not simulated');
  includes(b.text, 'nothing billable', 'the banner must say nothing is billable');
});

T('locked and real is the ONLY case with no banner', () => {
  eq(S.bannerFor({ locked: true, simulated: false }), null, 'locked+real');
  ok(S.bannerFor({ locked: false, simulated: false }) !== null, 'unlocked+real');
  ok(S.bannerFor({ locked: false, simulated: true }) !== null, 'unlocked+sim');
  ok(S.bannerFor({ locked: true, simulated: true }) !== null, 'locked+sim');
});

T('a simulated feed is labelled SIMULATED even when the mat is locked', () => {
  const b = S.bannerFor({ locked: true, simulated: true });
  includes(b.text, 'SIMULATED FEED', 'invariant 7: simulated must be visible');
  eq(b.simulated, true, 'flagged');
  includes(b.text, 'not from a camera', 'must say where the pixels came from');
  includes(b.text, 'billable', 'must say it is not billable');
});

T('a simulated unlocked feed carries BOTH warnings', () => {
  const b = S.bannerFor({ locked: false, simulated: true });
  includes(b.text, 'SIMULATED FEED', 'simulated');
  includes(b.text, 'PREVIEW', 'preview');
  includes(b.text, 'nothing billable', 'not billable');
});

T('a missing context is treated as unlocked, not as locked', () => {
  ok(S.bannerFor() !== null, 'no context must fail SAFE, to the banner');
  ok(S.bannerFor({}) !== null, 'empty context must fail SAFE');
  ok(S.bannerFor({ locked: 'yes' }) !== null, 'a truthy non-true must not count as a lock');
});

// ============================================================ 6. ABSTENTIONS
G('scoutStatus — every I-DO-NOT-KNOW stays reachable');

T('every abstention is reachable and every one has an explanation', () => {
  eq(S.scoutStatus({ opencv: false }).why, S.S_NO_OPENCV, 'no opencv');
  eq(S.scoutStatus({ opencv: true, framesSeen: 0 }).why, S.S_NO_CAMERA, 'no camera');
  eq(S.scoutStatus({ opencv: true, framesSeen: 3, boxCount: 0 }).why, S.S_NOTHING_FOUND, 'nothing found');
  eq(S.scoutStatus({ opencv: true, framesSeen: 3, boxCount: 2, locked: false }).why,
    S.S_SEEN_UNMEASURED, 'seen but unmeasurable');
  eq(S.scoutStatus({ opencv: true, framesSeen: 3, boxCount: 0, reason: 'scout_cv_threw: boom' }).why,
    'scout_cv_threw: boom', 'a cv throw');
  const reached = new Set([S.S_NO_OPENCV, S.S_NO_CAMERA, S.S_NOTHING_FOUND,
    S.S_SEEN_UNMEASURED, S.S_CV_THREW]);
  eq(reached.size, Object.keys(S.ABSTENTIONS).length, 'an abstention exists with no path to it');
  for (const k of reached) ok((S.ABSTENTIONS[k] || '').length > 40, `${k} has no explanation`);
});

T('*** a crash is reported as a crash, not as "nothing found" ***', () => {
  // These are different claims. "Nothing found" says the counter looked and the
  // counter is empty; a throw says it never finished looking.
  const crashed = S.scoutStatus({ opencv: true, framesSeen: 5, boxCount: 0, reason: 'scout_cv_threw: boom' });
  const empty = S.scoutStatus({ opencv: true, framesSeen: 5, boxCount: 0, reason: S.S_NOTHING_FOUND });
  ok(crashed.why !== empty.why, 'a crash and an empty frame report the same thing');
  includes(crashed.why, 'boom', 'the thrown message was thrown away');
  includes(S.explainReason(crashed.why), 'never finished looking', 'the distinction is not explained');
  eq(S.explainReason(S.S_NOTHING_FOUND), S.ABSTENTIONS[S.S_NOTHING_FOUND], 'exact-match lookup');
  eq(S.explainReason('something_unmapped'), 'something_unmapped', 'unknown reason passes through');
  eq(S.explainReason(null), '', 'null reason');

  // and it survives the whole panel, into the DOM
  const { doc, raw } = makePage();
  raw.__contours = [rectContour(640, 480, 300, 180, 0, 32)];
  const cv = makeFakeCv();
  cv.findContours = () => { throw new Error('kaboom'); };
  const p = S.createPanel({ doc, cv, everyMs: 0 });
  p.tick(0);
  const block = doc.getElementById(S.SCOUT_ROOT_ID);
  includes(block.dataset.why, 'scout_cv_threw', 'the panel reported a crash as something else');
  includes(block.text, 'kaboom', 'the thrown message never reached the screen');
  includes(block.text, 'never finished looking', 'the crash was not explained to the operator');
  eq(cv.book.live.size, 0, 'LEAK on the panel throw path');
});

T('OK requires a lock — boxes without a mat are NOT ok', () => {
  eq(S.scoutStatus({ opencv: true, framesSeen: 9, boxCount: 4, locked: true }).status, 'OK', 'locked+boxes');
  eq(S.scoutStatus({ opencv: true, framesSeen: 9, boxCount: 4, locked: false }).status, 'ABSTAIN',
    'boxes without a mat must not be OK');
});

T('SCOUT can never declare GREEN', () => {
  const rnd = mulberry32(5);
  for (let i = 0; i < 500; i++) {
    const v = {
      opencv: rnd() < 0.5, framesSeen: Math.floor(rnd() * 5),
      boxCount: Math.floor(rnd() * 5), locked: rnd() < 0.5,
    };
    const st = S.scoutStatus(v);
    ok(st.status === 'OK' || st.status === 'ABSTAIN', `invented status ${st.status}`);
    ok(st.status !== 'GREEN', 'GREEN');
  }
});

// ========================================================== 7. THE OVERLAY
G('drawScout — what actually lands on the canvas');

function recordingCtx() {
  const calls = [];
  const texts = [];
  const rec = (name) => (...args) => { calls.push({ name, args }); };
  return {
    calls, texts,
    _style: {},
    clearRect: rec('clearRect'),
    beginPath: rec('beginPath'),
    moveTo: rec('moveTo'),
    lineTo: rec('lineTo'),
    closePath: rec('closePath'),
    stroke: rec('stroke'),
    fillRect: rec('fillRect'),
    save: rec('save'),
    restore: rec('restore'),
    fillText: (t, x, y) => { calls.push({ name: 'fillText', args: [t, x, y] }); texts.push(t); },
    // A monospace metric that HONOURS the current font, so the wrap and shrink
    // logic is exercised for real rather than against a constant.
    _fontPx: 15,
    measureText(t) { return { width: String(t).length * this._fontPx * 0.6 }; },
    set strokeStyle(v) { calls.push({ name: 'strokeStyle', args: [v] }); },
    set fillStyle(v) { calls.push({ name: 'fillStyle', args: [v] }); },
    set font(v) {
      calls.push({ name: 'font', args: [v] });
      const m = /(\d+(?:\.\d+)?)px/.exec(String(v));
      if (m) this._fontPx = Number(m[1]);
    },
    set lineWidth(v) { calls.push({ name: 'lineWidth', args: [v] }); },
    set textBaseline(v) { calls.push({ name: 'textBaseline', args: [v] }); },
    styles() { return this.calls.filter((c) => c.name === 'strokeStyle' || c.name === 'fillStyle').map((c) => c.args[0]); },
  };
}

const twoTracks = [
  { id: 1, ...boxAt(90, 120, 60, 30), axis: [1, 0], missing: 0 },
  { id: 2, ...boxAt(230, 160, 40, 40), axis: [1, 0], missing: 0 },
];

T('an unlocked overlay draws a box per track, the preview banner, and no mm', () => {
  const ctx = recordingCtx();
  const out = S.drawScout(ctx, twoTracks, { width: 320, height: 240, locked: false });
  eq(out.drawn, 2, 'two boxes');
  eq(out.labels[0], 'id:1  object  (size unknown)', 'label 1');
  eq(out.labels[1], 'id:2  object  (size unknown)', 'label 2');
  includes(out.banner, 'PREVIEW', 'the banner');
  eq(ctx.calls.filter((c) => c.name === 'clearRect').length, 1, 'the overlay must be cleared once');
  eq(ctx.calls.filter((c) => c.name === 'stroke').length, 2, 'two strokes');
  // 4 corners: 1 moveTo + 3 lineTo per box
  eq(ctx.calls.filter((c) => c.name === 'moveTo').length, 2, 'two polygons started');
  eq(ctx.calls.filter((c) => c.name === 'lineTo').length, 6, 'each box must be a 4-corner polygon');
  // the banner may wrap, so assert the drawn LINES rejoin to the whole warning
  eq(ctx.texts.slice(-out.bannerLines.length).join(' '), out.banner,
    'the banner was not actually drawn in full');
  for (const t of ctx.texts) excludes(t, ' mm', 'millimetres appeared with no mat lock');
});

T('a locked overlay draws millimetres and no banner', () => {
  const ctx = recordingCtx();
  const out = S.drawScout(ctx, twoTracks, { width: S.BUF_W, height: S.BUF_H, locked: true });
  eq(out.banner, null, 'locked and real needs no banner');
  ok(S.LABEL_LOCKED_RE.test(out.labels[0]), `locked label: ${out.labels[0]}`);
  ok(ctx.texts.every((t) => / mm$/.test(t)), `a non-measurement was drawn: ${JSON.stringify(ctx.texts)}`);
});

T('the overlay never paints the settled green', () => {
  for (const locked of [false, true]) {
    for (const simulated of [false, true]) {
      const ctx = recordingCtx();
      S.drawScout(ctx, twoTracks, { width: 320, height: 240, locked, simulated });
      for (const s of ctx.styles()) {
        excludes(String(s).toLowerCase(), '3ddc84', 'the settled green reached the overlay');
        excludes(String(s).toLowerCase(), 'green', 'a green was named');
      }
    }
  }
  for (const v of Object.values(S.COLOURS)) {
    excludes(String(v).toLowerCase(), '3ddc84', 'COLOURS carries the settled green');
  }
});

T('a held track is drawn faintly, so "was here" is not "is here"', () => {
  const ctx = recordingCtx();
  S.drawScout(ctx, [{ ...twoTracks[0], missing: 2 }], { width: 320, height: 240, locked: false });
  ok(ctx.styles().includes(S.COLOURS.faint), 'a held box was drawn as solidly as a live one');
});

T('labels are clamped inside the frame', () => {
  const top = { id: 5, ...boxAt(60, 8, 40, 12), missing: 0 };
  const a = S.labelAnchor(top, 320, 240);
  ok(a.y >= 0, `label escaped the top of the frame: y=${a.y}`);
  ok(a.x >= 0, `label escaped the left of the frame: x=${a.x}`);
  ok(a.y + a.h <= 240 + 1, `label escaped the bottom: y=${a.y} h=${a.h}`);
});

T('a missing context is reported, not thrown on', () => {
  const out = S.drawScout(null, twoTracks, { width: 320, height: 240 });
  eq(out.cleared, false, 'claimed to have drawn on nothing');
  eq(out.drawn, 0, 'claimed boxes');
});

T('an empty track list still draws the banner — an empty preview is still a preview', () => {
  const ctx = recordingCtx();
  const out = S.drawScout(ctx, [], { width: 320, height: 240, locked: false });
  eq(out.drawn, 0, 'no boxes');
  includes(out.banner, 'PREVIEW', 'the banner must survive an empty frame');
  eq(ctx.texts.join(' '), out.banner, 'the banner was not drawn in full on an empty frame');
});

G('the overlay is legible at the size the operator actually sees');

T('drawScaleFor grows the overlay with the camera and clamps', () => {
  eq(S.drawScaleFor(640), 1, 'the reference width');
  eq(S.drawScaleFor(320), 1, 'never smaller than the reference');
  near(S.drawScaleFor(1280), 2, 1e-9, '1280 is 2x');
  eq(S.drawScaleFor(8000), 3.2, 'clamped at the top');
  eq(S.drawScaleFor(0), 1, 'zero');
  eq(S.drawScaleFor(NaN), 1, 'NaN');
});

T('a known CSS width beats the guess — type is sized for the screen, not the buffer', () => {
  eq(S.drawScaleFor(1280, 320), 4, 'four backing pixels per CSS pixel');
  eq(S.drawScaleFor(840, 340), 840 / 340, 'the squeezed rectified pane');
  eq(S.drawScaleFor(640, 640), 1, 'one to one');
  eq(S.drawScaleFor(1280, 0), 2, 'a zero CSS width falls back to the guess');
  eq(S.drawScaleFor(1280, NaN), 2, 'a NaN CSS width falls back to the guess');
  eq(S.drawScaleFor(100, 4000), 0.6, 'clamped at the bottom');
  // the squeezed pane is exactly the case the guess got wrong
  ok(S.drawScaleFor(840, 340) > S.drawScaleFor(840),
    'the CSS-aware scale must be larger for a pane narrower than its buffer');
});

T('containedWidth is the letterboxed picture, not the element box', () => {
  // the raw pane: a 4:3 buffer in a 4:3 box fills it
  eq(S.containedWidth(1280, 960, 640, 480), 640, 'no letterboxing');
  // the rectified pane: an 840x1188 portrait buffer in a 640x480 landscape box
  const w = S.containedWidth(840, 1188, 640, 480);
  near(w, 480 * (840 / 1188), 1e-9, 'portrait in a landscape box');
  ok(w < 640 * 0.6, `the rectified pane is letterboxed to ${w.toFixed(0)}px, not 640`);
  // and a landscape buffer in a tall box is limited by width
  eq(S.containedWidth(1280, 960, 300, 900), 300, 'limited by width');
  eq(S.containedWidth(840, 1188, 0, 0), null, 'no layout yet: say so');
  eq(S.containedWidth(NaN, 1188, 640, 480), 640, 'fall back to the element box');
  measured.rect_pane_content_px = `${w.toFixed(0)} of 640`;
});

T('a label at the right edge is pulled back in, whole', () => {
  const ctx = recordingCtx();
  // a box hard against the right edge: the label is far wider than what is left
  const t = { id: 3, ...boxAt(300, 120, 30, 30), missing: 0 };
  S.drawScout(ctx, [t], { width: 320, height: 240, locked: false });
  const bg = ctx.calls.filter((c) => c.name === 'fillRect')
    .find((c) => c.args[1] !== 0);           // not the banner strip
  const [x, , w] = bg.args;
  ok(x >= 0, `the label backing starts off-canvas at x=${x}`);
  ok(x + w <= 320 + 0.5, `the label runs ${(x + w - 320).toFixed(0)}px off the right edge`);
  const text = ctx.calls.find((c) => c.name === 'fillText' && c.args[0].startsWith('id:'));
  ok(text.args[1] >= 0, 'the label text starts off-canvas');
  includes(text.args[0], '(size unknown)', 'the qualifying half of the label must survive');
});

T('a 1280px camera gets bigger type than a 640px one', () => {
  const fontOf = (w) => {
    const ctx = recordingCtx();
    S.drawScout(ctx, [{ id: 1, ...boxAt(160, 120, 60, 30), missing: 0 }],
      { width: w, height: Math.round(w * 0.75), locked: false });
    const f = ctx.calls.find((c) => c.name === 'font' && !String(c.args[0]).startsWith('600'));
    return Number(/(\d+)px/.exec(f.args[0])[1]);
  };
  const small = fontOf(640), big = fontOf(1280);
  measured.label_font_640 = `${small}px`;
  measured.label_font_1280 = `${big}px`;
  ok(big > small * 1.8, `1280px label font ${big} is not ~2x the 640px font ${small}`);
  // scaleUi:false is the escape hatch, and it must actually pin the size
  const ctx = recordingCtx();
  S.drawScout(ctx, [{ id: 1, ...boxAt(160, 120, 60, 30), missing: 0 }],
    { width: 1280, height: 960, locked: false, scaleUi: false });
  const f = ctx.calls.find((c) => c.name === 'font' && !String(c.args[0]).startsWith('600'));
  eq(Number(/(\d+)px/.exec(f.args[0])[1]), S.DRAW_DEFAULTS.labelFontPx, 'scaleUi:false did not pin');
});

T('wrapToWidth keeps every word — a wrapped warning, never a truncated one', () => {
  const measure = (s) => s.length * 6;
  const text = 'the quick brown fox jumps over the lazy dog';
  const lines = S.wrapToWidth(measure, text, 60);   // 10 chars per line
  eq(lines.join(' '), text, 'a word was lost in the wrap');
  ok(lines.length > 1, 'it did not wrap at all');
  for (const l of lines) ok(measure(l) <= 60 || l.split(' ').length === 1, `line too wide: ${l}`);
  eq(S.wrapToWidth(measure, 'supercalifragilistic', 12).join(' '), 'supercalifragilistic',
    'a word longer than the line was dropped instead of overflowing');
  eq(S.wrapToWidth(measure, '', 60).join(''), '', 'empty text');
  eq(S.wrapToWidth(null, 'a b c', 60).join(' '), 'a b c', 'no measurer: one line, nothing lost');
});

T('*** the whole banner reaches the screen at every canvas width ***', () => {
  const seen = [];
  for (const w of [240, 320, 480, 640, 900, 1280, 1920]) {
    const ctx = recordingCtx();
    const out = S.drawScout(ctx, [], { width: w, height: Math.round(w * 0.75), locked: false, simulated: true });
    // 1. nothing was dropped
    eq(out.bannerDrawn, out.banner, `the banner was TRUNCATED at width ${w}`);
    // 2. it is still readable
    ok(out.bannerFontPx >= 9, `banner font collapsed to ${out.bannerFontPx}px at width ${w}`);
    // 3. every line actually fits the canvas
    const px = out.bannerFontPx;
    for (const line of out.bannerLines) {
      const lw = line.length * px * 0.6;
      ok(lw <= w, `a banner line overflows a ${w}px canvas: ${lw.toFixed(0)}px`);
    }
    // 4. and the strip does not eat the frame
    const strip = ctx.calls.find((c) => c.name === 'fillRect' && c.args[1] === 0 && c.args[2] === w);
    ok(strip.args[3] <= Math.round(w * 0.75) * 0.42,
      `the banner ate ${strip.args[3]}px of a ${Math.round(w * 0.75)}px canvas`);
    seen.push(`${w}:${out.bannerLines.length}L@${px}px`);
  }
  measured.banner_layout = seen.join(' ');
});

T('a box at the top of the frame keeps its label out from under the banner', () => {
  const ctx = recordingCtx();
  const top = { id: 5, ...boxAt(160, 14, 60, 20), missing: 0 };
  S.drawScout(ctx, [top], { width: 320, height: 240, locked: false });
  const labelY = ctx.calls.find((c) => c.name === 'fillText' && c.args[0].startsWith('id:')).args[2];
  const bannerY = ctx.calls.filter((c) => c.name === 'fillRect').find((c) => c.args[1] === 0 && c.args[2] === 320);
  ok(bannerY !== undefined, 'the banner strip was not drawn');
  const bannerH = bannerY.args[3];
  ok(labelY >= bannerH, `the label at y=${labelY} is hidden under a ${bannerH}px banner`);
});

T('the banner is drawn last, so nothing paints over the warning', () => {
  const ctx = recordingCtx();
  const out = S.drawScout(ctx, [{ id: 1, ...boxAt(160, 120, 60, 30), missing: 0 }],
    { width: 320, height: 240, locked: false });
  const texts = ctx.calls.filter((c) => c.name === 'fillText').map((c) => c.args[0]);
  eq(texts.slice(-out.bannerLines.length).join(' '), out.banner,
    'the banner was not the last thing drawn');
  ok(texts.slice(0, -out.bannerLines.length).every((t) => t.startsWith('id:')),
    'something other than a label was drawn before the banner');
});

// ==================================================== 8. THE OPENCV BRIDGE
G('contoursFrom — a fake cv that counts every Mat');

/**
 * A fake OpenCV. It implements only the calls scout.js makes, computes the
 * shape statistics with scout's own pure functions, and — the point — refuses
 * to let a Mat be deleted twice and reports any Mat still alive at the end.
 */
function makeFakeCv() {
  const book = { made: 0, deleted: 0, live: new Set() };
  class FMat {
    constructor() { this.deleted = false; this.points = null; this.src = null; this.k = 1; this.data32S = new Int32Array(0); book.made++; book.live.add(this); }
    delete() {
      if (this.deleted) throw new Error('DOUBLE DELETE of a Mat');
      this.deleted = true; book.live.delete(this); book.deleted++;
    }
  }
  class FMatVector {
    constructor() { this.items = []; this.deleted = false; book.made++; book.live.add(this); }
    size() { return this.items.length; }
    get(i) { const m = new FMat(); m.points = this.items[i]; return m; }
    delete() { if (this.deleted) throw new Error('DOUBLE DELETE of a MatVector'); this.deleted = true; book.live.delete(this); book.deleted++; }
  }
  const passthrough = (src, dst) => { dst.src = src.src; dst.k = src.k; };
  const cv = {
    book,
    Mat: FMat, MatVector: FMatVector,
    Size: function Size(w, h) { this.width = w; this.height = h; },
    INTER_AREA: 3, COLOR_RGBA2GRAY: 11, BORDER_DEFAULT: 4,
    MORPH_RECT: 0, MORPH_CLOSE: 3, RETR_EXTERNAL: 0, CHAIN_APPROX_SIMPLE: 2,
    ADAPTIVE_THRESH_GAUSSIAN_C: 1, THRESH_BINARY_INV: 1,
    imread(source) { const m = new FMat(); m.src = source; m.k = 1; return m; },
    resize(src, dst, size) { dst.src = src.src; dst.k = size.width / src.src.width; },
    cvtColor: passthrough,
    GaussianBlur: passthrough,
    getStructuringElement() { return new FMat(); },
    Canny(src, dst) { dst.src = src.src; dst.k = src.k; dst.mode = 'canny'; },
    adaptiveThreshold(src, dst) { dst.src = src.src; dst.k = src.k; dst.mode = 'adaptive'; },
    morphologyEx: (src, dst) => { dst.src = src.src; dst.k = src.k; dst.mode = src.mode; },
    findContours(img, out) {
      const s = img.src || {};
      const list = (img.mode === 'adaptive' ? (s.__adaptiveContours ?? s.__contours) : s.__contours) || [];
      out.items = list.map((c) => c.map(([x, y]) => [x * img.k, y * img.k]));
    },
    contourArea(m) { return S.polygonArea(m.points || []); },
    convexHull(m, hull) { hull.points = S.convexHull(m.points || []); },
    arcLength(m) { return S.polygonPerimeter(m.points || []); },
    approxPolyDP(m, out) {
      out.points = m.points || [];
      const d = new Int32Array(out.points.length * 2);
      out.points.forEach(([x, y], i) => { d[i * 2] = Math.round(x); d[i * 2 + 1] = Math.round(y); });
      out.data32S = d;
    },
  };
  return cv;
}

function fakeSource(w, h, contours, extra = {}) {
  return { width: w, height: h, __contours: contours, ...extra };
}

T('a rectangle in the source comes back as a contour, scaled to detection px', () => {
  const cv = makeFakeCv();
  const src = fakeSource(1280, 960, [rectContour(640, 480, 200, 100, 0, 24)]);
  const r = S.contoursFrom(cv, src, { targetWidth: 320 });
  eq(r.reason, null, `bridge refused: ${r.reason}`);
  eq(r.mode, 'canny', 'mode');
  eq(r.width, 320, 'detect width');
  eq(r.height, 240, 'detect height');
  near(r.scale, 4, 1e-9, 'scale back to source');
  eq(r.contours.length, 1, 'one contour');
  const rect = S.minAreaRect(r.contours[0].points);
  near(rect.longPx, 50, 1.5, '200 source px at 1/4 scale');
  near(rect.shortPx, 25, 1.5, '100 source px at 1/4 scale');
});

T('*** every Mat handed out is deleted — no leak on the happy path ***', () => {
  const cv = makeFakeCv();
  S.contoursFrom(cv, fakeSource(1280, 960, [rectContour(640, 480, 200, 100, 0, 24)]));
  measured.mats_made_happy = String(cv.book.made);
  eq(cv.book.live.size, 0, `LEAK: ${cv.book.live.size} of ${cv.book.made} Mats still alive`);
  eq(cv.book.made, cv.book.deleted, 'made != deleted');
  ok(cv.book.made > 5, 'the fake cv was never exercised');
});

T('no leak when Canny finds nothing and adaptiveThreshold takes over', () => {
  const cv = makeFakeCv();
  const src = fakeSource(640, 480, [], { __adaptiveContours: [rectContour(320, 240, 120, 80, 15, 24)] });
  const r = S.contoursFrom(cv, src);
  eq(r.mode, 'adaptive', 'the fallback did not run');
  eq(r.contours.length, 1, 'the fallback found nothing');
  eq(cv.book.live.size, 0, `LEAK on the fallback path: ${cv.book.live.size} alive`);
  measured.mats_made_fallback = String(cv.book.made);
});

T('no leak when a cv call throws mid-pipeline', () => {
  const cv = makeFakeCv();
  cv.findContours = () => { throw new Error('boom'); };
  const r = S.contoursFrom(cv, fakeSource(640, 480, [rectContour(320, 240, 100, 60, 0)]));
  includes(r.reason, 'scout_cv_threw', 'the throw was swallowed silently');
  includes(r.reason, 'boom', 'the message was lost');
  eq(r.contours.length, 0, 'contours after a throw');
  eq(cv.book.live.size, 0, `LEAK on the throw path: ${cv.book.live.size} alive`);
});

T('mode canny and mode adaptive can each be forced', () => {
  const src = () => fakeSource(640, 480, [rectContour(320, 240, 100, 60, 0, 20)],
    { __adaptiveContours: [rectContour(100, 100, 80, 80, 0, 20)] });
  eq(S.contoursFrom(makeFakeCv(), src(), { mode: S.MODE_CANNY }).mode, 'canny', 'forced canny');
  eq(S.contoursFrom(makeFakeCv(), src(), { mode: S.MODE_ADAPTIVE }).mode, 'adaptive', 'forced adaptive');
});

T('*** INVARIANT 4: nothing image-shaped is reachable from the result ***', () => {
  const cv = makeFakeCv();
  const src = fakeSource(1280, 960, [rectContour(640, 480, 200, 100, 12, 24), starContour(200, 200, 60, 50, 5)]);
  const r = S.contoursFrom(cv, src);
  const seen = new Set();
  const walk = (v, path) => {
    if (v === null || v === undefined) return;
    if (typeof v === 'number' || typeof v === 'string' || typeof v === 'boolean') return;
    if (seen.has(v)) return;
    seen.add(v);
    ok(!ArrayBuffer.isView(v), `a typed array survived at ${path}`);
    ok(!(v instanceof ArrayBuffer), `an ArrayBuffer survived at ${path}`);
    ok(typeof v !== 'function', `a function survived at ${path}`);
    for (const k of Object.keys(v)) {
      ok(!/canvas|image|pixel|bitmap|data32|dataurl|blob|video|raw/i.test(k),
        `an image-shaped key survived: ${path}.${k}`);
      walk(v[k], `${path}.${k}`);
    }
  };
  walk(r, 'result');
  ok(r.contours.length >= 1, 'the walk had nothing to walk');
  measured.invariant4_nodes_walked = String(seen.size);
  // and the source object itself must not be referenced back
  ok(!JSON.stringify(r).includes('__contours'), 'the source leaked into the result');
});

T('the bridge abstains rather than guesses when there is no cv or no frame', () => {
  eq(S.contoursFrom(null, fakeSource(320, 240, [])).reason, S.S_NO_OPENCV, 'no cv');
  eq(S.contoursFrom({}, fakeSource(320, 240, [])).reason, S.S_NO_OPENCV, 'a cv with no imread');
  eq(S.contoursFrom(makeFakeCv(), null).reason, S.S_NO_CAMERA, 'no source');
  eq(S.contoursFrom(makeFakeCv(), { width: 0, height: 0 }).reason, S.S_NO_CAMERA, 'a zero-size source');
  for (const bad of [null, {}, undefined]) {
    const r = S.contoursFrom(bad, fakeSource(320, 240, []));
    eq(r.contours.length, 0, 'invented contours with no cv');
  }
});

T('contours below the pixel floor never reach the pure filter', () => {
  const cv = makeFakeCv();
  const r = S.contoursFrom(cv, fakeSource(320, 240, [rectContour(100, 100, 3, 3, 0)]), { targetWidth: 320 });
  eq(r.contours.length, 0, 'a 9-pixel speck was passed downstream');
});

// ======================================================= 9. THE FULL SCAN
G('scanSource — pixels in, tracked boxes out');

T('a moving object is found and tracked across ten synthetic frames', () => {
  const cv = makeFakeCv();
  let st = S.newTrackState();
  const ids = new Set();
  let labels = [];
  for (let f = 0; f < 10; f++) {
    const src = fakeSource(1280, 960, [rectContour(300 + f * 40, 480, 240, 140, 0, 32)]);
    const r = S.scanSource(cv, src, st, { targetWidth: 320, ts: f });
    st = { tracks: r.tracks, nextId: r.nextId };
    eq(r.tracks.length, 1, `frame ${f}: expected one track, got ${r.tracks.length}`);
    ids.add(r.tracks[0].id);
    labels = r.tracks.map((t) => S.labelFor(t, { locked: false }));
  }
  eq(ids.size, 1, `the id changed while the object moved: ${[...ids].join(',')}`);
  eq(labels[0], 'id:1  object  (size unknown)', 'the label the operator sees');
  eq(cv.book.live.size, 0, 'LEAK across ten frames');
  measured.scan_frames_no_leak = '10';
});

T('a blank frame ages the tracks instead of retiring them instantly', () => {
  const cv = makeFakeCv();
  let r = S.scanSource(cv, fakeSource(640, 480, [rectContour(320, 240, 200, 120, 0, 24)]), S.newTrackState(), {});
  eq(r.tracks.length, 1, 'setup');
  const id = r.tracks[0].id;
  r = S.scanSource(cv, fakeSource(640, 480, []), { tracks: r.tracks, nextId: r.nextId }, {});
  eq(r.reason, S.S_NOTHING_FOUND, 'a blank frame must be an abstention, not silence');
  eq(r.tracks.length, 1, 'the held track vanished');
  eq(r.tracks[0].id, id, 'the id changed while held');
  eq(r.tracks[0].missing, 1, 'not marked missing');
});

T('a cv failure ages the tracks rather than deleting them', () => {
  const cv = makeFakeCv();
  let r = S.scanSource(cv, fakeSource(640, 480, [rectContour(320, 240, 200, 120, 0, 24)]), S.newTrackState(), {});
  const id = r.tracks[0].id;
  r = S.scanSource(null, fakeSource(640, 480, []), { tracks: r.tracks, nextId: r.nextId }, {});
  eq(r.reason, S.S_NO_OPENCV, 'the reason was lost');
  eq(r.tracks.length, 1, 'a cv outage wiped the tracks');
  eq(r.tracks[0].id, id, 'the id was lost to a cv outage');
});

T('the full scan reports the geometry it actually ran at', () => {
  const cv = makeFakeCv();
  const r = S.scanSource(cv, fakeSource(1920, 1080, [rectContour(960, 540, 400, 200, 0, 24)]),
    S.newTrackState(), { targetWidth: 480 });
  eq(r.detectW, 480, 'detect width');
  eq(r.detectH, 270, 'detect height');
  eq(r.mode, 'canny', 'mode');
  // and the box comes back in SOURCE pixels, not detection pixels
  near(r.tracks[0].longPx, 400, 12, 'the box was not scaled back to source px');
});

// ================================================== 10. THE PANEL AND THE DOM
G('the panel — mounting, the overlay, the seam');

function makeDom() {
  const byId = new Map();
  class El {
    constructor(tag) {
      this.tagName = String(tag).toUpperCase();
      this.childNodes = [];
      this.style = {};
      this.dataset = {};
      this.attrs = {};
      this.parentNode = null;
      this._id = '';
      this.className = '';
      this.textContent = '';
      this.width = 0; this.height = 0;
      this._ctx = null;
    }
    get id() { return this._id; }
    set id(v) { this._id = String(v); byId.set(this._id, this); }
    setAttribute(k, v) { this.attrs[k] = String(v); if (k === 'id') this.id = v; }
    getAttribute(k) { return this.attrs[k] ?? null; }
    appendChild(c) { c.parentNode = this; this.childNodes.push(c); return c; }
    insertBefore(c, ref) {
      const i = this.childNodes.indexOf(ref);
      c.parentNode = this;
      if (i < 0) this.childNodes.push(c); else this.childNodes.splice(i, 0, c);
      return c;
    }
    replaceChild(fresh, old) {
      const i = this.childNodes.indexOf(old);
      if (i < 0) return this.appendChild(fresh);
      fresh.parentNode = this; this.childNodes[i] = fresh; old.parentNode = null;
      return fresh;
    }
    get nextSibling() {
      if (!this.parentNode) return null;
      const i = this.parentNode.childNodes.indexOf(this);
      return this.parentNode.childNodes[i + 1] ?? null;
    }
    getContext() { if (!this._ctx) this._ctx = recordingCtx(); return this._ctx; }
    /** flatten to text, for asserting the block's copy */
    get text() {
      return (this.textContent || '') + this.childNodes.map((c) => c.text).join(' ');
    }
    find(pred) {
      if (pred(this)) return this;
      for (const c of this.childNodes) { const r = c.find(pred); if (r) return r; }
      return null;
    }
    all(pred, out = []) {
      if (pred(this)) out.push(this);
      for (const c of this.childNodes) c.all(pred, out);
      return out;
    }
  }
  const doc = {
    createElement: (t) => new El(t),
    getElementById: (id) => byId.get(id) ?? null,
    readyState: 'complete',
    addEventListener() {},
    byId,
    El,
  };
  return doc;
}

/** A page with the two panes app.js owns, and nothing else. */
function makePage() {
  const doc = makeDom();
  const body = doc.createElement('div'); body.id = 'body-core';
  const stack = doc.createElement('div');
  const raw = doc.createElement('canvas'); raw.id = 'raw'; raw.width = 1280; raw.height = 960;
  const shade = doc.createElement('div'); shade.className = 'pane-shade';
  stack.appendChild(raw); stack.appendChild(shade);
  body.appendChild(stack);
  return { doc, body, raw, shade, stack };
}

T('ensureOverlay stacks one canvas over the target, exactly once', () => {
  const { doc, raw, stack } = makePage();
  const a = S.ensureOverlay(doc, raw, S.SCOUT_OVERLAY_ID);
  const b = S.ensureOverlay(doc, raw, S.SCOUT_OVERLAY_ID);
  ok(a === b, 'a second overlay was created');
  eq(stack.childNodes.length, 3, 'the overlay was not inserted into the pane stack');
  eq(a.width, 1280, 'overlay width must match the target backing store');
  eq(a.height, 960, 'overlay height must match');
  eq(a.style.position, 'absolute', 'the overlay must be stacked, not stretch the layout');
  eq(a.style.pointerEvents, 'none', 'the overlay must not eat the revert tap');
  eq(a.getAttribute('aria-hidden'), 'true', 'decorative canvas must be hidden from AT');
});

T('the overlay sits BEFORE the no-camera shade, so the shade still covers it', () => {
  const { doc, raw, stack, shade } = makePage();
  const ov = S.ensureOverlay(doc, raw, S.SCOUT_OVERLAY_ID);
  eq(stack.childNodes.indexOf(ov), 1, 'the overlay is in the wrong stacking position');
  eq(stack.childNodes.indexOf(shade), 2, 'the shade must stay on top');
});

T('the overlay resizes when the camera resolution changes', () => {
  const { doc, raw } = makePage();
  const ov = S.ensureOverlay(doc, raw, S.SCOUT_OVERLAY_ID);
  raw.width = 640; raw.height = 480;
  S.ensureOverlay(doc, raw, S.SCOUT_OVERLAY_ID);
  eq(ov.width, 640, 'overlay did not follow the camera');
  eq(ov.height, 480, 'overlay did not follow the camera');
});

T('a tick with a live raw canvas draws boxes and mounts the block', () => {
  const { doc, body, raw } = makePage();
  const cv = makeFakeCv();
  raw.__contours = [rectContour(640, 480, 300, 180, 0, 32), rectContour(200, 200, 220, 220, 25, 32)];
  const p = S.createPanel({ doc, cv, everyMs: 0, now: () => 0 });
  const out = p.tick(1000);
  ok(out !== null, 'the tick refused to run');
  eq(out.drawn, 2, `expected two boxes, got ${out.drawn}`);
  ok(S.LABEL_UNLOCKED_RE.test(out.labels[0]), `label: ${out.labels[0]}`);
  includes(out.banner, 'PREVIEW', 'no banner on an unlocked preview');
  const block = doc.getElementById(S.SCOUT_ROOT_ID);
  ok(block !== null, 'the SCOUT block never mounted');
  eq(block.parentNode, body, 'the block mounted somewhere unexpected');
  eq(block.dataset.status, 'ABSTAIN', 'boxes without a mat must not read OK');
  eq(block.dataset.why, S.S_SEEN_UNMEASURED, 'wrong abstention');
  includes(block.text, 'PREVIEW - boxes only', 'the block must repeat the banner');
  includes(block.text, 'object  (size unknown)', 'the block must list the honest labels');
  excludes(block.text, ' mm', 'the block printed millimetres with no mat');
  eq(cv.book.live.size, 0, 'LEAK from the panel tick');
});

T('repeated ticks replace the block instead of stacking copies', () => {
  const { doc, body, raw } = makePage();
  raw.__contours = [rectContour(640, 480, 300, 180, 0, 32)];
  const p = S.createPanel({ doc, cv: makeFakeCv(), everyMs: 0 });
  for (let i = 0; i < 5; i++) p.tick(i * 100);
  eq(body.all((e) => e.id === S.SCOUT_ROOT_ID).length, 1, 'the block was appended five times');
  eq(p.rawTracks.length, 1, 'the track count drifted');
  eq(p.rawTracks[0].id, 1, 'the id drifted across ticks');
});

T('the block is rebuilt when what it SAYS changes, not on every scan', () => {
  const { doc, body, raw } = makePage();
  raw.__contours = [rectContour(640, 480, 300, 180, 0, 32)];
  let t = 0;
  const p = S.createPanel({ doc, cv: makeFakeCv(), everyMs: 0, now: () => t });
  p.tick(0);
  const first = doc.getElementById(S.SCOUT_ROOT_ID);
  for (let i = 1; i <= 6; i++) { t = i; p.tick(i); }
  ok(doc.getElementById(S.SCOUT_ROOT_ID) === first,
    'the block was rebuilt six times to say the same thing');
  // a second object appears: the block must repaint at once, not in half a second
  raw.__contours.push(rectContour(200, 200, 220, 220, 0, 32));
  t = 7; p.tick(7);
  ok(doc.getElementById(S.SCOUT_ROOT_ID) !== first, 'the block did not follow a change');
  eq(body.all((e) => e.id === S.SCOUT_ROOT_ID).length, 1, 'a stale block was left behind');
  // and it still ticks over on its own after half a second, so counters move
  const second = doc.getElementById(S.SCOUT_ROOT_ID);
  t = 8; p.tick(8);
  ok(doc.getElementById(S.SCOUT_ROOT_ID) === second, 'repainted with nothing to say');
  t = 700; p.tick(700);
  ok(doc.getElementById(S.SCOUT_ROOT_ID) !== second, 'the block froze instead of ticking over');
});

T('attach registers once, however the load order goes', () => {
  const { doc } = makePage();
  const calls = [];
  const register = (id) => { calls.push(id); return { ok: id === 'core', id, reason: 'r' }; };
  const panel = S.createPanel({ doc, cv: makeFakeCv() });
  S.attach(register, { panel });
  S.attach(register, { panel });
  eq(calls.join(','), 'scout,core,scout,core', 'attach must be callable twice without throwing');
  eq(panel.model.registeredAs, 'core', 'the second attach lost the id');
  eq(doc.byId.get(S.SCOUT_ROOT_ID).parentNode.all((e) => e.id === S.SCOUT_ROOT_ID).length, 1,
    'two blocks were mounted');
});

T('everyMs throttles the scan', () => {
  const { doc, raw } = makePage();
  raw.__contours = [rectContour(640, 480, 300, 180, 0, 32)];
  const p = S.createPanel({ doc, cv: makeFakeCv(), everyMs: 100 });
  ok(p.tick(0) !== null, 'first tick');
  eq(p.tick(50), null, 'a tick inside the interval must be skipped');
  ok(p.tick(120) !== null, 'a tick past the interval must run');
});

T('onFrame takes the rectified crop and produces MILLIMETRES', () => {
  const { doc } = makePage();
  const crop = doc.createElement('canvas');
  crop.id = 'rect'; crop.width = S.BUF_W; crop.height = S.BUF_H;
  const holder = doc.createElement('div'); holder.appendChild(crop);
  // a 60mm x 30mm object, drawn in rectified buffer pixels
  crop.__contours = [rectContour(400, 600, 60 * S.PX_PER_MM_X, 30 * S.PX_PER_MM_Y, 0, 32)];
  const p = S.createPanel({ doc, cv: makeFakeCv(), everyMs: 0 });
  const okRet = p.onFrame({ cropKind: 'rectified_mat_crop', crop, width: S.BUF_W, height: S.BUF_H });
  eq(okRet, true, 'onFrame refused the rectified crop');
  eq(p.model.labels.length, 1, `expected one label, got ${JSON.stringify(p.model.labels)}`);
  ok(S.LABEL_LOCKED_RE.test(p.model.labels[0]), `not a measurement: ${p.model.labels[0]}`);
  const m = /(\d+\.\d) x (\d+\.\d) mm/.exec(p.model.labels[0]);
  near(Number(m[1]), 60, 2.5, `long side mm (label: ${p.model.labels[0]})`);
  near(Number(m[2]), 30, 2.5, `short side mm (label: ${p.model.labels[0]})`);
  const block = doc.getElementById(S.SCOUT_ROOT_ID);
  eq(block.dataset.status, 'OK', 'a measured box with a lock should be OK');
  eq(block.dataset.locked, 'true', 'locked flag');
});

T('the locked path scans at higher resolution, and says what that buys', () => {
  const { doc } = makePage();
  const crop = doc.createElement('canvas');
  crop.width = S.BUF_W; crop.height = S.BUF_H;
  crop.__contours = [rectContour(400, 600, 200, 120, 0, 24)];
  doc.createElement('div').appendChild(crop);
  const p = S.createPanel({ doc, cv: makeFakeCv(), everyMs: 0 });
  p.onFrame({ cropKind: 'rectified_mat_crop', crop });
  eq(p.model.detectW, 640, 'the locked path must not scan at preview resolution');
  near(p.model.quantMm, (S.BUF_W / 640) / S.PX_PER_MM_X, 1e-9, 'quantisation mm');
  ok(p.model.quantMm < 0.5, `a 0.1 mm label needs better than ${p.model.quantMm.toFixed(2)} mm/px`);
  measured.locked_quant_mm = p.model.quantMm.toFixed(3);
  const block = doc.getElementById(S.SCOUT_ROOT_ID);
  includes(block.text, 'not as accuracy', 'the block must not let the decimal imply accuracy');
  includes(block.text, p.model.quantMm.toFixed(2), 'the block must print the actual quantisation');
});

T('the preview keeps scanning small — it prints no number, so it buys no pixels', () => {
  const { doc, raw } = makePage();
  raw.__contours = [rectContour(640, 480, 300, 180, 0, 32)];
  const p = S.createPanel({ doc, cv: makeFakeCv(), everyMs: 0 });
  p.tick(0);
  eq(p.model.detectW, S.CV_DEFAULTS.targetWidth, 'the preview scan size changed');
  eq(p.model.quantMm, null, 'an unlocked scan must not claim a millimetre precision');
});

T('onFrame refuses anything that is not the rectified crop', () => {
  const { doc, raw } = makePage();
  const p = S.createPanel({ doc, cv: makeFakeCv(), everyMs: 0 });
  eq(p.onFrame(null), false, 'null');
  eq(p.onFrame({}), false, 'empty payload');
  eq(p.onFrame({ cropKind: 'raw_frame', crop: raw }), false, 'A RAW FRAME WAS ACCEPTED');
  eq(p.onFrame({ cropKind: 'rectified_mat_crop' }), false, 'a payload with no crop');
  eq(p.model.labels.length, 0, 'a refused payload produced labels');
});

T('a lock clears the raw overlay — app.js owns that pane once the mat is in', () => {
  const { doc, raw } = makePage();
  raw.__contours = [rectContour(640, 480, 300, 180, 0, 32)];
  const p = S.createPanel({ doc, cv: makeFakeCv(), everyMs: 0 });
  p.tick(0);
  const ov = doc.getElementById(S.SCOUT_OVERLAY_ID);
  const before = ov.getContext().calls.filter((c) => c.name === 'clearRect').length;
  const crop = doc.createElement('canvas');
  crop.width = S.BUF_W; crop.height = S.BUF_H; crop.__contours = [];
  doc.createElement('div').appendChild(crop);
  p.onFrame({ cropKind: 'rectified_mat_crop', crop });
  const after = ov.getContext().calls.filter((c) => c.name === 'clearRect').length;
  ok(after > before, 'the raw overlay was left painted after the mat locked');
  eq(p.tick(1000), null, 'the preview loop kept scanning the raw feed while locked');
});

T('losing the mat retires the millimetre tracks rather than freezing them on screen', () => {
  const { doc } = makePage();
  const crop = doc.createElement('canvas');
  crop.width = S.BUF_W; crop.height = S.BUF_H;
  crop.__contours = [rectContour(400, 600, 200, 120, 0, 24)];
  doc.createElement('div').appendChild(crop);
  const p = S.createPanel({ doc, cv: makeFakeCv(), everyMs: 0 });
  p.onFrame({ cropKind: 'rectified_mat_crop', crop });
  ok(p.model.labels.length > 0, 'setup: a measurement was on screen');
  p.onState({ matLocked: false });
  eq(p.model.labels.length, 0, 'a stale millimetre measurement survived the mat going away');
  eq(p.model.locked, false, 'still claiming a lock');
  const block = doc.getElementById(S.SCOUT_ROOT_ID);
  eq(block.dataset.status, 'ABSTAIN', 'still OK with no mat');
});

T('setDrawOnRaw is the switch the shell flips, and it clears what it turned off', () => {
  const { doc, raw } = makePage();
  raw.__contours = [rectContour(640, 480, 300, 180, 0, 32)];
  const p = S.createPanel({ doc, cv: makeFakeCv(), everyMs: 0 });
  eq(p.drawOnRaw, true, 'draw-on-raw should be on by default');
  ok(p.tick(0) !== null, 'setup');
  const ov = doc.getElementById(S.SCOUT_OVERLAY_ID);
  const before = ov.getContext().calls.filter((c) => c.name === 'clearRect').length;
  eq(p.setDrawOnRaw(false), false, 'the switch did not turn off');
  const after = ov.getContext().calls.filter((c) => c.name === 'clearRect').length;
  ok(after > before, 'turning draw-on-raw off left boxes painted over the feed');
  eq(p.tick(1000), null, 'the loop kept running after being switched off');
  eq(p.setDrawOnRaw(true), true, 'the switch did not turn back on');
  ok(p.tick(2000) !== null, 'the loop did not resume');
});

T('setSimulated makes the SIMULATED label appear on screen', () => {
  const { doc, raw } = makePage();
  raw.__contours = [rectContour(640, 480, 300, 180, 0, 32)];
  const p = S.createPanel({ doc, cv: makeFakeCv(), everyMs: 0, simulated: true });
  const out = p.tick(0);
  includes(out.banner, 'SIMULATED FEED', 'invariant 7: simulated frames must say so');
  const ov = doc.getElementById(S.SCOUT_OVERLAY_ID);
  ok(ov.getContext().texts.some((t) => t.includes('SIMULATED FEED')),
    'SIMULATED was never actually drawn on the canvas');
  includes(doc.getElementById(S.SCOUT_ROOT_ID).text, 'SIMULATED FEED', 'the block must say it too');
});

T('with no cv the panel abstains and draws nothing', () => {
  const { doc, raw } = makePage();
  raw.__contours = [rectContour(640, 480, 300, 180, 0, 32)];
  const p = S.createPanel({ doc, cv: null, everyMs: 0 });
  eq(p.tick(0), null, 'it drew something without OpenCV');
  p.repaint();
  const block = doc.getElementById(S.SCOUT_ROOT_ID);
  eq(block.dataset.why, S.S_NO_OPENCV, 'wrong abstention with no cv');
  includes(block.text, 'I DO NOT KNOW', 'the abstention must be visible');
});

T('with no camera frame the panel abstains', () => {
  const doc = makeDom();
  const body = doc.createElement('div'); body.id = 'body-core';
  const p = S.createPanel({ doc, cv: makeFakeCv(), everyMs: 0 });
  eq(p.tick(0), null, 'it scanned a canvas that does not exist');
  p.repaint();
  eq(doc.getElementById(S.SCOUT_ROOT_ID).dataset.why, S.S_NO_CAMERA, 'wrong abstention');
  eq(body.childNodes.length, 1, 'the block did not mount');
});

T('a blank raw frame abstains with nothing-found rather than drawing nothing silently', () => {
  const { doc, raw } = makePage();
  raw.__contours = [];
  const p = S.createPanel({ doc, cv: makeFakeCv(), everyMs: 0 });
  p.tick(0);
  const block = doc.getElementById(S.SCOUT_ROOT_ID);
  eq(block.dataset.why, S.S_NOTHING_FOUND, 'wrong abstention on a blank frame');
  includes(block.text, 'nothing on screen', 'the empty state must be stated');
});

T('attach takes scout, or falls back to core, and says which', () => {
  const { doc } = makePage();
  const calls = [];
  const registerStrict = (id, hooks) => {
    calls.push(id);
    if (id !== 'core') return { ok: false, reason: `panel_unknown:${id}` };
    return { ok: true, id, replaced: false, reason: 'panel_registered' };
  };
  const r = S.attach(registerStrict, { doc, cv: makeFakeCv() });
  eq(calls.join(','), 'scout,core', 'the id order changed');
  eq(r.registeredAs, 'core', 'the fallback did not take');
  eq(r.panel.model.registeredAs, 'core', 'the model does not know its id');
  includes(doc.getElementById(S.SCOUT_ROOT_ID).text, 'registered as core',
    'the block must print which id it took');

  const calls2 = [];
  const registerOpen = (id) => { calls2.push(id); return { ok: true, id, reason: 'panel_registered' }; };
  eq(S.attach(registerOpen, { doc: makeDom(), cv: makeFakeCv() }).registeredAs, 'scout',
    'scout must be preferred when the shell knows it');
  eq(calls2.join(','), 'scout', 'core was claimed unnecessarily');
});

T('attach survives a registry that refuses everything, and never throws', () => {
  const r = S.attach(() => ({ ok: false, reason: 'panel_unknown' }), { doc: makeDom(), cv: makeFakeCv() });
  eq(r.registeredAs, null, 'claimed a registration it did not get');
  eq(r.tried.length, S.REGISTRY_IDS.length, 'not every id was tried');
  const t = S.attach((id) => { if (id === 'scout') throw new Error('nope'); return { ok: true, id }; },
    { doc: makeDom(), cv: makeFakeCv() });
  eq(t.registeredAs, 'core', 'a throwing register stopped the fallback');
});

T('attach refuses a non-function register loudly', () => {
  let threw = null;
  try { S.attach(null); } catch (e) { threw = e; }
  ok(threw instanceof TypeError, 'a bad seam was accepted silently');
});

T('the panel hooks match the registerPanel contract', () => {
  const p = S.createPanel({ doc: makeDom() });
  eq(typeof p.onState, 'function', 'onState');
  eq(typeof p.onFrame, 'function', 'onFrame');
  eq(p.onState({ matLocked: false }), true, 'onState must not throw on a plain view');
  eq(p.onState(null), true, 'onState must survive a null view');
});

// ============================================== 11. INVARIANTS IN THE SOURCE
G('invariants, asserted against the file itself');

const CODE = stripComments(SCOUT_SRC);

T('invariant 3 — no model weights, no network, nothing to download', () => {
  for (const bad of ['fetch(', 'XMLHttpRequest', 'WebSocket', 'importScripts',
    'tf.', 'onnx', 'tensorflow', '.weights', '.tflite', 'https://', 'http://']) {
    excludes(CODE, bad, `SCOUT reaches the network or a model: ${bad}`);
  }
});

T('invariant 4 — SCOUT has no encoder and no egress', () => {
  for (const bad of ['toDataURL', 'toBlob', 'getImageData', 'putImageData',
    'createImageBitmap', 'captureStream', 'FileReader', 'postMessage', 'send(']) {
    excludes(CODE, bad, `SCOUT can move a frame off the canvas: ${bad}`);
  }
});

T('invariant 2 — SCOUT cannot touch money and cannot go green', () => {
  for (const bad of ['paise', 'Paise', 'rupee', 'amount', 'razorpay', 'Razorpay',
    'webhook', 'intent', 'setPanelStatus', '3ddc84']) {
    excludes(CODE, bad, `SCOUT reaches into the billing loop: ${bad}`);
  }
  excludes(CODE, "'GREEN'", 'SCOUT names a GREEN status');
  excludes(CODE, '"GREEN"', 'SCOUT names a GREEN status');
});

T('no class name and no confidence anywhere in the shipped strings', () => {
  const strings = SCOUT_SRC.match(/'[^'\\\n]*'|"[^"\\\n]*"/g) || [];
  const looksLikeCopy = strings
    .map((s) => s.slice(1, -1))
    .filter((s) => /\s/.test(s) && s.length > 3);
  ok(looksLikeCopy.length > 20, 'the string scan found nothing to scan');
  for (const s of looksLikeCopy) {
    ok(!/\b\d\.\d\d\b/.test(s), `a confidence-shaped number is in the copy: ${JSON.stringify(s)}`);
  }
  measured.copy_strings_scanned = String(looksLikeCopy.length);
});

T('the mat constants still agree with web/app.js', () => {
  if (APP_SRC === null) { measured.app_constants = 'app.js unreadable'; return; }
  for (const [name, want] of [['MAT_W_MM', S.MAT_W_MM], ['MAT_H_MM', S.MAT_H_MM],
    ['BUF_W', S.BUF_W], ['BUF_H', S.BUF_H]]) {
    const m = new RegExp(`export const ${name} = ([\\d.]+)`).exec(APP_SRC);
    ok(m !== null, `app.js no longer exports ${name}`);
    eq(Number(m[1]), want, `${name} drifted from app.js`);
  }
  near(S.PX_PER_MM_X, 2.82828, 0.0001, 'px/mm across');
  near(S.PX_PER_MM_Y, 2.82857, 0.0001, 'px/mm down');
  measured.app_constants = 'agree';
});

T('the crop kind SCOUT accepts is the one app.js actually sends', () => {
  if (APP_SRC === null) { measured.cropkind = 'app.js unreadable'; return; }
  includes(APP_SRC, "RETAIN_RECTIFIED = 'rectified_mat_crop'", 'the crop kind moved');
  includes(SCOUT_SRC, "'rectified_mat_crop'", 'SCOUT stopped checking the crop kind');
  measured.cropkind = 'rectified_mat_crop';
});

T("the fallback to 'core' is needed, and 'core' is a real id — checked against app.js", () => {
  if (APP_SRC === null) { measured.panel_ids = 'app.js unreadable'; return; }
  const m = /export const PANEL_IDS = Object\.freeze\(\[([^\]]*)\]\)/.exec(APP_SRC);
  ok(m !== null, 'app.js no longer exports PANEL_IDS');
  const ids = m[1].split(',').map((s) => s.trim().replace(/^'|'$/g, '')).filter(Boolean);
  measured.panel_ids = ids.join('|');
  // registerPanel refuses an id outside this list, so the chain must end in one
  ok(S.REGISTRY_IDS.some((id) => ids.includes(id)),
    `none of SCOUT's ids ${S.REGISTRY_IDS.join(',')} is registerable: ${ids.join(',')}`);
  eq(S.REGISTRY_IDS[0], 'scout', "'scout' must be tried first, so the shell can adopt it");
  ok(ids.includes('core'), "the fallback id 'core' is not a real panel");
  // If app.js ever grows 'scout', say so — the fallback becomes dead weight.
  measured.scout_id_adopted = ids.includes('scout') ? 'YES — the core fallback is now dead code' : 'not yet';
});

T('the shell mount points SCOUT needs exist, and its own ids do not collide', () => {
  if (HTML_SRC === null) { measured.shell_seam = 'index.html unreadable'; return; }
  const need = [S.SCOUT_HOST_ID, S.RAW_CANVAS_ID, S.RECT_CANVAS_ID];
  const found = need.filter((id) => HTML_SRC.includes(`id="${id}"`));
  measured.shell_mount_points = found.join('|') || 'none';
  eq(found.length, need.length, `missing mount point: ${need.filter((n) => !found.includes(n)).join(',')}`);
  for (const mine of [S.SCOUT_ROOT_ID, S.SCOUT_OVERLAY_ID, S.SCOUT_RECT_OVERLAY_ID]) {
    excludes(HTML_SRC, `id="${mine}"`, `the shell already owns ${mine}`);
  }
});

T('SCOUT imports nothing, so a data: URL can load it', () => {
  ok(!/^\s*import\s/m.test(CODE), 'SCOUT grew an import and can no longer be unit-tested');
});

T('the exports the task named are all present and are functions', () => {
  for (const name of ['boxesFromContours', 'trackBoxes', 'labelFor', 'bannerFor',
    'drawScout', 'contoursFrom', 'scanSource', 'createPanel', 'attach',
    'minAreaRect', 'convexHull', 'newTrackState', 'ensureOverlay', 'scoutStatus']) {
    eq(typeof S[name], 'function', `missing export: ${name}`);
  }
  eq(typeof S.SCOUT_ID, 'string', 'SCOUT_ID');
  ok(Object.isFrozen(S.BOX_DEFAULTS), 'BOX_DEFAULTS must be frozen');
  ok(Object.isFrozen(S.COLOURS), 'COLOURS must be frozen');
});

// ================================================================= report ===
console.log('\n──────────────────────────────────────────────────────────────');
console.log('MEASURED NUMBERS (produced by this run)');
for (const [k, v] of Object.entries(measured)) console.log(`  ${k.padEnd(30)} ${v}`);

if (failures.length) {
  console.log('\nFAILURES');
  for (const f of failures) console.log(`  ✗ ${f}`);
}
console.log('\n──────────────────────────────────────────────────────────────');
console.log(`${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
