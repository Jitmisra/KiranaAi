import { useCallback, useEffect, useState } from 'react';
import * as m from '../lib/manageapi';
import * as db from '../lib/daybookapi';
import { useT, type Translator } from '../lib/i18n';
import { rupees } from '../lib/money';
import {
  Button, Card, KV, Pill, Verdict, Empty, Refusal, Insight, Fig, Skeleton, Thinking,
} from '../components/ui';
import '../styles/today.css';

/**
 * TODAY — "aaj kitna hua?"
 *
 * The question a shopkeeper actually asks at the end of a shift, and the one
 * screen this product never had. Every number here is counted from the
 * hash-chained audit log for this calendar day, in this counter's own
 * timezone, by the server — nothing cached and nothing estimated.
 *
 * YESTERDAY IS SHOWN BESIDE TODAY AND NEVER DIVIDED INTO IT. Both figures are
 * the same derivation asked about two windows, which is what makes them
 * comparable at all — but one window is finished and the other is being traded
 * in, and a percentage across that boundary is at its largest at five past
 * midnight and falls all day whatever the shop does. A delta between two
 * differently-shaped numbers is a random number with a percent sign, and this
 * screen used to print one. The Insights screen makes this comparison properly,
 * because gawaah.insights cuts the earlier day at the same time of day;
 * `/manage/today` sends no such cut and no delta, and the browser is not
 * entitled to invent either.
 *
 * The one rule that shapes the layout: the settled figure is the only money
 * that is REAL. Bills whose link was sent but whose webhook never arrived are
 * shown as what they are — awaited — and the webhook liveness card sits on
 * this screen precisely because "nobody paid today" and "payments cannot
 * reach this counter" look identical from the till.
 *
 * TWO ENDPOINTS, AND THE SECOND ONE IS THE POINT
 * ----------------------------------------------
 * `/manage/today` counts BILLS. It is correct and it cannot answer the
 * questions a shopkeeper loses money to, because every one of those is about a
 * gap between two books:
 *
 *   * A bill that closed with no payment link ever minted. The day brief files
 *     it under "awaiting the gateway", and this screen printed that as "N links
 *     sent, not settled" — a sentence about links that were never sent. On this
 *     counter's own chain the day this was fixed: 238 bills closed, 233 links
 *     minted, 5 bills nothing was ever asked of the gateway for.
 *   * A webhook that ARRIVED AND WAS REFUSED. `paisa /health` reports what its
 *     current process has seen, so a money service restarted an hour ago says
 *     it has seen none while the chain holds fifteen, eleven of them posts
 *     whose signature did not check out. This screen used to print "No webhook
 *     has reached this counter" over exactly that.
 *   * A mint the gateway errored on, and a basket the counter refused to
 *     charge. Neither was anywhere on these books.
 *
 * So `/daybook/reconcile` is asked as well, and what it says is drawn BESIDE
 * the day brief, never folded into it. Nothing on this screen is netted out: a
 * disagreement between what the till billed and what the gateway did is the
 * most valuable thing this counter can say, and the moment one figure is
 * adjusted to agree with another it stops being sayable.
 *
 * AN EMPTY DAY IS DRAWN AS AN EMPTY DAY. It used to render four tiles reading
 * ₹0.00 under the words "billed today", which is a zero wearing the clothes of
 * a measurement. Nothing was billed is a state, and it is drawn as one.
 */

const REFRESH_MS = 30_000;

export default function Today() {
  const [body, setBody] = useState<m.TodayBody | null>(null);
  const [rec, setRec] = useState<db.ReconcileBody | null>(null);
  /** Why the reconciliation is missing, when it is. Never a blank space: the
      panel it feeds is the one that says what does not add up, and a silently
      absent "nothing disagrees" reads exactly like "everything agrees". */
  const [recErr, setRecErr] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<{ reason: string; detail?: string } | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    /* Both asked at once, and the day brief is NOT held hostage to the
       reconciliation: they come off the same chain but from two routers, and a
       shopkeeper whose /daybook mount is missing should still see his day. */
    const [r, q] = await Promise.all([m.today(), db.reconcile()]);
    if (r.ok) { setBody(r as unknown as m.TodayBody); setRefusal(null); }
    else setRefusal({ reason: r.reason, detail: r.detail });
    if (q.ok) { setRec(q as db.ReconcileBody); setRecErr(null); }
    else { setRec(null); setRecErr(q.detail ? `${q.reason} — ${q.detail}` : q.reason); }
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
    const id = setInterval(() => void load(), REFRESH_MS);
    return () => clearInterval(id);
  }, [load]);

  /** Ask again after a refusal. The refusal is cleared first, so the wait is
      the same drawn wait as the first load rather than a dead button. */
  const retry = useCallback(() => {
    setRefusal(null);
    setLoading(true);
    void load();
  }, [load]);

  const t = body?.today ?? null;
  const y = body?.yesterday ?? null;

  /* THE EMPTY DAY IS DECIDED BY THE BILL COUNT, not by the revenue. A day of
     baskets that all came to nothing is not an empty day, and a day whose only
     bill was refused still has something to show. */
  const nothingBilled = t !== null && t.bills === 0;

  /* THE SETTLED FIGURE, TAKEN FROM THE STRICTER OF THE TWO SOURCES.
     `/manage/today` counts `b["settled"]`, and `bills_from` sets that flag for
     `kernel/intent.settled` as a fallback when the webhook line is not in the
     chain — labelling it `settled_by: "kernel"` but setting the same flag. So
     the day brief's settled figure can include money no signature-verified
     webhook stands behind, under a tile that calls it REAL MONEY. Invariant 2
     says only the webhook may say that. The reconciliation splits the two, so
     it is used where it is available; without it the brief's figure stands and
     the tile drops the word that claims a webhook. */
  const settledPaise = rec ? rec.today.settled.paise : (t?.settled_paise ?? 0);
  const settledCount = rec ? rec.today.settled.bills : (t?.settled_count ?? 0);
  const unwitnessed = rec ? rec.today.settled_unwitnessed : null;

  return (
    <div className="stack">
      <div className="page-head">
        <h1>Today</h1>
        <p>
          What happened at this counter today, counted line by line from its own audit chain.
          Nothing on this page is a running total and nothing is estimated.
        </p>
      </div>

      {refusal ? (
        <Refusal
          reason={refusal.reason}
          detail={refusal.detail}
          hint="Nothing is shown in its place. Every figure here is counted on request, so when the count cannot be made there is no smaller version of it to print."
          action={<Button size="sm" onClick={retry}>TRY AGAIN</Button>}
        />
      ) : loading || !t ? (
        <TodayWaiting />
      ) : nothingBilled ? (
        <NothingBilled body={body!} rec={rec} recErr={recErr} yesterday={y} />
      ) : (
        <>
          {/* THE DAY, AS A SENTENCE. The same four figures are in the tiles
              below; this is them read aloud, with the numbers carrying the
              emphasis and the words receding — and with the source line under
              it, because on this product a figure without its derivation is
              the thing the whole counter exists to refuse. */}
          <Insight
            tag="the day so far"
            foot={`counted from this counter's own hash-chained audit log, midnight to midnight on its own clock${
              y && y.bills > 0 ? ' · today is a part day and yesterday is a whole one, so the two are set side by side and never divided into a percentage' : ''}${
              t.excluded_lines > 0 ? ` · ${t.excluded_lines} line${t.excluded_lines === 1 ? '' : 's'} the counter would not price are excluded` : ''}`}
          >
            <Fig>{rupees(t.revenue_paise)}</Fig> billed today across{' '}
            <Fig tone="ink">{t.bills}</Fig> bill{t.bills === 1 ? '' : 's'}.{' '}
            {settledCount > 0 ? (
              /* GREEN ONLY WHEN THE RECONCILIATION ANSWERED. Without it this
                 figure is the day brief's, and the brief counts
                 `kernel/intent.settled` as settled — so it can hold money no
                 signature-verified webhook stands behind. Measured on a scratch
                 counter carrying one webhook-settled bill (Rs 10) and one
                 kernel-only one (Rs 40): with the reconciliation blocked this
                 sentence read "Rs 50.00 of it has settled" IN GREEN. Invariant
                 2 gives the webhook alone the right to say that and invariant 6
                 gives it the colour, so when the check is unavailable the
                 figure keeps the neutral accent and the word "recorded" — the
                 weaker sentence is the true one. */
              <><Fig tone={rec ? 'green' : 'ink'}>{rupees(settledPaise)}</Fig>{' '}
              {rec ? 'of it has settled.' : 'of it is recorded settled — unverified here.'}</>
            ) : (
              <>None of it has settled yet.</>
            )}
            {/* Named in the same breath, never inside the same figure. */}
            {unwitnessed && unwitnessed.bills > 0 && (
              <> A further <Fig tone="amber">{rupees(unwitnessed.paise)}</Fig> is recorded
              settled with no webhook to witness it, and is not in that figure.</>
            )}
            {y && y.bills > 0 && (
              <> Yesterday finished on <Fig tone="ink">{rupees(y.revenue_paise)}</Fig>.</>
            )}
          </Insight>

          <div className="brief-grid">
            <BriefStat
              label="billed today"
              value={rupees(t.revenue_paise)}
              sub={`${t.bills} bill${t.bills === 1 ? '' : 's'}`}
              delta={y && y.bills > 0 ? `yesterday, all day: ${rupees(y.revenue_paise)}` : null}
            />
            <BriefStat
              label={rec ? 'settled — real money' : 'settled — as the day brief counts it'}
              value={rupees(settledPaise)}
              sub={rec
                ? `${settledCount} of ${t.bills} bills, on a verified webhook`
                : `${settledCount} of ${t.bills} bills — no webhook check available`}
              /* The label already drops the claim; the COLOUR has to drop it
                 too. Green is reserved for money a signature-verified webhook
                 settled, and the brief's figure is not that figure. */
              tone={rec && settledCount > 0 ? 'green' : undefined}
              delta={unwitnessed && unwitnessed.bills > 0
                ? `not in this: ${rupees(unwitnessed.paise)} settled with no webhook line`
                : null}
            />
            {/* "AWAITING" USED TO MEAN "EVERY BILL THAT IS NOT SETTLED", and
                the tile said those were links that had been sent. The day
                brief's `awaiting_count` is `bills − settled`: it includes a
                bill that closed with no link ever minted, which is not waiting
                on the gateway because nothing was ever asked of it. The
                reconciliation splits the two, so the tile now states the part
                it can name and the panel below names the rest. When the
                reconciliation is unavailable the tile drops back to the day
                brief's figure and to WORDS THAT DO NOT CLAIM A LINK — the
                weaker sentence is the true one either way. */}
            <BriefStat
              label="awaiting the gateway"
              value={rupees(rec ? rec.today.awaiting.paise : t.awaiting_paise)}
              sub={rec
                ? `${rec.today.awaiting.bills} link${rec.today.awaiting.bills === 1 ? '' : 's'} sent, not settled`
                : `${t.awaiting_count} bill${t.awaiting_count === 1 ? '' : 's'} the gateway has not confirmed`}
              delta={rec && rec.today.never_asked.bills > 0
                ? `not in this: ${rec.today.never_asked.bills} bill${rec.today.never_asked.bills === 1 ? '' : 's'} with no link at all`
                : null}
            />
            {/* AN AVERAGE OF NO BILLS IS NOT ZERO RUPEES, IT IS NOTHING. The
                server sends 0 for the empty day because a paise field has to
                carry an integer; printing it beside the words "average bill"
                would state a figure nothing derived. The em-dash is the same
                answer the Insights screen gives for a median it cannot take. */}
            <BriefStat
              label="average bill"
              value={t.bills > 0 ? rupees(t.average_paise) : '—'}
              sub={t.bills > 0
                ? `first ${clock(t.first_bill_at)} · last ${clock(t.last_bill_at)}`
                : 'no bills yet — an average needs one'}
            />
          </div>

          <Reconciliation rec={rec} err={recErr} />

          <div className="grid two">
            <div className="stack">
              <Card
                title="What sold most"
                aside={t.excluded_lines > 0 && (
                  <Pill tone="amb">{t.excluded_lines} line{t.excluded_lines === 1 ? '' : 's'} excluded</Pill>
                )}
              >
                {body!.top_sellers.length === 0 ? (
                  <Empty
                    title="Nothing has crossed the counter today"
                    action={<a className="btn sm" href="#/till">OPEN THE TILL</a>}
                  >
                    The first bill will appear here the moment it closes — this list is counted
                    from the same chain as every figure above it, so it fills itself.
                  </Empty>
                ) : (
                  <div className="sellers">
                    {body!.top_sellers.map((s, i) => (
                      <div className="seller" key={s.sku_id}>
                        <span className="rank">{i + 1}</span>
                        <span className="nm">
                          {s.name}
                          {!s.still_in_catalogue && (
                            <span className="muted"> — no longer in the catalogue</span>
                          )}
                        </span>
                        <span className="units">{s.units}×</span>
                        <span className="amt">{rupees(s.revenue_paise)}</span>
                      </div>
                    ))}
                  </div>
                )}
                {t.excluded_lines > 0 && (
                  <p className="hint">
                    {t.excluded_lines === 1 ? 'One line was' : `${t.excluded_lines} lines were`} seen
                    on the counter and excluded from a total because the counter could not price
                    {t.excluded_lines === 1 ? ' it' : ' them'} confidently. Abstaining is the
                    product working; the history screen names each one.
                  </p>
                )}
              </Card>

              {/* THE CARD IS ALWAYS HERE, INCLUDING WHEN THERE IS NOTHING TO
                  COMPARE AGAINST. It used to vanish on a day after a day that
                  took nothing, which left the sentence at the top of the screen
                  quietly missing its "on yesterday" clause and no word anywhere
                  about why. A comparison this counter cannot make is a state,
                  and it says so in the same words the Insights screen uses. */}
              {y && (y.bills > 0 ? (
                <Card title="Against yesterday" tight>
                  <KV k="yesterday billed">{rupees(y.revenue_paise)} <span className="muted">across {y.bills} bill{y.bills === 1 ? '' : 's'}</span></KV>
                  <KV k="yesterday settled">{rupees(y.settled_paise)}</KV>
                  <p className="hint">
                    Both sides of this comparison are the same derivation, asked about two windows
                    of the same chain — never a cached delta.
                  </p>
                  <p className="hint">
                    They are set side by side and not turned into a percentage. Yesterday is a
                    finished day and today is not, so a share of one over the other would be at
                    its largest just after midnight and shrink all day whatever the shop did. The
                    Insights screen makes this comparison properly, by cutting the earlier day at
                    the same time of day.
                  </p>
                </Card>
              ) : (
                <Card title="Against yesterday" tight>
                  <Empty title="Nothing was billed yesterday" icon={false}>
                    No change is shown against it, and none is shown in the sentence above either.
                    A percentage measured from a day that took nothing is not a comparison, and
                    no smaller version of it is printed here.
                  </Empty>
                </Card>
              ))}
            </div>

            <div className="stack">
              <WebhookCard
                w={body!.webhook}
                awaiting={rec ? rec.today.awaiting.bills : t.awaiting_count}
                settledToday={settledCount}
                rec={rec}
              />
              <Card title="Where these numbers come from" tight>
                <KV k="source">the hash-chained audit log</KV>
                <KV k="chain"><ChainPill chain={body!.chain} /></KV>
                <KV k="window">midnight to midnight, this counter's clock ({body!.date})</KV>
                <p className="hint">{body!.derived_from}</p>
              </Card>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ pieces -- */

/**
 * THE WAIT, AT THE SHAPE OF THE SCREEN THAT IS COMING.
 *
 * This screen used to render one small card saying "Reading the chain…" and
 * then, a walk of the whole audit log later, replace it with a page — a
 * headline sentence, four tiles and two columns of cards. Everything below the
 * fold jumped. What is drawn here occupies the same boxes the figures will:
 * the mesh panel where the sentence goes, four tiles at the tiles' size, and
 * the two cards at theirs.
 *
 * BLUE, AND ONLY BLUE. A skeleton is the machine working, and green, amber and
 * red on this counter mean a payment settled, an abstention and a refusal. A
 * settled tile that arrived green out of a green wait would have been announced
 * before it was counted.
 */
function TodayWaiting() {
  return (
    <>
      <Thinking
        title="Counting today from the chain"
        steps={[
          { label: 'walking the hash-chained audit log from its genesis line' },
          { label: "gathering every bill that closed inside this counter's own day" },
          { label: 'asking the same question of yesterday, for the comparison' },
        ]}
        foot="Nothing is drawn until the walk finishes. A total counted half way down a chain is not a smaller truth, it is a wrong number."
      />

      <div className="brief-grid tdy-skel">
        {[0, 1, 2, 3].map((i) => (
          <div className="brief-stat" key={i} aria-hidden="true">
            <Skeleton w="62%" h={9} radius={999} />
            <Skeleton className="s-val" w="74%" h={26} />
            <Skeleton w="48%" h={9} radius={999} />
          </div>
        ))}
      </div>

      <div className="grid two">
        <div className="stack">
          <Card title="What sold most">
            <div className="sellers tdy-skel" role="status" aria-label="Counting what sold most">
              {[0, 1, 2, 3, 4].map((i) => (
                <div className="seller" key={i} aria-hidden="true">
                  <Skeleton className="s-rank" w={22} h={22} radius={6} />
                  <Skeleton className="s-nm" h={11} radius={999} />
                  <Skeleton className="s-units" w={30} h={11} radius={999} />
                  {/* Right-aligned, because the figure that replaces it is. */}
                  <Skeleton className="s-amt" w={72} h={11} radius={999} />
                </div>
              ))}
            </div>
          </Card>
        </div>
        <div className="stack">
          <Card title="Can a payment land here?">
            <Skeleton h={78} radius={10} />
          </Card>
          <Card title="Where these numbers come from" tight>
            <div className="tdy-skel" aria-hidden="true">
              {[0, 1, 2].map((i) => (
                <div className="kv" key={i}>
                  <Skeleton w={92} h={10} radius={999} />
                  <Skeleton w={i === 2 ? 168 : 120} h={10} radius={999} />
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </>
  );
}

/**
 * WHAT DOES NOT ADD UP.
 *
 * The most valuable panel on this screen, and the one it did not have. Every
 * row is a condition `gawaah/daybook.py` found true on the chain — this file
 * decides how loud each one looks, never whether it is true.
 *
 * NOTHING HERE IS NETTED. "Still owed" is billed minus settled and it is stated
 * next to both, not instead of them; a bill with no link, a bill the counter
 * refused and a bill waiting on a link are three different problems and are
 * three different rows. The one arithmetic this panel does is none: every
 * figure below is an integer the server counted.
 *
 * AMBER, NOT RED. Money that has not settled is not a refusal by anybody, and
 * red on this counter means something was refused. The only red here is the row
 * for baskets the money service actually declined to charge.
 */
function Reconciliation({ rec, err }: {
  rec: db.ReconcileBody | null;
  err: string | null;
}) {
  const { t } = useT();

  if (!rec) {
    return (
      <Card title={t('today.recon.title')} tight>
        <Verdict tone="amber" title={t('today.recon.unavailable')}>
          {t('today.recon.unavailable.detail')}
          {err && <><br /><span className="mono">{err}</span></>}
        </Verdict>
      </Card>
    );
  }

  const d = rec.today;
  const life = rec.lifetime;
  const channels = Object.entries(d.by_channel).filter(([, v]) => v.billed.bills > 0);

  /* NOTHING TO RECONCILE IS NOT THE SAME AS AGREEING. On a counter that has
     billed nothing today and seen no webhook, "the till and the gateway agree"
     is a pass mark awarded for an exam nobody sat — and it would be shown on
     the exact morning a shopkeeper most wants to know his payment path is
     untested. The lifetime block below still has something to say, so the card
     stays; only the verdict changes. */
  const nothingToCheck = d.billed.bills === 0
    && d.events.webhooks_received === 0
    && d.disagreements.length === 0;

  return (
    <Card
      title={t('today.recon.title')}
      aside={d.disagreements.length > 0
        ? <Pill tone="amb">{d.disagreements.length}</Pill>
        : nothingToCheck ? undefined : <Pill tone="ok">{t('today.recon.clear')}</Pill>}
    >
      {nothingToCheck ? (
        <Verdict tone="info" title={t('today.recon.nothing')} icon={false}>
          {t('today.recon.nothing.detail')}
        </Verdict>
      ) : d.disagreements.length === 0 ? (
        <Verdict tone="green" title={t('today.recon.none')} icon={false}>
          {t('today.recon.none.detail')}
        </Verdict>
      ) : (
        <div className="stack">
          {d.disagreements.map((x) => (
            <Verdict
              key={x.code}
              /* Red is reserved for a refusal, and exactly one of these is
                 one. The rest are money that has not moved, which is amber. */
              tone={x.code === 'the_counter_refused_to_charge' ? 'red' : 'amber'}
              title={x.headline}
            >
              {x.detail}
            </Verdict>
          ))}
        </div>
      )}

      {/* THE DAY'S SPLIT IS DRAWN ONLY WHEN THERE IS A DAY TO SPLIT. Six rows
          of ₹0.00 under "today, split by what actually happened" is the same
          zero-dressed-as-a-measurement this screen's empty state exists to
          stop, and it would be worse here, because a reader who sees "still
          owed ₹0.00" on a blank morning has been told something reassuring by
          arithmetic performed on nothing. The lifetime block still runs. */}
      {!nothingToCheck && (
        <>
          <div className="eyebrow" style={{ marginTop: 16 }}>{t('today.recon.split')}</div>
          <KV k={t('today.recon.billed')}>
            {rupees(d.billed.paise)} <span className="muted">· {d.billed.bills}</span>
          </KV>
          <KV k={t('today.recon.settled')}>
            {d.settled.bills > 0
              ? <span className="fig green">{rupees(d.settled.paise)}</span>
              : <span className="muted">{t('today.recon.settled.none')}</span>}
          </KV>
          <KV k={t('today.recon.linksent')}>
            {rupees(d.awaiting.paise)} <span className="muted">· {d.awaiting.bills}</span>
          </KV>
          {d.never_asked.bills > 0 && (
            <KV k={t('today.recon.nolink')}>
              {rupees(d.never_asked.paise)} <span className="muted">· {d.never_asked.bills}</span>
            </KV>
          )}
          {d.refused.bills > 0 && (
            <KV k={t('today.recon.refused')}>
              {rupees(d.refused.paise)} <span className="muted">· {d.refused.bills}</span>
            </KV>
          )}
          {/* THE UNWITNESSED ROW IS NEVER HIDDEN WHEN IT IS NON-ZERO, and it is
              never added to the settled row above it. Invariant 2: only a
              signature-verified webhook may say money settled. */}
          {d.settled_unwitnessed.bills > 0 && (
            <KV k={t('today.recon.unwitnessed')}>
              <span className="fig amber">{rupees(d.settled_unwitnessed.paise)}</span>{' '}
              <span className="muted">· {d.settled_unwitnessed.bills}</span>
            </KV>
          )}
          <KV k={t('today.recon.owed')}>
            <b>{rupees(d.owed.paise)}</b> <span className="muted">· {d.owed.bills}</span>
          </KV>
        </>
      )}

      {channels.length > 0 && (
        <>
          <div className="eyebrow" style={{ marginTop: 16 }}>{t('today.recon.channel')}</div>
          {channels.map(([name, v]) => (
            <KV key={name} k={channelName(name, t)}>
              {rupees(v.billed.paise)} <span className="muted">· {v.billed.bills}</span>
              {v.settled.bills > 0 && (
                <> · <span className="fig green">{rupees(v.settled.paise)}</span></>
              )}
            </KV>
          ))}
        </>
      )}

      {/* AND THE SAME RULE ONE LEVEL UP. A counter installed this morning has
          billed nothing ever, and three lifetime rows of ₹0.00 would be its
          first impression of its own books. */}
      {life.billed.bills > 0 && (
        <>
          <div className="eyebrow" style={{ marginTop: 16 }}>{t('today.recon.lifetime')}</div>
          <KV k={t('today.recon.billed')}>
            {rupees(life.billed.paise)} <span className="muted">· {life.billed.bills}</span>
          </KV>
          <KV k={t('today.recon.settled')}>
            {life.settled.bills > 0
              ? <span className="fig green">{rupees(life.settled.paise)}</span>
              : <span className="muted">{t('today.recon.settled.never')}</span>}
          </KV>
          <KV k={t('today.recon.owed')}>
            <b>{rupees(life.owed.paise)}</b> <span className="muted">· {life.owed.bills}</span>
          </KV>
        </>
      )}

      <p className="hint">{rec.derived_from}</p>
    </Card>
  );
}

/** The channel names, translated. `unnamed` is a real answer and gets a real
    label: a session id that is neither the till's nor the storefront's is a
    bill this counter cannot say where was rung up, and filing it under either
    would be inventing a channel. An id the server grows later falls through to
    itself rather than to a wrong name. */
function channelName(id: string, t: Translator['t']): string {
  if (id === 'till') return t('today.recon.ch.till');
  if (id === 'storefront') return t('today.recon.ch.storefront');
  if (id === 'unnamed') return t('today.recon.ch.unnamed');
  return id;
}

/**
 * A DAY ON WHICH NOTHING HAS BEEN BILLED.
 *
 * This used to be the ordinary screen with zeros in it: four tiles reading
 * ₹0.00, "0 of 0 bills", and a headline sentence announcing that ₹0.00 had been
 * billed across 0 bills. Every one of those is arithmetic performed on an empty
 * set and dressed as a measurement — and on the morning of a normal trading day
 * it is what a shopkeeper sees for the first hour the shutter is up.
 *
 * WHAT STAYS. The webhook card, because "nobody has bought anything yet" and
 * "payments cannot reach this counter" look identical from here and the second
 * one is worth knowing before the first customer, not after. The chain block,
 * because an empty day and an unreadable chain must not look the same. And the
 * reconciliation, because a day with no bills can still have had webhooks
 * arrive and be refused — which is the loudest thing this counter could say on
 * a quiet morning.
 */
function NothingBilled({ body, rec, recErr, yesterday }: {
  body: m.TodayBody;
  rec: db.ReconcileBody | null;
  recErr: string | null;
  yesterday: m.DayBrief | null;
}) {
  const { t, tn } = useT();
  return (
    <>
      <Card title={t('today.empty.title')}>
        <Empty
          title={t('today.empty.head')}
          action={<a className="btn sm" href="#/till">{t('today.empty.action')}</a>}
        >
          {t('today.empty.body')}
        </Empty>
        {yesterday && yesterday.bills > 0 && (
          <p className="hint">
            {tn('today.empty.yesterday', yesterday.bills, {
              amount: rupees(yesterday.revenue_paise),
              n: String(yesterday.bills),
            })}
          </p>
        )}
      </Card>

      <Reconciliation rec={rec} err={recErr} />

      <div className="grid two">
        <div className="stack">
          <WebhookCard w={body.webhook} awaiting={0} settledToday={0} rec={rec} />
        </div>
        <div className="stack">
          <Card title="Where these numbers come from" tight>
            <KV k="source">the hash-chained audit log</KV>
            <KV k="chain"><ChainPill chain={body.chain} /></KV>
            <KV k="window">midnight to midnight, this counter's clock ({body.date})</KV>
            <p className="hint">{body.derived_from}</p>
          </Card>
        </div>
      </div>
    </>
  );
}

/**
 * The chain's state, in the three words it can actually be in.
 *
 * A CHAIN THAT DOES NOT EXIST IS NOT A VERIFIED CHAIN. `read_chain` reports
 * `{ok: true, exists: false, lines_verified: 0}` for a counter that has never
 * written a line — correct, because nothing failed to verify — and this pill
 * used to render exactly that as "VERIFIED · 0 lines" in a green pill. On a
 * counter installed this morning, the first thing its books said about
 * themselves was a pass mark for an audit of an empty room. Worse, it is the
 * same green a settled payment wears, spent on nothing.
 */
function ChainPill({ chain }: { chain: m.TodayBody['chain'] }) {
  if (!chain.exists) return <Pill tone="off">none yet — nothing has been recorded</Pill>;
  if (!chain.ok) return <Pill tone="bad">BROKEN</Pill>;
  return <Pill tone="ok">VERIFIED · {chain.lines_verified} lines</Pill>;
}

function BriefStat({ label, value, sub, delta, tone }: {
  label: string; value: string; sub: string;
  delta?: string | null; tone?: 'green';
}) {
  return (
    <div className={`brief-stat${tone ? ` ${tone}` : ''}`}>
      <span className="lbl">{label}</span>
      <span className="val">{value}</span>
      <span className="sub">{sub}</span>
      {delta && <span className="delta">{delta}</span>}
    </div>
  );
}

/**
 * Can a payment land here?
 *
 * THE WORDS BELOW THE TITLE ARE THE SERVER'S, VERBATIM. The title is the only
 * sentence this file writes, and it deliberately makes the WEAKER of the two
 * available claims: it says what this counter has seen, never what has "ever"
 * arrived. The strong version was written here, not by the server — this file
 * used to print "No payment has ever been able to reach this counter" — and a
 * browser has no way to tell a path that has never worked from a counter that
 * was restarted an hour ago. A shopkeeper sent to rebuild a tunnel that works
 * has been cost a day by a sentence a screen made up.
 *
 * THE COLOUR IS A CORRECTION, NOT A PREFERENCE. Green stays: a verified webhook
 * actually arrived, which is the one thing green means on this product. Every
 * other state is AMBER, including never-heard, because red on this counter
 * means something REFUSED, and a reading of what has reached this counter is
 * not a refusal by anybody. This card is a note about what has been observed;
 * an alarm is what the till shows when a mint is declined.
 */
function WebhookCard({ w, awaiting, settledToday, rec }: {
  w: m.TodayBody['webhook'];
  awaiting: number;
  /** Bills this same page counted as settled today, from the same chain. */
  settledToday: number;
  /** The chain's own count of webhook posts, when it could be read. */
  rec: db.ReconcileBody | null;
}) {
  const tone: 'green' | 'amber' = w.status === 'live' ? 'green' : 'amber';
  /* THE 'never' TITLE IS ABOUT THE MONEY SERVICE, NOT ABOUT THE COUNTER, and
     the difference is the whole card. `paisa /health` counts webhooks THIS
     PROCESS has seen; it is reset by a restart. The chain is not: it holds one
     line per post received, written before anything decided whether to believe
     it, so a post whose signature failed exists there and nowhere else. Saying
     "no webhook has reached this counter" over a chain that records three
     arriving today is the strong claim this file's own docstring forbids —
     and it was being made in the one situation where it is most wrong, because
     a counter under a forged or misconfigured webhook is exactly the counter
     whose money service has settled nothing. */
  const arrived = rec?.today.events.webhooks_received ?? 0;
  const title = w.status === 'live' ? 'Payments can reach this counter'
    : w.status === 'never'
      ? (arrived > 0
        ? 'Webhooks are arriving and none is being trusted'
        : 'No webhook has reached this counter')
      : w.status === 'silent' ? 'The payment path has gone quiet'
        : 'The money service is not answering';
  /* THE PAGE CONTRADICTING ITSELF IS WORTH MORE THAN EITHER HALF OF IT. A bill
     counted settled today is a bill this chain records as paid; a reading of
     "nothing has reached here" printed beside it is a reading to distrust, and
     saying so is cheaper than a shopkeeper deciding which of his own screens
     is lying. Neither figure is adjusted to agree with the other. */
  const disagrees = settledToday > 0 && (w.status === 'never' || w.status === 'silent');
  return (
    <Card title="Can a payment land here?" aside={awaiting > 0 && w.status !== 'live'
      ? <Pill tone="amb">{awaiting} awaiting</Pill> : undefined}>
      <Verdict tone={tone} title={title}>
        {/* THE CHAIN GOES FIRST WHEN IT CONTRADICTS THE READING. Both sentences
            stay, because they are answers to different questions and one of
            them is not wrong — but "no webhook has reached this counter since
            it started" read first, above evidence that three arrived this
            morning, is a reader's first impression forming on the weaker
            source. So the observed fact leads and the money service's reading
            follows, attributed, in its own words. */}
        {arrived > 0 && w.status !== 'live' && (
          <><b>{arrived}</b> webhook post{arrived === 1 ? '' : 's'} reached this counter today
          {rec && rec.today.events.webhooks_refused_total > 0 && (
            <>, and {rec.today.events.webhooks_refused_total === arrived
              ? 'not one of them was trusted'
              : `${rec.today.events.webhooks_refused_total} of them were not trusted`}{' '}
            ({refusalWords(rec.today.events.webhooks_refused)})</>
          )}. That count comes from the audit chain, which records a post before
          anything decides whether to believe it. The money service reports only what it has
          seen since it last started, and its own reading is:{' '}</>
        )}
        {w.headline}
        {awaiting > 0 && w.status !== 'live' && (
          <> {awaiting === 1 ? 'One bill is' : `${awaiting} bills are`} waiting on exactly this
          path — a customer may already have paid.</>
        )}
        {disagrees && (
          <> This page has already counted{' '}
          {settledToday === 1 ? 'one bill' : `${settledToday} bills`} settled today off the same
          chain. The two readings do not agree, and neither has been adjusted to match the
          other.</>
        )}
      </Verdict>
    </Card>
  );
}

/** "3 bad signature, 1 unknown session" — the server's own reason names, with
    the underscores taken out and nothing else changed. */
function refusalWords(byReason: Record<string, number>): string {
  return Object.entries(byReason)
    .map(([reason, n]) => `${n} ${reason.replace(/_/g, ' ')}`)
    .join(', ');
}

/*
 * THERE IS NO PERCENTAGE ON THIS SCREEN, AND THAT IS THE FIX.
 *
 * Two functions lived here — `deltaParts` and `deltaLine` — and both divided
 * TODAY, a day still being traded, by YESTERDAY, a finished one. The figure
 * they produced is at its largest at five past midnight and falls all day
 * whatever the shop does: at 09:00 a shop having its best week in a year reads
 * "down 80% on yesterday". The percentage was the only number on this page
 * nothing had derived, under a header that says nothing here is estimated and
 * a docstring that calls a delta between two differently-derived numbers a
 * random number with a percent sign.
 *
 * Both figures are still on the screen — today's in the tile, yesterday's
 * beside it and again in "Against yesterday" — so nothing was hidden; only the
 * arithmetic that was not entitled to be performed is gone. The Insights screen
 * makes this comparison honestly, because gawaah.insights cuts the earlier days
 * at the same time of day; `/manage/today` sends no such cut and no delta, and
 * inventing one in the browser is exactly what this counter exists not to do.
 */

function clock(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—'
    : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
