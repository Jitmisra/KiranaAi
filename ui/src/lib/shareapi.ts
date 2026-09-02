/**
 * Every request the ShareSheet makes, in one place, with types.
 *
 * WHAT THIS FILE DOES NOT DO, and the reason it is worth a paragraph: it does
 * not compose a message, it does not format a rupee, and it does not build a
 * `wa.me` address. All three come back from `gawaah/share.py` fully formed.
 *
 * That is not fastidiousness. `wa.me/<digits>?text=<anything>` is a URL any
 * page could assemble in one line, and a page that assembled it would be a
 * page that could put a `upi://pay?pa=…` into a message going out in the
 * shop's own voice. The server refuses that (`refused_to_share_this_message`)
 * and the refusal is only worth having if the browser cannot route around it.
 * So the ONLY payable-shaped string this module ever handles is the `wa_url`
 * the server returned, and it is opened, never edited.
 *
 * Three rules copied from `api.ts` and kept for the same reasons:
 *
 *  1. THE BROWSER IS NEVER AN AUTHOR. The page sends a session id, an order id
 *     and a phone number a person typed. Everything else on the screen was
 *     derived by the server.
 *  2. A REFUSAL IS A RESULT, NOT AN ERROR. `/share/*` answers `{ok:false,
 *     reason, detail}` with a 400 (404 for an order or supplier that is not
 *     there) on purpose, so the body is parsed on non-2xx rather than thrown,
 *     and only a transport failure produces a refusal about the network.
 *  3. `send` is duplicated rather than imported: it is module-private in
 *     `api.ts`, and a new screen must not widen the till's own request layer
 *     to borrow one function. The precedence rule it encodes — explicit `ok`,
 *     then an `error` string, then the HTTP STATUS — is load-bearing and
 *     copied intact, because FastAPI's own 422 comes back as `{"detail": …}`
 *     with neither `ok` nor `error` and a rule that only read the body would
 *     file a validation crash as a success.
 *
 * NOTE ON DEV: `vite.config.ts` proxies an explicit allowlist of path prefixes
 * to the till on :8790. `/share` has to be in that list, and `share.router`
 * has to be mounted on the till, or every request here 404s. Neither of those
 * files is in this screen's scope; see the report.
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

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

/* ------------------------------------------------------------------ types -- */

/**
 * What every `/share/*` route returns. Field names read off `share.py`'s
 * `_composed()` and `_addressed()`, not guessed from the shape of another
 * module — a key that does not exist renders as `undefined` in the one caption
 * that mattered, and nothing tells you.
 *
 * `message` is the server's text VERBATIM and is displayed verbatim. It is the
 * thing the shopkeeper is about to send in his own name, so the preview has to
 * be the same bytes the link carries; a page that re-wrapped or prettified it
 * would be showing him something other than what goes out.
 */
export interface Composed {
  kind: 'receipt' | 'order' | 'reorder';
  message: string;
  message_chars: number;
  message_cap: number;
  /** Whether a receipt page address is inside the message. */
  link_included: boolean;
  /** Why it is not, when it is not — a loopback address, or an unreadable Host. */
  link_problem?: string;
  /** Always false. The server refuses to put a payment target in a message. */
  carries_a_payment_link: boolean;
  note: string;
}

/** The half that only exists once a number has been read and accepted. */
export interface Addressed extends Composed {
  /** E.164, as the server derived it: `+919876543210`. */
  to: string;
  /** The same number grouped for a person to check: `+91 98765 43210`. */
  to_display: string;
  /** THE ONLY EXTERNAL ADDRESS THIS PAGE OPENS. Opened, never edited. */
  wa_url: string;
  wa_host: string;
}

export interface ReceiptShare extends Composed {
  session_id: string;
  total_paise: number;
  total_rupees: string;
  payment_state: string;
  payment_headline: string;
  settled_by_verified_webhook: boolean;
  excluded_count: number;
  receipt_url: string | null;
}

export interface OrderShare extends Composed {
  order_id: string;
  status: string;
  total_paise: number;
  total_rupees: string;
  paid: boolean;
  /** The number the customer left when they ordered. Preview only. */
  phone_on_file?: string | null;
  /** Which number the link was built on: typed, or the one on the order. */
  phone_from?: string;
}

export interface ReorderShare extends Composed {
  /** Lines actually on the purchase order. */
  low_count: number;
  /** stock.py's own count of what is at or under a level, unfiltered. */
  at_or_under_level_count: number;
  unknown_count: number;
  needs_recount_count: number;
  supplier: { supplier_id: string; name: string; phone: string } | null;
  phone_on_file?: string | null;
  phone_from?: string;
  filtered_by_supplier: boolean;
}

/**
 * What this counter will and will not do with WhatsApp. Fetched and shown
 * rather than written into the page, because the page saying "nothing was
 * sent" is only true for as long as the server agrees, and a shopkeeper who
 * believes a receipt went out will stop checking that it did.
 */
export interface ShareLimits {
  sends_messages: boolean;
  host: string;
  how: string;
  why_not_the_api: string;
  carries_a_payment_link: boolean;
  payment_links_note: string;
  records_what_was_sent: boolean;
  records_note: string;
  numbers: { accepts: string; refuses: string; stated_limit: string };
  message_cap_chars: number;
  lines_in_a_message: number;
}

/* ----------------------------------------------------------------- calls -- */

export const shareLimits = () => send<ShareLimits>('/share/limits');

/**
 * The message with NO number attached.
 *
 * A GET, and deliberately without the phone: a query string is written to
 * every access log between the browser and the till, and a customer's number
 * does not belong in one. The number goes in the POST body below.
 */
export const previewReceipt = (sessionId: string) =>
  send<ReceiptShare>(`/share/receipt/${encodeURIComponent(sessionId)}`);

export const previewOrder = (orderId: string) =>
  send<OrderShare>(`/share/order/${encodeURIComponent(orderId)}`);

export const previewReorder = (supplierId?: string) =>
  send<ReorderShare>(
    supplierId
      ? `/share/reorder?supplier_id=${encodeURIComponent(supplierId)}`
      : '/share/reorder',
  );

/** The same message, plus the link that opens WhatsApp on one number. */
export const shareReceipt = (sessionId: string, phone: string) =>
  send<ReceiptShare & Addressed>(
    `/share/receipt/${encodeURIComponent(sessionId)}`, json({ phone }));

/** An empty `phone` means "the number the customer left on the order". */
export const shareOrder = (orderId: string, phone?: string) =>
  send<OrderShare & Addressed>(
    `/share/order/${encodeURIComponent(orderId)}`,
    json(phone ? { phone } : {}));

export const shareReorder = (phone?: string, supplierId?: string) =>
  send<ReorderShare & Addressed>('/share/reorder', json({
    ...(phone ? { phone } : {}),
    ...(supplierId ? { supplier_id: supplierId } : {}),
  }));

/* ------------------------------------------------------------- the target -- */

/**
 * What a ShareSheet is being opened for. One shape per thing this counter can
 * put on WhatsApp, so a caller cannot open the sheet for a receipt and hand it
 * an order id.
 */
export type ShareTarget =
  | { kind: 'receipt'; sessionId: string }
  | { kind: 'order'; orderId: string }
  | { kind: 'reorder'; supplierId?: string };

export const previewFor = (t: ShareTarget) =>
  t.kind === 'receipt' ? previewReceipt(t.sessionId)
    : t.kind === 'order' ? previewOrder(t.orderId)
      : previewReorder(t.supplierId);

export const addressFor = (t: ShareTarget, phone: string) =>
  t.kind === 'receipt' ? shareReceipt(t.sessionId, phone)
    : t.kind === 'order' ? shareOrder(t.orderId, phone || undefined)
      : shareReorder(phone || undefined, t.supplierId);

/**
 * Whether asking the server for a link is worth a request yet.
 *
 * NOT A VALIDATION. The server decides what an Indian mobile is and answers
 * with one of seven named refusals, each of which this sheet shows verbatim;
 * duplicating that rule here would be a second answer to the same question and
 * the two would drift. This only stops the page firing a request on every
 * keystroke of a number that is obviously half-typed.
 *
 * An EMPTY field is worth a request when the target has a number on file — an
 * order carries the customer's own — so emptiness is not the same as "not yet".
 */
export function worthAsking(phone: string, hasOneOnFile: boolean): boolean {
  const digits = phone.replace(/\D/g, '');
  if (digits.length === 0) return hasOneOnFile;
  return digits.length >= 10;
}
