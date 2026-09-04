import { describe, expect, it } from 'vitest';
import {
  CPS, MIN_HOLD, estimateUtterance, scheduleWord, visemesForWord, wordAt, wordSpans,
  type Viseme,
} from './visemes';

/** Just the shapes, in order — the weights are tested separately. */
const shapes = (word: string): Viseme[] => visemesForWord(word).map((s) => s.v);

describe('visemesForWord — Latin', () => {
  it('reads a lip closure at the front of a bilabial', () => {
    expect(shapes('maggi')[0]).toBe('PP');
    expect(shapes('bill')[0]).toBe('PP');
    expect(shapes('paisa')[0]).toBe('PP');
  });

  it('takes digraphs as one sound, not two letters', () => {
    expect(shapes('the')[0]).toBe('TH');
    expect(shapes('shop')[0]).toBe('CH');
    expect(shapes('chai')[0]).toBe('CH');
    // The one that catches every naive mapper: "ph" is an F, and reading it as
    // p-then-h puts a lip press in the middle of the word.
    expect(shapes('phone')[0]).toBe('FF');
    expect(shapes('phone')).not.toContain('PP');
  });

  it('never leaves a vowel silent', () => {
    for (const w of ['aaj', 'order', 'stock', 'margin', 'rupees']) {
      const out = shapes(w);
      expect(out.some((v) => ['aa', 'E', 'I', 'O', 'U'].includes(v))).toBe(true);
    }
  });

  it('gives digits shapes, because every engine reads them out loud', () => {
    expect(shapes('139')).toHaveLength(3);
    expect(shapes('139')).not.toContain('sil');
  });

  it('collapses a repeated shape rather than tapping it twice', () => {
    // "ll" is one tongue position held, not two taps — two would read as a
    // stutter at 60 frames a second.
    const out = visemesForWord('bill');
    const nn = out.filter((s) => s.v === 'nn');
    expect(nn).toHaveLength(1);
  });

  it('rests on a word with nothing sayable in it', () => {
    expect(shapes('₹')).toEqual(['sil']);
    expect(shapes('')).toEqual(['sil']);
    expect(shapes('—')).toEqual(['sil']);
  });
});

describe('visemesForWord — Devanagari', () => {
  it('gives a bare consonant its inherent vowel, but not at the end of a word', () => {
    // कल — the final consonant's schwa is deleted in Hindi, so this is
    // kk + aa + nn and NOT kk + aa + nn + aa.
    expect(shapes('कल')).toEqual(['kk', 'aa', 'nn']);
  });

  it('lets a matra replace the inherent vowel', () => {
    // की is kk + I, with no 'aa' anywhere.
    expect(shapes('की')).toEqual(['kk', 'I']);
    expect(shapes('को')).toEqual(['kk', 'O']);
    expect(shapes('कू')).toEqual(['kk', 'U']);
  });

  it('lets the virama delete it, so a conjunct is two consonants running together', () => {
    // क् + क has no vowel between the two.
    expect(shapes('क्क')).toEqual(['kk']);   // and the repeat is collapsed
    expect(shapes('स्त')).toEqual(['SS', 'TH']);
  });

  it('reads फ as an aspirated P and not as an F', () => {
    // The single most common wrong mapping. फल is a lip press, not a lip on
    // teeth, and the difference is visible on a face.
    expect(shapes('फल')).toEqual(['PP', 'aa', 'nn']);
    expect(shapes('फल')).not.toContain('FF');
  });

  it('but reads फ़, with the nukta, as an F — decomposed or precomposed', () => {
    expect(shapes('फ़')[0]).toBe('FF');            // precomposed U+095E
    expect(shapes('फ़')[0]).toBe('FF');           // फ + U+093C
  });

  it('collapses aspiration, because a puff of air is not a mouth shape', () => {
    expect(shapes('क')).toEqual(shapes('ख'));
    expect(shapes('ग')).toEqual(shapes('घ'));
    expect(shapes('ट')).toEqual(shapes('ठ'));
  });

  it('gives the anusvara its own beat', () => {
    expect(shapes('हूं')).toContain('nn');
  });

  it('reads a real sentence without ever sitting on silence', () => {
    for (const w of ['आज', 'की', 'बिक्री', 'कितनी', 'हुई']) {
      expect(shapes(w)).not.toContain('sil');
      expect(shapes(w).length).toBeGreaterThan(0);
    }
  });
});

describe('visemesForWord — Bengali', () => {
  it('uses /ɔ/ as the inherent vowel, not /ə/', () => {
    // The rounded O is what makes a Bengali word the right shape. Borrowing
    // Devanagari's open 'aa' here is wrong from the first syllable.
    expect(shapes('কল')).toEqual(['kk', 'O', 'nn']);
    expect(shapes('কল')).not.toContain('aa');
  });

  it('has no /v/ and no /w/: ব and ভ are both a lip press', () => {
    expect(shapes('ব')).toEqual(['PP']);
    expect(shapes('ভ')).toEqual(['PP']);
    expect(shapes('বল')).not.toContain('FF');
  });

  it('treats all three sibilants as /ʃ/', () => {
    expect(shapes('শ')).toEqual(['CH']);
    expect(shapes('ষ')).toEqual(['CH']);
    expect(shapes('স')).toEqual(['CH']);
  });

  it('takes its own matras and its own virama', () => {
    expect(shapes('কি')).toEqual(['kk', 'I']);
    expect(shapes('ক্ত')).toEqual(['kk', 'TH']);
  });
});

describe('wordSpans', () => {
  it('indexes into the ORIGINAL string, which is what a boundary event reports', () => {
    const text = 'Aaj ki bikri 80 rupaye.';
    const spans = wordSpans(text);
    expect(spans.map((s) => s.text)).toEqual(['Aaj', 'ki', 'bikri', '80', 'rupaye']);
    for (const s of spans) expect(text.slice(s.start, s.end)).toBe(s.text);
  });

  it('keeps a Devanagari word whole instead of splitting it at every matra', () => {
    // Without \p{M} in the pattern every combining vowel sign became its own
    // word, and the mouth ran three times too fast through a Hindi sentence.
    const spans = wordSpans('आज की बिक्री');
    expect(spans.map((s) => s.text)).toEqual(['आज', 'की', 'बिक्री']);
  });

  it('finds nothing in a string with no words', () => {
    expect(wordSpans('   ,.— ')).toEqual([]);
    expect(wordSpans('')).toEqual([]);
  });
});

describe('wordAt', () => {
  const spans = wordSpans('one two three');

  it('lands in the word the index is inside', () => {
    expect(wordAt(spans, 0)).toBe(0);
    expect(wordAt(spans, 2)).toBe(0);
    expect(wordAt(spans, 4)).toBe(1);
    expect(wordAt(spans, 8)).toBe(2);
  });

  it('reads an index in the whitespace as the word that has not started yet', () => {
    // Engines disagree about whether charIndex points at the letter or at the
    // space in front of it; both readings have to land on the same word.
    expect(wordAt(spans, 3)).toBe(1);
    expect(wordAt(spans, 7)).toBe(2);
  });

  it('degrades to the last word rather than to nothing', () => {
    expect(wordAt(spans, 9999)).toBe(spans.length - 1);
    expect(wordAt([], 5)).toBe(-1);
  });
});

describe('scheduleWord', () => {
  it('lays the shapes out inside the duration it was given, in order', () => {
    const cues = scheduleWord(visemesForWord('bikri'), 400);
    expect(cues.length).toBeGreaterThan(1);
    for (let i = 1; i < cues.length; i++) {
      expect(cues[i]!.at).toBeGreaterThanOrEqual(cues[i - 1]!.at);
    }
    expect(cues[0]!.at).toBe(0);
    expect(cues[cues.length - 1]!.at).toBeLessThan(400);
  });

  it('gives a vowel more time than a plosive', () => {
    // Weighting them equally gave a mouth that chewed evenly through a word
    // instead of pulsing on its vowels, which is what reads as "not talking".
    const cues = scheduleWord([{ v: 'PP', w: 0.7 }, { v: 'aa', w: 1.8 }, { v: 'PP', w: 0.7 }], 640);
    const first = cues[1]!.at - cues[0]!.at;
    const vowel = cues[2]!.at - cues[1]!.at;
    expect(vowel).toBeGreaterThan(first);
  });

  it('shares the time evenly rather than strobing when the word is too fast', () => {
    const steps = visemesForWord('bikri');
    const cues = scheduleWord(steps, 60);
    for (let i = 1; i < cues.length; i++) {
      const held = cues[i]!.at - cues[i - 1]!.at;
      expect(held).toBeGreaterThan(0);
    }
    // Every shape gets the same slice once the floor binds, so no single one
    // is a one-frame flicker between two long holds.
    const holds = cues.slice(1).map((c, i) => c.at - cues[i]!.at);
    expect(Math.max(...holds) - Math.min(...holds)).toBeLessThanOrEqual(1);
  });

  it('says nothing about nothing', () => {
    expect(scheduleWord([], 300)).toEqual([]);
  });

  it('never asks for a hold shorter than the eye can see', () => {
    expect(MIN_HOLD).toBeGreaterThanOrEqual(40);
  });
});

describe('estimateUtterance — the fallback for an engine with no boundary events', () => {
  const text = 'Aaj ki bikri 80 rupaye hui. Do bill bane.';

  it('starts at zero and ends closed', () => {
    const cues = estimateUtterance(text);
    expect(cues[0]!.at).toBe(0);
    expect(cues[cues.length - 1]!.v).toBe('sil');
  });

  it('runs forwards and only forwards', () => {
    const cues = estimateUtterance(text);
    for (let i = 1; i < cues.length; i++) {
      expect(cues[i]!.at).toBeGreaterThanOrEqual(cues[i - 1]!.at);
    }
  });

  it('finishes in about the time the sentence takes to say', () => {
    const cues = estimateUtterance(text);
    const end = cues[cues.length - 1]!.at;
    const expected = (text.length / CPS) * 1000;
    // Within a quarter either way. It is an estimate and it is only ever used
    // inside a window the engine itself opened and closed.
    expect(end).toBeGreaterThan(expected * 0.75);
    expect(end).toBeLessThan(expected * 1.25);
  });

  it('runs faster at a faster rate', () => {
    const slow = estimateUtterance(text, 1);
    const fast = estimateUtterance(text, 1.5);
    expect(fast[fast.length - 1]!.at).toBeLessThan(slow[slow.length - 1]!.at);
  });

  it('closes the mouth over a long pause between words', () => {
    const cues = estimateUtterance('one.        two');
    expect(cues.filter((c) => c.v === 'sil').length).toBeGreaterThan(1);
  });

  it('has nothing to say about an empty string', () => {
    expect(estimateUtterance('')).toEqual([]);
    expect(estimateUtterance('   ')).toEqual([]);
  });
});
