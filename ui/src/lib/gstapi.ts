/**
 * Every request the GST screen makes, in one place, with types.
 *
 * Three rules this module exists to keep:
 *
 *  1. THE BROWSER IS NEVER AN AUTHOR OF TAX. Nothing here computes a taxable
 *     value, a CGST or an SGST figure. The page sends an HSN heading and a
 *     whole-number slab a person chose; the server puts them against the
 *     lines it already holds and does the arithmetic in integer paise with a
 *     stated rounding rule. Every figure this screen shows came back from
 *     `/gst/*` as an integer, and `money()` only formats it.
 *  2. A REFUSAL IS A RESULT, NOT AN ERROR. `/gst/*` answers `{ok:false,
 *     reason, detail}` with a 400 on purpose (404 for a product or bill that
 *     does not exist), so the body is parsed on non-2xx rather than thrown,
 *     and only a transport failure produces a refusal about the network.
 *  3. WHAT THIS IS NOT. `is_filing` is false on every response and the type
 *     says so. There is no request here that files a return, mints an
 *     e-invoice, or asks anything of the government. The CSV is a file for
 *     whoever does.
 *
 * `send` is duplicated from `api.ts` rather than imported: it is
 * module-private there, and a new screen must not widen the till's own
 * request layer to borrow one function. The precedence rule it encodes —
 * explicit `ok`, then an `error` string, then the HTTP STATUS — is
 * load-bearing and copied intact. FastAPI's own 422 comes back as
 * `{"detail": ...}` with neither `ok` nor `error`, and a rule that only read
 * the body would file a validation crash as a success.
 *
 * NOTE ON DEV: `vite.config.ts` proxies an explicit allowlist of path
 * prefixes to the till on :8790. `/gst` has to be in that list, and
 * `gawaah/gst.py` has to be mounted on the till, or every request here 404s.
 * The screen renders that 404 as a refusal rather than an empty page.
 */

import type { Result } from './api';
import { rupees } from './money';

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

/* ------------------------------------------------------------- the facts -- */

/** The slabs the server records. Read off `/gst/products`, never assumed:
    the server is the one place the set is defined, and the screen draws
    exactly the buttons it will accept. */
export type Slab = number;

/** How the rounding is done, in the server's own words. Shown, not paraphrased. */
export interface Rounding {
  prices_are: string;
  taxable_value: string;
  tax: string;
  split: string;
  per_line: string;
  never: string;
}

/** The state of the hash chain the bills were folded out of. `exists: false`
    is a counter with no bills yet, not a failure; `ok: false` is. */
export interface Chain {
  ok: boolean;
  exists: boolean;
  lines_verified: number;
  lines_readable: number;
  head: string;
  error: string | null;
  path: string;
}

/**
 * The tax inside one tax-inclusive price, as the server split it.
 *
 * Integer paise throughout, and `taxable + tax === price` by construction —
 * the server's rule, not this page's arithmetic. The rupee strings are the
 * same integers rendered server-side, so a column of them never disagrees
 * with the paise beside it.
 */
export interface Split {
  price_paise: number;
  taxable_paise: number;
  tax_paise: number;
  cgst_paise: number;
  sgst_paise: number;
  price_rupees: string;
  taxable_rupees: string;
  tax_rupees: string;
  cgst_rupees: string;
  sgst_rupees: string;
}

/** A split the server declined to make, with its reason. Shown beside the
    row, never hidden and never approximated. */
export interface SplitRefused {
  refused: string;
  detail: string;
}

/* ---------------------------------------------------------- the products -- */

/**
 * A proposal from the keyword table. `rate` is null when the table proposes
 * the HSN and NOT a rate — the server's author was not sure of the slab, and
 * the screen leaves that choice to the person rather than defaulting it.
 */
export interface Suggestion {
  label: string;
  hsn: string;
  rate: number | null;
  keyword: string;
  why: string;
}

export type Source = 'typed' | 'accepted_suggestion' | null;

export interface ProductRow {
  sku_id: string;
  name: string;
  price_paise: number | null;
  price_rupees: string | null;
  taught_with: string;
  set: boolean;
  hsn: string | null;
  rate: number | null;
  set_at: string | null;
  source: Source;
  /** The split at the shelf price, only when a rate is set. */
  at_marked_price: Split | SplitRefused | null;
  /** Only when NO rate is set, and only when the table matched. */
  suggestion: Suggestion | null;
}

export interface ProductsBody {
  items: ProductRow[];
  count: number;
  set_count: number;
  unset_count: number;
  proposed_count: number;
  set_but_not_in_catalogue: Array<{ sku_id: string; hsn: string; rate: number; set_at: string | null; source: Source }>;
  /** Rows of the sidecar the server refused to read. Named, never coerced. */
  problems: string[];
  slabs: Slab[];
  rounding: Rounding;
  note: string;
  schedule_note: string;
}

export interface SetBody {
  changed: boolean;
  audited: boolean | null;
  product: ProductRow;
  previous?: { hsn: string; rate: number } | null;
  detail: string;
}

export interface ClearBody {
  sku_id: string;
  cleared: boolean;
  previous: { hsn: string; rate: number };
  audited: boolean;
  detail: string;
}

export interface HealthBody {
  is_filing: false;
  produces: string;
  does_not: string[];
  note: string;
  slabs: Slab[];
  slab_limit: string;
  rounding: Rounding;
  prices_are_tax_inclusive: boolean;
  sidecar: string;
  owns_catalog_json: boolean;
  audit: string;
  rates_set: number;
  problems: string[];
  suggester: string;
}

export interface RulesBody {
  rules: Array<{ label: string; hsn: string; rate: number | null; keywords: string[] }>;
  count: number;
  matching: string;
  schedule_note: string;
  slabs: Slab[];
}

/* -------------------------------------------------------------- the bills -- */

/** One slab's row of the B2C table. `bills` is present on the month, absent
    on a single bill. */
export interface RateRow {
  rate: number;
  lines: number;
  bills?: number;
  gross_paise: number;
  taxable_paise: number;
  tax_paise: number;
  cgst_paise: number;
  sgst_paise: number;
  gross_rupees: string;
  taxable_rupees: string;
  tax_rupees: string;
  cgst_rupees: string;
  sgst_rupees: string;
}

/** The totals across every rated line. Same shape as a row, without the rate. */
export type RatedTotals = Omit<RateRow, 'rate' | 'bills'>;

export interface BillLine extends Split {
  item_id: string;
  sku_id: string;
  name: string | null;
  at: string | null;
  hsn: string;
  rate: number;
}

/** A priced line whose product has no rate. Its money is here; no tax is,
    because none was worked out — that is the design, not a gap. */
export interface UnratedLine {
  item_id: string;
  sku_id: string;
  name: string | null;
  at: string | null;
  price_paise: number;
  price_rupees: string;
}

/** An amber line: the counter refused to price it, so there is no money to
    split and nothing here invents any. */
export interface ExcludedLine {
  item_id: string;
  sku_id: string;
  name: string | null;
  reason: string | null;
}

export interface BillBody {
  is_filing: false;
  is_invoice: false;
  session_id: string;
  at: string | null;
  settled: boolean;
  settled_at: string | null;
  total_paise: number;
  total_rupees: string;
  lines_sum_paise: number;
  total_agrees: boolean;
  lines: BillLine[];
  by_rate: RateRow[];
  rated: RatedTotals;
  unrated: UnratedLine[];
  unrated_paise: number;
  unrated_rupees: string;
  excluded: ExcludedLine[];
  unreadable_lines: number;
  complete: boolean;
  rounding: Rounding;
  problems: string[];
  chain: Chain;
  note: string;
}

export type Basis = 'closed' | 'settled';

export interface MonthException {
  session_id: string;
  at: string | null;
  settled: boolean;
  total_paise: number;
  total_rupees: string;
  unrated_lines: Array<{ sku_id: string; name: string | null; price_paise: number; price_rupees: string }>;
  unrated_paise: number;
  unrated_rupees: string;
}

export interface MonthBody {
  is_filing: false;
  month: string;
  basis: Basis;
  window: { start: string; end: string; timezone: string; note: string };
  shape: string;
  rows: RateRow[];
  rated: RatedTotals;
  unrated: {
    lines: number;
    bills: number;
    gross_paise: number;
    gross_rupees: string;
    by_sku: Array<{ sku_id: string; name: string | null; in_catalogue: boolean; lines: number; gross_paise: number; gross_rupees: string }>;
  };
  gross_paise: number;
  gross_rupees: string;
  bills: number;
  bills_closed_in_month: number;
  bills_settled_in_month: number;
  excluded_amber_lines: number;
  unreadable_lines: number;
  undated_bills: number;
  exceptions: MonthException[];
  /** False while any bill in the month has an unrated line, an unreadable
      price, or sits on a chain that does not verify. */
  complete: boolean;
  months_with_bills: string[];
  rounding: Rounding;
  slab_limit: string;
  storefront_note: string;
  problems: string[];
  chain: Chain;
  csv_url: string;
  note: string;
}

/* --------------------------------------------------------------- requests -- */

export const health = () => send<HealthBody>('/gst/health');

export const rules = () => send<RulesBody>('/gst/rules');

export const products = () => send<ProductsBody>('/gst/products');

/**
 * Record a person's decision for one product.
 *
 * `hsn` goes up as TEXT — a leading zero is part of the code (milk is 0401)
 * and a number would lose it. `rate` is a whole number the server checks
 * against its own slab set; the page does not decide what a slab is.
 * `accepted` marks that the values came from the suggester, so a later reader
 * can tell which rows were thought about and which were waved through.
 */
export const setRate = (skuId: string, hsn: string, rate: number, accepted: boolean) =>
  send<SetBody>(`/gst/products/${encodeURIComponent(skuId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ hsn, rate, accepted_suggestion: accepted }),
  });

export const clearRate = (skuId: string) =>
  send<ClearBody>(`/gst/products/${encodeURIComponent(skuId)}`, { method: 'DELETE' });

export const bill = (sessionId: string) =>
  send<BillBody>(`/gst/bill/${encodeURIComponent(sessionId)}`);

export const month = (label?: string, basis?: Basis) => {
  const q = new URLSearchParams();
  if (label) q.set('month', label);
  if (basis) q.set('basis', basis);
  const qs = q.toString();
  return send<MonthBody>(`/gst/month${qs ? `?${qs}` : ''}`);
};

/** The file. A plain link the browser follows; nothing is built client-side. */
export const csvUrl = (label: string, basis: Basis) =>
  `/gst/month.csv?month=${encodeURIComponent(label)}&basis=${encodeURIComponent(basis)}`;

/* ----------------------------------------------------------------- shared -- */

/**
 * Format paise for this screen, without ever throwing and without ever
 * quietly repairing a number.
 *
 * `rupees()` refuses a float, a negative and anything over ₹10,00,000, and it
 * is right to at a till. This screen is the shopkeeper's window onto a month
 * of bills, so a figure that fails that check is SHOWN — as the raw paise the
 * server sent, named as out of range — rather than blanking the page.
 */
export function money(paise: number | null | undefined): string {
  if (paise === null || paise === undefined) return '—';
  try {
    return rupees(paise);
  } catch {
    return `${paise} paise — outside the range this till will price`;
  }
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

/** This month, as this browser's clock reads it, in the YYYY-MM the server
    wants. Built from the date parts rather than `toISOString()`, which is UTC
    and would put a shop in Chennai on last month's page until half past five
    on the first. */
export function thisMonth(d: Date = new Date()): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

/** Step a YYYY-MM by whole months, without a date library. */
export function shiftMonth(label: string, months: number): string {
  const [y, m] = label.split('-').map((n) => Number(n));
  if (!y || !m) return label;
  const at = new Date(y, m - 1 + months, 1);
  return thisMonth(at);
}

/** A month as a shopkeeper says it: "August 2026". */
export function monthName(label: string): string {
  const [y, m] = label.split('-').map((n) => Number(n));
  if (!y || !m) return label;
  return new Date(y, m - 1, 1).toLocaleDateString('en-IN', { month: 'long', year: 'numeric' });
}

/** "5%" — a rate as a label. The number is the server's; this only appends the sign. */
export function pct(rate: number | null | undefined): string {
  return rate === null || rate === undefined ? '—' : `${rate}%`;
}
