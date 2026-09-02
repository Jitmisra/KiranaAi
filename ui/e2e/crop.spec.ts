import { test, expect, chromium, type Browser, type Page } from '@playwright/test';
import { writePatternFeed, SCENE, PROBE, FRAME_W, FRAME_H } from './patterncam';

/**
 * THE CROP THE OPERATOR DRAWS IS THE CROP THAT GETS TAUGHT.
 *
 * Reported as: "the captured still is cropped much tighter vertically than the
 * box I drew — the top of the carton and its bottom edge are cut off, and it is
 * zoomed in. Horizontally it looks about right."
 *
 * That asymmetry was the whole diagnosis. The teach stage is 4:3 and the camera
 * is 16:9, the frozen still is `object-fit: contain` inside it, so it was
 * letterboxed with 12.5% of black bar above and below — and the drag layer was
 * `inset: 0`, covering the bars as well. A fraction of the layer therefore was
 * not a fraction of the frame, while `cropBlob` multiplied it by the frame's
 * own 1280x720. Measured on the running page at a 1500x1000 viewport: a box
 * dragged over the picture from y=20 to y=700 was applied as y=105..615.
 * 156 px of a 680 px selection discarded, and the crop 1.333x too tight, which
 * is exactly (4/3) / (16/9). The horizontal axis had no bars and so was
 * pixel-exact, every time.
 *
 * This test drives the real page against a synthetic camera whose every pixel
 * is known (see patterncam.ts), draws a box over a known region, and asserts
 * that the captured still IS that region: right size, same pixels as the frame
 * it was cut from, and carrying the two colour bands that a vertically-squeezed
 * crop loses first.
 *
 * IT TEACHES NOTHING. The run stops at USE THIS BOX, so no catalogue is written
 * and there is nothing to clean up.
 *
 * The drag is aimed at where the PICTURE is on screen, worked out
 * independently from the still's own natural size — the way an operator aims at
 * the carton they can see — and never at the drag layer's own box. Aiming at
 * the layer would have made this test pass against the bug it exists to catch:
 * the old layer was the stage, so a fraction of it was exactly the wrong
 * fraction the old code then applied.
 */

const BASE = process.env.GAWAAH_BASE || 'http://127.0.0.1:8790';
const FEED = process.env.GAWAAH_CROP_FEED || '/tmp/gawaah_pattern_cam.y4m';

let browser: Browser;
let page: Page;

test.beforeAll(async () => {
  writePatternFeed(FEED);
  browser = await chromium.launch({
    args: [
      '--use-fake-ui-for-media-stream',
      '--use-fake-device-for-media-stream',
      `--use-file-for-fake-video-capture=${FEED}`,
      '--autoplay-policy=no-user-gesture-required',
    ],
  });
});
test.afterAll(async () => { await browser?.close(); });

test.beforeEach(async () => {
  page = await (await browser.newContext({
    permissions: ['camera'], viewport: { width: 1500, height: 1000 },
  })).newPage();
  page.on('pageerror', (e) => { throw new Error(`page error: ${e.message}`); });
});

/** Open the teach camera and freeze a frame, leaving the crop layer up. */
async function freezeAFrame(): Promise<void> {
  await page.goto(`${BASE}#/products`);
  await page.getByRole('button', { name: 'By photo', exact: true }).click();
  await page.locator('.mg-pic-head button', { hasText: 'Use the camera' }).click();
  await page.getByRole('button', { name: /START CAMERA/i }).click();
  await expect(page.locator('.stage video')).toBeVisible();
  // The camera has to deliver a frame before the burst means anything.
  await expect
    .poll(async () => page.evaluate(() => {
      const v = document.querySelector('.stage video') as HTMLVideoElement | null;
      return v ? v.videoWidth : 0;
    }), { timeout: 20_000 })
    .toBe(FRAME_W);
  await page.getByRole('button', { name: /CAPTURE THIS FRAME/i }).click();
  await expect(page.locator('.mg-croplayer')).toBeVisible({ timeout: 30_000 });
  // Keep the whole frozen frame, at frame resolution. Every expectation below
  // is stated against THIS image rather than against the RGB the scene was
  // authored in, so nothing depends on how the camera pipeline rendered it.
  await page.evaluate(() => {
    const img = document.querySelector('.mg-still') as HTMLImageElement;
    const c = document.createElement('canvas');
    c.width = img.naturalWidth; c.height = img.naturalHeight;
    c.getContext('2d')!.drawImage(img, 0, 0);
    (window as unknown as { __frame: HTMLCanvasElement }).__frame = c;
  });
}

/**
 * Read the captured still back and describe it.
 *
 *   w, h    its size in pixels — the drawn box, if the mapping is right.
 *   diff    mean absolute error, per channel, against the pixels of the frozen
 *           frame inside `want`. -1 when the sizes differ and it cannot be
 *           computed. Two JPEG generations sit between the two images.
 *   tally   the fraction of the capture nearest to each landmark colour.
 *
 * Landmark colours are sampled from the FROZEN FRAME, not written down here:
 * the question is what this camera pipeline actually produced, and nearest-of-
 * five is robust to the checkerboard texture in a way a fixed radius is not.
 */
async function readCapture(want: { x: number; y: number; w: number; h: number }) {
  return page.evaluate(async (arg) => {
    const img = document.querySelector('.mg-still') as HTMLImageElement;
    if (!img.complete || !img.naturalWidth) await new Promise((r) => { img.onload = r; });
    const c = document.createElement('canvas');
    c.width = img.naturalWidth; c.height = img.naturalHeight;
    const ctx = c.getContext('2d')!;
    ctx.drawImage(img, 0, 0);
    const cut = ctx.getImageData(0, 0, c.width, c.height).data;

    const frame = (window as unknown as { __frame: HTMLCanvasElement }).__frame;
    const fx = frame.getContext('2d')!;

    const ref: Record<string, [number, number, number]> = {};
    for (const [k, p] of Object.entries(arg.probe)) {
      const d = fx.getImageData(p.x, p.y, 1, 1).data;
      ref[k] = [d[0], d[1], d[2]];
    }

    let diff = -1;
    if (c.width === arg.want.w && c.height === arg.want.h) {
      const px = fx.getImageData(arg.want.x, arg.want.y, arg.want.w, arg.want.h).data;
      let sum = 0;
      const n = px.length / 4;
      for (let i = 0; i < n; i += 1) {
        sum += Math.abs(cut[i * 4] - px[i * 4])
          + Math.abs(cut[i * 4 + 1] - px[i * 4 + 1])
          + Math.abs(cut[i * 4 + 2] - px[i * 4 + 2]);
      }
      diff = sum / (n * 3);
    }

    const names = Object.keys(ref);
    const tally: Record<string, number> = {};
    for (const k of names) tally[k] = 0;
    const total = cut.length / 4;
    for (let i = 0; i < total; i += 1) {
      const r = cut[i * 4], g = cut[i * 4 + 1], b = cut[i * 4 + 2];
      let best = names[0], bd = Infinity;
      for (const k of names) {
        const [rr, gg, bb] = ref[k];
        const d = (r - rr) ** 2 + (g - gg) ** 2 + (b - bb) ** 2;
        if (d < bd) { bd = d; best = k; }
      }
      tally[best] += 1;
    }
    for (const k of names) tally[k] /= total;
    return { w: c.width, h: c.height, diff, tally };
  }, { probe: PROBE as unknown as Record<string, { x: number; y: number }>, want });
}

/**
 * Where the frozen frame's pixels are on screen, measured from the still
 * itself. `object-fit: contain` scales by the smaller of the two ratios and
 * centres what is left over; this is that rule, applied by the test rather than
 * asked of the page, so it is an independent answer and not the page's own.
 */
async function pictureBox(): Promise<{ x: number; y: number; w: number; h: number }> {
  return page.evaluate(() => {
    const img = document.querySelector('.mg-still') as HTMLImageElement;
    const r = img.getBoundingClientRect();
    const s = Math.min(r.width / img.naturalWidth, r.height / img.naturalHeight);
    const w = img.naturalWidth * s;
    const h = img.naturalHeight * s;
    return { x: r.left + (r.width - w) / 2, y: r.top + (r.height - h) / 2, w, h };
  });
}

test('the box drawn over the picture is the crop that is captured', async () => {
  await freezeAFrame();

  const pic = await pictureBox();
  // The still is 16:9 inside a 4:3 stage, so it must NOT fill it. If this ever
  // stops being true the rest of the test is not measuring anything.
  const stage = (await page.locator('.stage').boundingBox())!;
  expect(pic.h).toBeLessThan(stage.height - 20);

  const at = (fx: number, fy: number) => ({
    x: pic.x + (fx / FRAME_W) * pic.w,
    y: pic.y + (fy / FRAME_H) * pic.h,
  });
  const from = at(SCENE.drawn.x, SCENE.drawn.y);
  const to = at(SCENE.drawn.x + SCENE.drawn.w, SCENE.drawn.y + SCENE.drawn.h);

  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move((from.x + to.x) / 2, (from.y + to.y) / 2, { steps: 6 });
  await page.mouse.move(to.x, to.y, { steps: 6 });
  await page.mouse.up();

  // 1. THE VISIBLE PROMISE. The dashed rectangle on screen covers the frame
  //    pixels the operator meant. A crop that is right while the box shown is
  //    wrong is still a lie to the person drawing it.
  const shown = await page.evaluate(() => {
    const r = (document.querySelector('.mg-croprect') as HTMLElement).getBoundingClientRect();
    return { x: r.left, y: r.top, w: r.width, h: r.height };
  });
  const shownFrame = {
    x: ((shown.x - pic.x) / pic.w) * FRAME_W,
    y: ((shown.y - pic.y) / pic.h) * FRAME_H,
    w: (shown.w / pic.w) * FRAME_W,
    h: (shown.h / pic.h) * FRAME_H,
  };
  expect(shownFrame.x).toBeCloseTo(SCENE.drawn.x, -1);
  expect(shownFrame.y).toBeCloseTo(SCENE.drawn.y, -1);
  expect(shownFrame.w).toBeCloseTo(SCENE.drawn.w, -1);
  expect(shownFrame.h).toBeCloseTo(SCENE.drawn.h, -1);

  // 2. THE CAPTURE. Cut the burst to the box and keep the gate's survivor.
  await page.getByRole('button', { name: /USE THIS BOX/i }).click();
  await expect(page.locator('.stage-bar', { hasText: 'CAPTURED' }))
    .toBeVisible({ timeout: 60_000 });

  const got = await readCapture(SCENE.drawn);

  console.log('captured', JSON.stringify(got));

  // 3. THE SIZE. 480x680, the box that was drawn. Before the fix this came back
  //    480x510 — right across, a quarter short down.
  expect(got.w).toBe(SCENE.drawn.w);
  expect(got.h).toBe(SCENE.drawn.h);
  expect(got.h).not.toBe(SCENE.squeezed.h);

  // 4. THE PIXELS. Not merely the right shape — the right part of the frame.
  //    Two JPEG generations sit between the two images, so a few levels of
  //    mean error is expected and a wrong region is tens.
  expect(got.diff).toBeGreaterThanOrEqual(0);
  expect(got.diff).toBeLessThan(12);

  // 5. THE BANDS. The brand lockup and the foot are inside the drawn box and
  //    outside the squeezed one, so this is the operator's complaint stated as
  //    a number: the top of the carton and its bottom edge are still there.
  //    ~8.1% of the crop each, by construction.
  expect(got.tally.brand).toBeGreaterThan(0.04);
  expect(got.tally.foot).toBeGreaterThan(0.04);
  // ...and the face they kept outside the box is still outside it.
  expect(got.tally.face).toBeLessThan(0.005);
});

test('a box drawn low down takes the bottom of the frame, not the middle', async () => {
  // The first box is symmetric about the frame's centre, where an offset error
  // and a scale error partly cancel. This one is deliberately off-centre — the
  // foot band and the counter under it — and under the old mapping it came
  // back as y 540..622: mostly carton BODY, the wrong part of the product,
  // taught under the right name.
  await freezeAFrame();
  const pic = await pictureBox();
  const band = { x: 400, y: 600, w: 480, h: 110 };
  const at = (fx: number, fy: number) => ({
    x: pic.x + (fx / FRAME_W) * pic.w,
    y: pic.y + (fy / FRAME_H) * pic.h,
  });
  const from = at(band.x, band.y);
  const to = at(band.x + band.w, band.y + band.h);
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(to.x, to.y, { steps: 8 });
  await page.mouse.up();

  await page.getByRole('button', { name: /USE THIS BOX/i }).click();
  await expect(page.locator('.stage-bar', { hasText: 'CAPTURED' }))
    .toBeVisible({ timeout: 60_000 });

  const got = await readCapture(band);

  console.log('low box', JSON.stringify(got));
  expect(got.w).toBe(band.w);
  expect(got.h).toBe(band.h);
  expect(got.diff).toBeGreaterThanOrEqual(0);
  expect(got.diff).toBeLessThan(12);
  // 60 of the 110 rows are foot band across 440 of the 480 columns: ~50%, and
  // the counter below the carton is the rest. The old mapping produced 480x82
  // pixels taken from y 540..622 — 98% carton BODY, ~2% foot.
  expect(got.tally.foot).toBeGreaterThan(0.35);
  expect(got.tally.room).toBeGreaterThan(0.2);
});

/**
 * THE OTHER HALF OF THE SAME BUG.
 *
 * After USE THIS BOX, TEACH THIS PRODUCT was refused with
 * `matless_region_touches_every_border` — the server re-segmenting a rectangle
 * a person had already drawn, and refusing it for the tightness this very
 * screen asked for. The server now takes a hand-drawn region as the
 * segmentation when the page says one was drawn, so the page has to actually
 * say it, on this path and not on the file-upload path.
 *
 * The request is INTERCEPTED and answered locally. Proving the field is sent
 * does not require teaching anything, and a browser test that can write to the
 * shopkeeper's catalogue has destroyed it once already.
 */
test('teaching a hand-drawn box tells the server a human drew it', async () => {
  await freezeAFrame();
  const pic = await pictureBox();
  const at = (fx: number, fy: number) => ({
    x: pic.x + (fx / FRAME_W) * pic.w,
    y: pic.y + (fy / FRAME_H) * pic.h,
  });
  const from = at(SCENE.carton.x, SCENE.carton.y);
  const to = at(SCENE.carton.x + SCENE.carton.w, SCENE.carton.y + SCENE.carton.h);
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(to.x, to.y, { steps: 8 });
  await page.mouse.up();
  await page.getByRole('button', { name: /USE THIS BOX/i }).click();
  await expect(page.locator('.stage-bar', { hasText: 'CAPTURED' }))
    .toBeVisible({ timeout: 60_000 });

  let body = '';
  await page.route('**/enrol', async (route) => {
    body = route.request().postData() ?? '';
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true, appearance_only: true,
        stored: { sku_id: 'e2e_never_stored', name: 'intercepted',
                  price_paise: 4200, price_rupees: '42.00' },
      }),
    });
  });

  await page.locator('input[placeholder="parle_g_biscuit"]').fill('e2e_never_stored');
  await page.locator('input[placeholder="Parle-G biscuit 100g"]').fill('intercepted');
  await page.locator('input[placeholder="10"]').fill('42');
  await page.getByRole('button', { name: /TEACH THIS PRODUCT/i }).click();
  await expect.poll(() => body.length, { timeout: 30_000 }).toBeGreaterThan(0);

  expect(body).toContain('name="mode"');
  expect(body).toMatch(/name="mode"[\s\S]{0,40}plain_photo/);
  expect(body).toContain('name="region"');
  expect(body).toMatch(/name="region"[\s\S]{0,40}user_drawn/);
});

test('a file the operator merely uploaded does NOT claim a human drew the region', async () => {
  // The flag is a claim about provenance. An uploaded photo has had no box
  // drawn on it, so the server must still be the one to find the product in it
  // — and still refuse by name when it cannot.
  await page.goto(`${BASE}#/products`);
  await page.getByRole('button', { name: 'By photo', exact: true }).click();
  await expect(page.locator('.thumb-slot')).toBeVisible();

  let body = '';
  await page.route('**/enrol', async (route) => {
    body = route.request().postData() ?? '';
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ ok: true, appearance_only: true,
        stored: { sku_id: 'e2e_never_stored', name: 'intercepted',
                  price_paise: 4200, price_rupees: '42.00' } }),
    });
  });

  await page.locator('input[placeholder="parle_g_biscuit"]').fill('e2e_never_stored');
  await page.locator('input[placeholder="Parle-G biscuit 100g"]').fill('intercepted');
  await page.locator('input[placeholder="10"]').fill('42');
  await page.locator('input[type=file]').first().setInputFiles({
    name: 'photo.png', mimeType: 'image/png',
    // A 1x1 PNG. It never reaches a segmenter — the request is intercepted.
    buffer: Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
      'base64'),
  });
  await page.getByRole('button', { name: /TEACH THIS PRODUCT/i }).click();
  await expect.poll(() => body.length, { timeout: 30_000 }).toBeGreaterThan(0);

  expect(body).toMatch(/name="mode"[\s\S]{0,40}plain_photo/);
  expect(body).not.toContain('name="region"');
});
