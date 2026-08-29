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

      // ---- add an SKU ----------------------------------------------------
      mk(doc, 'div', {
        class: 'enrol-form',
        kids: [
          mk(doc, 'h3', { class: 'orient-key enrol-h3', text: '1. add an SKU' }),
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
          mk(doc, 'h3', { class: 'orient-key enrol-h3', text: `2. the catalogue (${model.catalogue.count})` }),
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
          mk(doc, 'h3', { class: 'orient-key enrol-h3', text: '3. enrol a sticker' }),
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
          mk(doc, 'h3', { class: 'orient-key enrol-h3', text: '4. capture burst (SAAF)' }),
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
// 8. THE PANEL OBJECT.
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
  };

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
    /** SAAF-style: this surface has nothing to draw over the live view. */
    onFrame() { return false; },
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
// 9. THE LOAD-ORDER SEAM. Same contract as the other five panels.
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
// 10. BROWSER AUTO-MOUNT. Inert under node: guarded on `document`.
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
