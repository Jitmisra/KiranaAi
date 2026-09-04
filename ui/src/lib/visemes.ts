/**
 * VISEMES — the mouth shapes a sentence needs, and when it needs them.
 *
 * The advisor speaks through the browser's own `speechSynthesis`. That API will
 * not hand out its audio: there is no AudioNode to tap, no waveform to measure,
 * no amplitude to drive a jaw with. What it WILL hand out is a `boundary` event
 * per word, carrying the character index it has just reached and how long it has
 * been speaking. That is the only honest timing source on this page, and this
 * file turns it into mouth shapes.
 *
 * THE RULE THIS FILE EXISTS TO KEEP: a mouth that moves while the browser is
 * silent is a lie, and a worse one than no mouth at all. So nothing here loops.
 * Every shape is scheduled against a word the synthesiser has told us it has
 * reached, and the schedule runs out when the words do.
 *
 * PURE. No DOM, no timers, no audio. The component owns the clock; this file
 * owns the linguistics, and the linguistics are the part worth testing without
 * a browser.
 *
 * THE VISEME SET is the Oculus/Meta fifteen — the set nearly every rigged avatar
 * ships blendshapes for, so a future 3D head can be driven from exactly this
 * output without changing a line of it:
 *
 *   sil  closed, at rest       aa  open, "father"
 *   PP   lips pressed, p/b/m   E   half-open spread, "bed"
 *   FF   lip on teeth, f/v     I   narrow spread, "see"
 *   TH   tongue to teeth       O   rounded, "go"
 *   DD   tongue to ridge       U   tight round, "boot"
 *   kk   back of tongue
 *   CH   pushed forward, sh/ch
 *   SS   narrow, teeth close
 *   nn   tongue up, n/l
 *   RR   rounded-ish, r
 *
 * The last five are spelled the way rigged avatars name their morph targets
 * (`viseme_aa`, `viseme_E`, `viseme_I`, `viseme_O`, `viseme_U`); Meta's own
 * documentation calls the same five shapes `aa, E, ih, oh, ou`. Same set, two
 * spellings — worth knowing if this output is ever pointed at a 3D head.
 *
 * THREE SCRIPTS, because this counter speaks to a shopkeeper in Hindi, Bengali
 * or English and the speech engine returns whichever the voice was set to.
 * Devanagari and Bengali are abugidas — a bare consonant carries its own vowel —
 * so they cannot be read with the Latin table, and reading them with it produced
 * a mouth that sat on `sil` through an entire Hindi sentence.
 *
 * THREE RULES THAT ARE EASY TO GET WRONG, and each one visibly wrong on a face:
 *
 *  1. ASPIRATION IS INVISIBLE. क and ख are the same mouth. The one that matters
 *     is फ — it is an aspirated P, NOT an F, and reading it as F puts a
 *     lip-on-teeth shape in the middle of "फल". Only फ़, with the nukta, is /f/.
 *  2. BENGALI'S INHERENT VOWEL IS /ɔ/, NOT /ə/. A bare Bengali consonant is
 *     rounded, so the inherent vowel is `O` and not `aa`. Borrowing the
 *     Devanagari value here makes every Bengali word the wrong shape.
 *  3. BENGALI HAS NO /v/ AND NO /w/. ব and ভ are both `PP`. And শ, ষ and স are
 *     all commonly /ʃ/, so all three are `CH` rather than `SS`.
 */

/* ==========================================================================
 * 1. The set
 * ======================================================================== */

export const VISEMES = [
  'sil', 'PP', 'FF', 'TH', 'DD', 'kk', 'CH', 'SS', 'nn', 'RR',
  'aa', 'E', 'I', 'O', 'U',
] as const;

export type Viseme = (typeof VISEMES)[number];

/** One shape, and how much of the word's time it deserves. */
export interface VisemeStep {
  v: Viseme;
  /**
   * Relative duration. Not milliseconds — the word's real duration is not known
   * until the synthesiser reaches the next word, so shapes are laid out in
   * proportion and scaled when the time arrives.
   */
  w: number;
}

/**
 * How long each shape wants to be held, relative to the others.
 *
 * Vowels carry a syllable and are seen; plosives are a flick of the lips and are
 * over before the eye settles. Weighting them equally gave a mouth that chewed
 * evenly through a word instead of pulsing on its vowels, which is the single
 * thing that reads as "not really talking".
 */
const WEIGHT: Record<Viseme, number> = {
  sil: 1,
  PP: 0.7, FF: 0.9, TH: 0.9, DD: 0.7, kk: 0.7, CH: 1.0, SS: 1.1, nn: 0.8, RR: 0.9,
  aa: 1.8, E: 1.5, I: 1.4, O: 1.6, U: 1.5,
};

/* ==========================================================================
 * 2. Latin — English, and Hinglish typed in roman letters
 * ======================================================================== */

/**
 * Digraphs first, longest first, because "ph" is one sound and reading it as
 * p-then-h puts a lip press in the middle of "phone".
 */
const LATIN_MULTI: Array<[string, Viseme[]]> = [
  ['tch', ['CH']],
  ['sch', ['SS', 'kk']],
  ['ough', ['O']],
  ['tion', ['SS', 'O', 'nn']],
  ['ch', ['CH']],
  ['sh', ['CH']],
  ['th', ['TH']],
  ['ph', ['FF']],
  ['gh', ['kk']],
  ['ck', ['kk']],
  ['ng', ['nn']],
  ['nk', ['nn', 'kk']],
  ['qu', ['kk', 'U']],
  ['wh', ['U']],
  ['wr', ['RR']],
  ['kn', ['nn']],
  ['ee', ['I']],
  ['ea', ['I']],
  ['ie', ['I']],
  ['oo', ['U']],
  ['ou', ['O', 'U']],
  ['ow', ['O', 'U']],
  ['oa', ['O']],
  ['oi', ['O', 'I']],
  ['oy', ['O', 'I']],
  ['au', ['O']],
  ['aw', ['O']],
  ['ai', ['E', 'I']],
  ['ay', ['E', 'I']],
  ['ei', ['E', 'I']],
  ['ey', ['E', 'I']],
  ['aa', ['aa']],
  ['ii', ['I']],
  ['uu', ['U']],
];

const LATIN_ONE: Record<string, Viseme> = {
  a: 'aa', e: 'E', i: 'I', o: 'O', u: 'U', y: 'I',
  b: 'PP', p: 'PP', m: 'PP',
  f: 'FF', v: 'FF', w: 'U',
  t: 'DD', d: 'DD',
  k: 'kk', g: 'kk', c: 'kk', q: 'kk', x: 'kk',
  j: 'CH',
  s: 'SS', z: 'SS',
  n: 'nn', l: 'nn',
  r: 'RR',
  h: 'aa',
  // Digits get said out loud by every engine on this page, and a bill read
  // back as "one hundred and forty" is the sentence a shopkeeper listens
  // hardest to. These are the shapes of the English words, roughly.
  '0': 'O', '1': 'U', '2': 'U', '3': 'I', '4': 'O',
  '5': 'FF', '6': 'SS', '7': 'SS', '8': 'E', '9': 'nn',
};

/* ==========================================================================
 * 3. Devanagari and Bengali
 * ========================================================================
 *
 * Both are abugidas: a bare consonant already carries the vowel /a/, a MATRA
 * replaces that vowel, and the VIRAMA deletes it. So a consonant is read as
 * "consonant shape, then a vowel shape" unless the next character says
 * otherwise — which is why these need their own scanner rather than a lookup.
 */

/**
 * Consonants, by the place the mouth makes them.
 *
 * Aspirated pairs are collapsed onto their unaspirated partner, because the
 * difference is a puff of air and the eye cannot see it. The one entry that is
 * NOT what it looks like is फ: an aspirated P, so `PP`. Its /f/ reading needs
 * the nukta, and that is handled by `NUKTA` below.
 */
const INDIC_CONSONANT: Record<string, Viseme> = {
  // ---- Devanagari -------------------------------------------------------
  'क': 'kk', 'ख': 'kk', 'ग': 'kk', 'घ': 'kk', 'ङ': 'nn',
  'च': 'CH', 'छ': 'CH', 'ज': 'CH', 'झ': 'CH', 'ञ': 'nn',
  // Retroflex: the tongue curls back to the ridge and nothing shows at the
  // teeth — `DD`. Dental त थ द ध put the tongue tip ON the teeth, which is
  // visible, and that is `TH`.
  'ट': 'DD', 'ठ': 'DD', 'ड': 'DD', 'ढ': 'DD', 'ण': 'nn',
  'त': 'TH', 'थ': 'TH', 'द': 'TH', 'ध': 'TH', 'न': 'nn',
  'प': 'PP', 'फ': 'PP', 'ब': 'PP', 'भ': 'PP', 'म': 'PP',
  'य': 'I', 'र': 'RR', 'ल': 'nn', 'व': 'FF',
  'श': 'CH', 'ष': 'CH', 'स': 'SS', 'ह': 'aa',
  // Precomposed nukta forms. The decomposed spellings reach the same values
  // through `NUKTA`; both exist in the wild and an engine may return either.
  'क़': 'kk', 'ख़': 'kk', 'ग़': 'kk', 'ज़': 'SS', 'फ़': 'FF', 'ड़': 'RR', 'ढ़': 'RR',
  // ---- Bengali ----------------------------------------------------------
  'ক': 'kk', 'খ': 'kk', 'গ': 'kk', 'ঘ': 'kk', 'ঙ': 'nn',
  'চ': 'CH', 'ছ': 'CH', 'জ': 'CH', 'ঝ': 'CH', 'ঞ': 'nn',
  'ট': 'DD', 'ঠ': 'DD', 'ড': 'DD', 'ঢ': 'DD', 'ণ': 'nn',
  'ত': 'TH', 'থ': 'TH', 'দ': 'TH', 'ধ': 'TH', 'ন': 'nn',
  // No /v/ and no /w/ in Bengali: ব and ভ are both a lip press.
  'প': 'PP', 'ফ': 'PP', 'ব': 'PP', 'ভ': 'PP', 'ম': 'PP',
  'য': 'CH', 'র': 'RR', 'ল': 'nn', 'য়': 'I', 'ড়': 'RR', 'ঢ়': 'RR',
  // All three sibilants are commonly /ʃ/, so all three push the lips forward.
  'শ': 'CH', 'ষ': 'CH', 'স': 'CH', 'হ': 'aa',
};

/** What a nukta does to the consonant in front of it, when it does anything. */
const NUKTA: Record<string, Viseme> = {
  'फ': 'FF', 'ज': 'SS', 'ड': 'RR', 'ঢ': 'RR', 'ড': 'RR', 'য': 'I',
};

/** Independent vowels — the ones that start a word. */
const INDIC_VOWEL: Record<string, Viseme> = {
  'अ': 'aa', 'आ': 'aa', 'इ': 'I', 'ई': 'I', 'उ': 'U', 'ऊ': 'U',
  'ऋ': 'RR', 'ए': 'E', 'ऐ': 'E', 'ओ': 'O', 'औ': 'O',
  'অ': 'O', 'আ': 'aa', 'ই': 'I', 'ঈ': 'I', 'উ': 'U', 'ঊ': 'U',
  'ঋ': 'RR', 'এ': 'E', 'ঐ': 'O', 'ও': 'O', 'ঔ': 'O',
};

/** Matras — a vowel written onto the consonant before it. */
const INDIC_MATRA: Record<string, Viseme> = {
  'ा': 'aa', 'ि': 'I', 'ी': 'I', 'ु': 'U', 'ू': 'U', 'ृ': 'RR',
  'े': 'E', 'ै': 'E', 'ो': 'O', 'ौ': 'O',
  'া': 'aa', 'ি': 'I', 'ী': 'I', 'ু': 'U', 'ূ': 'U', 'ৃ': 'RR',
  'ে': 'E', 'ৈ': 'O', 'ো': 'O', 'ৌ': 'O',
};

/** The virama, both scripts: "no vowel after this consonant". */
const VIRAMA = new Set(['्', '্']);

/** Nasal and breath marks that get their own beat of the mouth. */
const INDIC_SIGN: Record<string, Viseme> = {
  'ं': 'nn', 'ँ': 'nn', 'ः': 'aa',   // anusvara, candrabindu, visarga
  'ং': 'nn', 'ঁ': 'nn', 'ঃ': 'aa',
  'ৎ': 'DD',                                    // Bengali khanda ta
};

/** Devanagari and Bengali digits, read as their spoken words would be. */
const INDIC_DIGIT: Record<string, Viseme> = {
  '०': 'SS', '१': 'E', '२': 'O', '३': 'I', '४': 'aa', '५': 'PP', '६': 'CH', '७': 'aa', '८': 'aa', '९': 'nn',
  '০': 'SS', '১': 'E', '২': 'O', '৩': 'I', '৪': 'aa', '৫': 'PP', '৬': 'CH', '৭': 'SS', '৮': 'aa', '৯': 'nn',
};

/**
 * The vowel a bare consonant carries when nothing overrides it.
 *
 * Devanagari's is /ə/ — an unremarkable half-open mouth. Bengali's is /ɔ/,
 * which is ROUNDED, and using Devanagari's value for it makes every Bengali
 * word the wrong shape from the first syllable.
 */
function inherentVowel(ch: string): Viseme {
  const cp = ch.codePointAt(0) ?? 0;
  return cp >= 0x0980 && cp <= 0x09FF ? 'O' : 'aa';
}

const isIndic = (ch: string) =>
  ch in INDIC_CONSONANT || ch in INDIC_VOWEL || ch in INDIC_MATRA
  || VIRAMA.has(ch) || ch in INDIC_SIGN || ch in INDIC_DIGIT;

/* ==========================================================================
 * 4. The scanner
 * ======================================================================== */

/**
 * Read one word into the shapes a mouth would make saying it.
 *
 * Not a phonemiser and it does not pretend to be one. It is a grapheme reader
 * with the digraphs that matter, and its job is to be RIGHT ABOUT THE SHAPE OF
 * THE MOUTH, which is a far coarser question than being right about the sound:
 * "th", "the" and "there" all park the tongue on the teeth, and the eye cannot
 * tell those apart at 24 frames a second. What the eye CAN tell is a closed
 * mouth on a vowel, and that is what this avoids.
 */
export function visemesForWord(word: string): VisemeStep[] {
  const raw: Viseme[] = [];
  const chars = [...(word ?? '')];
  let i = 0;

  while (i < chars.length) {
    const ch = chars[i] ?? '';

    // ---- Indic -----------------------------------------------------------
    if (isIndic(ch)) {
      const cons = INDIC_CONSONANT[ch];
      if (cons !== undefined) {
        // What follows decides whether this consonant carries a vowel — and,
        // if a nukta follows, what the consonant even is.
        let j = i + 1;
        let nukta = false;
        while (j < chars.length && (chars[j] === '़' || chars[j] === '়')) { nukta = true; j++; }
        raw.push((nukta ? NUKTA[ch] : undefined) ?? cons);
        const next = chars[j] ?? '';
        if (VIRAMA.has(next)) {
          i = j + 1;                       // no vowel: the cluster continues
          continue;
        }
        const matra = INDIC_MATRA[next];
        if (matra !== undefined) {
          raw.push(matra);
          i = j + 1;
          continue;
        }
        // Bare consonant: it carries its script's inherent vowel — except at
        // the end of a word, where Hindi and Bengali both delete it. "kitna" is
        // two open beats and not three, and a mouth that opens a third time at
        // the end of every word looks like it is chewing.
        const atEnd = j >= chars.length;
        if (!atEnd) raw.push(inherentVowel(ch));
        i = j;
        continue;
      }
      const vowel = INDIC_VOWEL[ch] ?? INDIC_MATRA[ch] ?? INDIC_SIGN[ch] ?? INDIC_DIGIT[ch];
      if (vowel !== undefined) raw.push(vowel);
      i += 1;
      continue;
    }

    // ---- Latin -----------------------------------------------------------
    const lower = ch.toLowerCase();
    let matched = false;
    if (/[a-z]/.test(lower)) {
      const rest = chars.slice(i).join('').toLowerCase();
      for (const [seq, out] of LATIN_MULTI) {
        if (rest.startsWith(seq)) {
          raw.push(...out);
          i += seq.length;
          matched = true;
          break;
        }
      }
    }
    if (matched) continue;

    const one = LATIN_ONE[lower];
    if (one !== undefined) raw.push(one);
    i += 1;
  }

  // A word with nothing readable in it — punctuation, an emoji, a currency sign
  // — still takes time to be skipped over, and the mouth should rest, not stall
  // on whatever it was holding.
  if (raw.length === 0) return [{ v: 'sil', w: WEIGHT.sil }];

  // Collapse repeats: "little" is not two separate tongue taps to the eye, and
  // re-hitting the same shape reads as a stutter. The held shape gets a little
  // longer instead, which is what actually happens in a mouth.
  const out: VisemeStep[] = [];
  for (const v of raw) {
    const last = out[out.length - 1];
    if (last && last.v === v) { last.w = Math.min(last.w * 1.5, WEIGHT[v] * 2); continue; }
    out.push({ v, w: WEIGHT[v] });
  }
  return out;
}

/* ==========================================================================
 * 5. Words, and where they sit in the sentence
 * ======================================================================== */

export interface WordSpan {
  /** Index of the first character, into the string handed to the synthesiser. */
  start: number;
  /** One past the last character. */
  end: number;
  text: string;
}

/**
 * Split a sentence the way a `boundary` event indexes it.
 *
 * Chromium reports `charIndex` against the ORIGINAL string, so the spans have
 * to be offsets into that string and not into a cleaned copy of it. A word here
 * is a run of letters, digits and the marks letters need — `\p{M}` included, or
 * every Devanagari matra becomes its own word and the mouth runs three times
 * too fast.
 */
export function wordSpans(text: string): WordSpan[] {
  const out: WordSpan[] = [];
  const re = /[\p{L}\p{N}\p{M}]+(?:['’‍][\p{L}\p{M}]+)*/gu;
  for (const m of (text ?? '').matchAll(re)) {
    const start = m.index ?? 0;
    out.push({ start, end: start + m[0].length, text: m[0] });
  }
  return out;
}

/**
 * The word a `boundary` event landed in or just before.
 *
 * Engines disagree about whether `charIndex` points at the first letter of the
 * word or at the whitespace in front of it, and Safari has been seen to report
 * the index of the word it has just FINISHED. Taking "the first word that has
 * not ended yet" is right under all three readings and degrades to the last
 * word rather than to nothing.
 */
export function wordAt(spans: ReadonlyArray<WordSpan>, charIndex: number): number {
  for (let i = 0; i < spans.length; i++) {
    const s = spans[i];
    if (s && charIndex < s.end) return i;
  }
  return spans.length - 1;
}

/* ==========================================================================
 * 6. The schedule
 * ======================================================================== */

export interface Cue {
  /** Milliseconds from the start of the word. */
  at: number;
  v: Viseme;
}

/**
 * How long a word's shapes should be held, given how long the word lasts.
 *
 * The duration is not known when the word starts — the synthesiser only reveals
 * it by reaching the NEXT word — so the caller passes its best estimate and the
 * shapes are laid out in proportion to their weights inside it. When the next
 * boundary arrives early the schedule is simply replaced; when it arrives late
 * the last shape is held, which is what a held vowel looks like anyway.
 *
 * A floor of `MIN_HOLD` per shape stops a fast engine from strobing: below
 * about 45 ms a shape is a flicker rather than a movement, and a row of
 * flickers reads as noise, not speech.
 */
export const MIN_HOLD = 45;

export function scheduleWord(steps: ReadonlyArray<VisemeStep>, durationMs: number): Cue[] {
  if (steps.length === 0) return [];
  const dur = Math.max(durationMs, MIN_HOLD);
  const total = steps.reduce((n, s) => n + Math.max(s.w, 0.01), 0);

  // Everything fits at its weighted size, or nothing does and they share evenly
  // at the floor. Two cases, no clamping loop: a mixed schedule where some
  // shapes are floored and others are not slides the vowels off their beat.
  const scale = dur / total;
  const floored = steps.some((s) => Math.max(s.w, 0.01) * scale < MIN_HOLD);
  const each = dur / steps.length;

  const cues: Cue[] = [];
  let t = 0;
  for (const s of steps) {
    cues.push({ at: Math.round(t), v: s.v });
    t += floored ? each : Math.max(s.w, 0.01) * scale;
  }
  return cues;
}

/**
 * A whole utterance laid out at a fixed reading speed.
 *
 * The fallback for an engine that never fires `boundary` — Safari with some
 * voices, and a handful of Android builds. It is honest about being an
 * estimate: it starts when `onstart` fires and it is CUT OFF by `onend`, so the
 * mouth still cannot move while the browser is silent. It only guesses the
 * shapes in between.
 *
 * `CPS` is characters per second at rate 1.0, measured against the en-IN and
 * hi-IN voices on this machine by timing `elapsedTime` at the last boundary of
 * a few hundred characters. It is deliberately a little fast: a mouth that
 * finishes a fraction early and rests is unremarkable, and one still moving
 * after the sound stops is the exact failure this file is written to avoid.
 */
export const CPS = 15.5;

export function estimateUtterance(text: string, rate = 1): Cue[] {
  const spans = wordSpans(text);
  if (spans.length === 0) return [];
  const perChar = 1000 / (CPS * (rate || 1));
  const cues: Cue[] = [];
  let t = 0;
  let cursor = 0;
  for (const span of spans) {
    // The gap in front of the word — spaces, commas — is mouth-closed time.
    const gap = (span.start - cursor) * perChar;
    if (gap > MIN_HOLD) { cues.push({ at: Math.round(t), v: 'sil' }); t += gap; }
    else t += gap;
    const dur = (span.end - span.start) * perChar;
    for (const cue of scheduleWord(visemesForWord(span.text), dur)) {
      cues.push({ at: Math.round(t + cue.at), v: cue.v });
    }
    t += dur;
    cursor = span.end;
  }
  cues.push({ at: Math.round(t), v: 'sil' });
  return cues;
}
