import { useCallback, useEffect, useState, type ReactNode } from 'react';
import Shop, { slugFromHash, type Stage } from '../routes/Shop';
import CustomerOrders from './CustomerOrders';
import * as shopapi from '../lib/shopapi';
import { storeShop, type StoreShop } from '../lib/adminapi';
import '../styles/shopface.css';
import '../styles/customer.css';

/**
 * THE CUSTOMER'S OWN SHELL.
 *
 * What shipped before this: a stranger photographed the sticker on the
 * shutter, landed on `#/shop`, and got the SHOPKEEPER'S chrome — the sidebar
 * offering "Your products / Orders / Customers / Loyalty / Your shop", and
 * COUNTER · SHOP · BOOKS across the top. Three separate faults in one screen:
 * it is confusing (none of it is theirs), it advertises screens that answer
 * their phone with a 401, and the list is a map of the shop's admin handed to
 * anybody who has the link.
 *
 * This is the chrome that belongs to the person holding the phone. The shop's
 * name and face, three places, and who they are. Nothing else, and in
 * particular nothing that names a shopkeeper's figure: this shell reads
 * `/store`, `/store/shop`, `/store/shop/photo`, `/store/photo/*` and
 * `/store/customer/*`, and no route behind the lock. Measured on the built
 * site in a context with no cookies: those, plus `/auth/status`, are every
 * request the page makes.
 *
 * WHY THEY ANSWER A STRANGER, precisely — an earlier version of this comment
 * credited `gawaah/auth.py`, which disclaims it in so many words ("IT DOES NOT
 * KNOW ABOUT THE STOREFRONT"). Its `OPEN_PATHS` holds `/store/shop` and
 * `/store/shop/photo` and neither `/store` nor `/store/customer/*`. The whole
 * customer side is opened one level up, where the routers are mounted:
 * `tools/upload_app.py` builds its guard with
 * `depends_open(paths=("/", "/health"), prefixes=("/store", "/receipt",
 * "/qr/link"))`. That is the line to read if this shell ever starts getting
 * 401s — not the auth module's own list.
 *
 * WHO GETS IT is decided in App.tsx, not here — see the block comment on
 * `strangerHere`. A signed-in shopkeeper opening the same address keeps the
 * full counter chrome and the "you are previewing your own shop" notice.
 *
 * ENGLISH ONLY, like routes/Shop.tsx, which this shell wraps. The three-way
 * en/hi/bn parity test covers the till, the shopkeeper's shell and Products;
 * the storefront has never been in it, and half-translating it here would put
 * a Hindi tab strip over an English catalogue.
 */

/**
 * The four screens this shell can show. Three of them are the storefront in a
 * different `Stage`; the fourth is the only screen that is not the shop.
 *
 * `order` is not a fourth TAB. It is the order this phone has just placed or
 * come back to, and it lights MY ORDERS — which is what it is.
 */
export type CustomerView = 'shop' | 'basket' | 'order' | 'orders';

/** The storefront's screen for each of the three views that are the shop. */
const STAGE_OF: Record<'shop' | 'basket' | 'order', Stage> = {
  shop: 'browse',
  basket: 'checkout',
  order: 'order',
};

/** The reverse, so the page asking for a screen moves the shell's tab with it. */
const VIEW_OF: Record<Stage, CustomerView> = {
  browse: 'shop',
  checkout: 'basket',
  order: 'order',
};

type TabId = 'shop' | 'basket' | 'orders';

/* Inline SVG on the same 16-unit grid and 1.65 stroke as components/shell.tsx.
   Drawn here rather than imported: the CSP allows no external host and this
   app ships no icon package. `currentColor`, so the open tab takes its accent
   from CSS. */
const ICO = {
  width: 20, height: 20, viewBox: '0 0 16 16',
  fill: 'none', stroke: 'currentColor',
  strokeWidth: 1.65, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const,
  'aria-hidden': true, focusable: false,
};

/** An awning — the same mark the shopkeeper's sidebar uses for the storefront. */
const IcoShelf = (
  <svg {...ICO}>
    <path d="M2 6.5 3.2 2.5h9.6L14 6.5" />
    <path d="M2 6.5c0 1 .9 1.8 2 1.8s2-.8 2-1.8c0 1 .9 1.8 2 1.8s2-.8 2-1.8c0 1 .9 1.8 2 1.8s2-.8 2-1.8" />
    <path d="M3.2 8.3v5.2h9.6V8.3" />
  </svg>
);

/** A basket. */
const IcoBasket = (
  <svg {...ICO}>
    <path d="M2 6h12l-1.1 7.2a1 1 0 0 1-1 .8H4.1a1 1 0 0 1-1-.8L2 6Z" />
    <path d="M5.6 6 8 1.8 10.4 6" />
    <path d="M6.3 9v2.2M9.7 9v2.2" />
  </svg>
);

/** A parcel — the same mark the shopkeeper's Orders row uses. */
const IcoParcels = (
  <svg {...ICO}>
    <path d="M2.4 5 8 2.2 13.6 5v6L8 13.8 2.4 11V5Z" />
    <path d="M2.4 5 8 7.8 13.6 5M8 7.8v6" />
  </svg>
);

const TABS: ReadonlyArray<{ id: TabId; label: string; ico: ReactNode }> = [
  { id: 'shop', label: 'Shop', ico: IcoShelf },
  { id: 'basket', label: 'Basket', ico: IcoBasket },
  { id: 'orders', label: 'My orders', ico: IcoParcels },
];

/** Whole packets in a basket read off this browser. See `seedCount` below. */
function countOf(cart: Record<string, number>): number {
  let n = 0;
  for (const q of Object.values(cart)) if (Number.isInteger(q) && q > 0) n += q;
  return n;
}

export default function CustomerShell() {
  /**
   * A CUSTOMER WHO HAS ALREADY ORDERED COMES BACK TO THEIR ORDER.
   *
   * Decided here, in the first render, and NOT by the storefront: the page
   * used to restore the order itself, which was right when it owned the whole
   * screen and wrong under a shell with a SHOP tab — tapping Shop after
   * ordering would be yanked straight back to the order by the page's own
   * effect. The shell picks the screen; the page draws it.
   */
  const [view, setView] = useState<CustomerView>(() => (shopapi.lastOrder() ? 'order' : 'shop'));

  /** The shop's public face: name, address, photograph, and whether the link
      this phone arrived with was made for THIS shop. Four open fields. */
  const [face, setFace] = useState<StoreShop | 'unreadable' | null>(null);
  const [slug, setSlug] = useState<string | null>(() => slugFromHash());

  /** Who this phone is, to the shop. Read HERE and handed to the storefront,
      so the two do not each ask — and so signing in on MY ORDERS fills in the
      delivery form on the very next tap. */
  const [me, setMe] = useState<shopapi.CustomerMe | null>(null);

  /**
   * How many packets are in the basket, for the tab badge.
   *
   * Seeded from this browser's own basket so the badge is right on the first
   * paint, then CORRECTED by the storefront once the catalogue lands: the
   * seed counts every line in storage, and the page counts only the lines
   * that are still on sale. The two differ for exactly as long as it takes to
   * read `/store`, and the number is a count of packets, not money.
   */
  const [count, setCount] = useState<number>(() => countOf(shopapi.loadCart()));

  /* A SECOND SHOP'S LINK OPENED IN THE SAME TAB is a hash change on a route
     that is already mounted — nothing remounts — so the slug read at mount
     would stay the first shop's and the verdict would be about the wrong
     link. Same defect, same fix, as ShopPlate in routes/Shop.tsx. */
  useEffect(() => {
    const onHash = () => setSlug(slugFromHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  useEffect(() => {
    let live = true;
    void (async () => {
      const r = await storeShop(slug);
      if (live) setFace(r.ok ? r : 'unreadable');
    })();
    return () => { live = false; };
  }, [slug]);

  const readMe = useCallback(async () => {
    const r = await shopapi.customerMe();
    // A refusal leaves `me` as it was. This route does not refuse — see
    // `store_customer_me_ep`, which answers "nobody" rather than erroring —
    // so the only way here is the network, and a stranger who cannot reach
    // the shop learns that from the catalogue, not from a chip in the bar.
    if (r.ok) setMe(r);
  }, []);
  useEffect(() => { void readMe(); }, [readMe]);

  /* THE STOREFRONT'S BASKET BAR IS PORTALLED ONTO <body>, outside this
     subtree, so it cannot be lifted above the tab strip by a descendant
     selector. The class on <body> is what customer.css hangs that rule off,
     and it is removed on unmount so a shopkeeper who signs in on this very
     screen does not keep a raised basket bar afterwards. */
  useEffect(() => {
    document.body.classList.add('cx-shell');
    return () => { document.body.classList.remove('cx-shell'); };
  }, []);

  /** The page asking for a screen: CHECKOUT, BACK, ORDER SOMETHING ELSE, and
      the moment an order is accepted. */
  const onStage = useCallback((s: Stage) => { setView(VIEW_OF[s]); }, []);

  /** Open one order from the list. The storefront reads the remembered id on
      mount, which is the path a returning customer already takes. */
  const openOrder = useCallback((orderId: string) => {
    shopapi.rememberOrder(orderId);
    setView('order');
  }, []);

  const known = face !== null && face !== 'unreadable' ? face : null;
  // A counter that cannot say its name is still a shop. "The shop" is the
  // honest fallback and is what this header said for every counter on earth
  // before the profile existed; it is never a placeholder pretending to be
  // a name.
  const name = known?.name ?? 'The shop';
  const initial = (known?.name ?? 'S').slice(0, 1).toUpperCase();
  const who = me?.customer ?? null;
  const tab: TabId = view === 'shop' ? 'shop' : view === 'basket' ? 'basket' : 'orders';

  return (
    <div className="cx">
      <header className="cx-top">
        <span className="cx-face">
          {known?.photo_url
            ? <img src={known.photo_url} alt={`The front of ${name}`} />
            : <span className="initial" aria-hidden="true">{face === null ? '' : initial}</span>}
        </span>
        <span className="cx-name">
          <b title={name}>{name}</b>
        </span>
        {/* IDENTITY LIVES IN ONE PLACE, and it is not the shopkeeper's sign-in
            screen. `#/signin` wants a phone AND A PASSWORD and mints a counter
            session; a customer has neither and must never be sent there. This
            chip opens MY ORDERS, which is where a customer signs in, signs out
            and reads back what they ordered. */}
        <button
          className="cx-who"
          onClick={() => setView('orders')}
          aria-label={who ? `Signed in as ${who.name}. Open my orders.` : 'Sign in to see your orders'}
        >
          {who ? <span className="nm">{who.name}</span> : 'Sign in'}
        </button>
      </header>

      <nav className="cx-tabs" aria-label="This shop">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setView(t.id === 'orders' ? 'orders' : t.id)}
            aria-current={tab === t.id ? 'page' : undefined}
            /* The badge is a numeral with no word beside it, so the accessible
               name has to carry the word. Only on the basket, and only when
               there is something in it. */
            aria-label={t.id === 'basket' && count > 0
              ? `Basket, ${count} item${count === 1 ? '' : 's'}`
              : undefined}
          >
            <span className="ico">
              {t.ico}
              {/* The basket count, in the machine's blue. Never green: the
                  three verdict colours are reserved for what the money path
                  did, and a basket has done nothing with money. */}
              {t.id === 'basket' && count > 0 && (
                <span className="cx-badge" aria-hidden="true">{count}</span>
              )}
            </span>
            {t.label}
          </button>
        ))}
      </nav>

      <main className="cx-main">
        {/* THE LINK WAS PRINTED FOR A DIFFERENT SHOP. Said above everything, on
            every tab, because it is a fact about the sticker rather than about
            one screen. Ink on glass with a navy rule — see shopface.css: it is
            not a verdict on a rupee, so it wears none of the three colours. */}
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

        {view === 'shop' && (
          <p className="cx-intro">
            Everything the counter has been taught, at the price the counter charges. Add
            what you need and give an address — the shopkeeper packs it.
          </p>
        )}

        {view === 'orders' ? (
          <CustomerOrders me={me} onIdentity={() => void readMe()} onOpen={openOrder} />
        ) : (
          <Shop
            nav={{
              stage: STAGE_OF[view],
              setStage: onStage,
              onBasket: setCount,
              me,
              refreshMe: () => void readMe(),
            }}
          />
        )}

        {/* WHOSE SHOP THIS IS. Name and address come from `/store/shop`, the
            open header — four public fields and no more. The shop's own phone
            number is NOT one of them: `store_shop_ep` keeps it on
            `/shop/profile`, behind the lock, so it is named as absent here
            rather than fetched from somewhere a stranger cannot read or, worse,
            invented. */}
        <footer className="cx-foot">
          <h2>{name}</h2>
          {known?.address && <address>{known.address}</address>}
          <p>
            This page does not carry the shop&rsquo;s own phone number — the counter keeps it.
            The shop rings the number you leave with an order.
          </p>
          <p>
            Every bill here is witnessed at the counter: a camera names what leaves the shelf,
            and the shop adds your basket up again from its own catalogue before it accepts an
            order. An order is marked paid only when Razorpay&rsquo;s own signed callback reaches
            the shop — nothing on this page can do it.
          </p>
          <div className="cx-mark">
            GAWAAH <span className="deva" lang="hi">गवाह</span>
          </div>
        </footer>
      </main>
    </div>
  );
}
