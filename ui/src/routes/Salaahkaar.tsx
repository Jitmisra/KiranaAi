import { useEffect, useState } from 'react';
import { rupees } from '../lib/money';
import { readTopSellers, shortName, type Suggestion } from '../lib/salaahkaar';
import { Card, KV, Pill, Refusal, Segmented, Skeleton, LoadingCard, Verdict } from '../components/ui';
import { useSalaahkaar } from '../components/useSalaahkaar';
import { Chips, Composer, MicGlyph, Thread, Thumb, Tile } from '../components/SalaahkaarCall';
import '../styles/advisor.css';
import '../styles/salaahkaar.css';

/**
 * SALAAHKAAR — the one conversation the shop has with its counter, as a call
 * with somebody drawn on the other end of it.
 *
 * This screen is what "Ask" and "Salaahkaar" were, together. A shopkeeper
 * says or types ONE sentence; the browser decides which brain it is for
 * (`lib/salaahkaar.ts`, pure and tested); a question is answered on the call,
 * reasoned about, voiced by the presenter with a mouth that makes the shape of
 * every word; an instruction comes back as a PROPOSAL on a card — what would
 * change, in this shop's own prices and names — with a button a person has to
 * press and an UNDO wherever the server has one. Every turn says which door it
 * went through. Nothing said here bills, and nothing here mints a payment.
 *
 * The same call is behind the round button at the foot of every other screen
 * (`components/SalaahkaarFab.tsx`): one transcript, one session, two views.
 *
 * WHAT THE PRESENTER IS is documented in `components/Presenter.tsx`, and the
 * reasons it is a drawing and not a video are there too. In one line: it is a
 * fictional character animated in this browser from the words being said; it
 * is not a photograph, not anyone's likeness, and it is labelled as such on
 * the tile at every size.
 */
export default function Salaahkaar() {
  const e = useSalaahkaar();
  const { call, presence } = e;

  return (
    <div className="adv sk-page">
      <div className="page-head">
        <h1>Salaahkaar</h1>
        <p>
          Ask the counter anything about its own shop, out loud or typed, and hear it answer — or tell
          it what to do, and watch it propose exactly that for you to press. It reads the figures from the
          shop’s files; with a model set it reasons about them; every turn shows which brain answered and
          what left this machine. Nobody is on the call, and it bills nothing.
        </p>
      </div>

      <div className="adv-call">
        {/* ----------------------------------------------------- the tile -- */}
        <section className="adv-stage" aria-label="The presenter">
          <Tile e={e} size="full" />

          {/* THE ONE CALL CONTROL.
              It stood here AND in the pulse's header — two buttons reading
              START CALL on one screen, and a big round microphone between
              them that the composer already had. One of each now: the tile is
              where she IS, so the control that opens and closes the line is
              against it; the microphone and the language are in the composer,
              beside the box a person types in, because that is where a person
              acts. */}
          <div className="sk-callbar">
            {call.onCall ? (
              <button type="button" className="btn lg sk-callbtn" onClick={e.hangUp}>HANG UP</button>
            ) : (
              <button
                type="button"
                className="btn primary lg sk-callbtn"
                onClick={e.startCall}
                disabled={e.healthLoading || !!e.healthRefusal}
              >
                <MicGlyph size={16} /> START CALL
              </button>
            )}
            <span className="sk-callbar-l">
              {call.onCall
                ? 'on the line — the microphone is in the bar you type in'
                : e.micOk
                  ? 'or type it — the microphone beside the box reaches the same call'
                  : e.micReason}
            </span>
          </div>

          {e.voiceRefusal && (
            <Refusal
              reason={e.voiceRefusal.reason}
              detail={e.voiceRefusal.detail}
              hint="The answer was read out anyway, by this browser’s own voice, and that turn is tagged VOICE · THIS BROWSER. Press “This browser” to stop asking for the natural one."
              action={<button type="button" className="btn sm ghost" onClick={() => e.chooseVoice('browser')}>USE THIS BROWSER’S VOICE</button>}
            />
          )}
          {e.micError && (
            <Refusal reason="The microphone stopped" detail={e.micError}
                     hint="Press the microphone to try again. Typing under the transcript reaches the same call." />
          )}
        </section>

        {/* ----------------------------------------------------- the prose --
            SEPARATE FROM THE TILE ON PURPOSE, so the grid can put it AFTER the
            shop on one column. On a phone the old order was face, then a wall
            of chrome and prose, and the figures were a screen and a half down;
            now it is face, the call, the shop, the conversation, and then the
            two things that are a promise and a setting. On two columns the
            grid puts it straight back under the tile, where it reads as the
            caption to the face it is about. */}
        <section className="adv-prose" aria-label="What this call is, and what leaves this machine">
          <p className="adv-honest">
            <b>Nobody is on this call.</b> The face above is a drawing this browser animates. Its
            mouth moves in time with the answer being read aloud — by this browser, or by a voice
            the till fetched once for that sentence and keeps. There is no camera, no recording,
            and no person seeing your shop’s figures.
          </p>

          {/* THE SETTINGS AND THE DISCLOSURE, FOLDED INTO ONE LINE.
              Which voice reads the answer is a SETTING, not an action, and it
              stood in the middle of the screen as two more control rows and a
              paragraph — four rows of chrome between the face and the first
              question. It is folded here with the prose that explains what the
              choice costs, because the two belong together: the switch and the
              sentence saying what it sends. Every word that was on the page is
              still on the page, one press away. */}
          <details className="adv-how sk-fold">
            <summary>
              <span className="adv-how-t">Voice, and what leaves this machine</span>
              <span className="adv-how-s">
                {e.speakOn ? 'answers read aloud' : 'answers not read aloud'} ·{' '}
                {e.useNatural ? 'your words and the spoken answer' : 'your words only'} · the presenter
                sends nothing · it cannot bill, charge, or invent a number
              </span>
            </summary>

            <Card title="How it speaks">
              <div className="sk-settings">
                <div className="sk-setting">
                  <button
                    type="button"
                    className="btn sm"
                    onClick={() => e.setSpeakOn(!e.speakOn)}
                    aria-pressed={e.speakOn}
                    disabled={typeof speechSynthesis === 'undefined'}
                    title={e.speakOn ? 'Stop reading answers aloud' : 'Read answers aloud'}
                  >
                    {e.speakOn ? 'VOICE ON' : 'VOICE OFF'}
                  </button>
                  <span className="l">speaker</span>
                </div>

                <div className="sk-setting">
                  <Segmented
                    value={e.useNatural ? 'natural' : 'browser'}
                    onChange={e.chooseVoice}
                    size="sm"
                    options={[
                      {
                        value: 'natural',
                        label: 'Natural voice',
                        disabled: !e.naturalAvailable,
                        title: e.health?.voice?.available
                          ? `${e.health.voice.model} — one sentence leaves this machine per answer, once, and is kept on the till`
                          : (e.health?.voice?.why_not ?? 'No natural voice is available on this counter'),
                      },
                      { value: 'browser', label: 'This browser', title: 'The browser’s own voice. Nothing leaves this machine to be spoken.' },
                    ]}
                  />
                  <span className="l">{e.useNatural ? 'a sentence leaves, once' : 'nothing leaves'}</span>
                </div>
              </div>

              <p className="adv-voice sk-note">
                {e.micOk ? <>Hearing through this browser’s speech service. </> : <>{e.micReason} </>}
                {e.useNatural
                  ? <>Speaking with <span className="mono">{e.health?.voice?.voice ?? 'a natural voice'}</span> via {e.health?.voice?.model ?? 'the provider'}, fetched once per sentence and kept on the till; the browser’s own voice takes over if that fails.</>
                  : <>{e.choice.note}</>}
              </p>
            </Card>

            <Card title="What leaves this machine">
              <p className="adv-lede">
                <b>The microphone always sends audio somewhere.</b> The browser transcribes speech with its
                own service, so what you say leaves this machine even with no key set.{' '}
                {e.useNatural ? (
                  <>
                    <b>With the natural voice on, the spoken sentence leaves too</b> — once per distinct
                    sentence, to {e.health?.voice?.model ?? 'the provider'}, to be voiced; the till keeps the
                    sound and never asks twice for the same words. Every answer below says which voice read it.
                  </>
                ) : (
                  <>The spoken answer is made on this machine by the browser’s own voice and goes nowhere.</>
                )}
              </p>
              <p className="adv-lede">
                <b>The presenter sends nothing.</b> It is one picture that came with this page, and code
                that draws a mouth into it from the words being spoken. Nothing about the face, the call or
                the shop is uploaded to make it move, and it works with the internet unplugged.
              </p>
              <p className="adv-lede">
                {e.reasons ? (
                  <>
                    With a key, each question sends the model this call’s last few sentences and, to phrase the
                    answer, <b>the one tool’s result for that question</b> — rupee strings, counts, product
                    names. Never a paise integer, a customer’s name, phone or address, a sku id, or the audit
                    chain. An instruction sends the sentence alone; the proposal is priced here. Every turn
                    lists the exact fields that went.
                  </>
                ) : (
                  <>
                    With no key, nothing. Each turn is read from the shop’s files here and spoken here.
                  </>
                )}
              </p>
            </Card>

            <Card title="What it will not do">
              <ul className="adv-limits">
                <li><b>It is not a person.</b> The presenter is a drawing, animated in this browser. Nobody is speaking, and nobody is watching.</li>
                <li><b>It cannot bill, and it cannot charge.</b> “do Maggi daal do” comes back as a proposal priced from your own catalogue; pressing HOLD keeps it for the till, which is the only thing that writes a bill line. Nothing here mints a payment, on any command.</li>
                <li><b>It does nothing on a voice command alone.</b> A stock movement or an expense is written only when you press RECORD, at the server’s own endpoint, and every one of those has an UNDO on the card.</li>
                <li><b>It cannot invent a number.</b> Every figure in the model’s advice is checked against the figures it was given. One that came from nowhere drops the advice, and the counter’s own sentence is spoken instead — with the reason shown.</li>
                <li><b>It does not remember yesterday.</b> A call is kept in the server’s memory for a few minutes and never written down. Hang up and it is gone.</li>
                <li><b>With no model it does not advise.</b> It reads the figures and says so. Advice from nowhere is the thing this product exists to refuse.</li>
              </ul>
            </Card>
          </details>
        </section>

        {/* ------------------------------------------------- the transcript -- */}
        <section className="adv-side" aria-label="The conversation">
          <Thread e={e} size="full">
            {e.healthLoading && (
              <div className="adv-cold">
                <div className="adv-cold-wait" role="status" aria-label="Checking who is on the line">
                  <Skeleton w={168} h={22} radius={6} />
                  <div className="adv-cold-wait-lines">
                    <Skeleton w="94%" h={11} radius={999} />
                    <Skeleton w="88%" h={11} radius={999} />
                    <Skeleton w="62%" h={11} radius={999} />
                  </div>
                  <Skeleton w={152} h={40} radius={999} />
                  <div className="adv-chips">
                    {[168, 186, 158, 172].map((w, i) => <Skeleton key={i} w={w} h={30} radius={999} />)}
                  </div>
                </div>
              </div>
            )}
            {!e.healthLoading && e.healthRefusal && (
              <div style={{ padding: 'var(--s2)' }}>
                <Refusal reason={e.healthRefusal.reason} detail={e.healthRefusal.detail}
                         hint="Nothing else on the till is affected — the camera, the bill and the storefront do not go through here." />
              </div>
            )}
            {!e.healthLoading && !e.healthRefusal && (
              <Pulse
                chips={e.chips}
                chipsLoading={e.catLoading}
                catRefusal={e.catRefusal}
                reasons={e.reasons}
                micOk={e.micOk}
                busy={e.busy}
                onPick={(s) => void e.say(s, 'text')}
              />
            )}
          </Thread>

          {call.turns.length > 0 && (
            <div className="adv-quick" aria-label="Things it can be asked">
              <Chips chips={e.chips} loading={e.catLoading} busy={e.busy} small onPick={(s) => void e.say(s, 'text')} />
            </div>
          )}

          <Composer e={e} size="full" />
        </section>
      </div>

      {/* --------------------------------------------------------- the rail --
          One card now. "What leaves this machine" and "What it will not do"
          moved up beside the tile, into the same fold as the voice choice they
          describe — every word of them intact, and the settings a person might
          actually change are now one press from the face rather than three
          rows down the middle of the screen. */}
      <div className="adv-rail one">
        <Card
          title="Which brain answers"
          aside={e.healthLoading
            ? <Pill tone="off">CHECKING</Pill>
            : <Pill tone={e.reasons ? 'code' : 'off'}>{e.reasons ? (e.health?.brain ?? 'MODEL').toUpperCase() : 'FIGURES ONLY'}</Pill>}
        >
          {e.healthLoading ? (
            <LoadingCard lines={5} label="Asking this counter what it can reach" />
          ) : e.healthRefusal ? (
            <Refusal reason={e.healthRefusal.reason} detail={e.healthRefusal.detail}
                     hint="Nothing else on the till is affected — the camera, the bill and the storefront do not go through here." />
          ) : e.health ? (
            <>
              <KV k="a question goes to">the advisor, on this call — it reasons, and never acts</KV>
              <KV k="an instruction goes to">the assistant — it proposes, and you press</KV>
              <KV k="products it can price"><span className="tnum">{e.health.products_priced}</span></KV>
              <KV k="remembers">{e.health.keeps.turns} turns, for {Math.round(e.health.keeps.for_s / 60)} min, in memory</KV>
              <KV k="on disk">{e.health.keeps.on_disk ? 'yes' : 'nothing'}</KV>
              {e.reasons ? (
                <>
                  <KV k="model"><span className="mono">{e.health.model ?? '—'}</span></KV>
                  <KV k="endpoint"><span className="mono">{e.health.base_url}</span></KV>
                </>
              ) : (
                <>
                  <p className="adv-lede" style={{ marginTop: 'var(--s4)' }}>{e.health.cannot_reason_because}</p>
                  <code className="adv-code">export XAI_API_KEY=…{'\n'}make serve</code>
                  <p className="hint">
                    There is no box on this page to paste a key into. It is read from the till’s
                    environment on every question and never sent to this browser.
                  </p>
                </>
              )}
              {e.health.catalogue_problem && (
                <Refusal reason={e.health.catalogue_problem.reason} detail={e.health.catalogue_problem.detail} />
              )}
            </>
          ) : null}
        </Card>

      </div>

      {presence === 'off' && !e.micOk && (
        <div style={{ marginTop: 'var(--s5)' }}>
          <Verdict tone="info" title="This browser cannot listen">
            {e.micReason} Typing under the transcript reaches exactly the same call.
          </Verdict>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------- the pulse -- */

type PulseTakings = {
  bills: number; revenue_paise: number; settled_paise: number; settled_count: number;
  awaiting_paise: number; awaiting_count: number;
};
type PulseData = {
  today: PulseTakings | null;
  yesterday: PulseTakings | null;
  top: ReturnType<typeof readTopSellers>;
  webhook: { status: string; headline: string } | null;
  orders: { open: number; new: number } | null;
  low: { count: number; first: string | null } | null;
  expiry: { soon: number; expired: number; at_risk_paise: number; first: string | null; days: number | null } | null;
};

const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);

async function pulseGet<T>(url: string): Promise<T | null> {
  try {
    const r = await fetch(url, { cache: 'no-store', credentials: 'same-origin' });
    if (!r.ok) return null;
    const d = (await r.json()) as { ok?: boolean } & T;
    return d.ok === false ? null : d;
  } catch { return null; }
}

/** The shop's numbers, read straight off the same files the advisor reads. */
async function readPulse(): Promise<PulseData> {
  type Today = { today?: Record<string, unknown>; yesterday?: Record<string, unknown>; top_sellers?: unknown; webhook?: Record<string, unknown> };
  type Orders = { counts?: Record<string, number> };
  type Low = { count?: number; low?: Array<{ name?: string }> };
  type Exp = { counts?: Record<string, number>; soon?: Array<{ name?: string; days_left?: number }>; value_at_risk?: Record<string, unknown> };
  const [t, o, l, x] = await Promise.all([
    pulseGet<Today>('/manage/today'), pulseGet<Orders>('/orders'),
    pulseGet<Low>('/stock/low'), pulseGet<Exp>('/expiry'),
  ]);
  const takings = (v?: Record<string, unknown>): PulseTakings | null => v ? {
    bills: num(v.bills), revenue_paise: num(v.revenue_paise),
    settled_paise: num(v.settled_paise), settled_count: num(v.settled_count),
    awaiting_paise: num(v.awaiting_paise), awaiting_count: num(v.awaiting_count),
  } : null;
  const oc = o?.counts ?? {};
  return {
    today: takings(t?.today), yesterday: takings(t?.yesterday),
    top: readTopSellers({ top_sellers: t?.top_sellers }, 3),
    webhook: t?.webhook ? { status: String(t.webhook.status ?? ''), headline: String(t.webhook.headline ?? '') } : null,
    orders: o ? { open: num(oc.new) + num(oc.preparing) + num(oc.out_for_delivery), new: num(oc.new) } : null,
    low: l ? { count: num(l.count), first: l.low?.[0]?.name ?? null } : null,
    expiry: x ? {
      soon: num(x.counts?.soon), expired: num(x.counts?.expired),
      at_risk_paise: num(x.value_at_risk?.soon_paise), first: x.soon?.[0]?.name ?? null,
      days: typeof x.soon?.[0]?.days_left === 'number' ? x.soon[0].days_left : null,
    } : null,
  };
}

/**
 * THE SHOP'S PULSE — what the advisor would talk about, on screen before
 * anybody talks. Each tile IS the question — press it and the advisor is
 * asked exactly that, out loud. Every figure comes off the same files the
 * advisor's tools read; a tile whose source cannot be read says so and shows
 * no number, because a dash is honest and a zero is a claim. (A missing
 * BLOCK — the whole endpoint refused — is the dash. A missing FIELD inside a
 * block that did answer would render as 0 through `num()`; the field names
 * here were printed off the live server, not guessed, and a verifier found
 * that comment overstated before this sentence was added.)
 */
function Pulse({ chips, chipsLoading, catRefusal, reasons, micOk, busy, onPick }: {
  chips: Suggestion[];
  chipsLoading: boolean;
  catRefusal: { reason: string; detail?: string } | null;
  reasons: boolean;
  /** Only to word the headline. The call button is beside the tile, once. */
  micOk: boolean;
  busy: boolean;
  onPick: (say: string) => void;
}) {
  const [d, setD] = useState<PulseData | null>(null);
  useEffect(() => { let on = true; void readPulse().then((x) => { if (on) setD(x); }); return () => { on = false; }; }, []);

  const t = d?.today ?? null;
  const settledPct = t && t.revenue_paise > 0 ? Math.round((t.settled_paise / t.revenue_paise) * 100) : 0;
  const most = Math.max(1, ...(d?.top ?? []).map((r) => r.units));

  return (
    <div className="adv-pulse">
      <div className="adv-pulse-head">
        <div>
          <span className="adv-pulse-kicker">THE SHOP, RIGHT NOW</span>
          <h2>{micOk ? 'Press a number, or just talk' : 'Press a number, or type below'}</h2>
          <p>
            {reasons
              ? 'A model is set: it reads these figures, reasons about them, and says what to do next. Tell it to do something and it proposes — you press.'
              : 'No model is set: it reads these figures aloud and says plainly that it cannot reason about them. An instruction still comes back as a proposal for you to press.'}
          </p>
        </div>
        {/* NO SECOND START CALL HERE. It was the other half of the pair —
            this header's button and the one under the presenter, both saying
            START CALL, on one screen. The tile keeps it. */}
      </div>

      <div className="adv-tiles" aria-busy={!d || undefined}>
        <button type="button" className="adv-tile-q wide" onClick={() => onPick('aaj ki bikri kitni hui')}
                disabled={!d} title="aaj ki bikri kitni hui">
          <span className="q-label">Today’s takings</span>
          {!d ? <Skeleton w="60%" h={34} /> : t ? (
            <>
              <span className="q-big tnum">{rupees(t.revenue_paise)}</span>
              <span className="q-sub">
                <span className="tnum">{t.bills}</span> bill{t.bills === 1 ? '' : 's'}
                {d.yesterday && d.yesterday.revenue_paise > 0 && (
                  <> · yesterday <span className="tnum">{rupees(d.yesterday.revenue_paise)}</span></>
                )}
              </span>
              <span className="q-bar" aria-label={`${settledPct}% settled`}>
                <i className="ok" style={{ width: `${settledPct}%` }} />
              </span>
              <span className="q-legend">
                <span><b className="dot ok" />settled <span className="tnum">{rupees(t.settled_paise)}</span></span>
                <span><b className="dot amb" />awaiting <span className="tnum">{rupees(t.awaiting_paise)}</span> · {t.awaiting_count} link{t.awaiting_count === 1 ? '' : 's'}</span>
              </span>
            </>
          ) : <span className="q-none">the day’s figures could not be read</span>}
          <span className="q-ask">ask →</span>
        </button>

        <button type="button" className="adv-tile-q" onClick={() => onPick('kitne orders pending hain')}
                disabled={!d} title="kitne orders pending hain">
          <span className="q-label">Orders open</span>
          {!d ? <Skeleton w="40%" h={34} /> : d.orders ? (
            <>
              <span className="q-big tnum">{d.orders.open}</span>
              <span className="q-sub">{d.orders.new} new, not yet packed</span>
            </>
          ) : <span className="q-none">unreadable</span>}
          <span className="q-ask">ask →</span>
        </button>

        <button type="button" className="adv-tile-q" onClick={() => onPick('kya khatam ho raha hai')}
                disabled={!d} title="kya khatam ho raha hai">
          <span className="q-label">Running low</span>
          {!d ? <Skeleton w="40%" h={34} /> : d.low ? (
            <>
              <span className={`q-big tnum${d.low.count > 0 ? ' amb' : ''}`}>{d.low.count}</span>
              <span className="q-sub">{d.low.first ? shortName(d.low.first) : 'nothing at its reorder level'}</span>
            </>
          ) : <span className="q-none">unreadable</span>}
          <span className="q-ask">ask →</span>
        </button>

        {/* TOP SELLERS AS BARS. Three rows, units deciding the length, the
            product's own photograph beside each — the question answered
            before it is asked, and the ask still one press away.

            IT RENDERED "P", "A.", "A." — one letter per product, and it was
            the LAYOUT, not the data. `/manage/today` returns the full names
            ("Parle-G biscuit 100g", "Aashirvaad Whole Wheat Atta 5 kg", read
            off the running till before a line of this was touched); the tile
            was one of four columns in a 628px panel — 133px wide — so a row
            of `22px | name | 56px | auto` left the name track 0px and the
            ellipsis ate everything but the first character. Measured at 1440:
            name track 0px and 1px before, 169px and 176px after, with
            `scrollWidth` equal to the rendered width on all three rows — so
            nothing is clipped. The tile is two columns wide now (279px), the
            name has its own line above the bar, and it WRAPS rather than
            truncating: a shortened product name is one the shopkeeper has to
            guess at, and guessing is what this counter refuses. */}
        <button type="button" className="adv-tile-q two" onClick={() => onPick('aaj sabse zyada kya bika')}
                disabled={!d} title="aaj sabse zyada kya bika">
          <span className="q-label">Top sellers today · by units</span>
          {!d ? <Skeleton w="70%" h={34} /> : d.top.length > 0 ? (
            <span className="q-bars">
              {d.top.map((r) => (
                <span className="q-bar-row" key={r.sku_id}>
                  <Thumb sku={r.sku_id} name={r.name} />
                  {/* The raw name in the title keeps the Devanagari gloss that
                      `shortName` strips for the line. */}
                  <span className="q-bar-nm" title={r.name}>{shortName(r.name)}</span>
                  <span className="q-bar-n"><b className="tnum">{r.units}</b> sold</span>
                  <span className="q-bar-track" aria-hidden="true"><i style={{ width: `${Math.round((r.units / most) * 100)}%` }} /></span>
                </span>
              ))}
            </span>
          ) : <span className="q-none">nothing billed yet today</span>}
          <span className="q-ask">ask →</span>
        </button>

        <a className="adv-tile-q two link" href="#/expiry">
          <span className="q-label">Expiring soon</span>
          {!d ? <Skeleton w="40%" h={34} /> : d.expiry ? (
            <>
              <span className={`q-big tnum${d.expiry.soon > 0 ? ' amb' : ''}`}>{d.expiry.soon}</span>
              <span className="q-sub">
                {d.expiry.first
                  ? <>{shortName(d.expiry.first)}{d.expiry.days !== null ? <> · {d.expiry.days} day{d.expiry.days === 1 ? '' : 's'}</> : null}</>
                  : 'no batch inside the window'}
                {d.expiry.at_risk_paise > 0 && <> · <span className="tnum">{rupees(d.expiry.at_risk_paise)}</span> at risk</>}
              </span>
            </>
          ) : <span className="q-none">unreadable</span>}
          <span className="q-ask">see →</span>
        </a>
      </div>

      {d?.webhook && d.webhook.status === 'never' && t && t.bills > 0 && (
        <p className="adv-pulse-note">
          <b className="dot amb" /> No webhook has reached this counter since it started, so nothing
          today can be shown as settled — however many links went out.
        </p>
      )}

      <Chips chips={chips} loading={chipsLoading} busy={busy} onPick={onPick} />
      {catRefusal && (
        <div className="adv-cold-refusal">
          <Refusal reason={catRefusal.reason} detail={catRefusal.detail}
                   hint="The questions above still work. Only the suggestion that names one of your own products is missing, because this counter’s shelf could not be read." />
        </div>
      )}
    </div>
  );
}
