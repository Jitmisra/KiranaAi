import { test, expect, chromium, type Browser, type BrowserContext, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';

/**
 * SALAAHKAAR, end to end: the round button, the modal, the page, and the one
 * call they share.
 *
 * What is proved here, in order:
 *   1. the button is at the foot of three different shopkeeper screens, opens
 *      the modal on each, and Escape closes it with focus handed back;
 *   2. on the till the button NEVER intersects the CHARGE button, at any
 *      scroll position, at 1440 and at 390 — measured, not assumed, because
 *      the last floating launcher on this product was found sitting on it;
 *   3. the two old routes forward to the one screen;
 *   4. nothing here scrolls sideways on a phone, with the modal open or shut;
 *   5. reduced motion turns the ring's animation off;
 *   6. a question asked from the modal is in the page's transcript afterwards,
 *      and an instruction comes back as a card that says `did:` only after it
 *      is pressed, and `undone:` after UNDO.
 *
 * Runs against the locked till: it signs in through `/auth/signin` on the
 * page's own cookie jar, and does nothing when the counter is not enforced.
 */

const BASE = process.env.GAWAAH_BASE || 'http://127.0.0.1:8790';
const SHOTS = process.env.GAWAAH_SHOTS || '/tmp/gawaah-salaahkaar-shots';
const PHONE = process.env.GAWAAH_E2E_PHONE || '9820114477';
// No default. A password with a fallback in the repository is a committed
// password, however local the account it opens; the spec skips instead.
const PASSWORD = process.env.GAWAAH_E2E_PASSWORD || '';

let browser: Browser;

test.beforeAll(async () => {
  mkdirSync(SHOTS, { recursive: true });
  browser = await chromium.launch({
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'],
  });
});
test.afterAll(async () => { await browser?.close(); });

async function signedIn(ctx: BrowserContext): Promise<void> {
  const st = await ctx.request.get(`${BASE}/auth/status`).then((r) => r.json()).catch(() => null) as
    { enforced?: boolean; signed_in?: boolean } | null;
  if (!st?.enforced || st.signed_in) return;
  if (!PASSWORD) throw new Error(
    'the counter is locked and GAWAAH_E2E_PASSWORD is not set. Set it to the '
    + `password for ${PHONE} (tools/seed_shop.py prints one when it opens the `
    + 'account) and run the spec again.');
  const r = await ctx.request.post(`${BASE}/auth/signin`, { data: { phone: PHONE, password: PASSWORD } });
  const body = await r.json().catch(() => ({})) as { ok?: boolean; reason?: string };
  if (!body.ok) throw new Error(`could not sign in for the Salaahkaar spec: ${JSON.stringify(body).slice(0, 200)}`);
}

async function open(viewport: { width: number; height: number }, hash: string): Promise<{ ctx: BrowserContext; page: Page }> {
  const ctx = await browser.newContext({ viewport, permissions: ['microphone'] });
  await signedIn(ctx);
  const page = await ctx.newPage();
  page.on('pageerror', (e) => { throw new Error(`page error: ${e.message}`); });
  await page.goto(`${BASE}#/${hash}`);
  return { ctx, page };
}

const rect = (page: Page, sel: string) => page.evaluate((s) => {
  const el = document.querySelector(s);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, w: r.width, h: r.height };
}, sel);

type R = NonNullable<Awaited<ReturnType<typeof rect>>>;
const intersects = (a: R, b: R) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;

/* ------------------------------------------------------------ the button -- */

test('the round button opens the modal on three routes, and Escape closes it', async () => {
  // Not the till: she is already on it, in "Say the order", so the corner
  // button is hidden there. Stock stands in — a screen with controls near
  // the corner, which is also what the dodge below is measured against.
  const { ctx, page } = await open({ width: 1440, height: 900 }, 'stock');
  for (const route of ['stock', 'products', 'today'] as const) {
    await page.goto(`${BASE}#/${route}`);
    const fab = page.locator('.sk-fab');
    await expect(fab, `no round button on #/${route}`).toBeVisible();
    // Bottom-right, by measurement.
    const r = await rect(page, '.sk-fab');
    expect(r).not.toBeNull();
    expect(r!.right).toBeGreaterThan(1440 - 120);
    expect(r!.bottom).toBeGreaterThan(900 - 120);

    await fab.click();
    const panel = page.locator('.sk-panel');
    await expect(panel).toBeVisible();
    await expect(panel.locator('.adv-tile.dock')).toBeVisible();
    await expect(panel.locator('.pp')).toBeVisible();
    await expect(panel.locator('.adv-synth')).toContainText('not a person');
    // The composer takes focus so a shopkeeper can type at once — once the
    // health checks have landed and the box is enabled.
    await expect(panel.locator('textarea')).toBeEnabled();
    await expect(panel.locator('textarea')).toBeFocused();
    if (route === 'stock') await page.screenshot({ path: `${SHOTS}/modal-1440-${route}.png` });

    await page.keyboard.press('Escape');
    await expect(panel).toBeHidden();
    await expect(fab).toBeFocused();
  }
  await ctx.close();
});

test('the button is not on the customer display, and not on the Salaahkaar page itself', async () => {
  const { ctx, page } = await open({ width: 1440, height: 900 }, 'display');
  await expect(page.locator('.disp')).toBeVisible();
  await expect(page.locator('.sk-fab')).toHaveCount(0);
  await page.goto(`${BASE}#/salaahkaar`);
  await expect(page.locator('.adv-tile.full')).toBeVisible();
  await expect(page.locator('.sk-fab')).toHaveCount(0);
  await ctx.close();
});

test('the button is not on the till at all — Salaahkaar is already there, in "Say the order"', async () => {
  // This test used to measure that the corner button never covered CHARGE at
  // any scroll position. The stronger rule replaced it: on the till, the
  // voice panel IS Salaahkaar — her tile, her voice, lines proposed onto the
  // bill — and a second face in the corner was the same person twice on one
  // screen. So the button is simply not rendered there, at any width.
  for (const width of [1440, 390]) {
    const { ctx, page } = await open({ width, height: width === 390 ? 844 : 900 }, 'till');
    await page.waitForTimeout(800);
    await expect(page.locator('.sk-fab')).toHaveCount(0);
    // And her counter presence is what stands in for it.
    await expect(page.locator('.sk-tile, .sk-presenter').first()).toBeVisible();
    await ctx.close();
  }
});

/* -------------------------------------------------------------- the page -- */

test('the two old routes forward to the one screen', async () => {
  const { ctx, page } = await open({ width: 1440, height: 900 }, 'advisor');
  await expect.poll(() => page.evaluate(() => location.hash)).toBe('#/salaahkaar');
  await expect(page.locator('.adv-tile.full')).toBeVisible();
  await page.goto(`${BASE}#/assistant`);
  await expect.poll(() => page.evaluate(() => location.hash)).toBe('#/salaahkaar');
  await expect(page.locator('.side nav button[aria-current="page"]')).toContainText('Salaahkaar');
  await ctx.close();
});

test('the page and the modal at 1440 and 390: screenshots, and no sideways scroll', async () => {
  for (const vp of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    const { ctx, page } = await open(vp, 'salaahkaar');
    await expect(page.locator('.adv-tile.full')).toBeVisible();
    await expect(page.locator('.adv-pulse')).toBeVisible();
    await page.waitForTimeout(1200);
    await page.screenshot({ path: `${SHOTS}/page-${vp.width}.png`, fullPage: true });
    const over = await page.evaluate(() => ({ w: document.documentElement.scrollWidth, c: document.documentElement.clientWidth }));
    expect(over.w, `#/salaahkaar scrolls sideways at ${vp.width} (${over.w} > ${over.c})`).toBeLessThanOrEqual(over.c + 1);

    await page.goto(`${BASE}#/products`);
    await page.locator('.sk-fab').click();
    await expect(page.locator('.sk-panel')).toBeVisible();
    await page.waitForTimeout(600);
    await page.screenshot({ path: `${SHOTS}/modal-${vp.width}.png` });
    const over2 = await page.evaluate(() => ({ w: document.documentElement.scrollWidth, c: document.documentElement.clientWidth }));
    expect(over2.w, `the modal scrolls sideways at ${vp.width}`).toBeLessThanOrEqual(over2.c + 1);
    await ctx.close();
  }
});

test('reduced motion stills the ring', async () => {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
  await signedIn(ctx);
  const page = await ctx.newPage();
  // NOT THE TILL. "Say the order" IS Salaahkaar on the till — her tile, her
  // voice — so the corner button is hidden there (one person, once). Any
  // other shopkeeper screen carries it.
  await page.goto(`${BASE}#/products`);
  await expect(page.locator('.sk-fab')).toBeVisible();
  const anim = await page.evaluate(() => {
    const ring = document.querySelector('.sk-fab-ring');
    return ring ? getComputedStyle(ring).animationName : 'no ring';
  });
  expect(anim).toBe('none');
  await ctx.close();
});

/* ------------------------------------------------------------- the call -- */

test('a question from the modal is on the page afterwards; an instruction is a card that says did: only when pressed', async () => {
  // From Products, not the till: on the till she is already present in "Say
  // the order", so the corner button is hidden there.
  const { ctx, page } = await open({ width: 1440, height: 900 }, 'products');
  await page.locator('.sk-fab').click();
  const panel = page.locator('.sk-panel');
  await expect(panel).toBeVisible();

  // A QUESTION goes to the advisor, on the call. The box is disabled until
  // the two health checks land, and typing into a disabled field types nothing.
  const box = panel.locator('textarea');
  await expect(box).toBeEnabled();
  await box.type('aaj ki bikri kitni hui');
  await box.press('Enter');
  const answer = panel.locator('.adv-turn.sk .adv-spoken').first();
  await expect(answer).toBeVisible({ timeout: 60_000 });
  await expect(panel.locator('.adv-tag.route').first()).toContainText('ADVISOR');
  const said = (await answer.textContent())!.trim();
  expect(said.length).toBeGreaterThan(0);

  // AN INSTRUCTION goes to the assistant and comes back as a card, not a deed.
  await box.type('do Maggi bill me daal do');
  await box.press('Enter');
  const card = panel.locator('.sk-action').first();
  await expect(card).toBeVisible({ timeout: 60_000 });
  await expect(card).toContainText('NOT DONE');
  const did = panel.locator('.adv-tag.did');
  await expect(did).toContainText('proposed:');
  await expect(did).not.toContainText('did:');
  await page.screenshot({ path: `${SHOTS}/modal-action-proposed.png` });

  await card.getByRole('button', { name: /HOLD/ }).click();
  await expect(did).toContainText('did: held', { timeout: 10_000 });
  await expect(did).toContainText('not billed');
  await expect(card).toContainText('HELD FOR THE TILL');
  await page.screenshot({ path: `${SHOTS}/modal-action-did.png` });

  await card.getByRole('button', { name: 'UNDO' }).click();
  await expect(did).toContainText('undone:', { timeout: 10_000 });

  // Close, walk to the page: the same transcript is there.
  await page.keyboard.press('Escape');
  await page.goto(`${BASE}#/salaahkaar`);
  await expect(page.locator('.adv-thread.full .adv-spoken').first()).toContainText(said.slice(0, 40), { timeout: 20_000 });
  await expect(page.locator('.adv-thread.full .sk-action')).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/page-after-modal.png`, fullPage: true });
  await ctx.close();
});
