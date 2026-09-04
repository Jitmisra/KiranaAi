import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import * as shopapi from '../lib/shopapi';
import { storeShop, type StoreShop } from '../lib/adminapi';
import { rupees, totalPaise } from '../lib/money';
import { Card, KV, Pill, Verdict, Empty, Field, Refusal, LoadingCard, Working } from '../components/ui';
import '../styles/storefront.css';
import '../styles/shopface.css';

/**
 * The shop, read on a customer's phone.
 *
 * This is the only screen in this product that a customer ever sees, and it is
 * the most-shared surface the product has: it is what a stranger gets when they
 * photograph the shutter QR. So it looks like a shop — a grid of products with
 * the photograph large, a search box because nobody scrolls four hundred
 * packets, and a basket bar that rises when the first item lands. Every control
 * is at least 44 px, prices are tabular, and the word "sku" appears nowhere.
 *
 * THE PAGE PROPOSES; THE SHOP DECIDES. Every total drawn here is arithmetic the
 * server will redo from its own catalogue before it accepts the order — the
 * basket sends sku ids, whole-number quantities and a checksum the server is
 * free to reject. If a price changed while this page was open, the order is
 * refused and this screen says so, rather than the shop silently honouring a
 * number a browser was holding.
 *
 * A DISCOUNT IS THE SERVER'S FACT. `price_paise` is what the shop will charge,
 * offers already applied; `marked_paise`/`off_paise` ride along only while an
 * offer is active. This page strikes one number through and adds nothing —
 * it never computes a discount of its own.
 */

/** Which of this page's three screens is open. Exported for the customer's
    shell, which OWNS this choice when there is one — see `StorefrontNav`. */
export type Stage = 'browse' | 'checkout' | 'order';

/**
 * THE CUSTOMER'S SHELL, when the person on this page has one.
 *
 * Absent for the SHOPKEEPER previewing their own shop front from inside the
 * counter's chrome, and this page then behaves exactly as it always has: it
 * owns its own screen, reads who this browser is for itself, and restores a
 * remembered order on mount.
 *
 * Present for a customer (components/CustomerShell.tsx). Then the shell owns
 * WHICH screen is open, because it has a SHOP tab and this page's own restore
 * effect would otherwise yank a customer who tapped it back to an order they
 * had finished reading.
 */
export interface StorefrontNav {
  /** The screen the shell wants open. */
  stage: Stage;
  /** This page asking for another: CHECKOUT, BACK, an order accepted. */
  setStage: (s: Stage) => void;
  /** Whole packets in the basket, for the shell's tab badge. */
  onBasket: (count: number) => void;
  /** Who this phone is, read ONCE by the shell so the two do not each ask. */
  me: shopapi.CustomerMe | null;
  /** Re-read it — a `?k=` link has just made this phone somebody. */
  refreshMe: () => void;
}

/** The delivery journey, in the order it happens. `cancelled` is not a step. */
const JOURNEY: Array<{ id: string; label: string }> = [
  { id: 'new', label: 'Ordered' },
  { id: 'preparing', label: 'Being packed' },
  { id: 'out_for_delivery', label: 'On the way' },
  { id: 'delivered', label: 'Delivered' },
];

/** One word for each state, for a list of orders. Derived from `JOURNEY` so
    the rail and the list can never drift apart; `cancelled` is not on the
    rail and is named here. */
export const STATUS_LABEL: Record<string, string> = {
  ...Object.fromEntries(JOURNEY.map((s) => [s.id, s.label])),
  cancelled: 'Cancelled',
};

export const STATUS_LINE: Record<string, string> = {
  new: 'The shop has your order. Nothing has been charged yet.',
  preparing: 'Your order is being packed.',
  out_for_delivery: 'Your order has left the shop.',
  delivered: 'Delivered. Thank you.',
  cancelled: 'This order was cancelled by the shop.',
};

/** How often to ask whether anything changed while the customer waits. */
const POLL_MS = 4000;

/**
 * Which box on the delivery form each server refusal is ABOUT.
 *
 * The server already names its refusals precisely; this is the only thing the
 * page has to add — putting the name next to the box it concerns. Left as one
 * block at the bottom of the card, "customer_phone_not_a_number" makes the
 * customer read three fields to work out which one it means, on a phone, with
 * the keyboard covering half the form.
 *
 * A reason NOT in this table still renders in full below the fields, so a new
 * refusal the server learns to make is never swallowed by this map.
 */
const FIELD_OF_REFUSAL: Record<string, 'name' | 'phone' | 'address'> = {
  customer_name_missing: 'name',
  customer_phone_missing: 'phone',
  customer_phone_not_a_number: 'phone',
  delivery_address_missing: 'address',
  delivery_address_too_short: 'address',
};

/**
 * The basket bar, rendered ONTO <body> and not into the page tree.
 *
 * Not a nicety — a bug fix, found by looking at the running page. app.css
 * animates every route root with `routeIn`, fill-mode `both`, and a filled
 * transform animation leaves the root reporting an identity matrix forever.
 * An ancestor with any transform becomes the containing block for
 * `position: fixed`, so a bar drawn inside it pinned itself to the bottom of
 * the DOCUMENT — present in the DOM, styled, and simply not on screen until
 * the customer had scrolled past the last product. The portal lifts the bar
 * out of that subtree so `fixed` means the viewport again.
 */
function BasketBar({ children }: { children: ReactNode }) {
  return createPortal(
    <div className="sf-bar" role="region" aria-label="Your basket">{children}</div>,
    document.body,
  );
}

/* --------------------------------------------------------------------------
   THE PLATE — the shop's name, address and photograph, and the verdict on the
   link the customer arrived with.

   Until this existed the header said "The shop" for every counter on earth,
   and the shutter code encoded `/#/shop` for every counter on earth: peel one
   shop's sticker off, stick it on another, and the second shop's catalogue
   opened under the first shop's name with nothing on screen to say so. The
   link now carries the shop's own handle, `?s=<slug>`, and this header asks
   the counter — `GET /store/shop`, open, four public fields — whether that
   handle is its own. When it is not, the customer is told BY NAME which shop
   they have actually reached, above the grid, before they put anything in a
   basket. The grid below is still this counter's own catalogue; what is
   refused is the pretence that it is the other shop's.

   THE HANDLE IS READ SYNCHRONOUSLY, in the first render, before any effect
   runs. The effect above that spends a `?k=` token rewrites the hash without
   its query, and reading `?s=` from an effect would race it.
   -------------------------------------------------------------------------- */

export function slugFromHash(): string | null {
  const hash = window.location.hash;                   // "#/shop?s=..."
  const qs = hash.includes('?') ? hash.slice(hash.indexOf('?') + 1) : '';
  const s = new URLSearchParams(qs).get('s');
  return s && s.trim() ? s.trim() : null;
}

function ShopPlate() {
  const [slug, setSlug] = useState<string | null>(() => slugFromHash());
  const [face, setFace] = useState<StoreShop | 'refused' | null>(null);

  // A SECOND SHOP'S LINK OPENED IN THE SAME TAB is a hash change on a route
  // that is already mounted: the router does not remount this page, so the
  // slug read at mount would stay the first shop's and the verdict would be
  // about the wrong link. Measured, not supposed — driving `#/shop?s=own`
  // and then `#/shop?s=other` in one tab left the notice absent.
  useEffect(() => {
    const onHash = () => setSlug(slugFromHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  useEffect(() => {
    let live = true;
    setFace(null);
    void (async () => {
      const r = await storeShop(slug);
      if (live) setFace(r.ok ? r : 'refused');
    })();
    return () => { live = false; };
  }, [slug]);

  const known = face !== null && face !== 'refused' ? face : null;
  const loading = face === null;
  // A counter that cannot say its name is still a shop; "The shop" is the
  // honest fallback, and it is what this header said for everyone before.
  const name = known?.name ?? 'The shop';
  const initial = (known?.name ?? 'S').slice(0, 1).toUpperCase();

  return (
    <>
      {known && known.link === 'other' && (
        <div className="shf-wrong" role="alert" data-testid="wrong-shop">
          <span className="shf-wrong-ico" aria-hidden="true">!</span>
          <div>
            <b>This link was made for a different shop</b>
            <p>
              The code you scanned carries <code>{known.requested}</code>, which is not this
              shop&rsquo;s handle{known.slug ? <> (<code>{known.slug}</code>)</> : null}. You have
              reached <strong>{known.name ?? 'a counter that has no name yet'}</strong> — the
              prices below are its own, not the other shop&rsquo;s. If you meant the other shop,
              ask them for their sticker.
            </p>
          </div>
        </div>
      )}

      <header className="shf-plate" data-testid="shop-plate">
        <div className="shf-plate-ph">
          {known?.photo_url ? (
            <img src={known.photo_url} alt={`The front of ${name}`} />
          ) : (
            <span className="initial" aria-hidden="true">{loading ? '' : initial}</span>
          )}
        </div>
        <div className="shf-plate-txt">
          <h1 data-testid="shop-name">
            {loading
              ? <span className="skel" style={{ display: 'inline-block', width: 180, height: 22, borderRadius: 8 }} aria-label="Reading the shop’s name" />
              : name}
          </h1>
          {known?.address && <p className="shf-plate-addr">{known.address}</p>}
          <p className="shf-plate-line">
            Everything the counter has been taught, at the price the counter charges.
            Add what you need and give an address — the shopkeeper packs it.
          </p>
        </div>
      </header>
    </>
  );
}

export default function Shop({ nav }: { nav?: StorefrontNav }) {
  const [items, setItems] = useState<shopapi.StoreItem[] | null>(null);
  const [loadRefusal, setLoadRefusal] = useState<shopapi.Refusal | null>(null);
  const [cart, setCart] = useState<Record<string, number>>(() => shopapi.loadCart());
  const [q, setQ] = useState('');

  /**
   * WHICH SCREEN IS OPEN, and who decides.
   *
   * Held in a ref as well as read as a prop because half the callbacks below
   * are `useCallback`s whose dependency lists are load-bearing: `place`
   * re-created on every keystroke of the address box would rebuild the whole
   * checkout form. `setStage` is therefore stable in both modes, and reads
   * the shell through the ref rather than closing over a prop that changes.
   */
  const navRef = useRef<StorefrontNav | undefined>(nav);
  navRef.current = nav;
  const [ownStage, setOwnStage] = useState<Stage>('browse');
  const stage = nav ? nav.stage : ownStage;
  const setStage = useCallback((s: Stage) => {
    const n = navRef.current;
    if (n) n.setStage(s); else setOwnStage(s);
  }, []);

  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [address, setAddress] = useState('');

  const [placing, setPlacing] = useState(false);
  const [orderRefusal, setOrderRefusal] = useState<shopapi.Refusal | null>(null);
  const [order, setOrder] = useState<shopapi.CustomerOrder | null>(null);

  const [paying, setPaying] = useState(false);
  const [payLink, setPayLink] = useState<string | null>(null);
  const [payRefusal, setPayRefusal] = useState<shopapi.Refusal | null>(null);

  /**
   * WHO IS LOOKING AT THIS PAGE.
   *
   * Two separate facts, and they are not opposites — a shopkeeper's browser can
   * hold both a counter session and a customer session at once.
   *
   *   `me.previewing`  a SHOPKEEPER is signed in here, so this is the shop front
   *                    being previewed and the server will refuse an order from
   *                    it. Read at mount so the page can say so before anybody
   *                    fills in a delivery address, which is where the old
   *                    behaviour dropped it — after the form, as a refusal.
   *   `me.customer`    the customer this browser has identified as, if any.
   *
   * READ BY THE SHELL WHEN THERE IS ONE, and handed down. Two reasons, and
   * neither is tidiness: two components each asking `/store/customer/me` is
   * two requests for one fact, and — the one that shows — a customer who signs
   * in on MY ORDERS would still be a stranger to the delivery form until this
   * page happened to remount.
   */
  const [ownMe, setOwnMe] = useState<shopapi.CustomerMe | null>(null);
  const me = nav ? nav.me : ownMe;
  const [relinking, setRelinking] = useState(false);
  /** What a link made out to one person said, once it has been spent. */
  const [claimed, setClaimed] = useState<{ name: string } | shopapi.Refusal | null>(null);

  /**
   * A customer coming back to an order they already placed sees a LOADING
   * SCREEN, not the shop.
   *
   * Seeded from `lastOrder()` synchronously, on the first render, because the
   * effect that fetches the order runs after paint: seeded `false` and flipped
   * in the effect, the returning customer got a flash of the product grid with
   * an empty basket before their order replaced it — which reads as "the shop
   * lost my order" for as long as it is on screen.
   */
  const [restoring, setRestoring] = useState<boolean>(() => shopapi.lastOrder() !== null);

  /**
   * Products that were in the basket and are not in the catalogue any more.
   *
   * Held as state rather than derived, because the basket is PRUNED the moment
   * they are noticed. Derived, the message said "removed from the basket" while
   * the line sat in `localStorage` telling the next page load the same thing
   * forever.
   */
  const [droppedNames, setDroppedNames] = useState<string[]>([]);

  /**
   * THE SHELF, AND WHAT IT DID TO THE BASKET.
   *
   * `stockInfo`  the catalogue's own word on whether its figures mean anything
   *              and how they are derived — printed, not paraphrased.
   * `capped`     lines this page cut down when the catalogue arrived, because
   *              a basket kept in localStorage can outlive the shelf: two in
   *              the basket overnight, one left by morning.
   * `stockFix`   what the SHOP said when it refused the order, and what this
   *              page did about it. The server names every short line with
   *              the exact number it will accept, so the basket is set to
   *              those numbers and the customer is told, line by line — a
   *              refusal that only said "not enough" would leave them guessing
   *              which of six things to remove.
   */
  const [stockInfo, setStockInfo] = useState<shopapi.Store['stock'] | null>(null);
  const [capped, setCapped] = useState<shopapi.CartChange[]>([]);
  const [stockFix, setStockFix] = useState<{ lines: shopapi.ShortLine[]; changes: shopapi.CartChange[] } | null>(null);

  const listRef = useRef<HTMLDivElement>(null);

  /* ---- the catalogue ---------------------------------------------------- */

  /**
   * A LINK MADE OUT TO ONE PERSON, spent on arrival.
   *
   * The shopkeeper sends `.../#/shop?k=<token>` to a regular. Opening it makes
   * this phone that customer, so they never type their name and number again.
   *
   * THE TOKEN IS REMOVED FROM THE ADDRESS BAR IMMEDIATELY, before anything
   * else renders. It is a bearer credential: leaving it in the URL leaves it in
   * this browser's history, in the next page's `Referer`, and in whatever the
   * customer pastes when they send the shop's address to a friend. It is spent
   * by then anyway — it works once — but a dead credential in a screenshot is
   * still a habit worth not teaching.
   */
  useEffect(() => {
    const hash = window.location.hash;                 // "#/shop?k=..."
    const qs = hash.includes('?') ? hash.slice(hash.indexOf('?') + 1) : '';
    const token = new URLSearchParams(qs).get('k');
    if (!token) return;
    // Strip it first, so a failed claim does not leave it behind either.
    // ONLY `k`. This used to cut the hash at the first `?`, which also threw
    // away `?s=<slug>` — the shop's own link identity, which another effect
    // reads. A customer link is minted without `s` today, so nothing broke;
    // it would have the day the two were combined.
    const rest = new URLSearchParams(qs); rest.delete('k');
    const kept = rest.toString();
    window.history.replaceState(null, '', (hash.slice(0, hash.indexOf('?')) || '#/shop') + (kept ? `?${kept}` : ''));
    void (async () => {
      const r = await shopapi.claimCustomer(token);
      if (r.ok) {
        setClaimed({ name: r.customer.name });
        // The shell owns `me` when there is one, so ask it to re-read rather
        // than patching a copy this page does not own — the bar's own "who is
        // this phone" chip has to change too, and it reads the shell's.
        setOwnMe((m) => (m ? { ...m, customer: r.customer } : m));
        navRef.current?.refreshMe();
      } else {
        setClaimed(r);
      }
    })();
  }, []);

  /**
   * The catalogue, and the basket checked against it. A callback rather than
   * an inline effect because it is also called after the shop refuses an
   * order for stock: the figures on the cards must move to what the shop just
   * said, not wait for a reload.
   */
  const loadStore = useCallback(async () => {
    const res = await shopapi.store();
    if (!res.ok) {
      setItems([]);
      setLoadRefusal(res);
      return;
    }
    setItems(res.items);
    setLoadRefusal(null);
    setStockInfo(res.stock ?? null);
    // Prune the basket against the catalogue that just arrived, and keep
    // the names so the customer is TOLD rather than quietly short-changed.
    // Only when the catalogue really loaded: a refusal is the shop being
    // unreadable, and emptying somebody's basket over that would be this
    // page inventing a fact about what is on sale.
    //
    // Then CUT IT DOWN to what the shelf allows, the same way and for the
    // same reason. A product with no figure has no cap; the server sends
    // null for it and `fitCartToStore` leaves it alone.
    setCart((c) => {
      const live = new Set(res.items.map((it) => it.sku_id));
      const gone = Object.keys(c).filter((s) => !live.has(s));
      let next = c;
      if (gone.length > 0) {
        setDroppedNames(gone);
        next = { ...c };
        for (const s of gone) delete next[s];
      }
      const fit = shopapi.fitCartToStore(next, res.items);
      if (fit.changes.length === 0) return next;
      setCapped(fit.changes);
      return fit.cart;
    });
  }, []);
  useEffect(() => { void loadStore(); }, [loadStore]);

  // A customer who has already ordered comes back to their order, not to an
  // empty basket. The id is the only thing kept, and the order is re-read from
  // the shop — a status held in this browser would be a status nobody checked.
  useEffect(() => {
    const last = shopapi.lastOrder();
    if (!last) return;
    void (async () => {
      const res = await shopapi.myOrder(last);
      if (res.ok) {
        setOrder(res);
        // WHO MOVES THE SCREEN. Without a shell this page decides, and a
        // customer with an order is not shopping. WITH a shell the shell has
        // already chosen — it reads `lastOrder()` in its own first render for
        // exactly this — and moving it here would yank a customer who tapped
        // SHOP after ordering straight back to the order they had finished
        // reading. Measured: that is what it did.
        if (!navRef.current) setStage('order');
      } else {
        // The shop does not have it any more — a cleared scratch shop, or an id
        // from another counter. Forget it and show the shop, which is a better
        // answer than an error about an order the customer cannot act on.
        shopapi.forgetOrder();
        // Under a shell the ORDER screen may be the one open, and it would sit
        // on an order that is gone. Ask for the shelf instead. A no-op without
        // a shell: the stage is already `browse`.
        setStage('browse');
      }
      setRestoring(false);
    })();
  }, [setStage]);

  // Who is holding this phone. Asked once, at mount, because the answer decides
  // whether the whole page is a shop or a preview of one. SKIPPED under a
  // shell, which has already asked and passes the answer down.
  useEffect(() => {
    if (nav) return;
    void (async () => {
      const res = await shopapi.customerMe();
      if (!res.ok) return;
      setOwnMe(res);
    })();
    // Only whether there IS a shell matters, and that cannot change for a
    // mounted page: App.tsx renders one shell or the other, never both.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A customer the shop already knows does not type their own name and number
  // for the fourth time. Its own effect, keyed on `me`, because under a shell
  // that fact can arrive LATER than this page — signing in on MY ORDERS and
  // walking straight to the basket is the case that made it obvious. It never
  // overwrites something being typed.
  useEffect(() => {
    const c = me?.customer;
    if (!c) return;
    setName((n) => n || c.name);
    setPhone((p) => p || c.phone);
  }, [me]);

  useEffect(() => { shopapi.saveCart(cart); }, [cart]);

  /* ---- the basket ------------------------------------------------------- */

  const byId = useMemo(() => {
    const m = new Map<string, shopapi.StoreItem>();
    for (const it of items ?? []) m.set(it.sku_id, it);
    return m;
  }, [items]);

  /**
   * The basket, priced from the CATALOGUE THIS PAGE LOADED — never from a
   * number kept in the basket itself. A line whose product is no longer on sale
   * is dropped here and reported below, because a shop that quietly bills for
   * something it has stopped selling is the same bug as one that quietly bills
   * the wrong price.
   */
  const lines = useMemo(() => {
    const out: Array<{ item: shopapi.StoreItem; qty: number }> = [];
    for (const [sku, qty] of Object.entries(cart)) {
      const item = byId.get(sku);
      if (item && Number.isInteger(qty) && qty > 0) out.push({ item, qty });
    }
    out.sort((a, b) => a.item.name.localeCompare(b.item.name));
    return out;
  }, [cart, byId]);

  const total = useMemo(
    () => totalPaise(lines.map((l) => ({ price_paise: l.item.price_paise, qty: l.qty }))),
    [lines],
  );
  const count = lines.reduce((n, l) => n + l.qty, 0);

  /* The shell's BASKET tab carries this count. Reported from here rather than
     read from storage by the shell, because THIS is the pruned figure: lines
     whose product has left the catalogue, and lines cut down to what the shelf
     allows, are already out of it. The shell seeds itself from storage so the
     badge is right on the first paint, and this corrects it. */
  useEffect(() => { navRef.current?.onBasket(count); }, [count]);

  /**
   * What the offers in this basket add up to. PRESENTATION ONLY: the saving is
   * the difference between two integers the server sent, summed the same way
   * the total is. Nothing here changes what is charged.
   */
  const saved = useMemo(() => {
    let s = 0;
    for (const l of lines) {
      const marked = l.item.marked_paise;
      if (typeof marked === 'number' && Number.isInteger(marked) && marked > l.item.price_paise) {
        s += (marked - l.item.price_paise) * l.qty;
      }
    }
    return s;
  }, [lines]);

  /** Search over what a customer knows: the name, or the code on the packet. */
  const visible = useMemo(() => {
    const all = items ?? [];
    const needle = q.trim().toLowerCase();
    if (!needle) return all;
    return all.filter(
      (it) => it.name.toLowerCase().includes(needle) || it.sku_id.toLowerCase().includes(needle),
    );
  }, [items, q]);

  /**
   * Never above what the shop said it has. The stepper's + is disabled at the
   * cap as well; this is the second lock on the same door, for a keyboard or
   * a stale render. A product with no figure has no cap.
   */
  const setQty = useCallback((sku: string, qty: number) => {
    const item = byId.get(sku);
    const cap = item ? shopapi.availableOf(item) : null;
    const want = cap === null ? qty : Math.min(qty, cap);
    setCart((c) => {
      const next = { ...c };
      if (want <= 0) delete next[sku];
      else next[sku] = want;
      return next;
    });
  }, [byId]);

  /* ---- placing it ------------------------------------------------------- */

  const place = useCallback(async () => {
    setPlacing(true);
    setOrderRefusal(null);
    setStockFix(null);
    const res = await shopapi.placeOrder({
      items: lines.map((l) => ({ sku_id: l.item.sku_id, qty: l.qty })),
      name,
      phone,
      address,
      total_paise: total,
    });
    setPlacing(false);
    if (!res.ok) {
      // THE SHOP SAID EXACTLY HOW MANY THERE ARE. Another phone may have
      // taken the last packet while this basket was open, or the shop may
      // have counted the shelf. The refusal names every short line with the
      // number it will accept, so the basket is set to those numbers here —
      // never above, never for a line the shop did not name — and the
      // catalogue is re-read so the cards agree with the basket.
      const short = shopapi.shortLines(res);
      if (short) {
        const fit = shopapi.fitCartToRefusal(cart, short);
        setCart(fit.cart);
        setStockFix({ lines: short, changes: fit.changes });
        void loadStore();
        return;
      }
      setOrderRefusal(res);
      return;
    }
    shopapi.rememberOrder(res.order_id);
    /* AND WHO PLACED IT, so MY ORDERS opens with the name and number already
       in it and the customer types only the one thing that screen actually
       asks for — the order id. Placing an order creates no customer session
       (measured: `/store/customer/me` still answers `customer: null` right
       after a 200 from `/store/order`), so without this the next screen has
       two empty boxes and refuses `customer_name_missing` to a customer who
       did exactly what it told them to. */
    shopapi.rememberCustomer(name, phone);
    setOrder(res);
    setCart({});
    setStage('order');
  }, [lines, name, phone, address, total, cart, loadStore]);

  /* ---- watching it ------------------------------------------------------ */

  useEffect(() => {
    if (stage !== 'order' || !order) return;
    if (order.status === 'delivered' || order.status === 'cancelled') return;
    const id = setInterval(() => {
      void (async () => {
        const res = await shopapi.myOrder(order.order_id);
        if (res.ok) setOrder(res);
      })();
    }, POLL_MS);
    return () => clearInterval(id);
  }, [stage, order]);

  const startPayment = useCallback(async () => {
    if (!order) return;
    setPaying(true);
    setPayRefusal(null);
    const res = await shopapi.pay(order.order_id);
    setPaying(false);
    if (!res.ok) {
      setPayRefusal(res);
      return;
    }
    // ONLY A LINK THE GATEWAY WILL SERVE. A mint can succeed and still hand
    // back a string that resolves to `{}` — that is the defect this whole path
    // exists to close — so the server's verdict decides whether this becomes a
    // button. `payable === false` is checked explicitly rather than
    // `!res.payable`, so an older server that does not send the field at all
    // cannot silently disable a working payment.
    if (res.payable === false) {
      setPayLink(null);
      // Fold the verdict back onto the order, so the screen below renders the
      // dead-link case from one place whether the customer just pressed PAY or
      // arrived on a page that already knew.
      setOrder((o) => (o ? { ...o, payable: false, link_state: res.link_state,
                             short_url: res.short_url, can_relink: res.can_relink,
                             payment_note: res.note } : o));
      return;
    }
    setPayLink(res.short_url);
  }, [order]);

  /**
   * Ask for a replacement link. Only reachable when the link this order has is
   * one nobody can be sent to — the gateway denied it (`dead`), or it does not
   * point at the gateway at all (`refused`). A link that is live, or that the
   * shop simply could not reach, is NOT replaced: the server refuses this
   * otherwise, and it is the server's refusal that makes it safe, not this
   * button being hidden.
   */
  const getNewLink = useCallback(async () => {
    if (!order) return;
    setRelinking(true);
    setPayRefusal(null);
    const res = await shopapi.relink(order.order_id);
    setRelinking(false);
    if (!res.ok) {
      setPayRefusal(res);
      return;
    }
    const fresh = await shopapi.myOrder(order.order_id);
    if (fresh.ok) setOrder(fresh);
    if (res.payable !== false) setPayLink(res.short_url);
  }, [order]);

  const startAgain = useCallback(() => {
    shopapi.forgetOrder();
    setOrder(null);
    setPayLink(null);
    setPayRefusal(null);
    setOrderRefusal(null);
    setStockFix(null);
    setCapped([]);
    setName('');
    setPhone('');
    setAddress('');
    setStage('browse');
    // The shelf moved while the order was being handled — this customer's
    // own order is holding packets now — so the cards are re-read.
    void loadStore();
  }, [loadStore]);

  /** Which delivery box the server's refusal is about, if it named one. */
  const badField = orderRefusal ? FIELD_OF_REFUSAL[orderRefusal.reason] : undefined;

  /**
   * THE SHOPKEEPER IS LOOKING AT THEIR OWN SHOP FRONT.
   *
   * Said up front, on the shelves and again at the delivery form, because the
   * alternative is what shipped: the shopkeeper fills in a name and an address,
   * presses PLACE ORDER, and only then learns the shop will not take it. The
   * server refuses either way — this is the courtesy, not the rule.
   *
   * Blue, not amber or red. This is the machine explaining itself, not a
   * refusal of anything the person has done yet, and the three colours are
   * reserved for what the money path did.
   */
  const previewing = me?.previewing ? (
    <Verdict tone="info" title="You are previewing your own shop">
      This is what a customer sees when they photograph the shutter code. You are
      signed in at the counter{me.shopkeeper_name ? ` as ${me.shopkeeper_name}` : ''},
      so the shop will not take an order from this browser — ordering from yourself
      would make a real order, a real payment link and a real line in the books for a
      sale that never happened. To try it as a customer does, open the shop in a
      private window.
    </Verdict>
  ) : null;

  /* ---- coming back to an order already placed ---------------------------- */

  // Before the shop, because a customer who has an order is not shopping. The
  // alternative is a flash of an empty basket over the top of their order.
  //
  // UNDER A SHELL, ONLY THE ORDER SCREEN WAITS. The shell has already chosen
  // the screen, and this page is remounted every time the customer comes back
  // from MY ORDERS — so a bare `if (restoring)` put a spinner captioned "Your
  // order" over the SHELF, for a customer who had tapped SHOP and was not
  // asking about an order at all.
  if (restoring && (!nav || stage === 'order')) {
    return (
      <div className="sf narrow">
        <header className="sf-hero">
          <h1>Your order</h1>
          <p>Asking the shop where it has got to.</p>
        </header>
        <LoadingCard lines={4} label="Loading your order" />
      </div>
    );
  }

  /* ---- the order screen -------------------------------------------------- */

  if (stage === 'order' && order) {
    const cancelled = order.status === 'cancelled';
    const step = JOURNEY.findIndex((s) => s.id === order.status);
    // THE SERVER'S VERDICT, NOT THIS PAGE'S GUESS. `payable === false` means the
    // gateway itself denied the code — a 404 for a link it never issued, which
    // is exactly what shipped. Anything else, including the field being absent,
    // leaves the link showable: not reaching the gateway is not evidence
    // against a payment, and refusing money the customer wanted to pay is the
    // more expensive mistake of the two.
    const deadLink = order.payable === false && !order.paid;
    const link = deadLink ? null : (payLink ?? order.short_url);
    return (
      <div className="sf narrow">
        <header className="sf-hero">
          <h1>Your order</h1>
          <p className="sf-oid mono">{order.order_id}</p>
        </header>

        <Card
          title={cancelled ? 'Cancelled' : 'Where it is'}
          aside={<Pill tone={order.paid ? 'ok' : 'amb'} dot={order.paid}>
            {order.paid ? 'PAID' : 'NOT PAID'}
          </Pill>}
        >
          {cancelled ? (
            <Verdict tone="red" title="The shop cancelled this order">
              {STATUS_LINE.cancelled}
            </Verdict>
          ) : (
            <ol className="sf-journey">
              {JOURNEY.map((s, i) => (
                <li key={s.id} className={i < step ? 'done' : i === step ? 'now' : ''}>
                  <span className="mark" aria-hidden />
                  <span className="lbl">{s.label}</span>
                </li>
              ))}
            </ol>
          )}
          <p className="hint sf-status">{STATUS_LINE[order.status] ?? order.status}</p>
        </Card>

        <Card title="What you ordered">
          <div className="bill">
            <div className="bill-lines">
              {order.lines.map((l) => (
                <div className="bill-line" key={l.sku_id}>
                  <span className="nm">{l.name}</span>
                  <span className="qty">×{l.qty}</span>
                  <span className="amt tnum">{rupees(l.line_paise)}</span>
                </div>
              ))}
            </div>
            <div className="bill-total">
              <span className="lbl">Total</span>
              <span className="amt tnum">{rupees(order.total_paise)}</span>
            </div>
          </div>

          {order.paid ? (
            <Verdict tone="green" title={`Paid — ${rupees(order.total_paise)}`}>
              The gateway's own signed callback reached the shop and matched this
              order. That is the only thing that produces this line.
            </Verdict>
          ) : cancelled ? null : deadLink ? (
            <>
              {/* THE LINK IS DEAD AND THIS SAYS SO IN WORDS.
                  What used to happen here: `short_url` was present, so this
                  branch drew a green PAY button, and pressing it opened a page
                  whose entire content was `{}` — the gateway's 404 body for a
                  short code it never issued. A customer cannot act on that and
                  cannot tell it from the shop being broken, which it was.
                  Amber, not red: the counter is abstaining, not refusing the
                  customer, and the order itself is fine. */}
              {/* The title must not claim the gateway spoke when it did not:
                  `refused` is the shop declining to send anyone to a string
                  that is not on the gateway, and the gateway was never asked. */}
              <Verdict
                tone="amber"
                title={order.link_state === 'refused'
                  ? 'This payment link cannot be used'
                  : 'This payment link no longer works'}
              >
                {order.payment_note
                  ?? 'The shop’s payment gateway does not recognise the link this '
                   + 'order was given. Nothing has been charged.'}
              </Verdict>
              {order.can_relink && (
                <button
                  className="btn pay big"
                  onClick={() => void getNewLink()}
                  disabled={relinking}
                >
                  {relinking ? 'ASKING THE SHOP…' : 'GET A NEW PAYMENT LINK'}
                </button>
              )}
              <p className="hint">
                Or pay the delivery person at the door. Asking for a new link does not
                charge anything and cannot charge you twice — the shop only replaces a
                link its own gateway has said is gone.
              </p>
            </>
          ) : link ? (
            <>
              {/* A RENDER OF THE GATEWAY'S OWN LINK. This page has no code that
                  builds a payment address, and the shop refuses to repeat a
                  link that did not come from the gateway. Reached only when the
                  gateway confirmed it still serves this link — see `deadLink`. */}
              <a className="btn pay big" href={link} target="_blank" rel="noreferrer">
                PAY {rupees(order.total_paise)}
              </a>
              {/* THE PAGE IS WAITING, AND SAYS SO. Without this the customer
                  comes back from the gateway to a screen identical to the one
                  they left — same button, no acknowledgement — and presses PAY
                  again. It reports only that the shop is still asking; it never
                  claims the money arrived, because this page cannot know that
                  and the server will not say so until the signed callback. */}
              <p className="hint sf-status" aria-live="polite">
                <Working /> Waiting for the gateway to confirm
                {order.payment_state ? <> · <b className="mono">{order.payment_state}</b></> : null}
              </p>
              <p className="hint">
                This opens the payment page Razorpay issued for this order. The shop
                did not build that link and cannot mark this order paid on its own —
                only the gateway's signed callback does that.
              </p>
            </>
          ) : (
            <>
              <button className="btn pay big" onClick={() => void startPayment()} disabled={paying}>
                {paying ? 'ASKING THE SHOP…' : `PAY ${rupees(order.total_paise)}`}
              </button>
              <p className="hint">
                You can also pay the delivery person at the door. Paying now asks the
                shop's payment gateway for a link; nothing is charged until you finish
                on the gateway's own page.
              </p>
            </>
          )}
          {payRefusal && (
            <Refusal
              reason={payRefusal.reason}
              detail={payRefusal.detail}
              hint="Nothing was charged. The shop can still take payment at the door."
            />
          )}
        </Card>

        <div className="btn-row sf-foot">
          <button className="btn" onClick={startAgain}>ORDER SOMETHING ELSE</button>
        </div>
      </div>
    );
  }

  /* ---- the checkout screen ----------------------------------------------- */

  if (stage === 'checkout') {
    return (
      <div className="sf narrow">
        <header className="sf-hero">
          <h1>Where should it go?</h1>
          <p>{count} item{count === 1 ? '' : 's'} · {rupees(total)}</p>
        </header>

        {previewing}

        <Card title="Delivery">
          {/* `htmlFor`/`id` on every one: without the pair the label is decoration,
              tapping it does not focus the box — which on a phone is most of the
              target area — and a screen reader announces an unnamed field. */}
          <Field
            label="Your name"
            htmlFor="sf-name"
            required
            error={badField === 'name' ? orderRefusal?.detail : undefined}
          >
            <input
              id="sf-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoComplete="name"
              placeholder="Rekha"
              enterKeyHint="next"
            />
          </Field>
          <Field
            label="Phone"
            htmlFor="sf-phone"
            required
            sub="The shop calls this number if it cannot find the door."
            error={badField === 'phone' ? orderRefusal?.detail : undefined}
          >
            <input
              id="sf-phone"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              autoComplete="tel"
              inputMode="tel"
              type="tel"
              placeholder="98765 43210"
              enterKeyHint="next"
            />
          </Field>
          <Field
            label="Address"
            htmlFor="sf-address"
            required
            sub="House or flat number, floor, and something nearby."
            error={badField === 'address' ? orderRefusal?.detail : undefined}
          >
            <textarea
              id="sf-address"
              value={address}
              onChange={(e) => setAddress(e.target.value)}
              autoComplete="street-address"
              rows={4}
              placeholder={'12 MG Road, second floor\nnear the water tank'}
            />
          </Field>

          {/* THE SHOP HAD FEWER THAN THE BASKET ASKED FOR, and said how many.
              The basket below has already been set to those numbers; this
              says what changed, line by line, in the shop's own figures. Amber
              like every refusal on this counter: the shop declined the order,
              nothing was charged, and the customer can send it again. */}
          {stockFix && (
            <Verdict tone="amber" title="The shop has fewer of some of these than you asked for">
              Nothing was ordered. Your basket has been changed to what the shop can sell
              right now — check it below and press PLACE ORDER again.
              {stockFix.lines.map((l) => (
                <span className="sf-fix-line" key={l.sku_id}>
                  <br />
                  <b>{l.name}</b>: you asked for {l.asked},{' '}
                  {l.out_of_stock ? 'none left' : `${l.available} available`}
                  {' — '}
                  {l.available === 0 ? 'taken out of your basket' : `set to ${l.available}`}.
                </span>
              ))}
              <br />
              Available means what is on the shelf minus what other customers have already
              ordered and not yet received.
            </Verdict>
          )}

          {/* Everything the map above does not place. A refusal this page has
              never seen still arrives in full rather than vanishing. */}
          {orderRefusal && !badField && (
            <Refusal
              reason={orderRefusal.reason}
              detail={orderRefusal.detail}
              hint="Nothing was ordered. Fix the line above and send it again."
            />
          )}
        </Card>

        <Card title="Your basket" tight>
          <div className="bill">
            <div className="bill-lines">
              {lines.map((l) => (
                <div className="bill-line" key={l.item.sku_id}>
                  <span className="nm">{l.item.name}</span>
                  <span className="qty">×{l.qty}</span>
                  <span className="amt tnum">{rupees(l.item.price_paise * l.qty)}</span>
                </div>
              ))}
            </div>
            {saved > 0 && (
              <div className="sf-saved">
                <span>You save</span>
                <span className="tnum">{rupees(saved)}</span>
              </div>
            )}
            <div className="bill-total">
              <span className="lbl">Total</span>
              <span className="amt tnum">{rupees(total)}</span>
            </div>
          </div>
          <p className="hint">
            The shop adds this up again from its own catalogue before it accepts the
            order. If a price changed while you were here, it says so rather than
            charging either number quietly.
          </p>
        </Card>

        <BasketBar>
          <button className="btn ghost" onClick={() => setStage('browse')}>BACK</button>
          {/* Disabled for a shopkeeper previewing their own shop. The server
              refuses it too — this is so the button does not invite a press it
              is going to reject, not because disabling it is the rule. */}
          <button
            className="btn primary sf-go"
            onClick={() => void place()}
            disabled={placing || count === 0 || me?.previewing === true}
          >
            {me?.previewing === true
              ? 'PREVIEW — NOT A CUSTOMER'
              : placing ? 'SENDING…' : `PLACE ORDER · ${rupees(total)}`}
          </button>
        </BasketBar>
      </div>
    );
  }

  /* ---- the shop ---------------------------------------------------------- */

  return (
    <div className="sf">
      {/* The name over the door, the picture, and whether the link was this
          shop's — see ShopPlate above.
          NOT UNDER THE CUSTOMER'S SHELL: that shell's own bar already carries
          the photograph and the name, and its own header carries the verdict
          on the link. Drawn here too, the shop would say its name twice on one
          390 px screen — and would ask `/store/shop` twice to do it. */}
      {!nav && <ShopPlate />}

      {previewing}

      {/* A link made out to this person, spent. Said out loud, because being
          silently recognised by a shop you have not typed anything into is
          unsettling — and because if the link had already been used, the
          customer needs to know why the shop still does not know them. */}
      {claimed && ('name' in claimed ? (
        <div className="sf-note">
          <b>Namaste, {claimed.name}.</b>{' '}
          This shop already knows your name and number, so you can go straight to
          the basket. Nothing has been ordered and nothing has been charged.
        </div>
      ) : (
        <Refusal
          reason={claimed.reason}
          detail={claimed.detail}
          hint="You can still order — put your name and number in at the basket."
        />
      ))}

      {loadRefusal && (
        <Refusal
          reason={loadRefusal.reason}
          detail={loadRefusal.detail}
          hint="Nothing can be ordered until the shop's catalogue can be read."
        />
      )}

      {droppedNames.length > 0 && (
        <Verdict tone="amber" title="Something in your basket is no longer on sale">
          {droppedNames.join(', ')} — taken out of your basket, because the shop has
          stopped selling it. Everything else is still here.
        </Verdict>
      )}

      {/* A basket kept overnight can outlive the shelf. Said, with the
          numbers, rather than quietly reduced. */}
      {capped.length > 0 && (
        <Verdict tone="amber" title="Your basket was cut down to what the shop has">
          {capped.map((c) => (
            <span key={c.sku_id}>
              <b>{c.name}</b>: {c.from} in your basket, {c.to === 0 ? 'none left — taken out' : `${c.to} left — set to ${c.to}`}.
              <br />
            </span>
          ))}
          What is on the shelf minus what other customers have already ordered.
        </Verdict>
      )}

      {items !== null && items.length > 0 && (
        <>
          <div className="sf-search" role="search">
            <div className="box">
              <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
                <circle cx="9" cy="9" r="6" stroke="currentColor" strokeWidth="2" />
                <path d="m13.5 13.5 4 4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search the shelves"
                aria-label="Search products by name or code"
                enterKeyHint="search"
              />
              {q !== '' && (
                <button className="clear" onClick={() => setQ('')} aria-label="Clear the search">
                  ✕
                </button>
              )}
            </div>
          </div>
          <p className="sf-count" aria-live="polite">
            {q.trim()
              ? `${visible.length} of ${items.length} products match`
              : `${items.length} product${items.length === 1 ? '' : 's'} on sale`}
          </p>
        </>
      )}

      <div ref={listRef}>
        {items === null ? (
          <div className="sf-grid" aria-hidden="true">
            {Array.from({ length: 8 }, (_, i) => (
              <div className="sf-card" key={i}>
                <div className="skel sf-ph-skel" />
                <div className="sf-body">
                  <div className="skel sf-txt-skel" />
                  <div className="skel sf-txt-skel short" />
                </div>
              </div>
            ))}
          </div>
        ) : items.length === 0 ? (
          <Empty>
            This shop has not put anything on sale yet.
            <br />
            The shopkeeper teaches a product at the counter and it appears here.
          </Empty>
        ) : visible.length === 0 ? (
          <Empty>
            Nothing on the shelves matches “{q.trim()}”.
            <br />
            <button className="btn sm" style={{ marginTop: 12 }} onClick={() => setQ('')}>
              SHOW EVERYTHING
            </button>
          </Empty>
        ) : (
          <div className="sf-grid">
            {visible.map((it) => {
              const qty = cart[it.sku_id] ?? 0;
              const marked = it.marked_paise;
              const off = it.off_paise;
              const deal =
                typeof off === 'number' && Number.isInteger(off) && off > 0 &&
                typeof marked === 'number' && Number.isInteger(marked) &&
                marked > it.price_paise;
              /**
               * THE SHELF. `cap` is what the shop will sell of this — its own
               * count minus what other customers' open orders hold, minus
               * whatever it keeps back for the counter — or null when nobody
               * has counted it, which is NOT zero: such a product is sold as
               * before and its card says "no stock figure". `out` is the
               * server's verdict, never this page's, and it is only ever true
               * where there IS a figure. The card stays on the shelf greyed
               * rather than vanishing: a customer should see what the shop
               * normally carries. The button is off as a courtesy; the order
               * route refuses the line as the rule.
               */
              const cap = shopapi.availableOf(it);
              const out = shopapi.isOut(it);
              const atCap = cap !== null && qty >= cap;
              const held = typeof it.reserved_units === 'number' && it.reserved_units > 0
                ? it.reserved_units : 0;
              const shelf = cap === null
                ? 'no stock figure'
                : out
                  ? 'none left to sell online'
                  : cap <= 5
                    ? `only ${cap} left`
                    : `${cap} available`;
              return (
                <article
                  className={['sf-card', qty > 0 ? 'in' : '', out ? 'out' : ''].filter(Boolean).join(' ')}
                  key={it.sku_id}
                  data-stock={cap === null ? 'none' : String(cap)}
                >
                  <div className="sf-ph">
                    {/* Out of stock outranks the deal tag in the corner: a
                        discount on something you cannot buy is noise. */}
                    {out
                      ? <span className="sf-oos">OUT OF STOCK</span>
                      : deal && <span className="sf-save">SAVE {rupees(off)}</span>}
                    {it.photo_url ? (
                      <img src={it.photo_url} alt="" loading="lazy" />
                    ) : (
                      <span className="initial" aria-hidden>{it.name.slice(0, 1)}</span>
                    )}
                  </div>
                  <div className="sf-body">
                    <div className="sf-nm">{it.name}</div>
                    <div className="sf-price">
                      <span className="now tnum">{rupees(it.price_paise)}</span>
                      {deal && <s className="sf-mrp tnum">{rupees(marked)}</s>}
                    </div>
                    <div
                      className={cap !== null && !out && cap <= 5 ? 'sf-stock low' : 'sf-stock'}
                      /* A SENTENCE, NOT A COUNT. The server used to send the
                         exact number of packets held by other orders and this
                         tooltip printed it. `/store` is open to anyone with
                         the shutter link, so an exact reservation count polled
                         through the day reads the shop's order book. The
                         server now sends a flag; the customer still learns
                         why they may only have three. */
                      title={held > 0
                        ? 'Some are held for orders already placed and not yet delivered'
                        : undefined}
                    >
                      {shelf}
                    </div>
                  </div>
                  <div className="sf-ctl">
                    {qty === 0 ? (
                      <button
                        className="sf-add"
                        onClick={() => setQty(it.sku_id, 1)}
                        disabled={out}
                        aria-label={out ? `${it.name} is out of stock` : `Add ${it.name}`}
                        title={out ? 'The shop has none of this to sell online right now.' : undefined}
                      >
                        {out ? 'OUT OF STOCK' : 'ADD'}
                      </button>
                    ) : (
                      <div className="sf-step" role="group" aria-label={it.name}>
                        <button onClick={() => setQty(it.sku_id, qty - 1)} aria-label="One fewer">−</button>
                        <span className="n tnum" aria-live="polite">{qty}</span>
                        <button
                          onClick={() => setQty(it.sku_id, qty + 1)}
                          disabled={atCap}
                          aria-label={atCap ? `Only ${cap} available` : 'One more'}
                          title={atCap
                            ? `The shop has ${cap} of these to sell — the rest are on the shelf for the counter or already in other orders.`
                            : undefined}
                        >
                          +
                        </button>
                      </div>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>

      {count > 0 && (
        <BasketBar>
          <div className="sf-tot">
            <span className="n">
              {count} item{count === 1 ? '' : 's'}
              {saved > 0 && <> · <b>saving {rupees(saved)}</b></>}
            </span>
            <span className="amt tnum">{rupees(total)}</span>
          </div>
          <button className="btn primary sf-go" onClick={() => setStage('checkout')}>
            CHECKOUT
          </button>
        </BasketBar>
      )}

      <div className="sf-note">
        <Card title="How this shop works" tight>
          <KV k="prices">the same ones the counter charges</KV>
          <KV k="this page">names products; it never sets a price</KV>
          {/* THE RESERVATION RULE, SAID WHERE THE CUSTOMER CAN READ IT. An
              order holds its packets from the moment it is placed, so the
              "available" on a card is the shelf minus other people's open
              orders — which is why it can drop while this page is open. */}
          <KV k="stock">
            what is on the shelf, minus what other customers have ordered and not yet
            received. Placing an order holds your packets; nothing leaves the shop's count
            until it packs them.
          </KV>
          <KV k="paying">on the gateway's own page, or at the door</KV>
          <p className="hint">
            Nothing on this page can mark an order paid. A payment settles when the
            gateway's signed callback reaches the shop, and not before.
          </p>
          {stockInfo && !stockInfo.figures && (
            <p className="hint">
              The shop's stock figures could not be read just now, so no product is capped
              and every card says "no stock figure".
            </p>
          )}
        </Card>
      </div>
    </div>
  );
}
