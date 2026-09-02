import { describe, it, expect } from 'vitest';
import { PEAK, loudnessContractHolds } from './audio';

/**
 * AN ABSTENTION MUST NEVER BE QUIETER THAN A SUCCESS.
 *
 * A refusal that sounds like a success trains a shopkeeper to stop hearing the
 * difference, and the whole product rests on that difference carrying across a
 * shop. `loudnessContractHolds()` narrates in its own docstring the mutation
 * test that proved an earlier version vacuous — and was then exported, called
 * by nothing, tested by nothing, and tree-shaken out of the bundle entirely.
 *
 * Reproduced before writing this: setting ABSTAIN to 0.01 left the suite green
 * at 68/68 with the refusal twenty times quieter than the chirp.
 */
describe('the loudness contract', () => {
  it('holds for the shipped constants', () => {
    expect(loudnessContractHolds()).toBe(true);
  });

  it('is stated over the constants the voices actually use', () => {
    expect(Math.max(PEAK.ABSTAIN, PEAK.ABSTAIN_2))
      .toBeGreaterThanOrEqual(Math.max(PEAK.NAMED, PEAK.NAMED_2));
  });

  it('is loud enough to carry over a fan and a television', () => {
    // Tuned on headphones in a quiet room the first time, and inaudible in the
    // room this product actually runs in.
    expect(PEAK.NAMED).toBeGreaterThanOrEqual(0.2);
    expect(PEAK.ABSTAIN).toBeGreaterThanOrEqual(0.3);
  });

  it('every peak is a sane gain, not a clipped one', () => {
    for (const [name, v] of Object.entries(PEAK)) {
      expect(v, `${name} is outside a usable gain range`).toBeGreaterThan(0);
      expect(v, `${name} would clip`).toBeLessThanOrEqual(0.5);
    }
  });
});
