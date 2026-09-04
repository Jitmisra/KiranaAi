import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useCamera } from '../hooks/useCamera';
import * as wp from '../lib/waapsiapi';
import * as api from '../lib/api';
import { rupees, type Paise } from '../lib/money';
import {
  Button, Card, Empty, KV, Pill, Refusal, Skeleton, Verdict,
} from '../components/ui';
import '../styles/waapsi.css';

/**
 * WAAPSI — वापसी — a return by camera, refunded by Razorpay.
 *
 * THE ONE THING THIS SCREEN PROVES: money went BACK, and it went back because
 * the gateway's own signed webhook said so — never because a button was
 * pressed. A refund the person asks for reads "refund requested" in NEUTRAL
 * ink and looks finished on its own (test-mode refunds take minutes); it turns
 * REFUNDED, in green, only when a signature-verified refund.processed lands.
 *
 * THE BROWSER IS NEVER AN AUTHOR. It sends pixels (the packet and the
 * customer's receipt QR in one frame) and a bill id it read off THIS counter's
 * own receipt code. Every rupee — that the bill settled, that this line was on
 * it, and what was CHARGED (the offer price on the day, off the chain, not
 * today's catalogue) — is the money service's, re-derived from the signed
 * audit chain before a paisa moves. A packet not on the bill is
 * `item_not_on_this_bill`; a second press is `already_refunded`; a bill no
 * webhook ever settled is `bill_not_settled`. Each is shown by its own name.
 */

type Err = { reason: string; detail?: string; extra?: Record<string, unknown> };

/** The state pill for one refund. GREEN only for PROCESSED — a refund the
    gateway confirmed with a signed webhook. Everything before that is neutral
    ink, because a refund merely asked for is not money that has moved. */
function RefundStatePill({ state, needsHuman }: { state: string; needsHuman?: boolean }) {
  if (needsHuman) return <Pill tone="off" dot>parked · needs a person</Pill>;
  if (state === 'PROCESSED') return <Pill tone="ok" dot>refunded</Pill>;
  if (state === 'REQUESTED') return <Pill tone="off" dot>refund requested</Pill>;
  if (state === 'CALLING' || state === 'NEW') return <Pill tone="off">asking the gateway</Pill>;
  if (state === 'INDETERMINATE') return <Pill tone="off">unknown · needs a person</Pill>;
  if (state === 'FAILED') return <Pill tone="off">refund failed</Pill>;
  return <Pill tone="off">{state}</Pill>;
}

/** A short "3 minutes ago" from an ISO stamp and the server's own clock. */
function ago(iso: string | null, now: string): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  const n = Date.parse(now) || Date.now();
  if (!Number.isFinite(t)) return null;
  const s = Math.max(0, Math.round((n - t) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  return `${Math.floor(s / 3600)} h ago`;
}

interface Target {
  sku_id: string;
  name: string;
  item_id: string;
  unit_paise: Paise;
  unit_rupees: string;
}

export default function Waapsi() {
  const cam = useCamera();
  const [mode, setMode] = useState<string | null>(null);

  const [session, setSession] = useState<string | null>(null);
  const [bill, setBill] = useState<wp.ReceiptBill | null>(null);
  const [billErr, setBillErr] = useState<Err | null>(null);
  const [loadingBill, setLoadingBill] = useState(false);

  const [scanning, setScanning] = useState(false);
  const [scan, setScan] = useState<wp.ReturnScan | null>(null);
  const [scanErr, setScanErr] = useState<Err | null>(null);

  const [refunding, setRefunding] = useState(false);
  const [refund, setRefund] = useState<wp.Refund | null>(null);
  const [refundErr, setRefundErr] = useState<Err | null>(null);
  const [simming, setSimming] = useState(false);

  const fileRef = useRef<HTMLInputElement>(null);

  // The mode banner: on the simulator, the shopkeeper is the gateway's back
  // office and can process the refund here. On the live gateway there is no
  // such button — the gateway does it on its own clock.
  useEffect(() => {
    void (async () => {
      const h = await api.moneyHealth();
      setMode(h.ok ? String((h as unknown as { mode?: string }).mode ?? '') : null);
    })();
  }, []);

  // #/waapsi?session=till_… opens straight onto that bill, prefilled from the
  // till's RETURN button. Read once, at mount.
  useEffect(() => {
    const q = location.hash.split('?')[1] ?? '';
    const want = new URLSearchParams(q).get('session');
    if (want) setSession(want);
  }, []);

  const loadBill = useCallback(async (sid: string) => {
    setLoadingBill(true);
    setBillErr(null);
    const r = await wp.bill(sid);
    if (r.ok) setBill(r);
    else { setBill(null); setBillErr({ reason: r.reason, detail: r.detail }); }
    setLoadingBill(false);
  }, []);

  useEffect(() => {
    if (session) void loadBill(session);
    else setBill(null);
  }, [session, loadBill]);

  const doScan = useCallback(async () => {
    if (scanning) return;
    const blob = await cam.capture({ x: 0, y: 0, w: cam.frame.w, h: cam.frame.h }, 0.92);
    if (!blob) { setScanErr({ reason: 'the camera gave nothing to read' }); return; }
    await runScan(blob);
  }, [cam, scanning]);

  const runScan = useCallback(async (blob: Blob) => {
    setScanning(true);
    setScanErr(null);
    setRefund(null);
    setRefundErr(null);
    const r = await wp.scan(blob);
    if (!r.ok) { setScanErr({ reason: r.reason, detail: r.detail }); setScanning(false); return; }
    setScan(r);
    if (r.receipt_session) setSession(r.receipt_session);
    setScanning(false);
  }, []);

  const onFile = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = '';
    if (f) await runScan(f);
  }, [runScan]);

  // The line the customer is returning: the SKU the packet's code named,
  // matched to a line on THIS bill that still has a packet not yet refunded.
  // A packet whose SKU is not a line here is the `item_not_on_this_bill` case,
  // and it is shown as one before REFUND is ever pressed.
  const scannedSkus = useMemo(
    () => (scan?.items ?? []).map((i) => i.sku_id).filter((s): s is string => !!s),
    [scan],
  );
  const targets: Target[] = useMemo(() => {
    if (!bill) return [];
    const out: Target[] = [];
    // When a packet was scanned, only its SKUs; otherwise (prefilled from the
    // till) every returnable line on the bill.
    const want = scannedSkus.length > 0 ? new Set(scannedSkus) : null;
    for (const line of bill.lines) {
      if (want && !want.has(line.sku_id)) continue;
      if (line.unit_paise === null) continue;
      const item = line.returnable_item_ids[0];
      if (!item) continue;
      out.push({
        sku_id: line.sku_id, name: line.name, item_id: item,
        unit_paise: line.unit_paise, unit_rupees: line.unit_rupees ?? rupees(line.unit_paise),
      });
    }
    return out;
  }, [bill, scannedSkus]);

  // A scanned packet whose SKU is on no line of this bill: name it, so the
  // refusal is on screen before a button is pressed.
  const notOnBill = useMemo(() => {
    if (!bill || scannedSkus.length === 0) return [];
    const onBill = new Set(bill.lines.map((l) => l.sku_id));
    return scannedSkus.filter((s) => !onBill.has(s));
  }, [bill, scannedSkus]);

  const doRefund = useCallback(async (t: Target) => {
    if (refunding) return;
    setRefunding(true);
    setRefundErr(null);
    setRefund(null);
    const r = await wp.refund({
      session_id: session!, item_id: t.item_id, sku_id: t.sku_id,
      amount_paise: t.unit_paise,
    });
    if (!r.ok) { setRefundErr({ reason: r.reason, detail: r.detail, extra: r.extra }); }
    else setRefund(r);
    setRefunding(false);
    if (session) void loadBill(session);
  }, [refunding, session, loadBill]);

  // Poll a REQUESTED refund until the signed webhook turns it PROCESSED (or
  // FAILED). The screen LEARNS the outcome; it never asserts it. On the
  // simulator the operator drives the callback with the button below instead.
  useEffect(() => {
    if (!refund || refund.state === 'PROCESSED' || refund.state === 'FAILED') return;
    if (mode === 'sim') return; // the operator processes it explicitly
    let alive = true;
    const id = setInterval(async () => {
      const r = await wp.view(refund.refund_key);
      if (alive && r.ok) {
        setRefund(r);
        if (r.state === 'PROCESSED' || r.state === 'FAILED') {
          clearInterval(id);
          if (session) void loadBill(session);
        }
      }
    }, 3000);
    return () => { alive = false; clearInterval(id); };
  }, [refund, mode, session, loadBill]);

  const simProcess = useCallback(async (outcome: 'processed' | 'failed') => {
    if (!refund || simming) return;
    setSimming(true);
    const r = await wp.simProcess(refund.refund_key, outcome);
    if (r.ok) setRefund(r);
    else setRefundErr({ reason: r.reason, detail: r.detail });
    setSimming(false);
    if (session) void loadBill(session);
  }, [refund, simming, session, loadBill]);

  const reset = useCallback(() => {
    setSession(null); setBill(null); setBillErr(null);
    setScan(null); setScanErr(null); setRefund(null); setRefundErr(null);
    location.hash = '#/waapsi';
  }, []);

  return (
    <div className="wp-page">
      <div className="page-head">
        <h1>Returns <span className="wp-deva" lang="hi">वापसी</span></h1>
        <p>
          A return by camera. Hold up the packet and the customer&rsquo;s receipt QR together;
          this counter resolves the bill, checks it was settled, and refunds the line through
          Razorpay. The money goes back only when the gateway&rsquo;s own signed webhook confirms it.
        </p>
      </div>

      <div className="wp-grid">
        {/* ---------------------------------------------------------- camera -- */}
        <Card title="Hold it up" sub="the packet and the paper receipt's QR, in one frame" flush>
          <div className="stage wp-stage">
            <video
              ref={cam.videoRef}
              playsInline
              muted
              style={{ display: cam.running ? 'block' : 'none' }}
            />
            {!cam.running && (
              <div className="camgate">
                <h3>{cam.error ? 'The camera did not start' : 'Start the camera, or use a photo'}</h3>
                <p>Nothing is uploaded until you press SCAN. A photo from the gallery works the same way.</p>
                <div className="btn-row" style={{ justifyContent: 'center' }}>
                  <Button variant="primary" onClick={() => void cam.start()}>Start camera</Button>
                  <Button variant="ghost" className="wp-ghost" onClick={() => fileRef.current?.click()}>Use a photo</Button>
                </div>
                {cam.error && <p className="wp-camerr">{cam.error}</p>}
              </div>
            )}
            <div className="stage-bar">
              {cam.running
                ? <><span className="mono">{cam.frame.w}×{cam.frame.h} px</span><span>live · nothing uploaded until you press SCAN</span></>
                : <span>camera off</span>}
            </div>
          </div>
          <input ref={fileRef} type="file" accept="image/*" hidden onChange={onFile} />
          <div className="wp-scanrow">
            <Button variant="primary" onClick={() => void doScan()}
                    disabled={!cam.running || scanning}>
              {scanning ? 'READING…' : 'SCAN the packet + receipt'}
            </Button>
            {(session || scan) && (
              <Button variant="ghost" onClick={reset}>Start over</Button>
            )}
          </div>
          {scanErr && <div className="wp-mt"><Refusal reason={scanErr.reason} detail={scanErr.detail} /></div>}
          {scan && !scan.receipt_session && !session && (
            <Verdict tone="amber" title="No receipt code in the frame">
              A return needs the bill it is against. Hold the customer&rsquo;s paper receipt&rsquo;s QR up
              beside the packet — it is this counter&rsquo;s own bill link, and the money service checks
              it against the signed audit chain before any refund is asked for.
            </Verdict>
          )}
          {scan && scan.items.length > 0 && (
            <div className="wp-seen">
              <div className="eyebrow">Read off the packet</div>
              {scan.items.map((it, i) => (
                <KV k={<span className="mono">{it.code}</span>} key={`${it.code}-${i}`}>
                  <span style={{ color: it.sku_id ? 'var(--ink)' : 'var(--amber)' }}>
                    {it.name ?? it.sku_id ?? 'not a taught product code'}
                    {it.price_rupees && <> · {rupees(it.price_paise as Paise)}</>}
                  </span>
                </KV>
              ))}
            </div>
          )}
        </Card>

        {/* ------------------------------------------------------------ bill -- */}
        <div className="wp-detail">
          {!session && !loadingBill && (
            <Card title="The bill" sub="scan a receipt, or open a return from the till">
              <Empty title="No bill yet">
                Hold up the packet and the receipt QR, or press RETURN on a bill on the till.
              </Empty>
            </Card>
          )}
          {loadingBill && <Skeleton w="100%" h={160} />}
          {billErr && <Refusal reason={billErr.reason} detail={billErr.detail} />}

          {bill && (
            <Card
              title={<>Bill <span className="mono">{bill.session_id}</span></>}
              sub={bill.at_human ?? bill.at ?? undefined}
            >
              {/* SETTLEMENT — a refund is only possible on a bill a signed
                  webhook settled, and the proof is shown as it came. */}
              {bill.settled_by_verified_webhook ? (
                <p className="wp-settled">
                  <span className="wp-green">settled</span> via{' '}
                  <span className="mono">{bill.payment_id ?? 'a signed webhook'}</span>
                  {bill.settled_at_human && <> · {bill.settled_at_human}</>}
                </p>
              ) : (
                <Verdict tone="amber" title={bill.payment_headline}>
                  A refund is only possible on a bill the payment gateway&rsquo;s own signed
                  callback settled. This bill does not carry one, so nothing can be sent back.
                </Verdict>
              )}

              <div className="wp-billtotal">
                <span>Bill total</span>
                <span className="tnum">{bill.total_rupees}</span>
              </div>
              {bill.refunded_paise > 0 && (
                <div className="wp-refunded-line">
                  <span className="wp-green tnum">{bill.refunded_rupees}</span> of{' '}
                  <span className="tnum">{bill.total_rupees}</span> refunded
                  {bill.refund_requested_paise > 0 && (
                    <> · <span className="tnum">{bill.refund_requested_rupees}</span> more requested</>
                  )}
                </div>
              )}

              {/* The line(s) to send back. */}
              {notOnBill.length > 0 && (
                <Verdict tone="red" title="item_not_on_this_bill">
                  <span className="mono">{notOnBill.join(', ')}</span> was read off a packet but is
                  not a line on this bill. Nothing is refunded for a packet this bill did not sell.
                </Verdict>
              )}

              {bill.settled_by_verified_webhook && targets.length > 0 && !refund && (
                <div className="wp-targets">
                  {targets.map((t) => (
                    <div className="wp-target" key={t.item_id}>
                      <div className="wp-target-what">
                        <span className="nm">{t.name}</span>
                        <span className="muted">{t.unit_rupees} <span className="wp-offer">offer price at sale</span></span>
                      </div>
                      <Button variant="pay" onClick={() => void doRefund(t)} disabled={refunding}>
                        {refunding ? 'ASKING…' : `REFUND ${t.unit_rupees}`}
                      </Button>
                    </div>
                  ))}
                </div>
              )}

              {bill.settled_by_verified_webhook && targets.length === 0 && !refund && scannedSkus.length > 0 && notOnBill.length === 0 && (
                <Verdict tone="amber" title="already_refunded">
                  Every packet of this on the bill has already been refunded. One line goes back once.
                </Verdict>
              )}

              {refundErr && (
                <div className="wp-mt">
                  <Refusal reason={refundErr.reason} detail={refundErr.detail} />
                  {typeof refundErr.extra?.charged_paise === 'number' && (
                    <p className="hint">
                      The chain says <b>{rupees(refundErr.extra.charged_paise as Paise)}</b> was
                      charged for this line. What goes back is what was charged.
                    </p>
                  )}
                </div>
              )}

              {/* THE REFUND, once asked for. Neutral ink while requested; green
                  only once a signed refund.processed has confirmed it. */}
              {refund && (
                <div className={refund.state === 'PROCESSED' ? 'wp-refund done' : 'wp-refund'}>
                  <div className="wp-refund-head">
                    <RefundStatePill state={refund.state} needsHuman={refund.needs_human} />
                    <span className="wp-refund-amt tnum">{refund.amount_rupees}</span>
                    <span className="muted">{refund.sku_id}</span>
                  </div>
                  {refund.state === 'PROCESSED' ? (
                    <div className="wp-refunded">
                      <svg className="wp-check" viewBox="0 0 56 56" aria-hidden="true">
                        <circle cx="28" cy="28" r="25.5" />
                        <path d="M17.5 29.5 25 37 38.5 21" />
                      </svg>
                      <div className="wp-refunded-word">Refunded</div>
                      <p>
                        The gateway&rsquo;s signed <span className="mono">refund.processed</span> confirmed{' '}
                        <b>{refund.amount_rupees}</b> went back
                        {refund.gateway_refund_id && <> · <span className="mono">{refund.gateway_refund_id}</span></>}.
                        {bill && <> This bill now shows <b>{bill.refunded_rupees} of {bill.total_rupees}</b> refunded.</>}
                      </p>
                      <p className="hint">Stock +1 for {refund.sku_id}, and the points for these paise are clawed back — both derived from this same signed line.</p>
                    </div>
                  ) : refund.state === 'FAILED' ? (
                    <p className="wp-neutral">
                      The gateway could not process this refund. The line is free to try again.
                    </p>
                  ) : (
                    <p className="wp-neutral">
                      Asked for {ago(refund.requested_ts, refund.now) ?? 'just now'}. A test-mode refund
                      can take minutes; this stays in neutral ink and turns green only on the gateway&rsquo;s
                      own signed <span className="mono">refund.processed</span>. Nothing on this counter
                      may grant that.
                    </p>
                  )}

                  {mode === 'sim' && refund.state === 'REQUESTED' && (
                    <div className="wp-sim">
                      <div className="eyebrow">Simulator only</div>
                      <p className="hint">
                        The money service is on the simulator, so the gateway&rsquo;s back office is this
                        box. Processing here produces a SIGNED refund.processed and goes through the same
                        gates a real one would.
                      </p>
                      <div className="btn-row">
                        <Button onClick={() => void simProcess('processed')} disabled={simming}>
                          {simming ? 'PROCESSING…' : 'Gateway processes the refund'}
                        </Button>
                        <Button variant="ghost" onClick={() => void simProcess('failed')} disabled={simming}>
                          Gateway fails it
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
