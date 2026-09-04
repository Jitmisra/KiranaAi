import { useEffect, useRef } from 'react';
import type { Viseme } from '../lib/visemes';
import plate from '../lib/presenter/advisor.webp';

/**
 * THE PRESENTER — a face for the advisor, and a mouth that moves with the words
 * the browser is actually saying.
 *
 * WHAT IT IS. One still portrait, 912x684, 19.4 kB of WebP, with a LIVE VECTOR
 * MOUTH drawn into it and eyelids drawn over it. The portrait was generated
 * once, ahead of time, and vendored; the mouth is code. Every frame is one
 * `requestAnimationFrame` writing a handful of attributes — React renders this
 * component once and never re-renders for a frame of animation, because a
 * setState at 60 Hz on a four-year-old Android is how a face becomes a
 * slideshow.
 *
 * The plate is served from this origin like any other asset. Nothing is
 * fetched from anywhere else, at any point. See
 * `ui/public/presenter/PROVENANCE.txt` for what it is, what was done to it and
 * where it came from.
 *
 * WHY THE PAINTED MOUTH WAS REMOVED FROM THE PLATE. An illustration comes with
 * a mouth on it. A drawn mouth on top of a painted one is two mouths, and the
 * eye finds that immediately. So the plate's mouth was inpainted out at build
 * time and the area is plain skin; the only mouth on this face is the one below,
 * and it is a shape this browser computes from the sentence being spoken.
 *
 * WHY NOT 3D. The page is served under
 *
 *     default-src 'self'; script-src 'self'; connect-src 'self'; frame-src 'none'
 *
 * `script-src 'self'` with no `'wasm-unsafe-eval'` means WebAssembly will not
 * compile, which rules out Live2D and every ONNX-based option outright. The
 * three.js routes (met4citizen/TalkingHead, @pixiv/three-vrm — both MIT, both
 * genuinely good) are clean under this CSP and would bundle, but the AVATAR is
 * 2.3–12 MB of GLB or VRM and three.js with GLTFLoader is another 158 kB
 * gzipped. That is six times this route's whole budget, on a shop's phone, for a
 * face that would still be a drawing. Twenty kilobytes and a rig gets a better
 * face onto a worse phone.
 *
 * WHY IT IS OBVIOUSLY DRAWN, ON PURPOSE. It is flat vector art and it reads as
 * flat vector art at any size — no photographic texture, no pores, no film
 * grain. It is a fictional character, not anyone's likeness, and it is not
 * trying to be taken for a real person on a video call. That is a product
 * requirement here and not an artistic one: a counter that lets a shopkeeper
 * believe a human being is watching their shop's figures has told its first lie.
 *
 * THE MOUTH is not a swap between fifteen pictures. It is five continuous
 * parameters — how open, how wide, how rounded, how much teeth, how much tongue
 * — that every frame are sprung towards the shape the current viseme wants. The
 * spring is why it reads as flesh: real lips overshoot slightly on an open vowel
 * and settle, and they never teleport. `lib/lipsync.ts` decides WHICH viseme;
 * this file decides what that looks like on this face. The `{open, wide, round}`
 * parameterisation follows the approach in Amoner/lipsync-engine (MIT), with
 * teeth and tongue added because at this size they are the difference between
 * "SS" and "I".
 *
 * MOTION, AND WHAT `prefers-reduced-motion` TURNS OFF. Three things move here
 * and the guard treats them differently, on purpose:
 *
 *   the drift    A slow camera-and-breathing wander of the whole frame. Pure
 *                atmosphere. STOPPED COMPLETELY under reduced motion.
 *   the blink    Slowed, not stopped. A face that never blinks stops reading as
 *                a face within about four seconds and starts reading as a
 *                photograph of one; what the guard protects against is movement
 *                that pulls the eye across the screen, and a 130 ms blink every
 *                five seconds is not that.
 *   the mouth    NEVER STOPPED. It is carrying information about what is being
 *                said out loud. A shopkeeper who has turned motion down still
 *                needs to see that the counter is mid-sentence, and the caption
 *                under the tile moves for the same reason.
 */

export type PresenterState = 'off' | 'idle' | 'listening' | 'thinking' | 'speaking';

export interface PresenterProps {
  state: PresenterState;
  /**
   * The shape the mouth should be moving towards, as a REF and not a value.
   *
   * `lib/lipsync.ts` changes this up to fifteen times a second. Passing it as a
   * prop would re-render the whole Advisor route — transcript, facts, rail — at
   * that rate, which on the phone this counter is aimed at is how a face becomes
   * a slideshow and a scroll position starts fighting back. The frame loop below
   * already runs; it reads the ref.
   */
  viseme: { readonly current: Viseme };
  /**
   * How loud the voice is RIGHT NOW, 0..1, as a ref for the same reason as
   * `viseme`. Fed from a Web Audio analyser on the natural voice; absent (or
   * zero) on the browser's own voice, where the mouth's openness stands in.
   * Drives the halo and the head, never the mouth — the mouth is the words.
   */
  amplitude?: { readonly current: number };
  className?: string;
}

/* ==========================================================================
 * 1. What each viseme does to a mouth
 * ========================================================================
 *
 * Five numbers, all 0..1, and between them they cover every shape a mouth makes
 * that a person can see from across a counter:
 *
 *   open    the jaw. 0 is lips touching, 1 is "aa".
 *   wide    the corners, pulled out for "ee" and in for "oo".
 *   round   protrusion. Rounds the corners in and pushes the lips forward.
 *   teeth   how much of the upper teeth shows.
 *   tongue  how far the tongue comes forward into the gap.
 *
 * The numbers come from the standard descriptions of the Oculus fifteen,
 * adjusted by eye at 1440 and again at 390 — a mouth tuned only at desktop size
 * loses every distinction except "open" once it is 90 px wide.
 */
export interface Mouth { open: number; wide: number; round: number; teeth: number; tongue: number }

const SHAPES: Record<Viseme, Mouth> = {
  sil: { open: 0.03, wide: 0.42, round: 0.10, teeth: 0.00, tongue: 0.00 },
  PP:  { open: 0.00, wide: 0.38, round: 0.18, teeth: 0.00, tongue: 0.00 },
  FF:  { open: 0.10, wide: 0.48, round: 0.04, teeth: 0.72, tongue: 0.00 },
  TH:  { open: 0.26, wide: 0.46, round: 0.04, teeth: 0.34, tongue: 0.90 },
  DD:  { open: 0.30, wide: 0.44, round: 0.04, teeth: 0.46, tongue: 0.44 },
  kk:  { open: 0.34, wide: 0.42, round: 0.10, teeth: 0.30, tongue: 0.14 },
  CH:  { open: 0.24, wide: 0.28, round: 0.58, teeth: 0.54, tongue: 0.22 },
  SS:  { open: 0.12, wide: 0.54, round: 0.02, teeth: 0.86, tongue: 0.00 },
  nn:  { open: 0.20, wide: 0.44, round: 0.05, teeth: 0.40, tongue: 0.62 },
  RR:  { open: 0.28, wide: 0.32, round: 0.46, teeth: 0.20, tongue: 0.32 },
  aa:  { open: 0.95, wide: 0.50, round: 0.06, teeth: 0.26, tongue: 0.22 },
  E:   { open: 0.54, wide: 0.64, round: 0.00, teeth: 0.46, tongue: 0.16 },
  I:   { open: 0.26, wide: 0.74, round: 0.00, teeth: 0.62, tongue: 0.10 },
  O:   { open: 0.64, wide: 0.22, round: 0.86, teeth: 0.10, tongue: 0.10 },
  U:   { open: 0.28, wide: 0.14, round: 1.00, teeth: 0.00, tongue: 0.00 },
};

/* ==========================================================================
 * 2. Where the face is
 * ========================================================================
 *
 * Measured off the plate at 4x with a 20 px grid, in the plate's own pixels.
 * The rig is drawn in the same coordinate space as the picture, which is the
 * only way a drawn mouth lands on a painted face and stays there at every
 * screen size.
 */
const W = 912;
const H = 684;

/** Where the mouth was, before it was painted out. */
const MOUTH_AT = { x: 459, y: 437 };
/**
 * The mouth is drawn in a local space about 2 units to the plate's 1, then
 * scaled. Keeping the shape maths in small numbers is what makes the constants
 * in `mouthPaths` readable; this is the one place the two spaces meet.
 *
 * 1.95 is not a taste: the plate's own lips ran 114 px wide and 54 px tall, and
 * this is the number that puts the rest shape exactly there.
 */
const MOUTH_SCALE = 1.95;

const EYES = [
  { id: 'l', x0: 349, x1: 420, cy: 308, top: 290, bot: 326 },
  { id: 'r', x0: 480, x1: 551, cy: 307, top: 289, bot: 325 },
] as const;

/** Round for an attribute. Two decimals; more is bytes with no pixels. */
const r2 = (n: number) => Math.round(n * 100) / 100;

/**
 * The lips, the gap, the teeth and the tongue, from the five parameters, in the
 * mouth's own local space centred on (0, 0).
 *
 * One function, called once a frame, returning strings that go straight onto
 * `d` attributes. It allocates a few short strings per frame, which is nothing
 * next to a layout pass — and it is the reason the mouth can be a continuous
 * morph rather than fifteen stills.
 */
export function mouthPaths(m: Mouth): {
  lips: string; gap: string; upperTeeth: string; lowerTeeth: string; tongue: string; press: number;
} {
  // Corners: pulled out by `wide`, dragged in by `round`.
  const halfW = 25 + 17 * m.wide - 15 * m.round;
  // The jaw. The lower lip travels about twice as far as the upper one, which
  // is what a hinge does, and what makes an open vowel read as a jaw dropping
  // rather than a hole appearing.
  const gapPx = 26 * m.open;
  // How far from shut. A mouth at rest must close COMPLETELY: leaving a
  // constant hairline of gap put a permanent white line of teeth between the
  // lips, which on a still face reads as a grimace.
  const ajar = Math.min(m.open * 9, 1);
  const innerTop = -gapPx * 0.36 - ajar;
  const innerBot = gapPx * 0.64 + ajar;
  // And teeth only exist once there is a mouth for them to be in. Below this
  // they are given zero height, which puts them outside the gap's clip
  // entirely — so a nearly-closed mouth is a DARK line, which is what a closed
  // mouth is, rather than a bright one.
  const showTeeth = Math.min(Math.max(m.open - 0.04, 0) * 10, 1);

  // The outer silhouette. The upper lip carries a cupid's bow; the lower is one
  // fuller curve. Both ride outward as the mouth opens.
  const lipTop = innerTop - 9.5 - 4 * m.round;
  const lipBot = innerBot + 12 + 3.5 * m.round;

  const x = (f: number) => r2(halfW * f);
  const y = (v: number) => r2(v);

  const lips =
    `M${x(-1)} 0`
    + `C${x(-0.64)} ${y(lipTop + 1)} ${x(-0.30)} ${y(lipTop)} ${x(-0.11)} ${y(lipTop + 2.6)}`
    + `L0 ${y(lipTop + 0.9)}`
    + `L${x(0.11)} ${y(lipTop + 2.6)}`
    + `C${x(0.30)} ${y(lipTop)} ${x(0.64)} ${y(lipTop + 1)} ${x(1)} 0`
    + `C${x(0.62)} ${y(lipBot)} ${x(-0.62)} ${y(lipBot)} ${x(-1)} 0Z`;

  // The gap. A lens: two quadratics whose control points are placed so each
  // curve passes exactly through the top and the bottom of the opening.
  const gw = halfW * 0.84;
  const gap =
    `M${r2(-gw)} 0Q0 ${y(2 * innerTop)} ${r2(gw)} 0Q0 ${y(2 * innerBot)} ${r2(-gw)} 0Z`;

  // Teeth hang from the top of the gap and rise from the bottom of it. Both are
  // clipped to the gap, so `teeth` only decides how far they come, never
  // whether they escape the mouth.
  const utH = (1.5 + 8 * m.teeth) * showTeeth;
  // The rounding at the biting edge is scaled by `showTeeth` as well. It has to
  // be: a quadratic bulges towards its control point, so with zero-height teeth
  // a fixed 2.4 of overshoot still put a bright crescent INSIDE the gap — which
  // is what a closed mouth looked like until this was found in a screenshot.
  const upperTeeth =
    `M${r2(-gw)} ${y(innerTop - 5)}H${r2(gw)}V${y(innerTop + utH)}`
    + `Q0 ${y(innerTop + utH + 2.4 * showTeeth)} ${r2(-gw)} ${y(innerTop + utH)}Z`;

  const ltH = (1 + 4.5 * m.teeth * Math.min(m.open * 2.2, 1)) * showTeeth;
  const lowerTeeth =
    `M${r2(-gw * 0.9)} ${y(innerBot + 5)}H${r2(gw * 0.9)}V${y(innerBot - ltH)}`
    + `Q0 ${y(innerBot - ltH - 1.6 * showTeeth)} ${r2(-gw * 0.9)} ${y(innerBot - ltH)}Z`;

  // The tongue sits at the floor of the mouth and comes forward, never up past
  // the teeth. `tongue` moves it; `open` decides whether there is anywhere for
  // it to be seen at all.
  const tw = gw * 0.72;
  const ty = innerBot - (2 + 8 * m.tongue) * Math.min(m.open * 2.6, 1);
  const tongue = `M${r2(-tw)} ${y(innerBot + 5)}Q0 ${y(ty - 6)} ${r2(tw)} ${y(innerBot + 5)}Z`;

  // How closed the lips are, for the seam and for the shadow under the jaw. An
  // open mouth has no seam, and drawing one there looks like a scar.
  const press = Math.max(0, 1 - m.open * 7);

  return { lips, gap, upperTeeth, lowerTeeth, tongue, press };
}

/* ==========================================================================
 * 3. The component
 * ======================================================================== */

interface Live {
  m: Mouth;
  blink: number;
  blinkAt: number;
  blinkPhase: number;
  t0: number;
  /** Head pose, sprung. Degrees of tilt, a lean-in scale, and a nod offset. */
  tilt: number;
  lean: number;
  nod: number;
  /** The voice's level, smoothed so a halo breathes rather than flickers. */
  level: number;
  /** When the state last changed, for the one-shot gestures. */
  stateAt: number;
  lastState: PresenterState;
}

const lerp = (a: number, b: number, k: number) => a + (b - a) * k;

export default function Presenter({ state, viseme, amplitude, className }: PresenterProps) {
  const root = useRef<SVGSVGElement>(null);
  const stateRef = useRef(state);
  useEffect(() => { stateRef.current = state; }, [state]);

  useEffect(() => {
    const svg = root.current;
    if (!svg) return;
    const pick = <T extends Element>(sel: string) => svg.querySelector<T>(sel);

    const drift = pick<SVGGElement>('.pp-drift');
    const lips = pick<SVGPathElement>('.pp-lips');
    const gap = pick<SVGPathElement>('.pp-gap');
    const gapClip = pick<SVGPathElement>('.pp-gap-clip');
    const upper = pick<SVGPathElement>('.pp-teeth-u');
    const lower = pick<SVGPathElement>('.pp-teeth-l');
    const tongue = pick<SVGPathElement>('.pp-tongue');
    const seam = pick<SVGPathElement>('.pp-seam');
    const jaw = pick<SVGEllipseElement>('.pp-jaw');
    const lidL = pick<SVGGElement>('.pp-lid-l');
    const lidR = pick<SVGGElement>('.pp-lid-r');
    if (!lips || !gap || !gapClip) return;

    const reduced = typeof matchMedia === 'function'
      && matchMedia('(prefers-reduced-motion: reduce)').matches;

    const now = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());
    const live: Live = {
      m: { ...SHAPES.sil },
      blink: 0, blinkAt: now() + 1500, blinkPhase: 0,
      t0: now(),
      tilt: 0, lean: 1, nod: 0, level: 0,
      stateAt: now(), lastState: stateRef.current,
    };
    const halo = pick<SVGEllipseElement>('.pp-halo');
    const host = svg.parentElement as HTMLElement | null;

    let raf = 0;
    let last = now();

    const frame = () => {
      const t = now();
      // Clamped: a backgrounded tab hands back a delta of several seconds, and
      // every spring below would fly apart on it.
      const dt = Math.min((t - last) / 1000, 0.05);
      last = t;
      const age = (t - live.t0) / 1000;
      const st = stateRef.current;

      /* ---- the mouth --------------------------------------------------- */
      const want = SHAPES[viseme.current] ?? SHAPES.sil;
      // Two speeds. Opening is fast — a jaw drops quickly, and a slow open makes
      // every consonant land late — and closing is slower, which is what lips do.
      const k = (a: number, b: number, fast: number, slow: number) =>
        1 - Math.exp(-(b > a ? fast : slow) * dt);
      live.m.open = lerp(live.m.open, want.open, k(live.m.open, want.open, 34, 22));
      live.m.wide = lerp(live.m.wide, want.wide, 1 - Math.exp(-20 * dt));
      live.m.round = lerp(live.m.round, want.round, 1 - Math.exp(-20 * dt));
      live.m.teeth = lerp(live.m.teeth, want.teeth, 1 - Math.exp(-24 * dt));
      live.m.tongue = lerp(live.m.tongue, want.tongue, 1 - Math.exp(-22 * dt));

      const p = mouthPaths(live.m);
      lips.setAttribute('d', p.lips);
      gap.setAttribute('d', p.gap);
      gapClip.setAttribute('d', p.gap);
      if (upper) upper.setAttribute('d', p.upperTeeth);
      if (lower) lower.setAttribute('d', p.lowerTeeth);
      if (tongue) {
        tongue.setAttribute('d', p.tongue);
        tongue.setAttribute('opacity', String(r2(Math.min(1, live.m.tongue * 1.4))));
      }
      if (seam) seam.setAttribute('opacity', String(r2(p.press * 0.42)));
      // The plate's chin cannot move, so an open mouth gets a deepening shadow
      // beneath it instead. It is a cheat and it is the difference between a
      // jaw dropping and a hole opening in a photograph.
      if (jaw) {
        jaw.setAttribute('opacity', String(r2(live.m.open * 0.20)));
        jaw.setAttribute('cy', String(r2(MOUTH_AT.y + 46 + live.m.open * 12)));
      }

      /* ---- blinking ----------------------------------------------------- */
      // Kept even under reduced motion at a slower cadence: a face that never
      // blinks at all stops reading as a face within about four seconds, and
      // what the guard is protecting against is movement that pulls the eye,
      // not the fact of being alive.
      if (live.blinkPhase > 0) {
        live.blinkPhase -= dt / 0.13;
        // Down fast, up slow: a real blink is not symmetric.
        const ph = 1 - Math.max(live.blinkPhase, 0);
        live.blink = ph < 0.35 ? ph / 0.35 : 1 - (ph - 0.35) / 0.65;
        if (live.blinkPhase <= 0) {
          live.blink = 0;
          // 2.4 s to 6.8 s. Regular blinking is uncanny — people blink in
          // clusters and then not at all for a while.
          live.blinkAt = t + (reduced ? 5200 : 2400) + Math.random() * 4400;
        }
      } else if (t > live.blinkAt) {
        live.blinkPhase = 1;
      }
      // 56 is how far the lid has to travel to clear the PADDED clip below —
      // the eye is 36 px tall, the clip is padded by 7 either way, and the lid
      // starts 4 px above it. A lid that only travels the height of the eye
      // leaves the padding uncovered, which is a crescent of face-coloured
      // nothing at the corner of a shut eye.
      const drop = live.blink * 56;
      if (lidL) lidL.setAttribute('transform', `translate(0 ${r2(drop)})`);
      if (lidR) lidR.setAttribute('transform', `translate(0 ${r2(drop)})`);

      /* ---- the level ------------------------------------------------------ */
      // Real audio when there is an analyser; the mouth's own openness when
      // the browser is doing the talking and there is no stream to measure.
      const rawLevel = amplitude && amplitude.current > 0.001
        ? amplitude.current
        : (st === 'speaking' ? live.m.open * 0.75 : 0);
      // Up fast, down slow: a halo that snaps shut between words flickers.
      live.level = lerp(live.level, rawLevel, 1 - Math.exp(-(rawLevel > live.level ? 30 : 7) * dt));
      if (halo) {
        halo.setAttribute('opacity', String(r2(0.14 + live.level * 0.80)));
        halo.setAttribute('rx', String(r2(380 + live.level * 110)));
        halo.setAttribute('ry', String(r2(330 + live.level * 90)));
      }
      // The tile reads the level too — for the wave under the caption and the
      // ring around the frame — off one custom property, set once a frame.
      if (host) host.style.setProperty('--amp', String(r2(live.level)));

      /* ---- the head ------------------------------------------------------- */
      // THE POSE IS THE STATE, MADE VISIBLE. A face that only moves its mouth
      // is a puppet; what makes it read as attending is what the HEAD does
      // between the words. Each state wants a pose, and the pose is sprung
      // towards so a change of state is a movement, not a cut:
      //
      //   listening  leans in and tips very slightly — "go on";
      //   thinking   tips the other way and settles, the way people look
      //              slightly off to the side while they work something out;
      //   speaking   sits square and nods on the open vowels, because that is
      //              where the stress of a sentence lands;
      //   idle / off sits back.
      if (st !== live.lastState) { live.lastState = st; live.stateAt = t; }
      const since = (t - live.stateAt) / 1000;
      const wantTilt = st === 'listening' ? 1.6 : st === 'thinking' ? -2.2 : 0;
      const wantLean = st === 'listening' ? 1.045 : st === 'speaking' ? 1.02 : st === 'off' ? 0.985 : 1;
      live.tilt = lerp(live.tilt, wantTilt, 1 - Math.exp(-2.6 * dt));
      live.lean = lerp(live.lean, wantLean, 1 - Math.exp(-2.2 * dt));
      // A nod is the jaw opening carried up into the whole head — small, and
      // only while speaking, so a listening face does not bob at nothing.
      const wantNod = st === 'speaking' ? live.m.open * 5.5 + live.level * 4 : 0;
      live.nod = lerp(live.nod, wantNod, 1 - Math.exp(-14 * dt));
      // One acknowledging dip when listening begins — the "I'm here" a person
      // gives when you start talking to them. 700 ms, once per entry.
      const ack = st === 'listening' && since < 0.7 ? Math.sin(since / 0.7 * Math.PI) * 6 : 0;

      /* ---- the drift ----------------------------------------------------- */
      if (drift) {
        // A camera on a counter, and someone breathing in front of it. Two sines
        // at unrelated periods so the frame never returns to the same place on a
        // beat; a single sine reads as a metronome within seconds. Under reduced
        // motion the wander stops and only the pose and the nod remain, both
        // of which are information rather than atmosphere.
        const speed = st === 'speaking' ? 1.1 : st === 'listening' ? 0.95 : st === 'off' ? 0.6 : 0.8;
        const dx = reduced ? 0 : Math.sin(age * 0.29 * speed) * 3.4 + Math.sin(age * 0.19 * speed + 1.7) * 1.8;
        const dy = (reduced ? 0 : Math.sin(age * 0.41 * speed + 0.8) * 2.2) + live.nod + ack;
        // Rotation is about the base of the neck, not the centre of the plate:
        // a head tilts from where it joins the body, and a tilt about the eyes
        // reads as the picture swinging rather than the person moving.
        const px = W / 2, py = H * 0.92;
        // The plate is drawn at 1.045 about its own centre, so a drift this size
        // can never pull an edge into view; the lean multiplies that.
        drift.setAttribute(
          'transform',
          `translate(${r2(dx)} ${r2(dy)}) `
          + `translate(${px} ${py}) rotate(${r2(live.tilt)}) translate(${-px} ${-py}) `
          + `translate(${W / 2} ${H / 2}) scale(${r2(1.045 * live.lean)}) translate(${-W / 2} ${-H / 2})`,
        );
      }

      raf = requestAnimationFrame(frame);
    };

    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [viseme, amplitude]);

  return (
    <svg
      ref={root}
      className={`pp${className ? ` ${className}` : ''}`}
      data-state={state}
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="xMidYMid slice"
      role="img"
      aria-label="A drawn presenter. An illustration, not a photograph and not a person."
      focusable="false"
    >
      <defs>
        {/*
          Real lips are not a smooth ramp. The upper lip faces up and away and
          sits in shadow; the lower lip faces the light and is the brightest
          thing on a mouth. So the darkest stop is at the SEAM — which, because
          both lips grow in proportion as the jaw drops, stays at about 0.42 of
          the shape's height whether the mouth is shut or wide open.
        */}
        <linearGradient id="pp-lip" x1="0.5" y1="0" x2="0.5" y2="1">
          <stop offset="0" stopColor="var(--pp-lip)" />
          <stop offset="0.40" stopColor="var(--pp-lip-lo)" />
          <stop offset="0.52" stopColor="var(--pp-lip-hi)" />
          <stop offset="1" stopColor="var(--pp-lip)" />
        </linearGradient>
        <linearGradient id="pp-teeth" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#FBF7F1" />
          <stop offset="1" stopColor="#D8D0C6" />
        </linearGradient>
        {/* Energy at MID radius, not the centre: the centre is masked off the
            face, so a gradient bright at 0 and gone by 0.55 left almost
            nothing where the mask lets it through. The wall around the head
            is where the light has to be. */}
        <radialGradient id="pp-halo-fill" cx="0.5" cy="0.5" r="0.5">
          <stop offset="0" stopColor="var(--pp-halo, #E4A340)" stopOpacity="0.55" />
          <stop offset="0.40" stopColor="var(--pp-halo, #E4A340)" stopOpacity="0.75" />
          <stop offset="0.72" stopColor="var(--pp-halo, #E4A340)" stopOpacity="0.30" />
          <stop offset="1" stopColor="var(--pp-halo, #E4A340)" stopOpacity="0" />
        </radialGradient>
        {/* Transparent over the face, solid at the edge of the tile. Measured
            off the plate: the head sits inside about 150 px of centre. */}
        <radialGradient id="pp-halo-ramp" cx="0.5" cy="0.5" r="0.5">
          <stop offset="0" stopColor="#000" />
          <stop offset="0.36" stopColor="#000" />
          <stop offset="0.62" stopColor="#fff" />
          <stop offset="1" stopColor="#fff" />
        </radialGradient>
        <mask id="pp-halo-mask" maskUnits="userSpaceOnUse" x="0" y="0" width={W} height={H}>
          <rect x="0" y="0" width={W} height={H} fill="url(#pp-halo-ramp)" />
        </mask>
        <radialGradient id="pp-jaw" cx="0.5" cy="0.5" r="0.5">
          <stop offset="0" stopColor="var(--pp-shade)" stopOpacity="1" />
          <stop offset="1" stopColor="var(--pp-shade)" stopOpacity="0" />
        </radialGradient>
        {/* The inside of the mouth. Its path is rewritten every frame, so the
            teeth and the tongue are contained by construction, not by luck. */}
        <clipPath id="pp-gap-clip">
          <path className="pp-gap-clip" d="" />
        </clipPath>
        {EYES.map((e) => (
          <clipPath id={`pp-eye-${e.id}`} key={e.id}>
            {/* PADDED, and it has to be. The clip measured tight to the visible
                eye left a pale crescent of sclera at each outer corner when the
                lid came down — the plate's eyeliner flick runs a few pixels
                wider than the eye it outlines. The lid is skin-coloured, so
                covering a little of the face around the eye costs nothing and
                closing the eye completely is the whole point. */}
            <path d={almond(e, 7)} />
          </clipPath>
        ))}
      </defs>

      <g className="pp-drift">
        {/* The plate. `href` is a bundled asset on this origin; nothing else is
            fetched, ever. */}
        <image
          className="pp-plate"
          href={plate}
          x="0" y="0" width={W} height={H}
          preserveAspectRatio="xMidYMid slice"
        />

        {/* ------------------------------------------------------ the mouth -- */}
        <ellipse className="pp-jaw" cx={MOUTH_AT.x} cy={MOUTH_AT.y + 46} rx="72" ry="30" opacity="0" />
        <g className="pp-mouth" transform={`translate(${MOUTH_AT.x} ${MOUTH_AT.y}) scale(${MOUTH_SCALE})`}>
          {/*
            ORDER IS THE WHOLE TRICK, and getting it backwards cost a round of
            screenshots: the lips are a SOLID silhouette, not a ring, so
            anything drawn under them is invisible. The lips go down first, the
            dark of the gap is painted ON them, and the teeth and tongue are
            clipped into that gap. An opening mouth then reveals a hole in a
            face, which is what an opening mouth is. Drawn the other way round
            it is a lozenge that changes size — which is exactly what it looked
            like.
          */}
          <path className="pp-lips" d="" />
          <path className="pp-gap" d="" />
          <g clipPath="url(#pp-gap-clip)">
            <path className="pp-teeth-u" d="" />
            <path className="pp-tongue" d="" />
            <path className="pp-teeth-l" d="" />
          </g>
          <path className="pp-seam" d="M-27 0q27 2.5 54 0" opacity="0" />
        </g>

        {/* ------------------------------------------------------- the eyes -- */}
        {/* The plate's eyes stay exactly as they were drawn. All the rig adds is
            a lid that comes down over them — clipped to the eye, so it can never
            spill onto the face — and a lash line at its edge to close it. */}
        {EYES.map((e) => (
          <g clipPath={`url(#pp-eye-${e.id})`} key={e.id}>
            <g className={`pp-lid-${e.id}`}>
              <rect
                className="pp-lid"
                x={e.x0 - 8} y={e.top - 4 - 90}
                width={e.x1 - e.x0 + 16} height={90}
              />
              <path
                className="pp-lash"
                d={`M${e.x0} ${e.top - 6}Q${(e.x0 + e.x1) / 2} ${e.top + 6} ${e.x1} ${e.top - 6}`}
              />
            </g>
          </g>
        ))}
      </g>

      {/* THE HALO. The marigold on the wall behind the head, sized and lit by
          the voice's level: the room responding to the voice, the way a lamp
          catches a wall.

          ABOVE THE PLATE, NOT UNDER IT — the first version painted it beneath
          the image, and the image is opaque RGB, so it lit nothing. It is
          screened over the picture instead, and MASKED OFF THE FACE: the mask
          is a radial ramp that is transparent over the head and opaque at the
          edges, so the wall warms and the skin keeps its own colour. Painted
          outside the drift group so it does not sway with the head; light does
          not move when a person leans. */}
      <ellipse
        className="pp-halo"
        cx={W / 2} cy={H * 0.44} rx="380" ry="330"
        fill="url(#pp-halo-fill)" opacity="0.1"
        mask="url(#pp-halo-mask)"
        style={{ mixBlendMode: 'screen' }}
      />
    </svg>
  );
}

/**
 * The almond an eye is clipped to.
 *
 * The control points are placed so the quadratic passes exactly through the
 * measured top and bottom of the eye: for a quadratic, the midpoint sits at
 * `(start + 2*control + end) / 4`, so the control has to be twice as far out as
 * the point you want the curve to reach.
 */
function almond(
  e: { x0: number; x1: number; cy: number; top: number; bot: number },
  pad = 0,
): string {
  const mx = (e.x0 + e.x1) / 2;
  const up = 2 * (e.top - pad * 0.7) - e.cy;
  const dn = 2 * (e.bot + pad * 0.7) - e.cy;
  return `M${e.x0 - pad} ${e.cy}Q${mx} ${up} ${e.x1 + pad} ${e.cy}Q${mx} ${dn} ${e.x0 - pad} ${e.cy}Z`;
}
