import { describe, expect, it } from 'vitest';

/**
 * The boundary must not name a cause it cannot read from the message.
 *
 * It used to print "A price that is not a whole number of paise…" under EVERY
 * error it caught. An audit caught it live explaining a Vite CSS preload
 * failure that way. These are the real messages, from the real engines.
 */
const MONEY = /\bpaise\b|not money|rupee|sub-paisa|integer paise|amount .*(too|out of)/i;
const CHUNK = /dynamically imported module|Importing a module script failed|error loading dynamically imported|Unable to preload|preload (CSS|module)|Loading (CSS )?chunk|ChunkLoadError/i;
const classify = (m: string) => CHUNK.test(m) ? 'chunk' : MONEY.test(m) ? 'money' : 'unknown';

describe('the error boundary classifies before it explains', () => {
  it('calls a chunk failure a chunk failure, in every engine that words it differently', () => {
    // Chrome / Vite, verbatim from the audit that found this.
    expect(classify('Unable to preload CSS for /assets/Today-CwcbXBrj.css')).toBe('chunk');
    expect(classify('Failed to fetch dynamically imported module: /assets/Gst-BX2.js')).toBe('chunk');
    // Firefox.
    expect(classify('error loading dynamically imported module')).toBe('chunk');
    // Safari.
    expect(classify('Importing a module script failed.')).toBe('chunk');
    // webpack-era wording that still shows up in the wild.
    expect(classify('ChunkLoadError: Loading chunk 42 failed.')).toBe('chunk');
    expect(classify('Loading CSS chunk 7 failed.')).toBe('chunk');
  });

  it('claims the money assertion only for what money.ts actually throws', () => {
    expect(classify('float is not money: 12.9')).toBe('money');
    expect(classify("sub-paisa precision is not money: '12.505'")).toBe('money');
    expect(classify('not a whole number of paise')).toBe('money');
    expect(classify("bad rupee string: '4820²'")).toBe('money');
    expect(classify('amount is too large for this till')).toBe('money');
  });

  it('admits it does not know, rather than reaching for the impressive answer', () => {
    expect(classify('Cannot read properties of undefined (reading map)')).toBe('unknown');
    expect(classify('NetworkError when attempting to fetch resource.')).toBe('unknown');
    expect(classify('t is not a function')).toBe('unknown');
    expect(classify('')).toBe('unknown');
  });

  it('never calls a chunk failure a money assertion — the bug that was shipped', () => {
    for (const m of [
      'Unable to preload CSS for /assets/Today-CwcbXBrj.css',
      'Failed to fetch dynamically imported module: /assets/Settings-9f2.js',
    ]) {
      expect(classify(m)).not.toBe('money');
    }
  });
});
