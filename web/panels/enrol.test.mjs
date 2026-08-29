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
