/**
 * Every request the Expiry screen makes, in one place, with types.
 *
 * THE MONEY IN THIS FILE IS A STRING THE SERVER SENT. `value_at_risk_paise`
 * arrives as an integer and `value_at_risk_rupees` arrives already rendered,
 * both derived on the server from units × the marked price in the catalogue.
 * Nothing here multiplies, sums, divides or rounds a price: the page prints the
 * rupee string it was given, and the paise integer rides alongside only so a
 * test can assert the two agree. The server calls that figure a description
 * and not a charge, and so does the page — it is what the packets would fetch
 * if every one of them sold, and it is on no bill.
 *
 * Two rules copied from `api.ts` and kept for the same reasons:
 *
 *  1. THE BROWSER IS NEVER AN AUTHOR. The page sends what a person knows at the
 *     crate — this product, this many, this date on the packet — and the server
 *     derives days left, expired-or-not, the value, and the sign of the stock
 *     movement a write-off causes. A body carrying anything that looks like a
 *     price is refused by name (`client_tried_to_price_the_batch`).
 *  2. A REFUSAL IS A RESULT, NOT AN ERROR. `/expiry/*` answers `{ok:false,
 *     reason, detail}` with a 400 (404 for a batch or a product that is not
 *     there), so the body is parsed on non-2xx rather than thrown, and only a
 *     transport failure produces a refusal about the network.
 *
 * `send` is duplicated from `api.ts` rather than imported: it is module-private
 * there, and a new screen must not widen the till's own request layer to borrow
 * one function. The precedence rule — explicit `ok`, then an `error` string,
 * then the HTTP STATUS — is load-bearing and copied intact: FastAPI's own 422
 * comes back as `{"detail": ...}` with neither `ok` nor `error`.
 *
 * NOTE ON DEV: `vite.config.ts` proxies an explicit allowlist of prefixes to
 * the till on :8790. `/expiry` has to be in that list, and `expiry.router` has
 * to be mounted on the till, or every request here 404s under `npm run dev`.
 * Neither file is in this screen's scope; see the report.
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

/** The state of the hash chain the batches are stored in. Field names read
    off `gawaah/expiry.py`'s `read_events()`. `exists: false` is not a
    failure — a shop that has never booked a batch has no log. `ok: false` is. */
export interface Chain {
  ok: boolean;
  exists: boolean;
  lines_verified: number;
  lines_readable: number;
  head: string | null;
  error: string | null;
  path: string;
}

/* ----------------------------------------------------------------- batches -- */

/** Where a batch is in its life. `expired` is "before today on the server's own
    calendar"; a packet dated today is `open`. `closed` means nothing is left on
    it — written off or sold through. */
export type BatchState = 'open' | 'expired' | 'closed';

export interface HistoryLine {
  at: string | null;
  kind: 'booked' | 'written_off' | 'sold';
  units: number;
  note: string | null;
  stock_movement_id: string | null;
  stock_recorded: boolean;
  hash: string | null;
}

export interface Batch {
  batch_id: string;
  sku_id: string;
  name: string;
  in_catalogue: boolean;

  /* --- the units: what was booked, and what a person has since said of them */
  units: number;
  written_off_units: number;
  sold_units: number;
  units_remaining: number;

  /* --- the date, and the server's reading of it against today */
  expires_on: string;
  /** Negative when expired. The server decides this; the page only words it. */
  days_left: number;
  state: BatchState;
  recorded_at: string | null;
  note: string | null;

  /* --- the money, as the server rendered it. A description, not a charge. */
  price_paise: number | null;
  price_rupees: string | null;
  value_at_risk_paise: number | null;
  value_at_risk_rupees: string | null;
  /** The server's sentence for a null value: no price, or product gone. */
  value_why: string | null;

  stock_in_recorded: boolean;
  stock_in_movement_id: string | null;
  history: HistoryLine[];
}

export interface ValueBlock {
  expired_paise: number;
  expired_rupees: string;
  expired_unpriced_batches: number;
  soon_paise: number;
  soon_rupees: string;
  soon_unpriced_batches: number;
  basis: string;
  note: string;
}

export interface Product {
  sku_id: string;
  name: string;
  price_paise: number | null;
  price_rupees: string | null;
}

/** Whether a write-off here will move the figure on the Stock screen. */
export interface StockLink {
  available: boolean;
  out_reason: string;
  detail: string;
}

export interface Overview {
  today: string;
  window_days: number;
  expired: Batch[];
  soon: Batch[];
  counts: { batches: number; open: number; expired: number; soon: number; closed: number };
  value_at_risk: ValueBlock;
  products: Product[];
  stock_link: StockLink;
  chain: Chain;
  unreadable_lines: number;
  store_dir: string;
  note: string;
}

export interface BatchesBody {
  today: string;
  sku: string | null;
  include_closed: boolean;
  count: number;
  matched: number;
  batches: Batch[];
  chain: Chain;
  unreadable_lines: number;
}

/** What comes back from booking a batch: the row, plus what happened on the
    stock log if the page asked for a delivery to be booked in. */
export interface Booked extends Batch {
  chain_head: string;
  stock_in_requested: boolean;
  stock_in_error: string | null;
  stock_figure_needs_recount: boolean;
  detail: string;
}

export interface WrittenOff extends Batch {
  written_off_now: number;
  written_off_value_paise: number | null;
  written_off_value_rupees: string | null;
  chain_head: string;
  stock_movement_id: string | null;
  stock_recorded: boolean;
  stock_error: string | null;
  /** True when the stock log was NOT written: the Stock screen's figure has not
      moved and the shelf needs counting. The page says so, verbatim. */
  stock_figure_needs_recount: boolean;
  detail: string;
}

export interface Sold extends Batch {
  sold_now: number;
  chain_head: string;
  stock_recorded: false;
  stock_figure_needs_recount: false;
  detail: string;
}

/* ---------------------------------------------------------------- requests -- */

export const overview = (days?: number) =>
  send<Overview>(`/expiry${days === undefined ? '' : `?days=${days}`}`);

export const batches = (opts: { sku?: string | null; includeClosed?: boolean } = {}) => {
  const q = new URLSearchParams();
  if (opts.sku) q.set('sku', opts.sku);
  if (opts.includeClosed) q.set('include_closed', '1');
  const qs = q.toString();
  return send<BatchesBody>(`/expiry/batches${qs ? `?${qs}` : ''}`);
};

/**
 * Book a batch. Units, a date, a product — and nothing about money.
 *
 * `stockIn` also books the units as a delivery on the stock log through
 * stock.py's own writer. Off unless the person ticks it, because a delivery
 * already recorded on the Stock screen and booked again here is twice the
 * packets on the figure, and a page cannot tell which of the two was done.
 */
export const book = (
  skuId: string,
  units: number,
  expiresOn: string,
  opts: { note?: string; stockIn?: boolean } = {},
) =>
  send<Booked>('/expiry/batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      sku_id: skuId,
      units,
      expires_on: expiresOn,
      ...(opts.note ? { note: opts.note } : {}),
      ...(opts.stockIn ? { stock_in: true } : {}),
    }),
  });

/**
 * It went off. `units` defaults to everything left on the batch. The server
 * writes the stock OUT (reason `expiry`) through the stock module's own path
 * and reports whether it landed; the page repeats that verbatim.
 */
export const writeOff = (batchId: string, opts: { units?: number; note?: string } = {}) =>
  send<WrittenOff>(`/expiry/batch/${encodeURIComponent(batchId)}/write-off`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...(opts.units !== undefined ? { units: opts.units } : {}),
      ...(opts.note ? { note: opts.note } : {}),
    }),
  });

/** It sold through before the date. Takes units off the batch only: the sales
    are already on the audit chain, and no stock line is written. */
export const sold = (batchId: string, opts: { units?: number; note?: string } = {}) =>
  send<Sold>(`/expiry/batch/${encodeURIComponent(batchId)}/sold`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...(opts.units !== undefined ? { units: opts.units } : {}),
      ...(opts.note ? { note: opts.note } : {}),
    }),
  });

/* ------------------------------------------------------------------ shared -- */

/** A calendar date as a shopkeeper reads one: "15 Sep 2026". */
export function onDay(iso: string | null | undefined): string {
  if (!iso) return '—';
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  // Built from the parts, not from `new Date(iso)`: a bare date parses as UTC
  // midnight and prints as the day before in every timezone west of it.
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

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
 * The server's `days_left`, in words. The NUMBER is the server's; this only
 * chooses the phrase, so "yesterday" and "in 2 days" are never off by one from
 * the figure the list is sorted on.
 */
export function daysWord(daysLeft: number): string {
  if (daysLeft < -1) return `${-daysLeft} days ago`;
  if (daysLeft === -1) return 'yesterday';
  if (daysLeft === 0) return 'today';
  if (daysLeft === 1) return 'tomorrow';
  return `in ${daysLeft} days`;
}

/**
 * A whole number of packets typed by a person, or a refusal in the same shape
 * the server uses. Here so a typo answers instantly at the crate instead of
 * after a round trip; it does not replace the server's checks, which refuse
 * 2.5, 2.0, a string, a bool and anything over the cap by five names.
 */
export function packets(text: string, field: string): { units: number } | { reason: string; detail: string } {
  const t = text.trim();
  if (!t) {
    return { reason: `${field} is empty`, detail: 'Type a whole number of packets. Nothing has been recorded.' };
  }
  if (!/^\d+$/.test(t)) {
    return {
      reason: `“${t}” is not a whole number of packets`,
      detail: 'Packets are counted, so a decimal, a minus sign or a word cannot be recorded.',
    };
  }
  return { units: Number(t) };
}

/** YYYY-MM-DD, as a date input yields it, or a refusal. The server re-checks
    the calendar and the plausible range; this only stops an empty field. */
export function dateField(text: string): { date: string } | { reason: string; detail: string } {
  const t = text.trim();
  if (!t) {
    return { reason: 'The date is empty', detail: 'Type the date printed on the packet. Nothing has been recorded.' };
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(t)) {
    return { reason: `“${t}” is not a date`, detail: 'The date is year-month-day, as the picker gives it.' };
  }
  return { date: t };
}
