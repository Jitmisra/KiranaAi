import { useCallback, useEffect, useState } from 'react';
import * as shopapi from '../lib/shopapi';
import { ago } from '../lib/inbound';
import { rupees } from '../lib/money';
import { STATUS_LINE, STATUS_LABEL } from '../routes/Shop';
import { Card, Field, Pill, Refusal, Verdict, Empty, LoadingCard } from './ui';

/**
 * MY ORDERS — the customer's own history, and the only place they identify
 * themselves.
 *
 * TWO RULES THIS SCREEN EXISTS TO KEEP, both of them the server's:
 *
 *  1. A CUSTOMER HAS NO PASSWORD AND MUST NOT BE GIVEN ONE. The shopkeeper's
 *     `#/signin` wants a phone AND a password and mints a counter session; a
 *     stranger who photographed a shutter has neither and never will. See the
 *     block comment above the customer-identity section of
 *     gawaah/storefront.py for why that is a decision and not an omission.
 *  2. READING BACK A NUMBER'S ORDERS NEEDS PROOF THE NUMBER IS YOURS, and the
 *     proof is the id of an order that number placed. A phone number is not a
 *     secret — without this rule, typing anyone's number into a box would read
 *     back their whole order history, every item and every address status. So
 *     `/store/customer/orders` refuses a session that has only been TOLD a
 *     number (`customer_has_not_proved_this_number`).
 *
 * The screen's whole job is to make rule 2 legible instead of arriving as a
 * silent refusal. A customer who has ordered here has the id on their order
 * screen and in their own hand; one who has not gets told that, plainly, and
 * is not left staring at a form they cannot complete.
 */

/** Which box on the form each of the server's refusals is about. */
const FIELD_OF_REFUSAL: Record<string, 'name' | 'phone' | 'order'> = {
  customer_name_missing: 'name',
  customer_phone_missing: 'phone',
  customer_phone_not_a_number: 'phone',
  customer_has_not_proved_this_number: 'order',
};

export default function CustomerOrders({ me, onIdentity, onOpen }: {
  /** Who this phone is, as the SHELL read it. Null while it is still asking. */
  me: shopapi.CustomerMe | null;
  /** Ask the shell to re-read `/store/customer/me` — this screen changed it. */
  onIdentity: () => void;
  /** Open one order on the storefront's own order screen. */
  onOpen: (orderId: string) => void;
}) {
  const who = me?.customer ?? null;
  const verified = who?.verified === true;

  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [proof, setProof] = useState('');
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<shopapi.Refusal | null>(null);

  const [orders, setOrders] = useState<shopapi.CustomerOrder[] | null>(null);
  const [listRefusal, setListRefusal] = useState<shopapi.Refusal | null>(null);

  /* The form starts from what the shop already knows, so a customer who is
     signed in but unproven types ONE box — the order id — rather than three.
     Keyed on `who`, and it never overwrites something being typed. */
  useEffect(() => {
    if (!who) return;
    setName((n) => n || who.name);
    setPhone((p) => p || who.phone);
  }, [who]);

  /* AND WHEN THE SHOP KNOWS NOTHING, from what this browser typed at the
     basket. Placing an order creates no customer session — measured: a 200
     from `POST /store/order` sets no cookie and `/store/customer/me` still
     answers `customer: null` — so `who` is null for exactly the person most
     likely to be standing here: someone who ordered a minute ago and tapped
     MY ORDERS. They then met `customer_name_missing`, because this screen
     asks for an order id, they gave an order id, and the two boxes above it
     were empty. Runs once, after the shell has finished asking (`me !== null`)
     so the server's answer always wins; `||` so it never overwrites typing. */
  useEffect(() => {
    if (me === null || who) return;
    const last = shopapi.lastCustomer();
    if (!last) return;
    setName((n) => n || last.name);
    setPhone((p) => p || last.phone);
  }, [me, who]);

  const readOrders = useCallback(async () => {
    const r = await shopapi.customerOrders();
    if (r.ok) { setOrders(r.orders); setListRefusal(null); return; }
    // NOT AN ERROR — the two reasons this refuses are the privacy rule itself,
    // and both are already explained above the form. It is kept so a refusal
    // this screen has never seen still arrives in full rather than vanishing.
    setOrders(null);
    setListRefusal(r);
  }, []);

  useEffect(() => {
    if (!verified) { setOrders(null); setListRefusal(null); return; }
    void readOrders();
  }, [verified, readOrders]);

  const signIn = useCallback(async () => {
    setBusy(true);
    setRefusal(null);
    const r = await shopapi.customerSignIn(name, phone, proof.trim() || undefined);
    setBusy(false);
    if (!r.ok) { setRefusal(r); return; }
    setProof('');
    onIdentity();
  }, [name, phone, proof, onIdentity]);

  const signOut = useCallback(async () => {
    setBusy(true);
    await shopapi.customerSignOut();
    setBusy(false);
    setOrders(null);
    setProof('');
    /* SIGN OUT HAS TO CLEAR THE BOXES TOO, and this is not tidiness — without
       it the button does nothing a customer can see. The prefill effect above
       re-runs the moment `who` becomes null, refills the name and number from
       this browser's own store, and the screen looks exactly as it did. On a
       phone lent to a neighbour that is the whole point of the button. */
    shopapi.forgetCustomer();
    setName('');
    setPhone('');
    onIdentity();
  }, [onIdentity]);

  const bad = refusal ? FIELD_OF_REFUSAL[refusal.reason] : undefined;

  if (me === null) {
    return (
      <div className="cx-orders cx-page">
        <header className="cx-head">
          <h1>Your orders</h1>
          <p>Asking the shop who this phone is.</p>
        </header>
        <LoadingCard lines={3} label="Reading who this phone is" />
      </div>
    );
  }

  return (
    <div className="cx-orders cx-page">
      <header className="cx-head">
        <h1>Your orders</h1>
        <p>
          {verified
            ? `Every order ${who?.phone} has placed at this shop, newest first.`
            : 'Everything you have ordered here, once the shop knows the number is yours.'}
        </p>
      </header>

      {/* ---- the list, for a session that has proved its number ---------- */}
      {verified && (
        <Card
          title="What you have ordered"
          aside={orders ? <Pill tone="code">{orders.length}</Pill> : undefined}
          tight
        >
          {listRefusal ? (
            <Refusal
              reason={listRefusal.reason}
              detail={listRefusal.detail}
              hint="Nothing was changed. Sign in again with an order id below."
            />
          ) : orders === null ? (
            <LoadingCard lines={3} label="Reading your orders" />
          ) : orders.length === 0 ? (
            <Empty>
              This number has not ordered anything from this shop yet.
              <br />
              Fill a basket and it will appear here.
            </Empty>
          ) : (
            orders.map((o) => (
              <button className="cx-order" key={o.order_id} onClick={() => onOpen(o.order_id)}>
                <span className="col">
                  <span className="what">
                    {o.lines.length} item{o.lines.length === 1 ? '' : 's'} ·{' '}
                    {STATUS_LABEL[o.status] ?? o.status}
                  </span>
                  <span className="when">{ago(o.at)}</span>
                  <span className="oid">{o.order_id}</span>
                </span>
                <span className="right">
                  <span className="amt tnum">{rupees(o.total_paise)}</span>
                  {/* GREEN ONLY WHEN THE GATEWAY'S SIGNED CALLBACK SAID SO.
                      `paid` is that fact and nothing else — this screen never
                      derives it, and amber is the counter abstaining rather
                      than a refusal of anything the customer has done. */}
                  <Pill tone={o.paid ? 'ok' : 'amb'} dot={o.paid}>
                    {o.paid ? 'PAID' : 'NOT PAID'}
                  </Pill>
                </span>
              </button>
            ))
          )}
        </Card>
      )}

      {/* ---- who this phone is, and the rule ----------------------------- */}
      <Card title={who ? 'This phone' : 'Tell the shop who you are'}>
        {who && (
          <Verdict tone="info" title={`The shop knows you as ${who.name}`}>
            {who.phone}
            {verified
              ? ' — and you have shown that this number is yours, so your orders are above.'
              : ' — remembered on this phone so you do not type it again at the basket.'}
          </Verdict>
        )}

        {!verified && (
          <p className="cx-why">
            <b>To read back what a number has ordered, the shop needs the id of one of
            those orders.</b>{' '}
            A phone number is not a secret — anybody can type anybody&rsquo;s — so a shop that
            showed an order history for a number alone would hand a stranger&rsquo;s
            deliveries to whoever typed it. Put in the number from an order you placed:
            it is the line under <b>Your order</b> on the order screen, and it looks like{' '}
            {/* THE REAL SHAPE, copied from the counter that mints it —
                `"ord_" + secrets.token_hex(6)` in gawaah/storefront.py, whose
                own refusal calls it "'ord_' followed by twelve hex
                characters". This example used to read `a1b2c3d4e5f6`, with no
                `ord_` on the front: a customer comparing it against the
                `ord_430ec3408960` on their own order screen would reasonably
                conclude the prefix was not part of it and type the wrong
                thing. */}
            <code>ord_a1b2c3d4e5f6</code>. Without it your name and number are still
            remembered for the delivery form, and nothing can be read back.
          </p>
        )}

        <div className="cx-form">
          <Field
            label="Your name"
            htmlFor="cx-name"
            required
            error={bad === 'name' ? refusal?.detail : undefined}
          >
            <input
              id="cx-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoComplete="name"
              placeholder="Rekha"
              enterKeyHint="next"
            />
          </Field>
          <Field
            label="Phone"
            htmlFor="cx-phone"
            required
            sub="The same number you gave the shop with an order."
            error={bad === 'phone' ? refusal?.detail : undefined}
          >
            <input
              id="cx-phone"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              autoComplete="tel"
              inputMode="tel"
              type="tel"
              placeholder="98765 43210"
              enterKeyHint="next"
            />
          </Field>
          <Field
            label="An order id from this shop"
            htmlFor="cx-order"
            sub={verified
              ? 'You have already shown this number is yours. This is only needed again after signing out.'
              : 'Leave it empty if you have not ordered here yet — everything but the list above still works.'}
            error={bad === 'order' ? refusal?.detail : undefined}
          >
            <input
              id="cx-order"
              className="mono"
              value={proof}
              onChange={(e) => setProof(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              placeholder="ord_a1b2c3d4e5f6"
              enterKeyHint="go"
            />
          </Field>

          {/* Everything the map above does not place — a refusal this screen
              has never seen arrives in full rather than being swallowed. */}
          {refusal && !bad && (
            <Refusal
              reason={refusal.reason}
              detail={refusal.detail}
              hint="Nothing was changed. Fix the line above and send it again."
            />
          )}

          <div className="btn-row">
            <button className="btn primary" onClick={() => void signIn()} disabled={busy}>
              {busy ? 'ASKING THE SHOP…' : proof.trim() ? 'SHOW MY ORDERS' : 'REMEMBER ME'}
            </button>
            {who && (
              <button className="btn" onClick={() => void signOut()} disabled={busy}>
                SIGN OUT
              </button>
            )}
          </div>
        </div>

        <p className="hint">
          There is no password here, and there is not going to be one. This is a shop front,
          not an account at the counter — signing in as a customer can never become a
          shopkeeper&rsquo;s session, whatever you type.
        </p>
      </Card>

      {/* The one sentence that explains what an order does after it is placed,
          for somebody who has not placed one yet. */}
      {!verified && (
        <p className="cx-intro">
          {STATUS_LINE.new}
        </p>
      )}
    </div>
  );
}
