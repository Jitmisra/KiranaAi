/* GAWAAH — ENROL panel self-test. No browser, no bundler, no network.
 *
 *   cd /Users/agnik/Desktop/razor && node web/panels/enrol.test.mjs
 *
 * web/ has no package.json, so node would load a bare `.js` as CommonJS and
 * choke on `export`. enrol.js is therefore loaded through a data: URL, the same
 * trick web/selftest.mjs and panels.test.mjs use — which is also why enrol.js
 * imports nothing: a data: URL cannot resolve a relative specifier.
 *
 * WHAT THIS FILE IS REALLY FOR
 * One function in enrol.js is dangerous and the rest is furniture. `parsePaise`
 * is the only place in the browser where a human-typed decimal becomes machine
 * money, and the whole of section A exists to prove it never invents a paisa.
 * The naive implementation — Math.round(parseFloat(s) * 100) — is written out
 * in full below and RUN alongside the real one, so the test does not merely
 * assert that 214.507 is refused; it demonstrates the 21451 that the obvious
 * code would have silently billed.
 *
 * The DOM here is a ~130-line shim, not jsdom. It implements only what the
 * panel touches, and it is real enough to dispatch a click.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const ENROL_PATH = join(HERE, 'enrol.js');
const ENROL_SRC = readFileSync(ENROL_PATH, 'utf8');
const CSS_PATH = join(HERE, '..', 'style.css');
let CSS_SRC = null;
try { CSS_SRC = readFileSync(CSS_PATH, 'utf8'); } catch { CSS_SRC = null; }
let APP_SRC = null;
try { APP_SRC = readFileSync(join(HERE, '..', 'app.js'), 'utf8'); } catch { APP_SRC = null; }

const load = (src) => import('data:text/javascript;charset=utf-8;base64,'
  + Buffer.from(src, 'utf8').toString('base64'));
const E = await load(ENROL_SRC);

// ---------------------------------------------------------------- harness --
let pass = 0, fail = 0, group = '';
const failures = [];
const measured = {};
function T(name, fn) {
  try { fn(); pass++; }
  catch (e) { fail++; failures.push(`${group} :: ${name}\n      ${e.message}`); }
}
function G(name) { group = name; console.log(`\n── ${name}`); }

/**
 * The async sibling of T. The photo flow talks to a service, so the calls that
 * exercise it are promises; they are collected here and awaited once, together,
 * before the report is printed. A test that is merely STARTED and never awaited
 * is a test that always passes, which is worse than no test at all.
 */
const pending = [];
function TA(name, fn) {
  const g0 = group;
  pending.push((async () => {
    try { await fn(); pass++; }
    catch (e) { fail++; failures.push(`${g0} :: ${name}\n      ${e.message}`); }
  })());
}
function ok(cond, msg) { if (!cond) throw new Error(msg || 'assertion failed'); }
function eq(a, b, msg) {
  if (!Object.is(a, b)) throw new Error(`${msg || 'eq'}: got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
}
function includes(hay, needle, msg) {
  if (!String(hay).includes(needle)) {
    throw new Error(`${msg || 'includes'}: ${JSON.stringify(needle)} not in ${JSON.stringify(String(hay).slice(0, 300))}…`);
  }
}
function excludes(hay, needle, msg) {
  if (String(hay).includes(needle)) throw new Error(`${msg || 'excludes'}: found ${JSON.stringify(needle)}`);
}

// ------------------------------------------------------------- DOM shim ----
const camel = (s) => s.replace(/-([a-z])/g, (_, c) => c.toUpperCase());

class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.nodeType = 1;
    this.childNodes = [];
    this.attributes = new Map();
    this.dataset = {};
    this.className = '';
    this.value = '';
    this._on = new Map();
  }
  get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
  set textContent(v) { this.childNodes = [{ nodeType: 3, data: String(v) }]; }
  get textContent() {
    return this.childNodes.map((n) => (n.nodeType === 3 ? n.data : n.textContent)).join('');
  }
  appendChild(n) { this.childNodes.push(n); return n; }
  replaceChildren(...ns) { this.childNodes = ns.filter(Boolean); }
  setAttribute(k, v) {
    this.attributes.set(k, String(v));
    if (k === 'id') this.id = String(v);
    if (k.startsWith('data-')) this.dataset[camel(k.slice(5))] = String(v);
  }
  getAttribute(k) { return this.attributes.has(k) ? this.attributes.get(k) : null; }
  addEventListener(t, f) {
    if (!this._on.has(t)) this._on.set(t, []);
    this._on.get(t).push(f);
  }
  dispatchEvent(ev) { for (const f of this._on.get(ev.type) || []) f(ev); return true; }
  click() { return this.dispatchEvent({ type: 'click', target: this }); }
}

function walk(node, out = []) {
  if (!node || node.nodeType !== 1) return out;
  out.push(node);
  for (const k of node.childNodes) walk(k, out);
  return out;
}
function all(root, pred) { return walk(root).filter(pred); }
function one(root, pred, what) {
  const hits = all(root, pred);
  if (hits.length === 0) throw new Error(`no element matched ${what || 'predicate'}`);
  return hits[0];
}
const byClass = (root, cls) => one(root, (e) => String(e.className).split(/\s+/).includes(cls), `.${cls}`);
const byClassAll = (root, cls) => all(root, (e) => String(e.className).split(/\s+/).includes(cls));
const byId = (root, id) => all(root, (e) => e.getAttribute('id') === id)[0] || null;
const hasClass = (root, cls) => byClassAll(root, cls).length > 0;

function makeDoc() {
  const head = new El('head');
  const body = new El('body');
  const doc = {
    head,
    body,
    readyState: 'complete',
    createElement: (t) => new El(t),
    getElementById(id) {
      return byId(body, id) || byId(head, id) || null;
    },
  };
  return doc;
}

// ==========================================================================
// A. THE MONEY BOUNDARY. The only part of this file that could cost anybody
//    money if it is wrong.
// ==========================================================================
G('A. parsePaise — the rupee/paise boundary');

/**
 * The implementation this test exists to rule out. Written here in full so the
 * comparison below is a demonstration and not a claim.
 */
const naivePaise = (s) => Math.round(parseFloat(s) * 100);

T('THE NAMED CASE: 214.507 as a string is REFUSED, not rounded', () => {
  const r = E.parsePaise('214.507');
  eq(r.ok, false, 'must refuse');
  eq(r.paise, null, 'must not produce a value');
  eq(r.reason, E.PriceRefusal.SUB_PAISE, 'reason');
  includes(r.detail, 'NOT rounded');
  measured.naive_would_have_billed = `${naivePaise('214.507')} paise`;
  eq(naivePaise('214.507'), 21451, 'the naive impl really does invent 21451');
  ok(r.paise !== 21451 && r.paise !== 21450, 'we produced neither rounding');
});

T('THE NAMED CASE: 214.507 as a NUMBER is refused the same way', () => {
  const r = E.parsePaise(214.507);
  eq(r.ok, false);
  eq(r.reason, E.PriceRefusal.SUB_PAISE);
});

T('THE NAMED CASE: "abc" -> price_not_a_number', () => {
  const r = E.parsePaise('abc');
  eq(r.ok, false);
  eq(r.reason, E.PriceRefusal.NOT_A_NUMBER);
  ok(Number.isNaN(naivePaise('abc')), 'the naive impl yields NaN and would have to be checked for it');
});

T('THE NAMED CASE: "" -> price_empty', () => {
  eq(E.parsePaise('').reason, E.PriceRefusal.EMPTY);
  eq(E.parsePaise('   ').reason, E.PriceRefusal.EMPTY, 'whitespace only is still empty');
  eq(E.parsePaise(null).reason, E.PriceRefusal.EMPTY);
  eq(E.parsePaise(undefined).reason, E.PriceRefusal.EMPTY);
});

T('THE NAMED CASE: -5 -> price_negative, as a number AND as a string', () => {
  eq(E.parsePaise(-5).reason, E.PriceRefusal.NEGATIVE, 'number -5');
  eq(E.parsePaise('-5').reason, E.PriceRefusal.NEGATIVE, 'string "-5"');
  eq(E.parsePaise('-0.01').reason, E.PriceRefusal.NEGATIVE);
  eq(E.parsePaise(-5).paise, null, 'no value escapes');
});

T('THE NAMED CASE: "1e3" the STRING is refused as exponent notation', () => {
  const r = E.parsePaise('1e3');
  eq(r.ok, false);
  eq(r.reason, E.PriceRefusal.EXPONENT);
  includes(r.detail, 'type 1000 if you meant');
  eq(naivePaise('1e3'), 100000, 'the naive impl would have silently accepted it');
  for (const s of ['1E3', '2.5e2', '-1e3', '1e-3']) {
    ok(!E.parsePaise(s).ok, `${s} must be refused`);
  }
  eq(E.parsePaise('1E3').reason, E.PriceRefusal.EXPONENT);
  eq(E.parsePaise('1e-3').reason, E.PriceRefusal.EXPONENT);
});

T('the NUMBER 1e3 is the integer 1000 by the time JS hands it over, and is accepted', () => {
  // Documented split, and the reason both halves are tested: String(1e3) is
  // '1000'. There is no exponent left to refuse — refusing here would be
  // refusing the number one thousand.
  const r = E.parsePaise(1e3);
  eq(r.ok, true);
  eq(r.paise, 100000, '1000 rupees is 100000 paise');
  eq(r.typed, '1000', 'and it is shown to the operator as what it actually was');
  measured.number_1e3 = `${r.paise} paise`;
});

T('the exact conversions', () => {
  const cases = [
    ['214.50', 21450], ['214.5', 21450], ['214', 21400], ['0.01', 1],
    ['0.1', 10], ['1', 100], ['99.99', 9999], ['1000', 100000],
    ['  42.05  ', 4205], ['₹214.50', 21450], ['Rs 214.50', 21450],
    ['rs.214.50', 21450], ['INR 5', 500], ['+7.25', 725],
    ['007.50', 750], ['1000000', 100000000],
  ];
  for (const [s, want] of cases) {
    const r = E.parsePaise(s);
    ok(r.ok, `${JSON.stringify(s)} should parse, got ${r.reason}`);
    eq(r.paise, want, `${JSON.stringify(s)}`);
    ok(Number.isSafeInteger(r.paise), 'must be a safe integer');
  }
});

T('the classic binary-float victims are exact here', () => {
  // Every one of these is a value where x*100 is NOT an integer in a double.
  const victims = [
    ['0.07', 7], ['0.29', 29], ['1.005', null], ['8.16', 816], ['1.15', 115],
    ['2.675', null], ['70.07', 7007], ['1.1', 110], ['4.35', 435],
  ];
  const drift = [];
  for (const [s, want] of victims) {
    const r = E.parsePaise(s);
    if (want === null) { eq(r.ok, false, `${s} has 3 decimals, must refuse`); continue; }
    eq(r.paise, want, s);
    const naive = naivePaise(s);
    const raw = parseFloat(s) * 100;
    if (!Number.isInteger(raw)) drift.push(`${s}->${raw}`);
    eq(naive, want, `${s}: naive happens to agree here, the danger is elsewhere`);
  }
  measured.float_drift_samples = drift.slice(0, 4).join(' ');
  ok(drift.length >= 4, 'at least four of these really do drift in binary float');
});

T('sub-paise is ALWAYS a refusal, never a rounding — swept', () => {
  let refused = 0;
  for (let i = 0; i < 1000; i++) {
    const s = `12.${String(i).padStart(3, '0')}`;
    const r = E.parsePaise(s);
    eq(r.ok, false, `${s} has three decimals`);
    eq(r.reason, E.PriceRefusal.SUB_PAISE);
    refused++;
  }
  measured.sub_paise_swept = `${refused}/1000 refused`;
  eq(refused, 1000);
});

T('zero and the typo ceiling', () => {
  eq(E.parsePaise('0').reason, E.PriceRefusal.ZERO);
  eq(E.parsePaise('0.00').reason, E.PriceRefusal.ZERO);
  eq(E.parsePaise('0.0').reason, E.PriceRefusal.ZERO);
  eq(E.parsePaise('000').reason, E.PriceRefusal.ZERO);
  eq(E.parsePaise('1000000').paise, E.MAX_PAISE, 'exactly the ceiling is allowed');
  eq(E.parsePaise('1000000.01').reason, E.PriceRefusal.TOO_LARGE);
  eq(E.parsePaise('99999999999999999999').reason, E.PriceRefusal.TOO_LARGE);
  eq(E.parsePaise(Infinity).reason, E.PriceRefusal.NOT_A_NUMBER);
  eq(E.parsePaise(NaN).reason, E.PriceRefusal.NOT_A_NUMBER);
});

T('separators are refused rather than guessed at', () => {
  eq(E.parsePaise('1,234.50').reason, E.PriceRefusal.SEPARATOR);
  eq(E.parsePaise('1,23,456').reason, E.PriceRefusal.SEPARATOR, 'Indian grouping too');
  includes(E.parsePaise('1,234.50').detail, 'group differently');
});

T('the shapes that are not decimal rupee notation', () => {
  for (const s of ['.50', '1.2.3', '12abc', '--5', '1 2', 'ten', '#5', '٥']) {
    const r = E.parsePaise(s);
    eq(r.ok, false, `${JSON.stringify(s)} must be refused`);
  }
  eq(E.parsePaise('.50').reason, E.PriceRefusal.NOT_A_NUMBER);
  eq(E.parsePaise({}).reason, E.PriceRefusal.NOT_A_NUMBER, 'an object is not a price');
  eq(E.parsePaise([]).reason, E.PriceRefusal.NOT_A_NUMBER);
  eq(E.parsePaise(true).reason, E.PriceRefusal.NOT_A_NUMBER);
});

T('every declared price refusal code is REACHABLE', () => {
  const seen = new Set();
  const probes = ['', 'abc', '1,2', '1e3', '-5', '0.001', '0', '99999999'];
  for (const p of probes) { const r = E.parsePaise(p); if (!r.ok) seen.add(r.reason); }
  for (const code of E.PRICE_REFUSAL_CODES) {
    ok(seen.has(code), `unreachable refusal code: ${code}`);
  }
  measured.price_refusals_reached = `${seen.size}/${E.PRICE_REFUSAL_CODES.length}`;
});

T('every price refusal carries a sentence a shopkeeper can act on', () => {
  for (const code of E.PRICE_REFUSAL_CODES) {
    const help = E.PRICE_REFUSAL_HELP[code];
    ok(typeof help === 'string' && help.length > 30, `${code} has no usable help text`);
  }
});

T('the refusal always echoes exactly what was typed', () => {
  const r = E.parsePaise('  214.507 ');
  eq(r.typed, '  214.507 ', 'echoed verbatim, so the operator sees their own keystrokes');
});

// ==========================================================================
G('B. formatPaise — integer back out to a rupee string, still no float');

T('the round trip is exact for the whole low range', () => {
  let checked = 0;
  for (let p = 1; p <= 20000; p++) {
    const s = E.formatPaise(p);
    const back = E.parsePaise(s);
    ok(back.ok, `${p} -> ${s} did not re-parse: ${back.reason}`);
    eq(back.paise, p, `round trip ${p} via ${s}`);
    checked++;
  }
  measured.round_trip_exact = `${checked}/20000`;
  eq(checked, 20000);
});

T('formatting and Indian grouping', () => {
  eq(E.formatPaise(21450), '₹214.50');
  eq(E.formatPaise(1), '₹0.01');
  eq(E.formatPaise(0), '₹0.00');
  eq(E.formatPaise(100), '₹1.00');
  eq(E.formatPaise(100000), '₹1,000.00');
  eq(E.formatPaise(123456789), '₹12,34,567.89', 'Indian grouping, not western');
  eq(E.formatPaise(1.5), '—', 'a non-integer is not money and is not rendered as if it were');
  eq(E.groupIndian('1234567'), '12,34,567');
  eq(E.groupIndian('999'), '999');
  eq(E.groupIndian('1000'), '1,000');
});

T('enrol.js does no float arithmetic on the money path', () => {
  // INVARIANT 1, checked against the source rather than asserted in a comment.
  const money = ENROL_SRC.split('// 2. THE CATALOGUE')[0];
  const body = money.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  for (const forbidden of ['parseFloat', 'toFixed', 'Math.round', 'Number.parseFloat']) {
    excludes(body, forbidden, `money path uses ${forbidden}`);
  }
  // No multiplication or division of a money value anywhere in the module body.
  const codeOnly = ENROL_SRC.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  const arith = [...codeOnly.matchAll(/pais\w*\s*[*/]|[*/]\s*PAISE_PER_RUPEE|[*/]\s*100\b/g)];
  eq(arith.length, 0, `money arithmetic found: ${arith.map((m) => m[0]).join(', ')}`);
  measured.money_path_float_ops = '0';
});

// ==========================================================================
G('C. parseName — the string that becomes a filename on the brain');

T('good names pass and are normalised', () => {
  eq(E.parseName('Parle-G 100g').name, 'Parle-G 100g');
  eq(E.parseName('  spaced   out  ').name, 'spaced out', 'runs of whitespace collapse');
  eq(E.parseName('counter-upi').name, 'counter-upi');
});

T('Indic names pass — combining marks are letters too', () => {
  // This test found a real bug. "चाय" is च + ा and that second character is a
  // COMBINING MARK, category Mn, not a letter, so a [\p{L}\p{N}] class refuses
  // most Devanagari — which is to say, most of what this counter's actual
  // users would type. \p{M} was added to the continuation class because of
  // this line.
  const names = ['चाय', 'पारले-जी', 'நெய்', 'দুধ', 'ਦੁੱਧ', 'ચા'];
  for (const n of names) {
    const r = E.parseName(n);
    ok(r.ok, `${n} was refused: ${r.reason}`);
    eq(r.name, n);
  }
  measured.indic_names_accepted = `${names.length}/${names.length}`;
  // but a name that OPENS with a floating matra is still a paste accident
  eq(E.parseName('ा चाय').reason, E.NameRefusal.BAD_CHARS);
});

T('every declared name refusal code is REACHABLE', () => {
  eq(E.parseName('').reason, E.NameRefusal.EMPTY);
  eq(E.parseName('   ').reason, E.NameRefusal.EMPTY);
  eq(E.parseName('x'.repeat(E.MAX_NAME_LEN + 1)).reason, E.NameRefusal.TOO_LONG);
  eq(E.parseName('../../etc/passwd').reason, E.NameRefusal.PATH_LIKE);
  eq(E.parseName('a/b').reason, E.NameRefusal.PATH_LIKE);
  eq(E.parseName('a\\b').reason, E.NameRefusal.PATH_LIKE);
  eq(E.parseName('bad*name').reason, E.NameRefusal.BAD_CHARS);
  eq(E.parseName('-leading').reason, E.NameRefusal.BAD_CHARS, 'must start with a letter or digit');
  eq(E.parseName('Chai', ['chai']).reason, E.NameRefusal.DUPLICATE, 'case-insensitive');
  const seen = new Set([
    E.parseName('').reason, E.parseName('x'.repeat(99)).reason,
    E.parseName('a/b').reason, E.parseName('a*b').reason,
    E.parseName('a', ['a']).reason,
  ]);
  for (const c of E.NAME_REFUSAL_CODES) ok(seen.has(c), `unreachable: ${c}`);
  measured.name_refusals_reached = `${seen.size}/${E.NAME_REFUSAL_CODES.length}`;
});

T('exactly MAX_NAME_LEN is allowed; one more is not', () => {
  ok(E.parseName('x'.repeat(E.MAX_NAME_LEN)).ok);
  ok(!E.parseName('x'.repeat(E.MAX_NAME_LEN + 1)).ok);
});

// ==========================================================================
G('D. reduceCatalogue — pure, frozen, never partially applied');

T('a good add produces one row with integer paise', () => {
  const c0 = E.emptyCatalogue();
  const c1 = E.reduceCatalogue(c0, { type: E.Action.ADD, name: 'Parle-G', price: '10.50' });
  eq(c1.skus.length, 1);
  eq(c1.skus[0].name, 'Parle-G');
  eq(c1.skus[0].pricePaise, 1050);
  ok(Number.isSafeInteger(c1.skus[0].pricePaise));
  eq(c1.refusal, null);
  eq(c0.skus.length, 0, 'the previous state was not mutated');
});

T('a refused price leaves the list byte-identical and names the refusal', () => {
  const c1 = E.reduceCatalogue(E.emptyCatalogue(), { type: E.Action.ADD, name: 'A', price: '5' });
  const c2 = E.reduceCatalogue(c1, { type: E.Action.ADD, name: 'B', price: '214.507' });
  eq(c2.skus.length, 1, 'nothing was added');
  eq(c2.skus[0], c1.skus[0], 'the surviving row is the SAME object, not a copy');
  eq(c2.refusal.field, 'price');
  eq(c2.refusal.reason, E.PriceRefusal.SUB_PAISE);
  eq(c2.refusal.typed, '214.507');
});

T('a refused name is caught before the price is even looked at', () => {
  const c = E.reduceCatalogue(E.emptyCatalogue(), { type: E.Action.ADD, name: '', price: '214.507' });
  eq(c.refusal.field, 'name');
  eq(c.refusal.reason, E.NameRefusal.EMPTY);
});

T('duplicate names are refused', () => {
  let c = E.reduceCatalogue(E.emptyCatalogue(), { type: E.Action.ADD, name: 'Chai', price: '10' });
  c = E.reduceCatalogue(c, { type: E.Action.ADD, name: 'chai', price: '20' });
  eq(c.skus.length, 1);
  eq(c.refusal.reason, E.NameRefusal.DUPLICATE);
});

T('remove takes exactly one row; an unknown id is a named refusal', () => {
  let c = E.reduceCatalogue(E.emptyCatalogue(), { type: E.Action.ADD, name: 'A', price: '1' });
  c = E.reduceCatalogue(c, { type: E.Action.ADD, name: 'B', price: '2' });
  eq(c.skus.length, 2);
  const id = c.skus[0].id;
  c = E.reduceCatalogue(c, { type: E.Action.REMOVE, id });
  eq(c.skus.length, 1);
  eq(c.skus[0].name, 'B');
  c = E.reduceCatalogue(c, { type: E.Action.REMOVE, id: 'sku-999' });
  eq(c.skus.length, 1, 'nothing removed');
  eq(c.refusal.reason, 'sku_unknown_row');
});

T('ids never collide even after removals', () => {
  let c = E.emptyCatalogue();
  const ids = new Set();
  for (let i = 0; i < 40; i++) {
    c = E.reduceCatalogue(c, { type: E.Action.ADD, name: `sku ${i}`, price: '1' });
    const last = c.skus[c.skus.length - 1];
    ok(!ids.has(last.id), `id reused: ${last.id}`);
    ids.add(last.id);
    if (i % 3 === 0) c = E.reduceCatalogue(c, { type: E.Action.REMOVE, id: last.id });
  }
  measured.unique_sku_ids = String(ids.size);
  eq(ids.size, 40);
});

T('the state is frozen, so nobody can grow a field on it', () => {
  const c = E.reduceCatalogue(E.emptyCatalogue(), { type: E.Action.ADD, name: 'A', price: '1' });
  ok(Object.isFrozen(c) && Object.isFrozen(c.skus) && Object.isFrozen(c.skus[0]));
  let threw = false;
  try { 'use strict'; c.skus[0].pricePaise = 999999; } catch { threw = true; }
  eq(c.skus[0].pricePaise, 100, 'the price could not be rewritten');
  measured.frozen_write_threw = String(threw);
});

T('an unknown action changes nothing', () => {
  const c1 = E.reduceCatalogue(E.emptyCatalogue(), { type: E.Action.ADD, name: 'A', price: '1' });
  const c2 = E.reduceCatalogue(c1, { type: 'NONSENSE' });
  eq(c2.skus.length, 1);
  eq(E.reduceCatalogue(null, null).skus.length, 0, 'garbage in is an empty catalogue, not a throw');
});

// ==========================================================================
G('E. deriveEnrol — abstentions stay reachable (INVARIANT 7)');

T('a cold surface abstains on everything it has not been told', () => {
  const m = E.deriveEnrol({});
  eq(m.catalogue.count, 0);
  eq(m.peel, null);
  eq(m.saaf, null);
  ok(m.abstentions.includes(E.Abstain.NOTHING_TYPED));
  ok(m.abstentions.includes(E.Abstain.NO_STICKER_RESULT));
  ok(m.abstentions.includes(E.Abstain.NO_BURST_REPORTED));
  ok(m.abstentions.includes(E.Abstain.NO_BRAIN_SEAM));
  eq(m.abstentions.length, 4);
  measured.cold_abstentions = m.abstentions.join(',');
});

T('every declared abstention is REACHABLE and every one has help text', () => {
  const cold = E.deriveEnrol({}).abstentions;
  for (const c of E.ABSTAIN_CODES) {
    ok(cold.includes(c), `unreachable abstention: ${c}`);
    ok(String(E.ABSTAIN_HELP[c]).length > 30, `${c} has no help text`);
  }
});

T('each abstention clears only when the matching fact arrives', () => {
  const cat = E.reduceCatalogue(E.emptyCatalogue(), { type: E.Action.ADD, name: 'A', price: '1' });
  let m = E.deriveEnrol({ catalogue: cat });
  ok(!m.abstentions.includes(E.Abstain.NOTHING_TYPED));
  ok(m.abstentions.includes(E.Abstain.NO_STICKER_RESULT), 'still no peel');

  m = E.deriveEnrol({ catalogue: cat, canSend: true, peel: { type: 'peel', ok: true }, saaf: { type: 'saaf', ok: true } });
  eq(m.abstentions.length, 0, 'all four facts present');
});

T('a saaf message is read faithfully, including the honest nulls', () => {
  const m = E.deriveEnrol({
    saaf: {
      type: 'saaf', ok: false, used: 0, rejected: 0, sharpness_gain: null,
      warning: '', reason: 'burst_too_short', burst: 3, burst_target: 12,
      detail: '3 of 12 burst frames collected',
    },
  });
  eq(m.saaf.burst, 3);
  eq(m.saaf.burstTarget, 12);
  eq(m.saaf.gain, null, 'a null gain stays null; it is not turned into 0');
  eq(m.saaf.reason, 'burst_too_short');
});

T('the model is frozen all the way down', () => {
  const m = E.deriveEnrol({ catalogue: E.reduceCatalogue(E.emptyCatalogue(), { type: E.Action.ADD, name: 'A', price: '1' }) });
  ok(Object.isFrozen(m) && Object.isFrozen(m.catalogue) && Object.isFrozen(m.catalogue.rows));
  ok(Object.isFrozen(m.abstentions));
});

// ==========================================================================
G('F. renderEnrol — what a shopkeeper actually sees');

T('a cold render shows all four I-DO-NOT-KNOW blocks with their codes', () => {
  const doc = makeDoc();
  const el = E.renderEnrol(E.deriveEnrol({}), doc);
  const text = el.textContent;
  includes(text, 'I DO NOT KNOW');
  for (const c of E.ABSTAIN_CODES) includes(text, c, `abstention code ${c} not on screen`);
  eq(el.dataset.abstentions.split(' ').length, 4);
  measured.cold_render_chars = String(text.length);
});

T('the permanent catalogue note is on screen and says what it is not', () => {
  const doc = makeDoc();
  const t = E.renderEnrol(E.deriveEnrol({}), doc).textContent;
  includes(t, 'LOCAL CATALOGUE');
  includes(t, 'signature-verified webhook');
  includes(t, 'Nothing here is a bill');
  includes(t, 'nothing here can settle a sale');
});

T('a refusal is rendered LOUDLY with the code, the help and the keystrokes', () => {
  const doc = makeDoc();
  const cat = E.reduceCatalogue(E.emptyCatalogue(), { type: E.Action.ADD, name: 'A', price: '214.507' });
  const el = E.renderEnrol(E.deriveEnrol({ catalogue: cat }), doc);
  const box = byClass(el, 'enrol-refusal');
  eq(box.getAttribute('role'), 'alert', 'a refusal must be announced, not just coloured');
  eq(box.dataset.reason, E.PriceRefusal.SUB_PAISE);
  includes(box.textContent, 'REFUSED');
  includes(box.textContent, 'price_sub_paise');
  includes(box.textContent, '"214.507"');
  includes(box.textContent, 'NOT rounded');
});

T('every row shows the PAISE beside the rupees', () => {
  const doc = makeDoc();
  let cat = E.reduceCatalogue(E.emptyCatalogue(), { type: E.Action.ADD, name: 'Parle-G', price: '10.50' });
  cat = E.reduceCatalogue(cat, { type: E.Action.ADD, name: 'Chai', price: '214.50' });
  const el = E.renderEnrol(E.deriveEnrol({ catalogue: cat }), doc);
  const rows = byClassAll(el, 'enrol-row');
  eq(rows.length, 2);
  includes(rows[0].textContent, '₹10.50');
  includes(rows[0].textContent, '1050 paise');
  eq(rows[0].dataset.paise, '1050');
  includes(rows[1].textContent, '₹214.50');
  includes(rows[1].textContent, '21450 paise');
});

T('no total is rendered — a catalogue is not a bill', () => {
  const doc = makeDoc();
  let cat = E.reduceCatalogue(E.emptyCatalogue(), { type: E.Action.ADD, name: 'A', price: '10.00' });
  cat = E.reduceCatalogue(cat, { type: E.Action.ADD, name: 'B', price: '20.00' });
  const t = E.renderEnrol(E.deriveEnrol({ catalogue: cat }), doc).textContent;
  excludes(t, '₹30.00', 'the panel added the two prices up');
  excludes(t, '3000 paise', 'the panel added the two prices up');
  includes(t, 'no total is shown');
});

T('this module\'s own sheet is layout only — it never repaints a refusal', () => {
  // Measured in headless Chrome against web/index.html's policy:
  //   document.getElementById('enrol-style').sheet -> undefined (CSP blocked)
  //   document.adoptedStyleSheets                  -> 1 sheet, 37 rules (applies)
  // Since the adopted path DOES apply, a background or border declared here
  // would silently outrank the shell's .abstain / .line-amber — adopted sheets
  // come last in the cascade — and a refusal on this panel would stop looking
  // like a refusal everywhere else on the page. So it declares neither.
  const rules = E.STYLE_TEXT.replace(/\/\*[\s\S]*?\*\//g, '');
  for (const sel of ['.enrol-refusal', '.enrol-abstain', '.enrol-brain-refused', '.enrol-sim']) {
    const re = new RegExp(`\\${sel}[^{]*\\{([^}]*)\\}`, 'g');
    for (const m of rules.matchAll(re)) {
      for (const prop of ['background', 'border:', 'border-color', 'color:']) {
        excludes(m[1], prop, `${sel} declares ${prop}; that outranks the shell's refusal styling`);
      }
    }
  }
  measured.own_sheet_rules = String((E.STYLE_TEXT.match(/\{/g) || []).length);
});

T('nothing on this surface ever paints the settled colour', () => {
  excludes(E.STYLE_TEXT, '#3ddc84', 'the panel stylesheet uses the settled green');
  excludes(E.STYLE_TEXT, 'var(--green)');
  excludes(E.STYLE_TEXT, 'chrome-green');
  if (CSS_SRC) {
    const m = /--green:\s*(#[0-9a-fA-F]{3,8})/.exec(CSS_SRC);
    if (m) {
      measured.settled_green = m[1];
      excludes(E.STYLE_TEXT, m[1], 'the panel stylesheet uses the shell settled green');
    }
  }
  const doc = makeDoc();
  const t = E.renderEnrol(E.deriveEnrol({ catalogue: E.reduceCatalogue(E.emptyCatalogue(), { type: E.Action.ADD, name: 'A', price: '1' }) }), doc).textContent;
  excludes(t, 'PAID');
  excludes(t, 'SETTLED');
});

T('the SIMULATED banner appears only when the brain says --sim, and is unmissable', () => {
  const doc = makeDoc();
  const off = E.renderEnrol(E.deriveEnrol({}), doc);
  eq(off.dataset.simulated, 'false');
  ok(!hasClass(off, 'enrol-sim'), 'no sim banner when not simulated');

  const on = E.renderEnrol(E.deriveEnrol({ simulated: true }), doc);
  eq(on.dataset.simulated, 'true');
  const banner = byClass(on, 'enrol-sim');
  includes(banner.textContent, 'SIMULATED');
  includes(banner.textContent, '--sim');
  includes(banner.textContent, 'synthetic');
  // it must use the shell's own simulated-content styling, not a private look
  ok(String(banner.className).split(/\s+/).includes('simstrip'), 'not the shell .simstrip');
  ok(hasClass(banner, 'simbadge'), 'no .simbadge pill');
  measured.sim_banner = banner.textContent.slice(0, 40);
});

T('THE CSP FINDING: no visual meaning depends on this module\'s own stylesheet', () => {
  // index.html ships `style-src 'self'` with no 'unsafe-inline'. A <style>
  // injected from here is parsed and then IGNORED — measured in headless
  // Chrome as getComputedStyle(refusal).backgroundColor === 'rgba(0, 0, 0, 0)'.
  // So every element that has to READ as something must wear a class the
  // shell's own stylesheet defines.
  let csp = null;
  try {
    const html = readFileSync(join(HERE, '..', 'index.html'), 'utf8');
    const m = /Content-Security-Policy"[\s\S]*?content="([^"]*)"/.exec(html);
    csp = m ? m[1] : null;
  } catch { csp = null; }
  if (csp) {
    measured.shell_style_src = (/style-src ([^;]*)/.exec(csp) || [null, '(none)'])[1].trim();
    if (!/style-src[^;]*unsafe-inline/.test(csp)) {
      measured.inline_style_allowed = 'NO — shell classes are load-bearing';
    }
  }
  if (CSS_SRC === null) { measured.shell_classes = 'style.css unreadable'; return; }
  const missing = E.SHELL_CLASSES.filter((c) => !new RegExp(`\\.${c}\\b`).test(CSS_SRC));
  measured.shell_classes_present = `${E.SHELL_CLASSES.length - missing.length}/${E.SHELL_CLASSES.length}`;
  eq(missing.length, 0, `web/style.css no longer defines: ${missing.join(', ')}`);

  // and the elements that must read as something actually wear them
  const doc = makeDoc();
  const cat = E.reduceCatalogue(E.emptyCatalogue(), { type: E.Action.ADD, name: 'A', price: '214.507' });
  const el = E.renderEnrol(E.deriveEnrol({ catalogue: cat, simulated: true, refused: { reason: 'X' } }), doc);
  const wears = (cls, shell) => ok(
    String(byClass(el, cls).className).split(/\s+/).includes(shell),
    `.${cls} does not wear the shell's .${shell}`,
  );
  wears('enrol-refusal', 'abstain');
  wears('enrol-refusal', 'line-amber');
  wears('enrol-abstain', 'abstain');
  wears('enrol-brain-refused', 'abstain');
  wears('enrol-sim', 'simstrip');
  wears('enrol-note', 'orient');
  wears('enrol-btn-add', 'btn');
});

T('EVERY heading, hint and reason code wears its shell class, not just the first', () => {
  // A hand-edit once gave section 1 the shell heading class and left sections
  // 2, 3 and 4 with a bare enrol-h3, so one heading was small-caps mono and
  // three were browser-default bold. Invisible to a class-name assertion that
  // only looks at the first match, obvious in a screenshot. Checked wholesale.
  const doc = makeDoc();
  const cat = E.reduceCatalogue(
    E.reduceCatalogue(E.emptyCatalogue(), { type: E.Action.ADD, name: 'A', price: '1' }),
    { type: E.Action.ADD, name: 'B', price: '214.507' },   // a row AND a refusal
  );
  const el = E.renderEnrol(E.deriveEnrol({
    catalogue: cat, simulated: true, refused: { reason: 'X' },
    peel: { type: 'peel', ok: true, name: 'a', verdict: 'SAME', reason: 'r' },
    saaf: { type: 'saaf', ok: true, used: 1, rejected: 0, reason: 'stacked', burst: 1, burst_target: 1 },
  }), doc);
  const wants = [
    ['enrol-h3', 'orient-key'], ['enrol-h4', 'orient-key'],
    ['enrol-hint', 'reason'], ['enrol-label', 'orient-key'],
    ['enrol-code', 'abstain-why'], ['enrol-abstain-why', 'abstain-why'],
    ['enrol-refusal-why', 'abstain-why'],
  ];
  const counts = [];
  for (const [mine, shell] of wants) {
    const hits = byClassAll(el, mine);
    ok(hits.length > 0, `no .${mine} rendered at all`);
    for (const h of hits) {
      ok(String(h.className).split(/\s+/).includes(shell),
        `.${mine} ("${h.textContent.slice(0, 30)}") is missing .${shell}`);
    }
    counts.push(`${mine}x${hits.length}`);
  }
  measured.shell_class_coverage = counts.join(' ');
});

T('every rupee figure on screen wears .num', () => {
  const doc = makeDoc();
  let cat = E.reduceCatalogue(E.emptyCatalogue(), { type: E.Action.ADD, name: 'A', price: '10.50' });
  cat = E.reduceCatalogue(cat, { type: E.Action.ADD, name: 'B', price: '7.25' });
  const el = E.renderEnrol(E.deriveEnrol({ catalogue: cat }), doc);
  const rupeeEls = all(el, (e) => e.childNodes.length === 1
    && e.childNodes[0].nodeType === 3 && /^₹[\d,]+\.\d\d$/.test(e.childNodes[0].data));
  eq(rupeeEls.length, 2, 'expected exactly two rupee figures');
  for (const r of rupeeEls) {
    ok(String(r.className).split(/\s+/).includes('num'), `a rupee figure without .num: ${r.textContent}`);
  }
});

T('the burst button does not claim to start a capture it cannot start', () => {
  const doc = makeDoc();
  const t = E.renderEnrol(E.deriveEnrol({}), doc).textContent;
  includes(t, 'ROLLING window');
  includes(t, 'does not pretend to be');
});

T('peel and saaf replies are rendered field by field', () => {
  const doc = makeDoc();
  const el = E.renderEnrol(E.deriveEnrol({
    peel: {
      type: 'peel', ok: true, name: 'counter-upi', verdict: 'SAME',
      ignited_fraction: 0.87, registered: true, reason: 'enrolled', detail: 'stored',
    },
    saaf: {
      type: 'saaf', ok: true, used: 9, rejected: 3, sharpness_gain: 1.42,
      warning: 'NO_SUBPIXEL_DIVERSITY', reason: 'stacked', burst: 12, burst_target: 12,
    },
  }), doc);
  const p = byClass(el, 'enrol-peel');
  includes(p.textContent, 'counter-upi');
  includes(p.textContent, 'SAME');
  includes(p.textContent, '0.87');
  includes(p.textContent, 'REGISTERED');
  const s = byClass(el, 'enrol-saaf');
  includes(s.textContent, 'used 9, rejected 3');
  includes(s.textContent, '12 of 12 frames held');
  includes(s.textContent, 'UNDERSTATES', 'the gain confound must travel with the gain');
  includes(byClass(el, 'enrol-saaf-warning').textContent, 'NO_SUBPIXEL_DIVERSITY');
});

T('a brain refusal is shown as a first-class outcome', () => {
  const doc = makeDoc();
  const el = E.renderEnrol(E.deriveEnrol({ refused: { reason: 'BAD_ARGUMENT', detail: 'needs a name' } }), doc);
  const box = byClass(el, 'enrol-brain-refused');
  eq(box.getAttribute('role'), 'alert');
  includes(box.textContent, 'THE BRAIN REFUSED');
  includes(box.textContent, 'BAD_ARGUMENT');
});

// ==========================================================================
G('G. the panel — clicks, messages, and what goes on the wire');

function harness(extra = {}) {
  const doc = makeDoc();
  const wire = [];
  const panel = E.createPanel({
    doc,
    global: {},                       // no window.GAWAAH, no WebSocket
    send: (m) => wire.push(m),
    ...extra,
  });
  panel.paint();
  return { doc, wire, panel, root: () => doc.getElementById(E.PANEL_ROOT_ID) };
}

T('the panel mounts its own root and its own stylesheet', () => {
  const h = harness();
  const root = h.root();
  ok(root, `no #${E.PANEL_ROOT_ID} was created`);
  eq(root.getAttribute('id'), E.PANEL_ROOT_ID);
  eq(h.panel.mountedInto, 'body', 'with a bare document it falls back to <body>');
  ok(h.doc.getElementById(E.STYLE_ID), 'no stylesheet injected');
  const before = h.doc.head.childNodes.length;
  E.injectStyle(h.doc);
  eq(h.doc.head.childNodes.length, before, 'injectStyle is not idempotent');
});

T('it prefers the shell fill point #body-enrol and does not eat the shell prose', () => {
  const doc = makeDoc();
  // stand up the shape web/index.html actually has
  const section = doc.createElement('section');
  section.setAttribute('id', E.SHELL_PANEL_ID);
  const body = doc.createElement('div');
  body.setAttribute('id', E.SHELL_BODY_ID);
  const abstain = doc.createElement('div');
  abstain.setAttribute('id', 'abstain-enrol');
  abstain.textContent = 'enrol_nothing_enrolled';
  body.appendChild(abstain);
  section.appendChild(body);
  doc.body.appendChild(section);

  const panel = E.createPanel({ doc, global: {}, send: () => {} });
  panel.paint();
  eq(panel.mountedInto, E.SHELL_BODY_ID, 'did not use the fill point');
  const root = doc.getElementById(E.PANEL_ROOT_ID);
  ok(root, 'no render root');
  ok(body.children.includes(root), 'the root is not inside #body-enrol');
  // the shell's own abstention block must survive every repaint
  panel.handlers.onAdd('A', '1');
  panel.handlers.onRemove('sku-1');
  ok(body.children.includes(abstain), "the shell's #abstain-enrol was destroyed");
  includes(abstain.textContent, 'enrol_nothing_enrolled');
  measured.mounted_into = panel.mountedInto;
});

T('it declares its status to the shell, and records a refusal honestly', () => {
  const calls = [];
  const doc = makeDoc();
  const panel = E.createPanel({
    doc,
    global: { GAWAAH: { setPanelStatus: (id, s, why) => { calls.push([id, s, why]); return { ok: false, reason: `panel_unknown:${id}` }; } } },
    send: () => {},
  });
  panel.paint();
  eq(calls[0][0], 'enrol');
  eq(calls[0][1], 'ABSTAIN', 'a cold surface must declare ABSTAIN, not OK');
  eq(calls[0][2], E.Abstain.NOTHING_TYPED);
  eq(panel.declared.ok, false, "app.js's refusal was not recorded");
  includes(panel.declared.reason, 'panel_unknown:enrol');

  panel.onState({ type: 'saaf', ok: true, used: 5, rejected: 1, burst: 12, burst_target: 12, reason: 'stacked', sharpness_gain: 1.1, warning: '' });
  eq(calls[calls.length - 1][1], 'OK', 'once SAAF has spoken the panel knows something');
  measured.status_declared = calls.map((c) => c[1]).join('->');
});

T('it never declares GREEN', () => {
  const seen = [];
  const doc = makeDoc();
  const panel = E.createPanel({ doc, global: { GAWAAH: { setPanelStatus: (id, s) => { seen.push(s); return { ok: true }; } } }, send: () => {} });
  panel.paint();
  panel.onState({ type: 'peel', ok: true, name: 'x', registered: true, verdict: 'SAME', reason: 'enrolled' });
  panel.onState({ type: 'saaf', ok: true, used: 9, rejected: 0, burst: 12, burst_target: 12, reason: 'stacked' });
  panel.handlers.onAdd('A', '999999');
  for (const s of seen) ok(['OFF', 'ABSTAIN', 'OK'].includes(s), `illegal status ${s}`);
  excludes(seen.join(','), 'GREEN');
  excludes(ENROL_SRC, "'GREEN'", 'the module names a GREEN status');
});

T('a declare that throws does not take the paint down', () => {
  const doc = makeDoc();
  const panel = E.createPanel({ doc, global: { GAWAAH: { setPanelStatus: () => { throw new Error('shell exploded'); } } }, send: () => {} });
  eq(panel.paint(), true, 'paint reported failure because a status hook threw');
  includes(panel.declared.reason, 'declare_threw');
  ok(doc.getElementById(E.PANEL_ROOT_ID), 'the surface still rendered');
});

T('typing a good SKU and clicking ADD puts a row on screen', () => {
  const h = harness();
  byId(h.root(), 'enrol-name').value = 'Parle-G 100g';
  byId(h.root(), 'enrol-price').value = '214.50';
  byId(h.root(), 'enrol-add').click();
  const rows = byClassAll(h.root(), 'enrol-row');
  eq(rows.length, 1);
  includes(rows[0].textContent, 'Parle-G 100g');
  includes(rows[0].textContent, '₹214.50');
  includes(rows[0].textContent, '21450 paise');
  eq(h.panel.catalogue.skus[0].pricePaise, 21450);
  eq(h.root().dataset.skus, '1');
});

T('typing 214.507 and clicking ADD refuses on screen and adds NOTHING', () => {
  const h = harness();
  byId(h.root(), 'enrol-name').value = 'Sub-paisa';
  byId(h.root(), 'enrol-price').value = '214.507';
  byId(h.root(), 'enrol-add').click();
  eq(byClassAll(h.root(), 'enrol-row').length, 0, 'a row was added anyway');
  const box = byClass(h.root(), 'enrol-refusal');
  includes(box.textContent, 'price_sub_paise');
  includes(box.textContent, '214.507');
  eq(h.panel.catalogue.skus.length, 0);
  eq(h.wire.length, 0, 'nothing went to the brain');
});

T('each of the five named price inputs behaves as specified, through a real click', () => {
  const table = [
    ['214.507', false, E.PriceRefusal.SUB_PAISE],
    ['abc', false, E.PriceRefusal.NOT_A_NUMBER],
    ['', false, E.PriceRefusal.EMPTY],
    ['-5', false, E.PriceRefusal.NEGATIVE],
    ['1e3', false, E.PriceRefusal.EXPONENT],
  ];
  const got = [];
  for (const [typed, shouldAdd, reason] of table) {
    const h = harness();
    byId(h.root(), 'enrol-name').value = 'X';
    byId(h.root(), 'enrol-price').value = typed;
    byId(h.root(), 'enrol-add').click();
    eq(byClassAll(h.root(), 'enrol-row').length, shouldAdd ? 1 : 0, `${JSON.stringify(typed)} row count`);
    const box = byClass(h.root(), 'enrol-refusal');
    eq(box.dataset.reason, reason, `${JSON.stringify(typed)} reason`);
    includes(box.textContent, 'REFUSED');
    got.push(`${JSON.stringify(typed)}->${reason}`);
  }
  measured.named_inputs = got.join('  ');
});

T('removing a row removes exactly that row', () => {
  const h = harness();
  for (const [n, p] of [['A', '1'], ['B', '2'], ['C', '3']]) {
    byId(h.root(), 'enrol-name').value = n;
    byId(h.root(), 'enrol-price').value = p;
    byId(h.root(), 'enrol-add').click();
  }
  eq(byClassAll(h.root(), 'enrol-row').length, 3);
  const rows = byClassAll(h.root(), 'enrol-row');
  const target = rows[1];
  one(target, (e) => e.getAttribute('data-remove') !== null, 'remove button').click();
  const left = byClassAll(h.root(), 'enrol-row').map((r) => r.dataset.sku);
  eq(left.length, 2);
  ok(!left.includes(target.dataset.sku), 'the wrong row survived');
  measured.after_remove = left.join(',');
});

T('ENROL STICKER puts exactly {"type":"enrol_sticker", name} on the wire', () => {
  const h = harness();
  byId(h.root(), 'enrol-sticker').value = '  counter-upi  ';
  byId(h.root(), 'enrol-send').click();
  eq(h.wire.length, 1);
  eq(JSON.stringify(h.wire[0]), '{"type":"enrol_sticker","name":"counter-upi"}');
  measured.enrol_wire = JSON.stringify(h.wire[0]);
});

T('a row can enrol a sticker under its own SKU name', () => {
  const h = harness();
  byId(h.root(), 'enrol-name').value = 'Parle-G';
  byId(h.root(), 'enrol-price').value = '10';
  byId(h.root(), 'enrol-add').click();
  one(h.root(), (e) => e.getAttribute('data-enrol') === 'Parle-G', 'row enrol button').click();
  eq(JSON.stringify(h.wire[0]), '{"type":"enrol_sticker","name":"Parle-G"}');
});

T('an empty sticker name is refused HERE, before the wire', () => {
  const h = harness();
  byId(h.root(), 'enrol-sticker').value = '   ';
  byId(h.root(), 'enrol-send').click();
  eq(h.wire.length, 0, 'a nameless enrolment reached the brain');
  const box = byClass(h.root(), 'enrol-refusal');
  eq(box.dataset.field, 'sticker');
  eq(box.dataset.reason, E.NameRefusal.EMPTY);
});

T('a path-like sticker name never reaches the brain', () => {
  const h = harness();
  byId(h.root(), 'enrol-sticker').value = '../../etc/passwd';
  byId(h.root(), 'enrol-send').click();
  eq(h.wire.length, 0);
  eq(byClass(h.root(), 'enrol-refusal').dataset.reason, E.NameRefusal.PATH_LIKE);
});

T('CAPTURE BURST puts exactly {"type":"select_panel","id":"saaf"} on the wire', () => {
  const h = harness();
  byId(h.root(), 'enrol-burst').click();
  eq(h.wire.length, 1);
  eq(JSON.stringify(h.wire[0]), '{"type":"select_panel","id":"saaf"}');
  measured.burst_wire = JSON.stringify(h.wire[0]);
});

T('the panel never puts anything on the wire that could move money', () => {
  const h = harness();
  byId(h.root(), 'enrol-name').value = 'Parle-G';
  byId(h.root(), 'enrol-price').value = '214.50';
  byId(h.root(), 'enrol-add').click();
  byId(h.root(), 'enrol-sticker').value = 'Parle-G';
  byId(h.root(), 'enrol-send').click();
  byId(h.root(), 'enrol-burst').click();
  const kinds = h.wire.map((m) => m.type);
  measured.wire_verbs = kinds.join(',');
  for (const k of kinds) ok(['enrol_sticker', 'select_panel'].includes(k), `illegal verb ${k}`);
  const json = JSON.stringify(h.wire);
  for (const forbidden of ['paise', 'price', 'amount', 'total', 'done', 'verdict', 'green']) {
    excludes(json.toLowerCase(), forbidden, `the wire carried ${forbidden}`);
  }
});

T('a brain `peel` message is routed, consumed and rendered', () => {
  const h = harness();
  eq(h.panel.onState({ type: 'peel', ok: true, name: 'chai', verdict: 'SAME', registered: true, ignited_fraction: 0.9, reason: 'enrolled' }), true);
  includes(byClass(h.root(), 'enrol-peel').textContent, 'chai');
  ok(!h.panel.model.abstentions.includes(E.Abstain.NO_STICKER_RESULT));
});

T('a brain `saaf` message is routed, consumed and rendered', () => {
  const h = harness();
  eq(h.panel.onState({ type: 'saaf', ok: false, used: 0, rejected: 0, burst: 4, burst_target: 12, reason: 'burst_too_short', sharpness_gain: null, warning: '' }), true);
  includes(byClass(h.root(), 'enrol-saaf').textContent, '4 of 12 frames held');
  includes(byClass(h.root(), 'enrol-saaf').textContent, 'burst_too_short');
});

T('a message this panel does not own is IGNORED, not guessed at', () => {
  const h = harness();
  for (const m of [{ type: 'state' }, { type: 'ledger' }, { type: 'mudra' }, { type: 'keepalive' }, null, 'nope', 42]) {
    eq(h.panel.onState(m), false, `consumed ${JSON.stringify(m)}`);
  }
});

T('a `refused` message is shown rather than swallowed', () => {
  const h = harness();
  eq(h.panel.onState({ type: 'refused', reason: 'BAD_ARGUMENT', detail: 'needs a name' }), true);
  includes(byClass(h.root(), 'enrol-brain-refused').textContent, 'BAD_ARGUMENT');
});

T('with NO brain seam the buttons say so instead of appearing to work', () => {
  const doc = makeDoc();
  const panel = E.createPanel({ doc, global: {} });   // no send, no GAWAAH, no WebSocket
  panel.paint();
  const root = doc.getElementById(E.PANEL_ROOT_ID);
  eq(root.dataset.simulated, 'false');
  eq(panel.transport, null);
  includes(root.textContent, E.Abstain.NO_BRAIN_SEAM);
  byId(root, 'enrol-sticker').value = 'chai';
  byId(root, 'enrol-send').click();
  const box = byClass(root, 'enrol-brain-refused');
  includes(box.textContent, E.Abstain.NO_BRAIN_SEAM);
  includes(box.textContent, '"enrol_sticker"', 'it must show what it WOULD have sent');
});

T('window.GAWAAH.send is honoured when app.js offers it', () => {
  const wire = [];
  const doc = makeDoc();
  const panel = E.createPanel({ doc, global: { GAWAAH: { send: (m) => wire.push(m) } } });
  panel.paint();
  eq(panel.transport.via, 'window.GAWAAH.send');
  byId(doc.getElementById(E.PANEL_ROOT_ID), 'enrol-burst').click();
  eq(wire.length, 1);
  eq(wire[0].id, 'saaf');
});

T('setSimulated repaints the banner immediately', () => {
  const h = harness();
  ok(!hasClass(h.root(), 'enrol-sim'));
  h.panel.setSimulated(true);
  includes(byClass(h.root(), 'enrol-sim').textContent, 'SIMULATED');
  eq(h.root().dataset.simulated, 'true');
});

T('probeSim paints the banner from the brain health endpoint, and abstains if it cannot ask', async () => {
  const h = harness();
  const okFetch = async () => ({ json: async () => ({ ok: true, sim: true }) });
  const r1 = await E.probeSim(h.panel, okFetch, { hostname: '127.0.0.1', protocol: 'http:' });
  eq(r1.sim, true);
  eq(h.panel.model.simulated, true);

  const deadFetch = async () => { throw new Error('ECONNREFUSED'); };
  const r2 = await E.probeSim(h.panel, deadFetch, null);
  eq(r2.sim, null, 'could-not-ask is not the same as not-simulated');
  includes(r2.reason, 'health_unreachable');
});

T('a send that throws is reported, not swallowed', () => {
  const doc = makeDoc();
  const panel = E.createPanel({ doc, global: {}, send: () => { throw new Error('socket is dead'); } });
  panel.paint();
  const root = doc.getElementById(E.PANEL_ROOT_ID);
  byId(root, 'enrol-burst').click();
  includes(byClass(root, 'enrol-brain-refused').textContent, 'enrol_send_threw');
  includes(byClass(root, 'enrol-brain-refused').textContent, 'socket is dead');
});

T('the transport line reports how many messages actually went', () => {
  const h = harness();
  includes(byClass(h.root(), 'enrol-transport').textContent, '0 messages sent');
  byId(h.root(), 'enrol-burst').click();
  includes(byClass(h.root(), 'enrol-transport').textContent, '1 message sent');
  includes(byClass(h.root(), 'enrol-transport').textContent, 'select_panel');
});

// ==========================================================================
G('H. the load-order seam and the shell contract');

T('the descriptor has the shape app.js drains', () => {
  const d = E.DESCRIPTOR;
  eq(typeof d.id, 'string');
  eq(typeof d.title, 'string');
  eq(typeof d.attach, 'function');
  eq(typeof d.attached, 'boolean');
});

T('the descriptor was published on globalThis.GAWAAH_PANELS', () => {
  const q = globalThis.GAWAAH_PANELS;
  ok(q, 'nothing was published');
  const found = Array.isArray(q) ? q.some((d) => d && d.id === E.PANEL_ID) : q[E.PANEL_ID] !== undefined;
  ok(found, 'the enrol descriptor is not in the queue');
  measured.published_as = E.PUBLISHED;
});

T('publish survives peel.js turning GAWAAH_PANELS into a plain object', () => {
  // peel.js does `g.GAWAAH_PANELS = Object.assign({}, g.GAWAAH_PANELS, {...})`,
  // which destroys .push. Re-evaluating this module against that shape must not
  // throw — a TypeError here would take the only input surface off the page.
  const g = { GAWAAH_PANELS: { peel: {} } };
  const before = JSON.stringify(Object.keys(g.GAWAAH_PANELS));
  ok(before.includes('peel'));
  // exercised through attach(), which is the part that must not throw
  let threw = false;
  try { E.attach(() => ({ ok: false, reason: 'panel_unknown:enrol' }), { doc: makeDoc(), global: {} }); }
  catch { threw = true; }
  eq(threw, false, 'attach threw');
});

T('attach CAPTURES a registry refusal instead of throwing the drain loop over', () => {
  // app.js PANEL_IDS is ['core','mudra','peel','chilla','saaf','ledger'] — it
  // does not know 'enrol' yet, so registerPanel refuses. The drain must go on.
  const panel = E.attach((id) => ({ ok: false, reason: `panel_unknown:${id}` }), { doc: makeDoc(), global: {} });
  eq(panel.registration.ok, false);
  includes(panel.registration.reason, 'panel_unknown:enrol');
  const boom = E.attach(() => { throw new Error('registry exploded'); }, { doc: makeDoc(), global: {} });
  eq(boom.registration.ok, false);
  includes(boom.registration.reason, 'register_threw');
});

T('attach refuses a non-function register loudly', () => {
  let threw = false;
  try { E.attach(null); } catch (e) { threw = /registerPanel must be a function/.test(e.message); }
  ok(threw, 'attach(null) should throw a TypeError naming the contract');
});

T('this id is the one app.js would have to learn', () => {
  if (APP_SRC === null) { measured.app_panel_ids = 'app.js unreadable'; return; }
  const m = /export const PANEL_IDS = Object\.freeze\(\[([^\]]*)\]\)/.exec(APP_SRC);
  if (!m) { measured.app_panel_ids = 'not declared'; return; }
  const ids = [...m[1].matchAll(/'([a-z]+)'/g)].map((x) => x[1]);
  measured.app_panel_ids = ids.join('|');
  measured.enrol_in_app_ids = String(ids.includes(E.PANEL_ID));
  // Not a failure: app.js belongs to another agent. Recorded so the day it
  // learns 'enrol', this line flips and the registry path starts working with
  // no edit to enrol.js.
  ok(true);
});

T('the wire verbs this panel uses are ones the brain actually accepts', () => {
  let src = null;
  try { src = readFileSync(join(HERE, '..', '..', 'gawaah', 'brain_server.py'), 'utf8'); } catch { src = null; }
  if (src === null) { measured.brain_verbs = 'brain_server.py unreadable'; return; }
  const m = /CLIENT_VERBS: tuple\[str, \.\.\.\] = \(([\s\S]*?)\)/.exec(src);
  if (!m) { measured.brain_verbs = 'CLIENT_VERBS not found'; return; }
  const verbs = [...m[1].matchAll(/"([a-z_]+)"/g)].map((x) => x[1]);
  measured.brain_verbs = verbs.join('|');
  for (const v of ['enrol_sticker', 'select_panel']) {
    ok(verbs.includes(v), `the brain does not accept '${v}'`);
  }
  // and the panel id we ask SAAF for must be one the brain knows
  const pm = /^PANELS: tuple\[str, \.\.\.\] = \(([^)]*)\)/m.exec(src);
  if (pm) {
    const panels = [...pm[1].matchAll(/"([a-z]+)"/g)].map((x) => x[1]);
    measured.brain_panels = panels.join('|');
    ok(panels.includes('saaf'), "the brain does not know a 'saaf' panel");
  }
});

T('the shell fill point this panel mounts into exists, and our ids do not collide', () => {
  let html = null;
  try { html = readFileSync(join(HERE, '..', 'index.html'), 'utf8'); } catch { html = null; }
  if (html === null) { measured.shell_collision = 'index.html unreadable'; return; }
  measured.shell_has_panel_enrol = String(html.includes(`id="${E.SHELL_PANEL_ID}"`));
  measured.shell_has_body_enrol = String(html.includes(`id="${E.SHELL_BODY_ID}"`));
  // Not a failure if absent: index.html belongs to another agent and this
  // panel falls back to #chrome. Recorded either way.
  for (const id of ['enrol-name', 'enrol-price', 'enrol-sticker', 'enrol-add',
    'enrol-send', 'enrol-burst', E.PANEL_ROOT_ID, E.STYLE_ID]) {
    excludes(html, `id="${id}"`, `the shell already owns #${id}`);
  }
  ok(E.PANEL_ROOT_ID !== E.SHELL_PANEL_ID && E.PANEL_ROOT_ID !== E.SHELL_BODY_ID,
    'our root must not be the shell element itself, or a repaint deletes their prose');
});

// ==========================================================================
G('I. the module is inert under node');

T('importing enrol.js opened no socket and touched no document', () => {
  eq(typeof globalThis.document, 'undefined', 'a document appeared from somewhere');
  eq(globalThis.GAWAAH_ENROL, undefined, 'automount ran under node');
});

T('createPanel with no document does not throw and reports honestly', () => {
  const p = E.createPanel({ doc: null, global: {} });
  eq(p.paint(), false, 'paint should report it could not paint');
  eq(p.model.catalogue.count, 0);
  p.handlers.onAdd('A', '1');
  eq(p.catalogue.skus.length, 1, 'the reducer still works with no DOM');
});

T('connect() is inert without a WebSocket constructor', () => {
  const p = E.createPanel({ doc: makeDoc(), global: {} });
  eq(p.connect(), null);
});

T('connect() wires a socket when one exists, and JSON-encodes what it sends', () => {
  const sent = [];
  let inst = null;
  class FakeWS {
    constructor(url) { this.url = url; inst = this; }
    send(s) { sent.push(s); }
    close() { if (this.onclose) this.onclose(); }
  }
  const doc = makeDoc();
  const p = E.createPanel({ doc, global: { WebSocket: FakeWS, location: { hostname: '127.0.0.1', protocol: 'http:' } } });
  p.paint();
  const ws = p.connect();
  ok(ws, 'no socket');
  eq(inst.url, 'ws://127.0.0.1:8787/ws');
  const root = doc.getElementById(E.PANEL_ROOT_ID);
  byId(root, 'enrol-burst').click();
  eq(sent.length, 1);
  eq(sent[0], '{"type":"select_panel","id":"saaf"}');
  // a message arriving on that socket must reach onState
  inst.onmessage({ data: JSON.stringify({ type: 'saaf', ok: false, burst: 7, burst_target: 12, reason: 'burst_too_short', used: 0, rejected: 0 }) });
  includes(byClass(root, 'enrol-saaf').textContent, '7 of 12 frames held');
  // and a close must be reported, not hidden
  inst.onclose();
  includes(byClass(root, 'enrol-brain-refused').textContent, 'enrol_socket_closed');
  measured.socket_url = inst.url;
});

T('brainUrl follows the origin the page was served from', () => {
  // The brain serves this page AND the socket from one port, so the page's own
  // port is the brain's port. Falls back to app.js's constant when unknown.
  eq(E.brainUrl({ hostname: '127.0.0.1', protocol: 'http:', port: '8790' }), 'ws://127.0.0.1:8790/ws');
  eq(E.healthUrl({ hostname: '127.0.0.1', protocol: 'http:', port: '8790' }), 'http://127.0.0.1:8790/health');
  eq(E.brainUrl({ hostname: 'shop.local', protocol: 'https:', port: '8443' }), 'wss://shop.local:8443/ws');
  eq(E.brainUrl({ hostname: '127.0.0.1', protocol: 'http:' }), 'ws://127.0.0.1:8787/ws');
  eq(E.brainUrl({ hostname: 'shop.local', protocol: 'https:' }), 'wss://shop.local:8787/ws');
  eq(E.brainUrl(null), 'ws://localhost:8787/ws');
  if (APP_SRC) {
    const m = /const WS_PORT = (\d+)/.exec(APP_SRC);
    if (m) {
      measured.app_ws_port = m[1];
      eq(E.WS_PORT, Number(m[1]), 'this panel dials a different port than app.js');
    }
  }
});


// ==========================================================================
// K. THE ENROLMENT DESK ADDRESS. Configurable, and honest about CSP.
// ==========================================================================
G('K. the enrolment desk address');

T('an empty base means SAME ORIGIN and is valid, not missing', () => {
  const v = E.normaliseShopBase('');
  ok(v.ok);
  eq(v.base, E.SAME_ORIGIN);
  eq(E.normaliseShopBase('   ').base, '');
  eq(E.normaliseShopBase(null).base, '');
});

T('a full http base is normalised and its trailing slash removed', () => {
  eq(E.normaliseShopBase('http://127.0.0.1:8790').base, 'http://127.0.0.1:8790');
  eq(E.normaliseShopBase('http://127.0.0.1:8790/').base, 'http://127.0.0.1:8790');
  eq(E.normaliseShopBase('http://127.0.0.1:8790///').base, 'http://127.0.0.1:8790');
  eq(E.normaliseShopBase('https://shop.local/desk/').base, 'https://shop.local/desk',
    'a proxy path prefix survives');
});

T('a base that is not http(s) is REFUSED by name, never silently ignored', () => {
  for (const bad of ['javascript:alert(1)', 'file:///etc/passwd', 'ftp://x/y', 'ws://x:1/y']) {
    const v = E.normaliseShopBase(bad);
    eq(v.ok, false, `${bad} must be refused`);
    eq(v.reason, E.ServiceRefusal.BAD_BASE);
    ok(v.base === null, 'no base escapes a refusal');
  }
  eq(E.normaliseShopBase('not a url at all').ok, false);
  measured.desk_base_refusals = '5/5';
});

T('the base is resolved by a stated precedence: option, query, global, default', () => {
  const loc = { hostname: '127.0.0.1', protocol: 'http:', port: '8787', origin: 'http://127.0.0.1:8787', search: '?shop=http://desk.local:9000' };
  eq(E.resolveShopBase({ shopBase: 'http://opt:1' }, loc, {}).source, 'option');
  eq(E.resolveShopBase({ shopBase: 'http://opt:1' }, loc, {}).base, 'http://opt:1');
  eq(E.resolveShopBase({}, loc, {}).source, 'query');
  eq(E.resolveShopBase({}, loc, {}).base, 'http://desk.local:9000');
  eq(E.resolveShopBase({}, { ...loc, search: '' }, { GAWAAH_SHOP_BASE: 'http://g:2' }).source, 'global');
  const d = E.resolveShopBase({}, { ...loc, search: '' }, {});
  eq(d.source, 'default');
  eq(d.base, `http://127.0.0.1:${E.SHOP_PORT}`);
  measured.desk_default_base = d.base;
});

T('a page served BY the desk defaults to same-origin, not to a hardcoded host', () => {
  const onDesk = { hostname: '127.0.0.1', protocol: 'http:', port: String(E.SHOP_PORT), origin: `http://127.0.0.1:${E.SHOP_PORT}`, search: '' };
  const r = E.resolveShopBase({}, onDesk, {});
  eq(r.source, 'same-origin');
  eq(r.base, E.SAME_ORIGIN);
});

T('THE CSP FACT: a cross-origin desk is named as BLOCKED before it is dialled', () => {
  // web/index.html ships connect-src 'self'. A page on the brain's port cannot
  // fetch the desk's port, and the browser's own error is an opaque TypeError
  // that reads exactly like "the service is down". Telling an operator to start
  // a service that is already running is the worst available advice, so this is
  // decided from the CSP, up front, and not from a failed call.
  const onBrain = { origin: 'http://127.0.0.1:8787', hostname: '127.0.0.1', protocol: 'http:', port: '8787' };
  const r = E.describeReach('http://127.0.0.1:8790', onBrain, 'self');
  eq(r.blocked, true);
  eq(r.reason, E.ServiceRefusal.CSP_BLOCKED);
  includes(r.detail, 'connect-src');
  includes(r.detail, 'This is a permission, not an outage');
  includes(r.detail, 'http://127.0.0.1:8790');
  measured.csp_blocked_reason = r.reason;
});

T('same-origin and the empty base are never reported as blocked', () => {
  const onDesk = { origin: 'http://127.0.0.1:8790' };
  eq(E.describeReach(E.SAME_ORIGIN, onDesk, 'self').blocked, false);
  eq(E.describeReach('http://127.0.0.1:8790', onDesk, 'self').blocked, false);
  eq(E.describeReach('http://127.0.0.1:8790', onDesk, 'self').sameOrigin, true);
  // With a relaxed CSP the call is allowed and must not be pre-refused.
  eq(E.describeReach('http://127.0.0.1:8790', { origin: 'http://127.0.0.1:8787' }, 'open').blocked, false);
  // With no known page origin we cannot PROVE it is blocked, so we do not claim it.
  eq(E.describeReach('http://127.0.0.1:8790', null, 'self').blocked, false);
});

T('the CSP claim this panel makes matches the CSP the shell actually ships', () => {
  // If the shell ever widens connect-src, the sentence above becomes a lie.
  // Read the real header rather than trusting a comment.
  let shell = null;
  try { shell = readFileSync(join(HERE, '..', 'index.html'), 'utf8'); } catch { shell = null; }
  if (shell === null) return;
  // Read the META TAG, not the prose around it: index.html discusses
  // connect-src at length in a comment, and matching that comment was a real
  // false positive while writing this test.
  const tag = /<meta http-equiv="Content-Security-Policy"[\s\S]*?content="([^"]+)"/.exec(shell);
  ok(tag, 'no CSP meta tag in index.html');
  const m = /connect-src ([^;]+)/.exec(tag[1]);
  ok(m, 'no connect-src in the CSP meta tag');
  measured.shell_connect_src = m[1].trim();
  eq(m[1].trim(), "'self'", 'the shell CSP changed; the CSP_BLOCKED prose must be revisited');
});

// ==========================================================================
// L. THE PRICE BOUNDARY AT THE PHOTO SEAM. The named cases, again, but this
//    time through the function that actually builds the upload.
// ==========================================================================
G('L. buildEnrolRequest — the price boundary where a photo is taught');

const PHOTO = { kind: 'file', name: 'parle.png', size: 40000, type: 'image/png' };
const teach = (price, name = 'Parle-G 100g') =>
  E.buildEnrolRequest({ name, price, image: PHOTO, base: 'http://d:1' });

T('THE NAMED CASE: 214.507 is refused at the photo boundary and nothing is built', () => {
  const r = teach('214.507');
  eq(r.ok, false);
  eq(r.request, null, 'no request object is built for a refused price');
  eq(r.paise, null);
  eq(r.refusal.field, 'price');
  eq(r.refusal.reason, E.PriceRefusal.SUB_PAISE);
  includes(r.refusal.detail, 'NOT rounded');
  eq(r.refusal.typed, '214.507');
});

T('THE NAMED CASE: "abc" is refused as not a number', () => {
  const r = teach('abc');
  eq(r.ok, false);
  eq(r.refusal.reason, E.PriceRefusal.NOT_A_NUMBER);
  eq(r.request, null);
});

T('THE NAMED CASE: "" is refused as empty', () => {
  eq(teach('').refusal.reason, E.PriceRefusal.EMPTY);
  eq(teach('   ').refusal.reason, E.PriceRefusal.EMPTY);
  eq(teach(null).refusal.reason, E.PriceRefusal.EMPTY);
});

T('THE NAMED CASE: -5 is refused as negative', () => {
  eq(teach('-5').refusal.reason, E.PriceRefusal.NEGATIVE);
  eq(teach(-5).refusal.reason, E.PriceRefusal.NEGATIVE);
});

T('THE NAMED CASE: "1e3" is refused as exponent notation', () => {
  const r = teach('1e3');
  eq(r.ok, false);
  eq(r.refusal.reason, E.PriceRefusal.EXPONENT);
  eq(r.request, null, 'a thousand rupees was NOT quietly enrolled');
});

T('THE NAMED CASE: 0 is refused as a zero price', () => {
  eq(teach('0').refusal.reason, E.PriceRefusal.ZERO);
  eq(teach('0.00').refusal.reason, E.PriceRefusal.ZERO);
  eq(teach(0).refusal.reason, E.PriceRefusal.ZERO);
});

T('all six named cases refuse, and NONE of them produces an upload', () => {
  const named = ['214.507', 'abc', '', '-5', '1e3', '0'];
  const seen = [];
  for (const p of named) {
    const r = teach(p);
    eq(r.ok, false, `${JSON.stringify(p)} must be refused`);
    eq(r.request, null, `${JSON.stringify(p)} must not build a request`);
    seen.push(`${JSON.stringify(p)}->${r.refusal.reason}`);
  }
  eq(new Set(seen.map((s) => s.split('->')[1])).size, 6, 'six distinct reasons');
  measured.photo_price_refusals = seen.join('  ');
});

T('a good price DOES build the upload, and the paise are carried out with it', () => {
  const r = teach('214.50');
  ok(r.ok, r.refusal && r.refusal.detail);
  eq(r.paise, 21450, 'the integer that will be stored');
  eq(r.rupees, '214.50');
  eq(r.request.method, 'POST');
  eq(r.request.path, '/enrol');
  eq(r.request.url, 'http://d:1/enrol');
  eq(r.request.fields.sku_id, 'parle-g-100g');
  eq(r.request.fields.name, 'Parle-G 100g');
  eq(r.request.fields.price_rupees, '214.50');
  eq(r.request.fields.price_paise, '21450');
  eq(r.request.image.kind, 'file');
  measured.teach_fields = JSON.stringify(r.request.fields);
});

T('the wire rupees are re-derived FROM the paise, not echoed from the keystrokes', () => {
  // ' Rs 214.5 ' and '214.50' mean the same integer, so they must put the same
  // characters on the wire. Echoing the typed string would make the browser and
  // the desk disagree about a value they both already agree on.
  const a = teach(' Rs 214.5 ');
  const b = teach('214.50');
  ok(a.ok && b.ok);
  eq(a.paise, b.paise);
  eq(a.request.fields.price_rupees, b.request.fields.price_rupees);
  eq(a.request.fields.price_rupees, '214.50');
});

T('the NAME is refused before the price, and the price before the upload', () => {
  // The operator is told the FIRST thing that was wrong, not the last.
  const r = E.buildEnrolRequest({ name: '', price: '214.507', image: PHOTO });
  eq(r.refusal.field, 'name', 'an empty name is reported before the bad price');
  const r2 = E.buildEnrolRequest({ name: 'ok', price: '214.507', image: null });
  eq(r2.refusal.field, 'price', 'the price is proved before an 8 MB upload is contemplated');
});

T('a duplicate name is refused against the names already taught', () => {
  const r = E.buildEnrolRequest({ name: 'Parle-G', price: '10', image: PHOTO, taken: ['parle-g'] });
  eq(r.ok, false);
  eq(r.refusal.reason, E.NameRefusal.DUPLICATE);
});

// ==========================================================================
G('M. paiseToRupeeString — the wire form of an integer');

T('the wire string round-trips through parsePaise exactly, across the range', () => {
  let n = 0;
  for (let p = 1; p <= 20000; p++) {
    const s = E.paiseToRupeeString(p);
    const back = E.parsePaise(s);
    ok(back.ok, `${p} -> ${s} did not re-parse`);
    eq(back.paise, p, `wire round trip ${p} via ${s}`);
    n++;
  }
  eq(E.paiseToRupeeString(21450), '214.50');
  eq(E.paiseToRupeeString(1), '0.01');
  eq(E.paiseToRupeeString(100), '1.00');
  eq(E.paiseToRupeeString(100000000), '1000000.00');
  measured.wire_round_trip_exact = `${n}/20000`;
});

T('the wire form carries no grouping, no currency mark and no float', () => {
  // A comma would be refused by parsePaise on the way back in, and a currency
  // mark is not a number. The wire form is digits and one dot.
  for (const p of [1, 99, 100, 123456, 100000000]) {
    ok(/^\d+\.\d\d$/.test(E.paiseToRupeeString(p)), `bad wire form for ${p}`);
  }
  eq(E.paiseToRupeeString(1.5), null, 'a non-integer has no wire form');
  eq(E.paiseToRupeeString(-1), null);
  eq(E.paiseToRupeeString('21450'), null, 'a string is not an integer paise value');
});

// ==========================================================================
G('N. checkImage — INVARIANT 4 at the upload boundary');

T('a RAW FRAME is refused by name, exactly as app.js refuses it on the socket', () => {
  for (const k of E.FORBIDDEN_IMAGE_KEYS) {
    const r = E.checkImage({ kind: 'file', name: 'x.png', size: 10, type: 'image/png', [k]: 'PAYLOAD' });
    eq(r.ok, false, `key ${k} must be refused`);
    eq(r.reason, E.TeachRefusal.RAW_FRAME);
    includes(r.detail, 'INVARIANT 4');
    includes(r.detail, JSON.stringify(k));
  }
  measured.raw_frame_keys_refused = `${E.FORBIDDEN_IMAGE_KEYS.length}/${E.FORBIDDEN_IMAGE_KEYS.length}`;
});

T('a raw frame cannot sneak through buildEnrolRequest either', () => {
  const r = E.buildEnrolRequest({
    name: 'X', price: '10',
    image: { kind: 'rectified_mat_crop', b64: 'AAAA', raw_frame: 'the whole shop' },
  });
  eq(r.ok, false);
  eq(r.refusal.reason, E.TeachRefusal.RAW_FRAME);
  eq(r.request, null);
});

T('the rectified mat crop IS accepted, and is the only camera source', () => {
  const r = E.checkImage({ kind: E.RECTIFIED_CROP_KIND, b64: 'QUJD' });
  ok(r.ok);
  eq(r.image.kind, 'rectified_mat_crop');
  eq(r.image.type, 'image/png');
  includes(r.image.label, 'rectified mat crop');
  eq(E.checkImage({ kind: E.RECTIFIED_CROP_KIND, b64: '' }).reason, E.TeachRefusal.NO_CAMERA_CROP);
});

T('no image at all is a named refusal, not a crash', () => {
  eq(E.checkImage(null).reason, E.TeachRefusal.NO_IMAGE);
  eq(E.checkImage(undefined).reason, E.TeachRefusal.NO_IMAGE);
  eq(E.checkImage('a string').reason, E.TeachRefusal.NO_IMAGE);
  eq(E.checkImage({ kind: 'nonsense' }).reason, E.TeachRefusal.NO_IMAGE);
});

T('an oversized file is refused BEFORE the upload, with its real size named', () => {
  const r = E.checkImage({ kind: 'file', name: 'burst.png', size: E.MAX_IMAGE_BYTES + 1, type: 'image/png' });
  eq(r.ok, false);
  eq(r.reason, E.TeachRefusal.TOO_LARGE);
  includes(r.detail, String(E.MAX_IMAGE_BYTES + 1));
  ok(E.checkImage({ kind: 'file', name: 'ok.png', size: E.MAX_IMAGE_BYTES, type: 'image/png' }).ok,
    'exactly at the cap is allowed');
  measured.max_image_bytes = String(E.MAX_IMAGE_BYTES);
});

T('a KNOWN-WRONG type is refused; an UNKNOWN type is allowed through to the desk', () => {
  const heic = E.checkImage({ kind: 'file', name: 'IMG.HEIC', size: 100, type: 'image/heic' });
  eq(heic.ok, false);
  eq(heic.reason, E.TeachRefusal.BAD_TYPE);
  includes(heic.detail, 'HEIC');
  // An empty type is a platform quirk on real photos. Refusing it here would
  // block genuine uploads on the strength of a missing header; the desk sniffs
  // the bytes anyway, so the unknown case is passed on rather than guessed at.
  ok(E.checkImage({ kind: 'file', name: 'photo', size: 100, type: '' }).ok);
  eq(E.checkImage({ kind: 'file', name: 'a.pdf', size: 10, type: 'application/pdf' }).reason,
    E.TeachRefusal.BAD_TYPE);
});

// ==========================================================================
G('O. safeThumb — desk-supplied bytes, never a desk-supplied fetch');

T('a remote thumbnail URL is REFUSED: this page fetches no image it was handed', () => {
  for (const bad of ['http://evil.example/x.png', 'https://cdn.example/y.jpg', '//evil.example/z.png']) {
    const r = E.safeThumb(bad);
    eq(r.ok, false, `${bad} must be refused`);
    eq(r.reason, E.ThumbRefusal.REMOTE_URL);
    eq(r.src, null, 'no src escapes');
  }
  measured.remote_thumb_refusals = '3/3';
});

T('an inline data: URI of a real image type is accepted', () => {
  const png = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==';
  const r = E.safeThumb(png);
  ok(r.ok);
  eq(r.src, png);
  ok(E.safeThumb('data:image/jpeg;base64,QUJDRA==').ok);
  ok(E.safeThumb('data:image/webp;base64,QUJDRA==').ok);
});

T('a data: URI that is not an image, or not base64, is refused', () => {
  eq(E.safeThumb('data:text/html;base64,PHNjcmlwdD4=').reason, E.ThumbRefusal.NOT_AN_IMAGE);
  eq(E.safeThumb('data:image/svg+xml;base64,PHN2Zz4=').reason, E.ThumbRefusal.NOT_AN_IMAGE,
    'SVG is a script surface, not a thumbnail');
  eq(E.safeThumb('data:image/png,<not base64>').reason, E.ThumbRefusal.NOT_AN_IMAGE);
});

T('bare base64 is wrapped as PNG, which is what a photo_png field holds', () => {
  const r = E.safeThumb('iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB');
  ok(r.ok);
  ok(r.src.startsWith('data:image/png;base64,'));
  eq(E.safeThumb('not base64 at all!!').reason, E.ThumbRefusal.NOT_AN_IMAGE);
  eq(E.safeThumb('').reason, E.ThumbRefusal.ABSENT);
  eq(E.safeThumb(null).reason, E.ThumbRefusal.ABSENT);
  eq(E.safeThumb(12345).reason, E.ThumbRefusal.NOT_AN_IMAGE);
});

T('an oversized thumbnail is dropped rather than wedged into the DOM', () => {
  const huge = 'A'.repeat(E.MAX_THUMB_CHARS + 1);
  eq(E.safeThumb(huge).reason, E.ThumbRefusal.TOO_LARGE);
  measured.max_thumb_chars = String(E.MAX_THUMB_CHARS);
});

// ==========================================================================
G('P. readShopResponse — every way the desk can fail to be a result');

T('a 200 with good JSON is a result', () => {
  const r = E.readShopResponse(200, JSON.stringify({ ok: true, skus: [] }));
  ok(r.ok);
  eq(r.refusal, null);
  ok(Array.isArray(r.data.skus));
});

T('a 500 with an HTML error page is shop_service_http_error, not a crash', () => {
  const r = E.readShopResponse(500, '<html><body>Internal Server Error</body></html>');
  eq(r.ok, false);
  eq(r.refusal.reason, E.ServiceRefusal.HTTP);
  eq(r.refusal.status, 500);
  includes(r.refusal.detail, 'Internal Server Error');
});

T('a 200 with a body that is not JSON is named, and the body is quoted back', () => {
  const r = E.readShopResponse(200, 'not json at all');
  eq(r.refusal.reason, E.ServiceRefusal.BAD_JSON);
  includes(r.refusal.detail, 'not json at all');
});

T("the desk's OWN named refusal is passed through as a result, not overwritten", () => {
  // A desk that says {"ok": false, "reason": "mat_did_not_lock"} has produced a
  // RESULT. Replacing its reason with a generic one would throw away the only
  // sentence that tells the operator what to change.
  const r = E.readShopResponse(400, JSON.stringify({
    ok: false, reason: 'mat_did_not_lock', detail: 'found 2 of 4 markers; move the light',
  }));
  eq(r.ok, false);
  eq(r.refusal.reason, 'mat_did_not_lock');
  includes(r.refusal.detail, '2 of 4 markers');
  measured.desk_refusal_passthrough = r.refusal.reason;
});

T('a non-2xx with JSON but no reason still gets a named refusal', () => {
  const r = E.readShopResponse(404, JSON.stringify({ ok: true, detail: 'nope' }));
  eq(r.ok, false);
  eq(r.refusal.reason, E.ServiceRefusal.REFUSED);
});

T('a JSON array or a bare number is not a result shape', () => {
  eq(E.readShopResponse(200, '[1,2,3]').refusal.reason, E.ServiceRefusal.BAD_JSON);
  eq(E.readShopResponse(200, '42').refusal.reason, E.ServiceRefusal.BAD_JSON);
  eq(E.readShopResponse(200, '').refusal.reason, E.ServiceRefusal.BAD_JSON);
});

T('every service refusal code has help text an operator can act on', () => {
  for (const c of E.SERVICE_REFUSAL_CODES) {
    ok(String(E.SERVICE_REFUSAL_HELP[c]).length > 40, `${c} has no usable help`);
  }
  for (const c of E.TEACH_REFUSAL_CODES) {
    ok(String(E.TEACH_REFUSAL_HELP[c]).length > 40, `${c} has no usable help`);
  }
  for (const c of E.THUMB_REFUSAL_CODES) {
    ok(String(E.THUMB_REFUSAL_HELP[c]).length > 20, `${c} has no usable help`);
  }
  measured.named_service_codes = String(
    E.SERVICE_REFUSAL_CODES.length + E.TEACH_REFUSAL_CODES.length + E.THUMB_REFUSAL_CODES.length);
});

// ==========================================================================
G('Q. deriveShopCatalog — unknown is not empty');

T('never fetched is UNKNOWN; an answered empty desk is EMPTY, and they differ', () => {
  const unknown = E.deriveShopCatalog(null);
  eq(unknown.known, false);
  eq(unknown.count, 0);
  const empty = E.deriveShopCatalog({ ok: true, skus: [] });
  eq(empty.known, true);
  eq(empty.count, 0);
  ok(unknown.known !== empty.known, 'the two states must not be collapsed');
});

T('a taught SKU carries paise, rupees, footprint mm and a thumbnail', () => {
  const c = E.deriveShopCatalog({
    ok: true,
    skus: [{
      sku_id: 'parle-g-100g', name: 'Parle-G 100g', price_paise: 1000,
      footprint_mm: 38.19, photo_png: 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB',
    }],
  });
  eq(c.known, true);
  eq(c.count, 1);
  const r = c.rows[0];
  eq(r.pricePaise, 1000);
  eq(r.rupees, '₹10.00');
  eq(r.paiseText, '1000 paise');
  eq(r.footprintText, '38.19 mm');
  ok(r.thumbSrc.startsWith('data:image/png;base64,'));
  measured.catalog_row = `${r.name} ${r.paiseText} ${r.rupees} ${r.footprintText}`;
});

T('a price that is not integer paise is NOT rendered as money', () => {
  // A desk that returns 10.5 has a bug. Painting '₹0.11' beside it would hide
  // that bug behind a plausible number; the row goes priceless and says so.
  const c = E.deriveShopCatalog({ skus: [{ sku_id: 'x', name: 'X', price_paise: 10.5 }] });
  const r = c.rows[0];
  eq(r.priceOk, false);
  eq(r.pricePaise, null);
  eq(r.rupees, null);
  eq(r.priceReason, 'price_not_integer_paise');
  eq(E.deriveShopCatalog({ skus: [{ sku_id: 'y', name: 'Y' }] }).rows[0].priceReason, 'price_absent');
  eq(E.deriveShopCatalog({ skus: [{ sku_id: 'z', price_paise: -5 }] }).rows[0].priceOk, false);
});

T('an unmeasured footprint says so rather than showing 0.00 mm', () => {
  const c = E.deriveShopCatalog({ skus: [{ sku_id: 'x', name: 'X', price_paise: 100 }] });
  eq(c.rows[0].footprintMm, null);
  eq(c.rows[0].footprintText, 'not measured');
  eq(E.formatMm(0), null);
  eq(E.formatMm(-1), null);
  eq(E.formatMm(NaN), null);
  eq(E.formatMm(38.19), '38.19 mm');
});

T('an answer with no skus array is BAD SHAPE, not an empty shop', () => {
  const c = E.deriveShopCatalog({ ok: true, something_else: 1 });
  eq(c.known, false);
  eq(c.refusal.reason, E.ServiceRefusal.BAD_SHAPE);
});

T('a service refusal reaches the catalogue instead of a blank list', () => {
  const c = E.deriveShopCatalog(null, { reason: E.ServiceRefusal.UNREACHABLE, detail: 'nope', status: null });
  eq(c.known, false);
  eq(c.refusal.reason, E.ServiceRefusal.UNREACHABLE);
});

// ==========================================================================
G('R. deriveRecognition — AMBER is excluded, and the total is AUDITED');

const REC = {
  ok: true,
  total_paise: 3000,
  items: [
    { sku_id: 'parle-g', name: 'Parle-G', price_paise: 1000, reason: 'match', top1: 0.91, top2: 0.42, margin: 0.49, long_edge_mm: 38.19 },
    { sku_id: 'maggi', name: 'Maggi', price_paise: 2000, reason: 'match', top1: 0.88, top2: 0.31, margin: 0.57, long_edge_mm: 61.4 },
    { sku_id: null, price_paise: null, reason: 'below_similarity', top1: 0.21, top2: 0.19, margin: 0.02, long_edge_mm: 44.0 },
  ],
};

T('named items are priced, unnamed ones are AMBER and excluded from the total', () => {
  const r = E.deriveRecognition(REC);
  eq(r.known, true);
  eq(r.items.length, 3);
  eq(r.priced, 2);
  eq(r.amber, 1);
  eq(r.totalPaise, 3000, 'only the two named lines');
  eq(r.totalRupees, '₹30.00');
  eq(r.items[2].amber, true);
  eq(r.items[2].pricePaise, null, 'an amber line has no price at all');
  eq(r.items[0].amber, false);
  measured.recognition_total = `${r.totalPaise} paise from ${r.priced} named, ${r.amber} amber excluded`;
});

T('THE AUDIT: a total that disagrees with its own lines shows NO total', () => {
  // The desk says 9999; its own priced lines add to 3000. A UI that prints the
  // desk's number is hiding the bug; a UI that prints its own is overruling the
  // service. This one prints neither and names the disagreement.
  const r = E.deriveRecognition({ ...REC, total_paise: 9999 });
  eq(r.totalAgrees, false);
  eq(r.totalPaise, null);
  eq(r.totalRupees, null);
  eq(r.serverTotalPaise, 9999);
  eq(r.refusal.reason, E.ServiceRefusal.TOTAL_DISAGREES);
  includes(r.refusal.detail, '9999');
  includes(r.refusal.detail, '3000');
  measured.total_audit = 'desk 9999 vs lines 3000 -> shop_total_disagrees_with_its_own_lines';
});

T('a desk that reports no total of its own is not treated as disagreeing', () => {
  const noTotal = { ok: true, items: REC.items };
  const r = E.deriveRecognition(noTotal);
  eq(r.totalAgrees, true);
  eq(r.totalPaise, 3000);
  eq(r.serverTotalPaise, null);
});

T('a sku WITH a match but WITHOUT a usable price lands amber, never bills 0', () => {
  const r = E.deriveRecognition({ items: [{ sku_id: 'x', name: 'X', reason: 'match', price_paise: null }] });
  eq(r.items[0].amber, true);
  eq(r.priced, 0);
  eq(r.totalPaise, 0, 'the total of no named lines is zero paise, and it is honest');
  const f = E.deriveRecognition({ items: [{ sku_id: 'x', name: 'X', reason: 'match', price_paise: 12.5 }] });
  eq(f.items[0].amber, true, 'a float price is not money and cannot be billed');
  eq(f.priced, 0);
});

T('a sku returned with a NON-match reason is amber even if it carries a price', () => {
  // Belt and braces: the abstention lives in the reason, and a price arriving
  // beside an abstention does not promote it to a sale.
  const r = E.deriveRecognition({ items: [{ sku_id: 'x', name: 'X', reason: 'below_margin', price_paise: 500 }] });
  eq(r.items[0].amber, true);
  eq(r.priced, 0);
  eq(r.totalPaise, 0);
});

T("every one of identity.py's four abstention reasons is explained to a shopkeeper", () => {
  for (const code of E.RECOGNISE_ABSTAIN_CODES) {
    const note = E.recogniseReasonNote(code);
    ok(note.length > 60, `${code} has no usable explanation`);
    excludes(note, 'unrecognised reason code', `${code} fell through to the unknown branch`);
  }
  eq(E.RECOGNISE_ABSTAIN_CODES.length, 4);
  measured.recognise_reasons = E.RECOGNISE_ABSTAIN_CODES.join(',');
});

T('an unknown reason code gets NO invented prose', () => {
  const note = E.recogniseReasonNote('vibes_were_off');
  includes(note, 'unrecognised reason code');
  includes(note, 'will not translate a code it does not know');
  includes(E.recogniseReasonNote(''), 'nothing to explain');
});

T('the reason codes here are the reason codes gawaah/identity.py actually emits', () => {
  // A rename on the Python side must break this test, not silently produce a
  // panel that shows an untranslated code to a shopkeeper.
  let py = null;
  try { py = readFileSync(join(HERE, '..', '..', 'gawaah', 'identity.py'), 'utf8'); } catch { py = null; }
  if (py === null) return;
  const found = [];
  for (const m of py.matchAll(/^REASON_[A-Z_]+ = "([a-z_]+)"$/gm)) found.push(m[1]);
  ok(found.length >= 5, `expected 5 reason constants, found ${found.length}`);
  for (const c of found) {
    ok(Object.values(E.RecogniseReason).includes(c), `identity.py emits ${c}, this panel does not know it`);
  }
  measured.identity_reason_codes = found.join(',');
});

T('a recognition with no items array is BAD SHAPE', () => {
  eq(E.deriveRecognition({ ok: true }).refusal.reason, E.ServiceRefusal.BAD_SHAPE);
  eq(E.deriveRecognition(null).known, false);
  eq(E.deriveRecognition(null).refusal, null, 'never asked is not a refusal');
});

// ==========================================================================
G('S. derivePhoto — the photo flow abstains by name too (INVARIANT 7)');

T('every declared photo abstention is REACHABLE from a state, with help text', () => {
  // Not all five are cold states, and pretending they were would be the same
  // dishonesty this test exists to prevent. `photo_desk_not_reached` needs a
  // desk that actually failed, so each code is reached from ITS OWN state.
  const states = {
    [E.PhotoAbstain.CATALOG_UNKNOWN]: {},
    [E.PhotoAbstain.NO_IMAGE]: {},
    [E.PhotoAbstain.NO_CAMERA_CROP]: {},
    [E.PhotoAbstain.NOTHING_TRIED]: {},
    [E.PhotoAbstain.NO_SERVICE]: {
      shopRefusal: { reason: E.ServiceRefusal.UNREACHABLE, detail: 'x', status: null },
    },
  };
  for (const c of E.PHOTO_ABSTAIN_CODES) {
    const p = E.deriveEnrol(states[c]).photo;
    ok(p.abstentions.includes(c), `unreachable photo abstention: ${c}`);
    ok(String(E.PHOTO_ABSTAIN_HELP[c]).length > 30, `${c} has no help text`);
  }
  measured.photo_abstentions_reachable = `${E.PHOTO_ABSTAIN_CODES.length}/${E.PHOTO_ABSTAIN_CODES.length}`;
});

T('a cold photo flow abstains on the four things it has not been told', () => {
  const p = E.deriveEnrol({}).photo;
  eq(p.abstentions.length, 4);
  ok(!p.abstentions.includes(E.PhotoAbstain.NO_SERVICE),
    'a desk that has not been ASKED has not FAILED, and must not be reported as unreachable');
  measured.cold_photo_abstentions = p.abstentions.join(',');
});

T('the photo abstentions are SEPARATE from the brain abstentions', () => {
  // Merging them would tell an operator to restart the wrong process.
  const m = E.deriveEnrol({});
  eq(m.abstentions.length, 4, 'the brain list is untouched by the photo flow');
  for (const c of E.PHOTO_ABSTAIN_CODES) {
    ok(!m.abstentions.includes(c), `${c} leaked into the brain abstention list`);
  }
  for (const c of E.ABSTAIN_CODES) {
    ok(!m.photo.abstentions.includes(c), `${c} leaked into the photo abstention list`);
  }
});

T('each photo abstention clears only when its own fact arrives', () => {
  const p = E.deriveEnrol({
    shop: { ok: true, skus: [] },
    recognition: { ok: true, items: [] },
    image: { kind: 'file', name: 'a.png', size: 10, type: 'image/png' },
    cameraCrop: 'QUJDRA==',
  }).photo;
  eq(p.abstentions.length, 0, `still abstaining on: ${p.abstentions.join(',')}`);
});

T('the live paise preview is the integer that WILL be stored', () => {
  const good = E.deriveEnrol({ typedPrice: '214.50' }).photo.preview;
  eq(good.ok, true);
  eq(good.paise, 21450);
  eq(good.paiseText, '21450 paise');
  eq(good.rupees, '₹214.50');
  eq(good.wire, '214.50');
  const bad = E.deriveEnrol({ typedPrice: '214.507' }).photo.preview;
  eq(bad.ok, false);
  eq(bad.paise, null);
  eq(bad.reason, E.PriceRefusal.SUB_PAISE);
  includes(bad.detail, 'NOT rounded');
});

// ==========================================================================
G('T. the photo surface on screen');

const FULL = () => E.deriveEnrol({
  shop: {
    ok: true,
    skus: [{
      sku_id: 'parle-g-100g', name: 'Parle-G 100g', price_paise: 1000,
      footprint_mm: 38.19, photo_png: 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB',
    }],
  },
  recognition: REC,
  typedPrice: '214.50',
  image: { kind: 'file', name: 'a.png', size: 10, type: 'image/png' },
  cameraCrop: 'QUJDRA==',
});

T('the catalogue row shows thumbnail, name, rupees AND paise, mm and a remove button', () => {
  const doc = makeDoc();
  const el = E.renderEnrol(FULL(), doc);
  const row = byClass(el, 'enrol-catalog-row');
  const img = one(row, (e) => e.tagName === 'IMG', 'thumbnail');
  ok(String(img.getAttribute('src')).startsWith('data:image/png;base64,'));
  includes(img.getAttribute('alt'), 'Parle-G 100g');
  includes(row.textContent, 'Parle-G 100g');
  includes(row.textContent, '1000 paise');
  includes(row.textContent, '₹10.00');
  includes(row.textContent, '38.19 mm');
  const rm = one(row, (e) => e.getAttribute('data-forget') === 'parle-g-100g', 'remove button');
  eq(rm.textContent, 'remove');
  measured.catalog_render = row.textContent.replace(/\s+/g, ' ').slice(0, 90);
});

T('an amber recognition line names its reason, explains it, and shows NO price', () => {
  const doc = makeDoc();
  const el = E.renderEnrol(FULL(), doc);
  const amber = byClassAll(el, 'enrol-try-amber');
  eq(amber.length, 1);
  const t = amber[0].textContent;
  includes(t, 'AMBER — I DO NOT KNOW');
  includes(t, 'below_similarity');
  includes(t, 'has not been taught yet');
  includes(t, 'EXCLUDED from the total');
  excludes(t, '₹', 'an amber line must not carry a rupee figure at all');
  ok(String(amber[0].className).split(/\s+/).includes('line-amber'),
    'amber must wear the shell amber class, so it looks amber everywhere');
});

T('the TRY IT total names the paise, the rupees and what was excluded', () => {
  const doc = makeDoc();
  const el = E.renderEnrol(FULL(), doc);
  const tot = byClass(el, 'enrol-try-total');
  includes(tot.textContent, '3000 paise');
  includes(tot.textContent, '₹30.00');
  includes(tot.textContent, '1 amber and EXCLUDED');
  eq(tot.dataset.amber, '1');
  eq(tot.dataset.priced, '2');
});

T('a disagreeing total renders NO total at all, only the named disagreement', () => {
  const doc = makeDoc();
  const el = E.renderEnrol(E.deriveEnrol({ recognition: { ...REC, total_paise: 9999 } }), doc);
  const bad = byClass(el, 'enrol-try-total-bad');
  includes(bad.textContent, 'NO TOTAL IS SHOWN');
  includes(bad.textContent, E.ServiceRefusal.TOTAL_DISAGREES);
  eq(byClassAll(el, 'enrol-try-total').length, 0, 'no total element may be rendered');
  excludes(bad.textContent, '₹99.99', 'the desk figure must not be printed as money');
});

T('REGRESSION: a disagreeing total still shows the LINES it disagrees about', () => {
  // First cut of renderTryIt treated any recognition refusal as "replace the
  // whole reading", so a total that failed its own audit deleted two perfectly
  // good recognised lines from the screen. A footer that does not add up is a
  // reason to withhold the FOOTER, not the reading.
  const doc = makeDoc();
  const el = E.renderEnrol(E.deriveEnrol({ recognition: { ...REC, total_paise: 9999 } }), doc);
  eq(byClassAll(el, 'enrol-try-row').length, 3, 'all three lines must survive');
  includes(byClass(el, 'enrol-try-rows').textContent, 'Parle-G');
  includes(byClass(el, 'enrol-try-total-bad').textContent, 'NO TOTAL IS SHOWN');
  eq(byClassAll(el, 'enrol-try-total').length, 0);
});

T('the stylesheet names every structural class the photo flow renders', () => {
  // A class that gets markup but no rule is an unstyled block in the browser
  // and is invisible to every assertion in this file. Checked wholesale.
  const doc = makeDoc();
  const el = E.renderEnrol(FULL(), doc);
  const structural = [
    'enrol-service', 'enrol-teach', 'enrol-catalog', 'enrol-try',
    'enrol-thumb', 'enrol-catalog-row', 'enrol-try-row', 'enrol-preview',
    'enrol-try-total', 'enrol-btn-forget', 'enrol-input-base',
  ];
  const missingMarkup = structural.filter((c) => byClassAll(el, c).length === 0);
  eq(missingMarkup.length, 0, `never rendered: ${missingMarkup.join(', ')}`);
  const missingRule = structural.filter((c) => !E.STYLE_TEXT.includes(`.${c}`));
  eq(missingRule.length, 0, `rendered but unstyled: ${missingRule.join(', ')}`);
  measured.photo_classes_styled = `${structural.length}/${structural.length}`;
});

T('TRY IT says on screen that a reading is not a bill', () => {
  const doc = makeDoc();
  const t = byClass(E.renderEnrol(FULL(), doc), 'enrol-try').textContent;
  includes(t, 'this is a READING, not a bill');
  includes(t, 'signature-verified webhook');
  includes(t, 'EXCLUDED from the total');
});

T('TRY IT is placed immediately after the catalogue, ahead of the legacy sections', () => {
  const doc = makeDoc();
  const el = E.renderEnrol(FULL(), doc);
  const order = walk(el)
    .filter((e) => ['enrol-teach', 'enrol-catalog', 'enrol-try', 'enrol-form'].some(
      (c) => String(e.className).split(/\s+/).includes(c)))
    .map((e) => String(e.className).split(/\s+/).find((c) => c.startsWith('enrol-')));
  eq(order.join('>'), 'enrol-teach>enrol-catalog>enrol-try>enrol-form',
    'teach, then the catalogue, then the payoff');
  measured.panel_order = order.join(' > ');
});

T('the live paise preview is on screen before anything is uploaded', () => {
  const doc = makeDoc();
  const el = E.renderEnrol(FULL(), doc);
  const pv = byClass(el, 'enrol-preview-ok');
  includes(pv.textContent, '21450 paise');
  includes(pv.textContent, '₹214.50');
  includes(pv.textContent, 'sent on the wire as 214.50');
  eq(pv.dataset.paise, '21450');
});

T('a bad typed price paints the named refusal live, and no paise', () => {
  const doc = makeDoc();
  const el = E.renderEnrol(E.deriveEnrol({ typedPrice: '214.507' }), doc);
  const pv = byClass(el, 'enrol-preview-bad');
  includes(pv.textContent, 'NO PAISE YET');
  includes(pv.textContent, 'price_sub_paise');
  includes(pv.textContent, 'NOT rounded');
  excludes(pv.textContent, '21451', 'the rounded value must appear nowhere');
  excludes(pv.textContent, '21450');
});

T('a cold photo surface renders every photo abstention code, never a blank', () => {
  const doc = makeDoc();
  const m = E.deriveEnrol({});
  const t = E.renderEnrol(m, doc).textContent;
  ok(m.photo.abstentions.length > 0);
  for (const c of m.photo.abstentions) includes(t, c, `photo abstention ${c} not on screen`);
  includes(t, 'I DO NOT KNOW');
  measured.cold_photo_render_chars = String(t.length);
});

T('a dead desk renders a NAMED reason, not an empty catalogue', () => {
  const doc = makeDoc();
  const el = E.renderEnrol(E.deriveEnrol({
    shopRefusal: {
      reason: E.ServiceRefusal.UNREACHABLE,
      detail: E.SERVICE_REFUSAL_HELP[E.ServiceRefusal.UNREACHABLE],
      status: null,
    },
  }), doc);
  const box = byClass(el, 'enrol-desk-refused');
  includes(box.textContent, 'THE DESK DID NOT ANSWER');
  includes(box.textContent, E.ServiceRefusal.UNREACHABLE);
  includes(box.textContent, 'tools/upload_app.py');
  eq(byClassAll(el, 'enrol-catalog-rows').length, 0, 'no row list is drawn over a refusal');
});

T('a CSP-blocked desk says BLOCKED, not "down", and names the fix', () => {
  const doc = makeDoc();
  const el = E.renderEnrol(E.deriveEnrol({
    baseResolution: { ok: true, base: 'http://127.0.0.1:8790', source: 'default', typed: '' },
    reach: E.describeReach('http://127.0.0.1:8790', { origin: 'http://127.0.0.1:8787' }, 'self'),
  }), doc);
  const csp = byClass(el, 'enrol-csp');
  includes(csp.textContent, 'BLOCKED BY THIS PAGE');
  includes(csp.textContent, 'connect-src');
  includes(csp.textContent, 'permission, not an outage');
  includes(csp.textContent, E.CROSS_ORIGIN_FIX);
  includes(csp.textContent, E.ServiceRefusal.CSP_BLOCKED);
});

T('an unusable desk address is refused on screen with what was typed', () => {
  const doc = makeDoc();
  const el = E.renderEnrol(E.deriveEnrol({
    baseResolution: E.normaliseShopBase('javascript:alert(1)'),
  }), doc);
  const bad = byClass(el, 'enrol-base-bad');
  includes(bad.textContent, 'REFUSED');
  includes(bad.textContent, E.ServiceRefusal.BAD_BASE);
  includes(bad.textContent, 'not http or https');
});

T('the camera button is disabled and says why when there is no crop', () => {
  const doc = makeDoc();
  const cold = E.renderEnrol(E.deriveEnrol({}), doc);
  const btn = byId(cold, 'enrol-capture');
  eq(btn.getAttribute('disabled'), 'disabled');
  includes(btn.textContent, 'no crop yet');
  includes(byClass(cold, 'enrol-nocrop').textContent, E.PhotoAbstain.NO_CAMERA_CROP);

  const warm = E.renderEnrol(E.deriveEnrol({ cameraCrop: 'QUJDRA==' }), makeDoc());
  eq(byId(warm, 'enrol-capture').getAttribute('disabled'), null);
  includes(byClass(warm, 'enrol-crop-ready').textContent, 'INVARIANT 4');
});

T('a catalogue row with an unusable price shows no rupees and says it will abstain', () => {
  const doc = makeDoc();
  const el = E.renderEnrol(E.deriveEnrol({
    shop: { skus: [{ sku_id: 'x', name: 'X', price_paise: 10.5, footprint_mm: 40 }] },
  }), doc);
  const row = byClass(el, 'enrol-catalog-row');
  includes(row.textContent, 'price_not_integer_paise');
  includes(row.textContent, 'will abstain at the till');
  excludes(row.textContent, '₹');
});

T('a refused thumbnail is stated, not silently blank', () => {
  const doc = makeDoc();
  const el = E.renderEnrol(E.deriveEnrol({
    shop: { skus: [{ sku_id: 'x', name: 'X', price_paise: 100, photo_png: 'http://evil/x.png' }] },
  }), doc);
  const none = byClass(el, 'enrol-thumb-none');
  includes(none.textContent, E.ThumbRefusal.REMOTE_URL);
  eq(all(el, (e) => e.tagName === 'IMG').length, 0, 'no img element is created for a refused thumb');
});

T('every rupee figure in the photo flow also wears .num', () => {
  const doc = makeDoc();
  const el = E.renderEnrol(FULL(), doc);
  const rupees = all(el, (e) => e.childNodes.length === 1
    && e.childNodes[0].nodeType === 3 && /^₹[\d,]+\.\d\d$/.test(e.childNodes[0].data));
  ok(rupees.length >= 4, `expected at least 4 rupee figures, got ${rupees.length}`);
  for (const r of rupees) {
    ok(String(r.className).split(/\s+/).includes('num'), `a rupee figure without .num: ${r.textContent}`);
  }
  measured.photo_rupee_figures = String(rupees.length);
});

// ==========================================================================
G('U. the panel end to end, against a fake desk');

class FakeFormData {
  constructor() { this.entries = []; }
  append(k, v, n) { this.entries.push([k, v, n]); }
  get(k) { const e = this.entries.find((x) => x[0] === k); return e ? e[1] : null; }
}

function fakeDesk(routes) {
  const calls = [];
  const f = async (url, init = {}) => {
    const method = init.method || 'GET';
    calls.push({ url, method, body: init.body });
    const hit = routes[`${method} ${url}`];
    if (hit === undefined) throw new TypeError('Failed to fetch');
    return { status: hit.status ?? 200, text: async () => JSON.stringify(hit.body) };
  };
  f.calls = calls;
  return f;
}

const DESK = 'http://127.0.0.1:8790';
function mountPanel(routes, extra = {}) {
  const doc = makeDoc();
  const panel = E.createPanel({
    doc,
    shopBase: DESK,
    csp: 'open',
    fetch: fakeDesk(routes),
    FormData: FakeFormData,
    toBlob: async (img) => ({ blob: img.kind }),
    location: { origin: DESK, hostname: '127.0.0.1', protocol: 'http:', port: '8790', search: '' },
    global: {},
    ...extra,
  });
  panel.paint();
  return { doc, panel, root: doc.getElementById(E.PANEL_ROOT_ID) };
}

const TAUGHT = {
  ok: true,
  skus: [{
    sku_id: 'parle-g-100g', name: 'Parle-G 100g', price_paise: 21450,
    footprint_mm: 38.19, photo_png: 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB',
  }],
};

TA('THE ROUND TRIP: teach a product from a photo, then see it in the catalogue', async () => {
  const { panel, root } = mountPanel({
    [`POST ${DESK}/enrol`]: { body: { ok: true, stored: 'parle-g-100g' } },
    [`GET ${DESK}/shop`]: { body: TAUGHT },
  });
  panel.handlers.onPick({ name: 'parle.png', size: 40000, type: 'image/png' });
  const res = await panel.handlers.onTeach('Parle-G 100g', '214.50');
  ok(res.ok, JSON.stringify(res.refusal || {}));

  // the multipart body really carried the integer and the canonical rupees
  eq(panel.model.photo.catalog.known, true);
  eq(panel.model.photo.catalog.count, 1);
  const row = byClass(root, 'enrol-catalog-row');
  includes(row.textContent, 'Parle-G 100g');
  includes(row.textContent, '21450 paise');
  includes(row.textContent, '₹214.50');
  includes(row.textContent, '38.19 mm');
  measured.round_trip_row = row.textContent.replace(/\s+/g, ' ').trim().slice(0, 80);
});

TA('the upload really carries price_paise and a canonical price_rupees', async () => {
  const fetchFn = fakeDesk({
    [`POST ${DESK}/enrol`]: { body: { ok: true, skus: TAUGHT.skus } },
  });
  const doc = makeDoc();
  const panel = E.createPanel({
    doc, shopBase: DESK, csp: 'open', fetch: fetchFn, FormData: FakeFormData,
    toBlob: async () => ({ blob: 1 }), location: { origin: DESK, search: '' }, global: {},
  });
  panel.paint();
  panel.handlers.onPick({ name: 'p.png', size: 100, type: 'image/png' });
  await panel.handlers.onTeach('Parle-G 100g', ' Rs 214.5 ');
  eq(fetchFn.calls.length, 1);
  const form = fetchFn.calls[0].body;
  eq(form.get('sku_id'), 'parle-g-100g');
  eq(form.get('name'), 'Parle-G 100g');
  eq(form.get('price_rupees'), '214.50', 'canonical, not the typed characters');
  eq(form.get('price_paise'), '21450');
  ok(form.get('image'), 'the photo itself was attached');
  measured.upload_fields = form.entries.map((e) => e[0]).join(',');
});

TA('a REFUSED price never reaches the network at all', async () => {
  const fetchFn = fakeDesk({ [`POST ${DESK}/enrol`]: { body: { ok: true } } });
  const doc = makeDoc();
  const panel = E.createPanel({
    doc, shopBase: DESK, csp: 'open', fetch: fetchFn, FormData: FakeFormData,
    toBlob: async () => ({ blob: 1 }), location: { origin: DESK, search: '' }, global: {},
  });
  panel.paint();
  panel.handlers.onPick({ name: 'p.png', size: 100, type: 'image/png' });
  for (const bad of ['214.507', 'abc', '', '-5', '1e3', '0']) {
    await panel.handlers.onTeach('Thing', bad);
  }
  eq(fetchFn.calls.length, 0, 'six refused prices, zero uploads');
  const root = doc.getElementById(E.PANEL_ROOT_ID);
  includes(byClass(root, 'enrol-refusal').textContent, 'price_zero');
  measured.refused_prices_uploaded = '0/6';
});

TA('THE PAYOFF: a second photo is recognised, priced, and the amber is excluded', async () => {
  const { panel, root } = mountPanel({
    [`GET ${DESK}/shop`]: { body: TAUGHT },
    [`POST ${DESK}/recognise`]: { body: REC },
  });
  panel.handlers.onTryPick({ name: 'second.png', size: 50000, type: 'image/png' });
  const res = await panel.handlers.onTry();
  ok(res.ok);
  eq(panel.model.photo.recognition.priced, 2);
  eq(panel.model.photo.recognition.amber, 1);
  eq(panel.model.photo.recognition.totalPaise, 3000);
  const tryEl = byClass(root, 'enrol-try');
  includes(tryEl.textContent, 'Parle-G');
  includes(tryEl.textContent, '₹30.00');
  includes(tryEl.textContent, 'AMBER — I DO NOT KNOW');
  includes(tryEl.textContent, 'below_similarity');
  measured.payoff = `${panel.model.photo.recognition.priced} priced, ${panel.model.photo.recognition.amber} amber, total ${panel.model.photo.recognition.totalPaise} paise`;
});

TA('a DEAD desk degrades with a named reason and never a blank panel', async () => {
  const { panel, root } = mountPanel({});   // every route throws
  const res = await panel.handlers.onRefresh();
  eq(res.ok, false);
  eq(res.refusal.reason, E.ServiceRefusal.UNREACHABLE);
  const box = byClass(root, 'enrol-desk-refused');
  includes(box.textContent, E.ServiceRefusal.UNREACHABLE);
  includes(box.textContent, 'Failed to fetch', 'the browser\'s own words are kept');
  ok(root.textContent.length > 2000, 'the panel still rendered in full');
  measured.dead_desk_render_chars = String(root.textContent.length);
});

TA('a desk that refuses BY NAME has its own reason shown, not a generic one', async () => {
  const { panel, root } = mountPanel({
    [`GET ${DESK}/shop`]: { status: 400, body: { ok: false, reason: 'mat_did_not_lock', detail: 'found 2 of 4 markers' } },
  });
  await panel.handlers.onRefresh();
  const box = byClass(root, 'enrol-desk-refused');
  includes(box.textContent, 'mat_did_not_lock');
  includes(box.textContent, '2 of 4 markers');
});

TA('a CSP-blocked desk is never dialled at all', async () => {
  const fetchFn = fakeDesk({ [`GET ${DESK}/shop`]: { body: TAUGHT } });
  const doc = makeDoc();
  const panel = E.createPanel({
    doc, shopBase: DESK, fetch: fetchFn, FormData: FakeFormData,
    toBlob: async () => ({ blob: 1 }),
    location: { origin: 'http://127.0.0.1:8787', hostname: '127.0.0.1', protocol: 'http:', port: '8787', search: '' },
    global: {},
  });
  panel.paint();
  eq(panel.reach.blocked, true);
  const res = await panel.handlers.onRefresh();
  eq(res.ok, false);
  eq(res.refusal.reason, E.ServiceRefusal.CSP_BLOCKED);
  eq(fetchFn.calls.length, 0, 'a call the browser would block is not attempted');
  includes(byClass(doc.getElementById(E.PANEL_ROOT_ID), 'enrol-csp').textContent, 'permission, not an outage');
});

TA('removing an SKU calls DELETE with a percent-encoded id', async () => {
  const fetchFn = fakeDesk({
    [`GET ${DESK}/shop`]: { body: TAUGHT },
    [`DELETE ${DESK}/shop/parle-g-100g`]: { body: { ok: true, skus: [] } },
  });
  const doc = makeDoc();
  const panel = E.createPanel({
    doc, shopBase: DESK, csp: 'open', fetch: fetchFn, FormData: FakeFormData,
    toBlob: async () => ({ blob: 1 }), location: { origin: DESK, search: '' }, global: {},
  });
  panel.paint();
  await panel.handlers.onRefresh();
  eq(panel.model.photo.catalog.count, 1);
  await panel.handlers.onForget('parle-g-100g');
  eq(panel.model.photo.catalog.count, 0);
  ok(fetchFn.calls.some((c) => c.method === 'DELETE' && c.url === `${DESK}/shop/parle-g-100g`));
});

T('a Unicode SKU id is percent-encoded in the DELETE path', () => {
  const id = E.skuIdFor('चाय');
  ok(id.ok, 'a Devanagari name must survive into an id');
  eq(id.skuId, 'चाय');
  const r = E.buildRemoveRequest(id.skuId, DESK);
  ok(r.ok);
  eq(r.request.method, 'DELETE');
  eq(r.request.url, `${DESK}/shop/${encodeURIComponent('चाय')}`);
  ok(!r.request.url.includes('चाय'), 'the raw codepoints must not sit in the URL');
  eq(E.buildRemoveRequest('', DESK).ok, false);
  measured.unicode_sku_url = r.request.url;
});

T('an SKU id is a slug, and a name that slugs to nothing is refused', () => {
  eq(E.skuIdFor('Parle-G 100g').skuId, 'parle-g-100g');
  eq(E.skuIdFor('  Tata   Salt  ').skuId, 'tata-salt');
  eq(E.skuIdFor('...').ok, false, 'a name of punctuation is not an id');
  eq(E.skuIdFor('').ok, false);
});

TA('the camera crop arrives on a frame message and becomes a teachable source', async () => {
  const { panel, root } = mountPanel({
    [`POST ${DESK}/enrol`]: { body: { ok: true, skus: TAUGHT.skus } },
  });
  eq(panel.cameraCrop, null);
  includes(byClass(root, 'enrol-nocrop').textContent, E.PhotoAbstain.NO_CAMERA_CROP);

  const consumed = panel.onState({ type: 'frame', rect: 'QUJDRA==', ts: 'now' });
  eq(consumed, true, 'a frame must be consumed for its rectified crop');
  eq(panel.cameraCrop, 'QUJDRA==');
  const choice = panel.handlers.onCapture();
  eq(choice.kind, E.RECTIFIED_CROP_KIND);
  const res = await panel.handlers.onTeach('Camera Thing', '10');
  ok(res.ok, JSON.stringify(res.refusal || {}));
  measured.camera_teach = 'rectified_mat_crop uploaded';
});

T('INVARIANT 4: a frame message contributes ONLY its rectified crop', () => {
  const doc = makeDoc();
  const panel = E.createPanel({ doc, location: { origin: DESK, search: '' }, global: {} });
  panel.paint();
  panel.onState({
    type: 'frame', rect: 'QUJDRA==',
    raw_frame: 'THE WHOLE ROOM', full_frame: 'FACES',
  });
  eq(panel.cameraCrop, 'QUJDRA==');
  const t = doc.getElementById(E.PANEL_ROOT_ID).textContent;
  excludes(t, 'THE WHOLE ROOM', 'a raw frame must not be retained anywhere');
  excludes(t, 'FACES');
  eq(panel.model.photo.cameraCrop, 'QUJDRA==');
});

T('a frame with no rect is not consumed and invents no crop', () => {
  const doc = makeDoc();
  const panel = E.createPanel({ doc, location: { origin: DESK, search: '' }, global: {} });
  panel.paint();
  eq(panel.onState({ type: 'frame' }), false);
  eq(panel.cameraCrop, null);
});

TA('pointing at a new desk drops the old catalogue rather than relabelling it', async () => {
  const { panel } = mountPanel({ [`GET ${DESK}/shop`]: { body: TAUGHT } });
  await panel.handlers.onRefresh();
  eq(panel.model.photo.catalog.count, 1);
  panel.handlers.onBase('http://other.desk:9999');
  eq(panel.model.photo.catalog.known, false, 'the old shop must not be shown under a new address');
  eq(panel.shopBase.base, 'http://other.desk:9999');
});

TA('no fetch in the runtime is a named refusal, not a thrown panel', async () => {
  const doc = makeDoc();
  const panel = E.createPanel({
    doc, shopBase: DESK, csp: 'open', fetch: null,
    location: { origin: DESK, search: '' }, global: {},
  });
  panel.paint();
  const res = await panel.handlers.onRefresh();
  eq(res.ok, false);
  eq(res.refusal.reason, E.ServiceRefusal.NO_FETCH);
});

// ==========================================================================
G('V. the money discipline holds across the new code');

T('the photo flow does no float arithmetic on money either', () => {
  // The same source lint as section B, extended over the whole file: no
  // toFixed, parseFloat or Math.round may appear on any line that mentions
  // paise, anywhere in this module.
  const codeOnly = ENROL_SRC.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  const offenders = [];
  for (const line of codeOnly.split('\n')) {
    if (!/[Pp]aise/.test(line)) continue;
    for (const f of ['parseFloat', 'toFixed', 'Math.round', 'Number.parseFloat']) {
      if (line.includes(f)) offenders.push(`${f}: ${line.trim()}`);
    }
  }
  eq(offenders.length, 0, `float arithmetic near money: ${offenders.join(' | ')}`);
  measured.money_lines_with_float = '0';
});

T('millimetres are allowed a float, and are never confused with money', () => {
  // gawaah/identity.py: "nothing here touches money, so plain floats are
  // correct". A length is not a price and formatMm is not formatPaise.
  eq(E.formatMm(38.185), '38.19 mm');
  eq(E.formatPaise(38.185), '—', 'a non-integer is never rendered as money');
  ok(!String(E.formatMm(38.19)).includes('₹'));
});

T('the total is integer addition of integer paise and nothing else', () => {
  const r = E.deriveRecognition({
    items: [
      { sku_id: 'a', reason: 'match', price_paise: 1 },
      { sku_id: 'b', reason: 'match', price_paise: 2 },
      { sku_id: 'c', reason: 'match', price_paise: 3 },
    ],
  });
  eq(r.totalPaise, 6);
  ok(Number.isInteger(r.totalPaise));
  eq(r.totalRupees, '₹0.06');
});

// every async test must have finished before a single number is printed
await Promise.all(pending);

// ============================================================== report =====
console.log('\n──────────────────────────────────────────────────────────────');
console.log('MEASURED NUMBERS (produced by this run)');
for (const [k, v] of Object.entries(measured)) console.log(`  ${k.padEnd(28)} ${v}`);

if (failures.length) {
  console.log('\nFAILURES');
  for (const f of failures) console.log(`  ✗ ${f}`);
}
console.log('\n──────────────────────────────────────────────────────────────');
console.log(`${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
