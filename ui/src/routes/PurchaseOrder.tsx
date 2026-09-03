import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import * as poapi from '../lib/poapi';
import {
  Button, Card, Empty, Field, IcoParcel, IcoReceipt, Input, Modal,
  Refusal, Skeleton, Stat, StatGrid, Table, Toaster, Verdict, toast, type Column,
} from '../components/ui';
import '../styles/po.css';

/**
 * Purchase order — what is running out, and who to order it from.
 *
 * THE FOUR THINGS THIS SCREEN IS HONEST ABOUT, because each is a place an
 * ordering page normally lies:
 *
 *  1. IT ORDERS NOTHING BY ITSELF. Every quantity on this page is the reorder
 *     level the shopkeeper set minus what the Stock screen says is on the
 *     shelf, derived on the server on every request. This page cannot send a
 *     quantity — `poapi.confirm` takes none — and the server refuses a body
 *     carrying one by name. What the shopkeeper changes to change an order is
 *     his reorder level, on the screen where that decision belongs.
 *  2. AN UNKNOWN COST IS THE WORD "UNKNOWN". A product this shop has never
 *     recorded buying has no cost, so the line shows the packets and the word,
 *     and the order's expected spend says how many lines it does not cover.
 *     ₹0.00 for six packets of soap would be the confident, wrong number this
 *     whole counter exists not to print.
 *  3. CONFIRMING IS NOT RECEIVING. An order is a piece of paper. The shelf
 *     figure moves when the delivery is opened and booked in on the Stock
 *     screen, and this page says so beside the button rather than in a
 *     footnote.
 *  4. NOTHING IS PAID HERE. There is no gateway on this screen and no payable
 *     string in the message it produces. What travels to a wholesaler is a
 *     list.
 *
 * COLOUR. Green, amber and red belong to money and recognition state. A shelf
 * running low is neither, so the shortfall is rendered QUIET — weight and a
 * rule, as on the Stock screen. The ONE amber on this page is an unknown cost,
 * which is a figure the counter abstained from, which is precisely what amber
 * means here. Red is kept for a hash chain that does not verify.
 */

/* --------------------------------------------------------------- fragments -- */

/**
 * A rupee figure, or the absence of one — never a zero standing in for both.
 *
 * `poapi.rupees` owns the null branch so no caller can print "₹0.00" for a cost
 * nobody has ever recorded; this adds the amber and the explanation.
 */
function Money({ rupees, why }: { rupees: string | null; why?: string | null }) {
  if (!rupees) {
    return (
      <span className="po-unknown" title={why ?? 'No cost has ever been recorded for this product.'}>
        unknown
      </span>
    );
  }
  return <span className="tnum">{poapi.rupees(rupees)}</span>;
}

/**
 * One figure in the row above a dialog's detail.
 *
 * Not `KV`: that primitive draws a dotted leader from the label to the value,
 * which is right down a list and reads as a rendering fault across a row.
 */
function Fig({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="po-fig">
      <span className="po-fig-l">{label}</span>
      <span className="po-fig-v tnum">{children}</span>
    </div>
  );
}

/**
 * A figure on its way, at the size and weight of the figure.
 *
 * The four tiles used to render an em dash while the request was in flight,
 * which is the SAME mark this page uses for "the server would not say" — so a
 * screen that was merely slow was indistinguishable from one that had been
 * refused. A bar is a bar; a dash is an answer.
 */
function StatSkel({ w }: { w: number }) {
  return <span className="po-statskel"><Skeleton w={w} h={26} radius={8} /></span>;
}

/** What the shelf holds, or the fact that nobody has counted it. */
function OnHand({ units }: { units: number | null }) {
  if (units === null || units === undefined) return <span className="po-absent">not counted</span>;
  return <b className="tnum">{units}</b>;
}

/**
 * The expected spend for one order, with what it does not cover.
 *
 * The server's own sentence is printed under the figure rather than
 * paraphrased: it is the machine's account of what it could and could not
 * price, and it is different in three ways depending on which case this is.
 */
function Expected({ group }: { group: poapi.PoGroup }) {
  return (
    <div className="po-expect">
      <span className="po-expect-l">Expected</span>
      <span className={`po-expect-v tnum${group.expected_rupees ? '' : ' unknown'}`}>
        {group.expected_rupees ? poapi.rupees(group.expected_rupees) : 'not known'}
        {group.expected_is_partial && <span className="po-plus"> + unknown</span>}
      </span>
      <span className="po-expect-s">{group.expected_note}</span>
    </div>
  );
}

/** One product left off the order, and the sentence saying why. */
function Reason({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div className="po-reason">
      <span className="po-reason-n">{name}</span>
      <span className="po-reason-w">{children}</span>
    </div>
  );
}

const LINE_COLS: ReadonlyArray<Column<poapi.PoLine>> = [
  {
    key: 'name',
    head: 'Product',
    cell: (l) => (
      <>
        <span className="po-name">{l.name ?? l.sku_id}</span>
        <span className="po-sku mono">{l.sku_id}</span>
      </>
    ),
  },
  { key: 'hand', head: 'On hand', num: true, drop: true, cell: (l) => <OnHand units={l.on_hand_units} /> },
  { key: 'level', head: 'Level', num: true, drop: true, cell: (l) => <span className="tnum">{l.reorder_level}</span> },
  { key: 'order', head: 'Order', num: true, cell: (l) => <b className="po-units tnum">{l.units_to_order}</b> },
  {
    key: 'cost',
    head: 'Last cost',
    num: true,
    drop: true,
    cell: (l) => <Money rupees={l.cost_rupees} why={l.why_no_cost} />,
  },
  { key: 'line', head: 'Line', num: true, cell: (l) => <Money rupees={l.line_rupees} why={l.why_no_cost} /> },
];

/* ------------------------------------------------------------------ screen -- */

export default function PurchaseOrder() {
  const [body, setBody] = useState<poapi.Draft | null>(null);
  const [err, setErr] = useState<{ reason: string; detail?: string } | null>(null);
  const [loading, setLoading] = useState(true);

  const [orders, setOrders] = useState<poapi.PoList | null>(null);

  /** The supplier whose order is being placed, and the note being typed on it. */
  const [asking, setAsking] = useState<poapi.PoGroup | null>(null);
  const [note, setNote] = useState('');
  const [placing, setPlacing] = useState(false);
  const [placeErr, setPlaceErr] = useState<{ reason: string; detail?: string } | null>(null);

  /** The order that was just written, or one opened off the list. */
  const [placed, setPlaced] = useState<poapi.Po | null>(null);
  const [message, setMessage] = useState('');
  const [printUrl, setPrintUrl] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    const [d, l] = await Promise.all([poapi.draft(), poapi.list()]);
    setLoading(false);
    if (d.ok) { setBody(d); setErr(null); } else { setErr(d); setBody(null); }
    if (l.ok) setOrders(l);
  }, []);

  useEffect(() => { void load(); }, [load]);

  const place = useCallback(async () => {
    if (!asking?.supplier_id) return;
    setPlacing(true);
    // NO QUANTITY AND NO PRICE CROSS THIS CALL. The supplier, optionally the
    // lines to leave out, and the shopkeeper's own sentence — that is all the
    // authority this page has.
    const r = await poapi.confirm(asking.supplier_id, undefined, note.trim() || undefined);
    setPlacing(false);
    if (!r.ok) { setPlaceErr(r); return; }
    setAsking(null);
    setNote('');
    setPlaceErr(null);
    setPlaced(r.po);
    setMessage(r.share_text);
    setPrintUrl(r.print_url);
    toast(`Order written for ${r.po.supplier_name ?? r.po.supplier_id}`, {
      note: 'Nothing has been paid and no stock has been received.',
    });
    await load();
  }, [asking, note, load]);

  const open = useCallback(async (poId: string) => {
    const r = await poapi.one(poId);
    if (!r.ok) { toast('That order could not be opened', { tone: 'amb', note: r.reason }); return; }
    setPlaced(r.po);
    setMessage(r.share_text);
    setPrintUrl(r.print_url);
  }, []);

  const groups = body?.groups ?? [];
  const orderable = useMemo(() => groups.filter((g) => g.can_confirm), [groups]);
  const orphan = useMemo(() => groups.find((g) => !g.can_confirm) ?? null, [groups]);
  const chain = body?.chain ?? orders?.chain ?? null;

  /**
   * Not one product has a reorder level.
   *
   * The distinction this whole screen turns on: a nought that MEANS nothing is
   * low, and a nought that means nothing has been compared. The server reports
   * both counts and the page had been collapsing them into the same zero.
   */
  const noLevels = !!body && (body.skus_with_a_level ?? 0) === 0;

  return (
    <div>
      <Toaster />

      <div className="page-head">
        <h1>Purchase order</h1>
        <p>
          What is at or under the reorder level you set, grouped by the supplier you last bought it
          from, with what it cost you last time. Confirming an order writes it down and prints it —
          it does not receive stock and it pays nobody.
        </p>
      </div>

      {err && (
        <div className="po-band">
          <Refusal
            reason="No order could be drafted"
            detail={err.reason}
            hint={err.detail}
            action={<Button size="sm" onClick={() => void load()}>TRY AGAIN</Button>}
          />
        </div>
      )}

      {chain && chain.exists && !chain.ok && (
        <div className="po-band">
          <Verdict tone="red" title="The order log does not verify">
            The orders below are read from their own files, but the chain that witnesses them stops
            being evidence at line <b>{chain.lines}</b>. An order after that point cannot be proved
            to have been written by this counter.
            <br />
            <span className="mono">{chain.error}</span>
          </Verdict>
        </div>
      )}

      {/* THE THREE DRAFT FIGURES SHOW A DASH WHEN THE DRAFT COULD NOT BE READ,
          never a nought. A zero here would be this page claiming the shelf is
          fine on the strength of a request that failed — which is the same
          class of lie as printing ₹0.00 for a cost nobody recorded. Orders
          written comes from a different request and is shown whenever THAT one
          answered.

          AND A DASH WHEN NOTHING HAS A LEVEL, for the same reason one layer
          down. "Products to order: 0 — at or under the level you set" was a
          measurement against a level that does not exist: with `skus_with_a_
          level` at nought there is no comparison to have come out at nought,
          and a shopkeeper reading four zeros across the top of this screen was
          being told his shelves were fine by a page that had not looked at
          them. It is an abstention, so it is drawn as one — `unknown`, which
          is ink and not amber, because declining to answer is not a warning. */}
      <div className="po-band">
        <StatGrid>
          <Stat
            label="Suppliers to ring"
            value={loading ? <StatSkel w={54} /> : !body ? '—' : noLevels ? '—' : orderable.length}
            tone={!loading && body && noLevels ? 'unknown' : undefined}
            sub={loading ? 'reading the shelf'
              : !body ? 'not known — the draft could not be read'
                : noLevels ? 'nothing to ring about until a level is set'
                  : 'each has something under its level'}
          />
          <Stat
            label="Products to order"
            value={loading ? <StatSkel w={44} /> : !body ? '—' : noLevels ? '—' : body.lines_total}
            tone={!loading && body && noLevels ? 'unknown' : undefined}
            sub={loading ? 'reading the shelf'
              : !body ? 'not known — the draft could not be read'
                : noLevels ? 'no level is set, so no shelf can be called low'
                  : 'at or under the level you set'}
          />
          <Stat
            label="No supplier yet"
            value={loading ? <StatSkel w={44} /> : !body ? '—' : noLevels ? '—' : (orphan?.line_count ?? 0)}
            tone={!loading && body && (noLevels || (orphan?.line_count ?? 0) > 0) ? 'unknown' : undefined}
            sub={loading ? 'reading the purchase book'
              : !body ? 'not known — the draft could not be read'
                : noLevels ? 'nothing is being ordered, so nothing is missing a supplier'
                  : 'never bought through this counter'}
          />
          <Stat
            label="Orders written"
            value={loading ? <StatSkel w={44} /> : !orders ? '—' : orders.count}
            sub="none of them received or paid"
          />
        </StatGrid>
      </div>

      {/* THE STATE THIS SCREEN IS ACTUALLY IN, said once, in front of the
          zeros. Against the live shop every figure above is an abstention and
          the two panels below are empty; without this the screen reads as a
          shop with nothing to reorder rather than as a counter that has not
          been told what low means. */}
      {!loading && body && noLevels && (
        <div className="po-band">
          <Verdict tone="info" title="This screen cannot fill itself in yet">
            A reorder level is the number of packets below which you want to be told. Nothing has
            one — {body.skus_without_a_level ?? 0} product
            {(body.skus_without_a_level ?? 0) === 1 ? '' : 's'} in the catalogue, none with a
            level — so there is no line for a shelf figure to fall under and nothing here is
            being withheld. Set one on the Stock screen and this page fills itself in from the
            next bill onward. It is your judgement about your own shelf, and this counter will
            not propose one for you.
          </Verdict>
        </div>
      )}

      <div className="po-band">
        <Verdict tone="info" title="An order is not a delivery" icon={false}>
          Confirming writes the order down and gives you a page to print and a message to send.
          The shelf figure does not move. When the delivery arrives, count what is in the box and
          book it in on the Stock screen — some of it will be short, and that is the moment anyone
          finds out.
        </Verdict>
      </div>

      {/* ------------------------------------------------------- the orders -- */}

      {loading ? (
        /* AT THE SHAPE OF THE ORDER THAT IS COMING, not three grey sentences.
           `Table` in its loading state draws the real header — Product, On
           hand, Level, Order, Last cost, Line — with a bar in every cell, and
           the four money and count columns are right-aligned by the shared
           stylesheet, exactly where their figures land. Nothing moves when the
           draft arrives. */
        <Card
          className="po-band"
          title="What is running out"
          sub="reading the shelf and the purchase book"
          flush
        >
          <Table
            cols={LINE_COLS}
            rows={[] as poapi.PoLine[]}
            rowKey={(l) => l.sku_id}
            loading
            loadingRows={4}
            label="Drafting the order"
            maxHeight="none"
          />
        </Card>
      ) : err ? null : groups.length === 0 ? (
        <Card
          className="po-band"
          title="What is running out"
          sub={noLevels ? 'nothing has a level to run out against' : 'nothing is at its level'}
        >
          {/* TWO DIFFERENT EMPTIES, because they are two different facts and
              the old one used the same heading for both. "Nothing is under its
              reorder level" is true of a well-stocked shop and false of a shop
              where no level exists — there, nothing is under anything, because
              there is nothing to be under. */}
          <Empty
            icon={<IcoParcel size={22} />}
            title={noLevels ? 'No reorder level has been set' : 'Nothing is under its reorder level'}
            action={
              <Button variant="primary" onClick={() => { location.hash = '#/stock'; }}>
                SET A REORDER LEVEL
              </Button>
            }
          >
            {noLevels
              ? `${body?.skus_without_a_level ?? 0} product(s) are taught and priced and none has a `
                + 'level, so no shelf can be called low and no order can be drafted. A level is '
                + 'the number of packets below which you want to be told.'
              : `${body?.skus_with_a_level ?? 0} product(s) have a level set and none of them is `
                + 'at it. This screen fills itself in as the shelf empties.'}
          </Empty>
        </Card>
      ) : (
        orderable.map((g) => (
          <Card
            key={g.supplier_id ?? 'none'}
            className="po-band"
            title={g.supplier_name ?? g.supplier_id ?? 'Supplier'}
            sub={
              g.supplier_phone
                ? `${g.supplier_phone} · ${g.line_count} line(s), ${g.units_total} packet(s)`
                : `${g.line_count} line(s), ${g.units_total} packet(s)`
            }
            aside={
              <Button
                variant="primary"
                onClick={() => { setAsking(g); setPlaceErr(null); setNote(''); }}
              >
                CONFIRM THIS ORDER
              </Button>
            }
            flush
          >
            <Table
              cols={LINE_COLS}
              rows={g.lines}
              rowKey={(l) => l.sku_id}
              label={`What to order from ${g.supplier_name ?? 'this supplier'}`}
              maxHeight="none"
            />
            <div className="po-foot">
              <Expected group={g} />
              {!g.supplier_on_file && (
                <span className="po-note">
                  This supplier is no longer on file. The name is the one on the last purchase these
                  products came from, and the order can still be written.
                </span>
              )}
            </div>
          </Card>
        ))
      )}

      {/* --------------------------------------------- what cannot be ordered */}

      {orphan && orphan.lines.length > 0 && (
        <Card
          className="po-band"
          title="Running out, with nobody to order from"
          sub="never bought through this counter"
          flush
        >
          <p className="po-why po-intro">{orphan.why_not}</p>
          <Table
            cols={LINE_COLS}
            rows={orphan.lines}
            rowKey={(l) => l.sku_id}
            label="Products with no supplier"
            maxHeight="none"
          />
        </Card>
      )}

      {body && (body.at_level_nothing_to_order.length > 0
        || body.level_set_but_never_counted.length > 0
        || body.needs_recount.length > 0) && (
        <Card className="po-band" title="Left off this order, and why" tight>
          <div className="po-aside">
            {body.at_level_nothing_to_order.map((r) => (
              <Reason key={r.sku_id} name={r.name ?? r.sku_id}>
                Exactly at its level of {r.reorder_level}, so the shortfall is nought. Raise the
                level on the Stock screen if you want it ordered.
              </Reason>
            ))}
            {body.level_set_but_never_counted.map((r) => (
              <Reason key={r.sku_id} name={r.name ?? r.sku_id}>
                A level of {r.reorder_level} is set but this shelf has never been counted, so
                whether it is low cannot be said.
              </Reason>
            ))}
            {body.needs_recount.map((r) => (
              <Reason key={r.sku_id} name={r.name ?? r.sku_id}>
                The derived figure is {r.on_hand_units}, below zero — something has left the shelf
                that nobody recorded. Count it again before ordering against it.
              </Reason>
            ))}
          </div>
        </Card>
      )}

      {/* ------------------------------------------------ the orders written */}

      <Card
        className="po-band"
        title="Orders written"
        sub="never edited, never deleted, none of them received"
        flush
      >
        <Table
          cols={[
            {
              key: 'date',
              head: 'Date',
              cell: (r: poapi.PoRow) => (
                <>
                  <span className="po-day">{poapi.day(r.at)}</span>
                  <span className="po-time">{poapi.clock(r.at)}</span>
                </>
              ),
            },
            {
              key: 'supplier',
              head: 'Supplier',
              cell: (r: poapi.PoRow) => r.supplier_name ?? r.supplier_id,
            },
            { key: 'lines', head: 'Lines', num: true, drop: true, cell: (r: poapi.PoRow) => r.line_count },
            { key: 'units', head: 'Packets', num: true, cell: (r: poapi.PoRow) => r.units_total },
            {
              key: 'spend',
              head: 'Expected',
              num: true,
              cell: (r: poapi.PoRow) => (
                <>
                  <Money rupees={r.expected_rupees} />
                  {r.expected_is_partial && <span className="po-plus"> + unknown</span>}
                </>
              ),
            },
          ]}
          rows={orders?.orders ?? []}
          rowKey={(r) => r.po_id}
          loading={loading}
          onRowClick={(r) => void open(r.po_id)}
          empty={
            /* A DIFFERENT MARK FROM THE PANEL ABOVE IT. Two open trays stacked
               down an otherwise blank screen read as one component rendered
               twice; these are two unrelated facts and each gets its own. */
            <Empty icon={<IcoReceipt size={22} />} title="No order has been written yet">
              Confirming one above writes it here, on this counter&rsquo;s own hash-chained log,
              with a page you can print and a message you can send. Nothing on this list is ever
              edited or deleted{chain && chain.exists ? '' : ', and the log is not written until the first one'}.
            </Empty>
          }
          label="Orders this counter has written"
        />
      </Card>

      {/* ------------------------------------------------------- confirming -- */}

      <Modal
        open={asking !== null}
        onClose={() => setAsking(null)}
        title={`Order from ${asking?.supplier_name ?? asking?.supplier_id ?? ''}`}
        sub="The quantities are worked out from your reorder levels. They cannot be typed here."
        note={
          placing
            ? 'Writing it down…'
            : 'This writes the order and prints it. It receives no stock and pays nobody.'
        }
        foot={
          <>
            <Button onClick={() => setAsking(null)} disabled={placing}>CANCEL</Button>
            <Button variant="primary" onClick={() => void place()} disabled={placing}>
              {placing ? 'WRITING…' : 'WRITE THE ORDER'}
            </Button>
          </>
        }
      >
        {placeErr && (
          <div className="po-band">
            <Refusal reason={placeErr.reason} detail={placeErr.detail} />
          </div>
        )}
        {asking && (
          <>
            <div className="po-sum">
              <Fig label="Lines">{asking.line_count}</Fig>
              <Fig label="Packets">{asking.units_total}</Fig>
              <Fig label="Expected">
                <Money rupees={asking.expected_rupees} />
                {asking.expected_is_partial && <span className="po-plus"> + unknown</span>}
              </Fig>
            </div>
            <p className="po-why">{asking.expected_note}</p>
            <Field
              label="A note for the supplier"
              sub="Printed on the order and carried in the message. Optional."
            >
              <Input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Deliver Tuesday morning"
                maxLength={400}
              />
            </Field>
            <p className="po-why">
              The order is drafted again at the moment you press the button, so what is written is
              what the shelf needs now — not what this screen showed when it was opened.
            </p>
          </>
        )}
      </Modal>

      {/* ----------------------------------------------------- one written -- */}

      <Modal
        open={placed !== null}
        onClose={() => setPlaced(null)}
        size="wide"
        title={placed ? `Order ${placed.po_id}` : ''}
        sub={placed ? `${placed.supplier_name ?? placed.supplier_id} · ${placed.date}` : ''}
        note="No stock has been received and nothing has been paid."
        foot={
          <>
            <Button
              onClick={() => void poapi.copy(message).then((ok) =>
                toast(ok ? 'Message copied' : 'The browser would not give up the clipboard', {
                  tone: ok ? 'info' : 'amb',
                  note: ok
                    ? 'Paste it into WhatsApp.'
                    : 'Select the text below and copy it by hand — a page served over plain HTTP cannot reach the clipboard.',
                }))}
            >
              COPY THE MESSAGE
            </Button>
            {printUrl && (
              <a className="btn primary po-print" href={printUrl} target="_blank" rel="noreferrer">
                PRINT
              </a>
            )}
          </>
        }
      >
        {placed && (
          <>
            <div className="po-sum">
              <Fig label="Lines">{placed.line_count}</Fig>
              <Fig label="Packets">{placed.units_total}</Fig>
              <Fig label="Expected">
                <Money rupees={placed.expected_rupees} />
                {placed.expected_is_partial && <span className="po-plus"> + unknown</span>}
              </Fig>
            </div>
            <p className="po-why">{placed.expected_note}</p>
            <Table
              cols={LINE_COLS}
              rows={placed.lines}
              rowKey={(l) => l.sku_id}
              label="What was ordered"
              maxHeight="none"
            />
            {/* The message is SHOWN, not merely copied: a page served over plain
                HTTP to a phone has no clipboard, and a button that silently
                failed would leave a shopkeeper with nothing to send. */}
            <div className="po-msg">
              <span className="po-msg-l">The message</span>
              <pre>{message}</pre>
            </div>
          </>
        )}
      </Modal>
    </div>
  );
}
