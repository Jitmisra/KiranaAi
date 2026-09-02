/**
 * The three management screens' requests, in one place, with types.
 *
 * Same two rules as `api.ts`, for the same reasons:
 *
 *  1. THE BROWSER IS NEVER AN AUTHOR. Nothing here sends a price, a total or a
 *     SKU's identity. The one write on these screens — an opening stock count —
 *     sends a number of PACKETS, which settles no money and prices nothing.
 *  2. A REFUSAL IS A RESULT, NOT AN ERROR. `/manage/*` answers `{ok:false,
 *     reason, detail}` with a 400 on purpose, so the body is parsed on non-2xx
 *     instead of thrown, and only a transport failure produces a refusal about
 *     the network.
 *
 * `send` is duplicated from api.ts rather than imported: it is module-private
 * there, and exporting it would widen that module's surface for this one. The
 * precedence rule it encodes — explicit `ok`, then an `error` string, then the
 * HTTP STATUS — is load-bearing and is copied intact. FastAPI's own 422 comes
 * back as `{"detail": ...}` with neither `ok` nor `error`, and a rule that only
 * read the body would file a validation crash as a success.
 *
 * NOTE ON DEV: `vite.config.ts` proxies an explicit allowlist of path prefixes
 * to the till on :8790. `/manage` has to be in that list or every request here
 * 404s under `npm run dev` while working perfectly in the built site, which is
 * a confusing way to lose an afternoon.
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

/* ------------------------------------------------------------- the chain -- */

/**
 * The state of the hash chain that produced everything else on the page.
 *
 * It rides on every response on purpose. A bill book derived from a chain that
 * does not verify is not a bill book, and the one place a shopkeeper must never
 * have to go looking for that fact is the page showing him the numbers.
 *
 * `exists: false` is NOT a failure — a counter installed this morning has no
 * log. `ok: false` is.
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

/* ----------------------------------------------------------- the bill book -- */

/** A line the counter priced. `price_paise` is integer paise, always. */
export interface BillLine {
  item_id: string;
  sku_id: string;
  price_paise: number | null;
  price_rupees: string | null;
}

/**
 * A line the counter REFUSED to price and excluded from the total.
 *
 * It carries no price and never will: that is invariant 7, not a gap in the
 * data. A screen that filled this in with a zero or a dash would turn an honest
 * abstention into a silent guess.
 */
export interface ExcludedLine {
  item_id: string;
  sku_id: string;
  reason: string | null;
}

export interface BillSummary {
  session_id: string;
  at: string | null;
  total_paise: number;
  total_rupees: string;
  lines: number;
  excluded_lines: number;
  items: BillLine[];
  excluded: ExcludedLine[];
  settled: boolean;
  settled_at: string | null;
  settled_by: 'webhook' | 'kernel' | null;
  state: string | null;
  minted: boolean;
  payment_link_id: string | null;
  payment_id: string | null;
  refused: boolean;
}

export interface HistoryBody {
  bills: BillSummary[];
  count: number;
  matched: number;
  limit: number;
  since: string | null;
  unparsed_timestamps: number;
  sessions_in_ledger: number;
  chain: Chain;
}

export interface BillEvent {
  ts: string | null;
  module: string | null;
  event: string | null;
  reason: string | null;
  from: string | null;
  to: string | null;
  item_id: string | null;
}

export interface BillRefusal {
  ts: string | null;
  module: string | null;
  reason: string | null;
  requested_paise: number | null;
  server_total_paise: number | null;
  session_total_paise: number | null;
}

export interface BillWebhook {
  ts: string | null;
  reason: string | null;
  razorpay_event: string | null;
  event_id: string | null;
  amount_paise: number | null;
  to: string | null;
}

export interface BillDetail {
  session_id: string;
  at: string | null;
  opened_at: string | null;
  total_paise: number;
  total_rupees: string;
  line_items: Array<BillLine & { reason: string | null; abstained: boolean; at: string | null; counted: boolean }>;
  excluded: Array<ExcludedLine & { abstained: boolean; at: string | null; counted: boolean; price_paise: null }>;
  closed: boolean;
  minted: boolean;
  settled: boolean;
  settled_at: string | null;
  settled_by: 'webhook' | 'kernel' | null;
  state: string | null;
  payment_link_id: string | null;
  payment_id: string | null;
  refusals: BillRefusal[];
  webhooks: BillWebhook[];
  events: BillEvent[];
  lines_sum_paise: number;
  total_agrees: boolean;
  chain: Chain;
}

/* ------------------------------------------------------------ the shelves -- */

/** How a product came to be in the catalogue. Three ways, not equivalent. */
export type TaughtBy = 'mat_measured' | 'appearance_only' | 'product_code_only' | null;

export interface InventoryRow {
  sku_id: string;
  name: string | null;
  price_paise: number | null;
  price_rupees: string | null;
  footprint_mm: number | null;
  taught_by: TaughtBy;
  taught_label: string;
  n_views?: number;
  codes?: string[];
  /** Committed into a basket that CLOSED. What a shopkeeper means by "sold". */
  billed_count: number;
  last_billed_at: string | null;
  /** The subset a signature-verified webhook turned PAID. Invariant 2. */
  settled_count: number;
  last_settled_at: string | null;
  /** Times the counter saw this and refused to price it. */
  amber_count: number;
  /** The shopkeeper's own count, and when he made it. Null means not counted. */
  opening_stock_units?: number | null;
  opening_stock_counted_at?: string | null;
  billed_since_count?: number | null;
  remaining_units?: number | null;
  in_catalogue: boolean;
}

export interface InventoryBody {
  count: number;
  items: InventoryRow[];
  sold_but_not_in_catalogue: InventoryRow[];
  counted_skus: number;
  stock_tracking: string;
  stock_note: string;
  stock_problem: string | null;
  catalogue_problems: Array<{ file: string; detail: string }>;
  orphan_code_bindings: string[];
  store_dir: string;
  now: string;
  chain: Chain;
}

/* ------------------------------------------------------------- the counter -- */

export interface SettingsBody {
  recognition: {
    phi: number;
    theta: number;
    tau_mm: number;
    phi_appearance_only: number;
    source: string;
    library_defaults: { phi: number; theta: number; tau_mm: number; phi_appearance_only: number };
  };
  mat: {
    sheet: string;
    width_mm: number;
    height_mm: number;
    markers: number;
    marker_ids: number[];
    marker_mm: number;
    margin_mm: number;
    marker_centres_mm: number[][];
    rectified_buffer_px: number[];
    max_scale_error: number;
    max_persp_index: number;
    max_tilt_deg: number;
  };
  catalogue: {
    count: number;
    by_taught: Record<string, number>;
    codes_bound: number;
    orphan_code_bindings: string[];
    problems: Array<{ file: string; detail: string }>;
    dir: string;
    gates_from_disk: boolean;
  };
  money: {
    reachable: boolean;
    base_url: string;
    status: number;
    mode: string | null;
    /** 'rzp_live' / 'rzp_test'. The account tail is dropped upstream. */
    key_id_prefix: string | null;
    /** BOOLEANS ONLY. No value, no prefix of one, no length. */
    key_secret_configured: boolean | null;
    webhook_secret_configured: boolean | null;
    sessions: number | null;
    intents: number | null;
    intents_needing_human: number | null;
    price_book_entries: number | null;
    detail: string | null;
  };
  /**
   * Is anything still able to reach this counter?
   *
   *   never   — no webhook has EVER arrived. Nothing can turn a bill green.
   *   silent  — it worked once and has been quiet a long time.
   *   live    — one arrived recently.
   *   unknown — the money service did not answer, so we cannot say.
   */
  webhook: {
    status: 'never' | 'silent' | 'live' | 'unknown';
    headline: string;
    webhooks_seen: number | null;
    last_webhook_at: string | null;
    last_green_webhook_at: string | null;
    silent_for_seconds: number | null;
    silent_after_seconds: number;
  };
  ledger: {
    path: string;
    exists: boolean;
    head: string;
    lines: number;
    chain_ok: boolean;
    error: string | null;
    sessions: number;
    bills_closed: number;
    bills_settled: number;
  };
}

/* --------------------------------------------------------------- requests -- */

export const history = (opts: { limit?: number; since?: string } = {}) => {
  const q = new URLSearchParams();
  if (opts.limit !== undefined) q.set('limit', String(opts.limit));
  // URLSearchParams encodes the '+' in a UTC offset. Without it the server sees
  // a space where the offset should be and refuses a timestamp the shopkeeper
  // copied out of this very product.
  if (opts.since) q.set('since', opts.since);
  const qs = q.toString();
  return send<HistoryBody>(`/manage/history${qs ? `?${qs}` : ''}`);
};

export const bill = (sessionId: string) =>
  send<BillDetail>(`/manage/history/${encodeURIComponent(sessionId)}`);

export const inventory = () => send<InventoryBody>('/manage/inventory');

export const settings = () => send<SettingsBody>('/manage/settings');

/**
 * Record the shopkeeper's own count of one product.
 *
 * Integer units. The server refuses a float, a string and a bool by name rather
 * than coercing — half a packet is not a thing a shelf holds — so the caller
 * must send a number it has already checked.
 */
export const setStock = (skuId: string, units: number) =>
  send<{ sku_id: string; units: number; counted_at: string; reason: string; detail: string }>(
    `/manage/inventory/${encodeURIComponent(skuId)}/stock`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ units }),
    },
  );

/* ---------------------------------------------------------------- shared -- */

/**
 * Format paise for a management screen, without ever throwing.
 *
 * `rupees()` REFUSES a float, a negative, and anything over ₹10,00,000 — and it
 * is right to, because at a till those are money bugs rather than large bills,
 * and a till should stop rather than render one.
 *
 * These pages are the other case. They are the shopkeeper's only window onto a
 * ledger that has gone wrong, so a figure that fails that check has to be SHOWN
 * — as the raw paise the chain actually holds, named as out of range. Throwing
 * here would blank the whole page and leave him with no way to see the bad
 * number at all, which is the failure these screens exist to prevent.
 *
 * The value is never quietly clamped, rounded or reformatted into something
 * plausible. It is reported verbatim or not at all.
 */
export function money(paise: number | null | undefined): string {
  if (paise === null || paise === undefined) return '—';
  try {
    return rupees(paise);
  } catch {
    return `${paise} paise — outside the range this till will price`;
  }
}

/** A timestamp as the shopkeeper reads a clock, not as the ledger writes one. */
export function when(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('en-IN', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: true,
  });
}

/** Plain English for a gap in seconds. Integer arithmetic, like the server's. */
export function ago(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—';
  const s = Math.trunc(seconds);
  if (s < 0) return `${-s}s in the future`;
  if (s < 120) return `${s} seconds`;
  const m = Math.trunc(s / 60);
  if (m < 120) return `${m} minutes`;
  const h = Math.trunc(m / 60);
  if (h < 48) return `${h} hours`;
  return `${Math.trunc(h / 24)} days`;
}

/* ----------------------------------------------------------- the day brief -- */

/** One day's numbers, every one counted from the chain for that window. */
export interface DayBrief {
  bills: number;
  revenue_paise: number;
  revenue_rupees: string;
  /** Integer floor — an average describes, but it is still money-shaped. */
  average_paise: number;
  average_rupees: string;
  settled_count: number;
  settled_paise: number;
  settled_rupees: string;
  awaiting_count: number;
  awaiting_paise: number;
  awaiting_rupees: string;
  excluded_lines: number;
  first_bill_at: string | null;
  last_bill_at: string | null;
}

export interface TodayBody {
  date: string;
  today: DayBrief;
  /** The SAME derivation asked about the previous window — never a cached delta. */
  yesterday: DayBrief;
  top_sellers: Array<{
    sku_id: string;
    name: string;
    units: number;
    revenue_paise: number;
    revenue_rupees: string;
    still_in_catalogue: boolean;
  }>;
  webhook: SettingsBody['webhook'];
  /** Field names READ OFF THE SERVER, not invented — the sixth shape bug in
      this codebase was an interface that claimed `lines` where the server says
      `lines_verified`, and it rendered `undefined` in a trust caption. */
  chain: { ok: boolean; exists: boolean; lines_verified: number;
           lines_readable: number; head: string | null; error: string | null };
  derived_from: string;
}

/** "Aaj kitna hua?" — the day, from the chain. `day` reads any past date. */
export const today = (day?: string) =>
  send<TodayBody>(`/manage/today${day ? `?day=${encodeURIComponent(day)}` : ''}`);
