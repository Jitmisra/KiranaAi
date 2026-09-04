/**
 * HISAAB — the requests the day-close screen makes, in one place, with types.
 *
 * Three rules this module exists to keep, the same three every other request
 * module here keeps:
 *
 *  1. THE BROWSER IS NEVER AN AUTHOR OF MONEY. The one write on this screen is
 *     the shopkeeper's own count of the drawer, and it goes up as the RUPEE
 *     STRING he typed — never as paise this file worked out. A page that
 *     converts rupees in the browser writes `parseFloat(x) * 100`, and
 *     48.20 * 100 is 4819.999999999999 in every browser on earth. That number
 *     would then be frozen into a record that cannot be reopened.
 *  2. A REFUSAL IS A RESULT, NOT AN ERROR. `/daybook/*` answers
 *     `{ok:false, reason, detail}` with a 400 on purpose, so the body is parsed
 *     on non-2xx instead of thrown, and only a transport failure produces a
 *     refusal about the network.
 *  3. NOTHING IS DERIVED HERE. Every figure below is a number the server
 *     counted off the hash-chained audit log. This file names the shapes and
 *     nothing else — there is no arithmetic in it at all.
 *
 * `send` is copied from `manageapi.ts` rather than imported, for the reason
 * stated there: it is module-private in `api.ts`, and exporting it would widen
 * that module's surface for one caller. The precedence rule it encodes —
 * explicit `ok`, then an `error` string, then the HTTP STATUS — is load-bearing
 * and is copied intact, because FastAPI's own 404 and 422 come back as
 * `{"detail": ...}` with neither `ok` nor `error`, and a rule that only read
 * the body would file a missing route as a success.
 *
 * NOTE ON DEV AND ON MOUNTING: `vite.config.ts` proxies an explicit allowlist
 * of path prefixes to the till on :8790, and `/daybook`, `/cash` and
 * `/expenses` are not on it yet. Until they are, every request here answers
 * `the server refused with HTTP 404` under `npm run dev` — which the screen
 * renders as a refusal rather than a blank page, but it is a refusal about the
 * mount and not about the shop.
 */

import type { Result } from './api';
import type { Chain, DayBrief } from './manageapi';
import type { BesideBody } from './milanapi';

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

/* ------------------------------------------------------------ the figures -- */

/**
 * One day's numbers as `gawaah/manage.py` states them.
 *
 * `DayBrief` is imported rather than restated: the close-out freezes exactly
 * what the Today screen derives, from the same fold of the same chain, and a
 * second interface here would be a second opinion about what a day's takings
 * are. The two maps are the working set that derivation carries; the day brief
 * endpoint drops them and this one does not, so they are optional.
 */
export interface Derived extends DayBrief {
  units_by_sku?: Record<string, number>;
  line_revenue_by_sku?: Record<string, number>;
}

/**
 * A product on the day, with the name AS IT READ WHEN THE DAY WAS CLOSED.
 *
 * `in_catalogue_at_close` is not `still_in_catalogue`: it is a fact about the
 * moment of closing, frozen with everything else, and it does not change when
 * the product is deleted next month.
 */
export interface TopSeller {
  sku_id: string;
  name: string;
  units: number;
  revenue_paise: number;
  revenue_rupees: string;
  in_catalogue_at_close: boolean;
}

export interface DayWindow {
  from: string;
  to: string;
}

/* ------------------------------------------------------------- the review -- */

/** What closing right now would write down. Reading this writes nothing. */
export interface PreviewBody {
  day: string;
  already_closed: boolean;
  closed_at: string | null;
  day_has_ended: boolean;
  /** Integer seconds. A duration, so "closed early" is a quantity. */
  seconds_left_in_day: number;
  closing_early_note: string;
  derived: Derived;
  top_sellers: TopSeller[];
  window: DayWindow;
  chain: Chain;
  /** Present only when the chain does not verify — the figures then read LOW. */
  chain_warning: string | null;
  derived_from: string;
  note: string;
}

/* -------------------------------------------------------------- the record -- */

/**
 * A closed day, exactly as it was frozen. Served byte for byte, never
 * recomputed and never corrected — see the module docstring in
 * `gawaah/daybook.py` for why that is a record rather than a cache.
 */
export interface CloseRecord {
  format: number;
  day: string;
  closed_at: string;
  closed_at_local: string;
  day_had_ended: boolean;
  seconds_left_in_day_at_close: number;
  closed_by: string;
  note: string;
  counted_cash_paise: number;
  counted_cash_rupees: string;
  derived: Derived;
  top_sellers: TopSeller[];
  window: DayWindow;
  chain_at_close: Chain;
  chain_warning_at_close: string | null;
  derived_from: string;
  record_sha256: string;
  /** Null when the close could not be written to this counter's own chain. */
  audit_head: string | null;
}

export interface CloseBody {
  day: string;
  closed: boolean;
  record: CloseRecord;
  /** False means the record is on disk but its chain line was not written. */
  audited: boolean;
  chain_warning: string | null;
  note: string;
}

/** One closed day as a LIST row. The frozen figures in full are behind `day()`. */
export interface DayRow {
  day: string;
  closed_at: string;
  day_had_ended: boolean;
  closed_by: string;
  note: string;
  bills: number | null;
  revenue_paise: number | null;
  revenue_rupees: string | null;
  settled_count: number | null;
  settled_paise: number | null;
  awaiting_count: number | null;
  awaiting_paise: number | null;
  counted_cash_paise: number | null;
  counted_cash_rupees: string | null;
  chain_verified_at_close: boolean;
  record_sha256: string | null;
  audit_head: string | null;
}

export interface ListBody {
  count: number;
  days_on_record: number;
  /** A capped list must never read as a complete one. */
  truncated: boolean;
  limit: number;
  days: DayRow[];
  dir: string;
  note: string;
}

/**
 * The same day, derived from the chain as it stands RIGHT NOW, beside the
 * per-field difference against the frozen figures.
 *
 * This is a separate block on purpose and it never overwrites the record. A
 * negative difference is possible and means the chain no longer verifies as far
 * as it did, so the LIVE figures are the short ones.
 */
export interface AfterClose {
  derived_now: Derived;
  difference: Record<string, number | null>;
  changed: boolean;
  changed_fields: string[];
  chain: Chain;
  chain_warning: string | null;
  note: string;
}

export interface OneDayBody {
  day: string;
  record: CloseRecord;
  record_sha256_recomputed: string;
  /** The digest recomputed from the file as served, against the stored one. */
  record_unedited: boolean;
  after_close: AfterClose | null;
  /** Named when the comparison could not be made, so it is never just blank. */
  after_close_unavailable: string | null;
  /**
   * MILAN, beside the record: the gateway's settlement report for the day
   * these bills reach the bank (T+1), matched against the chain just now.
   * Never inside `record`, and `record` is not changed by it.
   */
  milan: BesideBody | null;
  milan_unavailable: string | null;
  note: string;
}

/* ------------------------------------------------------------- the drawer -- */

/**
 * The cash position, from `gawaah/expenses.py`. It is on this screen because
 * counting the drawer is the act the close-out records, and the reconciliation
 * lives there rather than in the daybook: those two numbers are not comparable
 * and a difference between them would look like an answer.
 */
export interface CashPosition {
  day: string;
  opening: { counted: boolean; paise: number | null; rupees: string | null; counted_at: string | null };
  cash_sales: { bills: number; paise: number; rupees: string };
  gateway_sales: { bills: number; paise: number; rupees: string; settled_by: Record<string, number> };
  cash_expenses: { count: number; paise: number; rupees: string };
  /** Listed, and deliberately NOT subtracted from the drawer. */
  bank_expenses: { count: number; paise: number; rupees: string };
  movement_paise: number;
  movement_rupees: string;
  /** Absent until the opening float is counted, rather than assumed to be zero. */
  expected_closing_paise: number | null;
  expected_closing_rupees: string | null;
  counted_closing: {
    counted: boolean; paise: number | null; rupees: string | null;
    counted_at: string | null; note: string;
  };
  difference_paise: number | null;
  difference_rupees: string | null;
  difference_direction: 'exact' | 'over' | 'short' | null;
  difference_note: string;
  cash_sales_note: string;
  undated_bills: number;
  chain: Chain;
  chain_warning: string | null;
  derived_from: string;
}

export interface ExpenseRow {
  expense_id: string;
  at: string | null;
  day: string;
  /** Null when the record's amount is not integer paise — never read as zero. */
  amount_paise: number | null;
  amount_rupees: string | null;
  category: string;
  category_label: string;
  note: string;
  paid_with: 'cash' | 'bank';
  void: boolean;
  voided_at: string | null;
  void_reason: string | null;
}

export interface ExpensesDayBody {
  day: string;
  rows_on_record: number;
  unreadable_rows: number;
  expenses: ExpenseRow[];
  count: number;
  total_paise: number;
  total_rupees: string;
  cash_count: number;
  cash_paise: number;
  cash_rupees: string;
  bank_count: number;
  bank_paise: number;
  bank_rupees: string;
  voided_count: number;
  voided_paise: number;
  voided_rupees: string;
  by_category: Array<{ category: string; label: string; count: number; paise: number; rupees: string }>;
  note: string;
}

/* ------------------------------------------------------- reconciliation -- */

/**
 * One count-and-money pair. `paise` is the integer the server counted; `rupees`
 * is the string it formatted. NOTHING IN THE BROWSER DIVIDES ONE BY A HUNDRED
 * to get the other, and no screen adds two of these together — `Bucket` values
 * arrive already summed, in the split the server chose, and combining them here
 * would be the browser deciding two disagreeing figures are one figure.
 */
export interface Bucket {
  bills: number;
  paise: number;
  rupees: string;
}

/** Gateway activity in a window, counted as EVENTS rather than as bills. */
export interface ReconcileEvents {
  mint_attempts: number;
  sessions_minted: number;
  mints_beyond_one_per_session: number;
  gateway_errors: number;
  mint_refusals: Record<string, number>;
  mint_refusals_total: number;
  /** Webhook POSTs the counter received, trusted or not. This is the ONLY
      count of that: a post with a bad signature never reaches a session, so
      `paisa /health` — which counts what its current process has seen — cannot
      report one it received before its last restart. */
  webhooks_received: number;
  webhooks_green: number;
  webhooks_refused: Record<string, number>;
  webhooks_refused_total: number;
  abstained_lines: number;
}

/**
 * A thing the books do not agree on, in the server's own words.
 *
 * The browser does not decide whether one of these is true, only how to draw
 * it: each is emitted by `gawaah/daybook.py` when its own condition holds on
 * the chain, and carries the figures a shopkeeper would check it against.
 */
export interface Disagreement {
  code: string;
  count: number;
  paise?: number;
  headline: string;
  detail: string;
}

/** One window, split into states that are DISJOINT and sum back to `billed`. */
export interface ReconcileWindow {
  billed: Bucket;
  /** A signature-verified webhook matched the session. Nothing else. */
  settled: Bucket;
  /** The kernel recorded a settlement with no webhook line in the chain. Never
      added into `settled`, and always shown apart from it. */
  settled_unwitnessed: Bucket;
  refused: Bucket;
  /** Closed with no payment link ever minted — not awaiting anything. */
  never_asked: Bucket;
  awaiting: Bucket;
  /** billed − settled. Money the gateway has not confirmed to this counter. */
  owed: Bucket;
  by_channel: Record<string, { billed: Bucket; settled: Bucket }>;
  events: ReconcileEvents;
  disagreements: Disagreement[];
}

export interface ReconcileBody {
  ok: true;
  settles_money: false;
  day: string;
  window: DayWindow;
  today: ReconcileWindow;
  lifetime: ReconcileWindow;
  chain: Chain;
  chain_warning: string | null;
  derived_from: string;
}

/* -------------------------------------------------------------- requests -- */

const day_q = (day?: string) => (day ? `?day=${encodeURIComponent(day)}` : '');

/**
 * What the till billed against what the gateway actually did, unnetted.
 *
 * Carries the lifetime figures beside the day's on every call, because
 * "nothing settled today" and "nothing has ever settled here" are different
 * shops and a day-shaped answer cannot tell them apart.
 */
export const reconcile = (day?: string) =>
  send<ReconcileBody>(`/daybook/reconcile${day_q(day)}`);

/** The close-out review: the figures the close will freeze, in that shape. */
export const preview = (day?: string) =>
  send<PreviewBody>(`/daybook/preview${day_q(day)}`);

/** Every day this shop has closed, newest first. Rows only. */
export const days = (limit?: number) =>
  send<ListBody>(`/daybook${limit === undefined ? '' : `?limit=${limit}`}`);

/** One closed day in full: the frozen record, and what the chain says now. */
export const day = (which: string) =>
  send<OneDayBody>(`/daybook/${encodeURIComponent(which)}`);

/**
 * Close the day.
 *
 * `counted_cash_rupees` is the string the shopkeeper typed and is sent
 * unchanged; the server parses it with `money.from_rupees_str`, which never
 * touches a float. There is deliberately no paise argument on this function —
 * a caller that had one would have had to do the multiplication.
 */
export const close = (arg: {
  counted_cash_rupees: string;
  day?: string;
  note?: string;
  closed_by?: string;
}) =>
  send<CloseBody>('/daybook/close', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      counted_cash_rupees: arg.counted_cash_rupees,
      ...(arg.day ? { day: arg.day } : {}),
      ...(arg.note ? { note: arg.note } : {}),
      ...(arg.closed_by ? { closed_by: arg.closed_by } : {}),
    }),
  });

/** The drawer, reconciled by the module that owns that reconciliation. */
export const cash = (day?: string) => send<CashPosition>(`/cash${day_q(day)}`);

/** What went out of the shop that day. */
export const expenses = (day?: string) =>
  send<ExpensesDayBody>(`/expenses/day${day_q(day)}`);

/* --------------------------------------------------------------- helpers -- */

/**
 * Tidy what was typed into the field, WITHOUT changing its value.
 *
 * Spaces, a rupee sign and the grouping commas of `4,820.00` are removed. Not
 * one of those carries value, so dropping them cannot change the amount — and
 * the digits and the decimal point are passed through untouched, because the
 * server is the only thing here allowed to turn them into money.
 */
export function tidyRupees(typed: string): string {
  return typed.replace(/[\s,₹]/g, '');
}

/**
 * Is this a rupee amount the server will accept? Whole rupees, or rupees and
 * up to two decimal places.
 *
 * This is a SHAPE check and not a parse: it produces an instant, specific
 * answer for a typo instead of a round trip, and it deliberately does not
 * compute the value. The server remains the authority — it also refuses a
 * count past five lakh, which this cannot see without doing arithmetic on
 * money, so that refusal comes back from the server and is shown verbatim.
 */
export function looksLikeRupees(tidied: string): boolean {
  return /^\d{1,9}(\.\d{1,2})?$/.test(tidied);
}

/** Plain English for the shape check above, for the field's own error line. */
export const RUPEE_HINT =
  'Write it as rupees — 4820, or 4820.50 for rupees and paise. Two decimal '
  + 'places at most: a shop does not deal in half paise.';
