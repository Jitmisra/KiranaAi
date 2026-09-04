/**
 * Every request the Khata screen and the till's ON THE BOOK make, in one
 * place, with types.
 *
 * Three rules this module keeps, the same three loyaltyapi.ts keeps:
 *
 *  1. THE BROWSER IS NEVER AN AUTHOR. `book()` sends a session id, a phone, a
 *     name, the basket total and the witness's scan id — and the money
 *     service re-derives the amount from the witness before it agrees.
 *     `collect()` sends nothing but a household id: the balance the link is
 *     minted for is the money service's own figure. There is no function
 *     here that adds, subtracts or nets a rupee.
 *  2. A REFUSAL IS A RESULT. `/khata/*` answers `{ok:false, reason, detail}`
 *     with a 4xx on purpose — a second COLLECT while one is open is the
 *     product working — so the body is parsed on non-2xx rather than thrown.
 *  3. EVERY RUPEE IS THE SERVER'S. Figures arrive as integer paise beside a
 *     rupee string and are displayed as they came.
 */

import type { Result } from './api';
import type { Paise } from './money';

async function send<T>(url: string, init?: RequestInit): Promise<Result<T>> {
  let res: Response;
  try {
    res = await fetch(url, { cache: 'no-store', ...init });
  } catch (e) {
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

/* ------------------------------------------------------------- the shape -- */

/** The one sentence a judge hears, as the server's integers. */
export interface ValueLine {
  outstanding_paise: Paise;
  outstanding_rupees: string;
  households: number;
  households_total: number;
  oldest_days: number;
  collected_this_month_paise: Paise;
  collected_this_month_rupees: string;
  reminder_links_this_month: number;
  parked_paise: Paise;
  parked_rupees: string;
  links_open: number;
}

/** One signed webhook's worth of paise, keyed on the signed event id. */
export interface CaptureEntry {
  kind: 'capture';
  event_id: string | null;
  collection_id: string | null;
  payment_id: string | null;
  amount_paise: Paise;
  amount_rupees: string;
  credited: boolean;
  parked: boolean;
  reason: string | null;
  razorpay_event: string | null;
  final: boolean;
  outstanding_after_paise: Paise | null;
  at: string | null;
}

/** One Payment Link's life, as the kernel's last line about it. */
export interface CollectionEntry {
  kind: 'collection';
  collection_id: string;
  state: string;
  amount_paise: Paise | null;
  amount_rupees: string | null;
  captured_paise: Paise;
  captured_rupees: string;
  still_due_paise?: Paise | null;
  still_due_rupees?: string | null;
  short_url: string | null;
  payment_link_id: string | null;
  expire_by: number | null;
  needs_human: boolean;
  reason: string | null;
  opened_at: string | null;
  at: string | null;
}

export interface BillEntry {
  kind: 'bill';
  session_id: string | null;
  nonce: string | null;
  amount_paise: Paise;
  amount_rupees: string;
  at: string | null;
}

export type LedgerEntry = BillEntry | CaptureEntry | CollectionEntry;

export interface Household {
  book_id: string;
  name: string;
  phone: string;
  phone_masked: string;
  phone_tail: string;
  names_seen: string[];
  opened_at: string | null;
  unnamed?: boolean;
  bills: number;
  booked_paise: Paise;
  booked_rupees: string;
  captured_paise: Paise;
  captured_rupees: string;
  parked_paise: Paise;
  parked_rupees: string;
  outstanding_paise: Paise;
  outstanding_rupees: string;
  oldest_at: string | null;
  oldest_days: number | null;
  oldest_by: string;
  last_capture: CaptureEntry | null;
  last_booked_at: string | null;
  live_collection: CollectionEntry | null;
  collections: number;
  collected_this_month_paise: Paise;
  collected_this_month_rupees: string;
  reminder_links_this_month: number;
  needs_human: boolean;
}

export interface Chain {
  ok: boolean;
  exists: boolean;
  lines_verified: number;
  error: string | null;
  path: string | null;
}

export interface Book {
  value: ValueLine;
  households: Household[];
  count: number;
  truncated: boolean;
  chain: Chain;
  note: string;
}

export interface HouseholdDetail extends Household {
  entries: LedgerEntry[];
  collections_detail: CollectionEntry[];
  qr_url?: string;
  chain: Chain;
  note: string;
}

export interface Booked {
  booked: true;
  book_id: string;
  name: string;
  phone: string;
  phone_masked: string;
  new_household: boolean;
  session_id: string;
  nonce: string | null;
  state: string;
  amount_paise: Paise;
  amount_rupees: string;
  outstanding_paise: Paise | null;
  outstanding_rupees: string | null;
  replayed: boolean;
  audited: boolean;
  colour: 'none';
  note: string;
}

export interface Collected {
  collection_id: string;
  book_id: string;
  state: string;
  amount_paise: Paise;
  amount_rupees: string;
  captured_paise: Paise;
  captured_rupees: string;
  still_due_paise: Paise;
  still_due_rupees: string;
  payment_link_id: string | null;
  short_url: string | null;
  expire_by: number | null;
  first_min_partial_amount: Paise | null;
  reminder_enable: boolean;
  accept_partial: boolean;
  name: string | null;
  phone_masked: string | null;
  qr_url: string;
  audited: boolean;
  note: string;
}

export interface SimPaid {
  simulated: true;
  collection_id: string;
  link_status: string | null;
  amount_paid: Paise | null;
  amount: Paise | null;
  webhooks: Array<{
    event: string; status: number; green: boolean; reason: string;
    collection: { credited?: boolean; replayed?: boolean; reason?: string;
      capture_reason?: string | null; outstanding_rupees?: string } | null;
  }>;
}

export interface Lookup {
  asked_for: string;
  matches: Household[];
  count: number;
  matched_on: 'phone' | 'name';
}

/* -------------------------------------------------------------- requests -- */

export const book = () => send<Book>('/khata');
export const household = (bookId: string) => send<HouseholdDetail>(`/khata/${encodeURIComponent(bookId)}`);
export const lookup = (q: string) => send<Lookup>(`/khata/lookup?q=${encodeURIComponent(q)}`);

/** ON THE BOOK. The five fields the server re-derives against; none of them a price. */
export const bookBill = (body: {
  session_id: string; phone: string; name: string; amount_paise: Paise; scan_id: string;
}) => send<Booked>('/khata/book', postJson(body));

/** COLLECT. A household id and nothing else; the balance is the server's. */
export const collect = (bookId: string) =>
  send<Collected>(`/khata/${encodeURIComponent(bookId)}/collect`, { method: 'POST' });

/** Simulator only. The server refuses it by name on the live gateway. */
export const simPay = (collectionId: string, amountPaise?: Paise) =>
  send<SimPaid>('/khata/sim/pay', postJson(
    amountPaise === undefined ? { collection_id: collectionId }
      : { collection_id: collectionId, amount_paise: amountPaise }));

/** The QR is a RENDER OF THE GATEWAY'S OWN LINK. Never a payload we built. */
export const qrUrl = (bookId: string, collectionId: string) =>
  `/khata/${encodeURIComponent(bookId)}/qr/${encodeURIComponent(collectionId)}`;
