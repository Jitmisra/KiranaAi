import { useCallback, useEffect, useMemo, useState } from 'react';
import * as manage from '../lib/manageapi';
import {
  Button, Card, KV, Pill, Verdict, Segmented, Empty, Refusal, Insight, Fig, Skeleton, Thinking,
} from '../components/ui';
import { ShareSheet } from '../components/ShareSheet';
import '../styles/books.css';

/**
 * Billing history.
 *
 * There is no bills table anywhere in this system. Every row on this page is
 * rebuilt from the hash-chained audit log by walking it from genesis — which is
 * why the chain's own state is shown at the top rather than tucked in a corner.
 * A bill book derived from a chain that does not verify is not a bill book.
 *
 * THE AMBER LINES ARE THE POINT. When the counter cannot identify something it
 * excludes it from the total rather than guessing a price, and a history that
 * showed only the priced lines would hide the one thing the shopkeeper has to
 * check by hand. They appear in the list (as a count) and in the detail (in
 * full, with no price, because there is no price).
 *
 * EVERY FIGURE ON THE SUMMARY STRIP AND THE DAY HEADERS IS SUMMED HERE, in the
 * browser, in integer paise, from the rows actually loaded — and is labelled as
 * exactly that. Nothing is fetched twice, estimated, or extended to "the whole
 * chain": the chain's own totals live on the Settings screen where the chain
 * reports them itself.
 */

type Span = '25' | '50' | '200';

const SPANS: Array<{ value: Span; label: string; title: string }> = [
  { value: '25', label: '25', title: 'the last 25 bills' },
  { value: '50', label: '50', title: 'the last 50 bills' },
  { value: '200', label: '200', title: 'the last 200 bills' },
];

/** The chain block, worded for whichever page is showing it. */
function ChainState({ chain }: { chain: manage.Chain }) {
  if (chain.ok) return null;
  return (
    <Verdict tone="red" title="The audit chain does not verify">
      Bills are shown only up to the break — <b>{chain.lines_verified}</b> lines
      stood up to a re-walk from the beginning. Anything recorded after that
      point is not shown, because a line whose hash does not recompute is not
      evidence of anything. Nothing here has been adjusted or estimated to fill
      the gap.
      <br />
      <span className="mono">{chain.error}</span>
    </Verdict>
  );
}

/**
 * Settlement, in the colour it earned.
 *
 * GREEN IS `settled_by === 'webhook'` AND NOTHING ELSE, and that is a fix.
 * This read `if (bill.settled)`, which is not the same test: `bills_from`
 * accepts `kernel/intent.settled` as a FALLBACK when the webhook line is not in
 * the chain and labels it `settled_by: 'kernel'`. A bill in that state used to
 * get the same green PAID pill as one a signature-verified webhook matched —
 * on the one screen a shopkeeper scans down a column of them, where the colour
 * is the whole message and nobody opens the card to read the qualifier.
 *
 * Invariant 2 says only a signature-verified webhook may turn a bill green.
 * That money may well have arrived; the counter cannot witness that it did, and
 * amber is what this product says when it cannot witness something. The pill
 * names the gap rather than softening it, because "PAID" in amber would read as
 * a decorative choice.
 *
 * REFUSED is red because the money service declined to move money. LINK SENT
 * and NOT CHARGED are facts, not warnings, and dress accordingly.
 */
function Settlement({ bill }: { bill: manage.BillSummary }) {
  if (bill.settled && bill.settled_by === 'webhook') return <Pill tone="ok" dot>PAID</Pill>;
  if (bill.settled) return <Pill tone="amb">NO WEBHOOK</Pill>;
  if (bill.refused) return <Pill tone="bad">REFUSED</Pill>;
  if (bill.minted) return <span className="pill bk-quiet">LINK SENT</span>;
  return <span className="pill bk-ghost">NOT CHARGED</span>;
}

/** The time of day alone — the date lives on the day header above the row. */
function timeOf(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true });
}

function dayLabel(d: Date): string {
  const now = new Date();
  const midnight = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const daysAgo = Math.round((midnight(now) - midnight(d)) / 86_400_000);
  const name = d.toLocaleDateString('en-IN', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' });
  if (daysAgo === 0) return `Today — ${name}`;
  if (daysAgo === 1) return `Yesterday — ${name}`;
  return name;
}

interface DayGroup {
  key: string;
  label: string;
  /** True for bills whose timestamp could not be parsed — kept, never dropped. */
  undated: boolean;
  bills: manage.BillSummary[];
  total_paise: number;
  settled: number;
}

/**
 * Group bills by local day, preserving the order the server sent. The totals
 * are integer-paise sums of exactly the rows in the group. A bill whose
 * timestamp cannot be parsed goes into an "undated" group rather than being
 * guessed into a day it may not belong to.
 */
function groupByDay(bills: manage.BillSummary[]): DayGroup[] {
  const groups: DayGroup[] = [];
  const index = new Map<string, DayGroup>();
  for (const b of bills) {
    const d = b.at ? new Date(b.at) : null;
    const valid = d !== null && !Number.isNaN(d.getTime());
    const key = valid ? `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}` : 'undated';
    let g = index.get(key);
    if (!g) {
      g = {
        key,
        label: valid ? dayLabel(d) : 'Timestamp could not be read',
        undated: !valid,
        bills: [],
        total_paise: 0,
        settled: 0,
      };
      index.set(key, g);
      groups.push(g);
    }
    g.bills.push(b);
    g.total_paise += b.total_paise;
    // The day header's "N settled" is the same claim the strip makes, so it
    // takes the same test: a verified webhook, never the kernel fallback.
    if (b.settled && b.settled_by === 'webhook') g.settled += 1;
  }
  return groups;
}

/**
 * What the loaded rows add up to. Integer paise; counted, never estimated.
 *
 * THE SETTLED FIGURE COUNTS WEBHOOK SETTLEMENTS ONLY. A bill whose only
 * settlement record is `kernel/intent.settled` is counted in `unwitnessed` and
 * is NOT added to `settledPaise` — the summary strip calls that money "confirmed
 * by webhook", and a kernel-only bill folded into it would make the sentence
 * false by exactly its own amount. It is also not counted as awaiting: nothing
 * is waiting on a link that a kernel already recorded settling.
 */
function shownTotals(bills: manage.BillSummary[]) {
  let revenue = 0, settled = 0, settledPaise = 0, awaiting = 0, refused = 0, excluded = 0;
  let unwitnessed = 0, unwitnessedPaise = 0;
  for (const b of bills) {
    revenue += b.total_paise;
    excluded += b.excluded_lines;
    if (b.settled && b.settled_by === 'webhook') { settled += 1; settledPaise += b.total_paise; }
    else if (b.settled) { unwitnessed += 1; unwitnessedPaise += b.total_paise; }
    else if (b.refused) refused += 1;
    else if (b.minted) awaiting += 1;
  }
  return { revenue, settled, settledPaise, awaiting, refused, excluded,
    unwitnessed, unwitnessedPaise };
}

export default function History() {
  const [span, setSpan] = useState<Span>('50');
  const [body, setBody] = useState<manage.HistoryBody | null>(null);
  const [err, setErr] = useState<{ reason: string; detail?: string } | null>(null);
  const [loading, setLoading] = useState(true);

  const [openId, setOpenId] = useState<string | null>(null);
  /** The session whose bill is being drafted for WhatsApp, if any. */
  const [sharing, setSharing] = useState<string | null>(null);
  const [detail, setDetail] = useState<manage.BillDetail | null>(null);
  const [detailErr, setDetailErr] = useState<{ reason: string; detail?: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    const r = await manage.history({ limit: Number(span) });
    if (r.ok) { setBody(r); setErr(null); } else { setErr(r); setBody(null); }
    setLoading(false);
  }, [span]);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    if (!openId) { setDetail(null); setDetailErr(null); return; }
    let alive = true;
    void (async () => {
      const r = await manage.bill(openId);
      if (!alive) return;
      if (r.ok) { setDetail(r); setDetailErr(null); } else { setDetail(null); setDetailErr(r); }
    })();
    return () => { alive = false; };
  }, [openId]);

  const bills = body?.bills ?? [];
  const groups = useMemo(() => groupByDay(bills), [bills]);
  const sums = useMemo(() => shownTotals(bills), [bills]);

  return (
    <div>
      <div className="page-head">
        <h1>Billing history</h1>
        <p>
          Every bill this counter has closed, rebuilt from its own audit log. There is no separate
          record — if it is not in the chain, it did not happen here.
        </p>
      </div>

      {/* THE SERVER'S OWN REASON IS THE HEADING. It used to be filed under a
          sentence of ours — "The history could not be read" — with the
          machine's words demoted to the detail slot and its detail pushed down
          again into the hint, which is how a refusal stops being read as the
          product working and starts reading as our error message. */}
      {err && (
        <div style={{ marginBottom: 20 }}>
          <Refusal
            reason={err.reason}
            detail={err.detail}
            hint="No bill is shown from a cache in its place. Every row on this page is rebuilt from the chain when the page asks for it, so when the chain cannot be read there is nothing to fall back to."
            action={<Button size="sm" onClick={() => void load()}>TRY AGAIN</Button>}
          />
        </div>
      )}

      {body && !body.chain.ok && (
        <div style={{ marginBottom: 20 }}><ChainState chain={body.chain} /></div>
      )}

      {loading ? (
        <BookWaiting span={span} />
      ) : body && bills.length > 0 ? (
        <div className="bk-strip-wrap">
          {/* WHAT THESE BILLS COME TO, AND WHAT IS NOT IN THAT FIGURE. The
              strip below breaks the same sums into their parts; this says them
              in one sentence, with the omissions named rather than left for a
              caption to carry. Amber is the excluded lines because that is the
              counter abstaining; green is the settled money because a
              signature-verified webhook stands behind it and nothing else on
              this page may borrow either. */}
          <Insight
            tag="the bills below"
            foot={`summed in this browser, in integer paise, from exactly the ${bills.length} bill${bills.length === 1 ? '' : 's'} loaded below — never extended to the rest of the chain`}
          >
            <Fig>{manage.money(sums.revenue)}</Fig> across{' '}
            <Fig tone="ink">{bills.length}</Fig> bill{bills.length === 1 ? '' : 's'}.{' '}
            {sums.settled > 0 ? (
              <>
                <Fig tone="green">{manage.money(sums.settledPaise)}</Fig> of it settled, on a
                signature-verified webhook.
              </>
            ) : (
              <>None of it has settled: no verified webhook has matched any of these.</>
            )}{' '}
            {sums.unwitnessed > 0 && (
              <>
                A further <Fig tone="amber">{manage.money(sums.unwitnessedPaise)}</Fig> is
                recorded settled by the payment kernel with no webhook line behind it, and is
                not in the figure above.{' '}
              </>
            )}
            {sums.excluded > 0 || body.matched > bills.length ? (
              <>
                Not in that figure:{' '}
                {sums.excluded > 0 && (
                  <>
                    <Fig tone="amber">{sums.excluded}</Fig> line
                    {sums.excluded === 1 ? '' : 's'} the counter would not price
                    {body.matched > bills.length ? ', and ' : '.'}
                  </>
                )}
                {body.matched > bills.length && (
                  <>
                    the <Fig tone="ink">{body.matched - bills.length}</Fig> older bill
                    {body.matched - bills.length === 1 ? '' : 's'} on this chain that are not
                    loaded here.
                  </>
                )}
              </>
            ) : (
              <>
                Nothing is missing from it: no line was left unpriced, and these are every bill
                the chain holds.
              </>
            )}
          </Insight>
          <div className="bk-strip">
            <div className="bk-stat">
              <span className="l">Bills shown</span>
              <span className="n">{bills.length}</span>
              <span className="s">of {body.matched} closed on the chain</span>
            </div>
            <div className="bk-stat">
              <span className="l">Revenue shown</span>
              <span className="n">{manage.money(sums.revenue)}</span>
              <span className="s">the {bills.length} totals below, summed here</span>
            </div>
            <div className="bk-stat">
              <span className="l">Settled</span>
              <span className="n">{sums.settled}</span>
              <span className="s">
                {sums.settled > 0
                  ? `${manage.money(sums.settledPaise)} confirmed by webhook`
                  : 'no verified webhook among these'}
              </span>
            </div>
            <div className="bk-stat">
              <span className="l">Link sent, unpaid</span>
              <span className="n">{sums.awaiting}</span>
              <span className="s">a link exists; nothing has settled it</span>
            </div>
            {/* NEVER FOLDED INTO "SETTLED", AND NEVER HIDDEN. A bill the
                payment kernel recorded settling with no webhook line in the
                chain is money that may have arrived and cannot be witnessed.
                It gets its own tile rather than a footnote, because the tile
                beside it says "confirmed by webhook" and this money is not. */}
            {sums.unwitnessed > 0 && (
              <div className="bk-stat">
                <span className="l">Settled, no webhook</span>
                <span className="n">{sums.unwitnessed}</span>
                <span className="s">
                  {manage.money(sums.unwitnessedPaise)} the kernel recorded and this chain
                  cannot witness
                </span>
              </div>
            )}
            {sums.refused > 0 && (
              <div className="bk-stat">
                <span className="l">Refused</span>
                <span className="n">{sums.refused}</span>
                <span className="s">the money service declined to mint</span>
              </div>
            )}
          </div>
          <p className="bk-cap">
            Summed in the browser from the {bills.length} bills loaded below — not the whole chain.
            “Settled” means a signature-verified webhook and nothing else.
          </p>
        </div>
      ) : null}

      <div className="grid two">
        <Card
          title="Bills"
          aside={<Segmented<Span> value={span} onChange={setSpan} options={SPANS} />}
        >
          {loading ? (
            <BillsWaiting />
          ) : !body ? (
            /* No second TRY AGAIN here on purpose: this state is only ever
               reached with the refusal above it on screen, and that refusal
               already carries the button. Two controls with one accessible
               name is a screen a keyboard cannot describe. */
            <Empty title="No bills are loaded">
              The chain was not read, so nothing is listed. The reason the counter gave is at the
              top of this screen, in its own words, with the button that asks again.
            </Empty>
          ) : bills.length === 0 ? (
            <Empty
              title={body.chain.exists
                ? 'No bill has been closed on this counter yet'
                : 'This counter has no audit log yet'}
              action={<a className="btn sm" href="#/till">OPEN THE TILL</a>}
            >
              {body.chain.exists
                ? 'A bill appears here the moment its basket closes — paid or not.'
                : 'The first bill will create one, and it will appear here the moment its basket closes — paid or not.'}
            </Empty>
          ) : (
            <div className="bh-list">
              {groups.map((g) => (
                <section className="bh-sec" key={g.key}>
                  <header className="bh-day">
                    <span className="d">{g.label}</span>
                    <span className="c">
                      {g.bills.length} {g.bills.length === 1 ? 'bill' : 'bills'}
                      {g.settled > 0 && <> · {g.settled} settled</>}
                    </span>
                    <span className="t">{manage.money(g.total_paise)}</span>
                  </header>
                  {g.bills.map((b) => (
                    <button
                      type="button"
                      key={b.session_id}
                      className={`bh-row${openId === b.session_id ? ' open' : ''}`}
                      aria-expanded={openId === b.session_id}
                      onClick={() => setOpenId(b.session_id === openId ? null : b.session_id)}
                    >
                      <span className="tm">{g.undated ? manage.when(b.at) : timeOf(b.at)}</span>
                      <span className="what">
                        <span className="items">
                          {b.items.length === 0 ? (
                            <span className="muted">nothing priced</span>
                          ) : (
                            b.items.map((l) => l.sku_id).join(', ')
                          )}
                        </span>
                        <span className="sid">{b.session_id}</span>
                        {b.excluded_lines > 0 && (
                          <span className="bh-excl">
                            <Pill tone="amb">{b.excluded_lines} NOT ON THE BILL</Pill>
                          </span>
                        )}
                      </span>
                      <span className="right">
                        <span className="amt">{manage.money(b.total_paise)}</span>
                        <Settlement bill={b} />
                      </span>
                    </button>
                  ))}
                </section>
              ))}
            </div>
          )}

          {body && (
            <p className="hint">
              Showing {body.count} of {body.matched} closed {body.matched === 1 ? 'bill' : 'bills'};
              the chain holds {body.sessions_in_ledger} sessions in total, most of which never
              reached a total to charge.
              {body.unparsed_timestamps > 0 && (
                <> {body.unparsed_timestamps} of them carry a timestamp that could not be read, and
                are kept in the list rather than dropped.</>
              )}
            </p>
          )}
        </Card>

        <div className="bh-aside">
          <BillCard id={openId} detail={detail} err={detailErr} onShare={setSharing} />
        </div>
      </div>

      <ShareSheet
        open={sharing !== null}
        onClose={() => setSharing(null)}
        target={{ kind: 'receipt', sessionId: sharing ?? '' }}
      />
    </div>
  );
}

/* ------------------------------------------------------------- the wait -- */

/**
 * THE HEADLINE AND THE STRIP, WAITING.
 *
 * Every row on this page is rebuilt by walking a hash chain from its genesis
 * line, which is not instant on a counter with a few thousand lines in it. What
 * used to fill that time was a single 92-pixel grey slab, and what replaced the
 * slab was a sentence, five figures and a caption — so the whole page moved.
 * The panel here is the same mesh the sentence lands in, and the strip is drawn
 * at the strip's own shape.
 */
function BookWaiting({ span }: { span: Span }) {
  return (
    <div className="bk-strip-wrap">
      <Thinking
        title="Rebuilding the bill book from the chain"
        steps={[
          { label: 'walking the hash-chained audit log from its genesis line' },
          { label: `gathering the last ${span} sessions that reached a total to charge` },
          { label: 'reading which of them a signature-verified webhook settled' },
        ]}
        foot="There is no bills table behind this page. Nothing is listed until the walk finishes, because a bill book rebuilt from half a chain is not a bill book."
      />
      <div className="bk-strip bh-skel">
        {[0, 1, 2, 3].map((i) => (
          <div className="bk-stat" key={i} aria-hidden="true">
            <Skeleton w="72%" h={9} radius={999} />
            <Skeleton className="s-n" w="56%" h={22} />
            <Skeleton w="88%" h={9} radius={999} />
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * The list, waiting: a day header and the rows under it, at the shape they
 * arrive in. The money blank sits hard right, where the figure that replaces
 * it sits, so nothing slides across the row when the bills land.
 */
function BillsWaiting() {
  return (
    <div className="bh-list bh-skel" role="status" aria-label="Reading the bills from the chain">
      <section className="bh-sec">
        <header className="bh-day" aria-hidden="true">
          <Skeleton w={172} h={11} radius={999} />
          <Skeleton w={62} h={9} radius={999} />
          <Skeleton className="s-t" w={78} h={11} radius={999} />
        </header>
        {[0, 1, 2, 3, 4].map((i) => (
          <div className="bh-row" key={i} aria-hidden="true">
            <span className="tm"><Skeleton w={46} h={10} radius={999} /></span>
            <span className="what">
              <Skeleton w={`${76 - i * 7}%`} h={11} radius={999} />
              <Skeleton w="44%" h={9} radius={999} />
            </span>
            <span className="right">
              <Skeleton w={72} h={14} radius={999} />
              <Skeleton w={58} h={10} radius={999} />
            </span>
          </div>
        ))}
      </section>
    </div>
  );
}

/**
 * One bill in full.
 *
 * The excluded items get their own block, above the fold, in amber. They are
 * not an error state — they are the counter declining to charge for something
 * it could not name, and the shopkeeper needs to see them to decide whether to
 * ring that item up by hand.
 */
function BillCard({ id, detail, err, onShare }: {
  id: string | null;
  detail: manage.BillDetail | null;
  err: { reason: string; detail?: string } | null;
  /** Opens the share sheet for a session. Owned by the screen, not the card. */
  onShare: (sessionId: string) => void;
}) {
  if (!id) {
    return (
      <Card title="One bill">
        <div className="bk-hintstate">
          <span className="eyebrow">No bill open</span>
          {/* "on the left" was only true on a laptop: under 1081px this panel
              sits UNDER the list, and an instruction that points at the wrong
              side of a phone is not an instruction. */}
          <p>
            Pick a bill from the list and it opens here in full — every priced line, every line
            the counter refused to price, and the chain events behind both.
          </p>
        </div>
      </Card>
    );
  }
  if (err) {
    return (
      <Card title="One bill">
        <Refusal reason={err.reason} detail={err.detail} />
      </Card>
    );
  }
  if (!detail) {
    return (
      <Card title="One bill">
        {/* The bill's own shape while it is fetched: lines, then the rule and
            the total, then the four things the counter recorded. The panel
            used to be one 220px block, so every line landed somewhere the eye
            had not been waiting. */}
        <div className="bill bh-skel" role="status" aria-label="Reading this bill from the chain">
          <div className="bill-lines" aria-hidden="true">
            {[0, 1, 2].map((i) => (
              <div className="bill-line" key={i}>
                <Skeleton w={`${58 - i * 9}%`} h={11} radius={999} />
                <Skeleton className="s-amt" w={64} h={11} radius={999} />
              </div>
            ))}
          </div>
          <div className="bill-total" aria-hidden="true">
            <Skeleton w={44} h={9} radius={999} />
            <Skeleton className="s-amt" w={104} h={22} />
          </div>
        </div>
        <div className="bh-skel" aria-hidden="true">
          {[0, 1, 2, 3].map((i) => (
            <div className="kv" key={i}>
              <Skeleton w={76} h={10} radius={999} />
              <Skeleton w={i === 3 ? 154 : 118} h={10} radius={999} />
            </div>
          ))}
        </div>
      </Card>
    );
  }

  return (
    <Card
      title="One bill"
      aside={detail.settled && detail.settled_by === 'webhook'
        ? <Pill tone="ok" dot>PAID</Pill>
        : detail.settled
          ? <Pill tone="amb">NO WEBHOOK</Pill>
          : <span className="pill bk-quiet">{detail.state ?? 'not settled'}</span>}
    >
      <div className="bill">
        <div className="bill-lines">
          {detail.line_items.length === 0 ? (
            <Empty icon={false}>
              Nothing on this bill was priced. Every line this session did write is at the foot
              of this card, in the order the chain recorded it.
            </Empty>
          ) : detail.line_items.map((l) => (
            <div className="bill-line" key={l.item_id}>
              <span className="nm">{l.sku_id}</span>
              <span className="amt">
                {typeof l.price_paise === 'number' ? manage.money(l.price_paise) : '—'}
              </span>
            </div>
          ))}
        </div>
        <div className="bill-total">
          <span className="lbl">Total</span>
          <span className="amt">{manage.money(detail.total_paise)}</span>
        </div>
      </div>

      {!detail.total_agrees && (
        <Verdict tone="red" title="The lines do not add up to the total">
          These lines come to {manage.money(detail.lines_sum_paise)} and the counter recorded{' '}
          {manage.money(detail.total_paise)}. Both figures are read from the same chain and neither has
          been adjusted to match the other.
        </Verdict>
      )}

      {detail.excluded.length > 0 && (
        <Verdict
          tone="amber"
          title={`${detail.excluded.length} ${detail.excluded.length === 1 ? 'item was' : 'items were'} left off this bill`}
        >
          The counter could not name {detail.excluded.length === 1 ? 'it' : 'them'} closely enough to
          put a price on {detail.excluded.length === 1 ? 'it' : 'them'}, so{' '}
          {detail.excluded.length === 1 ? 'it was' : 'they were'} excluded from the total rather than
          guessed at. There is no price to show below because none was ever decided.
          {/* SPANS, not divs: `Verdict` renders its children inside a <p>, and a
              <div> there is invalid HTML — the browser closes the paragraph and
              lifts the block out of the amber box it belongs in. The grid comes
              from .ledger-line, which does not care what element carries it. */}
          <span className="excl-lines">
            {detail.excluded.map((l) => (
              <span className="ledger-line amber" key={l.item_id}>
                <span className="nm">{l.sku_id}</span>
                <span className="why">{(l.reason ?? '').replace(/_/g, ' ')}</span>
                <span className="amt">not charged</span>
              </span>
            ))}
          </span>
        </Verdict>
      )}

      <div className="eyebrow" style={{ marginTop: 16 }}>What the counter recorded</div>
      <KV k="opened">{manage.when(detail.opened_at)}</KV>
      <KV k="basket closed">{detail.closed ? manage.when(detail.at) : 'never — this was not billed'}</KV>
      <KV k="state">{detail.state ?? '—'}</KV>
      <KV k="session"><span className="mono">{detail.session_id}</span></KV>
      {/* Sending a bill is a thing a customer asks for AFTER they have gone —
          "woh bill bhej do". The sheet drafts it from this session's own
          recorded lines; the shopkeeper reads it before it goes. */}
      <button className="btn sm" style={{ marginTop: 10 }} onClick={() => onShare(detail.session_id)}>
        SEND THIS BILL
      </button>
      {detail.payment_link_id && (
        <KV k="payment link"><span className="mono">{detail.payment_link_id}</span></KV>
      )}
      {detail.payment_id && (
        <KV k="payment"><span className="mono">{detail.payment_id}</span></KV>
      )}

      {/* THE COLOUR FOLLOWS THE EVIDENCE, NOT THE FLAG. This block was one
          green Verdict headed "Settled" with the kernel-only case explained
          inside it as "the weaker of the two records" — green and the word
          settled, awarded to a bill this chain cannot witness. The words were
          right and the frame around them was making the opposite claim. */}
      {detail.settled && detail.settled_by === 'webhook' ? (
        <Verdict tone="green" title={`Settled — ${manage.money(detail.total_paise)}`}>
          A signature-verified webhook matched this session at {manage.when(detail.settled_at)}.
          That is the only thing in this system that can turn a bill green.
        </Verdict>
      ) : detail.settled ? (
        <Verdict tone="amber" title="Recorded settled, with no webhook to witness it">
          The payment kernel recorded a settlement at {manage.when(detail.settled_at)} and the
          webhook line is not in this chain. The money may well have arrived — this counter
          cannot show that it did, and only a signature-verified webhook is allowed to say so
          here. It is counted apart from the settled figure on this screen, and it is still
          in what is owed.
        </Verdict>
      ) : detail.minted ? (
        <Verdict tone="amber" title="A payment link was issued and nothing has settled it">
          The link exists. No signature-verified webhook has matched this session, so this bill is
          not paid — whatever the customer's phone may have shown them.
        </Verdict>
      ) : null}

      {detail.refusals.length > 0 && (
        <>
          <div className="eyebrow" style={{ marginTop: 16 }}>Refused</div>
          {detail.refusals.map((r, i) => (
            <div className="ledger-line" key={`${r.ts}-${i}`}>
              <span className="nm">{(r.reason ?? 'refused').replace(/_/g, ' ')}</span>
              <span className="why">{r.module} · {manage.when(r.ts)}</span>
              <span className="amt">
                {typeof r.requested_paise === 'number' ? manage.money(r.requested_paise) : ''}
              </span>
            </div>
          ))}
          <p className="hint">
            A refusal is the money service declining to mint, not a crash. It re-prices every
            witness from its own tables and will not issue a link when the two disagree.
          </p>
        </>
      )}

      <details className="timeline">
        <summary>Every line this session wrote to the chain ({detail.events.length})</summary>
        <div className="scroll-x">
          <table className="moments">
            <tbody>
              {detail.events.map((e, i) => (
                <tr key={`${e.ts}-${i}`}>
                  <td className="mono">{manage.when(e.ts)}</td>
                  <td className="mono">{e.module}·{e.event}</td>
                  <td>{(e.reason ?? '').replace(/_/g, ' ')}</td>
                  <td className="mono">{e.from && e.to ? `${e.from} → ${e.to}` : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </Card>
  );
}
