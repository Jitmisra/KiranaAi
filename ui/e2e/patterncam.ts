/**
 * A synthetic camera feed whose every pixel is known in advance.
 *
 * `tools/make_fake_cam.py` puts a QR in the corner to prove the till looks at
 * the whole frame. This one exists to prove something the QR cannot: that the
 * rectangle an operator DRAWS over the picture is the rectangle that gets cut
 * out of it. For that the feed has to be legible as geometry — you must be able
 * to say "the crop should contain this band and not that one" and check it.
 *
 * So the scene is a tall white carton held up against a plain room, with a
 * BRAND BAND across its top and a FOOT BAND across its bottom in two colours
 * that appear nowhere else, and a large blob off to the right standing in for
 * the operator's face. The bands sit in the top and bottom 10% of the drawn
 * box on purpose: they are exactly what a crop that is too tight vertically
 * throws away first, and their absence from the captured still is the
 * signature of that bug.
 *
 * Every frame is IDENTICAL. The bug under test is geometric, not temporal, and
 * identical frames make the captured crop comparable pixel-for-pixel with the
 * frame it was cut from — no sub-pixel drift to explain away. (The burst gate
 * still runs on them: it rejects blur and glare per frame, and a still, sharp,
 * un-blown burst passes it. The gate's own "no phase diversity" warning is
 * expected here and is not a failure.)
 *
 * No new dependencies: Y4M is a header, the word FRAME, and three raw planes.
 */

import { writeFileSync } from 'node:fs';

export const FRAME_W = 1280;
export const FRAME_H = 720;

/** Every landmark, in FRAME pixels. The test asserts against these numbers. */
export const SCENE = {
  /** The carton, held up close: nearly the full height of the frame. */
  carton: { x: 420, y: 40, w: 440, h: 640 },
  /** Its brand lockup — the first thing a too-tight crop loses. */
  brand: { x: 420, y: 40, w: 440, h: 60 },
  /** Its bottom edge — the second thing. */
  foot: { x: 420, y: 620, w: 440, h: 60 },
  /** The operator's face, well outside the box they will draw. */
  face: { x: 980, y: 120, w: 280, h: 480 },
  /**
   * The box the operator drags: comfortably around the carton, margin top and
   * bottom, the face well outside it to the right.
   */
  drawn: { x: 400, y: 20, w: 480, h: 680 },
  /**
   * What the crop came out as before the fix, kept as a named number rather
   * than a memory: the drag layer covered the 4:3 stage while the still was a
   * 16:9 picture letterboxed inside it, so the selection was squeezed by
   * (4/3)/(16/9) = 0.75 and pushed down by half the bar. It contains neither
   * band.
   */
  squeezed: { x: 400, y: 105, w: 480, h: 510 },
} as const;

/** Sampling points for the test's reference colours, in FRAME pixels. */
export const PROBE = {
  brand: { x: 640, y: 70 },
  body: { x: 640, y: 350 },
  foot: { x: 640, y: 650 },
  face: { x: 1120, y: 360 },
  room: { x: 120, y: 360 },
} as const;

type RGB = [number, number, number];

const ROOM: RGB = [58, 66, 88];
const BODY: RGB = [205, 203, 198];
const BRAND: RGB = [196, 40, 44];
const FOOT: RGB = [36, 72, 190];
const FACE: RGB = [70, 178, 96];

/**
 * A checkerboard over everything.
 *
 * Not decoration: the enrolment burst gate refuses a crop whose Laplacian
 * variance is too low, and a scene of flat colour rectangles has none. 4 px
 * cells (an 8 px period, one JPEG block) survive the two JPEG generations
 * between the sensor and the gate; 18 levels is plenty of edge and keeps the
 * white carton at 223 at its brightest, well under the 250 the glare gate
 * counts as blown.
 */
function textured(base: RGB, x: number, y: number): RGB {
  const on = (((x >> 2) + (y >> 2)) & 1) === 0;
  const d = on ? 18 : -18;
  return [
    Math.max(0, Math.min(255, base[0] + d)),
    Math.max(0, Math.min(255, base[1] + d)),
    Math.max(0, Math.min(255, base[2] + d)),
  ];
}

function inside(b: { x: number; y: number; w: number; h: number }, x: number, y: number): boolean {
  return x >= b.x && x < b.x + b.w && y >= b.y && y < b.y + b.h;
}

function pixel(x: number, y: number): RGB {
  let base = ROOM;
  if (inside(SCENE.face, x, y)) base = FACE;
  if (inside(SCENE.carton, x, y)) base = BODY;
  if (inside(SCENE.brand, x, y)) base = BRAND;
  if (inside(SCENE.foot, x, y)) base = FOOT;
  return textured(base, x, y);
}

/**
 * BT.601, STUDIO RANGE. A Y4M with no XCOLORRANGE is read as video range, so
 * writing full-range values here would come back out of the decoder clipped and
 * over-saturated. It would not break the geometry this feed exists to test, but
 * a feed that lies about its own colours is a bad instrument to reach for next
 * time.
 */
function toY(r: number, g: number, b: number): number {
  return Math.round(16 + (65.481 * r + 128.553 * g + 24.966 * b) / 255);
}
function toU(r: number, g: number, b: number): number {
  return Math.round(128 + (-37.797 * r - 74.203 * g + 112.0 * b) / 255);
}
function toV(r: number, g: number, b: number): number {
  return Math.round(128 + (112.0 * r - 93.786 * g - 18.214 * b) / 255);
}

const clamp8 = (v: number) => (v < 0 ? 0 : v > 255 ? 255 : v);

/** Write a looping Y4M of the scene above. Returns the path it wrote. */
export function writePatternFeed(path: string, frames = 60): string {
  const halfW = FRAME_W >> 1;
  const halfH = FRAME_H >> 1;
  const y = Buffer.alloc(FRAME_W * FRAME_H);
  const u = Buffer.alloc(halfW * halfH);
  const v = Buffer.alloc(halfW * halfH);

  for (let py = 0; py < FRAME_H; py += 1) {
    for (let px = 0; px < FRAME_W; px += 1) {
      const [r, g, b] = pixel(px, py);
      y[py * FRAME_W + px] = clamp8(toY(r, g, b));
    }
  }
  // Chroma is subsampled 2x2; average the four source pixels rather than point
  // sampling, so a band edge lands where the luma says it does.
  for (let cy = 0; cy < halfH; cy += 1) {
    for (let cx = 0; cx < halfW; cx += 1) {
      let sr = 0, sg = 0, sb = 0;
      for (let dy = 0; dy < 2; dy += 1) {
        for (let dx = 0; dx < 2; dx += 1) {
          const [r, g, b] = pixel(cx * 2 + dx, cy * 2 + dy);
          sr += r; sg += g; sb += b;
        }
      }
      const r = sr / 4, g = sg / 4, b = sb / 4;
      u[cy * halfW + cx] = clamp8(toU(r, g, b));
      v[cy * halfW + cx] = clamp8(toV(r, g, b));
    }
  }

  const head = Buffer.from(`YUV4MPEG2 W${FRAME_W} H${FRAME_H} F25:1 Ip A1:1 C420\n`, 'ascii');
  const tag = Buffer.from('FRAME\n', 'ascii');
  const parts: Buffer[] = [head];
  for (let i = 0; i < frames; i += 1) parts.push(tag, y, u, v);
  writeFileSync(path, Buffer.concat(parts));
  return path;
}
