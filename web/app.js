/* GAWAAH — the COUNTER PWA.
 *
 * The phone does GEOMETRY ONLY. There are zero model weights here and there is
 * no code path that loads any (invariant 3). Perception adjudication lives on
 * the brain; minting lives in paisa. This file computes a homography, applies
 * the mat mask AT FRAME GRAB, paints glyphs back through H-inverse, and keeps a
 * total in integer paise.
 *
 * Structure, deliberately:
 *   - everything above `boot()` is PURE and side-effect free, so it can be
 *     imported and exercised by `node web/selftest.mjs` with no DOM;
 *   - `boot()` and below is the browser shell, and only runs when `document`
 *     exists.
 *
 * Vendored OpenCV: ./vendor/opencv.js, pinned to
 * @techstark/opencv-js@4.11.0-release.1. See web/README.md for the byte budget
 * and why 5.0.0 is refused. It is NOT downloaded here; if it is absent the app
 * degrades loudly to reason OPENCV_ABSENT and never fakes a lock.
 */

// ===========================================================================
// Mat geometry. These MUST agree with gawaah/takhti.py to the last digit.
// A3 is 297x420mm into an 840x1188 buffer, i.e. 2*sqrt(2) px/mm. NOT 2.
// ===========================================================================
export const MAT_W_MM = 297.0;
export const MAT_H_MM = 420.0;
export const BUF_W = 840;
export const BUF_H = 1188;
export const PX_PER_MM_X = BUF_W / MAT_W_MM;   // 2.82828...
export const PX_PER_MM_Y = BUF_H / MAT_H_MM;   // 2.82857...
export const PX_PER_MM = (PX_PER_MM_X + PX_PER_MM_Y) / 2;
export const MARKER_MM = 30.0;
export const MARGIN_MM = 12.0;
export const MARKER_IDS = [0, 1, 2, 3];        // TL, TR, BR, BL

// Mat-lock gates, mirrored from gawaah/takhti.py.
export const MAX_SCALE_ERR = 0.015;
export const PERSP_K = 0.286;
export const MAX_PERSP_INDEX = 0.040;

/** Marker centres in mat millimetres, TL,TR,BR,BL. */
export function markerCentresMm() {
  const c = MARGIN_MM + MARKER_MM / 2;
  return [[c, c], [MAT_W_MM - c, c], [MAT_W_MM - c, MAT_H_MM - c], [c, MAT_H_MM - c]];
}

/** Mat millimetres -> rectified buffer pixels. */
export function mmToBuffer(pt) { return [pt[0] * PX_PER_MM_X, pt[1] * PX_PER_MM_Y]; }
/** Rectified buffer pixels -> mat millimetres. */
export function bufferToMm(pt) { return [pt[0] / PX_PER_MM_X, pt[1] / PX_PER_MM_Y]; }

// ===========================================================================
// MONEY BEGIN — integer paise only.
// INVARIANT 1. No float ever enters this block. There is no `parseFloat`, no
// `toFixed`, no decimal literal and no unguarded `/` on a money value below.
// The single division is `(p - r) / 100` where `p - r` is an exact multiple of
// 100, so IEEE754 returns the exact integer quotient. selftest.mjs re-checks
// this block by source scan, mirroring tools/lint_no_float.py.
// ===========================================================================
export class MoneyError extends Error {
  constructor(msg) { super(msg); this.name = 'MoneyError'; }
}

/** Construct paise. Rejects bool, float, string, null and unsafe integers. */
export function paise(value) {
  if (typeof value === 'boolean') throw new MoneyError(`bool is not money: ${value}`);
  if (typeof value !== 'number') {
    throw new MoneyError(`not a number: ${JSON.stringify(value)} (${typeof value})`);
  }
  if (!Number.isFinite(value)) throw new MoneyError(`not finite: ${value}`);
  if (!Number.isInteger(value)) {
    throw new MoneyError(
      `float is not money: ${value}. Money is integer paise; a rupee is not a float.`);
  }
  if (!Number.isSafeInteger(value)) throw new MoneyError(`outside safe integer range: ${value}`);
  return value;
}

/** Exact integer divmod by 100. No float division: `p - r` is a multiple of 100. */
function divmod100(p) {
  const r = p % 100;
  return [(p - r) / 100, r];
}

/**
 * Parse a decimal rupee STRING to paise without ever touching a float.
 * '214.50' -> 21450. Takes a string, never a number: 214.50 is already lossy.
 */
export function fromRupeesStr(s) {
  if (typeof s !== 'string') throw new MoneyError(`rupees must be a string, got ${typeof s}`);
  let t = s.trim();
  if (t === '') throw new MoneyError('empty rupee string');
  const neg = t.startsWith('-');
  if (neg) t = t.slice(1);
  const dot = t.indexOf('.');
  let whole = t, frac = '';
  if (dot >= 0) { whole = t.slice(0, dot); frac = t.slice(dot + 1); }
  if (t.indexOf('.', dot + 1) >= 0) throw new MoneyError(`bad rupee string: ${s}`);
  const digits = (x) => x.length > 0 && /^[0-9]+$/.test(x);
  if (whole !== '' && !digits(whole)) throw new MoneyError(`bad rupee string: ${s}`);
  if (frac !== '' && !digits(frac)) throw new MoneyError(`bad rupee string: ${s}`);
  if (whole === '' && frac === '') throw new MoneyError(`bad rupee string: ${s}`);
  if (frac.length > 2) throw new MoneyError(`sub-paisa precision is not money: ${s}`);
  const f = (frac + '00').slice(0, 2);
  const t2 = (whole === '' ? 0 : Number.parseInt(whole, 10)) * 100 + Number.parseInt(f, 10);
  const out = paise(t2);
  return neg ? -out : out;
}

/** Render paise as a rupee string. Never returns or touches a number with a fraction. */
export function toRupeesStr(p) {
  const v = paise(p);
  const sign = v < 0 ? '-' : '';
  const [whole, rem] = divmod100(v < 0 ? -v : v);
  return `${sign}${whole}.${String(rem).padStart(2, '0')}`;
}

/** Render paise for the counter chrome, e.g. 21450 -> '₹214.50'. */
export function formatRupees(p) { return `₹${toRupeesStr(p)}`; }

export function addPaise(...values) {
  let t = 0;
  for (const v of values) t = paise(t + paise(v));
  return t;
}

export function sumPaise(values) {
  let t = 0;
  for (const v of values) t = paise(t + paise(v));
  return t;
}
// ===========================================================================
// MONEY END
// ===========================================================================

// ===========================================================================
// Geometry: 3x3 homographies, pure JS. No OpenCV needed for any of this, which
// is why the geometry core stays testable under node.
// ===========================================================================

/** Apply a row-major 3x3 homography to a point. Returns [u, v]. */
export function applyH(H, x, y) {
  const w = H[6] * x + H[7] * y + H[8];
  if (w === 0) throw new Error('applyH: point maps to infinity (w == 0)');
  return [(H[0] * x + H[1] * y + H[2]) / w, (H[3] * x + H[4] * y + H[5]) / w];
}

/** Apply H to a list of [x,y] points. */
export function applyHMany(H, pts) { return pts.map((p) => applyH(H, p[0], p[1])); }

/** Invert a row-major 3x3. Throws when singular. */
export function invert3x3(H) {
  const [a, b, c, d, e, f, g, h, i] = H;
  const A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
  const det = a * A + b * B + c * C;
  if (det === 0 || !Number.isFinite(det)) throw new Error('invert3x3: singular matrix');
  const inv = [
    A / det, (c * h - b * i) / det, (b * f - c * e) / det,
    B / det, (a * i - c * g) / det, (c * d - a * f) / det,
    C / det, (b * g - a * h) / det, (a * e - b * d) / det,
  ];
  // Normalise so H[8] == 1 when possible; keeps comparisons scale-free.
  return inv[8] !== 0 ? inv.map((v) => v / inv[8]) : inv;
}

export function mat3Mul(A, B) {
  const out = new Array(9).fill(0);
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 3; c++) {
      let s = 0;
      for (let k = 0; k < 3; k++) s += A[r * 3 + k] * B[k * 3 + c];
      out[r * 3 + c] = s;
    }
  }
  return out;
}

/** Solve a dense n x n system by Gaussian elimination with partial pivoting. */
function solveDense(A, b) {
  const n = b.length;
  const M = A.map((row, i) => row.concat([b[i]]));
  for (let col = 0; col < n; col++) {
    let piv = col;
    for (let r = col + 1; r < n; r++) if (Math.abs(M[r][col]) > Math.abs(M[piv][col])) piv = r;
    if (Math.abs(M[piv][col]) < 1e-14) throw new Error('solveDense: singular system');
    if (piv !== col) { const t = M[piv]; M[piv] = M[col]; M[col] = t; }
    const p = M[col][col];
    for (let r = col + 1; r < n; r++) {
      const factor = M[r][col] / p;
      if (factor === 0) continue;
      for (let c = col; c <= n; c++) M[r][c] -= factor * M[col][c];
    }
  }
  const x = new Array(n).fill(0);
  for (let r = n - 1; r >= 0; r--) {
    let s = M[r][n];
    for (let c = r + 1; c < n; c++) s -= M[r][c] * x[c];
    x[r] = s / M[r][r];
  }
  return x;
}

/**
 * Exact 4-point DLT homography, src -> dst, both [[x,y],...] of length 4.
 * This is the same map cv2.findHomography(method=0) produces for 4 points, and
 * selftest.mjs checks it against a vector generated by the real Python
 * PlaneEngine. Having it in pure JS means the geometry is testable under node
 * and the app still has a homography if OpenCV is absent.
 */
export function homographyFrom4Points(src, dst) {
  if (src.length !== 4 || dst.length !== 4) {
    throw new Error('homographyFrom4Points: need exactly 4 correspondences');
  }
  const A = [], b = [];
  for (let k = 0; k < 4; k++) {
    const [x, y] = src[k], [u, v] = dst[k];
    A.push([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.push(u);
    A.push([0, 0, 0, x, y, 1, -v * x, -v * y]); b.push(v);
  }
  const h = solveDense(A, b);
  return [h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7], 1];
}

/** Root-mean-square reprojection error of H over correspondences, in px. */
export function reprojRmse(H, src, dst) {
  let s = 0;
  for (let k = 0; k < src.length; k++) {
    const [u, v] = applyH(H, src[k][0], src[k][1]);
    s += (u - dst[k][0]) ** 2 + (v - dst[k][1]) ** 2;
  }
  return Math.sqrt(s / src.length);
}

/**
 * Dimensionless perspective index, mirroring PlaneEngine._persp_index:
 * the last row of the buffer->frame homography, scaled by the buffer's
 * characteristic length. 0 == fronto-parallel.
 */
export function perspIndex(H) {
  let Hi;
  try { Hi = invert3x3(H); } catch { return Infinity; }
  if (Hi[8] === 0) return Infinity;
  const n = Hi.map((v) => v / Hi[8]);
  return Math.hypot(n[6], n[7]) * Math.max(BUF_W, BUF_H);
}

/**
 * Approximate tilt in degrees. HONESTY NOTE, carried over from takhti.py:
 * PERSP_K absorbs focal length, so this is calibrated for the synthetic rig
 * only. It is shown as "~N deg" and must not be reported as a measured angle.
 */
export function perspToDeg(index) { return (Math.atan(index / PERSP_K) * 180) / Math.PI; }

/**
 * Worst-case marker side error measured ON THE RECTIFIED PLANE, vs MARKER_MM.
 * Measuring in the raw frame instead conflates real scale error with ordinary
 * foreshortening. `quadsByFrame` is a list of 4 marker corner quads in FRAME px.
 */
export function scaleError(quadsByFrame, H) {
  let worst = 0;
  for (const quad of quadsByFrame) {
    const r = applyHMany(H, quad);
    for (const [a, b] of [[0, 1], [1, 2], [2, 3], [3, 0]]) {
      const sideMm = Math.hypot(r[a][0] - r[b][0], r[a][1] - r[b][1]) / PX_PER_MM;
      worst = Math.max(worst, Math.abs(sideMm - MARKER_MM) / MARKER_MM);
    }
  }
  return worst;
}

/**
 * Structural + numeric validation of one detected marker quad.
 * Returns null when the quad is usable, or a human reason when it is not.
 *
 * INVARIANT 7, at the boundary. The detector is a foreign function — OpenCV
 * compiled to wasm, driven by a camera — and it can hand back a short corner
 * buffer, a null row, or a non-finite coordinate. Every one of those is an
 * ABSTENTION, so it has to be turned into a named refusal HERE. Two failure
 * modes are being closed:
 *   - a missing/short corner makes `q[k][0]` throw a TypeError, which unwinds
 *     past the caller's assignment and leaves the PREVIOUS lock standing;
 *   - Infinity or NaN propagates silently into the DLT, poisons H, and then
 *     trips the scale gate — which abstains, but MISREPORTS the cause as
 *     "scale error NaN%" and still hands a non-finite H back to the caller.
 * Number.isFinite is the right predicate: it is false for NaN, ±Infinity,
 * undefined, null and strings alike.
 */
export function quadFault(q) {
  if (!Array.isArray(q)) return 'is not a quad';
  if (q.length !== 4) return `has ${q.length} corners, need 4`;
  for (let k = 0; k < 4; k++) {
    const c = q[k];
    if (!Array.isArray(c) || c.length < 2) return `corner ${k} is not an [x, y] pair`;
    if (!Number.isFinite(c[0]) || !Number.isFinite(c[1])) {
      return `corner ${k} is non-finite (${String(c[0])}, ${String(c[1])})`;
    }
  }
  return null;
}

/**
 * Adjudicate a mat lock from detected marker quads (frame px, keyed by id).
 * Pure: takes the detection result, returns the same shape PlaneEngine.detect
 * returns. Abstains with a named reason rather than guessing, and NEVER throws:
 * a throw here is indistinguishable, at the call site, from "nothing changed".
 */
export function adjudicateLock(quadsById) {
  if (quadsById === null || typeof quadsById !== 'object') {
    return { locked: false, reason: 'no markers detected', idsFound: [] };
  }
  const found = MARKER_IDS.filter((i) => Array.isArray(quadsById[i]));
  if (found.length === 0) return { locked: false, reason: 'no markers detected', idsFound: [] };
  if (found.length < 4) {
    const missing = MARKER_IDS.filter((i) => !found.includes(i));
    return { locked: false, reason: `missing markers ${JSON.stringify(missing)}`, idsFound: found };
  }
  // Every corner of every marker is validated BEFORE any of it reaches the DLT.
  for (const i of MARKER_IDS) {
    const fault = quadFault(quadsById[i]);
    if (fault) return { locked: false, reason: `marker ${i} ${fault}`, idsFound: found };
  }
  const centre = (q) => [
    (q[0][0] + q[1][0] + q[2][0] + q[3][0]) / 4,
    (q[0][1] + q[1][1] + q[2][1] + q[3][1]) / 4,
  ];
  const src = MARKER_IDS.map((i) => centre(quadsById[i]));
  const dst = markerCentresMm().map(mmToBuffer);
  let H;
  try { H = homographyFrom4Points(src, dst); }
  catch { return { locked: false, reason: 'homography failed', idsFound: found }; }
  // Finite inputs can still overflow to a non-finite H (huge coordinates make
  // the elimination divide Infinity by Infinity). Refuse rather than ship NaN.
  if (!H.every(Number.isFinite)) {
    return { locked: false, reason: 'homography is non-finite', idsFound: found };
  }

  const rmse = reprojRmse(H, src, dst);
  const persp = perspIndex(H);
  const scaleErr = scaleError(MARKER_IDS.map((i) => quadsById[i]), H);
  const base = { H, idsFound: found, scaleErr, perspIndex: persp, reprojRmsePx: rmse };

  if (!(scaleErr <= MAX_SCALE_ERR)) {
    return { locked: false, reason: `scale error ${(scaleErr * 100).toFixed(2)}% > 1.5%`, ...base };
  }
  if (!(persp <= MAX_PERSP_INDEX)) {
    return {
      locked: false,
      reason: `perspective index ${persp.toFixed(4)} > ${MAX_PERSP_INDEX} (~${perspToDeg(persp).toFixed(1)} deg)`,
      ...base,
    };
  }
  return { locked: true, reason: 'locked', ...base };
}

/** Named reason for a detector that threw. Mirrored into Reason below. */
export const DETECTOR_FAILED = 'detector_threw_lock_cleared';

/**
 * The ONLY sanctioned way to call a detector.
 *
 * INVARIANT 7, fail closed. `lock = detector(frame)` is a trap: when detector()
 * throws, the assignment never happens and the PREVIOUS lock silently survives.
 * The chrome then goes on saying MAT LOCK, the frame-grab policy goes on
 * retaining a crop, and the app goes on billing lines against a plane the camera
 * can no longer see. A detector that throws knows LESS than one that returns
 * "no markers", so it must produce a WEAKER verdict, never a stale stronger one.
 *
 * Always returns a fresh, not-locked-unless-proven verdict. Never throws.
 * Never returns the previous lock, because it has never seen it.
 */
export function safeDetect(detector, frame, absentReason) {
  if (typeof detector !== 'function') {
    return { locked: false, reason: absentReason || 'detector not ready' };
  }
  let out;
  try {
    out = detector(frame);
  } catch (e) {
    const msg = (e && e.message) ? e.message : String(e);
    return { locked: false, reason: `${DETECTOR_FAILED}: ${msg}` };
  }
  if (out === null || typeof out !== 'object' || Array.isArray(out)) {
    return { locked: false, reason: `${DETECTOR_FAILED}: detector returned ${typeof out}, not a verdict` };
  }
  // `locked` is checked for the literal true, so a truthy 1 or 'true' cannot
  // sneak a lock past this boundary.
  if (out.locked !== true) {
    return { ...out, locked: false, reason: out.reason || 'detector returned no lock' };
  }
  if (!Array.isArray(out.H) || out.H.length !== 9 || !out.H.every(Number.isFinite)) {
    return { ...out, locked: false, reason: 'detector claimed a lock without a finite homography' };
  }
  // render() formats these three with toFixed, and invert3x3/warpPerspective
  // consume H. A lock missing any of them would crash the frame loop, which is
  // the same stale-lock failure by a different door.
  for (const k of ['scaleErr', 'perspIndex', 'reprojRmsePx']) {
    if (!Number.isFinite(out[k])) {
      return { ...out, locked: false, reason: `detector claimed a lock without a finite ${k}` };
    }
  }
  return out;
}

// ===========================================================================
// Glyph projection. A rupee glyph lives at a point on the MAT (mm). To paint it
// on the counter in perspective we push it through H-inverse (buffer -> frame)
// and use the LOCAL AFFINE JACOBIAN of that projective map at the glyph's
// anchor. That is what makes text lie down on the wood instead of floating.
// ===========================================================================

/**
 * Analytic 2x2 Jacobian of the projective map H at (x, y).
 * u = (h0 x + h1 y + h2)/w, w = h6 x + h7 y + h8
 *   du/dx = (h0 - u h6)/w   du/dy = (h1 - u h7)/w
 *   dv/dx = (h3 - v h6)/w   dv/dy = (h4 - v h7)/w
 * Returns {ux, uy, vx, vy, u, v}.
 */
export function jacobianAt(H, x, y) {
  const w = H[6] * x + H[7] * y + H[8];
  if (w === 0) throw new Error('jacobianAt: point maps to infinity (w == 0)');
  const u = (H[0] * x + H[1] * y + H[2]) / w;
  const v = (H[3] * x + H[4] * y + H[5]) / w;
  return {
    u, v,
    ux: (H[0] - u * H[6]) / w,
    uy: (H[1] - u * H[7]) / w,
    vx: (H[3] - v * H[6]) / w,
    vy: (H[4] - v * H[7]) / w,
  };
}

/**
 * Canvas transform that paints a glyph in perspective at a mat position.
 * `Hinv` maps rectified BUFFER px -> FRAME px. `mm` is the glyph anchor on the
 * mat. `scale` is glyph units per millimetre.
 * Returns {a,b,c,d,e,f} for ctx.setTransform: x' = a x + c y + e, y' = b x + d y + f.
 */
export function glyphTransform(Hinv, mm, scale = 1) {
  const [bx, by] = mmToBuffer(mm);
  const J = jacobianAt(Hinv, bx, by);
  // Chain the mm->buffer scaling in, so callers work in millimetres.
  return {
    a: J.ux * PX_PER_MM_X * scale,
    b: J.vx * PX_PER_MM_X * scale,
    c: J.uy * PX_PER_MM_Y * scale,
    d: J.vy * PX_PER_MM_Y * scale,
    e: J.u,
    f: J.v,
  };
}

/** Corners of an axis-aligned mm rectangle centred on `mm`, TL,TR,BR,BL. */
export function glyphQuadMm(mm, wMm, hMm) {
  const [x, y] = mm, hw = wMm / 2, hh = hMm / 2;
  return [[x - hw, y - hh], [x + hw, y - hh], [x + hw, y + hh], [x - hw, y + hh]];
}

/** Project a mm quad all the way to frame pixels through Hinv (buffer->frame). */
export function projectQuadMm(Hinv, quadMm) {
  return quadMm.map((p) => { const b = mmToBuffer(p); return applyH(Hinv, b[0], b[1]); });
}

/** The mat outline in frame px, for the lock overlay and the raw-feed mask. */
export function matOutlineFrame(Hinv) {
  return projectQuadMm(Hinv, [[0, 0], [MAT_W_MM, 0], [MAT_W_MM, MAT_H_MM], [0, MAT_H_MM]]);
}

/** Even-odd winding point-in-polygon, used for tap hit-testing on glyph quads. */
export function pointInQuad(pt, quad) {
  let inside = false;
  for (let i = 0, j = quad.length - 1; i < quad.length; j = i++) {
    const [xi, yi] = quad[i], [xj, yj] = quad[j];
    const hit = (yi > pt[1]) !== (yj > pt[1])
      && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi;
    if (hit) inside = !inside;
  }
  return inside;
}

/** Topmost line whose projected glyph quad contains the tap. null when none. */
export function hitTestGlyph(pt, glyphs) {
  for (let i = glyphs.length - 1; i >= 0; i--) {
    if (pointInQuad(pt, glyphs[i].quad)) return glyphs[i].itemId;
  }
  return null;
}

// ===========================================================================
// INVARIANT 4 — the mask is applied AT FRAME GRAB. The rectified mat crop is
// the only buffer that survives. This is a policy function so it can be proven
// by test rather than asserted in prose: there is no input for which it returns
// anything that permits a raw frame to be retained or sent.
// ===========================================================================
export const RETAIN_RECTIFIED = 'rectified_mat_crop';
export const RETAIN_NOTHING = 'nothing';

export function frameGrabPolicy(lock) {
  if (!lock || lock.locked !== true) {
    return {
      retain: RETAIN_NOTHING,
      egress: RETAIN_NOTHING,
      reason: lock && lock.reason ? `mat not locked: ${lock.reason}` : 'mat not locked',
    };
  }
  if (!Array.isArray(lock.H) || lock.H.length !== 9) {
    return { retain: RETAIN_NOTHING, egress: RETAIN_NOTHING, reason: 'mat locked without a homography' };
  }
  return {
    retain: RETAIN_RECTIFIED,
    egress: RETAIN_RECTIFIED,
    reason: 'rectified crop only',
    width: BUF_W,
    height: BUF_H,
  };
}

/** Keys that must never appear on anything leaving the device. */
const FORBIDDEN_EGRESS_KEYS = [
  'raw', 'rawFrame', 'raw_frame', 'frame', 'fullFrame', 'full_frame',
  'camera', 'videoFrame', 'video_frame', 'unmasked', 'photo', 'snapshot',
];

/**
 * Runtime guard on every outbound payload. Throws rather than leaking. Called
 * on the send path, not merely at review time.
 */
export function assertRectifiedOnly(payload) {
  if (payload === null || typeof payload !== 'object') return payload;
  for (const k of Object.keys(payload)) {
    if (FORBIDDEN_EGRESS_KEYS.includes(k)) {
      throw new Error(`invariant 4 violated: payload key '${k}' is not the rectified crop`);
    }
  }
  // Both the live canvas (`crop`) and its encoded wire form (`cropPng`) are
  // pixels of the mat. Either one present without the rectified tag is a leak,
  // so they are gated identically — encoding a buffer must not launder it.
  for (const k of ['crop', 'cropPng']) {
    if (Object.prototype.hasOwnProperty.call(payload, k) && payload.cropKind !== RETAIN_RECTIFIED) {
      throw new Error(`invariant 4 violated: ${k} present without cropKind == '${RETAIN_RECTIFIED}'`);
    }
  }
  return payload;
}

// ===========================================================================
// Session state. Mirrors gawaah/session.py so the phone and the brain agree on
// what is amber. The brain remains authoritative; this is the display copy.
// ===========================================================================
export const State = Object.freeze({
  SETUP: 'SETUP', IDLE: 'IDLE', MEASURING: 'MEASURING', PRICED: 'PRICED', AMBER: 'AMBER',
  BASKET_OPEN: 'BASKET_OPEN', AWAITING_SETTLEMENT: 'AWAITING_SETTLEMENT',
  PENDING_OFFLINE: 'PENDING_OFFLINE', PAID: 'PAID', AMOUNT_MISMATCH: 'AMOUNT_MISMATCH',
  MAT_LOST: 'MAT_LOST', BRAIN_LOST: 'BRAIN_LOST', DEGRADED: 'DEGRADED',
  FROZEN_TOTAL: 'FROZEN_TOTAL',
});

export const Reason = Object.freeze({
  SESSION_OPENED: 'session_opened',
  MAT_LOCKED: 'mat_locked', MAT_LOST: 'mat_lost', MAT_REACQUIRED: 'mat_reacquired',
  BRAIN_LOST: 'brain_lost', BRAIN_REACQUIRED: 'brain_reacquired', STILL_FROZEN: 'still_frozen',
  PLACEMENT_SEEN: 'placement_seen', PRICED: 'priced_from_gallery', UNKNOWN_SKU: 'unknown_sku',
  PRICE_TAPPED: 'price_tapped', COMMITTED: 'exit_crossing_committed',
  COMMITTED_AMBER: 'exit_crossing_committed_amber_excluded',
  REVERTED: 'reverted_by_shopkeeper',
  UNCOUNTED_CROSSING: 'uncounted_crossing_no_tracker_id',
  UNTRACKED_EXIT: 'uncounted_crossing_unknown_item',
  HUMAN_ACKNOWLEDGED: 'human_acknowledged_freeze',
  INTENT_REQUESTED: 'intent_requested',
  OFFLINE_NO_AUTHORISATION: 'offline_billing_continues_nothing_authorised',
  NETWORK_DOWN: 'network_down',
  NETWORK_DOWN_BILLING_CONTINUES: 'network_down_billing_continues',
  NETWORK_RESTORED: 'network_restored',
  BAD_SIGNATURE: 'webhook_signature_invalid_discarded',
  FOREIGN_SESSION: 'webhook_session_id_does_not_match_discarded',
  NO_OPEN_INTENT: 'webhook_no_open_intent_discarded',
  ALREADY_SETTLED: 'webhook_after_settlement_ignored',
  NOT_IN_GREEN_SET: 'webhook_event_not_in_green_set',
  PAISA_REFUSED_GREEN: 'paisa_refused_green',
  AMOUNT_MISMATCH: 'webhook_amount_does_not_match_intent',
  SETTLED: 'settled_green',
  DEGRADED: 'p95_over_threshold', PERF_RECOVERED: 'p95_recovered',
  DEGRADED_REQUIRES_TAP: 'degraded_auto_commit_disabled_tap_required',

  // ---- camera. Invariant 7: a dead camera must NAME why it is dead. A black
  // pane with no text is the failure this whole block exists to prevent.
  CAMERA_IDLE: 'camera_not_started_tap_start',
  CAMERA_STARTING: 'camera_starting',
  CAMERA_LIVE: 'camera_live',
  CAMERA_DENIED: 'camera_permission_denied_by_the_browser',
  CAMERA_ABSENT: 'no_camera_device_found',
  CAMERA_BUSY: 'camera_busy_another_app_holds_it',
  CAMERA_INSECURE: 'insecure_context_camera_blocked_serve_over_https_or_localhost',
  CAMERA_UNSUPPORTED: 'getusermedia_unsupported_in_this_browser',
  CAMERA_OVERCONSTRAINED: 'camera_cannot_satisfy_requested_constraints',
  CAMERA_ABORTED: 'camera_hardware_aborted_the_stream',
  CAMERA_FAILED: 'camera_failed_for_an_unnamed_reason',
  CAMERA_REAR: 'rear_camera_environment_facing',
  CAMERA_FRONT: 'front_camera_user_facing_mat_will_not_lock',
  CAMERA_FACING_UNKNOWN: 'camera_facing_unknown_browser_did_not_say',
  CAMERA_STATE_UNKNOWN: 'camera_state_unrecognised_refusing_to_assume_it_is_live',

  // ---- brain transport
  WS_CONNECTING: 'brain_connecting',
  WS_OPEN: 'brain_connected',
  WS_RETRYING: 'brain_lost_retrying_with_backoff',
  WS_OFFLINE: 'brain_offline_billing_continues_nothing_authorised',

  // ---- panel router / panel API
  PANEL_SHOWN: 'panel_shown',
  PANEL_SAME: 'panel_already_shown',
  PANEL_UNKNOWN: 'refused_unknown_panel_id',
  PANEL_REGISTERED: 'panel_registered',
  PANEL_REPLACED: 'panel_registration_replaced',
  PANEL_BAD_HOOKS: 'refused_panel_hooks_are_not_functions',
  PANEL_HOOK_THREW: 'panel_hook_threw_isolated_from_the_counter',
  PANEL_NO_DATA: 'panel_has_no_reading_yet',
  PANEL_NOT_WIRED: 'no_module_is_attached_to_this_capability_yet',
  PANEL_ATTACH_FAILED: 'panel_module_failed_to_attach',
  PANEL_BAD_STATUS: 'refused_panel_status_not_off_abstain_or_ok',
  PANEL_NEVER_GREEN: 'refused_panel_may_not_declare_green',

  // ---- the wire form of a rectified crop
  WIRE_NO_ENCODER: 'rectified_crop_has_no_encoder_nothing_sent',
  WIRE_ENCODE_FAILED: 'rectified_crop_did_not_encode_nothing_sent',
  MAT_NOT_LOCKED: 'refused_mat_not_locked',
  REFUSED_MAT_LOST: 'refused_mat_lost', REFUSED_BRAIN_LOST: 'refused_brain_lost',
  REFUSED_FROZEN_TOTAL: 'refused_total_frozen',
  BASKET_LOCKED: 'refused_basket_locked_after_done',
  RED_HOLD: 'refused_red_hold_manual_resolution',
  EMPTY_BASKET: 'refused_empty_basket', ZERO_TOTAL: 'refused_zero_total_all_amber',
  UNKNOWN_ITEM: 'refused_unknown_item', REVERTED_ITEM: 'refused_item_already_reverted',
  DUPLICATE: 'duplicate_event_ignored',
  OPENCV_ABSENT: 'opencv_absent_geometry_unavailable',
  DETECTOR_FAILED,
});

export const GREEN_EVENTS = Object.freeze(
  ['payment_link.paid', 'payment.captured', 'qr_code.credited']);
export const DEGRADED_P95_MS = 250;

const FROZEN_STATES = Object.freeze([State.MAT_LOST, State.BRAIN_LOST, State.FROZEN_TOTAL]);
const FROZEN_REASON = Object.freeze({
  [State.MAT_LOST]: Reason.REFUSED_MAT_LOST,
  [State.BRAIN_LOST]: Reason.REFUSED_BRAIN_LOST,
  [State.FROZEN_TOTAL]: Reason.REFUSED_FROZEN_TOTAL,
});
const BASKET_CLOSED = Object.freeze(
  [State.AWAITING_SETTLEMENT, State.PENDING_OFFLINE, State.PAID]);

export function initialState(sessionId = 'sess-local') {
  return Object.freeze({
    sessionId,
    state: State.SETUP,
    lines: Object.freeze([]),
    matLocked: false,
    brainUp: false,
    online: true,
    degraded: false,
    frozenTotalPaise: null,
    intentAmountPaise: null,
    authorisedPaise: 0,
    seenEventIds: Object.freeze([]),
    lastReason: Reason.SESSION_OPENED,
    lastApplied: true,
    queued: 0,
  });
}

/** Does a line contribute to the total? Amber (null price) never does. */
export function lineCounts(li) {
  return li.committed === true && li.reverted !== true && li.pricePaise !== null
    && li.pricePaise !== undefined;
}
export function lineIsAmber(li) { return li.pricePaise === null || li.pricePaise === undefined; }

/** R2: the total is RECOMPUTED from committed lines every time. Never a counter. */
export function liveTotalPaise(lines) {
  return sumPaise(lines.filter(lineCounts).map((li) => li.pricePaise));
}

/** The billable total. Frozen states report the snapshot, not a live sum. */
export function totalPaise(st) {
  return st.frozenTotalPaise !== null && st.frozenTotalPaise !== undefined
    ? paise(st.frozenTotalPaise)
    : liveTotalPaise(st.lines);
}

export function amberLines(st) {
  return st.lines.filter((li) => li.committed && !li.reverted && lineIsAmber(li));
}
export function committedLines(st) {
  return st.lines.filter((li) => li.committed && !li.reverted);
}

function withState(st, patch, reason, applied = true) {
  return Object.freeze({ ...st, ...patch, lastReason: reason, lastApplied: applied });
}
function refuse(st, reason) { return withState(st, {}, reason, false); }

function resumeTarget(st) {
  return st.lines.some((li) => li.committed && !li.reverted) ? State.BASKET_OPEN : State.IDLE;
}

/** Billing actions are refused while frozen, after DONE, or on a red hold. */
function billingGuard(st, { allowPaid = false } = {}) {
  if (FROZEN_STATES.includes(st.state)) return FROZEN_REASON[st.state];
  if (st.state === State.AMOUNT_MISMATCH) return Reason.RED_HOLD;
  if (!allowPaid && BASKET_CLOSED.includes(st.state)) return Reason.BASKET_LOCKED;
  return null;
}

function replaceLine(lines, itemId, patch) {
  return Object.freeze(lines.map((li) => (li.itemId === itemId ? Object.freeze({ ...li, ...patch }) : li)));
}

/**
 * The reducer. Pure: never mutates `st`, always returns a new frozen state.
 * Refusals return the state unchanged with lastApplied === false and a named
 * reason. Nothing here can produce State.PAID except a WEBHOOK action that
 * passes all four gates of invariant 2 — no timer, no mint, no render.
 */
export function reduce(st, action) {
  switch (action.type) {
    case 'MAT_LOCK': {
      if (action.locked) {
        if (st.matLocked && st.state !== State.MAT_LOST) return refuse(st, Reason.DUPLICATE);
        const reason = st.state === State.SETUP ? Reason.MAT_LOCKED : Reason.MAT_REACQUIRED;
        const next = st.state === State.MAT_LOST || st.state === State.SETUP
          ? resumeTarget(st) : st.state;
        return withState(st, { matLocked: true, state: next }, reason);
      }
      if (st.state === State.MAT_LOST) return refuse(st, Reason.STILL_FROZEN);
      return withState(st, {
        matLocked: false,
        state: State.MAT_LOST,
        frozenTotalPaise: totalPaise(st),
      }, Reason.MAT_LOST);
    }

    case 'BRAIN': {
      if (action.up) {
        if (st.brainUp && st.state !== State.BRAIN_LOST) return refuse(st, Reason.DUPLICATE);
        const next = st.state === State.BRAIN_LOST ? resumeTarget(st) : st.state;
        return withState(st, { brainUp: true, state: next }, Reason.BRAIN_REACQUIRED);
      }
      if (st.state === State.BRAIN_LOST) return refuse(st, Reason.STILL_FROZEN);
      return withState(st, {
        brainUp: false,
        state: State.BRAIN_LOST,
        frozenTotalPaise: totalPaise(st),
      }, Reason.BRAIN_LOST);
    }

    case 'PLACEMENT': {
      const g = billingGuard(st);
      if (g) return refuse(st, g);
      if (!st.matLocked) return refuse(st, Reason.MAT_NOT_LOCKED);
      if (st.lines.some((li) => li.itemId === action.itemId)) return refuse(st, Reason.DUPLICATE);
      const price = action.pricePaise === undefined ? null : action.pricePaise;
      if (price !== null) paise(price);           // rejects a float price at the door
      const amber = price === null;
      const line = Object.freeze({
        itemId: action.itemId,
        name: action.name ?? null,
        pricePaise: price,
        reason: amber ? Reason.UNKNOWN_SKU : Reason.PRICED,
        committed: false,
        reverted: false,
        centreMm: action.centreMm ?? null,
      });
      return withState(st, {
        lines: Object.freeze([...st.lines, line]),
        state: amber ? State.AMBER : State.PRICED,
      }, amber ? Reason.UNKNOWN_SKU : Reason.PRICED);
    }

    case 'PRICE': {
      const amount = paise(action.pricePaise);    // MoneyError on float/bool/string
      const line = st.lines.find((li) => li.itemId === action.itemId);
      if (!line) return refuse(st, Reason.UNKNOWN_ITEM);
      if (line.pricePaise === amount) return refuse(st, Reason.DUPLICATE);
      const g = billingGuard(st);
      if (g) return refuse(st, g);
      if (line.reverted) return refuse(st, Reason.REVERTED_ITEM);
      // Warm enroll: pricing a line on the mat resolves it out of AMBER;
      // pricing an already-committed line changes the money, not the chrome.
      return withState(st, {
        lines: replaceLine(st.lines, action.itemId,
          { pricePaise: amount, reason: Reason.PRICE_TAPPED }),
        state: line.committed ? st.state : State.PRICED,
      }, Reason.PRICE_TAPPED);
    }

    case 'EXIT': {
      const g = billingGuard(st);
      if (g) return refuse(st, g);
      // An exit crossing we cannot attribute freezes the total and asks for a
      // human. Invariant 7: abstain loudly, amber, never a guess and never red.
      if (action.itemId === null || action.itemId === undefined) {
        return withState(st, {
          state: State.FROZEN_TOTAL, frozenTotalPaise: totalPaise(st),
        }, Reason.UNCOUNTED_CROSSING);
      }
      const line = st.lines.find((li) => li.itemId === action.itemId);
      if (!line) {
        return withState(st, {
          state: State.FROZEN_TOTAL, frozenTotalPaise: totalPaise(st),
        }, Reason.UNTRACKED_EXIT);
      }
      if (line.committed) return refuse(st, Reason.DUPLICATE);
      if (st.degraded && action.tap !== true) return refuse(st, Reason.DEGRADED_REQUIRES_TAP);
      const amber = lineIsAmber(line);
      return withState(st, {
        lines: replaceLine(st.lines, action.itemId, {
          committed: true,
          reason: amber ? Reason.COMMITTED_AMBER : Reason.COMMITTED,
        }),
        state: State.BASKET_OPEN,
      }, amber ? Reason.COMMITTED_AMBER : Reason.COMMITTED);
    }

    case 'REVERT': {
      const line = st.lines.find((li) => li.itemId === action.itemId);
      if (!line) return refuse(st, Reason.UNKNOWN_ITEM);
      if (line.reverted) return refuse(st, Reason.DUPLICATE);
      const g = billingGuard(st);
      if (g) return refuse(st, g);
      const lines = replaceLine(st.lines, action.itemId, { reverted: true });
      const anyCommitted = lines.some((li) => li.committed && !li.reverted);
      return withState(st, {
        lines, state: anyCommitted ? State.BASKET_OPEN : State.IDLE,
      }, Reason.REVERTED);
    }

    case 'ACK': {
      if (st.state !== State.FROZEN_TOTAL) return refuse(st, Reason.DUPLICATE);
      return withState(st, {
        state: resumeTarget(st), frozenTotalPaise: null,
      }, Reason.HUMAN_ACKNOWLEDGED);
    }

    case 'DONE': {
      const g = billingGuard(st);
      if (g) return refuse(st, g);
      const committed = committedLines(st);
      if (committed.length === 0) return refuse(st, Reason.EMPTY_BASKET);
      const t = liveTotalPaise(st.lines);
      if (t === 0) return refuse(st, Reason.ZERO_TOTAL);   // all amber -> nothing to mint
      // DONE mints an INTENT. It authorises nothing. Chrome stays amber.
      return withState(st, {
        state: st.online ? State.AWAITING_SETTLEMENT : State.PENDING_OFFLINE,
        frozenTotalPaise: t,
        intentAmountPaise: t,
      }, st.online ? Reason.INTENT_REQUESTED : Reason.OFFLINE_NO_AUTHORISATION);
    }

    case 'NETWORK': {
      if (action.up) {
        if (st.online) return refuse(st, Reason.DUPLICATE);
        const next = st.state === State.PENDING_OFFLINE ? State.AWAITING_SETTLEMENT : st.state;
        return withState(st, { online: true, state: next }, Reason.NETWORK_RESTORED);
      }
      if (!st.online) return refuse(st, Reason.DUPLICATE);
      if (st.state === State.AWAITING_SETTLEMENT) {
        return withState(st, { online: false, state: State.PENDING_OFFLINE }, Reason.NETWORK_DOWN);
      }
      // Billing continues locally. Nothing is authorised while offline.
      return withState(st, { online: false }, Reason.NETWORK_DOWN_BILLING_CONTINUES);
    }

    case 'PERF': {
      const over = action.p95Ms > (action.thresholdMs ?? DEGRADED_P95_MS);
      if (over === st.degraded) return refuse(st, Reason.DUPLICATE);
      if (over) {
        const next = FROZEN_STATES.includes(st.state) || BASKET_CLOSED.includes(st.state)
          ? st.state : State.DEGRADED;
        return withState(st, { degraded: true, state: next }, Reason.DEGRADED);
      }
      const next = st.state === State.DEGRADED ? resumeTarget(st) : st.state;
      return withState(st, { degraded: false, state: next }, Reason.PERF_RECOVERED);
    }

    case 'WEBHOOK':
      return applyVerdict(st, action.verdict);

    default:
      return refuse(st, `unknown_action_${String(action.type)}`);
  }
}

/**
 * INVARIANT 2, client side. GREEN requires all four, checked in this order:
 *   1. the signature over RAW BYTES verified (by paisa, before any JSON parse)
 *   2. the event is in the green set
 *   3. notes.session_id matches THIS open intent
 *   4. amount == intent.amount_paise EXACTLY
 *
 * The client cannot GRANT green — paisa adjudicates and sets verdict.green. The
 * client can only VETO it. There is deliberately no path from a timer, a mint,
 * or a render to State.PAID.
 */
export function greenGate(st, v) {
  if (!v || v.signatureValid !== true) return { green: false, reason: Reason.BAD_SIGNATURE };
  if (!GREEN_EVENTS.includes(v.event)) return { green: false, reason: Reason.NOT_IN_GREEN_SET };
  if (v.sessionId !== st.sessionId) return { green: false, reason: Reason.FOREIGN_SESSION };
  if (st.intentAmountPaise === null || st.intentAmountPaise === undefined) {
    return { green: false, reason: Reason.NO_OPEN_INTENT };
  }
  if (!Number.isInteger(v.amountPaise) || v.amountPaise !== st.intentAmountPaise) {
    return { green: false, reason: Reason.AMOUNT_MISMATCH };
  }
  if (v.green !== true) return { green: false, reason: Reason.PAISA_REFUSED_GREEN };
  return { green: true, reason: Reason.SETTLED };
}

export function applyVerdict(st, v) {
  if (v && v.eventId && st.seenEventIds.includes(v.eventId)) return refuse(st, Reason.DUPLICATE);
  const seen = v && v.eventId
    ? Object.freeze([...st.seenEventIds, v.eventId]) : st.seenEventIds;
  if (st.state === State.PAID) return withState(st, { seenEventIds: seen }, Reason.ALREADY_SETTLED, false);

  const gate = greenGate(st, v);
  if (gate.green) {
    return withState(st, {
      state: State.PAID,
      authorisedPaise: paise(v.amountPaise),
      seenEventIds: seen,
    }, Reason.SETTLED);
  }
  // An amount mismatch on an otherwise valid, signed, in-set event for THIS
  // session is a contradiction, not staleness. That is the one red state.
  if (gate.reason === Reason.AMOUNT_MISMATCH) {
    return withState(st, { state: State.AMOUNT_MISMATCH, seenEventIds: seen }, Reason.AMOUNT_MISMATCH);
  }
  return withState(st, { seenEventIds: seen }, gate.reason, false);
}

// ===========================================================================
// Chrome. Colour follows session state, from the PRD state table.
// Invariant 7: staleness (mat lost, brain lost, offline, unknown) is AMBER or
// GREY. RED is reserved for AMOUNT_MISMATCH, the only genuine contradiction.
// ===========================================================================
const CHROME = Object.freeze({
  [State.SETUP]: { colour: 'grey', label: 'setup — mat not locked' },
  [State.IDLE]: { colour: 'amber', label: 'ready' },
  [State.MEASURING]: { colour: 'amber', label: 'measuring' },
  [State.PRICED]: { colour: 'white', label: 'priced' },
  [State.AMBER]: { colour: 'amber', label: 'unknown item — excluded from total' },
  [State.BASKET_OPEN]: { colour: 'amber', label: 'basket open' },
  [State.AWAITING_SETTLEMENT]: { colour: 'amber', label: 'awaiting settlement — nothing authorised' },
  [State.PENDING_OFFLINE]: { colour: 'amber', label: 'AMBER PENDING — offline, nothing authorised' },
  [State.PAID]: { colour: 'green', label: 'paid' },
  [State.AMOUNT_MISMATCH]: { colour: 'red', label: 'RED HOLD — amount mismatch, resolve by hand' },
  [State.MAT_LOST]: { colour: 'grey', label: 'mat lost — total frozen' },
  [State.BRAIN_LOST]: { colour: 'grey', label: 'brain lost — total frozen, events buffered' },
  [State.DEGRADED]: { colour: 'yellow', label: 'degraded — commit requires a tap' },
  [State.FROZEN_TOTAL]: { colour: 'amber', label: 'check the counter — total frozen' },
});

export function chromeFor(state) {
  return CHROME[state] ?? { colour: 'grey', label: `unknown state ${state}` };
}

// ===========================================================================
// Transport: reconnect backoff and the offline queue. Pure, so the reconnect
// policy is a tested function and not an accident of setTimeout.
// ===========================================================================
export const WS_BASE_MS = 250;
export const WS_CAP_MS = 8000;
export const QUEUE_CAP = 512;

/** Exponential backoff with full jitter in [0.5, 1.0] of the window. */
export function backoffMs(attempt, rnd = Math.random) {
  const a = Math.max(0, Math.trunc(attempt));
  const window = Math.min(WS_CAP_MS, WS_BASE_MS * 2 ** a);
  return Math.round(window * (0.5 + 0.5 * rnd()));
}

/** Bounded FIFO. Drops the OLDEST and says so, rather than growing without limit. */
export function enqueue(queue, item, cap = QUEUE_CAP) {
  const next = [...queue, item];
  if (next.length <= cap) return { queue: next, dropped: 0 };
  const drop = next.length - cap;
  return { queue: next.slice(drop), dropped: drop };
}

/**
 * 30fps gate for requestVideoFrameCallback, which fires at the display rate.
 *
 * The epsilon is load-bearing, not defensive padding. A 60Hz source delivers
 * frames 1000/60 ms apart, and 2 * (1000/60) === 33.33333333333333 while
 * 1000/30 === 33.333333333333336 — so the exact comparison is FALSE and the
 * gate silently drops to every third frame, i.e. 22fps not 30. Half a
 * millisecond absorbs that representation error and is still far below any
 * real inter-frame interval (a 120Hz source is 8.3ms apart).
 */
export const FPS_EPS_MS = 0.5;
export function shouldRenderFrame(lastMs, nowMs, targetFps = 30) {
  if (lastMs === null || lastMs === undefined) return true;
  return nowMs - lastMs >= 1000 / targetFps - FPS_EPS_MS;
}

/** Rolling p95 in ms, for the DEGRADED gate. Integer ms, nearest-rank. */
export function p95(samples) {
  if (samples.length === 0) return 0;
  const s = [...samples].sort((a, b) => a - b);
  const rank = Math.ceil(0.95 * s.length) - 1;
  return Math.round(s[Math.min(rank, s.length - 1)]);
}

// ===========================================================================
// CAMERA — preflight, failure classification, and which lens we actually got.
//
// INVARIANT 7 at the hardware boundary. getUserMedia has five common ways to
// fail and every one of them renders as the SAME black rectangle if you let it.
// A black rectangle is a guess: it silently implies "still loading". These
// functions turn each failure into a named, printable refusal, and they are
// pure so the mapping is proven by test rather than by trying it on a phone.
// ===========================================================================

export const CameraState = Object.freeze({
  IDLE: 'IDLE', STARTING: 'STARTING', LIVE: 'LIVE', FAILED: 'FAILED',
});

export const Facing = Object.freeze({ REAR: 'rear', FRONT: 'front', UNKNOWN: 'unknown' });

/** DOMException.name -> named reason. Legacy Chrome/Firefox aliases included. */
export const CAMERA_ERROR_REASONS = Object.freeze({
  NotAllowedError: Reason.CAMERA_DENIED,
  PermissionDeniedError: Reason.CAMERA_DENIED,
  SecurityError: Reason.CAMERA_INSECURE,
  NotFoundError: Reason.CAMERA_ABSENT,
  DevicesNotFoundError: Reason.CAMERA_ABSENT,
  NotReadableError: Reason.CAMERA_BUSY,
  TrackStartError: Reason.CAMERA_BUSY,
  SourceUnavailableError: Reason.CAMERA_BUSY,
  OverconstrainedError: Reason.CAMERA_OVERCONSTRAINED,
  ConstraintNotSatisfiedError: Reason.CAMERA_OVERCONSTRAINED,
  AbortError: Reason.CAMERA_ABORTED,
  TypeError: Reason.CAMERA_UNSUPPORTED,
});

/** The sentence a shopkeeper can act on, per reason. Never blank. */
export const CAMERA_HELP = Object.freeze({
  [Reason.CAMERA_DENIED]:
    'The browser blocked the camera. Open the site settings for this page, set Camera to Allow, then tap START again.',
  [Reason.CAMERA_ABSENT]:
    'This device reports no camera at all. GAWAAH cannot see the mat; nothing will be counted.',
  [Reason.CAMERA_BUSY]:
    'Another app is holding the camera. Close the other camera app or video call, then tap START again.',
  [Reason.CAMERA_INSECURE]:
    'The page is not on a secure origin, so the browser will not hand over a camera. Serve it over https, or over localhost.',
  [Reason.CAMERA_UNSUPPORTED]:
    'This browser does not expose getUserMedia. Use a current Chrome, Safari or Firefox.',
  [Reason.CAMERA_OVERCONSTRAINED]:
    'No camera on this device matches what was asked for. Retrying with any camera.',
  [Reason.CAMERA_ABORTED]:
    'The camera hardware aborted the stream. Tap START again; if it repeats the camera is failing.',
  [Reason.CAMERA_FAILED]:
    'The camera failed and the browser did not say why. Tap START to try again.',
  [Reason.CAMERA_IDLE]:
    'The camera has not been started. Tap START — the browser only hands over a camera after a real tap.',
});

/**
 * Refuse BEFORE calling getUserMedia when the environment cannot possibly
 * satisfy it. Returns null when a call is worth making.
 *
 * `isSecureContext === false` is checked for the literal false: `undefined`
 * means "this runtime does not tell us", which is not evidence of insecurity,
 * so we let the real call adjudicate rather than inventing a refusal.
 */
export function cameraPreflight(env) {
  const e = env || {};
  if (e.isSecureContext === false) {
    return { ok: false, reason: Reason.CAMERA_INSECURE, help: CAMERA_HELP[Reason.CAMERA_INSECURE] };
  }
  if (!e.hasMediaDevices || !e.hasGetUserMedia) {
    return { ok: false, reason: Reason.CAMERA_UNSUPPORTED, help: CAMERA_HELP[Reason.CAMERA_UNSUPPORTED] };
  }
  return null;
}

/**
 * Read the environment the shell is actually running in, without touching it.
 * Split out from cameraPreflight so the policy is testable with no globals.
 */
export function cameraEnv(win, nav) {
  const md = nav ? nav.mediaDevices : null;
  return {
    isSecureContext: win && 'isSecureContext' in win ? win.isSecureContext : undefined,
    hasMediaDevices: !!md,
    hasGetUserMedia: !!(md && typeof md.getUserMedia === 'function'),
  };
}

/** Classify a getUserMedia rejection into a named, printable refusal. */
export function classifyCameraError(err) {
  const name = err && typeof err.name === 'string' ? err.name : '';
  const detail = err && err.message ? String(err.message) : String(err);
  const reason = CAMERA_ERROR_REASONS[name] || Reason.CAMERA_FAILED;
  return { reason, name: name || 'UnnamedError', detail, help: CAMERA_HELP[reason] };
}

/**
 * Only two failures are worth retrying with a weaker constraint: the device
 * has no camera matching `facingMode: environment` (a laptop), or it refuses
 * the exact constraint. A denial, a busy camera or an insecure origin will
 * fail identically on the second try, so retrying them only wastes the
 * shopkeeper's time and hides the real reason behind a spinner.
 */
export function shouldRetryCamera(reason) {
  return reason === Reason.CAMERA_OVERCONSTRAINED || reason === Reason.CAMERA_ABSENT;
}

/**
 * The constraint ladder. Rear camera first, because the mat is on the counter
 * in front of the shopkeeper and behind the phone. Then any camera at all,
 * because a front camera that says so is worth infinitely more than a black
 * pane that does not.
 */
export function cameraFallbackPlan() {
  const size = { width: { ideal: 1280 }, height: { ideal: 960 } };
  return Object.freeze([
    Object.freeze({ kind: 'rear-exact', constraints: { video: { facingMode: { exact: 'environment' }, ...size }, audio: false } }),
    Object.freeze({ kind: 'rear-ideal', constraints: { video: { facingMode: { ideal: 'environment' }, ...size }, audio: false } }),
    Object.freeze({ kind: 'any', constraints: { video: true, audio: false } }),
  ]);
}

/**
 * Which lens did we actually get, and what does that mean for the mat?
 *
 * On a laptop this resolves to FRONT, and the honest consequence is that the
 * mat is behind the screen and will never lock. That is a correct outcome, not
 * a bug, and the note says so in words rather than leaving NO LOCK looking
 * broken. `settings.facingMode` is authoritative; the track label is a weak
 * second opinion and is only consulted when the browser says nothing.
 */
export function describeCamera(settings, label) {
  const s = settings || {};
  const lbl = typeof label === 'string' ? label : '';
  let facing = Facing.UNKNOWN, evidence = 'none';
  if (s.facingMode === 'environment') { facing = Facing.REAR; evidence = 'facingMode'; }
  else if (s.facingMode === 'user') { facing = Facing.FRONT; evidence = 'facingMode'; }
  else if (/\b(back|rear|environment|world)\b/i.test(lbl)) { facing = Facing.REAR; evidence = 'label'; }
  else if (/\b(front|face|user|facetime|selfie)\b/i.test(lbl)) { facing = Facing.FRONT; evidence = 'label'; }
  const w = Number.isFinite(s.width) ? s.width : null;
  const h = Number.isFinite(s.height) ? s.height : null;
  const size = w && h ? `${w}x${h}` : 'size unknown';
  if (facing === Facing.REAR) {
    return {
      facing, evidence, reason: Reason.CAMERA_REAR, width: w, height: h,
      note: `rear camera (${size}) — point it at the mat`,
      matLockExpected: true,
    };
  }
  if (facing === Facing.FRONT) {
    return {
      facing, evidence, reason: Reason.CAMERA_FRONT, width: w, height: h,
      note: `FRONT camera (${size}) — the mat is behind this screen, so it will NOT lock. `
        + 'That is correct, not broken: use a phone with a rear camera to count.',
      matLockExpected: false,
    };
  }
  return {
    facing, evidence, reason: Reason.CAMERA_FACING_UNKNOWN, width: w, height: h,
    note: `camera in use (${size}) — the browser did not say which lens. `
      + 'If the mat never locks, this is probably the front camera.',
    matLockExpected: null,
  };
}

// ===========================================================================
// PANEL ROUTER. Six containers, one visible. Pure so the routing decision is a
// tested function and not a pile of classList toggles.
// ===========================================================================
export const PANEL_IDS = Object.freeze(['core', 'mudra', 'peel', 'chilla', 'saaf', 'ledger']);
export const DEFAULT_PANEL = 'core';

/** DOM id of a panel container, e.g. 'mudra' -> 'panel-mudra'. */
export function panelElementId(id) { return `panel-${id}`; }

/** '#panel-mudra', '#mudra', 'mudra' -> 'mudra'. Anything else -> null. */
export function panelIdFromHash(hash) {
  if (typeof hash !== 'string') return null;
  const raw = hash.replace(/^#/, '').replace(/^panel-/, '').trim().toLowerCase();
  return PANEL_IDS.includes(raw) ? raw : null;
}

export function initialPanelState(current = DEFAULT_PANEL) {
  const id = PANEL_IDS.includes(current) ? current : DEFAULT_PANEL;
  return Object.freeze({
    current: id, previous: null, reason: Reason.PANEL_SHOWN,
    applied: true, visited: Object.freeze([id]),
  });
}

/**
 * THE ROUTER. Pure: (state, id) -> new state. Never throws, never mutates.
 * An unknown id is an ABSTENTION — the previously shown panel stays shown and
 * the refusal is named — because silently falling back to CORE would hide a
 * typo in a panel agent's link forever.
 */
export function selectPanel(state, id) {
  const st = state && typeof state === 'object' ? state : initialPanelState();
  if (typeof id !== 'string' || !PANEL_IDS.includes(id)) {
    return Object.freeze({ ...st, reason: `${Reason.PANEL_UNKNOWN}:${String(id)}`, applied: false });
  }
  if (id === st.current) {
    return Object.freeze({ ...st, reason: Reason.PANEL_SAME, applied: false });
  }
  const visited = st.visited && Array.isArray(st.visited) ? st.visited : [];
  return Object.freeze({
    current: id,
    previous: st.current,
    reason: Reason.PANEL_SHOWN,
    applied: true,
    visited: Object.freeze([...new Set([...visited, id])]),
  });
}

/** Visibility map the shell applies: {'panel-core': false, 'panel-mudra': true, ...}. */
export function panelVisibility(panelState) {
  const cur = panelState && panelState.current ? panelState.current : DEFAULT_PANEL;
  return Object.freeze(Object.fromEntries(
    PANEL_IDS.map((id) => [panelElementId(id), id !== cur])));   // value is `hidden`
}

/** The shell's CSS tab router is a radio group; this is its input id. */
export function panelTabId(id) { return `tabsel-${id}`; }

/** 'tabsel-mudra' -> 'mudra'. Anything that is not a known tab -> null. */
export function panelIdFromTabId(tabId) {
  if (typeof tabId !== 'string') return null;
  const raw = tabId.replace(/^tabsel-/, '').trim().toLowerCase();
  return PANEL_IDS.includes(raw) ? raw : null;
}

/**
 * Which radio must be checked, given the router state.
 *
 * The shell shows panels with pure CSS — `.shell:has(#tabsel-X:checked)
 * #panel-X { display: block }` — so a tab keeps working before this module
 * loads and cannot be broken by a module that throws. The consequence is that
 * the router MUST move the radio, not the panel: setting `hidden` on a
 * `.panel` fights an author `display:block` rule and leaves the stage looking
 * blank while the router privately believes it switched. This map is the one
 * place that decision is made, so it can be tested without a DOM.
 */
export function panelTabSelection(panelState) {
  const cur = panelState && panelState.current ? panelState.current : DEFAULT_PANEL;
  return Object.freeze(Object.fromEntries(PANEL_IDS.map((id) => [panelTabId(id), id === cur])));
}

// ---------------------------------------------------------------------------
// PANEL STATUS — the shell contract's third attribute, #panel-X[data-status].
// OFF / ABSTAIN / OK, and deliberately NO GREEN: green belongs to a settled
// session and comes only from a signature-verified webhook (invariant 2).
// ---------------------------------------------------------------------------
export const PanelStatus = Object.freeze({ OFF: 'OFF', ABSTAIN: 'ABSTAIN', OK: 'OK' });
const PANEL_STATUSES = Object.freeze([PanelStatus.OFF, PanelStatus.ABSTAIN, PanelStatus.OK]);

/**
 * CORE's own status. CORE is the billing loop, so it "knows" exactly when it
 * has a mat lock and not one moment earlier. Every other outcome is an
 * ABSTENTION carrying the reason that actually caused it — a camera that was
 * never started, a camera that refused and why, an absent OpenCV, or the
 * lock's own refusal — rather than the markup's static `mat_not_locked`, which
 * is a guess whenever the true cause is upstream of the mat.
 *
 * There is no input for which this returns OK without a lock. That is the
 * whole point: OK on this panel means "billing is trustworthy right now".
 */
export function corePanelStatus(view) {
  const v = view || {};
  const cam = v.camera || {};
  const lock = v.lock || {};
  // Written as "OK requires LIVE", not "abstain on the three failures I can
  // name". The second form falls THROUGH on any state it has not heard of —
  // so a future CameraState, or a camera object that was never initialised,
  // would be read as a working camera. Unknown is not evidence of working.
  if (cam.state !== CameraState.LIVE) {
    if (cam.state === CameraState.IDLE) return { status: PanelStatus.ABSTAIN, why: Reason.CAMERA_IDLE };
    if (cam.state === CameraState.STARTING) return { status: PanelStatus.ABSTAIN, why: Reason.CAMERA_STARTING };
    if (cam.state === CameraState.FAILED) {
      return { status: PanelStatus.ABSTAIN, why: cam.reason || Reason.CAMERA_FAILED };
    }
    return { status: PanelStatus.ABSTAIN, why: `${Reason.CAMERA_STATE_UNKNOWN}:${String(cam.state)}` };
  }
  if (v.cvReason) return { status: PanelStatus.ABSTAIN, why: v.cvReason };
  if (lock.locked !== true) {
    return { status: PanelStatus.ABSTAIN, why: lock.reason || Reason.MAT_NOT_LOCKED };
  }
  return { status: PanelStatus.OK, why: null };
}

/**
 * A capability panel's status, from two facts this file can actually check:
 * whether a module registered for that id, and what that module last declared.
 *
 * Nothing attached          -> OFF, and `why` is null so the shell's own honest
 *                              placeholder text is left alone rather than
 *                              overwritten with a worse sentence.
 * Attached, said nothing    -> ABSTAIN. A module that is running and has not
 *                              spoken does not know; that is not OFF.
 * Attached, declared        -> what it declared, provided the status is one of
 *                              the three legal ones. A panel that tries to
 *                              declare GREEN — or any invented status — is
 *                              REFUSED down to ABSTAIN with a named reason,
 *                              because no capability may ever paint green.
 */
export function panelStatusFor(id, registered, declared) {
  if (!registered) return { status: PanelStatus.OFF, why: null };
  const d = declared && typeof declared === 'object' ? declared : null;
  if (!d || d.status === undefined || d.status === null) {
    return { status: PanelStatus.ABSTAIN, why: Reason.PANEL_NO_DATA };
  }
  const s = String(d.status).toUpperCase();
  if (s === 'GREEN' || s === 'PAID') {
    return { status: PanelStatus.ABSTAIN, why: `${Reason.PANEL_NEVER_GREEN}:${id}` };
  }
  if (!PANEL_STATUSES.includes(s)) {
    return { status: PanelStatus.ABSTAIN, why: `${Reason.PANEL_BAD_STATUS}:${String(d.status)}` };
  }
  if (s === PanelStatus.OK) {
    return { status: PanelStatus.OK, why: typeof d.why === 'string' ? d.why : null };
  }
  const why = typeof d.why === 'string' && d.why !== '' ? d.why : Reason.PANEL_NO_DATA;
  return { status: s, why };
}

/**
 * The sentence printed into #camreason. Never blank, in any state — a camera
 * gate with an empty reason line is the black rectangle all over again.
 */
export function cameraReasonLine(cam) {
  const c = cam || {};
  if (c.state === CameraState.LIVE) {
    return `${Reason.CAMERA_LIVE}: ${c.note || c.reason || Reason.CAMERA_FACING_UNKNOWN}`;
  }
  if (c.state === CameraState.STARTING) return Reason.CAMERA_STARTING;
  if (c.state === CameraState.IDLE) return `${Reason.CAMERA_IDLE} · ${CAMERA_HELP[Reason.CAMERA_IDLE]}`;
  const reason = c.reason || Reason.CAMERA_FAILED;
  const help = c.help || CAMERA_HELP[reason] || '';
  return help ? `${reason} · ${help}` : reason;
}

/**
 * The camera gate is one attribute on #camgate, per the shell contract in
 * index.html: IDLE | REQUESTING | LIVE | DENIED | ABSENT | INSECURE | ERROR.
 * Pure, so the mapping from our reason codes onto the shell's seven cases is a
 * tested table and not a chain of ifs that drifts from the markup.
 */
export const CAMERA_GATE_CODES = Object.freeze({
  [Reason.CAMERA_IDLE]: 'IDLE',
  [Reason.CAMERA_STARTING]: 'REQUESTING',
  [Reason.CAMERA_LIVE]: 'LIVE',
  [Reason.CAMERA_REAR]: 'LIVE',
  [Reason.CAMERA_FRONT]: 'LIVE',
  [Reason.CAMERA_FACING_UNKNOWN]: 'LIVE',
  [Reason.CAMERA_DENIED]: 'DENIED',
  [Reason.CAMERA_ABSENT]: 'ABSENT',
  [Reason.CAMERA_INSECURE]: 'INSECURE',
  [Reason.CAMERA_BUSY]: 'ERROR',
  [Reason.CAMERA_UNSUPPORTED]: 'ERROR',
  [Reason.CAMERA_OVERCONSTRAINED]: 'ERROR',
  [Reason.CAMERA_ABORTED]: 'ERROR',
  [Reason.CAMERA_FAILED]: 'ERROR',
});

export function cameraGateCode(cam) {
  const c = cam || {};
  if (c.state === CameraState.LIVE) return 'LIVE';
  if (c.state === CameraState.STARTING) return 'REQUESTING';
  if (c.state === CameraState.IDLE) return 'IDLE';
  // FAILED, and anything the table has not heard of, is ERROR rather than a
  // guess — the precise cause is printed in #camreason beside it.
  return CAMERA_GATE_CODES[c.reason] || 'ERROR';
}

// ===========================================================================
// THE PANEL API — the contract other agents build against.
// ===========================================================================
/**
 * registerPanel(id, { onState, onFrame }) -> { ok, reason, ... }
 *
 * WHY IT EXISTS: web/app.js is owned by one agent, and the MUDRA / PEEL /
 * CHILLA / SAAF / LEDGER panels are owned by others. This is the seam. A panel
 * module does:
 *
 *     import { registerPanel, RETAIN_RECTIFIED } from '../app.js';
 *     registerPanel('mudra', {
 *       onState(view) { ... },      // called on every counter state change
 *       onFrame(frame) { ... },     // called once per rendered frame, LOCKED only
 *     });
 *
 * or, from a plain script, `window.GAWAAH.registerPanel(...)` — the same
 * function, exposed on the global for panels that are not ES modules.
 *
 * THE THIRD CALL — saying what you know:
 *
 *     import { setPanelStatus, PanelStatus } from '../app.js';
 *     setPanelStatus('mudra', PanelStatus.OK, null);            // I have a reading
 *     setPanelStatus('mudra', PanelStatus.ABSTAIN, 'mudra_ambiguous_shape');
 *
 * That one call paints all three places the shell contract names: the panel's
 * status pill (#panel-mudra[data-status]), the rail dot, and the "I DO NOT
 * KNOW" block (#abstain-mudra is hidden on OK, and #why-mudra carries the
 * reason on ABSTAIN). A panel that never calls it stays ABSTAIN once it has
 * registered, and OFF until then — both of which are true statements.
 *
 * Statuses are OFF, ABSTAIN and OK. There is no GREEN and a panel that tries
 * to declare one is refused down to ABSTAIN with a named reason: green is a
 * settled session, and only a signature-verified webhook produces one.
 *
 * CORE is not declarable. Its status is DERIVED from the mat lock, because
 * nothing should be able to announce that the billing loop is trustworthy
 * except the geometry that makes it trustworthy.
 *
 * `id` MUST be one of PANEL_IDS. An unknown id is refused with a named reason
 * rather than silently accepted, so a typo surfaces at load instead of never.
 * Re-registering the same id REPLACES the hooks and says so (hot reload).
 * Both hooks are optional; anything present must be a function.
 *
 * onState(view) receives a frozen, read-only view:
 *   { state, reason, lines, totalPaise, amberCount, matLocked, online,
 *     lock, camera, conn, panel, visible }
 * `visible` is true only for the panel currently routed to. `lines` and `lock`
 * are copies: a panel cannot reach back and mutate the counter.
 *
 * onFrame(frame) receives ONLY the rectified mat crop — INVARIANT 4:
 *   { cropKind: 'rectified_mat_crop', crop: <HTMLCanvasElement 840x1188>,
 *     width: 840, height: 1188, ts, seq, lock }
 * There is no path by which a panel is handed the raw camera frame. `crop` is
 * the same 840x1188 canvas the split preview shows on the right; every payload
 * is run through assertRectifiedOnly() before dispatch, so a future edit that
 * tries to add `raw` throws instead of leaking. onFrame is NOT called when the
 * mat is unlocked, because there is no crop to hand over.
 *
 * INVARIANT 2: a panel is an OBSERVER. There is no hook by which a panel can
 * change money, commit a line, or set the chrome colour. MUDRA reveals a
 * target, PEEL warns, CHILLA corroborates, SAAF selects frames — none of them
 * decide, and none of them can turn the counter green.
 *
 * A hook that THROWS is caught, counted, and named on that panel's own status
 * line. It never unwinds into the frame loop, because a broken panel must not
 * be able to stop the counter from counting.
 */
export function makePanelRegistry() {
  const entries = new Map();
  const declared = new Map();
  const faults = [];
  let watcher = null;

  function register(id, hooks) {
    if (typeof id !== 'string' || !PANEL_IDS.includes(id)) {
      return { ok: false, reason: `${Reason.PANEL_UNKNOWN}:${String(id)}` };
    }
    const h = hooks && typeof hooks === 'object' ? hooks : {};
    for (const k of ['onState', 'onFrame']) {
      if (h[k] !== undefined && h[k] !== null && typeof h[k] !== 'function') {
        return { ok: false, reason: `${Reason.PANEL_BAD_HOOKS}:${id}.${k}` };
      }
    }
    const replaced = entries.has(id);
    entries.set(id, {
      id,
      onState: typeof h.onState === 'function' ? h.onState : null,
      onFrame: typeof h.onFrame === 'function' ? h.onFrame : null,
      stateCalls: 0, frameCalls: 0, errors: 0, lastError: null,
    });
    return { ok: true, id, replaced, reason: replaced ? Reason.PANEL_REPLACED : Reason.PANEL_REGISTERED };
  }

  function call(entry, hook, arg) {
    const fn = entry[hook];
    if (!fn) return;
    try {
      fn(arg);
      if (hook === 'onState') entry.stateCalls++; else entry.frameCalls++;
    } catch (e) {
      entry.errors++;
      entry.lastError = `${Reason.PANEL_HOOK_THREW}:${entry.id}.${hook}: ${(e && e.message) || e}`;
      faults.push(entry.lastError);
    }
  }

  /**
   * A panel declaring its own status. This is how MUDRA/PEEL/CHILLA/SAAF/LEDGER
   * paint their rail dot and their "I DO NOT KNOW" block without ever editing
   * app.js. The declaration is ADJUDICATED by panelStatusFor(), not trusted: a
   * panel cannot declare GREEN and cannot invent a fourth status.
   */
  function declare(id, status, why) {
    if (typeof id !== 'string' || !PANEL_IDS.includes(id)) {
      return { ok: false, reason: `${Reason.PANEL_UNKNOWN}:${String(id)}` };
    }
    if (id === DEFAULT_PANEL) {
      // CORE's status is derived from the mat lock, not declared. Nothing may
      // announce that the billing loop is fine; only a lock can show that.
      return { ok: false, reason: `${Reason.PANEL_BAD_STATUS}:core_is_derived_from_the_lock` };
    }
    const verdict = panelStatusFor(id, true, { status, why });
    declared.set(id, { status, why });
    if (watcher) {
      // A watcher that throws must not unwind into the declaring panel: a
      // broken repaint is not a reason to break the module that spoke.
      try { watcher(id, verdict); }
      catch (e) { faults.push(`${Reason.PANEL_HOOK_THREW}:watch: ${(e && e.message) || e}`); }
    }
    return { ok: true, id, ...verdict };
  }

  return {
    register,
    declare,
    /** The shell subscribes here to repaint a dot the moment a panel speaks. */
    watch(fn) { watcher = typeof fn === 'function' ? fn : null; },
    declaredFor(id) { return declared.get(id) ?? null; },
    /** The adjudicated status of every panel, for the shell to apply. */
    statuses() {
      return Object.fromEntries(PANEL_IDS.filter((id) => id !== DEFAULT_PANEL)
        .map((id) => [id, panelStatusFor(id, entries.has(id), declared.get(id))]));
    },
    unregister(id) { declared.delete(id); return entries.delete(id); },
    has(id) { return entries.has(id); },
    ids() { return [...entries.keys()]; },
    get(id) { return entries.get(id) || null; },
    faults,
    /** Fan a read-only counter view out to every panel. Never throws. */
    emitState(view) { for (const e of entries.values()) call(e, 'onState', view); return entries.size; },
    /**
     * Fan the RECTIFIED CROP out to every panel. Asserts invariant 4 on the way
     * through — a payload that is not the rectified crop throws HERE, before any
     * panel sees it, rather than being quietly forwarded.
     */
    emitFrame(frame) {
      assertRectifiedFrame(frame);
      for (const e of entries.values()) call(e, 'onFrame', frame);
      return entries.size;
    },
  };
}

const PANEL_REGISTRY = makePanelRegistry();
/** The public seam. See the comment block on makePanelRegistry above. */
export function registerPanel(id, hooks) { return PANEL_REGISTRY.register(id, hooks); }
/**
 * The second half of the seam: a panel telling the shell what it currently
 * knows. Paints #panel-X[data-status], the rail dot and #abstain-X / #why-X.
 * Legal statuses are OFF, ABSTAIN and OK. GREEN is refused — see PanelStatus.
 */
export function setPanelStatus(id, status, why) { return PANEL_REGISTRY.declare(id, status, why); }
/** Escape hatch for tests and for the shell; panels should use registerPanel. */
export function panelRegistry() { return PANEL_REGISTRY; }

/**
 * THE LOAD-ORDER SEAM.
 *
 * A panel module cannot know whether it is evaluated before or after app.js —
 * both are deferred ES modules and the shell decides the order. So each one
 * publishes a descriptor `{ id, title, attach(register), attached }` onto
 * `globalThis.GAWAAH_PANELS` and attaches itself only if it already found a
 * `registerPanel`. Whoever arrives second is responsible for draining the
 * queue; when app.js is second, this is that drain.
 *
 * `attached` is honoured, so a panel that already attached directly is not
 * registered twice. A descriptor whose attach() THROWS is refused by name and
 * the drain continues: one panel module that fails to construct must not stop
 * the other four from attaching, and must not stop the counter from booting.
 * Never throws.
 */
export function drainPanelQueue(queue, register) {
  const out = { attached: [], skipped: [], refused: [] };
  if (!Array.isArray(queue) || typeof register !== 'function') return out;
  for (const d of queue) {
    if (!d || typeof d !== 'object') {
      out.refused.push({ id: null, message: `not_a_descriptor:${String(d)}` });
      continue;
    }
    const id = typeof d.id === 'string' ? d.id : '(unnamed)';
    if (d.attached === true) { out.skipped.push(id); continue; }
    if (typeof d.attach !== 'function') {
      out.refused.push({ id, message: 'descriptor_has_no_attach' });
      continue;
    }
    try { d.attach(register); d.attached = true; out.attached.push(id); }
    catch (e) { out.refused.push({ id, message: (e && e.message) ? e.message : String(e) }); }
  }
  return out;
}

/**
 * INVARIANT 4, asserted in code rather than in prose. Everything that reaches a
 * panel or the wire must be the rectified 840x1188 mat crop and must say so.
 */
export function assertRectifiedFrame(frame) {
  if (frame === null || typeof frame !== 'object') {
    throw new Error('invariant 4 violated: frame payload is not an object');
  }
  assertRectifiedOnly(frame);
  if (frame.cropKind !== RETAIN_RECTIFIED) {
    throw new Error(`invariant 4 violated: cropKind is '${String(frame.cropKind)}', not '${RETAIN_RECTIFIED}'`);
  }
  if (frame.width !== BUF_W || frame.height !== BUF_H) {
    throw new Error(`invariant 4 violated: crop is ${frame.width}x${frame.height}, not ${BUF_W}x${BUF_H}`);
  }
  return frame;
}

/**
 * Build the frame payload, or refuse. Abstains (send:false, named reason)
 * whenever the mat is not locked, because with no lock there IS no rectified
 * crop and the only honest thing to send is nothing.
 */
export function frameEgress(lock, crop, meta) {
  const policy = frameGrabPolicy(lock);
  if (policy.retain !== RETAIN_RECTIFIED) {
    return { send: false, reason: policy.reason, payload: null };
  }
  const m = meta || {};
  const payload = {
    type: 'frame',
    cropKind: RETAIN_RECTIFIED,
    crop,
    width: BUF_W,
    height: BUF_H,
    ts: m.ts ?? null,
    seq: m.seq ?? null,
    lock: { scaleErr: lock.scaleErr, perspIndex: lock.perspIndex, reprojRmsePx: lock.reprojRmsePx },
  };
  return { send: true, reason: policy.reason, payload: assertRectifiedFrame(payload) };
}

/**
 * How often a frame may go to the brain. The frame loop runs at 30fps; the
 * brain does not need 30 encoded crops a second and a phone cannot afford to
 * make them. This is a policy, not a magic number in a closure.
 */
export const BRAIN_FRAME_EVERY_MS = 500;

/**
 * May a frame go over the wire right now?
 *
 * OFFLINE SENDS NOTHING. Not "queues the frame" — nothing. The outbox exists
 * for billing events, which the brain must eventually see; a stale crop from
 * forty seconds ago is worse than no crop, and filling a bounded queue with
 * images would evict the events that actually matter. Billing continues
 * locally either way, and either way nothing is authorised.
 */
export function shouldSendFrameToBrain(lastSentMs, nowMs, conn, everyMs = BRAIN_FRAME_EVERY_MS) {
  if (!connIsUp(conn)) return false;
  if (lastSentMs === null || lastSentMs === undefined) return true;
  if (!Number.isFinite(nowMs)) return false;
  return nowMs - lastSentMs >= everyMs;
}

/** The default encoder: the rectified canvas, and there is no other input. */
function encodeRectified(canvas) {
  return canvas && typeof canvas.toDataURL === 'function'
    ? canvas.toDataURL('image/jpeg', 0.6) : null;
}

/**
 * INVARIANT 4 ON THE WIRE. Turn a frameEgress payload into the JSON message the
 * brain receives, and prove on the way that it is the rectified mat crop and
 * nothing else.
 *
 * The encoded string is built from `payload.crop` — the 840x1188 rectified
 * canvas — and from no other source. There is deliberately no parameter, no
 * option and no branch by which the raw camera canvas or the <video> could be
 * encoded instead: this function cannot reach them. assertRectifiedFrame runs
 * on the way in and assertRectifiedOnly on the way out, so a future edit that
 * adds a raw buffer throws here rather than shipping it.
 *
 * Refuses (send:false, named reason) rather than sending a placeholder when
 * the crop cannot be encoded.
 */
export function frameWirePayload(payload, encode = encodeRectified) {
  assertRectifiedFrame(payload);
  if (typeof encode !== 'function') {
    return { send: false, reason: Reason.WIRE_NO_ENCODER, msg: null };
  }
  let cropPng = null;
  try { cropPng = encode(payload.crop); }
  catch { return { send: false, reason: Reason.WIRE_ENCODE_FAILED, msg: null }; }
  if (typeof cropPng !== 'string' || cropPng === '') {
    return { send: false, reason: Reason.WIRE_ENCODE_FAILED, msg: null };
  }
  const msg = {
    type: 'frame',
    cropKind: RETAIN_RECTIFIED,
    cropPng,
    width: payload.width,
    height: payload.height,
    ts: payload.ts,
    seq: payload.seq,
    lock: payload.lock,
  };
  return { send: true, reason: RETAIN_RECTIFIED, msg: assertRectifiedOnly(msg) };
}

// ===========================================================================
// BRAIN BRIDGE — connection state as a pure reducer.
// CONNECTING -> OPEN, or CONNECTING -> RETRYING -> ... -> OFFLINE.
// OFFLINE does not stop the retries; it is the moment we stop implying that
// the brain is about to answer and start saying PENDING_OFFLINE out loud.
// ===========================================================================
export const Conn = Object.freeze({
  CONNECTING: 'CONNECTING', OPEN: 'OPEN', RETRYING: 'RETRYING', OFFLINE: 'OFFLINE',
});
/** Retries before the banner stops saying "reconnecting" and says OFFLINE. */
export const WS_OFFLINE_AFTER_ATTEMPTS = 3;

export function initialConnState() {
  return Object.freeze({
    status: Conn.CONNECTING, attempt: 0, nextDelayMs: 0,
    reason: Reason.WS_CONNECTING, applied: true, opens: 0, closes: 0,
  });
}

/**
 * Pure connection reducer. Actions:
 *   {type:'CONNECT'}                  a socket is being opened
 *   {type:'OPEN'}                     the socket opened
 *   {type:'CLOSE', delayMs?, rnd?}    the socket closed; schedule a retry
 *   {type:'NET_DOWN'}                 the OS says there is no network
 *   {type:'NET_UP'}                   the OS says the network is back
 */
export function reduceConn(state, action) {
  const st = state && typeof state === 'object' ? state : initialConnState();
  const a = action && typeof action === 'object' ? action : { type: '' };
  const F = (patch, reason, applied = true) => Object.freeze({ ...st, ...patch, reason, applied });
  switch (a.type) {
    case 'CONNECT':
      return F({ status: Conn.CONNECTING, nextDelayMs: 0 }, Reason.WS_CONNECTING);
    case 'OPEN':
      return F({ status: Conn.OPEN, attempt: 0, nextDelayMs: 0, opens: st.opens + 1 }, Reason.WS_OPEN);
    case 'CLOSE': {
      const attempt = st.attempt + 1;
      const delay = Number.isFinite(a.delayMs) ? a.delayMs : backoffMs(attempt - 1, a.rnd || Math.random);
      const offline = attempt >= WS_OFFLINE_AFTER_ATTEMPTS;
      return F(
        { status: offline ? Conn.OFFLINE : Conn.RETRYING, attempt, nextDelayMs: delay, closes: st.closes + 1 },
        offline ? Reason.WS_OFFLINE : Reason.WS_RETRYING,
      );
    }
    case 'NET_DOWN':
      return F({ status: Conn.OFFLINE, nextDelayMs: 0 }, Reason.WS_OFFLINE);
    case 'NET_UP':
      return st.status === Conn.OPEN
        ? F({}, Reason.WS_OPEN, false)
        : F({ status: Conn.CONNECTING, attempt: 0, nextDelayMs: 0 }, Reason.WS_CONNECTING);
    default:
      return F({}, `unknown_conn_action_${String(a.type)}`, false);
  }
}

/** True only for OPEN. Everything else authorises nothing. */
export function connIsUp(cs) { return !!cs && cs.status === Conn.OPEN; }

/**
 * The banner sentence. Every branch carries "AMBER PENDING" and "nothing
 * authorised", because that is true in every branch: the brain is not what
 * turns the counter green, so its absence can never be reported as an outage
 * that merely delays a payment.
 */
export function bannerText(cs, queued = 0) {
  const q = queued > 0 ? `${queued} queued, ` : '';
  const status = cs && cs.status ? cs.status : Conn.OFFLINE;
  switch (status) {
    case Conn.CONNECTING:
      return `AMBER PENDING — connecting to the brain, ${q}nothing authorised`;
    case Conn.RETRYING:
      return `AMBER PENDING — brain lost, retry ${cs.attempt} in ${cs.nextDelayMs}ms, ${q}nothing authorised`;
    case Conn.OFFLINE:
      return `AMBER PENDING — PENDING_OFFLINE after ${cs.attempt || 0} attempts, billing continues locally, ${q}nothing authorised`;
    default:
      return `AMBER PENDING — offline, ${q}nothing authorised`;
  }
}

// ===========================================================================
// Browser shell. Everything below touches the DOM and never runs under node.
// ===========================================================================
export const OPENCV_PATH = './vendor/opencv.js';
export const WS_PORT = 8787;
/**
 * The brain socket, derived from the page's OWN origin.
 *
 * This was hardcoded to ws://localhost:8787, which broke two real cases and
 * tempted a bad fix. Opening the page on the loopback IP rather than the name
 * localhost is a DIFFERENT origin to the browser, so the socket was blocked by
 * connect-src; and a phone reaching the brain over the LAN by IP never says
 * localhost at all.
 *
 * The tempting fix was a wildcard host in connect-src. Selftest 13 rejected
 * that, correctly: a wildcard would let the client reach ANY host, which
 * is precisely the guarantee that keeps a model weight off this page
 * (INVARIANT 3). Deriving from location.host keeps CSP at 'self' and makes the
 * socket work on localhost, on 127.0.0.1 and on a phone over the LAN, with no
 * external origin permitted anywhere.
 */
export const WS_URL = (typeof location !== 'undefined' && location.host)
  ? `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}`
  : 'ws://localhost:8787';

/**
 * The brain address. The counter talks to exactly one origin: the machine that
 * served this page, on the brain's port. Built from parts so that no other
 * absolute address exists anywhere in this file — grep the source and the only
 * literal URL you will find is WS_URL above.
 */
export function brainUrl(loc) {
  const host = loc && typeof loc.hostname === 'string' && loc.hostname !== '' ? loc.hostname : 'localhost';
  const scheme = loc && loc.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${scheme}//${host}:${WS_PORT}`;
}

/**
 * Load the vendored OpenCV. Never downloads: if ./vendor/opencv.js is missing,
 * we abstain with OPENCV_ABSENT and the app stays in SETUP with the reason on
 * screen. It does not fall back to a CDN and it does not pretend to lock.
 */
function loadOpenCV(path = OPENCV_PATH) {
  return new Promise((resolve) => {
    const s = document.createElement('script');
    s.src = path;
    s.async = true;
    s.onerror = () => resolve({ ok: false, reason: Reason.OPENCV_ABSENT });
    s.onload = () => {
      const loaded = globalThis.cv;
      if (!loaded) return resolve({ ok: false, reason: Reason.OPENCV_ABSENT });

      const ready = (cv) => (cv && (cv.aruco_ArucoDetector || cv.ArucoDetector)
        ? resolve({ ok: true, cv })
        : resolve({ ok: false, reason: 'opencv_build_lacks_aruco' }));

      // @techstark/opencv-js@4.11 resolves as a PROMISE, not a ready module.
      // Measured in web/cvprobe.html: onload fires at 0.11s with
      // typeof cv === 'object', getBuildInformation undefined, and cv.then a
      // function; awaiting it yields the real module at 1.00s.
      //
      // The previous code checked getBuildInformation, found it undefined, and
      // fell through to `cv.onRuntimeInitialized = ready` -- a callback a
      // Promise never invokes. So the load hung forever on "loading vendored
      // OpenCV…", geometry stayed unavailable, and the mat could never lock.
      // A hang is worse than a failure here: OPENCV_ABSENT is at least an
      // honest state the UI can show, and this reached neither.
      if (typeof loaded.then === 'function') {
        loaded.then((mod) => { globalThis.cv = mod; ready(mod); },
                    () => resolve({ ok: false, reason: Reason.OPENCV_ABSENT }));
        return;
      }
      if (loaded.getBuildInformation) return ready(loaded);
      loaded.onRuntimeInitialized = () => ready(globalThis.cv);
    };
    document.head.appendChild(s);
  });
}

function boot() {
  const $ = (id) => document.getElementById(id);
  const els = {
    video: $('cam'), raw: $('raw'), rect: $('rect'), chrome: $('chrome'),
    total: $('total'), amber: $('amber'), lock: $('lock'), lockDetail: $('lockdetail'),
    lines: $('lines'), done: $('done'), ack: $('ack'), banner: $('banner'),
    reason: $('reason'), fps: $('fps'), cvstat: $('cvstat'),
    // Added by the shell agent. Every one of these is OPTIONAL: this file must
    // boot against an index.html that does not have them yet, so each use is
    // guarded. A missing #start means "start the camera immediately", which is
    // the pre-gesture behaviour and keeps the old shell working.
    start: $('start'), camgate: $('camgate'), camreason: $('camreason'),
    // #camstat and #conn are not in the current shell; the camera gate and the
    // banner carry that text now. They stay wired, guarded, for a shell that
    // offers them. There is no #panelnav: the rail IS the radio group.
    camstat: $('camstat'), conn: $('conn'),
  };
  // Set once OpenCV settles; feeds CORE's abstention reason so a panel that
  // cannot lock because the geometry never loaded says THAT, not 'no markers'.
  let cvReason = null;

  let st = initialState(`sess-${Date.now().toString(36)}`);
  let cv = null, detector = null, lock = { locked: false, reason: 'booting' };
  let outbox = [];
  let ws = null, wsTimer = null;
  let conn = initialConnState();
  let panel = initialPanelState();
  let camera = Object.freeze({
    state: CameraState.IDLE, reason: Reason.CAMERA_IDLE,
    help: CAMERA_HELP[Reason.CAMERA_IDLE], facing: Facing.UNKNOWN, note: '', kind: null,
  });
  let frameSeq = 0;
  let lastFrameMs = null, lastBrainFrameMs = null, frameTimes = [], fpsWindow = [];
  const registry = panelRegistry();
  const panelAttachFaults = new Map();   // id -> why this capability never loaded
  const glyphs = [];   // {itemId, quad} in frame px, rebuilt every render

  function setReason(text) { if (els.reason) els.reason.textContent = text; }

  // ---- state plumbing ----------------------------------------------------
  function dispatch(action) {
    const prev = st;
    st = reduce(st, action);
    if (st.lastApplied === false && prev.lastReason !== st.lastReason) {
      setReason(`refused: ${st.lastReason}`);
    } else if (st.lastApplied) {
      setReason(st.lastReason);
    }
    render();
  }

  function render() {
    const c = chromeFor(st.state);
    els.chrome.className = `chrome chrome-${c.colour}`;
    els.chrome.dataset.state = st.state;
    els.chrome.dataset.conn = conn.status;
    els.chrome.dataset.camera = camera.state;
    els.chrome.dataset.panel = panel.current;
    // WHOSE TOTAL IS ON SCREEN.
    //
    // The reducer owns the total when THIS page is doing the counting -- a
    // camera is running and placements are arriving locally. When the BRAIN is
    // driving instead (the scripted run, where the frames are synthetic and
    // this page never saw them), the reducer has nothing to count and would
    // show Rs.0.00 while the counter genuinely holds Rs.139.50. That is not an
    // honest blank: it is the screen failing to show the counter's own truth,
    // and the disagreement line at the bottom was reporting exactly that.
    //
    // So: render whichever side is actually counting, and SAY WHICH. This is
    // not the dual-writer problem -- there is still exactly one writer. The
    // brain writes, the page renders. What changed is that the page stopped
    // insisting on its own empty number.
    const localPaise = totalPaise(st);
    const brainPaise = brainView && typeof brainView.total_paise === 'number'
      ? brainView.total_paise : null;
    const brainDriving = brainPaise !== null && localPaise === 0 && brainPaise > 0;

    els.total.textContent = formatRupees(brainDriving ? brainPaise : localPaise);
    els.total.dataset.source = brainDriving ? 'brain' : 'counter';
    els.total.dataset.simulated = brainDriving && brainView.simulated === true
      ? 'yes' : 'no';

    const nAmber = brainDriving && typeof brainView.amber_count === 'number'
      ? brainView.amber_count : amberLines(st).length;
    const bits = [];
    if (nAmber) bits.push(`${nAmber} amber — excluded from the total`);
    if (brainDriving) {
      bits.push(brainView.simulated === true
        ? 'SIMULATED — scripted frames, not a camera. Nothing here is settled money.'
        : 'counted by the brain');
    }
    els.amber.textContent = bits.join(' · ');
    els.amber.hidden = bits.length === 0;
    els.lock.textContent = lock.locked ? 'MAT LOCK' : 'NO LOCK';
    els.lock.className = lock.locked ? 'lock lock-on' : 'lock lock-off';
    els.lockDetail.textContent = lock.locked
      ? `scale ${(lock.scaleErr * 100).toFixed(2)}% · rmse ${lock.reprojRmsePx.toFixed(2)}px · ~${perspToDeg(lock.perspIndex).toFixed(1)}° tilt`
      : lockDetailWhenUnlocked();
    els.banner.hidden = st.online && st.state !== State.PENDING_OFFLINE && connIsUp(conn);
    els.banner.textContent = bannerText(conn, outbox.length);
    els.ack.hidden = st.state !== State.FROZEN_TOTAL;
    els.done.disabled = committedLines(st).length === 0
      || BASKET_CLOSED.includes(st.state) || FROZEN_STATES.includes(st.state);
    if (els.conn) {
      els.conn.textContent = connIsUp(conn)
        ? 'brain connected'
        : `brain ${conn.status} — ${conn.reason}`;
      els.conn.className = `conn conn-${conn.status.toLowerCase()}`;
      els.conn.dataset.status = conn.status;
    }
    if (els.camstat) {
      els.camstat.textContent = camera.state === CameraState.LIVE
        ? camera.note
        : `${camera.state} — ${camera.reason}${camera.help ? ` · ${camera.help}` : ''}`;
      els.camstat.className = `camstat camstat-${camera.state.toLowerCase()}`;
      els.camstat.dataset.facing = camera.facing;
      els.camstat.dataset.reason = camera.reason;
    }
    // THE CAMERA GATE. One attribute, per the shell contract in index.html:
    // IDLE | REQUESTING | LIVE | DENIED | ABSENT | INSECURE | ERROR. The
    // stylesheet turns that single attribute into the whole screen — which
    // explanatory paragraph shows, whether the gate collapses, and whether the
    // "no camera feed" shade still covers the two preview canvases. Forgetting
    // to set it is exactly the black-pane failure invariant 7 forbids: the
    // stream would be live underneath a shade that still said no feed.
    if (els.camgate) {
      els.camgate.dataset.cam = cameraGateCode(camera);
      els.camgate.dataset.facing = camera.facing;
    }
    if (els.camreason) els.camreason.textContent = cameraReasonLine(camera);
    // INVARIANT 4, stated from the first paint rather than inherited from the
    // markup. Only the frame loop can retain a crop, and the frame loop only
    // runs while the camera is LIVE — so with no live camera the honest value
    // is `nothing`, and saying so here means the app asserts it even against a
    // shell whose data-policy attribute is missing, stale or wrong.
    if (camera.state !== CameraState.LIVE) els.rect.dataset.policy = RETAIN_NOTHING;
    applyPanelStatuses();
    renderLines();
    emitPanelState();
  }

  /**
   * Paint #panel-X[data-status], the rail dot and the "I DO NOT KNOW" block for
   * all six panels. CORE is derived from the lock; the other five report what
   * their module declared, adjudicated by panelStatusFor.
   *
   * INVARIANT 7: #abstain-X is hidden ONLY on OK. Every other status leaves the
   * abstention block on screen with a named reason in #why-X, so there is no
   * state of this app in which a panel shows nothing and explains nothing.
   */
  /**
   * A panel module that threw while attaching is OFF — it really is not
   * running — but it is off for a REASON, and that reason belongs on its own
   * status line where it persists. Writing it to the shared #reason field
   * instead loses it to the next repaint, which is how a capability comes to
   * be silently missing from a shipped counter.
   */
  function recordAttachFaults(drained) {
    for (const f of drained.refused) {
      if (f.id) panelAttachFaults.set(f.id, `${Reason.PANEL_ATTACH_FAILED}: ${f.message}`);
    }
    for (const id of drained.attached) panelAttachFaults.delete(id);
  }

  function applyPanelStatuses() {
    const statuses = { [DEFAULT_PANEL]: corePanelStatus({ camera, lock, cvReason }), ...registry.statuses() };
    for (const id of PANEL_IDS) {
      const s = statuses[id];
      if (!s) continue;
      if (s.status === PanelStatus.OFF && panelAttachFaults.has(id)) s.why = panelAttachFaults.get(id);
      const panelEl = document.getElementById(panelElementId(id));
      if (panelEl) panelEl.dataset.status = s.status;
      const whyEl = document.getElementById(`why-${id}`);
      if (whyEl && s.why) whyEl.textContent = s.why;
      const abstainEl = document.getElementById(`abstain-${id}`);
      if (abstainEl) abstainEl.hidden = s.status === PanelStatus.OK;
    }
  }

  /**
   * NO LOCK is not self-explanatory, and on a laptop it is not even a fault.
   * The detail line therefore always carries the CAUSE: no camera yet, the
   * camera refused and why, or — the one that looks most like a bug and is not
   * — the front camera is live and the mat is behind the screen.
   */
  function lockDetailWhenUnlocked() {
    if (camera.state === CameraState.IDLE) {
      return `camera not started — ${Reason.CAMERA_IDLE}`;
    }
    if (camera.state === CameraState.STARTING) return Reason.CAMERA_STARTING;
    if (camera.state === CameraState.FAILED) {
      return `no camera — ${camera.reason}${camera.help ? ` · ${camera.help}` : ''}`;
    }
    if (camera.matLockExpected === false) return `${lock.reason} — ${camera.note}`;
    return lock.reason;
  }

  /**
   * The read-only view handed to every registered panel. Copies, not the live
   * arrays: a panel is an observer and must not be able to reach back into the
   * counter (invariant 2).
   */
  function panelView() {
    return Object.freeze({
      state: st.state,
      reason: st.lastReason,
      lines: Object.freeze(st.lines.map((li) => Object.freeze({ ...li }))),
      totalPaise: totalPaise(st),
      amberCount: amberLines(st).length,
      matLocked: lock.locked === true,
      online: st.online,
      sessionId: st.sessionId,
      lock: Object.freeze({
        locked: lock.locked === true, reason: lock.reason,
        scaleErr: lock.scaleErr ?? null, perspIndex: lock.perspIndex ?? null,
        reprojRmsePx: lock.reprojRmsePx ?? null,
      }),
      camera,
      conn: Object.freeze({ ...conn }),
      panel: Object.freeze({ ...panel }),
      visible: panel.current,
    });
  }

  function emitPanelState() {
    try { registry.emitState(panelView()); }
    catch (e) { setReason(`${Reason.PANEL_HOOK_THREW}: ${e.message}`); }
  }

  function renderLines() {
    els.lines.replaceChildren();
    for (const li of st.lines) {
      const row = document.createElement('li');
      row.className = 'line'
        + (lineIsAmber(li) ? ' line-amber' : '')
        + (li.reverted ? ' line-reverted' : '')
        + (li.committed ? ' line-committed' : '');
      row.dataset.itemId = li.itemId;
      const name = document.createElement('span');
      name.className = 'line-name';
      name.textContent = li.name ?? li.itemId;
      const price = document.createElement('span');
      price.className = 'line-price';
      price.textContent = lineIsAmber(li) ? 'AMBER · excluded' : formatRupees(li.pricePaise);
      row.append(name, price);
      row.addEventListener('click', () => dispatch({ type: 'REVERT', itemId: li.itemId }));
      els.lines.appendChild(row);
    }
  }

  // ---- camera ------------------------------------------------------------
  /**
   * INVARIANT 7 at the camera. Every one of these failures used to render as
   * the same black rectangle, and a black rectangle is a lie — it reads as
   * "still loading" forever. Each branch now writes a NAMED reason to #reason,
   * paints the reason ONTO the preview pane so there is no silent black pane,
   * and leaves START tappable so the shopkeeper can retry after fixing it.
   */
  function setCamera(patch) {
    camera = Object.freeze({ ...camera, ...patch });
    if (els.start) {
      const busy = camera.state === CameraState.STARTING;
      els.start.disabled = busy;
      els.start.hidden = camera.state === CameraState.LIVE;
      if (camera.state === CameraState.FAILED) els.start.textContent = 'try the camera again';
    }
    render();
  }

  /** Paint a legible refusal onto the raw pane. Never leave it black and mute. */
  function paintCameraNotice(title, body) {
    if (!els.raw || typeof els.raw.getContext !== 'function') return;
    const ctx = els.raw.getContext('2d');
    const w = els.raw.width || 1280, h = els.raw.height || 960;
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = '#12151c';
    ctx.fillRect(0, 0, w, h);
    ctx.textAlign = 'center';
    ctx.fillStyle = '#e0a33c';
    ctx.font = 'bold 44px system-ui, sans-serif';
    ctx.fillText(title, w / 2, h / 2 - 40);
    ctx.fillStyle = '#c8ccd6';
    ctx.font = '26px system-ui, sans-serif';
    // Hand-wrapped: measureText is not worth a reflow for a static notice.
    const words = String(body || '').split(' ');
    let line = '', y = h / 2 + 16;
    for (const word of words) {
      if (line.length + word.length > 46) { ctx.fillText(line, w / 2, y); y += 34; line = ''; }
      line += (line ? ' ' : '') + word;
    }
    if (line) ctx.fillText(line, w / 2, y);
    ctx.restore();
  }

  function cameraRefused(reason, help, detail) {
    setCamera({ state: CameraState.FAILED, reason, help, facing: Facing.UNKNOWN, note: '' });
    setReason(`camera refused: ${reason}${detail ? ` (${detail})` : ''}`);
    paintCameraNotice('CAMERA UNAVAILABLE', `${reason} — ${help || ''}`);
  }

  /**
   * Walk the constraint ladder: rear camera first, then any camera. A denial,
   * a busy camera or an insecure origin STOPS the walk, because the second
   * attempt would fail identically and only serve to hide the reason.
   */
  async function acquireStream() {
    let last = null;
    for (const step of cameraFallbackPlan()) {
      try {
        const stream = await navigator.mediaDevices.getUserMedia(step.constraints);
        return { ok: true, stream, kind: step.kind };
      } catch (e) {
        last = classifyCameraError(e);
        last.kind = step.kind;
        if (!shouldRetryCamera(last.reason)) return { ok: false, ...last };
      }
    }
    return { ok: false, ...(last || { reason: Reason.CAMERA_FAILED, help: CAMERA_HELP[Reason.CAMERA_FAILED], detail: '' }) };
  }

  let pumping = false;
  async function startCamera() {
    if (camera.state === CameraState.STARTING || camera.state === CameraState.LIVE) return camera;
    setCamera({ state: CameraState.STARTING, reason: Reason.CAMERA_STARTING, help: '', note: '' });
    setReason(Reason.CAMERA_STARTING);

    const pre = cameraPreflight(cameraEnv(globalThis.window, globalThis.navigator));
    if (pre) { cameraRefused(pre.reason, pre.help, ''); return camera; }

    const got = await acquireStream();
    if (!got.ok) { cameraRefused(got.reason, got.help, got.detail); return camera; }

    els.video.srcObject = got.stream;
    try { await els.video.play(); }
    catch (e) {
      // Autoplay refused even after a tap: name it rather than showing black.
      cameraRefused(Reason.CAMERA_ABORTED, CAMERA_HELP[Reason.CAMERA_ABORTED], (e && e.message) || String(e));
      return camera;
    }

    const track = got.stream.getVideoTracks()[0];
    const settings = track && typeof track.getSettings === 'function' ? track.getSettings() : {};
    const d = describeCamera(settings, track ? track.label : '');
    els.raw.width = settings.width ?? 1280; els.raw.height = settings.height ?? 960;
    els.rect.width = BUF_W; els.rect.height = BUF_H;
    setCamera({
      state: CameraState.LIVE, reason: d.reason, help: '', facing: d.facing,
      note: d.note, kind: got.kind, matLockExpected: d.matLockExpected,
    });
    // SAY WHICH LENS. On a laptop this is the front camera and the mat will
    // never lock; that is a correct outcome and the note explains it in words
    // instead of leaving NO LOCK looking like a bug.
    setReason(`${Reason.CAMERA_LIVE}: ${d.note}`);
    if (!pumping) { pumping = true; pump(); }
    return camera;
  }

  function pump() {
    const step = (_now, meta) => {
      const ts = meta && meta.mediaTime !== undefined ? performance.now() : performance.now();
      if (shouldRenderFrame(lastFrameMs, ts, 30)) {
        const t0 = performance.now();
        try { onFrame(); } catch (e) { els.reason.textContent = `frame error: ${e.message}`; }
        frameTimes.push(performance.now() - t0);
        if (frameTimes.length > 120) frameTimes.shift();
        fpsWindow.push(ts);
        while (fpsWindow.length && ts - fpsWindow[0] > 1000) fpsWindow.shift();
        els.fps.textContent = `${fpsWindow.length} fps · p95 ${p95(frameTimes)}ms`;
        const over = p95(frameTimes) > DEGRADED_P95_MS;
        if (over !== st.degraded) dispatch({ type: 'PERF', p95Ms: p95(frameTimes) });
        lastFrameMs = ts;
      }
      schedule(step);
    };
    schedule(step);
  }

  function schedule(step) {
    if (els.video.requestVideoFrameCallback) els.video.requestVideoFrameCallback(step);
    else requestAnimationFrame((t) => step(t, null));
  }

  // ---- the frame loop ----------------------------------------------------
  function onFrame() {
    const rawCtx = els.raw.getContext('2d');
    rawCtx.drawImage(els.video, 0, 0, els.raw.width, els.raw.height);

    // FAIL CLOSED. safeDetect always returns a fresh verdict, so a detector
    // that throws CLEARS the lock instead of leaving the previous one standing.
    // The MAT_LOCK dispatch below then drives the session into MAT_LOST, which
    // freezes the total, greys the chrome and refuses further billing.
    lock = safeDetect(detector, els.raw, cv ? 'detector not ready' : Reason.OPENCV_ABSENT);
    if (lock.locked !== st.matLocked) dispatch({ type: 'MAT_LOCK', locked: lock.locked });

    // INVARIANT 4, made visible. The moment we have a lock we scrim everything
    // outside the mat quad on the raw preview, and the ONLY buffer we retain or
    // send is the rectified crop produced below.
    const policy = frameGrabPolicy(lock);
    els.rect.dataset.policy = policy.retain;
    if (policy.retain !== RETAIN_RECTIFIED) {
      const rc = els.rect.getContext('2d');
      rc.fillStyle = '#111'; rc.fillRect(0, 0, BUF_W, BUF_H);
      rc.fillStyle = '#c98a2b'; rc.font = '40px system-ui'; rc.textAlign = 'center';
      rc.fillText('no crop — ' + policy.reason, BUF_W / 2, BUF_H / 2);
      return;
    }

    const Hinv = invert3x3(lock.H);
    maskRawPreview(rawCtx, Hinv);
    rectify(lock.H);
    paintGlyphs(rawCtx, Hinv);

    // INVARIANT 4 on the panel seam. Panels are handed the RECTIFIED CROP and
    // nothing else — never els.raw, never the <video>. frameEgress builds the
    // payload and assertRectifiedFrame throws if it is ever anything else.
    const now = performance.now();
    const eg = frameEgress(lock, els.rect, { ts: now, seq: ++frameSeq });
    if (eg.send) {
      try { registry.emitFrame(eg.payload); }
      catch (e) { setReason(`${Reason.PANEL_HOOK_THREW}: ${e.message}`); }

      // INVARIANT 4 ON THE WIRE. The brain gets the rectified crop and nothing
      // else. `eg.payload.crop` is els.rect — the 840x1188 warp output — and
      // frameWirePayload can encode nothing but that; els.raw and the <video>
      // are not reachable from it. send() re-asserts on the way out.
      if (shouldSendFrameToBrain(lastBrainFrameMs, now, conn)) {
        const wire = frameWirePayload(eg.payload);
        els.rect.dataset.wire = wire.reason;
        if (wire.send) { send(wire.msg); lastBrainFrameMs = now; }
      }
    }
  }

  function maskRawPreview(ctx, Hinv) {
    const quad = matOutlineFrame(Hinv);
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, els.raw.width, els.raw.height);
    ctx.moveTo(quad[0][0], quad[0][1]);
    for (let i = quad.length - 1; i >= 1; i--) ctx.lineTo(quad[i][0], quad[i][1]);
    ctx.closePath();
    ctx.fillStyle = 'rgba(6,8,12,0.88)';   // everything outside the mat is scrimmed
    ctx.fill('evenodd');
    ctx.restore();
    ctx.save();
    ctx.strokeStyle = '#3ddc84'; ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(quad[0][0], quad[0][1]);
    for (let i = 1; i < quad.length; i++) ctx.lineTo(quad[i][0], quad[i][1]);
    ctx.closePath(); ctx.stroke(); ctx.restore();
  }

  function rectify(H) {
    // The rectified crop is produced with cv.warpPerspective and is the only
    // buffer that survives this function.
    const src = cv.imread(els.raw);
    const dst = new cv.Mat();
    const m = cv.matFromArray(3, 3, cv.CV_64F, H);
    const size = new cv.Size(BUF_W, BUF_H);
    cv.warpPerspective(src, dst, m, size, cv.INTER_LINEAR, cv.BORDER_CONSTANT, new cv.Scalar());
    cv.imshow(els.rect, dst);
    src.delete(); dst.delete(); m.delete();
  }

  function paintGlyphs(ctx, Hinv) {
    glyphs.length = 0;
    for (const li of st.lines) {
      if (li.reverted || !li.centreMm) continue;
      const quad = projectQuadMm(Hinv, glyphQuadMm(li.centreMm, 44, 20));
      glyphs.push({ itemId: li.itemId, quad });
      const t = glyphTransform(Hinv, li.centreMm, 1);
      ctx.save();
      ctx.setTransform(t.a, t.b, t.c, t.d, t.e, t.f);
      if (lineIsAmber(li)) {
        // AMBER: hatched outline, no glyph, no guessed name. Never a price.
        ctx.strokeStyle = '#e0a33c'; ctx.lineWidth = 0.7;
        ctx.beginPath();
        for (let x = -22; x <= 22; x += 4) { ctx.moveTo(x, -10); ctx.lineTo(x + 10, 10); }
        ctx.stroke();
        ctx.strokeRect(-22, -10, 44, 20);
        ctx.font = '6px system-ui'; ctx.fillStyle = '#e0a33c'; ctx.textAlign = 'center';
        ctx.fillText('EXCLUDED', 0, 3);
      } else {
        ctx.font = '13px system-ui'; ctx.textAlign = 'center';
        ctx.fillStyle = 'rgba(0,0,0,0.55)';
        ctx.fillText(formatRupees(li.pricePaise), 0.6, 5.6);
        ctx.fillStyle = '#ffffff';
        ctx.fillText(formatRupees(li.pricePaise), 0, 5);
      }
      ctx.restore();
    }
  }

  // ---- taps --------------------------------------------------------------
  els.raw.addEventListener('click', (ev) => {
    const r = els.raw.getBoundingClientRect();
    const pt = [
      ((ev.clientX - r.left) / r.width) * els.raw.width,
      ((ev.clientY - r.top) / r.height) * els.raw.height,
    ];
    const id = hitTestGlyph(pt, glyphs);
    if (id) dispatch({ type: 'REVERT', itemId: id });
  });

  els.done.addEventListener('click', () => {
    dispatch({ type: 'DONE' });
    if (st.lastApplied) {
      send({ type: 'done', sessionId: st.sessionId, amountPaise: st.intentAmountPaise });
    }
  });
  els.ack.addEventListener('click', () => dispatch({ type: 'ACK' }));

  // ---- transport ---------------------------------------------------------
  function send(msg) {
    // Invariant 4 is enforced on the send path, not in a comment.
    const payload = assertRectifiedOnly(msg);
    if (ws && ws.readyState === 1) { ws.send(JSON.stringify(payload)); return; }
    const r = enqueue(outbox, payload);
    outbox = r.queue;
    if (r.dropped) els.reason.textContent = `outbox full, dropped ${r.dropped} oldest`;
    render();
  }

  /**
   * The brain bridge. Connection state is the PURE reducer above, so the
   * escalation CONNECTING -> RETRYING -> OFFLINE is a tested function and not
   * an accident of setTimeout. Nothing here can authorise anything: a socket
   * that opens does not turn the counter green, and a socket that dies does not
   * stop it counting. Offline, the banner says PENDING_OFFLINE and billing
   * continues locally against a queue that authorises nothing.
   */
  function connect() {
    const url = brainUrl(globalThis.location || null);
    conn = reduceConn(conn, { type: 'CONNECT' });
    let sock;
    try { sock = new WebSocket(url); }
    catch (e) {
      conn = reduceConn(conn, { type: 'CLOSE' });
      setReason(`${conn.reason} (${(e && e.message) || e})`);
      wsTimer = setTimeout(connect, conn.nextDelayMs);
      render();
      return;
    }
    ws = sock;
    render();
    ws.onopen = () => {
      conn = reduceConn(conn, { type: 'OPEN' });
      dispatch({ type: 'BRAIN', up: true });
      if (!st.online) dispatch({ type: 'NETWORK', up: true });
      const drain = outbox; outbox = [];
      for (const m of drain) ws.send(JSON.stringify(m));
      setReason(Reason.WS_OPEN);
      render();
    };
    ws.onmessage = (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch { return; }
      onBrainMessage(m);
    };
    ws.onclose = () => {
      conn = reduceConn(conn, { type: 'CLOSE' });
      dispatch({ type: 'BRAIN', up: false });
      dispatch({ type: 'NETWORK', up: false });
      setReason(conn.status === Conn.OFFLINE
        ? `${Reason.WS_OFFLINE} — retrying in ${conn.nextDelayMs}ms`
        : `${Reason.WS_RETRYING} — attempt ${conn.attempt} in ${conn.nextDelayMs}ms`);
      wsTimer = setTimeout(connect, conn.nextDelayMs);
      render();
    };
    ws.onerror = () => { try { ws.close(); } catch { /* onclose handles it */ } };
  }

  // ---- panel router ------------------------------------------------------
  /**
   * Six containers, one visible. Driven by whichever of the two the shell
   * offers: a click on any [data-panel] control, or the location hash. Both
   * funnel through the pure selectPanel() so there is exactly one decision.
   */
  function showPanel(id, source) {
    const next = selectPanel(panel, id);
    if (!next.applied) {
      if (next.reason.startsWith(Reason.PANEL_UNKNOWN)) setReason(`refused: ${next.reason}`);
      return next;
    }
    panel = next;
    applyPanelVisibility();
    setReason(`${Reason.PANEL_SHOWN}: ${panel.current}${source ? ` (${source})` : ''}`);
    render();
    return next;
  }

  /**
   * WHO OWNS VISIBILITY. The shell shows a panel with pure CSS from a radio
   * group — `.shell:has(#tabsel-X:checked) #panel-X { display: block }` — so
   * the tabs work before this module loads and survive a module that throws.
   *
   * That means the router must move the RADIO. Setting `hidden` on a `.panel`
   * instead loses twice: the author `display:block` rule outranks the UA's
   * `[hidden] { display: none }` for the panel that IS selected, so hiding
   * does nothing there, while every panel the shopkeeper switches to by tapping
   * the rail gets hidden by a router that never noticed the radio moved. The
   * result is a blank stage and a router privately certain it is showing CORE.
   *
   * When the shell has no radio group — an older index.html — fall back to the
   * `hidden` map, which is what that shell understands.
   */
  function hasTabRadios() { return !!document.getElementById(panelTabId(DEFAULT_PANEL)); }

  function applyPanelVisibility() {
    if (hasTabRadios()) {
      for (const [tabId, checked] of Object.entries(panelTabSelection(panel))) {
        const t = document.getElementById(tabId);
        if (t && t.checked !== checked) t.checked = checked;
      }
    } else {
      for (const [elId, hidden] of Object.entries(panelVisibility(panel))) {
        const el = document.getElementById(elId);
        if (el) el.hidden = hidden;
      }
    }
    for (const id of PANEL_IDS) {
      const el = document.getElementById(panelElementId(id));
      if (el) el.dataset.active = id === panel.current ? 'true' : 'false';
    }
  }

  function wirePanelRouter() {
    // Radio router: the shell's own tabs. A label tap checks the radio, CSS
    // swaps the panel, and this listener tells the router what the DOM already
    // did — so panel.current, #chrome[data-panel] and the `visible` flag every
    // registered panel receives all stay true instead of drifting.
    for (const id of PANEL_IDS) {
      const t = document.getElementById(panelTabId(id));
      if (t && typeof t.addEventListener === 'function') {
        t.addEventListener('change', () => { if (t.checked) showPanel(id, 'rail'); });
      }
    }
    // Click router: any control carrying data-panel="<id>", wherever the shell
    // agent chose to put it. Delegated, so controls added later still work.
    if (document.addEventListener) {
      document.addEventListener('click', (ev) => {
        const t = ev && ev.target;
        const id = t && t.dataset && t.dataset.panel ? t.dataset.panel
          : (t && t.closest ? (t.closest('[data-panel]') || {}).dataset?.panel : undefined);
        if (id) showPanel(id, 'tap');
      });
    }
    // Hash router: deep links like #panel-mudra survive a reload.
    const loc = globalThis.location || null;
    if (globalThis.window && window.addEventListener) {
      window.addEventListener('hashchange', () => {
        const id = panelIdFromHash((globalThis.location || {}).hash || '');
        if (id) showPanel(id, 'hash');
      });
    }
    // Adopt whatever the markup already has checked BEFORE applying our own
    // idea of the selection, so a shell that ships with a different default
    // tab is followed rather than silently overridden on load.
    const checked = PANEL_IDS.find((id) => {
      const t = document.getElementById(panelTabId(id));
      return !!(t && t.checked === true);
    });
    if (checked && checked !== panel.current) panel = selectPanel(panel, checked);
    const initial = panelIdFromHash((loc && loc.hash) || '');
    if (initial) panel = selectPanel(panel, initial);
    applyPanelVisibility();
  }


  /** The brain's own view of the session, for display beside ours. */
  let brainView = null;

  /**
   * Route a capability message to whichever panel registered for it.
   *
   * Goes through PANEL_REGISTRY.get(id) -- the registry registerPanel() writes
   * to. The first version referenced a bare `registeredPanels` object that has
   * never existed in this file, so EVERY brain message threw a ReferenceError
   * inside ws.onmessage, several times a second, for as long as the socket was
   * up. Nothing caught it: the throw was inside an event handler, so the UI
   * carried on and only the console knew. Two lessons, both recorded rather
   * than fixed silently -- never invent the name of a collaborator you have not
   * read, and an unguarded throw in a socket handler is invisible from inside
   * the app.
   *
   * A panel whose hook throws is named on screen rather than taking the socket
   * handler down with it.
   */
  function deliverToPanel(id, msg) {
    const entry = PANEL_REGISTRY.get(id);
    if (!entry || typeof entry.onState !== 'function') return;
    try {
      entry.onState(msg);
    } catch (e) {
      els.reason.textContent = `panel ${id} failed: ${(e && e.message) || e}`;
    }
  }

  /**
   * Record the brain's view of the session WITHOUT writing it into the billing
   * reducer.
   *
   * The first version dispatched {type:'BRAIN_STATE'}, and the reducer refused
   * it as unknown_action_BRAIN_STATE -- correctly, and the refusal is the
   * reason this is now written differently rather than the reason an arm was
   * added. Feeding brain state into the same reducer that owns the local basket
   * would give the total TWO writers, and a money value with two writers has no
   * authority at all. The reducer owns what this counter believes; the brain's
   * view is displayed beside it, and any disagreement is a visible exception
   * rather than a silent overwrite.
   */
  function onBrainState(m) {
    brainView = m;
    deliverToPanel('ledger', { type: 'ledger', head: m.ledger_head, count: m.ledger_lines });
    // Re-render: brainView feeds the total, and nothing else was going to
    // trigger a paint. Without this the page held its own empty Rs.0.00 while
    // the brain was reporting a real basket, and only the disagreement line at
    // the bottom of the screen knew.
    render();
    // Compare against OUR total, computed by the reducer from committed lines.
    // `st` is the reducer state and totalPaise() derives the total from it --
    // there is no `state.totalPaise` field, and referencing one is what threw
    // ReferenceError here on the first attempt. Same class of mistake as
    // `registeredPanels`: a name assumed rather than read.
    // A real DISAGREEMENT is both sides having counted, differently. A local
    // total of zero while the brain drives a scripted run is not a conflict --
    // it is simply this page not being the one doing the counting, and warning
    // about it made an ordinary state look like a fault for the whole run.
    const ours = totalPaise(st);
    if (typeof m.total_paise === 'number' && ours > 0 && m.total_paise !== ours) {
      els.reason.textContent =
        `DISAGREEMENT: brain says ${m.total_paise}p, this counter says ${ours}p`;
    }
  }

  function onBrainMessage(m) {
    switch (m.type) {
      case 'placement':
        dispatch({
          type: 'PLACEMENT', itemId: m.itemId, name: m.name ?? null,
          pricePaise: m.pricePaise ?? null, centreMm: m.centreMm ?? null,
        });
        break;
      case 'price': dispatch({ type: 'PRICE', itemId: m.itemId, pricePaise: m.pricePaise }); break;
      case 'exit': dispatch({ type: 'EXIT', itemId: m.itemId ?? null, tap: m.tap === true }); break;
      case 'revert': dispatch({ type: 'REVERT', itemId: m.itemId }); break;
      case 'verdict': dispatch({ type: 'WEBHOOK', verdict: m.verdict }); break;

      // Panel feeds. The brain emits one message per capability; each is routed
      // to the panel that owns it and NEVER to the billing reducer -- none of
      // these may move money, which is why they do not dispatch().
      //
      // These were previously hitting `default` and being discarded with
      // "ignored unknown brain message", so every panel except CORE stayed
      // empty while the brain was faithfully sending it data. The abstention
      // was working exactly as designed and telling us so on screen; we just
      // had not written the arms.
      case 'mudra':
      case 'peel':
      case 'chilla':
      case 'saaf':
      case 'ledger':
        deliverToPanel(m.type, m);
        break;

      // Brain-side session truth. `state` carries the authoritative session,
      // total and amber set; the client renders it rather than recomputing.
      case 'state': onBrainState(m); break;

      // Housekeeping the client does not need to act on, but must not shout
      // about either -- a keepalive is not an anomaly.
      case 'keepalive':
      case 'panel':
      case 'refresh':
        break;

      // The brain refused something. This is a first-class outcome, not noise.
      case 'refused':
        els.reason.textContent =
          `brain refused: ${m.reason || 'no reason given'}`;
        break;

      default:
        // Unknown message types are abstained on loudly, never guessed at.
        els.reason.textContent = `ignored unknown brain message '${m.type}'`;
    }
  }

  window.addEventListener('online', () => {
    conn = reduceConn(conn, { type: 'NET_UP' });
    dispatch({ type: 'NETWORK', up: true });
  });
  window.addEventListener('offline', () => {
    conn = reduceConn(conn, { type: 'NET_DOWN' });
    dispatch({ type: 'NETWORK', up: false });
  });
  window.addEventListener('beforeunload', () => { if (wsTimer) clearTimeout(wsTimer); });

  // ---- the START gesture --------------------------------------------------
  // Browsers only hand over a camera after a real user gesture, and a page that
  // asks on load gets a permission prompt the shopkeeper has no context for.
  // #start is that gesture. When the shell has no #start we start immediately,
  // which is the older behaviour and keeps an un-updated index.html working.
  if (els.start) {
    els.start.addEventListener('click', () => {
      startCamera().catch((e) => cameraRefused(
        Reason.CAMERA_FAILED, CAMERA_HELP[Reason.CAMERA_FAILED], (e && e.message) || String(e)));
    });
  }

  // ---- start -------------------------------------------------------------
  (async () => {
    wirePanelRouter();
    // A panel declaring a new status repaints its dot immediately, rather than
    // waiting for the next frame — a panel that has just abstained should say
    // so now, not in 33ms.
    registry.watch(() => { applyPanelStatuses(); });

    // Attach the capability panels, whichever order they loaded in. Panels that
    // evaluated FIRST are sitting in the queue and get drained now; panels that
    // evaluate LATER find globalThis.registerPanel and attach themselves. The
    // patched push() catches the remaining case — a module that neither probes
    // the global nor loads before us — so no panel can silently fail to attach.
    globalThis.registerPanel = registerPanel;
    const queue = Array.isArray(globalThis.GAWAAH_PANELS) ? globalThis.GAWAAH_PANELS : [];
    recordAttachFaults(drainPanelQueue(queue, registerPanel));
    queue.push = (...ds) => {
      const n = Array.prototype.push.apply(queue, ds);
      recordAttachFaults(drainPanelQueue(ds, registerPanel));
      applyPanelStatuses();
      return n;
    };
    globalThis.GAWAAH_PANELS = queue;
    if (els.start) {
      // Not a black pane: say what is expected of the shopkeeper.
      paintCameraNotice('TAP START', CAMERA_HELP[Reason.CAMERA_IDLE]);
      setReason(Reason.CAMERA_IDLE);
    }
    render();
    const r = await loadOpenCV();
    if (!r.ok) {
      cvReason = r.reason;
      els.cvstat.textContent = `OpenCV absent (${r.reason}) — geometry unavailable, refusing to lock`;
      els.cvstat.className = 'cvstat cvstat-absent';
    } else {
      cvReason = null;
      cv = r.cv;
      els.cvstat.textContent = 'OpenCV 4.11.0-release.1 (vendored)';
      els.cvstat.className = 'cvstat cvstat-ok';
      detector = makeDetector(cv);
    }
    if (!els.start) {
      try { await startCamera(); }
      catch (e) {
        cameraRefused(Reason.CAMERA_FAILED, CAMERA_HELP[Reason.CAMERA_FAILED], (e && e.message) || String(e));
      }
    }
    connect();
  })();

  // The panel seam, also reachable from a non-module script. Same registry the
  // exported registerPanel() writes to — see makePanelRegistry's comment block.
  window.GAWAAH = Object.freeze({
    registerPanel,
    setPanelStatus,
    PanelStatus,
    showPanel: (id) => showPanel(id, 'api'),
    panels: PANEL_IDS,
    startCamera,
    RETAIN_RECTIFIED,
    version: 'gawaah-counter-1',
  });

  function makeDetector(cvv) {
    // getPredefinedDictionary is THE api. Verified in a browser against the
    // pinned 4.11.0 build (web/aprobe.js):
    //   getPredefinedDictionary      = function   -> returns an aruco_Dictionary
    //   getPredefinedDictionaryImpl  = undefined  <- the old first branch
    //   DICT_4X4_50                  = 0
    // The old code probed for ...Impl, found it undefined, and fell through to
    // `new aruco_Dictionary(DICT)`, whose binding takes 0 or 3 arguments and
    // NOT 1, so it threw:
    //   BindingError: Tried to invoke ctor of aruco_Dictionary with invalid
    //   number of parameters (1) - expected (0,3) parameters instead!
    // OpenCV had loaded fine by then; the mat could still never lock, because
    // the detector was never constructed.
    if (typeof cvv.getPredefinedDictionary !== 'function') {
      throw new Error('opencv_build_lacks_getPredefinedDictionary');
    }
    const dict = cvv.getPredefinedDictionary(cvv.DICT_4X4_50);
    const params = new cvv.aruco_DetectorParameters();
    params.cornerRefinementMethod = 1;   // CORNER_REFINE_SUBPIX, as in takhti.py
    const det = new cvv.aruco_ArucoDetector(dict, params, new cvv.aruco_RefineParameters(10, 3, true));
    return (canvas) => {
      const srcRgba = cvv.imread(canvas);
      const gray = new cvv.Mat();
      cvv.cvtColor(srcRgba, gray, cvv.COLOR_RGBA2GRAY);
      const corners = new cvv.MatVector();
      const ids = new cvv.Mat();
      det.detectMarkers(gray, corners, ids);
      const quads = {};
      for (let i = 0; i < ids.rows; i++) {
        const id = ids.intAt(i, 0);
        const d = corners.get(i).data32F;
        quads[id] = [[d[0], d[1]], [d[2], d[3]], [d[4], d[5]], [d[6], d[7]]];
      }
      // NOTE: @techstark/opencv-js Mat.clone() aliases the source buffer, which
      // silently breaks absdiff against a reference. Never clone; copyTo.
      srcRgba.delete(); gray.delete(); corners.delete(); ids.delete();
      return adjudicateLock(quads);
    };
  }
}

if (typeof document !== 'undefined' && typeof window !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
}
