/* CHILLA panel — screen corroboration, and the sentence about why.
 * ===========================================================================
 *
 * WHAT THIS PANEL IS FOR
 * The customer holds up a handset showing "PAYMENT SUCCESSFUL". We look at it.
 * The obvious thing to do is read the UPI reference string off the screen and
 * look it up. We do not do that, and the reason is arithmetic, not policy:
 *
 *     a 12sp UPI reference digit stroke is 0.19 mm.
 *     the rectified mat samples at 2.8284 px/mm.
 *     0.19 x 2.8284 = 0.54 px, against a 2 px Nyquist floor.
 *     it is not hard to read. IT IS NOT IN THE SIGNAL.
 *
 * That sentence is rendered into the panel, with the numbers COMPUTED here
 * from the rig's own geometry rather than typed, so it cannot drift away from
 * the truth if the buffer size changes. Mirrors gawaah/chilla.py `legibility`.
 *
 * So CHILLA matches on the composite key (amount, time window) instead, and
 * publishes the collision risk of that key at the moment it is used.
 *
 * INVARIANT 2, RENDERED AS STRUCTURE, NOT AS A COMMENT
 * `lightFor()` returns AMBER for every verdict there is, MATCHED included, and
 * throws on anything it does not recognise. There is no code path in this file
 * that can put a green or a red class on an element. NO_MATCH is amber: the
 * likeliest cause of a missing row is a slow webhook, not a liar. A stale
 * mirror is amber EVEN WHEN A MATCH EXISTS, because a mirror we did not manage
 * to refresh cannot corroborate anything. Two payments of the same amount in
 * the window is AMBIGUOUS, never a confident pick.
 *
 * INVARIANT 7: every region of this panel has a named "I DO NOT KNOW" state.
 * `deriveChilla()` collects them into `model.abstentions` so the test suite can
 * enumerate them and the operator can read them.
 *
 * CONTRACT
 *   import { attach } from './panels/chilla.js';
 *   attach(registerPanel);                    // registerPanel(id, {onState,onFrame})
 * or, if the host sets a global before loading panels, this module
 * self-registers on import. Both paths end at `createPanel()`.
 *
 * Everything above `createPanel` is pure: data in, data or DOM out, no globals,
 * no browser. `web/panels/panels2.test.mjs` runs the whole file under node.
 */

import { formatRupees, paise, MoneyError, PX_PER_MM, MAT_W_MM, MAT_H_MM } from '../app.js';

export const PANEL_ID = 'panel-chilla';
export const PANEL_TITLE = 'CHILLA — screen corroboration';

// ===========================================================================
// 1. THE OPTICAL BUDGET. Mirrors gawaah/chilla.py section 1, constant for
//    constant. panels2.test.mjs re-reads the Python module and asserts these
//    are the same numbers, so the browser cannot quietly claim a better rig.
// ===========================================================================

export const NYQUIST_PX = 2.0;
export const REFERENCE_STRING_STROKE_MM = 0.19;
export const SCREEN_TIMESTAMP_STROKE_MM = 0.19;
export const HERO_AMOUNT_CAP_MM = 4.45;
export const SUPER_RES_FACTOR = 2.0;

export class ChillaRefusal extends Error {
  constructor(msg) { super(msg); this.name = 'ChillaRefusal'; }
}

/**
 * Can this feature size survive this sampling rate? Arithmetic only, no model,
 * no opinion. `shortfall_x` reads "this many times short of the floor" when
 * unreadable and "this many times clear of it" when readable.
 */
export function legibility(feature, sizeMm, pxPerMm = PX_PER_MM) {
  if (!(sizeMm > 0) || !(pxPerMm > 0)) {
    throw new RangeError(`non-positive geometry: sizeMm=${sizeMm} pxPerMm=${pxPerMm}`);
  }
  const sizePx = sizeMm * pxPerMm;
  const readable = sizePx >= NYQUIST_PX;
  const l = {
    feature,
    sizeMm,
    pxPerMm,
    sizePx,
    nyquistPx: NYQUIST_PX,
    readable,
    shortfallX: readable ? sizePx / NYQUIST_PX : NYQUIST_PX / sizePx,
    readableWith2xSr: sizePx * SUPER_RES_FACTOR >= NYQUIST_PX,
  };
  l.explain = explainLegibility(l);
  return Object.freeze(l);
}

/** The same sentence gawaah/chilla.py `Legibility.explain()` produces. */
export function explainLegibility(l) {
  const verb = l.readable ? 'clears' : 'is short of';
  return `${l.feature}: ${l.sizeMm.toFixed(2)} mm at ${l.pxPerMm.toFixed(4)} px/mm `
    + `= ${l.sizePx.toFixed(2)} px, which ${verb} the ${l.nyquistPx.toFixed(1)} px `
    + `Nyquist floor by ${l.shortfallX.toFixed(2)}x. `
    + (l.readable
      ? 'It is above the floor and may be verified.'
      : 'It is not hard to read; it is not present in the signal.');
}

export const REFERENCE_STRING = legibility('UPI reference string (12sp)', REFERENCE_STRING_STROKE_MM);
export const SCREEN_TIMESTAMP = legibility('on-screen timestamp (12sp)', SCREEN_TIMESTAMP_STROKE_MM);
export const HERO_AMOUNT = legibility('hero amount (40sp)', HERO_AMOUNT_CAP_MM);
export const LEGIBILITY_ROWS = Object.freeze([REFERENCE_STRING, SCREEN_TIMESTAMP, HERO_AMOUNT]);

/** The headline sentence, with every number computed from the rig geometry. */
export const NYQUIST_SENTENCE =
  'We do NOT read the transaction reference. At this rig a UPI reference digit '
  + `stroke is ${REFERENCE_STRING_STROKE_MM.toFixed(2)}mm = ${REFERENCE_STRING.sizePx.toFixed(2)}px `
  + `against a ${NYQUIST_PX.toFixed(0)}px Nyquist floor — it is not hard to read, `
  + 'it is NOT IN THE SIGNAL. So we match on (amount, time window) instead.';

/** Fields CHILLA structurally refuses to read. An optics result, not a policy. */
export const NEVER_READ = Object.freeze([
  'reference_string', 'utr', 'rrn', 'screen_timestamp',
  'payer_name', 'payer_vpa', 'bank_last4',
]);

/**
 * Present so that any caller reaching for the UTR gets the arithmetic in the
 * stack trace instead of a plausible-looking string. Mirrors the Python
 * `read_reference_string` refusal.
 */
export function readReferenceString() { throw new ChillaRefusal(REFERENCE_STRING.explain); }
export function readScreenTimestamp() { throw new ChillaRefusal(SCREEN_TIMESTAMP.explain); }

// ===========================================================================
// 2. VERDICTS. Four of them, all amber.
// ===========================================================================

export const MATCHED = 'MATCHED';
export const NO_MATCH = 'NO_MATCH';
export const AMBIGUOUS = 'AMBIGUOUS';
export const AMBER_STALE = 'AMBER_STALE';
/** Not a CHILLA verdict — the panel's own "I do not know" display state. */
export const UNKNOWN = 'UNKNOWN';

export const VERDICTS = Object.freeze([MATCHED, NO_MATCH, AMBIGUOUS, AMBER_STALE]);
export const DISPLAY_VERDICTS = Object.freeze([...VERDICTS, UNKNOWN]);

/**
 * INVARIANT 2 AS A FUNCTION. Every verdict is amber. There is no argument that
 * makes this return 'GREEN' or 'RED'; unknown input throws rather than falls
 * through to a default colour.
 */
export function lightFor(verdict) {
  if (!DISPLAY_VERDICTS.includes(verdict)) {
    throw new RangeError(`not a CHILLA verdict: ${JSON.stringify(verdict)}`);
  }
  return 'AMBER';
}

/** What each verdict means, in the operator's words. */
export const VERDICT_NOTES = Object.freeze({
  [MATCHED]: 'exactly one captured payment of this amount inside the window. '
    + 'Corroboration, NOT settlement — only a signature-verified webhook settles.',
  [NO_MATCH]: 'no captured payment of this amount inside the window. AMBER, and '
    + 'never an accusation: the webhook may simply be late.',
  [AMBIGUOUS]: 'more than one captured payment of this amount inside the window. '
    + 'The composite key does not separate them, so CHILLA does not pick one.',
  [AMBER_STALE]: 'the settlement mirror is older than the staleness threshold. '
    + 'A stale mirror cannot corroborate, even when a row appears to match.',
  [UNKNOWN]: 'CHILLA has not returned a verdict for this frame.',
});

// ===========================================================================
// 3. DETECTION REASONS. Mirrors gawaah/chilla.py DETECTION_REASONS exactly;
//    panels2.test.mjs asserts set equality against the Python tuple.
// ===========================================================================

export const MIN_AREA_MM2 = 2500.0;
export const MAX_AREA_MM2 = 26000.0;
export const MIN_RECTANGULARITY = 0.80;
export const MIN_ASPECT = 1.15;
export const MAX_ASPECT = 3.20;
export const EDGE_MARGIN_MM = 2.0;
export const MAX_MASK_FRACTION = 0.35;
export const MIN_BRIGHTNESS_DELTA = 18.0;
export const MAX_ILLUM_COUPLING = 0.60;
export const PLACEMENT_BOX_MM = Object.freeze([68.5, 105.0, 228.5, 315.0]);

export const SCREEN_FOUND = 'screen_found';

export const DETECTION_REASONS = Object.freeze([
  'no_reference', 'buffer_shape_mismatch', 'global_illumination_shift',
  'no_bright_region', 'diff_saturated', 'all_regions_too_small',
  'too_small', 'too_large', 'not_rectangular', 'aspect_out_of_range',
  'touches_mat_edge', 'not_brighter_than_mat', 'reflective_not_emissive',
  'ambiguous_two_bright_quads', SCREEN_FOUND,
]);

export const ABSTENTION_REASONS = Object.freeze(
  DETECTION_REASONS.filter((r) => r !== SCREEN_FOUND));

export const REASON_NOTES = Object.freeze({
  no_reference: 'no empty-mat reference has been pushed, or it was cleared when '
    + 'the capture muted the track and AE/AWB reconverged.',
  buffer_shape_mismatch: 'the frame is not the rectified mat buffer. CHILLA only '
    + 'ever sees the rectified crop (invariant 4).',
  global_illumination_shift: 'the whole scene changed brightness, so every pixel '
    + '"differs". That is a re-baseline, not a phone.',
  no_bright_region: 'less changed than the noise floor. Nothing was put down.',
  diff_saturated: `more than ${(MAX_MASK_FRACTION * 100).toFixed(0)}% of the mat `
    + 'changed. Re-baseline; a phone does not cover a third of an A3 sheet.',
  all_regions_too_small: 'the mat changed, but every individual blob is under the '
    + 'noise floor: specks, highlights, foil.',
  too_small: `a real candidate whose measured rect is under ${MIN_AREA_MM2.toFixed(0)} mm2. `
    + 'Reported WITH the rect, so the operator sees the miss.',
  too_large: `measured rect over ${MAX_AREA_MM2.toFixed(0)} mm2: a tablet, a sheet, `
    + 'or the mat itself.',
  not_rectangular: `fill ratio under ${MIN_RECTANGULARITY}: the bright region is not `
    + 'a quadrilateral, so its rect is a fiction.',
  aspect_out_of_range: `long/short outside [${MIN_ASPECT}, ${MAX_ASPECT}]: not a `
    + 'handset silhouette.',
  touches_mat_edge: `within ${EDGE_MARGIN_MM} mm of the mat border, so the quad may `
    + 'be clipped and its size cannot be trusted.',
  not_brighter_than_mat: `mean brightness delta under ${MIN_BRIGHTNESS_DELTA} grey `
    + 'levels. NOTE: this gate measures BRIGHTNESS ONLY. Passing it does not '
    + 'establish emission.',
  reflective_not_emissive: `illumination coupling r >= ${MAX_ILLUM_COUPLING}: the `
    + 'patch tracks the lamp’s own gradient, which is what a diffuse reflector '
    + 'does and an emissive panel does not.',
  ambiguous_two_bright_quads: 'two plausible screens on the mat; abstain rather '
    + 'than pick one (invariant 7).',
  [SCREEN_FOUND]: 'a phone-sized, phone-shaped, brighter-than-mat quad that does '
    + 'not track the lamp. Geometry only — never pixels.',
});

/** Published next to the reasons, because an unpublished limit is a claim. */
export const LIMITATIONS = Object.freeze([
  'BRIGHTNESS IS NOT EMISSION. The brightness gate measures grey levels above the '
  + 'empty mat and nothing else. White paper under the lamp passes it.',
  'THE COUPLING TEST NEEDS A LIGHT GRADIENT. Under a flat light field the '
  + 'correlation is undefined and CHILLA reports coupling_measurable=false rather '
  + 'than pretending the test ran. Under uniform light, paper is NOT rejected.',
  'THE FAILURE DIRECTION IS ABSTENTION. A spurious coupling refuses a real screen '
  + '(amber); it never accepts a fake one.',
  'NONE OF THIS IS ANTI-SPOOF. A second phone playing a video of a payment screen '
  + 'is emissive and phone-shaped. CHILLA corroborates; it never decides.',
]);

export const HARD_RULE =
  'This panel can never show GREEN and can never show RED-as-fraud. Green comes '
  + 'only from a signature-verified webhook. NO_MATCH is AMBER — the likeliest '
  + 'cause is a slow webhook, not a liar.';

// ===========================================================================
// 4. THE COLLISION RISK OF THE COMPOSITE KEY
// ===========================================================================

/** The CHILLAR nonce is uniform over 01..99, never 00. */
export const CHILLAR_SPACE = 99;
export const DEFAULT_WINDOW_S = 180;
export const DEFAULT_STALE_THRESHOLD_S = 60.0;

/**
 * P(at least one OTHER payment in the window carries this exact amount).
 * Worst case by construction: assume every other payment in the window shares
 * our rupee part, so only the paise nonce separates them.
 *     P = 1 - ((k-1)/k) ** (n-1)
 * This is the number the AMBIGUOUS verdict exists to absorb, so the panel shows
 * it on every result rather than only when it is comfortable.
 */
export function collisionRisk(nInWindow, keySpace = CHILLAR_SPACE) {
  if (!(keySpace >= 1)) throw new RangeError(`key_space must be >= 1, got ${keySpace}`);
  const n = Math.trunc(nInWindow);
  if (!Number.isFinite(n) || n <= 1) return 0.0;
  return 1.0 - ((keySpace - 1) / keySpace) ** (n - 1);
}

/** Birthday form: P(ANY two payments in the window share an amount). */
export function anyCollisionRisk(nInWindow, keySpace = CHILLAR_SPACE) {
  if (!(keySpace >= 1)) throw new RangeError(`key_space must be >= 1, got ${keySpace}`);
  const n = Math.trunc(nInWindow);
  if (!Number.isFinite(n) || n <= 1) return 0.0;
  if (n > keySpace) return 1.0;
  let distinct = 1.0;
  for (let i = 0; i < n; i++) distinct *= (keySpace - i) / keySpace;
  return 1.0 - distinct;
}

/** Largest window occupancy whose collisionRisk stays within `target`. */
export function maxPaymentsForRisk(target, keySpace = CHILLAR_SPACE) {
  if (!(target > 0 && target < 1)) throw new RangeError(`target must be in (0,1), got ${target}`);
  let n = 1;
  while (collisionRisk(n + 1, keySpace) <= target) {
    n++;
    if (n > 10 * keySpace) break;
  }
  return n;
}

// ===========================================================================
// 5. THE WINDOW. Symmetric, because the customer's handset clock and ours are
//    not synchronised and a webhook can land either side of our frame grab.
// ===========================================================================

export function windowBounds(screenTs, windowSeconds = DEFAULT_WINDOW_S) {
  if (screenTs === null || screenTs === undefined || !Number.isFinite(Number(screenTs))) return null;
  const w = Math.trunc(windowSeconds);
  if (!(w > 0)) throw new RangeError(`windowSeconds must be positive, got ${windowSeconds}`);
  const t = Math.trunc(Number(screenTs));
  return Object.freeze({ lo: t - w, hi: t + w, spanS: 2 * w, centre: t, windowSeconds: w });
}

/** Unix seconds -> a stable UTC clock string. Never locale-dependent. */
export function fmtClock(unixS) {
  if (unixS === null || unixS === undefined || !Number.isFinite(Number(unixS))) return '—';
  return `${new Date(Math.trunc(Number(unixS)) * 1000).toISOString().slice(11, 19)}Z`;
}

export function fmtPct(x, dp = 2) {
  return Number.isFinite(x) ? `${(x * 100).toFixed(dp)}%` : '—';
}

// ===========================================================================
// 6. DERIVE — BrainState in, a fully-decided display model out. Pure.
// ===========================================================================

/** Number(null) is 0 and Number('') is 0. Neither is a reading. */
function num(v, dflt = null) {
  if (v === null || v === undefined || typeof v === 'boolean' || v === '') return dflt;
  const n = Number(v);
  return Number.isFinite(n) ? n : dflt;
}

function pick(o, ...names) {
  if (!o || typeof o !== 'object') return undefined;
  for (const n of names) if (o[n] !== undefined && o[n] !== null) return o[n];
  return undefined;
}

function coerceQuadMm(q) {
  if (!Array.isArray(q) || q.length !== 4) return null;
  const out = [];
  for (const p of q) {
    if (!Array.isArray(p) || p.length < 2) return null;
    const x = num(p[0]);
    const y = num(p[1]);
    if (x === null || y === null) return null;
    out.push([x, y]);
  }
  return out;
}

function coerceCandidate(c) {
  if (!c || typeof c !== 'object') return { paymentId: String(c ?? '?'), amountPaise: null, createdAt: null, status: null };
  return {
    paymentId: String(pick(c, 'paymentId', 'payment_id', 'id') ?? '?'),
    amountPaise: num(pick(c, 'amountPaise', 'amount_paise', 'amount'), null),
    createdAt: num(pick(c, 'createdAt', 'created_at'), null),
    status: pick(c, 'status') ?? null,
    method: pick(c, 'method') ?? null,
  };
}

/**
 * Build the display model. Never throws on shape: an absent or malformed field
 * becomes a NAMED abstention rather than a guess or a crash.
 *
 * Reads `state.chilla` (or a bare chilla-shaped object), tolerating both
 * snake_case (straight off the Python `as_dict()`) and camelCase.
 */
export function deriveChilla(state) {
  const abstentions = [];
  const note = (where, code, text) => { abstentions.push({ where, code, note: text }); };

  const c = (state && typeof state === 'object')
    ? (state.chilla ?? (state.match || state.detection ? state : null))
    : null;

  // ---- detection -------------------------------------------------------
  const d = pick(c, 'detection', 'screen') ?? null;
  let detection;
  if (!d) {
    detection = { present: false, found: false, reason: 'no_reading', note: 'CHILLA has not looked at a frame yet.' };
    note('detection', 'no_reading', 'CHILLA has not looked at a frame yet.');
  } else {
    const reason = String(pick(d, 'reason') ?? 'no_reading');
    const found = pick(d, 'found') === true;
    detection = {
      present: true,
      found,
      reason,
      known: DETECTION_REASONS.includes(reason),
      note: REASON_NOTES[reason] ?? `unrecognised detection reason '${reason}' — CHILLA will not translate a code it does not know.`,
      quadMm: coerceQuadMm(pick(d, 'quadMm', 'quad_mm')),
      quadBuf: coerceQuadMm(pick(d, 'quadBuf', 'quad_buf')),
      areaMm2: num(pick(d, 'areaMm2', 'area_mm2')),
      rectangularity: num(pick(d, 'rectangularity')),
      meanLuma: num(pick(d, 'meanLuma', 'mean_luma')),
      deltaLuma: num(pick(d, 'deltaLuma', 'delta_luma')),
      maskFraction: num(pick(d, 'maskFraction', 'mask_fraction')),
      nCandidates: num(pick(d, 'nCandidates', 'n_candidates')),
      inPlacementBox: pick(d, 'inPlacementBox', 'in_placement_box') === true,
      illumCoupling: num(pick(d, 'illumCoupling', 'illum_coupling')),
      refContrast: num(pick(d, 'refContrast', 'ref_contrast')),
      couplingMeasurable: pick(d, 'couplingMeasurable', 'coupling_measurable') === true,
    };
    if (!found) note('detection', reason, detection.note);
    if (!detection.known) note('detection', 'unknown_detection_reason', detection.note);
    if (found && !detection.couplingMeasurable) {
      note('detection', 'coupling_not_measurable',
        'the light field was too flat to run the reflective/emissive test. That is '
        + 'NOT evidence of emission — under uniform light, paper is not rejected.');
    }
    if (found && !detection.quadMm) {
      note('detection', 'quad_absent', 'a screen was found but no quad came with it; nothing to draw.');
    }
  }

  // ---- the match -------------------------------------------------------
  const m = pick(c, 'match', 'result') ?? null;
  const rawVerdict = m ? String(pick(m, 'verdict') ?? '') : '';
  const displayVerdict = VERDICTS.includes(rawVerdict) ? rawVerdict : UNKNOWN;
  if (m && displayVerdict === UNKNOWN) {
    note('verdict', 'unrecognised_verdict',
      `the brain sent verdict ${JSON.stringify(rawVerdict)}, which is not one of `
      + `${VERDICTS.join('/')}. The panel refuses to colour a code it does not know.`);
  } else if (!m) {
    note('verdict', 'no_verdict', 'CHILLA has not returned a match result for this frame.');
  }

  // ---- the amount ------------------------------------------------------
  const rawAmount = m ? pick(m, 'amountPaise', 'amount_paise') : undefined;
  let amount;
  if (rawAmount === undefined) {
    amount = { known: false, paise: null, text: 'I DO NOT KNOW', reason: 'amount_not_read' };
    note('amount', 'amount_not_read',
      'no hero amount has been read from the screen. The hero amount is the one '
      + 'field on that screen that is above the Nyquist floor; everything else is not.');
  } else {
    try {
      const p = paise(rawAmount);
      amount = { known: true, paise: p, text: formatRupees(p), reason: null };
    } catch (e) {
      const why = e instanceof MoneyError ? e.message : String(e && e.message);
      amount = { known: false, paise: null, text: 'I DO NOT KNOW', reason: 'amount_not_integer_paise' };
      note('amount', 'amount_not_integer_paise', `the amount is not integer paise: ${why}`);
    }
  }

  // ---- the window ------------------------------------------------------
  const screenTs = m ? num(pick(m, 'screenTs', 'screen_ts')) : null;
  const windowSeconds = Math.trunc(num(m ? pick(m, 'windowSeconds', 'window_seconds') : null, DEFAULT_WINDOW_S));
  const bounds = windowBounds(screenTs, windowSeconds > 0 ? windowSeconds : DEFAULT_WINDOW_S);
  const win = bounds
    ? {
      known: true,
      ...bounds,
      text: `${fmtClock(bounds.lo)} … ${fmtClock(bounds.hi)}  (±${bounds.windowSeconds}s, ${bounds.spanS}s span)`,
      reason: null,
    }
    : {
      known: false, lo: null, hi: null, spanS: null, centre: null,
      windowSeconds: windowSeconds > 0 ? windowSeconds : DEFAULT_WINDOW_S,
      text: 'I DO NOT KNOW — no capture time',
      reason: 'screen_ts_unknown',
    };
  if (m && !bounds) {
    note('window', 'screen_ts_unknown',
      'the capture time is unknown, so there is no window to search. We do not '
      + 'read the on-screen clock: at 0.19 mm it is below the Nyquist floor too.');
  }

  // ---- staleness, occupancy, collision ---------------------------------
  let mirrorAgeS = m ? num(pick(m, 'mirrorAgeS', 'mirror_age_s')) : null;
  if (mirrorAgeS === null && m) {
    const ms = num(pick(m, 'mirrorAgeMs', 'mirror_age_ms'));
    if (ms !== null) mirrorAgeS = ms < 0 ? Infinity : ms / 1000;
  }
  const staleThresholdS = num(pick(c, 'staleThresholdS', 'stale_threshold_s'), DEFAULT_STALE_THRESHOLD_S);
  const stale = mirrorAgeS === null ? null : !(mirrorAgeS <= staleThresholdS);
  if (m && mirrorAgeS === null) {
    note('mirror', 'mirror_age_unknown',
      'the mirror age is unknown, so freshness cannot be asserted. Treat as stale.');
  }

  const keySpace = Math.trunc(num(m ? pick(m, 'keySpace', 'key_space') : null, CHILLAR_SPACE));
  const nInWindow = Math.trunc(num(m ? pick(m, 'nInWindow', 'n_in_window') : null, 0));
  const reportedRisk = m ? num(pick(m, 'collisionRisk', 'collision_risk')) : null;
  const computedRisk = collisionRisk(nInWindow, keySpace >= 1 ? keySpace : CHILLAR_SPACE);
  // The panel shows ITS OWN arithmetic. It has n and k, the formula is four
  // characters long, and a display that merely echoes a number it could have
  // checked is not showing the operator anything. A brain figure that disagrees
  // is printed beside it as a disagreement, never silently substituted.
  const risk = computedRisk;
  const riskDisagrees = reportedRisk !== null && Math.abs(reportedRisk - computedRisk) > 1e-6;
  if (riskDisagrees) {
    note('collision', 'collision_risk_disagrees',
      `the brain reported ${reportedRisk} but this panel computes ${computedRisk} from `
      + `n=${nInWindow}, k=${keySpace}. Showing both; believing neither.`);
  }

  const rawCands = m ? (pick(m, 'candidates') ?? pick(m, 'candidateIds', 'candidate_ids') ?? []) : [];
  const candidates = Array.isArray(rawCands) ? rawCands.map(coerceCandidate) : [];
  if (displayVerdict === AMBIGUOUS && candidates.length < 2) {
    note('candidates', 'ambiguous_without_candidates',
      'the verdict is AMBIGUOUS but fewer than two candidates arrived, so the panel '
      + 'cannot show what collided.');
  }

  const match = {
    present: Boolean(m),
    verdict: displayVerdict,
    rawVerdict: rawVerdict || null,
    light: lightFor(displayVerdict),
    reason: m ? (pick(m, 'reason') ?? null) : null,
    note: VERDICT_NOTES[displayVerdict],
    nInWindow,
    candidates,
    mirrorAgeS,
    staleThresholdS,
    stale,
    keySpace: keySpace >= 1 ? keySpace : CHILLAR_SPACE,
  };

  return Object.freeze({
    id: PANEL_ID,
    title: PANEL_TITLE,
    light: 'AMBER',
    nyquistSentence: NYQUIST_SENTENCE,
    legibility: LEGIBILITY_ROWS,
    neverRead: NEVER_READ,
    limitations: LIMITATIONS,
    hardRule: HARD_RULE,
    detection,
    amount,
    window: win,
    match,
    collision: Object.freeze({
      n: nInWindow,
      keySpace: match.keySpace,
      risk,
      computedRisk,
      reportedRisk,
      disagrees: riskDisagrees,
      pct: fmtPct(risk),
      anyPct: fmtPct(anyCollisionRisk(nInWindow, match.keySpace)),
      safeOccupancy: maxPaymentsForRisk(0.05, match.keySpace),
      text: `1 - (${match.keySpace - 1}/${match.keySpace})^(${nInWindow}-1) = ${fmtPct(risk)} `
        + 'chance another payment in this window carries this exact amount',
    }),
    abstentions: Object.freeze(abstentions.map((a) => Object.freeze(a))),
  });
}

// ===========================================================================
// 7. RENDER. Pure: (model, doc) -> element. No globals, no side effects.
// ===========================================================================

/** The whole palette this panel is allowed to use. No green. No red. */
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

const SVG_NS = 'http://www.w3.org/2000/svg';
function svg(doc, tag, attrs = {}) {
  const el = doc.createElementNS ? doc.createElementNS(SVG_NS, tag) : doc.createElement(tag);
  if (el.setAttribute) for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}

/** SVG/canvas path data for a 4-point quad. Pure string arithmetic. */
export function quadPathD(quad) {
  const q = coerceQuadMm(quad);
  if (!q) return null;
  return `M ${q.map((p) => `${p[0].toFixed(2)} ${p[1].toFixed(2)}`).join(' L ')} Z`;
}

/**
 * The mat mini-map: A3 outline, the advisory ROKO placement box, and the
 * detected quad if there is one. Millimetres, so the viewBox IS the mat.
 */
export function renderMiniMap(model, doc) {
  // NOTE: SVG classes go on via setAttribute ONLY. `SVGElement.className` is a
  // read-only SVGAnimatedString, so assigning to it throws a TypeError under
  // the strict mode every ES module runs in. Caught by rendering this file to
  // HTML and reading the output, not by any test that only walks the tree.
  const s = svg(doc, 'svg', {
    class: 'chilla-map',
    viewBox: `0 0 ${MAT_W_MM} ${MAT_H_MM}`,
    role: 'img',
    'aria-label': 'detected screen quad on the rectified mat',
  });
  s.appendChild(svg(doc, 'rect', {
    x: 0, y: 0, width: MAT_W_MM, height: MAT_H_MM,
    fill: 'none', stroke: PALETTE.line, 'stroke-width': 2, class: 'chilla-map-mat',
  }));
  const [x0, y0, x1, y1] = PLACEMENT_BOX_MM;
  s.appendChild(svg(doc, 'rect', {
    x: x0, y: y0, width: x1 - x0, height: y1 - y0,
    fill: 'none', stroke: PALETTE.amberDim, 'stroke-width': 1.5,
    'stroke-dasharray': '6 6', class: 'chilla-map-box',
  }));
  const d = quadPathD(model.detection.quadMm);
  if (d) {
    s.appendChild(svg(doc, 'path', {
      d, fill: 'none', stroke: PALETTE.amber, 'stroke-width': 3, class: 'chilla-map-quad',
    }));
  } else {
    const t = svg(doc, 'text', {
      x: MAT_W_MM / 2, y: MAT_H_MM / 2, 'text-anchor': 'middle',
      fill: PALETTE.mute, 'font-size': 18, class: 'chilla-map-none',
    });
    t.textContent = 'no screen quad';
    s.appendChild(t);
  }
  return s;
}

function renderLegibility(model, doc) {
  const ul = mk(doc, 'ul', { class: 'chilla-legibility' });
  for (const l of model.legibility) {
    ul.appendChild(mk(doc, 'li', {
      class: `chilla-leg ${l.readable ? 'chilla-leg-above' : 'chilla-leg-below'}`,
      data: { readable: l.readable, sizePx: l.sizePx.toFixed(2) },
      text: l.explain,
    }));
  }
  return ul;
}

function renderDetection(model, doc) {
  const d = model.detection;
  const kids = [
    mk(doc, 'h3', { class: 'panel-h3', text: 'screen detection (geometry only, never pixels)' }),
    renderMiniMap(model, doc),
    row(doc, 'reason', d.reason, { data: { reason: d.reason } }),
    mk(doc, 'p', { class: 'chilla-reason-note', text: d.note }),
  ];
  if (d.present) {
    kids.push(
      row(doc, 'area', d.areaMm2 === null ? 'unknown' : `${d.areaMm2.toFixed(1)} mm²`),
      row(doc, 'rectangularity', d.rectangularity === null ? 'unknown' : d.rectangularity.toFixed(4)),
      row(doc, 'Δ luma vs empty mat', d.deltaLuma === null ? 'unknown' : `${d.deltaLuma.toFixed(2)} grey levels`),
      row(doc, 'in placement box', d.inPlacementBox ? 'yes (advisory only)' : 'no (advisory only)'),
      row(doc, 'illumination coupling r',
        d.couplingMeasurable && d.illumCoupling !== null
          ? d.illumCoupling.toFixed(4)
          : 'NOT MEASURABLE — the light field was too flat to run the test',
        { data: { measurable: d.couplingMeasurable } }),
    );
  }
  return mk(doc, 'section', {
    class: 'chilla-detection',
    data: { found: d.found, reason: d.reason },
    kids,
  });
}

function renderVerdict(model, doc) {
  const v = model.match.verdict;
  const chip = mk(doc, 'div', {
    // NOTE the class suffix: the light, not the verdict. There is no CSS hook
    // here that a stylesheet could paint green, because there is no verdict in
    // this panel whose light is anything but amber.
    class: 'chilla-verdict chilla-verdict-amber',
    data: { verdict: v, light: model.match.light },
    style: { color: PALETTE.amber, borderColor: PALETTE.amber },
    text: v === UNKNOWN ? 'I DO NOT KNOW' : v,
  });
  const kids = [
    mk(doc, 'h3', { class: 'panel-h3', text: 'verdict' }),
    chip,
    mk(doc, 'div', { class: 'chilla-light', data: { light: model.match.light }, text: `light: ${model.match.light}` }),
    mk(doc, 'p', { class: 'chilla-verdict-note', text: model.match.note }),
  ];
  if (model.match.reason) {
    kids.push(mk(doc, 'p', { class: 'chilla-verdict-reason', text: model.match.reason }));
  }
  if (model.match.stale === true) {
    kids.push(mk(doc, 'p', {
      class: 'chilla-stale',
      data: { stale: 'true' },
      text: `mirror is ${Number.isFinite(model.match.mirrorAgeS) ? `${model.match.mirrorAgeS.toFixed(1)}s` : 'of unknown age'}`
        + ` (> ${model.match.staleThresholdS}s threshold) — AMBER even if a row matches.`,
    }));
  }
  return mk(doc, 'section', { class: 'chilla-verdict-wrap', kids });
}

function renderCandidates(model, doc) {
  const ol = mk(doc, 'ol', { class: 'chilla-candidates' });
  if (model.match.candidates.length === 0) {
    ol.appendChild(mk(doc, 'li', {
      class: 'chilla-candidate chilla-candidate-none',
      text: 'no candidate rows',
    }));
    return ol;
  }
  for (const cand of model.match.candidates) {
    let amt = 'amount unknown';
    if (cand.amountPaise !== null) {
      try { amt = formatRupees(paise(cand.amountPaise)); } catch { amt = 'amount not integer paise'; }
    }
    ol.appendChild(mk(doc, 'li', {
      class: 'chilla-candidate',
      data: { paymentId: cand.paymentId },
      kids: [
        mk(doc, 'span', { class: 'cand-id', text: cand.paymentId }),
        mk(doc, 'span', { class: 'cand-amt', text: amt }),
        mk(doc, 'span', { class: 'cand-t', text: fmtClock(cand.createdAt) }),
        mk(doc, 'span', { class: 'cand-status', text: cand.status ?? 'status unknown' }),
      ],
    }));
  }
  return ol;
}

function renderAbstentions(model, doc) {
  const box = mk(doc, 'section', {
    class: 'chilla-abstentions',
    data: { count: model.abstentions.length },
    kids: [mk(doc, 'h3', { class: 'panel-h3', text: `I do not know (${model.abstentions.length})` })],
  });
  if (model.abstentions.length === 0) {
    box.appendChild(mk(doc, 'p', { class: 'chilla-abstain-none', text: 'nothing is being withheld on this frame.' }));
    return box;
  }
  const ul = mk(doc, 'ul', { class: 'chilla-abstain-list' });
  for (const a of model.abstentions) {
    ul.appendChild(mk(doc, 'li', {
      class: 'chilla-abstain',
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

/** (model, doc) -> the panel's whole subtree. Called for every state update. */
export function renderChilla(model, doc = globalThis.document) {
  if (!doc || typeof doc.createElement !== 'function') {
    throw new TypeError('renderChilla needs a document-like object with createElement');
  }
  const root = mk(doc, 'section', {
    class: 'panel panel-chilla',
    data: {
      panel: 'chilla',
      light: 'AMBER',
      verdict: model.match.verdict,
      neverGreen: 'true',
      neverRed: 'true',
      abstentions: model.abstentions.length,
    },
    kids: [
      mk(doc, 'header', {
        class: 'panel-head',
        kids: [
          mk(doc, 'h2', { class: 'panel-title', text: model.title }),
          mk(doc, 'span', {
            class: 'panel-light panel-light-amber',
            data: { light: 'AMBER' },
            style: { color: PALETTE.amber },
            text: 'AMBER',
          }),
        ],
      }),

      // The sentence. Rendered first, before any number, because it is the
      // reason all the other numbers are the ones they are.
      mk(doc, 'p', { class: 'chilla-nyquist', text: model.nyquistSentence }),
      renderLegibility(model, doc),
      mk(doc, 'p', {
        class: 'chilla-neverread',
        text: `never read off the screen: ${model.neverRead.join(', ')}`,
      }),

      renderDetection(model, doc),

      mk(doc, 'section', {
        class: 'chilla-key',
        kids: [
          mk(doc, 'h3', { class: 'panel-h3', text: 'the composite key' }),
          row(doc, 'amount read', model.amount.text, {
            class: model.amount.known ? 'kv-known' : 'kv-unknown',
            data: { known: model.amount.known, reason: model.amount.reason ?? '' },
          }),
          row(doc, 'ledger window searched', model.window.text, {
            class: model.window.known ? 'kv-known' : 'kv-unknown',
            data: { known: model.window.known, reason: model.window.reason ?? '' },
          }),
          row(doc, 'payments in window', String(model.collision.n)),
        ],
      }),

      renderVerdict(model, doc),

      mk(doc, 'section', {
        class: 'chilla-collision',
        data: { risk: model.collision.risk.toFixed(6), n: model.collision.n, k: model.collision.keySpace },
        kids: [
          mk(doc, 'h3', { class: 'panel-h3', text: 'collision risk of this key' }),
          mk(doc, 'div', { class: 'chilla-risk-big', text: model.collision.pct }),
          mk(doc, 'p', { class: 'chilla-risk-formula', text: model.collision.text }),
          row(doc, 'any-pair risk in window', model.collision.anyPct),
          row(doc, 'occupancy holding risk ≤ 5%', `${model.collision.safeOccupancy} payments`),
          model.collision.disagrees
            ? mk(doc, 'p', {
              class: 'chilla-risk-disagrees',
              text: `brain reported ${model.collision.reportedRisk}, panel computes ${model.collision.computedRisk}`,
            })
            : null,
        ],
      }),

      mk(doc, 'section', {
        class: 'chilla-cands-wrap',
        kids: [
          mk(doc, 'h3', { class: 'panel-h3', text: `candidates (${model.match.candidates.length})` }),
          renderCandidates(model, doc),
        ],
      }),

      renderAbstentions(model, doc),

      mk(doc, 'ul', {
        class: 'chilla-limitations',
        kids: model.limitations.map((t) => mk(doc, 'li', { class: 'chilla-limit', text: t })),
      }),

      // The hard rule, last and permanent. Present in every render, in every
      // state, whether or not anything was detected.
      mk(doc, 'p', {
        class: 'chilla-hardrule',
        data: { hardRule: 'amber-only-never-accuses' },
        text: model.hardRule,
      }),
    ],
  });
  return root;
}

// ===========================================================================
// 8. THE FRAME OVERLAY. Strokes the detected quad onto the RECTIFIED view.
//    Amber only — the colour is not a parameter, so it cannot become green.
// ===========================================================================

export const OVERLAY_STROKE = PALETTE.amber;

function ctxFrom(frame) {
  if (!frame) return null;
  if (frame.rectCtx) return frame.rectCtx;
  if (frame.ctx) return frame.ctx;
  const canvas = frame.rect || frame.rectified || frame.canvas;
  if (canvas && typeof canvas.getContext === 'function') return canvas.getContext('2d');
  return null;
}

/**
 * Draw the quad in RECTIFIED BUFFER pixels. Returns false — not a throw and not
 * a silent success — when there is nothing to draw, so a caller can tell the
 * difference between "abstained" and "drew".
 */
export function drawScreenQuad(ctx, quadBuf, label = '') {
  const q = coerceQuadMm(quadBuf);
  if (!ctx || !q) return false;
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(q[0][0], q[0][1]);
  for (let i = 1; i < q.length; i++) ctx.lineTo(q[i][0], q[i][1]);
  ctx.closePath();
  ctx.strokeStyle = OVERLAY_STROKE;
  ctx.lineWidth = 4;
  ctx.setLineDash([14, 10]);   // dashed: a corroboration, never a confirmation
  ctx.stroke();
  if (label) {
    ctx.setLineDash([]);
    ctx.fillStyle = OVERLAY_STROKE;
    ctx.font = '28px system-ui';
    ctx.textAlign = 'center';
    ctx.fillText(label, (q[0][0] + q[2][0]) / 2, q[0][1] - 12);
  }
  ctx.restore();
  return true;
}

// ===========================================================================
// 9. THE PANEL OBJECT. registerPanel(id, {onState, onFrame}).
// ===========================================================================

export function createPanel(opts = {}) {
  const doc = opts.doc ?? opts.document ?? globalThis.document;
  let root = opts.root ?? opts.host ?? null;
  let model = deriveChilla(null);

  const resolveRoot = () => {
    if (root) return root;
    if (doc && typeof doc.getElementById === 'function') root = doc.getElementById(PANEL_ID);
    return root;
  };

  return {
    id: PANEL_ID,
    title: PANEL_TITLE,
    get model() { return model; },

    /** Rebuild the subtree from state. Returns false if there is no host node. */
    onState(state) {
      model = deriveChilla(state);
      const host = resolveRoot();
      if (!host || typeof host.replaceChildren !== 'function') return false;
      host.replaceChildren(renderChilla(model, doc));
      if (host.dataset) {
        host.dataset.verdict = model.match.verdict;
        host.dataset.light = model.match.light;
      }
      return true;
    },

    /** Overlay the detected quad on the rectified crop, if we have one. */
    onFrame(frame) {
      const ctx = ctxFrom(frame);
      if (!ctx) return false;
      return drawScreenQuad(ctx, model.detection.quadBuf,
        model.match.verdict === UNKNOWN ? '' : model.match.verdict);
    },
  };
}

/** Register with the host shell. Throws loudly rather than failing quietly. */
export function attach(register, opts = {}) {
  if (typeof register !== 'function') {
    throw new TypeError('attach(register): registerPanel must be a function');
  }
  const panel = createPanel(opts);
  register(PANEL_ID, panel);
  return panel;
}

/**
 * The other shape the shell may call: `attachXPanel(opts)`, finding the
 * registrar rather than being handed it. web/panels/mudra.js and peel.js use
 * this convention; both are supported here so whoever wires the shell does not
 * have to reconcile two of them. Registering nothing is a reported outcome
 * (`registered: false`), never a silent one.
 */
export function attachChillaPanel(opts = {}) {
  const panel = createPanel(opts);
  const register = typeof opts.register === 'function'
    ? opts.register
    : (typeof globalThis.registerPanel === 'function' ? globalThis.registerPanel : null);
  const registration = register
    ? register(PANEL_ID, { onState: panel.onState, onFrame: panel.onFrame })
    : null;
  return { panel, registered: register !== null, registration };
}

/**
 * Two ways in, because this module does not own the shell that loads it:
 *   1. the shell imports { attach } and hands over its registerPanel; or
 *   2. the shell sets globalThis.registerPanel before loading the panels, or
 *      drains globalThis.GAWAAH_PANELS afterwards.
 * `attached` records which happened, so a shell that does both does not end up
 * with two live copies of the same panel.
 */
export const DESCRIPTOR = { id: PANEL_ID, title: PANEL_TITLE, createPanel, attach, attached: false };
if (typeof globalThis !== 'undefined') {
  if (typeof globalThis.registerPanel === 'function') {
    attach(globalThis.registerPanel);
    DESCRIPTOR.attached = true;
  }
  (globalThis.GAWAAH_PANELS ||= []).push(DESCRIPTOR);
}

export default { PANEL_ID, createPanel, attach, deriveChilla, renderChilla };
