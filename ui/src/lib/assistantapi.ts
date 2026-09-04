/**
 * Every request the Assistant screen makes, in one place, with types.
 *
 * The screen it serves is a conversation with `gawaah/assistant.py` — MUNSHI,
 * the counter's own shopkeeper's clerk. Three rules shape this file, and two of
 * them are the same rules the rest of the till obeys:
 *
 *  1. THE PAGE SENDS A SENTENCE AND NOTHING ELSE. `ask()` has exactly one
 *     payload field of substance — `text` — plus which input it came from. It
 *     cannot send a sku, a quantity, a price or a line, and the server refuses
 *     the whole request (`client_tried_to_author_the_bill`) if a body ever
 *     carries a key that looks like one. There is no function here that could
 *     write that body, on purpose: the browser is never an author.
 *  2. A REFUSAL IS A RESULT, NOT AN ERROR. `/assistant/*` answers `{ok:false,
 *     reason, detail}` with a 400 deliberately — a refused sentence is the
 *     product working — so the body is parsed on non-2xx rather than thrown,
 *     and only a transport failure produces a refusal about the network.
 *  3. NO KEY IS A FIRST-CLASS STATE, NOT A BROKEN ONE. With no `XAI_API_KEY`
 *     the counter answers on its own Hinglish parser and says `brain: "local"`.
 *     Nothing in this file reads, sends, stores or displays a key; there is no
 *     field for one, because a key typed into a browser is a key in a browser's
 *     history.
 *
 * `send` is duplicated from `api.ts` rather than imported — it is module-private
 * there, and a new screen must not widen the till's own request layer to borrow
 * one function. The precedence rule it encodes (explicit `ok`, then an `error`
 * string, then THE HTTP STATUS) is load-bearing and copied intact: FastAPI's own
 * 422 comes back as `{"detail": ...}` with neither `ok` nor `error`, and a rule
 * that only read the body would file a validation crash as a success.
 *
 * NOTE ON DEV AND ON MOUNTING: `vite.config.ts` proxies an explicit allowlist of
 * path prefixes to the till on :8790, and `/assistant` has to be in that list or
 * every request here 404s under `npm run dev` while working in the built site.
 * The router itself also has to be included by the till process; until it is,
 * `health()` answers `assistant_not_mounted` and the screen says so rather than
 * rendering an empty conversation.
 */

import type { Ok, Refusal, Result } from './api';
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
      // A 404 here has ONE overwhelmingly likely cause and it is worth naming:
      // the assistant router exists in the repo and has not been mounted by the
      // process serving this page. "HTTP 404" would send a reader to the
      // browser's network tab to learn something this line can just say.
      if (res.status === 404) {
        return {
          ok: false,
          reason: 'assistant_not_mounted',
          detail: `${url} is not served by this counter. gawaah/assistant.py exists but its `
            + 'router has not been included by the till process, so there is nothing to ask.',
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

/* ------------------------------------------------------------- the brains -- */

export type Brain = 'local' | 'grok';

/**
 * WHY THE MODEL DID NOT ANSWER — attached to an answer the LOCAL parser gave.
 *
 * The server splits transport failure from contract violation on purpose: an
 * unreachable provider falls back and names itself here, while a model that
 * invented a tool or a price is a refusal. So a `grok_error` beside an answer
 * means "the line was down and the shop kept working", which is a thing the
 * screen must say out loud rather than quietly degrade.
 */
export interface GrokError {
  reason: string;
  detail: string;
}

/** Present on every /assistant/ask response, answer or refusal alike. */
export interface BrainBlock {
  brain?: Brain;
  model?: string | null;
  key_present?: boolean;
  grok_error?: GrokError | null;
}

/* ------------------------------------------------------------------ health -- */

export interface AssistantHealth {
  settles_money: boolean;
  /** Which brain would answer the next sentence. */
  brain: Brain;
  /** A BOOLEAN. There is no route in this program that returns the key itself. */
  key_present: boolean;
  model: string | null;
  base_url: string;
  tools: string[];
  products_priced: number;
  catalogue_problem: { reason: string; detail: string } | null;
  sources: string[];
  sends_to_the_model: string;
  note: string;
}

export const health = () => send<AssistantHealth>('/assistant/health');

/* ------------------------------------------------------------------- tools -- */

export interface ToolSchema {
  type: string;
  function: {
    name: string;
    description: string;
    parameters: { type: string; properties: Record<string, unknown>; required: string[] };
  };
}

export interface ToolsBody {
  count: number;
  tools: ToolSchema[];
  system_prompt: string;
  model: string;
  note: string;
}

/**
 * EXACTLY WHAT IS SENT TO THE MODEL. Fetched only when the shopkeeper opens the
 * disclosure that shows it, because a privacy claim you cannot inspect is a
 * slogan. The payload is this prompt, these schemas and one sentence — no
 * catalogue, no price, no order, no takings, no stock.
 */
export const tools = () => send<ToolsBody>('/assistant/tools');

/* ------------------------------------------------------------------- asking -- */

export interface ProposalLine {
  sku_id: string;
  name: string;
  qty: number;
  unit_paise: Paise;
  unit_rupees: string;
  line_paise: Paise;
  line_rupees: string;
  taught_with: string;
  /** Only when an offer is live on this product: the shelf-edge price. */
  marked_paise?: Paise;
  marked_rupees?: string;
  off_paise?: Paise;
}

/**
 * A PROPOSAL, NEVER A BILL.
 *
 * The server resolves the shopkeeper's words to a sku of its own, prices it from
 * the shop's own offer-aware catalogue, writes the result down under an id and
 * appends one line to the assistant's own hash chain. `accepted` is false in
 * every proposal this counter has ever written, because accepting is an act a
 * person performs on the till and there is no endpoint that does it.
 */
/**
 * THREE KINDS, ONE SHAPE. Format 2 added `kind` and, for the two that are not
 * bill lines, the thing proposed and the ONE endpoint that would make it real:
 *
 *   bill             `lines` — held for the till, which writes the line
 *   stock_movement   `movement` — accepted at /stock/{sku}/{in|out}
 *   expense          `expense`  — accepted at /expenses
 *
 * `accept_by` is the server's own instruction, body included, integer paise
 * and all. The browser FORWARDS it when a person presses; it never composes
 * one. A format-1 proposal (no `kind`) is a bill proposal.
 */
export type ProposalKind = 'bill' | 'stock_movement' | 'expense';

export interface ProposedMovement {
  sku_id: string;
  name: string;
  direction: 'in' | 'out';
  units: number;
  reason: string;
  reason_label: string;
  note: string;
}

export interface ProposedExpense {
  amount_paise: Paise;
  amount_rupees: string;
  category: string;
  category_label: string;
  note: string;
  paid_with: string;
}

export interface AcceptBy {
  method: 'POST';
  path: string;
  body: Record<string, unknown>;
}

export interface Proposal {
  format: number;
  kind?: ProposalKind;
  proposal_id: string;
  at: string;
  brain: string;
  accepted: boolean;
  lines: ProposalLine[];
  total_paise: Paise;
  total_rupees: string;
  /** Weight read as packets, a dozen multiplied out. Shown, never hidden. */
  caution: string | null;
  note: string;
  /** Whether the proposal reached the assistant's own audit chain. */
  audited?: boolean;
  movement?: ProposedMovement;
  expense?: ProposedExpense;
  accept_by?: AcceptBy;
}

export interface AskAnswer extends Required<BrainBlock> {
  settles_money: boolean;
  /** The sentence as the server received it. */
  heard: string;
  source: string;
  /** One of the six tool names. */
  tool: string;
  arguments: Record<string, unknown>;
  /** The answer in one or two plain sentences. Shown verbatim. */
  answer: string;
  proposal: Proposal | null;
  data: Record<string, unknown> | null;
  note: string;
}

export type AskRefusal = Refusal & BrainBlock;
export type AskResult = Ok<AskAnswer> | AskRefusal;

export const TOOL_ADD = 'add_to_bill';
export const TOOL_ORDERS = 'list_pending_orders';
export const TOOL_TAKINGS = 'todays_takings';
export const TOOL_FIND = 'find_product';
export const TOOL_LOW_STOCK = 'low_stock';
export const TOOL_PRICE = 'price_of';

/** The server's cap on one sentence. The box counts down against this. */
export const MAX_TEXT = 400;

/**
 * One sentence in, one answer out. No history is sent and none is kept server
 * side: this is deliberately not a chat with a memory, so nothing said earlier
 * can change what a later sentence resolves to.
 */
export function ask(text: string, source: 'text' | 'voice'): Promise<AskResult> {
  return send<AskAnswer>('/assistant/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    // TWO FIELDS. Adding a third that named a sku, a quantity or a price would
    // be refused by the server by name — and rightly.
    body: JSON.stringify({ text, source }),
  });
}

/** Read a proposal back by id. It is still not a bill and still settles nothing. */
export const proposal = (id: string) =>
  send<{ proposal: Proposal }>(`/assistant/proposal/${encodeURIComponent(id)}`);

/* ------------------------------------------------- reading the data blocks -- */
//
// `data` differs per tool and arrives as unknown JSON. These readers are the one
// place that knows each shape, and every one of them ABSTAINS — a field that is
// missing or the wrong type comes back null and the screen omits that row
// rather than rendering `undefined` or, worse, a zero. A zero is a claim.

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

export interface ProductFacts {
  sku_id: string;
  name: string;
  price_paise: Paise | null;
  taught_with: string | null;
  marked_paise: Paise | null;
  off_paise: Paise | null;
}

export function readProduct(data: unknown): ProductFacts | null {
  const d = rec(data);
  const sku = str(d.sku_id);
  if (!sku) return null;
  return {
    sku_id: sku,
    name: str(d.name) ?? sku,
    price_paise: int(d.price_paise),
    taught_with: str(d.taught_with),
    marked_paise: int(d.marked_paise),
    off_paise: int(d.off_paise),
  };
}

export interface TakingsFacts {
  date: string | null;
  bills: number | null;
  revenue_paise: Paise | null;
  settled_paise: Paise | null;
  settled_count: number | null;
  awaiting_paise: Paise | null;
  awaiting_count: number | null;
  average_paise: Paise | null;
  /** Set only when the audit chain stopped verifying, with the line it stopped at. */
  chain_break: { lines_checked: number | null; error: string | null } | null;
}

export function readTakings(data: unknown): TakingsFacts | null {
  const d = rec(data);
  const bills = int(d.bills);
  if (bills === null) return null;
  const chain = rec(d.chain);
  const broken = chain.ok === false;
  return {
    date: str(d.date),
    bills,
    revenue_paise: int(d.revenue_paise),
    settled_paise: int(d.settled_paise),
    settled_count: int(d.settled_count),
    awaiting_paise: int(d.awaiting_paise),
    awaiting_count: int(d.awaiting_count),
    average_paise: int(d.average_paise),
    chain_break: broken
      ? { lines_checked: int(chain.lines_checked), error: str(chain.error) }
      : null,
  };
}

export interface OrderRow {
  order_id: string;
  status: string;
  total_paise: Paise | null;
  lines: number | null;
  name: string | null;
  paid: boolean;
}

export interface OrdersFacts {
  pending: number;
  total_paise: Paise | null;
  orders: OrderRow[];
  listed: number;
}

export function readOrders(data: unknown): OrdersFacts | null {
  const d = rec(data);
  const pending = int(d.pending);
  if (pending === null) return null;
  const rows: OrderRow[] = [];
  for (const raw of list(d.orders)) {
    const o = rec(raw);
    const id = str(o.order_id);
    if (!id) continue;
    rows.push({
      order_id: id,
      status: str(o.status) ?? 'unknown',
      total_paise: int(o.total_paise),
      lines: int(o.lines),
      name: str(o.name),
      paid: o.paid === true,
    });
  }
  return {
    pending,
    total_paise: int(d.total_paise),
    orders: rows,
    listed: int(d.listed) ?? rows.length,
  };
}

export interface StockRow {
  sku_id: string;
  name: string;
  remaining_units: number;
  billed_since_count: number | null;
}

export interface StockFacts {
  threshold_units: number | null;
  low: StockRow[];
  /** Products nobody has ever counted. Reported as uncounted, never as zero. */
  uncounted: number | null;
  counted: number | null;
}

export function readLowStock(data: unknown): StockFacts | null {
  const d = rec(data);
  if (d.low === undefined && d.uncounted === undefined) return null;
  const rows: StockRow[] = [];
  for (const raw of list(d.low)) {
    const r = rec(raw);
    const sku = str(r.sku_id);
    const remaining = int(r.remaining_units);
    if (!sku || remaining === null) continue;
    rows.push({
      sku_id: sku,
      name: str(r.name) ?? sku,
      remaining_units: remaining,
      billed_since_count: int(r.billed_since_count),
    });
  }
  return {
    threshold_units: int(d.threshold_units),
    low: rows,
    uncounted: int(d.uncounted),
    counted: int(d.counted),
  };
}

/* --------------------------------------------------- handing over to the till -- */
//
// WHAT ACCEPTING CAN AND CANNOT DO.
//
// The assistant has no endpoint that turns a proposal into a bill line, and that
// is deliberate: the till owns the basket, and a second thing that can write to
// it is a second thing that can disagree with it. So ACCEPT records the
// shopkeeper's decision in THIS BROWSER and hands the lines to the till screen.
// Nothing here is money and nothing here is authority — a consumer must re-price
// every sku from the shop's own catalogue, exactly as the till already does for
// a line the camera proposed. The paise below are carried for one purpose: so
// the till can refuse a line whose price has moved since it was proposed.

const HELD_KEY = 'gawaah.assistant.accepted.v1';

export interface HeldLine {
  sku_id: string;
  name: string;
  qty: number;
  /** What it was proposed at. FOR COMPARISON, never for billing. */
  proposed_unit_paise: Paise;
}

export interface HeldBatch {
  proposal_id: string;
  /** When the person accepted it, ISO, from this browser's clock. */
  accepted_at: string;
  proposed_total_paise: Paise;
  lines: HeldLine[];
}

function readHeld(): HeldBatch[] {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(HELD_KEY);
  } catch {
    // Storage disabled or full. Not an error worth interrupting anyone over:
    // the conversation still works, the handover does not.
    return [];
  }
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    const out: HeldBatch[] = [];
    for (const b of list(parsed)) {
      const batch = rec(b);
      const id = str(batch.proposal_id);
      const total = int(batch.proposed_total_paise);
      if (!id || total === null) continue;
      const lines: HeldLine[] = [];
      for (const l of list(batch.lines)) {
        const line = rec(l);
        const sku = str(line.sku_id);
        const qty = int(line.qty);
        const unit = int(line.proposed_unit_paise);
        if (!sku || qty === null || qty < 1 || unit === null) continue;
        lines.push({ sku_id: sku, name: str(line.name) ?? sku, qty, proposed_unit_paise: unit });
      }
      if (lines.length === 0) continue;
      out.push({
        proposal_id: id,
        accepted_at: str(batch.accepted_at) ?? '',
        proposed_total_paise: total,
        lines,
      });
    }
    return out;
  } catch {
    return [];
  }
}

/** Everything a person has accepted here and not yet cleared. Newest last. */
export const heldForTill = (): HeldBatch[] => readHeld();

export type HeldResult = { ok: true; batches: HeldBatch[] } | Refusal;

/**
 * Record an acceptance. Idempotent on `proposal_id`, so a double press of a
 * button on a slow phone cannot hold the same lines twice.
 */
export function holdForTill(p: Proposal): HeldResult {
  const batch: HeldBatch = {
    proposal_id: p.proposal_id,
    accepted_at: new Date().toISOString(),
    proposed_total_paise: p.total_paise,
    lines: p.lines.map((l) => ({
      sku_id: l.sku_id,
      name: l.name,
      qty: l.qty,
      proposed_unit_paise: l.unit_paise,
    })),
  };
  const kept = readHeld().filter((b) => b.proposal_id !== batch.proposal_id);
  const next = [...kept, batch];
  try {
    localStorage.setItem(HELD_KEY, JSON.stringify(next));
  } catch (e) {
    return {
      ok: false,
      reason: 'this browser would not keep the accepted lines',
      detail: `${String(e)}. Nothing was billed either way — add the lines on the till by hand.`,
    };
  }
  return { ok: true, batches: next };
}

export function clearHeld(): HeldResult {
  try {
    localStorage.removeItem(HELD_KEY);
  } catch (e) {
    return { ok: false, reason: 'this browser would not clear the accepted lines', detail: String(e) };
  }
  return { ok: true, batches: [] };
}

/** THE UNDO for a held batch: let one proposal's lines go, leave the rest. */
export function unholdForTill(proposalId: string): HeldResult {
  const next = readHeld().filter((b) => b.proposal_id !== proposalId);
  try {
    if (next.length === 0) localStorage.removeItem(HELD_KEY);
    else localStorage.setItem(HELD_KEY, JSON.stringify(next));
  } catch (e) {
    return { ok: false, reason: 'this browser would not let the held lines go', detail: String(e) };
  }
  return { ok: true, batches: next };
}

/* ------------------------------------------- accepting the other two kinds -- */
//
// A stock movement and an expense are accepted at THEIR OWN endpoints, which
// the proposal names in `accept_by`. Both are the shopkeeper's word written to
// the shop's own log; neither is money moving through this counter. The body
// is the server's, forwarded whole: this file has no function that could
// compose one, and the readers that draw the result abstain on anything the
// server did not say.

/** Forward one server-authored acceptance, or one server-shaped undo. */
export function post(path: string, body: Record<string, unknown>): Promise<Result<Record<string, unknown>>> {
  return send<Record<string, unknown>>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function acceptProposal(by: AcceptBy): Promise<Result<Record<string, unknown>>> {
  if (by.method !== 'POST' || !by.path.startsWith('/')) {
    return Promise.resolve({
      ok: false,
      reason: 'accept_by_not_understood',
      detail: `the proposal says to accept it with ${by.method} ${by.path}, which is not a route on this counter.`,
    });
  }
  return post(by.path, by.body);
}
