import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import * as exapi from '../lib/expiryapi';
import {
  Button, Card, Checkbox, Empty, Field, Input, LoadingCard, Modal, Refusal, Segmented,
  Select, SkeletonRows, Stat, StatGrid, Table, Verdict, type Column,
} from '../components/ui';
import '../styles/expiry.css';

/**
 * Expiry — what goes off, and what it is worth when it does.
 *
 * THE SCREEN LEADS WITH THE LIST. A shopkeeper opens this page to learn one
 * thing — what to clear first — so the first card is that list and nothing
 * else: what has already gone off at the top, then what goes off within the
 * window, soonest first, each line with the units left, the date, and what the
 * packets are marked at. Booking a batch is the second card. Everything else
 * is below the fold.
 *
 * THREE THINGS THIS SCREEN IS HONEST ABOUT, because each is a place a page
 * like this normally lies:
 *
 *  1. THE VALUE IS A DESCRIPTION, NOT A CHARGE. Every rupee here is a string
 *     the server rendered from units × the marked price in the catalogue. It
 *     is what the packets would fetch if every one sold; it is on no bill and
 *     it is not a loss the books record. The page repeats the server's own
 *     sentence saying so beside the figure, and a product with no price shows
 *     "no price", never a zero.
 *  2. THE COUNTER DOES NOT KNOW WHICH BATCH A SOLD PACKET CAME FROM. A batch
 *     stays at its booked units until a person says some sold through or some
 *     went off. The "left" figure is therefore the shopkeeper's word, and the
 *     page says so rather than pretending to have watched the shelf.
 *  3. A WRITE-OFF MOVES THE STOCK FIGURE ONLY IF THE STOCK LOG TOOK IT. The
 *     server writes the stock OUT through the stock module's own path and
 *     reports whether it landed. When it did not, this page says the Stock
 *     screen's figure has not moved and the shelf needs a re-count — in the
 *     server's words, not a softened paraphrase.
 *
 * COLOUR. Green, amber and red belong to money and recognition state on this
 * product, and a packet past its date is neither. So the expired rows are set
 * QUIET AND HEAVY — the darkest ink, a thick rule down the side, the word
 * EXPIRED — rather than red. Amber is earned by exactly one thing here: a
 * write-off whose stock line did not land, which is a figure on another screen
 * that is now wrong. Red is kept for a hash chain that does not verify.
 */

type Note = { reason: string; detail?: string } | null;

/* --------------------------------------------------------------- fragments -- */

/**
 * WHY A CONTROL IS DEAD, WHERE THE HAND IS.
 *
 * BOOK BATCH is dead every time this page is opened, because no product is
 * chosen yet, and it used to say nothing at all. Quiet ink: a control that is
 * not applicable yet is not a refusal, and amber and red are spoken for.
 */
function WhyDead({ id, children }: { id: string; children: ReactNode }) {
  return <p className="ex-whydead" id={id}>{children}</p>;
}

/**
 * Put the cursor where the sentence just sent it — and nowhere else. Writes
 * nothing, decides nothing.
 */
function jumpTo(id: string): void {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ block: 'center', behavior: 'smooth' });
  (el as HTMLElement).focus({ preventScroll: true });
}

/** The days-left phrase, worded from the server's number. */
function When({ b }: { b: exapi.Batch }) {
  const soon = b.state === 'open' && b.days_left <= 1;
  return (
    <span className={`ex-when${b.state === 'expired' ? ' gone' : soon ? ' soon' : ''}`}>
      {b.state === 'expired' ? (
        <><b>EXPIRED</b> · {exapi.daysWord(b.days_left)}</>
      ) : b.state === 'closed' ? (
        <>closed · was {exapi.onDay(b.expires_on)}</>
      ) : (
        <><b>{exapi.daysWord(b.days_left)}</b> · {exapi.onDay(b.expires_on)}</>
      )}
    </span>
  );
}

/**
 * The rupee string the server sent, or the server's reason for not having
 * one. Nothing here formats paise: `value_at_risk_rupees` arrives rendered.
 */
function Value({ b, label }: { b: exapi.Batch; label?: boolean }) {
  if (b.value_at_risk_rupees === null || b.value_at_risk_rupees === undefined) {
    return (
      <span className="ex-absent" title={b.value_why ?? undefined}>
        no price
      </span>
    );
  }
  return (
    <span className="ex-val">
      <b className="tnum">₹{b.value_at_risk_rupees}</b>
      {label && <span className="ex-val-l">at marked price</span>}
    </span>
  );
}

function Left({ b }: { b: exapi.Batch }) {
  return (
    <span className="ex-left">
      <b className="tnum">{b.units_remaining}</b>
      <span className="ex-left-of"> of {b.units}</span>
    </span>
  );
}

/** One line of the lead list. */
function Row({ b, onWriteOff, onSold }: {
  b: exapi.Batch;
  onWriteOff: (b: exapi.Batch) => void;
  onSold: (b: exapi.Batch) => void;
}) {
  return (
    <div className={`ex-row ${b.state}`}>
      <div className="ex-row-main">
        <When b={b} />
        <span className="ex-name">{b.name}</span>
        <span className="ex-meta">
          <Left b={b} /> left{!b.in_catalogue && ' · no longer in the catalogue'}
          {b.note && <> · {b.note}</>}
        </span>
      </div>
      <div className="ex-row-val"><Value b={b} label /></div>
      <div className="ex-row-act">
        <Button size="sm" onClick={() => onWriteOff(b)}>WRITE OFF</Button>
        <Button size="sm" variant="ghost" onClick={() => onSold(b)}>SOLD</Button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ screen -- */

type WindowDays = '3' | '7' | '14' | '30';

export default function Expiry() {
  const [over, setOver] = useState<exapi.Overview | null>(null);
  const [err, setErr] = useState<Note>(null);
  const [loading, setLoading] = useState(true);
  const [windowDays, setWindowDays] = useState<WindowDays>('7');

  /* every batch */
  const [all, setAll] = useState<exapi.BatchesBody | null>(null);
  const [allErr, setAllErr] = useState<Note>(null);
  const [allLoading, setAllLoading] = useState(true);
  const [filterSku, setFilterSku] = useState('');
  const [withClosed, setWithClosed] = useState(false);

  /* booking */
  const [sku, setSku] = useState('');
  const [qty, setQty] = useState('1');
  const [expiresOn, setExpiresOn] = useState('');
  const [note, setNote] = useState('');
  const [stockIn, setStockIn] = useState(false);
  const [booking, setBooking] = useState(false);
  const [bookErr, setBookErr] = useState<Note>(null);
  const [booked, setBooked] = useState<exapi.Booked | null>(null);

  /* the action dialog: write off or mark sold */
  const [act, setAct] = useState<{ kind: 'write-off' | 'sold'; b: exapi.Batch } | null>(null);
  const [actUnits, setActUnits] = useState('');
  const [actNote, setActNote] = useState('');
  const [acting, setActing] = useState(false);
  const [actErr, setActErr] = useState<Note>(null);
  const [acted, setActed] = useState<exapi.WrittenOff | exapi.Sold | null>(null);

  const load = useCallback(async () => {
    const r = await exapi.overview(Number(windowDays));
    if (r.ok) { setOver(r); setErr(null); } else { setErr(r); setOver(null); }
    setLoading(false);
  }, [windowDays]);

  useEffect(() => { void load(); }, [load]);

  const loadAll = useCallback(async () => {
    setAllLoading(true);
    const r = await exapi.batches({ sku: filterSku || null, includeClosed: withClosed });
    if (r.ok) { setAll(r); setAllErr(null); } else { setAllErr(r); setAll(null); }
    setAllLoading(false);
  }, [filterSku, withClosed]);

  useEffect(() => { void loadAll(); }, [loadAll]);

  const reload = useCallback(async () => { await Promise.all([load(), loadAll()]); }, [load, loadAll]);

  const products = useMemo(() => over?.products ?? [], [over]);

  const bump = useCallback((by: number) => {
    setQty((q) => {
      const n = Number(/^\d+$/.test(q.trim()) ? q.trim() : '0') + by;
      return String(n < 1 ? 1 : n);
    });
  }, []);

  /* A date N days from the SERVER'S today, for the three common shelf lives.
     Not money, and not a decision: the server re-reads the date it is sent. */
  const jumpDate = useCallback((days: number) => {
    const base = over?.today;
    const m = base ? /^(\d{4})-(\d{2})-(\d{2})$/.exec(base) : null;
    const d = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date();
    d.setDate(d.getDate() + days);
    const y = d.getFullYear();
    const mo = String(d.getMonth() + 1).padStart(2, '0');
    const da = String(d.getDate()).padStart(2, '0');
    setExpiresOn(`${y}-${mo}-${da}`);
  }, [over]);

  const book = useCallback(async () => {
    setBooked(null);
    if (!sku) {
      setBookErr({ reason: 'No product chosen', detail: 'Pick the product this batch is of.' });
      return;
    }
    const u = exapi.packets(qty, 'The units');
    if (!('units' in u)) { setBookErr(u); return; }
    const d = exapi.dateField(expiresOn);
    if (!('date' in d)) { setBookErr(d); return; }
    setBooking(true);
    const r = await exapi.book(sku, u.units, d.date, { note: note.trim() || undefined, stockIn });
    setBooking(false);
    if (r.ok) {
      setBooked(r);
      setBookErr(null);
      setNote('');
      setQty('1');
      setExpiresOn('');
      setStockIn(false);
      await reload();
    } else {
      setBookErr(r);
    }
  }, [sku, qty, expiresOn, note, stockIn, reload]);

  const openAct = useCallback((kind: 'write-off' | 'sold', b: exapi.Batch) => {
    setAct({ kind, b });
    setActUnits(String(b.units_remaining));
    setActNote('');
    setActErr(null);
  }, []);

  const confirmAct = useCallback(async () => {
    if (!act) return;
    const u = exapi.packets(actUnits, 'The units');
    if (!('units' in u)) { setActErr(u); return; }
    setActing(true);
    const opts = { units: u.units, note: actNote.trim() || undefined };
    const r = act.kind === 'write-off'
      ? await exapi.writeOff(act.b.batch_id, opts)
      : await exapi.sold(act.b.batch_id, opts);
    setActing(false);
    if (r.ok) {
      setActed(r);
      setAct(null);
      await reload();
    } else {
      setActErr(r);
    }
  }, [act, actUnits, actNote, reload]);

  const chain = over?.chain ?? all?.chain ?? null;
  const unreadable = over?.unreadable_lines ?? 0;
  const nothingSoon = !!over && over.expired.length === 0 && over.soon.length === 0;

  /* ---------------------------------------------------- every-batch columns -- */
  const cols = useMemo<Column<exapi.Batch>[]>(() => [
    {
      key: 'name', head: 'Product',
      cell: (b) => (
        <span className="ex-cell-name">
          <span className="nm">{b.name}</span>
          {/* Not `.sku`: that is the Products screen's product card, border
              and hover lift included. */}
          <span className="ex-sku">{b.sku_id}</span>
        </span>
      ),
    },
    { key: 'left', head: 'Left', num: true, cell: (b) => <Left b={b} /> },
    { key: 'when', head: 'Expires', cell: (b) => <When b={b} /> },
    {
      key: 'value', head: 'At marked price', num: true,
      cell: (b) => (b.state === 'closed' ? <span className="ex-absent">—</span> : <Value b={b} />),
    },
    { key: 'booked', head: 'Booked', drop: true, cell: (b) => exapi.when(b.recorded_at) },
    {
      key: 'act', head: '', className: 'ex-cell-act',
      cell: (b) => (b.state === 'closed' ? null : (
        <span className="ex-cell-btns">
          <Button size="sm" onClick={() => openAct('write-off', b)}>WRITE OFF</Button>
          <Button size="sm" variant="ghost" onClick={() => openAct('sold', b)}>SOLD</Button>
        </span>
      )),
    },
  ], [openAct]);

  return (
    <div>
      <div className="page-head">
        <h1>Expiry</h1>
        <p>
          Dahi, bread, milk, biscuits: what goes off, and when. Book the date on the packet when a
          delivery arrives, and this page ranks what to clear first. The value beside each line is
          what the packets are marked at — a description of what is on the shelf, not a charge and
          not a loss the books record.
        </p>
      </div>

      {err && (
        <div className="ex-band">
          <Refusal
            reason="The expiry list could not be read"
            detail={err.reason}
            hint={err.detail}
            action={<Button size="sm" onClick={() => void load()}>TRY AGAIN</Button>}
          />
        </div>
      )}

      {chain && !chain.ok && (
        <div className="ex-band">
          <Verdict tone="red" title="The batch log does not verify">
            Only the <b>{chain.lines_verified}</b> lines that stood up to a re-walk from the
            beginning are counted. Batches after the break are on no list on this page. Nothing has
            been estimated to cover the gap.
            <br />
            <span className="mono">{chain.error}</span>
          </Verdict>
        </div>
      )}

      {unreadable > 0 && (
        <div className="ex-band">
          <Verdict tone="amber" title={`${unreadable} lines on the log could not be read`}>
            A line that names a batch this log never booked cannot be believed, so it is not
            counted. The figures below are short by whatever those lines held.
          </Verdict>
        </div>
      )}

      {over && !over.stock_link.available && (
        <div className="ex-band">
          <Verdict tone="amber" title="A write-off here will not move the Stock figure">
            {over.stock_link.detail}
          </Verdict>
        </div>
      )}

      {/* THE STRIP. Every figure is a field the server sent; the rupee strings
          were rendered there. Nothing is added up in the browser. */}
      <div className="ex-band">
        <StatGrid>
          <Stat
            label="Past their date"
            value={over ? over.counts.expired : '—'}
            sub="batches with units still on them"
          />
          <Stat
            label={`Within ${windowDays} days`}
            value={over ? over.counts.soon : '—'}
            sub={over ? `as of ${exapi.onDay(over.today)}` : 'soonest first'}
          />
          <Stat
            label="Marked value, expired"
            value={over ? `₹${over.value_at_risk.expired_rupees}` : '—'}
            sub={
              over && over.value_at_risk.expired_unpriced_batches > 0
                ? `plus ${over.value_at_risk.expired_unpriced_batches} with no price · not a charge`
                : 'a description, not a charge'
            }
          />
          <Stat
            label={`Marked value, next ${windowDays} days`}
            value={over ? `₹${over.value_at_risk.soon_rupees}` : '—'}
            sub={
              over && over.value_at_risk.soon_unpriced_batches > 0
                ? `plus ${over.value_at_risk.soon_unpriced_batches} with no price · not a charge`
                : 'a description, not a charge'
            }
          />
        </StatGrid>
      </div>

      <div className="grid ex-grid">
        {/* -------------------------------------------------------- the list -- */}
        <Card
          title="Going off"
          sub={over ? `soonest first, as of ${exapi.onDay(over.today)} on this machine's calendar` : 'soonest first'}
          aside={
            <Segmented
              size="sm"
              value={windowDays}
              onChange={(v) => setWindowDays(v)}
              options={[
                { value: '3', label: '3 DAYS' },
                { value: '7', label: '7' },
                { value: '14', label: '14' },
                { value: '30', label: '30' },
              ]}
            />
          }
        >
          {loading ? (
            <SkeletonRows rows={4} cols={3} />
          ) : err ? (
            /* Not "nothing is going off": the reading failed, and an empty state
               here would be this page making a claim about a shelf it could not
               see. */
            <Empty
              title="The list could not be read"
              action={<Button variant="primary" onClick={() => void load()}>TRY AGAIN</Button>}
            >
              Nothing has been substituted for it. The refusal above carries the server's own words.
            </Empty>
          ) : nothingSoon ? (
            /* THE STATE THIS SHOP IS ACTUALLY IN: no batch has ever been booked,
               so this panel is empty every day until one is. It says what would
               be here, why nothing is, and hands over the control — instead of
               pointing "on the right", which is a lie at 390 px where the
               booking pad is below, not beside. */
            over && over.counts.batches === 0 ? (
              <Empty
                title="No batch has been booked yet"
                action={
                  products.length === 0
                    ? <a className="btn primary ex-linkbtn" href="#/products">TEACH A PRODUCT</a>
                    : <Button variant="primary" onClick={() => jumpTo('ex-sku')}>BOOK A BATCH</Button>
                }
              >
                This is the list a shopkeeper opens this page for: what has already gone off at the
                top, then what goes off soonest, each line with the units left and what they are
                marked at. It fills itself from the dates you book. The counter cannot read a date
                off a packet and never guesses one — so until a delivery is booked in, there is
                nothing here to rank.
              </Empty>
            ) : (
              <Empty title={`Nothing goes off within ${windowDays} days`}>
                Every batch on the log is dated later than that, or has been written off or sold
                through. Widen the window above to look further ahead.
              </Empty>
            )
          ) : (
            <div className="ex-list">
              {acted && (
                <Verdict
                  tone={acted.stock_figure_needs_recount ? 'amber' : 'info'}
                  title={
                    'written_off_now' in acted
                      ? `Written off: ${acted.written_off_now} × ${acted.name}`
                      : `Sold through: ${acted.sold_now} × ${acted.name}`
                  }
                >
                  {acted.detail}
                </Verdict>
              )}

              {over && over.expired.length > 0 && (
                <div className="ex-group">
                  <span className="eyebrow">Past their date — still on a batch</span>
                  {over.expired.map((b) => (
                    <Row key={b.batch_id} b={b}
                         onWriteOff={(x) => openAct('write-off', x)}
                         onSold={(x) => openAct('sold', x)} />
                  ))}
                </div>
              )}

              {over && over.soon.length > 0 && (
                <div className="ex-group">
                  <span className="eyebrow">Within {windowDays} days</span>
                  {over.soon.map((b) => (
                    <Row key={b.batch_id} b={b}
                         onWriteOff={(x) => openAct('write-off', x)}
                         onSold={(x) => openAct('sold', x)} />
                  ))}
                </div>
              )}

              {over && <p className="hint ex-note">{over.value_at_risk.note}</p>}
            </div>
          )}
        </Card>

        {/* ----------------------------------------------------- book a batch -- */}
        <Card title="Book a batch" sub="the date on the packet, when it arrives">
          {loading ? (
            <LoadingCard lines={3} label="Reading the catalogue" />
          ) : err ? (
            <Empty
              title="Nothing to book against"
              action={<Button variant="primary" onClick={() => void load()}>TRY AGAIN</Button>}
            >
              The catalogue could not be read, so there is no product to choose. The refusal above
              carries the server's own words.
            </Empty>
          ) : products.length === 0 ? (
            <Empty
              title="Nothing taught yet"
              action={<a className="btn primary ex-linkbtn" href="#/products">TEACH A PRODUCT</a>}
            >
              A batch is a batch of something. Teach a product on the Products screen and it can be
              chosen here — with its marked price, which is what the value beside every expiry line
              is worked out from.
            </Empty>
          ) : (
            <div className="ex-form">
              {/* A native select, deliberately: on a phone held one-handed over a
                  crate, the operating system's own picker is the steadiest target
                  a page can offer. */}
              <Field label="Product" htmlFor="ex-sku">
                <Select id="ex-sku" value={sku} onChange={(e) => { setSku(e.target.value); setBooked(null); setBookErr(null); }}>
                  <option value="">Choose a product…</option>
                  {products.map((p) => (
                    <option key={p.sku_id} value={p.sku_id}>
                      {p.name}{p.price_rupees ? ` — ₹${p.price_rupees}` : ' — no price'}
                    </option>
                  ))}
                </Select>
              </Field>

              <Field label="Units in the batch">
                <div className="ex-qty">
                  <Button className="ex-qbig" onClick={() => bump(-1)} aria-label="One fewer">−</Button>
                  <Input
                    className="tnum ex-qty-in"
                    type="text"
                    inputMode="numeric"
                    value={qty}
                    aria-label="How many units"
                    onChange={(e) => setQty(e.target.value)}
                  />
                  <Button className="ex-qbig" onClick={() => bump(1)} aria-label="One more">+</Button>
                  <div className="ex-jump">
                    {[6, 12, 24].map((n) => (
                      <Button key={n} size="sm" onClick={() => setQty(String(n))}>{n}</Button>
                    ))}
                  </div>
                </div>
              </Field>

              <Field label="Expires on" sub="The date printed on the packet. Today's date counts as still good.">
                <div className="ex-date">
                  <Input
                    type="date"
                    value={expiresOn}
                    aria-label="Expiry date"
                    onChange={(e) => setExpiresOn(e.target.value)}
                  />
                  <div className="ex-jump">
                    {[3, 7, 30].map((n) => (
                      <Button key={n} size="sm" onClick={() => jumpDate(n)} title={`${n} days from today`}>+{n}d</Button>
                    ))}
                  </div>
                </div>
              </Field>

              <Field label="Note (optional)" sub="Whose delivery, which shelf.">
                <Input
                  type="text"
                  value={note}
                  maxLength={200}
                  placeholder="e.g. Sharma Traders, morning van"
                  onChange={(e) => setNote(e.target.value)}
                />
              </Field>

              <Checkbox
                checked={stockIn}
                onChange={setStockIn}
                label="Also book these in as a delivery on the Stock log"
                sub="Leave this off if you already recorded the delivery on the Stock screen, or the shelf figure will count it twice."
              />

              <Button
                variant="primary"
                size="lg"
                block
                loading={booking}
                disabled={!sku}
                aria-describedby={!sku ? 'ex-why-book' : undefined}
                onClick={() => void book()}
              >
                BOOK BATCH
              </Button>

              {/* A DISABLED CONTROL SAYS WHY, and offers the way out of it. */}
              {!sku && (
                <WhyDead id="ex-why-book">
                  No product is chosen, and a batch is a batch of something.{' '}
                  <button type="button" className="ex-jumplink" onClick={() => jumpTo('ex-sku')}>
                    Choose one above.
                  </button>
                </WhyDead>
              )}

              <p className="hint">
                Your word, on a log that cannot be edited afterwards. The counter cannot see a date
                and cannot tell which batch a sold packet came from, so the units on a batch stay as
                booked until you write some off or mark some sold.
              </p>

              {bookErr && <Refusal reason={bookErr.reason} detail={bookErr.detail} />}

              {booked && (
                <Verdict
                  tone={booked.stock_figure_needs_recount ? 'amber' : 'info'}
                  title={`Booked: ${booked.units} × ${booked.name}`}
                >
                  {booked.detail}
                </Verdict>
              )}
            </div>
          )}
        </Card>
      </div>

      {/* ------------------------------------------------------- every batch -- */}
      <div className="ex-band top">
        <Card
          title="Every batch"
          sub="open ones soonest first; closed ones on request"
          aside={
            <div className="ex-filters">
              <Select
                value={filterSku}
                onChange={(e) => setFilterSku(e.target.value)}
                aria-label="Only this product"
              >
                <option value="">Every product</option>
                {products.map((p) => (
                  <option key={p.sku_id} value={p.sku_id}>{p.name}</option>
                ))}
              </Select>
              <Checkbox checked={withClosed} onChange={setWithClosed} label="Show closed" />
            </div>
          }
        >
          {allErr ? (
            <Refusal
              reason="The batches could not be read"
              detail={allErr.reason}
              hint={allErr.detail}
              action={<Button size="sm" onClick={() => void loadAll()}>TRY AGAIN</Button>}
            />
          ) : !allLoading && (all?.batches.length ?? 0) === 0 ? (
            /* Outside the table, not inside its one wide cell: on a phone the
               table keeps a floor width and scrolls, and an empty state inside
               it was being cut off at the card's edge. */
            <Empty
              title={filterSku ? 'No open batch for this product' : 'No batches yet'}
              action={
                filterSku ? (
                  <>
                    {!withClosed && <Button onClick={() => setWithClosed(true)}>SHOW CLOSED</Button>}
                    <Button variant="ghost" onClick={() => setFilterSku('')}>EVERY PRODUCT</Button>
                  </>
                ) : products.length === 0 ? (
                  <a className="btn ex-linkbtn" href="#/products">TEACH A PRODUCT</a>
                ) : (
                  /* Not `primary`: the lead list above is empty for the same
                     reason and carries the page's one primary call. */
                  <Button onClick={() => jumpTo('ex-sku')}>BOOK A BATCH</Button>
                )
              }
            >
              {filterSku
                ? 'Nothing is booked against it, or every batch of it has been written off or sold through. Closed batches stay on the log for good and are shown on request.'
                : 'Every batch ever booked is listed here — open ones soonest first, closed ones on request — with the units left, the date on the packet, and what they are marked at. Book one when a delivery arrives and it appears on this table and on the list above.'}
            </Empty>
          ) : (
            <Table
              cols={cols}
              rows={all?.batches ?? []}
              rowKey={(b) => b.batch_id}
              loading={allLoading}
              label="Every batch"
              // `tbl-cards`: under 560 every row restacks into a card. This is
              // a LIST of batches, not a document whose columns have to line
              // up, and at 390 it ran 338 px past the viewport inside its
              // scroller — a shopkeeper had to drag it sideways to read a date.
              className="ex-table tbl-cards"
              // Prefixed: a bare `open` would be the shared table's expanded-row
              // style, and every open batch rendered as if it had been clicked.
              rowClass={(b) => `ex-${b.state}`}
            />
          )}
          {over && <p className="hint">{over.note}</p>}
        </Card>
      </div>

      {/* ------------------------------------------------------ the dialog -- */}
      <Modal
        open={act !== null}
        onClose={() => { if (!acting) setAct(null); }}
        title={act ? (act.kind === 'write-off' ? `Write off ${act.b.name}` : `Mark ${act.b.name} sold`) : ''}
        sub={act ? <>{act.b.units_remaining} left on this batch · <When b={act.b} /></> : undefined}
        size="narrow"
        note={
          act?.kind === 'write-off'
            ? 'Appends a stock OUT with reason “expiry” to the stock log. It cannot be undone; a mistake is corrected with an opposite movement on the Stock screen.'
            : 'Takes the units off this batch only. Nothing is written to the stock log: the sales are already on the audit chain.'
        }
        foot={
          <>
            <Button variant="ghost" disabled={acting} onClick={() => setAct(null)}>CANCEL</Button>
            <Button variant="primary" loading={acting} onClick={() => void confirmAct()}>
              {act?.kind === 'write-off' ? 'WRITE OFF' : 'MARK SOLD'}
            </Button>
          </>
        }
      >
        {act && (
          <div className="ex-form">
            <Field label="Units" sub={`Up to ${act.b.units_remaining}. Fewer, if some sold or some are still good.`}>
              <Input
                type="text"
                inputMode="numeric"
                className="tnum"
                value={actUnits}
                onChange={(e) => setActUnits(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') void confirmAct(); }}
              />
            </Field>
            <Field label="Note (optional)">
              <Input
                type="text"
                value={actNote}
                maxLength={200}
                placeholder={act.kind === 'write-off' ? 'e.g. binned at close' : 'e.g. sold on the morning rush'}
                onChange={(e) => setActNote(e.target.value)}
              />
            </Field>
            {act.kind === 'write-off' && act.b.value_at_risk_rupees && (
              <p className="hint ex-note">
                Marked at ₹{act.b.value_at_risk_rupees} for {act.b.units_remaining} — what they would have
                fetched, not a charge and not a loss the books record.
              </p>
            )}
            {actErr && <Refusal reason={actErr.reason} detail={actErr.detail} />}
          </div>
        )}
      </Modal>
    </div>
  );
}
