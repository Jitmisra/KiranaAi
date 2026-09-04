import {
  forwardRef, useCallback, useEffect, useId, useRef, useState, useSyncExternalStore,
} from 'react';
import { createPortal } from 'react-dom';
import type {
  ButtonHTMLAttributes, CSSProperties, InputHTMLAttributes, ReactNode,
  SelectHTMLAttributes, TextareaHTMLAttributes,
} from 'react';

/**
 * THE SHARED LAYER.
 *
 * Small, dumb presentational pieces. No fetch, no polling, no decisions about
 * money. Every one of these is styled by `styles/app.css` against a class name
 * that is now load-bearing: twenty screens and several e2e specs reference
 * `.card`, `.btn.pay`, `.verdict`, `.bill-line`. NOTHING HERE IS RENAMED. New
 * props are added optional, so every existing call site keeps working.
 *
 * Three rules run through the whole file:
 *
 *  1. GREEN, AMBER AND RED MEAN MONEY AND RECOGNITION. A saved setting is not
 *     green; a switch that is on is not green; a selected tab is not green.
 *     The accent (indigo — the `--blue-*` tokens, kept by name) is action and
 *     the machine's own mark; ink on paper is structure. On this product a
 *     green thing means a payment actually settled, and nothing decorative
 *     may borrow that.
 *  2. EVERY PANEL HAS THREE STATES. Loading is a `Skeleton` at the shape of
 *     what is coming, empty is an `Empty` that says what would put something
 *     there, and a server refusal is a `Refusal` that keeps the server's own
 *     words. Nothing renders blank while it waits.
 *  3. NOTHING HERE AUTHORS ANYTHING. These components display what they are
 *     given. No component computes a price, a total or a payable string.
 */

/* ========================================================================== *
 * ICONS
 * Inline SVG, drawn here rather than imported: the CSP allows no external host
 * and this app ships no icon package. `currentColor` throughout, so an icon
 * takes the colour of whatever it is put inside and never needs a tone prop.
 * ========================================================================== */

type IcoProps = { size?: number; className?: string };
const svg = (size: number, className?: string) => ({
  width: size, height: size, viewBox: '0 0 16 16',
  fill: 'none', stroke: 'currentColor',
  strokeWidth: 1.6, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const,
  'aria-hidden': true, focusable: false, className,
});

export function IcoCheck({ size = 16, className }: IcoProps) {
  return <svg {...svg(size, className)}><circle cx="8" cy="8" r="6.4" /><path d="m5.4 8.2 1.8 1.8 3.4-3.9" /></svg>;
}
export function IcoWarn({ size = 16, className }: IcoProps) {
  return <svg {...svg(size, className)}><path d="M8 1.9 1.6 13.4h12.8L8 1.9Z" /><path d="M8 6.3v3.1" /><path d="M8 11.5h.01" /></svg>;
}
export function IcoStop({ size = 16, className }: IcoProps) {
  return <svg {...svg(size, className)}><circle cx="8" cy="8" r="6.4" /><path d="M5.7 5.7 10.3 10.3" /><path d="M10.3 5.7 5.7 10.3" /></svg>;
}
export function IcoInfo({ size = 16, className }: IcoProps) {
  return <svg {...svg(size, className)}><circle cx="8" cy="8" r="6.4" /><path d="M8 7.4v3.6" /><path d="M8 5.1h.01" /></svg>;
}
export function IcoX({ size = 16, className }: IcoProps) {
  return <svg {...svg(size, className)}><path d="M3.8 3.8 12.2 12.2" /><path d="M12.2 3.8 3.8 12.2" /></svg>;
}
/** The empty state's mark: an open tray. Never a verdict colour. */
export function IcoTray({ size = 16, className }: IcoProps) {
  return (
    <svg {...svg(size, className)}>
      <path d="M2 9.5h3l1 2h4l1-2h3" />
      <path d="M3.4 3.2h9.2l1.4 6.3v3H1.4v-3l1.4-6.3Z" />
    </svg>
  );
}

/* Drawn marks for the cards that carry one. Same grammar as the six above:
   16-unit grid, 1.6 stroke, currentColor. */
export function IcoReceipt({ size = 18, className }: IcoProps) {
  return (
    <svg {...svg(size, className)}>
      <path d="M3.5 1.8h9v12.4l-1.8-1.2-1.8 1.2-1.8-1.2-1.8 1.2-1.8-1.2V1.8Z" />
      <path d="M6 5.2h4M6 7.8h4M6 10.4h2.4" />
    </svg>
  );
}
export function IcoMic({ size = 18, className }: IcoProps) {
  return (
    <svg {...svg(size, className)}>
      <rect x="5.6" y="1.8" width="4.8" height="7.6" rx="2.4" />
      <path d="M3.4 7.6a4.6 4.6 0 0 0 9.2 0M8 12.2v2M5.8 14.2h4.4" />
    </svg>
  );
}
export function IcoCamera({ size = 18, className }: IcoProps) {
  return (
    <svg {...svg(size, className)}>
      <path d="M2 5.2h2.6L6 3.2h4l1.4 2H14v8H2v-8Z" />
      <circle cx="8" cy="9" r="2.4" />
    </svg>
  );
}
export function IcoTag({ size = 18, className }: IcoProps) {
  return (
    <svg {...svg(size, className)}>
      <path d="M2 8.2V2.5h5.7l6.3 6.3-5.7 5.7L2 8.2Z" />
      <circle cx="5.2" cy="5.7" r="1" />
    </svg>
  );
}
export function IcoParcel({ size = 18, className }: IcoProps) {
  return (
    <svg {...svg(size, className)}>
      <path d="M2.4 5 8 2.2 13.6 5v6L8 13.8 2.4 11V5Z" />
      <path d="M2.4 5 8 7.8 13.6 5M8 7.8v6" />
    </svg>
  );
}
export function IcoSpark({ size = 18, className }: IcoProps) {
  return (
    <svg {...svg(size, className)}>
      <path d="M8 1.8l1.6 4.2 4.2 1.6-4.2 1.6L8 13.4l-1.6-4.2-4.2-1.6 4.2-1.6L8 1.8Z" />
    </svg>
  );
}

/* ========================================================================== *
 * SURFACES
 * ========================================================================== */

export function Card({ title, sub, icon, aside, children, tight, flush, clip, foot, className }: {
  title?: ReactNode;
  /** The second line of the title: what this panel is for, in a shopkeeper's words. */
  sub?: ReactNode;
  /** A drawn mark before the title, in a soft blue tile. For the card a screen
      is about; the five beside it do not need one. */
  icon?: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  tight?: boolean;
  /** No body padding at all — for a card whose body is a table or a full-bleed list. */
  flush?: boolean;
  /** Clip the body to the card's radius. Opt-in, because a card is the usual
      parent of a tooltip and clipping swallows it. */
  clip?: boolean;
  foot?: ReactNode;
  className?: string;
}) {
  const body = ['card-body', tight ? 'tight' : '', flush ? 'flush' : ''].filter(Boolean).join(' ');
  return (
    <section className={['card', clip ? 'clip' : '', className ?? ''].filter(Boolean).join(' ')}>
      {title !== undefined && (
        <header className="card-head">
          {icon !== undefined && <span className="card-ico" aria-hidden="true">{icon}</span>}
          {sub === undefined ? <h2>{title}</h2> : (
            <div className="card-title">
              <h2>{title}</h2>
              <span className="card-sub">{sub}</span>
            </div>
          )}
          <div className="spacer" />
          {aside}
        </header>
      )}
      <div className={body}>{children}</div>
      {foot !== undefined && <div className="card-foot">{foot}</div>}
    </section>
  );
}

/** A named division of a long screen: a small-caps label and a rule. */
export function SectionHead({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="sectionhead">
      <span className="eyebrow">{children}</span>
      <span className="line" />
      {aside}
    </div>
  );
}

export function Divider() {
  return <hr className="hr" />;
}

/** A row of controls above a list, all on one baseline. */
export function Toolbar({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={['toolbar', className ?? ''].filter(Boolean).join(' ')}>{children}</div>;
}

/* ========================================================================== *
 * BUTTON
 * ========================================================================== */

export type ButtonVariant = 'default' | 'primary' | 'pay' | 'ghost' | 'quiet' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /**
   * The request is in flight. The label is kept in place and hidden rather
   * than replaced, so the button does not change width mid-press and shove
   * whatever is beside it — and the button is disabled, because a second press
   * on a charge is a second payment link.
   */
  loading?: boolean;
  block?: boolean;
  icon?: ReactNode;
  /** A square button holding one glyph. Give it an `aria-label`. */
  iconOnly?: boolean;
  children?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'default', size = 'md', loading, block, icon, iconOnly, className,
    children, disabled, type = 'button', ...rest }, ref,
) {
  const cls = [
    'btn',
    variant === 'default' ? '' : variant,
    size === 'md' ? '' : size,
    block ? 'block' : '',
    iconOnly ? 'iconly' : '',
    loading ? 'loading' : '',
    className ?? '',
  ].filter(Boolean).join(' ');
  return (
    <button
      ref={ref}
      type={type}
      className={cls}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {/* Both children are ELEMENTS, always. `.btn.loading > *` is what hides
          the label while the ring spins, and a bare text node is not a `*`. */}
      {icon !== undefined && <span className="btn-ico">{icon}</span>}
      <span className="btn-label">{children}</span>
    </button>
  );
});

/* ========================================================================== *
 * STATUS
 * ========================================================================== */

export type PillTone = 'ok' | 'amb' | 'bad' | 'code' | 'off';

export function Pill({ tone = 'off', dot, lg, children }: {
  tone?: PillTone; dot?: boolean; lg?: boolean; children: ReactNode;
}) {
  return (
    <span className={['pill', tone, lg ? 'lg' : ''].filter(Boolean).join(' ')}>
      {dot && <span className="dot" />}
      {children}
    </span>
  );
}

export type VerdictTone = 'green' | 'amber' | 'red' | 'info';

const VERDICT_ICON: Record<VerdictTone, () => ReactNode> = {
  green: () => <IcoCheck size={17} />,
  amber: () => <IcoWarn size={17} />,
  red: () => <IcoStop size={17} />,
  info: () => <IcoInfo size={17} />,
};

/**
 * What the counter decided, and why.
 *
 * THE ICON IS A SIBLING OF THE <h4>, never inside it. `.verdict h4` is asserted
 * on by text in the e2e suite — one spec requires the list of those headings to
 * be exactly empty at a point in the payment flow — and a glyph parked in that
 * heading would be read as part of the machine's own words.
 */
export function Verdict({ tone, title, icon, children }: {
  tone: VerdictTone;
  title: ReactNode;
  /** Pass `false` for a verdict that is only a sentence. */
  icon?: ReactNode | false;
  children?: ReactNode;
}) {
  const mark = icon === false ? null : icon ?? VERDICT_ICON[tone]();
  return (
    <div className={`verdict ${tone}`} role={tone === 'red' ? 'alert' : 'status'}>
      {mark && <span className="v-ico">{mark}</span>}
      <div className="v-main">
        <h4>{title}</h4>
        {children && <p>{children}</p>}
      </div>
    </div>
  );
}

export function KV({ k, children }: { k: ReactNode; children: ReactNode }) {
  return (
    <div className="kv">
      <b>{k}</b>
      <span>{children}</span>
    </div>
  );
}

/**
 * One figure, what it is, and what qualifies it.
 *
 * `sub` is not decoration. A number on this product without its window and its
 * source is exactly the confident figure the counter exists to refuse to print,
 * so the qualifier has a place reserved for it.
 */
export function Stat({ label, value, sub, tone, sm, className }: {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  /** Money and recognition only. `unknown` is for a figure that was abstained on. */
  tone?: 'green' | 'amber' | 'red' | 'unknown';
  sm?: boolean;
  className?: string;
}) {
  return (
    <div className={['stat', tone ?? '', sm ? 'sm' : '', className ?? ''].filter(Boolean).join(' ')}>
      <span className="stat-l">{label}</span>
      <span className="stat-v tnum">{value}</span>
      {sub !== undefined && <span className="stat-s">{sub}</span>}
    </div>
  );
}

export function StatGrid({ children }: { children: ReactNode }) {
  return <div className="statgrid">{children}</div>;
}

/* ========================================================================== *
 * FORM
 * ========================================================================== */

export function Field({ label, sub, error, required, htmlFor, children }: {
  label: ReactNode;
  sub?: ReactNode;
  /** The server's own reason, verbatim. Turns the control's border red. */
  error?: ReactNode;
  required?: boolean;
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <div className={error ? 'field bad' : 'field'}>
      <label htmlFor={htmlFor}>
        {label}
        {required && <span className="field-req">required</span>}
      </label>
      {children}
      {sub !== undefined && <span className="sub">{sub}</span>}
      {error !== undefined && <span className="err">{error}</span>}
    </div>
  );
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { bad?: boolean }>(
  function Input({ className, bad, ...rest }, ref) {
    return <input ref={ref} className={['inp', bad ? 'bad' : '', className ?? ''].filter(Boolean).join(' ')} {...rest} />;
  },
);

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement> & { bad?: boolean }>(
  function Textarea({ className, bad, ...rest }, ref) {
    return <textarea ref={ref} className={['inp', bad ? 'bad' : '', className ?? ''].filter(Boolean).join(' ')} {...rest} />;
  },
);

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement> & { bad?: boolean }>(
  function Select({ className, bad, children, ...rest }, ref) {
    return (
      <select ref={ref} className={['inp', 'sel', bad ? 'bad' : '', className ?? ''].filter(Boolean).join(' ')} {...rest}>
        {children}
      </select>
    );
  },
);

/**
 * An input with a fixed prefix or suffix welded on — "₹" before a price, "kg"
 * after a weight. The unit is part of the CONTROL and not part of the value:
 * nothing typed here is ever parsed as money in this browser.
 */
export function InputGroup({ prefix, suffix, children }: {
  prefix?: ReactNode; suffix?: ReactNode; children: ReactNode;
}) {
  return (
    <div className="inp-group">
      {prefix !== undefined && <span className="fix">{prefix}</span>}
      {children}
      {suffix !== undefined && <span className="fix">{suffix}</span>}
    </div>
  );
}

/**
 * A setting that is on or off.
 *
 * BLUE WHEN ON, never green — see rule 1 at the top of this file. The whole row
 * is the target, because a 24px track is not something a thumb finds in a shop.
 */
export function Switch({ checked, onChange, label, sub, disabled, name, id }: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: ReactNode;
  sub?: ReactNode;
  disabled?: boolean;
  name?: string;
  id?: string;
}) {
  return (
    <label className="switch" htmlFor={id}>
      <input
        id={id}
        name={name}
        type="checkbox"
        role="switch"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="track"><span className="knob" /></span>
      {(label !== undefined || sub !== undefined) && (
        <span className="switch-text">
          {label !== undefined && <span className="l">{label}</span>}
          {sub !== undefined && <span className="s">{sub}</span>}
        </span>
      )}
    </label>
  );
}

export interface ChoiceOption<T extends string> {
  value: T;
  label: ReactNode;
  sub?: ReactNode;
  disabled?: boolean;
}

/**
 * One of several, where each choice needs a sentence explaining it. Where the
 * choices are one word each, use `Segmented` instead — it costs a third of the
 * vertical space.
 */
export function RadioGroup<T extends string>({ name, value, onChange, options, inline, label }: {
  name: string;
  value: T;
  onChange: (v: T) => void;
  options: ReadonlyArray<ChoiceOption<T>>;
  inline?: boolean;
  label?: string;
}) {
  return (
    <div className={inline ? 'opts inline' : 'opts'} role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <label className="opt" key={o.value}>
          <input
            type="radio"
            name={name}
            value={o.value}
            checked={value === o.value}
            disabled={o.disabled}
            onChange={() => onChange(o.value)}
          />
          <span className="mark" />
          <span className="opt-text">
            <span className="l">{o.label}</span>
            {o.sub !== undefined && <span className="s">{o.sub}</span>}
          </span>
        </label>
      ))}
    </div>
  );
}

export function Checkbox({ checked, onChange, label, sub, disabled, name }: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: ReactNode;
  sub?: ReactNode;
  disabled?: boolean;
  name?: string;
}) {
  return (
    <label className="opt check">
      <input
        type="checkbox"
        name={name}
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="mark" />
      <span className="opt-text">
        <span className="l">{label}</span>
        {sub !== undefined && <span className="s">{sub}</span>}
      </span>
    </label>
  );
}

export function Segmented<T extends string>({ value, onChange, options, size, wide }: {
  value: T;
  onChange: (v: T) => void;
  options: Array<{ value: T; label: string; title?: string; disabled?: boolean }>;
  size?: 'sm';
  /** Fill the width and share it equally — for a two- or three-way filter that
      heads a column rather than sitting in a toolbar. */
  wide?: boolean;
}) {
  return (
    <div className={['seg', size ?? '', wide ? 'wide' : ''].filter(Boolean).join(' ')} role="group">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={value === o.value}
          title={o.title}
          disabled={o.disabled}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ========================================================================== *
 * TABS
 * ========================================================================== */

export interface TabItem<T extends string> {
  value: T;
  label: ReactNode;
  /** A quantity beside the name. Never a verdict colour: a count is not a judgement. */
  count?: number;
  disabled?: boolean;
}

/**
 * Several views of ONE screen.
 *
 * Not to be confused with `.tabs` in the dark bar, which is the three halves of
 * the shop and is a different object with a different class.
 */
export function Tabs<T extends string>({ value, onChange, tabs, label }: {
  value: T;
  onChange: (v: T) => void;
  tabs: ReadonlyArray<TabItem<T>>;
  label?: string;
}) {
  return (
    <div className="tabset" role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <button
          key={t.value}
          type="button"
          role="tab"
          aria-selected={value === t.value}
          disabled={t.disabled}
          onClick={() => onChange(t.value)}
        >
          {t.label}
          {t.count !== undefined && <span className="tab-count tnum">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function TabPanel({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={['tabpanel', className ?? ''].filter(Boolean).join(' ')} role="tabpanel">{children}</div>;
}

/* ========================================================================== *
 * TOOLTIP
 * ========================================================================== */

/**
 * An explanation that is available without being read.
 *
 * CSS-only, on hover AND on focus, so a keyboard reaches it. It is positioned
 * inside its trigger, so a scrolling ancestor clips it — inside a scrolling
 * table, put the explanation in the column header instead.
 *
 * NOT for anything a shopkeeper must know. If a number needs a caveat to be
 * true, the caveat goes on the screen beside it, not behind a hover.
 */
export function Tooltip({ text, below, label, children }: {
  text: ReactNode;
  below?: boolean;
  /** Only used for the bare mark, which has no visible text of its own. */
  label?: string;
  children?: ReactNode;
}) {
  return (
    <span className="tip">
      {children ?? (
        <button type="button" className="tip-mark" aria-label={label ?? 'What this means'}>?</button>
      )}
      <span className={below ? 'tip-bubble below' : 'tip-bubble'} role="tooltip">{text}</span>
    </span>
  );
}

/* ========================================================================== *
 * TOAST
 * Something happened that the screen you are looking at cannot show. A NOTICE,
 * never a receipt — nothing here is the record of anything, and the audit chain
 * is where a shopkeeper goes for that.
 * ========================================================================== */

export type ToastTone = 'info' | 'ok' | 'amb' | 'bad';

interface ToastItem {
  id: number;
  tone: ToastTone;
  title: ReactNode;
  note?: ReactNode;
  out?: boolean;
}

let toastSeq = 0;
let toastList: readonly ToastItem[] = [];
const toastSubs = new Set<() => void>();
const emitToasts = () => { for (const f of toastSubs) f(); };
const subscribeToasts = (f: () => void) => { toastSubs.add(f); return () => { toastSubs.delete(f); }; };
const readToasts = () => toastList;

/** Take a notice off the screen. Safe to call twice. */
export function dismissToast(id: number): void {
  if (!toastList.some((t) => t.id === id && !t.out)) return;
  toastList = toastList.map((t) => (t.id === id ? { ...t, out: true } : t));
  emitToasts();
  // Long enough for the exit animation, and harmless when reduced motion has
  // flattened it to nothing — the row is simply gone a fifth of a second later.
  setTimeout(() => {
    toastList = toastList.filter((t) => t.id !== id);
    emitToasts();
  }, 200);
}

/**
 * Raise a notice. Returns its id so a caller can take it down early.
 *
 * `tone` DEFAULTS TO INFO on purpose: a routine "saved" must not come out
 * green, because on this product green means a payment settled.
 */
export function toast(title: ReactNode, opts?: { tone?: ToastTone; note?: ReactNode; ms?: number }): number {
  const id = ++toastSeq;
  toastList = [...toastList, { id, tone: opts?.tone ?? 'info', title, note: opts?.note }];
  emitToasts();
  const ms = opts?.ms ?? 5000;
  // A notice that stays until it is dismissed is for a refusal, which is not
  // what this is; a refusal belongs on the screen, in a `Refusal`.
  if (ms > 0) setTimeout(() => dismissToast(id), ms);
  return id;
}

/* Only ONE region may be on screen. Routes mount `<Toaster />` themselves until
   the shell owns one, and two of them would render every notice twice. The
   first to mount wins and the others render nothing; when it unmounts the next
   one in the list takes over. */
let hostSeq = 0;
const hosts: number[] = [];
const hostSubs = new Set<() => void>();
const notifyHosts = () => { for (const f of hostSubs) f(); };

export function Toaster() {
  const list = useSyncExternalStore(subscribeToasts, readToasts, readToasts);
  const [primary, setPrimary] = useState(false);

  useEffect(() => {
    const id = ++hostSeq;
    hosts.push(id);
    const check = () => setPrimary(hosts[0] === id);
    hostSubs.add(check);
    notifyHosts();
    return () => {
      const i = hosts.indexOf(id);
      if (i >= 0) hosts.splice(i, 1);
      hostSubs.delete(check);
      notifyHosts();
    };
  }, []);

  if (!primary || typeof document === 'undefined' || list.length === 0) return null;

  return createPortal(
    <div className="toasts" role="region" aria-live="polite" aria-label="Notices">
      {list.map((t) => (
        <div key={t.id} className={['toast', t.tone, t.out ? 'out' : ''].filter(Boolean).join(' ')}>
          <span className="toast-ico">
            {t.tone === 'ok' ? <IcoCheck /> : t.tone === 'amb' ? <IcoWarn /> : t.tone === 'bad' ? <IcoStop /> : <IcoInfo />}
          </span>
          <span className="toast-body">
            <span className="toast-title">{t.title}</span>
            {t.note !== undefined && <span className="toast-note">{t.note}</span>}
          </span>
          <button type="button" className="toast-x" onClick={() => dismissToast(t.id)} aria-label="Dismiss">
            <IcoX size={14} />
          </button>
        </div>
      ))}
    </div>,
    document.body,
  );
}

/* ========================================================================== *
 * MODAL / SHEET
 * ========================================================================== */

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * A dialog stops the page. On a phone it is a bottom sheet, because a centred
 * box on a 390px screen has no room either side of it and a thumb cannot reach
 * its top edge — see the breakpoint in app.css, which converts every modal.
 *
 * It traps Tab. A dialog whose focus can walk out behind its own scrim is a
 * dialog a keyboard user cannot close.
 */
export function Modal({ open, onClose, title, sub, children, foot, note, size, sheet, closeLabel }: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  sub?: ReactNode;
  children: ReactNode;
  /** The buttons. Rendered right-aligned; on a phone they share the width. */
  foot?: ReactNode;
  /** What pressing the button will actually do, on the left of the footer. */
  note?: ReactNode;
  size?: 'narrow' | 'wide';
  /** Force the bottom-sheet shape at every width. */
  sheet?: boolean;
  closeLabel?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const returnTo = useRef<Element | null>(null);

  const onKey = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') { e.stopPropagation(); onClose(); return; }
    if (e.key !== 'Tab' || !box.current) return;
    const items = Array.from(box.current.querySelectorAll<HTMLElement>(FOCUSABLE))
      .filter((el) => el.offsetParent !== null || el === document.activeElement);
    if (items.length === 0) { e.preventDefault(); box.current.focus(); return; }
    const first = items[0]!;
    const last = items[items.length - 1]!;
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    returnTo.current = document.activeElement;
    // The page behind must not scroll under the scrim: on a phone that reads as
    // the dialog itself having lost its place.
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', onKey, true);
    const t = setTimeout(() => {
      const first = box.current?.querySelector<HTMLElement>(FOCUSABLE);
      (first ?? box.current)?.focus();
    }, 0);
    return () => {
      clearTimeout(t);
      document.removeEventListener('keydown', onKey, true);
      document.body.style.overflow = prev;
      (returnTo.current as HTMLElement | null)?.focus?.();
    };
  }, [open, onKey]);

  if (!open || typeof document === 'undefined') return null;

  return createPortal(
    <div
      className={sheet ? 'modal-scrim sheet' : 'modal-scrim'}
      // Only a press that both starts and ends on the scrim closes it. A drag
      // that began inside the dialog and released outside it is not a dismissal.
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        ref={box}
        className={['modal', size ?? ''].filter(Boolean).join(' ')}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <header className="modal-head">
          <div className="modal-title">
            <h3 id={titleId}>{title}</h3>
            {sub !== undefined && <span className="modal-sub">{sub}</span>}
          </div>
          <button type="button" className="modal-x" onClick={onClose} aria-label={closeLabel ?? 'Close'}>
            <IcoX size={15} />
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {(foot !== undefined || note !== undefined) && (
          <footer className="modal-foot">
            {note !== undefined && <span className="modal-note">{note}</span>}
            {foot}
          </footer>
        )}
      </div>
    </div>,
    document.body,
  );
}

/* ========================================================================== *
 * THE WORKING STATES
 * A soft mesh panel for the moment the machine is busy, a mark that turns
 * while it is, a sentence whose figures carry the colour, and a success mark.
 * The CSS block in app.css records why every mesh here is blue and not green.
 * ========================================================================== */

/** Three flutes lighting in sequence: "this counter is doing something". */
export function Working({ className }: { className?: string }) {
  return (
    <span className={['working', className ?? ''].filter(Boolean).join(' ')} aria-hidden="true">
      <i /><i /><i />
    </span>
  );
}

export interface ThinkStep {
  label: ReactNode;
  /**
   * Omit it. The browser does not know which stage the SERVER is in, and a
   * tick this page cannot verify is the same lie as a figure it cannot
   * derive — so an unmarked step is the honest default. Pass it only where
   * the client itself drives the stages.
   */
  state?: 'done' | 'now';
}

/**
 * The machine is working, and this is what it is working through.
 *
 * A mesh panel, a title with the turning mark, and the stages as a list. It
 * replaces a row of bouncing dots, which said something was happening and
 * nothing about what.
 */
export function Thinking({ title, steps, foot, className }: {
  title: ReactNode;
  steps?: ReadonlyArray<ThinkStep>;
  /** One line under the list — usually what will NOT happen. */
  foot?: ReactNode;
  className?: string;
}) {
  return (
    <div className={['mesh thinking', className ?? ''].filter(Boolean).join(' ')} role="status" aria-live="polite">
      <div className="thinking-head"><Working />{title}</div>
      {steps !== undefined && steps.length > 0 && (
        <ul className="thinking-steps">
          {steps.map((st, i) => (
            <li key={i} className={['thinking-step', st.state ?? ''].filter(Boolean).join(' ')}>
              <span className="thinking-mark" aria-hidden="true" />
              <span>{st.label}</span>
            </li>
          ))}
        </ul>
      )}
      {foot !== undefined && <p className="insight-foot">{foot}</p>}
    </div>
  );
}

/**
 * A sentence set large, with the figures carrying the emphasis.
 *
 * Mark a figure with `<Fig>`. It is the accent by default; `tone="green"` is
 * reserved for money a webhook settled and `tone="amber"` for a figure the
 * counter abstained on, exactly as everywhere else on this product.
 */
export function Fig({ tone, children }: {
  tone?: 'green' | 'amber' | 'ink';
  children: ReactNode;
}) {
  return <span className={['fig', tone ?? ''].filter(Boolean).join(' ')}>{children}</span>;
}

export function Insight({ tag, children, foot, className }: {
  /** The small label above the sentence — what kind of reading this is. */
  tag?: ReactNode;
  children: ReactNode;
  /** Where the figures came from. On this product that line is not optional. */
  foot?: ReactNode;
  className?: string;
}) {
  return (
    <div className={['mesh insight', className ?? ''].filter(Boolean).join(' ')}>
      {tag !== undefined && (
        <span className="insight-tag"><Working />{tag}</span>
      )}
      <div>{children}</div>
      {foot !== undefined && <p className="insight-foot">{foot}</p>}
    </div>
  );
}

/** A filled bar, or a travelling one when the fraction is not known. */
export function Progress({ pct, label }: { pct?: number; label?: string }) {
  const known = typeof pct === 'number' && Number.isFinite(pct);
  const w = known ? Math.max(0, Math.min(100, pct)) : undefined;
  return (
    <div
      className={['progress', known ? '' : 'indeterminate'].filter(Boolean).join(' ')}
      role="progressbar"
      aria-label={label}
      aria-valuenow={known ? w : undefined}
      aria-valuemin={known ? 0 : undefined}
      aria-valuemax={known ? 100 : undefined}
    >
      <i style={known ? { width: `${w}%` } : undefined} />
    </div>
  );
}

/** The one green mark in this set, shown only when money actually settled. */
export function SuccessMark({ size = 56 }: { size?: number }) {
  return (
    <span className="success-mark" style={{ width: size, height: size }} aria-hidden="true">
      <svg width={Math.round(size * 0.46)} height={Math.round(size * 0.46)} viewBox="0 0 16 16"
           fill="none" stroke="currentColor" strokeWidth="2.1"
           strokeLinecap="round" strokeLinejoin="round">
        <path d="m3.4 8.4 3.1 3.1 6.1-6.6" />
      </svg>
    </span>
  );
}

/* ========================================================================== *
 * LOADING, EMPTY, REFUSAL
 * The three states every panel on this counter has to be able to be in.
 * ========================================================================== */

export function Skeleton({ w, h = 12, radius, className }: {
  w?: number | string; h?: number | string; radius?: number | string; className?: string;
}) {
  return (
    <span
      className={['skel', className ?? ''].filter(Boolean).join(' ')}
      style={{ display: 'block', width: w ?? '100%', height: h, borderRadius: radius }}
      aria-hidden="true"
    />
  );
}

/** A paragraph's worth of loading, at text metrics. */
export function SkeletonText({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skel-text" aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => <span className="skel" key={i} />)}
    </div>
  );
}

/** A list's worth: the shape of the rows that are coming. */
export function SkeletonRows({ rows = 4, cols = 3 }: { rows?: number; cols?: number }) {
  return (
    <div aria-hidden="true">
      {Array.from({ length: rows }, (_, r) => (
        <div className="skel-row" key={r}>
          {Array.from({ length: cols }, (_, c) => (
            <span className={c === 0 ? 'skel grow' : 'skel'} style={c === 0 ? undefined : { width: 64 }} key={c} />
          ))}
        </div>
      ))}
    </div>
  );
}

/**
 * A whole card body, waiting. Use it instead of rendering nothing: a panel that
 * is blank while it loads and full a moment later moves everything below it.
 */
export function LoadingCard({ lines = 3, label = 'Loading' }: { lines?: number; label?: string }) {
  return (
    <div className="skel-card" role="status" aria-live="polite" aria-label={label}>
      <Skeleton w="38%" h={13} />
      <SkeletonText lines={lines} />
    </div>
  );
}

/**
 * Nothing here yet — which is a state, not an absence.
 *
 * The old shape (`<Empty>a sentence</Empty>`) still renders exactly as it did.
 * `title`, `icon` and `action` are additions; passing any of them draws the
 * fuller version.
 */
export function Empty({ title, icon, action, children }: {
  title?: ReactNode;
  /** Pass `false` for a bare sentence with no mark above it. */
  icon?: ReactNode | false;
  action?: ReactNode;
  children: ReactNode;
}) {
  const rich = title !== undefined || icon !== undefined || action !== undefined;
  // THE BARE FORM IS STILL A DRAWN STATE. It used to be one grey sentence
  // centred in whatever height the card happened to have, which on a wide
  // screen reads as a panel that failed to load rather than as a panel with
  // nothing in it yet. The sentence is unchanged and the wrapper keeps its
  // `.empty` class; `bare` and the body span only give CSS something to hold
  // the sentence to a measure and draw the reserved space around it.
  if (!rich) return <div className="empty bare"><span className="empty-body">{children}</span></div>;
  // `<Empty icon={false}>a sentence</Empty>` is the same object as the bare
  // form — no mark, no heading — and half the screens reach it by that route.
  // It gets the same reserved-space treatment; a caller that passes a title or
  // keeps the tray mark already has something for the eye to land on.
  const bare = title === undefined && icon === false;
  return (
    <div className={bare ? 'empty bare' : 'empty'}>
      {icon !== false && <span className="empty-ico">{icon ?? <IcoTray size={20} />}</span>}
      {title !== undefined && <span className="empty-title">{title}</span>}
      <span className="empty-body">{children}</span>
      {action !== undefined && <div className="btn-row">{action}</div>}
    </div>
  );
}

/**
 * A server refusal, rendered as the product working rather than as an error.
 * The reason is shown verbatim: it is the machine's own account of why it
 * would not guess, and paraphrasing it would hide the thing worth reading.
 */
export function Refusal({ reason, detail, hint, action }: {
  reason: string; detail?: string; hint?: string; action?: ReactNode;
}) {
  return (
    <Verdict tone="amber" title={reason}>
      {detail && <span className="mono">{detail}</span>}
      {hint && <><br />{hint}</>}
      {/* A SPAN, NOT A DIV. `Verdict` renders its children inside a <p>, and a
          <div> there is invalid HTML — the browser closes the paragraph early
          and lifts the block clean out of the styled box, so the action button
          rendered outside the refusal card it belongs to. It type-checked and
          every test passed; only rendering it in a browser shows it.
          `display:flex` on the span keeps the layout identical. */}
      {action && (
        <span className="btn-row" style={{ display: 'flex', marginTop: 12 }}>{action}</span>
      )}
    </Verdict>
  );
}

/* ========================================================================== *
 * TABLE
 * ========================================================================== */

export interface Column<T> {
  key: string;
  head: ReactNode;
  cell: (row: T, index: number) => ReactNode;
  /** Right-aligned, tabular figures, no wrap. Every money and count column. */
  num?: boolean;
  /** Worth less than the horizontal scroll it costs on a phone: hidden under 720px. */
  drop?: boolean;
  width?: string;
  className?: string;
  /**
   * What this column is called when the table restacks into cards under 560px
   * (`className="tbl-cards"`), where each cell has to name itself. Defaults to
   * `head` when `head` is already a plain string, which it usually is — supply
   * it only when the header is markup, or when the card wants a shorter word
   * than the column header does.
   */
  label?: string;
}

/**
 * A list of rows, with a header that stays put.
 *
 * THE STICKY HEADER IS REAL. The wrapper caps its own height and scrolls, so
 * the header pins to the top of the WRAPPER — pinning it to the viewport would
 * put it under the dark bar. A table shorter than the cap never scrolls and
 * behaves like an ordinary one; on a phone the cap is lifted entirely, because
 * a scroll region inside a scrolling page catches the thumb and the page looks
 * frozen.
 *
 * Loading and empty are states of the TABLE, not of the screen: the header is
 * already drawn while the rows are on their way, so nothing moves when they
 * land.
 */
export function Table<T>({
  cols, rows, rowKey, loading, loadingRows = 5, empty, onRowClick, isOpen, rowClass,
  caption, maxHeight, className, label,
}: {
  cols: ReadonlyArray<Column<T>>;
  rows: readonly T[];
  rowKey: (row: T, index: number) => string;
  loading?: boolean;
  loadingRows?: number;
  empty?: ReactNode;
  onRowClick?: (row: T, index: number) => void;
  isOpen?: (row: T, index: number) => boolean;
  rowClass?: (row: T, index: number) => string | undefined;
  caption?: ReactNode;
  /** Any CSS length, or `'none'` to let it run the full height of the page. */
  maxHeight?: string;
  className?: string;
  label?: string;
}) {
  const style = maxHeight ? ({ ['--tbl-max' as string]: maxHeight } as CSSProperties) : undefined;
  const headCls = (c: Column<T>) => [c.num ? 'num' : '', c.drop ? 'drop' : ''].filter(Boolean).join(' ') || undefined;
  /* The name a cell wears when the table restacks into cards on a phone. It is
     written on EVERY cell, always — the attribute is inert until a stylesheet
     asks for it with `content: attr(data-label)`, so a table that never
     restacks is unaffected, and one that opts in later needs no other change.
     An empty string is meaningful and not a missing value: the restack reads it
     as "this column has no name", which is how the actions cell is recognised. */
  const cellLabel = (c: Column<T>) => (c.label !== undefined ? c.label : typeof c.head === 'string' ? c.head : '');

  return (
    <div className="tbl-wrap" style={style}>
      <table className={['tbl', className ?? ''].filter(Boolean).join(' ')} aria-label={label}>
        {caption !== undefined && <caption>{caption}</caption>}
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c.key} className={headCls(c)} style={c.width ? { width: c.width } : undefined} scope="col">
                {c.head}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading ? (
            Array.from({ length: loadingRows }, (_, r) => (
              <tr key={`skel-${r}`}>
                {cols.map((c) => (
                  <td key={c.key} className={headCls(c)} data-label={cellLabel(c)}>
                    <Skeleton w={c.num ? 56 : '70%'} h={11} radius={999} />
                  </td>
                ))}
              </tr>
            ))
          ) : rows.length === 0 ? (
            <tr>
              {/* One cell across the whole table: an empty state inside a <tbody>
                  has nowhere else it is valid to live. */}
              <td colSpan={cols.length}>
                {typeof empty === 'string' || empty === undefined
                  ? <Empty>{empty ?? 'Nothing here yet.'}</Empty>
                  : empty}
              </td>
            </tr>
          ) : (
            rows.map((row, i) => {
              const cls = [
                onRowClick ? 'pressable' : '',
                isOpen?.(row, i) ? 'open' : '',
                rowClass?.(row, i) ?? '',
              ].filter(Boolean).join(' ');
              return (
                <tr
                  key={rowKey(row, i)}
                  className={cls || undefined}
                  onClick={onRowClick ? () => onRowClick(row, i) : undefined}
                >
                  {cols.map((c) => (
                    <td
                      key={c.key}
                      className={[headCls(c) ?? '', c.className ?? ''].filter(Boolean).join(' ') || undefined}
                      data-label={cellLabel(c)}
                    >
                      {c.cell(row, i)}
                    </td>
                  ))}
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
