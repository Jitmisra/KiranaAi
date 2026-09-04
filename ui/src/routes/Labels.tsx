import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import * as labelsapi from '../lib/labelsapi';
import { rupees } from '../lib/money';
import {
  Button, Card, Empty, Field, Input, KV, LoadingCard, Pill, RadioGroup, Refusal, Segmented,
  Select, Skeleton, Stat, StatGrid, Table, Toaster, Verdict, Working, toast,
} from '../components/ui';
import type { Column } from '../components/ui';
import '../styles/labels.css';

/**
 * Labels — a price and a code for the packets that carry neither.
 *
 * The counter reads printed codes. A jar of home-made pickle has none, so the
 * shopkeeper prints one: this screen picks the products, picks the sticker
 * sheet the stationer sold, and opens a page the browser prints at 100 %. The
 * same code the single sticker carries, many at a time, with the name and the
 * price beside each.
 *
 * WHAT THIS PAGE DOES NOT DO. It does not count the sheets, it does not price a
 * label and it does not decide what the code says. Every figure on the right —
 * labels, sheets, blank cells, the price each sticker prints — is the server's
 * answer to `POST /labels/plan`, and the sheet itself is a server-rendered page
 * this screen only opens. The page sends sku ids, copy counts and a layout id.
 *
 * TWO PRICES, AND WHICH ONE GOES ON PAPER. A sticker prints the MARKED price:
 * it stays on the packet for months and an offer does not. A shelf talker
 * prints what the till CHARGES today, with the marked price struck through
 * when an offer is on, because a talker is exactly the paper a shop uses to
 * announce one. Both are said on the screen beside the numbers, not implied.
 *
 * PRINTING. The sheet opens in a new tab so the shopkeeper can see it before
 * spending paper. PRINT opens the same tab and calls its print dialog once it
 * has loaded — same origin, so the page can. The sheet carries no script of its
 * own: this counter's policy allows none inline, and a print page that works
 * saved to a USB stick is worth more than a button on it.
 */

/** A copy count as typed. Whole numbers only; anything else is left to the server to refuse. */
function copiesOf(raw: string): number | null {
  const t = raw.trim();
  if (!/^\d{1,4}$/.test(t)) return null;
  const n = Number(t);
  return n > 0 ? n : null;
}

/**
 * Open the sheet in a new tab and, if asked, print it once it has loaded.
 *
 * Same origin, so the opener can read the tab's `readyState`. The poll is
 * bounded: a tab that never becomes ours (a pop-up blocker replaced it, the
 * server refused and answered JSON) is simply left open for the shopkeeper to
 * read, and nothing here retries a print.
 */
function openSheet(url: string, andPrint: boolean): void {
  const w = window.open(url, '_blank');
  if (!w) {
    toast('The browser blocked the new tab.', {
      tone: 'amb', note: 'Allow pop-ups for this counter, then press the button again.',
    });
    return;
  }
  if (!andPrint) return;
  const started = Date.now();
  const tick = () => {
    try {
      if (w.closed) return;
      if (w.document.readyState === 'complete' && w.location.pathname.startsWith('/labels/')) {
        w.focus();
        w.print();
        return;
      }
    } catch {
      /* not ours yet */
    }
    if (Date.now() - started < 15_000) setTimeout(tick, 150);
  };
  setTimeout(tick, 150);
}

/** One sticker drawn to the layout's millimetres. A preview of proportion, not of print. */
function Preview({ layout, row, line }: {
  layout: labelsapi.Layout; row: labelsapi.ProductRow; line: labelsapi.PlanLine | null;
}) {
  const pad = layout.cut_lines ? 1.8 : Math.max(1.2, layout.label_h_mm * 0.065);
  const style = {
    ['--lb-w' as string]: layout.label_w_mm,
    ['--lb-h' as string]: layout.label_h_mm,
    ['--lb-pad' as string]: pad,
    ['--lb-qr' as string]: layout.qr_mm,
  } as CSSProperties;
  // Type scales with the label's height so the three grids read differently.
  const namePx = Math.max(9, Math.round(layout.label_h_mm * 0.42));
  const pricePx = Math.max(13, Math.round(layout.label_h_mm * 0.72));
  const skuPx = Math.max(8, Math.round(layout.label_h_mm * 0.3));
  return (
    <div className="lb-preview-wrap">
      <div className={layout.cut_lines ? 'lb-preview cut' : 'lb-preview'} style={style}>
        <img src={row.qr_png_url} alt="" />
        <div className="t">
          <div className="n" style={{ fontSize: namePx }}>{row.name}</div>
          <div className="p" style={{ fontSize: pricePx }}>{rupees(row.price_paise)}</div>
          <div className="k" style={{ fontSize: skuPx }}>{row.sku_id}</div>
        </div>
      </div>
      <span className="lb-preview-cap">
        {layout.label_w_mm} × {layout.label_h_mm} mm, code {layout.qr_mm} mm square
        {line ? ` — ${line.module_mm} mm a module for this product; a code this small is read held near the camera` : ''}.
        Drawn to proportion on this screen; the printed size is the sheet&rsquo;s.
      </span>
    </div>
  );
}

/**
 * THE SHEET, DRAWN WHOLE, AT ITS OWN PROPORTIONS.
 *
 * The complaint this answers: a 65-up sheet showed three labels in the top-left
 * corner of a blank white rectangle, and nothing on the screen said the other
 * 62 cells were anywhere in particular. Every cell is drawn now — the ones that
 * get a label, the ones a part-used sheet has already lost, and the ones that
 * will come out of the printer blank and be thrown away. A shopkeeper buys
 * these sheets; the waste should be visible before the paper is spent.
 *
 * IT IS A PICTURE OF THE SERVER'S PLAN, NOT A SECOND COUNT. The figures beside
 * it — labels, sheets, blank cells — stay the server's answer to `/labels/plan`
 * and are not derived from anything here. What this walks is the same order the
 * server walks (`plan.lines`, in order, from `plan.skipped`, wrapping every
 * `plan.cells_per_page`), so the picture cannot disagree with the sheet unless
 * the server's own loop changes; there is no arithmetic here that the sheet
 * does not already do.
 *
 * THE PAGE SIZE IS DERIVED, NOT TYPED. `left + (cols-1)*pitch + label + right`
 * is the sheet's own width, from the server's millimetres. A hardcoded 210 x
 * 297 here would be a second copy of a number that already exists, and the one
 * that drifts is always the copy.
 */
function SheetMap({ layout, plan, skip, onPick }: {
  layout: labelsapi.Layout;
  plan: labelsapi.Plan | null;
  skip: number;
  onPick: (cell: number) => void;
}) {
  const pageW = layout.left_mm + (layout.cols - 1) * layout.pitch_x_mm + layout.label_w_mm + layout.right_mm;
  const pageH = layout.top_mm + (layout.rows - 1) * layout.pitch_y_mm + layout.label_h_mm + layout.bottom_mm;

  /** cell on sheet 1 -> the product that lands on it. The server's own walk. */
  const owner = useMemo(() => {
    const out = new Map<number, string>();
    if (!plan) return out;
    let idx = plan.skipped;
    for (const ln of plan.lines) {
      for (let i = 0; i < ln.copies; i += 1) {
        if (idx < plan.cells_per_page) out.set(idx, ln.name);
        idx += 1;
      }
    }
    return out;
  }, [plan]);

  const cells = [];
  for (let cell = 0; cell < layout.per_page; cell += 1) {
    const row = Math.floor(cell / layout.cols);
    const col = cell % layout.cols;
    const name = owner.get(cell);
    const past = cell < skip;
    const what = name
      ? `cell ${cell}: ${name}`
      : past
        ? `cell ${cell}: skipped — this sheet has already lost it`
        : `cell ${cell}: blank. Click to start printing here.`;
    cells.push(
      <button
        key={cell}
        type="button"
        /* The labelled number field below is the keyboard control for this;
           these 65 buttons are a pointer shortcut and must not become 65 tab
           stops between the sheet size and the PRINT button. */
        tabIndex={-1}
        aria-label={what}
        title={what}
        onClick={() => onPick(cell)}
        style={{
          position: 'absolute',
          left: `${((layout.left_mm + col * layout.pitch_x_mm) / pageW) * 100}%`,
          top: `${((layout.top_mm + row * layout.pitch_y_mm) / pageH) * 100}%`,
          width: `${(layout.label_w_mm / pageW) * 100}%`,
          height: `${(layout.label_h_mm / pageH) * 100}%`,
          padding: 0,
          borderRadius: 2,
          cursor: 'pointer',
          font: 'inherit',
          fontSize: 8,
          lineHeight: 1,
          color: name ? '#fff' : 'var(--ink-3)',
          /* Blue is the machine's own colour; green, amber and red belong to
             settled, abstained and refused and are not free for stationery. */
          background: name ? 'var(--blue-500)' : past ? 'rgba(17,20,32,.10)' : 'transparent',
          border: `1px solid ${name ? 'var(--blue-500)' : 'var(--line-2)'}`,
          opacity: past && !name ? 0.7 : 1,
          display: 'flex',
          alignItems: 'flex-end',
          justifyContent: 'flex-end',
          overflow: 'hidden',
        }}
      >
        {name ? '' : <span style={{ opacity: 0.55, padding: '0 2px 1px 0' }}>{cell}</span>}
      </button>,
    );
  }

  const blankHere = layout.per_page - skip - owner.size;
  return (
    <div>
      <div
        role="group"
        className="lb-sheet"
        aria-label={`Sheet 1 of this run: ${layout.per_page} cells, ${layout.cols} across by ${layout.rows} down`}
        /* WIDTH, MAX-WIDTH AND MARGIN MOVED TO labels.css (`.lb-sheet`), and
           they are the only three that left. The rest stay inline because they
           are either constant chrome or, in aspectRatio's case, computed from
           this layout's millimetres and so cannot live in a stylesheet.

           The three that moved had to move: an inline `maxWidth: 316` beats any
           stylesheet rule without `!important`, and at 390 the phone needs to
           widen this box past 316 to get its cells over the thumb floor. See
           the media query in labels.css for the measurement. */
        style={{
          position: 'relative',
          aspectRatio: `${pageW} / ${pageH}`,
          background: '#fff',
          border: '1px solid var(--line-2)',
          borderRadius: 4,
          boxShadow: '0 1px 6px rgba(17,20,32,.10)',
        }}
      >
        {cells}
      </div>
      <span className="lb-preview-cap" style={{ display: 'block', marginTop: 8 }}>
        {plan
          ? <>Sheet 1 of {plan.pages}: {owner.size} cell{owner.size === 1 ? '' : 's'} printed
            {skip > 0 ? <>, {skip} skipped</> : null}
            {blankHere > 0 ? <>, {blankHere} left blank</> : null}. Click any cell to start
            printing there — a part-used sheet is not a wasted one.</>
          : <>All {layout.per_page} cells, {layout.cols} across × {layout.rows} down. Click the
            first free cell of a part-used sheet to start there.</>}
      </span>
    </div>
  );
}

export default function Labels() {
  const [layouts, setLayouts] = useState<labelsapi.Layouts | null>(null);
  const [products, setProducts] = useState<labelsapi.Products | null>(null);
  const [health, setHealth] = useState<labelsapi.LabelsHealth | null>(null);
  /**
   * THREE FETCHES, THREE ANSWERS.
   *
   * These used to share ONE error slot, filled by `setErr(l.ok ? p : l)` inside
   * the branch that only ran when the PRODUCTS call failed. So when the sheet
   * sizes were refused and the catalogue was fine, nothing was ever recorded:
   * `loading` went false, `layouts` stayed null, and the card that holds the
   * whole right-hand column rendered an empty body with no heading, no
   * skeleton and no refusal. The talker card below it did the same. A screen
   * that goes blank is the one failure mode a shopkeeper cannot report.
   */
  const [err, setErr] = useState<{ reason: string; detail?: string } | null>(null);
  const [layoutErr, setLayoutErr] = useState<{ reason: string; detail?: string } | null>(null);
  const [healthErr, setHealthErr] = useState<{ reason: string; detail?: string } | null>(null);
  const [loading, setLoading] = useState(true);

  const [layoutId, setLayoutId] = useState('a4_65');
  const [skip, setSkip] = useState('0');
  /** Said out loud when this screen moved the start cell, so it is not a silent edit. */
  const [skipNote, setSkipNote] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  /** sku -> copies as typed. Presence is the tick. */
  const [chosen, setChosen] = useState<Record<string, string>>({});

  const [plan, setPlan] = useState<labelsapi.Plan | null>(null);
  const [planErr, setPlanErr] = useState<{ reason: string; detail?: string } | null>(null);
  const [planning, setPlanning] = useState(false);
  const planSeq = useRef(0);

  const [talkerSku, setTalkerSku] = useState('');
  const [talkerSize, setTalkerSize] = useState('a6');
  const [talkerCopies, setTalkerCopies] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    const [l, p, h] = await Promise.all([labelsapi.layouts(), labelsapi.products(), labelsapi.health()]);
    // EACH ANSWER IS RECORDED WHERE IT BELONGS, whether it came back or not.
    if (l.ok) { setLayouts(l); setLayoutErr(null); } else { setLayoutErr(l); }
    if (p.ok) {
      setProducts(p);
      setErr(null);
      // The screen exists for the products with no printed code, so those
      // start ticked; the ones that already carry a barcode do not.
      setChosen((prev) => {
        if (Object.keys(prev).length) return prev;
        const next: Record<string, string> = {};
        for (const it of p.items) if (!it.has_printed_code) next[it.sku_id] = '1';
        return next;
      });
      setTalkerSku((cur) => cur || p.items.find((it) => !it.has_printed_code)?.sku_id || p.items[0]?.sku_id || '');
    } else {
      setErr(p);
    }
    if (h.ok) { setHealth(h); setHealthErr(null); } else { setHealth(null); setHealthErr(h); }
    setLoading(false);
  }, []);

  useEffect(() => { void load(); }, [load]);

  const layout = useMemo(
    () => layouts?.layouts.find((l) => l.layout_id === layoutId) ?? layouts?.layouts[0] ?? null,
    [layouts, layoutId],
  );
  const items = useMemo(() => products?.items ?? [], [products]);
  const bySku = useMemo(() => new Map(items.map((it) => [it.sku_id, it])), [items]);

  /**
   * The start cell as typed, and whether it is a cell on THIS sheet.
   *
   * `skipOff` only styles the field and draws the map from cell 0; it does not
   * quietly correct the number. What gets sent is still what was typed (or -1
   * when it is not a number at all), so the refusal a shopkeeper sees is the
   * server's own sentence about their own sheet, not this page's guess.
   *
   * `\d+`, NOT `\d{1,3}`. The length bound was a stand-in for a range check
   * and it got the range wrong: "0060" is four characters and cell 60, which
   * `/labels/sheet?skip=0060` renders happily (measured — 200, cell 60). The
   * bound flagged the field red, sent -1 instead, and turned PRINT off for a
   * number the server accepts. The range is checked on the next line, where
   * the sheet that defines it is actually in hand; a digit string too long to
   * be a cell fails there, which is the same answer for the right reason.
   */
  const skipN = /^\d+$/.test(skip.trim()) ? Number(skip.trim()) : null;
  const skipOff = skipN === null || (layout !== null && skipN > layout.per_page - 1);
  const skipCell = skipOff ? 0 : (skipN ?? 0);

  /** What will be sent: ticked rows with a whole-number count. */
  const request = useMemo(() => {
    const out: Array<{ sku_id: string; copies: number }> = [];
    for (const it of items) {
      const raw = chosen[it.sku_id];
      if (raw === undefined) continue;
      const n = copiesOf(raw);
      if (n !== null) out.push({ sku_id: it.sku_id, copies: n });
    }
    return out;
  }, [items, chosen]);
  const badCounts = useMemo(
    () => Object.entries(chosen).filter(([, v]) => copiesOf(v) === null).length,
    [chosen],
  );

  // The plan is the server's arithmetic, asked for a moment after the last
  // change so typing "12" does not send two requests. A stale answer is
  // dropped by sequence number, never shown.
  useEffect(() => {
    if (!layout) return;
    if (request.length === 0) { setPlan(null); setPlanErr(null); setPlanning(false); return; }
    const seq = ++planSeq.current;
    setPlanning(true);
    const t = setTimeout(async () => {
      const r = await labelsapi.plan({ layout: layout.layout_id, items: request, skip: skipN ?? -1 });
      if (seq !== planSeq.current) return;
      setPlanning(false);
      if (r.ok) { setPlan(r); setPlanErr(null); } else { setPlan(null); setPlanErr(r); }
    }, 250);
    return () => clearTimeout(t);
  }, [layout, request, skipN]);

  /**
   * SWITCHING SHEET SIZES MUST NOT LEAVE THE SCREEN REFUSED.
   *
   * Cell 60 is a cell on a 65-up sheet and is not a cell at all on an 8-up one.
   * Switching between them kept sending skip=60, and the server answered
   * `skip_not_a_cell_on_this_sheet` — correctly — which turned PRINT off and
   * put a refusal on screen for something the shopkeeper had not done. The only
   * way out was to read the hint and retype. A different sheet size is a
   * different physical sheet, whose used corner nobody here knows about, so the
   * start cell goes back to the first one and the screen SAYS it did.
   *
   * `skipN`, not a second parse. This read `/^\d{1,3}$/` and fell back to 0,
   * so "0060" scored 0, cleared the `n > per_page - 1` test, and the reset it
   * needed never ran: the switch left "0060" in the box and the refusal on
   * screen — the exact state this guard exists to prevent. One parse for the
   * whole screen means the guard cannot disagree with the field it guards.
   */
  const chooseLayout = useCallback((id: string) => {
    setLayoutId(id);
    const next = layouts?.layouts.find((l) => l.layout_id === id);
    const n = skipN ?? 0;
    if (next && n > next.per_page - 1) {
      setSkip('0');
      setSkipNote(`${next.name} has cells 0 to ${next.per_page - 1}, so the start cell went back to the first one.`);
    } else {
      setSkipNote(null);
    }
  }, [layouts, skipN]);

  const pickCell = useCallback((cell: number) => {
    setSkip(String(cell));
    setSkipNote(null);
  }, []);

  const tick = useCallback((sku: string, on: boolean) => {
    setChosen((prev) => {
      const next = { ...prev };
      if (on) next[sku] = next[sku] ?? '1'; else delete next[sku];
      return next;
    });
  }, []);
  const setCopies = useCallback((sku: string, v: string) => {
    setChosen((prev) => ({ ...prev, [sku]: v }));
  }, []);

  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return items;
    return items.filter((it) => it.name.toLowerCase().includes(q) || it.sku_id.toLowerCase().includes(q));
  }, [items, filter]);

  const chooseAll = (which: 'nocode' | 'all' | 'none') => {
    setChosen((prev) => {
      if (which === 'none') return {};
      const next: Record<string, string> = { ...prev };
      for (const it of items) {
        if (which === 'all' || !it.has_printed_code) next[it.sku_id] = next[it.sku_id] ?? '1';
      }
      return next;
    });
  };

  const cols = useMemo<ReadonlyArray<Column<labelsapi.ProductRow>>>(() => [
    {
      key: 'on', head: '', width: '36px',
      cell: (r) => (
        <input
          type="checkbox"
          className="lb-check"
          aria-label={`Print a label for ${r.name}`}
          checked={r.sku_id in chosen}
          onChange={(e) => tick(r.sku_id, e.target.checked)}
        />
      ),
    },
    {
      key: 'name', head: 'Product',
      cell: (r) => (
        <>
          <span className="lb-name">{r.name}</span>
          <span className="lb-sku">{r.sku_id}</span>
        </>
      ),
    },
    {
      key: 'how', head: 'Taught', drop: true,
      cell: (r) => (r.has_printed_code
        ? <Pill tone="off">HAS A CODE</Pill>
        : <Pill tone="code">{r.taught_with === 'mat_measured' ? 'ON THE MAT' : 'BY LOOK'}</Pill>),
    },
    {
      key: 'price', head: 'On the sticker', num: true,
      cell: (r) => (
        <>
          <span className="lb-price">{rupees(r.price_paise)}</span>
          {r.offer_today && (
            <span className="lb-charged">till charges {rupees(r.charged_paise)} today</span>
          )}
        </>
      ),
    },
    {
      key: 'copies', head: 'Copies', num: true, width: '90px',
      cell: (r) => (
        <Input
          className="lb-copies sm"
          type="text"
          inputMode="numeric"
          aria-label={`Copies of ${r.name}`}
          disabled={!(r.sku_id in chosen)}
          bad={r.sku_id in chosen && copiesOf(chosen[r.sku_id] ?? '') === null}
          value={chosen[r.sku_id] ?? ''}
          onChange={(e) => setCopies(r.sku_id, e.target.value)}
        />
      ),
    },
  ], [chosen, tick, setCopies]);

  /**
   * WHY THE SHEET HAS FEWER PRODUCTS ON IT THAN THE SHOP HAS.
   *
   * Four priced products and three labels looked like the fourth had been lost.
   * It has not: it was taught FROM a printed code, so it already carries one,
   * and this screen starts it unticked. Every row is in the list and every one
   * can be ticked — the only thing that was missing was the sentence saying so,
   * which a `HAS A CODE` chip on one row is not.
   */
  const codedOff = useMemo(
    () => items.filter((it) => it.has_printed_code && !(it.sku_id in chosen)),
    [items, chosen],
  );

  const previewRow = request.length ? bySku.get(request[0]!.sku_id) ?? null : null;
  const talkerRow = bySku.get(talkerSku) ?? null;
  const talkerSizeRow = layouts?.talker_sizes.find((t) => t.size_id === talkerSize) ?? null;
  const talkerN = talkerCopies.trim() === '' ? undefined : copiesOf(talkerCopies);
  const talkerBad = talkerCopies.trim() !== '' && talkerN === null;

  return (
    <div className="lb-page">
      <Toaster />
      <div className="page-head">
        <h1>Labels</h1>
        <p>
          For the packets that carry no barcode: loose atta, a jar of pickle, anything made in the
          shop. Pick the products, pick the sticker sheet you have, and print once. The code names
          the product; the till prices it from the catalogue at the sale, so a sticker can never set
          a bill.
        </p>
      </div>

      {err && (
        <div>
          <Refusal
            reason="The catalogue could not be read"
            detail={err.reason}
            hint={err.detail}
            action={<Button size="sm" onClick={() => void load()}>TRY AGAIN</Button>}
          />
        </div>
      )}

      <div className="lb-grid">
        {/* ------------------------------------------------------- picker -- */}
        <div className="lb-col">
          <Card
            title="What to print"
            sub="the price shown is the marked price, which is what the sticker prints"
            aside={products ? <Pill tone={request.length ? 'code' : 'off'}>{request.length} CHOSEN</Pill> : undefined}
          >
            {loading && !products && <LoadingCard lines={4} label="Loading the catalogue" />}
            {/* The card carries its own refusal too. The one at the top of the
                page scrolls away on a phone, and an empty card body under a
                heading reads as a screen that is still loading forever. */}
            {!loading && !products && (
              <Refusal
                reason="The catalogue could not be read"
                detail={err?.reason}
                hint={err?.detail ?? 'Nothing can be chosen until the list of priced products answers.'}
                action={<Button size="sm" onClick={() => void load()}>TRY AGAIN</Button>}
              />
            )}
            {!loading && products && products.count === 0 && (
              <Empty
                title="Nothing to print yet"
                action={<a className="btn sm" href="#/products">TEACH A PRODUCT</a>}
              >
                A label needs a name and a price, and nothing in this shop has either yet.
              </Empty>
            )}
            {products && products.count > 0 && (
              <>
                <div className="toolbar lb-tools">
                  <Input
                    className="sm"
                    type="search"
                    placeholder="Find a product"
                    aria-label="Find a product"
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                  />
                  <div className="spacer" />
                  <Button size="sm" variant="ghost" onClick={() => chooseAll('nocode')}>
                    THE {products.without_printed_code} WITHOUT A CODE
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => chooseAll('all')}>ALL</Button>
                  <Button size="sm" variant="ghost" onClick={() => chooseAll('none')}>NONE</Button>
                </div>
                <Table
                  cols={cols}
                  rows={visible}
                  rowKey={(r) => r.sku_id}
                  rowClass={(r) => (r.sku_id in chosen ? undefined : 'lb-off')}
                  empty={filter ? (
                    <Empty
                      title={`Nothing matches “${filter}”`}
                      action={<Button size="sm" onClick={() => setFilter('')}>CLEAR THE SEARCH</Button>}
                    >
                      The shop has {products.count} priced product{products.count === 1 ? '' : 's'};
                      none of them is named or filed under that.
                    </Empty>
                  ) : 'Nothing here yet.'}
                  maxHeight="560px"
                  label="Products"
                />
                {codedOff.length > 0 && (
                  <p className="lb-note" style={{ marginTop: 12 }}>
                    {codedOff.length === 1
                      ? <><b>{codedOff[0]!.name}</b> is not ticked</>
                      : <><b>{codedOff.length} products</b> are not ticked
                        ({codedOff.slice(0, 3).map((it) => it.name).join(', ')}
                        {codedOff.length > 3 ? ` and ${codedOff.length - 3} more` : ''})</>}
                    {' '}because {codedOff.length === 1 ? 'it was' : 'they were'} taught from a
                    printed code and already {codedOff.length === 1 ? 'carries' : 'carry'} one the
                    counter reads. A second code on the same packet scans no better. Tick{' '}
                    {codedOff.length === 1 ? 'it' : 'them'} anyway if the packet&rsquo;s own code is
                    worn, or if you are relabelling loose stock into your own packets.
                  </p>
                )}
                {badCounts > 0 && (
                  <p className="lb-note" style={{ marginTop: 12 }}>
                    {badCounts} copy count{badCounts === 1 ? ' is' : 's are'} not a whole number.
                    Those rows are left off the plan until they are.
                  </p>
                )}
              </>
            )}
          </Card>

          {loading && !health && !healthErr && (
            <Card title="Every print is witnessed" className="lb-health">
              <LoadingCard lines={4} label="Reading the labels chain" />
            </Card>
          )}
          {healthErr && (
            <Card title="Every print is witnessed" className="lb-health">
              {/* THE WITNESS PANEL HAS TO SURVIVE ITS OWN FAILURE. It used to be
                  dropped on the floor by `h.ok ? h : null` — so the one card
                  that says whether the chain verifies disappeared exactly when
                  the chain could not be read, which reads as "nothing to
                  report" and means the opposite. */}
              <Refusal
                reason="The labels chain could not be read"
                detail={healthErr.reason}
                hint={healthErr.detail ?? 'Printing still works; what cannot be shown is whether past runs are on a verified chain.'}
                action={<Button size="sm" onClick={() => void load()}>TRY AGAIN</Button>}
              />
            </Card>
          )}
          {health && (
            <Card title="Every print is witnessed" className="lb-health">
              <p className="lb-preview-cap" style={{ marginBottom: 12 }}>
                Each sheet and each talker appends one line to this counter&rsquo;s own labels chain:
                which products, how many, at what price, when. A sticker found on a shelf next year
                can be matched to the run that printed it.
              </p>
              <KV k="Chain">
                {health.chain_ok
                  ? <Pill tone={health.lines ? 'ok' : 'off'}>{health.lines ? 'VERIFIES' : 'NOTHING PRINTED YET'}</Pill>
                  : <Pill tone="bad">BROKEN</Pill>}
              </KV>
              <KV k="Lines">{health.lines}</KV>
              <KV k="File"><span className="mono">{health.audit_file}</span></KV>
              {!health.chain_ok && health.chain_error && (
                <KV k="Error"><span className="mono">{health.chain_error}</span></KV>
              )}
              {!health.qr_encoder && (
                <KV k="Encoder"><span className="mono">OpenCV is not importable; no code can be drawn.</span></KV>
              )}
            </Card>
          )}
        </div>

        {/* -------------------------------------------------------- sheet -- */}
        <div className="lb-col">
          <Card title="The sheet" sub="an A4 grid, placed in millimetres, printed at 100 %">
            {loading && !layouts && <LoadingCard lines={5} label="Loading the sheet sizes" />}
            {!loading && !layouts && (
              <Refusal
                reason="The sheet sizes could not be read"
                detail={layoutErr?.reason}
                hint={layoutErr?.detail
                  ?? 'The layouts are the server’s: their millimetres, their grid, their compatible stationery. Nothing here can be guessed at, so nothing is drawn.'}
                action={<Button size="sm" onClick={() => void load()}>TRY AGAIN</Button>}
              />
            )}
            {layouts && (
              <div className="lb-plan">
                <div className="lb-layouts">
                  <RadioGroup
                    name="lb-layout"
                    label="Sticker sheet"
                    value={layoutId}
                    onChange={chooseLayout}
                    options={layouts.layouts.map((l) => ({
                      value: l.layout_id,
                      label: l.name,
                      sub: `${l.label_w_mm} × ${l.label_h_mm} mm · ${l.cols} across × ${l.rows} down · ${l.compatible}`,
                    }))}
                  />
                </div>

                {layout && (
                  <SheetMap layout={layout} plan={planning ? null : plan} skip={skipCell} onPick={pickCell} />
                )}

                <Field
                  label="Start at cell"
                  sub={layout ? `A part-used sheet: click its first free cell above, or count them from the top left, 0 to ${layout.per_page - 1}.` : undefined}
                  htmlFor="lb-skip"
                  error={skipOff && layout
                    ? `This sheet has cells 0 to ${layout.per_page - 1}.`
                    : undefined}
                >
                  <div className="lb-skip">
                    <Input
                      id="lb-skip"
                      className="sm"
                      type="text"
                      inputMode="numeric"
                      value={skip}
                      bad={skipOff}
                      onChange={(e) => { setSkip(e.target.value); setSkipNote(null); }}
                    />
                  </div>
                </Field>

                {skipNote && <p className="lb-note">{skipNote}</p>}

                {layout && previewRow && (
                  <Preview
                    layout={layout}
                    row={previewRow}
                    line={plan && !planning ? plan.lines.find((l) => l.sku_id === previewRow.sku_id) ?? null : null}
                  />
                )}

                {request.length === 0 && !loading && (
                  <Empty title="Nothing chosen yet">
                    Tick a product in the list on the left. The count of labels, the count of sheets
                    and the cells left blank are the server&rsquo;s arithmetic, and they appear here
                    as soon as there is something to count.
                  </Empty>
                )}

                {planErr && (
                  <Refusal
                    reason={planErr.reason}
                    detail={planErr.detail}
                    hint="Nothing was printed and nothing was witnessed. Your ticks and counts are unchanged."
                  />
                )}

                {/* EVERY ONE OF THESE FIGURES IS A FETCH. `…` said a number was
                    missing; a skeleton at the shape of the figure says one is on
                    its way, and the tile does not change height when it lands. */}
                {request.length > 0 && !planErr && (
                  <>
                    {planning && (
                      <p className="lb-working"><Working /> asking the server for the arithmetic</p>
                    )}
                    <StatGrid>
                      <Stat
                        sm
                        label="Labels"
                        value={plan && !planning ? plan.labels : <Skeleton w={44} h={19} radius={999} />}
                        sub={plan && !planning ? `${plan.lines.length} product${plan.lines.length === 1 ? '' : 's'}` : 'asking the server'}
                      />
                      <Stat
                        sm
                        label="Sheets of A4"
                        value={plan && !planning ? plan.pages : <Skeleton w={30} h={19} radius={999} />}
                        sub={plan && !planning ? `${plan.cells_per_page} cells each` : 'asking the server'}
                      />
                      <Stat
                        sm
                        label="Left blank"
                        value={plan && !planning ? plan.blank_on_last_page : <Skeleton w={30} h={19} radius={999} />}
                        sub={plan && !planning ? 'on the last sheet' : 'asking the server'}
                      />
                    </StatGrid>
                  </>
                )}

                {plan && !planning && plan.offers_today.length > 0 && (
                  <Verdict tone="info" title={`${plan.offers_today.length} of these has an offer on today`}>
                    The sticker prints the marked price, not the offer, because a sticker outlives
                    an offer. The till applies today&rsquo;s offer at the sale. For an offer price on
                    paper, print a shelf talker below.
                  </Verdict>
                )}

                <div className="lb-actions">
                  <Button
                    variant="primary"
                    disabled={!plan || planning}
                    loading={planning && request.length > 0}
                    onClick={() => plan && openSheet(plan.sheet_url, true)}
                  >
                    PRINT{plan && !planning ? ` ${plan.pages} SHEET${plan.pages === 1 ? '' : 'S'}` : ''}
                  </Button>
                  <Button
                    disabled={!plan || planning}
                    onClick={() => plan && openSheet(plan.sheet_url, false)}
                  >
                    OPEN THE SHEET
                  </Button>
                  {/* THE CHEAPEST SHEET TO GET WRONG IS A BLANK ONE. `grid=1`
                      is the same run with the cell edges printing; on plain
                      paper, held up against the sticker sheet, it says whether
                      the printer is scaling before any stock is spent. The URL
                      is still the server's — this asks for the other rendering
                      of it and names no product and no price. */}
                  <Button
                    variant="ghost"
                    disabled={!plan || planning}
                    onClick={() => plan && openSheet(`${plan.sheet_url}&grid=1`, false)}
                    title="The same run with the cell edges printing. Put plain paper in the printer, then hold it against your sticker sheet."
                  >
                    ALIGNMENT PROOF
                  </Button>
                </div>

                <p className="lb-note">
                  A sheet of {layout ? layout.per_page : 65} costs money and a printer that scales
                  wastes all of it. ALIGNMENT PROOF prints this same run on PLAIN paper with the
                  cell edges showing: hold it against your sticker sheet and every box should sit
                  on a sticker. Nothing but the labels prints on the sheet itself.
                </p>

                {/* WHY THE TWO BUTTONS ARE OFF. Both need a plan, and a plan is
                    the server's answer — this page never counts a sheet. */}
                {!plan && (
                  <p className="lb-why">
                    {request.length === 0
                      ? 'PRINT is off because nothing is ticked. The sheet is built from the products you choose on the left.'
                      : planErr
                        ? 'PRINT is off because the counter refused to plan this run — the reason is above, in its own words.'
                        : planning
                          ? 'PRINT is off until the server has worked out how many sheets this is. This page never counts them itself.'
                          : 'PRINT is off because there is no plan to print.'}
                  </p>
                )}

                <p className="lb-note">
                  Print at 100 % on A4 with margins set to none, and turn off &ldquo;fit to
                  page&rdquo;: the labels are placed in millimetres and scaling moves every row onto
                  the gap. The page carries no script and no outside file, so it prints the same
                  saved to a stick.
                </p>
              </div>
            )}
          </Card>

          <Card title="A shelf talker" sub="one product, the price large enough to read from the door">
            {loading && (!products || !layouts) && <LoadingCard lines={3} label="Loading" />}
            {/* The same silent blank the sheet card had: this body needed BOTH
                answers and said nothing at all when one of them never came. */}
            {!loading && !layouts && (
              <Refusal
                reason="The talker sizes could not be read"
                detail={layoutErr?.reason}
                hint={layoutErr?.detail ?? 'A talker is placed in the server’s millimetres on the server’s page size. Neither is guessed at here.'}
                action={<Button size="sm" onClick={() => void load()}>TRY AGAIN</Button>}
              />
            )}
            {!loading && layouts && !products && (
              <Refusal
                reason="The catalogue could not be read"
                detail={err?.reason}
                hint={err?.detail ?? 'A talker names one product, and the list of products is what did not answer.'}
                action={<Button size="sm" onClick={() => void load()}>TRY AGAIN</Button>}
              />
            )}
            {products && products.count === 0 && (
              <Empty
                title="Nothing to announce yet"
                action={<a className="btn sm" href="#/products">TEACH A PRODUCT</a>}
              >
                A talker is a price set large enough to read from the door, and this shop has no
                priced product to set.
              </Empty>
            )}
            {products && products.count > 0 && layouts && (
              <div className="lb-talker">
                <Field label="Which product" htmlFor="lb-talker-sku">
                  <Select id="lb-talker-sku" value={talkerSku} onChange={(e) => setTalkerSku(e.target.value)}>
                    {items.map((it) => (
                      <option key={it.sku_id} value={it.sku_id}>
                        {it.name} — {rupees(it.charged_paise)}
                      </option>
                    ))}
                  </Select>
                </Field>

                <Field label="Size">
                  <Segmented
                    value={talkerSize}
                    onChange={setTalkerSize}
                    options={layouts.talker_sizes.map((t) => ({
                      value: t.size_id,
                      label: t.size_id.toUpperCase(),
                      title: `${t.name}: ${t.w_mm} × ${t.h_mm} mm on ${t.page}`,
                    }))}
                  />
                  {talkerSizeRow && (
                    <span className="sub">
                      {talkerSizeRow.w_mm} × {talkerSizeRow.h_mm} mm, {talkerSizeRow.per_page} to a
                      sheet of {talkerSizeRow.page}. Cut along the dashed line.
                    </span>
                  )}
                </Field>

                <Field
                  label="How many"
                  sub={talkerSizeRow ? `Leave blank to fill one sheet (${talkerSizeRow.per_page}). Up to ${layouts.limits.max_talker_copies}.` : undefined}
                  htmlFor="lb-talker-n"
                  error={talkerBad ? 'A count is a whole number.' : undefined}
                >
                  <div className="lb-skip">
                    <Input
                      id="lb-talker-n"
                      className="sm"
                      type="text"
                      inputMode="numeric"
                      placeholder={talkerSizeRow ? String(talkerSizeRow.per_page) : ''}
                      value={talkerCopies}
                      bad={talkerBad}
                      onChange={(e) => setTalkerCopies(e.target.value)}
                    />
                  </div>
                </Field>

                {talkerRow && (
                  talkerRow.offer_today ? (
                    <Verdict tone="info" title="An offer is on today">
                      The talker will show {rupees(talkerRow.charged_paise)} large with{' '}
                      {rupees(talkerRow.price_paise)} struck through, and today&rsquo;s date. When the
                      offer ends, print it again.
                    </Verdict>
                  ) : (
                    <p className="lb-preview-cap">
                      The talker will show {rupees(talkerRow.charged_paise)}, the marked price, and
                      today&rsquo;s date.
                    </p>
                  )
                )}

                <div className="lb-actions">
                  <Button
                    variant="primary"
                    disabled={!talkerRow || talkerBad}
                    onClick={() => talkerRow && openSheet(labelsapi.talkerUrl(talkerRow.sku_id, talkerSize, talkerN ?? undefined), true)}
                  >
                    PRINT THE TALKER
                  </Button>
                  <Button
                    disabled={!talkerRow || talkerBad}
                    onClick={() => talkerRow && openSheet(labelsapi.talkerUrl(talkerRow.sku_id, talkerSize, talkerN ?? undefined), false)}
                  >
                    OPEN IT
                  </Button>
                </div>

                {(talkerBad || !talkerRow) && (
                  <p className="lb-why">
                    {talkerBad
                      ? 'Both buttons are off because HOW MANY is not a whole number. Clear it to fill one sheet.'
                      : 'Both buttons are off until a product is chosen above.'}
                  </p>
                )}
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
