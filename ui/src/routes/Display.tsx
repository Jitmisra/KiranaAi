import { useEffect, useRef, useState, type ReactNode } from 'react';
import { paymentQrUrl } from '../lib/api';
import { shopProfile } from '../lib/adminapi';
import { rupees } from '../lib/money';
import {
  current, isStale, subscribe, totalAgrees, type DisplayLine, type DisplayPay, type DisplayState,
} from '../lib/displaybus';
import '../styles/display.css';

/**
 * THE CUSTOMER DISPLAY.
 *
 * A second screen that faces the customer: the bill as it is being built, in
 * type that reads from across the counter, and the payment code when it is
 * time to pay. Every real till has one. This one runs in any spare tab or
 * window of the same browser as the Till — see `lib/displaybus.ts` for the
 * wire, and for the limit (a separate phone is a separate browser).
 *
 * This screen has no controls and makes no requests that change anything. It
 * shows exactly what the Till published and two things it fetches for itself:
 * the shop's name, and the render of the gateway's own payment link from
 * `/qr/link/{session_id}`. It never shows a price the customer is not being
 * charged, never a history, never another product.
 *
 * Nothing on it computes money. The total is the Till's integer total; the
 * display re-adds the lines only to CHECK it, and when the two disagree it
 * says so instead of choosing one.
 */

type Profile =
  | { kind: 'loading' }
  | { kind: 'named'; name: string }
  | { kind: 'unnamed' }
  | { kind: 'unreachable'; reason: string };

/** How often to re-ask the clock whether the last bill has gone stale. */
const TICK_MS = 30_000;

/** How long a payment code may be on its way before the screen admits it is late. */
const QR_SLOW_MS = 12_000;

/**
 * CAN THIS BROWSER CARRY A BILL TO THIS SCREEN AT ALL?
 *
 * The bus is two browser features and the display needs at least one of them.
 * Without either, this screen is not "waiting" — it is deaf, permanently, and
 * the advice it used to give ("open the Till in another tab") would never once
 * have worked. A private window, or a browser set to block site data, is
 * exactly how that happens, and it is silent.
 *
 * localStorage is probed by writing, not by reading: Safari in private mode
 * exposes the object and throws on `setItem`.
 */
type BusReach =
  | { kind: 'ok' }
  /** Live publishes arrive, but a bill already in progress cannot be recovered. */
  | { kind: 'live-only'; reason: string }
  | { kind: 'deaf'; reason: string };

function busReach(): BusReach {
  const live = typeof BroadcastChannel === 'function';
  let stored = true;
  let why = 'this browser refused to store site data';
  try {
    const probe = 'gawaah.display.probe';
    localStorage.setItem(probe, '1');
    localStorage.removeItem(probe);
  } catch (e) {
    stored = false;
    const name = (e as { name?: string } | null)?.name;
    if (name) why = `storage refused: ${name}`;
  }
  if (stored) return { kind: 'ok' };
  if (live) return { kind: 'live-only', reason: why };
  return { kind: 'deaf', reason: `${why}, and this browser has no BroadcastChannel` };
}

export default function Display() {
  const [bus, setBus] = useState<DisplayState | null>(() => current());
  const [profile, setProfile] = useState<Profile>({ kind: 'loading' });
  const [now, setNow] = useState(() => Date.now());
  // Asked once, at mount. Neither transport appears halfway through a shift.
  const [reach] = useState<BusReach>(busReach);

  // Hear the Till. `current()` seeded the first render synchronously, so a
  // display opened mid-bill shows the bill before this effect even runs.
  useEffect(() => subscribe((s) => { setBus(s); setNow(Date.now()); }), []);

  // A stale bill has to expire even if nothing else happens on the page.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(id);
  }, []);

  // The shop's name, from the profile the shopkeeper wrote. Fetched here so
  // the Till does not have to know it; the bus may still carry one, and if it
  // does that wins because it is what the Till is showing.
  useEffect(() => {
    let alive = true;
    void (async () => {
      const r = await shopProfile();
      if (!alive) return;
      if (!r.ok) setProfile({ kind: 'unreachable', reason: r.reason });
      else if (r.profile?.name) setProfile({ kind: 'named', name: r.profile.name });
      else setProfile({ kind: 'unnamed' });
    })();
    return () => { alive = false; };
  }, []);

  // NO CHROME. The class hides the bar, the sidebar, the dock and the palette
  // (see display.css) wherever the shell chose to mount this screen, and the
  // root below is fixed to the viewport regardless. Removed on the way out so
  // navigating back to the Till gets its sidebar back.
  useEffect(() => {
    document.body.classList.add('display-mode');
    return () => document.body.classList.remove('display-mode');
  }, []);

  const stale = bus !== null && isStale(bus, now);
  const phase = bus === null || stale ? 'idle' : bus.phase;
  const shop = bus?.shop ?? (profile.kind === 'named' ? profile.name : null);

  useEffect(() => {
    document.title = shop ? `${shop} — your bill` : 'Your bill';
    return () => { document.title = 'Kirana Shop AI — the counter'; };
  }, [shop]);

  return (
    /* The phase rides on a data attribute, not a class: `disp-pay` and
       `disp-paid` are the names of the panels below, and a phase class on the
       root would hand the panel's type sizes to every line on the screen. */
    <div className="disp" data-phase={phase}>
      <header className="disp-head">
        <ShopMark shop={shop} loading={profile.kind === 'loading' && !bus?.shop} />
        {phase === 'bill' && <span className="disp-head-note">Your bill</span>}
        {phase === 'pay' && <span className="disp-head-note"><span className="disp-dot amber" aria-hidden="true" />Waiting for your payment</span>}
        {phase === 'paid' && <span className="disp-head-note">Paid</span>}
      </header>

      {phase === 'idle' && (
        <Idle
          shop={shop}
          profile={profile}
          reach={reach}
          heard={bus !== null}
          staleMinutes={stale && bus ? Math.max(1, Math.floor((now - bus.at) / 60_000)) : null}
        />
      )}

      {bus && phase === 'bill' && (
        <div className="disp-body">
          <Lines lines={bus.lines} />
          <Total state={bus} />
        </div>
      )}

      {bus && bus.pay && phase === 'pay' && (
        <div className="disp-body disp-body-pay">
          <Lines lines={bus.lines} />
          {/* Keyed on the session so a re-charge gets a fresh image and a
              fresh refusal state rather than the last session's. */}
          <PayPanel key={bus.pay.session_id} pay={bus.pay} total={bus.total_paise} />
        </div>
      )}

      {bus && bus.paid && phase === 'paid' && <Paid amount={bus.paid.amount_paise} />}
    </div>
  );
}

/* ----------------------------------------------------------------- head -- */

function ShopMark({ shop, loading }: { shop: string | null; loading: boolean }) {
  if (shop) return <span className="disp-shop">{shop}</span>;
  if (loading) return <span className="disp-shop skel disp-shop-skel" aria-hidden="true" />;
  return (
    <span className="disp-shop disp-brand">
      KIRANA SHOP AI <span className="disp-brand-deva" lang="hi">किराना शॉप</span>
    </span>
  );
}

/* ----------------------------------------------------------------- idle -- */

/**
 * One note for whoever set this screen up: what is true, then what to do.
 *
 * A bare grey sentence adrift under a huge WELCOME reads as a screen that half
 * failed. Every note here is a titled row with a mark and a next step, and the
 * marks are the on-dark ink — never amber or green, which on this product mean
 * a payment abstained or settled and may not be spent on a setup hint.
 */
function Note({ title, children, tone }: {
  title: string;
  children: ReactNode;
  /** `stop` for a state that will never resolve on its own. Still not red. */
  tone?: 'stop';
}) {
  return (
    <div className={tone === 'stop' ? 'disp-note stop' : 'disp-note'}>
      <span className="disp-note-t">{title}</span>
      <span className="disp-note-b">{children}</span>
    </div>
  );
}

function Idle({ shop, profile, reach, heard, staleMinutes }: {
  shop: string | null;
  profile: Profile;
  reach: BusReach;
  /** False until any Till in this browser has ever published. */
  heard: boolean;
  staleMinutes: number | null;
}) {
  const [fs, setFs] = useState(() => typeof document !== 'undefined' && !!document.fullscreenElement);
  useEffect(() => {
    const on = () => setFs(!!document.fullscreenElement);
    document.addEventListener('fullscreenchange', on);
    return () => document.removeEventListener('fullscreenchange', on);
  }, []);
  const canFs = typeof document !== 'undefined' && !!document.fullscreenEnabled && !fs;

  return (
    <div className="disp-idle">
      <div className="disp-welcome">
        {shop ? (
          <>
            <h1 className="disp-welcome-shop">{shop}</h1>
            <p className="disp-welcome-word">Welcome</p>
          </>
        ) : profile.kind === 'loading' ? (
          <>
            <span className="skel disp-welcome-skel" aria-hidden="true" />
            <p className="disp-welcome-word">Welcome</p>
          </>
        ) : (
          <>
            <h1 className="disp-welcome-shop">Welcome</h1>
            <p className="disp-welcome-word">Your bill will appear here.</p>
          </>
        )}
      </div>

      {/* Notes for the person who set this screen up, not for the customer.
          Each one names a state this screen can actually be in and what fixes
          it. None of them shows a number that is not known. */}
      <div className="disp-notes">
        {/* THE STATE THAT NEVER RESOLVES ITSELF, FIRST. Told to open the Till
            in another tab, somebody in a private window would do it and see
            nothing change, forever. */}
        {reach.kind === 'deaf' ? (
          <Note title="This screen cannot hear the counter" tone="stop">
            Nothing can reach it in this browser — <span className="mono">{reach.reason}</span>.
            Open the display in an ordinary window, or allow site data for this page, and reload.
          </Note>
        ) : (
          <>
            {reach.kind === 'live-only' && (
              <Note title="A bill already in progress will not appear here">
                This browser is not keeping site data (<span className="mono">{reach.reason}</span>),
                so this screen can only be told about changes made from now on. The next line added
                at the counter brings the whole bill with it.
              </Note>
            )}
            {!heard && (
              <Note title="Nothing has reached this screen yet">
                Open the Till in another tab or window of this same browser. The bill appears here
                as it is built — a phone on the shop's Wi-Fi is a different browser and cannot see it.
              </Note>
            )}
            {staleMinutes !== null && (
              <Note title={`The counter last spoke ${staleMinutes} minute${staleMinutes === 1 ? '' : 's'} ago`}>
                That bill is too old to show a customer, so it is not shown. Charge something at the
                Till and this screen follows it again.
              </Note>
            )}
          </>
        )}
        {!shop && profile.kind === 'loading' && (
          <Note title="Reading the shop's name">
            Until it lands, this screen shows the counter's own mark rather than a name it has not read.
          </Note>
        )}
        {!shop && profile.kind === 'unnamed' && (
          <Note title="This shop has no name yet">
            Set one under Shop, Your shop, and it appears here and on every bill this screen shows.
          </Note>
        )}
        {!shop && profile.kind === 'unreachable' && (
          <Note title="The shop's name could not be read">
            <span className="mono">{profile.reason}</span>
            {' '}— the bill itself does not depend on this and will still appear. Check that the
            counter's own server is running.
          </Note>
        )}
        {canFs && (
          <button
            className="disp-fs"
            onClick={() => {
              try { void document.documentElement.requestFullscreen(); } catch { /* not allowed here */ }
            }}
          >
            FULL SCREEN
          </button>
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- lines -- */

function Lines({ lines }: { lines: DisplayLine[] }) {
  const ref = useRef<HTMLDivElement>(null);
  // The newest line lands at the bottom; keep it in view without the
  // customer having to scroll a screen they are not meant to touch.
  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines.length]);

  /**
   * A PAYMENT WITH NO LINES BESIDE IT.
   *
   * `parseState` requires lines for the `bill` phase and deliberately does not
   * for `pay` — a bill cleared under a live link is a real sequence. Rendered
   * as-is that left half the customer's screen empty next to a QR code they
   * were being asked to pay, which reads as a bill that failed to load rather
   * than as a payment with nothing itemised. So it says which it is.
   */
  if (lines.length === 0) {
    return (
      <div className="disp-lines disp-lines-empty" role="list" aria-label="Your bill">
        <p className="disp-nolines">
          No items are itemised for this payment.
          <span>The amount beside this is what the counter is asking for. Please ask at the counter for a breakdown.</span>
        </p>
      </div>
    );
  }

  return (
    <div className="disp-lines" ref={ref} role="list" aria-label="Your bill">
      {lines.map((l) => (
        <div className="disp-line" key={l.sku_id} role="listitem">
          <span className="nm">{l.name}</span>
          <span className="qty tnum">×{l.qty}</span>
          {/* Keyed on the quantity so a changed count re-lands the amount and
              is seen changing, not merely found changed. */}
          <span className="amt tnum" key={l.qty}>
            {l.marked_paise !== undefined && l.marked_paise !== l.price_paise && (
              <s className="was">{rupees(l.marked_paise * l.qty)}</s>
            )}
            {rupees(l.price_paise * l.qty)}
          </span>
        </div>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- total -- */

function Total({ state }: { state: DisplayState }) {
  const n = state.lines.reduce((a, l) => a + l.qty, 0);
  if (!totalAgrees(state)) {
    return (
      <div className="disp-total disp-total-disagree" role="status">
        <span className="lbl">Total</span>
        <span className="disp-cannot">cannot be shown</span>
        <p className="sub">
          The lines above and the total the counter sent do not add up to the same amount.
          Please ask at the counter.
        </p>
      </div>
    );
  }
  return (
    <div className="disp-total" role="status">
      <span className="lbl">Total</span>
      <span className="amt tnum" key={state.total_paise}>{rupees(state.total_paise)}</span>
      <span className="sub">{n} item{n === 1 ? '' : 's'}</span>
    </div>
  );
}

/* ------------------------------------------------------------------ pay -- */

type QrStatus =
  | { kind: 'loading' }
  | { kind: 'shown' }
  | { kind: 'refused'; reason: string; detail: string };

function PayPanel({ pay, total }: { pay: DisplayPay; total: number }) {
  const [qr, setQr] = useState<QrStatus>({ kind: 'loading' });
  /**
   * A REQUEST THAT NEVER ANSWERS FIRES NEITHER `onLoad` NOR `onError`.
   *
   * A hung connection — the commonest way a shop's own machine fails — left a
   * shimmering white square under the words "Scan with any UPI app" with no
   * end and nothing said, in front of a customer holding a phone. After this
   * the screen admits the code is late and gives them somewhere to go.
   */
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const id = setTimeout(() => setSlow(true), QR_SLOW_MS);
    return () => clearTimeout(id);
  }, []);
  const url = paymentQrUrl(pay.session_id);

  return (
    <div className="disp-pay">
      <span className="lbl">To pay</span>
      <span className="amt tnum">{rupees(pay.amount_paise)}</span>
      {pay.amount_paise !== total && (
        /* The Till refuses to mint on a disagreement and paisa refuses again,
           so this should never render. If it ever does, the number the
           gateway will collect is the one above, and the customer is told
           the lines do not explain it rather than shown a second figure. */
        <p className="disp-pay-note">
          This is the amount the gateway will collect. The lines beside it add up differently.
          Please ask at the counter before paying.
        </p>
      )}

      {qr.kind === 'refused' ? (
        <div className="disp-refusal" role="alert">
          <span className="disp-refusal-t">The payment code could not be shown</span>
          <span className="disp-refusal-r mono">{qr.reason}</span>
          {qr.detail && <p>{qr.detail}</p>}
          <p>Please pay at the counter.</p>
        </div>
      ) : (
        <div className={`disp-qr${qr.kind === 'shown' ? ' shown' : ''}`}>
          {/* A render of the link the gateway issued, made by the server from
              the session id. Nothing in this page composes a payment target.
              A larger render than the Till's because this one is read from
              arm's length, and the modules stay crisp at any size. */}
          <img
            src={`${url}?px=900`}
            alt={`Payment code for ${rupees(pay.amount_paise)}`}
            onLoad={() => setQr({ kind: 'shown' })}
            onError={() => {
              // The server answers a refusal as 400 + JSON. Ask it once more,
              // as a plain GET with no side effects, to read the reason.
              void fetch(url, { cache: 'no-store' })
                .then((r) => r.json())
                .then((j: Record<string, unknown>) => setQr({
                  kind: 'refused',
                  reason: String(j.reason ?? 'the payment code could not be produced'),
                  detail: String(j.detail ?? ''),
                }))
                .catch(() => setQr({
                  kind: 'refused',
                  reason: 'the counter could not be reached',
                  detail: 'This screen could not reach its own server to find out why.',
                }));
            }}
          />
        </div>
      )}

      {qr.kind !== 'refused' && (
        <p className="disp-pay-how">
          <span className="disp-dot amber" aria-hidden="true" />
          Scan with any UPI app
        </p>
      )}
      {qr.kind === 'loading' && slow && (
        /* Not amber. Amber on this product means the counter abstained on a
           figure; a code that is merely late is the machine still working, and
           this screen has no accent to spend on it — so it is plain ink. */
        <p className="disp-pay-late" role="status">
          The payment code is taking longer than it should.
          <span>Please ask at the counter if it does not appear.</span>
        </p>
      )}
      {/* The link as text, only once the server has rendered it — that render
          is the allowlist check, and a string it refused is not repeated here
          in a form a phone could type. */}
      {qr.kind === 'shown' && <p className="disp-link mono">{pay.short_url}</p>}
    </div>
  );
}

/* ----------------------------------------------------------------- paid -- */

function Paid({ amount }: { amount: number }) {
  /* Green here is not decoration: the Till publishes `paid` only after a
     signature-verified webhook matched the session and the amount. */
  return (
    <div className="disp-paid" role="status">
      <svg className="disp-paid-check" viewBox="0 0 56 56" aria-hidden="true">
        <circle cx="28" cy="28" r="25.5" />
        <path d="M17.5 29.5 25 37 38.5 21" />
      </svg>
      <span className="disp-paid-word">Paid</span>
      <span className="disp-paid-amt tnum">{rupees(amount)}</span>
      <p>Thank you. The gateway confirmed this payment to the counter.</p>
    </div>
  );
}
