/**
 * Every request the Stock screen makes, in one place, with types.
 *
 * THERE IS NO MONEY IN THIS FILE. Not a paise, not a rupee, not a price. The
 * server module it talks to (`gawaah/stock.py`) has none either, and that is
 * deliberate on both sides: a valuation of what is on the shelf would be an
 * arithmetic claim about money, and neither this page nor that module has any
 * business making one. Units are counts of packets.
 *
 * Two rules copied from `api.ts` and kept for the same reasons:
 *
 *  1. THE BROWSER IS NEVER AN AUTHOR. The page sends an intent — this many
 *     packets of this product arrived, for this reason — and the server decides
 *     the SIGN, writes the chain line and re-derives the figure. Nothing here
 *     computes on-hand, days of cover, or a shortfall against a reorder level.
 *     Every number rendered on the screen was derived by the server.
 *  2. A REFUSAL IS A RESULT, NOT AN ERROR. `/stock/*` answers `{ok:false,
 *     reason, detail}` with a 400 (404 for an unknown SKU) on purpose, so the
 *     body is parsed on non-2xx rather than thrown, and only a transport failure
 *     produces a refusal about the network.
 *
 * `send` is duplicated from `api.ts` rather than imported: it is module-private
 * there, and a new screen must not widen the till's own request layer to borrow
 * one function. The precedence rule it encodes — explicit `ok`, then an `error`
 * string, then the HTTP STATUS — is load-bearing and copied intact. FastAPI's
 * own 422 comes back as `{"detail": ...}` with neither `ok` nor `error`, and a
 * rule that only read the body would file a validation crash as a success.
 *
 * NOTE ON DEV: `vite.config.ts` proxies an explicit allowlist of path prefixes
 * to the till on :8790. `/stock` has to be in that list, and `stock.router` has
 * to be mounted on the till, or every request here 404s. Neither of those files
 * is in this screen's scope; see the report.
 */

import type { Result } from './api';

async function send<T>(url: string, init?: RequestInit): Promise<Result<T>> {
  let res: Response;
  try {
    res = await fetch(url, { cache: 'no-store', ...init });
  } catch (e) {
    // The network, not the product. Say which, because they need different fixes.
    return { ok: false, reason: 'the counter could not reach its own server', detail: String(e) };
  }
  let body: Record<string, unknown>;
  try {
    body = (await res.json()) as Record<string, unknown>;
  } catch {
    return { ok: false, reason: `server replied ${res.status} with something that was not JSON` };
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

/* ------------------------------------------------------------------ chain -- */

/**
 * The state of the hash chain the movements are stored in.
 *
 * Field names READ OFF `gawaah/stock.py`'s `read_events()`, not guessed from
 * the shape of some other module: `lines_verified` is the count that stood up
 * to a re-walk, `lines_readable` is the count that merely parsed, and they are
 * different numbers whenever anything is wrong. An interface that claimed
 * `lines` would render `undefined` in the one caption a shopkeeper needs.
 *
 * `exists: false` is NOT a failure — a counter that has never had a delivery
 * recorded has no log. `ok: false` is.
 */
export interface Chain {
  ok: boolean;
  exists: boolean;
  lines_verified: number;
  lines_readable: number;
  head: string | null;
  error: string | null;
  path: string;
}

/* ------------------------------------------------------------- the figures -- */

/**
 * Why days of cover is, or is not, a number.
 *
 * `days: null` is the honest answer in five distinct situations and the server
 * sends the sentence for whichever one it is. The page prints that sentence
 * VERBATIM: "not enough history" and "nothing has been billed in 30 days" are
 * different facts about a shelf, and collapsing them into one grey dash is how
 * a column stops being read.
 */
export interface Cover {
  days: number | null;
  units_billed: number;
  over_days: number | null;
  window_days: number;
  rate_text: string | null;
  why: string;
}

/** Whether there is a figure at all, and where it came from. */
export type Basis = 'counted' | 'never_counted';

export interface StockRow {
  sku_id: string;
  name: string | null;
  in_catalogue: boolean;
  taught_label: string | null;

  /* --- the baseline, which belongs to manage.py and is passed straight through */
  counted_units: number | null;
  counted_at: string | null;
  billed_since_count: number | null;
  remaining_after_billing: number | null;

  /* --- what the movement log adds to it */
  units_in_since_count: number;
  units_out_since_count: number;
  movement_delta_units: number;
  movements_since_count: number;
  movements_superseded_by_count: number;
  last_movement_at: string | null;

  /* --- the figure, and the server's own sentence saying how it was reached */
  on_hand_units: number | null;
  basis: Basis;
  /** True when the derived figure is below zero, which is not possible on a shelf. */
  needs_recount: boolean;
  derivation: string;

  /* --- reordering */
  reorder_level: number | null;
  reorder_level_set_at: string | null;
  at_or_under_reorder_level: boolean;
  days_of_cover: number | null;
  cover: Cover;
}

/** A product that has left the catalogue with movements still on the log. */
export interface OrphanRow {
  sku_id: string;
  name: string | null;
  in_catalogue: false;
  movements: number;
  units_in: number;
  units_out: number;
  reorder_level: number | null;
}

/** The closed vocabularies, served BY the server so the page cannot invent one. */
export interface Reasons {
  in: Record<string, string>;
  out: Record<string, string>;
}

export interface StockBody {
  count: number;
  counted_skus: number;
  at_or_under_level: number;
  /** A COUNT here. On `/stock/low` the same key is a LIST of rows, and on a row
      it is a boolean — three different types behind one word, read off the
      server rather than assumed. */
  needs_recount: number;
  reasons: Reasons;
  note: string;
  items: StockRow[];
  moved_but_not_in_catalogue: OrphanRow[];
  chain: Chain;
  /** The MONEY chain, whose state decides whether `billed_since_count` is whole. */
  bill_chain: Chain | null;
  unreadable_movement_lines: number;
  store_dir: string;
  now: string;
}

/** A level is set but the shelf has never been counted, so "low" cannot be said. */
export interface UnknownLow {
  sku_id: string;
  name: string | null;
  reorder_level: number | null;
  why: string;
}

export interface LowBody {
  count: number;
  low: StockRow[];
  unknown: UnknownLow[];
  /** Rows whose derived figure is below zero. A LIST here — see StockBody. */
  needs_recount: StockRow[];
  skus_with_a_level: number;
  skus_without_a_level: number;
  chain: Chain;
  bill_chain: Chain | null;
  now: string;
  note: string;
}

/* ------------------------------------------------------------ the movements -- */

export type Direction = 'in' | 'out';

export interface Movement {
  movement_id: string | null;
  at: string | null;
  sku_id: string;
  kind: Direction;
  /** SIGNED, and signed by the server: positive in, negative out. */
  units: number;
  reason: string | null;
  reason_label: string | null;
  note: string | null;
  hash: string | null;
}

export interface MovementsBody {
  count: number;
  matched: number;
  limit: number;
  sku: string | null;
  movements: Movement[];
  unreadable_movement_lines: number;
  chain: Chain;
}

export interface MovementRecorded {
  sku_id: string;
  movement_id: string;
  kind: Direction;
  units: number;
  reason: string;
  reason_label: string;
  note: string | null;
  recorded_at: string;
  chain_head: string;
  on_hand_units: number | null;
  derivation: string | null;
  needs_recount: boolean;
  at_or_under_reorder_level: boolean;
  detail: string;
}

export interface CountRecorded {
  sku_id: string;
  counted_units: number;
  counted_at: string;
  /** What the counter expected before the count. Null when nothing was counted before. */
  expected_units: number | null;
  /** counted − expected. Negative means stock left without being recorded. */
  discrepancy_units: number | null;
  superseded_movements: number;
  on_hand_units: number;
  /** The count is on disk even when the audit line is not; the server says which. */
  audited: boolean;
  audit_error: string | null;
  chain_head: string | null;
  note: string | null;
  detail: string;
}

export interface LevelSet {
  sku_id: string;
  reorder_level: number | null;
  cleared: boolean;
  chain_head: string;
  on_hand_units?: number | null;
  at_or_under_reorder_level?: boolean;
  detail: string;
}

/* ---------------------------------------------------------------- requests -- */

export const list = () => send<StockBody>('/stock');

export const low = () => send<LowBody>('/stock/low');

export const movements = (opts: { sku?: string | null; limit?: number } = {}) => {
  const q = new URLSearchParams();
  if (opts.sku) q.set('sku', opts.sku);
  if (opts.limit !== undefined) q.set('limit', String(opts.limit));
  const qs = q.toString();
  return send<MovementsBody>(`/stock/movements${qs ? `?${qs}` : ''}`);
};

/**
 * Record a movement. THE PAGE SENDS A MAGNITUDE, NEVER A SIGN.
 *
 * The direction is the endpoint, and the server turns it into the signed
 * integer that lands on the log — posting a negative number to `/in` is refused
 * by name. So there is exactly one place in this program that decides what
 * "twelve packets left the shelf" means as a number, and it is not the browser.
 */
export const move = (
  skuId: string,
  direction: Direction,
  units: number,
  reason: string,
  note?: string,
) =>
  send<MovementRecorded>(`/stock/${encodeURIComponent(skuId)}/${direction}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ units, reason, ...(note ? { note } : {}) }),
  });

/**
 * A re-count. This RESETS the baseline and supersedes the movements before it.
 *
 * Whole packets only. The server refuses 2.5 and 2.0 by two different names —
 * half a packet is not a thing a shelf holds, and a whole number that arrived as
 * a decimal means something upstream is doing arithmetic on packets in floating
 * point — so the caller must send a number it has already established is whole.
 */
export const count = (skuId: string, units: number, note?: string) =>
  send<CountRecorded>(`/stock/${encodeURIComponent(skuId)}/count`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ units, ...(note ? { note } : {}) }),
  });

/** Set the level this product should be reordered at, or `null` to clear it. */
export const setLevel = (skuId: string, units: number | null) =>
  send<LevelSet>(`/stock/${encodeURIComponent(skuId)}/reorder`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ units }),
  });

/* ------------------------------------------------------------------ shared -- */

/** A timestamp as the shopkeeper reads a clock, not as the ledger writes one. */
export function when(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('en-IN', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: true,
  });
}

/**
 * A whole number of packets typed by a person, or a refusal in the same shape
 * the server uses.
 *
 * The check is here so a typo answers instantly at the counter instead of after
 * a round trip, and so the value that goes on the wire is one this code has
 * already established is a whole number. It does NOT replace the server's
 * checks: the server refuses 2.5, 2.0, a string, a bool and anything over the
 * cap by five different names, and those refusals are shown verbatim when they
 * come back.
 */
export function packets(text: string, field: string): { units: number } | { reason: string; detail: string } {
  const t = text.trim();
  if (!t) {
    return {
      reason: `${field} is empty`,
      detail: 'Type a whole number of packets. Nothing has been recorded.',
    };
  }
  if (!/^\d+$/.test(t)) {
    return {
      reason: `“${t}” is not a whole number of packets`,
      detail:
        'Packets are counted, so a decimal, a minus sign or a word cannot be recorded. ' +
        'Stock that left the shelf goes in as a positive number under OUT.',
    };
  }
  return { units: Number(t) };
}
