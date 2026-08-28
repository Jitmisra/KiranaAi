# GAWAAH — the counter PWA

The phone client. Five static files, no build step, no framework, no dependency
graph. `python3 -m http.server` in this directory serves it.

```
web/index.html     markup + CSP.       no inline script
web/style.css      chrome palette, hatched amber lines
web/app.js         pure core + browser shell, one ES module
web/selftest.mjs   node-runnable test of the pure core
web/vendor/opencv.js   NOT IN THE REPO — see "Vendoring OpenCV" below
```

Run the tests:

```
cd /Users/agnik/Desktop/razor && node web/selftest.mjs
```

---

## What the phone does, and what it refuses to do

The phone does **geometry only**.

It finds four ArUco markers, computes a homography to the fixed 840×1188
rectified buffer, adjudicates a mat lock against the same three gates as
`gawaah/takhti.py`, applies the mat mask **at frame grab**, paints rupee glyphs
back through H⁻¹ so they lie down on the counter in perspective, and keeps a
running total in integer paise.

It does not classify, it does not embed, it does not hold a secret, it does not
mint, and it cannot turn its own chrome green.

### Invariant 3 — zero model weights

There is no `.onnx`, no `.tflite`, no `onnxruntime`, no `transformers.js` and no
inference runtime in this directory or reachable from it. `selftest.mjs`
enforces this by scanning `app.js`, `index.html` and `style.css` for every one
of those tokens and failing the run if any appears. The recognition decision
belongs to the brain over the LAN; the phone only ever ships the rectified crop
and the geometry that produced it.

Consequence, which is the point: the cold load is the shell plus OpenCV and
nothing else. `selftest.mjs` prints the measured shell size on every run.

### Invariant 4 — the rectified crop is the only buffer that survives

`frameGrabPolicy(lock)` is a pure function with exactly two possible answers:
`'rectified_mat_crop'` or `'nothing'`. There is no third answer and no input
that produces one; the self-test fuzzes 20,000 malformed lock objects to say so.

Two further mechanical guards:

- `assertRectifiedOnly(payload)` runs on the send path — not at review time —
  and throws if an outbound message carries any of `raw`, `rawFrame`, `frame`,
  `fullFrame`, `camera`, `videoFrame`, `unmasked`, `photo`, `snapshot`, or a
  `crop` that is not tagged `cropKind: 'rectified_mat_crop'`.
- The raw preview canvas is **scrimmed outside the mat quad** the instant a lock
  is achieved, so the invariant is visible rather than merely claimed. That pane
  is a display surface: nothing calls `toDataURL` or `toBlob` on it, and it is
  not an egress path.

The split preview exists so a viewer can watch the mask being applied: left is
the (masked) raw feed, right is the 840×1188 crop that is the only thing that
leaves the device.

### Invariant 2 — GREEN needs all four, and the client can only veto

`greenGate(state, verdict)` requires, in order:

1. the HMAC-SHA256 signature over the **raw bytes** verified — by `paisa`,
   before any JSON parsing, which is why it is a boolean arriving here and not
   a computation done here;
2. the event in `{payment_link.paid, payment.captured, qr_code.credited}`;
3. `notes.session_id` equal to **this** session's open intent;
4. `amount` exactly equal to `intent.amount_paise` — integer compare, no
   tolerance.

Plus `verdict.green === true`, which is `paisa`'s own adjudication. The client
therefore **cannot grant green, only withhold it**. `DONE` moves to
`AWAITING_SETTLEMENT` and authorises nothing. There is no timer, no mint path
and no render path into `State.PAID`; the self-test drives 40,000 random
non-webhook actions and asserts `PAID` is never reached and `authorisedPaise`
never moves off zero.

### Invariant 6 — no forgery primitives

Nothing here constructs, encodes or regenerates a UPI payload. There is no QR
encoder, no `upi://` string, no `pa=`/`pn=` assembly, no VPA concatenation. The
self-test scans for all of them. A QR shown to a customer is an image supplied
by `paisa`; the phone renders what it is handed and never composes one.

### Invariant 7 — abstain rather than guess

- An item the brain cannot name arrives with `pricePaise: null` → **AMBER**,
  rendered hatched with the word `EXCLUDED`, and **excluded from the total**.
  It never shows a guessed name and never shows a price.
- An exit crossing that cannot be attributed to a track → `FROZEN_TOTAL`, amber
  chrome, "check the counter", and every billing action refused until a human
  acknowledges. It is an abstention, so it is amber.
- Mat lost, brain lost, offline, degraded → grey or amber freeze. **Never red.**
  `RED` is used by exactly one state, `AMOUNT_MISMATCH`, because that is the
  only genuine contradiction rather than a staleness. The self-test asserts the
  red set and the green set are each exactly one state.
- An unknown WebSocket message type from the brain is reported on screen and
  ignored, not guessed at.

---

## Vendoring OpenCV

The app loads OpenCV from **`./vendor/opencv.js`**, a local path. It is not in
this repo and this module does not download it. Fetch it once, out of band:

```
npm pack @techstark/opencv-js@4.11.0-release.1
# then copy dist/opencv.js to web/vendor/opencv.js
```

### The pin is 4.11.0-release.1, and the reason is bytes

| build | single-file JS | note |
|---|---:|---|
| `@techstark/opencv-js@4.11.0-release.1` | **11,386,540 bytes** | pinned |
| `@techstark/opencv-js@5.0.0` | 13,298,869 bytes | refused |

5.0.0 is **1,912,329 bytes — 1.91 MB — heavier for no benefit here.** Every
symbol this app touches is present in 4.11: `aruco_ArucoDetector`,
`getPredefinedDictionary`, `detectMarkers`, `findHomography`,
`warpPerspective`. Nothing in the counter's hot path is a 5.x-only API. On a
shop phone over a shared connection that 1.91 MB is the difference between a
cold load that finishes and one that gets abandoned, so the version is pinned
exactly — not `^4.11`, not `latest`. `selftest.mjs` recomputes the delta from
the two byte counts and fails if this file misstates it.

### If it is absent, the app abstains loudly

`loadOpenCV()` attaches an `onerror` handler. A missing or unreadable
`./vendor/opencv.js` resolves to `{ok: false, reason: 'opencv_absent_geometry_unavailable'}`.
The app then:

- prints `OpenCV absent (…) — geometry unavailable, refusing to lock` in the
  status line,
- keeps the camera and the UI running,
- **never reports a mat lock**, so `frameGrabPolicy` returns `'nothing'` and no
  crop is produced or sent.

It does not fall back to a CDN. The CSP in `index.html` is `default-src 'none'`
with `script-src 'self'`, so a CDN fallback would be blocked even if someone
added one.

### One landmine, already avoided

In `@techstark/opencv-js` 4.x, `Mat.clone()` **aliases the source buffer**. If
the empty-mat reference is ever cloned, `absdiff` returns zeros and no crossing
ever fires — a silent failure that looks like a dead detector. Use
`new cv.Mat(); src.copyTo(dst)` instead. `app.js` contains no `.clone(` call and
`selftest.mjs` fails the run if one appears.

---

## Architecture of `app.js`

Everything above `boot()` is pure and has no DOM reference, which is why the
whole geometry and money core is testable under node. `boot()` and below is the
browser shell and is invoked only when `document` exists.

`selftest.mjs` imports `app.js` through a `data:` URL, because `web/` has no
`package.json` to declare `"type": "module"` and this module does not own one.

### The pure core

**Money** — integer paise, no exceptions. `paise()` rejects float, bool, string,
`null`, `NaN` and anything past `Number.MAX_SAFE_INTEGER`. `fromRupeesStr`
parses a *string* (`'214.50' → 21450`) because `214.50` is already lossy before
you can inspect it. The single division in the whole money block is
`(p - r) / 100` where `p - r` is an exact multiple of 100, so IEEE-754 returns
the exact integer. `selftest.mjs` re-lints that block by source scan — no
`parseFloat`, no `toFixed`, no `Math.round`, no decimal literal, exactly one
division — mirroring what `tools/lint_no_float.py` does to the Python money
path.

**Geometry** — `homographyFrom4Points` is an exact 4-point DLT solved by
Gaussian elimination with partial pivoting. It exists in pure JS so the geometry
is testable without a browser and so the app still has a homography if OpenCV is
absent. It is checked against a vector produced by running the real
`gawaah.takhti.PlaneEngine` over `tests.test_plane.synth_frame(px_per_mm=4.0,
tilt=(3.0, -2.0), seed=7)`; the two agree to well under a millipixel across the
frame. `perspIndex` and `scaleError` mirror `PlaneEngine` exactly, including the
part that matters: **scale error is measured on the rectified plane**, not in
the raw frame, so an honest 2° tilt is not misreported as a 2.7% scale failure.

Scale is `2·√2 ≈ 2.8284 px/mm` (840/297 and 1188/420), not the 2 px/mm the PRD
states. The self-test asserts it is not 2.

**Glyph projection** — a glyph anchored at a point on the mat is painted through
the **local affine Jacobian** of H⁻¹ at that point:

```
u = (h₀x + h₁y + h₂)/w,  w = h₆x + h₇y + h₈
∂u/∂x = (h₀ − u·h₆)/w    ∂u/∂y = (h₁ − u·h₇)/w
∂v/∂x = (h₃ − v·h₆)/w    ∂v/∂y = (h₄ − v·h₇)/w
```

`glyphTransform` folds the mm→buffer scaling in and returns `{a,b,c,d,e,f}` for
`ctx.setTransform`, so callers work in millimetres and the text lies down on the
wood in perspective instead of floating flat over it. The self-test checks the
analytic Jacobian against central finite differences, checks the glyph origin
lands exactly on the projected mat point, checks a fronto-parallel view yields a
pure uniform scale with zero shear, and checks a tilted view produces a real
near/far scale gradient.

Canvas 2D has no projective transform, so this is a first-order approximation
over the glyph's own footprint. The self-test measures the deviation from the
exactly-projected quad and holds it under one pixel at 44×20 mm.

**Reducer** — `reduce(state, action)` mirrors `gawaah/session.py`: the same
fourteen states, the same reason codes, the same freeze/refuse structure. It is
pure and never mutates its input (asserted against a deep-frozen state). The
total is **recomputed from the committed lines every time** — never a counter
that can drift.

### The browser shell

- `getUserMedia` with `facingMode: environment`, driven by
  `requestVideoFrameCallback` with a `requestAnimationFrame` fallback, gated to
  30 fps by `shouldRenderFrame` (60 Hz in → exactly 30 admitted).
- Per-frame time is sampled into a rolling window; when p95 exceeds 250 ms the
  session goes `DEGRADED` and auto-commit is disabled until the shopkeeper taps.
- Tap-to-revert works two ways: on the line list, and on the projected glyph
  quad in the raw pane via `hitTestGlyph`, which hit-tests the *perspective*
  quad, not a screen-space rectangle.
- WebSocket to `ws://localhost:8787` with exponential backoff (250 ms → 8 s cap,
  full jitter in [0.5, 1.0] of the window) and a bounded 512-entry outbox that
  drops the oldest and says so rather than growing without limit. While
  disconnected the AMBER PENDING banner shows the queue depth and states that
  nothing is authorised.

### Wire protocol expected from the brain

Inbound, JSON, one object per frame:

```
{type:'placement', itemId, name|null, pricePaise|null, centreMm:[x,y]}
{type:'price',     itemId, pricePaise}
{type:'exit',      itemId|null, tap?}
{type:'revert',    itemId}
{type:'verdict',   verdict:{eventId, event, sessionId, amountPaise, green, signatureValid}}
```

Outbound: `{type:'done', sessionId, amountPaise}`, plus any crop message, which
must carry `cropKind: 'rectified_mat_crop'` or `assertRectifiedOnly` throws.

`pricePaise: null` is the amber signal and is the correct thing for the brain to
send when it is not sure. Unknown `type` values are shown on screen and ignored.

**This schema is this module's assumption, not a negotiated contract.** The
brain is built by another module; if its wire format differs, `onBrainMessage`
is the single function to change.

---

## What the self-test actually covers

`node web/selftest.mjs` runs **173 assertions in 18 groups** and exits non-zero
on any failure. Beyond the pure core it boots the browser shell three times
against a minimal DOM stub — a fake `document`, `canvas` 2D context,
`getUserMedia`, `requestAnimationFrame`, `WebSocket` and `setTimeout` — so
`boot()`, `render()`, `onFrame()` and `onBrainMessage()` are **executed, not
merely reviewed**:

- **shell A** — `./vendor/opencv.js` absent: asserts the app abstains with
  `opencv_absent_geometry_unavailable`, never claims a lock, retains no crop,
  and refuses a placement that arrives without a mat lock.
- **shell B** — OpenCV stubbed so `detectMarkers` returns the four marker quads
  implied by the Python vector: asserts the lock, asserts `warpPerspective` is
  called at exactly 840×1188, drives placements/exits/reverts over the wire,
  asserts the amber line stays out of the DOM total, taps the **projected glyph
  quad** in frame pixels and watches the right line revert, and checks that a
  foreign session, an unsigned verdict and a wrong amount each fail to turn the
  chrome green.
- **shell C** — the green path end to end: `DONE` → intent on the wire → a fully
  valid verdict → `chrome-green`; then socket loss → AMBER PENDING banner and a
  scheduled retry; then mat loss mid-session → lock dropped and crop dropped.

Run it three times and the output is byte-identical except
`shell_first_backoff_ms`, which is the real `Math.random()` reconnect jitter and
is asserted by range rather than by value.

## Honest limits

- **No real browser ran this.** The DOM stub exercises the shell's logic and
  wiring, but it is a stub: it cannot tell you that `getUserMedia` negotiates
  the rear camera on a given handset, that `requestVideoFrameCallback` fires at
  the rate the driver claims, that `ctx.setTransform` renders the glyph legibly,
  or that the loop holds 30fps on real hardware. Those are device measurements
  and none of them have been taken. **No fps or latency figure in this module is
  from real hardware** — the `p95` shown in the UI is computed at runtime from
  `performance.now()`, and the DEGRADED gate is tested only against synthetic
  samples.
- **OpenCV is not vendored here.** `web/vendor/opencv.js` is absent by design —
  no network downloads. So the real wasm never ran: `makeDetector` is written
  against the `@techstark` 4.11 embind surface and verified only against a stub
  that mimics it. If the real build's `aruco_ArucoDetector` constructor
  signature or `data32F` layout differs, that function is where it will break.
  Everything downstream of detection — the homography, the gates, the mask, the
  glyph maths — is real code and is fully tested.
- **The two OpenCV byte figures are quoted from the build specification**, not
  measured here, for the same reason. The 1,912,329-byte delta between them *is*
  computed by the self-test from those two figures.
- **The wire protocol is assumed, not agreed.** The brain is another module. If
  its message shapes differ, `onBrainMessage` is the one function to change.
- `perspToDeg` inherits the honesty note from `takhti.py`: `PERSP_K` absorbs
  focal length, so degrees are calibrated for the synthetic rig only. The UI
  shows `~N°` and the gate is on the dimensionless index, never on the angle.
- No service worker, no offline app-shell cache, no manifest yet. "PWA" here
  means a phone-shaped single-page client; installability is not implemented.
- No audio, no Hindi voice, no wake lock. Those are named in the PRD and are not
  in this module.
