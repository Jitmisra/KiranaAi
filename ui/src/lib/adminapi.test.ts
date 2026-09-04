import { describe, expect, it } from 'vitest';
import { rupeesForInput, DAYS } from './adminapi';

/**
 * The one piece of arithmetic on money that happens in the BROWSER on the edit
 * path: turning stored paise back into the rupee string a shopkeeper edits and
 * sends straight back.
 *
 * It matters because the string it produces is parsed by `gawaah/money.py` on
 * the way home. A grouped `1,234.50`, a `₹` or a `12.5` would each be refused
 * by the server — correctly, and confusingly, since the page put them there.
 */
describe('rupeesForInput', () => {
  it('renders exactly two decimal places, ungrouped and unsigned', () => {
    expect(rupeesForInput(2145)).toBe('21.45');
    expect(rupeesForInput(1000)).toBe('10.00');
    expect(rupeesForInput(5)).toBe('0.05');
    expect(rupeesForInput(50)).toBe('0.50');
  });

  it('does not group thousands — the server refuses a comma', () => {
    expect(rupeesForInput(123450)).toBe('1234.50');
    expect(rupeesForInput(100000000)).toBe('1000000.00');
  });

  it('never emits a currency symbol', () => {
    expect(rupeesForInput(2145)).not.toContain('₹');
  });

  it('round-trips every paisa of a rupee without losing one', () => {
    // The failure this guards is a `/ 100` producing 21.449999999999999.
    for (let p = 0; p < 500; p += 1) {
      const s = rupeesForInput(p);
      const [whole, frac] = s.split('.');
      expect(Number(whole) * 100 + Number(frac)).toBe(p);
    }
  });

  it('answers an unset price with an empty box, not a zero', () => {
    // A zero would be a price the shop never set, and saving it back would be
    // refused as "0 is not a price" — after the page had already shown it.
    expect(rupeesForInput(null)).toBe('');
    expect(rupeesForInput(undefined)).toBe('');
    expect(rupeesForInput(12.5 as unknown as number)).toBe('');
  });
});

describe('DAYS', () => {
  it('is in week order, because the question is which day the shop is shut', () => {
    expect(DAYS.map((d) => d.key)).toEqual(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']);
  });
});
