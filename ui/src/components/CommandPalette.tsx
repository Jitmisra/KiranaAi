import {
  useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore,
} from 'react';
import { createPortal } from 'react-dom';
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from 'react';
import * as sx from '../lib/searchapi';
import { TABS } from './shell';
import { Empty, Refusal, IcoX, IcoWarn } from './ui';
import '../styles/palette.css';

/**
 * KHOJ — one box over the whole counter.
 *
 * ⌘K anywhere (Ctrl-K where there is no ⌘), type, arrows, enter. It searches
 * products, orders, bills and the shop's derived groups through
 * `gawaah/search.py`, and it navigates to every screen this build has.
 *
 * FOUR THINGS THIS COMPONENT WILL NOT DO.
 *
 *  1. IT DOES NOT AUTHOR MONEY, OR EVEN RENDER IT. Every rupee on this screen
 *     is a string the server already rendered from integer paise and is passed
 *     through untouched. There is no division, no rounding and no currency
 *     formatting anywhere in this file — searching for "139.50" is a query, not
 *     an arithmetic.
 *  2. IT DOES NOT RE-RANK. The order of results is the server's, which is
 *     stable and documented. Grouping keeps that order: rows stay in the order
 *     they arrived within each group, and the groups themselves are ordered by
 *     where their first row landed, so the top hit is still the top row.
 *  3. IT DOES NOT INVENT A DESTINATION. Every navigation command is built from
 *     the shell's own `TABS`, so the palette can only offer a screen this build
 *     actually registered. A screen nobody has wired is not listed, because the
 *     hash for an unregistered route lands on the till and that would be a
 *     command that lies.
 *  4. IT DOES NOT GO BLANK. Loading is skeleton rows at the height of the rows
 *     that are coming; nothing found is a sentence naming what search does not
 *     look at; a server refusal keeps the server's own reason, verbatim — and
 *     even then the "Go to" commands are still listed underneath it, because
 *     they need no server at all.
 *
 * COLOUR. Blue for the machine's own marks — the selected row, the highlighted
 * letters, the kind glyph. Amber appears only where an answer is SHORT: a
 * source that could not be read, or a refusal. Nothing here is ever green: on
 * this product green means a payment settled, and a search result is not one.
 *
 * MOUNTING. `<CommandPalette />` renders nothing until it is opened, so it can
 * be dropped anywhere inside the shell. It portals to `document.body`. Mounting
 * it twice is safe — the first one wins and the others render nothing — but the
 * intent is one, at the top of the app. `PaletteButton` is the touch way in and
 * belongs in the top bar; a shop phone has no ⌘K.
 */

/* ========================================================================== *
 * THE SWITCH
 * A module-level store rather than a prop, so anything anywhere can open the
 * palette without the shell threading a callback through every screen.
 * ========================================================================== */

let isOpen = false;
const subs = new Set<() => void>();
const emit = () => { for (const f of subs) f(); };
const subscribe = (f: () => void) => { subs.add(f); return () => { subs.delete(f); }; };
const readOpen = () => isOpen;

export function openPalette(): void {
  if (isOpen) return;
  isOpen = true;
  emit();
}

export function closePalette(): void {
  if (!isOpen) return;
  isOpen = false;
  emit();
}

export function togglePalette(): void {
  isOpen = !isOpen;
  emit();
}

/* Only ONE palette may be on screen. Two mounted copies would each draw their
   own scrim, and the one underneath would eat the clicks meant for the one on
   top. Same arbitration the Toaster uses: first to mount wins, and when it
   unmounts the next in the list takes over. */
let hostSeq = 0;
const hosts: number[] = [];
const hostSubs = new Set<() => void>();
const notifyHosts = () => { for (const f of hostSubs) f(); };

/* ========================================================================== *
 * MARKS
 * Inline SVG. The CSP allows no external host and this app ships no icon
 * package. `currentColor` throughout, so a glyph takes the colour of the row.
 * ========================================================================== */

const svg = (size: number) => ({
  width: size, height: size, viewBox: '0 0 16 16',
  fill: 'none', stroke: 'currentColor',
  strokeWidth: 1.5, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const,
  'aria-hidden': true, focusable: false,
});

export function IcoSearch({ size = 16 }: { size?: number }) {
  return <svg {...svg(size)}><circle cx="7.1" cy="7.1" r="4.6" /><path d="M10.5 10.5 13.8 13.8" /></svg>;
}
/** A packet on the counter. */
function IcoPacket() {
  return <svg {...svg(15)}><path d="M2.4 5.2 8 2.4l5.6 2.8v5.6L8 13.6l-5.6-2.8V5.2Z" /><path d="M2.4 5.2 8 8l5.6-2.8" /><path d="M8 8v5.6" /></svg>;
}
/** A delivery, going out. */
function IcoBag() {
  return <svg {...svg(15)}><path d="M3.4 5.4h9.2l-.8 8H4.2l-.8-8Z" /><path d="M5.9 7V4.6a2.1 2.1 0 0 1 4.2 0V7" /></svg>;
}
/** A bill off the chain. */
function IcoBill() {
  return <svg {...svg(15)}><path d="M4 2.4h8v11.2l-1.6-1.1-1.6 1.1-1.6-1.1-1.6 1.1L4 12.5V2.4Z" /><path d="M6.2 5.6h3.6" /><path d="M6.2 8.2h3.6" /></svg>;
}
/** A group the shop's own data implies. Not a taxonomy — see the server. */
function IcoGroup() {
  return <svg {...svg(15)}><path d="M2.6 5.6 8 2.9l5.4 2.7L8 8.3 2.6 5.6Z" /><path d="m2.6 9 5.4 2.7L13.4 9" /></svg>;
}
/** Go there. */
function IcoGo() {
  return <svg {...svg(15)}><path d="M3.2 8h9.2" /><path d="M8.8 4.4 12.4 8l-3.6 3.6" /></svg>;
}

type RowKind = sx.Kind | 'nav';

const MARK: Record<RowKind, () => ReactNode> = {
  product: () => <IcoPacket />,
  order: () => <IcoBag />,
  bill: () => <IcoBill />,
  category: () => <IcoGroup />,
  nav: () => <IcoGo />,
};

/* ========================================================================== *
 * THE ROWS
 * ========================================================================== */

interface Row {
  key: string;
  kind: RowKind;
  id: string;
  title: string;
  subtitle: string;
  /** The server's own account of why this matched. Shown, never rewritten. */
  why: string;
  /** The right-hand column: a count, a tab name, how long ago. Never money. */
  right: string;
  route: string;
}

interface Group {
  label: string;
  /** What this group is, where the label alone would not say. */
  note?: string;
  rows: Row[];
  /** Drawn at the group's head, on its right. The forget control lives here. */
  aside?: ReactNode;
}

const GROUP_LABEL: Record<sx.Kind, string> = {
  product: 'Products',
  order: 'Orders',
  bill: 'Bills and sessions',
  category: 'Groups',
};

/**
 * The order the RECENT buckets are laid out in, with nothing typed.
 *
 * It applies to that list alone. A list of search results is ordered by the
 * server and this array is not allowed anywhere near it — see the grouping
 * below.
 */
const KIND_ORDER: readonly sx.Kind[] = ['product', 'order', 'bill', 'category'];

function rowOfHit(hit: sx.Hit, withWhen: boolean): Row {
  return {
    key: `${hit.type}:${hit.id}`,
    kind: hit.type,
    id: hit.id,
    title: hit.title,
    subtitle: hit.subtitle,
    why: hit.why ?? '',
    right: withWhen ? (hit.when ?? '') : hit.type === 'category'
      ? String((hit as sx.CategoryHit).count) : '',
    route: hit.route,
  };
}

/* ========================================================================== *
 * THE PALETTE
 * ========================================================================== */

/** How long the shopkeeper stops typing before the counter is asked. */
const SETTLE_MS = 140;
/** More than a screenful; `matched` says how many were left out. */
const LIMIT = 24;
/** Enough to open with, few enough that the list is not itself a search. */
const RECENT = 8;
const OPENED_SHOWN = 5;

type Filter = 'all' | sx.Kind | 'nav';

const FILTERS: Array<{ value: Filter; label: string; title: string }> = [
  { value: 'all', label: 'Everything', title: 'Products, orders, bills, groups and screens' },
  { value: 'product', label: 'Products', title: 'The catalogue: names, sku ids and printed codes' },
  { value: 'order', label: 'Orders', title: 'Storefront orders: id, customer, phone, total' },
  { value: 'bill', label: 'Bills', title: 'Sessions on the audit chain, by id or amount' },
  { value: 'category', label: 'Groups', title: 'Facets this shop’s own data implies' },
  { value: 'nav', label: 'Screens', title: 'Go to a screen. Needs no server.' },
];

const IS_MAC = typeof navigator !== 'undefined'
  && /Mac|iPhone|iPad|iPod/i.test(navigator.userAgent || '');

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Every screen this build registered, as something to go to. */
const NAV: sx.NavCommand[] = TABS.flatMap((t) =>
  t.items.map((i) => ({ id: i.id, label: i.label, sub: i.sub, group: t.label })),
);

/**
 * The palette is full-screen below this width, and the input is then about
 * twenty-five characters wide.
 *
 * The long placeholder is a whole sentence and a phone truncates it mid-word —
 * "an order, a bill, an am". A placeholder that has to be read to be useful and
 * cannot be read is worse than a short one, so the width picks the sentence.
 * The number matches the breakpoint in palette.css and is the only thing in
 * this file that has to stay in step with it.
 */
const NARROW = '(max-width: 620px)';

function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(
    () => typeof matchMedia === 'function' && matchMedia(NARROW).matches,
  );
  useEffect(() => {
    if (typeof matchMedia !== 'function') return;
    const mq = matchMedia(NARROW);
    const on = () => setNarrow(mq.matches);
    on();
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  return narrow;
}

function Marked({ text, q }: { text: string; q: string }) {
  const parts = sx.highlight(text, q);
  return (
    <>
      {parts.map((p, i) => (p.hit
        ? <mark key={i}>{p.text}</mark>
        : <span key={i}>{p.text}</span>))}
    </>
  );
}

function SkelRows({ n }: { n: number }) {
  return (
    <div className="pal-skels" aria-hidden="true">
      {Array.from({ length: n }, (_, i) => (
        <div className="pal-skel" key={i}>
          <span className="skel pal-skel-m" />
          <span className="pal-skel-t">
            <span className="skel" style={{ width: `${58 - i * 6}%` }} />
            <span className="skel" style={{ width: `${76 - i * 5}%` }} />
          </span>
        </div>
      ))}
    </div>
  );
}

/** A source that could not be read, in the server's own words. Never hidden. */
function ShortAnswer({ sources }: { sources: Record<string, sx.SourceState> }) {
  const bad = Object.entries(sources).filter(([, s]) => !s.available || !s.complete);
  if (bad.length === 0) return null;
  return (
    <div className="pal-warn" role="status">
      <span className="pal-warn-i"><IcoWarn size={14} /></span>
      <span className="pal-warn-t">
        <b>This answer is short of what the shop holds.</b>
        {bad.map(([name, s]) => (
          <span className="pal-warn-l" key={name}>
            {name}: {s.detail ?? s.reason ?? 'could not be read'}
          </span>
        ))}
      </span>
    </div>
  );
}

function Palette() {
  const [q, setQ] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [sel, setSel] = useState(0);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<sx.Result<sx.SearchBody> | null>(null);
  const [rec, setRec] = useState<sx.Result<sx.RecentBody> | null>(null);
  const [health, setHealth] = useState<sx.SearchHealth | null>(null);
  const [opened, setOpened] = useState<sx.OpenedItem[]>(() => sx.opened());
  const narrow = useNarrow();

  const box = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const returnTo = useRef<Element | null>(null);

  const term = q.trim();

  /* --- opening: focus, scroll lock, and putting focus back afterwards ---- */
  useEffect(() => {
    returnTo.current = document.activeElement;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const t = setTimeout(() => input.current?.focus(), 0);
    return () => {
      clearTimeout(t);
      document.body.style.overflow = prev;
      (returnTo.current as HTMLElement | null)?.focus?.();
    };
  }, []);

  /* --- what is recent, and what search does not look at ------------------ */
  useEffect(() => {
    const ctl = new AbortController();
    void (async () => {
      const r = await sx.recent(RECENT, ctl.signal);
      if (!ctl.signal.aborted) setRec(r);
    })();
    void (async () => {
      const h = await sx.health(ctl.signal);
      if (!ctl.signal.aborted && h.ok) setHealth(h);
    })();
    return () => ctl.abort();
  }, []);

  /* --- the query --------------------------------------------------------
     Debounced, and every in-flight request is ABORTED by the cleanup before
     the next one starts. Without that, a slow reply to "mag" can land after a
     fast reply to "maggi" and the box shows the wrong list for a query that is
     no longer on screen. The abort comes back as `REPLACED` and is dropped. */
  useEffect(() => {
    if (!term || filter === 'nav') {
      setRes(null);
      setBusy(false);
      return;
    }
    setBusy(true);
    const ctl = new AbortController();
    const t = setTimeout(() => {
      void (async () => {
        const kinds = filter === 'all' ? undefined : ([filter] as sx.Kind[]);
        const r = await sx.search(term, { limit: LIMIT, kinds, signal: ctl.signal });
        if (ctl.signal.aborted) return;
        if (!r.ok && r.reason === sx.REPLACED) return;
        setRes(r);
        setBusy(false);
      })();
    }, SETTLE_MS);
    return () => { clearTimeout(t); ctl.abort(); };
  }, [term, filter]);

  /* --- the list ---------------------------------------------------------- */

  const navHits = useMemo(
    () => (term ? sx.matchNav(NAV, term) : NAV.map((c) => ({ ...c, score: 0, why: '' }))),
    [term],
  );

  const groups: Group[] = useMemo(() => {
    const out: Group[] = [];

    const navGroup: Group = {
      label: 'Go to',
      note: 'the screens this counter has',
      rows: navHits.map((c) => ({
        key: `nav:${c.id}`,
        kind: 'nav' as const,
        id: c.id,
        title: c.label,
        subtitle: c.sub,
        why: c.why,
        right: c.group,
        route: `#/${c.id}`,
      })),
    };

    if (!term) {
      if (filter === 'all' && opened.length > 0) {
        out.push({
          label: 'Opened from this box',
          note: 'this browser’s own list, not the counter’s',
          rows: opened.slice(0, OPENED_SHOWN).map((o) => ({
            key: `open:${o.type}:${o.id}`,
            kind: o.type,
            id: o.id,
            title: o.title,
            subtitle: '',
            why: '',
            right: '',
            route: o.route,
          })),
          aside: (
            <button
              type="button"
              className="pal-forget"
              onClick={() => { sx.forgetOpened(); setOpened([]); }}
            >
              Forget these
            </button>
          ),
        });
      }
      /* THE SCREENS COME BEFORE THE RECENTS with nothing typed. A counter that
         has rung up two hundred bills has nine rows of recent before the first
         command, and pushing "go to Orders" under the fold makes the fastest
         thing in the app the one you have to scroll for. With a query typed the
         order is the other way round — see below. */
      if (filter === 'all' || filter === 'nav') out.push(navGroup);
      if (filter !== 'nav') {
        const body = rec && rec.ok ? rec : null;
        /* `items` is what happened last, LITERALLY — and on a counter that has
           rung up two hundred bills and taken three orders that literal answer
           is eight till sessions and nothing else. The server buckets the same
           rows a few to a kind for exactly this box, so a palette opening on
           nothing typed shows a bit of each. Asking for one kind takes the
           literal list instead, because then the kind IS the question. */
        const items = filter === 'all'
          ? KIND_ORDER.flatMap((k) => body?.by_kind[k] ?? [])
          : (body?.items ?? []).filter((h) => h.type === filter);
        if (items.length > 0) {
          out.push({
            label: 'Last touched on this counter',
            note: filter === 'all'
              ? `newest first, up to ${body?.per_kind ?? 3} of each kind`
              : 'newest first',
            rows: items.map((h) => rowOfHit(h, true)),
          });
        }
        const cats = filter === 'all' || filter === 'category' ? body?.categories ?? [] : [];
        if (cats.length > 0) {
          out.push({
            label: 'Groups',
            note: 'facets this shop’s own data implies, not a product taxonomy',
            rows: cats.map((h) => rowOfHit(h, false)),
          });
        }
      }
      return out;
    }

    // A query. Server results keep the server's order; the groups follow the
    // order their FIRST row arrived in, so the top-ranked hit is still on top.
    const hits = res && res.ok ? res.results : [];
    const seen: sx.Kind[] = [];
    const byKind = new Map<sx.Kind, Row[]>();
    for (const h of hits) {
      if (!byKind.has(h.type)) { byKind.set(h.type, []); seen.push(h.type); }
      byKind.get(h.type)?.push(rowOfHit(h, false));
    }

    // "orders" typed in full should offer the Orders SCREEN first. Anything
    // weaker than the screen's own name goes below the shop's own results,
    // because a partial word is far more often a product than a destination.
    const strong = (navHits[0]?.score ?? 0) >= sx.NAV_STRONG;
    const showNav = (filter === 'all' || filter === 'nav') && navGroup.rows.length > 0;
    if (showNav && strong) out.push(navGroup);
    /* `seen` is FIRST-ARRIVAL ORDER and is deliberately not sorted into a fixed
       product-order-bill sequence. Sorting it would put a product group above a
       bill group even when the bill was the top-ranked hit, and the first row of
       the list is the one Enter opens — so a fixed order would quietly hand the
       shopkeeper something the server ranked second. The server's order is
       already stable, so this one is too. */
    for (const kind of seen) {
      out.push({ label: GROUP_LABEL[kind], rows: byKind.get(kind) ?? [] });
    }
    if (showNav && !strong) out.push(navGroup);
    return out;
  }, [term, filter, res, rec, navHits, opened]);

  const rows = useMemo(() => groups.flatMap((g) => g.rows), [groups]);
  /* The row's place in the whole list, by key. Every group needs it to draw a
     row's id and its selected state, and walking the flat list once per row to
     find it is the kind of thing that is free at forty rows and is not at four
     hundred. */
  const indexOf = useMemo(() => {
    const m = new Map<string, number>();
    rows.forEach((r, i) => m.set(r.key, i));
    return m;
  }, [rows]);
  const sig = `${term}|${filter}|${rows.length}`;

  useEffect(() => { setSel(0); }, [sig]);

  useEffect(() => {
    const el = list.current?.querySelector<HTMLElement>('[aria-selected="true"]');
    el?.scrollIntoView({ block: 'nearest' });
  }, [sel, sig]);

  const run = useCallback((row: Row) => {
    sx.noteOpened({ type: row.kind, id: row.id, title: row.title, route: row.route });
    closePalette();
    // The shell derives its route from the hash and listens for `hashchange`,
    // so assigning it IS the navigation. Nothing here needs App.tsx's own `go`,
    // which is why this component takes no props and can be mounted anywhere.
    if (location.hash !== row.route) location.hash = row.route;
  }, []);

  const onKey = useCallback((e: ReactKeyboardEvent) => {
    if (e.key === 'Escape') { e.preventDefault(); closePalette(); return; }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSel((i) => (rows.length === 0 ? 0 : (i + 1) % rows.length));
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSel((i) => (rows.length === 0 ? 0 : (i - 1 + rows.length) % rows.length));
      return;
    }
    if (e.key === 'Home' && rows.length > 0) { e.preventDefault(); setSel(0); return; }
    if (e.key === 'End' && rows.length > 0) { e.preventDefault(); setSel(rows.length - 1); return; }
    if (e.key === 'Enter') {
      const row = rows[sel];
      if (row) { e.preventDefault(); run(row); }
      return;
    }
    // Tab stays inside the box. A palette whose focus walks out behind its own
    // scrim is a palette a keyboard user cannot get out of.
    if (e.key === 'Tab' && box.current) {
      const items = Array.from(box.current.querySelectorAll<HTMLElement>(FOCUSABLE))
        .filter((el) => el.offsetParent !== null || el === document.activeElement);
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
    }
  }, [rows, sel, run]);

  const current = rows[sel];
  const refused = res && !res.ok ? res : null;
  const recRefused = !term && rec && !rec.ok ? rec : null;
  const body = res && res.ok ? res : null;
  const recBody = rec && rec.ok ? rec : null;
  const waiting = busy || (!!term && filter !== 'nav' && res === null);
  const loadingRecent = !term && rec === null && filter !== 'nav';

  return createPortal(
    <div
      className="pal-scrim"
      onMouseDown={(e) => { if (e.target === e.currentTarget) closePalette(); }}
    >
      <div
        ref={box}
        className="pal"
        role="dialog"
        aria-modal="true"
        aria-label="Search this counter"
        onKeyDown={onKey}
      >
        <div className="pal-head">
          <span className="pal-mag"><IcoSearch size={17} /></span>
          <input
            ref={input}
            className="pal-inp"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={narrow
              ? 'Search, or go to a screen'
              : 'Search a product, an order, a bill, an amount — or a screen'}
            aria-label="Search this counter"
            role="combobox"
            aria-expanded="true"
            aria-controls="pal-list"
            aria-activedescendant={current ? `pal-opt-${sel}` : undefined}
            autoComplete="off"
            autoCorrect="off"
            spellCheck={false}
            enterKeyHint="go"
          />
          {q !== '' && (
            <button type="button" className="pal-clear" onClick={() => { setQ(''); input.current?.focus(); }}
                    aria-label="Clear what you typed">
              <IcoX size={13} />
            </button>
          )}
          {/* Both marks are rendered and the width shows one: `esc` names the
              key on a machine that has one, and the WORD on a phone — a second
              cross next to the one that clears the field is two marks that look
              alike and do different things. */}
          <button type="button" className="pal-close" onClick={closePalette} aria-label="Close">
            <kbd>esc</kbd>
            <span className="pal-close-w">Close</span>
          </button>
        </div>

        <div className="pal-kinds" role="group" aria-label="What to search">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              type="button"
              className="pal-kind"
              title={f.title}
              aria-pressed={filter === f.value}
              onClick={() => setFilter(f.value)}
            >
              {f.label}
            </button>
          ))}
        </div>

        <div className="pal-list" id="pal-list" ref={list} role="listbox" aria-label="Results">
          {refused && (
            <div className="pal-block">
              <Refusal
                reason={refused.reason}
                detail={refused.detail}
                hint={rows.length > 0
                  ? 'The screens below need no server and still work.'
                  : 'Typing a screen’s name still works — that list is in the page.'}
              />
            </div>
          )}
          {recRefused && (
            <div className="pal-block">
              <Refusal reason={recRefused.reason} detail={recRefused.detail} />
            </div>
          )}
          {body?.partial && <ShortAnswer sources={body.sources} />}
          {!term && recBody?.partial && <ShortAnswer sources={recBody.sources} />}

          {(waiting || loadingRecent) && <SkelRows n={4} />}

          {/* NOT when the server refused. "Nothing matches" is a claim about
              the shop, and a counter that could not be reached has made no
              claim about anything — printing both at once tells a shopkeeper
              their product is missing when it is the line that is down. */}
          {!waiting && !loadingRecent && rows.length === 0 && !refused && !recRefused && (
            <div className="pal-block">
              <Empty
                title={term ? `Nothing on this counter matches “${term}”` : 'Nothing to show yet'}
                icon={false}
              >
                {term ? (
                  <>
                    Try fewer words, or part of a printed code.
                    {health && health.not_searched.length > 0 && (
                      <span className="pal-notlist">
                        <b>What search does not look at</b>
                        {health.not_searched.map((n, i) => <span key={i}>{n}</span>)}
                      </span>
                    )}
                  </>
                ) : (
                  <>
                    Nothing has been sold, ordered or taught on this counter yet.
                    {recBody?.notes.map((n, i) => <span className="pal-note" key={i}>{n}</span>)}
                  </>
                )}
              </Empty>
            </div>
          )}

          {!waiting && !loadingRecent && groups.map((g) => (
            <div className="pal-group" role="group" aria-label={g.label} key={g.label}>
              <div className="pal-group-h">
                <span className="eyebrow">{g.label}</span>
                {g.note && <span className="pal-group-n">{g.note}</span>}
                <span className="pal-group-line" />
                {g.aside}
              </div>
              {g.rows.map((row) => {
                const i = indexOf.get(row.key) ?? 0;
                const on = i === sel;
                return (
                  <div
                    key={row.key}
                    id={`pal-opt-${i}`}
                    className="pal-opt"
                    role="option"
                    aria-selected={on}
                    onMouseMove={() => { if (!on) setSel(i); }}
                    onClick={() => run(row)}
                  >
                    <span className="pal-m">{MARK[row.kind]()}</span>
                    <span className="pal-t">
                      <span className="pal-title"><Marked text={row.title} q={term} /></span>
                      {row.subtitle && (
                        <span className="pal-sub"><Marked text={row.subtitle} q={term} /></span>
                      )}
                    </span>
                    {row.right && <span className="pal-r tnum">{row.right}</span>}
                  </div>
                );
              })}
            </div>
          ))}

          {/* HOW MUCH WAS LEFT OUT, always on the list rather than in the
              footer: the footer is already saying why the selected row matched,
              and a count that only appears when nothing is selected is a count
              nobody ever sees. */}
          {body?.truncated && !waiting && (
            <p className="pal-more">
              Showing {body.count} of {body.matched}. Type more of the name.
            </p>
          )}

          {/* What is missing from "recent", and why — most often that nothing
              in this program dates a product until somebody edits one. It is a
              footnote when there are rows and the whole answer when there are
              not, but it is never dropped. */}
          {!term && !waiting && !loadingRecent && rows.length > 0
            && recBody && recBody.notes.length > 0 && (
            <p className="pal-notes">
              {recBody.notes.map((n, i) => <span key={i}>{n}</span>)}
            </p>
          )}
        </div>

        <div className="pal-foot">
          <span className="pal-why">
            {current?.why
              ? current.why.charAt(0).toUpperCase() + current.why.slice(1)
              : !term
                ? 'Type to search. Nothing here settles money.'
                : ''}
          </span>
          <span className="pal-keys">
            <kbd>↑</kbd><kbd>↓</kbd><span>move</span>
            <kbd>↵</kbd><span>open</span>
            <kbd>esc</kbd><span>close</span>
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/* ========================================================================== *
 * WHAT THE APP MOUNTS
 * ========================================================================== */

/**
 * Mount ONE of these, anywhere inside the shell. It renders nothing until the
 * palette is opened, and it owns the ⌘K listener while it is mounted.
 */
export default function CommandPalette() {
  const open = useSyncExternalStore(subscribe, readOpen, readOpen);
  const [primary, setPrimary] = useState(false);

  useEffect(() => {
    const id = ++hostSeq;
    hosts.push(id);
    const check = () => setPrimary(hosts[0] === id);
    hostSubs.add(check);
    notifyHosts();
    return () => {
      const i = hosts.indexOf(id);
      if (i >= 0) hosts.splice(i, 1);
      hostSubs.delete(check);
      notifyHosts();
    };
  }, []);

  useEffect(() => {
    if (!primary) return;
    const on = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.altKey) return;
      if (e.key !== 'k' && e.key !== 'K') return;
      // A dialog stops the page, and a palette opened over one would put a text
      // field outside that dialog's focus trap. ⌘K still CLOSES the palette, so
      // this can never strand anybody.
      if (!isOpen && document.querySelector('[role="dialog"][aria-modal="true"]')) return;
      e.preventDefault();
      togglePalette();
    };
    addEventListener('keydown', on);
    return () => removeEventListener('keydown', on);
  }, [primary]);

  if (!primary || !open || typeof document === 'undefined') return null;
  return <Palette />;
}

/**
 * The way in for a thumb.
 *
 * Built for the dark top bar, which is where it belongs — a shop phone has no
 * ⌘K and a palette nobody can open is a palette nobody has. It is a separate
 * export so the shell can place it without this file knowing anything about
 * the bar's layout.
 */
export function PaletteButton({ label = 'Search', className }: {
  label?: string;
  className?: string;
}) {
  return (
    <button
      type="button"
      className={['pal-btn', className ?? ''].filter(Boolean).join(' ')}
      onClick={openPalette}
      title={`Search products, orders and bills — ${IS_MAC ? '⌘' : 'Ctrl-'}K`}
      aria-label="Search this counter"
    >
      <IcoSearch size={14} />
      <span className="pal-btn-l">{label}</span>
      <kbd className="pal-btn-k">{IS_MAC ? '⌘K' : 'Ctrl K'}</kbd>
    </button>
  );
}
