import { useCallback, useEffect, useMemo, useState } from 'react';
import * as db from '../lib/daybookapi';
import { money, when } from '../lib/manageapi';
import {
  Card, KV, Pill, Verdict, Empty, Refusal, Field, Segmented,
  Button, Modal, Progress, Skeleton, SkeletonText,
} from '../components/ui';
import Denominations, {
  tallyOf, rupeeStringFromPaise, piecesLine, type DenomCounts,
} from '../components/Denominations';
import '../styles/daybook.css';
import * as mi from '../lib/milanapi';
import '../styles/milan.css';

/**
 * HISAAB — closing the shop for the day.
 *
 * The one thing that shapes this screen: CLOSING IS AN ACT, NOT A VIEW. Every
 * other screen in Books derives its numbers from the audit chain each time it
 * is drawn, which is right for a screen and impossible for a close-out — the
 * chain keeps growing, so a day derived on Tuesday does not read the same on
 * Friday. A late webhook settles a Tuesday bill on Wednesday. A basket open at
 * closing time lands inside Tuesday's window an hour later. A product gets
 * renamed. Each is correct for a live screen and wrong for a day somebody has
 * already signed off on.
 *
 * So the page has two halves and they are deliberately not the same thing:
 *
 *   THE REVIEW is live. It is what the chain says right now about the day being
 *   closed, plus the drawer and the day's outgoings, and it is what the
 *   shopkeeper reads before he decides.
 *
 *   A CLOSED DAY is a snapshot. It is served exactly as it was frozen and is
 *   never recomputed here — the screen would not be able to tell if it were.
 *   Beside it, clearly separated, is what the chain says about that same day
 *   now. Neither figure overwrites the other, and when they differ the record
 *   is not the thing that is wrong.
 *
 * THE BROWSER IS NOT AN AUTHOR OF MONEY here either. Everything on this screen
 * except one figure is a number the server counted off the audit chain. That
 * one figure is the counted cash, which no machine can derive — it is the
 * shopkeeper's own assertion about a physical drawer — and it goes up as a
 * rupee STRING for the server to parse. Nothing here converts rupees to paise.
 *
 * There are two ways to write that assertion, and they end at the same string.
 * A shopkeeper who has already added it up types the total. A shopkeeper doing
 * it at the counter counts the drawer pile by pile — see components/
 * Denominations.tsx, which adds whole counts of whole paise and renders the sum
 * back to rupees by integer subtraction and remainder. The request that closes
 * the day is identical either way.
 */

/** Poll nothing. A close-out review is a still frame a person is reading, and
    a page that reshuffled its own figures under a shopkeeper mid-count would be
    changing the thing he is about to sign off on. The REFRESH button is his. */

type View = 'review' | 'closed';
type How = 'notes' | 'total';

export default function DayClose() {
  const today = useMemo(() => todayLabel(), []);
  const [view, setView] = useState<View>('review');
  const [day, setDay] = useState<string>(today);

  /* ------------------------------------------------------------ the review -- */
  const [pv, setPv] = useState<db.PreviewBody | null>(null);
  const [pvErr, setPvErr] = useState<Refused | null>(null);
  const [pvLoading, setPvLoading] = useState(true);
  const [cash, setCash] = useState<db.CashPosition | null>(null);
  const [cashErr, setCashErr] = useState<Refused | null>(null);
  const [spend, setSpend] = useState<db.ExpensesDayBody | null>(null);
  const [spendErr, setSpendErr] = useState<Refused | null>(null);
  /* THE DAY'S DISAGREEMENTS, READ BEFORE IT IS FROZEN. A close-out cannot be
     reopened, so a day closed over three refused webhooks and five bills the
     gateway was never asked for is a day permanently recorded as agreeing with
     itself. The figures the close freezes are not changed by any of this — this
     is what the shopkeeper is told before he presses the button. */
  const [rec, setRec] = useState<db.ReconcileBody | null>(null);
  const [recErr, setRecErr] = useState<Refused | null>(null);

  /* -------------------------------------------------------------- the form -- */
  // How the drawer was counted. `notes` is the default because it is what a
  // shopkeeper is actually doing at closing time — the total box is for the one
  // who added it up before he got to this screen.
  const [how, setHow] = useState<How>('notes');
  const [counts, setCounts] = useState<DenomCounts>({});
  const [counted, setCounted] = useState('');
  const [by, setBy] = useState('');
  const [note, setNote] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [closing, setClosing] = useState(false);
  const [closeErr, setCloseErr] = useState<Refused | null>(null);
  const [done, setDone] = useState<db.CloseBody | null>(null);

  /* ------------------------------------------------------- the closed days -- */
  const [list, setList] = useState<db.ListBody | null>(null);
  const [listErr, setListErr] = useState<Refused | null>(null);
  const [listLoading, setListLoading] = useState(true);
  const [openDay, setOpenDay] = useState<string | null>(null);
  const [detail, setDetail] = useState<db.OneDayBody | null>(null);
  const [detailErr, setDetailErr] = useState<Refused | null>(null);

  const loadReview = useCallback(async (which: string) => {
    setPvLoading(true);
    // Three independent reads. One of them refusing must not empty the other
    // two: the drawer and the day's outgoings come from a different router, and
    // a shopkeeper whose expenses file is unreadable still has to be able to
    // see his takings and close his day.
    const [p, c, e, q] = await Promise.all([
      db.preview(which), db.cash(which), db.expenses(which), db.reconcile(which),
    ]);
    if (p.ok) { setPv(p); setPvErr(null); } else { setPv(null); setPvErr(p); }
    if (c.ok) { setCash(c); setCashErr(null); } else { setCash(null); setCashErr(c); }
    if (e.ok) { setSpend(e); setSpendErr(null); } else { setSpend(null); setSpendErr(e); }
    if (q.ok) { setRec(q); setRecErr(null); } else { setRec(null); setRecErr(q); }
    setPvLoading(false);
  }, []);

  const loadList = useCallback(async () => {
    setListLoading(true);
    const r = await db.days();
    if (r.ok) { setList(r); setListErr(null); } else { setList(null); setListErr(r); }
    setListLoading(false);
  }, []);

  useEffect(() => { void loadReview(day); }, [loadReview, day]);
  useEffect(() => { void loadList(); }, [loadList]);

  // A day changed under the form is a different day. Everything the form was
  // holding was about the old one, and carrying a typed count across would be
  // the exact mistake that cannot be corrected once it is frozen.
  //
  // THE FIGURES GO WITH IT. What is still in memory belongs to the day that was
  // on screen a moment ago, and leaving it up under the new date renders one
  // day's takings labelled as another's for as long as the read takes. Cleared
  // here and not in `loadReview`, because REFRESH re-reads the SAME day: that is
  // the same figures arriving again, and blanking the page for it would throw
  // away the thing the shopkeeper was in the middle of reading.
  useEffect(() => {
    setCounted(''); setCounts({}); setBy(''); setNote('');
    setConfirming(false); setCloseErr(null); setDone(null);
    setPv(null); setPvErr(null);
    setCash(null); setCashErr(null);
    setSpend(null); setSpendErr(null);
    setRec(null); setRecErr(null);
  }, [day]);

  useEffect(() => {
    if (openDay === null) { setDetail(null); setDetailErr(null); return; }
    let alive = true;
    setDetail(null); setDetailErr(null);
    void (async () => {
      const r = await db.day(openDay);
      if (!alive) return;
      if (r.ok) setDetail(r); else setDetailErr(r);
    })();
    return () => { alive = false; };
  }, [openDay]);

  const tidied = db.tidyRupees(counted);
  const countLooksRight = db.looksLikeRupees(tidied);

  // The sheet, added up. One call, and the figure on screen, the figure in the
  // confirmation and the figure on the wire are all this one.
  const tally = useMemo(() => tallyOf(counts), [counts]);
  const fromSheet = tally.entered === 0 || tally.totalPaise === null
    ? null
    : rupeeStringFromPaise(tally.totalPaise);

  /** The rupee string this close would send, or null when there is not one yet. */
  const wire = how === 'notes' ? fromSheet : (countLooksRight ? tidied : null);

  // Which box the server refused, if it refused one of them. A refusal about the
  // counted cash is shown under the counted-cash box; one about the note is
  // shown under the note. Only what it could not attach to a box stays at the
  // foot of the form, so a refusal is never in two places at once.
  const refusedBox = whichBox(closeErr);
  // The sheet has eleven boxes and no single field the count was typed into, so
  // a cash refusal there has nowhere to hang and goes back to the form foot.
  const cashOnField = refusedBox === 'cash' && how === 'total';
  const boxErr = cashOnField || refusedBox === 'note' || refusedBox === 'by' ? refusedBox : null;

  const doClose = useCallback(async () => {
    // Belt and braces: the button is disabled without a figure, and a close
    // with no counted cash must not be possible even so.
    if (wire === null) return;
    setClosing(true);
    setCloseErr(null);
    const r = await db.close({
      counted_cash_rupees: wire,
      day,
      ...(note.trim() ? { note: note.trim() } : {}),
      ...(by.trim() ? { closed_by: by.trim() } : {}),
    });
    setClosing(false);
    setConfirming(false);
    if (r.ok) {
      setDone(r);
      setCounted(''); setCounts({}); setBy(''); setNote('');
      await Promise.all([loadReview(day), loadList()]);
    } else {
      setCloseErr(r);
    }
  }, [wire, day, note, by, loadReview, loadList]);

  const showRecord = useCallback((which: string) => {
    setView('closed');
    setOpenDay(which);
  }, []);

  const d = pv?.derived ?? null;

  return (
    <div>
      <div className="page-head">
        <h1>Closing the day</h1>
        <p>
          Read what the counter recorded, count the drawer, and close the day. Closing writes those
          figures down as they stood at that moment. It is not a total that keeps updating: the audit
          chain goes on growing, and a closed day is the one thing here that never moves again.
        </p>
      </div>

      <div className="dc-bar">
        <Segmented
          value={view}
          onChange={setView}
          options={[
            { value: 'review', label: 'Close a day', title: 'review the day and close it' },
            {
              value: 'closed',
              label: list ? `Closed days · ${list.days_on_record}` : 'Closed days',
              title: 'days already closed, frozen as they stood',
            },
          ]}
        />
        {view === 'review' && (
          <div className="dc-pick">
            <label htmlFor="dc-day">day</label>
            <input
              id="dc-day"
              type="date"
              value={day}
              max={today}
              onChange={(e) => setDay(e.target.value || today)}
            />
            {day !== today && (
              <button className="btn sm ghost" onClick={() => setDay(today)}>TODAY</button>
            )}
            {/* A disabled button that does not say why reads as a broken one.
                While the read is in flight the title says what it is waiting
                for, and the label says what it is doing. */}
            <button
              className="btn sm"
              onClick={() => void loadReview(day)}
              disabled={pvLoading}
              aria-busy={pvLoading || undefined}
              title={pvLoading
                ? 'Already reading this day off the chain — wait for it to land'
                : 'Read this day off the chain again'}
            >
              {pvLoading ? 'READING…' : 'REFRESH'}
            </button>
          </div>
        )}
      </div>

      {view === 'review' ? (
        <div className="stack">
          {pvErr && (
            <Refusal
              reason="This day could not be read"
              detail={pvErr.reason}
              hint={pvErr.detail}
              action={<button className="btn sm" onClick={() => void loadReview(day)}>TRY AGAIN</button>}
            />
          )}

          {done && (
            <Verdict tone="info" title={`${dayLong(done.day)} is closed`}>
              Frozen at {when(done.record.closed_at)}
              {done.record.closed_by ? ` by ${done.record.closed_by}` : ''}, with{' '}
              {/* The server's own integer paise, rendered the way every other
                  figure on this screen is. The typed string was echoed in the
                  confirmation; what is reported back is what was stored. */}
              <b>{money(done.record.counted_cash_paise)}</b> counted in the drawer.{' '}
              {done.audited
                ? 'The close is on this counter’s own hash chain.'
                : 'THE CHAIN LINE WAS NOT WRITTEN — the record is on disk, but nothing stands behind it.'}
              <br />
              <span className="dc-actions">
                <button className="btn sm" onClick={() => showRecord(done.day)}>SEE THE RECORD</button>
              </span>
            </Verdict>
          )}

          {pv?.chain_warning && (
            <Verdict tone="red" title="The audit chain does not verify">
              {pv.chain_warning}
            </Verdict>
          )}

          {/* Not when this page just closed it: the banner above already says
              so, in the present tense, and two boxes about one act read as two
              acts. This one is for a day somebody else closed, or a day closed
              before this page was opened. */}
          {pv?.already_closed && !done && (
            <Verdict tone="info" title={`${dayLong(pv.day)} is already closed`}>
              It was closed at {when(pv.closed_at)}. There is no reopen and no overwrite — a
              close-out that can be replaced is not a record of anything. If the count was wrong, say
              so in a note on the next day’s close.
              <br />
              <span className="dc-actions">
                <button className="btn sm" onClick={() => showRecord(pv.day)}>SEE THE RECORD</button>
              </span>
            </Verdict>
          )}

          {pvLoading && !pv ? (
            <ReviewSkeleton />
          ) : !d || !pv ? null : (
            <>
              <div className="dc-stats">
                <Stat
                  label="billed on this day"
                  value={money(d.revenue_paise)}
                  sub={`${d.bills} bill${d.bills === 1 ? '' : 's'} closed`}
                />
                {/* THE SAME LEAK THE AWAITING TILE BELOW WAS FIXED FOR, and it
                    matters more here than anywhere else on this product: this
                    screen freezes its figures into a record that cannot be
                    reopened. `/daybook/preview` derives `settled_paise` from
                    `manage.brief_for`, and `bills_from` sets the settled flag
                    for `kernel/intent.settled` when the webhook line is not in
                    the chain. So this tile read "settled — real money" in GREEN
                    over money no signature-verified webhook stands behind.
                    Measured on a scratch counter with one webhook-settled bill
                    (Rs 10) and one kernel-only one (Rs 40): the tile said
                    Rs 50.00, green, while the disagreement panel directly below
                    it named the missing webhook. Invariant 2 gives only the
                    webhook the right to that word and invariant 6 gives it the
                    colour, so the reconciliation is used where it answers and
                    the tile keeps the weaker wording and no colour where it
                    does not. */}
                <Stat
                  label={rec ? 'settled — real money' : 'settled — as the day brief counts it'}
                  value={money(rec ? rec.today.settled.paise : d.settled_paise)}
                  sub={rec
                    ? `${rec.today.settled.bills} of ${d.bills} bill${d.bills === 1 ? '' : 's'}, on a verified webhook`
                    : `${d.settled_count} of ${d.bills} bill${d.bills === 1 ? '' : 's'} — no webhook check available`}
                  tone={rec
                    ? (rec.today.settled.bills > 0 ? 'green' : undefined)
                    : undefined}
                />
                {/* The day brief's `awaiting_count` is `bills − settled`, so it
                    counts a bill that closed with no link ever minted. Calling
                    those "links sent" was a claim about the gateway nothing had
                    checked. The reconciliation splits them; without it the tile
                    falls back to wording that claims no link. */}
                <Stat
                  label="awaiting the gateway"
                  value={money(rec ? rec.today.awaiting.paise : d.awaiting_paise)}
                  sub={rec
                    ? `${rec.today.awaiting.bills} link${rec.today.awaiting.bills === 1 ? '' : 's'} sent, not settled`
                    : `${d.awaiting_count} bill${d.awaiting_count === 1 ? '' : 's'} the gateway has not confirmed`}
                />
                <Stat
                  label="expected in the drawer"
                  value={cash && cash.expected_closing_paise !== null
                    ? money(cash.expected_closing_paise) : 'no figure'}
                  // Not a zero, and not a dash pretending to be a number. Until
                  // the opening float has been counted there is no honest
                  // expected figure, and one computed from an assumed nought
                  // reads as the drawer being over by exactly the float.
                  quiet={!cash || cash.expected_closing_paise === null}
                  sub={cash && cash.expected_closing_paise !== null
                    ? 'opening float, plus cash sales, less cash paid out'
                    // NAMED BY THE SCREEN A SHOPKEEPER CAN REACH. This said "on
                    // the cash screen"; there is no cash entry in the sidebar
                    // and `/cash` is a JSON endpoint. The drawer is drawn on
                    // Expenses, under "The cash drawer".
                    : 'count the opening float on the Expenses screen to get this'}
                />
              </div>

              <DayDisagreements rec={rec} err={recErr} closed={pv.already_closed} />

              {/* MILAN. The one figure on this screen that is not the chain's:
                  what the gateway says reached the bank, matched row by row
                  against what the chain says settled. It sits with the review
                  because a shopkeeper closes his day against the bank, not
                  against his own bill book. */}
              <MatchSection />

              <div className="grid two">
                <div className="stack">
                  <Card
                    title="What sold most"
                    aside={d.excluded_lines > 0 && (
                      <Pill tone="amb">
                        {d.excluded_lines} line{d.excluded_lines === 1 ? '' : 's'} excluded
                      </Pill>
                    )}
                  >
                    {pv.top_sellers.length === 0 ? (
                      <Empty title="No bill closed on this day">
                        Nothing crossed the counter on this day. Ring one up on the till and the
                        products that sold appear here, biggest first.
                      </Empty>
                    ) : (
                      <Sellers rows={pv.top_sellers} frozen={false} />
                    )}
                    {d.excluded_lines > 0 && (
                      <p className="hint">
                        {d.excluded_lines === 1 ? 'One line was' : `${d.excluded_lines} lines were`}{' '}
                        seen on the counter and left out of a total, because the counter would not
                        put a price on {d.excluded_lines === 1 ? 'it' : 'them'}. That abstention is
                        the product working, and it is frozen with everything else.
                      </p>
                    )}
                  </Card>

                  <Card
                    title="What went out"
                    aside={spend && spend.voided_count > 0
                      ? <span className="pill dc-quiet">{spend.voided_count} VOIDED</span>
                      : undefined}
                  >
                    {spendErr ? (
                      <InlineRefusal what="The day’s outgoings" err={spendErr} />
                    ) : !spend ? (
                      <MoneyRowSkeletons rows={3} label="Reading the day book" />
                    ) : spend.count === 0 ? (
                      <Empty title="Nothing went out on this day">
                        Nothing was entered as an expense for this day. Enter what left the drawer
                        on the Expenses screen — chai, an autorickshaw, the electricity bill.
                        Anything paid out of the drawer that is not entered will show up as cash
                        the drawer is short of.
                      </Empty>
                    ) : (
                      <>
                        <div className="dc-cats">
                          {spend.by_category.map((c) => (
                            <div className="dc-cat" key={c.category}>
                              <span className="nm">{c.label}</span>
                              <span className="n">{c.count}</span>
                              <span className="amt">{money(c.paise)}</span>
                            </div>
                          ))}
                        </div>
                        <div className="dc-splits">
                          <KV k="paid in cash">
                            {money(spend.cash_paise)}{' '}
                            <span className="muted">
                              {spend.cash_count} entr{spend.cash_count === 1 ? 'y' : 'ies'}
                            </span>
                          </KV>
                          <KV k="paid from the bank">
                            {money(spend.bank_paise)}{' '}
                            <span className="muted">not out of the drawer</span>
                          </KV>
                        </div>
                        {spend.unreadable_rows > 0 && (
                          <p className="hint">
                            {spend.unreadable_rows} row{spend.unreadable_rows === 1 ? '' : 's'} on
                            record could not be added up and {spend.unreadable_rows === 1 ? 'is' : 'are'}{' '}
                            in none of these totals. Nothing was read as zero to make them balance.
                          </p>
                        )}
                      </>
                    )}
                  </Card>
                </div>

                <div className="stack">
                  <Card title="The drawer">
                    {cashErr ? (
                      <InlineRefusal what="The cash position" err={cashErr} />
                    ) : !cash ? (
                      <MoneyRowSkeletons rows={4} label="Reading the drawer" />
                    ) : (
                      <>
                        <KV k="opening float">
                          {cash.opening.counted
                            ? money(cash.opening.paise)
                            : <span className="muted">not counted</span>}
                        </KV>
                        <KV k="sales the gateway did not confirm">
                          {money(cash.cash_sales.paise)}{' '}
                          <span className="muted">
                            {cash.cash_sales.bills} bill{cash.cash_sales.bills === 1 ? '' : 's'}
                          </span>
                        </KV>
                        <KV k="paid out in cash">−{money(cash.cash_expenses.paise)}</KV>
                        <KV k="expected in the drawer">
                          {cash.expected_closing_paise !== null
                            ? <b>{money(cash.expected_closing_paise)}</b>
                            : <span className="muted">—</span>}
                        </KV>
                        <p className="hint">{cash.cash_sales_note}</p>
                      </>
                    )}
                  </Card>

                  <Card title="Where these figures come from" tight>
                    <KV k="source">the hash-chained audit log</KV>
                    <KV k="chain">
                      {pv.chain.ok
                        ? <Pill tone="ok">VERIFIED · {pv.chain.lines_verified} lines</Pill>
                        : <Pill tone="bad">BROKEN</Pill>}
                    </KV>
                    <KV k="window">midnight to midnight, this counter’s clock ({pv.day})</KV>
                    <KV k="day">
                      {pv.day_has_ended
                        ? 'ended'
                        : `${hours(pv.seconds_left_in_day)} still to run`}
                    </KV>
                    <p className="hint">{pv.derived_from}</p>
                  </Card>
                </div>
              </div>

              {!pv.already_closed && (
                <section className="card dc-close">
                  <header className="card-head">
                    <h2>Close {dayLong(pv.day)}</h2>
                  </header>
                  <div className="dc-close-body">
                    {/* THE COUNT COMES FIRST and gets the width of the card.
                        It is the only thing on this screen a person has to do
                        rather than read, and at ten at night it is done with
                        one hand while the other is still in the drawer. */}
                    <div className="dc-count">
                      <div className="dc-how">
                        <p className="dc-eyebrow">how you counted it</p>
                        <Segmented
                          value={how}
                          onChange={(v) => { setHow(v); setConfirming(false); }}
                          options={[
                            { value: 'notes', label: 'Count it by note', title: 'a box per denomination — the sheet adds them up' },
                            { value: 'total', label: 'Type the total', title: 'for a drawer you have already counted' },
                          ]}
                        />
                      </div>

                      {how === 'notes' ? (
                        <Denominations
                          counts={counts}
                          tally={tally}
                          disabled={closing}
                          onChange={(next) => { setCounts(next); setConfirming(false); }}
                        />
                      ) : (
                        <div className="dc-typed">
                          {/* THE REFUSAL LIVES UNDER THE BOX IT IS ABOUT. A
                              malformed amount is named — with the server's own
                              name for that state, so the same string is what a
                              maintainer greps for whichever side refused it —
                              and never coerced into a number. When the server
                              is the one that refused, its reason and its detail
                              are shown verbatim in the same place. */}
                          <Field
                            label="counted cash"
                            htmlFor="dc-counted"
                            sub="What you counted in the drawer, in rupees. Zero is a valid count and means it was empty."
                            error={
                              boxErr === 'cash' && closeErr
                                ? <><span className="mono">{closeErr.reason}</span>
                                    {closeErr.detail ? ` — ${closeErr.detail}` : ''}</>
                                : counted !== '' && !countLooksRight
                                  ? <><span className="mono">counted_cash_not_a_rupee_string</span>
                                      {` — ${db.RUPEE_HINT}`}</>
                                  : undefined
                            }
                          >
                            <input
                              id="dc-counted"
                              type="text"
                              inputMode="decimal"
                              autoComplete="off"
                              placeholder="4820.00"
                              value={counted}
                              aria-invalid={(counted !== '' && !countLooksRight) || boxErr === 'cash'}
                              onChange={(e) => { setCounted(e.target.value); setCloseErr(null); setConfirming(false); }}
                            />
                          </Field>
                          <p className="hint">
                            This goes up exactly as you write it — the counter reads the rupees, and
                            nothing in this page turns them into paise. If you have not added the
                            drawer up yet, count it by note instead and the sheet will do it.
                          </p>
                        </div>
                      )}
                    </div>

                    <div className="dc-close-why">
                      <p className="dc-eyebrow">what gets written down</p>
                      <div className="dc-freeze">
                        <span>billed</span><b>{money(d.revenue_paise)}</b>
                        <span>settled</span><b>{money(d.settled_paise)}</b>
                        <span>awaiting</span><b>{money(d.awaiting_paise)}</b>
                        <span>bills</span><b className="tnum">{d.bills}</b>
                      </div>
                      <p className="hint">
                        These figures are taken again at the moment you press the button, so what is
                        frozen is the day as it stands then and not as it stood when this page was
                        drawn. Afterwards they do not move: a bill rung up later, or a payment that
                        settles tomorrow morning, is reported beside the record and never inside it.
                      </p>
                      <p className="hint">{pv.closing_early_note}</p>
                      {/* The prose all lives on this side. It keeps the entry
                          column short enough to read as one act, and it puts
                          the sentence about what the counted cash IS beside the
                          figures it is not going to be compared with. */}
                      <p className="hint">{pv.note}</p>
                    </div>

                    <div className="dc-close-form">
                      {/* The one figure this act writes, beside the button that
                          writes it. In the sheet it is the sum of the boxes; in
                          the total box it is the string as typed, unformatted,
                          because that is the thing that will be sent. */}
                      <div className={wire === null ? 'dc-counted none' : 'dc-counted'}>
                        <span className="lbl">counted in the drawer</span>
                        <span className="val">
                          {wire === null ? 'no figure yet'
                            : how === 'notes' ? money(tally.totalPaise) : `₹${tidied}`}
                        </span>
                        <span className="sub">
                          {how === 'notes'
                            ? (tally.unreadable.length > 0
                              ? 'one of the boxes is not a whole count'
                              : (piecesLine(tally) ?? 'nothing counted in yet'))
                            : (wire === null
                              ? 'write it as rupees — 4820, or 4820.50'
                              : 'as you typed it, and as it will be sent')}
                        </span>
                      </div>

                      <Field
                        label="counted by"
                        htmlFor="dc-by"
                        sub="optional — whoever counted it"
                        error={boxErr === 'by' && closeErr
                          ? <><span className="mono">{closeErr.reason}</span>
                              {closeErr.detail ? ` — ${closeErr.detail}` : ''}</>
                          : undefined}
                      >
                        <input
                          id="dc-by"
                          type="text"
                          autoComplete="off"
                          maxLength={80}
                          placeholder="Ramesh"
                          value={by}
                          aria-invalid={boxErr === 'by'}
                          onChange={(e) => { setBy(e.target.value); setCloseErr(null); }}
                        />
                      </Field>
                      <Field
                        label="note"
                        htmlFor="dc-note"
                        sub="optional — anything about this day worth reading back"
                        error={boxErr === 'note' && closeErr
                          ? <><span className="mono">{closeErr.reason}</span>
                              {closeErr.detail ? ` — ${closeErr.detail}` : ''}</>
                          : undefined}
                      >
                        <textarea
                          id="dc-note"
                          rows={2}
                          maxLength={400}
                          placeholder="Shutter down early, power cut from 6."
                          value={note}
                          aria-invalid={boxErr === 'note'}
                          onChange={(e) => { setNote(e.target.value); setCloseErr(null); }}
                        />
                      </Field>

                      {/* Only what could not be pinned on a box. A refusal about
                          the counted cash, the note or the name is already
                          under the box it is about, and one refusal shown twice
                          reads as two refusals. The server's own name for the
                          state goes in the mono line and its sentence in plain
                          text: neither is paraphrased — the reason is what a
                          maintainer greps for and the sentence is what a
                          shopkeeper acts on. */}
                      {closeErr && !boxErr && (
                        <div className="dc-form-refusal">
                          <Refusal
                            reason="The day was not closed"
                            detail={closeErr.reason}
                            hint={closeErr.detail}
                          />
                        </div>
                      )}

                      <div className="btn-row">
                        <button
                          className="btn primary lg"
                          disabled={wire === null || closing}
                          aria-busy={closing || undefined}
                          title={wire !== null ? undefined
                            : how === 'notes'
                              ? (tally.unreadable.length > 0
                                ? 'One of the boxes is not a whole count'
                                : 'Nothing is counted in yet — a box per pile')
                              : 'There is no rupee amount in the box yet'}
                          onClick={() => setConfirming(true)}
                        >
                          CLOSE THE DAY
                        </button>
                        {wire === null && (
                          <span className="dc-gate">
                            {how === 'notes'
                              ? (tally.unreadable.length > 0
                                ? 'One of the boxes is not a whole count.'
                                : 'Count the drawer first — a box per pile.')
                              : (counted ? 'That is not a rupee amount yet.' : 'Count the drawer first.')}
                          </span>
                        )}
                      </div>

                      {/* THE ACT THAT CANNOT BE UNDONE ASKS FIRST, and it asks
                          in a dialog rather than in a panel further down the
                          rail: a shopkeeper who has just pressed a large button
                          is looking at that button, and a confirmation that
                          renders below the fold is a confirmation he presses
                          past. The dialog also stops the page, which is the
                          right amount of friction for the one write on this
                          counter that has no reopen. */}
                      <Modal
                        open={confirming}
                        onClose={() => { if (!closing) setConfirming(false); }}
                        size="narrow"
                        title={`Close ${dayLong(pv.day)}?`}
                        sub="A closed day is frozen. There is no reopen and no overwrite."
                        closeLabel="Go back without closing the day"
                        note={closing
                          ? 'Freezing the day — do not close this window.'
                          : 'Nothing is written until you press it.'}
                        foot={
                          <>
                            <Button variant="ghost" disabled={closing} onClick={() => setConfirming(false)}>
                              GO BACK
                            </Button>
                            {/* `loading` disables it, so a second press cannot
                                become a second close. */}
                            <Button variant="primary" loading={closing} onClick={() => void doClose()}>
                              {closing ? 'CLOSING…' : 'YES, CLOSE THE DAY'}
                            </Button>
                          </>
                        }
                      >
                        {/* The amount is echoed as the exact string that will
                            be sent — not a re-formatting of it, and not a
                            second opinion about what was counted. */}
                        <p className="dc-confirm-amount">
                          <span>counted in the drawer</span>
                          <b className="tnum">₹{wire ?? ''}</b>
                        </p>
                        {how === 'notes' && (
                          <p className="dc-confirm-pieces">
                            Off the sheet: {piecesLine(tally) ?? '—'}. Check that against what is
                            on the counter before you press it.
                          </p>
                        )}
                        <p className="dc-confirm-why">
                          This counter does not reopen or overwrite a close-out. If the count
                          turns out to be wrong, it stays as it is and has to be noted against the
                          next day’s close.
                        </p>
                        {/* An indeterminate bar: the stages are the SERVER's and
                            this page cannot see which one it is in, so the bar
                            travels rather than claiming a fraction nobody
                            measured. */}
                        {closing && (
                          <div className="dc-closing" role="status" aria-live="polite">
                            <Progress label="Closing the day" />
                            <span>
                              Taking the figures again, writing the record, and signing it onto the
                              chain.
                            </span>
                          </div>
                        )}
                      </Modal>
                    </div>
                  </div>
                </section>
              )}
            </>
          )}
        </div>
      ) : (
        <div className="stack">
          <Card
            title="Days this shop has closed"
            aside={list && list.days_on_record > 0
              ? <span className="pill dc-quiet">{list.days_on_record} ON RECORD</span>
              : undefined}
          >
            {listErr ? (
              <InlineRefusal what="The closed days" err={listErr} retry={() => void loadList()} />
            ) : listLoading && !list ? (
              <ClosedDaysSkeleton />
            ) : !list || list.days.length === 0 ? (
              <Empty
                title="No day has been closed yet"
                action={<Button variant="primary" size="sm" onClick={() => setView('review')}>CLOSE A DAY</Button>}
              >
                Nothing on this counter has been frozen. Read a day on the review, count the
                drawer, and close it — it appears here afterwards, exactly as it stood.
              </Empty>
            ) : (
              <>
                {/* A header, not only the legend below: five money-shaped
                    columns with nothing naming them is how a shopkeeper reads
                    the counted cash as the takings. It is hidden on a phone,
                    where each figure carries its own label instead. */}
                <div className="dc-rows-head" aria-hidden="true">
                  <span>day</span>
                  <span>bills</span>
                  <span>billed</span>
                  <span>settled</span>
                  <span>counted</span>
                  <span />
                </div>
                <div className="dc-rows">
                  {list.days.map((r) => (
                    <button
                      key={r.day}
                      className={`dc-row${r.day === openDay ? ' open' : ''}`}
                      aria-expanded={r.day === openDay}
                      aria-controls="dc-open-day"
                      onClick={() => setOpenDay(r.day === openDay ? null : r.day)}
                    >
                      <span className="dy">
                        <b>{dayShort(r.day)}</b>
                        <span className="t">
                          closed {clock(r.closed_at)}
                          {!r.day_had_ended && <span className="pill dc-quiet">EARLY</span>}
                        </span>
                      </span>
                      <span className="n tnum">
                        {r.bills ?? '—'} <span className="muted">bills</span>
                      </span>
                      <span className="amt bill tnum">{money(r.revenue_paise)}</span>
                      {/* Green only when something actually settled. A green
                          nought would be the settled colour spent on nothing,
                          and on this product that colour has one meaning. */}
                      <span className={`amt set tnum${r.settled_paise ? '' : ' none'}`}>
                        {money(r.settled_paise)}
                      </span>
                      <span className="amt cnt tnum">{money(r.counted_cash_paise)}</span>
                      <span className="chev" aria-hidden="true">{r.day === openDay ? '−' : '+'}</span>
                    </button>
                  ))}
                </div>
                <div className="dc-legend">
                  <span><b>billed</b> · what closed</span>
                  <span><b className="set">settled</b> · what the gateway confirmed</span>
                  <span><b>counted</b> · what was in the drawer</span>
                </div>
                {list.truncated && (
                  <p className="hint">
                    Showing {list.count} of {list.days_on_record} closed days.
                  </p>
                )}
                <p className="hint">{list.note}</p>
              </>
            )}
          </Card>

          {/* The id every row points at with `aria-controls`. The record opens
              below the list rather than inside the row, so a screen reader is
              otherwise told a button expanded something it cannot name. */}
          <div id="dc-open-day">
            {openDay !== null && (
              <ClosedDay day={openDay} body={detail} err={detailErr} />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ pieces -- */

type Refused = { reason: string; detail?: string };

/* ----------------------------------------------------------------- boxes -- */

/**
 * WHICH BOX THE SERVER REFUSED, by the reason's own name.
 *
 * `gawaah/daybook.py` names the state it refused on rather than describing it —
 * `counted_cash_not_a_rupee_string`, `counted_cash_implausible`,
 * `note_too_long`, `closed_by_too_long` — so a refusal can be put back under the
 * box that caused it instead of only at the foot of the form. This decides
 * WHERE the refusal is shown and nothing else: the reason and the detail are
 * rendered exactly as they arrived either way.
 *
 * Anything it does not recognise returns null and stays at the foot, which is
 * the safe direction: a refusal in the wrong place is worse than a refusal in
 * the general place.
 */
function whichBox(r: Refused | null): 'cash' | 'note' | 'by' | null {
  if (!r) return null;
  if (r.reason.startsWith('counted_cash')) return 'cash';
  if (r.reason === 'note_too_long') return 'note';
  if (r.reason === 'closed_by_too_long') return 'by';
  return null;
}

/* --------------------------------------------------------------- waiting --
   THE SHAPE OF WHAT IS COMING, drawn while it comes.

   This screen used to answer three requests in flight with one grey sentence in
   an empty card, and then drop four tiles, two columns of cards and the close
   form onto the page at once — a shopkeeper reaching for the count box hit
   whatever landed under his thumb. Each block below uses the SAME grid, the same
   padding and the same number of rows as the thing that replaces it, and every
   money block sits at the right edge its figure will sit at.

   Pale blue, because a skeleton is the machine working and blue is the machine's
   colour here. Green, amber and red are spoken for.
*/

function StatSkeletons() {
  return (
    <div className="dc-stats" aria-hidden="true">
      {[0, 1, 2, 3].map((i) => (
        <div className="dc-stat dc-skel-stat" key={i}>
          <Skeleton w="64%" h={10} radius={999} />
          <Skeleton w="80%" h={27} />
          <Skeleton w="90%" h={10} radius={999} />
        </div>
      ))}
    </div>
  );
}

/**
 * A list of names with money against them: the label on the left at a plausible
 * measure, the figure hard right where the rupees will be. A money column whose
 * skeleton sits on the left teaches the eye the wrong place to look and then
 * moves the figure there.
 */
function MoneyRowSkeletons({ rows = 3, label }: { rows?: number; label: string }) {
  const widths = ['58%', '44%', '66%', '50%', '61%'];
  return (
    <div className="dc-skel-amts" role="status" aria-live="polite" aria-label={label}>
      {Array.from({ length: rows }, (_, i) => (
        <div className="dc-skel-amt" key={i} aria-hidden="true">
          <Skeleton w={widths[i % widths.length]} h={11} radius={999} />
          <Skeleton w={70} h={11} radius={999} />
        </div>
      ))}
    </div>
  );
}

/** The closed-day list, at the six-column grid its rows land in. */
function ClosedDaysSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="dc-skel-days" role="status" aria-live="polite" aria-label="Reading the closed days">
      {Array.from({ length: rows }, (_, i) => (
        <div className="dc-skel-day" key={i} aria-hidden="true">
          <span className="dy">
            <Skeleton w="72%" h={12} radius={999} />
            <Skeleton w="48%" h={9} radius={999} />
          </span>
          <span className="n"><Skeleton w={38} h={11} radius={999} /></span>
          <span className="amt"><Skeleton w={62} h={11} radius={999} /></span>
          <span className="amt"><Skeleton w={62} h={11} radius={999} /></span>
          <span className="amt"><Skeleton w={62} h={11} radius={999} /></span>
          <span />
        </div>
      ))}
    </div>
  );
}

/**
 * The whole review, waiting.
 *
 * The close card is drawn too. A day that turns out to be already closed
 * replaces it with the banner at the top of the page rather than with something
 * in its place, so nothing under it moves — and the un-closed day, which is what
 * a shopkeeper opens this screen for, gets its form reserved at full height
 * instead of shoved onto the page a moment after he starts reading.
 */
function ReviewSkeleton() {
  return (
    <>
      <StatSkeletons />
      <div className="grid two">
        <div className="stack">
          <Card title="What sold most"><MoneyRowSkeletons rows={4} label="Reading what sold" /></Card>
          <Card title="What went out"><MoneyRowSkeletons rows={3} label="Reading the day book" /></Card>
        </div>
        <div className="stack">
          <Card title="The drawer"><MoneyRowSkeletons rows={4} label="Reading the drawer" /></Card>
          <Card title="Where these figures come from" tight>
            <MoneyRowSkeletons rows={3} label="Reading where the figures come from" />
          </Card>
        </div>
      </div>
      <section className="card dc-close" aria-hidden="true">
        <header className="card-head"><h2>Close this day</h2></header>
        <div className="dc-close-body">
          <div className="dc-count dc-skel-block">
            <Skeleton w="36%" h={10} radius={999} />
            <Skeleton h={38} />
            <Skeleton h={188} />
          </div>
          <div className="dc-close-why dc-skel-block">
            <Skeleton w="42%" h={10} radius={999} />
            <Skeleton h={72} />
            <SkeletonText lines={3} />
          </div>
          <div className="dc-close-form dc-skel-block">
            <Skeleton h={92} />
            <Skeleton h={54} />
            <Skeleton h={68} />
            <Skeleton w="58%" h={44} />
          </div>
        </div>
      </section>
    </>
  );
}

/**
 * WHAT THIS DAY IS ABOUT TO BE FROZEN OVER.
 *
 * A close-out cannot be reopened. That is deliberate and it is the whole value
 * of the record — and it means a day closed while three webhook posts were
 * being refused, or with five bills the gateway was never asked for, is
 * permanently on file as a day that agreed with itself. The figures the close
 * freezes are NOT changed by anything here; this is what the shopkeeper reads
 * before he presses the button, and it is placed above the form for that
 * reason rather than tucked beside it.
 *
 * A day already closed still shows it: the disagreement did not go away when
 * the record was written, and it is the reason to look at the day again.
 */
function DayDisagreements({ rec, err, closed }: {
  rec: db.ReconcileBody | null;
  err: Refused | null;
  closed: boolean;
}) {
  if (!rec) {
    return (
      <Verdict tone="amber" title="This day has not been checked against the gateway">
        The reconciliation did not answer, so nothing is claimed either way about whether the
        till and the gateway agree on this day. The figures above are unaffected — what is
        missing is the check, not the takings. Closing is still allowed.
        {err && <><br /><span className="mono">{err.reason}</span></>}
      </Verdict>
    );
  }
  const d = rec.today;
  if (d.disagreements.length === 0) return null;
  return (
    <div className="stack" style={{ marginBottom: 18 }}>
      {d.disagreements.map((x) => (
        <Verdict
          key={x.code}
          /* Red is a refusal and nothing else. Money that has not moved is
             amber however alarming the count is. */
          tone={x.code === 'the_counter_refused_to_charge' ? 'red' : 'amber'}
          title={x.headline}
        >
          {x.detail}
          {!closed && (
            <> This does not stop the day being closed and it does not change any figure above.
            It is here because a closed day cannot be reopened.</>
          )}
        </Verdict>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ MILAN -- */

/**
 * THE DAY CLOSE, MATCHED AGAINST RAZORPAY'S OWN SETTLEMENT REPORT.
 *
 * Everything else in Books is one source, folded. This section is two: the
 * chain's settled bills and the gateway's report of what it paid into the
 * bank, matched by payment id and exact paise, with every disagreement named.
 * Nothing here computes a figure — the server sums the gateway's rows and
 * says which side each exception belongs to — and nothing here can mint or
 * charge. The one press, SETTLE FROM THE GATEWAY'S RECORD, sends a nonce to
 * the kernel's existing reconcile path, which looks the link up and settles
 * for exactly the intent's amount or parks it for a person.
 *
 * DEFAULT: YESTERDAY. UPI settles T+1, so today's report is empty until
 * tomorrow, and a section that opened on today would open on nothing.
 */
function MatchSection() {
  const [day, setDay] = useState<string>(mi.yesterdayLabel());
  const [body, setBody] = useState<mi.MatchBody | null>(null);
  const [err, setErr] = useState<Refused | null>(null);
  const [loading, setLoading] = useState(false);
  const [settling, setSettling] = useState<string | null>(null);
  const [settled, setSettled] = useState<mi.SettleBody | null>(null);
  const [settleErr, setSettleErr] = useState<Refused | null>(null);
  const [sweeping, setSweeping] = useState(false);
  const [swept, setSwept] = useState<mi.SimSettleBody | null>(null);
  const today = useMemo(() => todayLabel(), []);

  const run = useCallback(async (which: string) => {
    setLoading(true);
    setSettleErr(null);
    const r = await mi.match(which);
    if (r.ok) { setBody(r); setErr(null); } else { setBody(null); setErr(r); }
    setLoading(false);
  }, []);

  // A different day is a different report; the last one must not sit under
  // the new date while the read is in flight.
  useEffect(() => { setBody(null); setErr(null); setSettled(null); setSwept(null); }, [day]);

  const doSettle = useCallback(async (nonce: string) => {
    setSettling(nonce);
    setSettleErr(null);
    const r = await mi.settle(nonce);
    setSettling(null);
    if (r.ok) { setSettled(r); await run(day); } else { setSettleErr(r); }
  }, [run, day]);

  const doSweep = useCallback(async () => {
    setSweeping(true);
    const r = await mi.simSettle();
    setSweeping(false);
    if (r.ok) { setSwept(r); await run(day); } else { setSettleErr(r); }
  }, [run, day]);

  const x = body?.exceptions ?? null;
  const m = body?.matched ?? null;

  return (
    <Card
      title="Matched against the bank"
      sub="मिलान · Razorpay’s settlement report, row by row against the chain"
      aside={body ? (
        <span className="row" style={{ gap: 6 }}>
          {body.simulated
            ? <span className="pill dc-quiet">SIMULATOR ROWS</span>
            : <span className="pill dc-quiet">GATEWAY ROWS</span>}
          {body.exception_count === 0
            ? <span className="pill dc-quiet">NO EXCEPTIONS</span>
            : <Pill tone="amb">{body.exception_count} EXCEPTION{body.exception_count === 1 ? '' : 'S'}</Pill>}
        </span>
      ) : undefined}
    >
      <div className="mi-head">
        <Segmented
          value={day === today ? 'today' : day === mi.yesterdayLabel() ? 'yesterday' : 'other'}
          onChange={(v) => {
            if (v === 'today') setDay(today);
            else if (v === 'yesterday') setDay(mi.yesterdayLabel());
          }}
          options={[
            { value: 'yesterday', label: 'Yesterday', title: 'the report for yesterday — where a bill paid the day before lands' },
            { value: 'today', label: 'Today', title: 'the report for today — empty until tomorrow on the live gateway' },
            { value: 'other', label: 'Pick a day', title: 'any settlement day', disabled: day === today || day === mi.yesterdayLabel() },
          ]}
          size="sm"
        />
        <div className="mi-day">
          <label htmlFor="mi-day">settlement day</label>
          <input
            id="mi-day"
            type="date"
            value={day}
            max={today}
            onChange={(e) => setDay(e.target.value || mi.yesterdayLabel())}
          />
          <button
            className="btn sm primary"
            onClick={() => void run(day)}
            disabled={loading}
            aria-busy={loading || undefined}
            title="Read the gateway’s settlement report for this day and match it against the chain"
          >
            {loading ? 'MATCHING…' : 'MATCH'}
          </button>
        </div>
      </div>

      <p className="mi-cycle">
        <b>UPI settles T+1.</b> A bill paid today reaches the bank on the gateway’s next cycle and
        is filed in <b>tomorrow’s</b> report, so yesterday is the day to match. A bill the chain
        settled today is listed below as still with Razorpay, not as missing.
      </p>

      {err && (
        <Refusal
          reason="The report could not be matched"
          detail={err.reason}
          hint={err.detail}
          action={<button className="btn sm" onClick={() => void run(day)}>TRY AGAIN</button>}
        />
      )}

      {!body && !err && !loading && (
        <Empty title="Not matched yet" icon={false}>
          Press MATCH to read Razorpay’s settlement report for {dayLong(day)} and put it beside
          what the chain says settled. Nothing is written by reading it.
        </Empty>
      )}

      {loading && !body && <MoneyRowSkeletons rows={3} label="Reading the settlement report" />}

      {body && m && x && (
        <>
          <p className="mi-value" data-testid="mi-value-line">{body.value_line}</p>

          <div className="mi-stats">
            <div className="mi-stat">
              <span className="lbl">bills matched</span>
              <span className="val tnum" data-testid="mi-matched-count">{m.count}</span>
              <span className="sub">
                {m.by_webhook} on a verified webhook
                {m.by_kernel > 0 ? ` · ${m.by_kernel} reconciled from the gateway’s record` : ''}
              </span>
            </div>
            <div className="mi-stat">
              <span className="lbl">gross</span>
              <span className="val" data-testid="mi-gross">{money(m.gross_paise)}</span>
              <span className="sub">the gateway’s own amounts, summed</span>
            </div>
            <div className="mi-stat">
              <span className="lbl">fees and tax deducted</span>
              <span className="val" data-testid="mi-deducted">{money(m.deducted_paise)}</span>
              <span className="sub">fee {money(m.fee_paise)} · tax {money(m.tax_paise)}, as reported</span>
            </div>
            <div className="mi-stat net">
              <span className="lbl">net to the bank</span>
              <span className="val" data-testid="mi-net">{money(m.net_paise)}</span>
              <span className="sub">the gateway’s credit, summed — not gross minus fee</span>
            </div>
          </div>

          {settled && (
            <p className="mi-settled-note" data-testid="mi-settled-note">
              Intent <span className="mono">{settled.nonce}</span> for bill{' '}
              <span className="mono">{settled.session_id}</span> went{' '}
              <b>{settled.state_before} → {settled.state}</b>
              {settled.payment_id ? <> on payment <span className="mono">{settled.payment_id}</span></> : null}
              {settled.reason ? <> (<span className="mono">{settled.reason}</span>)</> : null}.{' '}
              {settled.settled
                ? 'The kernel looked the link up on the gateway and recorded what it found; nothing was minted or charged.'
                : 'The gateway did not report it paid, so nothing was settled.'}
              {settled.needs_human ? ' It is parked for a person.' : ''}
            </p>
          )}
          {settleErr && (
            <div style={{ marginBottom: 12 }}>
              <Refusal reason="Not settled" detail={settleErr.reason} hint={settleErr.detail} />
            </div>
          )}

          <ExceptionBlock
            code="in_recon_not_on_chain"
            title="In the report, not on the chain — the found money"
            bucket={x.in_recon_not_on_chain}
            why="Razorpay paid these out and no bill on this counter settled them. A row naming a bill this counter minted and never heard back about is a customer who paid while nobody was listening."
            head={['payment', 'bill on the counter', 'amount', 'credited', 'settled at', '']}
            render={(r: mi.FoundRow) => (
              <div className="mi-row" key={r.entity_id} data-testid="mi-found-row">
                <span className="id">
                  <span className="mono">{r.entity_id}</span>
                  {r.nonce && <span className="t">nonce <span className="mono">{r.nonce}</span></span>}
                </span>
                <span className="id">
                  {r.session_id
                    ? <><span className="mono">{r.session_id}</span>
                        <span className="t">{r.counter_state ? `kernel: ${r.counter_state}` : 'no intent on this counter'}{r.bill_at ? ` · billed ${when(r.bill_at)}` : ''}</span></>
                    : <span className="t">not minted by this counter</span>}
                </span>
                <span className="amt">{money(r.amount_paise)}</span>
                <span className="amt quiet">{money(r.credit_paise)}</span>
                <span className="amt quiet">{when(r.settled_at)}</span>
                <span className="act">
                  {r.settleable && r.nonce ? (
                    <button
                      className="btn sm primary"
                      disabled={settling !== null}
                      aria-busy={settling === r.nonce || undefined}
                      onClick={() => void doSettle(r.nonce as string)}
                      title="Run the kernel’s reconcile path: look this link up on the gateway and settle the intent for exactly its amount, or park it"
                      data-testid="mi-settle"
                    >
                      {settling === r.nonce ? 'ASKING THE GATEWAY…' : 'SETTLE FROM THE GATEWAY’S RECORD'}
                    </button>
                  ) : (
                    <span className="mi-by human">needs a person</span>
                  )}
                </span>
              </div>
            )}
          />

          <ExceptionBlock
            code="settled_not_yet_in_recon"
            title="Settled on the chain, still with Razorpay (T+1)"
            bucket={x.settled_not_yet_in_recon}
            why="A verified webhook settled these; the gateway files them in a later day’s report. Expected, and named so it is not mistaken for missing."
            head={['bill', 'payment', 'amount', 'settled', 'due in the report of', '']}
            render={(r: mi.ChainRow) => (
              <div className="mi-row" key={r.session_id}>
                <span className="id"><span className="mono">{r.session_id}</span><span className="t">billed {when(r.bill_at)}</span></span>
                <span className="id"><span className="mono">{r.payment_id ?? '—'}</span></span>
                <span className="amt">{money(r.amount_paise)}</span>
                <span className="amt quiet">{when(r.settled_at)}</span>
                <span className="amt quiet">{r.due_day ?? '—'}</span>
                <span className="act"><span className={`mi-by${r.settled_by === 'webhook' ? ' webhook' : ''}`}>{r.settled_by === 'webhook' ? 'webhook' : 'reconciled'}</span></span>
              </div>
            )}
          />

          <ExceptionBlock
            code="settled_not_in_recon"
            title="Settled on the chain, missing from this day’s report"
            bucket={x.settled_not_in_recon}
            why="These should be in this report by the T+1 rule and are not. The gateway’s word is missing and a person has to ask it why."
            head={['bill', 'payment', 'amount', 'settled', 'was due', '']}
            render={(r: mi.ChainRow) => (
              <div className="mi-row" key={r.session_id}>
                <span className="id"><span className="mono">{r.session_id}</span><span className="t">billed {when(r.bill_at)}</span></span>
                <span className="id"><span className="mono">{r.payment_id ?? '—'}</span></span>
                <span className="amt">{money(r.amount_paise)}</span>
                <span className="amt quiet">{when(r.settled_at)}</span>
                <span className="amt quiet">{r.due_day ?? '—'}</span>
                <span className="act"><span className="mi-by human">needs a person</span></span>
              </div>
            )}
          />

          <ExceptionBlock
            code="amount_mismatch"
            title="Matched by id, the paise disagree"
            bucket={x.amount_mismatch}
            why="The same payment, two different amounts. Parked and named; never rounded or corrected to either side."
            head={['payment', 'bill', 'gateway says', 'chain says', 'difference', '']}
            render={(r: mi.MatchedRow) => (
              <div className="mi-row" key={r.entity_id}>
                <span className="id"><span className="mono">{r.entity_id}</span></span>
                <span className="id"><span className="mono">{r.session_id ?? '—'}</span></span>
                <span className="amt">{money(r.amount_paise)}</span>
                <span className="amt">{money(r.bill_paise)}</span>
                <span className="amt quiet">{r.difference_paise === null || r.difference_paise === undefined ? '—' : money(r.difference_paise)}</span>
                <span className="act"><span className="mi-by human">needs a person</span></span>
              </div>
            )}
          />

          <ExceptionBlock
            code="refunds"
            title="Refunds in this report"
            bucket={x.refunds}
            why="Money the gateway sent back, debited from the batch. Listed, never netted against the bills."
            head={['refund', 'against payment', 'debited', '', 'settled at', '']}
            render={(r: mi.RefundRow) => (
              <div className="mi-row" key={r.entity_id}>
                <span className="id"><span className="mono">{r.entity_id}</span>{r.bill_session_id && <span className="t">bill <span className="mono">{r.bill_session_id}</span></span>}</span>
                <span className="id"><span className="mono">{r.payment_id ?? '—'}</span></span>
                <span className="amt">−{money(r.debit_paise)}</span>
                <span className="amt quiet" />
                <span className="amt quiet">{when(r.settled_at)}</span>
                <span className="act" />
              </div>
            )}
          />

          <ExceptionBlock
            code="adjustments"
            title="Adjustments and everything else the gateway filed"
            bucket={x.adjustments}
            why="Rows that are neither a payment nor a refund. Shown as they came."
            head={['row', 'type', 'amount', 'credit', 'debit', '']}
            render={(r: mi.MatchedRow) => (
              <div className="mi-row" key={r.entity_id}>
                <span className="id"><span className="mono">{r.entity_id}</span></span>
                <span className="id"><span className="mono">{r.type}</span></span>
                <span className="amt">{money(r.amount_paise)}</span>
                <span className="amt quiet">{money(r.credit_paise)}</span>
                <span className="amt quiet">{money(r.debit_paise)}</span>
                <span className="act" />
              </div>
            )}
          />

          {x.unreadable_rows.count > 0 && (
            <div className="mi-block">
              <div className="mi-block-head">
                <h3>Rows this counter would not read</h3>
                <span className="mi-code">unreadable_rows</span>
                <span className="mi-n"><b>{x.unreadable_rows.count}</b></span>
              </div>
              <p className="mi-why">A money field that is not whole paise is not money; these are abstained on, never coerced.</p>
              {x.unreadable_rows.rows.map((r, i) => (
                <p className="mi-none" key={`${r.entity_id ?? 'row'}-${i}`}>
                  <span className="mono">{r.entity_id ?? '(no id)'}</span> — {r.why}
                </p>
              ))}
            </div>
          )}

          <div className="mi-block">
            <div className="mi-block-head">
              <h3>Matched</h3>
              <span className="mi-code">matched</span>
              <span className="mi-n"><b>{m.count}</b> row{m.count === 1 ? '' : 's'}</span>
            </div>
            {m.count === 0 ? (
              <p className="mi-none">No row in this report matched a settled bill on the chain.</p>
            ) : (
              <div className="mi-rows">
                <div className="mi-rows-head" aria-hidden="true">
                  <span>payment</span><span>bill</span>
                  <span className="r">amount</span><span className="r">fee + tax</span><span className="r">credited</span><span />
                </div>
                {m.rows.slice(0, 60).map((r) => (
                  <div className="mi-row" key={r.entity_id} data-testid="mi-matched-row">
                    <span className="id"><span className="mono">{r.entity_id}</span><span className="t">{when(r.settled_at)}</span></span>
                    <span className="id"><span className="mono">{r.session_id ?? '—'}</span><span className="t">billed {when(r.bill_at)}</span></span>
                    <span className="amt">{money(r.amount_paise)}</span>
                    <span className="amt quiet">{money((r.fee_paise ?? 0) + (r.tax_paise ?? 0))}</span>
                    <span className="amt">{money(r.credit_paise)}</span>
                    <span className="act">
                      <span className={`mi-by${r.settled_by === 'webhook' ? ' webhook' : ''}`}>
                        {r.settled_by === 'webhook' ? 'webhook' : 'reconciled'}
                      </span>
                    </span>
                  </div>
                ))}
                {m.rows.length > 60 && (
                  <p className="mi-none">Showing 60 of {m.rows.length} matched rows.</p>
                )}
              </div>
            )}
          </div>

          <div className="mi-foot">
            {body.chain.ok
              ? <Pill tone="ok">CHAIN VERIFIED · {body.chain.lines_verified} lines</Pill>
              : <Pill tone="bad">CHAIN BROKEN</Pill>}
            <span className="pill dc-quiet">{body.settlement_cycle}</span>
            {body.earlier_days.count > 0 && (
              <span>{body.earlier_days.count} settled bill{body.earlier_days.count === 1 ? '' : 's'} belong to earlier reports and {body.earlier_days.count === 1 ? 'is' : 'are'} not this day’s business.</span>
            )}
            {body.mode === 'sim' && (
              <button
                className="btn sm ghost"
                onClick={() => void doSweep()}
                disabled={sweeping}
                aria-busy={sweeping || undefined}
                title="Simulator only: file today’s captured payments in a batch now, the way Razorpay’s on-demand settlement would. Amounts are untouched. Refused by name on the live gateway."
                data-testid="mi-sim-settle"
              >
                {sweeping ? 'FILING…' : 'SIMULATOR: RUN THE SETTLEMENT BATCH NOW'}
              </button>
            )}
            {swept && (
              <span>
                Batch <span className="mono">{swept.settlement_id}</span> filed {swept.payments} payment{swept.payments === 1 ? '' : 's'}, {money(swept.amount_settled)} net. Match today to see it.
              </span>
            )}
          </div>
          <p className="hint">{body.derived_from}</p>
        </>
      )}
    </Card>
  );
}

/**
 * One exception class, drawn even when empty: the list of what was checked is
 * the list of what could have been wrong, and an absent heading reads as an
 * unchecked one.
 */
function ExceptionBlock<R>({ code, title, bucket, why, head, render }: {
  code: string;
  title: string;
  bucket: mi.Bucket<R>;
  why: string;
  head: string[];
  render: (row: R) => JSX.Element;
}) {
  return (
    <div className="mi-block" data-testid={`mi-x-${code}`}>
      <div className="mi-block-head">
        <h3>{title}</h3>
        <span className="mi-code">{code}</span>
        <span className="mi-n">
          <b>{bucket.count}</b>
          {bucket.count > 0 && bucket.paise !== null ? <> · {money(bucket.paise)}</> : null}
        </span>
      </div>
      <p className="mi-why">{why}</p>
      {bucket.count === 0 ? (
        <p className="mi-none">none</p>
      ) : (
        <div className="mi-rows">
          <div className="mi-rows-head" aria-hidden="true">
            {head.map((h, i) => <span key={`${code}-${i}`} className={i >= 2 && i <= 4 ? 'r' : ''}>{h}</span>)}
          </div>
          {bucket.rows.map(render)}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, sub, tone, quiet }: {
  label: string; value: string; sub: string; tone?: 'green'; quiet?: boolean;
}) {
  return (
    <div className={`dc-stat${tone ? ` ${tone}` : ''}`}>
      <span className="lbl">{label}</span>
      <span className={quiet ? 'val quiet' : 'val'}>{value}</span>
      <span className="sub">{sub}</span>
    </div>
  );
}

/**
 * A refusal INSIDE a card, so one unavailable source does not take the page
 * with it. The server's own reason is kept verbatim — it is the machine's
 * account of what it would not do, and a paraphrase would hide the useful part.
 */
function InlineRefusal({ what, err, retry }: {
  what: string; err: Refused; retry?: () => void;
}) {
  return (
    <Refusal
      reason={`${what} could not be read`}
      detail={err.reason}
      hint={err.detail}
      action={retry ? <button className="btn sm" onClick={retry}>TRY AGAIN</button> : undefined}
    />
  );
}

function Sellers({ rows, frozen }: { rows: db.TopSeller[]; frozen: boolean }) {
  return (
    <div className="dc-sellers">
      {rows.map((s, i) => (
        <div className="dc-seller" key={s.sku_id}>
          <span className="rank">{i + 1}</span>
          <span className="nm">
            {s.name}
            {frozen && !s.in_catalogue_at_close && (
              <span className="muted"> — not in the catalogue when the day was closed</span>
            )}
          </span>
          <span className="units tnum">{s.units}×</span>
          <span className="amt tnum">{money(s.revenue_paise)}</span>
        </div>
      ))}
    </div>
  );
}

/* --------------------------------------------------------- one closed day -- */

const DIFF_LABEL: Record<string, string> = {
  bills: 'bills closed',
  revenue_paise: 'billed',
  settled_count: 'bills settled',
  settled_paise: 'settled',
  awaiting_count: 'bills awaiting',
  awaiting_paise: 'awaiting the gateway',
  excluded_lines: 'lines excluded',
};

/**
 * A closed day, rendered as the snapshot it is.
 *
 * The record is drawn from the frozen document and from nothing else. What the
 * chain says about the day NOW lives in its own block below, labelled, with the
 * difference stated per field — because the two are different claims and a
 * screen that merged them would be quietly rewriting a record.
 */
function ClosedDay({ day, body, err }: {
  day: string; body: db.OneDayBody | null; err: Refused | null;
}) {
  if (err) {
    return (
      <Card title={dayLong(day)}>
        <Refusal
          reason="This closed day could not be read"
          detail={err.reason}
          hint={err.detail}
        />
      </Card>
    );
  }
  if (!body) {
    return (
      <Card title={dayLong(day)}>
        <div className="dc-skel-record" role="status" aria-live="polite" aria-label="Reading the record">
          <SkeletonText lines={3} />
          <div className="dc-frozen-grid" aria-hidden="true">
            {[0, 1, 2, 3].map((i) => (
              <div className="dc-stat dc-skel-stat" key={i}>
                <Skeleton w="62%" h={10} radius={999} />
                <Skeleton w="76%" h={24} />
                <Skeleton w="88%" h={10} radius={999} />
              </div>
            ))}
          </div>
          <SkeletonText lines={2} />
        </div>
      </Card>
    );
  }

  const r = body.record;
  const f = r.derived;
  const after = body.after_close;
  const moved = after?.changed_fields ?? [];

  return (
    <Card
      title={dayLong(r.day)}
      aside={
        <span className="row" style={{ gap: 6 }}>
          {!r.day_had_ended && <span className="pill dc-quiet">CLOSED EARLY</span>}
          {body.record_unedited
            ? <span className="pill dc-quiet">RECORD UNEDITED</span>
            : <Pill tone="bad">RECORD EDITED SINCE</Pill>}
        </span>
      }
    >
      <p className="dc-frozen">
        Frozen at {when(r.closed_at)}
        {r.closed_by ? ` by ${r.closed_by}` : ''}. These figures are not recomputed when this page
        opens, and they never will be. The audit chain keeps growing — a bill closed after the
        shutter came down, a webhook that settles the next morning, a product renamed next month —
        so a day re-derived later would not read the same, and a record that changes is not a record
        of anything. What the chain says about this day now is below, separately.
      </p>

      <div className="dc-frozen-grid">
        <Stat label="billed" value={money(f.revenue_paise)} sub={`${f.bills} bill${f.bills === 1 ? '' : 's'}`} />
        <Stat
          label="settled"
          value={money(f.settled_paise)}
          sub={`${f.settled_count} of ${f.bills}`}
          tone={f.settled_count > 0 ? 'green' : undefined}
        />
        <Stat label="awaiting" value={money(f.awaiting_paise)} sub={`${f.awaiting_count} not settled`} />
        <Stat label="counted in the drawer" value={money(r.counted_cash_paise)} sub="the shopkeeper’s own count" />
      </div>

      <p className="hint">
        The counted cash is not compared with the takings here, and nothing on this screen computes a
        difference between them. They are not the same thing: the takings include bills the gateway
        settled straight to the account, and the drawer includes the float the day started with and
        whatever was paid out of it. The cash drawer on the Expenses screen reconciles the drawer;
        this record witnesses what was counted.
      </p>

      {r.note && (
        <div className="dc-note">
          <span className="dc-eyebrow">note on the close</span>
          <p>{r.note}</p>
        </div>
      )}

      {r.chain_warning_at_close && (
        <div className="dc-block">
          <Verdict tone="red" title="This day was closed over a chain that did not verify">
            {r.chain_warning_at_close}
          </Verdict>
        </div>
      )}

      {r.top_sellers.length > 0 && (
        <div className="dc-block">
          <p className="dc-eyebrow">what sold most, named as the catalogue read that day</p>
          <Sellers rows={r.top_sellers} frozen />
        </div>
      )}

      <div className="dc-block">
        <p className="dc-eyebrow">what the chain says about this day now</p>
        {body.after_close_unavailable ? (
          <Verdict tone="amber" title="The chain could not be re-read for this day">
            The record above is still exactly what was frozen. Only the comparison is missing.
            <br />
            <span className="mono">{body.after_close_unavailable}</span>
          </Verdict>
        ) : !after ? (
          <Empty title="No comparison was returned" icon={false}>
            The record above is exactly what was frozen and is unaffected. Only the second
            reading is missing — reopen this day to ask the chain again.
          </Empty>
        ) : !after.changed ? (
          <p className="dc-same">
            <span className="pill dc-quiet">UNCHANGED</span> {after.note}
          </p>
        ) : (
          <>
            <div className="dc-diff">
              <div className="dc-diff-head">
                <span>figure</span><span>frozen</span><span>now</span><span>difference</span>
              </div>
              {moved.map((key) => (
                <div className="dc-diff-row" key={key}>
                  <span className="k">{DIFF_LABEL[key] ?? key}</span>
                  <span className="tnum">{cell(key, numberOf(f, key))}</span>
                  <span className="tnum">{cell(key, numberOf(after.derived_now, key))}</span>
                  <span className="tnum delta">{signed(key, after.difference[key])}</span>
                </div>
              ))}
            </div>
            <p className="hint">{after.note}</p>
          </>
        )}
        {after?.chain_warning && (
          <Verdict tone="red" title="The chain does not verify right now">
            {after.chain_warning}
          </Verdict>
        )}
      </div>

      <div className="dc-block" data-testid="dc-milan">
        <p className="dc-eyebrow">what the bank received for this day, per Razorpay’s report</p>
        {body.milan_unavailable ? (
          <Verdict tone="amber" title="The settlement report could not be read for this day">
            The record above is exactly what was frozen. Only the match is missing.
            <br />
            <span className="mono">{body.milan_unavailable}</span>
          </Verdict>
        ) : !body.milan ? (
          <p className="dc-same"><span className="pill dc-quiet">NOT READ</span> No settlement match was returned.</p>
        ) : (
          <>
            <p className="mi-value">{body.milan.value_line}</p>
            <div className="dc-diff">
              <div className="dc-diff-head">
                <span>report of {body.milan.settlement_day}</span><span>count</span><span>amount</span><span />
              </div>
              <div className="dc-diff-row">
                <span className="k">matched</span>
                <span className="tnum">{body.milan.matched.count}</span>
                <span className="tnum">{money(body.milan.matched.net_paise)}</span>
                <span className="tnum">net</span>
              </div>
              {Object.entries(body.milan.exceptions).map(([name, b]) => (
                <div className="dc-diff-row" key={name}>
                  <span className="k">{name}</span>
                  <span className="tnum">{b.count}</span>
                  <span className="tnum">{b.paise === null ? '—' : money(b.paise)}</span>
                  <span className="tnum">{b.count > 0 ? 'named' : '—'}</span>
                </div>
              ))}
            </div>
            <p className="hint">{body.milan.note}{body.milan.simulated ? ' The rows are the simulator’s and say so.' : ''}</p>
          </>
        )}
      </div>

      <div className="dc-block">
        <p className="dc-eyebrow">what stands behind this record</p>
        {/* One reading of the moment, not two. `when` already renders the UTC
            stamp in the reader's own timezone, so printing `closed_at_local`
            beside it says the same thing twice. */}
        <KV k="closed at">{when(r.closed_at)}</KV>
        <KV k="day had ended">{r.day_had_ended
          ? 'yes'
          : `no — ${hours(r.seconds_left_in_day_at_close)} of the day were still to run`}</KV>
        <KV k="chain at close">
          {r.chain_at_close.ok
            ? <Pill tone="ok">VERIFIED · {r.chain_at_close.lines_verified} lines</Pill>
            : <Pill tone="bad">BROKEN</Pill>}
        </KV>
        <KV k="record digest"><span className="mono">{r.record_sha256}</span></KV>
        <KV k="chain line">
          {r.audit_head
            ? <span className="mono">{r.audit_head}</span>
            : <span className="muted">not written — nothing on the chain stands behind this record</span>}
        </KV>
        <p className="hint">
          The digest is recomputed from the file as it was served and compared with the one stored
          inside it. Matching means nothing has edited the record since the chain line was written;
          the chain line is what makes that check worth anything.
        </p>
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ small -- */

/** A number off a brief, or null. Never coerced, never read as zero. */
function numberOf(brief: db.Derived, key: string): number | null {
  const v = (brief as unknown as Record<string, unknown>)[key];
  return typeof v === 'number' && Number.isInteger(v) ? v : null;
}

/** Money keys are rendered as money; counts are rendered as counts. */
function cell(key: string, value: number | null): string {
  if (value === null) return '—';
  return key.endsWith('_paise') ? money(value) : String(value);
}

/**
 * A difference, with its sign kept. A negative one is not an error and is not
 * hidden: it means the chain no longer verifies as far as it did when the day
 * was closed, so the LIVE figures are the short ones.
 */
function signed(key: string, value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  if (value === 0) return '—';
  const sign = value > 0 ? '+' : '−';
  const size = value > 0 ? value : -value;
  return `${sign}${key.endsWith('_paise') ? money(size) : String(size)}`;
}

/** Today, in the counter's own timezone, written the way the server writes one. */
function todayLabel(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

function dayLong(day: string): string {
  const d = new Date(`${day}T00:00:00`);
  return Number.isNaN(d.getTime()) ? day
    : d.toLocaleDateString('en-IN', {
      weekday: 'long', day: '2-digit', month: 'long', year: 'numeric',
    });
}

function dayShort(day: string): string {
  const d = new Date(`${day}T00:00:00`);
  return Number.isNaN(d.getTime()) ? day
    : d.toLocaleDateString('en-IN', { weekday: 'short', day: '2-digit', month: 'short' });
}

function clock(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso
    : d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true });
}

/** A duration in plain English. Integer arithmetic, like the server's. */
function hours(seconds: number): string {
  const s = Math.trunc(seconds);
  if (s <= 0) return 'none';
  // "0 minutes still to run" is not a thing anybody says.
  if (s < 60) return 'less than a minute';
  const h = Math.trunc(s / 3600);
  const m = Math.trunc((s % 3600) / 60);
  if (h === 0) return `${m} minute${m === 1 ? '' : 's'}`;
  if (m === 0) return `${h} hour${h === 1 ? '' : 's'}`;
  return `${h}h ${m}m`;
}
