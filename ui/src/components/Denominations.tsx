import { useRef } from 'react';
import { money } from '../lib/manageapi';
import '../styles/daybook.css';

/**
 * COUNT THE DRAWER BY NOTE.
 *
 * Nobody counts a cash drawer as a single figure. A shopkeeper squares the
 * notes into piles — five hundreds here, hundreds there — counts each pile,
 * writes the count down, and adds the piles up at the end. Asking him for one
 * total instead is asking him to do that last step in his head at ten at night
 * and then type the answer with no way of checking it. This is the pile sheet.
 *
 * WHY THIS DOES NOT BREAK "THE BROWSER IS NEVER AN AUTHOR".
 * That rule is about prices, SKUs and payloads: the page states an intent and
 * the server derives the money. The counted cash was never derived by anything
 * — it is the shopkeeper's own assertion about a physical drawer, and it has
 * always gone up as a figure he authored. What changes here is only HOW he
 * writes it: eleven whole counts instead of one total. Every step of the
 * arithmetic between them is integer paise (a count is a whole number, a
 * denomination is a whole number of paise, and a sum of those is a whole number
 * of paise), and the value that leaves this module is rendered back to a rupee
 * STRING by integer subtraction and remainder — never by `p / 100`. The server
 * parses that string with `money.from_rupees_str` and remains the only thing
 * that turns it into stored money.
 *
 * A FIGURE THAT CANNOT BE DERIVED SAYS SO. If any box holds something that is
 * not a whole count, the grand total is null and the screen prints that it has
 * no figure. It does not skip the bad row and show a total that looks right.
 *
 * WHAT IS ON THE LIST. The notes in circulation (500, 200, 100, 50, 20, 10) and
 * the coins (20, 10, 5, 2, 1). Twenty and ten appear twice on purpose, because
 * they exist as both and a drawer holds them in two different places — the
 * arithmetic is identical either way, so a note dropped in the coin box costs
 * nothing. The ₹2,000 note is deliberately absent: it was withdrawn from
 * circulation in 2023 and a row nobody uses is a row that gets mis-tapped. If
 * one is in the drawer, the typed-total path takes it, and the screen says so
 * rather than leaving the shopkeeper to discover the gap.
 */

/* ------------------------------------------------------------ the sheet -- */

export type DenomKind = 'note' | 'coin';

export interface Denom {
  /** The key in the counts map. Carries the kind, since ₹20 is both. */
  key: string;
  /** What is printed on it, for the label. Never parsed. */
  label: string;
  /** Its value in whole paise, written out. Nothing here multiplies to get it. */
  paise: number;
  kind: DenomKind;
}

export const NOTES: ReadonlyArray<Denom> = [
  { key: 'n500', label: '500', paise: 50_000, kind: 'note' },
  { key: 'n200', label: '200', paise: 20_000, kind: 'note' },
  { key: 'n100', label: '100', paise: 10_000, kind: 'note' },
  { key: 'n50', label: '50', paise: 5_000, kind: 'note' },
  { key: 'n20', label: '20', paise: 2_000, kind: 'note' },
  { key: 'n10', label: '10', paise: 1_000, kind: 'note' },
];

export const COINS: ReadonlyArray<Denom> = [
  { key: 'c20', label: '20', paise: 2_000, kind: 'coin' },
  { key: 'c10', label: '10', paise: 1_000, kind: 'coin' },
  { key: 'c5', label: '5', paise: 500, kind: 'coin' },
  { key: 'c2', label: '2', paise: 200, kind: 'coin' },
  { key: 'c1', label: '1', paise: 100, kind: 'coin' },
];

export const DENOMINATIONS: ReadonlyArray<Denom> = [...NOTES, ...COINS];

/**
 * The most of one denomination this sheet will take: three digits.
 *
 * Not an arbitrary round number. It is the ceiling that makes every figure this
 * component can produce a figure `lib/money.ts` will format: 999 of each of the
 * eleven rows comes to ₹9,17,082, and `rupees()` refuses past ₹10,00,000. So
 * the "outside the range this till will price" branch is unreachable here by
 * construction rather than by hope, and no subtotal can render as an internal
 * error message. It is also well past what any kirana drawer holds — 999
 * five-hundreds is ₹4,99,500, which the server already refuses to close on.
 */
export const MAX_PER_ROW = 999;

/** What the boxes hold: the raw text typed, per denomination key. */
export type DenomCounts = Record<string, string>;

export interface RowTally {
  denom: Denom;
  /** The text in the box, exactly as typed. */
  typed: string;
  /** Something has been typed here. Blank is not the same as zero. */
  entered: boolean;
  /** A whole count, or null when the box holds something that is not one. */
  count: number | null;
  /** count × denomination, in whole paise. Null when the count is not readable. */
  paise: number | null;
}

export interface Tally {
  rows: RowTally[];
  /** Whole paise, or null when any filled box could not be read as a count. */
  notesPaise: number | null;
  coinsPaise: number | null;
  totalPaise: number | null;
  notesPieces: number | null;
  coinsPieces: number | null;
  /** How many boxes carry something. Zero means the drawer is not counted yet. */
  entered: number;
  notesEntered: number;
  coinsEntered: number;
  /** The denominations whose boxes hold something unreadable. */
  unreadable: Denom[];
}

/**
 * A whole count, or null.
 *
 * Blank returns null too — the caller separates "nothing typed" from "typed
 * something that is not a count" with `entered`, because those two need
 * different sentences on screen.
 */
export function countOf(typed: string): number | null {
  const t = typed.trim();
  if (!/^\d{1,3}$/.test(t)) return null;
  const n = Number(t);
  return Number.isInteger(n) && n >= 0 && n <= MAX_PER_ROW ? n : null;
}

/**
 * The whole sheet, added up.
 *
 * Integer arithmetic end to end, and `Number.isSafeInteger` on every product
 * and every running sum: JavaScript has no integer type to lean on, so the
 * discipline is asserted rather than assumed — the same rule `lib/money.ts`
 * states for prices.
 */
export function tallyOf(counts: DenomCounts): Tally {
  const rows: RowTally[] = [];
  const unreadable: Denom[] = [];
  let entered = 0;
  let notesEntered = 0;
  let coinsEntered = 0;

  let notesPaise: number | null = 0;
  let coinsPaise: number | null = 0;
  let notesPieces: number | null = 0;
  let coinsPieces: number | null = 0;

  for (const denom of DENOMINATIONS) {
    const typed = counts[denom.key] ?? '';
    const isEntered = typed.trim() !== '';
    const count = isEntered ? countOf(typed) : null;

    let paise: number | null = null;
    if (count !== null) {
      const p = denom.paise * count;
      paise = Number.isSafeInteger(p) ? p : null;
    }
    rows.push({ denom, typed, entered: isEntered, count, paise });

    // A blank box is not a zero anybody typed, but it contributes nothing to a
    // pile that is not there, so it leaves the subtotals alone.
    if (!isEntered) continue;
    entered += 1;

    const isNote = denom.kind === 'note';
    if (isNote) notesEntered += 1; else coinsEntered += 1;
    // One unreadable box poisons its own group's subtotal and the grand total.
    // A total that quietly skipped a row would be a plausible-looking number
    // for a drawer nobody counted that way.
    if (count === null || paise === null) {
      unreadable.push(denom);
      if (isNote) { notesPaise = null; notesPieces = null; } else { coinsPaise = null; coinsPieces = null; }
      continue;
    }
    if (isNote) {
      if (notesPaise !== null) {
        const s = notesPaise + paise;
        notesPaise = Number.isSafeInteger(s) ? s : null;
      }
      if (notesPieces !== null) notesPieces += count;
    } else {
      if (coinsPaise !== null) {
        const s = coinsPaise + paise;
        coinsPaise = Number.isSafeInteger(s) ? s : null;
      }
      if (coinsPieces !== null) coinsPieces += count;
    }
  }

  let totalPaise: number | null = null;
  if (notesPaise !== null && coinsPaise !== null) {
    const t = notesPaise + coinsPaise;
    totalPaise = Number.isSafeInteger(t) ? t : null;
  }

  return {
    rows,
    notesPaise,
    coinsPaise,
    totalPaise,
    notesPieces,
    coinsPieces,
    entered,
    notesEntered,
    coinsEntered,
    unreadable,
  };
}

/**
 * Whole paise back to the rupee string that goes on the wire.
 *
 * `(p - (p % 100)) / 100` divides a number that is already a multiple of a
 * hundred, so it is exact for every value inside `Number.MAX_SAFE_INTEGER`.
 * Writing `p / 100` here is the one line that could put a float into a record
 * that never reopens, which is why it is spelled out. No grouping commas: this
 * string is for the server, and it is the shape `money.from_rupees_str` reads.
 */
export function rupeeStringFromPaise(paise: number): string | null {
  if (!Number.isSafeInteger(paise) || paise < 0) return null;
  const whole = (paise - (paise % 100)) / 100;
  const rest = paise % 100;
  return `${whole}.${String(rest).padStart(2, '0')}`;
}

/**
 * How many notes and coins, in plain English, or null when a box cannot be read.
 *
 * A group nobody touched is reported as not counted, never as nought of them.
 * "21 notes and 0 coins" is a claim about the coin tray; "21 notes, coins not
 * counted" is what actually happened.
 */
export function piecesLine(t: Tally): string | null {
  if (t.notesPieces === null || t.coinsPieces === null) return null;
  const n = t.notesPieces;
  const c = t.coinsPieces;
  const notes = `${n} note${n === 1 ? '' : 's'}`;
  const coins = `${c} coin${c === 1 ? '' : 's'}`;
  if (t.notesEntered === 0 && t.coinsEntered === 0) return 'nothing counted in yet';
  if (t.coinsEntered === 0) return `${notes}, coins not counted`;
  if (t.notesEntered === 0) return `${coins}, notes not counted`;
  return `${notes} and ${coins}`;
}

/* ------------------------------------------------------------ the sheet -- */

export default function Denominations({ counts, tally, onChange, disabled }: {
  counts: DenomCounts;
  /** Computed by the caller with `tallyOf`, so one arithmetic runs once and the
      figure on screen and the figure on the wire cannot drift apart. */
  tally: Tally;
  onChange: (next: DenomCounts) => void;
  disabled?: boolean;
}) {
  // Enter walks down the column. A shopkeeper counting one-handed on a phone
  // keypad never has to reach for Tab, and the DOM order is the tab order
  // anyway, so the two agree.
  const boxes = useRef<Array<HTMLInputElement | null>>([]);

  const setOne = (key: string, value: string) => {
    // Only what a count can be made of reaches the state — typed or pasted, it
    // comes through here. A box that silently ate a letter would leave the total
    // null with nothing on screen to explain it; a box that never takes the
    // letter explains itself.
    //
    // This makes the unreadable-box branches below unreachable from a keyboard,
    // and they are kept anyway: `tallyOf` is exported and a caller that seeds
    // counts from somewhere else must still be refused a total rather than
    // handed one that quietly skipped a row.
    const clean = value.replace(/[^\d]/g, '').slice(0, 3);
    onChange({ ...counts, [key]: clean });
  };

  const row = (r: RowTally, index: number, last: boolean) => (
    <label className="dc-denom" key={r.denom.key}>
      <span className="mult" aria-hidden="true">
        <span className="x">×</span>
        <b>{r.denom.label}</b>
      </span>
      <input
        ref={(el) => { boxes.current[index] = el; }}
        type="text"
        inputMode="numeric"
        pattern="[0-9]*"
        autoComplete="off"
        maxLength={3}
        enterKeyHint={last ? 'done' : 'next'}
        disabled={disabled}
        aria-label={`how many ${r.denom.label} rupee ${r.denom.kind}s`}
        aria-invalid={r.entered && r.count === null}
        value={r.typed}
        onChange={(e) => setOne(r.denom.key, e.target.value)}
        onFocus={(e) => e.target.select()}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); boxes.current[index + 1]?.focus(); }
        }}
      />
      <span className="sum">
        {!r.entered ? (
          <span className="none" aria-hidden="true">—</span>
        ) : r.paise === null ? (
          <span className="bad">not a count</span>
        ) : (
          money(r.paise)
        )}
      </span>
    </label>
  );

  const group = (
    title: string, kind: DenomKind, offset: number,
    subtotal: number | null, pieces: number | null, groupEntered: number, unit: string,
  ) => (
    <div className="dc-group">
      <div className="dc-group-head">
        <span className="nm">{title}</span>
        <span className="hd">how many</span>
        <span className="hd amt">comes to</span>
      </div>
      {tally.rows.filter((r) => r.denom.kind === kind).map((r, i) => row(
        r,
        offset + i,
        offset + i === DENOMINATIONS.length - 1,
      ))}
      {/* A group nobody has touched has no subtotal. A ₹0.00 there is a figure,
          and there is no figure — the boxes are empty, not counted as nought. */}
      <div className="dc-group-sum">
        <span className="nm">
          {groupEntered === 0 || pieces === null
            ? title
            : `${pieces} ${unit}${pieces === 1 ? '' : 's'}`}
        </span>
        <span className="amt">
          {groupEntered === 0 ? <span className="none">not counted</span>
            : subtotal === null ? <span className="none">no figure</span>
              : money(subtotal)}
        </span>
      </div>
    </div>
  );

  return (
    <div className="dc-count-sheet">
      <div className="dc-denoms-head">
        <p className="dc-eyebrow">count the drawer, pile by pile</p>
        {tally.entered > 0 && (
          <button
            type="button"
            className="btn sm ghost"
            disabled={disabled}
            onClick={() => onChange({})}
          >
            CLEAR
          </button>
        )}
      </div>

      <div className="dc-denoms">
        {group('Notes', 'note', 0, tally.notesPaise, tally.notesPieces, tally.notesEntered, 'note')}
        {group('Coins', 'coin', NOTES.length, tally.coinsPaise, tally.coinsPieces, tally.coinsEntered, 'coin')}
      </div>

      {tally.unreadable.length > 0 && (
        <p className="dc-count-bad">
          {tally.unreadable.length === 1 ? 'One box does' : `${tally.unreadable.length} boxes do`} not
          hold a whole count, so there is no total. Nothing was read as zero to get past it.
        </p>
      )}

      <p className="hint">
        Boxes left blank count for nothing. Only these denominations add up here — an old ₹2,000
        note, or anything else not on the sheet, has to go in the typed total instead.
      </p>
    </div>
  );
}
