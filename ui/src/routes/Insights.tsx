import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { RefObject } from 'react';
import * as ins from '../lib/insightsapi';
import { rupees } from '../lib/money';
import {
  Button, Card, KV, Pill, Verdict, Empty, Refusal, Segmented, SectionHead,
  Insight, Fig, Skeleton, Thinking, Table, type Column,
} from '../components/ui';
import '../styles/insights.css';

/**
 * How wide the box a chart is about to be drawn into actually is.
 *
 * WHY THIS EXISTS. Both charts on this screen used to be authored at a fixed
 * width — 30 days x 20 px = 616, and 24 hours x 21 px = 520 — and were given
 * `min-width: <that>` so a narrow screen scrolled them sideways instead of
 * scaling them down. The reasoning was sound as far as it went: an SVG scaled
 * from 616 to 356 takes its axis labels with it, and a 10 px date rendered at
 * 0.58x is a 5.8 px smudge that looks like information.
 *
 * But the conclusion was wrong. Measured at 390: the day chart was drawn 616
 * px wide inside a 356 px card and the shopkeeper saw 17 of 30 days, with the
 * newest — the ones they opened the screen for — off the right edge behind a
 * swipe. The answer is not to scale the drawing down and not to push it off
 * the screen: it is to DRAW IT AT THE WIDTH THERE IS. A narrower step and a
 * thinner bar cost nothing legible; the labels stay at the px they were
 * authored at because the SVG stays 1:1 with its viewBox.
 *
 * Returns 0 until the first measurement, and callers fall back to the authored
 * width for that first paint so nothing renders at zero.
 */
function useBoxWidth(): [RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    /* THE CONTENT BOX, NOT `clientWidth`. This wrapper is `box-sizing:
       border-box` with 20px of padding a side (it bleeds the chart into the
       card's gutters), so `clientWidth` reported 356 where only 316 could be
       drawn into. The chart was built 40 px too wide, the wrapper scrolled by
       exactly that 40, and the two figures that live at the right-hand end —
       the peak "₹3,980.00" and the last date — were off the edge. Worse, the
       overflow flag computed from the same number said `data-scrolls="no"`, so
       the sentence telling the shopkeeper to swipe was hidden at the one
       moment it was true. Measured at 390: clientWidth 356, padding 20+20,
       scrollWidth 396 against clientWidth 356. */
    const read = () => {
      const cs = getComputedStyle(el);
      const pad = parseFloat(cs.paddingLeft || '0') + parseFloat(cs.paddingRight || '0');
      setW(Math.max(0, el.clientWidth - pad));
    };
    read();
    // ResizeObserver and not a window `resize` listener: the card also changes
    // width when the side rail opens, which fires no window resize at all.
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

/** Keep n inside [lo, hi]. */
const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));

/**
 * Where to put a chart label so it cannot fall off the end of the drawing.
 *
 * A centred <text> at the last bar sits about half its own width past the
 * viewBox, and SVG clips it silently — no scrollbar, no ellipsis, just a
 * figure with its right-hand digits shaved off. Measured at 390 on the day
 * chart: the peak label "₹3,980.00" is centred at x=344 of a 356 wide box and
 * lost everything past the comma, and the last date "4 Sep" lost "Sep". The
 * peak is the one number on that chart a shopkeeper came to read.
 *
 * So a label that would cross either end stops being centred and anchors to
 * that end instead. `chars * 5.6` is a deliberate over-estimate of a 10px
 * Plus Jakarta digit's advance: erring wide anchors a borderline label a few
 * pixels early, which is invisible, where erring narrow clips it, which is not.
 */
function labelAt(cx: number, text: string, W: number): { x: number; anchor: 'start' | 'middle' | 'end' } {
  const half = (text.length * 5.6) / 2;
  if (cx + half > W - 2) return { x: W - 2, anchor: 'end' };
  if (cx - half < 2) return { x: 2, anchor: 'start' };
  return { x: cx, anchor: 'middle' };
}

/**
 * INSIGHTS — what this counter has seen over time, and nothing it has not.
 *
 * Every figure on this screen is folded by the server out of the hash-chained
 * audit log, through the same `read_chain()` / `bills_from()` / per-day window
 * the History and Today screens use. Nothing here is computed in the browser:
 * this file draws rectangles and prints strings the server derived.
 *
 * THREE THINGS SHAPE THE LAYOUT, and they are all the same rule.
 *
 *  1. THE NUMBERS ARE NEXT TO THE DRAWING, ALWAYS. Every chart here has its
 *     figures rendered as text beside it and a "read as text" table behind it.
 *     A shopkeeper who cannot tell two bars apart across a counter has lost
 *     nothing; a screen where the only copy of a number is a bar height has
 *     shown him a picture and called it a book.
 *  2. "NOT ENOUGH HISTORY YET" IS A FIRST-CLASS STATE, not an error and not a
 *     blank. On a counter installed last week most of this page is in that
 *     state, so it is designed rather than tolerated: it says what it needs,
 *     how far along it is, and what will appear when it gets there.
 *  3. THERE IS NO PROJECTION ANYWHERE. No trend line, no "on track for", no
 *     run rate. A line drawn forward through a few weeks of a kirana's trading
 *     is a lie with a chart under it.
 *
 * COLOUR. Bars are indigo — the machine's own mark — with ONE exception that
 * earns it: the settled portion of a day is green, because green on this
 * product means a signature-verified webhook stands behind that money and
 * nothing else may borrow it. Amber and red do not appear on a chart here at
 * all; an anomaly is marked with an ink ring and explained in words, because an
 * unusual Thursday is not a recognition failure and must not look like one.
 */

const REFRESH_MS = 120_000;

export default function Insights() {
  const [days, setDays] = useState<ins.WindowDays>(30);
  const [body, setBody] = useState<ins.InsightsBody | null>(null);
  const [refusal, setRefusal] = useState<{ reason: string; detail?: string } | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (n: number) => {
    const r = await ins.insights({ days: n });
    if (r.ok) { setBody(r as ins.InsightsBody); setRefusal(null); }
    else { setRefusal({ reason: r.reason, detail: r.detail }); }
    setLoading(false);
  }, []);

  useEffect(() => {
    setLoading(true);
    void load(days);
    const id = setInterval(() => void load(days), REFRESH_MS);
    return () => clearInterval(id);
  }, [days, load]);

  /** Ask again after a refusal, showing the same drawn wait as a first load
      rather than leaving the refusal on screen with a button that looks dead. */
  const retry = useCallback(() => {
    setRefusal(null);
    setLoading(true);
    void load(days);
  }, [days, load]);

  return (
    <div className="stack insights">
      <div className="page-head">
        <h1>Insights</h1>
        <p>
          The last {days} days of this counter, folded out of its own audit chain. Where the
          history will not carry a figure this page says so and shows nothing, because a
          comparison made against days that do not exist reads as a real change. Nothing here
          is projected forward.
        </p>
      </div>

      <div className="ins-bar">
        <Segmented
          value={String(days)}
          onChange={(v) => setDays(Number(v) as ins.WindowDays)}
          options={ins.WINDOWS.map((d) => ({ value: String(d), label: `${d} days` }))}
        />
        <div className="spacer" />
        {body && (
          body.chain.ok
            ? <Pill tone="ok">chain verified · {body.chain.lines_verified} lines</Pill>
            : <Pill tone="bad">chain broken at line {body.chain.lines_verified}</Pill>
        )}
      </div>

      {refusal ? (
        <Refusal
          reason={refusal.reason}
          detail={refusal.detail}
          hint="Nothing was shown rather than a figure derived some other way."
          action={<Button size="sm" onClick={retry}>TRY AGAIN</Button>}
        />
      ) : loading || !body ? (
        <InsightsWaiting days={days} />
      ) : body.history.closed_bills === 0 ? (
        <Card title="Nothing has crossed this counter yet">
          <Empty
            title="No bills in the chain"
            action={<a className="btn sm" href="#/till">OPEN THE TILL</a>}
          >
            This screen is built entirely out of bills that closed. The first one will put a
            bar on the chart the moment it does, and each comparison below will appear as its
            own stretch of history arrives.
          </Empty>
        </Card>
      ) : (
        <Loaded body={body} />
      )}
    </div>
  );
}

/* ================================================================= loaded == */

function Loaded({ body }: { body: ins.InsightsBody }) {
  const d = body.days;
  const anomalyDays = useMemo(
    () => new Set(body.anomalies.available ? body.anomalies.days.map((a) => a.key) : []),
    [body.anomalies],
  );

  return (
    <>
      {/* THE WINDOW, AS A SENTENCE — and the same rule as everywhere else on
          this screen. Where the history carries a rise or a fall, the figure
          for it is here in the accent; where it does not, the sentence says
          what the comparison needs and how far along this counter is, and NO
          smaller version of the figure appears in its place. Green is the
          settled figure and nothing else, because a signature-verified webhook
          is the only thing on this product that earns it. */}
      <Headline days={d} week={body.week} window={body.window} />

      <div className="ins-stats">
        <BigStat
          label={`billed over ${d.days_of_history} day${d.days_of_history === 1 ? '' : 's'}`}
          value={money(d.total_paise, d.total_rupees)}
          sub={`${d.bills} bill${d.bills === 1 ? '' : 's'} · ${d.trading_days} day${d.trading_days === 1 ? '' : 's'} took money`}
        />
        <BigStat
          label="settled — real money"
          value={money(d.settled_paise, d.settled_rupees)}
          sub="only a signature-verified webhook counts here"
          tone="green"
        />
        <BigStat
          label="a typical day"
          value={d.baseline.available ? money(d.baseline.median_paise, d.baseline.median_rupees) : '—'}
          sub={d.baseline.available
            ? 'the median day, not the average — one festival cannot move it'
            : `not enough history yet: ${d.baseline.days_of_history} of ${d.baseline.days_needed} ${d.baseline.counting}`}
        />
        <BigStat
          label="history"
          value={`${body.history.days_spanned} day${body.history.days_spanned === 1 ? '' : 's'}`}
          sub={body.history.first_bill_day
            ? `first bill ${body.history.first_bill_day}`
            : 'no bills yet'}
        />
      </div>

      <DayByDay days={d} anomalyDays={anomalyDays} window={body.window} />

      <div className="grid two">
        <div className="stack">
          <WeekCard week={body.week} series={d.series} />
          <WeekdayCard block={body.same_weekday} />
        </div>
        <div className="stack">
          <HoursCard block={body.hours} />
          <ProductsCard block={body.products} />
        </div>
      </div>

      <AnomaliesCard block={body.anomalies} />

      <Card title="Where these numbers come from" tight>
        <KV k="source">the hash-chained audit log, folded by gawaah.manage</KV>
        <KV k="window">
          {body.window.from} to {body.window.to} · this counter's own clock (UTC{fmtOffset(body.window.utc_offset)})
        </KV>
        <KV k="chain">
          {body.chain.ok
            ? <Pill tone="ok">verified · {body.chain.lines_verified} lines</Pill>
            : <Pill tone="bad">broken — {body.chain.error}</Pill>}
        </KV>
        {body.history.undated_bills > 0 && (
          <KV k="undated bills">
            {body.history.undated_bills} closed bill{body.history.undated_bills === 1 ? '' : 's'} whose
            timestamp would not parse. Counted here, and on no day, because no day is the honest
            answer.
          </KV>
        )}
        <p className="hint">{body.derived_from}</p>
        <SectionHead>What this screen does not do</SectionHead>
        <ul className="ins-limits">
          {body.limits.map((l) => <li key={l}>{l}</li>)}
        </ul>
      </Card>
    </>
  );
}

/* ============================================================ day by day == */

type DayView = 'chart' | 'text';

function DayByDay({ days, anomalyDays, window: w }: {
  days: ins.DaysBlock; anomalyDays: Set<string>; window: ins.Window;
}) {
  const [view, setView] = useState<DayView>('chart');
  const withHistory = days.series.filter((p) => !p.no_history);
  const gaps = days.series.length - withHistory.length;

  const columns: Array<Column<ins.DayPoint>> = [
    { key: 'day', head: 'day', cell: (p) => <span className={p.no_history ? 'muted' : ''}>{p.short}</span> },
    { key: 'bills', head: 'bills', num: true, cell: (p) => (p.no_history ? '—' : p.bills) },
    { key: 'billed', head: 'billed', num: true, cell: (p) => (p.no_history ? '—' : money(p.revenue_paise, p.revenue_rupees)) },
    { key: 'settled', head: 'settled', num: true, cell: (p) => (p.no_history ? '—' : money(p.settled_paise, p.settled_rupees)) },
    {
      key: 'note', head: '', cell: (p) => (
        p.no_history ? <span className="muted">before this counter</span>
          : !p.complete ? <span className="muted">still running</span>
            : anomalyDays.has(p.date) ? <span className="ins-flag">far from usual</span>
              : p.bills === 0 ? <span className="muted">nothing billed</span> : null
      ),
    },
  ];

  return (
    <Card
      title="Day by day"
      sub={`${w.from} to ${w.to}, in this counter's own timezone`}
      aside={
        <Segmented
          size="sm"
          value={view}
          onChange={setView}
          options={[{ value: 'chart', label: 'Chart' }, { value: 'text', label: 'Read as text' }]}
        />
      }
    >
      {view === 'text' ? (
        <Table
          rows={days.series}
          cols={columns}
          rowKey={(p) => p.date}
          maxHeight="none"
          label="Billed and settled for every day in the window"
        />
      ) : (
        <>
          <DayBars days={days} anomalyDays={anomalyDays} />
          <div className="ins-legend">
            <span><i className="sw billed" /> billed</span>
            <span><i className="sw settled" /> settled — a verified webhook</span>
            <span><i className="sw zero" /> open, took nothing</span>
            {gaps > 0 && <span><i className="sw gap" /> before this counter existed ({gaps})</span>}
            {days.baseline.available && <span><i className="sw med" /> median day</span>}
          </div>
          <p className="ins-swipe">
            The chart runs wider than this screen. Swipe it sideways for the rest of the
            window, or read the same figures as text.
          </p>
        </>
      )}

      <div className="ins-facts">
        {days.busiest_complete_day && (
          <KV k="busiest finished day">
            {days.busiest_complete_day.short} — {money(days.busiest_complete_day.revenue_paise, days.busiest_complete_day.revenue_rupees)}
          </KV>
        )}
        {days.quietest_complete_day && (
          <KV k="quietest day that traded">
            {days.quietest_complete_day.short} — {money(days.quietest_complete_day.revenue_paise, days.quietest_complete_day.revenue_rupees)}
          </KV>
        )}
        <KV k="median day">
          {days.baseline.available
            ? money(days.baseline.median_paise, days.baseline.median_rupees)
            : <span className="muted">not enough history yet</span>}
        </KV>
      </div>
      {/* The method sentence lives here rather than in the KV above: a KV sets
          its value hard right, and a sentence in one wraps ragged under a
          two-line label the moment the screen is a phone. */}
      <p className="hint">
        {days.baseline.available ? days.baseline.method : days.baseline.detail}
      </p>
      <p className="hint">
        {view === 'chart'
          ? 'The last bar is today and today is not over, so it is drawn lighter and no comparison on this page uses it as a whole day.'
          : 'The last row is today and today is not over, so no comparison on this page uses it as a whole day.'}
        {gaps > 0 && ` ${gaps} day${gaps === 1 ? '' : 's'} at the start of this window are before
        this counter's first bill. They are gaps, not zeros: counting them would drag every
        baseline below towards nothing.`}
      </p>
    </Card>
  );
}

/**
 * The bar chart.
 *
 * Drawn by hand rather than by a library: the page ships no chart package, the
 * CSP allows no external host, and thirty rectangles do not need four hundred
 * kilobytes. Every colour comes from a class in insights.css, never a literal.
 *
 * It scrolls sideways on a narrow screen instead of scaling down. A chart
 * scaled to a phone's width takes its axis labels with it, and a five-pixel
 * date is not a label — it is a smudge that looks like information.
 */
function DayBars({ days, anomalyDays }: { days: ins.DaysBlock; anomalyDays: Set<string> }) {
  const series = days.series;
  const n = series.length;
  const [boxRef, box] = useBoxWidth();
  const padL = 8;
  const padR = 8;
  // The step this chart WANTS, on a card with room for it.
  const wantStep = n <= 31 ? 20 : n <= 62 ? 13 : 9;
  // The narrowest step that is still a bar and not a hairline. Below 6 the
  // 2 px settled/billed seam eats the whole mark and the two fills read as one.
  const MIN_STEP = 6;
  // The step the box in front of us can actually pay for. `box` is 0 on the
  // very first paint, before the observer has measured; fall back to the
  // authored step then so nothing is ever drawn at zero width.
  const fitStep = box > 0 ? (box - padL - padR) / n : wantStep;
  const step = box > 0 ? clamp(fitStep, MIN_STEP, wantStep) : wantStep;
  const barw = Math.max(3, Math.floor(step) - Math.min(7, Math.max(2, Math.round(step / 3))));
  const top = 34;
  const plotH = 132;
  const bottom = 28;
  const W = Math.round(padL + padR + n * step);
  const H = top + plotH + bottom;
  // It only scrolls when even the floor step will not fit — 60+ days on a
  // phone. Everything narrower is drawn whole, at the width there is.
  const scrolls = box > 0 && W > box + 1;
  const base = top + plotH;

  const peak = Math.max(days.peak_paise, 1);
  const y = (paise: number) => base - Math.round((paise * plotH) / peak);
  const x = (i: number) => padL + i * step + (step - barw) / 2;

  const median = days.baseline.available ? days.baseline.median_paise : null;

  // WHICH DAYS GET A DATE UNDER THEM. Every kth, plus the last one — but the
  // last one wins, and any regular label close enough to collide with it is
  // dropped. Two dates printed on top of each other is not two labels.
  const every = Math.max(1, Math.ceil(n / 8));
  const labelled = new Set<number>();
  for (let i = 0; i < n; i += every) labelled.add(i);
  for (let i = n - 1; i > n - 1 - Math.ceil(every / 2) && i >= 0; i -= 1) labelled.delete(i);
  labelled.add(n - 1);

  return (
    <div className="ins-chart-scroll" ref={boxRef} data-scrolls={scrolls ? 'yes' : 'no'}>
      <svg
        className="ins-chart"
        width={W}
        height={H}
        viewBox={`0 0 ${W} ${H}`}
        /* W is now computed FROM this box, so 1:1 is the normal case and the
           axis labels keep the px they were authored at. `minWidth` still
           holds the floor for the one case that cannot fit — a 90-day window
           on a 390 px phone — and only then does the wrapper scroll. */
        style={{ width: '100%', minWidth: W, height: 'auto' }}
        role="img"
        aria-label={`Billed and settled for each of ${n} days, ${series[0]?.short} to ${series[n - 1]?.short}. The same figures are in the "read as text" table.`}
      >
        <line className="ax" x1={0} y1={base + 0.5} x2={W} y2={base + 0.5} />

        {series.map((p, i) => {
          const bx = x(i);
          if (p.no_history) {
            return (
              <g key={p.date}>
                <title>{p.short} — before this counter's first bill</title>
                <rect className="gap" x={bx} y={base - 2} width={barw} height={2} rx={1} />
              </g>
            );
          }
          const flagged = anomalyDays.has(p.date);
          if (p.revenue_paise === 0) {
            return (
              <g key={p.date} className={flagged ? 'flagged' : undefined}>
                <title>{p.short} — open, nothing billed</title>
                <rect className="zero" x={bx} y={base - 3} width={barw} height={3} rx={1.5} />
                {flagged && <circle className="flag" cx={bx + barw / 2} cy={base - 15} r={3.5} />}
              </g>
            );
          }
          const topY = y(p.revenue_paise);
          const settledY = y(p.settled_paise);
          const hasSettled = p.settled_paise > 0;
          const partial = hasSettled && p.settled_paise < p.revenue_paise;
          return (
            <g key={p.date} className={`bar${p.complete ? '' : ' running'}${flagged ? ' flagged' : ''}`}>
              <title>
                {`${p.short} — billed ${money(p.revenue_paise, p.revenue_rupees)}, settled ${money(p.settled_paise, p.settled_rupees)}, ${p.bills} bill${p.bills === 1 ? '' : 's'}`}
                {p.complete ? '' : ' (today, still running)'}
              </title>
              <path className="billed" d={topBar(bx, topY, barw, base - topY, 3)} />
              {hasSettled && (
                <path className="settled" d={topBar(bx, settledY, barw, base - settledY, 3)} />
              )}
              {/* the 2px surface gap that keeps two stacked fills from reading
                  as one shape. Only where both actually exist. */}
              {partial && <rect className="seam" x={bx} y={settledY - 1} width={barw} height={2} />}
              {flagged && <circle className="flag" cx={bx + barw / 2} cy={topY - 9} r={3.5} />}
            </g>
          );
        })}

        {/* THE MEDIAN RULE IS DRAWN AFTER THE BARS. SVG paints in document
            order, so drawn first it was covered by every bar it crossed — which
            is every bar above the median, which is the half a reader is looking
            at. The plate keeps the value legible where it crosses one. */}
        {median !== null && median > 0 && (() => {
          const text = `median ${days.baseline.available ? money(median, days.baseline.median_rupees) : ''}`;
          // Width estimated from the character count rather than measured: a
          // getBBox() read would force a layout on every render for six pixels.
          const tw = text.length * 6.3 + 14;
          return (
            <>
              <line className="median" x1={padL} y1={y(median) + 0.5} x2={W - padR} y2={y(median) + 0.5} />
              <rect className="lbl-plate" x={W - padR - tw} y={y(median) - 17} width={tw} height={14} rx={3} />
              <text className="median-lbl" x={W - padR - 6} y={y(median) - 6} textAnchor="end">{text}</text>
            </>
          );
        })()}

        {/* Direct labels: the peak, and every flagged day. Never a number on
            every bar — thirty labels is a wall, not a chart. */}
        {series.map((p, i) => {
          const isPeak = !p.no_history && p.revenue_paise === days.peak_paise && days.peak_paise > 0;
          if (!isPeak && !anomalyDays.has(p.date)) return null;
          // A flagged day that billed nothing has no bar to sit a label above,
          // so the label clears the ring instead of landing on the axis.
          // Two lanes above a bar: the ring at −9, the figure clear above it.
          const ly = p.revenue_paise === 0 ? base - 31 : Math.max(11, y(p.revenue_paise) - 22);
          const txt = money(p.revenue_paise, p.revenue_rupees);
          const at = labelAt(x(i) + barw / 2, txt, W);
          return (
            <text key={`l-${p.date}`} className="val" x={at.x} y={ly}
                  textAnchor={at.anchor}>
              {txt}
            </text>
          );
        })}

        {series.map((p, i) => {
          if (!labelled.has(i)) return null;
          const txt = p.short.slice(4);
          const at = labelAt(x(i) + barw / 2, txt, W);
          return (
            <text key={`x-${p.date}`} className="xlbl" x={at.x} y={base + 15}
                  textAnchor={at.anchor}>
              {txt}
            </text>
          );
        })}
        {series.map((p, i) => {
          if (!labelled.has(i)) return null;
          const at = labelAt(x(i) + barw / 2, p.weekday_short, W);
          return (
            <text key={`w-${p.date}`} className="xlbl dim" x={at.x} y={base + 26}
                  textAnchor={at.anchor}>
              {p.weekday_short}
            </text>
          );
        })}
      </svg>
    </div>
  );
}

/** A bar with rounded top corners, square on the baseline it is anchored to. */
function topBar(x: number, y: number, w: number, h: number, r: number): string {
  const rr = Math.min(r, w / 2, Math.max(h, 0));
  const bottom = y + Math.max(h, 0);
  return `M${x} ${bottom} L${x} ${y + rr} Q${x} ${y} ${x + rr} ${y} L${x + w - rr} ${y} Q${x + w} ${y} ${x + w} ${y + rr} L${x + w} ${bottom} Z`;
}

/* ================================================================== week == */

function WeekCard({ week, series }: { week: ins.Block<ins.WeekBlockBody>; series: ins.DayPoint[] }) {
  if (!week.available) {
    return (
      <Card title="This week against last" sub="seven complete days against the seven before">
        <Waiting block={week} what="A week-against-week comparison" />
      </Card>
    );
  }
  const inWindow = series.filter(
    (p) => week.last_week.from !== null && p.date >= week.last_week.from && p.date <= (week.this_week.to ?? ''),
  );
  return (
    <Card title="This week against last" sub={`${week.this_week.from} to ${week.this_week.to}`}>
      <div className="ins-week">
        <div className="wk">
          <span className="lbl">these seven days</span>
          <span className="val">{money(week.this_week.revenue_paise, week.this_week.revenue_rupees)}</span>
          <span className="sub">{week.this_week.bills} bills · {week.this_week.units} packets</span>
        </div>
        <div className={`wk delta ${week.direction}`}>
          <span className="lbl">the change</span>
          <span className="val">
            {week.delta_paise === 0 ? 'level' : `${week.direction === 'up' ? '+' : '−'}${money(Math.abs(week.delta_paise), week.delta_rupees.replace('-', ''))}`}
          </span>
          <span className="sub">
            {week.delta_pct === null ? 'no percentage: last week took nothing'
              : `${Math.abs(week.delta_pct)}% on the week · ${week.bills_delta >= 0 ? '+' : ''}${week.bills_delta} bills`}
          </span>
        </div>
        <div className="wk">
          <span className="lbl">the seven before</span>
          <span className="val">{money(week.last_week.revenue_paise, week.last_week.revenue_rupees)}</span>
          <span className="sub">{week.last_week.bills} bills · {week.last_week.units} packets</span>
        </div>
      </div>

      <Spark points={inWindow} splitAfter={7} />

      <p className="hint">{week.sentence} {week.method[0]?.toUpperCase()}{week.method.slice(1)}.</p>
    </Card>
  );
}

/**
 * The sparkline over the fourteen complete days the comparison stands on, split
 * where one week ends and the next begins. It carries no numbers of its own —
 * both week totals are set as text directly above it — so it is doing the one
 * job a sparkline is honest at: showing the shape between two figures already
 * stated.
 */
function Spark({ points, splitAfter }: { points: ins.DayPoint[]; splitAfter: number }) {
  const n = points.length;
  if (n < 2) return null;
  const W = 320;
  const H = 56;
  const pad = 3;
  const peak = Math.max(...points.map((p) => p.revenue_paise), 1);
  const x = (i: number) => pad + (i * (W - pad * 2)) / (n - 1);
  const y = (v: number) => H - pad - Math.round((v * (H - pad * 2)) / peak);
  const line = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)} ${y(p.revenue_paise)}`).join(' ');
  const area = `${line} L${x(n - 1).toFixed(1)} ${H - pad} L${x(0).toFixed(1)} ${H - pad} Z`;
  const splitX = x(n - splitAfter) - (x(1) - x(0)) / 2;
  return (
    <div className="ins-spark">
      <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
           role="img" aria-label={`Daily billed takings across the ${n} days of both weeks. The totals for each week are stated above.`}>
        <path className="sp-area" d={area} />
        <path className="sp-line" d={line} />
        <line className="sp-split" x1={splitX} y1={0} x2={splitX} y2={H} />
      </svg>
      <div className="sp-ends">
        <span>{points[0]?.short}</span>
        <span>{points[n - 1]?.short}</span>
      </div>
    </div>
  );
}

/* ========================================================= the same weekday == */

function WeekdayCard({ block }: { block: ins.Block<ins.WeekdayBlockBody> }) {
  if (!block.available) {
    return (
      <Card title="This day of the week" sub="today against the same weekday before it">
        <Waiting block={block} what="A same-weekday comparison" />
      </Card>
    );
  }
  const rows = [
    {
      key: block.today.date, label: 'today', short: block.today.date,
      paise: block.today.revenue_paise, rupees: block.today.revenue_rupees, now: true,
    },
    ...block.previous.map((p) => ({
      key: p.date,
      label: `${p.weeks_ago} week${p.weeks_ago === 1 ? '' : 's'} ago`,
      short: p.short,
      paise: p.revenue_paise,
      rupees: p.revenue_rupees,
      now: false,
    })),
  ];
  const peak = Math.max(...rows.map((r) => r.paise), block.baseline_paise, 1);

  return (
    <Card
      title={`This ${block.weekday}`}
      sub={block.day_complete
        ? `against the last ${block.samples} ${block.weekday}s, whole days`
        : `against the last ${block.samples} ${block.weekday}s, each cut at ${block.cut_at}`}
      aside={<Pill tone="off">{block.samples} to compare</Pill>}
    >
      <div className="ins-hbars">
        {rows.map((r) => (
          <div className={`hb${r.now ? ' now' : ''}`} key={r.key}>
            <span className="hb-l">{r.label}</span>
            <span className="hb-track">
              <span className="hb-fill" style={{ width: `${pctWidth(r.paise, peak)}%` }} />
              {block.baseline_paise > 0 && (
                <span className="hb-med" style={{ left: `${pctWidth(block.baseline_paise, peak)}%` }} />
              )}
            </span>
            <span className="hb-v">{money(r.paise, r.rupees)}</span>
          </div>
        ))}
      </div>
      <div className="ins-facts">
        <KV k="the baseline">{money(block.baseline_paise, block.baseline_rupees)}</KV>
        <KV k="today against it">
          {block.delta_paise === 0 ? 'level' : (
            <>
              {block.direction === 'up' ? 'ahead by ' : 'behind by '}
              {money(Math.abs(block.delta_paise), block.delta_rupees.replace('-', ''))}
              {block.delta_pct !== null && <span className="muted"> · {Math.abs(block.delta_pct)}%</span>}
            </>
          )}
        </KV>
      </div>
      <p className="hint">
        The baseline is {block.baseline_method}.
        {!block.day_complete && (
          <>
            {' '}Today is not over, so this is a like-for-like figure and not a half day set
            against {block.samples === 1 ? 'a whole one' : `${block.samples} whole ones`}. Those
            days finished on{' '}
            {block.previous.map((p) => money(p.full_day_paise, p.full_day_rupees)).join(', ')}.
          </>
        )}
      </p>
    </Card>
  );
}

/* ================================================================= hours == */

function HoursCard({ block }: { block: ins.Block<ins.HoursBlockBody> }) {
  if (!block.available) {
    return (
      <Card title="When the shop is busy" sub="every closed bill, counted in the hour it closed">
        <Waiting block={block} what="An hour-of-day profile" />
      </Card>
    );
  }
  const peak = Math.max(block.peak_paise, 1);
  const top = [...block.profile]
    .filter((h) => h.revenue_paise > 0)
    .sort((a, b) => b.revenue_paise - a.revenue_paise)
    .slice(0, 4);
  // Same rule as the day chart: 24 hours drawn at the width the card has, not
  // at an authored 520 that pushed 8 of the 24 hours off a 390 px phone.
  const [hoursRef, hoursBox] = useBoxWidth();
  const HPAD = 8;
  const hStep = hoursBox > 0 ? clamp((hoursBox - HPAD * 2) / 24, 9, 21) : 21;
  const hBar = Math.max(5, Math.round(hStep) - 6);
  const HW = Math.round(HPAD * 2 + 24 * hStep);

  return (
    <Card
      title="When the shop is busy"
      sub={`across ${block.days_of_history} days that took money`}
      aside={block.busiest_hour ? <Pill tone="off">peak {block.busiest_hour.label}</Pill> : undefined}
    >
      <div className="ins-chart-scroll" ref={hoursRef} data-scrolls={hoursBox > 0 && HW > hoursBox + 1 ? 'yes' : 'no'}>
        <svg className="ins-chart hours" width={HW} height={128}
             viewBox={`0 0 ${HW} 128`}
             style={{ width: '100%', minWidth: HW, height: 'auto' }} role="img"
             aria-label="Takings by hour of day. The busiest hours are listed as text below.">
          <line className="ax" x1={0} y1={96.5} x2={HW} y2={96.5} />
          {block.profile.map((h) => {
            const bx = HPAD + h.hour * hStep + (hStep - hBar) / 2;
            const bw = hBar;
            const ht = Math.round((h.revenue_paise * 76) / peak);
            return (
              <g key={h.hour} className={h.revenue_paise > 0 ? 'bar' : 'bar empty'}>
                <title>{`${h.label} — ${money(h.revenue_paise, h.revenue_rupees)} across ${h.days_with_a_bill} day${h.days_with_a_bill === 1 ? '' : 's'}`}</title>
                {h.revenue_paise > 0
                  ? <path className="billed" d={topBar(bx, 96 - ht, bw, ht, 3)} />
                  : <rect className="zero" x={bx} y={94} width={bw} height={2} rx={1} />}
              </g>
            );
          })}
          {block.profile.map((h) => (
            // Every third hour, at every width. A first pass here dropped to
            // every sixth below a 14 px step to avoid overlapping labels, and
            // the arithmetic did not support it: an "03" is about 11 px at
            // 10px, and every third hour at the 12.5 px step a 390 px phone
            // gives puts them 37 px apart. It was hiding four axis labels to
            // buy 26 px of clearance it already had.
            h.hour % 3 === 0 ? (
              <text key={`x${h.hour}`} className="xlbl" y={112}
                    {...(({ x, anchor }) => ({ x, textAnchor: anchor }))(
                      labelAt(HPAD + h.hour * hStep + hStep / 2, String(h.hour).padStart(2, '0'), HW))}
              >{String(h.hour).padStart(2, '0')}</text>
            ) : null
          ))}
        </svg>
      </div>
      <div className="ins-hourlist">
        {top.map((h) => (
          <div className="hour-row" key={h.hour}>
            <span className="nm">{h.label}</span>
            <span className="amt">{money(h.revenue_paise, h.revenue_rupees)}</span>
            <span className="pct">{h.share_pct === null ? '—' : `${h.share_pct}%`}</span>
          </div>
        ))}
      </div>
      <p className="hint">{block.method}. {block.shares_note}</p>
    </Card>
  );
}

/* ============================================================== products == */

function ProductsCard({ block }: { block: ins.Block<ins.ProductsBlockBody> }) {
  if (!block.available) {
    return (
      <Card title="Selling more, selling less" sub="seven days against the seven before">
        <Waiting block={block} what="A rising-and-falling comparison" />
      </Card>
    );
  }
  const nothing = block.rising.length + block.falling.length
    + block.started_selling.length + block.stopped_selling.length === 0;
  return (
    <Card
      title="Selling more, selling less"
      sub={`${block.this_week.from} to ${block.this_week.to} against the week before`}
    >
      {nothing ? (
        <Empty>
          Nothing moved by enough to name. A product needs {block.min_units_to_judge} packets
          across both weeks before this card will call it a mover — one packet to two is one
          more packet, not a doubling.
        </Empty>
      ) : (
        <div className="ins-movers">
          <MoverList title="rising" rows={block.rising} total={block.rising_total} />
          <MoverList title="falling" rows={block.falling} total={block.falling_total} />
          <MoverList title="started this week" rows={block.started_selling} total={block.started_total} />
          <MoverList title="stopped" rows={block.stopped_selling} total={block.stopped_total} />
        </div>
      )}
      <p className="hint">
        {block.method}
        {block.too_few_to_judge > 0 && ` ${block.too_few_to_judge} product${block.too_few_to_judge === 1 ? ' was' : 's were'} below that line and ${block.too_few_to_judge === 1 ? 'is' : 'are'} not named above.`}
      </p>
    </Card>
  );
}

function MoverList({ title, rows, total }: { title: string; rows: ins.Mover[]; total: number }) {
  if (rows.length === 0) return null;
  return (
    <div className="mv-group">
      <SectionHead aside={total > rows.length ? <span className="muted">{rows.length} of {total}</span> : undefined}>
        {title}
      </SectionHead>
      {rows.map((m) => (
        <div className="mv" key={m.sku_id}>
          <span className="nm">
            {m.name}
            {!m.still_in_catalogue && <span className="muted"> — no longer in the catalogue</span>}
          </span>
          <span className="units">
            {m.units_before} → {m.units_now} <span className="muted">packets</span>
          </span>
          <span className={`delta ${m.delta_units > 0 ? 'up' : m.delta_units < 0 ? 'down' : ''}`}>
            {m.delta_units > 0 ? '+' : ''}{m.delta_units}
            {m.delta_pct !== null && <span className="muted"> · {Math.abs(m.delta_pct)}%</span>}
          </span>
        </div>
      ))}
    </div>
  );
}

/* ============================================================= anomalies == */

function AnomaliesCard({ block }: { block: ins.Block<ins.AnomaliesBlockBody> }) {
  if (!block.available) {
    return (
      <Card title="Far from usual" sub="a day or an hour a long way from its own baseline">
        <Waiting block={block} what="An anomaly is a distance from a baseline, and a baseline" />
      </Card>
    );
  }
  const all = [...block.days, ...block.hours];
  return (
    <Card
      title="Far from usual"
      sub={`${block.days_checked} days and ${block.hours_checked} hours measured against their own baselines`}
      aside={all.length > 0 ? <Pill tone="off">{block.days_found + block.hours_found} found</Pill> : undefined}
    >
      {all.length === 0 ? (
        <Verdict tone="info" title="Nothing was far enough from its own baseline to report">
          {block.nothing_found_note} {block.method.flagged_when[0]?.toUpperCase()}{block.method.flagged_when.slice(1)}.
        </Verdict>
      ) : (
        <div className="ins-anoms">
          {all.map((a) => <AnomalyRow key={`${a.kind}-${a.key}`} a={a} />)}
        </div>
      )}
      <p className="hint">
        The baseline is {block.method.baseline} and the spread is {block.method.spread}.{' '}
        {block.method.why_median} A point is flagged only when {block.method.flagged_when}.
        {block.subject_day_short && ` The hour scan reads ${block.subject_day_short}, the most recent complete day this counter traded — an hour still running has not finished taking money.`}
      </p>
    </Card>
  );
}

/**
 * One anomaly, with the two figures drawn on ONE shared scale so the distance
 * between them is the length of the gap and not a number to be trusted. Both
 * are printed as text beside the bars regardless.
 */
function AnomalyRow({ a }: { a: ins.Anomaly }) {
  const peak = Math.max(a.value_paise, a.baseline_paise, 1);
  return (
    <div className="anom">
      <div className="anom-head">
        <span className="k">{a.label}</span>
        <span className="kind">{a.kind === 'day' ? 'a day' : 'an hour'}</span>
        <span className={`arrow ${a.direction}`}>
          {a.direction === 'above' ? '▲' : '▼'} {money(Math.abs(a.deviation_paise), a.deviation_rupees.replace('-', ''))}
          {/* The percentage arrives signed; the arrow beside it already carries
              the direction, so showing the sign twice would read as a typo. */}
          {a.deviation_pct !== null && <span className="muted"> · {Math.abs(a.deviation_pct)}%</span>}
          {a.deviation_spreads_x10 !== null && (
            <span className="muted"> · {tenths(a.deviation_spreads_x10)}× the usual spread</span>
          )}
        </span>
      </div>
      <div className="anom-bars">
        <div className="ab">
          <span className="l">it took</span>
          <span className="t"><span className="f val" style={{ width: `${pctWidth(a.value_paise, peak)}%` }} /></span>
          <span className="v">{money(a.value_paise, a.value_rupees)}</span>
        </div>
        <div className="ab">
          <span className="l">usually</span>
          <span className="t"><span className="f base" style={{ width: `${pctWidth(a.baseline_paise, peak)}%` }} /></span>
          <span className="v">{money(a.baseline_paise, a.baseline_rupees)}</span>
        </div>
      </div>
      <p className="anom-say">{a.sentence}</p>
    </div>
  );
}

/* ================================================================ pieces == */

/**
 * The window, read aloud.
 *
 * The figures carry the colour and the words recede — the same object the
 * Today screen opens with, doing the same job here: saying the one thing a
 * shopkeeper came to this screen for before he has to find it among six cards.
 *
 * THE RISE-OR-FALL CLAUSE IS THE WHOLE POINT, and it is the clause this counter
 * most often cannot fill. When the week block is not available the sentence
 * says what the comparison needs and how far along this shop is, in the same
 * words the cards below use, and prints no figure at all — a percentage taken
 * against days that do not exist reads as a real change.
 */
function Headline({ days: d, week: w, window: win }: {
  days: ins.DaysBlock;
  week: ins.Block<ins.WeekBlockBody>;
  window: ins.Window;
}) {
  return (
    <Insight
      tag={`the last ${win.days} days`}
      foot={`folded out of this counter's own hash-chained audit log · ${win.from} to ${win.to}, on this counter's own clock (UTC${fmtOffset(win.utc_offset)})`}
    >
      <Fig>{money(d.total_paise, d.total_rupees)}</Fig> billed across{' '}
      <Fig tone="ink">{d.bills}</Fig> bill{d.bills === 1 ? '' : 's'}, on{' '}
      <Fig tone="ink">{d.trading_days}</Fig> day{d.trading_days === 1 ? '' : 's'} that took
      money.{' '}
      {d.settled_paise > 0 ? (
        <><Fig tone="green">{money(d.settled_paise, d.settled_rupees)}</Fig> of it has settled.</>
      ) : (
        <>None of it has settled.</>
      )}{' '}
      {w.available ? (
        w.direction === 'level' ? (
          <>The last seven days came out <Fig>level</Fig> with the seven before.</>
        ) : (
          <>
            The last seven days are{' '}
            <Fig>
              {w.direction} {money(Math.abs(w.delta_paise), w.delta_rupees.replace('-', ''))}
            </Fig>{' '}
            on the seven before.
          </>
        )
      ) : (
        <>
          Whether that is rising or falling is not shown: the week-against-week comparison needs{' '}
          {w.days_needed} {w.counting} and this counter has {w.days_of_history}.
        </>
      )}
    </Insight>
  );
}

/**
 * THE WAIT, AT THE SHAPE OF THE SCREEN THAT IS COMING.
 *
 * This screen used to wait behind one three-line card while the server folded a
 * whole window out of the chain, and then grew into a headline, four tiles, a
 * chart and five cards — every panel below the fold jumped. The cards are drawn
 * here with the titles they will keep, so nothing moves but the contents.
 *
 * The window buttons re-fetch, so this is not only a first-load state: it is
 * what the screen looks like every time a shopkeeper asks for 90 days instead
 * of 7, which is the moment the fold takes longest.
 */
function InsightsWaiting({ days }: { days: number }) {
  return (
    <>
      <Thinking
        title={`Folding the last ${days} days out of the chain`}
        steps={[
          { label: 'walking the hash-chained audit log from its genesis line' },
          { label: "placing every closed bill on the day and the hour it closed, on this counter's clock" },
          { label: 'working out which comparisons this much history can carry, and which it cannot' },
        ]}
        foot="Each block appears when its own fold finishes. None of them is drawn early against a partial read of the chain."
      />

      <div className="ins-stats ins-skel">
        {[0, 1, 2, 3].map((i) => (
          <div className="ins-stat" key={i} aria-hidden="true">
            <Skeleton w="66%" h={9} radius={999} />
            <Skeleton className="s-val" w="72%" h={26} />
            <Skeleton w="54%" h={9} radius={999} />
          </div>
        ))}
      </div>

      <Card title="Day by day" sub={`the last ${days} days, in this counter's own timezone`}>
        {/* The chart's own height, so the cards under it do not travel when
            thirty rectangles land. */}
        <Skeleton h={194} radius={10} />
        <div className="ins-legend ins-skel" aria-hidden="true">
          <Skeleton w={72} h={9} radius={999} />
          <Skeleton w={148} h={9} radius={999} />
          <Skeleton w={104} h={9} radius={999} />
        </div>
      </Card>

      <div className="grid two">
        <div className="stack">
          <Card title="This week against last" sub="seven complete days against the seven before">
            <Skeleton h={132} radius={10} />
          </Card>
          <Card title="This day of the week" sub="today against the same weekday before it">
            <Skeleton h={132} radius={10} />
          </Card>
        </div>
        <div className="stack">
          <Card title="When the shop is busy" sub="every closed bill, counted in the hour it closed">
            <Skeleton h={132} radius={10} />
          </Card>
          <Card title="Selling more, selling less" sub="seven days against the seven before">
            <Skeleton h={132} radius={10} />
          </Card>
        </div>
      </div>

      <Card title="Far from usual" sub="a day or an hour a long way from its own baseline">
        <Skeleton h={104} radius={10} />
      </Card>
    </>
  );
}

/**
 * The "not enough history yet" state, designed rather than tolerated.
 *
 * On a counter installed last week most of this screen is in this state, so it
 * has to read as a wait rather than a fault: what it needs, how far along the
 * shop is, and the fact that nothing is being hidden.
 */
function Waiting({ block, what }: { block: ins.NotEnough; what: string }) {
  const have = Math.max(0, Math.min(block.days_of_history, block.days_needed));
  return (
    <div className="ins-wait">
      <div className="ins-wait-head">
        <span className="t">Not enough history yet</span>
        <span className="c tnum">{block.days_of_history} of {block.days_needed}</span>
      </div>
      <div className="ins-wait-track" role="img"
           aria-label={`${block.days_of_history} of ${block.days_needed} ${block.counting}`}>
        {Array.from({ length: block.days_needed }, (_, i) => (
          <span key={i} className={i < have ? 'on' : ''} />
        ))}
      </div>
      <p className="ins-wait-say">
        {what} needs {block.days_needed} {block.counting} and this counter has {block.days_of_history}.
      </p>
      <p className="ins-wait-why">
        No smaller version of this figure is shown. A comparison made against days that do not
        exist reads as a real change, and acting on one is worse than being told to wait.
      </p>
    </div>
  );
}

function BigStat({ label, value, sub, tone }: {
  label: string; value: string; sub: string; tone?: 'green';
}) {
  return (
    <div className={`ins-stat${tone ? ` ${tone}` : ''}`}>
      <span className="lbl">{label}</span>
      <span className="val">{value}</span>
      <span className="sub">{sub}</span>
    </div>
  );
}

/* --------------------------------------------------------------- helpers -- */

/**
 * Render integer paise.
 *
 * `rupees()` asserts at the boundary — it throws on a float, and on an amount
 * outside the range this till will price. Both of those are real signals and
 * neither should take the page down, so the fallback is the SERVER'S OWN rupee
 * string, which it derived the same way it derived the figure. A ninety-day
 * total on a busy counter can exceed that range legitimately; a float in a
 * price cannot, and in that case what appears is the server's string rather
 * than a browser-rounded number.
 */
function money(paise: number, fallback: string): string {
  try {
    return rupees(paise);
  } catch {
    return `₹${fallback}`;
  }
}

/** A width percentage for a bar. Geometry, not money — never used for a figure. */
function pctWidth(value: number, peak: number): number {
  if (peak <= 0) return 0;
  return Math.max(value > 0 ? 1.5 : 0, Math.round((value * 1000) / peak) / 10);
}

/** 63 -> "6.3". The server sends tenths because a float never touches money. */
function tenths(x: number): string {
  return `${Math.trunc(x / 10)}.${Math.abs(x % 10)}`;
}

/** '+0530' -> '+05:30'. */
function fmtOffset(z: string): string {
  return z.length === 5 ? `${z.slice(0, 3)}:${z.slice(3)}` : z;
}
