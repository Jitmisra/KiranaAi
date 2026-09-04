import { useCallback, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useT } from '../lib/i18n';
import { useSalaahkaar } from './useSalaahkaar';
import { Chips, Composer, Thread, Tile } from './SalaahkaarCall';
import { IcoX } from './ui';
import '../styles/advisor.css';
import '../styles/salaahkaar.css';

/**
 * THE MODAL behind the round button — the same call as the full page, at a
 * size that sits over whatever screen the shopkeeper was on. Loaded on the
 * first press, never before: the button itself is a face and a ring and
 * nothing heavier.
 *
 * It is a dialog in the strict sense: a scrim, Escape closes it, Tab stays
 * inside, and focus goes back to the button when it goes. The transcript and
 * the session survive its closing (they live in the store); the microphone and
 * the voice do not, which is what the engine's unmount is for.
 */
export default function SalaahkaarPanel({ onClose, returnTo }: {
  onClose: () => void;
  /** The button that opened it, to give focus back to. */
  returnTo: HTMLElement | null;
}) {
  const { t } = useT();
  const e = useSalaahkaar();
  const box = useRef<HTMLDivElement>(null);

  const onKey = useCallback((ev: KeyboardEvent) => {
    if (ev.key === 'Escape') { ev.stopPropagation(); onClose(); return; }
    if (ev.key !== 'Tab' || !box.current) return;
    const items = Array.from(box.current.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input, textarea, select, [tabindex]:not([tabindex="-1"])',
    )).filter((el) => el.offsetParent !== null);
    if (items.length === 0) return;
    const first = items[0]!;
    const last = items[items.length - 1]!;
    if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
  }, [onClose]);

  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      document.body.style.overflow = prev;
      returnTo?.focus?.();
    };
  }, [onKey, returnTo]);

  // THE COMPOSER TAKES FOCUS ONCE IT CAN. On the first open of a page load the
  // box is disabled while the two health checks are in flight, and a focus()
  // on a disabled field does nothing — so this waits for the box to be
  // enabled rather than for the panel to be mounted. Measured: the modal opened
  // with nothing focused every time until this was keyed on the loading flag.
  const canType = !e.healthLoading && !e.healthRefusal;
  const field = e.boxRef;
  useEffect(() => {
    if (!canType) return;
    const id = setTimeout(() => field.current?.focus(), 30);
    return () => clearTimeout(id);
  }, [canType, field]);

  return createPortal(
    <div className="sk-scrim" onMouseDown={(ev) => { if (ev.target === ev.currentTarget) onClose(); }}>
      <div ref={box} className="sk-panel adv" role="dialog" aria-modal="true" aria-label={t('app.salaahkaar.title')} tabIndex={-1}>
        <header className="sk-panel-head">
          <span className="sk-panel-title">
            <b>{t('app.salaahkaar')}</b>
            <span>{e.call.onCall ? 'on the line — the same call as the page' : t('app.salaahkaar.title')}</span>
          </span>
          <a className="sk-panel-full" href="#/salaahkaar" onClick={onClose} title="Open the full page. The call comes with you.">
            full page →
          </a>
          <button type="button" className="modal-x" onClick={onClose} aria-label={t('app.salaahkaar.close')}>
            <IcoX size={15} />
          </button>
        </header>

        <Tile e={e} size="dock" />

        <Thread e={e} size="dock">
          <div className="sk-cold">
            {e.healthRefusal ? (
              <p className="sk-cold-p"><b>{e.healthRefusal.reason}</b> {e.healthRefusal.detail}</p>
            ) : (
              <>
                <p className="sk-cold-p">
                  <b>Ask it anything about the shop</b>, or tell it what to do. A question is answered on the
                  call and read aloud; an instruction comes back as a proposal you press. Nothing here bills.
                </p>
                <Chips chips={e.chips} loading={e.catLoading || e.healthLoading} busy={e.busy} small onPick={(s) => void e.say(s, 'text')} />
              </>
            )}
          </div>
        </Thread>

        {e.micError && <p className="sk-mic-err" role="status">{e.micError}</p>}
        <Composer e={e} size="dock" />
        <p className="sk-panel-foot">
          <span className="sk-dot" data-state={e.presence} aria-hidden="true" />
          {e.presence === 'off' ? 'not on a call' : e.presence === 'idle' ? 'on the line' : e.presence}
          {' · '}
          {e.useNatural ? 'natural voice, one sentence leaves per answer' : 'this browser’s voice, nothing leaves'}
          {' · '}
          a drawing, not a person
          {e.call.onCall && <button type="button" className="sk-hang" onClick={e.hangUp}>HANG UP</button>}
        </p>
      </div>
    </div>,
    document.body,
  );
}
