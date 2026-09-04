/**
 * THE CUSTOMER DISPLAY BUS.
 *
 * A second screen faces the customer and shows the bill as it is built. That
 * screen is another tab or window of the SAME browser — a second monitor, a
 * tablet running the same profile — and this module is the wire between the
 * Till and it. No server is involved: the Till publishes, the display listens.
 *
 * Two transports, both browser-native, both same-origin:
 *
 *   1. BroadcastChannel — every other tab hears a publish the moment it
 *      happens. Not replayed: a display opened AFTER the bill started would
 *      hear nothing until the next change.
 *   2. localStorage — every publish is also written under one key. A display
 *      that opens late reads the last state straight off it, and browsers
 *      without BroadcastChannel still get the `storage` event, which fires in
 *      every OTHER tab whenever the key changes.
 *
 * The same publish therefore reaches a display twice, so each message carries
 * an id and the subscriber drops the second copy.
 *
 * Three rules this file keeps:
 *
 *   - THE DISPLAY IS NEVER AN AUTHOR. What crosses this bus is what the Till
 *     already showed the shopkeeper: server-priced lines, the integer total the
 *     Till computed from them, and the session id of a link the gateway minted.
 *     There is no field here for a price the server did not set and no field
 *     for a payment target — the payment QR is fetched from `/qr/link` by the
 *     display, from the session id alone.
 *   - INTEGER PAISE. Every amount is asserted on the way in (`billState`) and
 *     again on the way out (`parseState`). A message that fails is dropped,
 *     not repaired.
 *   - A LATE READER MUST KNOW IT IS LATE. Every state is stamped with the
 *     publisher's clock; a bill older than `STALE_MS` is treated as idle by
 *     `isStale`, because a customer must not be shown yesterday's total, or —
 *     worse — yesterday's payable QR.
 *
 * What this cannot do, stated plainly: reach a DIFFERENT device. A phone on the
 * shop's Wi-Fi is another browser with its own storage, and nothing here can
 * see it. That needs a relay on the server and is not built.
 */

import { assertPaise, totalPaise, type Paise } from './money';

/** One channel name and one storage key. Versioned so a schema change cannot
    be read by a display that predates it. */
export const DISPLAY_CHANNEL = 'gawaah-display-v1';
export const DISPLAY_KEY = 'gawaah.display.v1';
export const DISPLAY_VERSION = 1 as const;

/**
 * How old a bill may be before the display refuses to show it. Thirty minutes
 * is longer than any real transaction at a counter and shorter than "the Till
 * crashed before lunch". Idle states never go stale — an empty counter at
 * 9 a.m. is still an empty counter at noon.
 */
export const STALE_MS = 30 * 60 * 1000;

export type DisplayPhase = 'idle' | 'bill' | 'pay' | 'paid';

export interface DisplayLine {
  sku_id: string;
  name: string;
  /** A count, never money. A whole number of at least one. */
  qty: number;
  /** Unit price the server put on the catalogue, in integer paise. */
  price_paise: Paise;
  /** The shelf-edge price when the line is under an offer. Display only. */
  marked_paise?: Paise;
}

export interface DisplayPay {
  /** The Till's session id. The display fetches `/qr/link/{session_id}`. */
  session_id: string;
  /** The opaque link the gateway issued. Shown as text only after the server
      has rendered it as a QR — that render is the allowlist check. */
  short_url: string;
  /** What the gateway will collect: the amount paisa re-derived and minted. */
  amount_paise: Paise;
}

export interface DisplayState {
  v: typeof DISPLAY_VERSION;
  /** Unique per publish, so a message heard on both transports is shown once. */
  id: string;
  /** The publisher's `Date.now()`. Drives `isStale`. */
  at: number;
  /** The shop's name, or null when the profile has none. The display says so. */
  shop: string | null;
  phase: DisplayPhase;
  lines: DisplayLine[];
  /** The Till's own integer total of `lines`. Cross-checked by the display. */
  total_paise: Paise;
  pay: DisplayPay | null;
  /** Set only after the Till saw a signature-verified webhook. */
  paid: { amount_paise: Paise } | null;
}

/* ---------------------------------------------------------------- shape -- */

function isRecord(x: unknown): x is Record<string, unknown> {
  return typeof x === 'object' && x !== null && !Array.isArray(x);
}

function isWholeCount(n: unknown): n is number {
  return typeof n === 'number' && Number.isInteger(n) && n >= 1 && n <= 10_000;
}

function paiseOrNull(n: unknown, where: string): Paise | null {
  try {
    return assertPaise(n, where);
  } catch {
    return null;
  }
}

/**
 * Parse an untrusted message into a DisplayState, or null.
 *
 * Untrusted because it arrived over a channel any same-origin script can write
 * to, and because a Till from a previous build may have written the key. A
 * message is accepted whole or not at all — a bill with one unreadable line is
 * not shown short, it is not shown.
 */
export function parseState(raw: unknown): DisplayState | null {
  if (!isRecord(raw)) return null;
  if (raw.v !== DISPLAY_VERSION) return null;
  if (typeof raw.id !== 'string' || raw.id.length === 0 || raw.id.length > 64) return null;
  if (typeof raw.at !== 'number' || !Number.isFinite(raw.at) || raw.at <= 0) return null;
  const shop = raw.shop === null ? null : typeof raw.shop === 'string' ? raw.shop : undefined;
  if (shop === undefined) return null;
  const phase = raw.phase;
  if (phase !== 'idle' && phase !== 'bill' && phase !== 'pay' && phase !== 'paid') return null;
  if (!Array.isArray(raw.lines) || raw.lines.length > 500) return null;

  const lines: DisplayLine[] = [];
  for (const l of raw.lines) {
    if (!isRecord(l)) return null;
    if (typeof l.sku_id !== 'string' || typeof l.name !== 'string') return null;
    if (!isWholeCount(l.qty)) return null;
    const price = paiseOrNull(l.price_paise, 'display line');
    if (price === null) return null;
    const line: DisplayLine = { sku_id: l.sku_id, name: l.name, qty: l.qty, price_paise: price };
    if (l.marked_paise !== undefined) {
      const m = paiseOrNull(l.marked_paise, 'display marked');
      if (m === null) return null;
      line.marked_paise = m;
    }
    lines.push(line);
  }

  const total = paiseOrNull(raw.total_paise, 'display total');
  if (total === null) return null;

  let pay: DisplayPay | null = null;
  if (raw.pay !== null && raw.pay !== undefined) {
    if (!isRecord(raw.pay)) return null;
    if (typeof raw.pay.session_id !== 'string' || raw.pay.session_id.length === 0) return null;
    if (typeof raw.pay.short_url !== 'string') return null;
    const amt = paiseOrNull(raw.pay.amount_paise, 'display pay');
    if (amt === null) return null;
    pay = { session_id: raw.pay.session_id, short_url: raw.pay.short_url, amount_paise: amt };
  }

  let paid: { amount_paise: Paise } | null = null;
  if (raw.paid !== null && raw.paid !== undefined) {
    if (!isRecord(raw.paid)) return null;
    const amt = paiseOrNull(raw.paid.amount_paise, 'display paid');
    if (amt === null) return null;
    paid = { amount_paise: amt };
  }

  // The phase must agree with what it claims to describe.
  if (phase === 'pay' && !pay) return null;
  if (phase === 'paid' && !paid) return null;
  if (phase === 'bill' && lines.length === 0) return null;

  return { v: DISPLAY_VERSION, id: raw.id, at: raw.at, shop, phase, lines, total_paise: total, pay, paid };
}

/** A bill older than STALE_MS must not be shown. Idle never expires. */
export function isStale(s: DisplayState, now: number = Date.now()): boolean {
  if (s.phase === 'idle') return false;
  return now - s.at > STALE_MS;
}

/**
 * Does the published total agree with the published lines?
 *
 * The Till computes both from the same basket, so they agree unless a
 * publisher has a bug. The display checks anyway: a customer-facing total that
 * cannot be derived from the lines above it is a number nobody can stand
 * behind, and the display says so rather than picking one.
 */
export function totalAgrees(s: DisplayState): boolean {
  try {
    return totalPaise(s.lines) === s.total_paise;
  } catch {
    return false;
  }
}

/* -------------------------------------------------------------- builder -- */

let seq = 0;
function nextId(): string {
  seq += 1;
  return `${Date.now().toString(36)}-${seq.toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * Build the state the Till should publish, deriving the phase.
 *
 *   paid set         -> 'paid'
 *   pay set          -> 'pay'
 *   any lines        -> 'bill'
 *   otherwise        -> 'idle'
 *
 * Throws on a non-integer amount or a fractional count, the same way
 * `totalPaise` does — the Till has already run every line through that, so a
 * throw here means a float reached the bill somewhere upstream and the right
 * response is a loud failure, not a quiet display.
 */
export function billState(input: {
  shop: string | null;
  lines: ReadonlyArray<{ sku_id: string; name: string; qty: number; price_paise: Paise; marked_paise?: Paise }>;
  total_paise: Paise;
  pay: DisplayPay | null;
  paid: { amount_paise: Paise } | null;
}): DisplayState {
  const lines: DisplayLine[] = input.lines.map((l) => {
    if (!isWholeCount(l.qty)) throw new TypeError(`display line ${l.sku_id}: quantity ${l.qty} is not a whole count`);
    const line: DisplayLine = {
      sku_id: l.sku_id, name: l.name, qty: l.qty,
      price_paise: assertPaise(l.price_paise, `display line ${l.sku_id}`),
    };
    if (l.marked_paise !== undefined) line.marked_paise = assertPaise(l.marked_paise, `display marked ${l.sku_id}`);
    return line;
  });
  const phase: DisplayPhase = input.paid ? 'paid' : input.pay ? 'pay' : lines.length ? 'bill' : 'idle';
  return {
    v: DISPLAY_VERSION,
    id: nextId(),
    at: Date.now(),
    shop: input.shop,
    phase,
    lines,
    total_paise: assertPaise(input.total_paise, 'display total'),
    pay: input.pay
      ? { ...input.pay, amount_paise: assertPaise(input.pay.amount_paise, 'display pay') }
      : null,
    paid: input.paid ? { amount_paise: assertPaise(input.paid.amount_paise, 'display paid') } : null,
  };
}

/** The empty counter. What the Till publishes when the bill is cleared or the screen closes. */
export function idleState(shop: string | null): DisplayState {
  return billState({ shop, lines: [], total_paise: 0, pay: null, paid: null });
}

/* ------------------------------------------------------------ transport -- */

let channel: BroadcastChannel | null | undefined;

/** Lazily opened; null where the browser has no BroadcastChannel. */
function bc(): BroadcastChannel | null {
  if (channel !== undefined) return channel;
  try {
    channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel(DISPLAY_CHANNEL) : null;
  } catch {
    channel = null;
  }
  return channel;
}

function readStored(): DisplayState | null {
  try {
    const raw = localStorage.getItem(DISPLAY_KEY);
    if (!raw) return null;
    return parseState(JSON.parse(raw));
  } catch {
    return null;
  }
}

/**
 * Publish a state to every display in this browser.
 *
 * Storage first, then the channel: a display woken by the channel message that
 * then reads storage must find the same state, not the one before it.
 * Each transport is wrapped on its own — a full or disabled localStorage must
 * not stop the channel, and vice versa.
 */
export function publish(state: DisplayState): void {
  try {
    localStorage.setItem(DISPLAY_KEY, JSON.stringify(state));
  } catch {
    /* private mode, quota, or storage disabled — the channel still carries it */
  }
  try {
    bc()?.postMessage(state);
  } catch {
    /* no channel — the storage event carried it */
  }
}

/** The last state any Till in this browser published, or null if none ever has. */
export function current(): DisplayState | null {
  return readStored();
}

/**
 * Hear every publish from now on. Returns the unsubscribe.
 *
 * Does NOT replay the current state — call `current()` for that — so a
 * subscriber sees each state exactly once, whichever transport delivered it.
 */
export function subscribe(fn: (s: DisplayState) => void): () => void {
  let lastId: string | null = null;
  const deliver = (raw: unknown) => {
    const s = parseState(raw);
    if (!s || s.id === lastId) return;
    lastId = s.id;
    fn(s);
  };

  const onMessage = (e: MessageEvent) => deliver(e.data);
  const onStorage = (e: StorageEvent) => {
    if (e.key !== DISPLAY_KEY || e.newValue === null) return;
    try {
      deliver(JSON.parse(e.newValue));
    } catch {
      /* not ours */
    }
  };

  const ch = bc();
  ch?.addEventListener('message', onMessage);
  addEventListener('storage', onStorage);
  return () => {
    ch?.removeEventListener('message', onMessage);
    removeEventListener('storage', onStorage);
  };
}
