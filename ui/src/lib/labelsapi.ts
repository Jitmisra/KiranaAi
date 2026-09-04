/**
 * Every request the Labels screen makes, in one place, with types.
 *
 * Two rules this module keeps, and one thing it cannot do:
 *
 *  1. THE BROWSER IS NEVER AN AUTHOR. `plan()` sends sku ids, whole-number copy
 *     counts, a layout id and a cell to start at. It never sends a name or a
 *     price; the server reads both from the catalogue and puts them on paper.
 *     There is no function here that computes how many sheets a run is —
 *     `plan()` asks, and the answer is the server's arithmetic.
 *  2. A REFUSAL IS A RESULT. `/labels/*` answers `{ok:false, reason, detail}`
 *     with a 400 on purpose, so the body is parsed on non-2xx rather than
 *     thrown, and only a transport failure produces a refusal about the network.
 *
 * WHAT THE PAGE CANNOT DO: put anything on the code. The symbol encodes
 * `gawaah:<sku_id>`, built by the server; the page has no field in which to
 * say what a sticker should carry.
 *
 * `send` is duplicated from `api.ts` rather than imported: it is module-private
 * there, and a new screen must not widen the till's own request layer to borrow
 * one function. The precedence it encodes — explicit `ok`, then `error`, then
 * the HTTP status — is copied intact.
 *
 * NOTE ON DEV: `vite.config.ts` proxies an explicit allowlist of path prefixes
 * to the till. `/labels` has to be in that list or every request here 404s
 * under `npm run dev` while working in the built site.
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

const postJson = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

/* ------------------------------------------------------------- the shape -- */

/** One sticker sheet, as the stationer cuts it. Every figure is millimetres. */
export interface Layout {
  layout_id: string;
  name: string;
  label_w_mm: number;
  label_h_mm: number;
  cols: number;
  rows: number;
  per_page: number;
  left_mm: number;
  top_mm: number;
  right_mm: number;
  bottom_mm: number;
  pitch_x_mm: number;
  pitch_y_mm: number;
  gap_x_mm: number;
  gap_y_mm: number;
  /** The side of the code square on this label. */
  qr_mm: number;
  /** What is left beside the code for the name and the price. */
  text_mm: number;
  compatible: string;
  cut_lines: boolean;
  page: string;
}

export interface TalkerSize {
  size_id: string;
  name: string;
  w_mm: number;
  h_mm: number;
  page: string;
  per_page: number;
  cols: number;
  rows: number;
  code_mm: number;
}

export interface Layouts {
  count: number;
  layouts: Layout[];
  talker_sizes: TalkerSize[];
  limits: { max_copies: number; max_lines: number; max_labels: number; max_talker_copies: number };
  quiet_zone_modules: number;
  note: string;
}

/** One product as the label screen sees it. `price_paise` is the MARKED price. */
export interface ProductRow {
  sku_id: string;
  name: string;
  /** What the sticker prints: the shelf-edge price, offers not applied. */
  price_paise: Paise;
  price_rupees: string;
  /** What the till charges today, offers applied. What the talker prints. */
  charged_paise: Paise;
  charged_rupees: string;
  offer_today: boolean;
  taught_with: string;
  /** Taught from a printed code: it already has one, so a sticker is optional. */
  has_printed_code: boolean;
  qr_text: string;
  /** The existing single-sticker PNG, for a preview. */
  qr_png_url: string;
}

export interface Products {
  count: number;
  without_printed_code: number;
  offers_today: number;
  items: ProductRow[];
  price_on_label: 'marked';
  qr_prefix: string;
}

export interface PlanLine {
  sku_id: string;
  name: string;
  copies: number;
  price_paise: Paise;
  price_rupees: string;
  offer_today: boolean;
  charged_today_paise: Paise;
  qr_text: string;
  qr_modules: number;
  /** How large one module prints on this grid, in millimetres. */
  module_mm: number;
  /** The size the price is set at, fitted to the column by the server. */
  figure_pt: number;
}

/** The server's arithmetic for a run. Nothing here is computed in the browser. */
export interface Plan {
  layout: Layout;
  lines: PlanLine[];
  labels: number;
  pages: number;
  skipped: number;
  blank_on_last_page: number;
  cells_per_page: number;
  price_on_label: 'marked';
  offers_today: string[];
  /** The page to open and print. Built by the server from what was asked. */
  sheet_url: string;
  note: string;
}

export interface LabelsHealth {
  module: string;
  layouts: number;
  talker_sizes: number;
  audit_file: string;
  exists: boolean;
  lines: number;
  chain_ok: boolean;
  chain_error: string | null;
  head: string;
  shop_dir: string;
  qr_encoder: boolean;
  qr_prefix: string;
}

/* --------------------------------------------------------------- requests -- */

export const layouts = () => send<Layouts>('/labels/layouts');
export const products = () => send<Products>('/labels/products');
export const health = () => send<LabelsHealth>('/labels/health');

/**
 * How many labels and sheets a run would be, with no paper spent and nothing
 * written. `copies` are whole numbers; the server refuses anything else by name.
 */
export const plan = (body: {
  layout: string;
  items: Array<{ sku_id: string; copies: number }>;
  skip: number;
}) => send<Plan>('/labels/plan', postJson(body));

/** The shelf talker page for one product. `copies` omitted fills one sheet. */
export const talkerUrl = (skuId: string, size: string, copies?: number) => {
  const q = new URLSearchParams({ size });
  if (copies !== undefined) q.set('copies', String(copies));
  return `/labels/talker/${encodeURIComponent(skuId)}?${q.toString()}`;
};
