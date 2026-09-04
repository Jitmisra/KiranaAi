import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import * as shelfapi from '../lib/shelfapi';
import { useCamera } from '../hooks/useCamera';
import {
  Button, Card, Checkbox, Empty, Field, Input, KV, Modal, Pill, Refusal,
  Segmented, Select, Skeleton, SkeletonRows, Stat, StatGrid, Verdict, toast,
} from '../components/ui';
import '../styles/shelf.css';

/**
 * Shelf — count what is facing out, with the camera.
 *
 * A COUNT IS ONLY WORTH ANYTHING IF THE SHOPKEEPER CAN CHECK IT AND FIX IT.
 * This screen used to be a camera and four numbers, which is a demo: the
 * numbers were right or they were not and there was no way to tell from the
 * outside, and no way to do anything about it if they were wrong. So the
 * picture, the list and the count are one object here — every region the
 * camera proposed is drawn on the frame AND listed beside it, the two are
 * linked by a region number that appears in both, hovering one lights the
 * other, and every row carries the two corrections a shopkeeper actually
 * needs: that is the wrong name, and that is not a product.
 *
 * THE FIVE THINGS THIS SCREEN IS HONEST ABOUT, because each is a place a
 * shelf-scanning demo normally lies:
 *
 *  1. A FACING IS NOT A UNIT. The camera sees the front row and nothing behind
 *     it. So the big number is "facings", the sentence beside it says "front
 *     row", and the comparison with the stock figure is printed as the
 *     server's own sentence. The ONE direction in which a photograph is
 *     decisive — more visible than the figure says the shop holds — is the
 *     only one that gets an emphasis.
 *  2. AN ABSTENTION IS ALWAYS VISIBLE AS ONE. Every region the camera could
 *     not name is amber, on the picture and in the list, with its crop and its
 *     nearest miss. Nothing is ever named as "the nearest thing", and nothing
 *     amber is ever quietly dropped — including the fragments the server flags
 *     as the same packet seen twice, which are folded under the packet they
 *     sit on and stay teachable.
 *  3. A NAME THE CAMERA FOUND AND A NAME A PERSON TYPED ARE DIFFERENT
 *     EVIDENCE. Corrections count as facings and are marked "by you", in the
 *     tile, on the row and on the picture, and are never folded into what the
 *     camera recognised.
 *  4. WHAT IS NOT THERE IS THE POINT. An empty facing is the thing a
 *     shopkeeper most needs to see, and the easiest thing on this screen to
 *     lie about — so "missing" says only that this frame does not show it, and
 *     the server's own sentence, ordered by how much evidence there really is.
 *  5. NOTHING HERE WRITES STOCK. There is no "save as count" button, on
 *     purpose. Counting the shelf is the shopkeeper's own act on the Stock
 *     screen.
 *
 * COLOUR. Green and amber are recognition state — a region the camera named, a
 * region it did not — and that is what they mark here. A region a PERSON named
 * is ink, because it is neither. The stock gap is a fact about a shop, not a
 * verdict, and is set in ink.
 *
 * NO MONEY. This screen renders no price, no total and no valuation.
 */

type View = 'live' | 'read';
type TeachMode = 'known' | 'new';
type Note = { reason: string; detail?: string };
/** Teaching a region the camera abstained on, or correcting one it named. */
type TeachKind = 'teach' | 'correct';

/* ------------------------------------------------------------- fragments -- */

/**
 * WHY A CONTROL IS DEAD, WHERE THE HAND IS.
 *
 * A disabled button on a counter is a question — "is it broken, or am I?" — and
 * this screen has two that go dead for two different reasons. The sentence is
 * tied to the button with `aria-describedby`, so it is not only for the eye.
 */
function WhyDead({ id, children }: { id: string; children: ReactNode }) {
  return <p className="sh-whydead" id={id}>{children}</p>;
}

/** The shape of the earlier-read rows that are on their way: a time, a line. */
function EarlierSkeleton() {
  return (
    <div className="sh-earlier-list" aria-hidden="true">
      {[0, 1, 2, 3].map((i) => (
        <div className="sh-earlier" key={i}>
          <Skeleton w={64} h={10} radius={999} />
          <span className="what">
            <Skeleton w="72%" h={11} radius={999} />
            <Skeleton w="44%" h={9} radius={999} />
          </span>
        </div>
      ))}
    </div>
  );
}

/** The gap sentence, with an emphasis only where a photograph is decisive. */
function Gap({ stock }: { stock: shelfapi.StockGap }) {
  return (
    <span className={`sh-gap${stock.shelf_exceeds_figure ? ' decisive' : ''}`} title={stock.derivation ?? undefined}>
      {stock.sentence}
    </span>
  );
}

/** The stock figure, or the absence of one. Never a zero standing in for both. */
function Figure({ stock }: { stock: shelfapi.StockGap }) {
  if (stock.on_hand_units === null || stock.on_hand_units === undefined) {
    return <span className="sh-absent">{stock.verdict === 'never_counted' ? 'not counted' : 'no figure'}</span>;
  }
  return <b className="tnum">{stock.on_hand_units}</b>;
}

/**
 * How this product's facings moved since the last read of THIS shelf.
 *
 * Absent rather than zero when there is nothing to compare against: "no
 * change" and "no earlier read" are different facts and a dash for both would
 * be the page inventing the first. Ink, not colour — fewer facings than last
 * time is not a failure, it is a shelf that has been selling.
 */
function Change({ f }: { f: shelfapi.Facing }) {
  if (f.change === null || f.change === undefined) {
    return <span className="sh-absent">{f.new_here ? 'new here' : 'no earlier read'}</span>;
  }
  if (f.change === 0) return <span className="sh-same">same as last read</span>;
  const down = f.change < 0;
  return (
    <span className={`sh-change${down ? ' down' : ' up'}`}>
      <b className="tnum">{down ? '−' : '+'}{Math.abs(f.change)}</b>
      <span className="l">since {f.previous_facings} last read</span>
    </span>
  );
}

function FacingRow({ f, onPoint, thumb }: { f: shelfapi.Facing; onPoint: (region: number | null) => void; thumb?: string | null }) {
  return (
    <div
      className="sh-facing"
      onMouseEnter={() => onPoint(f.regions[0] ?? null)}
      onMouseLeave={() => onPoint(null)}
    >
      <div className="sh-facing-top">
        <span className="sh-facing-id">
          <span className="sh-thumb">{thumb ? <img src={thumb} alt="" /> : <em>{initials(f.name)}</em>}</span>
          <span className="sh-name">{f.name}<span className="sh-sku">{f.sku_id}</span></span>
        </span>
        <span className="sh-count">
          <b className="tnum">{f.facings}</b>
          <span className="l">facing{f.facings === 1 ? '' : 's'}</span>
        </span>
      </div>
      <div className="sh-facing-mid">
        <span className="sh-how">
          {f.by_code > 0 && <Pill tone="code">{f.by_code} by code</Pill>}
          {f.by_appearance > 0 && <Pill tone="ok">{f.by_appearance} by sight</Pill>}
          {/* NEVER FOLDED INTO "by sight". A facing a person typed is a facing
              and is not something the camera recognised, and the row that
              claims a count has to say which it is. */}
          {f.by_hand > 0 && <Pill tone="off">{f.by_hand} by you</Pill>}
          <span className="sh-regions">region{f.regions.length === 1 ? '' : 's'} {f.regions.join(', ')}</span>
        </span>
        <span className="sh-fig">
          <span className="l">stock figure</span>
          <Figure stock={f.stock} />
        </span>
      </div>
      <div className="sh-facing-mid">
        <Change f={f} />
      </div>
      <Gap stock={f.stock} />
      {f.appearance_said.length > 0 && (
        <span className="sh-note">
          The code on {f.appearance_said.length === 1 ? 'one of these' : `${f.appearance_said.length} of these`} named
          it; by sight it looked like {f.appearance_said.join(', ')}. The code wins: it is a measurement.
        </span>
      )}
    </div>
  );
}

/** The word for a state, as the shopkeeper reads it on the row and the box. */
const STATE_WORD: Record<shelfapi.RegionState, string> = {
  named: 'named by the camera',
  by_hand: 'named by you',
  unnamed: 'not named',
  rejected: 'not a product',
};

/**
 * ONE ROW PER REGION, whatever state it is in.
 *
 * There used to be two lists — facings, and things it could not name — and a
 * region that moved between them looked like two different objects. A shelf
 * read is a list of positions on a shelf, each of which is named or is not, so
 * this is one list in reading order, and the state is a property of the row
 * rather than which card it landed in.
 */
function RegionRow({ r, focus, onPoint, onTeach, onCorrect, onReject, busy }: {
  r: shelfapi.Region & Partial<shelfapi.Unnamed> & { crop_png_b64?: string | null };
  focus: number | null;
  onPoint: (region: number | null) => void;
  onTeach: (region: number) => void;
  onCorrect: (region: number) => void;
  onReject: (region: number, undo: boolean) => void;
  busy: number | null;
}) {
  const src = shelfapi.pngUrl(r.crop_png_b64 ?? null);
  const seen = r.name_seen ?? null;
  const working = busy === r.region;
  return (
    <div
      className={`sh-region ${r.state}${focus === r.region ? ' on' : ''}`}
      onMouseEnter={() => onPoint(r.region)}
      onMouseLeave={() => onPoint(null)}
      id={`sh-region-${r.region}`}
    >
      <span className={`sh-thumb ${r.state}`}>
        {src ? <img src={src} alt={`region ${r.region}`} /> : <span className="sh-nothumb">{r.region}</span>}
        <span className="sh-badge">{r.region}</span>
      </span>

      <span className="sh-what">
        <span className="nm">
          {r.name ?? seen ?? `Region ${r.region}`}
          {r.sku_id && <span className="sh-sku">{r.sku_id}</span>}
        </span>
        <span className="rs">
          {/* RECOGNISED, AND STILL NOT COUNTED. On a frame the counter would
              not count, a region it named is amber like every other, because
              no facing was derived from it — but saying only "not named" of a
              packet it had just named would be the screen calling itself
              blind. So the state says both. */}
          <span className={`sh-state ${r.state}`}>
            {seen && r.state === 'unnamed' ? `recognised as ${seen} — not counted` : STATE_WORD[r.state]}
          </span>
          {r.state === 'named' && r.top1 !== null && r.top1 !== undefined && (
            <> · {r.top1.toFixed(2)} cosine</>
          )}
          {r.state === 'unnamed' && r.top1_sku && r.top1 !== null && r.top1 !== undefined && (
            <> · nearest {r.top1_sku} at {r.top1.toFixed(2)}, under the bar</>
          )}
          {r.code && <> · code {r.code}</>}
        </span>
        {/* The server's own sentence, never a second one composed here. */}
        {r.same_packet_as && (
          <span className="dt sh-samepacket">{r.same_packet_as.detail}</span>
        )}
        {!r.same_packet_as && r.detail && <span className="dt">{r.detail}</span>}
      </span>

      <span className="sh-acts">
        {/* A region the counter recognised needs no Teach button: the shop has
            already taught this product, and offering to teach it again reads
            as the counter not knowing what it just said it saw. */}
        {r.state === 'unnamed' && !seen && (
          <Button size="sm" disabled={working} onClick={() => onTeach(r.region)}>Teach</Button>
        )}
        {r.state === 'named' && (
          <Button size="sm" disabled={working} onClick={() => onCorrect(r.region)}>Wrong name</Button>
        )}
        {r.state === 'rejected' ? (
          <Button size="sm" variant="quiet" loading={working} onClick={() => onReject(r.region, true)}>
            Put it back
          </Button>
        ) : (
          <Button size="sm" variant="quiet" loading={working} onClick={() => onReject(r.region, false)}>
            Not a product
          </Button>
        )}
      </span>
    </div>
  );
}

/** A taught product this frame does not show. The server's sentence, verbatim. */
function MissingRow({ m, thumb }: { m: shelfapi.Missing; thumb?: string | null }) {
  return (
    <div className={`sh-missing ${m.verdict}`}>
      <span className="sh-thumb gone">{thumb ? <img src={thumb} alt="" /> : <em>{initials(m.name)}</em>}</span>
      <span className="sh-what">
        <span className="nm">
          {m.name}<span className="sh-sku">{m.sku_id}</span>
          {m.verdict === 'was_here' && <Pill tone="amb">was here</Pill>}
          {!m.taught_by_sight && <Pill tone="off">code only</Pill>}
        </span>
        <span className="rs">{m.sentence}</span>
      </span>
      {m.previous_facings ? (
        <span className="sh-was"><b className="tnum">{m.previous_facings}</b><span className="l">last read</span></span>
      ) : null}
    </div>
  );
}

function EarlierRow({ r }: { r: shelfapi.EarlierRead }) {
  const line = r.facings.map((f) => `${f.sku_id} ×${f.facings}`).join(', ');
  return (
    <div className="sh-earlier">
      <span className="tm">{shelfapi.when(r.at)}</span>
      <span className="what">
        <span className="nm">
          {r.label ? <b className="sh-lbl">{r.label}</b> : null}
          {r.regions_seen} seen · {r.named} named · {r.unnamed} not named
          {r.corrected && <Pill tone="off">{r.corrections} correction{r.corrections === 1 ? '' : 's'}</Pill>}
        </span>
        <span className="rs">{line || (r.regions_seen === 0 ? 'an empty shelf' : r.counted ? 'nothing named' : 'not counted — not a shelf')}</span>
      </span>
    </div>
  );
}

/**
 * The frame the server drew, with a hit target over every box.
 *
 * THE PICTURE IS STILL THE SERVER'S. Nothing is drawn here — the boxes, their
 * colours and their labels are in the PNG, because the browser must not be the
 * author of what a shopkeeper reads as a measurement. What the browser adds is
 * a transparent button per region, positioned from the same `box` the server
 * drew from, so the picture can be pointed at.
 *
 * The wrapper carries the PHOTOGRAPH's aspect ratio, not the stage's, so a
 * percentage of the wrapper is a percentage of the frame exactly. Positioning
 * against a `object-fit: contain` letterbox instead would need the fitted size
 * recomputed on every resize, and would be a pixel or two out forever.
 */
function Frame({ read, src, focus, onPoint, onPick }: {
  read: shelfapi.ShelfRead;
  src: string;
  focus: number | null;
  onPoint: (region: number | null) => void;
  onPick: (region: number) => void;
}) {
  const [fw, fh] = read.frame_px;
  return (
    <div className="sh-frame" style={{ aspectRatio: `${fw} / ${fh}` }}>
      <img src={src} alt="the shelf, with every region the camera found boxed on it" />
      {read.regions.map((r, i) => (
        <button
          key={r.region}
          type="button"
          className={`sh-hit ${r.state}${focus === r.region ? ' on' : ''}`}
          style={{
            // The lock-on runs in reading order, 70 ms apart — see .sh-hit in
            // shelf.css. Index only; the boxes themselves are the server's.
            ['--i' as string]: i,
            left: `${(r.box[0] / fw) * 100}%`,
            top: `${(r.box[1] / fh) * 100}%`,
            width: `${(r.box[2] / fw) * 100}%`,
            height: `${(r.box[3] / fh) * 100}%`,
          }}
          title={`Region ${r.region} — ${r.name ?? STATE_WORD[r.state]}`}
          aria-label={`Region ${r.region}, ${r.name ?? STATE_WORD[r.state]}`}
          onMouseEnter={() => onPoint(r.region)}
          onMouseLeave={() => onPoint(null)}
          onFocus={() => onPoint(r.region)}
          onBlur={() => onPoint(null)}
          onClick={() => onPick(r.region)}
        />
      ))}
    </div>
  );
}

/* ------------------------------------------------------------ the screen -- */

/* ------------------------------------------------------- the shelf map -- */

type Look = { name: string; thumb: string | null };
/** rack key -> the products the shopkeeper's own tags put on that rack. */
type Plan = Map<string, string[]>;

/** "Rack 1 — Staples" and "rack 1 staples" are the same shelf. */
const rackKey = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '');

/**
 * sku_id -> {name, thumb} off `/shop`, and the PLAN off `/categories/products`:
 * a tag like "rack 1 staples" is the shopkeeper saying where a product lives.
 * Two reads, one hook, because the map needs both to say what it says.
 */
function useCatalogueLook(): { look: Map<string, Look>; plan: Plan } {
  const [look, setLook] = useState<Map<string, Look>>(() => new Map());
  const [plan, setPlan] = useState<Plan>(() => new Map());
  useEffect(() => {
    let on = true;
    const get = async (u: string) => {
      const r = await fetch(u, { cache: 'no-store', credentials: 'same-origin' });
      return r.ok ? r.json() : null;
    };
    (async () => {
      try {
        const [shop, cats] = await Promise.all([get('/shop'), get('/categories/products')]);
        const m = new Map<string, Look>();
        for (const k of ((shop as { skus?: Array<{ sku_id: string; name?: string; thumb_png?: string | null }> } | null)?.skus ?? [])) {
          m.set(k.sku_id, { name: k.name ?? k.sku_id, thumb: k.thumb_png ? `data:image/png;base64,${k.thumb_png}` : null });
        }
        const pl: Plan = new Map();
        for (const pr of ((cats as { products?: Array<{ sku_id: string; tags?: string[] | string }> } | null)?.products ?? [])) {
          const tags = Array.isArray(pr.tags) ? pr.tags : [];
          for (const tg of tags) {
            // Only tags that name a place. "daily need" is a fact about the
            // product; "rack 1 staples" and "cold rack" are facts about the shop.
            if (!/\b(rack|shelf|counter|aisle|fridge|freezer|chiller|cold|front|back)\b/i.test(tg)) continue;
            const k = rackKey(tg);
            if (!pl.has(k)) pl.set(k, []);
            pl.get(k)!.push(pr.sku_id);
          }
        }
        if (on) { setLook(m); setPlan(pl); }
      } catch { /* the rows and the map draw initials instead; nothing is invented */ }
    })();
    return () => { on = false; };
  }, []);
  return { look, plan };
}

const initials = (n: string) => n.replace(/\(.*?\)/g, '').trim().split(/\s+/).slice(0, 2).map((w) => w[0] ?? '').join('').toUpperCase();

/** A product name without its Devanagari gloss, for a tile. */
const shortName = (n: string) => n.replace(/\s*\(.*?\)\s*$/, '').trim();

const MAX_SLOTS = 14;

/** The tag as a shelf name: "rack 1 staples" -> "Rack 1 — Staples". */
function planLabel(key: string): string | null {
  const cap = (w: string) => (w ? w.charAt(0).toUpperCase() + w.slice(1) : '');
  const m = key.match(/^(rack|shelf|aisle)(\d+)(.*)$/);
  if (m) {
    const kind = m[1] ?? '', n = m[2] ?? '', rest = m[3] ?? '';
    return `${cap(kind)} ${n}${rest ? ` — ${cap(rest)}` : ''}`;
  }
  return key ? cap(key) : null;
}

/**
 * Does a tag name this shelf? "cold rack" is the tag; "Cold rack — Dairy &
 * Cold drinks" is the label the shopkeeper typed on the read. Same shelf. So a
 * plan key matches a label when the label's key begins with it.
 */
const tagNamesShelf = (planKey: string, labelKey: string) => {
  if (labelKey === planKey || labelKey.startsWith(planKey)) return true;
  // "rack 2 snacks" and "Rack 2 — Biscuits & Namkeen" are one rack: the NUMBER
  // is the identity and the words after it are a description that two people
  // (or one person on two days) will not write the same way twice.
  const a = planKey.match(/^(rack|shelf|aisle)(\d+)/), b = labelKey.match(/^(rack|shelf|aisle)(\d+)/);
  return !!(a && b && a[1] === b[1] && a[2] === b[2]);
};

/**
 * THE SHOP'S SHELVES, AS LAST READ — a picture of the shop, built from the
 * counter's own chain.
 *
 * Every read on the chain names a shelf and lists what was facing on it. Take
 * the latest read of each named shelf and draw it as a rack: one slot per
 * facing, wearing the product's own photograph, in the order the camera found
 * them. What comes out is a planogram nobody drew — the shop as the camera
 * last saw it, rack by rack.
 *
 * WHAT IT IS NOT. It is not stock. A slot is a facing the camera counted on
 * one read; the packets behind it are not in this picture and neither is the
 * back room. It says so on the band. A rack the camera last found empty is
 * drawn empty, dashed — an empty facing is the thing worth walking to the
 * shelf for, and a list of what IS there cannot show one.
 */
function ShelfMap({ reads, look, plan }: { reads: shelfapi.EarlierRead[]; look: Map<string, Look>; plan: Plan }) {
  const racks = new Map<string, shelfapi.EarlierRead | null>();
  for (const r of reads) {
    if (!r.label || !r.counted) continue;
    if (!racks.has(r.label)) racks.set(r.label, r);   // newest first on the chain
  }
  // A rack the tags describe and the camera has never read is still a rack.
  const readKeys = [...racks.keys()].map(rackKey);
  for (const k of plan.keys()) {
    if (!readKeys.some((lk) => tagNamesShelf(k, lk))) racks.set(planLabel(k) ?? k, null);
  }
  if (racks.size === 0) return null;
  const order = [...racks.entries()].sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }));
  const planned = (label: string) => {
    const lk = rackKey(label);
    const out: string[] = [];
    for (const [k, skus] of plan) if (tagNamesShelf(k, lk)) for (const s of skus) if (!out.includes(s)) out.push(s);
    return out;
  };
  const slotsOf = (r: shelfapi.EarlierRead) => {
    const out: Array<{ sku: string; f: shelfapi.EarlierRead['facings'][number] }> = [];
    for (const f of r.facings) for (let i = 0; i < f.facings; i++) out.push({ sku: f.sku_id, f });
    return out;
  };
  const totalFacings = order.reduce((n, [, r]) => n + (r ? r.facings.reduce((a, f) => a + f.facings, 0) : 0), 0);
  const totalPlanned = order.reduce((n, [label]) => n + planned(label).length, 0);

  return (
    <section className="sh-map" aria-label="The shop's shelves">
      <div className="sh-map-head">
        <span className="sh-map-kicker">THE SHOP — WHAT IS MEANT TO BE THERE, AND WHAT THE CAMERA SAW</span>
        <h2>{order.length} shelves · <span className="tnum">{totalFacings}</span> facing{totalFacings === 1 ? '' : 's'} seen · <span className="tnum">{totalPlanned}</span> placed by you</h2>
        <p>
          A lit slot is a facing the camera counted on the newest read of that shelf. A faded slot is a
          product your own tags put on that shelf that the camera has not seen there — worth a look, not
          yet an empty facing. Never the stock behind either.
        </p>
        <div className="sh-map-legend">
          <span><i className="seen" />seen by the camera</span>
          <span><i className="ghost" />placed by you, not seen</span>
          <span><i className="none" />read empty</span>
        </div>
      </div>
      <div className="sh-racks">
        {order.map(([label, r]) => {
          const slots = r ? slotsOf(r) : [];
          const shown = slots.slice(0, MAX_SLOTS);
          const more = slots.length - shown.length;
          const seenSkus = new Set(slots.map((x) => x.sku));
          const ghosts = planned(label).filter((sku) => !seenSkus.has(sku)).slice(0, Math.max(0, MAX_SLOTS + 6 - shown.length));
          const readEmpty = r !== null && (r.regions_seen === 0 || slots.length === 0);
          const nothing = !readEmpty && shown.length === 0 && ghosts.length === 0;
          return (
            <div className={`sh-rack${readEmpty && ghosts.length === 0 ? ' empty' : ''}`} key={label}>
              <div className="sh-rack-h">
                <b>{label}</b>
                <span>
                  {r ? shelfapi.when(r.at) : <i className="amb">never read</i>}
                  {r && r.unnamed > 0 && <> · <i className="amb">{r.unnamed} not named</i></>}
                  {r?.corrected && <> · {r.corrections} correction{r.corrections === 1 ? '' : 's'}</>}
                  {ghosts.length > 0 && <> · {ghosts.length} placed, not seen</>}
                </span>
              </div>
              <div className="sh-rack-row">
                {shown.map((sl, i) => {
                  const k = look.get(sl.sku);
                  const nm = k?.name ?? sl.sku;
                  return (
                    <span className="sh-slot" key={`${sl.sku}-${i}`}
                          title={`${shortName(nm)} — ${sl.f.facings} facing${sl.f.facings === 1 ? '' : 's'}${sl.f.on_hand_units !== null ? ` · stock figure ${sl.f.on_hand_units}` : ''}`}
                          style={{ ['--i' as string]: i }}>
                      {k?.thumb ? <img src={k.thumb} alt="" /> : <em>{initials(nm)}</em>}
                    </span>
                  );
                })}
                {more > 0 && <span className="sh-slot more" style={{ ['--i' as string]: MAX_SLOTS }}>+{more}</span>}
                {readEmpty && ghosts.length === 0 && (
                  <span className="sh-slot none">
                    {r!.regions_seen === 0 ? 'empty when last read' : `${r!.regions_seen} seen, none named`}
                  </span>
                )}
                {ghosts.map((sku, i) => {
                  const k = look.get(sku);
                  const nm = k?.name ?? sku;
                  return (
                    <span className="sh-slot ghost" key={`g-${sku}`} title={`${shortName(nm)} — placed here by your tags, not seen by the camera`}
                          style={{ ['--i' as string]: shown.length + i }}>
                      {k?.thumb ? <img src={k.thumb} alt="" /> : <em>{initials(nm)}</em>}
                    </span>
                  );
                })}
                {nothing && <span className="sh-slot none">nothing here yet</span>}
              </div>
              <div className="sh-rack-lip" aria-hidden="true" />
            </div>
          );
        })}
      </div>
    </section>
  );
}

/**
 * THE HEADLINE OF A READ, before the tables: one big number and one bar.
 *
 * The facings total is the figure a shopkeeper walked over for. The bar under
 * it is the read's own honesty — how much of what the camera saw it could
 * name, how much a person named, how much it would not guess at, and what was
 * struck out. Amber is never hidden inside green.
 */
function ShelfHero({ read }: { read: shelfapi.ShelfRead }) {
  const c = read.counts;
  const facings = read.facings.reduce((a, f) => a + f.facings, 0);
  const seen = Math.max(1, c.regions_seen);
  const seg = (n: number) => `${(n / seen) * 100}%`;
  const delta = read.facings.reduce((a, f) => a + (f.change ?? 0), 0);
  const comparable = !!read.previous?.same_shelf && read.facings.some((f) => f.change !== null);
  return (
    <div className="sh-hero">
      <div className="sh-hero-n">
        <span className="big tnum">{facings}</span>
        <span className="l">facing{facings === 1 ? '' : 's'} on {read.label ?? 'this shelf'}</span>
        {comparable && (
          <span className={`sh-hero-delta${delta > 0 ? ' up' : delta < 0 ? ' down' : ''}`}>
            {delta === 0 ? 'same as the last read' : `${delta > 0 ? '+' : '−'}${Math.abs(delta)} since the last read`}
          </span>
        )}
      </div>
      <div className="sh-hero-bar">
        <div className="sh-seg" aria-label={`${c.regions_seen} regions: ${c.named} named, ${c.by_hand} by you, ${c.unnamed} not named, ${c.rejected} struck out`}>
          {c.named > 0 && <i className="named" style={{ width: seg(c.named) }} />}
          {c.by_hand > 0 && <i className="by_hand" style={{ width: seg(c.by_hand) }} />}
          {c.unnamed > 0 && <i className="unnamed" style={{ width: seg(c.unnamed) }} />}
          {c.rejected > 0 && <i className="rejected" style={{ width: seg(c.rejected) }} />}
        </div>
        <div className="sh-seg-legend">
          <span><b className="named" />{c.named} named by the camera</span>
          {c.by_hand > 0 && <span><b className="by_hand" />{c.by_hand} by you</span>}
          <span><b className="unnamed" />{c.unnamed} not named</span>
          {c.rejected > 0 && <span><b className="rejected" />{c.rejected} struck out</span>}
          <span className="ms">{c.regions_seen} regions · <span className="tnum">{Math.round(read.elapsed_ms)}</span> ms</span>
        </div>
      </div>
    </div>
  );
}

export default function Shelf() {
  const { look, plan } = useCatalogueLook();
  const cam = useCamera();
  const fileRef = useRef<HTMLInputElement>(null);

  const [about, setAbout] = useState<shelfapi.ShelfDescribe | null>(null);
  const [aboutRefusal, setAboutRefusal] = useState<Note | null>(null);
  const [earlier, setEarlier] = useState<shelfapi.EarlierRead[] | null>(null);
  /** Why the chain could not be read. An empty list and an unreadable one are
      not the same thing, and this screen used to print the first for both. */
  const [earlierRefusal, setEarlierRefusal] = useState<Note | null>(null);

  const [counting, setCounting] = useState(false);
  const [read, setRead] = useState<shelfapi.ShelfRead | null>(null);
  const [refusal, setRefusal] = useState<Note | null>(null);
  const [view, setView] = useState<View>('live');
  const [label, setLabel] = useState('');
  /** Which region the hand is on. Lights the row and its box together. */
  const [focus, setFocus] = useState<number | null>(null);
  /** Which region a correction is in flight for, so its buttons say so. */
  const [busy, setBusy] = useState<number | null>(null);

  const [teaching, setTeaching] = useState<{ region: number; kind: TeachKind } | null>(null);
  const [products, setProducts] = useState<shelfapi.Product[] | null>(null);
  /** The picture, big. On a phone the card is 350 px wide and the boxes on a
      shelf of a dozen packets are then smaller than a fingernail. */
  const [big, setBig] = useState(false);

  const load = useCallback(async () => {
    const [d, c] = await Promise.all([shelfapi.describe(), shelfapi.counts(12)]);
    if (d.ok) { setAbout(d); setAboutRefusal(null); } else setAboutRefusal(d);
    // A REFUSAL IS NOT AN EMPTY LIST. `setEarlier(c.ok ? c.reads : [])` turned
    // an unreadable chain into the sentence "No shelf has been read on this
    // counter yet" — this page inventing a fact about the shop out of its own
    // failed request. The server's words go on the screen instead.
    if (c.ok) { setEarlier(c.reads); setEarlierRefusal(null); }
    else { setEarlier(null); setEarlierRefusal(c); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const refreshEarlier = useCallback(() => {
    void shelfapi.counts(12).then((c) => {
      if (c.ok) { setEarlier(c.reads); setEarlierRefusal(null); } else setEarlierRefusal(c);
    });
  }, []);

  /**
   * THE WHOLE FRAME, ONCE. There is no counter area to draw here: the point of
   * this gesture is that the operator has not told the counter where to look,
   * and a shelf fills the view anyway. So the whole frame is uploaded and the
   * page says so.
   */
  const countBlob = useCallback(async (blob: Blob) => {
    setCounting(true);
    setRefusal(null);
    try {
      const r = await shelfapi.count(blob, { label });
      if (!r.ok) { setRefusal(r); return; }
      setRead(r);
      setFocus(null);
      setView('read');
      refreshEarlier();
      if (r.empty_shelf) toast('Nothing on this shelf that looks like a product', { tone: 'info' });
    } finally { setCounting(false); }
  }, [label, refreshEarlier]);

  const countCamera = useCallback(async () => {
    if (!cam.running || counting) return;
    const blob = await cam.capture({ x: 0, y: 0, w: cam.frame.w, h: cam.frame.h }, 0.92);
    if (!blob) { setRefusal({ reason: 'the camera gave no frame', detail: 'Press START again.' }); return; }
    await countBlob(blob);
  }, [cam, counting, countBlob]);

  const countFile = useCallback(async (file: File | undefined) => {
    if (!file) return;
    await countBlob(file);
    if (fileRef.current) fileRef.current.value = '';
  }, [countBlob]);

  const openTeach = useCallback(async (region: number, kind: TeachKind) => {
    setTeaching({ region, kind });
    if (products === null) {
      const p = await shelfapi.products();
      setProducts(p.ok ? p.skus : []);
    }
  }, [products]);

  /**
   * THE SERVER'S NEW READING REPLACES THE OLD ONE, WHOLE.
   *
   * The page used to strike the taught region off its own `unnamed` list and
   * leave every other figure alone — so the facing count stayed one short of
   * what the counter now knew, and that shortfall was a number the browser had
   * authored by subtraction. Every correction now comes back with the reading
   * recomputed where the first one was.
   */
  const onTaught = useCallback((res: shelfapi.TeachResult) => {
    setTeaching(null);
    setRead(res.read);
    setProducts(null);
    toast(
      res.was
        ? `Region ${res.region} is ${res.sku_id}, not ${res.was}`
        : res.how === 'view_added' ? `Another view of ${res.sku_id} stored` : `${res.sku_id} taught`,
      { tone: 'ok', note: res.detail },
    );
    refreshEarlier();
    void load();
  }, [load, refreshEarlier]);

  const onReject = useCallback(async (region: number, undo: boolean) => {
    if (!read) return;
    setBusy(region);
    try {
      const r = await shelfapi.reject(read.shelf_id, region, undo);
      if (!r.ok) { setRefusal(r); return; }
      setRead(r.read);
      refreshEarlier();
      toast(undo ? `Region ${region} is back` : `Region ${region} struck out`,
        { tone: undo ? 'ok' : 'info', note: r.detail });
    } finally { setBusy(null); }
  }, [read, refreshEarlier]);

  const annotated = shelfapi.pngUrl(read?.annotated_png_b64);
  const showRead = view === 'read' && !!annotated;
  const nothingTaught = about !== null && about.taught.total === 0;
  /** The camera gate is showing: no live video, no annotated frame. */
  const gated = !cam.running && !showRead;

  const abstained = !!read && read.counted === false && !!read.abstained;

  /**
   * EVERY REGION, IN READING ORDER, with what the server said about it.
   *
   * `regions` carries the state and the box; `unnamed` and `rejected` carry the
   * crop, the nearest miss and the sentence. They are joined by region number
   * rather than rendered from two lists, so the picture and the list can never
   * disagree about how many regions there were.
   */
  const rows = useMemo(() => {
    if (!read) return [];
    const extra = new Map<number, shelfapi.Unnamed | shelfapi.Rejected>();
    for (const u of read.unnamed) extra.set(u.region, u);
    for (const x of read.rejected) extra.set(x.region, x);
    return read.regions.map((r) => ({ ...(extra.get(r.region) ?? {}), ...r }));
  }, [read]);

  /** Regions the server flagged as the same packet as one already counted. */
  const samePacket = useMemo(
    () => new Set(read?.unnamed.filter((u) => u.same_packet_as).map((u) => u.region) ?? []),
    [read],
  );
  const [showSame, setShowSame] = useState(false);
  const mainRows = useMemo(
    () => rows.filter((r) => !samePacket.has(r.region)),
    [rows, samePacket],
  );
  const sameRows = useMemo(
    () => rows.filter((r) => samePacket.has(r.region)),
    [rows, samePacket],
  );

  /** Distinct products recognised on a frame the counter would not count. */
  const recognisedProducts = useMemo(() => {
    const names: string[] = [];
    for (const u of read?.unnamed ?? []) {
      const n = (u.name_seen ?? '').trim();
      if (n && !names.includes(n)) names.push(n);
    }
    return names;
  }, [read]);

  /* WHY THE TWO COUNT BUTTONS ARE DEAD. Two different reasons, and a shopkeeper
     standing in front of a dead button cannot tell which without being told. */
  const noPhotoWhy = nothingTaught
    ? 'Nothing has been taught yet, so there is nothing to name a region against. Teach a product on the Products screen first.'
    : null;
  const countWhy = noPhotoWhy
    ?? (!cam.running ? 'The camera is off. Start it above, or count from a photo instead.' : null);

  return (
    <div className="page shelf">
      <div className="page-head">
        <h1>Shelf</h1>
        <p>
          Point the camera at a shelf and press once. Every product-shaped region is found and named against
          what this shop has taught, and the count is of <b>facings</b> — what is visible in the front row.
          This counts the shelf face, not the stock. Anything it names wrong, you can fix here, and the fix
          teaches it.
        </p>
      </div>

      {aboutRefusal && (
        <div className="sh-band">
          <Refusal reason={aboutRefusal.reason} detail={aboutRefusal.detail} action={<Button size="sm" onClick={() => void load()}>Try again</Button>} />
        </div>
      )}

      {nothingTaught && (
        <div className="sh-band">
          <Empty
            title="Nothing has been taught yet"
            action={<a className="btn primary sh-linkbtn" href="#/products">TEACH A PRODUCT</a>}
          >
            A shelf can only be counted against products this shop knows, and this shop knows none. So
            the camera would find every packet on the shelf and be able to name none of them. Teach one
            on the Products screen first — <b>by photograph</b>, because a product taught from its
            printed code alone has no appearance stored and is counted here only when its code happens
            to face the camera.
          </Empty>
        </div>
      )}

      {earlier !== null && earlier.length > 0 && <ShelfMap reads={earlier} look={look} plan={plan} />}

      <div className="grid sh-grid">
        {/* ------------------------------------------------ the shelf -- */}
        <Card
          title="The shelf"
          sub="the whole frame is uploaded — there is no area to draw"
          aside={read && annotated ? (
            <span className="sh-stagetools">
              <Segmented<View>
                size="sm"
                value={view}
                onChange={setView}
                options={[{ value: 'live', label: 'Camera' }, { value: 'read', label: 'Last read' }]}
              />
              <Button size="sm" variant="quiet" onClick={() => { setView('read'); setBig(true); }}>
                Bigger
              </Button>
            </span>
          ) : undefined}
        >
          {/* `sh-gated` releases the 16:9 box while the gate is in it. At 390 px
              the ratio made the stage 178 px tall against a gate 269 px tall,
              and `overflow:hidden` on `.stage` cut START CAMERA and USE A PHOTO
              clean off the bottom — the only way to start the camera, invisible
              on the one device this screen is for. The stage bar sat on top of
              the sentence that was left. */}
          <div className={gated ? 'stage sh-stage sh-gated' : 'stage sh-stage'}>
            <video
              ref={cam.videoRef}
              playsInline
              muted
              style={{ display: cam.running && !showRead ? 'block' : 'none' }}
            />
            {showRead && read && annotated && (
              <Frame
                read={read}
                src={annotated}
                focus={focus}
                onPoint={setFocus}
                onPick={(n) => {
                  setFocus(n);
                  document.getElementById(`sh-region-${n}`)
                    ?.scrollIntoView({ block: 'center', behavior: 'smooth' });
                }}
              />
            )}

            {!cam.running && !showRead && (
              <div className="camgate">
                <h3>{cam.error ? 'The camera did not start' : 'Start the camera, or use a photo'}</h3>
                <p>
                  Nothing is uploaded until you press COUNT. A shelf photo from the phone's gallery works the same way.
                </p>
                <div className="btn-row" style={{ justifyContent: 'center' }}>
                  <Button variant="primary" onClick={() => void cam.start()}>Start camera</Button>
                  <Button variant="ghost" className="sh-ghost" onClick={() => fileRef.current?.click()}>Use a photo</Button>
                </div>
                {cam.error && <p className="sh-camerr">{cam.error}</p>}
              </div>
            )}

            <div className="stage-bar">
              {read && showRead ? (
                <>
                  <span className="mono">{read.frame_px[0]}×{read.frame_px[1]} px</span>
                  <span className="mono">{read.elapsed_ms} ms</span>
                  <span>{read.counts.regions_seen} region{read.counts.regions_seen === 1 ? '' : 's'} · front row only</span>
                  {read.label && <span className="sh-stagelbl">{read.label}</span>}
                </>
              ) : cam.running ? (
                <>
                  <span className="mono">{cam.frame.w}×{cam.frame.h} px</span>
                  <span>live · nothing uploaded until you press COUNT</span>
                </>
              ) : (
                <span>camera off</span>
              )}
            </div>
          </div>

          {/* WHICH SHELF THIS IS. Not decoration: it is the only thing that
              makes the comparison with the last read a comparison rather than
              two different aisles subtracted from each other. */}
          <div className="sh-label">
            <Field
              label="Which shelf is this?"
              sub="optional — but the comparison with the last count is only a comparison if it is of the same shelf"
              htmlFor="sh-label-input"
            >
              <Input
                id="sh-label-input"
                value={label}
                list={about && about.labels.length > 0 ? 'sh-label-list' : undefined}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="Aisle 2, top"
                maxLength={60}
              />
              {about && about.labels.length > 0 && (
                <datalist id="sh-label-list">
                  {about.labels.map((l) => <option key={l} value={l} />)}
                </datalist>
              )}
            </Field>
          </div>

          <div className="sh-actions">
            <Button
              variant="primary"
              size="lg"
              loading={counting}
              disabled={!cam.running || nothingTaught}
              aria-describedby={countWhy ? 'sh-why-count' : undefined}
              onClick={() => void countCamera()}
            >
              Count the shelf
            </Button>
            <Button
              size="lg"
              onClick={() => fileRef.current?.click()}
              loading={counting}
              disabled={nothingTaught}
              aria-describedby={noPhotoWhy ? 'sh-why-photo' : undefined}
            >
              Use a photo
            </Button>
            {cam.running && (
              <Button variant="quiet" size="lg" onClick={cam.stop}>Stop camera</Button>
            )}
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              hidden
              onChange={(e) => void countFile(e.target.files?.[0])}
            />
          </div>

          {/* A DISABLED CONTROL SAYS WHY. Both of these go dead, for two
              different reasons, and neither used to account for itself. */}
          {countWhy && <WhyDead id="sh-why-count">{countWhy}</WhyDead>}
          {noPhotoWhy && <WhyDead id="sh-why-photo">Counting from a photo is off for the same reason.</WhyDead>}

          {refusal && (
            <div className="sh-band top">
              <Refusal reason={refusal.reason} detail={refusal.detail} />
            </div>
          )}

          {/* The limits are read off the server and printed verbatim, so they
              are a fetch like any other and get the shape of what is coming
              rather than a gap that fills in and shoves the card taller. */}
          {!about && !aboutRefusal && (
            <div className="hint sh-limitskel" aria-hidden="true">
              <Skeleton w="94%" h={10} radius={999} />
              <Skeleton w="88%" h={10} radius={999} />
              <Skeleton w="52%" h={10} radius={999} />
            </div>
          )}
          {about && (
            <p className="hint">
              {about.limits.front_row_only} {about.limits.touching_packets}
              {about.taught.by_code_only ? <> {about.limits.code_only_products}</> : null}
            </p>
          )}
        </Card>

        {/* ---------------------------------------------- the facings -- */}
        <Card title="Facings" sub="how many of each product are visible, beside the stock figure">
          {counting && !read && <SkeletonRows rows={4} cols={2} />}

          {!counting && !read && (
            <Empty
              title="No shelf read yet"
              action={
                nothingTaught ? (
                  <a className="btn primary sh-linkbtn" href="#/products">TEACH A PRODUCT</a>
                ) : cam.running ? (
                  <Button variant="primary" loading={counting} onClick={() => void countCamera()}>
                    COUNT THE SHELF
                  </Button>
                ) : (
                  <>
                    <Button variant="primary" onClick={() => void cam.start()}>START THE CAMERA</Button>
                    <Button onClick={() => fileRef.current?.click()} disabled={nothingTaught}>USE A PHOTO</Button>
                  </>
                )
              }
            >
              {refusal
                ? 'The last count was refused — the reason is beside the picture. Point the camera along a shelf and press again, and every product it can name appears here with its facings.'
                : nothingTaught
                  ? 'A shelf is counted against the products this shop has taught, and none has been. Teach one from a photograph and it can be named here.'
                  : 'Point the camera along a shelf and press once. Every product it can name appears here with its facings, and the Stock screen’s own figure beside each for comparison. Facings are what is visible in the front row — they are not the stock.'}
            </Empty>
          )}

          {/* AN ADMISSION, BEFORE ANY FIGURE. When the camera was pointed at a
              person holding stock rather than along a shelf, there is no
              facing count — a facing is a position in a row and this frame has
              no rows. What it DID see is still listed below, because the
              products are real even when the count is not. */}
          {read && abstained && read.abstained && (
            <Verdict tone="amber" title="This is not a shelf, so nothing was counted">
              {read.abstained.detail}
              <span className="sh-abstain-saw">
                {read.abstained.saw.slice(0, 4).map((o, i) => (
                  <span className="pill" key={i}>
                    {o.label} · {Math.round(o.score * 100)}%
                  </span>
                ))}
                <span className="mono">{read.abstained.covers_frame_pct}% of the frame</span>
              </span>
              {recognisedProducts.length > 0 && (
                <> It did recognise {recognisedProducts.join(', ')} in the picture — those regions are
                listed beside it, without a facing figure.</>
              )}
            </Verdict>
          )}

          {read && (
            <div className="sh-results" aria-busy={counting || undefined}>
              {!abstained && <ShelfHero read={read} />}
              <StatGrid>
                {/* THE FIGURES ARE THE SERVER'S, UNTOUCHED. Only what qualifies
                    them changes when the counter abstained: under abstention it
                    sends 0 for named and 0 products, because no region was
                    promoted to a facing. `unknown` is the shared tile's tone for
                    a figure that was abstained on. */}
                <Stat
                  label="Regions seen"
                  value={read.counts.regions_seen}
                  sub={abstained
                    ? 'product-shaped things in view — NOT a facing count'
                    : read.counts.rejected > 0
                      ? `${read.counts.rejected} struck out by you`
                      : 'product-shaped things in view'}
                  tone={abstained ? 'amber' : undefined}
                />
                <Stat
                  label={abstained ? 'Counted as facings' : 'Named'}
                  value={read.counts.named}
                  sub={abstained
                    ? (recognisedProducts.length > 0
                      ? 'nothing was counted — what it recognised is listed beside the picture'
                      : 'nothing was counted on this frame')
                    : read.counts.by_hand > 0
                      ? `${read.counts.named - read.counts.by_hand} by the camera, ${read.counts.by_hand} by you`
                      : 'matched a taught product'}
                  tone={abstained ? 'unknown' : read.counts.named > 0 ? 'green' : undefined}
                />
                <Stat
                  label="Could not name"
                  value={read.counts.unnamed}
                  sub={abstained
                    ? 'every region, because none was counted'
                    : read.counts.unnamed === 0 ? 'every region was named'
                      : read.counts.same_packet > 0
                        ? `${read.counts.same_packet} of them the same packet twice`
                        : 'listed beside the picture — teach them'}
                  tone={abstained ? 'unknown' : read.counts.unnamed > 0 ? 'amber' : undefined}
                />
                <Stat
                  label="Not on this shelf"
                  value={read.counts.missing}
                  sub={abstained ? 'no facing count, so nothing to miss'
                    : read.counts.gone > 0 ? `${read.counts.gone} were here last read`
                      : 'taught products this frame does not show'}
                  tone={abstained ? 'unknown' : undefined}
                />
              </StatGrid>

              {read.counts.shelf_exceeds_figure > 0 && (
                <Verdict tone="info" title={`The front row shows more than the stock figure for ${read.counts.shelf_exceeds_figure} product${read.counts.shelf_exceeds_figure === 1 ? '' : 's'}`}>
                  The shop cannot hold fewer than are visible, so those figures are wrong. Count those shelves on the Stock screen.
                </Verdict>
              )}

              {!read.stock_figures.available && (
                <Verdict tone="info" title="The stock figure could not be read" icon={false}>
                  {read.stock_figures.detail ?? read.stock_figures.reason ?? 'No figure to compare against.'} The facings stand on their own.
                </Verdict>
              )}

              {read.empty_shelf ? (
                <Empty icon={false}>Nothing on this shelf looked like a product. An empty shelf is a result, not an error.</Empty>
              ) : read.facings.length === 0 ? (
                /* "and none named" was a LIE on an abstained frame: the counter
                   names what it can and then declines to count it. Only the
                   ordinary case — regions seen, nothing matched — may say that. */
                abstained ? null : (
                  <Empty icon={false}>
                    {read.counts.unnamed} region{read.counts.unnamed === 1 ? '' : 's'} seen and none named. They are
                    listed beside the picture with their crop, and can be taught from it.
                  </Empty>
                )
              ) : (
                <div className="sh-facings">
                  {read.facings.map((f) => <FacingRow key={f.sku_id} f={f} onPoint={setFocus} thumb={look.get(f.sku_id)?.thumb ?? null} />)}
                </div>
              )}

              {/* WHAT THIS COUNT IS SET BESIDE, said before any difference is
                  read off it. A comparison across two aisles is a number the
                  counter invented, so which read this one is against — and
                  whether it is even the same shelf — is stated, not implied. */}
              {read.previous ? (
                <p className="sh-limit">
                  Set beside the read of {shelfapi.when(read.previous.at)}
                  {read.previous.label ? <> — <b>{read.previous.label}</b></> : null}, which found{' '}
                  {read.previous.named} facing{read.previous.named === 1 ? '' : 's'} across{' '}
                  {read.previous.products} product{read.previous.products === 1 ? '' : 's'}.
                  {!read.previous.label && ' Neither read is named, so it may have been a different shelf.'}
                </p>
              ) : (
                <p className="sh-limit">
                  {read.label
                    ? `No earlier read of “${read.label}”, so there is nothing to compare this one with.`
                    : read.limits.comparison_needs_a_label}
                </p>
              )}

              <div className="sh-facts">
                <KV k="Read at">{shelfapi.when(read.at)}</KV>
                <KV k="On the chain">{read.audited ? 'yes' : 'no — the line could not be written'}</KV>
                <KV k="Proposers">{read.use_yolo ? 'contour + optional model' : 'contour only'}</KV>
                <KV k="Writes stock">never</KV>
              </div>
              <p className="sh-limit">{read.limits.not_a_stock_count}</p>
            </div>
          )}
        </Card>

        {/* -------------------------------------------------- regions -- */}
        <Card
          title="Every region"
          sub="what the camera found, in reading order — point at one to find it on the picture"
          aside={read ? <Pill tone="off">{read.counts.regions_seen}</Pill> : undefined}
        >
          {counting && !read && <SkeletonRows rows={3} cols={2} />}
          {!counting && !read && (
            <Empty title="Nothing read yet" icon={false}>
              Every product-shaped region the camera finds is listed here beside the picture it is drawn
              on — named in green, not named in amber, and named by you in ink. A region it could not
              match carries its own crop and can be taught from that picture without photographing
              anything again. A region it named wrong can be corrected, and the correction teaches it.
            </Empty>
          )}
          {read && rows.length === 0 && (
            <Empty icon={false}>
              Nothing on this frame looked like a product, so there are no regions to list. An empty
              shelf is a result, not an error.
            </Empty>
          )}
          {read && rows.length > 0 && (
            <div className="sh-region-list">
              {mainRows.map((r) => (
                <RegionRow
                  key={r.region}
                  r={r}
                  focus={focus}
                  busy={busy}
                  onPoint={setFocus}
                  onTeach={(n) => void openTeach(n, 'teach')}
                  onCorrect={(n) => void openTeach(n, 'correct')}
                  onReject={(n, undo) => void onReject(n, undo)}
                />
              ))}

              {/* THE SAME PACKET, SEEN TWICE — folded, never dropped.
                  These are amber regions that sit inside a packet already
                  counted: its lower half, its shadow, the price label under
                  it. Listing them as equals made a shelf of five packets read
                  as five names and five unknowns. They are still here, still
                  amber, still teachable — one tap away instead of in the way. */}
              {sameRows.length > 0 && (
                <div className="sh-same-block">
                  <button
                    type="button"
                    className="sh-same-toggle"
                    aria-expanded={showSame}
                    onClick={() => setShowSame((s) => !s)}
                  >
                    <span className="sh-state unnamed">{sameRows.length} region{sameRows.length === 1 ? '' : 's'} look like the same packet twice</span>
                    <span className="sh-chev">{showSame ? 'Hide' : 'Show'}</span>
                  </button>
                  <p className="sh-limit">
                    Each of these lies mostly inside a packet already counted, and two facings are two
                    positions in a row and do not overlap. They are not counted and not dropped: if one
                    really is another packet, teach it here.
                  </p>
                  {showSame && sameRows.map((r) => (
                    <RegionRow
                      key={r.region}
                      r={r}
                      focus={focus}
                      busy={busy}
                      onPoint={setFocus}
                      onTeach={(n) => void openTeach(n, 'teach')}
                      onCorrect={(n) => void openTeach(n, 'correct')}
                      onReject={(n, undo) => void onReject(n, undo)}
                    />
                  ))}
                </div>
              )}

              <p className="hint">
                The picture is held on the server for {Math.round(read.held_for_seconds / 60)} minutes so a
                region can be taught or corrected from it. {read.limits.rejection_teaches_nothing}
              </p>
            </div>
          )}
        </Card>

        {/* ------------------------------------------ what is not here -- */}
        <Card
          title="Not on this shelf"
          sub="taught products this frame does not show"
          aside={read && read.missing.length > 0 ? (
            <Pill tone={read.counts.gone > 0 ? 'amb' : 'off'}>{read.missing.length}</Pill>
          ) : undefined}
        >
          {counting && !read && <SkeletonRows rows={2} cols={2} />}
          {!counting && !read && (
            <Empty title="Nothing read yet" icon={false}>
              An empty facing is the thing worth walking to the shelf for, and it is invisible in a list
              of what IS there. After a count this shows every product the shop has taught that this
              frame does not show — the ones that were here on the last read of the same shelf first,
              because that is the only case a photograph gives real evidence for.
            </Empty>
          )}
          {read && abstained && (
            <Empty icon={false}>
              No facing was counted on this frame, so nothing can be said about what is not on it.
            </Empty>
          )}
          {read && !abstained && read.missing.length === 0 && (
            <Empty icon={false}>
              Every product this shop has taught is somewhere on this frame. Nothing is missing from it.
            </Empty>
          )}
          {read && !abstained && read.missing.length > 0 && (
            <div className="sh-missing-list">
              {read.missing.map((m) => <MissingRow key={m.sku_id} m={m} thumb={look.get(m.sku_id)?.thumb ?? null} />)}
              <p className="sh-limit">{read.limits.missing_is_not_out_of_stock}</p>
            </div>
          )}
        </Card>

        {/* -------------------------------------------- earlier reads -- */}
        <Card title="Earlier reads" sub="from this counter's own chain, newest first">
          {/* The shape of the rows, not a paragraph: a time and a line, four of
              them, so nothing moves when they land. */}
          {earlier === null && !earlierRefusal && <EarlierSkeleton />}
          {earlierRefusal && (
            <Refusal
              reason="The earlier reads could not be read"
              detail={earlierRefusal.reason}
              hint={earlierRefusal.detail}
              action={<Button size="sm" onClick={() => void load()}>TRY AGAIN</Button>}
            />
          )}
          {earlier !== null && earlier.length === 0 && (
            <Empty
              title="No shelf has been read yet"
              icon={false}
              action={
                nothingTaught
                  ? <a className="btn sh-linkbtn" href="#/products">TEACH A PRODUCT</a>
                  : <Button onClick={() => void cam.start()} disabled={cam.running}>START THE CAMERA</Button>
              }
            >
              Every count this counter makes is appended to its own hash chain and listed here — what
              was seen, what was named, and what it could not name. Count one shelf and the first line
              appears. Nothing on this list is a stock figure.
            </Empty>
          )}
          {earlier !== null && earlier.length > 0 && (
            <div className="sh-earlier-list">
              {earlier.map((r, i) => <EarlierRow key={r.hash ?? i} r={r} />)}
            </div>
          )}
          {about && (
            <p className="hint">
              {about.reads_on_chain} read{about.reads_on_chain === 1 ? '' : 's'} on the chain
              {about.chain.ok ? '' : ` — the chain does not verify: ${about.chain.error ?? 'unknown break'}`}.
              {' '}{about.taught.by_sight ?? '—'} product{about.taught.by_sight === 1 ? '' : 's'} can be named by sight
              {about.taught.by_code_only ? `, ${about.taught.by_code_only} only by code` : ''}.
              {' '}A corrected read is listed with its corrections applied; the chain still holds both lines.
            </p>
          )}
        </Card>
      </div>

      {/* THE PICTURE, BIG. The boxes are the evidence for every figure on this
          page, and inside a card on a phone they are a few millimetres across.
          The same hit targets are live here, so tapping a box in the big view
          closes it and puts the hand on that row. */}
      {read && annotated && big && (
        <Modal
          open
          size="wide"
          onClose={() => setBig(false)}
          title={read.label ? `${read.label} — ${read.counts.regions_seen} regions` : `${read.counts.regions_seen} regions`}
          sub="tap a box to find its row · green named by the camera, amber not named, ink named by you"
          foot={<Button variant="quiet" onClick={() => setBig(false)}>Close</Button>}
        >
          <div className="sh-bigframe">
            <Frame
              read={read}
              src={annotated}
              focus={focus}
              onPoint={setFocus}
              onPick={(n) => {
                setFocus(n);
                setBig(false);
                window.setTimeout(() => {
                  document.getElementById(`sh-region-${n}`)
                    ?.scrollIntoView({ block: 'center', behavior: 'smooth' });
                }, 60);
              }}
            />
          </div>
        </Modal>
      )}

      {read && teaching && (
        <TeachModal
          shelfId={read.shelf_id}
          kind={teaching.kind}
          region={rows.find((r) => r.region === teaching.region)!}
          products={products}
          onClose={() => setTeaching(null)}
          onTaught={onTaught}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------- teaching -- */

/**
 * Name one region, or correct the name the counter gave it.
 *
 * The page sends a REGION NUMBER and a name; the server holds the frame, cuts
 * the crop and derives the vectors. The price is sent as a decimal STRING and
 * parsed at the server's own money boundary, which refuses a float by name —
 * this input never becomes a number here.
 *
 * BOTH KINDS TEACH, and the dialog says which it is doing. A correction that
 * only relabelled the screen would leave the counter making the same mistake on
 * the next photograph, which is the difference between a fix and a complaint.
 */
function TeachModal({ shelfId, kind, region, products, onClose, onTaught }: {
  shelfId: string;
  kind: TeachKind;
  region: shelfapi.Region & Partial<shelfapi.Unnamed>;
  products: shelfapi.Product[] | null;
  onClose: () => void;
  onTaught: (res: shelfapi.TeachResult) => void;
}) {
  const correcting = kind === 'correct';
  const bySight = useMemo(
    () => (products ?? []).filter((p) => p.taught_with !== 'product_code_only'
      // The product it is being corrected AWAY from is not an answer to
      // "what is it really", and the server refuses it by name anyway.
      && !(correcting && p.sku_id === region.sku_id)),
    [products, correcting, region.sku_id],
  );
  const [mode, setMode] = useState<TeachMode>(region.top1_sku && !correcting ? 'known' : 'known');
  const [knownSku, setKnownSku] = useState<string>(
    region.top1_sku && region.top1_sku !== region.sku_id ? region.top1_sku : '');
  const [skuId, setSkuId] = useState('');
  const [name, setName] = useState('');
  const [rupees, setRupees] = useState('');
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<{ reason: string; detail?: string } | null>(null);

  useEffect(() => {
    if (mode === 'known' && !knownSku && bySight.length > 0) setKnownSku(bySight[0]!.sku_id);
  }, [mode, knownSku, bySight]);

  const submit = useCallback(async () => {
    setBusy(true);
    setRefusal(null);
    try {
      const body: shelfapi.TeachBody = mode === 'known'
        ? { region: region.region, sku_id: knownSku, force }
        : { region: region.region, sku_id: skuId.trim(), name: name.trim(), price_rupees: rupees.trim(), force };
      const r = correcting
        ? await shelfapi.correct(shelfId, body)
        : await shelfapi.teach(shelfId, body);
      if (!r.ok) { setRefusal(r); return; }
      onTaught(r);
    } finally { setBusy(false); }
  }, [mode, region.region, knownSku, skuId, name, rupees, force, shelfId, correcting, onTaught]);

  const collided = refusal?.reason === 'enrol_collision' || refusal?.reason === 'does_not_look_like_this_product';
  const src = shelfapi.pngUrl(region.crop_png_b64 ?? null);
  const canSubmit = mode === 'known' ? !!knownSku : !!skuId.trim() && !!name.trim() && !!rupees.trim();

  return (
    <Modal
      open
      onClose={onClose}
      title={correcting ? `Region ${region.region} is not ${region.name}` : `Teach region ${region.region}`}
      sub="the crop comes from the picture just taken; nothing is photographed again"
      note={correcting
        ? 'This view is taught to the product you name, so the next read is made by a counter that has seen it. The old name is not un-taught — nothing here can do that.'
        : mode === 'known' ? 'Adds another view to a product the shop knows.' : 'Teaches a new product from this one view.'}
      foot={
        <>
          <Button variant="quiet" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button variant="primary" onClick={() => void submit()} loading={busy} disabled={!canSubmit}>
            {correcting ? 'Correct it' : mode === 'known' ? 'Add this view' : 'Teach it'}
          </Button>
        </>
      }
    >
      <div className="sh-teach">
        <div className="sh-teach-crop">
          {src ? <img src={src} alt={`region ${region.region}`} /> : <span className="sh-nothumb">no crop</span>}
          <span className="sh-teach-why">
            {correcting
              ? <>the counter called this <b>{region.sku_id}</b></>
              : region.reason}
            {region.top1_sku && region.top1 !== null && region.top1 !== undefined && (
              <> · nearest {region.top1_sku} at {region.top1.toFixed(2)}</>
            )}
          </span>
        </div>

        <Segmented<TeachMode>
          wide
          value={mode}
          onChange={(m) => { setMode(m); setRefusal(null); }}
          options={[
            { value: 'known', label: 'A product it already knows', disabled: bySight.length === 0 },
            { value: 'new', label: 'A new product' },
          ]}
        />

        {mode === 'known' ? (
          <Field label="Which product" sub="only products taught by sight are listed — a code-only product has no appearance to add to">
            {products === null ? (
              <span className="muted">Loading the catalogue…</span>
            ) : (
              <Select value={knownSku} onChange={(e) => setKnownSku(e.target.value)}>
                {bySight.map((p) => (
                  <option key={p.sku_id} value={p.sku_id}>
                    {p.name} ({p.sku_id}, {p.n_views} view{p.n_views === 1 ? '' : 's'})
                  </option>
                ))}
              </Select>
            )}
          </Field>
        ) : (
          <>
            <Field label="Product id" sub="letters, digits, dots, dashes and underscores; it becomes a filename and a ledger key" required>
              <Input value={skuId} onChange={(e) => setSkuId(e.target.value)} placeholder="maggi_70g" autoCapitalize="off" />
            </Field>
            <Field label="Name" sub="what the shopkeeper reads on the bill" required>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Maggi 70g" />
            </Field>
            <Field label="Price, in rupees" sub="typed as text and parsed at the server's money boundary; never a float" required>
              <Input value={rupees} onChange={(e) => setRupees(e.target.value)} placeholder="14.00" inputMode="decimal" />
            </Field>
          </>
        )}

        {refusal && <Refusal reason={refusal.reason} detail={refusal.detail} />}
        {collided && (
          <Checkbox
            checked={force}
            onChange={setForce}
            label={correcting ? 'Correct it anyway' : 'Teach it anyway'}
            sub="Skips the guard above. Two products that look alike will both be amber at the till."
          />
        )}
      </div>
    </Modal>
  );
}
