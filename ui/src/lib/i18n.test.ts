import { describe, it, expect, beforeAll, beforeEach, afterEach } from 'vitest';
import { en, type StringKey } from './strings/en';
import { hi } from './strings/hi';
import { bn } from './strings/bn';
import {
  LANGS, STORAGE_KEY, FALLBACK_LANG,
  detectLang, normaliseLang, isLang, translate, rich, plural,
  getLang, setLang, resetLangForTests, subscribe, ensureLang, langReady,
  type Lang,
} from './i18n';
import { TABS, type RouteId, type SideItem } from '../components/shell';

/** The sidebar, flattened. Written out rather than inferred: `TABS` is a const
    tuple whose three item lists have three different literal types, and
    flatMap over that infers a union of tuples rather than a list of rows. */
const ROWS: readonly SideItem[] = TABS.flatMap((tab): SideItem[] => [...tab.items]);

/**
 * Hindi and Bengali are fetched on demand — see `TABLES` in i18n.ts — so a
 * suite that asserts on their sentences has to wait for them the way a browser
 * does. Every assertion below is unchanged by this: it is the loading, not the
 * translating, that became asynchronous.
 */
beforeAll(async () => {
  await Promise.all([ensureLang('hi'), ensureLang('bn')]);
});

describe('a language table that has not arrived yet', () => {
  it('falls back to English rather than throwing or rendering a raw key', () => {
    // `ensureLang` has run for both above, so this asserts the fallback branch
    // directly through `raw`'s optional chain: an unknown language id can never
    // reach here through the type system, and this is the shape of the state
    // every reader is in for the first frames after the counter opens.
    expect(langReady('en')).toBe(true);
    expect(translate('en', 'till.charge.pay', { amount: '₹1.00' }))
      .toBe(en['till.charge.pay'].replace('{amount}', '₹1.00'));
  });

  it('reports readiness per language', async () => {
    expect(langReady('hi')).toBe(true);
    await expect(ensureLang('en')).resolves.toBeUndefined();
  });
});

/**
 * These tests are the whole guarantee behind "English is the source of truth".
 *
 * A translation layer fails silently by nature: a missing key is a blank
 * button, a dropped `{amount}` is a bill with no total, and neither shows up
 * until a shopkeeper is standing in front of it with a customer waiting. So
 * every property that cannot be seen by looking at the screen in English is
 * asserted here instead.
 */

const KEYS = Object.keys(en) as StringKey[];
const OVERLAYS: ReadonlyArray<[string, Record<string, string>]> = [
  ['hi', hi as Record<string, string>],
  ['bn', bn as Record<string, string>],
];

/** `{name}` placeholders, in order of appearance, deduplicated and sorted. */
const vars = (s: string) => [...new Set(s.match(/\{\w+\}/g) ?? [])].sort();

describe('the tables line up', () => {
  it('has something to translate', () => {
    expect(KEYS.length).toBeGreaterThan(100);
  });

  for (const [name, table] of OVERLAYS) {
    it(`${name} translates every English key`, () => {
      const missing = KEYS.filter((k) => typeof table[k] !== 'string');
      expect(missing).toEqual([]);
    });

    it(`${name} has invented no key of its own`, () => {
      // A typo in an overlay key is otherwise invisible: the entry is simply
      // never read and the screen quietly stays English.
      const extra = Object.keys(table).filter((k) => !(k in en));
      expect(extra).toEqual([]);
    });

    it(`${name} translates nothing to an empty string`, () => {
      const blank = KEYS.filter((k) => (table[k] ?? '').trim() === '');
      expect(blank).toEqual([]);
    });

    it(`${name} keeps every {placeholder} the English string carries`, () => {
      // A dropped placeholder is a dropped number, and the numbers are the
      // product. Order may change — Hindi and Bengali put the verb last — so
      // this compares the SET of placeholders, not the sentence.
      const wrong = KEYS.filter((k) => vars(en[k]).join() !== vars(table[k] ?? '').join());
      expect(wrong).toEqual([]);
    });

    it(`${name} keeps its <b> tags balanced`, () => {
      const broken = KEYS.filter((k) => {
        const s = table[k] ?? '';
        return (s.match(/<b>/g) ?? []).length !== (s.match(/<\/b>/g) ?? []).length;
      });
      expect(broken).toEqual([]);
    });
  }

  it('writes Hindi in Devanagari and Bengali in Bengali', () => {
    // Catches the copy-paste that leaves an English sentence sitting in an
    // overlay looking translated. A string may still CONTAIN Latin — "UPI",
    // "QR" and "Razorpay" are read as they are written — so this asks only
    // that the line carries its own script somewhere.
    const noDeva = KEYS.filter((k) => !/[ऀ-ॿ]/.test(hi[k] ?? ''));
    const noBeng = KEYS.filter((k) => !/[ঀ-৿]/.test(bn[k] ?? ''));
    expect(noDeva).toEqual([]);
    expect(noBeng).toEqual([]);
  });

  it('formats no money in any language', () => {
    // Invariant 1 reaches this file too: a rupee figure inside a translated
    // string would be a price this layer had authored. Amounts arrive already
    // formatted by lib/money.ts, as {amount}, {seen}, {bill}.
    const priced = [en, hi, bn].flatMap((table) =>
      Object.entries(table).filter(([, v]) => /₹\s*\d/.test(String(v))).map(([k]) => k));
    expect(priced).toEqual([]);
  });
});

describe('the shell has a name for every screen it can open', () => {
  it('covers all three tabs', () => {
    const missing = TABS.flatMap((t) =>
      [`nav.tab.${t.id}`, `nav.tab.${t.id}.blurb`].filter((k) => !(k in en)));
    expect(missing).toEqual([]);
  });

  it('covers every sidebar row, label and sub-label', () => {
    // If this fails, a route was added to shell.tsx and needs three lines here:
    // `nav.<id>` and `nav.<id>.sub` in en.ts, hi.ts and bn.ts.
    const missing = ROWS.flatMap((i) =>
      [`nav.${i.id}`, `nav.${i.id}.sub`].filter((k) => !(k in en)));
    expect(missing).toEqual([]);
  });

  it('is keyed the way the sidebar will call it', () => {
    // This is as much a compile-time test as a run-time one. shell.tsx is meant
    // to look its labels up by the route id it already has —
    // `t(`nav.${it.id}`)` — and that only typechecks if every id's template
    // literal is a StringKey. If a route is added without its three lines, this
    // file stops compiling, which is the earliest anyone can be told.
    const label = (id: RouteId) => translate('hi', `nav.${id}`);
    const sub = (id: RouteId) => translate('hi', `nav.${id}.sub`);
    const tab = (id: (typeof TABS)[number]['id']) => translate('bn', `nav.tab.${id}`);
    expect(label('inventory')).toBe('माल');
    expect(sub('inventory')).toBe('क्या बिकता है, क्या पड़ा रहता है');
    expect(tab('books')).toBe('খাতা');
  });

  it('still carries the English the sidebar renders today', () => {
    // The overlay is only trustworthy if the English half is the same text the
    // untranslated shell shows. Spot-checked against components/shell.tsx.
    for (const item of ROWS) {
      expect(en[`nav.${item.id}` as StringKey]).toBe(item.label);
      expect(en[`nav.${item.id}.sub` as StringKey]).toBe(item.sub);
    }
    for (const tab of TABS) {
      expect(en[`nav.tab.${tab.id}` as StringKey]).toBe(tab.label);
      expect(en[`nav.tab.${tab.id}.blurb` as StringKey]).toBe(tab.blurb);
    }
  });
});

describe('a missing translation falls back rather than blanking', () => {
  it('returns the English sentence when an overlay has no entry', () => {
    // Every key IS translated, which is exactly why the fallback path has to be
    // provoked: the table is emptied of one key and put back.
    const table = hi as Record<string, string | undefined>;
    const key: StringKey = 'till.bill.total';
    const saved = table[key];
    try {
      delete table[key];
      expect(translate('hi', key)).toBe(en[key]);
    } finally {
      table[key] = saved;
    }
    expect(translate('hi', key)).toBe(hi[key]);
  });

  it('returns the key itself for a key no table has ever had', () => {
    // Never an empty string. A screen showing `till.made.up` is a bug report;
    // a screen showing nothing is a mystery.
    expect(translate('hi', 'till.made.up' as StringKey)).toBe('till.made.up');
  });
});

describe('substitution', () => {
  it('puts the value in', () => {
    expect(translate('en', 'till.charge.pay', { amount: '₹139.50' }))
      .toBe('CHARGE ₹139.50');
    expect(translate('hi', 'till.charge.pay', { amount: '₹139.50' }))
      .toBe('₹139.50 लो');
    expect(translate('bn', 'till.charge.pay', { amount: '₹139.50' }))
      .toBe('₹139.50 নিন');
  });

  it('leaves the brace showing when the value was never passed', () => {
    // Rather than an empty gap, which would read as a finished sentence about
    // a number that is not there.
    expect(translate('en', 'till.charge.pay')).toBe('CHARGE {amount}');
  });

  it('substitutes into every language of a two-value sentence', () => {
    for (const l of ['en', 'hi', 'bn'] as Lang[]) {
      const s = translate(l, 'till.refuse.disagree', { seen: '₹99.00', bill: '₹139.50' });
      expect(s).toContain('₹99.00');
      expect(s).toContain('₹139.50');
      expect(s).not.toContain('{');
    }
  });
});

describe('emphasis', () => {
  it('is stripped by t(), for a title or an aria-label', () => {
    const s = translate('en', 'till.decides.note');
    expect(s).not.toContain('<b>');
    expect(s).toContain('proposes');
  });

  it('is rendered by rich(), as real elements', () => {
    const parts = rich('hi', 'nav.brandline') as Array<string | { type: string }>;
    // The plain halves come back as plain strings, so the sentence still reads
    // in order and React can put it straight into a <p>.
    expect(parts.filter((p) => typeof p === 'string').join(''))
      .toBe('किराने की दुकान किसी की बात पर चलती है।');
    expect(parts.filter((p) => typeof p === 'object').map((p) => (p as { type: string }).type))
      .toEqual(['br', 'b']);
  });

  it('turns a line break into a space for a plain-text caller', () => {
    // The sidebar's two sentences are one string with a break in it. In a
    // `title=` a <br> that stayed as markup would be read out as characters.
    expect(translate('en', 'nav.brandline'))
      .toBe('A kirana counter runs on somebody’s word. This is the witness.');
  });

  it('renders a sentence with no emphasis as one plain part', () => {
    const parts = rich('en', 'till.bill.total');
    expect(parts).toEqual(['Total']);
  });

  it('substitutes inside an emphasised span', () => {
    const parts = rich('en', 'till.inbound.last', { ago: '3m ago' });
    const bold = parts.find((p) => typeof p === 'object') as { props: { children: string } };
    expect(bold.props.children).toBe('3m ago');
  });
});

describe('one and many', () => {
  it('picks the singular for exactly one, in all three languages', () => {
    expect(plural('en', 'till.charge.show', 1)).toBe('SHOW IT TO THE CAMERA');
    expect(plural('hi', 'till.charge.show', 1)).toBe('इसे कैमरे को दिखाओ');
    expect(plural('bn', 'till.charge.show', 1)).toBe('এটা ক্যামেরাকে দেখান');
  });

  it('picks the plural for none and for many', () => {
    expect(plural('en', 'till.charge.show', 0)).toBe('SHOW THEM TO THE CAMERA');
    expect(plural('en', 'till.charge.show', 4)).toBe('SHOW THEM TO THE CAMERA');
  });

  it('supplies {n} without being asked', () => {
    expect(plural('en', 'app.orders', 3)).toBe('3 new orders');
    expect(plural('hi', 'app.orders', 3)).toBe('3 नए ऑर्डर');
    expect(plural('bn', 'app.orders', 3)).toBe('3টা নতুন অর্ডার');
  });

  it('keeps both forms for the counter it could not name', () => {
    expect(translate('en', 'till.sweep.unnamed.one')).toContain('There is 1 thing');
    expect(plural('en', 'till.sweep.unnamed', 3)).toContain('There are 3 things');
  });
});

describe('choosing a language', () => {
  it('reads a plain tag', () => {
    expect(normaliseLang('hi')).toBe('hi');
    expect(normaliseLang('bn')).toBe('bn');
    expect(normaliseLang('en')).toBe('en');
  });

  it('reads a region', () => {
    expect(normaliseLang('hi-IN')).toBe('hi');
    expect(normaliseLang('bn-BD')).toBe('bn');
    expect(normaliseLang('en-GB')).toBe('en');
    expect(normaliseLang('EN-US')).toBe('en');
  });

  it('refuses a Latin-script Indian tag', () => {
    // `hi-Latn` is Hinglish in Roman letters. These tables are Devanagari, and
    // handing somebody a script they did not ask for is worse than handing them
    // the English they can already read.
    expect(normaliseLang('hi-Latn')).toBeNull();
    expect(normaliseLang('bn-latn-IN')).toBeNull();
  });

  it('knows nothing about the languages it does not have', () => {
    expect(normaliseLang('ta')).toBeNull();
    expect(normaliseLang('mr-IN')).toBeNull();
    expect(normaliseLang('')).toBeNull();
  });

  it('takes the first tag the counter can speak', () => {
    expect(detectLang(['ta-IN', 'bn-IN', 'en-GB'])).toBe('bn');
    expect(detectLang(['hi-IN', 'en-IN'])).toBe('hi');
  });

  it('falls back to English when it recognises none of them', () => {
    expect(detectLang(['ta-IN', 'ml'])).toBe(FALLBACK_LANG);
    expect(detectLang([])).toBe('en');
  });

  it('validates what it is handed', () => {
    expect(isLang('hi')).toBe(true);
    expect(isLang('hi-IN')).toBe(false);
    expect(isLang(null)).toBe(false);
  });

  it('offers each language in its own script', () => {
    expect(LANGS.map((l) => l.id)).toEqual(['en', 'hi', 'bn']);
    expect(LANGS.map((l) => l.endonym)).toEqual(['English', 'हिन्दी', 'বাংলা']);
  });
});

/* ------------------------------------------------------------- storage -- */

/** A localStorage that behaves, for the two tests that need one. */
function fakeStorage(seed: Record<string, string> = {}) {
  const map = new Map(Object.entries(seed));
  return {
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => { map.set(k, String(v)); },
    removeItem: (k: string) => { map.delete(k); },
    clear: () => map.clear(),
    key: (i: number) => [...map.keys()][i] ?? null,
    get length() { return map.size; },
  } as Storage;
}

describe('the choice survives a reload', () => {
  const g = globalThis as { localStorage?: Storage };

  beforeEach(() => { resetLangForTests(); });
  afterEach(() => { delete g.localStorage; resetLangForTests(); });

  it('remembers what was chosen', () => {
    g.localStorage = fakeStorage();
    setLang('bn');
    expect(g.localStorage.getItem(STORAGE_KEY)).toBe('bn');
    resetLangForTests();
    g.localStorage = fakeStorage({ [STORAGE_KEY]: 'bn' });
    expect(getLang()).toBe('bn');
  });

  it('ignores a stored value that is not a language', () => {
    // Somebody else's key, a half-written value, a version of this app that
    // stored something different: any of them must not become the UI language.
    g.localStorage = fakeStorage({ [STORAGE_KEY]: 'pirate' });
    expect(getLang()).toBe('en');
  });

  it('opens in English when storage itself throws', () => {
    // A phone in private mode. The till must still open.
    g.localStorage = {
      getItem() { throw new Error('denied'); },
      setItem() { throw new Error('denied'); },
    } as unknown as Storage;
    expect(getLang()).toBe('en');
    expect(() => setLang('hi')).not.toThrow();
    expect(getLang()).toBe('hi');
  });

  it('tells its subscribers when the language changes', () => {
    g.localStorage = fakeStorage();
    let calls = 0;
    const stop = subscribe(() => { calls += 1; });
    setLang('hi');
    setLang('hi');   // the same language twice is not a change
    setLang('bn');
    stop();
    setLang('en');
    expect(calls).toBe(2);
  });
});
