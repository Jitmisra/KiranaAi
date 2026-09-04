import { useCallback, useEffect, useRef, useState } from 'react';
import type { Roi } from '../lib/roi';

/**
 * The camera, and the one place a frame becomes bytes.
 *
 * `capture()` is the ONLY path from the video element to the network, so the
 * counter area is applied in exactly one place and cannot be forgotten at a
 * second call site. It draws the ROI into an offscreen canvas and hands back a
 * JPEG — nothing outside `roi` is ever serialised.
 */

export interface CameraState {
  videoRef: React.RefObject<HTMLVideoElement>;
  running: boolean;
  error: string | null;
  frame: { w: number; h: number };
  start: () => Promise<boolean>;
  stop: () => void;
  capture: (roi: Roi, quality?: number) => Promise<Blob | null>;
}

export function useCamera(): CameraState {
  const videoRef = useRef<HTMLVideoElement>(null);
  const workRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  /**
   * A second START while the first is still resolving orphaned the first
   * MediaStream: `streamRef.current` was overwritten, so stop() could never
   * release it and the camera light stayed on — on a page whose own gate copy
   * says "Nothing is uploaded until you start it". Returning the in-flight
   * promise opens the device once; stopping the loser afterwards would still
   * double-prompt in Firefox.
   */
  const pendingRef = useRef<Promise<boolean> | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [frame, setFrame] = useState({ w: 1280, h: 720 });

  if (!workRef.current && typeof document !== 'undefined') {
    workRef.current = document.createElement('canvas');
  }

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    const v = videoRef.current;
    if (v) v.srcObject = null;
    setRunning(false);
  }, []);

  const start = useCallback(async (): Promise<boolean> => {
    if (pendingRef.current) return pendingRef.current;
    if (streamRef.current) return true;
    const attempt = (async (): Promise<boolean> => {
    setError(null);
    try {
      // The back camera is the one pointed at the counter. `ideal`, not
      // `exact`: a laptop has no environment camera and must still work.
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      streamRef.current = stream;
      const v = videoRef.current;
      if (!v) {
        stream.getTracks().forEach((t) => t.stop());
        return false;
      }
      v.srcObject = stream;
      await v.play();
      setFrame({ w: v.videoWidth || 1280, h: v.videoHeight || 720 });
      setRunning(true);
      return true;
    } catch (e) {
      const err = e as DOMException;
      // Name the actual obstacle. "Camera failed" sends a shopkeeper to the
      // wrong fix; "another app is using it" sends them to the right one.
      const why =
        err?.name === 'NotAllowedError'
          ? 'The browser blocked the camera. Allow camera access for this page and press START again.'
          : err?.name === 'NotFoundError'
            ? 'No camera was found on this device.'
            : err?.name === 'NotReadableError'
              ? 'Another application is holding the camera. Close it and press START again.'
              : `The camera could not start (${err?.name || 'unknown'}).`;
      setError(why);
      setRunning(false);
      return false;
    }
    })();
    pendingRef.current = attempt;
    try { return await attempt; } finally { pendingRef.current = null; }
  }, []);

  const capture = useCallback(async (roi: Roi, quality = 0.86): Promise<Blob | null> => {
    const v = videoRef.current;
    const work = workRef.current;
    if (!v || !work || !roi || roi.w < 1 || roi.h < 1) return null;
    work.width = roi.w;
    work.height = roi.h;
    const c = work.getContext('2d');
    if (!c) return null;
    c.drawImage(v, roi.x, roi.y, roi.w, roi.h, 0, 0, roi.w, roi.h);
    return new Promise((res) => work.toBlob(res, 'image/jpeg', quality));
  }, []);

  /**
   * Re-attach the stream whenever the <video> element is a different node.
   *
   * React unmounts and remounts that element whenever the route renders a
   * different branch — the pay screen replaces the camera stage entirely. The
   * MediaStream stayed live and `running` stayed true, but `srcObject` had been
   * set on a node that no longer existed, so after CANCEL the counter looked
   * armed and captured black frames: CHARGE then refused with "nothing on this
   * counter could be priced" over a packet sitting in plain view.
   *
   * Checking identity rather than assigning blindly keeps this from restarting
   * playback on every render.
   */
  useEffect(() => {
    const v = videoRef.current;
    const stream = streamRef.current;
    if (!v || !stream || v.srcObject === stream) return;
    v.srcObject = stream;
    void v.play().catch(() => { /* a re-attach that cannot autoplay is not fatal */ });
  });

  /**
   * KEEP `frame` EQUAL TO THE VIDEO'S INTRINSIC SIZE, ALWAYS.
   *
   * It was read once, at start(). A track that renegotiates its resolution —
   * a camera app switching lens, a constraint applied late, a device rotating
   * — then leaves `frame` describing a picture that no longer exists, and
   * `frame` is what every caller passes to `capture()` as its region AND what
   * the crop stage uses to work out where the frozen still sits on screen. A
   * stale pair of numbers there is a mis-crop nobody can see.
   */
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const sync = () => {
      const w = v.videoWidth, h = v.videoHeight;
      if (!w || !h) return;
      setFrame((f) => (f.w === w && f.h === h ? f : { w, h }));
    };
    sync();
    v.addEventListener('loadedmetadata', sync);
    v.addEventListener('resize', sync);
    return () => {
      v.removeEventListener('loadedmetadata', sync);
      v.removeEventListener('resize', sync);
    };
  }, [running]);

  // A page left open with a live camera keeps the light on and the battery
  // draining. Release the device when the component goes away.
  useEffect(() => stop, [stop]);

  return { videoRef, running, error, frame, start, stop, capture };
}
