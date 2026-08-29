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
  if (Object.prototype.hasOwnProperty.call(payload, 'crop')
      && payload.cropKind !== RETAIN_RECTIFIED) {
    throw new Error("invariant 4 violated: crop present without cropKind == 'rectified_mat_crop'");
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
// Browser shell. Everything below touches the DOM and never runs under node.
// ===========================================================================
export const OPENCV_PATH = './vendor/opencv.js';
export const WS_URL = 'ws://localhost:8787';

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
      const cv = globalThis.cv;
      if (!cv) return resolve({ ok: false, reason: Reason.OPENCV_ABSENT });
      const ready = () => (cv.aruco_ArucoDetector || cv.ArucoDetector
        ? resolve({ ok: true, cv })
        : resolve({ ok: false, reason: 'opencv_build_lacks_aruco' }));
      if (cv.getBuildInformation) ready(); else cv.onRuntimeInitialized = ready;
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
  };

  let st = initialState(`sess-${Date.now().toString(36)}`);
  let cv = null, detector = null, lock = { locked: false, reason: 'booting' };
  let outbox = [];
  let ws = null, wsAttempt = 0, wsTimer = null;
  let lastFrameMs = null, frameTimes = [], fpsWindow = [];
  const glyphs = [];   // {itemId, quad} in frame px, rebuilt every render

  // ---- state plumbing ----------------------------------------------------
  function dispatch(action) {
    const prev = st;
    st = reduce(st, action);
    if (st.lastApplied === false && prev.lastReason !== st.lastReason) {
      els.reason.textContent = `refused: ${st.lastReason}`;
    } else if (st.lastApplied) {
      els.reason.textContent = st.lastReason;
    }
    render();
  }

  function render() {
    const c = chromeFor(st.state);
    els.chrome.className = `chrome chrome-${c.colour}`;
    els.chrome.dataset.state = st.state;
    els.total.textContent = formatRupees(totalPaise(st));
    const nAmber = amberLines(st).length;
    els.amber.textContent = nAmber === 0
      ? '' : `${nAmber} amber — excluded from the total`;
    els.amber.hidden = nAmber === 0;
    els.lock.textContent = lock.locked ? 'MAT LOCK' : 'NO LOCK';
    els.lock.className = lock.locked ? 'lock lock-on' : 'lock lock-off';
    els.lockDetail.textContent = lock.locked
      ? `scale ${(lock.scaleErr * 100).toFixed(2)}% · rmse ${lock.reprojRmsePx.toFixed(2)}px · ~${perspToDeg(lock.perspIndex).toFixed(1)}° tilt`
      : lock.reason;
    els.banner.hidden = st.online && st.state !== State.PENDING_OFFLINE;
    els.banner.textContent = outbox.length
      ? `AMBER PENDING — offline, ${outbox.length} queued, nothing authorised`
      : 'AMBER PENDING — offline, nothing authorised';
    els.ack.hidden = st.state !== State.FROZEN_TOTAL;
    els.done.disabled = committedLines(st).length === 0
      || BASKET_CLOSED.includes(st.state) || FROZEN_STATES.includes(st.state);
    renderLines();
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
  async function startCamera() {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 960 } },
      audio: false,
    });
    els.video.srcObject = stream;
    await els.video.play();
    const t = stream.getVideoTracks()[0].getSettings();
    els.raw.width = t.width ?? 1280; els.raw.height = t.height ?? 960;
    els.rect.width = BUF_W; els.rect.height = BUF_H;
    pump();
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

  function connect() {
    ws = new WebSocket(WS_URL);
    ws.onopen = () => {
      wsAttempt = 0;
      dispatch({ type: 'BRAIN', up: true });
      if (!st.online) dispatch({ type: 'NETWORK', up: true });
      const drain = outbox; outbox = [];
      for (const m of drain) ws.send(JSON.stringify(m));
      render();
    };
    ws.onmessage = (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch { return; }
      onBrainMessage(m);
    };
    ws.onclose = () => {
      dispatch({ type: 'BRAIN', up: false });
      dispatch({ type: 'NETWORK', up: false });
      const wait = backoffMs(wsAttempt++);
      els.reason.textContent = `brain lost — reconnecting in ${wait}ms`;
      wsTimer = setTimeout(connect, wait);
    };
    ws.onerror = () => { try { ws.close(); } catch { /* onclose handles it */ } };
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
      default:
        // Unknown message types are abstained on loudly, never guessed at.
        els.reason.textContent = `ignored unknown brain message '${m.type}'`;
    }
  }

  window.addEventListener('online', () => dispatch({ type: 'NETWORK', up: true }));
  window.addEventListener('offline', () => dispatch({ type: 'NETWORK', up: false }));
  window.addEventListener('beforeunload', () => { if (wsTimer) clearTimeout(wsTimer); });

  // ---- start -------------------------------------------------------------
  (async () => {
    render();
    const r = await loadOpenCV();
    if (!r.ok) {
      els.cvstat.textContent = `OpenCV absent (${r.reason}) — geometry unavailable, refusing to lock`;
      els.cvstat.className = 'cvstat cvstat-absent';
    } else {
      cv = r.cv;
      els.cvstat.textContent = 'OpenCV 4.11.0-release.1 (vendored)';
      els.cvstat.className = 'cvstat cvstat-ok';
      detector = makeDetector(cv);
    }
    try { await startCamera(); }
    catch (e) { els.reason.textContent = `camera refused: ${e.message}`; }
    connect();
  })();

  function makeDetector(cvv) {
    const dict = cvv.getPredefinedDictionaryImpl
      ? cvv.getPredefinedDictionaryImpl(cvv.DICT_4X4_50)
      : new cvv.aruco_Dictionary(cvv.DICT_4X4_50);
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
