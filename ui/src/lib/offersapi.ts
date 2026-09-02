/**
 * Every request the Offers screen makes, in one place, with types.
 *
 * Two rules this module exists to keep, and one thing it deliberately cannot do:
 *
 *  1. THE BROWSER IS NEVER AN AUTHOR OF MONEY. Nothing here sends a price, a
 *     discounted price, or a total. `create()` sends the offer's FIELDS — which
 *     product, which kind, how much off — and the server decides what that means
 *     in paise. There is no function in this file that computes a price, and
 *     `preview()` below is a display aid built from numbers the SERVER already
 *     returned, never a second implementation of the discount arithmetic.
 *  2. A REFUSAL IS A RESULT, NOT AN ERROR. `/offers/*` answers `{ok:false,
 *     reason, detail}` with a 400 on purpose, so the body is parsed on non-2xx
 *     rather than thrown, and only a transport failure produces a refusal about
 *     the network.
 *
 * WHAT THE PAGE CANNOT DO: it cannot make a discount real. The money service is
 * a separate process holding the gateway keys, and it re-prices every basket
 * from its own price book before it mints. This page writes an offer to a file;
 * `gawaah/offers.py` puts that file inside the price book paisa reads. If those
 * two ever named different files the till would show a discount the gateway had
 * never heard of, which is why `health()` exists and why the screen prints the
 * resolved path.
 *
 * `send` is duplicated from `api.ts` rather than imported: it is module-private
 * there, and a new screen must not widen the till's own request layer to borrow
 * one function. The precedence rule it encodes — explicit `ok`, then an `error`
 * string, then the HTTP STATUS — is load-bearing and copied intact. FastAPI's
 * own 422 comes back as `{"detail": ...}` with neither `ok` nor `error`, and a
 * rule that only read the body would file a validation crash as a success.
 *
 * NOTE ON DEV: `vite.config.ts` proxies an explicit allowlist of path prefixes
 * to the till on :8790. `/offers` has to be in that list or every request here
 * 404s under `npm run dev` while working perfectly in the built site.
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

const postJson = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

/* ------------------------------------------------------------- the shape -- */

export type OfferKind = 'flat' | 'percent';

/** What one offer does to one product. Every number is integer paise. */
export interface OfferExample {
  sku_id: string;
  name: string;
  base_paise: Paise;
  base_rupees: string;
  price_paise: Paise;
  price_rupees: string;
  off_paise: Paise;
  off_rupees: string;
}

export interface Offer {
  offer_id: string;
  /** null means every product in the shop. */
  sku_id: string | null;
  kind: OfferKind;
  /** Integer paise when `kind` is flat; a whole percentage when it is percent. */
  value: number;
  active: boolean;
  created_at: string;
  label: string;
  /** The offer as it would be written on a shutter. Server's words. */
  says: string;
  scope: string;
  off_rupees: string | null;
  percent: number | null;
  /** What this offer alone does to the product it applies to. */
  example: OfferExample | null;
  /**
   * True when the discount is worth more than the product and is being held at
   * the minimum price. Shown, never hidden: an offer quietly doing something
   * other than what it says is worse than one visibly refusing to.
   */
  clamped: boolean;
}

export interface OfferBook {
  count: number;
  active: number;
  clamped: number;
  offers: Offer[];
  file: string;
  catalogue_known: boolean;
  kinds: OfferKind[];
  percent_range: [number, number];
  max_offers: number;
}

/** One product's marked price and what it actually costs after offers. */
export interface PriceRow {
  sku_id: string;
  name: string;
  base_paise: Paise;
  base_rupees: string;
  price_paise: Paise;
  price_rupees: string;
  off_paise: Paise;
  off_rupees: string;
  offer_id: string | null;
  clamped: boolean;
}

export interface PriceBook {
  count: number;
  discounted: number;
  active_offers: number;
  items: PriceRow[];
}

export interface OffersHealth {
  module: string;
  /** The file BOTH processes must resolve to. Printed on the screen on purpose. */
  file: string;
  exists: boolean;
  offers: number;
  active: number;
  shop_dir: string;
  rounding: string;
  min_price_paise: Paise;
}

/* --------------------------------------------------------------- requests -- */

export const list = () => send<OfferBook>('/offers');

/**
 * What every product costs right now, before and after offers.
 *
 * THIS IS WHAT A TILL SHOWING A DISCOUNTED LINE MUST READ. It is the same
 * arithmetic, from the same file, that the money service applies inside its own
 * price book — so a screen drawing `price_paise` from here is showing a number
 * paisa will independently derive and agree with at the moment of the mint.
 */
export const prices = () => send<PriceBook>('/offers/prices');

export const health = () => send<OffersHealth>('/offers/health');

/**
 * Create an offer.
 *
 * `off_rupees` is a STRING ("5.00") and that is not an oversight. A decimal sent
 * as a JSON number is a float by the time it is parsed, and `float('5.10')` is
 * already lossy before anything rounds it. The server parses the text to integer
 * paise itself.
 */
export function create(body: {
  sku_id: string | null;
  kind: OfferKind;
  off_rupees?: string;
  percent?: number;
  label?: string;
  active?: boolean;
}) {
  return send<{ offer: Offer; file: string; audited: boolean; note: string }>(
    '/offers',
    postJson(body),
  );
}

export const setActive = (offerId: string, active: boolean) =>
  send<{ offer: Offer; audited: boolean }>(
    `/offers/${encodeURIComponent(offerId)}/active`,
    postJson({ active }),
  );

export const remove = (offerId: string) =>
  send<{ offer_id: string; removed: string; audited: boolean }>(
    `/offers/${encodeURIComponent(offerId)}`,
    { method: 'DELETE' },
  );
