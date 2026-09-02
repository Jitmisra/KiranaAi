import { test, expect, chromium, type Browser, type Page } from '@playwright/test';

/**
 * The teaching quality gate, in a real browser.
 *
 * This file used to cover four capability screens as well. Those screens are
 * gone — the product is the till and the catalogue — and what survives here is
 * the one thing they left behind that a shopkeeper actually depends on: a
 * reference learned from a bad photograph is wrong for as long as it stays in
 * the catalogue, so teaching from the camera runs a burst through the gate.
 */

const BASE = process.env.GAWAAH_BASE || 'http://127.0.0.1:8790';

let browser: Browser;
let page: Page;

test.beforeAll(async () => { browser = await chromium.launch(); });
test.afterAll(async () => { await browser?.close(); });

test.beforeEach(async () => {
  page = await (await browser.newContext({ viewport: { width: 1500, height: 1100 } })).newPage();
  page.on('pageerror', (e) => { throw new Error(`page error: ${e.message}`); });
});

test('the products page states which teaching path the quality gate protects', async () => {
  await page.goto(`${BASE}#/products`);
  // Appearance mode, file source: the gate cannot apply, and the page says so
  // rather than implying a protection that is not there.
  const photo = page.getByRole('button', { name: /photo/i }).first();
  if (await photo.count()) await photo.click();
  const body = (await page.locator('main').innerText()).toLowerCase();
  expect(body).toContain('cannot');
});
