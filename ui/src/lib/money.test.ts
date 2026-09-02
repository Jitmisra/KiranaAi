import { describe, it, expect } from 'vitest';
import { rupees, totalPaise, assertPaise } from './money';

describe('money is integer paise, and formatting must not invent a rounding error', () => {
  it('formats whole rupees and paise exactly', () => {
    expect(rupees(0)).toBe('₹0.00');
    expect(rupees(5)).toBe('₹0.05');
    expect(rupees(1000)).toBe('₹10.00');
    expect(rupees(10999)).toBe('₹109.99');
  });

  it('groups in the Indian system, because the shopkeeper reads it', () => {
    expect(rupees(10000000)).toBe('₹1,00,000.00');
  });

  it('is exact at every paise value across a wide sweep — no float drift', () => {
    for (let p = 0; p < 20000; p += 7) {
      const s = rupees(p, { symbol: false });
      const [w, c] = s.replace(/,/g, '').split('.') as [string, string];
      expect(Number(w) * 100 + Number(c)).toBe(p);
    }
  });

  it('refuses a float before it can reach a bill', () => {
    expect(() => assertPaise(10.5, 'test')).toThrow(/not a whole number/);
    expect(() => rupees(99.999)).toThrow();
  });

  it('refuses a value that is not a number at all', () => {
    expect(() => assertPaise('100' as unknown, 'test')).toThrow(/expected a number/);
    expect(() => assertPaise(NaN, 'test')).toThrow();
  });
});

describe('a column of prices', () => {
  it('totals by integer multiplication', () => {
    expect(totalPaise([{ price_paise: 999, qty: 3 }, { price_paise: 2200, qty: 2 }])).toBe(7397);
  });

  it('is empty at zero, not undefined', () => {
    expect(totalPaise([])).toBe(0);
  });

  it('refuses a fractional quantity', () => {
    expect(() => totalPaise([{ price_paise: 100, qty: 1.5 }])).toThrow(/whole count/);
  });

  it('refuses a fractional unit price rather than truncating it silently', () => {
    expect(() => totalPaise([{ price_paise: 33.33, qty: 3 }])).toThrow(/not a whole number/);
  });
});
