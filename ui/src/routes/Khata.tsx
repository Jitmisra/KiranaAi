import { useCallback, useEffect, useState } from 'react';
import * as kh from '../lib/khataapi';
import * as api from '../lib/api';
import { when } from '../lib/manageapi';
import { rupees, type Paise } from '../lib/money';
import {
  Button, Card, Empty, Field, Input, KV, Pill, Refusal, Skeleton, Table, Verdict, type Column,
} from '../components/ui';
import '../styles/khata.css';

/**
 * KHATA — the udhaar book. Collected by Razorpay; drops only on a signed webhook.
 *
 * EVERY NUMBER ON THIS SCREEN WAS DERIVED BY THE SERVER from the money
 * service's hash-chained log at the moment it was asked for:
 *
 *     outstanding = sum(booked bills) − sum(credited captures)
 *
 * The page adds nothing, nets nothing, rounds nothing. A figure the server
 * did not send is a dash, never a plausible zero.
 *
 * COLOUR IS RESERVED, AND THIS SCREEN IS MOSTLY WITHOUT IT. A bill on the book
 * is a debt in neutral ink — not green (nothing settled), not amber (nothing
 * abstained), not red (nothing refused). The only green here is a CAPTURE: paise
 * that a signature-verified webhook credited against a collection link. A
 * capture the kernel PARKED because it did not reconcile is shown in neutral
 * ink with its reason, and it is not in any total.
 *
 * WHAT COLLECT DOES. One press asks the money service for ONE Payment Link for
 * the whole balance, payable in parts, with Razorpay's reminders on and an
 * SMS to the customer's number. This counter sends no message. A second press
 * while a link is open is refused by name — `collection_link_already_open` —
 * and the refusal is printed as it came, with the open link beside it.
 *
 * THE QR IS A RENDER OF THE GATEWAY'S OWN LINK. The page never composes a
 * payment target; it asks the server for a PNG of the short_url, and the
 * server refuses to encode a host it does not recognise. On the simulator that
 * refusal is the correct answer, and it is shown as one.
 */

type Err = { reason: string; detail?: string; extra?: Record<string, unknown> };

function d(n: number | null | undefined): string {
  return n === null || n === undefined ? '—' : n.toLocaleString('en-IN');
}

/** The pill for a collection's state. Green ONLY for PAID: a link the
    gateway closed because every paisa of it arrived by signed webhook. */
function StatePill({ state }: { state: string | null | undefined }) {
  if (state === 'PAID') return <Pill tone="ok" dot>paid</Pill>;
  if (state === 'OPEN') return <Pill tone="code" dot>link open</Pill>;
  if (state === 'CALLING' || state === 'NEW') return <Pill tone="off">minting</Pill>;
  if (state === 'INDETERMINATE') return <Pill tone="off">unknown · needs a person</Pill>;
  if (state === 'EXPIRED') return <Pill tone="off">expired</Pill>;
  if (state === 'CANCELLED') return <Pill tone="off">cancelled</Pill>;
  return <Pill tone="off">{state ?? '—'}</Pill>;
}

/** The value line: the one sentence a judge hears, from the server's figures. */
function ValueLine({ v }: { v: kh.ValueLine }) {
  const one = v.households === 1;
  return (
    <section className="kh-value" aria-label="What the book is worth">
      <p className="kh-value-line">
        <b className="kh-ink">{rupees(v.outstanding_paise)}</b> outstanding across{' '}
        <b>{d(v.households)}</b> household{one ? '' : 's'}
        {v.households > 0 && <>, oldest <b>{d(v.oldest_days)}</b> day{v.oldest_days === 1 ? '' : 's'}</>}.
        {' '}
        <b className="kh-green">{rupees(v.collected_this_month_paise)}</b> collected this month
        {' '}through <b>{d(v.reminder_links_this_month)}</b> Razorpay reminder link{v.reminder_links_this_month === 1 ? '' : 's'}
        {' '}you never sent.
      </p>
      <div className="kh-tiles">
        <div className="kh-tile">
          <span className="lbl">On the book</span>
          <span className="val">{rupees(v.outstanding_paise)}</span>
          <span className="sub">sum of bills booked − captures credited</span>
        </div>
        <div className="kh-tile">
          <span className="lbl">Households</span>
          <span className="val">{d(v.households)}</span>
          <span className="sub">{d(v.households_total)} in the book, {d(v.links_open)} link{v.links_open === 1 ? '' : 's'} open</span>
        </div>
        <div className="kh-tile">
          <span className="lbl">Oldest</span>
          <span className="val">{d(v.oldest_days)}<span className="unit"> days</span></span>
          <span className="sub">oldest bill not yet covered</span>
        </div>
        <div className="kh-tile green">
          <span className="lbl">Collected this month</span>
          <span className="val">{rupees(v.collected_this_month_paise)}</span>
          <span className="sub">signed webhooks only · {d(v.reminder_links_this_month)} reminder link{v.reminder_links_this_month === 1 ? '' : 's'}</span>
        </div>
      </div>
      {v.parked_paise > 0 && (
        <p className="kh-parked">
          {rupees(v.parked_paise)} arrived that does not reconcile against any book. It is
          parked and named, not netted — a person looks at it.
        </p>
      )}
    </section>
  );
}

/** The QR of the gateway's link, or the server's refusal to encode it. */
function LinkQr({ bookId, collectionId, shortUrl }: {
  bookId: string; collectionId: string; shortUrl: string | null;
}) {
  const url = kh.qrUrl(bookId, collectionId);
  const [state, setState] = useState<{ kind: 'loading' } | { kind: 'png' } | { kind: 'refused'; e: Err }>({ kind: 'loading' });
  useEffect(() => {
    let alive = true;
    setState({ kind: 'loading' });
    void (async () => {
      try {
        const res = await fetch(url, { cache: 'no-store' });
        const ct = res.headers.get('content-type') ?? '';
        if (!alive) return;
        if (ct.startsWith('image/')) { setState({ kind: 'png' }); return; }
        const body = (await res.json()) as { reason?: string; detail?: string; host?: string };
        if (!alive) return;
        setState({ kind: 'refused', e: { reason: body.reason ?? `http_${res.status}`, detail: body.detail } });
      } catch (e) {
        if (alive) setState({ kind: 'refused', e: { reason: 'the counter could not be reached', detail: String(e) } });
      }
    })();
    return () => { alive = false; };
  }, [url]);
  if (state.kind === 'loading') return <Skeleton w="220px" h={220} />;
  if (state.kind === 'png') {
    return (
      <figure className="kh-qr">
        <img src={url} alt="Razorpay payment link, as a QR" width={220} height={220} />
        <figcaption className="mono">{shortUrl}</figcaption>
      </figure>
    );
  }
  return (
    <div className="kh-qr-refused">
      <Refusal reason={state.e.reason} detail={state.e.detail} />
      {shortUrl && (
        <p className="hint">
          The link itself, as the gateway issued it: <span className="mono">{shortUrl}</span>.
          Nothing was encoded because its host is not one a payable link may live on.
        </p>
      )}
    </div>
  );
}

export default function Khata() {
  const [book, setBook] = useState<kh.Book | null>(null);
  const [bookErr, setBookErr] = useState<Err | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<kh.HouseholdDetail | null>(null);
  const [detailErr, setDetailErr] = useState<Err | null>(null);
  const [collecting, setCollecting] = useState(false);
  const [collectErr, setCollectErr] = useState<Err | null>(null);
  const [collected, setCollected] = useState<kh.Collected | null>(null);
  const [mode, setMode] = useState<string | null>(null);
  const [simRupees, setSimRupees] = useState('200');
  const [simming, setSimming] = useState(false);
  const [simNote, setSimNote] = useState<string | null>(null);
  const [simErr, setSimErr] = useState<Err | null>(null);

  const load = useCallback(async () => {
    const r = await kh.book();
    if (r.ok) { setBook(r); setBookErr(null); } else { setBookErr({ reason: r.reason, detail: r.detail }); }
    setLoading(false);
  }, []);

  const loadDetail = useCallback(async (id: string) => {
    const r = await kh.household(id);
    if (r.ok) { setDetail(r); setDetailErr(null); } else { setDetail(null); setDetailErr({ reason: r.reason, detail: r.detail }); }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    void (async () => {
      const h = await api.moneyHealth();
      setMode(h.ok ? String((h as unknown as { mode?: string }).mode ?? '') : null);
    })();
  }, []);
  useEffect(() => {
    if (selected) void loadDetail(selected);
    else setDetail(null);
  }, [selected, loadDetail]);

  // Follow the book from the till: `#/khata?book=bk_…` opens that household.
  useEffect(() => {
    const q = location.hash.split('?')[1] ?? '';
    const want = new URLSearchParams(q).get('book');
    if (want) setSelected(want);
  }, []);

  const refresh = useCallback(async () => {
    await load();
    if (selected) await loadDetail(selected);
  }, [load, loadDetail, selected]);

  const collect = useCallback(async () => {
    if (!selected || collecting) return;
    setCollecting(true);
    setCollectErr(null);
    setCollected(null);
    setSimNote(null);
    try {
      const r = await kh.collect(selected);
      if (!r.ok) {
        const rr = r as unknown as Record<string, unknown>;
        setCollectErr({
          reason: r.reason, detail: r.detail,
          extra: { short_url: rr.short_url, collection_id: rr.collection_id, state: rr.state },
        });
      } else {
        setCollected(r);
      }
    } finally {
      setCollecting(false);
      await refresh();
    }
  }, [selected, collecting, refresh]);

  const simPay = useCallback(async () => {
    const live = detail?.live_collection;
    if (!live || simming) return;
    const r = /^\d+(\.\d{1,2})?$/.test(simRupees.trim()) ? simRupees.trim() : '';
    if (!r) { setSimErr({ reason: 'not a rupee figure', detail: 'Whole rupees, or two decimal places.' }); return; }
    const [w, f = ''] = r.split('.');
    const paiseN = (Number(w) * 100 + Number((f + '00').slice(0, 2))) as Paise;
    setSimming(true);
    setSimErr(null);
    setSimNote(null);
    try {
      const res = await kh.simPay(live.collection_id, paiseN);
      if (!res.ok) { setSimErr({ reason: res.reason, detail: res.detail }); return; }
      const w0 = res.webhooks[0];
      const c = w0?.collection;
      setSimNote(
        `${w0?.event ?? 'webhook'} · signed by the simulator · `
        + (c?.credited ? `credited ${rupees(paiseN)}; ${c.outstanding_rupees ? `₹${c.outstanding_rupees} still on the book` : ''}`
          : c?.replayed ? 'replayed — nothing changed'
            : `not credited: ${c?.capture_reason ?? c?.reason ?? w0?.reason ?? 'refused'}`),
      );
    } finally {
      setSimming(false);
      await refresh();
    }
  }, [detail, simming, simRupees, refresh]);

  const cols: ReadonlyArray<Column<kh.Household>> = [
    {
      key: 'who', head: 'Household',
      cell: (h) => (
        <div className="kh-who">
          <span className="nm">{h.name || <i>unnamed</i>}</span>
          <span className="ph mono">{h.phone_masked || h.book_id}</span>
        </div>
      ),
    },
    {
      key: 'due', head: 'On the book', num: true,
      cell: (h) => <span className="kh-ink tnum">{rupees(h.outstanding_paise)}</span>,
    },
    {
      key: 'old', head: 'Oldest', num: true, drop: true,
      cell: (h) => <span className="tnum">{h.oldest_days === null ? '—' : `${d(h.oldest_days)} d`}</span>,
    },
    {
      key: 'last', head: 'Last collected', drop: true,
      cell: (h) => h.last_capture
        ? <span><span className="kh-green tnum">{rupees(h.last_capture.amount_paise)}</span> <span className="muted">{when(h.last_capture.at)}</span></span>
        : <span className="muted">—</span>,
    },
    {
      key: 'link', head: 'Link',
      cell: (h) => h.live_collection
        ? <StatePill state={h.live_collection.state} />
        : h.needs_human ? <Pill tone="off">needs a person</Pill> : <span className="muted">—</span>,
    },
  ];

  const ledgerCols: ReadonlyArray<Column<kh.LedgerEntry>> = [
    {
      key: 'what', head: 'What',
      cell: (e) => {
        if (e.kind === 'bill') return <span>Bill booked <span className="mono muted">{e.session_id}</span></span>;
        if (e.kind === 'capture') {
          return (
            <span>
              {e.credited ? 'Captured' : 'Parked'}{' '}
              <span className="muted">{e.razorpay_event ?? ''}{e.final ? ' · final' : ''}</span>
              {e.parked && e.reason && <><br /><span className="mono muted">{e.reason}</span></>}
            </span>
          );
        }
        return (
          <span>
            Collection link <StatePill state={e.state} />
            {e.short_url && <><br /><span className="mono muted">{e.short_url}</span></>}
          </span>
        );
      },
    },
    {
      key: 'when', head: 'When', drop: true,
      cell: (e) => <span className="muted">{when(e.at)}</span>,
    },
    {
      key: 'amt', head: 'Amount', num: true,
      cell: (e) => {
        if (e.kind === 'capture' && e.credited) return <span className="kh-green tnum">+ {rupees(e.amount_paise)}</span>;
        if (e.kind === 'capture') return <span className="kh-ink tnum">({rupees(e.amount_paise)} parked)</span>;
        if (e.kind === 'bill') return <span className="kh-ink tnum">{rupees(e.amount_paise)}</span>;
        return <span className="tnum muted">{e.amount_paise === null ? '—' : rupees(e.amount_paise)} asked · <span className="kh-green">{rupees(e.captured_paise)}</span> paid</span>;
      },
    },
  ];

  const live = detail?.live_collection ?? null;

  return (
    <div className="kh-page">
      <div className="page-head">
        <h1>Khata <span className="kh-deva" lang="hi">खाता</span></h1>
        <p>
          The udhaar book. A bill closes onto a household in neutral ink; one COLLECT mints one
          Razorpay link for the whole balance, payable in parts, with Razorpay&rsquo;s own reminders.
          The balance drops only when a signature-verified webhook credits a capture.
        </p>
      </div>

      {loading && <Skeleton w="100%" h={120} />}
      {bookErr && <Refusal reason={bookErr.reason} detail={bookErr.detail} />}
      {book && <ValueLine v={book.value} />}

      <div className="kh-grid">
        <Card title="Households" sub="worst balance first" flush>
          {book && book.households.length === 0 ? (
            <div className="kh-empty">
              <Empty title="Nobody is on the book">
                Bill something on the till and press ON THE BOOK, or say
                &ldquo;Sharma ji ke khate mein likh do&rdquo; to Salaahkaar.
              </Empty>
            </div>
          ) : (
            <Table<kh.Household>
              cols={cols}
              rows={book?.households ?? []}
              rowKey={(h) => h.book_id}
              loading={loading}
              onRowClick={(h) => setSelected(h.book_id)}
              isOpen={(h) => h.book_id === selected}
              label="Households with a book"
            />
          )}
          {book && (
            <p className="kh-chain">
              Chain {book.chain.ok ? 'verified' : 'BROKEN'} · {d(book.chain.lines_verified)} lines
              {book.chain.error && <> · <span className="mono">{book.chain.error}</span></>}
            </p>
          )}
        </Card>

        <div className="kh-detail">
          {!selected && (
            <Card title="One household" sub="pick a row to see its ledger and collect">
              <Empty>Nothing selected.</Empty>
            </Card>
          )}
          {selected && detailErr && <Refusal reason={detailErr.reason} detail={detailErr.detail} />}
          {selected && detail && (
            <>
              <Card
                title={detail.name || 'Unnamed household'}
                sub={<span className="mono">{detail.phone || detail.book_id}</span>}
                aside={
                  <Button
                    variant="primary"
                    onClick={() => void collect()}
                    disabled={collecting || detail.outstanding_paise <= 0}
                    title={detail.outstanding_paise <= 0 ? 'Nothing outstanding on this book.' : undefined}
                  >
                    {collecting ? 'ASKING FOR A LINK…' : `COLLECT ${rupees(detail.outstanding_paise)}`}
                  </Button>
                }
              >
                <div className="kh-figs">
                  <div>
                    <span className="l">Booked</span>
                    <span className="v kh-ink">{rupees(detail.booked_paise)}</span>
                    <span className="s">{d(detail.bills)} bill{detail.bills === 1 ? '' : 's'}</span>
                  </div>
                  <div>
                    <span className="l">Settled</span>
                    <span className="v kh-green">{rupees(detail.captured_paise)}</span>
                    <span className="s">signed webhooks only</span>
                  </div>
                  <div>
                    <span className="l">Still on the book</span>
                    <span className="v kh-ink">{rupees(detail.outstanding_paise)}</span>
                    <span className="s">{detail.oldest_days === null ? 'nothing owed' : `oldest ${d(detail.oldest_days)} d`}</span>
                  </div>
                  {detail.parked_paise > 0 && (
                    <div>
                      <span className="l">Parked</span>
                      <span className="v kh-ink">{rupees(detail.parked_paise)}</span>
                      <span className="s">did not reconcile · needs a person</span>
                    </div>
                  )}
                </div>

                {collectErr && (
                  <div className="kh-refusal">
                    <Refusal reason={collectErr.reason} detail={collectErr.detail} />
                    {typeof collectErr.extra?.short_url === 'string' && (
                      <p className="hint">
                        The open link, as the gateway issued it:{' '}
                        <span className="mono">{String(collectErr.extra.short_url)}</span>
                      </p>
                    )}
                  </div>
                )}
                {collected && (
                  <Verdict tone="info" title={`One link minted for ${rupees(collected.amount_paise)}`}>
                    accept_partial on, first instalment at least{' '}
                    {collected.first_min_partial_amount !== null ? rupees(collected.first_min_partial_amount) : '—'};
                    {' '}reminders {collected.reminder_enable ? 'on — Razorpay sends them' : 'off'}.
                    {' '}{collected.note}
                  </Verdict>
                )}

                {live && (
                  <div className="kh-link">
                    <div className="kh-link-head">
                      <StatePill state={live.state} />
                      <span className="kh-link-amt">
                        <span className="kh-green tnum">{rupees(live.captured_paise)}</span> settled ·{' '}
                        <span className="kh-ink tnum">{live.still_due_paise !== null && live.still_due_paise !== undefined ? rupees(live.still_due_paise) : '—'}</span> still on the book
                      </span>
                    </div>
                    <KV k="Link">
                      <span className="mono">{live.short_url ?? '—'}</span>
                    </KV>
                    <KV k="Expires">{live.expire_by ? new Date(live.expire_by * 1000).toLocaleString('en-IN') : '—'}</KV>
                    <KV k="Reminders">Razorpay, by SMS. This counter sends nothing.</KV>
                    <LinkQr bookId={detail.book_id} collectionId={live.collection_id} shortUrl={live.short_url} />

                    {mode === 'sim' && (
                      <div className="kh-sim">
                        <div className="eyebrow">Simulator only</div>
                        <p className="hint">
                          The money service is on the simulator, so the customer&rsquo;s phone is
                          this box. A payment here produces a SIGNED webhook and goes through the
                          same gates a real one would.
                        </p>
                        <div className="kh-sim-row">
                          <Field label="Pay, in rupees" htmlFor="kh-sim-amt">
                            <Input id="kh-sim-amt" inputMode="decimal" value={simRupees}
                                   onChange={(e) => setSimRupees(e.target.value)} />
                          </Field>
                          <Button onClick={() => void simPay()} disabled={simming}>
                            {simming ? 'PAYING…' : 'Simulate a partial payment'}
                          </Button>
                        </div>
                        {simErr && <Refusal reason={simErr.reason} detail={simErr.detail} />}
                        {simNote && <p className="kh-sim-note">{simNote}</p>}
                      </div>
                    )}
                  </div>
                )}
              </Card>

              <Card title="Ledger" sub="every line, newest first, from the chain" flush>
                <Table<kh.LedgerEntry>
                  cols={ledgerCols}
                  rows={detail.entries}
                  rowKey={(e, i) => `${e.kind}-${i}`}
                  empty="Nothing on this book yet."
                  label="Ledger"
                />
              </Card>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
