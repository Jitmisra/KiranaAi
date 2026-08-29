/* GAWAAH — MUDRA + PEEL panel self-test. No browser, no bundler, no network.
 *
 *   cd /Users/agnik/Desktop/razor && node web/panels/panels.test.mjs
 *
 * web/ has no package.json, so node would load a bare `.js` as CommonJS and
 * choke on `export`. Both panels are therefore loaded through a data: URL, the
 * same trick web/selftest.mjs uses for app.js — which is also why neither panel
 * imports anything: a data: URL cannot resolve a relative specifier.
 *
 * The DOM here is a ~200-line shim, not jsdom. It implements only what the
 * panels touch, and every panel render is asserted through it: an element tree,
 * its data-* attributes and its CSSOM styles. That is enough to prove the two
 * things that matter — that an abstention is VISIBLE, and that nothing on
 * screen ever paints the settled colour.
 */
import { readFileSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const MUDRA_PATH = join(HERE, 'mudra.js');
const PEEL_PATH = join(HERE, 'peel.js');
const MUDRA_SRC = readFileSync(MUDRA_PATH, 'utf8');
const PEEL_SRC = readFileSync(PEEL_PATH, 'utf8');
const load = (src) => import('data:text/javascript;charset=utf-8;base64,'
  + Buffer.from(src, 'utf8').toString('base64'));
const M = await load(MUDRA_SRC);
const P = await load(PEEL_SRC);

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
/** A CSSOM percentage as a number — float strings are not worth asserting on. */
function parsePct(v) {
  const m = /^(-?[\d.]+)%$/.exec(String(v));
  if (!m) throw new Error(`not a percentage: ${JSON.stringify(v)}`);
  return Number(m[1]);
}
function includes(hay, needle, msg) {
  if (!String(hay).includes(needle)) {
    throw new Error(`${msg || 'includes'}: ${JSON.stringify(needle)} not in ${JSON.stringify(String(hay).slice(0, 400))}`);
  }
}
function excludes(hay, needle, msg) {
  if (String(hay).includes(needle)) {
    throw new Error(`${msg || 'excludes'}: ${JSON.stringify(needle)} IS in ${JSON.stringify(String(hay).slice(0, 400))}`);
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

/** Strip comments only — string literals survive, so UI copy is still linted. */
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

/** Strip comments AND string/regex literals: what the source EXECUTES. */
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

// ------------------------------------------------------------- DOM shim ----
const camel = (s) => s.replace(/-([a-z])/g, (_, c) => c.toUpperCase());

class TextNode {
  constructor(text) { this.nodeType = 3; this.data = String(text); this.parentNode = null; }
  get textContent() { return this.data; }
  set textContent(v) { this.data = String(v); }
  get childNodes() { return []; }
}

class Element {
  constructor(ownerDocument, tag) {
    this.nodeType = 1;
    this.ownerDocument = ownerDocument;
    this.tagName = String(tag).toUpperCase();
    this.localName = String(tag).toLowerCase();
    this.childNodes = [];
    this.parentNode = null;
    this.attributes = new Map();
    this.style = makeStyle();
    this.className = '';
    this.id = '';
    this._listeners = new Map();
  }
  get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
  get classList() {
    const self = this;
    const set = () => new Set(String(self.className).split(/\s+/).filter(Boolean));
    return {
      add(...c) { const s = set(); c.forEach((x) => s.add(x)); self.className = [...s].join(' '); },
      remove(...c) { const s = set(); c.forEach((x) => s.delete(x)); self.className = [...s].join(' '); },
      contains(c) { return set().has(c); },
      toString() { return self.className; },
    };
  }
  appendChild(n) {
    if (!n) return n;
    if (n.parentNode) n.parentNode.removeChild(n);
    n.parentNode = this;
    this.childNodes.push(n);
    return n;
  }
  append(...ns) {
    for (const n of ns) this.appendChild(typeof n === 'string' ? new TextNode(n) : n);
  }
  removeChild(n) {
    const i = this.childNodes.indexOf(n);
    if (i >= 0) { this.childNodes.splice(i, 1); n.parentNode = null; }
    return n;
  }
  replaceChild(fresh, old) {
    const i = this.childNodes.indexOf(old);
    if (i < 0) throw new Error('replaceChild: node is not a child');
    if (fresh.parentNode) fresh.parentNode.removeChild(fresh);
    fresh.parentNode = this;
    this.childNodes[i] = fresh;
    old.parentNode = null;
    return old;
  }
  replaceChildren(...ns) {
    for (const n of this.childNodes) n.parentNode = null;
    this.childNodes = [];
    for (const n of ns) this.appendChild(typeof n === 'string' ? new TextNode(n) : n);
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  setAttribute(k, v) {
    if (k === 'class') { this.className = String(v); return; }
    if (k === 'id') this.id = String(v);
    if (k === 'value' && this.localName === 'input') this.value = String(v);
    this.attributes.set(k, String(v));
  }
  getAttribute(k) {
    if (k === 'class') return this.className;
    return this.attributes.has(k) ? this.attributes.get(k) : null;
  }
  hasAttribute(k) { return k === 'class' ? this.className !== '' : this.attributes.has(k); }
  removeAttribute(k) { this.attributes.delete(k); }
  get dataset() {
    const o = {};
    for (const [k, v] of this.attributes) if (k.startsWith('data-')) o[camel(k.slice(5))] = v;
    return o;
  }
  get textContent() { return this.childNodes.map((n) => n.textContent).join(''); }
  set textContent(v) {
    for (const n of this.childNodes) n.parentNode = null;
    this.childNodes = [];
    if (v !== '' && v !== null && v !== undefined) this.appendChild(new TextNode(v));
  }
  addEventListener(type, fn) {
    if (!this._listeners.has(type)) this._listeners.set(type, []);
    this._listeners.get(type).push(fn);
  }
  dispatch(type, ev = {}) {
    for (const fn of this._listeners.get(type) ?? []) fn(ev);
    return (this._listeners.get(type) ?? []).length;
  }
  click() { return this.dispatch('click', { type: 'click' }); }
  descendants() {
    const out = [];
    const walk = (n) => { for (const c of n.childNodes) { if (c.nodeType === 1) { out.push(c); walk(c); } } };
    walk(this);
    return out;
  }
  querySelectorAll(sel) { return this.descendants().filter((e) => matchesSelector(e, sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] ?? null; }
  matches(sel) { return matchesSelector(this, sel); }
  toHTML() {
    const attrs = [...this.attributes].map(([k, v]) => ` ${k}="${v}"`).join('');
    const cls = this.className ? ` class="${this.className}"` : '';
    const kids = this.childNodes.map((n) => (n.nodeType === 3 ? n.data : n.toHTML())).join('');
    return `<${this.localName}${cls}${attrs}>${kids}</${this.localName}>`;
  }
}

function makeStyle() {
  const s = {
    setProperty(k, v) { s[camel(k)] = String(v); },
    getPropertyValue(k) { return s[camel(k)] ?? ''; },
    entries() {
      return Object.entries(s).filter(([k, v]) => typeof v === 'string' && k !== 'cssText');
    },
  };
  return s;
}

function matchesOne(el, token) {
  const m = /^([a-zA-Z][\w-]*)?((?:[#.][\w-]+|\[[^\]]+\])*)$/.exec(token);
  if (!m) return false;
  if (m[1] && el.localName !== m[1].toLowerCase()) return false;
  const rest = m[2] ?? '';
  const re = /#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:([~^$*|]?=)"?([^\]"]*)"?)?\]/g;
  let t;
  while ((t = re.exec(rest)) !== null) {
    if (t[1] !== undefined && el.id !== t[1]) return false;
    if (t[2] !== undefined && !el.classList.contains(t[2])) return false;
    if (t[3] !== undefined) {
      const have = el.getAttribute(t[3]);
      if (have === null) return false;
      if (t[4] === '=' && have !== t[5]) return false;
    }
  }
  return true;
}

function matchesSelector(el, selector) {
  return String(selector).split(',').map((s) => s.trim()).filter(Boolean).some((group) => {
    const parts = group.split(/\s+/).filter(Boolean);
    if (!matchesOne(el, parts[parts.length - 1])) return false;
    let node = el.parentNode;
    for (let i = parts.length - 2; i >= 0; i--) {
      let found = false;
      while (node) {
        if (node.nodeType === 1 && matchesOne(node, parts[i])) { found = true; node = node.parentNode; break; }
        node = node.parentNode;
      }
      if (!found) return false;
    }
    return true;
  });
}

function makeDocument() {
  const listeners = new Map();
  const doc = {
    created: 0,
    readyState: 'complete',
    createElement(tag) { doc.created++; return new Element(doc, tag); },
    createTextNode(t) { return new TextNode(t); },
    getElementById(id) { return doc.body.descendants().find((e) => e.id === id) ?? null; },
    querySelector(sel) { return doc.body.querySelector(sel); },
    querySelectorAll(sel) { return doc.body.querySelectorAll(sel); },
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    dispatchEvent(ev) {
      for (const fn of listeners.get(ev.type) ?? []) fn(ev);
      return (listeners.get(ev.type) ?? []).length;
    },
  };
  doc.body = new Element(doc, 'body');
  return doc;
}

/** Every CSSOM colour value anywhere under `root`, flattened. */
function allStyleValues(root) {
  const out = [];
  for (const e of [root, ...root.descendants()]) {
    for (const [k, v] of e.style.entries()) out.push(`${k}:${v}`);
  }
  return out;
}

// ============================================ 1. the panel contract ========
G('1. the panel contract — registerPanel(id, {onState, onFrame})');

T('mudra.js exports the contract surface', () => {
  for (const k of ['MUDRA_ID', 'MUDRA_PANEL_ID', 'MUDRA_BODY_ID', 'makePanelRegistry',
    'resolveRegister', 'resolveSetStatus', 'mudraView', 'renderMudraPanel',
    'createMudraPanel', 'attachMudraPanel', 'roiOverlayOps', 'paintRoiOverlay',
    'solidityGauge', 'panelOccupancy', 'formatPaise', 'Abstain', 'PanelStatus',
    'mudraPanelStatus', 'counterViewToMudraInput']) {
    ok(k in M, `mudra.js does not export ${k}`);
  }
  eq(M.MUDRA_ID, 'mudra', 'app.js PANEL_IDS uses the bare name');
  eq(M.MUDRA_PANEL_ID, 'panel-mudra');
  eq(M.MUDRA_BODY_ID, 'body-mudra');
  eq(M.PANEL_CONTRACT_VERSION, 1);
});

T('peel.js exports the contract surface', () => {
  for (const k of ['PEEL_ID', 'PEEL_PANEL_ID', 'PEEL_BODY_ID', 'resolveRegister',
    'resolveSetStatus', 'peelView', 'renderPeelPanel', 'createPeelPanel',
    'attachPeelPanel', 'guardFalseAccusation', 'safeImageSrc', 'TAMPER_GATE',
    'ECC_BENCH', 'PanelAbstain', 'PanelStatus', 'peelPanelStatus',
    'counterViewToPeelInput']) {
    ok(k in P, `peel.js does not export ${k}`);
  }
  eq(P.PEEL_ID, 'peel', 'app.js PANEL_IDS uses the bare name');
  eq(P.PEEL_PANEL_ID, 'panel-peel');
  eq(P.PEEL_BODY_ID, 'body-peel');
  eq(P.PANEL_CONTRACT_VERSION, 1);
});

T('both panels satisfy {id, onState, onFrame}', () => {
  const doc = makeDocument();
  const a = M.createMudraPanel({ document: doc });
  const b = P.createPeelPanel({ document: doc });
  for (const p of [a, b]) {
    eq(typeof p.id, 'string');
    eq(typeof p.onState, 'function');
    eq(typeof p.onFrame, 'function');
    ok(p.root && p.root.nodeType === 1, 'panel has no root element');
  }
  eq(a.id, 'mudra');
  eq(b.id, 'peel');
  // the SHELL owns #panel-mudra; a second element with that id would break
  // every getElementById in app.js, so our root has its own
  eq(a.root.id, 'mudra-render');
  eq(b.root.id, 'peel-render');
  eq(a.elementId, 'panel-mudra');
  eq(b.elementId, 'panel-peel');
});

T('the fallback registry fans state and frames out to every panel', () => {
  const doc = makeDocument();
  const reg = M.makePanelRegistry();
  const a = M.createMudraPanel({ document: doc });
  const b = P.createPeelPanel({ document: doc });
  reg.registerPanel(a.id, a);
  reg.registerPanel(b.id, b);
  eq(reg.ids().join(','), 'mudra,peel');
  reg.onState({ matLocked: true, state: 'AWAITING_SETTLEMENT',
    mudra: { state: 'FIST', solidity: 0.73, defects: 0, area_mm2: 9000, reason: 'closed_hand' } });
  eq(reg.get('mudra').view.state, 'FIST');
  eq(reg.onFrame({}), undefined);   // no context: nothing painted, nothing thrown
  eq(reg.get('nope'), null);
});

T('the registry refuses a nameless or non-object panel', () => {
  const reg = M.makePanelRegistry();
  let threw = 0;
  try { reg.registerPanel('', {}); } catch { threw++; }
  try { reg.registerPanel('x', null); } catch { threw++; }
  eq(threw, 2);
});

T('resolveRegister finds app.js registerPanel, and abstains when it is absent', () => {
  const calls = [];
  const g1 = { GAWAAH: { registerPanel: (id, p) => calls.push([id, p]) } };
  const r1 = M.resolveRegister(undefined, g1);
  r1('panel-mudra', { id: 'x' });
  eq(calls.length, 1);
  eq(calls[0][0], 'panel-mudra');
  const g2 = { registerPanel: (id) => calls.push([id]) };
  ok(typeof P.resolveRegister(undefined, g2) === 'function', 'bare global registerPanel missed');
  eq(M.resolveRegister(undefined, {}), null, 'invented a registry that does not exist');
  const explicit = () => {};
  eq(M.resolveRegister(explicit, g1), explicit, 'explicit register was overridden');
});

T('attach mounts into the shell fill point and registers as mudra/peel', () => {
  const doc = makeDocument();
  // the shell's real markup: a section per panel, a body inside it, and the
  // shell's own abstain block that app.js's setPanelStatus owns
  const shell = (id) => {
    const sec = doc.createElement('section');
    sec.setAttribute('id', `panel-${id}`);
    sec.setAttribute('data-status', 'OFF');
    const body = doc.createElement('div');
    body.setAttribute('id', `body-${id}`);
    const abstain = doc.createElement('div');
    abstain.setAttribute('id', `abstain-${id}`);
    body.appendChild(abstain);
    sec.appendChild(body);
    doc.body.appendChild(sec);
    return { sec, body, abstain };
  };
  const sm = shell('mudra'), sp = shell('peel');
  const seen = [];
  const register = (id, hooks) => { seen.push([id, Object.keys(hooks).sort().join('+')]); return { ok: true, id }; };
  const a = M.attachMudraPanel({ document: doc, register });
  const b = P.attachPeelPanel({ document: doc, register });
  eq(a.host, sm.body, 'mudra did not mount into #body-mudra');
  eq(b.host, sp.body, 'peel did not mount into #body-peel');
  eq(seen.map((x) => x[0]).join(','), 'mudra,peel');
  eq(seen[0][1], 'onFrame+onState', 'the registered hooks are not the contract shape');
  eq(a.registered, true);
  eq(b.registered, true);
  // the shell's abstain block SURVIVES: app.js owns it, we only add beside it
  ok(sm.body.querySelector('#abstain-mudra') === sm.abstain, 'the shell abstain block was destroyed');
  eq(sm.body.querySelectorAll('[data-gawaah-panel="mudra"]').length, 1);
  // and re-attaching replaces our render rather than stacking a second one
  M.attachMudraPanel({ document: doc, register });
  eq(sm.body.querySelectorAll('[data-gawaah-panel="mudra"]').length, 1, 'second attach stacked a duplicate');
  ok(sm.body.querySelector('#abstain-mudra') === sm.abstain, 'the shell abstain block was destroyed on re-attach');
  // exactly one element carries the shell's id, still
  eq(doc.body.descendants().filter((e) => e.id === 'panel-mudra').length, 1, 'duplicate #panel-mudra id');
});

T('a registration refused by app.js is reported, not swallowed', () => {
  const doc = makeDocument();
  const register = () => ({ ok: false, reason: 'refused_unknown_panel:mudra' });
  const r = M.attachMudraPanel({ document: doc, register });
  eq(r.registered, false);
  eq(r.registration.reason, 'refused_unknown_panel:mudra');
});

T('a gawaah:brain event is a second, equal route for a brain message', () => {
  const doc = makeDocument();
  const body = doc.createElement('div'); body.setAttribute('id', 'body-mudra');
  doc.body.appendChild(body);
  const r = M.attachMudraPanel({ document: doc });
  eq(r.listening, true);
  r.panel.onState({ matLocked: true, state: 'AWAITING_SETTLEMENT' });
  doc.dispatchEvent({ type: 'gawaah:brain', detail: { type: 'mudra', state: 'FIST', solidity: 0.73, defects: 0, area_mm2: 9000, reason: 'closed_hand' } });
  eq(r.panel.view.state, 'FIST');
  // a message for someone else does not move this panel
  doc.dispatchEvent({ type: 'gawaah:brain', detail: { type: 'saaf', used: 5 } });
  eq(r.panel.view.state, 'FIST');
  const pb = doc.createElement('div'); pb.setAttribute('id', 'body-peel');
  doc.body.appendChild(pb);
  const rp = P.attachPeelPanel({ document: doc });
  doc.dispatchEvent({ type: 'gawaah:brain', detail: { type: 'peel', name: 'c1', verdict: 'GENUINE', registered: true, ecc_ok: true, ignited_fraction: 0.001 } });
  eq(rp.panel.view.verdict, 'GENUINE');
});

T('the browser auto-mount is guarded and inert under node', () => {
  for (const [name, src] of [['mudra.js', MUDRA_SRC], ['peel.js', PEEL_SRC]]) {
    ok(/typeof document !== 'undefined' && typeof window !== 'undefined'/.test(src), `${name} automount is unguarded`);
    ok(/__GAWAAH_PANEL_AUTOMOUNT !== false/.test(src), `${name} automount cannot be turned off`);
    ok(/DOMContentLoaded/.test(src), `${name} automount does not wait for the DOM`);
  }
  // and importing under node mounted nothing anywhere
  eq(globalThis.GAWAAH_PANELS, undefined);
  eq(typeof globalThis.document, 'undefined');
});

T('attach works with no host and no registry (a panel is usable alone)', () => {
  const doc = makeDocument();
  const r = M.attachMudraPanel({ document: doc });
  eq(r.host, null);
  eq(r.registered, false);
  ok(r.panel.root.nodeType === 1);
});

T('onState re-renders in place, keeping the mount point', () => {
  const doc = makeDocument();
  const host = doc.createElement('div'); host.setAttribute('id', 'body-mudra');
  doc.body.appendChild(host);
  const { panel } = M.attachMudraPanel({ document: doc });
  const before = host.children[0];
  panel.onState({ matLocked: true, state: 'AWAITING_SETTLEMENT' });
  panel.onMessage({ type: 'mudra', state: 'OPEN', solidity: 0.92, defects: 4, compactness: 0.6, area_mm2: 12000, reason: 'open_palm' });
  eq(host.children.length, 1, 'render leaked a second root');
  ok(host.children[0] !== before, 'root was not replaced');
  eq(host.children[0].getAttribute('data-state'), 'OPEN');
});

T('the counter view and the brain message MERGE — neither erases the other', () => {
  const doc = makeDocument();
  const panel = M.createMudraPanel({ document: doc });
  panel.onMessage({ type: 'mudra', state: 'OPEN', solidity: 0.92, defects: 4, area_mm2: 12000, reason: 'open_palm' });
  eq(panel.view.abstain, M.Abstain.MAT_UNLOCKED, 'no lock yet, so no millimetres');
  // a counter view arrives with the lock; the gesture must survive it
  panel.onState({ matLocked: true, state: 'AWAITING_SETTLEMENT', lines: [] });
  eq(panel.view.state, 'OPEN', 'the counter view erased the gesture');
  eq(panel.view.sessionState, 'AWAITING_SETTLEMENT');
  // and the lines become the placements the pay-panel check reads
  panel.onState({ matLocked: true, state: 'AWAITING_SETTLEMENT', lines: [{ itemId: 'x', centreMm: [100, 350] }] });
  eq(panel.view.state, 'OPEN');
  eq(panel.occupancyAfterLines ?? panel.view.occupancy.status, 'UNKNOWN', 'no ROI configured, so occupancy is unknown');
});

T('a message for another panel is ignored, not misread as a gesture', () => {
  const doc = makeDocument();
  const panel = M.createMudraPanel({ document: doc });
  panel.onState({ matLocked: true, state: 'AWAITING_SETTLEMENT' });
  panel.onMessage({ type: 'chilla', verdict: 'MATCHED' });
  eq(panel.view.abstain, M.Abstain.NO_READING);
});

T('counterViewToMudraInput maps the app view onto the panel input', () => {
  const i = M.counterViewToMudraInput({
    state: 'AWAITING_SETTLEMENT', matLocked: true, visible: 'mudra',
    lines: [{ itemId: 'a', centreMm: [10, 20] }], totalPaise: 21437,
  });
  eq(i.matLocked, true);
  eq(i.sessionState, 'AWAITING_SETTLEMENT');
  eq(i.visible, true);
  eq(i.placements.length, 1);
  eq('mudra' in i, false, 'an absent reading must not be invented as null');
  eq(M.counterViewToMudraInput(null).matLocked, false);
});

T('counterViewToPeelInput maps the app view onto the panel input', () => {
  const i = P.counterViewToPeelInput({ matLocked: true, visible: 'peel', slots: [{ name: 'a' }] });
  eq(i.matLocked, true);
  eq(i.visible, true);
  eq(i.slots.length, 1);
  eq('peel' in i, false);
});

T('PEEL merges its verdict and its registry from separate messages', () => {
  const doc = makeDocument();
  const panel = P.createPeelPanel({ document: doc });
  panel.onMessage({ type: 'peel', name: 'counter-1', verdict: 'TAMPERED', registered: true, ecc_ok: true, ignited_fraction: 0.3 });
  // the registry has not been reported, so the brain's own `registered` flag is
  // all we have — trusted, but the panel SAYS the list is unknown
  eq(panel.view.registryKnown, false);
  eq(panel.view.verdict, 'TAMPERED');
  includes(panel.root.textContent, 'enrolment list has not been reported');
  panel.onMessage({ type: 'stickers', slots: [{ name: 'counter-1' }] });
  eq(panel.view.slotCount, 1);
  eq(panel.view.registryKnown, true);
  eq(panel.view.verdict, 'TAMPERED', 'the enrolled slot never arrived at the verdict');
  eq(panel.view.name, 'counter-1');
  excludes(panel.root.textContent, 'enrolment list has not been reported');
  // and the moment the registry says this slot is NOT enrolled, the accusation dies
  panel.onMessage({ type: 'stickers', slots: [{ name: 'other' }] });
  eq(panel.view.verdict, 'UNREGISTERABLE');
  eq(panel.view.reason, 'NOT_ENROLLED');
});

T('INVARIANT 4 — a panel refuses any frame that is not the rectified crop', () => {
  const doc = makeDocument();
  const ctx = recorder();
  const canvas = { getContext: () => ctx };
  const m = M.createMudraPanel({ document: doc });
  m.onState({ matLocked: true, state: 'AWAITING_SETTLEMENT' });
  m.onMessage({ type: 'mudra', state: 'OPEN', solidity: 0.92, defects: 4, area_mm2: 12000, reason: 'open_palm', roiMm: [20, 300, 150, 100] });
  eq(m.onFrame({ cropKind: 'raw_camera_frame', crop: canvas }), 0, 'painted on a raw frame');
  eq(m.onFrame({ raw: canvas }), 0, 'painted on a raw frame');
  eq(m.refusedFrames, 2);
  ok(m.onFrame({ cropKind: 'rectified_mat_crop', crop: canvas, width: 840, height: 1188 }) >= 2,
    'the rectified crop was not painted');
  eq(m.refusedFrames, 2);
  const p = P.createPeelPanel({ document: doc });
  eq(p.onFrame({ cropKind: 'raw_camera_frame' }), 0);
  eq(p.refusedFrames, 1);
});

// ============================================ 2. source invariants =========
G('2. source lints — the invariants, executable');

const MUDRA_CODE = stripJs(MUDRA_SRC);
const PEEL_CODE = stripJs(PEEL_SRC);
const MUDRA_NOCOMMENT = stripComments(MUDRA_SRC);
const PEEL_NOCOMMENT = stripComments(PEEL_SRC);

T('the strippers leave real code and remove real prose', () => {
  ok(MUDRA_CODE.includes('export function mudraView'), 'stripJs ate the code');
  ok(!MUDRA_CODE.includes('INVARIANT 3'), 'stripJs left comments in');
  ok(!MUDRA_CODE.includes('MediaPipe'), 'stripJs left string copy in');
  ok(MUDRA_NOCOMMENT.includes('NO MODEL'), 'stripComments ate the UI copy it must lint');
  ok(!MUDRA_NOCOMMENT.includes('INVARIANT 3'), 'stripComments left comments in');
});

T('INVARIANT 3 — neither panel names a model runtime in executable code', () => {
  const banned = [/\.onnx\b/i, /\.tflite\b/i, /\.task\b/, /\.safetensors\b/i, /onnxruntime/i,
    /mediapipe/i, /@xenova/i, /transformers\.js/i, /tensorflow/i, /\btfjs\b/i,
    /hand_?landmark/i, /\bwebnn\b/i, /\.pth\b/i, /\.pb\b/i, /\.wasm\b/i];
  for (const re of banned) {
    ok(!re.test(MUDRA_CODE), `mudra.js executes ${re}`);
    ok(!re.test(PEEL_CODE), `peel.js executes ${re}`);
  }
});

T('PEEL ships NO QR library — no encoder, no decoder, no module grid', () => {
  const banned = [/jsqr/i, /qrcode/i, /\bqr_?decoder/i, /zxing/i, /reed[-_ ]?solomon/i,
    /galois/i, /bitmatrix/i, /finder_?pattern/i, /\bversion_?table/i, /alignment_?pattern/i,
    /\bupi:\/\//i, /rzp\.io/i];
  for (const re of banned) ok(!re.test(PEEL_CODE), `peel.js executes ${re} — that is a forgery primitive`);
  // and the pixel-diff machinery IS what it uses
  ok(/ignitedFraction/.test(PEEL_CODE), 'peel.js does not carry the ignited fraction');
  ok(/TAMPER_GATE/.test(PEEL_CODE), 'peel.js does not carry the tamper gate');
});

T('no panel reaches the network, spawns a worker or evals', () => {
  const banned = [/\bfetch\s*\(/, /XMLHttpRequest/, /importScripts/, /new\s+Worker/,
    /sendBeacon/, /\beval\s*\(/, /new\s+Function/, /WebSocket/, /EventSource/,
    /https?:\/\//, /wss?:\/\//];
  for (const re of banned) {
    ok(!re.test(MUDRA_CODE), `mudra.js executes ${re}`);
    ok(!re.test(PEEL_CODE), `peel.js executes ${re}`);
  }
});

T('neither panel imports anything (they load from a data: URL)', () => {
  ok(!/(^|\n)\s*import\s/.test(MUDRA_CODE), 'mudra.js has a static import');
  ok(!/(^|\n)\s*import\s/.test(PEEL_CODE), 'peel.js has a static import');
  ok(!/\bimport\s*\(/.test(MUDRA_CODE + PEEL_CODE), 'a panel has a dynamic import');
});

T('INVARIANT 2 — no panel can paint the settled colour', () => {
  // Run on comment-stripped source, so the strings that CARRY the colours are
  // still linted; only the prose that explains the ban is removed.
  const green = [/var\(\s*--green/, /#3ddc84/i, /\bgreen\b\s*:/, /=\s*['"]green['"]/];
  for (const re of green) {
    ok(!re.test(MUDRA_NOCOMMENT), `mudra.js can paint ${re}`);
    ok(!re.test(PEEL_NOCOMMENT), `peel.js can paint ${re}`);
  }
});

T('the green lint is itself tested (a planted violation is caught)', () => {
  const planted = stripComments('const c = { ok: "var(--green, #3ddc84)" };');
  ok(/var\(\s*--green/.test(planted) && /#3ddc84/i.test(planted), 'planted colour not seen');
  const innocent = stripComments('/* never green: var(--green) #3ddc84 */\nconst c = 1;');
  ok(!/var\(\s*--green/.test(innocent) && !/#3ddc84/i.test(innocent), 'the lint reads comments');
});

T('styles go through CSSOM, never a style ATTRIBUTE (CSP is style-src self)', () => {
  for (const [name, code] of [['mudra.js', MUDRA_CODE], ['peel.js', PEEL_CODE]]) {
    ok(!/setAttribute\(\s*STR\s*,\s*STR\s*\)\s*;?\s*\/\/style/.test(code), name);
  }
  // the real check: the only setAttribute keys are data-* and a small allowlist
  for (const [name, src] of [['mudra.js', MUDRA_NOCOMMENT], ['peel.js', PEEL_NOCOMMENT]]) {
    const keys = [...src.matchAll(/setAttribute\(\s*[`'"]([^`'"]+)[`'"]/g)].map((m) => m[1]);
    for (const k of keys) {
      ok(k === 'data-panel-mounted' || k.startsWith('data-'), `${name} sets attribute ${k}`);
    }
    ok(!/\bstyle\s*=\s*["']/.test(src), `${name} writes a style attribute`);
  }
});

T('panel bytes stay inside the cold-load budget', () => {
  const mb = statSync(MUDRA_PATH).size, pb = statSync(PEEL_PATH).size;
  measured.bytes_mudra_js = mb.toLocaleString('en-US');
  measured.bytes_peel_js = pb.toLocaleString('en-US');
  measured.bytes_panels_total = (mb + pb).toLocaleString('en-US');
  measured.panels_vs_mediapipe = `${((mb + pb) / M.FORBIDDEN_MODEL_BYTES * 100).toFixed(3)} % of the forbidden model`;
  measured.panels_vs_budget = `${((mb + pb) / M.COLD_LOAD_BUDGET_BYTES * 100).toFixed(2)} % of the 4.8 MB budget`;
  // Uncompressed, comment-heavy source. The bound that matters is the cold-load
  // budget the whole client shares, not a round number.
  ok(mb + pb < 0.05 * M.COLD_LOAD_BUDGET_BYTES,
    `panels are ${mb + pb} bytes, over 5 % of the cold-load budget`);
});

// ============================================ 3. the money helper ==========
G('3. MUDRA money — integer paise, no float, ever');

const MONEY_BLOCK = (() => {
  const a = MUDRA_SRC.indexOf('// PANEL MONEY BEGIN');
  const b = MUDRA_SRC.indexOf('// PANEL MONEY END');
  ok(a > 0 && b > a, 'money block markers missing from mudra.js');
  return MUDRA_SRC.slice(a, b);
})();
const MONEY_CODE = stripJs(MONEY_BLOCK);

T('the money block exists and is executable code', () => {
  ok(MONEY_CODE.includes('export function formatPaise'), 'the block is not the money path');
});
T('no parseFloat, toFixed or Math rounding in the money path', () => {
  ok(!/parseFloat/.test(MONEY_CODE), 'parseFloat');
  ok(!/toFixed/.test(MONEY_CODE), 'toFixed');
  ok(!/Math\.(round|floor|ceil)/.test(MONEY_CODE), 'Math rounding');
});
T('no decimal literal in the money path', () => {
  const m = MONEY_CODE.match(/(?<![\w.])\d+\.\d+/g);
  ok(m === null, `decimal literal(s): ${JSON.stringify(m)}`);
});
T('the only division in the money path is the exact (a - r) / 100', () => {
  const divs = MONEY_CODE.match(/\/(?![/*=])/g) || [];
  eq(divs.length, 1, `divisions found: ${divs.length}`);
  ok(/\(a - r\) \/ 100/.test(MONEY_CODE), 'the one division is not the exact divmod100 form');
});
T('formatPaise renders integer paise exactly', () => {
  eq(M.formatPaise(21437), '₹214.37');
  eq(M.formatPaise(0), '₹0.00');
  eq(M.formatPaise(5), '₹0.05');
  eq(M.formatPaise(50), '₹0.50');
  eq(M.formatPaise(100), '₹1.00');
  eq(M.formatPaise(-2599), '-₹25.99');
});
T('formatPaise REFUSES anything that is not integer paise', () => {
  for (const bad of [2.5, 0.1, NaN, Infinity, '100', null, undefined, {}, 1e308 * 10]) {
    eq(M.formatPaise(bad), null, `formatPaise accepted ${JSON.stringify(bad)}`);
  }
});
T('divmod100 is exact across a 200k sweep', () => {
  for (let p = 0; p < 200000; p++) {
    const s = M.formatPaise(p).slice(1);
    const [w, f] = s.split('.');
    if (Number.parseInt(w, 10) * 100 + Number.parseInt(f, 10) !== p) {
      throw new Error(`inexact at ${p}: ${s}`);
    }
  }
  measured.paise_sweep = 200000;
});

// ============================================ 4. MUDRA view model ==========
G('4. MUDRA view model — states, causes and abstention');

const reading = (over = {}) => ({
  matLocked: true,
  mudra: {
    state: 'OPEN', raw_state: 'OPEN', solidity: 0.92, defects: 4, compactness: 0.62,
    area_mm2: 12000, reason: 'open_palm', frames_held: 9, border_touching: true,
    ...over,
  },
});

T('no reading at all is a named abstention, not a NONE', () => {
  const v = M.mudraView({});
  eq(v.state, 'UNKNOWN');
  eq(v.abstain, M.Abstain.NO_READING);
  eq(v.hasReading, false);
  eq(v.decided, false);
});

T('a live reading under a mat lock passes through', () => {
  const v = M.mudraView(reading());
  eq(v.state, 'OPEN');
  eq(v.abstain, null);
  eq(v.decided, true);
  eq(v.solidity, 0.92);
  eq(v.defects, 4);
  eq(v.areaMm2, 12000);
  eq(v.borderTouching, true);
  eq(v.reason.code, 'open_palm');
});

T('without a mat lock a millimetre is not a millimetre — abstain', () => {
  const v = M.mudraView({ ...reading(), matLocked: false });
  eq(v.state, 'UNKNOWN');
  eq(v.abstain, M.Abstain.MAT_UNLOCKED);
});

T('a stale reading is not a live verdict', () => {
  const base = reading();
  base.mudra.ts = 1000;
  const fresh = M.mudraView(base, { nowMs: 1000 + 200 });
  eq(fresh.abstain, null);
  eq(fresh.age.stale, false);
  const stale = M.mudraView(base, { nowMs: 1000 + M.STALE_MS + 1 });
  eq(stale.abstain, M.Abstain.STALE);
  eq(stale.state, 'UNKNOWN');
  eq(stale.age.stale, true);
});

T('an unknown age is reported as unknown, not as fresh', () => {
  const v = M.mudraView(reading());
  eq(v.age.known, false);
  eq(v.age.stale, false);
  eq(v.abstain, null, 'an untimestamped reading must not be forced to abstain');
});

T('an unpublished STATE is refused rather than displayed', () => {
  const v = M.mudraView(reading({ state: 'PINCH' }));
  eq(v.state, 'UNKNOWN');
  eq(v.abstain, M.Abstain.NO_READING);
  eq(v.engineState, null);
});

T('an unpublished REASON is flagged and never glossed', () => {
  const v = M.mudraView(reading({ reason: 'vibes' }));
  eq(v.reason.known, false);
  includes(v.reason.gloss, 'unpublished cause');
});

T('every published mudra reason has a gloss', () => {
  for (const r of M.MUDRA_REASONS) {
    ok(typeof M.REASON_GLOSS[r] === 'string' && M.REASON_GLOSS[r].length > 3, `no gloss for ${r}`);
  }
  measured.mudra_reasons = M.MUDRA_REASONS.length;
});

T('every panel abstention has a gloss', () => {
  for (const a of Object.values(M.Abstain)) {
    ok(typeof M.ABSTAIN_GLOSS[a] === 'string' && M.ABSTAIN_GLOSS[a].length > 3, `no gloss for ${a}`);
  }
  measured.mudra_abstentions = Object.keys(M.Abstain).length;
});

T('dwell telemetry is parsed off the reason, not swallowed', () => {
  const v = M.mudraView(reading({ state: 'FIST', reason: 'open_palm|dwell_2/4' }));
  eq(v.reason.code, 'open_palm');
  eq(v.reason.dwell.count, 2);
  eq(v.reason.dwell.of, 4);
  eq(v.reason.known, true);
});

T('AMBIGUOUS is a first-class state, not an error', () => {
  const v = M.mudraView(reading({ state: 'AMBIGUOUS', reason: 'mid_solidity_too_few_defects', solidity: 0.86, defects: 1 }));
  eq(v.ambiguous, true);
  eq(v.decided, false);
  eq(v.abstain, null, 'AMBIGUOUS was mistaken for a panel failure');
  includes(v.reason.gloss, 'too few deep notches');
});

T('raw vs committed chatter is visible', () => {
  const v = M.mudraView(reading({ state: 'FIST', raw_state: 'OPEN', reason: 'open_palm|dwell_1/4' }));
  eq(v.chattering, true);
  eq(v.rawState, 'OPEN');
  eq(v.state, 'FIST');
});

T('mudraView is idempotent (a view re-rendered is not re-read as a reading)', () => {
  const v1 = M.mudraView(reading());
  const v2 = M.mudraView(v1);
  ok(v1 === v2, 'a view was re-normalised into a different object');
  eq(v2.state, 'OPEN');
});

T('the bare wire message {type:"mudra", ...} is accepted', () => {
  const v = M.mudraView({ state: 'GOODS', solidity: 0.97, defects: 0, area_mm2: 30000, reason: 'inert_object', matLocked: true });
  eq(v.state, 'GOODS');
  eq(v.sessionState, null, 'the gesture state was mistaken for the session state');
});

// ---- thresholds and calibration
T('uncalibrated thresholds are module defaults and SAY SO', () => {
  const t = M.normaliseThresholds(null);
  eq(t.calibrated, false);
  eq(t.fistMax, 0.80);
  eq(t.armReason, M.Abstain.UNCALIBRATED);
});

T('a measured gap under 0.08 disarms the solidity channel', () => {
  const t = M.normaliseThresholds({ p95Open: 0.90, p05Fist: 0.85, samples: 20 });
  near(t.gap, 0.05, 1e-9);
  eq(t.gapOk, false);
  eq(t.armReason, M.Abstain.GAP_TOO_SMALL);
});

T('a measured gap at or over 0.08 arms it', () => {
  const t = M.normaliseThresholds({ p95Open: 0.93, p05Fist: 0.85, samples: 20, fistMax: 0.85, openLo: 0.85, openHi: 0.90, goodsMin: 0.90 });
  near(t.gap, 0.08, 1e-9);
  eq(t.gapOk, true);
  eq(t.armReason, null);
  eq(t.fistMax, 0.85);
  eq(t.openHi, 0.90);
});

T('snake_case calibration from the brain is accepted', () => {
  const t = M.normaliseThresholds({ p95_open: 0.94, p05_fist: 0.84, samples: 20, fist_max: 0.86 });
  near(t.gap, 0.10, 1e-9);
  eq(t.fistMax, 0.86);
});

// ---- the gauge
T('the gauge partitions the solidity axis with the calibrated cut points', () => {
  const g = M.solidityGauge(0.92, M.DEFAULT_THRESHOLDS);
  eq(g.lo, 0.60); eq(g.hi, 1.00);
  const states = g.zones.map((z) => z.state).join(',');
  eq(states, 'FIST,OPEN,GOODS', `zones: ${states}`);
  near(g.zones[0].leftPct, 0, 1e-9);
  near(g.zones[0].widthPct, 50, 1e-9);      // 0.60..0.80 of a 0.40 domain
  near(g.valuePct, 80, 1e-9);               // 0.92 -> 80 %
});

T('a calibrated dead band appears as an AMBIGUOUS zone', () => {
  const th = M.normaliseThresholds({ fistMax: 0.85, openLo: 0.86, openHi: 0.90, goodsMin: 0.94, samples: 9, gap: 0.2 });
  const g = M.solidityGauge(0.88, th);
  const states = g.zones.map((z) => z.state).join(',');
  eq(states, 'FIST,AMBIGUOUS,OPEN,AMBIGUOUS,GOODS');
});

T('the gauge carries the measured hand references as provenance', () => {
  const g = M.solidityGauge(0.73);
  const labels = g.refs.map((r) => r.label).join(',');
  eq(labels, 'fist,open palm,goods');
  near(g.refs[0].at, 0.73, 1e-9);
  near(g.refs[1].at, 0.92, 1e-9);
  near(g.refs[2].from, 0.96, 1e-9);
  near(M.gaugePct(0.73), 32.5, 1e-9);
});

T('a solidity below the scale is flagged, not silently clamped', () => {
  const g = M.solidityGauge(0.41);
  eq(g.belowScale, true);
  eq(g.valuePct, 0);
});

T('duplicate cut points collapse into one tick', () => {
  const g = M.solidityGauge(0.9, M.DEFAULT_THRESHOLDS);
  eq(g.ticks.length, 2, `ticks: ${JSON.stringify(g.ticks.map((t) => t.label))}`);
  includes(g.ticks[0].label, 'fist max');
  includes(g.ticks[0].label, 'open lo');
});

// ---- the pay panel
const ROI = { x: 20, y: 300, w: 150, h: 100 };

T('overlapFraction is the fraction of the ROI covered', () => {
  near(M.overlapFraction(ROI, { x: 20, y: 300, w: 75, h: 100 }), 0.5, 1e-12);
  eq(M.overlapFraction(ROI, { x: 200, y: 0, w: 10, h: 10 }), 0);
  eq(M.overlapFraction(null, ROI), 0);
});

T('an unconfigured pay panel is UNKNOWN with a named cause', () => {
  const o = M.panelOccupancy(null, []);
  eq(o.status, 'UNKNOWN');
  eq(o.reason, M.Abstain.ROI_UNCONFIGURED);
});

T('no placement list at all is UNKNOWN, never CLEAR', () => {
  const o = M.panelOccupancy(ROI, null);
  eq(o.status, 'UNKNOWN');
  eq(o.reason, M.Abstain.OCCUPANCY_UNKNOWN);
});

T('an empty placement list is CLEAR', () => {
  const o = M.panelOccupancy(ROI, []);
  eq(o.status, 'CLEAR');
  eq(o.reason, null);
});

T('goods over the panel BLOCK it (SIX.md 8.2 — the false-cancel case)', () => {
  const o = M.panelOccupancy(ROI, [{ itemId: 'i1', boxMm: { x: 30, y: 310, w: 60, h: 60 } }]);
  eq(o.status, 'BLOCKED');
  eq(o.reason, M.Abstain.PANEL_BLOCKED);
  eq(o.blockers.length, 1);
  eq(o.blockers[0].id, 'i1');
  near(o.coverage, 0.24, 1e-9);
});

T('a placement centre inside the panel blocks it even with no extent', () => {
  const o = M.panelOccupancy(ROI, [{ itemId: 'i2', centreMm: [100, 350] }]);
  eq(o.status, 'BLOCKED');
  eq(o.blockers[0].how, 'centre');
  eq(o.coverage, null, 'a point was reported as an area');
});

T('a centre NEAR the panel with no extent is UNKNOWN, not CLEAR and not BLOCKED', () => {
  const o = M.panelOccupancy(ROI, [{ itemId: 'i3', centreMm: [100, 290] }]);
  eq(o.status, 'UNKNOWN');
  eq(o.reason, M.Abstain.GOODS_EXTENT_UNKNOWN);
});

T('a reverted placement no longer blocks the panel', () => {
  const o = M.panelOccupancy(ROI, [{ itemId: 'i4', centreMm: [100, 350], reverted: true }]);
  eq(o.status, 'CLEAR');
});

T('a tiny overlap under the coverage floor does not block', () => {
  const o = M.panelOccupancy(ROI, [{ itemId: 'i5', boxMm: { x: 19, y: 299, w: 2, h: 2 } }]);
  eq(o.status, 'CLEAR', 'a 1 mm² clip counted as a blocked panel');
});

// ---- arming
const armState = (over = {}) => ({
  ...reading(),
  sessionState: 'AWAITING_SETTLEMENT',
  payPanelMm: ROI,
  placements: [],
  calibration: { p95Open: 0.94, p05Fist: 0.84, samples: 20 },
  ...over,
});

T('a calibrated, clear, settled, locked mat arms the gesture', () => {
  const v = M.mudraView(armState());
  eq(v.arm.armed, true);
  eq(v.arm.reasons.length, 0);
  eq(v.arm.tapFallback, false);
});

T('every refusal to arm is named and they stack', () => {
  const v = M.mudraView(armState({
    sessionState: 'BASKET_OPEN',
    placements: [{ itemId: 'i1', boxMm: { x: 30, y: 310, w: 60, h: 60 } }],
    screenMatchLive: true,
    calibration: null,
  }));
  eq(v.arm.armed, false);
  ok(v.arm.reasons.includes(M.Abstain.PANEL_BLOCKED), 'blocked panel not named');
  ok(v.arm.reasons.includes(M.Abstain.DISARMED_BY_SCREEN), 'screen disarm not named');
  ok(v.arm.reasons.includes(M.Abstain.NOT_SETTLEMENT), 'session gate not named');
  ok(v.arm.reasons.includes(M.Abstain.UNCALIBRATED), 'calibration gate not named');
  eq(v.arm.tapFallback, true);
});

T('MUDRA is disarmed while a payment screen is being matched', () => {
  const v = M.mudraView(armState({ screenMatchLive: true }));
  eq(v.arm.armed, false);
  eq(v.arm.reasons[0], M.Abstain.DISARMED_BY_SCREEN);
});

// ---- the target
T('with no minted target the panel says so and shows nothing', () => {
  const v = M.mudraView(armState());
  eq(v.target.present, false);
  eq(v.target.abstain, M.Abstain.NO_TARGET);
  eq(v.target.revealed, false);
});

T('an armed OPEN palm REVEALS a pre-minted target — it does not mint it', () => {
  const v = M.mudraView(armState({ target: { amountPaise: 21437, minted: true, source: 'CORE' } }));
  eq(v.target.present, true);
  eq(v.target.text, '₹214.37');
  eq(v.target.minted, true);
  eq(v.target.revealed, true);
  eq(v.mintsMoney, false);
  eq(v.canGoGreen, false);
});

T('a FIST hides the target again', () => {
  const v = M.mudraView(armState({
    mudra: { ...reading().mudra, state: 'FIST', reason: 'closed_hand', solidity: 0.73, defects: 0 },
    target: { amountPaise: 21437, minted: true },
  }));
  eq(v.state, 'FIST');
  eq(v.target.revealed, false);
});

T('a disarmed panel never reveals, however open the palm', () => {
  const v = M.mudraView(armState({ screenMatchLive: true, target: { amountPaise: 21437, minted: true } }));
  eq(v.state, 'OPEN');
  eq(v.target.revealed, false);
});

T('a non-integer target amount is REFUSED, never rounded', () => {
  const v = M.mudraView(armState({ target: { amountPaise: 214.37, minted: true } }));
  eq(v.target.text, null);
  eq(v.target.refused, M.Abstain.TARGET_NOT_INTEGER);
});

T('area outside the hand band is called implausible', () => {
  eq(M.mudraView(reading({ area_mm2: 12000 })).areaPlausible, true);
  eq(M.mudraView(reading({ area_mm2: 45000 })).areaPlausible, false);
  eq(M.mudraView(reading({ area_mm2: 900 })).areaPlausible, false);
});

// ============================================ 5. MUDRA rendering ===========
G('5. MUDRA rendering — the abstention has to be VISIBLE');

const renderM = (state) => {
  const doc = makeDocument();
  const root = M.renderMudraPanel(state, doc);
  doc.body.appendChild(root);
  return { doc, root };
};

T('the root carries the panel id, state and abstention as data attributes', () => {
  const { root } = renderM(reading());
  eq(root.id, 'mudra-render');
  eq(root.getAttribute('data-gawaah-panel'), 'mudra');
  eq(root.getAttribute('data-panel'), 'mudra');
  eq(root.getAttribute('data-state'), 'OPEN');
  eq(root.getAttribute('data-abstain'), '');
  eq(root.getAttribute('data-contract'), '1');
  ok(root.classList.contains('panel-mudra'));
});

T('AMBIGUOUS is LOUD — bigger, bordered, amber, and says I DO NOT KNOW', () => {
  const { root } = renderM(reading({ state: 'AMBIGUOUS', reason: 'goods_solidity_but_elongated', solidity: 0.97 }));
  const v = root.querySelector('.mudra-verdict');
  eq(v.getAttribute('data-state'), 'AMBIGUOUS');
  eq(v.getAttribute('data-tone'), 'amber');
  includes(v.style.border, '2px');
  includes(v.style.background, 'rgba(224,163,60');
  const word = root.querySelector('.mudra-verdict-state');
  eq(word.textContent, 'AMBIGUOUS');
  eq(word.style.fontSize, '30px');
  includes(word.style.color, '--amber');
  includes(root.textContent, 'I DO NOT KNOW');
  includes(root.textContent, 'goods_solidity_but_elongated');
  includes(root.textContent, 'too elongated');
});

T('a decided state is calm — 24px, one-pixel border, no I DO NOT KNOW', () => {
  const { root } = renderM(reading());
  const word = root.querySelector('.mudra-verdict-state');
  eq(word.textContent, 'OPEN');
  eq(word.style.fontSize, '24px');
  excludes(root.querySelector('.mudra-verdict').textContent, 'I DO NOT KNOW');
});

T('every abstention renders its named cause on screen', () => {
  const cases = [
    [{}, M.Abstain.NO_READING],
    [{ ...reading(), matLocked: false }, M.Abstain.MAT_UNLOCKED],
    [(() => { const s = reading(); s.mudra.ts = 0; s.nowMs = 99999; return s; })(), M.Abstain.STALE],
  ];
  for (const [state, code] of cases) {
    const { root } = renderM(state);
    eq(root.getAttribute('data-state'), 'UNKNOWN', `state for ${code}`);
    eq(root.getAttribute('data-abstain'), code);
    includes(root.textContent, code, `the cause ${code} is not on screen`);
    includes(root.textContent, 'I DO NOT KNOW');
  }
});

T('the solidity number and gauge are both rendered, with the thresholds on it', () => {
  const { root } = renderM(reading({ solidity: 0.92 }));
  const val = root.querySelector('.mudra-metric-solidity .mudra-metric-value');
  eq(val.textContent, '0.920');
  const zones = root.querySelectorAll('.mudra-gauge-zone');
  eq(zones.length, 3);
  eq(zones.map((z) => z.getAttribute('data-zone')).join(','), 'FIST,OPEN,GOODS');
  const ticks = root.querySelectorAll('.mudra-gauge-tick');
  eq(ticks.map((t) => t.getAttribute('data-at')).join(','), '0.80,0.95');
  const needle = root.querySelector('.mudra-gauge-needle');
  eq(needle.getAttribute('data-value'), '0.920');
  near(parsePct(needle.style.left), 80, 1e-9, 'needle position');
});

T('the measured hand references are drawn on the gauge', () => {
  const { root } = renderM(reading());
  const refs = root.querySelectorAll('.mudra-gauge-ref');
  eq(refs.length, 3);
  includes(refs[0].textContent, 'fist 0.73');
  includes(refs[1].textContent, 'open palm 0.92');
  includes(refs[2].textContent, 'goods 0.96–1.00');
  near(parsePct(refs[0].style.left), 32.5, 1e-9, 'fist reference position');
});

T('defect count, compactness and area in mm² are all on screen', () => {
  const { root } = renderM(reading({ defects: 4, compactness: 0.62, area_mm2: 12345 }));
  const byMetric = (m) => root.querySelector(`[data-metric="${m}"]`).textContent;
  includes(byMetric('defects'), '4');
  includes(byMetric('defects'), '≥3');
  includes(byMetric('compactness'), '0.620');
  includes(byMetric('compactness'), '0.897');
  includes(byMetric('area'), '12,345 mm²');
  includes(byMetric('area'), '4,000–22,000 mm²');
});

T('an implausible area is called out as a hand merged with goods', () => {
  const { root } = renderM(reading({ area_mm2: 45000 }));
  includes(root.querySelector('[data-metric="area"]').textContent, 'NOT HAND-SIZED');
});

T('a missing metric renders an em dash, never a zero', () => {
  const { root } = renderM({ matLocked: true, mudra: { state: 'NONE', reason: 'no_occluder' } });
  eq(root.querySelector('.mudra-metric-solidity .mudra-metric-value').textContent, '—');
  includes(root.querySelector('[data-metric="area"]').textContent, '—');
});

T('an uncalibrated panel says the thresholds are not this shop\'s hand', () => {
  const { root } = renderM(reading());
  const th = root.querySelector('.mudra-thresholds');
  eq(th.getAttribute('data-calibrated'), 'false');
  includes(th.textContent, 'UNCALIBRATED');
  includes(th.textContent, 'solidity channel NOT ARMED');
  includes(th.textContent, 'tap-to-arm');
});

T('a calibrated panel prints the measured gap against the 0.08 floor', () => {
  const { root } = renderM(armState());
  const th = root.querySelector('.mudra-thresholds');
  eq(th.getAttribute('data-calibrated'), 'true');
  includes(th.textContent, 'p95(open) 0.940');
  includes(th.textContent, 'p05(fist) 0.840');
  includes(th.textContent, 'gap 0.100');
});

T('PANEL BLOCKED is a loud, named state on the panel and the map', () => {
  const { root } = renderM(armState({ placements: [{ itemId: 'atta-1kg', boxMm: { x: 30, y: 310, w: 60, h: 60 } }] }));
  eq(root.getAttribute('data-occupancy'), 'BLOCKED');
  const occ = root.querySelector('.mudra-occupancy');
  eq(occ.getAttribute('data-occupancy'), 'BLOCKED');
  includes(occ.textContent, 'PANEL BLOCKED');
  includes(occ.textContent, M.Abstain.PANEL_BLOCKED);
  includes(occ.textContent, '0.859');            // the measured false-cancel number
  includes(occ.textContent, 'atta-1kg');
  const roiBox = root.querySelector('.mudra-map-roi');
  eq(roiBox.getAttribute('data-status'), 'BLOCKED');
  includes(roiBox.style.border, 'dashed');
});

T('the pay-panel ROI is drawn to scale on the mat map', () => {
  const { root } = renderM(armState());
  const box = root.querySelector('.mudra-map-roi');
  near(parsePct(box.style.left), (20 / 297) * 100, 1e-9, 'roi x');
  near(parsePct(box.style.top), (300 / 420) * 100, 1e-9, 'roi y');
  near(parsePct(box.style.width), (150 / 297) * 100, 1e-9, 'roi w');
  near(parsePct(box.style.height), (100 / 420) * 100, 1e-9, 'roi h');
  includes(root.querySelector('.mudra-roi-rect').textContent, '150.00×100.00 mm');
  includes(root.querySelector('.mudra-roi-buffer').textContent, '840×1188');
});

T('an unconfigured ROI renders its reason instead of an empty box', () => {
  const { root } = renderM(reading());
  eq(root.getAttribute('data-occupancy'), 'UNKNOWN');
  includes(root.textContent, M.Abstain.ROI_UNCONFIGURED);
  eq(root.querySelector('.mudra-map-roi'), null);
});

T('the reveal disclaimer is on screen, in the DOM, every render', () => {
  for (const s of [{}, reading(), armState()]) {
    const { root } = renderM(s);
    const foot = root.querySelector('.mudra-disclaimer');
    includes(foot.textContent, 'reveals a target CORE already minted');
    includes(foot.textContent, 'never turns the counter green');
    eq(foot.getAttribute('data-invariant'), '2');
  }
});

T('the no-model arithmetic is printed, not claimed', () => {
  const { root } = renderM(reading());
  const badge = root.querySelector('.gw-badge-nomodel');
  includes(badge.textContent, '7,819,105 B');
  includes(badge.textContent, '1.63×');
  includes(badge.textContent, '4.8 MB');
  eq(badge.getAttribute('data-invariant'), '3');
  measured.mediapipe_vs_budget = `${(M.FORBIDDEN_MODEL_BYTES / M.COLD_LOAD_BUDGET_BYTES).toFixed(3)}x`;
});

T('the target is rendered with its provenance and never as a mint', () => {
  const { root } = renderM(armState({ target: { amountPaise: 21437, minted: true, source: 'CORE' } }));
  includes(root.querySelector('.mudra-target-amount').textContent, '₹214.37');
  includes(root.querySelector('.mudra-target-provenance').textContent, 'pre-minted by CORE');
  includes(root.querySelector('.mudra-target-provenance').textContent, 'REVEALED');
});

T('a refused (non-integer) target renders a refusal, not a number', () => {
  const { root } = renderM(armState({ target: { amountPaise: 214.37, minted: true } }));
  const n = root.querySelector('.mudra-target-refused');
  includes(n.textContent, 'REFUSED');
  includes(n.textContent, M.Abstain.TARGET_NOT_INTEGER);
  eq(root.querySelector('.mudra-target-amount'), null, 'a float amount reached the screen');
});

T('INVARIANT 2 at the DOM level — no rendered style is ever the settled colour', () => {
  const states = [{}, reading(), reading({ state: 'AMBIGUOUS', reason: 'solidity_dead_band' }),
    reading({ state: 'GOODS', reason: 'inert_object' }), armState(),
    armState({ target: { amountPaise: 1, minted: true } })];
  let checked = 0;
  for (const s of states) {
    const { root } = renderM(s);
    for (const v of allStyleValues(root)) {
      ok(!/--green|#3ddc84/i.test(v), `a rendered style is green: ${v}`);
      checked++;
    }
  }
  measured.mudra_style_values_checked = checked;
  ok(checked > 200, `only ${checked} style values were checked`);
});

// ============================================ 6. the ROI overlay ===========
G('6. MUDRA overlay — drawn on the rectified crop, as data first');

function recorder() {
  const calls = [];
  return {
    calls,
    save() { calls.push(['save']); }, restore() { calls.push(['restore']); },
    beginPath() { calls.push(['beginPath']); }, stroke() { calls.push(['stroke']); },
    moveTo(...a) { calls.push(['moveTo', ...a]); }, lineTo(...a) { calls.push(['lineTo', ...a]); },
    strokeRect(...a) { calls.push(['strokeRect', ...a]); },
    fillText(...a) { calls.push(['fillText', ...a]); },
    setLineDash(a) { calls.push(['setLineDash', JSON.stringify(a)]); },
    set strokeStyle(v) { calls.push(['strokeStyle', v]); },
    set fillStyle(v) { calls.push(['fillStyle', v]); },
    set lineWidth(v) { calls.push(['lineWidth', v]); },
    set font(v) { calls.push(['font', v]); },
    textAlign: 'left',
  };
}

T('with no ROI the overlay is one honest label, not an invented rectangle', () => {
  const ops = M.roiOverlayOps(M.mudraView(reading()));
  eq(ops.length, 1);
  eq(ops[0].op, 'text');
  includes(ops[0].text, M.Abstain.ROI_UNCONFIGURED);
});

T('a clear panel draws a solid rectangle', () => {
  const ops = M.roiOverlayOps(M.mudraView(armState()));
  const r = ops.find((o) => o.op === 'rect');
  eq(r.dashMm, null);
  eq(r.xMm, 20); eq(r.wMm, 150);
  ok(ops.some((o) => o.op === 'text' && o.text === 'PAY PANEL'));
  ok(!ops.some((o) => o.op === 'hatch'), 'a clear panel was hatched');
});

T('a blocked panel is dashed, hatched and labelled', () => {
  const ops = M.roiOverlayOps(M.mudraView(armState({ placements: [{ itemId: 'i1', centreMm: [100, 350] }] })));
  ok(ops.some((o) => o.op === 'rect' && o.dashMm !== null), 'not dashed');
  ok(ops.some((o) => o.op === 'hatch'), 'not hatched');
  ok(ops.some((o) => o.op === 'text' && o.text.includes('BLOCKED')), 'not labelled');
  ok(ops.every((o) => o.stroke !== '#3ddc84'), 'the overlay painted the settled colour');
});

T('paintRoiOverlay converts millimetres to the 840×1188 buffer', () => {
  const ctx = recorder();
  const n = M.paintRoiOverlay(ctx, M.mudraView(armState()));
  ok(n >= 2, `painted ${n} ops`);
  const rectCall = ctx.calls.find((c) => c[0] === 'strokeRect');
  near(rectCall[1], 20 * (840 / 297), 1e-9);
  near(rectCall[2], 300 * (1188 / 420), 1e-9);
  near(rectCall[3], 150 * (840 / 297), 1e-9);
});

T('paintRoiOverlay with no context is a no-op, not a crash', () => {
  eq(M.paintRoiOverlay(null, M.mudraView(armState())), 0);
  eq(M.paintRoiOverlay(undefined, M.mudraView({})), 0);
  eq(M.paintRoiOverlay({}, M.mudraView({})), 0);
});

T('onFrame paints through the canvas the app hands the panel', () => {
  const doc = makeDocument();
  const ctx = recorder();
  const panel = M.createMudraPanel({ document: doc });
  panel.onState(armState());
  const painted = panel.onFrame({ rect: { getContext: () => ctx } });
  ok(painted >= 2, 'nothing was painted onto the rectified crop');
  ok(ctx.calls.some((c) => c[0] === 'strokeRect'));
});

T('a frame can never change a verdict', () => {
  const doc = makeDocument();
  const panel = M.createMudraPanel({ document: doc });
  panel.onState(reading({ state: 'FIST', reason: 'closed_hand' }));
  const before = panel.view.state;
  panel.onFrame({ rectCtx: recorder() });
  eq(panel.view.state, before);
});

// ============================================ 7. PEEL — the guard ==========
G('7. PEEL — the false-accusation guard (the worst failure this product has)');

const verdictMsg = (over = {}) => ({
  name: 'counter-1', verdict: P.GENUINE, reason: 'COMPARED', registered: true,
  ecc_ok: true, ignited_fraction: 0.0068, ecc_cc: 0.94, ecc_shift_px: 0.4,
  ...over,
});

T('an UNENROLLED sticker is GREY and is never called tampered', () => {
  const v = P.peelView({ peel: verdictMsg({ verdict: P.TAMPERED, registered: false, ignited_fraction: 0.31 }) });
  eq(v.verdict, P.UNREGISTERABLE);
  eq(v.reason, P.R.NOT_ENROLLED);
  eq(v.accused, false);
  eq(v.downgraded, true);
  ok(v.downgrades.includes(P.R.NOT_ENROLLED));
});

T('a slot missing from the registry cannot be "registered", whatever the message says', () => {
  const v = P.peelView({
    slots: [{ name: 'counter-2' }],
    peel: verdictMsg({ verdict: P.TAMPERED, registered: true, ecc_ok: true, ignited_fraction: 0.4 }),
  });
  eq(v.verdict, P.UNREGISTERABLE);
  eq(v.reason, P.R.NOT_ENROLLED);
  eq(v.accused, false);
});

T('an accusation with no ECC re-registration is refused', () => {
  const v = P.peelView({
    slots: [{ name: 'counter-1' }],
    peel: verdictMsg({ verdict: P.TAMPERED, ecc_ok: false, ignited_fraction: 0.161 }),
  });
  eq(v.verdict, P.UNREGISTERABLE);
  eq(v.reason, P.PanelAbstain.ECC_NOT_APPLIED);
  eq(v.accused, false);
});

T('an accusation under its own gate is refused as self-contradictory', () => {
  const v = P.peelView({
    slots: [{ name: 'counter-1' }],
    peel: verdictMsg({ verdict: P.TAMPERED, ignited_fraction: 0.01 }),
  });
  eq(v.verdict, P.UNREGISTERABLE);
  eq(v.reason, P.PanelAbstain.BELOW_TAMPER_GATE);
});

T('an accusation with no number at all is refused', () => {
  const v = P.peelView({
    slots: [{ name: 'counter-1' }],
    peel: { name: 'counter-1', verdict: P.TAMPERED, registered: true, ecc_ok: true },
  });
  eq(v.verdict, P.UNREGISTERABLE);
  eq(v.reason, P.PanelAbstain.NO_IGNITED_FRACTION);
});

T('a real substitution — enrolled, ECC applied, over the gate — IS rendered TAMPERED', () => {
  const v = P.peelView({
    slots: [{ name: 'counter-1' }],
    peel: verdictMsg({ verdict: P.TAMPERED, ignited_fraction: 0.2241, ecc_cc: 0.37 }),
  });
  eq(v.verdict, P.TAMPERED);
  eq(v.accused, true);
  eq(v.downgraded, false);
});

T('a stale verdict is not shown as a live accusation', () => {
  const st = { slots: [{ name: 'counter-1' }], peel: verdictMsg({ verdict: P.TAMPERED, ignited_fraction: 0.3, ts: 0 }) };
  const v = P.peelView(st, { nowMs: P.STALE_MS + 1 });
  eq(v.verdict, P.UNREGISTERABLE);
  eq(v.reason, P.PanelAbstain.VERDICT_STALE);
  eq(v.accused, false);
});

T('an unpublished verdict word is refused rather than displayed', () => {
  const v = P.peelView({ slots: [{ name: 'counter-1' }], peel: verdictMsg({ verdict: 'FRAUD' }) });
  eq(v.verdict, P.UNREGISTERABLE);
  eq(v.reason, P.PanelAbstain.UNPUBLISHED_VERDICT);
});

T('no comparison yet is its own named state', () => {
  const v = P.peelView({ slots: [] });
  eq(v.verdict, P.UNREGISTERABLE);
  eq(v.reason, P.PanelAbstain.NO_COMPARISON);
  eq(v.hasComparison, false);
});

T('a GENUINE verdict with no number is refused too', () => {
  const v = P.peelView({
    slots: [{ name: 'counter-1' }],
    peel: { name: 'counter-1', verdict: P.GENUINE, registered: true, ecc_ok: true },
  });
  eq(v.verdict, P.UNREGISTERABLE);
  eq(v.reason, P.PanelAbstain.NO_IGNITED_FRACTION);
});

T('PROPERTY: over 6000 random messages, an accusation implies all five conditions', () => {
  const rnd = mulberry32(0x5eed);
  const verdicts = [P.GENUINE, P.TAMPERED, P.UNREGISTERABLE, 'FRAUD', null];
  let accusations = 0, cases = 0;
  for (let i = 0; i < 6000; i++) {
    const verdict = verdicts[Math.floor(rnd() * verdicts.length)];
    const registered = rnd() < 0.6;
    const eccOk = rnd() < 0.6;
    const ignited = rnd() < 0.15 ? null : rnd() * 0.5;
    const stale = rnd() < 0.3;
    const g = P.guardFalseAccusation({ verdict, registered, eccOk, ignitedFraction: ignited, reason: 'COMPARED' }, { stale });
    cases++;
    ok(P.VERDICTS.includes(g.verdict), `unpublished verdict escaped: ${g.verdict}`);
    ok(typeof g.reason === 'string' && g.reason.length > 0, 'a verdict escaped with no named cause');
    if (g.accused) {
      accusations++;
      ok(verdict === P.TAMPERED, 'accused without the brain saying TAMPERED');
      ok(registered === true, 'accused an unenrolled sticker');
      ok(eccOk === true, 'accused with no ECC re-registration');
      ok(ignited !== null && ignited >= P.TAMPER_GATE, 'accused under the gate');
      ok(stale === false, 'accused on a stale verdict');
    }
    ok(g.accused === (g.verdict === P.TAMPERED), 'TAMPERED and accused disagree');
  }
  measured.peel_guard_cases = cases;
  measured.peel_guard_accusations = accusations;
  ok(accusations > 100, `only ${accusations} accusations — the guard may be refusing everything`);
});

T('PROPERTY: an unenrolled sticker is NEVER accused, across the whole matrix', () => {
  const rnd = mulberry32(99);
  for (let i = 0; i < 3000; i++) {
    const g = P.guardFalseAccusation({
      verdict: [P.GENUINE, P.TAMPERED, P.UNREGISTERABLE][Math.floor(rnd() * 3)],
      registered: false,
      eccOk: rnd() < 0.5,
      ignitedFraction: rnd() * 0.9,
      reason: 'COMPARED',
    }, { stale: rnd() < 0.5 });
    eq(g.verdict, P.UNREGISTERABLE);
    eq(g.reason, P.R.NOT_ENROLLED);
    eq(g.accused, false);
  }
});

// ============================================ 8. PEEL view model ===========
G('8. PEEL view model — enrolment, crops and the ECC benefit');

T('safeImageSrc admits inline data and blobs, and refuses the network', () => {
  eq(P.safeImageSrc('data:image/png;base64,iVBORw0KGgo='), 'data:image/png;base64,iVBORw0KGgo=');
  eq(P.safeImageSrc('blob:null/abc'), 'blob:null/abc');
  eq(P.safeImageSrc('http://example.com/a.png'), null);
  eq(P.safeImageSrc('https://cdn.example.com/a.png'), null);
  eq(P.safeImageSrc('javascript:alert(1)'), null);
  eq(P.safeImageSrc('data:text/html;base64,PHNjcmlwdD4='), null);
  eq(P.safeImageSrc(''), null);
  eq(P.safeImageSrc(null), null);
});

T('slots are normalised, sorted and counted', () => {
  const v = P.peelView({
    slots: [{ name: 'zeta', shape: [180, 200], contrast: 41.2, sharpness: 88.1, enrolled_ts: '2026-08-29T00:00:00Z', digest: 'abcdef0123456789' },
      'alpha', { name: '' }, null],
  });
  eq(v.slotCount, 2);
  eq(v.slots.map((s) => s.name).join(','), 'alpha,zeta');
  eq(v.slots[1].digest, 'abcdef012345');
  eq(v.slots[1].shape.join('x'), '180x200');
});

T('the enrol gates are named, one per refusal', () => {
  const base = { slots: [], selectedSlot: 'counter-1', freshCropPx: [120, 130], freshContrast: 22, matLocked: true };
  eq(P.peelView(base).enrol.can, true);
  eq(P.peelView({ ...base, selectedSlot: '' }).enrol.blocks[0], 'enrol_needs_a_slot_name');
  ok(P.peelView({ ...base, freshCropPx: [40, 200] }).enrol.blocks.includes('enrol_crop_below_64px'));
  ok(P.peelView({ ...base, freshContrast: 3 }).enrol.blocks.includes('enrol_crop_carries_no_structure'));
  ok(P.peelView({ ...base, matLocked: false }).enrol.blocks.includes('enrol_needs_a_mat_lock'));
  ok(P.peelView({ slots: [], selectedSlot: 'x' }).enrol.blocks.includes('enrol_needs_a_fresh_crop'));
});

T('the ECC benefit falls back to bench numbers and LABELS them as bench', () => {
  const v = P.peelView({ slots: [] });
  eq(v.ecc.benefit.measuredHere, false);
  near(v.ecc.benefit.withEcc, 0.004, 1e-12);
  near(v.ecc.benefit.withoutEcc, 0.161, 1e-12);
  includes(v.ecc.benefit.provenance, 'test_ident_sticker.py');
  includes(v.ecc.benefit.provenance, 'not this device');
});

T('a device-measured ECC benefit overrides the bench and says so', () => {
  const v = P.peelView({ slots: [], eccBenefit: { withEcc: 0.0041, withoutEcc: 0.1732, shiftPx: 1.2 } });
  eq(v.ecc.benefit.measuredHere, true);
  near(v.ecc.benefit.withoutEcc, 0.1732, 1e-12);
  includes(v.ecc.benefit.provenance, 'this device');
});

T('every engine abstention and every panel abstention has a gloss', () => {
  for (const r of [...P.ENGINE_ABSTENTIONS, P.R.COMPARED, ...Object.values(P.PanelAbstain)]) {
    ok(typeof P.REASON_GLOSS[r] === 'string' && P.REASON_GLOSS[r].length > 3, `no gloss for ${r}`);
  }
  measured.peel_abstentions = P.ENGINE_ABSTENTIONS.length + Object.keys(P.PanelAbstain).length;
});

T('an engine abstention passes through with its own reason', () => {
  const v = P.peelView({ slots: [{ name: 'counter-1' }], peel: verdictMsg({ verdict: P.UNREGISTERABLE, reason: P.R.FOCUS_MISMATCH, sharpness_ratio: 0.41 }) });
  eq(v.verdict, P.UNREGISTERABLE);
  eq(v.reason, P.R.FOCUS_MISMATCH);
  eq(v.reasonKnown, true);
  eq(v.quality.sharpnessRatio, 0.41);
});

T('peelView is idempotent', () => {
  const v1 = P.peelView({ slots: [{ name: 'counter-1' }], peel: verdictMsg() });
  eq(P.peelView(v1), v1);
});

T('the bare wire message {type:"peel", ...} is accepted', () => {
  const v = P.peelView({ name: 'counter-1', verdict: P.GENUINE, ignited_fraction: 0.002, ecc_ok: true, registered: true });
  eq(v.verdict, P.GENUINE);
  eq(v.name, 'counter-1');
  near(v.ignitedFraction, 0.002, 1e-12);
});

// ============================================ 9. PEEL rendering ============
G('9. PEEL rendering — grey is grey, and GENUINE is not green');

const renderP = (state, handlers) => {
  const doc = makeDocument();
  const root = P.renderPeelPanel(state, doc, handlers);
  doc.body.appendChild(root);
  return { doc, root };
};

T('the root carries verdict, reason and enrolment as data attributes', () => {
  const { root } = renderP({ slots: [{ name: 'counter-1' }], peel: verdictMsg() });
  eq(root.id, 'peel-render');
  eq(root.getAttribute('data-gawaah-panel'), 'peel');
  eq(root.getAttribute('data-verdict'), 'GENUINE');
  eq(root.getAttribute('data-accused'), 'false');
  eq(root.getAttribute('data-enrolled'), 'true');
  eq(root.getAttribute('data-contract'), '1');
});

T('the enrolled crop and the fresh crop are rendered side by side', () => {
  const png = 'data:image/png;base64,iVBORw0KGgo=';
  const { root } = renderP({
    slots: [{ name: 'counter-1', shape: [180, 200] }], selectedSlot: 'counter-1',
    enrolledSrc: png, freshSrc: png, freshCropPx: [180, 200],
    peel: verdictMsg(),
  });
  const figs = root.querySelectorAll('.peel-crop');
  eq(figs.length, 2);
  eq(figs[0].getAttribute('data-crop'), 'enrolled');
  eq(figs[1].getAttribute('data-crop'), 'fresh');
  eq(figs[0].querySelector('img').getAttribute('src'), png);
  includes(figs[0].textContent, '200×180 px');
  includes(figs[1].textContent, '200×180 px');
});

T('a network image URL never reaches an img tag', () => {
  const { root } = renderP({ slots: [], enrolledSrc: 'https://cdn.example.com/x.png', freshSrc: 'http://x/y.png' });
  eq(root.querySelectorAll('img').length, 0);
  includes(root.querySelector('.peel-crop-enrolled').textContent, 'nothing enrolled');
});

T('the ignited fraction is a number AND a bar, with the gate marked', () => {
  const { root } = renderP({ slots: [{ name: 'counter-1' }], peel: verdictMsg({ ignited_fraction: 0.0068 }) });
  const val = root.querySelector('.peel-bar-value');
  eq(val.textContent, '0.68 %');
  const fill = root.querySelector('.peel-bar-fill');
  near(parsePct(fill.style.width), 6.8, 1e-9, '0.68 % of a 10 % domain');
  const gate = root.querySelector('.peel-bar-gate');
  near(parsePct(gate.style.left), 30, 1e-9, 'the 3 % gate on a 10 % scale');
  includes(root.querySelector('.peel-bar-gate-label').textContent, 'gate 3 %');
});

T('an off-scale ignited fraction says so instead of overflowing', () => {
  const { root } = renderP({ slots: [{ name: 'counter-1' }], peel: verdictMsg({ verdict: P.TAMPERED, ignited_fraction: 0.2241 }) });
  near(parsePct(root.querySelector('.peel-bar-fill').style.width), 100, 1e-9);
  includes(root.querySelector('.peel-bar-offscale').textContent, '22.41 %');
});

T('GENUINE is slate and says it does not mean paid', () => {
  const { root } = renderP({ slots: [{ name: 'counter-1' }], peel: verdictMsg() });
  const v = root.querySelector('.peel-verdict');
  eq(v.getAttribute('data-tone'), 'slate');
  eq(root.querySelector('.peel-verdict-word').textContent, 'GENUINE');
  includes(root.querySelector('.peel-verdict-not-paid').textContent, 'does not mean paid');
  includes(root.querySelector('.peel-verdict-not-paid').textContent, 'not green');
});

T('TAMPERED is loud red — but only on the one path that earns it', () => {
  const { root } = renderP({ slots: [{ name: 'counter-1' }], peel: verdictMsg({ verdict: P.TAMPERED, ignited_fraction: 0.2241 }) });
  const v = root.querySelector('.peel-verdict');
  eq(v.getAttribute('data-tone'), 'red');
  eq(v.getAttribute('data-accused'), 'true');
  includes(v.style.border, '2px');
  eq(root.querySelector('.peel-verdict-word').textContent, 'TAMPERED');
});

T('THE FAILURE MODE: an unenrolled sticker renders grey, never the word TAMPERED', () => {
  const { root } = renderP({
    slots: [{ name: 'counter-2' }], selectedSlot: 'counter-1',
    peel: verdictMsg({ verdict: P.TAMPERED, registered: false, ignited_fraction: 0.44 }),
  });
  eq(root.getAttribute('data-verdict'), 'UNREGISTERABLE');
  eq(root.getAttribute('data-accused'), 'false');
  eq(root.querySelector('.peel-verdict-word').textContent, 'UNREGISTERABLE');
  eq(root.querySelector('.peel-verdict').getAttribute('data-tone'), 'grey');
  includes(root.textContent, 'this is not an accusation');
  includes(root.textContent, 'NOT_ENROLLED');
  // the refusal is disclosed rather than hidden
  includes(root.querySelector('.peel-verdict-downgrade').textContent, 'the brain reported TAMPERED');
  // and nothing in the verdict block carries the accusation styling
  eq(root.querySelectorAll('.verdict-TAMPERED').length, 0);
  const box = root.querySelector('.peel-verdict');
  for (const e of [box, ...box.descendants()]) {
    ok(!/--red|#e2503f/i.test(e.style.color ?? ''), 'the verdict block was painted with the accusation colour');
    ok(!/226,80,63/.test(e.style.background ?? ''), 'the verdict block was washed with the accusation colour');
  }
});

T('an ignited fraction with no enrolment is WITHHELD, not shown beside a grey verdict', () => {
  const { root } = renderP({
    slots: [{ name: 'counter-2' }], selectedSlot: 'counter-1',
    peel: verdictMsg({ verdict: P.TAMPERED, registered: false, ignited_fraction: 0.4412 }),
  });
  eq(root.querySelector('.peel-bar-value').textContent, '—');
  eq(root.querySelector('.peel-bar-fill'), null, 'a bar was filled with no enrolment to fill it from');
  const w = root.querySelector('.peel-bar-withheld');
  includes(w.textContent, '44.12 %');
  includes(w.textContent, 'nothing it can be a difference from');
  // the number is still disclosed, just not as a verdict
  eq(w.getAttribute('data-reported'), '0.4412');
});

T('the ENROL action exists, carries the slot name and is gated', () => {
  const seen = [];
  const { root } = renderP({
    slots: [], selectedSlot: 'counter-1', freshSrc: 'data:image/png;base64,iVBORw0KGgo=',
    freshCropPx: [180, 200], freshContrast: 30, matLocked: true,
  }, { onEnrol: (n) => seen.push(n) });
  const btn = root.querySelector('#peel-enrol');
  eq(btn.getAttribute('data-can'), 'true');
  eq(btn.textContent, 'ENROL this crop');
  btn.click();
  eq(seen.join(','), 'counter-1');
});

T('the ENROL action is disabled with a named reason when it cannot run', () => {
  const seen = [];
  const { root } = renderP({ slots: [], selectedSlot: 'counter-1', freshCropPx: [40, 40], matLocked: true },
    { onEnrol: (n) => seen.push(n) });
  const btn = root.querySelector('#peel-enrol');
  eq(btn.disabled, true);
  eq(btn.getAttribute('data-can'), 'false');
  btn.click();
  eq(seen.length, 0, 'a blocked enrolment fired anyway');
  includes(root.textContent, 'cannot enrol: enrol_crop_below_64px');
});

T('the enrolled slots are listed, with the count and the selected slot marked', () => {
  const { root } = renderP({
    slots: [{ name: 'counter-1', shape: [180, 200], contrast: 41.2 }, { name: 'counter-2' }],
    selectedSlot: 'counter-2', peel: verdictMsg({ name: 'counter-2' }),
  });
  const list = root.querySelector('.peel-slots');
  eq(list.getAttribute('data-count'), '2');
  const items = root.querySelectorAll('.peel-slot');
  eq(items.length, 2);
  eq(items[0].getAttribute('data-slot'), 'counter-1');
  eq(items[1].getAttribute('data-selected'), 'true');
  includes(items[0].textContent, '200×180 px');
  includes(items[0].textContent, 'contrast 41.20');
  includes(root.querySelector('.peel-registry-label').textContent, 'enrolled slots (2)');
});

T('an empty registry says abstaining is correct behaviour, not a fault', () => {
  const { root } = renderP({ slots: [] });
  includes(root.querySelector('.peel-slots-empty').textContent, 'no slots enrolled');
  includes(root.querySelector('.peel-slots-empty').textContent, 'correct behaviour, not a fault');
});

T('the ECC benefit is displayed as a pair, with its provenance', () => {
  const { root } = renderP({ slots: [{ name: 'counter-1' }], peel: verdictMsg() });
  includes(root.querySelector('.peel-ecc-with').textContent, 'with ECC 0.40 % → GENUINE');
  includes(root.querySelector('.peel-ecc-without').textContent, 'without ECC 16.10 % → TAMPERED');
  includes(root.querySelector('.peel-ecc-provenance').textContent, 'NOT measured on this device');
  includes(root.querySelector('.peel-ecc-benefit-note').textContent, 'false-accusation machine');
  includes(root.querySelector('.peel-ecc-bench').textContent, '100 % accused');
  includes(root.querySelector('.peel-ecc-bench').textContent, '0 % accused');
});

T('the ECC numbers of THIS comparison are shown when the brain reports them', () => {
  const { root } = renderP({ slots: [{ name: 'counter-1' }], peel: verdictMsg({ ecc_cc: 0.94, ecc_shift_px: 0.42, ecc_rotation_deg: 0.013 }) });
  const rows = root.querySelector('.peel-ecc-rows').textContent;
  includes(rows, '0.940 (floor 0.30)');
  includes(rows, '0.42 px');
  includes(rows, '0.013°');
});

T('the quality gates that produce an abstention are on screen', () => {
  const { root } = renderP({ slots: [{ name: 'counter-1' }], peel: verdictMsg({ sharpness_ratio: 0.41, blind_fraction: 0.05, valid_fraction: 0.98 }) });
  const q = root.querySelector('.peel-quality').textContent;
  includes(q, 'sharpness ratio: 0.41');
  includes(q, 'must be ≥ 0.55');
  includes(q, 'blind fraction: 5.00 %');
  includes(q, 'valid overlap: 98.00 %');
});

T('the honest limits are rendered, all five of them', () => {
  const { root } = renderP({ slots: [] });
  const items = root.querySelectorAll('.peel-limit');
  eq(items.length, 5);
  includes(root.textContent, 'A perfect forgery of the enrolled image reads GENUINE');
  includes(root.textContent, 'synthetic');
});

T('the no-QR badge and the warn disclaimer are on every render', () => {
  for (const s of [{ slots: [] }, { slots: [{ name: 'counter-1' }], peel: verdictMsg() }]) {
    const { root } = renderP(s);
    includes(root.querySelector('.gw-badge-noqr').textContent, 'NO QR LIBRARY');
    includes(root.querySelector('.gw-badge-noqr').textContent, 'forgery primitive');
    includes(root.querySelector('.peel-disclaimer').textContent, 'never turns the counter green');
    eq(root.querySelector('.peel-disclaimer').getAttribute('data-invariant'), '2');
  }
});

T('INVARIANT 2 at the DOM level — PEEL never paints the settled colour either', () => {
  const states = [
    { slots: [] },
    { slots: [{ name: 'counter-1' }], peel: verdictMsg() },
    { slots: [{ name: 'counter-1' }], peel: verdictMsg({ verdict: P.TAMPERED, ignited_fraction: 0.3 }) },
    { slots: [{ name: 'counter-2' }], selectedSlot: 'counter-1', peel: verdictMsg({ verdict: P.TAMPERED, registered: false }) },
  ];
  let checked = 0;
  for (const s of states) {
    const { root } = renderP(s);
    for (const v of allStyleValues(root)) {
      ok(!/--green|#3ddc84/i.test(v), `a rendered style is green: ${v}`);
      checked++;
    }
  }
  measured.peel_style_values_checked = checked;
  ok(checked > 150, `only ${checked} style values were checked`);
});

T('PEEL onFrame decides nothing', () => {
  const doc = makeDocument();
  const panel = P.createPeelPanel({ document: doc });
  panel.onState({ slots: [{ name: 'counter-1' }], peel: verdictMsg() });
  const before = panel.view.verdict;
  eq(panel.onFrame({ rectCtx: recorder() }), 0);
  eq(panel.view.verdict, before);
});

// ============================================ 10. cross-panel ==============
G('10. cross-panel invariants');

T('neither view model can ever assert green or money', () => {
  eq(M.mudraView(armState({ target: { amountPaise: 1, minted: true } })).canGoGreen, false);
  eq(M.mudraView({}).mintsMoney, false);
  eq(P.peelView({ slots: [{ name: 'counter-1' }], peel: verdictMsg() }).canGoGreen, false);
  eq(P.peelView({}).touchesMoney, false);
});

T('every panel state renders an explicit "I do not know" when it does not know', () => {
  const idkM = [{}, { ...reading(), matLocked: false }, reading({ state: 'AMBIGUOUS', reason: 'solidity_dead_band' })];
  for (const s of idkM) includes(renderM(s).root.textContent, 'I DO NOT KNOW');
  const idkP = [{ slots: [] }, { slots: [{ name: 'a' }], selectedSlot: 'b', peel: verdictMsg({ registered: false }) }];
  for (const s of idkP) includes(renderP(s).root.textContent, 'I DO NOT KNOW');
});

T('both panels render without throwing on hostile input', () => {
  const hostile = [null, undefined, 0, '', [], { mudra: null }, { peel: 'nope' },
    { mudra: { state: {} } }, { peel: { verdict: 42, ignited_fraction: 'x' } },
    { slots: 'not-a-list' }, { placements: 'not-a-list', payPanelMm: [0, 0, -1, 5] }];
  for (const h of hostile) {
    const a = renderM(h).root;
    const b = renderP(h).root;
    ok(a.textContent.length > 100 && b.textContent.length > 100, `empty render for ${JSON.stringify(h)}`);
    ok(['UNKNOWN', 'NONE'].includes(a.getAttribute('data-state')), 'hostile input produced a decided state');
    eq(b.getAttribute('data-accused'), 'false', 'hostile input produced an accusation');
  }
  measured.hostile_inputs = hostile.length;
});

T('the panels declare only OFF / ABSTAIN / OK — never green', () => {
  const doc = makeDocument();
  const said = [];
  const setStatus = (id, status, why) => said.push([id, status, why]);
  const m = M.createMudraPanel({ document: doc, setStatus });
  m.onState({ matLocked: false, state: 'IDLE' });
  m.onMessage({ type: 'mudra', state: 'AMBIGUOUS', solidity: 0.86, defects: 1, area_mm2: 12000, reason: 'mid_solidity_too_few_defects' });
  const p = P.createPeelPanel({ document: doc, setStatus });
  p.onMessage({ type: 'peel', name: 'c1', verdict: 'GENUINE', registered: true, ecc_ok: true, ignited_fraction: 0.001 });
  ok(said.length >= 3, `only ${said.length} declarations`);
  for (const [id, status, why] of said) {
    ok(['mudra', 'peel'].includes(id), `declared for ${id}`);
    ok(['OFF', 'ABSTAIN', 'OK'].includes(status), `illegal status ${status}`);
    if (status === 'ABSTAIN') ok(typeof why === 'string' && why.length > 0, 'ABSTAIN with no named cause');
  }
  eq(M.mudraPanelStatus({}).status, 'ABSTAIN');
  eq(M.mudraPanelStatus({}).why, M.Abstain.NO_READING);
  eq(M.mudraPanelStatus(armState()).status, 'OK');
  eq(P.peelPanelStatus({}).status, 'ABSTAIN');
  eq(P.peelPanelStatus({ slots: [{ name: 'counter-1' }], peel: verdictMsg() }).status, 'OK');
  eq(P.peelPanelStatus({ slots: [{ name: 'counter-1' }], peel: verdictMsg({ verdict: 'TAMPERED', ignited_fraction: 0.3 }) }).why,
    'sticker_tampered_see_panel');
  // a slot the registry does not list stays an abstention, not an OK
  eq(P.peelPanelStatus({ slots: [{ name: 'other' }], peel: verdictMsg() }).status, 'ABSTAIN');
});

T('the DOM shim itself works (the tests are testing something)', () => {
  const doc = makeDocument();
  const a = doc.createElement('div');
  a.setAttribute('data-x', '1');
  a.className = 'p q';
  const b = doc.createElement('span');
  b.textContent = 'hello';
  a.appendChild(b);
  doc.body.appendChild(a);
  eq(a.textContent, 'hello');
  eq(a.dataset.x, '1');
  eq(doc.querySelector('.p span'), b);
  eq(doc.querySelectorAll('[data-x="1"]').length, 1);
  eq(doc.querySelectorAll('[data-x="2"]').length, 0);
  a.replaceChildren();
  eq(a.textContent, '');
  eq(a.classList.contains('q'), true);
});

// ============================================ 11. cross-file seam ==========
G('11. the seam against app.js and index.html, as they exist right now');

// app.js and index.html belong to other agents and may be mid-edit. So: if the
// seam is not there yet, that is REPORTED, not failed. If it IS there, the ids
// and constants this module hard-codes must match it exactly — a panel that
// registers under the wrong id is a panel that never renders.
const APP_SRC = (() => { try { return readFileSync(join(HERE, '..', 'app.js'), 'utf8'); } catch { return null; } })();
const HTML_SRC = (() => { try { return readFileSync(join(HERE, '..', 'index.html'), 'utf8'); } catch { return null; } })();

T('the registry ids match app.js PANEL_IDS', () => {
  if (APP_SRC === null) { measured.app_seam = 'app.js unreadable — seam unchecked'; return; }
  const m = /export const PANEL_IDS = Object\.freeze\(\[([^\]]*)\]\)/.exec(APP_SRC);
  if (!m) { measured.app_seam = 'no PANEL_IDS in app.js yet'; return; }
  const ids = m[1].split(',').map((x) => x.trim().replace(/^['"]|['"]$/g, '')).filter(Boolean);
  measured.app_panel_ids = ids.join('|');
  ok(ids.includes(M.MUDRA_ID), `app.js PANEL_IDS lacks ${M.MUDRA_ID}`);
  ok(ids.includes(P.PEEL_ID), `app.js PANEL_IDS lacks ${P.PEEL_ID}`);
});

T('the frame contract constant matches app.js RETAIN_RECTIFIED', () => {
  if (APP_SRC === null) return;
  const m = /export const RETAIN_RECTIFIED = '([^']+)'/.exec(APP_SRC);
  if (!m) { measured.app_crop_kind = 'not declared in app.js yet'; return; }
  measured.app_crop_kind = m[1];
  eq(M.RECTIFIED_CROP_KIND, m[1], 'the panel would refuse every legal frame');
  eq(P.RECTIFIED_CROP_KIND, m[1]);
});

T('the statuses this panel declares are the ones app.js accepts', () => {
  if (APP_SRC === null) return;
  const m = /export const PanelStatus = Object\.freeze\(\{([^}]*)\}\)/.exec(APP_SRC);
  if (!m) { measured.app_statuses = 'not declared in app.js yet'; return; }
  const vals = [...m[1].matchAll(/'([A-Z]+)'/g)].map((x) => x[1]);
  measured.app_statuses = vals.join('|');
  for (const v of Object.values(M.PanelStatus)) ok(vals.includes(v), `app.js would refuse ${v}`);
  ok(!vals.includes('GREEN'), 'app.js grew a GREEN status');
  ok(!Object.values(M.PanelStatus).includes('GREEN'), 'this panel grew a GREEN status');
});

T('the shell fill points this panel mounts into exist in index.html', () => {
  if (HTML_SRC === null) { measured.shell_seam = 'index.html unreadable'; return; }
  const found = [];
  for (const id of [M.MUDRA_PANEL_ID, M.MUDRA_BODY_ID, P.PEEL_PANEL_ID, P.PEEL_BODY_ID]) {
    if (HTML_SRC.includes(`id="${id}"`)) found.push(id);
  }
  measured.shell_mount_points = found.join('|') || 'none yet';
  if (found.length === 0) return;    // shell not built yet: reported, not failed
  ok(found.includes(M.MUDRA_BODY_ID) || found.includes(M.MUDRA_PANEL_ID), 'no MUDRA mount point');
  ok(found.includes(P.PEEL_BODY_ID) || found.includes(P.PEEL_PANEL_ID), 'no PEEL mount point');
  // our own root ids must NOT collide with anything the shell already owns
  ok(!HTML_SRC.includes(`id="${M.MUDRA_ROOT_ID}"`), 'the shell already owns mudra-render');
  ok(!HTML_SRC.includes(`id="${P.PEEL_ROOT_ID}"`), 'the shell already owns peel-render');
});

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
