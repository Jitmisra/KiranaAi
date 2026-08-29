/* GAWAAH counter — node self-test for the DEMO panel.
 *
 *   cd /Users/agnik/Desktop/razor && node web/panels/demo.test.mjs
 *
 * There is no browser here. demo.js is written as pure functions over a
 * document-like object, so the whole render path — badge, headline tiles,
 * commentary, controls, beat list and abstentions — runs against the ~70-line
 * DOM shim below, and the socket tap runs against a fake global.
 *
 * Four kinds of test carry the weight:
 *
 *   THE SCRIPT IS CHECKED AGAINST THE SCRIPT. Every beat boundary, tap frame
 *   and total this panel prints is also a value on gawaah.brain_server's
 *   SimScript. The tests shell out to the repo venv, read the Python values,
 *   and assert equality. A beat list that captions the wrong photograph is
 *   worse than no beat list, and this is what catches it.
 *
 *   THE PANEL IS DRIVEN BY A REAL RUN. `python -m gawaah.brain_server --sim
 *   --dry-run` is executed and every message the browser would receive is fed
 *   through reduceDemo in order. The assertions about what the panel shows at
 *   frames 31, 38, 42, 53 and 62 are assertions about what the brain actually
 *   did on those frames, not about a JS-made fixture.
 *
 *   NO GREEN, ANYWHERE, ON THE PIXELS. A walker refuses green or fraud-red in
 *   any class name, data attribute, style value or attribute, and greps the
 *   shipped stylesheet for the same. Prose is exempt on purpose: this panel is
 *   REQUIRED to say the words "never comes from this panel", and a scan that
 *   banned the word would ban the promise.
 *
 *   EVERY ABSTENTION STAYS REACHABLE. There is one test per I-DO-NOT-KNOW
 *   state that constructs the world in which it is the honest answer and
 *   asserts the panel says so.
 */
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { readFileSync } from 'node:fs';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB = dirname(HERE);
const REPO = dirname(WEB);
const PY = join(REPO, '.venv', 'bin', 'python');
const DEMO_PATH = join(HERE, 'demo.js');

const D = await import(pathToFileURL(DEMO_PATH).href);

// ---------------------------------------------------------------- harness --
let pass = 0, fail = 0, group = '';
const failures = [];
const measured = {};
function T(name, fn) {
  try { fn(); pass++; }
  catch (e) { fail++; failures.push(`${group} :: ${name}\n      ${e.stack || e.message}`); }
}
function G(name) { group = name; console.log(`\n── ${name}`); }
function ok(cond, msg) { if (!cond) throw new Error(msg || 'assertion failed'); }
function eq(a, b, msg) {
  if (!Object.is(a, b)) throw new Error(`${msg || 'eq'}: got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
}
function deepEq(a, b, msg) {
  const x = JSON.stringify(a), y = JSON.stringify(b);
  if (x !== y) throw new Error(`${msg || 'deepEq'}:\n  got  ${x}\n  want ${y}`);
}
function throws(fn, what) {
  let threw = false;
  try { fn(); } catch { threw = true; }
  if (!threw) throw new Error(`expected a throw: ${what}`);
}
function has(hay, needle, msg) {
  if (!String(hay).includes(needle)) {
    throw new Error(`${msg || 'contains'}: ${JSON.stringify(needle)} not in ${JSON.stringify(String(hay).slice(0, 500))}`);
  }
}
function hasNot(hay, needle, msg) {
  if (String(hay).includes(needle)) {
    throw new Error(`${msg || 'must not contain'}: ${JSON.stringify(needle)} IS in the text`);
  }
}

// ------------------------------------------------------------- DOM shim ----
// Small on purpose: if the panel needs more of the DOM than this, it is doing
// something a pure render function should not be doing.
function makeEl(tag) {
  const el = {
    tagName: tag, children: [], attrs: {}, dataset: {}, style: {},
    className: '', textContent: '', listeners: {}, id: '',
    appendChild(c) { el.children.push(c); return c; },
    append(...c) { el.children.push(...c); },
    replaceChildren(...c) { el.children = c.slice(); },
    setAttribute(k, v) { el.attrs[k] = String(v); },
    removeAttribute(k) { delete el.attrs[k]; },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(el.attrs, k) ? el.attrs[k] : null; },
    addEventListener(t, f) { (el.listeners[t] ||= []).push(f); },
    removeEventListener() {},
    remove() {},
    fire(t, ev) { return (el.listeners[t] || []).map((f) => f(ev)); },
  };
  return el;
}
function makeDoc(ids = []) {
  const byId = Object.fromEntries(ids.map((i) => [i, makeEl('section')]));
  const doc = {
    byId,
    head: makeEl('head'),
    body: makeEl('body'),
    documentElement: makeEl('html'),
    readyState: 'complete',
    createElement: (t) => makeEl(t),
    getElementById: (i) => byId[i] ?? null,
    querySelector: () => null,
    addEventListener() {},
  };
  return doc;
}
const DOC = makeDoc();

function nodes(root) {
  const out = [];
  (function walk(n) { out.push(n); for (const c of n.children) walk(c); })(root);
  return out;
}
function text(root) { return nodes(root).map((n) => n.textContent).join(' '); }
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
 * INVARIANT 2, ASSERTED ON THE PIXELS. Same list the other panel tests use.
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
      ...Object.entries(n.attrs).filter(([k]) => k !== 'alt' && k !== 'aria-label' && k !== 'title')
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
  const r = spawnSync(PY, ['-c', code], { cwd: REPO, encoding: 'utf8', maxBuffer: 64 << 20 });
  if (r.status !== 0) throw new Error(`python failed: ${r.stderr || r.error}`);
  return r.stdout;
}
let PYS = null, pyAvailable = true, pyWhy = '';
try {
  PYS = JSON.parse(python(`
import json
import gawaah.brain_server as BS
from gawaah.brain_server import SimScript
import gawaah.brain as B
s = SimScript()
print(json.dumps({
  "PHASES": [list(p) for p in SimScript.PHASES],
  "total_frames": s.total_frames,
  "done_at": s.done_at,
  "pay_at": s.pay_at,
  "enrol_at": s.enrol_at,
  "sticker_name": s.sticker_name,
  "period_s": s.period_s,
  "phase_at": [list(s.phase_at(i)) for i in range(0, s.total_frames + 4)],
  "starts": {name: s._phase_start(name) for name, _ in SimScript.PHASES},
  "refuse_after_frames": B.REFUSE_AFTER_FRAMES,
  "goods_settle": SimScript.GOODS_SETTLE,
  "goods_y0": SimScript.GOODS_Y0_MM,
  "goods_y1": SimScript.GOODS_Y1_MM,
  "goods_step": SimScript.GOODS_STEP_MM,
  "SIM_MODES": list(BS.SIM_MODES),
  "SIM_ACTIONS": list(BS.SIM_ACTIONS),
  "SIM_BEATS": list(BS.SIM_BEATS),
  "beat_at": [s.beat_at(i) for i in (0, 8, 31, 38, 50, 62, 71)],
}))
`));
} catch (e) {
  pyAvailable = false;
  pyWhy = (e && e.message) || String(e);
}

// ======================================================================== 1 ==
G('1. the script constants match gawaah/brain_server.py, value for value');

T('python is reachable (otherwise the cross-language checks are vacuous)', () => {
  ok(pyAvailable, `the repo venv could not be driven: ${pyWhy}`);
});

if (pyAvailable) {
  T('SIM_PHASES is SimScript.PHASES', () => {
    deepEq(D.SIM_PHASES.map((p) => [p[0], p[1]]), PYS.PHASES);
  });
  T('TOTAL_FRAMES is SimScript.total_frames', () => eq(D.TOTAL_FRAMES, PYS.total_frames));
  T('DONE_AT is SimScript.done_at', () => eq(D.DONE_AT, PYS.done_at));
  T('PAY_AT is SimScript.pay_at', () => eq(D.PAY_AT, PYS.pay_at));
  T('ENROL_AT is SimScript.enrol_at', () => eq(D.ENROL_AT, PYS.enrol_at));
  T('STICKER_NAME is SimScript.sticker_name', () => eq(D.STICKER_NAME, PYS.sticker_name));
  T('SIM_PERIOD_S is SimScript.period_s', () => eq(D.SIM_PERIOD_S, PYS.period_s));
  T('REFUSE_AFTER_FRAMES is brain.REFUSE_AFTER_FRAMES', () => {
    eq(D.REFUSE_AFTER_FRAMES, PYS.refuse_after_frames);
  });
  T('phaseRanges() reproduces SimScript._phase_start for every phase', () => {
    for (const r of D.phaseRanges()) eq(r.from, PYS.starts[r.name], `start of ${r.name}`);
  });
  T('beatAt() agrees with SimScript.phase_at on EVERY frame, including past the end', () => {
    for (let i = 0; i < PYS.phase_at.length; i++) {
      const [name, k] = PYS.phase_at[i];
      const b = D.beatAt(i);
      eq(b.name, name, `phase name at frame ${i}`);
      eq(b.phaseIndex, k, `phase index at frame ${i}`);
    }
    measured.beatAt_frames_cross_checked = PYS.phase_at.length;
  });
  T('the tap frames named in MARKS are the frames the script actually taps', () => {
    const byFrame = Object.fromEntries(D.MARKS.map((m) => [m.frame, m.verb]));
    eq(byFrame[PYS.enrol_at], 'enrol_sticker');
    eq(byFrame[PYS.done_at], 'done');
    ok(PYS.pay_at in byFrame, 'the pay frame is not on the beat list');
    // and the pay is deliberately NOT modelled as a client message
    has(D.MARKS.find((m) => m.frame === PYS.pay_at).verb, 'not a client message');
  });
  T('every MARK falls inside the beat it is printed under', () => {
    for (const mk of D.MARKS) {
      const b = D.BEATS.find((x) => mk.frame >= x.from && mk.frame <= x.to);
      ok(b, `mark at frame ${mk.frame} falls in no beat`);
    }
  });
  T('SIM_MODES matches brain_server.SIM_MODES', () => {
    deepEq(D.SIM_MODES.slice(), PYS.SIM_MODES);
    for (const mode of PYS.SIM_MODES) {
      ok(D.MODE_NOTE[mode], `no operator-language note for mode ${mode}`);
    }
  });
  T('every control this panel offers is a verb brain_server accepts', () => {
    for (const a of D.CONTROL_ACTIONS) {
      ok(PYS.SIM_ACTIONS.includes(a), `the brain does not accept '${a}'`);
    }
    measured.brain_sim_actions = PYS.SIM_ACTIONS.join(',');
  });
  T('the beat NAMES are brain_server.SIM_BEATS, minus the one that is a mode', () => {
    // SIM_BEATS has a sixth key, `hold`, which is what the driver reports past
    // the last frame. It is not a beat of the script and must not be a row.
    deepEq(D.BEATS.map((b) => b.name), PYS.SIM_BEATS.filter((k) => k !== D.HOLD_BEAT));
    ok(PYS.SIM_BEATS.includes(D.HOLD_BEAT), 'HOLD_BEAT is not a key the brain uses');
    eq(D.BEATS.length, PYS.SIM_BEATS.length - 1);
  });
  T('beatAt() names the same beat SimScript.beat_at() does at every sampled frame', () => {
    for (const got of PYS.beat_at) {
      const b = D.beatAt(got.sim_frame ?? got.index_frame ?? null);
      // beat_at returns {name, index, of, label, detail}; sample by frame below
      ok(typeof got.name === 'string');
    }
    // sampled frames, in the same order they were requested from python
    const frames = [0, 8, 31, 38, 50, 62, 71];
    frames.forEach((f, i) => {
      eq(D.beatAt(f).name, PYS.beat_at[i].name, `frame ${f}`);
      eq(D.beatAt(f).phaseIndex, PYS.beat_at[i].index, `frame ${f} index`);
    });
  });
  T('the beat prose does not quote a frame number outside its own beat', () => {
    // Every "frame N" in a beat's prose must be a frame that beat covers, or a
    // frame explicitly attributed to another beat by name. Catches a beat that
    // was edited without its numbers being re-read.
    for (const b of D.BEATS) {
      const prose = `${b.what} ${b.why} ${b.watch}`;
      for (const m of prose.matchAll(/\bframe (\d+)\b/g)) {
        const n = Number(m[1]);
        ok(n >= b.from && n <= b.to, `beat ${b.name} cites frame ${n}, outside ${b.from}-${b.to}`);
      }
    }
  });
}

// ======================================================================== 2 ==
G('2. money is integer paise and the rupee string is built without a float');

T('rupeesFromPaise on the sim price and the edges', () => {
  eq(D.rupeesFromPaise(2850), '28.50');
  eq(D.rupeesFromPaise(0), '0.00');
  eq(D.rupeesFromPaise(5), '0.05');
  eq(D.rupeesFromPaise(99), '0.99');
  eq(D.rupeesFromPaise(100), '1.00');
  eq(D.rupeesFromPaise(123456789), '1234567.89');
  eq(D.rupeesFromPaise(-2850), '-28.50');
});
T('a non-integer paise value is REFUSED, not rounded', () => {
  for (const bad of [28.5, '2850', null, undefined, NaN, Infinity, {}]) {
    eq(D.rupeesFromPaise(bad), null, `rupeesFromPaise(${JSON.stringify(bad)})`);
    eq(D.paiseLabel(bad), '—');
  }
});
T('paiseLabel prints the currency and the abstention dash', () => {
  eq(D.paiseLabel(2850), '₹28.50');
  eq(D.paiseLabel(null), '—');
});
T('countLabel and headLabel abstain rather than invent', () => {
  eq(D.countLabel(0), '0');
  eq(D.countLabel(3), '3');
  eq(D.countLabel(null), '—');
  eq(D.countLabel(-1), '—');
  eq(D.headLabel('83b90dfad97f3c01b319'), '83b90dfad97f…');
  eq(D.headLabel('short'), '—');
  eq(D.headLabel(null), '—');
});
T('no float ever reaches the rendered total: 1..2000 paise round-trip exactly', () => {
  let checked = 0;
  for (let p = 0; p <= 2000; p++) {
    const s = D.rupeesFromPaise(p);
    const [maj, min] = s.split('.');
    eq(Number(maj) * 100 + Number(min), p, `round trip ${p}`);
    checked++;
  }
  measured.paise_round_trips = checked;
});

// ======================================================================== 3 ==
G('3. the model folds brain messages and abstains where it must');

const st = (o = {}) => ({
  type: 'state', frame_index: 0, ts: 'x', session_id: 's', session_state: 'IDLE',
  mat_lock: {}, placements: [], lines: [], total_paise: 0, amber_items: [],
  amber_count: 0, exceptions: [], ledger_head: 'a'.repeat(64), ledger_lines: 1,
  net_crossings: 0, crossings_amber: false, frozen: false, online: true,
  money_authorised: false, intent_amount_paise: null, nonce: null, short_url: null,
  settled_payment_id: null, last_webhook_reason: null, ...o,
});

T('a cold model knows nothing and says so on every headline', () => {
  const m = D.initialModel();
  eq(m.totalPaise, null);
  eq(m.amberCount, null);
  eq(m.ledgerLines, null);
  eq(D.beatOf(m).source, D.BEAT_UNKNOWN);
  eq(D.provenanceOf(m).code, D.PROV_UNKNOWN);
  eq(D.livenessOf(m).running, null);
  eq(D.commentaryFor(m).code, D.C_NO_BRAIN);
});
T('junk instead of a message leaves the model untouched', () => {
  const m = D.initialModel();
  for (const junk of [null, undefined, 0, '', 'nope', [], {}, { type: 5 }]) {
    eq(D.reduceDemo(m, junk), m, `junk ${JSON.stringify(junk)} changed the model`);
  }
});
T('an unknown message type is counted and otherwise ignored', () => {
  const m = D.reduceDemo(D.initialModel(), { type: 'weather', sunny: true }, 1000);
  eq(m.messages, 1);
  eq(m.byType.weather, 1);
  eq(m.totalPaise, null);
});
T('a state message moves all three headline numbers', () => {
  const m = D.reduceDemo(D.initialModel(), st({
    total_paise: 2850, amber_count: 0, ledger_lines: 7, frame_index: 31,
    lines: [{ item_id: 't1', name: 'PACKET', price_paise: 2850, amber: false }],
  }), 1000);
  eq(m.totalPaise, 2850);
  eq(m.amberCount, 0);
  eq(m.ledgerLines, 7);
  eq(m.frameIndex, 31);
  eq(m.lineCount, 1);
});
T('a ledger message can move the audit numbers on its own', () => {
  const m = D.reduceDemo(D.initialModel(), { type: 'ledger', head: 'b'.repeat(64), count: 32 }, 1);
  eq(m.ledgerLines, 32);
  eq(m.ledgerHead, 'b'.repeat(64));
});
T('amber lines are named, with their reason, and named as EXCLUDED', () => {
  const m = D.reduceDemo(D.initialModel(), st({
    session_state: 'AMBER', total_paise: 0, amber_count: 1,
    amber_items: [{ item_id: 't7', name: null, price_paise: null, amber: true }],
    lines: [{ item_id: 't7', amber: true, price_paise: null, reason: 'no_candidate_in_footprint' }],
  }), 1);
  eq(m.amberCount, 1);
  deepEq([...m.amberNames], ['t7']);
  deepEq([...m.amberReasons], ['no_candidate_in_footprint']);
  const c = D.commentaryFor(m);
  eq(c.code, D.C_AMBER);
  has(c.text, 'EXCLUDED from the total');
  has(c.text, 't7');
  has(c.text, 'no_candidate_in_footprint');
});
T('THE AMBER HEADLINE COUNTS EXCLUDED BILL LINES, and disagreement is declared', () => {
  // The real sim run reports amber_count 0 while three of its own lines carry
  // amber:true. The panel shows the excluded lines — the number the total is
  // missing — and prints the brain's field beside it rather than hiding either.
  const m = D.reduceDemo(D.initialModel(), st({
    session_state: 'AMBER', total_paise: 0, amber_count: 0, amber_items: [],
    lines: [
      { item_id: 't2', amber: true, price_paise: null, reason: 'no_candidate_in_footprint' },
      { item_id: 't3', amber: true, price_paise: null, reason: 'MERGED_CONTOUR' },
      { item_id: 't9', amber: false, price_paise: 2850, reason: 'priced_from_gallery' },
    ],
  }), 1);
  eq(m.amberCount, 2);
  eq(m.brainAmberCount, 0);
  const codes = D.abstentionsFor(m).map((a) => a.code);
  ok(codes.includes('demo_amber_count_disagreement'), `got ${codes.join(',')}`);
  const t = text(D.renderHeadline(m, DOC));
  has(t, 'MERGED_CONTOUR');
  has(t, "The brain's own amber_count field says 0");
});
T('deriveDemo folds a list, with or without timestamps', () => {
  const a = D.deriveDemo([st({ total_paise: 100 }), st({ total_paise: 200 })]);
  eq(a.totalPaise, 200);
  eq(a.messages, 2);
  const b = D.deriveDemo([{ at: 10, msg: st({ total_paise: 1 }) }, { at: 20, msg: st({ total_paise: 2 }) }]);
  eq(b.totalPaise, 2);
  eq(b.now, 20);
});

// ======================================================================== 4 ==
G('4. provenance: three states, and UNKNOWN is reachable and stays reachable');

T('no evidence at all -> PROVENANCE UNKNOWN, and it refuses both answers', () => {
  const p = D.provenanceOf(D.initialModel());
  eq(p.code, D.PROV_UNKNOWN);
  has(p.detail, 'cannot tell');
  has(p.detail, 'not going to guess');
  has(p.evidence, 'has not been read yet');
});
T('a FAILED /health is still UNKNOWN, and names the failure', () => {
  const m = D.noteHealthError(D.initialModel(), 'HTTP 503', 5);
  const p = D.provenanceOf(m);
  eq(p.code, D.PROV_UNKNOWN);
  has(p.evidence, 'HTTP 503');
});
T('/health sim:true -> SIMULATED, with the evidence attached', () => {
  const m = D.noteHealth(D.initialModel(), { ok: true, sim: true }, 5);
  const p = D.provenanceOf(m);
  eq(p.code, D.PROV_SIMULATED);
  has(p.evidence, 'sim: true');
  has(p.detail, 'not seen by a camera');
});
T('/health sim:false -> NOT SIMULATED', () => {
  const p = D.provenanceOf(D.noteHealth(D.initialModel(), { ok: true, sim: false }, 5));
  eq(p.code, D.PROV_LIVE);
  has(p.detail, 'NO synthetic frame source at all');
});
T('a health body with no `sim` key does NOT decide provenance', () => {
  const p = D.provenanceOf(D.noteHealth(D.initialModel(), { ok: true }, 5));
  eq(p.code, D.PROV_UNKNOWN);
});
T('a brain sim message outranks /health, in both directions', () => {
  let m = D.noteHealth(D.initialModel(), { sim: false }, 1);
  m = D.reduceDemo(m, { type: 'sim', simulated: true }, 2);
  eq(D.provenanceOf(m).code, D.PROV_SIMULATED);
  let n = D.noteHealth(D.initialModel(), { sim: true }, 1);
  n = D.reduceDemo(n, { type: 'sim', simulated: false }, 2);
  eq(D.provenanceOf(n).code, D.PROV_LIVE);
});
T('PRESSING RUN DEMO DOES NOT MAKE THE BADGE SAY SIMULATED', () => {
  // The badge is evidence-driven. This is the single most important property
  // of this panel: a button press is a request, not a fact about the frames.
  const p = D.createPanel({ doc: makeDoc([]), now: () => 1 });
  p.press('start');
  eq(D.provenanceOf(p.model).code, D.PROV_UNKNOWN);
});
T('the three badge headlines cannot be mistaken for one another', () => {
  const hs = [D.PROV_SIMULATED, D.PROV_LIVE, D.PROV_UNKNOWN].map((c) => D.BADGE_HEADLINE[c]);
  eq(new Set(hs).size, 3);
  has(hs[0], 'SIMULATED');
  has(hs[1], 'NOT SIMULATED');
  has(hs[2], 'PROVENANCE UNKNOWN');
  // "NOT SIMULATED" must not be readable as "SIMULATED" at a glance: the two
  // are additionally distinguished by the data attribute, checked below.
  ok(hs[0] !== hs[1]);
});
T('the badge stamps a distinct data-provenance for each state', () => {
  const seen = new Set();
  for (const m of [
    D.initialModel(),
    D.noteHealth(D.initialModel(), { sim: true }, 1),
    D.noteHealth(D.initialModel(), { sim: false }, 1),
  ]) {
    const el = D.renderBadge(m, DOC);
    seen.add(el.dataset.provenance);
  }
  eq(seen.size, 3);
  deepEq([...seen].sort(), [D.PROV_LIVE, D.PROV_UNKNOWN, D.PROV_SIMULATED].sort());
});
T('when SIMULATED, every headline tile is stamped and carries a visible SIM tag', () => {
  const m = D.reduceDemo(D.noteHealth(D.initialModel(), { sim: true }, 1), st({ total_paise: 2850 }), 2);
  const el = D.renderHeadline(m, DOC);
  const tiles = byClass(el, 'demo-head');
  eq(tiles.length, 3);
  for (const t of tiles) eq(t.dataset.simulated, '1', 'a tile was not stamped simulated');
  eq(byClass(el, 'demo-head-sim').length, 3, 'a headline number had no SIM tag');
  has(text(el), 'SIM');
});
T('when NOT simulated, no tile carries a SIM tag', () => {
  const m = D.reduceDemo(D.noteHealth(D.initialModel(), { sim: false }, 1), st({ total_paise: 1 }), 2);
  const el = D.renderHeadline(m, DOC);
  eq(byClass(el, 'demo-head-sim').length, 0);
  for (const t of byClass(el, 'demo-head')) eq(t.dataset.simulated, '0');
});

// ======================================================================== 5 ==
G('5. the beat: known, inferred, or refused — never guessed');

T('no frame_index -> no beat highlighted, and the abstention is listed', () => {
  const m = D.initialModel();
  eq(D.beatOf(m).index, null);
  const el = D.renderScript(m, DOC);
  eq(byData(el, 'current', '1').length, 0, 'a beat was highlighted with no position');
  has(text(D.renderAbstentions(m, DOC)), 'demo_no_script_position');
});
T('a frame_index infers a beat AND labels the inference', () => {
  const m = D.reduceDemo(D.initialModel(), st({ frame_index: 40 }), 1);
  const b = D.beatOf(m);
  eq(b.name, 'screen');
  eq(b.source, D.BEAT_INFERRED);
  const el = D.renderScript(m, DOC);
  eq(el.dataset.beatSource, D.BEAT_INFERRED);
  has(text(el), 'INFERRED');
  has(text(D.renderAbstentions(m, DOC)), 'demo_beat_is_inferred');
});
T('the brain-stamped sim_frame is used instead, and is NOT called an inference', () => {
  let m = D.reduceDemo(D.initialModel(), st({ frame_index: 40 }), 1);
  m = D.reduceDemo(m, st({
    frame_index: 40, simulated: true, sim_run: 1, sim_frame: 5,
    beat: 'settle', beat_label: 'bare mat — taking the reference',
    beat_detail: 'frame 0 becomes the empty-mat reference', beat_index: 5, beat_of: 8,
  }), 2);
  const b = D.beatOf(m);
  eq(b.frame, 5);
  eq(b.name, 'settle');
  eq(b.source, D.BEAT_FROM_BRAIN);
  hasNot(text(D.renderAbstentions(m, DOC)), 'demo_beat_is_inferred');
  has(text(D.renderScript(m, DOC)), 'The brain calls this beat "settle"');
  has(text(D.renderScript(m, DOC)), 'bare mat — taking the reference');
});
T('simTagOf keeps "no tag" and "tagged not simulated" as different facts', () => {
  eq(D.simTagOf({ type: 'state' }), null);
  eq(D.simTagOf(null), null);
  eq(D.simTagOf({ type: 'state', simulated: 'yes' }), null, 'a string was taken for a boolean');
  deepEq(D.simTagOf({ type: 'state', simulated: false }), { simulated: false });
  deepEq(
    D.simTagOf({ type: 'state', simulated: true, sim_frame: 3, beat: 'settle', sim_run: 2 }),
    { simulated: true, sim_run: 2, beat: 'settle', sim_frame: 3 },
  );
});
T('A BEAT THE BRAIN AND THIS PANEL DISAGREE ABOUT IS DECLARED, NOT RESOLVED', () => {
  // If the beat list on screen is captioning a different script from the one
  // the brain is running, the numbers are still real and the story is wrong.
  const m = D.reduceDemo(D.initialModel(), st({
    simulated: true, sim_frame: 3, beat: 'goods', beat_label: 'x', beat_index: 0, beat_of: 30,
  }), 1);
  const dis = D.disagreementOf(m);
  ok(dis, 'a mismatched beat name was silently accepted');
  eq(dis.brain, 'goods');
  eq(dis.panel, 'settle');
  const t = text(D.renderScript(m, DOC));
  has(t, 'DISAGREEMENT');
  has(t, 'do not describe the script this brain is running');
  has(text(D.renderAbstentions(m, DOC)), 'demo_beat_disagreement');
});
T('`hold` past the end of the script is NOT a disagreement', () => {
  const m = D.reduceDemo(D.initialModel(), st({
    simulated: true, sim_frame: D.TOTAL_FRAMES + 40, beat: D.HOLD_BEAT,
    beat_label: 'the script is finished — this is the final board',
  }), 1);
  eq(D.disagreementOf(m), null);
  eq(D.beatOf(m).name, 'tamper');
  eq(D.beatOf(m).complete, true);
  has(text(D.renderScript(m, DOC)), 'REPEATING ITS FINAL FRAME');
});
T('the beat-machine mode is rendered with what it means for the viewer', () => {
  for (const mode of D.SIM_MODES) {
    const m = D.reduceDemo(D.initialModel(), {
      type: 'sim', ok: true, enabled: true, simulated: true, mode,
      modes: D.SIM_MODES.slice(), actions: ['start', 'stop', 'pause', 'step', 'reset', 'status'],
      index: 0, of: D.TOTAL_FRAMES, run: 1, frames_emitted: 0, pending_steps: 0, fault: '',
    }, 1);
    eq(D.modeOf(m).mode, mode);
    const el = D.renderControls(m, DOC);
    eq(el.dataset.mode, mode);
    has(text(el), `BEAT MACHINE ${mode}`);
    has(text(el), D.MODE_NOTE[mode].slice(0, 30));
  }
});
T('a brain that does not accept a verb has that button disabled, not hidden', () => {
  const m = D.reduceDemo(D.initialModel(), {
    type: 'sim', ok: true, enabled: true, simulated: true, mode: 'RUNNING',
    actions: ['start', 'status'], index: 0, of: 72,
  }, 1);
  const el = D.renderControls(m, DOC);
  const btns = tagsOf(el, 'button');
  eq(btns.length, 4, 'a control was removed instead of disabled');
  eq(btns.find((b) => b.dataset.action === 'start').getAttribute('disabled'), null);
  for (const a of ['stop', 'step', 'reset']) {
    const b = btns.find((x) => x.dataset.action === a);
    eq(b.getAttribute('disabled'), 'disabled', `${a} was left enabled`);
    eq(b.dataset.unsupported, '1');
  }
});
T('a brain started WITHOUT --sim says so, in BOTH shapes it can say it', () => {
  // shape 1: a sim status that reports itself disabled
  const a = D.reduceDemo(D.initialModel(), {
    type: 'sim', ok: false, enabled: false, mode: 'STOPPED', reason: D.SIM_NOT_ENABLED,
  }, 1);
  has(text(D.renderAbstentions(a, DOC)), 'demo_sim_not_enabled');
  // shape 2: the refusal a brain with no sim at all really sends. Verbatim from
  // `build_sim_server(with_sim=False).handle({"type":"sim","action":"start"})`.
  const b = D.reduceDemo(D.noteControlSent(D.initialModel(), 'start', 1), {
    type: 'refused', reason: 'SIM_NOT_ENABLED',
    detail: 'no synthetic source is attached; start the server with --sim',
    actions: ['start', 'stop', 'pause', 'step', 'reset', 'status'], frame_index: -1,
  }, 2);
  const codes = D.abstentionsFor(b).map((x) => x.code);
  ok(codes.includes('demo_sim_not_enabled'), `got ${codes.join(',')}`);
  ok(codes.includes('demo_controls_refused'), 'the refusal itself was dropped');
  eq(b.control.code, D.CTRL_REFUSED);
  has(text(D.renderAbstentions(b, DOC)), 'nothing here to script');
});
T('the NOT SIMULATED badge does not claim frames are flowing when none are', () => {
  const m = D.noteHealth(D.initialModel(), { ok: true, sim: false }, 1);
  const t = text(D.renderBadge(m, DOC));
  has(t, 'NO synthetic frame source');
  has(t, 'If the counter is empty, that is because nothing has been shown to it');
  hasNot(t, 'every frame came from a camera');
});
T('a FAULTED beat machine is named, with its fault', () => {
  const m = D.reduceDemo(D.initialModel(), {
    type: 'sim', ok: false, enabled: true, simulated: true, mode: 'FAULTED',
    fault: 'source.frame(31) raised ValueError', actions: ['reset', 'status'],
  }, 1);
  const t = text(D.renderAbstentions(m, DOC));
  has(t, 'demo_sim_faulted');
  has(t, 'source.frame(31) raised ValueError');
});
T('exactly one beat is current, for every frame in the script', () => {
  for (let f = 0; f < D.TOTAL_FRAMES; f++) {
    const m = D.reduceDemo(D.initialModel(), st({ frame_index: f }), 1);
    const el = D.renderScript(m, DOC);
    eq(byData(el, 'current', '1').length, 1, `frame ${f} highlighted the wrong number of beats`);
  }
  measured.beats_highlighted_uniquely_for_frames = D.TOTAL_FRAMES;
});
T('past the end the LAST beat stays current and the panel says the script finished', () => {
  const m = D.reduceDemo(D.initialModel(), st({ frame_index: 9535 }), 1);
  const b = D.beatOf(m);
  eq(b.name, 'tamper');
  eq(b.complete, true);
  const el = D.renderScript(m, DOC);
  eq(byData(el, 'current', '1').length, 1);
  has(text(el), 'REPEATING ITS FINAL FRAME');
  has(text(el), 'does not loop');
});
T('beats before the current one are marked past, after are neither', () => {
  const m = D.reduceDemo(D.initialModel(), st({ frame_index: 55 }), 1);  // hand, index 3
  const el = D.renderScript(m, DOC);
  eq(byData(el, 'past', '1').length, 3);
  eq(byData(el, 'current', '1').length, 1);
});
T('the beat list names all five beats and prints their true frame ranges', () => {
  const el = D.renderScript(D.initialModel(), DOC);
  const t = text(el);
  for (const b of D.BEATS) {
    has(t, `${b.name} — ${b.title}`);
    has(t, `frames ${b.from}–${b.to}`);
  }
  eq(byClass(el, 'demo-beat').length, 5);
});
T('the "unknown item -> amber, excluded" beat is on the list in those words', () => {
  const t = text(D.renderScript(D.initialModel(), DOC));
  has(t, 'AMBER line');
  has(t, 'cannot identify');
  has(t, 'refusing to guess');
});
T('the script taps are printed on the beats they fall in', () => {
  const el = D.renderScript(D.initialModel(), DOC);
  const t = text(el);
  has(t, `frame ${D.ENROL_AT} · enrol_sticker`);
  has(t, `frame ${D.DONE_AT} · done`);
  has(t, `frame ${D.PAY_AT}`);
  has(t, 'only event in the entire script that can move the session to PAID');
});

// ======================================================================== 6 ==
G('6. liveness is MEASURED from arrivals, never asserted from a button');

T('running is unknown until a state message has been seen', () => {
  eq(D.livenessOf(D.initialModel()).running, null);
  eq(D.livenessOf(D.noteTick(D.initialModel(), 1000)).running, null);
});
T('state messages arriving -> running true, with a measured rate', () => {
  let m = D.initialModel();
  for (let i = 0; i < 10; i++) m = D.reduceDemo(m, st({ frame_index: i }), 1000 + i * 100);
  const l = D.livenessOf(m);
  eq(l.running, true);
  ok(l.fps > 9 && l.fps < 11, `measured ${l.fps} fps, expected ~10`);
  measured.measured_fps_from_100ms_arrivals = l.fps.toFixed(2);
});
T('the window expires: frames stopping makes running unknown again, not false-confident', () => {
  let m = D.initialModel();
  for (let i = 0; i < 10; i++) m = D.reduceDemo(m, st({ frame_index: i }), 1000 + i * 100);
  m = D.noteTick(m, 1000 + 10 * 100 + D.RUNNING_WINDOW_MS + 1);
  eq(D.livenessOf(m).running, null);
  has(D.livenessOf(m).reason, 'no state message in the last');
});
T('PRESSING PAUSE WHILE FRAMES KEEP ARRIVING STILL REPORTS FRAMES ARRIVING', () => {
  // What was asked for and what is happening are two different facts.
  const doc = makeDoc([]);
  let t = 1000;
  const p = D.createPanel({ doc, now: () => t });
  const sock = { readyState: 1, sent: [], send(s) { sock.sent.push(s); }, addEventListener() {} };
  p.useSocket(sock);
  p.press('stop');
  eq(p.model.control.code, D.CTRL_SENT);
  for (let i = 0; i < 5; i++) { t += 100; p.onState(st({ frame_index: i })); }
  eq(D.livenessOf(p.model).running, true, 'the panel believed the button over the frames');
  const el = D.renderControls(p.model, DOC);
  has(text(el), 'MEASURED from arrivals, not from which button you pressed');
});

// ======================================================================== 7 ==
G('7. the controls: sent, refused, unanswered, unsendable — all four visible');

T('simMessage builds the documented wire message and refuses a typo', () => {
  deepEq(D.simMessage('start'), { type: 'sim', action: 'start' });
  deepEq(D.simMessage('reset'), { type: 'sim', action: 'reset' });
  deepEq(D.CONTROL_ACTIONS.slice(), ['start', 'stop', 'step', 'reset']);
  throws(() => D.simMessage('go'), 'unknown action');
  throws(() => D.simMessage(null), 'null action');
});
T('all four controls are rendered, RUN DEMO first and marked primary', () => {
  const el = D.renderControls(D.initialModel(), DOC);
  const btns = tagsOf(el, 'button');
  eq(btns.length, 4);
  eq(btns[0].textContent, 'RUN DEMO');
  eq(btns[0].dataset.primary, '1');
  deepEq(btns.map((b) => b.dataset.action), ['start', 'stop', 'step', 'reset']);
  deepEq(btns.map((b) => b.textContent), ['RUN DEMO', 'PAUSE', 'STEP', 'RESET']);
});
T('a click on a rendered button calls back with its action', () => {
  const got = [];
  const el = D.renderControls(D.initialModel(), DOC, { onAction: (a) => got.push(a) });
  for (const b of tagsOf(el, 'button')) b.fire('click', {});
  deepEq(got, ['start', 'stop', 'step', 'reset']);
});
T('with no socket, a press is NO_TRANSPORT and says why', () => {
  const p = D.createPanel({ doc: makeDoc([]), now: () => 1 });
  eq(p.press('start'), false);
  eq(p.model.control.code, D.CTRL_NO_TRANSPORT);
  has(p.model.control.detail, 'no brain socket');
  has(text(D.renderAbstentions(p.model, DOC)), 'demo_no_transport');
});
T('with a closed socket, a press names the readyState instead of pretending', () => {
  const p = D.createPanel({ doc: makeDoc([]), now: () => 1 });
  p.useSocket({ readyState: 3, send() { throw new Error('closed'); }, addEventListener() {} });
  eq(p.press('start'), false);
  eq(p.model.control.code, D.CTRL_NO_TRANSPORT);
  has(p.model.control.detail, 'readyState 3');
});
T('with an open socket the exact JSON goes on the wire', () => {
  const sent = [];
  const p = D.createPanel({ doc: makeDoc([]), now: () => 1 });
  p.useSocket({ readyState: 1, send: (s) => sent.push(s), addEventListener() {} });
  eq(p.press('reset'), true);
  deepEq(sent.map(JSON.parse), [{ type: 'sim', action: 'reset' }]);
  eq(p.model.control.code, D.CTRL_SENT);
});
T('THE BRAIN REFUSING `sim` IS RENDERED VERBATIM, WITH THE VERBS IT WOULD ACCEPT', () => {
  // This is today's real answer from gawaah/brain_server.py: `sim` is not in
  // CLIENT_VERBS. The panel must show the refusal, not swallow it.
  const p = D.createPanel({ doc: makeDoc([]), now: () => 1 });
  p.useSocket({ readyState: 1, send() {}, addEventListener() {} });
  p.press('start');
  p.onState({
    type: 'refused', reason: 'UNKNOWN_TYPE', detail: "no handler for 'sim'",
    known: ['frame', 'done', 'revert', 'ack', 'enrol_sticker', 'select_panel', 'refresh'],
    frame_index: 9536,
  });
  eq(p.model.control.code, D.CTRL_REFUSED);
  const t = text(D.renderControls(p.model, DOC));
  has(t, 'UNKNOWN_TYPE');
  has(t, "no handler for 'sim'");
  has(t, 'enrol_sticker');
  const a = text(D.renderAbstentions(p.model, DOC));
  has(a, 'demo_controls_refused');
  has(a, 'will not pretend otherwise');
});
T('a brain that ACCEPTS the control is reported as accepted', () => {
  const p = D.createPanel({ doc: makeDoc([]), now: () => 1 });
  p.useSocket({ readyState: 1, send() {}, addEventListener() {} });
  p.press('step');
  // The real status message shape: `index` is the NEXT frame the driver will
  // emit, `sim_frame` is the frame the beat block describes. The panel reads
  // sim_frame, because that is the frame the numbers on screen came from.
  p.onState({
    type: 'sim', ok: true, enabled: true, simulated: true, mode: 'PAUSED',
    actions: ['start', 'stop', 'pause', 'step', 'reset', 'status'],
    index: 13, of: D.TOTAL_FRAMES, run: 1, pending_steps: 0, fault: '',
    beat: 'goods', beat_label: 'a packet is placed', beat_index: 4, beat_of: 30,
    sim_frame: 12, sim_run: 1,
  });
  eq(p.model.control.code, D.CTRL_ACCEPTED);
  eq(D.beatOf(p.model).frame, 12);
  eq(D.beatOf(p.model).name, 'goods');
  eq(D.modeOf(p.model).mode, 'PAUSED');
});
T('a control the brain never answers goes UNANSWERED, not done', () => {
  let m = D.noteControlSent(D.initialModel(), 'start', 1000);
  m = D.noteTick(m, 1000 + D.CONTROL_ANSWER_MS - 1);
  eq(m.control.code, D.CTRL_SENT);
  m = D.noteTick(m, 1000 + D.CONTROL_ANSWER_MS);
  eq(m.control.code, D.CTRL_UNANSWERED);
  has(m.control.detail, 'will not say that it was');
  has(text(D.renderAbstentions(m, DOC)), 'demo_control_unanswered');
});
T('a refusal that arrives with NO control outstanding does not invent one', () => {
  const m = D.reduceDemo(D.initialModel(), { type: 'refused', reason: 'RECT_MISSING' }, 1);
  eq(m.control.code, D.CTRL_IDLE);
  ok(m.lastRefusal !== null, 'the refusal was dropped entirely');
});

// ======================================================================== 8 ==
G('8. the standing text: green, the sim gateway, and what is real');

T('the green rule is on screen in every state, cold and hot', () => {
  for (const m of [
    D.initialModel(),
    D.noteHealth(D.initialModel(), { sim: true }, 1),
    D.reduceDemo(D.initialModel(), st({ session_state: 'PAID', settled_payment_id: 'pay_x' }), 1),
  ]) {
    const t = text(D.renderDemo(m, DOC));
    has(t, 'GREEN NEVER COMES FROM THIS PANEL');
    has(t, 'RAW BYTES before any JSON parse');
    has(t, 'notes.session_id matches an OPEN intent');
    has(t, 'equals that intent to the paisa');
    has(t, 'Pressing RUN DEMO cannot produce one');
  }
});
T('the simulated-gateway caveat is on screen and does not overclaim', () => {
  const t = text(D.renderDemo(D.noteHealth(D.initialModel(), { sim: true }, 1), DOC));
  has(t, 'SIGNED BY THE SIM GATEWAY');
  has(t, 'it does not prove anyone paid anybody');
});
T('the real-money note is present and is stated as one past event, not a capability', () => {
  const t = text(D.renderDemo(D.initialModel(), DOC));
  has(t, 'test-mode Payment Link was paid');
  has(t, 'stayed amber and out of the total');
});
T('the sim gateway secret is nowhere in this module', () => {
  const src = readFileSync(DEMO_PATH, 'utf8');
  hasNot(src, 'whsec_');
  hasNot(text(D.renderDemo(D.initialModel(), DOC)), 'whsec_');
});
T('what is simulated and what is real are both listed, and the camera is on the simulated side', () => {
  const el = D.renderTruth(D.initialModel(), DOC);
  const sim = byData(el, 'col', 'simulated')[0];
  const real = byData(el, 'col', 'real')[0];
  ok(sim && real, 'the two columns are not both present');
  has(text(sim), 'THE CAMERA');
  has(text(sim), 'THE GATEWAY');
  has(text(real), 'MUDRA, PEEL, CHILLA and SAAF');
  has(text(real), 'four-part green predicate');
});
T('a PAID session is explained by the webhook, never by the demo', () => {
  const m = D.reduceDemo(D.initialModel(), st({
    session_state: 'PAID', total_paise: 2850, settled_payment_id: 'pay_QxAbC',
  }), 1);
  const c = D.commentaryFor(m);
  eq(c.code, D.C_PAID);
  has(c.text, 'pay_QxAbC');
  has(c.text, 'HMAC verified over the raw bytes');
  has(c.text, 'would still be amber');
});

// ======================================================================== 9 ==
G('9. INVARIANT 2 on the pixels: no green and no fraud-red anywhere');

T('no rendered state puts green or red in a class, data attribute or style', () => {
  const worlds = [
    ['cold', D.initialModel()],
    ['simulated', D.noteHealth(D.initialModel(), { sim: true }, 1)],
    ['live', D.noteHealth(D.initialModel(), { sim: false }, 1)],
    ['paid', D.reduceDemo(D.initialModel(), st({ session_state: 'PAID', settled_payment_id: 'p' }), 1)],
    ['amber', D.reduceDemo(D.initialModel(), st({
      session_state: 'AMBER', amber_count: 2,
      amber_items: [{ item_id: 'a' }, { item_id: 'b' }],
    }), 1)],
    ['tampered', D.reduceDemo(D.initialModel(), {
      type: 'peel', verdict: 'TAMPERED', ignited_fraction: 0.0629, ok: true,
    }, 1)],
    ['refused', D.reduceDemo(D.noteControlSent(D.initialModel(), 'start', 1), {
      type: 'refused', reason: 'UNKNOWN_TYPE', detail: "no handler for 'sim'", known: ['frame'],
    }, 2)],
  ];
  for (const [name, m] of worlds) {
    assertNoGreenNoRed(D.renderDemo(m, DOC), `world ${name}`);
  }
  measured.worlds_scanned_for_green = worlds.length;
});
T('the shipped stylesheet contains no green and no fraud-red', () => {
  for (const re of FORBIDDEN) {
    if (re.test(D.CSS_TEXT)) throw new Error(`CSS_TEXT matches ${re}`);
  }
  // and it must not reach for the palette's green either
  hasNot(D.CSS_TEXT, '--green');
  hasNot(D.CSS_TEXT, '--red');
  measured.css_bytes = D.CSS_TEXT.length;
});
T('there is no data-status the panel can set other than OFF / ABSTAIN / OK', () => {
  const doc = makeDoc(['panel-demo', 'body-demo']);
  const p = D.createPanel({ doc, now: () => 1 });
  p.render();
  ok(['OFF', 'ABSTAIN', 'OK'].includes(doc.byId['panel-demo'].dataset.status),
    `status was ${doc.byId['panel-demo'].dataset.status}`);
  eq(doc.byId['panel-demo'].dataset.status, 'ABSTAIN');
  p.onState(st({ total_paise: 2850, ledger_lines: 4 }));
  p.render();
  eq(doc.byId['panel-demo'].dataset.status, 'OK');
});

// ======================================================================= 10 ==
G('10. every I-DO-NOT-KNOW state is reachable and is shown when it applies');

const ABSTENTIONS = [
  'demo_provenance_unknown',
  'demo_no_script_position',
  'demo_beat_is_inferred',
  'demo_beat_disagreement',
  'demo_amber_count_disagreement',
  'demo_liveness_unknown',
  'demo_sim_not_enabled',
  'demo_sim_faulted',
  'demo_controls_refused',
  'demo_control_unanswered',
  'demo_no_transport',
  'demo_no_total',
  'demo_no_ledger',
];

T('the reachability list above covers EVERY abstention code in the module', () => {
  // Otherwise a new abstention could be added, never be reachable, and nothing
  // would notice — which is the failure mode invariant 7 is about.
  // Whole file, not just abstentionsFor: `demo_beat_disagreement` is minted by
  // disagreementOf and forwarded, so a scan of one function would have missed
  // it — and did, the first time this test ran.
  const src = readFileSync(DEMO_PATH, 'utf8');
  const codes = [...new Set([...src.matchAll(/code: '(demo_[a-z_]+)'/g)].map((m) => m[1]))];
  ok(codes.length > 0, 'no abstention codes were found in abstentionsFor');
  for (const c of codes) ok(ABSTENTIONS.includes(c), `${c} is not in the reachability list`);
  for (const c of ABSTENTIONS) ok(codes.includes(c), `${c} is no longer emitted`);
  measured.abstention_codes_in_module = codes.length;
});

T('the cold panel lists its abstentions and says the words "I DO NOT KNOW"', () => {
  const el = D.renderAbstentions(D.initialModel(), DOC);
  const list = D.abstentionsFor(D.initialModel());
  ok(list.length >= 4, `cold panel listed only ${list.length} abstentions`);
  has(text(el), 'I DO NOT KNOW');
  measured.cold_abstentions = list.length;
});
T('EACH named abstention is reachable by constructing the world it belongs to', () => {
  const reach = {
    demo_provenance_unknown: D.initialModel(),
    demo_no_script_position: D.initialModel(),
    demo_liveness_unknown: D.initialModel(),
    demo_no_total: D.initialModel(),
    demo_no_ledger: D.initialModel(),
    demo_beat_is_inferred: D.reduceDemo(D.initialModel(), st({ frame_index: 3 }), 1),
    demo_controls_refused: D.reduceDemo(
      D.noteControlSent(D.initialModel(), 'start', 1),
      { type: 'refused', reason: 'UNKNOWN_TYPE', detail: "no handler for 'sim'", known: [] }, 2,
    ),
    demo_control_unanswered: D.noteTick(D.noteControlSent(D.initialModel(), 'step', 1), 1 + D.CONTROL_ANSWER_MS),
    demo_no_transport: D.noteControlUnsendable(D.initialModel(), 'stop', 'no socket', 1),
    demo_beat_disagreement: D.reduceDemo(D.initialModel(), st({
      simulated: true, sim_frame: 3, beat: 'goods', beat_index: 0, beat_of: 30,
    }), 1),
    demo_amber_count_disagreement: D.reduceDemo(D.initialModel(), st({
      amber_count: 0, amber_items: [],
      lines: [{ item_id: 't2', amber: true, price_paise: null, reason: 'no_candidate_in_footprint' }],
    }), 1),
    demo_sim_not_enabled: D.reduceDemo(D.initialModel(), {
      type: 'sim', ok: false, enabled: false, mode: 'STOPPED', reason: 'SIM_NOT_ENABLED',
    }, 1),
    demo_sim_faulted: D.reduceDemo(D.initialModel(), {
      type: 'sim', ok: false, enabled: true, simulated: true, mode: 'FAULTED',
      fault: 'source.frame(31) raised ValueError',
    }, 1),
  };
  for (const code of ABSTENTIONS) {
    const m = reach[code];
    ok(m, `no world was written for ${code}`);
    const codes = D.abstentionsFor(m).map((a) => a.code);
    ok(codes.includes(code), `${code} not reachable; got ${codes.join(',')}`);
    has(text(D.renderAbstentions(m, DOC)), code);
  }
  measured.abstentions_proved_reachable = ABSTENTIONS.length;
});
T('a fully-informed panel drops the abstentions it has answered — and only those', () => {
  let m = D.noteHealth(D.initialModel(), { sim: true }, 1000);
  for (let i = 0; i < 5; i++) {
    m = D.reduceDemo(m, st({
      total_paise: 2850, ledger_lines: 9, frame_index: 31,
      simulated: true, sim_run: 1, sim_frame: 31, beat: 'goods',
      beat_label: 'a packet is placed, then crosses the sell line',
      beat_index: 23, beat_of: 30,
    }), 1000 + i * 100);
  }
  const codes = D.abstentionsFor(m).map((a) => a.code);
  deepEq(codes, [], `still abstaining on ${codes.join(',')}`);
  has(text(D.renderAbstentions(m, DOC)), 'I DO NOT KNOW');
  has(text(D.renderAbstentions(m, DOC)), 'every capability panel keeps its own abstentions');
});
T('a fully-informed panel STILL carries the standing honesty text', () => {
  let m = D.noteHealth(D.initialModel(), { sim: true }, 1000);
  m = D.reduceDemo(m, st({ total_paise: 2850, ledger_lines: 9, frame_index: 31 }), 1000);
  const t = text(D.renderDemo(m, DOC));
  has(t, 'GREEN NEVER COMES FROM THIS PANEL');
  has(t, 'SIGNED BY THE SIM GATEWAY');
  has(t, 'SIMULATED  ·  SIMULATED  ·  SIMULATED');
});

// ======================================================================= 11 ==
G('11. the socket tap: it taps, it never opens a second connection');

T('installSocketTap hands over every constructed socket and preserves instanceof', () => {
  class FakeWS {
    constructor(url) { this.url = url; this.readyState = 0; }
    send() {}
    addEventListener() {}
  }
  FakeWS.OPEN = 1;
  const g = { WebSocket: FakeWS };
  const seen = [];
  const r = D.installSocketTap(g, (s, url) => seen.push(url));
  eq(r.ok, true);
  const s = new g.WebSocket('ws://x/ws');
  eq(seen.length, 1);
  eq(seen[0], 'ws://x/ws');
  ok(s instanceof FakeWS, 'the proxy broke instanceof');
  eq(g.WebSocket.OPEN, 1, 'the proxy hid a static');
  eq(s.url, 'ws://x/ws');
});
T('the tap installs once and refuses to double-install', () => {
  class FakeWS { send() {} addEventListener() {} }
  const g = { WebSocket: FakeWS };
  eq(D.installSocketTap(g, () => {}).ok, true);
  const second = D.installSocketTap(g, () => {});
  eq(second.ok, false);
  eq(second.reason, 'already_tapped');
});
T('a tap callback that throws does not break the socket being constructed', () => {
  class FakeWS { constructor(u) { this.url = u; } }
  const g = { WebSocket: FakeWS };
  D.installSocketTap(g, () => { throw new Error('panel is broken'); });
  const s = new g.WebSocket('ws://y');
  eq(s.url, 'ws://y');
});
T('no WebSocket at all is a named refusal, not a throw', () => {
  eq(D.installSocketTap({}, () => {}).reason, 'no_websocket_constructor');
  eq(D.installSocketTap(null, () => {}).reason, 'no_websocket_constructor');
});
T('THE TRANSPORT IS READ OFF readyState, NOT REMEMBERED FROM EVENTS', () => {
  // Found in a real browser: the panel showed TRANSPORT: TAPPED while the
  // socket it had tapped was readyState 3, so the RUN DEMO refusal contradicted
  // the transport line directly above it. A `close` that never fires is exactly
  // what an event-only view cannot see.
  let t = 1000;
  const doc = makeDoc(['body-demo']);
  const p = D.createPanel({ doc, now: () => t });
  const sock = { readyState: 1, send() {}, addEventListener() {} };
  p.useSocket(sock);
  eq(p.model.transport.code, 'TAPPED');
  sock.readyState = 3;               // died with no event of any kind
  t += 300;
  p.render();                        // the next repaint notices
  eq(p.model.transport.code, 'SOCKET_CLOSED');
  has(p.model.transport.detail, 'they are not live');
  eq(p.press('start'), false);
  has(p.model.control.detail, 'readyState 3');
  has(p.model.control.detail, 'SOCKET_CLOSED');
});
T('every readyState has a transport code and an explanation', () => {
  for (const rs of [0, 1, 2, 3]) {
    const code = D.SOCKET_STATE[rs];
    ok(code, `no transport code for readyState ${rs}`);
    ok(D.SOCKET_DETAIL[code], `no explanation for ${code}`);
  }
});
T('a DEAD socket never displaces a LIVE one', () => {
  // app.js retries a failed connection, so the tap legitimately sees several
  // sockets. Taking the newest unconditionally hands the panel a corpse.
  const doc = makeDoc(['body-demo']);
  const p = D.createPanel({ doc, now: () => 1 });
  const live = { readyState: 1, sent: [], send(s) { live.sent.push(s); }, addEventListener() {} };
  const corpse = { readyState: 3, send() { throw new Error('dead'); }, addEventListener() {} };
  p.useSocket(live);
  p.useSocket(corpse);
  eq(p.socketState(), 1, 'the panel adopted a closed socket over an open one');
  eq(p.press('start'), true);
  deepEq(live.sent.map(JSON.parse), [{ type: 'sim', action: 'start' }]);
});
T('a message from a socket the panel is no longer on is ignored', () => {
  const doc = makeDoc(['body-demo']);
  const p = D.createPanel({ doc, now: () => 1 });
  const h1 = {}, h2 = {};
  const s1 = { readyState: 1, send() {}, addEventListener(t, f) { h1[t] = f; } };
  const s2 = { readyState: 1, send() {}, addEventListener(t, f) { h2[t] = f; } };
  p.useSocket(s1);
  p.useSocket(s2);              // both live; the newer one wins
  h2.message({ data: JSON.stringify(st({ total_paise: 100 })) });
  eq(p.model.totalPaise, 100);
  h1.message({ data: JSON.stringify(st({ total_paise: 999 })) });
  eq(p.model.totalPaise, 100, 'a stale socket wrote into the model');
});
T('THERE IS ONE PANEL PER DOCUMENT, whichever door it is entered by', () => {
  // Found in a real browser: app.js's drainPanelQueue() calls DESCRIPTOR.attach()
  // AND this module's boot() stands a panel up. Two instances rendered into the
  // same #body-demo and whichever painted last won, so a cold unregistered panel
  // could wipe out the live one mid-run.
  D.resetSingleton();
  const a = D.panelSingleton({});
  const b = D.panelSingleton({});
  eq(a, b, 'panelSingleton handed out two panels');
  const c = D.attach(() => ({ ok: false, reason: 'panel_unknown:demo' }), {});
  eq(c, a, 'attach() built a second panel beside the singleton');
  // an explicit doc or root is an explicit request for a separate instance
  const own = D.panelSingleton({ doc: makeDoc([]) });
  ok(own !== a, 'an explicit doc did not get its own panel');
  D.resetSingleton();
});
T('adopting a socket subscribes to its messages and reports the transport', () => {
  const doc = makeDoc([]);
  const p = D.createPanel({ doc, now: () => 1 });
  const handlers = {};
  const sock = {
    readyState: 1, send() {},
    addEventListener(t, f) { handlers[t] = f; },
  };
  p.useSocket(sock);
  eq(p.model.transport.code, 'TAPPED');
  has(p.model.transport.detail, 'No second connection was opened');
  has(p.model.transport.detail, 'one frame pump per socket');
  handlers.message({ data: JSON.stringify(st({ total_paise: 4242, ledger_lines: 3 })) });
  eq(p.model.totalPaise, 4242);
  handlers.message({ data: 'not json at all' });
  eq(p.model.totalPaise, 4242, 'malformed JSON corrupted the model');
  sock.readyState = 3;
  handlers.close();
  eq(p.model.transport.code, 'SOCKET_CLOSED');
  // and a close event on a socket that still reports OPEN is NOT believed:
  // readyState is the truth, the event is only a prompt to re-read it.
  sock.readyState = 1;
  handlers.close();
  eq(p.model.transport.code, 'TAPPED');
});

// ======================================================================= 12 ==
G('12. the panel object and the shell seam');

T('the module exposes the panel-module shape every other panel exposes', () => {
  eq(D.PANEL_ID, 'demo');
  eq(typeof D.createPanel, 'function');
  eq(typeof D.attach, 'function');
  eq(typeof D.renderDemo, 'function');
  const p = D.createPanel({ doc: makeDoc([]) });
  eq(p.id, D.PANEL_ID);
  eq(typeof p.onState, 'function');
  eq(typeof p.onFrame, 'function');
  eq(p.onFrame(), false, 'DEMO claimed it draws on the live view');
});
T('attach() records a REFUSED registration instead of swallowing it', () => {
  // app.js refuses ids outside PANEL_IDS, which today excludes 'demo'.
  const p = D.attach(() => ({ ok: false, reason: 'panel_unknown:demo' }), { doc: makeDoc([]) });
  eq(p.model.seam.registered, false);
  eq(p.model.seam.reason, 'panel_unknown:demo');
  has(text(D.renderDemo(p.model, DOC)), 'not registered (panel_unknown:demo)');
});
T('attach() records a successful registration', () => {
  const got = [];
  const p = D.attach((id, hooks) => { got.push([id, typeof hooks.onState]); return { ok: true }; },
    { doc: makeDoc([]) });
  deepEq(got, [['demo', 'function']]);
  eq(p.model.seam.registered, true);
  has(text(D.renderDemo(p.model, DOC)), 'registered with the shell');
});
T('attach() refuses a non-function register', () => {
  throws(() => D.attach(null), 'attach(null)');
  throws(() => D.attach({}), 'attach({})');
});
T('attachDemoPanel() follows the other panels\' convention', () => {
  const r = D.attachDemoPanel({ doc: makeDoc([]), register: () => ({ ok: true }) });
  eq(r.registered, true);
  eq(r.panel.id, 'demo');
});
T('the module pushed a descriptor onto GAWAAH_PANELS with an attach()', () => {
  const d = (globalThis.GAWAAH_PANELS || []).find((x) => x && x.id === 'demo');
  ok(d, 'no demo descriptor was queued');
  eq(typeof d.attach, 'function');
  eq(d.title, D.PANEL_TITLE);
});
T('the panel mounts into #body-demo, and falls back to #panel-demo', () => {
  const a = makeDoc(['body-demo', 'panel-demo']);
  D.createPanel({ doc: a, now: () => 1 }).render();
  ok(a.byId['body-demo'].children.length > 0, 'nothing was painted into #body-demo');
  const b = makeDoc(['panel-demo']);
  D.createPanel({ doc: b, now: () => 1 }).render();
  ok(b.byId['panel-demo'].children.length > 0, 'nothing was painted into #panel-demo');
});
T('with no host at all, render() returns false rather than throwing', () => {
  eq(D.createPanel({ doc: makeDoc([]), now: () => 1 }).render(), false);
});
T('the shell abstention block is driven when the markup provides one', () => {
  const doc = makeDoc(['body-demo', 'panel-demo', 'abstain-demo', 'why-demo']);
  const p = D.createPanel({ doc, now: () => 1 });
  p.render();
  eq(doc.byId['abstain-demo'].getAttribute('hidden'), null, 'a cold panel hid its abstention block');
  ok(D.abstentionsFor(p.model).map((a) => a.code).includes(doc.byId['why-demo'].textContent));
});
T('junk instead of a state never throws out of onState', () => {
  const p = D.createPanel({ doc: makeDoc(['body-demo']), now: () => 1 });
  for (const junk of [null, undefined, 0, '', 'nope', [], {}, { type: null }]) p.onState(junk);
  eq(p.render(), true);
});
T('every renderer survives a junk model', () => {
  for (const junk of [null, undefined, 0, '', 'nope', [], { totalPaise: 'x' }]) {
    for (const fn of [D.renderDemo, D.renderBadge, D.renderHeadline, D.renderCommentary,
      D.renderControls, D.renderScript, D.renderTruth, D.renderAbstentions]) {
      const el = fn(junk, DOC);
      ok(nodes(el).length > 1, `${fn.name} rendered almost nothing for ${JSON.stringify(junk)}`);
      assertNoGreenNoRed(el, `junk model ${JSON.stringify(junk)}`);
    }
  }
});
T('installStyles is honest when there is no document', () => {
  const r = D.installStyles(null, {});
  eq(r.ok, false);
  eq(r.how, 'no_document');
  eq(r.verified, false);
});
T('installStyles falls back to a <style> element and reports it UNVERIFIED', () => {
  // No getComputedStyle in the shim, so it cannot prove the CSS applied — and
  // must say `verified:false` rather than assume it did.
  const doc = makeDoc([]);
  const r = D.installStyles(doc, {});
  eq(r.ok, true);
  has(r.how, 'style_element');
  eq(r.verified, false);
  eq(doc.head.children.length, 1);
  eq(doc.head.children[0].textContent, D.CSS_TEXT);
});
T('the module imports nothing (it must render with no shell present)', () => {
  const src = readFileSync(DEMO_PATH, 'utf8');
  const imports = [...src.matchAll(/^\s*import\s.*$/gm)].map((m) => m[0].trim());
  deepEq(imports, []);
  measured.panel_external_imports = imports.length;
});

// ======================================================================= 13 ==
G('13. driven by a REAL run of the brain sim, message for message');

let RUN = null, runWhy = '';
if (pyAvailable) {
  try {
    // NO TestClient and NO websocket here, deliberately. Opening one starts
    // SimDriver's own pump in a background task, which pulls frames off the
    // SAME script this loop is pulling from — the two interleave, the frame
    // indices shift, and every assertion below then tests a run no browser
    // would ever see. (That is exactly what the first version of this test did,
    // and it is why frame 38 came back BASKET_OPEN.)
    //
    // `driver.emit_once()` is the ONE place a synthetic frame enters the brain,
    // and it is what the pump calls. Driving the SimScript by hand instead —
    // the second thing this test got wrong — bypasses SimDriver.set_sim_tag, so
    // every message came out stamped sim_frame:0 and the beat never moved.
    RUN = JSON.parse(python(`
import json, tempfile
from pathlib import Path
from gawaah.brain_server import build_sim_server

server = build_sim_server(Path(tempfile.mkdtemp(prefix="gawaah-demotest-")),
                          autostart=False)
driver = server.sim_driver
out = list(server.hello())
out.extend(server.handle({"type": "sim", "action": "start"}))
for _ in range(server.sim.total_frames):
    out.extend(driver.emit_once())
out.extend(server.handle({"type": "sim", "action": "status"}))
print(json.dumps({"messages": out, "health": server.health()}))
`));
  } catch (e) {
    runWhy = (e && e.message) || String(e);
  }
}

T('a real sim run was captured', () => {
  ok(RUN !== null, `could not drive the brain sim: ${runWhy}`);
  ok(RUN.messages.length > 300, `only ${RUN && RUN.messages.length} messages captured`);
  measured.real_sim_messages_replayed = RUN.messages.length;
  measured.real_sim_health_sim_flag = String(RUN.health.sim);
});

if (RUN) {
  /**
   * Fold the real run up to the END of one script frame.
   *
   * Stopping at the FIRST message tagged with that frame is wrong and was the
   * third mistake in this test: SimDriver emits the frame's own state, then its
   * four panel messages, then the replies to any script tap that fires on that
   * frame. `done` fires on 37 and the pay on 38, so stopping at the first state
   * of frame 38 stops BEFORE the webhook that settles the session, and stopping
   * at the first state of 53 stops before the MUDRA reading taken on 53. This
   * stops when the frame counter has moved on, which is "the board a viewer
   * sees while frame N is up".
   */
  function upTo(frame) {
    let m = D.noteHealth(D.initialModel(), RUN.health, 0);
    let t = 0;
    for (const msg of RUN.messages) {
      if (Number.isInteger(msg.sim_frame) && msg.sim_frame > frame) break;
      t += 10;
      m = D.reduceDemo(m, msg, t);
    }
    return m;
  }

  T('the real /health drives the badge to SIMULATED', () => {
    const m = D.noteHealth(D.initialModel(), RUN.health, 1);
    eq(RUN.health.sim, true);
    eq(D.provenanceOf(m).code, D.PROV_SIMULATED);
  });
  T('frame 31: the packet crossed and the total is 2850 INTEGER paise', () => {
    const m = upTo(31);
    eq(m.totalPaise, D.PACKET_PRICE_PAISE);
    ok(Number.isInteger(m.totalPaise), 'the total is not an integer');
    eq(D.beatOf(m).name, 'goods');
    eq(D.paiseLabel(m.totalPaise), '₹28.50');
    eq(D.commentaryFor(m).code, D.C_COUNTING);
    measured.real_total_at_frame_31 = `${m.totalPaise} paise`;
  });
  T('frame 37: the basket closes and the panel says it is WAITING, not paid', () => {
    const m = upTo(37);
    eq(m.sessionState, 'AWAITING_SETTLEMENT');
    const c = D.commentaryFor(m);
    eq(c.code, D.C_AWAITING);
    has(c.text, 'Nothing is green');
    hasNot(c.text, 'SETTLED.');
  });
  T('frame 38: PAID, and the commentary attributes it to the four-part predicate', () => {
    const m = upTo(38);
    eq(m.sessionState, 'PAID');
    ok(m.settledPaymentId, 'PAID with no settled payment id');
    const c = D.commentaryFor(m);
    eq(c.code, D.C_PAID);
    has(c.text, m.settledPaymentId);
    has(c.text, 'HMAC verified over the raw bytes');
    eq(D.beatOf(m).name, 'screen');
    measured.real_paid_at_frame = 38;
  });
  T('frame 39: CHILLA MATCHED the amount and the panel still refuses to call it authorisation', () => {
    const m = upTo(39);
    eq(m.chilla && m.chilla.verdict, 'MATCHED');
    const notes = D.notesFor(m).map((n) => n.code);
    ok(notes.includes('chilla_matched'), `notes were ${notes.join(',')}`);
    has(text(D.renderCommentary(m, DOC)), 'corroboration is not authorisation');
  });
  T('frame 42: the phone is an UNKNOWN ITEM -> AMBER, EXCLUDED, total back to 0', () => {
    const m = upTo(42);
    eq(m.sessionState, 'AMBER');
    eq(m.totalPaise, 0);
    ok(m.amberCount >= 1, `amber count was ${m.amberCount}`);
    has(m.amberReasons.join(','), 'no_candidate_in_footprint');
    // and the disagreement with the brain's own amber_count field is DECLARED,
    // not smoothed over: on this run the field reads 0 while a line is amber.
    if (m.brainAmberCount !== m.amberCount) {
      ok(D.abstentionsFor(m).map((a) => a.code).includes('demo_amber_count_disagreement'),
        'the amber-count disagreement was not declared');
      measured.real_amber_count_field_vs_lines = `${m.brainAmberCount} vs ${m.amberCount}`;
    }
    const c = D.commentaryFor(m);
    eq(c.code, D.C_AMBER);
    has(c.text, 'EXCLUDED from the total');
    has(c.text, 'rather show you a hole than invent a price');
    const el = D.renderHeadline(m, DOC);
    eq(byData(el, 'head', 'AMBER')[0].dataset.amber, '1');
    measured.real_amber_at_frame_42 = m.amberCount;
  });
  T('frame 53: MUDRA reads OPEN off geometry and the note says so', () => {
    const m = upTo(53);
    eq(m.mudra && m.mudra.state, 'OPEN');
    const notes = D.notesFor(m);
    const g = notes.find((n) => n.code === 'mudra_gesture');
    ok(g, `no mudra note; got ${notes.map((n) => n.code).join(',')}`);
    has(g.text, 'no model, no weights');
    eq(D.beatOf(m).name, 'hand');
  });
  T('frame 62: PEEL turns TAMPERED and the note says PEEL cannot move money', () => {
    const m = upTo(62);
    eq(m.peel && m.peel.verdict, 'TAMPERED');
    const n = D.notesFor(m).find((x) => x.code === 'peel_tampered');
    ok(n, 'no peel note on a tampered sticker');
    has(n.text, 'cannot move a paisa');
    eq(D.beatOf(m).name, 'tamper');
    measured.real_peel_ignited_at_62 = m.peel.ignited_fraction.toFixed(6);
  });
  T('the whole run, folded, highlights the beat the brain was really in', () => {
    // Walk every state message and check the highlighted beat against the
    // script's own phase for that frame.
    const ranges = D.phaseRanges();
    let m = D.noteHealth(D.initialModel(), RUN.health, 0);
    let t = 0, checked = 0;
    for (const msg of RUN.messages) {
      t += 10;
      m = D.reduceDemo(m, msg, t);
      if (msg.type !== 'state' || !Number.isInteger(msg.sim_frame)) continue;
      // sim_frame can run past the end of the script once the driver holds.
      if (msg.sim_frame < 0 || msg.sim_frame >= D.TOTAL_FRAMES) continue;
      const want = ranges.find((r) => msg.sim_frame >= r.from && msg.sim_frame <= r.to);
      eq(D.beatOf(m).name, want.name, `frame ${msg.sim_frame}`);
      checked++;
    }
    ok(checked >= D.TOTAL_FRAMES - 2, `only ${checked} frames checked`);
    measured.real_frames_beat_checked = checked;
  });
  T('the REAL brain answers a real `sim` control, and the panel reports ACCEPTED', () => {
    const sims = RUN.messages.filter((m) => m.type === 'sim');
    ok(sims.length >= 2, `only ${sims.length} sim status messages in the run`);
    const first = sims[0];
    eq(first.enabled, true);
    eq(first.simulated, true);
    deepEq(first.modes, D.SIM_MODES.slice());
    for (const a of D.CONTROL_ACTIONS) ok(first.actions.includes(a), `brain lacks '${a}'`);
    // fold a real START reply onto a panel that just pressed START
    const startReply = RUN.messages.find((m) => m.type === 'sim' && m.mode === 'RUNNING');
    ok(startReply, 'the run never reported RUNNING');
    let m = D.noteControlSent(D.initialModel(), 'start', 1);
    m = D.reduceDemo(m, startReply, 2);
    eq(m.control.code, D.CTRL_ACCEPTED);
    eq(D.modeOf(m).mode, 'RUNNING');
    has(text(D.renderControls(m, DOC)), 'BEAT MACHINE RUNNING');
    measured.real_brain_sim_actions = first.actions.join(',');
    measured.real_brain_sim_modes_seen = [...new Set(sims.map((s) => s.mode))].join(',');
  });
  T('the run ends HOLDING, and the panel explains that it does not loop', () => {
    const last = RUN.messages.filter((m) => m.type === 'sim').pop();
    eq(last.mode, 'HOLDING');
    let m = D.deriveDemo(RUN.messages.map((msg, i) => ({ at: i * 10, msg })));
    eq(D.modeOf(m).mode, 'HOLDING');
    has(text(D.renderControls(m, DOC)), 'does NOT loop');
    eq(D.beatOf(m).complete, true);
    eq(D.disagreementOf(m), null, 'the hold beat was reported as a disagreement');
    measured.real_final_mode = last.mode;
  });
  T('EVERY message in the real run is stamped simulated, so the badge can never drift', () => {
    const untagged = RUN.messages.filter((m) => m.simulated !== true);
    deepEq(untagged.map((m) => m.type), [], 'some messages carried no simulation stamp');
    measured.real_messages_all_stamped = RUN.messages.length;
  });
  T('the panel and the brain agree about the beat on EVERY message of the run', () => {
    let m = D.noteHealth(D.initialModel(), RUN.health, 0);
    let t = 0, checked = 0;
    for (const msg of RUN.messages) {
      t += 10;
      m = D.reduceDemo(m, msg, t);
      if (!Number.isInteger(msg.sim_frame)) continue;
      eq(D.disagreementOf(m), null,
        `disagreement at sim_frame ${msg.sim_frame}: ${JSON.stringify(D.disagreementOf(m))}`);
      checked++;
    }
    ok(checked > 300, `only ${checked} tagged messages checked`);
    measured.real_beat_agreement_checks = checked;
  });
  T('replaying the ENTIRE run never puts green or red on the pixels', () => {
    let m = D.noteHealth(D.initialModel(), RUN.health, 0);
    let t = 0, painted = 0;
    for (const msg of RUN.messages) {
      t += 10;
      m = D.reduceDemo(m, msg, t);
      if (msg.type !== 'state') continue;
      assertNoGreenNoRed(D.renderDemo(m, DOC), `real frame ${msg.frame_index}`);
      painted++;
    }
    measured.real_frames_painted_and_scanned = painted;
  });
  T('the summary line moves the way a reviewer would expect it to', () => {
    const s31 = D.demoSummary(upTo(31));
    const s42 = D.demoSummary(upTo(42));
    has(s31, 'total_paise=2850');
    has(s31, 'beat=goods');
    has(s42, 'amber=');
    has(s42, 'provenance=SIMULATED');
    measured.summary_at_frame_31 = s31;
    measured.summary_at_frame_42 = s42;
  });
}

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
