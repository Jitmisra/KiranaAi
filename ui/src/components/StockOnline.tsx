import { useEffect, useState } from 'react';
import * as shopapi from '../lib/shopapi';
import * as stockapi from '../lib/stockapi';
import { Refusal } from './ui';
import { useT } from '../lib/i18n';
import '../styles/stockonline.css';

/**
 * ONE PRODUCT'S SHELF, AS THE STOREFRONT SELLS AGAINST IT — the shopkeeper's
 * control for it, shared by the Products cards and the Your-products editor
 * so there is one of these and not two.
 *
 * Two writes, two owners, two buttons, exactly as ShopItems argues for its own
 * saves: the count goes to `POST /stock/{sku}/count` (gawaah/stock.py, which
 * resets the baseline and records the discrepancy) and the floor goes to
 * `POST /stock/{sku}/floor` (the same chain, a different line). Neither is a
 * raw write to a file, and neither is decided here — this component sends a
 * whole number of packets and prints what the server said.
 *
 * WHAT IT SHOWS IS THE SERVER'S ARITHMETIC. `figure` is one row of
 * `/orders/stock`, where the storefront already worked out
 *
 *     available online = on hand − in open orders − delivered since the count − floor
 *
 * and this component prints the terms; it never recomputes the sum, so the
 * number a shopkeeper reads here is the number a customer's phone was given.
 *
 * COLOUR. A shelf that is empty online is a fact, not a verdict: it is drawn
 * in ink, never red (refused) or amber (abstained). Saves are blue, like every
 * other "the machine did this" mark on the shopkeeper's screens.
 */
export function StockOnline({ skuId, figure, figuresError, countField = true, onChanged }: {
  skuId: string;
  /** The `/orders/stock` row for this product; null when there is none yet. */
  figure: shopapi.OnlineStockRow | null;
  /** The server's reason the figures could not be read, if they could not. */
  figuresError?: string | null;
  /** The count box. Off where the screen already has one (ShopItems). */
  countField?: boolean;
  onChanged: () => void;
}) {
  const { t } = useT();
  const onHand = figure?.on_hand_units ?? null;
  const available = figure?.available_units ?? null;
  const out = figure?.out_of_stock === true;
  const open = figure?.reserved_open_units ?? 0;
  const delivered = figure?.reserved_delivered_units ?? 0;
  const floor = figure?.online_floor ?? 0;

  const [count, setCount] = useState(onHand === null ? '' : String(onHand));
  const [floorText, setFloorText] = useState(String(floor));
  // The boxes FOLLOW the figure rather than being seeded from it once: the
  // Your-products editor mounts this before `/orders/stock` has answered, and
  // a floor box seeded with "0" over a floor of 2 offered a SET that would
  // have written a change nobody typed. After a save the figure comes back
  // equal to what was typed, so the text does not move under the cursor.
  useEffect(() => { setCount(onHand === null ? '' : String(onHand)); }, [onHand]);
  useEffect(() => { setFloorText(String(floor)); }, [floor]);
  const [busy, setBusy] = useState<null | 'count' | 'floor'>(null);
  const [did, setDid] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<{ reason: string; detail?: string } | null>(null);

  /**
   * A whole number of packets, checked HERE so a typo answers before a round
   * trip — and then checked again by the server, which refuses 2.5, 2.0, a
   * word and a bool by four different names. `stockapi.packets` is the same
   * check the Stock and Your-products screens use, so "2.5" is refused in the
   * same words everywhere.
   */
  const recount = async () => {
    setDid(null); setRefusal(null);
    const parsed = stockapi.packets(count, t('products.stock.count'));
    if (!('units' in parsed)) { setRefusal(parsed); return; }
    setBusy('count');
    try {
      const r = await stockapi.count(skuId, parsed.units);
      if (!r.ok) { setRefusal({ reason: r.reason, detail: r.detail }); return; }
      setDid(r.detail);
      onChanged();
    } finally { setBusy(null); }
  };

  const setFloor = async () => {
    setDid(null); setRefusal(null);
    const parsed = stockapi.packets(floorText, t('products.stock.floor'));
    if (!('units' in parsed)) { setRefusal(parsed); return; }
    setBusy('floor');
    try {
      const r = await shopapi.setOnlineFloor(skuId, parsed.units);
      if (!r.ok) { setRefusal({ reason: r.reason, detail: r.detail }); return; }
      setDid(r.detail);
      onChanged();
    } finally { setBusy(null); }
  };

  return (
    <div className="so" data-sku={skuId}>
      <div className="so-figs">
        <span className="so-fig">
          <span className="so-k">{t('products.stock.onhand')}</span>
          {/* "not counted" is a claim about the shelf, and it is only true
              when the stock module answered and had no count. When the
              figures could not be read at all — a stock module that refused,
              or `/orders/stock` itself refused — the row is simply absent, and
              the honest word is the same one the online cell uses: no figure.
              Before this branch a broken sidecar showed "not counted" over a
              shelf that had been counted that morning. */}
          {figuresError
            ? <em className="so-none">{t('products.stock.noFigure')}</em>
            : onHand === null
              ? <em className="so-none">{t('products.stock.notCounted')}</em>
              : <b className="tnum">{onHand}</b>}
        </span>
        <span className="so-fig">
          <span className="so-k">{t('products.stock.online')}</span>
          {figuresError
            ? <em className="so-none">{t('products.stock.noFigure')}</em>
            : available === null
              ? <em className="so-none">{t('products.stock.noFigure')}</em>
              : out
                ? <b className="so-out">{t('products.stock.out')}</b>
                : <b className="tnum">{t('products.stock.available', { n: available })}</b>}
        </span>
        {/* Only the piles that hold something. "0 delivered since the count"
            on every card is a sentence about nothing. */}
        {open > 0 && delivered > 0 ? (
          <span className="so-held tnum">{t('products.stock.held', { open, delivered })}</span>
        ) : open > 0 ? (
          <span className="so-held tnum">{t('products.stock.heldOpen', { open })}</span>
        ) : delivered > 0 ? (
          <span className="so-held tnum">{t('products.stock.heldDelivered', { delivered })}</span>
        ) : null}
        {floor > 0 && (
          <span className="so-held tnum">{t('products.stock.floorIs', { n: floor })}</span>
        )}
      </div>

      <div className="so-edit">
        {countField && (
          <label className="so-ctl">
            <span className="so-k">{t('products.stock.count')}</span>
            <input
              className="tnum"
              value={count}
              inputMode="numeric"
              placeholder="0"
              aria-label={`${t('products.stock.count')} — ${skuId}`}
              onChange={(e) => setCount(e.target.value)}
              disabled={busy !== null}
            />
            <button
              className="btn sm"
              onClick={() => void recount()}
              disabled={busy !== null || !count.trim()}
              title={!count.trim() ? t('products.stock.count.empty') : undefined}
            >
              {busy === 'count' ? t('products.stock.saving') : t('products.stock.count.go')}
            </button>
          </label>
        )}
        <label className="so-ctl">
          <span className="so-k">{t('products.stock.floor')}</span>
          <input
            className="tnum"
            value={floorText}
            inputMode="numeric"
            placeholder="0"
            aria-label={`${t('products.stock.floor')} — ${skuId}`}
            onChange={(e) => setFloorText(e.target.value)}
            disabled={busy !== null}
          />
          <button
            className="btn sm"
            onClick={() => void setFloor()}
            disabled={busy !== null || floorText.trim() === String(floor)}
            title={floorText.trim() === String(floor) ? t('products.stock.floor.same') : undefined}
          >
            {busy === 'floor' ? t('products.stock.saving') : t('products.stock.floor.go')}
          </button>
        </label>
      </div>
      <p className="so-fine">{t('products.stock.floor.sub')}</p>

      {figuresError && (
        <p className="so-fine so-warn">{t('products.stock.noFigures', { why: figuresError })}</p>
      )}
      {refusal && <Refusal reason={refusal.reason} detail={refusal.detail} />}
      {did && <p className="so-did">{did}</p>}

      {/* The rule, on the screen where the count is typed: an order reserves,
          packing is what sells, and a delivery stays subtracted until the
          shelf is counted again. Said here because the number above only
          makes sense with it. */}
      <p className="so-fine">{t('products.stock.reserve')}</p>
    </div>
  );
}
