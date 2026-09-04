/**
 * Correcting a taught product, and naming the shop. Every request in one place.
 *
 * Four rules this module exists to keep:
 *
 *  1. THE BROWSER IS NEVER AN AUTHOR. Nothing here sends paise, a vector, a
 *     footprint or a storage location. `editSku` sends the three TEXT fields a
 *     shopkeeper typed — a name, a rupee string and a printed code — and the
 *     server validates them, converts the money and decides where they land.
 *  2. THE SKU ID IS NOT A FIELD. It goes in the PATH and nowhere else. It is
 *     what the code bindings, the orders and every bill already printed refer
 *     to; the server refuses a body that tries to move it, and this module
 *     gives no way to send one.
 *  3. RUPEES GO UP AS A STRING. `12.50` as a JavaScript number is a float that
 *     has already lost before any check could run. The one place paise are
 *     turned back into a rupee string for an input box is `rupeesForInput`,
 *     and it divides a number that is already a multiple of 100.
 *  4. A REFUSAL IS A RESULT, NOT AN ERROR. The server answers `{ok:false,
 *     reason}` with a 400 on purpose — that is the product working. So these
 *     wrappers parse the body on non-2xx instead of throwing.
 *
 * `send` is a near-copy of the one in `lib/api.ts`, and that is deliberate:
 * that module does not export it, and this screen must not edit the till's own
 * request layer to borrow one function. The precedence rules below are the
 * same ones, for the same reasons — the comments there record five separate
 * bugs where a response shape was guessed and the page lied instead of broke.
 */

import type { Paise } from './money';

export type Refusal = { ok: false; reason: string; detail?: string };
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
  // An explicit `ok` is authoritative; then the HTTP status. FastAPI's own
  // failures — a 422, a 500 — carry `{"detail": ...}` with no `ok` at all, and
  // a rule that read only the body would file a crash as a SUCCESS and hand
  // the caller an object whose every field is undefined.
  if (body && body.ok === undefined) {
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

const jsonBody = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

/* --------------------------------------------------------- editing a sku -- */

/**
 * The three fields an edit may carry, all of them TEXT.
 *
 * ABSENT AND EMPTY ARE DIFFERENT INSTRUCTIONS and the server draws that
 * distinction: a key that is not sent means "leave this alone", and an empty
 * `code` means "this product has no printed code". So only send what the
 * shopkeeper actually changed — sending every field on every save would rebind
 * a code nobody touched.
 */
export interface SkuEdit {
  name?: string;
  /** Rupees as the shopkeeper typed them: `12`, `12.50`. Never a number. */
  price_rupees?: string;
  /** The printed code. `''` clears every code bound to this product. */
  code?: string;
}

export interface SkuSnapshot {
  name: string;
  price_paise: Paise | null;
  price_rupees: string | null;
  codes: string[];
}

export interface CodeChange {
  code: string | null;
  bound: string | null;
  /** Every code this product carried that the edit dropped. Named, never silent. */
  unbound: string[];
  action: 'bound' | 'rebound' | 'cleared' | 'unchanged' | 'untouched';
}

export interface SkuEdited {
  sku_id: string;
  reason: string;
  /** Which of name / price / code actually moved. Empty means nothing did. */
  changed: string[];
  /** Which catalogue the write landed in. Both, when the product is in both. */
  stored_in: string[];
  before: SkuSnapshot;
  after: SkuSnapshot;
  codes: CodeChange;
  price_published: string | null;
  /** `{head, line}` on the shop's own hash chain, or null if it could not be written. */
  audit: { head: string; line: number } | null;
  audit_note: string;
  untouched: string;
}

export const editSku = (skuId: string, fields: SkuEdit) =>
  send<SkuEdited>(`/shop/${encodeURIComponent(skuId)}`, jsonBody('PATCH', fields));

/* ------------------------------------------------- adding one by hand ---- */

/**
 * A product put on the shelf with NO CAMERA.
 *
 * The weak path, and the wrappers here do not pretend otherwise: nothing is
 * measured and nothing is embedded, so the counter learns a name and a price
 * and nothing about what the product looks like. The response carries the
 * server's own `warning` saying exactly that, and the screen prints it.
 *
 * Rule 3 above applies with full force: `price_rupees` is the STRING the
 * shopkeeper typed. There is no field here that takes paise.
 */
export interface NewSku {
  name: string;
  /** Rupees as typed: `12`, `12.50`. Never a number. */
  price_rupees: string;
  /**
   * Optional. Left out, the server derives one from the name and says so.
   * It can never be changed afterwards — every bill points at it.
   */
  sku_id?: string;
  /** The printed code, if the packet has one and the shopkeeper typed it. */
  code?: string;
  /** Base64, with or without the `data:image/…;base64,` a FileReader adds. */
  photo_b64?: string;
}

export interface SkuAdded {
  sku_id: string;
  /** True when the server made the id out of the name rather than being told one. */
  sku_id_derived: boolean;
  name: string;
  price_paise: Paise;
  price_rupees: string;
  codes: string[];
  has_photo: boolean;
  stored_in: string[];
  price_published: string | null;
  /** Present only when the money service cannot see the new price yet. */
  price_map_warning?: string | null;
  audit: { head: string; line: number } | null;
  audit_note: string;
  /** What this product CANNOT do, in the server's words. Always shown. */
  warning: string;
  better: string;
  permanent: string;
}

export const addSku = (body: NewSku) => send<SkuAdded>('/shop', jsonBody('POST', body));

/* ------------------------------------------------------- its photograph -- */

export interface PhotoStored {
  sku_id: string;
  has_photo: boolean;
  photo_bytes: number;
  stored_in: string[];
  /**
   * Whether the CUSTOMER will see this picture, asked rather than assumed.
   * A product with no descriptor keeps its photo in the till's sidecar, which
   * the storefront cannot read yet; `storefront_note` says so in words.
   */
  on_storefront: boolean;
  storefront_note: string | null;
  audit: { head: string; line: number } | null;
  audit_note: string;
  untouched: string;
}

/** `photoB64` empty removes the picture. The server draws that distinction. */
export const setSkuPhoto = (skuId: string, photoB64: string) =>
  send<PhotoStored>(`/shop/${encodeURIComponent(skuId)}/photo`,
                    jsonBody('PUT', { photo_b64: photoB64 }));

/* ------------------------------------------------- what a price has been -- */

export interface EditEntry {
  ts: string;
  event: string;
  sku_id: string;
  changed: string[];
  name_before?: string;
  name_after?: string;
  price_paise_before?: Paise | null;
  price_paise_after?: Paise;
  price_rupees_before?: string | null;
  price_rupees_after?: string;
  codes_before?: string[];
  codes_after?: string[];
  hash: string;
}

export interface SkuHistory {
  sku_id: string;
  count: number;
  entries: EditEntry[];
  /** The chain's own verdict travels with the rows it produced. */
  chain: { verified: boolean; lines: number; head: string; error: string | null; path: string };
}

export const skuHistory = (skuId: string) =>
  send<SkuHistory>(`/shop/${encodeURIComponent(skuId)}/history`);

/* ---------------------------------------------------------- the shop itself -- */

export interface OpeningHours {
  /** 24-hour clock, `HH:MM`. */
  open: string;
  close: string;
  /** In week order — mon..sun — never alphabetical. */
  days: string[];
  crosses_midnight: boolean;
  days_label: string;
  label: string;
}

export interface ShopProfileDoc {
  format: number;
  name: string;
  address: string;
  /** Ten bare digits, however the shopkeeper typed it. */
  phone: string;
  phone_e164: string;
  hours: OpeningHours;
  /**
   * The shop's own handle in the customer link — `verma-kirana-store-k7m2`.
   * Minted by the server once and carried across every save; a profile written
   * before slugs existed gets one on its first read. Not a field the form
   * sends: the server ignores a body that tries.
   */
  slug?: string | null;
  updated_at: string;
}

export interface ProfileRead {
  /** False is a FACT, not a failure: the shopkeeper has not filled it in yet. */
  configured: boolean;
  profile: ShopProfileDoc | null;
  path: string;
  hint: string | null;
  days: string[];
}

export interface ProfileSaved {
  changed: string[];
  profile: ShopProfileDoc;
  path: string;
  audit: { head: string; line: number } | null;
  note: string;
}

/** What the form sends. Text and a list of day keys; nothing derived. */
export interface ProfileForm {
  name: string;
  address: string;
  phone: string;
  hours: { open: string; close: string; days: string[] };
}

export const shopProfile = () => send<ProfileRead>('/shop/profile');

/**
 * Just the name and address, and readable with no session.
 *
 * The sign-in screen needs the shop's name and is, by definition, drawn before
 * anybody has signed in. With the lock on, `/shop/profile` answers 401 there —
 * so the screen used to report that the counter had no name, when what had
 * actually happened is that it would not say. Two fields, GET only.
 */
export type Nameplate = { configured: boolean; name: string | null; address: string | null };
export const shopNameplate = () => send<Nameplate>('/shop/nameplate');
export const saveShopProfile = (form: ProfileForm) =>
  send<ProfileSaved>('/shop/profile', jsonBody('PUT', form));

/* ------------------------------------------------------ the face of the shop -- */

/**
 * The shop's OWN customer link, its printed code and its photograph —
 * `gawaah/shopface.py`.
 *
 * The shutter code used to encode `<origin>/#/shop`, the same string for every
 * counter on earth. The link now carries the shop's slug, `?s=<slug>`, and the
 * storefront asks `/store/shop` whether the slug it arrived with is this
 * shop's. The page never builds the link: it renders the string the server
 * answered with, and the QR is the server's own PNG of that same string.
 */
export interface ShopLinkRead {
  configured: boolean;
  slug: string | null;
  name: string | null;
  /** The address a customer opens. Rendered, copied and printed — never edited. */
  url: string;
  /** Where the PNG of `url` is. Guarded, like this read. */
  qr_url: string;
  origin: string;
  /** False for a loopback address — a good QR that no phone can open. */
  reachable_from_a_phone: boolean;
  note: string;
  /** False while the shop has no name: the link is then the plain one. */
  unique: boolean;
  unique_note: string | null;
}

export interface ShopLinkRenewed extends ShopLinkRead {
  slug_before: string | null;
  audit: { head: string; line: number } | null;
  audit_note: string;
  /** What the old stickers now do, in the server's words. Always shown. */
  warning: string;
}

export const shopLink = () => send<ShopLinkRead>('/shop/link');
export const renewShopLink = () =>
  send<ShopLinkRenewed>('/shop/link/renew', { method: 'POST' });
/** The printable code. `px` is the side of the code itself, before padding. */
export const shopLinkQrUrl = (px = 700) => `/shop/link/qr?px=${px}`;

export interface ShopPhotoStored {
  has_photo: boolean;
  photo_bytes: number;
  /** Versioned by the file's mtime, so a replaced picture is never served stale. */
  photo_url: string | null;
  edge_px: number;
  cap_bytes: number;
  on_storefront: boolean;
  audit: { head: string; line: number } | null;
  audit_note: string;
  untouched: string;
}

/** `photoB64` empty removes the picture. The server draws that distinction. */
export const setShopPhoto = (photoB64: string) =>
  send<ShopPhotoStored>('/shop/photo', jsonBody('PUT', { photo_b64: photoB64 }));

/**
 * What a CUSTOMER'S phone learns about this shop, and whether the slug it
 * arrived with is this shop's. Open — no session — and four public fields.
 *
 *   link  'own'    the slug is this shop's
 *         'none'   the link carried no slug (an older sticker)
 *         'other'  the link was printed for a different shop; `matches` is
 *                  false and `note` says so by name
 */
export interface StoreShop {
  configured: boolean;
  slug: string | null;
  name: string | null;
  address: string | null;
  photo_url: string | null;
  requested: string | null;
  matches: boolean;
  link: 'own' | 'none' | 'other';
  note: string;
}

export const storeShop = (slug?: string | null) =>
  send<StoreShop>(slug ? `/store/shop?s=${encodeURIComponent(slug)}` : '/store/shop');

/* ------------------------------------------------------------- formatting -- */

/**
 * Paise back into the plain rupee string an input box holds — `2145` -> `21.45`.
 *
 * NOT `rupees()` from lib/money: that one groups digits for reading (`1,234.50`)
 * and returns the rupee sign with them, and the server refuses both. This is
 * the string the shopkeeper will edit and send straight back, so it has to be
 * the exact shape `gawaah/money.py` parses.
 *
 * `(p - p % 100) / 100` divides a number that is ALREADY a multiple of 100, so
 * it is exact. `p / 100` here would be the single most likely place for a
 * rounding error to enter a price.
 */
export function rupeesForInput(paise: Paise | null | undefined): string {
  if (typeof paise !== 'number' || !Number.isInteger(paise)) return '';
  const whole = (paise - (paise % 100)) / 100;
  const rest = paise % 100;
  return `${whole}.${String(rest).padStart(2, '0')}`;
}

/** The days of the week, in week order, with the labels a person reads. */
export const DAYS: ReadonlyArray<{ key: string; label: string; short: string }> = [
  { key: 'mon', label: 'Monday', short: 'Mon' },
  { key: 'tue', label: 'Tuesday', short: 'Tue' },
  { key: 'wed', label: 'Wednesday', short: 'Wed' },
  { key: 'thu', label: 'Thursday', short: 'Thu' },
  { key: 'fri', label: 'Friday', short: 'Fri' },
  { key: 'sat', label: 'Saturday', short: 'Sat' },
  { key: 'sun', label: 'Sunday', short: 'Sun' },
];
