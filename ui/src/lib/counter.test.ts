import { describe, it, expect } from 'vitest';
import { packetKey, boxScale, headline, addToBasket, PacketTracker, StreakTracker, STABLE_N, ABSENT_FRAMES, RECOMMIT_COOLDOWN_MS, type ScanItem, type BasketLine, setQty, decLine, incLine, removeLine, DELETE_SUPPRESS_MS } from './counter';
import { totalPaise, type Paise } from './money';

const packet = (code: string, x = 100, y = 100): ScanItem => ({
  code, sku_id: 'parle_g', name: 'Parle-G', price_paise: 1000,
  box: [x, y, 60, 60], gate: 'product_code',
});
const bill = (b: ReadonlyMap<string, BasketLine>) => totalPaise([...b.values()]);

describe('when a packet becomes a line', () => {
  it('bills a decoded code on the FIRST read, with no consecutive-frame wait', () => {
    const t = new PacketTracker();
    expect(t.observe([packet('891')])).toHaveLength(1);
  });

  it('does not bill a held packet again frame after frame', () => {
    const t = new PacketTracker();
    let basket = new Map<string, BasketLine>();
    for (let i = 0; i < 25; i++) basket = addToBasket(basket, t.observe([packet('891')]));
    expect(bill(basket)).toBe(1000);
  });

  it('survives a one-frame dropout without re-billing', () => {
    const t = new PacketTracker();
    let basket = new Map<string, BasketLine>();
    for (const f of [['891'], [], ['891'], [], ['891']]) {
      basket = addToBasket(basket, t.observe(f.map((c) => packet(c))));
    }
    expect(bill(basket)).toBe(1000);
  });

  it('bills twice when a packet is really taken away and brought back LATER', () => {
    // Absence alone is no longer enough — it also has to be outside the
    // five-second cooldown. A shopkeeper's thumb covers a code for a second at
    // a time all day, and each of those used to become another line.
    const t = new PacketTracker();
    let basket = new Map<string, BasketLine>();
    basket = addToBasket(basket, t.observe([packet('891')], 0));
    for (let i = 0; i < ABSENT_FRAMES + 1; i++) {
      basket = addToBasket(basket, t.observe([], 100 + i * 240));
    }
    basket = addToBasket(basket, t.observe([packet('891')], RECOMMIT_COOLDOWN_MS + 500));
    expect(bill(basket)).toBe(2000);
  });

  it('bills two identical packets in one frame twice — position keys, not payload keys', () => {
    const t = new PacketTracker();
    const basket = addToBasket(basket0(), t.observe([packet('891', 100), packet('891', 400)]));
    expect(bill(basket)).toBe(2000);
  });

  it('treats a hand that is not quite still as the same packet', () => {
    const t = new PacketTracker();
    let basket = new Map<string, BasketLine>();
    for (const dx of [0, 6, 12, 4, 9]) basket = addToBasket(basket, t.observe([packet('891', 100 + dx)]));
    expect(bill(basket)).toBe(1000);
  });
});

const basket0 = () => new Map<string, BasketLine>();

describe('appearance mode is slower on purpose', () => {
  it(`waits ${STABLE_N} consecutive frames before billing a look`, () => {
    const s = new StreakTracker();
    const fired = [1, 2, 3, 4].map(() => s.observe('parle_g'));
    expect(fired).toEqual([false, false, true, false]);
  });

  it('restarts the streak when the guess changes', () => {
    const s = new StreakTracker();
    s.observe('parle_g'); s.observe('parle_g'); s.observe('lifebuoy');
    expect(s.observe('lifebuoy')).toBe(false);
  });

  it('reports an abstention once, not once per frame', () => {
    const s = new StreakTracker();
    s.observe(null);
    expect(s.abstained).toBe(true);
  });
});

describe('boxes are drawn in the space the server measured them in', () => {
  it('scales boxes from the server working image back to the counter area', () => {
    const frame = { items: [{ measured: { frame_px: [640, 360] as [number, number] } }] };
    expect(boxScale(frame, { w: 1280, h: 720 })).toEqual({ sx: 2, sy: 2 });
  });

  it('falls back to 1:1 when the server did not say', () => {
    expect(boxScale({ items: [] }, { w: 800, h: 600 })).toEqual({ sx: 1, sy: 1 });
  });

  it('keys a packet by where it stands, so two of a kind stay two', () => {
    expect(packetKey(packet('891', 100))).not.toBe(packetKey(packet('891', 400)));
  });
});

describe('what the counter says it saw', () => {
  it('separates symbols from distinct codes', () => {
    const h = headline({ codes_found: 3, distinct_codes: 2, items: [] });
    expect([h.symbols, h.distinct]).toEqual([3, 2]);
  });

  it('counts untaught codes so an operator knows why a total looks short', () => {
    const h = headline({
      codes_found: 2,
      items: [packet('891'), { code: '999', box: [0, 0, 9, 9], reason: 'not taught' }],
    });
    expect(h.untaught).toBe(1);
    expect(h.named).toHaveLength(1);
  });
});

describe('the bill itself', () => {
  it('adds quantity rather than duplicating a line', () => {
    let b = addToBasket(basket0(), [packet('891', 100)]);
    b = addToBasket(b, [packet('891', 400)]);
    expect(b.size).toBe(1);
    expect(b.get('parle_g')?.qty).toBe(2);
    expect(bill(b)).toBe(2000);
  });

  it('never adds an untaught item to the total', () => {
    const b = addToBasket(basket0(), [{ code: '999', box: [0, 0, 9, 9], reason: 'not taught' }]);
    expect(b.size).toBe(0);
  });

  it('returns a new map, so React sees the change', () => {
    const a = basket0();
    expect(addToBasket(a, [packet('891')])).not.toBe(a);
  });
});


/**
 * THE FIVE-SECOND COOLDOWN.
 *
 * Position keying was not enough in a real hand: a packet the shopkeeper is
 * still holding drifts over the 64 px bucket boundary, or a thumb covers the
 * code for more than ABSENT_FRAMES, and it comes back as a new packet. One
 * biscuit packet became three lines.
 */
describe('the same code does not go on the bill twice within five seconds', () => {
  const at = (t: number) => t;              // an explicit clock, so no test sleeps
  const parle = (x = 100) => packet('891', x);
  const soap = (): ScanItem => ({
    code: '999', sku_id: 'lifebuoy', name: 'Lifebuoy', price_paise: 3500,
    box: [600, 100, 60, 60], gate: 'product_code',
  });

  it('refuses a re-read of the same code one second later', () => {
    const t = new PacketTracker();
    expect(t.observe([parle(100)], at(0))).toHaveLength(1);
    // gone long enough to count as removed...
    for (let i = 0; i < ABSENT_FRAMES + 1; i++) t.observe([], at(200 + i * 240));
    // ...and brought straight back. Under the old rule this billed again.
    expect(t.observe([parle(100)], at(1200)), 'the same packet re-billed inside the cooldown').toHaveLength(0);
  });

  it('refuses even when the packet drifts to a new position', () => {
    const t = new PacketTracker();
    t.observe([parle(100)], at(0));
    // A drift of 300 px is a different position key entirely.
    expect(t.observe([parle(400)], at(900))).toHaveLength(0);
  });

  it('allows it again once five seconds have passed AND it actually left', () => {
    const t = new PacketTracker();
    t.observe([parle()], at(0));
    // Still in view at 4.999 s: blocked, and correctly so.
    expect(t.observe([parle()], at(RECOMMIT_COOLDOWN_MS - 1))).toHaveLength(0);
    // It leaves...
    for (let i = 0; i < ABSENT_FRAMES + 1; i++) t.observe([], at(RECOMMIT_COOLDOWN_MS + i * 240));
    // ...and comes back after the cooldown. Now it is a second sale.
    expect(t.observe([parle()], at(RECOMMIT_COOLDOWN_MS * 2))).toHaveLength(1);
  });

  it('never re-bills a packet that simply stays on the counter', () => {
    // The cooldown expiring is NOT permission to bill again. One packet left
    // sitting in view for half a minute is one packet, not six.
    const t = new PacketTracker();
    let n = 0;
    for (let i = 0; i < 130; i++) n += t.observe([parle()], at(i * 240)).length;
    expect(n, 'a packet that never moved was billed more than once').toBe(1);
  });

  it('lets a DIFFERENT product through immediately — the cooldown is per code', () => {
    const t = new PacketTracker();
    t.observe([parle()], at(0));
    const fresh = t.observe([parle(), soap()], at(500));
    expect(fresh, 'a different product was blocked by another product’s cooldown').toHaveLength(1);
    expect(fresh[0]!.sku_id).toBe('lifebuoy');
  });

  it('still bills two identical packets put down TOGETHER', () => {
    // A supermarket lane must be able to sell two of the same thing at once.
    const t = new PacketTracker();
    expect(t.observe([packet('891', 100), packet('891', 500)], at(0))).toHaveLength(2);
  });

  it('reports how long is left, so the counter can show it', () => {
    const t = new PacketTracker();
    t.observe([parle()], at(0));
    expect(t.cooldownLeft('891', at(2000))).toBe(RECOMMIT_COOLDOWN_MS - 2000);
    expect(t.cooldownLeft('891', at(RECOMMIT_COOLDOWN_MS + 10))).toBe(0);
    expect(t.cooldownLeft('never-seen', at(0))).toBe(0);
  });

  it('a held packet is still billed exactly once across a long hold', () => {
    const t = new PacketTracker();
    let n = 0;
    for (let i = 0; i < 120; i++) n += t.observe([parle()], at(i * 240)).length;
    expect(n, 'a packet held for 28 seconds was billed more than once').toBe(1);
  });
});

/* ---------------------------------------------- the operator overrules it -- */

describe('editing a bill line', () => {
  const bill = (): Map<string, BasketLine> => new Map([
    ['a', { sku_id: 'a', name: 'Parle-G', price_paise: 1000 as Paise, qty: 2 }],
    ['b', { sku_id: 'b', name: 'Soap', price_paise: 3500 as Paise, qty: 1 }],
  ]);

  it('changes a quantity without touching the price', () => {
    const next = setQty(bill(), 'a', 5);
    expect(next.get('a')!.qty).toBe(5);
    expect(next.get('a')!.price_paise).toBe(1000);
    expect(next.get('b')!.qty).toBe(1);
  });

  it('removes the line at zero rather than storing a zero quantity', () => {
    // A zero-quantity row charges nothing and reads to a shopkeeper as an item
    // they forgot to price.
    expect(setQty(bill(), 'a', 0).has('a')).toBe(false);
    expect(setQty(bill(), 'a', -3).has('a')).toBe(false);
  });

  it('decrements down to removal', () => {
    let b = bill();
    b = decLine(b, 'a');
    expect(b.get('a')!.qty).toBe(1);
    b = decLine(b, 'a');
    expect(b.has('a')).toBe(false);
  });

  it('increments only a line that is already on the bill', () => {
    expect(incLine(bill(), 'a').get('a')!.qty).toBe(3);
    // Never invents a line — the till may only bill what the camera or the
    // operator's own voice put there, and a + on nothing has no price to use.
    expect(incLine(bill(), 'nope').has('nope')).toBe(false);
  });

  it('removes a whole line', () => {
    const next = removeLine(bill(), 'b');
    expect(next.has('b')).toBe(false);
    expect(next.size).toBe(1);
  });

  it('does not mutate the basket it was given', () => {
    const before = bill();
    setQty(before, 'a', 9);
    decLine(before, 'a');
    removeLine(before, 'a');
    expect(before.get('a')!.qty).toBe(2);
    expect(before.size).toBe(2);
  });

  it('ignores a line that is not on the bill', () => {
    expect(setQty(bill(), 'ghost', 4).size).toBe(2);
    expect(removeLine(bill(), 'ghost').size).toBe(2);
  });

  it('floors a fractional quantity — a count is an integer', () => {
    expect(setQty(bill(), 'a', 2.9).get('a')!.qty).toBe(2);
  });

  it('keeps the line total in integer paise at every quantity', () => {
    for (const q of [1, 2, 3, 7, 99]) {
      const line = setQty(bill(), 'a', q).get('a')!;
      const total = line.price_paise * line.qty;
      expect(Number.isInteger(total)).toBe(true);
    }
  });
});

/* ------------------------------- the operator's delete outranks the camera -- */

describe('a removed line stays off the bill for a moment', () => {
  const seenAt = (t: PacketTracker, code: string, x: number, now: number) =>
    t.observe([{ code, sku_id: 'parle_g', name: 'Parle-G', price_paise: 1000,
                 box: [x, 100, 60, 60], gate: 'product_code' }], now);

  it('does not re-commit a product the operator just removed', () => {
    const t = new PacketTracker();
    expect(seenAt(t, 'C1', 100, 0)).toHaveLength(1);      // billed
    t.suppress('parle_g', 0);                             // operator deletes it
    // It leaves view and comes straight back — the exact sequence that used to
    // resurrect the line a second after it was deleted.
    for (let i = 1; i <= ABSENT_FRAMES + 1; i++) t.observe([], i * 10);
    expect(seenAt(t, 'C1', 100, 500)).toHaveLength(0);
    expect(seenAt(t, 'C1', 100, 2900)).toHaveLength(0);
  });

  it('lets it be billed again once the hold expires', () => {
    // A customer genuinely handing over a second one must still be charged.
    //
    // TWO INDEPENDENT HOLDS, and this test isolates the delete one. The
    // five-second RECOMMIT cooldown also runs from the original commit, so
    // probing at DELETE_SUPPRESS_MS + 1 measures whichever expires LAST and
    // proves nothing about either. Suppress well after the re-commit cooldown
    // has already lapsed, then step past the delete hold alone.
    const t = new PacketTracker();
    seenAt(t, 'C1', 100, 0);
    const late = RECOMMIT_COOLDOWN_MS + 1000;
    t.suppress('parle_g', late);
    for (let i = 1; i <= ABSENT_FRAMES + 1; i++) t.observe([], late + i * 10);
    expect(seenAt(t, 'C1', 100, late + 500)).toHaveLength(0);
    // AND IT HAS TO LEAVE AGAIN. That probe at +500 put the packet back in
    // `seen`, and a packet continuously in view never re-commits whatever the
    // holds say — which is the older, stricter rule and is correct. The real
    // sequence is: removed, taken off the counter, handed over again.
    for (let i = 1; i <= ABSENT_FRAMES + 1; i++) t.observe([], late + 600 + i * 10);
    expect(seenAt(t, 'C1', 100, late + DELETE_SUPPRESS_MS + 1)).toHaveLength(1);
  });

  it('reports how long is left, so a screen can say so', () => {
    const t = new PacketTracker();
    t.suppress('parle_g', 1000);
    expect(t.suppressedFor('parle_g', 1000)).toBe(DELETE_SUPPRESS_MS);
    expect(t.suppressedFor('parle_g', 2000)).toBe(DELETE_SUPPRESS_MS - 1000);
    expect(t.suppressedFor('parle_g', 9000)).toBe(0);
    expect(t.suppressedFor('never_removed', 1000)).toBe(0);
  });

  it('suppresses by SKU, so an appearance line with no code is covered too', () => {
    const t = new PacketTracker();
    const look = (now: number) => t.observe(
      [{ sku_id: 'lifebuoy', name: 'Lifebuoy', price_paise: 3500,
         box: [200, 200, 80, 80], gate: 'appearance_only' }], now);
    expect(look(0)).toHaveLength(1);
    t.suppress('lifebuoy', 0);
    for (let i = 1; i <= ABSENT_FRAMES + 1; i++) t.observe([], i * 10);
    expect(look(500)).toHaveLength(0);
  });

  it('never suppresses a product that was not removed', () => {
    const t = new PacketTracker();
    t.suppress('parle_g', 0);
    const other = t.observe([{ code: 'C9', sku_id: 'soap', name: 'Soap',
                               price_paise: 3500, box: [10, 10, 40, 40] }], 100);
    expect(other).toHaveLength(1);
  });

  it('an empty sku id suppresses nothing', () => {
    const t = new PacketTracker();
    t.suppress('', 0);
    expect(seenAt(t, 'C1', 100, 10)).toHaveLength(1);
  });
});
