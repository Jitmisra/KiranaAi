/**
 * Hinglish voice billing — the sentence, and the microphone that heard it.
 *
 * At a real counter the shopkeeper's hands are wet, or holding a packet, or
 * taking money. Voice is the input that fits. But a heard sentence is a guess
 * about a guess: the speech engine guessed the words, and this file guesses
 * which product those words name. So the whole module is built around one rule,
 * the same one the camera path already obeys (invariant 7):
 *
 *     ABSTAIN RATHER THAN GUESS.
 *
 * A word this file cannot place is reported to the operator verbatim — "I heard
 * X, I do not know it" — and never silently dropped, never rounded to the
 * nearest SKU. A short bill a shopkeeper can see beats a confident bill that is
 * wrong, and voice has more ways to be confidently wrong than a barcode does.
 *
 * The parser is PURE and lives apart from the microphone on purpose. Everything
 * hard here is in turning "do Maggi aur ek Pepsi" into rows, and a rule you can
 * only test by speaking into a laptop is a rule that stops being tested.
 *
 * Nothing in this file touches money. It proposes `{sku_id, qty}` and the
 * component makes a person accept it; prices are the catalogue's, in integer
 * paise, formatted by lib/money.ts.
 */

/* ==========================================================================
 * 1. Vocabulary
 * ======================================================================== */

/**
 * Counting words, Hindi and English, plus the spellings a speech engine
 * actually emits. `hi-IN` returns Devanagari, `en-IN` returns roman letters and
 * spells the same sound several ways, so both scripts are listed.
 *
 * Deliberately absent: "sat" (English past tense), "no" (nau), "tera" (13, but
 * far more often "yours"), "saath" (7, but far more often "with"), "chai" (6 in
 * some spellings, but tea in every shop). A counting word that is also a common
 * word turns a product into a quantity, which is the one mistake that changes
 * a number on a bill without changing a name the operator would notice.
 */
const NUMBERS: Record<string, number> = {
  // Hindi, roman
  ek: 1, eak: 1, aik: 1,
  do: 2, doh: 2,
  teen: 3, theen: 3,
  char: 4, chaar: 4,
  panch: 5, paanch: 5, paanj: 5,
  chhe: 6, che: 6, chah: 6, chheh: 6, cheh: 6,
  saat: 7,
  aath: 8, ath: 8,
  nau: 9, nao: 9,
  das: 10, dus: 10,
  gyarah: 11, gyaarah: 11, gyara: 11,
  barah: 12, baarah: 12, bara: 12,
  chaudah: 14,
  pandrah: 15, pandra: 15,
  solah: 16,
  bees: 20, bis: 20,
  // Hindi, Devanagari
  'एक': 1, 'दो': 2, 'तीन': 3, 'चार': 4, 'पांच': 5, 'पाँच': 5,
  'छह': 6, 'छे': 6, 'सात': 7, 'आठ': 8, 'नौ': 9, 'दस': 10,
  'ग्यारह': 11, 'बारह': 12, 'पंद्रह': 15, 'बीस': 20,
  // Bengali, roman. The counting particles "-ta" / "-te" / "-ti" are part of
  // the word as it is said ("duto Maggi"), so they are listed as words rather
  // than stripped. "at" (8) and "sat" (7) are left out for the reason "saath"
  // is: both are far more often the English word.
  ekta: 1, ekti: 1,
  dui: 2, duto: 2, duti: 2, duita: 2,
  tin: 3, tinte: 3, tinti: 3,
  charte: 4, charti: 4,
  pach: 5, panchta: 5,
  chhoy: 6, choy: 6, chhay: 6,
  shat: 7,
  aat: 8,
  noy: 9, nou: 9,
  dosh: 10, doshta: 10,
  egaro: 11, baro: 12, ponero: 15, kuri: 20,
  // Bengali script. `bn-IN` returns this.
  'এক': 1, 'একটা': 1, 'একটি': 1, 'দুই': 2, 'দুটো': 2, 'দুটি': 2,
  'তিন': 3, 'তিনটে': 3, 'চার': 4, 'চারটে': 4, 'পাঁচ': 5, 'পাঁচটা': 5,
  'ছয়': 6, 'ছটা': 6, 'সাত': 7, 'আট': 8, 'নয়': 9, 'দশ': 10,
  'এগারো': 11, 'বারো': 12, 'পনেরো': 15, 'বিশ': 20, 'কুড়ি': 20,
  // English
  one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8,
  nine: 9, ten: 10, eleven: 11, twelve: 12, thirteen: 13, fourteen: 14,
  fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18, nineteen: 19, twenty: 20,
};

/**
 * A dozen is twelve everywhere, so it is the one packing word that is
 * MULTIPLIED: "ek dozen Maggi" is twelve Maggi, "do darjan" is twenty-four.
 * A carton or a peti is not here — how many packets are in one is the
 * wholesaler's decision and this counter has never been told, so the server
 * reads those as one and says so.
 */
const DOZEN = new Set(['dozen', 'dozens', 'darjan', 'दर्जन', 'ডজন']);

/**
 * Halves and quarters, in the three languages a counter hears them in. A
 * fraction in front of a product is an order for a WEIGHT of it — "aadha kilo
 * chawal" — which only the server can price, from gawaah/weighed.py's own
 * per-kilo table. Here it decides one thing: that the sentence is an order
 * and not a question, even though it carries no count at all.
 */
const FRACTIONS = new Set([
  'aadha', 'adha', 'aadhi', 'adhi', 'half',
  'dedh', 'derh', 'dhai', 'dhaai', 'sava', 'savva', 'sawa',
  'paune', 'pauna', 'pav', 'pao', 'quarter',
  'ordhek', 'adhek', 'poya', 'sikey',
  'आधा', 'आधी', 'डेढ़', 'ढाई', 'सवा', 'पौने', 'पौन', 'पाव',
  'অর্ধেক', 'আধা', 'পোয়া', 'সিকি', 'দেড়', 'আড়াই',
]);

/**
 * "a Maggi" is one Maggi — but only at the front. Trailing "a" is a stray
 * article far more often than it is a count, and reading it as a count would
 * silently rewrite a quantity.
 */
const LEADING_ONE = new Set(['a', 'an']);

/**
 * Units are measure words, not counts and not names: "do kilo chawal" is two of
 * chawal, not two of "kilo chawal". Dropping them from the name is what lets
 * the matcher see the product.
 *
 * "g" and "gram" are NOT here, and that is not an oversight — the shop sells
 * Parle-G, and a rule that eats "g" would make the product unsayable.
 */
const UNITS = new Set([
  'kilo', 'kilos', 'kg', 'kgs',
  'packet', 'packets', 'pack', 'packs',
  'bottle', 'bottles',
  'piece', 'pieces', 'pcs', 'pc',
  'litre', 'litres', 'liter', 'liters', 'ltr',
  'किलो', 'पैकेट', 'बोतल',
  'কিলো', 'কেজি', 'লিটার', 'প্যাকেট', 'বোতল',
]);

/**
 * One order, next order. Commas and full stops are turned into these upstream.
 *
 * The bare Bengali "o" ("dudh o chini") is DELIBERATELY ABSENT, for the reason
 * the server's parser leaves it out: a one-letter conjunction would eat the
 * tail of a brand name like "Nestle-O". "ar" and "ebong" carry the Bengali.
 */
const SEPARATORS = new Set([
  'aur', 'and', 'or', 'plus', 'phir', 'then', ',', 'और', 'फिर',
  'ar', 'aar', 'ebong', 'আর', 'এবং',
]);

/**
 * Politeness, not products. A shopkeeper says "ek Maggi de do bhaiya", and
 * every word after the product is noise that would otherwise be scored against
 * the catalogue.
 *
 * WHAT IT COSTS: if this shop ever stocks something called "Bas" or "Ji", voice
 * will not be able to name it — the camera still can. That is the cheaper
 * failure, because it is visible: the line simply does not appear.
 */
const FILLER = new Set([
  'mujhe', 'muje', 'mujhko', 'please', 'bhaiya', 'bhai', 'ji', 'ok', 'okay',
  'chahiye', 'chaiye', 'dijiye', 'dena', 'de', 'le', 'lena', 'lo', 'bas',
  'hai', 'hain', 'theek', 'thik', 'yaar', 'zara', 'thoda',
  'मुझे', 'चाहिए', 'दीजिए', 'दे', 'भैया', 'जी', 'बस',
  // Bengali: give / bring / a little. Sentence endings, not products.
  'dao', 'din', 'diye', 'amake', 'ektu', 'দাও', 'দিন', 'আমাকে', 'একটু',
  // Yes and no. A recogniser left open hears these on their own, and a
  // "haan" that reached the server as a product name would be refused by
  // name — noisy, not wrong, but noise at a counter is cost enough.
  'haan', 'han', 'nahi', 'nahin', 'हाँ', 'हां', 'नहीं', 'হ্যাঁ', 'না',
]);

/**
 * "de do" is "give", not "give two". The bigram is removed as a unit before the
 * numbers are read; without this, "ek Maggi de do" carries two counting words
 * and the whole order is refused as ambiguous.
 */
const GIVE_BIGRAMS: Array<[string, string]> = [['de', 'do'], ['dede', 'do'], ['de', 'de']];

/**
 * Hindi words for things the catalogue spells in English. An ALIAS TABLE, not
 * inference: each entry was written down by a person, and the matcher scores
 * both the heard word and its alias and keeps the better one.
 *
 * WHAT IT COSTS: a wrong entry here puts a wrong product on a bill, which is
 * exactly the failure this module exists to avoid. So the list is short, and
 * every line is a word with one meaning in a kirana.
 */
const ALIASES: Record<string, string> = {
  sabun: 'soap', saabun: 'soap',
  biskut: 'biscuit', biskoot: 'biscuit', biscut: 'biscuit',
  doodh: 'milk', dudh: 'milk',
  chawal: 'rice',
  namak: 'salt',
  cheeni: 'sugar', chini: 'sugar',
  tel: 'oil',
  pani: 'water', paani: 'water',
  atta: 'flour',
  'साबुन': 'soap', 'बिस्कुट': 'biscuit', 'दूध': 'milk', 'चावल': 'rice',
};

/** Devanagari and Bengali digits, which `hi-IN` and `bn-IN` return for a spoken number. */
const DEVANAGARI_DIGITS = '०१२३४५६७८९';
const BENGALI_DIGITS = '০১২৩৪৫৬৭৮৯';

/**
 * The largest count voice will bill.
 *
 * A mis-heard "do" as "do sau" — or a stray "200" — proposes two hundred lines
 * of something, and although a person still has to accept it, a number that
 * large is far more likely to be a mishearing than an order. Above this the
 * segment is refused and shown to the operator, who can type it.
 */
export const MAX_QTY = 99;

/* ==========================================================================
 * 2. The parser
 * ======================================================================== */

export interface HeardItem {
  /** The product words, units and counts removed. Original casing kept. */
  name: string;
  /** How many. Always a whole number, 1..MAX_QTY. A dozen is already ×12. */
  qty: number;
  /** The fragment exactly as it was heard, for showing back to the operator. */
  raw: string;
  /**
   * A half or a quarter said before the product, as it was said. Only present
   * when one was: this file does not price a weight, the server does, so all
   * it records is that a weight was asked for.
   */
  fraction?: string;
  /** A measure word said with it ("kilo", "dozen"), as it was said. */
  unit?: string;
}

export interface Heard {
  items: HeardItem[];
  /** Fragments that were heard but could not be read as an order. Never dropped. */
  unparsed: string[];
  /**
   * How many items carried a SPOKEN quantity — a count, a dozen or a fraction —
   * as opposed to the default of one. "Maggi" alone is one item and zero
   * counted; the classifier reads that as a question, the way the server does.
   */
  counted: number;
}

/**
 * Strip a token to its comparable core: lowercase letters, digits, and the
 * marks that letters need.
 *
 * `\p{M}` is not decoration. "दो" is one consonant plus a combining vowel sign,
 * and a rule that kept only `\p{L}` reduced it to "द" — so every Devanagari
 * count silently became a quantity of 1, which is the exact shape of bug this
 * module is supposed to make impossible.
 */
function normToken(token: string): string {
  // NFC first. The nukta letters (ड़, য়) have two encodings that look the same
  // on screen, and which one arrives depends on the engine; the tables above
  // are typed in one of them. Normalising both sides is what makes "ছয়" find
  // its entry whichever way it was spelt.
  const folded = token
    .normalize('NFC')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\p{M}]+/gu, '');
  // A spoken "10" can come back as "१०" or "১০"; fold it so digits are digits.
  let out = '';
  for (const ch of folded) {
    const d = DEVANAGARI_DIGITS.indexOf(ch);
    const b = BENGALI_DIGITS.indexOf(ch);
    out += d >= 0 ? String(d) : b >= 0 ? String(b) : ch;
  }
  return out;
}

/** A vocabulary set whose entries went through the same fold as the tokens. */
function vocab(words: Iterable<string>): Set<string> {
  return new Set([...words].map(normToken));
}
const DOZEN_N = vocab(DOZEN);
const FRACTIONS_N = vocab(FRACTIONS);
const UNITS_N = vocab(UNITS);
const FILLER_N = vocab(FILLER);
const SEPARATORS_N = vocab(SEPARATORS);
const NUMBERS_N: Record<string, number> = Object.fromEntries(
  Object.entries(NUMBERS).map(([k, v]) => [normToken(k), v]),
);

/** A count, or null. Bare digits only when the token is ALL digits — see below. */
function asNumber(token: string): number | null {
  if (token === '') return null;
  // "10C" is a SKU in this shop. Splitting a digit prefix off a token would
  // turn that product into "ten of C", so a token is a count only when there is
  // nothing else in it. "7up" and "100g" stay names for the same reason.
  if (/^\d+$/.test(token)) {
    const n = Number.parseInt(token, 10);
    return Number.isFinite(n) ? n : null;
  }
  const word = NUMBERS_N[token];
  return word === undefined ? null : word;
}

/** Split the transcript into per-order fragments, keeping original casing. */
function segments(transcript: string): string[][] {
  // Punctuation is a separator the speaker did not have to say. The Devanagari
  // danda is included because `hi-IN` ends sentences with it.
  const flat = transcript.replace(/[,;।|/!?]+|\.(?!\d)/g, ' , ');
  const words = flat.split(/\s+/).filter((w) => w.length > 0);
  const out: string[][] = [];
  let cur: string[] = [];
  for (const w of words) {
    if (SEPARATORS_N.has(normToken(w)) || w === ',') {
      if (cur.length) out.push(cur);
      cur = [];
      continue;
    }
    cur.push(w);
  }
  if (cur.length) out.push(cur);
  return out;
}

/** Remove "de do"-style give-phrases before any word is read as a count. */
function dropGivePhrases(words: string[]): string[] {
  const norm = words.map(normToken);
  const keep = words.map(() => true);
  for (let i = 0; i + 1 < words.length; i++) {
    if (!keep[i]) continue;
    for (const [a, b] of GIVE_BIGRAMS) {
      if (norm[i] === a && norm[i + 1] === b) {
        keep[i] = false;
        keep[i + 1] = false;
        break;
      }
    }
  }
  return words.filter((_, i) => keep[i]);
}

/**
 * Turn a heard sentence into proposed lines.
 *
 * Pure: no microphone, no catalogue, no network. It decides QUANTITY and NAME
 * and nothing else — whether that name is a product this shop sells is
 * `matchProduct`'s question, deliberately asked separately so that "two of
 * something I do not stock" and "some number of Maggi" are different failures
 * with different messages.
 */
export function parseHinglish(transcript: string): Heard {
  const items: HeardItem[] = [];
  const unparsed: string[] = [];
  let counted = 0;

  for (const seg of segments(transcript ?? '')) {
    const raw = seg.join(' ');
    const words = dropGivePhrases(seg);

    const counts: number[] = [];
    const fractions: string[] = [];
    const nameWords: string[] = [];
    let dozens = 0;
    let unit: string | undefined;

    // Position is not consulted. "do Maggi" and "Maggi do" are the same order,
    // and so is "mujhe do Maggi dena" — one count in a fragment is that
    // fragment's count wherever it sits. Two counts is a different question,
    // handled below.
    words.forEach((w, i) => {
      const t = normToken(w);
      if (t === '' || FILLER_N.has(t)) return;
      const n = asNumber(t) ?? (i === 0 && LEADING_ONE.has(t) ? 1 : null);
      if (n !== null) {
        counts.push(n);
        return;
      }
      if (FRACTIONS_N.has(t)) { fractions.push(w); return; }
      if (DOZEN_N.has(t)) { dozens += 1; unit = unit ?? w; return; }
      if (UNITS_N.has(t)) { unit = unit ?? w; return; }
      nameWords.push(w);
    });

    if (nameWords.length === 0) {
      // "do kilo" with no product, or a stray "haan". Heard, not understood.
      if (raw.trim() !== '') unparsed.push(raw);
      continue;
    }
    if (counts.length > 1 || fractions.length > 1 || dozens > 1) {
      // "do teen Maggi" is a person hesitating, not an order for five. There is
      // no safe number to bill, so the operator gets the sentence back.
      unparsed.push(raw);
      continue;
    }

    let qty = counts[0] ?? 1;     // at most one count survives the check above
    // "ek dozen" is twelve and "dozen" alone is twelve: the count multiplies
    // the dozen, it does not sit beside it.
    if (dozens === 1) qty *= 12;
    if (!Number.isInteger(qty) || qty < 1 || qty > MAX_QTY) {
      unparsed.push(raw);
      continue;
    }
    const item: HeardItem = { name: nameWords.join(' '), qty, raw };
    const fraction = fractions[0];
    if (fraction !== undefined) item.fraction = fraction;
    if (unit !== undefined) item.unit = unit;
    if (counts.length === 1 || dozens === 1 || fraction !== undefined) counted += 1;
    items.push(item);
  }

  return { items, unparsed, counted };
}

/* ==========================================================================
 * 3. The matcher
 * ======================================================================== */

export interface CatalogueEntry {
  sku_id: string;
  name: string;
}

export interface Match {
  sku_id: string;
  name: string;
  /** 0..1. Compare against MATCH_THRESHOLD; it is not a probability. */
  score: number;
}

/**
 * The bar a heard name has to clear before it may appear as a billable line.
 *
 * Calibrated on what each failure costs. BELOW the bar the operator scans or
 * types the item — a few seconds, and they can SEE that the line did not
 * appear. ABOVE the bar but wrong is the wrong product on a customer's bill,
 * found later or never. The two are not the same size, so the bar does not sit
 * in the middle of the gap; it sits in the upper half of it.
 *
 * Measured against this shop's real catalogue (`voice.test.ts` asserts the gap
 * is still there, so a new SKU that closes it fails a test rather than a bill):
 *
 *   things that should match          things that should not
 *   "thums up"  -> ThumsUp   1.000    "pep"     (a fragment)      0.566
 *   "shampoo"   -> sachet    0.951    "namak"   (not stocked)     0.483
 *   "parle g"   -> Parle-G   0.919    "chawal"  (not stocked)     0.307
 *   "sabun"     -> Lifebuoy  0.900    "colgate" (not stocked)     0.263
 *   "thumbs up" -> ThumsUp   0.876
 *   "maggie"    -> Maggi     0.773
 *   "pepsy"     -> Pepsi     0.755
 *
 * The valley runs 0.566 to 0.755. 0.72 clears every wrong answer observed by
 * 0.15 and admits every right one — with 0.035 to spare on the tightest, which
 * is the margin that gets spent when a shopkeeper mumbles.
 */
export const MATCH_THRESHOLD = 0.72;

/**
 * How far clear the winner must be. Two products that score within this of each
 * other are not a match, they are a question — "soap" over a shop with two
 * soaps has no right answer, and picking the higher float is guessing.
 */
export const AMBIGUITY_MARGIN = 0.06;

/**
 * A heard name shorter than this cannot be matched at all.
 *
 * One or two letters are far too easy to score 1.0 against a real product's
 * token — the "g" of Parle-G would match on its own. "das C" therefore ends up
 * as an unknown line rather than as ten of the SKU called 10C, which is the
 * correct outcome: nobody can tell from that sentence which was meant.
 *
 * WHAT IT COSTS: a two-letter product could never be added by voice.
 */
export const MIN_HEARD_CHARS = 3;

/** Longest string pair the edit distance will look at; beyond this it is capped. */
const MAX_CMP = 40;

/**
 * Levenshtein distance, one row of state.
 *
 * The `?? 0` on each row read is noise that `noUncheckedIndexedAccess` demands;
 * every index below is provably in range.
 */
function editDistance(a: string, b: string): number {
  const s = a.slice(0, MAX_CMP);
  const t = b.slice(0, MAX_CMP);
  if (s === t) return 0;
  if (s.length === 0) return t.length;
  if (t.length === 0) return s.length;

  let row = Array.from({ length: t.length + 1 }, (_, j) => j);
  for (let i = 1; i <= s.length; i++) {
    const next: number[] = [i];
    for (let j = 1; j <= t.length; j++) {
      const same = s.charCodeAt(i - 1) === t.charCodeAt(j - 1);
      next.push(Math.min(
        (row[j] ?? 0) + 1,            // delete
        (next[j - 1] ?? 0) + 1,       // insert
        (row[j - 1] ?? 0) + (same ? 0 : 1),
      ));
    }
    row = next;
  }
  return row[t.length] ?? 0;
}

/** 1.0 for identical strings, 0.0 for nothing in common. */
function similarity(a: string, b: string): number {
  if (a === '' || b === '') return 0;
  const span = Math.max(Math.min(a.length, MAX_CMP), Math.min(b.length, MAX_CMP));
  return 1 - editDistance(a, b) / span;
}

/**
 * How much of `from` is accounted for by `to`, weighted by word length.
 *
 * Weighting matters: unweighted, the single letter "g" is half of "parle g" and
 * carries as much evidence as the six letters that actually name the product.
 */
function coverage(from: string[], to: string[]): number {
  let num = 0;
  let den = 0;
  for (const token of from) {
    let best = 0;
    for (const cand of to) best = Math.max(best, similarity(token, cand));
    num += token.length * best;
    den += token.length;
  }
  return den === 0 ? 0 : num / den;
}

/**
 * Split a catalogue string into comparable tokens.
 *
 * SKU ids in this shop are written three ways at once — `parle_g_biscuit`,
 * `ThumsUp`, `10C` — so underscores, hyphens AND camel-case humps are all word
 * boundaries. Without the camel-case rule "thums up" never reaches "ThumsUp".
 *
 * The hump has to be letter-to-capital and not digit-to-capital, or `10C` is
 * split into "10" and "C" — the same mistake the parser refuses to make, made
 * one layer down.
 */
function catTokens(text: string): string[] {
  return text
    .replace(/(\p{Ll})(\p{Lu})/gu, '$1 $2')
    .split(/[\s_\-./]+/u)
    .map(normToken)
    .filter((t) => t.length > 0);
}

/**
 * Score one heard name against one catalogue string.
 *
 * Two views, and the better one wins, because they fail in different places:
 *
 *  - TOKEN COVERAGE handles a short spoken name against a long shelf name —
 *    "soap" against "Lifebuoy soap 125g". Weighted 0.85 towards what was heard,
 *    because a shopkeeper says the short name and means the whole product; the
 *    0.15 the other way is only a tie-break, so an exact "Pepsi" beats "Pepsi
 *    500ml" rather than drawing with it.
 *  - THE JOINED STRING handles a word the speaker ran together or the catalogue
 *    did — "thums up" against "ThumsUp" — where token coverage under-scores
 *    both sides at once.
 */
function scoreAgainst(heardTokens: string[], candTokens: string[]): number {
  const tokenScore = 0.85 * coverage(heardTokens, candTokens)
    + 0.15 * coverage(candTokens, heardTokens);
  const joined = similarity(heardTokens.join(''), candTokens.join(''));
  return Math.max(tokenScore, joined);
}

/** The heard name, tokenised, plus the alias-substituted reading of it. */
function heardReadings(heard: string): { tokens: string[]; aliased: string[] } {
  const tokens = heard.split(/[\s_\-./]+/u).map(normToken).filter((t) => t.length > 0);
  const aliased = tokens.map((t) => ALIASES[t] ?? t);
  return { tokens, aliased };
}

/**
 * Every catalogue entry, scored, best first. Exported because the component
 * needs to tell "I do not stock that" apart from "I cannot tell which of these
 * two you meant" — two refusals that read very differently to a shopkeeper.
 */
export function scoreProducts(
  heard: string,
  catalogue: ReadonlyArray<CatalogueEntry>,
): Match[] {
  const { tokens, aliased } = heardReadings(heard);
  if (tokens.length === 0) return [];

  const scored = catalogue.map((entry) => {
    // Both the shelf name and the SKU id are legitimate things to say out loud.
    // In this shop half the catalogue has no separate name at all — the id IS
    // the name — so scoring only one of them would make those unsayable.
    const fields = [catTokens(entry.name), catTokens(entry.sku_id)];
    let best = 0;
    for (const field of fields) {
      if (field.length === 0) continue;
      best = Math.max(best, scoreAgainst(tokens, field), scoreAgainst(aliased, field));
    }
    return { sku_id: entry.sku_id, name: entry.name, score: best };
  });

  return scored.sort((a, b) => b.score - a.score);
}

/**
 * The one product this heard name means — or null, which is a real answer.
 *
 * Null on three counts, and they are all deliberate: the name is too short to
 * carry evidence, nothing clears the bar, or two products are too close to
 * separate. In every case the caller must show the operator what was heard
 * rather than bill the nearest thing.
 */
export function matchProduct(
  heard: string,
  catalogue: ReadonlyArray<{ sku_id: string; name: string }>,
): Match | null {
  const { tokens } = heardReadings(heard);
  if (tokens.join('').length < MIN_HEARD_CHARS) return null;

  const ranked = scoreProducts(heard, catalogue);
  const top = ranked[0];
  if (!top || top.score < MATCH_THRESHOLD) return null;

  const runnerUp = ranked[1];
  if (!runnerUp || runnerUp.sku_id === top.sku_id) return top;

  // An EXACT name is not a guess. A shop with both "Pepsi" and "Pepsi 500ml
  // bottle" scores the second one high enough to look like a tie, but the
  // operator said the first one's name and nothing else — so an exact reading
  // wins outright, and only ANOTHER exact reading can make it ambiguous.
  const exact = (m: Match) => m.score >= 1 - 1e-9;
  if (exact(top) && !exact(runnerUp)) return top;

  if (top.score - runnerUp.score < AMBIGUITY_MARGIN) return null;
  return top;
}

/* ==========================================================================
 * 4. The microphone
 * ======================================================================== */

/**
 * Minimal shapes for the Web Speech API.
 *
 * Written out here rather than imported: `SpeechRecognition` is not in
 * TypeScript's DOM library (only its result types are), and declaring it
 * globally would fight whatever ships in a future release. These are structural
 * and local, so nothing else in the app inherits a claim about the platform.
 */
interface SpeechAlternativeLike { transcript: string }
interface SpeechResultLike { isFinal: boolean; length: number; [i: number]: SpeechAlternativeLike }
interface SpeechResultListLike { length: number; [i: number]: SpeechResultLike }
interface SpeechResultEventLike { resultIndex: number; results: SpeechResultListLike }
interface SpeechErrorEventLike { error: string; message?: string }

interface RecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((e: SpeechResultEventLike) => void) | null;
  onerror: ((e: SpeechErrorEventLike) => void) | null;
  onend: (() => void) | null;
}

export type RecognitionCtor = new () => RecognitionLike;

export interface Support {
  ok: boolean;
  ctor: RecognitionCtor | null;
  /** Plain English, for showing when `ok` is false. Says what would fix it. */
  reason: string;
}

/**
 * Is there a speech recogniser here at all?
 *
 * `scope` is injected so this is answerable in a test runner with no window,
 * and so the component can be exercised against a stub.
 */
export function speechSupport(scope: unknown = globalThis): Support {
  const w = (scope ?? {}) as Record<string, unknown>;
  const ctor = (w.SpeechRecognition ?? w.webkitSpeechRecognition) as RecognitionCtor | undefined;
  if (typeof ctor !== 'function') {
    return {
      ok: false,
      ctor: null,
      reason: 'This browser does not provide speech recognition. Chrome or Edge will run it; '
        + 'Firefox does not ship the API at all. Until then, scan the packet or type the item.',
    };
  }
  return { ok: true, ctor, reason: '' };
}

/**
 * What went wrong, said the way a shopkeeper can act on.
 *
 * The codes come from the spec's `SpeechRecognitionErrorEvent.error`. Every
 * message names the failure and the fix, because "an error occurred" in the
 * middle of a queue at the counter is worth nothing.
 */
export function micErrorMessage(code: string): string {
  switch (code) {
    case 'not-allowed':
    case 'service-not-allowed':
      return 'The microphone is blocked for this page. Allow it in the browser\'s address-bar '
        + 'permission menu, then press LISTEN again.';
    case 'audio-capture':
      return 'No microphone was found. Plug one in, or pick one in the system sound settings.';
    case 'no-speech':
      return 'Nothing was heard. Press LISTEN and speak towards the microphone.';
    case 'network':
      return 'The speech service could not be reached. Chrome transcribes in the cloud, so voice '
        + 'needs a working internet connection — the camera and the bill do not.';
    case 'aborted':
      return 'Listening stopped.';
    case 'language-not-supported':
      return 'This browser has no voice pack for the chosen language. Switch the language and try again.';
    default:
      return `The microphone stopped: ${code}`;
  }
}

export interface MicEvents {
  /** Words heard but not yet settled. Shown live; never billed. */
  partial(text: string): void;
  /** One settled utterance. This is what gets parsed. */
  final(text: string): void;
  error(message: string): void;
  end(): void;
}

/**
 * Which language to hand the recogniser.
 *
 * DEFAULT IS 'hi-IN' because the counting words and the sentence shape are
 * Hindi, and that is what decides whether "do" is heard as a number at all.
 * The trade is real and it is why this is a prop: 'en-IN' catches English brand
 * names far better — "Thums Up", "Lifebuoy", "Parle-G" come back as roman
 * letters that match the catalogue directly — while 'hi-IN' returns Devanagari
 * for those, which this module can only report as unknown. A shop whose
 * catalogue is entirely English brands should be run on 'en-IN'.
 */
export const DEFAULT_LANG = 'hi-IN';

/**
 * THE COUNTER'S OWN EARS — a recorder, when the browser's recogniser cannot
 * reach its service.
 *
 * `SpeechRecognition` is a cloud call to Google's speech service wearing a
 * browser API's clothes. On a shop's wifi it fails with `network` and the
 * microphone simply stops: "The speech service could not be reached", over a
 * till that is otherwise working. That is what this is for.
 *
 * It records with `MediaRecorder` and posts the bytes to the till, which
 * writes them down on the same key that already reasons and speaks (see
 * `gawaah/stt.py`). Same origin, so the till's `default-src 'self'` needs no
 * widening — and a browser that cannot reach Google's speech service certainly
 * cannot be handed a third-party one.
 *
 * IT IS THE SECOND CHOICE, NOT THE FIRST. The browser's recogniser needs no
 * key, costs nothing, and streams partial words as they are spoken, which this
 * cannot: a recording is one utterance, delivered when the speaker stops. So
 * this runs only when the first has failed or is absent, and it says so.
 */
export class ServerEars {
  private rec: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private stream: MediaStream | null = null;
  private startedAt = 0;
  private cancelled = false;

  constructor(private readonly opts: { lang?: string; on: MicEvents }) {}

  get listening(): boolean { return this.rec !== null; }

  /** The formats a browser will actually give us, in the order the server
      prefers them. Chrome gives webm/opus; Safari gives mp4/aac. */
  private static pickMime(): string {
    const want = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'];
    const MR = (globalThis as { MediaRecorder?: typeof MediaRecorder }).MediaRecorder;
    if (!MR?.isTypeSupported) return '';
    return want.find((m) => MR.isTypeSupported(m)) ?? '';
  }

  static get supported(): boolean {
    return typeof navigator !== 'undefined'
      && !!navigator.mediaDevices?.getUserMedia
      && typeof (globalThis as { MediaRecorder?: unknown }).MediaRecorder === 'function';
  }

  async start(): Promise<void> {
    if (this.rec) return;
    if (!ServerEars.supported) {
      this.opts.on.error('This browser cannot record audio, so the counter cannot listen for you. Type the order instead.');
      return;
    }
    this.cancelled = false;
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      // The same refusal the recogniser gives, in the same words.
      this.opts.on.error(micErrorMessage('not-allowed'));
      return;
    }
    const mime = ServerEars.pickMime();
    const rec = mime ? new MediaRecorder(this.stream, { mimeType: mime })
                     : new MediaRecorder(this.stream);
    this.chunks = [];
    this.startedAt = Date.now();
    rec.ondataavailable = (e) => { if (e.data.size > 0) this.chunks.push(e.data); };
    rec.onstop = () => { void this.send(rec.mimeType || mime || 'audio/webm'); };
    rec.start();
    this.rec = rec;
    // A HARD CAP, because the server has one and being refused after twenty
    // seconds of talking is worse than being stopped at fifteen.
    setTimeout(() => { if (this.rec === rec) this.stop(); }, 15_000);
  }

  stop(): void {
    const rec = this.rec;
    this.rec = null;
    try { rec?.stop(); } catch { /* already stopped */ }
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
  }

  /** Stop without transcribing — the operator changed their mind. */
  cancel(): void { this.cancelled = true; this.stop(); }

  private async send(mime: string): Promise<void> {
    const seconds = (Date.now() - this.startedAt) / 1000;
    const blob = new Blob(this.chunks, { type: mime });
    this.chunks = [];
    this.opts.on.end();
    if (this.cancelled || blob.size < 1200) return;   // a tap, not a sentence
    try {
      const buf = new Uint8Array(await blob.arrayBuffer());
      let bin = '';
      for (let i = 0; i < buf.length; i += 0x8000) {
        bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
      }
      const r = await fetch('/advisor/listen', {
        method: 'POST',
        cache: 'no-store',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ audio_b64: btoa(bin), mime, lang: this.opts.lang ?? DEFAULT_LANG, seconds }),
      });
      const d = (await r.json()) as { ok?: boolean; text?: string; detail?: string; reason?: string };
      if (!r.ok || !d.ok) {
        this.opts.on.error(d.detail || d.reason || 'The counter could not write down what was said.');
        return;
      }
      if (d.text) this.opts.on.final(d.text.trim());
    } catch (e) {
      this.opts.on.error(`The counter could not be reached to write that down. ${String(e)}`);
    }
  }
}

/**
 * A thin, restartable wrapper. It owns exactly one recogniser at a time and
 * hands text out; it holds no transcript, so the component stays the only
 * place where heard words accumulate.
 */
export class VoiceMic {
  private rec: RecognitionLike | null = null;
  private stopping = false;

  constructor(private readonly opts: { lang?: string; on: MicEvents; scope?: unknown }) {}

  get listening(): boolean {
    return this.rec !== null;
  }

  start(): void {
    if (this.rec) return;
    const support = speechSupport(this.opts.scope ?? globalThis);
    if (!support.ctor) {
      this.opts.on.error(support.reason);
      return;
    }
    const rec = new support.ctor();
    rec.lang = this.opts.lang ?? DEFAULT_LANG;
    // Continuous, so a shopkeeper can read out a whole basket in one press
    // instead of pressing the button between every item.
    rec.continuous = true;
    rec.interimResults = true;
    // One reading only. A second-best transcript is another guess, and this
    // module already refuses to pick between two things that sound alike.
    rec.maxAlternatives = 1;

    rec.onresult = (e) => {
      let partial = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const result = e.results[i];
        if (!result) continue;
        const alt = result[0];
        if (!alt) continue;
        if (result.isFinal) this.opts.on.final(alt.transcript.trim());
        else partial += alt.transcript;
      }
      this.opts.on.partial(partial.trim());
    };

    rec.onerror = (e) => {
      // A stop WE asked for arrives as 'aborted'. Reporting it would put an
      // error on screen every time the operator finished speaking.
      if (e.error === 'aborted' && this.stopping) return;
      this.opts.on.error(micErrorMessage(e.error));
    };

    // `stopping` is NOT cleared here. Chrome fires onerror('aborted') and onend
    // in either order, and clearing the flag first put "Listening stopped." on
    // screen as an error every time the operator finished speaking.
    rec.onend = () => {
      this.rec = null;
      this.opts.on.end();
    };

    this.rec = rec;
    this.stopping = false;
    try {
      rec.start();
    } catch (err) {
      // start() throws if called twice, and Chrome also throws here when the
      // page is not on a secure origin — worth saying, because it looks
      // identical to a dead button otherwise.
      this.rec = null;
      this.opts.on.error(
        `The microphone would not start: ${String(err)}. Voice needs https, or localhost.`,
      );
    }
  }

  stop(): void {
    const rec = this.rec;
    if (!rec) return;
    this.stopping = true;
    try {
      rec.stop();
    } catch {
      // Already stopped; onend may never fire, so let go of it here too.
      this.rec = null;
      this.opts.on.end();
    }
  }
}

/* ==========================================================================
 * 5. Order or question?
 * ========================================================================
 *
 * Salaahkaar stands at the till and hears two kinds of sentence: "do Maggi aur
 * ek Parle-G", which is an order she puts on the bill as a proposal, and
 * "Parle-G ka daam kya hai", which is a question she answers out loud and
 * never touches the bill for. The two go to different endpoints, and the
 * choice has to be made BEFORE anything is sent — so it is made here, in a
 * pure function with a test, and it is written on the screen beside her
 * answer so a wrong choice is visible rather than mysterious.
 *
 * THE ORDER OF THE RULES IS THE SERVER'S. `gawaah/assistant.py::local_route`
 * checks the shop's topic words (price, stock, takings, expenses…) before it
 * looks for a count in front of a product, and reads one bare product name as
 * a question and two as an order. This classifier keeps the same precedence so
 * the two disagree as rarely as possible; when they do, the server's reading
 * wins and the screen says so (`Told.reread`).
 */

/**
 * Words that make a sentence ABOUT the shop rather than an order for a thing.
 * Every one of them is in the server's own keyword sets; a word here that the
 * server does not know would send a question down a path that refuses it.
 */
const SHOP_WORDS = [
  // price
  'price', 'prices', 'daam', 'dam', 'damm', 'rate', 'bhav', 'bhaav', 'kimat',
  'keemat', 'kimmat', 'mrp', 'cost', 'costs', 'mullo', 'mulyo', 'dor',
  'दाम', 'कीमत', 'क़ीमत', 'भाव', 'রেट', 'दर', 'দাম', 'মূল্য', 'দর',
  // takings, stock, money, people
  'takings', 'bikri', 'bikree', 'bikroy', 'sale', 'sales', 'kamai', 'kamaai',
  'collection', 'galla', 'gulla', 'revenue', 'turnover', 'total',
  'stock', 'stok', 'khatam', 'khatm', 'khatham', 'bacha', 'bache', 'bachi',
  'restock', 'inventory', 'shelf', 'sesh', 'furiye',
  'kharch', 'kharcha', 'kharche', 'expense', 'expenses', 'spent', 'khoroch',
  'cash', 'drawer', 'nakad', 'nagad', 'tijori',
  'margin', 'munafa', 'munaafa', 'profit', 'labh', 'fayda', 'faida', 'lav',
  'supplier', 'suppliers', 'distributor', 'wholesaler', 'mahajan',
  'customer', 'customers', 'grahak', 'graahak', 'khoddar',
  'category', 'categories', 'offer', 'offers', 'discount', 'discounts',
  'chhoot', 'scheme', 'gst', 'tax', 'hsn', 'expiry', 'expired', 'expiring',
  'loyalty', 'points', 'order', 'orders', 'ordar', 'delivery', 'parcel',
  'close', 'closing', 'daybook', 'hisab', 'hisaab', 'hishab', 'khata',
  'बिक्री', 'स्टॉक', 'खर्च', 'खर्चा', 'ख़र्च', 'ख़र्चा', 'नकद', 'गल्ला',
  'मुनाफा', 'मुनाफ़ा', 'फायदा', 'फ़ायदा', 'ग्राहक', 'छूट', 'ऑर्डर', 'हिसाब',
  'कमाई', 'खत्म', 'ख़त्म',
  'বিক্রি', 'স্টক', 'খরচ', 'নগদ', 'লাভ', 'খদ্দের', 'ছাড়', 'অর্ডার', 'হিসাব',
];

/** The furniture of a question, in the three languages. */
const QUESTION_WORDS = [
  'kya', 'kyaa', 'kitna', 'kitne', 'kitni', 'kaun', 'kaunsa', 'kaunse',
  'konsa', 'konse', 'kab', 'kahan', 'kaha', 'kyon', 'kyun', 'kaise', 'kaisa',
  'kaisi', 'batao', 'bataiye', 'dikhao', 'milega', 'milta', 'milti',
  'how', 'what', 'which', 'when', 'where', 'why', 'who', 'much', 'many',
  'tell', 'show', 'is', 'are', 'does',
  'koto', 'kotota', 'kotogulo', 'kothay', 'kobe', 'kemon', 'keno', 'kon',
  'konta', 'ache', 'achhe',
  'क्या', 'कितना', 'कितने', 'कितनी', 'कौन', 'कौनसा', 'कब', 'कहाँ', 'कहां',
  'क्यों', 'कैसे', 'कैसा', 'बताओ', 'बताइए', 'दिखाओ', 'मिलेगा',
  'কি', 'কী', 'কত', 'কতটা', 'কোথায়', 'কবে', 'কেমন', 'কেন', 'কোন', 'কোনটা',
  'আছে',
];

/** Words that only ever mean "put this on the bill". */
const ADD_VERBS = [
  'add', 'jodo', 'jod', 'likho', 'likh', 'lagao', 'lagado', 'chadhao',
  'daalo', 'dalo', 'daldo', 'daaldo',
  'जोड़ो', 'लिखो', 'डालो', 'लगाओ', 'चढ़ाओ', 'লিখো', 'যোগ',
];

const SHOP_WORDS_N = vocab(SHOP_WORDS);
const QUESTION_WORDS_N = vocab(QUESTION_WORDS);
const ADD_VERBS_N = vocab(ADD_VERBS);

export type Route = 'order' | 'advice';

/** WHY a sentence went where it went. Shown on the screen, key by key. */
export type Why =
  | 'shop_word'      // a price / stock / takings word: a question about the shop
  | 'question_word'  // kya, kitna, how, koto…
  | 'nothing'        // no product in it at all; the server says why
  | 'add_verb'       // "add karo", "likho"
  | 'weight'         // a half or a quarter before a product
  | 'count'          // a count or a dozen before a product
  | 'several'        // two products named, no question asked
  | 'one_bare';      // one product named alone: "Maggi?" — a question, as on the server

export interface Classified {
  route: Route;
  why: Why;
  heard: Heard;
}

/** Order or question. Pure; the same sentence always goes the same way. */
export function classifyUtterance(text: string): Classified {
  const heard = parseHinglish(text ?? '');
  const tokens = (text ?? '').split(/\s+/).map(normToken).filter((t) => t.length > 0);
  const has = (set: Set<string>) => tokens.some((t) => set.has(t));

  if (has(SHOP_WORDS_N)) return { route: 'advice', why: 'shop_word', heard };
  if (has(QUESTION_WORDS_N) || /\?\s*$/.test(text ?? '')) {
    return { route: 'advice', why: 'question_word', heard };
  }
  if (heard.items.length === 0) return { route: 'advice', why: 'nothing', heard };
  if (has(ADD_VERBS_N)) return { route: 'order', why: 'add_verb', heard };
  if (heard.items.some((i) => i.fraction !== undefined)) {
    return { route: 'order', why: 'weight', heard };
  }
  if (heard.counted > 0) return { route: 'order', why: 'count', heard };
  if (heard.items.length > 1) return { route: 'order', why: 'several', heard };
  return { route: 'advice', why: 'one_bare', heard };
}

/* ==========================================================================
 * 6. The voice out
 * ======================================================================== */

/**
 * What this browser can say with, and in which language.
 *
 * Copied from routes/Advisor.tsx rather than imported: that route is loaded
 * lazily and the till is the eager bundle, so importing from it would drag the
 * whole advisor screen into the first paint of the counter.
 */
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

/* ==========================================================================
 * 7. What she says back
 * ========================================================================
 *
 * The confirmation is composed HERE, from the server's own proposal, and not
 * on the server: the server's `answer` is one English sentence for every
 * caller, and a shopkeeper who spoke Hindi should hear Hindi back. Every
 * figure in it is the server's integer paise, said as rupees; nothing is
 * computed except the split of paise into rupees and paise, which is an
 * integer division.
 */

/** One catalogue line the server proposed. Integer paise, read defensively. */
export interface ProposedLine {
  sku_id: string;
  name: string;
  qty: number;
  unit_paise: number;
  line_paise: number;
  by: 'packet' | 'weighed';
  /** "500 g", on a weighed line. The server's words. */
  weight?: string;
  /** The phrase the server resolved this from, when it can be told apart. */
  heard?: string;
}

export interface BillProposal {
  proposal_id: string;
  lines: ProposedLine[];
  total_paise: number;
  /** A dozen multiplied out, a weight read as packets. Shown, never hidden. */
  caution: string | null;
  audited: boolean;
}

/** The counting words she says a quantity with. Past twelve, the digits. */
const COUNT_WORDS: Record<string, readonly string[]> = {
  hi: ['', 'एक', 'दो', 'तीन', 'चार', 'पाँच', 'छह', 'सात', 'आठ', 'नौ', 'दस', 'ग्यारह', 'बारह'],
  bn: ['', 'একটা', 'দুটো', 'তিনটে', 'চারটে', 'পাঁচটা', 'ছ’টা', 'সাতটা', 'আটটা', 'নয়টা', 'দশটা', 'এগারোটা', 'বারোটা'],
};

/** The two letters before the dash of a BCP-47 tag: 'hi-IN' -> 'hi'. */
function langBase(lang: string): string {
  return (lang ?? '').toLowerCase().split('-')[0] ?? '';
}

export function countWord(n: number, lang: string): string {
  const table = COUNT_WORDS[langBase(lang)];
  const word = table?.[n];
  return word && n >= 1 ? word : String(n);
}

/**
 * Integer paise as a sum a voice can read: "28 रुपये", "27 रुपये 50 पैसे".
 * Integer arithmetic only — the same split `lib/money.ts` makes, restated
 * because this string is for a mouth and that one is for a receipt.
 */
export function saidRupees(paise: number, lang: string): string {
  if (!Number.isInteger(paise) || paise < 0) {
    throw new Error(`saidRupees: ${String(paise)} is not integer paise`);
  }
  const rest = paise % 100;
  const whole = (paise - rest) / 100;
  const words = {
    hi: ['रुपये', 'पैसे'], bn: ['টাকা', 'পয়সা'], en: ['rupees', 'paise'],
  }[langBase(lang)] ?? ['rupees', 'paise'];
  return rest === 0 ? `${whole} ${words[0]}` : `${whole} ${words[0]} ${rest} ${words[1]}`;
}

/**
 * The catalogue name as a mouth would say it: the parenthetical Devanagari
 * gloss dropped ("Maggi 2-Minute Noodles 70 g (मैगी नूडल्स)" -> the first
 * half), because a voice reading both is reading the same name twice.
 */
export function spokenName(name: string): string {
  const cut = name.indexOf(' (');
  const bare = cut > 0 ? name.slice(0, cut) : name;
  return bare.replace(/\s+/g, ' ').trim() || name;
}

/** The sentence she says once the lines are on the bill as proposed. */
export function confirmation(p: BillProposal, lang: string): string {
  const base = langBase(lang);
  const parts = p.lines.map((l) => (
    l.by === 'weighed' && l.weight
      ? `${l.weight} ${spokenName(l.name)}`
      : `${countWord(l.qty, lang)} ${spokenName(l.name)}`
  ));
  const prices = p.lines.map((l) => saidRupees(l.line_paise, lang));
  const several = p.lines.length > 1;
  const total = saidRupees(p.total_paise, lang);
  if (base === 'hi') {
    return `${parts.join(' और ')} — ${prices.join(' और ')}${several ? `, कुल ${total}` : ''}। `
      + 'बिल पर रख दिया है, accept कर दीजिए।';
  }
  if (base === 'bn') {
    return `${parts.join(' আর ')} — ${prices.join(' আর ')}${several ? `, মোট ${total}` : ''}। `
      + 'বিলে রেখেছি, accept করুন।';
  }
  return `${parts.join(' and ')} — ${prices.join(' and ')}${several ? `, ${total} in all` : ''}. `
    + 'On the bill as proposed; accept to bill it.';
}

/* ==========================================================================
 * 8. The wire
 * ========================================================================
 *
 * TWO PATHS AND NO OTHERS. An order goes to `/assistant/ask`, which resolves
 * the products, prices them from the catalogue and writes a proposal down;
 * a question goes to `/advisor/say`, which answers and remembers the call.
 * Neither of them can mint a link, and this file has no way to reach the
 * endpoints that can: `voice.test.ts` drives this function with a fake fetch
 * and asserts that nothing it sends goes anywhere but these two paths. CHARGE
 * is a button on the till, pressed by a hand, and nothing said out loud gets
 * near it (invariant 6).
 *
 * THE BODY IS THE SENTENCE. `{text, source}` and, for a question, the
 * language and the call id. No sku, no quantity, no price: the server refuses
 * a request that carries one, by name, and it is right to.
 */
export const SALAAHKAAR_PATHS = {
  order: '/assistant/ask',
  advice: '/advisor/say',
} as const;

/** The server's name for "that was an order, and this is a call". */
const NOT_A_COUNTER = 'this_is_a_call_not_the_till';

export interface TellOpts {
  source: 'text' | 'voice';
  /** BCP-47: what the answer should be phrased and voiced in. */
  lang: string;
  /** The advisor's call id from the last question, so "uska daam" works. */
  sessionId?: string | null;
  /** Injected for the test. */
  fetchImpl?: typeof fetch;
}

/**
 * KHATA. "Sharma ji ke khate mein likh do": the server proposes putting the
 * bill on the counter onto ONE household's book. Read defensively like a bill
 * proposal; a person accepts it on the till, where ON THE BOOK asks for the
 * number if the book does not know one yet. Nothing here books anything.
 */
export interface BookProposal {
  proposal_id: string;
  name: string;
  phone: string | null;
  phone_masked: string | null;
  book_id: string | null;
  /** True when the book already has this household. */
  known: boolean;
  outstanding_rupees: string | null;
  /** What the shopkeeper actually said the customer's name was. */
  said: string;
}

export function readBookProposal(raw: unknown): BookProposal | null {
  const p = rec(raw);
  if (!p || p.kind !== 'khata_book') return null;
  const id = str(p.proposal_id);
  const c = rec(p.customer);
  if (!id || !c) return null;
  const name = str(c.name);
  if (!name) return null;
  return {
    proposal_id: id,
    name,
    phone: str(c.phone),
    phone_masked: str(c.phone_masked),
    book_id: str(c.book_id),
    known: c.known === true,
    outstanding_rupees: str(c.outstanding_rupees),
    said: str(c.said) ?? name,
  };
}

export type Told =
  | {
      kind: 'proposal';
      route: Classified;
      heard: string;
      proposal: BillProposal;
      /** The server's own English sentence, shown beside the spoken one. */
      answer: string;
      brain: string | null;
    }
  | {
      /** ON THE BOOK, proposed. The till draws it beside the bill. */
      kind: 'book';
      route: Classified;
      heard: string;
      proposal: BookProposal;
      answer: string;
      spoken: string;
      brain: string | null;
    }
  | {
      kind: 'answer';
      route: Classified;
      heard: string;
      tool: string | null;
      answer: string;
      /** THE ONE STRING TO SAY ALOUD. */
      spoken: string;
      sessionId: string | null;
      brain: string | null;
      /**
       * Set when the server read the sentence the other way from this file:
       * 'as_question' — sent as an order, answered as a question, bill
       * untouched; 'as_order' — sent as a question, the call refused it as
       * an order, so it was put to the till instead.
       */
      reread?: 'as_question' | 'as_order';
    }
  | {
      kind: 'refusal';
      route: Classified;
      heard: string;
      reason: string;
      detail: string;
      brain: string | null;
    };

type Body = Record<string, unknown>;

async function post(
  fetchImpl: typeof fetch, url: string, body: Body,
): Promise<{ ok: true; body: Body } | { ok: false; reason: string; detail: string; body: Body | null }> {
  let res: Response;
  try {
    res = await fetchImpl(url, {
      method: 'POST',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch (e) {
    return { ok: false, reason: 'the counter could not be reached', detail: String(e), body: null };
  }
  let parsed: unknown = null;
  try { parsed = await res.json(); } catch { /* answered with something that is not JSON */ }
  const b = parsed !== null && typeof parsed === 'object' ? (parsed as Body) : null;
  if (b && b.ok === true) return { ok: true, body: b };
  return {
    ok: false,
    reason: typeof b?.reason === 'string' ? b.reason : `http_${res.status}`,
    detail: typeof b?.detail === 'string' ? b.detail : `The counter answered HTTP ${res.status}.`,
    body: b,
  };
}

const str = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null);
const int = (v: unknown): number | null =>
  (typeof v === 'number' && Number.isInteger(v) ? v : null);
const rec = (v: unknown): Body | null =>
  (v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Body) : null);

/**
 * The server's proposal, or null. ABSTAINS on any line it cannot read in
 * full: a proposal with a non-integer paisa in it is not rounded, it is
 * refused, because the alternative is a number on a bill nobody typed.
 */
export function readProposal(raw: unknown, phrases: ReadonlyArray<string> = []): BillProposal | null {
  const p = rec(raw);
  if (!p || p.kind !== 'bill') return null;
  const id = str(p.proposal_id);
  const total = int(p.total_paise);
  if (!id || total === null || !Array.isArray(p.lines) || p.lines.length === 0) return null;
  const lines: ProposedLine[] = [];
  for (const l of p.lines) {
    const line = rec(l);
    if (!line) return null;
    const sku_id = str(line.sku_id);
    const name = str(line.name) ?? sku_id;
    const qty = int(line.qty);
    const unit_paise = int(line.unit_paise);
    const line_paise = int(line.line_paise);
    if (!sku_id || !name || qty === null || qty < 1 || unit_paise === null || line_paise === null) return null;
    const by: ProposedLine['by'] = line.by === 'weighed' ? 'weighed' : 'packet';
    const out: ProposedLine = { sku_id, name, qty, unit_paise, line_paise, by };
    const weight = str(line.weight);
    if (weight) out.weight = weight;
    lines.push(out);
  }
  // The phrase each line came from, but only when the two lists line up. The
  // server merges "do Maggi aur ek Maggi" into one line, and after that the
  // phrases and the lines no longer correspond — so nothing is claimed.
  if (phrases.length === lines.length) {
    lines.forEach((l, i) => { const h = phrases[i]; if (h) l.heard = h; });
  }
  return {
    proposal_id: id, lines, total_paise: total,
    caution: str(p.caution), audited: p.audited === true,
  };
}

/** The product phrases the server's parser or model pulled out, in order. */
function phrasesOf(args: unknown): string[] {
  const a = rec(args);
  if (!a) return [];
  const out: string[] = [];
  const one = str(a.product);
  if (one) out.push(one);
  if (Array.isArray(a.items)) {
    for (const it of a.items) {
      if (typeof it === 'string') out.push(it);
      else { const p = str(rec(it)?.product); if (p) out.push(p); }
    }
  }
  return out;
}

/** Tell her one sentence. Never throws; every failure is a named refusal. */
export async function tellSalaahkaar(text: string, opts: TellOpts): Promise<Told> {
  const fetchImpl = opts.fetchImpl ?? fetch;
  const heard = (text ?? '').trim();
  const route = classifyUtterance(heard);

  const order = async (reread?: 'as_order'): Promise<Told> => {
    const r = await post(fetchImpl, SALAAHKAAR_PATHS.order, { text: heard, source: opts.source });
    if (!r.ok) {
      return { kind: 'refusal', route, heard, reason: r.reason, detail: r.detail, brain: str(r.body?.brain) };
    }
    const b = r.body;
    const tool = str(b.tool);
    const answer = str(b.answer) ?? '';
    const brain = str(b.brain);
    if (tool === 'add_to_bill') {
      const proposal = readProposal(b.proposal, phrasesOf(b.arguments));
      if (!proposal) {
        return {
          kind: 'refusal', route, heard, brain,
          reason: 'the proposal could not be read',
          detail: 'The counter answered with a bill proposal this screen could not read in full '
            + '— a line without an integer price, or none at all. Nothing was put on the bill.',
        };
      }
      return { kind: 'proposal', route, heard, proposal, answer, brain };
    }
    if (tool === 'book_on_khata') {
      const proposal = readBookProposal(b.proposal);
      if (!proposal) {
        return {
          kind: 'refusal', route, heard, brain,
          reason: 'the proposal could not be read',
          detail: 'The counter answered with a khata proposal this screen could not read in full '
            + '— no household name on it. Nothing was put on the book.',
        };
      }
      return { kind: 'book', route, heard, proposal, answer, spoken: answer, brain };
    }
    // Sent as an order, answered as something else: the server's reading of
    // the sentence wins, the answer is spoken, and the bill is left alone.
    const told: Told = { kind: 'answer', route, heard, tool, answer, spoken: answer, sessionId: opts.sessionId ?? null, brain };
    if (reread) told.reread = reread;
    else if (route.route === 'order') told.reread = 'as_question';
    return told;
  };

  if (route.route === 'order') return order();

  const body: Body = { text: heard, source: opts.source, lang: opts.lang };
  if (opts.sessionId) body.session_id = opts.sessionId;
  const r = await post(fetchImpl, SALAAHKAAR_PATHS.advice, body);
  if (!r.ok) {
    // The call refused it as an order. That is the server's parser saying
    // this file misread the sentence — so it goes where the server says it
    // belongs, and the screen says that happened.
    if (r.reason === NOT_A_COUNTER) return order('as_order');
    return {
      kind: 'refusal', route, heard, reason: r.reason, detail: r.detail,
      brain: str(r.body?.brain),
    };
  }
  const b = r.body;
  const answer = str(b.answer) ?? '';
  return {
    kind: 'answer', route, heard,
    tool: str(b.tool),
    answer,
    spoken: str(b.spoken) ?? answer,
    sessionId: str(b.session_id) ?? opts.sessionId ?? null,
    brain: str(b.brain),
  };
}
