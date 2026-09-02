/**
 * SHARE SHEET — the bill, the order or the reorder list, on its way to WhatsApp.
 *
 * A dialog with three things in it: the message the SERVER composed, shown
 * verbatim; one field for a phone number; and one button that opens WhatsApp
 * with both already filled in. On a phone it is a bottom sheet, because
 * `Modal` converts at the narrow breakpoint and a thumb cannot reach the top
 * edge of a centred box on a 390 px screen.
 *
 * THE PAGE COMPOSES NOTHING. Not a line, not a rupee, not the `wa.me` address.
 * `wa.me/<digits>?text=<anything>` is one line of JavaScript, and a page that
 * wrote that line would be a page that could put a payment payload into a
 * message going out in the shopkeeper's own name — which is exactly what the
 * server refuses, and a refusal the browser can route around is decoration.
 * So the preview is the server's bytes and the button is an anchor pointing at
 * the server's `wa_url`.
 *
 * WHY THE BUTTON IS AN <a> AND NOT A <Button>. The link is fetched while the
 * number is being typed, so that pressing the button is a plain anchor click.
 * A `window.open()` that runs after an `await` has lost its user gesture and
 * is blocked by every mobile browser worth naming — measured as a button that
 * did nothing at all on iOS Safari, with no error anywhere. Fetching first
 * costs one request per number and makes the press work.
 *
 * THE DESKTOP HAS NO WHATSAPP. `wa.me` degrades to WhatsApp Web there, which
 * needs a session the shopkeeper's laptop may not have, so the message is also
 * selectable and there is a copy button beside it. That is not a fallback for
 * a broken feature; it is the feature, on a machine with no phone attached.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import {
  addressFor, previewFor, shareLimits, worthAsking,
} from '../lib/shareapi';
import type {
  Addressed, Composed, OrderShare, ReceiptShare, ReorderShare, ShareLimits,
  ShareTarget,
} from '../lib/shareapi';
import {
  Button, Empty, Field, Input, KV, LoadingCard, Modal, Pill, Refusal, toast,
} from './ui';
import '../styles/share.css';

/** The one refusal that is really an absence: there is nothing to order. */
const NOTHING_TO_SEND = 'nothing_is_low_on_stock';

type Loaded = Composed & Partial<ReceiptShare & OrderShare & ReorderShare>;
type Linked = Loaded & Addressed;

interface Refused { reason: string; detail?: string }

const HEAD: Record<ShareTarget['kind'], { title: string; verb: string }> = {
  receipt: { title: 'Send this bill', verb: 'Send the bill' },
  order: { title: 'Send this order', verb: 'Send the update' },
  reorder: { title: 'Send this reorder list', verb: 'Send the list' },
};

/** A WhatsApp glyph, drawn rather than fetched: the CSP allows no CDN. */
function IcoWhatsApp({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12.04 2C6.58 2 2.13 6.45 2.13 11.91c0 1.75.46 3.45 1.32 4.95L2 22l5.25-1.38a9.9 9.9 0 0 0 4.79 1.22h.01c5.46 0 9.9-4.45 9.9-9.91C21.95 6.45 17.5 2 12.04 2Zm0 18.15h-.01a8.2 8.2 0 0 1-4.19-1.15l-.3-.18-3.11.82.83-3.04-.2-.31a8.22 8.22 0 0 1-1.26-4.38c0-4.54 3.7-8.23 8.24-8.23a8.23 8.23 0 0 1 8.23 8.24c0 4.54-3.69 8.23-8.23 8.23Zm4.52-6.16c-.25-.12-1.47-.72-1.69-.81-.23-.08-.39-.12-.56.13-.16.24-.64.8-.78.97-.15.16-.29.18-.53.06-.25-.13-1.05-.39-2-1.23-.74-.66-1.24-1.47-1.38-1.72-.15-.25-.02-.38.1-.5.11-.11.25-.29.37-.44.13-.14.17-.24.25-.41.09-.16.04-.3-.02-.43-.06-.12-.56-1.34-.76-1.84-.2-.48-.4-.42-.56-.43h-.47c-.16 0-.43.06-.65.31-.22.24-.85.83-.85 2.03s.87 2.35.99 2.51c.12.17 1.71 2.61 4.14 3.66.58.25 1.03.4 1.38.51.58.19 1.11.16 1.53.1.47-.07 1.44-.59 1.64-1.16.2-.57.2-1.05.14-1.16-.06-.1-.22-.16-.47-.28Z" />
    </svg>
  );
}

function IcoCopy({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none"
      stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" />
      <path d="M10.5 3.5v-1a1 1 0 0 0-1-1h-7a1 1 0 0 0-1 1v7a1 1 0 0 0 1 1h1" />
    </svg>
  );
}

/**
 * The money and recognition state of the thing being sent, as a pill.
 *
 * GREEN ONLY ON A VERIFIED WEBHOOK — the same rule the rest of this counter
 * keeps. A settlement the counter recorded without the gateway's signed
 * callback beside it is AMBER and says so, because green on this product means
 * a rupee actually arrived.
 */
function StateChip({ data }: { data: Loaded }) {
  if (data.kind === 'receipt') {
    if (data.settled_by_verified_webhook) return <Pill tone="ok" dot>Paid</Pill>;
    if (data.payment_state === 'recorded_paid_by_the_counter') {
      return <Pill tone="amb" dot>Paid — counter&rsquo;s record</Pill>;
    }
    return <Pill tone="off">Not paid</Pill>;
  }
  if (data.kind === 'order') {
    return data.paid
      ? <Pill tone="ok" dot>Paid</Pill>
      : <Pill tone="off">{String(data.status ?? '').replace(/_/g, ' ') || 'new'}</Pill>;
  }
  return <Pill tone="off">{data.low_count ?? 0} to order</Pill>;
}

export interface ShareSheetProps {
  open: boolean;
  onClose: () => void;
  /** What is being shared. One shape per kind, so an id cannot go astray. */
  target: ShareTarget;
  /**
   * A number to start the field with — the customer on this bill, say. It is a
   * SUGGESTION and is sent to the server like any other typed number; the
   * server decides whether it is a mobile.
   */
  suggestedPhone?: string;
}

export function ShareSheet({ open, onClose, target, suggestedPhone }: ShareSheetProps) {
  const [data, setData] = useState<Loaded | null>(null);
  const [refused, setRefused] = useState<Refused | null>(null);
  const [loading, setLoading] = useState(false);

  const [phone, setPhone] = useState('');
  const [linked, setLinked] = useState<Linked | null>(null);
  const [phoneRefused, setPhoneRefused] = useState<Refused | null>(null);
  const [asking, setAsking] = useState(false);

  const [limits, setLimits] = useState<ShareLimits | null>(null);
  const [showLimits, setShowLimits] = useState(false);
  const phoneRow = useRef<HTMLDivElement>(null);

  // The key of the thing on screen. A sheet reopened for a different bill must
  // not show the previous bill's message for the frame before the fetch lands.
  const key = target.kind === 'receipt' ? target.sessionId
    : target.kind === 'order' ? target.orderId
      : `reorder:${target.supplierId ?? ''}`;

  // Every fetch carries the key it was fired for and is dropped if the sheet
  // has moved on. Without this, a slow preview for bill A lands on top of the
  // message for bill B and the shopkeeper sends the wrong receipt.
  const live = useRef(key);

  useEffect(() => {
    if (!open) return;
    live.current = key;
    setData(null); setRefused(null); setLinked(null); setPhoneRefused(null);
    setPhone(suggestedPhone ?? '');
    setLoading(true);
    let cancelled = false;
    void previewFor(target).then((res) => {
      if (cancelled || live.current !== key) return;
      setLoading(false);
      if (res.ok) setData(res as Loaded);
      else setRefused({ reason: res.reason, detail: res.detail });
    });
    return () => { cancelled = true; };
    // `target` is rebuilt by the caller on every render; `key` is its identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, key, suggestedPhone]);

  useEffect(() => {
    if (!open || limits) return;
    void shareLimits().then((res) => { if (res.ok) setLimits(res as ShareLimits); });
  }, [open, limits]);

  // The number is turned into a link WHILE IT IS BEING TYPED, so that pressing
  // the button is a plain anchor click with its user gesture intact. Debounced,
  // because a request per keystroke is ten requests per number.
  const onFile = Boolean(data && (data.phone_on_file || target.kind === 'order'));
  useEffect(() => {
    if (!open || !data) return;
    setLinked(null);
    if (!worthAsking(phone, onFile)) { setPhoneRefused(null); return; }
    setAsking(true);
    let cancelled = false;
    const t = setTimeout(() => {
      void addressFor(target, phone.trim()).then((res) => {
        if (cancelled || live.current !== key) return;
        setAsking(false);
        if (res.ok) { setLinked(res as Linked); setPhoneRefused(null); }
        else { setLinked(null); setPhoneRefused({ reason: res.reason, detail: res.detail }); }
      });
    }, 350);
    return () => { cancelled = true; clearTimeout(t); setAsking(false); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, phone, key, data, onFile]);

  // A refusal about the number sits BELOW the message, which on a phone is
  // below the fold. Measured: a bad number produced a disabled button and a
  // reason the shopkeeper could not see without scrolling, which reads as the
  // button being broken. Bring the field to him instead.
  useEffect(() => {
    if (!phoneRefused) return;
    phoneRow.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [phoneRefused]);

  const copy = useCallback(async () => {
    const text = data?.message ?? '';
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      toast('Message copied', { note: 'Paste it into WhatsApp yourself.' });
    } catch {
      // A clipboard the browser will not open is not a failure worth an alarm:
      // the text below is selectable, which is what the fallback actually is.
      toast('This browser would not open the clipboard',
        { tone: 'amb', note: 'Select the message above and copy it by hand.' });
    }
  }, [data]);

  if (!open) return null;

  const head = HEAD[target.kind];
  const isAbsence = refused?.reason === NOTHING_TO_SEND;

  // How many digits are in the field. NOT a validation — the server owns what
  // an Indian mobile is and answers with one of seven named refusals. This
  // only lets a half-typed number say it is half-typed, instead of leaving a
  // disabled button with no account of itself.
  const typed = phone.replace(/\D/g, '').length;
  const shortHint = typed > 0 && typed < 10
    ? `${typed} of the ten digits an Indian mobile has.`
    : undefined;

  // Only near the cap. A character count on a 300-character message is a
  // number about nothing; on a 1500-character one it is why the next line
  // might be refused.
  const nearCap = Boolean(data && data.message_chars * 4 > data.message_cap * 3);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={head.title}
      sub="WhatsApp opens with this typed. You press send."
      foot={
        // NOTHING TO SEND, NOTHING TO PRESS. On a refusal or an empty list the
        // two action buttons are absent rather than disabled: a greyed-out
        // "Send the bill" beside a refusal reads as a thing that might work.
        data ? (
          <>
            <Button variant="quiet" icon={<IcoCopy />} onClick={() => void copy()}
              aria-label="Copy the message">
              Copy the text
            </Button>
            {linked ? (
              <a
                className="btn primary sh-wa"
                href={linked.wa_url}
                target="_blank"
                rel="noopener noreferrer"
                onClick={onClose}
              >
                <span className="btn-ico"><IcoWhatsApp /></span>
                <span className="btn-label">{head.verb}</span>
              </a>
            ) : (
              <Button variant="primary" icon={<IcoWhatsApp />} loading={asking} disabled>
                {head.verb}
              </Button>
            )}
          </>
        ) : (
          <Button variant="ghost" onClick={onClose}>Close</Button>
        )
      }
    >
      {loading && <LoadingCard lines={4} label="Composing the message" />}

      {/* EMPTY — a reorder list with nothing on it. Not an error: a shop with
          full shelves is the point of keeping levels. */}
      {isAbsence && (
        <Empty title="Nothing to reorder">
          {refused?.detail}
        </Empty>
      )}

      {/* REFUSAL — the server's own words, shown as the product working. */}
      {refused && !isAbsence && (
        <Refusal reason={refused.reason} detail={refused.detail}
          hint="Nothing was composed and nothing was sent." />
      )}

      {data && (
        <div className="sh">
          <div className="sh-meta">
            <StateChip data={data} />
            {data.total_rupees !== undefined && (
              <span className="sh-total tnum">&#8377;{data.total_rupees}</span>
            )}
            {nearCap && (
              <span className="sh-count">
                long — {data.message_chars} of {data.message_cap} characters
              </span>
            )}
          </div>

          <div ref={phoneRow}>
            <Field
              label="Send it to"
              htmlFor="sh-phone"
              sub={
                shortHint ?? (data.phone_on_file
                  ? `Leave it blank to use ${data.phone_on_file}, the number on file.`
                  : 'An Indian mobile. 9876543210, +91 98765 43210 or 09876543210.')
              }
              error={phoneRefused ? (phoneRefused.detail ?? phoneRefused.reason) : undefined}
            >
              <Input
                id="sh-phone"
                type="tel"
                inputMode="tel"
                autoComplete="tel"
                placeholder={data.phone_on_file ?? '9876543210'}
                value={phone}
                bad={Boolean(phoneRefused)}
                onChange={(e) => setPhone(e.currentTarget.value)}
              />
            </Field>

            {linked && (
              <KV k="Opening">
                <span className="tnum">{linked.to_display}</span>
                {linked.phone_from && (
                  <span className="sh-from"> · {linked.phone_from}</span>
                )}
              </KV>
            )}
          </div>

          {/* THE SERVER'S BYTES, VERBATIM. `white-space: pre-wrap` so the line
              breaks are the ones that go out; a page that reflowed this would
              be showing the shopkeeper something other than what he sends. */}
          <pre className="sh-msg" aria-label="The message that will be sent">
            {data.message}
          </pre>

          {data.link_included === false && data.link_problem && (
            <p className="sh-note">{data.link_problem}</p>
          )}

          {/* The server's own sentence about what pressing the button does.
              Shown here rather than in the footer, where five lines of it made
              the footer taller than the sheet on a 390 px screen and pushed
              the phone field's refusal below the fold. */}
          <p className="sh-said">{data.note}</p>

          {/* The one honest sentence about what this button does, from the
              server rather than typed into the page: it stays true only for as
              long as the server agrees with it. */}
          <details className="sh-limits" open={showLimits}
            onToggle={(e) => setShowLimits(e.currentTarget.open)}>
            <summary>What this does, and what it does not</summary>
            {limits ? (
              <div className="sh-limits-body">
                <p>{limits.how}</p>
                <p>{limits.payment_links_note}</p>
                <p>{limits.records_note}</p>
                <p>{limits.why_not_the_api}</p>
                <p className="sh-fine">{limits.numbers.stated_limit}</p>
              </div>
            ) : (
              <div className="sh-limits-body"><LoadingCard lines={2} label="Reading the limits" /></div>
            )}
          </details>
        </div>
      )}
    </Modal>
  );
}

export default ShareSheet;
