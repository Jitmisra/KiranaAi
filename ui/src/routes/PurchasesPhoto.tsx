import { useCallback, useEffect, useMemo, useState } from 'react';
import * as pr from '../lib/parchiapi';
import type * as pu from '../lib/purchapi';
import { rupees } from '../lib/money';
import { ParchiCapture } from '../components/ParchiCapture';
import { Card, Pill, Verdict, Refusal, Button, Working } from '../components/ui';
import '../styles/parchi.css';

/**
 * NEW FROM PHOTO — the wholesaler's bill, photographed, and the margin known.
 *
 * The photograph goes to a vision model; everything else happens here and on
 * this machine. What comes back is a table beside the photograph, one row per
 * printed line, each wearing one word:
 *
 *   PROPOSED   the printed name is exactly a product this shop sells
 *   CONFIRM?   it is probably one — an abbreviation, a brand left off — and a
 *              person says which
 *   NO MATCH   nothing this shop sells has every word of it; the row is left
 *              out, and the Products screen is a link
 *   REFUSED    qty × rate is not the printed amount — and the WHOLE bill is
 *              refused, by that line's name, until the paper is right
 *
 * THE BROWSER DECIDES NOTHING ABOUT MONEY. The gate arrived decided; the
 * figures arrived as integer paise the server read digit by digit; ACCEPT
 * sends which lines and which products, and the server books them through
 * the same writer the typed form uses. `readyToAccept` can only add reasons
 * to wait, never override a refusal.
 *
 * NO RAZORPAY PRODUCT IS USED ON THIS SCREEN, and it says so: this is the cost
 * side of the counter. Nothing is paid, minted or settled here.
 */

type Trouble = { reason: string; detail?: string };
const asTrouble = (r: { reason: string; detail?: string }): Trouble =>
  r.detail === undefined ? { reason: r.reason } : { reason: r.reason, detail: r.detail };

function signed(p: number): string {
  return p < 0 ? `− ${rupees(-p)}` : rupees(p);
}

const NEW = '__new__';

const STATUS_WORD: Record<pr.LineStatus, { label: string; tone: 'code' | 'amb' | 'off' | 'bad' }> = {
  proposed: { label: 'PROPOSED', tone: 'code' },
  confirm: { label: 'CONFIRM?', tone: 'amb' },
  no_match: { label: 'NO MATCH', tone: 'off' },
  arithmetic_fails: { label: 'REFUSED', tone: 'bad' },
  unreadable: { label: 'UNREADABLE', tone: 'bad' },
};

export function PhotoView({ suppliers, products, onBooked }: {
  suppliers: pu.Supplier[] | null;
  /** The catalogue, from the margin book, for a person choosing by hand. */
  products: pu.MarginRow[];
  /** A booking changed the margin; the parent re-reads it. */
  onBooked: () => void;
}) {
  const [st, setSt] = useState<pr.Status | null>(null);
  const [stErr, setStErr] = useState<Trouble | null>(null);

  const [picked, setPicked] = useState<{ blob: Blob; name: string } | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const [doc, setDoc] = useState<pr.Parchi | null>(null);
  const [readErr, setReadErr] = useState<Trouble | null>(null);

  const [choices, setChoices] = useState<Record<number, pr.LineChoice>>({});
  const [supPick, setSupPick] = useState<string>(NEW);
  const [supName, setSupName] = useState('');
  const [supPhone, setSupPhone] = useState('');
  const [date, setDate] = useState('');
  const [invoiceNo, setInvoiceNo] = useState('');

  const [booking, setBooking] = useState(false);
  const [booked, setBooked] = useState<pr.Booked | null>(null);
  const [bookErr, setBookErr] = useState<Trouble | null>(null);

  useEffect(() => {
    void pr.status().then((r) => {
      if (r.ok) { setSt(r); setStErr(null); } else { setSt(null); setStErr(asTrouble(r)); }
    });
  }, []);

  useEffect(() => {
    if (!picked) { setPreview(null); return; }
    const url = URL.createObjectURL(picked.blob);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [picked]);

  const reset = useCallback(() => {
    setPicked(null); setDoc(null); setReadErr(null); setChoices({});
    setBooked(null); setBookErr(null); setSupPick(NEW); setSupName(''); setSupPhone('');
    setDate(''); setInvoiceNo('');
  }, []);

  /** A photograph was chosen: it leaves now, and the screen says so. */
  const read = useCallback(async (blob: Blob, name: string) => {
    reset();
    setPicked({ blob, name });
    setReading(true);
    const r = await pr.parse(blob, name);
    setReading(false);
    if (!r.ok) { setReadErr(asTrouble(r)); return; }
    setDoc(r);
    setChoices(pr.defaultChoices(r.lines));
    setSupPick(r.supplier.on_file?.supplier_id ?? NEW);
    setSupName(r.supplier.name);
    setSupPhone(r.supplier.phone);
    setDate(r.date ?? '');
    setInvoiceNo(r.invoice_no ?? '');
  }, [reset]);

  const supplier = useMemo(() => ({
    id: supPick === NEW ? null : supPick,
    name: supName,
    phone: supPhone,
  }), [supPick, supName, supPhone]);

  const ready: pr.NotReady = doc
    ? pr.readyToAccept(doc, choices, supplier)
    : { ready: false, why: 'Photograph a bill first.' };

  const setChoice = useCallback((i: number, patch: Partial<pr.LineChoice>) => {
    setChoices((c) => ({ ...c, [i]: { ...(c[i] ?? { include: false, sku_id: null, confirmed: false }), ...patch } }));
  }, []);

  const accept = useCallback(async () => {
    if (!doc) return;
    setBooking(true);
    setBookErr(null);
    const over: { date?: string; invoice_no?: string } = {};
    if (date.trim()) over.date = date.trim();
    if (invoiceNo.trim()) over.invoice_no = invoiceNo.trim();
    const r = await pr.book(doc.parchi_id, pr.bookBody(doc, choices, supplier, over));
    setBooking(false);
    if (!r.ok) { setBookErr(asTrouble(r)); return; }
    setBooked(r);
    onBooked();
  }, [doc, choices, supplier, date, invoiceNo, onBooked]);

  const kept = doc ? doc.lines.filter((l) => choices[l.i]?.include).length : 0;

  return (
    <div className="pr">
      <Card
        title="New from a photograph"
        sub="Photograph the wholesaler's bill; the cost of every product on it becomes known."
        aside={
          st ? (
            <Pill tone={st.available ? 'code' : 'amb'}>
              {st.available ? `READS WITH ${st.model ?? 'a model'}` : 'NO MODEL KEY'}
            </Pill>
          ) : undefined
        }
      >
        {stErr && (
          <Refusal
            reason="The photograph reader could not be asked whether it is available"
            detail={stErr.reason}
            hint={stErr.detail}
          />
        )}

        {st && !st.available && (
          <Verdict tone="amber" title="No model key, so a photograph cannot be read">
            {st.detail} The RECORD A PURCHASE form beside this one works exactly as before,
            and nothing leaves this machine.
          </Verdict>
        )}

        {st?.available && !doc && !reading && !readErr && (
          <div className="pr-leaves" style={{ marginBottom: 16 }}>
            <div>
              <b>What leaves this machine</b>
              <ul>{st.what_leaves.map((w) => <li key={w}>{w}</li>)}</ul>
            </div>
            <div>
              <b>What stays</b>
              <ul>{st.what_stays.map((w) => <li key={w}>{w}</li>)}</ul>
              <span>
                Every product match is made here, against this shop&rsquo;s own catalogue.
                No Razorpay product is used on this screen: this is the cost side of the counter.
              </span>
            </div>
          </div>
        )}

        {st?.available && (
          <ParchiCapture onPicked={(b, n) => void read(b, n)} disabled={reading} />
        )}

        {reading && (
          <div className="pr-reading" style={{ marginTop: 16 }} role="status" aria-live="polite">
            <Working />
            <span>
              Reading the bill. The photograph has left this machine to {st?.model ?? 'the model'}
              &nbsp;&mdash; nothing else went with it.
            </span>
          </div>
        )}

        {readErr && (
          <div style={{ marginTop: 16 }}>
            <Refusal
              reason={readErr.reason}
              detail={readErr.detail}
              hint="Nothing was booked. Try a straighter, better-lit photograph, or type the invoice on RECORD A PURCHASE."
              action={<button className="btn sm" onClick={reset}>TRY ANOTHER PHOTOGRAPH</button>}
            />
          </div>
        )}
      </Card>

      {doc && (
        <div className="pr-read">
          <div className="pr-photo">
            {preview && <img src={preview} alt="the bill, as photographed" />}
            <div className="pr-photo-cap">
              <span>{doc.lines.length} line{doc.lines.length === 1 ? '' : 's'} read by {doc.model}</span>
              <span className="mono">sha256 {doc.image.sha256.slice(0, 12)}…</span>
              <span>{Math.round(doc.image.bytes / 1024)} KB left this machine</span>
            </div>
          </div>

          <Card
            title="What the bill says"
            aside={
              <Pill tone={doc.gate.ok ? 'code' : 'bad'} dot>
                {doc.gate.ok ? 'ADDS UP TO THE PAISA' : 'DOES NOT ADD UP'}
              </Pill>
            }
          >
            {/* THE GATE'S VERDICT, FIRST. A refused bill is refused whatever
                the rows below say, so it is said before the rows. */}
            {doc.gate.ok ? (
              <Verdict tone="info" title={`Adds up: ${doc.gate.lines_checked} lines, ${rupees(doc.gate.printed_total_paise ?? 0)} printed`}>
                Every line&rsquo;s qty × rate is its printed amount
                {doc.gate.subtotal_printed ? ', the lines add to the printed subtotal' : ''}
                {doc.gate.taxes.length ? ', and subtotal plus the printed taxes is the printed total' : ', and the lines add to the printed total'}
                &nbsp;&mdash; checked in whole paise on this machine, nothing rounded.
              </Verdict>
            ) : (
              <Verdict tone="red" title="Refused: this bill does not add up">
                {doc.gate.detail}
                <br />
                Nothing on it will be booked until the paper is right. One paisa off is enough.
              </Verdict>
            )}

            <div className="pr-head" style={{ marginTop: 16 }}>
              <div className="field">
                <label htmlFor="pr-sup">Bought from</label>
                <select
                  id="pr-sup"
                  value={supPick}
                  disabled={!!booked}
                  onChange={(e) => setSupPick(e.target.value)}
                >
                  <option value={NEW}>
                    {doc.supplier.name ? `Add "${doc.supplier.name}" as a new supplier` : 'Add a new supplier'}
                  </option>
                  {(suppliers ?? []).map((s) => (
                    <option key={s.supplier_id} value={s.supplier_id}>{s.name}</option>
                  ))}
                </select>
                {doc.supplier.on_file ? (
                  <span className="pr-onfile">
                    The bill says <b>{doc.supplier.name}</b>, who is already on the list.
                  </span>
                ) : (
                  <span className="pr-onfile">
                    The bill says <b>{doc.supplier.name || 'nobody'}</b>; nobody by that name is on the list.
                  </span>
                )}
                {supPick === NEW && (
                  <>
                    <input
                      aria-label="New supplier's name"
                      placeholder="Supplier's name"
                      value={supName}
                      disabled={!!booked}
                      style={{ marginTop: 6 }}
                      onChange={(e) => setSupName(e.target.value)}
                    />
                    <input
                      aria-label="New supplier's phone"
                      placeholder="Phone, from the bill's header"
                      inputMode="tel"
                      value={supPhone}
                      disabled={!!booked}
                      style={{ marginTop: 6 }}
                      onChange={(e) => setSupPhone(e.target.value)}
                    />
                  </>
                )}
              </div>
              <div className="field">
                <label htmlFor="pr-date">Day it arrived</label>
                <input
                  id="pr-date"
                  type="date"
                  value={date}
                  disabled={!!booked}
                  onChange={(e) => setDate(e.target.value)}
                />
                <span className="pr-onfile">
                  {doc.date
                    ? `Read off the bill as ${doc.date_printed}.`
                    : doc.date_printed
                      ? `The bill prints "${doc.date_printed}", which this counter will not guess at; today is used unless you set it.`
                      : 'No date was read; today is used unless you set one.'}
                </span>
              </div>
              <div className="field">
                <label htmlFor="pr-inv">Invoice number</label>
                <input
                  id="pr-inv"
                  type="text"
                  value={invoiceNo}
                  disabled={!!booked}
                  maxLength={40}
                  onChange={(e) => setInvoiceNo(e.target.value)}
                />
                <span className="pr-onfile">
                  Entering the same invoice twice is refused by name.
                </span>
              </div>
            </div>

            <div className="scroll-x">
              <table className="pr-table">
                <thead>
                  <tr>
                    <th className="pr-tick" aria-label="Book this line" />
                    <th>#</th>
                    <th>Printed line</th>
                    <th className="num">Qty</th>
                    <th className="num">Rate</th>
                    <th className="num">Amount</th>
                    <th>Product in this shop</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {doc.lines.map((l) => {
                    const c = choices[l.i] ?? { include: false, sku_id: null, confirmed: false };
                    const bookable = l.arithmetic === 'ok';
                    const word = STATUS_WORD[l.status];
                    const cls = [
                      l.status === 'arithmetic_fails' || l.status === 'unreadable' ? 'pr-fails' : '',
                      l.status === 'confirm' && !c.confirmed ? 'pr-confirm' : '',
                      l.status === 'no_match' ? 'pr-none' : '',
                      bookable && !c.include ? 'pr-out' : '',
                    ].filter(Boolean).join(' ') || undefined;
                    return (
                      <tr key={l.i} className={cls} data-status={l.status}>
                        <td className="pr-tick">
                          <input
                            type="checkbox"
                            aria-label={`Book line ${l.i + 1}`}
                            checked={c.include}
                            disabled={!bookable || !!booked}
                            onChange={(e) => setChoice(l.i, { include: e.target.checked })}
                          />
                        </td>
                        <td className="num">{l.i + 1}</td>
                        <td>
                          <span className="pr-printed">{l.name}</span>
                          {l.arithmetic !== 'ok' && (
                            <span className="pr-lerr">
                              <span className="mono">{l.arithmetic === 'fails' ? 'arithmetic_fails' : 'figure_unreadable'}</span>
                              {' — '}{l.arithmetic_detail}
                            </span>
                          )}
                        </td>
                        <td className="num">{l.qty ?? '?'}</td>
                        <td className="num">{l.rate_paise === null ? String(l.rate ?? '?') : rupees(l.rate_paise)}</td>
                        <td className="num">
                          {l.amount_paise === null ? String(l.amount ?? '?') : rupees(l.amount_paise)}
                          {l.arithmetic === 'fails' && l.computed_paise !== null && (
                            <span className="pr-sub">should be {rupees(l.computed_paise)}</span>
                          )}
                        </td>
                        <td>
                          <div className="pr-match">
                            {l.status === 'proposed' && (
                              <>
                                <span className="pr-nm">{l.match.sku_name}</span>
                                <span className="pr-why">{l.match.why}</span>
                              </>
                            )}
                            {l.status === 'confirm' && (
                              <>
                                <select
                                  aria-label={`Product for line ${l.i + 1}`}
                                  value={c.sku_id ?? ''}
                                  disabled={!!booked}
                                  onChange={(e) => setChoice(l.i, { sku_id: e.target.value || null, confirmed: !!e.target.value })}
                                >
                                  {l.match.candidates.map((k) => (
                                    <option key={k.sku_id} value={k.sku_id}>{k.name}</option>
                                  ))}
                                  <option value="">— none of these —</option>
                                </select>
                                <span className="pr-why">{l.match.why}</span>
                                {!c.confirmed ? (
                                  <span className="btn-row">
                                    <button
                                      className="btn sm primary"
                                      disabled={!!booked || !c.sku_id}
                                      onClick={() => setChoice(l.i, { confirmed: true, include: true })}
                                    >
                                      CONFIRM
                                    </button>
                                  </span>
                                ) : (
                                  <span className="pr-why">confirmed by you</span>
                                )}
                              </>
                            )}
                            {l.status === 'no_match' && (
                              <>
                                <span className="pr-why">{l.match.why}</span>
                                <select
                                  aria-label={`Product for line ${l.i + 1}`}
                                  value={c.sku_id ?? ''}
                                  disabled={!!booked}
                                  onChange={(e) => {
                                    const v = e.target.value || null;
                                    setChoice(l.i, { sku_id: v, confirmed: !!v, include: !!v });
                                  }}
                                >
                                  <option value="">— leave this line out —</option>
                                  {products.map((p) => (
                                    <option key={p.sku_id} value={p.sku_id}>{p.name}</option>
                                  ))}
                                </select>
                                <a className="pr-why" href={doc.add_product_route}>
                                  or add it as a new product on the Products screen
                                </a>
                              </>
                            )}
                            {(l.status === 'arithmetic_fails' || l.status === 'unreadable') && (
                              <span className="pr-why">
                                {l.match.sku_name ? `would be ${l.match.sku_name}, ` : ''}not bookable
                              </span>
                            )}
                          </div>
                        </td>
                        <td><Pill tone={word.tone}>{word.label}</Pill></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="pr-scrollhint">Slide the table sideways for the rest of the columns.</p>

            <div className="pr-figs" aria-label="The figures the gate checked">
              <span>lines add to <b>{rupees(doc.gate.sum_of_lines_paise)}</b></span>
              {doc.gate.subtotal_printed && (
                <span>printed subtotal <b>{doc.gate.subtotal_paise === null ? '?' : rupees(doc.gate.subtotal_paise)}</b></span>
              )}
              {doc.gate.taxes.map((t, i) => (
                <span key={`${t.label}-${i}`}>{t.label} <b>{t.amount_paise === null ? String(t.amount ?? '?') : signed(t.amount_paise)}</b></span>
              ))}
              <span className="pr-total">printed total <b>{doc.gate.printed_total_paise === null ? String(doc.gate.printed_total ?? '?') : rupees(doc.gate.printed_total_paise)}</b></span>
            </div>

            <div className="pr-foot">
              <div className="pr-counts">
                <Pill tone="code">{doc.counts.proposed} proposed</Pill>
                <Pill tone="amb">{doc.counts.confirm} to confirm</Pill>
                <Pill tone="off">{doc.counts.no_match} no match</Pill>
                {(doc.counts.arithmetic_fails + doc.counts.unreadable) > 0 && (
                  <Pill tone="bad">{doc.counts.arithmetic_fails + doc.counts.unreadable} refused</Pill>
                )}
              </div>
              <div className="btn-row">
                {!booked ? (
                  <>
                    <Button
                      variant="primary"
                      loading={booking}
                      disabled={!ready.ready}
                      title={ready.ready ? undefined : ready.why}
                      onClick={() => void accept()}
                    >
                      {booking ? 'BOOKING…' : `ACCEPT ${kept} LINE${kept === 1 ? '' : 'S'}`}
                    </Button>
                    {!ready.ready && !booking && <span className="pr-gate">{ready.why}</span>}
                    {ready.ready && !booking && (
                      <span className="pr-gate">
                        Books {kept} of {doc.lines.length} lines through the purchase book. Nothing is paid.
                      </span>
                    )}
                  </>
                ) : (
                  <button className="btn" onClick={reset}>PHOTOGRAPH ANOTHER BILL</button>
                )}
              </div>
            </div>

            {bookErr && (
              <div style={{ marginTop: 12 }}>
                <Refusal reason={bookErr.reason} detail={bookErr.detail} />
              </div>
            )}

            {booked && (
              <>
                <div style={{ marginTop: 12 }}>
                  <Verdict tone="info" title={`Booked — ${rupees(booked.purchase.total_paise)}, ${booked.purchase.lines.length} line${booked.purchase.lines.length === 1 ? '' : 's'}`}>
                    {booked.purchase.units} units from {booked.purchase.supplier_name}
                    {booked.purchase.invoice_no ? ` on invoice ${booked.purchase.invoice_no}` : ''}, dated {booked.purchase.date},
                    as <span className="mono">{booked.purchase.purchase_id}</span>. That total is the counter&rsquo;s
                    own arithmetic over the lines. {booked.note}
                    {booked.booked.left_out.length > 0 && (
                      <> {booked.booked.left_out.length} line{booked.booked.left_out.length === 1 ? '' : 's'} left out.</>
                    )}
                  </Verdict>
                </div>
                <div className="pr-value">
                  <div>
                    <span className="lbl">cost known for</span>
                    <span className="val">
                      {booked.cost_known.before}<span className="pr-arrow">→</span>{booked.cost_known.after}
                      <span className="sub" style={{ display: 'inline', marginLeft: 6 }}>of {booked.cost_known.of} products</span>
                    </span>
                    <span className="sub">after one photograph</span>
                  </div>
                  <div>
                    <span className="lbl">today&rsquo;s margin</span>
                    <span className="val">
                      <span className={booked.today.before?.margin_is_partial ? 'pr-partial' : undefined}>
                        {booked.today.before ? (booked.today.before.margin_is_partial ? 'partial' : 'complete') : '—'}
                      </span>
                      <span className="pr-arrow">→</span>
                      <span className={booked.today.after?.margin_is_partial ? 'pr-partial' : undefined}>
                        {booked.today.after ? (booked.today.after.margin_is_partial ? 'partial' : 'complete') : '—'}
                      </span>
                    </span>
                    <span className="sub">
                      {booked.today.after
                        ? `${signed(booked.today.after.margin_paise)} on ${rupees(booked.today.after.covered_revenue_paise)} of ${rupees(booked.today.after.revenue_paise)} sold${booked.today.after.margin_pct_of_price ? ` · ${booked.today.after.margin_pct_of_price}% of price` : ''}${booked.today.after.uncovered_skus.length ? ` · ${booked.today.after.uncovered_skus.length} product${booked.today.after.uncovered_skus.length === 1 ? '' : 's'} still without a cost` : ''}`
                        : 'the day’s margin could not be read'}
                    </span>
                  </div>
                  <div>
                    <span className="lbl">what left this machine</span>
                    <span className="val">1 photograph</span>
                    <span className="sub">{doc.left_the_machine.note}</span>
                  </div>
                </div>
              </>
            )}

            <p className="pr-note">
              <b>What the model was and was not given.</b> {doc.left_the_machine.note} The
              figures were read digit by digit into whole paise on this machine; a bill one
              paisa off is refused, by the line, and nothing on it is booked. The quantity
              is as printed — if the bill counts cases, correct the count on RECORD A PURCHASE
              instead. Taxes printed on the bill are checked but not spread into unit costs:
              the cost recorded is the printed rate, and spreading a tax would be a guess about
              how this shop treats input credit.
            </p>
          </Card>
        </div>
      )}
    </div>
  );
}
