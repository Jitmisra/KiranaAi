import { useCallback, useEffect, useMemo, useState } from 'react';
import * as offersapi from '../lib/offersapi';
import { rupees } from '../lib/money';
import {
  Button, Card, Empty, KV, Modal, Pill, Refusal, Skeleton, Verdict,
} from '../components/ui';
import '../styles/offers.css';
import '../styles/manage.css';

/**
 * Offers — the shopkeeper writes a discount, and it is what gets charged.
 *
 * THE ONE THING THIS PAGE DOES NOT DO is work out a price. Every rupee on the
 * screen was computed by the server and sent here as integer paise; the page
 * formats them and nothing else. That is not fussiness. The money service is a
 * separate process holding the gateway keys, and it re-prices every basket from
 * its own price book before it mints — so a number this page invented would be
 * a number paisa disagrees with, and the mint would be refused with a customer
 * standing at the counter. The page describes an intent; the server decides.
 *
 * WHY THE FORM ASKS FOR RUPEES AS TEXT. `off_rupees` goes up as the string the
 * shopkeeper typed. A decimal sent as a JSON number arrives as a float, and
 * `float('5.10')` is lossy before anything rounds it. The server parses the text
 * to integer paise itself.
 *
 * WHY THERE IS NO "₹20 OFF OVER ₹500". A price book answers one question — what
 * does ONE of these cost — so a whole-bill threshold cannot be expressed in it
 * and would fail at the moment money moves. The server refuses that kind by name
 * rather than accepting it and breaking later, and this form does not offer it.
 */

/** Reads back what the shopkeeper typed, so a wrong field is obvious before saving. */
function Preview({ kind, amount, percent }: { kind: offersapi.OfferKind; amount: string; percent: string }) {
  if (kind === 'flat') {
    const t = amount.trim();
    if (!t) return <span className="off-preview muted">Type how many rupees come off.</span>;
    if (!/^\d{1,7}(\.\d{1,2})?$/.test(t)) {
      return <span className="off-preview bad">₹{t} is not an amount. Two decimal places at most.</span>;
    }
    return <span className="off-preview">Takes <b>₹{t}</b> off each one.</span>;
  }
  const n = Number(percent);
  if (!percent.trim()) return <span className="off-preview muted">Type a whole percentage.</span>;
  if (!Number.isInteger(n) || n < 1 || n > 99) {
    return <span className="off-preview bad">A percentage runs 1 to 99. 100% would make it free.</span>;
  }
  return <span className="off-preview">Takes <b>{n}%</b> off each one.</span>;
}

export default function Offers() {
  const [book, setBook] = useState<offersapi.OfferBook | null>(null);
  const [priced, setPriced] = useState<offersapi.PriceBook | null>(null);
  const [health, setHealth] = useState<offersapi.OffersHealth | null>(null);
  const [err, setErr] = useState<{ reason: string; detail?: string } | null>(null);
  const [loading, setLoading] = useState(true);

  const [kind, setKind] = useState<offersapi.OfferKind>('flat');
  const [sku, setSku] = useState<string>('*');
  const [amount, setAmount] = useState('');
  const [percent, setPercent] = useState('');
  const [label, setLabel] = useState('');
  const [saveErr, setSaveErr] = useState<{ reason: string; detail?: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  /**
   * A refusal of an ACTION, kept apart from a refusal of the READ.
   *
   * Turning an offer off and failing used to set the same state the initial
   * fetch sets, so the screen answered "The offers could not be read" — which
   * is not what happened, and sends the shopkeeper to look at the wrong thing.
   * The server's own words for the thing that actually failed go here, and the
   * list stays on the screen underneath them.
   */
  const [actErr, setActErr] = useState<{ reason: string; detail?: string; what: string } | null>(null);

  /** The offer a DELETE has been asked for. Deleting changes what the gateway
      charges from the next basket onward and the file has no undo, so it is
      confirmed rather than done on the first press. */
  const [dropping, setDropping] = useState<offersapi.Offer | null>(null);

  /** The price book could not be read, separately from the offers. Without it
      the screen cannot say which prices an offer reaches, and it says so
      rather than showing an empty table that reads as "no products". */
  const [priceErr, setPriceErr] = useState<{ reason: string; detail?: string } | null>(null);

  const load = useCallback(async () => {
    const [b, p, h] = await Promise.all([offersapi.list(), offersapi.prices(), offersapi.health()]);
    if (b.ok) {
      setBook(b);
      setErr(null);
    } else {
      setErr(b);
    }
    if (p.ok) { setPriced(p); setPriceErr(null); } else { setPriced(null); setPriceErr(p); }
    setHealth(h.ok ? h : null);
    setLoading(false);
  }, []);

  useEffect(() => { void load(); }, [load]);

  const submit = useCallback(async () => {
    setSaving(true);
    setSaveErr(null);
    const body: Parameters<typeof offersapi.create>[0] = {
      sku_id: sku === '*' ? null : sku,
      kind,
      ...(label.trim() ? { label: label.trim() } : {}),
    };
    if (kind === 'flat') body.off_rupees = amount.trim();
    else body.percent = Number(percent);
    const r = await offersapi.create(body);
    setSaving(false);
    if (r.ok) {
      setAmount('');
      setPercent('');
      setLabel('');
      await load();
    } else {
      setSaveErr(r);
    }
  }, [sku, kind, amount, percent, label, load]);

  const toggle = useCallback(async (o: offersapi.Offer, next: boolean) => {
    setBusy(o.offer_id);
    setActErr(null);
    const r = await offersapi.setActive(o.offer_id, next);
    setBusy(null);
    if (r.ok) await load();
    else setActErr({ ...r, what: `${next ? 'Turning on' : 'Turning off'} “${o.says}” was refused` });
  }, [load]);

  const drop = useCallback(async (o: offersapi.Offer) => {
    setBusy(o.offer_id);
    setActErr(null);
    const r = await offersapi.remove(o.offer_id);
    setBusy(null);
    setDropping(null);
    if (r.ok) await load();
    else setActErr({ ...r, what: `Deleting “${o.says}” was refused` });
  }, [load]);

  /** Products the shopkeeper can pick, newest prices first from the server. */
  const products = useMemo(() => priced?.items ?? [], [priced]);
  const discounted = useMemo(() => products.filter((p) => p.off_paise > 0), [products]);
  const rows = book?.offers ?? [];

  /**
   * WHICH PRICES EACH OFFER IS ACTUALLY CHANGING, right now.
   *
   * This is the question the screen exists to answer and the one it could not.
   * `/offers/prices` is the same file the money service re-prices every basket
   * from, and it stamps each discounted row with the `offer_id` that won it —
   * so counting the rows carrying an id is a COUNT of the server's own
   * decisions, not a second implementation of the discount arithmetic. No
   * price is computed here and none ever should be: FAILURES.md records a
   * storefront quoting the shelf price while the mint derived the discounted
   * one, and that is what a browser deciding this for itself looks like.
   */
  const reach = useMemo(() => {
    const m = new Map<string, number>();
    for (const p of products) {
      if (p.offer_id) m.set(p.offer_id, (m.get(p.offer_id) ?? 0) + 1);
    }
    return m;
  }, [products]);

  /**
   * THE OFFER NAMES A PRODUCT THIS SHOP NO LONGER HAS.
   *
   * Deleting a product takes away every place that could price it, and the
   * offer file keeps its row — switched ON, pointing at a sku that is in no
   * price book. That is a different fact from "an offer that reaches nothing":
   * an offer on `*` in an empty shop reaches nothing too, and the fix for the
   * two is not the same. This one has a NAME in it, and naming it is the whole
   * repair — an offer sitting on a deleted sku is a discount waiting to come
   * back to life the moment somebody teaches that sku again.
   *
   * The test is against the price book the money service itself mints from, so
   * "not in this list" means exactly "the gateway will not find it either".
   * With the price book unread the answer is `null`, never `true`: this screen
   * does not accuse a product of being missing on the strength of a request
   * that did not answer.
   */
  const missingSku = useCallback((o: offersapi.Offer): string | null => {
    if (!priced || !o.sku_id) return null;
    return products.some((p) => p.sku_id === o.sku_id) ? null : o.sku_id;
  }, [priced, products]);

  /** The switched-on offers whose product is gone. */
  const orphaned = useMemo(
    () => rows.filter((o) => o.active && missingSku(o) !== null),
    [rows, missingSku],
  );

  /**
   * An offer is switched on and not one price is different because of it —
   * MINUS the ones `orphaned` above already names.
   *
   * The shop believes it is running a discount and the gateway will charge the
   * full marked price on every basket. An offer on a deleted sku reaches
   * nothing too, so this set used to contain every one of those and the screen
   * said the same thing twice in two stacked amber panels. That case has a name
   * and a sharper repair, so it belongs to that panel; what is left here is the
   * residue — an offer on `*` in a shop with nothing priced, or one whose
   * product IS in the price book and still won no row.
   */
  const deadUnexplained = useMemo(
    () => rows.filter((o) => o.active && !reach.get(o.offer_id) && missingSku(o) === null),
    [rows, reach, missingSku],
  );

  /** What stops the offer being made, in the words the shopkeeper needs. */
  const cannotMake = kind === 'flat'
    ? (!amount.trim() ? 'Type how many rupees come off first.' : null)
    : (!percent.trim() ? 'Type a whole percentage first.' : null);

  return (
    <div className="off-page">
      <div className="page-head">
        <h1>Offers</h1>
        <p>
          A discount here is not a note on the bill. It goes into the price book the money service
          re-prices every basket from, so what the counter shows and what the gateway charges are
          the same number — computed twice, in two processes, from one file.
        </p>
      </div>

      {err && (
        <div>
          <Refusal
            reason="The offers could not be read"
            detail={err.reason}
            hint={err.detail}
            action={<button className="btn sm" onClick={() => void load()}>TRY AGAIN</button>}
          />
        </div>
      )}

      {/* A REFUSED ACTION, IN THE SERVER'S OWN WORDS, naming the offer it was
          about. Kept above the list rather than inside the row, because the
          list is reloaded on every attempt and a message parked in a row
          disappears with it. */}
      {actErr && (
        <div>
          <Refusal
            reason={actErr.what}
            detail={actErr.reason}
            hint={actErr.detail}
            action={<Button size="sm" onClick={() => setActErr(null)}>DISMISS</Button>}
          />
        </div>
      )}

      {book && !book.catalogue_known && (
        <div>
          <Verdict tone="amber" title="The catalogue could not be read">
            The offers below are what is on disk. What each one does to a price cannot be shown,
            because the prices themselves are not readable from here, and nothing has been guessed
            in their place.
          </Verdict>
        </div>
      )}

      {/* THE ONE FINDING THIS SCREEN OWES THE SHOPKEEPER. An offer that is on
          and reaches nothing is a sign on the shutter the till will not honour.
          Amber, because it is money the counter is not taking off — the same
          thing amber means everywhere else here — and it names the offers. */}
      {!loading && priced && deadUnexplained.length > 0 && (
        <div>
          <Verdict
            tone="amber"
            title={deadUnexplained.length === 1
              ? 'An offer is switched on and is changing no price'
              : `${deadUnexplained.length} offers are switched on and are changing no price`}
          >
            {deadUnexplained.map((o) => <b className="off-dead-say" key={o.offer_id}>{o.says}</b>)}
            {' '}
            {deadUnexplained.length === 1 ? 'It applies to a product' : 'Each applies to a product'} the
            price book does not hold, so the money service will charge the full marked price on
            every basket. Point the offer at a product that is priced, or turn it off — the shutter
            should not promise what the gateway will not do.
          </Verdict>
        </div>
      )}

      {/* THE PRODUCT IS GONE AND THE OFFER IS STILL ON.
          Named, because "an offer that reaches nothing" and "an offer on a
          product you deleted" need different repairs and only one of them has a
          sku in it. This is the dangerous one: the row is switched ON and is
          waiting for that sku to come back, so teaching the product again
          quietly resurrects a discount nobody re-approved. Amber — money this
          counter is not taking off — and never red: nothing has refused. */}
      {!loading && priced && orphaned.length > 0 && (
        <div>
          <Verdict
            tone="amber"
            title={orphaned.length === 1
              ? `An offer is switched on for ${missingSku(orphaned[0]!)}, which is not in this shop`
              : `${orphaned.length} offers are switched on for products that are not in this shop`}
          >
            {orphaned.map((o) => (
              <b className="off-dead-say" key={o.offer_id}>
                {o.says} — <span className="mono">{missingSku(o)}</span> is in no price book
              </b>
            ))}
            {' '}
            Deleting a product does not switch off the offers that name it. The row stays on disk,
            switched on, pointing at a sku the money service cannot find — so it changes nothing
            today and starts discounting again the moment that sku is taught back.
            <br />
            Two ways out: teach{' '}
            {orphaned.map((o) => missingSku(o)).filter(Boolean).join(', ')} again with a price on
            the Products screen, if the offer is still meant, or switch the offer off below.
          </Verdict>
        </div>
      )}

      <div className="offers-grid">
        <Card title="Make an offer" aside={<Pill tone="code">SERVER PRICES IT</Pill>}>
          <div className="off-form">
            <div className="field">
              <label htmlFor="off-sku">On what</label>
              {/* A SELECT WAITING FOR ITS OPTIONS IS NOT AN EMPTY SHOP. While
                  the price book was in flight this read "Nothing is priced yet.
                  Teach a product first." — a statement about the shop, made on
                  the strength of a request that had not answered. */}
              {loading ? (
                <Skeleton h={40} radius={10} />
              ) : (
                <select
                  id="off-sku"
                  value={sku}
                  onChange={(e) => setSku(e.target.value)}
                  disabled={!products.length}
                  title={products.length ? undefined
                    : 'There is nothing priced to put an offer on.'}
                >
                  <option value="*">Everything in the shop</option>
                  {products.map((p) => (
                    <option key={p.sku_id} value={p.sku_id}>
                      {p.name} — {rupees(p.base_paise)}
                    </option>
                  ))}
                </select>
              )}
              {loading ? (
                <span className="sub">Reading the price book the money service prices from…</span>
              ) : products.length ? (
                <span className="sub">
                  {products.length} priced product{products.length === 1 ? '' : 's'}. An offer on
                  a product that is not in this list reaches no price at all.
                </span>
              ) : priceErr ? (
                <span className="sub">
                  The price book could not be read, so this list is empty — that is this screen
                  failing, not the shop being empty.
                </span>
              ) : (
                <span className="sub">Nothing is priced yet. Teach a product first.</span>
              )}
            </div>

            <div className="field">
              <label>What kind</label>
              <div className="seg" role="group">
                {/* aria-pressed carries the selected state AND its styling —
                    `.seg button[aria-pressed='true']` is already in app.css, so
                    a second class here would be a second source of truth. */}
                <button type="button" aria-pressed={kind === 'flat'} onClick={() => setKind('flat')}>
                  RUPEES OFF
                </button>
                <button type="button" aria-pressed={kind === 'percent'} onClick={() => setKind('percent')}>
                  PERCENT OFF
                </button>
              </div>
            </div>

            {kind === 'flat' ? (
              <div className="field">
                <label htmlFor="off-amount">How much off, in rupees</label>
                {/* TEXT, NOT number. A decimal in a number input arrives as a
                    float and 5.10 is lossy before anything rounds it. The
                    server parses the string to integer paise. */}
                <input
                  id="off-amount"
                  type="text"
                  inputMode="decimal"
                  placeholder="5.00"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                />
              </div>
            ) : (
              <div className="field">
                <label htmlFor="off-percent">How many percent off</label>
                <input
                  id="off-percent"
                  type="text"
                  inputMode="numeric"
                  placeholder="10"
                  value={percent}
                  onChange={(e) => setPercent(e.target.value)}
                />
              </div>
            )}

            <div className="field">
              <label htmlFor="off-label">What to call it <span className="muted">(optional)</span></label>
              <input
                id="off-label"
                type="text"
                placeholder="Diwali"
                maxLength={60}
                value={label}
                onChange={(e) => setLabel(e.target.value)}
              />
            </div>

            <Preview kind={kind} amount={amount} percent={percent} />

            {/* WHY IT CANNOT BE PRESSED, beside it. The button sat greyed with
                no explanation whenever the amount box was empty, which on a
                form of four controls is a puzzle rather than a hint. */}
            <div className="btn-row off-make">
              <button
                className="btn primary"
                disabled={saving || !!cannotMake}
                title={cannotMake ?? undefined}
                onClick={() => void submit()}
              >
                {saving ? 'SAVING…' : 'MAKE THE OFFER'}
              </button>
              {cannotMake && !saving && <span className="off-cannot">{cannotMake}</span>}
            </div>

            {saveErr && (
              <div style={{ marginTop: 12 }}>
                <Refusal reason={saveErr.reason} detail={saveErr.detail} />
              </div>
            )}

            <p className="off-note">
              A percentage that does not divide evenly rounds the discount <b>up</b> — 10% of ₹9.99
              is ₹1.00 off, not 99 paise. The shop absorbs the part-paisa so the sign on the shutter
              is true.
            </p>
          </div>
        </Card>

        <Card
          title="What is on now"
          sub="an offer is only real where it reaches a price"
          aside={book && (
            /* NOT GREEN. Green on this product means a signature-verified
               webhook settled a payment; a count of switched-on offers is not
               that, and the shared layer says so in as many words. Blue is the
               machine's own mark and is what a count wears. */
            <Pill tone={book.active ? 'code' : 'off'}>{book.active} ACTIVE</Pill>
          )}
        >
          {loading ? (
            /* At the shape of an offer row: the sentence, the price change
               under it, and the switch out on the right. */
            <div className="off-list" role="status" aria-live="polite" aria-label="Reading the offers">
              {[0, 1].map((i) => (
                <div className="off-row off-skel" key={i} aria-hidden="true">
                  <div className="off-main">
                    <Skeleton w="58%" h={14} radius={999} />
                    <Skeleton w="38%" h={11} radius={999} />
                  </div>
                  <div className="off-actions"><Skeleton w={54} h={26} radius={999} /></div>
                </div>
              ))}
            </div>
          ) : !rows.length ? (
            <Empty title="No offer has been made">
              Every product sells at the price on its label, and the money service will charge
              exactly that. Make one on the left — it goes into the price book the gateway
              re-prices from, so the shutter and the bill cannot disagree.
            </Empty>
          ) : (
            <div className="off-list">
              {rows.map((o) => {
                const hits = reach.get(o.offer_id) ?? 0;
                const dead = o.active && hits === 0;
                return (
                  <div
                    className={`off-row${o.active ? '' : ' off-dim'}${dead ? ' off-nohit' : ''}`}
                    key={o.offer_id}
                  >
                    <div className="off-main">
                      <div className="off-says">
                        {o.says}
                        {o.label && <span className="off-label">{o.label}</span>}
                      </div>
                      {o.example ? (
                        <div className="off-effect">
                          {/* `base_rupees` is "10.00" — the server's string
                              carries no currency mark, so the struck-through
                              marked price rendered as a bare number beside a
                              ₹-prefixed one. Same integer, same formatter as
                              the charged price beside it. */}
                          <span className="off-was">{rupees(o.example.base_paise)}</span>
                          <span className="off-arrow" aria-hidden="true">→</span>
                          <b className="off-now">{rupees(o.example.price_paise)}</b>
                          <span className="muted">on {o.example.name}</span>
                        </div>
                      ) : missingSku(o) ? (
                        /* NAME THE SKU. "This counter cannot see a price to
                           apply it to" is true of an offer on an empty shop and
                           of an offer on a product somebody deleted, and only
                           the second one has a repair a shopkeeper can act on. */
                        <div className="off-effect muted">
                          <span className="mono">{missingSku(o)}</span> is not in this shop&rsquo;s
                          price book — the product was removed, or never taught with a price. Teach
                          it again to make this offer live, or switch the offer off.
                        </div>
                      ) : (
                        <div className="off-effect muted">
                          This counter cannot see a price to apply it to.
                        </div>
                      )}

                      {/* HOW MANY PRICES THIS OFFER IS CHANGING RIGHT NOW,
                          counted off the same price book the money service
                          mints from. Not a claim this page derived: each
                          discounted row is stamped by the server with the
                          offer that won it, and these are those stamps. */}
                      {priced && (
                        <div className={dead ? 'off-reach none' : o.active ? 'off-reach' : 'off-reach idle'}>
                          {!o.active
                            ? `Switched off. It would reach ${hits === 0 ? 'no price' : `${hits} price${hits === 1 ? '' : 's'}`} if it were on.`
                            : dead
                              ? 'Switched on and changing no price. The gateway will charge the full marked price.'
                              : `Changing ${hits} of ${products.length} price${products.length === 1 ? '' : 's'} the gateway will charge.`}
                        </div>
                      )}

                      {o.clamped && (
                        <div className="off-clamp">
                          Held back. This discount is worth more than the product, so the price is
                          being kept at the minimum instead of going to nothing. Fix the offer or the
                          price — a packet is never free.
                        </div>
                      )}
                    </div>
                    <div className="off-actions">
                      {/* The switch pattern: on/off readable from across the
                          room. Same call, same guard as the old TURN ON/OFF
                          button. */}
                      <span className="mg-state">
                        <button
                          className="mg-switch"
                          role="switch"
                          aria-checked={o.active}
                          aria-label={o.active ? `Turn off ${o.says}` : `Turn on ${o.says}`}
                          disabled={busy === o.offer_id}
                          title={busy === o.offer_id ? 'The server is being asked now.' : undefined}
                          onClick={() => void toggle(o, !o.active)}
                        />
                        <span className={o.active ? 'mg-onoff on' : 'mg-onoff'}>
                          {o.active ? 'ON' : 'OFF'}
                        </span>
                      </span>
                      <button
                        className="btn sm ghost"
                        disabled={busy === o.offer_id}
                        title={busy === o.offer_id ? 'The server is being asked now.' : undefined}
                        onClick={() => setDropping(o)}
                      >
                        DELETE
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      </div>

      <Card
        title="What each thing costs now"
        sub="every price the money service will charge from the next basket"
        aside={priced && (
          /* A count of discounted prices is not settled money either. */
          <Pill tone={discounted.length ? 'code' : 'off'}>{discounted.length} DISCOUNTED</Pill>
        )}
      >
        <p className="off-lede">
          The marked price, and what the counter and the gateway will both charge. These are the
          server&rsquo;s numbers, read from the same file the money service prices from.
        </p>
        {loading ? (
          /* At the shape of the table, money bars right where the figures go. */
          <div className="off-skel-tbl" role="status" aria-live="polite" aria-label="Reading the price book">
            {[0, 1, 2].map((r) => (
              <div className="off-skel-row" key={r} aria-hidden="true">
                <Skeleton w="42%" h={12} radius={999} />
                <span className="n"><Skeleton w={62} h={12} radius={999} /></span>
                <span className="n"><Skeleton w={44} h={12} radius={999} /></span>
                <span className="n"><Skeleton w={62} h={12} radius={999} /></span>
              </div>
            ))}
          </div>
        ) : priceErr ? (
          <Refusal
            reason="The price book could not be read"
            detail={priceErr.reason}
            hint={priceErr.detail}
            action={<Button size="sm" onClick={() => void load()}>TRY AGAIN</Button>}
          />
        ) : !products.length ? (
          <Empty title="Nothing is priced yet">
            An offer can only come off a price, and there are none. Teach a product on the
            Products screen and give it a price; it appears here, and any offer aimed at it
            starts reaching the gateway.
          </Empty>
        ) : (
          <div className="scroll-x">
            <table className="off-table">
              <thead>
                <tr>
                  <th>Product</th>
                  <th className="num">Marked</th>
                  <th className="num">Off</th>
                  <th className="num">Charged</th>
                </tr>
              </thead>
              <tbody>
                {products.map((p) => (
                  <tr key={p.sku_id} className={p.off_paise > 0 ? 'off-hit' : undefined}>
                    <td>
                      {p.name}
                      <span className="mono off-sku">{p.sku_id}</span>
                      {/* WHICH OFFER DID THIS, on the row it did it to. The
                          server names it; the page only looks it up. */}
                      {p.offer_id && (
                        <span className="off-by">
                          {rows.find((o) => o.offer_id === p.offer_id)?.says ?? p.offer_id}
                        </span>
                      )}
                    </td>
                    {/* Marked struck through where a discount applies — the
                        same marked → charged pattern as everywhere else. */}
                    <td className="num">
                      {p.off_paise > 0
                        ? <s className="mg-was">{rupees(p.base_paise)}</s>
                        : rupees(p.base_paise)}
                    </td>
                    <td className="num">
                      {p.off_paise > 0 ? `− ${rupees(p.off_paise)}` : <span className="muted">—</span>}
                    </td>
                    <td className="num">
                      <b>{rupees(p.price_paise)}</b>
                      {p.clamped && <Pill tone="amb">HELD</Pill>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {!loading && products.length > 0 && discounted.length === 0 && (
          <p className="off-lede off-none">
            No price is discounted. Everything above is charged at its marked price, whatever the
            offers list says.
          </p>
        )}
      </Card>

      {health && (
        <Card title="Where the offers live">
          <p className="off-lede">
            The till and the money service are two processes and each works this path out for
            itself. If they ever name different files, the counter shows a discount the gateway has
            never read and every payment is refused — so the path is printed rather than assumed.
          </p>
          <KV k="Offers file"><span className="mono">{health.file}</span></KV>
          <KV k="On disk">{health.exists ? 'yes' : 'not written yet'}</KV>
          <KV k="Offers">{health.offers} · {health.active} on</KV>
          <KV k="Rounding">{health.rounding}</KV>
        </Card>
      )}

      {/* DELETING AN OFFER CHANGES WHAT THE GATEWAY CHARGES, from the next
          basket, and the file it is removed from keeps no history of it. That
          is an irreversible action taken on money, so it is asked for once and
          confirmed once, and the dialog says what will be different afterwards
          rather than "are you sure". */}
      <Modal
        open={dropping !== null}
        onClose={() => setDropping(null)}
        size="narrow"
        title="Delete this offer?"
        sub="It is removed from the price book. There is no undo."
        note={dropping && busy === dropping.offer_id
          ? 'Removing it…'
          : 'The counter and the gateway both stop applying it at once.'}
        foot={
          <>
            <Button
              onClick={() => setDropping(null)}
              disabled={!!dropping && busy === dropping.offer_id}
            >
              KEEP IT
            </Button>
            <Button
              variant="danger"
              disabled={!!dropping && busy === dropping.offer_id}
              onClick={() => { if (dropping) void drop(dropping); }}
            >
              {dropping && busy === dropping.offer_id ? 'DELETING…' : 'DELETE THE OFFER'}
            </Button>
          </>
        }
      >
        {dropping && (
          <div className="off-drop">
            <div className="off-says">
              {dropping.says}
              {dropping.label && <span className="off-label">{dropping.label}</span>}
            </div>
            <p className="off-lede" style={{ marginBottom: 0 }}>
              {(() => {
                const hits = reach.get(dropping.offer_id) ?? 0;
                if (!dropping.active) {
                  return 'It is switched off, so no price changes when it goes — but it cannot be '
                    + 'switched back on afterwards either.';
                }
                if (hits === 0) {
                  return 'It is switched on and is currently changing no price, so nothing the '
                    + 'gateway charges will be different.';
                }
                return `It is changing ${hits} price${hits === 1 ? '' : 's'} right now. Those `
                  + `product${hits === 1 ? '' : 's'} go back to the marked price the moment this `
                  + 'is deleted.';
              })()}
            </p>
            {dropping.example && (
              <div className="off-effect">
                <span className="off-was">{rupees(dropping.example.price_paise)}</span>
                <span className="off-arrow" aria-hidden="true">→</span>
                <b className="off-now">{rupees(dropping.example.base_paise)}</b>
                <span className="muted">on {dropping.example.name}</span>
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}
