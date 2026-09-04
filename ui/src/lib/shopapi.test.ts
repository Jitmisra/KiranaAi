import { describe, it, expect } from 'vitest';
import {
  availableOf, isOut, shortLines, fitCart, fitCartToStore, fitCartToRefusal,
  STOCK_REFUSAL, type StoreItem,
} from './shopapi';

/**
 * The basket against the shelf.
 *
 * Three claims, each one a way the page could sell a packet the shop does not
 * have or refuse one it does:
 *
 *   - NULL IS NOT ZERO. A product with no stock figure has no cap. An older
 *     server that sends no field at all reads the same way.
 *   - THE PAGE ONLY EVER CUTS DOWN, and says what it cut. It never adds, never
 *     rounds, and never touches a line the server did not name.
 *   - THE REFUSAL'S NUMBERS ARE CHECKED before they are believed. A line with
 *     a fractional `available` is dropped rather than clamped to it.
 */

const item = (over: Partial<StoreItem> & { sku_id: string }): StoreItem => ({
  name: over.sku_id,
  price_paise: 1000,
  price_rupees: '10.00',
  has_photo: false,
  photo_url: null,
  taught_with: 'product_code_only',
  available_units: null,
  out_of_stock: false,
  reserved_units: 0,
  stock_note: 'no stock figure',
  ...over,
});

describe('the cap on a product', () => {
  it('is null for a product nobody has counted', () => {
    expect(availableOf(item({ sku_id: 'a' }))).toBeNull();
  });

  it('is null when an older server sends no field at all', () => {
    // `undefined`, not `null`: the field is simply absent from the JSON.
    expect(availableOf({ available_units: undefined as unknown as null })).toBeNull();
  });

  it('is the whole number the server sent, zero included', () => {
    expect(availableOf(item({ sku_id: 'a', available_units: 0 }))).toBe(0);
    expect(availableOf(item({ sku_id: 'a', available_units: 7 }))).toBe(7);
  });

  it('refuses a fraction or a negative rather than rounding it', () => {
    expect(availableOf(item({ sku_id: 'a', available_units: 1.5 }))).toBeNull();
    expect(availableOf(item({ sku_id: 'a', available_units: -1 }))).toBeNull();
  });

  it('is out only on an explicit true', () => {
    expect(isOut(item({ sku_id: 'a', out_of_stock: true }))).toBe(true);
    expect(isOut(item({ sku_id: 'a' }))).toBe(false);
    expect(isOut({ out_of_stock: undefined as unknown as boolean })).toBe(false);
  });
});

describe('fitting the basket to the shelf', () => {
  const caps = new Map([
    ['biscuit', { cap: 1, name: 'Parle-G' }],
    ['soap', { cap: 0, name: 'Lifebuoy' }],
    ['rice', { cap: null, name: 'Rice' }],
  ]);

  it('leaves a basket that fits exactly alone', () => {
    const r = fitCart({ biscuit: 1, rice: 40 }, caps);
    expect(r.cart).toEqual({ biscuit: 1, rice: 40 });
    expect(r.changes).toEqual([]);
  });

  it('cuts a line down to the cap and says so', () => {
    const r = fitCart({ biscuit: 3 }, caps);
    expect(r.cart).toEqual({ biscuit: 1 });
    expect(r.changes).toEqual([{ sku_id: 'biscuit', name: 'Parle-G', from: 3, to: 1 }]);
  });

  it('removes an out-of-stock line and says so', () => {
    const r = fitCart({ soap: 2 }, caps);
    expect(r.cart).toEqual({});
    expect(r.changes).toEqual([{ sku_id: 'soap', name: 'Lifebuoy', from: 2, to: 0 }]);
  });

  it('never caps a product with no figure', () => {
    const r = fitCart({ rice: 99 }, caps);
    expect(r.cart).toEqual({ rice: 99 });
    expect(r.changes).toEqual([]);
  });

  it('leaves a product it was not told about alone', () => {
    // "Not on sale" is pruned elsewhere, against the catalogue. Cutting an
    // unknown line here would conflate it with "none left".
    const r = fitCart({ ghee: 2 }, caps);
    expect(r.cart).toEqual({ ghee: 2 });
    expect(r.changes).toEqual([]);
  });

  it('reads the caps off a catalogue', () => {
    const items = [
      item({ sku_id: 'biscuit', name: 'Parle-G', available_units: 2 }),
      item({ sku_id: 'rice', name: 'Rice' }),
    ];
    const r = fitCartToStore({ biscuit: 5, rice: 5 }, items);
    expect(r.cart).toEqual({ biscuit: 2, rice: 5 });
    expect(r.changes.map((c) => c.sku_id)).toEqual(['biscuit']);
  });
});

describe('the stock refusal', () => {
  it('is only the one the server names', () => {
    expect(shortLines({ ok: false, reason: 'cart_is_empty' })).toBeNull();
  });

  it('reads the short lines as structure, checking every number', () => {
    const lines = shortLines({
      ok: false,
      reason: STOCK_REFUSAL,
      detail: 'Parle-G: 2 asked, 1 available',
      lines: [
        { sku_id: 'biscuit', name: 'Parle-G', asked: 2, available: 1, out_of_stock: false },
        { sku_id: 'soap', name: 'Lifebuoy', asked: 1, available: 0, out_of_stock: true },
        // A line whose figure is not a whole number is not believed.
        { sku_id: 'bad', name: 'Bad', asked: 1, available: 0.5, out_of_stock: false },
        'not a line',
      ],
    } as never);
    expect(lines).toEqual([
      { sku_id: 'biscuit', name: 'Parle-G', asked: 2, available: 1, out_of_stock: false },
      { sku_id: 'soap', name: 'Lifebuoy', asked: 1, available: 0, out_of_stock: true },
    ]);
  });

  it('fits the basket to exactly what the shop said', () => {
    const lines = shortLines({
      ok: false, reason: STOCK_REFUSAL,
      lines: [
        { sku_id: 'biscuit', name: 'Parle-G', asked: 2, available: 1, out_of_stock: false },
        { sku_id: 'soap', name: 'Lifebuoy', asked: 1, available: 0, out_of_stock: true },
      ],
    } as never)!;
    const r = fitCartToRefusal({ biscuit: 2, soap: 1, rice: 3 }, lines);
    expect(r.cart).toEqual({ biscuit: 1, rice: 3 });
    expect(r.changes).toEqual([
      { sku_id: 'biscuit', name: 'Parle-G', from: 2, to: 1 },
      { sku_id: 'soap', name: 'Lifebuoy', from: 1, to: 0 },
    ]);
  });
});
