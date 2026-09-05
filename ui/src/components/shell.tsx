import type { ReactNode } from 'react';
import { useT } from '../lib/i18n';
import type { StringKey } from '../lib/i18n';

/**
 * TWO LEVELS OF NAVIGATION.
 *
 * A paper top bar carrying the three halves of the shop, and each half owns
 * the sidebar underneath it. One continuous light chrome: the bar, the
 * sidebar and the page are the same paper divided by hairlines, and the
 * shop's own name sits in the bar beside the product's, because a counter is
 * somebody's counter.
 *
 * The single rule that everything here exists to keep: THE TAB IS DERIVED FROM
 * THE ROUTE AND IS NEVER STORED. Two pieces of state that both claim to know
 * where you are will eventually disagree, and the way they disagree is a deep
 * link — a printed QR pointing at #/inventory would land on Inventory with
 * Counter lit up above it. There is one source of truth and it is the hash.
 *
 * Every route id below is exactly what it has always been. Tests and printed
 * codes depend on those strings, so they are the one thing in this file that
 * is not free to change. So are two class names: `.brand-mark` (its text is
 * asserted to be "GAWAAH") and `.side nav button` with `aria-current="page"`.
 */

export type RouteId =
  | 'till' | 'products' | 'categories' | 'stock' | 'expiry' | 'weighed'
  | 'offers' | 'labels' | 'shelf' | 'salaahkaar' | 'waapsi'
  // The two screens `salaahkaar` replaced. Still route ids — a bookmark, a
  // printed sheet or an old test may still say #/assistant — and App.tsx
  // forwards both to the merged screen. Neither has a sidebar row.
  | 'assistant' | 'advisor'
  | 'shop' | 'shopitems' | 'orders' | 'customers' | 'loyalty' | 'shopprofile'
  | 'today' | 'insights' | 'history' | 'expenses' | 'purchases' | 'po'
  | 'khata'
  | 'gst' | 'dayclose' | 'inventory' | 'settings' | 'signin'
  // Faces the customer; deliberately absent from every sidebar.
  | 'display';

export interface SideItem {
  id: RouteId;
  label: string;
  /** What this screen is for, in a shopkeeper's words. */
  sub: string;
}

export interface Tab {
  id: string;
  label: string;
  blurb: string;
  items: readonly SideItem[];
}

/* `as const satisfies` and not a plain `Tab[]` annotation: it keeps the literal
   tuple types, which is what tells the compiler that a tab always HAS a first
   sidebar item and that `TABS[0]` exists. Otherwise every landing lookup below
   needs a null branch for a case the data makes impossible. */
export const TABS = [
  {
    id: 'counter',
    label: 'Counter',
    blurb: 'what leaves the shelf',
    items: [
      { id: 'till', label: 'Till', sub: 'bill what is on the counter' },
      /* WAAPSI. A return by camera: the packet and the receipt QR, refunded
         through Razorpay, dropped only on a signed webhook. Sits by the till
         because it is the till's other direction. */
      { id: 'waapsi', label: 'Returns', sub: 'a return by camera, refunded by Razorpay' },
      { id: 'products', label: 'Products', sub: 'teach it what things are' },
      { id: 'categories', label: 'Categories', sub: 'where things sit on the shelf' },
      { id: 'stock', label: 'Stock', sub: 'what came in, what went out' },
      { id: 'expiry', label: 'Expiry', sub: 'what goes off, and when' },
      { id: 'weighed', label: 'By weight', sub: 'rice, dal, atta from the sack' },
      { id: 'shelf', label: 'Shelf', sub: 'count the front row with the camera' },
      { id: 'labels', label: 'Labels', sub: 'print a price for the shelf' },
      { id: 'offers', label: 'Offers', sub: 'what comes off the price' },
      /* ONE ROW WHERE THERE WERE TWO. "Ask" (a parser that acted) and
         "Salaahkaar" (a presenter that advised) did overlapping things from
         two screens; this is both, on one call, and the same call is
         behind the round button at the foot of every other screen. */
      { id: 'salaahkaar', label: 'Salaahkaar', sub: 'ask it anything, out loud or typed' },
    ],
  },
  {
    id: 'shop',
    label: 'Shop',
    blurb: 'the customers who are not standing here',
    items: [
      { id: 'shop', label: 'The storefront', sub: 'what a customer sees' },
      /* SECOND, and directly under the storefront it changes. `shop` is the
         page a CUSTOMER lands on from the shutter QR — a shopkeeper who spots
         a wrong price there had nowhere to go and fix it. This is that place:
         add a product, correct a name, change a price, replace a photograph,
         count the shelf. */
      { id: 'shopitems', label: 'Your products', sub: 'add one, fix a price, put a photo on it' },
      { id: 'orders', label: 'Orders', sub: 'what people asked you to send' },
      { id: 'customers', label: 'Customers', sub: 'who buys, and what they spend' },
      { id: 'loyalty', label: 'Loyalty', sub: 'points on money that settled' },
      { id: 'shopprofile', label: 'Your shop', sub: 'name, address, hours' },
    ],
  },
  {
    id: 'books',
    label: 'Books',
    blurb: 'derived from the chain, never a second copy',
    items: [
      { id: 'today', label: 'Today', sub: 'aaj kitna hua — the day, from the chain' },
      { id: 'insights', label: 'Insights', sub: 'what is rising, what is falling' },
      { id: 'history', label: 'History', sub: 'every bill, and what it excluded' },
      { id: 'expenses', label: 'Expenses', sub: 'kharcha, and the cash drawer' },
      { id: 'purchases', label: 'Purchases', sub: 'what you bought, and the margin' },
      /* THE UDHAAR BOOK. The one money flow the till could not see: credit
         written in a notebook. Booked in neutral ink, collected by the
         gateway's own reminders, dropped only on a signed webhook. */
      { id: 'khata', label: 'Khata', sub: 'udhaar on the book, collected by Razorpay' },
      { id: 'po', label: 'Reorder', sub: 'a purchase order from what is short' },
      { id: 'gst', label: 'GST', sub: 'tax-ready records, not a filing' },
      { id: 'dayclose', label: 'Close the day', sub: 'count the cash, freeze the figures' },
      { id: 'inventory', label: 'Inventory', sub: 'what sells and what sits' },
      { id: 'settings', label: 'Settings', sub: 'what this counter is set to do' },
    ],
  },
] as const satisfies readonly Tab[];

export const HOME: RouteId = 'till';

/**
 * Screens that are REACHABLE but not NAVIGABLE — no sidebar row leads to them.
 *
 * `ROUTES` used to be derived from `TABS` alone, which quietly meant "if it is
 * not in a sidebar, it does not exist": `#/display` and `#/signin` both fell
 * through to the till, and a full-page screenshot of either showed the till
 * looking perfectly healthy. Deriving the allow-list from the navigation was
 * the bug — these two are opened deliberately, one by turning a spare window
 * toward the customer and one by arriving unauthenticated, and neither belongs
 * in a list of places to go.
 */
const UNLISTED: readonly RouteId[] = ['display', 'signin', 'assistant', 'advisor'];

const ROUTES: readonly string[] = [
  ...TABS.flatMap((t) => t.items.map((i) => i.id)),
  ...UNLISTED,
];

/** `#/inventory`, `#inventory` and `#/inventory?x=1` all mean Inventory. */
export function routeFromHash(hash: string): RouteId {
  const h = (hash || '').replace(/^#\/?/, '').split(/[?&]/)[0] ?? '';
  return (ROUTES.includes(h) ? h : HOME) as RouteId;
}

/** The tab that owns a route. Total: every RouteId lives in exactly one tab. */
export function tabOfRoute(route: RouteId): Tab {
  return TABS.find((t) => t.items.some((i) => i.id === route)) ?? TABS[0];
}

/* ---------------------------------------------------------------- icons --
   Inline SVG, drawn here rather than imported: the CSP allows no external
   host and this app ships no icon package. `currentColor` throughout, so the
   open row's icon takes the accent from CSS and the rest take ink. One icon
   per route, 16-unit grid, 1.6 stroke — the same grammar as ui.tsx. */

/* 1.65, not 1.5: the icons now sit in their own small tiles in the sidebar,
   and at 1.5 the stroke read as a pencil sketch inside a filled shape. */
const ICO = {
  width: 18, height: 18, viewBox: '0 0 16 16',
  fill: 'none', stroke: 'currentColor',
  strokeWidth: 1.65, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const,
  'aria-hidden': true, focusable: false,
};

const ICONS: Record<RouteId, ReactNode> = {
  // a screen on a stand — it faces the customer, so it is in no sidebar
  display: <svg {...ICO}><path d="M1.8 2.6h12.4v8.2H1.8V2.6Z" /><path d="M6.4 13.4h3.2M8 10.8v2.6" /></svg>,
  // a clock over a box — something with a date on it
  expiry: <svg {...ICO}><path d="M2.4 5.6h11.2v8H2.4v-8Z" /><path d="M2.4 5.6 4 2.4h8l1.6 3.2" /><circle cx="8" cy="9.8" r="2.2" /><path d="M8 8.6v1.4l1 .6" /></svg>,
  // a balance scale
  weighed: <svg {...ICO}><path d="M8 2.2v11.6M4.4 13.8h7.2" /><path d="M2 6.6h12" /><path d="M2 6.6 4.2 11H-.2L2 6.6Z" transform="translate(2)" /><path d="M12 6.6 14.2 11H9.8L12 6.6Z" /></svg>,
  // stacked shelves with items
  shelf: <svg {...ICO}><path d="M2 3.4h12M2 8h12M2 12.6h12" /><path d="M4.4 3.4v-1.2h2.2v1.2M9.4 8V6.8h2.2V8M4.4 12.6v-1.2h2.2v1.2" /></svg>,
  // a printed label with a corner hole
  labels: <svg {...ICO}><path d="M2.4 3.6h11.2v8.8H2.4V3.6Z" /><circle cx="4.6" cy="5.8" r=".7" /><path d="M7 5.4h5M7 8h5M7 10.6h3" /></svg>,
  // a star, for points
  loyalty: <svg {...ICO}><path d="M8 1.8l1.9 3.9 4.3.6-3.1 3 .7 4.3L8 11.6l-3.8 2 .7-4.3-3.1-3 4.3-.6L8 1.8Z" /></svg>,
  // a rising line over bars
  insights: <svg {...ICO}><path d="M2 13.6h12" /><path d="M4 13.6V9M7.3 13.6V6.2M10.7 13.6v-5M14 13.6V3.4" /></svg>,
  // a clipboard with a tick
  po: <svg {...ICO}><path d="M4.4 3h7.2v11H4.4V3Z" /><path d="M6.2 3V1.8h3.6V3" /><path d="M6.4 8.6l1.3 1.3 2.6-2.6" /></svg>,
  // a rupee inside a document
  gst: <svg {...ICO}><path d="M3.4 1.8h6.2l3 3v9.4H3.4V1.8Z" /><path d="M9.6 1.8v3h3" /><path d="M6 7h4M6 8.8h4M9 7c0 2-1.4 2.4-3 1.8l3 2.6" /></svg>,
  // a speaking head with a speech mark — the one call the shop has with its counter
  salaahkaar: <svg {...ICO}><circle cx="6.6" cy="5.6" r="2.5" /><path d="M2 14c.5-2.6 2.2-3.9 4.6-3.9 1 0 1.9.2 2.6.7" /><path d="M10.2 2.4h3.4a1 1 0 0 1 1 1v3.2a1 1 0 0 1-1 1h-1.4L10.6 9V7.6h-.4a1 1 0 0 1-1-1V3.4a1 1 0 0 1 1-1Z" /></svg>,
  // a speaking head — the advisor
  advisor: <svg {...ICO}><circle cx="8" cy="5.4" r="2.6" /><path d="M3 14c.6-2.7 2.5-4 5-4s4.4 1.3 5 4" /></svg>,
  // a receipt
  till: <svg {...ICO}><path d="M3.5 1.8h9v12.4l-1.8-1.2-1.8 1.2-1.8-1.2-1.8 1.2-1.8-1.2V1.8Z" /><path d="M6 5.2h4M6 7.8h4M6 10.4h2.4" /></svg>,
  // an arrow curving back — a return
  waapsi: <svg {...ICO}><path d="M3.4 8.2a5 5 0 1 1 1.7 3.8" /><path d="M3 4.2v4h4" /></svg>,
  // a price tag
  products: <svg {...ICO}><path d="M2 8.2V2.5h5.7l6.3 6.3-5.7 5.7L2 8.2Z" /><circle cx="5.2" cy="5.7" r="1" /></svg>,
  // a shelf grid
  categories: <svg {...ICO}><rect x="2" y="2" width="5" height="5" rx="1" /><rect x="9" y="2" width="5" height="5" rx="1" /><rect x="2" y="9" width="5" height="5" rx="1" /><rect x="9" y="9" width="5" height="5" rx="1" /></svg>,
  // stacked crates
  stock: <svg {...ICO}><path d="M2 5.2 8 2l6 3.2-6 3.2-6-3.2Z" /><path d="M2 8.4 8 11.6l6-3.2" /><path d="M2 11.2 8 14.4l6-3.2" /></svg>,
  // percent
  offers: <svg {...ICO}><path d="M3.2 12.8 12.8 3.2" /><circle cx="4.6" cy="4.6" r="1.7" /><circle cx="11.4" cy="11.4" r="1.7" /></svg>,
  // a speech mark
  assistant: <svg {...ICO}><path d="M2.2 3.6A1.6 1.6 0 0 1 3.8 2h8.4a1.6 1.6 0 0 1 1.6 1.6v6.2a1.6 1.6 0 0 1-1.6 1.6H6.5L3.4 14v-2.6h.4a1.6 1.6 0 0 1-1.6-1.6V3.6Z" /><path d="M5.5 6.4h5M5.5 8.6h3" /></svg>,
  // an awning
  shop: <svg {...ICO}><path d="M2 6.5 3.2 2.5h9.6L14 6.5" /><path d="M2 6.5c0 1 .9 1.8 2 1.8s2-.8 2-1.8c0 1 .9 1.8 2 1.8s2-.8 2-1.8c0 1 .9 1.8 2 1.8s2-.8 2-1.8" /><path d="M3.2 8.3v5.2h9.6V8.3" /><path d="M6.4 13.5V10h3.2v3.5" /></svg>,
  // A packet with a price tag on it: the shopkeeper's own side of the shelf.
  shopitems: <svg {...ICO}><rect x="2" y="4.6" width="8.4" height="8.8" rx="1.2" /><path d="M2 7.4h8.4" /><path d="M8.6 2.6h5.2v4.2" /><circle cx="12" cy="4.4" r=".8" /></svg>,
  // a parcel
  orders: <svg {...ICO}><path d="M2.4 5 8 2.2 13.6 5v6L8 13.8 2.4 11V5Z" /><path d="M2.4 5 8 7.8 13.6 5M8 7.8v6" /></svg>,
  // two people
  customers: <svg {...ICO}><circle cx="6" cy="5" r="2.3" /><path d="M1.8 13.4c.4-2.7 2-4.1 4.2-4.1s3.8 1.4 4.2 4.1" /><path d="M10.3 3.2a2.2 2.2 0 0 1 0 3.8" /><path d="M11.4 9.5c1.6.3 2.6 1.6 2.9 3.9" /></svg>,
  // a signboard
  shopprofile: <svg {...ICO}><rect x="2" y="3" width="12" height="8" rx="1.2" /><path d="M8 11v3M5 14h6" /><path d="M5 6.3h6M5 8.3h3.5" /></svg>,
  // the sun
  today: <svg {...ICO}><circle cx="8" cy="8" r="2.8" /><path d="M8 1.6v1.8M8 12.6v1.8M1.6 8h1.8M12.6 8h1.8M3.5 3.5l1.3 1.3M11.2 11.2l1.3 1.3M3.5 12.5l1.3-1.3M11.2 4.8l1.3-1.3" /></svg>,
  // a clock
  history: <svg {...ICO}><circle cx="8" cy="8" r="6.2" /><path d="M8 4.4V8l2.4 1.5" /></svg>,
  // a wallet
  expenses: <svg {...ICO}><path d="M2 4.8A1.8 1.8 0 0 1 3.8 3h8.4v2" /><rect x="2" y="5" width="12" height="8.6" rx="1.5" /><path d="M10.2 9.3h3.8" /><circle cx="10.4" cy="9.3" r=".6" fill="currentColor" /></svg>,
  // a crate coming in
  purchases: <svg {...ICO}><path d="M2.5 7.5h11v6h-11v-6Z" /><path d="M8 2v4M5.8 4.2 8 6.4l2.2-2.2" /><path d="M6.2 10.5h3.6" /></svg>,
  // a bound notebook with ruled lines — the udhaar book
  khata: <svg {...ICO}><path d="M3.6 2.2h8.8v11.6H3.6V2.2Z" /><path d="M3.6 4.6h-1.2M3.6 8h-1.2M3.6 11.4h-1.2" /><path d="M6.2 5.6h3.8M6.2 8h3.8M6.2 10.4h2.4" /></svg>,
  // a padlock
  dayclose: <svg {...ICO}><rect x="3" y="7" width="10" height="7" rx="1.5" /><path d="M5.3 7V5a2.7 2.7 0 0 1 5.4 0v2" /><path d="M8 10v1.6" /></svg>,
  // bars
  inventory: <svg {...ICO}><path d="M2.5 13.5h11" /><path d="M4 13.5V8.5M7 13.5V4M10 13.5V6.5M13 13.5V3" /></svg>,
  // sliders
  settings: <svg {...ICO}><path d="M2.5 4.5h7M12.5 4.5h1M2.5 11.5h1M6.5 11.5h7" /><circle cx="10.8" cy="4.5" r="1.7" /><circle cx="4.8" cy="11.5" r="1.7" /></svg>,
  // a key
  signin: <svg {...ICO}><circle cx="5.5" cy="10.5" r="3" /><path d="M7.7 8.3 13.5 2.5M11 5l2 2M9.3 6.7l1.6 1.6" /></svg>,
};

/**
 * The mark. A rounded square in the accent gradient, a slanted white FLUTE
 * across it — the RazorSense glyph geometry — and the witness's dot on the
 * flute where an eye would be. Two ideas in one shape: the bar that Razorpay
 * is drawn from, and the lens this counter looks through.
 */
export function BrandGlyph({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id="gw-brand" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#3E9BFF" />
          <stop offset="1" stopColor="#1B66D9" />
        </linearGradient>
      </defs>
      <rect x="1" y="1" width="22" height="22" rx="7.5" fill="url(#gw-brand)" />
      {/* The flute: a parallelogram, skewed the same 18° every flute on the
          product is skewed. */}
      <path d="M9.4 5.5h5.2L12.2 18.5H7Z" fill="#fff" opacity=".96" />
      {/* The witness's eye, sitting on the flute. */}
      <circle cx="11.1" cy="12" r="1.7" fill="#1B66D9" />
    </svg>
  );
}

/**
 * A decorative flute, for a page or a card that wants the mark without a
 * title before it. `n` bars, stepped, in the accent gradient.
 */
export function Flute({ n = 3, height = 18 }: { n?: number; height?: number }) {
  const w = n * 9 + 4;
  return (
    <svg width={w} height={height} viewBox={`0 0 ${w} ${height}`} aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id="gw-flute" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#3E9BFF" />
          <stop offset="1" stopColor="#1B66D9" />
        </linearGradient>
      </defs>
      {Array.from({ length: n }, (_, i) => (
        <path
          key={i}
          d={`M${i * 9 + 4} 0h5L${i * 9 + 3} ${height}h-5Z`}
          fill="url(#gw-flute)"
          opacity={1 - i * 0.28}
        />
      ))}
    </svg>
  );
}

/* ------------------------------------------------------------- top bar -- */

export function TopBar({ route, onGo, onMenu, menuOpen, status, shopName }: {
  route: RouteId;
  onGo: (id: RouteId) => void;
  onMenu: () => void;
  menuOpen: boolean;
  /**
   * The right-hand end of the bar: the account, the assistant's trigger, and a
   * waiting-order bell when there is one.
   *
   * It used to carry two more chips — the taught count and gateway health — and
   * the bar was the wrong place for both. A number that moves once a day and a
   * service state that has a whole card about it on /#/settings were sitting in
   * permanent chrome, next to the two controls a shopkeeper actually presses,
   * and at 390 px they were what pushed the account chip across the wordmark.
   * What is here now is what changes while somebody is standing at the counter.
   */
  status?: ReactNode;
  /**
   * The shop's own name, from its profile. Optional: a counter that has not
   * been told whose it is shows the product's name alone rather than a
   * placeholder pretending to be a shop.
   */
  shopName?: string | null;
}) {
  // `tr`, not `t`: `t` is the tab in the loops below.
  const { t: tr } = useT();
  const here = tabOfRoute(route);
  const shop = (shopName ?? '').trim();
  return (
    <header className="topbar">
      <div className="topbar-in">
        <button
          className="side-toggle"
          onClick={onMenu}
          aria-label={tr('nav.menu')}
          aria-expanded={menuOpen}
        >
          <span /><span /><span />
        </button>

        <a
          className="brand"
          href={`#/${HOME}`}
          onClick={(e) => { e.preventDefault(); onGo(HOME); }}
        >
          <span className="brand-glyph"><BrandGlyph /></span>
          <span className="brand-mark">KIRANA SHOP AI</span>
          <span className="brand-deva" lang="hi">किराना शॉप</span>
          {shop && <span className="brand-shop" title={shop}>{shop}</span>}
        </a>

        <nav className="tabs" aria-label="Sections">
          {TABS.map((t) => (
            <button
              key={t.id}
              title={tr(`nav.tab.${t.id}.blurb` as StringKey)}
              // Landing on the first item is right when you ARRIVE at a tab and
              // wrong when you are already inside it: pressing Books from
              // Inventory would throw away the screen you are reading.
              onClick={() => { if (t.id !== here.id) onGo(t.items[0].id); }}
              aria-current={t.id === here.id ? 'true' : undefined}
            >
              {tr(`nav.tab.${t.id}` as StringKey)}
            </button>
          ))}
        </nav>

        {status && <div className="topbar-status">{status}</div>}
      </div>
    </header>
  );
}

/* ------------------------------------------------------------- sidebar -- */

export function SideNav({ route, onGo }: { route: RouteId; onGo: (id: RouteId) => void }) {
  const { t: tr, tx } = useT();
  const here = tabOfRoute(route);
  return (
    <aside className="side">
      {/* `aria-label` names the tab, because the whole list changes underneath
          the tabs and a screen reader otherwise hears seven items with no
          account of which three of them are on screen. */}
      <nav aria-label={tr(`nav.tab.${here.id}` as StringKey)}>
        <div className="navgroup">
          <div className="navgroup-h">
            {tr(`nav.tab.${here.id}` as StringKey)}
            <span>{tr(`nav.tab.${here.id}.blurb` as StringKey)}</span>
          </div>
          {here.items.map((it) => (
            <button
              key={it.id}
              onClick={() => onGo(it.id)}
              aria-current={route === it.id ? 'page' : undefined}
            >
              <span className="i">{ICONS[it.id]}</span>
              <span className="t">
                <span className="l">{tr(`nav.${it.id}` as StringKey)}</span>
                <span className="s">{tr(`nav.${it.id}.sub` as StringKey)}</span>
              </span>
            </button>
          ))}
        </div>
      </nav>

      {/* The other halves of the shop, one press away. Outside the <nav> on
          purpose: the e2e suite addresses a sidebar item as `.side nav button`
          by its label, and "Shop" must not match a section switch. */}
      <div className="side-sections" role="group" aria-label={tr('nav.sections')}>
        {TABS.map((t) => (
          <button
            key={t.id}
            title={tr(`nav.tab.${t.id}.blurb` as StringKey)}
            onClick={() => { if (t.id !== here.id) onGo(t.items[0].id); }}
            aria-current={t.id === here.id ? 'true' : undefined}
          >
            {tr(`nav.tab.${t.id}` as StringKey)}
          </button>
        ))}
      </div>

      {/* The line the whole product is named for, in the shopkeeper's own
          language. `tx` because the second sentence is bold in all three. */}
      <p className="brand-line">{tx('nav.brandline')}</p>
    </aside>
  );
}
