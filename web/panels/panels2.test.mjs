/* GAWAAH counter — node self-test for the CHILLA, SAAF and LEDGER panels.
 *
 *   cd /Users/agnik/Desktop/razor && node web/panels/panels2.test.mjs
 *
 * There is no browser here. The panels are written as pure functions over a
 * document-like object, so the whole render path — including the SVG mini-map,
 * the contact sheet and the VERIFY button — runs against the ~60-line DOM shim
 * below.
 *
 * Two kinds of test carry most of the weight:
 *
 *   CROSS-LANGUAGE CONSTANT CHECKS. Every threshold the panels print is also a
 *   constant in gawaah/*.py. The tests below shell out to the repo's own venv,
 *   read the Python values, and assert equality. A panel that quietly claims a
 *   better Nyquist floor, a looser blur gate or a different verdict set than the
 *   brain enforces is a lie told in a nice font, and this is what catches it.
 *
 *   A REAL HASH CHAIN. The ledger tests write an actual chain with
 *   gawaah.ledger.Ledger, verify it in JavaScript, then tamper with it four
 *   different ways and assert the JS verifier names the same line Python's own
 *   verifier does. That is the claim the VERIFY button makes, tested end to end
 *   across the language boundary rather than against a JS-made fixture.
 */
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { tmpdir } from 'node:os';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB = dirname(HERE);
const REPO = dirname(WEB);
const PY = join(REPO, '.venv', 'bin', 'python');

const CHILLA_PATH = join(HERE, 'chilla.js');
const SAAF_PATH = join(HERE, 'saaf.js');
const LEDGER_PATH = join(HERE, 'ledger.js');

const C = await import(pathToFileURL(CHILLA_PATH).href);
const S = await import(pathToFileURL(SAAF_PATH).href);
const L = await import(pathToFileURL(LEDGER_PATH).href);

// ---------------------------------------------------------------- harness --
let pass = 0, fail = 0, group = '';
const failures = [];
const measured = {};
function T(name, fn) {
  try { fn(); pass++; }
  catch (e) { fail++; failures.push(`${group} :: ${name}\n      ${e.stack || e.message}`); }
}
async function TA(name, fn) {
  try { await fn(); pass++; }
  catch (e) { fail++; failures.push(`${group} :: ${name}\n      ${e.stack || e.message}`); }
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
function has(hay, needle, msg) {
  if (!String(hay).includes(needle)) {
    throw new Error(`${msg || 'contains'}: ${JSON.stringify(needle)} not in ${JSON.stringify(String(hay).slice(0, 400))}`);
  }
}
function hasNot(hay, needle, msg) {
  if (String(hay).includes(needle)) {
    throw new Error(`${msg || 'must not contain'}: ${JSON.stringify(needle)} IS in the text`);
  }
}

// ------------------------------------------------------------- DOM shim ----
// Small on purpose: if a panel needs more of the DOM than this, it is doing
// something a pure render function should not be doing.
function makeEl(tag, ns = null) {
  const el = {
    tagName: tag, ns, children: [], attrs: {}, dataset: {}, style: {},
    className: '', textContent: '', listeners: {},
    appendChild(c) { el.children.push(c); return c; },
    append(...c) { el.children.push(...c); },
    replaceChildren(...c) { el.children = c.slice(); },
    setAttribute(k, v) { el.attrs[k] = String(v); },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(el.attrs, k) ? el.attrs[k] : null; },
    addEventListener(t, f) { (el.listeners[t] ||= []).push(f); },
    removeEventListener() {},
    fire(t, ev) { return (el.listeners[t] || []).map((f) => f(ev)); },
  };
  return el;
}
function makeDoc(ids = []) {
  const byId = Object.fromEntries(ids.map((i) => [i, makeEl('section')]));
  return {
    byId,
    createElement: (t) => makeEl(t),
    createElementNS: (ns, t) => makeEl(t, ns),
    getElementById: (i) => byId[i] ?? null,
  };
}
const DOC = makeDoc();

function nodes(root) {
  const out = [];
  (function walk(n) { out.push(n); for (const c of n.children) walk(c); })(root);
  return out;
}
function text(root) { return nodes(root).map((n) => n.textContent).join(' '); }
/** Matches className AND the `class` attribute, because SVG nodes use the latter. */
function byClass(root, cls) {
  return nodes(root).filter((n) => `${n.className} ${n.attrs.class ?? ''}`.split(/\s+/).includes(cls));
}
function one(root, cls) {
  const m = byClass(root, cls);
  if (m.length !== 1) throw new Error(`expected exactly one .${cls}, found ${m.length}`);
  return m[0];
}
function byData(root, key, val) {
  return nodes(root).filter((n) => n.dataset[key] === String(val));
}
function tagsOf(root, tag) { return nodes(root).filter((n) => n.tagName === tag); }

/**
 * INVARIANT 2, ASSERTED ON THE PIXELS. Walks a rendered subtree and refuses any
 * green or fraud-red in a class name, a data attribute or a style value. Prose
 * is exempt on purpose: these panels are REQUIRED to say the words "can never
 * show green", and a scan that banned the word would ban the promise.
 */
const FORBIDDEN = [
  /green/i, /#0f0\b/i, /#00ff00/i, /#3ddc84/i, /rgb\(\s*0\s*,\s*2[0-9]{2}/i,
  /\bred\b/i, /#f00\b/i, /#ff0000/i, /crimson/i, /\bdanger\b/i, /\bsuccess\b/i,
];
function assertNoGreenNoRed(root, where) {
  for (const n of nodes(root)) {
    const surfaces = [
      ['class', String(n.className)],
      ...Object.entries(n.dataset).map(([k, v]) => [`data-${k}`, String(v)]),
      ...Object.entries(n.style).map(([k, v]) => [`style.${k}`, String(v)]),
      ...Object.entries(n.attrs).filter(([k]) => k !== 'alt' && k !== 'aria-label')
        .map(([k, v]) => [`attr:${k}`, String(v)]),
    ];
    for (const [what, v] of surfaces) {
      for (const re of FORBIDDEN) {
        if (re.test(v)) {
          throw new Error(`${where}: <${n.tagName}> ${what}=${JSON.stringify(v)} matches ${re}`);
        }
      }
    }
  }
}

// ------------------------------------------------------------ python probe -
function python(code) {
  const r = spawnSync(PY, ['-c', code], { cwd: REPO, encoding: 'utf8' });
  if (r.status !== 0) throw new Error(`python failed: ${r.stderr || r.error}`);
  return r.stdout;
}
let PYC = null, PYS = null, pyAvailable = true;
try {
  const out = python(`
import json, gawaah.chilla as c, gawaah.saaf as s
print(json.dumps({
 "c": {"NYQUIST_PX": c.NYQUIST_PX, "REF_MM": c.REFERENCE_STRING_STROKE_MM,
       "TS_MM": c.SCREEN_TIMESTAMP_STROKE_MM, "HERO_MM": c.HERO_AMOUNT_CAP_MM,
       "SR": c.SUPER_RES_FACTOR, "PX_PER_MM": c.PX_PER_MM,
       "ref_px": c.REFERENCE_STRING.size_px, "ref_short": c.REFERENCE_STRING.shortfall_x,
       "ref_explain": c.REFERENCE_STRING.explain(),
       "hero_explain": c.HERO_AMOUNT.explain(),
       "hero_px": c.HERO_AMOUNT.size_px, "hero_readable": c.HERO_AMOUNT.readable,
       "ref_sr": c.REFERENCE_STRING.readable_with_2x_sr,
       "VERDICTS": list(c.VERDICTS), "LIGHT": c.LIGHT_FOR_VERDICT,
       "WIN": c.DEFAULT_WINDOW_S, "STALE": c.DEFAULT_STALE_THRESHOLD_S,
       "K": c.CHILLAR_SPACE,
       "risk": {str(n): c.collision_risk(n) for n in (0,1,2,5,10,30,99,200)},
       "anyrisk": {str(n): c.any_collision_risk(n) for n in (1,2,10,50,200)},
       "maxfor": {"0.05": c.max_payments_for_risk(0.05), "0.01": c.max_payments_for_risk(0.01)},
       "DET": list(c.DETECTION_REASONS), "ABST": list(c.ABSTENTION_REASONS),
       "NEVER": list(c.NEVER_READ), "PBOX": list(c.PLACEMENT_BOX_MM),
       "MIN_AREA": c.MIN_AREA_MM2, "MAX_AREA": c.MAX_AREA_MM2,
       "MIN_RECT": c.MIN_RECTANGULARITY, "MIN_ASP": c.MIN_ASPECT, "MAX_ASP": c.MAX_ASPECT,
       "EDGE": c.EDGE_MARGIN_MM, "MASKF": c.MAX_MASK_FRACTION,
       "BRIGHT": c.MIN_BRIGHTNESS_DELTA, "COUPL": c.MAX_ILLUM_COUPLING},
 "s": {"SCALE": s.DEFAULT_SCALE, "BLUR_VAR_MIN": s.DEFAULT_BLUR_VAR_MIN,
       "BLUR_REL_MIN": s.DEFAULT_BLUR_REL_MIN, "MAX_SHIFT": s.DEFAULT_MAX_SHIFT_PX,
       "SAT_LEVEL": s.DEFAULT_SAT_LEVEL, "SAT_FRAC_MAX": s.DEFAULT_SAT_FRAC_MAX,
       "MAX_BLUR_SCORE": s.DEFAULT_MAX_BLUR_SCORE, "MTF50": s.BLUR_SCORE_MTF50_CYC_PX,
       "MIN_SHIFT": s.DEFAULT_MIN_SHIFT_PX, "MIN_DIV": s.DEFAULT_MIN_DIVERSITY,
       "R": [s.R_REFERENCE, s.R_OK, s.R_BLUR, s.R_GLARE, s.R_ECC_FAILED,
             s.R_SHIFT_TOO_LARGE, s.R_WARP_NOT_FINITE, s.R_DEFOCUS],
       "W": [s.W_NONE, s.W_ALL_REJECTED, s.W_SINGLE_FRAME, s.W_NO_DIVERSITY,
             s.W_DEGENERATE_PHASE, s.W_UNIFORMLY_DEFOCUSED]}}))
`);
  const j = JSON.parse(out);
  PYC = j.c; PYS = j.s;
} catch (e) {
  pyAvailable = false;
  console.log(`\n!! python cross-checks CANNOT RUN: ${e.message}`);
}

// ===========================================================================
G('CHILLA — the optical budget (the reason this panel exists)');
// ===========================================================================

T('the reference stroke is 0.19 mm and lands at 0.54 px', () => {
  near(C.REFERENCE_STRING.sizePx, 0.5374011544, 1e-9, 'reference stroke px');
  eq(C.REFERENCE_STRING.sizePx.toFixed(2), '0.54');
  measured.chilla_reference_stroke_px = C.REFERENCE_STRING.sizePx.toFixed(4);
});
T('0.54 px is BELOW the 2 px Nyquist floor, so it is not readable', () => {
  eq(C.REFERENCE_STRING.readable, false);
  eq(C.NYQUIST_PX, 2.0);
});
T('the shortfall is reported as a factor, 3.72x', () => {
  near(C.REFERENCE_STRING.shortfallX, 3.7216146, 1e-6);
  eq(C.REFERENCE_STRING.shortfallX.toFixed(2), '3.72');
  measured.chilla_reference_shortfall_x = C.REFERENCE_STRING.shortfallX.toFixed(4);
});
T('even a 2x super-resolution claim does not reach the floor', () => {
  eq(C.REFERENCE_STRING.readableWith2xSr, false, '0.54 x 2 = 1.07 px, still under 2');
  near(C.REFERENCE_STRING.sizePx * 2, 1.0748, 1e-3);
});
T('the on-screen timestamp is the same verdict, so we window on OUR clock', () => {
  eq(C.SCREEN_TIMESTAMP.readable, false);
  eq(C.SCREEN_TIMESTAMP.sizeMm, C.REFERENCE_STRING.sizeMm);
});
T('the hero amount IS above the floor — which is why an amount can be read', () => {
  eq(C.HERO_AMOUNT.readable, true);
  near(C.HERO_AMOUNT.sizePx, 12.5865, 1e-3);
  measured.chilla_hero_amount_px = C.HERO_AMOUNT.sizePx.toFixed(4);
});
T('explain() says "not present in the signal", not "hard to read"', () => {
  has(C.REFERENCE_STRING.explain, 'not present in the signal');
  hasNot(C.REFERENCE_STRING.explain, 'blurry');
  has(C.HERO_AMOUNT.explain, 'above the floor');
});
T('legibility refuses non-positive geometry rather than dividing by zero', () => {
  throws(() => C.legibility('x', 0), 'zero mm');
  throws(() => C.legibility('x', 1, 0), 'zero px/mm');
  throws(() => C.legibility('x', -1), 'negative mm');
});
T('reading the reference string is a structural refusal carrying the arithmetic', () => {
  let err = null;
  try { C.readReferenceString(); } catch (e) { err = e; }
  ok(err instanceof C.ChillaRefusal, 'expected ChillaRefusal');
  has(err.message, '0.54 px');
  has(err.message, 'not present in the signal');
});
T('reading the on-screen clock is refused the same way', () => {
  let err = null;
  try { C.readScreenTimestamp(); } catch (e) { err = e; }
  ok(err instanceof C.ChillaRefusal);
  has(err.message, 'Nyquist floor');
});
T('the never-read list names the fields a keen engineer would reach for', () => {
  for (const f of ['reference_string', 'utr', 'rrn', 'screen_timestamp', 'payer_vpa']) {
    ok(C.NEVER_READ.includes(f), `${f} missing from NEVER_READ`);
  }
});
T('THE SENTENCE is present, with the computed numbers in it', () => {
  has(C.NYQUIST_SENTENCE, '0.19mm');
  has(C.NYQUIST_SENTENCE, '0.54px');
  has(C.NYQUIST_SENTENCE, '2px Nyquist floor');
  has(C.NYQUIST_SENTENCE, 'NOT IN THE SIGNAL');
  has(C.NYQUIST_SENTENCE, '(amount, time window)');
});

// ===========================================================================
G('CHILLA — cross-checked against gawaah/chilla.py');
// ===========================================================================

if (!pyAvailable) {
  console.log('   (skipped: the venv python could not be run)');
} else {
  T('px/mm is the same number the brain uses', () => {
    near(C.REFERENCE_STRING.pxPerMm, PYC.PX_PER_MM, 1e-12);
    measured.px_per_mm = PYC.PX_PER_MM;
  });
  T('every optical constant matches the Python module', () => {
    eq(C.NYQUIST_PX, PYC.NYQUIST_PX);
    eq(C.REFERENCE_STRING_STROKE_MM, PYC.REF_MM);
    eq(C.SCREEN_TIMESTAMP_STROKE_MM, PYC.TS_MM);
    eq(C.HERO_AMOUNT_CAP_MM, PYC.HERO_MM);
    eq(C.SUPER_RES_FACTOR, PYC.SR);
  });
  T('the computed size_px and shortfall match Python to 1e-12', () => {
    near(C.REFERENCE_STRING.sizePx, PYC.ref_px, 1e-12);
    near(C.REFERENCE_STRING.shortfallX, PYC.ref_short, 1e-12);
    near(C.HERO_AMOUNT.sizePx, PYC.hero_px, 1e-12);
    eq(C.HERO_AMOUNT.readable, PYC.hero_readable);
    eq(C.REFERENCE_STRING.readableWith2xSr, PYC.ref_sr);
  });
  T('explain() is BYTE-IDENTICAL to the Python sentence', () => {
    eq(C.REFERENCE_STRING.explain, PYC.ref_explain);
    eq(C.HERO_AMOUNT.explain, PYC.hero_explain);
  });
  T('the verdict set matches', () => {
    eq(JSON.stringify(C.VERDICTS), JSON.stringify(PYC.VERDICTS));
  });
  T('Python also says every verdict is AMBER', () => {
    for (const v of PYC.VERDICTS) eq(PYC.LIGHT[v], 'AMBER', `python light for ${v}`);
    for (const v of PYC.VERDICTS) eq(C.lightFor(v), PYC.LIGHT[v], `js light for ${v}`);
  });
  T('the detection-reason tuple matches, in order', () => {
    eq(JSON.stringify(C.DETECTION_REASONS), JSON.stringify(PYC.DET));
    eq(JSON.stringify(C.ABSTENTION_REASONS), JSON.stringify(PYC.ABST));
  });
  T('every detection reason has an operator note', () => {
    for (const r of PYC.DET) ok(typeof C.REASON_NOTES[r] === 'string' && C.REASON_NOTES[r].length > 20, r);
  });
  T('the never-read list matches', () => {
    eq(JSON.stringify(C.NEVER_READ), JSON.stringify(PYC.NEVER));
  });
  T('window, staleness threshold and key space match', () => {
    eq(C.DEFAULT_WINDOW_S, PYC.WIN);
    eq(C.DEFAULT_STALE_THRESHOLD_S, PYC.STALE);
    eq(C.CHILLAR_SPACE, PYC.K);
  });
  T('the detection thresholds quoted in the panel notes match the gates', () => {
    eq(C.MIN_AREA_MM2, PYC.MIN_AREA);
    eq(C.MAX_AREA_MM2, PYC.MAX_AREA);
    eq(C.MIN_RECTANGULARITY, PYC.MIN_RECT);
    eq(C.MIN_ASPECT, PYC.MIN_ASP);
    eq(C.MAX_ASPECT, PYC.MAX_ASP);
    eq(C.EDGE_MARGIN_MM, PYC.EDGE);
    eq(C.MAX_MASK_FRACTION, PYC.MASKF);
    eq(C.MIN_BRIGHTNESS_DELTA, PYC.BRIGHT);
    eq(C.MAX_ILLUM_COUPLING, PYC.COUPL);
    eq(JSON.stringify(C.PLACEMENT_BOX_MM), JSON.stringify(PYC.PBOX));
  });
  T('collision_risk agrees with Python at eight occupancies', () => {
    for (const [n, want] of Object.entries(PYC.risk)) {
      near(C.collisionRisk(Number(n)), want, 1e-12, `collisionRisk(${n})`);
    }
    measured.collision_risk_n5 = C.collisionRisk(5).toFixed(6);
    measured.collision_risk_n30 = C.collisionRisk(30).toFixed(6);
  });
  T('any_collision_risk (birthday form) agrees with Python', () => {
    for (const [n, want] of Object.entries(PYC.anyrisk)) {
      near(C.anyCollisionRisk(Number(n)), want, 1e-12, `anyCollisionRisk(${n})`);
    }
  });
  T('max_payments_for_risk agrees with Python', () => {
    eq(C.maxPaymentsForRisk(0.05), PYC.maxfor['0.05']);
    eq(C.maxPaymentsForRisk(0.01), PYC.maxfor['0.01']);
    measured.window_occupancy_at_5pct_risk = C.maxPaymentsForRisk(0.05);
  });
}

// ===========================================================================
G('CHILLA — invariant 2: this panel cannot go green and cannot accuse');
// ===========================================================================

T('lightFor is AMBER for all four verdicts, MATCHED included', () => {
  for (const v of C.VERDICTS) eq(C.lightFor(v), 'AMBER', v);
  eq(C.lightFor('MATCHED'), 'AMBER', 'a corroborated screen is still not settlement');
});
T('the panel\'s own UNKNOWN state is amber too', () => {
  eq(C.lightFor(C.UNKNOWN), 'AMBER');
});
T('lightFor THROWS rather than defaulting for anything else', () => {
  throws(() => C.lightFor('GREEN'), 'GREEN');
  throws(() => C.lightFor('RED'), 'RED');
  throws(() => C.lightFor('PAID'), 'PAID');
  throws(() => C.lightFor(undefined), 'undefined');
  throws(() => C.lightFor(''), 'empty');
});
T('the palette this panel may draw with contains no green and no red', () => {
  for (const v of Object.values(C.PALETTE)) {
    for (const re of FORBIDDEN) ok(!re.test(v), `${v} matches ${re}`);
  }
});
T('collisionRisk refuses a degenerate key space instead of returning 0', () => {
  throws(() => C.collisionRisk(5, 0), 'k=0');
  throws(() => C.anyCollisionRisk(5, -1), 'k=-1');
  throws(() => C.maxPaymentsForRisk(0), 'risk 0');
  throws(() => C.maxPaymentsForRisk(1), 'risk 1');
});
T('collisionRisk(1) is exactly zero — one payment cannot collide with itself', () => {
  eq(C.collisionRisk(1), 0);
  eq(C.collisionRisk(0), 0);
});

// ===========================================================================
G('CHILLA — the symmetric ledger window');
// ===========================================================================

T('the window is symmetric: +/-180 s is a 360 s span', () => {
  const w = C.windowBounds(1000, 180);
  eq(w.lo, 820); eq(w.hi, 1180); eq(w.spanS, 360); eq(w.centre, 1000);
});
T('no capture time means no window, not a window centred on now', () => {
  eq(C.windowBounds(null), null);
  eq(C.windowBounds(undefined), null);
  eq(C.windowBounds(NaN), null);
});
T('a non-positive window is refused', () => {
  throws(() => C.windowBounds(1000, 0), 'zero window');
  throws(() => C.windowBounds(1000, -5), 'negative window');
});
T('clock formatting is UTC and locale-independent', () => {
  eq(C.fmtClock(0), '00:00:00Z');
  eq(C.fmtClock(3661), '01:01:01Z');
  eq(C.fmtClock(null), '—');
});

// ===========================================================================
G('CHILLA — derive + render, state by state');
// ===========================================================================

const SCREEN_QUAD_MM = [[80, 120], [150, 120], [150, 260], [80, 260]];
function screenDetection(over = {}) {
  return {
    found: true, reason: 'screen_found',
    quad_mm: SCREEN_QUAD_MM,
    quad_buf: SCREEN_QUAD_MM.map(([x, y]) => [x * 2.828, y * 2.828]),
    area_mm2: 9800.0, rectangularity: 0.94, mean_luma: 188.0, delta_luma: 61.5,
    mask_fraction: 0.052, n_candidates: 1, in_placement_box: true,
    illum_coupling: 0.11, ref_contrast: 3.4, coupling_measurable: true,
    ...over,
  };
}
function matchResult(over = {}) {
  return {
    verdict: 'MATCHED',
    reason: 'exactly one captured payment of 21457 paise within +/-180s',
    light: 'AMBER', amount_paise: 21457, screen_ts: 1_756_000_000,
    window_seconds: 180, mirror_age_s: 3.2, n_in_window: 4,
    collision_risk: C.collisionRisk(4), key_space: 99,
    candidates: [{ payment_id: 'pay_A1', amount_paise: 21457, created_at: 1_755_999_990, status: 'captured' }],
    ...over,
  };
}
const chillaState = (over = {}) => ({ chilla: { detection: screenDetection(), match: matchResult(), ...over } });

T('with no state at all, every region says I DO NOT KNOW with a named code', () => {
  const m = C.deriveChilla(null);
  eq(m.match.verdict, C.UNKNOWN);
  eq(m.amount.text, 'I DO NOT KNOW');
  eq(m.window.known, false);
  const codes = m.abstentions.map((a) => a.code);
  ok(codes.includes('no_reading'), `codes=${codes}`);
  ok(codes.includes('no_verdict'), `codes=${codes}`);
  ok(codes.includes('amount_not_read'), `codes=${codes}`);
  measured.chilla_cold_abstentions = codes.length;
});
T('the cold panel still renders, and still carries the hard rule', () => {
  const el = C.renderChilla(C.deriveChilla(null), DOC);
  has(text(el), 'I DO NOT KNOW');
  has(text(el), 'can never show GREEN');
  has(text(el), 'NO_MATCH is AMBER');
  assertNoGreenNoRed(el, 'cold chilla');
});
T('a MATCHED result renders MATCHED — and still says AMBER next to it', () => {
  const m = C.deriveChilla(chillaState());
  eq(m.match.verdict, 'MATCHED');
  eq(m.match.light, 'AMBER');
  const el = C.renderChilla(m, DOC);
  eq(one(el, 'chilla-verdict').textContent, 'MATCHED');
  eq(one(el, 'chilla-verdict').dataset.light, 'AMBER');
  has(text(el), 'Corroboration, NOT settlement');
  assertNoGreenNoRed(el, 'MATCHED');
});
T('the verdict chip is classed by its LIGHT, so no stylesheet can paint it green', () => {
  for (const v of [...C.VERDICTS, C.UNKNOWN]) {
    const m = C.deriveChilla(chillaState({ match: matchResult({ verdict: v }) }));
    const chip = one(C.renderChilla(m, DOC), 'chilla-verdict');
    eq(chip.className, 'chilla-verdict chilla-verdict-amber', `chip class for ${v}`);
  }
});
T('NO_MATCH is amber and is explicitly not an accusation', () => {
  const m = C.deriveChilla(chillaState({
    match: matchResult({ verdict: 'NO_MATCH', candidates: [], n_in_window: 3 }),
  }));
  eq(m.match.light, 'AMBER');
  const el = C.renderChilla(m, DOC);
  has(text(el), 'never an accusation');
  has(text(el), 'the webhook may simply be late');
  assertNoGreenNoRed(el, 'NO_MATCH');
});
T('AMBER_STALE wins even when an exact-amount candidate is present', () => {
  const m = C.deriveChilla(chillaState({
    match: matchResult({ verdict: 'AMBER_STALE', mirror_age_s: 412.0 }),
    stale_threshold_s: 60.0,
  }));
  eq(m.match.verdict, 'AMBER_STALE');
  eq(m.match.stale, true);
  eq(m.match.candidates.length, 1, 'the matching row is still shown');
  const el = C.renderChilla(m, DOC);
  has(text(el), 'AMBER even if a row matches');
  has(text(el), 'stale mirror cannot corroborate');
});
T('a stale mirror is stale on the NUMBERS, whatever verdict arrived', () => {
  const m = C.deriveChilla(chillaState({ match: matchResult({ mirror_age_s: 90 }) }));
  eq(m.match.stale, true, '90 s > 60 s threshold');
  eq(m.match.verdict, 'MATCHED', 'the panel does not rewrite the brain’s verdict');
  has(text(C.renderChilla(m, DOC)), 'AMBER even if a row matches');
});
T('an unknown mirror age is treated as unknown, never as fresh', () => {
  const m = C.deriveChilla(chillaState({
    match: matchResult({ mirror_age_s: undefined, mirror_age_ms: undefined }),
  }));
  eq(m.match.stale, null);
  ok(m.abstentions.some((a) => a.code === 'mirror_age_unknown'));
  has(text(C.renderChilla(m, DOC)), 'Treat as stale');
});
T('mirror_age_ms straight off the Python as_dict is understood', () => {
  const m = C.deriveChilla(chillaState({
    match: matchResult({ mirror_age_s: undefined, mirror_age_ms: 4500 }),
  }));
  near(m.match.mirrorAgeS, 4.5, 1e-9);
  eq(m.match.stale, false);
});
T('mirror_age_ms of -1 (Python’s "infinite") is stale, not fresh', () => {
  const m = C.deriveChilla(chillaState({
    match: matchResult({ mirror_age_s: undefined, mirror_age_ms: -1 }),
  }));
  eq(m.match.mirrorAgeS, Infinity);
  eq(m.match.stale, true);
});
T('AMBIGUOUS shows both colliding payments and never picks one', () => {
  const m = C.deriveChilla(chillaState({
    match: matchResult({
      verdict: 'AMBIGUOUS', n_in_window: 7,
      candidates: [
        { payment_id: 'pay_A1', amount_paise: 21457, created_at: 1_755_999_990, status: 'captured' },
        { payment_id: 'pay_B2', amount_paise: 21457, created_at: 1_756_000_030, status: 'captured' },
      ],
    }),
  }));
  eq(m.match.verdict, 'AMBIGUOUS');
  const el = C.renderChilla(m, DOC);
  eq(byClass(el, 'chilla-candidate').length, 2);
  has(text(el), 'pay_A1');
  has(text(el), 'pay_B2');
  has(text(el), 'does not separate them');
  assertNoGreenNoRed(el, 'AMBIGUOUS');
});
T('AMBIGUOUS without the colliding rows is itself an abstention', () => {
  const m = C.deriveChilla(chillaState({
    match: matchResult({ verdict: 'AMBIGUOUS', candidates: [] }),
  }));
  ok(m.abstentions.some((a) => a.code === 'ambiguous_without_candidates'));
});
T('a verdict code the panel does not know is NOT coloured, it is abstained on', () => {
  const m = C.deriveChilla(chillaState({ match: matchResult({ verdict: 'DEFINITELY_PAID' }) }));
  eq(m.match.verdict, C.UNKNOWN);
  eq(m.match.rawVerdict, 'DEFINITELY_PAID');
  ok(m.abstentions.some((a) => a.code === 'unrecognised_verdict'));
  const el = C.renderChilla(m, DOC);
  eq(one(el, 'chilla-verdict').textContent, 'I DO NOT KNOW');
  assertNoGreenNoRed(el, 'unknown verdict');
});
T('the amount is shown as integer paise through the money path', () => {
  const m = C.deriveChilla(chillaState());
  eq(m.amount.paise, 21457);
  eq(m.amount.text, '₹214.57');
});
T('a float amount is a MoneyError, and becomes a named abstention', () => {
  const m = C.deriveChilla(chillaState({ match: matchResult({ amount_paise: 214.57 }) }));
  eq(m.amount.known, false);
  eq(m.amount.text, 'I DO NOT KNOW');
  ok(m.abstentions.some((a) => a.code === 'amount_not_integer_paise'));
  has(text(C.renderChilla(m, DOC)), 'float is not money');
});
T('a missing amount does not become zero', () => {
  const m = C.deriveChilla(chillaState({ match: matchResult({ amount_paise: undefined }) }));
  eq(m.amount.paise, null);
  eq(m.amount.known, false);
  hasNot(one(C.renderChilla(m, DOC), 'chilla-key').textContent, '₹0.00');
});
T('the window searched is printed with both ends and the span', () => {
  const el = C.renderChilla(C.deriveChilla(chillaState()), DOC);
  const t = text(el);
  has(t, '±180s');
  has(t, '360s span');
  has(t, C.fmtClock(1_756_000_000 - 180));
  has(t, C.fmtClock(1_756_000_000 + 180));
});
T('no capture time means the window says so and names why', () => {
  const m = C.deriveChilla(chillaState({ match: matchResult({ screen_ts: null }) }));
  eq(m.window.known, false);
  ok(m.abstentions.some((a) => a.code === 'screen_ts_unknown'));
  has(text(C.renderChilla(m, DOC)), 'below the Nyquist floor too');
});
T('the collision risk is shown as a number, with its formula', () => {
  const m = C.deriveChilla(chillaState({
    match: matchResult({ n_in_window: 30, collision_risk: C.collisionRisk(30) }),
  }));
  near(m.collision.risk, 0.2550355444832344, 1e-12);
  const el = C.renderChilla(m, DOC);
  has(text(el), '25.50%');
  has(text(el), '1 - (98/99)^(30-1)');
  has(text(el), 'occupancy holding risk');
});
T('a brain-reported risk that disagrees with the arithmetic is surfaced, not adopted', () => {
  const m = C.deriveChilla(chillaState({ match: matchResult({ n_in_window: 30, collision_risk: 0.001 }) }));
  eq(m.collision.disagrees, true);
  has(text(C.renderChilla(m, DOC)), 'panel computes');
});
T('a detection abstention prints the reason AND the operator note', () => {
  for (const r of C.ABSTENTION_REASONS) {
    const m = C.deriveChilla({ chilla: { detection: { found: false, reason: r }, match: matchResult() } });
    const el = C.renderChilla(m, DOC);
    eq(one(el, 'chilla-detection').dataset.reason, r);
    has(text(el), C.REASON_NOTES[r].slice(0, 40), `note for ${r}`);
  }
});
T('an unrecognised detection reason is refused a translation', () => {
  const m = C.deriveChilla({ chilla: { detection: { found: false, reason: 'looks_fine_to_me' } } });
  ok(m.abstentions.some((a) => a.code === 'unknown_detection_reason'));
  has(text(C.renderChilla(m, DOC)), 'will not translate a code it does not know');
});
T('coupling_measurable=false is published as a limitation, not as emission', () => {
  const m = C.deriveChilla(chillaState({
    detection: screenDetection({ coupling_measurable: false, illum_coupling: null }),
  }));
  ok(m.abstentions.some((a) => a.code === 'coupling_not_measurable'));
  const t = text(C.renderChilla(m, DOC));
  has(t, 'NOT MEASURABLE');
  has(t, 'paper is NOT rejected');
});
T('the four published limitations are on screen', () => {
  const el = C.renderChilla(C.deriveChilla(chillaState()), DOC);
  eq(byClass(el, 'chilla-limit').length, 4);
  has(text(el), 'BRIGHTNESS IS NOT EMISSION');
  has(text(el), 'NONE OF THIS IS ANTI-SPOOF');
});

// ===========================================================================
G('CHILLA — the quad on the rectified view');
// ===========================================================================

T('SVG nodes are classed by ATTRIBUTE — className on an SVGElement is read-only', () => {
  // Assigning `svgEl.className = 'x'` throws a TypeError in the strict mode
  // every ES module runs under. A node-side tree walk would never see it, so
  // this asserts the mechanism rather than the appearance.
  const el = C.renderChilla(C.deriveChilla(chillaState()), DOC);
  for (const n of nodes(el).filter((x) => x.ns !== null)) {
    eq(n.className, '', `<${n.tagName}> had className assigned`);
    ok(typeof n.attrs.class === 'string' && n.attrs.class.length > 0, `<${n.tagName}> has no class attr`);
  }
});
T('the mini-map draws the mat, the placement box and the quad, in millimetres', () => {
  const el = C.renderChilla(C.deriveChilla(chillaState()), DOC);
  const map = one(el, 'chilla-map');
  eq(map.attrs.viewBox, '0 0 297 420', 'the viewBox IS the A3 mat');
  eq(byClass(el, 'chilla-map-quad').length, 1);
  eq(byClass(el, 'chilla-map-box').length, 1);
  eq(one(el, 'chilla-map-quad').attrs.d, 'M 80.00 120.00 L 150.00 120.00 L 150.00 260.00 L 80.00 260.00 Z');
});
T('with no quad the map says so instead of drawing an empty box', () => {
  const el = C.renderChilla(C.deriveChilla({ chilla: { detection: { found: false, reason: 'no_bright_region' } } }), DOC);
  eq(byClass(el, 'chilla-map-quad').length, 0);
  has(text(el), 'no screen quad');
});
T('quadPathD refuses a malformed quad rather than drawing a fiction', () => {
  eq(C.quadPathD(null), null);
  eq(C.quadPathD([[0, 0], [1, 1]]), null);
  eq(C.quadPathD([[0, 0], [1, 1], [2, 2], [3, NaN]]), null);
});
T('the frame overlay strokes the quad in amber, dashed, and returns true', () => {
  const calls = [];
  const ctx = new Proxy({}, {
    get: (t, k) => (...a) => { calls.push([k, ...a]); },
    set: (t, k, v) => { calls.push([`set:${k}`, v]); return true; },
  });
  const drew = C.drawScreenQuad(ctx, SCREEN_QUAD_MM, 'MATCHED');
  eq(drew, true);
  const strokes = calls.filter(([k]) => k === 'set:strokeStyle').map(([, v]) => v);
  eq(strokes.length, 1);
  eq(strokes[0], C.PALETTE.amber);
  for (const re of FORBIDDEN) ok(!re.test(strokes[0]), `overlay stroke matches ${re}`);
  ok(calls.some(([k]) => k === 'setLineDash'), 'the corroboration outline is dashed');
});
T('the overlay ABSTAINS rather than draws when there is no quad', () => {
  eq(C.drawScreenQuad({}, null), false);
  eq(C.drawScreenQuad(null, SCREEN_QUAD_MM), false);
});

// ===========================================================================
G('CHILLA — the panel object');
// ===========================================================================

T('onState mounts the subtree into #panel-chilla and stamps the verdict', () => {
  const doc = makeDoc([C.PANEL_ID]);
  const p = C.createPanel({ doc });
  eq(p.id, 'panel-chilla');
  eq(p.onState(chillaState()), true);
  const host = doc.byId[C.PANEL_ID];
  eq(host.children.length, 1);
  eq(host.dataset.verdict, 'MATCHED');
  eq(host.dataset.light, 'AMBER');
  assertNoGreenNoRed(host.children[0], 'mounted chilla');
});
T('with no host node the panel reports false rather than throwing', () => {
  const p = C.createPanel({ doc: makeDoc([]) });
  eq(p.onState(chillaState()), false);
  eq(p.model.match.verdict, 'MATCHED', 'the model still updated');
});
T('onFrame overlays only when there is a quad AND a context', () => {
  const doc = makeDoc([C.PANEL_ID]);
  const p = C.createPanel({ doc });
  p.onState(chillaState());
  const seen = [];
  const ctx = new Proxy({}, { get: () => () => {}, set: (t, k, v) => { seen.push(k); return true; } });
  eq(p.onFrame({ rectCtx: ctx }), true);
  eq(p.onFrame({}), false, 'no context -> abstain');
  p.onState(null);
  eq(p.onFrame({ rectCtx: ctx }), false, 'no quad -> abstain');
});
T('attach hands (id, {onState,onFrame}) to the host registrar', () => {
  const seen = [];
  const p = C.attach((id, panel) => seen.push([id, panel]), { doc: makeDoc([C.PANEL_ID]) });
  eq(seen.length, 1);
  eq(seen[0][0], 'panel-chilla');
  eq(typeof seen[0][1].onState, 'function');
  eq(typeof seen[0][1].onFrame, 'function');
  eq(seen[0][1], p);
});
T('attach refuses a non-function registrar loudly', () => {
  throws(() => C.attach(null), 'null registrar');
  throws(() => C.attach({}), 'object registrar');
});
T('renderChilla refuses a document that is not one', () => {
  throws(() => C.renderChilla(C.deriveChilla(null), {}), 'not a document');
});
T('THE SENTENCE is on the panel, above every number it justifies', () => {
  const el = C.renderChilla(C.deriveChilla(chillaState()), DOC);
  const p = one(el, 'chilla-nyquist');
  eq(p.textContent, C.NYQUIST_SENTENCE);
  has(p.textContent, 'NOT IN THE SIGNAL');
  // and the three legibility rows are printed under it, with the arithmetic
  eq(byClass(el, 'chilla-leg').length, 3);
  eq(byClass(el, 'chilla-leg-below').length, 2, 'reference string and screen clock');
  eq(byClass(el, 'chilla-leg-above').length, 1, 'the hero amount');
  has(text(el), 'never read off the screen: reference_string, utr, rrn');
});
T('the root node carries the verdict and the never-green stamp for every state', () => {
  for (const v of [...C.VERDICTS, C.UNKNOWN]) {
    const el = C.renderChilla(C.deriveChilla(chillaState({ match: matchResult({ verdict: v }) })), DOC);
    eq(el.dataset.verdict, v);
    eq(el.dataset.light, 'AMBER');
    eq(el.dataset.neverGreen, 'true');
    eq(el.dataset.neverRed, 'true');
    has(text(el), C.HARD_RULE);
    assertNoGreenNoRed(el, `root stamp ${v}`);
  }
});

// ===========================================================================
G('SAAF — cross-checked against gawaah/saaf.py');
// ===========================================================================

if (!pyAvailable) {
  console.log('   (skipped: the venv python could not be run)');
} else {
  T('every gate constant matches the Python module', () => {
    eq(S.DEFAULT_SCALE, PYS.SCALE);
    eq(S.BLUR_VAR_MIN, PYS.BLUR_VAR_MIN);
    eq(S.BLUR_REL_MIN, PYS.BLUR_REL_MIN);
    eq(S.MAX_SHIFT_PX, PYS.MAX_SHIFT);
    eq(S.SAT_LEVEL, PYS.SAT_LEVEL);
    eq(S.SAT_FRAC_MAX, PYS.SAT_FRAC_MAX);
    eq(S.MAX_BLUR_SCORE, PYS.MAX_BLUR_SCORE);
    eq(S.BLUR_SCORE_MTF50_CYC_PX, PYS.MTF50);
    eq(S.MIN_SHIFT_PX, PYS.MIN_SHIFT);
    eq(S.MIN_DIVERSITY, PYS.MIN_DIV);
  });
  T('the frame reason codes match, in order', () => {
    eq(JSON.stringify(S.FRAME_REASONS), JSON.stringify(PYS.R));
  });
  T('the warning codes match, in order', () => {
    eq(JSON.stringify(S.WARNING_CODES), JSON.stringify(PYS.W));
  });
  T('every non-empty warning code has an explanation', () => {
    for (const w of PYS.W) {
      if (w === '') continue;
      ok(typeof S.WARNING_NOTES[w] === 'string' && S.WARNING_NOTES[w].length > 30, w);
    }
  });
}

// ===========================================================================
G('SAAF — the contact sheet');
// ===========================================================================

function burst(over = {}) {
  return {
    saaf: {
      image: 'data:image/png;base64,AAAA',
      used: 5, rejected: 3, mean_shift_px: 1.84, sharpness_gain: 1.62,
      warning: '', reference_index: 0, subpixel_diversity: 0.71,
      diversity_x: 0.68, diversity_y: 0.74, burst_blur_score: 0.181, scale: 2,
      reports: [
        { index: 0, used: true, reason: 'reference', vlap: 3204.0, vlap_raw: 3204.0, sat_frac: 0.001, blur_score: 0.18, shift_px: 0.0, dx: 0, dy: 0 },
        { index: 1, used: true, reason: 'ok', vlap: 2980.0, vlap_raw: 2981.0, sat_frac: 0.002, blur_score: 0.19, shift_px: 1.42, dx: 1.1, dy: 0.9 },
        { index: 2, used: false, reason: 'blur: vlap 41.0 < rel floor 1121.4', vlap: 41.0, vlap_raw: 41.0, sat_frac: 0.001, blur_score: 0.52, shift_px: 1.1 },
        { index: 3, used: false, reason: 'glare: sat_frac 0.0731', vlap: 4100.0, vlap_raw: 9900.0, sat_frac: 0.0731, blur_score: 0.16, shift_px: 0.9 },
        { index: 4, used: true, reason: 'ok', vlap: 3100.0, vlap_raw: 3101.0, sat_frac: 0.003, blur_score: 0.18, shift_px: 2.2 },
        { index: 5, used: false, reason: 'ecc_failed', vlap: 2900.0, vlap_raw: 2900.0, sat_frac: 0.002, blur_score: 0.2, shift_px: null },
        { index: 6, used: true, reason: 'ok', vlap: 3050.0, vlap_raw: 3051.0, sat_frac: 0.002, blur_score: 0.18, shift_px: 3.1 },
        { index: 7, used: true, reason: 'ok', vlap: 2870.0, vlap_raw: 2871.0, sat_frac: 0.002, blur_score: 0.2, shift_px: 2.7 },
      ],
      ...over,
    },
  };
}

T('with no burst the panel says so and names the code', () => {
  const m = S.deriveSaaf(null);
  eq(m.present, false);
  ok(m.abstentions.some((a) => a.code === 'no_burst'));
  const el = S.renderSaaf(m, DOC);
  has(text(el), 'I DO NOT KNOW');
  has(text(el), 'no enrolment burst');
  assertNoGreenNoRed(el, 'cold saaf');
});
T('one contact-sheet cell per burst frame, each marked KEPT or REJECTED', () => {
  const m = S.deriveSaaf(burst());
  const el = S.renderSaaf(m, DOC);
  eq(byClass(el, 'saaf-cell').length, 8);
  eq(byData(el, 'used', 'true').filter((n) => n.className.includes('saaf-cell ')).length, 5);
  eq(byClass(el, 'saaf-cell-kept').length, 5);
  eq(byClass(el, 'saaf-cell-rejected').length, 3);
  measured.saaf_contact_sheet_cells = byClass(el, 'saaf-cell').length;
  assertNoGreenNoRed(el, 'saaf contact sheet');
});
T('the header counts what the sheet shows', () => {
  const el = S.renderSaaf(S.deriveSaaf(burst()), DOC);
  eq(one(el, 'saaf-counts').textContent, '5 kept · 3 rejected · 8 frames shown');
  eq(byClass(el, 'saaf-cell').length, 8, 'the header count and the sheet must agree');
});
T('a headline the panel raised itself gets the note that goes with it', () => {
  // A headline saying NO_SUBPIXEL_DIVERSITY over a note saying "no warning" is
  // a screen saying two things and meaning neither.
  const el = S.renderSaaf(S.deriveSaaf(burst({ warning: '', mean_shift_px: 0.01 })), DOC);
  has(one(el, 'saaf-warning-code').textContent, 'NO_SUBPIXEL_DIVERSITY');
  has(one(el, 'saaf-warning-note').textContent, 'DEGENERATES TO DENOISING');
  hasNot(one(el, 'saaf-warning-note').textContent, 'no warning');
});
T('the burst-relative blur floor is computed here and printed', () => {
  const m = S.deriveSaaf(burst());
  near(m.maxVlap, 4100.0, 1e-9);
  near(m.relFloor, 0.35 * 4100.0, 1e-9);
  has(text(S.renderSaaf(m, DOC)), '0.35 x 4100.0 (sharpest frame) = 1435.0');
});
T('a blur rejection quotes the measured vLap and the floor it failed', () => {
  const cell = byData(S.renderSaaf(S.deriveSaaf(burst()), DOC), 'index', 2)[0];
  has(cell.textContent === '' ? text(cell) : text(cell), 'REJECTED — blur below the floor');
  has(text(cell), 'vLap 41.0');
  has(text(cell), '1435.0');
  has(text(cell), 'absolute floor 60');
});
T('a glare rejection quotes the blown fraction and the 2% guard', () => {
  const cell = byData(S.renderSaaf(S.deriveSaaf(burst()), DOC), 'index', 3)[0];
  has(text(cell), '7.31% of pixels are at or above level 250');
  has(text(cell), '2.00% limit');
  has(text(cell), 'would inflate vLap');
});
T('the glare frame shows guarded AND raw vLap, which is what makes glare visible', () => {
  const cell = byData(S.renderSaaf(S.deriveSaaf(burst()), DOC), 'index', 3)[0];
  has(text(cell), '4100.0');
  has(text(cell), '9900.0');
});
T('an ECC throw is labelled REJECTED, never "no motion detected"', () => {
  const cell = byData(S.renderSaaf(S.deriveSaaf(burst()), DOC), 'index', 5)[0];
  has(text(cell), 'the ECC alignment THREW');
  has(text(cell), 'never as "no motion detected"');
});
T('the reference frame is labelled as the frame everything registers to', () => {
  const cell = byData(S.renderSaaf(S.deriveSaaf(burst()), DOC), 'index', 0)[0];
  has(text(cell), 'KEPT as the reference');
  has(text(cell), 'sharpest frame in the burst');
});
T('every frame reason code has an explanation with its threshold in it', () => {
  const cases = {
    blur: ['1435.0'],
    glare: ['level 250', '2.00%'],
    defocus: ['0.46 ceiling', 'MTF50 0.15'],
    shift_too_large: ['exceeds 10 px'],
    warp_not_finite: ['non-finite'],
    ecc_failed: ['THREW'],
    ok: ['KEPT'],
    reference: ['reference'],
  };
  for (const [code, needles] of Object.entries(cases)) {
    const s = S.explainFrame({ code, vlap: 1, satFrac: 0.5, blurScore: 0.9, shiftPx: 42 }, 1435.0);
    for (const n of needles) has(s, n, `explainFrame(${code})`);
  }
});
T('an unrecognised frame code gets no invented explanation', () => {
  const s = S.explainFrame({ code: 'vibes' }, null);
  has(s, 'will not invent an explanation');
  const m = S.deriveSaaf(burst({ reports: [{ index: 0, used: false, reason: 'vibes' }] }));
  ok(m.abstentions.some((a) => a.code === 'unknown_frame_reason'));
});
T('a frame discarded with NO reason at all is called out as unexplained', () => {
  has(S.explainFrame({ code: '' }, null), 'no reason code was supplied');
  const m = S.deriveSaaf(burst({ reports: [{ index: 0, used: false }] }));
  has(text(S.renderSaaf(m, DOC)), 'An unexplained discard is exactly what this panel exists to prevent');
  eq(one(S.renderSaaf(m, DOC), 'saaf-cell-code').textContent, '(no reason code)');
});
T('snake_case frame reports straight off the Python dataclass are understood', () => {
  const m = S.deriveSaaf(burst());
  eq(m.frames[3].satFrac, 0.0731);
  eq(m.frames[1].vlapRaw, 2981.0);
  eq(m.frames[2].blurScore, 0.52);
  eq(m.frames[2].code, 'blur', 'the code is the reason before the colon');
  eq(m.frames[2].detail, 'vlap 41.0 < rel floor 1121.4');
});
T('a used-count that disagrees with the reports is surfaced, not reconciled', () => {
  const m = S.deriveSaaf(burst({ used: 7 }));
  ok(m.abstentions.some((a) => a.code === 'used_count_disagrees'));
  eq(m.counts.keptFromFrames, 5);
});
T('with no thumbnails the cells say so instead of showing a blank tile', () => {
  const el = S.renderSaaf(S.deriveSaaf(burst()), DOC);
  eq(byClass(el, 'saaf-thumb-absent').length, 8);
  has(text(el), 'no thumbnail supplied');
});
T('with thumbnails the cells carry an <img> with the frame’s own src', () => {
  const reports = burst().saaf.reports.map((r, i) => ({ ...r, thumb: `data:image/png;base64,f${i}` }));
  const el = S.renderSaaf(S.deriveSaaf(burst({ reports })), DOC);
  const imgs = tagsOf(el, 'img').filter((n) => n.className.includes('saaf-thumb'));
  eq(imgs.length, 8);
  eq(imgs[3].attrs.src, 'data:image/png;base64,f3');
  has(imgs[3].attrs.alt, 'REJECTED');
});
T('a burst with no per-frame reports is an abstention, not an empty success', () => {
  const m = S.deriveSaaf(burst({ reports: [] }));
  ok(m.abstentions.some((a) => a.code === 'no_frame_reports'));
  has(text(S.renderSaaf(m, DOC)), 'the discard cannot be shown');
});

// ===========================================================================
G('SAAF — sharpness gain, with its confound printed next to it');
// ===========================================================================

T('the measured gain is rendered', () => {
  const el = S.renderSaaf(S.deriveSaaf(burst()), DOC);
  eq(one(el, 'saaf-gain-big').textContent, '1.620x');
  measured.saaf_sharpness_gain_shown = '1.620x';
});
T('the vLap/noise confound is stated wherever the gain is', () => {
  const t = text(S.renderSaaf(S.deriveSaaf(burst()), DOC));
  has(t, 'vLap also counts NOISE');
  has(t, 'UNDERSTATES');
  has(t, 'MTF50 is the honest resolution measure');
});
T('the panel states that nothing was sharpened to produce that number', () => {
  has(text(S.renderSaaf(S.deriveSaaf(burst()), DOC)), 'no unsharp mask');
});
T('a gain at or below 1.00x is called out as no gain', () => {
  const m = S.deriveSaaf(burst({ sharpness_gain: 0.98 }));
  ok(m.abstentions.some((a) => a.code === 'no_measured_gain'));
  has(text(S.renderSaaf(m, DOC)), 'No resolution was recovered');
});
T('an absent gain is I DO NOT KNOW, not 1.0', () => {
  const m = S.deriveSaaf(burst({ sharpness_gain: undefined }));
  eq(m.gain.known, false);
  eq(one(S.renderSaaf(m, DOC), 'saaf-gain-big').textContent, 'I DO NOT KNOW');
  ok(m.abstentions.some((a) => a.code === 'sharpness_gain_absent'));
});

// ===========================================================================
G('SAAF — the warning state (the one this panel exists for)');
// ===========================================================================

T('a healthy burst carries no warning and is not called degenerate', () => {
  const m = S.deriveSaaf(burst());
  eq(m.warning.present, false);
  eq(m.motion.nearZero, false);
  eq(one(S.renderSaaf(m, DOC), 'saaf-warning-code').textContent, 'no warning');
});
T('NO_SUBPIXEL_DIVERSITY says the stack degenerated to denoising', () => {
  const m = S.deriveSaaf(burst({
    warning: 'NO_SUBPIXEL_DIVERSITY: mean shift 0.02 px below 0.15',
    mean_shift_px: 0.02, subpixel_diversity: 0.004, sharpness_gain: 1.01,
  }));
  eq(m.warning.code, 'NO_SUBPIXEL_DIVERSITY');
  eq(m.warning.degenerate, true);
  const el = S.renderSaaf(m, DOC);
  has(text(el), 'STACKING DEGENERATED TO DENOISING');
  has(text(el), 'no sub-pixel diversity');
  has(text(el), 'returning a worse image silently is not');
  eq(one(el, 'saaf-warning').dataset.degenerate, 'true');
  assertNoGreenNoRed(el, 'NO_SUBPIXEL_DIVERSITY');
});
T('the warning DETAIL from the Python string survives to the screen', () => {
  const m = S.deriveSaaf(burst({ warning: 'NO_SUBPIXEL_DIVERSITY: mean shift 0.02 px below 0.15' }));
  eq(m.warning.detail, 'mean shift 0.02 px below 0.15');
  has(text(S.renderSaaf(m, DOC)), 'mean shift 0.02 px below 0.15');
});
T('near-zero motion with NO warning attached is raised BY THE PANEL', () => {
  const m = S.deriveSaaf(burst({ warning: '', mean_shift_px: 0.03, subpixel_diversity: 0.02 }));
  eq(m.motion.nearZero, true);
  eq(m.warning.present, false);
  eq(m.warning.selfDetected, true);
  ok(m.abstentions.some((a) => a.code === 'degenerate_but_unwarned'));
  const el = S.renderSaaf(m, DOC);
  has(text(el), 'raised by this panel from the numbers');
  has(text(el), 'STACKING DEGENERATED TO DENOISING');
  eq(one(el, 'saaf-warning').dataset.degenerate, 'true');
});
T('a clustered sampling phase counts as degenerate even when the frames moved', () => {
  const m = S.deriveSaaf(burst({ mean_shift_px: 4.0, subpixel_diversity: 0.02 }));
  eq(m.motion.shiftNearZero, false);
  eq(m.motion.phaseClustered, true);
  eq(m.motion.nearZero, true);
  has(text(S.renderSaaf(m, DOC)), 'DEGENERATED TO DENOISING');
});
T('DEGENERATE_SAMPLING_PHASE explains why moving is not enough', () => {
  const m = S.deriveSaaf(burst({ warning: 'DEGENERATE_SAMPLING_PHASE', subpixel_diversity: 0.03 }));
  has(text(S.renderSaaf(m, DOC)), 're-sample the same grid positions');
});
T('BURST_UNIFORMLY_DEFOCUSED explains the blind spot it closes', () => {
  const m = S.deriveSaaf(burst({ warning: 'BURST_UNIFORMLY_DEFOCUSED', burst_blur_score: 0.51 }));
  const t = text(S.renderSaaf(m, DOC));
  has(t, 'No frame is the "sharp" one');
  has(t, 'passed the burst silently');
  has(t, '0.510 (ceiling 0.46)');
});
T('SINGLE_FRAME says no multi-frame information was added', () => {
  has(text(S.renderSaaf(S.deriveSaaf(burst({ warning: 'SINGLE_FRAME' })), DOC)),
    'no multi-frame information was added');
});
T('unknown motion is I DO NOT KNOW, and is not read as healthy', () => {
  const m = S.deriveSaaf(burst({ mean_shift_px: undefined, subpixel_diversity: undefined }));
  eq(m.motion.nearZero, null);
  ok(m.abstentions.some((a) => a.code === 'mean_shift_absent'));
  eq(one(S.renderSaaf(m, DOC), 'saaf-motion-big').textContent, 'I DO NOT KNOW');
});
T('an unknown warning code is not translated', () => {
  const m = S.deriveSaaf(burst({ warning: 'PROBABLY_FINE: trust me' }));
  eq(m.warning.known, false);
  ok(m.abstentions.some((a) => a.code === 'unknown_warning_code'));
  has(text(S.renderSaaf(m, DOC)), 'will not translate a code it does not know');
});
T('ALL_FRAMES_REJECTED returns NO IMAGE and says nothing was enrolled', () => {
  const m = S.deriveSaaf(burst({ warning: 'ALL_FRAMES_REJECTED', image: null, used: 0, rejected: 8 }));
  eq(m.image.present, false);
  eq(m.image.reason, 'ALL_FRAMES_REJECTED');
  const el = S.renderSaaf(m, DOC);
  has(text(el), 'NO IMAGE');
  has(text(el), 'Nothing was enrolled');
  has(text(el), 'abstaining is the designed behaviour, not a bug');
  eq(tagsOf(el, 'img').filter((n) => n.className === 'saaf-result-img').length, 0);
});
T('a returned image is shown as an image', () => {
  const el = S.renderSaaf(S.deriveSaaf(burst()), DOC);
  const img = one(el, 'saaf-result-img');
  eq(img.attrs.src, 'data:image/png;base64,AAAA');
});
T('a missing image without ALL_FRAMES_REJECTED is still an abstention', () => {
  const m = S.deriveSaaf(burst({ image: null }));
  eq(m.image.reason, 'no_image_returned');
  ok(m.abstentions.some((a) => a.code === 'no_image_returned'));
});

// ===========================================================================
G('SAAF — the panel object');
// ===========================================================================

T('onState mounts into #panel-saaf and stamps the warning code', () => {
  const doc = makeDoc([S.PANEL_ID]);
  const p = S.createPanel({ doc });
  eq(p.onState(burst({ warning: 'NO_SUBPIXEL_DIVERSITY' })), true);
  eq(doc.byId[S.PANEL_ID].dataset.warning, 'NO_SUBPIXEL_DIVERSITY');
  eq(doc.byId[S.PANEL_ID].dataset.kept, '5');
});
T('SAAF has nothing to overlay on the live view, and says so by returning false', () => {
  eq(S.createPanel({ doc: makeDoc([]) }).onFrame({ rectCtx: {} }), false);
});
T('attach registers the saaf panel under its id', () => {
  const seen = [];
  S.attach((id, panel) => seen.push([id, panel]), { doc: makeDoc([]) });
  eq(seen[0][0], 'panel-saaf');
  eq(typeof seen[0][1].onState, 'function');
});
T('the saaf palette has no green and no red either', () => {
  for (const v of Object.values(S.PALETTE)) {
    for (const re of FORBIDDEN) ok(!re.test(v), `${v} matches ${re}`);
  }
});
T('with no host node SAAF reports false rather than throwing', () => {
  const p = S.createPanel({ doc: makeDoc([]) });
  eq(p.onState(burst()), false);
  eq(p.model.counts.total, 8, 'the model still updated');
});
T('every warning code renders, and not one of them is green or red', () => {
  for (const w of S.WARNING_CODES) {
    const el = S.renderSaaf(S.deriveSaaf(burst({ warning: w })), DOC);
    assertNoGreenNoRed(el, `saaf warning ${w}`);
    if (w !== '') has(text(el), w);
  }
});
T('every frame reason code renders a cell, and not one of them is green or red', () => {
  for (const code of S.FRAME_REASONS) {
    const el = S.renderSaaf(S.deriveSaaf(burst({
      reports: [{ index: 0, used: S.KEEP_REASONS.includes(code), reason: code, vlap: 10, sat_frac: 0.5, blur_score: 0.9, shift_px: 42 }],
    })), DOC);
    eq(byClass(el, 'saaf-cell')[0].dataset.code, code);
    assertNoGreenNoRed(el, `saaf frame ${code}`);
  }
});

// ===========================================================================
G('LEDGER — canonical JSON, byte-compatible with gawaah/ledger.py');
// ===========================================================================

T('keys are sorted and there is no whitespace', () => {
  const n = L.parseJsonNode('{"z":1, "a":2, "m":{"b":1,"a":2}}');
  eq(L.canonicalizeNode(n), '{"a":2,"m":{"a":2,"b":1},"z":1}');
});
T('THE FLOAT TRAP: the original number literal is preserved verbatim', () => {
  eq(L.canonicalizeNode(L.parseJsonNode('{"a":60.0}')), '{"a":60.0}');
  eq(L.canonicalizeNode(L.parseJsonNode('{"a":1e-7}')), '{"a":1e-7}');
  eq(L.canonicalizeNode(L.parseJsonNode('{"a":-0.0}')), '{"a":-0.0}');
});
T('...and re-serialising a PARSED value loses it, which is why that path is flagged', () => {
  eq(L.canonicalizeValue({ a: 60.0 }), '{"a":60}', 'JS cannot tell 60.0 from 60');
  // Therefore NO number can be certified once a line has been through
  // JSON.parse — not even an apparent integer, which may have been written 60.0.
  eq(L.reserialisationIsExact({ a: 60.0 }), false);
  eq(L.reserialisationIsExact({ a: 60 }), false);
  eq(L.reserialisationIsExact({ a: 60.5 }), false);
  eq(L.reserialisationIsExact({ a: 'x', b: [null, true, 'y'] }), true, 'no numbers, so certifiable');
  eq(L.reserialisationIsExact({ a: 'x', b: [1, null] }), false, 'one number is enough to disqualify');
});
T('strings escape the way Python escapes them with ensure_ascii=False', () => {
  eq(L.canonicalizeNode(L.parseJsonNode('{"a":"\\u00e9"}')), '{"a":"é"}');
  eq(L.canonicalizeNode(L.parseJsonNode('{"a":"x\\ny"}')), '{"a":"x\\ny"}');
  eq(L.canonicalizeNode(L.parseJsonNode('{"a":"q\\"q"}')), '{"a":"q\\"q"}');
});
T('the parser reads nested structures and every literal', () => {
  const v = L.nodeToPlain(L.parseJsonNode('{"a":[1,{"b":null},true,false,"s"],"c":-2.5e3}'));
  eq(JSON.stringify(v), '{"a":[1,{"b":null},true,false,"s"],"c":-2500}');
});
T('the parser accepts Python’s Infinity and NaN rather than choking on a real ledger', () => {
  eq(L.canonicalizeNode(L.parseJsonNode('{"a":Infinity,"b":-Infinity,"c":NaN}')),
    '{"a":Infinity,"b":-Infinity,"c":NaN}');
  ok(Number.isNaN(L.nodeToPlain(L.parseJsonNode('NaN'))));
});
T('the parser refuses trailing junk instead of silently truncating', () => {
  throws(() => L.parseJsonNode('{"a":1} trailing'), 'trailing');
  throws(() => L.parseJsonNode('{"a":}'), 'bad value');
  throws(() => L.parseJsonNode('{a:1}'), 'unquoted key');
  throws(() => L.parseJsonNode('[1,2'), 'unterminated');
});
T('keys sort by code point, the way Python sorts them', () => {
  ok(L.cmpCodePoints('a', 'b') < 0);
  ok(L.cmpCodePoints('Z', 'a') < 0);
  ok(L.cmpCodePoints('ab', 'a') > 0);
  eq(L.cmpCodePoints('a', 'a'), 0);
});
T('a duplicated key resolves the same way in the parser and the canonicaliser', () => {
  eq(L.canonicalizeNode(L.parseJsonNode('{"a":1,"a":2}')), '{"a":2}', 'last wins');
});

// ===========================================================================
G('LEDGER — verifying a chain gawaah/ledger.py actually wrote');
// ===========================================================================

let TMP = null, CHAIN = '', PY_HEAD = '', PY_COUNT = 0;
if (pyAvailable) {
  try {
    TMP = mkdtempSync(join(tmpdir(), 'gawaah-panels-'));
    const p = join(TMP, 'kaala.jsonl');
    const out = python(`
import json
from gawaah.ledger import Ledger, verify
led = Ledger(${JSON.stringify(p)})
led.append(ts="2026-08-29T09:00:00+00:00", module="session", reason="session_opened")
led.append(ts="2026-08-29T09:00:04+00:00", module="placement", reason="placement_seen",
           item_id="itm-1", price_paise=4500, centre_mm=[120.0, 210.0])
led.append(ts="2026-08-29T09:00:09+00:00", module="placement", reason="unknown_sku",
           item_id="itm-2", price_paise=None)
led.append(ts="2026-08-29T09:00:20+00:00", module="chilla", verdict="NO_MATCH",
           amount_paise=21457, collision_risk="0.039796", n_in_window=5,
           mirror_age_ms=1200, light="AMBER")
led.append(ts="2026-08-29T09:00:31+00:00", module="saaf", reason="ALL_FRAMES_REJECTED",
           used=0, rejected=8, sharpness_gain=1.0, mean_shift_px=0.0)
led.append(ts="2026-08-29T09:00:44+00:00", module="session", reason="exit_crossing_committed",
           item_id="itm-1", total_paise=4500)
ok, n, head, err = verify(${JSON.stringify(p)})
print(json.dumps({"ok": ok, "n": n, "head": head, "err": err, "count": led.count}))
`);
    const meta = JSON.parse(out);
    CHAIN = readFileSync(p, 'utf8');
    PY_HEAD = meta.head;
    PY_COUNT = meta.n;
    ok(meta.ok, 'python could not verify its own chain');
  } catch (e) {
    console.log(`   !! could not build a real chain: ${e.message}`);
    TMP = null;
  }
}

if (!TMP) {
  console.log('   (skipped: no real chain could be written)');
} else {
  await TA('a real Python-written chain verifies VERIFIED in the browser', async () => {
    const r = await L.verifyChain(CHAIN);
    eq(r.verdict, L.VERIFIED, r.error || '');
    eq(r.ok, true);
    eq(r.linesChecked, PY_COUNT);
    eq(r.exact, true, 'verified against the writer’s own bytes');
    measured.ledger_lines_verified = r.linesChecked;
  });
  await TA('and lands on the same head hash Python reports', async () => {
    const r = await L.verifyChain(CHAIN);
    eq(r.head, PY_HEAD);
    measured.ledger_head = `${PY_HEAD.slice(0, 16)}…`;
  });
  await TA('a chain of raw LINES verifies identically to the raw text', async () => {
    const a = await L.verifyChain(CHAIN);
    const b = await L.verifyChain(CHAIN.split('\n'));
    eq(b.verdict, a.verdict);
    eq(b.head, a.head);
    eq(b.linesChecked, a.linesChecked);
  });
  await TA('an empty chain is VERIFIED at genesis, not an error', async () => {
    const r = await L.verifyChain('');
    eq(r.verdict, L.VERIFIED);
    eq(r.linesChecked, 0);
    eq(r.head, L.GENESIS);
  });

  // -- the four tampers --------------------------------------------------
  const lines = CHAIN.split('\n').filter((s) => s.trim() !== '');

  await TA('TAMPER 1: change a price on line 2 -> BROKEN at line 2', async () => {
    const bad = lines.slice();
    bad[1] = bad[1].replace('"price_paise": 4500', '"price_paise": 45');
    ok(bad[1] !== lines[1], 'the tamper did not apply');
    const r = await L.verifyChain(bad);
    eq(r.verdict, L.BROKEN);
    eq(r.brokenLine, 2);
    has(r.error, 'line 2: hash mismatch');
    eq(r.linesChecked, 1, 'line 1 was fine and is reported as checked');
  });
  await TA('TAMPER 2: delete line 3 -> chain break at the line that took its place', async () => {
    const bad = lines.filter((_, i) => i !== 2);
    const r = await L.verifyChain(bad);
    eq(r.verdict, L.BROKEN);
    eq(r.brokenLine, 3);
    has(r.error, 'line 3: chain break — prev_hash');
    has(r.error, '!= expected');
  });
  await TA('TAMPER 3: swap two lines -> chain break at the first one moved', async () => {
    const bad = lines.slice();
    [bad[3], bad[4]] = [bad[4], bad[3]];
    const r = await L.verifyChain(bad);
    eq(r.verdict, L.BROKEN);
    eq(r.brokenLine, 4);
    has(r.error, 'chain break');
  });
  await TA('TAMPER 4: strip the hash off line 5 -> "missing hash" at line 5', async () => {
    const bad = lines.slice();
    const rec = JSON.parse(bad[4]);
    delete rec.hash;
    bad[4] = JSON.stringify(rec);
    const r = await L.verifyChain(bad);
    eq(r.verdict, L.BROKEN);
    eq(r.brokenLine, 5);
    has(r.error, 'line 5: missing hash');
  });
  await TA('a malformed line is named as malformed, not as tampering', async () => {
    const bad = lines.slice();
    bad[2] = '{"ts": "oops",';
    const r = await L.verifyChain(bad);
    eq(r.verdict, L.BROKEN);
    eq(r.brokenLine, 3);
    has(r.error, 'line 3: not valid JSON');
  });
  await TA('the JS verifier names the SAME line Python’s own verifier names', async () => {
    const bad = lines.slice();
    bad[1] = bad[1].replace('"price_paise": 4500', '"price_paise": 45');
    const p2 = join(TMP, 'tampered.jsonl');
    writeFileSync(p2, `${bad.join('\n')}\n`, 'utf8');
    const out = python(`
import json
from gawaah.ledger import verify
ok, n, head, err = verify(${JSON.stringify(p2)})
print(json.dumps({"ok": ok, "n": n, "err": err}))
`);
    const py = JSON.parse(out);
    const js = await L.verifyChain(bad);
    eq(py.ok, false);
    eq(js.ok, false);
    eq(js.linesChecked, py.n, 'the two verifiers stopped at the same line');
    has(py.err, 'line 2: hash mismatch');
    has(js.error, 'line 2: hash mismatch');
    measured.ledger_cross_language_break_line = 2;
  });
  await TA('a clean chain re-verifies clean after all that tampering', async () => {
    const r = await L.verifyChain(CHAIN);
    eq(r.verdict, L.VERIFIED);
  });

  // -- the re-serialised path -------------------------------------------
  await TA('parsed objects with NO numbers at all can still be certified exact', async () => {
    const objs = lines.map((s) => JSON.parse(s)).slice(0, 1);
    ok(Object.values(objs[0]).every((v) => typeof v === 'string'), 'line 1 should be all strings');
    const r = await L.verifyChain(objs);
    eq(r.exact, true);
    eq(r.verdict, L.VERIFIED, r.error || '');
  });
  await TA('parsed objects containing ANY number cannot be certified exact', async () => {
    // 120.0 in the file arrives as the JavaScript number 120. There is no way
    // left to tell it from an integer, so the check cannot vouch for its bytes.
    const objs = lines.map((s) => JSON.parse(s));
    eq(L.reserialisationIsExact(objs[1]), false, 'price_paise 4500 is a number');
    const r = await L.verifyChain(objs);
    eq(r.exact, false);
    measured.ledger_reserialised_verdict = r.verdict;
  });
  await TA('THE FALSE-ACCUSATION GUARD: a float difference reads INDETERMINATE, not BROKEN', async () => {
    // The chain below is untampered. It only lost its number formatting on the
    // way through JSON.parse. A verifier that called this BROKEN would be
    // accusing a clean ledger of tampering.
    const objs = lines.map((s) => JSON.parse(s));
    const r = await L.verifyChain(objs);
    eq(r.verdict, L.INDETERMINATE, `got ${r.verdict}: ${r.error}`);
    ok(r.error !== null, 'the mismatch is still reported, just not as tampering');
    has(r.error, 'hash mismatch');
    // and the same bytes, read as bytes, verify clean
    eq((await L.verifyChain(lines)).verdict, L.VERIFIED);
  });
}

await TA('with no SubtleCrypto the answer is UNAVAILABLE, never VERIFIED', async () => {
  const r = await L.verifyChain('', { subtle: null, forceNoSubtle: true });
  // opts.subtle null falls through to globalThis.crypto, so prove the branch
  // with an explicit stub-free call instead:
  const saved = globalThis.crypto;
  try {
    Object.defineProperty(globalThis, 'crypto', { configurable: true, value: undefined });
    const r2 = await L.verifyChain('anything');
    eq(r2.verdict, L.UNAVAILABLE);
    eq(r2.ok, false);
    has(r2.error, 'no SubtleCrypto');
  } finally {
    Object.defineProperty(globalThis, 'crypto', { configurable: true, value: saved });
  }
  eq(r.verdict, L.VERIFIED, 'the control call still worked');
});

// ===========================================================================
G('LEDGER — money actions and reason codes');
// ===========================================================================

T('an integer-paise field is rendered through the money path', () => {
  const a = L.moneyActionOf({ amount_paise: 21457 });
  eq(a.known, true); eq(a.field, 'amount_paise'); eq(a.text, '₹214.57');
});
T('a null price is AMBER and excluded, not zero', () => {
  const a = L.moneyActionOf({ price_paise: null });
  eq(a.known, false);
  eq(a.reason, 'amber_null_price');
  has(a.text, 'excluded from the total');
});
T('a float price is NOT INTEGER PAISE, and says why', () => {
  const a = L.moneyActionOf({ price_paise: 45.5 });
  eq(a.known, false);
  eq(a.reason, 'not_integer_paise');
  has(a.detail, 'float is not money');
});
T('a line with no money field says exactly that', () => {
  eq(L.moneyActionOf({ module: 'session', reason: 'session_opened' }).reason, 'no_money_field');
  eq(L.moneyActionOf({ module: 'session' }).text, 'no money field');
});
T('reason codes fall back through the fields the brain actually writes', () => {
  eq(L.reasonCodeOf({ reason: 'a' }), 'a');
  eq(L.reasonCodeOf({ verdict: 'NO_MATCH' }), 'NO_MATCH');
  eq(L.reasonCodeOf({ event: 'payment.captured' }), 'payment.captured');
  eq(L.reasonCodeOf({ module: 'x' }), null, 'no reason code is null, not ""');
});
T('shortHash truncates with an ellipsis and passes short values through', () => {
  eq(L.shortHash('a'.repeat(64), 12), `${'a'.repeat(12)}…`);
  eq(L.shortHash('abc', 12), 'abc');
  eq(L.shortHash(null), '—');
});

// ===========================================================================
G('LEDGER — derive + render');
// ===========================================================================

const SAMPLE = {
  ledger: {
    head: 'f'.repeat(64),
    count: 3,
    entries: [
      { ts: '2026-08-29T09:00:00Z', module: 'session', reason: 'session_opened', prev_hash: L.GENESIS, hash: 'a'.repeat(64) },
      { ts: '2026-08-29T09:00:04Z', module: 'placement', reason: 'priced_from_gallery', price_paise: 4500, prev_hash: 'a'.repeat(64), hash: 'b'.repeat(64) },
      { ts: '2026-08-29T09:00:20Z', module: 'chilla', verdict: 'AMBIGUOUS', amount_paise: 21457, prev_hash: 'b'.repeat(64), hash: 'f'.repeat(64) },
    ],
  },
};

T('with no chain the panel says I DO NOT KNOW', () => {
  const m = L.deriveLedger(null);
  eq(m.present, false);
  eq(m.head, null);
  ok(m.abstentions.some((a) => a.code === 'no_ledger'));
  const el = L.renderLedger(m, DOC);
  has(text(el), 'I DO NOT KNOW');
  assertNoGreenNoRed(el, 'cold ledger');
});
T('the head hash and the line count are on screen', () => {
  const m = L.deriveLedger(SAMPLE);
  eq(m.count, 3);
  eq(m.head, 'f'.repeat(64));
  const el = L.renderLedger(m, DOC);
  has(text(el), 'f'.repeat(64));
  has(text(el), 'head hash');
  eq(byClass(el, 'ledger-row').length, 3);
});
T('each row shows module, reason code and money action', () => {
  const el = L.renderLedger(L.deriveLedger(SAMPLE), DOC);
  const rows = byClass(el, 'ledger-row');
  eq(rows[0].dataset.module, 'session');
  eq(rows[0].dataset.reason, 'session_opened');
  has(text(rows[1]), 'price_paise ₹45.00');
  eq(rows[2].dataset.reason, 'AMBIGUOUS');
  has(text(rows[2]), 'amount_paise ₹214.57');
});
T('a line with no money field says "no money field" rather than nothing', () => {
  has(text(byClass(L.renderLedger(L.deriveLedger(SAMPLE), DOC), 'ledger-row')[0]), 'no money field');
});
T('only the last N entries are shown, and the count says so', () => {
  const many = { ledger: { entries: Array.from({ length: 40 }, (_, i) => ({ module: 'm', reason: `r${i}`, hash: String(i) })) } };
  const m = L.deriveLedger(many, null, { lastN: 5 });
  eq(m.count, 40);
  eq(m.entries.length, 5);
  eq(m.entries[0].lineNo, 36);
  has(text(L.renderLedger(m, DOC)), 'last 5 of 40 entries');
});
T('a head the host reports that disagrees with the last line is surfaced', () => {
  const m = L.deriveLedger({ ledger: { ...SAMPLE.ledger, head: 'c'.repeat(64) } });
  ok(m.abstentions.some((a) => a.code === 'head_disagrees'));
  has(text(L.renderLedger(m, DOC)), 'Showing both; believing neither');
});
T('a count the host reports that disagrees with the lines read is surfaced', () => {
  const m = L.deriveLedger({ ledger: { ...SAMPLE.ledger, count: 99 } });
  ok(m.abstentions.some((a) => a.code === 'count_disagrees'));
  has(text(L.renderLedger(m, DOC)), 'lines reported by host');
});
T('a parsed-entries source is flagged as not byte-exact BEFORE anything is verified', () => {
  const m = L.deriveLedger(SAMPLE);
  eq(m.canVerifyExactly, false);
  ok(m.abstentions.some((a) => a.code === 'reserialised_source'));
  has(text(L.renderLedger(m, DOC)), 're-serialised verification only');
});
T('before VERIFY is pressed the verdict is NOT_RUN, never VERIFIED', () => {
  const el = L.renderLedger(L.deriveLedger(SAMPLE), DOC);
  eq(one(el, 'ledger-verdict').textContent, 'NOT_RUN');
  has(text(el), 'Press VERIFY');
  // and no genesis hash masquerading as a checked head
  has(one(el, 'ledger-verification').children.map((c) => text(c)).join(' '), '— not run');
  hasNot(text(one(el, 'ledger-verification')), '0000000000000000…');
});
T('the VERIFY button exists and calls the injected handler', () => {
  let clicks = 0;
  const el = L.renderLedger(L.deriveLedger(SAMPLE), DOC, { onVerify: () => { clicks++; } });
  const btn = one(el, 'ledger-verify');
  eq(btn.tagName, 'button');
  eq(btn.attrs.type, 'button');
  eq(btn.textContent, 'VERIFY CHAIN');
  btn.fire('click');
  eq(clicks, 1);
});
T('a broken line is marked on the row a judge is looking at', () => {
  const v = { verdict: L.BROKEN, ok: false, linesChecked: 1, head: 'a'.repeat(64), error: 'line 2: hash mismatch — stored aaaa… recomputed bbbb…', brokenLine: 2, exact: true, durationMs: 1.2 };
  const m = L.deriveLedger(SAMPLE, v);
  const el = L.renderLedger(m, DOC);
  eq(byClass(el, 'ledger-row-broken').length, 1);
  eq(byData(el, 'broken', 'true').filter((n) => n.className.includes('ledger-row')).length, 1);
  has(text(el), 'line 2: hash mismatch');
  assertNoGreenNoRed(el, 'broken ledger');
});
T('every verify verdict renders, and none of them is green or red', () => {
  for (const verdict of L.VERIFY_VERDICTS) {
    const el = L.renderLedger(L.deriveLedger(SAMPLE, { verdict, ok: verdict === L.VERIFIED, linesChecked: 3, head: 'f'.repeat(64), error: null, brokenLine: null, exact: true, durationMs: 0.5 }), DOC);
    eq(one(el, 'ledger-verdict').textContent, verdict);
    has(text(el), L.VERIFY_NOTES[verdict].slice(0, 30));
    assertNoGreenNoRed(el, `ledger ${verdict}`);
  }
});
T('the ledger palette has no green and no red', () => {
  for (const v of Object.values(L.PALETTE)) {
    for (const re of FORBIDDEN) ok(!re.test(v), `${v} matches ${re}`);
  }
});
T('renderLedger refuses a document that is not one', () => {
  throws(() => L.renderLedger(L.deriveLedger(null), {}), 'not a document');
});
T('a raw JSONL source is read into rows AND flagged as byte-exact-verifiable', () => {
  const jsonl = [
    JSON.stringify({ ts: 't1', module: 'session', reason: 'session_opened', prev_hash: L.GENESIS, hash: 'a'.repeat(64) }),
    JSON.stringify({ ts: 't2', module: 'paisa', reason: 'priced_from_gallery', price_paise: 4500, prev_hash: 'a'.repeat(64), hash: 'b'.repeat(64) }),
    '',
  ].join('\n');
  const m = L.deriveLedger({ ledger: { jsonl } });
  eq(m.sourceKind, 'jsonl');
  eq(m.canVerifyExactly, true);
  eq(m.count, 2, 'the blank trailing line is not a line');
  eq(m.head, 'b'.repeat(64), 'the head was read off the last line');
  has(text(L.renderLedger(m, DOC)), 'byte-exact verification available');
});
T('a malformed line stops the read and is named, not skipped', () => {
  const m = L.deriveLedger({ ledger: { jsonl: '{"a":1}\n{"b":' } });
  ok(m.abstentions.some((a) => a.code === 'malformed_line'));
  has(text(L.renderLedger(m, DOC)), 'line 2: not valid JSON');
});
T('an empty chain is genesis, but an UNREADABLE one is not allowed to borrow that', () => {
  eq(L.deriveLedger({ ledger: { jsonl: '' } }).head, L.GENESIS);
  eq(L.deriveLedger({ ledger: { entries: [] } }).head, L.GENESIS);
  const bad = L.deriveLedger({ ledger: { count: 3 } });
  eq(bad.head, null, 'an unreadable ledger must not report head=genesis');
  ok(bad.abstentions.some((a) => a.code === 'unreadable_ledger'));
  has(text(L.renderLedger(bad, DOC)), 'I DO NOT KNOW');
});
T('a line with no module is an abstention, not a blank cell', () => {
  const m = L.deriveLedger({ ledger: { entries: [{ reason: 'x', hash: 'h' }] } });
  ok(m.abstentions.some((a) => a.code === 'module_missing'));
  has(text(L.renderLedger(m, DOC)), 'module unknown');
});

// ===========================================================================
G('LEDGER — the panel object, end to end on the real chain');
// ===========================================================================

await TA('onState mounts, VERIFY runs, and the verdict reaches the DOM', async () => {
  if (!TMP) { console.log('   (skipped: no real chain)'); return; }
  const doc = makeDoc([L.PANEL_ID]);
  const p = L.createPanel({ doc });
  eq(p.onState({ ledger: { jsonl: CHAIN, head: PY_HEAD, count: PY_COUNT } }), true);
  const host = doc.byId[L.PANEL_ID];
  eq(host.dataset.verdict, L.NOT_RUN);
  eq(host.dataset.count, String(PY_COUNT));
  eq(p.model.canVerifyExactly, true);

  // press the button the way a judge would
  one(host.children[0], 'ledger-verify').fire('click');
  const r = await p.verify();
  eq(r.verdict, L.VERIFIED, r.error || '');
  eq(host.dataset.verdict, L.VERIFIED);
  eq(one(host.children[0], 'ledger-verdict').textContent, 'VERIFIED');
  has(text(host.children[0]), 'byte-exact');
  assertNoGreenNoRed(host.children[0], 'verified ledger panel');
});
await TA('a tampered chain reaches the DOM as BROKEN with the line named', async () => {
  if (!TMP) { console.log('   (skipped: no real chain)'); return; }
  const doc = makeDoc([L.PANEL_ID]);
  const p = L.createPanel({ doc });
  const bad = CHAIN.split('\n').filter((s) => s.trim() !== '');
  bad[1] = bad[1].replace('"price_paise": 4500', '"price_paise": 45');
  p.onState({ ledger: { jsonl: `${bad.join('\n')}\n` } });
  const r = await p.verify();
  eq(r.verdict, L.BROKEN);
  const host = doc.byId[L.PANEL_ID];
  eq(host.dataset.verdict, L.BROKEN);
  has(text(host.children[0]), 'line 2: hash mismatch');
  eq(byClass(host.children[0], 'ledger-row-broken').length, 1);
});
await TA('a NEW state drops the old verification instead of vouching for new lines', async () => {
  if (!TMP) { console.log('   (skipped: no real chain)'); return; }
  const doc = makeDoc([L.PANEL_ID]);
  const p = L.createPanel({ doc });
  p.onState({ ledger: { jsonl: CHAIN } });
  eq((await p.verify()).verdict, L.VERIFIED);
  p.onState({ ledger: { jsonl: CHAIN } });
  eq(p.model.verification.verdict, L.NOT_RUN, 'a stale VERIFIED was left on screen');
  eq(doc.byId[L.PANEL_ID].dataset.verdict, L.NOT_RUN);
});
T('attach registers the ledger panel under its id', () => {
  const seen = [];
  L.attach((id, panel) => seen.push([id, panel]), { doc: makeDoc([]) });
  eq(seen[0][0], 'panel-ledger');
  eq(typeof seen[0][1].onState, 'function');
  eq(typeof seen[0][1].onFrame, 'function');
});

// ===========================================================================
G('all three panels — source discipline');
// ===========================================================================

const SRC = {
  'chilla.js': readFileSync(CHILLA_PATH, 'utf8'),
  'saaf.js': readFileSync(SAAF_PATH, 'utf8'),
  'ledger.js': readFileSync(LEDGER_PATH, 'utf8'),
};

T('INVARIANT 3: no panel fetches anything, ever', () => {
  for (const [name, src] of Object.entries(SRC)) {
    for (const bad of ['fetch(', 'XMLHttpRequest', 'importScripts', 'new WebSocket',
      'createObjectURL', 'navigator.sendBeacon', 'eval(']) {
      ok(!src.includes(bad), `${name} contains ${bad}`);
    }
  }
});
T('INVARIANT 3: no panel loads a model, a weight file or a CDN', () => {
  for (const [name, src] of Object.entries(SRC)) {
    for (const bad of ['http://', 'https://', '.onnx', '.tflite', '.pb', 'cdn.', 'unpkg', 'jsdelivr']) {
      // the SVG namespace is a URL that is never fetched; allow exactly that one
      const cleaned = src.split('http://www.w3.org/2000/svg').join('');
      ok(!cleaned.includes(bad), `${name} contains ${bad}`);
    }
  }
});
T('the only cross-file import any panel makes is the app’s own money/geometry core', () => {
  for (const [name, src] of Object.entries(SRC)) {
    const specs = [...src.matchAll(/^import[^;]*?from\s+'([^']+)'/gm)].map((m) => m[1]);
    for (const s of specs) ok(s === '../app.js', `${name} imports ${s}`);
  }
  measured.panel_external_imports = 0;
});
T('CSP: no panel sets a style ATTRIBUTE (style-src has no unsafe-inline)', () => {
  for (const [name, src] of Object.entries(SRC)) {
    ok(!/setAttribute\(\s*['"]style['"]/.test(src), `${name} sets a style attribute`);
    ok(!src.includes('innerHTML'), `${name} uses innerHTML`);
    ok(!src.includes('insertAdjacentHTML'), `${name} uses insertAdjacentHTML`);
    ok(!src.includes('document.write'), `${name} uses document.write`);
  }
  // and prove it on the rendered trees: styling arrives via CSSOM, never an attr
  for (const el of [
    C.renderChilla(C.deriveChilla(chillaState()), DOC),
    S.renderSaaf(S.deriveSaaf(burst()), DOC),
    L.renderLedger(L.deriveLedger(SAMPLE), DOC),
  ]) {
    for (const n of nodes(el)) ok(!('style' in n.attrs), `<${n.tagName}> carries a style attribute`);
  }
});
T('all three panels announce themselves on the shared descriptor list', () => {
  const mine = new Set(['panel-chilla', 'panel-saaf', 'panel-ledger']);
  const listed = (globalThis.GAWAAH_PANELS || []).filter((d) => mine.has(d.id));
  eq(listed.length, 3, 'a panel did not publish a descriptor');
  for (const d of listed) {
    eq(d.attached, false, 'nothing auto-attached without a registrar');
    eq(typeof d.attach, 'function');
    eq(typeof d.createPanel, 'function');
  }
  measured.panels_registered = listed.length;
});
T('the attachXPanel(opts) convention works too, and reports what it did', () => {
  const seen = [];
  const register = (id, spec) => { seen.push([id, spec]); return { ok: true }; };
  const r1 = C.attachChillaPanel({ register, doc: makeDoc([C.PANEL_ID]) });
  const r2 = S.attachSaafPanel({ register, doc: makeDoc([S.PANEL_ID]) });
  const r3 = L.attachLedgerPanel({ register, doc: makeDoc([L.PANEL_ID]) });
  eq(seen.map(([id]) => id).join(','), 'panel-chilla,panel-saaf,panel-ledger');
  for (const [, spec] of seen) {
    eq(typeof spec.onState, 'function');
    eq(typeof spec.onFrame, 'function');
  }
  for (const r of [r1, r2, r3]) eq(r.registered, true);
  // the destructured onState must still work — the panels close over their
  // state rather than reading `this`, which is what makes that safe
  eq(seen[0][1].onState(chillaState()), true);
  eq(r1.panel.model.match.verdict, 'MATCHED');
});
T('with no registrar anywhere, attachXPanel reports registered:false, not a crash', () => {
  const r = C.attachChillaPanel({ doc: makeDoc([]) });
  eq(r.registered, false);
  eq(r.registration, null);
  eq(typeof r.panel.onState, 'function');
});
T('createPanel accepts both the doc/root and document/host option names', () => {
  const doc = makeDoc([]);
  const host = makeEl('section');
  const p = C.createPanel({ document: doc, host });
  eq(p.onState(chillaState()), true);
  eq(host.children.length, 1);
});
T('no panel formats money with toFixed or a division — that is app.js’s job', () => {
  for (const [name, src] of Object.entries(SRC)) {
    ok(!/paise[^\n]*toFixed/.test(src), `${name} formats paise with toFixed`);
    ok(!/\bpaise\s*\/\s*100/.test(src), `${name} divides paise by 100`);
  }
});
T('all three panels export the registerPanel contract', () => {
  for (const M of [C, S, L]) {
    eq(typeof M.PANEL_ID, 'string');
    eq(typeof M.createPanel, 'function');
    eq(typeof M.attach, 'function');
    const p = M.createPanel({ doc: makeDoc([]) });
    eq(p.id, M.PANEL_ID);
    eq(typeof p.onState, 'function');
    eq(typeof p.onFrame, 'function');
  }
  eq(C.PANEL_ID, 'panel-chilla');
  eq(S.PANEL_ID, 'panel-saaf');
  eq(L.PANEL_ID, 'panel-ledger');
});
T('every panel survives being handed junk instead of a state', () => {
  for (const junk of [null, undefined, 0, '', 'nope', [], { chilla: 5 }, { saaf: 'x' }, { ledger: 7 }]) {
    const a = C.renderChilla(C.deriveChilla(junk), DOC);
    const b = S.renderSaaf(S.deriveSaaf(junk), DOC);
    const c = L.renderLedger(L.deriveLedger(junk), DOC);
    for (const el of [a, b, c]) {
      ok(nodes(el).length > 5, 'a junk state rendered almost nothing');
      assertNoGreenNoRed(el, `junk ${JSON.stringify(junk)}`);
    }
  }
});
T('every panel has at least one visible "I do not know" region', () => {
  const cold = [
    C.renderChilla(C.deriveChilla(null), DOC),
    S.renderSaaf(S.deriveSaaf(null), DOC),
    L.renderLedger(L.deriveLedger(null), DOC),
  ];
  const counts = [];
  for (const el of cold) {
    const n = byClass(el, 'chilla-abstain').length
      + byClass(el, 'saaf-abstain').length
      + byClass(el, 'ledger-abstain').length;
    ok(n > 0, 'a cold panel listed no abstentions');
    has(text(el), 'I do not know');
    counts.push(n);
  }
  measured.cold_abstentions_chilla_saaf_ledger = counts.join('/');
});
T('a fully-populated, fully-healthy state STILL renders the standing honesty text', () => {
  has(text(C.renderChilla(C.deriveChilla(chillaState()), DOC)), 'can never show GREEN');
  has(text(S.renderSaaf(S.deriveSaaf(burst()), DOC)), 'UNDERSTATES');
  has(text(L.renderLedger(L.deriveLedger(SAMPLE), DOC)), 'prev_hash is inside the hashed payload');
});

if (TMP) rmSync(TMP, { recursive: true, force: true });

// ============================================================== report =====
console.log('\n──────────────────────────────────────────────────────────────');
console.log('MEASURED NUMBERS (produced by this run)');
for (const [k, v] of Object.entries(measured)) console.log(`  ${k.padEnd(38)} ${v}`);

if (failures.length) {
  console.log('\nFAILURES');
  for (const f of failures) console.log(`  ✗ ${f}`);
}
console.log('\n──────────────────────────────────────────────────────────────');
console.log(`${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
