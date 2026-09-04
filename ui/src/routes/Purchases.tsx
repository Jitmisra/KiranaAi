import { useCallback, useEffect, useMemo, useState } from 'react';
import * as pu from '../lib/purchapi';
import { rupees } from '../lib/money';
import {
  Card, Pill, Verdict, Empty, Refusal, Segmented, Field, Button, Skeleton,
} from '../components/ui';
import { PhotoView } from './PurchasesPhoto';
import '../styles/purchases.css';

/**
 * KHAREED — what the shop paid, who it paid, and what it earns.
 *
 * Every other screen on this counter knows what a packet SELLS for. Until the
 * cost price could be written down, nothing here could answer the question a
 * shopkeeper actually runs the shop on: what do I make on this?
 *
 * THE UNKNOWN MARGIN IS THE SCREEN. A product with no recorded cost shows
 * UNKNOWN — in amber, spelled out, never a dash and never a zero. That is not
 * politeness. A missing cost read as nought reports the shop making its entire
 * selling price as profit on everything it has never entered an invoice for,
 * which is both wrong and extremely flattering, and a shopkeeper who believed
 * it would price against it. The day view is split the same way: revenue the
 * counter can put a margin on, and revenue it cannot, side by side, with the
 * second one refusing to be summed into the first.
 *
 * WHAT THIS PAGE DOES NOT DO. It does not total an invoice. The line figures
 * and the invoice figure beside the SAVE button are the browser's own check
 * against the paper in the shopkeeper's hand — they are sent as an ASSERTION
 * the server compares against its own arithmetic and refuses on disagreement
 * (`client_total_disagrees`), and what is stored is always the server's number.
 * The cost per unit is the one money field this page is allowed to state,
 * because it is a fact off an invoice and exists nowhere else in this program;
 * it goes up as the STRING the shopkeeper typed, because a decimal sent as a
 * JSON number is a float before anything rounds it.
 *
 * NOTHING HERE PAYS ANYBODY. There is no payables ledger, no due date, no
 * outstanding balance and no gateway. "Bought" is the sum of what has been
 * recorded, not what is owed, and the page says so rather than letting a
 * shopkeeper read it as a bill.
 */

/* ------------------------------------------------------------------ bits -- */

type Trouble = { reason: string; detail?: string };

const asTrouble = (r: { reason: string; detail?: string }): Trouble =>
  r.detail === undefined ? { reason: r.reason } : { reason: r.reason, detail: r.detail };

/**
 * A margin can be negative and `rupees()` refuses a negative on purpose — it
 * guards a till total, where a minus is a bug. Here a minus is a real answer,
 * so the sign is carried outside the formatter and the magnitude still goes
 * through the same integer-paise assertion.
 */
function signed(p: number): string {
  return p < 0 ? `− ${rupees(-p)}` : rupees(p);
}

/** A day label a shopkeeper reads, from the YYYY-MM-DD the server speaks. */
function dayWords(label: string): string {
  const d = new Date(`${label}T00:00:00`);
  if (Number.isNaN(d.getTime())) return label;
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

/**
 * The one hint worth adding to a server refusal on this screen.
 *
 * `gawaah/purchases.py` carries no prefix and has to be included in the till.
 * Until it is, every request here is a plain 404 — which reads as "this screen
 * is broken" when it means "this screen's server is not mounted yet". Say
 * which.
 */
function mountHint(t: Trouble): string | undefined {
  return /404/.test(t.reason) || /404/.test(t.detail ?? '')
    ? 'This screen reads gawaah/purchases.py. If the till has not been restarted since that module landed, its routes are not mounted and every request here answers 404.'
    : undefined;
}

/**
 * The server's own account of a refusal, for the line under a field: its name
 * for the state, then its sentence. Neither is rewritten — the name is what a
 * maintainer greps for and the sentence is what a shopkeeper acts on.
 */
function said(t: Trouble) {
  return <><span className="mono">{t.reason}</span>{t.detail ? ` — ${t.detail}` : ''}</>;
}

/**
 * WHICH BOX THE SERVER REFUSED, by the reason's own name.
 *
 * `gawaah/purchases.py` names the state it refused on rather than describing it
 * — `invoice_already_recorded`, `date_is_in_the_future`, `units_not_a_whole_number`,
 * `cost_not_positive_integer_paise` — so a refusal can go back under the box
 * that caused it instead of only at the foot of a form with fifteen boxes in it.
 * This decides WHERE it is shown and nothing else: the reason and the detail are
 * rendered exactly as they arrived either way, and anything unrecognised stays
 * at the foot, which is the safe direction.
 */
function whichBox(t: Trouble | null): 'supplier' | 'date' | 'invoice' | 'line' | 'name' | 'phone' | null {
  if (!t) return null;
  const n = t.reason;
  if (n === 'supplier_name_missing' || n === 'supplier_already_recorded') return 'name';
  if (n === 'supplier_phone_missing' || n === 'supplier_phone_not_a_number') return 'phone';
  if (n === 'supplier_id_malformed' || n === 'no_such_supplier') return 'supplier';
  if (n.startsWith('date_')) return 'date';
  if (n.startsWith('invoice_')) return 'invoice';
  if (n === 'sku_not_in_this_shop' || n.startsWith('units_') || n.startsWith('cost_')
    || n === 'client_line_total_disagrees') return 'line';
  return null;
}

/**
 * The sku a line refusal is about, or null.
 *
 * Every per-line refusal in `purchases.py` prints the sku with `!r`, so the id
 * arrives inside single quotes in the detail. Matching the draft lines against
 * that is exact — no parsing of the sentence, and a sku that does not appear in
 * it simply returns null and the refusal stays at the foot.
 */
function skuNamed(t: Trouble | null, lines: ReadonlyArray<DraftLine>): string | null {
  const d = t?.detail;
  if (!d) return null;
  for (const l of lines) {
    if (l.sku_id && d.includes(`'${l.sku_id}'`)) return l.sku_id;
  }
  return null;
}

/* ------------------------------------------------------------- waiting --
   THE SHAPE OF WHAT IS COMING, drawn while it comes.

   These panels used to wait behind three bars of one arbitrary height, which
   said something was loading and nothing about what — and then a five-column
   table, or a stack of purchase rows, landed at a different height and moved the
   page. Each block below is the same grid and the same row count as the thing
   that replaces it, and every money block sits at the right edge its figure will
   sit at.

   Pale blue, because a skeleton is the machine working and blue is the machine's
   own colour here. Green, amber and red are spoken for.
*/

function StatSkeletons() {
  return (
    <div className="pu-stats" aria-hidden="true">
      {Array.from({ length: 4 }, (_, i) => (
        <div className="pu-stat skel-tile" key={i}>
          <span className="skel short" />
          <span className="skel big" />
          <span className="skel" />
        </div>
      ))}
    </div>
  );
}

/**
 * A table, waiting, at the columns it will have.
 *
 * The header is real and is drawn straight away: it is the one part of a table
 * that is known before the rows arrive, and drawing it means nothing moves when
 * they land. The money columns are right-aligned exactly where their figures
 * will be.
 */
function TableSkeleton({ heads, rows = 4, label }: {
  heads: ReadonlyArray<{ head: string; num?: boolean; drop?: boolean }>;
  rows?: number;
  label: string;
}) {
  return (
    <div className="scroll-x" role="status" aria-live="polite" aria-label={label}>
      {/* `tbl-cards` so the waiting shape is the shape of what lands: under
          560 the rows restack, and a skeleton that stayed a grid would jump. */}
      <table className="pu-table tbl-cards">
        <thead>
          <tr>
            {heads.map((h) => (
              <th key={h.head} className={[h.num ? 'num' : '', h.drop ? 'pu-hide-sm' : ''].filter(Boolean).join(' ') || undefined}>
                {h.head}
              </th>
            ))}
          </tr>
        </thead>
        <tbody aria-hidden="true">
          {Array.from({ length: rows }, (_, r) => (
            <tr key={r}>
              {heads.map((h, c) => (
                <td key={h.head} data-label={h.head} className={[h.num ? 'num' : '', h.drop ? 'pu-hide-sm' : ''].filter(Boolean).join(' ') || undefined}>
                  <Skeleton w={h.num ? 56 : c === 0 ? '74%' : '60%'} h={11} radius={999} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A stack of recorded purchases, at the shape one of those rows has. */
function PurchaseSkeletons({ rows = 3 }: { rows?: number }) {
  return (
    <div className="pu-list" role="status" aria-live="polite" aria-label="Reading the purchase book">
      {Array.from({ length: rows }, (_, i) => (
        <div className="pu-row pu-skel-row" key={i} aria-hidden="true">
          <div className="pu-row-head">
            <Skeleton w={84} h={12} radius={999} />
            <Skeleton w="38%" h={12} radius={999} />
            <Skeleton w={78} h={15} radius={999} className="pu-skel-amt" />
          </div>
          <div className="pu-row-sub">
            <Skeleton w={52} h={9} radius={999} />
            <Skeleton w={60} h={9} radius={999} />
            <Skeleton w={104} h={9} radius={999} />
          </div>
        </div>
      ))}
    </div>
  );
}

/** The suppliers list: a name, a phone, and what has been bought, right. */
function SupplierSkeletons({ rows = 3 }: { rows?: number }) {
  return (
    <div className="pu-list" role="status" aria-live="polite" aria-label="Reading the supplier list">
      {Array.from({ length: rows }, (_, i) => (
        <div className="pu-sup pu-skel-sup" key={i} aria-hidden="true">
          <div className="pu-sup-main">
            <Skeleton w="52%" h={13} radius={999} />
            <Skeleton w="34%" h={10} radius={999} />
          </div>
          <div className="pu-sup-fig">
            <Skeleton w={82} h={15} radius={999} />
            <Skeleton w={104} h={9} radius={999} />
          </div>
        </div>
      ))}
    </div>
  );
}

/** The invoice form: the three heading boxes, then the lines. */
function InvoiceSkeleton() {
  return (
    <div className="pu-skel" role="status" aria-live="polite" aria-label="Reading the supplier list">
      <div className="grid three" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <div className="pu-skel-field" key={i}>
            <Skeleton w="46%" h={10} radius={999} />
            <Skeleton h={38} />
          </div>
        ))}
      </div>
      <div className="pu-lines" aria-hidden="true">
        {[0, 1].map((i) => <Skeleton h={44} key={i} />)}
      </div>
    </div>
  );
}

function Stat({ label, value, sub, tone }: {
  label: string; value: string; sub: string; tone?: 'amber' | 'red';
}) {
  return (
    <div className={`pu-stat${tone ? ` ${tone}` : ''}`}>
      <span className="lbl">{label}</span>
      <span className="val">{value}</span>
      <span className="sub">{sub}</span>
    </div>
  );
}

/** UNKNOWN, said out loud. The one thing this screen must never render blank. */
function Unknown({ children = 'UNKNOWN' }: { children?: string }) {
  return <span className="pu-unknown">{children}</span>;
}

/* ------------------------------------------------------------------ page -- */

/** `photo` is PARCHI: the bill photographed and read, then booked through the
    same server writer RECORD A PURCHASE uses. See `PurchasesPhoto.tsx`. */
type View = 'margin' | 'record' | 'photo' | 'suppliers';

interface DraftLine { key: string; sku_id: string; units: string; cost: string; }

let seq = 0;
const blankLine = (sku = ''): DraftLine => ({ key: `l${++seq}`, sku_id: sku, units: '', cost: '' });

export default function Purchases() {
  const [view, setView] = useState<View>('margin');
  const [day, setDay] = useState<string>(() => pu.todayLabel());

  const [book, setBook] = useState<pu.MarginBook | null>(null);
  const [bookErr, setBookErr] = useState<Trouble | null>(null);
  const [today, setToday] = useState<pu.MarginToday | null>(null);
  const [todayErr, setTodayErr] = useState<Trouble | null>(null);
  const [marginLoading, setMarginLoading] = useState(true);

  const [sups, setSups] = useState<pu.Supplier[] | null>(null);
  const [supsErr, setSupsErr] = useState<Trouble | null>(null);
  const [pbook, setPbook] = useState<pu.PurchaseBook | null>(null);
  const [pbookErr, setPbookErr] = useState<Trouble | null>(null);
  const [shopLoading, setShopLoading] = useState(true);

  /* --- the invoice being typed ---------------------------------------- */
  const [supplierId, setSupplierId] = useState('');
  const [invoiceNo, setInvoiceNo] = useState('');
  const [bought, setBought] = useState<string>(() => pu.todayLabel());
  const [lines, setLines] = useState<DraftLine[]>(() => [blankLine()]);
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<Trouble | null>(null);
  const [saved, setSaved] = useState<pu.Purchase | null>(null);

  /* --- the supplier being typed --------------------------------------- */
  const [supName, setSupName] = useState('');
  const [supPhone, setSupPhone] = useState('');
  const [supNotes, setSupNotes] = useState('');
  const [editing, setEditing] = useState<string | null>(null);
  const [supSaving, setSupSaving] = useState(false);
  const [supErr, setSupErr] = useState<Trouble | null>(null);
  // A write that says so. The form empties itself on success, which on its own
  // is indistinguishable from a press that did nothing.
  const [supSaved, setSupSaved] = useState<{ name: string; corrected: boolean } | null>(null);

  /* --- one purchase at a time may be open, or being struck out --------- */
  const [openId, setOpenId] = useState<string | null>(null);
  const [voidingId, setVoidingId] = useState<string | null>(null);
  const [voidReason, setVoidReason] = useState('');
  const [voidErr, setVoidErr] = useState<Trouble | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // A strike that says so. The row it changed keeps its figures and gains a
  // struck-out note, which on a long list is easy to press and then not find.
  const [struck, setStruck] = useState<string | null>(null);

  const loadMargin = useCallback(async (which: string) => {
    setMarginLoading(true);
    const [b, t] = await Promise.all([pu.margin(which), pu.marginToday(which)]);
    if (b.ok) { setBook(b); setBookErr(null); } else { setBook(null); setBookErr(asTrouble(b)); }
    if (t.ok) { setToday(t); setTodayErr(null); } else { setToday(null); setTodayErr(asTrouble(t)); }
    setMarginLoading(false);
  }, []);

  const loadShop = useCallback(async () => {
    setShopLoading(true);
    const [s, p] = await Promise.all([pu.suppliers(), pu.purchases()]);
    if (s.ok) { setSups(s.suppliers); setSupsErr(null); } else { setSups(null); setSupsErr(asTrouble(s)); }
    if (p.ok) { setPbook(p); setPbookErr(null); } else { setPbook(null); setPbookErr(asTrouble(p)); }
    setShopLoading(false);
  }, []);

  useEffect(() => { void loadMargin(day); }, [loadMargin, day]);
  useEffect(() => { void loadShop(); }, [loadShop]);

  /** Products to pick from. The catalogue arrives on the margin response, so
      the picker and the margin table can never disagree about what exists. */
  const products = useMemo(
    () => (book?.items ?? []).filter((r) => r.still_in_catalogue),
    [book],
  );

  const nameOf = useCallback((sku: string) => {
    const hit = (book?.items ?? []).find((r) => r.sku_id === sku)
      ?? (today?.items ?? []).find((r) => r.sku_id === sku);
    return hit?.name ?? sku;
  }, [book, today]);

  /**
   * A row with NOTHING in it is not a line of an invoice, it is an empty box —
   * pressing ADD ANOTHER LINE and then saving must not be refused for a row
   * the shopkeeper never started. A row with ANYTHING in it is sent exactly as
   * typed, so the server names what is wrong with it rather than this page
   * quietly dropping a packet off the invoice.
   */
  const filled = useMemo(
    () => lines.filter((l) => l.sku_id || l.units.trim() || l.cost.trim()),
    [lines],
  );
  const check = useMemo(() => pu.assertions(filled), [filled]);

  const setLine = useCallback((key: string, patch: Partial<DraftLine>) => {
    setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  }, []);

  /** Jump from a product with no cost straight to the line that would fix it. */
  const recordCostFor = useCallback((sku: string) => {
    setLines((ls) => {
      const empty = ls.find((l) => !l.sku_id);
      if (empty) return ls.map((l) => (l.key === empty.key ? { ...l, sku_id: sku } : l));
      return [...ls, blankLine(sku)];
    });
    setSaved(null);
    setSaveErr(null);
    setView('record');
  }, []);

  const submitPurchase = useCallback(async () => {
    setSaving(true);
    setSaveErr(null);
    setSaved(null);
    const body: Parameters<typeof pu.recordPurchase>[0] = {
      supplier_id: supplierId,
      lines: filled.map((l, i) => {
        const units = pu.unitsOf(l.units);
        const line: pu.DraftLineBody = {
          sku_id: l.sku_id,
          // A half-filled line is sent half-filled — units 0 if the box is
          // empty — so the SERVER names what is wrong with it. Repairing it
          // here would save a short invoice, and a short bill is the failure
          // this program treats as disqualifying.
          units: units ?? 0,
          cost_rupees: l.cost.trim(),
        };
        const asserted = check?.lines[i];
        if (asserted !== undefined) line.line_paise = asserted;
        return line;
      }),
      date: bought,
    };
    if (invoiceNo.trim()) body.invoice_no = invoiceNo.trim();
    if (check) body.total_paise = check.total;

    const r = await pu.recordPurchase(body);
    setSaving(false);
    if (r.ok) {
      setSaved(r.purchase);
      setLines([blankLine()]);
      setInvoiceNo('');
      await Promise.all([loadShop(), loadMargin(day)]);
    } else {
      setSaveErr(asTrouble(r));
    }
  }, [supplierId, filled, check, bought, invoiceNo, loadShop, loadMargin, day]);

  const submitSupplier = useCallback(async () => {
    setSupSaving(true);
    setSupErr(null);
    setSupSaved(null);
    const body = { name: supName.trim(), phone: supPhone.trim(), notes: supNotes.trim() };
    const r = editing ? await pu.editSupplier(editing, body) : await pu.addSupplier(body);
    setSupSaving(false);
    if (r.ok) {
      setSupName(''); setSupPhone(''); setSupNotes(''); setEditing(null);
      setSupSaved({ name: r.supplier.name, corrected: !!editing });
      await loadShop();
      if (!editing) setSupplierId(r.supplier.supplier_id);
    } else {
      setSupErr(asTrouble(r));
    }
  }, [supName, supPhone, supNotes, editing, loadShop]);

  const strike = useCallback(async (id: string, what: string) => {
    setBusy(id);
    setVoidErr(null);
    setStruck(null);
    const r = await pu.voidPurchase(id, voidReason.trim());
    setBusy(null);
    if (r.ok) {
      setVoidingId(null);
      setVoidReason('');
      setStruck(what);
      await Promise.all([loadShop(), loadMargin(day)]);
    } else {
      setVoidErr(asTrouble(r));
    }
  }, [voidReason, loadShop, loadMargin, day]);

  const maxDay = pu.todayLabel();

  return (
    <div className="pu-page">
      <div className="page-head">
        <h1>Purchases</h1>
        <p>
          What this shop paid for its stock, and what that stock earns. A product with no
          recorded cost has an <b>unknown</b> margin here — not a zero, and not the whole
          selling price. Nothing on this screen pays a supplier or knows whether one has
          been paid.
        </p>
      </div>

      <div className="pu-switch">
        <Segmented<View>
          value={view}
          onChange={setView}
          options={[
            { value: 'margin', label: 'MARGIN', title: 'what each product earns, and what today earned' },
            { value: 'record', label: 'RECORD A PURCHASE', title: 'enter an invoice' },
            { value: 'photo', label: 'NEW FROM PHOTO', title: 'photograph the wholesaler’s bill and book it' },
            { value: 'suppliers', label: 'SUPPLIERS', title: 'who this shop buys from' },
          ]}
        />
      </div>

      {view === 'photo' && (
        <PhotoView
          suppliers={sups}
          products={products}
          onBooked={() => { void Promise.all([loadShop(), loadMargin(day)]); }}
        />
      )}

      {view === 'margin' && (
        <MarginView
          day={day}
          maxDay={maxDay}
          onDay={setDay}
          loading={marginLoading}
          book={book}
          bookErr={bookErr}
          today={today}
          todayErr={todayErr}
          nameOf={nameOf}
          onRetry={() => void loadMargin(day)}
          onRecordCost={recordCostFor}
        />
      )}

      {view === 'record' && (
        <RecordView
          suppliers={sups}
          supsErr={supsErr}
          loading={shopLoading}
          products={products}
          supplierId={supplierId}
          onSupplier={setSupplierId}
          bought={bought}
          onBought={setBought}
          maxDay={maxDay}
          invoiceNo={invoiceNo}
          onInvoice={setInvoiceNo}
          lines={lines}
          onLine={setLine}
          onAddLine={() => setLines((ls) => [...ls, blankLine()])}
          onDropLine={(k) => setLines((ls) => (ls.length > 1 ? ls.filter((l) => l.key !== k) : ls))}
          check={check}
          saving={saving}
          saveErr={saveErr}
          saved={saved}
          onSave={() => void submitPurchase()}
          onGoSuppliers={() => setView('suppliers')}
          pbook={pbook}
          pbookErr={pbookErr}
          openId={openId}
          onOpen={(id) => setOpenId((c) => (c === id ? null : id))}
          voidingId={voidingId}
          voidReason={voidReason}
          voidErr={voidErr}
          struck={struck}
          busy={busy}
          onStartVoid={(id) => { setVoidingId(id); setVoidReason(''); setVoidErr(null); setStruck(null); }}
          onVoidReason={(v) => { setVoidReason(v); setVoidErr(null); }}
          onCancelVoid={() => { setVoidingId(null); setVoidErr(null); }}
          onVoid={(id, what) => void strike(id, what)}
          onRetry={() => void loadShop()}
        />
      )}

      {view === 'suppliers' && (
        <SuppliersView
          loading={shopLoading}
          suppliers={sups}
          supsErr={supsErr}
          name={supName}
          phone={supPhone}
          notes={supNotes}
          editing={editing}
          saving={supSaving}
          err={supErr}
          saved={supSaved}
          onName={(v) => { setSupName(v); setSupErr(null); }}
          onPhone={(v) => { setSupPhone(v); setSupErr(null); }}
          onNotes={setSupNotes}
          onEdit={(s) => {
            setEditing(s.supplier_id);
            setSupName(s.name ?? '');
            setSupPhone(s.phone ?? '');
            setSupNotes(s.notes ?? '');
            setSupErr(null);
            setSupSaved(null);
          }}
          onCancel={() => {
            setEditing(null); setSupName(''); setSupPhone(''); setSupNotes('');
            setSupErr(null); setSupSaved(null);
          }}
          onSave={() => void submitSupplier()}
          onRetry={() => void loadShop()}
        />
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- margin -- */

function MarginView({
  day, maxDay, onDay, loading, book, bookErr, today, todayErr, nameOf, onRetry, onRecordCost,
}: {
  day: string;
  maxDay: string;
  onDay: (d: string) => void;
  loading: boolean;
  book: pu.MarginBook | null;
  bookErr: Trouble | null;
  today: pu.MarginToday | null;
  todayErr: Trouble | null;
  nameOf: (sku: string) => string;
  onRetry: () => void;
  onRecordCost: (sku: string) => void;
}) {
  const isToday = day === maxDay;
  /**
   * WHILE A DIFFERENT DAY IS BEING READ, THE FIGURES IN MEMORY ARE NOT THIS
   * DAY'S. They belong to the day that was in the picker a moment ago, and
   * leaving them on screen under the new date renders one day's margin labelled
   * as another's for as long as the read takes — on this screen, where the
   * whole point is what a particular day earned, that is the worst kind of
   * wrong figure: a plausible one. So the read replaces them with its own shape
   * rather than showing them stale.
   */
  const shown = loading ? null : today;
  const shownBook = loading ? null : book;
  const cov = shown?.covered;
  const unc = shown?.uncovered;

  return (
    <>
      <div className="pu-daybar">
        <label htmlFor="pu-day">Day</label>
        <input
          id="pu-day"
          type="date"
          value={day}
          max={maxDay}
          onChange={(e) => { if (e.target.value) onDay(e.target.value); }}
        />
        {!isToday && (
          <button className="btn sm ghost" onClick={() => onDay(maxDay)}>BACK TO TODAY</button>
        )}
        <span>
          Costs are read as they stood on this day. A purchase entered tomorrow does not
          change what this day earned.
        </span>
      </div>

      {loading && <StatSkeletons />}

      {todayErr && (
        <Refusal
          reason="The day's margin could not be worked out"
          detail={todayErr.reason}
          hint={mountHint(todayErr) ?? todayErr.detail}
          action={<button className="btn sm" onClick={onRetry}>TRY AGAIN</button>}
        />
      )}

      {shown && cov && unc && (
        <>
          <div className="pu-stats">
            <Stat
              label={isToday ? 'sold today' : `sold on ${dayWords(day)}`}
              value={rupees(shown.revenue_paise)}
              sub={`${shown.bills} bill${shown.bills === 1 ? '' : 's'} closed`}
            />
            <Stat
              label="what that stock cost"
              value={rupees(cov.cost_paise)}
              sub={`${cov.units} unit${cov.units === 1 ? '' : 's'} across ${cov.skus} product${cov.skus === 1 ? '' : 's'} with a cost on file`}
            />
            <Stat
              label="margin"
              value={signed(cov.margin_paise)}
              sub={
                cov.margin_pct_of_price
                  ? `${cov.margin_pct_of_price}% of the ${rupees(cov.revenue_paise)} it can account for`
                  : 'nothing with a recorded cost sold on this day'
              }
              {...(cov.margin_paise < 0 ? { tone: 'red' as const } : {})}
            />
            <Stat
              label="margin not known"
              value={rupees(unc.revenue_paise)}
              sub={
                unc.skus.length
                  ? `${unc.skus.length} product${unc.skus.length === 1 ? '' : 's'} sold with no cost recorded. Counted in the takings, left out of the margin.`
                  // "every product that sold has a cost on file" is true of a
                  // day when nothing sold, and reads as a claim about a shop
                  // that has done no trade. Say the plainer thing.
                  : shown.bills === 0
                    ? 'nothing has sold on this day'
                    : 'every product that sold has a cost on file'
              }
              {...(unc.skus.length ? { tone: 'amber' as const } : {})}
            />
          </div>

          {shown.margin_is_partial && (
            <Verdict tone="amber" title="This margin covers part of the day">
              {rupees(unc.revenue_paise)} of the day&rsquo;s {rupees(shown.revenue_paise)} is on
              products this counter has never been told the cost of. That revenue is counted in
              the takings and deliberately left out of the margin — it is not zero, and treating
              it as zero would report the shop earning all of it.
              <span className="pu-chips">
                {unc.skus.map((s) => (
                  <button
                    className="pu-chip"
                    key={s}
                    onClick={() => onRecordCost(s)}
                    title="Record what this cost"
                  >
                    {nameOf(s)}
                  </button>
                ))}
              </span>
            </Verdict>
          )}

          {shown.chain.exists && !shown.chain.ok && (
            <Verdict tone="red" title="The audit chain stops verifying">
              Revenue above is counted only as far as the break, so this day&rsquo;s takings — and
              therefore its margin — read low. Nothing has been adjusted to cover the gap.
              <br />
              <span className="mono">{shown.chain.error}</span>
            </Verdict>
          )}

          {shown.lines_without_a_price > 0 && (
            <p className="pu-note pu-loose">
              {shown.lines_without_a_price} line{shown.lines_without_a_price === 1 ? '' : 's'} on
              the chain carried no price and {shown.lines_without_a_price === 1 ? 'was' : 'were'}
              {' '}left out of both figures. Those are items the counter saw and refused to price,
              which is the counter working.
            </p>
          )}
        </>
      )}

      {/* Only when there is something to put in it. A card whose data was
          refused renders as an empty white box, and the refusal explaining why
          is already above it. */}
      {(loading || shown) && (
      <Card
        title={isToday ? 'What sold today' : `What sold on ${dayWords(day)}`}
        aside={shown ? (
          <Pill tone={shown.margin_is_partial ? 'amb' : 'off'}>
            {shown.items.length === 0
              ? 'NOTHING SOLD'
              : shown.margin_is_partial ? 'PART COVERED' : 'ALL COVERED'}
          </Pill>
        ) : undefined}
      >
        {loading && (
          <TableSkeleton
            label="Working out what sold on this day"
            rows={4}
            heads={[
              { head: 'Product' }, { head: 'Units', num: true }, { head: 'Sold for', num: true },
              { head: 'Cost of those', num: true }, { head: 'Earned', num: true },
            ]}
          />
        )}
        {shown && shown.items.length === 0 && (
          <Empty title="Nothing sold on this day">
            Nothing crossed the counter, so there is no margin to work out. The moment a bill
            closes on the till, what it earned appears here.
          </Empty>
        )}
        {shown && shown.items.length > 0 && (
          <div className="scroll-x">
            <table className="pu-table tbl-cards">
              <thead>
                <tr>
                  <th>Product</th>
                  <th className="num">Units</th>
                  <th className="num">Sold for</th>
                  <th className="num">Cost of those</th>
                  <th className="num">Earned</th>
                </tr>
              </thead>
              <tbody>
                {shown.items.map((r) => (
                  <tr
                    key={r.sku_id}
                    className={!r.cost_known ? 'pu-nocost' : r.below_cost ? 'pu-loss' : undefined}
                  >
                    <td>
                      <span className="pu-nm">{r.name}</span>
                      {!r.still_in_catalogue && (
                        <span className="pu-sku">no longer in the catalogue</span>
                      )}
                    </td>
                    <td className="num" data-label="Units">{r.units}</td>
                    <td className="num" data-label="Sold for">{rupees(r.revenue_paise)}</td>
                    <td className="num" data-label="Cost of those">
                      {r.cost_total_paise === null
                        ? <Unknown>NOT RECORDED</Unknown>
                        : rupees(r.cost_total_paise)}
                    </td>
                    <td className="num" data-label="Earned">
                      {r.margin_paise === null ? (
                        <span className="pu-unknown-cell">
                          <Unknown />
                          <button className="btn sm" onClick={() => onRecordCost(r.sku_id)}>
                            RECORD COST
                          </button>
                        </span>
                      ) : (
                        <>
                          <b className={r.below_cost ? 'pu-neg' : undefined}>
                            {signed(r.margin_paise)}
                          </b>
                          {r.margin_pct_of_price && (
                            <span className="pu-pct">{r.margin_pct_of_price}% of price</span>
                          )}
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {/* OUTSIDE the scrolling box on purpose — inside it, the hint sits at
            the far left of a 560 px canvas and scrolls away with the table. */}
        {shown && shown.items.length > 0 && (
          <p className="pu-scrollhint">Slide the table sideways for the rest of the columns.</p>
        )}
        {shown && (
          <p className="pu-note">
            {shown.derived_from}
          </p>
        )}
      </Card>
      )}

      <Card
        title="What each product earns"
        aside={shownBook ? (
          <Pill tone={shownBook.without_a_cost ? 'amb' : 'off'}>
            {shownBook.with_a_cost} of {shownBook.count} costed
          </Pill>
        ) : undefined}
      >
        <p className="pu-lede">
          The selling price is what the counter actually charges — with today&rsquo;s offers
          already applied, because the margin that matters is the margin on what a customer
          pays. The cost is the most recent one recorded on or before the chosen day.
        </p>

        {bookErr && (
          <Refusal
            reason="The margin list could not be read"
            detail={bookErr.reason}
            hint={mountHint(bookErr) ?? bookErr.detail}
            action={<button className="btn sm" onClick={onRetry}>TRY AGAIN</button>}
          />
        )}

        {loading && (
          <TableSkeleton
            label="Reading what each product earns"
            rows={5}
            heads={[
              { head: 'Product' }, { head: 'Sells for', num: true }, { head: 'Last paid', num: true },
              { head: 'Earns each', num: true }, { head: 'Cost from', drop: true },
            ]}
          />
        )}
        {shownBook && shownBook.items.length === 0 && (
          <Empty title="Nothing is priced yet">
            This counter has not been taught a product with a price, so there is nothing to work
            a margin on. Teach one on the Products screen, then record what it cost here.
          </Empty>
        )}

        {shownBook && shownBook.items.length > 0 && (
          <div className="scroll-x">
            <table className="pu-table tbl-cards">
              <thead>
                <tr>
                  <th>Product</th>
                  <th className="num">Sells for</th>
                  <th className="num">Last paid</th>
                  <th className="num">Earns each</th>
                  <th className="pu-hide-sm">Cost from</th>
                </tr>
              </thead>
              <tbody>
                {shownBook.items.map((r) => (
                  <tr
                    key={r.sku_id}
                    className={!r.cost_known ? 'pu-nocost' : r.below_cost ? 'pu-loss' : undefined}
                  >
                    <td>
                      <span className="pu-nm">{r.name}</span>
                      <span className="pu-sku">{r.sku_id}</span>
                    </td>
                    <td className="num" data-label="Sells for">
                      {r.on_offer && r.marked_paise !== undefined && (
                        <span className="pu-was">{rupees(r.marked_paise)}</span>
                      )}
                      {r.sell_paise === null ? <Unknown>NO PRICE</Unknown> : rupees(r.sell_paise)}
                    </td>
                    <td className="num" data-label="Last paid">
                      {r.cost_paise === null
                        ? <Unknown>NOT RECORDED</Unknown>
                        : rupees(r.cost_paise)}
                      {r.cost_recorded_on && (
                        <span className="pu-pct">{dayWords(r.cost_recorded_on)}</span>
                      )}
                    </td>
                    <td className="num" data-label="Earns each">
                      {r.margin_paise === null ? (
                        <span className="pu-unknown-cell">
                          <Unknown />
                          <button className="btn sm" onClick={() => onRecordCost(r.sku_id)}>
                            RECORD COST
                          </button>
                        </span>
                      ) : (
                        <>
                          <b className={r.below_cost ? 'pu-neg' : undefined}>
                            {signed(r.margin_paise)}
                          </b>
                          <span className="pu-pct">
                            {r.margin_pct_of_price}% of price · {r.markup_pct_of_cost}% on cost
                          </span>
                        </>
                      )}
                    </td>
                    <td className="pu-hide-sm" data-label="Cost from">
                      {r.cost_from?.supplier_name
                        ? <>
                            {r.cost_from.supplier_name}
                            {r.cost_from.invoice_no && (
                              <span className="pu-sku">invoice {r.cost_from.invoice_no}</span>
                            )}
                          </>
                        : <span className="muted">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {shownBook && shownBook.items.length > 0 && (
          <p className="pu-scrollhint">Slide the table sideways for the rest of the columns.</p>
        )}

        {shownBook && (
          <p className="pu-note">
            <b>Two percentages, because they are two numbers.</b> ₹25 earned on a ₹100 sale is a
            25% margin and a 33.3% markup, and a single figure would let each person read the one
            they expected. Both are floored, so a loss is never shown smaller than it is.
            {shownBook.below_cost.length > 0 && (
              <>
                {' '}
                <br />
                <b>{shownBook.below_cost.length} product{shownBook.below_cost.length === 1 ? ' is' : 's are'} selling
                below cost:</b> {shownBook.below_cost.map(nameOf).join(', ')}.
              </>
            )}
            {shownBook.bought_but_not_in_the_catalogue.length > 0 && (
              <>
                {' '}
                <br />
                {shownBook.bought_but_not_in_the_catalogue.length} product
                {shownBook.bought_but_not_in_the_catalogue.length === 1 ? ' has' : 's have'} a recorded
                cost but no longer sell here, so they are not listed above.
              </>
            )}
            {' '}
            <br />
            A sale is costed at the last price recorded on or before its day — not lot by lot. A
            shop that bought the same item twice in a week at two rates gets the later rate on
            everything sold after it.
          </p>
        )}
      </Card>
    </>
  );
}

/* ---------------------------------------------------------------- record -- */

function RecordView(p: {
  suppliers: pu.Supplier[] | null;
  supsErr: Trouble | null;
  loading: boolean;
  products: pu.MarginRow[];
  supplierId: string;
  onSupplier: (v: string) => void;
  bought: string;
  onBought: (v: string) => void;
  maxDay: string;
  invoiceNo: string;
  onInvoice: (v: string) => void;
  lines: DraftLine[];
  onLine: (key: string, patch: Partial<DraftLine>) => void;
  onAddLine: () => void;
  onDropLine: (key: string) => void;
  check: pu.Assertions | null;
  saving: boolean;
  saveErr: Trouble | null;
  saved: pu.Purchase | null;
  onSave: () => void;
  onGoSuppliers: () => void;
  pbook: pu.PurchaseBook | null;
  pbookErr: Trouble | null;
  openId: string | null;
  onOpen: (id: string) => void;
  voidingId: string | null;
  voidReason: string;
  voidErr: Trouble | null;
  struck: string | null;
  busy: string | null;
  onStartVoid: (id: string) => void;
  onVoidReason: (v: string) => void;
  onCancelVoid: () => void;
  onVoid: (id: string, what: string) => void;
  onRetry: () => void;
}) {
  const hasSuppliers = !!p.suppliers?.length;
  const ready = !!p.supplierId && p.lines.some((l) => l.sku_id && l.units.trim() && l.cost.trim());
  // A disabled button that does not say why reads as a broken one. Name the
  // FIRST thing still missing rather than all of them at once.
  const whyNotReady = !p.supplierId ? 'Choose which supplier this invoice came from.'
    : 'Fill one line completely — a product, a whole number of packets, and what each one cost.';
  // Where the server put its refusal, when it named a box.
  const box = whichBox(p.saveErr);
  // A line refusal names the sku it is about, in quotes, in its own detail —
  // `'atta-5kg' is not a product this shop has been taught`. That is enough to
  // put the reason on the line that caused it instead of at the foot of an
  // invoice with eleven of them.
  const badSku = box === 'line' ? skuNamed(p.saveErr, p.lines) : null;

  return (
    <>
      <Card title="Record a purchase" aside={<Pill tone="code">THE SERVER TOTALS IT</Pill>}>
        {p.supsErr && (
          <Refusal
            reason="The supplier list could not be read"
            detail={p.supsErr.reason}
            hint={mountHint(p.supsErr) ?? p.supsErr.detail}
            action={<button className="btn sm" onClick={p.onRetry}>TRY AGAIN</button>}
          />
        )}

        {p.loading && !p.suppliers && <InvoiceSkeleton />}

        {p.suppliers && !hasSuppliers && (
          <Empty
            title="No suppliers yet"
            action={<Button variant="primary" onClick={p.onGoSuppliers}>ADD A SUPPLIER</Button>}
          >
            A purchase has to be filed against a supplier, and this shop has none on the list.
            Add the wholesaler it buys most from and come back — the invoice form appears here.
          </Empty>
        )}

        {hasSuppliers && (
          <>
            <div className="grid three">
              <Field
                label="Bought from"
                htmlFor="pu-sup"
                error={box === 'supplier' && p.saveErr ? said(p.saveErr) : undefined}
              >
                <select
                  id="pu-sup"
                  value={p.supplierId}
                  aria-invalid={box === 'supplier'}
                  onChange={(e) => p.onSupplier(e.target.value)}
                >
                  <option value="">Choose a supplier</option>
                  {p.suppliers?.map((s) => (
                    <option key={s.supplier_id} value={s.supplier_id}>{s.name}</option>
                  ))}
                </select>
              </Field>
              <Field
                label="Day it arrived"
                htmlFor="pu-date"
                sub="Stock cannot arrive on a day that has not come."
                error={box === 'date' && p.saveErr ? said(p.saveErr) : undefined}
              >
                <input
                  id="pu-date"
                  type="date"
                  value={p.bought}
                  max={p.maxDay}
                  aria-invalid={box === 'date'}
                  onChange={(e) => p.onBought(e.target.value)}
                />
              </Field>
              <Field
                label={<>Invoice number <span className="muted">(optional)</span></>}
                htmlFor="pu-inv"
                sub="Entering the same invoice twice is refused by name."
                error={box === 'invoice' && p.saveErr ? said(p.saveErr) : undefined}
              >
                <input
                  id="pu-inv"
                  type="text"
                  placeholder="MT/4821"
                  maxLength={40}
                  value={p.invoiceNo}
                  aria-invalid={box === 'invoice'}
                  onChange={(e) => p.onInvoice(e.target.value)}
                />
              </Field>
            </div>

            <div className="pu-lhead">
              <span>Product</span>
              <span className="num">Units</span>
              <span className="num">Cost each (₹)</span>
              <span className="num">Line</span>
              <span />
            </div>

            <div className="pu-lines" style={{ marginTop: 12 }}>
              {p.lines.map((l) => {
                const cost = pu.rupeesToPaise(l.cost);
                const units = pu.unitsOf(l.units);
                // Per LINE, not from the invoice assertion: a half-typed row
                // further down must not blank out the figure beside a row that
                // is already complete.
                const sum = pu.linePaise(l.units, l.cost);
                // A BOX THIS PAGE WILL NOT SEND IS NAMED, at the box, with the
                // state the server would refuse it under — never rounded into
                // something it would send. `21,50` is not twenty-one fifty here;
                // it is a shape neither side parses, and saying so beside the
                // box is the difference between a typo and a wrong cost frozen
                // into every margin that product has from now on.
                const costBad = !!l.cost.trim() && cost === null;
                const unitsBad = !!l.units.trim() && units === null;
                const serverBad = badSku !== null && badSku === l.sku_id;
                return (
                  <div className={serverBad ? 'pu-line bad' : 'pu-line'} key={l.key}>
                    <div className="pu-cell-sku">
                      <span className="pu-mini">Product</span>
                      <select
                        aria-label="Product"
                        value={l.sku_id}
                        aria-invalid={serverBad}
                        onChange={(e) => p.onLine(l.key, { sku_id: e.target.value })}
                      >
                        <option value="">Choose a product</option>
                        {p.products.map((r) => (
                          <option key={r.sku_id} value={r.sku_id}>
                            {r.name}{r.sell_rupees ? ` — sells at ₹${r.sell_rupees}` : ''}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="pu-cell-units">
                      <span className="pu-mini">Units</span>
                      <input
                        className={unitsBad ? 'tnum bad' : 'tnum'}
                        aria-label="Units"
                        type="text"
                        inputMode="numeric"
                        placeholder="12"
                        value={l.units}
                        aria-invalid={unitsBad}
                        onChange={(e) => p.onLine(l.key, { units: e.target.value })}
                      />
                    </div>
                    <div className="pu-cell-cost">
                      <span className="pu-mini">Cost each (₹)</span>
                      {/* TEXT, NOT number. A decimal in a number input arrives as
                          a float and 21.10 is lossy before anything rounds it.
                          The server parses this string to integer paise. */}
                      <input
                        className={costBad ? 'tnum bad' : 'tnum'}
                        aria-label="Cost each in rupees"
                        type="text"
                        inputMode="decimal"
                        placeholder="21.50"
                        value={l.cost}
                        aria-invalid={costBad}
                        onChange={(e) => p.onLine(l.key, { cost: e.target.value })}
                      />
                    </div>
                    <div className={sum === null ? 'pu-lsum quiet' : 'pu-lsum'}>
                      {sum !== null
                        ? rupees(sum)
                        : costBad
                          ? 'not an amount'
                          : unitsBad
                            ? 'whole packets only'
                            : '—'}
                    </div>
                    <button
                      className="pu-drop"
                      aria-label="Remove this line"
                      title={p.lines.length === 1
                        ? 'The last line cannot be removed — an invoice needs one'
                        : 'Remove this line'}
                      disabled={p.lines.length === 1}
                      onClick={() => p.onDropLine(l.key)}
                    >
                      ✕
                    </button>
                    {/* The reason, on the line it is about. */}
                    {(costBad || unitsBad || serverBad) && (
                      <span className="pu-lerr">
                        {serverBad && p.saveErr ? said(p.saveErr)
                          : costBad
                            ? <><span className="mono">cost_not_positive_integer_paise</span>
                                {' — rupees and paise, two decimals at most. 21, or 21.50. '}
                                {'Nothing here turns what you typed into a number.'}</>
                            : <><span className="mono">units_not_a_whole_number</span>
                                {' — a count of packets is a whole number. A shelf does not hold half a packet.'}</>}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>

            <div className="btn-row" style={{ marginTop: 12 }}>
              <button className="btn sm" onClick={p.onAddLine}>ADD ANOTHER LINE</button>
            </div>

            <div className="pu-foot">
              <div className="pu-foot-sum">
                <span className="lbl">These lines come to</span>
                <span className={p.check ? 'val' : 'val quiet'}>
                  {p.check ? rupees(p.check.total) : 'not yet'}
                </span>
                <span className="sub">
                  {p.check
                    ? 'Your own check against the paper. The counter works the total out again when you save, and refuses the invoice if the two disagree.'
                    : 'A figure appears here once every line has a product, a whole number of packets and a cost. Until then there is nothing to add up.'}
                </span>
              </div>
              <div className="btn-row">
                {/* `loading` disables it, so a second press on a slow connection
                    cannot become a second invoice — which the server would then
                    refuse as a duplicate, or worse, accept. */}
                <Button
                  variant="primary"
                  loading={p.saving}
                  disabled={!ready}
                  title={ready ? undefined : whyNotReady}
                  onClick={p.onSave}
                >
                  {p.saving ? 'SAVING…' : 'RECORD THIS PURCHASE'}
                </Button>
                {!ready && !p.saving && <span className="pu-gate">{whyNotReady}</span>}
                {p.saving && <span className="pu-gate">Totalling the lines and writing the invoice…</span>}
              </div>
            </div>

            {/* Only what could not be pinned on a box. A refusal about the
                invoice number is already under the invoice number, and one
                refusal shown twice reads as two refusals. */}
            {p.saveErr && (box === null || (box === 'line' && badSku === null)) && (
              <div style={{ marginTop: 12 }}>
                <Refusal reason={p.saveErr.reason} detail={p.saveErr.detail} hint={mountHint(p.saveErr)} />
              </div>
            )}

            {p.saved && (
              <div style={{ marginTop: 12 }}>
                <Verdict tone="info" title={`Recorded — ${rupees(p.saved.total_paise)}`}>
                  {p.saved.units} unit{p.saved.units === 1 ? '' : 's'} from {p.saved.supplier_name}
                  {p.saved.invoice_no ? ` on invoice ${p.saved.invoice_no}` : ''}, dated{' '}
                  {dayWords(p.saved.date)}. That total is the counter&rsquo;s own arithmetic over
                  the lines, not the figure this page showed you. Nothing was paid.
                </Verdict>
              </div>
            )}

            <p className="pu-note">
              A cost of zero is refused: free stock from a supplier scheme has to go on at the
              invoice rate or stay off the record, because a zero would make that product look
              like pure profit for as long as it stayed the most recent cost. A purchase cannot be
              edited afterwards — only struck out and entered again, so a cost can never change
              without leaving a trace.
            </p>
          </>
        )}
      </Card>

      <Card
        title="What has been bought"
        aside={p.pbook ? (
          <Pill tone="off">{p.pbook.count} recorded · {rupees(p.pbook.spent_paise)}</Pill>
        ) : undefined}
      >
        {p.pbookErr && (
          <Refusal
            reason="The purchase book could not be read"
            detail={p.pbookErr.reason}
            hint={mountHint(p.pbookErr) ?? p.pbookErr.detail}
            action={<button className="btn sm" onClick={p.onRetry}>TRY AGAIN</button>}
          />
        )}
        {p.struck && (
          <div style={{ marginBottom: 12 }}>
            <Verdict tone="info" title="Struck out">
              {p.struck} is struck out. It stays on this list with its lines and its total, counted
              in no figure and setting no cost price. Nothing was deleted, and there is no undo —
              enter it again if it was struck out by mistake.
            </Verdict>
          </div>
        )}
        {p.loading && !p.pbook && <PurchaseSkeletons rows={3} />}
        {p.pbook && p.pbook.purchases.length === 0 && (
          <Empty title="No purchase recorded yet">
            Until a cost is entered, every margin on this counter reads unknown — not zero.
            Enter one invoice above and the margin screen starts answering.
          </Empty>
        )}

        <div className="pu-list">
          {(p.pbook?.purchases ?? []).map((r) => (
            <div className={`pu-row${r.void ? ' pu-void' : ''}`} key={r.purchase_id}>
              <div className="pu-row-head">
                <span className="pu-when">{dayWords(r.date)}</span>
                <span className="pu-who">
                  {r.supplier_name ?? 'supplier no longer named'}
                  {r.invoice_no && <span className="muted"> · invoice {r.invoice_no}</span>}
                </span>
                <span className="pu-amt">{rupees(r.total_paise)}</span>
              </div>
              <div className="pu-row-sub">
                <span>{r.lines.length} line{r.lines.length === 1 ? '' : 's'}</span>
                <span>{r.units} unit{r.units === 1 ? '' : 's'}</span>
                <span className="mono">{r.purchase_id}</span>
              </div>

              {r.void && (
                <div className="pu-voidnote">
                  Struck out{r.voided_at ? ` on ${dayWords(r.voided_at.slice(0, 10))}` : ''}:{' '}
                  {r.void_reason}. It is counted in no total and sets no cost price. Nothing was
                  deleted.
                </div>
              )}

              <div className="pu-row-acts">
                <button className="btn sm ghost" onClick={() => p.onOpen(r.purchase_id)}>
                  {p.openId === r.purchase_id ? 'HIDE LINES' : 'SHOW LINES'}
                </button>
                {!r.void && p.voidingId !== r.purchase_id && (
                  <button className="btn sm ghost" onClick={() => p.onStartVoid(r.purchase_id)}>
                    STRIKE OUT
                  </button>
                )}
              </div>

              {p.voidingId === r.purchase_id && (
                /* THE REASON BOX IS THE CONFIRMATION. Striking out is not
                   undoable and it moves every margin this product has, so it
                   does not happen on one press: the act asks what it is for and
                   the button stays shut until there is an answer. A word typed
                   on purpose is a firmer gate than an OK on a dialog, and it is
                   the word that explains the strike a year later. */
                <>
                  <div className="pu-voidform">
                    <input
                      type="text"
                      className={p.voidErr ? 'bad' : undefined}
                      aria-label="Why this purchase is being struck out"
                      placeholder="Why — a wrong cost, an invoice entered twice…"
                      maxLength={200}
                      value={p.voidReason}
                      aria-invalid={!!p.voidErr}
                      onChange={(e) => p.onVoidReason(e.target.value)}
                    />
                    <Button
                      variant="danger"
                      size="sm"
                      loading={p.busy === r.purchase_id}
                      disabled={!p.voidReason.trim()}
                      title={p.voidReason.trim()
                        ? 'Strike this purchase out — it cannot be undone'
                        : 'Say why first. A purchase struck out with no reason is indistinguishable from a deleted one.'}
                      onClick={() => p.onVoid(
                        r.purchase_id,
                        `${dayWords(r.date)}${r.invoice_no ? ` · invoice ${r.invoice_no}` : ''} · ${rupees(r.total_paise)}`,
                      )}
                    >
                      {p.busy === r.purchase_id ? 'STRIKING…' : 'STRIKE IT OUT'}
                    </Button>
                    <button
                      className="btn sm ghost"
                      disabled={p.busy === r.purchase_id}
                      onClick={p.onCancelVoid}
                    >
                      CANCEL
                    </button>
                  </div>
                  <span className="pu-voidwhy">
                    {p.voidErr
                      ? <span className="pu-lerr">{said(p.voidErr)}</span>
                      : p.voidReason.trim()
                        ? 'It stays on this list with its lines and its total, counted in nothing, and sets no cost price. Nothing is deleted.'
                        : 'Say why in a few words. It then stays on this list with its lines and its total, counted in nothing and setting no cost price — nothing is deleted.'}
                  </span>
                </>
              )}

              {p.openId === r.purchase_id && (
                <div className="pu-sublines scroll-x">
                  <table className="pu-table tbl-cards">
                    <thead>
                      <tr>
                        <th>Product</th>
                        <th className="num">Units</th>
                        <th className="num">Cost each</th>
                        <th className="num">Line</th>
                      </tr>
                    </thead>
                    <tbody>
                      {r.lines.map((ln, i) => (
                        <tr key={`${ln.sku_id}-${i}`}>
                          <td><span className="pu-nm">{ln.name}</span></td>
                          <td className="num" data-label="Units">{ln.units}</td>
                          <td className="num" data-label="Cost each">{rupees(ln.cost_paise)}</td>
                          <td className="num" data-label="Line">{rupees(ln.line_paise)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ))}
        </div>

        {p.pbook && p.pbook.void_count > 0 && (
          <p className="pu-note">{p.pbook.note}</p>
        )}
      </Card>
    </>
  );
}

/* ------------------------------------------------------------- suppliers -- */

function SuppliersView(p: {
  loading: boolean;
  suppliers: pu.Supplier[] | null;
  supsErr: Trouble | null;
  name: string;
  phone: string;
  notes: string;
  editing: string | null;
  saving: boolean;
  err: Trouble | null;
  saved: { name: string; corrected: boolean } | null;
  onName: (v: string) => void;
  onPhone: (v: string) => void;
  onNotes: (v: string) => void;
  onEdit: (s: pu.Supplier) => void;
  onCancel: () => void;
  onSave: () => void;
  onRetry: () => void;
}) {
  const rows = p.suppliers ?? [];
  const box = whichBox(p.err);
  const whyNotSave = !p.name.trim() ? 'A supplier needs a name.'
    : !p.phone.trim() ? 'A phone number is required — it is how a short delivery gets chased.'
      : null;
  return (
    <div className="pu-grid">
      <Card title={p.editing ? 'Correct a supplier' : 'Add a supplier'}>
        <Field
          label="Name"
          htmlFor="pu-name"
          error={box === 'name' && p.err ? said(p.err) : undefined}
        >
          <input
            id="pu-name"
            type="text"
            placeholder="Sharma Traders"
            maxLength={80}
            value={p.name}
            aria-invalid={box === 'name'}
            onChange={(e) => p.onName(e.target.value)}
          />
        </Field>
        <Field
          label="Phone"
          htmlFor="pu-phone"
          sub="Required. It is how a short delivery gets chased, and it is what tells two wholesalers with the same family name apart."
          error={box === 'phone' && p.err ? said(p.err) : undefined}
        >
          <input
            id="pu-phone"
            type="text"
            inputMode="tel"
            placeholder="98200 12345"
            maxLength={24}
            value={p.phone}
            aria-invalid={box === 'phone'}
            onChange={(e) => p.onPhone(e.target.value)}
          />
        </Field>
        <Field label={<>Notes <span className="muted">(optional)</span></>} htmlFor="pu-notes">
          <textarea
            id="pu-notes"
            rows={3}
            placeholder="Delivers Tuesdays. Ask for Vikas."
            maxLength={400}
            value={p.notes}
            onChange={(e) => p.onNotes(e.target.value)}
          />
        </Field>

        <div className="btn-row">
          <Button
            variant="primary"
            loading={p.saving}
            disabled={!!whyNotSave}
            title={whyNotSave ?? undefined}
            onClick={p.onSave}
          >
            {p.saving ? 'SAVING…' : p.editing ? 'SAVE THE CORRECTION' : 'ADD THIS SUPPLIER'}
          </Button>
          {p.editing && (
            <button className="btn ghost" disabled={p.saving} onClick={p.onCancel}>CANCEL</button>
          )}
          {whyNotSave && !p.saving && <span className="pu-gate">{whyNotSave}</span>}
          {p.saving && <span className="pu-gate">Writing it to the supplier list…</span>}
        </div>

        {p.saved && !p.saving && (
          <div style={{ marginTop: 12 }}>
            <Verdict tone="info" title={`${p.saved.name} is on the list`}>
              {p.saved.corrected
                ? 'The correction is saved. Purchases keep the name they were filed under, so nothing already on the books has been rewritten.'
                : 'It is selected on the invoice form, so the next purchase can be filed against it straight away.'}
            </Verdict>
          </div>
        )}

        {/* Only what could not be pinned on a box. */}
        {p.err && !box && (
          <div style={{ marginTop: 12 }}>
            <Refusal reason={p.err.reason} detail={p.err.detail} hint={mountHint(p.err)} />
          </div>
        )}

        <p className="pu-note">
          A supplier can be corrected but not deleted — deleting one would orphan every purchase
          filed against it. Purchases keep the name they were filed under, so a correction here
          never rewrites what last year&rsquo;s invoices say.
        </p>
      </Card>

      <Card
        title="Who this shop buys from"
        aside={<Pill tone="off">{rows.length} on the list</Pill>}
      >
        {p.supsErr && (
          <Refusal
            reason="The supplier list could not be read"
            detail={p.supsErr.reason}
            hint={mountHint(p.supsErr) ?? p.supsErr.detail}
            action={<button className="btn sm" onClick={p.onRetry}>TRY AGAIN</button>}
          />
        )}
        {p.loading && !p.suppliers && <SupplierSkeletons rows={3} />}
        {p.suppliers && rows.length === 0 && (
          <Empty title="Nobody on the list yet">
            Add the wholesaler the shop buys most from, in the box beside this one. A purchase
            has to be filed against a supplier, so this is the first step to a margin.
          </Empty>
        )}

        <div className="pu-list">
          {rows.map((s) => (
            <div className="pu-sup" key={s.supplier_id}>
              <div className="pu-sup-main">
                <div className="pu-sup-name">{s.name}</div>
                <div className="pu-sup-phone">{s.phone}</div>
                {s.notes && <div className="pu-sup-notes">{s.notes}</div>}
              </div>
              <div className="pu-sup-fig">
                {/* The grouped renderer, not the server's plain string: this
                    column is read down against the others on the screen and
                    "12450.00" beside "₹12,450.00" reads as two currencies. The
                    paise are the server's either way. */}
                <span className="amt">{rupees(s.bought_paise ?? 0)}</span>
                <span className="cnt">
                  across {s.purchases ?? 0} purchase{(s.purchases ?? 0) === 1 ? '' : 's'}
                </span>
                <button className="btn sm ghost" onClick={() => p.onEdit(s)}>EDIT</button>
              </div>
            </div>
          ))}
        </div>

        {rows.length > 0 && (
          <p className="pu-note">
            What was bought is the sum of the purchases recorded here. It is <b>not</b> what is
            owed: this counter has no payables ledger, no due dates and no way to pay anybody.
          </p>
        )}
      </Card>
    </div>
  );
}
