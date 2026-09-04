import { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react';
import { useT } from '../lib/i18n';
import { useCall } from '../lib/salaahkaar-store';
import type { RouteId } from './shell';
import plate from '../lib/presenter/advisor.webp';
import '../styles/salaahkaar-fab.css';

/**
 * THE ROUND BUTTON — Salaahkaar's face, small, at the bottom-right of every
 * shopkeeper screen, wearing the live state of the call as a ring.
 *
 * ONE ENTRY POINT. This replaces the "Ask" trigger in the top bar and the chat
 * dock behind it; the sidebar row and this button open the same call, and the
 * page and the modal share one transcript and one session.
 *
 * WHERE IT IS NOT: the customer's storefront in a customer's session
 * (`App.tsx` decides that — person, not route), the customer display (no
 * chrome at all), and the Salaahkaar page itself, where the whole screen is
 * the call and a second door to it would be a door painted on a wall.
 *
 * THE CHARGE BUTTON. The last floating launcher on this product was measured
 * sitting squarely on the till's CHARGE button at 1440 — the single most
 * important control on the counter — and was moved into the chrome for it.
 * This one stays in the corner and MOVES OUT OF THE WAY instead: on the till
 * it measures `.till .btn.pay` on every scroll and resize, and if the two
 * rectangles would touch it lifts itself above the button by exactly the
 * overlap. Measured, not assumed: the e2e script scrolls the till through its
 * whole height and asserts the two never intersect.
 */

const Panel = lazy(() => import('./SalaahkaarPanel'));

/**
 * What this button must never cover, and the gap it keeps.
 *
 * It was `.till .btn.pay` — CHARGE, and only CHARGE, and only on the till. A
 * verifier measured the fab sitting on the corner of READ THE WHOLE COUNTER
 * at 390 px, on a product `<select>` on Stock, on two customer rows, on the
 * scope control on Offers — every one at scroll 0 on a short page. The rule
 * is not "keep off CHARGE"; it is "keep off anything a thumb is about to
 * press". So: every interactive element, on every route the fab shows.
 * The lift is capped, because a long list can always put one more control
 * under the corner and a button that climbs the screen is its own problem.
 */
const DODGE_SELECTOR = 'button, select, input, textarea, a.btn, [role="button"]';
const DODGE_GAP = 12;
const DODGE_MAX_LIFT = 160;

export default function SalaahkaarFab({ route }: { route: RouteId }) {
  const { t } = useT();
  const call = useCall();
  const [open, setOpen] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const [lift, setLift] = useState(0);
  // Stable, so the panel's mount effect does not re-run on every tick of
  // the store — it hands focus back to this button in its cleanup.
  const close = useCallback(() => setOpen(false), []);

  // The panel's chunk, fetched on idle so the first press opens at once.
  useEffect(() => {
    const w = window as Window & { requestIdleCallback?: (cb: () => void) => number };
    const fetchIt = () => { void import('./SalaahkaarPanel'); };
    if (typeof w.requestIdleCallback === 'function') w.requestIdleCallback(fetchIt);
    else setTimeout(fetchIt, 1500);
  }, []);

  /**
   * THE DODGE. On every route the fab shows, while closed. rAF-throttled: a scroll
   * fires many times a frame on a trackpad, and one measurement per frame is
   * plenty for a button that moves 60 px at most.
   */
  const measure = useCallback(() => {
    const el = btn.current;
    if (!el) return;
    // Where the button RESTS, with no lift. `offset*` ignores transforms —
    // `getBoundingClientRect` does not, and reading the lifted rect and adding
    // the lift back was measured chasing its own tail one scroll step behind.
    // A fixed element's offsetParent is null, so these are viewport numbers.
    const restTop = el.offsetTop;
    const restBottom = restTop + el.offsetHeight;
    const left = el.offsetLeft;
    const right = left + el.offsetWidth;
    // Every control drawn on screen that is NOT a full-width row. A list row
    // that spans the viewport (a customer, an order) is left alone: a button
    // in the corner of a full-width row is the convention every phone app
    // uses, the row stays tappable across the other 85% of its width, and a
    // list can always put one more row under the corner — lifting off it
    // just lands on the next. The cap below exists for the same reason.
    const rects: DOMRect[] = [];
    for (const c of document.querySelectorAll<HTMLElement>(DODGE_SELECTOR)) {
      if (c === el || el.contains(c)) continue;
      const p = c.getBoundingClientRect();
      if (p.width === 0 || p.height === 0) continue;               // not drawn
      if (p.bottom < 0 || p.top > window.innerHeight) continue;    // off screen
      if (p.width > window.innerWidth * 0.8) continue;             // a row
      rects.push(p);
    }
    // ITERATE. Lifting clear of one control can land on the one above it —
    // measured on Customers at 390: lifted 86 px, still on a row. So the
    // lift is re-checked at its new position until nothing is under it, or
    // the cap is reached.
    let lift = 0;
    for (let pass = 0; pass < 6; pass++) {
      let need = 0;
      for (const p of rects) {
        const xOverlap = left < p.right + DODGE_GAP && right > p.left - DODGE_GAP;
        const yOverlap = restTop - lift < p.bottom + DODGE_GAP && restBottom - lift > p.top - DODGE_GAP;
        if (xOverlap && yOverlap) need = Math.max(need, Math.ceil(restBottom - lift - p.top + DODGE_GAP));
      }
      if (need === 0) break;
      lift = Math.min(DODGE_MAX_LIFT, lift + need);
      if (lift >= DODGE_MAX_LIFT) break;
    }
    setLift(lift);
  }, []);

  useEffect(() => {
    if (open) { setLift(0); return; }
    let raf = 0;
    const on = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; measure(); }); };
    on();
    // The till's layout settles after its own fetches; look again a few times.
    const late = [200, 800, 2000].map((ms) => setTimeout(on, ms));
    addEventListener('scroll', on, { passive: true });
    addEventListener('resize', on);
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(on) : null;
    ro?.observe(document.body);
    return () => {
      cancelAnimationFrame(raf);
      late.forEach(clearTimeout);
      removeEventListener('scroll', on);
      removeEventListener('resize', on);
      ro?.disconnect();
    };
  }, [route, open, measure]);

  // Not on the page that IS the call, and not at the locked counter's door:
  // nobody is signed in on #/signin, and a modal that opens onto a 401 is a
  // face announcing a locked room.
  // NOT ON THE TILL EITHER. "Say the order" IS Salaahkaar there — her tile,
  // her voice, questions answered, lines proposed onto the bill — so a second
  // face in the corner was the same person twice on one screen.
  if (route === 'salaahkaar' || route === 'signin' || route === 'till') return null;

  const presence = open ? call.presence : call.onCall ? 'idle' : 'off';

  return (
    <>
      <button
        ref={btn}
        type="button"
        className="sk-fab"
        data-state={presence}
        data-lifted={lift > 0 ? 'true' : undefined}
        style={lift > 0 ? ({ '--sk-lift': `${lift}px` } as React.CSSProperties) : undefined}
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={t('app.salaahkaar')}
        title={t('app.salaahkaar.title')}
      >
        <span className="sk-fab-ring" aria-hidden="true" />
        <span className="sk-fab-face" aria-hidden="true">
          <img src={plate} alt="" draggable={false} />
        </span>
        {call.turns.length > 0 && !open && (
          <span className="sk-fab-n tnum" aria-hidden="true">{Math.min(99, call.turns.filter((x) => x.who === 'sk').length)}</span>
        )}
      </button>
      {open && (
        <Suspense fallback={null}>
          <Panel onClose={close} returnTo={btn.current} />
        </Suspense>
      )}
    </>
  );
}
