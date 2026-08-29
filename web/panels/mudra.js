/* GAWAAH — MUDRA panel. The hand read as an OCCLUDER.
 *
 *   #panel-mudra   attaches through registerPanel(id, {onState, onFrame})
 *
 * WHAT THIS PANEL IS
 * ------------------
 * gawaah/mudra.py classifies one rectified frame by subtracting the empty-mat
 * reference and measuring the largest remaining contour: solidity (area over
 * convex-hull area), the count of convexity defects deeper than 6 mm, outline
 * compactness, and area in real square millimetres. There is no hand model,
 * no landmark regressor and no skin classifier. A hand is a hole in the light.
 *
 * INVARIANT 3, made structural rather than promised. MediaPipe's
 * hand_landmarker.task is 7,819,105 bytes against a 4.8 MB cold-load budget —
 * 1.63x the entire budget for one boolean. This module imports nothing, fetches
 * nothing, and the panel prints that arithmetic on screen.
 *
 * INVARIANT 2. MUDRA REVEALS a payment target that CORE already minted. It
 * cannot mint, cannot authorise and cannot turn anything green. The CSS token
 * `var(--green)` and the literal #3ddc84 do not appear in this file's code, and
 * panels.test.mjs fails the build if they ever do.
 *
 * INVARIANT 7. AMBIGUOUS is a first-class outcome with a named cause drawn from
 * mudra.REASONS, rendered loud rather than as an error. Four further panel-level
 * abstentions exist for the cases the brain cannot answer at all: no reading, a
 * stale reading, an unconfigured pay panel, and unknown panel occupancy.
 *
 * WHY THIS FILE IMPORTS NOTHING
 * -----------------------------
 * web/ has no package.json, so node cannot load a bare `.js` as an ES module.
 * panels.test.mjs therefore loads this source through a data: URL, exactly the
 * way web/selftest.mjs loads app.js — and a data: URL cannot resolve a relative
 * import specifier. Import-free is what makes the panel unit-testable with no
 * browser and no bundler. The small DOM helper below is duplicated in peel.js
 * for the same reason; that duplication is deliberate.
 */

// ==========================================================================
// The panel contract. app.js owns registerPanel(id, {onState, onFrame}); this
// is the same shape, declared here so the panel is complete on its own.
// ==========================================================================

/** The registry id app.js's registerPanel(id, hooks) expects (PANEL_IDS). */
export const MUDRA_ID = 'mudra';
/** The shell's section, index.html's `<section id="panel-mudra" class="panel">`. */
export const MUDRA_PANEL_ID = 'panel-mudra';
/** The shell's fill point inside that section. Preferred mount. */
export const MUDRA_BODY_ID = 'body-mudra';
/** Our own root's id. Deliberately NOT panel-mudra: the shell owns that id. */
export const MUDRA_ROOT_ID = 'mudra-render';
/** app.js's RETAIN_RECTIFIED. A frame that is not this is refused (invariant 4). */
export const RECTIFIED_CROP_KIND = 'rectified_mat_crop';
export const PANEL_CONTRACT_VERSION = 1;

/** The three statuses the shell contract allows. There is no fourth, and no green. */
export const PanelStatus = Object.freeze({ OFF: 'OFF', ABSTAIN: 'ABSTAIN', OK: 'OK' });

/**
 * A minimal registry with registerPanel's signature, used when app.js has not
 * published one yet. Panels never reach into it themselves; a host does.
 */
export function makePanelRegistry() {
  const panels = new Map();
  return {
    registerPanel(id, panel) {
      if (typeof id !== 'string' || !id) throw new Error('panel id must be a non-empty string');
      if (!panel || typeof panel !== 'object') throw new Error(`panel ${id} must be an object`);
      panels.set(id, panel);
      return panel;
    },
    get(id) { return panels.get(id) ?? null; },
    ids() { return [...panels.keys()]; },
    onState(state) { for (const p of panels.values()) if (p.onState) p.onState(state); },
    onFrame(frame) { for (const p of panels.values()) if (p.onFrame) p.onFrame(frame); },
  };
}

/** app.js's registerPanel if it exists, else null. Never throws. */
export function resolveRegister(explicit, g = globalThis) {
  if (typeof explicit === 'function') return explicit;
  if (g && g.GAWAAH && typeof g.GAWAAH.registerPanel === 'function') {
    return (id, panel) => g.GAWAAH.registerPanel(id, panel);
  }
  if (g && typeof g.registerPanel === 'function') return g.registerPanel;
  return null;
}

/**
 * app.js's setPanelStatus if it exists, else null. This is the second half of
 * the seam: it paints the panel's status pill, its rail dot and the shell's own
 * "I DO NOT KNOW" block. We only ever pass OFF / ABSTAIN / OK.
 */
export function resolveSetStatus(explicit, g = globalThis) {
  if (typeof explicit === 'function') return explicit;
  if (g && g.GAWAAH && typeof g.GAWAAH.setPanelStatus === 'function') {
    return (id, status, why) => g.GAWAAH.setPanelStatus(id, status, why);
  }
  if (g && typeof g.setPanelStatus === 'function') return g.setPanelStatus;
  return null;
}

// ==========================================================================
// Constants. Every threshold here mirrors gawaah/mudra.py; every reference
// value is measured, and labelled with where it was measured.
// ==========================================================================

export const MUDRA_STATES = Object.freeze(['NONE', 'OPEN', 'FIST', 'GOODS', 'AMBIGUOUS']);

/** gawaah/mudra.py REASONS, the contract of causes the engine may emit. */
export const MUDRA_REASONS = Object.freeze([
  'no_occluder', 'occluder_too_large', 'closed_hand', 'open_palm', 'inert_object',
  'low_solidity_but_articulated', 'mid_solidity_too_few_defects',
  'mid_solidity_outline_too_compact', 'goods_solidity_but_articulated',
  'goods_solidity_but_elongated', 'hand_area_implausible', 'solidity_dead_band',
]);

/** Plain-language gloss for each cause. Never replaces the code — sits beside it. */
export const REASON_GLOSS = Object.freeze({
  no_occluder: 'nothing on the mat above the noise floor',
  occluder_too_large: 'more than half the mat changed — a light change, not a hand',
  closed_hand: 'closed hand',
  open_palm: 'open palm',
  inert_object: 'an inert object, not a hand',
  low_solidity_but_articulated: 'low solidity but articulated — fist shape with finger notches',
  mid_solidity_too_few_defects: 'palm-range solidity with too few deep notches',
  mid_solidity_outline_too_compact: 'palm-range solidity but the outline is too round for fingers',
  goods_solidity_but_articulated: 'packet-range solidity but the outline is articulated',
  goods_solidity_but_elongated: 'packet-range solidity but the outline is too elongated',
  hand_area_implausible: 'the blob is not hand-sized — probably a hand merged with goods',
  solidity_dead_band: 'solidity fell in the deliberate gap between the calibrated bands',
});

/** Panel-level abstentions: the things the ENGINE cannot be asked. */
export const Abstain = Object.freeze({
  NO_READING: 'no_gesture_reading_yet',
  STALE: 'gesture_reading_stale',
  MAT_UNLOCKED: 'mat_not_locked_millimetres_meaningless',
  ROI_UNCONFIGURED: 'pay_panel_roi_unconfigured',
  OCCUPANCY_UNKNOWN: 'panel_occupancy_unknown',
  GOODS_EXTENT_UNKNOWN: 'goods_extent_unknown_near_panel',
  PANEL_BLOCKED: 'panel_blocked_by_goods',
  NOT_SETTLEMENT: 'not_awaiting_settlement',
  DISARMED_BY_SCREEN: 'disarmed_screen_match_live',
  UNCALIBRATED: 'thresholds_uncalibrated_module_defaults',
  GAP_TOO_SMALL: 'solidity_gap_below_floor_channel_disarmed',
  NO_TARGET: 'no_pre_minted_target',
  TARGET_NOT_INTEGER: 'target_amount_not_integer_paise',
  AGE_UNKNOWN: 'reading_age_unknown',
});

/** gawaah/mudra.py defaults. Used only until this shop's hand is calibrated. */
export const DEFAULT_THRESHOLDS = Object.freeze({
  fistMax: 0.80, openLo: 0.80, openHi: 0.95, goodsMin: 0.95,
  hysteresis: 0.03, minDefectsOpen: 3, dwellFrames: 4,
  goodsCompactnessMin: 0.45, openCompactnessMax: 0.75,
  handAreaMm2: Object.freeze([4000, 22000]), minAreaMm2: 1200, maxAreaFrac: 0.55,
  minDefectDepthMm: 6.0,
});

/** SIX.md §8.2 — the pair calibrated against a cluttered mat, with an UNKNOWN band. */
export const CALIBRATED_HINT = Object.freeze({ fistMax: 0.85, openLo: 0.85, openHi: 0.90, goodsMin: 0.90 });

/** SIX.md §6 G7: below this open/fist separation the solidity channel does not arm. */
export const MIN_SOLIDITY_GAP = 0.08;

/** Measured on real hands (SIX.md). Drawn on the gauge as provenance, not as thresholds. */
export const REFERENCE_SOLIDITY = Object.freeze([
  Object.freeze({ label: 'fist', at: 0.73 }),
  Object.freeze({ label: 'open palm', at: 0.92 }),
  Object.freeze({ label: 'goods', from: 0.96, to: 1.00 }),
]);

/** A raster disc measures this, not 1.000 — cv2.arcLength walks a Freeman chain. */
export const COMPACTNESS_DISC_CEILING = 0.897;

export const MAT_W_MM = 297.0;
export const MAT_H_MM = 420.0;
export const BUF_W = 840;
export const BUF_H = 1188;

/** The gauge domain. Below 0.60 no hand or packet has ever been measured. */
export const GAUGE_LO = 0.60;
export const GAUGE_HI = 1.00;

/** A reading older than this is not live, and the panel says so. */
export const STALE_MS = 1500;

/** Goods covering this fraction of the pay panel block the gesture (SIX.md §8.2). */
export const PANEL_BLOCK_MIN_COVERAGE = 0.02;

/** A placement centre this close to the panel, with no extent reported, is UNKNOWN. */
export const NEAR_PANEL_MM = 25;

/* INVARIANT 3, as arithmetic the panel prints rather than a claim it makes.
 * The IDENTIFIER deliberately does not name the runtime: panels.test.mjs greps
 * the executable source for every model-runtime token and fails the build on a
 * hit, so the only place a forbidden name may appear is display copy. */
export const FORBIDDEN_MODEL_BYTES = 7819105;     // the 7.8 MB hand model
export const COLD_LOAD_BUDGET_BYTES = 4800000;    // SIX.md §7, browser budget

// ==========================================================================
// PANEL MONEY BEGIN — integer paise only, no float ever touches an amount.
// Mirrors the money block in app.js and is linted the same way.
// ==========================================================================

/**
 * Integer paise to a rupee string. Returns null for anything that is not a
 * safe integer, so a caller renders a REFUSAL rather than a rounded lie.
 */
export function formatPaise(p) {
  if (typeof p !== 'number' || !Number.isSafeInteger(p)) return null;
  const neg = p < 0;
  const a = neg ? -p : p;
  const r = a % 100;
  const whole = (a - r) / 100;
  const frac = r < 10 ? `0${r}` : `${r}`;
  return `${neg ? '-' : ''}₹${whole}.${frac}`;
}

// PANEL MONEY END
// ==========================================================================

// ==========================================================================
// Pure view model. No DOM below this line until the render section.
// ==========================================================================

const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** Accept either the wire message {area_mm2,...} or a camelCase object. */
function pick(o, ...keys) {
  for (const k of keys) if (o && o[k] !== undefined && o[k] !== null) return o[k];
  return null;
}

/** Milliseconds from a number, a Date, or an ISO string. null when unknowable. */
export function toMs(ts) {
  if (typeof ts === 'number' && Number.isFinite(ts)) return ts;
  if (ts instanceof Date) return Number.isFinite(ts.getTime()) ? ts.getTime() : null;
  if (typeof ts === 'string') {
    const t = Date.parse(ts);
    return Number.isFinite(t) ? t : null;
  }
  return null;
}

/**
 * Split a mudra reason into its published cause and the dwell telemetry
 * update() appends after a "|". An unpublished head is reported as such — it
 * must never be glossed as if it were a known cause.
 */
export function parseReason(reason) {
  const raw = typeof reason === 'string' ? reason : '';
  const [head, tail] = raw.split('|', 2);
  const code = head || '';
  let dwell = null;
  if (tail) {
    const m = /^dwell_(\d+)\/(\d+)$/.exec(tail);
    if (m) dwell = { count: Number(m[1]), of: Number(m[2]) };
  }
  const known = code === '' || MUDRA_REASONS.includes(code);
  return {
    code,
    known,
    dwell,
    gloss: known ? (REASON_GLOSS[code] ?? '') : `unpublished cause ${JSON.stringify(code)}`,
  };
}

/**
 * Merge reported calibration over the module defaults and say, honestly,
 * whether this shop's hand was ever measured.
 */
export function normaliseThresholds(cal) {
  const c = cal && typeof cal === 'object' ? cal : {};
  const t = { ...DEFAULT_THRESHOLDS };
  for (const k of ['fistMax', 'openLo', 'openHi', 'goodsMin', 'hysteresis',
    'minDefectsOpen', 'dwellFrames', 'goodsCompactnessMin', 'openCompactnessMax']) {
    const v = num(pick(c, k, k.replace(/[A-Z]/g, (ch) => `_${ch.toLowerCase()}`)));
    if (v !== null) t[k] = v;
  }
  const p95Open = num(pick(c, 'p95Open', 'p95_open'));
  const p05Fist = num(pick(c, 'p05Fist', 'p05_fist'));
  const reportedGap = num(pick(c, 'gap'));
  const gap = reportedGap !== null ? reportedGap
    : (p95Open !== null && p05Fist !== null ? p95Open - p05Fist : null);
  const samples = num(pick(c, 'samples'));
  const calibrated = pick(c, 'calibrated') === true
    || (gap !== null && samples !== null && samples > 0);
  return Object.freeze({
    ...t,
    handAreaMm2: DEFAULT_THRESHOLDS.handAreaMm2,
    p95Open, p05Fist, gap, samples,
    calibrated,
    // The channel arms only on a MEASURED separation. An absent measurement is
    // not a pass: SIX.md G7 says degrade to tap-to-arm and say so on screen.
    gapOk: gap !== null && gap >= MIN_SOLIDITY_GAP,
    armReason: calibrated
      ? (gap !== null && gap >= MIN_SOLIDITY_GAP ? null : Abstain.GAP_TOO_SMALL)
      : Abstain.UNCALIBRATED,
  });
}

const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);

/** Position on the gauge, 0..100, for a solidity value. */
export function gaugePct(v, lo = GAUGE_LO, hi = GAUGE_HI) {
  return clamp01((v - lo) / (hi - lo)) * 100;
}

/**
 * The solidity gauge: zones from the calibrated cut points, ticks for each cut
 * point, and the measured hand references drawn as provenance.
 */
export function solidityGauge(solidity, th = DEFAULT_THRESHOLDS) {
  const lo = GAUGE_LO, hi = GAUGE_HI;
  const at = (v) => gaugePct(v, lo, hi);
  const seg = (from, to, state, label) => ({
    state, label, from, to, leftPct: at(from), widthPct: Math.max(0, at(to) - at(from)),
  });
  const zones = [seg(lo, th.fistMax, 'FIST', 'FIST')];
  if (th.openLo > th.fistMax) zones.push(seg(th.fistMax, th.openLo, 'AMBIGUOUS', 'dead band'));
  zones.push(seg(th.openLo, th.openHi, 'OPEN', 'OPEN'));
  if (th.goodsMin > th.openHi) zones.push(seg(th.openHi, th.goodsMin, 'AMBIGUOUS', 'dead band'));
  zones.push(seg(th.goodsMin, hi, 'GOODS', 'GOODS'));

  const ticks = [];
  for (const [v, label] of [[th.fistMax, 'fist max'], [th.openLo, 'open lo'],
    [th.openHi, 'open hi'], [th.goodsMin, 'goods min']]) {
    const prev = ticks[ticks.length - 1];
    if (prev && Math.abs(prev.at - v) < 1e-9) { prev.label += ` / ${label}`; continue; }
    ticks.push({ at: v, label, pct: at(v) });
  }

  const refs = REFERENCE_SOLIDITY.map((r) => (r.at !== undefined
    ? { label: r.label, at: r.at, pct: at(r.at), widthPct: 0 }
    : { label: r.label, from: r.from, to: r.to, pct: at(r.from), widthPct: at(r.to) - at(r.from) }));

  const v = num(solidity);
  const hyst = num(th.hysteresis) ?? 0;
  return {
    lo, hi, zones, ticks, refs,
    value: v,
    valuePct: v === null ? null : at(v),
    belowScale: v !== null && v < lo,
    aboveScale: v !== null && v > hi,
    hysteresis: hyst,
    hysteresisPct: (hyst / (hi - lo)) * 100,
  };
}

const rect = (r) => {
  if (!r) return null;
  const a = Array.isArray(r) ? { x: r[0], y: r[1], w: r[2], h: r[3] } : r;
  const x = num(pick(a, 'x', 'xMm', 'x_mm'));
  const y = num(pick(a, 'y', 'yMm', 'y_mm'));
  const w = num(pick(a, 'w', 'wMm', 'w_mm', 'width'));
  const h = num(pick(a, 'h', 'hMm', 'h_mm', 'height'));
  if (x === null || y === null || w === null || h === null || w <= 0 || h <= 0) return null;
  return { x, y, w, h };
};

/** Fraction of `roi` covered by `box`. Both in mat millimetres. */
export function overlapFraction(roi, box) {
  if (!roi || !box) return 0;
  const x0 = Math.max(roi.x, box.x), x1 = Math.min(roi.x + roi.w, box.x + box.w);
  const y0 = Math.max(roi.y, box.y), y1 = Math.min(roi.y + roi.h, box.y + box.h);
  if (x1 <= x0 || y1 <= y0) return 0;
  return ((x1 - x0) * (y1 - y0)) / (roi.w * roi.h);
}

function pointInRect(r, p) {
  return p && r && p[0] >= r.x && p[0] <= r.x + r.w && p[1] >= r.y && p[1] <= r.y + r.h;
}

function distanceToRect(r, p) {
  const dx = Math.max(r.x - p[0], 0, p[0] - (r.x + r.w));
  const dy = Math.max(r.y - p[1], 0, p[1] - (r.y + r.h));
  return Math.hypot(dx, dy);
}

/**
 * Is the pay panel clear of goods?
 *
 * SIX.md §8.2: an open hand overlapping a goods blob measures solidity 0.859 —
 * an OPEN hand scoring above a CLOSED threshold, i.e. a FALSE CANCEL. So if any
 * goods blob intersects the pay panel, MUDRA refuses to arm and says so.
 *
 * Three outcomes, never two: CLEAR, BLOCKED, and UNKNOWN for the cases where
 * the extent of a placement was never reported and a point is not an extent.
 */
export function panelOccupancy(roiMm, placements) {
  const roi = rect(roiMm);
  if (!roi) return { status: 'UNKNOWN', reason: Abstain.ROI_UNCONFIGURED, blockers: [], coverage: null, near: [] };
  if (!Array.isArray(placements)) {
    return { status: 'UNKNOWN', reason: Abstain.OCCUPANCY_UNKNOWN, blockers: [], coverage: null, near: [] };
  }
  const blockers = [];
  const near = [];
  let coverage = 0;
  for (const p of placements) {
    if (!p || p.reverted === true) continue;
    const id = pick(p, 'itemId', 'item_id', 'id') ?? '(unnamed)';
    const box = rect(pick(p, 'boxMm', 'box_mm', 'bboxMm', 'bbox_mm'));
    if (box) {
      const f = overlapFraction(roi, box);
      if (f >= PANEL_BLOCK_MIN_COVERAGE) { blockers.push({ id, coverage: f, how: 'extent' }); coverage += f; }
      continue;
    }
    const c = pick(p, 'centreMm', 'centre_mm', 'centerMm', 'center_mm');
    const pt = Array.isArray(c) && num(c[0]) !== null && num(c[1]) !== null ? [c[0], c[1]] : null;
    if (!pt) { near.push({ id, how: 'no_geometry' }); continue; }
    if (pointInRect(roi, pt)) { blockers.push({ id, coverage: null, how: 'centre' }); continue; }
    if (distanceToRect(roi, pt) <= NEAR_PANEL_MM) near.push({ id, how: 'centre_near', mm: distanceToRect(roi, pt) });
  }
  if (blockers.length) {
    return {
      status: 'BLOCKED', reason: Abstain.PANEL_BLOCKED, blockers, near,
      coverage: coverage > 0 ? Math.min(coverage, 1) : null,
    };
  }
  if (near.length) {
    return { status: 'UNKNOWN', reason: Abstain.GOODS_EXTENT_UNKNOWN, blockers: [], near, coverage: null };
  }
  return { status: 'CLEAR', reason: null, blockers: [], near: [], coverage: 0 };
}

/**
 * The full view model for one render. Everything the DOM shows is decided here,
 * so panels.test.mjs can assert on judgement without touching an element.
 *
 * Accepts the brain's wire message {type:"mudra", state, solidity, defects,
 * area_mm2}, an app state carrying `mudra`, or a camelCase object.
 */
export function mudraView(input = {}, opts = {}) {
  // Idempotent: rendering an already-built view must not re-normalise it, or a
  // view's own `state: 'OPEN'` would be re-read as a fresh gesture reading.
  if (input && input.__mudraView === true) return input;
  const st = input && typeof input === 'object' ? input : {};
  const r = st.mudra && typeof st.mudra === 'object' ? st.mudra
    : (st.state !== undefined && (st.solidity !== undefined || st.area_mm2 !== undefined
      || st.areaMm2 !== undefined) ? st : null);

  const nowMs = num(opts.nowMs) ?? num(st.nowMs);
  const tsMs = r ? toMs(pick(r, 'tsMs', 'ts_ms', 'ts')) : null;
  const reportedAge = r ? num(pick(r, 'ageMs', 'age_ms')) : null;
  const ageMs = reportedAge !== null ? reportedAge
    : (tsMs !== null && nowMs !== null ? nowMs - tsMs : null);
  const age = {
    ms: ageMs,
    known: ageMs !== null,
    stale: ageMs !== null && ageMs > STALE_MS,
    limitMs: STALE_MS,
  };

  const thresholds = normaliseThresholds(pick(st, 'calibration', 'mudraCalibration') ?? (r ? r.calibration : null));
  const roiMm = rect(pick(st, 'payPanelMm', 'pay_panel_mm', 'roiMm', 'roi_mm')
    ?? (r ? pick(r, 'roiMm', 'roi_mm') : null));
  const placements = pick(st, 'placements', 'lines');
  const occupancy = panelOccupancy(roiMm, Array.isArray(placements) ? placements : null);

  const matLocked = pick(st, 'matLocked', 'mat_locked') === true;
  const sessionState = pick(st, 'sessionState', 'session_state')
    ?? (st.session && typeof st.session === 'object' ? pick(st.session, 'state') : null)
    // `st.state` is the session's state only when st is an app state. When the
    // caller handed us the bare mudra message, st.state is the GESTURE.
    ?? (r === st ? null : pick(st, 'state'));
  const screenLive = pick(st, 'screenMatchLive', 'screen_match_live') === true;

  const reported = r ? String(pick(r, 'state') ?? 'NONE') : null;
  const engineState = reported !== null && MUDRA_STATES.includes(reported) ? reported : null;
  const rawReported = r ? pick(r, 'rawState', 'raw_state') : null;
  const rawState = typeof rawReported === 'string' && MUDRA_STATES.includes(rawReported)
    ? rawReported : null;

  // ---- panel-level abstention. An unreadable panel is not a NONE. ---------
  let abstain = null;
  if (!r) abstain = Abstain.NO_READING;
  else if (engineState === null) abstain = Abstain.NO_READING;   // unpublished state, refuse it
  else if (age.stale) abstain = Abstain.STALE;
  else if (!matLocked) abstain = Abstain.MAT_UNLOCKED;

  const reason = parseReason(r ? pick(r, 'reason') : '');
  const displayState = abstain ? 'UNKNOWN' : engineState;
  const ambiguous = displayState === 'AMBIGUOUS';
  const unknown = displayState === 'UNKNOWN';

  // ---- arming. Every refusal is named; the reasons stack, worst first. ----
  const armBlocks = [];
  if (abstain) armBlocks.push(abstain);
  if (!matLocked) armBlocks.push(Abstain.MAT_UNLOCKED);
  if (occupancy.status !== 'CLEAR') armBlocks.push(occupancy.reason);
  if (screenLive) armBlocks.push(Abstain.DISARMED_BY_SCREEN);
  if (sessionState && sessionState !== 'AWAITING_SETTLEMENT') armBlocks.push(Abstain.NOT_SETTLEMENT);
  if (thresholds.armReason) armBlocks.push(thresholds.armReason);
  const arm = {
    armed: armBlocks.length === 0,
    reasons: [...new Set(armBlocks)],
    reason: armBlocks.length ? armBlocks[0] : null,
    // Without a calibrated, armed solidity channel the gesture degrades to
    // tap-to-arm. SIX.md §6 requires that be said on screen, not hidden.
    tapFallback: thresholds.armReason !== null,
  };

  // ---- the target. REVEALED, never minted here. --------------------------
  const t = pick(st, 'target', 'mudraTarget') ?? null;
  const amountPaise = t ? pick(t, 'amountPaise', 'amount_paise') : null;
  const formatted = amountPaise === null ? null : formatPaise(amountPaise);
  const targetPresent = t !== null && amountPaise !== null;
  const target = {
    present: targetPresent,
    amountPaise,
    text: formatted,
    // A non-integer amount is refused loudly. It is never rounded into a rupee.
    refused: amountPaise !== null && formatted === null ? Abstain.TARGET_NOT_INTEGER : null,
    minted: t ? pick(t, 'minted', 'preMinted', 'pre_minted') === true : false,
    source: (t ? pick(t, 'source', 'mintedBy', 'minted_by') : null) ?? 'CORE',
    // Nothing is revealed unless something was minted to reveal. An armed open
    // palm over an empty session reveals an empty session.
    revealed: targetPresent && displayState === 'OPEN' && arm.armed,
    abstain: targetPresent ? null : Abstain.NO_TARGET,
  };

  const solidity = num(r ? pick(r, 'solidity') : null);
  const defects = num(r ? pick(r, 'defects') : null);
  const compactness = num(r ? pick(r, 'compactness') : null);
  const areaMm2 = num(r ? pick(r, 'areaMm2', 'area_mm2') : null);
  const handLo = thresholds.handAreaMm2[0], handHi = thresholds.handAreaMm2[1];

  return {
    __mudraView: true,
    id: MUDRA_PANEL_ID,
    hasReading: r !== null,
    abstain,
    state: displayState,
    engineState,
    rawState,
    chattering: rawState !== null && engineState !== null && rawState !== engineState,
    ambiguous,
    unknown,
    decided: !abstain && (displayState === 'OPEN' || displayState === 'FIST' || displayState === 'GOODS'),
    reason,
    framesHeld: num(r ? pick(r, 'framesHeld', 'frames_held') : null),
    borderTouching: r ? pick(r, 'borderTouching', 'border_touching') === true : false,
    age,
    thresholds,
    gauge: solidityGauge(solidity, thresholds),
    solidity,
    defects,
    compactness,
    areaMm2,
    areaPlausible: areaMm2 === null ? null : (areaMm2 >= handLo && areaMm2 <= handHi),
    matLocked,
    sessionState: sessionState ?? null,
    roiMm,
    occupancy,
    arm,
    target,
    // INVARIANT 2, carried in the model so a test can assert it rather than
    // read it in a comment: this panel has no colour that means "paid".
    canGoGreen: false,
    mintsMoney: false,
  };
}

/** The one sentence that keeps this feature honest. */
export const REVEAL_DISCLAIMER =
  'MUDRA reveals a target CORE already minted. It cannot mint, cannot authorise '
  + 'and never turns the counter green — green comes only from a signature-verified webhook.';

export const NO_MODEL_NOTE = (() => {
  const ratio = FORBIDDEN_MODEL_BYTES / COLD_LOAD_BUDGET_BYTES;
  return `NO MODEL · 0 bytes of weights · MediaPipe hand_landmarker.task is `
    + `${FORBIDDEN_MODEL_BYTES.toLocaleString('en-US')} B = ${ratio.toFixed(2)}× the whole `
    + `${(COLD_LOAD_BUDGET_BYTES / 1e6).toFixed(1)} MB cold-load budget`;
})();

// ==========================================================================
// Render. Pure: (state, document) -> Element. Nothing here reads a global.
// ==========================================================================

/* Colours resolve through the stylesheet's custom properties and fall back to
 * literals when style.css has not loaded. `var(--green)` is absent on purpose:
 * no panel may ever paint the settled colour. */
const C = Object.freeze({
  ink: 'var(--ink, #eef1f6)',
  dim: 'var(--ink-dim, #97a0b0)',
  panel: 'var(--panel, #141821)',
  line: 'var(--line, #232a36)',
  grey: 'var(--grey, #6b7480)',
  amber: 'var(--amber, #e0a33c)',
  red: 'var(--red, #e2503f)',
  slate: '#8fb0d8',          // "decided" — deliberately not the settled colour
  amberWash: 'rgba(224,163,60,0.16)',
  greyWash: 'rgba(107,116,128,0.14)',
});

export const STATE_TONE = Object.freeze({
  NONE: 'grey', OPEN: 'slate', FIST: 'slate', GOODS: 'amber',
  AMBIGUOUS: 'amber', UNKNOWN: 'grey',
});
const TONE_COLOUR = Object.freeze({ grey: C.grey, slate: C.slate, amber: C.amber, red: C.red });

/** Tone for a state. Never returns anything that renders as settled/green. */
export function toneFor(state) { return STATE_TONE[state] ?? 'grey'; }

function doc0(doc) {
  const d = doc ?? (typeof globalThis !== 'undefined' ? globalThis.document : null);
  if (!d || typeof d.createElement !== 'function') {
    throw new Error('mudra panel needs a document (pass one in tests)');
  }
  return d;
}

/** Tiny element builder. Styles go through CSSOM, never setAttribute('style'):
 *  the page's CSP is `style-src 'self'` and a style ATTRIBUTE would be blocked. */
function el(doc, tag, spec = {}, children = []) {
  const n = doc.createElement(tag);
  if (spec.class) n.className = spec.class;
  if (spec.id) n.id = spec.id;
  if (spec.text !== undefined && spec.text !== null) n.textContent = String(spec.text);
  if (spec.data) for (const [k, v] of Object.entries(spec.data)) {
    if (v !== null && v !== undefined) n.setAttribute(`data-${k}`, String(v));
  }
  if (spec.attrs) for (const [k, v] of Object.entries(spec.attrs)) {
    if (v !== null && v !== undefined) n.setAttribute(k, String(v));
  }
  if (spec.style) for (const [k, v] of Object.entries(spec.style)) n.style[k] = v;
  for (const c of children) if (c) n.appendChild(c);
  return n;
}

const pct = (x) => `${x}%`;
const f2 = (x) => x.toFixed(2);
const f3 = (x) => x.toFixed(3);

function kv(doc, cls, label, valueText, note, tone) {
  return el(doc, 'div', { class: `mudra-metric ${cls}`, data: { metric: cls.replace('mudra-metric-', '') } }, [
    el(doc, 'div', { class: 'mudra-metric-head' }, [
      el(doc, 'span', { class: 'mudra-metric-label', text: label, style: { color: C.dim } }),
      el(doc, 'span', {
        class: 'mudra-metric-value',
        text: valueText,
        style: { color: tone ? TONE_COLOUR[tone] ?? C.ink : C.ink, fontVariantNumeric: 'tabular-nums' },
      }),
    ]),
    note ? el(doc, 'div', { class: 'mudra-metric-note', text: note, style: { color: C.dim } }) : null,
  ]);
}

function renderGauge(doc, view) {
  const g = view.gauge;
  const track = el(doc, 'div', {
    class: 'mudra-gauge-track',
    style: {
      position: 'relative', height: '26px', borderRadius: '6px',
      background: C.greyWash, border: `1px solid ${C.line}`, overflow: 'hidden',
    },
  });
  for (const z of g.zones) {
    track.appendChild(el(doc, 'div', {
      class: `mudra-gauge-zone zone-${z.state}`,
      data: { zone: z.state, from: f2(z.from), to: f2(z.to) },
      attrs: { title: `${z.label} ${f2(z.from)}–${f2(z.to)}` },
      style: {
        position: 'absolute', top: '0', bottom: '0',
        left: pct(z.leftPct), width: pct(z.widthPct),
        background: z.state === 'AMBIGUOUS' ? C.amberWash : 'transparent',
        borderRight: `1px solid ${C.line}`,
      },
    }, [
      el(doc, 'span', {
        class: 'mudra-gauge-zone-label', text: z.label,
        style: { position: 'absolute', left: '3px', top: '5px', fontSize: '9px', letterSpacing: '.08em', color: C.dim },
      }),
    ]));
  }
  for (const t of g.ticks) {
    track.appendChild(el(doc, 'div', {
      class: 'mudra-gauge-tick', data: { at: f2(t.at), label: t.label },
      attrs: { title: `${t.label} ${f2(t.at)}` },
      style: { position: 'absolute', top: '0', bottom: '0', left: pct(t.pct), width: '1px', background: C.dim },
    }));
  }
  if (g.valuePct !== null) {
    track.appendChild(el(doc, 'div', {
      class: 'mudra-gauge-needle', data: { value: f3(g.value) },
      style: {
        position: 'absolute', top: '-3px', bottom: '-3px', left: pct(g.valuePct),
        width: '3px', marginLeft: '-1px',
        background: TONE_COLOUR[toneFor(view.state)] ?? C.ink,
      },
    }));
    if (g.hysteresis > 0) {
      track.appendChild(el(doc, 'div', {
        class: 'mudra-gauge-hysteresis',
        attrs: { title: `Schmitt half-width ±${f2(g.hysteresis)} solidity` },
        style: {
          position: 'absolute', top: '0', bottom: '0',
          left: pct(Math.max(0, g.valuePct - g.hysteresisPct)),
          width: pct(Math.min(100, 2 * g.hysteresisPct)),
          background: 'rgba(143,176,216,0.18)',
        },
      }));
    }
  }

  const scale = el(doc, 'div', { class: 'mudra-gauge-scale', style: { position: 'relative', height: '14px', marginTop: '2px' } });
  for (const t of g.ticks) {
    scale.appendChild(el(doc, 'span', {
      class: 'mudra-gauge-scale-label', text: f2(t.at),
      style: {
        position: 'absolute', left: pct(t.pct), transform: 'translateX(-50%)',
        fontSize: '9px', color: C.dim, fontVariantNumeric: 'tabular-nums',
      },
    }));
  }

  const refs = el(doc, 'div', { class: 'mudra-gauge-refs', style: { position: 'relative', height: '16px' } });
  for (const r of g.refs) {
    refs.appendChild(el(doc, 'span', {
      class: 'mudra-gauge-ref', data: { ref: r.label },
      text: r.at !== undefined ? `${r.label} ${f2(r.at)}` : `${r.label} ${f2(r.from)}–${f2(r.to)}`,
      style: {
        position: 'absolute', left: pct(r.pct), fontSize: '9px', color: C.dim,
        whiteSpace: 'nowrap', transform: r.at !== undefined ? 'translateX(-50%)' : 'none',
      },
    }));
  }

  const out = el(doc, 'div', { class: 'mudra-gauge' }, [
    track,
    scale,
    el(doc, 'div', {
      class: 'mudra-gauge-refs-caption',
      text: 'measured on real hands — provenance, not thresholds:',
      style: { fontSize: '9px', color: C.dim, marginTop: '2px' },
    }),
    refs,
  ]);
  if (g.belowScale) {
    out.appendChild(el(doc, 'div', {
      class: 'mudra-gauge-offscale',
      text: `◀ ${f3(g.value)} is below the ${f2(g.lo)} floor of this scale`,
      style: { fontSize: '10px', color: C.amber },
    }));
  }
  return out;
}

function renderMap(doc, view) {
  const wrap = el(doc, 'div', {
    class: 'mudra-map',
    data: { occupancy: view.occupancy.status },
    style: {
      position: 'relative', width: '100%', maxWidth: '150px', aspectRatio: `${MAT_W_MM} / ${MAT_H_MM}`,
      border: `1px solid ${C.line}`, background: C.panel, borderRadius: '4px',
    },
  });
  const asX = (mm) => pct((mm / MAT_W_MM) * 100);
  const asY = (mm) => pct((mm / MAT_H_MM) * 100);
  if (view.roiMm) {
    const tone = view.occupancy.status === 'BLOCKED' ? C.amber
      : view.occupancy.status === 'UNKNOWN' ? C.grey : C.slate;
    wrap.appendChild(el(doc, 'div', {
      class: 'mudra-map-roi',
      data: { status: view.occupancy.status },
      attrs: {
        title: `pay panel ${f2(view.roiMm.w)}×${f2(view.roiMm.h)} mm at `
          + `(${f2(view.roiMm.x)}, ${f2(view.roiMm.y)}) mm`,
      },
      style: {
        position: 'absolute', left: asX(view.roiMm.x), top: asY(view.roiMm.y),
        width: asX(view.roiMm.w), height: asY(view.roiMm.h),
        border: `1px ${view.occupancy.status === 'CLEAR' ? 'solid' : 'dashed'} ${tone}`,
        background: view.occupancy.status === 'BLOCKED' ? C.amberWash : 'transparent',
      },
    }));
  }
  const blockers = Array.isArray(view.occupancy.blockers) ? view.occupancy.blockers : [];
  for (const b of blockers) {
    wrap.appendChild(el(doc, 'div', {
      class: 'mudra-map-blocker', data: { item: String(b.id) },
      style: {
        position: 'absolute', right: '2px', top: '2px', fontSize: '8px', color: C.amber,
      },
      text: '■',
    }));
  }
  return wrap;
}

function renderOccupancy(doc, view) {
  const o = view.occupancy;
  const tone = o.status === 'BLOCKED' ? 'amber' : o.status === 'UNKNOWN' ? 'grey' : 'slate';
  const headline = o.status === 'BLOCKED' ? 'PANEL BLOCKED'
    : o.status === 'UNKNOWN' ? 'PANEL OCCUPANCY UNKNOWN' : 'panel clear';
  const detail = o.status === 'BLOCKED'
    ? `${o.blockers.length} item${o.blockers.length === 1 ? '' : 's'} over the pay panel`
      + (o.coverage !== null ? ` · ~${f2(o.coverage * 100)} % covered` : ' · extent unknown')
      + ' — an open hand over goods measures solidity 0.859 and would read as a FIST. Refusing.'
    : o.status === 'UNKNOWN'
      ? (o.reason === Abstain.ROI_UNCONFIGURED
        ? 'the pay-panel rectangle has not been configured, so nothing can be checked against it'
        : o.reason === Abstain.OCCUPANCY_UNKNOWN
          ? 'the brain reported no placements, so the panel cannot be shown to be clear'
          : 'a placement sits near the panel with no reported extent — a centre is not an extent')
      : 'no goods intersect the pay panel';

  return el(doc, 'section', {
    class: 'mudra-occupancy', data: { occupancy: o.status, reason: o.reason ?? '' },
  }, [
    el(doc, 'div', { class: 'mudra-occupancy-head' }, [
      el(doc, 'span', {
        class: 'mudra-occupancy-status', text: headline,
        style: {
          color: TONE_COLOUR[tone], fontWeight: '700', letterSpacing: '.1em', fontSize: '11px',
        },
      }),
      o.reason ? el(doc, 'code', {
        class: 'mudra-occupancy-reason', text: o.reason,
        style: { color: C.dim, fontSize: '10px', marginLeft: '6px' },
      }) : null,
    ]),
    el(doc, 'div', { class: 'mudra-occupancy-detail', text: detail, style: { color: C.dim, fontSize: '11px' } }),
    el(doc, 'div', { class: 'mudra-occupancy-body', style: { display: 'flex', gap: '10px', marginTop: '6px' } }, [
      renderMap(doc, view),
      el(doc, 'div', { class: 'mudra-roi-figures', style: { fontSize: '10px', color: C.dim } }, [
        el(doc, 'div', {
          class: 'mudra-roi-rect',
          text: view.roiMm
            ? `ROI ${f2(view.roiMm.w)}×${f2(view.roiMm.h)} mm at (${f2(view.roiMm.x)}, ${f2(view.roiMm.y)}) mm`
            : 'ROI — not configured',
        }),
        el(doc, 'div', {
          class: 'mudra-roi-buffer',
          text: `drawn on the rectified ${BUF_W}×${BUF_H} crop — the only buffer that survives a frame grab`,
        }),
        ...view.occupancy.blockers.map((b) => el(doc, 'div', {
          class: 'mudra-roi-blocker', data: { item: String(b.id) },
          text: `blocked by ${b.id} (${b.how}${b.coverage !== null && b.coverage !== undefined ? ` ${f2(b.coverage * 100)} %` : ''})`,
          style: { color: C.amber },
        })),
      ]),
    ]),
  ]);
}

function renderVerdict(doc, view) {
  const tone = toneFor(view.state);
  const loud = view.ambiguous || view.unknown;
  const box = el(doc, 'div', {
    class: `mudra-verdict tone-${tone}${view.ambiguous ? ' is-ambiguous' : ''}${view.unknown ? ' is-unknown' : ''}`,
    data: { state: view.state, tone, abstain: view.abstain ?? '' },
    style: {
      border: `${loud ? '2px' : '1px'} solid ${TONE_COLOUR[tone]}`,
      background: loud ? (view.ambiguous ? C.amberWash : C.greyWash) : 'transparent',
      borderRadius: '8px', padding: '10px 12px', margin: '8px 0',
    },
  });
  box.appendChild(el(doc, 'div', {
    class: 'mudra-verdict-state', text: view.state,
    style: {
      color: TONE_COLOUR[tone], fontWeight: '700',
      fontSize: loud ? '30px' : '24px', letterSpacing: '.06em', lineHeight: '1.05',
    },
  }));
  if (view.ambiguous || view.unknown) {
    box.appendChild(el(doc, 'div', {
      class: 'mudra-verdict-idk', text: 'I DO NOT KNOW',
      style: { color: TONE_COLOUR[tone], fontWeight: '700', fontSize: '12px', letterSpacing: '.18em' },
    }));
  }
  const causeCode = view.abstain ?? view.reason.code;
  const causeGloss = view.abstain
    ? ABSTAIN_GLOSS[view.abstain] ?? ''
    : view.reason.gloss;
  box.appendChild(el(doc, 'div', { class: 'mudra-verdict-cause' }, [
    el(doc, 'code', {
      class: 'mudra-verdict-code', text: causeCode || '(no cause reported)',
      style: { color: C.ink, fontSize: '11px' },
    }),
    causeGloss ? el(doc, 'span', {
      class: 'mudra-verdict-gloss', text: ` — ${causeGloss}`,
      style: { color: C.dim, fontSize: '11px' },
    }) : null,
  ]));
  if (view.reason.known === false) {
    box.appendChild(el(doc, 'div', {
      class: 'mudra-verdict-unpublished',
      text: 'this cause is not in mudra.REASONS — refusing to gloss it',
      style: { color: C.red, fontSize: '11px' },
    }));
  }
  if (view.rawState) {
    const d = view.reason.dwell;
    box.appendChild(el(doc, 'div', {
      class: 'mudra-verdict-raw', data: { raw: view.rawState, chattering: String(view.chattering) },
      text: `this frame read ${view.rawState}`
        + (d ? ` · committing ${d.count}/${d.of} frames` : '')
        + (view.framesHeld !== null ? ` · held ${view.framesHeld} frames` : ''),
      style: { color: C.dim, fontSize: '11px' },
    }));
  }
  if (view.age.known) {
    box.appendChild(el(doc, 'div', {
      class: 'mudra-verdict-age', data: { stale: String(view.age.stale) },
      text: `reading ${Math.round(view.age.ms)} ms old`
        + (view.age.stale ? ` — STALE, over the ${STALE_MS} ms limit` : ''),
      style: { color: view.age.stale ? C.amber : C.dim, fontSize: '10px' },
    }));
  } else if (view.hasReading) {
    box.appendChild(el(doc, 'div', {
      class: 'mudra-verdict-age', data: { stale: 'unknown' },
      text: `freshness unknown (${Abstain.AGE_UNKNOWN}) — no timestamp on the reading`,
      style: { color: C.dim, fontSize: '10px' },
    }));
  }
  return box;
}

export const ABSTAIN_GLOSS = Object.freeze({
  [Abstain.NO_READING]: 'the brain has not reported a gesture yet',
  [Abstain.STALE]: 'the last reading is older than the freshness limit',
  [Abstain.MAT_UNLOCKED]: 'without a mat lock a millimetre is not a millimetre',
  [Abstain.ROI_UNCONFIGURED]: 'the pay-panel rectangle has not been configured',
  [Abstain.OCCUPANCY_UNKNOWN]: 'no placement list, so the panel cannot be shown to be clear',
  [Abstain.GOODS_EXTENT_UNKNOWN]: 'a placement is near the panel with no reported extent',
  [Abstain.PANEL_BLOCKED]: 'goods intersect the pay panel',
  [Abstain.NOT_SETTLEMENT]: 'the gesture arms only after DONE, in AWAITING_SETTLEMENT',
  [Abstain.DISARMED_BY_SCREEN]: 'a payment screen is being matched; both want the mat',
  [Abstain.UNCALIBRATED]: 'this hand under this light was never measured',
  [Abstain.GAP_TOO_SMALL]: 'measured open/fist separation is under the 0.08 floor',
  [Abstain.NO_TARGET]: 'nothing has been minted to reveal',
  [Abstain.TARGET_NOT_INTEGER]: 'the amount is not integer paise and will not be rounded',
  [Abstain.AGE_UNKNOWN]: 'the reading carries no timestamp',
});

function renderThresholdNote(doc, view) {
  const t = view.thresholds;
  const lines = [];
  lines.push(`bands: FIST < ${f2(t.fistMax)} · OPEN ${f2(t.openLo)}–${f2(t.openHi)} `
    + `· GOODS > ${f2(t.goodsMin)} · Schmitt ±0.03 · dwell ${t.dwellFrames} frames`);
  if (t.calibrated) {
    lines.push(`calibrated on this hand: p95(open) ${t.p95Open === null ? '?' : f3(t.p95Open)} `
      + `− p05(fist) ${t.p05Fist === null ? '?' : f3(t.p05Fist)} = gap `
      + `${t.gap === null ? '?' : f3(t.gap)} (floor ${f2(MIN_SOLIDITY_GAP)})`
      + (t.samples !== null ? ` · ${t.samples} samples` : ''));
  } else {
    lines.push(`UNCALIBRATED — these are gawaah/mudra.py defaults, not this shop's hand `
      + `under this light. SIX.md §8.2 calibrates ${f2(CALIBRATED_HINT.fistMax)}/`
      + `${f2(CALIBRATED_HINT.openHi)} against a cluttered mat.`);
  }
  if (!t.gapOk) {
    lines.push('solidity channel NOT ARMED — degraded to tap-to-arm, which is press-and-release '
      + 'and has no open-vs-closed semantics. Said here rather than pretended.');
  }
  return el(doc, 'div', {
    class: 'mudra-thresholds', data: { calibrated: String(t.calibrated), gapok: String(t.gapOk) },
    style: { fontSize: '10px', color: t.gapOk ? C.dim : C.amber, marginTop: '6px' },
  }, lines.map((s, i) => el(doc, 'div', { class: `mudra-threshold-line line-${i}`, text: s })));
}

function renderArm(doc, view) {
  const a = view.arm;
  return el(doc, 'div', {
    class: `mudra-arm ${a.armed ? 'is-armed' : 'is-disarmed'}`,
    data: { armed: String(a.armed), reason: a.reason ?? '' },
    style: {
      marginTop: '8px', padding: '7px 9px', borderRadius: '6px',
      border: `1px solid ${a.armed ? C.slate : C.grey}`,
      color: a.armed ? C.slate : C.dim, fontSize: '11px',
    },
  }, [
    el(doc, 'div', {
      class: 'mudra-arm-head',
      text: a.armed ? 'ARMED — an open palm will reveal the target' : 'NOT ARMED',
      style: { fontWeight: '700', letterSpacing: '.08em' },
    }),
    ...a.reasons.map((r) => el(doc, 'div', { class: 'mudra-arm-reason' }, [
      el(doc, 'code', { text: r, style: { color: C.ink } }),
      el(doc, 'span', { text: ` — ${ABSTAIN_GLOSS[r] ?? ''}`, style: { color: C.dim } }),
    ])),
    a.tapFallback ? el(doc, 'div', {
      class: 'mudra-arm-fallback',
      text: 'tap-to-arm is the fallback and it is bound: the visual is identical and the audit chip '
        + 'records trigger: key rather than trigger: gesture.',
      style: { color: C.amber },
    }) : null,
  ]);
}

function renderTarget(doc, view) {
  const t = view.target;
  const body = [];
  if (t.refused) {
    body.push(el(doc, 'div', {
      class: 'mudra-target-refused',
      text: `REFUSED — ${t.refused}: ${JSON.stringify(t.amountPaise)} is not integer paise`,
      style: { color: C.red, fontWeight: '700' },
    }));
  } else if (!t.present) {
    body.push(el(doc, 'div', {
      class: 'mudra-target-none', text: `no target — ${Abstain.NO_TARGET}`,
      style: { color: C.dim },
    }));
  } else {
    body.push(el(doc, 'div', {
      class: 'mudra-target-amount', text: t.text,
      style: { color: C.ink, fontSize: '20px', fontWeight: '700', fontVariantNumeric: 'tabular-nums' },
    }));
    body.push(el(doc, 'div', {
      class: 'mudra-target-provenance',
      text: t.minted
        ? `pre-minted by ${t.source} · ${t.revealed ? 'REVEALED by the open palm' : 'hidden until an armed open palm'}`
        : `${t.source} has not confirmed a mint — nothing here mints one`,
      style: { color: C.dim, fontSize: '11px' },
    }));
  }
  return el(doc, 'section', {
    class: 'mudra-target', data: { present: String(t.present), revealed: String(t.revealed) },
    style: { marginTop: '8px', padding: '8px 10px', border: `1px solid ${C.line}`, borderRadius: '6px' },
  }, [
    el(doc, 'div', {
      class: 'mudra-target-label', text: 'payment target',
      style: { color: C.dim, fontSize: '10px', letterSpacing: '.14em' },
    }),
    ...body,
  ]);
}

/**
 * THE render function: (state, document) -> Element. Detached, so a caller can
 * mount it wherever it likes and a test can read it with no browser.
 */
export function renderMudraPanel(state, doc) {
  const d = doc0(doc);
  const view = mudraView(state);
  const root = el(d, 'section', {
    id: MUDRA_ROOT_ID,
    class: 'gw-panel panel-mudra',
    data: {
      'gawaah-panel': 'mudra',
      panel: 'mudra', state: view.state, tone: toneFor(view.state),
      abstain: view.abstain ?? '', armed: String(view.arm.armed),
      occupancy: view.occupancy.status, contract: String(PANEL_CONTRACT_VERSION),
    },
    style: {
      background: C.panel, border: `1px solid ${C.line}`, borderRadius: '10px',
      padding: '10px 12px', color: C.ink,
    },
  }, [
    el(d, 'header', { class: 'gw-panel-head' }, [
      el(d, 'h2', {
        class: 'gw-panel-title', text: 'MUDRA',
        style: { margin: '0', fontSize: '13px', letterSpacing: '.16em' },
      }),
      el(d, 'div', {
        class: 'gw-panel-sub', text: 'the hand read as an occluder — a hole in the light, not a landmark',
        style: { color: C.dim, fontSize: '11px' },
      }),
      el(d, 'div', {
        class: 'gw-badge gw-badge-nomodel', text: NO_MODEL_NOTE,
        data: { invariant: '3' },
        style: {
          color: C.dim, fontSize: '10px', border: `1px solid ${C.line}`,
          borderRadius: '999px', padding: '3px 8px', marginTop: '6px', display: 'inline-block',
        },
      }),
    ]),
    renderVerdict(d, view),
    el(d, 'div', { class: 'mudra-metrics' }, [
      el(d, 'div', { class: 'mudra-metric mudra-metric-solidity', data: { metric: 'solidity' } }, [
        el(d, 'div', { class: 'mudra-metric-head' }, [
          el(d, 'span', { class: 'mudra-metric-label', text: 'solidity = area / convex-hull area', style: { color: C.dim, fontSize: '11px' } }),
          el(d, 'span', {
            class: 'mudra-metric-value',
            text: view.solidity === null ? '—' : f3(view.solidity),
            data: { value: view.solidity === null ? '' : f3(view.solidity) },
            style: {
              color: view.solidity === null ? C.grey : C.ink, fontWeight: '700',
              fontSize: '16px', fontVariantNumeric: 'tabular-nums', marginLeft: '8px',
            },
          }),
        ]),
        renderGauge(d, view),
      ]),
      kv(d, 'mudra-metric-defects', `convexity defects deeper than ${f2(view.thresholds.minDefectDepthMm ?? 6)} mm`,
        view.defects === null ? '—' : String(view.defects),
        `an open palm shows ≥${view.thresholds.minDefectsOpen} inter-finger notches; a fist and a packet show none`,
        view.defects === null ? 'grey' : null),
      kv(d, 'mudra-metric-compactness', 'compactness = 4πA / P²',
        view.compactness === null ? '—' : f3(view.compactness),
        `GOODS ≥ ${f2(view.thresholds.goodsCompactnessMin)} · OPEN ≤ ${f2(view.thresholds.openCompactnessMax)} `
        + `· a rasterised disc measures ${f3(COMPACTNESS_DISC_CEILING)}, never 1.000 (Freeman-chain perimeter)`,
        view.compactness === null ? 'grey' : null),
      kv(d, 'mudra-metric-area', 'area',
        view.areaMm2 === null ? '—' : `${Math.round(view.areaMm2).toLocaleString('en-US')} mm²`,
        `a hand measures ${view.thresholds.handAreaMm2[0].toLocaleString('en-US')}–`
        + `${view.thresholds.handAreaMm2[1].toLocaleString('en-US')} mm²`
        + (view.areaPlausible === false ? ' — THIS IS NOT HAND-SIZED: probably a hand merged with goods' : ''),
        view.areaMm2 === null ? 'grey' : (view.areaPlausible === false ? 'amber' : null)),
      view.borderTouching ? el(d, 'div', {
        class: 'mudra-border-touching',
        text: 'contour touches the mat border — a forearm always exits the mat, goods never do',
        style: { color: C.dim, fontSize: '10px' },
      }) : null,
    ]),
    renderThresholdNote(d, view),
    renderOccupancy(d, view),
    renderArm(d, view),
    renderTarget(d, view),
    el(d, 'footer', {
      class: 'gw-panel-foot mudra-disclaimer', text: REVEAL_DISCLAIMER,
      data: { invariant: '2' },
      style: { color: C.dim, fontSize: '10px', marginTop: '10px', borderTop: `1px solid ${C.line}`, paddingTop: '6px' },
    }),
  ]);
  return root;
}

// ==========================================================================
// The rectified-view overlay. Ops are data so they can be asserted without a
// canvas; paintRoiOverlay executes them and is a no-op without a context.
// ==========================================================================

/** Canvas colours must be literals — a canvas cannot resolve var(--token). */
const CANVAS = Object.freeze({
  clear: '#8fb0d8', blocked: '#e0a33c', unknown: '#6b7480', ink: '#eef1f6',
});

export function roiOverlayOps(view) {
  const ops = [];
  if (!view || !view.roiMm) {
    return [{
      op: 'text', xMm: 8, yMm: 14, sizeMm: 5, fill: CANVAS.unknown,
      text: `pay panel: ${Abstain.ROI_UNCONFIGURED}`,
    }];
  }
  const s = view.occupancy.status;
  const stroke = s === 'BLOCKED' ? CANVAS.blocked : s === 'UNKNOWN' ? CANVAS.unknown : CANVAS.clear;
  const r = view.roiMm;
  ops.push({
    op: 'rect', xMm: r.x, yMm: r.y, wMm: r.w, hMm: r.h, stroke,
    lineWidthMm: s === 'CLEAR' ? 0.7 : 1.2, dashMm: s === 'CLEAR' ? null : [4, 3],
  });
  ops.push({
    op: 'text', xMm: r.x, yMm: Math.max(4, r.y - 2), sizeMm: 4.5, fill: stroke,
    text: s === 'BLOCKED' ? 'PAY PANEL — BLOCKED'
      : s === 'UNKNOWN' ? 'PAY PANEL — OCCUPANCY UNKNOWN' : 'PAY PANEL',
  });
  if (s === 'BLOCKED') {
    ops.push({ op: 'hatch', xMm: r.x, yMm: r.y, wMm: r.w, hMm: r.h, stroke, stepMm: 6, lineWidthMm: 0.5 });
    ops.push({
      op: 'text', xMm: r.x + 2, yMm: r.y + r.h / 2, sizeMm: 5, fill: stroke,
      text: `${view.occupancy.blockers.length} item(s) over the panel`,
    });
  }
  if (view.arm && !view.arm.armed && view.arm.reason) {
    ops.push({
      op: 'text', xMm: r.x, yMm: Math.min(MAT_H_MM - 2, r.y + r.h + 6), sizeMm: 4,
      fill: CANVAS.unknown, text: `not armed: ${view.arm.reason}`,
    });
  }
  return ops;
}

/**
 * Paint the overlay onto the rectified crop's 2D context. Returns the number of
 * ops painted; 0 means there was no context, which is a legal state (the panel
 * still shows its own mini-map).
 */
export function paintRoiOverlay(ctx, view, geom = {}) {
  if (!ctx || typeof ctx.beginPath !== 'function') return 0;
  const sx = geom.pxPerMmX ?? BUF_W / MAT_W_MM;
  const sy = geom.pxPerMmY ?? BUF_H / MAT_H_MM;
  const s = (sx + sy) / 2;
  const ops = roiOverlayOps(view);
  let painted = 0;
  if (ctx.save) ctx.save();
  for (const o of ops) {
    if (o.op === 'rect') {
      ctx.strokeStyle = o.stroke;
      ctx.lineWidth = Math.max(1, o.lineWidthMm * s);
      if (ctx.setLineDash) ctx.setLineDash(o.dashMm ? o.dashMm.map((d) => d * s) : []);
      ctx.strokeRect(o.xMm * sx, o.yMm * sy, o.wMm * sx, o.hMm * sy);
      painted++;
    } else if (o.op === 'text') {
      ctx.fillStyle = o.fill;
      ctx.font = `${Math.max(9, Math.round(o.sizeMm * s))}px system-ui`;
      if ('textAlign' in ctx) ctx.textAlign = 'left';
      ctx.fillText(o.text, o.xMm * sx, o.yMm * sy);
      painted++;
    } else if (o.op === 'hatch') {
      ctx.strokeStyle = o.stroke;
      ctx.lineWidth = Math.max(1, o.lineWidthMm * s);
      if (ctx.setLineDash) ctx.setLineDash([]);
      ctx.beginPath();
      for (let x = o.xMm; x <= o.xMm + o.wMm + o.hMm; x += o.stepMm) {
        const x0 = x, y0 = o.yMm;
        const x1 = x - o.hMm, y1 = o.yMm + o.hMm;
        const cx0 = Math.min(Math.max(x0, o.xMm), o.xMm + o.wMm);
        const cx1 = Math.min(Math.max(x1, o.xMm), o.xMm + o.wMm);
        ctx.moveTo(cx0 * sx, y0 * sy);
        ctx.lineTo(cx1 * sx, y1 * sy);
      }
      ctx.stroke();
      painted++;
    }
  }
  if (ctx.restore) ctx.restore();
  return painted;
}

// ==========================================================================
// The panel object. This is what registerPanel receives.
// ==========================================================================

/**
 * Map app.js's read-only counter view onto this panel's inputs.
 *
 * The counter view carries the mat lock, the session state and the committed
 * lines (each with a centreMm) — everything MUDRA needs EXCEPT the gesture
 * itself, which arrives as the brain's {type:"mudra", ...} message. The two are
 * merged, never confused: a counter view that carries no gesture leaves the
 * last gesture alone and only refreshes the world around it.
 */
export function counterViewToMudraInput(view) {
  const v = view && typeof view === 'object' ? view : {};
  const out = {
    matLocked: v.matLocked === true,
    sessionState: typeof v.state === 'string' ? v.state : null,
    visible: v.visible === MUDRA_ID || v.visible === MUDRA_PANEL_ID,
  };
  if (Array.isArray(v.lines)) out.placements = v.lines;
  else if (Array.isArray(v.placements)) out.placements = v.placements;
  // Fields the app may or may not carry yet. Absent means "leave it alone",
  // which is why they are only copied when they are actually present.
  for (const k of ['mudra', 'payPanelMm', 'calibration', 'target', 'screenMatchLive', 'nowMs']) {
    if (v[k] !== undefined && v[k] !== null) out[k] = v[k];
  }
  return out;
}

/**
 * What this panel declares to the shell. OFF is the registry's word for "nobody
 * attached", so a live panel only ever says OK or ABSTAIN — and every ABSTAIN
 * carries the code that caused it, which is what #why-mudra prints.
 */
export function mudraPanelStatus(view) {
  const v = mudraView(view);
  if (v.abstain) return { status: PanelStatus.ABSTAIN, why: v.abstain };
  if (v.ambiguous) return { status: PanelStatus.ABSTAIN, why: v.reason.code || 'AMBIGUOUS' };
  if (!v.arm.armed) return { status: PanelStatus.ABSTAIN, why: v.arm.reason };
  return { status: PanelStatus.OK, why: v.reason.code || null };
}

/**
 * Build the panel.
 *
 *   onState(view)     app.js's counter view. Merged with the last gesture.
 *   onMessage(msg)    the brain's {type:"mudra", ...}. Optional seam: app.js
 *                     may instead put the reading on the counter view as
 *                     `view.mudra`, and both routes land in the same place.
 *   onFrame(frame)    { cropKind:'rectified_mat_crop', crop:<canvas 840x1188> }.
 *                     Paints the pay-panel ROI. INVARIANT 4: anything that is
 *                     not the rectified crop is REFUSED, not drawn on.
 *
 * A frame never carries a verdict, so a frame can never change one.
 */
export function createMudraPanel(opts = {}) {
  const d = doc0(opts.document);
  const setStatus = resolveSetStatus(opts.setStatus);
  const now = () => (opts.now ? opts.now() : undefined);
  let src = opts.initialState && typeof opts.initialState === 'object' ? { ...opts.initialState } : {};
  let view = mudraView(src, { nowMs: now() });
  let root = renderMudraPanel(view, d);
  let refusedFrames = 0;

  function ingest(patch) {
    src = { ...src, ...patch };
    view = mudraView(src, { nowMs: now() });
    const fresh = renderMudraPanel(view, d);
    if (root.parentNode && typeof root.parentNode.replaceChild === 'function') {
      root.parentNode.replaceChild(fresh, root);
    }
    root = fresh;
    if (setStatus) {
      const s = mudraPanelStatus(view);
      setStatus(MUDRA_ID, s.status, s.why);
    }
    return root;
  }

  return {
    id: MUDRA_ID,
    elementId: MUDRA_PANEL_ID,
    contract: PANEL_CONTRACT_VERSION,
    get root() { return root; },
    get view() { return view; },
    get refusedFrames() { return refusedFrames; },
    onState(state) { return ingest(counterViewToMudraInput(state)); },
    onMessage(msg) {
      if (!msg || typeof msg !== 'object') return root;
      if (msg.type !== undefined && msg.type !== MUDRA_ID) return root;
      return ingest({ mudra: msg });
    },
    onFrame(frame = {}) {
      // INVARIANT 4. The only buffer a panel may touch is the rectified crop,
      // and it must say that it is one. A raw frame is refused, counted and
      // never drawn on — a silent no-op here would hide a leak.
      if (frame.raw !== undefined || frame.video !== undefined
        || (frame.cropKind !== undefined && frame.cropKind !== RECTIFIED_CROP_KIND)) {
        refusedFrames++;
        return 0;
      }
      const canvas = frame.crop ?? frame.rect ?? null;
      const ctx = frame.rectCtx
        ?? (canvas && typeof canvas.getContext === 'function' ? canvas.getContext('2d') : null);
      return paintRoiOverlay(ctx, view, frame);
    },
    destroy() { if (root && typeof root.remove === 'function') root.remove(); },
  };
}

/**
 * Attach to the shell. Mounts into #body-mudra (the fill point inside the
 * shell's #panel-mudra section), leaving the shell's own abstain block in place
 * so app.js's setPanelStatus keeps owning it. Registers as 'mudra' through
 * app.js's registerPanel when it exists. Idempotent: re-attaching replaces our
 * previous render rather than stacking a second one.
 */
export function attachMudraPanel(opts = {}) {
  const d = doc0(opts.document);
  const panel = createMudraPanel({ ...opts, document: d });
  const byId = (id) => (typeof d.getElementById === 'function' ? d.getElementById(id) : null);
  const host = opts.host ?? byId(MUDRA_BODY_ID) ?? byId(MUDRA_PANEL_ID);
  if (host) {
    const prior = typeof host.querySelector === 'function'
      ? host.querySelector('[data-gawaah-panel="mudra"]') : null;
    if (prior && typeof host.replaceChild === 'function') host.replaceChild(panel.root, prior);
    else host.appendChild(panel.root);
    if (typeof host.setAttribute === 'function') host.setAttribute('data-panel-mounted', 'mudra');
  }
  const register = resolveRegister(opts.register);
  const result = register
    ? register(MUDRA_ID, { onState: panel.onState, onFrame: panel.onFrame })
    : null;

  // The brain's {type:"mudra", ...} message has no route through the counter
  // view, so a host may hand it over either by calling panel.onMessage(m) or by
  // dispatching a `gawaah:brain` CustomEvent with the message as its detail.
  // Both land in the same place; neither is required for the panel to render.
  let listening = false;
  if (opts.listen !== false && d && typeof d.addEventListener === 'function') {
    d.addEventListener('gawaah:brain', (ev) => panel.onMessage(ev && ev.detail));
    listening = true;
  }

  return {
    panel,
    host: host ?? null,
    registered: result === null ? false : result.ok !== false,
    registration: result,
    listening,
  };
}

// ==========================================================================
// Browser auto-mount. Inert under node (there is no document), and inert in a
// page that has no #panel-mudra. A host that wires the panel itself can turn
// this off with globalThis.__GAWAAH_PANEL_AUTOMOUNT = false before loading.
// ==========================================================================
function automount() {
  try {
    if (!document.getElementById(MUDRA_BODY_ID) && !document.getElementById(MUDRA_PANEL_ID)) return;
    const r = attachMudraPanel({ document });
    const g = globalThis;
    g.GAWAAH_PANELS = Object.assign({}, g.GAWAAH_PANELS, { [MUDRA_ID]: r.panel });
  } catch { /* a panel that cannot mount must not take the counter down */ }
}

if (typeof document !== 'undefined' && typeof window !== 'undefined'
  && globalThis.__GAWAAH_PANEL_AUTOMOUNT !== false) {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', automount);
  else automount();
}
