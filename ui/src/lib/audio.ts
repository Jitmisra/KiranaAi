/**
 * The counter's three voices. Synthesised, never a file: the CSP forbids
 * external assets, and a till that depends on a CDN for its beep is a till that
 * goes silent on a bad day.
 *
 *   NAMED    two rising notes, bright, short      — something was priced
 *   ABSTAIN  one low note that does NOT resolve   — I do not know
 *   DUP      a single muted tap                   — already on the bill
 *   PAID     a four-note rise                     — a webhook settled it
 *
 * The abstain is deliberately the LONGEST and is not pleasant. A refusal that
 * sounds like a success trains a shopkeeper to stop hearing the difference,
 * and the whole product rests on that difference being audible across a shop.
 */

/** One source for the loudness contract; the assertion below reads THESE. */
/**
 * Loud enough for a shop.
 *
 * The first values were tuned on headphones in a quiet room and were inaudible
 * over a ceiling fan and a television, which is the actual acoustic environment
 * this product runs in. Roughly doubled, and the ORDER is preserved: the
 * abstention is still the loudest thing the counter can say.
 */
export const PEAK = {
  NAMED: 0.28,
  NAMED_2: 0.24,
  ABSTAIN: 0.36,
  ABSTAIN_2: 0.30,
  DUP: 0.14,
  PAID: 0.30,
} as const;

/**
 * An abstention must never be quieter than a success.
 *
 * Asserted over the constants the voices actually use. An earlier version
 * compared two hand-copied literals sitting next to the code they claimed to
 * guard — a mutation test proved it would pass unchanged with the abstain
 * silenced sixteenfold.
 */
export function loudnessContractHolds(): boolean {
  return Math.max(PEAK.ABSTAIN, PEAK.ABSTAIN_2) >= Math.max(PEAK.NAMED, PEAK.NAMED_2);
}

type Ctx = AudioContext | null;

export class Voices {
  private ac: Ctx = null;
  muted = false;

  private boot(): Ctx {
    if (this.ac) return this.ac;
    try {
      const C = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      this.ac = new C();
    } catch {
      this.ac = null; // a till with no audio still counts; it just counts quietly
    }
    return this.ac;
  }

  /** Browsers only allow audio after a gesture — call this from the START click. */
  unlock(): void {
    const ac = this.boot();
    if (ac && ac.state === 'suspended') void ac.resume();
  }

  /**
   * Arm on the FIRST touch of the page, whatever it is.
   *
   * Unlocking only from START CAMERA meant that any other route in — teaching
   * first, arriving with the camera already live, or simply clicking something
   * else — left the context suspended and the counter silent, with nothing on
   * screen to say why.
   */
  armOnFirstGesture(): void {
    if (typeof document === 'undefined') return;
    const arm = () => {
      this.unlock();
      for (const ev of ['pointerdown', 'keydown', 'touchstart']) {
        document.removeEventListener(ev, arm);
      }
    };
    for (const ev of ['pointerdown', 'keydown', 'touchstart']) {
      document.addEventListener(ev, arm, { once: false, passive: true });
    }
  }

  /** Is the audio engine actually able to make a sound right now? */
  get status(): 'ready' | 'blocked' | 'muted' | 'unavailable' {
    if (this.muted) return 'muted';
    if (!this.ac) return 'blocked';
    if (this.ac.state !== 'running') return 'blocked';
    return 'ready';
  }

  /** Play the three voices in order, so an operator can hear the difference. */
  demo(): void {
    this.unlock();
    this.named(0);
    const ac = this.ac;
    if (!ac) return;
    setTimeout(() => this.abstain(), 500);
    setTimeout(() => this.dup(), 1100);
  }

  private voice(freq: number, t0: number, dur: number, peak: number, type: OscillatorType = 'sine'): void {
    const ac = this.ac;
    if (!ac || this.muted) return;
    const o = ac.createOscillator();
    const g = ac.createGain();
    o.type = type;
    o.frequency.setValueAtTime(freq, t0);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(peak, t0 + 0.008);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    o.connect(g);
    g.connect(ac.destination);
    o.start(t0);
    o.stop(t0 + dur + 0.02);
  }

  /** `i` staggers a multi-code arpeggio, so a basket of four sounds like four. */
  named(i = 0): void {
    const ac = this.boot();
    if (!ac) return;
    const t = ac.currentTime + i * 0.13;
    const s = Math.pow(2, i / 6);
    // Slightly longer than the original 85/110 ms: a click that short reads as
    // a UI tick rather than as "that packet is on the bill".
    this.voice(880 * s, t, 0.13, PEAK.NAMED, 'square');
    this.voice(1320 * s, t + 0.075, 0.17, PEAK.NAMED_2, 'square');
  }

  abstain(): void {
    const ac = this.boot();
    if (!ac) return;
    const t = ac.currentTime;
    this.voice(196, t, 0.3, PEAK.ABSTAIN, 'sawtooth');
    this.voice(185, t + 0.16, 0.34, PEAK.ABSTAIN_2, 'sawtooth'); // does not resolve, on purpose
  }

  dup(): void {
    const ac = this.boot();
    if (!ac) return;
    this.voice(520, ac.currentTime, 0.05, PEAK.DUP, 'sine');
  }

  paid(): void {
    const ac = this.boot();
    if (!ac) return;
    const t = ac.currentTime;
    [523.25, 659.25, 783.99, 1046.5].forEach((f, i) => this.voice(f, t + i * 0.075, 0.34, PEAK.PAID, 'sine'));
  }
}

export const voices = new Voices();
