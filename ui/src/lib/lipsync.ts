/**
 * LIP SYNC — the mouth, tied to the browser's own voice.
 *
 * `lib/visemes.ts` knows which shapes a word needs. This file knows WHEN, and
 * the whole difficulty is that `speechSynthesis` will not tell you directly.
 * There is no audio node, no waveform, no amplitude. There is one event —
 * `boundary` — which fires as the engine reaches each word and carries
 * `charIndex` and `elapsedTime`.
 *
 * So the model is: a boundary is a FIX, like a lighthouse. Between fixes the
 * mouth runs on dead reckoning at the pace the last few fixes implied, and every
 * new fix corrects it. The pace is measured, not assumed, so a slow Hindi voice
 * and a fast en-IN one both end up in time after a word or two.
 *
 * THE HARD RULE, and every branch below is written to keep it: THE MOUTH DOES
 * NOT MOVE WHILE THE BROWSER IS SILENT. It is armed by `onstart`, disarmed by
 * `onend`, `onerror` and `cancel()`, and a watchdog closes it if the engine
 * stops without saying so — which Chrome does when a tab is backgrounded
 * mid-sentence. Rest is `sil`, and rest is the default of every path.
 *
 * DEGRADING. An engine that never fires `boundary` — Safari on some voices,
 * a few Android builds — still gets a moving mouth, from
 * `estimateUtterance`: laid out at a measured reading speed, started by
 * `onstart`, and cut off by `onend`. It is an estimate INSIDE a window the
 * engine defined, so it can drift within a sentence but it cannot flap over
 * silence.
 *
 * A browser with no voices at all speaks nothing and this drives nothing: the
 * caller's `done` runs, the mouth stays closed, and the answer is read on
 * screen. That is a real outcome for a shopkeeper on an old Android, and it is
 * not an error.
 */

import { type Cue, type Viseme, estimateUtterance, scheduleWord, visemesForWord, wordAt, wordSpans, CPS } from './visemes';

/* ==========================================================================
 * 1. Pace
 * ======================================================================== */

/**
 * Milliseconds per character, from what the engine has actually done so far.
 *
 * A plain average over every boundary would be dragged around by the long pause
 * an engine takes at a comma, so the estimate is the median of the recent
 * per-character costs. Median, not mean, for exactly one reason: one 900 ms
 * pause in a sample of six should not double the estimate for the next word.
 *
 * PURE, and exported, because "how fast is this voice" is the number that
 * decides whether the mouth looks in time, and it deserves a test that does not
 * involve a loudspeaker.
 */
export function paceFrom(samples: ReadonlyArray<number>, fallback = 1000 / CPS): number {
  const ok = samples.filter((n) => Number.isFinite(n) && n > 4 && n < 400);
  if (ok.length === 0) return fallback;
  const sorted = [...ok].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  const median = sorted.length % 2 === 1
    ? (sorted[mid] ?? fallback)
    : ((sorted[mid - 1] ?? fallback) + (sorted[mid] ?? fallback)) / 2;
  // Bounded either side. A single wild sample cannot make the mouth crawl or
  // strobe; the bounds are about three times either side of every real voice
  // measured on this machine.
  return Math.min(Math.max(median, 18), 190);
}

/** Sentence-sized pieces. Chrome truncates a single long utterance mid-word. */
export function chunks(text: string, max = 180): string[] {
  const parts = text.split(/(?<=[.!?।])\s+/).filter((p) => p.trim() !== '');
  const out: string[] = [];
  let cur = '';
  for (const p of parts) {
    if ((cur + ' ' + p).trim().length > max && cur) { out.push(cur.trim()); cur = p; }
    else cur = `${cur} ${p}`;
  }
  if (cur.trim()) out.push(cur.trim());
  const kept = out.filter((s) => s !== '');
  return kept.length ? kept : (text.trim() ? [text.trim()] : []);
}

/* ==========================================================================
 * 2. The director
 * ======================================================================== */

export interface SpeakHandle {
  /** Stop at once. Safe to call twice, and safe after it has finished. */
  cancel(): void;
}

export interface DirectorEvents {
  /** A new mouth shape. Called only while the engine is actually speaking. */
  viseme(v: Viseme): void;
  /**
   * Which word of the whole answer is being said, as an index into `words`.
   * -1 before the first and after the last. Drives the caption under the
   * presenter, which is the part a shopkeeper can check the sync against.
   */
  word(index: number): void;
  /** Speaking started / stopped, for the state chip and the rest of the page. */
  speaking(on: boolean): void;
}

export interface SpeakRequest {
  text: string;
  voice: SpeechSynthesisVoice | null;
  rate?: number;
  pitch?: number;
  /** Called once, when the whole answer has been said or abandoned. */
  done: () => void;
}

/**
 * A sentence that arrives as AUDIO — a natural voice the server fetched and
 * cached — rather than as text for the browser to synthesise.
 *
 * The clock is different and better. `speechSynthesis` reveals its timing one
 * `boundary` at a time; an <audio> element carries `currentTime`, which is the
 * exact position of the sound, every frame, with no dead reckoning at all. So
 * the schedule is the whole-utterance estimate scaled to the file's real
 * duration, and the mouth reads the audio clock rather than a stopwatch. It
 * can still be wrong INSIDE a sentence — the estimate does not know where the
 * voice paused — but it starts and stops with the sound to the frame.
 */
export interface AudioSpeakRequest {
  text: string;
  /** A SAME-ORIGIN URL. Not a blob: one — the till's CSP has no media-src, so
      `default-src 'self'` refuses a blob and the answer goes silent — and
      never a foreign host. */
  url: string;
  /**
   * Called exactly once. 'played' when the sound reached its end; 'failed'
   * when the browser would not play it, the file errored, or nothing was
   * given — so the caller can fall back to the browser's own voice rather
   * than leave a sentence unsaid.
   */
  done: (outcome: 'played' | 'failed') => void;
}

/** The slice of HTMLAudioElement this file touches, so a test can fake one. */
export interface AudioLike {
  src: string;
  readonly duration: number;
  readonly paused: boolean;
  readonly ended: boolean;
  currentTime: number;
  play(): Promise<void> | void;
  pause(): void;
  addEventListener(type: string, fn: () => void): void;
  removeEventListener(type: string, fn: () => void): void;
}
export type AudioFactory = (url: string) => AudioLike;

const defaultAudio: AudioFactory = (url) => new Audio(url);

/**
 * How long after `onstart` we wait for a first `boundary` before deciding this
 * engine does not send them. Chromium's first boundary lands within ~60 ms of
 * start; 400 ms is generous enough not to misfire and short enough that the
 * fallback catches the top of the sentence.
 */
const BOUNDARY_GRACE = 400;

/**
 * The mouth is closed if no boundary has arrived for this long AND the schedule
 * has run out. Guards against an engine that stalls without firing `onend`.
 */
const STALL = 1200;

/**
 * Owns one spoken answer: the utterances, the cue schedule, and the frame loop
 * that reads it. One at a time — a second `speak` cancels the first, because
 * two answers over each other is not a thing a counter should ever do.
 */
export class SpeechDirector {
  private synth: SpeechSynthesis | null;
  private on: DirectorEvents;

  /** The whole answer, and the word spans a boundary indexes into. */
  private words: ReturnType<typeof wordSpans> = [];
  /** Where each chunk starts inside the whole answer. */
  private chunkOffset: number[] = [];

  private cues: Cue[] = [];
  private cueBase = 0;          // performance.now() at cue index 0
  private lastViseme: Viseme = 'sil';
  private lastWord = -1;
  private raf = 0;
  private live = false;
  private token = 0;            // invalidates callbacks from a cancelled run
  private samples: number[] = [];
  private lastBoundary = { at: 0, char: 0 };
  private sawBoundary = false;
  private startedAt = 0;
  /** Set while a fetched voice is playing; the frame loop reads ITS clock. */
  private audio: AudioLike | null = null;
  private audioText = '';
  private audioMs = 0;

  constructor(synth: SpeechSynthesis | null, on: DirectorEvents) {
    this.synth = synth;
    this.on = on;
  }

  get speaking(): boolean { return this.live; }

  /**
   * Say it, move the mouth, and call `done` exactly once.
   *
   * `done` runs even when nothing can be spoken — no synthesiser, no voice, the
   * speaker switched off, an empty string. The advisor reopens its microphone in
   * that callback, so a path that never calls it is a call that goes deaf.
   */
  speak(req: SpeakRequest): SpeakHandle {
    this.cancel();
    const token = ++this.token;
    const text = (req.text ?? '').trim();
    const pieces = chunks(text);

    if (!this.synth || !req.voice || pieces.length === 0) {
      req.done();
      return { cancel: () => {} };
    }

    // Word spans over the WHOLE answer, so the caption highlight and the
    // schedule share one index space across sentence boundaries.
    this.words = wordSpans(text);
    this.chunkOffset = [];
    let cursor = 0;
    for (const piece of pieces) {
      const at = text.indexOf(piece, cursor);
      const off = at >= 0 ? at : cursor;
      this.chunkOffset.push(off);
      cursor = off + piece.length;
    }

    this.samples = [];
    this.sawBoundary = false;
    this.cues = [];
    this.lastWord = -1;
    this.emitWord(-1);

    let left = pieces.length;
    const finish = () => {
      if (token !== this.token) return;
      if (--left > 0) return;
      this.stopMouth();
      req.done();
    };

    const synth = this.synth;
    // Chrome keeps a cancelled queue's `speaking` flag set for a beat; cancel
    // before queueing or the first utterance can be swallowed.
    synth.cancel();

    pieces.forEach((piece, ci) => {
      const u = new SpeechSynthesisUtterance(piece);
      u.voice = req.voice;
      u.lang = req.voice?.lang ?? 'en-IN';
      u.rate = req.rate ?? 1;
      u.pitch = req.pitch ?? 1;
      const base = this.chunkOffset[ci] ?? 0;

      u.onstart = () => {
        if (token !== this.token) return;
        this.startMouth();
        this.startedAt = now();
        // Nothing said yet, so nothing is scheduled; if this engine sends
        // boundaries the first one lands before the grace runs out and this
        // fallback never gets used.
        window.setTimeout(() => {
          if (token !== this.token || this.sawBoundary || !this.live) return;
          this.setCues(
            estimateUtterance(piece, u.rate).map((c) => ({ ...c })),
            this.startedAt,
            base,
          );
        }, BOUNDARY_GRACE);
      };

      u.onboundary = (e: SpeechSynthesisEvent) => {
        if (token !== this.token) return;
        this.sawBoundary = true;
        this.startMouth();
        this.onBoundary(base, e.charIndex ?? 0, u.rate);
      };

      u.onend = finish;
      u.onerror = finish;
      synth.speak(u);
    });

    return { cancel: () => { if (token === this.token) this.cancel(); } };
  }

  /**
   * Say a sentence that arrived as audio, move the mouth on its clock, and call
   * `done` exactly once — including when the browser refuses to play (autoplay
   * policy on a page nobody has touched), because the advisor reopens its
   * microphone in that callback.
   */
  speakAudio(req: AudioSpeakRequest, makeAudio: AudioFactory = defaultAudio): SpeakHandle {
    this.cancel();
    const token = ++this.token;
    const text = (req.text ?? '').trim();
    if (!req.url || !text) {
      req.done('failed');
      return { cancel: () => {} };
    }

    this.words = wordSpans(text);
    this.samples = [];
    this.sawBoundary = false;
    this.cues = [];
    this.lastWord = -1;
    this.emitWord(-1);

    let audio: AudioLike;
    try {
      audio = makeAudio(req.url);
    } catch {
      req.done('failed');
      return { cancel: () => {} };
    }
    this.audio = audio;
    this.audioText = text;
    this.audioMs = 0;

    let finished = false;
    let started = false;
    const finish = (outcome: 'played' | 'failed') => {
      if (finished) return;
      finished = true;
      unhook();
      if (token !== this.token) return;
      this.releaseAudio();
      this.stopMouth();
      req.done(outcome);
    };

    const onPlaying = () => {
      if (token !== this.token) return;
      // The real length is known now. The estimate is laid out at reading
      // speed and then STRETCHED to fit it, so the last shape lands on the
      // last sound rather than a guess at it.
      const est = estimateUtterance(text, 1);
      const estMs = est.length ? (est[est.length - 1]?.at ?? 0) : 0;
      const real = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration * 1000 : estMs;
      this.audioMs = real;
      const k = estMs > 0 ? real / estMs : 1;
      this.cues = est.map((c) => ({ at: Math.round(c.at * k), v: c.v }));
      this.cueBase = now();
      started = true;
      this.startMouth();
    };
    const onEnded = () => finish(started ? 'played' : 'failed');
    const onError = () => finish('failed');

    const unhook = () => {
      audio.removeEventListener('playing', onPlaying);
      audio.removeEventListener('ended', onEnded);
      audio.removeEventListener('error', onError);
    };
    audio.addEventListener('playing', onPlaying);
    audio.addEventListener('ended', onEnded);
    audio.addEventListener('error', onError);

    try {
      const p = audio.play();
      if (p && typeof (p as Promise<void>).then === 'function') {
        (p as Promise<void>).catch(() => finish('failed'));
      }
    } catch {
      finish('failed');
    }

    return { cancel: () => { if (token === this.token) this.cancel(); } };
  }

  private releaseAudio(): void {
    const a = this.audio;
    if (!a) return;
    this.audio = null;
    this.audioText = '';
    this.audioMs = 0;
    try { a.pause(); } catch { /* already gone */ }
    // A blob: URL keeps its bytes alive while something references it.
    try { a.src = ''; } catch { /* a fake may make src read-only */ }
  }

  /** Stop the voice and close the mouth. Idempotent. */
  cancel(): void {
    this.token += 1;
    if (this.synth) this.synth.cancel();
    this.releaseAudio();
    this.stopMouth();
  }

  /* ---- the schedule ---------------------------------------------------- */

  private onBoundary(base: number, charIndex: number, rate: number): void {
    const at = now();
    const absolute = base + charIndex;

    // Pace: how long the engine took over the characters between this boundary
    // and the last one. Only recorded inside one chunk — the gap between two
    // utterances is queue time, not speech.
    if (this.lastBoundary.at > 0 && absolute > this.lastBoundary.char) {
      const chars = absolute - this.lastBoundary.char;
      this.samples.push((at - this.lastBoundary.at) / chars);
      if (this.samples.length > 9) this.samples.shift();
    }
    this.lastBoundary = { at, char: absolute };

    const idx = wordAt(this.words, absolute);
    const span = this.words[idx];
    if (!span) return;
    this.emitWord(idx);

    // How long this word will last, at the pace measured so far. Divided by the
    // rate only through the fallback, because a measured pace already includes
    // whatever rate the engine is running at.
    const perChar = paceFrom(this.samples, 1000 / (CPS * (rate || 1)));
    const dur = Math.max((span.end - span.start) * perChar, 90);

    const cues = scheduleWord(visemesForWord(span.text), dur);
    // The mouth closes at the end of the word and stays closed until the next
    // boundary arrives. If the next word follows immediately the closure is
    // replaced before it is ever drawn; if the engine has hit a comma, the
    // closure is exactly right.
    cues.push({ at: Math.round(dur), v: 'sil' });
    this.setCues(cues, at, base);
  }

  private setCues(cues: Cue[], base: number, _offset: number): void {
    this.cues = cues;
    this.cueBase = base;
    this.startMouth();
  }

  /* ---- the frame loop --------------------------------------------------- */

  private startMouth(): void {
    if (!this.live) {
      this.live = true;
      this.on.speaking(true);
    }
    if (this.raf === 0) this.raf = requestAnimationFrame(this.tick);
  }

  private stopMouth(): void {
    if (this.raf !== 0) { cancelAnimationFrame(this.raf); this.raf = 0; }
    this.cues = [];
    this.lastBoundary = { at: 0, char: 0 };
    if (this.lastViseme !== 'sil') { this.lastViseme = 'sil'; this.on.viseme('sil'); }
    this.emitWord(-1);
    if (this.live) { this.live = false; this.on.speaking(false); }
  }

  private emitWord(i: number): void {
    if (i === this.lastWord) return;
    this.lastWord = i;
    this.on.word(i);
  }

  private tick = (): void => {
    this.raf = 0;
    if (!this.live) return;

    // A fetched voice has an exact clock; the browser's voice has a stopwatch
    // started at the last boundary. Read whichever is playing.
    const a = this.audio;
    const t = a ? a.currentTime * 1000 : now() - this.cueBase;
    let v: Viseme = 'sil';
    for (const cue of this.cues) {
      if (cue.at <= t) v = cue.v; else break;
    }

    if (a && this.audioMs > 0 && this.audioText) {
      // The caption's lit word, from the audio position. Proportional to
      // characters — the same assumption the schedule makes — so the two
      // never disagree with each other, only, sometimes, with the voice.
      const char = Math.min(this.audioText.length, Math.floor((t / this.audioMs) * this.audioText.length));
      this.emitWord(a.ended ? -1 : wordAt(this.words, char));
    }

    // The watchdog. If the engine has gone quiet without telling us — a
    // backgrounded tab, a killed voice service — the mouth closes rather than
    // holding the last shape forever.
    const quiet = a
      ? (a.paused || a.ended)
      : (this.synth ? !this.synth.speaking && !this.synth.pending : true);
    const stalled = !a && this.lastBoundary.at > 0 && now() - this.lastBoundary.at > STALL;
    if (quiet || stalled) v = 'sil';

    if (v !== this.lastViseme) { this.lastViseme = v; this.on.viseme(v); }
    this.raf = requestAnimationFrame(this.tick);
  };
}

const now = () =>
  (typeof performance !== 'undefined' && typeof performance.now === 'function')
    ? performance.now()
    : Date.now();
