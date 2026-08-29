/* ENROL panel — the surface where a human teaches the counter.
 * ===========================================================================
 *
 * WHY THIS FILE EXISTS
 * Every other panel in this rig is a READER. MUDRA reads a hand, PEEL reads a
 * sticker, CHILLA reads a screen, SAAF reads a burst, LEDGER reads the chain.
 * Not one of them has an input. So a shopkeeper who opens this page has no way
 * to tell the counter anything at all: no SKU, no price, no name to enrol a
 * sticker under. The panels sit on honest abstentions forever because nobody
 * can feed them. This is the missing half — the WRITE surface.
 *
 * It does four things, all with a mouse:
 *
 *   1. ADD AN SKU. Name plus price typed in RUPEES. The price crosses a
 *      boundary here — a human types decimal rupees, the machine holds integer
 *      paise — and that crossing is the single most dangerous line in this
 *      file. It is done in STRING SPACE with no arithmetic (see parsePaise),
 *      and anything that is not an exact whole number of paise is REFUSED by
 *      name. 214.507 is not 214.51 and it is not 21450 paise; it is a refusal.
 *
 *   2. SHOW THE CATALOGUE, with the paise printed next to the rupees, and
 *      remove a row. The paise are shown because the paise are what is true;
 *      the rupee string is a rendering of it.
 *
 *   3. ENROL A STICKER by name -> {"type":"enrol_sticker", name} to the brain.
 *      The brain answers with a `peel` message and a `saaf` message and this
 *      panel renders exactly what it is handed. It does not decide whether an
 *      enrolment succeeded; PEEL does, in Python, against a real registry.
 *
 *   4. ASK SAAF FOR THE BURST -> {"type":"select_panel","id":"saaf"}. The
 *      brain's burst is a ROLLING WINDOW of the last N rectified frames; there
 *      is no "start capturing" verb in the protocol and this panel does not
 *      invent one. What the button does is ask SAAF to publish the burst it
 *      currently holds, and the label says so.
 *
 * WHAT IT MAY NOT DO — INVARIANT 2
 * This panel never computes money. It converts one typed string into one
 * integer at the boundary, refuses everything inexact, and stops. It does not
 * total the catalogue (a catalogue is not a bill), does not price a line, does
 * not touch the reducer and cannot turn anything green. A price typed here is
 * a LOCAL CATALOGUE ENTRY and the surface says that on screen, in words,
 * permanently — because a number a shopkeeper typed and a number the brain has
 * accepted are different kinds of thing and must not look alike.
 *
 * WHAT IT MAY NOT DO — INVARIANT 7
 * Every abstention below stays reachable. `enrol_nothing_typed`,
 * `enrol_no_sticker_result`, `enrol_no_burst_reported` and
 * `enrol_no_brain_seam` are all real states this surface renders, and three of
 * them are what you see on a cold load. And when the brain is running under
 * --sim, this panel paints a SIMULATED banner over every reading it shows,
 * because a synthetic frame that looks like a measurement is the exact lie
 * this rig exists to not tell.
 *
 * Everything above `createPanel` is PURE: no document, no globals, no network.
 * That is what makes `node web/panels/enrol.test.mjs` possible with no browser.
 *
 * This file imports nothing — it is loaded through a data: URL by its own test,
 * and a data: URL cannot resolve a relative specifier. Same reason as
 * mudra.js / peel.js / saaf.js.
 */

export const PANEL_ID = 'enrol';
export const PANEL_TITLE = 'ENROL — teach the counter';

/** The shell's section and its fill point. web/index.html owns both. */
export const SHELL_PANEL_ID = 'panel-enrol';
export const SHELL_BODY_ID = 'body-enrol';

/**
 * OUR root, created inside the shell's fill point. It is a separate element on
 * purpose: `#body-enrol` also holds the shell's own `.orient` copy and its
 * `#abstain-enrol` block, and those belong to the shell agent. Owning a child
 * rather than the parent means a repaint here cannot delete their prose, and
 * app.js keeps its normal control over the abstention block.
 */
export const PANEL_ROOT_ID = 'enrol-render';
export const STYLE_ID = 'enrol-style';

// ===========================================================================
// 1. THE MONEY BOUNDARY.
// ===========================================================================

/** Rupees to paise. Named so the 100 is never a magic number in a expression. */
export const PAISE_PER_RUPEE = 100;

/**
 * The ceiling, in paise: 10,00,000.00 rupees. Not a currency limit — a TYPO
 * limit. A kirana SKU above ten lakh is a stuck key, and a counter that
 * cheerfully accepts it will be believed. Refusing is the cheaper failure.
 */
export const MAX_PAISE = 100000000;

/** Longest SKU / sticker name. The brain writes a file per sticker name. */
export const MAX_NAME_LEN = 48;

/**
 * Why a typed price was refused. Every one of these fires in enrol.test.mjs;
 * a reason code that cannot be reached is a lie in a docstring.
 */
export const PriceRefusal = Object.freeze({
  EMPTY: 'price_empty',
  NOT_A_NUMBER: 'price_not_a_number',
  SEPARATOR: 'price_has_separator',
  EXPONENT: 'price_exponent_notation',
  NEGATIVE: 'price_negative',
  SUB_PAISE: 'price_sub_paise',
  ZERO: 'price_zero',
  TOO_LARGE: 'price_too_large',
});

export const PRICE_REFUSAL_CODES = Object.freeze(Object.values(PriceRefusal));

/**
 * The sentence shown to the shopkeeper for each refusal. Written for the
 * person holding the mouse, not for the person holding the stack trace: it
 * says what was wrong AND what to type instead.
 */
export const PRICE_REFUSAL_HELP = Object.freeze({
  [PriceRefusal.EMPTY]:
    'no price was typed. An SKU with no price is not a priced SKU, so nothing '
    + 'was added. Type the rupee amount, e.g. 214.50',
  [PriceRefusal.NOT_A_NUMBER]:
    'that is not a decimal rupee amount. Digits and at most one dot, e.g. '
    + '214.50 — and write 0.50, not .50',
  [PriceRefusal.SEPARATOR]:
    'thousands separators are refused rather than guessed at: 1,23,456 and '
    + '1,234.56 group differently and this surface will not pick one for you. '
    + 'Type the digits with no commas.',
  [PriceRefusal.EXPONENT]:
    'exponent notation is not a rupee amount. 1e3 might be a price or it might '
    + 'be a slipped finger; type 1000 if you meant one thousand rupees.',
  [PriceRefusal.NEGATIVE]:
    'a negative price is refused. This counter bills; it does not refund from '
    + 'a catalogue entry.',
  [PriceRefusal.SUB_PAISE]:
    'that is finer than one paisa and it was NOT rounded. 214.507 is not '
    + '214.51 and it is not 214.50 — money here is an exact integer number of '
    + 'paise, so the third decimal makes it unrepresentable and it is refused.',
  [PriceRefusal.ZERO]:
    'zero was refused. A zero-price SKU is indistinguishable on the bill from '
    + 'an item the counter failed to price, and those two must never look alike.',
  [PriceRefusal.TOO_LARGE]:
    `above the typo ceiling of ${MAX_PAISE} paise (10,00,000.00 rupees). This `
    + 'is a stuck-key guard, not a currency limit.',
});

/** Why a typed name was refused. */
export const NameRefusal = Object.freeze({
  EMPTY: 'name_empty',
  TOO_LONG: 'name_too_long',
  BAD_CHARS: 'name_bad_chars',
  PATH_LIKE: 'name_path_like',
  DUPLICATE: 'name_duplicate',
});

export const NAME_REFUSAL_CODES = Object.freeze(Object.values(NameRefusal));

export const NAME_REFUSAL_HELP = Object.freeze({
  [NameRefusal.EMPTY]: 'no name was typed. Nothing can be enrolled unnamed.',
  [NameRefusal.TOO_LONG]:
    `longer than ${MAX_NAME_LEN} characters. The brain writes one file per `
    + 'sticker name; a name that long is a paragraph.',
  [NameRefusal.BAD_CHARS]:
    'letters, digits, space, dot, dash and underscore only. The name reaches a '
    + 'filesystem on the brain, so it is restricted here rather than sanitised '
    + 'quietly there.',
  [NameRefusal.PATH_LIKE]:
    'a name containing a slash or `..` looks like a path and is refused. This '
    + 'name is used to build a filename on the brain.',
  [NameRefusal.DUPLICATE]:
    'that name is already in this catalogue. Remove the existing row first, so '
    + 'there is never a moment where one name has two prices.',
});

/** Matches a plain unsigned decimal: digits, optionally a dot and more digits. */
const DECIMAL_RE = /^\d+(?:\.\d+)?$/;

/** Matches anything in exponent form, signed or not, so it can be named. */
const EXPONENT_RE = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)[eE][+-]?\d+$/;

/** A leading currency mark a shopkeeper may reasonably type. Stripped, not refused. */
const CURRENCY_RE = /^(?:₹|rs\.?|inr)\s*/i;

/**
 * Names the brain can safely turn into a filename.
 *
 * `\p{M}` is in the continuation class and its absence was a real bug caught by
 * enrol.test.mjs: "चाय" is च + ा, and that second character is a COMBINING MARK
 * (category Mn), not a letter. A class of `\p{L}\p{N}` alone therefore refuses
 * most Devanagari, Tamil and Bengali words — which is to say, it refuses the
 * names an Indian kirana would actually type. The first character still has to
 * be a letter or a digit, because a name that opens with a floating matra is a
 * paste accident.
 */
const NAME_RE = /^[\p{L}\p{N}][\p{L}\p{M}\p{N} ._-]*$/u;

/**
 * THE ONE FUNCTION THAT MATTERS. A typed rupee string -> integer paise, or a
 * named refusal. Never returns a rounded value; never returns a float.
 *
 * HOW IT AVOIDS FLOAT ENTIRELY
 * The obvious implementation is `Math.round(parseFloat(s) * 100)`. That is
 * wrong twice over. `parseFloat('214.507') * 100` is 21450.699999999997 — the
 * binary double cannot hold 214.507 — and the Math.round that papers over it
 * SILENTLY INVENTS 21451 paise the shopkeeper never typed. So this function
 * does no arithmetic at all. It splits the string on the dot, pads the
 * fractional part to exactly two digits, CONCATENATES the two digit strings,
 * and converts that integer literal once. '214' + '50' -> '21450'. Exact by
 * construction, because no value ever enters a floating-point register.
 *
 * A three-digit fraction is not padded, not truncated and not rounded: it is
 * refused, because there is no integer number of paise that it means.
 *
 * NUMBERS vs STRINGS. A DOM input always yields a string. A JS number is
 * accepted too, for programmatic callers, and is converted with String() —
 * which is honest about what already happened to it: `String(214.507)` is
 * '214.507' and refuses, while `String(1e3)` is '1000' because by the time JS
 * hands us the value the exponent notation is GONE and the value simply is the
 * integer one thousand. The STRING '1e3' is a different thing: that is a
 * character sequence a human typed into a text box, it is not decimal rupee
 * notation, and it is refused by name. Both cases are tested.
 *
 * @param {string|number} raw
 * @returns {{ok: boolean, paise: number|null, reason: string|null,
 *             detail: string, typed: string}}
 */
export function parsePaise(raw) {
  const typed = raw === null || raw === undefined ? '' : String(raw);
  const refuse = (reason) => ({
    ok: false,
    paise: null,
    reason,
    detail: PRICE_REFUSAL_HELP[reason] || reason,
    typed,
  });

  if (typeof raw === 'number' && !Number.isFinite(raw)) {
    return refuse(PriceRefusal.NOT_A_NUMBER);
  }
  if (raw !== null && raw !== undefined
    && typeof raw !== 'string' && typeof raw !== 'number') {
    return refuse(PriceRefusal.NOT_A_NUMBER);
  }

  let s = typed.trim().replace(CURRENCY_RE, '').trim();
  if (s === '') return refuse(PriceRefusal.EMPTY);
  if (s.includes(',')) return refuse(PriceRefusal.SEPARATOR);

  // Sign is peeled BEFORE the shape check so that '-5' is refused as NEGATIVE
  // — which tells the shopkeeper what is wrong — and not as NOT_A_NUMBER,
  // which would tell them nothing.
  let negative = false;
  if (s.startsWith('-')) { negative = true; s = s.slice(1); }
  else if (s.startsWith('+')) { s = s.slice(1); }

  if (!DECIMAL_RE.test(s)) {
    if (EXPONENT_RE.test(typed.trim())) return refuse(PriceRefusal.EXPONENT);
    return refuse(PriceRefusal.NOT_A_NUMBER);
  }
  if (negative) return refuse(PriceRefusal.NEGATIVE);

  const dot = s.indexOf('.');
  const intPart = dot === -1 ? s : s.slice(0, dot);
  const fracPart = dot === -1 ? '' : s.slice(dot + 1);
  if (fracPart.length > 2) return refuse(PriceRefusal.SUB_PAISE);

  // Pad to exactly two digits, then CONCATENATE. No multiplication anywhere.
  const frac2 = (fracPart + '00').slice(0, 2);
  const digits = intPart + frac2;

  // Bound the string before Number() sees it: 400 digits would silently become
  // Infinity, and Infinity is not a refusal reason anybody can act on.
  if (digits.length > 15) return refuse(PriceRefusal.TOO_LARGE);
  const paise = Number(digits);
  if (!Number.isSafeInteger(paise)) return refuse(PriceRefusal.TOO_LARGE);
  if (paise === 0) return refuse(PriceRefusal.ZERO);
  if (paise > MAX_PAISE) return refuse(PriceRefusal.TOO_LARGE);

  return { ok: true, paise, reason: null, detail: '', typed };
}

/**
 * Indian digit grouping: 1234567 -> '12,34,567'. Last three, then pairs.
 * Pure string surgery — the whole point is that the integer is never divided.
 */
export function groupIndian(digits) {
  const d = String(digits);
  if (d.length <= 3) return d;
  const last3 = d.slice(-3);
  let rest = d.slice(0, -3);
  const parts = [];
  while (rest.length > 2) {
    parts.unshift(rest.slice(-2));
    rest = rest.slice(0, -2);
  }
  if (rest) parts.unshift(rest);
  return `${parts.join(',')},${last3}`;
}

/**
 * Integer paise -> the rupee string a human reads. Also string surgery: pad to
 * three digits so there is always something to slice, take the last two as
 * paise and everything before it as rupees. `formatPaise(21450)` is
 * '₹214.50'. No division, no toFixed, no float.
 */
export function formatPaise(paise) {
  if (!Number.isInteger(paise)) return '—';
  const neg = paise < 0;
  const s = String(neg ? -paise : paise).padStart(3, '0');
  const rupees = groupIndian(s.slice(0, -2));
  const p = s.slice(-2);
  return `${neg ? '-' : ''}₹${rupees}.${p}`;
}

/** A typed name -> a name the brain will accept, or a named refusal. */
export function parseName(raw, taken = []) {
  const typed = raw === null || raw === undefined ? '' : String(raw);
  const refuse = (reason) => ({
    ok: false,
    name: null,
    reason,
    detail: NAME_REFUSAL_HELP[reason] || reason,
    typed,
  });
  const name = typed.trim().replace(/\s+/g, ' ');
  if (name === '') return refuse(NameRefusal.EMPTY);
  if (name.length > MAX_NAME_LEN) return refuse(NameRefusal.TOO_LONG);
  if (name.includes('/') || name.includes('\\') || name.includes('..')) {
    return refuse(NameRefusal.PATH_LIKE);
  }
  if (!NAME_RE.test(name)) return refuse(NameRefusal.BAD_CHARS);
  const lower = name.toLowerCase();
  for (const t of taken) {
    if (String(t).trim().toLowerCase() === lower) return refuse(NameRefusal.DUPLICATE);
  }
  return { ok: true, name, reason: null, detail: '', typed };
}

// ===========================================================================
// 2. THE CATALOGUE. A pure reducer, so the list is a tested function and not
//    an accident of a click handler.
// ===========================================================================

export const Action = Object.freeze({
  ADD: 'ADD_SKU',
  REMOVE: 'REMOVE_SKU',
  CLEAR: 'CLEAR_REFUSAL',
});

/** The empty catalogue. Frozen shape, so a caller cannot grow a field. */
export function emptyCatalogue() {
  return Object.freeze({ skus: Object.freeze([]), seq: 0, refusal: null });
}

/**
 * Never mutates, never throws, never partially applies. A refused ADD leaves
 * `skus` byte-identical and puts a named refusal in `refusal` — the shopkeeper
 * sees WHY, and the list they were looking at does not shuffle under them.
 */
export function reduceCatalogue(state, action) {
  const st = state && typeof state === 'object' ? state : emptyCatalogue();
  const skus = Array.isArray(st.skus) ? st.skus : [];
  const a = action && typeof action === 'object' ? action : { type: null };

  switch (a.type) {
    case Action.ADD: {
      const nameV = parseName(a.name, skus.map((s) => s.name));
      if (!nameV.ok) {
        return Object.freeze({
          ...st,
          skus: Object.freeze(skus),
          refusal: Object.freeze({
            field: 'name', reason: nameV.reason, detail: nameV.detail, typed: nameV.typed,
          }),
        });
      }
      const priceV = parsePaise(a.price);
      if (!priceV.ok) {
        return Object.freeze({
          ...st,
          skus: Object.freeze(skus),
          refusal: Object.freeze({
            field: 'price', reason: priceV.reason, detail: priceV.detail, typed: priceV.typed,
          }),
        });
      }
      const seq = (Number.isInteger(st.seq) ? st.seq : 0) + 1;
      const row = Object.freeze({
        id: `sku-${seq}`,
        name: nameV.name,
        pricePaise: priceV.paise,
        typed: priceV.typed,
      });
      return Object.freeze({ skus: Object.freeze([...skus, row]), seq, refusal: null });
    }

    case Action.REMOVE: {
      const next = skus.filter((s) => s.id !== a.id);
      if (next.length === skus.length) {
        return Object.freeze({
          ...st,
          skus: Object.freeze(skus),
          refusal: Object.freeze({
            field: 'row',
            reason: 'sku_unknown_row',
            detail: `no catalogue row ${String(a.id)}; nothing was removed`,
            typed: String(a.id ?? ''),
          }),
        });
      }
      return Object.freeze({ ...st, skus: Object.freeze(next), refusal: null });
    }

    case Action.CLEAR:
      return Object.freeze({ ...st, skus: Object.freeze(skus), refusal: null });

    default:
      return Object.freeze({ ...st, skus: Object.freeze(skus) });
  }
}

// ===========================================================================
// 3. THE ABSTENTIONS. Invariant 7: each of these is a state this surface can
//    actually be in, and each is rendered as an I-DO-NOT-KNOW block.
// ===========================================================================

export const Abstain = Object.freeze({
  NOTHING_TYPED: 'enrol_nothing_typed',
  NO_STICKER_RESULT: 'enrol_no_sticker_result',
  NO_BURST_REPORTED: 'enrol_no_burst_reported',
  NO_BRAIN_SEAM: 'enrol_no_brain_seam',
});

export const ABSTAIN_CODES = Object.freeze(Object.values(Abstain));

export const ABSTAIN_HELP = Object.freeze({
  [Abstain.NOTHING_TYPED]:
    'nothing has been typed into this catalogue, so there is no SKU and no '
    + 'price. This is the cold-load state and it is the truth.',
  [Abstain.NO_STICKER_RESULT]:
    'no sticker enrolment has been attempted from this surface, so PEEL has '
    + 'said nothing to it. This panel shows the brain\'s verdict; it does not '
    + 'have one yet and will not invent one.',
  [Abstain.NO_BURST_REPORTED]:
    'SAAF has not reported a burst to this surface. The burst lives on the '
    + 'brain as a rolling window of rectified frames; until it is asked, this '
    + 'panel does not know how many frames are in it.',
  [Abstain.NO_BRAIN_SEAM]:
    'there is no open path to the brain from this surface — no injected send, '
    + 'no window.GAWAAH.send and no socket. Buttons here would do nothing, so '
    + 'they say so instead of appearing to work.',
});

// ===========================================================================
// 4. THE VIEW MODEL. Pure: state in, a plain object out. No document.
// ===========================================================================

/**
 * Fold the catalogue, the last brain replies and the transport status into the
 * one object the renderer walks. Anything the panel does not know comes out as
 * a named abstention rather than a blank or a zero.
 */
export function deriveEnrol(input = {}) {
  const cat = input.catalogue && typeof input.catalogue === 'object'
    ? input.catalogue : emptyCatalogue();
  const skus = Array.isArray(cat.skus) ? cat.skus : [];
  const peel = input.peel && typeof input.peel === 'object' ? input.peel : null;
  const saaf = input.saaf && typeof input.saaf === 'object' ? input.saaf : null;
  const refused = input.refused && typeof input.refused === 'object' ? input.refused : null;
  const canSend = input.canSend === true;
  const simulated = input.simulated === true;

  const abstentions = [];
  if (skus.length === 0) abstentions.push(Abstain.NOTHING_TYPED);
  if (peel === null) abstentions.push(Abstain.NO_STICKER_RESULT);
  if (saaf === null) abstentions.push(Abstain.NO_BURST_REPORTED);
  if (!canSend) abstentions.push(Abstain.NO_BRAIN_SEAM);

  return Object.freeze({
    id: PANEL_ID,
    title: PANEL_TITLE,
    simulated,
    transport: Object.freeze({
      canSend,
      via: typeof input.via === 'string' ? input.via : 'none',
      sent: Number.isInteger(input.sent) ? input.sent : 0,
      lastSent: input.lastSent ? Object.freeze({ ...input.lastSent }) : null,
    }),
    catalogue: Object.freeze({
      rows: Object.freeze(skus.map((s) => Object.freeze({
        id: s.id,
        name: s.name,
        pricePaise: s.pricePaise,
        rupees: formatPaise(s.pricePaise),
        paiseText: `${s.pricePaise} paise`,
        typed: s.typed ?? '',
      }))),
      count: skus.length,
      refusal: cat.refusal ? Object.freeze({ ...cat.refusal }) : null,
    }),
    peel: peel === null ? null : Object.freeze({
      ok: peel.ok === true,
      name: typeof peel.name === 'string' ? peel.name : '',
      verdict: typeof peel.verdict === 'string' ? peel.verdict : '',
      registered: peel.registered === true,
      ignited: typeof peel.ignited_fraction === 'number' ? peel.ignited_fraction : null,
      reason: typeof peel.reason === 'string' ? peel.reason : '',
      detail: typeof peel.detail === 'string' ? peel.detail : '',
    }),
    saaf: saaf === null ? null : Object.freeze({
      ok: saaf.ok === true,
      used: Number.isFinite(saaf.used) ? saaf.used : 0,
      rejected: Number.isFinite(saaf.rejected) ? saaf.rejected : 0,
      burst: Number.isFinite(saaf.burst) ? saaf.burst : null,
      burstTarget: Number.isFinite(saaf.burst_target) ? saaf.burst_target : null,
      gain: typeof saaf.sharpness_gain === 'number' ? saaf.sharpness_gain : null,
      warning: typeof saaf.warning === 'string' ? saaf.warning : '',
      reason: typeof saaf.reason === 'string' ? saaf.reason : '',
      detail: typeof saaf.detail === 'string' ? saaf.detail : '',
    }),
    refused: refused === null ? null : Object.freeze({
      reason: typeof refused.reason === 'string' ? refused.reason : 'no reason given',
      detail: typeof refused.detail === 'string' ? refused.detail : '',
    }),
    abstentions: Object.freeze(abstentions),
    // The PHOTO flow (sections 8-10) hangs off its own key with its own
    // abstention list. It is deliberately NOT merged into `abstentions` above:
    // that list is what the BRAIN has not said, this one is what the DESK has
    // not said, and an operator who conflates them restarts the wrong process.
    // `derivePhoto` is a function declaration, so it is hoisted and callable
    // from here even though it is defined three sections further down.
    photo: derivePhoto(input),
  });
}

// ===========================================================================
// 5. RENDER. Still pure in the sense that matters: `doc` is a parameter, so
//    the test drives it with a 200-line shim and no browser at all.
// ===========================================================================

function mk(doc, tag, spec = {}) {
  const el = doc.createElement(tag);
  if (spec.class) el.className = spec.class;
  if (spec.text !== undefined && spec.text !== null) el.textContent = String(spec.text);
  if (spec.attrs) for (const [k, v] of Object.entries(spec.attrs)) el.setAttribute(k, String(v));
  if (spec.data && el.dataset) for (const [k, v] of Object.entries(spec.data)) el.dataset[k] = String(v);
  if (spec.on && typeof el.addEventListener === 'function') {
    for (const [k, v] of Object.entries(spec.on)) el.addEventListener(k, v);
  }
  for (const kid of spec.kids || []) if (kid) el.appendChild(kid);
  return el;
}

/**
 * The permanent, non-dismissable statement of what a price on this surface IS.
 * It is not a bill line, it is not authorised and it is not green. Said once,
 * at the top, always — an operator who scrolls past it still saw it.
 */
export const CATALOGUE_NOTE =
  'LOCAL CATALOGUE — typed on this device. These prices are integer paise held '
  + 'in this browser tab. Nothing here is a bill, nothing here has been '
  + 'accepted by the brain, and nothing here can settle a sale: only a '
  + 'signature-verified webhook turns this counter green.';

export const BURST_NOTE =
  'the brain holds a ROLLING window of the last N rectified frames. There is no '
  + '"start capture" verb in the protocol and this button does not pretend to be '
  + 'one: it asks SAAF to publish the burst it is holding right now.';

export const SIM_NOTE =
  'the brain is running with --sim. Every frame behind the readings below is '
  + 'synthetic: these are real measurements of a fake counter, and nothing '
  + 'enrolled from them describes any physical object. SIMULATED.';

/**
 * THE SHELL'S CSS VOCABULARY.
 *
 * index.html ships `style-src 'self'` with no 'unsafe-inline'. A <style>
 * element injected by this module is therefore PARSED INTO THE DOM AND THEN
 * IGNORED — it is present, it matches nothing, and the surface renders
 * unstyled. That was found by running this panel in headless Chrome and
 * reading getComputedStyle off the refusal box: `bg=rgba(0, 0, 0, 0)`. It is
 * invisible to every unit test in the file, because a DOM shim has no CSSOM
 * and no CSP.
 *
 * So the visual meaning is carried by the classes the SHELL already defines in
 * web/style.css, which is loaded from 'self' and therefore applies:
 *
 *   .simstrip/.simbadge  the yellow "this is synthetic" strip
 *   .abstain/-tag/-why   the amber I-DO-NOT-KNOW block every panel ships with
 *   .line-amber          amber hatching, which in this app MEANS "refused
 *                        rather than guessed" — exactly what a rejected price
 *                        is, so a refusal here looks like a refusal everywhere
 *   .lines/.line/...     the basket row, reused for a catalogue row
 *   .num                 tabular numerals, mandatory on every rupee figure
 *   .orient/.orient-key  the explanatory block and its small caps heading
 *   .btn                 an action
 *
 * Each of those is asserted to still exist in style.css by enrol.test.mjs, so
 * a rename in the shell surfaces as a failed test here rather than as a panel
 * that quietly loses its paint. The `enrol-*` classes alongside them are
 * test hooks and carry no styling.
 */
export const SHELL_CLASSES = Object.freeze([
  'simstrip', 'simbadge', 'abstain', 'abstain-tag', 'abstain-why',
  'line-amber', 'lines', 'line', 'line-name', 'line-price', 'num',
  'orient', 'orient-is', 'orient-key', 'btn', 'reason',
]);

function renderRefusal(model, doc) {
  const r = model.catalogue.refusal;
  if (!r) return null;
  return mk(doc, 'div', {
    // .abstain + .line-amber: amber, hatched. In this app that pattern means
    // "nothing was guessed here", which is precisely what a refused price is.
    class: 'abstain line-amber enrol-refusal',
    data: { field: r.field, reason: r.reason },
    attrs: { role: 'alert' },
    kids: [
      mk(doc, 'div', { class: 'abstain-tag enrol-refusal-tag', text: 'REFUSED' }),
      mk(doc, 'p', { class: 'enrol-refusal-detail', text: r.detail }),
      r.typed === '' ? null : mk(doc, 'p', {
        class: 'enrol-refusal-typed',
        text: `you typed: ${JSON.stringify(r.typed)} — it was NOT added and it was NOT rounded.`,
      }),
      mk(doc, 'code', { class: 'abstain-why enrol-refusal-why', text: r.reason }),
    ],
  });
}

function renderRows(model, doc, onRemove) {
  if (model.catalogue.count === 0) {
    return mk(doc, 'p', {
      class: 'reason enrol-empty',
      data: { reason: Abstain.NOTHING_TYPED },
      text: ABSTAIN_HELP[Abstain.NOTHING_TYPED],
    });
  }
  return mk(doc, 'ol', {
    class: 'lines enrol-rows',
    data: { count: String(model.catalogue.count) },
    kids: model.catalogue.rows.map((row) => mk(doc, 'li', {
      class: 'line enrol-row',
      data: { sku: row.id, paise: String(row.pricePaise) },
      kids: [
        mk(doc, 'span', { class: 'line-name enrol-row-name', text: row.name }),
        // The paise are shown BESIDE the rupees, always. The rupee string is a
        // rendering; the integer is the value. Showing only the rendering is
        // how a rounding bug hides for a year.
        mk(doc, 'code', { class: 'enrol-row-paise', text: row.paiseText }),
        mk(doc, 'span', { class: 'line-price num enrol-row-rupees', text: row.rupees }),
        mk(doc, 'button', {
          class: 'enrol-btn enrol-btn-remove',
          text: 'remove',
          attrs: { type: 'button', 'data-remove': row.id },
          on: { click: () => onRemove(row.id) },
        }),
        mk(doc, 'button', {
          class: 'enrol-btn enrol-btn-sticker',
          text: 'enrol sticker',
          attrs: { type: 'button', 'data-enrol': row.name },
          on: { click: () => onRemove(null, row.name) },
        }),
      ],
    })),
  });
}

function renderPeel(model, doc) {
  if (model.peel === null) {
    return mk(doc, 'div', {
      class: 'abstain enrol-abstain',
      data: { reason: Abstain.NO_STICKER_RESULT },
      kids: [
        mk(doc, 'div', { class: 'abstain-tag enrol-abstain-tag', text: 'I DO NOT KNOW' }),
        mk(doc, 'p', { text: ABSTAIN_HELP[Abstain.NO_STICKER_RESULT] }),
        mk(doc, 'code', { class: 'abstain-why enrol-abstain-why', text: Abstain.NO_STICKER_RESULT }),
      ],
    });
  }
  const p = model.peel;
  return mk(doc, 'div', {
    class: 'orient enrol-peel',
    data: { ok: String(p.ok), registered: String(p.registered), verdict: p.verdict },
    kids: [
      mk(doc, 'h4', { class: 'orient-key enrol-h4', text: `PEEL says: ${p.verdict || p.reason || 'nothing'}` }),
      mk(doc, 'p', { class: 'enrol-peel-name', text: `name: ${p.name || '(none)'}` }),
      p.ignited === null
        ? mk(doc, 'p', { class: 'enrol-peel-ignited', text: 'ignited fraction: not measured' })
        : mk(doc, 'p', { class: 'enrol-peel-ignited', text: `ignited fraction: ${p.ignited}` }),
      mk(doc, 'p', {
        class: 'enrol-peel-registered',
        text: p.registered
          ? 'the brain REGISTERED this sticker.'
          : 'the brain did NOT register it — a refused enrolment is a good outcome, '
            + 'not an error: a bad reference is silent forever.',
      }),
      p.detail ? mk(doc, 'p', { class: 'enrol-peel-detail', text: p.detail }) : null,
      mk(doc, 'code', { class: 'abstain-why enrol-code', text: p.reason || '(no reason code)' }),
    ],
  });
}

function renderSaaf(model, doc) {
  if (model.saaf === null) {
    return mk(doc, 'div', {
      class: 'abstain enrol-abstain',
      data: { reason: Abstain.NO_BURST_REPORTED },
      kids: [
        mk(doc, 'div', { class: 'abstain-tag enrol-abstain-tag', text: 'I DO NOT KNOW' }),
        mk(doc, 'p', { text: ABSTAIN_HELP[Abstain.NO_BURST_REPORTED] }),
        mk(doc, 'code', { class: 'abstain-why enrol-abstain-why', text: Abstain.NO_BURST_REPORTED }),
      ],
    });
  }
  const s = model.saaf;
  return mk(doc, 'div', {
    class: 'orient enrol-saaf',
    data: { ok: String(s.ok), burst: String(s.burst ?? ''), warning: s.warning },
    kids: [
      mk(doc, 'h4', { class: 'orient-key enrol-h4', text: 'SAAF says' }),
      mk(doc, 'p', {
        class: 'enrol-saaf-burst',
        text: s.burst === null
          ? 'burst size: not reported'
          : `burst: ${s.burst}${s.burstTarget === null ? '' : ` of ${s.burstTarget}`} frames held`,
      }),
      mk(doc, 'p', { class: 'enrol-saaf-counts', text: `used ${s.used}, rejected ${s.rejected}` }),
      mk(doc, 'p', {
        class: 'enrol-saaf-gain',
        text: s.gain === null
          ? 'sharpness gain: not measured (SAAF runs on enrolment, not every frame)'
          : `sharpness gain: ${s.gain} (vLap ratio — it also counts noise, so on a `
            + 'noisy burst this UNDERSTATES the real gain)',
      }),
      s.warning ? mk(doc, 'p', { class: 'enrol-saaf-warning', text: `WARNING: ${s.warning}` }) : null,
      s.detail ? mk(doc, 'p', { class: 'enrol-saaf-detail', text: s.detail }) : null,
      mk(doc, 'code', { class: 'abstain-why enrol-code', text: s.reason || '(no reason code)' }),
    ],
  });
}

function renderTransport(model, doc) {
  const t = model.transport;
  if (!t.canSend) {
    return mk(doc, 'div', {
      class: 'abstain enrol-abstain',
      data: { reason: Abstain.NO_BRAIN_SEAM },
      kids: [
        mk(doc, 'div', { class: 'abstain-tag enrol-abstain-tag', text: 'I DO NOT KNOW' }),
        mk(doc, 'p', { text: ABSTAIN_HELP[Abstain.NO_BRAIN_SEAM] }),
        mk(doc, 'code', { class: 'abstain-why enrol-abstain-why', text: Abstain.NO_BRAIN_SEAM }),
      ],
    });
  }
  return mk(doc, 'p', {
    class: 'reason enrol-transport',
    data: { via: t.via, sent: String(t.sent) },
    text: `brain seam: ${t.via} — ${t.sent} message${t.sent === 1 ? '' : 's'} sent`
      + (t.lastSent ? ` (last: ${t.lastSent.type})` : ''),
  });
}

/**
 * Build the whole surface. `handlers` carries the three things a click can do;
 * the test supplies its own and asserts the message that would go on the wire.
 */
export function renderEnrol(model, doc, handlers = {}) {
  const onAdd = typeof handlers.onAdd === 'function' ? handlers.onAdd : () => {};
  const onRemove = typeof handlers.onRemove === 'function' ? handlers.onRemove : () => {};
  const onEnrol = typeof handlers.onEnrol === 'function' ? handlers.onEnrol : () => {};
  const onBurst = typeof handlers.onBurst === 'function' ? handlers.onBurst : () => {};

  const nameInput = mk(doc, 'input', {
    class: 'enrol-input enrol-input-name',
    attrs: { type: 'text', id: 'enrol-name', placeholder: 'SKU name, e.g. Parle-G 100g', maxlength: String(MAX_NAME_LEN) },
  });
  const priceInput = mk(doc, 'input', {
    class: 'enrol-input enrol-input-price',
    attrs: { type: 'text', id: 'enrol-price', placeholder: 'price in RUPEES, e.g. 214.50', inputmode: 'decimal' },
  });
  const stickerInput = mk(doc, 'input', {
    class: 'enrol-input enrol-input-sticker',
    attrs: { type: 'text', id: 'enrol-sticker', placeholder: 'sticker name to enrol' },
  });

  // The row buttons call one handler with (id) to remove and (null, name) to
  // enrol — one closure, two intents, so the row markup stays flat.
  const rowClick = (id, name) => { if (id === null) onEnrol(name); else onRemove(id); };

  return mk(doc, 'section', {
    class: 'enrol-surface',
    data: {
      panel: PANEL_ID,
      simulated: String(model.simulated),
      cansend: String(model.transport.canSend),
      skus: String(model.catalogue.count),
      abstentions: model.abstentions.join(' '),
    },
    kids: [
      mk(doc, 'div', {
        class: 'enrol-head',
        kids: [
          mk(doc, 'h2', { class: 'enrol-title', text: PANEL_TITLE }),
          mk(doc, 'p', { class: 'enrol-sub', text: 'the only surface in this rig with an input. It writes; it decides nothing.' }),
        ],
      }),

      // INVARIANT 7 on screen. `.simstrip` is the shell's yellow band and
      // `.simbadge` its pill; using them means a simulated reading looks
      // identical here to a simulated reading anywhere else on the page, which
      // is the only way a viewer learns to trust the label.
      model.simulated
        ? mk(doc, 'div', {
          class: 'simstrip enrol-sim',
          data: { sim: 'true' },
          attrs: { role: 'note' },
          kids: [
            mk(doc, 'b', { class: 'simbadge', text: 'SIMULATED' }),
            mk(doc, 'p', { text: SIM_NOTE }),
          ],
        })
        : null,

      mk(doc, 'p', { class: 'orient orient-is enrol-note', text: CATALOGUE_NOTE }),

      renderTransport(model, doc),

      // ---- THE PHOTO FLOW -------------------------------------------------
      // Where the desk is, teach it, what it has been taught, and the payoff.
      // TRY IT sits immediately after the catalogue because that ordering IS
      // the demonstration: teach it, see it listed, then show it a new photo.
      renderService(model, doc, handlers),
      renderTeach(model, doc, handlers),
      renderCatalog(model, doc, handlers),
      renderTryIt(model, doc, handlers),

      mk(doc, 'div', {
        class: 'abstain enrol-photo-abstentions',
        data: { count: String(model.photo.abstentions.length) },
        kids: [
          mk(doc, 'h4', {
            class: 'orient-key enrol-h4',
            text: `what the enrolment desk has not told this surface (${model.photo.abstentions.length})`,
          }),
          model.photo.abstentions.length === 0
            ? mk(doc, 'p', { class: 'reason enrol-hint', text: 'nothing outstanding on the photo flow.' })
            : mk(doc, 'ul', {
              class: 'enrol-photo-abstain-list',
              kids: model.photo.abstentions.map((code) => mk(doc, 'li', {
                class: 'enrol-abstain-item',
                data: { reason: code },
                kids: [
                  mk(doc, 'code', { class: 'abstain-why enrol-abstain-why', text: code }),
                  mk(doc, 'span', { text: ` ${PHOTO_ABSTAIN_HELP[code] || ''}` }),
                ],
              })),
            }),
        ],
      }),

      // ---- add an SKU ----------------------------------------------------
      mk(doc, 'div', {
        class: 'enrol-form',
        kids: [
          mk(doc, 'h3', { class: 'orient-key enrol-h3', text: '4. add a LOCAL catalogue row (typed, no photo)' }),
          mk(doc, 'label', { class: 'orient-key enrol-label', attrs: { for: 'enrol-name' }, text: 'name' }),
          nameInput,
          mk(doc, 'label', { class: 'orient-key enrol-label', attrs: { for: 'enrol-price' }, text: 'price (rupees)' }),
          priceInput,
          mk(doc, 'button', {
            class: 'btn enrol-btn enrol-btn-add',
            text: 'ADD SKU',
            attrs: { type: 'button', id: 'enrol-add' },
            on: { click: () => onAdd(nameInput.value, priceInput.value) },
          }),
          mk(doc, 'p', {
            class: 'reason enrol-hint',
            text: 'the price is converted to INTEGER PAISE here and refused if it is not '
              + 'exact. 214.507 is refused, not rounded. Commas and 1e3 are refused too.',
          }),
        ],
      }),

      renderRefusal(model, doc),

      // ---- the list ------------------------------------------------------
      mk(doc, 'div', {
        class: 'enrol-list',
        kids: [
          mk(doc, 'h3', { class: 'orient-key enrol-h3', text: `5. the LOCAL typed list (${model.catalogue.count})` }),
          renderRows(model, doc, rowClick),
          mk(doc, 'p', {
            class: 'reason enrol-hint',
            text: 'no total is shown. A catalogue is not a bill, and this panel does not '
              + 'add money up — the brain owns the basket and the reducer owns the total.',
          }),
        ],
      }),

      // ---- enrol a sticker ------------------------------------------------
      mk(doc, 'div', {
        class: 'enrol-sticker-form',
        kids: [
          mk(doc, 'h3', { class: 'orient-key enrol-h3', text: '6. enrol a sticker' }),
          mk(doc, 'label', { class: 'orient-key enrol-label', attrs: { for: 'enrol-sticker' }, text: 'sticker name' }),
          stickerInput,
          mk(doc, 'button', {
            class: 'btn enrol-btn enrol-btn-enrol',
            text: 'ENROL STICKER',
            attrs: { type: 'button', id: 'enrol-send' },
            on: { click: () => onEnrol(stickerInput.value) },
          }),
          mk(doc, 'p', {
            class: 'reason enrol-hint',
            text: 'sends {"type":"enrol_sticker", name} to the brain. The brain stacks the '
              + 'burst with SAAF and enrols with PEEL, and both answer below. This panel '
              + 'does not decide whether an enrolment worked.',
          }),
          renderPeel(model, doc),
        ],
      }),

      // ---- ask SAAF -------------------------------------------------------
      mk(doc, 'div', {
        class: 'enrol-burst-form',
        kids: [
          mk(doc, 'h3', { class: 'orient-key enrol-h3', text: '7. capture burst (SAAF)' }),
          mk(doc, 'button', {
            class: 'btn enrol-btn enrol-btn-burst',
            text: 'CAPTURE BURST',
            attrs: { type: 'button', id: 'enrol-burst' },
            on: { click: () => onBurst() },
          }),
          mk(doc, 'p', { class: 'reason enrol-hint', text: BURST_NOTE }),
          renderSaaf(model, doc),
        ],
      }),

      model.refused
        ? mk(doc, 'div', {
          class: 'abstain line-amber enrol-brain-refused',
          data: { reason: model.refused.reason },
          attrs: { role: 'alert' },
          kids: [
            mk(doc, 'div', { class: 'abstain-tag enrol-refusal-tag', text: 'THE BRAIN REFUSED' }),
            mk(doc, 'code', { class: 'abstain-why enrol-code', text: model.refused.reason }),
            model.refused.detail ? mk(doc, 'p', { text: model.refused.detail }) : null,
          ],
        })
        : null,

      // ---- what this surface does not know --------------------------------
      mk(doc, 'div', {
        class: 'abstain enrol-abstentions',
        data: { count: String(model.abstentions.length) },
        kids: [
          mk(doc, 'h3', { class: 'orient-key enrol-h3', text: `what this surface does not know (${model.abstentions.length})` }),
          model.abstentions.length === 0
            ? mk(doc, 'p', { class: 'reason enrol-hint', text: 'nothing outstanding: a catalogue exists, both panels have answered and the brain seam is open.' })
            : mk(doc, 'ul', {
              class: 'enrol-abstain-list',
              kids: model.abstentions.map((code) => mk(doc, 'li', {
                class: 'enrol-abstain-item',
                data: { reason: code },
                kids: [
                  mk(doc, 'code', { class: 'abstain-why enrol-abstain-why', text: code }),
                  mk(doc, 'span', { text: ` ${ABSTAIN_HELP[code] || ''}` }),
                ],
              })),
            }),
        ],
      }),
    ],
  });
}

// ===========================================================================
// 6. THE STYLESHEET. Injected by this module rather than added to style.css,
//    because style.css belongs to the shell agent and this panel must not need
//    a second file to be edited before it can be seen.
// ===========================================================================

export const STYLE_TEXT = `
/* Layout only. Nothing in here carries meaning — see the SHELL_CLASSES note.
   Measured in headless Chrome under index.html's policy:
     document.getElementById('enrol-style').sheet   -> undefined  (CSP blocked)
     document.adoptedStyleSheets.length             -> 1, 37 rules (applies)
   So this text reaches the page through the constructed-stylesheet path and is
   simply absent on engines that lack it. Deliberately declares no background,
   border or colour on .enrol-refusal / .enrol-abstain / .enrol-brain-refused:
   those wear the shell's .abstain and .line-amber, and an override here would
   make a refusal on this panel look unlike a refusal everywhere else. */
#${PANEL_ROOT_ID}{display:block;margin:14px 0 0}
.enrol-surface{display:block}
.enrol-title{font-size:15px;letter-spacing:.08em;margin:0 0 2px}
.enrol-sub{margin:0 0 12px}
.enrol-h3{margin:18px 0 6px}
.enrol-h4{margin:0 0 4px}
.enrol-label{margin:8px 0 3px}
.enrol-input{display:block;width:min(340px,100%);padding:7px 9px;margin:0;
  border-radius:5px;font:14px ui-monospace,Menlo,Consolas,monospace}
.enrol-form .enrol-btn,.enrol-sticker-form .enrol-btn,.enrol-burst-form .enrol-btn{
  display:inline-block;flex:none;margin:12px 8px 0 0;font-size:13px;padding:10px 16px}
.enrol-row{flex-wrap:wrap;gap:8px;cursor:default}
.enrol-row-name{flex:1 1 150px}
/* the shell's .line-price uses the \`font\` SHORTHAND, which resets
   font-variant-numeric and undoes .num. Put the tabular figures back. */
.enrol-row-rupees,.enrol-row-paise{font-variant-numeric:tabular-nums;
  font-feature-settings:"tnum" 1}
.enrol-row-paise{opacity:.72;font-size:11px;margin-left:auto}
.enrol-row-rupees{margin-left:12px}
.enrol-btn-remove,.enrol-btn-sticker{flex:none;font-size:11px;padding:4px 9px;
  border-radius:5px;letter-spacing:.06em;cursor:pointer}
.enrol-hint{margin:7px 0 0;max-width:78ch}
.enrol-note{margin:0 0 12px}
.enrol-transport{margin:0 0 6px}
.enrol-peel,.enrol-saaf{margin-top:10px}
.enrol-peel p,.enrol-saaf p{margin:3px 0;font-size:13px}
.enrol-saaf-warning{font-weight:700}
.enrol-abstain-list{margin:9px 0 0;padding-left:18px}
.enrol-abstain-item{margin:6px 0}
.enrol-refusal-typed{font-weight:600}
.enrol-empty{margin:6px 0 0;max-width:78ch}

/* ---- the photo flow (sections 8-10). Layout only, same rule as above. ---- */
.enrol-teach .enrol-btn,.enrol-catalog .enrol-btn,.enrol-try .enrol-btn,
.enrol-service .enrol-btn{display:inline-block;flex:none;margin:12px 8px 0 0;
  font-size:13px;padding:10px 16px}
.enrol-teach,.enrol-catalog,.enrol-service{margin:0 0 14px}
/* TRY IT is the payoff and is the one block that gets visual weight: a rule
   above it and room around it, so the eye lands there after the catalogue. */
.enrol-try{display:block;margin:22px 0 14px;padding:14px 0 0;
  border-top:2px solid currentColor}
.enrol-preview{margin:10px 0 0;font-size:13px}
.enrol-preview-paise{font-variant-numeric:tabular-nums;font-weight:700}
.enrol-preview-rupees{font-variant-numeric:tabular-nums;margin:0 2px}
.enrol-catalog-row,.enrol-try-row{flex-wrap:wrap;gap:8px;cursor:default;
  align-items:center}
.enrol-thumb{flex:none;width:64px;height:64px;object-fit:cover;border-radius:4px}
.enrol-thumb-none{flex:none;font-size:10px;opacity:.7;max-width:90px}
.enrol-catalog-name,.enrol-try-name{flex:1 1 140px;font-weight:600}
.enrol-catalog-sku,.enrol-catalog-mm,.enrol-try-mm,.enrol-try-scores{
  font-size:11px;opacity:.72}
.enrol-catalog-paise,.enrol-try-paise{font-size:11px;opacity:.72;
  font-variant-numeric:tabular-nums}
.enrol-catalog-rupees,.enrol-try-rupees,.enrol-try-total-paise,
.enrol-try-total-rupees{font-variant-numeric:tabular-nums;
  font-feature-settings:"tnum" 1}
.enrol-btn-forget{flex:none;font-size:11px;padding:4px 9px;border-radius:5px;
  letter-spacing:.06em;cursor:pointer}
.enrol-try-tag{flex:none;font-size:10px;letter-spacing:.08em}
.enrol-try-note,.enrol-try-excluded{flex:1 1 100%;margin:4px 0 0;font-size:12px}
.enrol-try-total{margin:12px 0 0;font-size:14px;font-weight:600}
.enrol-try-audit{margin:6px 0 0}
.enrol-photo-abstain-list{margin:9px 0 0;padding-left:18px}
.enrol-input-base{width:min(420px,100%)}
`;

/**
 * Extra paint, on a best-effort basis. Idempotent.
 *
 * READ THE SHELL_CLASSES NOTE FIRST. Under index.html's `style-src 'self'`
 * this sheet does NOTHING: the element lands in <head> and every rule in it is
 * ignored. That is why none of the MEANING on this surface depends on it — the
 * abstentions, the refusal, the money and the SIMULATED strip are all carried
 * by classes from web/style.css, which is served from 'self' and does apply.
 * What is here is layout polish for a shell without that policy, and it is
 * kept precisely because the panel has to look right in both.
 *
 * A constructible stylesheet is tried first because it is not an "inline
 * style" as CSP defines one; the <style> element is the fallback. Neither is
 * allowed to throw: a panel that cannot paint must still work.
 */
export function injectStyle(doc) {
  if (!doc || typeof doc.createElement !== 'function') return null;
  const existing = typeof doc.getElementById === 'function' ? doc.getElementById(STYLE_ID) : null;
  if (existing) return existing;
  try {
    const g = globalThis;
    if (typeof g.CSSStyleSheet === 'function' && Array.isArray(doc.adoptedStyleSheets)) {
      const sheet = new g.CSSStyleSheet();
      if (typeof sheet.replaceSync === 'function') {
        sheet.replaceSync(STYLE_TEXT);
        doc.adoptedStyleSheets = [...doc.adoptedStyleSheets, sheet];
      }
    }
  } catch { /* CSP or an old engine; the shell classes carry the meaning */ }
  const el = doc.createElement('style');
  el.setAttribute('id', STYLE_ID);
  el.textContent = STYLE_TEXT;
  const head = doc.head || doc.body || null;
  if (head && typeof head.appendChild === 'function') head.appendChild(el);
  return el;
}

// ===========================================================================
// 7. THE TRANSPORT. Three seams tried in order, and an honest "none" if all
//    three are absent. A button that cannot reach the brain says so; it does
//    not appear to work.
// ===========================================================================

/** The port web/app.js hardcodes. Used only when the page has no port of its own. */
export const WS_PORT = 8787;

/**
 * Which port the brain is on.
 *
 * THE PAGE CAME FROM THE BRAIN. `create_app()` in gawaah/brain_server.py mounts
 * the WebSocket at `/ws` and `/` and then mounts `web/` as StaticFiles on `/`
 * — one process, one port, serving both this file and the socket it dials. So
 * the honest default is the ORIGIN THIS PAGE WAS LOADED FROM, not a constant.
 *
 * app.js hardcodes 8787, which is right whenever the brain is on 8787 and
 * wrong the moment it is not — a second brain on another port serves a page
 * that immediately dials a different machine's socket. This function prefers
 * the page's own port and falls back to app.js's constant when there isn't one
 * (a file:// load, or a default-port URL where the brain is elsewhere), so the
 * two agree in the deployment they were both written for and this one is also
 * right in the deployment they were not.
 */
export function brainPort(loc) {
  const p = loc && typeof loc.port === 'string' ? loc.port : '';
  return p === '' ? String(WS_PORT) : p;
}

export function brainUrl(loc) {
  const host = loc && typeof loc.hostname === 'string' && loc.hostname !== '' ? loc.hostname : 'localhost';
  const scheme = loc && loc.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${scheme}//${host}:${brainPort(loc)}/ws`;
}

/** The brain's /health, used only to ask whether frames are synthetic. */
export function healthUrl(loc) {
  const host = loc && typeof loc.hostname === 'string' && loc.hostname !== '' ? loc.hostname : 'localhost';
  const scheme = loc && loc.protocol === 'https:' ? 'https:' : 'http:';
  return `${scheme}//${host}:${brainPort(loc)}/health`;
}

/**
 * Pick a way to talk to the brain.
 *   1. opts.send        — injected. What the test uses; also what a host shell
 *                         should pass so there is ONE socket for the page.
 *   2. GAWAAH.send      — if app.js ever exposes it. peel.js already probes for
 *                         this name, so honouring it keeps the two consistent.
 *   3. our own socket   — brain_server holds ONE BrainServer across every
 *                         connection (create_app closes over it), so a second
 *                         socket sees the same brain, the same rolling burst
 *                         and the same registry. This is not a private
 *                         simulation; it is the real bridge, dialled twice.
 *   4. nothing          — abstain by name. No silent no-op.
 */
export function resolveTransport(opts = {}, g = globalThis) {
  if (typeof opts.send === 'function') return { send: opts.send, via: 'injected', socket: null };
  if (g && g.GAWAAH && typeof g.GAWAAH.send === 'function') {
    return { send: (m) => g.GAWAAH.send(m), via: 'window.GAWAAH.send', socket: null };
  }
  return null;
}

// ===========================================================================
// 8. THE SHOP SERVICE (:8790) — the seam that turns a PHOTO into a PRODUCT.
//
// WHY THIS IS A DIFFERENT SEAM FROM SECTION 7
// Section 7 dials the BRAIN: a WebSocket, JSON verbs, the live counter. This
// section dials the SHOP: the HTTP service in tools/upload_app.py that owns
// the photo pipeline — lock the mat, measure the item in millimetres, embed
// the crop, store it against a price. Two services, two failure modes, two
// named degradations. Collapsing them into one "is it up?" boolean would give
// a page that reports the counter down when only the enrolment desk is.
//
//   POST   /enrol       image + sku_id + name + price_rupees -> what was stored
//   POST   /recognise   image -> per item: sku|null, reason, mm, price, total
//   GET    /shop        the catalogue, with prices and thumbnails
//   DELETE /shop/{id}   forget one SKU
//
// THE CSP FACT THAT SHAPES THIS WHOLE SECTION
// web/index.html ships `connect-src 'self'`. A page served by the brain on
// :8787 therefore CANNOT fetch http://127.0.0.1:8790 — the browser refuses
// before a socket is opened, and the failure surfaces as an opaque TypeError
// indistinguishable from "the service is down". It is not down; it is
// FORBIDDEN, and those two need different sentences because they need
// different fixes. So the default base here is the SAME ORIGIN — a bare
// '/enrol' — which is correct when this panel is served by upload_app.py
// itself, and any cross-origin base is labelled CSP-blocked BEFORE it is
// dialled rather than after it fails.
// ===========================================================================

/** The port tools/upload_app.py listens on. */
export const SHOP_PORT = 8790;

/**
 * The default base: the empty string, meaning "relative to whatever origin
 * served this page". Not a constant host — see the CSP note above.
 */
export const SAME_ORIGIN = '';

export const ShopPath = Object.freeze({
  ENROL: '/enrol',
  RECOGNISE: '/recognise',
  SHOP: '/shop',
  HEALTH: '/health',
});

/**
 * 8 MB, the same ceiling gawaah/brain_server.py puts on a decoded frame. A
 * bigger upload is a phone's full-resolution burst, not a photo of a packet,
 * and it is refused here rather than after a 30-second upload.
 */
export const MAX_IMAGE_BYTES = 8 * 1024 * 1024;

/**
 * A thumbnail is inlined into the DOM as a data: URI, so its size is a page
 * cost, not a network one. 400k characters of base64 is roughly 300 KB of
 * image — generous for a thumbnail, and a hard stop before a catalogue of 24
 * of them makes the panel unscrollable.
 */
export const MAX_THUMB_CHARS = 400000;

/** The image types tools/upload_app.py's decode_upload() actually decodes. */
export const IMAGE_TYPES = Object.freeze(['image/png', 'image/jpeg', 'image/webp']);

/**
 * INVARIANT 4, restated at this boundary. web/app.js refuses to put anything
 * on the wire whose payload key is one of these; the same list is honoured
 * here so that a raw camera frame cannot reach the enrolment desk either. The
 * only image this panel will send from the camera is the rectified mat crop.
 */
export const RECTIFIED_CROP_KIND = 'rectified_mat_crop';
export const FORBIDDEN_IMAGE_KEYS = Object.freeze([
  'raw', 'rawFrame', 'raw_frame', 'frame', 'fullFrame', 'full_frame',
]);

/** How a call to the shop service can fail without the service being wrong. */
export const ServiceRefusal = Object.freeze({
  NO_FETCH: 'shop_no_fetch_in_this_runtime',
  BAD_BASE: 'shop_base_url_unusable',
  CSP_BLOCKED: 'shop_blocked_by_connect_src',
  UNREACHABLE: 'shop_service_unreachable',
  HTTP: 'shop_service_http_error',
  BAD_JSON: 'shop_service_bad_json',
  BAD_SHAPE: 'shop_service_bad_shape',
  REFUSED: 'shop_service_refused',
  TOTAL_DISAGREES: 'shop_total_disagrees_with_its_own_lines',
});

export const SERVICE_REFUSAL_CODES = Object.freeze(Object.values(ServiceRefusal));

export const SERVICE_REFUSAL_HELP = Object.freeze({
  [ServiceRefusal.NO_FETCH]:
    'this runtime has no fetch(), so the enrolment desk cannot be called at '
    + 'all. Nothing was sent and nothing was guessed.',
  [ServiceRefusal.BAD_BASE]:
    'the configured shop address is not a usable http(s) base. Nothing was '
    + 'dialled — an address that cannot be parsed is not a service that is down.',
  [ServiceRefusal.CSP_BLOCKED]:
    'this page ships connect-src \'self\', so the browser will refuse a call '
    + 'to a different origin before any socket opens. The enrolment desk may be '
    + 'running perfectly; it simply cannot be reached FROM HERE. Open this '
    + 'panel on the enrolment desk\'s own port instead.',
  [ServiceRefusal.UNREACHABLE]:
    'the enrolment desk did not answer. It is probably not running: start '
    + 'tools/upload_app.py. Nothing was stored and nothing was recognised.',
  [ServiceRefusal.HTTP]:
    'the enrolment desk answered with an error status. Its own words are shown '
    + 'below; this panel does not translate a status it did not invent.',
  [ServiceRefusal.BAD_JSON]:
    'the enrolment desk answered with something that is not JSON. A body that '
    + 'cannot be parsed is not a result, so nothing is shown as one.',
  [ServiceRefusal.BAD_SHAPE]:
    'the enrolment desk answered with JSON that does not carry the fields this '
    + 'panel needs. Rendering it would mean inventing the missing ones.',
  [ServiceRefusal.REFUSED]:
    'the enrolment desk refused, by name, and its reason is shown. A refusal '
    + 'is a result: it is the desk declining to guess.',
  [ServiceRefusal.TOTAL_DISAGREES]:
    'the total the desk reported is not the sum of the lines it reported '
    + 'alongside it. This panel will not pick a winner between them, so it '
    + 'shows neither as authoritative and names the disagreement.',
});

/** Why a photo could not be turned into an enrolment request. */
export const TeachRefusal = Object.freeze({
  NO_IMAGE: 'teach_no_image_chosen',
  NO_CAMERA_CROP: 'teach_no_camera_crop',
  RAW_FRAME: 'teach_raw_frame_refused',
  TOO_LARGE: 'teach_image_too_large',
  BAD_TYPE: 'teach_image_type_unsupported',
});

export const TEACH_REFUSAL_CODES = Object.freeze(Object.values(TeachRefusal));

export const TEACH_REFUSAL_HELP = Object.freeze({
  [TeachRefusal.NO_IMAGE]:
    'no photo was chosen. A name and a price with no picture teaches the '
    + 'camera nothing, so this is refused rather than stored as a priced ghost.',
  [TeachRefusal.NO_CAMERA_CROP]:
    'the camera has not delivered a rectified mat crop to this surface yet, so '
    + 'there is nothing to capture. Point the camera at the mat until it locks.',
  [TeachRefusal.RAW_FRAME]:
    'that payload is a RAW CAMERA FRAME, not the rectified mat crop. '
    + 'INVARIANT 4 says only the mat crop survives a frame grab, so it is '
    + 'refused here exactly as web/app.js refuses it on the socket.',
  [TeachRefusal.TOO_LARGE]:
    'that image is larger than the 8 MB the desk will decode. Refused before '
    + 'the upload rather than after it.',
  [TeachRefusal.BAD_TYPE]:
    'that file type is not one the desk decodes (PNG, JPEG or WebP). A HEIC '
    + 'straight off an iPhone lands here; re-save it as JPEG.',
});

/** Why a thumbnail from the desk was not painted. */
export const ThumbRefusal = Object.freeze({
  ABSENT: 'thumb_absent',
  REMOTE_URL: 'thumb_remote_url_refused',
  NOT_AN_IMAGE: 'thumb_not_an_image',
  TOO_LARGE: 'thumb_too_large',
});

export const THUMB_REFUSAL_CODES = Object.freeze(Object.values(ThumbRefusal));

export const THUMB_REFUSAL_HELP = Object.freeze({
  [ThumbRefusal.ABSENT]: 'the desk stored no photo for this SKU.',
  [ThumbRefusal.REMOTE_URL]:
    'that thumbnail is a URL to another host. Painting it would make this page '
    + 'fetch an image from somewhere the operator never named, so it is '
    + 'refused: a thumbnail is an inline data: URI here or it is nothing.',
  [ThumbRefusal.NOT_AN_IMAGE]:
    'that thumbnail is not base64 image data this panel can inline.',
  [ThumbRefusal.TOO_LARGE]:
    'that thumbnail is past the inline cap and was dropped rather than wedged '
    + 'into the DOM.',
});

/**
 * The reason codes gawaah/identity.py attaches when it declines to name an
 * SKU. They are reproduced here as CONSTANTS rather than matched as strings so
 * that a rename on the Python side surfaces as a failing test on this side.
 */
export const RecogniseReason = Object.freeze({
  MATCH: 'match',
  BELOW_MARGIN: 'below_margin',
  BELOW_SIMILARITY: 'below_similarity',
  NO_CANDIDATE: 'no_candidate_in_footprint',
  AMBIGUOUS: 'ambiguous_pair',
});

/** Exactly ABSTAIN_REASONS in gawaah/identity.py, same order. */
export const RECOGNISE_ABSTAIN_CODES = Object.freeze([
  RecogniseReason.BELOW_MARGIN,
  RecogniseReason.BELOW_SIMILARITY,
  RecogniseReason.NO_CANDIDATE,
  RecogniseReason.AMBIGUOUS,
]);

/**
 * What each reason means TO A SHOPKEEPER, and what to do about it. Every one
 * of these is a correct outcome, not an error, and the wording says so —
 * amber is the product working, not the product failing.
 */
export const RECOGNISE_REASON_HELP = Object.freeze({
  [RecogniseReason.MATCH]:
    'the gallery named this item and it is priced from the catalogue.',
  [RecogniseReason.BELOW_MARGIN]:
    'there is a leader, but it does not lead by enough to be trusted. The '
    + 'leader is named below as a SUGGESTION and never as a fact — tap the '
    + 'right one, or teach this packet again from a better angle.',
  [RecogniseReason.BELOW_SIMILARITY]:
    'nothing in the taught catalogue looks like this item. That usually means '
    + 'it has not been taught yet — teach it above and try again.',
  [RecogniseReason.NO_CANDIDATE]:
    'nothing in the catalogue is the right SIZE. The mat measured this item in '
    + 'millimetres and no taught SKU is within the footprint tolerance, so '
    + 'appearance was never even consulted.',
  [RecogniseReason.AMBIGUOUS]:
    'the top two candidates are tied to within numerical noise, so which one '
    + 'sorted first carries no information at all. Both are named below and '
    + 'one of them must be tapped.',
});

/**
 * Translate a reason code, or refuse to. An unknown code gets NO invented
 * prose — the same discipline chilla.js applies to detection reasons, for the
 * same reason: a plausible sentence attached to a code nobody has defined is
 * how a UI starts lying on behalf of a service.
 */
export function recogniseReasonNote(code) {
  const c = typeof code === 'string' ? code : '';
  if (c === '') return 'no reason code was supplied, so there is nothing to explain.';
  if (Object.prototype.hasOwnProperty.call(RECOGNISE_REASON_HELP, c)) {
    return RECOGNISE_REASON_HELP[c];
  }
  return `unrecognised reason code ${JSON.stringify(c)} — this panel will not `
    + 'translate a code it does not know.';
}

// ------------------------------------------------------------- the base URL

/**
 * A typed base -> a usable http(s) origin, or a named refusal.
 *
 * The empty string is VALID and means same-origin. Anything with a scheme that
 * is not http/https is refused: a `javascript:` or `file:` base is not a
 * service address, and quietly ignoring it would leave the panel dialling
 * somewhere the operator did not name.
 */
export function normaliseShopBase(raw) {
  const typed = raw === null || raw === undefined ? '' : String(raw);
  const refuse = (detail) => ({
    ok: false, base: null, reason: ServiceRefusal.BAD_BASE, detail, typed,
  });
  const s = typed.trim();
  if (s === '') {
    return { ok: true, base: SAME_ORIGIN, reason: null, detail: '', typed };
  }
  let u = null;
  try { u = new URL(s); } catch { u = null; }
  if (u === null) {
    return refuse(`${JSON.stringify(typed)} is not a URL. Type a full address `
      + 'like http://127.0.0.1:8790, or leave it empty for this page\'s own origin.');
  }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') {
    return refuse(`the scheme ${JSON.stringify(u.protocol)} is not http or https. `
      + 'An enrolment desk is an HTTP service; nothing else will be dialled.');
  }
  // Keep any path prefix (a reverse proxy may mount the desk under one) but
  // never a trailing slash, so joining a path is plain concatenation.
  const path = u.pathname.replace(/\/+$/, '');
  return { ok: true, base: `${u.origin}${path}`, reason: null, detail: '', typed };
}

/**
 * Where the enrolment desk is, and HOW we decided. The source is carried out
 * with the answer because "which of the four rules won" is the first question
 * anybody asks when a panel is dialling the wrong host.
 */
export function resolveShopBase(opts = {}, loc = null, g = globalThis) {
  const tries = [];
  if (typeof opts.shopBase === 'string') tries.push(['option', opts.shopBase]);
  const search = loc && typeof loc.search === 'string' ? loc.search : '';
  if (search) {
    const m = /[?&]shop=([^&]*)/.exec(search);
    if (m) tries.push(['query', decodeURIComponent(m[1])]);
  }
  if (g && typeof g.GAWAAH_SHOP_BASE === 'string') tries.push(['global', g.GAWAAH_SHOP_BASE]);

  for (const [source, raw] of tries) {
    const v = normaliseShopBase(raw);
    if (!v.ok) return { ok: false, base: null, source, reason: v.reason, detail: v.detail, typed: v.typed };
    return { ok: true, base: v.base, source, reason: null, detail: '', typed: v.typed };
  }

  // Nothing was configured. If this page came off the desk's own port, the
  // honest base is same-origin. Otherwise name the desk's default host so the
  // panel is at least dialling the right place, and let the CSP check below
  // explain why that call will not be allowed to leave.
  const port = loc && typeof loc.port === 'string' ? loc.port : '';
  if (port === String(SHOP_PORT)) {
    return { ok: true, base: SAME_ORIGIN, source: 'same-origin', reason: null, detail: '', typed: '' };
  }
  const host = loc && typeof loc.hostname === 'string' && loc.hostname !== ''
    ? loc.hostname : '127.0.0.1';
  const scheme = loc && loc.protocol === 'https:' ? 'https:' : 'http:';
  return {
    ok: true,
    base: `${scheme}//${host}:${SHOP_PORT}`,
    source: 'default',
    reason: null,
    detail: '',
    typed: '',
  };
}

/**
 * Will the browser let us dial this base from this page?
 *
 * This is a CSP question, not a network one, and it is answered BEFORE the
 * call — because the browser's own answer arrives as a bare TypeError that is
 * indistinguishable from a dead service, and telling an operator to "start the
 * service" when the service is already running is the worst possible advice.
 */
export function describeReach(base, loc = null, csp = 'self') {
  if (base === SAME_ORIGIN) {
    return { sameOrigin: true, blocked: false, reason: null, detail: '' };
  }
  const pageOrigin = loc && typeof loc.origin === 'string' ? loc.origin : '';
  let baseOrigin = '';
  try { baseOrigin = new URL(base).origin; } catch { baseOrigin = ''; }
  if (pageOrigin === '' || baseOrigin === '') {
    // No page origin (a file:// load, or a shim) — we cannot prove it is
    // blocked, so we do not claim it is. It will fail honestly if it fails.
    return { sameOrigin: false, blocked: false, reason: null, detail: '' };
  }
  if (pageOrigin === baseOrigin) {
    return { sameOrigin: true, blocked: false, reason: null, detail: '' };
  }
  if (csp !== 'self') {
    return { sameOrigin: false, blocked: false, reason: null, detail: '' };
  }
  return {
    sameOrigin: false,
    blocked: true,
    reason: ServiceRefusal.CSP_BLOCKED,
    detail: `this page was served from ${pageOrigin} and ships `
      + 'connect-src \'self\', so the browser will not allow a call to '
      + `${baseOrigin}. This is a permission, not an outage: the desk may be up. `
      + `Open this panel from ${baseOrigin} instead, where the call is `
      + 'same-origin and allowed.',
  };
}

// -------------------------------------------------------------- the request

/**
 * The canonical rupee string for an integer paise value: '21450' -> '214.50'.
 *
 * This is what goes on the wire, NOT the characters the shopkeeper typed. The
 * typed string has already been proved to mean exactly this many paise;
 * re-deriving the rupees from the integer means the browser and the desk
 * cannot disagree about what ' 214.5 ' meant. Still pure string surgery: pad
 * to three digits, cut the last two off. No division, no toFixed.
 */
export function paiseToRupeeString(paise) {
  if (!Number.isInteger(paise) || paise < 0) return null;
  const s = String(paise).padStart(3, '0');
  return `${s.slice(0, -2)}.${s.slice(-2)}`;
}

/** A validated name -> the id the desk keys the SKU under. */
export function skuIdFor(name) {
  const v = parseName(name);
  if (!v.ok) return { ok: false, skuId: null, reason: v.reason, detail: v.detail };
  // Unicode letters and digits SURVIVE. parseName deliberately accepts
  // Devanagari, Tamil and Bengali names, and an ASCII-only slug would turn
  // 'चाय' into the empty string — which is to say it would refuse exactly the
  // names an Indian kirana would actually type.
  const slug = v.name
    .toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/-{2,}/g, '-')
    .replace(/^[-._]+/, '')
    .replace(/[-._]+$/, '');
  if (slug === '') {
    return {
      ok: false,
      skuId: null,
      reason: NameRefusal.BAD_CHARS,
      detail: `${JSON.stringify(v.name)} leaves nothing behind once it is turned `
        + 'into an id. Give the SKU a name with a letter or a digit in it.',
    };
  }
  return { ok: true, skuId: slug, reason: null, detail: '' };
}

/**
 * Check an image choice before it is uploaded. Returns the descriptor or a
 * named refusal; never throws, never mutates.
 *
 * `image` is one of
 *   { kind: 'file', name, size, type }            a file the operator picked
 *   { kind: 'rectified_mat_crop', b64, bytes }    the live crop, INVARIANT 4
 */
export function checkImage(image) {
  const refuse = (reason, extra = '') => ({
    ok: false,
    image: null,
    reason,
    detail: (TEACH_REFUSAL_HELP[reason] || reason) + (extra ? ` ${extra}` : ''),
  });
  if (!image || typeof image !== 'object') return refuse(TeachRefusal.NO_IMAGE);

  // INVARIANT 4 first, before anything else is looked at. A payload carrying a
  // raw-frame key is refused even if it is small and well-typed.
  for (const k of FORBIDDEN_IMAGE_KEYS) {
    if (Object.prototype.hasOwnProperty.call(image, k)) {
      return refuse(TeachRefusal.RAW_FRAME, `The offending key is ${JSON.stringify(k)}.`);
    }
  }

  if (image.kind === RECTIFIED_CROP_KIND) {
    const b64 = typeof image.b64 === 'string' ? image.b64 : '';
    if (b64 === '') return refuse(TeachRefusal.NO_CAMERA_CROP);
    if (b64.length > MAX_THUMB_CHARS * 8) return refuse(TeachRefusal.TOO_LARGE);
    return {
      ok: true,
      image: Object.freeze({
        kind: RECTIFIED_CROP_KIND,
        b64,
        label: 'the live rectified mat crop',
        type: 'image/png',
      }),
      reason: null,
      detail: '',
    };
  }

  if (image.kind === 'file') {
    const type = typeof image.type === 'string' ? image.type.toLowerCase() : '';
    const size = Number.isFinite(image.size) ? image.size : null;
    if (size !== null && size > MAX_IMAGE_BYTES) {
      return refuse(TeachRefusal.TOO_LARGE, `That file is ${size} bytes.`);
    }
    // An empty type happens on some platforms for a legitimate file; the desk
    // sniffs the bytes anyway, so an unknown type is allowed through and only
    // a KNOWN-WRONG type is refused. Refusing the unknown case here would
    // block real photos on the strength of a missing header.
    if (type !== '' && !IMAGE_TYPES.includes(type)) {
      return refuse(TeachRefusal.BAD_TYPE, `That file is ${JSON.stringify(type)}.`);
    }
    return {
      ok: true,
      image: Object.freeze({
        kind: 'file',
        name: typeof image.name === 'string' ? image.name : 'photo',
        size,
        type: type || 'image/*',
        label: typeof image.name === 'string' ? image.name : 'the chosen photo',
      }),
      reason: null,
      detail: '',
    };
  }

  return refuse(TeachRefusal.NO_IMAGE);
}

/**
 * THE ENROLMENT REQUEST, built and validated with no network in sight.
 *
 * Order matters and is deliberate: NAME, then PRICE, then IMAGE. The price is
 * the dangerous field, so it is proved before an 8 MB upload is contemplated,
 * and the refusal an operator sees names the FIRST thing that was wrong rather
 * than the last.
 *
 * The paise are returned alongside the request so the surface can show the
 * integer that will be stored — the whole point of the exercise.
 */
export function buildEnrolRequest(input = {}) {
  const base = typeof input.base === 'string' ? input.base : SAME_ORIGIN;
  const fail = (field, reason, detail, typed) => ({
    ok: false,
    request: null,
    paise: null,
    rupees: null,
    refusal: Object.freeze({ field, reason, detail, typed: typed ?? '' }),
  });

  const nameV = parseName(input.name, Array.isArray(input.taken) ? input.taken : []);
  if (!nameV.ok) return fail('name', nameV.reason, nameV.detail, nameV.typed);

  const idV = skuIdFor(nameV.name);
  if (!idV.ok) return fail('name', idV.reason, idV.detail, nameV.typed);

  const priceV = parsePaise(input.price);
  if (!priceV.ok) return fail('price', priceV.reason, priceV.detail, priceV.typed);

  const imgV = checkImage(input.image);
  if (!imgV.ok) return fail('image', imgV.reason, imgV.detail, '');

  const rupees = paiseToRupeeString(priceV.paise);
  return {
    ok: true,
    request: Object.freeze({
      method: 'POST',
      path: ShopPath.ENROL,
      url: `${base}${ShopPath.ENROL}`,
      fields: Object.freeze({
        sku_id: idV.skuId,
        name: nameV.name,
        // The desk's documented field is price_rupees. The integer travels
        // beside it because the integer is the value and the string is a
        // rendering of it; a desk that reads either one gets the same answer,
        // and a desk that reads both can prove they agree.
        price_rupees: rupees,
        price_paise: String(priceV.paise),
      }),
      image: imgV.image,
    }),
    paise: priceV.paise,
    rupees,
    refusal: null,
  };
}

/** The recognise request. One field: the photo. */
export function buildRecogniseRequest(input = {}) {
  const base = typeof input.base === 'string' ? input.base : SAME_ORIGIN;
  const imgV = checkImage(input.image);
  if (!imgV.ok) {
    return {
      ok: false,
      request: null,
      refusal: Object.freeze({ field: 'image', reason: imgV.reason, detail: imgV.detail, typed: '' }),
    };
  }
  return {
    ok: true,
    request: Object.freeze({
      method: 'POST',
      path: ShopPath.RECOGNISE,
      url: `${base}${ShopPath.RECOGNISE}`,
      fields: Object.freeze({}),
      image: imgV.image,
    }),
    refusal: null,
  };
}

/**
 * DELETE /shop/{sku_id}. The id is percent-encoded, which is not decoration:
 * skuIdFor keeps Unicode, so 'चाय' is a perfectly ordinary id here and a raw
 * one in a URL would be mangled or rejected.
 */
export function buildRemoveRequest(skuId, base = SAME_ORIGIN) {
  const id = typeof skuId === 'string' ? skuId.trim() : '';
  if (id === '') {
    return {
      ok: false,
      request: null,
      refusal: Object.freeze({
        field: 'row', reason: 'sku_unknown_row',
        detail: 'no SKU id was given, so nothing was removed.', typed: '',
      }),
    };
  }
  const path = `${ShopPath.SHOP}/${encodeURIComponent(id)}`;
  return {
    ok: true,
    request: Object.freeze({
      method: 'DELETE', path, url: `${base}${path}`, fields: Object.freeze({}), image: null,
    }),
    refusal: null,
  };
}

// ------------------------------------------------------------- the response

/**
 * An HTTP answer -> data or a named refusal. PURE, so every failure mode the
 * desk can produce is unit-tested without a socket: a 500, an HTML error page,
 * a body that is valid JSON but the wrong shape, and the desk's own
 * `{"ok": false, "reason": ...}` refusal, which is a RESULT and not a fault.
 */
export function readShopResponse(status, bodyText) {
  const text = typeof bodyText === 'string' ? bodyText : '';
  const refuse = (reason, detail) => ({
    ok: false, data: null,
    refusal: Object.freeze({ reason, detail, status: Number.isFinite(status) ? status : null }),
  });

  let data = null;
  try { data = JSON.parse(text); } catch { data = null; }

  if (data === null || typeof data !== 'object' || Array.isArray(data)) {
    if (!Number.isFinite(status) || status < 200 || status >= 300) {
      return refuse(ServiceRefusal.HTTP,
        `${SERVICE_REFUSAL_HELP[ServiceRefusal.HTTP]} It said: HTTP ${status} `
        + `${JSON.stringify(text.slice(0, 200))}`);
    }
    return refuse(ServiceRefusal.BAD_JSON,
      `${SERVICE_REFUSAL_HELP[ServiceRefusal.BAD_JSON]} The first 200 characters `
      + `were ${JSON.stringify(text.slice(0, 200))}.`);
  }

  // The desk's own named refusal. It parsed, it is well-formed, and it says no.
  const deskReason = typeof data.reason === 'string' ? data.reason : '';
  if (data.ok === false || (!Number.isFinite(status) || status < 200 || status >= 300)) {
    const detail = typeof data.detail === 'string' && data.detail !== ''
      ? data.detail
      : SERVICE_REFUSAL_HELP[ServiceRefusal.REFUSED];
    return {
      ok: false,
      data,
      refusal: Object.freeze({
        reason: deskReason || ServiceRefusal.REFUSED,
        detail,
        status: Number.isFinite(status) ? status : null,
      }),
    };
  }
  return { ok: true, data, refusal: null };
}

/** Strip a `data:image/...;base64,` prefix if one is present. Idempotent. */
export function stripDataUri(s) {
  const v = typeof s === 'string' ? s : '';
  const i = v.indexOf('base64,');
  return i === -1 ? v : v.slice(i + 'base64,'.length);
}

/**
 * base64 -> Blob, for uploading the live rectified crop. Returns null rather
 * than throwing when the runtime has no atob or no Blob, so a missing global
 * becomes a named refusal upstream instead of an exception mid-render.
 */
export function b64ToBlob(b64, type = 'image/png', g = globalThis) {
  const raw = stripDataUri(b64);
  if (raw === '') return null;
  const atobFn = g && typeof g.atob === 'function' ? g.atob : null;
  const B = g && typeof g.Blob === 'function' ? g.Blob : null;
  if (atobFn === null || B === null) return null;
  let bin = '';
  try { bin = atobFn(raw); } catch { return null; }
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) arr[i] = bin.charCodeAt(i);
  return new B([arr], { type });
}

/**
 * The one impure function in this section: it calls fetch. Everything it
 * decides, it decides in readShopResponse above. It NEVER throws — a thrown
 * fetch becomes a named `shop_service_unreachable`, because a panel that
 * throws on a dead service renders nothing at all, and a blank panel is the
 * one outcome this file exists to prevent.
 */
export async function callShop(request, deps = {}) {
  const fetchFn = typeof deps.fetch === 'function' ? deps.fetch : null;
  const FD = deps.FormData || (typeof FormData !== 'undefined' ? FormData : null);
  const fail = (reason, detail) => ({
    ok: false, data: null, refusal: Object.freeze({ reason, detail, status: null }),
  });
  if (fetchFn === null) {
    return fail(ServiceRefusal.NO_FETCH, SERVICE_REFUSAL_HELP[ServiceRefusal.NO_FETCH]);
  }
  if (!request || typeof request.url !== 'string') {
    return fail(ServiceRefusal.BAD_BASE, SERVICE_REFUSAL_HELP[ServiceRefusal.BAD_BASE]);
  }

  const init = { method: request.method };
  if (request.image || (request.fields && Object.keys(request.fields).length > 0)) {
    if (FD === null) {
      return fail(ServiceRefusal.NO_FETCH,
        'this runtime has no FormData, so a multipart upload cannot be built.');
    }
    const form = new FD();
    for (const [k, v] of Object.entries(request.fields || {})) form.append(k, v);
    if (request.image) {
      const blob = typeof deps.toBlob === 'function' ? await deps.toBlob(request.image) : null;
      if (blob === null) {
        return fail(ServiceRefusal.BAD_SHAPE,
          'the chosen image could not be turned into an upload body.');
      }
      form.append('image', blob, request.image.name || 'photo.png');
    }
    init.body = form;
  }

  let res = null;
  try {
    res = await fetchFn(request.url, init);
  } catch (e) {
    return fail(ServiceRefusal.UNREACHABLE,
      `${SERVICE_REFUSAL_HELP[ServiceRefusal.UNREACHABLE]} The browser said: `
      + `${(e && e.message) || String(e)}.`);
  }
  let text = '';
  try { text = await res.text(); } catch { text = ''; }
  return readShopResponse(res.status, text);
}

// ===========================================================================
// 9. THE PHOTO VIEW MODEL. Pure: a desk payload in, a plain object out.
// ===========================================================================

/**
 * The abstentions of the PHOTO flow. Deliberately a separate namespace from
 * `Abstain` in section 3: those are things the BRAIN has not said, these are
 * things the DESK has not said, and a panel that merges them tells an operator
 * to restart the wrong process.
 */
export const PhotoAbstain = Object.freeze({
  NO_SERVICE: 'photo_desk_not_reached',
  NO_IMAGE: 'photo_no_image_chosen',
  NO_CAMERA_CROP: 'photo_no_camera_crop',
  CATALOG_UNKNOWN: 'photo_catalogue_not_fetched',
  NOTHING_TRIED: 'photo_nothing_tried_yet',
});

export const PHOTO_ABSTAIN_CODES = Object.freeze(Object.values(PhotoAbstain));

export const PHOTO_ABSTAIN_HELP = Object.freeze({
  [PhotoAbstain.NO_SERVICE]:
    'the enrolment desk has not answered this surface, so nothing is known '
    + 'about what has been taught. This is not an empty catalogue; it is an '
    + 'unknown one, and the two are different.',
  [PhotoAbstain.NO_IMAGE]:
    'no photo has been chosen, so there is nothing to teach the camera with.',
  [PhotoAbstain.NO_CAMERA_CROP]:
    'the camera has not delivered a rectified mat crop here, so "use the '
    + 'camera" has nothing to capture and says so instead of capturing black.',
  [PhotoAbstain.CATALOG_UNKNOWN]:
    'the catalogue has not been fetched from the desk yet. Nothing is claimed '
    + 'about how many SKUs exist.',
  [PhotoAbstain.NOTHING_TRIED]:
    'no photo has been put through TRY IT, so the counter has not been asked '
    + 'to recognise anything and has no opinion to report.',
});

/** A measurement in millimetres -> text, or an honest "not measured". */
export function formatMm(v) {
  // Millimetres are a MEASUREMENT, not money. gawaah/identity.py says so in as
  // many words ("nothing here touches money, so plain floats are correct"), and
  // tools/lint_no_float.py covers the money path only. Rendering a length with
  // two decimals is therefore correct here and would be a bug two sections up.
  if (typeof v !== 'number' || !Number.isFinite(v) || v <= 0) return null;
  return `${v.toFixed(2)} mm`;
}

/** A cosine score -> text, or null. Three decimals: theta is 0.10. */
export function formatScore(v) {
  if (typeof v !== 'number' || !Number.isFinite(v)) return null;
  return v.toFixed(3);
}

const B64_RE = /^[A-Za-z0-9+/]+={0,2}$/;
const DATA_URI_RE = /^data:image\/(png|jpeg|webp);base64,([A-Za-z0-9+/]+={0,2})$/;

/**
 * A thumbnail from the desk -> something safe to put in an <img src>, or a
 * named refusal.
 *
 * The refusal that matters is REMOTE_URL. A catalogue row is desk-controlled
 * data, and an `<img src="http://elsewhere/x.png">` built from it would make
 * this page issue a request to a host nobody named — a tracking pixel with
 * extra steps, and a hole in the same privacy story INVARIANT 4 is defending.
 * A thumbnail here is inline bytes or it is nothing.
 */
export function safeThumb(v) {
  if (v === null || v === undefined || v === '') {
    return { ok: false, src: null, reason: ThumbRefusal.ABSENT, detail: THUMB_REFUSAL_HELP[ThumbRefusal.ABSENT] };
  }
  if (typeof v !== 'string') {
    return { ok: false, src: null, reason: ThumbRefusal.NOT_AN_IMAGE, detail: THUMB_REFUSAL_HELP[ThumbRefusal.NOT_AN_IMAGE] };
  }
  const s = v.trim();
  if (s.length > MAX_THUMB_CHARS) {
    return { ok: false, src: null, reason: ThumbRefusal.TOO_LARGE, detail: THUMB_REFUSAL_HELP[ThumbRefusal.TOO_LARGE] };
  }
  const isProtocolRelative = /^\/\//.test(s);
  const hasScheme = /^[a-z][a-z0-9+.-]*:/i.test(s);
  if (isProtocolRelative || (hasScheme && !s.startsWith('data:'))) {
    return { ok: false, src: null, reason: ThumbRefusal.REMOTE_URL, detail: THUMB_REFUSAL_HELP[ThumbRefusal.REMOTE_URL] };
  }
  if (s.startsWith('data:')) {
    if (!DATA_URI_RE.test(s)) {
      return { ok: false, src: null, reason: ThumbRefusal.NOT_AN_IMAGE, detail: THUMB_REFUSAL_HELP[ThumbRefusal.NOT_AN_IMAGE] };
    }
    return { ok: true, src: s, reason: null, detail: '' };
  }
  // Bare base64, which is what a JSON field called photo_png most naturally
  // holds. Wrapped, never guessed at: the charset is checked first.
  if (s.length > 16 && B64_RE.test(s)) {
    return { ok: true, src: `data:image/png;base64,${s}`, reason: null, detail: '' };
  }
  return { ok: false, src: null, reason: ThumbRefusal.NOT_AN_IMAGE, detail: THUMB_REFUSAL_HELP[ThumbRefusal.NOT_AN_IMAGE] };
}

/** Pull an integer paise value out of desk JSON, or refuse to call it money. */
function readPaise(v) {
  if (Number.isInteger(v) && Number.isSafeInteger(v) && v >= 0) {
    return { ok: true, paise: v, reason: null };
  }
  if (v === null || v === undefined) return { ok: false, paise: null, reason: 'price_absent' };
  return { ok: false, paise: null, reason: 'price_not_integer_paise' };
}

/**
 * GET /shop -> the catalogue view model.
 *
 * `payload === null` means NOT FETCHED, which is not the same as EMPTY, and
 * the two produce different screens: unknown abstains, empty says "nothing has
 * been taught yet" and offers the form.
 */
export function deriveShopCatalog(payload, refusal = null) {
  if (refusal) {
    return Object.freeze({
      known: false, rows: Object.freeze([]), count: 0,
      refusal: Object.freeze({ ...refusal }),
    });
  }
  if (payload === null || payload === undefined) {
    return Object.freeze({ known: false, rows: Object.freeze([]), count: 0, refusal: null });
  }
  const raw = Array.isArray(payload) ? payload
    : (Array.isArray(payload.skus) ? payload.skus
      : (Array.isArray(payload.catalog) ? payload.catalog : null));
  if (raw === null) {
    return Object.freeze({
      known: false, rows: Object.freeze([]), count: 0,
      refusal: Object.freeze({
        reason: ServiceRefusal.BAD_SHAPE,
        detail: `${SERVICE_REFUSAL_HELP[ServiceRefusal.BAD_SHAPE]} A catalogue `
          + 'needs a `skus` array; this answer has none.',
        status: null,
      }),
    });
  }
  const rows = raw.map((s, i) => {
    const rec = s && typeof s === 'object' ? s : {};
    const money = readPaise(rec.price_paise);
    const thumb = safeThumb(rec.photo_png ?? rec.thumb ?? rec.thumbnail ?? null);
    const mm = typeof rec.footprint_mm === 'number' ? rec.footprint_mm : null;
    return Object.freeze({
      skuId: typeof rec.sku_id === 'string' ? rec.sku_id : `row-${i}`,
      name: typeof rec.name === 'string' && rec.name !== '' ? rec.name : '(unnamed)',
      pricePaise: money.paise,
      priceOk: money.ok,
      priceReason: money.reason,
      rupees: money.ok ? formatPaise(money.paise) : null,
      paiseText: money.ok ? `${money.paise} paise` : null,
      footprintMm: mm,
      footprintText: formatMm(mm) ?? 'not measured',
      thumbSrc: thumb.ok ? thumb.src : null,
      thumbReason: thumb.ok ? null : thumb.reason,
      thumbDetail: thumb.ok ? '' : thumb.detail,
    });
  });
  return Object.freeze({
    known: true,
    rows: Object.freeze(rows),
    count: rows.length,
    refusal: null,
  });
}

/**
 * POST /recognise -> the TRY IT view model.
 *
 * THE TOTAL, AND WHY IT IS CHECKED RATHER THAN COMPUTED
 * INVARIANT 2 says this panel does not settle money, and section 5 says it
 * does not total the catalogue. Neither is contradicted here. The desk reports
 * a total; this function ADDS UP THE LINES THE DESK SENT WITH IT and compares.
 * That is an audit, not a bill — and it is integer addition of integer paise,
 * with no multiplication, no division and no float anywhere near it.
 *
 * If the two disagree the panel shows NEITHER as authoritative and names
 * `shop_total_disagrees_with_its_own_lines`. A total that does not match its
 * own lines is precisely the bug that a trusting UI hides for a year.
 *
 * An item the gallery cannot name is AMBER, carries its reason, and is
 * EXCLUDED from the sum. That exclusion is the product.
 */
export function deriveRecognition(payload, refusal = null) {
  const empty = {
    known: false, items: Object.freeze([]), priced: 0, amber: 0,
    totalPaise: null, totalRupees: null, serverTotalPaise: null,
    totalAgrees: true, refusal: null, note: '',
  };
  if (refusal) return Object.freeze({ ...empty, refusal: Object.freeze({ ...refusal }) });
  if (payload === null || payload === undefined) return Object.freeze(empty);

  const raw = Array.isArray(payload) ? payload
    : (Array.isArray(payload.items) ? payload.items : null);
  if (raw === null) {
    return Object.freeze({
      ...empty,
      refusal: Object.freeze({
        reason: ServiceRefusal.BAD_SHAPE,
        detail: `${SERVICE_REFUSAL_HELP[ServiceRefusal.BAD_SHAPE]} A recognition `
          + 'needs an `items` array; this answer has none.',
        status: null,
      }),
    });
  }

  let sum = 0;
  let priced = 0;
  let amber = 0;
  const items = raw.map((it, i) => {
    const rec = it && typeof it === 'object' ? it : {};
    const sku = typeof rec.sku_id === 'string' && rec.sku_id !== '' ? rec.sku_id : null;
    const reason = typeof rec.reason === 'string' ? rec.reason : '';
    const money = readPaise(rec.price_paise);
    // AMBER is decided by the ABSENCE of a usable answer, not by the presence
    // of the string 'match'. A desk that returns a sku with no price, or a
    // 'match' with a price that is not an integer, lands amber — never billed.
    const isAmber = sku === null || !money.ok || reason !== RecogniseReason.MATCH;
    if (isAmber) {
      amber += 1;
    } else {
      priced += 1;
      sum = sum + money.paise;
    }
    const mm = typeof rec.long_edge_mm === 'number' ? rec.long_edge_mm
      : (typeof rec.footprint_mm === 'number' ? rec.footprint_mm : null);
    return Object.freeze({
      index: i,
      skuId: sku,
      name: typeof rec.name === 'string' && rec.name !== '' ? rec.name : (sku || '(unnamed)'),
      pricePaise: money.ok ? money.paise : null,
      rupees: money.ok ? formatPaise(money.paise) : null,
      paiseText: money.ok ? `${money.paise} paise` : null,
      priceReason: money.ok ? null : money.reason,
      amber: isAmber,
      reason: reason || (isAmber ? 'no_reason_given' : RecogniseReason.MATCH),
      reasonNote: recogniseReasonNote(reason),
      top1: formatScore(rec.top1),
      top2: formatScore(rec.top2),
      margin: formatScore(rec.margin),
      runnerUp: typeof rec.top2_sku === 'string' ? rec.top2_sku : null,
      longEdgeMm: mm,
      footprintText: formatMm(mm) ?? 'not measured',
    });
  });

  const server = readPaise(payload && payload.total_paise);
  const agrees = !server.ok || server.paise === sum;

  return Object.freeze({
    known: true,
    items: Object.freeze(items),
    priced,
    amber,
    totalPaise: agrees ? sum : null,
    totalRupees: agrees ? formatPaise(sum) : null,
    serverTotalPaise: server.ok ? server.paise : null,
    totalAgrees: agrees,
    refusal: agrees ? null : Object.freeze({
      reason: ServiceRefusal.TOTAL_DISAGREES,
      detail: `${SERVICE_REFUSAL_HELP[ServiceRefusal.TOTAL_DISAGREES]} The desk `
        + `reported ${server.paise} paise; its own priced lines add to ${sum} paise.`,
      status: null,
    }),
    note: `${priced} priced, ${amber} amber and EXCLUDED from the total.`,
  });
}

/**
 * Fold everything the photo flow knows into one frozen object. Its own
 * abstention list, separate from the brain's.
 */
export function derivePhoto(input = {}) {
  const baseR = input.baseResolution && typeof input.baseResolution === 'object'
    ? input.baseResolution
    : { ok: true, base: SAME_ORIGIN, source: 'default', reason: null, detail: '' };
  const reach = input.reach && typeof input.reach === 'object'
    ? input.reach
    : { sameOrigin: true, blocked: false, reason: null, detail: '' };

  const catalog = deriveShopCatalog(
    input.shop === undefined ? null : input.shop,
    input.shopRefusal || null,
  );
  const recognition = deriveRecognition(
    input.recognition === undefined ? null : input.recognition,
    input.recogniseRefusal || null,
  );

  const priceV = parsePaise(input.typedPrice === undefined ? '' : input.typedPrice);
  const imageV = checkImage(input.image);
  const cameraCrop = typeof input.cameraCrop === 'string' && input.cameraCrop !== ''
    ? input.cameraCrop : null;

  const abstentions = [];
  if (!catalog.known && catalog.refusal === null) abstentions.push(PhotoAbstain.CATALOG_UNKNOWN);
  if (catalog.refusal !== null || baseR.ok === false || reach.blocked) {
    abstentions.push(PhotoAbstain.NO_SERVICE);
  }
  if (!imageV.ok) abstentions.push(PhotoAbstain.NO_IMAGE);
  if (cameraCrop === null) abstentions.push(PhotoAbstain.NO_CAMERA_CROP);
  if (!recognition.known && recognition.refusal === null) abstentions.push(PhotoAbstain.NOTHING_TRIED);

  return Object.freeze({
    base: baseR.ok ? baseR.base : null,
    baseTyped: typeof baseR.typed === 'string' ? baseR.typed : '',
    baseSource: typeof baseR.source === 'string' ? baseR.source : 'default',
    baseOk: baseR.ok !== false,
    baseDetail: typeof baseR.detail === 'string' ? baseR.detail : '',
    baseReason: baseR.reason || null,
    reach: Object.freeze({ ...reach }),
    // The whole point of section 1, surfaced live: the integer that WILL be
    // stored, shown before the button is pressed.
    preview: Object.freeze({
      typed: priceV.typed,
      ok: priceV.ok,
      paise: priceV.paise,
      paiseText: priceV.ok ? `${priceV.paise} paise` : null,
      rupees: priceV.ok ? formatPaise(priceV.paise) : null,
      wire: priceV.ok ? paiseToRupeeString(priceV.paise) : null,
      reason: priceV.reason,
      detail: priceV.detail,
    }),
    image: imageV.ok ? imageV.image : null,
    imageReason: imageV.ok ? null : imageV.reason,
    imageDetail: imageV.ok ? '' : imageV.detail,
    cameraCrop,
    catalog,
    recognition,
    busy: typeof input.busy === 'string' ? input.busy : '',
    abstentions: Object.freeze(abstentions),
  });
}

// ===========================================================================
// 10. THE PHOTO RENDER. Same discipline as section 5: `doc` is a parameter.
// ===========================================================================

export const TEACH_NOTE =
  'TEACH — the photo, the name and the price go to the enrolment desk, which '
  + 'locks the mat, measures the item in millimetres and stores a descriptor of '
  + 'it. The price is converted to INTEGER PAISE in this browser and refused if '
  + 'it is not exact.';

export const TRY_NOTE =
  'TRY IT — this is a READING, not a bill. An item the gallery cannot name is '
  + 'AMBER with its reason and is EXCLUDED from the total. Nothing on this '
  + 'surface settles money: only a signature-verified webhook turns this '
  + 'counter green.';

export const CROSS_ORIGIN_FIX =
  'open this panel from the enrolment desk itself';

function photoAbstain(doc, code, extraClass = '') {
  return mk(doc, 'div', {
    class: `abstain enrol-abstain ${extraClass}`.trim(),
    data: { reason: code },
    kids: [
      mk(doc, 'div', { class: 'abstain-tag enrol-abstain-tag', text: 'I DO NOT KNOW' }),
      mk(doc, 'p', { text: PHOTO_ABSTAIN_HELP[code] || code }),
      mk(doc, 'code', { class: 'abstain-why enrol-abstain-why', text: code }),
    ],
  });
}

function serviceRefusalBlock(doc, refusal, tag = 'THE DESK DID NOT ANSWER') {
  if (!refusal) return null;
  return mk(doc, 'div', {
    class: 'abstain line-amber enrol-desk-refused',
    data: { reason: refusal.reason, status: String(refusal.status ?? '') },
    attrs: { role: 'alert' },
    kids: [
      mk(doc, 'div', { class: 'abstain-tag enrol-refusal-tag', text: tag }),
      mk(doc, 'p', { class: 'enrol-desk-detail', text: refusal.detail || '' }),
      mk(doc, 'code', { class: 'abstain-why enrol-code', text: refusal.reason }),
    ],
  });
}

/** Where the desk is, how we decided, and whether we are allowed to dial it. */
function renderService(model, doc, handlers) {
  const p = model.photo;
  const onBase = typeof handlers.onBase === 'function' ? handlers.onBase : () => {};
  const baseInput = mk(doc, 'input', {
    class: 'enrol-input enrol-input-base',
    attrs: {
      type: 'text', id: 'enrol-base', placeholder: 'http://127.0.0.1:8790 (empty = this origin)',
    },
  });
  baseInput.value = p.baseOk ? (p.base ?? '') : p.baseTyped;

  return mk(doc, 'div', {
    class: 'enrol-service',
    data: { base: p.base ?? '', source: p.baseSource, blocked: String(p.reach.blocked) },
    kids: [
      mk(doc, 'h3', { class: 'orient-key enrol-h3', text: 'the enrolment desk' }),
      mk(doc, 'label', { class: 'orient-key enrol-label', attrs: { for: 'enrol-base' }, text: 'desk address' }),
      baseInput,
      mk(doc, 'button', {
        class: 'btn enrol-btn enrol-btn-base',
        text: 'USE THIS DESK',
        attrs: { type: 'button', id: 'enrol-set-base' },
        on: { click: () => onBase(baseInput.value) },
      }),
      mk(doc, 'p', {
        class: 'reason enrol-hint',
        text: p.baseOk
          ? `dialling ${p.base === SAME_ORIGIN ? 'this page\'s own origin' : p.base}`
            + ` (chosen by: ${p.baseSource}).`
          : 'no usable desk address.',
      }),
      p.baseOk ? null : mk(doc, 'div', {
        class: 'abstain line-amber enrol-base-bad',
        data: { reason: p.baseReason },
        attrs: { role: 'alert' },
        kids: [
          mk(doc, 'div', { class: 'abstain-tag enrol-refusal-tag', text: 'REFUSED' }),
          mk(doc, 'p', { text: p.baseDetail }),
          mk(doc, 'code', { class: 'abstain-why enrol-code', text: p.baseReason }),
        ],
      }),
      p.reach.blocked ? mk(doc, 'div', {
        class: 'abstain line-amber enrol-csp',
        data: { reason: p.reach.reason },
        attrs: { role: 'alert' },
        kids: [
          mk(doc, 'div', { class: 'abstain-tag enrol-refusal-tag', text: 'BLOCKED BY THIS PAGE' }),
          mk(doc, 'p', { class: 'enrol-csp-detail', text: p.reach.detail }),
          mk(doc, 'p', { class: 'reason enrol-hint', text: `the fix is to ${CROSS_ORIGIN_FIX}.` }),
          mk(doc, 'code', { class: 'abstain-why enrol-code', text: p.reach.reason }),
        ],
      }) : null,
    ],
  });
}

/** 1. TEACH: a photo or the live crop, a name, a price, and the paise preview. */
function renderTeach(model, doc, handlers) {
  const p = model.photo;
  const onPick = typeof handlers.onPick === 'function' ? handlers.onPick : () => {};
  const onCapture = typeof handlers.onCapture === 'function' ? handlers.onCapture : () => {};
  const onTeach = typeof handlers.onTeach === 'function' ? handlers.onTeach : () => {};
  const onPriceInput = typeof handlers.onPriceInput === 'function' ? handlers.onPriceInput : () => {};

  const file = mk(doc, 'input', {
    class: 'enrol-input enrol-input-photo',
    attrs: { type: 'file', id: 'enrol-photo', accept: IMAGE_TYPES.join(',') },
    on: { change: (ev) => onPick(ev) },
  });
  const name = mk(doc, 'input', {
    class: 'enrol-input enrol-input-teach-name',
    attrs: { type: 'text', id: 'enrol-teach-name', placeholder: 'product name, e.g. Parle-G 100g', maxlength: String(MAX_NAME_LEN) },
  });
  const price = mk(doc, 'input', {
    class: 'enrol-input enrol-input-teach-price',
    attrs: { type: 'text', id: 'enrol-teach-price', placeholder: 'price in RUPEES, e.g. 214.50', inputmode: 'decimal' },
    on: { input: () => onPriceInput(price.value) },
  });
  price.value = p.preview.typed;

  // The paise preview. When the typed price is good this shows the INTEGER
  // that will be stored; when it is bad it shows the named refusal, live,
  // before anything is uploaded.
  const preview = p.preview.ok
    ? mk(doc, 'p', {
      class: 'enrol-preview enrol-preview-ok',
      data: { paise: String(p.preview.paise) },
      kids: [
        mk(doc, 'span', { text: 'will be stored as ' }),
        mk(doc, 'code', { class: 'enrol-preview-paise', text: p.preview.paiseText }),
        mk(doc, 'span', { text: ' = ' }),
        mk(doc, 'span', { class: 'line-price num enrol-preview-rupees', text: p.preview.rupees }),
        mk(doc, 'span', { text: ` — sent on the wire as ${p.preview.wire}` }),
      ],
    })
    : mk(doc, 'div', {
      class: 'abstain line-amber enrol-preview enrol-preview-bad',
      data: { reason: p.preview.reason },
      attrs: { role: 'status' },
      kids: [
        mk(doc, 'div', { class: 'abstain-tag enrol-refusal-tag', text: 'NO PAISE YET' }),
        mk(doc, 'p', { class: 'enrol-preview-detail', text: p.preview.detail }),
        mk(doc, 'code', { class: 'abstain-why enrol-code', text: p.preview.reason }),
      ],
    });

  return mk(doc, 'div', {
    class: 'enrol-teach',
    data: {
      hasimage: String(p.image !== null),
      hascrop: String(p.cameraCrop !== null),
      busy: p.busy,
    },
    kids: [
      mk(doc, 'h3', { class: 'orient-key enrol-h3', text: '1. teach the counter a product' }),
      mk(doc, 'p', { class: 'orient orient-is enrol-note enrol-teach-note', text: TEACH_NOTE }),

      mk(doc, 'label', { class: 'orient-key enrol-label', attrs: { for: 'enrol-photo' }, text: 'photo of the product' }),
      file,

      mk(doc, 'button', {
        class: 'btn enrol-btn enrol-btn-capture',
        text: p.cameraCrop === null ? 'USE THE CAMERA (no crop yet)' : 'USE THE CAMERA CROP',
        attrs: {
          type: 'button', id: 'enrol-capture',
          ...(p.cameraCrop === null ? { disabled: 'disabled' } : {}),
        },
        on: { click: () => onCapture() },
      }),
      p.cameraCrop === null
        ? photoAbstain(doc, PhotoAbstain.NO_CAMERA_CROP, 'enrol-nocrop')
        : mk(doc, 'p', {
          class: 'reason enrol-hint enrol-crop-ready',
          text: 'the camera has a rectified mat crop ready. Capturing sends THAT '
            + 'crop and never a raw frame — INVARIANT 4.',
        }),

      p.image === null
        ? (p.imageReason ? mk(doc, 'div', {
          class: 'abstain line-amber enrol-image-bad',
          data: { reason: p.imageReason },
          attrs: { role: 'status' },
          kids: [
            mk(doc, 'div', { class: 'abstain-tag enrol-refusal-tag', text: 'NO PHOTO YET' }),
            mk(doc, 'p', { class: 'enrol-image-detail', text: p.imageDetail }),
            mk(doc, 'code', { class: 'abstain-why enrol-code', text: p.imageReason }),
          ],
        }) : null)
        : mk(doc, 'p', {
          class: 'reason enrol-hint enrol-image-ready',
          data: { kind: p.image.kind },
          text: `ready to teach from: ${p.image.label}`,
        }),

      mk(doc, 'label', { class: 'orient-key enrol-label', attrs: { for: 'enrol-teach-name' }, text: 'name' }),
      name,
      mk(doc, 'label', { class: 'orient-key enrol-label', attrs: { for: 'enrol-teach-price' }, text: 'price (rupees)' }),
      price,
      preview,

      mk(doc, 'button', {
        class: 'btn enrol-btn enrol-btn-teach',
        text: p.busy === 'teach' ? 'TEACHING…' : 'TEACH THIS PRODUCT',
        attrs: { type: 'button', id: 'enrol-teach' },
        on: { click: () => onTeach(name.value, price.value) },
      }),
      mk(doc, 'p', {
        class: 'reason enrol-hint',
        text: '214.507 is REFUSED, never rounded to 214.51. So are "abc", an empty '
          + 'box, -5, 1e3 and 0. The integer above is what the desk will be told.',
      }),
    ],
  });
}

/** 2. THE CATALOG: thumbnail, name, rupees AND paise, measured mm, remove. */
function renderCatalog(model, doc, handlers) {
  const cat = model.photo.catalog;
  const onForget = typeof handlers.onForget === 'function' ? handlers.onForget : () => {};
  const onRefresh = typeof handlers.onRefresh === 'function' ? handlers.onRefresh : () => {};

  let bodyEl;
  if (cat.refusal) bodyEl = serviceRefusalBlock(doc, cat.refusal);
  else if (!cat.known) bodyEl = photoAbstain(doc, PhotoAbstain.CATALOG_UNKNOWN, 'enrol-catalog-unknown');
  else if (cat.count === 0) {
    bodyEl = mk(doc, 'p', {
      class: 'reason enrol-hint enrol-catalog-empty',
      text: 'the desk answered and it has nothing taught yet. This is an EMPTY '
        + 'catalogue, which is a fact — not an unknown one. Teach a product above.',
    });
  } else {
    bodyEl = mk(doc, 'ol', {
      class: 'lines enrol-catalog-rows',
      data: { count: String(cat.count) },
      kids: cat.rows.map((r) => mk(doc, 'li', {
        class: 'line enrol-catalog-row',
        data: { sku: r.skuId, paise: r.priceOk ? String(r.pricePaise) : '' },
        kids: [
          r.thumbSrc
            ? mk(doc, 'img', {
              class: 'enrol-thumb',
              attrs: { src: r.thumbSrc, alt: `photo taught for ${r.name}`, width: '64', height: '64' },
            })
            : mk(doc, 'span', {
              class: 'enrol-thumb-none',
              data: { reason: r.thumbReason },
              text: `[no photo: ${r.thumbReason}]`,
            }),
          mk(doc, 'span', { class: 'line-name enrol-catalog-name', text: r.name }),
          mk(doc, 'code', { class: 'enrol-catalog-sku', text: r.skuId }),
          r.priceOk
            ? mk(doc, 'code', { class: 'enrol-catalog-paise', text: r.paiseText })
            : mk(doc, 'code', {
              class: 'abstain-why enrol-code enrol-catalog-noprice',
              text: r.priceReason,
            }),
          r.priceOk
            ? mk(doc, 'span', { class: 'line-price num enrol-catalog-rupees', text: r.rupees })
            : mk(doc, 'span', {
              class: 'enrol-catalog-noprice-say',
              text: 'no usable price — this SKU is NOT priced and will abstain at the till',
            }),
          mk(doc, 'span', { class: 'enrol-catalog-mm', text: r.footprintText }),
          mk(doc, 'button', {
            class: 'enrol-btn enrol-btn-forget',
            text: 'remove',
            attrs: { type: 'button', 'data-forget': r.skuId },
            on: { click: () => onForget(r.skuId) },
          }),
        ],
      })),
    });
  }

  return mk(doc, 'div', {
    class: 'enrol-catalog',
    data: { known: String(cat.known), count: String(cat.count) },
    kids: [
      mk(doc, 'h3', {
        class: 'orient-key enrol-h3',
        text: cat.known ? `2. the catalogue (${cat.count} taught)` : '2. the catalogue (unknown)',
      }),
      mk(doc, 'button', {
        class: 'btn enrol-btn enrol-btn-refresh',
        text: 'REFRESH FROM THE DESK',
        attrs: { type: 'button', id: 'enrol-refresh' },
        on: { click: () => onRefresh() },
      }),
      bodyEl,
      mk(doc, 'p', {
        class: 'reason enrol-hint',
        text: 'every row shows the paise beside the rupees and the footprint the '
          + 'MAT measured, because the integer is the value and the millimetres '
          + 'are what stops a 500 ml and a 1 L bottle being confused.',
      }),
    ],
  });
}

/** 3. TRY IT — the payoff. A second photo, and what the counter thinks. */
function renderTryIt(model, doc, handlers) {
  const rec = model.photo.recognition;
  const p = model.photo;
  const onTryPick = typeof handlers.onTryPick === 'function' ? handlers.onTryPick : () => {};
  const onTry = typeof handlers.onTry === 'function' ? handlers.onTry : () => {};

  const file = mk(doc, 'input', {
    class: 'enrol-input enrol-input-try',
    attrs: { type: 'file', id: 'enrol-try-photo', accept: IMAGE_TYPES.join(',') },
    on: { change: (ev) => onTryPick(ev) },
  });

  // A refusal that arrived INSTEAD of a reading (a dead desk, a bad shape)
  // replaces the list. A refusal about the TOTAL is different: the lines are
  // still real and still worth showing, and it is only the total that is
  // withheld. Collapsing the two would throw away a perfectly good reading
  // because its footer did not add up.
  let bodyEl;
  if (rec.refusal && !rec.known) {
    bodyEl = serviceRefusalBlock(doc, rec.refusal, 'THE READING IS NOT TRUSTWORTHY');
  } else if (!rec.known) bodyEl = photoAbstain(doc, PhotoAbstain.NOTHING_TRIED, 'enrol-try-cold');
  else if (rec.items.length === 0) {
    bodyEl = mk(doc, 'p', {
      class: 'reason enrol-hint enrol-try-nothing',
      text: 'the desk locked the mat and found NO items on it. An empty mat is a '
        + 'reading, not a failure.',
    });
  } else {
    bodyEl = mk(doc, 'ol', {
      class: 'lines enrol-try-rows',
      data: { count: String(rec.items.length), amber: String(rec.amber) },
      kids: rec.items.map((it) => mk(doc, 'li', {
        class: it.amber ? 'line line-amber enrol-try-row enrol-try-amber' : 'line enrol-try-row',
        data: { amber: String(it.amber), reason: it.reason, sku: it.skuId ?? '' },
        kids: [
          mk(doc, 'span', {
            class: 'abstain-tag enrol-try-tag',
            text: it.amber ? 'AMBER — I DO NOT KNOW' : 'NAMED',
          }),
          mk(doc, 'span', { class: 'line-name enrol-try-name', text: it.amber ? '(not named)' : it.name }),
          it.amber
            ? null
            : mk(doc, 'code', { class: 'enrol-try-paise', text: it.paiseText }),
          it.amber
            ? null
            : mk(doc, 'span', { class: 'line-price num enrol-try-rupees', text: it.rupees }),
          mk(doc, 'span', { class: 'enrol-try-mm', text: `measured ${it.footprintText}` }),
          mk(doc, 'span', {
            class: 'enrol-try-scores',
            text: it.top1 === null ? 'no scores reported'
              : `top1 ${it.top1}, top2 ${it.top2 ?? '—'}, margin ${it.margin ?? '—'}`,
          }),
          mk(doc, 'code', { class: 'abstain-why enrol-code enrol-try-why', text: it.reason }),
          mk(doc, 'p', { class: 'abstain-why enrol-try-note', text: it.reasonNote }),
          it.amber
            ? mk(doc, 'p', {
              class: 'reason enrol-hint enrol-try-excluded',
              text: 'EXCLUDED from the total. It is not priced by guess.',
            })
            : null,
        ],
      })),
    });
  }

  const totalEl = !rec.known
    ? null
    : (rec.totalAgrees
      ? mk(doc, 'p', {
        class: 'enrol-try-total',
        data: { paise: String(rec.totalPaise), priced: String(rec.priced), amber: String(rec.amber) },
        kids: [
          mk(doc, 'span', { text: 'total of the NAMED lines: ' }),
          mk(doc, 'code', { class: 'enrol-try-total-paise', text: `${rec.totalPaise} paise` }),
          mk(doc, 'span', { text: ' = ' }),
          mk(doc, 'span', { class: 'line-price num enrol-try-total-rupees', text: rec.totalRupees }),
          mk(doc, 'span', { text: ` — ${rec.note}` }),
        ],
      })
      : mk(doc, 'div', {
        class: 'abstain line-amber enrol-try-total-bad',
        data: { reason: ServiceRefusal.TOTAL_DISAGREES },
        attrs: { role: 'alert' },
        kids: [
          mk(doc, 'div', { class: 'abstain-tag enrol-refusal-tag', text: 'NO TOTAL IS SHOWN' }),
          mk(doc, 'p', { text: rec.refusal ? rec.refusal.detail : '' }),
          mk(doc, 'code', { class: 'abstain-why enrol-code', text: ServiceRefusal.TOTAL_DISAGREES }),
        ],
      }));

  return mk(doc, 'section', {
    class: 'enrol-try',
    data: {
      known: String(rec.known),
      items: String(rec.items.length),
      amber: String(rec.amber),
      priced: String(rec.priced),
    },
    kids: [
      mk(doc, 'h3', { class: 'orient-key enrol-h3', text: '3. TRY IT — show the counter a photo' }),
      mk(doc, 'p', { class: 'orient orient-is enrol-note enrol-try-note', text: TRY_NOTE }),
      mk(doc, 'label', { class: 'orient-key enrol-label', attrs: { for: 'enrol-try-photo' }, text: 'a second photo' }),
      file,
      mk(doc, 'button', {
        class: 'btn enrol-btn enrol-btn-try',
        text: p.busy === 'try' ? 'LOOKING…' : 'WHAT IS THIS?',
        attrs: { type: 'button', id: 'enrol-try' },
        on: { click: () => onTry() },
      }),
      bodyEl,
      totalEl,
      rec.known && rec.refusal === null
        ? mk(doc, 'p', {
          class: 'reason enrol-hint enrol-try-audit',
          text: rec.serverTotalPaise === null
            ? 'the desk reported no total of its own, so the figure above is this '
              + 'panel adding up the desk\'s own NAMED lines and nothing else.'
            : 'the desk\'s reported total was checked against the sum of the lines '
              + 'it sent with it, and they agree.',
        })
        : null,
    ],
  });
}

// ===========================================================================
// 11. THE PANEL OBJECT.
// ===========================================================================

/**
 * Build the panel. Nothing here touches the network unless `opts.socket` is
 * true AND a WebSocket constructor exists — so importing this module under
 * node is completely inert.
 */
export function createPanel(opts = {}) {
  const doc = opts.doc ?? opts.document ?? (typeof document !== 'undefined' ? document : null);
  const g = opts.global ?? globalThis;

  let catalogue = emptyCatalogue();
  let peel = null;
  let saaf = null;
  let refused = null;
  let simulated = opts.simulated === true;
  let sent = 0;
  let lastSent = null;
  let root = opts.root ?? null;
  let mountedInto = opts.root ? 'given' : null;
  let lastDeclared = null;

  let transport = resolveTransport(opts, g);
  let ws = null;

  // ---- the photo flow's state (sections 8-10) ----------------------------
  const loc = opts.location ?? (g && g.location) ?? null;
  let baseResolution = resolveShopBase(opts, loc, g);
  let reach = baseResolution.ok
    ? describeReach(baseResolution.base, loc, opts.csp ?? 'self')
    : { sameOrigin: false, blocked: false, reason: null, detail: '' };
  /** The real File the operator picked. The pure layer only ever sees a descriptor. */
  let pickedFile = null;
  let tryFile = null;
  let imageChoice = null;          // the descriptor derivePhoto/checkImage read
  let typedPrice = '';
  let cameraCrop = null;           // base64 PNG of the rectified mat, from a frame
  let shopPayload = undefined;     // undefined = never fetched
  let shopRefusal = null;
  let recognitionPayload = undefined;
  let recogniseRefusal = null;
  let busy = '';

  /**
   * The upload dependencies for ONE call. The blob is resolved from the live
   * File the operator picked, which never enters the pure layer — the pure
   * layer sees a descriptor (name, size, type) and nothing else, so
   * buildEnrolRequest stays testable with no File constructor in sight.
   */
  const depsFor = (file) => ({
    fetch: opts.fetch ?? (g && typeof g.fetch === 'function' ? (...a) => g.fetch(...a) : null),
    FormData: opts.FormData ?? (g && g.FormData) ?? null,
    toBlob: opts.toBlob ?? ((img) => (
      img && img.kind === 'file' ? file : b64ToBlob(img && img.b64, 'image/png', g)
    )),
  });

  const model = () => deriveEnrol({
    catalogue,
    peel,
    saaf,
    refused,
    simulated,
    canSend: transport !== null,
    via: transport ? transport.via : 'none',
    sent,
    lastSent,
    baseResolution,
    reach,
    image: imageChoice,
    typedPrice,
    cameraCrop,
    shop: shopPayload,
    shopRefusal,
    recognition: recognitionPayload,
    recogniseRefusal,
    busy,
  });

  let current = model();

  /** Send, or record honestly that there was nowhere to send it. */
  function push(msg) {
    if (!transport) {
      refused = {
        reason: Abstain.NO_BRAIN_SEAM,
        detail: `nothing was sent. The message that would have gone is `
          + `${JSON.stringify(msg)}.`,
      };
      paint();
      return false;
    }
    try {
      transport.send(msg);
      sent += 1;
      lastSent = msg;
      refused = null;
    } catch (e) {
      refused = { reason: 'enrol_send_threw', detail: (e && e.message) || String(e) };
    }
    paint();
    return refused === null;
  }

  const handlers = {
    onAdd(name, price) {
      catalogue = reduceCatalogue(catalogue, { type: Action.ADD, name, price });
      paint();
      return catalogue;
    },
    onRemove(id) {
      catalogue = reduceCatalogue(catalogue, { type: Action.REMOVE, id });
      paint();
      return catalogue;
    },
    onEnrol(rawName) {
      // Validated HERE, before the wire, so an empty name is refused with a
      // sentence the shopkeeper can act on rather than with the brain's
      // R_BAD_ARGUMENT arriving three lines later.
      const v = parseName(rawName);
      if (!v.ok) {
        catalogue = Object.freeze({
          ...catalogue,
          refusal: Object.freeze({ field: 'sticker', reason: v.reason, detail: v.detail, typed: v.typed }),
        });
        paint();
        return false;
      }
      return push({ type: 'enrol_sticker', name: v.name });
    },
    onBurst() {
      return push({ type: 'select_panel', id: 'saaf' });
    },

    // ---- the photo flow -------------------------------------------------

    /** Point this surface at a different enrolment desk. */
    onBase(raw) {
      baseResolution = { ...normaliseShopBase(raw), source: 'option' };
      reach = baseResolution.ok
        ? describeReach(baseResolution.base, loc, opts.csp ?? 'self')
        : { sameOrigin: false, blocked: false, reason: null, detail: '' };
      // The catalogue we were holding belonged to the OLD desk. Keeping it on
      // screen under a new address would be a lie about which shop this is.
      shopPayload = undefined;
      shopRefusal = null;
      recognitionPayload = undefined;
      recogniseRefusal = null;
      paint();
      return baseResolution;
    },

    /** A file was chosen for TEACH. `ev` is a change event or a bare File. */
    onPick(ev) {
      const f = fileFrom(ev);
      pickedFile = f;
      imageChoice = f === null ? null : { kind: 'file', name: f.name, size: f.size, type: f.type };
      paint();
      return imageChoice;
    },

    /** A file was chosen for TRY IT. */
    onTryPick(ev) {
      tryFile = fileFrom(ev);
      paint();
      return tryFile === null ? null : { kind: 'file', name: tryFile.name, size: tryFile.size, type: tryFile.type };
    },

    /** Teach from the LIVE rectified crop instead of a file. INVARIANT 4. */
    onCapture() {
      if (cameraCrop === null) {
        shopRefusal = {
          reason: TeachRefusal.NO_CAMERA_CROP,
          detail: TEACH_REFUSAL_HELP[TeachRefusal.NO_CAMERA_CROP],
          status: null,
        };
        paint();
        return null;
      }
      pickedFile = null;
      imageChoice = { kind: RECTIFIED_CROP_KIND, b64: cameraCrop };
      paint();
      return imageChoice;
    },

    /** Live paise preview as the operator types. No network. */
    onPriceInput(v) {
      typedPrice = v === null || v === undefined ? '' : String(v);
      paint();
      return current.photo.preview;
    },

    /** POST /enrol. Never throws; a dead desk becomes a named refusal. */
    async onTeach(name, price) {
      typedPrice = price === null || price === undefined ? typedPrice : String(price);
      const built = buildEnrolRequest({
        base: baseResolution.ok ? baseResolution.base : SAME_ORIGIN,
        name,
        price: typedPrice,
        image: imageChoice,
      });
      if (!built.ok) {
        catalogue = Object.freeze({ ...catalogue, refusal: built.refusal });
        paint();
        return built;
      }
      if (reach.blocked) {
        shopRefusal = { reason: reach.reason, detail: reach.detail, status: null };
        paint();
        return { ok: false, refusal: shopRefusal };
      }
      busy = 'teach';
      catalogue = Object.freeze({ ...catalogue, refusal: null });
      paint();
      const res = await callShop(built.request, depsFor(pickedFile));
      busy = '';
      if (!res.ok) { shopRefusal = res.refusal; paint(); return res; }
      shopRefusal = null;
      // The desk's answer to /enrol may or may not carry the whole catalogue.
      // If it does, take it; otherwise ask for it, so the row appears without
      // the operator having to press REFRESH to believe the teach worked.
      if (res.data && Array.isArray(res.data.skus)) shopPayload = res.data;
      else await handlers.onRefresh();
      paint();
      return res;
    },

    /** GET /shop. */
    async onRefresh() {
      if (!baseResolution.ok) {
        shopRefusal = { reason: baseResolution.reason, detail: baseResolution.detail, status: null };
        paint();
        return { ok: false, refusal: shopRefusal };
      }
      if (reach.blocked) {
        shopRefusal = { reason: reach.reason, detail: reach.detail, status: null };
        paint();
        return { ok: false, refusal: shopRefusal };
      }
      const url = `${baseResolution.base}${ShopPath.SHOP}`;
      const res = await callShop(
        { method: 'GET', path: ShopPath.SHOP, url, fields: {}, image: null },
        depsFor(null),
      );
      if (res.ok) { shopPayload = res.data; shopRefusal = null; }
      else { shopRefusal = res.refusal; }
      paint();
      return res;
    },

    /** DELETE /shop/{sku_id}. */
    async onForget(skuId) {
      const built = buildRemoveRequest(skuId, baseResolution.ok ? baseResolution.base : SAME_ORIGIN);
      if (!built.ok) {
        catalogue = Object.freeze({ ...catalogue, refusal: built.refusal });
        paint();
        return built;
      }
      if (reach.blocked) {
        shopRefusal = { reason: reach.reason, detail: reach.detail, status: null };
        paint();
        return { ok: false, refusal: shopRefusal };
      }
      const res = await callShop(built.request, depsFor(null));
      if (!res.ok) { shopRefusal = res.refusal; paint(); return res; }
      shopRefusal = null;
      if (res.data && Array.isArray(res.data.skus)) { shopPayload = res.data; paint(); }
      else await handlers.onRefresh();
      return res;
    },

    /** POST /recognise — the payoff. */
    async onTry() {
      const image = tryFile === null
        ? (cameraCrop === null ? null : { kind: RECTIFIED_CROP_KIND, b64: cameraCrop })
        : { kind: 'file', name: tryFile.name, size: tryFile.size, type: tryFile.type };
      const built = buildRecogniseRequest({
        base: baseResolution.ok ? baseResolution.base : SAME_ORIGIN,
        image,
      });
      if (!built.ok) {
        recogniseRefusal = { reason: built.refusal.reason, detail: built.refusal.detail, status: null };
        paint();
        return built;
      }
      if (reach.blocked) {
        recogniseRefusal = { reason: reach.reason, detail: reach.detail, status: null };
        paint();
        return { ok: false, refusal: recogniseRefusal };
      }
      busy = 'try';
      recogniseRefusal = null;
      paint();
      const res = await callShop(built.request, depsFor(tryFile));
      busy = '';
      if (res.ok) { recognitionPayload = res.data; recogniseRefusal = null; }
      else { recognitionPayload = undefined; recogniseRefusal = res.refusal; }
      paint();
      return res;
    },
  };

  /** A change event, a File, or null. Tolerant because browsers differ. */
  function fileFrom(ev) {
    if (!ev) return null;
    if (ev.target && ev.target.files && ev.target.files.length > 0) return ev.target.files[0];
    if (ev.files && ev.files.length > 0) return ev.files[0];
    if (typeof ev.name === 'string' && ('size' in ev)) return ev;
    return null;
  }

  /**
   * Find somewhere to draw, in descending order of how much the shell agreed
   * to it:
   *   1. an #enrol-render we already made,
   *   2. inside #body-enrol — the fill point index.html names for this panel,
   *   3. inside #panel-enrol, if the body id ever changes,
   *   4. at the end of #chrome, or of <body>, so that a shell which has never
   *      heard of this surface still gets an input rather than nothing.
   * Case 4 is what made this panel usable before index.html grew a section for
   * it, and it stays because a mount that depends on another agent's file
   * having been edited first is a mount that is broken half the time.
   */
  function resolveRoot() {
    if (root) return root;
    if (!doc || typeof doc.getElementById !== 'function') return null;
    root = doc.getElementById(PANEL_ROOT_ID);
    if (root) return root;
    if (typeof doc.createElement !== 'function') return null;
    const host = doc.getElementById(SHELL_BODY_ID)
      || doc.getElementById(SHELL_PANEL_ID)
      || doc.getElementById('chrome')
      || doc.body
      || null;
    if (!host || typeof host.appendChild !== 'function') return null;
    root = doc.createElement('div');
    root.setAttribute('id', PANEL_ROOT_ID);
    mountedInto = host.getAttribute ? (host.getAttribute('id') || 'body') : 'body';
    host.appendChild(root);
    injectStyle(doc);
    return root;
  }

  /**
   * Tell the shell what this panel currently knows, so its rail dot and its
   * #abstain-enrol block follow reality instead of staying frozen on the
   * cold-load text. app.js REFUSES an id outside PANEL_IDS and 'enrol' is not
   * in that list yet, so the refusal is recorded rather than thrown — the day
   * app.js learns the id, this starts working with no edit here.
   *
   * It can only ever say OFF, ABSTAIN or OK. There is no GREEN to declare and
   * app.js would refuse one anyway: green is a settled session.
   */
  function declareStatus() {
    const api = g && g.GAWAAH;
    if (!api || typeof api.setPanelStatus !== 'function') return null;
    const knows = peel !== null || saaf !== null;
    const why = current.abstentions.length ? current.abstentions[0] : null;
    try {
      lastDeclared = knows
        ? api.setPanelStatus(PANEL_ID, 'OK', null)
        : api.setPanelStatus(PANEL_ID, 'ABSTAIN', why);
    } catch (e) {
      lastDeclared = { ok: false, reason: `declare_threw:${(e && e.message) || e}` };
    }
    return lastDeclared;
  }

  function paint() {
    current = model();
    const host = resolveRoot();
    if (!host || typeof host.replaceChildren !== 'function') { declareStatus(); return false; }
    host.replaceChildren(renderEnrol(current, doc, handlers));
    if (host.dataset) {
      host.dataset.skus = String(current.catalogue.count);
      host.dataset.abstentions = String(current.abstentions.length);
      host.dataset.simulated = String(current.simulated);
      host.dataset.taught = current.photo.catalog.known
        ? String(current.photo.catalog.count) : 'unknown';
      host.dataset.deskblocked = String(current.photo.reach.blocked);
    }
    declareStatus();
    return true;
  }

  /**
   * Brain -> panel. Accepts the raw protocol messages; anything it does not own
   * is ignored rather than guessed at. Returns whether it consumed the message,
   * so the test can assert the routing instead of inferring it.
   */
  function onState(msg) {
    if (!msg || typeof msg !== 'object') return false;
    switch (msg.type) {
      case 'peel': peel = msg; break;
      case 'saaf': saaf = msg; break;
      case 'refused': refused = { reason: msg.reason, detail: msg.detail || '' }; break;
      case 'frame': {
        // INVARIANT 4. brain_server.py sends {"type":"frame","rect":"<base64
        // PNG>"} and `rect` is the RECTIFIED MAT CROP — the only pixels that
        // survive a frame grab. We keep that one field and nothing else: no
        // raw frame is stored here, so none can later be uploaded from here.
        const r = typeof msg.rect === 'string' ? msg.rect : '';
        if (r === '') return false;
        cameraCrop = r;
        break;
      }
      default: return false;
    }
    paint();
    return true;
  }

  return {
    id: PANEL_ID,
    title: PANEL_TITLE,
    get model() { return current; },
    get catalogue() { return catalogue; },
    get transport() { return transport; },
    /** Which shell element this surface actually found. Reported, not assumed. */
    get mountedInto() { return mountedInto; },
    /** app.js's verdict on our last status declaration, refusals included. */
    get declared() { return lastDeclared; },
    handlers,
    onState,
    /** Where this surface thinks the enrolment desk is, and how it decided. */
    get shopBase() { return baseResolution; },
    get reach() { return reach; },
    /** The live rectified crop, if the brain has sent one. Never a raw frame. */
    get cameraCrop() { return cameraCrop; },
    /**
     * SAAF-style: this surface draws nothing over the live view. It DOES keep
     * the rectified crop the frame carried, so "teach from the camera" has
     * something to send — but it returns false because it paints no overlay.
     */
    onFrame(frame) {
      if (frame && typeof frame.rect === 'string' && frame.rect !== '') {
        cameraCrop = frame.rect;
        paint();
      }
      return false;
    },
    paint,
    /** Say, out loud and on screen, that what follows is synthetic. */
    setSimulated(v) { simulated = v === true; paint(); return simulated; },

    /**
     * Open our own socket to the brain. Only called from the browser mount, and
     * only when no better seam was offered. Inert with no WebSocket.
     */
    connect(url) {
      const WS = g.WebSocket;
      if (typeof WS !== 'function') return null;
      const target = url || brainUrl(g.location || null);
      try { ws = new WS(target); }
      catch (e) {
        refused = { reason: 'enrol_socket_refused', detail: (e && e.message) || String(e) };
        paint();
        return null;
      }
      transport = { send: (m) => ws.send(JSON.stringify(m)), via: `socket ${target}`, socket: ws };
      ws.onopen = () => { paint(); };
      ws.onmessage = (ev) => {
        let m;
        try { m = JSON.parse(ev.data); } catch { return; }
        onState(m);
      };
      ws.onclose = () => {
        transport = resolveTransport(opts, g);
        refused = { reason: 'enrol_socket_closed', detail: `the socket to ${target} closed` };
        paint();
      };
      ws.onerror = () => { try { ws.close(); } catch { /* onclose reports it */ } };
      paint();
      return ws;
    },

    close() { if (ws) { try { ws.close(); } catch { /* already gone */ } ws = null; } },
  };
}

// ===========================================================================
// 12. THE LOAD-ORDER SEAM. Same contract as the other five panels.
// ===========================================================================

/**
 * `register` is app.js's registerPanel. It refuses any id outside PANEL_IDS,
 * and 'enrol' is not in that list — index.html and app.js belong to another
 * agent and neither knows this surface exists yet. That refusal is CAPTURED
 * rather than thrown: the drain loop must keep going, and this panel does not
 * need the registry to work, because it mounts its own root and dials its own
 * socket. When app.js grows an 'enrol' id, this same call starts succeeding
 * with no edit here.
 */
export function attach(register, opts = {}) {
  if (typeof register !== 'function') {
    throw new TypeError('attach(register): registerPanel must be a function');
  }
  const panel = createPanel(opts);
  let registration = null;
  try { registration = register(PANEL_ID, { onState: panel.onState, onFrame: panel.onFrame }); }
  catch (e) { registration = { ok: false, reason: `register_threw:${(e && e.message) || e}` }; }
  panel.registration = registration;
  return panel;
}

export function attachEnrolPanel(opts = {}) {
  const panel = createPanel(opts);
  const register = typeof opts.register === 'function'
    ? opts.register
    : (typeof globalThis.registerPanel === 'function' ? globalThis.registerPanel : null);
  let registration = null;
  if (register) {
    try { registration = register(PANEL_ID, { onState: panel.onState, onFrame: panel.onFrame }); }
    catch (e) { registration = { ok: false, reason: `register_threw:${(e && e.message) || e}` }; }
  }
  return { panel, registered: registration !== null && registration.ok === true, registration };
}

export const DESCRIPTOR = {
  id: PANEL_ID,
  title: PANEL_TITLE,
  createPanel,
  attach,
  attached: false,
};

/**
 * Publish. peel.js's automount REPLACES globalThis.GAWAAH_PANELS with a plain
 * object (`Object.assign({}, ...)`), so `.push` is not guaranteed to exist by
 * the time this file evaluates. All three shapes are handled rather than
 * assumed, because a TypeError here would take the whole module down and the
 * shopkeeper would lose the only input surface on the page.
 */
function publish(g) {
  if (!g) return 'no_global';
  try {
    if (Array.isArray(g.GAWAAH_PANELS)) { g.GAWAAH_PANELS.push(DESCRIPTOR); return 'array'; }
    if (g.GAWAAH_PANELS && typeof g.GAWAAH_PANELS === 'object') {
      g.GAWAAH_PANELS[PANEL_ID] = DESCRIPTOR;
      return 'object';
    }
    g.GAWAAH_PANELS = [DESCRIPTOR];
    return 'created';
  } catch (e) { return `refused:${(e && e.message) || e}`; }
}

export const PUBLISHED = (typeof globalThis !== 'undefined')
  ? (() => {
    const g = globalThis;
    if (typeof g.registerPanel === 'function') {
      try { attach(g.registerPanel); DESCRIPTOR.attached = true; }
      catch { /* the queue drain will retry; never take the module down */ }
    }
    return publish(g);
  })()
  : 'no_global';

// ===========================================================================
// 13. BROWSER AUTO-MOUNT. Inert under node: guarded on `document`.
// ===========================================================================

/**
 * Ask the brain whether it is running synthetic frames, and paint the SIMULATED
 * banner if it is. INVARIANT 7: anything simulated must be visibly labelled, so
 * this is not decoration — it is the difference between a measurement and a
 * story. A failed fetch leaves the banner OFF and leaves the reason on the
 * panel, because "I could not ask" is not "it is real".
 */
export async function probeSim(panel, fetchFn, loc) {
  const f = fetchFn || (typeof fetch === 'function' ? fetch : null);
  if (!f) return { asked: false, sim: null, reason: 'no_fetch' };
  try {
    const res = await f(healthUrl(loc));
    const body = await res.json();
    const sim = body && body.sim === true;
    if (panel && typeof panel.setSimulated === 'function') panel.setSimulated(sim);
    return { asked: true, sim, reason: sim ? 'brain_reports_sim' : 'brain_reports_live' };
  } catch (e) {
    return { asked: true, sim: null, reason: `health_unreachable:${(e && e.message) || e}` };
  }
}

function automount() {
  try {
    const g = globalThis;
    const panel = createPanel({ doc: document, global: g });
    panel.paint();
    if (panel.transport === null) panel.connect();
    g.GAWAAH_ENROL = panel;
    probeSim(panel, null, g.location);
  } catch { /* a panel that cannot mount must not take the counter down */ }
}

if (typeof document !== 'undefined' && typeof window !== 'undefined'
  && globalThis.__GAWAAH_PANEL_AUTOMOUNT !== false) {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', automount);
  else automount();
}

export default {
  PANEL_ID, PANEL_TITLE, PANEL_ROOT_ID,
  parsePaise, formatPaise, groupIndian, parseName,
  emptyCatalogue, reduceCatalogue, deriveEnrol, renderEnrol,
  createPanel, attach, DESCRIPTOR,
};
