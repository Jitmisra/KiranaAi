/* SAAF panel — the enrolment frame gate, made legible.
 * ===========================================================================
 *
 * WHAT THIS PANEL IS FOR
 * SAAF is the most invisible thing in the rig. It takes a burst of frames of a
 * product, throws most of them away, registers what is left to sub-pixel
 * precision and returns one image to enrol from. When it works nobody sees it;
 * when it silently degrades, every downstream identity decision is made from a
 * worse image than the operator believes they enrolled. So this panel exists to
 * show the discard, frame by frame, with the number that fired the gate.
 *
 * Three things are shown that a "just works" UI would hide:
 *
 *   1. THE CONTACT SHEET. Every frame in the burst, KEPT or REJECTED, with the
 *      measured value and the named threshold it failed. A mistuned gate is
 *      diagnosable from this sheet alone.
 *
 *   2. THE SHARPNESS GAIN, WITH ITS CONFOUND PRINTED NEXT TO IT. The gain is
 *      guarded vLap(stack) / guarded vLap(upscaled sharpest single frame).
 *      vLap also counts noise, and stacking removes noise, so on a noisy burst
 *      this UNDERSTATES the true resolution gain. Saying so costs nothing and
 *      not saying so would make the number a claim instead of a measurement.
 *
 *   3. THE NO-DIVERSITY WARNING. Multi-frame super-resolution needs the subject
 *      to move by a FRACTION of a pixel between frames — that is where the extra
 *      information comes from. On a tripod, with a still product, inter-frame
 *      motion goes to zero, there is no new sampling phase, and the stack
 *      degenerates to plain denoising. The honest behaviour is to say so, in
 *      capitals, rather than return a blurrier image with a confident face.
 *
 * `ecc_failed` deserves its own note. cv2.findTransformECC THROWS when it fails
 * to converge; it does not return a low correlation. gawaah/saaf.py treats a
 * throw as FRAME REJECTED, never as "no motion detected" — reading a throw as
 * zero motion would invert this module's central honesty check. The panel
 * labels it that way too, so the distinction survives into the UI.
 *
 * Everything above `createPanel` is pure. No browser, no globals.
 *
 * This file imports nothing. SAAF handles no money and no mat geometry, so
 * reaching into app.js for constants it does not use would be coupling for the
 * look of it.
 */

export const PANEL_ID = 'panel-saaf';
export const PANEL_TITLE = 'SAAF — enrolment frame gate';

// ===========================================================================
// 1. THE GATES. Mirrors gawaah/saaf.py, constant for constant; panels2.test.mjs
//    re-reads the Python module and asserts they are the same numbers.
// ===========================================================================

export const DEFAULT_SCALE = 2;
export const BLUR_VAR_MIN = 60.0;        // absolute guarded-vLap floor
export const BLUR_REL_MIN = 0.35;        // fraction of the burst's sharpest frame
export const MAX_SHIFT_PX = 10.0;        // registration limit, frame px
export const SAT_LEVEL = 250;            // 8-bit level counted as "blown"
export const SAT_FRAC_MAX = 0.02;        // >2% blown == specular glare
export const MAX_BLUR_SCORE = 0.46;      // absolute, scale-free focus ceiling
export const BLUR_SCORE_MTF50_CYC_PX = 0.15;  // what that ceiling means
export const MIN_SHIFT_PX = 0.15;        // below this the subject did not move
export const MIN_DIVERSITY = 0.10;       // circular variance of sampling phase

// --- frame reason codes -----------------------------------------------------
export const R_REFERENCE = 'reference';
export const R_OK = 'ok';
export const R_BLUR = 'blur';
export const R_GLARE = 'glare';
export const R_ECC_FAILED = 'ecc_failed';
export const R_SHIFT_TOO_LARGE = 'shift_too_large';
export const R_WARP_NOT_FINITE = 'warp_not_finite';
export const R_DEFOCUS = 'defocus';

export const FRAME_REASONS = Object.freeze([
  R_REFERENCE, R_OK, R_BLUR, R_GLARE, R_ECC_FAILED,
  R_SHIFT_TOO_LARGE, R_WARP_NOT_FINITE, R_DEFOCUS,
]);

export const KEEP_REASONS = Object.freeze([R_REFERENCE, R_OK]);

// --- warning codes ----------------------------------------------------------
export const W_NONE = '';
export const W_ALL_REJECTED = 'ALL_FRAMES_REJECTED';
export const W_SINGLE_FRAME = 'SINGLE_FRAME';
export const W_NO_DIVERSITY = 'NO_SUBPIXEL_DIVERSITY';
export const W_DEGENERATE_PHASE = 'DEGENERATE_SAMPLING_PHASE';
export const W_UNIFORMLY_DEFOCUSED = 'BURST_UNIFORMLY_DEFOCUSED';

export const WARNING_CODES = Object.freeze([
  W_NONE, W_ALL_REJECTED, W_SINGLE_FRAME, W_NO_DIVERSITY,
  W_DEGENERATE_PHASE, W_UNIFORMLY_DEFOCUSED,
]);

export const WARNING_NOTES = Object.freeze({
  [W_ALL_REJECTED]: 'every frame failed a gate. SAAF returns NO IMAGE rather than '
    + 'enrol a crop it could not verify. Nothing was enrolled.',
  [W_SINGLE_FRAME]: 'only one frame survived, so there is nothing to stack. The '
    + 'result is that single frame upscaled — no multi-frame information was added.',
  [W_NO_DIVERSITY]: `inter-frame motion is below ${MIN_SHIFT_PX} px: the subject `
    + 'effectively did not move. With no new sampling phase there is no sub-pixel '
    + 'information to recover, so the stack DEGENERATES TO DENOISING. It is not '
    + 'super-resolution and this panel will not call it that.',
  [W_DEGENERATE_PHASE]: `the sampling phases are clustered (circular variance below `
    + `${MIN_DIVERSITY}): the frames moved, but all by the same sub-pixel offset, so `
    + 'they re-sample the same grid positions. Same consequence as no motion at all.',
  [W_UNIFORMLY_DEFOCUSED]: `the whole burst is out of focus (registered-mean blur `
    + `score above ${MAX_BLUR_SCORE}). No frame is the "sharp" one, so the relative `
    + 'blur gate has nothing to catch and would have passed the burst silently.',
});

/** Warnings whose meaning is "the stack added no resolution". */
export const DEGENERATE_WARNINGS = Object.freeze([W_NO_DIVERSITY, W_DEGENERATE_PHASE]);

export const GAIN_CONFOUND =
  'sharpness gain = guarded vLap(stack) / guarded vLap(cubic-upscaled sharpest '
  + 'single frame). 1.00 means no gain. vLap also counts NOISE, and stacking '
  + 'removes noise, so on a noisy burst this number UNDERSTATES the real '
  + 'resolution gain. A slanted-edge MTF50 is the honest resolution measure; '
  + 'this is the cheap one, labelled as such.';

export const NO_SHARPENING_NOTE =
  'no unsharp mask, no learned prior, no deconvolution. A sharpening filter '
  + 'would inflate every number on this panel without adding one bit of '
  + 'information, which would make the reported gain a lie.';

// ===========================================================================
// 2. WHY EACH FRAME WENT. The sentence carries the measured number AND the
//    named threshold, so a mistuned gate is visible without reading the source.
// ===========================================================================

function f(x, dp = 2) { return Number.isFinite(x) ? x.toFixed(dp) : '?'; }
function pct(x, dp = 2) { return Number.isFinite(x) ? `${(x * 100).toFixed(dp)}%` : '?'; }

/**
 * Explain one frame verdict. `relFloor` is BLUR_REL_MIN * max(vlap) over the
 * burst — recomputed here rather than trusted, so the panel can show the floor
 * the frame was actually judged against.
 */
export function explainFrame(fr, relFloor = null) {
  switch (fr.code) {
    case R_REFERENCE:
      return `KEPT as the reference: the sharpest frame in the burst (vLap ${f(fr.vlap, 1)}). `
        + 'Every other frame is registered to this one.';
    case R_OK:
      return `KEPT: vLap ${f(fr.vlap, 1)}, blown pixels ${pct(fr.satFrac)}, `
        + `shift ${f(fr.shiftPx, 3)} px, blur score ${f(fr.blurScore, 3)}.`;
    case R_BLUR:
      return `REJECTED — blur below the floor. vLap ${f(fr.vlap, 1)} is under `
        + (relFloor !== null
          ? `the burst-relative floor ${BLUR_REL_MIN} x ${f(relFloor / BLUR_REL_MIN, 1)} = ${f(relFloor, 1)}`
          : `the relative floor (${BLUR_REL_MIN} of the burst maximum)`)
        + ` or the absolute floor ${BLUR_VAR_MIN.toFixed(1)}.`;
    case R_GLARE:
      return `REJECTED — saturation above the guard. ${pct(fr.satFrac)} of pixels are `
        + `at or above level ${SAT_LEVEL}, over the ${pct(SAT_FRAC_MAX)} limit. That is `
        + 'specular glare: the blown region carries no detail and would inflate vLap.';
    case R_DEFOCUS:
      return `REJECTED — absolute defocus. Blur score ${f(fr.blurScore, 3)} is above the `
        + `${MAX_BLUR_SCORE} ceiling (calibrated to MTF50 ${BLUR_SCORE_MTF50_CYC_PX} cyc/px). `
        + 'This gate is scale-free, so it catches what a burst-relative gate cannot: '
        + 'a burst where every frame is equally soft.';
    case R_SHIFT_TOO_LARGE:
      return `REJECTED — registration limit. Mean corner displacement ${f(fr.shiftPx, 2)} px `
        + `exceeds ${MAX_SHIFT_PX} px, so the crop is showing different content, not the `
        + 'same region moved slightly.';
    case R_ECC_FAILED:
      return 'REJECTED — the ECC alignment THREW. cv2.findTransformECC raises when it '
        + 'fails to converge; it does not return a low correlation. A throw is read as '
        + 'FRAME REJECTED, never as "no motion detected".';
    case R_WARP_NOT_FINITE:
      return 'REJECTED — the estimated warp contained a non-finite number, so the '
        + 'alignment is meaningless and the frame cannot be placed on the grid.';
    case '':
    case undefined:
      return 'REJECTED — no reason code was supplied with this frame. An '
        + 'unexplained discard is exactly what this panel exists to prevent, so '
        + 'the panel will not invent a reason for it.';
    default:
      return `REJECTED — unrecognised reason code '${fr.code}'. This panel will not `
        + 'invent an explanation for a code it does not know.';
  }
}

// ===========================================================================
// 3. DERIVE. StackResult in, display model out. Pure, and never throws on shape.
// ===========================================================================

/** Number(null) is 0 and Number('') is 0. Neither is a measurement. */
function num(v, dflt = null) {
  if (v === null || v === undefined || typeof v === 'boolean' || v === '') return dflt;
  const n = Number(v);
  return Number.isFinite(n) ? n : dflt;
}
function pickf(o, ...names) {
  if (!o || typeof o !== 'object') return undefined;
  for (const n of names) if (o[n] !== undefined && o[n] !== null) return o[n];
  return undefined;
}

function coerceFrame(raw, i) {
  const reason = String(pickf(raw, 'reason') ?? '');
  const code = reason.split(':', 1)[0];
  const used = pickf(raw, 'used') === true;
  return {
    index: Math.trunc(num(pickf(raw, 'index'), i)),
    used,
    reason,
    code,
    known: FRAME_REASONS.includes(code),
    detail: reason.includes(':') ? reason.slice(reason.indexOf(':') + 1).trim() : '',
    vlap: num(pickf(raw, 'vlap')),
    vlapRaw: num(pickf(raw, 'vlapRaw', 'vlap_raw')),
    satFrac: num(pickf(raw, 'satFrac', 'sat_frac')),
    dx: num(pickf(raw, 'dx')),
    dy: num(pickf(raw, 'dy')),
    shiftPx: num(pickf(raw, 'shiftPx', 'shift_px')),
    blurScore: num(pickf(raw, 'blurScore', 'blur_score')),
    thumb: pickf(raw, 'thumb', 'thumbUrl', 'thumb_url') ?? null,
  };
}

export function deriveSaaf(state) {
  const abstentions = [];
  const note = (where, code, text) => { abstentions.push({ where, code, note: text }); };

  const s = (state && typeof state === 'object')
    ? (state.saaf ?? (state.reports || state.frames ? state : null))
    : null;

  if (!s) {
    note('burst', 'no_burst', 'no enrolment burst has been captured in this session.');
    return Object.freeze({
      id: PANEL_ID, title: PANEL_TITLE, present: false,
      frames: Object.freeze([]),
      counts: Object.freeze({ total: 0, kept: 0, rejected: 0, byCode: Object.freeze({}) }),
      gain: Object.freeze({ known: false, value: null, text: 'I DO NOT KNOW', confound: GAIN_CONFOUND }),
      motion: Object.freeze({
        known: false, meanShiftPx: null, diversity: null,
        nearZero: null, text: 'I DO NOT KNOW', reason: 'no_burst',
      }),
      warning: Object.freeze({
        raw: '', code: W_NONE, known: true, degenerate: false, present: false,
        note: 'no burst, so no warning.',
      }),
      image: Object.freeze({ present: false, url: null, reason: 'no_burst' }),
      relFloor: null,
      maxVlap: null,
      referenceIndex: -1,
      scale: DEFAULT_SCALE,
      burstBlurScore: null,
      thumbs: false,
      abstentions: Object.freeze(abstentions.map((a) => Object.freeze(a))),
    });
  }

  const rawFrames = pickf(s, 'frames', 'reports') ?? [];
  const frames = (Array.isArray(rawFrames) ? rawFrames : []).map(coerceFrame);
  if (frames.length === 0) {
    note('contact_sheet', 'no_frame_reports',
      'the burst produced no per-frame reports, so the discard cannot be shown. '
      + 'An unexplained discard is exactly what this panel exists to prevent.');
  }
  for (const fr of frames) {
    if (!fr.known) {
      note('contact_sheet', 'unknown_frame_reason',
        `frame ${fr.index} carries reason code '${fr.code}', which is not one of `
        + `${FRAME_REASONS.join('/')}.`);
    }
  }

  const vlaps = frames.map((fr) => fr.vlap).filter((v) => v !== null);
  const maxVlap = vlaps.length ? Math.max(...vlaps) : null;
  const relFloor = maxVlap === null ? null : BLUR_REL_MIN * maxVlap;

  const byCode = {};
  for (const fr of frames) byCode[fr.code] = (byCode[fr.code] ?? 0) + 1;
  const keptFromFrames = frames.filter((fr) => fr.used).length;
  const usedReported = num(pickf(s, 'used'));
  const rejReported = num(pickf(s, 'rejected'));
  const kept = usedReported === null ? keptFromFrames : Math.trunc(usedReported);
  const rejected = rejReported === null
    ? frames.length - keptFromFrames
    : Math.trunc(rejReported);
  if (frames.length > 0 && usedReported !== null && Math.trunc(usedReported) !== keptFromFrames) {
    note('counts', 'used_count_disagrees',
      `the result reports used=${Math.trunc(usedReported)} but ${keptFromFrames} of the `
      + `${frames.length} frame reports are marked used. Showing both; believing neither.`);
  }

  const referenceIndex = Math.trunc(num(pickf(s, 'referenceIndex', 'reference_index'), -1));

  // ---- sharpness gain --------------------------------------------------
  const rawGain = num(pickf(s, 'sharpnessGain', 'sharpness_gain'));
  const gain = rawGain === null
    ? { known: false, value: null, text: 'I DO NOT KNOW', reason: 'sharpness_gain_absent' }
    : {
      known: true,
      value: rawGain,
      text: `${rawGain.toFixed(3)}x`,
      atOrBelowUnity: rawGain <= 1.0,
      reason: null,
    };
  if (!gain.known) {
    note('gain', 'sharpness_gain_absent',
      'no sharpness gain was reported, so the stack cannot be shown to have improved '
      + 'anything. Absence of a measurement is not evidence of a gain.');
  } else if (gain.atOrBelowUnity) {
    note('gain', 'no_measured_gain',
      `measured gain ${rawGain.toFixed(3)}x is at or below 1.00x: the stack is no `
      + 'sharper than the sharpest single frame, upscaled.');
  }

  // ---- inter-frame motion / sub-pixel diversity ------------------------
  const meanShiftPx = num(pickf(s, 'meanShiftPx', 'mean_shift_px'));
  const diversity = num(pickf(s, 'subpixelDiversity', 'subpixel_diversity'));
  const diversityX = num(pickf(s, 'diversityX', 'diversity_x'));
  const diversityY = num(pickf(s, 'diversityY', 'diversity_y'));
  const shiftNearZero = meanShiftPx === null ? null : meanShiftPx < MIN_SHIFT_PX;
  const phaseClustered = diversity === null ? null : diversity < MIN_DIVERSITY;
  const nearZero = (shiftNearZero === null && phaseClustered === null)
    ? null
    : (shiftNearZero === true || phaseClustered === true);
  const motion = {
    known: meanShiftPx !== null || diversity !== null,
    meanShiftPx,
    diversity,
    diversityX,
    diversityY,
    minShiftPx: MIN_SHIFT_PX,
    minDiversity: MIN_DIVERSITY,
    shiftNearZero,
    phaseClustered,
    nearZero,
    text: meanShiftPx === null
      ? 'I DO NOT KNOW — no inter-frame displacement was reported'
      : `mean inter-frame shift ${meanShiftPx.toFixed(3)} px (floor ${MIN_SHIFT_PX} px)`
        + (diversity === null ? '' : `, sampling-phase diversity ${diversity.toFixed(3)} (floor ${MIN_DIVERSITY})`),
    reason: meanShiftPx === null ? 'mean_shift_absent' : null,
  };
  if (meanShiftPx === null) {
    note('motion', 'mean_shift_absent',
      'inter-frame motion was not reported, so the panel cannot tell whether this '
      + 'stack recovered sub-pixel detail or merely denoised. It will not assume.');
  }

  // ---- the warning -----------------------------------------------------
  const rawWarning = String(pickf(s, 'warning') ?? '');
  const wcode = rawWarning ? rawWarning.split(':', 1)[0].trim() : W_NONE;
  const warning = {
    raw: rawWarning,
    code: wcode,
    present: rawWarning !== '',
    known: WARNING_CODES.includes(wcode),
    degenerate: DEGENERATE_WARNINGS.includes(wcode),
    detail: rawWarning.includes(':') ? rawWarning.slice(rawWarning.indexOf(':') + 1).trim() : '',
    note: WARNING_NOTES[wcode] ?? (rawWarning
      ? `unrecognised warning code '${wcode}'. This panel will not translate a code it does not know.`
      : 'no warning: the stack is what a caller asking for super-resolution wanted.'),
  };
  if (warning.present) {
    note('warning', wcode || 'warning_present', warning.note);
  }
  if (warning.present && !warning.known) {
    note('warning', 'unknown_warning_code', warning.note);
  }
  // The panel raises the degeneracy itself when the numbers say so, even if the
  // brain forgot to set the warning. A silent degradation is the failure mode
  // this whole panel exists to catch.
  const selfDetectedDegenerate = nearZero === true && !warning.degenerate;
  if (selfDetectedDegenerate) {
    note('motion', 'degenerate_but_unwarned',
      `measured motion is below the floor (shift ${meanShiftPx === null ? '?' : meanShiftPx.toFixed(3)} px, `
      + `diversity ${diversity === null ? '?' : diversity.toFixed(3)}) but the result carries `
      + `warning '${rawWarning}'. The panel raises the degeneracy on the numbers.`);
  }

  // ---- the returned image ----------------------------------------------
  const img = pickf(s, 'image', 'imageUrl', 'image_url');
  const image = {
    present: img !== undefined && img !== null && img !== false,
    url: typeof img === 'string' ? img : null,
    reason: null,
  };
  if (!image.present) {
    image.reason = wcode === W_ALL_REJECTED ? W_ALL_REJECTED : 'no_image_returned';
    note('image', image.reason,
      wcode === W_ALL_REJECTED
        ? 'every frame was rejected, so SAAF returned no image. Nothing was enrolled — '
          + 'abstaining is the designed behaviour, not a bug.'
        : 'no stacked image was returned with this result, so nothing can be enrolled from it.');
  }

  const thumbs = frames.some((fr) => typeof fr.thumb === 'string' && fr.thumb.length > 0);
  if (frames.length > 0 && !thumbs) {
    note('contact_sheet', 'no_thumbnails',
      'no thumbnails were supplied with the burst. The contact sheet shows every '
      + 'frame with its gate numbers, but not its pixels.');
  }

  const burstBlurScore = num(pickf(s, 'burstBlurScore', 'burst_blur_score'));

  return Object.freeze({
    id: PANEL_ID,
    title: PANEL_TITLE,
    present: true,
    frames: Object.freeze(frames.map((fr) => Object.freeze({
      ...fr, explain: explainFrame(fr, relFloor),
      isReference: fr.index === referenceIndex || fr.code === R_REFERENCE,
    }))),
    counts: Object.freeze({
      total: frames.length, kept, rejected, byCode: Object.freeze(byCode), keptFromFrames,
    }),
    relFloor,
    maxVlap,
    referenceIndex,
    scale: Math.trunc(num(pickf(s, 'scale'), DEFAULT_SCALE)),
    gain: Object.freeze({ ...gain, confound: GAIN_CONFOUND }),
    motion: Object.freeze(motion),
    warning: Object.freeze({ ...warning, selfDetected: selfDetectedDegenerate }),
    image: Object.freeze(image),
    burstBlurScore,
    thumbs,
    abstentions: Object.freeze(abstentions.map((a) => Object.freeze(a))),
  });
}

// ===========================================================================
// 4. RENDER. Pure: (model, doc) -> element.
// ===========================================================================

/** No green, no red. A kept frame is ink; a rejected frame is amber. */
export const PALETTE = Object.freeze({
  amber: '#e0a33c',
  amberDim: '#7a5a1e',
  ink: '#e8eaee',
  mute: '#8b929d',
  panel: '#12151b',
  line: '#242a33',
});

function mk(doc, tag, opts = {}) {
  const el = doc.createElement(tag);
  if (opts.class) el.className = opts.class;
  if (opts.text !== undefined) el.textContent = String(opts.text);
  if (opts.data) for (const [k, v] of Object.entries(opts.data)) el.dataset[k] = String(v);
  if (opts.attrs && el.setAttribute) {
    for (const [k, v] of Object.entries(opts.attrs)) el.setAttribute(k, String(v));
  }
  if (opts.style && el.style) for (const [k, v] of Object.entries(opts.style)) el.style[k] = v;
  for (const kid of opts.kids || []) if (kid) el.appendChild(kid);
  return el;
}

function row(doc, label, value, extra = {}) {
  return mk(doc, 'div', {
    class: `kv ${extra.class || ''}`.trim(),
    data: extra.data,
    kids: [
      mk(doc, 'span', { class: 'kv-k', text: label }),
      mk(doc, 'span', { class: 'kv-v', text: value }),
    ],
  });
}

/** One contact-sheet cell. Exported so the sheet can be unit-tested cell-wise. */
export function renderFrameCell(fr, doc, relFloor = null) {
  const verdictWord = fr.used ? 'KEPT' : 'REJECTED';
  const kids = [
    mk(doc, 'div', {
      class: 'saaf-cell-head',
      kids: [
        mk(doc, 'span', { class: 'saaf-cell-idx', text: `#${fr.index}` }),
        mk(doc, 'span', {
          class: `saaf-cell-verdict saaf-cell-${fr.used ? 'kept' : 'rejected'}`,
          data: { used: fr.used },
          text: verdictWord + (fr.isReference ? ' · REF' : ''),
        }),
      ],
    }),
  ];

  // The thumbnail, or an explicit "no thumbnail" tile. Never a blank box that
  // could be mistaken for a black frame.
  if (typeof fr.thumb === 'string' && fr.thumb.length > 0) {
    kids.push(mk(doc, 'img', {
      class: 'saaf-thumb',
      attrs: { src: fr.thumb, alt: `burst frame ${fr.index}, ${verdictWord}`, loading: 'lazy' },
    }));
  } else {
    kids.push(mk(doc, 'div', {
      class: 'saaf-thumb saaf-thumb-absent',
      data: { thumb: 'absent' },
      style: { borderColor: PALETTE.line, color: PALETTE.mute },
      text: 'no thumbnail supplied',
    }));
  }

  kids.push(
    mk(doc, 'code', { class: 'saaf-cell-code', text: fr.code || '(no reason code)' }),
    mk(doc, 'p', { class: 'saaf-cell-why', text: fr.explain ?? explainFrame(fr, relFloor) }),
    mk(doc, 'div', {
      class: 'saaf-cell-nums',
      kids: [
        row(doc, 'vLap (guarded)', f(fr.vlap, 1)),
        row(doc, 'vLap (raw)', f(fr.vlapRaw, 1)),
        row(doc, 'blown pixels', pct(fr.satFrac)),
        row(doc, 'blur score', f(fr.blurScore, 3)),
        row(doc, 'shift', fr.shiftPx === null ? '—' : `${f(fr.shiftPx, 3)} px`),
      ],
    }),
  );
  if (fr.detail) kids.push(mk(doc, 'p', { class: 'saaf-cell-detail', text: fr.detail }));

  return mk(doc, 'figure', {
    class: `saaf-cell ${fr.used ? 'saaf-cell-is-kept' : 'saaf-cell-is-rejected'}`,
    data: { index: fr.index, used: fr.used, code: fr.code, known: fr.known },
    style: { borderColor: fr.used ? PALETTE.line : PALETTE.amberDim },
    kids,
  });
}

function renderContactSheet(model, doc) {
  const sheet = mk(doc, 'div', {
    class: 'saaf-sheet',
    data: { frames: model.frames.length, thumbs: model.thumbs },
  });
  if (model.frames.length === 0) {
    sheet.appendChild(mk(doc, 'p', {
      class: 'saaf-sheet-empty',
      data: { reason: model.present ? 'no_frame_reports' : 'no_burst' },
      text: model.present
        ? 'I DO NOT KNOW — the burst produced no per-frame reports, so the discard cannot be shown.'
        : 'I DO NOT KNOW — no enrolment burst has been captured in this session.',
    }));
    return sheet;
  }
  for (const fr of model.frames) sheet.appendChild(renderFrameCell(fr, doc, model.relFloor));
  return sheet;
}

function renderWarning(model, doc) {
  const w = model.warning;
  const degenerate = w.degenerate || model.motion.nearZero === true;
  const headline = w.present
    ? w.code
    : (degenerate ? `${W_NO_DIVERSITY} (raised by this panel from the numbers)` : 'no warning');
  // When the panel raised the degeneracy itself, the note must explain THAT,
  // not the result's own "no warning" — a headline and a note disagreeing is
  // how a screen ends up saying two things and meaning neither.
  const note = (!w.present && degenerate) ? WARNING_NOTES[W_NO_DIVERSITY] : w.note;
  return mk(doc, 'section', {
    class: `saaf-warning ${w.present || degenerate ? 'saaf-warning-on' : 'saaf-warning-off'}`,
    data: {
      warning: w.code || W_NONE,
      present: w.present,
      degenerate,
      selfDetected: w.selfDetected === true,
    },
    style: { borderColor: w.present || degenerate ? PALETTE.amber : PALETTE.line },
    kids: [
      mk(doc, 'h3', { class: 'panel-h3', text: 'warning state' }),
      mk(doc, 'div', {
        class: 'saaf-warning-code',
        style: { color: w.present || degenerate ? PALETTE.amber : PALETTE.mute },
        text: headline,
      }),
      mk(doc, 'p', { class: 'saaf-warning-note', text: note }),
      w.detail ? mk(doc, 'p', { class: 'saaf-warning-detail', text: w.detail }) : null,
      degenerate
        ? mk(doc, 'p', {
          class: 'saaf-degenerate',
          data: { degenerate: 'true' },
          text: 'STACKING DEGENERATED TO DENOISING. There was no sub-pixel diversity to '
            + 'recover detail from, so this result is a cleaner copy of the same '
            + 'resolution — not a sharper image. Saying so is the honest behaviour; '
            + 'returning a worse image silently is not.',
        })
        : null,
      w.selfDetected
        ? mk(doc, 'p', {
          class: 'saaf-selfdetected',
          text: 'The result did not carry this warning. The panel raised it from the '
            + 'measured motion, because a silent degradation is exactly the failure '
            + 'this panel exists to catch.',
        })
        : null,
    ],
  });
}

function renderMotion(model, doc) {
  const m = model.motion;
  return mk(doc, 'section', {
    class: 'saaf-motion',
    data: {
      known: m.known,
      nearZero: m.nearZero === null ? 'unknown' : String(m.nearZero),
    },
    kids: [
      mk(doc, 'h3', { class: 'panel-h3', text: 'inter-frame motion (where the extra information comes from)' }),
      mk(doc, 'div', {
        class: m.known ? 'saaf-motion-big' : 'saaf-motion-big saaf-unknown',
        style: { color: m.nearZero === true ? PALETTE.amber : PALETTE.ink },
        text: m.meanShiftPx === null ? 'I DO NOT KNOW' : `${m.meanShiftPx.toFixed(3)} px`,
      }),
      mk(doc, 'p', { class: 'saaf-motion-note', text: m.text }),
      row(doc, 'motion floor', `${MIN_SHIFT_PX} px`),
      row(doc, 'phase diversity floor', String(MIN_DIVERSITY)),
      row(doc, 'diversity x / y',
        `${f(m.diversityX, 3)} / ${f(m.diversityY, 3)}`),
    ],
  });
}

function renderGain(model, doc) {
  const g = model.gain;
  return mk(doc, 'section', {
    class: 'saaf-gain',
    data: { known: g.known, value: g.known ? g.value.toFixed(4) : '' },
    kids: [
      mk(doc, 'h3', { class: 'panel-h3', text: 'measured sharpness gain' }),
      mk(doc, 'div', {
        class: g.known ? 'saaf-gain-big' : 'saaf-gain-big saaf-unknown',
        style: { color: g.known && !g.atOrBelowUnity ? PALETTE.ink : PALETTE.amber },
        text: g.text,
      }),
      g.known && g.atOrBelowUnity
        ? mk(doc, 'p', {
          class: 'saaf-gain-none',
          text: 'at or below 1.00x — the stack is no sharper than the sharpest single '
            + 'frame, upscaled. No resolution was recovered.',
        })
        : null,
      mk(doc, 'p', { class: 'saaf-gain-confound', text: g.confound }),
      mk(doc, 'p', { class: 'saaf-no-sharpening', text: NO_SHARPENING_NOTE }),
      row(doc, 'burst blur score (registered mean)',
        model.burstBlurScore === null ? 'unknown' : `${model.burstBlurScore.toFixed(3)} (ceiling ${MAX_BLUR_SCORE})`),
      row(doc, 'upscale factor', `${model.scale}x`),
    ],
  });
}

function renderAbstentions(model, doc) {
  const box = mk(doc, 'section', {
    class: 'saaf-abstentions',
    data: { count: model.abstentions.length },
    kids: [mk(doc, 'h3', { class: 'panel-h3', text: `I do not know (${model.abstentions.length})` })],
  });
  if (model.abstentions.length === 0) {
    box.appendChild(mk(doc, 'p', { class: 'saaf-abstain-none', text: 'nothing is being withheld on this burst.' }));
    return box;
  }
  const ul = mk(doc, 'ul', { class: 'saaf-abstain-list' });
  for (const a of model.abstentions) {
    ul.appendChild(mk(doc, 'li', {
      class: 'saaf-abstain',
      data: { where: a.where, code: a.code },
      kids: [
        mk(doc, 'code', { class: 'abstain-code', text: `${a.where}: ${a.code}` }),
        mk(doc, 'span', { class: 'abstain-note', text: a.note }),
      ],
    }));
  }
  box.appendChild(ul);
  return box;
}

export function renderSaaf(model, doc = globalThis.document) {
  if (!doc || typeof doc.createElement !== 'function') {
    throw new TypeError('renderSaaf needs a document-like object with createElement');
  }
  const c = model.counts;
  return mk(doc, 'section', {
    class: 'panel panel-saaf',
    data: {
      panel: 'saaf',
      present: model.present,
      kept: c.kept,
      rejected: c.rejected,
      warning: model.warning.code || W_NONE,
      degenerate: model.warning.degenerate || model.motion.nearZero === true,
      abstentions: model.abstentions.length,
    },
    kids: [
      mk(doc, 'header', {
        class: 'panel-head',
        kids: [
          mk(doc, 'h2', { class: 'panel-title', text: model.title }),
          mk(doc, 'span', {
            class: 'saaf-counts',
            data: { kept: c.kept, rejected: c.rejected, total: c.total },
            text: `${c.kept} kept · ${c.rejected} rejected · ${c.total} frames shown`,
          }),
        ],
      }),
      mk(doc, 'p', {
        class: 'saaf-blurb',
        text: 'SAAF is invisible by construction: it discards most of a burst and '
          + 'returns one image. Everything it threw away is below, with the number '
          + 'that fired the gate.',
      }),

      renderWarning(model, doc),
      renderMotion(model, doc),
      renderGain(model, doc),

      mk(doc, 'section', {
        class: 'saaf-sheet-wrap',
        kids: [
          mk(doc, 'h3', { class: 'panel-h3', text: `contact sheet (${c.total} frames)` }),
          model.relFloor === null
            ? null
            : mk(doc, 'p', {
              class: 'saaf-relfloor',
              text: `burst-relative blur floor for this burst: ${BLUR_REL_MIN} x `
                + `${model.maxVlap.toFixed(1)} (sharpest frame) = ${model.relFloor.toFixed(1)}. `
                + `Absolute vLap floor ${BLUR_VAR_MIN.toFixed(1)}. `
                + `Absolute focus ceiling ${MAX_BLUR_SCORE} blur score.`,
            }),
          renderContactSheet(model, doc),
        ],
      }),

      mk(doc, 'section', {
        class: 'saaf-result',
        data: { image: model.image.present, reason: model.image.reason ?? '' },
        kids: [
          mk(doc, 'h3', { class: 'panel-h3', text: 'stacked result' }),
          model.image.present
            ? mk(doc, 'img', {
              class: 'saaf-result-img',
              attrs: { src: model.image.url ?? '', alt: 'stacked enrolment image' },
            })
            : mk(doc, 'p', {
              class: 'saaf-result-none',
              data: { reason: model.image.reason ?? '' },
              text: `NO IMAGE — ${model.image.reason}. SAAF abstains rather than enrol a `
                + 'crop it could not verify.',
            }),
        ],
      }),

      renderAbstentions(model, doc),
    ],
  });
}

// ===========================================================================
// 5. THE PANEL OBJECT.
// ===========================================================================

export function createPanel(opts = {}) {
  const doc = opts.doc ?? opts.document ?? globalThis.document;
  let root = opts.root ?? opts.host ?? null;
  let model = deriveSaaf(null);

  const resolveRoot = () => {
    if (root) return root;
    if (doc && typeof doc.getElementById === 'function') root = doc.getElementById(PANEL_ID);
    return root;
  };

  return {
    id: PANEL_ID,
    title: PANEL_TITLE,
    get model() { return model; },

    onState(state) {
      model = deriveSaaf(state);
      const host = resolveRoot();
      if (!host || typeof host.replaceChildren !== 'function') return false;
      host.replaceChildren(renderSaaf(model, doc));
      if (host.dataset) {
        host.dataset.warning = model.warning.code || W_NONE;
        host.dataset.kept = String(model.counts.kept);
      }
      return true;
    },

    /**
     * SAAF has nothing to overlay on the live view: it operates on a burst that
     * has already been captured. Returning false rather than pretending to draw
     * keeps the honest answer in the return value.
     */
    onFrame() { return false; },
  };
}

export function attach(register, opts = {}) {
  if (typeof register !== 'function') {
    throw new TypeError('attach(register): registerPanel must be a function');
  }
  const panel = createPanel(opts);
  register(PANEL_ID, panel);
  return panel;
}

/** See chilla.js: the attachXPanel(opts) convention, supported alongside. */
export function attachSaafPanel(opts = {}) {
  const panel = createPanel(opts);
  const register = typeof opts.register === 'function'
    ? opts.register
    : (typeof globalThis.registerPanel === 'function' ? globalThis.registerPanel : null);
  const registration = register
    ? register(PANEL_ID, { onState: panel.onState, onFrame: panel.onFrame })
    : null;
  return { panel, registered: register !== null, registration };
}

/** See chilla.js: the shell may import `attach`, or drain GAWAAH_PANELS. */
export const DESCRIPTOR = { id: PANEL_ID, title: PANEL_TITLE, createPanel, attach, attached: false };
if (typeof globalThis !== 'undefined') {
  if (typeof globalThis.registerPanel === 'function') {
    attach(globalThis.registerPanel);
    DESCRIPTOR.attached = true;
  }
  (globalThis.GAWAAH_PANELS ||= []).push(DESCRIPTOR);
}

export default { PANEL_ID, createPanel, attach, deriveSaaf, renderSaaf };
