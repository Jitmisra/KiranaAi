import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import * as g from '../lib/gstapi';
import {
  Button, Card, Empty, Fig, Insight, KV, Pill, Refusal, Segmented, Skeleton, Stat,
  StatGrid, Table, TabPanel, Tabs, Verdict, type Column,
} from '../components/ui';
import '../styles/gst.css';

/**
 * GST — HSN and rate per product, the tax inside every bill, and a month laid
 * out the way GSTR-1's B2C table wants it.
 *
 * WHAT THIS SCREEN IS NOT, said where the reader is. It produces GST-READY
 * records and a summary. It files nothing with the government, generates no
 * e-invoice, and is not tax advice. The server says so on every response
 * (`is_filing: false`) and the sentence is printed under the heading, not
 * behind a tooltip.
 *
 * NOTHING HERE COMPUTES TAX. The page sends an HSN heading and a slab a
 * person chose; the server puts them against the lines it already holds —
 * the same bills the History screen shows, folded from the same chain — and
 * does the arithmetic in integer paise with a rounding rule it states. Every
 * figure below is one the server sent, formatted. Two processes that both
 * work out the tax inside a bill will one day disagree in front of an
 * accountant.
 *
 * THE SUGGESTER PROPOSES; A PERSON ACCEPTS. A proposal is drawn as plain text
 * with the word that matched in bold, beside an ACCEPT button. Where the
 * server proposes a heading and no rate, the button is not there: the rate is
 * left for the person, because a default would be a decision nobody made.
 *
 * THE COLOUR RULE. No tax figure is green — nothing here has settled. Amber
 * marks the one thing it marks on the till: a line the counter would not put
 * a number on, here a product with no rate set, whose tax was NOT guessed.
 */

type View = 'products' | 'month' | 'bill';
type Err = { reason: string; detail?: string };
type Filter = 'all' | 'unset' | 'set';

const HSN_RE = /^\d{4}(\d{2}){0,2}$/;

export default function Gst() {
  const [view, setView] = useState<View>('products');

  /* the products */
  const [prod, setProd] = useState<g.ProductsBody | null>(null);
  const [prodErr, setProdErr] = useState<Err | null>(null);
  const [prodLoading, setProdLoading] = useState(true);
  const [filter, setFilter] = useState<Filter>('all');
  const [editing, setEditing] = useState<string | null>(null);
  const [hsnIn, setHsnIn] = useState('');
  const [rateIn, setRateIn] = useState<string>('');
  const [busy, setBusy] = useState<string | null>(null);
  const [saveErr, setSaveErr] = useState<Err | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  /* the month */
  const [monthLabel, setMonthLabel] = useState<string>(() => g.thisMonth());
  const [basis, setBasis] = useState<g.Basis>('closed');
  const [mon, setMon] = useState<g.MonthBody | null>(null);
  const [monErr, setMonErr] = useState<Err | null>(null);
  const [monLoading, setMonLoading] = useState(true);

  /* one bill */
  const [billIn, setBillIn] = useState('');
  const [billId, setBillId] = useState('');
  const [bill, setBill] = useState<g.BillBody | null>(null);
  const [billErr, setBillErr] = useState<Err | null>(null);
  const [billLoading, setBillLoading] = useState(false);

  /**
   * What the screen is, in the server's own record of itself.
   *
   * `/gst/health` is the one endpoint that states the limits as DATA rather
   * than as prose this page wrote: `is_filing` false, the list of things it
   * does not do, the slabs it records, and the sentence about the slabs it
   * cannot. The screen used to leave it unread and paraphrase the limits in
   * three grey footnotes at the bottom of a right-hand rail. It is loaded once
   * and printed at the top, because a limit belongs where the reader is.
   */
  const [know, setKnow] = useState<g.HealthBody | null>(null);

  const loadProducts = useCallback(async () => {
    const r = await g.products();
    if (r.ok) { setProd(r); setProdErr(null); } else { setProd(null); setProdErr(r); }
    setProdLoading(false);
  }, []);

  const loadMonth = useCallback(async (label: string, b: g.Basis) => {
    const r = await g.month(label, b);
    if (r.ok) { setMon(r); setMonErr(null); } else { setMon(null); setMonErr(r); }
    setMonLoading(false);
  }, []);

  const loadBill = useCallback(async (id: string) => {
    const r = await g.bill(id);
    if (r.ok) { setBill(r); setBillErr(null); } else { setBill(null); setBillErr(r); }
    setBillLoading(false);
  }, []);

  useEffect(() => { void loadProducts(); }, [loadProducts]);
  useEffect(() => {
    void (async () => {
      const r = await g.health();
      if (r.ok) setKnow(r);
    })();
  }, []);
  useEffect(() => { setMonLoading(true); void loadMonth(monthLabel, basis); }, [monthLabel, basis, loadMonth]);
  useEffect(() => {
    if (!billId) return;
    setBillLoading(true);
    void loadBill(billId);
  }, [billId, loadBill]);

  /** A rate changed, so every figure derived from rates is stale at once. */
  const reloadDerived = useCallback(async () => {
    await loadProducts();
    void loadMonth(monthLabel, basis);
    if (billId) void loadBill(billId);
  }, [loadProducts, loadMonth, loadBill, monthLabel, basis, billId]);

  const startEdit = useCallback((row: g.ProductRow) => {
    setEditing(row.sku_id);
    setHsnIn(row.hsn ?? row.suggestion?.hsn ?? '');
    const r = row.rate ?? row.suggestion?.rate ?? null;
    setRateIn(r === null ? '' : String(r));
    setSaveErr(null);
    setSaved(null);
  }, []);

  const save = useCallback(async (row: g.ProductRow, hsn: string, rate: number, accepted: boolean) => {
    setBusy(row.sku_id);
    setSaveErr(null);
    setSaved(null);
    const r = await g.setRate(row.sku_id, hsn, rate, accepted);
    setBusy(null);
    if (r.ok) {
      setEditing(null);
      setSaved(r.detail);
      await reloadDerived();
    } else {
      setSaveErr(r);
    }
  }, [reloadDerived]);

  const clear = useCallback(async (row: g.ProductRow) => {
    setBusy(row.sku_id);
    setSaveErr(null);
    setSaved(null);
    const r = await g.clearRate(row.sku_id);
    setBusy(null);
    if (r.ok) {
      if (editing === row.sku_id) setEditing(null);
      setSaved(r.detail);
      await reloadDerived();
    } else {
      setSaveErr(r);
    }
  }, [editing, reloadDerived]);

  /** From the month or the bill, straight to the row that needs a rate. */
  const openProduct = useCallback((sku: string) => {
    setView('products');
    setFilter('all');
    const row = prod?.items.find((p) => p.sku_id === sku);
    if (row) startEdit(row);
  }, [prod, startEdit]);

  const openBill = useCallback((sid: string) => {
    setView('bill');
    setBillIn(sid);
    setBillId(sid);
  }, []);

  const rows = useMemo(() => {
    const all = prod?.items ?? [];
    if (filter === 'unset') return all.filter((r) => !r.set);
    if (filter === 'set') return all.filter((r) => r.set);
    return all;
  }, [prod, filter]);

  const editRow = editing ? prod?.items.find((p) => p.sku_id === editing) ?? null : null;
  const slabs = prod?.slabs ?? mon?.rows.map((r) => r.rate) ?? [];

  return (
    <div className="gst-page">
      <div className="page-head">
        <h1>GST</h1>
        <p>
          The HSN heading and rate for each product, the tax inside every bill this counter closed,
          and a month laid out the way GSTR-1&rsquo;s B2C table wants it.
        </p>
        <p className="gst-not">
          <b>This screen files nothing.</b> It produces GST-ready records and a summary. It does
          not send anything to the government, does not make an e-invoice, and is not tax advice.
          Take the month&rsquo;s file to whoever does the return.
        </p>
      </div>

      {/* THE THREE LIMITS, AT THE TOP, ON EVERY TAB. Each was previously a grey
          footnote at the bottom of a right-hand rail that only the month view
          drew — so the reader met the figures long before the sentence saying
          what the figures leave out. The words are the server's; the only thing
          this markup adds is where they sit. */}
      <Knows slabs={know?.slabs ?? slabs} limit={know?.slab_limit ?? mon?.slab_limit ?? null}
             storefront={mon?.storefront_note ?? null} />

      <Tabs<View>
        value={view}
        onChange={setView}
        label="GST views"
        tabs={[
          { value: 'products', label: 'Products', count: prod ? prod.unset_count : undefined },
          { value: 'month', label: 'This month', count: mon && mon.exceptions.length > 0 ? mon.exceptions.length : undefined },
          { value: 'bill', label: 'One bill' },
        ]}
      />

      {/* ============================================================ products */}
      {view === 'products' && (
        <TabPanel className="stack">
          {editRow && (
            <Card
              title={`Set the rate for ${editRow.name}`}
              sub={editRow.price_paise !== null
                ? `shelf price ${g.money(editRow.price_paise)}, tax included — the split below the table is at that price`
                : 'this product has no price yet'}
              aside={<Pill tone="off">A PERSON DECIDES</Pill>}
            >
              <Editor
                row={editRow}
                slabs={slabs}
                hsn={hsnIn}
                rate={rateIn}
                onHsn={setHsnIn}
                onRate={setRateIn}
                busy={busy === editRow.sku_id}
                onSave={() => {
                  const rate = Number(rateIn);
                  const accepted = !!editRow.suggestion
                    && editRow.suggestion.hsn === hsnIn.trim()
                    && editRow.suggestion.rate === rate;
                  void save(editRow, hsnIn.trim(), rate, accepted);
                }}
                onCancel={() => { setEditing(null); setSaveErr(null); }}
              />
              {saveErr && (
                <div className="gst-inline-refusal">
                  <Refusal reason={saveErr.reason} detail={saveErr.detail} />
                </div>
              )}
            </Card>
          )}

          {/* THE FINDING, BEFORE THE TABLE. With no rate anywhere, this screen
              is its own empty state: the table below is four rows of the words
              "not set" and nothing on the page said what that costs. It costs
              the month. */}
          {prod && prod.count > 0 && (
            <Insight
              tag={prod.unset_count > 0 ? 'what is missing' : 'every product is rated'}
              foot={prod.schedule_note}
            >
              {prod.unset_count > 0 ? (
                <>
                  <Fig tone="amber">{prod.unset_count}</Fig> of{' '}
                  <Fig tone="ink">{prod.count}</Fig> products have no rate. Their money is left
                  out of every month this counter summarises — listed as an exception, never
                  taxed at a guess.
                  {prod.proposed_count > 0 && (
                    <> The suggester has a heading and a slab ready for{' '}
                      <Fig>{prod.proposed_count}</Fig> of them; a person still has to accept it.</>
                  )}
                </>
              ) : (
                <>
                  All <Fig tone="ink">{prod.count}</Fig> products carry an HSN heading and a
                  rate, so every line the counter bills can be summarised.
                </>
              )}
            </Insight>
          )}

          <Card
            title="HSN and rate, per product"
            sub="the suggester proposes from the name; nothing is set until you accept or type it"
            aside={
              <div className="gst-complete">
                {prod && <Pill tone={prod.unset_count > 0 ? 'amb' : 'off'}>
                  {prod.set_count} OF {prod.count} SET
                </Pill>}
                <Segmented<Filter>
                  size="sm"
                  value={filter}
                  onChange={setFilter}
                  options={[
                    { value: 'all', label: 'ALL' },
                    { value: 'unset', label: 'NOT SET' },
                    { value: 'set', label: 'SET' },
                  ]}
                />
              </div>
            }
            flush
            foot={prod ? (
              <span>
                {prod.schedule_note}
              </span>
            ) : undefined}
          >
            {prodErr ? (
              <div style={{ padding: 'var(--s5)' }}>
                <Refusal
                  reason="The product list could not be read"
                  detail={prodErr.reason}
                  hint={prodErr.detail}
                  action={<Button size="sm" onClick={() => void loadProducts()}>TRY AGAIN</Button>}
                />
              </div>
            ) : (
              <ProductTable
                rows={rows}
                loading={prodLoading || !prod}
                editing={editing}
                busy={busy}
                filter={filter}
                onEdit={startEdit}
                onAccept={(row) => {
                  if (row.suggestion && row.suggestion.rate !== null) {
                    void save(row, row.suggestion.hsn, row.suggestion.rate, true);
                  }
                }}
                onClear={(row) => void clear(row)}
              />
            )}
          </Card>

          {saved && !editRow && <p className="gst-saved">{saved}</p>}
          {saveErr && !editRow && (
            <Refusal reason={saveErr.reason} detail={saveErr.detail} />
          )}

          {prod && prod.problems.length > 0 && (
            <Verdict tone="amber" title={`${prod.problems.length} row${prod.problems.length === 1 ? '' : 's'} of the rates file could not be read`}>
              {prod.problems.join(' · ')}. Those products read as not set. Nothing was coerced:
              a rate typed wrong by hand is refused rather than taxed.
            </Verdict>
          )}
          {prod && prod.set_but_not_in_catalogue.length > 0 && (
            <p className="hint">
              {prod.set_but_not_in_catalogue.length === 1 ? 'One rate is' : `${prod.set_but_not_in_catalogue.length} rates are`} set
              for products no longer in the catalogue
              ({prod.set_but_not_in_catalogue.map((r) => `${r.sku_id} · ${r.hsn} at ${g.pct(r.rate)}`).join(', ')}).
              They still apply to old bills and are left alone.
            </p>
          )}
        </TabPanel>
      )}

      {/* =============================================================== month */}
      {view === 'month' && (
        <TabPanel className="stack">
          <div className="gst-bar">
            <div className="gst-monthpick">
              <Button size="sm" onClick={() => setMonthLabel(g.shiftMonth(monthLabel, -1))}>‹ PREVIOUS</Button>
              <span className="gst-monthname">{g.monthName(monthLabel)}</span>
              <Button size="sm" disabled={monthLabel >= g.thisMonth()}
                      onClick={() => setMonthLabel(g.shiftMonth(monthLabel, 1))}>NEXT ›</Button>
            </div>
            <Segmented<g.Basis>
              size="sm"
              value={basis}
              onChange={setBasis}
              options={[
                { value: 'closed', label: 'EVERY BILL CLOSED', title: 'every basket the counter closed, paid or not' },
                { value: 'settled', label: 'ONLY SETTLED', title: 'only bills a signature-verified webhook turned PAID' },
              ]}
            />
            <span className="spacer" />
            <a className="btn sm" href={g.csvUrl(monthLabel, basis)} download>DOWNLOAD CSV</a>
          </div>

          {/* WHAT IS IN THE FILE, beside the button that fetches it. The
              abstention survives the export: the unrated money is a row of its
              own with the rate column reading `unrated` and the four tax
              columns left EMPTY, so an accountant opening it finds the gap
              rather than a total that quietly excludes it. Worth a sentence —
              the person who downloads this is not the person who built it. */}
          {mon && (
            <p className="gst-csvnote">
              One row per rate, and — when there is any — one row marked
              {' '}<span className="mono">unrated</span> carrying that money with the four tax
              columns left blank. Nothing is filed by downloading it.
              {mon.unrated.lines > 0 && (
                <> This month that row is <b>{g.money(mon.unrated.gross_paise)}</b>.</>
              )}
            </p>
          )}

          {mon && mon.months_with_bills.length > 0 && (
            <div className="gst-chips">
              <span className="lbl">months with bills</span>
              {mon.months_with_bills.map((m) => (
                <button key={m} type="button" aria-pressed={m === monthLabel} onClick={() => setMonthLabel(m)}>
                  {g.monthName(m)}
                </button>
              ))}
            </div>
          )}

          {monErr ? (
            <Refusal
              reason="The month could not be summarised"
              detail={monErr.reason}
              hint={monErr.detail}
              action={<Button size="sm" onClick={() => { setMonLoading(true); void loadMonth(monthLabel, basis); }}>TRY AGAIN</Button>}
            />
          ) : monLoading || !mon ? (
            <MonthSkeleton />
          ) : (
            <>
              {!mon.chain.ok && (
                <Verdict tone="red" title="The audit chain stops part way through">
                  {mon.chain.error}. Bills after that line are not in these figures, and the
                  month cannot be called complete until the chain verifies.
                </Verdict>
              )}

              <MonthHeadline m={mon} />

              {/* `display: contents` on a wrapper inside StatGrid put the class
                  on a box that lays nothing out, so the rule that was meant to
                  size these five figures could never reach the grid itself.
                  The class now sits OUTSIDE, on a real element, which is what
                  lets `.gst-figs` cap the type — five ₹-figures at the shared
                  42px display size broke mid-number ("₹2,068.5 / 0") in a
                  190px tile at every width this screen is used at. */}
              <div className="gst-figs">
                <StatGrid>
                  <Stat className="lead" label="billed, tax included" value={g.money(mon.gross_paise)}
                        sub={`${mon.bills} bill${mon.bills === 1 ? '' : 's'} · ${mon.basis === 'settled' ? 'settled by the gateway' : 'closed at the counter'}`} />
                  <Stat label="taxable value" value={g.money(mon.rated.taxable_paise)}
                        sub={`${mon.rated.lines} rated line${mon.rated.lines === 1 ? '' : 's'}`} />
                  <Stat label="CGST" value={g.money(mon.rated.cgst_paise)} sub="half the tax, rounded down" />
                  <Stat label="SGST" value={g.money(mon.rated.sgst_paise)} sub="the other half, and the odd paisa" />
                  <Stat label="not summarised" value={g.money(mon.unrated.gross_paise)}
                        tone={mon.unrated.lines > 0 ? 'amber' : undefined}
                        sub={mon.unrated.lines > 0
                          ? `${mon.unrated.lines} line${mon.unrated.lines === 1 ? '' : 's'} with no rate set`
                          : 'every line has a rate'} />
                </StatGrid>
              </div>

              <div className="gst-two">
                <div className="stack">
                  <Card
                    title="By rate"
                    sub={mon.shape}
                    aside={mon.complete
                      ? <Pill tone="off">EVERY LINE RATED</Pill>
                      : <Pill tone="amb">NOT COMPLETE</Pill>}
                    flush
                  >
                    <RateTable rows={mon.rows} withBills
                               empty={<Empty>No bills {mon.basis === 'settled' ? 'settled' : 'closed'} in {g.monthName(mon.month)}.<br />
                                 A bill appears here the moment the counter closes a basket.</Empty>} />
                  </Card>

                  {mon.unrated.lines > 0 && (
                    <Card title="Not summarised" sub="money on bills whose product has no rate — no tax was guessed">
                      <Verdict tone="amber" title={`${mon.unrated.lines} line${mon.unrated.lines === 1 ? '' : 's'} on ${mon.unrated.bills} bill${mon.unrated.bills === 1 ? '' : 's'}, ${g.money(mon.unrated.gross_paise)} in all`}>
                        Set a rate for each product below and the month rebuilds itself. Nothing about
                        the bills is rewritten.
                      </Verdict>
                      <div className="gst-unrated">
                        <Table<g.MonthBody['unrated']['by_sku'][number]>
                          label="Products with no rate"
                          rows={mon.unrated.by_sku}
                          rowKey={(r) => r.sku_id}
                          cols={[
                            { key: 'p', head: 'Product', cell: (r) => (
                              <div className="gst-prod">
                                <span className="name">{r.name ?? r.sku_id}</span>
                                <span className="sku">{r.sku_id}{!r.in_catalogue && ' · no longer in the catalogue'}</span>
                              </div>
                            ) },
                            { key: 'l', head: 'Lines', num: true, cell: (r) => r.lines },
                            { key: 'g', head: 'Billed', num: true, cell: (r) => g.money(r.gross_paise) },
                            { key: 'a', head: '', cell: (r) => (
                              <div className="gst-actions">
                                {r.in_catalogue && <Button size="sm" onClick={() => openProduct(r.sku_id)}>SET A RATE</Button>}
                              </div>
                            ) },
                          ]}
                        />
                      </div>
                    </Card>
                  )}

                  <Card
                    title="Exceptions"
                    sub="bills with a line no rate has been set for — listed, never taxed at a guess"
                    aside={<Pill tone={mon.exceptions.length > 0 ? 'amb' : 'off'}>{mon.exceptions.length}</Pill>}
                    flush
                    foot={mon.exceptions.length > 6 ? (
                      <span>
                        Every one of the {mon.exceptions.length} bills is listed — the table scrolls
                        rather than showing a first few. Setting one rate on the product above
                        clears every bill it appears on at once.
                      </span>
                    ) : undefined}
                  >
                    <Table<g.MonthException>
                      label="Bills with unrated lines"
                      rows={mon.exceptions}
                      rowKey={(e) => e.session_id}
                      maxHeight="52vh"
                      empty={<Empty>No exceptions. Every line on every bill this month has a rate.</Empty>}
                      cols={[
                        { key: 's', head: 'Bill', cell: (e) => <span className="mono">{e.session_id}</span> },
                        { key: 'w', head: 'When', cell: (e) => g.when(e.at) },
                        { key: 'u', head: 'Unrated lines', cell: (e) => (
                          <div className="gst-exc-lines">
                            {e.unrated_lines.map((ln, i) => (
                              <span key={`${ln.sku_id}-${i}`}>{ln.name ?? ln.sku_id} <span className="sku">{g.money(ln.price_paise)}</span></span>
                            ))}
                          </div>
                        ) },
                        { key: 'm', head: 'Unrated', num: true, cell: (e) => g.money(e.unrated_paise) },
                        { key: 't', head: 'Bill total', num: true, cell: (e) => g.money(e.total_paise) },
                        { key: 'a', head: '', cell: (e) => (
                          <div className="gst-actions">
                            <Button size="sm" variant="ghost" onClick={() => openBill(e.session_id)}>SPLIT IT</Button>
                          </div>
                        ) },
                      ]}
                    />
                  </Card>
                </div>

                <div className="stack">
                  <SourceCard
                    chain={mon.chain}
                    rounding={mon.rounding}
                    note={mon.note}
                    extra={
                      <>
                        <KV k="window">{g.monthName(mon.month)}, {mon.window.timezone}</KV>
                        <KV k="basis">{mon.basis === 'settled' ? 'bills the gateway confirmed' : 'every bill the counter closed'}</KV>
                        <KV k="closed this month">{mon.bills_closed_in_month}</KV>
                        <KV k="settled this month">{mon.bills_settled_in_month}{mon.bills_settled_in_month > 0 && <> <Pill tone="ok">PAID</Pill></>}</KV>
                        {mon.excluded_amber_lines > 0 && (
                          <KV k="amber lines">{mon.excluded_amber_lines} <span className="muted">not priced, so not taxed</span></KV>
                        )}
                        {mon.unreadable_lines > 0 && (
                          <KV k="unreadable prices">{mon.unreadable_lines} <span className="muted">not whole paise on the chain</span></KV>
                        )}
                        {mon.undated_bills > 0 && (
                          <KV k="bills with no readable time">{mon.undated_bills} <span className="muted">in no month&rsquo;s figures</span></KV>
                        )}
                      </>
                    }
                    hints={[mon.storefront_note, mon.slab_limit]}
                  />
                </div>
              </div>
            </>
          )}
        </TabPanel>
      )}

      {/* ================================================================ bill */}
      {view === 'bill' && (
        <TabPanel className="stack">
          <Card title="The tax inside one bill" sub="the same lines the History screen shows, with a rate put against each">
            <form
              className="gst-lookup"
              onSubmit={(e) => { e.preventDefault(); if (billIn.trim()) setBillId(billIn.trim()); }}
            >
              <input
                className="inp"
                type="text"
                value={billIn}
                placeholder="session id, from the History screen"
                aria-label="Session id"
                onChange={(e) => setBillIn(e.target.value)}
              />
              <Button type="submit" variant="primary" size="sm" disabled={!billIn.trim()} loading={billLoading}>LOOK IT UP</Button>
            </form>
          </Card>

          {!billId ? (
            <Card title="Nothing looked up yet">
              <Empty>
                Type a session id above, or press SPLIT IT on an exception in the month view.
                <br />
                Session ids are on the History screen, one per bill.
              </Empty>
            </Card>
          ) : billErr ? (
            <Refusal reason={billErr.reason} detail={billErr.detail} />
          ) : billLoading || !bill ? (
            <BillSkeleton />
          ) : (
            <>
              <Card
                title={<span className="mono">{bill.session_id}</span>}
                sub={`closed ${g.when(bill.at)}`}
                aside={
                  <div className="gst-complete">
                    {bill.settled && <Pill tone="ok" dot>PAID {g.when(bill.settled_at)}</Pill>}
                    {bill.complete ? <Pill tone="off">EVERY LINE RATED</Pill> : <Pill tone="amb">NOT COMPLETE</Pill>}
                  </div>
                }
              >
                <div className="gst-billhead">
                  <div><span className="k">bill total</span><span className="v tnum">{g.money(bill.total_paise)}</span></div>
                  <div><span className="k">taxable value</span><span className="v tnum">{g.money(bill.rated.taxable_paise)}</span></div>
                  <div><span className="k">CGST</span><span className="v tnum">{g.money(bill.rated.cgst_paise)}</span></div>
                  <div><span className="k">SGST</span><span className="v tnum">{g.money(bill.rated.sgst_paise)}</span></div>
                  <div><span className="k">not summarised</span><span className="v tnum">{g.money(bill.unrated_paise)}</span></div>
                </div>
                {!bill.total_agrees && (
                  <Verdict tone="amber" title="The lines do not add up to the bill's own total">
                    The lines come to {g.money(bill.lines_sum_paise)}; the chain recorded {g.money(bill.total_paise)} at
                    close. Both are shown; neither was adjusted.
                  </Verdict>
                )}
                <p className="hint">
                  This is not an invoice. It is the tax inside a bill the counter already closed, worked out
                  line by line from the chain.
                </p>
              </Card>

              <Card title="Lines" sub="taxable value and the two halves, per line, at the price actually charged" flush>
                <Table<g.BillLine>
                  label="Rated lines"
                  rows={bill.lines}
                  rowKey={(l) => l.item_id}
                  empty={<Empty>No line on this bill has a rate yet.</Empty>}
                  cols={[
                    { key: 'p', head: 'Product', cell: (l) => (
                      <div className="gst-prod">
                        <span className="name">{l.name ?? l.sku_id}</span>
                        <span className="sku">{l.item_id}</span>
                      </div>
                    ) },
                    { key: 'h', head: 'HSN', cell: (l) => <span className="gst-hsn">{l.hsn}</span> },
                    { key: 'r', head: 'Rate', num: true, cell: (l) => g.pct(l.rate) },
                    { key: 'g', head: 'Charged', num: true, cell: (l) => g.money(l.price_paise) },
                    { key: 'tv', head: 'Taxable', num: true, cell: (l) => g.money(l.taxable_paise) },
                    { key: 'c', head: 'CGST', num: true, cell: (l) => g.money(l.cgst_paise) },
                    { key: 's', head: 'SGST', num: true, cell: (l) => g.money(l.sgst_paise) },
                  ]}
                />
              </Card>

              {bill.by_rate.length > 0 && (
                <Card title="By rate" flush>
                  <RateTable rows={bill.by_rate} />
                </Card>
              )}

              {bill.unrated.length > 0 && (
                <Card title="Not summarised" sub="lines whose product has no rate set">
                  <Verdict tone="amber" title={`${bill.unrated.length} line${bill.unrated.length === 1 ? '' : 's'}, ${g.money(bill.unrated_paise)}, with no tax worked out`}>
                    Set a rate for the product and this bill rebuilds itself.
                  </Verdict>
                  <div className="gst-unrated">
                    <Table<g.UnratedLine>
                      label="Unrated lines"
                      rows={bill.unrated}
                      rowKey={(l) => l.item_id}
                      cols={[
                        { key: 'p', head: 'Product', cell: (l) => (
                          <div className="gst-prod">
                            <span className="name">{l.name ?? l.sku_id}</span>
                            <span className="sku">{l.item_id}</span>
                          </div>
                        ) },
                        { key: 'g', head: 'Charged', num: true, cell: (l) => g.money(l.price_paise) },
                        { key: 'a', head: '', cell: (l) => (
                          <div className="gst-actions">
                            {l.name !== null && <Button size="sm" onClick={() => openProduct(l.sku_id)}>SET A RATE</Button>}
                          </div>
                        ) },
                      ]}
                    />
                  </div>
                </Card>
              )}

              {(bill.excluded.length > 0 || bill.unreadable_lines > 0) && (
                <Card title="Not on this split" tight>
                  {bill.excluded.length > 0 && (
                    <div className="gst-excluded">
                      <p className="muted">
                        {bill.excluded.length === 1 ? 'One item' : `${bill.excluded.length} items`} the counter would not price,
                        so there is no money to split:
                      </p>
                      <ul>
                        {bill.excluded.map((e) => (
                          <li key={e.item_id}>{e.name ?? e.item_id} <span className="why">{e.reason}</span></li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {bill.unreadable_lines > 0 && (
                    <p className="hint">
                      {bill.unreadable_lines === 1 ? 'One line' : `${bill.unreadable_lines} lines`} on the chain
                      carries a price that is not whole paise. Left out rather than read as zero.
                    </p>
                  )}
                </Card>
              )}

              <SourceCard chain={bill.chain} rounding={bill.rounding} note={bill.note} />
            </>
          )}
        </TabPanel>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ pieces -- */

/**
 * The three limits, drawn once, at the top, on every tab.
 *
 * Not a tooltip and not a footnote. Two of these were previously grey
 * paragraphs at the bottom of a right-hand rail that only the month tab drew,
 * which put them below every figure they qualify and out of the products tab
 * altogether. The slabs are drawn as the chips they are — the reader is being
 * told the size of a set — and the two sentences are the server's, verbatim.
 */
function Knows({ slabs, limit, storefront }: {
  slabs: number[];
  limit: string | null;
  storefront: string | null;
}) {
  if (slabs.length === 0 && !limit && !storefront) return null;
  return (
    <div className="gst-knows">
      {slabs.length > 0 && (
        <div className="gst-know">
          <span className="k">the slabs it records</span>
          <span className="gst-slabs">
            {slabs.map((s) => <span className="gst-slab" key={s}>{s}%</span>)}
          </span>
        </div>
      )}
      {limit && (
        <div className="gst-know">
          <span className="k">the slab it cannot</span>
          <span className="v">{limit}</span>
        </div>
      )}
      {storefront && (
        <div className="gst-know">
          <span className="k">what is not counted</span>
          <span className="v">{storefront}</span>
        </div>
      )}
    </div>
  );
}

/**
 * The finding, in one sentence, above the five figures it is drawn from.
 *
 * A month whose money is mostly unrated shows five tiles of which one is amber
 * and four are small — and a reader takes four confident figures and one
 * qualifier away from that, which is exactly backwards. Every figure here is
 * the server's own integer, formatted; nothing is added, divided or
 * proportioned.
 */
function MonthHeadline({ m }: { m: g.MonthBody }) {
  const gap = m.unrated.lines > 0;
  const why: string[] = [];
  if (m.unrated.lines > 0) why.push('a line whose product has no rate');
  if (m.unreadable_lines > 0) why.push('a price that is not whole paise');
  if (!m.chain.ok) why.push('a chain that does not verify');
  return (
    <Insight
      tag={gap ? 'this month is not complete' : m.complete ? 'this month is complete' : 'this month'}
      foot={
        `${m.shape}. ${m.basis === 'settled'
          ? 'Only bills a signature-verified webhook turned PAID.'
          : 'Every bill the counter closed, paid or not.'}`
        + (m.complete
          ? ' Every line carries a rate.'
          : ` Not complete while any bill carries ${why.join(', ') || 'an exception'}.`)
      }
    >
      {gap ? (
        <>
          Of <Fig tone="ink">{g.money(m.gross_paise)}</Fig> billed on{' '}
          <Fig tone="ink">{m.bills}</Fig> bill{m.bills === 1 ? '' : 's'},{' '}
          <Fig tone="amber">{g.money(m.unrated.gross_paise)}</Fig> cannot be summarised —{' '}
          <Fig tone="amber">{m.unrated.lines}</Fig> line{m.unrated.lines === 1 ? '' : 's'} on{' '}
          <Fig tone="amber">{m.unrated.bills}</Fig> bill{m.unrated.bills === 1 ? '' : 's'} whose
          product has no rate. No tax was worked out for them, and none was guessed.
        </>
      ) : m.bills === 0 ? (
        <>
          No bill was {m.basis === 'settled' ? 'settled' : 'closed'} in{' '}
          <Fig tone="ink">{g.monthName(m.month)}</Fig>, so there is nothing to summarise. The
          month fills itself in as the counter closes baskets.
        </>
      ) : (
        <>
          <Fig tone="ink">{g.money(m.gross_paise)}</Fig> billed on{' '}
          <Fig tone="ink">{m.bills}</Fig> bill{m.bills === 1 ? '' : 's'}, all of it rated:{' '}
          <Fig>{g.money(m.rated.taxable_paise)}</Fig> taxable, with{' '}
          <Fig>{g.money(m.rated.cgst_paise)}</Fig> CGST and{' '}
          <Fig>{g.money(m.rated.sgst_paise)}</Fig> SGST inside it.
        </>
      )}
    </Insight>
  );
}

/**
 * The month, waiting, at the SHAPE of the month.
 *
 * Two grey rectangles told the reader something was coming and nothing about
 * what, and the page jumped a screen-height when it landed. This reserves the
 * sentence, the five figures — money bars right-aligned in their tiles, where
 * the figure will be — and the table under them.
 */
function MonthSkeleton() {
  return (
    <div className="gst-skels" role="status" aria-live="polite" aria-label="Summarising the month">
      <div className="mesh insight gst-skel-lede" aria-hidden="true">
        <Skeleton w="34%" h={11} radius={999} />
        <Skeleton w="92%" h={19} radius={999} />
        <Skeleton w="68%" h={19} radius={999} />
      </div>
      <div className="gst-figs" aria-hidden="true">
        <StatGrid>
          {['billed, tax included', 'taxable value', 'CGST', 'SGST', 'not summarised'].map((l) => (
            <div className="stat" key={l}>
              <span className="stat-l">{l}</span>
              <span className="gst-skel-fig"><Skeleton w={96} h={26} radius={8} /></span>
              <Skeleton w="70%" h={10} radius={999} />
            </div>
          ))}
        </StatGrid>
      </div>
      <div className="card" aria-hidden="true">
        <div className="card-body">
          <Skeleton w="26%" h={13} radius={999} />
          <div className="gst-skel-rows">
            {[0, 1, 2, 3].map((r) => (
              <div className="skel-row" key={r}>
                <span className="skel grow" />
                <span className="skel" style={{ width: 64 }} />
                <span className="skel" style={{ width: 64 }} />
                <span className="skel" style={{ width: 64 }} />
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/** One bill, waiting: the five head figures, then its lines. */
function BillSkeleton() {
  return (
    <div className="gst-skels" role="status" aria-live="polite" aria-label="Splitting the bill">
      <div className="card" aria-hidden="true">
        <div className="card-body">
          <Skeleton w="30%" h={13} radius={999} />
          <div className="gst-billhead gst-skel-head">
            {['bill total', 'taxable value', 'CGST', 'SGST', 'not summarised'].map((k) => (
              <div key={k}>
                <span className="k">{k}</span>
                <Skeleton w={88} h={22} radius={8} />
              </div>
            ))}
          </div>
        </div>
      </div>
      <div className="card" aria-hidden="true">
        <div className="card-body">
          <Skeleton w="18%" h={13} radius={999} />
          <div className="gst-skel-rows">
            {[0, 1, 2].map((r) => (
              <div className="skel-row" key={r}>
                <span className="skel grow" />
                <span className="skel" style={{ width: 52 }} />
                <span className="skel" style={{ width: 64 }} />
                <span className="skel" style={{ width: 64 }} />
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function ProductTable({ rows, loading, editing, busy, filter, onEdit, onAccept, onClear }: {
  rows: g.ProductRow[];
  loading: boolean;
  editing: string | null;
  busy: string | null;
  filter: Filter;
  onEdit: (row: g.ProductRow) => void;
  onAccept: (row: g.ProductRow) => void;
  onClear: (row: g.ProductRow) => void;
}) {
  const cols: Column<g.ProductRow>[] = [
    { key: 'p', head: 'Product', cell: (r) => (
      <div className="gst-prod">
        <span className="name">{r.name}</span>
        <span className="sku">{r.sku_id}</span>
        <span className="price">{r.price_paise === null ? 'no price' : `${g.money(r.price_paise)}, tax included`}</span>
      </div>
    ) },
    { key: 'h', head: 'HSN', cell: (r) => r.set
      ? <span className="gst-hsn">{r.hsn}</span>
      : <span className="gst-unset">not set</span> },
    { key: 'r', head: 'Rate', num: true, cell: (r) => r.set
      ? <Pill tone="off">{g.pct(r.rate)}</Pill>
      : <span className="gst-unset">not set</span> },
    { key: 't', head: 'Tax at shelf price', cell: (r) => {
      const s = r.at_marked_price;
      if (!s) return <span className="gst-unset">—</span>;
      if ('refused' in s) return <span className="gst-unset">{s.refused}</span>;
      return (
        <span className="gst-split tnum">
          CGST <b>{g.money(s.cgst_paise)}</b><span className="sep">·</span>SGST <b>{g.money(s.sgst_paise)}</b>
          <span className="sep">·</span>taxable <b>{g.money(s.taxable_paise)}</b>
        </span>
      );
    } },
    { key: 'sug', head: 'Proposal', cell: (r) => {
      if (r.set) {
        return (
          <span className="gst-propose">
            {r.source === 'accepted_suggestion' ? 'accepted from the suggester' : 'typed'}
            {r.set_at && <> · {g.when(r.set_at)}</>}
          </span>
        );
      }
      const s = r.suggestion;
      if (!s) return <span className="gst-unset">nothing in the name the table knows</span>;
      return (
        <span className="gst-propose">
          <b>{s.hsn}</b>{s.rate !== null ? <> at <b>{g.pct(s.rate)}</b></> : <> — <span className="norate">no rate proposed</span></>}
          {' '}because of &ldquo;{s.keyword}&rdquo; ({s.label})
        </span>
      );
    } },
    { key: 'a', head: '', cell: (r) => (
      <div className="gst-actions">
        {!r.set && r.suggestion && r.suggestion.rate !== null && (
          <Button size="sm" variant="primary" loading={busy === r.sku_id} onClick={() => onAccept(r)}>ACCEPT</Button>
        )}
        <Button size="sm" disabled={busy === r.sku_id} onClick={() => onEdit(r)}>{r.set ? 'CHANGE' : 'TYPE IT'}</Button>
        {r.set && <Button size="sm" variant="ghost" disabled={busy === r.sku_id} onClick={() => onClear(r)}>CLEAR</Button>}
      </div>
    ) },
  ];
  return (
    <Table<g.ProductRow>
      label="Products and their GST rates"
      cols={cols}
      rows={rows}
      rowKey={(r) => r.sku_id}
      loading={loading}
      loadingRows={5}
      isOpen={(r) => r.sku_id === editing}
      rowClass={(r) => (r.sku_id === editing ? 'gst-editing' : undefined)}
      maxHeight="none"
      empty={
        <Empty title={filter === 'all' ? 'Nothing priced yet' : filter === 'unset' ? 'Every product has a rate' : 'No rates set yet'}>
          {filter === 'all'
            ? 'Teach a product on the Products screen and give it a price; it appears here with a proposal.'
            : filter === 'unset'
              ? 'There is nothing left to set. The month view will say the same.'
              : 'Accept a proposal or type a rate and it moves here.'}
        </Empty>
      }
    />
  );
}

function Editor({ row, slabs, hsn, rate, onHsn, onRate, busy, onSave, onCancel }: {
  row: g.ProductRow;
  slabs: number[];
  hsn: string;
  rate: string;
  onHsn: (v: string) => void;
  onRate: (v: string) => void;
  busy: boolean;
  onSave: () => void;
  onCancel: () => void;
}) {
  const typed = hsn.trim();
  const hsnOk = HSN_RE.test(typed);
  const rateOk = rate !== '' && slabs.includes(Number(rate));
  const s = row.suggestion;

  /* THE REASON IS REPORTED WHERE THE PERSON TYPED, and only once they have
     typed. An empty field has not been refused — it is unfinished — so it
     carries the requirement as a note and not a red border. */
  const hsnErr = typed !== '' && !hsnOk
    ? `“${typed}” is not an HSN heading. It is 4, 6 or 8 digits, and a leading zero is part of it.`
    : undefined;

  /* WHY THE BUTTON IS OFF, in the button's own words rather than in nothing at
     all. A control that cannot be pressed and will not say why is a control
     the shopkeeper presses repeatedly. */
  const cannot = !hsnOk && !rateOk ? 'Type an HSN heading and pick a rate first.'
    : !hsnOk ? (typed === '' ? 'Type an HSN heading first.' : 'That HSN heading is not 4, 6 or 8 digits.')
      : !rateOk ? (slabs.length === 0
        ? 'The slab list has not loaded, so there is nothing to record against.'
        : 'Pick one of the slabs first.')
        : null;

  return (
    <div className="gst-editor">
      <div className={hsnErr ? 'field bad' : 'field'}>
        <label htmlFor="gst-hsn">HSN heading</label>
        {/* TEXT, not number: milk is 0401 and a number input drops the zero. */}
        <input
          id="gst-hsn"
          className="inp"
          type="text"
          inputMode="numeric"
          placeholder="3401"
          maxLength={8}
          value={hsn}
          aria-invalid={hsnErr ? true : undefined}
          aria-describedby={hsnErr ? 'gst-hsn-err' : undefined}
          onChange={(e) => onHsn(e.target.value)}
        />
        <span className="sub">4, 6 or 8 digits.</span>
        {hsnErr && <span className="err" id="gst-hsn-err">{hsnErr}</span>}
      </div>
      <div className="field">
        <label>Rate</label>
        {slabs.length > 0 ? (
          <Segmented<string>
            wide
            value={rate}
            onChange={onRate}
            options={slabs.map((s) => ({ value: String(s), label: `${s}%` }))}
          />
        ) : (
          <span className="sub">The slab list comes from the server and has not loaded.</span>
        )}
        <span className="sub">The slabs the server records. A product taxed outside them stays unrated.</span>
      </div>
      <div className="gst-editor-act">
        <Button
          variant="primary"
          disabled={!hsnOk || !rateOk}
          title={cannot ?? undefined}
          loading={busy}
          onClick={onSave}
        >
          SAVE
        </Button>
        <Button variant="ghost" onClick={onCancel}>CANCEL</Button>
        {cannot && <span className="gst-cannot">{cannot}</span>}
      </div>
      {s && !row.set && (
        <p className="gst-editor-from">
          Prefilled from the suggester: <b>{s.hsn}</b>
          {s.rate !== null ? <> at <b>{g.pct(s.rate)}</b></> : <>, with no rate proposed</>}
          {' '}— {s.why}
        </p>
      )}
    </div>
  );
}

function RateTable({ rows, withBills, empty }: { rows: g.RateRow[]; withBills?: boolean; empty?: ReactNode }) {
  const cols: Column<g.RateRow>[] = [
    { key: 'r', head: 'Rate', cell: (r) => <Pill tone="off">{g.pct(r.rate)}</Pill> },
    ...(withBills ? [{ key: 'b', head: 'Bills', num: true, cell: (r: g.RateRow) => r.bills ?? 0 } as Column<g.RateRow>] : []),
    { key: 'l', head: 'Lines', num: true, cell: (r) => r.lines },
    { key: 'g', head: 'Billed', num: true, cell: (r) => g.money(r.gross_paise) },
    { key: 'tv', head: 'Taxable value', num: true, cell: (r) => g.money(r.taxable_paise) },
    { key: 'c', head: 'CGST', num: true, cell: (r) => g.money(r.cgst_paise) },
    { key: 's', head: 'SGST', num: true, cell: (r) => g.money(r.sgst_paise) },
    { key: 't', head: 'Tax', num: true, cell: (r) => g.money(r.tax_paise) },
  ];
  return (
    <Table<g.RateRow>
      label="Taxable value, CGST and SGST by rate"
      cols={cols}
      rows={rows}
      rowKey={(r) => String(r.rate)}
      empty={empty}
    />
  );
}

/** Where every number came from, and the rounding rule in the server's words. */
function SourceCard({ chain, rounding, note, extra, hints }: {
  chain: g.Chain;
  rounding: g.Rounding;
  note: string;
  extra?: ReactNode;
  hints?: string[];
}) {
  return (
    <Card title="Where these numbers come from" tight className="gst-source">
      <KV k="the bills">the hash-chained audit log, through the same fold the History screen uses</KV>
      <KV k="chain">
        {!chain.exists
          ? <Pill tone="off">NO LOG YET</Pill>
          : chain.ok
            ? <Pill tone="ok">VERIFIED · {chain.lines_verified} lines</Pill>
            : <Pill tone="bad">BROKEN at line {chain.lines_verified + 1}</Pill>}
      </KV>
      {extra}
      <div className="gst-rules">
        <KV k="prices">{rounding.prices_are}</KV>
        <KV k="taxable">{rounding.taxable_value}</KV>
        <KV k="tax">{rounding.tax}</KV>
        <KV k="split">{rounding.split}</KV>
        <KV k="per line">{rounding.per_line}</KV>
        <KV k="never">{rounding.never}</KV>
      </div>
      <p className="hint">{note}</p>
      {hints?.map((h) => <p className="hint" key={h}>{h}</p>)}
    </Card>
  );
}
