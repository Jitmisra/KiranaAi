import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as cust from '../lib/custapi';
import { money } from '../lib/manageapi';
import { ago } from '../lib/inbound';
import {
  Button, Card, Empty, KV, Pill, Refusal, Segmented, Skeleton, SkeletonText, Verdict,
} from '../components/ui';
import '../styles/customers.css';

/**
 * Customers — the people who buy, and what the shop already knows about them.
 *
 * Every figure on this screen is derived by the server from this shop's own
 * order files at the moment it is asked. There is no customer record anywhere,
 * so there is nothing on this screen to edit, merge or delete, and no button
 * that would pretend otherwise.
 *
 * THE SHAPE OF THE SCREEN IS THE PRIVACY RULE. A narrow index of everybody on
 * the left; ONE person, opened by their number, on the right. Addresses exist
 * only in the second of those, because the server's list shape has no address
 * field in it at all — a page that rendered forty doorsteps at once would be
 * one screenshot away from being a leak, and nobody at a counter ever needed
 * forty addresses.
 *
 * Colour: REGULAR is marked in blue, the machine's own mark, and never in
 * green. Green appears exactly once here — on money a gateway webhook
 * confirmed — because on this product a green thing means a payment actually
 * settled.
 *
 * WHAT A VERDICT SAYS HERE. This screen used to state the asked-for and the
 * confirmed-paid figures side by side and leave the reader to notice that they
 * are different numbers about different things. They now carry a verdict each:
 * GREEN only where a signature-verified webhook settled money, BLUE where
 * nothing has settled yet — a customer who owes this shop money is not an error
 * and must never wear amber or red — and AMBER only where the server itself
 * abstained, on an order whose total it would not read.
 */

/** The storefront's own status vocabulary. Anything else is shown as it came. */
const STATUS: Record<string, string> = {
  new: 'New',
  preparing: 'Packing',
  out_for_delivery: 'Out for delivery',
  delivered: 'Delivered',
  cancelled: 'Cancelled',
};

function statusLabel(s: string): string {
  return STATUS[s] ?? s.replace(/_/g, ' ');
}

/** Typing should not fire a request per keystroke; the server rescans every order. */
const SEARCH_SETTLE_MS = 250;

/** How many of a long history to draw before asking whether the rest is wanted. */
const ORDERS_SHOWN = 20;

const SORTS: Array<{ value: cust.Sort; label: string; title: string }> = [
  { value: 'recent', label: 'Recent', title: 'Whoever ordered most recently, first' },
  { value: 'spend', label: 'Spend', title: 'By what they have asked this shop for' },
  { value: 'orders', label: 'Orders', title: 'By how many times they have ordered' },
  { value: 'name', label: 'Name', title: 'Alphabetically' },
];

const RANKINGS: Array<{ value: cust.Ranking; label: string; title: string }> = [
  { value: 'spend', label: 'By spend', title: 'The biggest baskets' },
  { value: 'frequency', label: 'By visits', title: 'The people who keep coming back' },
];

function MagnifierIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false">
      <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path d="M10.4 10.4 L14 14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

function Chevron() {
  return (
    <svg className="cu-chev" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true" focusable="false">
      <path d="M6 3.5 L10.5 8 L6 12.5" fill="none" stroke="currentColor" strokeWidth="1.8"
            strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** A number a shopkeeper reads across a row. Tabular, so columns line up. */
function Fig({ label, value, sub, green }: {
  label: string; value: string; sub?: string; green?: boolean;
}) {
  return (
    <div className={green ? 'cu-fig green' : 'cu-fig'}>
      <span className="lbl">{label}</span>
      <span className="val tnum">{value}</span>
      {sub && <span className="sub">{sub}</span>}
    </div>
  );
}

export default function Customers() {
  const [q, setQ] = useState('');
  const [needle, setNeedle] = useState('');
  const [sort, setSort] = useState<cust.Sort>('recent');

  const [list, setList] = useState<cust.CustomerList | null>(null);
  const [listRefusal, setListRefusal] = useState<cust.Refusal | null>(null);
  const [listBusy, setListBusy] = useState(false);

  const [reg, setReg] = useState<cust.RegularsList | null>(null);
  const [regRefusal, setRegRefusal] = useState<cust.Refusal | null>(null);
  const [ranking, setRanking] = useState<cust.Ranking>('spend');

  const [phone, setPhone] = useState<string | null>(null);
  const [detail, setDetail] = useState<cust.CustomerDetail | null>(null);
  const [detailRefusal, setDetailRefusal] = useState<cust.Refusal | null>(null);
  const [showAllOrders, setShowAllOrders] = useState(false);

  /**
   * A refused read used to leave the screen stuck until the whole page was
   * reloaded. Bumping a nonce re-runs the effect that fetched it, which is the
   * same request the screen made on its own — not a second path to the server.
   */
  const [listNonce, setListNonce] = useState(0);
  const [regNonce, setRegNonce] = useState(0);
  const [detailNonce, setDetailNonce] = useState(0);

  const detailRef = useRef<HTMLDivElement>(null);

  /* ---- the search box settles before it asks ----------------------------- */

  useEffect(() => {
    const id = setTimeout(() => setNeedle(q.trim()), SEARCH_SETTLE_MS);
    return () => clearTimeout(id);
  }, [q]);

  /* ---- the list ---------------------------------------------------------- */

  useEffect(() => {
    let alive = true;
    setListBusy(true);
    void (async () => {
      const res = await cust.customers(needle, sort);
      if (!alive) return;
      setListBusy(false);
      if (res.ok) {
        setList(res);
        setListRefusal(null);
      } else {
        // The previous list is kept on screen underneath the refusal. Blanking
        // it would take away the only thing that still tells the shopkeeper
        // anything while the reason is being read.
        setListRefusal(res);
      }
    })();
    return () => { alive = false; };
  }, [needle, sort, listNonce]);

  /* ---- who counts as a regular, according to the server ------------------ */

  useEffect(() => {
    let alive = true;
    void (async () => {
      const res = await cust.regulars();
      if (!alive) return;
      if (res.ok) { setReg(res); setRegRefusal(null); } else { setRegRefusal(res); }
    })();
    return () => { alive = false; };
  }, [regNonce]);

  /* ---- one person ------------------------------------------------------- */

  const open = useCallback((p: string) => {
    setPhone(p);
    setDetail(null);
    setDetailRefusal(null);
    setShowAllOrders(false);
  }, []);

  const close = useCallback(() => {
    setPhone(null);
    setDetail(null);
    setDetailRefusal(null);
  }, []);

  useEffect(() => {
    if (phone === null) return;
    let alive = true;
    void (async () => {
      const res = await cust.customer(phone);
      if (!alive) return;
      if (res.ok) { setDetail(res); setDetailRefusal(null); } else { setDetailRefusal(res); }
    })();
    return () => { alive = false; };
  }, [phone, detailNonce]);

  // On a phone the index and the person are the same column, so opening
  // somebody scrolls the page to them. On a wide screen they sit side by side
  // and are both already in view, so moving the page would be a surprise.
  useEffect(() => {
    if (phone === null) return;
    if (window.innerWidth >= 1100) return;
    detailRef.current?.scrollIntoView({ block: 'start' });
  }, [phone]);

  useEffect(() => {
    if (phone === null) return;
    const on = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
    addEventListener('keydown', on);
    return () => removeEventListener('keydown', on);
  }, [phone, close]);

  /**
   * The floor for calling somebody a regular is the SERVER'S number, read off
   * the regulars response. Until it has answered, nobody is marked — a `2`
   * written into this page would be a second definition of "regular" sitting
   * next to the real one, free to disagree with it.
   */
  const floor = reg?.min_orders_for_frequency ?? null;
  const isRegular = useCallback(
    (c: cust.CustomerSummary) => floor !== null && c.kept_count >= floor,
    [floor],
  );

  const ranked = useMemo(() => {
    if (!reg) return [];
    return (ranking === 'spend' ? reg.by_spend : reg.by_frequency) ?? [];
  }, [reg, ranking]);

  const rows = list?.customers ?? [];
  const capped = list !== null && list.matched > list.count;

  /* ---- one row of the index --------------------------------------------- */

  const renderRow = (c: cust.CustomerSummary) => (
    <button
      type="button"
      key={c.phone}
      className={`cu-row${isRegular(c) ? ' regular' : ''}${phone === c.phone ? ' on' : ''}`}
      aria-current={phone === c.phone ? 'true' : undefined}
      onClick={() => open(c.phone)}
    >
      <span className="nm">{c.name || <span className="cu-unnamed">No name given</span>}</span>
      <span className="amt tnum">{money(c.total_paise)}</span>
      <span className="sub">
        <span className="tnum">{cust.dialled(c.phone)}</span>
        {' · '}
        {c.order_count} order{c.order_count === 1 ? '' : 's'}
        {c.last_order_at ? <> · {ago(c.last_order_at)}</> : null}
      </span>
      {isRegular(c) && <span className="cu-reg">REGULAR</span>}
    </button>
  );

  return (
    <>
      <div className="page-head">
        <h1>Customers</h1>
        <p>
          Everyone who has ordered from the shutter code, worked out from this shop’s own
          order files each time this screen is opened. Nobody is stored, nobody can be added
          by hand, and nothing about a customer leaves this machine.
        </p>
      </div>

      <div className={`cu-grid${phone !== null ? ' open' : ''}`}>
        {/* --------------------------------------------------------- index -- */}
        <div className="stack cu-col-list">
          <Card
            title="Everyone who has ordered"
            aside={
              list && <Pill tone="off">{list.total_customers} {list.total_customers === 1 ? 'person' : 'people'}</Pill>
            }
          >
            <div className="cu-controls">
              <div className="cu-search">
                <span className="ic"><MagnifierIcon /></span>
                <input
                  type="search"
                  value={q}
                  maxLength={40}
                  placeholder="A name, or any part of a number"
                  aria-label="Search customers by name or phone number"
                  onChange={(e) => setQ(e.target.value)}
                />
                {q !== '' && (
                  <button type="button" className="cl" onClick={() => setQ('')} aria-label="Clear the search">
                    ×
                  </button>
                )}
              </div>
              <Segmented value={sort} onChange={setSort} options={SORTS} />
            </div>

            {listRefusal && (
              <Refusal
                reason={listRefusal.reason}
                detail={listRefusal.detail}
                // Only true when there IS a list under it. Said over an empty
                // panel it would be describing rows that are not there — and
                // it names the search those rows actually answer, because the
                // box has moved on and they have not.
                hint={list === null ? undefined
                  : `Nothing was changed. The rows below are still the answer to ${
                    list.q ? `“${list.q}”` : 'the unfiltered list'}, read before this happened.`}
                action={
                  <Button size="sm" loading={listBusy} onClick={() => setListNonce((n) => n + 1)}>
                    READ IT AGAIN
                  </Button>
                }
              />
            )}

            {/* Nothing has ever been read. When that is because the server
                refused, the reason above is the whole story: an empty state
                under it would be this page asserting the shop has no
                customers, which is exactly what it does not know. */}
            {list === null ? (
              listRefusal ? null : (
                <div className="cu-skels" role="status" aria-label="Reading the customer list">
                  {/* At the shape of a row: a name over a number, with the
                      total held to the right, four times over. */}
                  {[0, 1, 2, 3].map((i) => (
                    <div className="cu-row" key={i} aria-hidden="true">
                      <span className="nm"><Skeleton w={i % 2 ? 96 : 128} h={12} radius={999} /></span>
                      <span className="amt"><Skeleton w={56} h={12} radius={999} /></span>
                      <span className="sub"><Skeleton w={172} h={9} radius={999} /></span>
                    </div>
                  ))}
                </div>
              )
            ) : rows.length === 0 ? (
              list.total_customers === 0 ? (
                <Empty title="Nobody has ordered yet">
                  A customer appears on this screen the moment they send an order from the
                  shutter code — there is no way to add one by hand. Print the code from the
                  Orders screen and tape it where a customer waiting at the counter can
                  photograph it.
                </Empty>
              ) : (
                /* `list.q` IS THE SERVER'S ECHO OF THE SEARCH THAT PRODUCED
                   THESE ROWS, and `needle` is what is in the box right now.
                   They are the same thing until a read is refused, at which
                   point the box has moved on and the rows have not — and a
                   heading built from `needle` would then put the server's name
                   to a search it never ran. */
                <Empty
                  title={`Nothing matches “${list.q}”`}
                  action={<Button size="sm" onClick={() => setQ('')}>CLEAR THE SEARCH</Button>}
                >
                  A number matches on any run of its digits — the last four will do — and a
                  name matches on any part of it. All {list.total_customers}{' '}
                  {list.total_customers === 1 ? 'person is' : 'people are'} still here behind
                  the search.
                </Empty>
              )
            ) : (
              <div className={listBusy ? 'cu-list busy' : 'cu-list'}>{rows.map(renderRow)}</div>
            )}

            {list && (
              <div className="cu-foot">
                {capped && (
                  <p className="hint">
                    Showing {list.count} of {list.matched} matches. Type more of the name or the
                    number rather than scrolling — the list is capped, not paged.
                  </p>
                )}
                {list.orders_without_a_phone > 0 && (
                  <p className="hint">
                    {list.orders_without_a_phone === 1
                      ? 'One order carries no dialable number, so it belongs to nobody on this screen.'
                      : `${list.orders_without_a_phone} orders carry no dialable number, so they belong to nobody on this screen.`}
                    {' '}That is a different thing from having no customers.
                  </p>
                )}
                {floor !== null && (
                  <p className="hint">
                    The blue edge marks a regular: somebody who has kept {floor} or more orders.
                    That floor is the shop’s, not this page’s.
                  </p>
                )}
                <p className="hint">
                  This list carries no addresses at all — not hidden, absent from what the server
                  sends it. Open one person to see where they live.
                </p>
              </div>
            )}
          </Card>
        </div>

        {/* -------------------------------------------------------- person -- */}
        <div className="stack cu-col-side">
          <div ref={detailRef}>
            {phone !== null && (
              <Card
                title={detail ? (detail.name || 'No name given') : 'Opening…'}
                aside={
                  <div className="row">
                    {detail && isRegular(detail) && <Pill tone="code">REGULAR</Pill>}
                    <button type="button" className="btn ghost sm" onClick={close}>CLOSE</button>
                  </div>
                }
              >
                {detailRefusal && (
                  <Refusal
                    reason={detailRefusal.reason}
                    detail={detailRefusal.detail}
                    hint="Nobody can be added by hand. A customer appears when they order."
                    action={
                      <div className="btn-row">
                        <Button size="sm" onClick={() => setDetailNonce((n) => n + 1)}>
                          TRY AGAIN
                        </Button>
                        <Button size="sm" variant="ghost" onClick={close}>CLOSE</Button>
                      </div>
                    }
                  />
                )}

                {/* At the shape of the person: the number, four figures, then
                    the run of facts. Not one grey slab that becomes a page. */}
                {!detail && !detailRefusal && (
                  <div role="status" aria-label="Opening this customer">
                    <div className="cu-who" aria-hidden="true">
                      <Skeleton w={190} h={26} radius={999} />
                    </div>
                    <div className="cu-figs" aria-hidden="true">
                      {[0, 1, 2, 3].map((i) => (
                        <div className="cu-fig" key={i}>
                          <span className="lbl"><Skeleton w={64} h={8} radius={999} /></span>
                          <span className="val"><Skeleton w={92} h={20} radius={999} /></span>
                          <span className="sub"><Skeleton w={78} h={8} radius={999} /></span>
                        </div>
                      ))}
                    </div>
                    <div style={{ marginTop: 20 }} aria-hidden="true"><SkeletonText lines={4} /></div>
                  </div>
                )}

                {detail && (
                  <>
                    <div className="cu-who">
                      <a className="tel tnum" href={`tel:${detail.phone}`}>{cust.dialled(detail.phone)}</a>
                      {detail.phone_as_given && detail.phone_as_given !== detail.phone && (
                        <span className="muted">typed as {detail.phone_as_given}</span>
                      )}
                    </div>

                    <div className="cu-figs">
                      <Fig
                        label="asked for"
                        value={money(detail.total_paise)}
                        sub={`${detail.kept_count} order${detail.kept_count === 1 ? '' : 's'} kept`}
                      />
                      <Fig
                        label="paid — confirmed"
                        value={money(detail.paid_paise)}
                        sub={`${detail.paid_count} of ${detail.order_count} orders`}
                        green={detail.paid_paise > 0}
                      />
                      <Fig
                        label="cancelled"
                        value={money(detail.cancelled_paise)}
                        sub={`${detail.cancelled_count} order${detail.cancelled_count === 1 ? '' : 's'}`}
                      />
                      <Fig
                        label="last came"
                        value={detail.last_order_at ? ago(detail.last_order_at) : '—'}
                        sub={detail.last_order_at ? cust.dayOf(detail.last_order_at) : undefined}
                      />
                    </div>

                    {/* THE VERDICT ON THE TWO FIGURES ABOVE.
                        Green is money and only money: a signature-verified
                        webhook said it arrived. Where none has, the verdict is
                        BLUE — somebody who has not paid yet is not an error and
                        does not get the colour that means one, on a screen a
                        shopkeeper reads about their own regulars. */}
                    {detail.paid_paise > 0 ? (
                      <Verdict
                        tone="green"
                        title={`${money(detail.paid_paise)} confirmed by the gateway`}
                      >
                        {detail.paid_count} of {detail.order_count} order
                        {detail.order_count === 1 ? '' : 's'} on this number settled, each on a
                        signature-verified webhook from the payment gateway. This shop did not
                        decide any of it — nothing here turns green until that callback arrives.
                      </Verdict>
                    ) : (
                      <Verdict tone="info" title="No payment on this number has settled yet">
                        {money(detail.total_paise)} was asked for across {detail.kept_count} kept
                        order{detail.kept_count === 1 ? '' : 's'}, and no gateway webhook has
                        confirmed any of it arriving. That is not a debt this screen can assert:
                        cash handed over at the door leaves no record here at all.
                      </Verdict>
                    )}

                    <p className="hint cu-split">
                      Asked for and paid are two numbers on purpose. Paid is only the part a
                      signed gateway webhook confirmed arrived — an order placed is not money
                      received — and cancelled orders are in neither figure.
                    </p>

                    <div className="cu-sec">
                      <span className="t">Known here since</span>
                      <span className="rule" />
                    </div>
                    <KV k="first order">{cust.dayOf(detail.first_order_at)}</KV>
                    <KV k="last order">
                      {cust.dayOf(detail.last_order_at)}
                      {detail.last_status && (
                        <span className="muted"> · {statusLabel(detail.last_status)}</span>
                      )}
                    </KV>
                    <KV k="days between the two">
                      {detail.days_known === null ? 'not readable' : `${detail.days_known}`}
                    </KV>
                    {detail.unpriced_count > 0 && (
                      <KV k="orders with no readable total">
                        <span className="cu-warn">{detail.unpriced_count}</span>
                      </KV>
                    )}
                    {/* AMBER, because the server abstained. It is the only
                        amber on this screen and it is not about this person:
                        it is about what the counter would not read. */}
                    {detail.unpriced_count > 0 && (
                      <Verdict
                        tone="amber"
                        title={detail.unpriced_count === 1
                          ? 'One order here has no total this counter would read'
                          : `${detail.unpriced_count} orders here have no total this counter would read`}
                      >
                        {detail.unpriced_count === 1 ? 'It is' : 'They are'} counted as visits
                        and {detail.unpriced_count === 1 ? 'is' : 'are'} in none of the rupee
                        figures above. The server abstains rather than rounding a total it
                        cannot read, so {money(detail.total_paise)} is what it could read and
                        not what this person has spent.
                      </Verdict>
                    )}

                    {detail.names_seen.length > 1 && (
                      <>
                        <div className="cu-sec">
                          <span className="t">Names used on this number</span>
                          <span className="rule" />
                        </div>
                        <div className="cu-names">
                          {detail.names_seen.map((n) => <span className="cu-name" key={n}>{n}</span>)}
                        </div>
                        <p className="hint">
                          One number is one customer here. More than one name on it is a family
                          ordering together — or a number the operator gave to somebody new. The
                          names are shown rather than merged so you can tell which.
                        </p>
                      </>
                    )}

                    <div className="cu-sec">
                      <span className="t">Where to send it</span>
                      <span className="n">{detail.addresses.length}</span>
                      <span className="rule" />
                    </div>
                    {detail.addresses.length === 0 ? (
                      <Empty title="No address on this number">
                        Not one order from this number carried a delivery address, so there is
                        nowhere on file to send anything. Ring{' '}
                        <span className="tnum">{cust.dialled(detail.phone)}</span> and ask —
                        the address arrives here on their next order, not by being typed in.
                      </Empty>
                    ) : (
                      <div className="cu-addrs">
                        {detail.addresses.map((a) => (
                          <div className="cu-addr" key={a.address}>
                            <address>{a.address}</address>
                            <span className="sub">
                              {a.orders} order{a.orders === 1 ? '' : 's'} · last used {cust.dayOf(a.last_seen)}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                    <p className="hint">
                      Addresses are on this screen and on no other. The list beside it carries a
                      count of them and no address at all, because looking one person up is a
                      different act from holding everybody’s doorstep on one page. The newest
                      address is first — the older ones stay for the delivery that says “the new
                      place, not the old one”.
                    </p>

                    <div className="cu-sec">
                      <span className="t">Every order</span>
                      <span className="n">{detail.orders.length}</span>
                      <span className="rule" />
                    </div>
                    {detail.orders.length === 0 ? (
                      <Empty title="No orders on this number">
                        This number is known to the shop but carries no order file the server
                        could read. Nothing here can be added by hand; the next order they send
                        from the shutter code fills this in.
                      </Empty>
                    ) : (
                      <div className="cu-orders">
                        {(showAllOrders ? detail.orders : detail.orders.slice(0, ORDERS_SHOWN))
                          .map((o) => (
                            <details className={o.status === 'cancelled' ? 'cu-order off' : 'cu-order'}
                                     key={o.order_id}>
                              <summary>
                                <Chevron />
                                <span className="when">{cust.dayOf(o.at)}</span>
                                <Pill tone="off">{statusLabel(o.status)}</Pill>
                                <span className="items">
                                  {o.line_count} item{o.line_count === 1 ? '' : 's'}
                                </span>
                                <span className="spacer" />
                                {o.paid && <Pill tone="ok" dot>PAID</Pill>}
                                <span className="amt tnum">
                                  {o.priced ? money(o.total_paise) : 'no readable total'}
                                </span>
                              </summary>
                              <div className="cu-lines">
                                {o.lines.length === 0 ? (
                                  <p className="hint">This order file carries no lines.</p>
                                ) : o.lines.map((l, i) => (
                                  <div className="bill-line" key={`${o.order_id}:${l.sku_id}:${i}`}>
                                    <span className="nm">{l.name || l.sku_id}</span>
                                    <span className="qty">×{l.qty ?? '?'}</span>
                                    <span className="amt tnum">{money(l.line_paise)}</span>
                                  </div>
                                ))}
                                <div className="cu-oid mono">{o.order_id}</div>
                              </div>
                            </details>
                          ))}
                      </div>
                    )}
                    {!showAllOrders && detail.orders.length > ORDERS_SHOWN && (
                      <button type="button" className="btn sm cu-more"
                              onClick={() => setShowAllOrders(true)}>
                        SHOW ALL {detail.orders.length} ORDERS
                      </button>
                    )}
                  </>
                )}
              </Card>
            )}
          </div>

          <div className="stack cu-secondary">
            <Card
              title="Regulars"
              aside={<Segmented value={ranking} onChange={setRanking} options={RANKINGS} />}
            >
              {regRefusal && (
                <Refusal
                  reason={regRefusal.reason}
                  detail={regRefusal.detail}
                  hint="Without this, no row in the list beside it can be marked a regular: the floor is the shop's number and this page will not invent one."
                  action={
                    <Button size="sm" onClick={() => setRegNonce((n) => n + 1)}>TRY AGAIN</Button>
                  }
                />
              )}

              {!reg && !regRefusal && (
                <div className="cu-skels" role="status" aria-label="Working out the regulars">
                  {[0, 1, 2].map((i) => (
                    <div className="cu-rk" key={i} aria-hidden="true">
                      <span className="i"><Skeleton w={10} h={9} radius={999} /></span>
                      <span className="nm"><Skeleton w={i === 0 ? 120 : 92} h={11} radius={999} /></span>
                      <span className="sub"><Skeleton w={82} h={8} radius={999} /></span>
                      <span className="v"><Skeleton w={54} h={11} radius={999} /></span>
                    </div>
                  ))}
                </div>
              )}

              {reg && ranked.length === 0 && (
                <Empty
                  title={ranking === 'frequency' ? 'No regulars yet' : 'Nothing spent yet'}
                  action={
                    ranking === 'frequency'
                      ? <Button size="sm" onClick={() => setRanking('spend')}>SEE THEM BY SPEND</Button>
                      : <Button size="sm" onClick={() => setRanking('frequency')}>SEE THEM BY VISITS</Button>
                  }
                >
                  {ranking === 'frequency' ? (
                    <>
                      Nobody has kept {reg.min_orders_for_frequency} orders yet, and one visit is
                      not a habit. The floor is the shop’s number, not this page’s — names appear
                      here on their {reg.min_orders_for_frequency === 2 ? 'second' : `${reg.min_orders_for_frequency}th`}{' '}
                      kept order, on their own.
                    </>
                  ) : (
                    <>
                      No order this shop could price has been kept, so there is nothing to rank.
                      Cancelled orders are excluded from spend on purpose, and cash over the
                      counter never reaches this screen at all.
                    </>
                  )}
                </Empty>
              )}

              {reg && ranked.length > 0 && (
                <div className="cu-rank">
                  {ranked.map((c, i) => (
                    <button
                      type="button"
                      key={c.phone}
                      className={phone === c.phone ? 'cu-rk on' : 'cu-rk'}
                      onClick={() => open(c.phone)}
                    >
                      <span className="i tnum">{i + 1}</span>
                      <span className="nm">{c.name || 'No name given'}</span>
                      <span className="sub tnum">{cust.dialled(c.phone)}</span>
                      <span className="v tnum">
                        {ranking === 'spend'
                          ? money(c.total_paise)
                          : `${c.kept_count} order${c.kept_count === 1 ? '' : 's'}`}
                      </span>
                    </button>
                  ))}
                </div>
              )}

              {reg && (
                <p className="hint">
                  {ranking === 'frequency' ? (
                    <>
                      This is a COUNT of orders kept, not a rate: five orders in a week and five
                      across a year sit in the same place. Open somebody to see the dates that
                      separate them. Fewer than {reg.min_orders_for_frequency} orders is not a
                      habit, so those names are not here.
                    </>
                  ) : (
                    <>
                      By what each person has asked the shop for, cancelled orders excluded. The
                      biggest basket and the most loyal customer are usually two different people,
                      which is why these are two lists and not one score.
                    </>
                  )}
                </p>
              )}
            </Card>

            <Card title="What this screen can and cannot know" tight>
              <KV k="derived from">this shop’s order files, re-read every time</KV>
              <KV k="stored">nothing — there is no customer record to edit</KV>
              <KV k="one customer is">one phone number</KV>
              <KV k="not counted here">anybody who pays at the counter</KV>
              <p className="hint">
                Only orders from the shutter code carry a name and a number, so a regular who
                walks in and pays cash does not appear on this screen at all. A husband ordering
                on his wife’s number is her, here — there is nothing in an order that could say
                otherwise.
              </p>
              {list && (
                <p className="hint">
                  {list.orders_read} order{list.orders_read === 1 ? '' : 's'} read to draw this.
                </p>
              )}
            </Card>
          </div>
        </div>
      </div>
    </>
  );
}
