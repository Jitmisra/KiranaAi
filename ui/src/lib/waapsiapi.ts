/**
 * WAAPSI — a return by camera, refunded by Razorpay. Every request the return
 * screen makes, in one place, with types.
 *
 * The three rules khataapi.ts and loyaltyapi.ts keep, kept here too:
 *
 *  1. THE BROWSER IS NEVER AN AUTHOR OF MONEY. `scan()` sends pixels and gets
 *     back the bill the receipt QR names and the SKU(s) on the packet — no
 *     price it authored. `refund()` sends the bill, the line and the paise it
 *     BELIEVES were charged; the money service re-derives all three from the
 *     signed audit chain and refuses by name on any disagreement. There is no
 *     function here that nets, rounds, or invents a rupee.
 *  2. A REFUSAL IS A RESULT. `/api/money/refund` answers `{error, reason}` with
 *     a 4xx on purpose — item_not_on_this_bill, already_refunded — so the body
 *     is parsed on non-2xx rather than thrown.
 *  3. REFUNDED ONLY ON A SIGNED WEBHOOK. `refund()` gets a REQUESTED refund
 *     back; only the gateway's signed refund.processed turns it PROCESSED, and
 *     the page LEARNS that by polling, it never asserts it.
 */

import type { Paise } from './money';

export type Refusal = { ok: false; reason: string; detail?: string; extra?: Record<string, unknown> };

async function send<T>(url: string, init?: RequestInit): Promise<({ ok: true } & T) | Refusal> {
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
  // Two conventions: the till answers {ok}, paisa answers a payload with no
  // `ok` on success and {error, detail} on a refusal. Precedence: explicit
  // `ok`, then `error`, then the HTTP status.
  if (body && body.ok === undefined) {
    if (typeof body.error === 'string') {
      const { error, detail, ...rest } = body;
      return { ok: false, reason: error, detail: String(detail ?? ''), extra: rest };
    }
    if (!res.ok) {
      return {
        ok: false,
        reason: `the server refused with HTTP ${res.status}`,
        detail: typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body),
      };
    }
    return { ...body, ok: true } as unknown as ({ ok: true } & T);
  }
  return body as unknown as ({ ok: true } & T);
}

const postJson = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

/* ------------------------------------------------------------- the shapes -- */

/** One decoded packet code, priced where taught. */
export interface ScanItem {
  code: string;
  format: string | null;
  box: number[] | null;
  read_by: string | null;
  sku_id: string | null;
  name: string | null;
  price_paise: Paise | null;
  price_rupees: string | null;
  reason: string;
}

/** What `/waapsi/scan` saw: the bill the receipt QR names, and the packet(s). */
export interface ReturnScan {
  frame_px: [number, number];
  codes_found: number;
  /** The bill this return is against, off THIS counter's own receipt QR. */
  receipt_session: string | null;
  receipt_payload: string | null;
  items: ScanItem[];
  other_codes: Array<{ code: string; reason: string; box: number[] | null }>;
  counts: { priced: number; amber: number };
  note: string;
}

/** One signed refund event, keyed on the event id inside the envelope. */
export interface RefundEvent {
  event_id: string;
  event: string | null;
  state: string;
  amount_paise: Paise | null;
  gateway_refund_id: string | null;
  reason: string | null;
  at: string | null;
}

/** One refund's whole life, as the kernel's rows report it. */
export interface Refund {
  refund_key: string;
  state: string;
  refunded: boolean;
  session_id: string;
  item_id: string;
  sku_id: string;
  amount_paise: Paise;
  amount_rupees: string;
  gateway_refund_id: string | null;
  needs_human: boolean;
  reason: string | null;
  requested_ts: string | null;
  processed_ts: string | null;
  bill_amount_paise: Paise | null;
  bill_amount_rupees: string | null;
  refunded_paise: Paise;
  refunded_rupees: string;
  committed_paise: Paise;
  events: RefundEvent[];
  now: string;
  gateway_status?: string;
  speed_requested?: string;
  speed_processed?: string | null;
}

/** One line of the receipt, folded per (sku, unit price), with the return
    state WAAPSI adds. `returnable_item_ids` are the packets on this line that
    have not already been refunded — one of them is what REFUND names. */
export interface ReceiptLine {
  sku_id: string;
  name: string;
  qty: number;
  unit_paise: Paise | null;
  unit_rupees: string | null;
  line_paise: Paise | null;
  line_rupees: string | null;
  priced: boolean;
  item_ids: string[];
  returnable_item_ids: string[];
  refunded_qty: number;
  refund_committed_qty: number;
}

/** The bill as the receipt derives it, with the return figures. */
export interface ReceiptBill {
  session_id: string;
  at: string | null;
  at_human: string | null;
  lines: ReceiptLine[];
  total_paise: Paise;
  total_rupees: string;
  settled: boolean;
  settled_by_verified_webhook: boolean;
  settled_at_human: string | null;
  payment_id: string | null;
  payment_headline: string;
  refunded_paise: Paise;
  refunded_rupees: string;
  refund_requested_paise: Paise;
  refund_requested_rupees: string;
  net_paise: Paise;
  net_rupees: string;
  refund_count: number;
}

/** Every refund on one bill, and where the bill's money stands. */
export interface BillRefunds {
  session_id: string;
  settled: boolean;
  payment_id: string | null;
  bill_amount_paise: Paise | null;
  bill_amount_rupees: string | null;
  refunded_paise: Paise;
  refunded_rupees: string;
  requested_paise: Paise;
  committed_paise: Paise;
  refunds: Refund[];
}

/** What the simulator's back office did to a refund. */
export interface SimRefund extends Refund {
  simulated: true;
  outcome: string;
  webhooks: Array<{
    event: string; status: number; green: boolean; reason: string;
    refund: { applied?: boolean; refunded?: boolean; refund_state?: string;
      event_reason?: string | null } | null;
  }>;
}

/* -------------------------------------------------------------- requests -- */

/** Hold up the packet AND the receipt QR: pixels in, a bill and SKUs out. */
export const scan = (blob: Blob) => {
  const fd = new FormData();
  fd.append('image', blob, 'return.jpg');
  return send<ReturnScan>('/waapsi/scan', { method: 'POST', body: fd });
};

/** REFUND one line. The three re-derived fields, none of them a price the
    browser owns — the amount is compared against the charged price, not used. */
export const refund = (body: {
  session_id: string; item_id: string; sku_id: string; amount_paise: Paise;
}) => send<Refund>('/api/money/refund', postJson(body));

/** The bill the receipt QR named, derived from the signed audit chain. This is
    how the page shows "settled via pay_… · Lifebuoy ₹31.50 (offer price at
    sale)" BEFORE anyone presses REFUND — every figure is the server's. */
export const bill = (sessionId: string) =>
  send<ReceiptBill>(`/receipt/${encodeURIComponent(sessionId)}`);

/** Read one refund back — the pay screen polls this to LEARN it processed. */
export const view = (refundKey: string) =>
  send<Refund>(`/api/money/refund/${encodeURIComponent(refundKey)}`);

/** Every refund on a bill, and its money state. */
export const forBill = (sessionId: string) =>
  send<BillRefunds>(`/api/money/refunds/${encodeURIComponent(sessionId)}`);

/** Simulator only. The server refuses it by name on the live gateway. */
export const simProcess = (refundKey: string, outcome: 'processed' | 'failed' = 'processed') =>
  send<SimRefund>('/api/money/sim/refund', postJson({ refund_key: refundKey, outcome }));
