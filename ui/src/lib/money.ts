/**
 * Money. INTEGER PAISE ONLY.
 *
 * Invariant 1 of this product: no float ever touches a price. The Python side
 * enforces it with `tools/lint_no_float.py`; JavaScript has no integer type to
 * lean on, so the discipline has to be explicit here instead:
 *
 *   - every amount is a whole number of paise, asserted at the boundary;
 *   - rupees are produced by integer subtraction and remainder, never by
 *     `p / 100`, which would produce a float one rounding step away from a
 *     bill that does not add up;
 *   - nothing in this file computes a price. It formats a price the server
 *     already decided. The browser is not an author of money.
 */

export type Paise = number;

/** The largest amount we will handle without complaint: ₹10,00,000. */
const SANE_MAX: Paise = 100_000_000;

export function assertPaise(n: unknown, where: string): Paise {
  if (typeof n !== 'number' || !Number.isFinite(n)) {
    throw new TypeError(`${where}: expected a number of paise, got ${JSON.stringify(n)}`);
  }
  if (!Number.isInteger(n)) {
    throw new TypeError(`${where}: ${n} is not a whole number of paise — a float reached money`);
  }
  if (n < 0 || n > SANE_MAX) {
    throw new RangeError(`${where}: ${n} paise is outside the range this till will price`);
  }
  return n;
}

/**
 * Format paise as rupees, exactly.
 *
 * `(a - a % 100) / 100` divides a number that is already a multiple of 100, so
 * the result is exact for every value inside Number.MAX_SAFE_INTEGER. Writing
 * `a / 100` here would be the single most likely place for a rounding error to
 * enter a bill, which is why it is spelled out.
 */
export function rupees(paise: Paise, opts: { symbol?: boolean } = {}): string {
  const p = assertPaise(paise, 'rupees');
  const whole = (p - (p % 100)) / 100;
  const rest = p % 100;
  const grouped = whole.toLocaleString('en-IN');
  const sym = opts.symbol === false ? '' : '₹';
  return `${sym}${grouped}.${String(rest).padStart(2, '0')}`;
}

/** Sum a column of paise. Integer addition only; overflow is asserted, not wrapped. */
export function totalPaise(lines: ReadonlyArray<{ price_paise: Paise; qty: number }>): Paise {
  let sum = 0;
  for (const l of lines) {
    const unit = assertPaise(l.price_paise, 'totalPaise line');
    if (!Number.isInteger(l.qty) || l.qty < 0) {
      throw new TypeError(`totalPaise: quantity ${l.qty} is not a whole count`);
    }
    sum += unit * l.qty;
  }
  return assertPaise(sum, 'totalPaise sum');
}
