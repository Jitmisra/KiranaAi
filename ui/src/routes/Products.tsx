import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as api from '../lib/api';
import * as admin from '../lib/adminapi';
import * as shopapi from '../lib/shopapi';
import { StockOnline } from '../components/StockOnline';
import { rupees } from '../lib/money';
import { useCamera } from '../hooks/useCamera';
import { containedBoxPct } from '../lib/roi';
import {
  Card, KV, Pill, Verdict, Segmented, Empty, Field, Refusal, IcoTag,
  Progress, Skeleton, SkeletonRows, Thinking, Working,
} from '../components/ui';
import { POLL_MS } from '../lib/counter';
import { useT } from '../lib/i18n';
import { voices } from '../lib/audio';
import '../styles/admin.css';
import '../styles/manage.css';

type TeachMode = 'code' | 'photo' | 'mat';
type Source = 'file' | 'camera';

/**
 * When an offer is live the server sends the marked price and the discount on
 * every catalogue row. `api.Sku` does not declare them yet — that file is owned
 * by another surface right now — so the catalogue reads them through this view
 * of the same object rather than asserting fields it does not own.
 */
type PricedSku = api.Sku & { marked_paise?: number; off_paise?: number };

/**
 * Teaching a product.
 *
 * BY CODE is the default and the one that matters: a printed code is an
 * identifier that was READ, so it needs no likeness, no mat and no millimetres
 * — a typed number and a price is a complete product. The photo paths remain
 * for goods with no code on them, and they say plainly what they cannot do.
 */
const BURST_N = 8;
const BURST_GAP_MS = 90;

/**
 * The camera stage's shape, as ONE pair of numbers.
 *
 * It is used TWICE — once as the CSS box the stage is drawn at, and once to
 * work out where inside that box the letterboxed frame actually lands — and
 * the bug this constant exists to prevent is those two answers disagreeing.
 * Hard-coding `4 / 3` in the style and assuming "the layer is the frame" in
 * the maths is exactly what cost the top and bottom of every taught carton.
 */
const STAGE_AR = { w: 4, h: 3 };

/** The dominant reason CODE among the rejected frames — the thing to group on. */
function worstOfCode(rep: api.SaafStackBody): string {
  const counts = new Map<string, number>();
  for (const f of rep.frames) {
    if (f.used) continue;
    counts.set(f.code, (counts.get(f.code) ?? 0) + 1);
  }
  let worst = ''; let n = 0;
  for (const [k, v] of counts) if (v > n) { worst = k; n = v; }
  return worst;
}

/** Name the measurement that failed most often, in words, not in field names. */
function worstOf(rep: api.SaafStackBody): string {
  const counts = new Map<string, number>();
  for (const f of rep.frames) {
    if (f.used) continue;
    counts.set(f.code, (counts.get(f.code) ?? 0) + 1);
  }
  let worst = ''; let n = 0;
  for (const [k, v] of counts) if (v > n) { worst = k; n = v; }
  const prose = GATE_PROSE[worst];
  if (prose) return prose;
  return worst ? `${worst.replace(/_/g, ' ')}.` : 'no measurement was recorded.';
}

/**
 * The reason codes `gawaah/saaf.py` actually emits, in words a shopkeeper can
 * act on. These are copied from R_* in that module, not invented here — an
 * invented key would silently fall through to the raw code and read as a bug
 * report rather than an instruction.
 */
const GATE_PROSE: Record<string, string> = {
  glare: 'too much of the picture is blown out. Move the light, or tilt the packet away from it.',
  blur: 'the picture is not sharp enough. Hold still, or move the camera back a little.',
  defocus: 'the camera never found focus on it. Try again a hand-width further away.',
  ecc_failed: 'the frames could not be lined up with each other. Something moved between them.',
  shift_too_large: 'the camera or the packet moved too far between frames to compare them.',
  warp_not_finite: 'the alignment did not converge on these frames.',
};

/** The four things a person typed into this card. */
type FieldKey = 'sku' | 'name' | 'price' | 'code';
type FieldErrors = Partial<Record<FieldKey, string>>;

/**
 * WHERE A REFUSAL BELONGS.
 *
 * A refusal about the price is not news at the bottom of the card; it is news
 * beside the price. These keys are the R_* names `tools/upload_app.py` actually
 * raises from `/enrol` — copied, not invented, because an invented key would
 * quietly send a real refusal to no field at all and read as a screen that
 * ignored it. The refusal itself is STILL shown whole and verbatim in the
 * `Refusal` below the button; this only puts a second copy of the server's own
 * detail against the control that caused it.
 */
const TEACH_FIELD: Record<string, FieldKey> = {
  sku_id_invalid: 'sku',
  enrol_collision: 'sku',
  name_invalid: 'name',
  price_not_integer_paise: 'price',
};

/** The refusal's own words against the control it is about, or nothing. */
function fieldsFor(reason: string, detail?: string): FieldErrors | undefined {
  const key = TEACH_FIELD[reason];
  if (!key) return undefined;
  return { [key]: detail || reason } as FieldErrors;
}

export default function Products() {
  const { t, tx, tn } = useT();
  const [mode, setMode] = useState<TeachMode>('code');
  const [source, setSource] = useState<Source>('file');
  const [sku, setSku] = useState('');
  const [name, setName] = useState('');
  const [price, setPrice] = useState('');
  const [code, setCode] = useState('');

  const [file, setFile] = useState<File | null>(null);
  const [shot, setShot] = useState<Blob | null>(null);
  const [preview, setPreview] = useState<string | null>(null);

  const [busy, setBusy] = useState(false);
  /**
   * THE GAP THE ENROLMENT PAGE USED TO ADMIT TO.
   *
   * SAAF gated the sticker reference on the brain and nothing else, so the one
   * enrolment a shopkeeper actually performs — teaching a product — took a
   * single frame with no quality check at all. A blurred or glared reference is
   * not a slightly worse reference; it is a permanent one that will mis-price
   * things for as long as it is in the catalogue.
   *
   * So an appearance taught FROM THE CAMERA now goes through the burst gate,
   * and what gets enrolled is the sharpest frame that survived it. There is
   * deliberately no override: a gate you can wave through is decoration.
   */
  const [gate, setGate] = useState<api.SaafStackBody | null>(null);
  /**
   * WHICH LONG THING IS HAPPENING.
   *
   * `gating` used to be one boolean covering two operations that take a second
   * each and look nothing alike — eight frames coming off the camera, and eight
   * cut frames being scored by the server — so the screen could only say "…".
   * Naming the phase is what lets each of them report what it is actually
   * doing, and `burst` is the only one whose progress this browser genuinely
   * knows: it is the one the browser performs.
   */
  const [work, setWork] = useState<null | 'burst' | 'cut' | 'score'>(null);
  const gating = work !== null;
  /** Frames of the burst already in memory. A client-side count, so it is honest. */
  const [burstAt, setBurstAt] = useState(0);
  /**
   * A capture the gate refused, in its own words, shown UNDER THE STAGE where
   * the shopkeeper is looking — not in the teach column. Separate from
   * `result`, which is about the teach call itself.
   */
  const [capErr, setCapErr] = useState<{ reason: string; detail?: string } | null>(null);
  /**
   * The raw burst, held while the operator crops. The gate used to run on the
   * WHOLE frame at capture time, and it judged the room instead of the
   * product: a bright wall behind a perfectly held jar blew the ≤2% glare
   * budget in every frame, 8 of 8 rejected, and the button read as "not
   * capturing". Measured on the operator's own frame — reason `glare`, from a
   * wall the product does not even touch.
   *
   * So capture now freezes INSTANTLY on the first frame, the operator crops to
   * the product, and only then is the burst — every frame cut to the same
   * rectangle — sent through the gate. The wall no longer gets a vote.
   */
  const [burstBlobs, setBurstBlobs] = useState<Blob[] | null>(null);
  const [frozen, setFrozen] = useState<string | null>(null);
  /**
   * Fractional crop over the frozen still — fractions OF THE CAMERA FRAME, in
   * the same space `cropBlob` multiplies by the frame's own pixels. Defaults
   * to the middle.
   */
  const [crop, setCrop] = useState({ x: 0.2, y: 0.12, w: 0.6, h: 0.76 });
  const [dragC, setDragC] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const cropRef = useRef<HTMLDivElement>(null);
  /**
   * Did a HUMAN draw the region in this picture?
   *
   * Tracked as a fact about the blob rather than inferred from the mode,
   * because it changes what the server is allowed to conclude: a rectangle a
   * person dragged around a packet IS the segmentation, and the server must
   * not then refuse it for the very tightness the UI asked for. Set only where
   * a drag actually produced the picture; cleared everywhere else.
   */
  const [drawnBox, setDrawnBox] = useState(false);
  /** Which sku is having a view added, and how it went. */
  const [viewing, setViewing] = useState<string | null>(null);
  /**
   * A REFUSED VIEW IS A REFUSAL, NOT A NOTE.
   *
   * This used to be one string built as `r.detail || r.reason`, which threw one
   * of the server's two sentences away every time it had both — and the reason
   * is the half that NAMES the refusal. Both are kept, and an unsuccessful one
   * renders through `Refusal` on the card it is about, like every other refusal
   * on this counter.
   */
  const [viewNote, setViewNote] = useState<
    | { sku: string; ok: true; text: string }
    | { sku: string; ok: false; reason: string; detail?: string }
    | null
  >(null);
  const [result, setResult] = useState<
    | { kind: 'ok'; taught: api.Taught; appearanceOnly: boolean }
    | { kind: 'refused'; reason: string; detail?: string; canForce: boolean; fields?: FieldErrors }
    | null
  >(null);

  const [skus, setSkus] = useState<api.Sku[]>([]);
  const [loading, setLoading] = useState(true);
  /** A re-read after a write. The list on screen is still true, only stale. */
  const [refreshing, setRefreshing] = useState(false);
  /** A failed /shop rendered as "Nothing taught yet" over a shop of seven. */
  const [shopErr, setShopErr] = useState<{ reason: string; detail?: string } | null>(null);
  /**
   * FORGET threw the server's answer away: a refused delete said nothing at all.
   * It also said nothing WHILE it ran, and it carried no sku — so a refusal
   * about one product was announced at the top of a grid of twenty. Both fixed:
   * the sku is part of the fact, and the refusal renders on that card.
   */
  const [forgetErr, setForgetErr] = useState<{ sku: string; reason: string; detail?: string } | null>(null);
  const [forgetting, setForgetting] = useState<string | null>(null);
  /** Focus target for the empty catalogue's own next action. */
  const skuRef = useRef<HTMLInputElement>(null);
  /**
   * THE RECOGNITION GATES, READ FROM THE COUNTER THAT ENFORCES THEM.
   *
   * The legend below used to print `0.92` as the similarity bar, with a table
   * of cosines beside it. Both were facts about the RETIRED 461-dimension
   * handcrafted descriptor. The live path embeds through
   * `gawaah/embedder2.py`, and the gates were re-derived from a measured
   * separation bench — `0.90 -> 0.55` and `0.92 -> 0.60`, recorded in
   * `gawaah/identity.py` and in FAILURES.md. The screen had gone past stale
   * and become INVERTED: it told a shopkeeper a product would not be
   * recognised at an angle whose measured score clears the bar that is
   * actually shipping, while Settings, reading the same server, printed the
   * real number. A page must not disagree with the counter it is a window on,
   * so this one asks rather than remembers.
   */
  const [gates, setGates] = useState<api.Health['identity_gates'] | null>(null);
  const [gatesErr, setGatesErr] = useState<{ reason: string; detail?: string } | null>(null);
  const [gatesLoading, setGatesLoading] = useState(true);

  const cam = useCamera();
  /** The camera is being opened. A permission prompt is not instant. */
  const [starting, setStarting] = useState(false);
  const [aim, setAim] = useState<{ found: number; payloads: string[]; hint?: string }>({ found: 0, payloads: [] });
  /**
   * Has the aiming poll ANSWERED yet?
   *
   * Without this the bar said "no code readable yet" from the instant the
   * camera opened — a report on a look that had not happened. A quarter of a
   * second of "no" before the first frame is even grabbed is the counter
   * claiming a reading it has not taken.
   */
  const [aimSeen, setAimSeen] = useState(false);
  /** The last code announced, so one packet chimes once rather than four times a second. */
  const lastHeard = useRef<string | null>(null);
  /** Which value the camera filled in, so the page can say it did. */
  const [autoFilled, setAutoFilled] = useState<string | null>(null);

  /**
   * THE SHELF, AS THE STOREFRONT SELLS AGAINST IT.
   *
   * One request for the whole catalogue — `/orders/stock` — rather than one
   * per card: it carries, per product, the shopkeeper's count, what open
   * orders are holding, the floor, and what a phone may buy, already worked
   * out by the server. The card prints those terms and recomputes nothing, so
   * the figure beside RECORD here is the figure a customer's phone was given.
   *
   * `null` until it has answered, so the strip can show a skeleton rather than
   * "not counted" over a shelf that has been counted.
   */
  const [online, setOnline] = useState<Map<string, shopapi.OnlineStockRow> | null>(null);
  const [onlineErr, setOnlineErr] = useState<string | null>(null);
  const loadOnline = useCallback(async () => {
    const r = await shopapi.onlineStock();
    if (!r.ok) {
      setOnlineErr(`${r.reason}${r.detail ? ` — ${r.detail}` : ''}`);
      setOnline(new Map());
      return;
    }
    // `figures: false` is a stock module that could not answer; every row is
    // then null and the strip says why, rather than reading as an empty shop.
    setOnlineErr(r.figures ? null : (r.error ?? 'the stock figures could not be read'));
    setOnline(new Map(r.items.map((row) => [row.sku_id, row])));
  }, []);
  useEffect(() => { void loadOnline(); }, [loadOnline]);

  const loadShop = useCallback(async (again = false) => {
    if (again) setRefreshing(true);
    // A taught or forgotten product changes what the shelf has rows for.
    if (again) void loadOnline();
    const r = await api.shop();
    setLoading(false);
    setRefreshing(false);
    if (r.ok) { setSkus(r.skus); setShopErr(null); return; }
    // An empty list and an unreadable list are different facts. Rendering both
    // as "Nothing taught yet" told a shopkeeper their catalogue was gone.
    setShopErr({ reason: r.reason, detail: r.detail });
  }, [loadOnline]);
  useEffect(() => { void loadShop(); }, [loadShop]);

  const loadGates = useCallback(async () => {
    setGatesLoading(true);
    const r = await api.health();
    setGatesLoading(false);
    if (r.ok) { setGates((r as unknown as api.Health).identity_gates); setGatesErr(null); return; }
    setGatesErr({ reason: r.reason, detail: r.detail });
  }, []);
  useEffect(() => { void loadGates(); }, [loadGates]);

  /* ---- the picture ------------------------------------------------------- */

  const image = useMemo<Blob | null>(() => (source === 'camera' ? shot : file), [source, shot, file]);

  useEffect(() => {
    if (!image) { setPreview(null); return; }
    const url = URL.createObjectURL(image);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [image]);

  /**
   * Aiming preview. While the teach camera is open this polls `/codes`, which
   * is an AIMING endpoint and never becomes a bill — a shopkeeper holding a
   * packet up needs to know NOW whether the code is legible, not after pressing
   * TEACH and being told nothing was bound.
   */
  // `busyAim` lives in a ref, not in the effect. `cam` is a fresh object
  // literal every render, so with it in the dep array every keystroke in the
  // sku/name/price fields tore down and rebuilt the 240 ms interval — and the
  // single-flight guard, being a `let` per run, did not survive the re-arm.
  const busyAim = useRef(false);
  useEffect(() => {
    // A held capture freezes the stage on the still, so aiming against the
    // live feed underneath it would be feedback about a picture nobody can
    // see. The poll pauses until RETAKE.
    if (source !== 'camera' || !cam.running || shot) return;
    let alive = true;
    // Every arming of the loop starts from "not looked yet", so RETAKE gets the
    // same honest first quarter-second the first open did.
    setAimSeen(false);
    const id = setInterval(async () => {
      if (busyAim.current || !alive) return;
      busyAim.current = true;
      try {
        const blob = await cam.capture({ x: 0, y: 0, w: cam.frame.w, h: cam.frame.h });
        if (!blob) return;
        const d = await api.codes(blob);
        if (!alive) return;
        setAimSeen(true);
        if (d.ok) {
          // Defensive on the array even though the type promises it: this exact
          // line threw on every poll for a week because the field was misnamed,
          // and the failure mode was a preview that lied rather than one that broke.
          const codes = d.codes ?? [];
          setAim({
            found: d.count ?? codes.length,
            payloads: codes.map((c) => `${c.payload}  (${c.format}, ${c.px_across}px, ${c.read_by})`),
            hint: d.hint,
          });

          // SAME FEEDBACK AS THE TILL. A shopkeeper holding a packet up should
          // not have to watch a text line to know it was read: the counter
          // chimes and fills the field in, exactly as it does when billing.
          // Announced ONCE per distinct code — a chirp four times a second
          // while the packet sits there is noise, not information.
          const first = codes[0];
          if (first && first.payload !== lastHeard.current) {
            lastHeard.current = first.payload;
            voices.named(0);
            setCode((cur) => (cur.trim() ? cur : first.payload));
            setAutoFilled(first.payload);
          }
          if (!first) lastHeard.current = null;
        } else {
          setAim({ found: 0, payloads: [], hint: d.reason });
        }
      } catch (e) {
        // An aiming poll that throws must not take the loop down with it.
        if (alive) { setAimSeen(true); setAim({ found: 0, payloads: [], hint: `aiming failed: ${String(e)}` }); }
      } finally {
        busyAim.current = false;
      }
    }, POLL_MS);
    return () => { alive = false; clearInterval(id); };
  }, [source, cam.running, cam.frame.w, cam.frame.h, cam, shot]);

  /**
   * CAPTURE runs the burst gate, not TEACH.
   *
   * It used to be the other way round: CAPTURE took one decorative frame and
   * TEACH captured eight FRESH ones — so a shopkeeper could hold the packet
   * perfectly, see "frame captured", relax their arm, and then have all eight
   * of the frames that actually mattered rejected for focus. The moment the
   * button is pressed is the moment the packet is being held up, so that is
   * when the burst happens. What survives is shown frozen in the stage, the
   * contact sheet appears immediately, and TEACH sends the survivor without
   * re-bursting.
   *
   * BY CODE still takes a single frame: a code is an identifier to be read,
   * not a likeness to be judged, and the gate does not apply to it.
   */
  const grab = useCallback(async () => {
    if (!cam.running) return;
    setCapErr(null); setGate(null); setShot(null); setBurstBlobs(null); setFrozen(null);
    setDrawnBox(false);
    if (mode === 'code') {
      const blob = await cam.capture({ x: 0, y: 0, w: cam.frame.w, h: cam.frame.h }, 0.92);
      if (blob) setShot(blob);
      return;
    }
    // Eight frames into memory, ~a second, then FREEZE on the first. Nothing
    // is judged yet — judging happens after the crop, on the crop.
    //
    // The count is reported as it climbs. This browser is the one taking the
    // frames, so `burstAt` is a fact it owns rather than a stage it is guessing
    // at on the server's behalf — and a second of "hold still" with no sign of
    // anything happening is the second in which an arm relaxes.
    setWork('burst'); setBurstAt(0);
    try {
      const burst: Blob[] = [];
      for (let i = 0; i < BURST_N; i += 1) {
        const b = await cam.capture({ x: 0, y: 0, w: cam.frame.w, h: cam.frame.h }, 0.92);
        if (b) burst.push(b);
        setBurstAt(i + 1);
        if (i < BURST_N - 1) await new Promise((r) => setTimeout(r, BURST_GAP_MS));
      }
      if (!burst.length) {
        setCapErr({ reason: 'The camera produced no frames', detail: 'Nothing came back from the capture. Check the camera and try again.' });
        return;
      }
      setBurstBlobs(burst);
      setFrozen(URL.createObjectURL(burst[0] as Blob));
    } finally { setWork(null); }
  }, [cam, mode]);

  /** Cut one region out of one captured frame, in pixels of the frame itself. */
  const cropBlob = useCallback(async (blob: Blob, r: { x: number; y: number; w: number; h: number }): Promise<Blob | null> => {
    const bmp = await createImageBitmap(blob);
    const x = Math.round(r.x * bmp.width), y = Math.round(r.y * bmp.height);
    const w = Math.max(24, Math.round(r.w * bmp.width));
    const h = Math.max(24, Math.round(r.h * bmp.height));
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    c.getContext('2d')!.drawImage(bmp, x, y, w, h, 0, 0, w, h);
    bmp.close();
    return new Promise((res) => c.toBlob(res, 'image/jpeg', 0.92));
  }, []);

  /**
   * The operator decided where the product is. Cut every buffered frame to
   * that rectangle, gate the CUT frames, keep the sharpest survivor.
   */
  const useThisPicture = useCallback(async () => {
    if (!burstBlobs?.length) return;
    // TWO STAGES, AND ONLY ONE OF THEM IS THIS BROWSER'S. The cutting happens
    // here, on a canvas, so it may be reported as done. The scoring happens on
    // the server, so it is never ticked.
    setWork('cut'); setCapErr(null); setGate(null);
    try {
      const cut = (await Promise.all(burstBlobs.map((b) => cropBlob(b, crop))))
        .filter((b): b is Blob => b !== null);
      setWork('score');
      if (cut.length < 2) {
        // One frame cannot be gated — the measurements are comparative. Keep
        // it rather than pretending a check happened, and say so.
        if (cut[0]) { setShot(cut[0]); setDrawnBox(true); setBurstBlobs(null); }
        else setCapErr({ reason: 'The crop produced nothing usable', detail: 'Draw a larger rectangle and try again.' });
        return;
      }
      const g = await api.saafStack(cut);
      const rep = (g as unknown as api.SaafStackBody);
      if (Array.isArray(rep?.frames)) {
        setGate(rep);
        if ((rep.used ?? 0) < 1) {
          // THE FRAME'S OWN SENTENCE, NOT A NUMBER THIS PAGE PICKED.
          //
          // This used to print `max(blur_score)` next to whichever reason was
          // most common -- two facts from different frames, glued together, and
          // they contradicted each other on a real capture: "the camera never
          // found focus ... scored 0.387 against a 0.46 ceiling", when 0.387 is
          // comfortably UNDER that ceiling. A refusal that argues with itself is
          // worse than one that says nothing.
          //
          // `reason` already carries the measurement that actually failed,
          // written by the module that failed it, so the number and the words
          // can never come apart again.
          const worstCode = worstOfCode(rep);
          const measured = (() => {
            const f = rep.frames.find((x) => !x.used && x.code === worstCode && x.reason);
            return f ? ` ${String(f.reason).replace(/^[a-z_]+:\s*/, '').trim()}` : '';
          })();
          setCapErr({
            reason: 'Every frame failed the quality gate',
            detail: `${rep.rejected} of ${rep.burst} frames rejected — `
              + `${worstOf(rep)}${measured} The gate judged ONLY the rectangle you drew, `
              + `so this is about the product, not the room. Nothing was kept. `
              + `UPLOAD A FILE takes a still photo instead, which is not burst-gated.`,
          });
          return;
        }
        const best = rep.reference_index ?? rep.frames.find((f) => f.used)?.index ?? 0;
        setShot(cut[best] ?? cut[0] ?? null);
        setDrawnBox(true);
        setBurstBlobs(null);
      } else if (!g.ok) {
        setCapErr({ reason: g.reason, detail: g.detail });
      } else {
        setShot(cut[0] ?? null);
        setDrawnBox(true);
        setBurstBlobs(null);
      }
    } finally { setWork(null); }
  }, [burstBlobs, crop, cropBlob]);

  /** Back to the live feed: drop the still, the buffer, and the gate report. */
  const retake = useCallback(() => {
    setShot(null); setGate(null); setCapErr(null);
    setBurstBlobs(null); setDrawnBox(false);
    setFrozen((u) => { if (u) URL.revokeObjectURL(u); return null; });
  }, []);

  /**
   * A pointer on screen -> a fraction OF THE CAMERA FRAME.
   *
   * `cropRef` is pinned to the still's pixels, not to the stage (see
   * `stillFit` and `containedBoxPct`), so this division is already in frame
   * space and needs no letterbox correction of its own. That is the whole
   * point of pinning it there: one coordinate space, no conversion to forget.
   */
  const toCropFrac = useCallback((e: React.PointerEvent) => {
    const el = cropRef.current;
    if (!el) return { x: 0, y: 0 };
    const r = el.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
      y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)),
    };
  }, []);

  /**
   * Where the frozen still's pixels land inside the 4:3 stage.
   *
   * The still is `object-fit: contain` and the camera is 16:9, so it is
   * letterboxed: 12.5% of the stage's height is black bar, top and bottom. The
   * drag layer used to cover the whole stage, so a fraction of the layer was
   * NOT a fraction of the frame — and `cropBlob` multiplies by the frame. Both
   * halves now read from the same numbers.
   */
  const stillFit = containedBoxPct(cam.frame, STAGE_AR);
  const stillFitStyle = {
    left: `${stillFit.left}%`, top: `${stillFit.top}%`,
    width: `${stillFit.width}%`, height: `${stillFit.height}%`,
  } as const;

  const shownCrop = dragC
    ? { x: Math.min(dragC.x0, dragC.x1), y: Math.min(dragC.y0, dragC.y1),
        w: Math.abs(dragC.x1 - dragC.x0), h: Math.abs(dragC.y1 - dragC.y0) }
    : crop;

  /* ---- teaching ---------------------------------------------------------- */

  const teach = useCallback(async (force = false) => {
    if (!sku.trim() || !name.trim() || !price.trim()) {
      // The refusal stays whole under the button — it is the sentence a person
      // reads — and each empty control is ALSO marked, so the eye is sent to
      // the box that has to change rather than made to work out which of three.
      setResult({
        kind: 'refused', reason: 'Needs all three',
        detail: 'An sku id, a name and a price.', canForce: false,
        fields: {
          ...(sku.trim() ? {} : { sku: 'This is empty. The counter needs a handle for the product.' }),
          ...(name.trim() ? {} : { name: 'This is empty. The bill has to print something.' }),
          ...(price.trim() ? {} : { price: 'This is empty. A product with no price cannot be sold.' }),
        },
      });
      return;
    }
    if (mode === 'code' && !code.trim() && !image) {
      setResult({
        kind: 'refused',
        reason: 'Nothing to read a code from',
        detail: 'Type the number under the bars, or show the packet to the camera.',
        canForce: false,
        fields: { code: 'Type the digits under the bars, or show the packet to the camera.' },
      });
      return;
    }
    if (mode !== 'code' && !image) {
      setResult({ kind: 'refused', reason: 'No picture yet', detail: 'Choose a file or capture a frame.', canForce: false });
      return;
    }
    setBusy(true);
    setResult(null);

    try {
      const fields: api.TeachFields = {
        sku_id: sku.trim(),
        name: name.trim(),
        price_rupees: price.trim(),
      };
      if (code.trim()) fields.barcode = code.trim();
      if (mode === 'code') fields.mode = 'basket';
      else if (mode === 'photo' || force) fields.mode = 'plain_photo';
      if (force) fields.force = '1';
      /**
       * SAY WHO DREW THE REGION.
       *
       * A picture the operator cropped by hand has already had the question
       * "where is the product?" answered, by a person, on purpose, tightly —
       * this page's own instruction is "DRAW A BOX AROUND THE PRODUCT". The
       * server otherwise re-segments it and refuses a good tight box with
       * `matless_region_touches_every_border`, which is the right answer for a
       * whole photograph and the wrong one for a rectangle a human drew. This
       * flag is the difference, and it is only ever set when a drag actually
       * produced the blob being sent.
       */
      if (fields.mode === 'plain_photo' && source === 'camera' && drawnBox) {
        fields.region = 'user_drawn';
      }

      // `/enrol` always takes an image; when a code is typed it is never read.
      // A camera capture was already burst-gated when it was taken, and `image`
      // IS that burst's sharpest survivor — nothing is re-captured here, so
      // what is taught is exactly the frame the shopkeeper saw frozen in the
      // stage. A file cannot be gated at all, and the page says so.
      const blob = image ?? api.EMPTY_PNG();
      const r = await api.enrol(blob, fields);
      if (!r.ok) {
        // NOTHING TYPED AND NOTHING CHOSEN IS THROWN AWAY HERE. The fields and
        // the picture are only cleared on the success path below; a refusal
        // leaves the whole card exactly as the shopkeeper left it, so fixing
        // two characters costs two characters.
        setResult({
          kind: 'refused', reason: r.reason, detail: r.detail,
          canForce: mode === 'mat' && !force,
          fields: fieldsFor(r.reason, r.detail),
        });
        return;
      }
      setResult({ kind: 'ok', taught: r as unknown as api.Taught, appearanceOnly: !!(r as api.Taught).appearance_only });
      setSku(''); setName(''); setPrice(''); setCode('');
      setFile(null); setShot(null); setGate(null); setCapErr(null); setDrawnBox(false);
      void loadShop(true);
    } finally {
      setBusy(false);
    }
  }, [sku, name, price, code, mode, image, source, drawnBox, cam, loadShop]);

  /**
   * ANOTHER ANGLE OF A PRODUCT ALREADY TAUGHT.
   *
   * THE TABLE THAT USED TO BE HERE HAS BEEN REMOVED, NOT UPDATED. It read
   * "cosine falls from 1.000 at 0 degrees to 0.874 at 5 and 0.775 at 10,
   * against a gate of 0.92", and every one of those numbers was measured on
   * the retired 461-dimension handcrafted descriptor. The live path is
   * `gawaah/embedder2.py` (SqueezeNet fire9, nuisance-whitened, rotation-TTA)
   * and its gate was re-derived from a separation bench: `0.92 -> 0.60` for a
   * photo-taught product, `0.90 -> 0.55` otherwise. See the constant docstring
   * in `gawaah/identity.py`, which keeps the new measurements and points at
   * the retired tables in git history.
   *
   * No angle table replaces it, because this file has no measurement of the
   * SHIPPED embedder against rotation to quote, and quoting one descriptor's
   * numbers under another's name is exactly the failure being fixed. What the
   * screen says instead is what the gate IS, read live from `/health`.
   *
   * The feature is unchanged and still worth having: a packet has more than
   * one face, and a view is one face.
   *
   * Uses the live camera when it is running, because turning the packet and
   * pressing again is the whole gesture, and falls back to a file so this
   * works on a machine with no camera at all.
   */
  const addViewFrom = useCallback(async (skuId: string, blob: Blob) => {
    if (viewing) return;
    setViewing(skuId); setViewNote(null);
    try {
      const r = await api.addView(blob, skuId);
      if (!r.ok) {
        // BOTH SENTENCES. `r.detail || r.reason` dropped the name of the
        // refusal whenever the server sent an explanation with it, which is
        // most of the time — and the name is the half a shopkeeper can look up.
        setViewNote({ sku: skuId, ok: false, reason: r.reason, detail: r.detail });
        return;
      }
      const v = r as unknown as api.ViewAdded;
      setViewNote({
        sku: skuId, ok: true,
        text: `Now ${v.views_after} views, was ${v.views_before}. This one matched the `
            + `others at ${v.similarity_to_existing.toFixed(3)}.`,
      });
      voices.named(0);
      void loadShop(true);
    } finally { setViewing(null); }
  }, [loadShop, viewing]);

  const addViewFromCamera = useCallback(async (skuId: string) => {
    const blob = await cam.capture({ x: 0, y: 0, w: cam.frame.w, h: cam.frame.h }, 0.92);
    if (blob) await addViewFrom(skuId, blob);
  }, [cam, addViewFrom]);

  const forget = useCallback(async (id: string) => {
    if (forgetting) return;
    setForgetting(id); setForgetErr(null);
    try {
      const r = await api.forget(id);
      // The dangerous case is a PARTIAL removal: the descriptor gone but the code
      // binding and the published price still live. Silence over that leaves a
      // code that still prices a product the counter says it has forgotten.
      setForgetErr(r.ok ? null : { sku: id, reason: r.reason, detail: r.detail });
      await loadShop(true);
    } finally { setForgetting(null); }
  }, [loadShop, forgetting]);

  /**
   * CORRECTING A PRODUCT INSTEAD OF FORGETTING IT.
   *
   * Until this existed a mistyped price meant FORGET and photograph the packet
   * again — throwing away every taught view, the millimetres and the photograph
   * to fix two characters. (The cosine table that used to quantify that cost
   * here was measured on the retired handcrafted descriptor and is gone; see
   * the note on `addViewFrom`. The cost itself is unchanged and needs no
   * number: a product re-taught from one fresh photo has one view where it had
   * several, and no millimetres at all.)
   *
   * The editor changes the name, the price and the bound code. It cannot reach
   * the vectors, the footprint or the photograph, and it cannot move the sku
   * id — the server refuses a body that tries, because that id is what the code
   * bindings, the orders and every bill already printed refer to.
   */
  const [editing, setEditing] = useState<string | null>(null);
  const [editNote, setEditNote] = useState<{ sku: string; text: string } | null>(null);

  const onEdited = useCallback((skuId: string, r: admin.SkuEdited) => {
    setEditing(null);
    const parts: string[] = [];
    if (r.changed.includes('name')) parts.push(`renamed to “${r.after.name}”`);
    if (r.changed.includes('price')) {
      parts.push(`priced ${r.before.price_rupees ?? '—'} → ${r.after.price_rupees ?? '—'}`);
    }
    if (r.changed.includes('code')) {
      parts.push(r.codes.bound ? `code now ${r.codes.bound}` : 'code cleared');
    }
    // The audit note is the server's own account of whether the change is on a
    // chain. Shown, not paraphrased: a price change that was made but NOT
    // recorded is the one case a shopkeeper has to know about.
    const chain = r.changed.includes('price')
      ? (r.audit ? ' The old and new price are on the shop’s audit chain.' : ` ${r.audit_note}`)
      : '';
    setEditNote({
      sku: skuId,
      text: (parts.length ? parts.join(', ') + '.' : 'Nothing had changed.') + chain,
    });
    void loadShop(true);
  }, [loadShop]);

  /* ---- render ------------------------------------------------------------ */

  /** Whether the teach flow needs a picture at all right now. */
  const wantsPicture = mode !== 'code' || !code.trim();
  /** The refusal's own words, per control. Cleared the moment a new teach starts. */
  const bad: FieldErrors = (result?.kind === 'refused' && result.fields) || {};
  /**
   * WHY THE BUTTON IS OFF.
   *
   * A control that is disabled and silent is a control the shopkeeper reads as
   * broken. Every disabled state on this card now answers the question.
   */
  const teachOff = busy
    ? 'The counter is teaching this product. One press is one enrolment.'
    : work === 'burst'
      ? 'The camera is still taking the eight frames. Hold the packet still.'
      : work
        ? 'The frames you captured are still being scored. This finishes in about a second.'
        : null;

  return (
    <div className="stack">
      <div className="page-head">
        <h1>{t('nav.products')}</h1>
        <p>
          Teach the counter what a thing is and what it costs. A printed code is the strongest
          teacher — it is an identifier that was read, not a likeness that was judged, so it needs
          no photograph, no mat and no millimetres.
        </p>
      </div>

      {/* ZONE ONE — teaching. The form beside the picture, the action beneath
          the form, so at 1440 the whole job fits one screen and at 390 the
          zones stack in task order: describe it, show it, teach it. */}
      <Card
        icon={<IcoTag />}
        title={t('products.teach.title')}
        aside={(
          <Segmented<TeachMode>
            value={mode}
            onChange={setMode}
            options={[
              { value: 'code', label: t('products.mode.code'), title: t('products.mode.code.t') },
              { value: 'photo', label: t('products.mode.photo'), title: t('products.mode.photo.t') },
              { value: 'mat', label: t('products.mode.mat'), title: t('products.mode.mat.t') },
            ]}
          />
        )}
      >
        <div className="mg-teach">
          <div className="mg-teach-form">
            {/* EVERY ONE OF THESE FOUR IS TIED TO ITS VISIBLE LABEL.
                They had no accessible name at all: no `for`, no wrapping
                label, no aria-label — so a screen reader read the placeholder
                and announced the sku box as "edit text, parle_g_biscuit",
                which is an example, not a name. The correction form directly
                below has always been labelled properly; the teach form, which
                is the first thing anyone does on this counter, was not. The
                association is `label[for]`, the same as that form, rather than
                an aria-label — a label the eye and the ear share cannot drift
                apart. PLACEHOLDERS ARE UNTOUCHED: the e2e suite addresses
                these four controls by them. */}
            <Field
              label={t('products.f.sku')}
              htmlFor="tp-sku"
              sub="lowercase, no spaces — the counter's own handle for it"
              error={bad.sku}
            >
              <input
                id="tp-sku"
                ref={skuRef}
                value={sku}
                onChange={(e) => setSku(e.target.value)}
                placeholder="parle_g_biscuit"
                aria-invalid={bad.sku ? true : undefined}
              />
            </Field>
            <Field label={t('products.f.name')} htmlFor="tp-name" sub={t('products.f.name.sub')} error={bad.name}>
              <input
                id="tp-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t('products.teach.nameEg')}
                aria-invalid={bad.name ? true : undefined}
              />
            </Field>
            <Field
              label={t('products.f.price')}
              htmlFor="tp-price"
              sub={t('products.f.price.sub')}
              error={bad.price}
            >
              <input
                id="tp-price"
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                placeholder="10"
                inputMode="decimal"
                aria-invalid={bad.price ? true : undefined}
              />
            </Field>
            <Field
              label={t(mode === 'code' ? 'products.f.code.label' : 'products.f.code.optional')}
              htmlFor="tp-code"
              error={bad.code}
              sub={autoFilled && code === autoFilled
                ? t('products.f.code.read', { code: autoFilled })
                : t('products.f.code.sub')}
            >
              <input
                id="tp-code"
                value={code}
                onChange={(e) => { setCode(e.target.value); setAutoFilled(null); }}
                placeholder="8901063093157"
                inputMode="numeric"
                aria-invalid={bad.code ? true : undefined}
              />
            </Field>
          </div>

          <div className="mg-teach-pic">
            {wantsPicture ? (
              <>
                <div className="mg-pic-head">
                  <Segmented<Source>
                    value={source}
                    onChange={(s) => { setSource(s); if (s === 'file') cam.stop(); }}
                    options={[
                      { value: 'file', label: t('products.src.file') },
                      { value: 'camera', label: t('products.src.camera') },
                    ]}
                  />
                </div>

                {source === 'file' ? (
                  <div className="thumb-slot" style={{ marginBottom: 12 }}>
                    {preview ? <img src={preview} alt={t('products.pic.alt')} /> : <span className="muted">{t('products.pic.none')}</span>}
                  </div>
                ) : (
                  <div className="stage" style={{ aspectRatio: `${STAGE_AR.w} / ${STAGE_AR.h}`, marginBottom: 12 }}>
                    {/* The <video> stays MOUNTED while the still covers it.
                        Unmounting it kills the stream, and RETAKE should be
                        one press, not a camera restart. */}
                    <video ref={cam.videoRef} playsInline muted style={{ display: cam.running ? 'block' : 'none' }} />
                    {burstBlobs && frozen ? (
                      <>
                        {/* THE CROP IS THE DECISION. The frame is frozen; the
                            operator draws where the product is, and only that
                            rectangle is judged and taught. The room — the
                            bright wall that was failing every frame on glare —
                            never gets a vote again. */}
                        <img className="mg-still" src={frozen} alt="the captured frame, frozen for cropping" />
                        {/* PINNED TO THE PICTURE, NOT TO THE STAGE. The layer
                            covers exactly the pixels the frozen frame occupies,
                            so the dashed rectangle the operator drags and the
                            rectangle `cropBlob` cuts out of the 1280x720 frame
                            are the same rectangle, at any window size and any
                            camera aspect ratio. */}
                        <div
                          ref={cropRef}
                          className="mg-croplayer"
                          style={stillFitStyle}
                          onPointerDown={(e) => {
                            (e.target as HTMLElement).setPointerCapture(e.pointerId);
                            const f = toCropFrac(e); setDragC({ x0: f.x, y0: f.y, x1: f.x, y1: f.y });
                          }}
                          onPointerMove={(e) => {
                            if (!dragC) return;
                            const f = toCropFrac(e); setDragC((d) => (d ? { ...d, x1: f.x, y1: f.y } : d));
                          }}
                          onPointerUp={() => {
                            if (!dragC) return;
                            const r = { x: Math.min(dragC.x0, dragC.x1), y: Math.min(dragC.y0, dragC.y1),
                                        w: Math.abs(dragC.x1 - dragC.x0), h: Math.abs(dragC.y1 - dragC.y0) };
                            setDragC(null);
                            if (r.w > 0.05 && r.h > 0.05) setCrop(r);
                          }}
                        >
                          <div
                            className="mg-croprect"
                            style={{
                              left: `${shownCrop.x * 100}%`, top: `${shownCrop.y * 100}%`,
                              width: `${shownCrop.w * 100}%`, height: `${shownCrop.h * 100}%`,
                            }}
                          />
                        </div>
                        <div className="stage-bar">
                          <Pill tone="amb" dot>{t('products.draw')}</Pill>
                          <span>only the box is checked and taught — the room stays out of it</span>
                        </div>
                      </>
                    ) : shot && preview ? (
                      <>
                        <img className="mg-still" src={preview} alt="the captured frame, frozen" />
                        <div className="stage-bar">
                          <Pill tone="ok" dot>{t('products.captured')}</Pill>
                          <span>this exact crop is what will be taught — RETAKE to go back live</span>
                        </div>
                      </>
                    ) : !cam.running ? (
                      <div className="camgate">
                        <h3>{t('products.show')}</h3>
                        <p>{t('products.show.sub')}</p>
                        <button
                          className="btn primary"
                          disabled={starting}
                          title={starting ? 'Waiting for the browser to hand over the camera.' : undefined}
                          onClick={() => {
                            voices.unlock();
                            // OPENING A CAMERA IS NOT INSTANT. A permission
                            // prompt can sit there for seconds, and a button
                            // that looks untouched through all of it reads as
                            // a button that did nothing.
                            setStarting(true);
                            void cam.start().finally(() => setStarting(false));
                          }}
                        >
                          {starting ? 'STARTING…' : 'START CAMERA'}
                        </button>
                        {starting && (
                          <p className="camgate-wait">
                            Waiting for the browser. If it is asking whether this page may use the
                            camera, say yes — nothing is uploaded until you press CAPTURE.
                          </p>
                        )}
                        {cam.error && <Verdict tone="red" title={t('products.camera.dead')}>{cam.error}</Verdict>}
                      </div>
                    ) : (
                      <div className="stage-bar">
                        {work === 'burst' ? (
                          /* BLUE, NOT AMBER. This is the machine working, and
                             amber on this counter means it abstained. */
                          <Pill tone="code" dot>{t('products.burst', { at: burstAt, n: BURST_N })}</Pill>
                        ) : gating ? (
                          <Pill tone="code" dot>{t('products.checking')}</Pill>
                        ) : aim.found > 0 ? (
                          <Pill tone="ok" dot>{aim.found} CODE{aim.found === 1 ? '' : 'S'} READABLE</Pill>
                        ) : !aimSeen ? (
                          /* IT HAS NOT LOOKED YET. "no code readable" before the
                             first frame has been read is a reading that was
                             never taken, reported as a result. */
                          <Pill tone="code" dot>{t('products.lookingForCode')}</Pill>
                        ) : (
                          <Pill tone="amb">no code readable yet</Pill>
                        )}
                        <span className="mono">{gating || !aimSeen ? '' : aim.payloads[0] ?? aim.hint ?? ''}</span>
                      </div>
                    )}
                  </div>
                )}

                <div className="btn-row">
                  {source === 'file' ? (
                    <FilePick onPick={(f) => { setFile(f); setShot(null); setDrawnBox(false); }} />
                  ) : (
                    <>
                      {burstBlobs ? (
                        <>
                          <button className="btn primary" onClick={() => void useThisPicture()} disabled={gating}>
                            {gating ? 'CHECKING THE BOX…' : 'USE THIS BOX'}
                          </button>
                          <button className="btn" onClick={retake} disabled={gating}>{t('products.retake')}</button>
                        </>
                      ) : shot ? (
                        <button className="btn" onClick={retake}>{t('products.retake')}</button>
                      ) : (
                        <button
                          className="btn"
                          onClick={() => void grab()}
                          disabled={!cam.running || gating}
                          title={!cam.running ? 'The camera is not open yet — press START CAMERA.' : undefined}
                        >
                          {gating ? 'CAPTURING…' : 'CAPTURE THIS FRAME'}
                        </button>
                      )}
                      {cam.running && <button className="btn ghost" onClick={cam.stop} disabled={gating}>{t('products.stopCamera')}</button>}
                    </>
                  )}
                </div>

                {/* Every disabled control on this card says why it is off. */}
                {source === 'camera' && !cam.running && !gating && !shot && (
                  <p className="hint">
                    CAPTURE is off until the camera is open. Press START CAMERA in the stage above —
                    nothing leaves this browser until you capture.
                  </p>
                )}

                {/* ---- the two long operations, each reporting what it is ---- */}

                {/* THE BURST. Eight frames, taken here, counted here. The
                    fraction is genuine: this browser is the thing doing it. */}
                {work === 'burst' && (
                  <div className="mg-work">
                    <div className="mg-work-head">
                      <Working />
                      <span>Taking {BURST_N} frames — hold the packet still</span>
                      <span className="tnum">{burstAt} / {BURST_N}</span>
                    </div>
                    <Progress pct={(burstAt / BURST_N) * 100} label="Frames captured" />
                    <p className="hint">
                      Nothing is judged yet. You will draw a box around the product and only that
                      rectangle is scored.
                    </p>
                  </div>
                )}

                {/* THE GATE. The cutting is this browser's and is ticked; the
                    scoring is the server's and is not — a tick the page cannot
                    verify is the same lie as a figure it cannot derive. */}
                {(work === 'cut' || work === 'score') && (
                  <Thinking
                    className="mg-thinking"
                    title={t('products.checkingBox')}
                    steps={[
                      {
                        label: `Cutting ${burstBlobs?.length ?? BURST_N} frames to your rectangle`,
                        state: work === 'cut' ? 'now' : 'done',
                      },
                      { label: 'Scoring each cut frame on glare, blur and focus' },
                      { label: 'Keeping the sharpest one that survived' },
                    ]}
                    foot="Only the first step is ticked: this browser does the cutting, and it cannot see which stage the gate is in. The room outside your box is not measured."
                  />
                )}

                {/* The gate's verdict on the capture, right where the capture
                    happened: one chip per frame, kept or rejected, and the
                    reason on hover. */}
                {source === 'camera' && gate && (
                  <div style={{ marginTop: 12 }}>
                    <KV k="quality gate">
                      <Pill tone={gate.used > 0 ? 'ok' : 'bad'}>
                        {gate.used} kept · {gate.rejected} rejected
                      </Pill>
                      <span className="muted" style={{ marginLeft: 10 }}>
                        {gate.used > 0
                          ? `keeping the sharpest survivor${gate.reference_index != null ? ` (frame ${gate.reference_index + 1})` : ''}`
                          : 'nothing survived, so nothing was kept'}
                      </span>
                    </KV>
                    <div className="contact-sheet">
                      {gate.frames.map((f) => (
                        <span
                          key={f.index}
                          className={`chip ${f.used ? 'ok' : 'bad'}`}
                          title={f.used ? `frame ${f.index + 1}: kept` : `frame ${f.index + 1}: ${f.reason}`}
                        >
                          {f.index + 1}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {/* The capture's own refusal, under the stage where the
                    capture happened, with the one thing that answers it. */}
                {source === 'camera' && capErr && (
                  <Refusal
                    reason={capErr.reason}
                    detail={capErr.detail}
                    hint="Nothing was stored. The fields you typed are untouched."
                    action={<button className="btn sm" onClick={retake}>{t('products.backToLive')}</button>}
                  />
                )}
              </>
            ) : (
              /* A typed code needs no likeness — the zone says so rather than
                 collapsing and leaving half the card blank. */
              <div className="mg-pic-quiet">
                <span>
                  {tx('products.noPhoto')}
                </span>
              </div>
            )}
          </div>

          <div className="mg-teach-act">
            <div className="btn-row">
              <button
                className="btn primary"
                onClick={() => void teach(false)}
                disabled={busy || gating}
                title={teachOff ?? undefined}
              >
                {t(busy ? 'products.teach.busy' : 'products.teach.go')}
              </button>
            </div>

            {teachOff && !busy && <p className="hint">{teachOff}</p>}

            {/* TEACHING RUNS GATES, AND THE SCREEN NAMES THEM.
                Not one of these is ticked. Every one of them happens on the
                server, and this browser has no way to see which it is in — an
                unmarked step is the honest default and a ticked one it cannot
                verify would be a figure it did not derive. */}
            {busy && (
              <Thinking
                className="mg-thinking"
                title={`Teaching ${sku.trim() || 'this product'}`}
                steps={mode === 'code'
                  ? [
                    { label: 'Checking the sku id, the name and the price' },
                    { label: 'Parsing the price in string space — integer paise or a refusal' },
                    { label: 'Binding the printed code to this sku' },
                    { label: 'Publishing the price the till will read' },
                  ]
                  : [
                    { label: 'Checking the sku id, the name and the price' },
                    { label: 'Parsing the price in string space — integer paise or a refusal' },
                    { label: 'Looking at the picture: contrast, size, whether there is a region at all' },
                    { label: mode === 'mat' ? 'Locking the printed mat for real millimetres' : 'Segmenting the product off its background' },
                    { label: 'Turning what it found into a descriptor' },
                    { label: 'Comparing it against everything already taught' },
                  ]}
                foot="Nothing here is ticked. These stages happen on the counter, not in this browser, and a tick this page cannot verify would be a guess."
              />
            )}

            {result?.kind === 'refused' && (
              <Refusal
                reason={result.reason}
                detail={result.detail}
                /* SAY THAT NOTHING WAS LOST. A refused teach keeps every field
                   and the chosen picture, and a shopkeeper who does not know
                   that retypes all four of them. */
                hint={
                  'Nothing was taught and nothing was thrown away — what you typed and the picture you '
                  + 'chose are still here. Change what the counter named and press TEACH again.'
                }
                action={
                  result.canForce && (
                    <button className="btn sm" onClick={() => void teach(true)}>TEACH IT ANYWAY (no mat)</button>
                  )
                }
              />
            )}

            {result?.kind === 'ok' && (
              <Verdict
                tone={result.appearanceOnly ? 'amber' : 'green'}
                title={`TAUGHT — ${result.taught.stored.sku_id}`}
              >
                <KV k="price">{result.taught.stored.price_paise} paise = {rupees(result.taught.stored.price_paise)}</KV>
                {result.taught.stored.code && <KV k="code"><span className="mono">{result.taught.stored.code}</span></KV>}
                {result.appearanceOnly && ' No mat in this photograph, so this product has no millimetres and no size check.'}
              </Verdict>
            )}

            {/* Load-bearing honesty, kept in sight: what this mode stores, and
                which teaching path the burst gate does and does not protect. */}
            <div className="mg-fine">
              <p className="hint">
                {mode === 'code'
                  ? t('products.fine.code')
                  : mode === 'photo'
                    ? t('products.fine.photo')
                    : t('products.fine.mat')}
              </p>

              {mode !== 'code' && (
                <p className="hint">
                  {source === 'camera'
                    ? <>{tx('products.gate.camera')}</>
                    : <>{tx('products.gate.upload')}</>}
                </p>
              )}
            </div>
          </div>
        </div>
      </Card>

      {/* ZONE TWO — the catalogue, at full width so the cards get a real
          grid instead of a half-column squeeze. */}
      <Card
        title={t('products.catalogue')}
        aside={(
          <>
            {/* A RE-READ AFTER A WRITE IS STILL A FETCH. The grid below is
                true, only a moment old, so it stays on screen and the header
                says a fresh answer is on its way — replacing twenty cards with
                skeletons to report a rename is a worse lie than a stale count. */}
            {refreshing && (
              <span className="mg-refreshing">
                <Working />
                <span>reading the catalogue again</span>
              </span>
            )}
            {/* AN EM DASH WHILE IT LOADS, NOT A NOUGHT. Photographed at 1440
                mid-load this pill read "0 TAUGHT" in amber over a shop of
                four — a count this panel had not read yet, stated as a fact,
                in the colour that means the counter abstained. `off` is grey
                and says nothing, which is the truth until /shop answers. */}
            <Pill tone={loading || shopErr ? 'off' : skus.length ? 'ok' : 'amb'}>
              {loading || shopErr ? '—' : `${skus.length} taught`}
            </Pill>
          </>
        )}
      >
        {/* A REFUSED REMOVAL WHOSE PRODUCT IS NO LONGER IN THE LIST.
            The refusal normally renders on the card it names. The one case
            where there is no card is the dangerous one this refusal exists
            for: a PARTIAL removal, where the row went but the code binding or
            the published price did not. It must not fall silently through the
            gap, so it comes back to the top of the catalogue instead. */}
        {forgetErr && !skus.some((s) => s.sku_id === forgetErr.sku) && (
          <Refusal
            reason={forgetErr.reason}
            detail={forgetErr.detail}
            hint={`${forgetErr.sku} is no longer in this list, but the counter refused part of the removal. `
              + 'Check whether its code still prices anything at the till.'}
          />
        )}
        {loading ? (
          /* A SKELETON AT THE SHAPE OF WHAT IS COMING. A card is a picture
             well, a name, a price and a row of pills, so that is what waits
             here — a flat grey oblong of the right height told a shopkeeper
             nothing about what was about to land in it. */
          <div className="mg-cat" role="status" aria-label={t('products.reading')}>
            {[0, 1, 2, 3].map((i) => (
              <div className="mg-sku-skel" key={i} aria-hidden="true">
                <div className="mg-sku-skel-well"><Skeleton w={72} h={72} radius={8} /></div>
                <div className="mg-sku-skel-body">
                  <Skeleton w="78%" h={12} radius={999} />
                  <Skeleton w="42%" h={17} radius={999} />
                  <div className="mg-sku-skel-pills">
                    <Skeleton w={58} h={17} radius={999} />
                    <Skeleton w={44} h={17} radius={999} />
                  </div>
                </div>
                <div className="mg-sku-skel-acts">
                  <Skeleton w={28} h={10} radius={999} />
                  <Skeleton w={38} h={10} radius={999} />
                  <Skeleton w={46} h={10} radius={999} />
                </div>
              </div>
            ))}
          </div>
        ) : shopErr ? (
          <Refusal
            reason="The catalogue could not be read"
            detail={shopErr.reason}
            hint={shopErr.detail}
            action={<button className="btn sm" onClick={() => void loadShop(true)}>{t('products.tryAgain')}</button>}
          />
        ) : skus.length === 0 ? (
          <Empty
            title={t('products.emptyCatalogue')}
            action={(
              <button
                className="btn sm primary"
                onClick={() => { setMode('code'); skuRef.current?.focus(); }}
              >
                TEACH THE FIRST ONE
              </button>
            )}
          >
            The till can only price what this catalogue holds. Teach one product — a printed code, a
            name and a price is a complete entry, and it needs no photograph.
          </Empty>
        ) : (
          <div className="mg-cat">
            {skus.map((s) => {
              const priced = s as PricedSku;
              const marked = typeof priced.marked_paise === 'number' && priced.marked_paise > s.price_paise
                ? priced.marked_paise
                : null;
              return (
                <article className="mg-sku" key={s.sku_id}>
                  <div className="mg-sku-well">
                    {s.thumb_png ? (
                      <img src={`data:image/png;base64,${s.thumb_png}`} alt={s.name} />
                    ) : (
                      <img className="mg-qr" src={api.productQrUrl(s.sku_id)} alt={`QR for ${s.name}`} />
                    )}
                  </div>
                  <div className="mg-sku-body">
                    <div className="mg-sku-name">{s.name}</div>
                    {/* The marked price struck through when an offer is live:
                        what the sign says, then what is actually charged —
                        the same pattern the offers screen uses. */}
                    <div className="mg-sku-price">
                      {marked !== null && <s className="mg-was">{rupees(marked)}</s>}
                      <b>{rupees(s.price_paise)}</b>
                    </div>
                    <div className="mg-sku-pills">
                      {/* CAN THE CAMERA RECOGNISE THIS ONE?
                          The catalogue has always known and never said. A
                          product taught by its printed code stores ZERO
                          descriptor vectors on purpose — "nothing is measured
                          and nothing is embedded" — so holding it up to the
                          camera and expecting appearance recognition can never
                          work. Three of this shop's products were in exactly
                          that state, and the symptom was read as "recognition
                          is broken" rather than "this product has no
                          appearance". Saying which is which costs one pill. */}
                      {s.n_views > 0
                        ? <Pill tone="ok">{tn('products.pill.views', s.n_views)}</Pill>
                        : <Pill tone="amb">{t('products.codeOnly')}</Pill>}
                      {s.codes?.length ? (
                        <Pill tone="code">{tn('products.pill.codes', s.codes.length)}</Pill>
                      ) : null}
                      {s.appearance_only ? <Pill tone="amb">{t('products.noMm')}</Pill> : null}
                      {/* footprint_mm is ONE number of millimetres, not a pair.
                          Indexing it produced "NaN×NaN MM" on every measured product. */}
                      {typeof s.footprint_mm === 'number' && Number.isFinite(s.footprint_mm) ? (
                        <Pill tone="ok">{s.footprint_mm.toFixed(1)} mm</Pill>
                      ) : null}
                    </div>
                  </div>
                  {/* THE SHELF STRIP. The count the storefront sells against,
                      the floor it stops at, and what a phone may buy right
                      now — with the open orders that are holding packets,
                      because "9 on the shelf, 4 can be sold" is a question
                      until the 5 in open orders are on the same line. */}
                  <div className="mg-sku-stock">
                    {online === null ? (
                      <Skeleton w="62%" h={12} radius={999} />
                    ) : (
                      <StockOnline
                        skuId={s.sku_id}
                        figure={online.get(s.sku_id) ?? null}
                        figuresError={onlineErr}
                        onChanged={() => { void loadOnline(); }}
                      />
                    )}
                  </div>
                  <div className="mg-sku-actions">
                    <a className="mg-act" href={api.productQrUrl(s.sku_id)} target="_blank" rel="noreferrer">QR</a>
                    {/* Only offered where it can work. A product taught from
                        its code has no views to add to, and the refusal for
                        that says so — but a button that always refuses is a
                        worse answer than no button. */}
                    {s.n_views > 0 && (
                      cam.running ? (
                        <button
                          className="mg-act"
                          disabled={viewing !== null}
                          title={viewing && viewing !== s.sku_id
                            ? `A view is being added to ${viewing}. One at a time.`
                            : undefined}
                          onClick={() => void addViewFromCamera(s.sku_id)}
                        >
                          {viewing === s.sku_id ? t('products.adding') : t('products.addView')}
                        </button>
                      ) : (
                        /* A <label> ignores `disabled`, so a second picture
                           chosen mid-upload used to start a second request
                           against the same gallery. `aria-disabled` says so and
                           `addViewFrom` refuses it. */
                        <label
                          className={viewing !== null ? 'mg-act off' : 'mg-act'}
                          aria-disabled={viewing !== null || undefined}
                          title={viewing && viewing !== s.sku_id
                            ? `A view is being added to ${viewing}. One at a time.`
                            : undefined}
                        >
                          {viewing === s.sku_id ? t('products.adding') : t('products.addView')}
                          <input
                            type="file" accept="image/*" hidden
                            disabled={viewing !== null}
                            onChange={(e) => {
                              const f = e.target.files?.[0];
                              e.target.value = '';
                              if (f) void addViewFrom(s.sku_id, f);
                            }}
                          />
                        </label>
                      )
                    )}
                    {/* Offered on EVERY product, including the code-only
                        ones: a name and a price are the two things every row
                        in this catalogue has, however it was taught. */}
                    <button
                      className="mg-act"
                      aria-expanded={editing === s.sku_id}
                      onClick={() => {
                        setEditNote(null);
                        setEditing((cur) => (cur === s.sku_id ? null : s.sku_id));
                      }}
                    >
                      {t(editing === s.sku_id ? 'products.close' : 'products.edit')}
                    </button>
                    {/* A DELETE THAT SAYS IT IS HAPPENING. It used to fire and
                        look untouched until the whole catalogue came back, so
                        a slow answer read as a dead button and got a second
                        press. */}
                    <button
                      className="mg-act danger"
                      disabled={forgetting !== null}
                      title={forgetting && forgetting !== s.sku_id
                        ? `${forgetting} is being removed. One at a time.`
                        : undefined}
                      onClick={() => void forget(s.sku_id)}
                    >
                      {t(forgetting === s.sku_id ? 'products.forgetting' : 'products.forget')}
                    </button>
                  </div>
                  {editing === s.sku_id && (
                    <SkuEditor
                      sku={s}
                      onCancel={() => setEditing(null)}
                      onSaved={(r) => onEdited(s.sku_id, r)}
                    />
                  )}
                  {editNote?.sku === s.sku_id && (
                    <p className="adm-note"><b>{t('products.corrected')}</b> {editNote.text}</p>
                  )}
                  {/* THE REFUSAL LANDS ON THE PRODUCT IT IS ABOUT. A removal
                      that half-succeeded is the dangerous case — the descriptor
                      gone, the code binding still pricing — and announcing it at
                      the top of a grid of twenty made a shopkeeper guess which. */}
                  {forgetErr?.sku === s.sku_id && (
                    <div className="mg-sku-refusal">
                      <Refusal
                        reason={forgetErr.reason}
                        detail={forgetErr.detail}
                        hint="This product may be only partly removed. Check whether its code still prices anything at the till."
                      />
                    </div>
                  )}
                  {viewNote?.sku === s.sku_id && (
                    viewNote.ok ? (
                      <p className="hint"><b>{t('products.viewAdded')} </b>{viewNote.text}</p>
                    ) : (
                      <div className="mg-sku-refusal">
                        <Refusal
                          reason={viewNote.reason}
                          detail={viewNote.detail}
                          hint="Nothing was added to this product. Its taught views, its price and its name are unchanged."
                        />
                      </div>
                    )
                  )}
                </article>
              );
            })}
          </div>
        )}
        {/* The legend: every word kept, tucked behind one disclosure so the
            grid is the first thing the eye lands on. */}
        <details className="mg-legend">
          <summary>{t('products.legend')}</summary>
          {/* THE BAR IS THE SERVER'S, ASKED FOR, NOT REMEMBERED. This
              paragraph used to print 0.92 and a table of cosines beside it;
              both were measurements of a descriptor that has been retired, and
              the sentence they supported had become the opposite of the truth.
              What is printed now is whatever this counter is actually
              enforcing, and when it has not answered the page says so rather
              than filling the gap with a number. */}
          <p className="hint">{tx('products.legend.addView')}</p>
          <p className="hint">
            <b>{t('products.bar')}</b>{' '}
            {gates
              ? tx('products.legend.gates', {
                phi: gates.phi,
                appearance: gates.phi_appearance_only,
                theta: gates.theta,
              })
              : gatesLoading
                ? tx('products.legend.gates.loading')
                : tx('products.legend.gates.none', { why: gatesErr ? ` (${gatesErr.reason})` : '' })}
          </p>
          <p className="hint">{tx('products.legend.codeOnly')}</p>
          <p className="hint">{tx('products.legend.edits')}</p>
          <p className="hint">{tx('products.legend.forget')}</p>
        </details>
      </Card>
    </div>
  );
}

/**
 * The three fields of a taught product that a person decided, and can be wrong.
 *
 * ONLY WHAT CHANGED IS SENT. Absent and empty are different instructions to the
 * server — an absent key means "leave this alone" and an empty `code` means
 * "this product has no printed code" — so posting every field on every save
 * would rebind a code nobody touched, and the response would say a code
 * changed when nothing had.
 */
function SkuEditor({ sku, onSaved, onCancel }: {
  sku: api.Sku;
  onSaved: (r: admin.SkuEdited) => void;
  onCancel: () => void;
}) {
  const { t } = useT();
  // The code shown is the FIRST one bound. A product may carry more than one —
  // a multipack and a single often do — and saving this field makes whichever
  // code is in it the only one, so the count is said out loud below rather
  // than discovered afterwards.
  const firstCode = sku.codes?.[0] ?? '';
  const [name, setName] = useState(sku.name);
  const [price, setPrice] = useState(() => admin.rupeesForInput(sku.price_paise));
  const [code, setCode] = useState(firstCode);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<admin.Refusal | null>(null);

  const [history, setHistory] = useState<admin.EditEntry[] | null>(null);
  /**
   * A BROKEN CHAIN AND AN UNREADABLE CHAIN ARE DIFFERENT FACTS.
   *
   * They shared one string, so a refusal from the server was rendered as the
   * same grey warning line as "the chain does not verify" — and the refusal's
   * own NAME was dropped on the way (`r.detail || r.reason`). They are two
   * pieces of state now: a refusal is a `Refusal` with both sentences, and a
   * chain that read but did not verify is the warning it always was.
   */
  const [histErr, setHistErr] = useState<{ reason: string; detail?: string } | null>(null);
  const [chainWarn, setChainWarn] = useState<string | null>(null);
  const [histBusy, setHistBusy] = useState(false);

  const dirty = name !== sku.name
    || price !== admin.rupeesForInput(sku.price_paise)
    || code !== firstCode;

  const save = useCallback(async () => {
    setBusy(true);
    setRefusal(null);
    try {
      const fields: admin.SkuEdit = {};
      if (name !== sku.name) fields.name = name;
      // Rupees go up as the STRING that was typed. `Number(price)` here would
      // make 12.50 a float before the server ever saw it, and a float is not
      // money — the server parses in string space and refuses sub-paisa
      // precision rather than rounding it away.
      if (price !== admin.rupeesForInput(sku.price_paise)) fields.price_rupees = price.trim();
      if (code !== firstCode) fields.code = code.trim();
      const r = await admin.editSku(sku.sku_id, fields);
      if (!r.ok) { setRefusal(r); return; }
      onSaved(r);
    } finally {
      setBusy(false);
    }
  }, [name, price, code, firstCode, sku, onSaved]);

  const showHistory = useCallback(async () => {
    // Reading a chain is a fetch across the network and a verification walk at
    // the other end. It used to report neither, so a slow answer looked like a
    // button that did nothing.
    setHistBusy(true); setHistErr(null);
    try {
      const r = await admin.skuHistory(sku.sku_id);
      if (!r.ok) { setHistErr({ reason: r.reason, detail: r.detail }); return; }
      setChainWarn(r.chain.verified ? null
        : `The audit chain does not verify: ${r.chain.error ?? 'unknown break'}. `
          + 'What is below is what could still be read.');
      setHistory(r.entries);
    } finally { setHistBusy(false); }
  }, [sku.sku_id]);

  return (
    <div className="adm-edit">
      <div className="adm-row">
        <label htmlFor={`nm-${sku.sku_id}`}>{t('products.f.name')}</label>
        <input
          id={`nm-${sku.sku_id}`}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t('products.f.name.sub')}
        />
      </div>

      <div className="adm-row adm-money">
        <label htmlFor={`pr-${sku.sku_id}`}>{t('products.f.price')}</label>
        <input
          id={`pr-${sku.sku_id}`}
          value={price}
          onChange={(e) => setPrice(e.target.value)}
          inputMode="decimal"
          placeholder="12.50"
        />
      </div>

      <div className="adm-row">
        <label htmlFor={`cd-${sku.sku_id}`}>{t('products.f.code')}</label>
        <input
          id={`cd-${sku.sku_id}`}
          value={code}
          onChange={(e) => setCode(e.target.value)}
          inputMode="numeric"
          placeholder="8901063093157"
        />
      </div>

      {sku.codes && sku.codes.length > 1 && code !== firstCode && (
        <p className="adm-warn">
          This product carries {sku.codes.length} codes ({sku.codes.join(', ')}). Saving this field
          makes the one above its only code and unbinds the rest — the response will name every
          code that was dropped.
        </p>
      )}
      {code.trim() === '' && firstCode !== '' && (
        <p className="adm-warn">
          Clearing this field unbinds <span className="adm-mono">{firstCode}</span>. Scanning that
          code will then price nothing rather than this product.
        </p>
      )}

      <div className="adm-actions">
        <button
          className="btn primary sm"
          onClick={() => void save()}
          disabled={busy || !dirty}
          title={!busy && !dirty ? 'Nothing has changed yet — there is nothing to save.' : undefined}
        >
          {busy ? 'SAVING…' : 'SAVE'}
        </button>
        <button className="btn ghost sm" onClick={onCancel} disabled={busy}>{t('products.cancel')}</button>
        {!history && (
          <button className="btn sm" onClick={() => void showHistory()} disabled={busy || histBusy}>
            {histBusy ? 'READING THE CHAIN…' : 'WHAT THIS PRICE HAS BEEN'}
          </button>
        )}
      </div>

      {/* Why SAVE is off, said out loud rather than left as a grey button. */}
      {!busy && !dirty && (
        <p className="adm-hint">
          SAVE is off because nothing has changed yet. Edit the name, the price or the code above.
        </p>
      )}

      {refusal && (
        <Refusal
          reason={refusal.reason}
          detail={refusal.detail}
          hint="Nothing was changed. What you typed is still in the boxes above."
        />
      )}

      {/* The chain is being read: rows of the shape that are coming. */}
      {histBusy && (
        <div className="adm-history" role="status" aria-label={t('products.chain.reading')}>
          <SkeletonRows rows={3} cols={2} />
        </div>
      )}

      {histErr && (
        <Refusal
          reason={histErr.reason}
          detail={histErr.detail}
          hint="The price shown above is still what the till charges; only the record of how it got there could not be read."
          action={<button className="btn sm" onClick={() => void showHistory()}>{t('products.tryAgain')}</button>}
        />
      )}
      {chainWarn && <p className="adm-warn">{chainWarn}</p>}

      {history && (
        history.length === 0 ? (
          <Empty title={t('products.chain.empty')} icon={false}>
            Nothing has been changed about this product since it was taught. Change the name, the
            price or the code above and the old value and the new one are written here.
          </Empty>
        ) : (
          <div className="adm-history">
            {history.map((e) => (
              <div className="adm-history-row" key={e.hash}>
                <span className="adm-history-when">{when(e.ts)}</span>
                <span className="adm-history-what">{describe(e)}</span>
              </div>
            ))}
          </div>
        )
      )}

      <p className="adm-hint">
        The taught views, the millimetres and the photograph are not touched by this. The SKU id
        (<span className="adm-mono">{sku.sku_id}</span>) cannot change: past bills, orders and code
        bindings all point at it.
      </p>
    </div>
  );
}

/** One recorded change, in the words a shopkeeper would use. */
function describe(e: admin.EditEntry): string {
  const bits: string[] = [];
  if (e.name_after !== undefined) bits.push(`name ${e.name_before} → ${e.name_after}`);
  if (e.price_rupees_after !== undefined) {
    bits.push(`price ₹${e.price_rupees_before ?? '?'} → ₹${e.price_rupees_after}`);
  }
  if (e.codes_after !== undefined) {
    bits.push(`code ${(e.codes_before ?? []).join(', ') || 'none'} → ${(e.codes_after ?? []).join(', ') || 'none'}`);
  }
  return bits.join(' · ') || e.event;
}

function when(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function FilePick({ onPick }: { onPick: (f: File) => void }) {
  const { t } = useT();
  const ref = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={ref}
        type="file"
        accept="image/*"
        style={{ display: 'none' }}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onPick(f);
        }}
      />
      <button className="btn" onClick={() => ref.current?.click()}>{t('products.choosePicture')}</button>
    </>
  );
}
