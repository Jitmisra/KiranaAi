import { useCallback, useEffect, useMemo, useState } from 'react';
import * as shopapi from '../lib/shopapi';
import { rupees } from '../lib/money';
import {
  Button, Card, Empty, KV, Modal, Pill, Refusal, Skeleton, SkeletonText, Verdict, IcoParcel,
} from '../components/ui';
import type { PillTone } from '../components/ui';
import { ShareSheet } from '../components/ShareSheet';
import type { ShareTarget } from '../lib/shareapi';
import '../styles/storefront.css';

/**
 * Orders — the shopkeeper's side of the storefront, read on the laptop at the
 * counter while somebody is being served.
 *
 * The screen has one job: make a new order impossible to miss and its next
 * action a single press. So it polls, it puts the newest order at the top,
 * groups the open queue by where each order actually is — NEW first, because
 * NEW is the pile that needs a decision — and it draws ONLY the status buttons
 * the server says are legal from where the order stands. Drawing all five and
 * letting the server refuse three of them would teach a shopkeeper to expect
 * refusals, which is how people learn to click through them.
 *
 * A STRANDED ORDER IS THE ONE THING THIS SCREEN COULD NOT SAY. Deleting a
 * product removes every place that could price it — which is right — but an
 * order already placed keeps its lines, and the money service re-derives every
 * rupee from its own book at mint time. It will not find that sku, so the mint
 * is refused with `amber_in_basket` and the order can never be paid. That
 * happened here: four orders were left holding three cleared-out products and
 * the customer pressing PAY on a ₹431.50 basket got a refusal naming a line the
 * shop had been openly selling an hour earlier. So the catalogue is read
 * alongside the queue, ON THE SAME POLL, and any line whose sku is no longer in
 * it is named on the card with the two ways out. Same poll matters: a stranded
 * verdict drawn from a stale catalogue would be a fresh lie in place of the old
 * silence. When the catalogue cannot be read this screen says nothing about
 * stranding at all, because then it does not know.
 */

const POLL_MS = 5000;

const LABEL: Record<string, string> = {
  new: 'New',
  preparing: 'Packing',
  out_for_delivery: 'Out',
  delivered: 'Delivered',
  cancelled: 'Cancelled',
};

/** What each open pile means, spelled on the group header. */
const GROUP: Array<{ id: string; label: string }> = [
  { id: 'new', label: 'New — decide' },
  { id: 'preparing', label: 'Being packed' },
  { id: 'out_for_delivery', label: 'Out for delivery' },
];

const ACTION: Record<string, string> = {
  preparing: 'START PACKING',
  out_for_delivery: 'SEND IT OUT',
  delivered: 'MARK DELIVERED',
  cancelled: 'CANCEL',
};

/** What the button says WHILE it is doing it. A press has to have an answer. */
const DOING: Record<string, string> = {
  preparing: 'STARTING…',
  out_for_delivery: 'SENDING OUT…',
  delivered: 'MARKING…',
  cancelled: 'CANCELLING…',
};

/**
 * WHERE THE ORDER IS. Not a verdict about money, so not a verdict colour.
 *
 * These were green for delivered and amber for the two in-flight states, and
 * both were borrowing a meaning they do not have. Green on this product means a
 * signature-verified webhook settled a payment — so a DELIVERED pill in green
 * sat one pill away from PAY AT THE DOOR and read as a paid order, on a screen
 * whose whole job is to tell those apart. Amber means the counter abstained; an
 * order being packed is not an abstention.
 *
 * NEW keeps the accent because NEW is the pile that needs a decision, and the
 * accent is this program's mark for its own attention. Every other state is
 * ink: the pile headers already say which pile, and the pill beside it — the
 * payment one — is the pill allowed to carry money's colour.
 */
const TONE: Record<string, PillTone> = {
  new: 'code',
  preparing: 'off',
  out_for_delivery: 'off',
  delivered: 'off',
  cancelled: 'off',
};

/** The states the server itself treats as still live — see `orders_still_wanting`. */
const LIVE = new Set(['new', 'preparing', 'out_for_delivery']);

/** "4 minutes ago", from an ISO timestamp. Never a claim about anything else. */
function ago(iso: string): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return iso;
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(t).toLocaleDateString();
}

/** One order card's worth of waiting, at the shape of the card that is coming. */
function OrderSkeleton() {
  return (
    <article className="od-card" aria-hidden="true">
      <div className="od-head">
        <Skeleton w={64} h={20} radius={999} />
        <Skeleton w={96} h={13} radius={999} />
        <span className="spacer" />
        <Skeleton w={110} h={20} radius={999} />
        <Skeleton w={78} h={20} radius={999} />
      </div>
      <div className="od-body">
        <div className="od-items">
          <Skeleton w={62} h={9} radius={999} />
          <div style={{ marginTop: 10 }}><SkeletonText lines={3} /></div>
        </div>
        <div className="od-dest">
          <Skeleton w={72} h={9} radius={999} />
          <div style={{ marginTop: 10 }}><SkeletonText lines={2} /></div>
        </div>
      </div>
      <div className="od-act">
        <Skeleton w={150} h={40} radius={10} />
      </div>
    </article>
  );
}

export default function Orders() {
  const [book, setBook] = useState<shopapi.OrderBook | null>(null);
  const [refusal, setRefusal] = useState<shopapi.Refusal | null>(null);
  /** A refused move belongs on the order it was refused for, not at the top. */
  const [moveRefusal, setMoveRefusal] =
    useState<{ orderId: string; refusal: shopapi.Refusal } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // Which order (if any) the share sheet is composing a message about.
  const [share, setShare] = useState<ShareTarget | null>(null);
  /** Cancelling is the one move on this screen that cannot be undone. */
  const [confirmCancel, setConfirmCancel] = useState<shopapi.ShopOrder | null>(null);
  const [filter, setFilter] = useState<string>('open');
  const [link, setLink] = useState<shopapi.ShopLink | null>(null);
  const [linkRefusal, setLinkRefusal] = useState<shopapi.Refusal | null>(null);
  const [linkLoading, setLinkLoading] = useState(true);
  const [showQr, setShowQr] = useState(true);
  /** What the share row last did, shown for a moment so a press has an answer. */
  const [shared, setShared] = useState('');

  /** The sku ids this shop can still price. `null` until it has been read. */
  const [priced, setPriced] = useState<ReadonlySet<string> | null>(null);
  const [catRefusal, setCatRefusal] = useState<shopapi.Refusal | null>(null);

  const refresh = useCallback(async () => {
    // BOTH, TOGETHER. The queue and the catalogue are compared against each
    // other below; reading them a poll apart would let this screen call an
    // order stranded because the catalogue it held was five seconds old.
    const [res, cat] = await Promise.all([shopapi.orders(), shopapi.store()]);
    if (res.ok) {
      setBook(res);
      setRefusal(null);
    } else {
      setRefusal(res);
    }
    if (cat.ok) {
      setPriced(new Set(cat.items.map((i) => i.sku_id)));
      setCatRefusal(null);
    } else {
      // Keep whatever was last read, but say the comparison is not current.
      setCatRefusal(cat);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    const id = setInterval(() => { void refresh(); }, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    void (async () => {
      const res = await shopapi.shopLink();
      setLinkLoading(false);
      if (res.ok) { setLink(res); setLinkRefusal(null); } else { setLinkRefusal(res); }
    })();
  }, []);

  const move = useCallback(async (orderId: string, status: string) => {
    setBusy(`${orderId}:${status}`);
    setMoveRefusal(null);
    const res = await shopapi.setStatus(orderId, status);
    setBusy(null);
    if (!res.ok) {
      setMoveRefusal({ orderId, refusal: res });
      // Re-read anyway: the commonest cause of a refused move is that this list
      // is stale, and showing the refusal beside the stale row explains nothing.
      void refresh();
      return;
    }
    void refresh();
  }, [refresh]);

  const shown = useMemo(() => {
    const all = book?.orders ?? [];
    if (filter === 'all') return all;
    if (filter === 'open') {
      return all.filter((o) => o.status !== 'delivered' && o.status !== 'cancelled');
    }
    return all.filter((o) => o.status === filter);
  }, [book, filter]);

  /**
   * The open queue, grouped into the three piles a shopkeeper actually works:
   * decide, pack, deliver. Newest first inside each pile — the server already
   * sorted, and grouping keeps that order. Any other filter is a flat list.
   */
  const groups = useMemo(() => {
    if (filter !== 'open') return null;
    const g = GROUP.map((s) => ({ ...s, orders: [] as shopapi.ShopOrder[] }));
    for (const o of shown) {
      const pile = g.find((x) => x.id === o.status);
      if (pile) pile.orders.push(o);
    }
    return g.filter((x) => x.orders.length > 0);
  }, [shown, filter]);

  /**
   * The lines on this order the shop can no longer price.
   *
   * EMPTY WHEN THE CATALOGUE HAS NOT BEEN READ, and empty for an order that is
   * finished: a delivered order is a record, not a liability, which is the same
   * cut the server makes in `orders_still_wanting`. Nothing here decides that a
   * mint would fail — it reports that the sku the money service will look for is
   * not in the list this shop publishes.
   */
  const strandedLines = useCallback((o: shopapi.ShopOrder): shopapi.OrderLine[] => {
    if (priced === null) return [];
    if (!LIVE.has(o.status)) return [];
    return o.lines.filter((l) => !priced.has(l.sku_id));
  }, [priced]);

  const strandedCount = useMemo(
    () => (book?.orders ?? []).filter((o) => strandedLines(o).length > 0).length,
    [book, strandedLines],
  );

  /**
   * ORDERS THE SHOP HAS HANDED OVER AND NOT BEEN PAID FOR.
   *
   * The default filter is `open`, which hides `delivered` — so on this box
   * three delivered orders that nobody has paid for were invisible unless the
   * shopkeeper thought to change a filter he had no reason to suspect. Goods
   * leaving the shop for nothing is not something a person should have to go
   * looking for, so this is computed across the WHOLE book and shown whatever
   * the filter says.
   *
   * NO TOTAL IS ADDED UP HERE, and that is deliberate rather than an omission.
   * Every rupee on this screen is an integer of paise the server sent, and
   * this browser does not do arithmetic on money — the same rule `poapi.ts`
   * and `offersapi.ts` state in as many words. A figure summed here is a
   * figure no server agreed to. What is honest is the COUNT, which is a count
   * of orders and not of money, and each order's own amount beside it.
   */
  const handedOverUnpaid = useMemo(
    () => (book?.orders ?? []).filter((o) => o.status === 'delivered' && !o.payment.paid),
    [book],
  );

  const counts = book?.counts ?? {};
  const open = (counts.new ?? 0) + (counts.preparing ?? 0) + (counts.out_for_delivery ?? 0);

  /** One order, drawn the same way in every pile. */
  const renderOrder = (o: shopapi.ShopOrder) => {
    const next = book?.next_status[o.status] ?? [];
    const forward = next.filter((s) => s !== 'cancelled');
    const canCancel = next.includes('cancelled');
    const itemCount = o.lines.reduce((n, l) => n + l.qty, 0);
    // ONE press at a time per order. Disabling only the pressed button left
    // CANCEL live while START PACKING was in flight, and the two moves would
    // have raced each other through the same order file.
    const working = busy !== null && busy.startsWith(`${o.order_id}:`);
    const stranded = strandedLines(o);
    const refusedHere = moveRefusal?.orderId === o.order_id ? moveRefusal.refusal : null;
    return (
      <article className={o.status === 'new' ? 'od-card fresh' : 'od-card'} key={o.order_id}>
        <header className="od-head">
          <Pill tone={TONE[o.status] ?? 'off'} dot={o.status === 'new'}>
            {LABEL[o.status] ?? o.status}
          </Pill>
          <span className="who">{o.customer.name}</span>
          <span className="when">{ago(o.at)}</span>
          <span className="spacer" />
          {/* FOUR STATES, BECAUSE THERE ARE FOUR.
              PAID is green — a signature-verified webhook settled it, which is
              the only thing green means here. A minted link that has not been
              paid is quiet. A mint the money service REFUSED is amber: the
              counter abstained from producing a payable string, which is
              exactly what amber means on every other screen, and the reason is
              its own word — `amber_in_basket`, `scan_total_disagreement` —
              printed rather than paraphrased.

              THE FOURTH BRANCH USED TO SAY "PAY AT THE DOOR". `minted_at` is
              null both when nobody has pressed pay and when the mint was
              refused, so two refused orders on this box — ₹431.50 out for
              delivery and ₹35.00 already delivered — advertised a payment
              method this product does not have. There is no cash path from a
              storefront order to the drawer. The honest fallback is that it is
              not paid, and nothing more. */}
          <Pill
            tone={o.payment.paid ? 'ok' : o.payment.last_refusal ? 'amb' : 'off'}
            dot={o.payment.paid}
          >
            {o.payment.paid
              ? 'PAID'
              : o.payment.minted_at
                ? 'LINK SENT · NOT PAID'
                : o.payment.last_refusal
                  ? `NO LINK · ${o.payment.last_refusal.reason}`
                  : 'NOT PAID'}
          </Pill>
          <span className="od-amt tnum">{rupees(o.total_paise)}</span>
        </header>

        <div className="od-body">
          <div className="od-items">
            <div className="od-k">{itemCount} item{itemCount === 1 ? '' : 's'}</div>
            {o.lines.map((l) => (
              <div className="bill-line" key={l.sku_id}>
                <span className="nm">{l.name}</span>
                <span className="qty">×{l.qty}</span>
                <span className="amt tnum">{rupees(l.line_paise)}</span>
              </div>
            ))}
          </div>
          <div className="od-dest">
            <div className="od-k">Deliver to</div>
            <address>{o.customer.address}</address>
            <a className="phone" href={`tel:${o.customer.phone.replace(/[^\d+]/g, '')}`}>
              {o.customer.phone}
            </a>
            <span className="oid mono">{o.order_id}</span>
          </div>
        </div>

        {/* AMBER, not red. Nothing has refused yet — this counter is saying it
            cannot price a line, which is the abstain colour everywhere else on
            this product. `amber_in_basket` is the money service's own name for
            the refusal that follows, and it is printed rather than paraphrased
            so a shopkeeper reading the customer's screen recognises it. */}
        {stranded.length > 0 && (
          <div style={{ padding: '0 var(--s4) var(--s4)' }}>
            <Verdict
              tone="amber"
              title={stranded.length === 1
                ? `This order cannot be paid: ${stranded[0]!.name} has no price any more`
                : `This order cannot be paid: ${stranded.length} lines have no price any more`}
            >
              {stranded.map((l) => l.name).join(', ')}
              {stranded.length === 1 ? ' is' : ' are'} no longer in this shop's catalogue.
              The money service prices every line from its own book, so pressing PAY on this
              order is refused with <span className="mono">amber_in_basket</span> naming
              {stranded.length === 1 ? ' that product' : ' those products'}.
              <br />
              Two ways out: teach{' '}
              {stranded.map((l) => l.sku_id).join(', ')} again with a price on the Products
              screen and this order becomes payable, or cancel the order below. Cash at the
              door still works — it is the payment LINK that cannot be minted.
            </Verdict>
          </div>
        )}

        {/* WHY NO LINK EXISTS, ON THE ORDER IT DOES NOT EXIST FOR, in the money
            service's own words.

            Only when this page is not already saying it: `stranded` above
            explains `amber_in_basket` from the catalogue side, in more useful
            detail and with the two ways out, so repeating it here would be the
            same refusal twice on one card. Everything else — a scan total that
            disagrees, a gateway that would not answer — has had nowhere to be
            said at all.

            Optional field, checked before use: an order the storefront has not
            yet stamped simply shows the quiet "NOT PAID" pill above and no
            block here, which is what every order on the box does today. */}
        {o.payment.last_refusal && stranded.length === 0 && (
          <div style={{ padding: '0 var(--s4) var(--s4)' }}>
            <Verdict tone="amber" title="No payment link could be minted for this order">
              The money service refused it and said{' '}
              <span className="mono">{o.payment.last_refusal.reason}</span>.
              {o.payment.last_refusal.detail && (
                <>
                  <br />
                  <span className="mono">{o.payment.last_refusal.detail}</span>
                </>
              )}
              <br />
              That was the most recent attempt, {ago(o.payment.last_refusal.at)}. Nothing has been
              paid and there is no link to send. This counter has no cash path for a storefront
              order, so until the reason above is fixed and PAY is pressed again, the money for
              this order has not been collected by anything.
            </Verdict>
          </div>
        )}

        {/* The reason sits on the order it was refused for. Shown at the top of
            the queue it described a row four cards away. */}
        {refusedHere && (
          <div style={{ padding: '0 var(--s4) var(--s4)' }}>
            <Refusal
              reason={refusedHere.reason}
              detail={refusedHere.detail}
              hint="Nothing was changed. The list has been re-read, so the buttons below are current."
            />
          </div>
        )}

        {/* An order moving is the moment the customer wants to hear about it,
            and the phone is already on the card. The sheet composes the message
            on the SERVER and shows it before anything is sent — this button
            opens a draft, it does not send one. */}
        <div className="od-act">
          <button className="btn sm od-send" onClick={() => setShare({ kind: 'order', orderId: o.order_id })}>
            SEND UPDATE
          </button>
        </div>

        {next.length > 0 && (
          <div className="od-act">
            {forward.map((s) => (
              <button
                key={s}
                className="btn primary od-next"
                disabled={working}
                aria-busy={busy === `${o.order_id}:${s}` || undefined}
                title={working && busy !== `${o.order_id}:${s}`
                  ? 'Another change to this order is still going through.'
                  : undefined}
                onClick={() => void move(o.order_id, s)}
              >
                {busy === `${o.order_id}:${s}` ? (DOING[s] ?? '…') : (ACTION[s] ?? s)}
              </button>
            ))}
            {canCancel && (
              <button
                className="btn sm danger od-cancel"
                disabled={working}
                aria-busy={busy === `${o.order_id}:cancelled` || undefined}
                title={working && busy !== `${o.order_id}:cancelled`
                  ? 'Another change to this order is still going through.'
                  : undefined}
                onClick={() => setConfirmCancel(o)}
              >
                {busy === `${o.order_id}:cancelled` ? DOING.cancelled : ACTION.cancelled}
              </button>
            )}
          </div>
        )}
      </article>
    );
  };

  return (
    <>
      <div className="page-head">
        <h1>Orders</h1>
        <p>
          What customers sent from their own phones. The newest is at the top and this
          list refreshes itself, so a phone order arrives here without anyone pressing
          anything.
        </p>
      </div>

      <div className="od-grid">
        {/* `od-queue` only pins this column beside the taller rail on a wide
            screen — see storefront.css. No layout of its own. */}
        <div className="stack od-queue">
          <Card
            icon={<IcoParcel />}
            title="The queue"
            aside={
              <div className="row">
                <Pill tone={open > 0 ? 'code' : 'off'} dot={open > 0}>{open} open</Pill>
                {/* The count rides beside the filter that hides them, so the
                    tab a shopkeeper never presses announces itself. Amber: it
                    is money the counter did not collect, not a fault. */}
                {handedOverUnpaid.length > 0 && (
                  <Pill tone="amb">{handedOverUnpaid.length} delivered · unpaid</Pill>
                )}
                <div className="seg" role="group">
                  {(['open', 'all', 'delivered', 'cancelled'] as const).map((f) => (
                    <button
                      key={f}
                      type="button"
                      aria-pressed={filter === f}
                      onClick={() => setFilter(f)}
                    >
                      {f === 'open' ? 'Open' : f === 'all' ? 'All' : LABEL[f]}
                    </button>
                  ))}
                </div>
              </div>
            }
          >
            {refusal && (
              <Refusal
                reason={refusal.reason}
                detail={refusal.detail}
                hint="The list below may be out of date until this is fixed."
                action={<Button size="sm" onClick={() => void refresh()}>READ IT AGAIN</Button>}
              />
            )}
            {/* Without the catalogue this screen cannot tell a stranded order
                from a payable one, so it says that rather than staying quiet
                and letting the absence of a warning read as an all-clear. */}
            {catRefusal && (
              <Refusal
                reason={catRefusal.reason}
                detail={catRefusal.detail}
                hint={priced === null
                  ? 'Until the catalogue is read, this screen cannot say whether an order still has a price for every line.'
                  : 'The stranded-order warnings below were worked out from a catalogue read before this happened.'}
                action={<Button size="sm" onClick={() => void refresh()}>READ IT AGAIN</Button>}
              />
            )}
            {/* The refused move whose order is no longer in this view. It still
                happened, so it is still said — just with nowhere to sit. */}
            {moveRefusal && !shown.some((o) => o.order_id === moveRefusal.orderId) && (
              <Refusal
                reason={moveRefusal.refusal.reason}
                detail={moveRefusal.refusal.detail}
                hint={`Nothing was changed. Order ${moveRefusal.orderId} is not in this view.`}
              />
            )}
            {/* GOODS THAT LEFT THE SHOP AND WERE NOT PAID FOR, said in every
                filter including the one that hides them.

                Each order carries its OWN amount, straight off the server.
                There is no line here adding them together: money is integer
                paise and this browser does not do arithmetic on it, so the
                count is of orders and the rupees are quoted one at a time. */}
            {handedOverUnpaid.length > 0 && (
              <Verdict
                tone="amber"
                title={handedOverUnpaid.length === 1
                  ? 'One delivered order has not been paid for'
                  : `${handedOverUnpaid.length} delivered orders have not been paid for`}
              >
                The goods have gone and no signature-verified webhook has settled any of them.
                A storefront order has no cash path to this counter&rsquo;s drawer, so nothing
                here is waiting to be reconciled — it is simply uncollected.
                {/* A SPAN, NOT A DIV — `Verdict` puts its children inside a
                    <p>, and a block element there is invalid HTML: the browser
                    closes the paragraph early and lifts the list clean out of
                    the amber box it belongs to. The same trap `Refusal` records
                    in ui.tsx. `display: flex` keeps the layout. */}
                <span
                  style={{
                    display: 'flex', flexDirection: 'column', gap: 6,
                    margin: '10px 0', padding: '10px 0',
                    borderTop: '1px solid var(--amber-line)',
                    borderBottom: '1px solid var(--amber-line)',
                  }}
                >
                  {handedOverUnpaid.map((o) => (
                    <span
                      key={o.order_id}
                      style={{
                        display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap',
                      }}
                    >
                      <b style={{ color: 'var(--navy-900)' }}>{o.customer.name}</b>
                      <span className="mono" style={{ fontSize: 'var(--t-micro)' }}>{o.order_id}</span>
                      <b className="tnum" style={{ marginLeft: 'auto' }}>{rupees(o.total_paise)}</b>
                      <span style={{ fontSize: 'var(--t-micro)', width: '100%' }}>
                        {o.payment.last_refusal
                          ? `no link · ${o.payment.last_refusal.reason}`
                          : o.payment.minted_at
                            ? 'link sent, never paid'
                            : 'no link was ever minted'}
                      </span>
                    </span>
                  ))}
                </span>
                {filter === 'open' && (
                  <>
                    They are not in this view — press <b>All</b> or <b>Delivered</b> above to open
                    them.
                  </>
                )}
              </Verdict>
            )}

            {strandedCount > 0 && (
              <Verdict
                tone="amber"
                title={strandedCount === 1
                  ? 'One open order is holding a product this shop can no longer price'
                  : `${strandedCount} open orders are holding a product this shop can no longer price`}
              >
                They are marked below. A deleted product takes its price with it; the money
                service prices every line from its own book and will not find the sku, so no
                payment link can be minted for them.
              </Verdict>
            )}

            {book === null ? (
              refusal ? null : (
                <div className="od-list" role="status" aria-label="Reading the queue">
                  <OrderSkeleton />
                  <OrderSkeleton />
                </div>
              )
            ) : shown.length === 0 ? (
              book.count === 0 ? (
                <Empty
                  title="No orders yet"
                  action={
                    <Button
                      size="sm"
                      variant="primary"
                      onClick={() => {
                        setShowQr(true);
                        document.querySelector('.od-sticker')
                          ?.scrollIntoView({ block: 'center' });
                      }}
                    >
                      SHOW THE SHOP'S CODE
                    </Button>
                  }
                >
                  Nothing has been ordered from the shutter code. Print the code and tape it
                  where a customer waiting at the counter can photograph it — that is the only
                  way an order reaches this screen.
                </Empty>
              ) : filter === 'open' ? (
                <Empty
                  title="Nothing waiting"
                  action={<Button size="sm" onClick={() => setFilter('all')}>SHOW ALL ORDERS</Button>}
                >
                  Every order has been delivered or cancelled. A new one appears here on its
                  own within a few seconds of a customer sending it.
                </Empty>
              ) : (
                <Empty
                  title={`No ${LABEL[filter]?.toLowerCase() ?? filter} orders`}
                  action={<Button size="sm" onClick={() => setFilter('all')}>SHOW ALL ORDERS</Button>}
                >
                  This shop has {book.count} order{book.count === 1 ? '' : 's'} in total; none of
                  them is in this view.
                </Empty>
              )
            ) : groups ? (
              groups.map((g) => (
                <div key={g.id}>
                  <div className="od-sec">
                    <span className="t">{g.label}</span>
                    <span className="n">{g.orders.length}</span>
                    <span className="rule" />
                  </div>
                  <div className="od-list">{g.orders.map(renderOrder)}</div>
                </div>
              ))
            ) : (
              <div className="od-list">{shown.map(renderOrder)}</div>
            )}
          </Card>
        </div>

        <div className="stack">
          <Card
            title="Your shop's code"
            aside={
              <button className="btn ghost sm" onClick={() => setShowQr((v) => !v)}>
                {showQr ? 'HIDE' : 'SHOW'}
              </button>
            }
          >
            <p className="hint" style={{ marginTop: 0 }}>
              Print this and stick it on the shutter. A customer photographs it with the
              phone in their hand and the shop opens — no app, no install.
            </p>

            {/* The address this code carries is a fetch like any other, and it
                used to fail silently: the share row simply never appeared and
                nothing on the screen said why. */}
            {linkLoading && (
              <div role="status" aria-label="Reading this shop's address">
                <Skeleton w="70%" h={13} radius={999} />
                <div style={{ marginTop: 10 }}><Skeleton w="46%" h={13} radius={999} /></div>
              </div>
            )}

            {linkRefusal && (
              <Refusal
                reason={linkRefusal.reason}
                detail={linkRefusal.detail}
                hint="The code below still opens this shop from this machine. What could not be read is the address to print under it."
              />
            )}

            {link && !link.reachable_from_a_phone && (
              <Verdict tone="amber" title="A phone cannot open this address">
                {link.note}
              </Verdict>
            )}

            {showQr && (
              <div className="od-sticker">
                <span className="brand">Scan to shop</span>
                <h3>Order from this shop on your phone</h3>
                {/* Not a payment code. It carries this server's own address and
                    nothing else — the endpoint refuses to encode anything that
                    looks like a payment target. */}
                <img src={shopapi.shopQrUrl()} alt="A QR code that opens this shop" />
                {link && <span className="url">{link.url}</span>}
                <span className="sub">
                  The dashed edge is the scissor line. Tape it at eye level, where a
                  person waiting at the counter stands.
                </span>
              </div>
            )}

            {/* SENDING THE SHOP TO SOMEBODY. The link is this server's own
                address and nothing else — no amount, no order, no payment
                target — so it can be sent anywhere without carrying money.
                Every action here is disabled while the address is one a phone
                cannot open, because sending a loopback link to a customer is
                sending them nothing — and each disabled button says so on
                itself rather than leaving the reason four lines up the card. */}
            {link && (
              <div className="od-share">
                <button
                  className="btn sm primary"
                  disabled={!link.reachable_from_a_phone}
                  title={link.reachable_from_a_phone
                    ? undefined
                    : 'This address only works on this machine, so there is nothing worth sending.'}
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(link.url);
                      setShared('Link copied.');
                    } catch {
                      setShared('This browser would not let the page copy. Long-press the address below.');
                    }
                    setTimeout(() => setShared(''), 4000);
                  }}
                >
                  COPY THE LINK
                </button>

                {typeof navigator !== 'undefined' && 'share' in navigator && (
                  <button
                    className="btn sm"
                    disabled={!link.reachable_from_a_phone}
                    title={link.reachable_from_a_phone
                      ? undefined
                      : 'This address only works on this machine, so there is nothing worth sending.'}
                    onClick={async () => {
                      try {
                        await navigator.share({
                          title: 'Order from this shop',
                          text: 'Our shop is open on your phone — no app, no install.',
                          url: link.url,
                        });
                        setShared('Sent.');
                      } catch {
                        // A cancelled share is not a failure and says nothing.
                        setShared('');
                      }
                      setTimeout(() => setShared(''), 4000);
                    }}
                  >
                    SEND IT
                  </button>
                )}

                <button
                  className="btn sm"
                  onClick={async () => {
                    try {
                      const r = await fetch(shopapi.shopQrUrl());
                      if (!r.ok) throw new Error(String(r.status));
                      const blob = await r.blob();
                      const href = URL.createObjectURL(blob);
                      const a = document.createElement('a');
                      a.href = href;
                      a.download = 'shop-code.png';
                      a.click();
                      URL.revokeObjectURL(href);
                      setShared('Saved as shop-code.png.');
                    } catch {
                      setShared('The code could not be saved. It is still on screen to photograph.');
                    }
                    setTimeout(() => setShared(''), 4000);
                  }}
                >
                  SAVE THE CODE
                </button>
              </div>
            )}
            {shared && <p className="hint od-shared" aria-live="polite">{shared}</p>}

            {link && (
              <KV k="opens">
                <a className="mono" href={link.url} target="_blank" rel="noreferrer">{link.url}</a>
              </KV>
            )}
            <KV k="carries">this shop's address — never a payment</KV>
            <p className="hint">
              Open this till at the laptop's own address on the shop's wifi before you
              print, so the code points somewhere a phone can reach.
            </p>
          </Card>

          <Card title="What the customer can and cannot do" tight>
            <KV k="picks">products and how many</KV>
            <KV k="never sets">a price — the shop re-prices every order</KV>
            <KV k="pays">on the gateway's own page, or at the door</KV>
            <p className="hint">
              A phone that asks for a cheaper total is refused, not obliged. Every order
              and every status change above is on a hash-chained log beside the
              catalogue.
            </p>
          </Card>
        </div>
      </div>

      {/* CANCELLING IS THE END OF AN ORDER. `cancelled` has no legal move out of
          it — the server publishes an empty `next_status` for it — so this is
          the one press on this screen a shopkeeper cannot take back, and it asks
          first. Everything else here moves an order forward and can be moved on
          again. */}
      <Modal
        open={confirmCancel !== null}
        onClose={() => setConfirmCancel(null)}
        title="Cancel this order?"
        sub={confirmCancel ? `${confirmCancel.customer.name} · ${rupees(confirmCancel.total_paise)}` : undefined}
        size="narrow"
        foot={
          <>
            <Button variant="ghost" onClick={() => setConfirmCancel(null)}>KEEP IT</Button>
            <Button
              variant="danger"
              onClick={() => {
                const o = confirmCancel;
                setConfirmCancel(null);
                if (o) void move(o.order_id, 'cancelled');
              }}
            >
              CANCEL THE ORDER
            </Button>
          </>
        }
      >
        {confirmCancel && (
          <>
            <p>
              A cancelled order is the end of the line: it cannot be moved back to packing or
              delivery, and this screen will keep it only as a record.
            </p>
            <div style={{ marginTop: 12 }}>
              {confirmCancel.lines.map((l) => (
                <div className="bill-line" key={l.sku_id}>
                  <span className="nm">{l.name}</span>
                  <span className="qty">×{l.qty}</span>
                  <span className="amt tnum">{rupees(l.line_paise)}</span>
                </div>
              ))}
            </div>
            <p className="hint">
              Order <span className="mono">{confirmCancel.order_id}</span> for{' '}
              {confirmCancel.customer.name} on {confirmCancel.customer.phone}. Tell them
              yourself with SEND UPDATE — cancelling does not message anybody.
            </p>
            {strandedLines(confirmCancel).length > 0 && (
              <p className="hint">
                This is one of the orders that can no longer be paid, because{' '}
                {strandedLines(confirmCancel).map((l) => l.name).join(', ')} left the
                catalogue. Cancelling is one of the two ways out of that; teaching the
                product again with a price is the other.
              </p>
            )}
          </>
        )}
      </Modal>

      {/* One sheet for the screen, not one per card: only one message is being
          composed at a time, and a sheet per order is a sheet per order to
          keep in sync. */}
      <ShareSheet
        open={share !== null}
        onClose={() => setShare(null)}
        target={share ?? { kind: 'reorder' }}
      />
    </>
  );
}
