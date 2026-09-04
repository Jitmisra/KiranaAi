import { test, expect } from '@playwright/test';

/**
 * THE MICROPHONE HEARS DEVANAGARI AND THE CATALOGUE IS WRITTEN IN LATIN.
 *
 * The recogniser is set to hi-IN, so a shopkeeper saying "ponds" gets back
 * पॉन्ड्स. The in-browser matcher compares latin letters and reported
 *
 *   I heard "पॉन्ड्स" and I do not know it — nothing in this shop is close to it
 *
 * about a product sitting in the catalogue under that exact name.
 *
 * The browser does not carry a transliterator — a second copy of that rule
 * could drift from the one the assistant and the search box use. Instead the
 * local matcher stays the fast offline path, and anything it cannot place is
 * offered to `/search`, which can respell it. Only an EXACT match after
 * respelling is accepted onto a bill.
 *
 * A real `SpeechRecognition` cannot be driven from a test, so one is installed
 * before the page loads — the same approach the camera tests take.
 */

const FAKE_MIC = `
  class FakeRecogniser {
    constructor() { this.lang = ''; this.continuous = false; this.interimResults = false; }
    start() { window.__mic = this; this.onstart && this.onstart(); }
    stop() { this.onend && this.onend(); }
    abort() { this.onend && this.onend(); }
    /** Deliver a final transcript exactly as the real engine would. */
    say(text) {
      const alt = { transcript: text, confidence: 0.99 };
      const res = { 0: alt, isFinal: true, length: 1 };
      this.onresult && this.onresult({ resultIndex: 0, results: { 0: res, length: 1 } });
    }
  }
  window.SpeechRecognition = FakeRecogniser;
  window.webkitSpeechRecognition = FakeRecogniser;
`;

test('a product said in Hindi reaches the bill, and the bill says it was respelt', async ({ page }) => {
  await page.addInitScript(FAKE_MIC);
  await page.goto('/#/till');
  await page.getByRole('button', { name: '🎤 LISTEN' }).click();

  // What hi-IN actually returns when somebody says "two ponds".
  await page.evaluate(() => (window as any).__mic.say('do पॉन्ड्स'));

  const bill = page.locator('.bill-lines');
  await expect(bill).toContainText('ponds', { timeout: 10_000 });
  // It must not be silent about having changed the word.
  await expect(bill).toContainText('spelt in latin letters');
  await expect(bill).not.toContainText('I do not know it');
});

test('a word that merely RESEMBLES a product is refused, not billed', async ({ page }) => {
  await page.addInitScript(FAKE_MIC);
  await page.goto('/#/till');
  await page.getByRole('button', { name: '🎤 LISTEN' }).click();

  // धर्म is one syllable from `derma` and is also an ordinary Hindi word.
  // A resemblance may not walk onto a bill on its own.
  await page.evaluate(() => (window as any).__mic.say('do धर्म'));

  // The refusal is spoken on Salaahkaar's card now, not printed into the bill:
  // "Say the order" became her counter presence, and a word she cannot name is
  // refused there by name. The bill must simply never carry the resemblance.
  await expect(page.locator('.sk-told')).toContainText('nothing called', { timeout: 10_000 });
  await expect(page.locator('.till-receipt')).not.toContainText('derma');
});

test('the microphone can be told which language to listen in', async ({ page }) => {
  await page.addInitScript(FAKE_MIC);
  await page.goto('/#/till');
  await expect(page.locator('.vb-langid')).toHaveText('hi-IN');
  await page.locator('.vb-langs button', { hasText: 'English' }).click();
  await expect(page.locator('.vb-langid')).toHaveText('en-IN');
  // Remembered on this device, so a shop does not re-pick it every morning.
  await page.reload();
  await expect(page.locator('.vb-langid')).toHaveText('en-IN');
});
