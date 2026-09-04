/**
 * The counter area — which pixels leave this browser.
 *
 * This module exists because getting it wrong is invisible. A crop that
 * discards the barcode produces a server log that says, correctly and
 * uselessly, "no code readable". The till looked broken for days while every
 * measurement of the server came back healthy.
 *
 * Measured on a 5x5 grid of held positions across a 1280x720 view:
 *
 *     centre crop, 56% x 66%   ->   3 of 25 read
 *     the whole frame          ->  25 of 25 read
 *
 * Twenty-two of those losses happened in the browser, before the request.
 */

export type Roi = { x: number; y: number; w: number; h: number };
export type ReadMode = 'code' | 'look';

/** A box as percentages of its container, ready to be written into a style. */
export type FitPct = { left: number; top: number; width: number; height: number };

/**
 * WHERE A LETTERBOXED FRAME'S PIXELS ACTUALLY ARE INSIDE ITS BOX.
 *
 * A camera frame drawn with `object-fit: contain` into a box of a different
 * shape does not fill it — bars appear on the axis that ran out of room first.
 * So a rectangle drawn over the whole BOX is not the same rectangle as one
 * drawn over the IMAGE, and applying the first as if it were the second is a
 * silent, perfectly deterministic mis-crop. Nothing looks broken; the operator
 * simply gets back less than they selected, every single time.
 *
 * That shipped, and this is the measurement that found it. Teach stage 4:3,
 * camera 1280x720, viewport 1500x1000 -> stage 582.75 x 437.06 CSS px, still
 * rendered 582.75 x 327.80 with 54.63 px bars top and bottom, drag layer
 * pinned to the whole stage. A box dragged from 20% to 85% of the stage was
 * applied to the frame as y 144..612 px when the operator had selected
 * y 72..648 px: 156 px of a 624 px selection thrown away, top and bottom, and
 * the crop 1.333x too tight — exactly (4/3) / (16/9). Horizontally it was
 * pixel-exact, because that axis had no bars. That asymmetry is the signature
 * of this bug and nothing else.
 *
 * The fix is to pin the drag layer to the IMAGE. This returns where the image
 * is, in PERCENTAGES of the container, so it can go straight into a style and
 * nothing has to be measured at render time: the answer is the same at every
 * window size, every device pixel ratio and every camera aspect ratio.
 *
 * `box` is an aspect ratio, not a measurement — pass the same numbers the CSS
 * box is declared with, so the two cannot drift apart.
 */
export function containedBoxPct(
  frame: { w: number; h: number },
  box: { w: number; h: number },
): FitPct {
  const fw = frame.w > 0 ? frame.w : 1;
  const fh = frame.h > 0 ? frame.h : 1;
  const bw = box.w > 0 ? box.w : 1;
  const bh = box.h > 0 ? box.h : 1;
  const frameAr = fw / fh;
  const boxAr = bw / bh;
  if (frameAr > boxAr) {
    // Wider than its box: full width, bars above and below.
    const height = (boxAr / frameAr) * 100;
    return { left: 0, top: (100 - height) / 2, width: 100, height };
  }
  // Taller than its box (or the same shape): full height, bars left and right.
  const width = (frameAr / boxAr) * 100;
  return { left: (100 - width) / 2, top: 0, width, height: 100 };
}

/** The bounded rectangle used when MEASURING a silhouette. */
const LOOK_INSET = { x: 0.22, y: 0.2, w: 0.56, h: 0.66 } as const;

/**
 * Reading a code and measuring a silhouette want opposite defaults, so they no
 * longer share one.
 *
 * CODE — the whole frame. A barcode is wherever the packet's printer put it:
 * an edge, a corner, a side seam. The customer's hand is wherever it is. A
 * centre crop cannot know either, so it throws codes away.
 *
 * LOOK — a bounded rectangle. Here the crop is both the privacy boundary of
 * invariant 4 and a real accuracy aid: the region picker wants one product on
 * a counter, not the shelf behind it.
 */
export function defaultRoi(mode: ReadMode, frame: { w: number; h: number }): Roi {
  const W = frame.w || 1280;
  const H = frame.h || 720;
  if (mode === 'code') return { x: 0, y: 0, w: W, h: H };
  return {
    x: Math.round(W * LOOK_INSET.x),
    y: Math.round(H * LOOK_INSET.y),
    w: Math.round(W * LOOK_INSET.w),
    h: Math.round(H * LOOK_INSET.h),
  };
}

/** Does the uploaded area contain this point of the camera image? */
export function roiHolds(r: Roi | null, x: number, y: number): boolean {
  if (!r) return false;
  return x >= r.x && y >= r.y && x <= r.x + r.w && y <= r.y + r.h;
}

/** True when the area is the entire camera image — the page says so out loud. */
export function isWholeFrame(r: Roi | null, frame: { w: number; h: number }): boolean {
  return !!r && r.x === 0 && r.y === 0 && r.w === frame.w && r.h === frame.h;
}

export type ClampResult =
  | { ok: true; roi: Roi }
  | { ok: false; reason: string };

/**
 * Turn a drag into a counter area, or refuse it.
 *
 * The size check runs on the CLAMPED rectangle. Silently keeping the old area
 * after a drag the shopkeeper watched themselves draw would be its own lie, so
 * a rectangle that fell outside the image is refused in words.
 */
export function clampDrag(
  drag: { x0: number; y0: number; x1: number; y1: number },
  frame: { w: number; h: number },
  minSide = 60,
): ClampResult {
  const x = Math.min(drag.x0, drag.x1);
  const y = Math.min(drag.y0, drag.y1);
  const w = Math.abs(drag.x1 - drag.x0);
  const h = Math.abs(drag.y1 - drag.y0);
  const cx = Math.max(0, Math.min(frame.w, x));
  const cy = Math.max(0, Math.min(frame.h, y));
  const cw = Math.max(0, Math.min(w, frame.w - cx));
  const ch = Math.max(0, Math.min(h, frame.h - cy));
  if (cw > minSide && ch > minSide) return { ok: true, roi: { x: cx, y: cy, w: cw, h: ch } };
  return { ok: false, reason: 'That rectangle fell outside the camera image. The counter area is unchanged.' };
}
