import { test, expect, chromium, type Browser, type Page } from '@playwright/test';

/**
 * End to end, in a real browser, against the real server.
 *
 * The fake camera feed holds a QR code in the TOP-LEFT CORNER of the frame,
 * rolled 37 degrees. That is deliberate: the till used to crop to the centre
 * 56% x 66% of the view and would read nothing at all here. If the bill fills,
 * the counter really is looking at the whole frame.
 *
 * Run the till on :8790 and `make ui` first. Generate the feed with
 * tools/make_fake_cam.py.
 */

const BASE = process.env.GAWAAH_BASE || 'http://127.0.0.1:8790';
const FEED = process.env.GAWAAH_FEED || '/tmp/gawaah_cam.y4m';

let browser: Browser;
let page: Page;

test.beforeAll(async () => {
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
  const ctx = await browser.newContext({ permissions: ['camera'], viewport: { width: 1440, height: 950 } });
  page = await ctx.newPage();
  // A console error or a CSP violation is a failure, not noise. The whole point
  // of tightening script-src was that nothing inline remains to be blocked.
  page.on('pageerror', (e) => { throw new Error(`page error: ${e.message}`); });
});

test('the shell loads and routes without a single console error', async () => {
  const errors: string[] = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(BASE);

  await expect(page.locator('.brand-mark')).toHaveText('KIRANA SHOP AI');
  // The nav is a grouped sidebar; each button carries a label and a sub-label,
  // so it is addressed by its label text rather than by an exact accessible name.
  for (const label of ['Products', 'Till']) {
    const btn = page.locator('.side nav button', { hasText: label }).first();
    await btn.click();
    await expect(btn).toHaveAttribute('aria-current', 'page');
  }
  expect(errors, `console errors: ${errors.join(' | ')}`).toHaveLength(0);
});

test('the catalogue loads from the server', async () => {
  await page.goto(`${BASE}#/products`);
  // `.mg-sku` is the catalogue card class Products.tsx actually renders — the
  // screen was reworked and the old `.sku` card no longer exists anywhere.
  await expect(page.locator('.mg-sku').first()).toBeVisible({ timeout: 15_000 });
  const n = await page.locator('.mg-sku').count();
  expect(n).toBeGreaterThan(0);
});

test('a QR in the corner of the view reaches the bill', async () => {
  await page.goto(BASE);
  await page.getByRole('button', { name: 'START CAMERA' }).click();

  // The camera must actually start — this is the button that was once covered
  // by the overlay canvas and dead in every real hand while tests passed.
  await expect(page.locator('.stage-bar')).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('.stage-bar')).toContainText('WHOLE FRAME');

  await expect(page.locator('.bill-line')).toHaveCount(1, { timeout: 30_000 });
  await expect(page.locator('.bill-line .nm')).toContainText('Parle-G');
  await expect(page.locator('.bill-total .amt')).not.toHaveText('₹0.00');
});

test('a held packet is billed once, not once per frame', async () => {
  await page.goto(BASE);
  await page.getByRole('button', { name: 'START CAMERA' }).click();
  await expect(page.locator('.bill-line')).toHaveCount(1, { timeout: 30_000 });

  // The feed keeps showing the same packet. Several seconds of it must not
  // become several lines, and must not become a growing quantity.
  const before = await page.locator('.bill-line .qty').textContent();
  await page.waitForTimeout(4000);
  await expect(page.locator('.bill-line')).toHaveCount(1);
  await expect(page.locator('.bill-line .qty')).toHaveText(before ?? '×1');
});

test('the counter area narrows when the operator asks it to', async () => {
  await page.goto(BASE);
  await page.getByRole('button', { name: 'START CAMERA' }).click();
  await expect(page.locator('.stage-bar')).toContainText('WHOLE FRAME', { timeout: 20_000 });

  const stage = page.locator('.stage');
  const box = (await stage.boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.45, box.y + box.height * 0.45);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.85, box.y + box.height * 0.85, { steps: 8 });
  await page.mouse.up();

  await expect(page.locator('.stage-bar')).toContainText('CROPPED');

  // ...and REDRAW must return a working area, never a dead counter.
  await page.getByRole('button', { name: 'REDRAW AREA' }).click();
  await expect(page.locator('.stage-bar')).toContainText('WHOLE FRAME');
});

test('switching to appearance mode narrows the upload, and back widens it', async () => {
  await page.goto(BASE);
  await page.getByRole('button', { name: 'START CAMERA' }).click();
  await expect(page.locator('.stage-bar')).toContainText('WHOLE FRAME', { timeout: 20_000 });

  await page.getByRole('button', { name: 'By look' }).click();
  await expect(page.locator('.stage-bar')).toContainText('CROPPED');

  await page.getByRole('button', { name: 'By code' }).click();
  await expect(page.locator('.stage-bar')).toContainText('WHOLE FRAME');
});
