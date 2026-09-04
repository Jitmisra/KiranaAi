import { test, expect, chromium, type Browser, type Page } from '@playwright/test';

/**
 * EVERY user-facing feature, exercised end to end in a real browser.
 *
 * The three suites split by what they protect:
 *   till.spec.ts         the counter area and the frames-to-charge rule
 *   capabilities.spec.ts the four capabilities that live on the brain
 *   everything.spec.ts   THIS — the paths a shopkeeper actually walks:
 *                        teach a product, see it priced, charge it, be shown a
 *                        real payment QR, and forget it again.
 *
 * The money path is here because it had never been tested in the React UI at
 * all, and it is the one path where being wrong costs somebody money.
 */

const BASE = process.env.GAWAAH_BASE || 'http://127.0.0.1:8790';
const FEED = process.env.GAWAAH_FEED || '/tmp/gawaah_cam.y4m';
const SKU = 'e2e_probe_sku';
const ROUTES = ['till', 'products'] as const;

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
test.afterAll(async () => {
  // Never leave a probe SKU in the shopkeeper's catalogue. A harness that can
  // write to live data has already destroyed this catalogue once.
  try {
    const api = await (await import('@playwright/test')).request.newContext();
    await api.delete(`${BASE}/shop/${SKU}`);
  } catch { /* nothing to clean */ }
  await browser?.close();
});

test.beforeEach(async () => {
  page = await (await browser.newContext({
    permissions: ['camera'], viewport: { width: 1500, height: 1000 },
  })).newPage();
  page.on('pageerror', (e) => { throw new Error(`page error: ${e.message}`); });
});

/* ------------------------------------------------------------- teaching -- */

test('a shopkeeper can teach a product by typing its code, and forget it again', async () => {
  await page.goto(`${BASE}#/products`);
  await expect(page.locator('.mg-sku').first()).toBeVisible({ timeout: 15_000 });
  const before = await page.locator('.mg-sku').count();

  await page.locator('input[placeholder="parle_g_biscuit"]').fill(SKU);
  await page.locator('input[placeholder="Parle-G biscuit 100g"]').fill('E2E probe packet');
  await page.locator('input[placeholder="10"]').fill('42');
  await page.locator('input[placeholder="8901063093157"]').fill('9990001112223');
  await page.getByRole('button', { name: 'TEACH THIS PRODUCT' }).click();

  await expect(page.locator('.verdict h4')).toContainText('TAUGHT', { timeout: 20_000 });
  await expect(page.locator('.mg-sku')).toHaveCount(before + 1);

  // ...and the price came back as integer paise, not a rounded float.
  await expect(page.locator('.verdict')).toContainText('4200 paise');

  const card = page.locator('.mg-sku', { hasText: 'E2E probe packet' });
  await expect(card).toBeVisible();
  await card.getByRole('button', { name: 'FORGET' }).click();
  await expect(page.locator('.mg-sku')).toHaveCount(before, { timeout: 15_000 });
});

test('teaching refuses, in words, when a required field is missing', async () => {
  await page.goto(`${BASE}#/products`);
  await page.locator('input[placeholder="parle_g_biscuit"]').fill('no_price_sku');
  await page.getByRole('button', { name: 'TEACH THIS PRODUCT' }).click();
  await expect(page.locator('.verdict h4')).toContainText('Needs all three');
  // A refusal is a RESULT. Nothing may have been written.
  await expect(page.locator('.mg-sku', { hasText: 'no_price_sku' })).toHaveCount(0);
});

test('every taught product has a scannable QR that the server renders', async () => {
  await page.goto(`${BASE}#/products`);
  await expect(page.locator('.mg-sku').first()).toBeVisible({ timeout: 15_000 });
  const img = page.locator('.mg-sku img').first();
  await expect(img).toBeVisible();
  // A broken <img> has naturalWidth 0, which proves the endpoint really
  // answered — but so does one that simply has not finished loading. Wait for
  // the decode rather than sampling it once and blaming the server.
  await expect
    .poll(async () => img.evaluate((el: HTMLImageElement) => (el.complete ? el.naturalWidth : 0)),
      { timeout: 15_000, message: 'the product QR never loaded' })
    .toBeGreaterThan(20);
});

/* ---------------------------------------------------------------- money -- */

/**
 * Which gateway the money service is talking to. Asked of the money service
 * itself, not inferred: `sim` is the simulator, anything else is Razorpay's
 * real API on a test key.
 */
async function moneyMode(): Promise<string> {
  try {
    const r = await fetch(`${MONEY}/health`);
    const d = (await r.json()) as { mode?: string };
    return String(d.mode ?? 'unknown');
  } catch { return 'unreachable'; }
}
const MONEY = process.env.GAWAAH_MONEY || 'http://127.0.0.1:8788';

test('THE MONEY PATH: a scanned basket becomes a real payment QR', async () => {
  test.setTimeout(120_000);
  const mode = await moneyMode();
  await page.goto(BASE);
  await page.getByRole('button', { name: 'START CAMERA' }).click();

  // The fake camera holds a Parle-G QR in the corner of the frame.
  await expect(page.locator('.bill-line')).toHaveCount(1, { timeout: 30_000 });
  const total = await page.locator('.bill-total .amt').textContent();
  expect(total).not.toBe('₹0.00');

  await page.getByRole('button', { name: /CHARGE/ }).click();

  // The counter writes its own witness server-side, then paisa reloads that
  // witness BY ID and re-prices it from its own tables before minting. The page
  // never sends a price.
  await expect(page.locator('.pay-grid, .verdict').first()).toBeVisible({ timeout: 45_000 });

  await expect(page.locator('.pay-grid .bill-line')).toHaveCount(1);

  if (mode === 'sim') {
    // THE SIMULATOR CANNOT PRODUCE A PAYABLE LINK, AND THIS TEST USED TO PASS
    // ANYWAY. For most of this project's life `gawaah/rzp_sim.py` minted its
    // links on `https://rzp.io/i/` — Razorpay's real domain — so this spec saw
    // a gateway host, drew a QR of a fabricated address, and went green. One
    // of those addresses reached a customer's phone and answered `404 {}`.
    //
    // Simulated links now live on a reserved `.invalid` host. The till's QR
    // encoder refuses to encode a non-gateway host, by name, and that refusal
    // IS the correct end of this path in sim mode. So in sim this test asserts
    // the safety property: no QR, no gateway-looking anchor, the refusal named
    // on screen, nothing green. The money property is asserted only against
    // the real gateway, below.
    await expect(page.locator('.verdict h4').first()).toHaveText('refused_to_encode_this_string');
    await expect(page.locator('.qr-wrap.big img')).toHaveCount(0);
    await expect(page.locator('.pay-grid a[href^="https://rzp.io"], .pay-grid a[href*="razorpay.com"], .pay-grid a[href^="https://rzp.link"]'))
      .toHaveCount(0);
    // And the screen must not tell the shopkeeper the link is payable — it is
    // not, and saying so about a simulated address is the lie this whole
    // change exists to stop.
    expect((await page.locator('.pay-grid').innerText()).toLowerCase()).not.toContain('still payable');
    await expect(page.locator('.pay-amount')).toHaveText(total!);
    expect(await page.locator('.verdict.green').count(),
      'the page went green without a verified webhook').toBe(0);
    return;
  }

  // THE REAL GATEWAY. The itemised bill on one side, the QR on the other. These
  // come FIRST, and the no-refusal check below comes second, which is the
  // opposite of how this test used to read.
  //
  // WHY THE ORDER MATTERS. The wait above clears as soon as EITHER `.pay-grid`
  // or `.verdict` exists. The refusal check that used to sit here read
  // `allTextContents()` — a snapshot, not a retrying assertion — so a refusal
  // that rendered one tick after the pay grid's first paint was read as an
  // empty list and the test passed on a page that had refused. Waiting for the
  // QR to be visible first puts the page in a terminal state: a rendered QR and
  // a refusal verdict cannot both be the end of this path.
  const qr = page.locator('.qr-wrap.big img');
  await expect(qr).toBeVisible();
  expect(await qr.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(50);

  // NO EARLY RETURN. Every assertion that protects this path — the gateway-host
  // check, the amount match, and the only browser-side guard on invariant 2 —
  // used to sit below a `return` taken on any refusal, so stubbing the mint to
  // fail made a copy of this test pass in 1.5 s. Worse: the PAID verdict also
  // renders an h4, so a page that went green WITHOUT a webhook satisfied the
  // early return and skipped the no-green check entirely.
  expect(await page.locator('.verdict h4').allTextContents(),
    'CHARGE did not mint — the page refused instead').toEqual([]);

  // It must be a RENDER OF THE GATEWAY'S OWN LINK. toHaveAttribute RETRIES;
  // getAttribute does not, and read the href before React had committed it.
  await expect(page.locator('.pay-grid a').first(),
    'the link shown is not a gateway link').toHaveAttribute(
    'href', /^https:\/\/(rzp\.io|.*razorpay\.com|rzp\.link)/);

  // The amount offered must equal the amount billed.
  await expect(page.locator('.pay-amount')).toHaveText(total!);

  // And it must NOT be green. Only a signature-verified webhook can do that.
  expect(await page.locator('.verdict.green').count(),
    'the page went green without a verified webhook').toBe(0);
});

test('the charge button says what it is waiting for, rather than offering ₹0.00', async () => {
  // It used to render a big green "CHARGE ₹0.00" and simply be disabled. The
  // button now names the condition it is waiting on, because a shopkeeper who
  // presses a live-looking CHARGE and gets "nothing on this counter could be
  // priced" reads the product as broken.
  await page.goto(BASE);
  const btn = page.locator('.btn.pay');
  await expect(btn).toBeDisabled();
  await expect(btn).toHaveText('NOTHING ON THE COUNTER');
});

/* ------------------------------------------------------------ integrity -- */

test('no screen anywhere claims a payment settled without a webhook', async () => {
  for (const r of ROUTES) {
    await page.goto(`${BASE}#/${r}`);
    await page.waitForTimeout(400);
    const body = (await page.locator('main').innerText()).toLowerCase();
    expect(body, `${r} claims payment on its own authority`).not.toContain('payment confirmed');
    expect(body, `${r} claims to guarantee`).not.toContain('guaranteed');
  }
});

test('every route renders without a console error', async () => {
  const errors: string[] = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  for (const r of ROUTES) {
    await page.goto(`${BASE}#/${r}`);
    await page.waitForTimeout(700);
    await expect(page.locator('h1')).toBeVisible();
  }
  expect(errors, `console errors: ${errors.join(' | ')}`).toHaveLength(0);
});


/* ------------------------------------------------------ money, after CHARGE -- */

/**
 * These three exist because an adversarial audit found all of them unguarded,
 * and each one can cost a shopkeeper real money.
 */

test('CANCEL returns to a live counter and does NOT double the bill', async () => {
  test.setTimeout(120_000);
  await page.goto(BASE);
  await page.getByRole('button', { name: 'START CAMERA' }).click();
  await expect(page.locator('.bill-line')).toHaveCount(1, { timeout: 30_000 });
  const qty = await page.locator('.bill-line .qty').textContent();
  const total = await page.locator('.bill-total .amt').textContent();

  await page.getByRole('button', { name: /CHARGE/ }).click();
  await expect(page.locator('.pay-grid, .verdict').first()).toBeVisible({ timeout: 45_000 });
  if (!(await page.locator('.pay-grid').count())) return;   // refused; covered elsewhere

  await page.getByRole('button', { name: /CANCEL/ }).click();
  // The packet is still in front of the camera. Resetting the tracker here
  // while leaving the basket alone billed every visible packet a second time:
  // x1 became x2 became x3.
  await page.waitForTimeout(3500);
  await expect(page.locator('.bill-line'), 'CANCEL split the bill into more lines').toHaveCount(1);
  await expect(page.locator('.bill-line .qty'), 'CANCEL re-billed a packet still in view').toHaveText(qty!);
  await expect(page.locator('.bill-total .amt')).toHaveText(total!);
});

test('pressing CHARGE twice replays one payment link, it does not mint a second', async () => {
  test.setTimeout(120_000);
  const minted: string[] = [];
  page.on('request', (r) => {
    if (!r.url().includes('/api/money/mint')) return;
    try { minted.push(JSON.parse(r.postData() ?? '{}').session_id); } catch { /* not our shape */ }
  });

  await page.goto(BASE);
  await page.getByRole('button', { name: 'START CAMERA' }).click();
  await expect(page.locator('.bill-line')).toHaveCount(1, { timeout: 30_000 });

  await page.getByRole('button', { name: /CHARGE/ }).click();
  await expect(page.locator('.pay-grid, .verdict').first()).toBeVisible({ timeout: 45_000 });
  if (!(await page.locator('.pay-grid').count())) return;

  await page.getByRole('button', { name: /CANCEL/ }).click();
  await page.waitForTimeout(3500);
  await page.getByRole('button', { name: /CHARGE/ }).click();
  await expect(page.locator('.pay-grid, .verdict').first()).toBeVisible({ timeout: 45_000 });
  await page.waitForTimeout(1500);

  expect(minted.length, 'the second CHARGE never reached the mint').toBeGreaterThan(1);
  // paisa keys its intents on the session id and hands that nonce to Razorpay as
  // reference_id. A fresh id per press defeats the link cache AND the gateway's
  // own duplicate rejection — the audit log showed three live links in 31 s.
  expect(minted[0], 'a second live payment link was minted for one basket').toBe(minted[1]);
});

test('the amount on the button is the amount that gets minted', async () => {
  test.setTimeout(120_000);
  await page.goto(BASE);
  await page.getByRole('button', { name: 'START CAMERA' }).click();
  await expect(page.locator('.bill-line')).toHaveCount(1, { timeout: 30_000 });
  const button = await page.getByRole('button', { name: /CHARGE/ }).textContent();
  const agreed = button!.replace('CHARGE ', '').trim();

  await page.getByRole('button', { name: /CHARGE/ }).click();
  await expect(page.locator('.pay-grid, .verdict').first()).toBeVisible({ timeout: 45_000 });
  if (!(await page.locator('.pay-grid').count())) return;

  // The button shows the browser basket; the mint sends the fresh witness. Two
  // quantities measured a moment apart, and the page used to echo the server's
  // number back so paisa's divergence guard could never fire from here.
  await expect(page.locator('.pay-amount'),
    'the customer was shown an amount the operator never agreed to').toHaveText(agreed);
});
