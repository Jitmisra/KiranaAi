import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import * as manage from '../lib/manageapi';
import {
  Button, Card, Empty, IcoTag, KV, Pill, Refusal, Skeleton, Verdict,
} from '../components/ui';
import '../styles/books.css';

/**
 * Settings — a READ-ONLY readout of what this counter is configured to do.
 *
 * Read-only in the strong sense. Every number here was decided somewhere it can
 * be reviewed: a constant in gawaah/identity.py, the gates written into the
 * catalogue when it was built, the environment the money service booted with.
 * A page that could widen phi from a browser would make "abstain rather than
 * guess" a suggestion, so there is no control on this page that changes
 * anything. The one button is COPY, and it moves a value out, never in.
 *
 * THE WEBHOOK BLOCK IS THE REASON THIS PAGE EXISTS. A counter whose inbound
 * webhook path has quietly died looks IDENTICAL to one where nobody has paid
 * yet: both show a link, both spin, neither turns green. That ambiguity cost a
 * real payment 78 seconds of silence. So the liveness of that path is the first
 * thing on the page, at full size, and the case that has never worked is red
 * and says what to do about it.
 *
 * NO SECRET APPEARS HERE. Not a value, not a prefix of one, not a length. The
 * key secret and the webhook secret are booleans from the money service's own
 * health, passed straight through as booleans; nothing in the browser or in the
 * router that feeds it ever reads either value.
 */

/** Copy one identifier. Plain button, navigator.clipboard, nothing else. */
function CopyBtn({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className={done ? 'bk-copy done' : 'bk-copy'}
      title="Copy to the clipboard"
      onClick={() => {
        if (!navigator.clipboard) return;
        void navigator.clipboard.writeText(text).then(() => {
          setDone(true);
          setTimeout(() => setDone(false), 1600);
        });
      }}
    >
      {done ? 'copied' : 'copy'}
    </button>
  );
}

/** A mono value with its copy affordance beside it. */
function Hash({ v }: { v: string }) {
  return (
    <span className="bk-hash">
      <span className="mono">{v}</span>
      <CopyBtn text={v} />
    </span>
  );
}

/**
 * The webhook headline, at the size of the thing it is telling you.
 *
 * Three states, three unmistakable dresses: never-heard is red (nothing can
 * turn a bill green), heard-long-ago is amber (worked once, may be dead now),
 * heard-recently is green — a money fact, the one green here that is not a
 * settled bill. `unknown` is colourless on purpose: the money service did not
 * answer, so this panel does not pretend to know.
 */
function WebhookHero({ w, ledger }: {
  w: manage.SettingsBody['webhook'];
  ledger: manage.SettingsBody['ledger'];
}) {
  /**
   * THE COUNTER SAYS "NEVER" AND ITS OWN CHAIN SAYS OTHERWISE.
   *
   * `webhooks_seen` is a count kept in the money service's memory, so it goes
   * back to zero every time that process restarts. The word `never` is built
   * from `seen <= 0`, which makes a counter that has merely been quiet since
   * lunchtime indistinguishable from one whose inbound path has never worked.
   *
   * This page holds the evidence that tells them apart. A bill cannot settle
   * except through a signature-verified webhook — that is invariant 2 of this
   * whole product — so a VERIFIED audit chain carrying a settled bill is proof
   * that something reached this counter at some point. When it does, the red
   * dress and the `role="alert"` come off: this is a note about a quiet
   * restart, not an emergency.
   *
   * Getting this wrong in the loud direction is the ₹99 tunnel incident in
   * FAILURES.md running backwards — a false alarm that sends a shopkeeper to
   * rebuild infrastructure that works. The server's own sentence is still
   * printed below, unedited; what changes is that this page stops shouting it.
   */
  const settled = ledger.chain_ok ? (ledger.bills_settled ?? 0) : 0;
  const quietSinceRestart = w.status === 'never' && settled > 0;

  const tone =
    quietSinceRestart ? 'unk'
      : w.status === 'never' ? 'bad'
        : w.status === 'live' ? 'ok'
          : w.status === 'silent' ? 'warn'
            : 'unk';
  const title =
    quietSinceRestart ? 'Nothing has reached this counter since it started'
      : w.status === 'never' ? 'Nothing has ever reached this counter'
        : w.status === 'live' ? 'The webhook path is alive'
          : w.status === 'silent' ? 'Nothing has reached this counter recently'
            : 'The webhook path cannot be checked';
  const tag =
    quietSinceRestart ? 'none since this restart'
      : w.status === 'never' ? 'never heard'
        : w.status === 'live' ? 'heard recently'
          : w.status === 'silent' ? 'heard long ago'
            : 'unknown';
  return (
    <div
      className={`wh-hero ${tone}`}
      role={w.status === 'never' && !quietSinceRestart ? 'alert' : 'status'}
    >
      <div className="wh-state">
        <span className="wh-dot" />
        <span className="wh-word">{title}</span>
        <span className="wh-tag">{tag}</span>
      </div>
      {quietSinceRestart ? (
        <>
          <p className="wh-line">
            This shop&rsquo;s own audit chain holds {settled} settled bill{settled === 1 ? '' : 's'}{' '}
            on {ledger.lines} lines that verify, and a bill cannot settle except through a
            signature-verified webhook — so the path has worked. The count below is kept in the
            money service&rsquo;s memory and starts again from zero every time that process
            restarts, which is what &ldquo;never&rdquo; is being derived from here.
          </p>
          <p className="wh-line muted">
            The money service&rsquo;s own sentence, unedited: &ldquo;{w.headline}&rdquo;
          </p>
        </>
      ) : (
        <p className="wh-line">{w.headline}</p>
      )}
      <div className="wh-facts">
        <span className="wh-fact">
          <span className="wh-n">{w.webhooks_seen ?? '—'}</span>
          <span className="wh-l">webhooks seen</span>
        </span>
        <span className="wh-fact">
          <span className="wh-n">{w.last_webhook_at ? manage.ago(w.silent_for_seconds) : 'never'}</span>
          <span className="wh-l">since the last one</span>
        </span>
        <span className="wh-fact">
          <span className="wh-n">{w.last_green_webhook_at ? manage.when(w.last_green_webhook_at) : 'never'}</span>
          <span className="wh-l">last one that paid a bill</span>
        </span>
      </div>
      {w.last_webhook_at && (
        <div className="mono wh-last">last: {manage.when(w.last_webhook_at)}</div>
      )}
    </div>
  );
}

/** A labelled band of the page: eyebrow, one honest sentence, then the cards. */
function Section({ label, sub, children }: {
  label: string; sub: string; children: ReactNode;
}) {
  return (
    <section className="bk-section">
      <div className="bk-section-h">
        <span className="eyebrow">{label}</span>
        <span className="sub">{sub}</span>
      </div>
      {children}
    </section>
  );
}

export default function Settings() {
  const [body, setBody] = useState<manage.SettingsBody | null>(null);
  const [err, setErr] = useState<{ reason: string; detail?: string } | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    const r = await manage.settings();
    if (r.ok) { setBody(r); setErr(null); } else { setErr(r); setBody(null); }
    setLoading(false);
  }, []);

  useEffect(() => { void load(); }, [load]);

  // The webhook fact goes stale while you look at it, and a stale liveness
  // readout is the exact failure this panel exists to prevent. Thirty seconds
  // is slow enough to cost nothing and fast enough that a tunnel dying while
  // the page is open shows up before the next customer does.
  useEffect(() => {
    const id = setInterval(() => { void load(); }, 30_000);
    return () => clearInterval(id);
  }, [load]);

  if (loading && !body) {
    return (
      <div>
        <div className="page-head">
          <h1>Settings</h1>
          <p>
            What this counter is set to do, read back from where each decision actually lives.
          </p>
        </div>
        {/* AT THE SHAPE OF THE PAGE, not three grey rectangles of arbitrary
            height. The first thing that lands is the webhook readout — the
            panel this screen exists for — so its three figures are reserved
            where they will appear, and the two paired cards below reserve
            their rows of label-and-value rather than a slab. */}
        <div className="stack" role="status" aria-live="polite" aria-label="Reading the configuration">
          <div className="card" aria-hidden="true">
            <div className="card-head"><Skeleton w={260} h={15} radius={999} /></div>
            <div className="card-body">
              <div className="wh-hero unk">
                <Skeleton w="46%" h={17} radius={999} />
                <div style={{ margin: '12px 0 16px' }}><Skeleton w="88%" h={11} radius={999} /></div>
                <div className="wh-facts">
                  {['webhooks seen', 'since the last one', 'last one that paid a bill'].map((l) => (
                    <span className="wh-fact" key={l}>
                      <Skeleton w={64} h={22} radius={8} />
                      <span className="wh-l">{l}</span>
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </div>
          <div className="grid two">
            {[0, 1].map((c) => (
              <div className="card" key={c} aria-hidden="true">
                <div className="card-head"><Skeleton w={170} h={15} radius={999} /></div>
                <div className="card-body">
                  {[0, 1, 2, 3, 4].map((r) => (
                    <div
                      key={r}
                      style={{
                        display: 'flex', justifyContent: 'space-between', gap: 24,
                        padding: '9px 0', borderBottom: '1px solid var(--line)',
                      }}
                    >
                      <Skeleton w={130} h={11} radius={999} />
                      <Skeleton w={72} h={11} radius={999} />
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (err || !body) {
    return (
      <div>
        <div className="page-head"><h1>Settings</h1></div>
        <Refusal
          reason="This counter could not report its own configuration"
          detail={err?.reason}
          hint={err?.detail}
          action={<Button size="sm" onClick={() => void load()}>TRY AGAIN</Button>}
        />
      </div>
    );
  }

  const { recognition, mat, catalogue, money, ledger } = body;

  /**
   * How the money service's intents actually ended.
   *
   * Read through a local cast rather than off `manageapi`'s `money` type: the
   * server is growing this field and a screen that only renders when the type
   * has caught up would render nothing today. Absent is a state — `byState`
   * stays null and the card says it cannot answer — never a zero.
   */
  const byState =
    (money as { intents_by_state?: Record<string, number> | null }).intents_by_state ?? null;
  const calling = byState?.CALLING ?? 0;
  const indeterminate = byState?.INDETERMINATE ?? 0;
  /* CALLING and INDETERMINATE are the two states where the gateway was reached
     and this counter cannot say what happened. SETTLED and REFUSED are answers;
     these are not. */
  const unknownOutcome = calling + indeterminate;

  return (
    <div>
      <div className="page-head">
        <h1>Settings</h1>
        <p>
          What this counter is set to do, read back from where each decision actually lives. Nothing
          on this page can be changed from here — the gates are in the code and in the catalogue, so
          that widening one is a change somebody can review.
        </p>
      </div>

      <div className="stack">
        <Section
          label="Payments"
          sub="whether money can still reach this counter, and who holds the keys"
        >
          <Card title="Can a payment still reach this counter?">
            <WebhookHero w={body.webhook} ledger={ledger} />

            {/* WHICH GATEWAY IS BEHIND ALL OF THIS, beside the readout rather
                than in a grey pill in the card below.

                The hero's advice — check the tunnel, check the Razorpay
                dashboard — is the right advice for a LIVE counter that has gone
                quiet. It is the wrong first move for one whose money service is
                running against its own simulator, where there is no Razorpay
                account at the other end to have stopped calling. Both cases
                drew the identical red panel and the identical instruction, and
                telling them apart meant scrolling to a small grey pill in the
                next card down. The mode is a fact the money service reports
                about itself; it is printed, not interpreted. */}
            {money.reachable && money.mode && (
              <div
                style={{
                  display: 'flex', flexDirection: 'column', gap: 8,
                  marginTop: 'var(--s4)', padding: 'var(--s4)',
                  background: 'var(--surface-2)',
                  border: '1px solid var(--line)',
                  borderRadius: 'var(--r-md)',
                }}
              >
                <span
                  style={{
                    font: '700 var(--t-micro)/1 var(--font)',
                    letterSpacing: 'var(--tr-caps)', textTransform: 'uppercase',
                    color: 'var(--ink-4)',
                  }}
                >
                  the gateway behind this
                </span>
                <span style={{ fontSize: 'var(--t-small)', lineHeight: 'var(--lh-body)', color: 'var(--ink-2)' }}>
                  {money.mode === 'live' ? (
                    <>
                      <Pill tone="code">LIVE</Pill>{' '}Razorpay, with the key beginning{' '}
                      <span className="mono">{money.key_id_prefix ?? '—'}</span>. A webhook that
                      never arrives here is a path that is actually broken, and the tunnel and the
                      dashboard are where to look.
                    </>
                  ) : (
                    <>
                      <span className="pill bk-quiet">{money.mode.toUpperCase()}</span> — the money
                      service is running against its own built-in simulator, not a Razorpay account.
                      Payments are produced and signed locally, and no real money moves in this
                      state. So a quiet reading above can be describing the mode rather than a
                      broken path: check this before the tunnel and the dashboard.
                    </>
                  )}
                </span>
              </div>
            )}

            <p className="hint">
              Only a signature-verified webhook can turn a bill green; the browser can refuse a
              payment but never grant one. If nothing is arriving, a customer can pay in full and the
              till will sit there saying nothing happened. A quick tunnel gets a new address every
              restart and can be revoked while this process keeps running — so a path that worked this
              morning is not evidence that it works now.
            </p>
            <KV k="counted silent after">{manage.ago(body.webhook.silent_after_seconds)} without one</KV>
            <KV k="the money service">
              {money.reachable
                ? <>answering at <span className="mono">{money.base_url}</span></>
                : <span style={{ color: 'var(--red)' }}>not answering — see the card below</span>}
            </KV>
          </Card>

          <Card title="The money service">
            {money.reachable ? (
              <>
                <div className="bk-kv2">
                  <KV k="answering at"><Hash v={money.base_url} /></KV>
                  <KV k="mode">
                    {money.mode === 'live'
                      ? <Pill tone="code">LIVE</Pill>
                      : <span className="pill bk-quiet">{money.mode ?? '—'}</span>}
                  </KV>
                  <KV k="gateway key"><span className="mono">{money.key_id_prefix ?? '—'}</span></KV>
                  <KV k="gateway secret">
                    {money.key_secret_configured
                      ? <span className="pill bk-quiet">CONFIGURED</span>
                      : <Pill tone="bad">MISSING</Pill>}
                  </KV>
                  <KV k="webhook secret">
                    {money.webhook_secret_configured
                      ? <span className="pill bk-quiet">CONFIGURED</span>
                      : <Pill tone="bad">MISSING</Pill>}
                  </KV>
                  <KV k="prices it can see">{money.price_book_entries ?? '—'}</KV>
                  <KV k="sessions held">{money.sessions ?? '—'}</KV>
                  <KV k="intents">{money.intents ?? '—'}</KV>
                  {byState && (
                    <KV k="intents by outcome">
                      <span className="mono">
                        {Object.entries(byState)
                          .sort((a, b) => b[1] - a[1])
                          .map(([k, v]) => `${k} ${v}`)
                          .join(' · ')}
                      </span>
                    </KV>
                  )}
                </div>

                {/* A HEALTH READOUT THAT ONLY COUNTS ESCALATIONS IS A TAUTOLOGY,
                    NOT A MEASUREMENT — paisa.py says so where the histogram is
                    built. This card used to print `intents: 282` and then
                    suppress its one amber verdict because `intents_needing_human`
                    was 0, while 269 of those intents sat in INDETERMINATE. The
                    verdict's own words — the gateway was called and the outcome
                    is unknown — are the DEFINITION of that state, and they were
                    wired to a different column.

                    Amber, because that is what this counter wears when it
                    abstained: it called out, it did not hear back, and it will
                    not say which way it went. */}
                {unknownOutcome > 0 && (
                  <Verdict
                    tone="amber"
                    title={`${unknownOutcome} payment${unknownOutcome === 1 ? '' : 's'} of unknown outcome`}
                  >
                    The gateway was called and the outcome is unknown, so money may have moved.
                    {indeterminate > 0 && <> {indeterminate} sat down as INDETERMINATE.</>}
                    {calling > 0 && <> {calling} {calling === 1 ? 'is' : 'are'} still CALLING.</>}{' '}
                    Nothing settles these on its own, and nothing here turns green until a
                    signature-verified webhook says the money arrived.
                  </Verdict>
                )}

                {typeof money.intents_needing_human === 'number' && money.intents_needing_human > 0 && (
                  <Verdict tone="amber" title={`${money.intents_needing_human} payments need a person`}>
                    The money service has escalated these itself: it stopped retrying and is waiting
                    for somebody to reconcile them against the gateway&rsquo;s own dashboard.
                  </Verdict>
                )}
                {byState === null && (
                  <p className="hint">
                    This counter did not report how those intents ended, so this card cannot say how
                    many of them have a known outcome. A count of escalations on its own is not that
                    answer.
                  </p>
                )}
                <p className="hint">
                  The two secrets are reported as yes or no and nothing more. This page never reads
                  either value, and there is no request it can make that would return one.
                </p>
              </>
            ) : (
              <>
                <Verdict tone="red" title="The money service did not answer">
                  {money.detail ?? `Nothing responded at ${money.base_url}.`} Until it does, this
                  counter can show a bill but cannot charge one.
                </Verdict>
                <KV k="expected at"><span className="mono">{money.base_url}</span></KV>
                <p className="hint">
                  It is the only process holding gateway credentials, so it runs separately on
                  purpose. Start it with <span className="mono">make serve-money</span>.
                </p>
              </>
            )}
          </Card>
        </Section>

        <Section
          label="Recognition"
          sub="the gates every path is judged under, and the mat that gives it millimetres"
        >
          <div className="grid two">
            <Card title="How sure it has to be">
              <KV k="appearance match (phi)"><b className="tnum">{recognition.phi}</b></KV>
              <KV k="lead over second place (theta)"><b className="tnum">{recognition.theta}</b></KV>
              <KV k="size tolerance (tau)"><b className="tnum">{recognition.tau_mm} mm</b></KV>
              <KV k="photo-taught bar"><b className="tnum">{recognition.phi_appearance_only}</b></KV>
              <KV k="read from">
                {catalogue.gates_from_disk
                  ? <Pill tone="code">THE CATALOGUE</Pill>
                  : <Pill tone="amb">LIBRARY DEFAULTS</Pill>}
              </KV>
              <p className="hint">
                {catalogue.gates_from_disk ? (
                  <>These are the gates the catalogue was built under, and it refuses to reopen under
                  different ones — so a product taught yesterday cannot be judged today by a looser
                  bar. An item that does not clear them is amber and is left off the total.</>
                ) : (
                  <>Nothing has been taught yet, so these are the library defaults. The first
                  enrolment writes them into the catalogue and they are fixed for it from then on.</>
                )}
              </p>
              <p className="hint">
                A product taught from a photograph has no millimetres, so the size check can never run
                for it. It is judged at the higher bar instead — {recognition.phi_appearance_only}{' '}
                against {recognition.phi} — because one discriminator is gone.
              </p>
            </Card>

            <Card title="The printed mat">
              <KV k="sheet">{mat.sheet} — {mat.width_mm} × {mat.height_mm} mm</KV>
              <KV k="markers">{mat.markers} ArUco squares, {mat.marker_mm} mm each</KV>
              <KV k="margin">{mat.margin_mm} mm from the sheet edge</KV>
              <KV k="rectified to">
                <span className="tnum">{mat.rectified_buffer_px[0]} × {mat.rectified_buffer_px[1]} px</span>
              </KV>
              <KV k="scale error allowed">
                {(mat.max_scale_error * 100).toFixed(1)}% on the worst marker side
              </KV>
              <KV k="tilt allowed">up to {mat.max_tilt_deg}°</KV>
              <p className="hint">
                The four markers are a known distance apart, so a lock gives the counter a real
                millimetre scale. That is what lets a wrong-sized packet be thrown out by the tape
                measure before its appearance is ever consulted.
              </p>
            </Card>
          </div>
        </Section>

        <Section
          label="The catalogue"
          sub="what has been taught, and by which of the three ways"
        >
          <Card title="The catalogue">
            <div className="bk-kv2">
              <KV k="products">{catalogue.count}</KV>
              <KV k="taught on the mat">{catalogue.by_taught['mat_measured'] ?? 0}</KV>
              <KV k="taught from a photo">{catalogue.by_taught['appearance_only'] ?? 0}</KV>
              <KV k="taught by code alone">{catalogue.by_taught['product_code_only'] ?? 0}</KV>
              <KV k="printed codes bound">{catalogue.codes_bound}</KV>
              <div className="bk-kv-wide"><KV k="kept in"><Hash v={catalogue.dir} /></KV></div>
            </div>
            {catalogue.orphan_code_bindings.length > 0 && (
              <Verdict tone="amber" title={`${catalogue.orphan_code_bindings.length} codes point at nothing`}>
                <span className="mono">{catalogue.orphan_code_bindings.join(', ')}</span> — bound to
                a product that is not in the catalogue, so scanning one prices nothing.
              </Verdict>
            )}
            {catalogue.problems.length > 0 && (
              <Verdict tone="amber" title="Part of the catalogue could not be read">
                {catalogue.problems.map((p) => (
                  <span className="mono blk" key={p.file + p.detail}>{p.file}: {p.detail}</span>
                ))}
              </Verdict>
            )}
            {catalogue.count === 0 && (
              <Empty
                icon={<IcoTag size={22} />}
                title="Nothing has been taught"
                action={
                  <Button variant="primary" onClick={() => { location.hash = '#/products'; }}>
                    TEACH A PRODUCT
                  </Button>
                }
              >
                The till cannot price anything until something is. Teaching the first product also
                writes the recognition gates above into the catalogue, and they are fixed for it
                from then on — so the figures in that card are library defaults until it happens.
              </Empty>
            )}
          </Card>
        </Section>

        <Section
          label="The record"
          sub="the hash chain every screen in this section is rebuilt from"
        >
          <Card
            title="The audit chain"
            aside={ledger.chain_ok
              ? <Pill tone="code">VERIFIES</Pill>
              : <Pill tone="bad" dot>BROKEN</Pill>}
          >
            {!ledger.chain_ok && (
              <Verdict tone="red" title="The chain does not verify from the beginning">
                Every screen in this section is showing only what was recorded before the break, and
                nothing has been filled in after it.
                <br />
                <span className="mono">{ledger.error}</span>
              </Verdict>
            )}
            {/* THE FIFTH CARD OF LIVE FIGURES GETS AN EMPTY STATE TOO. A chain
                with no lines rendered as six rows of noughts and a head of
                sixty-four zeros, which reads as a counter reporting on itself
                rather than as one that has not been used yet. */}
            {ledger.lines === 0 ? (
              <Empty title="Nothing has been recorded yet">
                Every money action and every perception decision appends one line to this chain,
                and it is what all of these screens are rebuilt from. It is written the first time
                the till closes a basket — until then there is nothing to verify, which is not the
                same as a chain that failed to.
                <br />
                <span className="mono">{ledger.path}</span>
              </Empty>
            ) : (
              <div className="bk-kv2">
                <KV k="lines that verify"><span className="tnum">{ledger.lines}</span></KV>
                <KV k="sessions">{ledger.sessions}</KV>
                <KV k="bills closed">{ledger.bills_closed}</KV>
                <KV k="bills settled">
                  {ledger.bills_settled}
                  {ledger.bills_closed > 0 && ledger.bills_settled === 0 && (
                    <> — <span className="muted">nothing has been paid for on this chain</span></>
                  )}
                </KV>
                <div className="bk-kv-wide"><KV k="head"><Hash v={ledger.head} /></KV></div>
                <div className="bk-kv-wide"><KV k="kept in"><Hash v={ledger.path} /></KV></div>
              </div>
            )}
            <p className="hint">
              Every money action and every perception decision appends one line, and each line carries
              the hash of the one before it. Deleting or reordering a line breaks every hash after it,
              which is why the head above is worth more than a row count.
            </p>
          </Card>
        </Section>
      </div>
    </div>
  );
}
