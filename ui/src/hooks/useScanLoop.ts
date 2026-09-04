import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from '../lib/api';
import {
  PacketTracker, StreakTracker, boxScale, headline, POLL_MS,
  type ScanFrame, type BasketLine,
} from '../lib/counter';
import { rupees } from '../lib/money';
import type { Roi, ReadMode } from '../lib/roi';
import type { Overlay, LiveBox } from '../lib/overlay';
import { voices } from '../lib/audio';

/**
 * The loop: grab, upload, decide, show, sound.
 *
 * SINGLE FLIGHT. One request is in the air at a time. Without the guard a slow
 * frame stacks requests behind it, and the counter starts answering questions
 * about a packet that has already been taken away.
 *
 * The poll interval is the ATTEMPTS-PER-SECOND lever, and it is worth spending:
 * a failed frame costs about 104 ms server-side, so a 240 ms poll keeps up with
 * room to spare, and more looks per second is what turns a marginal code into a
 * read one while the hand is still moving.
 */

export interface ScanLoopArgs {
  camera: { running: boolean; capture: (roi: Roi, q?: number) => Promise<Blob | null> };
  roi: Roi | null;
  read: ReadMode;
  enabled: boolean;
  overlay: Overlay;
  onCommit: (items: BasketLine[]) => void;
  /**
   * Bumped ONLY when the bill is emptied.
   *
   * The trackers used to be reset in the effect body, which re-ran whenever the
   * loop re-armed — on CANCEL, on switching By code/By look, on STOP then
   * START. Each of those cleared the memory of what was already billed while
   * leaving the basket alone, so every packet still in front of the camera
   * committed a second time: x1 became x2 became x3. The mirror of the same bug
   * emptied the basket on CLEAR without resetting the trackers, so the next
   * look at an unchanged counter billed nothing at all.
   *
   * Tying the reset to THIS instead means the trackers forget exactly when, and
   * only when, the bill forgets.
   */
  billGeneration: number;
}

/**
 * What the loop lets the OUTSIDE tell it. Removing a line is the operator
 * overruling the camera, and the loop has to hear about it — otherwise the
 * packet comes straight back the next time it leaves view and returns.
 */
export interface ScanControls {
  suppress: (skuId: string) => void;
}

export interface ScanStatus {
  symbols: number;
  distinct: number;
  untaught: number;
  names: string[];
  elapsedMs: number | null;
  uploadedPx: string;
  refusal: string | null;
  attempts: number;
  /**
   * Codes that are IN VIEW but still inside their re-commit cooldown.
   *
   * Without this the counter looks broken: the shopkeeper holds the packet up,
   * the box goes green, and nothing joins the bill. A refusal has to be visible
   * or it is indistinguishable from a failure.
   */
  cooling: Array<{ code: string; msLeft: number }>;
}

const EMPTY: ScanStatus = {
  symbols: 0, distinct: 0, untaught: 0, names: [],
  elapsedMs: null, uploadedPx: '—', refusal: null, attempts: 0, cooling: [],
};

export function useScanLoop(args: ScanLoopArgs): ScanStatus & ScanControls {
  const { enabled, overlay, read } = args;
  const [status, setStatus] = useState<ScanStatus>(EMPTY);

  const busy = useRef(false);
  const packets = useRef(new PacketTracker());
  const streak = useRef(new StreakTracker());
  // The interval closes over ONE object that every render refreshes, so the
  // loop always reads current props without being torn down and rebuilt.
  const live = useRef(args);
  live.current = args;

  // Forget what has been billed exactly when the bill itself is emptied.
  useEffect(() => {
    packets.current.reset();
    streak.current.reset();
  }, [args.billGeneration]);

  useEffect(() => {
    if (!enabled || !args.camera.running) return;

    // `armed` is checked AFTER the await: a scan already in flight when the
    // camera stops would otherwise land and add a line to a bill the operator
    // has already walked away from. (Named to avoid the `live` props ref above.)
    let armed = true;
    const tick = async (): Promise<void> => {
      const { roi, read: mode, camera, overlay: ov, onCommit } = live.current;
      if (busy.current || !roi || !camera.running) return;
      busy.current = true;
      try {
        const blob = await camera.capture(roi);
        if (!blob) return;
        const px = `${roi.w}×${roi.h} = ${(roi.w * roi.h / 1e6).toFixed(2)} MP (${Math.round(blob.size / 1024)} KB)`;
        const d = await api.recognise(blob, mode === 'code' ? 'basket' : 'plain_photo');
        if (!armed) return;

        if (!d.ok) {
          streak.current.reset();
          ov.setBoxes([], { x: roi.x, y: roi.y });
          setStatus((s) => ({ ...EMPTY, uploadedPx: px, refusal: d.reason, attempts: s.attempts + 1 }));
          return;
        }
        const frame = d as unknown as ScanFrame;
        if (mode === 'code') {
          commitCodes(frame, roi, ov, packets.current, onCommit);
        } else {
          commitLook(frame, roi, ov, streak.current, onCommit);
        }
        const h = headline(frame);
        const now = Date.now();
        const cooling = Array.from(
          new Map(
            (frame.items ?? [])
              .filter((i) => i.sku_id)
              .map((i) => [String(i.code ?? ''), packets.current.cooldownLeft(String(i.code ?? ''), now)] as const)
              .filter(([, left]) => left > 0)
              .map(([code, msLeft]) => [code, { code, msLeft }] as const),
          ).values(),
        );
        setStatus((s) => ({
          symbols: h.symbols,
          distinct: h.distinct,
          untaught: h.untaught,
          names: h.named.map((i) => i.name || i.sku_id || '?'),
          elapsedMs: frame.elapsed_ms ?? null,
          uploadedPx: px,
          refusal: null,
          attempts: s.attempts + 1,
          cooling,
        }));
      } finally {
        busy.current = false;
      }
    };

    const id = setInterval(() => void tick(), POLL_MS);
    void tick();
    return () => {
      armed = false;
      clearInterval(id);
      overlay.clear();
    };
    // Re-arm only when the instrument or the camera changes. `roi` and the
    // callbacks are read through `live`, so moving the rectangle must not
    // restart the loop and reset what is already on the bill.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, args.camera.running, read]);

  // Stable across renders: the tracker instance is a ref, so this closure
  // never needs rebuilding and callers can pass it straight to a button.
  const suppress = useCallback((skuId: string) => {
    packets.current.suppress(skuId);
  }, []);

  return { ...status, suppress };
}

/* ------------------------------------------------------------------------- */

function liveBoxes(frame: ScanFrame, sx: number, sy: number): LiveBox[] {
  return (frame.items || []).map((it) => ({
    box: it.box || [0, 0, 0, 0],
    sx,
    sy,
    named: !!it.sku_id,
    label: it.sku_id ? `${it.name}  ${rupees(it.price_paise ?? 0)}` : `I DO NOT KNOW — ${it.reason ?? ''}`,
  }));
}

function toLine(it: { sku_id?: string | null; name?: string | null; price_paise?: number | null; gate?: string | null }): BasketLine {
  return {
    sku_id: it.sku_id as string,
    name: it.name || (it.sku_id as string),
    price_paise: it.price_paise ?? 0,
    qty: 1,
    by: it.gate ?? undefined,
  };
}

/** Code mode: every symbol in the frame, each committed on its FIRST clean read. */
function commitCodes(
  frame: ScanFrame,
  roi: Roi,
  overlay: Overlay,
  tracker: PacketTracker,
  onCommit: ScanLoopArgs['onCommit'],
): void {
  const { sx, sy } = boxScale(frame, roi);
  overlay.setBoxes(liveBoxes(frame, sx, sy), { x: roi.x, y: roi.y });

  const fresh = tracker.observe(frame.items || []);
  let namedIdx = 0;
  const lines: BasketLine[] = [];
  for (const it of fresh) {
    if (it.sku_id) {
      overlay.snap(it.box, 'named', sx, sy);
      voices.named(namedIdx++);
      lines.push(toLine(it));
    } else {
      overlay.snap(it.box, 'abstain', sx, sy);
      voices.abstain();
    }
  }
  if (lines.length) onCommit(lines);
}

/** Appearance mode: one subject, committed only after STABLE_N steady frames. */
function commitLook(
  frame: ScanFrame,
  roi: Roi,
  overlay: Overlay,
  tracker: StreakTracker,
  onCommit: ScanLoopArgs['onCommit'],
): void {
  const it = (frame.items || [])[0] ?? null;
  const { sx, sy } = boxScale(frame, roi);
  const region = it?.measured?.region_px ?? null;
  overlay.setBoxes(it ? liveBoxes({ items: [{ ...it, box: region }] }, sx, sy) : [], { x: roi.x, y: roi.y });
  if (!it) {
    tracker.reset();
    return;
  }
  if (it.sku_id) {
    if (tracker.observe(it.sku_id)) {
      overlay.snap(region, 'named', sx, sy);
      voices.named(0);
      onCommit([toLine(it)]);
    }
    return;
  }
  // An abstention is announced once, not once per frame — a shopkeeper who
  // hears the refusal buzz four times a second stops hearing it at all.
  if (!tracker.abstained) {
    overlay.snap(region, 'abstain', sx, sy);
    voices.abstain();
  }
  tracker.observe(null);
}
