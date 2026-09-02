/**
 * Every request the Expenses screen makes, in one place, with types.
 *
 * Two rules this module exists to keep, and one thing it deliberately will not
 * do:
 *
 *  1. THE BROWSER IS NEVER AN AUTHOR OF MONEY. An amount goes up as the STRING
 *     the shopkeeper typed — `amount_rupees`, `counted_rupees` — and the server
 *     parses it to integer paise itself. A page that turned "12.10" into paise
 *     in the browser would write `parseFloat(x) * 100`, and that is
 *     1209.9999999999998, which is how a drawer ends up a paisa short for a
 *     reason nobody can find. Nothing here multiplies, divides or rounds money.
 *  2. A REFUSAL IS A RESULT, NOT AN ERROR. `/expenses/*` and `/cash*` answer
 *     `{ok:false, reason, detail}` with a 400 on purpose, so the body is parsed
 *     on non-2xx rather than thrown, and only a transport failure produces a
 *     refusal about the network.
 *
 * WHAT THIS CANNOT ASK FOR. There is no edit and no delete: a mistyped expense
 * is VOIDED, with a reason, and stays on the list counted in nothing. So this
 * module has no `update()` and no `remove()`, and the screen has no button that
 * would need one.
 *
 * `send` is duplicated from `api.ts` rather than imported: it is module-private
 * there, and a new screen must not widen the till's own request layer to borrow
 * one function. The precedence rule it encodes — explicit `ok`, then an `error`
 * string, then the HTTP STATUS — is load-bearing and copied intact. FastAPI's
 * own 422 comes back as `{"detail": ...}` with neither `ok` nor `error`, and a
 * rule that only read the body would file a validation crash as a success.
 *
 * NOTE ON DEV: `vite.config.ts` proxies an explicit allowlist of path prefixes
 * to the till on :8790. `/expenses` and `/cash` have to be in that list, and
 * `gawaah/expenses.py` has to be mounted on the till, or every request here
 * 404s. The screen renders that 404 as a refusal rather than an empty page.
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

/* ------------------------------------------------------------ an expense -- */

/** Cash out of the drawer, or off the bank account. Only the first moves the
    drawer, and mis-marking one is the difference between a drawer that
    reconciles and one that appears to be short by a month's rent. */
export type PaidWith = 'cash' | 'bank';

export interface Category {
  category: string;
  label: string;
}

export interface CategoriesBody {
  categories: Category[];
  paid_with: PaidWith[];
  max_expense_paise: number;
  max_expense_rupees: string;
  max_note: number;
  note: string;
}

/**
 * One expense as the day book holds it.
 *
 * `amount_paise` is NULL when the stored record's amount is not integer paise.
 * That is not a gap to fill with a zero: the server leaves such a row out of
 * every total and reports it, and this screen shows it as unreadable rather
 * than as free.
 */
export interface Expense {
  expense_id: string;
  at: string | null;
  day: string;
  amount_paise: number | null;
  amount_rupees: string | null;
  category: string;
  category_label: string;
  note: string;
  paid_with: PaidWith;
  void: boolean;
  voided_at: string | null;
  void_reason: string | null;
}

/** One category's share of a day. `paise` is the sum of unvoided rows. */
export interface CategoryTotal {
  category: string;
  label: string;
  count: number;
  paise: number;
  rupees: string;
}

/**
 * The totals block, which the server splices into both list responses.
 *
 * `count` IS NOT THE NUMBER OF ROWS RETURNED. It is the number of rows that
 * were added up — voided rows and rows whose amount is not integer paise are
 * excluded from it. The screen uses `expenses.length` when it means "rows on
 * screen" and this when it means "rows in the total", because reading one as
 * the other is how a voided entry silently becomes a missing entry.
 */
export interface Totals {
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
  by_category: CategoryTotal[];
}

export interface DayBody extends Totals {
  day: string;
  rows_on_record: number;
  /** Rows the server could not add up. Reported, never counted as nothing. */
  unreadable_rows: number;
  expenses: Expense[];
  note: string;
}

export interface ListBody extends Totals {
  day: string | null;
  total_on_record: number;
  truncated: boolean;
  limit: number;
  expenses: Expense[];
}

export interface ExpenseWritten {
  expense: Expense;
  /** False means the row is on disk but its line did not reach the hash chain. */
  audited: boolean;
  note: string;
}

/* ---------------------------------------------------------- the drawer -- */

/** A figure the shopkeeper counted with his hands. Never an observation. */
export interface Counted {
  counted: boolean;
  paise: number | null;
  rupees: string | null;
  counted_at: string | null;
}

export interface CountedClosing extends Counted {
  note: string;
}

/** The state of the hash chain the sales figures were folded out of. */
export interface Chain {
  ok: boolean;
  exists: boolean;
  lines_verified: number;
  lines_readable: number;
  head: string | null;
  error: string | null;
  path: string;
}

/**
 * The cash position for one day.
 *
 * `expected_closing_paise` is NULL until the opening is counted, and that null
 * is the whole design: a drawer that started with two thousand rupees of change
 * and is reported against an assumed empty opening reads as two thousand over.
 * A figure wrong by exactly the float is worse than no figure, because it looks
 * like an answer. The screen must render the null as a missing step, never as a
 * zero.
 *
 * `difference_paise` is signed and may be negative. Nothing in this file gives
 * it a colour or a verdict — see the screen.
 */
export interface CashBody {
  day: string;
  opening: Counted;
  cash_sales: { bills: number; paise: number; rupees: string };
  gateway_sales: {
    bills: number; paise: number; rupees: string;
    settled_by: Record<string, number>;
  };
  cash_expenses: { count: number; paise: number; rupees: string };
  /** Listed, totalled, and deliberately NOT taken out of the drawer. */
  bank_expenses: { count: number; paise: number; rupees: string };
  movement_paise: number;
  movement_rupees: string;
  expected_closing_paise: number | null;
  expected_closing_rupees: string | null;
  counted_closing: CountedClosing;
  difference_paise: number | null;
  difference_rupees: string | null;
  difference_direction: 'exact' | 'over' | 'short' | null;
  /** The server's own sentence about the gap. Shown verbatim: it is written to
      describe and not to accuse, and paraphrasing it here would put this screen
      in the business of having an opinion about a shopkeeper's drawer. */
  difference_note: string;
  /** The limit, stated in the same response that carries the number. */
  cash_sales_note: string;
  undated_bills: number;
  chain: Chain;
  /** Present only when the chain stopped verifying — and then the sales are
      short by whatever came after the break, so the drawer reads over. */
  chain_warning: string | null;
  derived_from: string;
}

export interface CountWritten {
  day: string;
  kind: 'opening' | 'closing';
  counted_paise: number;
  counted_rupees: string;
  counted_at: string;
  audited: boolean;
  note: string;
}

/* --------------------------------------------------------------- requests -- */

export const categories = () => send<CategoriesBody>('/expenses/categories');

/** One day's spending, every row of it — not a capped page. */
export const day = (d: string) =>
  send<DayBody>(`/expenses/day?day=${encodeURIComponent(d)}`);

/** Everything on record, newest first, capped. `truncated` says when there was
    more, so a capped list never reads as a complete one. */
export const list = (opts: { day?: string; limit?: number } = {}) => {
  const q = new URLSearchParams();
  if (opts.day) q.set('day', opts.day);
  if (opts.limit !== undefined) q.set('limit', String(opts.limit));
  const qs = q.toString();
  return send<ListBody>(`/expenses${qs ? `?${qs}` : ''}`);
};

/**
 * Record one thing the shop paid for.
 *
 * `amount_rupees` is the typed string and nothing else. The server refuses a
 * body carrying both spellings of the amount rather than picking one, so this
 * function never sends `amount_paise`.
 */
export const add = (e: {
  amount_rupees: string;
  category: string;
  note: string;
  paid_with: PaidWith;
  day: string;
}) =>
  send<ExpenseWritten>('/expenses', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(e),
  });

/** Stop an entry counting. The row keeps its amount, its note and its id. */
export const voidExpense = (expenseId: string, reason: string) =>
  send<ExpenseWritten>(`/expenses/${encodeURIComponent(expenseId)}/void`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  });

export const cash = (d: string) =>
  send<CashBody>(`/cash?day=${encodeURIComponent(d)}`);

export const countOpening = (counted_rupees: string, d: string) =>
  send<CountWritten>('/cash/opening', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ counted_rupees, day: d }),
  });

export const countClosing = (counted_rupees: string, d: string, note: string) =>
  send<CountWritten>('/cash/closing', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ counted_rupees, day: d, note }),
  });

/* ---------------------------------------------------------------- shared -- */

/**
 * Format paise for this screen, without ever throwing and without ever
 * quietly repairing a number.
 *
 * `rupees()` refuses a float, a negative and anything over ₹10,00,000, and it
 * is right to at a till — those are money bugs rather than large bills. This
 * screen is the other case twice over. A drawer that is short produces a
 * NEGATIVE difference, which is an ordinary Tuesday and not a bug, so the sign
 * is split off and rendered rather than refused. And a stored amount outside
 * the range is shown as the raw paise it is, named as out of range, because
 * blanking the page would leave the shopkeeper with no way to see the bad
 * figure at all.
 */
export function money(paise: number | null | undefined): string {
  if (paise === null || paise === undefined) return '—';
  try {
    // Math.abs on an integer is an integer; no division, no rounding.
    return paise < 0 ? `−${rupees(-paise)}` : rupees(paise);
  } catch {
    return `${paise} paise — outside the range this till will price`;
  }
}

/** The size of a gap, with the direction said in words instead of a sign. */
export function magnitude(paise: number | null | undefined): string {
  if (paise === null || paise === undefined) return '—';
  return money(paise < 0 ? -paise : paise);
}

/** Today, as this browser's clock reads it, in the YYYY-MM-DD the server wants.
    Built from the date parts rather than from `toISOString()`, which is UTC and
    would put a shop in Chennai on yesterday's page until half past five. */
export function todayLabel(d: Date = new Date()): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

/** Step a YYYY-MM-DD by whole days, without importing a date library. */
export function shiftDay(label: string, days: number): string {
  const [y, m, d] = label.split('-').map((n) => Number(n));
  if (!y || !m || !d) return label;
  const at = new Date(y, m - 1, d);
  at.setDate(at.getDate() + days);
  return todayLabel(at);
}

/** A calendar day as a shopkeeper says it. The label is already local. */
export function dayName(label: string): string {
  const [y, m, d] = label.split('-').map((n) => Number(n));
  if (!y || !m || !d) return label;
  return new Date(y, m - 1, d).toLocaleDateString('en-IN', {
    weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
  });
}

/** A timestamp as the shopkeeper reads a clock, not as the ledger writes one. */
export function clock(iso: string | null | undefined): string {
  if (!iso) return '—';
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  return at.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true });
}
