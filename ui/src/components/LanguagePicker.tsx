import { useEffect, useRef, useState } from 'react';
import { LANGS, applyDocumentLang, useT, type Lang } from '../lib/i18n';
import '../styles/i18n.css';

/**
 * THE LANGUAGE OF THE COUNTER.
 *
 * Three languages, named in their own scripts. A picker that offers "Hindi" in
 * Latin letters is a picker that is useless to the person who needs it most, so
 * every option carries its endonym — हिन्दी, বাংলা — with the English name
 * underneath as the second line rather than the first.
 *
 * Each endonym is marked with its own `lang` attribute. That is not decoration:
 * it is what lets `styles/i18n.css` reach the Devanagari and Bengali faces for
 * those two words even while the rest of the page is in English, and what stops
 * a screen reader pronouncing বাংলা as if it were English.
 *
 * TWO SHAPES, ONE CONTROL. `bar` is a compact button and a menu, for the top
 * bar where a shopkeeper changes this once and never again. `row` is the three
 * options laid out flat, for a settings screen where the choice is the subject
 * of the page rather than a corner of it.
 *
 * This component also OWNS `<html lang>`: it applies the current language on
 * mount, so a counter that opens in Hindi is marked as Hindi before anything
 * is drawn. `setLang` keeps it in step afterwards.
 */

const ICO = {
  width: 15, height: 15, viewBox: '0 0 16 16',
  fill: 'none', stroke: 'currentColor',
  strokeWidth: 1.5, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const,
  'aria-hidden': true, focusable: false,
};

/* A globe: the one glyph that means "language" without being a flag. A flag
   would be wrong twice over — Hindi and Bengali are not countries, and Bengali
   is spoken across two of them. */
const Globe = () => (
  <svg {...ICO}>
    <circle cx="8" cy="8" r="6.2" />
    <path d="M1.8 8h12.4" />
    <path d="M8 1.8c1.7 1.7 2.6 3.8 2.6 6.2S9.7 12.6 8 14.2C6.3 12.6 5.4 10.4 5.4 8S6.3 3.4 8 1.8Z" />
  </svg>
);

const Tick = () => (
  <svg {...ICO} className="langpick-tick"><path d="M3 8.4 6.3 11.6 13 4.8" /></svg>
);

export default function LanguagePicker({ variant = 'bar' }: { variant?: 'bar' | 'row' }) {
  const { lang, setLang, t } = useT();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const first = useRef<HTMLButtonElement>(null);

  // The page's own language, on the element a screen reader and the CSS both
  // read. Runs on mount as well as on change, because a stored choice is
  // already in force before anybody presses anything.
  useEffect(() => { applyDocumentLang(lang); }, [lang]);

  // A menu with no way out but a precise click on the button that opened it is
  // a trap on a phone. Escape closes it and returns focus; a press anywhere
  // outside closes it silently.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        box.current?.querySelector<HTMLButtonElement>('.langpick-btn')?.focus();
      }
    };
    const onDown = (e: PointerEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    addEventListener('keydown', onKey);
    addEventListener('pointerdown', onDown);
    first.current?.focus();
    return () => { removeEventListener('keydown', onKey); removeEventListener('pointerdown', onDown); };
  }, [open]);

  const choose = (id: Lang) => { setLang(id); setOpen(false); };

  /* The flat shape. `radiogroup` and not `menu`: nothing here opens, the three
     options are all visible, and a radio group is what a screen reader should
     be told that is. */
  if (variant === 'row') {
    return (
      <div className="langpick-row" role="radiogroup" aria-label={t('lang.choose')}>
        {LANGS.map((l) => (
          <button
            key={l.id}
            type="button"
            role="radio"
            aria-checked={l.id === lang}
            onClick={() => choose(l.id)}
          >
            <span className="e" lang={l.id}>{l.endonym}</span>
            {l.english !== l.endonym && <span className="n">{l.english}</span>}
            {l.id === lang && <Tick />}
          </button>
        ))}
      </div>
    );
  }

  const now = LANGS.find((l) => l.id === lang) ?? LANGS[0]!;

  return (
    <div className={`langpick${open ? ' open' : ''}`} ref={box}>
      <button
        type="button"
        className="langpick-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`${t('lang.label')} — ${now.english}`}
        title={t('lang.choose')}
        onClick={() => setOpen((v) => !v)}
      >
        <Globe />
        <span className="langpick-now" lang={now.id}>{now.short}</span>
      </button>

      {open && (
        <div className="langpick-menu" role="menu" aria-label={t('lang.choose')}>
          <div className="langpick-h">{t('lang.label')}</div>
          {LANGS.map((l, i) => (
            <button
              key={l.id}
              type="button"
              role="menuitemradio"
              aria-checked={l.id === lang}
              ref={i === 0 ? first : undefined}
              onClick={() => choose(l.id)}
            >
              <span className="e" lang={l.id}>{l.endonym}</span>
              {/* English's endonym IS its English name; a second identical
                  line would read as a stutter. The row keeps its height from
                  a min-height in i18n.css, so the list stays even. */}
              {l.english !== l.endonym && <span className="n">{l.english}</span>}
              {l.id === lang && <Tick />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
