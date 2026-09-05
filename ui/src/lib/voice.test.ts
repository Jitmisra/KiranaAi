import { describe, it, expect } from 'vitest';
import {
  parseHinglish, matchProduct, scoreProducts, speechSupport, micErrorMessage,
  VoiceMic, MATCH_THRESHOLD, MAX_QTY, DEFAULT_LANG,
  type MicEvents,
} from './voice';

/**
 * The real shop, plus the two products the demo sentence names.
 *
 * Half of it has no separate display name — the SKU id IS what is written on
 * the shelf label — and one id is `10C`, which is why the parser may never
 * split a digit prefix off a token.
 */
const SHOP = [
  { sku_id: 'parle_g_biscuit', name: 'Parle-G biscuit 100g' },
  { sku_id: 'lifebuoy_soap', name: 'Lifebuoy soap 125g' },
  { sku_id: 'shampoo_sachet', name: 'Clinic shampoo sachet' },
  { sku_id: 'ThumsUp', name: 'ThumsUp' },
  { sku_id: 'PONDS', name: 'PONDS' },
  { sku_id: 'maxfresh', name: 'maxfresh' },
  { sku_id: 'tretin', name: 'tretin' },
  { sku_id: '10C', name: '10C' },
  { sku_id: 'maggi_noodles', name: 'Maggi noodles 70g' },
  { sku_id: 'pepsi_can', name: 'Pepsi can 250ml' },
];

const names = (t: string) => parseHinglish(t).items.map((i) => `${i.qty}x${i.name}`);
const sku = (heard: string) => matchProduct(heard, SHOP)?.sku_id ?? null;

/* ---------------------------------------------------------------- parser -- */

describe('turning a heard sentence into lines', () => {
  it('reads the sentence this feature exists for', () => {
    const heard = parseHinglish('do Maggi aur ek Pepsi');
    expect(heard.items).toEqual([
      { name: 'Maggi', qty: 2, raw: 'do Maggi' },
      { name: 'Pepsi', qty: 1, raw: 'ek Pepsi' },
    ]);
    expect(heard.unparsed).toEqual([]);
  });

  it('knows the Hindi counting words a kirana actually uses', () => {
    const table: Array<[string, number]> = [
      ['ek', 1], ['do', 2], ['teen', 3], ['char', 4], ['chaar', 4],
      ['panch', 5], ['paanch', 5], ['chhe', 6], ['che', 6], ['chah', 6],
      ['saat', 7], ['aath', 8], ['nau', 9], ['das', 10],
      ['gyarah', 11], ['barah', 12], ['pandrah', 15], ['bees', 20],
    ];
    for (const [word, qty] of table) {
      expect(names(`${word} maggi`), `${word} should be ${qty}`).toEqual([`${qty}xmaggi`]);
    }
  });

  it('knows the English counting words too, because en-IN returns them', () => {
    expect(names('three maggi')).toEqual(['3xmaggi']);
    expect(names('twelve maggi')).toEqual(['12xmaggi']);
    expect(names('twenty maggi')).toEqual(['20xmaggi']);
  });

  it('reads a bare digit as a count', () => {
    expect(names('2 maggi')).toEqual(['2xmaggi']);
  });

  it('takes the count after the name as readily as before it', () => {
    expect(names('Maggi do')).toEqual(['2xMaggi']);
    expect(names('do Maggi')).toEqual(['2xMaggi']);
  });

  it('bills one when no count was said', () => {
    expect(parseHinglish('Pepsi').items).toEqual([{ name: 'Pepsi', qty: 1, raw: 'Pepsi' }]);
  });

  it('separates on aur, on and, and on a comma', () => {
    expect(names('ek soap and do shampoo, teen pepsi')).toEqual(['1xsoap', '2xshampoo', '3xpepsi']);
  });

  it('drops unit words from the name instead of billing them as the product', () => {
    // "do kilo chawal" is two of chawal, not two of "kilo chawal".
    expect(parseHinglish('do kilo chawal').items).toEqual([
      { name: 'chawal', qty: 2, raw: 'do kilo chawal', unit: 'kilo' },
    ]);
    expect(names('teen packet parle g')).toEqual(['3xparle g']);
    expect(names('ek bottle pepsi')).toEqual(['1xpepsi']);
  });

  it('never eats the g of Parle-G as a unit', () => {
    expect(names('do parle g biscuit')).toEqual(['2xparle g biscuit']);
  });

  it('ignores politeness, and reads "de do" as give rather than as two', () => {
    expect(names('bhaiya ek Maggi de do')).toEqual(['1xMaggi']);
    expect(names('mujhe do Pepsi chahiye')).toEqual(['2xPepsi']);
  });

  /* ---- the failure cases ------------------------------------------------ */

  it('says nothing about an empty transcript', () => {
    expect(parseHinglish('')).toEqual({ items: [], unparsed: [], counted: 0 });
    expect(parseHinglish('   ')).toEqual({ items: [], unparsed: [], counted: 0 });
  });

  it('surfaces a quantity with no product rather than inventing one', () => {
    const heard = parseHinglish('do kilo');
    expect(heard.items).toEqual([]);
    expect(heard.unparsed).toEqual(['do kilo']);
  });

  it('refuses a fragment with two counts in it instead of picking one', () => {
    // "do teen Maggi" is a person hesitating between two and three. There is no
    // safe number to bill, so the sentence goes back to the operator.
    const heard = parseHinglish('do teen Maggi');
    expect(heard.items).toEqual([]);
    expect(heard.unparsed).toEqual(['do teen Maggi']);
  });

  it('refuses a count larger than a counter order, and keeps the good half', () => {
    const heard = parseHinglish('200 maggi aur do pepsi');
    expect(heard.items).toEqual([{ name: 'pepsi', qty: 2, raw: 'do pepsi' }]);
    expect(heard.unparsed).toEqual(['200 maggi']);
    expect(MAX_QTY).toBe(99);
  });

  it('refuses zero of something', () => {
    expect(parseHinglish('zero maggi').items.map((i) => i.qty)).not.toContain(0);
  });

  it('keeps a digit that is part of a brand name attached to it', () => {
    // 10C is a SKU in this shop, and 7up is a drink. Splitting the digits off
    // would turn a product into a quantity and lose the product entirely.
    expect(parseHinglish('10C').items).toEqual([{ name: '10C', qty: 1, raw: '10C' }]);
    expect(names('do 7up')).toEqual(['2x7up']);
    expect(names('do 10C')).toEqual(['2x10C']);
  });

  it('reads Devanagari counting words, which is what hi-IN returns', () => {
    const heard = parseHinglish('दो मैगी और एक पेप्सी');
    expect(heard.items.map((i) => i.qty)).toEqual([2, 1]);
    expect(heard.items.map((i) => i.name)).toEqual(['मैगी', 'पेप्सी']);
  });
});

/* --------------------------------------------------------------- matcher -- */

describe('placing a heard name in the catalogue', () => {
  it('finds the obvious ones', () => {
    expect(sku('maggi')).toBe('maggi_noodles');
    expect(sku('pepsi')).toBe('pepsi_can');
    expect(sku('ponds')).toBe('PONDS');
    expect(sku('shampoo')).toBe('shampoo_sachet');
    expect(sku('biscuit')).toBe('parle_g_biscuit');
  });

  it('survives the mishearings a speech engine really produces', () => {
    expect(sku('maggie')).toBe('maggi_noodles');
    expect(sku('pepsy')).toBe('pepsi_can');
    expect(sku('thums up')).toBe('ThumsUp');
    expect(sku('thumbs up')).toBe('ThumsUp');
    expect(sku('parle g')).toBe('parle_g_biscuit');
  });

  it('returns null for a product this shop does not stock', () => {
    // Rice, salt and milk are real kirana words and none of them is taught.
    expect(sku('chawal')).toBeNull();
    expect(sku('namak')).toBeNull();
    expect(sku('colgate')).toBeNull();
  });

  it('refuses a name too short to carry any evidence', () => {
    // "das C" parses as ten of "C". Nobody can tell from that sentence whether
    // the SKU 10C was meant, so it is shown back rather than billed.
    const heard = parseHinglish('das C');
    expect(heard.items).toEqual([{ name: 'C', qty: 10, raw: 'das C' }]);
    expect(matchProduct('C', SHOP)).toBeNull();
    expect(matchProduct('', SHOP)).toBeNull();
  });

  it('places 10C exactly, said as one word', () => {
    expect(sku('10C')).toBe('10C');
    expect(sku('10c')).toBe('10C');
  });

  it('refuses when two products are too close to separate', () => {
    // Two soaps and the word "soap": there is no right answer, and picking the
    // higher float would be a guess wearing a number.
    const twoSoaps = [
      { sku_id: 'lifebuoy_soap', name: 'Lifebuoy soap 125g' },
      { sku_id: 'lux_soap', name: 'Lux soap 125g' },
    ];
    expect(matchProduct('soap', twoSoaps)).toBeNull();
    // Name one of them and the tie is broken.
    expect(matchProduct('lifebuoy soap', twoSoaps)?.sku_id).toBe('lifebuoy_soap');
  });

  it('breaks a near-tie towards the exact name, not the longer one', () => {
    const twoPepsis = [
      { sku_id: 'pepsi', name: 'Pepsi' },
      { sku_id: 'pepsi_500', name: 'Pepsi 500ml bottle' },
    ];
    expect(matchProduct('pepsi', twoPepsis)?.sku_id).toBe('pepsi');
  });

  it('understands the Hindi word for soap through a written-down alias', () => {
    expect(sku('sabun')).toBe('lifebuoy_soap');
    expect(sku('biskut')).toBe('parle_g_biscuit');
  });

  it('will not match on a fragment of a word', () => {
    // Two letters missing from a five-letter name is not a mishearing, it is a
    // different word.
    expect(sku('pep')).toBeNull();
  });

  it('scores every entry and sorts them, so a caller can say WHY it abstained', () => {
    const ranked = scoreProducts('soap', SHOP);
    expect(ranked).toHaveLength(SHOP.length);
    expect(ranked[0]?.sku_id).toBe('lifebuoy_soap');
    expect(ranked[0]?.score).toBeGreaterThan(MATCH_THRESHOLD);
    for (let i = 1; i < ranked.length; i++) {
      expect(ranked[i - 1]?.score).toBeGreaterThanOrEqual(ranked[i]?.score ?? 0);
    }
  });

  it('keeps a measured gap between what it accepts and what it refuses', () => {
    // The threshold is only defensible while this gap exists. A new SKU that
    // closes it should fail here — loudly, in a test — rather than quietly
    // start putting a near-miss on somebody's bill.
    const shouldMatch = ['thums up', 'shampoo', 'parle g', 'sabun', 'thumbs up', 'maggie', 'pepsy'];
    const shouldNot = ['pep', 'namak', 'chawal', 'colgate', 'tooth paste'];
    const best = (h: string) => scoreProducts(h, SHOP)[0]?.score ?? 0;

    const worstTrue = Math.min(...shouldMatch.map(best));
    const bestFalse = Math.max(...shouldNot.map(best));
    expect(bestFalse).toBeLessThan(MATCH_THRESHOLD);
    expect(worstTrue).toBeGreaterThan(MATCH_THRESHOLD);
    // ...and the bar sits in the upper half of the gap, because a refusal costs
    // a few seconds and a wrong line costs a customer.
    expect(MATCH_THRESHOLD).toBeGreaterThan((worstTrue + bestFalse) / 2);
  });

  it('answers null over an empty catalogue rather than throwing', () => {
    expect(matchProduct('maggi', [])).toBeNull();
    expect(scoreProducts('maggi', [])).toEqual([]);
  });

  it('reports a Devanagari product name as unknown rather than guessing', () => {
    // hi-IN returns the brand in Devanagari and the catalogue is in roman
    // letters. That is a real gap, and the honest answer is "I do not know it"
    // — which is why the recogniser language is a prop.
    expect(matchProduct('मैगी', SHOP)).toBeNull();
  });
});

/* ------------------------------------------------------------ end to end -- */

describe('a whole sentence, parsed and placed', () => {
  it('proposes what it knows and reports what it does not', () => {
    const heard = parseHinglish('do Maggi aur ek sabun aur teen chawal');
    const placed = heard.items.map((i) => ({ qty: i.qty, sku: sku(i.name), raw: i.raw }));
    expect(placed).toEqual([
      { qty: 2, sku: 'maggi_noodles', raw: 'do Maggi' },
      { qty: 1, sku: 'lifebuoy_soap', raw: 'ek sabun' },
      { qty: 3, sku: null, raw: 'teen chawal' },
    ]);
  });
});

/* ------------------------------------------------------------ microphone -- */

/** A recogniser that never touches an audio device. */
class FakeRecogniser {
  static last: FakeRecogniser | null = null;
  lang = '';
  continuous = false;
  interimResults = false;
  maxAlternatives = 0;
  starts = 0;
  stops = 0;
  /* eslint-disable @typescript-eslint/no-explicit-any */
  onresult: ((e: any) => void) | null = null;
  onerror: ((e: any) => void) | null = null;
  onend: (() => void) | null = null;

  constructor() {
    FakeRecogniser.last = this;
  }

  start() { this.starts++; }
  stop() { this.stops++; this.onend?.(); }
  abort() {}

  /** Feed it words the way Chrome does: interim first, then final. */
  say(transcript: string, isFinal: boolean) {
    const result = Object.assign([{ transcript }], { isFinal, length: 1 });
    this.onresult?.({ resultIndex: 0, results: Object.assign([result], { length: 1 }) });
  }
}

const collector = () => {
  const log: string[] = [];
  const on: MicEvents = {
    partial: (t) => log.push(`partial:${t}`),
    final: (t) => log.push(`final:${t}`),
    error: (m) => log.push(`error:${m}`),
    end: () => log.push('end'),
  };
  return { log, on };
};

describe('the microphone, degrading honestly', () => {
  it('says which browser is needed when the API is absent', () => {
    const s = speechSupport({});
    expect(s.ok).toBe(false);
    expect(s.ctor).toBeNull();
    expect(s.reason).toMatch(/Chrome or Edge/);
    expect(s.reason).toMatch(/Firefox/);
  });

  it('accepts the webkit-prefixed constructor', () => {
    expect(speechSupport({ webkitSpeechRecognition: FakeRecogniser }).ok).toBe(true);
    expect(speechSupport({ SpeechRecognition: FakeRecogniser }).ok).toBe(true);
  });

  it('turns a permission refusal into a sentence with a fix in it', () => {
    expect(micErrorMessage('not-allowed')).toMatch(/microphone is blocked/);
    expect(micErrorMessage('not-allowed')).toMatch(/permission menu/);
    expect(micErrorMessage('audio-capture')).toMatch(/No microphone was found/);
    expect(micErrorMessage('no-speech')).toMatch(/Nothing was heard/);
    expect(micErrorMessage('network')).toMatch(/internet connection/);
    expect(micErrorMessage('weird-new-code')).toMatch(/weird-new-code/);
  });

  it('reports the missing API through the same channel when start is pressed', () => {
    const { log, on } = collector();
    new VoiceMic({ on, scope: {} }).start();
    expect(log).toHaveLength(1);
    expect(log[0]).toMatch(/^error:This browser does not provide speech recognition/);
  });

  it('listens in Hindi by default and hands interim and final text apart', () => {
    const { log, on } = collector();
    const mic = new VoiceMic({ on, scope: { SpeechRecognition: FakeRecogniser } });
    mic.start();
    const rec = FakeRecogniser.last;
    expect(rec?.lang).toBe(DEFAULT_LANG);
    expect(rec?.continuous).toBe(true);
    expect(rec?.interimResults).toBe(true);
    expect(mic.listening).toBe(true);

    rec?.say('do mag', false);
    rec?.say('do Maggi', true);
    expect(log).toEqual(['partial:do mag', 'final:do Maggi', 'partial:']);

    mic.stop();
    expect(mic.listening).toBe(false);
    expect(log.at(-1)).toBe('end');
  });

  it('takes the language as a prop, for a shop of English brand names', () => {
    const { on } = collector();
    new VoiceMic({ on, lang: 'en-IN', scope: { SpeechRecognition: FakeRecogniser } }).start();
    expect(FakeRecogniser.last?.lang).toBe('en-IN');
  });

  it('does not report the abort caused by pressing stop', () => {
    const { log, on } = collector();
    const mic = new VoiceMic({ on, scope: { SpeechRecognition: FakeRecogniser } });
    mic.start();
    const rec = FakeRecogniser.last;
    mic.stop();
    rec?.onerror?.({ error: 'aborted' });
    expect(log.filter((l) => l.startsWith('error:'))).toEqual([]);
  });

  it('does report an abort nobody asked for', () => {
    const { log, on } = collector();
    const mic = new VoiceMic({ on, scope: { SpeechRecognition: FakeRecogniser } });
    mic.start();
    FakeRecogniser.last?.onerror?.({ error: 'not-allowed' });
    expect(log.some((l) => l.includes('microphone is blocked'))).toBe(true);
  });
});

/* ======================================================================
 * Salaahkaar at the counter: the three languages, the two paths, the one
 * thing she may never do.
 * ==================================================================== */

import {
  classifyUtterance, pickVoice, voiceGenderRank, saidRupees, countWord, spokenName, confirmation,
  readProposal, tellSalaahkaar, SALAAHKAAR_PATHS, type BillProposal,
} from './voice';

describe('counting in three languages', () => {
  it('reads Bengali counting words, roman and script', () => {
    const table: Array<[string, number]> = [
      ['duto', 2], ['dui', 2], ['tin', 3], ['tinte', 3], ['pach', 5], ['chhoy', 6],
      ['dosh', 10], ['baro', 12], ['kuri', 20],
      ['দুটো', 2], ['দুই', 2], ['তিন', 3], ['পাঁচ', 5], ['ছয়', 6], ['দশ', 10], ['বারো', 12],
    ];
    for (const [word, qty] of table) {
      expect(names(`${word} maggi`), `${word} should be ${qty}`).toEqual([`${qty}xmaggi`]);
    }
  });

  it('folds Bengali digits the way it folds Devanagari ones', () => {
    expect(names('২ maggi')).toEqual(['2xmaggi']);
    expect(names('२ maggi')).toEqual(['2xmaggi']);
  });

  it('splits a Bengali sentence at "ar" and "ebong"', () => {
    expect(names('duto maggi ar ekta parle')).toEqual(['2xmaggi', '1xparle']);
    expect(names('দুটো maggi আর একটা parle')).toEqual(['2xmaggi', '1xparle']);
  });

  it('leaves "at" and "sat" alone, because they are English far more often', () => {
    expect(names('maggi at the counter')).toEqual(['1xmaggi at the counter']);
  });
});

describe('a dozen, and a weight', () => {
  it('multiplies a dozen by twelve, in all three spellings', () => {
    expect(names('ek dozen maggi')).toEqual(['12xmaggi']);
    expect(names('a dozen maggi')).toEqual(['12xmaggi']);
    expect(names('do darjan maggi')).toEqual(['24xmaggi']);
    expect(names('एक दर्जन maggi')).toEqual(['12xmaggi']);
    expect(names('এক ডজন maggi')).toEqual(['12xmaggi']);
  });

  it('records the unit it heard, so a dozen is visibly a dozen', () => {
    expect(parseHinglish('ek dozen maggi').items[0]?.unit).toBe('dozen');
    expect(parseHinglish('do kilo chawal').items[0]?.unit).toBe('kilo');
  });

  it('keeps a half or a quarter as a fraction and does not invent a count', () => {
    const half = parseHinglish('aadha kilo chawal');
    expect(half.items).toEqual([{ name: 'chawal', qty: 1, raw: 'aadha kilo chawal', fraction: 'aadha', unit: 'kilo' }]);
    expect(half.counted).toBe(1);
    expect(parseHinglish('half kilo rice').items[0]?.fraction).toBe('half');
    expect(parseHinglish('ordhek kilo chal').items[0]?.fraction).toBe('ordhek');
    expect(parseHinglish('আধা কিলো chal').items[0]?.fraction).toBe('আধা');
  });

  it('refuses two fractions in one breath, like two counts', () => {
    const h = parseHinglish('aadha pav kilo chawal');
    expect(h.items).toEqual([]);
    expect(h.unparsed).toEqual(['aadha pav kilo chawal']);
  });

  it('counts only the items whose quantity was actually said', () => {
    expect(parseHinglish('maggi').counted).toBe(0);
    expect(parseHinglish('maggi aur parle').counted).toBe(0);
    expect(parseHinglish('do maggi aur parle').counted).toBe(1);
    expect(parseHinglish('ek dozen maggi').counted).toBe(1);
  });
});

/* ---------------------------------------------------------- the classifier -- */

describe('order or question', () => {
  const route = (s: string) => classifyUtterance(s).route;
  const why = (s: string) => classifyUtterance(s).why;

  it('sends a count before a product to the bill', () => {
    expect(classifyUtterance('do Maggi aur ek Parle-G')).toMatchObject({ route: 'order', why: 'count' });
    expect(route('दो मैगी और एक पारले जी')).toBe('order');
    expect(route('duto Maggi ar ekta Parle')).toBe('order');
    expect(route('2 Maggi')).toBe('order');
    expect(route('ek dozen Maggi')).toBe('order');
  });

  it('sends a price question to the advisor, whichever language it is asked in', () => {
    expect(classifyUtterance('Parle-G ka daam kya hai')).toMatchObject({ route: 'advice', why: 'shop_word' });
    expect(route('Parle-G ka daam?')).toBe('advice');
    expect(route('पारले जी का दाम क्या है')).toBe('advice');
    expect(route('Parle-G er daam koto')).toBe('advice');
    expect(route('পার্লে জি-র দাম কত')).toBe('advice');
    expect(route('what is the price of Maggi')).toBe('advice');
  });

  it('lets a shop word win over a count, as the server does', () => {
    // "do Maggi ka daam" is a question about the price of two, not an order.
    expect(why('do Maggi ka daam')).toBe('shop_word');
    expect(why('aaj ki bikri kitni hui')).toBe('shop_word');
    expect(why('Maggi stock me kitna bacha')).toBe('shop_word');
  });

  it('reads a question word as a question even with a product in it', () => {
    expect(classifyUtterance('Maggi milega kya')).toMatchObject({ route: 'advice', why: 'question_word' });
    expect(why('kitne Maggi hain?')).toBe('question_word');
    expect(why('Maggi ache?')).toBe('question_word');
  });

  it('reads one bare product name as a question and two as an order', () => {
    expect(classifyUtterance('Maggi')).toMatchObject({ route: 'advice', why: 'one_bare' });
    expect(classifyUtterance('Maggi aur Parle-G')).toMatchObject({ route: 'order', why: 'several' });
  });

  it('reads an add verb as an order and a weight as an order', () => {
    expect(classifyUtterance('Maggi add karo')).toMatchObject({ route: 'order', why: 'add_verb' });
    expect(classifyUtterance('Maggi likho')).toMatchObject({ route: 'order', why: 'add_verb' });
    expect(classifyUtterance('aadha kilo chawal')).toMatchObject({ route: 'order', why: 'weight' });
    expect(classifyUtterance('half kilo rice')).toMatchObject({ route: 'order', why: 'weight' });
  });

  it('sends nothing-in-particular to the advisor, which refuses it by name', () => {
    expect(classifyUtterance('')).toMatchObject({ route: 'advice', why: 'nothing' });
    expect(classifyUtterance('haan theek hai')).toMatchObject({ route: 'advice', why: 'nothing' });
  });
});

/* ----------------------------------------------------------- what she says -- */

describe('what she says back', () => {
  const p: BillProposal = {
    proposal_id: 'prop_1',
    lines: [
      { sku_id: 'maggi', name: 'Maggi 2-Minute Noodles 70 g (मैगी नूडल्स)', qty: 2, unit_paise: 1400, line_paise: 2800, by: 'packet' },
      { sku_id: 'parle', name: 'Parle-G biscuit 100g', qty: 1, unit_paise: 1000, line_paise: 1000, by: 'packet' },
    ],
    total_paise: 3800, caution: null, audited: true,
  };

  it('says integer paise as rupees with no arithmetic but a split', () => {
    expect(saidRupees(2800, 'hi-IN')).toBe('28 रुपये');
    expect(saidRupees(2750, 'hi-IN')).toBe('27 रुपये 50 पैसे');
    expect(saidRupees(1000, 'bn-IN')).toBe('10 টাকা');
    expect(saidRupees(5, 'en-IN')).toBe('0 rupees 5 paise');
    expect(() => saidRupees(27.5, 'hi-IN')).toThrow(/integer/);
  });

  it('counts in the language she was spoken to in', () => {
    expect(countWord(2, 'hi-IN')).toBe('दो');
    expect(countWord(2, 'bn-IN')).toBe('দুটো');
    expect(countWord(2, 'en-IN')).toBe('2');
    expect(countWord(13, 'hi-IN')).toBe('13');
  });

  it('drops the parenthetical gloss from a name before saying it', () => {
    expect(spokenName('Maggi 2-Minute Noodles 70 g (मैगी नूडल्स)')).toBe('Maggi 2-Minute Noodles 70 g');
    expect(spokenName('Parle-G biscuit 100g')).toBe('Parle-G biscuit 100g');
  });

  it('confirms the lines, the prices and the total, and says they wait to be accepted', () => {
    const hi = confirmation(p, 'hi-IN');
    expect(hi).toBe('दो Maggi 2-Minute Noodles 70 g और एक Parle-G biscuit 100g — 28 रुपये और 10 रुपये, कुल 38 रुपये। बिल पर रख दिया है, accept कर दीजिए।');
    expect(confirmation(p, 'bn-IN')).toContain('দুটো Maggi');
    expect(confirmation(p, 'bn-IN')).toContain('মোট 38 টাকা');
    const en = confirmation(p, 'en-IN');
    expect(en).toContain('2 Maggi 2-Minute Noodles 70 g and 1 Parle-G biscuit 100g');
    expect(en).toContain('38 rupees in all');
    expect(en).toContain('accept');
  });

  it('says a weighed line by its weight', () => {
    const w: BillProposal = {
      ...p, lines: [{ sku_id: 'rice', name: 'Basmati rice', qty: 1, unit_paise: 4950, line_paise: 4950, by: 'weighed', weight: '500 g' }],
      total_paise: 4950,
    };
    expect(confirmation(w, 'hi-IN')).toContain('500 g Basmati rice — 49 रुपये 50 पैसे');
  });
});

/* -------------------------------------------------------------- the wire -- */

describe('reading a proposal', () => {
  const line = { sku_id: 'maggi', name: 'Maggi', qty: 2, unit_paise: 1400, line_paise: 2800, by: 'packet' };
  const doc = { kind: 'bill', proposal_id: 'prop_1', lines: [line], total_paise: 2800, caution: null, audited: true };

  it('reads the server\'s own shape', () => {
    expect(readProposal(doc, ['Maggi'])).toEqual({
      proposal_id: 'prop_1', total_paise: 2800, caution: null, audited: true,
      lines: [{ ...line, heard: 'Maggi' }],
    });
  });

  it('abstains on a paisa that is not an integer, rather than rounding it', () => {
    expect(readProposal({ ...doc, lines: [{ ...line, line_paise: 28.5 }] })).toBeNull();
    expect(readProposal({ ...doc, total_paise: '2800' })).toBeNull();
  });

  it('abstains on a proposal that is not a bill', () => {
    expect(readProposal({ ...doc, kind: 'stock_movement' })).toBeNull();
    expect(readProposal({ ...doc, lines: [] })).toBeNull();
  });

  it('attaches heard phrases only when they line up with the lines', () => {
    expect(readProposal(doc, ['Maggi', 'Maggi'])?.lines[0]?.heard).toBeUndefined();
  });
});

/**
 * A fetch that records every URL and answers to order. The two paths she may
 * use answer with the server's real shapes; anything else answers 404, which
 * is what the till would do — and the assertion is that nothing else is asked.
 */
function fakeFetch(answers: Record<string, unknown>) {
  const calls: Array<{ url: string; body: unknown }> = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
    const a = answers[url];
    const status = a === undefined ? 404 : ((a as { ok?: boolean }).ok === false ? 400 : 200);
    return new Response(JSON.stringify(a ?? { ok: false, reason: 'no_such_route', detail: 'nothing here' }), {
      status, headers: { 'Content-Type': 'application/json' },
    });
  }) as typeof fetch;
  return { calls, impl };
}

const PROPOSAL = {
  ok: true, tool: 'add_to_bill', brain: 'local', answer: '2 x Maggi comes to Rs 28.00.',
  arguments: { items: [{ product: 'Maggi', qty: 2 }, { product: 'Parle-G', qty: 1 }] },
  proposal: {
    kind: 'bill', proposal_id: 'prop_9', accepted: false, audited: true, caution: null,
    lines: [
      { sku_id: 'maggi', name: 'Maggi', qty: 2, unit_paise: 1400, line_paise: 2800, by: 'packet' },
      { sku_id: 'parle', name: 'Parle-G', qty: 1, unit_paise: 1000, line_paise: 1000, by: 'packet' },
    ],
    total_paise: 3800,
  },
};

describe('telling her', () => {
  it('sends an order to the assistant with the sentence and its source, and nothing else', async () => {
    const f = fakeFetch({ [SALAAHKAAR_PATHS.order]: PROPOSAL });
    const r = await tellSalaahkaar('do Maggi aur ek Parle-G', { source: 'voice', lang: 'hi-IN', fetchImpl: f.impl });
    expect(f.calls.map((c) => c.url)).toEqual(['/assistant/ask']);
    expect(f.calls[0]?.body).toEqual({ text: 'do Maggi aur ek Parle-G', source: 'voice' });
    expect(r.kind).toBe('proposal');
    if (r.kind !== 'proposal') return;
    expect(r.proposal.lines.map((l) => [l.sku_id, l.qty, l.heard])).toEqual([['maggi', 2, 'Maggi'], ['parle', 1, 'Parle-G']]);
    expect(r.proposal.total_paise).toBe(3800);
  });

  it('sends a question to the advisor with the language and the call id', async () => {
    const f = fakeFetch({
      [SALAAHKAAR_PATHS.advice]: { ok: true, tool: 'price_of', answer: 'Parle-G is Rs 10.00.', spoken: 'Parle-G dus rupaye ka hai.', session_id: 'call_1', brain: 'local' },
    });
    const r = await tellSalaahkaar('Parle-G ka daam kya hai', { source: 'text', lang: 'hi-IN', sessionId: 'call_0', fetchImpl: f.impl });
    expect(f.calls.map((c) => c.url)).toEqual(['/advisor/say']);
    expect(f.calls[0]?.body).toEqual({ text: 'Parle-G ka daam kya hai', source: 'text', lang: 'hi-IN', session_id: 'call_0' });
    expect(r).toMatchObject({ kind: 'answer', tool: 'price_of', spoken: 'Parle-G dus rupaye ka hai.', sessionId: 'call_1' });
  });

  it('puts a sentence the call refused as an order to the till instead, and says so', async () => {
    const f = fakeFetch({
      [SALAAHKAAR_PATHS.advice]: { ok: false, reason: 'this_is_a_call_not_the_till', detail: 'that would put something on a bill' },
      [SALAAHKAAR_PATHS.order]: PROPOSAL,
    });
    // One bare product is a question to this file; the server may disagree.
    const r = await tellSalaahkaar('Maggi', { source: 'text', lang: 'en-IN', fetchImpl: f.impl });
    expect(f.calls.map((c) => c.url)).toEqual(['/advisor/say', '/assistant/ask']);
    expect(r.kind).toBe('proposal');
  });

  it('reports the server reading an order as a question, and touches no bill', async () => {
    const f = fakeFetch({
      [SALAAHKAAR_PATHS.order]: { ok: true, tool: 'find_product', answer: 'Lifebuoy is taught.', brain: 'local', proposal: null },
    });
    const r = await tellSalaahkaar('do lifebuoy', { source: 'text', lang: 'en-IN', fetchImpl: f.impl });
    expect(r).toMatchObject({ kind: 'answer', tool: 'find_product', reread: 'as_question' });
  });

  it('hands a refusal back by name, with the server\'s own words', async () => {
    const f = fakeFetch({
      [SALAAHKAAR_PATHS.order]: { ok: false, reason: 'no_such_product_in_this_shop', detail: "this shop has nothing called 'pepsi'." },
    });
    const r = await tellSalaahkaar('do Pepsi', { source: 'voice', lang: 'hi-IN', fetchImpl: f.impl });
    expect(r).toMatchObject({ kind: 'refusal', reason: 'no_such_product_in_this_shop' });
  });

  it('refuses a proposal it cannot read in full rather than putting part of it on the bill', async () => {
    const bad = { ...PROPOSAL, proposal: { ...PROPOSAL.proposal, lines: [{ sku_id: 'maggi', name: 'Maggi', qty: 2, unit_paise: 14.5, line_paise: 29 }] } };
    const f = fakeFetch({ [SALAAHKAAR_PATHS.order]: bad });
    const r = await tellSalaahkaar('do Maggi', { source: 'voice', lang: 'hi-IN', fetchImpl: f.impl });
    expect(r.kind).toBe('refusal');
  });

  it('never throws: a dead counter is a named refusal', async () => {
    const impl = (async () => { throw new TypeError('Failed to fetch'); }) as unknown as typeof fetch;
    const r = await tellSalaahkaar('do Maggi', { source: 'voice', lang: 'hi-IN', fetchImpl: impl });
    expect(r).toMatchObject({ kind: 'refusal', reason: 'the counter could not be reached' });
  });

  /**
   * INVARIANT 6: SHE NEVER CHARGES.
   *
   * Every sentence shape this file can classify, sent through the real wire
   * with a fetch that records every URL. The only paths that may appear are
   * the two she has; the money paths may not appear at all — not on a
   * proposal, not on an answer, not on a refusal, not on the reroute.
   */
  it('reaches /assistant/ask and /advisor/say and nothing on the money path, whatever is said', async () => {
    const MONEY = /\/(intent|pay|mint|scan|qr|charge|session|webhook)\b/;
    const sentences = [
      'do Maggi aur ek Parle-G', 'Parle-G ka daam kya hai', 'Maggi', 'Maggi aur Parle-G',
      'aadha kilo chawal', 'Maggi add karo', 'aaj ki bikri', 'charge karo', 'pay', 'CHARGE ₹38.00',
      'ek Maggi charge kar do', '', 'haan',
    ];
    const seen = new Set<string>();
    for (const s of sentences) {
      const f = fakeFetch({
        [SALAAHKAAR_PATHS.order]: PROPOSAL,
        [SALAAHKAAR_PATHS.advice]: { ok: false, reason: 'this_is_a_call_not_the_till', detail: 'x' },
      });
      await tellSalaahkaar(s, { source: 'voice', lang: 'hi-IN', fetchImpl: f.impl });
      for (const c of f.calls) seen.add(c.url);
    }
    expect([...seen].sort()).toEqual([SALAAHKAAR_PATHS.order, SALAAHKAAR_PATHS.advice].sort());
    for (const url of seen) expect(url, `${url} is a money path`).not.toMatch(MONEY);
  });
});

/* -------------------------------------------------------------- the voice -- */

describe('picking a voice', () => {
  const v = (name: string, lang: string, def = false) => ({ name, lang, default: def }) as SpeechSynthesisVoice;

  it('prefers the exact tag, then the language, then Indian English', () => {
    const voices = [v('Rishi', 'en-IN'), v('Lekha', 'hi-IN'), v('Alex', 'en-US', true)];
    expect(pickVoice(voices, 'hi-IN').voice?.name).toBe('Lekha');
    expect(pickVoice(voices, 'bn-IN')).toMatchObject({ matched: false });
    expect(pickVoice(voices, 'bn-IN').voice?.name).toBe('Rishi');
    expect(pickVoice([v('Alex', 'en-US', true)], 'hi-IN').note).toMatch(/mispronounce/);
  });

  it('says so when there is nothing to speak with', () => {
    expect(pickVoice([], 'hi-IN')).toMatchObject({ voice: null, matched: false });
  });
});

/* ==========================================================================
 * The fallback voice's gender
 *
 * When the provider 429s, this picker is what the shopkeeper hears. Salaahkaar
 * is drawn as a woman, so the tier that answers must not open with Rishi.
 * ======================================================================== */

function v(name: string, lang: string, isDefault = false): SpeechSynthesisVoice {
  return { name, lang, default: isDefault, localService: true,
           voiceURI: name } as SpeechSynthesisVoice;
}

describe('pickVoice prefers a woman inside the tier it lands on', () => {
  it('takes Veena over Rishi when both are en-IN', () => {
    const chosen = pickVoice([v('Rishi', 'en-IN'), v('Veena', 'en-IN')], 'en-IN');
    expect(chosen.voice?.name).toBe('Veena');
    expect(chosen.matched).toBe(true);
  });

  it('takes Lekha over Hemant for hi-IN', () => {
    const chosen = pickVoice([v('Hemant', 'hi-IN'), v('Lekha', 'hi-IN')], 'hi-IN');
    expect(chosen.voice?.name).toBe('Lekha');
  });

  it('does not reach past its tier to find her', () => {
    // An exact hi-IN match, even a man's, beats a woman in another language:
    // the language is the stronger claim and mispronunciation is worse.
    const chosen = pickVoice([v('Veena', 'en-IN'), v('Hemant', 'hi-IN')], 'hi-IN');
    expect(chosen.voice?.name).toBe('Hemant');
  });

  it('keeps the browser order when it knows neither name', () => {
    const chosen = pickVoice([v('Aarav', 'en-IN'), v('Bhavna', 'en-IN')], 'en-IN');
    expect(chosen.voice?.name).toBe('Aarav');
  });

  it('ranks an unknown name above a man', () => {
    const chosen = pickVoice([v('Ravi', 'en-IN'), v('Aarav', 'en-IN')], 'en-IN');
    expect(chosen.voice?.name).toBe('Aarav');
  });

  it('reads "female" in a name without matching "male" inside it', () => {
    expect(voiceGenderRank('Google UK English Female')).toBe(-1);
    expect(voiceGenderRank('Google UK English Male')).toBe(1);
    expect(voiceGenderRank('Rishi')).toBe(1);
    expect(voiceGenderRank('Veena')).toBe(-1);
    expect(voiceGenderRank('Aarav')).toBe(0);
  });

  it('still says nothing is installed when the list is empty', () => {
    expect(pickVoice([], 'hi-IN').voice).toBeNull();
  });
});

/* ==========================================================================
 * Two counts in one breath — the natural Hindi order
 * ======================================================================== */

describe('a count after a name starts a new line', () => {
  it('reads "do Maggi ek Parle-G" as two lines, not a hesitation', () => {
    const h = parseHinglish('do Maggi ek Parle-G');
    expect(h.items.map((i) => [i.qty, i.name])).toEqual([[2, 'Maggi'], [1, 'Parle-G']]);
    expect(h.counted).toBe(2);
    expect(h.unparsed).toEqual([]);
    expect(classifyUtterance('do Maggi ek Parle-G')).toMatchObject({ route: 'order', why: 'count' });
  });
  it('does the same in Devanagari, three lines, no conjunction', () => {
    const h = parseHinglish('दो मैगी एक पार्ले जी एक पॉन्ड्स क्रीम');
    expect(h.items.map((i) => i.qty)).toEqual([2, 1, 1]);
    expect(h.unparsed).toEqual([]);
    expect(classifyUtterance('दो मैगी एक पार्ले जी एक पॉन्ड्स क्रीम').route).toBe('order');
  });
  it('still treats "do teen Maggi" as a person hesitating', () => {
    const h = parseHinglish('do teen Maggi');
    expect(h.items).toEqual([]);
    expect(h.unparsed).toEqual(['do teen Maggi']);
  });
  it('still reads a trailing count: "Maggi do" is two Maggi', () => {
    expect(parseHinglish('Maggi do').items).toEqual([{ name: 'Maggi', qty: 2, raw: 'Maggi do' }]);
  });
  it('routes a count the parser could not attach to the order door, not the advisor', () => {
    // A name the client cannot see is the server's problem; it refuses by name.
    expect(classifyUtterance('2 xyzzy').route).toBe('order');
  });
});
