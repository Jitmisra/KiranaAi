/**
 * Every request the Purchase order screen makes, in one place, with types.
 *
 * WHAT THIS FILE MAY SEND IS TWO THINGS: which supplier to order from, and
 * which of that supplier's lines to leave out. That is the whole of the
 * browser's authority here.
 *
 *  1. THE BROWSER IS NEVER AN AUTHOR. How many packets to order is the
 *     shopkeeper's reorder level minus what stock.py says is on the shelf, and
 *     every rupee is the last cost purchases.py recorded — both derived on the
 *     server, on every request. `gawaah/po.py` REFUSES BY NAME a body carrying
 *     `units`, `qty`, `cost_paise` or any other quantity or price, so there is
 *     deliberately no way to send one from here: `confirm()` builds its own
 *     body and takes no such argument.
 *  2. NO ARITHMETIC IN THIS FILE. Not a sum, not a multiplication, not a
 *     division. `expected_paise` and every `line_paise` arrive computed. A
 *     total added up in a browser is a total the server never agreed to, and
 *     `0.1 + 0.2 !== 0.3` is a bad way to find that out at a counter.
 *  3. A REFUSAL IS A RESULT, NOT AN ERROR. `/po/*` answers `{ok:false, reason,
 *     detail}` with a 400 (404 for an id that does not exist) on purpose, so
 *     the body is parsed on non-2xx rather than thrown, and only a transport
 *     failure produces a refusal about the network.
 *
 * MONEY IS `number` HERE AND IT IS INTEGER PAISE. It is never divided by a
 * hundred: the server sends `*_rupees` as a STRING alongside every `*_paise`,
 * and that string is what is rendered. A `null` where a rupee figure would go
 * means the cost is not known — it does not mean nought, and `rupees()` below
 * exists so no caller can accidentally print one as the other.
 *
 * `send` is duplicated from `api.ts` rather than imported: it is module-private
 * there, and a new screen must not widen the till's own request layer to borrow
 * one function. The precedence rule it encodes — explicit `ok`, then an `error`
 * string, then the HTTP STATUS — is load-bearing and copied intact, because
 * FastAPI's own 422 comes back as `{"detail": …}` with neither field and a rule
 * that only read the body would file a validation crash as a success.
 *
 * NOTE ON DEV: `vite.config.ts` proxies an explicit allowlist of path prefixes
 * to the till on :8790. `/po` has to be in that list, and `po.router` has to be
 * mounted on the till, or every request here 404s under `npm run dev`. Neither
 * file is in this screen's scope; see the report.
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

/* ------------------------------------------------------------------ shapes -- */

/** The state of the chain the confirmed orders are recorded on. */
export interface Chain {
  exists: boolean;
  ok: boolean;
  lines: number;
  head: string | null;
  error: string | null;
  path: string;
}

/**
 * One product on an order.
 *
 * `cost_known: false` is the honest case and it is not rare: a product this
 * shop has never recorded buying has no cost, so `cost_paise`, `line_paise` and
 * both rupee strings are null. NOT ZERO. Every renderer of this type has to
 * handle the null, which is why the fields are typed nullable rather than
 * defaulted to 0 somewhere convenient.
 */
export interface PoLine {
  sku_id: string;
  name: string | null;
  /** What the shelf holds, on stock.py's figures. Null where nothing is counted. */
  on_hand_units: number | null;
  reorder_level: number | null;
  /** The level minus the shelf, floored at nought. Derived on the server. */
  units_to_order: number;
  days_of_cover: number | null;
  cost_known: boolean;
  cost_paise: number | null;
  cost_rupees: string | null;
  cost_recorded_on: string | null;
  cost_from: { purchase_id: string; invoice_no: string | null } | null;
  line_paise: number | null;
  line_rupees: string | null;
  why_no_cost: string | null;
}

/** One supplier's worth of an order, or — with a null id — the products that
    belong to nobody yet and therefore cannot be ordered from this screen. */
export interface PoGroup {
  supplier_id: string | null;
  supplier_name: string | null;
  supplier_phone: string | null;
  supplier_on_file: boolean;
  can_confirm: boolean;
  why_not: string | null;
  lines: PoLine[];
  line_count: number;
  units_total: number;
  expected_paise: number | null;
  expected_rupees: string | null;
  expected_is_partial: boolean;
  lines_priced: number;
  lines_with_no_cost: number;
  expected_note: string;
}

/** A product that is low but has nothing to order: the shelf is exactly at the
    level the shopkeeper set, so the shortfall is nought. */
export interface AtLevel {
  sku_id: string;
  name: string | null;
  on_hand_units: number | null;
  reorder_level: number | null;
  why: string;
}

/** A product with a level and no count. Whether it is low cannot be said. */
export interface Uncounted {
  sku_id: string;
  name: string | null;
  reorder_level: number | null;
  why: string;
}

export interface Draft {
  count: number;
  orderable_groups: number;
  lines_total: number;
  groups: PoGroup[];
  at_level_nothing_to_order: AtLevel[];
  level_set_but_never_counted: Uncounted[];
  needs_recount: Array<{
    sku_id: string;
    name: string | null;
    on_hand_units: number | null;
    reorder_level: number | null;
  }>;
  skus_with_a_level: number | null;
  skus_without_a_level: number | null;
  chain: Chain;
  stock_chain?: Chain;
  note: string;
  now: string | null;
}

/** A confirmed order, as it is stored. */
export interface Po {
  po_id: string;
  at: string;
  date: string;
  supplier_id: string;
  supplier_name: string | null;
  supplier_phone: string | null;
  supplier_on_file: boolean;
  shop_name: string | null;
  shop_address: string | null;
  shop_phone: string | null;
  lines: PoLine[];
  line_count: number;
  units_total: number;
  note: string | null;
  /** Always false. Confirming an order does not receive stock. */
  stock_received: boolean;
  expected_paise: number | null;
  expected_rupees: string | null;
  expected_is_partial: boolean;
  lines_priced: number;
  lines_with_no_cost: number;
  expected_note: string;
  chain_head?: string;
}

export interface Confirmed {
  po: Po;
  /** Plain text for a phone. No link, no payable string — see `gawaah/po.py`. */
  share_text: string;
  print_url: string;
  print_html: string;
  chain: Chain;
  stock_received: boolean;
  detail: string;
}

export interface PoRow {
  po_id: string;
  at: string;
  date: string;
  supplier_id: string;
  supplier_name: string | null;
  line_count: number;
  units_total: number;
  expected_paise: number | null;
  expected_rupees: string | null;
  expected_is_partial: boolean;
  lines_with_no_cost: number;
  stock_received: boolean;
  chain_head: string | null;
}

export interface PoList {
  count: number;
  orders: PoRow[];
  chain: Chain;
  note: string;
}

export interface OnePo {
  po: Po;
  share_text: string;
  print_url: string;
  chain: Chain;
  stock_received: boolean;
}

/* ------------------------------------------------------------------ calls -- */

/** What is running out, grouped by who it is bought from. Saves nothing. */
export const draft = () => send<Draft>('/po/draft');

/**
 * Write one supplier's order down.
 *
 * Takes NO quantity and NO price, deliberately: both are derived on the server
 * from the shelf and the purchase book at the moment this is called, so an
 * order confirmed off a screen that has been open all morning is an order for
 * what the shelf needs now. `skus` leaves lines out; it cannot add one.
 */
export const confirm = (supplierId: string, skus?: string[], note?: string) =>
  send<Confirmed>('/po/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      supplier_id: supplierId,
      ...(skus ? { skus } : {}),
      ...(note ? { note } : {}),
    }),
  });

/** Every order this counter has written, newest first. */
export const list = () => send<PoList>('/po');

/** One order in full, with the message that can be sent for it. */
export const one = (poId: string) => send<OnePo>(`/po/${encodeURIComponent(poId)}`);

/* ----------------------------------------------------------------- shared -- */

/**
 * A rupee figure as the server sent it, or the word for not knowing.
 *
 * THE WHOLE POINT OF THIS FUNCTION IS THE NULL BRANCH. A missing cost rendered
 * as "₹0.00" is a purchase order that says four packets of soap are free, and
 * it is the exact failure `gawaah/purchases.py` and `gawaah/po.py` are built to
 * avoid. Nothing is computed here: the string is the server's own.
 */
export function rupees(value: string | null | undefined): string {
  return value ? `₹${value}` : 'unknown';
}

/** A timestamp as a shopkeeper reads a clock, not as the ledger writes one. */
export function when(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('en-IN', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: true,
  });
}

/**
 * The day and the clock, separately.
 *
 * `when()` in one table cell wrapped to four lines at 390 px — "02 / Sept, /
 * 06:25 / pm" — which is not a date, it is a column of words. These two let the
 * cell set the day on one line and the time under it, deliberately.
 */
export function day(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' });
}

export function clock(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true });
}

/**
 * Copy the order's message to the clipboard, and say whether it worked.
 *
 * `navigator.clipboard` is unavailable on a page served over plain HTTP to
 * anything but localhost — which is exactly how a shopkeeper reaches this
 * counter from the phone in his hand — so the failure is reported rather than
 * swallowed, and the screen leaves the text on display to be selected by hand.
 */
export async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
