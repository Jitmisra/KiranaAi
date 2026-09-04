import { test, expect, chromium, type Browser, type Page } from '@playwright/test';

/**
 * THE CUSTOMER'S SIDE OF THE COUNTER.
 *
 * `routes/Display.tsx` and `lib/displaybus.ts` were both built and NOTHING EVER
 * CALLED `publish`. The second screen — the one a customer reads, the one whose
 * reader cannot ask anybody what happened — therefore sat on "Nothing has
 * reached this screen yet" through every bill this till has ever rung up. It
 * type-checked, every unit test passed, and the only way to see it was to open
 * the screen and look.
 *
 * So these tests open TWO TABS OF ONE BROWSER, which is exactly what the bus
 * is: the Till in one, the display in the other. They assert the wire, not the
 * markup — that a line billed on the shopkeeper's screen appears on the
 * customer's, at the same money, and that clearing the bill takes it away
 * again.
 *
 * They also pin the states that only the customer ever sees, because those are
 * the ones nobody discovers by using the product: a display opened before any
 * Till ever ran, and a bill left standing long enough to go stale.
 */

const BASE = process.env.GAWAAH_BASE || 'http://127.0.0.1:8790';
const FEED = process.env.GAWAAH_FEED || '/tmp/gawaah_cam.y4m';

let browser: Browser;

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

/** One context = one browser as far as the bus is concerned. Two tabs in it. */
async function shopBrowser(): Promise<{ till: Page; display: Page; close: () => Promise<void> }> {
  const ctx = await browser.newContext({
    permissions: ['camera'], viewport: { width: 1280, height: 900 },
  });
  const till = await ctx.newPage();
  const display = await ctx.newPage();
  for (const p of [till, display]) {
    p.on('pageerror', (e) => { throw new Error(`page error: ${e.message}`); });
  }
  return { till, display, close: () => ctx.close() };
}

test('a display opened before any till has run says so, and says what to do', async () => {
  const { display, close } = await shopBrowser();
  await display.goto(`${BASE}#/display`);

  // Not a blank navy rectangle, and not a spinner that never ends.
  await expect(display.locator('.disp-welcome-word')).toBeVisible();
  const note = display.locator('.disp-note', { hasText: 'Nothing has reached this screen yet' });
  await expect(note).toBeVisible();
  // A state is not drawn until it names the next step.
  await expect(note).toContainText('Open the Till');
  await close();
});

test('a line billed at the till appears on the customer display, at the same money', async () => {
  test.setTimeout(120_000);
  const { till, display, close } = await shopBrowser();

  // The control: this display rendered, and it has no bill on it. (What it
  // SAYS about that is the previous test's business; asserting the sentence
  // twice only buys a second way to fail.)
  await display.goto(`${BASE}#/display`);
  await expect(display.locator('.disp-welcome-word')).toBeVisible();
  await expect(display.locator('.disp-line')).toHaveCount(0);

  await till.goto(`${BASE}#/till`);
  await till.getByRole('button', { name: 'START CAMERA' }).click();
  await expect(till.locator('.bill-line')).toHaveCount(1, { timeout: 40_000 });
  const billed = await till.locator('.bill-total .amt').textContent();
  const name = await till.locator('.bill-line .nm').textContent();

  // The wire. No server is involved: this is one browser talking to itself.
  await expect(display.locator('.disp-line')).toHaveCount(1, { timeout: 20_000 });
  await expect(display.locator('.disp-line .nm')).toHaveText(name!);

  // THE NUMBER IS THE SAME NUMBER. The display re-adds the lines and refuses to
  // show a total it cannot derive; if this ever reads "cannot be shown" the two
  // sides of the counter disagree and the customer is told so.
  await expect(display.locator('.disp-total .amt')).toHaveText(billed!);
  await expect(display.locator('.disp-cannot')).toHaveCount(0);
  await close();
});

test('clearing the bill clears the customer display', async () => {
  test.setTimeout(120_000);
  const { till, display, close } = await shopBrowser();

  await till.goto(`${BASE}#/till`);
  await till.getByRole('button', { name: 'START CAMERA' }).click();
  await expect(till.locator('.bill-line')).toHaveCount(1, { timeout: 40_000 });

  // Opened LATE, mid-bill. It must show the bill already in progress rather
  // than waiting for the next change — that is what the storage half of the
  // bus is for.
  await display.goto(`${BASE}#/display`);
  await expect(display.locator('.disp-line')).toHaveCount(1, { timeout: 20_000 });

  await till.getByRole('button', { name: 'CLEAR' }).click();
  await expect(display.locator('.disp-line')).toHaveCount(0, { timeout: 20_000 });
  await expect(display.locator('.disp-welcome-word')).toBeVisible();
  await close();
});

test('the display never shows a customer a bill that has gone stale', async () => {
  const { display, close } = await shopBrowser();

  // Written straight onto the bus, dated 45 minutes ago. A real one is written
  // by the Till; the age is what is under test, and no camera can wait 45 real
  // minutes inside a test suite.
  await display.addInitScript(() => {
    window.localStorage.setItem('gawaah.display.v1', JSON.stringify({
      v: 1, id: 'stale-e2e', at: Date.now() - 45 * 60 * 1000, shop: null, phase: 'bill',
      lines: [{ sku_id: 'x', name: 'Yesterday biscuit', qty: 1, price_paise: 9900 }],
      total_paise: 9900, pay: null, paid: null,
    }));
  });
  await display.goto(`${BASE}#/display`);

  await expect(display.locator('.disp-line')).toHaveCount(0);
  await expect(display.locator('body')).not.toContainText('Yesterday biscuit');
  await expect(display.locator('.disp-note', { hasText: 'last spoke' })).toContainText('45 minutes ago');
  await close();
});

test('the customer display carries no shopkeeper chrome and no horizontal scroll', async () => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  page.on('pageerror', (e) => { throw new Error(`page error: ${e.message}`); });
  await page.goto(`${BASE}#/display`);
  await expect(page.locator('.disp')).toBeVisible();

  // The sidebar, the top bar and the dock are the OTHER side of the counter.
  for (const sel of ['.side', '.topbar', '.sk-fab']) {
    await expect(page.locator(sel)).toBeHidden();
  }
  const over = await page.evaluate(() => {
    const de = document.documentElement;
    return { w: de.scrollWidth, c: de.clientWidth };
  });
  expect(over.w, `the customer display scrolls sideways at 390px (${over.w} > ${over.c})`)
    .toBeLessThanOrEqual(over.c + 1);
  await ctx.close();
});
