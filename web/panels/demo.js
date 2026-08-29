/* DEMO — the one-click proof.
 * ===========================================================================
 *
 * WHAT THIS PANEL IS FOR
 * Every other panel in this counter answers a question about a thing that is
 * physically present: a hand, a sticker, a phone screen, a burst of frames. A
 * visitor with no camera, no printed mat and no phone can therefore see only
 * the honest abstentions — which is CORRECT, and completely useless as a way
 * of finding out whether any of this works.
 *
 * This panel is the path to the working state. It drives the brain's synthetic
 * frame script, names the beat being demonstrated, says in one sentence WHY
 * the counter is doing what it is doing, and shows the three numbers that
 * actually matter moving as they move. It adds no capability and it decides
 * nothing. It is a window with a caption.
 *
 * FOUR THINGS THIS PANEL REFUSES TO DO, and they are the whole design:
 *
 *   1. IT NEVER CLAIMS A FRAME IS REAL. The SIMULATED badge is driven by
 *      EVIDENCE (`GET /health` -> `sim: true`, or a `sim` message from the
 *      brain), never by "the user pressed RUN DEMO". With no evidence it says
 *      PROVENANCE UNKNOWN and refuses to guess in either direction. There are
 *      three badge states and no two of them read alike.
 *
 *   2. IT NEVER CLAIMS THE SCRIPT IS SOMEWHERE IT CANNOT SEE. The brain does
 *      not publish its script position today, so the beat is INFERRED from
 *      `frame_index` under a stated assumption, and the inference is labelled
 *      as an inference on screen (`data-beat-source`). If the brain ever sends
 *      `{"type":"sim", index: N}` the panel uses that instead and says so.
 *
 *   3. IT NEVER CLAIMS A BUTTON WORKED. `{"type":"sim", ...}` is not in this
 *      brain's CLIENT_VERBS; it answers `refused / UNKNOWN_TYPE`. That refusal
 *      is rendered verbatim, with the verbs the brain says it WOULD accept. A
 *      control that was sent and never answered is reported as UNANSWERED, not
 *      as done.
 *
 *   4. IT NEVER SHOWS GREEN. Not for a simulated payment, not for a real one.
 *      Green belongs to the counter chrome and comes from one place only: a
 *      webhook whose HMAC verified over the raw bytes before any JSON parse,
 *      whose event is in the green set, whose notes.session_id matched an OPEN
 *      intent, and whose amount equalled that intent exactly. This panel says
 *      that sentence permanently, on screen, in both the simulated and the
 *      live case, and it says which gateway signed the webhook it is watching.
 *
 * RUNNING vs PAUSED IS MEASURED, NOT ASSERTED. `running` is derived from the
 * arrival rate of `state` messages in the last two seconds. If you press PAUSE
 * and frames keep coming, this panel says frames are still coming. What the
 * user asked for and what is happening are two different facts and they are
 * displayed as two different facts.
 *
 * HOW IT GETS ITS DATA. Preferred: the shell's panel seam — `attach()` below
 * registers `onState`, and the shell routes brain messages to it. That seam
 * only accepts ids in app.js's PANEL_IDS, which does not yet contain 'demo',
 * and app.js exposes no send(). So this module ALSO carries a socket tap: it
 * wraps `globalThis.WebSocket` in a Proxy that hands the constructed socket
 * back here. It reads that socket's messages and sends its sim controls on it.
 *
 *   It TAPS the counter's existing socket; it does NOT open a second one. That
 *   is not fastidiousness: brain_server starts one `_sim_pump` PER CONNECTION
 *   against one shared SimScript, so a second socket would advance the script
 *   at twice the rate and the frame pacing on screen would be a lie. Measured
 *   on the running rig: one socket, ~10 state messages per second, period_s
 *   0.1. The Proxy leaves the prototype chain and every static alone, so
 *   `instanceof`, `readyState` and `onmessage` behave exactly as before, and
 *   the whole install is inside a try/catch — a tap that fails downgrades this
 *   panel to read-only and says so, it does not break the counter.
 *
 * Everything above `installStyles` is pure: no browser, no globals, no clock.
 * demo.test.mjs runs the entire render path against a ~60-line DOM shim and
 * checks every constant in section 1 against gawaah/brain_server.py.
 *
 * This file imports nothing. It deliberately does not import app.js: it must
 * render in node with no shell present, and the two constants it would want
 * from there (BUF_W/BUF_H) it does not use.
 */

export const PANEL_ID = 'demo';
export const PANEL_TITLE = 'DEMO — the one-click proof';

// ===========================================================================
// 1. THE SCRIPT. Mirrors gawaah/brain_server.py SimScript, constant for
//    constant. demo.test.mjs shells out to the repo venv, reads the Python
//    values, and asserts equality — a beat list that claims a different script
//    than the brain runs is a caption on the wrong photograph.
// ===========================================================================

/** (phase name, frame count), in order. SimScript.PHASES. */
export const SIM_PHASES = Object.freeze([
  Object.freeze(['settle', 8]),
  Object.freeze(['goods', 30]),
  Object.freeze(['screen', 12]),
  Object.freeze(['hand', 12]),
  Object.freeze(['tamper', 10]),
]);

/** SimScript.total_frames. */
export const TOTAL_FRAMES = SIM_PHASES.reduce((n, p) => n + p[1], 0);

/** SimScript.__init__ period_s — the pump's sleep between frames. */
export const SIM_PERIOD_S = 0.1;

/** SimScript.__init__ enrol_at / sticker_name. */
export const ENROL_AT = 9;
export const STICKER_NAME = 'counter-upi';

/** SimScript.done_at — last frame of the goods phase. */
export const DONE_AT = 37;
/** SimScript.pay_at — one frame later, so the mint is published first. */
export const PAY_AT = 38;

/** The one price in the sim gallery, in paise. build_sim_server: _paise(2850). */
export const PACKET_PRICE_PAISE = 2850;

/** brain.REFUSE_AFTER_FRAMES — frames before an unidentified blob is ambered. */
export const REFUSE_AFTER_FRAMES = 5;

/**
 * brain_server.SIM_BEATS has a sixth key the script has no frames for: `hold`,
 * which the driver reports once the last frame has been pushed. It is a MODE of
 * the beat machine rather than a beat of the script, so it is named here and
 * kept off the beat list — a sixth row that can never be reached by playing the
 * script would be a lie about the script's length.
 */
export const HOLD_BEAT = 'hold';

/** brain_server.SIM_MODES, in order. */
export const SIM_MODES = Object.freeze(['STOPPED', 'RUNNING', 'PAUSED', 'HOLDING', 'FAULTED']);

/** What each mode means for the person watching. */
export const MODE_NOTE = Object.freeze({
  STOPPED: 'the beat machine is attached but idle. No frames are being pushed.',
  RUNNING: 'frames are being pushed on the script\'s own clock.',
  PAUSED: 'the board is held on one frame. STEP advances it by exactly one.',
  HOLDING: 'the script has finished. The board is the final board and nothing '
    + 'further is being pushed — it does NOT loop, because replaying a settled '
    + 'sale onto the same brain would show a second customer paying with the '
    + 'first one\'s webhook. RESET plays it again on a fresh session.',
  FAULTED: 'the sim hit something it must never do and stopped rather than carry '
    + 'on producing frames it cannot vouch for. The fault is printed below.',
});

/**
 * Absolute frame ranges per phase, computed the way SimScript._phase_start
 * computes them. Kept as data rather than as five hand-written pairs so the
 * ranges cannot drift from SIM_PHASES.
 */
export function phaseRanges(phases = SIM_PHASES) {
  const out = [];
  let n = 0;
  for (const [name, count] of phases) {
    out.push({ name, from: n, to: n + count - 1, count });
    n += count;
  }
  return out;
}

/**
 * SimScript.phase_at, and the clamp from SimScript.next_frame: after the
 * script ends the sim REPEATS THE FINAL FRAME rather than looping, because a
 * loop would re-seed nothing and silently replay a sale that already settled.
 * So a frame index past the end is still the last beat, and `complete` says
 * so rather than the beat list pretending to start again.
 */
export function beatAt(frameIndex, phases = SIM_PHASES) {
  const ranges = phaseRanges(phases);
  const total = ranges.length ? ranges[ranges.length - 1].to + 1 : 0;
  if (!Number.isFinite(frameIndex) || frameIndex < 0 || total === 0) {
    return { index: null, name: null, phaseIndex: null, complete: false, frame: null };
  }
  const f = Math.floor(frameIndex);
  const clamped = Math.min(f, total - 1);
  for (let i = 0; i < ranges.length; i++) {
    const r = ranges[i];
    if (clamped <= r.to) {
      return {
        index: i,
        name: r.name,
        phaseIndex: clamped - r.from,
        complete: f >= total,
        frame: f,
      };
    }
  }
  /* istanbul ignore next - phaseRanges covers [0, total) exhaustively */
  return { index: null, name: null, phaseIndex: null, complete: true, frame: f };
}

/**
 * The script taps. These are CLIENT messages the sim injects on the shopkeeper's
 * behalf, except the pay, which is deliberately NOT a client message: paying a
 * link happens on the gateway, and modelling it as a UI tap would put the one
 * action that can settle a session inside the UI.
 */
export const MARKS = Object.freeze([
  Object.freeze({
    frame: ENROL_AT,
    verb: 'enrol_sticker',
    label: `enrol the sticker "${STICKER_NAME}"`,
    detail: 'SAAF stacks the burst it has been collecting and PEEL gets the '
      + 'reference it compares every later frame against. Before this frame PEEL '
      + 'correctly reports UNREGISTERABLE — there is nothing enrolled to compare to.',
  }),
  Object.freeze({
    frame: DONE_AT,
    verb: 'done',
    label: 'close the basket and mint a payment link',
    detail: 'the session leaves BASKET_OPEN for AWAITING_SETTLEMENT. An intent '
      + 'for the exact total is opened. Nothing is settled and nothing is green.',
  }),
  Object.freeze({
    frame: PAY_AT,
    verb: '(not a client message)',
    label: 'the CUSTOMER pays the link on the gateway',
    detail: 'the gateway signs a webhook with HMAC-SHA256 and delivers it to the '
      + 'brain, which runs the real four-part green predicate on it. This is the '
      + 'only event in the entire script that can move the session to PAID.',
  }),
]);

/**
 * THE BEAT LIST. One entry per phase of the script, in order.
 *
 * `watch` is what a reviewer should look at while the beat is highlighted, and
 * every number in it was READ OFF a real run of
 * `python -m gawaah.brain_server --sim --dry-run`, not estimated. The frame
 * numbers in the prose are that run's frame numbers.
 */
export const BEATS = Object.freeze(phaseRanges().map((r) => Object.freeze({
  ...r,
  ...({
    settle: {
      title: 'the bare mat',
      what: 'eight frames of the empty TAKHTI with the printed sticker on it. '
        + 'Frame 0 becomes the reference frame for MUDRA, for CHILLA and for the '
        + 'placement detector.',
      why: 'this is the cold state, and it is the state every panel was stuck in '
        + 'before there was a way to feed them. Every abstention on screen right '
        + 'now is correct: nothing has happened yet, so nothing is known.',
      watch: 'MUDRA moves from mudra_no_reference_frame to NONE once it has a '
        + 'reference. PEEL stays UNREGISTERABLE — nothing has been enrolled. '
        + 'Total 0 paise, 0 amber, and the ledger head moves once, for the '
        + 'session-open line.',
    },
    goods: {
      title: 'a packet is sold',
      what: 'a textured packet appears at y=180 mm, SITS STILL, then walks 12 mm '
        + 'per frame to y=352 mm, past the sell line at y=340 mm, and holds.',
      why: 'the sitting still is not padding. placement.py will not call a blob '
        + 'STABLE until it has been motionless for its dwell count, and the brain '
        + 'only identifies a stable placement — a packet that walks in from off-mat '
        + 'is never registered, and its crossing then FREEZES the total instead of '
        + 'billing it. That is the gate working; the schedule was fixed, not the gate.',
      watch: 'the total goes 0 -> 2850 paise at frame 31, when the line crossing '
        + 'has been held for the three frames LineZone requires. Integer paise, '
        + 'never a float, all the way from the gallery price to this screen.',
    },
    screen: {
      title: 'the customer pays, and CHILLA still says AMBER',
      what: 'the minted link is paid on the gateway. Its signed webhook reaches '
        + 'the brain and the session goes PAID at frame 38. A phone-shaped '
        + 'emissive rectangle then appears on the mat and CHILLA reads it.',
      why: 'CHILLA finds the screen and MATCHES the amount against the settlement '
        + 'mirror — and reports AMBER anyway. That is invariant 2 rendered as a '
        + 'screenshot: the counter is PAID because a signature-verified webhook '
        + 'said so, and CHILLA agreeing had nothing to do with it. Corroboration '
        + 'is not authorisation.',
      watch: `then watch it come apart, on purpose. A phone laid on the billing `
        + `mat is also an OBJECT on the billing mat. The placement detector sees `
        + `it, cannot identify it, and after ${REFUSE_AFTER_FRAMES} frames admits `
        + `it as an AMBER line — which moves the session to AMBER, clears the `
        + `intent, and drops the displayed total back to 0 at frame 42. The `
        + `counter refusing to guess is worth more than a tidy demo, so the `
        + `collision is left visible instead of being tuned away.`,
    },
    hand: {
      title: 'a hand, read as geometry',
      what: 'an open palm enters the frame: a palm disc and five finger capsules, '
        + 'drawn in millimetres so its area is a real measurement.',
      why: 'MUDRA reads OPEN / FIST / GOODS / AMBIGUOUS off solidity, convexity '
        + 'defect count and compactness. There is no model and no weights — the '
        + 'hand is an OCCLUDER of a plane whose appearance is already known, which '
        + 'is why this can run on a phone with nothing downloaded.',
      watch: 'MUDRA commits to OPEN at frame 53, after the four frames it requires '
        + 'before it will name a gesture. The palm is also an unidentifiable object, '
        + 'so it becomes a SECOND amber line — the same collision as the phone, and '
        + 'the same correct refusal.',
    },
    tamper: {
      title: 'the sticker is tampered with',
      what: 'one sixteenth of the printed sticker is replaced with INVERTED '
        + 'modules — not blanked, and not re-randomised.',
      why: 'both of those were measured and both were wrong. A blanked patch is '
        + 'structure DESTROYED, which ident_sticker blinds out as glare or a thumb '
        + 'rather than substitution, and it measured 0.0000 ignited. A freshly '
        + 'randomised patch agrees with the original on about half its cells by '
        + 'chance and measured 0.0262 — under the 3 % gate, which would have '
        + 'shipped a "tamper" beat that reads GENUINE.',
      watch: 'PEEL turns TAMPERED at frame 62 with an ignited fraction around 6 %, '
        + 'over its 3 % gate. PEEL WARNS. It cannot authorise, it cannot refuse a '
        + 'sale, and it cannot move one paisa.',
    },
  })[r.name],
})));

// ===========================================================================
// 2. NUMBERS AND TEXT. Money is integer paise everywhere; the rupee string is
//    built with integer arithmetic so there is no float anywhere on the path
//    from the gallery price to the pixels.
// ===========================================================================

/** 2850 -> "28.50". Integer ops only: no division that could produce a float. */
export function rupeesFromPaise(paise) {
  if (!Number.isInteger(paise)) return null;
  const neg = paise < 0;
  const abs = neg ? -paise : paise;
  const minor = abs % 100;
  const major = (abs - minor) / 100;   // exact: abs - minor is a multiple of 100
  return `${neg ? '-' : ''}${major}.${String(minor).padStart(2, '0')}`;
}

/** The display form of a paise figure, or the abstention dash. */
export function paiseLabel(paise) {
  const r = rupeesFromPaise(paise);
  return r === null ? '—' : `₹${r}`;
}

export function countLabel(n) {
  return Number.isInteger(n) && n >= 0 ? String(n) : '—';
}

/** First 12 hex of a chain head, or the dash. Never truncates to fewer. */
export function headLabel(head) {
  if (typeof head !== 'string' || head.length < 12) return '—';
  return `${head.slice(0, 12)}…`;
}

// ===========================================================================
// 3. THE MODEL. A fold over brain messages plus two out-of-band facts: the
//    /health probe and a clock tick. Pure — `at` is passed in, never read from
//    Date.now, so the whole thing is deterministic under test.
// ===========================================================================

export const PROV_SIMULATED = 'SIMULATED';
export const PROV_LIVE = 'NOT_SIMULATED';
export const PROV_UNKNOWN = 'PROVENANCE_UNKNOWN';

export const BEAT_FROM_BRAIN = 'brain_sim_message';
export const BEAT_INFERRED = 'inferred_from_frame_index';
export const BEAT_UNKNOWN = 'unknown';

/** brain_server's refusal reason for a control on a brain with no sim attached. */
export const SIM_NOT_ENABLED = 'SIM_NOT_ENABLED';

export const CTRL_IDLE = 'IDLE';
export const CTRL_SENT = 'SENT';
export const CTRL_ACCEPTED = 'ACCEPTED';
export const CTRL_REFUSED = 'REFUSED';
export const CTRL_NO_TRANSPORT = 'NO_TRANSPORT';
export const CTRL_UNANSWERED = 'UNANSWERED';

/** How long a control may go unanswered before the panel stops waiting. */
export const CONTROL_ANSWER_MS = 2000;
/** The window over which "is it running?" is measured. */
export const RUNNING_WINDOW_MS = 2000;

export const CONTROLS = Object.freeze([
  Object.freeze({ action: 'start', label: 'RUN DEMO', hint: 'drive the script from wherever it is' }),
  Object.freeze({ action: 'stop', label: 'PAUSE', hint: 'stop the frame pump; the board holds' }),
  Object.freeze({ action: 'step', label: 'STEP', hint: 'exactly one frame, then stop' }),
  Object.freeze({ action: 'reset', label: 'RESET', hint: 'back to frame 0 of the script' }),
]);

export const CONTROL_ACTIONS = Object.freeze(CONTROLS.map((c) => c.action));

/**
 * The client -> brain control message. Throws on an action the brain could not
 * possibly understand, so a typo fails here rather than becoming a refusal the
 * user has to interpret.
 */
export function simMessage(action) {
  if (!CONTROL_ACTIONS.includes(action)) {
    throw new TypeError(`simMessage: unknown action ${JSON.stringify(action)}`);
  }
  return { type: 'sim', action };
}

export function initialModel() {
  return Object.freeze({
    // provenance evidence
    health: null,              // the last /health body we read, or null
    healthError: null,         // why we could not read it
    simMessageSeen: null,      // the last {"type":"sim"} the brain sent

    // headline numbers
    totalPaise: null,
    amberCount: null,       // bill lines flagged amber: the excluded ones
    brainAmberCount: null,  // the brain's own amber_count field, read separately
    amberReasons: Object.freeze([]),
    ledgerLines: null,
    ledgerHead: null,

    // session truth, straight from the brain
    sessionState: null,
    sessionId: null,
    intentPaise: null,
    settledPaymentId: null,
    lastWebhookReason: null,
    lineCount: null,
    amberNames: [],

    // capability headlines, for the supporting notes
    peel: null,
    chilla: null,
    mudra: null,
    saaf: null,

    // where in the script we are.
    //
    // `simTag` is the block brain_server._stamp() puts on EVERY outbound
    // message when a SimDriver is attached: simulated, sim_run, beat,
    // beat_label, beat_detail, beat_index, beat_of, sim_frame. It is the
    // authoritative script position and it is authoritative per-message — a
    // replayed panel carries the beat it was MEASURED on, not the beat that is
    // on screen now, so this panel never re-attributes an old reading.
    frameIndex: null,
    beatSource: BEAT_UNKNOWN,
    simTag: null,              // the last sim tag seen, whole
    simStatus: null,           // the last {"type":"sim"} status message, whole

    // liveness, MEASURED
    stateTimes: Object.freeze([]),
    now: null,

    // controls
    control: Object.freeze({ code: CTRL_IDLE, action: null, detail: null, known: Object.freeze([]), at: null }),

    // transport / mounting, reported rather than assumed
    transport: Object.freeze({ code: 'UNKNOWN', detail: 'the panel has not reported a transport yet' }),
    seam: Object.freeze({ registered: false, reason: null }),

    // bookkeeping
    messages: 0,
    byType: Object.freeze({}),
    lastRefusal: null,
  });
}

/**
 * Normalise anything into a usable model.
 *
 * Every derived view and every renderer starts here. `model || initialModel()`
 * is not enough — a HALF-model (an object with some of the fields, which is
 * exactly what a caller building state by hand produces) passes the truthiness
 * check and then blows up on `.stateTimes.length`. A panel whose job is to be
 * the thing you look at when nothing else works must not be the thing that
 * throws.
 */
export function asModel(model) {
  // Fast path, and it matters: a well-formed model comes back BY IDENTITY, so
  // folding a message the panel does not care about is a genuine no-op rather
  // than a fresh object every frame.
  if (model && typeof model === 'object' && !Array.isArray(model)
      && Array.isArray(model.stateTimes) && Array.isArray(model.amberNames)
      && Array.isArray(model.amberReasons)
      && model.control && typeof model.control === 'object' && Array.isArray(model.control.known)
      && model.transport && typeof model.transport === 'object'
      && model.seam && typeof model.seam === 'object'
      && model.byType && typeof model.byType === 'object'
      && Number.isInteger(model.messages)) {
    return model;
  }
  const base = initialModel();
  if (!model || typeof model !== 'object' || Array.isArray(model)) return base;
  const m = { ...base, ...model };
  if (!Array.isArray(m.stateTimes)) m.stateTimes = base.stateTimes;
  if (!Array.isArray(m.amberNames)) m.amberNames = base.amberNames;
  if (!Array.isArray(m.amberReasons)) m.amberReasons = base.amberReasons;
  if (!m.control || typeof m.control !== 'object') m.control = base.control;
  if (!Array.isArray(m.control.known)) m.control = { ...m.control, known: [] };
  if (!m.transport || typeof m.transport !== 'object') m.transport = base.transport;
  if (!m.seam || typeof m.seam !== 'object') m.seam = base.seam;
  if (!m.byType || typeof m.byType !== 'object') m.byType = base.byType;
  if (!Number.isInteger(m.messages) || m.messages < 0) m.messages = base.messages;
  return m;
}

function withT(model, patch) {
  return Object.freeze({ ...model, ...patch });
}

function bump(byType, t) {
  const next = { ...byType };
  next[t] = (next[t] || 0) + 1;
  return Object.freeze(next);
}

function trimTimes(times, at, windowMs) {
  if (!Number.isFinite(at)) return times;
  const keep = times.filter((t) => at - t <= windowMs);
  return Object.freeze(keep.length > 64 ? keep.slice(keep.length - 64) : keep);
}

/**
 * Pull the simulation tag off a message, or null if it carries none.
 *
 * A message with `simulated: false` is a real answer and is kept as one — the
 * absence of a tag and a tag that says "not simulated" are different facts and
 * this panel keeps them different.
 */
export function simTagOf(msg) {
  if (!msg || typeof msg !== 'object') return null;
  if (typeof msg.simulated !== 'boolean') return null;
  const t = { simulated: msg.simulated };
  for (const k of ['sim_run', 'beat', 'beat_label', 'beat_detail', 'beat_index', 'beat_of', 'sim_frame']) {
    if (msg[k] !== undefined && msg[k] !== null) t[k] = msg[k];
  }
  return Object.freeze(t);
}

/**
 * One brain message in, a new model out. Unknown message types are COUNTED and
 * otherwise ignored — this panel is a reader and must not care what else the
 * protocol grows.
 */
export function reduceDemo(model, msg, at = null) {
  const m = asModel(model);
  if (!msg || typeof msg !== 'object' || typeof msg.type !== 'string') return m;
  const now = Number.isFinite(at) ? at : m.now;
  let next = withT(m, {
    messages: m.messages + 1,
    byType: bump(m.byType, msg.type),
    now,
  });

  // frame_index is -1 on the opening `hello` burst, before any frame has been
  // ingested. That is not frame minus one of the script; it is "no frame yet",
  // and treating it as a position would highlight a beat off the front of the
  // list. Negative indices are dropped rather than clamped to 0.
  if (Number.isInteger(msg.frame_index) && msg.frame_index >= 0) {
    next = withT(next, { frameIndex: msg.frame_index });
    if (next.beatSource !== BEAT_FROM_BRAIN) {
      next = withT(next, { beatSource: BEAT_INFERRED });
    }
  }

  // The sim tag, off ANY message type. See initialModel().
  const tag = simTagOf(msg);
  if (tag) {
    next = withT(next, { simTag: tag });
    if (Number.isInteger(tag.sim_frame)) next = withT(next, { beatSource: BEAT_FROM_BRAIN });
  }

  switch (msg.type) {
    case 'state': {
      const lines = Array.isArray(msg.lines) ? msg.lines : [];
      // THE AMBER HEADLINE IS DEFINED, not guessed at. It counts BILL LINES
      // CARRYING `amber: true` — the lines whose price_paise is null and which
      // are therefore excluded from total_paise. That is the quantity the
      // number beside it is about.
      //
      // The brain also publishes `amber_count`/`amber_items`, and on the sim
      // script those come back 0/[] while three of its own lines are flagged
      // amber. Both are read; when they disagree the panel prints BOTH and
      // raises an abstention rather than quietly choosing the flattering one.
      const amberLines = Array.isArray(msg.lines) ? lines.filter((li) => li && li.amber === true) : null;
      const brainAmber = Number.isInteger(msg.amber_count)
        ? msg.amber_count
        : (Array.isArray(msg.amber_items) ? msg.amber_items.length : null);
      next = withT(next, {
        totalPaise: Number.isInteger(msg.total_paise) ? msg.total_paise : next.totalPaise,
        amberCount: amberLines === null ? next.amberCount : amberLines.length,
        brainAmberCount: brainAmber === null ? next.brainAmberCount : brainAmber,
        ledgerLines: Number.isInteger(msg.ledger_lines) ? msg.ledger_lines : next.ledgerLines,
        ledgerHead: typeof msg.ledger_head === 'string' ? msg.ledger_head : next.ledgerHead,
        sessionState: typeof msg.session_state === 'string' ? msg.session_state : next.sessionState,
        sessionId: typeof msg.session_id === 'string' ? msg.session_id : next.sessionId,
        intentPaise: Number.isInteger(msg.intent_amount_paise) ? msg.intent_amount_paise : null,
        settledPaymentId: typeof msg.settled_payment_id === 'string' ? msg.settled_payment_id : null,
        lastWebhookReason: typeof msg.last_webhook_reason === 'string'
          ? msg.last_webhook_reason : next.lastWebhookReason,
        lineCount: lines.length,
        amberNames: amberLines === null ? next.amberNames : Object.freeze(amberLines.map(
          (li) => (li && (li.name || li.item_id)) || 'unnamed'
        )),
        amberReasons: amberLines === null ? next.amberReasons : Object.freeze(amberLines.map(
          (li) => (li && li.reason) || 'no reason given'
        )),
        stateTimes: Number.isFinite(now)
          ? trimTimes([...next.stateTimes, now], now, RUNNING_WINDOW_MS)
          : next.stateTimes,
      });
      break;
    }
    case 'ledger':
      next = withT(next, {
        ledgerLines: Number.isInteger(msg.count) ? msg.count : next.ledgerLines,
        ledgerHead: typeof msg.head === 'string' ? msg.head : next.ledgerHead,
      });
      break;
    case 'peel': case 'chilla': case 'mudra': case 'saaf':
      next = withT(next, { [msg.type]: Object.freeze({ ...msg }) });
      break;
    case 'refused':
      next = withT(next, { lastRefusal: Object.freeze({ ...msg }) });
      if (next.control.code === CTRL_SENT) {
        next = withT(next, {
          control: Object.freeze({
            code: CTRL_REFUSED,
            action: next.control.action,
            detail: `${msg.reason || 'no reason given'} — ${msg.detail || 'no detail given'}`,
            known: Object.freeze(Array.isArray(msg.known) ? msg.known.slice() : []),
            at: now,
          }),
        });
      }
      break;
    case 'sim': {
      next = withT(next, {
        simMessageSeen: Object.freeze({ ...msg }),
        simStatus: Object.freeze({ ...msg }),
      });
      if (next.control.code === CTRL_SENT) {
        next = withT(next, {
          control: Object.freeze({
            code: msg.ok === false ? CTRL_REFUSED : CTRL_ACCEPTED,
            action: next.control.action,
            detail: [typeof msg.mode === 'string' ? `mode ${msg.mode}` : null,
              typeof msg.detail === 'string' && msg.detail ? msg.detail : null]
              .filter(Boolean).join(' — ') || 'the brain acknowledged the control',
            known: Object.freeze(Array.isArray(msg.actions) ? msg.actions.slice() : []),
            at: now,
          }),
        });
      }
      break;
    }
    default:
      break;
  }
  return next;
}

export function deriveDemo(messages, opts = {}) {
  const start = asModel(opts.model);
  const list = Array.isArray(messages) ? messages : [];
  return list.reduce((m, entry) => {
    if (entry && typeof entry === 'object' && 'msg' in entry) {
      return reduceDemo(m, entry.msg, entry.at);
    }
    return reduceDemo(m, entry, opts.at);
  }, start);
}

/** Record a `GET /health` body — the strongest provenance evidence we have. */
export function noteHealth(model, health, at = null) {
  const m = asModel(model);
  if (!health || typeof health !== 'object') {
    return withT(m, {
      healthError: typeof health === 'string' ? health : 'health body was not an object',
      now: Number.isFinite(at) ? at : m.now,
    });
  }
  return withT(m, {
    health: Object.freeze({ ...health }),
    healthError: null,
    now: Number.isFinite(at) ? at : m.now,
  });
}

/** Record a failed `GET /health`. Named, never silent. */
export function noteHealthError(model, reason, at = null) {
  const m = asModel(model);
  return withT(m, {
    healthError: String(reason || 'unknown'),
    now: Number.isFinite(at) ? at : m.now,
  });
}

export function noteTransport(model, code, detail) {
  return withT(asModel(model), {
    transport: Object.freeze({ code: String(code), detail: String(detail || '') }),
  });
}

export function noteSeam(model, registered, reason) {
  return withT(asModel(model), {
    seam: Object.freeze({ registered: registered === true, reason: reason == null ? null : String(reason) }),
  });
}

/** A control was pressed. Records the ASK; the answer arrives as a message. */
export function noteControlSent(model, action, at = null) {
  const m = asModel(model);
  return withT(m, {
    now: Number.isFinite(at) ? at : m.now,
    control: Object.freeze({
      code: CTRL_SENT, action, detail: 'sent; waiting for the brain to answer',
      known: Object.freeze([]), at: Number.isFinite(at) ? at : null,
    }),
  });
}

/** A control could not be sent at all. */
export function noteControlUnsendable(model, action, detail, at = null) {
  const m = asModel(model);
  return withT(m, {
    now: Number.isFinite(at) ? at : m.now,
    control: Object.freeze({
      code: CTRL_NO_TRANSPORT, action, detail: String(detail),
      known: Object.freeze([]), at: Number.isFinite(at) ? at : null,
    }),
  });
}

/**
 * The clock moving. Two jobs: expire the running-window samples so `running`
 * can go false when frames stop, and time out a control the brain never
 * answered. Both are the panel noticing an absence, which is the only way an
 * absence ever gets noticed.
 */
export function noteTick(model, at) {
  const m = asModel(model);
  if (!Number.isFinite(at)) return m;
  let next = withT(m, { now: at, stateTimes: trimTimes(m.stateTimes, at, RUNNING_WINDOW_MS) });
  const c = next.control;
  if (c.code === CTRL_SENT && Number.isFinite(c.at) && at - c.at >= CONTROL_ANSWER_MS) {
    next = withT(next, {
      control: Object.freeze({
        code: CTRL_UNANSWERED, action: c.action,
        detail: `no reply in ${(CONTROL_ANSWER_MS / 1000).toFixed(1)} s. This panel `
          + 'cannot tell you whether the control was honoured, so it will not say '
          + 'that it was.',
        known: Object.freeze([]), at: c.at,
      }),
    });
  }
  return next;
}

// ===========================================================================
// 4. DERIVED VIEWS. Provenance, beat and liveness, each with the reason it
//    reached the answer attached to it.
// ===========================================================================

export function provenanceOf(model) {
  const m = asModel(model);
  // Strongest evidence first: brain_server stamps `simulated` onto EVERY
  // outbound message when a SimDriver is attached, so this is a statement about
  // the very message the numbers on screen came from, not about the process in
  // general. A /health probe is one round trip older and could in principle
  // describe a different run.
  if (m.simTag && typeof m.simTag.simulated === 'boolean') {
    return m.simTag.simulated
      ? {
        code: PROV_SIMULATED,
        evidence: 'the brain stamped simulated:true on the message these numbers came from'
          + (Number.isInteger(m.simTag.sim_run) ? ` (sim run ${m.simTag.sim_run})` : ''),
        detail: SIMULATED_DETAIL,
      }
      : {
        code: PROV_LIVE,
        evidence: 'the brain stamped simulated:false on the message these numbers came from',
        detail: LIVE_DETAIL,
      };
  }
  if (m.health && typeof m.health.sim === 'boolean') {
    return m.health.sim
      ? {
        code: PROV_SIMULATED,
        evidence: 'GET /health on this same origin reports sim: true',
        detail: SIMULATED_DETAIL,
      }
      : {
        code: PROV_LIVE,
        evidence: 'GET /health on this same origin reports sim: false',
        detail: LIVE_DETAIL,
      };
  }
  return {
    code: PROV_UNKNOWN,
    evidence: m.healthError
      ? `GET /health failed: ${m.healthError}`
      : 'GET /health has not been read yet',
    detail: UNKNOWN_DETAIL,
  };
}

export const SIMULATED_DETAIL =
  'Every frame reaching the counter right now is drawn by gawaah/brain_server.py, '
  + 'not seen by a camera. There is no mat on a table and no phone in a hand.';

export const LIVE_DETAIL =
  'This brain has NO synthetic frame source at all, so nothing on this screen was '
  + 'drawn: whatever it has counted, it counted from a camera pointed at a printed '
  + 'mat. If the counter is empty, that is because nothing has been shown to it.';

export const UNKNOWN_DETAIL =
  'This panel cannot tell whether these frames are synthetic or came from a camera, '
  + 'so it will not tell you either. It is not going to guess about the one fact '
  + 'that decides whether anything on this screen means something.';

export function beatOf(model) {
  const m = asModel(model);
  const unknown = {
    index: null, name: null, phaseIndex: null, complete: false, frame: null,
    source: BEAT_UNKNOWN,
  };
  // A beat whose index came back null is a position that could not be placed on
  // the script at all, so the SOURCE is unknown too — reporting "inferred" for
  // a beat nobody could infer would name a method that produced no answer.
  const tag = m.simTag;
  if (tag && Number.isInteger(tag.sim_frame)) {
    const b = beatAt(tag.sim_frame);
    return b.index === null ? unknown : {
      ...b,
      source: BEAT_FROM_BRAIN,
      label: typeof tag.beat_label === 'string' ? tag.beat_label : null,
      detail: typeof tag.beat_detail === 'string' ? tag.beat_detail : null,
      brainName: typeof tag.beat === 'string' ? tag.beat : null,
    };
  }
  if (Number.isInteger(m.frameIndex)) {
    const b = beatAt(m.frameIndex);
    return b.index === null ? unknown : { ...b, source: BEAT_INFERRED };
  }
  return unknown;
}

/**
 * The brain names the beat it is on; this panel computes the beat that frame
 * ought to be. When those two disagree, SAY SO rather than picking a winner.
 *
 * A disagreement means the beat list on screen and the script the brain is
 * running are not the same script — the numbers would still be real and the
 * caption beside them would be wrong, which is precisely the failure this whole
 * panel exists to avoid. `hold` is not a disagreement: it is the driver's name
 * for "past the last frame", which the beat list shows as the final beat with
 * the script marked complete.
 */
export function disagreementOf(model) {
  const m = asModel(model);
  const b = beatOf(m);
  if (!b.brainName || !b.name) return null;
  if (b.brainName === b.name) return null;
  if (b.brainName === HOLD_BEAT && b.complete) return null;
  return {
    code: 'demo_beat_disagreement',
    brain: b.brainName,
    panel: b.name,
    frame: b.frame,
  };
}

/** The beat machine's mode, if it has told us. */
export function modeOf(model) {
  const m = asModel(model);
  const s = m.simStatus;
  if (!s || typeof s.mode !== 'string') {
    return { mode: null, note: 'the brain has not published a beat-machine mode.' };
  }
  return { mode: s.mode, note: MODE_NOTE[s.mode] || 'this mode is not one this panel knows about.' };
}

export const BEAT_SOURCE_NOTE = Object.freeze({
  [BEAT_FROM_BRAIN]: 'the brain stamped its own script position (sim_frame) onto '
    + 'the message, so this is not an inference.',
  [BEAT_INFERRED]: 'INFERRED. The brain does not publish where it is in the script, '
    + 'so the beat is read off frame_index, which is only the script position if '
    + 'every frame this brain accepted came from the sim. If a camera also fed it, '
    + 'this highlight is wrong and nothing here would know.',
  [BEAT_UNKNOWN]: 'no frame_index has arrived, so the script position is not known.',
});

/**
 * Is the script actually advancing? MEASURED from the arrival of `state`
 * messages, not from which button was last pressed.
 *
 * Returns null — genuinely unknown — until there has been a full window to
 * measure over. Reporting "stopped" during the first 300 ms of a live socket
 * would be a confident wrong answer, which is the one thing this counter is
 * built not to do.
 */
export function livenessOf(model) {
  const m = asModel(model);
  const n = m.stateTimes.length;
  if (!Number.isFinite(m.now)) {
    return { running: null, fps: null, reason: 'no clock reading yet' };
  }
  if (n === 0) {
    return {
      running: null, fps: null,
      reason: `no state message in the last ${RUNNING_WINDOW_MS / 1000} s`,
    };
  }
  const span = m.now - m.stateTimes[0];
  const fps = span > 0 ? (n - 1) * 1000 / span : null;
  return {
    running: true,
    fps: fps !== null && Number.isFinite(fps) ? fps : null,
    reason: `${n} state message${n === 1 ? '' : 's'} in the last `
      + `${RUNNING_WINDOW_MS / 1000} s`,
  };
}

// ===========================================================================
// 5. THE COMMENTARY. One sentence, chosen by the highest-priority thing that
//    is true, saying WHY the counter is doing what it is doing. Never more
//    than one primary line: a running commentary that says five things at once
//    is not a commentary.
// ===========================================================================

export const C_NO_BRAIN = 'demo_no_brain_message';
export const C_PAID = 'demo_paid';
export const C_AWAITING = 'demo_awaiting_settlement';
export const C_AMBER = 'demo_amber_excluded';
export const C_COUNTING = 'demo_counting';
export const C_OPEN_EMPTY = 'demo_nothing_on_the_mat';

export function commentaryFor(model) {
  const m = asModel(model);
  if (m.messages === 0) {
    return {
      code: C_NO_BRAIN,
      text: 'No message has reached this panel yet, so there is nothing to '
        + 'explain. Everything below is the cold state.',
    };
  }
  // PAID is the CURRENT session state, not a memory of one. On the sim script
  // the session settles at frame 38 and then goes AMBER at 42 when the phone
  // itself becomes an unidentified object — the settled payment is still real,
  // but leading with "SETTLED" while the counter is amber would describe the
  // wrong moment. The settlement is carried into the amber line instead.
  if (m.settledPaymentId && m.sessionState === 'PAID') {
    return {
      code: C_PAID,
      text: `SETTLED. Payment ${m.settledPaymentId} moved this session to PAID — `
        + 'and only because a webhook arrived whose HMAC verified over the raw '
        + 'bytes before any JSON was parsed, whose event was in the green set, '
        + 'whose notes.session_id matched an OPEN intent, and whose amount '
        + 'equalled that intent exactly. Any one of those four failing and it '
        + 'would still be amber.',
    };
  }
  if (m.sessionState === 'AWAITING_SETTLEMENT') {
    return {
      code: C_AWAITING,
      text: `The basket is closed and an intent for ${paiseLabel(m.intentPaise)} is `
        + 'open. Nothing is green: the counter is waiting for a signed webhook and '
        + 'will keep waiting rather than assume the customer paid.',
    };
  }
  if (Number.isInteger(m.amberCount) && m.amberCount > 0) {
    const who = m.amberNames.length ? m.amberNames.join(', ') : 'an unidentified object';
    const why = m.amberReasons.length ? ` (${[...new Set(m.amberReasons)].join(', ')})` : '';
    return {
      code: C_AMBER,
      text: `AMBER. ${who} is on the mat and the counter cannot say what it is${why}, so `
        + `the line is EXCLUDED from the total — the total beside it is the sum of `
        + `the identified lines only. It would rather show you a hole than invent `
        + `a price to fill it.`
        + (m.settledPaymentId
          ? ` (Payment ${m.settledPaymentId} settled earlier in this session and `
            + 'that has not been undone; what is amber is what is on the mat NOW.)'
          : ''),
    };
  }
  if (Number.isInteger(m.totalPaise) && m.totalPaise > 0) {
    const n = Number.isInteger(m.lineCount) ? m.lineCount : 0;
    return {
      code: C_COUNTING,
      text: `Counting. ${n} identified line${n === 1 ? '' : 's'} on the bill, `
        + `${paiseLabel(m.totalPaise)} so far — matched against the enrolled gallery `
        + 'by size and appearance, priced from the gallery, in integer paise.',
    };
  }
  return {
    code: C_OPEN_EMPTY,
    text: 'Nothing identified on the mat yet. The counter is watching the plane '
      + 'and billing nothing, which is the correct output for an empty mat.',
  };
}

/**
 * Supporting notes: at most two, each a fact a capability panel is currently
 * reporting, phrased so a reviewer knows what it does NOT mean.
 */
export function notesFor(model) {
  const m = asModel(model);
  const out = [];
  if (m.peel && m.peel.verdict === 'TAMPERED') {
    const f = typeof m.peel.ignited_fraction === 'number'
      ? `${(m.peel.ignited_fraction * 100).toFixed(2)} %` : 'over gate';
    out.push({
      code: 'peel_tampered',
      text: `PEEL: ignited fraction ${f} — the sticker no longer matches what was `
        + 'enrolled. PEEL warns and nothing else: it cannot stop a sale and it '
        + 'cannot move a paisa.',
    });
  }
  if (m.chilla && m.chilla.verdict === 'MATCHED') {
    out.push({
      code: 'chilla_matched',
      text: 'CHILLA: the amount on the customer\'s screen matches a payment in the '
        + 'settlement mirror — and CHILLA still reports AMBER, because reading a '
        + 'screen is corroboration and corroboration is not authorisation.',
    });
  }
  if (m.mudra && typeof m.mudra.state === 'string'
      && m.mudra.state !== 'NONE' && m.mudra.decided !== false) {
    out.push({
      code: 'mudra_gesture',
      text: `MUDRA: ${m.mudra.state}, read from solidity `
        + `${typeof m.mudra.solidity === 'number' ? m.mudra.solidity.toFixed(3) : '?'} `
        + `and ${m.mudra.defects ?? '?'} convexity defects. Geometry against a known `
        + 'plane — no model, no weights, nothing downloaded.',
    });
  }
  return out.slice(0, 2);
}

// ===========================================================================
// 6. THE ABSTENTIONS. Everything this panel does not know, named. This list is
//    the point of the panel as much as the numbers are: a demo surface that
//    can only ever look confident is a sales tool, not an instrument.
// ===========================================================================

export function abstentionsFor(model) {
  const m = asModel(model);
  const out = [];
  const prov = provenanceOf(m);
  if (prov.code === PROV_UNKNOWN) {
    out.push({
      code: 'demo_provenance_unknown',
      text: `I do not know whether these frames are simulated. ${prov.evidence}. `
        + 'Until /health answers, treat every number on this panel as unattributed.',
    });
  }
  const beat = beatOf(m);
  if (beat.source === BEAT_UNKNOWN) {
    out.push({
      code: 'demo_no_script_position',
      text: 'I do not know where the script is. No frame_index has arrived, so no '
        + 'beat is highlighted below.',
    });
  } else if (beat.source === BEAT_INFERRED) {
    out.push({
      code: 'demo_beat_is_inferred',
      text: 'I do not know the script position for certain. The brain does not '
        + 'publish it, so the highlighted beat is inferred from frame_index and is '
        + 'only right if every frame this brain accepted came from the sim.',
    });
  }
  if (Number.isInteger(m.amberCount) && Number.isInteger(m.brainAmberCount)
      && m.amberCount !== m.brainAmberCount) {
    out.push({
      code: 'demo_amber_count_disagreement',
      text: `I do not know which amber count to believe. ${m.amberCount} bill `
        + `line${m.amberCount === 1 ? '' : 's'} carry amber:true and are excluded from `
        + `the total, but the brain's own amber_count field says ${m.brainAmberCount}. `
        + 'The tile above shows the excluded lines, because that is the number the '
        + 'total is missing. Both are printed rather than one being chosen quietly.',
    });
  }
  const dis = disagreementOf(m);
  if (dis) {
    out.push({
      code: dis.code,
      text: `The brain says frame ${dis.frame} is beat "${dis.brain}" and this beat `
        + `list says "${dis.panel}". They are running different scripts, so the `
        + 'caption beside these numbers is not to be trusted. The numbers are '
        + 'still the brain\'s; the story about them is not.',
    });
  }
  const live = livenessOf(m);
  if (live.running === null) {
    out.push({
      code: 'demo_liveness_unknown',
      text: `I do not know whether the script is advancing: ${live.reason}.`,
    });
  }
  // Two shapes say the same thing and both are real. A brain built WITH a sim
  // that is switched off answers `{"type":"sim", enabled:false}`; a brain built
  // WITHOUT one never sends a sim status at all and answers the control with
  // `{"type":"refused", reason:"SIM_NOT_ENABLED"}`. The second is what a real
  // browser got from `python -m gawaah.brain_server` with no --sim, and keying
  // only off the first missed it entirely.
  if ((m.simStatus && m.simStatus.enabled === false)
      || (m.lastRefusal && m.lastRefusal.reason === SIM_NOT_ENABLED)) {
    out.push({
      code: 'demo_sim_not_enabled',
      text: 'This brain was started without --sim, so there is no synthetic source '
        + 'to run and the controls above cannot do anything. That is the honest '
        + 'answer, not a fault: this build counts what a camera shows it, and '
        + 'there is nothing here to script.',
    });
  }
  if (m.simStatus && typeof m.simStatus.fault === 'string' && m.simStatus.fault) {
    out.push({
      code: 'demo_sim_faulted',
      text: `The beat machine FAULTED and stopped: ${m.simStatus.fault}. It will not `
        + 'carry on pushing frames it cannot vouch for. RESET to try again.',
    });
  }
  if (m.control.code === CTRL_REFUSED) {
    out.push({
      code: 'demo_controls_refused',
      text: `The brain refused the ${m.control.action} control: ${m.control.detail}. `
        + (m.control.known.length
          ? `It says it would accept: ${m.control.known.join(', ')}. `
          : '')
        + 'The buttons above are therefore not driving anything, and this panel '
        + 'will not pretend otherwise.',
    });
  }
  if (m.control.code === CTRL_UNANSWERED) {
    out.push({
      code: 'demo_control_unanswered',
      text: `I do not know whether ${m.control.action} was honoured: ${m.control.detail}`,
    });
  }
  if (m.control.code === CTRL_NO_TRANSPORT) {
    out.push({
      code: 'demo_no_transport',
      text: `I could not send ${m.control.action} at all: ${m.control.detail}`,
    });
  }
  if (m.totalPaise === null) {
    out.push({
      code: 'demo_no_total',
      text: 'I do not know the total: no state message has carried one.',
    });
  }
  if (m.ledgerLines === null) {
    out.push({
      code: 'demo_no_ledger',
      text: 'I do not know the ledger line count: no audit head has arrived.',
    });
  }
  return out;
}

// ===========================================================================
// 7. THE STANDING TEXT. These sentences are on screen whatever the state is.
//    They are the difference between a demo and a claim.
// ===========================================================================

export const GREEN_RULE =
  'GREEN NEVER COMES FROM THIS PANEL, OR FROM ANY PANEL. A session goes green '
  + 'on exactly one event: a webhook whose HMAC-SHA256 verified over the RAW '
  + 'BYTES before any JSON parse, whose event type is in the green set, whose '
  + 'notes.session_id matches an OPEN intent, and whose amount equals that '
  + 'intent to the paisa. Pressing RUN DEMO cannot produce one. Neither can a '
  + 'matching screen, a genuine sticker or a recognised hand.';

export const SIM_GATEWAY_RULE =
  'IN A SIMULATED RUN THE WEBHOOK IS SIGNED BY THE SIM GATEWAY. The signature '
  + 'check is real — the same HMAC over the same raw bytes, the same four-part '
  + 'predicate — but the secret it verifies against exists only inside the sim '
  + 'process and the payment behind it is not money. A simulated PAID proves the '
  + 'predicate runs; it does not prove anyone paid anybody.';

export const REAL_MONEY_NOTE =
  'The live path is separate and has been exercised once for real: a genuine '
  + 'Razorpay test-mode Payment Link was paid, the signature-verified webhook '
  + 'moved the counter to PAID, and an unknown item on the mat stayed amber and '
  + 'out of the total.';

/** What is simulated, and what is the real module, side by side. */
export const SIMULATED_PARTS = Object.freeze([
  'THE CAMERA. Frames are drawn into an 840x1188 buffer instead of being seen.',
  'THE BROWSER\'S HOMOGRAPHY. The buffer is already rectified, so no mat is '
    + 'detected and no lock is adjudicated.',
  'THE GATEWAY. Payment links are minted and paid by gawaah/rzp_sim.py, which '
    + 'signs its webhooks with a secret that never leaves that process.',
  'THE CUSTOMER. Nobody taps anything; the sim pays the link at frame '
    + `${PAY_AT} on the customer's behalf.`,
]);

export const REAL_PARTS = Object.freeze([
  'THE PLACEMENT DETECTOR, the centroid tracker and the sell line, running on '
    + 'these pixels frame by frame.',
  'THE IDENTIFIER and the price gallery — an unknown object is genuinely '
    + 'unknown and genuinely excluded.',
  'MUDRA, PEEL, CHILLA and SAAF. Every number those panels show was measured '
    + 'off these frames by the same code a camera would drive.',
  'THE SESSION KERNEL, the sqlite store, the hash-chained ledger, and the '
    + 'four-part green predicate that the signed webhook is put through.',
]);

// ===========================================================================
// 8. STYLE. Shipped from this module so the panel is self-contained.
//
//    index.html sets `style-src 'self'`, which blocks the CONTENT of an inline
//    <style> element. A constructable stylesheet is CSSOM, not an inline style,
//    and is not blocked — but rather than assert that, installStyles PROBES:
//    it renders a sentinel and reads the computed value back. If the styles did
//    not take, the panel says so in `data-styles` instead of quietly rendering
//    as unstyled text and letting someone conclude the module is broken.
//
//    No green and no fraud-red anywhere in here, and demo.test.mjs greps this
//    string to make sure it stays that way.
// ===========================================================================

export const STYLE_PROBE_CLASS = 'gawaah-demo-style-probe';

export const CSS_TEXT = `
.gawaah-demo{display:block;color:var(--ink,#eef1f6);font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.gawaah-demo-standalone{padding:18px;background:var(--panel,#141821);border:1px solid var(--line,#232a36);border-radius:10px;margin:14px}
.${STYLE_PROBE_CLASS}{position:absolute;left:-9999px;letter-spacing:3px}
.demo-badge{display:block;padding:12px 14px;border-radius:8px;margin:0 0 14px;font:700 15px/1.35 var(--mono,ui-monospace,Menlo,monospace);letter-spacing:.16em}
.demo-badge-detail{display:block;margin-top:7px;font:400 13px/1.5 ui-sans-serif,system-ui,sans-serif;letter-spacing:0;opacity:.92}
.demo-badge-evidence{display:block;margin-top:5px;font:400 12px/1.5 var(--mono,ui-monospace,Menlo,monospace);letter-spacing:0;opacity:.72}
.gawaah-demo[data-provenance="SIMULATED"] .demo-badge{color:#12141a;background:repeating-linear-gradient(135deg,var(--amber,#e0a33c) 0 12px,color-mix(in srgb,var(--amber,#e0a33c) 78%,#000) 12px 24px);border:2px solid var(--amber,#e0a33c)}
.gawaah-demo[data-provenance="NOT_SIMULATED"] .demo-badge{color:var(--white,#eef1f6);background:transparent;border:2px solid var(--white,#eef1f6)}
.gawaah-demo[data-provenance="PROVENANCE_UNKNOWN"] .demo-badge{color:var(--grey,#6b7480);background:transparent;border:2px dashed var(--grey,#6b7480)}
.demo-heads{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 14px}
.demo-head{flex:1 1 170px;min-width:150px;padding:12px 14px;background:var(--panel-2,#1b202b);border:1px solid var(--line,#232a36);border-radius:8px}
.demo-head-k{display:block;font:700 11px/1 var(--mono,ui-monospace,Menlo,monospace);letter-spacing:.2em;color:var(--ink-faint,#6d7686)}
.demo-head-v{display:block;margin-top:9px;font:700 32px/1 var(--mono,ui-monospace,Menlo,monospace);font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1;color:var(--ink,#eef1f6)}
.demo-head-sub{display:block;margin-top:7px;font:400 12px/1.45 ui-sans-serif,system-ui,sans-serif;color:var(--ink-dim,#97a0b0)}
.demo-head[data-amber="1"] .demo-head-v{color:var(--amber,#e0a33c)}
.demo-head-sim{display:inline-block;margin-left:8px;padding:2px 6px;border:1px solid var(--amber,#e0a33c);border-radius:4px;font:700 10px/1 var(--mono,ui-monospace,Menlo,monospace);letter-spacing:.16em;color:var(--amber,#e0a33c);vertical-align:middle}
.demo-say{margin:0 0 14px;padding:12px 14px;border-left:3px solid var(--amber,#e0a33c);background:var(--panel-2,#1b202b);border-radius:0 8px 8px 0;font-size:14px;max-width:88ch}
.demo-say-k{display:block;font:700 10px/1 var(--mono,ui-monospace,Menlo,monospace);letter-spacing:.2em;color:var(--ink-faint,#6d7686);margin-bottom:7px}
.demo-note{margin:8px 0 0;font-size:13px;color:var(--ink-dim,#97a0b0);max-width:88ch}
.demo-controls{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 8px}
.demo-btn{appearance:none;padding:10px 16px;border-radius:7px;border:1px solid var(--line,#232a36);background:var(--panel-2,#1b202b);color:var(--ink,#eef1f6);font:700 12px/1 var(--mono,ui-monospace,Menlo,monospace);letter-spacing:.16em;cursor:pointer}
.demo-btn[data-primary="1"]{border-color:var(--amber,#e0a33c);color:var(--amber,#e0a33c)}
.demo-btn:disabled{opacity:.35;cursor:not-allowed}
.demo-ctrlstate{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;margin:0 0 16px;max-width:104ch}
.demo-ctrlk{font:700 10px/1.6 var(--mono,ui-monospace,Menlo,monospace);letter-spacing:.16em;color:var(--ink-faint,#6d7686);white-space:nowrap}
.demo-ctrlv{margin:0;font:400 12px/1.6 var(--mono,ui-monospace,Menlo,monospace);color:var(--ink-dim,#97a0b0)}
.demo-ctrlstate dd[data-row="beat_machine"]{color:var(--ink,#eef1f6)}
.demo-h3{margin:18px 0 8px;font:700 11px/1 var(--mono,ui-monospace,Menlo,monospace);letter-spacing:.22em;color:var(--ink-faint,#6d7686)}
.demo-beats{list-style:none;margin:0;padding:0}
.demo-beat{padding:11px 13px;border:1px solid var(--line,#232a36);border-radius:8px;margin-bottom:7px;background:var(--panel-2,#1b202b)}
.demo-beat[data-current="1"]{border-color:var(--amber,#e0a33c);border-left-width:4px;background:color-mix(in srgb,var(--amber,#e0a33c) 9%,var(--panel-2,#1b202b))}
.demo-beat[data-past="1"]{opacity:.55}
.demo-beat-head{display:flex;flex-wrap:wrap;gap:10px;align-items:baseline}
.demo-beat-n{font:700 11px/1 var(--mono,ui-monospace,Menlo,monospace);letter-spacing:.16em;color:var(--ink-faint,#6d7686);font-variant-numeric:tabular-nums}
.demo-beat-title{font-weight:700;font-size:14px}
.demo-beat-range{margin-left:auto;font:400 11px/1 var(--mono,ui-monospace,Menlo,monospace);color:var(--ink-faint,#6d7686);font-variant-numeric:tabular-nums}
.demo-beat p{margin:7px 0 0;font-size:13px;color:var(--ink-dim,#97a0b0);max-width:92ch}
.demo-beat-why{color:var(--ink,#eef1f6)}
.demo-marks{list-style:none;margin:8px 0 0;padding:0;border-top:1px dashed var(--line,#232a36)}
.demo-mark{margin-top:7px;font:400 12px/1.5 var(--mono,ui-monospace,Menlo,monospace);color:var(--ink-dim,#97a0b0)}
.demo-mark-f{color:var(--amber,#e0a33c);font-weight:700}
.demo-cols{display:flex;flex-wrap:wrap;gap:12px}
.demo-col{flex:1 1 300px;min-width:260px;padding:12px 14px;background:var(--panel-2,#1b202b);border:1px solid var(--line,#232a36);border-radius:8px}
.demo-col ul{margin:8px 0 0;padding-left:18px}
.demo-col li{margin:6px 0;font-size:13px;color:var(--ink-dim,#97a0b0)}
.demo-rule{margin:14px 0 0;padding:12px 14px;border:1px solid var(--amber,#e0a33c);border-radius:8px;background:color-mix(in srgb,var(--amber,#e0a33c) 7%,transparent);font-size:13px;color:var(--ink,#eef1f6);max-width:92ch}
.demo-abstain{margin:14px 0 0;padding:12px 14px;border:1px dashed var(--amber,#e0a33c);border-radius:8px}
.demo-abstain-tag{font:700 11px/1 var(--mono,ui-monospace,Menlo,monospace);letter-spacing:.2em;color:var(--amber,#e0a33c)}
.demo-abstain ul{margin:9px 0 0;padding-left:18px}
.demo-abstain li{margin:7px 0;font-size:13px;color:color-mix(in srgb,var(--amber,#e0a33c) 55%,var(--ink,#eef1f6))}
.demo-why{font:700 11px/1 var(--mono,ui-monospace,Menlo,monospace);letter-spacing:.1em;color:var(--amber,#e0a33c)}
.demo-foot{margin:14px 0 0;font:400 11px/1.6 var(--mono,ui-monospace,Menlo,monospace);color:var(--ink-faint,#6d7686);max-width:96ch}
`;

/**
 * Install CSS_TEXT and VERIFY it took. Returns a named verdict either way;
 * nothing here throws into the caller.
 */
export function installStyles(doc, g = (typeof globalThis !== 'undefined' ? globalThis : undefined)) {
  if (!doc || typeof doc.createElement !== 'function') {
    return { ok: false, how: 'no_document', verified: false };
  }
  let how = 'none';
  try {
    const Sheet = g && g.CSSStyleSheet;
    if (typeof Sheet === 'function' && 'adoptedStyleSheets' in doc) {
      const sheet = new Sheet();
      sheet.replaceSync(CSS_TEXT);
      doc.adoptedStyleSheets = [...doc.adoptedStyleSheets, sheet];
      how = 'adoptedStyleSheets';
    }
  } catch (e) {
    how = `adopted_failed:${(e && e.message) || e}`;
  }
  if (how !== 'adoptedStyleSheets') {
    try {
      const el = doc.createElement('style');
      el.textContent = CSS_TEXT;
      (doc.head || doc.body || doc.documentElement).appendChild(el);
      how = how === 'none' ? 'style_element' : `${how}+style_element`;
    } catch (e) {
      return { ok: false, how: `${how}+style_failed:${(e && e.message) || e}`, verified: false };
    }
  }
  // The probe: 3px letter-spacing on a class nothing else defines.
  let verified = false;
  try {
    const probe = doc.createElement('span');
    probe.className = STYLE_PROBE_CLASS;
    probe.textContent = '.';
    (doc.body || doc.documentElement).appendChild(probe);
    const cs = g && typeof g.getComputedStyle === 'function' ? g.getComputedStyle(probe) : null;
    verified = !!cs && String(cs.letterSpacing || '').startsWith('3');
    probe.remove ? probe.remove() : null;
  } catch { verified = false; }
  return { ok: true, how, verified };
}

// ===========================================================================
// 9. RENDER. Pure over a document-like object: demo.test.mjs runs every branch
//    below against a DOM shim with no browser anywhere.
// ===========================================================================

function mk(doc, tag, opts = {}) {
  const el = doc.createElement(tag);
  if (opts.class) el.className = opts.class;
  if (opts.text !== undefined && opts.text !== null) el.textContent = String(opts.text);
  if (opts.data) {
    for (const [k, v] of Object.entries(opts.data)) {
      if (v === null || v === undefined) continue;
      el.dataset[k] = String(v);
    }
  }
  if (opts.attrs) {
    for (const [k, v] of Object.entries(opts.attrs)) {
      if (v === null || v === undefined) continue;
      el.setAttribute(k, String(v));
    }
  }
  if (opts.on && typeof el.addEventListener === 'function') {
    for (const [t, fn] of Object.entries(opts.on)) el.addEventListener(t, fn);
  }
  for (const kid of opts.kids || []) if (kid) el.appendChild(kid);
  return el;
}

export const BADGE_HEADLINE = Object.freeze({
  [PROV_SIMULATED]: 'SIMULATED  ·  SIMULATED  ·  SIMULATED',
  [PROV_LIVE]: 'NOT SIMULATED — REAL CAMERA FRAMES',
  [PROV_UNKNOWN]: 'PROVENANCE UNKNOWN — I CANNOT TELL YOU',
});

export function renderBadge(model, doc) {
  const p = provenanceOf(model);
  return mk(doc, 'section', {
    class: 'demo-badge',
    data: { provenance: p.code },
    attrs: { role: 'note', 'aria-label': `frame provenance: ${p.code}` },
    kids: [
      mk(doc, 'span', { text: BADGE_HEADLINE[p.code] }),
      mk(doc, 'span', { class: 'demo-badge-detail', text: p.detail }),
      mk(doc, 'span', { class: 'demo-badge-evidence', text: `evidence: ${p.evidence}` }),
    ],
  });
}

export function renderHeadline(model, doc) {
  const m = asModel(model);
  const simulated = provenanceOf(m).code === PROV_SIMULATED;
  const simTag = () => (simulated
    ? mk(doc, 'span', { class: 'demo-head-sim', text: 'SIM' })
    : null);

  const tile = (key, value, sub, amber) => mk(doc, 'section', {
    class: 'demo-head',
    data: { head: key, amber: amber ? '1' : '0', simulated: simulated ? '1' : '0' },
    kids: [
      mk(doc, 'span', { class: 'demo-head-k', text: key }),
      mk(doc, 'span', {
        class: 'demo-head-v num',
        kids: [mk(doc, 'span', { text: value }), simTag()],
      }),
      mk(doc, 'span', { class: 'demo-head-sub', text: sub }),
    ],
  });

  return mk(doc, 'div', {
    class: 'demo-heads',
    kids: [
      tile(
        'TOTAL',
        paiseLabel(m.totalPaise),
        m.totalPaise === null
          ? 'no state message has carried a total yet'
          : `${m.totalPaise} paise — an integer, all the way down. No float `
            + 'ever touches this number.',
        false,
      ),
      tile(
        'AMBER',
        countLabel(m.amberCount),
        m.amberCount === null
          ? 'not known yet'
          : (m.amberCount === 0
            ? 'no bill line is flagged amber'
            : `bill lines flagged amber and EXCLUDED from the total above: `
              + `${m.amberNames.join(', ') || 'unnamed'}`
              + (m.amberReasons.length ? ` (${[...new Set(m.amberReasons)].join(', ')})` : '')
              + (Number.isInteger(m.brainAmberCount) && m.brainAmberCount !== m.amberCount
                ? `. The brain's own amber_count field says ${m.brainAmberCount} — see I DO NOT KNOW.`
                : '')),
        Number.isInteger(m.amberCount) && m.amberCount > 0,
      ),
      tile(
        'LEDGER',
        countLabel(m.ledgerLines),
        m.ledgerLines === null
          ? 'no audit head has arrived'
          : `hash-chained lines · head ${headLabel(m.ledgerHead)}`,
        false,
      ),
    ],
  });
}

export function renderCommentary(model, doc) {
  const c = commentaryFor(model);
  const notes = notesFor(model);
  return mk(doc, 'section', {
    class: 'demo-say',
    data: { commentary: c.code },
    attrs: { 'aria-live': 'polite' },
    kids: [
      mk(doc, 'span', { class: 'demo-say-k', text: 'WHAT THE COUNTER IS DOING, AND WHY' }),
      mk(doc, 'p', { class: 'demo-say-line', text: c.text }),
      ...notes.map((n) => mk(doc, 'p', {
        class: 'demo-note', data: { note: n.code }, text: n.text,
      })),
    ],
  });
}

export const CONTROL_STATE_TEXT = Object.freeze({
  [CTRL_IDLE]: 'no control has been pressed yet.',
  [CTRL_SENT]: 'sent; waiting for the brain to answer.',
  [CTRL_ACCEPTED]: 'the brain acknowledged it.',
  [CTRL_REFUSED]: 'the brain REFUSED it.',
  [CTRL_NO_TRANSPORT]: 'it could not be sent at all.',
  [CTRL_UNANSWERED]: 'sent, and never answered.',
});

export function renderControls(model, doc, handlers = {}) {
  const m = asModel(model);
  const live = livenessOf(m);
  const onAction = typeof handlers.onAction === 'function' ? handlers.onAction : null;
  // The brain publishes the verbs it accepts. A button for a verb it has not
  // claimed is disabled rather than removed: the reviewer should see the whole
  // transport and which parts of it this brain implements.
  const accepted = m.simStatus && Array.isArray(m.simStatus.actions)
    ? m.simStatus.actions : null;
  const buttons = CONTROLS.map((c) => {
    const unsupported = accepted !== null && !accepted.includes(c.action);
    return mk(doc, 'button', {
      class: 'demo-btn',
      text: c.label,
      data: {
        action: c.action,
        primary: c.action === 'start' ? '1' : '0',
        unsupported: unsupported ? '1' : '0',
      },
      attrs: {
        type: 'button',
        title: unsupported ? `this brain does not accept the '${c.action}' verb` : c.hint,
        ...(unsupported ? { disabled: 'disabled' } : {}),
      },
      on: onAction && !unsupported ? { click: () => onAction(c.action) } : null,
    });
  });

  const fps = live.fps === null ? null : live.fps.toFixed(1);
  const runLine = live.running === null
    ? `SCRIPT ADVANCING: not known — ${live.reason}.`
    : `SCRIPT ADVANCING: yes, ${fps === null ? 'rate not yet measurable' : `${fps} state messages/s`} `
      + `(${live.reason}). This is MEASURED from arrivals, not from which button you pressed.`;

  const ctrl = m.control;
  const ctrlLine = `LAST CONTROL: ${ctrl.action ? ctrl.action.toUpperCase() : '—'} `
    + `— ${ctrl.code}. ${CONTROL_STATE_TEXT[ctrl.code] || ''}`
    + (ctrl.detail ? ` ${ctrl.detail}` : '')
    + (ctrl.known && ctrl.known.length
      ? ` The brain says it would accept: ${ctrl.known.join(', ')}.`
      : '');

  const mode = modeOf(m);
  const s = m.simStatus;
  const modeLine = mode.mode === null
    ? 'BEAT MACHINE: no mode published — this brain may not have one.'
    : `BEAT MACHINE: ${mode.mode}. ${mode.note}`
      + (s && Number.isInteger(s.pending_steps) && s.pending_steps > 0
        ? ` ${s.pending_steps} step${s.pending_steps === 1 ? '' : 's'} queued.` : '')
      + (s && Number.isInteger(s.run) ? ` Run ${s.run}.` : '')
      + (s && Number.isInteger(s.frames_emitted) ? ` ${s.frames_emitted} frames pushed.` : '');

  return mk(doc, 'section', {
    class: 'demo-controls-wrap',
    data: {
      control: ctrl.code,
      running: live.running === null ? 'unknown' : String(live.running),
      mode: mode.mode || 'unpublished',
    },
    kids: [
      mk(doc, 'div', { class: 'demo-controls', kids: buttons }),
      // Four separate rows rather than one <br>-joined paragraph: read as a
      // block of monospace prose these four facts were indistinguishable, which
      // is the opposite of what a status line is for.
      mk(doc, 'dl', {
        class: 'demo-ctrlstate',
        kids: [
          ['BEAT MACHINE', modeLine.replace(/^BEAT MACHINE: /, '')],
          ['ADVANCING', runLine.replace(/^SCRIPT ADVANCING: /, '')],
          ['LAST CONTROL', ctrlLine.replace(/^LAST CONTROL: /, '')],
          ['TRANSPORT', `${m.transport.code} — ${m.transport.detail}`],
        ].flatMap(([k, v]) => [
          mk(doc, 'dt', { class: 'demo-ctrlk', text: k }),
          mk(doc, 'dd', { class: 'demo-ctrlv', data: { row: k.toLowerCase().replace(' ', '_') }, text: v }),
        ]),
      }),
    ],
  });
}

export function renderScript(model, doc) {
  const beat = beatOf(model);
  const marksFor = (b) => MARKS.filter((k) => k.frame >= b.from && k.frame <= b.to);

  const rows = BEATS.map((b, i) => {
    const current = beat.index === i;
    const past = beat.index !== null && i < beat.index;
    const marks = marksFor(b);
    return mk(doc, 'li', {
      class: 'demo-beat',
      data: { beat: b.name, current: current ? '1' : '0', past: past ? '1' : '0' },
      attrs: current ? { 'aria-current': 'step' } : null,
      kids: [
        mk(doc, 'div', {
          class: 'demo-beat-head',
          kids: [
            mk(doc, 'span', { class: 'demo-beat-n', text: `BEAT ${i + 1}/${BEATS.length}` }),
            mk(doc, 'span', { class: 'demo-beat-title', text: `${b.name} — ${b.title}` }),
            mk(doc, 'span', {
              class: 'demo-beat-range num',
              text: current && Number.isInteger(beat.phaseIndex)
                ? `frames ${b.from}–${b.to} · now at ${beat.phaseIndex + 1}/${b.count}`
                : `frames ${b.from}–${b.to}`,
            }),
          ],
        }),
        mk(doc, 'p', { class: 'demo-beat-what', text: b.what }),
        mk(doc, 'p', { class: 'demo-beat-why', text: b.why }),
        mk(doc, 'p', { class: 'demo-beat-watch', text: `WATCH: ${b.watch}` }),
        marks.length
          ? mk(doc, 'ul', {
            class: 'demo-marks',
            kids: marks.map((k) => mk(doc, 'li', {
              class: 'demo-mark',
              data: { mark: String(k.frame) },
              kids: [
                mk(doc, 'span', { class: 'demo-mark-f', text: `frame ${k.frame} · ${k.verb} — ` }),
                mk(doc, 'span', { text: `${k.label}. ${k.detail}` }),
              ],
            })),
          })
          : null,
      ],
    });
  });

  const pos = beat.frame === null
    ? 'script position not known'
    : (beat.complete
      ? `frame ${beat.frame}; the ${TOTAL_FRAMES}-frame script finished and the sim `
        + 'is REPEATING ITS FINAL FRAME. It does not loop: looping would replay a '
        + 'sale that already settled.'
      : `frame ${beat.frame} of ${TOTAL_FRAMES}`);

  const dis = disagreementOf(model);
  return mk(doc, 'section', {
    class: 'demo-script',
    data: {
      beatSource: beat.source,
      beatIndex: beat.index === null ? 'none' : String(beat.index),
      disagreement: dis ? '1' : '0',
    },
    kids: [
      mk(doc, 'h3', { class: 'demo-h3', text: `THE SCRIPT — ${BEATS.length} BEATS, ${TOTAL_FRAMES} FRAMES` }),
      mk(doc, 'p', {
        class: 'demo-note',
        text: `${pos}. Source of this position: ${BEAT_SOURCE_NOTE[beat.source]}`,
      }),
      // The brain's OWN caption for the beat it says it is on, printed beside
      // this panel's, so the two can be compared rather than merged.
      beat.label
        ? mk(doc, 'p', {
          class: 'demo-note',
          data: { note: 'brain_beat_caption' },
          text: `The brain calls this beat "${beat.brainName}": ${beat.label}`
            + (beat.detail ? ` — ${beat.detail}` : ''),
        })
        : null,
      dis
        ? mk(doc, 'p', {
          class: 'demo-rule',
          data: { rule: 'disagreement' },
          text: `DISAGREEMENT: the brain says frame ${dis.frame} is beat "${dis.brain}"; `
            + `this beat list says "${dis.panel}". The captions below do not describe `
            + 'the script this brain is running.',
        })
        : null,
      mk(doc, 'ol', { class: 'demo-beats', kids: rows }),
    ],
  });
}

export function renderTruth(model, doc) {
  return mk(doc, 'section', {
    class: 'demo-truth',
    kids: [
      mk(doc, 'h3', { class: 'demo-h3', text: 'WHAT IS SIMULATED, AND WHAT IS THE REAL MODULE' }),
      mk(doc, 'div', {
        class: 'demo-cols',
        kids: [
          mk(doc, 'div', {
            class: 'demo-col',
            data: { col: 'simulated' },
            kids: [
              mk(doc, 'span', { class: 'demo-head-k', text: 'SIMULATED' }),
              mk(doc, 'ul', { kids: SIMULATED_PARTS.map((t) => mk(doc, 'li', { text: t })) }),
            ],
          }),
          mk(doc, 'div', {
            class: 'demo-col',
            data: { col: 'real' },
            kids: [
              mk(doc, 'span', { class: 'demo-head-k', text: 'REAL, RUNNING ON THESE PIXELS' }),
              mk(doc, 'ul', { kids: REAL_PARTS.map((t) => mk(doc, 'li', { text: t })) }),
            ],
          }),
        ],
      }),
      // data-rule carries a NAME, not a colour word: demo.test.mjs walks every
      // class, data attribute and style value and refuses anything matching
      // /green/i on the pixels. The prose is exempt and must stay — this panel
      // is required to say the word — but the attribute has no business
      // carrying it.
      mk(doc, 'p', { class: 'demo-rule', data: { rule: 'settlement' }, text: GREEN_RULE }),
      mk(doc, 'p', { class: 'demo-rule', data: { rule: 'sim_gateway' }, text: SIM_GATEWAY_RULE }),
      mk(doc, 'p', { class: 'demo-note', data: { note: 'real_money' }, text: REAL_MONEY_NOTE }),
    ],
  });
}

export function renderAbstentions(model, doc) {
  const list = abstentionsFor(model);
  return mk(doc, 'section', {
    class: 'demo-abstain',
    data: { abstentions: String(list.length) },
    kids: [
      mk(doc, 'span', { class: 'demo-abstain-tag', text: 'I DO NOT KNOW' }),
      list.length === 0
        ? mk(doc, 'p', {
          class: 'demo-note',
          text: 'Nothing on this panel is currently unknown to it. That is a '
            + 'statement about this panel only: every capability panel keeps its '
            + 'own abstentions and they are not repeated here.',
        })
        : mk(doc, 'ul', {
          kids: list.map((a) => mk(doc, 'li', {
            data: { why: a.code },
            kids: [
              mk(doc, 'span', { class: 'demo-why', text: `${a.code} — ` }),
              mk(doc, 'span', { text: a.text }),
            ],
          })),
        }),
    ],
  });
}

export function renderDemo(model, doc, handlers = {}) {
  const m = asModel(model);
  const prov = provenanceOf(m);
  const beat = beatOf(m);
  const live = livenessOf(m);
  return mk(doc, 'div', {
    class: 'gawaah-demo',
    data: {
      provenance: prov.code,
      beatSource: beat.source,
      running: live.running === null ? 'unknown' : String(live.running),
      messages: String(m.messages),
      seam: m.seam.registered ? 'registered' : 'unregistered',
    },
    kids: [
      renderBadge(m, doc),
      renderHeadline(m, doc),
      renderCommentary(m, doc),
      renderControls(m, doc, handlers),
      renderScript(m, doc),
      renderTruth(m, doc),
      renderAbstentions(m, doc),
      mk(doc, 'p', {
        class: 'demo-foot',
        text: `${m.messages} brain messages seen`
          + (Object.keys(m.byType).length
            ? ` (${Object.entries(m.byType).sort().map(([k, v]) => `${k}:${v}`).join(' ')})`
            : '')
          + `. Panel seam: ${m.seam.registered ? 'registered with the shell' : `not registered${m.seam.reason ? ` (${m.seam.reason})` : ''}`}.`,
      }),
    ],
  });
}

/** A one-line summary. Used by the browser check and by anything scripting this. */
export function demoSummary(model) {
  const m = asModel(model);
  const b = beatOf(m);
  const l = livenessOf(m);
  return [
    `provenance=${provenanceOf(m).code}`,
    `beat=${b.name || 'none'}(${b.index === null ? '-' : b.index})`,
    `frame=${b.frame === null ? '-' : b.frame}/${TOTAL_FRAMES}`,
    `source=${b.source}`,
    `total_paise=${m.totalPaise === null ? '-' : m.totalPaise}`,
    `amber=${m.amberCount === null ? '-' : m.amberCount}`,
    `ledger=${m.ledgerLines === null ? '-' : m.ledgerLines}`,
    `running=${l.running === null ? 'unknown' : l.running}`,
    `control=${m.control.code}`,
    `messages=${m.messages}`,
  ].join(' ');
}

// ===========================================================================
// 10. TRANSPORT. The socket tap. See the header comment for why this taps the
//     counter's existing socket instead of opening a second one.
// ===========================================================================

export const TAP_FLAG = '__GAWAAH_DEMO_SOCKET_TAP__';

/** WebSocket.readyState -> the transport code this panel reports. */
export const SOCKET_STATE = Object.freeze({
  0: 'SOCKET_CONNECTING', 1: 'TAPPED', 2: 'SOCKET_CLOSING', 3: 'SOCKET_CLOSED',
});

export const SOCKET_DETAIL = Object.freeze({
  SOCKET_CONNECTING: 'the counter\'s brain socket is still opening. Controls cannot '
    + 'be sent until it is up.',
  TAPPED: 'reading and sending on the counter\'s own brain socket. No second '
    + 'connection was opened: brain_server runs one frame pump per socket, so a '
    + 'second one would double the frame rate and misreport the pacing.',
  SOCKET_CLOSING: 'the counter\'s brain socket is closing.',
  SOCKET_CLOSED: 'the counter\'s brain socket is CLOSED. Controls cannot be sent, '
    + 'and the numbers above are the last ones that arrived — they are not live.',
  SOCKET_UNKNOWN: 'the socket reported a readyState this panel does not know.',
});

/**
 * Wrap `g.WebSocket` so every socket the page constructs is handed to
 * `onSocket`. A Proxy construct trap is used rather than a subclass because it
 * leaves the constructor's identity, prototype chain and statics untouched:
 * `sock instanceof WebSocket`, `WebSocket.OPEN` and `readyState` all behave
 * exactly as they did.
 *
 * Never throws. A failure returns a named reason and the panel goes read-only.
 */
export function installSocketTap(g, onSocket) {
  if (!g || typeof g.WebSocket !== 'function') {
    return { ok: false, reason: 'no_websocket_constructor' };
  }
  if (g[TAP_FLAG]) return { ok: false, reason: 'already_tapped' };
  if (typeof Proxy !== 'function' || typeof Reflect !== 'object') {
    return { ok: false, reason: 'no_proxy_support' };
  }
  const Native = g.WebSocket;
  try {
    g.WebSocket = new Proxy(Native, {
      construct(target, args, nt) {
        const sock = Reflect.construct(target, args, nt);
        try { onSocket(sock, String(args && args[0] !== undefined ? args[0] : '')); }
        catch { /* a tap that throws must never break the counter's socket */ }
        return sock;
      },
    });
    g[TAP_FLAG] = true;
  } catch (e) {
    return { ok: false, reason: `tap_install_failed:${(e && e.message) || e}` };
  }
  return { ok: true, reason: 'tapped', native: Native };
}

// ===========================================================================
// 11. THE PANEL OBJECT.
// ===========================================================================

/** The DOM ids this panel will mount into, in order of preference. */
export const MOUNT_IDS = Object.freeze(['body-demo', 'panel-demo']);

/**
 * Repaint coalescing window. `state` arrives ten times a second on the sim; a
 * panel that reflows on every one of them is a strobe, not an instrument. The
 * MODEL is always the latest message — only the painting is rate-limited.
 */
export const REPAINT_MS = 250;

export function createPanel(opts = {}) {
  const doc = opts.doc ?? opts.document ?? globalThis.document;
  const g = opts.global ?? globalThis;
  const now = typeof opts.now === 'function' ? opts.now : () => Date.now();
  let root = opts.root ?? opts.host ?? null;
  let model = initialModel();
  let dirty = true;
  let flushTimer = null;
  let sock = opts.socket ?? null;

  const resolveRoot = () => {
    if (root) return root;
    if (!doc || typeof doc.getElementById !== 'function') return null;
    for (const id of MOUNT_IDS) {
      const el = doc.getElementById(id);
      if (el) { root = el; return root; }
    }
    return null;
  };

  /**
   * The transport, READ OFF THE SOCKET rather than remembered from events.
   *
   * Found in a browser, not in a test: the panel sat on transport TAPPED while
   * the socket it had tapped was readyState 3 (CLOSED), so RUN DEMO answered
   * NO_TRANSPORT with a line that contradicted the transport line above it. A
   * `close` event that never fired, or fired on a socket that was replaced, is
   * exactly the kind of absence an event-only view cannot see. readyState is
   * the truth and it is free to read.
   */
  const refreshTransport = () => {
    const s = sock;
    if (!s) return;
    const code = SOCKET_STATE[s.readyState] || 'SOCKET_UNKNOWN';
    if (model.transport.code === code) return;
    model = noteTransport(model, code, SOCKET_DETAIL[code] || `socket readyState ${s.readyState}`);
  };

  const paint = () => {
    const host = resolveRoot();
    if (!host || typeof host.replaceChildren !== 'function') return false;
    host.replaceChildren(renderDemo(model, doc, { onAction: api.press }));
    // The shell contract's one attribute, set on the panel section if we can
    // find it. A panel that is showing live numbers is OK; otherwise ABSTAIN.
    const section = (doc.getElementById && doc.getElementById('panel-demo')) || host;
    if (section && section.dataset) {
      const known = model.totalPaise !== null && model.ledgerLines !== null;
      section.dataset.status = known ? 'OK' : 'ABSTAIN';
      section.dataset.provenance = provenanceOf(model).code;
    }
    const ab = doc.getElementById && doc.getElementById('abstain-demo');
    if (ab) {
      const list = abstentionsFor(model);
      if (list.length === 0) ab.setAttribute('hidden', '');
      else {
        ab.removeAttribute && ab.removeAttribute('hidden');
        const why = doc.getElementById('why-demo');
        if (why) why.textContent = list[0].code;
      }
    }
    return true;
  };

  /**
   * Repaints are coalesced at ~4 Hz. `state` arrives ten times a second and a
   * panel that reflows on every one of them is a strobe, not an instrument.
   * The model is always the latest; only the painting is rate-limited.
   */
  const schedule = () => {
    dirty = true;
    if (flushTimer !== null) return;
    // Called as g.setTimeout(...) and never as a detached reference: a bare
    // `const t = g.setTimeout; t(fn)` throws "Illegal invocation" in a browser
    // because the timer needs its global as the receiver.
    if (typeof g.setTimeout !== 'function') { dirty = false; paint(); return; }
    flushTimer = g.setTimeout(() => {
      flushTimer = null;
      dirty = false;
      model = noteTick(model, now());
      refreshTransport();
      paint();
    }, REPAINT_MS);
  };

  const api = {
    id: PANEL_ID,
    title: PANEL_TITLE,
    get model() { return model; },
    set model(next) { model = next; schedule(); },

    /** Every brain message, whatever its type. Never throws. */
    onState(msg) {
      model = reduceDemo(model, msg, now());
      schedule();
      return true;
    },

    /** DEMO overlays nothing on the live view. Saying false is the true answer. */
    onFrame() { return false; },

    /** A control press. Sends if it can; records the refusal if it cannot. */
    press(action) {
      let payload;
      try { payload = simMessage(action); }
      catch (e) {
        model = noteControlUnsendable(model, String(action), (e && e.message) || String(e), now());
        schedule();
        return false;
      }
      const s = sock;
      const open = s && s.readyState === 1;
      if (!s || !open) {
        refreshTransport();
        model = noteControlUnsendable(
          model, action,
          s ? `the counter's socket is not open (readyState ${s.readyState}, `
              + `${SOCKET_STATE[s.readyState] || 'unknown'})`
            : 'no brain socket has been observed by this panel yet',
          now(),
        );
        schedule();
        return false;
      }
      try { s.send(JSON.stringify(payload)); }
      catch (e) {
        model = noteControlUnsendable(model, action, `send failed: ${(e && e.message) || e}`, now());
        schedule();
        return false;
      }
      model = noteControlSent(model, action, now());
      schedule();
      return true;
    },

    /**
     * Adopt a socket to read from and send on.
     *
     * A socket that is ALREADY dead never displaces a live one. app.js retries
     * a failed connection, so the tap legitimately sees several sockets; taking
     * the newest unconditionally would hand the panel a corpse if a retry
     * failed after a good connection was up.
     */
    useSocket(s) {
      const dead = s && (s.readyState === 2 || s.readyState === 3);
      const haveLive = sock && sock.readyState === 1;
      if (!(dead && haveLive)) sock = s;
      if (s && typeof s.addEventListener === 'function') {
        s.addEventListener('message', (ev) => {
          if (s !== sock) return;   // messages from a socket we are not on
          let m;
          try { m = JSON.parse(ev.data); } catch { return; }
          api.onState(m);
        });
        s.addEventListener('close', () => { refreshTransport(); schedule(); });
        s.addEventListener('open', () => { sock = s; refreshTransport(); schedule(); });
      }
      refreshTransport();
      schedule();
      return true;
    },

    /** Which socket this panel is on, for a diagnostic. Never sends on it. */
    socketState() { return sock ? sock.readyState : null; },

    /** boot() calls this when it had to create its own host element. */
    setRoot(el) { root = el; schedule(); return el; },

    noteTransport(code, detail) { model = noteTransport(model, code, detail); schedule(); },
    noteSeam(reg, reason) { model = noteSeam(model, reg, reason); schedule(); },
    noteHealth(h) { model = noteHealth(model, h, now()); schedule(); },
    noteHealthError(r) { model = noteHealthError(model, r, now()); schedule(); },
    tick() { model = noteTick(model, now()); refreshTransport(); schedule(); },
    render() { refreshTransport(); return paint(); },
  };
  return api;
}

/**
 * THE ONE PANEL IN THIS DOCUMENT.
 *
 * Found in a browser: app.js drains GAWAAH_PANELS and calls DESCRIPTOR.attach(),
 * and this module's own boot() also stands a panel up. Two instances then
 * rendered into the SAME #body-demo, and whichever painted last won — so a
 * cold, unregistered panel could wipe out the live one mid-run. There must be
 * exactly one panel per document, whichever door it is entered by.
 *
 * A caller that passes its own `doc` or `root` (every test, and any shell that
 * wants a second instance on purpose) gets a fresh panel and never touches this.
 */
let SINGLETON = null;

export function panelSingleton(opts = {}) {
  if (opts.doc || opts.document || opts.root || opts.host) return createPanel(opts);
  if (SINGLETON) return SINGLETON;
  SINGLETON = createPanel(opts);
  return SINGLETON;
}

/** Test seam: forget the singleton so a fresh document gets a fresh panel. */
export function resetSingleton() { SINGLETON = null; }

export function attach(register, opts = {}) {
  if (typeof register !== 'function') {
    throw new TypeError('attach(register): registerPanel must be a function');
  }
  const panel = panelSingleton(opts);
  const r = register(PANEL_ID, { onState: panel.onState, onFrame: panel.onFrame });
  panel.noteSeam(!!(r && r.ok !== false), r && r.ok === false ? r.reason : null);
  return panel;
}

/** See the other panels: the attachXPanel(opts) convention, supported alongside. */
export function attachDemoPanel(opts = {}) {
  const panel = panelSingleton(opts);
  const register = typeof opts.register === 'function'
    ? opts.register
    : (typeof globalThis.registerPanel === 'function' ? globalThis.registerPanel : null);
  const registration = register
    ? register(PANEL_ID, { onState: panel.onState, onFrame: panel.onFrame })
    : null;
  panel.noteSeam(
    !!(registration && registration.ok !== false),
    registration && registration.ok === false
      ? registration.reason
      : (register ? null : 'no registerPanel on the page'),
  );
  return { panel, registered: register !== null, registration };
}

// ===========================================================================
// 12. BOOT. Everything below is browser-only and is skipped entirely in node,
//     which is why the test file can import this module and get pure functions.
// ===========================================================================

export const HEALTH_POLL_MS = 5000;

/**
 * Stand the panel up in a page. Mounts, installs styles, taps the socket, polls
 * /health for provenance evidence, and ticks so absences get noticed.
 */
export function boot(g = globalThis) {
  const doc = g.document;
  if (!doc) return null;

  // Mount. If the shell has not shipped the markup yet, make our own host so a
  // visitor still gets the panel rather than nothing. Which of the two happened
  // is recorded, not hidden.
  let host = null;
  let mount = 'none';
  for (const id of MOUNT_IDS) {
    const el = doc.getElementById(id);
    if (el) { host = el; mount = `#${id}`; break; }
  }
  if (!host) {
    host = doc.createElement('section');
    host.id = 'panel-demo';
    host.className = 'gawaah-demo-standalone';
    host.setAttribute('aria-label', PANEL_TITLE);
    const target = (doc.querySelector && doc.querySelector('.panels')) || doc.body;
    if (!target) return null;
    target.appendChild(host);
    mount = 'self-created #panel-demo';
  }

  const styles = installStyles(doc, g);
  host.dataset.styles = `${styles.how}${styles.verified ? '+verified' : '+unverified'}`;

  // The SAME instance app.js's drainPanelQueue() may already have attached —
  // see panelSingleton. `global` is passed only on first construction; a panel
  // attached before boot ran already resolved the right document.
  const panel = panelSingleton({ global: g });
  panel.setRoot(host);
  if (panel.socketState() === null) {
    panel.noteTransport('NO_SOCKET',
      'no brain socket has been observed yet. The tap is installed; it will pick up '
      + 'the counter\'s socket the moment app.js opens one.');
  }

  // The seam, if the shell has one. Refusal is expected today — app.js's
  // PANEL_IDS does not contain 'demo' — and is reported, not swallowed.
  const reg = typeof g.registerPanel === 'function' ? g.registerPanel : null;
  if (reg) {
    let r = null;
    try { r = reg(PANEL_ID, { onState: panel.onState, onFrame: panel.onFrame }); }
    catch (e) { r = { ok: false, reason: (e && e.message) || String(e) }; }
    panel.noteSeam(!!(r && r.ok !== false), r && r.ok === false ? r.reason : null);
  } else {
    panel.noteSeam(false, 'the shell exposed no registerPanel; this panel reads its own socket tap');
  }

  const tap = installSocketTap(g, (sock) => panel.useSocket(sock));
  if (!tap.ok) {
    panel.noteTransport('TAP_FAILED',
      `could not tap the counter's socket (${tap.reason}); this panel is read-only `
      + 'and its controls cannot be sent.');
  }

  const readHealth = () => {
    if (typeof g.fetch !== 'function') {
      panel.noteHealthError('this page has no fetch(), so /health cannot be read');
      return;
    }
    // Absolute, because brain_server mounts /health at the origin root and this
    // page may be served from any path under it.
    g.fetch('/health', { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((h) => panel.noteHealth(h))
      .catch((e) => panel.noteHealthError((e && e.message) || String(e)));
  };
  readHealth();
  if (g.setInterval) {
    g.setInterval(readHealth, HEALTH_POLL_MS);
    g.setInterval(() => panel.tick(), 1000);
  }
  panel.render();
  g.GAWAAH_DEMO = { panel, summary: () => demoSummary(panel.model), styles, tap: tap.ok, mount };
  return panel;
}

export const DESCRIPTOR = { id: PANEL_ID, title: PANEL_TITLE, createPanel, attach, attached: false };
if (typeof globalThis !== 'undefined') {
  (globalThis.GAWAAH_PANELS ||= []).push(DESCRIPTOR);
  if (typeof globalThis.document !== 'undefined' && typeof globalThis.window !== 'undefined') {
    const start = () => { try { boot(globalThis); } catch (e) { /* never break the page */ } };
    if (globalThis.document.readyState === 'loading') {
      globalThis.document.addEventListener('DOMContentLoaded', start);
    } else {
      start();
    }
  }
}

export default {
  PANEL_ID, PANEL_TITLE, BEATS, MARKS, TOTAL_FRAMES,
  initialModel, reduceDemo, deriveDemo, renderDemo, demoSummary,
  createPanel, attach, boot,
};
