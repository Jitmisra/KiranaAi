import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as api from '../lib/api';
import { rupees, totalPaise, type Paise } from '../lib/money';
import { defaultRoi, clampDrag, isWholeFrame, type Roi, type ReadMode } from '../lib/roi';
import { Overlay } from '../lib/overlay';
import { voices } from '../lib/audio';
import { addToBasket, decLine, incLine, removeLine, type BasketLine } from '../lib/counter';
import { diagnoseInbound, ago } from '../lib/inbound';
import { useT } from '../lib/i18n';
import { useCamera } from '../hooks/useCamera';
import { useScanLoop } from '../hooks/useScanLoop';
import { Card, KV, Pill, Verdict, Segmented, Empty, Refusal, Thinking, IcoReceipt, Modal, Field, Input } from '../components/ui';
import { billState, publish } from '../lib/displaybus';
import VoiceBar from '../components/VoiceBar';
import * as assistantapi from '../lib/assistantapi';
import * as kh from '../lib/khataapi';
import type { ProposedLine, BookProposal } from '../lib/voice';
import '../styles/till.css';

type Payment = { session_id: string; short_url: string; amount_paise: number; scan_id: string };

/**
 * One line Salaahkaar put on the bill and nobody has accepted yet. The
 * server's own line, plus an id so two proposals of the same product are two
 * rows a person can accept or drop separately.
 */
type Proposed = ProposedLine & { id: string; proposal_id: string };

/**
 * A `/shop` row under an offer carries the shelf-edge price beside the charged
 * one. The shared Sku type does not know these fields (they exist only when a
 * discount is live), so they are read through this local widening rather than
 * by editing api.ts, which other screens own.
 */
type ShopRow = api.Sku & { marked_paise?: number; off_paise?: number };

/**
 * How long to wait before saying anything about WHY. Long enough that a
 * customer opening their UPI app is not accused of a dead tunnel; short enough
 * that a shopkeeper is not left staring at a spinner for 78 seconds, which is
 * what actually happened on a payment that had already settled.
 */
const DIAGNOSE_AFTER_MS = 25_000;

export default function Till() {
  const { t, tx, tn, tnx } = useT();
  const cam = useCamera();
  const overlay = useMemo(() => new Overlay(), []);
  const ovRef = useRef<HTMLCanvasElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);

  const [read, setRead] = useState<ReadMode>('code');
  const [roi, setRoi] = useState<Roi | null>(null);
  const [roiUserSet, setRoiUserSet] = useState(false);
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const [dragNote, setDragNote] = useState<string | null>(null);

  const [basket, setBasket] = useState<ReadonlyMap<string, BasketLine>>(new Map());
  // Bumped ONLY when the bill is emptied — see ScanLoopArgs.billGeneration.
  const [billGeneration, setBillGeneration] = useState(0);
  /**
   * ONE session id per bill, not one per press of CHARGE.
   *
   * paisa keys its intents on (session_id, cycle, amount) and passes that nonce
   * to Razorpay as `reference_id`. A fresh id on every press defeats both the
   * link cache and the gateway's own duplicate rejection, so a lost response, a
   * double-click, or CANCEL-then-CHARGE mints a SECOND live payment link for
   * the same basket — and the audit log shows three 1000-paise links inside 31
   * seconds. Retrying with the same id replays the same link instead.
   *
   * Cleared in exactly two places: when the session is PAID, and when the bill
   * is cleared. Deliberately NOT cleared on cancel or on refusal — that is
   * precisely when a retry must land on the link that already exists.
   */
  const sessionRef = useRef<string | null>(null);
  const [muted, setMuted] = useState(false);

  const [charging, setCharging] = useState(false);
  const [payment, setPayment] = useState<Payment | null>(null);
  const [payState, setPayState] = useState<string>('');
  /** Set once we can say something sharper than "still waiting". Never a payment claim. */
  const [inbound, setInbound] = useState<{ seen: number; lastAt: string | null; waitedS: number } | null>(null);
  /** The last whole-counter read. Held so the operator can see what was SEEN but not named. */
  const [sweep, setSweep] = useState<api.CounterRead | null>(null);
  const [sweeping, setSweeping] = useState(false);
  /**
   * THE SWEEP, STAGE BY STAGE.
   *
   * `sweeping` alone only ever changed a button label, so a whole-counter read
   * — a full-frame upload plus a server-side segmentation, seconds on a shop
   * laptop — looked identical to a press that had done nothing. These two say
   * which half of it is running, and they are set from the CLIENT's own
   * progress: the photograph is taken here, and the upload is started here.
   * Neither of them claims a stage of the server's that this page cannot see.
   */
  const [sweepStage, setSweepStage] = useState<'photo' | 'send' | null>(null);
  const [sweepRefusal, setSweepRefusal] = useState<{ reason: string; detail?: string } | null>(null);
  /** Same rule for CHARGE, which is the longer wait and the one that costs money. */
  const [chargeStage, setChargeStage] = useState<'photo' | 'witness' | 'mint' | null>(null);
  /**
   * THE CATALOGUE NO LONGER LIVES ON THIS SCREEN.
   *
   * It was fetched here for the microphone, which matched heard words against
   * it in the browser, and a fetch that refused had to be drawn as a refusal
   * so the mic did not answer "I do not know it" to every word over an empty
   * list. Salaahkaar now asks the server to resolve what she hears, and an
   * empty or unreadable catalogue comes back from there as a refusal with its
   * own name. What this screen still reads from `/shop` is the offer facts
   * below, and those are display only.
   */
  /**
   * sku_id -> shelf-edge price in paise, for lines whose product is under a
   * live offer. Display only: the bill shows the marked price struck through
   * beside what is charged, because a cheaper line the shopkeeper cannot
   * explain is worse than no offer at all. The charged amount still comes from
   * the server-priced catalogue; nothing here computes money.
   */
  const [marked, setMarked] = useState<ReadonlyMap<string, number>>(new Map());
  /**
   * WHAT SALAAHKAAR PUT ON THE BILL, AND NOBODY HAS AGREED TO YET.
   *
   * Amber rows under the receipt: the server's own proposal, priced in its
   * integer paise. They are NOT in `basket`, so they are not in the total, not
   * on the customer display and not chargeable. ACCEPT moves one into the
   * basket, and the camera still has to witness it before CHARGE — voice
   * changes who types the line, not what it takes to charge for it.
   */
  const [proposed, setProposed] = useState<ReadonlyArray<Proposed>>([]);
  /** What happened to lines Salaahkaar held for this till elsewhere. */
  const [heldNote, setHeldNote] = useState<string | null>(null);

  /**
   * LINES SHE HELD FOR THE TILL, FROM ANOTHER SCREEN.
   *
   * A shopkeeper on the Salaahkaar page says "2 Maggi bill me daal do" and
   * accepts; the card says "held for the till — not billed". Nothing on the
   * till read that hold, so the lines never arrived: a verifier found
   * `heldForTill()` with zero consumers. This is the consumer.
   *
   * THE PRICE IS THE TILL'S, NOT THE HOLD'S. A held line carries the paise it
   * was proposed at for one purpose — comparison. The till re-prices every
   * line from the catalogue it is billing from right now; a line whose price
   * moved arrives PROPOSED with the change said on it, and a product the
   * catalogue no longer has is left off and named. Nothing here reaches the
   * basket without the same accept a spoken line needs.
   */
  useEffect(() => {
    let alive = true;
    const take = async () => {
      const batches = assistantapi.heldForTill();
      if (batches.length === 0) return;
      const cat = await api.shop();
      if (!alive) return;
      if (!cat.ok) {
        setHeldNote(t('till.bill.held.noCatalogue', { n: batches.reduce((a, b) => a + b.lines.length, 0) }));
        return;
      }
      const price = new Map(cat.skus.map((k) => [k.sku_id, { name: k.name, paise: k.price_paise }]));
      const rows: Proposed[] = [];
      const moved: string[] = [];
      const gone: string[] = [];
      for (const b of batches) {
        b.lines.forEach((l, i) => {
          const live = price.get(l.sku_id);
          if (!live) { gone.push(l.name); return; }
          const changed = live.paise !== l.proposed_unit_paise;
          if (changed) moved.push(`${live.name}: ${rupees(l.proposed_unit_paise)} → ${rupees(live.paise as Paise)}`);
          rows.push({
            sku_id: l.sku_id, name: live.name, qty: l.qty, by: 'packet',
            unit_paise: live.paise, line_paise: live.paise * l.qty,
            heard: changed ? t('till.bill.held.repriced') : t('till.bill.held.heard'),
            id: `held-${b.proposal_id}-${i}`, proposal_id: b.proposal_id,
          });
        });
      }
      if (rows.length > 0) setProposed((cur) => [...cur, ...rows]);
      const notes: string[] = [];
      if (rows.length > 0) notes.push(t('till.bill.held.arrived', { n: rows.length }));
      if (moved.length > 0) notes.push(t('till.bill.held.moved', { list: moved.join('; ') }));
      if (gone.length > 0) notes.push(t('till.bill.held.gone', { list: gone.join(', ') }));
      setHeldNote(notes.join(' ') || null);
      // Consumed. The hold is a hand-off, not a record: the record is the
      // till's own proposed list, and the chain once a line is billed.
      assistantapi.clearHeld();
    };
    void take();
    // Another tab holding lines while this till is open: same hand-off.
    const onStorage = (e: StorageEvent) => { if (e.key && e.key.includes('assistant.accepted')) void take(); };
    addEventListener('storage', onStorage);
    return () => { alive = false; removeEventListener('storage', onStorage); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  /** `paise` is the integer the gateway minted, kept beside the rupee string so
      the customer display can be told the amount without re-parsing money. */
  const [paid, setPaid] = useState<{ total: string; paise: number; session: string } | null>(null);
  const [chargeRefusal, setChargeRefusal] = useState<{ reason: string; detail?: string } | null>(null);
  /**
   * ON THE BOOK — the bill closes onto a customer's khata instead of onto a
   * payment link. The SAME evidence as a charge: the counter photographs the
   * bill and the money service re-derives it from the witness before the
   * kernel closes the bill as BOOKED. It is not green, not amber, not red.
   *
   * `bookProposal` is what Salaahkaar heard ("Sharma ji ke khate mein likh
   * do"): a household to book onto, drawn in neutral ink beside the bill until
   * a person presses ACCEPT, which opens the sheet with the name and, when the
   * book knows the household, the number already filled in.
   */
  const [bookOpen, setBookOpen] = useState(false);
  const [booking, setBooking] = useState(false);
  const [bookName, setBookName] = useState('');
  const [bookPhone, setBookPhone] = useState('');
  const [bookRefusal, setBookRefusal] = useState<{ reason: string; detail?: string } | null>(null);
  const [booked, setBooked] = useState<kh.Booked | null>(null);
  const [bookProposal, setBookProposal] = useState<BookProposal | null>(null);
  const [witnessLines, setWitnessLines] = useState<Array<{ code: string; text: string; ok: boolean }>>([]);
  /**
   * /qr/link answers 400 + JSON for five reasons — the ordinary one being
   * `paisa_unreachable`, i.e. any timeout. The <img> had no onError, so the
   * shopkeeper got a BROKEN IMAGE ICON under the words "Scan to pay ₹99.00",
   * beside a caption still asserting that a render had happened. Worse, when
   * the reason is `refused_to_encode_this_string` the page was handing them
   * that exact rejected URL as a live link.
   */
  const [qrRefusal, setQrRefusal] = useState<{ reason: string; detail?: string } | null>(null);
  /**
   * A witness taken the MOMENT the whole bill was visible together.
   *
   * CHARGE photographs the counter when pressed, and a bill assembled by
   * holding packets up one at a time can never be covered by one photograph
   * unless they are all in view at once. Requiring that at press time meant a
   * two-item bill was uncharegable unless the shopkeeper juggled both packets
   * with their free hand.
   *
   * So the counter watches, and the instant it can see everything on the bill
   * it writes the witness down and keeps it. Put the packets down afterwards
   * and the button stays armed — the evidence was already taken.
   *
   * This grants nothing: it is the SAME server-authored witness, and paisa
   * still reloads it by id and re-prices every line from its own tables. It
   * only moves WHEN the photograph is taken, not who decides what it says.
   */
  const [heldWitness, setHeldWitness] = useState<
    { scan_id: string; paise: number; at: number } | null>(null);
  const capturing = useRef(false);
  /**
   * The payment poll is keyed on `payment` ALONE, so that the 3-second interval
   * and the elapsed seconds it reports survive everything else on the screen
   * changing. It still has to speak the language in force when it finally has
   * something to say, so it reads the translator through a ref rather than
   * taking it as a dependency — naming `t` there would restart the interval,
   * and the counter would tell the shopkeeper it had been waiting 3 seconds
   * when it had been waiting ninety.
   */
  const tRef = useRef(t);
  useEffect(() => { tRef.current = t; }, [t]);

  /* ---- the counter area -------------------------------------------------- */

  // Re-pick the default when the instrument changes, unless the shopkeeper drew
  // their own. A narrow look-mode rectangle carried into code mode would
  // silently eat codes, which is the exact bug this rule exists to prevent.
  useEffect(() => {
    if (roiUserSet || !cam.running) return;
    setRoi(defaultRoi(read, cam.frame));
  }, [read, cam.running, cam.frame, roiUserSet]);

  useEffect(() => {
    overlay.attach(ovRef.current);
  }, [overlay, cam.running]);

  useEffect(() => {
    voices.muted = muted;
  }, [muted]);

  // Arm the audio engine on the first touch of the page, whatever it is.
  // Unlocking only from START CAMERA left the counter silent for anyone who
  // clicked something else first, with nothing on screen to say why.
  useEffect(() => { voices.armOnFirstGesture(); }, []);

  // Keep the overlay canvas in the camera's pixel space so box coordinates
  // need no second scaling factor to be correct.
  useEffect(() => {
    const c = ovRef.current;
    if (!c) return;
    c.width = cam.frame.w;
    c.height = cam.frame.h;
    overlay.paint();
  }, [cam.frame, overlay, cam.running]);

  const toVideo = useCallback((e: React.PointerEvent) => {
    const el = stageRef.current;
    if (!el) return { x: 0, y: 0 };
    const r = el.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(cam.frame.w, Math.round((e.clientX - r.left) * (cam.frame.w / r.width)))),
      y: Math.max(0, Math.min(cam.frame.h, Math.round((e.clientY - r.top) * (cam.frame.h / r.height)))),
    };
  }, [cam.frame]);

  const onDragEnd = useCallback(() => {
    if (!drag) return;
    const res = clampDrag(drag, cam.frame);
    setDrag(null);
    if (res.ok) {
      setRoi(res.roi);
      setRoiUserSet(true);
      setDragNote(
        `Counter area set to ${res.roi.w}×${res.roi.h} px. Only this rectangle is uploaded.` +
        (read === 'code' ? ' A code outside it will not be read.' : ''),
      );
    } else {
      setDragNote(res.reason);
    }
  }, [drag, cam.frame, read]);

  const resetArea = useCallback(() => {
    // REDRAW returns to the DEFAULT area, never to no area at all. Clearing it
    // left the loop with nothing to crop and the till went quietly dead.
    setRoiUserSet(false);
    setRoi(cam.running ? defaultRoi(read, cam.frame) : null);
    setDragNote(null);
    overlay.clear();
  }, [cam.running, cam.frame, read, overlay]);

  /* ---- the loop ---------------------------------------------------------- */

  const onCommit = useCallback((lines: BasketLine[]) => {
    setBasket((b) => addToBasket(b, lines));
  }, []);

  useEffect(() => {
    let alive = true;
    void (async () => {
      const r = await api.shop();
      if (!alive) return;
      // A refusal here costs a strike-through, not a bill: the charged price
      // is the server's either way, so a `/shop` that would not answer leaves
      // the offer marks off and nothing else changes.
      if (!r.ok) return;
      // Carry the offer facts through so a discounted basket line can show
      // its strike-through. Guarded field by field: a row without a live
      // offer simply has neither field, and that is the common case.
      const m = new Map<string, number>();
      for (const s of r.skus as ShopRow[]) {
        if (typeof s.marked_paise === 'number' && (s.off_paise ?? 0) > 0) {
          m.set(s.sku_id, s.marked_paise);
        }
      }
      setMarked(m);
    })();
    return () => { alive = false; };
  }, [billGeneration]);

  /**
   * READ THE WHOLE COUNTER, ONCE.
   *
   * The continuous loop reads ONE subject per frame, which is right for a
   * packet held up to the camera and wrong for four things put down at once.
   * This is the second gesture: lay the shopping out, press once, and every
   * region that can be priced goes on the bill in one go.
   *
   * It adds only what it could NAME. What it saw and could not name is
   * reported, never guessed at and never quietly priced — that is invariant 7,
   * and it is what makes the count trustworthy enough to act on.
   */
  const readWholeCounter = useCallback(async () => {
    if (!cam.running || sweeping) return;
    setSweeping(true);
    setSweepStage('photo');
    setSweepRefusal(null);
    try {
      // The WHOLE frame, not the drawn rectangle: the point of this gesture is
      // that the operator has not told the counter where to look.
      const blob = await cam.capture({ x: 0, y: 0, w: cam.frame.w, h: cam.frame.h }, 0.92);
      if (!blob) {
        // A press that produced no frame used to return in silence, leaving
        // the button back at rest as though nothing had been asked of it.
        setSweepRefusal({
          reason: t('till.refuse.notPhotographed'),
          detail: 'The camera is running but gave no frame. Press STOP, then START CAMERA again.',
        });
        return;
      }
      setSweepStage('send');
      const r = await api.readCounter(blob);
      // A REFUSAL IS A RESULT. It used to be pushed into `dragNote` — the small
      // blue hint under the instrument row that otherwise says how big the
      // counter area is — where the server's reason read as a note about
      // cropping. It is a refusal and it is now drawn as one.
      if (!r.ok) { setSweep(null); setSweepRefusal({ reason: r.reason, detail: r.detail }); return; }
      const read = r as unknown as api.CounterRead;
      setSweep(read);
      const lines: BasketLine[] = read.items
        .filter((i) => i.sku_id && i.price_paise != null)
        .map((i) => ({
          sku_id: String(i.sku_id), name: i.name || String(i.sku_id),
          price_paise: Number(i.price_paise), qty: 1,
          code: i.code ?? undefined,
        }));
      if (lines.length) { onCommit(lines); voices.named(lines.length); }
      else voices.abstain();
    } finally { setSweeping(false); setSweepStage(null); }
  }, [cam, sweeping, onCommit, t]);

  const status = useScanLoop({
    camera: cam, roi, read, overlay, onCommit, billGeneration,
    // ...and NOT while charging. The loop used to keep committing through the
    // entire /scan + /mint round trip, and the PAID branch then wiped the
    // basket — so an item that chimed and appeared on the bill during those
    // seconds was never charged for.
    enabled: cam.running && !payment && !charging,
  });

  const total = useMemo(() => totalPaise([...basket.values()]), [basket]);

  /**
   * Can the counter actually charge what is on the bill, right now?
   *
   * CHARGE photographs the counter at the moment it is pressed, so a bill built
   * by holding packets up one at a time cannot be charged once they are put
   * down. That is correct — the browser must never charge its own running total
   * — but the button gave no warning, so pressing it produced "nothing on this
   * counter could be priced" over a bill showing ₹99.00 and read as broken.
   *
   * The names in view are compared against the names on the bill, so the button
   * can say what it needs before it is pressed rather than after.
   */
  const onBill = useMemo(() => new Set([...basket.values()].map((l) => l.name)), [basket]);
  const seenNow = useMemo(() => new Set(status.names), [status.names]);
  const missing = useMemo(
    () => [...onBill].filter((n) => !seenNow.has(n)),
    [onBill, seenNow],
  );
  // A held witness must still match the bill it was taken for.
  const witnessUsable =
    heldWitness !== null && heldWitness.paise === total && Date.now() - heldWitness.at < 600_000;
  /**
   * A BILL WITH A TOTAL CAN BE CHARGED. That is the whole rule now.
   *
   * It used to also require the camera to be running AND to have seen every
   * line, because the only evidence this counter could mint against was a
   * photograph. The effect was a till that could build a correct bill by voice
   * — "do Maggi aur ek Parle-G", accepted, ₹338 on screen — and then refuse to
   * take money for it. Loose goods, a label facing away, anything the lens
   * cannot resolve: all uncharegable.
   *
   * The camera path has not weakened. When it HAS witnessed the bill, that
   * photograph is what gets minted, exactly as before. What changed is that a
   * bill it did not witness is now minted against a record that says so —
   * `kind: counter_entered`, `read_by: shopkeeper` — through the same single
   * mint path, where paisa re-prices every line from its own book and refuses
   * on a paisa of disagreement. See `POST /counter/entered`.
   */
  const canCharge = basket.size > 0 && total > 0;
  /** Whether this press will be backed by a photograph or by the shopkeeper. */
  const witnessedByCamera = cam.running && (missing.length === 0 || witnessUsable);

  // Take the photograph the moment everything is visible together.
  useEffect(() => {
    if (payment || charging || basket.size === 0) return;
    if (missing.length > 0 || witnessUsable || capturing.current || !roi || !cam.running) return;
    capturing.current = true;
    void (async () => {
      try {
        const blob = await cam.capture(roi, 0.92);
        if (!blob) return;
        const w = await api.scan(blob);
        // Only keep a witness that is chargeable AND agrees with the bill.
        if (w.ok && w.chargeable && w.witnessed_paise === total) {
          setHeldWitness({ scan_id: w.scan_id, paise: w.witnessed_paise, at: Date.now() });
          setWitnessLines((w.items || []).map((it) => ({
            code: String(it.code ?? ''),
            text: it.sku_id ? `${it.name} — ${rupees(it.price_paise ?? 0)}` : String(it.reason ?? t('till.witness.notTaught')),
            ok: !!it.sku_id,
          })));
        }
      } finally {
        capturing.current = false;
      }
    })();
  }, [missing.length, witnessUsable, basket.size, total, payment, charging, roi, cam, t]);

  // A bill that changes invalidates the photograph taken of the old one.
  useEffect(() => {
    setHeldWitness((h) => (h && h.paise === total ? h : null));
  }, [total]);
  const wholeFrame = isWholeFrame(roi, cam.frame);

  /* ---- money ------------------------------------------------------------- */

  const charge = useCallback(async () => {
    // No `cam.running` guard: a bill with a total can be charged, and when the
    // camera has not witnessed it the branch below writes a record saying so.
    if (basket.size === 0 || total <= 0) return;
    setCharging(true);
    setChargeRefusal(null);
    setWitnessLines([]);
    setQrRefusal(null);
    try {
      // A higher quality than the scan loop uses: this frame becomes the
      // server's witness, and it is written down once.
      // Use the photograph already taken of this exact bill, if there is one.
      if (witnessUsable && heldWitness) {
        setChargeStage('mint');
        const session_id = sessionRef.current ?? (sessionRef.current = api.newSessionId());
        const m = await api.mint({
          session_id, amount_paise: heldWitness.paise, scan_id: heldWitness.scan_id,
        });
        if (!m.ok) { setChargeRefusal({ reason: m.reason, detail: m.detail }); return; }
        setPayment({ session_id, short_url: m.short_url, amount_paise: m.amount_paise,
                     scan_id: heldWitness.scan_id });
        setPayState(t('till.pay.waiting'));
        return;
      }
      // NO PHOTOGRAPH TO TAKE. The camera is off, or it cannot see every line
      // on this bill. The bill is still correct — a person built it and can
      // read it — so it is minted against a record that says a person built
      // it, through the SAME mint path. paisa re-prices every line from its
      // own book and refuses on a paisa of disagreement, exactly as it does
      // for a photograph.
      if (!witnessedByCamera) {
        setChargeStage('witness');
        const lines = [...basket.values()].map((l) => ({ sku_id: l.sku_id, qty: l.qty }));
        const e = await api.enteredBill(lines);
        if (!e.ok) { setChargeRefusal({ reason: e.reason, detail: e.detail }); return; }
        if (e.witnessed_paise !== total) {
          setChargeRefusal({
            reason: t('till.refuse.disagree', { seen: rupees(e.witnessed_paise), bill: rupees(total) }),
            detail: t('till.refuse.disagree.detail'),
          });
          return;
        }
        setChargeStage('mint');
        const sid = sessionRef.current ?? (sessionRef.current = api.newSessionId());
        const mm = await api.mint({ session_id: sid, amount_paise: e.witnessed_paise, scan_id: e.scan_id });
        if (!mm.ok) { setChargeRefusal({ reason: mm.reason, detail: mm.detail }); return; }
        setPayment({ session_id: sid, short_url: mm.short_url,
                     amount_paise: mm.amount_paise, scan_id: e.scan_id });
        setPayState(t('till.pay.waiting'));
        return;
      }

      setChargeStage('photo');
      // Past the guard above this is the camera path, so `roi` is set — but the
      // type does not know that, and an assertion here would be a lie the next
      // refactor could make true. Checked instead.
      if (!roi) { setChargeRefusal({ reason: t('till.charge.startCamera') }); return; }
      const blob = await cam.capture(roi, 0.92);
      if (!blob) throw new Error('no frame');
      setChargeStage('witness');
      const w = await api.scan(blob);
      if (!w.ok) {
        setChargeRefusal({ reason: w.reason, detail: w.detail });
        return;
      }
      setWitnessLines(
        (w.items || []).map((it) => ({
          code: String(it.code ?? ''),
          text: it.sku_id ? `${it.name} — ${rupees(it.price_paise ?? 0)}` : String(it.reason ?? t('till.witness.notTaught')),
          ok: !!it.sku_id,
        })),
      );
      if (!w.chargeable) {
        // The server writes a careful, specific sentence for this. Showing my
        // own generic guess instead told Agnik "every line has to resolve to a
        // taught product" when the real answer was "nothing on this counter
        // could be priced" — the packet had been put down before pressing.
        setChargeRefusal({
          reason: w.why_not_chargeable ?? t('till.refuse.cannotCharge'),
          detail: w.counts?.named === 0 ? t('till.refuse.putBack') : undefined,
        });
        return;
      }
      // THE NUMBER ON THE BUTTON MUST BE THE NUMBER THAT GETS MINTED.
      //
      // The button says CHARGE <basket total>; the mint sends the FRESH
      // witness's total. Those are two different quantities measured a moment
      // apart, and the page was echoing the server's number straight back — so
      // paisa's own `scan_total_disagreement` guard could never fire from here.
      // If a packet drifted out of view between the last scan and the press,
      // the operator agreed to one amount and the customer was shown another.
      if (w.witnessed_paise !== total) {
        setChargeRefusal({
          reason: t('till.refuse.disagree', {
            seen: rupees(w.witnessed_paise), bill: rupees(total),
          }),
          detail: t('till.refuse.disagree.detail'),
        });
        return;
      }
      setChargeStage('mint');
      const session_id = sessionRef.current ?? (sessionRef.current = api.newSessionId());
      const m = await api.mint({ session_id, amount_paise: w.witnessed_paise, scan_id: w.scan_id });
      if (!m.ok) {
        setChargeRefusal({ reason: m.reason, detail: m.detail });
        return;
      }
      setPayment({ session_id, short_url: m.short_url, amount_paise: m.amount_paise, scan_id: w.scan_id });
      setPayState('waiting for the gateway');
    } catch (e) {
      setChargeRefusal({ reason: t('till.refuse.notPhotographed'), detail: String(e) });
    } finally {
      setCharging(false);
      setChargeStage(null);
    }
  }, [roi, cam, total, witnessUsable, heldWitness, witnessedByCamera, basket, t]);

  // Only a signature-verified webhook can turn this screen green. The browser
  // polls to LEARN that it happened; it can never assert that it did.
  useEffect(() => {
    if (!payment) return;
    const startedAt = Date.now();
    setInbound(null);
    let n = 0;
    const id = setInterval(async () => {
      n += 1;
      const d = await api.session(payment.session_id);
      if (d.ok && d.paid === true && d.state === 'PAID') {
        clearInterval(id);
        voices.paid();
        setPaid({
          total: d.total_rupees ? `₹${d.total_rupees}` : rupees(payment.amount_paise),
          paise: payment.amount_paise,
          // Held so RETURN can carry this exact settled bill to WAAPSI. It is
          // the session a signature-verified webhook just settled — the only
          // kind a refund can hang off.
          session: payment.session_id,
        });
        setPayment(null);
        setBasket(new Map());
        setProposed([]);                   // they were for the customer who just paid
        setBillGeneration((g) => g + 1);   // the bill forgot; the trackers must too
        sessionRef.current = null;         // a paid session id is spent
      } else if (!d.ok && d.reason === 'unknown_session') {
        // TERMINAL. paisa keeps sessions in memory, so after a restart this can
        // never go green — while a real payable QR is on screen. Saying
        // "checking" for that is a word meaning "in progress" over a dead end.
        clearInterval(id);
        setPayState(tRef.current('till.pay.noRecord'));
      } else {
        setPayState(`${d.ok ? d.state : d.reason} — ${n * 3}s`);
        // WHY are we still waiting? There are two very different answers and
        // this screen used to give the identical spinner for both: the customer
        // has not paid, or nothing can reach this counter. The second one is
        // detectable — no callback of ANY kind has arrived since the link was
        // minted — and it is the one that will never resolve on its own.
        if (d.ok) {
          setInbound(diagnoseInbound(d, startedAt, Date.now(), DIAGNOSE_AFTER_MS));
        }
      }
      if (n > 200) {
        clearInterval(id);
        setPayState(tRef.current('till.pay.stopped'));
      }
    }, 3000);
    return () => clearInterval(id);
  }, [payment]);

  const cancelPayment = useCallback(() => {
    setPayment(null);
    setPayState('');
  }, []);

  /* ---- ON THE BOOK --------------------------------------------------------- */

  /** Open the sheet, with whatever the proposal or the last booking knew. */
  const openBook = useCallback((from?: BookProposal | null) => {
    setBookRefusal(null);
    if (from) {
      setBookName(from.name);
      setBookPhone(from.phone ?? '');
    }
    setBookOpen(true);
  }, []);

  /**
   * The bill closes onto a household. Same witness rule as CHARGE — a
   * photograph the counter took of THIS bill, priced by the server — and then
   * `/khata/book`, which asks the money service to close the kernel row as
   * BOOKED after re-deriving the amount from that witness. Nothing here sends
   * a price; the total goes along only so the server can refuse a bill that
   * moved between the photograph and the press.
   */
  const bookBill = useCallback(async () => {
    if (booking) return;
    setBooking(true);
    setBookRefusal(null);
    try {
      let scan_id: string;
      let paise: number;
      if (witnessUsable && heldWitness) {
        scan_id = heldWitness.scan_id;
        paise = heldWitness.paise;
      } else {
        if (!roi || !cam.running) {
          setBookRefusal({ reason: t('till.charge.startCamera'), detail: t('till.book.needsWitness') });
          return;
        }
        const blob = await cam.capture(roi, 0.92);
        if (!blob) throw new Error('no frame');
        const w = await api.scan(blob);
        if (!w.ok) { setBookRefusal({ reason: w.reason, detail: w.detail }); return; }
        if (!w.chargeable) {
          setBookRefusal({
            reason: w.why_not_chargeable ?? t('till.refuse.cannotCharge'),
            detail: w.counts?.named === 0 ? t('till.refuse.putBack') : undefined,
          });
          return;
        }
        if (w.witnessed_paise !== total) {
          setBookRefusal({
            reason: t('till.refuse.disagree', { seen: rupees(w.witnessed_paise), bill: rupees(total) }),
            detail: t('till.refuse.disagree.detail'),
          });
          return;
        }
        scan_id = w.scan_id;
        paise = w.witnessed_paise;
      }
      const session_id = sessionRef.current ?? (sessionRef.current = api.newSessionId());
      const r = await kh.bookBill({
        session_id, phone: bookPhone, name: bookName, amount_paise: paise as Paise, scan_id,
      });
      if (!r.ok) { setBookRefusal({ reason: r.reason, detail: r.detail }); return; }
      // BOOKED, with no colour. The bill is done; the debt lives on the khata.
      setBooked(r);
      setBookOpen(false);
      setBookProposal(null);
      setBasket(new Map());
      setProposed([]);
      setBillGeneration((g) => g + 1);
      sessionRef.current = null;
      setHeldWitness(null);
      setWitnessLines([]);
    } catch (e) {
      setBookRefusal({ reason: t('till.refuse.notPhotographed'), detail: String(e) });
    } finally {
      setBooking(false);
    }
  }, [booking, witnessUsable, heldWitness, roi, cam, total, bookPhone, bookName, t]);

  const clearBill = useCallback(() => {
    setBasket(new Map());
    setProposed([]);
    setBillGeneration((g) => g + 1);   // the bill forgot; the trackers must too
    sessionRef.current = null;         // a new bill is a new session
    setHeldWitness(null);
    setPaid(null);
    setBooked(null);
    setBookProposal(null);
    setBookRefusal(null);
    setChargeRefusal(null);
    setWitnessLines([]);
    setQrRefusal(null);
    setSweep(null);
    setSweepRefusal(null);
    overlay.clear();
  }, [overlay]);

  /* ---- Salaahkaar's proposals --------------------------------------------- */

  /**
   * ACCEPT: a person moving proposed lines into the bill.
   *
   * NOT through `addToBasket`. That function counts ITEMS — one camera read is
   * one packet — and ignores `qty`, which is right for the scan loop and was
   * silently wrong for voice: "do Maggi" accepted through it put ONE Maggi on
   * the bill. The merge below adds the quantity the server proposed to whatever
   * the line already holds, at the unit price the server proposed.
   *
   * A weighed line is one scoop at one price and is keyed like a packet line,
   * so it is refused onto a bill that already carries the same sku as packets:
   * a map keyed by sku_id cannot hold both, and merging them would count a
   * weight as a packet. It stays amber with the reason on the row.
   */
  const acceptProposed = useCallback((ids: ReadonlySet<string>) => {
    const next = new Map(basket);
    const taken = new Set<string>();
    for (const p of proposed) {
      if (!ids.has(p.id)) continue;
      const cur = next.get(p.sku_id);
      if (p.by === 'weighed') {
        if (cur) continue;
        next.set(p.sku_id, {
          sku_id: p.sku_id, name: p.weight ? `${p.name} · ${p.weight}` : p.name,
          price_paise: p.line_paise as Paise, qty: 1, by: 'salaahkaar',
        });
      } else if (cur) {
        next.set(p.sku_id, { ...cur, qty: cur.qty + p.qty });
      } else {
        next.set(p.sku_id, {
          sku_id: p.sku_id, name: p.name, price_paise: p.unit_paise as Paise, qty: p.qty, by: 'salaahkaar',
        });
      }
      taken.add(p.id);
    }
    if (taken.size === 0) return;
    setBasket(next);
    setProposed((cur) => cur.filter((p) => !taken.has(p.id)));
    voices.named(taken.size);
  }, [basket, proposed]);

  const dropProposed = useCallback((ids: ReadonlySet<string>) => {
    setProposed((cur) => cur.filter((p) => !ids.has(p.id)));
  }, []);

  /** The sum of what is proposed — shown beside the total, never added to it. */
  const proposedPaise = useMemo(
    () => totalPaise(proposed.map((p) => ({ price_paise: p.line_paise as Paise, qty: 1 }))),
    [proposed],
  );

  /* ---- the customer's side of the counter --------------------------------
   *
   * THE SECOND SCREEN WAS NEVER FED.
   *
   * `routes/Display.tsx` and `lib/displaybus.ts` were both built and neither
   * had a publisher: nothing in this app ever called `publish`, so the
   * customer display sat on "Nothing has reached this screen yet" through
   * every bill, every payment and every settlement. It is the one screen whose
   * reader cannot ask anyone what happened, and it was structurally blank.
   *
   * This mirrors what the shopkeeper is already looking at. It is not an
   * authority on anything: the lines are the server-priced ones already on the
   * bill, the total is the same integer, and the pay block carries a session id
   * the display turns into a QR by asking the server — no payment target is
   * composed here or sent over the bus.
   *
   * `shop` is published as null on purpose. The Till does not fetch the shop's
   * profile and inventing a name here would be a second source of truth; the
   * display reads the name from `/shop/profile` itself and says so when it
   * cannot.
   */
  useEffect(() => {
    try {
      publish(billState({
        shop: null,
        lines: [...basket.values()].map((l) => {
          const shelf = marked.get(l.sku_id);
          return {
            sku_id: l.sku_id, name: l.name, qty: l.qty, price_paise: l.price_paise,
            ...(shelf !== undefined && shelf !== l.price_paise ? { marked_paise: shelf } : {}),
          };
        }),
        total_paise: total,
        pay: payment
          ? { session_id: payment.session_id, short_url: payment.short_url, amount_paise: payment.amount_paise }
          : null,
        paid: paid ? { amount_paise: paid.paise } : null,
      }));
    } catch {
      // `billState` throws on a non-integer amount rather than showing a
      // customer a repaired number. Caught HERE and nowhere else: the till is
      // the money path, and it must not be taken off the screen because the
      // second monitor could not be told about a bill. The display's own
      // staleness rule then retires whatever it is still showing.
    }
  }, [basket, marked, total, payment, paid]);

  /* ---- render ------------------------------------------------------------ */

  const dragRect = drag
    ? {
        x: Math.min(drag.x0, drag.x1), y: Math.min(drag.y0, drag.y1),
        w: Math.abs(drag.x1 - drag.x0), h: Math.abs(drag.y1 - drag.y0),
      }
    : null;
  const shown = dragRect ?? roi;

  if (payment) {
    return (
      <div className="till till-pay">
        <div className="page-head">
          <h1>{t('till.pay.title')}</h1>
          <p>{t('till.pay.sub')}</p>
        </div>
        {/* The camera keeps running behind the pay screen so CANCEL returns to a
            live counter. It is mounted, not merely hidden, because the stream
            has to have a node to be attached to. */}
        <video ref={cam.videoRef} playsInline muted style={{ display: 'none' }} />
        <div className="grid pay-grid">
          <Card title={t('till.bill.title')}>
            <div className="bill till-receipt">
              {[...basket.values()].map((l) => {
                const shelf = marked.get(l.sku_id);
                return (
                  <div className="bill-line" key={l.sku_id}>
                    <span className="nm">{l.name}</span>
                    <span className="qty">×{l.qty}</span>
                    <span className="amt tnum">
                      {shelf !== undefined && shelf !== l.price_paise && (
                        <s className="was">{rupees(shelf * l.qty)}</s>
                      )}
                      {rupees(l.price_paise * l.qty)}
                    </span>
                  </div>
                );
              })}
              <div className="bill-total">
                <span className="lbl">{t('till.bill.toPay')}</span>
                <span className="amt">{rupees(payment.amount_paise)}</span>
              </div>
            </div>
            {inbound && (
              <Verdict tone="amber" title={t('till.inbound.title')}>
                <p>
                  {inbound.seen === 0
                    ? t('till.inbound.never')
                    : inbound.lastAt
                      ? tx('till.inbound.last', { ago: ago(inbound.lastAt) })
                      : t('till.inbound.none')}
                  {' '}{t('till.inbound.since', { s: inbound.waitedS })}
                </p>
                <p style={{ marginTop: 10 }}>{tx('till.inbound.mayHavePaid')}</p>
                <p className="hint" style={{ marginTop: 10 }}>{t('till.inbound.stillPayable')}</p>
              </Verdict>
            )}

            {witnessLines.length > 0 && (
              <div style={{ marginTop: 18 }}>
                <div className="eyebrow">{t('till.witness.headingPay')}</div>
                {witnessLines.map((l, i) => (
                  <KV k={<span className="mono">{l.code}</span>} key={`${l.code}-${i}`}>{l.text}</KV>
                ))}
              </div>
            )}
          </Card>

          <Card title={t('till.pay.qrTitle')} aside={<Pill tone="amb" dot>{payState}</Pill>}>
            {/* THE AMOUNT, ABOVE EVERYTHING AND UNDER EVERY OUTCOME. It used to
                live inside the QR block, so a refused QR took the amount with
                it — and the one figure a customer and an operator both need to
                check against the button they just pressed was gone from the
                screen exactly when something had gone wrong. */}
            <div className="pay-amount tnum">{rupees(payment.amount_paise)}</div>
            {qrRefusal ? (
              /* NOT A DEAD END, BUT NOT THE SAME HINT FOR EVERY REFUSAL. The
                 refusal named the reason; the hint names what is still true
                 and what to do next. The URL stays inert text, because handing
                 over a clickable copy of a string this program just refused to
                 encode would undo the refusal.

                 THE HINT USED TO SAY "STILL PAYABLE" UNDER EVERY REFUSAL, and
                 that was a lie once the simulator moved its links off the
                 gateway's domain: a link on `pay.gawaah-sim.invalid` is refused
                 BECAUSE it is not a gateway address, and a screen that then
                 says it is still payable is contradicting its own refusal one
                 line down. So the host refusal gets its own sentence. */
              <Refusal
                reason={qrRefusal.reason}
                detail={qrRefusal.detail}
                hint={/gateway hosts/i.test(qrRefusal.detail ?? '')
                  ? t('till.pay.qrRefused.notGateway')
                  : t('till.pay.qrRefused.stillPayable')}
              />
            ) : (
              /* The climax reads top-down the way a customer reads it: the
                 amount first and huge, then the code to point a phone at. The
                 frame keeps a shimmering placeholder underneath, so the panel
                 is never a blank hole while the render is fetched. */
              <div className="till-qr">
                <div className="till-qr-sub">{t('till.pay.scanWithUpi')}</div>
                <div className="qr-wrap big">
                  <img
                    src={api.paymentQrUrl(payment.session_id)}
                    alt={t('till.pay.qrAlt', { amount: rupees(payment.amount_paise) })}
                    onError={() => {
                      // Re-fetch the same URL to read WHY. A GET with no side
                      // effects; the server already refused once.
                      void fetch(api.paymentQrUrl(payment.session_id), { cache: 'no-store' })
                        .then((r) => r.json())
                        .then((j) => setQrRefusal({
                          reason: String(j.reason ?? t('till.pay.qrRefused')),
                          detail: String(j.detail ?? ''),
                        }))
                        .catch(() => setQrRefusal({
                          reason: t('till.pay.qrRefused'),
                          detail: t('till.pay.qrRefused.detail'),
                        }));
                    }}
                  />
                </div>
                <p className="hint" style={{ textAlign: 'center' }}>{t('till.pay.renderNote')}</p>
              </div>
            )}
            <div className="till-link-facts">
            <KV k={t('till.pay.link')}>
              {/* Never a live <a> when the server refused to encode the string:
                  handing the shopkeeper a clickable copy of a link this program
                  just judged unsafe would undo the refusal. */}
              {qrRefusal ? (
                <span className="mono">{payment.short_url}</span>
              ) : (
                <a className="mono" href={payment.short_url} target="_blank" rel="noreferrer">{payment.short_url}</a>
              )}
            </KV>
            <KV k={t('till.pay.session')}><span className="mono">{payment.session_id}</span></KV>
            </div>
            <div className="btn-row" style={{ marginTop: 16 }}>
              <button className="btn" onClick={cancelPayment}>{t('till.pay.cancel')}</button>
            </div>
          </Card>
        </div>
      </div>
    );
  }

  return (
    <div className="till">
      <div className="page-head">
        <h1>{t('till.head.title')}</h1>
        <p>{t('till.head.sub')}</p>
      </div>
      <div className="grid till-grid">
      <div className="stack">
        <div className="card till-stage-card">
          {/* `looking` drives the sweep-and-breathe chrome: on exactly while the
              scan loop is allowed to run, so the animation IS the loop's state
              and can never claim the counter is looking when it is not. */}
          <div
            className={`stage till-stage${cam.running && !charging ? ' looking' : ''}`}
            ref={stageRef}
          >
            <video ref={cam.videoRef} playsInline muted style={{ display: cam.running ? 'block' : 'none' }} />
            {/* Before the canvas in DOM, so recognition boxes paint over the sweep. */}
            {cam.running && !charging && <span className="till-sweep" aria-hidden="true" />}
            <canvas className="ov" ref={ovRef} />

            {/* The rectangle the operator is drawing, in DOM rather than on the
                overlay canvas, so it survives every overlay repaint. Position is
                arithmetic and stays inline; colour lives in till.css tokens. */}
            {cam.running && shown && (
              <div
                aria-hidden
                className={`till-roi${drag ? ' drag' : wholeFrame ? ' whole' : ''}`}
                style={{
                  left: `${(shown.x / cam.frame.w) * 100}%`,
                  top: `${(shown.y / cam.frame.h) * 100}%`,
                  width: `${(shown.w / cam.frame.w) * 100}%`,
                  height: `${(shown.h / cam.frame.h) * 100}%`,
                }}
              />
            )}

            {cam.running && (
              <div
                className="drag-layer"
                onPointerDown={(e) => {
                  (e.target as HTMLElement).setPointerCapture(e.pointerId);
                  const p = toVideo(e);
                  setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y });
                }}
                onPointerMove={(e) => {
                  if (!drag) return;
                  const p = toVideo(e);
                  setDrag((d) => (d ? { ...d, x1: p.x, y1: p.y } : d));
                }}
                onPointerUp={onDragEnd}
                onPointerCancel={() => setDrag(null)}
              />
            )}

            {/* Viewfinder corners — after the drag layer in DOM so they sit on
                top visually, pointer-events: none so they never eat a drag. */}
            {cam.running && <span className="till-viewfinder" aria-hidden="true" />}

            {!cam.running && (
              <div className="camgate">
                {/* A lens, not a warning sign: the camera being off is the rest
                    state of a counter, and the gate is an invitation.

                    The dashed outer ring turns and the iris breathes — slowly,
                    because this is a resting state and a fast animation on an
                    idle screen reads as something going wrong. Both stop under
                    prefers-reduced-motion. */}
                <svg className="till-lens" width="76" height="76" viewBox="0 0 46 46" aria-hidden="true">
                  <circle className="lens-ring" cx="23" cy="23" r="21" fill="none" stroke="currentColor"
                          strokeWidth="1.2" opacity=".45" strokeDasharray="3 7" strokeLinecap="round" />
                  <circle cx="23" cy="23" r="17" fill="none" stroke="currentColor" strokeWidth="1.6" opacity=".5" />
                  <circle className="lens-iris" cx="23" cy="23" r="9.5" fill="none" stroke="currentColor" strokeWidth="2.2" />
                  <circle cx="27.2" cy="18.8" r="2.2" fill="currentColor" />
                </svg>
                <h3>{t('till.cam.off.title')}</h3>
                {/* ONE LINE, NOT A PARAGRAPH. What used to sit here was three
                    sentences: the promise, what gets uploaded, and how to
                    narrow it. Only the first belongs next to the button — the
                    other two are the disclosure, and they now get their own
                    strip along the foot of the stage rather than being the
                    tail of something a person stops reading. */}
                <p className="gate-lead">{t('till.cam.off.lead')}</p>
                <button
                  className="btn primary"
                  onClick={() => {
                    voices.unlock(); // browsers only allow audio after a gesture
                    void cam.start();
                  }}
                >
                  {t('till.cam.start')}
                </button>
                {cam.error && (
                  <Verdict tone="red" title={t('till.cam.failed')}>{cam.error}</Verdict>
                )}
              </div>
            )}

            {/* WHAT LEAVES THIS MACHINE, given its own strip. This product's
                whole argument is that it says what it does, so the sentence
                describing what it uploads should not be the part of a
                paragraph that gets skipped. Rendered only with the camera off:
                once it is running the instrument bar owns this edge, and two
                strips stacked on the same corner is how a stage stops being
                readable. */}
            {!cam.running && (
              <div className="till-disclose">
                <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M12 2.6 4.6 5.8v5.5c0 4.6 3.1 8.5 7.4 9.7 4.3-1.2 7.4-5.1 7.4-9.7V5.8L12 2.6Z"
                        fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
                </svg>
                {/* THE MODE'S OWN SENTENCE, not a second copy of it. This
                    strip first shipped with its own wording about uploading
                    the whole image — which is true in code mode and FALSE in
                    look mode, where only the rectangle is sent. `till.hint.*`
                    already says the right thing for each mode and is the only
                    place that should; this renders whichever one applies. */}
                <span>{read === 'code' ? tx('till.hint.code') : tx('till.hint.look')}</span>
              </div>
            )}

            {cam.running && (
              <div className="stage-bar">
                <Pill tone="ok" dot>{t('till.stage.live')}</Pill>
                <span className="mono sb-item">{status.uploadedPx}</span>
                {status.elapsedMs != null && <span className="mono sb-item">{status.elapsedMs} ms</span>}
                <span className="mono sb-item">{t('till.stage.looks', { n: status.attempts })}</span>
                <div style={{ flex: 1 }} />
                <span className="mono sb-item sb-frame">
                  {wholeFrame ? t('till.stage.whole') : t('till.stage.cropped')}
                </span>
              </div>
            )}
          </div>

          <div className="card-body tight">
            <div className="readout till-readout">
              {/* The pills are SIBLINGS, not nested under `symbols`. Nesting
                  them made the NOT TAUGHT pill — the visible half of "abstain
                  rather than guess" — unreachable in look mode, where symbols
                  is legitimately 0 while an item is named and billed. */}
              {status.refusal ? (
                <Pill tone="amb">{status.refusal}</Pill>
              ) : status.symbols || status.untaught || status.names.length ? (
                <>
                  {status.symbols > 0 && (
                    <Pill tone="code">
                      {tn('till.readout.symbols', status.symbols)}
                      {status.distinct !== status.symbols
                        && ` · ${t('till.readout.distinct', { n: status.distinct })}`}
                    </Pill>
                  )}
                  {status.untaught > 0 && (
                    <Pill tone="amb">{t('till.readout.untaught', { n: status.untaught })}</Pill>
                  )}
                  {status.cooling.map((c) => (
                    <Pill tone="off" key={c.code}>
                      {t('till.readout.cooling', { s: Math.ceil(c.msLeft / 1000) })}
                    </Pill>
                  ))}
                </>
              ) : (
                <span className="muted">
                  {cam.running ? t('till.readout.nothing') : t('till.readout.cameraOff')}
                </span>
              )}
              <div style={{ flex: 1 }} />
              <span className="names">{status.names.slice(0, 3).join(', ') || '—'}</span>
            </div>

            {/* THE SWEEP, WHILE IT IS RUNNING.
                At the SHAPE of the verdict that replaces it — a panel the same
                width in the same slot — so the instrument row below does not
                jump when the counter answers. Only the two stages this browser
                actually performs are marked; the two the SERVER performs carry
                no tick, because a tick this page cannot verify is the same lie
                as a figure it cannot derive. */}
            {sweeping && (
              <Thinking
                title={t('till.sweep.reading')}
                steps={[
                  { label: 'Photograph the whole counter', state: sweepStage === 'photo' ? 'now' : 'done' },
                  { label: 'Send those pixels up', state: sweepStage === 'send' ? 'now' : undefined },
                  { label: 'Find every region in the frame' },
                  { label: 'Name the ones it can, at the same gate as the loop' },
                ]}
                foot="What it cannot name goes on no bill. It is reported instead."
              />
            )}

            {/* The server would not read the counter, and it said why. */}
            {sweepRefusal && !sweeping && (
              <Refusal
                reason={sweepRefusal.reason}
                detail={sweepRefusal.detail}
                hint="Nothing was added to the bill. Hold the packets up one at a time, or press READ THE WHOLE COUNTER again."
                action={
                  <button
                    className="btn sm"
                    onClick={() => { setSweepRefusal(null); void readWholeCounter(); }}
                    disabled={!cam.running}
                    title={cam.running ? undefined : 'The camera is not running.'}
                  >
                    READ IT AGAIN
                  </button>
                }
              />
            )}

            {sweep && !sweeping && (
              <Verdict
                tone={sweep.counts.unnamed > 0 ? 'amber' : 'green'}
                title={sweep.counts.unnamed > 0
                  ? t('till.sweep.mixed', {
                      named: sweep.counts.named, unnamed: sweep.counts.unnamed,
                    })
                  : t('till.sweep.allPriced', { named: sweep.counts.named })}
              >
                {sweep.counts.unnamed > 0
                  ? tnx('till.sweep.unnamed', sweep.counts.unnamed)
                  : t('till.sweep.allBody', {
                      byCode: sweep.counts.by_code, byLook: sweep.counts.by_appearance,
                    })}
                <div style={{ marginTop: 10 }}>
                  <span className="muted" style={{ fontSize: 12 }}>
                    {t('till.sweep.gapNote', { ms: sweep.elapsed_ms })}
                  </span>
                </div>
              </Verdict>
            )}

            {/* THE INSTRUMENT ROW. Two clusters, not one line of five buttons
                of equal weight: on the left, how the counter is reading and
                the area it reads; on the right, the sound, and the one action
                that is not a setting — reading the whole counter at once —
                as the primary. Every label is unchanged; the suite addresses
                these by name. */}
            <div className="till-tools">
              <div className="till-tools-l">
                <Segmented<ReadMode>
                  value={read}
                  onChange={setRead}
                  options={[
                    { value: 'code', label: t('till.mode.code'), title: t('till.mode.code.title') },
                    { value: 'look', label: t('till.mode.look'), title: t('till.mode.look.title') },
                  ]}
                />
                {/* A DISABLED CONTROL SAYS WHY IT IS DISABLED. Both of these
                    were dead and silent with the camera off, which reads as a
                    broken button rather than as one waiting for something. */}
                <button
                  className="btn sm quiet"
                  onClick={resetArea}
                  disabled={!cam.running}
                  title={t(cam.running ? 'till.redraw.title.on' : 'till.redraw.title.off')}
                >
                  {t('till.redraw')}
                </button>
                {cam.running && (
                  <button className="btn sm quiet" onClick={cam.stop}>{t('till.stop')}</button>
                )}
              </div>
              <div className="till-tools-r">
                {/* A toggle that shows STATE reads as a button that performs an
                    ACTION: "SOUND OFF" looked like "press me to turn sound off",
                    so pressing it to get sound was what silenced the counter.
                    Say what pressing it does, and show the state as a symbol. */}
                <button
                  className={muted ? 'btn sm danger' : 'btn sm quiet'}
                  onClick={() => { if (muted) voices.unlock(); setMuted((m) => !m); }}
                  title={muted ? t('till.sound.muted.title') : t('till.sound.on.title')}
                >
                  {muted ? t('till.sound.muted') : t('till.sound.on')}
                </button>
                <button
                  className="btn sm quiet"
                  title={t('till.sound.test.title')}
                  onClick={() => { voices.muted = false; setMuted(false); voices.demo(); }}
                >
                  {t('till.sound.test')}
                </button>
                {/* The second gesture. The loop above reads one subject at a
                    time; this reads the whole counter in one press, which is
                    what a customer putting four things down actually needs. */}
                <button
                  className="btn primary"
                  onClick={() => void readWholeCounter()}
                  disabled={!cam.running || sweeping}
                  title={!cam.running
                    ? t('till.sweep.title.off')
                    : sweeping
                      ? t('till.sweep.title.busy')
                      : t('till.sweep.title')}
                >
                  {sweeping ? t('till.sweep.reading') : t('till.sweep.button')}
                </button>
              </div>
            </div>

            {/* Only while the camera is live. With the camera off the same
                sentence is already on the stage's disclosure strip, and it was
                printed twice, forty pixels apart. */}
            {cam.running && (
              <p className="hint">
                {read === 'code' ? tx('till.hint.code') : t('till.hint.look')}
              </p>
            )}
            {dragNote && <p className="hint" style={{ color: 'var(--blue-700)' }}>{dragNote}</p>}
          </div>
        </div>
      </div>

      {/* ------------------------------------------------------------- bill -- */}
      <div className="stack">
        {/* HANDS FULL, WET, OR HOLDING CHANGE. The camera is the fast path
            when the packet is in front of it; Salaahkaar is the fast path when
            it is not — a loose item, something behind the counter, or a
            quantity the camera can see only one of. She PROPOSES lines and a
            person accepts them below: voice never moves money on its own.
            The catalogue refusal that used to be threaded in here is the
            server's to make now — she asks it, and it refuses by name. */}
        <VoiceBar
          muted={muted}
          onPropose={(p, heard) => {
            setProposed((cur) => [
              ...cur,
              ...p.lines.map((l, i) => ({
                ...l, id: `${p.proposal_id}-${i}`, proposal_id: p.proposal_id,
                heard: l.heard ?? heard,
              })),
            ]);
          }}
          onProposeBook={(p) => { setBooked(null); setBookProposal(p); }}
        />
        <Card
          title={t('till.bill.title')}
          icon={<IcoReceipt />}
          aside={(basket.size > 0 || proposed.length > 0) && (
            <button className="btn ghost sm" onClick={clearBill}>{t('till.bill.clear')}</button>
          )}
        >
          <div className="bill till-receipt">
            {basket.size === 0 && proposed.length === 0 ? (
              <Empty>
                {/* A barcode glyph that admits to being a placeholder — the
                    panel is never blank, and the art says what fills it. */}
                <svg className="till-empty-art" width="72" height="34" viewBox="0 0 72 34" aria-hidden="true">
                  <g fill="currentColor">
                    <rect x="0" y="0" width="3" height="34" /><rect x="6" y="0" width="1.5" height="34" />
                    <rect x="11" y="0" width="4" height="34" /><rect x="18" y="0" width="1.5" height="34" />
                    <rect x="23" y="0" width="2" height="34" /><rect x="28" y="0" width="5" height="34" />
                    <rect x="36" y="0" width="1.5" height="34" /><rect x="41" y="0" width="3" height="34" />
                    <rect x="47" y="0" width="1.5" height="34" /><rect x="52" y="0" width="4" height="34" />
                    <rect x="59" y="0" width="2" height="34" /><rect x="64" y="0" width="1.5" height="34" />
                    <rect x="69" y="0" width="3" height="34" />
                  </g>
                </svg>
                {t('till.bill.empty.1')}
                <br />
                {t('till.bill.empty.2')}
              </Empty>
            ) : basket.size === 0 ? null : (
              <div className="bill-lines">
                {[...basket.values()].map((l) => {
                  const shelf = marked.get(l.sku_id);
                  return (
                  <div className="bill-line editable" key={l.sku_id}>
                    <span className="nm">{l.name}</span>
                    {/* THE OPERATOR OVERRULING THE CAMERA. The bill used to be
                        read-only, so a wrong line meant CLEAR and start the
                        whole order again — and a customer changing their mind
                        at the counter is most of a shift, not a rare case. */}
                    <span className="qty-edit">
                      <button
                        className="qbtn"
                        aria-label={t('till.bill.oneFewer', { name: l.name })}
                        onClick={() => setBasket((b) => {
                          const next = decLine(b, l.sku_id);
                          // The last one removed the LINE, so the camera has
                          // to be told; a decrement that leaves the line
                          // standing is not a removal and must not suppress.
                          if (!next.has(l.sku_id)) status.suppress(l.sku_id);
                          return next;
                        })}
                      >−</button>
                      <span className="qty">{l.qty}</span>
                      <button
                        className="qbtn"
                        aria-label={t('till.bill.oneMore', { name: l.name })}
                        onClick={() => setBasket((b) => incLine(b, l.sku_id))}
                      >+</button>
                    </span>
                    <span className="amt tnum">
                      {/* The shelf-edge price struck through beside the charged
                          one, so a discount is a visible fact, not a mystery. */}
                      {shelf !== undefined && shelf !== l.price_paise && (
                        <s className="was">{rupees(shelf * l.qty)}</s>
                      )}
                      {rupees(l.price_paise * l.qty)}
                    </span>
                    <button
                      className="qbtn drop"
                      aria-label={t('till.bill.drop', { name: l.name })}
                      title={t('till.bill.drop.title')}
                      onClick={() => {
                        status.suppress(l.sku_id);
                        setBasket((b) => removeLine(b, l.sku_id));
                      }}
                    >×</button>
                  </div>
                  );
                })}
              </div>
            )}

            {/* ---- what Salaahkaar proposed, in amber, until a person acts --
                Amber is the counter abstaining: these are on the bill to be
                LOOKED AT, not in the total. Each row has its own ACCEPT and
                its own ×, and one button takes them all, because "do Maggi
                aur ek Parle-G" is two rows a shopkeeper checks with one
                glance and agrees to with one thumb. */}
            {heldNote && (
              <Verdict tone="amber" title={t('till.bill.held.title')}>
                {heldNote}
                <button type="button" className="btn ghost sm" style={{ marginLeft: 10 }} onClick={() => setHeldNote(null)}>
                  {t('till.bill.held.ok')}
                </button>
              </Verdict>
            )}
            {proposed.length > 0 && (
              <div className="till-proposed" role="region" aria-label={t('till.bill.proposed.pill')}>
                <div className="till-proposed-head">
                  <Pill tone="amb" dot>{t('till.bill.proposed.pill')}</Pill>
                  <span className="muted">{tn('till.bill.proposed.count', proposed.length)}</span>
                  <div className="spacer" />
                  <button
                    className="btn sm primary"
                    onClick={() => acceptProposed(new Set(proposed.map((p) => p.id)))}
                  >
                    {t('till.bill.proposed.acceptAll')}
                  </button>
                  <button
                    className="btn sm ghost"
                    onClick={() => dropProposed(new Set(proposed.map((p) => p.id)))}
                  >
                    {t('till.bill.proposed.dropAll')}
                  </button>
                </div>
                <div className="bill-lines">
                  {proposed.map((p) => {
                    const blocked = p.by === 'weighed' && basket.has(p.sku_id);
                    return (
                      <div className="bill-line proposed" key={p.id}>
                        <span className="nm">
                          {p.name}
                          <br />
                          {/* The word on the bill is not always the word the
                              microphone returned: hi-IN hears "ponds" as
                              पॉन्ड्स and the server respells it to find the
                              product. A phrase in another script from the
                              name says so, rather than silently swapping. */}
                          <span className="mono muted">
                            {p.by === 'weighed' && p.weight
                              ? t('till.bill.proposed.weighed', { weight: p.weight })
                              : p.heard
                                ? t(/\p{Script=Latin}/u.test(p.name) && !/\p{Script=Latin}/u.test(p.heard)
                                  ? 'till.bill.proposed.respelt'
                                  : 'till.bill.proposed.heard', { heard: p.heard })
                                : p.sku_id}
                          </span>
                          {blocked && (
                            <>
                              <br />
                              <span className="till-proposed-blocked">{t('till.bill.proposed.onBill')}</span>
                            </>
                          )}
                        </span>
                        <span className="qty">{p.by === 'weighed' ? p.weight ?? '×1' : `×${p.qty}`}</span>
                        <span className="amt tnum">{rupees(p.line_paise)}</span>
                        <button
                          className="btn sm primary"
                          disabled={blocked}
                          onClick={() => acceptProposed(new Set([p.id]))}
                        >
                          {t('till.bill.proposed.accept')}
                        </button>
                        <button
                          className="qbtn drop"
                          aria-label={t('till.bill.proposed.drop', { name: p.name })}
                          onClick={() => dropProposed(new Set([p.id]))}
                        >×</button>
                      </div>
                    );
                  })}
                </div>
                <p className="hint">{t('till.bill.proposed.hint')}</p>
              </div>
            )}

            <div className="bill-total">
              <span className="lbl">{t('till.bill.total')}</span>
              <span className="amt">{rupees(total)}</span>
            </div>
            {proposed.length > 0 && (
              <p className="till-proposed-sum">{t('till.bill.proposed.notCounted', { amount: rupees(proposedPaise) })}</p>
            )}

            {/* ---- what Salaahkaar proposed for the BOOK, in neutral ink ---
                Not amber: a booking is not an abstention. Not green: nothing
                settled. A person presses ACCEPT, and the sheet asks for the
                number if the book does not know this household yet. */}
            {bookProposal && !booked && !payment && (
              <div className="till-book-proposed" role="region" aria-label={t('till.book.action')}>
                <p>
                  {bookProposal.known && bookProposal.phone_masked
                    ? tx('till.book.proposed', { name: bookProposal.name, phone: bookProposal.phone_masked })
                    : tx('till.book.proposed.unknown', { name: bookProposal.name })}
                </p>
                <div className="btn-row">
                  <button className="btn primary sm" onClick={() => openBook(bookProposal)}
                          disabled={basket.size === 0}>
                    {t('till.book.accept')}
                  </button>
                  <button className="btn ghost sm" onClick={() => setBookProposal(null)}>
                    {t('till.book.drop')}
                  </button>
                </div>
              </div>
            )}

            {!payment && !paid && !booked && (
              <>
              <button
                className="btn pay"
                onClick={charge}
                disabled={charging || !canCharge}
                /* WHY IT CANNOT BE PRESSED, in every case it cannot be. The
                   label already carries the first two; the title repeats them
                   on hover so a shopkeeper reaching for a dead button gets an
                   answer where their cursor already is. */
                title={charging
                  ? 'The counter is being photographed and witnessed. One charge at a time.'
                  : basket.size === 0
                    ? 'There is nothing on the bill to charge for.'
                    : !cam.running
                      ? 'This bill was entered at the counter, not photographed. It is recorded that way.'
                      : missing.length
                        ? t('till.charge.notInView', { names: missing.join(', ') })
                        : undefined}
              >
                {charging
                  ? t('till.charge.witnessing')
                  : basket.size === 0
                    ? t('till.charge.nothing')
                    : t('till.charge.pay', { amount: rupees(total) })}
              </button>

              {/* ON THE BOOK, beside CHARGE. Ghost, not green and not the pay
                  button's colour: it closes the bill without any money moving.
                  Same arming rule as CHARGE — the camera has to have seen the
                  bill — because a debt nobody witnessed is a number the
                  browser authored. */}
              <button
                className="btn ghost till-book-btn"
                onClick={() => openBook(bookProposal)}
                disabled={charging || booking || !canCharge}
                title={basket.size === 0
                  ? t('till.charge.nothing')
                  : !cam.running ? t('till.charge.startCamera')
                    : missing.length && !witnessUsable ? t('till.charge.notInView', { names: missing.join(', ') })
                      : t('till.book.sub')}
              >
                {booking ? t('till.book.working') : t('till.book.action')}
              </button>

              {/* WHAT IS HAPPENING TO THE MONEY, WHILE IT HAPPENS.
                  A photograph, a server-side witness and a mint at a gateway
                  is a wait of seconds, and all a shopkeeper had was a greyed
                  button. Marked from what this browser has SEEN happen: the
                  frame it took, and each response it has actually read back.
                  Nothing here is ticked on a guess about the server. */}
              {charging && (
                <Thinking
                  className="till-working"
                  /* Not the button's own words repeated back. The button says
                     what it is doing; this says what it is doing it THROUGH. */
                  title="Taking the witness, then asking for a link"
                  steps={[
                    {
                      label: witnessUsable
                        ? 'Use the photograph already taken of this bill'
                        : 'Photograph what is on the counter',
                      state: chargeStage === 'photo' ? 'now' : 'done',
                    },
                    {
                      label: 'The counter writes its own witness and prices it',
                      state: chargeStage === 'witness' ? 'now' : chargeStage === 'mint' ? 'done' : undefined,
                    },
                    {
                      label: 'The money service re-prices that witness and mints a link',
                      state: chargeStage === 'mint' ? 'now' : undefined,
                    },
                  ]}
                  foot="No money moves here. Only the gateway's own webhook can settle this bill."
                />
              )}

              {missing.length > 0 && !witnessUsable && cam.running && (
                <p className="hint">
                  {tnx('till.charge.missing', missing.length, { names: missing.join(', ') })}
                </p>
              )}

              {witnessUsable && (
                <p className="hint" style={{ color: 'var(--green)' }}>
                  {t('till.charge.ready')}
                </p>
              )}
              </>
            )}

            {witnessLines.length > 0 && (
              <div style={{ marginTop: 16 }}>
                <div className="eyebrow">{t('till.witness.heading')}</div>
                {witnessLines.map((l, i) => (
                  <KV k={<span className="mono">{l.code}</span>} key={`${l.code}-${i}`}>
                    <span style={{ color: l.ok ? 'var(--ink)' : 'var(--amber)' }}>{l.text}</span>
                  </KV>
                ))}
              </div>
            )}

            {chargeRefusal && <Refusal reason={chargeRefusal.reason} detail={chargeRefusal.detail} />}
            {bookRefusal && !bookOpen && <Refusal reason={bookRefusal.reason} detail={bookRefusal.detail} />}

            {booked && (
              /* ON THE BOOK, done. NO COLOUR: this block is ink on paper. It
                 renders only after the money service closed the kernel row as
                 BOOKED — a state with no legal move to SETTLED — so nothing
                 here can be mistaken for a payment. */
              <div className="till-booked" role="status">
                <div className="till-booked-word">{t('till.book.action')}</div>
                <div className="till-booked-line">
                  {t('till.book.done', {
                    amount: rupees(booked.amount_paise), name: booked.name, phone: booked.phone_masked,
                  })}
                  {booked.new_household && <span className="till-booked-new"> · {t('till.book.new')}</span>}
                </div>
                <p>{tx('till.book.done.body', { outstanding: booked.outstanding_paise !== null ? rupees(booked.outstanding_paise) : rupees(booked.amount_paise) })}</p>
                <div className="btn-row">
                  <a className="btn sm" href={`#/khata?book=${encodeURIComponent(booked.book_id)}`}>{t('till.book.open')}</a>
                  <button className="btn ghost sm" onClick={clearBill}>{t('till.bill.clear')}</button>
                </div>
              </div>
            )}

            <Modal
              open={bookOpen}
              onClose={() => { if (!booking) setBookOpen(false); }}
              title={t('till.book.title')}
              sub={t('till.book.sub')}
              note={t('till.book.needsWitness')}
              foot={
                <>
                  <button className="btn ghost" onClick={() => setBookOpen(false)} disabled={booking}>
                    {t('till.book.cancel')}
                  </button>
                  <button className="btn primary" onClick={() => void bookBill()}
                          disabled={booking || !bookName.trim() || !bookPhone.trim() || basket.size === 0}>
                    {booking ? t('till.book.working') : t('till.book.confirm', { amount: rupees(total) })}
                  </button>
                </>
              }
            >
              <Field label={t('till.book.name')} htmlFor="till-book-name" required>
                <Input id="till-book-name" value={bookName} autoComplete="off"
                       onChange={(e) => setBookName(e.target.value)} />
              </Field>
              <Field label={t('till.book.phone')} sub={t('till.book.phone.sub')} htmlFor="till-book-phone" required>
                <Input id="till-book-phone" value={bookPhone} inputMode="tel" autoComplete="off"
                       onChange={(e) => setBookPhone(e.target.value)} />
              </Field>
              {bookRefusal && <Refusal reason={bookRefusal.reason} detail={bookRefusal.detail} />}
            </Modal>

            {paid && (
              /* THE MOMENT. This block renders only after a signature-verified
                 webhook matched the session, so the green here is the fact
                 itself — the one place on the till where real green belongs.
                 The check draws itself and one ring blooms; CSS only. */
              <div className="till-paid" role="status">
                <svg className="till-paid-check" viewBox="0 0 56 56" aria-hidden="true">
                  <circle cx="28" cy="28" r="25.5" />
                  <path d="M17.5 29.5 25 37 38.5 21" />
                </svg>
                <div className="till-paid-word">{t('till.paid.word')}</div>
                <div className="till-paid-amt">{paid.total}</div>
                <p>{t('till.paid.body')}</p>
                {/* RETURN. A settled bill is the only kind a refund can hang
                    off, and this is one — so the door to WAAPSI carries this
                    exact session. Ghost, not green: pressing it moves no money;
                    only the gateway's own signed refund.processed does. */}
                <div className="btn-row" style={{ justifyContent: 'center', marginTop: 12 }}>
                  <a className="btn ghost sm"
                     href={`#/waapsi?session=${encodeURIComponent(paid.session)}`}>
                    {t('till.return.fromPaid')}
                  </a>
                </div>
              </div>
            )}
          </div>
        </Card>

        <Card title={t('till.decides.title')} tight>
          <KV k={t('till.decides.code')}>{t('till.decides.code.v')}</KV>
          <KV k={t('till.decides.look')}>{t('till.decides.look.v')}</KV>
          <KV k={t('till.decides.forget')}>{t('till.decides.forget.v')}</KV>
          <KV k={t('till.decides.rate')}>{Math.round(1000 / 240)}</KV>
          <p className="hint">{tx('till.decides.note')}</p>
        </Card>
      </div>
      </div>
    </div>
  );
}
