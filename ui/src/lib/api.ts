/**
 * Every request the till makes, in one place, with types.
 *
 * Two rules this module exists to keep:
 *
 *  1. THE BROWSER IS NEVER AN AUTHOR OF MONEY. There is no function here that
 *     sends a price, a payload or a SKU to the money path. `scan()` sends
 *     pixels and receives an id; `mint()` sends that id and a total the server
 *     already told us. The money service reloads the witness by id and
 *     re-derives every rupee from its own tables before it mints anything.
 *  2. A REFUSAL IS A RESULT, NOT AN ERROR. The server answers `{ok:false,
 *     reason}` with a 400 on purpose — that is the product working. So these
 *     wrappers parse the body on non-2xx instead of throwing, and only a
 *     genuine transport failure produces a thrown error.
 */

import type { ScanFrame, ScanItem, Box } from './counter';
import type { Paise } from './money';

export type Refusal = { ok: false; reason: string; detail?: string; hint?: string };
export type Ok<T> = { ok: true } & T;
export type Result<T> = Ok<T> | Refusal;

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
  // TWO SERVICES, TWO CONVENTIONS.
  //
  // The till answers {ok: true|false, reason}. paisa answers with NO `ok` FIELD
  // AT ALL: a success is the payload alone, a refusal carries {error, detail}.
  //
  // Read through the till's convention, a successful mint therefore looked like
  // `ok === undefined` — falsy — and the page filed a real, minted, paid-for
  // payment link as a refusal, then rendered it as an amber card with an empty
  // title because there was no `reason` either. The money path was broken end
  // to end and every individual request was a 200.
  //
  // So the precedence is: an explicit `ok` is authoritative (the till answers a
  // deliberate refusal as 400 + {ok:false}, and that is the product working);
  // then an `error` string; then THE HTTP STATUS.
  //
  // The status check is not belt-and-braces. FastAPI's own failures — a 422
  // validation error, a 500 — come back as `{"detail": ...}` with neither `ok`
  // nor `error`, so a rule that only looked at the body would mark a crash as a
  // SUCCESS and hand the caller a Result whose fields are all undefined. That is
  // the same shape of bug as the four this file already carries scar tissue for.
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

const form = (fields: Record<string, string | Blob | undefined | null>, file?: { blob: Blob; name: string }) => {
  const fd = new FormData();
  if (file) fd.append('image', file.blob, file.name);
  for (const [k, v] of Object.entries(fields)) {
    if (v === undefined || v === null || v === '') continue;
    fd.append(k, v);
  }
  return fd;
};

/* ---------------------------------------------------------------- health -- */

/**
 * These interfaces mirror what the server ACTUALLY returns.
 *
 * An earlier version of this file guessed: it read `health.catalog_size` and
 * `moneyHealth.reachable`, neither of which exists. Both silently became
 * `undefined`, and the status chips reported "0 taught" over a shop of seven
 * products and "gateway down" over a working gateway. Nothing threw; the page
 * simply lied. `e2e/contract.spec.ts` now asserts every field named here is
 * really present, so a rename on the server fails a test instead of quietly
 * turning a readout into fiction.
 */
export interface Health {
  service: string;
  buffer_px: [number, number];
  mat_mm: [number, number];
  px_per_mm: [number, number];
  marker_ids: number[];
  marker_mm: number;
  opencv: string;
  reference_loaded: boolean;
  model_weights: string;
  store_dir: string;
  identity_gates: { theta: number; phi: number; tau_mm: number; phi_appearance_only: number };
  gates: { max_scale_err_pct: number; max_persp_index: number; min_area_mm2: number };
  limits: { max_upload_bytes: number; max_side_px: number };
  dependencies: Record<string, { available: boolean; reason: string | null }>;
}
export const health = () => send<Health>('/health');

export interface MoneyHealth {
  module: string;
  mode: string;
  key_id: string;
  key_secret_configured: boolean;
  webhook_secret_configured: boolean;
  sessions: number;
  intents: number;
  intents_needing_human: number;
  intents_escalated: number;
  intents_by_state: Record<string, number>;
  payment_links: number;
  ledger_lines: number;
  ledger_head: string;
  price_book_entries: number;
}
export const moneyHealth = () => send<MoneyHealth>('/api/money/health');

/* ------------------------------------------------------------------ shop -- */

export interface Sku {
  sku_id: string;
  name: string;
  price_paise: Paise;
  price_rupees: string;
  /** A SINGLE number of millimetres, or null when taught with no mat. Not a pair. */
  footprint_mm: number | null;
  n_views: number;
  vector_dim: number;
  thumb_png: string | null;
  taught_with: string;
  appearance_only: boolean;
  size_check: string;
  phi_used: number | null;
  storage: string;
  warning: string | null;
  /** Every code bound to this SKU. A product can carry more than one. */
  codes: string[];
}
export const shop = () => send<{ count: number; skus: Sku[]; settles_money: boolean; money_note: string }>('/shop');
export const forget = (skuId: string) => send<{ removed: string }>(`/shop/${encodeURIComponent(skuId)}`, { method: 'DELETE' });

/* --------------------------------------------------------------- looking -- */

export type ReadMode = 'basket' | 'plain_photo' | 'mat';


/**
 * The AIMING response. The array is `codes` and the count is `count` — NOT
 * `items`/`codes_found`, which is what this interface used to claim.
 *
 * That mistake did not fail loudly. `d.items.map(...)` threw on every poll, the
 * rejection was swallowed by the interval, and the preview sat on
 * "NO CODE READABLE YET" **while the server was decoding the barcode fine**.
 * The only visible symptom was a console error repeating a few hundred times.
 * Fifth field-shape bug in this file; every one of them lied rather than broke.
 */
export interface CodesFound {
  count: number;
  codes: Array<{
    payload: string;
    format: string;
    box: Box;
    px_across: number;
    read_by: string;
    sku_id: string | null;
  }>;
  frame_px: [number, number];
  /** Present only when nothing decoded — says what would help, in measured numbers. */
  hint?: string;
  elapsed_ms?: number;
  candidates?: number;
}
/** Aiming only — "is this code readable right now". Never becomes a bill. */
export const codes = (blob: Blob) =>
  send<CodesFound>('/codes', { method: 'POST', body: form({}, { blob, name: 'aim.jpg' }) });

/**
 * ONE FRAME, PRICED. This is the till's loop — the request the page actually
 * sends several times a second, so it is the one worth measuring.
 *
 * `mode` decides what the server is allowed to conclude from the image:
 *   basket      read codes. An identifier that was READ, not a likeness judged.
 *   plain_photo appearance only — no millimetres, so a stricter similarity bar.
 *
 * The whole frame goes up in code mode and only the chosen rectangle in
 * appearance mode. That is invariant 4, and it is asymmetric on purpose: a code
 * can be anywhere on a packet, but a likeness must not carry the room with it.
 */
export const recognise = (blob: Blob, mode: ReadMode) =>
  send<ScanFrame>('/recognise', {
    method: 'POST',
    body: form({ mode, thumbs: '0' }, { blob, name: 'frame.jpg' }),
  });

/* ------------------------------------------------------- the whole counter -- */

/**
 * SEVERAL PRODUCTS, ONE FRAME.
 *
 * `recognise('plain_photo')` names ONE item, because a photo with no mat has
 * one subject by construction. A customer puts four things down at once, and
 * asking the shopkeeper to photograph them one at a time is asking them to do
 * the work the camera was supposed to do.
 *
 * The server answers this by separating two questions a single vision model
 * conflates: WHERE the things are (class-agnostic regions) and WHICH product
 * each one is (the shop's own taught vectors, at the same cosine gate).
 */
export interface CounterItem {
  box: [number, number, number, number];
  /** 'code' — an identifier was READ. 'appearance' — a similarity was judged. */
  how: 'code' | 'appearance';
  found_by?: 'contour' | 'yolo';
  code?: string;
  sku_id: string | null;
  name: string | null;
  price_paise: Paise | null;
  price_rupees?: string;
  reason: string;
  detail?: string;
  top1?: number | null;
  top1_sku?: string | null;
}

export interface CounterRead {
  mode: 'counter';
  frame_px: [number, number];
  items: CounterItem[];
  counts: {
    regions_seen: number;
    named: number;
    /**
     * THE NUMBER THAT MATTERS MOST. How many regions this counter could SEE
     * and could not NAME. It is not an accusation and it is not a guess — it
     * is the counter saying there is something here I cannot price, which is
     * the honest form of "the camera saw three items and the bill has two".
     */
    unnamed: number;
    by_code: number;
    by_appearance: number;
  };
  total_paise: Paise;
  total_rupees: string;
  elapsed_ms: number;
}

/** Read every product on the counter at once. */
export const readCounter = (blob: Blob, opts?: { yolo?: boolean }) =>
  send<CounterRead>('/counter', {
    method: 'POST',
    body: form({ yolo: opts?.yolo === false ? '0' : '1' }, { blob, name: 'counter.jpg' }),
  });

/* --------------------------------------------------------------- witness -- */

export interface Witness {
  scan_id: string;
  witnessed_paise: Paise;
  witnessed_rupees: string;
  total_paise: Paise;
  chargeable: boolean;
  /** The server's OWN account of the refusal. Read this; never invent one. */
  why_not_chargeable: string | null;
  counts: { named: number; amber: number };
  codes_found: number;
  distinct_codes: number;
  items: ScanItem[];
  amber: ScanItem[];
  elapsed_ms?: number;
  reason?: string;
}
/**
 * Write down what is on the counter, server-side, under an id.
 * The page is given no field in which to assert anything about it.
 */
export const scan = (blob: Blob) =>
  send<Witness>('/scan', { method: 'POST', body: form({}, { blob, name: 'counter.jpg' }) });

/* --------------------------------------------------------------- teaching -- */

export interface TeachFields {
  sku_id: string;
  name: string;
  price_rupees: string;
  barcode?: string;
  mode?: 'plain_photo' | 'basket';
  force?: string;
  /**
   * WHO DECIDED WHERE THE PRODUCT IS IN THIS PICTURE.
   *
   * Absent means "nobody — find it yourself", and the server segments the
   * photograph and refuses by name when it cannot. `user_drawn` means the
   * operator dragged a rectangle around the product, so the region is already
   * decided and the server must not re-derive it. It is a claim about
   * provenance, not a permission: everything that can still be wrong with the
   * IMAGE — flat, empty, too few pixels — is still refused.
   */
  region?: 'user_drawn';
}
export interface Taught {
  stored: { sku_id: string; name: string; price_paise: Paise; code?: string | null };
  measured?: Record<string, unknown>;
  collision?: Record<string, unknown> | null;
  appearance_only?: boolean;
}
export const enrol = (blob: Blob, fields: TeachFields) =>
  send<Taught>('/enrol', {
    method: 'POST',
    body: form(fields as unknown as Record<string, string>, { blob, name: 'teach.jpg' }),
  });

/** A 1x1 PNG header. `/enrol` always takes an image; when a code is typed it is never read. */
export const EMPTY_PNG = () =>
  new Blob([new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10])], { type: 'image/png' });

/**
 * ANOTHER ANGLE OF A PRODUCT ALREADY TAUGHT.
 *
 * Appearance recognition survives light and a 180-degree flip and degrades on
 * rotation — measured, cosine against the one taught view:
 *
 *     0 deg 1.000 | 5 deg 0.874 | 10 deg 0.775 | 25 deg 0.715
 *
 * THE GATE THOSE NUMBERS WERE TAKEN AGAINST WAS 0.92 AND NO LONGER EXISTS.
 * `gawaah/identity.py` gates on DEFAULT_PHI 0.55, or PHI_APPEARANCE_ONLY 0.60
 * when appearance is the only discriminator, so at today's gate 25 deg clears
 * it and the old sentence here — "falls apart on rotation" — overstates what
 * its own figures show. Left corrected rather than deleted: the measurement is
 * real, and a reader who finds 0.92 in a comment and greps for it finds
 * nothing, which is how a stale number costs an afternoon.
 *
 * The reason to add views is unchanged and is the last line, not the gate:
 * nobody puts a packet down at the angle it was photographed at, and two
 * rotated views took the same product to 1.000 at every angle out to 45 deg.
 *
 * It only ever ADDS to what a product looks like. Price, name and footprint are
 * decisions a person made and a camera is not a reason to revise them.
 */
export interface ViewAdded {
  sku_id: string;
  name: string;
  views_before: number;
  views_after: number;
  added: number;
  similarity_to_existing: number;
  floor: number;
}

export const addView = (blob: Blob, skuId: string, opts?: { force?: boolean }) =>
  send<ViewAdded>(`/shop/${encodeURIComponent(skuId)}/view`, {
    method: 'POST',
    body: form(opts?.force ? { force: '1' } : {}, { blob, name: 'view.jpg' }),
  });

/* ----------------------------------------------------------------- money -- */

export interface Minted {
  session_id: string;
  amount_paise: Paise;
  short_url: string;
  reference_id?: string;
  state: string;
}
/**
 * Ask paisa to mint. Three fields, none of them evidence.
 * paisa loads the witness by `scan_id` and re-prices it from its own book.
 *
 * THE SCAN ID IS NESTED under `scan`. The forward reads
 * `body["scan"]["scan_id"]`; sent flat as `scan_id` it arrives as the empty
 * string and paisa correctly refuses with `scan_not_found`, which reads like a
 * broken witness rather than a malformed request. Verified against the real
 * endpoint in e2e/everything.spec.ts.
 */
export const mint = (body: { session_id: string; amount_paise: Paise; scan_id: string }) =>
  send<Minted>('/api/money/mint', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: body.session_id,
      amount_paise: body.amount_paise,
      scan: { scan_id: body.scan_id },
    }),
  });

export interface SessionState {
  session_id: string;
  state: string;
  paid: boolean;
  total_rupees?: string;
  total_paise?: Paise;
  /**
   * LIVENESS OF THE INBOUND PATH — not a payment fact.
   *
   * `webhooks_seen` counts every callback that reached the money service,
   * including ones rejected for a bad signature, because the question is "can
   * anything get here at all". A pay screen that knows only "not green yet"
   * shows the same spinner for a customer who has not paid and for a tunnel
   * that has been dead since Saturday. It span for 78 s on a payment that had
   * in fact settled.
   */
  webhooks_seen?: number;
  last_webhook_at?: string | null;
}
export const session = (id: string) => send<SessionState>(`/api/money/session/${encodeURIComponent(id)}`);

/** The payment QR is a RENDER OF THE GATEWAY'S OWN LINK. Never a payload we built. */
export const paymentQrUrl = (sessionId: string) => `/qr/link/${encodeURIComponent(sessionId)}`;
export const productQrUrl = (skuId: string) => `/qr/${encodeURIComponent(skuId)}`;

/* --------------------------------------------------------------- session -- */

/**
 * A till session id. `Date.now()` is fine here — this is a correlation handle
 * the shop chooses, not a security token and not money. The gateway issues the
 * only identifier that matters.
 */
export function newSessionId(): string {
  const r = Math.random().toString(36).slice(2, 10);
  return `till_${Date.now().toString(36)}_${r}`;
}


/* ------------------------------------------------- the enrolment gate, live -- */

/**
 * SAAF on an ordinary camera — the second capability that never needed the mat.
 *
 * `frames` is the CONTACT SHEET: one row per frame with the measurement that
 * decided it. The brain's own serialiser dropped these, which is why the panel
 * built to draw them has never had anything to draw.
 */
export interface SaafFrameReport {
  index: number;
  used: boolean;
  /** Carries the measurement inline, e.g. `blur:12.3`. Good to show, useless to group on. */
  reason: string;
  /** The bare reason code, e.g. `blur`. This is the one to count. */
  code: string;
  vlap: number | null;
  sat_frac: number | null;
  blur_score: number | null;
  shift_px: number | null;
}

export interface SaafStackBody {
  used: number;
  rejected: number;
  burst: number;
  mean_shift_px: number | null;
  subpixel_diversity: number | null;
  sharpness_gain: number | null;
  warning: string;
  reference_index: number | null;
  frames: SaafFrameReport[];
  gates: Record<string, number>;
}

export const saafStack = (blobs: Blob[]) => {
  const fd = new FormData();
  blobs.forEach((b, i) => fd.append(`image${i}`, b, `f${i}.jpg`));
  return send<SaafStackBody>('/saaf/stack', { method: 'POST', body: fd });
};


