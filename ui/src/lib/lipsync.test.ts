import { describe, expect, it } from 'vitest';
import { chunks, paceFrom } from './lipsync';
import { CPS } from './visemes';

describe('paceFrom — how fast this voice actually is', () => {
  const fallback = 1000 / CPS;

  it('falls back when the engine has not said anything yet', () => {
    expect(paceFrom([])).toBeCloseTo(fallback, 5);
    expect(paceFrom([], 90)).toBe(90);
  });

  it('takes the median, so one long pause does not slow the whole mouth down', () => {
    // A comma costs the engine most of a second. A mean over six samples would
    // let that one pause double the estimate for the next word.
    const withPause = paceFrom([60, 62, 58, 900, 61, 59]);
    const without = paceFrom([60, 62, 58, 61, 59]);
    expect(Math.abs(withPause - without)).toBeLessThan(6);
  });

  it('throws out samples that cannot be a per-character cost', () => {
    expect(paceFrom([0, -5, 1e9, 70, 72])).toBeCloseTo(71, 0);
    expect(paceFrom([0, 0, 0])).toBeCloseTo(fallback, 5);
  });

  it('is bounded either side, so no sample can make the mouth crawl or strobe', () => {
    // Clamped to 18..190 ms per character — roughly three times either side of
    // every real voice measured on this machine.
    expect(paceFrom([5, 5, 5])).toBe(18);
    expect(paceFrom([380, 380, 380])).toBe(190);
    expect(paceFrom([64, 66, 65])).toBe(65);
  });

  it('ignores a sample too small to be a character at all', () => {
    // Under 5 ms per character is an engine reporting two boundaries in the
    // same tick, not a voice.
    expect(paceFrom([2, 3, 1])).toBeCloseTo(fallback, 5);
  });

  it('averages the middle two on an even sample', () => {
    expect(paceFrom([40, 60])).toBe(50);
  });
});

describe('chunks — the sentence-sized pieces Chrome will actually finish', () => {
  it('packs whole sentences together up to the limit, and never splits inside one', () => {
    // The split point is a sentence end, but short sentences RIDE TOGETHER: an
    // utterance boundary is an audible gap, and one per full stop makes an
    // answer sound like a list being read out.
    expect(chunks('One. Two. Three.')).toEqual(['One. Two. Three.']);
    expect(chunks('आज की बिक्री। दो बिल।')).toEqual(['आज की बिक्री। दो बिल।']);
  });

  it('breaks at a sentence end once the piece is too long for Chrome', () => {
    const a = `${'a'.repeat(150)}.`;
    const b = `${'b'.repeat(150)}.`;
    expect(chunks(`${a} ${b}`)).toEqual([a, b]);
  });

  it('recognises the Devanagari danda as a sentence end', () => {
    const a = `${'क'.repeat(150)}।`;
    const b = `${'ख'.repeat(150)}।`;
    expect(chunks(`${a} ${b}`)).toEqual([a, b]);
  });

  it('keeps every piece under the length Chrome truncates at', () => {
    const long = 'Aaj ki bikri bahut hui hai aur abhi bhi kuch bill baaki hain. '.repeat(8);
    for (const piece of chunks(long)) expect(piece.length).toBeLessThanOrEqual(240);
  });

  it('loses nothing: every word survives the split', () => {
    const text = 'Aaj ki bikri 80 rupaye hui. Do bill bane. Kal dekhenge.';
    expect(chunks(text).join(' ')).toBe(text);
  });

  it('returns one piece for one sentence, and none for nothing', () => {
    expect(chunks('just the one')).toEqual(['just the one']);
    expect(chunks('')).toEqual([]);
    expect(chunks('   ')).toEqual([]);
  });

  it('never emits an empty piece, which would be an utterance that says nothing', () => {
    for (const piece of chunks('A.  B.   C.')) expect(piece.trim()).not.toBe('');
  });
});
