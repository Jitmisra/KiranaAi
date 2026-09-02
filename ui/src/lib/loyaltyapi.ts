/**
 * Every request the Loyalty screen makes, in one place, with types.
 *
 * Three rules this module exists to keep:
 *
 *  1. THE BROWSER IS NEVER AN AUTHOR. Nothing here sends a point balance, a
 *     discount, or a rupee. `setRules()` sends two whole numbers the shopkeeper
 *     chose; `redeem()` sends a phone and a count of points; `apply()` sends a
 *     session id. Every balance, every value in paise and every "why" on the
 *     screen was derived by the server from the audit chain and is displayed as
 *     it came. There is no function in this file that multiplies points by
 *     anything.
 *  2. A REFUSAL IS A RESULT, NOT AN ERROR. `/loyalty/*` answers `{ok:false,
 *     reason, detail}` with a 400 on purpose — a redemption past the balance is
 *     the product working — so the body is parsed on non-2xx rather than
 *     thrown, and only a transport failure produces a refusal about the network.
 *  3. WHAT THE PAGE CANNOT DO. It cannot make a redemption reach the gateway.
 *     The money service re-prices every basket from its own tables before it
 *     mints, and until it is taught to read a redemption id off the witness, a
 *     till that subtracts the discount is refused. The server says so in
 *     `till_must` on every proposal, and the screen prints it.
 *
 * `send` is duplicated from `api.ts` rather than imported: it is module-private
 * there, and a new screen must not widen the till's own request layer to
 * borrow one function. The precedence rule — explicit `ok`, then an `error`
 * string, then the HTTP STATUS — is load-bearing and copied intact.
 *
 * NOTE ON DEV: `vite.config.ts` proxies an explicit allowlist of path prefixes
 * to the till on :8790. `/loyalty` has to be in that list or every request
 * here 404s under `npm run dev` while working perfectly in the built site.
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

/* ------------------------------------------------------------- the shape -- */

/** The scheme. Both zero means off, and the server says so. */
export interface Rules {
  points_per_rupee: number;
  paise_per_point: number;
  set_at: string | null;
  on: boolean;
}

export interface RulesView {
  rules: Rules;
  /** What 100 points buy, computed server-side. Null while a point is worth nothing. */
  example: { points: number; value_paise: Paise; value_rupees: string } | null;
  history_count: number;
  limits: { max_points_per_rupee: number; max_paise_per_point: number };
  file: string;
  note: string;
  /** Only on a POST: whether the change reached the module's own chain. */
  audited?: boolean;
  was?: Rules;
}

/** What the audit chain says about one bill. `found: false` is a session the
    chain has not seen yet, which is a state and not an error. */
export interface BillState {
  found: boolean;
  closed: boolean;
  minted: boolean;
  settled: boolean;
  settled_at: string | null;
  settled_by: 'webhook' | 'kernel' | null;
  settled_paise: Paise | null;
  settled_rupees: string | null;
  total_paise: Paise | null;
}

/** Why a bill earned what it did — including why zero. The server's strings. */
export type Why =
  | 'settled_by_the_gateway'
  | 'bill_not_in_the_ledger'
  | 'link_sent_but_not_settled'
  | 'bill_closed_but_no_link_issued'
  | 'bill_still_open'
  | 'settled_but_amount_not_recorded'
  | 'no_rule_in_force_when_it_settled'
  | 'settled_for_less_than_a_whole_rupee';

export interface EarnEntry {
  kind: 'earn';
  session_id: string;
  at: string | null;
  source: 'attached_at_the_counter' | 'storefront_order';
  order_id: string | null;
  bill: BillState;
  points: number;
  whole_rupees: number;
  points_per_rupee: number | null;
  why: Why;
  /** The reason as a sentence, from the server. */
  said: string;
}

export interface RedeemEntry {
  kind: 'redeem';
  redemption_id: string;
  at: string | null;
  proposed_at: string | null;
  applied: boolean;
  applied_at: string | null;
  session_id: string | null;
  bill: BillState | null;
  points: number;
  value_paise: Paise;
  value_rupees: string;
  paise_per_point: number | null;
  said: string;
}

export type Entry = EarnEntry | RedeemEntry;

export interface ChainBlock {
  ok: boolean;
  exists: boolean;
  lines_verified: number;
  error: string | null;
  path: string | null;
  orders_readable: boolean;
  orders_seen: number;
}

export interface Balance {
  phone: string;
  /** False for a number with no history at all. Zero is still derived, not guessed. */
  known: boolean;
  earned_points: number;
  redeemed_points: number;
  /** Proposed and not yet put on a bill. Listed, never deducted. */
  proposed_points: number;
  balance_points: number;
  balance_value_paise: Paise;
  balance_value_rupees: string;
  settled_paise: Paise;
  settled_rupees: string;
  bills_settled: number;
  bills_awaiting: number;
  bills_not_in_ledger: number;
  rules: Rules;
  chain: ChainBlock;
  note: string;
}

export interface Ledger extends Balance {
  count: number;
  entries: Entry[];
  why: Record<Why, string>;
}

export interface Member {
  phone: string;
  earned_points: number;
  redeemed_points: number;
  proposed_points: number;
  balance_points: number;
  balance_value_paise: Paise;
  balance_value_rupees: string;
  bills_settled: number;
  bills_awaiting: number;
  last_at: string;
}

export interface Members {
  count: number;
  truncated: boolean;
  members: Member[];
  rules: Rules;
  chain: ChainBlock;
}

export interface Attached {
  session_id: string;
  phone: string;
  changed: boolean;
  bill: BillState;
  earns: { points: number; why: Why; said: string };
  audited: boolean | null;
  note: string;
}

export interface Redemption {
  redemption_id: string;
  phone: string;
  points: number;
  paise_per_point: number;
  value_paise: Paise;
  value_rupees: string;
  proposed_at: string;
  applied: boolean;
  applied_at: string | null;
  session_id: string | null;
  balance_before_points: number;
  balance_after_points?: number;
}

/** The discount line as the till should draw it. Positive `off_paise`, the
    way offers name a discount; a negative price is a thing this program never
    writes. */
export interface RedemptionLine {
  kind: 'loyalty_redemption';
  redemption_id: string;
  label: string;
  off_paise: Paise;
  off_rupees: string;
  points: number;
}

export interface Proposal {
  redemption: Redemption;
  line: RedemptionLine;
  applied: boolean;
  balance_before_points: number;
  balance_if_applied_points?: number;
  balance_after_points?: number;
  session_id?: string;
  bill?: BillState;
  audited?: boolean;
  /** What the till has to do with this. The server's list, printed as is. */
  till_must: string[];
  note?: string;
}

export interface Health {
  module: string;
  file: string;
  exists: boolean;
  file_error: string | null;
  audit_file: string;
  audit: { ok: boolean; lines: number; head: string; error: string | null };
  money_chain: { ok: boolean | null; path: string | null };
  shop_dir: string;
  rules: Rules | null;
  attachments: number;
  redemptions: number;
  earns_on: string;
}

/* --------------------------------------------------------------- requests -- */

export const rules = () => send<RulesView>('/loyalty/rules');

/** Two whole numbers. The server refuses anything else by name. */
export const setRules = (points_per_rupee: number, paise_per_point: number) =>
  send<RulesView>('/loyalty/rules', postJson({ points_per_rupee, paise_per_point }));

export const balance = (phone: string) =>
  send<Balance>(`/loyalty/balance/${encodeURIComponent(phone)}`);

export const ledger = (phone: string) =>
  send<Ledger>(`/loyalty/ledger/${encodeURIComponent(phone)}`);

export const members = () => send<Members>('/loyalty/members');

export const health = () => send<Health>('/loyalty/health');

/** Tie a counter bill to a number. Accepted before the bill is in the chain. */
export const attach = (session_id: string, phone: string) =>
  send<Attached>('/loyalty/attach', postJson({ session_id, phone }));

/** Propose spending points. Deducts nothing; refused past the balance. */
export const redeem = (phone: string, points: number) =>
  send<Proposal>('/loyalty/redeem', postJson({ phone, points }));

export const redemption = (redemptionId: string) =>
  send<Proposal>(`/loyalty/redemptions/${encodeURIComponent(redemptionId)}`);

/** The till says which bill the line went on. This is the debit. */
export const apply = (redemptionId: string, session_id: string) =>
  send<Proposal>(
    `/loyalty/redemptions/${encodeURIComponent(redemptionId)}/apply`,
    postJson({ session_id }),
  );
