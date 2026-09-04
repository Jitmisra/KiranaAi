/**
 * PARCHI (पर्ची) — the photographed bill. Every request the NEW FROM PHOTO
 * flow makes, in one place, with types; and the one decision the browser is
 * allowed to make, as a pure function a test can hold.
 *
 * THREE RULES IT KEEPS, in the order they matter:
 *
 *  1. THE BROWSER IS NEVER AN AUTHOR OF MONEY. Nothing here sends a cost, a
 *     quantity or a total. `parse()` sends PIXELS and receives a document the
 *     server has already read, matched and gated; `book()` sends the parchi id,
 *     which lines a person accepted and which product each is, and the server
 *     takes every figure from its own stored parse. A cost in the body has no
 *     field to land in.
 *
 *  2. THE GATE IS THE SERVER'S. `gate.ok` arrives decided. `readyToAccept`
 *     below can only say NO for reasons of its own (a confirm row nobody
 *     confirmed, a supplier nobody named); it can never say YES over a
 *     refused gate, and the server refuses again on ACCEPT regardless.
 *
 *  3. A REFUSAL IS A RESULT, NOT AN ERROR. `/parchi/*` answers `{ok:false,
 *     reason, detail}` with a 400 on purpose — one paisa off is the product
 *     working. The body is parsed on non-2xx rather than thrown.
 *
 * `send` is duplicated from `purchapi.ts` rather than imported, for the reason
 * that file gives: it is module-private there and a new flow must not widen
 * another screen's request layer to borrow a function.
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

/* ------------------------------------------------------------------ shapes -- */

export type LineStatus = 'proposed' | 'confirm' | 'no_match' | 'arithmetic_fails' | 'unreadable';
export type MatchStatus = 'proposed' | 'confirm' | 'none';

export interface Candidate {
  sku_id: string;
  name: string;
  score: number;
  why: string;
  sell_paise: Paise | null;
}

export interface Match {
  status: MatchStatus;
  sku_id: string | null;
  sku_name: string | null;
  score: number;
  why: string;
  candidates: Candidate[];
  query: string;
}

/** One printed line, as the SERVER read, priced and judged it. */
export interface ParsedLine {
  i: number;
  name: string;
  qty: number | null;
  rate: unknown;
  rate_paise: Paise | null;
  rate_rupees: string | null;
  amount: unknown;
  amount_paise: Paise | null;
  amount_rupees: string | null;
  computed_paise: Paise | null;
  computed_rupees: string | null;
  arithmetic: 'ok' | 'fails' | 'unreadable';
  arithmetic_detail: string | null;
  match: Match;
  status: LineStatus;
}

export interface Tax {
  label: string;
  amount: unknown;
  amount_paise: Paise | null;
  amount_rupees: string | null;
}

export interface Gate {
  ok: boolean;
  reason: string | null;
  detail: string | null;
  failing_lines: number[];
  lines_checked: number;
  sum_of_lines_paise: Paise;
  subtotal_printed: boolean;
  subtotal_paise: Paise | null;
  taxes: Tax[];
  tax_paise: Paise;
  expected_total_paise: Paise;
  printed_total: unknown;
  printed_total_paise: Paise | null;
  rule: string;
}

export interface LeftTheMachine {
  photograph: { bytes: number; mime: string; sha256: string };
  fields: string[];
  not_sent: string[];
  to: { provider: string; model: string; host: string };
  note: string;
}

export interface SupplierOnFile {
  supplier_id: string;
  name: string;
  phone: string;
}

export interface Parchi {
  parchi_id: string;
  at: string;
  image: { bytes: number; mime: string; sha256: string; file: string };
  model: string;
  provider: string;
  supplier: { name: string; phone: string; on_file: SupplierOnFile | null };
  invoice_no: string | null;
  date: string | null;
  date_printed: string;
  lines: ParsedLine[];
  gate: Gate;
  counts: Record<LineStatus | 'lines', number>;
  left_the_machine: LeftTheMachine;
  booked: { purchase_id: string; at: string } | null;
  uses_razorpay: false;
  add_product_route: string;
  audited?: boolean;
}

export interface Status {
  available: boolean;
  reason: string | null;
  detail: string | null;
  provider: string | null;
  model: string | null;
  what_leaves: string[];
  what_stays: string[];
  gate: string;
  typed_form: string;
  uses_razorpay: false;
}

export interface DayMargin {
  date: string;
  revenue_paise: Paise;
  margin_is_partial: boolean;
  covered_skus: number;
  covered_revenue_paise: Paise;
  margin_paise: Paise;
  margin_pct_of_price: string | null;
  uncovered_skus: string[];
  uncovered_revenue_paise: Paise;
}

export interface Booked {
  parchi_id: string;
  purchase: {
    purchase_id: string;
    supplier_name: string | null;
    invoice_no: string | null;
    date: string;
    units: number;
    total_paise: Paise;
    lines: Array<{ sku_id: string; name: string; units: number; cost_paise: Paise; line_paise: Paise }>;
  };
  supplier_added: SupplierOnFile | null;
  booked: { purchase_id: string; left_out: number[]; lines: Array<{ i: number; chosen_by: string }> };
  cost_known: { before: number; after: number; of: number };
  today: { before: DayMargin | null; after: DayMargin | null };
  note: string;
}

/* ---------------------------------------------------------------- requests -- */

export const status = () => send<Status>('/parchi/status');

/** The photograph, as bytes, to be read. Nothing else goes with it. */
export function parse(image: Blob, filename = 'bill.jpg') {
  const form = new FormData();
  form.set('image', image, filename);
  return send<Parchi>('/parchi/parse', { method: 'POST', body: form });
}

export interface AcceptedLine { i: number; sku_id: string }

export interface BookBody {
  lines: AcceptedLine[];
  supplier_id?: string;
  new_supplier?: { name: string; phone: string };
  date?: string;
  invoice_no?: string;
}

export const book = (parchiId: string, body: BookBody) =>
  send<Booked>(`/parchi/${encodeURIComponent(parchiId)}/book`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

/* ------------------------------------------------------------- the decision -- */

/** What a person has done to each line: kept it in, and which product it is. */
export interface LineChoice {
  /** Included in the booking. Never true for a line the gate failed. */
  include: boolean;
  /** The product chosen. Starts as the machine's proposal for proposed rows. */
  sku_id: string | null;
  /** A "confirm?" row a person has actually looked at and confirmed. */
  confirmed: boolean;
}

/** The defaults a parse arrives with: proposed rows in, confirm rows waiting,
    the rest out — and nothing the gate failed can ever be in. */
export function defaultChoices(lines: ReadonlyArray<ParsedLine>): Record<number, LineChoice> {
  const out: Record<number, LineChoice> = {};
  for (const l of lines) {
    const bookable = l.arithmetic === 'ok';
    out[l.i] = {
      include: bookable && (l.status === 'proposed' || l.status === 'confirm'),
      sku_id: l.match.sku_id,
      confirmed: l.status === 'proposed',
    };
  }
  return out;
}

export type NotReady =
  | { ready: true }
  | { ready: false; why: string };

/**
 * May ACCEPT fire? The FIRST reason it may not, in a shopkeeper's words.
 *
 * Pure, so it is tested. The order is the order a person fixes things: a
 * refused bill cannot be fixed on this screen at all, so it comes first; a
 * missing supplier is one box; a confirm row is a click; no lines is the
 * end. This function never overrides `gate.ok` — it can only add reasons.
 */
export function readyToAccept(
  doc: Pick<Parchi, 'gate' | 'lines'>,
  choices: Record<number, LineChoice>,
  supplier: { id: string | null; name: string; phone: string },
): NotReady {
  if (!doc.gate.ok) {
    return { ready: false, why: 'This bill does not add up, so nothing on it can be booked. Photograph the right bill, or type the invoice.' };
  }
  if (!supplier.id && !supplier.name.trim()) {
    return { ready: false, why: 'Say which supplier this bill is from.' };
  }
  if (!supplier.id && !supplier.phone.trim()) {
    return { ready: false, why: 'A new supplier needs a phone number — it is how a short delivery gets chased.' };
  }
  const kept = doc.lines.filter((l) => choices[l.i]?.include);
  if (kept.length === 0) {
    return { ready: false, why: 'No line is ticked. Tick the lines that matched a product.' };
  }
  for (const l of kept) {
    const c = choices[l.i];
    if (l.arithmetic !== 'ok') {
      return { ready: false, why: `Line ${l.i + 1} did not pass the arithmetic gate and cannot be booked.` };
    }
    if (!c?.sku_id) {
      return { ready: false, why: `Line ${l.i + 1} (${l.name}) has no product chosen. Choose one, or untick it.` };
    }
    if (l.status === 'confirm' && !c.confirmed) {
      return { ready: false, why: `Line ${l.i + 1} (${l.name}) is a guess — confirm which product it is, or untick it.` };
    }
  }
  return { ready: true };
}

/** The body ACCEPT sends: which lines, which product each, which supplier. */
export function bookBody(
  doc: Pick<Parchi, 'lines' | 'date' | 'invoice_no'>,
  choices: Record<number, LineChoice>,
  supplier: { id: string | null; name: string; phone: string },
  over: { date?: string; invoice_no?: string } = {},
): BookBody {
  const lines: AcceptedLine[] = [];
  for (const l of doc.lines) {
    const c = choices[l.i];
    if (c?.include && c.sku_id) lines.push({ i: l.i, sku_id: c.sku_id });
  }
  const body: BookBody = { lines };
  if (supplier.id) body.supplier_id = supplier.id;
  else body.new_supplier = { name: supplier.name.trim(), phone: supplier.phone.trim() };
  const date = over.date ?? doc.date ?? undefined;
  if (date) body.date = date;
  const inv = over.invoice_no ?? doc.invoice_no ?? undefined;
  if (inv) body.invoice_no = inv;
  return body;
}
