import { test, expect, chromium, type Browser, type Page } from '@playwright/test';

/**
 * THE OPERATOR OVERRULING THE CAMERA.
 *
 * The bill was read-only. If the counter added the wrong thing, or a customer
 * changed their mind at the last second, the only options were CLEAR — which
 * wipes the whole order — or charge for something nobody wanted. On a real
 * counter that is not a rare case; it is most of a shift.
 *
 * These drive the same fake camera feed as `till.spec.ts` (a QR held in the
 * top-left corner, rolled 37 degrees) so a line really is billed by the camera
 * before it is edited by hand.
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
    ],
  });
});
test.afterAll(async () => { await browser?.close(); });

test.beforeEach(async () => {
  const ctx = await browser.newContext({
    viewport: { width: 1500, height: 1000 },
    permissions: ['camera'],
  });
  page = await ctx.newPage();
  page.on('pageerror', (e) => { throw new Error(`page error: ${e.message}`); });
  await page.goto(BASE);
  await page.getByRole('button', { name: 'START CAMERA' }).click();
  await expect(page.locator('.bill-line')).toHaveCount(1, { timeout: 30_000 });
});

async function total(p: Page): Promise<string> {
  return (await p.locator('.bill-total .amt').textContent()) ?? '';
}

test('one more of a line, and the total follows', async () => {
  // RELATIVE, NOT ABSOLUTE. The feed is still running, so the camera can commit
  // a second packet between reading the quantity and clicking. Asserting "the
  // quantity is 2" makes the test a race; asserting "it went up by exactly one"
  // is the property the button actually promises.
  const line = page.locator('.bill-line').first();
  const before = Number(await line.locator('.qty').textContent());
  const beforeTotal = await total(page);

  await line.getByRole('button', { name: /One more/ }).click();

  await expect(line.locator('.qty')).toHaveText(String(before + 1));
  expect(await total(page)).not.toBe(beforeTotal);
});

test('one fewer, and the last one takes the line off the bill', async () => {
  const line = page.locator('.bill-line').first();
  await line.getByRole('button', { name: /One more/ }).click();
  await expect(page.locator('.bill-line .qty')).toHaveText('2');

  await line.getByRole('button', { name: /One fewer/ }).click();
  await expect(page.locator('.bill-line .qty')).toHaveText('1');

  // The last one removes the row rather than leaving a zero-quantity line,
  // which charges nothing and reads as an item somebody forgot to price.
  await line.getByRole('button', { name: /One fewer/ }).click();
  await expect(page.locator('.bill-line')).toHaveCount(0);
  await expect(page.locator('.bill-total .amt')).toHaveText('₹0.00');
});

test('a line can be taken off the bill outright', async () => {
  await page.locator('.bill-line').first().getByRole('button', { name: /off the bill/ }).click();
  await expect(page.locator('.bill-line')).toHaveCount(0);
  await expect(page.locator('.bill-total .amt')).toHaveText('₹0.00');
});

test('a removed line does not bounce straight back while the packet is in view', async () => {
  /**
   * The feed never stops showing the packet. The tracker keeps it in `seen`
   * with `missing: 0`, so it must NOT re-commit while it sits there — otherwise
   * removing a line would be undone a moment later by the camera and the
   * operator could never overrule it at all.
   */
  await page.locator('.bill-line').first().getByRole('button', { name: /off the bill/ }).click();
  await expect(page.locator('.bill-line')).toHaveCount(0);
  await page.waitForTimeout(6000);              // longer than the 5 s cooldown
  await expect(page.locator('.bill-line')).toHaveCount(0);
});

test('an empty bill cannot be charged', async () => {
  await page.locator('.bill-line').first().getByRole('button', { name: /off the bill/ }).click();
  const pay = page.locator('.btn.pay');
  await expect(pay).toBeDisabled();
  await expect(pay).toHaveText('NOTHING ON THE COUNTER');
});

test('the controls are reachable from the keyboard, not only on hover', async () => {
  // They are hidden until hover on purpose — a bill that shouts - + x at every
  // row reads like a spreadsheet. Hidden must not mean unreachable.
  const minus = page.locator('.bill-line').first().getByRole('button', { name: /One fewer/ });
  await minus.focus();
  await expect(minus).toBeFocused();
});
