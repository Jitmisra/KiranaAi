/**
 * Every request the Shelf screen makes, in one place, with types.
 *
 * THERE IS NO MONEY IN THIS FILE. `gawaah/shelf.py` publishes no price, no
 * total and no valuation, and this page renders none: a facing is a count of
 * packets visible in the front row, and money has no business beside it.
 *
 * Two rules copied from `api.ts` and kept for the same reasons:
 *
 *  1. THE BROWSER IS NEVER AN AUTHOR. The page sends pixels and receives boxes,
 *     names and counts. When it teaches, it sends a REGION NUMBER and a name —
 *     the server holds the frame, cuts the crop, derives the vectors and reads
 *     the price through its own boundary. Nothing here computes a facing, a
 *     gap or a similarity; every figure on the screen was derived server-side.
 *  2. A REFUSAL IS A RESULT, NOT AN ERROR. `/shelf/*` answers `{ok:false,
 *     reason, detail}` with a 400 (404 for a frame no longer held) on purpose,
 *     so the body is parsed on non-2xx rather than thrown, and only a transport
 *     failure produces a refusal about the network.
 *
 * `send` is duplicated from `api.ts` rather than imported: it is module-private
 * there, and a new screen must not widen the till's own request layer to borrow
 * one function. The precedence rule — explicit `ok`, then an `error` string,
 * then the HTTP STATUS — is load-bearing and copied intact.
 *
 * NOTE ON DEV: `vite.config.ts` proxies an explicit allowlist of prefixes to the
 * till. `/shelf` has to be in that list, and `shelf.router` mounted on the till,
 * or every request here 404s. Neither file is in this screen's scope.
 */

import type { Result } from './api';

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

/* ------------------------------------------------------------------ shapes -- */

/** The state of the hash chain the shelf reads are recorded on. */
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
 * The limits, as sentences, read off the server. Printed verbatim: the number
 * on this page is only honest with its limit beside it, and the server is the
 * one that measured the limit.
 */
export interface Limits {
  front_row_only: string;
  touching_packets: string;
  code_only_products: string;
  not_a_stock_count: string;
  not_a_shelf: string;
  /** "Not seen here" is not "out of stock", and the list must not read as one. */
  missing_is_not_out_of_stock: string;
  /** Why the comparison wants a shelf name before it is worth anything. */
  comparison_needs_a_label: string;
  /** What rejecting a region does, and — the surprising half — what it does not. */
  rejection_teaches_nothing: string;
}

export type GapVerdict =
  | 'never_counted'
  | 'shelf_exceeds_figure'
  | 'face_matches_figure'
  | 'face_below_figure'
  | 'no_figure_available'
  | 'not_in_stock_rows';

/**
 * The facings beside the stock figure. `on_hand_units` is `gawaah/stock.py`'s
 * own derivation and `null` when the shelf was never counted — an absence,
 * never a zero. `sentence` is the server's own account of what follows, which
 * is the thing to print; `verdict` is for choosing an emphasis, never for
 * composing a second sentence.
 */
export interface StockGap {
  on_hand_units: number | null;
  basis: 'counted' | 'never_counted' | null;
  counted_at: string | null;
  derivation: string | null;
  difference: number | null;
  shelf_exceeds_figure: boolean;
  verdict: GapVerdict;
  sentence: string;
}

export type Box = [number, number, number, number];

/**
 * WHAT A BOX ON THE PICTURE IS, IN FOUR STATES, and they are four because
 * three of them would hide something:
 *
 *   named     the CAMERA recognised it against the shop's own vectors;
 *   by_hand   a PERSON named it, by teaching it or by correcting the counter.
 *             Counted as a facing, and never folded into `named`: a figure a
 *             person typed and a figure a camera derived are both true and are
 *             not the same kind of evidence;
 *   unnamed   seen, not matched. Amber, always, and never guessed;
 *   rejected  struck out by the shopkeeper as not a product at all. Drawn
 *             rather than deleted, so his own correction is visible as one.
 */
export type RegionState = 'named' | 'by_hand' | 'unnamed' | 'rejected';

export interface Region {
  region: number;
  box: Box;
  state: RegionState;
  sku_id: string | null;
  name: string | null;
  /**
   * The crop, for EVERY region and not only the ones the counter gave up on.
   * It is what a shopkeeper looks at to decide whether a name is right, and
   * the dialog that asks him exactly that used to have nothing in it to look
   * at, because a named region carried no crop.
   */
  crop_png_b64: string | null;
}

export interface Facing {
  sku_id: string;
  name: string;
  facings: number;
  boxes: Box[];
  regions: number[];
  by_code: number;
  by_appearance: number;
  /** Of `facings`, how many a person named. Zero on a read nobody has touched. */
  by_hand: number;
  appearance_said: string[];
  stock: StockGap;
  /** Facings on the last comparable read, or `null` when there was none. */
  previous_facings: number | null;
  /** now − then. `null` when there is nothing to compare against. */
  change: number | null;
  /** On this shelf now and not on the last read of it. */
  new_here: boolean;
}

/**
 * The same packet, seen twice. Two facings are two positions in a row and do
 * not overlap, so a region mostly inside a named one is its lower half, its
 * shadow, or the price label under it — measured at 73% and 75% against a
 * floor of 0% for any two boxes that were really two products.
 *
 * The region is NOT removed by this: it keeps its crop and can still be taught.
 */
export interface SamePacket {
  region: number;
  sku_id: string | null;
  name: string | null;
  inside: number;
  detail: string;
}

/** A region the camera could see and could not name. Never a price. */
export interface Unnamed {
  region: number;
  box: Box;
  state: RegionState;
  found_by: 'contour' | 'yolo' | 'code' | null;
  reason: string;
  detail: string | null;
  top1: number | null;
  top1_sku: string | null;
  code: string | null;
  crop_png_b64: string | null;
  same_packet_as?: SamePacket | null;
  /**
   * On a frame the counter would not COUNT, a region it nonetheless
   * recognised carries the name it matched, so the shopkeeper can see his own
   * stock was seen even though no facing was derived from it.
   */
  name_seen?: string | null;
  sku_id_seen?: string | null;
}

/** A region the shopkeeper struck out. Kept on the response, never dropped. */
export interface Rejected {
  region: number;
  box: Box;
  state: 'rejected';
  reason: string;
  detail: string | null;
  /** What the counter had called it before it was struck out, if anything. */
  was: string | null;
}

/**
 * A taught product this frame does not show.
 *
 * `verdict` orders the list by how much evidence there actually is that the
 * facing is empty, and the sentence is the server's own: `was_here` is the
 * strong one (same shelf, same camera, the packets are gone), `never_seen`
 * only says the frame does not show it, and `cannot_be_seen` says its absence
 * is evidence of nothing because the product was taught from a printed code
 * and has no appearance stored.
 */
export interface Missing {
  sku_id: string;
  name: string;
  taught_by_sight: boolean;
  previous_facings: number | null;
  on_hand_units: number | null;
  verdict: 'was_here' | 'was_here_elsewhere' | 'never_seen' | 'cannot_be_seen';
  sentence: string;
}

/** The read this one is set beside, and whether it is of the same shelf. */
export interface Previous {
  shelf_id: string | null;
  at: string | null;
  label: string | null;
  same_shelf: boolean;
  named: number;
  products: number;
  regions_seen: number;
  facings: Array<{ sku_id: string; facings: number }>;
}

export interface ShelfRead {
  /**
   * TRUE ONLY WHEN A FACING COUNT WAS ACTUALLY DERIVED. False exactly when
   * `abstained` is set, so a caller that reads one cannot get a wrong answer
   * from the other.
   */
  counted?: boolean;
  /** Why no count was produced. Present only when `counted` is false. */
  abstained?: {
    reason: string;
    detail: string;
    covers_frame_pct: number;
    saw: { box: number[]; label: string; score: number }[];
  } | null;
  mode: 'shelf';
  shelf_id: string;
  /** Which shelf this is, in the shopkeeper's own word. `null` if he did not say. */
  label: string | null;
  at: string;
  frame_px: [number, number];
  counts: {
    regions_seen: number;
    named: number;
    by_hand: number;
    unnamed: number;
    rejected: number;
    same_packet: number;
    products: number;
    missing: number;
    gone: number;
    corrections: number;
    shelf_exceeds_figure: number;
  };
  facings: Facing[];
  unnamed: Unnamed[];
  rejected: Rejected[];
  missing: Missing[];
  regions: Region[];
  previous: Previous | null;
  stock_figures: { available: boolean; reason: string | null; detail: string | null; source: string };
  annotated_png_b64: string | null;
  empty_shelf: boolean;
  held_for_seconds: number;
  limits: Limits;
  use_yolo: boolean;
  audited: boolean;
  chain_head: string | null;
  elapsed_ms: number;
  note: string;
}

export interface ShelfDescribe {
  module: 'shelf';
  taught: { by_sight: number | null; by_code_only: number | null; total: number | null; problem: string | null };
  detector: { proposers: string[]; identifies_products: boolean; note: string };
  stock_figures: { available: boolean; reason: string | null; detail: string | null; source: string };
  limits: Limits;
  reads_on_chain: number;
  last_read_at: string | null;
  /** Shelf names this counter has been given, most recently used first. */
  labels: string[];
  held_for_seconds: number;
  chain: Chain;
  counts_money: false;
  writes_stock: false;
}

export interface EarlierRead {
  at: string | null;
  shelf_id: string | null;
  label: string | null;
  frame_px: [number, number] | null;
  regions_seen: number;
  named: number;
  unnamed: number;
  products: number;
  counted: boolean;
  /** The figures here are the CORRECTED ones; the chain still holds both lines. */
  corrected: boolean;
  corrections: number;
  facings: Array<{ sku_id: string; facings: number; on_hand_units: number | null; difference: number | null }>;
  hash: string | null;
}

export interface ShelfCounts {
  count: number;
  matched: number;
  limit: number;
  reads: EarlierRead[];
  chain: Chain;
}

export interface TeachResult {
  shelf_id: string;
  region: number;
  sku_id: string;
  /** What the counter had called this region, on a correction. Absent on a teach. */
  was?: string | null;
  how: 'view_added' | 'product_taught';
  stored: {
    sku_id: string | null;
    name: string | null;
    n_views?: number | null;
    views_before?: number | null;
    views_after?: number | null;
    similarity_to_existing?: number | null;
    replaced_existing?: boolean | null;
    storage: string | null;
  };
  crop_png_b64: string | null;
  audited: boolean;
  detail: string;
  /**
   * THE WHOLE READING, RECOMPUTED BY THE SERVER. The page never patches a row
   * of its own: it replaces the read with this one, so every figure on screen
   * after a correction was derived where the first one was.
   */
  read: ShelfRead;
}

export interface RejectResult {
  shelf_id: string;
  region: number;
  was: string | null;
  /** True when this put a struck-out region back. A mis-tap is not permanent. */
  undone: boolean;
  /** Always false, and printed. A rejection corrects a read; it teaches nothing. */
  teaches_the_camera: false;
  audited: boolean;
  detail: string;
  read: ShelfRead;
}

/** A product the shop has taught, as `/shop` lists it. Only what the picker needs. */
export interface Product {
  sku_id: string;
  name: string;
  taught_with: string;
  n_views: number;
}

/* ---------------------------------------------------------------- requests -- */

export const describe = () => send<ShelfDescribe>('/shelf');

export const counts = (limit = 20) => send<ShelfCounts>(`/shelf/counts?limit=${limit}`);

/**
 * Count the facings on one photograph. The whole frame goes up — the point of
 * this gesture is that the operator has not told the counter where to look.
 */
export const count = (blob: Blob, opts?: { yolo?: boolean; label?: string }) => {
  const fd = new FormData();
  fd.append('image', blob, 'shelf.jpg');
  fd.append('yolo', opts?.yolo === false ? '0' : '1');
  // The shelf's name, when there is one. It is what makes the comparison with
  // the last read a comparison rather than two aisles subtracted.
  const label = (opts?.label ?? '').trim();
  if (label) fd.append('label', label);
  return send<ShelfRead>('/shelf/count', { method: 'POST', body: fd });
};

export interface TeachBody {
  region: number;
  sku_id: string;
  name?: string;
  /** A decimal STRING, never a number: a rupee is not a float. */
  price_rupees?: string;
  force?: boolean;
}

const post = <T>(url: string, body: unknown) =>
  send<T>(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });

/** Teach one unnamed region of a held read. The server holds the pixels. */
export const teach = (shelfId: string, body: TeachBody) =>
  post<TeachResult>(`/shelf/${encodeURIComponent(shelfId)}/teach`, body);

/**
 * The counter named a region and it named it wrong.
 *
 * Same body as `teach`. This one TEACHES the crop to the product named, so it
 * is not a relabelling of a screen — the next photograph is read by a counter
 * that has seen this packet from this angle.
 */
export const correct = (shelfId: string, body: TeachBody) =>
  post<TeachResult>(`/shelf/${encodeURIComponent(shelfId)}/correct`, body);

/**
 * This region is not a product: a price label, a bracket, a hand.
 *
 * It corrects THIS read and is written to the chain. It teaches the camera
 * nothing — there is no way to teach this counter that something is not a
 * product — and the response says so rather than letting the button imply it.
 */
export const reject = (shelfId: string, region: number, undo = false) =>
  post<RejectResult>(`/shelf/${encodeURIComponent(shelfId)}/reject`, { region, undo });

/** The taught catalogue, for the "a product it already knows" picker. */
export const products = async (): Promise<Result<{ skus: Product[] }>> => {
  const r = await send<{ count: number; skus: Product[] }>('/shop');
  if (!r.ok) return r;
  return { ok: true, skus: (r.skus ?? []).map((s) => ({
    sku_id: String(s.sku_id), name: String(s.name ?? s.sku_id),
    taught_with: String(s.taught_with ?? ''), n_views: Number(s.n_views ?? 0),
  })) };
};

/* ------------------------------------------------------------------ helpers -- */

/** "14:05" today, "Tue 14:05" this week, "3 Sep" otherwise. Local time. */
export function when(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (sameDay) return hm;
  const days = (now.getTime() - d.getTime()) / 86_400_000;
  if (days < 6) return `${d.toLocaleDateString([], { weekday: 'short' })} ${hm}`;
  return d.toLocaleDateString([], { day: 'numeric', month: 'short' });
}

/** A data URL for a base64 PNG the server drew. `null` stays `null`. */
export function pngUrl(b64: string | null | undefined): string | null {
  return b64 ? `data:image/png;base64,${b64}` : null;
}
