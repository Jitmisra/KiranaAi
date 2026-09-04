/**
 * MILAN — the requests the Close screen's MATCH section makes, with types.
 *
 * The same three rules as daybookapi.ts:
 *
 *  1. THE BROWSER IS NEVER AN AUTHOR OF MONEY. The one POST here carries a
 *     nonce — the id of an intent the counter minted — and nothing else. The
 *     amount the intent settles for is the kernel's own, checked by the
 *     kernel's reconcile path against the gateway's record. There is no
 *     figure in this file, and no arithmetic.
 *  2. A REFUSAL IS A RESULT. `/milan/*` answers `{ok:false, reason, detail}`
 *     with a 4xx on purpose, so the body is parsed on non-2xx.
 *  3. EVERY RUPEE IS THE SERVER'S. Integer paise beside a rupee string,
 *     displayed as they came.
 */

import type { Result } from './api';

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

/** A row the gateway's report carried and the chain matched. */
export interface MatchedRow {
  entity_id: string;
  type: string;
  settlement_id: string | null;
  settled_at: string | null;
  created_at: string | null;
  simulated: boolean;
  amount_paise: number;
  amount_rupees: string;
  credit_paise: number;
  debit_paise: number;
  fee_paise: number | null;
  tax_paise: number | null;
  session_id: string | null;
  nonce: string | null;
  bill_paise: number | null;
  bill_rupees: string | null;
  bill_at: string | null;
  /** `webhook` is a signature-verified settlement; `kernel` is the reconcile path. */
  settled_by: 'webhook' | 'kernel' | null;
  chain_settled_at: string | null;
  /** Present on the amount_mismatch bucket only. */
  needs_human?: boolean;
  difference_paise?: number | null;
}

/** THE FOUND MONEY: a row the gateway paid out that no bill on the chain settled. */
export interface FoundRow {
  entity_id: string;
  settlement_id: string | null;
  settled_at: string | null;
  created_at: string | null;
  simulated: boolean;
  amount_paise: number;
  amount_rupees: string;
  credit_paise: number;
  session_id: string | null;
  nonce: string | null;
  counter_state: string | null;
  counter_amount_paise: number | null;
  counter_payment_id: string | null;
  bill_on_chain: boolean;
  bill_at: string | null;
  bill_settled_on_chain: boolean;
  /** The kernel holds an open-ended row for this nonce with this amount. */
  settleable: boolean;
  needs_human: boolean;
}

/** A chain settlement the report did not carry. */
export interface ChainRow {
  session_id: string;
  payment_id: string | null;
  amount_paise: number;
  amount_rupees: string;
  settled_at: string | null;
  settled_by: 'webhook' | 'kernel' | null;
  bill_at: string | null;
  due_day: string | null;
  needs_human?: boolean;
}

export interface RefundRow {
  entity_id: string;
  settled_at: string | null;
  simulated: boolean;
  amount_paise: number;
  amount_rupees: string;
  debit_paise: number;
  payment_id: string | null;
  bill_session_id: string | null;
}

export interface UnreadableRow {
  entity_id: string | null;
  why: string;
  raw?: Record<string, unknown>;
}

export interface Bucket<R> {
  count: number;
  paise: number | null;
  rupees: string | null;
  rows: R[];
}

export interface Exceptions {
  settled_not_yet_in_recon: Bucket<ChainRow>;
  settled_not_in_recon: Bucket<ChainRow>;
  in_recon_not_on_chain: Bucket<FoundRow>;
  amount_mismatch: Bucket<MatchedRow>;
  refunds: Bucket<RefundRow>;
  adjustments: Bucket<MatchedRow>;
  unreadable_rows: Bucket<UnreadableRow>;
}

export interface Matched {
  count: number;
  gross_paise: number;
  gross_rupees: string;
  fee_paise: number;
  fee_rupees: string;
  tax_paise: number;
  tax_rupees: string;
  /** The gateway's own credit, summed. Not gross minus fee. */
  net_paise: number;
  net_rupees: string;
  deducted_paise: number;
  deducted_rupees: string;
  by_webhook: number;
  by_kernel: number;
  rows: MatchedRow[];
}

export interface MatchBody {
  day: string;
  settlement_cycle: string;
  counter_tz: string;
  mode: string | null;
  simulated: boolean;
  recon: { count: number; fetched_at: string | null; source: string | null; day: string | null };
  matched: Matched;
  exceptions: Exceptions;
  exception_count: number;
  earlier_days: { count: number; paise: number; rupees: string };
  value_line: string;
  chain: { ok: boolean; lines_verified: number };
  derived_from: string;
}

export interface SettleBody {
  nonce: string;
  session_id: string;
  amount_paise: number;
  amount_rupees: string;
  state_before: string;
  state: string;
  settled: boolean;
  payment_id: string | null;
  reason: string | null;
  needs_human: boolean;
  changed: boolean;
  minted: false;
  charged: false;
  how: string;
  audited: boolean;
}

export interface SimSettleBody {
  simulated: true;
  settlement_id: string | null;
  amount_settled: number;
  payments: number;
}

/* ---------------------------------------------------------- the requests -- */

/** The match for one settlement day. Omit the day for yesterday (T+1). */
export const match = (day?: string) =>
  send<MatchBody>(`/milan${day ? `?day=${encodeURIComponent(day)}` : ''}`);

/**
 * SETTLE FROM THE GATEWAY'S RECORD. A nonce, nothing else: the kernel's
 * reconcile path looks the link up and settles for exactly the intent's
 * amount, or parks it. Nothing here can mint or charge.
 */
export const settle = (nonce: string) =>
  send<SettleBody>('/milan/settle', postJson({ nonce }));

/** Simulator only: run the settlement batch now. Refused by name on the live gateway. */
export const simSettle = () => send<SimSettleBody>('/milan/sim/settle', postJson({}));

/** Yesterday, in the browser's own calendar, written the way the server writes a day. */
export function yesterdayLabel(): string {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/* ------------------------------------------------------ beside a closed day -- */

/**
 * What `GET /daybook/{day}` carries BESIDE a frozen record: the match for the
 * day those bills settle (T+1), counts and totals only, read just now. It is
 * never inside the record and never changes it.
 */
export interface BesideBody {
  settlement_day: string;
  settlement_cycle: string;
  simulated: boolean;
  matched: Omit<Matched, 'rows'>;
  exceptions: Record<string, { count: number; paise: number | null; rupees: string | null }>;
  exception_count: number;
  value_line: string;
  note: string;
}
