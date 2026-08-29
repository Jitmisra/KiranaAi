/* LEDGER panel — KAALA DABBA, the audit chain, with a button a judge can press.
 * ===========================================================================
 *
 * WHAT THIS PANEL IS FOR
 * Every money action and every perception decision appends one line to an
 * append-only, SHA-256 hash-chained log. `prev_hash` is INSIDE the hashed
 * payload, so deleting or reordering a line breaks every hash after it, not
 * just the tampered one. This panel shows the head hash, the line count, the
 * last N entries with their module / reason code / money action, and — the
 * point of the whole exercise — a VERIFY button that recomputes the chain from
 * genesis in the browser and reports either VERIFIED or the EXACT LINE where it
 * breaks.
 *
 * THE HARD PART, AND WHY THIS FILE IS LONGER THAN IT LOOKS
 * The chain is only verifiable if the browser can reproduce the writer's bytes
 * exactly. gawaah/ledger.py hashes
 *     json.dumps(payload, sort_keys=True, separators=(",",":"), ensure_ascii=False)
 * so this file reimplements THAT serialisation, not "some JSON". Two traps:
 *
 *   1. FLOAT FORMATTING. Python writes the float 60.0 as "60.0"; JavaScript's
 *      Number 60 stringifies as "60". If we JSON.parse a line and re-serialise
 *      it, every integer-valued float in it changes bytes and the chain reports
 *      a break THAT IS NOT THERE. A verifier that cries tamper on clean data is
 *      worse than no verifier. So this file ships its own JSON reader that
 *      PRESERVES THE ORIGINAL NUMBER LITERAL and re-emits it verbatim. Round
 *      trip: the literal in the file is repr(float), and repr is idempotent, so
 *      the literal is exactly what Python would write again.
 *
 *   2. VERIFYING FROM ALREADY-PARSED OBJECTS. If the host hands us parsed JS
 *      objects instead of the original lines, trap 1 is unavoidable — the
 *      original bytes are gone. Rather than claim VERIFIED (or, worse, claim
 *      BROKEN) on a re-serialisation we cannot vouch for, the verdict in that
 *      case is INDETERMINATE with the reason spelled out. Invariant 7 applies
 *      to the panel's own verification, not only to the perception modules.
 *
 * The verifier is also deliberately standalone in the same sense as the Python
 * one: it does not reuse a "writer" here, because a bug shared between writer
 * and verifier cannot be caught by the verifier.
 *
 * Everything above `createPanel` is pure (`verifyChain` is async only because
 * SubtleCrypto is). No browser required; panels2.test.mjs runs it under node.
 */

import { formatRupees, paise, MoneyError } from '../app.js';

export const PANEL_ID = 'panel-ledger';
export const PANEL_TITLE = 'KAALA DABBA — audit chain';

export const GENESIS = '0'.repeat(64);
export const DEFAULT_LAST_N = 12;

// ===========================================================================
// 1. CANONICAL JSON — byte-compatible with gawaah/ledger.py `canonical()`
// ===========================================================================

export class LedgerJsonError extends Error {
  constructor(msg, pos) { super(msg); this.name = 'LedgerJsonError'; this.pos = pos; }
}

/**
 * Compare two keys by UNICODE CODE POINT, which is what Python's `sorted()`
 * does to strings. JavaScript's default sort compares UTF-16 code units, and
 * the two disagree for astral characters. Ledger keys are ASCII identifiers, so
 * this never bites in practice — it is here so that it cannot start to.
 */
export function cmpCodePoints(a, b) {
  const A = Array.from(a);
  const B = Array.from(b);
  const n = Math.min(A.length, B.length);
  for (let i = 0; i < n; i++) {
    const x = A[i].codePointAt(0);
    const y = B[i].codePointAt(0);
    if (x !== y) return x < y ? -1 : 1;
  }
  return A.length - B.length;
}

const WS = new Set([' ', '\t', '\n', '\r']);
const NUM_RE = /-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/y;

/**
 * A JSON reader that keeps the original text of every number.
 *
 * Returns a node tree:
 *   {t:'obj', entries:[[key, node], ...]}   {t:'arr', items:[node]}
 *   {t:'num', raw:'1.0'}                    {t:'str', v:'…'}
 *   {t:'lit', raw:'true'|'false'|'null'}
 *
 * Also accepts Python's `Infinity`, `-Infinity` and `NaN`, which json.dumps
 * emits by default and JSON.parse rejects — a ledger containing one must still
 * be verifiable rather than unreadable.
 */
export function parseJsonNode(text) {
  if (typeof text !== 'string') throw new LedgerJsonError('not a string', 0);
  let i = 0;

  const skip = () => { while (i < text.length && WS.has(text[i])) i++; };
  const fail = (msg) => { throw new LedgerJsonError(`${msg} at position ${i}`, i); };

  function value() {
    skip();
    if (i >= text.length) fail('unexpected end of input');
    const ch = text[i];
    if (ch === '{') return object();
    if (ch === '[') return array();
    if (ch === '"') return { t: 'str', v: string() };
    for (const lit of ['true', 'false', 'null', 'Infinity', '-Infinity', 'NaN']) {
      if (text.startsWith(lit, i)) {
        i += lit.length;
        return lit === 'true' || lit === 'false' || lit === 'null'
          ? { t: 'lit', raw: lit }
          : { t: 'num', raw: lit };
      }
    }
    NUM_RE.lastIndex = i;
    const m = NUM_RE.exec(text);
    if (m && m.index === i) { i += m[0].length; return { t: 'num', raw: m[0] }; }
    return fail(`unexpected character ${JSON.stringify(ch)}`);
  }

  function object() {
    i++; // {
    const entries = [];
    skip();
    if (text[i] === '}') { i++; return { t: 'obj', entries }; }
    for (;;) {
      skip();
      if (text[i] !== '"') fail('expected an object key');
      const k = string();
      skip();
      if (text[i] !== ':') fail('expected ":"');
      i++;
      entries.push([k, value()]);
      skip();
      if (text[i] === ',') { i++; continue; }
      if (text[i] === '}') { i++; return { t: 'obj', entries }; }
      fail('expected "," or "}"');
    }
  }

  function array() {
    i++; // [
    const items = [];
    skip();
    if (text[i] === ']') { i++; return { t: 'arr', items }; }
    for (;;) {
      items.push(value());
      skip();
      if (text[i] === ',') { i++; continue; }
      if (text[i] === ']') { i++; return { t: 'arr', items }; }
      fail('expected "," or "]"');
    }
  }

  function string() {
    i++; // opening quote
    let out = '';
    for (;;) {
      if (i >= text.length) fail('unterminated string');
      const ch = text[i];
      if (ch === '"') { i++; return out; }
      if (ch === '\\') {
        i++;
        const e = text[i];
        i++;
        if (e === 'u') {
          const hex = text.slice(i, i + 4);
          if (!/^[0-9a-fA-F]{4}$/.test(hex)) fail('bad \\u escape');
          out += String.fromCharCode(Number.parseInt(hex, 16));
          i += 4;
        } else {
          const simple = { '"': '"', '\\': '\\', '/': '/', b: '\b', f: '\f', n: '\n', r: '\r', t: '\t' };
          if (!(e in simple)) fail(`bad escape \\${e}`);
          out += simple[e];
        }
        continue;
      }
      out += ch;
      i++;
    }
  }

  const v = value();
  skip();
  if (i !== text.length) fail('trailing content');
  return v;
}

/**
 * Serialise a node tree the way `gawaah.ledger.canonical` serialises a dict:
 * sorted keys, no spaces, ensure_ascii=False, original number literals.
 */
export function canonicalizeNode(node) {
  switch (node.t) {
    case 'obj': {
      // last key wins, exactly as both Python's json.loads and a JS object do
      const seen = new Map();
      for (const [k, v] of node.entries) seen.set(k, v);
      const keys = [...seen.keys()].sort(cmpCodePoints);
      return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalizeNode(seen.get(k))}`).join(',')}}`;
    }
    case 'arr':
      return `[${node.items.map(canonicalizeNode).join(',')}]`;
    case 'num':
      return node.raw;
    case 'str':
      return JSON.stringify(node.v);
    case 'lit':
      return node.raw;
    default:
      throw new LedgerJsonError(`unknown node type ${node && node.t}`, 0);
  }
}

/** Node tree -> ordinary JS value, for display. Numbers lose their literal. */
export function nodeToPlain(node) {
  switch (node.t) {
    case 'obj': {
      const o = {};
      for (const [k, v] of node.entries) o[k] = nodeToPlain(v);
      return o;
    }
    case 'arr': return node.items.map(nodeToPlain);
    case 'num':
      if (node.raw === 'NaN') return NaN;
      if (node.raw === 'Infinity') return Infinity;
      if (node.raw === '-Infinity') return -Infinity;
      return Number(node.raw);
    case 'str': return node.v;
    case 'lit': return node.raw === 'null' ? null : node.raw === 'true';
    default: throw new LedgerJsonError(`unknown node type ${node && node.t}`, 0);
  }
}

/**
 * Canonicalise an ORDINARY JS value the same way. Used only on the
 * re-serialised path, where the original literals are already lost — see the
 * INDETERMINATE verdict.
 */
export function canonicalizeValue(v) {
  if (v === null) return 'null';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (typeof v === 'number') {
    if (Number.isNaN(v)) return 'NaN';
    if (v === Infinity) return 'Infinity';
    if (v === -Infinity) return '-Infinity';
    return String(v);
  }
  if (typeof v === 'string') return JSON.stringify(v);
  if (Array.isArray(v)) return `[${v.map(canonicalizeValue).join(',')}]`;
  if (typeof v === 'object') {
    const keys = Object.keys(v).sort(cmpCodePoints);
    return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalizeValue(v[k])}`).join(',')}}`;
  }
  throw new LedgerJsonError(`not JSON-serialisable: ${typeof v}`, 0);
}

/**
 * True when re-serialising this ALREADY-PARSED value is guaranteed to reproduce
 * the writer's bytes.
 *
 * The rule is harsher than it first looks: ANY number at all disqualifies the
 * value. Not because integers serialise differently — they do not — but because
 * once a line has been through JSON.parse there is no way left to tell whether
 * the writer wrote `120` or `120.0`. Both arrive as the JavaScript number 120,
 * and only one of them re-serialises to the original bytes. Certifying that
 * case as exact is what would turn a formatting difference into a false
 * accusation of tampering, which is the one outcome this file exists to avoid.
 */
export function reserialisationIsExact(v) {
  if (v === null || typeof v === 'boolean' || typeof v === 'string') return true;
  if (typeof v === 'number') return false;
  if (Array.isArray(v)) return v.every(reserialisationIsExact);
  if (typeof v === 'object') return Object.values(v).every(reserialisationIsExact);
  return false;
}

// ===========================================================================
// 2. HASHING
// ===========================================================================

function subtleOf(opts = {}) {
  if (opts.subtle) return opts.subtle;
  const c = globalThis.crypto;
  return c && c.subtle ? c.subtle : null;
}

function toHex(buf) {
  const b = new Uint8Array(buf);
  let s = '';
  for (let i = 0; i < b.length; i++) s += b[i].toString(16).padStart(2, '0');
  return s;
}

/** SHA-256 of a UTF-8 string, lowercase hex. Async because SubtleCrypto is. */
export async function sha256Hex(text, opts = {}) {
  const subtle = subtleOf(opts);
  if (!subtle) throw new Error('no SubtleCrypto in this context');
  return toHex(await subtle.digest('SHA-256', new TextEncoder().encode(text)));
}

// ===========================================================================
// 3. VERIFY — recompute the chain from genesis
// ===========================================================================

export const VERIFIED = 'VERIFIED';
export const BROKEN = 'BROKEN';
export const INDETERMINATE = 'INDETERMINATE';
export const NOT_RUN = 'NOT_RUN';
export const UNAVAILABLE = 'UNAVAILABLE';

export const VERIFY_VERDICTS = Object.freeze([VERIFIED, BROKEN, INDETERMINATE, NOT_RUN, UNAVAILABLE]);

export const VERIFY_NOTES = Object.freeze({
  [VERIFIED]: 'every line rehashes to the hash it stores, and every prev_hash '
    + 'points at the line before it, all the way back to genesis.',
  [BROKEN]: 'the chain does not rehash. The first line that fails is named below; '
    + 'every line after it is unverifiable regardless of its own contents.',
  [INDETERMINATE]: 'the chain was checked against RE-SERIALISED objects, not the '
    + 'writer’s original bytes, so a mismatch here may be a formatting difference '
    + '(Python writes 60.0, JavaScript writes 60) rather than tampering. Ask the '
    + 'host for the raw ledger lines to get a byte-exact answer.',
  [NOT_RUN]: 'the chain has not been verified in this session. Press VERIFY.',
  [UNAVAILABLE]: 'SHA-256 is not available in this context (no SubtleCrypto), so '
    + 'the chain cannot be recomputed here. The panel will not claim a chain it '
    + 'could not check.',
});

const RESULT_BASE = Object.freeze({
  verdict: NOT_RUN, ok: false, linesChecked: 0, head: GENESIS,
  error: null, brokenLine: null, exact: null, durationMs: null,
});

function splitLines(text) {
  return text.split('\n');
}

/**
 * Verify a hash chain.
 *
 * `source` may be:
 *   - a string: the raw JSONL, verified BYTE-EXACTLY (the honest path);
 *   - an array of strings: the raw lines, same guarantee;
 *   - an array of objects: already-parsed entries. The original bytes are gone,
 *     so a failure is reported as INDETERMINATE, not BROKEN, unless every value
 *     in the chain re-serialises exactly (integers, strings, bools, nulls).
 *
 * Mirrors gawaah.ledger.verify: returns the same four facts plus the exactness
 * of the check itself.
 */
export async function verifyChain(source, opts = {}) {
  const subtle = subtleOf(opts);
  if (!subtle) {
    return Object.freeze({
      ...RESULT_BASE, verdict: UNAVAILABLE,
      error: 'no SubtleCrypto in this context; SHA-256 cannot be computed here',
    });
  }
  const t0 = (globalThis.performance && globalThis.performance.now)
    ? globalThis.performance.now() : 0;

  let rawLines = null;
  let objects = null;
  if (typeof source === 'string') rawLines = splitLines(source);
  else if (Array.isArray(source) && source.every((x) => typeof x === 'string')) rawLines = source;
  else if (Array.isArray(source)) objects = source;
  else if (source === null || source === undefined) rawLines = [];
  else {
    return Object.freeze({
      ...RESULT_BASE, verdict: INDETERMINATE,
      error: `cannot read a chain from ${typeof source}`,
    });
  }

  const exact = rawLines !== null
    ? true
    : objects.every((o) => o && typeof o === 'object' && reserialisationIsExact(o));

  const done = (verdict, linesChecked, head, error, brokenLine = null) => Object.freeze({
    verdict, ok: verdict === VERIFIED, linesChecked, head, error, brokenLine, exact,
    durationMs: ((globalThis.performance && globalThis.performance.now)
      ? globalThis.performance.now() : 0) - t0,
  });
  const failVerdict = exact ? BROKEN : INDETERMINATE;

  let prev = GENESIS;
  let n = 0;

  const units = rawLines !== null
    ? rawLines.map((s, k) => ({ lineNo: k + 1, raw: s }))
    : objects.map((o, k) => ({ lineNo: k + 1, obj: o }));

  for (const u of units) {
    let stored = null;
    let canonicalPayload = null;
    let prevHash;

    if (u.raw !== undefined) {
      if (u.raw.trim() === '') continue;
      let node;
      try { node = parseJsonNode(u.raw); }
      catch (e) { return done(failVerdict, n, prev, `line ${u.lineNo}: not valid JSON: ${e.message}`, u.lineNo); }
      if (node.t !== 'obj') {
        return done(failVerdict, n, prev, `line ${u.lineNo}: not a JSON object`, u.lineNo);
      }
      const kept = [];
      for (const [k, v] of node.entries) {
        if (k === 'hash') stored = v.t === 'str' ? v.v : null;
        else kept.push([k, v]);
      }
      if (stored === null) return done(failVerdict, n, prev, `line ${u.lineNo}: missing hash`, u.lineNo);
      // LAST occurrence wins, matching both json.loads and canonicalizeNode. A
      // duplicated key must not be read differently by the two of them.
      const ph = node.entries.filter(([k]) => k === 'prev_hash').pop();
      prevHash = ph ? (ph[1].t === 'str' ? ph[1].v : null) : null;
      canonicalPayload = canonicalizeNode({ t: 'obj', entries: kept });
    } else {
      const o = u.obj;
      if (!o || typeof o !== 'object' || Array.isArray(o)) {
        return done(failVerdict, n, prev, `line ${u.lineNo}: not a JSON object`, u.lineNo);
      }
      stored = typeof o.hash === 'string' ? o.hash : null;
      if (stored === null) return done(failVerdict, n, prev, `line ${u.lineNo}: missing hash`, u.lineNo);
      prevHash = typeof o.prev_hash === 'string' ? o.prev_hash : null;
      const payload = {};
      for (const k of Object.keys(o)) if (k !== 'hash') payload[k] = o[k];
      canonicalPayload = canonicalizeValue(payload);
    }

    if (prevHash !== prev) {
      return done(failVerdict, n, prev,
        `line ${u.lineNo}: chain break — prev_hash ${prevHash === null ? 'null' : `'${prevHash}'`} `
        + `!= expected '${prev}'`, u.lineNo);
    }

    const recomputed = await sha256Hex(canonicalPayload, { subtle });
    if (recomputed !== stored) {
      return done(failVerdict, n, prev,
        `line ${u.lineNo}: hash mismatch — stored ${stored.slice(0, 16)}… `
        + `recomputed ${recomputed.slice(0, 16)}…`, u.lineNo);
    }
    prev = stored;
    n++;
  }
  return done(VERIFIED, n, prev, null);
}

// ===========================================================================
// 4. WHAT ONE LINE DID TO THE MONEY
// ===========================================================================

/** Fields that mean money, most specific first. Integer paise, never floats. */
export const MONEY_FIELDS = Object.freeze([
  'amount_paise', 'total_paise', 'price_paise', 'authorised_paise',
  'intent_amount_paise', 'frozen_total_paise', 'delta_paise', 'settled_paise',
]);

/**
 * Read the money action off one entry. Never guesses: a line with no money
 * field says so, and a money field that is not integer paise says THAT, rather
 * than rounding it into something printable.
 */
export function moneyActionOf(entry) {
  if (!entry || typeof entry !== 'object') {
    return { known: false, field: null, paise: null, text: 'no entry', reason: 'no_entry' };
  }
  for (const fieldName of MONEY_FIELDS) {
    if (!(fieldName in entry)) continue;
    const v = entry[fieldName];
    if (v === null) {
      return {
        known: false, field: fieldName, paise: null,
        text: 'AMBER — excluded from the total', reason: 'amber_null_price',
      };
    }
    try {
      const p = paise(v);
      return { known: true, field: fieldName, paise: p, text: formatRupees(p), reason: null };
    } catch (e) {
      return {
        known: false, field: fieldName, paise: null,
        text: 'NOT INTEGER PAISE', reason: 'not_integer_paise',
        detail: e instanceof MoneyError ? e.message : String(e && e.message),
      };
    }
  }
  return { known: false, field: null, paise: null, text: 'no money field', reason: 'no_money_field' };
}

/** The reason code a line carries, or an explicit absence. */
export function reasonCodeOf(entry) {
  if (!entry || typeof entry !== 'object') return null;
  for (const k of ['reason', 'reason_code', 'verdict', 'event', 'action']) {
    if (typeof entry[k] === 'string' && entry[k] !== '') return entry[k];
  }
  return null;
}

export function shortHash(h, n = 12) {
  return typeof h === 'string' && h.length > n ? `${h.slice(0, n)}…` : (h ?? '—');
}

// ===========================================================================
// 5. DERIVE
// ===========================================================================

/** Number(null) is 0 and Number('') is 0. Neither is a count; both are absent. */
function num(v, dflt = null) {
  if (v === null || v === undefined || typeof v === 'boolean' || v === '') return dflt;
  const n = Number(v);
  return Number.isFinite(n) ? n : dflt;
}

/**
 * Build the display model.
 *
 * Reads, in order of preference:
 *   state.ledger.jsonl   raw JSONL text          -> byte-exact verification
 *   state.ledger.lines   array of raw line texts -> byte-exact verification
 *   state.ledger.entries array of parsed objects -> INDETERMINATE on failure
 */
export function deriveLedger(state, verification = null, opts = {}) {
  const abstentions = [];
  const note = (where, code, text) => { abstentions.push({ where, code, note: text }); };
  const lastN = Math.max(1, Math.trunc(num(opts.lastN, DEFAULT_LAST_N)));

  const L = (state && typeof state === 'object')
    ? (state.ledger ?? (state.entries || state.jsonl || state.lines ? state : null))
    : null;

  let source = null;
  let sourceKind = 'none';
  let entries = [];
  let parseError = null;

  if (L) {
    if (typeof L.jsonl === 'string') { source = L.jsonl; sourceKind = 'jsonl'; }
    else if (Array.isArray(L.lines) && L.lines.every((x) => typeof x === 'string')) {
      source = L.lines; sourceKind = 'lines';
    } else if (Array.isArray(L.entries)) { source = L.entries; sourceKind = 'entries'; }
  }

  if (sourceKind === 'jsonl' || sourceKind === 'lines') {
    const raw = sourceKind === 'jsonl' ? splitLines(source) : source;
    for (let k = 0; k < raw.length; k++) {
      if (raw[k].trim() === '') continue;
      try { entries.push({ lineNo: entries.length + 1, rec: nodeToPlain(parseJsonNode(raw[k])) }); }
      catch (e) {
        parseError = `line ${k + 1}: not valid JSON: ${e.message}`;
        break;
      }
    }
  } else if (sourceKind === 'entries') {
    entries = source.map((rec, k) => ({ lineNo: k + 1, rec }));
  }

  if (!L) {
    note('chain', 'no_ledger', 'no audit chain has been supplied to this panel.');
  } else if (sourceKind === 'none') {
    note('chain', 'unreadable_ledger',
      'the ledger object carries neither raw JSONL, nor raw lines, nor parsed '
      + 'entries, so there is nothing to display or verify.');
  } else if (sourceKind === 'entries') {
    note('verification', 'reserialised_source',
      'the host supplied PARSED entries rather than the writer’s bytes. The chain '
      + 'can still be recomputed, but a mismatch cannot be distinguished from a '
      + 'number-formatting difference, so a failure will read INDETERMINATE, not '
      + 'BROKEN. Supply ledger.jsonl for a byte-exact answer.');
  }
  if (parseError) note('chain', 'malformed_line', parseError);

  // ---- head and count --------------------------------------------------
  const reportedHead = typeof (L && L.head) === 'string' ? L.head : null;
  let observedHead = null;
  if (parseError) {
    // The read stopped early. Whatever the last GOOD line stored is not the
    // head of this chain, and offering it as one would be a quiet lie.
    observedHead = null;
  } else if (entries.length) {
    const last = entries[entries.length - 1].rec;
    observedHead = typeof last.hash === 'string' ? last.hash : null;
  } else if (sourceKind !== 'none') {
    // A source was supplied and it held no lines: the head IS genesis. An
    // UNREADABLE source is a different thing and must not borrow that answer.
    observedHead = GENESIS;
  }
  const head = reportedHead ?? observedHead;
  if (!head) {
    note('head', 'head_unknown', 'no head hash was supplied and none could be read from the lines.');
  }
  if (reportedHead && observedHead && reportedHead !== observedHead) {
    note('head', 'head_disagrees',
      `the host reports head ${shortHash(reportedHead)} but the last line stores `
      + `${shortHash(observedHead)}. Showing both; believing neither.`);
  }

  const reportedCount = num(L && L.count, null);
  const observedCount = entries.length;
  if (reportedCount !== null && Math.trunc(reportedCount) !== observedCount && sourceKind !== 'none') {
    note('count', 'count_disagrees',
      `the host reports ${Math.trunc(reportedCount)} lines but ${observedCount} were `
      + 'read here. The panel shows the number it counted.');
  }

  // ---- the last N rows -------------------------------------------------
  const tail = entries.slice(-lastN).map(({ lineNo, rec }) => {
    const money = moneyActionOf(rec);
    return Object.freeze({
      lineNo,
      ts: typeof rec.ts === 'string' ? rec.ts : null,
      module: typeof rec.module === 'string' ? rec.module : null,
      reason: reasonCodeOf(rec),
      money: Object.freeze(money),
      hash: typeof rec.hash === 'string' ? rec.hash : null,
      prevHash: typeof rec.prev_hash === 'string' ? rec.prev_hash : null,
      raw: rec,
    });
  });
  for (const r of tail) {
    if (!r.module) note('entries', 'module_missing', `line ${r.lineNo} carries no module name.`);
  }

  const v = verification ?? RESULT_BASE;

  return Object.freeze({
    id: PANEL_ID,
    title: PANEL_TITLE,
    present: Boolean(L) && sourceKind !== 'none',
    sourceKind,
    canVerifyExactly: sourceKind === 'jsonl' || sourceKind === 'lines',
    head,
    headShort: shortHash(head, 16),
    reportedHead,
    observedHead,
    count: observedCount,
    reportedCount: reportedCount === null ? null : Math.trunc(reportedCount),
    lastN,
    entries: Object.freeze(tail),
    genesis: GENESIS,
    verification: Object.freeze({ ...v, note: VERIFY_NOTES[v.verdict] ?? '' }),
    abstentions: Object.freeze(abstentions.map((a) => Object.freeze(a))),
  });
}

// ===========================================================================
// 6. RENDER
// ===========================================================================

/** No green even here. A VERIFIED chain is ink on the panel's own ground. */
export const PALETTE = Object.freeze({
  amber: '#e0a33c',
  ink: '#e8eaee',
  mute: '#8b929d',
  line: '#242a33',
});

function mk(doc, tag, opts = {}) {
  const el = doc.createElement(tag);
  if (opts.class) el.className = opts.class;
  if (opts.text !== undefined) el.textContent = String(opts.text);
  if (opts.data) for (const [k, v] of Object.entries(opts.data)) el.dataset[k] = String(v);
  if (opts.attrs && el.setAttribute) {
    for (const [k, v] of Object.entries(opts.attrs)) el.setAttribute(k, String(v));
  }
  if (opts.style && el.style) for (const [k, v] of Object.entries(opts.style)) el.style[k] = v;
  if (opts.on) for (const [k, fn] of Object.entries(opts.on)) el.addEventListener(k, fn);
  for (const kid of opts.kids || []) if (kid) el.appendChild(kid);
  return el;
}

function row(doc, label, value, extra = {}) {
  return mk(doc, 'div', {
    class: `kv ${extra.class || ''}`.trim(),
    data: extra.data,
    kids: [
      mk(doc, 'span', { class: 'kv-k', text: label }),
      mk(doc, 'span', { class: 'kv-v', text: value }),
    ],
  });
}

/** One entry row. Exported so the table can be tested row-wise. */
export function renderEntryRow(e, doc, brokenLine = null) {
  const broken = brokenLine !== null && e.lineNo === brokenLine;
  return mk(doc, 'li', {
    class: `ledger-row${broken ? ' ledger-row-broken' : ''}`,
    data: {
      line: e.lineNo,
      module: e.module ?? '',
      reason: e.reason ?? '',
      money: e.money.known ? String(e.money.paise) : '',
      broken: broken ? 'true' : 'false',
    },
    style: broken ? { borderColor: PALETTE.amber, color: PALETTE.amber } : undefined,
    kids: [
      mk(doc, 'span', { class: 'ledger-line-no', text: `#${e.lineNo}` }),
      mk(doc, 'span', { class: 'ledger-ts', text: e.ts ?? 'no timestamp' }),
      mk(doc, 'span', {
        class: e.module ? 'ledger-module' : 'ledger-module ledger-unknown',
        text: e.module ?? 'module unknown',
      }),
      mk(doc, 'code', {
        class: e.reason ? 'ledger-reason' : 'ledger-reason ledger-unknown',
        text: e.reason ?? 'no reason code',
      }),
      mk(doc, 'span', {
        class: `ledger-money${e.money.known ? '' : ' ledger-unknown'}`,
        data: { known: e.money.known, field: e.money.field ?? '', reason: e.money.reason ?? '' },
        text: e.money.known ? `${e.money.field} ${e.money.text}` : e.money.text,
      }),
      mk(doc, 'code', { class: 'ledger-hash', attrs: { title: e.hash ?? '' }, text: shortHash(e.hash) }),
    ],
  });
}

function renderVerification(model, doc) {
  const v = model.verification;
  const loud = v.verdict === BROKEN || v.verdict === INDETERMINATE || v.verdict === UNAVAILABLE;
  return mk(doc, 'section', {
    class: `ledger-verification ledger-v-${v.verdict.toLowerCase()}`,
    data: {
      verdict: v.verdict,
      lines: v.linesChecked,
      exact: v.exact === null ? 'unknown' : String(v.exact),
      broken: v.brokenLine ?? '',
    },
    kids: [
      mk(doc, 'div', {
        class: 'ledger-verdict',
        data: { verdict: v.verdict },
        style: { color: loud ? PALETTE.amber : PALETTE.ink, borderColor: loud ? PALETTE.amber : PALETTE.line },
        text: v.verdict,
      }),
      mk(doc, 'p', { class: 'ledger-verdict-note', text: v.note }),
      v.error
        ? mk(doc, 'pre', {
          class: 'ledger-break',
          data: { line: v.brokenLine ?? '' },
          text: v.error,
        })
        : null,
      row(doc, 'lines checked', v.verdict === NOT_RUN ? '— not run' : String(v.linesChecked)),
      // Genesis is what the check STARTS from. Printing it as "the head after
      // the check" before any check has run would read as a verified result.
      row(doc, 'head after check', v.verdict === NOT_RUN ? '— not run' : shortHash(v.head, 16)),
      row(doc, 'byte-exact check',
        v.exact === null ? 'not run'
          : v.exact ? 'yes — verified against the writer’s own bytes'
            : 'NO — verified against re-serialised objects'),
      v.durationMs === null ? null : row(doc, 'took', `${v.durationMs.toFixed(1)} ms`),
    ],
  });
}

function renderAbstentions(model, doc) {
  const box = mk(doc, 'section', {
    class: 'ledger-abstentions',
    data: { count: model.abstentions.length },
    kids: [mk(doc, 'h3', { class: 'panel-h3', text: `I do not know (${model.abstentions.length})` })],
  });
  if (model.abstentions.length === 0) {
    box.appendChild(mk(doc, 'p', { class: 'ledger-abstain-none', text: 'nothing is being withheld about this chain.' }));
    return box;
  }
  const ul = mk(doc, 'ul', { class: 'ledger-abstain-list' });
  for (const a of model.abstentions) {
    ul.appendChild(mk(doc, 'li', {
      class: 'ledger-abstain',
      data: { where: a.where, code: a.code },
      kids: [
        mk(doc, 'code', { class: 'abstain-code', text: `${a.where}: ${a.code}` }),
        mk(doc, 'span', { class: 'abstain-note', text: a.note }),
      ],
    }));
  }
  box.appendChild(ul);
  return box;
}

/**
 * (model, doc, handlers) -> the panel subtree. `handlers.onVerify` is called
 * with no arguments when the VERIFY button is pressed; rendering itself stays
 * free of side effects.
 */
export function renderLedger(model, doc = globalThis.document, handlers = {}) {
  if (!doc || typeof doc.createElement !== 'function') {
    throw new TypeError('renderLedger needs a document-like object with createElement');
  }
  const list = mk(doc, 'ol', { class: 'ledger-rows', data: { shown: model.entries.length } });
  if (model.entries.length === 0) {
    list.appendChild(mk(doc, 'li', {
      class: 'ledger-row ledger-row-none',
      text: model.present
        ? 'the chain is empty — genesis and nothing after it.'
        : 'I DO NOT KNOW — no audit chain has been supplied to this panel.',
    }));
  } else {
    for (const e of model.entries) {
      list.appendChild(renderEntryRow(e, doc, model.verification.brokenLine));
    }
  }

  return mk(doc, 'section', {
    class: 'panel panel-ledger',
    data: {
      panel: 'ledger',
      verdict: model.verification.verdict,
      count: model.count,
      source: model.sourceKind,
      abstentions: model.abstentions.length,
    },
    kids: [
      mk(doc, 'header', {
        class: 'panel-head',
        kids: [
          mk(doc, 'h2', { class: 'panel-title', text: model.title }),
          mk(doc, 'button', {
            class: 'ledger-verify btn',
            attrs: { type: 'button' },
            data: { action: 'verify' },
            text: 'VERIFY CHAIN',
            on: handlers.onVerify ? { click: () => handlers.onVerify() } : undefined,
          }),
        ],
      }),
      mk(doc, 'p', {
        class: 'ledger-blurb',
        text: 'Append-only, SHA-256 hash-chained. prev_hash is inside the hashed '
          + 'payload, so deleting or reordering one line breaks every hash after it. '
          + 'VERIFY recomputes the whole chain from genesis in this browser.',
      }),

      mk(doc, 'section', {
        class: 'ledger-headline',
        kids: [
          row(doc, 'head hash', model.head ?? 'I DO NOT KNOW', {
            class: model.head ? '' : 'kv-unknown',
            data: { head: model.head ?? '' },
          }),
          row(doc, 'lines', String(model.count), { data: { count: model.count } }),
          model.reportedCount !== null && model.reportedCount !== model.count
            ? row(doc, 'lines reported by host', String(model.reportedCount), { class: 'kv-unknown' })
            : null,
          row(doc, 'genesis', model.genesis),
          row(doc, 'source', model.canVerifyExactly
            ? `${model.sourceKind} — byte-exact verification available`
            : `${model.sourceKind} — re-serialised verification only`),
        ],
      }),

      renderVerification(model, doc),

      mk(doc, 'section', {
        class: 'ledger-tail',
        kids: [
          mk(doc, 'h3', {
            class: 'panel-h3',
            text: `last ${Math.min(model.lastN, model.count)} of ${model.count} entries`,
          }),
          list,
        ],
      }),

      renderAbstentions(model, doc),
    ],
  });
}

// ===========================================================================
// 7. THE PANEL OBJECT
// ===========================================================================

export function createPanel(opts = {}) {
  const doc = opts.doc ?? opts.document ?? globalThis.document;
  let root = opts.root ?? opts.host ?? null;
  let lastState = null;
  let verification = null;
  let model = deriveLedger(null, null, opts);

  const resolveRoot = () => {
    if (root) return root;
    if (doc && typeof doc.getElementById === 'function') root = doc.getElementById(PANEL_ID);
    return root;
  };

  function paint() {
    model = deriveLedger(lastState, verification, opts);
    const host = resolveRoot();
    if (!host || typeof host.replaceChildren !== 'function') return false;
    // eslint-disable-next-line no-use-before-define
    host.replaceChildren(renderLedger(model, doc, { onVerify: () => { void panel.verify(); } }));
    if (host.dataset) {
      host.dataset.verdict = model.verification.verdict;
      host.dataset.count = String(model.count);
    }
    return true;
  }

  const panel = {
    id: PANEL_ID,
    title: PANEL_TITLE,
    get model() { return model; },

    onState(state) {
      lastState = state;
      // A new state means new lines: the previous verification no longer
      // describes this chain, so it is dropped rather than left on screen
      // vouching for content it never saw.
      verification = null;
      return paint();
    },

    /**
     * Recompute the chain. Never rejects: a verifier that throws into an
     * unhandled promise would leave the last verdict standing on screen, which
     * is the one thing this button must not do.
     */
    async verify() {
      const L = (lastState && typeof lastState === 'object') ? lastState.ledger ?? lastState : null;
      let src = null;
      if (L && typeof L.jsonl === 'string') src = L.jsonl;
      else if (L && Array.isArray(L.lines)) src = L.lines;
      else if (L && Array.isArray(L.entries)) src = L.entries;
      try {
        verification = await verifyChain(src, opts);
      } catch (e) {
        verification = Object.freeze({
          ...RESULT_BASE, verdict: UNAVAILABLE,
          error: `verification could not run: ${(e && e.message) || e}`,
        });
      }
      paint();
      return verification;
    },

    /** The ledger has nothing to draw on the live camera view. */
    onFrame() { return false; },
  };
  return panel;
}

export function attach(register, opts = {}) {
  if (typeof register !== 'function') {
    throw new TypeError('attach(register): registerPanel must be a function');
  }
  const panel = createPanel(opts);
  register(PANEL_ID, panel);
  return panel;
}

/** See chilla.js: the attachXPanel(opts) convention, supported alongside. */
export function attachLedgerPanel(opts = {}) {
  const panel = createPanel(opts);
  const register = typeof opts.register === 'function'
    ? opts.register
    : (typeof globalThis.registerPanel === 'function' ? globalThis.registerPanel : null);
  const registration = register
    ? register(PANEL_ID, { onState: panel.onState, onFrame: panel.onFrame })
    : null;
  return { panel, registered: register !== null, registration };
}

/** See chilla.js: the shell may import `attach`, or drain GAWAAH_PANELS. */
export const DESCRIPTOR = { id: PANEL_ID, title: PANEL_TITLE, createPanel, attach, attached: false };
if (typeof globalThis !== 'undefined') {
  if (typeof globalThis.registerPanel === 'function') {
    attach(globalThis.registerPanel);
    DESCRIPTOR.attached = true;
  }
  (globalThis.GAWAAH_PANELS ||= []).push(DESCRIPTOR);
}

export default { PANEL_ID, createPanel, attach, deriveLedger, renderLedger, verifyChain };
