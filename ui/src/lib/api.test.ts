import { describe, it, expect, vi, afterEach } from 'vitest';
import * as api from './api';

/**
 * How a response becomes a Result.
 *
 * This file exists because the rule has been wrong twice, in opposite
 * directions, and both times every HTTP request returned a plausible status
 * while the page said something false:
 *
 *   - Read through the till's convention alone, paisa's SUCCESS (which carries
 *     no `ok` field at all) looked like a refusal, and a real minted Razorpay
 *     payment link was rendered as an amber card with an empty title.
 *   - Fixing that by trusting the body alone made the opposite mistake: a
 *     FastAPI 422 or 500 answers `{"detail": ...}` with neither `ok` nor
 *     `error`, so a crash would have been reported to the caller as a success
 *     whose every field was undefined.
 *
 * Precedence: explicit `ok` → `error` string → HTTP status.
 */

const reply = (status: number, body: unknown) => {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  })));
};

afterEach(() => { vi.unstubAllGlobals(); });

describe('a response becomes a Result', () => {
  it('trusts an explicit ok:true', async () => {
    reply(200, { ok: true, count: 7, skus: [] });
    const r = await api.shop();
    expect(r.ok).toBe(true);
    expect(r.ok && r.count).toBe(7);
  });

  it('trusts an explicit ok:false even on a 200', async () => {
    reply(200, { ok: false, reason: 'mat_not_locked' });
    const r = await api.shop();
    expect(r.ok).toBe(false);
    expect(r.ok === false && r.reason).toBe('mat_not_locked');
  });

  it('treats the till’s deliberate 400 refusal as a result, not a crash', async () => {
    // A refusal IS the product working. It must keep its reason.
    reply(400, { ok: false, reason: 'no_code_readable', detail: 'nothing decoded' });
    const r = await api.shop();
    expect(r.ok === false && r.reason).toBe('no_code_readable');
  });

  it('reads paisa’s success, which carries no ok field at all', async () => {
    reply(200, {
      session_id: 'till_x', state: 'CALLING', amount_paise: 1000,
      short_url: 'https://rzp.io/rzp/abc',
    });
    const r = await api.mint({ session_id: 'till_x', amount_paise: 1000, scan_id: 'scn_1' });
    expect(r.ok, 'a real minted payment link was filed as a refusal').toBe(true);
    expect(r.ok && r.short_url).toBe('https://rzp.io/rzp/abc');
  });

  it('reads paisa’s refusal, which uses `error` rather than `reason`', async () => {
    reply(409, { error: 'scan_not_found', detail: "no scan witness '' on this counter" });
    const r = await api.mint({ session_id: 'till_x', amount_paise: 1000, scan_id: '' });
    expect(r.ok).toBe(false);
    // The title must not be empty — that produced a blank amber card on screen.
    expect(r.ok === false && r.reason).toBe('scan_not_found');
    expect(r.ok === false && r.detail).toContain('no scan witness');
  });

  it('does NOT report a FastAPI 422 as a success', async () => {
    reply(422, { detail: [{ loc: ['body', 'amount_paise'], msg: 'field required' }] });
    const r = await api.mint({ session_id: 'x', amount_paise: 1, scan_id: 'y' });
    expect(r.ok, 'a validation failure was reported as a success').toBe(false);
    expect(r.ok === false && r.reason).toMatch(/422/);
  });

  it('does NOT report a 500 as a success', async () => {
    reply(500, { detail: 'Internal Server Error' });
    const r = await api.health();
    expect(r.ok, 'a server crash was reported as a success').toBe(false);
    expect(r.ok === false && r.detail).toContain('Internal Server Error');
  });

  it('says the network failed, rather than blaming the product', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    const r = await api.health();
    expect(r.ok).toBe(false);
    expect(r.ok === false && r.reason).toMatch(/could not reach/);
  });

  it('does not pretend non-JSON is a result', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true, status: 200, json: async () => { throw new SyntaxError('bad'); },
    })));
    const r = await api.health();
    expect(r.ok).toBe(false);
    expect(r.ok === false && r.reason).toMatch(/not JSON/);
  });
});

/** The JSON body the client actually put on the wire. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const sentBody = (spy: { mock: { calls: any[][] } }): Record<string, unknown> => {
  const call = spy.mock.calls[0];
  if (!call) throw new Error('fetch was never called');
  return JSON.parse(String(call[1].body));
};

describe('the mint body', () => {
  it('nests the scan id, because that is where the server reads it', async () => {
    // Sent flat, `scan.scan_id` arrives as '' and paisa refuses with
    // scan_not_found — which reads like a broken witness, not a bad request.
    const spy = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ short_url: 'x' }) }));
    vi.stubGlobal('fetch', spy);
    await api.mint({ session_id: 's', amount_paise: 500, scan_id: 'scn_abc' });
    const body = sentBody(spy);
    expect(body.scan).toEqual({ scan_id: 'scn_abc' });
    expect(body.scan_id, 'the flat key would be silently ignored').toBeUndefined();
    expect(body.amount_paise).toBe(500);
  });

  it('sends no price of its own — only what the server already told us', async () => {
    const spy = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }));
    vi.stubGlobal('fetch', spy);
    await api.mint({ session_id: 's', amount_paise: 500, scan_id: 'scn_abc' });
    const body = sentBody(spy);
    // Three fields, none of them evidence. paisa re-derives every rupee itself.
    expect(Object.keys(body).sort()).toEqual(['amount_paise', 'scan', 'session_id']);
  });
});
