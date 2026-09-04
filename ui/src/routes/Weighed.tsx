import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import * as w from '../lib/weighedapi';
import { rupees } from '../lib/money';
import {
  Button, Card, Empty, Field, IcoWarn, Input, KV, LoadingCard, Pill, Refusal, Segmented, Select,
  Verdict,
} from '../components/ui';
import '../styles/weighed.css';

/**
 * By weight — rice, dal, atta, sugar, out of the sack and onto the bill.
 *
 * THE ONE THING THIS PAGE DOES NOT DO is work out a price. The readout sends
 * the product and the weight the shopkeeper typed — grams as a whole number,
 * or kilograms as the TEXT typed, never a float — and the server answers with
 * integer paise, the sum it did, and how much of a paisa its floor dropped.
 * This page formats those numbers and nothing else.
 *
 * THE OTHER THING IT DOES NOT DO is touch the bill. The Till owns the basket.
 * A weight is written down here under a line id, on the weighed chain, and
 * parked for the Till (`weighedapi.stashForTill`); the Till puts it on the
 * bill. And a weighed line cannot yet be charged through the gateway — the
 * money service prices packets from its own book and does not price a weight —
 * so the screen says so beside the button rather than after the customer has
 * been shown a QR.
 *
 * ONE-HANDED. A scoop in one hand, a thumb free: big digits, a keypad, four
 * presets, one large button. The readout is a real input, so a keyboard works
 * too.
 */

type Unit = 'kg' | 'g';
type Err = { reason: string; detail?: string };

/** Up to three decimals of a kilogram — a gram is the finest a scale shows. */
const KG_TEXT = /^\d{0,3}(\.\d{0,3})?$/;
/** Whole grams, up to 100 000. */
const G_TEXT = /^\d{0,6}$/;

const cleanText = (t: string, unit: Unit): boolean => (unit === 'kg' ? KG_TEXT : G_TEXT).test(t);

/** The body the server takes, or null when there is nothing to price yet. */
function weightOf(text: string, unit: Unit): w.Weight | null {
  const t = text.trim();
  if (!t || t === '.') return null;
  if (unit === 'kg') return { kg: t };
  const n = Number(t);
  if (!Number.isInteger(n) || n <= 0) return null;
  return { grams: n };
}

const KEYS: ReadonlyArray<ReadonlyArray<string>> = [
  ['1', '2', '3'], ['4', '5', '6'], ['7', '8', '9'], ['.', '0', '⌫'],
];

/**
 * WHY A CONTROL IS DEAD, WHERE THE HAND IS.
 *
 * MARK IT is dead every time this page is opened — nothing is typed in the
 * per-kilo field yet — and it used to account for itself nowhere. Quiet ink: a
 * control that is not applicable yet is not a refusal and not an abstention,
 * and on this product amber and red are spoken for.
 */
function WhyDead({ id, children }: { id: string; children: ReactNode }) {
  return <p className="wg-whydead" id={id}>{children}</p>;
}

/** Put the cursor where the sentence just sent it. Writes nothing. */
function jumpTo(id: string): void {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ block: 'center', behavior: 'smooth' });
  (el as HTMLElement).focus({ preventScroll: true });
}

export default function Weighed() {
  const [book, setBook] = useState<w.WeighedBook | null>(null);
  const [health, setHealth] = useState<w.WeighedHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<Err | null>(null);
  /** Why the rule could not be read. Separate from `err`: they are two
      different requests and one is not evidence about the other. */
  const [healthErr, setHealthErr] = useState<Err | null>(null);
  /** A refused UNMARK, under its own name. It used to be pushed into `err`,
      which prints "Products sold by weight could not be read" — a heading about
      a request that had just succeeded. A refusal filed under the wrong
      question is worse than none: it sends the reader to fix the wrong thing. */
  const [unmarkErr, setUnmarkErr] = useState<(Err & { sku_id: string }) | null>(null);

  // The readout.
  const [sku, setSku] = useState('');
  const [unit, setUnit] = useState<Unit>('kg');
  const [text, setText] = useState('');
  const [quote, setQuote] = useState<w.PricedLine | null>(null);
  const [quoteErr, setQuoteErr] = useState<Err | null>(null);
  const [quoting, setQuoting] = useState(false);
  const [adding, setAdding] = useState(false);
  const [addErr, setAddErr] = useState<Err | null>(null);
  const [recent, setRecent] = useState<w.WrittenLine[]>([]);
  const [pending, setPending] = useState<w.PendingLine[]>(() => w.pendingForTill());

  // Marking a product.
  const [markSku, setMarkSku] = useState('');
  const [perKg, setPerKg] = useState('');
  const [marking, setMarking] = useState(false);
  const [markErr, setMarkErr] = useState<Err | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [b, h] = await Promise.all([w.list(), w.health()]);
    if (b.ok) { setBook(b); setErr(null); } else { setErr(b); }
    if (h.ok) { setHealth(h); setHealthErr(null); } else { setHealth(null); setHealthErr(h); }
    setLoading(false);
  }, []);

  useEffect(() => { void load(); }, [load]);

  const items = useMemo(() => book?.items ?? [], [book]);
  const markable = useMemo(() => book?.markable ?? [], [book]);
  const presets = useMemo(() => book?.presets_grams ?? [250, 500, 1000, 2000], [book]);

  // The first weighed product is selected when the list arrives, and a
  // selection that has been unmarked falls back rather than pointing at nothing.
  useEffect(() => {
    if (items.length === 0) { setSku(''); return; }
    if (!items.some((i) => i.sku_id === sku)) setSku(items[0]!.sku_id);
  }, [items, sku]);
  useEffect(() => {
    if (markable.length === 0) { setMarkSku(''); return; }
    if (!markable.some((m) => m.sku_id === markSku)) setMarkSku(markable[0]!.sku_id);
  }, [markable, markSku]);

  const row = useMemo(() => items.find((i) => i.sku_id === sku) ?? null, [items, sku]);

  /* ---- the live quote ----------------------------------------------------- */

  // A sequence number, so a slow answer to an old weight cannot overwrite the
  // answer to the weight that is on the readout now.
  const seq = useRef(0);
  useEffect(() => {
    const weight = weightOf(text, unit);
    if (!sku || !weight) { setQuote(null); setQuoteErr(null); setQuoting(false); return; }
    const mine = ++seq.current;
    setQuoting(true);
    const t = setTimeout(() => {
      void (async () => {
        const r = await w.price(sku, weight);
        if (mine !== seq.current) return;
        setQuoting(false);
        if (r.ok) { setQuote(r); setQuoteErr(null); } else { setQuote(null); setQuoteErr(r); }
      })();
    }, 120);
    return () => clearTimeout(t);
  }, [sku, text, unit]);

  /* ---- entering a weight -------------------------------------------------- */

  const type = useCallback((next: string) => {
    if (cleanText(next, unit)) { setText(next); setAddErr(null); }
  }, [unit]);

  const key = useCallback((k: string) => {
    if (k === '⌫') { type(text.slice(0, -1)); return; }
    if (k === '.' && (unit === 'g' || text.includes('.'))) return;
    type(text + k);
  }, [text, unit, type]);

  const preset = useCallback((grams: number) => {
    const u: Unit = grams < 1000 ? 'g' : 'kg';
    setUnit(u);
    setText(w.presetText(grams, u));
    setAddErr(null);
  }, []);

  // Switching unit carries the weight across, using the grams the SERVER
  // answered for what is typed — so "2.5" kg becomes "2500" g, exactly.
  const switchUnit = useCallback((u: Unit) => {
    if (u === unit) return;
    setUnit(u);
    if (quote) setText(w.presetText(quote.grams, u));
    else if (!cleanText(text, u)) setText('');
  }, [unit, quote, text]);

  const clear = useCallback(() => { setText(''); setAddErr(null); }, []);

  /* ---- writing the line --------------------------------------------------- */

  const add = useCallback(async () => {
    const weight = weightOf(text, unit);
    if (!sku || !weight || adding) return;
    setAdding(true);
    setAddErr(null);
    const r = await w.line(sku, weight);
    setAdding(false);
    if (!r.ok) { setAddErr(r); return; }
    w.stashForTill(r);
    setPending(w.pendingForTill());
    setRecent((rs) => [r, ...rs].slice(0, 12));
    setText('');
    setQuote(null);
  }, [sku, text, unit, adding]);

  const forget = useCallback((lineId: string) => {
    setPending(w.unstash(lineId));
  }, []);

  /* ---- marking ------------------------------------------------------------ */

  const doMark = useCallback(async () => {
    if (!markSku || !perKg.trim() || marking) return;
    setMarking(true);
    setMarkErr(null);
    const r = await w.mark(markSku, perKg.trim());
    setMarking(false);
    if (!r.ok) { setMarkErr(r); return; }
    setPerKg('');
    await load();
    setSku(r.sku_id);
  }, [markSku, perKg, marking, load]);

  const doUnmark = useCallback(async (skuId: string) => {
    setBusy(skuId);
    setUnmarkErr(null);
    const r = await w.unmark(skuId);
    setBusy(null);
    // Under its own name, beside the row it was refused on — not folded into
    // the page's "could not read" banner, which is about a different request.
    if (r.ok) await load(); else setUnmarkErr({ ...r, sku_id: skuId });
  }, [load]);

  /* ---- render ------------------------------------------------------------- */

  const canAdd = !!quote && !adding && !quoting;
  const exampleFor = (grams: number) => row?.examples.find((e) => e.grams === grams) ?? null;

  /* WHY MARK IT IS DEAD. Three different reasons, and the button used to give
     none of them. Ordered by what a shopkeeper can do about it first. */
  const markWhy = loading
    ? 'Still reading the catalogue.'
    : !markable.length
      ? (items.length
        ? 'Every product with a price is already sold by weight, so there is nothing left to mark. Unmark one above to change its per-kilo price.'
        : 'No product in the catalogue has a price, and a per-kilo price is set against one. Teach a product with a price first.')
      : !perKg.trim()
        ? 'No price per kilo is typed yet. That figure is what the whole sum is worked out from, so the counter will not guess it.'
        : null;

  return (
    <div className="wg-page">
      <div className="page-head">
        <h1>By weight</h1>
        <p>
          Rice, dal, atta, sugar — sold from the sack, not the packet. Mark a product with its
          price per kilo, put the scoop on the scale, type what it says, and the counter prices
          it in whole paise. The odd paisa that does not divide is dropped and goes to the
          customer.
        </p>
      </div>

      {err && (
        <div>
          <Refusal
            reason="Products sold by weight could not be read"
            detail={err.reason}
            hint={err.detail}
            action={<Button size="sm" onClick={() => void load()}>TRY AGAIN</Button>}
          />
        </div>
      )}

      {book && !book.catalogue_known && (
        <div>
          <Verdict tone="amber" title="The catalogue could not be read">
            What is below is what is on disk. Product names and the packet prices beside them
            cannot be shown, because the catalogue itself is not readable from here, and nothing
            has been guessed in their place.
          </Verdict>
        </div>
      )}

      <div className="wg-grid">
        {/* ------------------------------------------------------- the scale -- */}
        <Card title="Weigh" sub="what the scale says, priced" aside={<Pill tone="code">SERVER PRICES IT</Pill>}>
          {loading ? (
            <LoadingCard lines={4} label="Reading what is sold by weight" />
          ) : items.length === 0 ? (
            /* THE STATE THIS SHOP IS ACTUALLY IN: nothing is marked, so this
               scale is dead every day until something is. The old copy pointed
               "on the right on a wide screen, below on a phone", which is a
               sentence written around a missing button. Here is the button. */
            <Empty
              title="Nothing is sold by weight yet"
              action={
                markable.length > 0
                  ? <Button variant="primary" onClick={() => jumpTo('wg-perkg')}>MARK A PRODUCT</Button>
                  : <a className="btn primary wg-linkbtn" href="#/products">TEACH A PRODUCT</a>
              }
            >
              This is the scale: choose a product, put the scoop on, type what the scale says, and
              the server prices it in whole paise and shows you the sum it did.{' '}
              {markable.length > 0
                ? <>Nothing is marked as sold by weight yet, so there is nothing to weigh. Give one of your {markable.length} product{markable.length === 1 ? '' : 's'} a price per kilo and it appears here.</>
                : 'Nothing is priced in the catalogue yet, so there is nothing to give a per-kilo price to.'}
            </Empty>
          ) : (
            <div className="wg-pad">
              <Field label="Product" htmlFor="wg-sku">
                <Select id="wg-sku" value={sku} onChange={(e) => { setSku(e.target.value); setAddErr(null); }}>
                  {items.map((i) => (
                    <option key={i.sku_id} value={i.sku_id}>
                      {i.name} — ₹{i.price_per_kg_rupees}/kg
                    </option>
                  ))}
                </Select>
              </Field>

              <div>
                <div className="wg-readout">
                  {/* TEXT, NOT number. A number input hands back a float, and
                      "2.5" must reach the server as the characters typed. */}
                  <input
                    id="wg-weight"
                    type="text"
                    inputMode="decimal"
                    autoComplete="off"
                    placeholder="0"
                    aria-label={`Weight in ${unit === 'kg' ? 'kilograms' : 'grams'}`}
                    value={text}
                    onChange={(e) => type(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') void add(); }}
                  />
                  <span className="wg-unit">{unit}</span>
                </div>
                <div className="wg-also" aria-live="polite">
                  {quote
                    ? (unit === 'kg' ? `= ${quote.grams} g` : `= ${w.describeGrams(quote.grams)}`)
                    : ' '}
                </div>
              </div>

              <div className="wg-row">
                <Segmented<Unit>
                  value={unit}
                  onChange={switchUnit}
                  options={[
                    { value: 'kg', label: 'kg', title: 'Kilograms, up to three decimals' },
                    { value: 'g', label: 'g', title: 'Whole grams' },
                  ]}
                />
                <Button variant="quiet" size="sm" onClick={clear} disabled={!text}>CLEAR</Button>
              </div>

              <div className="wg-presets" role="group" aria-label="Common weights">
                {presets.map((g) => {
                  const ex = exampleFor(g);
                  return (
                    <button
                      key={g}
                      type="button"
                      className="wg-preset"
                      aria-pressed={quote?.grams === g}
                      onClick={() => preset(g)}
                    >
                      {w.describeGrams(g)}
                      <small>{ex ? rupees(ex.line_paise) : ' '}</small>
                    </button>
                  );
                })}
              </div>

              <div className="wg-keys" role="group" aria-label="Keypad">
                {KEYS.flat().map((k) => (
                  <button
                    key={k}
                    type="button"
                    className={k === '⌫' || k === '.' ? 'wg-key fn' : 'wg-key'}
                    aria-label={k === '⌫' ? 'Delete the last digit' : k === '.' ? 'Decimal point' : k}
                    disabled={k === '.' && (unit === 'g' || text.includes('.'))}
                    onClick={() => key(k)}
                  >
                    {k}
                  </button>
                ))}
              </div>

              <div className="wg-sum" aria-live="polite">
                {quoteErr ? (
                  <Refusal reason={quoteErr.reason} detail={quoteErr.detail} />
                ) : quote ? (
                  <>
                    <span className="wg-sum-l">The line</span>
                    <span className="wg-sum-eq">
                      ₹{quote.price_per_kg_rupees} a kilo × {quote.weight}
                    </span>
                    <span className="wg-sum-v">{rupees(quote.line_paise)}</span>
                    <span className="wg-sum-drop">
                      {quote.dropped_thousandths_of_a_paisa > 0
                        ? `${w.droppedText(quote.dropped_thousandths_of_a_paisa)} dropped — it goes to the customer.`
                        : 'Divides exactly; nothing dropped.'}
                      {' '}<span className="mono">{quote.arithmetic}</span>
                    </span>
                  </>
                ) : (
                  <>
                    <span className="wg-sum-l">The line</span>
                    <span className="wg-sum-v quiet">{quoting ? '…' : '—'}</span>
                    <span className="wg-sum-why">
                      {text ? 'Pricing…' : 'Type a weight, or press one of the four above.'}
                    </span>
                  </>
                )}
              </div>

              <Button
                variant="primary"
                size="lg"
                block
                className="wg-add"
                loading={adding}
                disabled={!canAdd}
                onClick={() => void add()}
              >
                {quote ? `WRITE THE LINE · ${rupees(quote.line_paise)}` : 'WRITE THE LINE'}
              </Button>

              {addErr && <Refusal reason={addErr.reason} detail={addErr.detail} />}

              {recent.length > 0 && (
                <Verdict tone="info" title={`Written down: ${recent[0]!.name} · ${recent[0]!.weight}, ${rupees(recent[0]!.line_paise)}`}>
                  Line <span className="mono">{recent[0]!.line_id}</span> is on the weighed chain
                  {recent[0]!.audited ? '' : ' (the chain could not be written; the line file was)'}
                  {' '}and is waiting for the Till.
                </Verdict>
              )}

              <div className="wg-limit" role="note">
                <IcoWarn size={15} />
                <span>
                  A weighed line is priced and written down here, and handed to the Till. It
                  cannot yet be charged through the gateway: the money service prices packets
                  from its own book and does not price a weight. The full statement is under
                  the rule, below.
                </span>
              </div>
            </div>
          )}
        </Card>

        {/* ---------------------------------------------------- the products -- */}
        <div className="stack">
          <Card
            title="Sold by weight"
            sub="the per-kilo price, and what the presets come to"
            aside={<Pill tone={items.length ? 'code' : 'off'}>{items.length} PRODUCT{items.length === 1 ? '' : 'S'}</Pill>}
          >
            {loading ? (
              <LoadingCard lines={3} label="Reading" />
            ) : items.length === 0 ? (
              <Empty title="No product is sold by weight">
                Everything in this shop is billed as a packet at its catalogue price. A product
                marked here keeps that packet price for anything the camera reads, and gains a
                per-kilo price for a weight typed on the scale above. Mark one below.
              </Empty>
            ) : (
              <div className="wg-list">
                {items.map((i) => (
                  <div className="wg-item" key={i.sku_id}>
                    <div className="wg-item-main">
                      <div className="wg-item-name">
                        {i.name}
                        <span className="wg-perkg">₹{i.price_per_kg_rupees} a kilo</span>
                        {!i.in_catalogue && <Pill tone="amb">NOT IN THE CATALOGUE</Pill>}
                      </div>
                      <span className="wg-item-sku">{i.sku_id}</span>
                      <div className="wg-examples">
                        {i.examples.map((e) => (
                          <span key={e.grams}>{e.weight} <b>{rupees(e.line_paise)}</b></span>
                        ))}
                      </div>
                      {i.catalogue_price_paise !== undefined && (
                        <div className="wg-item-note">
                          A packet of this the camera reads is still billed at its catalogue price,
                          {' '}{rupees(i.catalogue_price_paise)}. The per-kilo price applies to a weight
                          entered here.
                        </div>
                      )}
                    </div>
                    <div className="wg-item-actions">
                      <Button size="sm" onClick={() => { setSku(i.sku_id); document.getElementById('wg-weight')?.focus(); }}>
                        WEIGH
                      </Button>
                      <Button size="sm" variant="ghost" disabled={busy === i.sku_id} onClick={() => void doUnmark(i.sku_id)}>
                        UNMARK
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* A REFUSED UNMARK, UNDER ITS OWN NAME. It used to be filed under
                "Products sold by weight could not be read" at the top of the
                page — a heading about a request that had just succeeded. */}
            {unmarkErr && (
              <div className="wg-unmark-err">
                <Refusal
                  reason={`${unmarkErr.sku_id} could not be unmarked`}
                  detail={unmarkErr.reason}
                  hint={unmarkErr.detail}
                  action={<Button size="sm" onClick={() => setUnmarkErr(null)}>DISMISS</Button>}
                />
              </div>
            )}

            <div className="wg-form">
              <Field
                label="Mark a product as sold by weight"
                htmlFor="wg-mark-sku"
                sub={
                  markable.length ? undefined
                    // NOT A CLAIM WHILE IT IS STILL READING. This printed
                    // "Every priced product is already sold by weight" during
                    // the very first load, when it had read nothing at all.
                    : loading ? 'Reading the catalogue…'
                      : items.length ? 'Every priced product is already sold by weight.'
                        : 'Nothing is priced yet. Teach a product with a price first.'
                }
              >
                <Select
                  id="wg-mark-sku"
                  value={markSku}
                  onChange={(e) => setMarkSku(e.target.value)}
                  disabled={!markable.length}
                  aria-describedby={markable.length ? undefined : 'wg-why-mark'}
                >
                  {markable.length === 0 && <option value="">—</option>}
                  {markable.map((m) => (
                    <option key={m.sku_id} value={m.sku_id}>{m.name} — packet {rupees(m.price_paise)}</option>
                  ))}
                </Select>
              </Field>
              <Field label="Price per kilo, in rupees" htmlFor="wg-perkg">
                {/* TEXT, NOT number: "45.99" as a JSON number is a float on
                    arrival. The server reads the string to paise. */}
                <Input
                  id="wg-perkg"
                  type="text"
                  inputMode="decimal"
                  placeholder="45.99"
                  value={perKg}
                  disabled={!markable.length}
                  aria-describedby={markable.length ? undefined : 'wg-why-mark'}
                  onChange={(e) => setPerKg(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') void doMark(); }}
                />
              </Field>
              {markErr && <Refusal reason={markErr.reason} detail={markErr.detail} />}
              <div className="btn-row">
                <Button
                  variant="primary"
                  loading={marking}
                  disabled={!markable.length || !perKg.trim()}
                  aria-describedby={markWhy ? 'wg-why-mark' : undefined}
                  onClick={() => void doMark()}
                >
                  MARK IT
                </Button>
              </div>
              {/* A DISABLED CONTROL SAYS WHY — and there are three reasons this
                  one is dead, which is exactly why it had to start saying. */}
              {markWhy && <WhyDead id="wg-why-mark">{markWhy}</WhyDead>}
            </div>
          </Card>

          <Card
            title="Waiting for the Till"
            sub="written here, not yet on a bill"
            aside={<Pill tone={pending.length ? 'code' : 'off'}>{pending.length} LINE{pending.length === 1 ? '' : 'S'}</Pill>}
          >
            {pending.length === 0 ? (
              <Empty
                title="Nothing is waiting"
                action={
                  items.length > 0
                    ? <Button onClick={() => jumpTo('wg-weight')}>WEIGH SOMETHING</Button>
                    : undefined
                }
              >
                A weight written on the scale above is priced, put on the weighed chain under its own
                line id, and parked here until the Till puts it on a bill. This screen never touches
                the bill itself — the Till owns the basket.
                {items.length === 0 && ' Nothing is sold by weight yet, so nothing can be written.'}
              </Empty>
            ) : (
              <div className="wg-recent">
                {pending.map((p) => (
                  <div className="wg-recent-row" key={p.line_id}>
                    <span>{p.basket_line.name}</span>
                    <b>₹{p.line_rupees}</b>
                    <span className="wg-id">{p.line_id}</span>
                    <span />
                    <Button size="sm" variant="ghost" onClick={() => forget(p.line_id)}>FORGET</Button>
                  </div>
                ))}
                <div className="btn-row">
                  <a className="btn sm wg-linkbtn" href="#/till">OPEN THE TILL</a>
                </div>
              </div>
            )}
          </Card>
        </div>
      </div>

      {/* --------------------------------------------------------- the rule -- */}
      <Card title="The rule" sub="how a weight becomes paise, and what this counter cannot do with it">
        {loading ? (
          <LoadingCard lines={3} label="Reading the rule" />
        ) : (book?.rule ?? health?.rule) ? (
          <p className="wg-lede">{book?.rule ?? health?.rule}</p>
        ) : (
          /* NOT A SENTENCE THIS PAGE WROTE. Both requests that carry the rule
             refused, and the server's own words for why go here — the rule is
             the one thing on this screen that must not be paraphrased. */
          <Refusal
            reason="The rule could not be read from the server"
            detail={(healthErr ?? err)?.reason}
            hint={(healthErr ?? err)?.detail}
            action={<Button size="sm" onClick={() => void load()}>TRY AGAIN</Button>}
          />
        )}
        <div className="wg-kvs">
          <KV k="The sum"><span className="wg-rule">line_paise = price_per_kg_paise × grams // 1000</span></KV>
          <KV k="The odd paisa">
            Dropped, never rounded up. The customer keeps it; the shop gives up less than one paisa a line, and the screen says how much.
          </KV>
          <KV k="Never">A float, a division that is not integer, or a price sent by this page.</KV>
          {book && (
            <>
              <KV k="A line weighs">
                {book.limits.min_grams} g to {w.describeGrams(book.limits.max_grams)}. More is two lines.
              </KV>
              <KV k="A kilo costs">
                {rupees(book.limits.min_price_per_kg_paise)} to {rupees(book.limits.max_price_per_kg_paise)}.
              </KV>
              <KV k="Presets">{book.presets_grams.map(w.describeGrams).join(' · ')}</KV>
            </>
          )}
          {health && (
            <>
              <KV k="The file"><span className="mono">{health.file}</span>{health.exists ? '' : ' (not written yet)'}</KV>
              <KV k="Written lines"><span className="mono">{health.lines_dir}</span></KV>
              <KV k="The chain"><span className="mono">{health.audit}</span></KV>
            </>
          )}
        </div>
        {(book?.mint_note ?? health?.mint_note) && (
          <div className="wg-limit" role="note" style={{ marginTop: 'var(--s4)' }}>
            <IcoWarn size={15} />
            <span>{book?.mint_note ?? health?.mint_note}</span>
          </div>
        )}
      </Card>
    </div>
  );
}
