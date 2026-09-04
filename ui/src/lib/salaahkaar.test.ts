import { describe, it, expect } from 'vitest';
import {
  routeTurn, describeAction, undoPlan, readApplied, readTopSellers, readLowRows, pickVoice,
  suggestions, sayableProducts, shortName, hasRupees, tokens, LANGS, isLangTag,
} from './salaahkaar';
import type { Proposal } from './assistantapi';

/**
 * THE ROUTING DECISION IS THE WHOLE MERGE. Two brains behind one composer, and
 * the browser picks per sentence. The costly mistake is an instruction sent to
 * the advisor, which has no tool that changes anything and answers it as the
 * nearest question — measured: "do Maggi bill me daal do" came back as a
 * price. So every instruction shape a shopkeeper actually says is pinned here
 * as ACTION, and every question shape as ADVICE, including the ones that share
 * a word.
 */
describe('routeTurn — which brain', () => {
  const action = (s: string) => expect(routeTurn(s).route, s).toBe('action');
  const advice = (s: string) => expect(routeTurn(s).route, s).toBe('advice');

  it('sends bill instructions to the assistant', () => {
    action('do Maggi bill me daal do');
    action('Maggi daal do');
    action('ek Pepsi add karo');
    action('teen Parle G lagao');
    action('add two Maggi');
    action('bill me Thums Up');
    action('do Maggi');
    action('2 Maggi');
    // Bengali
    action('duto Maggi dao');
    action('bille Maggi');
  });

  it('says which kind of action it expects', () => {
    expect(routeTurn('do Maggi bill me daal do').hint).toBe('bill');
    expect(routeTurn('10 Maggi aaye supplier se').hint).toBe('stock_movement');
    expect(routeTurn('200 rupaye chai ka kharcha likh do').hint).toBe('expense');
  });

  it('sends stock movements to the assistant, whatever the reason', () => {
    action('10 Maggi aaye');
    action('das Maggi aaya supplier se');
    action('do Pepsi toot gaye');
    action('ek Amul wapas aaya');
    action('teen Maggi expire ho gaye');
    action('do Maggi ghar le gaya');
    action('ek carton Maggi supplier ko wapas bhejo');
    // reason unsaid: still the assistant's to refuse BY NAME
    action('do Maggi hatao');
  });

  it('sends a recorded expense to the assistant, and a spend question to the advisor', () => {
    action('200 rupaye chai ka kharcha likh do');
    action('kharcha 150 rickshaw');
    action('₹80 spent on tea');
    advice('aaj ka kharcha kitna hua');
    advice('what did we spend today');
  });

  it('sends every question to the advisor', () => {
    advice('aaj ki bikri kitni hui');
    advice('aaj ka munafa kitna hua');
    advice('kitne orders pending hain');
    advice('kya khatam ho raha hai');
    advice('Maggi ka daam kya hai');
    advice('Thums Up milega kya');
    advice('aaj sabse zyada kya bika');
    advice('what should I reorder this week');
    advice('should I stock more milk in winter');
    advice('ajker bikri koto');
  });

  it('a question wins over an add verb', () => {
    // "add" is in the sentence, but the sentence asks.
    advice('kya Maggi add hua?');
    advice('which items should I add to the shelf');
    advice('kitne Maggi aaye is hafte');
  });

  it('does not read a count-and-product as an order when the sentence is long', () => {
    // A number at the front of a long sentence is not "do Maggi".
    advice('2 din se Pepsi ki bikri kaisi chal rahi hai');
  });

  it('names the cue that decided it', () => {
    expect(routeTurn('do Maggi bill me daal do').cue).toBe('daal do');
    expect(routeTurn('ek Pepsi add karo').cue).toBe('add');
    expect(routeTurn('aaj ki bikri kitni hui').cue).toBeNull();
  });

  it('reads a rupee figure in the shapes people type it', () => {
    for (const s of ['200 rupaye', '₹200', 'rs 200', 'Rs. 200', '200 ka chai', '150']) {
      expect(hasRupees(s, tokens(s)), s).toBe(true);
    }
    expect(hasRupees('do Maggi', tokens('do Maggi'))).toBe(false);
    expect(hasRupees('2 Maggi', tokens('2 Maggi'))).toBe(false);
  });
});

/* ------------------------------------------------------------- actions -- */

const bill: Proposal = {
  format: 2, kind: 'bill', proposal_id: 'prop_a', at: '', brain: 'gemini', accepted: false,
  lines: [{
    sku_id: 'maggi', name: 'Maggi 2-Minute Noodles 70 g (मैगी नूडल्स)', qty: 2,
    unit_paise: 1400, unit_rupees: '14.00', line_paise: 2800, line_rupees: '28.00', taught_with: 'photo',
  }],
  total_paise: 2800, total_rupees: '28.00', caution: null, note: '',
};
const movement: Proposal = {
  format: 2, kind: 'stock_movement', proposal_id: 'prop_m', at: '', brain: 'gemini', accepted: false,
  lines: [], total_paise: 0, total_rupees: '0.00', caution: null, note: '',
  movement: { sku_id: 'maggi', name: 'Maggi (मैगी)', direction: 'in', units: 10, reason: 'delivery', reason_label: 'a delivery arrived', note: '' },
  accept_by: { method: 'POST', path: '/stock/maggi/in', body: { units: 10, reason: 'delivery', note: null } },
};
const expense: Proposal = {
  format: 2, kind: 'expense', proposal_id: 'prop_e', at: '', brain: 'gemini', accepted: false,
  lines: [], total_paise: 20000, total_rupees: '200.00', caution: null, note: '',
  expense: { amount_paise: 20000, amount_rupees: '200.00', category: 'tea', category_label: 'Tea and snacks', note: 'chai', paid_with: 'cash' },
  accept_by: { method: 'POST', path: '/expenses', body: { amount_paise: 20000, category: 'tea', note: 'chai', paid_with: 'cash' } },
};

describe('describeAction — the line on the turn', () => {
  it('says proposed until a person presses, and did afterwards', () => {
    expect(describeAction(bill, 'proposed')).toBe('proposed: 2× Maggi 2-Minute Noodles 70 g for the bill — not billed');
    expect(describeAction(bill, 'applied')).toBe('did: held 2× Maggi 2-Minute Noodles 70 g for the till — not billed');
    expect(describeAction(bill, 'undone')).toContain('undone:');
  });

  it('never says a bill line was billed', () => {
    for (const s of ['proposed', 'applied', 'undone'] as const) {
      expect(describeAction(bill, s)).not.toContain('added to the bill');
      expect(describeAction(bill, s)).toMatch(/not billed|never billed/);
    }
  });

  it('describes a movement with the reason, and an expense with the category', () => {
    expect(describeAction(movement, 'applied')).toBe('did: 10 Maggi in — a delivery arrived');
    expect(describeAction(movement, 'proposed')).toBe('proposed: 10 Maggi in — a delivery arrived — not moved');
    expect(describeAction(expense, 'applied')).toBe('did: recorded ₹200.00 under Tea and snacks');
    expect(describeAction(expense, 'proposed')).toBe('proposed: ₹200.00 under Tea and snacks — not recorded');
  });

  it('treats a format-1 proposal with no kind as a bill', () => {
    const old = { ...bill, kind: undefined };
    expect(describeAction(old, 'proposed')).toContain('for the bill');
  });
});

describe('undoPlan — only where the backend has one', () => {
  it('lets held bill lines go from this browser', () => {
    expect(undoPlan(bill, null)).toEqual(expect.objectContaining({ kind: 'unhold', proposal_id: 'prop_a' }));
  });

  it('reverses a movement with a correcting movement the other way', () => {
    const plan = undoPlan(movement, { at: 1, movement_id: 'mv_1' });
    expect(plan).toEqual(expect.objectContaining({ kind: 'post', path: '/stock/maggi/out' }));
    if (plan?.kind === 'post') {
      expect(plan.body).toEqual({ units: 10, reason: 'correction', note: 'undo of mv_1 from Salaahkaar' });
    }
  });

  it('voids an expense with a reason', () => {
    const plan = undoPlan(expense, { at: 1, expense_id: 'exp_9' });
    expect(plan).toEqual(expect.objectContaining({ kind: 'post', path: '/expenses/exp_9/void' }));
    if (plan?.kind === 'post') expect(typeof plan.body.reason).toBe('string');
  });

  it('has nothing to undo with before the server answered, or without its id', () => {
    expect(undoPlan(movement, null)).toBeNull();
    expect(undoPlan(movement, { at: 1 })).toBeNull();
    expect(undoPlan(expense, { at: 1, movement_id: 'mv_1' })).toBeNull();
  });
});

describe('readApplied — what the server said', () => {
  it('keeps the movement id and the shelf figure', () => {
    expect(readApplied({ ok: true, movement_id: 'mv_2', on_hand_units: 14 }, 5))
      .toEqual({ at: 5, movement_id: 'mv_2', on_hand_units: 14 });
  });
  it('keeps the expense id from under `expense`', () => {
    expect(readApplied({ ok: true, expense: { expense_id: 'exp_1' } }, 5)).toEqual({ at: 5, expense_id: 'exp_1' });
  });
  it('abstains on a body with neither', () => {
    expect(readApplied({ ok: true }, 5)).toEqual({ at: 5 });
    expect(readApplied(null, 5)).toEqual({ at: 5 });
  });
});

/* ------------------------------------------------------------- readers -- */

describe('readTopSellers', () => {
  it('reads /manage/today’s list', () => {
    const rows = readTopSellers({ top_sellers: [
      { sku_id: 'a', name: 'A', units: 2, revenue_paise: 200 },
      { sku_id: 'b', name: 'B', units: 5, revenue_paise: 500 },
    ] });
    expect(rows.map((r) => r.sku_id)).toEqual(['b', 'a']);
    expect(rows[0]?.revenue_paise).toBe(500);
  });
  it('reads day_close_preview’s derived map', () => {
    const rows = readTopSellers({ derived: { units_by_sku: { parle_g: 16, maggi: 3 }, line_revenue_by_sku: { parle_g: 16000 } } });
    expect(rows.map((r) => r.sku_id)).toEqual(['parle_g', 'maggi']);
    expect(rows[0]).toEqual({ sku_id: 'parle_g', name: 'parle g', units: 16, revenue_paise: 16000 });
    expect(rows[1]?.revenue_paise).toBeNull();
  });
  it('abstains on nothing', () => {
    expect(readTopSellers({})).toEqual([]);
    expect(readTopSellers(null)).toEqual([]);
    expect(readTopSellers({ top_sellers: [{ name: 'no sku', units: 2 }] })).toEqual([]);
  });
  it('caps the rows', () => {
    const many = Object.fromEntries(Array.from({ length: 20 }, (_, i) => [`s${i}`, i]));
    expect(readTopSellers({ units_by_sku: many }, 4)).toHaveLength(4);
  });
});

describe('readLowRows', () => {
  it('reads the assistant’s rows and stock.py’s rows alike', () => {
    const a = readLowRows({ low: [{ sku_id: 'x', name: 'X', remaining_units: 2, billed_since_count: 1 }], uncounted: 3 });
    expect(a?.rows[0]).toEqual({ sku_id: 'x', name: 'X', left: 2, billed_since_count: 1 });
    expect(a?.uncounted).toBe(3);
    const b = readLowRows({ low: [{ sku_id: 'y', name: 'Y', on_hand_units: 0, remaining_after_billing: 0 }], unknown: ['p', 'q'], skus_without_a_level: ['r'] });
    expect(b?.rows[0]).toEqual({ sku_id: 'y', name: 'Y', left: 0, billed_since_count: null });
    expect(b?.uncounted).toBe(2);
    expect(b?.without_level).toBe(1);
  });
  it('abstains on a figure it was not given, and on the wrong shape', () => {
    expect(readLowRows({ low: [{ sku_id: 'z', name: 'Z' }] })?.rows[0]?.left).toBeNull();
    expect(readLowRows({ bills: 3 })).toBeNull();
    expect(readLowRows(null)).toBeNull();
  });
});

/* --------------------------------------------------------------- voice -- */

const v = (lang: string, name: string, def = false) =>
  ({ lang, name, default: def, localService: true, voiceURI: name }) as SpeechSynthesisVoice;

describe('pickVoice', () => {
  it('prefers the exact tag, then the language, then Indian English', () => {
    const voices = [v('en-US', 'Sam'), v('en-IN', 'Veena'), v('hi-IN', 'Lekha'), v('hi_IN', 'Old')];
    expect(pickVoice(voices, 'hi-IN').voice?.name).toBe('Lekha');
    expect(pickVoice(voices, 'bn-IN').voice?.name).toBe('Veena');
    expect(pickVoice(voices, 'bn-IN').matched).toBe(false);
    expect(pickVoice([v('hi_IN', 'Old')], 'hi-IN').voice?.name).toBe('Old');
  });
  it('says so when there is nothing', () => {
    expect(pickVoice([], 'hi-IN').voice).toBeNull();
    expect(pickVoice([], 'hi-IN').note).toMatch(/no voices/);
  });
});

/* ---------------------------------------------------------- the rest -- */

describe('the small helpers', () => {
  it('lists the three languages the till voices', () => {
    expect(LANGS.map((l) => l.tag)).toEqual(['hi-IN', 'en-IN', 'bn-IN']);
    expect(isLangTag('bn-IN')).toBe(true);
    expect(isLangTag('fr-FR')).toBe(false);
  });
  it('drops the Devanagari gloss from a name', () => {
    expect(shortName('Maggi 2-Minute Noodles 70 g (मैगी नूडल्स)')).toBe('Maggi 2-Minute Noodles 70 g');
    expect(shortName('Parle-G biscuit 100g')).toBe('Parle-G biscuit 100g');
  });
  it('offers a bill chip only when it can name a product, and routes each chip as the router would', () => {
    const none = suggestions([]);
    expect(none.some((s) => s.route === 'action')).toBe(false);
    const some = suggestions(['Maggi']);
    const chip = some.find((s) => s.route === 'action');
    expect(chip?.say).toBe('2 Maggi bill me daal do');
    for (const s of some) expect(routeTurn(s.say).route, s.say).toBe(s.route);
  });
  it('names a product by a word only it carries', () => {
    const skus = [
      { sku_id: 'ponds', name: 'PONDS talc' },
      { sku_id: 'ponds_cream', name: 'Ponds cream' },
      { sku_id: 'maggi_70g', name: 'Maggi 2-Minute Noodles 70 g' },
    ];
    // "ponds" names two products, so it is skipped for the next unique word.
    expect(sayableProducts(skus, 3)).toEqual(['talc', 'cream', 'Maggi']);
  });
});
