/**
 * What the operator sees the machine seeing.
 *
 * Two layers on one canvas:
 *   BOXES — a live rectangle around every code in the current frame, labelled
 *           with what it was priced as, or with why it could not be.
 *   SNAPS — a 620 ms confirmation that a line was COMMITTED. Brackets travel
 *           1.35x -> 1.00x over 120 ms and close into a solid rectangle with
 *           ONE latched frame of white fill.
 *
 * The abstain uses the IDENTICAL curve and then stops 12 px short on all four
 * sides and shakes — a variation on the success rather than a separate
 * vocabulary, which is exactly why it cannot be mistaken for one.
 *
 * The snap runs its OWN requestAnimationFrame loop. Driving it from the 240 ms
 * scan poll rendered each snap in exactly two frames — brackets at rest, then
 * gone — which is not an animation.
 */

import type { Box } from './counter';

export type Tone = 'named' | 'abstain';

export interface LiveBox {
  box: Box;
  sx: number;
  sy: number;
  named: boolean;
  label: string;
}

interface Snap {
  box: Box;
  tone: Tone;
  sx: number;
  sy: number;
  flashed: boolean;
  t0: number;
}

const SNAP_MS = 620;
const CLOSE_MS = 120;
const ABSTAIN_SHORT = 12;

const GREEN = '#0E8A4F';
const GREEN_HI = '#33D68A';
const AMBER = '#F0B429';

export class Overlay {
  private snaps: Snap[] = [];
  private raf = 0;
  private boxes: LiveBox[] = [];
  private origin = { x: 0, y: 0 };
  private canvas: HTMLCanvasElement | null = null;

  attach(canvas: HTMLCanvasElement | null): void {
    this.canvas = canvas;
    this.paint();
  }

  setBoxes(boxes: LiveBox[], origin: { x: number; y: number }): void {
    this.boxes = boxes;
    this.origin = origin;
    this.paint();
  }

  snap(box: Box | null | undefined, tone: Tone, sx = 1, sy = 1): void {
    if (!box) return;
    this.snaps.push({ box: [...box] as Box, tone, sx, sy, flashed: false, t0: performance.now() });
    if (!this.raf) this.raf = requestAnimationFrame(this.loop);
  }

  clear(): void {
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    this.snaps.length = 0;
    this.boxes = [];
    this.paint();
  }

  private loop = (): void => {
    this.paint();
    this.raf = this.snaps.length ? requestAnimationFrame(this.loop) : 0;
  };

  paint(): void {
    const cv = this.canvas;
    if (!cv) return;
    const c = cv.getContext('2d');
    if (!c) return;
    c.clearRect(0, 0, cv.width, cv.height);
    this.drawBoxes(c);
    this.drawSnaps(c, this.origin.x, this.origin.y);
  }

  private drawBoxes(c: CanvasRenderingContext2D): void {
    const lw = Math.max(2, c.canvas.width / 420);
    for (const b of this.boxes) {
      const x = this.origin.x + b.box[0] * b.sx;
      const y = this.origin.y + b.box[1] * b.sy;
      const w = b.box[2] * b.sx;
      const h = b.box[3] * b.sy;
      c.save();
      c.strokeStyle = b.named ? GREEN : AMBER;
      c.lineWidth = lw;
      c.setLineDash(b.named ? [] : [10, 7]);
      c.strokeRect(x, y, w, h);
      c.setLineDash([]);
      if (b.label) this.label(c, b.label, x, y, b.named);
      c.restore();
    }
  }

  private label(c: CanvasRenderingContext2D, text: string, x: number, y: number, named: boolean): void {
    const size = Math.max(13, Math.round(c.canvas.width / 62));
    c.font = `600 ${size}px Inter, system-ui, sans-serif`;
    const pad = size * 0.45;
    const w = c.measureText(text).width + pad * 2;
    const h = size + pad * 1.6;
    // Keep the plate on-canvas: a label that runs off the top of a frame is a
    // label the operator cannot read at the moment they most need it.
    const ly = y - h - 6 < 0 ? y + 6 : y - h - 6;
    c.fillStyle = named ? GREEN : AMBER;
    c.beginPath();
    c.roundRect(x, ly, w, h, 6);
    c.fill();
    c.fillStyle = '#FFFFFF';
    c.textBaseline = 'middle';
    c.fillText(text, x + pad, ly + h / 2);
  }

  private drawSnaps(c: CanvasRenderingContext2D, ox: number, oy: number): void {
    const now = performance.now();
    for (let i = this.snaps.length - 1; i >= 0; i--) {
      const s = this.snaps[i]!;
      const age = now - s.t0;
      if (age > SNAP_MS) {
        this.snaps.splice(i, 1);
        continue;
      }
      const b = s.box;
      const x = ox + b[0] * s.sx;
      const y = oy + b[1] * s.sy;
      const w = b[2] * s.sx;
      const h = b[3] * s.sy;
      const named = s.tone === 'named';

      // 1.35 -> 1.00, expo-out
      const k = age < CLOSE_MS ? 1.35 - 0.35 * (1 - Math.pow(2, (-10 * age) / CLOSE_MS)) : 1;
      const short = named ? 0 : ABSTAIN_SHORT; // the abstain never closes
      const shake = named ? 0 : Math.sin((age / 1000) * 3 * 2 * Math.PI) * 3;
      const cx = x + w / 2;
      const cy = y + h / 2;
      const hw = (w / 2) * k - short;
      const hh = (h / 2) * k - short;

      c.save();
      c.translate(shake, 0);
      c.strokeStyle = named ? GREEN_HI : AMBER;
      c.lineWidth = Math.max(3, c.canvas.width / 300);
      const L = Math.min(hw, hh) * 0.42;
      for (const [gx, gy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]] as const) {
        const px = cx + gx * hw;
        const py = cy + gy * hh;
        c.beginPath();
        c.moveTo(px - gx * L, py);
        c.lineTo(px, py);
        c.lineTo(px, py - gy * L);
        c.stroke();
      }
      if (named && age >= CLOSE_MS && !s.flashed) {
        s.flashed = true; // ONE frame of white, latched
        c.fillStyle = 'rgba(255,255,255,0.35)';
        c.fillRect(cx - hw, cy - hh, hw * 2, hh * 2);
      }
      if (named && age >= CLOSE_MS) {
        const over = age < 210 ? 1 + 0.04 * (1 - (age - CLOSE_MS) / 90) : 1;
        c.strokeRect(cx - hw * over, cy - hh * over, hw * 2 * over, hh * 2 * over);
      }
      c.restore();
    }
  }
}
