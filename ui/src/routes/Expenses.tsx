import { useCallback, useEffect, useMemo, useState } from 'react';
import * as exp from '../lib/expapi';
import {
  Card, KV, Pill, Verdict, Empty, Refusal, Field, Button, Skeleton,
} from '../components/ui';
import '../styles/expenses.css';

/**
 * EXPENSES AND THE CASH DRAWER — kharcha, and what should be in the till.
 *
 * Two halves of one question a shopkeeper asks at closing time: what went out
 * today, and does the drawer agree.
 *
 * THE DIFFERENCE IS A FACT AND IS DRAWN AS ONE. A drawer that is short is not
 * coloured red on this page and a drawer that matches is not coloured green.
 * Both would be a verdict on a person, and this counter cannot see who did
 * what — it can see what it billed and what the gateway settled; it cannot see
 * a note change hands. So the gap is set in plain ink at the size of a fact,
 * beside the server's own sentence about why drawers go over and short for
 * ordinary reasons. The only green on this screen is the money a
 * signature-verified webhook actually settled, which is the one thing green is
 * for anywhere in this product.
 *
 * NOTHING HERE COMPUTES MONEY. Amounts go up as the string the shopkeeper
 * typed and come back as integer paise the server derived; the page formats
 * them. The expected closing figure in particular is the server's arithmetic,
 * not a subtraction done in the browser, because two processes that both work
 * out what should be in the drawer will one day disagree in front of a
 * customer.
 *
 * WHY THE EXPECTED FIGURE CAN BE ABSENT. Until the opening float is counted
 * there is no expected closing, and this page renders that as a missing step
 * rather than as zero. A drawer that started with two thousand rupees of change
 * measured against an assumed empty opening reads as two thousand over, and a
 * number wrong by exactly the float is worse than no number, because it looks
 * like an answer.
 */

/** What the shopkeeper typed, read back before it is sent. Two decimals at
    most, and the string goes up untouched — see lib/expapi.ts. */
const RUPEES_RE = /^\d{1,7}(\.\d{1,2})?$/;

/** A refusal, as every request module on this counter shapes one. */
type Trouble = { reason: string; detail?: string };

/**
 * WHICH BOX THE SERVER REFUSED, by the reason's own name.
 *
 * `gawaah/expenses.py` names the state it refused on rather than describing it —
 * `rupee_string_not_money`, `amount_not_positive`, `other_needs_a_note`,
 * `category_not_on_the_list` — so the refusal can be shown under the box that
 * caused it instead of only at the foot of the form. This decides WHERE it is
 * shown and nothing else: the reason and detail are rendered exactly as they
 * arrived either way, and anything unrecognised stays at the foot, which is the
 * safe direction.
 */
function whichBox(r: Trouble | null): 'amount' | 'category' | 'note' | 'paid' | null {
  if (!r) return null;
  const n = r.reason;
  if (n.startsWith('amount_') || n === 'rupee_string_not_money') return 'amount';
  if (n.startsWith('category_')) return 'category';
  if (n === 'other_needs_a_note' || n === 'note_too_long') return 'note';
  if (n.startsWith('paid_with')) return 'paid';
  return null;
}

/**
 * The server's own account of a refusal, for the line under a field: its name
 * for the state, then its sentence. Neither is rewritten — the name is what a
 * maintainer greps for and the sentence is what a shopkeeper acts on.
 */
function said(t: Trouble) {
  return <><span className="mono">{t.reason}</span>{t.detail ? ` — ${t.detail}` : ''}</>;
}

/** The client's own shape check, named with the state the SERVER would refuse
    it under, so one string covers both sides of the same rejection. */
const NOT_MONEY = (
  <><span className="mono">rupee_string_not_money</span>
    {' — rupees and paise, two decimal places at most. 40, or 40.50. '}
    {'Nothing here turns what you typed into a number; the counter parses it.'}</>
);

export default function Expenses() {
  const [day, setDay] = useState<string>(() => exp.todayLabel());

  const [cats, setCats] = useState<exp.CategoriesBody | null>(null);
  const [catsErr, setCatsErr] = useState<Trouble | null>(null);

  const [book, setBook] = useState<exp.DayBody | null>(null);
  const [bookErr, setBookErr] = useState<Trouble | null>(null);

  const [drawer, setDrawer] = useState<exp.CashBody | null>(null);
  const [drawerErr, setDrawerErr] = useState<Trouble | null>(null);

  const [loading, setLoading] = useState(true);

  /* the entry form */
  const [category, setCategory] = useState<string>('');
  const [amount, setAmount] = useState('');
  const [note, setNote] = useState('');
  const [paidWith, setPaidWith] = useState<exp.PaidWith>('cash');
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<Trouble | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  /* voiding one row */
  const [voidFor, setVoidFor] = useState<string | null>(null);
  const [voidReason, setVoidReason] = useState('');
  const [voiding, setVoiding] = useState(false);
  const [voidErr, setVoidErr] = useState<Trouble | null>(null);
  // What was struck out, said back. A void is not undoable and the row it
  // changed is one of many on a list; without this the only sign the press did
  // anything is a strikethrough somewhere above the fold.
  const [voided, setVoided] = useState<string | null>(null);

  /* the two counts */
  const [openingIn, setOpeningIn] = useState('');
  const [closingIn, setClosingIn] = useState('');
  const [closingNote, setClosingNote] = useState('');
  // A refusal belongs to the count it refused. One shared slot put an opening
  // refusal under the closing box.
  const [countErr, setCountErr] = useState<{ kind: 'opening' | 'closing'; t: Trouble } | null>(null);
  const [counted, setCounted] = useState<'opening' | 'closing' | null>(null);
  const [counting, setCounting] = useState<'opening' | 'closing' | null>(null);
  const [recount, setRecount] = useState<'opening' | 'closing' | null>(null);

  const load = useCallback(async (d: string) => {
    // Both halves are asked for at once and each keeps its own refusal: a day
    // book that cannot be read must not blank the drawer beside it.
    const [b, c] = await Promise.all([exp.day(d), exp.cash(d)]);
    if (b.ok) { setBook(b); setBookErr(null); } else { setBook(null); setBookErr(b); }
    if (c.ok) { setDrawer(c); setDrawerErr(null); } else { setDrawer(null); setDrawerErr(c); }
    setLoading(false);
  }, []);

  const loadCats = useCallback(async () => {
    const r = await exp.categories();
    if (r.ok) { setCats(r); setCatsErr(null); } else { setCats(null); setCatsErr(r); }
  }, []);

  useEffect(() => { void loadCats(); }, [loadCats]);

  useEffect(() => {
    setLoading(true);
    setVoidFor(null);
    setRecount(null);
    setCountErr(null);
    setCounted(null);
    // The confirmation belongs to the day it was written against; carrying it
    // across would read as something having been recorded on this one.
    setSaved(null);
    setSaveErr(null);
    setVoided(null);
    setVoidErr(null);
    void load(day);
  }, [day, load]);

  const isToday = day === exp.todayLabel();
  const rows = book?.expenses ?? [];
  const cash = drawer;

  /** The biggest category, so the bars below are proportional to something
      real. Integer comparison only — no money is computed here. */
  const widest = useMemo(
    () => (book?.by_category ?? []).reduce((m, c) => (c.paise > m ? c.paise : m), 0),
    [book],
  );

  const submit = useCallback(async () => {
    setSaving(true);
    setSaveErr(null);
    setSaved(null);
    const r = await exp.add({
      amount_rupees: amount.trim(),
      category,
      note: note.trim(),
      paid_with: paidWith,
      day,
    });
    setSaving(false);
    if (r.ok) {
      setAmount('');
      setNote('');
      setSaved(`${r.expense.category_label} · ${exp.money(r.expense.amount_paise)}`);
      await load(day);
    } else {
      setSaveErr(r);
    }
  }, [amount, category, note, paidWith, day, load]);

  const doVoid = useCallback(async (id: string, what: string) => {
    setVoiding(true);
    setVoidErr(null);
    setVoided(null);
    const r = await exp.voidExpense(id, voidReason.trim());
    setVoiding(false);
    if (r.ok) {
      setVoidFor(null);
      setVoidReason('');
      setVoided(what);
      await load(day);
    } else {
      setVoidErr(r);
    }
  }, [voidReason, day, load]);

  const count = useCallback(async (kind: 'opening' | 'closing') => {
    setCounting(kind);
    setCountErr(null);
    setCounted(null);
    const r = kind === 'opening'
      ? await exp.countOpening(openingIn.trim(), day)
      : await exp.countClosing(closingIn.trim(), day, closingNote.trim());
    setCounting(null);
    if (r.ok) {
      if (kind === 'opening') setOpeningIn(''); else { setClosingIn(''); setClosingNote(''); }
      setRecount(null);
      setCounted(kind);
      await load(day);
    } else {
      setCountErr({ kind, t: r });
    }
  }, [openingIn, closingIn, closingNote, day, load]);

  const amountLooksRight = RUPEES_RE.test(amount.trim());
  const canSubmit = !!category && amountLooksRight
    && (category !== 'other' || !!note.trim());
  // A disabled button that does not say why reads as a broken one. This is the
  // sentence beside it and the title on it, and it names the FIRST thing still
  // missing rather than all of them at once.
  const whyNotSubmit = !category ? 'Pick what kind of cost this was.'
    : !amount.trim() ? 'Type how many rupees went out.'
      : !amountLooksRight ? 'That is not a rupee amount yet.'
        : (category === 'other' && !note.trim()) ? 'Other needs a note saying what it was for.'
          : null;
  // Where the server put its refusal, when it named a box.
  const saveBox = whichBox(saveErr);

  return (
    <div className="exp-page">
      <div className="page-head">
        <h1>Expenses</h1>
        <p>
          What the shop paid out, and what should be in the drawer at the end of the day.
          The spending is what you enter; the sales side is counted from this counter&rsquo;s own
          audit chain. One day at a time — there is no week or month view here.
        </p>
      </div>

      <DayBar day={day} isToday={isToday} onDay={setDay} />

      {cash?.chain_warning && (
        <Verdict tone="amber" title="The audit chain stops part way through">
          {cash.chain_warning}
        </Verdict>
      )}

      <div className="exp-grid">
        <div className="stack">
          {/* ------------------------------------------------ record one -- */}
          <Card
            title="Record what you paid for"
            aside={<Pill tone="off">NOTHING IS PAID FROM HERE</Pill>}
          >
            {catsErr ? (
              <>
                <Refusal
                  reason="The category list could not be read"
                  detail={catsErr.reason}
                  hint={catsErr.detail}
                  action={<button className="btn sm" onClick={() => void loadCats()}>TRY AGAIN</button>}
                />
                <p className="hint">
                  Nothing can be recorded until it can be read. The list of categories belongs to
                  the server and this page will not invent one.
                </p>
              </>
            ) : !cats ? (
              <FormSkeleton />
            ) : (
              <div className="exp-form">
                <div className={saveBox === 'category' ? 'field bad' : 'field'}>
                  <label id="exp-cat-label">What kind of cost</label>
                  <div className="exp-cats" role="group" aria-labelledby="exp-cat-label">
                    {cats.categories.map((c) => (
                      <button
                        key={c.category}
                        type="button"
                        aria-pressed={category === c.category}
                        onClick={() => { setCategory(c.category); setSaveErr(null); }}
                      >
                        {c.label}
                      </button>
                    ))}
                  </div>
                  {!category && <span className="sub">Pick one. Anything that does not fit goes under Other.</span>}
                  {saveBox === 'category' && saveErr && <span className="err">{said(saveErr)}</span>}
                </div>

                <div className="exp-two">
                  {/* TEXT, NOT number. A decimal in a number input arrives as a
                      float and 12.10 is lossy before anything rounds it. The
                      server parses the string to integer paise — and a shape
                      this page will not send is REFUSED BY NAME under the box,
                      never quietly rounded into one it would send. */}
                  <Field
                    label="How much, in rupees"
                    htmlFor="exp-amount"
                    /* The cap read off the server and grouped the Indian way
                       here, rather than printing its ungrouped rupee string —
                       "₹100000.00" is a number a shopkeeper has to count the
                       zeroes of. */
                    sub={`Up to ${exp.money(cats.max_expense_paise)} on one line. A larger payment has to be entered as two, and both stay visible.`}
                    error={
                      saveBox === 'amount' && saveErr ? said(saveErr)
                        : amount.trim() !== '' && !amountLooksRight ? NOT_MONEY
                          : undefined
                    }
                  >
                    <input
                      id="exp-amount"
                      type="text"
                      inputMode="decimal"
                      autoComplete="off"
                      placeholder="40.00"
                      value={amount}
                      aria-invalid={(amount.trim() !== '' && !amountLooksRight) || saveBox === 'amount'}
                      onChange={(e) => { setAmount(e.target.value); setSaveErr(null); }}
                    />
                  </Field>

                  <div className={saveBox === 'paid' ? 'field bad' : 'field'}>
                    <label id="exp-paid-label">Paid with</label>
                    <div className="seg" role="group" aria-labelledby="exp-paid-label">
                      <button
                        type="button"
                        aria-pressed={paidWith === 'cash'}
                        onClick={() => setPaidWith('cash')}
                      >
                        CASH
                      </button>
                      <button
                        type="button"
                        aria-pressed={paidWith === 'bank'}
                        onClick={() => setPaidWith('bank')}
                      >
                        BANK
                      </button>
                    </div>
                    <span className="sub">
                      Only cash comes out of the drawer. A bank payment is counted in the day&rsquo;s
                      spending and left out of the cash position.
                    </span>
                    {saveBox === 'paid' && saveErr && <span className="err">{said(saveErr)}</span>}
                  </div>
                </div>

                <Field
                  label={<>What it was for{' '}
                    <span className="muted">{category === 'other' ? '(needed)' : '(optional)'}</span></>}
                  htmlFor="exp-note"
                  sub={category === 'other'
                    ? 'Other needs a note, so the line still explains itself in three months.'
                    : undefined}
                  error={saveBox === 'note' && saveErr ? said(saveErr) : undefined}
                >
                  <input
                    id="exp-note"
                    type="text"
                    placeholder={category === 'other' ? 'say what this was' : 'shop electricity, June'}
                    maxLength={cats.max_note}
                    value={note}
                    aria-invalid={saveBox === 'note'}
                    onChange={(e) => { setNote(e.target.value); setSaveErr(null); }}
                  />
                </Field>

                <EntryPreview
                  amount={amount}
                  ok={amountLooksRight}
                  label={cats.categories.find((c) => c.category === category)?.label ?? ''}
                  paidWith={paidWith}
                  day={day}
                  isToday={isToday}
                />

                <div className="btn-row">
                  {/* `loading` disables it, so a second press on a slow
                      connection cannot become a second expense. */}
                  <Button
                    variant="primary"
                    loading={saving}
                    disabled={!canSubmit}
                    title={whyNotSubmit ?? undefined}
                    onClick={() => void submit()}
                  >
                    {saving ? 'RECORDING…' : 'RECORD IT'}
                  </Button>
                  {/* ONE SENTENCE BESIDE THE BUTTON, in the order that matters:
                      what it is doing, then what it just did, then why it will
                      not fire. A save empties the form, so the gate and the
                      confirmation are both true the moment a write lands — and
                      "type an amount" beside "Recorded" reads as a complaint
                      about the thing that just worked. */}
                  {saving ? (
                    <span className="exp-gate">Writing it to the day book…</span>
                  ) : saved ? (
                    <span className="exp-saved">Recorded · {saved}</span>
                  ) : whyNotSubmit ? (
                    <span className="exp-gate">{whyNotSubmit}</span>
                  ) : null}
                </div>

                {/* Only what could not be pinned on a box: a refusal about the
                    amount is already under the amount, and one refusal shown
                    twice reads as two refusals. */}
                {saveErr && !saveBox && (
                  <div className="exp-inline-refusal">
                    <Refusal reason={saveErr.reason} detail={saveErr.detail} />
                  </div>
                )}
              </div>
            )}
          </Card>

          {/* --------------------------------------------------- the day -- */}
          <Card
            title={isToday ? 'What went out today' : `What went out on ${exp.dayName(day)}`}
            aside={book ? <Pill tone="off">{rows.length} {rows.length === 1 ? 'ENTRY' : 'ENTRIES'}</Pill> : undefined}
          >
            {bookErr ? (
              <Refusal
                reason="The day book could not be read"
                detail={bookErr.reason}
                hint={bookErr.detail}
                action={<button className="btn sm" onClick={() => void load(day)}>TRY AGAIN</button>}
              />
            ) : loading || !book ? (
              <DaySkeleton />
            ) : (
              <>
                <div className="exp-tiles">
                  <Tile label="paid out" value={exp.money(book.total_paise)}
                        sub={`${book.count} ${book.count === 1 ? 'entry' : 'entries'} counted`} />
                  <Tile label="in cash" value={exp.money(book.cash_paise)}
                        sub={`${book.cash_count} out of the drawer`} />
                  <Tile label="from the bank" value={exp.money(book.bank_paise)}
                        sub={`${book.bank_count} not out of the drawer`} />
                </div>

                {book.by_category.length > 0 && (
                  <div className="exp-cat-list">
                    {book.by_category.map((c) => (
                      <div className="exp-cat-row" key={c.category}>
                        <span className="lbl">{c.label}</span>
                        <span className="bar" aria-hidden="true">
                          {/* A width, not a price. The percentage is display
                              arithmetic on a ratio of two integers and never
                              reaches a rupee figure — every amount on this page
                              is the server's own integer paise. */}
                          <i style={{ width: `${widest > 0 ? Math.round((c.paise * 100) / widest) : 0}%` }} />
                        </span>
                        <span className="cnt">{c.count}×</span>
                        <span className="amt tnum">{exp.money(c.paise)}</span>
                      </div>
                    ))}
                  </div>
                )}

                {voided && (
                  <Verdict tone="info" title="Struck out">
                    {voided} is voided. It stays on the list with its amount and its note, and is
                    counted in no total from here on. There is no undo — enter it again if it was
                    voided by mistake.
                  </Verdict>
                )}

                {rows.length === 0 ? (
                  <Empty title="Nothing recorded for this day">
                    Chai, an autorickshaw, the electricity bill — anything that left the shop as
                    money goes here. Enter it in the box above and it appears on this list.
                  </Empty>
                ) : (
                  <div className="exp-rows">
                    {rows.map((r) => (
                      <div className={`exp-row${r.void ? ' voided' : ''}`} key={r.expense_id}>
                        <div className="what">
                          <span className="cat">{r.category_label}</span>
                          {r.note && <span className="note">{r.note}</span>}
                          <span className="meta">
                            {exp.clock(r.at)} · paid in {r.paid_with === 'bank' ? 'bank or UPI' : 'cash'}
                          </span>
                          {r.void && (
                            <span className="voidline">
                              Voided {exp.clock(r.voided_at)} — {r.void_reason}. Counted in nothing,
                              and still on record.
                            </span>
                          )}
                        </div>
                        <div className="amt tnum">
                          {r.amount_paise === null
                            ? <span className="muted">amount unreadable</span>
                            : r.void ? <s>{exp.money(r.amount_paise)}</s> : exp.money(r.amount_paise)}
                        </div>
                        <div className="act">
                          {!r.void && (
                            <button
                              className="btn sm ghost"
                              onClick={() => {
                                setVoidFor(voidFor === r.expense_id ? null : r.expense_id);
                                setVoidReason('');
                                setVoidErr(null);
                                setVoided(null);
                              }}
                            >
                              VOID
                            </button>
                          )}
                        </div>

                        {voidFor === r.expense_id && (
                          /* THE REASON BOX IS THE CONFIRMATION. Voiding cannot
                             be undone, so it does not happen on one press: the
                             act asks what it is for, and the button stays shut
                             until there is an answer. A word typed on purpose
                             is a firmer gate than an OK on a dialog, and it is
                             the word that explains the line three months on. */
                          <div className={voidErr ? 'exp-void bad' : 'exp-void'}>
                            <label htmlFor={`why-${r.expense_id}`}>
                              Why is it being voided
                            </label>
                            <div className="exp-void-in">
                              <input
                                id={`why-${r.expense_id}`}
                                className={voidErr ? 'inp bad' : 'inp'}
                                type="text"
                                placeholder="typed twice"
                                maxLength={200}
                                value={voidReason}
                                aria-invalid={!!voidErr}
                                onChange={(e) => { setVoidReason(e.target.value); setVoidErr(null); }}
                              />
                              <Button
                                variant="danger"
                                size="sm"
                                loading={voiding}
                                disabled={!voidReason.trim()}
                                title={voidReason.trim()
                                  ? 'Void this entry — it cannot be undone'
                                  : 'Say why first. A void with no reason is indistinguishable from a deletion.'}
                                onClick={() => void doVoid(
                                  r.expense_id,
                                  `${r.category_label}${r.amount_paise === null ? '' : ` · ${exp.money(r.amount_paise)}`}`,
                                )}
                              >
                                {voiding ? 'VOIDING…' : 'VOID IT'}
                              </Button>
                              <button className="btn sm ghost" disabled={voiding} onClick={() => setVoidFor(null)}>
                                CANCEL
                              </button>
                            </div>
                            <span className="sub">
                              {voidReason.trim()
                                ? 'The entry stays on the list with its amount and its note, and stops counting. There is no delete and no edit.'
                                : 'Say why in a few words. The entry then stays on the list with its amount and its note, and stops counting — there is no delete and no edit.'}
                            </span>
                            {/* The server's own name for what it refused, under
                                the box that caused it. */}
                            {voidErr && <span className="err">{said(voidErr)}</span>}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {book.voided_count > 0 && (
                  <p className="hint">
                    {book.voided_count === 1 ? 'One entry is' : `${book.voided_count} entries are`}{' '}
                    voided, worth {exp.money(book.voided_paise)}. They are listed above and counted
                    in no total.
                  </p>
                )}
                {book.unreadable_rows > 0 && (
                  <p className="hint">
                    {book.unreadable_rows === 1 ? 'One row' : `${book.unreadable_rows} rows`} could
                    not be added up because the stored amount is not whole paise. Left out of the
                    totals rather than read as zero.
                  </p>
                )}
              </>
            )}
          </Card>
        </div>

        {/* ------------------------------------------------- the drawer -- */}
        <div className="stack">
          <Card
            title="The cash drawer"
            // Only once the answer is in. While the request is in flight the
            // page knows nothing about the drawer, and a pill saying the
            // opening was never counted is a claim, not a placeholder.
            aside={cash && !cash.opening.counted
              ? <Pill tone="off">OPENING NOT COUNTED</Pill>
              : undefined}
          >
            {drawerErr ? (
              <Refusal
                reason="The cash position could not be worked out"
                detail={drawerErr.reason}
                hint={drawerErr.detail}
                action={<button className="btn sm" onClick={() => void load(day)}>TRY AGAIN</button>}
              />
            ) : loading || !cash ? (
              <DrawerSkeleton />
            ) : (
              <>
                <div className="exp-ledger">
                  <div className="lg-row">
                    <span className="sign" aria-hidden="true" />
                    <span className="lbl">
                      Opening count
                      <em>what was in the drawer when the shutter went up</em>
                    </span>
                    <span className="val tnum">
                      {cash.opening.counted ? exp.money(cash.opening.paise) : <span className="muted">not counted</span>}
                    </span>
                  </div>

                  {(!cash.opening.counted || recount === 'opening') ? (
                    <CountForm
                      id="exp-opening"
                      label={cash.opening.counted ? 'Count it again' : 'Count the opening cash'}
                      placeholder="2000.00"
                      value={openingIn}
                      onValue={(v) => { setOpeningIn(v); setCountErr(null); }}
                      busy={counting === 'opening'}
                      err={countErr?.kind === 'opening' ? countErr.t : null}
                      onSave={() => void count('opening')}
                      onCancel={cash.opening.counted ? () => setRecount(null) : undefined}
                      sub="Zero is a valid count and means the drawer was empty. That is a different
                           statement from not having counted."
                    />
                  ) : (
                    <p className="lg-since">
                      {counted === 'opening'
                        ? <><b>Counted.</b> The opening float is on record at {exp.clock(cash.opening.counted_at)}, and the expected closing below is worked out from it. </>
                        : <>Counted at {exp.clock(cash.opening.counted_at)}. </>}
                      <button className="btn sm ghost" onClick={() => setRecount('opening')}>
                        COUNT IT AGAIN
                      </button>
                    </p>
                  )}

                  <div className="lg-row">
                    <span className="sign plus" aria-hidden="true">+</span>
                    <span className="lbl">
                      Bills the gateway never confirmed
                      <em>{cash.cash_sales.bills} {cash.cash_sales.bills === 1 ? 'bill' : 'bills'} — mostly cash, and not only cash</em>
                    </span>
                    <span className="val tnum">{exp.money(cash.cash_sales.paise)}</span>
                  </div>

                  <div className="lg-row">
                    <span className="sign minus" aria-hidden="true">−</span>
                    <span className="lbl">
                      Paid out in cash
                      <em>{cash.cash_expenses.count} {cash.cash_expenses.count === 1 ? 'entry' : 'entries'} from the list beside this</em>
                    </span>
                    <span className="val tnum">{exp.money(cash.cash_expenses.paise)}</span>
                  </div>

                  <div className="lg-row total">
                    <span className="sign" aria-hidden="true" />
                    <span className="lbl">What should be in the drawer</span>
                    <span className="val tnum">
                      {cash.expected_closing_paise === null
                        ? <span className="muted">—</span>
                        : exp.money(cash.expected_closing_paise)}
                    </span>
                  </div>
                </div>

                <p className="hint">{cash.cash_sales_note}</p>

                <div className="exp-sub">
                  <KV k="Settled by the gateway">
                    {exp.money(cash.gateway_sales.paise)}{' '}
                    <span className="muted">across {cash.gateway_sales.bills} {cash.gateway_sales.bills === 1 ? 'bill' : 'bills'}</span>{' '}
                    {cash.gateway_sales.bills > 0 && <Pill tone="ok">IN THE BANK</Pill>}
                  </KV>
                  <KV k="Paid from the bank">
                    {exp.money(cash.bank_expenses.paise)}{' '}
                    <span className="muted">not taken out of the drawer</span>
                  </KV>
                  {cash.undated_bills > 0 && (
                    <KV k="Bills with no readable time">
                      {cash.undated_bills} <span className="muted">in no day&rsquo;s figures</span>
                    </KV>
                  )}
                </div>
              </>
            )}
          </Card>

          {cash && !drawerErr && !loading && (
            <Card
              title="What you counted"
              aside={cash.counted_closing.counted
                ? <Pill tone="off">COUNTED {exp.clock(cash.counted_closing.counted_at)}</Pill>
                : undefined}
            >
              {(!cash.counted_closing.counted || recount === 'closing') ? (
                <>
                  <CountForm
                    id="exp-closing"
                    label={cash.counted_closing.counted ? 'Count it again' : 'Count the drawer and enter what you found'}
                    placeholder="3450.00"
                    value={closingIn}
                    onValue={(v) => { setClosingIn(v); setCountErr(null); }}
                    busy={counting === 'closing'}
                    err={countErr?.kind === 'closing' ? countErr.t : null}
                    onSave={() => void count('closing')}
                    onCancel={cash.counted_closing.counted ? () => setRecount(null) : undefined}
                    sub="Recorded whether or not the opening was counted. What is in the drawer is a
                         fact on its own, and it cannot be recovered tomorrow."
                  />
                  <div className="field">
                    <label htmlFor="exp-closing-note">Anything worth noting <span className="muted">(optional)</span></label>
                    <input
                      id="exp-closing-note"
                      type="text"
                      placeholder="gave Ramesh ₹200 float for the evening"
                      maxLength={200}
                      value={closingNote}
                      onChange={(e) => setClosingNote(e.target.value)}
                    />
                  </div>
                </>
              ) : (
                <div className="exp-counted">
                  {counted === 'closing' && (
                    <span className="exp-saved">Recorded — this is what the drawer held.</span>
                  )}
                  <span className="big tnum">{exp.money(cash.counted_closing.paise)}</span>
                  <span className="muted">
                    counted at {exp.clock(cash.counted_closing.counted_at)}
                    {cash.counted_closing.note ? ` — ${cash.counted_closing.note}` : ''}
                  </span>
                  <button className="btn sm ghost" onClick={() => setRecount('closing')}>
                    COUNT IT AGAIN
                  </button>
                </div>
              )}

              {/* THE DIFFERENCE. Deliberately not green, amber or red: those
                  three say a payment settled or a packet was not recognised,
                  and a drawer that is fifty rupees light is neither. It is set
                  in ink, at the size of a fact. */}
              <div className="exp-diff">
                <span className="lbl">The difference</span>
                {cash.difference_paise === null ? (
                  <span className="none">not compared yet</span>
                ) : cash.difference_direction === 'exact' ? (
                  <span className="fig tnum">{exp.money(0)}<em>to the paisa</em></span>
                ) : (
                  <span className="fig tnum">
                    {exp.magnitude(cash.difference_paise)}
                    <em>{cash.difference_direction === 'over' ? 'more than expected' : 'less than expected'}</em>
                  </span>
                )}
                <p>{cash.difference_note}</p>
              </div>
            </Card>
          )}

          {cash && !drawerErr && !loading && (
            <Card title="Where these numbers come from" tight>
              <KV k="the spending">this shop&rsquo;s own day book</KV>
              <KV k="the sales split">the hash-chained audit log</KV>
              <KV k="chain">
                {cash.chain.ok
                  ? <Pill tone="ok">VERIFIED · {cash.chain.lines_verified} lines</Pill>
                  : <Pill tone="bad">BROKEN</Pill>}
              </KV>
              <KV k="window">midnight to midnight ({cash.day})</KV>
              <p className="hint">{cash.derived_from}</p>
              <p className="hint">
                Storefront delivery orders are not in these figures — they are on a separate chain.
                Entries can be voided with a reason and never edited or deleted.
              </p>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ pieces -- */

function DayBar({ day, isToday, onDay }: {
  day: string; isToday: boolean; onDay: (d: string) => void;
}) {
  return (
    <div className="exp-daybar">
      <div className="exp-daypick">
        <button className="btn sm" onClick={() => onDay(exp.shiftDay(day, -1))}>
          ‹ PREVIOUS
        </button>
        <input
          type="date"
          value={day}
          // A day in the future is refused by the server — rent cannot be
          // entered before it is paid — so the picker will not offer one.
          max={exp.todayLabel()}
          aria-label="Which day"
          onChange={(e) => { if (e.target.value) onDay(e.target.value); }}
        />
        {/* Disabled on today, and it says why: a day that has not come cannot
            have had money paid out of it, and the server refuses one. */}
        <button
          className="btn sm"
          disabled={isToday}
          title={isToday
            ? 'Today is the last day there is — nothing can be entered against a day that has not come'
            : 'The day after this one'}
          onClick={() => onDay(exp.shiftDay(day, 1))}
        >
          NEXT ›
        </button>
      </div>
      <span className="exp-dayname">
        {exp.dayName(day)}{isToday && <> · <b>today</b></>}
      </span>
      {!isToday && (
        <button className="btn sm ghost" onClick={() => onDay(exp.todayLabel())}>
          BACK TO TODAY
        </button>
      )}
    </div>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="exp-tile">
      <span className="lbl">{label}</span>
      <span className="val tnum">{value}</span>
      <span className="sub">{sub}</span>
    </div>
  );
}

/** Reads back what is about to be recorded, so a wrong field shows up before
    the entry is written rather than after — there is no edit afterwards. */
function EntryPreview({ amount, ok, label, paidWith, day, isToday }: {
  amount: string; ok: boolean; label: string; paidWith: exp.PaidWith;
  day: string; isToday: boolean;
}) {
  const typed = amount.trim();
  if (!typed) return <span className="exp-preview muted">Type how many rupees went out.</span>;
  if (!ok) {
    return (
      <span className="exp-preview bad">
        ₹{typed} is not an amount. Rupees and paise, two decimal places at most.
      </span>
    );
  }
  return (
    <span className="exp-preview">
      Records <b>₹{typed}</b>
      {label ? <> as <b>{label}</b></> : ''}, paid {paidWith === 'bank' ? 'from the bank' : 'in cash'}
      {isToday ? '' : <>, against <b>{exp.dayName(day)}</b></>}.
    </span>
  );
}

/**
 * One counted figure. The input is text for the same reason every other amount
 * on this screen is: the string goes to the server untouched.
 *
 * A MALFORMED COUNT IS REFUSED BY NAME, HERE. This form used to gate the button
 * on the shape check and say nothing at all — a shopkeeper who typed "2,000/-"
 * got a dead button and no account of why, which is the same experience as a
 * broken screen. The shape check now names the state the server would refuse it
 * under, and a refusal that comes back from the server is shown verbatim in the
 * same place.
 */
function CountForm({ id, label, placeholder, value, onValue, busy, err, onSave, onCancel, sub }: {
  id: string; label: string; placeholder: string; value: string;
  onValue: (v: string) => void; busy: boolean; err: Trouble | null; onSave: () => void;
  onCancel?: () => void; sub: string;
}) {
  const typed = value.trim();
  const ok = RUPEES_RE.test(typed);
  const bad = (typed !== '' && !ok) || !!err;
  return (
    <div className={bad ? 'exp-count bad' : 'exp-count'}>
      <label htmlFor={id}>{label}</label>
      <div className="exp-count-in">
        <input
          id={id}
          className={bad ? 'inp bad' : 'inp'}
          type="text"
          inputMode="decimal"
          autoComplete="off"
          placeholder={placeholder}
          value={value}
          aria-invalid={bad}
          onChange={(e) => onValue(e.target.value)}
        />
        <Button
          variant="primary"
          loading={busy}
          disabled={!ok}
          title={ok ? 'Write this count to the day book'
            : typed === '' ? 'Type what you counted first'
              : 'That is not a rupee amount yet'}
          onClick={onSave}
        >
          {busy ? 'SAVING…' : 'SAVE THE COUNT'}
        </Button>
        {onCancel && (
          <button className="btn sm ghost" disabled={busy} onClick={onCancel}>CANCEL</button>
        )}
      </div>
      <span className="sub">{sub}</span>
      {err ? <span className="err">{said(err)}</span>
        : typed !== '' && !ok ? <span className="err">{NOT_MONEY}</span>
          : busy ? <span className="sub">Writing the count…</span>
            : null}
    </div>
  );
}

/* ------------------------------------------------------------- waiting --
   THE SHAPE OF WHAT IS COMING, drawn while it comes.

   These panels used to wait behind two or three grey bars of arbitrary height,
   which said something was loading and nothing about what — and then the tiles,
   the category bars and the ledger landed at a different height and moved the
   page. Each block below is the same grid and the same row count as the thing
   that replaces it, and every money block sits at the right edge its figure
   will sit at.
*/

/** A name on the left, a figure hard right, repeated. */
function AmountRows({ rows, widths }: { rows: number; widths: string[] }) {
  return (
    <div className="exp-skel-rows" aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <div className="exp-skel-row" key={i}>
          <Skeleton w={widths[i % widths.length]} h={11} radius={999} />
          <Skeleton w={68} h={11} radius={999} />
        </div>
      ))}
    </div>
  );
}

/** The entry form: the chips, the two boxes beside each other, the note, the button. */
function FormSkeleton() {
  return (
    <div className="exp-skel" role="status" aria-live="polite" aria-label="Reading the category list">
      <div aria-hidden="true">
        <Skeleton w="34%" h={10} radius={999} />
        <div className="exp-skel-chips">
          {[92, 76, 110, 84, 68, 96].map((w, i) => <Skeleton w={w} h={30} radius={999} key={i} />)}
        </div>
      </div>
      <div className="exp-two" aria-hidden="true">
        <div className="exp-skel-field"><Skeleton w="52%" h={10} radius={999} /><Skeleton h={38} /></div>
        <div className="exp-skel-field"><Skeleton w="38%" h={10} radius={999} /><Skeleton h={38} /></div>
      </div>
      <div className="exp-skel-field" aria-hidden="true">
        <Skeleton w="44%" h={10} radius={999} /><Skeleton h={38} />
      </div>
      <Skeleton w="46%" h={40} radius={999} />
    </div>
  );
}

/** The day: three tiles, the category bars, then the entries. */
function DaySkeleton() {
  return (
    <div className="exp-skel" role="status" aria-live="polite" aria-label="Reading the day book">
      <div className="exp-tiles" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <div className="exp-tile exp-skel-tile" key={i}>
            <Skeleton w="64%" h={9} radius={999} />
            <Skeleton w="82%" h={24} />
            <Skeleton w="90%" h={9} radius={999} />
          </div>
        ))}
      </div>
      <AmountRows rows={3} widths={['46%', '62%', '38%']} />
      <AmountRows rows={3} widths={['70%', '54%', '64%']} />
    </div>
  );
}

/** The drawer: the four ledger lines, then the two sub-figures. */
function DrawerSkeleton() {
  return (
    <div className="exp-skel" role="status" aria-live="polite" aria-label="Working out the cash position">
      <div className="exp-ledger exp-skel-ledger" aria-hidden="true">
        {[0, 1, 2, 3].map((i) => (
          <div className="lg-row" key={i}>
            <span className="sign" />
            <span className="lbl">
              <Skeleton w={i === 3 ? '58%' : '76%'} h={11} radius={999} />
              {i !== 3 && <Skeleton w="46%" h={9} radius={999} />}
            </span>
            <span className="val"><Skeleton w={78} h={16} radius={999} /></span>
          </div>
        ))}
      </div>
      <AmountRows rows={2} widths={['58%', '48%']} />
    </div>
  );
}
