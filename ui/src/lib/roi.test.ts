import { describe, it, expect } from 'vitest';
import { defaultRoi, roiHolds, isWholeFrame, clampDrag, containedBoxPct } from './roi';

const FRAME = { w: 1280, h: 720 };

/**
 * The regression these tests exist for: the page cropped to its centre
 * 56% x 66% and uploaded only that, so a barcode near an edge was destroyed in
 * the browser and the server — blameless — reported nothing readable.
 * Measured 3 of 25 held positions before, 25 of 25 after.
 */
describe('the counter area', () => {
  it('uploads the WHOLE frame when reading codes', () => {
    expect(defaultRoi('code', FRAME)).toEqual({ x: 0, y: 0, w: 1280, h: 720 });
  });

  it('holds a code at every place a hand actually holds a packet', () => {
    const r = defaultRoi('code', FRAME);
    const corners: Array<[number, number]> = [
      [0.06, 0.08], [0.94, 0.08], [0.06, 0.92], [0.94, 0.92], [0.5, 0.05], [0.05, 0.5],
    ];
    for (const [fx, fy] of corners) {
      expect(roiHolds(r, FRAME.w * fx, FRAME.h * fy),
        `a code at ${fx * 100}% x ${fy * 100}% would never reach the server`).toBe(true);
    }
  });

  it('still uploads only a bounded rectangle when measuring a silhouette', () => {
    const r = defaultRoi('look', FRAME);
    expect(r.w).toBeLessThan(FRAME.w);
    expect(r.h).toBeLessThan(FRAME.h);
    expect(roiHolds(r, FRAME.w * 0.04, FRAME.h * 0.04)).toBe(false);
  });

  it('can tell the operator, truthfully, when everything is being uploaded', () => {
    expect(isWholeFrame(defaultRoi('code', FRAME), FRAME)).toBe(true);
    expect(isWholeFrame(defaultRoi('look', FRAME), FRAME)).toBe(false);
  });
});

describe('a rectangle the shopkeeper drags', () => {
  it('becomes the counter area', () => {
    const r = clampDrag({ x0: 200, y0: 100, x1: 700, y1: 500 }, FRAME);
    expect(r).toEqual({ ok: true, roi: { x: 200, y: 100, w: 500, h: 400 } });
  });

  it('works when dragged right-to-left or bottom-to-top', () => {
    const r = clampDrag({ x0: 700, y0: 500, x1: 200, y1: 100 }, FRAME);
    expect(r.ok && r.roi).toEqual({ x: 200, y: 100, w: 500, h: 400 });
  });

  it('is clamped to the camera image rather than running off it', () => {
    const r = clampDrag({ x0: 1000, y0: 600, x1: 5000, y1: 5000 }, FRAME);
    expect(r.ok && r.roi).toEqual({ x: 1000, y: 600, w: 280, h: 120 });
  });

  it('is refused in words when it lands outside — never silently ignored', () => {
    const r = clampDrag({ x0: 10, y0: 10, x1: 30, y1: 30 }, FRAME);
    expect(r.ok).toBe(false);
    expect(r.ok === false && r.reason).toMatch(/unchanged/);
  });
});

/**
 * The regression these exist for: the teach page's drag layer covered the whole
 * 4:3 stage while the frozen 16:9 still was letterboxed inside it, so a
 * fraction of the layer was not a fraction of the frame. Measured on the
 * running page: a box drawn over the picture from y=20 to y=700 was cut out of
 * the frame as y=105..615 — a quarter of the operator's selection, gone, and
 * the crop 1.333x too tight, every single time.
 */
describe('where a letterboxed frame actually sits in its box', () => {
  it('gives a 16:9 camera in a 4:3 stage full width and 12.5% bars', () => {
    const f = containedBoxPct({ w: 1280, h: 720 }, { w: 4, h: 3 });
    expect(f.left).toBe(0);
    expect(f.width).toBe(100);
    expect(f.height).toBeCloseTo(75, 6);
    expect(f.top).toBeCloseTo(12.5, 6);
  });

  it('gives a portrait camera full height and bars at the sides', () => {
    const f = containedBoxPct({ w: 720, h: 1280 }, { w: 4, h: 3 });
    expect(f.top).toBe(0);
    expect(f.height).toBe(100);
    expect(f.width).toBeCloseTo((720 / 1280) / (4 / 3) * 100, 6);
    expect(f.left).toBeCloseTo((100 - f.width) / 2, 6);
  });

  it('fills the box exactly when the shapes already agree', () => {
    for (const box of [{ w: 4, h: 3 }, { w: 16, h: 9 }, { w: 1, h: 1 }]) {
      const f = containedBoxPct({ w: box.w * 100, h: box.h * 100 }, box);
      expect(f).toEqual({ left: 0, top: 0, width: 100, height: 100 });
    }
  });

  it('maps the reported box back to the pixels that were being lost', () => {
    // The operator drew from 20 to 700 of a 720 px frame. Under the old
    // mapping that arrived as 105..615.
    const f = containedBoxPct({ w: 1280, h: 720 }, { w: 4, h: 3 });
    const stageFrac = (frameY: number) => (f.top + (frameY / 720) * f.height) / 100;
    expect(stageFrac(20) * 720).toBeCloseTo(105, 6);
    expect(stageFrac(700) * 720).toBeCloseTo(615, 6);
  });

  it('never divides by zero on a camera that has not reported a size yet', () => {
    expect(() => containedBoxPct({ w: 0, h: 0 }, { w: 0, h: 0 })).not.toThrow();
    const f = containedBoxPct({ w: 0, h: 0 }, { w: 4, h: 3 });
    expect(Number.isFinite(f.width) && Number.isFinite(f.height)).toBe(true);
  });
});
