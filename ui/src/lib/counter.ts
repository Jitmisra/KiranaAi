/**
 * When a packet becomes a line on the bill.
 *
 * All of the till's hard-won behaviour lives here as pure functions and small
 * classes with no DOM, no React and no fetch, because every rule in this file
 * was learned from a bug that reached a real bill:
 *
 *   - A code is committed on its FIRST clean read. Codes are identifiers that
 *     were READ, not likenesses that were judged, so making them wait three
 *     frames only produced the "it detects for one second then stops" flicker.
 *   - A LOOK is committed after STABLE_N consecutive frames, because appearance
 *     is a guess and one lucky frame is not evidence.
 *   - Packets are keyed by POSITION, never by payload. Two identical packets
 *     share a barcode; keying by payload billed the pair once.
 *   - Absence is COUNTED, not forgotten. Deleting a packet on the first missed
 *     frame let one dropped read re-commit the same item.
 */

import type { Paise } from './money';

export const STABLE_N = 3;      // consecutive frames before an APPEARANCE commits
export const ABSENT_FRAMES = 4; // missed frames before a packet counts as gone
export const POLL_MS = 240;     // attempts per second is the lever when a code is marginal
const FORGET_AFTER = 80;        // ~35 s at POLL_MS — keeps the tracking map bounded

/**
 * How long the same code stays off the bill after it is billed.
 *
 * Position keying alone was not enough in a real hand. A packet the shopkeeper
 * is still holding drifts across the 64 px bucket boundary, or drops out for
 * more than ABSENT_FRAMES while a thumb covers the code, and comes back as a
 * NEW packet — so one biscuit packet became two, three, four lines.
 *
 * The rule is per PAYLOAD and across frames only: hold the same packet up again
 * within five seconds and nothing happens; hold up a DIFFERENT product and it
 * bills immediately, because the cooldown is keyed to the code, not the clock.
 *
 * Two identical packets put down TOGETHER still bill twice — they arrive in one
 * observe() call, and the cooldown is stamped after that whole frame is
 * decided. A supermarket lane must be able to sell two of the same thing.
 */
export const RECOMMIT_COOLDOWN_MS = 5000;

/**
 * How long a line the OPERATOR removed stays off the bill.
 *
 * Removing a line is a decision. The camera is still pointed at the packet, so
 * the moment it leaves view and comes back — a hand shifting, the ROI moving,
 * the customer picking it up again — the loop re-commits it and the line the
 * shopkeeper just deleted reappears. At the counter that reads as the delete
 * button not working, or worse, as a duplicate nobody notices.
 *
 * Three seconds is long enough to take the packet off the counter and short
 * enough that a customer genuinely handing over a second one is still billed.
 * Deliberately SHORTER than RECOMMIT_COOLDOWN_MS: that one throttles a packet
 * the camera keeps re-reading, this one honours an instruction.
 */
export const DELETE_SUPPRESS_MS = 3000;

export type Box = [number, number, number, number];

export interface Measured {
  frame_px?: [number, number] | null;
  region_px?: Box | null;
  [k: string]: unknown;
}

export interface ScanItem {
  code?: string | null;
  sku_id?: string | null;
  name?: string | null;
  price_paise?: Paise | null;
  box?: Box | null;
  reason?: string | null;
  gate?: string | null;
  top1_sku?: string | null;
  top1?: number | null;
  phi_used?: number | null;
  measured?: Measured | null;
}

export interface ScanFrame {
  ok?: boolean;
  mode?: string;
  items?: ScanItem[];
  /** Basket mode reports this... */
  codes_found?: number;
  distinct_codes?: number | null;
  /** ...and the plain_photo (appearance) response reports the SAME fact here. */
  codes_seen?: number;
  elapsed_ms?: number | null;
  catalog_size?: number | null;
  reason?: string | null;
  crop_png?: string | null;
}

export interface BasketLine {
  sku_id: string;
  name: string;
  price_paise: Paise;
  qty: number;
  by?: string | undefined;
}

/**
 * A packet's identity for one frame: its code AND where it is standing.
 *
 * The 64 px quantisation is the tolerance for a hand that is not quite still.
 * Keying on the payload alone would merge two identical packets into one line;
 * keying on exact pixels would split one packet into a new line every frame.
 */
export function packetKey(it: ScanItem): string {
  const b = it.box || [0, 0, 0, 0];
  const cx = Math.round((b[0] + b[2] / 2) / 64);
  const cy = Math.round((b[1] + b[3] / 2) / 64);
  return `${it.code}@${cx},${cy}`;
}

/**
 * Region boxes come back in the SERVER's working image, which is the upload
 * unless the server downscaled it. `measured.frame_px` names that space —
 * drawing the coordinates raw put boxes 200 px away from the packets they named.
 */
export function boxScale(
  frame: ScanFrame,
  roi: { w: number; h: number } | null,
): { sx: number; sy: number } {
  const first = (frame.items || [])[0];
  const fp = first?.measured?.frame_px || [roi?.w ?? 1, roi?.h ?? 1];
  return {
    sx: (roi?.w ?? fp[0]) / Math.max(1, fp[0]),
    sy: (roi?.h ?? fp[1]) / Math.max(1, fp[1]),
  };
}

/** Tracks which packets are already on the bill, so a held basket bills once. */
export class PacketTracker {
  private seen = new Map<string, { missing: number }>();
  /** payload -> when it was last committed. Drives the re-commit cooldown. */
  private lastCommit = new Map<string, number>();
  /**
   * sku -> when the operator took it OFF the bill.
   *
   * Keyed on the SKU, not the code payload, because a line can be committed by
   * APPEARANCE and carry no code at all — keying on the payload would leave
   * exactly those lines unsuppressed, and an appearance duplicate is the
   * hardest kind to notice on a bill.
   */
  private removedAt = new Map<string, number>();

  constructor(private cooldownMs: number = RECOMMIT_COOLDOWN_MS) {}

  /**
   * Returns the items that are NEW this frame, in the order they were read.
   *
   * `now` is injected so the cooldown is testable without waiting five seconds
   * of real time — a rule you can only verify by sleeping is a rule that stops
   * being verified.
   */
  observe(items: ScanItem[], now: number = Date.now()): ScanItem[] {
    const live = new Set<string>();
    const fresh: ScanItem[] = [];
    for (const it of items) {
      const key = packetKey(it);
      live.add(key);
      const st = this.seen.get(key);
      // Never seen before, or seen but genuinely gone and now brought back.
      const isNew = !st || st.missing >= ABSENT_FRAMES;
      this.seen.set(key, { missing: 0 });
      if (!isNew) continue;

      // ...and still on cooldown from the last time this CODE was billed?
      const payload = String(it.code ?? '');
      const last = this.lastCommit.get(payload);
      if (last !== undefined && now - last < this.cooldownMs) continue;

      // ...or did the operator just take this product OFF the bill? For a few
      // seconds their instruction outranks the camera's opinion.
      const removed = this.removedAt.get(String(it.sku_id ?? ''));
      if (removed !== undefined && now - removed < DELETE_SUPPRESS_MS) continue;

      fresh.push(it);
    }
    // Stamped AFTER the whole frame, so two identical packets shown together
    // both survive — the cooldown is about coming back, not about being seen.
    for (const it of fresh) this.lastCommit.set(String(it.code ?? ''), now);

    for (const [k, st] of Array.from(this.seen.entries())) {
      if (live.has(k)) continue;
      st.missing++;
      if (st.missing > FORGET_AFTER) this.seen.delete(k);
    }
    // Do not let the cooldown map grow forever on a long shift.
    for (const [payload, at] of Array.from(this.lastCommit.entries())) {
      if (now - at > this.cooldownMs * 20) this.lastCommit.delete(payload);
    }
    for (const [sku, at] of Array.from(this.removedAt.entries())) {
      if (now - at > DELETE_SUPPRESS_MS * 10) this.removedAt.delete(sku);
    }
    return fresh;
  }

  /**
   * The operator removed this product. Hold it off the bill for
   * DELETE_SUPPRESS_MS so the camera cannot immediately put it back.
   */
  suppress(skuId: string, now: number = Date.now()): void {
    if (!skuId) return;
    this.removedAt.set(skuId, now);
    // Forget its PRESENCE too. A packet still sitting in view stays in `seen`
    // with missing:0, so once the hold expired it would never look new again
    // and the product would be silently unsellable for the rest of the bill —
    // suppression that never ends is worse than none.
    for (const k of Array.from(this.seen.keys())) {
      if (k.includes(skuId)) this.seen.delete(k);
    }
  }

  /** Milliseconds until a removed product may be billed again; 0 when free. */
  suppressedFor(skuId: string, now: number = Date.now()): number {
    const at = this.removedAt.get(skuId);
    if (at === undefined) return 0;
    return Math.max(0, DELETE_SUPPRESS_MS - (now - at));
  }

  /** Milliseconds until this code may be billed again; 0 when it is free. */
  cooldownLeft(payload: string, now: number = Date.now()): number {
    const last = this.lastCommit.get(payload);
    if (last === undefined) return 0;
    return Math.max(0, this.cooldownMs - (now - last));
  }

  reset(): void {
    this.seen.clear();
    this.lastCommit.clear();
  }

  get tracked(): number {
    return this.seen.size;
  }
}

/** Appearance mode: the same SKU must hold for STABLE_N frames before it bills. */
export class StreakTracker {
  private sku: string | null = null;
  private n = 0;

  /** True exactly once, on the frame the streak completes. */
  observe(skuId: string | null): boolean {
    if (skuId === null) {
      this.sku = '__abstained';
      this.n = 0;
      return false;
    }
    if (this.sku === skuId) this.n += 1;
    else {
      this.sku = skuId;
      this.n = 1;
    }
    return this.n === STABLE_N;
  }

  /** Has the last frame already been reported as an abstention? */
  get abstained(): boolean {
    return this.sku === '__abstained';
  }

  reset(): void {
    this.sku = null;
    this.n = 0;
  }
}

/** Fold committed items into the running bill. Returns a NEW map for React. */
export function addToBasket(
  basket: ReadonlyMap<string, BasketLine>,
  items: ScanItem[],
): Map<string, BasketLine> {
  const next = new Map(basket);
  for (const it of items) {
    if (!it.sku_id) continue;
    const cur = next.get(it.sku_id);
    if (cur) next.set(it.sku_id, { ...cur, qty: cur.qty + 1 });
    else
      next.set(it.sku_id, {
        sku_id: it.sku_id,
        name: it.name || it.sku_id,
        price_paise: (it.price_paise ?? 0) as Paise,
        qty: 1,
        by: it.gate ?? undefined,
      });
  }
  return next;
}

/**
 * THE OPERATOR OVERRULING THE CAMERA.
 *
 * The bill was read-only. If the counter added the wrong thing, or a customer
 * changed their mind at the last second, the only options were CLEAR — which
 * wipes the whole bill — or charge for something nobody wanted. On a real
 * counter that is not a rare case; it is most of a shift.
 *
 * A quantity is a COUNT, never money: it is an integer, and the line total is
 * still `price_paise * qty` in integer paise. Nothing here divides, and nothing
 * here can invent a price.
 *
 * Setting a quantity to zero or below REMOVES the line rather than storing a
 * zero. A zero-quantity line is a row on the bill that charges nothing, which
 * reads to a shopkeeper as an item they forgot to price.
 *
 * The packet tracker is deliberately NOT told about this. A packet still in
 * view stays in `seen` with `missing: 0`, so it will not re-commit while it sits
 * there — and once it genuinely leaves and comes back it SHOULD bill again,
 * because that is a customer handing over a second one.
 */
export function setQty(
  basket: ReadonlyMap<string, BasketLine>,
  skuId: string,
  qty: number,
): Map<string, BasketLine> {
  const next = new Map(basket);
  const cur = next.get(skuId);
  if (!cur) return next;
  const n = Math.floor(qty);
  if (!Number.isFinite(n) || n <= 0) next.delete(skuId);
  else next.set(skuId, { ...cur, qty: n });
  return next;
}

/** One fewer of this line; the last one removes it. */
export function decLine(
  basket: ReadonlyMap<string, BasketLine>,
  skuId: string,
): Map<string, BasketLine> {
  const cur = basket.get(skuId);
  return setQty(basket, skuId, (cur?.qty ?? 0) - 1);
}

/** One more of this line. Only ever of a line that is already on the bill. */
export function incLine(
  basket: ReadonlyMap<string, BasketLine>,
  skuId: string,
): Map<string, BasketLine> {
  const cur = basket.get(skuId);
  return cur ? setQty(basket, skuId, cur.qty + 1) : new Map(basket);
}

/** Take this line off the bill entirely. */
export function removeLine(
  basket: ReadonlyMap<string, BasketLine>,
  skuId: string,
): Map<string, BasketLine> {
  return setQty(basket, skuId, 0);
}

/**
 * What the counter says it saw. Symbols and distinct codes are different
 * numbers and both are said: "3 SYMBOLS · 2 DISTINCT" is a counter holding two
 * of something, which is a thing the shopkeeper needs to be able to check.
 */
export function headline(frame: ScanFrame): {
  symbols: number;
  distinct: number;
  untaught: number;
  named: ScanItem[];
} {
  const items = frame.items || [];
  // `codes_found` is the BASKET key; the appearance response carries `codes_seen`
  // and never `codes_found`. Reading only the first meant look mode always
  // computed 0 symbols, so the till printed "nothing readable in view" three
  // characters from where it was naming the product and putting it on the bill.
  // `??` not `||`, so a real 0 is not mistaken for a missing field.
  const symbols = frame.codes_found ?? frame.codes_seen ?? 0;
  return {
    symbols,
    distinct: frame.distinct_codes ?? symbols,
    untaught: items.filter((i) => !i.sku_id).length,
    named: items.filter((i) => !!i.sku_id),
  };
}
