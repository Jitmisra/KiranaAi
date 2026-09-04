import { describe, expect, it } from 'vitest';
import { SpeechDirector, type AudioLike, type DirectorEvents } from './lipsync';

/**
 * The mouth on a FETCHED voice reads the audio element's own clock.
 *
 * `speechSynthesis` reveals timing one boundary at a time; an <audio> element
 * carries `currentTime` every frame. So the natural-voice path scales the
 * reading-speed estimate to the file's real duration and then reads position
 * off the element. These tests drive a fake element through the same events a
 * real one fires and check the one rule that matters: THE MOUTH DOES NOT MOVE
 * WHILE THE AUDIO IS NOT PLAYING.
 */

class FakeAudio implements AudioLike {
  src: string;
  duration = NaN;
  paused = true;
  ended = false;
  currentTime = 0;
  playCalls = 0;
  private handlers = new Map<string, Set<() => void>>();
  constructor(url: string) { this.src = url; }
  play(): Promise<void> { this.playCalls += 1; return Promise.resolve(); }
  pause(): void { this.paused = true; }
  addEventListener(type: string, fn: () => void): void {
    if (!this.handlers.has(type)) this.handlers.set(type, new Set());
    this.handlers.get(type)!.add(fn);
  }
  removeEventListener(type: string, fn: () => void): void { this.handlers.get(type)?.delete(fn); }
  /** What the browser would do once the bytes are decoding. */
  startPlaying(durationS: number): void {
    this.duration = durationS;
    this.paused = false;
    this.fire('playing');
  }
  finish(): void { this.ended = true; this.paused = true; this.fire('ended'); }
  fail(): void { this.fire('error'); }
  fire(type: string): void { for (const fn of this.handlers.get(type) ?? []) fn(); }
}

/** requestAnimationFrame does not exist in node; the director's frame loop is
    driven by hand. */
function installRaf() {
  const queue: FrameRequestCallback[] = [];
  (globalThis as any).requestAnimationFrame = (fn: FrameRequestCallback) => { queue.push(fn); return queue.length; };
  (globalThis as any).cancelAnimationFrame = () => { queue.length = 0; };
  return { tick: () => { const q = queue.splice(0); for (const fn of q) fn(0); } };
}

function harness() {
  const seen: string[] = [];
  const words: number[] = [];
  let speaking: boolean[] = [];
  const events: DirectorEvents = {
    viseme: (v) => seen.push(v),
    word: (i) => words.push(i),
    speaking: (on) => speaking.push(on),
  };
  const d = new SpeechDirector(null, events);
  return { d, seen, words, speaking, reset: () => { seen.length = 0; words.length = 0; speaking = []; } };
}

describe('a fetched voice drives the mouth on the audio clock', () => {
  it('does not open the mouth until the audio is actually playing', () => {
    const raf = installRaf();
    const { d, seen, speaking } = harness();
    let fake!: FakeAudio;
    let done = 0;
    d.speakAudio({ text: 'Nothing has been billed today.', url: 'blob:x', done: () => { done += 1; } },
      (url) => (fake = new FakeAudio(url)));

    expect(fake.playCalls).toBe(1);
    raf.tick();
    expect(speaking).toEqual([]);           // nothing has fired `playing` yet
    expect(seen).toEqual([]);
    expect(done).toBe(0);

    fake.startPlaying(3);
    expect(speaking).toEqual([true]);
  });

  it('reads position off currentTime and closes when the audio ends', () => {
    const raf = installRaf();
    const { d, seen, speaking } = harness();
    let fake!: FakeAudio;
    let done = 0;
    d.speakAudio({ text: 'aaj kuch nahi bika', url: 'blob:y', done: () => { done += 1; } },
      (url) => (fake = new FakeAudio(url)));
    fake.startPlaying(2);

    // Swept, not sampled: three hand-picked instants once all landed in the
    // silent gaps BETWEEN words after the schedule was stretched to 2 s, and
    // the test called a correct mouth broken. Over the whole file it must
    // open more than once and close more than once.
    for (let t = 0.05; t < 2; t += 0.05) { fake.currentTime = t; raf.tick(); }
    const open = seen.filter((v) => v !== 'sil');
    expect(open.length).toBeGreaterThan(3);
    expect(seen.filter((v) => v === 'sil').length).toBeGreaterThan(1);

    fake.finish();
    expect(seen[seen.length - 1]).toBe('sil');
    expect(speaking[speaking.length - 1]).toBe(false);
    expect(done).toBe(1);
  });

  it('a paused element is a closed mouth, whatever the schedule says', () => {
    const raf = installRaf();
    const { d, seen } = harness();
    let fake!: FakeAudio;
    d.speakAudio({ text: 'derma is Rs 400.00', url: 'blob:z', done: () => {} },
      (url) => (fake = new FakeAudio(url)));
    fake.startPlaying(2);
    fake.currentTime = 0.5; raf.tick();
    fake.paused = true;                      // the tab was backgrounded
    fake.currentTime = 0.8; raf.tick();
    expect(seen[seen.length - 1]).toBe('sil');
  });

  it('the caption word follows the audio and clears at the end', () => {
    const raf = installRaf();
    const { d, words } = harness();
    let fake!: FakeAudio;
    d.speakAudio({ text: 'one two three four', url: 'blob:w', done: () => {} },
      (url) => (fake = new FakeAudio(url)));
    fake.startPlaying(4);
    fake.currentTime = 0.1; raf.tick();
    fake.currentTime = 2.1; raf.tick();
    fake.currentTime = 3.9; raf.tick();
    const lit = words.filter((i) => i >= 0);
    expect(lit[0]).toBe(0);
    expect(lit[lit.length - 1]).toBe(3);
    fake.finish();
    expect(words[words.length - 1]).toBe(-1);
  });

  it('a browser that refuses to play still calls done, and the mouth stays shut', async () => {
    installRaf();
    const { d, speaking } = harness();
    let done = 0;
    class Refusing extends FakeAudio {
      override play(): Promise<void> { return Promise.reject(new Error('NotAllowedError')); }
    }
    d.speakAudio({ text: 'hello', url: 'blob:r', done: () => { done += 1; } }, (u) => new Refusing(u));
    await Promise.resolve(); await Promise.resolve();
    expect(done).toBe(1);
    expect(speaking).toEqual([]);
  });

  it('an error mid-sentence closes the mouth and calls done once', () => {
    installRaf();
    const { d, speaking } = harness();
    let fake!: FakeAudio;
    let done = 0;
    d.speakAudio({ text: 'hello there', url: 'blob:e', done: () => { done += 1; } },
      (url) => (fake = new FakeAudio(url)));
    fake.startPlaying(1);
    fake.fail();
    fake.fail();                             // a second error must not call done twice
    expect(done).toBe(1);
    expect(speaking[speaking.length - 1]).toBe(false);
  });

  it('cancel pauses the audio, releases the blob, and closes the mouth', () => {
    const raf = installRaf();
    const { d, seen } = harness();
    let fake!: FakeAudio;
    d.speakAudio({ text: 'a long sentence to interrupt', url: 'blob:c', done: () => {} },
      (url) => (fake = new FakeAudio(url)));
    fake.startPlaying(5);
    fake.currentTime = 1; raf.tick();
    d.cancel();
    expect(fake.paused).toBe(true);
    expect(fake.src).toBe('');
    expect(seen[seen.length - 1]).toBe('sil');
  });

  it('a second speak cancels the first voice before starting', () => {
    installRaf();
    const { d } = harness();
    let first!: FakeAudio;
    d.speakAudio({ text: 'first', url: 'blob:1', done: () => {} }, (u) => (first = new FakeAudio(u)));
    first.startPlaying(3);
    let second!: FakeAudio;
    d.speakAudio({ text: 'second', url: 'blob:2', done: () => {} }, (u) => (second = new FakeAudio(u)));
    expect(first.paused).toBe(true);
    expect(second.playCalls).toBe(1);
  });

  it('empty text or no url speaks nothing and still calls done', () => {
    installRaf();
    const { d } = harness();
    let done = 0;
    d.speakAudio({ text: '   ', url: 'blob:n', done: () => { done += 1; } }, (u) => new FakeAudio(u));
    d.speakAudio({ text: 'hello', url: '', done: () => { done += 1; } }, (u) => new FakeAudio(u));
    expect(done).toBe(2);
  });
});
