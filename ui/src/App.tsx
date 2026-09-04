import { Suspense, lazy, useCallback, useEffect, useState } from 'react';
import * as shopapi from './lib/shopapi';
import * as authapi from './lib/authapi';
import { Pill } from './components/ui';
import { SideNav, TopBar, routeFromHash, type RouteId } from './components/shell';
import Till from './routes/Till';
import CommandPalette from './components/CommandPalette';
import SalaahkaarFab from './components/SalaahkaarFab';
import { useT } from './lib/i18n';

/**
 * EVERY SCREEN BUT THE COUNTER IS LOADED ON DEMAND.
 *
 * Seventeen routes imported eagerly built a 505 kB bundle, past the 400 kB
 * ceiling this project sets on purpose — "the counter runs on a shop's phone or
 * an old laptop". Only the Till stays eager: it is the route the counter opens
 * on, so it is the one screen that would otherwise show a spinner for its own
 * chunk before the first bill.
 *
 * PRODUCTS WAS EAGER TOO, AND CREPT THE BUNDLE BACK TO 494 kB. It is the hot
 * path — the screen a shopkeeper opens next — but "hot" is an argument for
 * fetching it EARLY, not for parsing it before the first bill is drawn. So it
 * is lazy like the rest and prefetched on the first idle frame after the till
 * mounts: by the time anybody presses Products the chunk is already in cache,
 * and a counter that only ever bills never pays for it at all.
 */
const Products = lazy(() => import('./routes/Products'));
const Shop = lazy(() => import('./routes/Shop'));
/**
 * THE CUSTOMER'S CHROME, around that same storefront. Lazy for a reason the
 * counter cares about and the customer does not: a shopkeeper's browser opens
 * the till and must never pay to parse a shell built for somebody else's
 * phone. It carries `routes/Shop` into its own chunk, which is the module the
 * customer needs anyway and the shopkeeper is already loading lazily above.
 */
const CustomerShell = lazy(() => import('./components/CustomerShell'));
const ShopItems = lazy(() => import('./routes/ShopItems'));
const Orders = lazy(() => import('./routes/Orders'));
const Today = lazy(() => import('./routes/Today'));
const Categories = lazy(() => import('./routes/Categories'));
const Stock = lazy(() => import('./routes/Stock'));
const Customers = lazy(() => import('./routes/Customers'));
const Expenses = lazy(() => import('./routes/Expenses'));
const Purchases = lazy(() => import('./routes/Purchases'));
const DayClose = lazy(() => import('./routes/DayClose'));
const SignIn = lazy(() => import('./routes/SignIn'));
const Gst = lazy(() => import('./routes/Gst'));
/**
 * SALAAHKAAR — one screen where "Ask" and "Salaahkaar" were two. The old
 * ids `assistant` and `advisor` are still routes and are forwarded to it below,
 * so a bookmark or a printed sheet that says #/advisor still arrives.
 */
const Salaahkaar = lazy(() => import('./routes/Salaahkaar'));
const Waapsi = lazy(() => import('./routes/Waapsi'));
const Shelf = lazy(() => import('./routes/Shelf'));
const Expiry = lazy(() => import('./routes/Expiry'));
const Weighed = lazy(() => import('./routes/Weighed'));
const Labels = lazy(() => import('./routes/Labels'));
const Loyalty = lazy(() => import('./routes/Loyalty'));
const Khata = lazy(() => import('./routes/Khata'));
const Insights = lazy(() => import('./routes/Insights'));
const PurchaseOrder = lazy(() => import('./routes/PurchaseOrder'));
const Display = lazy(() => import('./routes/Display'));
const History = lazy(() => import('./routes/History'));
const Inventory = lazy(() => import('./routes/Inventory'));
const Settings = lazy(() => import('./routes/Settings'));
const Offers = lazy(() => import('./routes/Offers'));
const ShopProfile = lazy(() => import('./routes/ShopProfile'));
/**
 * THE ACCOUNT CONTROL. It lives in the top bar rather than the sidebar because
 * `signin` is deliberately not a screen you navigate to among others.
 *
 * Without it there was no door at all: an audit found `#/signin` referenced
 * nowhere in the app except this file's own route switch, so a shopkeeper on a
 * brand-new counter — `accounts: 0`, sign-up open and waiting — could not
 * create the first account without knowing to type the hash by hand. There was
 * no way to sign OUT either.
 */
const AccountMenu = lazy(() => import('./routes/SignIn').then((m) => ({ default: m.AccountMenu })));

/**
 * The route the lock turned away, kept until the sign-in succeeds.
 *
 * Signing in used to leave the shopkeeper on the sign-in screen: the session
 * was set, the cookie was good for twelve hours, and the screen looked exactly
 * as it had before — which reads as "it asked me again" and is why this exists.
 */
export const BOUNCED_FROM = 'gawaah.after.signin.v1';

/**
 * The shell.
 *
 * SEVEN SCREENS UNDER THREE TABS. There were nine once, and seven of those were
 * pages ABOUT the counter rather than the counter. What is left is what a
 * shopkeeper stands in front of, and it is now arranged the way the dashboard
 * they already use is arranged: tabs across a dark bar, and the sidebar for the
 * tab you are in.
 *
 * Route state lives here and nowhere else. The tab is computed from it (see
 * components/shell.tsx) so a deep link into #/inventory cannot land with the
 * wrong tab lit.
 */
export default function App() {
  const { t, tn } = useT();
  const [route, setRoute] = useState<RouteId>(() => routeFromHash(location.hash));
  /** Orders sitting in `new`. A customer who ordered from the shutter QR is
      standing in the future waiting; a count nobody can see is not a queue. */
  const [newOrders, setNewOrders] = useState(0);
  /** Whether anybody is signed in on this browser. Null until the counter has
      answered — "not asked yet" and "nobody" are different states, and only one
      of them should decide what a stranger can see. */
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  /**
   * WHETHER THE COUNTER IS LOCKED, as the server MEASURED it — it walks its own
   * live route tree, so this is true only when the guard is genuinely attached.
   *
   * It is the difference between two questions this browser cannot otherwise
   * tell apart. When the lock is on, "nobody is signed in" is proof that the
   * person holding this browser is not the shopkeeper: a shopkeeper would have
   * a session, and without one every screen but the storefront turns them
   * away. When the lock is off, nobody has a session, so the same fact means
   * nothing and the older heuristic below is all there is.
   */
  const [enforced, setEnforced] = useState<boolean | null>(null);
  /**
   * DID THIS SESSION BEGIN AT THE STOREFRONT?
   *
   * The shutter sticker encodes `<origin>/#/shop` and nothing else — see
   * `store_qr_ep` in gawaah/storefront.py, which refuses to encode any other
   * address. So a stranger's session STARTS on the storefront, while a
   * shopkeeper's starts at the till and reaches the storefront through the
   * sidebar. Read once, at mount, from the hash the page was opened with: it is
   * a fact about how this browser arrived, so it must not change when they
   * navigate (a customer who taps a tab and comes back is still a customer).
   */
  const [beganAtStorefront] = useState(() => routeFromHash(location.hash) === 'shop');
  const [menu, setMenu] = useState(false);

  useEffect(() => {
    const on = () => setRoute(routeFromHash(location.hash));
    addEventListener('hashchange', on);
    return () => removeEventListener('hashchange', on);
  }, []);

  /**
   * PRODUCTS, FETCHED ON THE FIRST IDLE FRAME — see the note above the lazy
   * imports. The counter opens on the till; the next thing a shopkeeper presses
   * is almost always Products, and this puts its chunk in cache while nobody is
   * waiting for it. `requestIdleCallback` where it exists (not Safari), a timer
   * where it does not, and a rejected import is deliberately swallowed: a
   * prefetch that fails must not surface as an error on a screen that is
   * working. The real navigation will ask again and Suspense will show its
   * fallback then, which is the honest place for it.
   */
  useEffect(() => {
    const pull = () => { void import('./routes/Products').catch(() => { }); };
    type Idle = { requestIdleCallback?: (cb: () => void) => number };
    const w = window as Window & Idle;
    if (typeof w.requestIdleCallback === 'function') {
      const id = w.requestIdleCallback(pull);
      return () => (window as Window & { cancelIdleCallback?: (h: number) => void })
        .cancelIdleCallback?.(id);
    }
    const id = setTimeout(pull, 1200);
    return () => clearTimeout(id);
  }, []);

  const go = useCallback((id: RouteId) => {
    location.hash = `#/${id}`;
    setRoute(id);
    setMenu(false);
  }, []);

  /**
   * THE TWO OLD DOORS LEAD TO THE ONE ROOM. `#/assistant` and `#/advisor` were
   * screens; they are now forwards to `#/salaahkaar`, done with `replace` so
   * Back does not bounce between the old hash and the new one.
   */
  useEffect(() => {
    if (route !== 'assistant' && route !== 'advisor') return;
    location.replace(`${location.pathname}${location.search}#/salaahkaar`);
    setRoute('salaahkaar');
  }, [route]);

  /**
   * A LOCKED COUNTER SENDS YOU TO THE DOOR, not to twenty screens of 401s.
   *
   * `enforced` is the server's own measured answer — it walks the live route
   * tree, so it is true only when the guard is genuinely attached, not merely
   * when the environment variable is set. A counter that is locked and not
   * signed in has exactly one useful screen, and this is how a shopkeeper
   * arrives at it. `signin` itself is excluded or this would loop.
   *
   * The same answer carries `signed_in`, which is the only thing this browser
   * can honestly say today about WHO is holding it (see `customerSession`
   * below). It is re-read on `onAuthChanged` as well as on navigation: signing
   * in while standing on the storefront has to change what the bar offers
   * immediately, and a poll would leave the wrong answer up in between.
   */
  useEffect(() => {
    let alive = true;
    const read = async () => {
      const s = await authapi.status();
      if (!alive || !s.ok) return;
      const st = s as unknown as { enforced?: boolean; signed_in?: boolean };
      setSignedIn(!!st.signed_in);
      setEnforced(!!st.enforced);
      // THE STOREFRONT IS NOT THE SHOPKEEPER'S, so the lock does not send a
      // customer to a sign-in form they can never complete.
      //
      // `gawaah/auth.py` leaves `/store` open on purpose — a stranger who
      // scans the shutter QR has no account and never will. When the switch
      // was first turned on, this line redirected EVERY route, so the browser
      // gated the one page the server had deliberately left ungated and the
      // shutter QR led to a locked door. The server is the authority on what
      // is open; this list must not be shorter than its own.
      const OPEN_TO_CUSTOMERS = new Set(['signin', 'shop']);
      if (st.enforced && !st.signed_in && !OPEN_TO_CUSTOMERS.has(route)) {
        // WHERE THEY WERE GOING, so signing in can put them back there rather
        // than leaving them on the form they just completed. sessionStorage,
        // not a query param: a route name in a URL is one more thing to get
        // wrong, and this dies with the tab, which is the right lifetime.
        try { sessionStorage.setItem(BOUNCED_FROM, route); } catch { /* private mode */ }
        go('signin');
      }
    };
    void read();
    const off = authapi.onAuthChanged(() => { void read(); });
    return () => { alive = false; off(); };
  }, [route, go]);

  // A drawer with no way out but a precise tap on the scrim is a trap on a
  // phone held in one hand.
  useEffect(() => {
    if (!menu) return;
    const on = (e: KeyboardEvent) => { if (e.key === 'Escape') setMenu(false); };
    addEventListener('keydown', on);
    return () => removeEventListener('keydown', on);
  }, [menu]);

  /* THE TWO VITALS PILLS ARE GONE FROM THE BAR, AND SO IS THEIR POLLING.
     They used to read `/shop` and `/api/money/health` on every navigation to
     paint "N taught" and "gateway up". Neither fact was lost — the taught count
     is the pill on /#/products, which reads the catalogue it is already showing
     rather than a second copy, and gateway reachability is the Payments section
     of /#/settings, which says considerably more than a two-word chip could
     (mode, which key, whether the webhook secret is set, and what the last
     webhook did). What went with them is two HTTP round-trips per route change
     for facts nobody was reading while they walked. */

  /**
   * IS THE PERSON HOLDING THIS BROWSER A STRANGER?
   *
   * The question the storefront turns on, and it is asked of the PERSON, never
   * of the route. Three terms, in order of how much they actually know:
   *
   *   · `signedIn === true` — somebody holds a counter session here. Only a
   *     shopkeeper can get one; the storefront asks nobody to sign in. This is
   *     the server's own fact and it settles the question outright.
   *   · `enforced` — the lock is on and nobody is signed in. Then there is no
   *     shopkeeper on this browser AT ALL: every screen but the storefront and
   *     the sign-in form turns them away, so a shopkeeper standing here would
   *     have a session or be at the door. This is what fixed the reload hole
   *     the old heuristic admitted to — a shopkeeper who refreshed on `#/shop`
   *     used to look exactly like an arriving customer.
   *   · where the session began — the last resort, for a counter with the lock
   *     OFF, where nobody has a session and the two are otherwise identical.
   *     The shutter sticker encodes `<origin>/#/shop` and nothing else (see
   *     `store_qr_ep`, which refuses to encode any other address), so a
   *     stranger's first screen IS the storefront and a shopkeeper's is the
   *     till.
   *
   * Until `/auth/status` answers, the arrival is all there is — which is the
   * right way to be wrong: the customer's chrome shown for 30 ms to a
   * shopkeeper is a flicker, and the shopkeeper's sidebar shown for 30 ms to a
   * customer is the leak this shell exists to close.
   */
  const stranger =
    signedIn === true ? false
      : signedIn === false ? (enforced === true || beganAtStorefront)
        : beganAtStorefront;

  /** The storefront, on a stranger's phone: their own shell, not the shop's. */
  const customerShell = route === 'shop' && stranger;

  // The order bell. Polled, not pushed — the storefront writes orders over
  // plain HTTP and this page has no socket to it; twenty seconds is fast
  // enough for a shopkeeper to seem responsive and slow enough to cost
  // nothing. The count is `new` only: preparing and out-for-delivery are
  // already being handled, and a badge that counts handled work never reaches
  // zero, which teaches everyone to ignore it.
  //
  // NOT FROM A CUSTOMER'S PHONE. `/orders` is the shopkeeper's own order book
  // — every customer's name, number and address — and it is behind the lock,
  // so a stranger's browser was asking for it every twenty seconds and being
  // refused every twenty seconds. Nothing leaked, because the counter refused;
  // what it cost was a request the customer's phone had no business making and
  // a line in the log for it.
  useEffect(() => {
    if (customerShell) return;
    let alive = true;
    const poll = async () => {
      const r = await shopapi.orders();
      if (alive && r.ok) setNewOrders(r.counts?.new ?? 0);
    };
    void poll();
    const id = setInterval(() => void poll(), 20_000);
    return () => { alive = false; clearInterval(id); };
  }, [customerShell]);

  // The customer display is not a screen inside the shop's chrome — it faces
  // the OTHER way. No sidebar, no top bar, no assistant: a spare window turned
  // toward the customer shows the bill being built and nothing else. The wire
  // is a same-browser channel, so this is a second WINDOW on the counter's own
  // machine, not a second device.
  if (route === 'display') {
    return (
      <Suspense fallback={<div className="route-loading">{t('app.opening')}</div>}>
        <Display />
      </Suspense>
    );
  }

  /**
   * THE STOREFRONT ON A STRANGER'S PHONE GETS A SHELL OF ITS OWN.
   *
   * What this replaces: the customer landed inside the SHOPKEEPER'S chrome —
   * the sidebar offering "Your products / Orders / Customers / Loyalty / Your
   * shop", and COUNTER · SHOP · BOOKS across the top. Every row of it answers
   * their phone with a 401, and the list itself is a map of the shop's admin
   * handed to anybody holding the link. `customerSession` used to be computed
   * here and hid exactly one button.
   *
   * Same shape as the `display` early return above, and for the same reason:
   * this screen is not the counter with a different page in it, it faces the
   * other way. The customer's shell carries the shop's own name and face,
   * three places that are theirs — the shelf, the basket, their orders — and
   * an identity that is a name and a number, never a password.
   *
   * A SIGNED-IN SHOPKEEPER OPENING THE SAME ADDRESS KEEPS THE FULL CHROME,
   * and the "you are previewing your own shop" notice with it: `stranger` is
   * false for them, so this return is not taken and nothing below changes.
   */
  if (customerShell) {
    return (
      <Suspense fallback={<div className="route-loading">{t('app.opening')}</div>}>
        <CustomerShell />
      </Suspense>
    );
  }

  return (
    <div className={`shell${menu ? ' open' : ''}`}>
      <TopBar
        route={route}
        onGo={go}
        onMenu={() => setMenu((v) => !v)}
        menuOpen={menu}
        status={
          <>
            <Suspense fallback={null}>
              <AccountMenu onGo={(r) => go(r as RouteId)} />
            </Suspense>
            {newOrders > 0 && (
              <button className="order-bell" onClick={() => go('orders')}
                      title={t('app.orders.title')}>
                {/* TWO LABELS, ONE SHOWN — the same trick the account chip
                    plays, and for the same measured reason. "3 new orders" is
                    118.8 px of pill, and on a 390 px bar that was what squeezed
                    the brand column until `overflow: hidden` cut GAWAAH in
                    half. The COUNT is the fact; the words are what the button's
                    title already says in full, in every language. */}
                <Pill tone="code" dot>
                  <span className="bell-long">{tn('app.orders', newOrders)}</span>
                  {/* A numeral, not a translated string: it is the same digit in
                      all three languages, and a key whose whole value is a
                      placeholder would be a translation nobody can make. */}
                  <span className="bell-short">{newOrders}</span>
                </Pill>
              </button>
            )}
          </>
        }
      />

      {/* ⌘K / Ctrl-K from anywhere. Mounted OUTSIDE <main> on purpose: it is
          not part of a route and must survive every navigation, and it drives
          navigation itself through the hash rather than through this
          component's state. */}
      <CommandPalette />
      {/* SALAAHKAAR, reachable from every screen: the round button at the
          bottom-right, and the modal it opens. Outside <main> so the call
          survives navigation. It hides itself on #/salaahkaar, where the whole
          screen is the call.
          NO GUARD IS NEEDED HERE ANY MORE. It used to be withheld from a
          CUSTOMER'S session — a shopkeeper's assistant has no business on a
          stranger's phone — by a condition computed in this file. A customer
          now never reaches this shell at all: `customerShell` returns above,
          and it is true of strictly more people than that condition was. */}
      <SalaahkaarFab route={route} />

      <div className="body">
        <SideNav route={route} onGo={go} />
        <div className="scrim" onClick={() => setMenu(false)} aria-hidden="true" />

        <main className="main">
          <Suspense fallback={<div className="route-loading">{t('app.opening')}</div>}>
          {route === 'till' && <Till />}
          {route === 'waapsi' && <Waapsi />}
          {route === 'products' && <Products />}
        {route === 'offers' && <Offers />}
          {route === 'shop' && <Shop />}
        {route === 'shopitems' && <ShopItems />}
          {route === 'orders' && <Orders />}
        {route === 'shopprofile' && <ShopProfile />}
          {route === 'salaahkaar' && <Salaahkaar />}
        {route === 'categories' && <Categories />}
        {route === 'stock' && <Stock />}
        {route === 'customers' && <Customers />}
        {route === 'expenses' && <Expenses />}
        {route === 'purchases' && <Purchases />}
        {route === 'dayclose' && <DayClose />}
        {route === 'signin' && <SignIn />}
        {route === 'gst' && <Gst />}
        {route === 'shelf' && <Shelf />}
        {route === 'expiry' && <Expiry />}
        {route === 'weighed' && <Weighed />}
        {route === 'labels' && <Labels />}
        {route === 'loyalty' && <Loyalty />}
        {route === 'khata' && <Khata />}
        {route === 'insights' && <Insights />}
        {route === 'po' && <PurchaseOrder />}
        {route === 'today' && <Today />}
        {route === 'history' && <History />}
          {route === 'inventory' && <Inventory />}
          {route === 'settings' && <Settings />}
          </Suspense>
        </main>
      </div>
    </div>
  );
}
