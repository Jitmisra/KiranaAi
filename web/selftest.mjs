/* GAWAAH counter PWA — node-runnable self-test of the pure core.
 *
 *   cd /Users/agnik/Desktop/razor && node web/selftest.mjs
 *
 * There is no browser here, so this exercises exactly the part of app.js that
 * has no DOM: money, geometry, glyph projection, the frame-grab mask policy,
 * the state reducer and the transport policy. It also re-lints the source the
 * way tools/lint_no_float.py lints the Python money path.
 *
 * app.js is loaded through a data: URL because web/ has no package.json to
 * declare "type": "module", and this file is not permitted to add one.
 */
import { readFileSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const APP_PATH = join(HERE, 'app.js');
const APP_SRC = readFileSync(APP_PATH, 'utf8');
const A = await import('data:text/javascript;charset=utf-8;base64,'
  + Buffer.from(APP_SRC, 'utf8').toString('base64'));

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
function throws(fn, what) {
  let threw = false;
  try { fn(); } catch { threw = true; }
  if (!threw) throw new Error(`expected a throw: ${what}`);
}
function deepFreeze(o) {
  if (o && typeof o === 'object' && !Object.isFrozen(o)) {
    Object.freeze(o); Object.values(o).forEach(deepFreeze);
  }
  return o;
}
/**
 * Strip comments, string literals and regex literals so a source lint sees only
 * executable code. Without this the lint trips on its own documentation: the
 * money block's header comment names `parseFloat` in order to forbid it, and
 * app.js carries a comment warning against `Mat.clone()`. A comment cannot
 * execute, so the lint must not read one.
 */
function stripJs(src) {
  let out = '';
  const regexPos = () => {
    const t = out.replace(/\s+$/, '');
    if (t === '') return true;
    if ('([{,;:!&|?+-*=~%<>^'.includes(t[t.length - 1])) return true;
    return /\b(return|typeof|case|in|of|new|delete|void|do|else|yield|await)$/.test(t);
  };
  for (let i = 0; i < src.length;) {
    const c = src[i], d = src[i + 1];
    if (c === '/' && d === '/') { while (i < src.length && src[i] !== '\n') i++; continue; }
    if (c === '/' && d === '*') {
      i += 2;
      while (i < src.length && !(src[i] === '*' && src[i + 1] === '/')) i++;
      i += 2; continue;
    }
    if (c === '"' || c === "'" || c === '`') {
      i++;
      while (i < src.length && src[i] !== c) { if (src[i] === '\\') i++; i++; }
      i++; out += 'STR'; continue;
    }
    if (c === '/' && regexPos()) {
      i++;
      let inClass = false;
      while (i < src.length) {
        if (src[i] === '\\') { i += 2; continue; }
        if (src[i] === '[') inClass = true;
        else if (src[i] === ']') inClass = false;
        else if (src[i] === '/' && !inClass) break;
        else if (src[i] === '\n') break;
        i++;
      }
      i++;
      while (i < src.length && /[gimsuyd]/.test(src[i])) i++;
      out += 'RE'; continue;
    }
    out += c; i++;
  }
  return out;
}

// Deterministic PRNG so the property runs are reproducible.
function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

console.log('GAWAAH counter PWA — pure-core self-test');
console.log(`node ${process.version} · app.js ${APP_SRC.length} bytes`);

// =========================================================== 1. MONEY ======
G('1. money — integer paise only (invariant 1)');

T('paise accepts integers', () => { eq(A.paise(0), 0); eq(A.paise(21450), 21450); eq(A.paise(-7), -7); });
T('paise rejects a float', () => throws(() => A.paise(214.5), '214.5'));
T('paise rejects 0.1+0.2', () => throws(() => A.paise(0.1 + 0.2), '0.30000000000000004'));
T('paise rejects bool', () => { throws(() => A.paise(true), 'true'); throws(() => A.paise(false), 'false'); });
T('paise rejects string', () => throws(() => A.paise('100'), "'100'"));
T('paise rejects null/undefined', () => {
  throws(() => A.paise(null), 'null'); throws(() => A.paise(undefined), 'undefined');
});
T('paise rejects NaN and Infinity', () => {
  throws(() => A.paise(NaN), 'NaN'); throws(() => A.paise(Infinity), 'Infinity');
});
T('paise rejects unsafe integers', () => throws(() => A.paise(2 ** 53), '2^53'));
T('MoneyError is the thrown type', () => {
  try { A.paise(1.5); } catch (e) { ok(e instanceof A.MoneyError, `got ${e.name}`); }
});

T("fromRupeesStr('214.50') === 21450", () => eq(A.fromRupeesStr('214.50'), 21450));
T("fromRupeesStr('0.05') === 5", () => eq(A.fromRupeesStr('0.05'), 5));
T("fromRupeesStr('7') === 700", () => eq(A.fromRupeesStr('7'), 700));
T("fromRupeesStr('7.5') === 750", () => eq(A.fromRupeesStr('7.5'), 750));
T("fromRupeesStr('-3.01') === -301", () => eq(A.fromRupeesStr('-3.01'), -301));
T('fromRupeesStr rejects sub-paisa', () => throws(() => A.fromRupeesStr('1.234'), '1.234'));
T('fromRupeesStr rejects junk', () => {
  ['', '  ', 'abc', '1.2.3', '1,50', '1e3', '.', '-'].forEach((s) => throws(() => A.fromRupeesStr(s), s));
});
T('fromRupeesStr rejects a number argument', () => throws(() => A.fromRupeesStr(214.5), 'number arg'));
T("toRupeesStr(21450) === '214.50'", () => eq(A.toRupeesStr(21450), '214.50'));
T("toRupeesStr(5) === '0.05'", () => eq(A.toRupeesStr(5), '0.05'));
T("toRupeesStr(-301) === '-3.01'", () => eq(A.toRupeesStr(-301), '-3.01'));
T("formatRupees(21450) === '₹214.50'", () => eq(A.formatRupees(21450), '₹214.50'));

T('rupee string round-trips over 0..200000 paise', () => {
  for (let p = 0; p <= 200000; p++) {
    const s = A.toRupeesStr(p);
    if (A.fromRupeesStr(s) !== p) throw new Error(`round-trip broke at ${p} -> ${s}`);
  }
  measured.money_roundtrip_values = 200001;
});
T('rupee round-trip holds at large magnitudes', () => {
  for (const p of [1e9, 1e12, 999999999999, 8999999999999]) {
    eq(A.fromRupeesStr(A.toRupeesStr(p)), p, `large ${p}`);
  }
});
T('the classic float trap: 0.1 + 0.2 in paise is exact', () => {
  eq(A.addPaise(A.fromRupeesStr('0.10'), A.fromRupeesStr('0.20')), A.fromRupeesStr('0.30'));
});
T('sumPaise of 10000 x ₹0.07 is exactly ₹700.00', () => {
  eq(A.sumPaise(new Array(10000).fill(7)), 70000);
  eq(A.toRupeesStr(70000), '700.00');
});
T('addPaise/sumPaise reject a float member', () => {
  throws(() => A.addPaise(1, 2.5), 'float in add');
  throws(() => A.sumPaise([1, 2, 3.5]), 'float in sum');
});

// The JS mirror of tools/lint_no_float.py, run over the money block itself.
G('2. no-float lint on the JS money path (mirrors tools/lint_no_float.py)');
const MONEY_BLOCK = (() => {
  const a = APP_SRC.indexOf('// MONEY BEGIN');
  const b = APP_SRC.indexOf('// MONEY END');
  ok(a > 0 && b > a, 'money block markers missing from app.js');
  return APP_SRC.slice(a, b);
})();
const MONEY_CODE = stripJs(MONEY_BLOCK);
T('money block exists and is non-trivial', () => ok(MONEY_BLOCK.length > 1200, `${MONEY_BLOCK.length} bytes`));
T('the stripper leaves real code behind', () => {
  ok(MONEY_CODE.includes('export function paise'), 'stripJs ate the code');
  ok(!MONEY_CODE.includes('INVARIANT 1'), 'stripJs left comments in');
  measured.money_block_bytes = MONEY_BLOCK.length;
  measured.money_code_bytes = MONEY_CODE.length;
});
T('no parseFloat in the money path', () => ok(!/parseFloat/.test(MONEY_CODE)));
T('no toFixed in the money path', () => ok(!/toFixed/.test(MONEY_CODE)));
T('no Math.round/floor/ceil in the money path', () => ok(!/Math\.(round|floor|ceil)/.test(MONEY_CODE)));
T('no float coercion in the money path', () => ok(!/Number\.parseFloat|\+\+?["']|valueOf/.test(MONEY_CODE)));
T('no decimal literal in the money path', () => {
  const m = MONEY_CODE.match(/(?<![\w.])\d+\.\d+/g);
  ok(m === null, `decimal literal(s) in money code: ${JSON.stringify(m)}`);
});
T('the only division in the money path is the exact (p - r) / 100', () => {
  const divs = MONEY_CODE.match(/\/(?![/*=])/g) || [];
  eq(divs.length, 1, `divisions found: ${divs.length}`);
  ok(/\(p - r\) \/ 100/.test(MONEY_CODE), 'the one division is not the exact divmod100 form');
});
T('the lint catches a planted violation (the lint itself is tested)', () => {
  const planted = stripJs('function f(){ return parseFloat(x) / 3.5; }');
  ok(/parseFloat/.test(planted), 'planted parseFloat not seen');
  ok(planted.match(/(?<![\w.])\d+\.\d+/g) !== null, 'planted decimal literal not seen');
  eq((planted.match(/\/(?![/*=])/g) || []).length, 1, 'planted division not counted');
  // and it does NOT fire on the same tokens inside a comment or a string
  const innocent = stripJs('// parseFloat 3.5 / 2\nconst s = "parseFloat 3.5 / 2";');
  ok(!/parseFloat/.test(innocent) && innocent.match(/(?<![\w.])\d+\.\d+/g) === null,
    'the lint reads comments and strings');
});
T('divmod100 is exact across a 300k sweep of paise', () => {
  for (let p = 0; p < 300000; p++) {
    const s = A.toRupeesStr(p);
    const [w, f] = s.split('.');
    if (Number.parseInt(w, 10) * 100 + Number.parseInt(f, 10) !== p) {
      throw new Error(`divmod100 inexact at ${p}: ${s}`);
    }
  }
  measured.divmod100_sweep = 300000;
});

// ======================================================== 3. GEOMETRY ======
G('3. geometry — homography core');

const I3 = [1, 0, 0, 0, 1, 0, 0, 0, 1];
T('mat constants match gawaah/takhti.py', () => {
  near(A.PX_PER_MM_X, 840 / 297, 0, 'px/mm x');
  near(A.PX_PER_MM_Y, 1188 / 420, 0, 'px/mm y');
  near(A.PX_PER_MM, 2 * Math.SQRT2, 2e-4, 'px/mm is 2*sqrt(2), NOT 2');
  ok(Math.abs(A.PX_PER_MM - 2) > 0.8, 'px/mm must not be the PRD 2.0 error');
  measured.px_per_mm = A.PX_PER_MM.toFixed(6);
});
T('marker centres match the printed layout', () => {
  const c = A.markerCentresMm();
  near(c[0][0], 27, 1e-12); near(c[0][1], 27, 1e-12);
  near(c[2][0], 297 - 27, 1e-12); near(c[2][1], 420 - 27, 1e-12);
});
T('applyH with identity is a no-op', () => {
  const [x, y] = A.applyH(I3, 12.5, -3.25); near(x, 12.5, 0); near(y, -3.25, 0);
});
T('invert3x3 round-trips a random projective map', () => {
  const rnd = mulberry32(11);
  for (let k = 0; k < 200; k++) {
    const H = [1 + rnd(), rnd() * 0.2, rnd() * 50, rnd() * 0.2, 1 + rnd(), rnd() * 50,
      (rnd() - 0.5) * 1e-4, (rnd() - 0.5) * 1e-4, 1];
    const Hi = A.invert3x3(H);
    const p = [37.5, 91.25];
    const q = A.applyH(H, p[0], p[1]);
    const back = A.applyH(Hi, q[0], q[1]);
    near(back[0], p[0], 1e-8, 'x'); near(back[1], p[1], 1e-8, 'y');
  }
});
T('invert3x3 refuses a singular matrix', () => throws(() => A.invert3x3([1, 2, 3, 2, 4, 6, 1, 1, 1]), 'singular'));
T('mat3Mul(H, inv(H)) is the identity', () => {
  const H = [1.11, -0.004, -117.4, -0.006, 1.11, -121.2, -9.3e-6, -1.4e-5, 1];
  const P = A.mat3Mul(H, A.invert3x3(H));
  for (let i = 0; i < 9; i++) near(P[i] / P[8], I3[i], 1e-9, `element ${i}`);
});
T('homographyFrom4Points recovers a known H exactly', () => {
  const H = [1.113677, -0.00363, -117.4436, -0.00576, 1.110299, -121.1945, -9.267e-6, -1.3686e-5, 1];
  const src = [[100, 100], [800, 120], [790, 1000], [110, 1010]];
  const dst = src.map((p) => A.applyH(H, p[0], p[1]));
  const R = A.homographyFrom4Points(src, dst);
  for (const p of [[400, 500], [0, 0], [1279, 959], [55, 903]]) {
    const a1 = A.applyH(H, p[0], p[1]), a2 = A.applyH(R, p[0], p[1]);
    near(a2[0], a1[0], 1e-6, `u at ${p}`); near(a2[1], a1[1], 1e-6, `v at ${p}`);
  }
});
T('homographyFrom4Points demands exactly 4 correspondences', () => {
  throws(() => A.homographyFrom4Points([[0, 0]], [[0, 0]]), '1 point');
});

// Cross-language conformance. These numbers were PRODUCED, not typed: they come
// from running gawaah.takhti.PlaneEngine on tests.test_plane.synth_frame(
//   px_per_mm=4.0, tilt=(3.0, -2.0), size=(960,1280), noise=0.0, seed=7, fit=0.82)
// in the repo venv, i.e. the same detector the Python side ships.
const PY = {
  src_frame_px: [
    [174.328399658, 178.564575195], [785.045898438, 181.340911865],
    [779.502380371, 1092.198120117], [176.453475952, 1094.631469727]],
  dst_buffer_px: [
    [76.363636364, 76.371428571], [763.636363636, 76.371428571],
    [763.636363636, 1111.628571429], [76.363636364, 1111.628571429]],
  H_frame_to_buffer: [
    1.113677071648, -0.003630251596, -117.443654639132,
    -0.005759931735, 1.110298896725, -121.194520497945,
    -9.267002e-6, -1.3686107e-5, 1.0],
  reproj_rmse_px: 2.3677e-5,
  persp_index: 0.017737731,
};

G('4. cross-language conformance vs the Python PlaneEngine');
const JS_H = A.homographyFrom4Points(PY.src_frame_px, PY.dst_buffer_px);
T('the JS marker-centre destinations match mm_to_buffer()', () => {
  const dst = A.markerCentresMm().map(A.mmToBuffer);
  for (let i = 0; i < 4; i++) {
    near(dst[i][0], PY.dst_buffer_px[i][0], 1e-8, `dst[${i}].x`);
    near(dst[i][1], PY.dst_buffer_px[i][1], 1e-8, `dst[${i}].y`);
  }
});
T('JS DLT reprojects the 4 correspondences to < 1e-9 px', () => {
  const r = A.reprojRmse(JS_H, PY.src_frame_px, PY.dst_buffer_px);
  ok(r < 1e-9, `rmse ${r}`);
  measured.js_dlt_reproj_rmse_px = r.toExponential(3);
});
T('JS H agrees with cv2.findHomography to sub-millipixel over the frame', () => {
  let worst = 0;
  for (let x = 0; x <= 1280; x += 40) {
    for (let y = 0; y <= 960; y += 40) {
      const a = A.applyH(JS_H, x, y), b = A.applyH(PY.H_frame_to_buffer, x, y);
      worst = Math.max(worst, Math.hypot(a[0] - b[0], a[1] - b[1]));
    }
  }
  ok(worst < 1e-3, `worst disagreement ${worst} px`);
  measured.js_vs_python_worst_px = worst.toExponential(3);
});
T('perspIndex agrees with PlaneEngine._persp_index', () => {
  const pi = A.perspIndex(JS_H);
  near(pi, PY.persp_index, 1e-6, 'persp index');
  measured.persp_index_js = pi.toFixed(9);
  measured.persp_index_py = PY.persp_index.toFixed(9);
});
T('the 3 deg / -2 deg synthetic tilt is inside the 8 deg gate', () => {
  const pi = A.perspIndex(JS_H);
  ok(pi <= A.MAX_PERSP_INDEX, `${pi} > ${A.MAX_PERSP_INDEX}`);
  measured.persp_deg_approx = A.perspToDeg(pi).toFixed(2);
});
T('perspToDeg inverts the PERSP_K calibration', () => {
  for (const deg of [0, 2, 5, 8, 15, 25]) {
    near(A.perspToDeg(A.PERSP_K * Math.tan((deg * Math.PI) / 180)), deg, 1e-9, `${deg} deg`);
  }
});
T('a fronto-parallel homography has perspIndex 0', () => {
  near(A.perspIndex([2, 0, 10, 0, 2, 20, 0, 0, 1]), 0, 1e-12);
});

G('5. mat lock adjudication — abstains rather than guesses');
function markerQuads(H, sideMm = A.MARKER_MM) {
  // Build the four marker corner quads in FRAME px by pushing mm corners
  // through buffer -> frame (i.e. the inverse of a frame -> buffer H).
  const Hi = A.invert3x3(H);
  const out = {};
  A.markerCentresMm().forEach((c, i) => {
    out[i] = A.projectQuadMm(Hi, A.glyphQuadMm(c, sideMm, sideMm));
  });
  return out;
}
// The residual scale error on a PERFECT mat is not zero, and the reason is
// physical: the buffer is anisotropic. 840/297 = 2.828283 px/mm across but
// 1188/420 = 2.828571 px/mm down, while scaleError divides by the isotropic
// mean (as gawaah/takhti.py does). The floor is therefore
//   (PX_PER_MM_Y - PX_PER_MM_X) / (PX_PER_MM_Y + PX_PER_MM_X)
// and any measurement claiming to beat it would be measuring nothing.
const ANISOTROPY = (A.PX_PER_MM_Y - A.PX_PER_MM_X) / (A.PX_PER_MM_Y + A.PX_PER_MM_X);
T('four clean markers lock, at the buffer anisotropy floor', () => {
  const L = A.adjudicateLock(markerQuads(PY.H_frame_to_buffer));
  ok(L.locked, `${L.reason}`);
  ok(L.scaleErr < 1e-4, `scale err ${L.scaleErr} should be at the noise floor`);
  near(L.scaleErr / ANISOTROPY, 1, 0.02, 'scale err is not the predicted anisotropy');
  ok(L.scaleErr < A.MAX_SCALE_ERR / 100, 'the floor must be far inside the 1.5% gate');
  measured.lock_scale_err = L.scaleErr.toExponential(4);
  measured.buffer_anisotropy_floor = ANISOTROPY.toExponential(4);
});
T('a fronto-parallel view hits the anisotropy floor exactly', () => {
  const L = A.adjudicateLock(markerQuads([2, 0, 15, 0, 2, 25, 0, 0, 1]));
  ok(L.locked, L.reason);
  near(L.scaleErr, ANISOTROPY, 1e-12, 'untilted scale error must BE the anisotropy');
});
T('no markers -> abstain, named reason', () => {
  const L = A.adjudicateLock({});
  ok(!L.locked); eq(L.reason, 'no markers detected');
});
T('three markers -> abstain, names the missing one', () => {
  const q = markerQuads(PY.H_frame_to_buffer); delete q[2];
  const L = A.adjudicateLock(q);
  ok(!L.locked); ok(L.reason.includes('missing markers'), L.reason); ok(L.reason.includes('2'), L.reason);
});
T('markers 2% oversize -> refuses to lock on scale error', () => {
  const L = A.adjudicateLock(markerQuads(PY.H_frame_to_buffer, A.MARKER_MM * 1.02));
  ok(!L.locked, 'should refuse'); ok(L.reason.startsWith('scale error'), L.reason);
  ok(L.scaleErr > A.MAX_SCALE_ERR, `${L.scaleErr}`);
});
T('markers 1% oversize -> still locks (inside the 1.5% gate)', () => {
  const L = A.adjudicateLock(markerQuads(PY.H_frame_to_buffer, A.MARKER_MM * 1.01));
  ok(L.locked, L.reason);
});
T('a 20 deg tilt -> refuses to lock on perspective index', () => {
  const t = A.PERSP_K * Math.tan((20 * Math.PI) / 180);
  const Hbuf2frame = [1, 0, 0, 0, 1, 0, t / Math.max(A.BUF_W, A.BUF_H), 0, 1];
  const L = A.adjudicateLock(markerQuads(A.invert3x3(Hbuf2frame)));
  ok(!L.locked, 'should refuse'); ok(L.reason.startsWith('perspective index'), L.reason);
  ok(L.reason.includes('deg'), 'reason must name the approximate angle');
});

// ================================================= 6. GLYPH PROJECTION =====
G('6. glyph projection — perspective via the local Jacobian');
const HINV = A.invert3x3(PY.H_frame_to_buffer);   // buffer px -> frame px

T('analytic Jacobian matches central finite differences', () => {
  const h = 1e-4;
  let worst = 0;
  for (const [x, y] of [[100, 100], [420, 594], [800, 1100], [76, 76]]) {
    const J = A.jacobianAt(HINV, x, y);
    const dux = (A.applyH(HINV, x + h, y)[0] - A.applyH(HINV, x - h, y)[0]) / (2 * h);
    const duy = (A.applyH(HINV, x, y + h)[0] - A.applyH(HINV, x, y - h)[0]) / (2 * h);
    const dvx = (A.applyH(HINV, x + h, y)[1] - A.applyH(HINV, x - h, y)[1]) / (2 * h);
    const dvy = (A.applyH(HINV, x, y + h)[1] - A.applyH(HINV, x, y - h)[1]) / (2 * h);
    worst = Math.max(worst, Math.abs(J.ux - dux), Math.abs(J.uy - duy),
      Math.abs(J.vx - dvx), Math.abs(J.vy - dvy));
  }
  ok(worst < 1e-6, `worst Jacobian error ${worst}`);
  measured.jacobian_vs_finite_diff = worst.toExponential(3);
});
T('jacobianAt refuses a point at infinity', () => throws(() => A.jacobianAt([1, 0, 0, 0, 1, 0, 1, 0, 0], 0, 5), 'w == 0'));

T('a glyph anchored at a mat point lands on that point in the frame', () => {
  for (const mm of [[148.5, 210], [27, 27], [270, 393], [10, 400]]) {
    const t = A.glyphTransform(HINV, mm, 1);
    const b = A.mmToBuffer(mm);
    const expect = A.applyH(HINV, b[0], b[1]);
    near(t.e, expect[0], 1e-9, 'glyph origin x'); near(t.f, expect[1], 1e-9, 'glyph origin y');
  }
});
T('the glyph transform is non-degenerate (positive determinant)', () => {
  const t = A.glyphTransform(HINV, [148.5, 210], 1);
  const det = t.a * t.d - t.b * t.c;
  ok(det > 0, `det ${det}`);
  measured.glyph_det_at_mat_centre = det.toFixed(6);
});
T('under a fronto-parallel H the glyph transform is a pure uniform scale', () => {
  const Hb2f = [0.5, 0, 30, 0, 0.5, 40, 0, 0, 1];   // buffer -> frame, no tilt
  const t = A.glyphTransform(Hb2f, [100, 200], 1);
  near(t.b, 0, 1e-12, 'no shear b'); near(t.c, 0, 1e-12, 'no shear c');
  near(t.a, 0.5 * A.PX_PER_MM_X, 1e-12, 'a'); near(t.d, 0.5 * A.PX_PER_MM_Y, 1e-12, 'd');
});
T('a tilted view makes the glyph scale vary across the mat', () => {
  const near_ = A.glyphTransform(HINV, [148.5, 30], 1);
  const far_ = A.glyphTransform(HINV, [148.5, 390], 1);
  const dn = near_.a * near_.d - near_.b * near_.c;
  const df = far_.a * far_.d - far_.b * far_.c;
  ok(Math.abs(dn - df) > 1e-6, `perspective produced no scale gradient: ${dn} vs ${df}`);
  measured.glyph_scale_gradient_near_far = (dn / df).toFixed(6);
});
T('a glyph unit square maps to the same quad the projector produces', () => {
  const mm = [148.5, 210], wMm = 44, hMm = 20;
  const t = A.glyphTransform(HINV, mm, 1);
  const quad = A.projectQuadMm(HINV, A.glyphQuadMm(mm, wMm, hMm));
  // local affine applied to the mm-offset corners approximates the true quad
  const corners = [[-wMm / 2, -hMm / 2], [wMm / 2, -hMm / 2], [wMm / 2, hMm / 2], [-wMm / 2, hMm / 2]];
  let worst = 0;
  corners.forEach(([dx, dy], i) => {
    const px = t.a * dx + t.c * dy + t.e, py = t.b * dx + t.d * dy + t.f;
    worst = Math.max(worst, Math.hypot(px - quad[i][0], py - quad[i][1]));
  });
  ok(worst < 1.0, `local affine deviates ${worst} px from the exact quad`);
  measured.glyph_affine_vs_exact_px = worst.toFixed(4);
});
T('mm -> buffer -> frame -> buffer -> mm round-trips', () => {
  for (const mm of [[0, 0], [297, 420], [148.5, 210], [12, 408]]) {
    const b = A.mmToBuffer(mm);
    const f = A.applyH(HINV, b[0], b[1]);
    const b2 = A.applyH(PY.H_frame_to_buffer, f[0], f[1]);
    const mm2 = A.bufferToMm(b2);
    near(mm2[0], mm[0], 1e-9, 'mm x'); near(mm2[1], mm[1], 1e-9, 'mm y');
  }
});
T('matOutlineFrame gives a convex, correctly-ordered quad', () => {
  const q = A.matOutlineFrame(HINV);
  eq(q.length, 4);
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const signs = [cross(q[0], q[1], q[2]), cross(q[1], q[2], q[3]),
    cross(q[2], q[3], q[0]), cross(q[3], q[0], q[1])].map(Math.sign);
  ok(signs.every((s) => s === signs[0]), `not convex: ${signs}`);
});
T('pointInQuad / hitTestGlyph pick the right line', () => {
  const g = [
    { itemId: 'a', quad: A.projectQuadMm(HINV, A.glyphQuadMm([60, 100], 44, 20)) },
    { itemId: 'b', quad: A.projectQuadMm(HINV, A.glyphQuadMm([230, 330], 44, 20)) },
  ];
  const centreOf = (mm) => { const b = A.mmToBuffer(mm); return A.applyH(HINV, b[0], b[1]); };
  eq(A.hitTestGlyph(centreOf([60, 100]), g), 'a');
  eq(A.hitTestGlyph(centreOf([230, 330]), g), 'b');
  eq(A.hitTestGlyph(centreOf([150, 210]), g), null, 'a tap on empty mat must hit nothing');
  eq(A.hitTestGlyph([-500, -500], g), null);
});

// ============================== 7. INVARIANT 4 — mask at frame grab ========
G('7. invariant 4 — the rectified crop is the only buffer that survives');
T('locked + H -> retain the rectified crop, at buffer size', () => {
  const p = A.frameGrabPolicy({ locked: true, H: PY.H_frame_to_buffer });
  eq(p.retain, A.RETAIN_RECTIFIED); eq(p.egress, A.RETAIN_RECTIFIED);
  eq(p.width, 840); eq(p.height, 1188);
});
T('not locked -> retain nothing, named reason', () => {
  const p = A.frameGrabPolicy({ locked: false, reason: 'no markers detected' });
  eq(p.retain, A.RETAIN_NOTHING);
  ok(p.reason.includes('no markers detected'), p.reason);
});
T('locked but no homography -> retain nothing', () => {
  eq(A.frameGrabPolicy({ locked: true }).retain, A.RETAIN_NOTHING);
  eq(A.frameGrabPolicy({ locked: true, H: [1, 2, 3] }).retain, A.RETAIN_NOTHING);
});
T('null / undefined / junk -> retain nothing', () => {
  for (const bad of [null, undefined, {}, 0, '', [], { locked: 'true' }, { locked: 1 }]) {
    eq(A.frameGrabPolicy(bad).retain, A.RETAIN_NOTHING, `input ${JSON.stringify(bad)}`);
  }
});
T('fuzz: no input makes frameGrabPolicy return a raw frame', () => {
  const rnd = mulberry32(3);
  const pick = (arr) => arr[Math.floor(rnd() * arr.length)];
  let n = 0;
  for (let i = 0; i < 20000; i++) {
    const lock = {
      locked: pick([true, false, 1, 0, 'true', null, undefined]),
      reason: pick(['x', '', null, undefined]),
      H: pick([PY.H_frame_to_buffer, null, [1, 2, 3], [], undefined, new Array(9).fill(rnd())]),
    };
    const p = A.frameGrabPolicy(lock);
    if (p.retain !== A.RETAIN_RECTIFIED && p.retain !== A.RETAIN_NOTHING) {
      throw new Error(`leaked retain=${p.retain}`);
    }
    if (/raw|full|unmasked|camera|photo/i.test(String(p.retain) + String(p.egress))) {
      throw new Error(`raw egress permitted: ${JSON.stringify(p)}`);
    }
    n++;
  }
  measured.frame_grab_fuzz_cases = n;
});
T('assertRectifiedOnly throws on every raw-frame key', () => {
  for (const k of ['raw', 'rawFrame', 'raw_frame', 'frame', 'fullFrame', 'full_frame',
    'camera', 'videoFrame', 'video_frame', 'unmasked', 'photo', 'snapshot']) {
    throws(() => A.assertRectifiedOnly({ [k]: 'data:image/jpeg;base64,AAAA' }), k);
  }
});
T('assertRectifiedOnly throws on a crop that is not tagged rectified', () => {
  throws(() => A.assertRectifiedOnly({ crop: 'x' }), 'untagged crop');
  throws(() => A.assertRectifiedOnly({ crop: 'x', cropKind: 'raw' }), 'raw-tagged crop');
});
T('assertRectifiedOnly passes a correctly tagged payload', () => {
  const p = { crop: 'x', cropKind: A.RETAIN_RECTIFIED, H: PY.H_frame_to_buffer, ts: 1 };
  eq(A.assertRectifiedOnly(p), p);
});

// ================================================ 8. STATE REDUCER =========
G('8. state reducer — mirrors gawaah/session.py');
const S0 = A.initialState('sess-test');
function run(actions, start = S0) { return actions.reduce((s, a) => A.reduce(s, a), start); }
const LOCK = { type: 'MAT_LOCK', locked: true };
const BRAIN = { type: 'BRAIN', up: true };
function place(id, price, mm = [100, 200]) {
  return { type: 'PLACEMENT', itemId: id, name: id, pricePaise: price, centreMm: mm };
}

T('a fresh session is SETUP, zero total, nothing authorised', () => {
  eq(S0.state, A.State.SETUP); eq(A.totalPaise(S0), 0); eq(S0.authorisedPaise, 0);
  eq(S0.intentAmountPaise, null);
});
T('mat lock leaves SETUP for IDLE', () => {
  eq(run([LOCK]).state, A.State.IDLE);
});
T('placement without a mat lock is refused', () => {
  const s = A.reduce(S0, place('a', 2000));
  eq(s.lastApplied, false); eq(s.lastReason, A.Reason.MAT_NOT_LOCKED); eq(s.lines.length, 0);
});
T('a priced placement then an exit commits and climbs the total', () => {
  const s = run([LOCK, BRAIN, place('a', 2000), { type: 'EXIT', itemId: 'a' }]);
  eq(s.state, A.State.BASKET_OPEN); eq(A.totalPaise(s), 2000);
  eq(A.formatRupees(A.totalPaise(s)), '₹20.00');
});
T('two committed lines sum exactly', () => {
  const s = run([LOCK, place('a', 2000), { type: 'EXIT', itemId: 'a' },
    place('b', 1450), { type: 'EXIT', itemId: 'b' }]);
  eq(A.totalPaise(s), 3450); eq(A.toRupeesStr(A.totalPaise(s)), '34.50');
});
T('an unpriced placement goes AMBER with reason unknown_sku', () => {
  const s = run([LOCK, place('u', null)]);
  eq(s.state, A.State.AMBER); eq(s.lastReason, A.Reason.UNKNOWN_SKU);
  ok(A.lineIsAmber(s.lines[0]));
});
T('INVARIANT 7 — a committed AMBER line is EXCLUDED from the total', () => {
  const s = run([LOCK, place('a', 2000), { type: 'EXIT', itemId: 'a' },
    place('u', null), { type: 'EXIT', itemId: 'u' }]);
  eq(s.lastReason, A.Reason.COMMITTED_AMBER);
  eq(A.committedLines(s).length, 2, 'both lines are committed');
  eq(A.amberLines(s).length, 1, 'one is amber');
  eq(A.totalPaise(s), 2000, 'the amber line must not be in the total');
});
T('an amber line never contributes however many are added', () => {
  let s = run([LOCK, place('a', 2000), { type: 'EXIT', itemId: 'a' }]);
  for (let i = 0; i < 25; i++) {
    s = run([place(`u${i}`, null), { type: 'EXIT', itemId: `u${i}` }], s);
  }
  eq(A.amberLines(s).length, 25); eq(A.totalPaise(s), 2000);
});
T('warm enroll — tapping a price resolves amber into the total', () => {
  let s = run([LOCK, place('u', null), { type: 'EXIT', itemId: 'u' }]);
  eq(A.totalPaise(s), 0);
  s = A.reduce(s, { type: 'PRICE', itemId: 'u', pricePaise: 1500 });
  eq(s.lastReason, A.Reason.PRICE_TAPPED);
  eq(A.amberLines(s).length, 0); eq(A.totalPaise(s), 1500);
});
T('PRICE rejects a float price with MoneyError', () => {
  const s = run([LOCK, place('u', null)]);
  throws(() => A.reduce(s, { type: 'PRICE', itemId: 'u', pricePaise: 15.5 }), 'float price');
  throws(() => A.reduce(s, { type: 'PRICE', itemId: 'u', pricePaise: '1500' }), 'string price');
});
T('PRICE on an unknown item is refused', () => {
  const s = A.reduce(run([LOCK]), { type: 'PRICE', itemId: 'nope', pricePaise: 100 });
  eq(s.lastApplied, false); eq(s.lastReason, A.Reason.UNKNOWN_ITEM);
});
T('R3 tap-to-revert removes the line from the total', () => {
  let s = run([LOCK, place('a', 2000), { type: 'EXIT', itemId: 'a' },
    place('b', 1450), { type: 'EXIT', itemId: 'b' }]);
  eq(A.totalPaise(s), 3450);
  s = A.reduce(s, { type: 'REVERT', itemId: 'a' });
  eq(s.lastReason, A.Reason.REVERTED); eq(A.totalPaise(s), 1450);
});
T('reverting the last line returns to IDLE', () => {
  let s = run([LOCK, place('a', 2000), { type: 'EXIT', itemId: 'a' }]);
  s = A.reduce(s, { type: 'REVERT', itemId: 'a' });
  eq(s.state, A.State.IDLE); eq(A.totalPaise(s), 0);
});
T('a second revert of the same line is a no-op', () => {
  let s = run([LOCK, place('a', 2000), { type: 'EXIT', itemId: 'a' },
    { type: 'REVERT', itemId: 'a' }]);
  const before = A.totalPaise(s);
  s = A.reduce(s, { type: 'REVERT', itemId: 'a' });
  eq(s.lastApplied, false); eq(s.lastReason, A.Reason.DUPLICATE); eq(A.totalPaise(s), before);
});
T('an unattributable exit crossing FREEZES the total (abstain, not guess)', () => {
  let s = run([LOCK, place('a', 2000), { type: 'EXIT', itemId: 'a' }]);
  s = A.reduce(s, { type: 'EXIT', itemId: null });
  eq(s.state, A.State.FROZEN_TOTAL); eq(s.lastReason, A.Reason.UNCOUNTED_CROSSING);
  eq(A.totalPaise(s), 2000, 'the total is the frozen snapshot');
  eq(A.chromeFor(s.state).colour, 'amber', 'an abstention is amber, never red');
});
T('an exit for an unknown item freezes with untracked_exit', () => {
  const s = A.reduce(run([LOCK]), { type: 'EXIT', itemId: 'ghost' });
  eq(s.state, A.State.FROZEN_TOTAL); eq(s.lastReason, A.Reason.UNTRACKED_EXIT);
});
T('a frozen total refuses billing until acknowledged, then resumes', () => {
  let s = run([LOCK, place('a', 2000), { type: 'EXIT', itemId: 'a' }, { type: 'EXIT', itemId: null }]);
  const r = A.reduce(s, place('b', 500));
  eq(r.lastApplied, false); eq(r.lastReason, A.Reason.REFUSED_FROZEN_TOTAL);
  s = A.reduce(s, { type: 'ACK' });
  eq(s.lastReason, A.Reason.HUMAN_ACKNOWLEDGED); eq(s.state, A.State.BASKET_OPEN);
  eq(s.frozenTotalPaise, null);
});
T('mat loss freezes the total and refuses billing; reacquire resumes', () => {
  let s = run([LOCK, place('a', 2000), { type: 'EXIT', itemId: 'a' }]);
  s = A.reduce(s, { type: 'MAT_LOCK', locked: false });
  eq(s.state, A.State.MAT_LOST); eq(A.totalPaise(s), 2000);
  eq(A.chromeFor(s.state).colour, 'grey', 'staleness is never red');
  const r = A.reduce(s, place('b', 100));
  eq(r.lastApplied, false); eq(r.lastReason, A.Reason.REFUSED_MAT_LOST);
  s = A.reduce(s, LOCK);
  eq(s.lastReason, A.Reason.MAT_REACQUIRED); eq(s.state, A.State.BASKET_OPEN);
});
T('brain loss freezes and refuses billing; reconnect resumes', () => {
  let s = run([LOCK, BRAIN, place('a', 2000), { type: 'EXIT', itemId: 'a' }]);
  s = A.reduce(s, { type: 'BRAIN', up: false });
  eq(s.state, A.State.BRAIN_LOST);
  eq(A.reduce(s, place('b', 1)).lastReason, A.Reason.REFUSED_BRAIN_LOST);
  s = A.reduce(s, BRAIN);
  eq(s.state, A.State.BASKET_OPEN);
});
T('DEGRADED disables auto-commit; an explicit tap still commits', () => {
  let s = run([LOCK, place('a', 2000)]);
  s = A.reduce(s, { type: 'PERF', p95Ms: 400 });
  eq(s.state, A.State.DEGRADED); eq(s.degraded, true);
  eq(A.chromeFor(s.state).colour, 'yellow');
  const auto = A.reduce(s, { type: 'EXIT', itemId: 'a' });
  eq(auto.lastApplied, false); eq(auto.lastReason, A.Reason.DEGRADED_REQUIRES_TAP);
  const tapped = A.reduce(s, { type: 'EXIT', itemId: 'a', tap: true });
  eq(tapped.lastApplied, true); eq(A.totalPaise(tapped), 2000);
});
T('p95 back under threshold recovers', () => {
  let s = run([LOCK, place('a', 2000), { type: 'PERF', p95Ms: 400 }]);
  s = A.reduce(s, { type: 'PERF', p95Ms: 90 });
  eq(s.degraded, false); eq(s.lastReason, A.Reason.PERF_RECOVERED);
});
T('DONE on an empty basket is refused', () => {
  const s = A.reduce(run([LOCK]), { type: 'DONE' });
  eq(s.lastApplied, false); eq(s.lastReason, A.Reason.EMPTY_BASKET);
});
T('DONE on an all-amber basket is refused — there is nothing to mint', () => {
  const s = A.reduce(run([LOCK, place('u', null), { type: 'EXIT', itemId: 'u' }]), { type: 'DONE' });
  eq(s.lastApplied, false); eq(s.lastReason, A.Reason.ZERO_TOTAL);
});
T('DONE mints an intent and authorises NOTHING — chrome stays amber', () => {
  const s = run([LOCK, place('a', 2000), { type: 'EXIT', itemId: 'a' }, { type: 'DONE' }]);
  eq(s.state, A.State.AWAITING_SETTLEMENT);
  eq(s.intentAmountPaise, 2000);
  eq(s.authorisedPaise, 0, 'DONE must not authorise money');
  eq(A.chromeFor(s.state).colour, 'amber');
});
T('DONE while offline goes PENDING_OFFLINE, still nothing authorised', () => {
  const s = run([LOCK, place('a', 2000), { type: 'EXIT', itemId: 'a' },
    { type: 'NETWORK', up: false }, { type: 'DONE' }]);
  eq(s.state, A.State.PENDING_OFFLINE); eq(s.authorisedPaise, 0);
  eq(s.lastReason, A.Reason.OFFLINE_NO_AUTHORISATION);
  ok(A.chromeFor(s.state).label.includes('AMBER PENDING'), A.chromeFor(s.state).label);
});
T('billing continues while offline before DONE', () => {
  const s = run([LOCK, { type: 'NETWORK', up: false }, place('a', 2000), { type: 'EXIT', itemId: 'a' }]);
  eq(A.totalPaise(s), 2000); eq(s.online, false);
});
T('network restored moves PENDING_OFFLINE back to AWAITING_SETTLEMENT', () => {
  const s = run([LOCK, place('a', 2000), { type: 'EXIT', itemId: 'a' },
    { type: 'NETWORK', up: false }, { type: 'DONE' }, { type: 'NETWORK', up: true }]);
  eq(s.state, A.State.AWAITING_SETTLEMENT); eq(s.lastReason, A.Reason.NETWORK_RESTORED);
});
T('the basket is locked after DONE', () => {
  const s = run([LOCK, place('a', 2000), { type: 'EXIT', itemId: 'a' }, { type: 'DONE' }]);
  eq(A.reduce(s, place('b', 100)).lastReason, A.Reason.BASKET_LOCKED);
  eq(A.reduce(s, { type: 'REVERT', itemId: 'a' }).lastReason, A.Reason.BASKET_LOCKED);
});
T('the reducer never mutates its input state', () => {
  const frozen = deepFreeze(A.initialState('sess-frozen'));
  const acts = [LOCK, BRAIN, place('a', 2000), { type: 'EXIT', itemId: 'a' },
    { type: 'PRICE', itemId: 'a', pricePaise: 3000 }, { type: 'REVERT', itemId: 'a' },
    { type: 'PERF', p95Ms: 900 }, { type: 'NETWORK', up: false }];
  let s = frozen;
  for (const a of acts) { s = A.reduce(s, a); deepFreeze(s); }
  eq(frozen.state, A.State.SETUP); eq(frozen.lines.length, 0);
});
T('an unknown action is refused, not silently applied', () => {
  const s = A.reduce(run([LOCK]), { type: 'NONSENSE' });
  eq(s.lastApplied, false); ok(s.lastReason.startsWith('unknown_action_'), s.lastReason);
});

// ============================== 9. INVARIANT 2 — the four green gates ======
G('9. invariant 2 — GREEN needs all four, and only a webhook can grant it');
function armed() {
  return run([LOCK, BRAIN, place('a', 2000), { type: 'EXIT', itemId: 'a' }, { type: 'DONE' }]);
}
const GOOD = () => ({
  eventId: 'evt_1', event: 'payment.captured', sessionId: 'sess-test',
  amountPaise: 2000, green: true, signatureValid: true,
});

T('all four gates pass -> PAID, exact amount authorised', () => {
  const s = A.reduce(armed(), { type: 'WEBHOOK', verdict: GOOD() });
  eq(s.state, A.State.PAID); eq(s.authorisedPaise, 2000);
  eq(s.lastReason, A.Reason.SETTLED); eq(A.chromeFor(s.state).colour, 'green');
});
T('gate 1: an invalid signature is discarded, never green', () => {
  const s = A.reduce(armed(), { type: 'WEBHOOK', verdict: { ...GOOD(), signatureValid: false } });
  ok(s.state !== A.State.PAID); eq(s.lastReason, A.Reason.BAD_SIGNATURE); eq(s.authorisedPaise, 0);
});
T('gate 2: an event outside the green set is discarded', () => {
  for (const e of ['payment.failed', 'qr_code.created', 'qr_code.closed', 'order.paid', '']) {
    const s = A.reduce(armed(), { type: 'WEBHOOK', verdict: { ...GOOD(), event: e } });
    ok(s.state !== A.State.PAID, `event ${e} must not be green`);
    eq(s.lastReason, A.Reason.NOT_IN_GREEN_SET, `event ${e}`);
  }
});
T('the green set is exactly the three settlement events', () => {
  eq(JSON.stringify([...A.GREEN_EVENTS].sort()),
    JSON.stringify(['payment.captured', 'payment_link.paid', 'qr_code.credited']));
});
T('gate 3: a foreign session_id is discarded', () => {
  const s = A.reduce(armed(), { type: 'WEBHOOK', verdict: { ...GOOD(), sessionId: 'someone-else' } });
  ok(s.state !== A.State.PAID); eq(s.lastReason, A.Reason.FOREIGN_SESSION);
});
T('gate 3b: no open intent -> discarded', () => {
  const s = A.reduce(run([LOCK, place('a', 2000), { type: 'EXIT', itemId: 'a' }]),
    { type: 'WEBHOOK', verdict: GOOD() });
  ok(s.state !== A.State.PAID); eq(s.lastReason, A.Reason.NO_OPEN_INTENT);
});
T('gate 4: an amount that is off by one paisa is a RED HOLD, not green', () => {
  for (const amt of [1999, 2001, 0, 200000]) {
    const s = A.reduce(armed(), { type: 'WEBHOOK', verdict: { ...GOOD(), amountPaise: amt } });
    eq(s.state, A.State.AMOUNT_MISMATCH, `amount ${amt}`);
    eq(A.chromeFor(s.state).colour, 'red');
    eq(s.authorisedPaise, 0, 'a mismatch authorises nothing');
  }
});
T('gate 4: a float amount can never match an integer intent', () => {
  const s = A.reduce(armed(), { type: 'WEBHOOK', verdict: { ...GOOD(), amountPaise: 2000.0001 } });
  ok(s.state !== A.State.PAID); eq(s.state, A.State.AMOUNT_MISMATCH);
});
T('paisa refusing green vetoes the client too', () => {
  const s = A.reduce(armed(), { type: 'WEBHOOK', verdict: { ...GOOD(), green: false } });
  ok(s.state !== A.State.PAID); eq(s.lastReason, A.Reason.PAISA_REFUSED_GREEN);
});
T('exhaustive: green iff all four conditions hold (16 combinations)', () => {
  const base = armed();
  let greens = 0;
  for (let m = 0; m < 16; m++) {
    const sig = !!(m & 1), inSet = !!(m & 2), sess = !!(m & 4), amt = !!(m & 8);
    const v = {
      eventId: `evt_${m}`,
      event: inSet ? 'payment_link.paid' : 'payment.failed',
      sessionId: sess ? 'sess-test' : 'other',
      amountPaise: amt ? 2000 : 2500,
      green: true, signatureValid: sig,
    };
    const s = A.reduce(base, { type: 'WEBHOOK', verdict: v });
    const expectGreen = sig && inSet && sess && amt;
    eq(s.state === A.State.PAID, expectGreen,
      `sig=${sig} set=${inSet} sess=${sess} amt=${amt}`);
    if (expectGreen) greens++;
  }
  eq(greens, 1, 'exactly one of the 16 combinations may be green');
  measured.green_gate_combinations = 16;
});
T('no non-webhook action can ever reach PAID', () => {
  const rnd = mulberry32(99);
  const ids = ['a', 'b', 'c', null, 'ghost'];
  let s = S0, sawPaid = false, n = 0;
  for (let i = 0; i < 40000; i++) {
    const id = ids[Math.floor(rnd() * ids.length)];
    const acts = [
      LOCK, { type: 'MAT_LOCK', locked: false }, BRAIN, { type: 'BRAIN', up: false },
      place(id ?? 'x', rnd() < 0.4 ? null : Math.floor(rnd() * 10000)),
      { type: 'PRICE', itemId: id ?? 'x', pricePaise: Math.floor(rnd() * 10000) },
      { type: 'EXIT', itemId: id, tap: rnd() < 0.5 },
      { type: 'REVERT', itemId: id ?? 'x' },
      { type: 'DONE' }, { type: 'ACK' },
      { type: 'NETWORK', up: rnd() < 0.5 }, { type: 'PERF', p95Ms: Math.floor(rnd() * 600) },
    ];
    try { s = A.reduce(s, acts[Math.floor(rnd() * acts.length)]); } catch { /* MoneyError is fine */ }
    if (s.state === A.State.PAID) sawPaid = true;
    if (s.authorisedPaise !== 0) throw new Error('money authorised without a webhook');
    n++;
    if (i % 300 === 299) s = A.initialState('sess-test');
  }
  ok(!sawPaid, 'a non-webhook action reached PAID');
  measured.no_paid_without_webhook_steps = n;
});
T('a duplicate event_id is ignored', () => {
  let s = A.reduce(armed(), { type: 'WEBHOOK', verdict: GOOD() });
  eq(s.state, A.State.PAID);
  s = A.reduce(s, { type: 'WEBHOOK', verdict: GOOD() });
  eq(s.lastReason, A.Reason.DUPLICATE); eq(s.authorisedPaise, 2000);
});
T('a second, different event after settlement is ignored', () => {
  let s = A.reduce(armed(), { type: 'WEBHOOK', verdict: GOOD() });
  s = A.reduce(s, { type: 'WEBHOOK', verdict: { ...GOOD(), eventId: 'evt_2', amountPaise: 99999 } });
  eq(s.state, A.State.PAID); eq(s.lastReason, A.Reason.ALREADY_SETTLED);
  eq(s.authorisedPaise, 2000, 'settlement amount must not move');
});
T('a RED HOLD refuses everything until it is resolved by hand', () => {
  const s = A.reduce(armed(), { type: 'WEBHOOK', verdict: { ...GOOD(), amountPaise: 1 } });
  eq(s.state, A.State.AMOUNT_MISMATCH);
  for (const a of [place('z', 100), { type: 'EXIT', itemId: 'a' }, { type: 'DONE' },
    { type: 'REVERT', itemId: 'a' }]) {
    eq(A.reduce(s, a).lastReason, A.Reason.RED_HOLD, JSON.stringify(a));
  }
});

// ============================== 10. total property =========================
G('10. property — the total is always the sum of the counting lines');
T('20000 random action steps never break the total identity', () => {
  const rnd = mulberry32(2026);
  let s = A.initialState('sess-test'), steps = 0, maxTotal = 0;
  for (let i = 0; i < 20000; i++) {
    const id = `i${Math.floor(rnd() * 6)}`;
    const acts = [
      LOCK, BRAIN, { type: 'MAT_LOCK', locked: false }, { type: 'BRAIN', up: false },
      place(id, rnd() < 0.35 ? null : Math.floor(rnd() * 50000)),
      { type: 'PRICE', itemId: id, pricePaise: Math.floor(rnd() * 50000) },
      { type: 'EXIT', itemId: rnd() < 0.08 ? null : id, tap: true },
      { type: 'REVERT', itemId: id }, { type: 'ACK' },
      { type: 'NETWORK', up: rnd() < 0.5 }, { type: 'PERF', p95Ms: Math.floor(rnd() * 500) },
    ];
    s = A.reduce(s, acts[Math.floor(rnd() * acts.length)]);
    const t = A.totalPaise(s);
    if (!Number.isSafeInteger(t)) throw new Error(`total is not an integer: ${t}`);
    if (t < 0) throw new Error(`negative total: ${t}`);
    if (s.frozenTotalPaise === null) {
      const want = s.lines.filter(A.lineCounts).reduce((x, li) => x + li.pricePaise, 0);
      if (t !== want) throw new Error(`total drifted: ${t} != ${want}`);
    }
    // an amber line must never be inside the total
    for (const li of A.amberLines(s)) {
      if (li.pricePaise !== null) throw new Error('amberLines returned a priced line');
    }
    maxTotal = Math.max(maxTotal, t);
    steps++;
    if (i % 250 === 249) s = A.initialState('sess-test');
  }
  measured.property_steps = steps;
  measured.property_max_total = A.formatRupees(maxTotal);
});
T('committing an amber line is a no-op on the total, 5000 trials', () => {
  const rnd = mulberry32(7);
  for (let i = 0; i < 5000; i++) {
    let s = run([LOCK, place('a', Math.floor(rnd() * 100000)), { type: 'EXIT', itemId: 'a' }]);
    const before = A.totalPaise(s);
    s = run([place(`u${i}`, null), { type: 'EXIT', itemId: `u${i}` }], s);
    if (A.totalPaise(s) !== before) throw new Error('amber moved the total');
  }
  measured.amber_noop_trials = 5000;
});

// ============================== 11. chrome =================================
G('11. chrome — colour follows state, red is reserved');
T('every State has a chrome entry', () => {
  for (const st of Object.values(A.State)) {
    const c = A.chromeFor(st);
    ok(c.label && !c.label.startsWith('unknown state'), `no chrome for ${st}`);
    ok(['grey', 'amber', 'yellow', 'white', 'green', 'red'].includes(c.colour), `${st}: ${c.colour}`);
  }
});
T('RED is used by exactly one state: AMOUNT_MISMATCH', () => {
  const reds = Object.values(A.State).filter((s) => A.chromeFor(s).colour === 'red');
  eq(JSON.stringify(reds), JSON.stringify([A.State.AMOUNT_MISMATCH]));
});
T('GREEN is used by exactly one state: PAID', () => {
  const greens = Object.values(A.State).filter((s) => A.chromeFor(s).colour === 'green');
  eq(JSON.stringify(greens), JSON.stringify([A.State.PAID]));
});
T('INVARIANT 7 — every stale / unknown state is amber or grey, never red', () => {
  for (const st of [A.State.MAT_LOST, A.State.BRAIN_LOST, A.State.PENDING_OFFLINE,
    A.State.FROZEN_TOTAL, A.State.AMBER, A.State.AWAITING_SETTLEMENT]) {
    const c = A.chromeFor(st).colour;
    ok(c === 'amber' || c === 'grey', `${st} is ${c}, must be amber or grey`);
  }
});
T('an unknown state degrades to grey rather than throwing', () => {
  eq(A.chromeFor('NOT_A_STATE').colour, 'grey');
});

// ============================== 12. transport ==============================
G('12. transport — reconnect backoff and the bounded offline queue');
T('backoff grows exponentially and is capped', () => {
  const one = () => 1;
  eq(A.backoffMs(0, one), 250); eq(A.backoffMs(1, one), 500);
  eq(A.backoffMs(2, one), 1000); eq(A.backoffMs(5, one), 8000);
  eq(A.backoffMs(50, one), A.WS_CAP_MS, 'must be capped');
});
T('jitter stays inside [0.5, 1.0] of the window, 20000 draws', () => {
  const rnd = mulberry32(5);
  let lo = Infinity, hi = 0;
  for (let a = 0; a <= 8; a++) {
    const w = Math.min(A.WS_CAP_MS, A.WS_BASE_MS * 2 ** a);
    for (let i = 0; i < 20000 / 9; i++) {
      const v = A.backoffMs(a, rnd);
      if (v < w * 0.5 - 1 || v > w) throw new Error(`attempt ${a}: ${v} outside [${w * 0.5}, ${w}]`);
      lo = Math.min(lo, v / w); hi = Math.max(hi, v / w);
    }
  }
  measured.backoff_jitter_range = `${lo.toFixed(3)}..${hi.toFixed(3)}`;
});
T('backoff tolerates a negative or fractional attempt', () => {
  eq(A.backoffMs(-3, () => 1), 250); eq(A.backoffMs(1.9, () => 1), 500);
});
T('the outbox is bounded and drops the OLDEST', () => {
  let q = [];
  for (let i = 0; i < A.QUEUE_CAP; i++) q = A.enqueue(q, i).queue;
  eq(q.length, A.QUEUE_CAP); eq(q[0], 0);
  const r = A.enqueue(q, 'new');
  eq(r.queue.length, A.QUEUE_CAP); eq(r.dropped, 1);
  eq(r.queue[0], 1, 'the oldest must be the one dropped');
  eq(r.queue[r.queue.length - 1], 'new');
});
T('enqueue does not mutate the queue it was given', () => {
  const q = [1, 2, 3]; A.enqueue(q, 4, 3); eq(q.length, 3);
});
T('the 30fps gate admits exactly 30 from 60Hz, 90Hz and 120Hz sources', () => {
  const admits = (srcHz) => {
    let last = null, n = 0;
    for (let i = 0; i < srcHz; i++) {
      const t = (i * 1000) / srcHz;
      if (A.shouldRenderFrame(last, t, 30)) { n++; last = t; }
    }
    return n;
  };
  // The naive `>= 1000/targetFps` comparison yields 22 here, not 30, because
  // 2*(1000/60) is one ulp below 1000/30. This is the regression test for that.
  eq(admits(60), 30, 'from 60Hz');
  eq(admits(90), 30, 'from 90Hz');
  eq(admits(120), 30, 'from 120Hz');
  eq(admits(30), 30, 'a 30Hz source must pass every frame through');
  measured.fps_gate_admits_60_90_120 = `${admits(60)}/${admits(90)}/${admits(120)}`;
});
T('the fps epsilon is far below any real inter-frame interval', () => {
  ok(A.FPS_EPS_MS > 0 && A.FPS_EPS_MS < 1000 / 240, `${A.FPS_EPS_MS}ms`);
  ok(!A.shouldRenderFrame(0, 1000 / 60, 30), 'a single 60Hz frame must not pass the gate');
});
T('the first frame is always admitted', () => ok(A.shouldRenderFrame(null, 0, 30)));
T('p95 is nearest-rank and integral', () => {
  eq(A.p95([]), 0);
  eq(A.p95([10]), 10);
  eq(A.p95(Array.from({ length: 100 }, (_, i) => i + 1)), 95);
  ok(Number.isInteger(A.p95([1.4, 2.6, 300.5])));
});

// ============================== 13. invariants by source scan ==============
G('13. source scan — invariants 3 and 6, and the vendored OpenCV pin');
const HTML = readFileSync(join(HERE, 'index.html'), 'utf8');
const CSS = readFileSync(join(HERE, 'style.css'), 'utf8');
const README = readFileSync(join(HERE, 'README.md'), 'utf8');
const ALL_CLIENT = APP_SRC + '\n' + HTML + '\n' + CSS;

T('INVARIANT 3 — no model weights or inference runtime anywhere in the client', () => {
  const banned = [/\.onnx\b/i, /\.tflite\b/i, /\.safetensors\b/i, /\bonnxruntime/i,
    /\bort\.env\b/, /@xenova\b/i, /transformers\.js/i, /tensorflow/i, /\btfjs\b/i,
    /mobileclip/i, /siglip/i, /\bwebnn\b/i, /\.pth\b/i, /\.pb\b/i];
  for (const re of banned) {
    if (re.test(ALL_CLIENT)) throw new Error(`model/inference reference found: ${re}`);
  }
});
T('INVARIANT 6 — no UPI payload is ever constructed or regenerated', () => {
  const banned = [/upi:\/\//i, /\bpa=[^\s]*&pn=/i, /qrcode\s*\(/i, /\bQRCode\b/,
    /encodeQR/i, /toDataURL\(['"]?upi/i, /vpa\s*\+/i];
  for (const re of banned) {
    if (re.test(ALL_CLIENT)) throw new Error(`forgery primitive found: ${re}`);
  }
});
T('no secret ever lives on the phone', () => {
  for (const re of [/key_secret/i, /RAZORPAY_KEY/i, /webhook_secret/i, /rzp_(test|live)_/i,
    /hmac/i, /createHmac/i]) {
    if (re.test(ALL_CLIENT)) throw new Error(`secret-shaped token in the client: ${re}`);
  }
});
T('OpenCV is referenced from a LOCAL vendored path, never a CDN', () => {
  ok(APP_SRC.includes("'./vendor/opencv.js'"), 'vendored path missing');
  for (const re of [/https?:\/\/cdn/i, /unpkg\.com/i, /jsdelivr/i, /esm\.sh/i, /skypack/i]) {
    if (re.test(ALL_CLIENT)) throw new Error(`CDN reference found: ${re}`);
  }
});
T('the app degrades gracefully when OpenCV is absent', () => {
  ok(APP_SRC.includes('OPENCV_ABSENT'), 'no absent path');
  eq(A.Reason.OPENCV_ABSENT, 'opencv_absent_geometry_unavailable');
  ok(/onerror[\s\S]{0,80}OPENCV_ABSENT/.test(APP_SRC), 'script onerror does not abstain');
});
T('Mat.clone() is never CALLED (@techstark 4.x aliases the source buffer)', () => {
  const code = stripJs(APP_SRC);
  if (/\.clone\s*\(/.test(code)) throw new Error('.clone() found — use new cv.Mat(); src.copyTo(dst)');
  // the warning must still be documented for whoever edits the detector next
  ok(/clone\(\) aliases the source buffer/.test(APP_SRC), 'the clone() landmine is undocumented');
});
T('no external network origin is contacted by the client at all', () => {
  const urls = (ALL_CLIENT.match(/\b(https?|wss?):\/\/[^\s'"`)]+/g) || [])
    .map((u) => u.replace(/[;,.]+$/, ''));
  const allowed = urls.filter((u) => u === 'ws://localhost:8787' || /^http:\/\/www\.w3\.org/.test(u));
  const bad = [...new Set(urls.filter((u) => !allowed.includes(u)))];
  ok(bad.length === 0, `outbound origins: ${JSON.stringify(bad)}`);
  measured.client_outbound_origins = JSON.stringify([...new Set(allowed)]);
});
T('the brain address is ws://localhost:8787', () => eq(A.WS_URL, 'ws://localhost:8787'));

// the pinning arithmetic, computed rather than typed
const OPENCV_PINNED_BYTES = 11386540;   // @techstark/opencv-js@4.11.0-release.1
const OPENCV_5_BYTES = 13298869;        // @techstark/opencv-js@5.0.0
T('the README documents the pin and the byte delta correctly', () => {
  ok(README.includes('4.11.0-release.1'), 'pin not documented');
  ok(README.includes('11,386,540'), 'pinned byte count not documented');
  ok(README.includes('13,298,869'), '5.0.0 byte count not documented');
  const delta = OPENCV_5_BYTES - OPENCV_PINNED_BYTES;
  measured.opencv_pinned_bytes = OPENCV_PINNED_BYTES.toLocaleString('en-US');
  measured.opencv_5_bytes = OPENCV_5_BYTES.toLocaleString('en-US');
  measured.opencv_delta_bytes = delta.toLocaleString('en-US');
  measured.opencv_delta_mb = `${(delta / 1e6).toFixed(2)} MB`;
  ok(Math.abs(delta / 1e6 - 1.91) < 0.005, `delta is ${(delta / 1e6).toFixed(3)} MB, not 1.91`);
  ok(README.includes('1.91'), 'the 1.91 MB penalty is not stated');
});

G('14. cold-load byte budget — measured, not asserted');
T('the PWA shell is small (zero weights means it stays small)', () => {
  const files = ['index.html', 'app.js', 'style.css'];
  let total = 0;
  for (const f of files) {
    const n = statSync(join(HERE, f)).size;
    measured[`bytes_${f.replace('.', '_')}`] = n.toLocaleString('en-US');
    total += n;
  }
  measured.bytes_shell_total = total.toLocaleString('en-US');
  measured.bytes_shell_plus_opencv = (total + OPENCV_PINNED_BYTES).toLocaleString('en-US');
  ok(total < 200 * 1024, `shell is ${total} bytes, budget 204800`);
});
T('index.html wires the elements app.js reaches for', () => {
  for (const id of ['cam', 'raw', 'rect', 'chrome', 'total', 'amber', 'lock', 'lockdetail',
    'lines', 'done', 'ack', 'banner', 'reason', 'fps', 'cvstat']) {
    ok(new RegExp(`id=["']${id}["']`).test(HTML), `#${id} missing from index.html`);
  }
});
T('index.html has no inline script and no build step', () => {
  ok(!/<script(?![^>]*\bsrc=)/i.test(HTML), 'inline <script> present');
  ok(/src=["']\.\/app\.js["']/.test(HTML), 'app.js not linked');
  ok(!/import\s+.*from\s+['"][^.]/.test(APP_SRC), 'app.js has a bare-specifier import (needs a bundler)');
});
T('the split preview (raw vs rectified crop) exists in the markup', () => {
  ok(/id=["']raw["']/.test(HTML) && /id=["']rect["']/.test(HTML));
  ok(/rectified/i.test(HTML), 'the rectified pane is not labelled');
  ok(/\.chrome-amber|\.chrome-green|\.chrome-red/.test(CSS.replace(/\s+/g, '')) || /chrome-amber/.test(CSS),
    'chrome colours missing from style.css');
});
T('amber lines are styled hatched and marked excluded', () => {
  ok(/line-amber/.test(CSS), 'no amber line style');
  ok(/repeating-linear-gradient/.test(CSS), 'amber is not hatched');
  ok(/EXCLUDED|excluded/.test(APP_SRC), 'amber lines are not marked excluded');
});

// ============================== 15. browser shell under a DOM stub ========
// There is no browser here, but "no browser" is not a reason to ship the shell
// unexecuted. This installs a minimal DOM, re-imports app.js so boot() runs,
// and drives the real handlers. It catches what review misses: an element id
// that does not exist, a render path that throws, a handler wired to the wrong
// action, a mask that is not applied.
//
// Two shells are booted:
//   A. ./vendor/opencv.js ABSENT  -> the abstention path
//   B. ./vendor/opencv.js present -> the full locked path, with a cv stub whose
//      detectMarkers returns the same four marker quads the Python vector
//      implies, so the geometry downstream of detection is the real code.

// Captured ONCE, before any shell replaces the global: each shell installs a
// recording setTimeout, so a shell that captured the previous shell's stub
// would await a promise that is never scheduled.
const REAL_SET_TIMEOUT = globalThis.setTimeout;

const noop = () => {};
function ctx2d() {
  return new Proxy({}, {
    get: (t, k) => (k in t ? t[k] : noop),
    set: (t, k, v) => { t[k] = v; return true; },
  });
}
function makeEl(made, tag = 'div') {
  const el = {
    tagName: tag, children: [], listeners: {}, dataset: {}, style: {},
    className: '', textContent: '', hidden: false, disabled: false,
    src: '', width: 0, height: 0, srcObject: null, onload: null, onerror: null,
    classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
    appendChild(c) { el.children.push(c); return c; },
    append(...c) { el.children.push(...c); },
    replaceChildren(...c) { el.children = c; },
    addEventListener(t, f) { (el.listeners[t] ||= []).push(f); },
    removeEventListener: noop,
    getBoundingClientRect: () => ({ left: 0, top: 0, width: el.width || 100, height: el.height || 100 }),
    getContext: ctx2d,
    play: () => Promise.resolve(),
    fire(t, ev) { (el.listeners[t] || []).forEach((f) => f(ev)); },
  };
  made.push(el);
  return el;
}

/** A cv stub that reports the four marker quads implied by the Python vector. */
function makeCvStub(quadsById) {
  class Mat { constructor() { this.rows = 0; } delete() {} intAt(i) { return this.vals[i]; } }
  class MatVector { constructor() { this.items = []; } get(i) { return this.items[i]; } delete() {} }
  const cv = {
    DICT_4X4_50: 0, CV_64F: 6, INTER_LINEAR: 1, BORDER_CONSTANT: 0, COLOR_RGBA2GRAY: 11,
    Mat, MatVector,
    Size: class { constructor(w, h) { this.w = w; this.h = h; } },
    Scalar: class { },
    getBuildInformation: () => 'stub build',
    getPredefinedDictionaryImpl: () => ({}),
    aruco_DetectorParameters: class { constructor() { this.cornerRefinementMethod = 0; } },
    aruco_RefineParameters: class { constructor(a, b, c) { this.a = a; this.b = b; this.c = c; } },
    aruco_ArucoDetector: class {
      detectMarkers(gray, corners, ids) {
        cv.detectCalls++;
        corners.items = [0, 1, 2, 3].map((i) => ({
          data32F: Float64Array.from(quadsById[i].flat()),
        }));
        ids.rows = 4; ids.vals = [0, 1, 2, 3];
      }
    },
    imread: () => new Mat(),
    imshow: () => { cv.imshowCalls++; },
    cvtColor: noop,
    matFromArray: () => new Mat(),
    warpPerspective: (src, dst, m, size) => { cv.warpCalls++; cv.lastWarpSize = [size.w, size.h]; },
    detectCalls: 0, imshowCalls: 0, warpCalls: 0, lastWarpSize: null,
  };
  return cv;
}

/**
 * Boot a shell. `cvStub` null means ./vendor/opencv.js is absent.
 * Returns handles for driving the app: DOM by id, sockets, timers, frame pump.
 */
async function bootShell({ cvStub = null, tag = 'a' }) {
  const made = [];
  const ids = ['cam', 'raw', 'rect', 'chrome', 'total', 'amber', 'lock', 'lockdetail',
    'lines', 'done', 'ack', 'banner', 'reason', 'fps', 'cvstat'];
  const byId = Object.fromEntries(ids.map((i) => [i, makeEl(made, i === 'cam' ? 'video' : 'div')]));
  byId.raw.width = 1280; byId.raw.height = 960;

  const head = makeEl(made, 'head');
  head.appendChild = (c) => {
    head.children.push(c);
    queueMicrotask(() => {
      if (cvStub) { globalThis.cv = cvStub; if (c.onload) c.onload(); }
      else if (c.onerror) c.onerror(new Error('ENOENT ./vendor/opencv.js'));
    });
    return c;
  };
  if (!cvStub) delete globalThis.cv;

  globalThis.document = {
    readyState: 'complete', head,
    getElementById: (i) => byId[i] ?? null,
    createElement: (t) => makeEl(made, t),
    addEventListener: noop,
  };
  globalThis.window = { addEventListener: noop };

  let clock = 1000, rafBudget = 3, lastCb = null;
  Object.defineProperty(globalThis, 'performance', {
    configurable: true, writable: true, value: { now: () => clock },
  });
  globalThis.requestAnimationFrame = (cb) => {
    lastCb = cb;
    if (rafBudget > 0) { rafBudget--; clock += 40; cb(clock); }
    return 1;
  };
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true, writable: true,
    value: {
      mediaDevices: {
        getUserMedia: async () => ({
          getVideoTracks: () => [{ getSettings: () => ({ width: 1280, height: 960 }) }],
        }),
      },
    },
  });

  const sockets = [];
  globalThis.WebSocket = class {
    constructor(url) {
      this.url = url; this.readyState = 1; this.sent = [];
      this.onopen = this.onmessage = this.onclose = this.onerror = null;
      sockets.push(this);
    }
    send(m) { this.sent.push(m); }
    close() { this.readyState = 3; if (this.onclose) this.onclose(); }
  };

  const timers = [];
  globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };

  await import('data:text/javascript;charset=utf-8;base64,'
    + Buffer.from(`${APP_SRC}\n// shell-${tag}`, 'utf8').toString('base64'));
  await new Promise((r) => REAL_SET_TIMEOUT(r, 30));

  return {
    byId, sockets, timers, made, cv: cvStub, realSetTimeout: REAL_SET_TIMEOUT,
    /** Run n more animation frames, advancing the clock past the 30fps gate. */
    pump(n = 1) {
      for (let i = 0; i < n; i++) {
        rafBudget += 1;
        if (lastCb && rafBudget > 0) { rafBudget--; clock += 40; lastCb(clock); }
      }
    },
  };
}

G('15a. browser shell — OpenCV ABSENT, the abstention path');
const A_ = await bootShell({ cvStub: null, tag: 'a' });

T('boot() runs to completion and connects to the brain', () => {
  ok(A_.sockets.length >= 1, 'no WebSocket was opened');
  eq(A_.sockets[0].url, 'ws://localhost:8787');
});
T('a missing ./vendor/opencv.js abstains loudly instead of throwing', () => {
  ok(/OpenCV absent/.test(A_.byId.cvstat.textContent), A_.byId.cvstat.textContent);
  ok(A_.byId.cvstat.textContent.includes(A.Reason.OPENCV_ABSENT), 'reason code not shown');
  eq(A_.byId.cvstat.className, 'cvstat cvstat-absent');
});
T('without OpenCV the app refuses to lock and retains NO crop', () => {
  eq(A_.byId.lock.textContent, 'NO LOCK');
  eq(A_.byId.rect.dataset.policy, A.RETAIN_NOTHING, 'a crop was retained without a lock');
});
T('without a mat lock a placement from the brain is REFUSED', () => {
  A_.sockets[0].onopen();
  A_.sockets[0].onmessage({ data: JSON.stringify({ type: 'placement', itemId: 'x', pricePaise: 500, centreMm: [50, 50] }) });
  eq(A_.byId.lines.children.length, 0, 'a line was billed without a mat lock');
  ok(/refused/.test(A_.byId.reason.textContent), A_.byId.reason.textContent);
  eq(A_.byId.total.textContent, '₹0.00');
});
T('the initial render shows a zero total, grey chrome, DONE disabled', () => {
  ok(A_.byId.chrome.className.includes('chrome-grey'), A_.byId.chrome.className);
  eq(A_.byId.done.disabled, true);
});
T('the frame loop ran and reported fps', () => {
  ok(/fps/.test(A_.byId.fps.textContent), `fps element says "${A_.byId.fps.textContent}"`);
});

G('15b. browser shell — OpenCV present, the full locked path');
const CVQ = markerQuads(PY.H_frame_to_buffer);
const B_ = await bootShell({ cvStub: makeCvStub(CVQ), tag: 'b' });
const bsock = B_.sockets[0];

T('the vendored OpenCV is reported as loaded', () => {
  ok(/4\.11\.0-release\.1/.test(B_.byId.cvstat.textContent), B_.byId.cvstat.textContent);
  eq(B_.byId.cvstat.className, 'cvstat cvstat-ok');
});
T('four detected markers produce a MAT LOCK', () => {
  B_.pump(2);
  eq(B_.byId.lock.textContent, 'MAT LOCK');
  ok(B_.cv.detectCalls > 0, 'detectMarkers was never called');
  ok(/scale .* rmse .* tilt/.test(B_.byId.lockdetail.textContent), B_.byId.lockdetail.textContent);
});
T('INVARIANT 4 — on lock, the ONLY buffer produced is the 840x1188 crop', () => {
  eq(B_.byId.rect.dataset.policy, A.RETAIN_RECTIFIED);
  ok(B_.cv.warpCalls > 0, 'warpPerspective was never called');
  eq(JSON.stringify(B_.cv.lastWarpSize), JSON.stringify([840, 1188]), 'crop is not the metric buffer');
  ok(B_.cv.imshowCalls > 0, 'the crop was never written to the rectified canvas');
  measured.shell_warp_calls = B_.cv.warpCalls;
});
T('brain messages drive the real reducer and repaint the DOM', () => {
  bsock.onopen();
  bsock.onmessage({ data: JSON.stringify({ type: 'placement', itemId: 'p1', name: 'Parle-G', pricePaise: 2000, centreMm: [100, 200] }) });
  bsock.onmessage({ data: JSON.stringify({ type: 'placement', itemId: 'p2', name: null, pricePaise: null, centreMm: [180, 300] }) });
  eq(B_.byId.lines.children.length, 2, 'lines did not render');
  ok(B_.byId.lines.children[1].className.includes('line-amber'), 'the unpriced line is not amber');
  eq(B_.byId.lines.children[1].children[1].textContent, 'AMBER · excluded');
});
T('an exit commits and the DOM total climbs; amber stays EXCLUDED', () => {
  bsock.onmessage({ data: JSON.stringify({ type: 'exit', itemId: 'p1', tap: true }) });
  bsock.onmessage({ data: JSON.stringify({ type: 'exit', itemId: 'p2', tap: true }) });
  eq(B_.byId.total.textContent, '₹20.00', 'the amber line leaked into the total');
  ok(/1 amber/.test(B_.byId.amber.textContent), B_.byId.amber.textContent);
  eq(B_.byId.amber.hidden, false);
  eq(B_.byId.done.disabled, false, 'DONE should now be available');
});
T('glyphs paint without throwing and are hit-testable in perspective', () => {
  B_.pump(1);   // paintGlyphs rebuilds the glyph quads from the live lines
  const Hinv = A.invert3x3(PY.H_frame_to_buffer);
  const b = A.mmToBuffer([100, 200]);
  const [cx, cy] = A.applyH(Hinv, b[0], b[1]);
  ok(cx > 0 && cx < 1280 && cy > 0 && cy < 960, `glyph centre off-frame: ${cx},${cy}`);
  B_.byId.raw.fire('click', { clientX: cx, clientY: cy });
  eq(B_.byId.total.textContent, '₹0.00', 'tapping the projected glyph did not revert the line');
  measured.shell_glyph_tap_frame_px = `${cx.toFixed(1)},${cy.toFixed(1)}`;
});
T('a tap on empty mat reverts nothing', () => {
  bsock.onmessage({ data: JSON.stringify({ type: 'placement', itemId: 'p3', name: 'Soap', pricePaise: 3500, centreMm: [60, 90] }) });
  bsock.onmessage({ data: JSON.stringify({ type: 'exit', itemId: 'p3', tap: true }) });
  B_.pump(1);
  eq(B_.byId.total.textContent, '₹35.00');
  B_.byId.raw.fire('click', { clientX: 5, clientY: 5 });
  eq(B_.byId.total.textContent, '₹35.00', 'a stray tap reverted a line');
});
T('an unknown brain message is reported, not guessed at', () => {
  bsock.onmessage({ data: JSON.stringify({ type: 'wat', itemId: 'x' }) });
  ok(/ignored unknown brain message/.test(B_.byId.reason.textContent), B_.byId.reason.textContent);
});
T('malformed JSON from the brain does not throw', () => {
  bsock.onmessage({ data: '{not json' }); ok(true);
});
T('DONE sends an intent and the chrome stays AMBER — nothing authorised', () => {
  const before = bsock.sent.length;
  B_.byId.done.fire('click');
  eq(bsock.sent.length, before + 1, 'DONE sent nothing');
  const msg = JSON.parse(bsock.sent[bsock.sent.length - 1]);
  eq(msg.type, 'done'); eq(msg.amountPaise, 3500);
  ok(msg.sessionId && msg.sessionId.startsWith('sess-'), `bad session id ${msg.sessionId}`);
  ok(B_.byId.chrome.className.includes('chrome-amber'), B_.byId.chrome.className);
  eq(B_.byId.chrome.dataset.state, A.State.AWAITING_SETTLEMENT);
  eq(B_.byId.done.disabled, true, 'the basket is still open after DONE');
  B_.sessionId = msg.sessionId;
});
T('a foreign session verdict is discarded — chrome does NOT go green', () => {
  bsock.onmessage({ data: JSON.stringify({ type: 'verdict', verdict: { eventId: 'e1', event: 'payment.captured', sessionId: 'someone-else', amountPaise: 3500, green: true, signatureValid: true } }) });
  ok(!B_.byId.chrome.className.includes('chrome-green'), 'a foreign session went green');
  ok(B_.byId.reason.textContent.includes(A.Reason.FOREIGN_SESSION), B_.byId.reason.textContent);
});
T('an unsigned verdict for THIS session is discarded', () => {
  bsock.onmessage({ data: JSON.stringify({ type: 'verdict', verdict: { eventId: 'e2', event: 'payment.captured', sessionId: B_.sessionId, amountPaise: 3500, green: true, signatureValid: false } }) });
  ok(!B_.byId.chrome.className.includes('chrome-green'), 'an unsigned verdict went green');
  ok(B_.byId.reason.textContent.includes(A.Reason.BAD_SIGNATURE), B_.byId.reason.textContent);
});
T('a signed verdict for the WRONG amount is a RED HOLD', () => {
  bsock.onmessage({ data: JSON.stringify({ type: 'verdict', verdict: { eventId: 'e3', event: 'payment.captured', sessionId: B_.sessionId, amountPaise: 3499, green: true, signatureValid: true } }) });
  ok(B_.byId.chrome.className.includes('chrome-red'), B_.byId.chrome.className);
  eq(B_.byId.chrome.dataset.state, A.State.AMOUNT_MISMATCH);
});

G('15c. browser shell — the green path, end to end through the wire');
const C_ = await bootShell({ cvStub: makeCvStub(CVQ), tag: 'c' });
const csock = C_.sockets[0];
T('a fully valid verdict, and only then, floods the chrome GREEN', () => {
  C_.pump(2);
  csock.onopen();
  csock.onmessage({ data: JSON.stringify({ type: 'placement', itemId: 'q1', name: 'Tea', pricePaise: 12500, centreMm: [120, 150] }) });
  csock.onmessage({ data: JSON.stringify({ type: 'exit', itemId: 'q1', tap: true }) });
  eq(C_.byId.total.textContent, '₹125.00');
  C_.byId.done.fire('click');
  const msg = JSON.parse(csock.sent[csock.sent.length - 1]);
  eq(msg.amountPaise, 12500);
  ok(C_.byId.chrome.className.includes('chrome-amber'), 'chrome went green before the webhook');
  csock.onmessage({ data: JSON.stringify({ type: 'verdict', verdict: { eventId: 'g1', event: 'payment_link.paid', sessionId: msg.sessionId, amountPaise: 12500, green: true, signatureValid: true } }) });
  ok(C_.byId.chrome.className.includes('chrome-green'), C_.byId.chrome.className);
  eq(C_.byId.chrome.dataset.state, A.State.PAID);
  eq(C_.byId.total.textContent, '₹125.00');
});
T('losing the socket shows the AMBER PENDING banner and schedules a retry', () => {
  const before = C_.timers.length;
  csock.close();
  eq(C_.byId.banner.hidden, false, 'the offline banner did not appear');
  ok(/AMBER PENDING/.test(C_.byId.banner.textContent), C_.byId.banner.textContent);
  ok(/nothing authorised/.test(C_.byId.banner.textContent), 'the banner must say nothing is authorised');
  ok(C_.timers.length > before, 'no reconnect was scheduled');
  const wait = C_.timers[C_.timers.length - 1].ms;
  ok(wait >= A.WS_BASE_MS * 0.5 && wait <= A.WS_CAP_MS, `backoff ${wait}ms out of range`);
  measured.shell_first_backoff_ms = wait;
});
T('losing the mat mid-session freezes the total and drops the crop', () => {
  C_.cv.aruco_ArucoDetector.prototype.detectMarkers = (g, corners, ids) => {
    corners.items = []; ids.rows = 0; ids.vals = [];
  };
  C_.pump(2);
  eq(C_.byId.lock.textContent, 'NO LOCK');
  eq(C_.byId.rect.dataset.policy, A.RETAIN_NOTHING, 'a crop survived losing the mat');
  ok(/no markers detected/.test(C_.byId.lockdetail.textContent), C_.byId.lockdetail.textContent);
});
T('the shell created every element app.js reaches for', () => {
  measured.shell_dom_nodes_created = A_.made.length + B_.made.length + C_.made.length;
  ok(measured.shell_dom_nodes_created > 40, 'the shells barely rendered');
});

// hand the process back its real timer so the run can exit cleanly
globalThis.setTimeout = C_.realSetTimeout;


// ============================================================== report =====
console.log('\n──────────────────────────────────────────────────────────────');
console.log('MEASURED NUMBERS (produced by this run)');
for (const [k, v] of Object.entries(measured)) console.log(`  ${k.padEnd(34)} ${v}`);

if (failures.length) {
  console.log('\nFAILURES');
  for (const f of failures) console.log(`  ✗ ${f}`);
}
console.log('\n──────────────────────────────────────────────────────────────');
console.log(`${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
