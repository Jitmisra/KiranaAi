/**
 * Every request the By-weight screen makes, in one place, with types.
 *
 * Two rules this module exists to keep, and one thing it deliberately cannot do:
 *
 *  1. THE BROWSER NAMES A WEIGHT; THE SERVER PRICES IT. `price()` and `line()`
 *     send a product id and a weight — grams as a whole number, or kilograms as
 *     the TEXT the shopkeeper typed — and every paisa in the answer is the
 *     server's. There is no function here that multiplies a price by anything.
 *     Kilograms go up as a string on purpose: "2.5" sent as a JSON number is a
 *     float by the time it is parsed, and the server reads the text digit by
 *     digit into grams instead.
 *  2. A REFUSAL IS A RESULT, NOT AN ERROR. `/weighed/*` answers `{ok:false,
 *     reason, detail}` with a 400 on purpose, so the body is parsed on non-2xx
 *     rather than thrown, and only a transport failure produces a refusal about
 *     the network.
 *
 * WHAT THE PAGE CANNOT DO: put a line on the bill, or make one chargeable. The
 * Till owns the basket, and the money service re-prices every basket from its
 * own per-unit price book, which does not yet price a weight. So a written
 * line is handed to the Till through `stashForTill()` below, and every answer
 * from the server carries `mintable: false` with the reason in `mint_note`.
 *
 * `send` is duplicated from `api.ts` rather than imported: it is module-private
 * there, and a new screen must not widen the till's own request layer to borrow
 * one function. The precedence rule — explicit `ok`, then an `error` string,
 * then the HTTP STATUS — is load-bearing and copied intact.
 *
 * NOTE ON DEV: `vite.config.ts` proxies an explicit allowlist of path prefixes
 * to the till on :8790. `/weighed` has to be in that list or every request here
 * 404s under `npm run dev` while working in the built site.
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

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

/* ------------------------------------------------------------- the shape -- */

/** What one preset weight costs for one product. Server's numbers. */
export interface WeighedExample {
  grams: number;
  /** "250 g", "1 kg" — the server's own rendering. */
  weight: string;
  line_paise: Paise;
  line_rupees: string;
  /** 0–999. How much of a paisa the floor dropped for this weight. */
  dropped_thousandths_of_a_paisa: number;
}

export interface WeighedRow {
  sku_id: string;
  name: string;
  /** False when the product was forgotten from the catalogue after being marked. */
  in_catalogue: boolean;
  price_per_kg_paise: Paise;
  price_per_kg_rupees: string;
  since: string;
  examples: WeighedExample[];
  /** The per-packet price the catalogue still holds, if the product is in it. */
  catalogue_price_paise?: Paise;
  catalogue_price_rupees?: string;
}

/** A priced product that is not yet sold by weight. */
export interface Markable {
  sku_id: string;
  name: string;
  price_paise: Paise;
  price_rupees: string;
}

export interface WeighedBook {
  count: number;
  items: WeighedRow[];
  markable: Markable[];
  /** False when the catalogue could not be read at all — different from empty. */
  catalogue_known: boolean;
  presets_grams: number[];
  limits: {
    min_grams: number;
    max_grams: number;
    min_price_per_kg_paise: Paise;
    max_price_per_kg_paise: Paise;
    max_weighed: number;
  };
  /** The remainder rule, in the server's words. */
  rule: string;
  mintable: false;
  mint_note: string;
  file: string;
}

export interface WeighedHealth {
  module: string;
  file: string;
  exists: boolean;
  lines_dir: string;
  audit: string;
  shop_dir: string;
  count: number;
  rule: string;
  presets_grams: number[];
  min_grams: number;
  max_grams: number;
  mintable: false;
  mint_note: string;
}

/** The line exactly as the Till's basket holds one. `qty` is always 1. */
export interface BasketLineForTill {
  sku_id: string;
  name: string;
  price_paise: Paise;
  qty: 1;
  by: 'weighed';
}

export interface PricedLine {
  sku_id: string;
  name: string;
  grams: number;
  weight: string;
  price_per_kg_paise: Paise;
  price_per_kg_rupees: string;
  line_paise: Paise;
  line_rupees: string;
  /** price × grams, before the floor — the exact figure, as an integer. */
  exact_thousandths_of_a_paisa: number;
  dropped_thousandths_of_a_paisa: number;
  rule: string;
  /** "4599 × 333 // 1000 = 1531" — the sum, shown. */
  arithmetic: string;
  basket_line: BasketLineForTill;
  mintable: false;
  mint_note: string;
  written: boolean;
}

export interface WrittenLine extends PricedLine {
  written: true;
  line_id: string;
  at: string;
  file: string;
  audited: boolean;
  note: string;
}

/** A weight as the shopkeeper gave it: whole grams, or kilograms as text. */
export type Weight = { grams: number } | { kg: string };

/* --------------------------------------------------------------- requests -- */

export const list = () => send<WeighedBook>('/weighed');

export const health = () => send<WeighedHealth>('/weighed/health');

export const one = (skuId: string) =>
  send<WeighedRow & { rule: string }>(`/weighed/${encodeURIComponent(skuId)}`);

/**
 * Mark a product as sold by weight, or change its per-kilo price.
 * `price_per_kg_rupees` is TEXT ("45.99"); the server parses it to paise.
 */
export const mark = (skuId: string, pricePerKgRupees: string) =>
  send<WeighedRow & {
    replaced: boolean;
    was_price_per_kg_paise: Paise | null;
    audited: boolean;
    file: string;
    rule: string;
    note: string;
  }>(`/weighed/${encodeURIComponent(skuId)}`, json('POST', { price_per_kg_rupees: pricePerKgRupees }));

export const unmark = (skuId: string) =>
  send<{ sku_id: string; removed: true; was_price_per_kg_paise: Paise; audited: boolean; remaining: number }>(
    `/weighed/${encodeURIComponent(skuId)}`,
    { method: 'DELETE' },
  );

/** Price a weight. Nothing is written; this is what the readout shows live. */
export const price = (skuId: string, weight: Weight) =>
  send<PricedLine>('/weighed/price', json('POST', { sku_id: skuId, ...weight }));

/** Price a weight AND write it down under a line id, on the weighed chain. */
export const line = (skuId: string, weight: Weight) =>
  send<WrittenLine>('/weighed/line', json('POST', { sku_id: skuId, ...weight }));

/** Read a written line back — the server's record, not this browser's memory. */
export const readLine = (lineId: string) =>
  send<WrittenLine>(`/weighed/line/${encodeURIComponent(lineId)}`);

/* ------------------------------------------------------- the hand-off -- */

/**
 * A written line, waiting for the Till.
 *
 * THIS PAGE IS NOT THE TILL AND MAY NOT TOUCH THE BASKET. The Till owns it. So
 * a line written here is parked in `sessionStorage` under one key and announced
 * on `window` under one event name, and the Till — once the orchestrator wires
 * it — drains the queue on mount and listens for the event while open. The Till
 * should re-read each line by `line_id` (`readLine`) before trusting the price
 * it finds here; the copy of `basket_line` is a convenience for display.
 *
 * `sessionStorage`, not `localStorage`: a weighed line belongs to the bill being
 * built in this tab, and a line that outlived the tab would land on tomorrow's
 * first customer.
 */
export interface PendingLine {
  line_id: string;
  at: string;
  basket_line: BasketLineForTill;
  weight: string;
  line_rupees: string;
}

export const PENDING_KEY = 'gawaah.weighed.pending';
export const PENDING_EVENT = 'gawaah:weighed-line';

function readPending(): PendingLine[] {
  try {
    const raw = sessionStorage.getItem(PENDING_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw) as unknown;
    if (!Array.isArray(arr)) return [];
    return arr.filter(
      (x): x is PendingLine =>
        !!x && typeof x === 'object' && typeof (x as PendingLine).line_id === 'string'
        && !!(x as PendingLine).basket_line,
    );
  } catch {
    return [];
  }
}

function writePending(rows: PendingLine[]): void {
  try {
    sessionStorage.setItem(PENDING_KEY, JSON.stringify(rows));
  } catch {
    // Storage can be absent or full. The line is still on the server, by id.
  }
}

/** Park a written line for the Till and say so. */
export function stashForTill(w: WrittenLine): PendingLine {
  const row: PendingLine = {
    line_id: w.line_id,
    at: w.at,
    basket_line: w.basket_line,
    weight: w.weight,
    line_rupees: w.line_rupees,
  };
  const rows = readPending().filter((r) => r.line_id !== row.line_id);
  rows.push(row);
  writePending(rows);
  try {
    dispatchEvent(new CustomEvent<PendingLine>(PENDING_EVENT, { detail: row }));
  } catch {
    // No window to announce on. The queue is still there.
  }
  return row;
}

/** What is waiting for the Till, without taking it. */
export const pendingForTill = (): PendingLine[] => readPending();

/** Take everything waiting for the Till, and empty the queue. For the Till. */
export function takePendingForTill(): PendingLine[] {
  const rows = readPending();
  writePending([]);
  return rows;
}

/** Drop one parked line — the shopkeeper changed their mind before the Till saw it. */
export function unstash(lineId: string): PendingLine[] {
  const rows = readPending().filter((r) => r.line_id !== lineId);
  writePending(rows);
  return rows;
}

/* ------------------------------------------------------------ display -- */

/**
 * 2000 -> "2 kg", 250 -> "250 g", 1250 -> "1.25 kg". Display only, integer
 * arithmetic only, and the same rendering the server uses for `weight` — so a
 * preset button reads the way the priced line will.
 */
export function describeGrams(grams: number): string {
  if (!Number.isInteger(grams) || grams < 0) return '—';
  if (grams < 1000) return `${grams} g`;
  const rem = grams % 1000;
  const kg = (grams - rem) / 1000;          // exact: the numerator is a multiple of 1000
  if (rem === 0) return `${kg} kg`;
  return `${kg}.${String(rem).padStart(3, '0').replace(/0+$/, '')} kg`;
}

/** The text a preset puts in the readout, in the unit the readout is in. */
export function presetText(grams: number, unit: 'kg' | 'g'): string {
  if (unit === 'g') return String(grams);
  const rem = grams % 1000;
  const kg = (grams - rem) / 1000;
  if (rem === 0) return String(kg);
  return `${kg}.${String(rem).padStart(3, '0').replace(/0+$/, '')}`;
}

/** 0–999 thousandths of a paisa as "0.467 paisa". Display only. */
export function droppedText(thousandths: number): string {
  if (!Number.isInteger(thousandths) || thousandths <= 0) return 'nothing';
  return `0.${String(thousandths).padStart(3, '0')} paisa`;
}
