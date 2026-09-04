import { createElement, useCallback, useSyncExternalStore, type ReactNode } from 'react';
import { en, type StringKey, type Table } from './strings/en';

/* ===========================================================================
   THE COUNTER IN THREE LANGUAGES
   ---------------------------------------------------------------------------
   A shopkeeper in Barrackpore and one in Bhopal are running the same till. The
   product's claim is that it says what it can and cannot see; a claim made in
   a language you do not read is not a claim at all.

   FOUR DECISIONS, AND WHY:

   1. ENGLISH IS THE SOURCE OF TRUTH, NOT A PEER. `strings/en.ts` holds every
      key; Hindi and Bengali are partial overlays on it. A key nobody has
      translated yet renders the English sentence — never an empty button, and
      never the raw key. The one thing worse than an English word on a Hindi
      till is a blank space where the total should be.

   2. THE LANGUAGE IS THE READER'S, NOT THE SERVER'S. It lives in localStorage
      under one key, and the browser's own `navigator.languages` is the default.
      Nothing about the choice is sent anywhere: it is a preference about how a
      screen is drawn, not a fact about the shop.

   3. TRANSLATION IS PURE. `translate()` is a function of (language, key, vars)
      and touches no globals, which is why it can be tested without a DOM. The
      React hook is a thin wrapper over it plus a subscription.

   4. NOTHING HERE FORMATS MONEY. `lib/money.ts` owns rupees, in integer paise,
      and a translated string receives the already-formatted amount as a
      substitution. A number that went through a translation layer is a number
      that could have been rounded on the way.

   TWO SPELLINGS OF THE SAME ENTRY. `t()` returns plain text with the emphasis
   markers removed — right for a `title=`, an `aria-label` or an `alt`. `rich()`
   returns React nodes with `<b>` intact — right for a sentence on the page. One
   table entry serves both, so nobody has to keep two copies in step.
   =========================================================================== */

export type { StringKey } from './strings/en';

export type Lang = 'en' | 'hi' | 'bn';

export interface LangInfo {
  id: Lang;
  /** The language's name in its own script. Never translated — a picker that
      says "Hindi" to somebody who cannot read Latin is a picker they cannot use. */
  endonym: string;
  /** Two or three characters for a crowded bar. */
  short: string;
  /** For an English-language description of the choice, e.g. a title=. */
  english: string;
}

/** Order is deliberate: English first because it is the fallback, then by the
    number of people at a counter who would choose it. */
export const LANGS: readonly LangInfo[] = [
  { id: 'en', endonym: 'English', short: 'EN', english: 'English' },
  { id: 'hi', endonym: 'हिन्दी', short: 'हिं', english: 'Hindi' },
  { id: 'bn', endonym: 'বাংলা', short: 'বাং', english: 'Bengali' },
];

/**
 * ENGLISH IS BUILT IN; THE OVERLAYS ARRIVE WHEN SOMEBODY WANTS ONE.
 *
 * All three tables in the entry chunk cost every shopkeeper 73 kB of source to
 * read a counter in one language. English has to be here — it is the fallback
 * for every missing key, so it can never be the thing we are waiting on — but
 * Hindi and Bengali are fetched for the language actually in force.
 *
 * The gap is already a case this module handles. Decision 1 above says a key
 * with no translation renders the English sentence, and a table that has not
 * landed yet is that same case for every key at once: the counter opens in
 * English and turns Hindi a moment later, rather than showing a blank screen
 * or a raw key. `ensureLang` is started the instant the language is known —
 * before the first paint, in practice — so the moment is usually no moment.
 */
const TABLES: Partial<Record<Lang, Table>> = { en };

const LOADERS: Record<Exclude<Lang, 'en'>, () => Promise<Table>> = {
  hi: () => import('./strings/hi').then((m) => m.hi),
  bn: () => import('./strings/bn').then((m) => m.bn),
};

/** One in-flight fetch per language: every screen calls `useT`, and a table
    per caller would be one request each for the same file. */
const inFlight = new Map<Lang, Promise<void>>();

/** Whether this language can be rendered right now, rather than fallen back. */
export function langReady(lang: Lang): boolean {
  return TABLES[lang] !== undefined;
}

/**
 * Fetch a language's table if it is not here yet. Resolves when the counter
 * can render in it — immediately for English, and for one already loaded.
 *
 * A FAILED FETCH IS NOT AN ERROR ANYBODY CAN ACT ON. An offline till keeps
 * reading English, which is what it was already showing; throwing here would
 * put a network fault on screen in place of a working counter. The failure is
 * not cached, so the next call tries again.
 */
export function ensureLang(lang: Lang): Promise<void> {
  if (TABLES[lang]) return Promise.resolve();
  const loader = LOADERS[lang as Exclude<Lang, 'en'>];
  if (!loader) return Promise.resolve();
  let p = inFlight.get(lang);
  if (!p) {
    p = loader().then((table) => { TABLES[lang] = table; bump(); })
      .catch(() => { inFlight.delete(lang); });
    inFlight.set(lang, p);
  }
  return p;
}

export const FALLBACK_LANG: Lang = 'en';
export const STORAGE_KEY = 'gawaah.lang.v1';

/** A value the page substitutes into a string. Strings and numbers only: an
    object here would stringify to "[object Object]" in front of a customer. */
export type Vars = Readonly<Record<string, string | number>>;

export function isLang(v: unknown): v is Lang {
  return v === 'en' || v === 'hi' || v === 'bn';
}

/* ------------------------------------------------------------ detection -- */

/**
 * A BCP-47 tag to one of the three, or null.
 *
 * `hi-IN` and `hi` are Hindi; `bn-BD` and `bn-IN` are Bengali; `en-GB` is
 * English. A LATIN script subtag is refused on purpose: `hi-Latn` means a
 * person who has asked for Hindi written in Roman letters, and these tables are
 * in Devanagari — giving them a script they did not ask for is worse than
 * giving them the English they can already read.
 */
export function normaliseLang(tag: string): Lang | null {
  const parts = String(tag).toLowerCase().split('-');
  const primary = parts[0] ?? '';
  if (parts.includes('latn')) return null;
  return isLang(primary) ? primary : null;
}

/** The first tag in the browser's list that this counter can actually speak. */
export function detectLang(tags: readonly string[]): Lang {
  for (const tag of tags) {
    const l = normaliseLang(tag);
    if (l) return l;
  }
  return FALLBACK_LANG;
}

/** What the browser says it prefers, most-wanted first. Empty off a browser. */
export function browserLangs(): readonly string[] {
  if (typeof navigator === 'undefined') return [];
  const n = navigator as Navigator & { languages?: readonly string[] };
  if (Array.isArray(n.languages) && n.languages.length) return n.languages;
  return n.language ? [n.language] : [];
}

/* -------------------------------------------------------------- storage -- */

/**
 * Every access is wrapped: a phone in private mode, or a browser with site data
 * blocked, throws on the very first read. The counter must still open — in the
 * browser's own language — rather than showing a white screen.
 */
function readStored(): Lang | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return isLang(raw) ? raw : null;
  } catch {
    return null;
  }
}

function writeStored(lang: Lang): void {
  try {
    localStorage.setItem(STORAGE_KEY, lang);
  } catch {
    // Storage refused. The choice still holds for this page view; it simply
    // will not survive a reload, and that is not worth an error on a till.
  }
}

/* ---------------------------------------------------------- the current -- */

let current: Lang | null = null;
const listeners = new Set<() => void>();

/**
 * WHAT REACT WATCHES. Not the language id — that is the obvious choice and it
 * is wrong here. `useSyncExternalStore` re-renders only when the snapshot
 * CHANGES, and the event that matters most now is a table arriving for a
 * language that was already selected: same id, different sentences, and every
 * screen would keep showing the English fallback forever. So the snapshot
 * carries a counter that both events move.
 */
let seq = 0;
let snapshot: string | null = null;

function bump(): void {
  seq += 1;
  snapshot = `${getLang()}:${seq}`;
  for (const fn of listeners) fn();
}

function getSnapshot(): string {
  if (snapshot === null) snapshot = `${getLang()}:${seq}`;
  return snapshot;
}

/** The language in force: a stored choice, else the browser's, else English. */
export function getLang(): Lang {
  if (current === null) {
    current = readStored() ?? detectLang(browserLangs());
    // Start the fetch the moment the choice is known, which is during the first
    // render rather than after it. English resolves without a request.
    void ensureLang(current);
  }
  return current;
}

/**
 * Put the language on <html> so CSS can pick the right script's font stack and
 * a screen reader announces the page in the language it is actually written in.
 * `:lang()` in styles/i18n.css hangs off exactly this.
 */
export function applyDocumentLang(lang: Lang = getLang()): void {
  if (typeof document === 'undefined') return;
  document.documentElement.lang = lang;
}

export function setLang(lang: Lang): void {
  if (!isLang(lang) || lang === getLang()) return;
  current = lang;
  writeStored(lang);
  applyDocumentLang(lang);
  // The picker responds NOW, in English if the table is still coming, and
  // again when it lands. A picker that does nothing for 200ms reads as broken.
  void ensureLang(lang);
  bump();
}

/**
 * A counter often has a second tab open — the customer display, the storefront
 * as the customer sees it. They are the same shop, and one of them silently
 * staying in English is a bug a shopkeeper would have to discover by walking
 * round the counter. So the choice follows the `storage` event across tabs.
 *
 * Bound ONCE for the life of the page rather than per subscriber: every screen
 * calls `useT`, and a listener each would mean one cross-tab change waking the
 * same set of components once per mounted screen.
 */
let storageBound = false;

function bindStorage(): void {
  if (storageBound || typeof window === 'undefined') return;
  storageBound = true;
  window.addEventListener('storage', (e: StorageEvent) => {
    if (e.key !== STORAGE_KEY) return;
    const next = isLang(e.newValue) ? e.newValue : null;
    if (!next || next === current) return;
    current = next;
    applyDocumentLang(next);
    void ensureLang(next);
    bump();
  });
}

export function subscribe(onChange: () => void): () => void {
  bindStorage();
  listeners.add(onChange);
  return () => { listeners.delete(onChange); };
}

/** Tests, and nothing else: forget the memoised choice and the stored one. */
export function resetLangForTests(): void {
  current = null;
  snapshot = null;
  try { localStorage.removeItem(STORAGE_KEY); } catch { /* never stored */ }
}

/* ---------------------------------------------------------- translation -- */

const VAR_RE = /\{(\w+)\}/g;
const BOLD_RE = /<\/?b>/g;
const BR_RE = /<br\s*\/?>/g;
/** `<n>0.60</n>` — a FIGURE, not merely bold. See `rich()`. */
const NUM_RE = /<\/?n>/g;
/* `<b>…</b>` and `<br>`, kept as capture groups so `split` returns them. */
const MARKUP_SPLIT_RE = /(<b>[\s\S]*?<\/b>|<n>[\s\S]*?<\/n>|<br\s*\/?>)/g;

/**
 * A missing variable leaves its `{brace}` in place rather than substituting an
 * empty string. A sentence that reads "The counter sees , the bill says ₹99" is
 * a sentence that hides a bug; one that reads "{seen}" reports it.
 */
function fill(text: string, vars?: Vars): string {
  if (!vars) return text;
  return text.replace(VAR_RE, (whole, name: string) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : whole);
}

/** The raw entry, tags and braces intact: the fallback chain in one place. */
export function raw(lang: Lang, key: StringKey): string {
  return TABLES[lang]?.[key] ?? en[key] ?? key;
}

/** Plain text. The markers are removed — a `<br>` becomes the space it stands
    in for, so a two-sentence string still reads as a sentence in a `title=`. */
export function translate(lang: Lang, key: StringKey, vars?: Vars): string {
  return fill(raw(lang, key), vars)
    .replace(BR_RE, ' ').replace(BOLD_RE, '').replace(NUM_RE, '');
}

/**
 * The same entry with `<b>`, `<n>` and `<br>` as real elements, for a page.
 *
 * `<n>` IS A FIGURE, AND IT IS NOT THE SAME AS `<b>`. A number the counter
 * reports — a cosine gate, a threshold — is set in the tabular figures the rest
 * of this design uses for machine-read values, so a column of them lines up and
 * a changed digit does not shift the ones beside it. It got its own marker
 * because the alternative was translating a sentence and quietly dropping the
 * `className` its English original carried, which is how a design decision
 * disappears without anyone deciding to remove it.
 */
export function rich(lang: Lang, key: StringKey, vars?: Vars): ReactNode[] {
  const filled = fill(raw(lang, key), vars);
  return filled.split(MARKUP_SPLIT_RE).filter((part) => part !== '').map((part, i) => {
    if (part.startsWith('<br')) return createElement('br', { key: i });
    if (part.startsWith('<b>')) return createElement('b', { key: i }, part.slice(3, -4));
    if (part.startsWith('<n>')) {
      return createElement('b', { key: i, className: 'tnum' }, part.slice(3, -4));
    }
    return part;
  });
}

/** The keys that come in a `.one` / `.other` pair. */
type PluralBase = { [K in StringKey]: K extends `${infer B}.one` ? B : never }[StringKey];

/**
 * One or many.
 *
 * Two forms is the correct rule for all three languages here — English, Hindi
 * and Bengali each separate exactly one from everything else — so there is no
 * plural-rules table and no library. `{n}` is supplied automatically; a caller
 * may still override it (a count shown as "one" in words, say).
 */
export function plural(lang: Lang, base: PluralBase, n: number, vars?: Vars): string {
  const key = `${base}.${n === 1 ? 'one' : 'other'}` as StringKey;
  return translate(lang, key, { n, ...vars });
}

export function pluralRich(lang: Lang, base: PluralBase, n: number, vars?: Vars): ReactNode[] {
  const key = `${base}.${n === 1 ? 'one' : 'other'}` as StringKey;
  return rich(lang, key, { n, ...vars });
}

/* --------------------------------------------------------------- react -- */

export interface Translator {
  lang: Lang;
  setLang: (l: Lang) => void;
  /** Plain text — a label, a button, a `title=`, an `aria-label`. */
  t: (key: StringKey, vars?: Vars) => string;
  /** With `<b>` rendered — a sentence in the page. */
  tx: (key: StringKey, vars?: Vars) => ReactNode[];
  /** One or many, plain. */
  tn: (base: PluralBase, n: number, vars?: Vars) => string;
  /** One or many, with `<b>` rendered. */
  tnx: (base: PluralBase, n: number, vars?: Vars) => ReactNode[];
}

/**
 * The hook every screen uses.
 *
 * `useSyncExternalStore` rather than a context provider, so a screen deep in a
 * lazy-loaded chunk needs no provider above it and cannot render a stale
 * language for one frame after the picker changes.
 */
export function useT(): Translator {
  // Subscribed to the snapshot, read the language: see the note on `seq`.
  useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const lang = getLang();
  return {
    lang,
    setLang,
    t: useCallback((key: StringKey, vars?: Vars) => translate(lang, key, vars), [lang]),
    tx: useCallback((key: StringKey, vars?: Vars) => rich(lang, key, vars), [lang]),
    tn: useCallback((b: PluralBase, n: number, vars?: Vars) => plural(lang, b, n, vars), [lang]),
    tnx: useCallback((b: PluralBase, n: number, vars?: Vars) => pluralRich(lang, b, n, vars), [lang]),
  };
}
