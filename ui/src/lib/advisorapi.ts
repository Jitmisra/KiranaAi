/**
 * Every request the Advisor screen makes, in one place, with types.
 *
 * The screen it serves is a CALL with `gawaah/advisor.py` — SALAAHKAAR, the
 * advisor a shopkeeper can talk to. Three rules shape this file, and two of
 * them are the same rules the rest of the till obeys:
 *
 *  1. THE PAGE SENDS A SENTENCE AND A CALL ID, AND NOTHING ELSE. `say()` has
 *     exactly three payload fields — `text`, `source`, `session_id` — and the
 *     server refuses the whole request (`client_tried_to_author_the_bill`) if a
 *     body ever carries a key that looks like a figure. There is no function
 *     here that could write such a body: the browser is never an author.
 *  2. A REFUSAL IS A RESULT, NOT AN ERROR. `/advisor/*` answers `{ok:false,
 *     reason, detail}` with a 400 (404 for a call that has expired) on purpose,
 *     so the body is parsed on non-2xx rather than thrown.
 *  3. NO KEY IS A FIRST-CLASS STATE. With no `XAI_API_KEY` the counter reads
 *     its figures and speaks them, and every answer says it cannot REASON about
 *     them — `reasoned: false`, `cannot_reason_because` — rather than producing
 *     advice from nowhere. Nothing here reads, sends, stores or displays a key.
 *
 * WHAT IS DIFFERENT FROM THE ASSISTANT, in the response: `spoken` is the one
 * string the page says aloud; `advice` is the model's phrasing when there was
 * one and null when there was not; `left_the_machine` lists, by field name,
 * exactly what was sent to the model for this turn — and is null when nothing
 * was. The page shows that list rather than a slogan.
 *
 * `send` is duplicated from `api.ts` for the reason `assistantapi.ts` gives: it
 * is module-private there, and a new screen must not widen the till's own
 * request layer to borrow one function. The precedence rule (explicit `ok`,
 * then an `error` string, then THE HTTP STATUS) is copied intact.
 *
 * NOTE ON DEV AND ON MOUNTING: `vite.config.ts` proxies an explicit allowlist
 * of prefixes and `/advisor` has to be in it or every request here 404s under
 * `npm run dev`. The router also has to be included by the till process; until
 * it is, `health()` answers `advisor_not_mounted` and the screen says so.
 */

import type { Ok, Refusal, Result } from './api';
import type { Paise } from './money';
import type { Brain, GrokError } from './assistantapi';

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
      if (res.status === 404) {
        return {
          ok: false,
          reason: 'advisor_not_mounted',
          detail: `${url} is not served by this counter. gawaah/advisor.py exists but its `
            + 'router has not been included by the till process, so there is nobody on the line.',
        };
      }
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

/* ------------------------------------------------------------------ health -- */

export interface AdvisorHealth {
  settles_money: boolean;
  brain: Brain;
  /** A BOOLEAN. No route on this counter returns the key itself. */
  key_present: boolean;
  /** Whether a model will phrase advice. False means figures only. */
  reasons: boolean;
  model: string | null;
  base_url: string;
  tools: string[];
  products_priced: number;
  catalogue_problem: { reason: string; detail: string } | null;
  sessions_live: number;
  keeps: { turns: number; for_s: number; on_disk: boolean };
  /**
   * Whether the provider can VOICE the answer — a natural voice fetched once
   * per sentence and cached on the till — and why not when it cannot. The
   * browser's own voice is always there underneath.
   */
  voice?: {
    available: boolean;
    model: string | null;
    voice: string | null;
    languages: string[];
    why_not: string | null;
    sends: string;
  };
  sends_to_the_model: string;
  cannot_reason_because: string | null;
  note: string;
}

export const health = () => send<AdvisorHealth>('/advisor/health');

/* -------------------------------------------------------------------- saying -- */

export const TOOL_ORDERS = 'list_pending_orders';
export const TOOL_TAKINGS = 'todays_takings';
export const TOOL_FIND = 'find_product';
export const TOOL_LOW_STOCK = 'low_stock';
export const TOOL_PRICE = 'price_of';
export const TOOL_MARGIN = 'todays_margin';

/** The server's cap on one sentence. */
export const MAX_TEXT = 400;

/** What the server sent to the model for this turn, by NAME. Null: nothing. */
export interface LeftTheMachine {
  /** Sentences of this call that went out — the shopkeeper's own words. */
  sentences: number;
  /** Earlier spoken answers that went with them, for context. */
  answers: number;
  /** Dotted field names of the one tool result sent to phrase the answer. */
  fields: string[];
}

export interface SayAnswer {
  settles_money: boolean;
  session_id: string;
  /** This turn's number on the call, counted from 1. */
  turn: number;
  /** How many earlier turns were remembered when this one was answered. */
  context_turns: number;
  resumed: boolean;
  /** Set when the id the page sent was not found: the call had expired. */
  previous_call: 'expired_or_unknown' | null;
  expires_in_s: number;
  heard: string;
  source: string;
  /** Null when the model answered a general question with no tool behind it. */
  tool: string | null;
  arguments: Record<string, unknown>;
  /** The counter's own sentence, derived on this machine. */
  answer: string;
  /** The model's phrasing, or null. Every figure in it was checked. */
  advice: string | null;
  /** THE ONE STRING TO SAY ALOUD. `advice` when there is one, else `answer`. */
  spoken: string;
  /** The language the page asked for, echoed. Absent when it asked for none. */
  lang?: string | null;
  /** Did a tool run on this machine produce the figures? */
  grounded: boolean;
  /** Did a model phrase or reason about them? */
  reasoned: boolean;
  data: Record<string, unknown> | null;
  context: { carried_product: string | null };
  left_the_machine: LeftTheMachine | null;
  cannot_reason_because: string | null;
  brain: Brain;
  model: string | null;
  key_present: boolean;
  grok_error: GrokError | null;
  note: string;
}

export type SayRefusal = Refusal & {
  session_id?: string;
  brain?: Brain;
  model?: string | null;
  key_present?: boolean;
  grok_error?: GrokError | null;
};
export type SayResult = Ok<SayAnswer> | SayRefusal;

/**
 * One sentence on a call. Omit `sessionId` to start one; pass the id that came
 * back to stay on it. THREE FIELDS. A fourth that named a sku, a quantity or a
 * price would be refused by the server by name — and rightly.
 */
export function say(text: string, source: 'text' | 'voice', sessionId: string | null, lang?: string): Promise<SayResult> {
  const body: Record<string, string> = { text, source };
  if (sessionId) body.session_id = sessionId;
  // The language the answer should be PHRASED in. The figures are the tool's
  // whatever the script; only the words around them move.
  if (lang) body.lang = lang;
  return send<SayAnswer>('/advisor/say', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/* ------------------------------------------------------------------ the call -- */

export interface KeptTurn {
  at: string;
  you: string;
  tool: string | null;
  spoken: string;
  reasoned: boolean;
}

export interface CallView {
  session_id: string;
  started_at: string;
  turns: KeptTurn[];
  turn_count: number;
  kept: number;
  keeps_at_most: number;
  expires_after_s: number;
  on_disk: boolean;
}

/** Read a call's kept turns back from the server's memory. */
export const call = (id: string) =>
  send<CallView>(`/advisor/session/${encodeURIComponent(id)}`);

/** Hang up. The server forgets the call; there was nothing on disk to delete. */
export const hangUp = (id: string) =>
  send<{ session_id: string; turns_forgotten: number }>(
    `/advisor/session/${encodeURIComponent(id)}/end`,
    { method: 'POST' },
  );

/* ------------------------------------------------- reading the margin block -- */
//
// The other tools' data blocks are read by the assistant's own readers
// (`readTakings`, `readOrders`, `readLowStock`, `readProduct`), imported by the
// screen. The margin is this advisor's own tool, so its reader lives here and
// ABSTAINS the same way: a missing or mistyped field is a row not drawn, never
// a zero. A zero is a claim.

const num = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null;
const int = (v: unknown): number | null => {
  const n = num(v);
  return n !== null && Number.isInteger(n) ? n : null;
};
const str = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null);
const rec = (v: unknown): Record<string, unknown> =>
  v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

export interface MarginRow {
  sku_id: string;
  name: string;
  units: number;
  revenue_paise: Paise | null;
  cost_known: boolean;
  margin_paise: Paise | null;
  /** Integer tenths as a string, as the server formats it. Null when unknown. */
  margin_pct_of_price: string | null;
  below_cost: boolean;
}

export interface MarginFacts {
  date: string | null;
  bills: number;
  revenue_paise: Paise | null;
  covered_units: number | null;
  covered_revenue_paise: Paise | null;
  covered_cost_paise: Paise | null;
  margin_paise: Paise | null;
  margin_pct_of_price: string | null;
  uncovered_units: number | null;
  uncovered_revenue_paise: Paise | null;
  margin_is_partial: boolean;
  items: MarginRow[];
  chain_break: { lines_checked: number | null; error: string | null } | null;
}

export function readMargin(data: unknown): MarginFacts | null {
  const d = rec(data);
  const bills = int(d.bills);
  if (bills === null) return null;
  const cov = rec(d.covered);
  const unc = rec(d.uncovered);
  const chain = rec(d.chain);
  const rows: MarginRow[] = [];
  for (const raw of list(d.items)) {
    const r = rec(raw);
    const sku = str(r.sku_id);
    const units = int(r.units);
    if (!sku || units === null) continue;
    rows.push({
      sku_id: sku,
      name: str(r.name) ?? sku,
      units,
      revenue_paise: int(r.revenue_paise),
      cost_known: r.cost_known === true,
      margin_paise: int(r.margin_paise),
      margin_pct_of_price: str(r.margin_pct_of_price),
      below_cost: r.below_cost === true,
    });
  }
  return {
    date: str(d.date),
    bills,
    revenue_paise: int(d.revenue_paise),
    covered_units: int(cov.units),
    covered_revenue_paise: int(cov.revenue_paise),
    covered_cost_paise: int(cov.cost_paise),
    margin_paise: int(cov.margin_paise),
    margin_pct_of_price: str(cov.margin_pct_of_price),
    uncovered_units: int(unc.units),
    uncovered_revenue_paise: int(unc.revenue_paise),
    margin_is_partial: d.margin_is_partial === true,
    items: rows,
    chain_break: chain.ok === false
      ? { lines_checked: int(chain.lines_checked), error: str(chain.error) }
      : null,
  };
}


/* ------------------------------------------------------------------ the voice -- */

/** A sentence, voiced: WHERE it is, on this origin. `cached` is true when the
    till already had it on disk — nothing left the machine for this one. */
export interface Voiced {
  /** Same-origin: `/advisor/voice/<sha256>.wav`. The sentence is not in it. */
  url: string;
  cached: boolean;
  model: string | null;
  voice: string | null;
  chars: number;
}

/** Ask the till to voice one sentence (it asks the provider only the first
    time the words are said) and say where the sound is. On any refusal the
    caller uses the browser's own voice. */
export function speak(text: string, lang: string): Promise<Result<Voiced>> {
  return send<Voiced>('/advisor/speak', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, lang }),
  });
}
