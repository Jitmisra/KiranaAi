/**
 * Every request the storefront makes, in one place, with types.
 *
 * Three rules this module exists to keep:
 *
 *  1. THE PHONE IS NEVER AN AUTHOR OF MONEY. `placeOrder` sends sku ids and
 *     whole-number quantities and nothing else. There is no function here that
 *     sends a price. The one place a number goes up is `total_paise`, and it is
 *     sent so the server can DISAGREE with it — the server re-prices the basket
 *     from its own catalogue and refuses on a mismatch. That is a checksum, not
 *     an instruction.
 *  2. A REFUSAL IS A RESULT, NOT AN ERROR. The server answers `{ok:false,
 *     reason}` with a 400 on purpose. These wrappers parse the body on non-2xx
 *     instead of throwing, so a refusal renders as the product working.
 *  3. NO FORGERY PRIMITIVE. Nothing here builds a payment URL. `pay()` returns
 *     the opaque `short_url` the gateway minted, which the server has already
 *     checked against its own host allowlist before repeating it.
 *
 * `send` is a near-copy of the one in `lib/api.ts` and that is deliberate: that
 * module does not export it, and the storefront must not edit the till's own
 * request layer to borrow one function. The precedence rules below are the same
 * ones, for the same reasons — the comments there record five separate bugs
 * where a response shape was guessed and the page lied instead of breaking.
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
    // The network, not the shop. They need different fixes, so say which.
    return { ok: false, reason: 'this phone could not reach the shop', detail: String(e) };
  }
  let body: Record<string, unknown>;
  try {
    body = (await res.json()) as Record<string, unknown>;
  } catch {
    return { ok: false, reason: `the shop replied ${res.status} with something that was not JSON` };
  }
  // An explicit `ok` is authoritative; then the HTTP status. FastAPI's own
  // failures (a 422, a 500) carry `{"detail": ...}` with no `ok` at all, and a
  // rule that read only the body would file a crash as a success and hand the
  // caller an object whose every field is undefined.
  if (body && body.ok === undefined) {
    if (!res.ok) {
      return {
        ok: false,
        reason: `the shop refused with HTTP ${res.status}`,
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

/* ------------------------------------------------------------ catalogue -- */

export interface StoreItem {
  sku_id: string;
  name: string;
  /** What the shop will CHARGE — offers already applied, server-side. */
  price_paise: Paise;
  /** Formatted by the server, integer-only. The page formats its own totals. */
  price_rupees: string;
  /**
   * The shelf-edge price, present ONLY while an offer is active on this
   * product. `marked_paise - off_paise === price_paise`, all integer paise,
   * all derived by the server — this page strikes one number through and
   * never computes a discount of its own.
   */
  marked_paise?: Paise;
  marked_rupees?: string;
  /** The saving, integer paise. Present only when it is greater than zero. */
  off_paise?: Paise;
  has_photo: boolean;
  photo_url: string | null;
  /** How the shopkeeper taught it. Shown nowhere on the customer's page. */
  taught_with: string;
  /**
   * WHAT THIS PHONE MAY BUY, derived by the server as
   *
   *     on hand − units in orders not yet cancelled − the shop's floor
   *
   * clamped at zero. `null` is "the shop has not counted this" and is NOT a
   * zero: such a product is sold with no cap, and the page must say "no stock
   * figure" rather than invent a number. Read with `availableOf`, which also
   * survives an older server that does not send the field at all.
   */
  available_units: number | null;
  /** True only when there IS a figure and it allows nothing. Never for null. */
  out_of_stock: boolean;
  /** Packets other customers' open orders are holding. Shown, not hidden. */
  reserved_units: number;
  /** The server's own sentence for the card: "3 available", "out of stock", … */
  stock_note: string;
}

export interface Store {
  count: number;
  items: StoreItem[];
  delivery: { statuses: string[]; max_qty_per_line: number; max_lines: number };
  /**
   * Whether the figures on the items mean anything. `figures: false` is a
   * stock module that could not answer — every card then carries null — and
   * `error` says why, for the shopkeeper's benefit more than the customer's.
   */
  stock: { figures: boolean; error: string | null; out_of_stock: number; note: string };
}

export const store = () => send<Store>('/store');

/**
 * The cap on one product, or null for "no cap".
 *
 * Defensive on purpose: a StoreItem from a till that predates the shelf has
 * no `available_units` at all, and `undefined` must read as "no figure", never
 * as zero — the one mistake this whole feature exists not to make.
 */
export function availableOf(item: Pick<StoreItem, 'available_units'>): number | null {
  const v = item.available_units;
  return typeof v === 'number' && Number.isInteger(v) && v >= 0 ? v : null;
}

/** True only on an explicit `true` — an older server cannot close a shop. */
export const isOut = (item: Pick<StoreItem, 'out_of_stock'>): boolean => item.out_of_stock === true;

/* ------------------------------------------------------------- the shelf -- */

/** The refusal `/store/order` gives when a basket asks for more than there is. */
export const STOCK_REFUSAL = 'not_enough_stock_for_these_lines';

/** One short line, in the server's own numbers. */
export interface ShortLine {
  sku_id: string;
  name: string;
  asked: number;
  available: number;
  out_of_stock: boolean;
}

/**
 * The short lines a stock refusal carries, or null if this is not one.
 *
 * The server names them as STRUCTURE under `lines` beside the sentence, so
 * the page can set the basket to the numbers the shop will accept instead of
 * parsing prose. Every field is checked before it is believed: a line whose
 * `available` is not a whole number is dropped rather than clamped to it.
 */
export function shortLines(r: Refusal): ShortLine[] | null {
  if (r.reason !== STOCK_REFUSAL) return null;
  const raw = (r as Refusal & { lines?: unknown }).lines;
  if (!Array.isArray(raw)) return [];
  const out: ShortLine[] = [];
  for (const x of raw) {
    if (!x || typeof x !== 'object') continue;
    const o = x as Record<string, unknown>;
    if (typeof o.sku_id !== 'string' || !o.sku_id) continue;
    if (typeof o.available !== 'number' || !Number.isInteger(o.available) || o.available < 0) continue;
    if (typeof o.asked !== 'number' || !Number.isInteger(o.asked)) continue;
    out.push({
      sku_id: o.sku_id,
      name: typeof o.name === 'string' && o.name ? o.name : o.sku_id,
      asked: o.asked,
      available: o.available,
      out_of_stock: o.out_of_stock === true,
    });
  }
  return out;
}

/** What fitting a basket did to one line, for telling the customer. */
export interface CartChange { sku_id: string; name: string; from: number; to: number }

/**
 * A basket cut down to what the shelf allows. PURE, and the only place the
 * page changes a quantity the customer did not type.
 *
 * `caps` maps sku -> cap, where a cap of null means "no figure, no cap". A
 * product absent from the map is left alone — an unknown product is pruned
 * elsewhere, against the catalogue, and this function must not conflate
 * "not on sale" with "none left". Names are looked up for the message; a
 * missing name falls back to the sku so nothing is reported as undefined.
 */
export function fitCart(
  cart: Record<string, number>,
  caps: ReadonlyMap<string, { cap: number | null; name: string }>,
): { cart: Record<string, number>; changes: CartChange[] } {
  const next: Record<string, number> = {};
  const changes: CartChange[] = [];
  for (const [sku, qty] of Object.entries(cart)) {
    const c = caps.get(sku);
    if (!c || c.cap === null || qty <= c.cap) {
      next[sku] = qty;
      continue;
    }
    changes.push({ sku_id: sku, name: c.name, from: qty, to: c.cap });
    if (c.cap > 0) next[sku] = c.cap;
  }
  return { cart: next, changes };
}

/** `fitCart` against a catalogue: every item's `available_units` is its cap. */
export function fitCartToStore(cart: Record<string, number>, items: readonly StoreItem[]) {
  const caps = new Map<string, { cap: number | null; name: string }>();
  for (const it of items) caps.set(it.sku_id, { cap: availableOf(it), name: it.name });
  return fitCart(cart, caps);
}

/** `fitCart` against a refusal: the shop just said exactly how many there are. */
export function fitCartToRefusal(cart: Record<string, number>, lines: readonly ShortLine[]) {
  const caps = new Map<string, { cap: number | null; name: string }>();
  for (const ln of lines) caps.set(ln.sku_id, { cap: ln.available, name: ln.name });
  return fitCart(cart, caps);
}

/* --------------------------------------------------------------- orders -- */

export interface OrderLine {
  sku_id: string;
  name: string;
  qty: number;
  unit_paise: Paise;
  unit_rupees: string;
  line_paise: Paise;
  line_rupees: string;
  taught_with: string;
}

/** What the CUSTOMER is shown. No address and no phone — see the server. */
export interface CustomerOrder {
  order_id: string;
  at: string;
  status: string;
  status_changed_at: string | null;
  lines: OrderLine[];
  total_paise: Paise;
  total_rupees: string;
  paid: boolean;
  payment_state: string | null;
  short_url: string | null;
  name: string | null;
  /**
   * WHETHER `short_url` IS SOMETHING TO PRESS, which is not the same question
   * as whether one is stored. A link the gateway does not serve resolves to
   * `{}` in the browser, and this page used to render exactly that as a green
   * PAY button. Absent on an order with no link at all.
   *
   * Undefined means the server did not say — an older build, or a paid order.
   * Treat only an explicit `false` as a refusal to show the link, so a missing
   * field can never silently disable a working payment.
   */
  payable?: boolean;
  /**
   * Who said what about the link, and the first three are the GATEWAY's answer
   * rather than this page's guess: `live`, `dead` (404/410), `unknown` (the
   * shop could not reach the gateway — still payable).
   *
   * `refused` is the one that is THIS SHOP's answer: the stored string is not
   * on the gateway at all, so the shop declined to send anyone to it and the
   * gateway was never asked. Never render it as the gateway having denied
   * anything — `payment_note` already carries the right words.
   */
  link_state?: 'live' | 'dead' | 'unknown' | 'refused' | null;
  /** Plain words for the customer when the link cannot be paid. Render as given. */
  payment_note?: string;
  /** The order may ask for a replacement link. True when `dead` or `refused`. */
  can_relink?: boolean;
}

/** What the SHOPKEEPER is shown: the same order, plus where it has to go. */
export interface ShopOrder {
  order_id: string;
  at: string;
  status: string;
  status_changed_at: string;
  customer: { name: string; phone: string; address: string };
  lines: OrderLine[];
  total_paise: Paise;
  total_rupees: string;
  history: Array<{ at: string; from: string | null; to: string; by: string }>;
  payment: {
    session_id: string;
    paid: boolean;
    state: string | null;
    short_url: string | null;
    minted_at: string | null;
    /**
     * THE LAST TIME PAY WAS PRESSED ON THIS ORDER AND NOTHING WAS MINTED.
     *
     * Optional: absent on an order nobody has tried to pay, and REMOVED again
     * the moment a link is successfully minted, so its presence means "the most
     * recent attempt failed and no link exists" and never "it failed once".
     *
     * `reason` and `detail` are the money service's own words, stored verbatim
     * — `amber_in_basket` names a product the shopkeeper can go and fix, and a
     * paraphrase of it does not. Render them as they are.
     *
     * WHY IT EXISTS. A refused mint used to live only in the HTTP response to
     * the phone that asked. The order was left with `minted_at: null`, which is
     * indistinguishable from an order nobody tried to pay — so this screen
     * offered PAY AT THE DOOR, a payment method this product does not have, on
     * orders that had been refused for real reasons and then delivered.
     */
    last_refusal?: { reason: string; detail: string; at: string };
  };
}

export interface OrderBook {
  count: number;
  counts: Record<string, number>;
  orders: ShopOrder[];
  statuses: string[];
  /** What each state may legally become. The page draws only these buttons. */
  next_status: Record<string, string[]>;
}

export interface Cart {
  items: Array<{ sku_id: string; qty: number }>;
  name: string;
  phone: string;
  address: string;
  /**
   * The basket's own arithmetic, sent to be CHECKED. The server recomputes from
   * its catalogue and refuses the order if the two disagree, so a page showing
   * a stale price finds out here rather than at the door.
   */
  total_paise: Paise;
}

export const placeOrder = (cart: Cart) =>
  send<CustomerOrder & { audited: boolean; note: string }>('/store/order', postJson(cart));

export const myOrder = (orderId: string) =>
  send<CustomerOrder>(`/store/order/${encodeURIComponent(orderId)}`);

export const orders = () => send<OrderBook>('/orders');

/**
 * THE SHELF AS THE STOREFRONT SEES IT, for the shopkeeper: the whole
 * derivation per product, so the Products screen can print "online: 3 (2 in
 * open orders)" beside the count it edits. Field names read off
 * `storefront.availability()`, not guessed.
 */
export interface OnlineStockRow {
  sku_id: string;
  name: string | null;
  /** The shopkeeper's own figure from gawaah/stock.py; null when never counted. */
  on_hand_units: number | null;
  counted_at: string | null;
  basis: 'counted' | 'never_counted' | null;
  /** In orders that are new, preparing or out for delivery. */
  reserved_open_units: number;
  /** Delivered after the last count — gone, and nothing billed them. */
  reserved_delivered_units: number;
  reserved_units: number;
  /** on hand − everything reserved, before the floor. Null without a count. */
  shelf_after_orders: number | null;
  online_floor: number;
  /** What a phone may buy. Null without a count, never zero for it. */
  available_units: number | null;
  out_of_stock: boolean;
  /** The arithmetic as a sentence, e.g. "5 on hand − 2 in open orders − …". */
  why: string;
}

export interface OnlineStock {
  count: number;
  figures: boolean;
  error: string | null;
  out_of_stock: number;
  reserved_open_units: number;
  items: OnlineStockRow[];
  note: string;
  open_statuses: string[];
}

export const onlineStock = () => send<OnlineStock>('/orders/stock');

/**
 * "Stop selling online below N." Whole packets; 0 or null is the default and
 * sells down to the last one. Recorded on the stock chain by gawaah/stock.py;
 * applied by the storefront on every catalogue read and every order.
 */
export const setOnlineFloor = (skuId: string, units: number | null) =>
  send<{
    sku_id: string; online_floor: number; is_default: boolean;
    chain_head: string; on_hand_units: number | null; detail: string;
  }>(`/stock/${encodeURIComponent(skuId)}/floor`, postJson({ units }));

export const setStatus = (orderId: string, status: string) =>
  send<{ order_id: string; was: string; status: string; next_status: string[]; order: ShopOrder }>(
    `/orders/${encodeURIComponent(orderId)}/status`,
    postJson({ status }),
  );

/* ---------------------------------------------------------------- money -- */

export interface Minted {
  order_id: string;
  session_id: string;
  amount_paise: Paise;
  amount_rupees: string;
  /** THE GATEWAY'S OWN LINK. Never a string this program composed. */
  short_url: string;
  qr_url: string;
  replayed: boolean;
  note: string;
  /** See `CustomerOrder.payable`. An explicit `false` means do not open it. */
  payable?: boolean;
  link_state?: 'live' | 'dead' | 'unknown' | 'refused' | null;
  can_relink?: boolean;
}

export const pay = (orderId: string) =>
  send<Minted>(`/store/order/${encodeURIComponent(orderId)}/pay`, { method: 'POST' });

/**
 * Ask for a replacement link, for an order whose link the GATEWAY has denied.
 *
 * The server refuses this unless it has proof the existing link is dead — a
 * 404 or 410 from the gateway, not merely a shop that could not reach it. Two
 * live links on one order is how a customer gets charged twice, so this button
 * being pressed is never enough on its own.
 */
export const relink = (orderId: string) =>
  send<Minted>(`/store/order/${encodeURIComponent(orderId)}/relink`, { method: 'POST' });

/* ------------------------------------------------ who the customer is -- */

export interface CustomerMe {
  customer: { name: string; phone: string; verified: boolean } | null;
  signed_in: boolean;
  /**
   * A SHOPKEEPER IS SIGNED IN ON THIS BROWSER, so this is a preview of the shop
   * front rather than a customer's session. The server refuses an order from
   * this browser either way — this field exists so the page can say so before a
   * delivery address is typed, rather than after.
   */
  previewing: boolean;
  shopkeeper_name: string | null;
}

export const customerMe = () => send<CustomerMe>('/store/customer/me');

/**
 * Identify as a customer. No password: see the block comment in
 * `gawaah/storefront.py` for why a kirana customer does not get one.
 *
 * `orderId` is the proof. Without it the session is remembered but unverified
 * and can read nothing back; with it the customer can see every order that
 * number has placed.
 */
export const customerSignIn = (name: string, phone: string, orderId?: string) =>
  send<{ customer: { name: string; phone: string; verified: boolean }; note: string }>(
    '/store/customer/signin',
    postJson(orderId ? { name, phone, order_id: orderId } : { name, phone }),
  );

export const customerSignOut = () =>
  send<{ signed_out: boolean }>('/store/customer/signout', { method: 'POST' });

export const customerOrders = () =>
  send<{ count: number; orders: CustomerOrder[] }>('/store/customer/orders');

/* ------------------------------------------------------- the shutter code -- */

export interface ShopLink {
  url: string;
  qr_url: string;
  /** False for a loopback address — a good QR that no phone can open. */
  reachable_from_a_phone: boolean;
  note: string;
}

export const shopLink = () => send<ShopLink>('/store/link');

/**
 * THE SHOP'S OWN UNIQUE LINK, for the shopkeeper to hand out. `/shop/link` is
 * the guarded, shopkeeper-side answer (slug, whether it is unique, whether a
 * phone could open it); `/store/link` above is what the storefront itself reads.
 */
export interface ShopUniqueLink {
  ok: boolean;
  configured: boolean;
  slug: string | null;
  name: string | null;
  url: string;
  qr_url: string;
  origin: string;
  reachable_from_a_phone: boolean;
  unique: boolean;
  note: string;
  unique_note?: string;
}
export const shopUniqueLink = () => send<ShopUniqueLink>('/shop/link');

/**
 * A link made out to ONE customer, and the claiming of it.
 *
 * The shutter QR is one sticker everybody scans, which is right — a printed
 * code cannot know who is holding the phone. This is the other shape: the
 * shopkeeper mints a link for a regular and sends it to them, so they never
 * type their name and number again.
 *
 * The token is opaque and the phone number is NOT in the URL. `claimCustomer`
 * spends it once; after that it is dead.
 */
export type CustomerLink = {
  url: string;
  for: { name: string; phone: string };
  expires_in_days: number;
  single_use: boolean;
  note: string;
};
export const makeCustomerLink = (name: string, phone: string) =>
  send<CustomerLink>('/shop/customer-link', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, phone }),
  });

export const claimCustomer = (token: string) =>
  send<{ customer: { name: string; phone: string; verified: boolean } }>(
    '/store/customer/claim', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
export const shopQrUrl = () => '/store/qr';
export const productPhotoUrl = (skuId: string) => `/store/photo/${encodeURIComponent(skuId)}`;

/* ---------------------------------------------------------- the basket -- */

/**
 * The customer's basket lives in this tab and nowhere else.
 *
 * Deliberately NOT on the server: a cart that exists server-side needs an
 * identity for an anonymous phone, and inventing one would mean this program
 * tracking people who have not ordered anything. The cost of that choice is
 * that closing the tab loses the basket, which is the right way round.
 */
export const CART_KEY = 'gawaah.cart.v1';
export const LAST_ORDER_KEY = 'gawaah.order.v1';

export function loadCart(): Record<string, number> {
  try {
    const raw = localStorage.getItem(CART_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const out: Record<string, number> = {};
    for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
      // A quantity read back off disk is checked as hard as one off the wire.
      // A float here would reach a multiplication against a price.
      if (typeof v === 'number' && Number.isInteger(v) && v > 0) out[k] = v;
    }
    return out;
  } catch {
    return {};
  }
}

export function saveCart(cart: Record<string, number>): void {
  try {
    localStorage.setItem(CART_KEY, JSON.stringify(cart));
  } catch {
    // A phone in private mode refuses storage. The basket still works for this
    // page view; it simply will not survive a reload, and that is not worth an
    // error message on a shop front.
  }
}

export function rememberOrder(orderId: string): void {
  try {
    localStorage.setItem(LAST_ORDER_KEY, orderId);
  } catch { /* see saveCart */ }
}

export function lastOrder(): string | null {
  try {
    return localStorage.getItem(LAST_ORDER_KEY);
  } catch {
    return null;
  }
}

export function forgetOrder(): void {
  try {
    localStorage.removeItem(LAST_ORDER_KEY);
  } catch { /* see saveCart */ }
}

/**
 * WHO THIS BROWSER SAID IT WAS AT THE DELIVERY FORM.
 *
 * MEASURED, not assumed: a successful `POST /store/order` sets no cookie and
 * creates no customer session — `GET /store/customer/me` still answers
 * `customer: null` immediately after one. So the name and number a customer
 * typed at the basket are known to the SHOP (they are on the order) and to
 * nobody else, and the MY ORDERS screen one tap later used to open with two
 * empty boxes.
 *
 * That was not merely tedious, it produced a refusal the customer could not
 * have predicted. The screen tells them the order id is what is needed, so
 * they filled in the order id and pressed the button, and the counter answered
 * `customer_name_missing` — a 400 caused entirely by the page forgetting
 * something the person had typed ninety seconds earlier.
 *
 * WHY NOT READ IT BACK FROM THE ORDER: `GET /store/order/{id}` is an OPEN
 * route and deliberately returns the name but NOT the phone (see the comment
 * on `CustomerOrder`). Anyone holding a forwarded link may read that order, so
 * the phone is withheld on purpose. This is the customer's own device
 * remembering what the customer themselves typed into it — the same thing, and
 * the same lifetime, as the basket beside it. It is never a source of truth
 * about who anybody IS: proving the number is still an order id, checked by
 * the server, and this only fills the boxes in.
 */
export const CUSTOMER_KEY = 'gawaah.customer.v1';

export function rememberCustomer(name: string, phone: string): void {
  try {
    localStorage.setItem(CUSTOMER_KEY, JSON.stringify({ name, phone }));
  } catch { /* see saveCart */ }
}

export function lastCustomer(): { name: string; phone: string } | null {
  try {
    const raw = localStorage.getItem(CUSTOMER_KEY);
    if (!raw) return null;
    const d: unknown = JSON.parse(raw);
    if (!d || typeof d !== 'object') return null;
    // Read back off disk as strictly as off the wire: anything can be in
    // localStorage, including a half-written value from an older build.
    const { name, phone } = d as Record<string, unknown>;
    if (typeof name !== 'string' || typeof phone !== 'string') return null;
    if (!name.trim() || !phone.trim()) return null;
    return { name, phone };
  } catch {
    return null;
  }
}

export function forgetCustomer(): void {
  try {
    localStorage.removeItem(CUSTOMER_KEY);
  } catch { /* see saveCart */ }
}
