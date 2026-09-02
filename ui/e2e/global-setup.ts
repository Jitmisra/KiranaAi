import type { FullConfig } from '@playwright/test';

/**
 * THE SUITE PROVIDES ITS OWN FIXTURE PRODUCT.
 *
 * `tools/make_fake_cam.py` renders a QR reading `gawaah:parle_g_biscuit`, and
 * a dozen tests bill from it. That worked only while the LIVE catalogue
 * happened to contain that sku — so the morning the shopkeeper cleared their
 * shop and taught three new products, twelve end-to-end tests went red with
 * `code_names_a_missing_product`, describing nothing but the state of somebody
 * else's data.
 *
 * That is the same defect that had silently switched off twenty-four detector
 * tests, which globbed the live shop's photo directory and skipped when it
 * emptied. A test that reads live data is a test that reports on live data.
 *
 * So the suite now teaches what it needs, by CODE (no photograph, no mat, no
 * descriptor — a name and a price bound to a string of digits). It teaches only
 * if the sku is absent, and it never removes it: a shopkeeper who really does
 * sell Parle-G keeps their own row, price and all, untouched.
 */
const SKU = 'parle_g_biscuit';

async function main(config: FullConfig) {
  const base = process.env.GAWAAH_BASE
    || (config.projects[0]?.use?.baseURL as string)
    || 'http://127.0.0.1:8790';

  const shop = await fetch(`${base}/shop`).then((r) => r.json()).catch(() => null);
  if (!shop?.ok) {
    throw new Error(`the till is not answering on ${base} — start it before the e2e suite`);
  }
  if ((shop.skus ?? []).some((s: { sku_id: string }) => s.sku_id === SKU)) {
    return;
  }

  const form = new FormData();
  // A 1x1 PNG. The code path stores no descriptor, so the pixels are never used
  // — but the endpoint wants a file part, and inventing a photograph would be
  // teaching an appearance nobody photographed.
  const png = Uint8Array.from(atob(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
  ), (c) => c.charCodeAt(0));
  form.set('image', new Blob([png], { type: 'image/png' }), 'code.png');
  form.set('sku_id', SKU);
  form.set('name', 'Parle-G biscuit 100g');
  form.set('price_rupees', '10');
  form.set('mode', 'basket');
  form.set('barcode', `gawaah:${SKU}`);

  const res = await fetch(`${base}/enrol`, { method: 'POST', body: form });
  const body = await res.json().catch(() => ({}));
  if (!body?.ok) {
    throw new Error(
      `could not teach the fixture product ${SKU}: ${JSON.stringify(body).slice(0, 300)}`);
  }
}

export default main;
