import { describe, it, expect } from 'vitest';
import {
  billState, idleState, isStale, parseState, totalAgrees,
  DISPLAY_VERSION, STALE_MS, type DisplayState,
} from './displaybus';

/**
 * THE CONTRACT BETWEEN THE TWO SIDES OF THE COUNTER.
 *
 * The Till builds these; the customer display reads them. Nothing else in the
 * app touches this module, so if the two ever disagree the only symptom is a
 * customer-facing screen quietly showing the wrong thing — or showing nothing,
 * which is what it did for the whole of this product's life until the Till was
 * given a `publish` call.
 *
 * These tests are written from the TILL'S SIDE: every input below is a shape
 * `routes/Till.tsx` actually constructs, put through the same `billState` and
 * then through the display's own `parseState` as if it had crossed the wire.
 * A round trip that loses a field is a line a customer cannot see.
 */

/** What the Till hands `billState` for a two-line basket, one of them on offer. */
const BASKET = [
  { sku_id: 'parle_g', name: 'Parle-G biscuit 100g', qty: 2, price_paise: 1000 },
  { sku_id: 'pepsi', name: 'Pepsi 750ml', qty: 1, price_paise: 4500, marked_paise: 5000 },
];
const BASKET_TOTAL = 1000 * 2 + 4500;

/** The wire is JSON. Anything that does not survive it did not cross. */
const overTheWire = (s: DisplayState) => parseState(JSON.parse(JSON.stringify(s)));

describe('the phase the Till publishes', () => {
  it('is idle for an empty basket', () => {
    expect(idleState(null).phase).toBe('idle');
    expect(billState({ shop: null, lines: [], total_paise: 0, pay: null, paid: null }).phase).toBe('idle');
  });

  it('is bill once anything is on the counter', () => {
    const s = billState({ shop: null, lines: BASKET, total_paise: BASKET_TOTAL, pay: null, paid: null });
    expect(s.phase).toBe('bill');
    expect(s.total_paise).toBe(BASKET_TOTAL);
  });

  it('is pay while a link is live, even with the same lines', () => {
    const s = billState({
      shop: null, lines: BASKET, total_paise: BASKET_TOTAL,
      pay: { session_id: 'till_x', short_url: 'https://rzp.io/rzp/a', amount_paise: BASKET_TOTAL },
      paid: null,
    });
    expect(s.phase).toBe('pay');
  });

  it('is paid the moment the Till has seen the webhook, and outranks a live link', () => {
    // The Till clears the basket on PAID and holds the amount; both fields can
    // legitimately be set for one render, and PAID is the one that matters.
    const s = billState({
      shop: null, lines: [], total_paise: 0,
      pay: { session_id: 'till_x', short_url: 'https://rzp.io/rzp/a', amount_paise: 6500 },
      paid: { amount_paise: 6500 },
    });
    expect(s.phase).toBe('paid');
  });
});

describe('a state survives the wire intact', () => {
  it('keeps every line, its count and its shelf-edge price', () => {
    const sent = billState({ shop: 'Sharma Stores', lines: BASKET, total_paise: BASKET_TOTAL, pay: null, paid: null });
    const got = overTheWire(sent);
    expect(got).not.toBeNull();
    expect(got!.v).toBe(DISPLAY_VERSION);
    expect(got!.shop).toBe('Sharma Stores');
    expect(got!.lines).toHaveLength(2);
    expect(got!.lines[0]).toEqual({ sku_id: 'parle_g', name: 'Parle-G biscuit 100g', qty: 2, price_paise: 1000 });
    // The struck-through price is display-only and optional; a line without an
    // offer must not acquire one, and a line with one must not lose it.
    expect(got!.lines[0]!.marked_paise).toBeUndefined();
    expect(got!.lines[1]!.marked_paise).toBe(5000);
  });

  it('keeps the session id and the gateway link, and no payment target of its own', () => {
    const sent = billState({
      shop: null, lines: BASKET, total_paise: BASKET_TOTAL,
      pay: { session_id: 'till_abc', short_url: 'https://rzp.io/rzp/xyz', amount_paise: BASKET_TOTAL },
      paid: null,
    });
    const got = overTheWire(sent)!;
    expect(got.pay).toEqual({ session_id: 'till_abc', short_url: 'https://rzp.io/rzp/xyz', amount_paise: BASKET_TOTAL });
    // There is no field for a UPI string, an amount the display may edit, or a
    // price the server did not set. The display asks `/qr/link/{session}`.
    expect(Object.keys(got.pay!).sort()).toEqual(['amount_paise', 'session_id', 'short_url']);
  });

  it('gives every publish its own id, so a message heard twice is shown once', () => {
    const a = billState({ shop: null, lines: BASKET, total_paise: BASKET_TOTAL, pay: null, paid: null });
    const b = billState({ shop: null, lines: BASKET, total_paise: BASKET_TOTAL, pay: null, paid: null });
    expect(a.id).not.toBe(b.id);
  });
});

describe('what the Till may not publish', () => {
  it('refuses a fractional price rather than rounding it onto a customer screen', () => {
    expect(() => billState({
      shop: null, lines: [{ sku_id: 'x', name: 'x', qty: 1, price_paise: 10.5 }],
      total_paise: 11, pay: null, paid: null,
    })).toThrow(/whole number of paise/);
  });

  it('refuses a fractional count', () => {
    expect(() => billState({
      shop: null, lines: [{ sku_id: 'x', name: 'x', qty: 1.5, price_paise: 100 }],
      total_paise: 150, pay: null, paid: null,
    })).toThrow(/whole count/);
  });
});

describe('the display checks the total rather than trusting it', () => {
  it('agrees when the lines add up to what the Till sent', () => {
    const s = billState({ shop: null, lines: BASKET, total_paise: BASKET_TOTAL, pay: null, paid: null });
    expect(totalAgrees(s)).toBe(true);
  });

  it('disagrees when they do not — and it is the disagreement that is shown, not a number', () => {
    const s = billState({ shop: null, lines: BASKET, total_paise: BASKET_TOTAL + 1, pay: null, paid: null });
    expect(totalAgrees(s)).toBe(false);
  });
});

describe('a bill too old to put in front of a customer', () => {
  const bill = (at: number): DisplayState => ({
    ...billState({ shop: null, lines: BASKET, total_paise: BASKET_TOTAL, pay: null, paid: null }), at,
  });

  it('is fresh well inside the window', () => {
    const now = Date.now();
    expect(isStale(bill(now - 60_000), now)).toBe(false);
  });

  it('is stale past it', () => {
    const now = Date.now();
    expect(isStale(bill(now - STALE_MS - 1000), now)).toBe(true);
  });

  it('never expires an idle screen — an empty counter at 9am is still empty at noon', () => {
    const now = Date.now();
    expect(isStale({ ...idleState(null), at: now - 10 * STALE_MS }, now)).toBe(false);
  });
});

describe('untrusted messages are dropped, not repaired', () => {
  it('rejects a version this display does not know', () => {
    const s = billState({ shop: null, lines: BASKET, total_paise: BASKET_TOTAL, pay: null, paid: null });
    expect(parseState({ ...s, v: 99 })).toBeNull();
  });

  it('rejects a bill with one unreadable line rather than showing it short', () => {
    const s = billState({ shop: null, lines: BASKET, total_paise: BASKET_TOTAL, pay: null, paid: null });
    expect(parseState({ ...s, lines: [s.lines[0], { sku_id: 'y', name: 'y', qty: 1, price_paise: 'free' }] }))
      .toBeNull();
  });

  it('rejects a pay phase with nothing to pay', () => {
    const s = billState({ shop: null, lines: BASKET, total_paise: BASKET_TOTAL, pay: null, paid: null });
    expect(parseState({ ...s, phase: 'pay' })).toBeNull();
  });

  it('rejects a paid phase with no settled amount', () => {
    const s = billState({ shop: null, lines: BASKET, total_paise: BASKET_TOTAL, pay: null, paid: null });
    expect(parseState({ ...s, phase: 'paid' })).toBeNull();
  });

  it('rejects anything that is not an object at all', () => {
    for (const junk of [null, undefined, 4, 'a bill', [], true]) {
      expect(parseState(junk)).toBeNull();
    }
  });
});
