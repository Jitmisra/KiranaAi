/**
 * SALAAHKAAR — the pure half of the one conversation the shop has with its
 * counter. No DOM, no fetch, no React: everything here can be read in a test
 * runner, and the two halves that cannot (`components/useSalaahkaar.ts`,
 * `components/SalaahkaarCall.tsx`) call in here for every decision.
 *
 * WHY THERE IS A ROUTER AT ALL. Two brains sit behind one composer:
 *
 *   /advisor/say      SALAAHKAAR proper — a call with a session, a model that
 *                     REASONS about the shop's figures, a natural voice, and
 *                     NO tool that changes anything. It cannot bill, cannot
 *                     record, cannot move stock. Asked to, its model reaches
 *                     for the nearest question (`price_of`) and answers that —
 *                     measured: "do Maggi bill me daal do" came back as a price.
 *   /assistant/ask    MUNSHI — one sentence, no memory, and the three tools
 *                     that PROPOSE a change: a line for the bill, a stock
 *                     movement, an expense. Every one comes back as a proposal
 *                     a person has to press.
 *
 * So the browser has to decide, per sentence, which door to knock on, and it
 * has to decide BEFORE sending — a question sent to the assistant gets a
 * plainer answer, which is a small loss; an instruction sent to the advisor is
 * answered as if it were a question, which is the instruction quietly lost.
 * `routeTurn` therefore leans towards ACTION whenever an instruction cue is
 * present and nothing marks the sentence as a question.
 *
 * THE VOCABULARY IS A MIRROR of `gawaah/assistant.py`'s own local parser —
 * `ADD_VERBS`, `_MOVEMENT_REASONS`, `MOVEMENT_BARE_WORDS`, `EXPENSE_WORDS`,
 * `QUESTION_WORDS` — copied rather than imported because the server is Python
 * and the decision has to be made here, before the request. If a word is added
 * there it belongs here too; the test file pins the pairs that matter.
 */

import type { Paise } from './money';
import { rupees } from './money';
import type { Proposal } from './assistantapi';

/* ======================================================================== *
 * 1. Which brain
 * ======================================================================== */

export type TurnRoute = 'action' | 'advice';

export interface Routing {
  route: TurnRoute;
  /** The word that decided it, for the turn's own footnote. Null: nothing did,
      and the sentence went to the advisor by default. */
  cue: string | null;
  /** What kind of action the cue suggests. The server decides for real. */
  hint: 'bill' | 'stock_movement' | 'expense' | null;
}

/** Words the counter's parser reads as "put this on the bill". */
const ADD_VERBS = new Set([
  'add', 'jodo', 'jod', 'likho', 'likh', 'lagao', 'chadhao', 'daalo', 'dalo',
  'daaldo', 'daldo',
  // Bengali: give, put
  'dao', 'dio', 'bosao', 'boshao',
]);

/** "daal do", "dal de", "bill me", "bill pe", "bill e" — two-word cues. The
    first word alone is a food this shop sells (dal), which is exactly why the
    server refuses to treat it as a verb on its own. */
const ADD_BIGRAMS: ReadonlyArray<[string, string]> = [
  ['daal', 'do'], ['dal', 'do'], ['daal', 'de'], ['dal', 'de'], ['daal', 'dena'],
  ['bill', 'me'], ['bill', 'mein'], ['bill', 'main'], ['bill', 'mai'], ['bill', 'pe'],
  ['bill', 'par'], ['bill', 'e'], ['bill', 'te'], ['bil', 'me'], ['bil', 'e'],
];
const ADD_WORDS = new Set(['bille']);

/** Stock moved, and why — `_MOVEMENT_REASONS` and `MOVEMENT_BARE_WORDS`. */
const MOVEMENT_WORDS = new Set([
  // in: returned by a customer
  'wapas', 'wapsi', 'waapas', 'return', 'returned', 'lauta', 'lautaya', 'ferot', 'ferat',
  // in: a delivery
  'aaya', 'aya', 'aayi', 'ayi', 'aye', 'pohcha', 'pahucha', 'pohuche',
  // ...and the plural and the compound the recogniser actually emits for
  // "आए" and "आ गए", which the server's map spells "aye"
  'aaye', 'aayee', 'aagaya', 'aagayi', 'aagaye', 'pahunche', 'pahunchi', 'pahuncha',
  'eseche', 'esheche', 'elo', 'arrived', 'received', 'delivered',
  // out: expired, stolen, taken home, back to the supplier, a sample, broken
  'expired', 'expire', 'meyad', 'meyaad', 'baashi', 'basi', 'nosto', 'kharab', 'purana', 'puraana',
  'chori', 'churaya', 'chura', 'churi', 'theft', 'stolen', 'gayeb',
  'ghar', 'ghore', 'khud', 'nije', 'personal', 'apne',
  'sample', 'muft', 'free', 'namuna',
  'toot', 'toota', 'tut', 'tuta', 'toote', 'phek', 'pheka', 'fek', 'phenk',
  'bhenge', 'bhengeche', 'broke', 'broken', 'wasted', 'damaged',
  // moved, reason unsaid — the server refuses these BY NAME, which is still
  // the assistant's job and not the advisor's
  'hatao', 'hataye', 'hata', 'hatado', 'nikal', 'nikalo', 'nikala',
  'ghatao', 'minus', 'komao', 'soriye', 'sorao', 'kamkaro',
]);
/** "supplier" alone is a question about suppliers; with a return verb it is a
    movement. The server keys "returned_to_supplier" on the bare word, so it is
    kept out of the set above and read only beside a movement word. */
const SUPPLIER_WORDS = new Set(['supplier', 'mahajan', 'distributor', 'wholesaler']);

const EXPENSE_WORDS = new Set([
  'kharch', 'kharcha', 'kharche', 'expense', 'expenses', 'spent', 'spend',
  'kharoch', 'khoroch', 'byay', 'vyay', 'outgoing',
]);

/** A sentence that asks. Any of these beside an add verb, and it is a question
    about the bill rather than an order for it — "kya Maggi add hua?". */
const QUESTION_WORDS = new Set([
  'kitne', 'kitna', 'kitni', 'kya', 'kyaa', 'kaun', 'kaunse', 'konse', 'kab', 'kahan', 'kyun', 'kyu',
  'batao', 'bata', 'bataiye', 'dikhao', 'dikha', 'dikhaiye',
  'much', 'how', 'many', 'what', 'which', 'who', 'whose', 'why', 'when', 'where', 'show', 'tell', 'list',
  'koto', 'kota', 'kotota', 'kotogulo', 'kothay', 'kobe', 'kemon', 'keno', 'kon', 'konta', 'konti',
  'kongulo',
]);

const NUMBER_WORDS = new Set([
  'ek', 'do', 'teen', 'tin', 'char', 'chaar', 'paanch', 'panch', 'chhe', 'che', 'saat', 'aath', 'nau',
  'das', 'dus', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten',
  'ekta', 'duto', 'dui', 'tinte', 'tinti', 'charte', 'pachta', 'pach',
]);

export function tokens(text: string): string[] {
  return (text.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? []);
}

/** A rupee figure somewhere in the sentence: "200", "200 rupaye", "₹200", "rs 200". */
export function hasRupees(text: string, toks: ReadonlyArray<string>): boolean {
  if (/(?:₹|\brs\.?|\brupees?|\brupaye|\brupiya|\btaka)\s*\d/i.test(text)) return true;
  if (/\d\s*(?:₹|rs\b|rupees?|rupaye|rupiya|taka|ka|ke)/i.test(text)) return true;
  return toks.some((t) => /^\d{2,}$/.test(t));
}

/**
 * The decision. Read it top to bottom; the order is the same as the server's
 * local parser, narrowest first, and every branch says why.
 */
export function routeTurn(text: string): Routing {
  const toks = tokens(text);
  const has = (set: ReadonlySet<string>) => toks.find((t) => set.has(t)) ?? null;
  const question = has(QUESTION_WORDS) !== null || text.trim().endsWith('?');

  // Stock moved. "10 Maggi aaye" is a movement whether or not "kitne" is in the
  // sentence — but "kitne Maggi aaye?" is a question about movements, and the
  // advisor has `stock_movements` for exactly that.
  const moved = has(MOVEMENT_WORDS);
  if (moved && !question) return { route: 'action', cue: moved, hint: 'stock_movement' };
  const supplier = has(SUPPLIER_WORDS);
  if (supplier && !question && has(new Set(['wapas', 'wapsi', 'waapas', 'return', 'bhejo', 'bheja', 'gaya', 'gayi']))) {
    return { route: 'action', cue: supplier, hint: 'stock_movement' };
  }

  // Money went out. With an amount it is a record; without one it is the
  // question "what did we spend today", which the advisor answers and reasons
  // about. Checked before the add verbs, exactly as the server does, because
  // "chai ka kharcha likho" carries "likho" and names a product.
  const spent = has(EXPENSE_WORDS);
  if (spent) {
    if (hasRupees(text, toks) && !question) return { route: 'action', cue: spent, hint: 'expense' };
    return { route: 'advice', cue: spent, hint: null };
  }

  // Put it on the bill.
  if (!question) {
    const verb = has(ADD_VERBS) ?? has(ADD_WORDS);
    if (verb) return { route: 'action', cue: verb, hint: 'bill' };
    // In list order, so the verb ("daal do") names the cue ahead of the
    // place ("bill me") when a sentence carries both.
    for (const [a, b] of ADD_BIGRAMS) {
      for (let i = 0; i + 1 < toks.length; i++) {
        if (toks[i] === a && toks[i + 1] === b) return { route: 'action', cue: `${a} ${b}`, hint: 'bill' };
      }
    }
    // "do Maggi" with nothing else said — a count and a product and no verb —
    // is how an order is spoken across a counter, and the server's parser
    // reads it that way too. Two words or three, starting with a number.
    if (toks.length >= 2 && toks.length <= 4 && toks[0] !== undefined
      && (NUMBER_WORDS.has(toks[0]) || /^\d+$/.test(toks[0]))) {
      return { route: 'action', cue: toks[0], hint: 'bill' };
    }
  }

  return { route: 'advice', cue: null, hint: null };
}

/* ======================================================================== *
 * 2. Actions, described
 * ======================================================================== */

/** A product name without its Devanagari gloss, for a card or a pill. */
export const shortName = (n: string) => n.replace(/\s*\(.*?\)\s*$/, '').trim();

export type ActionState = 'proposed' | 'applied' | 'undone';

/** The one line on the turn that says what happened. `did:` only after the
    person pressed and the server answered; before that it is `proposed:`. */
export function describeAction(p: Proposal, state: ActionState): string {
  const kind = p.kind ?? 'bill';
  if (kind === 'bill') {
    const what = p.lines.map((l) => `${l.qty}× ${shortName(l.name)}`).join(', ') || 'nothing';
    if (state === 'proposed') return `proposed: ${what} for the bill — not billed`;
    if (state === 'applied') return `did: held ${what} for the till — not billed`;
    return `undone: ${what} let go — never billed`;
  }
  if (kind === 'stock_movement') {
    const m = p.movement;
    const what = m ? `${m.units} ${shortName(m.name)} ${m.direction}` : 'a movement';
    const why = m?.reason_label ? ` — ${m.reason_label}` : '';
    if (state === 'proposed') return `proposed: ${what}${why} — not moved`;
    if (state === 'applied') return `did: ${what}${why}`;
    return `undone: ${what} reversed by a correcting movement`;
  }
  const e = p.expense;
  const what = e ? `${rupees(e.amount_paise)} under ${e.category_label ?? e.category}` : 'an expense';
  if (state === 'proposed') return `proposed: ${what} — not recorded`;
  if (state === 'applied') return `did: recorded ${what}`;
  return `undone: ${what} voided — the line stays, and stops counting`;
}

/* ======================================================================== *
 * 3. Undo, where the backend has one
 * ======================================================================== */

/**
 * What applying a proposal produced, read from the server's answer by the
 * abstaining readers below. The undo needs an id the server minted; a
 * response without one is an action this browser cannot take back, and the
 * card says so rather than pretending.
 */
export interface Applied {
  at: number;
  /** `movement_id` from /stock/{sku}/{in|out}. */
  movement_id?: string;
  /** `expense.expense_id` from /expenses. */
  expense_id?: string;
  /** The shelf figure after the movement, when the server said. */
  on_hand_units?: number | null;
}

export type UndoPlan =
  | { kind: 'unhold'; proposal_id: string; says: string }
  | { kind: 'post'; path: string; body: Record<string, unknown>; says: string };

/**
 * THE UNDO IS THE SERVER'S OWN VOCABULARY, not a delete.
 *
 *   a held bill line      let go from this browser — nothing was ever billed
 *   a stock movement      a CORRECTING movement the other way. stock.py has no
 *                         delete and lists "correction" as a reason in both
 *                         directions; both movements stay on the log.
 *   an expense            /expenses/{id}/void — the row stays, stops counting,
 *                         and carries the reason it was voided.
 *
 * Null when there is nothing to undo with: no applied record, or one without
 * the id the endpoint needs.
 */
export function undoPlan(p: Proposal, applied: Applied | null): UndoPlan | null {
  const kind = p.kind ?? 'bill';
  if (kind === 'bill') {
    return { kind: 'unhold', proposal_id: p.proposal_id, says: 'Let the held lines go. Nothing was ever billed.' };
  }
  if (!applied) return null;
  if (kind === 'stock_movement') {
    const m = p.movement;
    if (!m || !applied.movement_id) return null;
    const back = m.direction === 'in' ? 'out' : 'in';
    return {
      kind: 'post',
      path: `/stock/${encodeURIComponent(m.sku_id)}/${back}`,
      body: { units: m.units, reason: 'correction', note: `undo of ${applied.movement_id} from Salaahkaar` },
      says: `A correcting movement of ${m.units} ${back}. Both movements stay on the log.`,
    };
  }
  if (!applied.expense_id) return null;
  return {
    kind: 'post',
    path: `/expenses/${encodeURIComponent(applied.expense_id)}/void`,
    body: { reason: 'undone from Salaahkaar' },
    says: 'Void it. The line stays in the day book with the reason, and stops counting.',
  };
}

/* ======================================================================== *
 * 4. Readers — every one abstains. A missing field is a row not drawn.
 * ======================================================================== */

const num = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null;
const int = (v: unknown): number | null => {
  const n = num(v);
  return n !== null && Number.isInteger(n) ? n : null;
};
const str = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null);
const rec = (v: unknown): Record<string, unknown> =>
  v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

/** What the server said after a movement or an expense was recorded. */
export function readApplied(body: unknown, at: number): Applied {
  const d = rec(body);
  const out: Applied = { at };
  const mv = str(d.movement_id);
  if (mv) { out.movement_id = mv; out.on_hand_units = int(d.on_hand_units); }
  const ex = str(rec(d.expense).expense_id);
  if (ex) out.expense_id = ex;
  return out;
}

export interface SellerRow {
  sku_id: string;
  name: string;
  units: number;
  revenue_paise: Paise | null;
}

/**
 * Top sellers, from any of the three shapes the counter uses for them:
 * `top_sellers[]` (/manage/today), `derived.units_by_sku` (day_close_preview)
 * and a bare `units_by_sku`. Sorted by units, at most `limit` rows.
 */
export function readTopSellers(data: unknown, limit = 6): SellerRow[] {
  const d = rec(data);
  const rows: SellerRow[] = [];
  const fromList = list(d.top_sellers);
  if (fromList.length > 0) {
    for (const raw of fromList) {
      const r = rec(raw);
      const sku = str(r.sku_id);
      const units = int(r.units);
      if (!sku || units === null) continue;
      rows.push({ sku_id: sku, name: str(r.name) ?? sku, units, revenue_paise: int(r.revenue_paise) });
    }
  } else {
    const src = rec(d.units_by_sku ?? rec(d.derived).units_by_sku);
    const rev = rec(d.line_revenue_by_sku ?? rec(d.derived).line_revenue_by_sku);
    for (const [sku, v] of Object.entries(src)) {
      const units = int(v);
      if (units === null) continue;
      rows.push({ sku_id: sku, name: sku.replace(/_/g, ' '), units, revenue_paise: int(rev[sku]) });
    }
  }
  rows.sort((a, b) => b.units - a.units);
  return rows.slice(0, limit);
}

export interface LowRow {
  sku_id: string;
  name: string;
  /** Units the counter believes are left. Null: it has no figure. */
  left: number | null;
  billed_since_count: number | null;
}

export interface LowFacts {
  rows: LowRow[];
  /** Products with no count, and products with no reorder level. Reported,
      never zeroed. */
  uncounted: number | null;
  without_level: number | null;
}

/**
 * What is running low, from either shape the counter answers with: the
 * assistant's `low_stock` rows (`remaining_units`) and stock.py's own
 * `/stock/low` rows, which the advisor's `reorder_list` returns whole
 * (`on_hand_units`, `remaining_after_billing`).
 */
export function readLowRows(data: unknown): LowFacts | null {
  const d = rec(data);
  if (d.low === undefined && d.uncounted === undefined && d.unknown === undefined) return null;
  const rows: LowRow[] = [];
  for (const raw of list(d.low)) {
    const r = rec(raw);
    const sku = str(r.sku_id);
    if (!sku) continue;
    rows.push({
      sku_id: sku,
      name: str(r.name) ?? sku,
      left: int(r.remaining_units) ?? int(r.on_hand_units) ?? int(r.remaining_after_billing),
      billed_since_count: int(r.billed_since_count),
    });
  }
  const unknown = Array.isArray(d.unknown) ? d.unknown.length : int(d.unknown);
  return {
    rows,
    uncounted: int(d.uncounted) ?? unknown,
    without_level: Array.isArray(d.skus_without_a_level) ? d.skus_without_a_level.length : int(d.skus_without_a_level),
  };
}

/* ======================================================================== *
 * 5. The voice out — which of the browser's voices, for which language
 * ======================================================================== */

export interface VoiceChoice {
  voice: SpeechSynthesisVoice | null;
  /** Plain English, printed under the microphone. */
  note: string;
  /** Whether the voice matches the language the shopkeeper chose. */
  matched: boolean;
}

/**
 * Pick a voice for `lang`, best first: the exact tag, then the same language
 * in any region, then an Indian-English voice, then any English, then the
 * browser's default. PURE, so the decision can be read without a browser.
 */
export function pickVoice(voices: ReadonlyArray<SpeechSynthesisVoice>, lang: string): VoiceChoice {
  if (voices.length === 0) {
    return { voice: null, matched: false, note: 'This browser has no voices installed, so answers are shown and not spoken.' };
  }
  const norm = (v: SpeechSynthesisVoice) => v.lang.replace('_', '-').toLowerCase();
  const want = lang.toLowerCase();
  const base = want.split('-')[0] ?? want;
  const exact = voices.find((v) => norm(v) === want);
  if (exact) return { voice: exact, matched: true, note: `Speaking with ${exact.name} (${exact.lang}).` };
  const sameLang = voices.find((v) => norm(v).startsWith(`${base}-`) || norm(v) === base);
  if (sameLang) {
    return { voice: sameLang, matched: true, note: `Speaking with ${sameLang.name} (${sameLang.lang}) — no ${lang} voice here, this is the nearest.` };
  }
  const indianEnglish = voices.find((v) => norm(v) === 'en-in');
  if (indianEnglish) {
    return { voice: indianEnglish, matched: false, note: `No ${lang} voice on this browser. Speaking with ${indianEnglish.name} (en-IN) instead — Hindi words will sound English.` };
  }
  const anyEnglish = voices.find((v) => norm(v).startsWith('en'));
  const fallback = anyEnglish ?? voices.find((v) => v.default) ?? voices[0] ?? null;
  return {
    voice: fallback,
    matched: false,
    note: fallback
      ? `No Indian voice on this browser. Speaking with ${fallback.name} (${fallback.lang}) — it will mispronounce Hindi.`
      : 'This browser has no voices installed, so answers are shown and not spoken.',
  };
}

/* ======================================================================== *
 * 6. Languages, and the first thing said
 * ======================================================================== */

/** The three the till's voice speaks (`/advisor/health` → voice.languages)
    and the recogniser hears. One control switches all of it. */
export const LANGS = [
  { tag: 'hi-IN', label: 'हिन्दी', short: 'हि', hears: 'hears Hindi, answers in Devanagari' },
  { tag: 'en-IN', label: 'English', short: 'En', hears: 'hears English brand names best' },
  { tag: 'bn-IN', label: 'বাংলা', short: 'বাং', hears: 'hears Bengali, answers in Bengali script' },
] as const;
export type LangTag = (typeof LANGS)[number]['tag'];
export const isLangTag = (v: string): v is LangTag => LANGS.some((l) => l.tag === v);

/** The first thing said on a call, in the language the shopkeeper chose. */
export const GREETING: Record<LangTag, { reasons: string; figures: string }> = {
  'hi-IN': {
    reasons: 'सलाहकार लाइन पर है। आज की बिक्री, मुनाफ़ा, ऑर्डर, स्टॉक या दाम पूछिए — या कहिए “दो मैगी बिल में डाल दो”।',
    figures: 'सलाहकार लाइन पर है, सिर्फ़ आँकड़े पढ़ रहा है — कोई मॉडल सेट नहीं है, इसलिए उन पर सोच नहीं सकता। बिक्री, मुनाफ़ा, ऑर्डर, स्टॉक या दाम पूछिए।',
  },
  'bn-IN': {
    reasons: 'সালাহকার লাইনে আছে। আজকের বিক্রি, লাভ, অর্ডার, স্টক বা দাম জিজ্ঞেস করুন — বা বলুন “দুটো ম্যাগি বিলে দাও”।',
    figures: 'সালাহকার লাইনে আছে, শুধু হিসাব পড়ছে — কোনো মডেল সেট নেই, তাই সেগুলো নিয়ে ভাবতে পারছে না। বিক্রি, লাভ, অর্ডার, স্টক বা দাম জিজ্ঞেস করুন।',
  },
  'en-IN': {
    reasons: 'Salaahkaar on the line. Ask about today’s takings, the margin, open orders, stock, or a price — or say “2 Maggi bill me daal do” and it goes on the bill for you to accept.',
    figures: 'Salaahkaar on the line, reading figures only — no model is set, so I cannot reason about them. Ask about today’s takings, the margin, open orders, stock, or a price.',
  },
};

/* ======================================================================== *
 * 7. Suggestions — sentences both parsers understand, so a chip works with
 *    no key set, which is how the till ships.
 * ======================================================================== */

export interface Suggestion { what: string; say: string; route: TurnRoute }

export function suggestions(products: ReadonlyArray<string>): Suggestion[] {
  const a = products[0];
  const b = products[1] ?? products[0];
  const out: Suggestion[] = [
    { what: 'today’s takings', say: 'aaj ki bikri kitni hui', route: 'advice' },
    { what: 'today’s margin', say: 'aaj ka munafa kitna hua', route: 'advice' },
    { what: 'orders still open', say: 'kitne orders pending hain', route: 'advice' },
    { what: 'what is running out', say: 'kya khatam ho raha hai', route: 'advice' },
  ];
  // A DIGIT, NOT "do". Measured against the live model brain: "do Maggi bill
  // me daal do" came back as 1× Maggi twice running ("do" read as the English
  // verb), while "do Parle …" and "2 Maggi …" came back as 2×. A digit reads
  // the same to both brains and in all three scripts.
  if (b) out.splice(1, 0, { what: 'put it on the bill', say: `2 ${b} bill me daal do`, route: 'action' });
  if (a) out.push({ what: 'what it costs', say: `${a} ka daam kya hai`, route: 'advice' });
  return out;
}

/**
 * ONE WORD THAT NAMES EXACTLY ONE PRODUCT, or nothing — counted the same way
 * the server counts it, so a chip never demonstrates an ambiguity refusal on
 * its first press.
 */
export function sayableProducts(skus: ReadonlyArray<{ sku_id: string; name: string }>, limit = 2): string[] {
  const words = (t: string) => t.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [];
  const perWord = new Map<string, number>();
  for (const s of skus) {
    for (const w of new Set([...words(s.name ?? ''), ...words(s.sku_id)])) {
      perWord.set(w, (perWord.get(w) ?? 0) + 1);
    }
  }
  const out: string[] = [];
  for (const s of skus) {
    for (const raw of (s.name || s.sku_id).split(/[\s,]+/)) {
      const m = raw.match(/[\p{L}\p{N}]+/u);
      if (!m) continue;
      const w = m[0];
      if ((w.match(/\p{L}/gu) ?? []).length < 3) continue;
      if (perWord.get(w.toLowerCase()) === 1 && !out.some((o) => o.toLowerCase() === w.toLowerCase())) {
        out.push(w);
        break;
      }
    }
    if (out.length >= limit) break;
  }
  return out;
}

export const clock = (ms: number) =>
  new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

export const mmss = (s: number) =>
  `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
