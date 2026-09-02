/**
 * KHOJ — every request the command palette makes, in one place, with types.
 *
 * The server module is `gawaah/search.py`. It answers three GETs and writes
 * nothing, so there is no POST, no PATCH and no DELETE in this file, because
 * there is none on the server either.
 *
 * Four rules this module keeps.
 *
 *  1. A REFUSAL IS A RESULT, NOT AN ERROR. `/search` answers `{ok:false,
 *     reason, detail}` with a 400 on purpose — an empty query is a refusal by
 *     name, not a crash — so the body is parsed on a non-2xx status instead of
 *     thrown, and the palette renders the server's own reason verbatim.
 *  2. NO MONEY IS COMPUTED HERE OR ANYWHERE ABOVE HERE. Every amount arrives
 *     already rendered as a string (`price_rupees`, `total_rupees`) beside the
 *     integer paise it came from. Nothing in this file or in
 *     `components/CommandPalette.tsx` divides, rounds or formats a price: the
 *     browser is not an author of money, and a palette that re-rendered a
 *     rupee figure would be a second opinion about it.
 *  3. A SHORT ANSWER IS NOT A COMPLETE ONE. `partial` and `sources` are carried
 *     through untouched so the palette can say WHICH source could not be read
 *     and why, rather than showing a shortened list as though it were the shop.
 *  4. THE TYPO BUDGET IS THE SERVER'S. Nothing here re-ranks, re-scores or
 *     re-orders what came back. `highlight()` below marks what it can see
 *     literally and marks nothing when the match was fuzzy — the server's own
 *     `why` sentence ("the name is one letter off what you typed") is what
 *     explains those, and the palette shows it.
 *
 * `send` is copied from `lib/api.ts` rather than imported: it is module-private
 * there, and a component must not widen the till's own request layer to borrow
 * one function. The precedence rule it encodes — an explicit `ok`, then an
 * `error` string, then the HTTP status — is load-bearing and is copied intact,
 * because FastAPI's own 422 arrives as `{"detail": ...}` with no `ok` at all
 * and a rule that read only the body would file a validation failure as a
 * success.
 *
 * NOTE ON DEV AND ON MOUNTING. Two things outside this file have to be true
 * before any of it answers, and both are named in the report to the
 * orchestrator: `gawaah/search.py`'s router has to be mounted on the till, and
 * `/search` has to be in the `API_ROUTES` allowlist in `vite.config.ts` or
 * every call here 404s under `npm run dev` while working in the built site.
 * Until then the palette still navigates — see `matchNav` — and shows the 404
 * as the refusal it is rather than an empty box.
 */

import type { Refusal, Result } from './api';

/**
 * A request this browser threw away because the shopkeeper kept typing.
 *
 * Not a failure and not something to show anybody: the caller compares against
 * this exact string and drops the result on the floor. It is a constant rather
 * than a magic string so a rename cannot silently start rendering aborted
 * requests as refusals.
 */
export const REPLACED = 'the search was replaced by a newer one';

async function send<T>(url: string, signal?: AbortSignal): Promise<Result<T>> {
  let res: Response;
  try {
    res = await fetch(url, { cache: 'no-store', signal });
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      return { ok: false, reason: REPLACED };
    }
    // The network, not the shop. They need different fixes, so say which.
    return { ok: false, reason: 'the counter could not reach its own server', detail: String(e) };
  }
  let body: Record<string, unknown>;
  try {
    body = (await res.json()) as Record<string, unknown>;
  } catch {
    return { ok: false, reason: `the server replied ${res.status} with something that was not JSON` };
  }
  if (body && body.ok === undefined) {
    if (typeof body.error === 'string') {
      return { ok: false, reason: body.error, detail: String(body.detail ?? '') };
    }
    if (!res.ok) {
      return {
        ok: false,
        reason: `the server refused with HTTP ${res.status}`,
        detail: typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body),
      };
    }
    return { ...body, ok: true } as unknown as Result<T>;
  }
  return body as unknown as Result<T>;
}

export type { Refusal, Result };

/* ------------------------------------------------------------- the shapes -- */

/** The four things this counter can be searched for. The server refuses any other by name. */
export type Kind = 'product' | 'order' | 'bill' | 'category';

export const KINDS: readonly Kind[] = ['product', 'order', 'bill', 'category'];

/** What every result carries, whatever kind it is. */
export interface HitBase {
  type: Kind;
  id: string;
  title: string;
  /** Already assembled by the server, money included. Rendered verbatim. */
  subtitle: string;
  /** The screen that owns this thing, e.g. `products`. */
  screen: string;
  /** The hash route that opens it, e.g. `#/products?sku=parle_g_200g`. */
  route: string;
  /** Integer, the server's own ranking score. Present on a search, not on recent. */
  score?: number;
  /** Plain English: why this matched, or why it is recent. Shown, never paraphrased. */
  why?: string;
  /** ISO-8601. On `/search/recent` only. */
  at?: string;
  /** "four minutes ago", computed by the server in whole units. */
  when?: string;
}

export interface ProductHit extends HitBase {
  type: 'product';
  sku_id: string;
  name: string;
  /**
   * THE CHARGED PRICE, offer applied — not the marked one. A palette quoting
   * the marked price would quote a number the money service will refuse to
   * mint. Null when the catalogue row's price is not integer paise, which is
   * the one case where the product is still findable and says it has no price.
   */
  price_paise: number | null;
  price_rupees: string | null;
  on_offer: boolean;
  taught_by: string;
  taught_label: string;
  codes: string[];
}

export interface OrderHit extends HitBase {
  type: 'order';
  order_id: string;
  customer_name: string;
  status: string;
  total_paise: number | null;
  total_rupees: string | null;
  /** The gateway confirmed this one. Not "the shop thinks it was paid". */
  paid: boolean;
}

export interface BillHit extends HitBase {
  type: 'bill';
  session_id: string;
  total_paise: number | null;
  total_rupees: string | null;
  /** False for a session that never became a bill. Included, and marked. */
  closed: boolean;
  settled: boolean;
  state: string | null;
}

export interface CategoryHit extends HitBase {
  type: 'category';
  count: number;
  /** What this group was computed from. A facet, never a taxonomy. */
  derived_from: string;
}

export type Hit = ProductHit | OrderHit | BillHit | CategoryHit;

/**
 * One of the four sources, and how much of it was readable.
 *
 * `available` and `complete` are different claims and are kept apart: an audit
 * chain that stops verifying half way is available and incomplete, and showing
 * that as a complete answer is how a shopkeeper is told there is no matching
 * bill when the matching one is past the break.
 */
export interface SourceState {
  available: boolean;
  complete: boolean;
  reason: string | null;
  detail: string | null;
  scanned: number;
}

/** What the query actually cost, measured, plus what the same code costs at 10,000 SKUs. */
export interface Cost {
  took_us: number;
  took_ms: number;
  budget_ms: number;
  within_budget: boolean;
  scanned: Record<string, number>;
  measured_us_per_1000_products: number;
  note: string;
}

export interface SearchBody {
  settles_money: boolean;
  q: string;
  kinds: Kind[];
  limit: number;
  /** How many matched in total — larger than `count` when capped by `limit`. */
  matched: number;
  count: number;
  truncated: boolean;
  results: Hit[];
  partial: boolean;
  sources: Record<string, SourceState>;
  cost: Cost;
}

export interface RecentBody {
  settles_money: boolean;
  limit: number;
  matched: number;
  count: number;
  items: Hit[];
  /** The same rows bucketed a few to a kind, for a palette that wants a bit of each. */
  by_kind: Record<string, Hit[]>;
  per_kind: number;
  categories: CategoryHit[];
  /**
   * Why something a shopkeeper expected is missing — most often that no product
   * can honestly be called recent, because nothing in this program timestamps
   * one until it is edited. Shown as written.
   */
  notes: string[];
  partial: boolean;
  sources: Record<string, SourceState>;
  cost: Cost;
}

export interface SearchHealth {
  settles_money: boolean;
  shop_dir: string | null;
  kinds: Kind[];
  sources: Record<string, SourceState>;
  searchable: number;
  limits: {
    max_query_chars: number;
    default_results: number;
    max_results: number;
    typo_budget: Record<string, number>;
  };
  /** The fields deliberately NOT searched, in the server's own words. */
  not_searched: string[];
  categories_are_derived: string;
  cost: Cost;
}

/* -------------------------------------------------------------- the calls -- */

/**
 * Everything that matches one query, ranked by the server.
 *
 * `signal` aborts a request the shopkeeper has already typed past. An aborted
 * call comes back as a refusal whose reason is exactly `REPLACED`, so the
 * caller can drop it without a thrown error crossing a component boundary.
 */
export function search(
  q: string,
  opts?: { limit?: number; kinds?: readonly Kind[]; signal?: AbortSignal },
): Promise<Result<SearchBody>> {
  const p = new URLSearchParams({ q });
  if (opts?.limit !== undefined) p.set('limit', String(opts.limit));
  if (opts?.kinds && opts.kinds.length > 0 && opts.kinds.length < KINDS.length) {
    p.set('kind', opts.kinds.join(','));
  }
  return send<SearchBody>(`/search?${p.toString()}`, opts?.signal);
}

/** The last things touched, so the palette opens with somewhere to go. */
export function recent(limit?: number, signal?: AbortSignal): Promise<Result<RecentBody>> {
  const p = new URLSearchParams();
  if (limit !== undefined) p.set('limit', String(limit));
  const qs = p.toString();
  return send<RecentBody>(qs ? `/search/recent?${qs}` : '/search/recent', signal);
}

/**
 * What search can see, what it costs, and what it deliberately does not look at.
 *
 * The palette asks once and uses `not_searched` in its no-results state: a
 * shopkeeper who searched for a street name and found nothing is owed the
 * sentence "delivery addresses are not searched", not an empty box.
 */
export function health(signal?: AbortSignal): Promise<Result<SearchHealth>> {
  return send<SearchHealth>('/search/health', signal);
}

/* -------------------------------------------------------- naming the parts -- */

/** The words of a query, lowercased. `.` survives so `35.45` stays one word. */
export function queryTokens(q: string): string[] {
  return q
    .toLowerCase()
    .split(/[^a-z0-9.]+/)
    .filter((t) => t.length > 0)
    .slice(0, 8);
}

/** A run of text, and whether it is part of what the shopkeeper typed. */
export interface Part {
  text: string;
  hit: boolean;
}

/** Is the character before `i` something other than a letter or a digit? */
function atWordStart(text: string, i: number): boolean {
  if (i <= 0) return true;
  const c = text[i - 1];
  return c === undefined || !/[a-z0-9]/.test(c);
}

/** The first occurrence of `tok` that starts a word, or -1. */
function boundaryIndex(low: string, tok: string): number {
  let from = 0;
  for (;;) {
    const at = low.indexOf(tok, from);
    if (at < 0) return -1;
    if (atWordStart(low, at)) return at;
    from = at + 1;
  }
}

/**
 * Split `text` into the runs that match the query and the runs that do not.
 *
 * Literal, case-insensitive, word-start-first. It deliberately does NOT
 * reproduce the server's typo budget: a highlighter that guessed which letters
 * the server forgave would be a second matcher that can disagree with the first,
 * and the visible symptom of disagreeing would be a row with the wrong letters
 * lit. When nothing lights up, the row's `why` line says what actually matched.
 */
export function highlight(text: string, q: string): Part[] {
  const t = text ?? '';
  const tokens = queryTokens(q);
  if (!t || tokens.length === 0) return [{ text: t, hit: false }];

  const low = t.toLowerCase();
  const ranges: Array<[number, number]> = [];
  for (const tok of tokens) {
    let at = boundaryIndex(low, tok);
    if (at < 0) at = low.indexOf(tok);
    if (at < 0) continue;
    ranges.push([at, at + tok.length]);
  }
  if (ranges.length === 0) return [{ text: t, hit: false }];

  ranges.sort((a, b) => a[0] - b[0]);
  const merged: Array<[number, number]> = [];
  for (const r of ranges) {
    const last = merged[merged.length - 1];
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
    else merged.push([r[0], r[1]]);
  }

  const out: Part[] = [];
  let cursor = 0;
  for (const [start, end] of merged) {
    if (start > cursor) out.push({ text: t.slice(cursor, start), hit: false });
    out.push({ text: t.slice(start, end), hit: true });
    cursor = end;
  }
  if (cursor < t.length) out.push({ text: t.slice(cursor), hit: false });
  return out;
}

/* --------------------------------------------------------- going somewhere -- */

/**
 * A screen this build actually has.
 *
 * Built by the palette from the shell's own `TABS`, never typed out here, so
 * the palette can only ever offer somewhere that exists. A screen the
 * orchestrator has not registered yet is not in `TABS`, is not offered, and
 * cannot be navigated to — which is the correct behaviour, because the hash for
 * an unregistered route lands on the till.
 */
export interface NavCommand {
  id: string;
  label: string;
  /** What the screen is for, in the shopkeeper's words. From the shell. */
  sub: string;
  /** The tab that owns it — Counter, Shop, Books. */
  group: string;
}

export interface NavHit extends NavCommand {
  score: number;
  why: string;
}

/** A nav score at or above this means the shopkeeper typed the screen's name. */
export const NAV_STRONG = 100;

const NAV_LABEL_START = 120;
const NAV_LABEL_WORD = 100;
const NAV_LABEL_IN = 60;
const NAV_GROUP_WORD = 45;
const NAV_SUB_WORD = 35;
const NAV_SUB_IN = 20;

/**
 * Match the query against the screens.
 *
 * EVERY WORD OF THE QUERY MUST LAND SOMEWHERE, the same rule the server applies
 * to products, so "orders paid" does not offer the Orders screen on the strength
 * of one word. Scores here are on their own scale and are never mixed with the
 * server's — the palette keeps these in their own group for exactly that reason.
 */
export function matchNav(commands: readonly NavCommand[], q: string): NavHit[] {
  const tokens = queryTokens(q);
  if (tokens.length === 0) return [];

  const out: NavHit[] = [];
  for (const cmd of commands) {
    const label = cmd.label.toLowerCase();
    const sub = cmd.sub.toLowerCase();
    const group = cmd.group.toLowerCase();

    let total = 0;
    let best = 0;
    let why = '';
    let missed = false;

    for (const tok of tokens) {
      let s = 0;
      let reason = '';
      if (label.startsWith(tok)) {
        s = NAV_LABEL_START;
        reason = 'the screen is called that';
      } else if (boundaryIndex(label, tok) >= 0) {
        s = NAV_LABEL_WORD;
        reason = 'the screen’s name has that word in it';
      } else if (label.includes(tok)) {
        s = NAV_LABEL_IN;
        reason = 'the screen’s name contains what you typed';
      } else if (boundaryIndex(group, tok) >= 0) {
        s = NAV_GROUP_WORD;
        reason = `it is under ${cmd.group}`;
      } else if (boundaryIndex(sub, tok) >= 0) {
        s = NAV_SUB_WORD;
        reason = 'that is what the screen is for';
      } else if (sub.includes(tok)) {
        s = NAV_SUB_IN;
        reason = 'that is what the screen is for';
      }
      if (s === 0) {
        missed = true;
        break;
      }
      total += s;
      if (s > best) {
        best = s;
        why = reason;
      }
    }

    if (missed || total === 0) continue;
    // The mean, so a two-word query that lands weakly twice does not outrank a
    // one-word query that named the screen outright.
    out.push({ ...cmd, score: Math.floor(total / tokens.length), why });
  }

  out.sort((a, b) => a.label.localeCompare(b.label));
  out.sort((a, b) => b.score - a.score);
  return out;
}

/* ------------------------------------------------------- what YOU opened -- */

/**
 * The palette's own memory of where this browser has been.
 *
 * It is NOT a second opinion about what is recent on the counter — the server
 * owns that, and answers it from orders, bills and the catalogue's edit chain.
 * This is a different fact that no server can know: the four things the person
 * at this keyboard opened from this box. It is labelled as that on screen.
 *
 * WHAT IS STORED, AND WHY SO LITTLE. Kind, id, title and route. No subtitle,
 * because a subtitle carries a price and an order status and both go stale in
 * minutes; no address, because search never returns one. A title can be a
 * customer's name ("Order for Rekha"), so `forget()` is on the screen next to
 * the list rather than buried, and every read and write is wrapped: a browser
 * with storage denied simply has no list here.
 */
const OPENED_KEY = 'gawaah.palette.opened';
const OPENED_MAX = 8;

export interface OpenedItem {
  type: Kind | 'nav';
  id: string;
  title: string;
  route: string;
  /** Milliseconds since the epoch. Ordering only; nothing is derived from it. */
  at: number;
}

function readOpened(): OpenedItem[] {
  try {
    const raw = localStorage.getItem(OPENED_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((row): row is OpenedItem => {
      if (typeof row !== 'object' || row === null) return false;
      const r = row as Record<string, unknown>;
      return typeof r.id === 'string' && typeof r.title === 'string'
        && typeof r.route === 'string' && typeof r.type === 'string'
        && typeof r.at === 'number';
    }).slice(0, OPENED_MAX);
  } catch {
    return [];
  }
}

/** What this browser has opened from the palette, newest first. */
export function opened(): OpenedItem[] {
  return readOpened();
}

/** Remember one. Newest first, de-duplicated on kind + id, capped. */
export function noteOpened(item: Omit<OpenedItem, 'at'>): void {
  try {
    const next = [
      { ...item, at: Date.now() },
      ...readOpened().filter((r) => !(r.id === item.id && r.type === item.type)),
    ].slice(0, OPENED_MAX);
    localStorage.setItem(OPENED_KEY, JSON.stringify(next));
  } catch {
    // Storage denied or full. The palette loses a convenience, not a fact.
  }
}

/** Throw the list away. Reachable from the palette itself. */
export function forgetOpened(): void {
  try {
    localStorage.removeItem(OPENED_KEY);
  } catch {
    /* nothing to undo */
  }
}
