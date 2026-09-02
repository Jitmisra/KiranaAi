/**
 * The Insights screen's one request, with types.
 *
 * Same three rules as `manageapi.ts`, for the same reasons:
 *
 *  1. THE BROWSER IS NEVER AN AUTHOR. There is no write here at all. The page
 *     sends a window length and a day to look back from; every figure, every
 *     baseline and every sentence is derived by the server off the audit chain.
 *     Not one number on this screen is computed in this file.
 *  2. A REFUSAL IS A RESULT. `/insights` answers `{ok:false, reason, detail}`
 *     with a 400 on purpose, so the body is parsed on a non-2xx instead of
 *     thrown. Only a transport failure produces a refusal about the network.
 *  3. "NOT ENOUGH HISTORY YET" IS NOT AN ERROR. Every block is a discriminated
 *     union on `available`, so the compiler will not let a screen read
 *     `delta_pct` off a block that refused to compute one. That is the whole
 *     point of the shape: the unavailable case cannot be forgotten.
 *
 * NOTE ON DEV: `vite.config.ts` proxies an explicit allowlist of path prefixes
 * to the till on :8790. `/insights` has to be in that list or every request
 * here 404s under `npm run dev` while working perfectly in the built site.
 */

import type { Result } from './api';

async function send<T>(url: string): Promise<Result<T>> {
  let res: Response;
  try {
    res = await fetch(url, { cache: 'no-store' });
  } catch (e) {
    // The network, not the product. Say which: they need different fixes.
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

/* ---------------------------------------------------------------- shared -- */

/** The state of the hash chain every figure below was folded out of. */
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
 * A block that would not answer.
 *
 * `days_of_history` and `days_needed` are both present on purpose: "not enough
 * history" with no numbers reads as a bug, and with them it reads as a wait.
 * `counting` names what was counted, because the blocks do not all count the
 * same thing — complete days, days that took money, earlier Tuesdays.
 */
export interface NotEnough {
  available: false;
  reason: 'not_enough_history_yet';
  detail: string;
  days_of_history: number;
  days_needed: number;
  counting: string;
}

export type Block<T> = (T & { available: true }) | NotEnough;

export interface Window {
  days: number;
  from: string;
  to: string;
  anchor_complete: boolean;
  now: string;
  utc_offset: string;
  days_with_history: number;
  complete_days_with_history: number;
}

export interface History {
  first_bill_at: string | null;
  last_bill_at: string | null;
  first_bill_day: string | null;
  last_bill_day: string | null;
  days_spanned: number;
  trading_days: number;
  trading_days_in_window: number;
  closed_bills: number;
  undated_bills: number;
  note: string;
}

/* ------------------------------------------------------------------ days -- */

/**
 * One day.
 *
 * `no_history` is not "zero". It is a day before this counter's first bill, and
 * it is drawn as a gap and left out of every baseline. `complete` is false for
 * the day you are standing in, which is why no comparison on this screen uses
 * it as a whole day.
 */
export interface DayPoint {
  date: string;
  short: string;
  weekday: string;
  weekday_short: string;
  weekday_index: number;
  bills: number;
  revenue_paise: number;
  revenue_rupees: string;
  settled_paise: number;
  settled_rupees: string;
  units: number;
  excluded_lines: number;
  complete: boolean;
  no_history: boolean;
}

export interface DayRef {
  date: string;
  short: string;
  revenue_paise: number;
  revenue_rupees: string;
}

export interface DaysBlock {
  available: true;
  series: DayPoint[];
  days_of_history: number;
  trading_days: number;
  total_paise: number;
  total_rupees: string;
  settled_paise: number;
  settled_rupees: string;
  bills: number;
  average_day_paise: number;
  average_day_rupees: string;
  peak_paise: number;
  /** FINISHED days only — a day still running has not taken all it is going to. */
  busiest_complete_day: DayRef | null;
  quietest_complete_day: DayRef | null;
  complete_trading_days: number;
  extremes_note: string;
  /** The median day. A `Block`, not a bare object: below seven days that took
      money there is no median worth printing, and the union is what stops a
      screen reading `median_paise` off a block that refused to compute one. */
  baseline: Block<{ median_paise: number; median_rupees: string; method: string }>;
}

/* ------------------------------------------------------------------ week -- */

export interface WeekTotals {
  from: string | null;
  to: string | null;
  days: number;
  bills: number;
  revenue_paise: number;
  revenue_rupees: string;
  settled_paise: number;
  settled_rupees: string;
  units: number;
  average_bill_paise: number;
  average_bill_rupees: string;
}

export interface WeekBlockBody {
  days_of_history: number;
  complete_days_only: true;
  this_week: WeekTotals;
  last_week: WeekTotals;
  delta_paise: number;
  delta_rupees: string;
  delta_pct: number | null;
  bills_delta: number;
  direction: 'up' | 'down' | 'level';
  sentence: string;
  method: string;
}

/* -------------------------------------------------------- the same weekday -- */

export interface PreviousWeekday {
  date: string;
  short: string;
  weeks_ago: number;
  revenue_paise: number;
  revenue_rupees: string;
  bills: number;
  full_day_paise: number;
  full_day_rupees: string;
  full_day_bills: number;
}

export interface WeekdayBlockBody {
  weekday: string;
  date: string;
  day_complete: boolean;
  cut_at: string;
  cut_seconds_into_day: number;
  today: { date: string; revenue_paise: number; revenue_rupees: string; bills: number };
  previous: PreviousWeekday[];
  samples: number;
  days_of_history: number;
  baseline_paise: number;
  baseline_rupees: string;
  baseline_method: string;
  delta_paise: number;
  delta_rupees: string;
  delta_pct: number | null;
  direction: 'up' | 'down' | 'level';
  sentence: string;
}

/* ----------------------------------------------------------------- hours -- */

export interface HourPoint {
  hour: number;
  label: string;
  revenue_paise: number;
  revenue_rupees: string;
  days_with_a_bill: number;
  share_pct: number | null;
}

export interface HoursBlockBody {
  days_of_history: number;
  profile: HourPoint[];
  total_paise: number;
  total_rupees: string;
  peak_paise: number;
  first_active_hour: number | null;
  last_active_hour: number | null;
  busiest_hour: { hour: number; label: string; revenue_paise: number; revenue_rupees: string; share_pct: number | null } | null;
  method: string;
  shares_note: string;
}

/* -------------------------------------------------------------- products -- */

/**
 * A product that moved.
 *
 * `delta_pct` is null when the earlier week sold none of it — a percentage
 * against zero is not a rate of change, so those products are in
 * `started_selling` and carry no percentage at all.
 */
export interface Mover {
  sku_id: string;
  name: string;
  still_in_catalogue: boolean;
  units_now: number;
  units_before: number;
  delta_units: number;
  revenue_now_paise: number;
  revenue_now_rupees: string;
  revenue_before_paise: number;
  revenue_before_rupees: string;
  delta_pct: number | null;
}

export interface ProductsBlockBody {
  days_of_history: number;
  this_week: { from: string; to: string };
  last_week: { from: string; to: string };
  rising: Mover[];
  falling: Mover[];
  started_selling: Mover[];
  stopped_selling: Mover[];
  rising_total: number;
  falling_total: number;
  started_total: number;
  stopped_total: number;
  too_few_to_judge: number;
  min_units_to_judge: number;
  method: string;
}

/* ------------------------------------------------------------- anomalies -- */

/**
 * A day or an hour far from its own baseline — never a bare "unusual".
 *
 * Every field a reader needs to disagree with the verdict is here: what it
 * took, what the baseline was, how that baseline was computed, how wide the
 * usual spread is, and the distance in rupees, in percent and in spreads.
 * `deviation_spreads_x10` is tenths, because a float never touches this
 * product: 63 means 6.3 spreads.
 */
export interface Anomaly {
  kind: 'day' | 'hour';
  key: string;
  label: string;
  value_paise: number;
  value_rupees: string;
  baseline_paise: number;
  baseline_rupees: string;
  baseline_method: string;
  spread_paise: number;
  spread_rupees: string;
  deviation_paise: number;
  deviation_rupees: string;
  deviation_pct: number | null;
  deviation_spreads_x10: number | null;
  direction: 'above' | 'below';
  samples: number;
  sentence: string;
}

export interface AnomaliesBlockBody {
  days_of_history: number;
  days: Anomaly[];
  hours: Anomaly[];
  days_found: number;
  hours_found: number;
  days_checked: number;
  hours_checked: number;
  subject_day: string | null;
  subject_day_short: string | null;
  method: { baseline: string; spread: string; flagged_when: string; why_median: string };
  nothing_found_note: string;
}

/* ------------------------------------------------------------ the payload -- */

export interface InsightsBody {
  ok: true;
  settles_money: false;
  window: Window;
  history: History;
  days: DaysBlock;
  week: Block<WeekBlockBody>;
  same_weekday: Block<WeekdayBlockBody>;
  hours: Block<HoursBlockBody>;
  products: Block<ProductsBlockBody>;
  anomalies: Block<AnomaliesBlockBody>;
  chain: Chain;
  limits: string[];
  derived_from: string;
}

/**
 * The whole screen in one request.
 *
 * One fold of the chain rather than six: six independent folds could disagree
 * about a Tuesday at a midnight boundary, and the server slices a single one
 * for exactly that reason. Asking for the composite keeps that guarantee.
 */
export function insights(opts: { days?: number; day?: string } = {}): Promise<Result<InsightsBody>> {
  const q = new URLSearchParams();
  if (opts.days !== undefined) q.set('days', String(opts.days));
  if (opts.day !== undefined) q.set('day', opts.day);
  const tail = q.toString();
  return send<InsightsBody>(`/insights${tail ? `?${tail}` : ''}`);
}

/** The window lengths the screen offers. The server refuses anything under 15. */
export const WINDOWS = [15, 30, 60, 90] as const;
export type WindowDays = (typeof WINDOWS)[number];
