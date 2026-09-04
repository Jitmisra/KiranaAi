import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import * as api from '../lib/api';
import * as admin from '../lib/adminapi';
import * as catapi from '../lib/catapi';
import * as shopapi from '../lib/shopapi';
import * as stockapi from '../lib/stockapi';
import { StockOnline } from '../components/StockOnline';
import { rupees } from '../lib/money';
import { useT } from '../lib/i18n';
import {
  Button, Card, Empty, Field, Input, InputGroup, LoadingCard, Modal, Pill,
  Refusal, Segmented, Select, Stat, StatGrid, Verdict,
} from '../components/ui';
import '../styles/shopitems.css';

/**
 * YOUR PRODUCTS — the shopkeeper's own side of the catalogue.
 *
 * The Shop section offered the storefront, the orders, the customers and the
 * loyalty book, and a shopkeeper standing at their own counter could not do the
 * five most ordinary things in a shop from any of them: put a new product on
 * the shelf, fix a name, change a price, replace a photograph, or say how many
 * are in stock. This screen is those five things.
 *
 * IT IS NOT A SECOND TEACHING SCREEN. `#/products` photographs a packet,
 * measures it on the TAKHTI mat and stores what it LOOKS like, which is the
 * only path that lets the counter name it across the counter. That is the
 * richer path and this screen says so, twice: on the add form and on every
 * product it lists that has never been photographed. What it adds is the path
 * for eleven o'clock at night — a sack of rice goes on the shelf, and finding
 * the mat and the light first is not a reasonable price for being able to sell
 * it.
 *
 * FOUR OWNERS, FOUR SAVES, AND THAT IS DELIBERATE.
 *
 *   name + price + code  ->  PATCH /shop/{sku}          (gawaah/shopadmin.py)
 *   the photograph       ->  PUT   /shop/{sku}/photo    (gawaah/shopadmin.py)
 *   where it sits        ->  PUT   /categories/sku/{sku}(gawaah/categories.py)
 *   how many on the shelf->  POST  /stock/{sku}/count   (gawaah/stock.py)
 *
 * One SAVE button fanning out to four services would half-succeed, and a half
 * -landed edit a shopkeeper cannot see the edge of is worse than one that did
 * not land at all — `catapi.assign` makes the same argument about batches. So
 * each group saves itself and prints its own result, and every refusal is shown
 * verbatim under the control it is about.
 *
 * THE BROWSER IS NEVER AN AUTHOR OF MONEY. A price leaves this page as the
 * rupee STRING the shopkeeper typed; `gawaah/money.py` parses it in string
 * space and stores whole paise. Nothing here multiplies by a hundred, and the
 * only place paise become a rupee string for an input box is
 * `admin.rupeesForInput`, which divides a number already a multiple of 100.
 *
 * COLOUR. Green, amber and red belong to money and to what the counter can
 * see. A saved name is none of those, so every state on this page is BLUE —
 * the machine's own mark — and the one amber is the server's own refusal
 * panel. "No photo" and "never counted" are drawn QUIET: they are facts about
 * work still to do, not warnings.
 */

/* ------------------------------------------------------------- the model -- */

/** One product as this screen needs it: the catalogue row plus what other
    modules know about it. Assembled here and nowhere else. */
interface Item {
  sku: api.Sku;
  /** null when the stock service has no row for it, not zero — a zero is a
      claim about a shelf and only a count can make one. */
  onHand: number | null;
  counted: boolean;
  categoryId: string | null;
  categoryName: string | null;
}

type Filter = 'all' | 'nophoto' | 'unseen';

/**
 * How this product got into the catalogue, in a shopkeeper's words.
 *
 * `product_code_only` is the server's bucket for "priced, no descriptor", and
 * TWO different products wear it: one taught from a printed code, and one typed
 * in at the counter with no code at all. They are not the same thing to a
 * shopkeeper — one can be scanned and the other can only be added by hand — so
 * the codes decide which is shown. The bucket name is not reinterpreted
 * anywhere else; this is a label, not a claim about storage.
 */
const HOW_KEY = {
  mat: 'shopitems.how.mat',
  look: 'shopitems.how.look',
  code: 'shopitems.how.code',
  typed: 'shopitems.how.typed',
} as const;

function taughtKey(s: api.Sku): keyof typeof HOW_KEY {
  if (s.taught_with === 'mat_measured') return 'mat';
  if (s.taught_with === 'product_code_only') return s.codes.length > 0 ? 'code' : 'typed';
  return 'look';
}

/* --------------------------------------------------------------- reading -- */

/**
 * A file the shopkeeper chose, as the base64 the server accepts.
 *
 * `readAsDataURL` produces `data:image/jpeg;base64,…` and the server takes it
 * whole, prefix and all — making the page strip its own prefix is one more
 * thing for the page to get wrong. The same string is the `<img src>` for the
 * preview, which the Content-Security-Policy permits (`img-src 'self' data:`)
 * and which is why this is not a `blob:` URL: that scheme is not in the policy
 * and the preview would silently fail to paint.
 */
function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onerror = () => reject(new Error('this file could not be read'));
    fr.onload = () => resolve(String(fr.result ?? ''));
    fr.readAsDataURL(file);
  });
}

/** Refused before it is read, so a 40 MB photograph does not become 53 MB of
    base64 in a request body. The server caps it too, and says the same number. */
const MAX_PHOTO_BYTES = 8 * 1024 * 1024;

/* ------------------------------------------------------------- fragments -- */

/** The product's picture, or the reserved space that says there is not one. */
function Thumb({ item, size = 44 }: { item: Item; size?: number }) {
  const { t } = useT();
  if (item.sku.thumb_png) {
    return (
      <img
        className="si-thumb"
        style={{ width: size, height: size }}
        src={`data:image/png;base64,${item.sku.thumb_png}`}
        alt={item.sku.name}
      />
    );
  }
  return (
    <span className="si-thumb none" style={{ width: size, height: size }}
          title={t('shopitems.nophoto')}>
      <svg viewBox="0 0 24 24" width={Math.round(size * 0.42)} height={Math.round(size * 0.42)}
           aria-hidden="true" focusable="false">
        <rect x="3" y="5" width="18" height="14" rx="2.5" fill="none"
              stroke="currentColor" strokeWidth="1.6" />
        <circle cx="9" cy="10" r="1.6" fill="currentColor" />
        <path d="M4.5 17.5 9.5 12l3.5 3.5 2.5-2.5 4 4.5" fill="none"
              stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      </svg>
    </span>
  );
}

/** The server's own refusal, verbatim, under the control it is about. */
function Why({ r }: { r: admin.Refusal }) {
  return (
    <span className="si-why">
      <span className="mono">{r.reason}</span>
      {r.detail ? <> — {r.detail}</> : null}
    </span>
  );
}

/** What a save did, in the machine's own words. Blue, never green. */
function Did({ children }: { children: ReactNode }) {
  return <p className="si-did">{children}</p>;
}

/* ============================================================== the screen */

export default function ShopItems() {
  const { t } = useT();

  const [items, setItems] = useState<Item[] | null>(null);
  const [loadErr, setLoadErr] = useState<admin.Refusal | null>(null);
  const [cats, setCats] = useState<catapi.CategoryRow[]>([]);
  const [q, setQ] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  /**
   * THE CATALOGUE IS THE SPINE; STOCK AND CATEGORIES ARE ENRICHMENT.
   *
   * Only `/shop` failing empties this screen. A stock service that is down
   * leaves every count reading "not counted" — which is what the Stock screen
   * itself prints for a product nobody has counted, and is true here in the
   * sense that matters: this page cannot say what is on the shelf. Letting
   * either of the other two blank the product list would hide the catalogue
   * because a subordinate module was unwell.
   */
  const load = useCallback(async () => {
    setLoadErr(null);
    const shop = await api.shop();
    if (!shop.ok) {
      setLoadErr(shop as admin.Refusal);
      setItems(null);
      return;
    }
    const [stock, prods, book] = await Promise.all([
      stockapi.list(), catapi.products({}), catapi.list(),
    ]);
    const onHand = new Map<string, { units: number | null; counted: boolean }>();
    if (stock.ok) {
      for (const r of stock.items) {
        onHand.set(r.sku_id, { units: r.on_hand_units, counted: r.basis === 'counted' });
      }
    }
    const cat = new Map<string, { id: string | null; name: string | null }>();
    if (prods.ok) {
      for (const p of prods.products) cat.set(p.sku_id, { id: p.category_id, name: p.category_name });
    }
    setCats(book.ok ? book.categories : []);
    setItems(shop.skus.map((s) => {
      const h = onHand.get(s.sku_id);
      const c = cat.get(s.sku_id);
      return {
        sku: s,
        onHand: h ? h.units : null,
        counted: h ? h.counted : false,
        categoryId: c ? c.id : null,
        categoryName: c ? c.name : null,
      };
    }));
  }, []);

  useEffect(() => { void load(); }, [load]);

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (items ?? []).filter((it) => {
      if (filter === 'nophoto' && it.sku.thumb_png) return false;
      if (filter === 'unseen' && it.sku.vector_dim > 0) return false;
      if (!needle) return true;
      return (
        it.sku.name.toLowerCase().includes(needle)
        || it.sku.sku_id.toLowerCase().includes(needle)
        || it.sku.codes.some((c) => c.includes(needle))
      );
    });
  }, [items, q, filter]);

  const counts = useMemo(() => {
    const all = items ?? [];
    return {
      total: all.length,
      noPhoto: all.filter((i) => !i.sku.thumb_png).length,
      unseen: all.filter((i) => i.sku.vector_dim === 0).length,
      counted: all.filter((i) => i.counted).length,
    };
  }, [items]);

  const current = useMemo(
    () => (open ? (items ?? []).find((i) => i.sku.sku_id === open) ?? null : null),
    [open, items],
  );

  return (
    <div className="stack">
      <div className="page-head">
        <h1>{t('shopitems.title')}</h1>
        <p>{t('shopitems.blurb')}</p>
      </div>

      <StatGrid>
        <Stat label={t('shopitems.stat.products')} value={items === null ? '—' : counts.total} />
        <Stat label={t('shopitems.stat.nophoto')} value={items === null ? '—' : counts.noPhoto}
              sub={t('shopitems.stat.nophoto.sub')} />
        <Stat label={t('shopitems.stat.unseen')} value={items === null ? '—' : counts.unseen}
              sub={t('shopitems.stat.unseen.sub')} />
        <Stat label={t('shopitems.stat.counted')} value={items === null ? '—' : counts.counted}
              sub={t('shopitems.stat.counted.sub')} />
      </StatGrid>

      <AddCard
        open={adding}
        onOpen={() => setAdding(true)}
        onCancel={() => setAdding(false)}
        cats={cats}
        onAdded={() => { void load(); }}
      />

      <Card
        title={t('shopitems.list.title')}
        sub={t('shopitems.list.sub')}
        aside={<Pill tone="code">{shown.length}</Pill>}
        flush
      >
        <div className="si-tools">
          <Input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t('shopitems.search')}
            aria-label={t('shopitems.search')}
          />
          <Segmented<Filter>
            value={filter}
            onChange={setFilter}
            options={[
              { value: 'all', label: t('shopitems.filter.all') },
              { value: 'nophoto', label: t('shopitems.filter.nophoto') },
              { value: 'unseen', label: t('shopitems.filter.unseen') },
            ]}
          />
        </div>

        {items === null && loadErr === null && <LoadingCard lines={4} />}

        {loadErr && (
          <div className="si-pad">
            <Refusal
              reason={t('shopitems.load.failed')}
              detail={loadErr.reason}
              hint={loadErr.detail}
              action={<Button size="sm" onClick={() => void load()}>{t('shopitems.retry')}</Button>}
            />
          </div>
        )}

        {items !== null && shown.length === 0 && (
          <div className="si-pad">
            <Empty title={counts.total === 0 ? t('shopitems.empty.title') : t('shopitems.nomatch.title')}>
              {counts.total === 0 ? t('shopitems.empty.body') : t('shopitems.nomatch.body')}
            </Empty>
          </div>
        )}

        {items !== null && shown.length > 0 && (
          <ul className="si-list">
            {shown.map((it) => (
              <li key={it.sku.sku_id}>
                <button className="si-row" onClick={() => setOpen(it.sku.sku_id)}>
                  <Thumb item={it} />
                  <span className="si-name">
                    <b>{it.sku.name}</b>
                    <span className="si-sub">
                      <span className="mono">{it.sku.sku_id}</span>
                      {it.categoryName && <> · {it.categoryName}</>}
                    </span>
                  </span>
                  {/* THE THREE FACTS TRAVEL AS ONE GROUP, and the wrapper is
                      `display: contents` on a laptop so they stay three
                      separate grid columns with the prices aligned down the
                      page. On a phone the wrapper becomes a real flex box on
                      its own line. Without it all three were assigned the same
                      grid area and drew ON TOP OF EACH OTHER — the price, the
                      count and the pill overlapping in one illegible smear.
                      It looked right in the CSS and only a 390 px screenshot
                      showed it. */}
                  <span className="si-meta">
                    <span className="si-price tnum">
                      {it.sku.price_paise === null ? '—' : rupees(it.sku.price_paise)}
                    </span>
                    <span className="si-stock tnum">
                      {it.onHand === null || !it.counted
                        ? <em>{t('shopitems.notcounted')}</em>
                        : t('shopitems.onhand', { n: it.onHand })}
                    </span>
                    <span className="si-how">
                      <Pill tone="code">{t(HOW_KEY[taughtKey(it.sku)])}</Pill>
                    </span>
                  </span>
                  <span className="si-go" aria-hidden="true">
                    <svg viewBox="0 0 16 16" width="14" height="14">
                      <path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor"
                            strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {current && (
        <EditModal
          key={current.sku.sku_id}
          item={current}
          cats={cats}
          onClose={() => setOpen(null)}
          onChanged={() => { void load(); }}
        />
      )}
    </div>
  );
}

/* ============================================================ adding one == */

function AddCard({ open, onOpen, onCancel, cats, onAdded }: {
  open: boolean;
  onOpen: () => void;
  onCancel: () => void;
  cats: catapi.CategoryRow[];
  onAdded: () => void;
}) {
  const { t } = useT();
  const [name, setName] = useState('');
  const [price, setPrice] = useState('');
  const [code, setCode] = useState('');
  const [skuId, setSkuId] = useState('');
  const [category, setCategory] = useState('');
  const [stock, setStock] = useState('');
  const [photo, setPhoto] = useState<string | null>(null);
  const [photoErr, setPhotoErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<admin.Refusal | null>(null);
  const [done, setDone] = useState<admin.Ok<admin.SkuAdded> | null>(null);
  const [extra, setExtra] = useState<string[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => { if (open) nameRef.current?.focus(); }, [open]);

  /**
   * EMPTY THE BOXES, AND NOTHING ELSE.
   *
   * It used to clear `done` and `extra` too, and it is called immediately after
   * they are set — so the panel saying what was added, what it cannot do, and
   * whether the count was recorded was wiped in the same tick it was written.
   * Found by adding a product in the browser and watching nothing appear.
   */
  const clearForm = () => {
    setName(''); setPrice(''); setCode(''); setSkuId(''); setCategory('');
    setStock(''); setPhoto(null); setPhotoErr(null); setRefusal(null);
    if (fileRef.current) fileRef.current.value = '';
  };

  const pick = async (f: File | null | undefined) => {
    setPhotoErr(null);
    if (!f) { setPhoto(null); return; }
    if (f.size > MAX_PHOTO_BYTES) {
      setPhoto(null);
      setPhotoErr(t('shopitems.photo.toobig', { n: Math.round(f.size / 1024 / 1024) }));
      return;
    }
    try {
      setPhoto(await readAsDataUrl(f));
    } catch {
      setPhoto(null);
      setPhotoErr(t('shopitems.photo.unreadable'));
    }
  };

  /**
   * THE PRODUCT IS ADDED FIRST AND ALONE.
   *
   * Name, price, code and photograph are ONE request the server applies
   * atomically — a code already naming something else refuses the whole thing
   * and leaves no half-made product behind. The category and the opening count
   * belong to two other services and are filed afterwards, each reported
   * separately, because a category service that is down must not undo a
   * product that is already on the shelf and already priced.
   */
  const submit = async () => {
    setBusy(true);
    setRefusal(null);
    setDone(null);
    setExtra([]);
    const body: admin.NewSku = { name, price_rupees: price };
    if (skuId.trim()) body.sku_id = skuId.trim();
    if (code.trim()) body.code = code.trim();
    if (photo) body.photo_b64 = photo;
    const res = await admin.addSku(body);
    if (!res.ok) {
      setRefusal(res);
      setBusy(false);
      return;
    }
    const notes: string[] = [];
    if (category) {
      const filed = await catapi.fileSku(res.sku_id, { category_id: category });
      notes.push(filed.ok
        ? t('shopitems.add.filed', { name: cats.find((c) => c.category_id === category)?.name ?? category })
        : t('shopitems.add.notfiled', { why: filed.reason }));
    }
    const typed = stock.trim();
    if (typed) {
      const parsed = stockapi.packets(typed, t('shopitems.f.stock'));
      if ('units' in parsed) {
        const counted = await stockapi.count(res.sku_id, parsed.units);
        notes.push(counted.ok
          ? t('shopitems.add.counted', { n: parsed.units })
          : t('shopitems.add.notcounted', { why: counted.reason }));
      } else {
        notes.push(t('shopitems.add.notcounted', { why: parsed.reason }));
      }
    }
    setDone(res);
    setExtra(notes);
    setBusy(false);
    clearForm();
    // THE CARD CLOSES ON SUCCESS, because that is where the result is drawn.
    // Left open, the form emptied itself and the shopkeeper saw nothing at all
    // — not the id that was made for them, not the warning that the camera
    // cannot recognise this product, not whether the count was recorded. The
    // list below is the confirmation that something happened; this panel is
    // the confirmation of WHAT.
    onCancel();
    onAdded();
  };

  if (!open) {
    return (
      <Card
        title={t('shopitems.add.title')}
        sub={t('shopitems.add.sub')}
        aside={<Button variant="primary" onClick={onOpen}>{t('shopitems.add.open')}</Button>}
      >
        {done ? <Added res={done} extra={extra} /> : <p className="si-lead">{t('shopitems.add.lead')}</p>}
      </Card>
    );
  }

  return (
    <Card title={t('shopitems.add.title')} sub={t('shopitems.add.sub')}>
      <div className="si-form">
        <Field label={t('shopitems.f.name')} sub={t('shopitems.f.name.sub')} required htmlFor="si-add-name">
          <Input id="si-add-name" ref={nameRef} value={name} autoComplete="off"
                 placeholder={t('shopitems.f.name.eg')}
                 onChange={(e) => setName(e.target.value)} />
        </Field>

        <Field label={t('shopitems.f.price')} sub={t('shopitems.f.price.sub')} required htmlFor="si-add-price">
          <InputGroup prefix="₹">
            <Input id="si-add-price" className="tnum" value={price} inputMode="decimal"
                   autoComplete="off" placeholder="0.00"
                   onChange={(e) => setPrice(e.target.value)} />
          </InputGroup>
        </Field>

        <Field label={t('shopitems.f.category')} sub={t('shopitems.f.category.sub')} htmlFor="si-add-cat">
          <Select id="si-add-cat" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">{t('shopitems.f.category.none')}</option>
            {cats.map((c) => (
              <option key={c.category_id} value={c.category_id}>{c.name}</option>
            ))}
          </Select>
        </Field>

        <Field label={t('shopitems.f.stock')} sub={t('shopitems.f.stock.sub')} htmlFor="si-add-stock">
          <Input id="si-add-stock" className="tnum" value={stock} inputMode="numeric"
                 autoComplete="off" placeholder="0"
                 onChange={(e) => setStock(e.target.value)} />
        </Field>

        <Field label={t('shopitems.f.code')} sub={t('shopitems.f.code.sub')} htmlFor="si-add-code">
          <Input id="si-add-code" className="mono" value={code} autoComplete="off"
                 inputMode="numeric" placeholder="8901030510005"
                 onChange={(e) => setCode(e.target.value)} />
        </Field>

        <Field label={t('shopitems.f.id')} sub={t('shopitems.f.id.sub')} htmlFor="si-add-id">
          <Input id="si-add-id" className="mono" value={skuId} autoComplete="off"
                 placeholder={t('shopitems.f.id.auto')}
                 onChange={(e) => setSkuId(e.target.value)} />
        </Field>

        <Field label={t('shopitems.f.photo')} sub={t('shopitems.f.photo.sub')}
               error={photoErr ?? undefined} htmlFor="si-add-photo">
          <div className="si-photo-pick">
            {photo
              ? <img className="si-thumb lg" src={photo} alt={t('shopitems.f.photo.alt')} />
              : <span className="si-thumb none lg" aria-hidden="true" />}
            <input id="si-add-photo" ref={fileRef} type="file" accept="image/*"
                   onChange={(e) => void pick(e.target.files?.[0])} />
          </div>
        </Field>
      </div>

      {refusal && (
        <div className="si-pad-t">
          <Refusal reason={refusal.reason} detail={refusal.detail} />
        </div>
      )}

      <div className="btn-row si-pad-t">
        <Button variant="primary" loading={busy} onClick={() => void submit()}
                disabled={!name.trim() || !price.trim()}>
          {t('shopitems.add.go')}
        </Button>
        <Button onClick={() => { clearForm(); onCancel(); }}>{t('shopitems.cancel')}</Button>
      </div>

      {/* SAID ON THE FORM, NOT AFTERWARDS. A shopkeeper who does not know they
          are about to add the weak kind cannot choose the other one. */}
      <p className="si-fine">{t('shopitems.add.fine')}</p>
    </Card>
  );
}

/** What was added, in the server's own words — including what it cannot do. */
function Added({ res, extra }: { res: admin.Ok<admin.SkuAdded>; extra: string[] }) {
  const { t } = useT();
  return (
    <Verdict tone="info" title={t('shopitems.add.done', { name: res.name })}>
      <span className="si-block">
        <span className="mono">{res.sku_id}</span> · {rupees(res.price_paise)}
        {res.sku_id_derived && <> · {t('shopitems.add.derived')}</>}
      </span>
      <span className="si-block">{res.warning}</span>
      <span className="si-block">{res.better}</span>
      {res.price_map_warning && <span className="si-block">{res.price_map_warning}</span>}
      {extra.map((e) => <span className="si-block" key={e}>{e}</span>)}
    </Verdict>
  );
}

/* ============================================================ editing one = */

function EditModal({ item, cats, onClose, onChanged }: {
  item: Item;
  cats: catapi.CategoryRow[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t } = useT();
  const sku = item.sku;

  const [name, setName] = useState(sku.name);
  const [price, setPrice] = useState(admin.rupeesForInput(sku.price_paise));
  const [code, setCode] = useState(sku.codes[0] ?? '');
  const [saving, setSaving] = useState(false);
  const [refusal, setRefusal] = useState<admin.Refusal | null>(null);
  const [saved, setSaved] = useState<admin.Ok<admin.SkuEdited> | null>(null);

  const [photoBusy, setPhotoBusy] = useState(false);
  const [photoErr, setPhotoErr] = useState<string | null>(null);
  const [photoRefusal, setPhotoRefusal] = useState<admin.Refusal | null>(null);
  const [photoNote, setPhotoNote] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const [category, setCategory] = useState(item.categoryId ?? '');
  const [catBusy, setCatBusy] = useState(false);
  const [catNote, setCatNote] = useState<string | null>(null);

  const [count, setCount] = useState(
    item.counted && item.onHand !== null ? String(item.onHand) : '',
  );
  const [countBusy, setCountBusy] = useState(false);
  const [countNote, setCountNote] = useState<string | null>(null);
  const [countErr, setCountErr] = useState<string | null>(null);

  /**
   * WHAT THE STOREFRONT WILL SELL OF THIS, read from `/orders/stock` when the
   * editor opens and again after every count or floor written from it. One
   * product's row, so the floor control below shows the figure a customer's
   * phone is being given right now rather than the count alone.
   */
  const [figure, setFigure] = useState<shopapi.OnlineStockRow | null>(null);
  const [figErr, setFigErr] = useState<string | null>(null);
  const [figTick, setFigTick] = useState(0);
  useEffect(() => {
    let alive = true;
    void (async () => {
      const r = await shopapi.onlineStock();
      if (!alive) return;
      if (!r.ok) { setFigErr(`${r.reason}${r.detail ? ` — ${r.detail}` : ''}`); return; }
      setFigErr(r.figures ? null : (r.error ?? 'the stock figures could not be read'));
      setFigure(r.items.find((row) => row.sku_id === sku.sku_id) ?? null);
    })();
    return () => { alive = false; };
  }, [sku.sku_id, figTick]);

  const [history, setHistory] = useState<admin.EditEntry[] | null>(null);

  /* ---- name, price, code: ONE endpoint, one button ---------------------- */

  /**
   * ONLY WHAT MOVED IS SENT. The server draws a distinction between an ABSENT
   * key ("leave this alone") and an empty one ("clear it"), so posting all
   * three every time would rebind a code nobody touched — and an empty code
   * field on a product with no code would arrive as an instruction to clear
   * bindings that do not exist.
   */
  const fields = (): admin.SkuEdit => {
    const out: admin.SkuEdit = {};
    if (name.trim() !== sku.name) out.name = name.trim();
    if (price.trim() !== admin.rupeesForInput(sku.price_paise)) out.price_rupees = price.trim();
    if (code.trim() !== (sku.codes[0] ?? '')) out.code = code.trim();
    return out;
  };
  const dirty = Object.keys(fields()).length > 0;

  const save = async () => {
    setSaving(true);
    setRefusal(null);
    setSaved(null);
    const res = await admin.editSku(sku.sku_id, fields());
    if (res.ok) { setSaved(res); onChanged(); } else setRefusal(res);
    setSaving(false);
  };

  /* ---- the photograph --------------------------------------------------- */

  const sendPhoto = async (data: string) => {
    setPhotoBusy(true);
    setPhotoErr(null);
    setPhotoRefusal(null);
    setPhotoNote(null);
    const res = await admin.setSkuPhoto(sku.sku_id, data);
    if (res.ok) {
      // The server is asked whether the CUSTOMER will see it rather than the
      // page assuming so: a product with no descriptor keeps its picture where
      // the storefront cannot read it, and that is worth knowing before going
      // to look for it on your own shutter QR.
      setPhotoNote(res.storefront_note ?? (res.has_photo
        ? t('shopitems.photo.stored')
        : t('shopitems.photo.removed')));
      onChanged();
    } else setPhotoRefusal(res);
    setPhotoBusy(false);
    if (fileRef.current) fileRef.current.value = '';
  };

  const pickPhoto = async (f: File | null | undefined) => {
    setPhotoErr(null);
    if (!f) return;
    if (f.size > MAX_PHOTO_BYTES) {
      setPhotoErr(t('shopitems.photo.toobig', { n: Math.round(f.size / 1024 / 1024) }));
      return;
    }
    try {
      await sendPhoto(await readAsDataUrl(f));
    } catch {
      setPhotoErr(t('shopitems.photo.unreadable'));
    }
  };

  /* ---- where it sits ---------------------------------------------------- */

  const file = async () => {
    setCatBusy(true);
    setCatNote(null);
    const res = await catapi.fileSku(sku.sku_id, { category_id: category || null });
    setCatNote(res.ok
      ? (category
        ? t('shopitems.cat.filed', { name: cats.find((c) => c.category_id === category)?.name ?? category })
        : t('shopitems.cat.cleared'))
      : `${res.reason}${res.detail ? ` — ${res.detail}` : ''}`);
    if (res.ok) onChanged();
    setCatBusy(false);
  };

  /* ---- how many on the shelf ------------------------------------------- */

  /**
   * A COUNT RESETS THE BASELINE — it is not a movement and not an edit.
   * `gawaah/stock.py` supersedes the movements before it and records the
   * discrepancy, which is the honest shape for "I have just counted the
   * shelf". Whole packets only: half a packet is not a thing a shelf holds.
   */
  const recount = async () => {
    setCountErr(null);
    setCountNote(null);
    const parsed = stockapi.packets(count, t('shopitems.f.count'));
    if (!('units' in parsed)) {
      setCountErr(`${parsed.reason} — ${parsed.detail}`);
      return;
    }
    setCountBusy(true);
    const res = await stockapi.count(sku.sku_id, parsed.units);
    if (res.ok) {
      setCountNote(res.detail);
      setFigTick((n) => n + 1);
      onChanged();
    } else setCountErr(`${res.reason}${res.detail ? ` — ${res.detail}` : ''}`);
    setCountBusy(false);
  };

  /* ---- what this price has been ---------------------------------------- */

  const showHistory = async () => {
    const res = await admin.skuHistory(sku.sku_id);
    setHistory(res.ok ? res.entries : []);
  };

  return (
    <Modal
      open
      onClose={onClose}
      size="wide"
      title={sku.name}
      sub={<span className="mono">{sku.sku_id}</span>}
      foot={<Button onClick={onClose}>{t('shopitems.close')}</Button>}
      note={t('shopitems.edit.permanent')}
    >
      <div className="si-edit">
        {/* --- name, price, printed code --- */}
        <section>
          <h3>{t('shopitems.g.basics')}</h3>
          <div className="si-form">
            <Field label={t('shopitems.f.name')} htmlFor={`si-n-${sku.sku_id}`}>
              <Input id={`si-n-${sku.sku_id}`} value={name} autoComplete="off"
                     onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label={t('shopitems.f.price')} sub={t('shopitems.f.price.sub')}
                   htmlFor={`si-p-${sku.sku_id}`}>
              <InputGroup prefix="₹">
                <Input id={`si-p-${sku.sku_id}`} className="tnum" value={price}
                       inputMode="decimal" autoComplete="off"
                       onChange={(e) => setPrice(e.target.value)} />
              </InputGroup>
            </Field>
            <Field label={t('shopitems.f.code')} sub={t('shopitems.f.code.edit')}
                   htmlFor={`si-c-${sku.sku_id}`}>
              <Input id={`si-c-${sku.sku_id}`} className="mono" value={code}
                     autoComplete="off" inputMode="numeric"
                     onChange={(e) => setCode(e.target.value)} />
            </Field>
          </div>
          {refusal && <div className="si-pad-t"><Refusal reason={refusal.reason} detail={refusal.detail} /></div>}
          {saved && (
            <Did>
              {saved.changed.length === 0
                ? t('shopitems.edit.nochange')
                : t('shopitems.edit.saved', { what: saved.changed.join(', ') })}
              {saved.codes.unbound.length > 0 && (
                <> {t('shopitems.edit.unbound', { codes: saved.codes.unbound.join(', ') })}</>
              )}
              {saved.audit === null && <> {saved.audit_note}</>}
            </Did>
          )}
          <div className="btn-row si-pad-t">
            <Button variant="primary" loading={saving} disabled={!dirty}
                    onClick={() => void save()}>
              {t('shopitems.edit.save')}
            </Button>
            <Button size="sm" onClick={() => void showHistory()}>{t('shopitems.history.show')}</Button>
          </div>
          {history !== null && (
            <div className="si-history">
              {history.length === 0 ? (
                <p className="si-fine">{t('shopitems.history.none')}</p>
              ) : history.map((e) => (
                <p key={e.hash} className="si-history-row">
                  <span className="si-when">{stockapi.when(e.ts)}</span>
                  <span>
                    {e.price_rupees_after !== undefined ? (
                      <>
                        {e.price_rupees_before
                          ? <><s className="tnum">₹{e.price_rupees_before}</s> → </>
                          : null}
                        <b className="tnum">₹{e.price_rupees_after}</b>
                      </>
                    ) : e.name_after !== undefined ? (
                      <>{e.name_before} → <b>{e.name_after}</b></>
                    ) : (
                      <span className="mono">{e.event}</span>
                    )}
                  </span>
                </p>
              ))}
            </div>
          )}
        </section>

        {/* --- the photograph --- */}
        <section>
          <h3>{t('shopitems.g.photo')}</h3>
          <div className="si-photo-pick">
            {sku.thumb_png
              ? <img className="si-thumb lg" src={`data:image/png;base64,${sku.thumb_png}`} alt={sku.name} />
              : <span className="si-thumb none lg" aria-hidden="true" />}
            <div>
              <input ref={fileRef} type="file" accept="image/*" disabled={photoBusy}
                     aria-label={t('shopitems.photo.choose')}
                     onChange={(e) => void pickPhoto(e.target.files?.[0])} />
              {sku.thumb_png && (
                <Button size="sm" loading={photoBusy} onClick={() => void sendPhoto('')}>
                  {t('shopitems.photo.remove')}
                </Button>
              )}
            </div>
          </div>
          {photoErr && <p className="si-why">{photoErr}</p>}
          {photoRefusal && <div className="si-pad-t"><Why r={photoRefusal} /></div>}
          {photoNote && <Did>{photoNote}</Did>}
          <p className="si-fine">{t('shopitems.photo.fine')}</p>
        </section>

        {/* --- where it sits --- */}
        <section>
          <h3>{t('shopitems.g.category')}</h3>
          {cats.length === 0 ? (
            <p className="si-fine">{t('shopitems.cat.none')}</p>
          ) : (
            <>
              <div className="si-inline">
                <Select value={category} aria-label={t('shopitems.g.category')}
                        onChange={(e) => setCategory(e.target.value)}>
                  <option value="">{t('shopitems.f.category.none')}</option>
                  {cats.map((c) => (
                    <option key={c.category_id} value={c.category_id}>{c.name}</option>
                  ))}
                </Select>
                <Button loading={catBusy} disabled={category === (item.categoryId ?? '')}
                        onClick={() => void file()}>
                  {t('shopitems.cat.file')}
                </Button>
              </div>
              {catNote && <Did>{catNote}</Did>}
            </>
          )}
        </section>

        {/* --- how many on the shelf --- */}
        <section>
          <h3>{t('shopitems.g.stock')}</h3>
          <div className="si-inline">
            <Input className="tnum si-count" value={count} inputMode="numeric"
                   aria-label={t('shopitems.f.count')} placeholder="0"
                   onChange={(e) => setCount(e.target.value)} />
            <Button loading={countBusy} onClick={() => void recount()}>
              {t('shopitems.stock.record')}
            </Button>
          </div>
          <p className="si-fine">{t('shopitems.stock.fine')}</p>
          {countErr && <p className="si-why">{countErr}</p>}
          {countNote && <Did>{countNote}</Did>}
          {/* THE ONLINE SIDE OF THE SAME SHELF. The count box above is this
              screen's own; what is added is the floor ("keep back N for the
              counter") and the figure the storefront is selling against —
              the same component the Products cards use, so there is one of
              it and not two. */}
          <div className="si-pad-t">
            <StockOnline
              skuId={sku.sku_id}
              figure={figure}
              figuresError={figErr}
              countField={false}
              onChanged={() => { setFigTick((n) => n + 1); onChanged(); }}
            />
          </div>
        </section>
      </div>
    </Modal>
  );
}
