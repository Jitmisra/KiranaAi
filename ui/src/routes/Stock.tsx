import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import * as stockapi from '../lib/stockapi';
import {
  Button, Card, Empty, Field, Input, KV, LoadingCard, Refusal, Segmented, Select,
  Skeleton, SkeletonRows, Stat, StatGrid, Verdict,
} from '../components/ui';
import { ShareSheet } from '../components/ShareSheet';
import '../styles/stock.css';

/**
 * Stock — what arrived, what broke, and what is running out.
 *
 * THE THREE THINGS THIS SCREEN IS HONEST ABOUT, because each of them is a place
 * a stock page normally lies:
 *
 *  1. THERE IS NO STOCK SENSOR. Every figure is the shopkeeper's own count, plus
 *     the movements he wrote down, minus what this counter billed. Anything that
 *     leaves the shop without being billed and without being recorded here is
 *     invisible, and the page says so beside the numbers rather than in a
 *     footnote nobody reads. A product nobody has counted shows "not counted"
 *     and never a zero: a zero is a claim about a shelf.
 *  2. DAYS OF COVER IS OFTEN UNKNOWABLE. It needs a count, and it needs enough
 *     trade to divide by. Where the server cannot derive it, this page prints
 *     "not enough history" and the server's own sentence saying which of the
 *     five reasons it is. A confident number off one busy afternoon is the
 *     failure that teaches a shopkeeper to stop reading the column.
 *  3. A RECORD IS PERMANENT. There is no edit and no delete: a mistake is
 *     corrected with an opposite movement carrying the reason "correction", and
 *     both lines stay on the log. So the entry form does not guess. It defaults
 *     the reason for stock coming IN, where a delivery is nearly always the
 *     answer, and refuses to default the reason for stock going OUT, where
 *     guessing "breakage" would file theft, expiry and a bottle taken home under
 *     one word forever.
 *
 * COLOUR. Green, amber and red belong to money and recognition state on this
 * product, and a shelf is neither. So low stock is rendered QUIET — a strong
 * number and a rule down the side, not an alarm — and the only amber on the page
 * is a derived figure below zero, which the Inventory screen already treats that
 * way because it means stock left without anybody recording it. Red is kept for
 * a hash chain that does not verify.
 *
 * NO MONEY. This screen renders no price, no total and no valuation of the
 * shelf. `gawaah/stock.py` publishes none, on purpose, and inventing one here
 * would be the browser authoring money.
 */

/* --------------------------------------------------------------- fragments -- */

/**
 * WHY A CONTROL IS DEAD, WHERE THE HAND IS.
 *
 * Quiet ink, never a verdict colour: a button that is not applicable yet is not
 * a refusal, and on this product amber and red are spoken for. Tied to the
 * control with `aria-describedby`, so it is not only for the eye.
 */
function WhyDead({ id, children }: { id: string; children: ReactNode }) {
  return <p className="st-whydead" id={id}>{children}</p>;
}

/**
 * Put the cursor where the sentence just sent it.
 *
 * An empty state that names the next action and then leaves the shopkeeper to
 * find the control is only half an instruction. This is the other half; it
 * moves focus and nothing else, and writes nothing anywhere.
 */
function jumpTo(id: string): void {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ block: 'center', behavior: 'smooth' });
  (el as HTMLElement).focus({ preventScroll: true });
}

/**
 * THE SHELF TABLE, WAITING — at the shape of the table that replaces it.
 *
 * The header is already drawn, the six columns are already the width they will
 * be, and the four numeric columns carry a RIGHT-ALIGNED bar, because that is
 * where the figure lands. `SkeletonRows` was standing in here and drew three
 * left-aligned bars for a six-column table, so every figure jumped left to
 * right when the rows arrived.
 */
function ShelfTableSkeleton() {
  const cells: ReadonlyArray<{ cls?: string; w: number | string; label: string }> = [
    { w: '60%', label: 'Product' }, { cls: 'r', w: 44, label: 'On hand' },
    { cls: 'r', w: 40, label: 'Reorder at' }, { cls: 'r', w: 72, label: 'Cover' },
    { cls: 'r hide-sm', w: 52, label: 'Since your count' },
    { cls: 'hide-sm', w: 64, label: 'Last movement' },
  ];
  return (
    <div className="scroll-x" aria-hidden="true">
      {/* `tbl-cards` so the waiting shape is the shape of what lands. Without it
          the skeleton was a six-column grid and the rows arrived as cards. */}
      <table className="moments st-table tbl-cards">
        <thead>
          <tr>
            <th>Product</th>
            <th className="r">On hand</th>
            <th className="r">Reorder at</th>
            <th className="r">Cover</th>
            <th className="r hide-sm">Since your count</th>
            <th className="hide-sm">Last movement</th>
          </tr>
        </thead>
        <tbody>
          {[0, 1, 2, 3].map((r) => (
            <tr key={r}>
              {cells.map((c, i) => (
                <td key={i} className={c.cls} data-label={c.label}>
                  <span className="st-skelcell">
                    <Skeleton w={c.w} h={11} radius={999} />
                  </span>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * The direction of a movement, in words.
 *
 * An arrow was drawn here first and taken out: beside a signed number, "↑ −4"
 * makes a reader stop and work out whether the arrow means the stock went up or
 * the shelf went down. The word says it once.
 */
function Dir({ kind }: { kind: stockapi.Direction }) {
  return <span className={`st-dirlab ${kind}`}>{kind === 'in' ? 'IN' : 'OUT'}</span>;
}

/**
 * The on-hand figure, or the absence of one. Never a zero standing in for both.
 *
 * `undefined` is treated as `null` rather than trusted: a field this page reads
 * off a response it did not build should not be able to print the word
 * "undefined" where a shopkeeper is looking for a count.
 */
function OnHand({ row, big }: { row: stockapi.StockRow; big?: boolean }) {
  if (row.on_hand_units === null || row.on_hand_units === undefined) {
    return <span className="st-absent">not counted</span>;
  }
  return (
    <b className={`st-onhand tnum${big ? ' big' : ''}${row.needs_recount ? ' below' : ''}`}>
      {row.on_hand_units}
    </b>
  );
}

/**
 * Days of cover, or the server's reason for having none.
 *
 * `title` carries the full sentence in the table, where a paragraph per row
 * would bury the rows. It is printed in full wherever there is room for it.
 */
function Cover({ cover, full }: { cover: stockapi.Cover | undefined; full?: boolean }) {
  // A missing block is not the same as a derived null, and neither is a number.
  // This costs one line and stops a renamed field taking the page down.
  if (!cover) {
    return <span className="st-absent">no cover figure on this row</span>;
  }
  if (cover.days === null || cover.days === undefined) {
    return (
      <span className="st-nohist" title={cover.why}>
        not enough history
        {full && <span className="st-why">{cover.why}</span>}
      </span>
    );
  }
  return (
    <span className="st-cover" title={cover.why}>
      <b className="tnum">{cover.days}</b> day{cover.days === 1 ? '' : 's'}
      {full && <span className="st-why">{cover.why}</span>}
    </span>
  );
}

/**
 * A signed count, with a real minus sign.
 *
 * The server sends the sign — the browser never decides which way a movement
 * went — and this only chooses the glyph, so a column of movements lines up
 * with the "+12 / −11" in the shelf table instead of mixing two dashes.
 */
function signed(units: number): string {
  return units > 0 ? `+${units}` : `−${Math.abs(units)}`;
}

/**
 * One movement, as one line of the log.
 *
 * `name` is null when the log is already filtered to one product: repeating the
 * same name down five rows crowds out the reason, which is the thing that
 * differs between them.
 */
function MovementLine({ m, name }: { m: stockapi.Movement; name: string | null }) {
  const why = m.reason_label ?? m.reason ?? 'no reason on the line';
  return (
    <div className="st-mv">
      <span className="tm">{stockapi.when(m.at)}</span>
      <span className="what">
        <span className="nm">{name ?? why}</span>
        <span className="rs">
          {name === null ? m.note : <>{why}{m.note && <> — {m.note}</>}</>}
        </span>
      </span>
      <span className="u tnum">
        <Dir kind={m.kind} />
        <span className="n">{signed(m.units)}</span>
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------ screen -- */

export default function Stock() {
  const [body, setBody] = useState<stockapi.StockBody | null>(null);
  const [low, setLow] = useState<stockapi.LowBody | null>(null);
  /** Why the low list could not be read. An unreadable list and an empty one
      are different facts, and this page used to print one sentence for both. */
  const [lowErr, setLowErr] = useState<{ reason: string; detail?: string } | null>(null);
  /** Whether the reorder list is being drafted for a wholesaler. */
  const [sharingList, setSharingList] = useState(false);
  const [log, setLog] = useState<stockapi.MovementsBody | null>(null);
  const [err, setErr] = useState<{ reason: string; detail?: string } | null>(null);
  const [logErr, setLogErr] = useState<{ reason: string; detail?: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [logLoading, setLogLoading] = useState(true);

  /** The product every form on this page is about. One subject, not four. */
  const [sku, setSku] = useState<string>('');

  const [dir, setDir] = useState<stockapi.Direction>('in');
  const [qty, setQty] = useState('1');
  const [reason, setReason] = useState('delivery');
  const [note, setNote] = useState('');
  const [moving, setMoving] = useState(false);
  const [moveErr, setMoveErr] = useState<{ reason: string; detail?: string } | null>(null);
  const [moved, setMoved] = useState<stockapi.MovementRecorded | null>(null);

  const [countDraft, setCountDraft] = useState('');
  const [counting, setCounting] = useState(false);
  const [countErr, setCountErr] = useState<{ reason: string; detail?: string } | null>(null);
  const [counted, setCounted] = useState<stockapi.CountRecorded | null>(null);

  const [levelDraft, setLevelDraft] = useState('');
  const [levelling, setLevelling] = useState(false);
  const [levelErr, setLevelErr] = useState<{ reason: string; detail?: string } | null>(null);
  const [levelled, setLevelled] = useState<stockapi.LevelSet | null>(null);

  /** Whether the log shows every product or only the one chosen above. */
  const [logAll, setLogAll] = useState(false);

  const load = useCallback(async () => {
    const [s, l] = await Promise.all([stockapi.list(), stockapi.low()]);
    if (s.ok) { setBody(s); setErr(null); } else { setErr(s); setBody(null); }
    // A REFUSAL IS NOT AN EMPTY LIST, and it is not a paraphrase either. The
    // server's own reason goes on the screen; `setLow(l.ok ? l : null)` threw
    // it away and left a sentence this page had written itself.
    if (l.ok) { setLow(l); setLowErr(null); } else { setLow(null); setLowErr(l); }
    setLoading(false);
  }, []);

  useEffect(() => { void load(); }, [load]);

  const filter = logAll || !sku ? null : sku;

  const loadLog = useCallback(async () => {
    setLogLoading(true);
    const r = await stockapi.movements({ sku: filter, limit: 60 });
    if (r.ok) { setLog(r); setLogErr(null); } else { setLogErr(r); setLog(null); }
    setLogLoading(false);
  }, [filter]);

  useEffect(() => { void loadLog(); }, [loadLog]);

  const rows = useMemo(() => body?.items ?? [], [body]);
  const row = useMemo(() => rows.find((r) => r.sku_id === sku) ?? null, [rows, sku]);
  const names = useMemo(() => {
    const map: Record<string, string> = {};
    for (const r of rows) map[r.sku_id] = r.name ?? r.sku_id;
    return map;
  }, [rows]);

  /* The vocabulary comes from the server. A reason this page invented would be
     refused by name, so there is no list of reasons written down here. */
  const reasons = useMemo(() => {
    const table = dir === 'in' ? body?.reasons?.in : body?.reasons?.out;
    return Object.entries(table ?? {});
  }, [body, dir]);

  /** Choosing a product clears the answers that belonged to the last one. */
  const pick = useCallback((next: string) => {
    setSku(next);
    setMoved(null);
    setMoveErr(null);
    setCounted(null);
    setCountErr(null);
    setLevelled(null);
    setLevelErr(null);
    setCountDraft('');
    setLevelDraft('');
    setLogAll(false);
  }, []);

  /* Stock IN is a delivery nearly every time, so it is defaulted. Stock OUT is
     breakage, expiry, theft or a packet taken for the house, and those are
     genuinely different things a month later — the log cannot be edited, so the
     page asks rather than guesses. */
  const changeDir = useCallback((next: stockapi.Direction) => {
    setDir(next);
    setReason(next === 'in' ? 'delivery' : '');
    setMoved(null);
    setMoveErr(null);
  }, []);

  const bump = useCallback((by: number) => {
    setQty((q) => {
      const n = Number(/^\d+$/.test(q.trim()) ? q.trim() : '0') + by;
      return String(n < 1 ? 1 : n);
    });
  }, []);

  const record = useCallback(async () => {
    setMoved(null);
    if (!sku) {
      setMoveErr({ reason: 'No product chosen', detail: 'Pick the product this movement is about.' });
      return;
    }
    if (!reason) {
      setMoveErr({
        reason: 'No reason chosen',
        detail: 'Stock leaving the shelf has to say why. The log cannot be edited afterwards.',
      });
      return;
    }
    const parsed = stockapi.packets(qty, 'The quantity');
    if (!('units' in parsed)) { setMoveErr(parsed); return; }
    setMoving(true);
    const r = await stockapi.move(sku, dir, parsed.units, reason, note.trim() || undefined);
    setMoving(false);
    if (r.ok) {
      setMoved(r);
      setMoveErr(null);
      setNote('');
      setQty('1');
      await Promise.all([load(), loadLog()]);
    } else {
      setMoveErr(r);
    }
  }, [sku, dir, qty, reason, note, load, loadLog]);

  const recount = useCallback(async () => {
    setCounted(null);
    if (!sku) return;
    const parsed = stockapi.packets(countDraft, 'The count');
    if (!('units' in parsed)) { setCountErr(parsed); return; }
    setCounting(true);
    const r = await stockapi.count(sku, parsed.units);
    setCounting(false);
    if (r.ok) {
      setCounted(r);
      setCountErr(null);
      setCountDraft('');
      await Promise.all([load(), loadLog()]);
    } else {
      setCountErr(r);
    }
  }, [sku, countDraft, load, loadLog]);

  const applyLevel = useCallback(async (clear: boolean) => {
    setLevelled(null);
    if (!sku) return;
    let units: number | null = null;
    if (!clear) {
      const parsed = stockapi.packets(levelDraft, 'The reorder level');
      if (!('units' in parsed)) { setLevelErr(parsed); return; }
      units = parsed.units;
    }
    setLevelling(true);
    const r = await stockapi.setLevel(sku, units);
    setLevelling(false);
    if (r.ok) {
      setLevelled(r);
      setLevelErr(null);
      setLevelDraft('');
      await load();
    } else {
      setLevelErr(r);
    }
  }, [sku, levelDraft, load]);

  const chain = body?.chain ?? log?.chain ?? null;
  const unreadable = body?.unreadable_movement_lines ?? 0;

  return (
    <div>
      <div className="page-head">
        <h1>Stock</h1>
        <p>
          Deliveries in, breakage and expiry out, and what is running low. What is on the shelf is
          your own count, plus what you record here, minus what this counter has billed since. It
          cannot see a packet that left the shop without either being billed or being written down.
        </p>
      </div>

      {err && (
        <div className="st-band">
          <Refusal
            reason="The stock figures could not be read"
            detail={err.reason}
            hint={err.detail}
            action={<Button size="sm" onClick={() => void load()}>TRY AGAIN</Button>}
          />
        </div>
      )}

      {chain && !chain.ok && (
        <div className="st-band">
          <Verdict tone="red" title="The movement log does not verify">
            Only the <b>{chain.lines_verified}</b> lines that stood up to a re-walk from the
            beginning are counted. Movements after the break are in no figure on this page, and a
            reorder level set after it has reverted to what it was before. Nothing has been
            estimated to cover the gap.
            <br />
            <span className="mono">{chain.error}</span>
          </Verdict>
        </div>
      )}

      {body?.bill_chain && !body.bill_chain.ok && (
        <div className="st-band">
          <Verdict tone="red" title="The bill chain does not verify">
            Every figure here subtracts what the counter has billed since your count, and that comes
            from the audit chain, which stops being evidence at line{' '}
            <b>{body.bill_chain.lines_verified}</b>. Stock on hand is therefore reported HIGH by
            whatever was billed after the break.
            <br />
            <span className="mono">{body.bill_chain.error}</span>
          </Verdict>
        </div>
      )}

      {unreadable > 0 && (
        <div className="st-band">
          <Verdict tone="amber" title={`${unreadable} lines on the log could not be read`}>
            A line whose direction and sign disagree cannot be believed, so it is not counted and not
            listed. The figures below are short by whatever those lines held.
          </Verdict>
        </div>
      )}

      {/* THE STRIP. Every number in it is a field the server sent; nothing here
          is added up in the browser. */}
      <div className="st-band">
        <StatGrid>
          <Stat
            label="Counted"
            value={body ? `${body.counted_skus}/${body.count}` : '—'}
            sub="products with a count to work from"
          />
          <Stat
            label="At or under level"
            value={body ? body.at_or_under_level : '—'}
            sub="on the shelf at or below what you set"
          />
          <Stat
            label="Below zero"
            value={body ? body.needs_recount : '—'}
            sub="more billed than the shelf could hold"
          />
          <Stat
            label="Levels set"
            value={low ? low.skus_with_a_level : '—'}
            sub={low ? `${low.skus_without_a_level} products have none` : 'a level is your judgement'}
          />
        </StatGrid>
      </div>

      <div className="grid st-grid">
        {/* ------------------------------------------------------ quick entry -- */}
        <Card
          title="Move stock"
          sub="your word, on a log that cannot be edited afterwards"
          aside={row ? <OnHand row={row} /> : null}
        >
          {loading ? (
            <LoadingCard lines={2} label="Reading the shelves" />
          ) : err ? (
            /* NOT "nothing is taught". The catalogue may be full; what failed is
               the reading of it, and an empty state here would be this page
               making a claim about a shop it could not see. */
            <Empty
              title="No shelves to move stock on"
              action={<Button variant="primary" onClick={() => void load()}>TRY AGAIN</Button>}
            >
              The stock figures could not be read, so there is nothing to choose from. The refusal at
              the top of the page carries the server's own words for why.
            </Empty>
          ) : rows.length === 0 ? (
            <Empty
              title="Nothing to move yet"
              action={<a className="btn primary st-linkbtn" href="#/products">TEACH A PRODUCT</a>}
            >
              A movement is a movement of something, and no product has been taught — so there is no
              shelf to move stock on or off. Teach one on the Products screen and it appears in this
              picker, whether or not it has ever been counted.
            </Empty>
          ) : (
            <div className="st-entry">
              {/* A native select, deliberately: on a phone held one-handed over a
                  crate, the operating system's own picker is a bigger and
                  steadier target than any list this page could draw. */}
              <Field label="Product" htmlFor="st-sku">
                <Select id="st-sku" value={sku} onChange={(e) => pick(e.target.value)}>
                  <option value="">Choose a product…</option>
                  {rows.map((r) => (
                    <option key={r.sku_id} value={r.sku_id}>
                      {r.name ?? r.sku_id}
                      {r.on_hand_units === null ? ' — not counted' : ` — ${r.on_hand_units} on hand`}
                    </option>
                  ))}
                </Select>
              </Field>

              <div className="st-dir">
                <Segmented
                  value={dir}
                  onChange={changeDir}
                  wide
                  options={[
                    { value: 'in', label: 'IN — it arrived', title: 'A delivery, a return, something found' },
                    { value: 'out', label: 'OUT — it left', title: 'Breakage, expiry, taken for the house' },
                  ]}
                />
                <span className="st-dir-note">
                  {dir === 'in'
                    ? 'Stock arriving on the shelf without going across the counter.'
                    : 'Stock leaving the shelf without being sold. A sale is already on the audit chain and must not be recorded twice.'}
                </span>
              </div>

              <div className="st-qty">
                <Button className="qbig" onClick={() => bump(-1)} aria-label="One fewer">−</Button>
                <Input
                  id="st-qty"
                  className="tnum st-qty-in"
                  type="text"
                  inputMode="numeric"
                  value={qty}
                  aria-label="How many packets"
                  onChange={(e) => setQty(e.target.value)}
                />
                <Button className="qbig" onClick={() => bump(1)} aria-label="One more">+</Button>
                <div className="st-jump">
                  {[6, 12, 24].map((n) => (
                    <Button key={n} size="sm" onClick={() => setQty(String(n))}>{n}</Button>
                  ))}
                </div>
              </div>

              <div className="st-reasons" role="group" aria-label="Why">
                {reasons.map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    className="st-reason"
                    aria-pressed={reason === key}
                    onClick={() => setReason(key)}
                  >
                    <span className="k">{key.replace(/_/g, ' ')}</span>
                    <span className="d">{label}</span>
                  </button>
                ))}
              </div>

              <Field label="Note (optional)" sub="Free text. Whose delivery, which shelf, what happened.">
                <Input
                  type="text"
                  value={note}
                  maxLength={200}
                  placeholder={dir === 'in' ? 'e.g. Sharma Traders, morning van' : 'e.g. crate dropped at the door'}
                  onChange={(e) => setNote(e.target.value)}
                />
              </Field>

              <Button
                variant="primary"
                size="lg"
                block
                loading={moving}
                disabled={!sku}
                aria-describedby={!sku ? 'st-why-record' : undefined}
                onClick={() => void record()}
              >
                {dir === 'in' ? 'RECORD STOCK IN' : 'RECORD STOCK OUT'}
              </Button>

              {/* A DISABLED CONTROL SAYS WHY, and offers the way out of it. */}
              {!sku && (
                <WhyDead id="st-why-record">
                  No product is chosen, and a movement has to be a movement of something.{' '}
                  <button type="button" className="st-jumplink" onClick={() => jumpTo('st-sku')}>
                    Choose one above.
                  </button>
                </WhyDead>
              )}

              <p className="hint">
                This is your word rather than something the counter saw. It goes on a hash-chained
                log and cannot be edited or deleted — a mistake is corrected with an opposite
                movement carrying the reason <b>correction</b>, and both lines stay.
              </p>

              {moveErr && <Refusal reason={moveErr.reason} detail={moveErr.detail} />}

              {moved && (
                <Verdict
                  tone="info"
                  title={`Recorded: ${signed(moved.units)} ${names[moved.sku_id] ?? moved.sku_id}`}
                >
                  {moved.detail}
                  {moved.derivation && <><br /><span className="mono">{moved.derivation}</span></>}
                </Verdict>
              )}
            </div>
          )}
        </Card>

        {/* ------------------------------------------------------- right column -- */}
        <div className="stack">
          <Card
            title="Running low"
            sub="at or under the level you set"
            aside={low ? (
              <span className="st-low-aside">
                <span className="pill st-quiet-pill">{low.count} AT OR UNDER</span>
                {/* This list, as a message to a wholesaler. The server composes it
                    from the same low figures on screen, so what is sent and what
                    is shown cannot drift apart. */}
                {low.count > 0 && (
                  <button className="btn sm st-linkbtn" onClick={() => setSharingList(true)}>SEND LIST</button>
                )}
              </span>
            ) : null}
          >
            {loading ? (
              <SkeletonRows rows={2} cols={2} />
            ) : lowErr ? (
              /* THE SERVER'S OWN WORDS, BY NAME. This was an `Empty` carrying a
                 sentence this page wrote, which is the one thing a refusal must
                 never become: an absence looks like "nothing is low", and
                 "nothing is low" is a claim about a shop nobody could read. */
              <Refusal
                reason="The low list could not be read"
                detail={lowErr.reason}
                hint={lowErr.detail}
                action={<Button size="sm" onClick={() => void load()}>TRY AGAIN</Button>}
              />
            ) : !low ? (
              <Empty title="The low list could not be read">
                Nothing has been substituted for it, so what is at or under a level cannot be said
                until it reads again.
              </Empty>
            ) : (
              <div className="stack st-lowstack">
                {low.low.length === 0 && low.unknown.length === 0 && low.needs_recount.length === 0 ? (
                  /* THE STATE THIS SHOP IS ACTUALLY IN. Four products, not one
                     reorder level between them — so this panel is empty every
                     day until somebody sets one, and the empty state is the
                     whole panel rather than a caption on it. It says what would
                     be here, why nothing is, and hands over the control. */
                  low.skus_with_a_level === 0 ? (
                    <Empty
                      title="No reorder level is set yet"
                      action={
                        <Button
                          variant="primary"
                          onClick={() => jumpTo(sku ? 'st-level' : 'st-sku')}
                        >
                          {sku ? 'SET THE LEVEL' : 'CHOOSE A PRODUCT'}
                        </Button>
                      }
                    >
                      This is where a product sitting at or under the level you set for it appears,
                      with what is on hand and how many days it lasts — the list you would take to a
                      wholesaler. Nothing can be called low until you say what low means for it, and
                      no level is set on any of your {low.skus_without_a_level} products. The counter
                      will not propose one: a number it worked out from a fortnight of trade would be
                      sitting in a field that says you chose it.
                    </Empty>
                  ) : (
                    <Empty title="Nothing is low">
                      Nothing is at or under the level you set. {low.skus_without_a_level > 0 ? (
                        <>
                          {low.skus_without_a_level} product
                          {low.skus_without_a_level === 1 ? ' has' : 's have'} no level, so
                          {low.skus_without_a_level === 1 ? ' it is' : ' they are'} not on this list
                          either way.
                        </>
                      ) : null}
                    </Empty>
                  )
                ) : null}

                {low.low.map((r) => (
                  <button key={r.sku_id} className="st-lowrow" onClick={() => pick(r.sku_id)}>
                    <span className="nm">{r.name ?? r.sku_id}</span>
                    <span className="fig">
                      <OnHand row={r} />
                      <span className="lvl">on hand · reorder at {r.reorder_level}</span>
                    </span>
                    <span className="cov"><Cover cover={r.cover} /></span>
                  </button>
                ))}

                {low.unknown.length > 0 && (
                  <div className="st-sub">
                    <span className="eyebrow">A level, but no count</span>
                    {low.unknown.map((u) => (
                      <button key={u.sku_id} className="st-lowrow quiet" onClick={() => pick(u.sku_id)}>
                        <span className="nm">{u.name ?? u.sku_id}</span>
                        <span className="fig">
                          <span className="st-absent">not counted</span>
                          <span className="lvl">reorder at {u.reorder_level}</span>
                        </span>
                        <span className="cov st-why-inline">{u.why}</span>
                      </button>
                    ))}
                  </div>
                )}

                {low.needs_recount.length > 0 && (
                  <div className="st-sub">
                    <span className="eyebrow">Below zero — count these again</span>
                    {low.needs_recount.map((r) => (
                      <button key={r.sku_id} className="st-lowrow quiet" onClick={() => pick(r.sku_id)}>
                        <span className="nm">{r.name ?? r.sku_id}</span>
                        <span className="fig">
                          <OnHand row={r} />
                          <span className="lvl">on hand</span>
                        </span>
                        <span className="cov st-why-inline">
                          More has been billed than the shelf could hold, so something left without
                          being recorded.
                        </span>
                      </button>
                    ))}
                  </div>
                )}

                <p className="hint" style={{ marginTop: 0 }}>{low.note}</p>
              </div>
            )}
          </Card>

          <Card
            title={row ? (row.name ?? row.sku_id) : 'This product'}
            sub={row ? row.sku_id : 'count a shelf, and set when to reorder it'}
            aside={row?.at_or_under_reorder_level ? <span className="pill st-low-pill">AT LEVEL</span> : null}
          >
            {loading ? (
              <LoadingCard lines={3} label="Reading the shelves" />
            ) : !row ? (
              <Empty
                title="No product chosen"
                /* Not `primary`: the card above it is empty for the same reason
                   and already carries the primary call. Two identical blue
                   buttons stacked one above the other read as a repeat, not as
                   two choices. */
                action={
                  rows.length === 0
                    ? <a className="btn st-linkbtn" href="#/products">TEACH A PRODUCT</a>
                    : <Button onClick={() => jumpTo('st-sku')}>CHOOSE A PRODUCT</Button>
                }
              >
                {rows.length === 0
                  ? 'Nothing has been taught, so there is no shelf to count and no level to set.'
                  : 'This is where one product’s own figure lives: what is on the shelf, the sum that reached it, when it was last counted, and the level it should be reordered at. Choose a product and it fills in.'}
              </Empty>
            ) : (
              <div className="stack st-detail">
                <div className="st-hero">
                  <OnHand row={row} big />
                  <span className="st-hero-l">
                    {row.on_hand_units === null ? 'nobody has counted this shelf' : 'on the shelf'}
                  </span>
                </div>

                <p className="st-derivation mono">{row.derivation}</p>

                {row.needs_recount && (
                  <Verdict tone="amber" title="This has gone below zero">
                    A shelf cannot hold fewer than none, so something has left without being billed
                    and without being written down. The figure is shown as it stands rather than
                    quietly corrected. Count it and this starts again from your number.
                  </Verdict>
                )}

                <div className="st-facts">
                  <KV k="counted">
                    {row.counted_units === null
                      ? <span className="st-absent">never</span>
                      : <>{row.counted_units} on {stockapi.when(row.counted_at)}</>}
                  </KV>
                  <KV k="billed since">{row.billed_since_count ?? <span className="st-absent">—</span>}</KV>
                  <KV k="recorded since">
                    {row.movements_since_count === 0
                      ? <span className="st-absent">nothing</span>
                      : <>+{row.units_in_since_count} in, −{row.units_out_since_count} out</>}
                  </KV>
                  <KV k="days of cover"><Cover cover={row.cover} /></KV>
                </div>

                {/* Guarded, like `Cover` above it is: a renamed field must not
                    be able to take the screen down between the two of them. */}
                {row.cover?.why && <p className="st-why-block">{row.cover.why}</p>}

                {row.movements_superseded_by_count > 0 && (
                  <p className="hint" style={{ marginTop: 0 }}>
                    {row.movements_superseded_by_count} movement
                    {row.movements_superseded_by_count === 1 ? '' : 's'} recorded before your count
                    are still on the log and no longer count towards the figure. Your own eyes beat
                    the derivation.
                  </p>
                )}

                {/* ----------------------------------------------- count the shelf */}
                <div className="st-form">
                  <Field
                    label="Count this shelf"
                    sub="A new baseline. What you count now replaces everything above it."
                  >
                    <div className="st-inline">
                      <Input
                        type="text"
                        inputMode="numeric"
                        className="tnum"
                        value={countDraft}
                        placeholder="packets on the shelf"
                        onChange={(e) => setCountDraft(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') void recount(); }}
                      />
                      <Button loading={counting} onClick={() => void recount()}>SAVE COUNT</Button>
                    </div>
                  </Field>
                  {countErr && <Refusal reason={countErr.reason} detail={countErr.detail} />}
                  {counted && (
                    <Verdict
                      tone={counted.discrepancy_units !== null && counted.discrepancy_units !== 0 ? 'amber' : 'info'}
                      title={`Counted ${counted.counted_units}`}
                    >
                      {counted.detail}
                      {!counted.audited && counted.audit_error && (
                        <>
                          <br />
                          The count is on disk. The audit line is not:{' '}
                          <span className="mono">{counted.audit_error}</span>
                        </>
                      )}
                    </Verdict>
                  )}
                </div>

                {/* ---------------------------------------------- reorder level -- */}
                <div className="st-form">
                  <Field
                    label="Reorder level"
                    htmlFor="st-level"
                    sub={
                      row.reorder_level === null
                        ? 'None set. This product will not appear on the low list.'
                        : `Set to ${row.reorder_level} on ${stockapi.when(row.reorder_level_set_at)}.`
                    }
                  >
                    <div className="st-inline">
                      <Input
                        id="st-level"
                        type="text"
                        inputMode="numeric"
                        className="tnum"
                        value={levelDraft}
                        aria-label="Reorder level, in packets"
                        placeholder={row.reorder_level === null ? 'tell me at' : String(row.reorder_level)}
                        onChange={(e) => setLevelDraft(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') void applyLevel(false); }}
                      />
                      <Button loading={levelling} onClick={() => void applyLevel(false)}>SET</Button>
                      {row.reorder_level !== null && (
                        <Button variant="ghost" disabled={levelling} onClick={() => void applyLevel(true)}>
                          CLEAR
                        </Button>
                      )}
                    </div>
                  </Field>
                  <p className="hint" style={{ marginTop: 0 }}>
                    The level is your judgement about your own shelf. This counter does not propose
                    one: a number it derived from a fortnight of trade would be sitting in a field
                    that says you chose it.
                  </p>
                  {levelErr && <Refusal reason={levelErr.reason} detail={levelErr.detail} />}
                  {levelled && <Verdict tone="info" title="Level saved">{levelled.detail}</Verdict>}
                </div>
              </div>
            )}
          </Card>
        </div>

        {/* ------------------------------------------------- the movement log --
            The grid's third child. On a wide screen CSS places it under the
            entry pad; on a phone the grid is one column and it simply follows. */}
        <Card
          title="The movement log"
          sub={filter ? `only ${names[filter] ?? filter}` : 'every product, newest first'}
          aside={
            sku ? (
              <Segmented
                size="sm"
                value={logAll ? 'all' : 'one'}
                onChange={(v) => setLogAll(v === 'all')}
                options={[
                  { value: 'one', label: 'THIS PRODUCT' },
                  { value: 'all', label: 'EVERYTHING' },
                ]}
              />
            ) : (
              <span className="st-quiet">{log ? `${log.matched} recorded` : ''}</span>
            )
          }
        >
          {logLoading ? (
            <SkeletonRows rows={4} cols={3} />
          ) : logErr ? (
            <Refusal
              reason="The movement log could not be read"
              detail={logErr.reason}
              hint={logErr.detail}
              action={<Button size="sm" onClick={() => void loadLog()}>TRY AGAIN</Button>}
            />
          ) : (log?.movements.length ?? 0) === 0 ? (
            <Empty
              title="Nothing recorded yet"
              action={
                rows.length === 0
                  ? <a className="btn st-linkbtn" href="#/products">TEACH A PRODUCT</a>
                  : <Button onClick={() => jumpTo(sku ? 'st-qty' : 'st-sku')}>RECORD A MOVEMENT</Button>
              }
            >
              {filter
                ? 'Nothing has been recorded against this product, so its figure is your count minus what the counter has billed since it — a delivery that arrived and was never written down is not in it.'
                : 'Every delivery, breakage, expiry and packet taken for the house appears here, newest first, on a hash-chained log that cannot be edited. None has been recorded yet, so every shelf figure above is your own count minus what the counter has billed since it.'}
            </Empty>
          ) : (
            <>
              <div className="st-log">
                {log?.movements.map((m) => (
                  <MovementLine
                    key={m.movement_id ?? `${m.at}-${m.sku_id}-${m.units}`}
                    m={m}
                    name={filter ? null : names[m.sku_id] ?? m.sku_id}
                  />
                ))}
              </div>
              {log && log.matched > log.count && (
                <p className="hint">
                  The {log.count} most recent of {log.matched}. Older lines are on the log and are
                  still counted in the figures above.
                </p>
              )}
            </>
          )}
        </Card>
      </div>

      {/* -------------------------------------------------------- every shelf -- */}
      <div className="st-band top">
        <Card
          title="Every shelf"
          sub="what is on hand, and how long it lasts"
          aside={body ? <span className="pill st-quiet-pill">{body.count} PRODUCTS</span> : null}
        >
          {loading ? (
            <ShelfTableSkeleton />
          ) : err ? (
            <Empty
              title="The shelves could not be read"
              action={<Button variant="primary" onClick={() => void load()}>TRY AGAIN</Button>}
            >
              This is not a statement that the catalogue is empty — the request failed, and the
              refusal at the top of the page carries the server's own words for how.
            </Empty>
          ) : rows.length === 0 ? (
            <Empty
              title="Nothing taught yet"
              action={<a className="btn primary st-linkbtn" href="#/products">TEACH A PRODUCT</a>}
            >
              Every product in the catalogue is listed here with what is on hand, the level it is
              reordered at and how long it lasts — whether or not it has ever been counted. Teach one
              on the Products screen and its row appears, showing “not counted” rather than a zero,
              because a zero would be a claim about a shelf nobody has looked at.
            </Empty>
          ) : (
            <div className="scroll-x">
              {/* `tbl-cards`: under 560 each row restacks into a card. At 390
                  this table ran 127 px past the viewport inside its scroller
                  AND bought that back by deleting its last two columns with
                  `hide-sm` — so "Since your count" and "Last movement" were on
                  no screen a phone could reach. Restacked, both come back. */}
              <table className="moments st-table tbl-cards">
                <thead>
                  <tr>
                    <th>Product</th>
                    <th className="r">On hand</th>
                    <th className="r" title="It appears on the low list at or under this.">Reorder at</th>
                    <th className="r" title="From what the counter has billed. Blind to anything unbilled.">Cover</th>
                    <th className="r hide-sm">Since your count</th>
                    <th className="hide-sm">Last movement</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.sku_id} className={r.sku_id === sku ? 'picked' : undefined}>
                      <td data-label="Product">
                        <button className="st-pick" onClick={() => pick(r.sku_id)}>
                          <span className="nm">{r.name ?? r.sku_id}</span>
                          <span className="sku mono">{r.sku_id}</span>
                        </button>
                      </td>
                      <td className="r" data-label="On hand"><OnHand row={r} /></td>
                      <td className="r tnum" data-label="Reorder at">
                        {r.reorder_level === null
                          ? <span className="st-absent">none set</span>
                          : r.reorder_level}
                      </td>
                      <td className="r" data-label="Cover"><Cover cover={r.cover} /></td>
                      <td className="r tnum hide-sm" data-label="Since your count">
                        {r.movements_since_count === 0
                          ? <span className="st-absent">—</span>
                          : <>+{r.units_in_since_count} / −{r.units_out_since_count}</>}
                      </td>
                      <td className="hide-sm" data-label="Last movement">
                        {r.last_movement_at
                          ? stockapi.when(r.last_movement_at)
                          : <span className="st-absent">never</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {body && <p className="hint">{body.note}</p>}
        </Card>
      </div>

      {body && body.moved_but_not_in_catalogue.length > 0 && (
        <div className="st-band top">
          <Card
            title="Movements against products that have left the catalogue"
            aside={<span className="pill st-quiet-pill">{body.moved_but_not_in_catalogue.length} TO RECONCILE</span>}
          >
            <p className="hint" style={{ marginTop: 0 }}>
              A reconciliation note rather than a fault. These deliveries and losses are on the log,
              and the product they were booked against is no longer in the catalogue — renamed,
              removed, or taught during a demonstration. They are listed because otherwise the log
              stops adding up to the shelves above it and there would be nowhere to look for the
              difference.
            </p>
            {body.moved_but_not_in_catalogue.map((o) => (
              <KV k={<span className="mono">{o.sku_id}</span>} key={o.sku_id}>
                {o.movements} movement{o.movements === 1 ? '' : 's'} · +{o.units_in} in / −{o.units_out} out
              </KV>
            ))}
          </Card>
        </div>
      )}

      <ShareSheet
        open={sharingList}
        onClose={() => setSharingList(false)}
        target={{ kind: 'reorder' }}
      />
    </div>
  );
}
