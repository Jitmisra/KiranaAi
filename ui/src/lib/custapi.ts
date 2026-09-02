/**
 * GRAAHAK — every request the Customers screen makes, in one place, with types.
 *
 * The server module (`gawaah/customers.py`) derives a customer from the order
 * files on every request. There is no customers table, nothing is stored, and
 * nothing here writes: there is no POST, no PATCH and no DELETE in this file,
 * because there is none on the server either. A shopkeeper cannot add somebody
 * by hand and cannot correct them — the orders are the only truth and a second
 * one could disagree with them.
 *
 * Three rules this module keeps, the same three every other request layer here
 * keeps:
 *
 *  1. A REFUSAL IS A RESULT, NOT AN ERROR. `/customers/*` answers
 *     `{ok:false, reason, detail}` with a 400 (or a 404 for a number nobody has
 *     ordered on) deliberately, so the body is parsed on a non-2xx status
 *     instead of thrown, and the screen renders the server's own reason
 *     verbatim.
 *  2. THE BROWSER IS NEVER AN AUTHOR. Nothing here sends a name, a number, a
 *     price or a total. Every field below is read.
 *  3. NO ADDRESS IN A LIST. `CustomerSummary` has no address field because the
 *     server's summary dict has no address key — the split is enforced by the
 *     shape rather than by a template remembering to leave it out. Only
 *     `CustomerDetail`, fetched by naming ONE whole phone number, carries where
 *     somebody lives.
 *
 * `send` is copied from `lib/api.ts` rather than imported: it is module-private
 * there, and a screen must not widen the till's own request layer to borrow one
 * function. The precedence rule it encodes — an explicit `ok`, then an `error`
 * string, then the HTTP status — is load-bearing and is copied intact, because
 * FastAPI's own 422 arrives as `{"detail": ...}` with no `ok` at all and a rule
 * that read only the body would file a validation failure as a success.
 *
 * NOTE ON DEV: `vite.config.ts` proxies an explicit allowlist of path prefixes
 * to the till on :8790. `/customers` has to be in that list or every request
 * here 404s under `npm run dev` while working perfectly in the built site.
 */

import type { Refusal, Result } from './api';

async function send<T>(url: string): Promise<Result<T>> {
  let res: Response;
  try {
    res = await fetch(url, { cache: 'no-store' });
  } catch (e) {
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

export type { Refusal };

/* ------------------------------------------------------------- the shapes -- */

/** How the list may be ordered. The server refuses anything else by name. */
export type Sort = 'recent' | 'spend' | 'orders' | 'name';

/** The two ways of being a regular. They rarely name the same person. */
export type Ranking = 'spend' | 'frequency';

/** One line of one order, as a customer's history carries it. */
export interface HistoryLine {
  sku_id: string;
  name: string;
  /** Null when the order file held something that was not a whole count. */
  qty: number | null;
  unit_paise: number | null;
  unit_rupees: string | null;
  line_paise: number | null;
  line_rupees: string | null;
}

/**
 * One order on a customer's history.
 *
 * `priced: false` is an order whose total could not be read as whole paise and
 * whose lines could not be added either. It is still a visit — it counts in
 * `order_count` — and it is in NO rupee figure anywhere. The server abstains
 * rather than rounding, and this screen says so rather than showing a zero.
 */
export interface HistoryOrder {
  order_id: string;
  at: string;
  status: string;
  total_paise: number | null;
  total_rupees: string | null;
  priced: boolean;
  /** The gateway confirmed this one. Not "the shop thinks it was paid". */
  paid: boolean;
  line_count: number;
  lines: HistoryLine[];
}

/** An address this number has ordered to. DETAIL ONLY — never in a list. */
export interface CustomerAddress {
  address: string;
  orders: number;
  first_seen: string;
  last_seen: string;
}

/**
 * One customer as a LIST shows them. There is no address field here and there
 * is none on the server either — see the module docstring, rule 3.
 *
 * THREE MONEY FIGURES THAT ARE NEVER MERGED:
 *   total_paise     what they ASKED the shop for, cancelled orders excluded.
 *   paid_paise      the part a gateway webhook confirmed actually arrived.
 *   cancelled_paise kept apart, in neither of the other two, so a customer who
 *                   orders and cancels every week reads as exactly that.
 */
export interface CustomerSummary {
  phone: string;
  name: string;
  order_count: number;
  kept_count: number;
  cancelled_count: number;
  paid_count: number;
  unpriced_count: number;
  total_paise: number;
  total_rupees: string;
  paid_paise: number;
  paid_rupees: string;
  cancelled_paise: number;
  cancelled_rupees: string;
  first_order_at: string;
  last_order_at: string;
  last_status: string;
  /** Whole days from first order to last, or null if a timestamp was unclear. */
  days_known: number | null;
  address_count: number;
  names_seen_count: number;
}

/** One customer in full. The only shape in this program that has an address. */
export interface CustomerDetail extends CustomerSummary {
  addresses: CustomerAddress[];
  /** Every name this number has used, newest first. A recycled number shows. */
  names_seen: string[];
  /** The number as the customer themselves typed it on their newest order. */
  phone_as_given: string;
  orders: HistoryOrder[];
  note: string;
}

export interface CustomerList {
  customers: CustomerSummary[];
  /** How many are in this response. */
  count: number;
  /** How many matched the search — larger than `count` when capped. */
  matched: number;
  total_customers: number;
  orders_read: number;
  /**
   * Orders with no dialable number on them. Counted, never dropped: "you have
   * no customers" and "your orders have no phone numbers in them" are different
   * answers and this is what tells them apart.
   */
  orders_without_a_phone: number;
  limit: number;
  sort: Sort;
  sorts: Sort[];
  q: string;
  note: string;
}

export interface RegularsList {
  by: Ranking | null;
  rankings: Ranking[];
  limit: number;
  /**
   * The shop's own floor for calling somebody a regular, in KEPT orders. This
   * screen marks nobody until the server has said what the number is — a `2`
   * typed into the page would be a second definition of "regular" that could
   * quietly disagree with the server's.
   */
  min_orders_for_frequency: number;
  total_customers: number;
  orders_read: number;
  by_spend?: CustomerSummary[];
  by_frequency?: CustomerSummary[];
  note: string;
}

/* ------------------------------------------------------------- the calls -- */

/**
 * The list. `q` matches a name or any run of digits in the number, so "rekha"
 * and "4210" both work. Sent empty when the box is empty — the server treats a
 * blank search as "everybody", capped by `limit`.
 */
export function customers(q: string, sort: Sort, limit?: number): Promise<Result<CustomerList>> {
  const p = new URLSearchParams({ sort });
  if (q) p.set('q', q);
  if (limit !== undefined) p.set('limit', String(limit));
  return send<CustomerList>(`/customers?${p.toString()}`);
}

/** Both regulars lists at once: by what they spend, and by how often they come. */
export function regulars(limit?: number): Promise<Result<RegularsList>> {
  const p = new URLSearchParams();
  if (limit !== undefined) p.set('limit', String(limit));
  const qs = p.toString();
  return send<RegularsList>(qs ? `/customers/regulars?${qs}` : '/customers/regulars');
}

/**
 * ONE customer, by their whole number. This is the only call that returns an
 * address, and it is reached by naming the number of the person whose address
 * it is. A number nobody has ordered on is a 404 carrying
 * `no_customer_with_this_number` — a refusal, rendered as one.
 */
export function customer(phone: string): Promise<Result<CustomerDetail>> {
  return send<CustomerDetail>(`/customers/${encodeURIComponent(phone)}`);
}

/* ------------------------------------------------------------- rendering -- */

/**
 * A date, with its year.
 *
 * Deliberately not `manageapi.when()`, which drops the year because the screens
 * it serves are about today. A customer's history is the one place in this
 * program that spans years, and "12 Feb" against a first order is ambiguous the
 * moment a shop has been open longer than one.
 */
export function dayOf(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

/**
 * Ten digits as a person reads them out: `98765 43210`.
 *
 * Display only, and only for the shape this shop's numbers actually have. A
 * number of any other length is shown exactly as the server keyed it, because
 * grouping digits by a rule invented here would make an unfamiliar number look
 * like a familiar one. The `tel:` link always carries the raw digits.
 */
export function dialled(phone: string): string {
  return phone.length === 10 ? `${phone.slice(0, 5)} ${phone.slice(5)}` : phone;
}
