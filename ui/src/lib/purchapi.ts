/**
 * Every request the Purchases screen makes, in one place, with types.
 *
 * This is the cost side of the counter. Everything else in this program knows
 * what a packet SELLS for; nothing until now knew what it cost, so nothing
 * could say what the shop earns. `gawaah/purchases.py` records the cost and
 * derives the margin; this module is the only thing the browser uses to reach
 * it.
 *
 * THREE RULES IT EXISTS TO KEEP
 *
 *  1. THE BROWSER IS NEVER AN AUTHOR OF MONEY. What the shopkeeper paid per
 *     unit is a fact off a piece of paper in his hand and it lives nowhere else
 *     in this program, so it goes up — as `cost_rupees`, a STRING, because a
 *     decimal sent as a JSON number is a float by the time it is parsed and
 *     `parseFloat('21.10') * 100` is 2109.9999999999998. Line totals and the
 *     invoice total are NOT sent as data. `assertions()` below computes them
 *     only so the server can COMPARE them against its own arithmetic and refuse
 *     on disagreement (`client_line_total_disagrees`, `client_total_disagrees`).
 *     What is stored is always the server's number. A screen showing one total
 *     while the book holds another is the failure that makes a book useless,
 *     and this is the cheapest way to make that impossible rather than
 *     unlikely.
 *
 *  2. INTEGER PAISE, NO FLOAT. `rupeesToPaise` is a character-by-character
 *     mirror of `gawaah/money.py::from_rupees_str` restricted to the shapes both
 *     sides parse identically. It divides nothing and multiplies only by 100. It
 *     returns null rather than guessing, and a null means no assertion is sent
 *     at all — the server then parses the shopkeeper's own text and refuses in
 *     its own words if it cannot.
 *
 *  3. A REFUSAL IS A RESULT, NOT AN ERROR. `/purchases/*` answers `{ok:false,
 *     reason, detail}` with a 400 on purpose — a margin it will not guess at is
 *     the product working. So the body is parsed on non-2xx rather than thrown,
 *     and only a transport failure produces a refusal about the network.
 *
 * THE UNKNOWN MARGIN. Where no cost has been recorded, every margin field is
 * `null` and `cost_known` is false. The types below spell that as `| null` on
 * purpose so the compiler will not let a screen quietly render it as 0: a
 * missing cost read as nought reports the shop making 100% on everything it has
 * never entered an invoice for, which is both wrong and flattering.
 *
 * `send` is duplicated from `api.ts` rather than imported — it is module-private
 * there, and a new screen must not widen the till's own request layer to borrow
 * one function. The precedence rule it encodes (explicit `ok`, then an `error`
 * string, then the HTTP STATUS) is load-bearing and copied intact: FastAPI's own
 * 422 comes back as `{"detail": ...}` with neither `ok` nor `error`, and a rule
 * that only read the body would file a validation crash as a success.
 *
 * NOTE ON MOUNTING: `gawaah/purchases.py` carries no prefix and its paths are
 * absolute, but at the time of writing it is not yet in `upload_app.py`'s
 * include list, and `/purchases` is not in the dev proxy allowlist in
 * `vite.config.ts`. Both are one line each and belong to the orchestrator; until
 * they land every call here answers 404, which this screen renders as a refusal
 * naming the missing mount rather than as a blank page.
 */

import type { Result } from './api';
import type { Paise } from './money';

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

const postJson = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

/* ------------------------------------------------------------- the chain -- */

/**
 * The state of the hash-chained audit log the day's revenue was counted off.
 *
 * Declared here rather than imported from `manageapi.ts`: the same seven fields
 * are on that module's own responses, but this screen must keep compiling while
 * that file is being edited by somebody else. `exists: false` is not a failure
 * — a counter installed this morning has no log. `ok: false` is.
 */
export interface Chain {
  ok: boolean;
  exists: boolean;
  lines_verified: number;
  lines_readable: number;
  head: string;
  error: string | null;
  path: string;
}

/* -------------------------------------------------------------- suppliers -- */

export interface Supplier {
  supplier_id: string;
  name: string;
  phone: string;
  notes: string;
  at: string | null;
  updated_at: string | null;
  /** Present on the list only: what has been bought from this supplier. */
  purchases?: number;
  bought_paise?: Paise;
  bought_rupees?: string;
}

export interface SupplierBook {
  count: number;
  suppliers: Supplier[];
  note: string;
}

export interface SupplierOne {
  supplier: Supplier;
  purchases: Purchase[];
  count: number;
  void_count: number;
  bought_paise: Paise;
  bought_rupees: string;
}

/* -------------------------------------------------------------- purchases -- */

/** One line of an invoice, as the SERVER wrote it down. Every figure is its. */
export interface PurchaseLine {
  sku_id: string;
  name: string;
  units: number;
  cost_paise: Paise;
  cost_rupees: string;
  line_paise: Paise;
  line_rupees: string;
}

export interface Purchase {
  purchase_id: string;
  at: string;
  /** The day the stock arrived, YYYY-MM-DD — the shopkeeper's word, not `at`. */
  date: string;
  supplier_id: string;
  /** Copied when the purchase was filed, so renaming a supplier changes no history. */
  supplier_name: string | null;
  invoice_no: string | null;
  lines: PurchaseLine[];
  units: number;
  total_paise: Paise;
  total_rupees: string;
  void: boolean;
  voided_at?: string;
  void_reason?: string;
}

export interface PurchaseBook {
  count: number;
  void_count: number;
  spent_paise: Paise;
  spent_rupees: string;
  purchases: Purchase[];
  note: string;
}

/* ----------------------------------------------------------------- margin -- */

/**
 * What one unit earns, or an honest statement that it is not known.
 *
 * `cost_known: false` means every number here is null. Both percentages name
 * their base because they are different numbers off the same two figures — 25
 * on a 100 sale is a 25% margin and a 33.3% markup — and they arrive as STRINGS
 * of integer tenths ("33.3"), floored, so a loss is never shown smaller than it
 * is and no float is ever in the room.
 */
export interface MarginBlock {
  cost_known: boolean;
  margin_paise: Paise | null;
  margin_rupees: string | null;
  margin_pct_of_price: string | null;
  markup_pct_of_cost: string | null;
  below_cost: boolean | null;
  note: string | null;
}

export interface CostRow {
  cost_paise: Paise;
  cost_rupees: string;
  units: number;
  date: string;
  at: string;
  purchase_id: string;
  supplier_id: string | null;
  supplier_name: string | null;
  invoice_no: string | null;
}

export interface MarginRow extends MarginBlock {
  sku_id: string;
  name: string;
  still_in_catalogue: boolean;
  sell_paise: Paise | null;
  sell_rupees: string | null;
  cost_paise: Paise | null;
  cost_rupees: string | null;
  cost_recorded_on: string | null;
  cost_from: {
    purchase_id: string | null;
    supplier_id: string | null;
    supplier_name: string | null;
    invoice_no: string | null;
  } | null;
  /** Only when an offer is running: the shelf-edge price the margin is NOT on. */
  marked_paise?: Paise;
  marked_rupees?: string;
  on_offer?: boolean;
}

export interface MarginBook {
  as_of: string;
  count: number;
  with_a_cost: number;
  without_a_cost: number;
  margin_known_for_every_product: boolean;
  unknown: string[];
  below_cost: string[];
  bought_but_not_in_the_catalogue: string[];
  items: MarginRow[];
  derived_from: string;
}

/** One product's share of a day: what it sold, and what that earned if known. */
export interface DayRow {
  sku_id: string;
  name: string;
  units: number;
  revenue_paise: Paise;
  revenue_rupees: string;
  still_in_catalogue: boolean;
  cost_known: boolean;
  cost_paise: Paise | null;
  cost_rupees?: string;
  cost_total_paise: Paise | null;
  cost_total_rupees?: string;
  cost_recorded_on?: string | null;
  margin_paise: Paise | null;
  margin_rupees: string | null;
  margin_pct_of_price: string | null;
  below_cost?: boolean;
  note?: string;
}

export interface MarginToday {
  date: string;
  bills: number;
  revenue_paise: Paise;
  revenue_rupees: string;
  /** Sales whose product has a recorded cost. Only these carry a margin. */
  covered: {
    skus: number;
    units: number;
    revenue_paise: Paise;
    revenue_rupees: string;
    cost_paise: Paise;
    cost_rupees: string;
    margin_paise: Paise;
    margin_rupees: string;
    margin_pct_of_price: string | null;
  };
  /** Sales whose product has none. Revenue reported, margin deliberately not. */
  uncovered: {
    skus: string[];
    units: number;
    revenue_paise: Paise;
    revenue_rupees: string;
  };
  margin_is_partial: boolean;
  lines_without_a_price: number;
  items: DayRow[];
  chain: Chain;
  derived_from: string;
}

export interface SkuCosts extends MarginRow {
  cost_history: CostRow[];
  times_bought: number;
  units_bought: number;
}

/* --------------------------------------------------------------- requests -- */

const day = (d?: string) => (d ? `?day=${encodeURIComponent(d)}` : '');

export const margin = (d?: string) => send<MarginBook>(`/purchases/margin${day(d)}`);
export const marginToday = (d?: string) => send<MarginToday>(`/purchases/margin/today${day(d)}`);

export const suppliers = () => send<SupplierBook>('/purchases/suppliers');
export const supplier = (id: string) =>
  send<SupplierOne>(`/purchases/suppliers/${encodeURIComponent(id)}`);

export const addSupplier = (body: { name: string; phone: string; notes?: string }) =>
  send<{ supplier: Supplier; audited: boolean }>('/purchases/suppliers', postJson(body));

export const editSupplier = (
  id: string,
  body: { name?: string; phone?: string; notes?: string },
) =>
  send<{ supplier: Supplier; audited: boolean; note: string }>(
    `/purchases/suppliers/${encodeURIComponent(id)}`,
    postJson(body),
  );

export const purchases = (supplierId?: string) =>
  send<PurchaseBook>(
    `/purchases${supplierId ? `?supplier_id=${encodeURIComponent(supplierId)}` : ''}`,
  );

export const purchase = (id: string) =>
  send<{ purchase: Purchase; lines_against_todays_prices: unknown[]; note: string }>(
    `/purchases/${encodeURIComponent(id)}`,
  );

export const sku = (id: string) => send<SkuCosts>(`/purchases/sku/${encodeURIComponent(id)}`);

/** What one line of the invoice claims. `cost_rupees` is the text as typed. */
export interface DraftLineBody {
  sku_id: string;
  units: number;
  cost_rupees: string;
  /** The client's own arithmetic, sent to be CHECKED. Omitted when unparseable. */
  line_paise?: Paise;
}

export const recordPurchase = (body: {
  supplier_id: string;
  lines: DraftLineBody[];
  date?: string;
  invoice_no?: string;
  total_paise?: Paise;
}) =>
  send<{ purchase: Purchase; audited: boolean; note: string }>('/purchases', postJson(body));

export const voidPurchase = (id: string, reason: string) =>
  send<{ purchase: Purchase; audited: boolean; note: string }>(
    `/purchases/${encodeURIComponent(id)}/void`,
    postJson({ reason }),
  );

/* ------------------------------------------------------------ arithmetic -- */

/**
 * A rupee STRING to integer paise, or null. No float, no division, no rounding.
 *
 * The accepted shape is a deliberate SUBSET of what `money.from_rupees_str`
 * takes: up to seven whole digits and at most two decimal places. The server
 * additionally accepts '.5' and '12.' and a leading minus; this rejects them,
 * and rejecting is safe in a way that accepting is not. A shape both sides
 * parse identically can be asserted; a shape only one side understands would
 * produce a `client_total_disagrees` refusal on a perfectly good invoice.
 *
 * `whole * 100 + frac` is exact for every value this counter will ever see, and
 * there is no `/ 100` anywhere in this file for the same reason there is none in
 * `money.py`: it is the single most likely place for a rounding error to enter a
 * book.
 */
export function rupeesToPaise(text: string): Paise | null {
  const m = /^(\d{1,7})(?:\.(\d{1,2}))?$/.exec(text.trim());
  if (!m) return null;
  const whole = Number(m[1] ?? '0');
  // '.5' means fifty paise, not five: pad on the RIGHT, exactly as the server does.
  const frac = Number(((m[2] ?? '') + '00').slice(0, 2));
  const p = whole * 100 + frac;
  return Number.isSafeInteger(p) ? p : null;
}

/** A whole count of packets, or null. No decimals: a shelf holds whole packets. */
export function unitsOf(text: string): number | null {
  const m = /^(\d{1,5})$/.exec(text.trim());
  if (!m) return null;
  const n = Number(m[1] ?? '0');
  return n > 0 ? n : null;
}

/** The largest figure this screen will assert. Beyond it, the server totals alone. */
const ASSERTION_CEILING = 100_000_000;

/**
 * What one line comes to: cost per unit x units, as integer paise, or null.
 *
 * Null when either box is unreadable or the product leaves the range this
 * counter will price — a page that rendered a figure past that would be
 * asserting something `money.ts` refuses to format. This is the ONLY place the
 * multiplication is written, so a line's own read-back and the invoice
 * assertion below cannot drift apart.
 */
export function linePaise(units: string, cost: string): Paise | null {
  const u = unitsOf(units);
  const c = rupeesToPaise(cost);
  if (u === null || c === null) return null;
  const p = c * u;
  return Number.isSafeInteger(p) && p <= ASSERTION_CEILING ? p : null;
}

export interface Assertions {
  /** One per line, in order. Every entry is present or the whole thing is null. */
  lines: Paise[];
  total: Paise;
}

/**
 * What the lines add up to, for the SERVER TO CHECK — never to store.
 *
 * Null when any line is unparseable or the sum leaves the range this counter
 * will price. Null means the request carries no assertion at all: the server
 * still totals it, and the screen simply says so rather than showing a figure
 * it could not derive honestly.
 */
export function assertions(
  lines: ReadonlyArray<{ units: string; cost: string }>,
): Assertions | null {
  const out: Paise[] = [];
  let total = 0;
  for (const l of lines) {
    const line = linePaise(l.units, l.cost);
    if (line === null) return null;
    out.push(line);
    total += line;
  }
  if (!out.length) return null;
  if (!Number.isSafeInteger(total) || total > ASSERTION_CEILING) return null;
  return { lines: out, total };
}

/**
 * Today, as this machine's calendar reads it.
 *
 * NOT `toISOString().slice(0, 10)`, which is UTC: at 1 a.m. in Delhi that names
 * yesterday, and the shopkeeper would be shown the wrong day's margin against a
 * server that windows on local midnight.
 */
export function todayLabel(d: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
