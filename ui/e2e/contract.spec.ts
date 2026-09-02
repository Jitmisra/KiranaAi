import { test, expect, request } from '@playwright/test';

/**
 * Does the server actually return the fields this front end reads?
 *
 * This file exists because of a real defect. `src/lib/api.ts` once declared
 * `health.catalog_size` and `moneyHealth.reachable`. Neither exists. In
 * TypeScript both were simply `undefined` at runtime — nothing threw, no test
 * failed, and the status chips confidently reported "0 taught" over a shop of
 * seven products and "gateway down" over a working gateway.
 *
 * A type is a claim about someone else's data. Only this file checks it.
 */

const BASE = process.env.GAWAAH_BASE || 'http://127.0.0.1:8790';

/** Assert every path is present and not undefined, naming the one that failed. */
function requireFields(obj: unknown, fields: string[], where: string): void {
  for (const f of fields) {
    const v = f.split('.').reduce<unknown>((o, k) => (o as Record<string, unknown>)?.[k], obj);
    expect(v, `${where}.${f} is missing — the UI reads it and would render undefined`).not.toBeUndefined();
  }
}

test('/health returns every field the shell reads', async () => {
  const api = await request.newContext();
  const r = await api.get(`${BASE}/health`);
  expect(r.ok()).toBe(true);
  requireFields(await r.json(), [
    'service', 'buffer_px', 'mat_mm', 'px_per_mm', 'opencv', 'model_weights',
    'identity_gates.phi', 'identity_gates.phi_appearance_only',
    'identity_gates.theta', 'identity_gates.tau_mm',
  ], 'health');
});

test('/api/money/health returns every field the shell reads', async () => {
  const api = await request.newContext();
  const r = await api.get(`${BASE}/api/money/health`);
  expect(r.ok()).toBe(true);
  requireFields(await r.json(), [
    'mode', 'key_id', 'key_secret_configured', 'webhook_secret_configured',
    'price_book_entries', 'payment_links', 'ledger_lines', 'intents_by_state',
    'intents_needing_human',
  ], 'money health');
});

/**
 * Presence is not enough. `footprint_mm` was present on every product and the
 * UI indexed it as a pair — it is a single float, so every measured product
 * displayed "NaN×NaN MM". Checking the SHAPE is what catches that.
 */
function requireShape(obj: unknown, spec: Record<string, string>, where: string): void {
  for (const [field, want] of Object.entries(spec)) {
    const v = (obj as Record<string, unknown>)?.[field];
    const got = v === null ? 'null' : Array.isArray(v) ? 'array' : typeof v;
    const allowed = want.split('|');
    expect(allowed, `${where}.${field} is ${got} (${JSON.stringify(v)}), the UI treats it as ${want}`)
      .toContain(got);
  }
}

test('/shop returns the count and the per-product fields the catalogue renders', async () => {
  const api = await request.newContext();
  const r = await api.get(`${BASE}/shop`);
  const body = await r.json();
  requireFields(body, ['count', 'skus'], 'shop');
  if (body.skus.length) {
    requireShape(body.skus[0], {
      sku_id: 'string',
      name: 'string',
      price_paise: 'number',
      price_rupees: 'string',
      footprint_mm: 'number|null',   // ONE number of millimetres, never a pair
      codes: 'array',                // every code bound to it, never a bare `code`
      appearance_only: 'boolean',
      taught_with: 'string',
      thumb_png: 'string|null',
      n_views: 'number',
    }, 'shop.skus[0]');
    expect(Number.isInteger(body.skus[0].price_paise),
      'a price came back as a float — invariant 1 is integer paise').toBe(true);
  }
});

test('a price is never a float, anywhere in the catalogue', async () => {
  const api = await request.newContext();
  const body = await (await api.get(`${BASE}/shop`)).json();
  for (const s of body.skus ?? []) {
    expect(Number.isInteger(s.price_paise), `${s.sku_id} priced at ${s.price_paise}`).toBe(true);
  }
});

test('the counter refuses rather than crashing on rubbish', async () => {
  const api = await request.newContext();
  const r = await api.post(`${BASE}/recognise`, {
    multipart: { mode: 'basket', image: { name: 'x.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('not an image') } },
  });
  // A refusal is a RESULT: 400 with a reason, never a 500 and never a throw.
  expect(r.status(), 'a bad upload produced a server error instead of a refusal').toBeLessThan(500);
  const body = await r.json();
  expect(body.ok).toBe(false);
  expect(typeof body.reason, 'a refusal must say why').toBe('string');
});
