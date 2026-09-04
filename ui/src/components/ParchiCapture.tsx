import { useCallback, useEffect, useRef, useState } from 'react';
import { useCamera } from '../hooks/useCamera';
import { Pill, Segmented } from './ui';

/**
 * A photograph of a bill: from a file, or from the camera.
 *
 * COPIED FROM THE PRODUCTS SCREEN'S CAPTURE, deliberately, and cut down. That
 * screen bursts eight frames and crops, because it is teaching an APPEARANCE
 * the counter will match against for months. A bill is read once, by a model
 * that reads text; one frame, whole, is the right capture, and the shopkeeper
 * gets to see the frozen frame before anything leaves. Sharing the code would
 * have meant sharing the burst gate, which is the wrong gate for paper.
 *
 * NOTHING LEAVES FROM HERE. `onPicked` hands the bytes to the screen; the
 * screen decides when they go, and says so beside a button. The <video> is
 * kept mounted while the still covers it so RETAKE is one press and not a
 * camera restart — the same lesson Products.tsx carries in its own comment.
 */

type Source = 'file' | 'camera';
const STAGE_AR = { w: 3, h: 4 };   // a bill is portrait; the stage says so

export function ParchiCapture({ onPicked, disabled }: {
  onPicked: (blob: Blob, filename: string) => void;
  /** While a photograph is being read: no second one may replace it. */
  disabled?: boolean;
}) {
  const cam = useCamera();
  const [source, setSource] = useState<Source>('file');
  const [starting, setStarting] = useState(false);
  const [shot, setShot] = useState<Blob | null>(null);
  const [frozen, setFrozen] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!shot) { setFrozen(null); return; }
    const url = URL.createObjectURL(shot);
    setFrozen(url);
    return () => URL.revokeObjectURL(url);
  }, [shot]);

  const grab = useCallback(async () => {
    if (!cam.running) return;
    // One whole frame at the camera's own size, JPEG at .92 — a bill's
    // figures are small type and a soft JPEG turns an 8 into a 3.
    const b = await cam.capture({ x: 0, y: 0, w: cam.frame.w, h: cam.frame.h }, 0.92);
    if (!b) return;
    setShot(b);
    onPicked(b, 'bill.jpg');
  }, [cam, onPicked]);

  return (
    <div className="pr-capture">
      <div className="pr-capture-head">
        <Segmented<Source>
          value={source}
          onChange={(s) => { setSource(s); if (s === 'file') cam.stop(); }}
          options={[
            { value: 'file', label: 'FROM A FILE', title: 'a photograph already on this device' },
            { value: 'camera', label: 'CAMERA', title: 'photograph the bill now' },
          ]}
        />
      </div>

      {source === 'file' ? (
        <div className="pr-filepick">
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            style={{ display: 'none' }}
            disabled={disabled}
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = '';
              if (f) onPicked(f, f.name);
            }}
          />
          <button
            className="btn primary"
            disabled={disabled}
            onClick={() => fileRef.current?.click()}
          >
            CHOOSE THE BILL&rsquo;S PHOTOGRAPH
          </button>
          <span className="pr-gate">
            A JPEG or PNG of the whole bill, header to total. Nothing leaves this
            browser until it is chosen; then the photograph, and only the photograph,
            goes to be read.
          </span>
        </div>
      ) : (
        <div className="stage pr-stage" style={{ aspectRatio: `${STAGE_AR.w} / ${STAGE_AR.h}` }}>
          <video ref={cam.videoRef} playsInline muted style={{ display: cam.running ? 'block' : 'none' }} />
          {shot && frozen ? (
            <>
              <img className="pr-still" src={frozen} alt="the bill, as photographed" />
              <div className="stage-bar">
                <Pill tone="code" dot>PHOTOGRAPHED</Pill>
                <span>this frame is what is read — RETAKE to go back live</span>
                <button
                  className="btn sm ghost pr-onstage"
                  disabled={disabled}
                  onClick={() => setShot(null)}
                >
                  RETAKE
                </button>
              </div>
            </>
          ) : !cam.running ? (
            <div className="camgate">
              <h3>Photograph the bill</h3>
              <p>
                Lay it flat, all of it in the frame, header to total. The camera does not
                start until you press this, and nothing leaves this browser until you
                press PHOTOGRAPH.
              </p>
              <button
                className="btn primary"
                disabled={starting || disabled}
                title={starting ? 'Waiting for the browser to hand over the camera.' : undefined}
                onClick={() => {
                  setStarting(true);
                  void cam.start().finally(() => setStarting(false));
                }}
              >
                {starting ? 'STARTING…' : 'START CAMERA'}
              </button>
              {cam.error && <p className="pr-camerr">{cam.error}</p>}
            </div>
          ) : (
            <div className="stage-bar">
              <Pill tone="off" dot>LIVE</Pill>
              <span>hold the bill flat and still</span>
              <button className="btn primary sm pr-onstage" disabled={disabled} onClick={() => void grab()}>
                PHOTOGRAPH
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
