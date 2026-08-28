# GAWAAH — ALL SIX, RESCUED
### Revised specification · 34 agents · 15 rescue attempts × 3 independent angles · adversarial verification

> **Result: zero features came back dead.** All five cuts returned `PARTIALLY_RESCUED` or `RESCUED`. The verifier killed 1 of 15 variants. Every rescue works by *deleting a hard problem*, not by solving it.

---

## 1. THE VERDICT TABLE

| Feature | Verdict | The new mechanism | Claimed | **Verified** | New prop? |
|---|---|---|---|---|---|
| **KAMPAN** → SAAF | PARTIAL | Move the shake from the camera to the **subject** | 1.25d | **0.5d** · HIGH | **No** |
| **PAKKA** → CHILLA | PARTIAL | Match on **amount + time**, never the reference string | 2d | **1.25d** · HIGH | Yes — customer's phone |
| **PEEL** → IDENT | RESCUED | **Delete the QR-ness entirely.** Pixel diff, no QR library | 2d | **1.5d** · MED | Yes — a sticker |
| **MUDRA** | PARTIAL | Hand as an **occluder**, QR on the mat | 2d | **1.5d** · MED | **No** |
| **KHATA LIFT** | PARTIAL | Only **digits**, never names | 2.5d | **4–5.5d** · MED | **Yes** — the ledger |

**Total if you build all five: ~5.25 days verified**, on top of a P0 core that doesn't exist yet, with 7 days left. KHATA LIFT alone is 4–5.5 and is the only one that genuinely breaks the one-prop rule.

---

## 2. WHAT CHANGED — the five insights

### KAMPAN: the original premise was dead on this rig and nobody noticed
KAMPAN says *"your shaking hand is the sensor."* **But the phone is clamped to a gooseneck. There is no hand tremor.** The whole feature was impossible on its own rig and three prior documents missed it.

The rescue: **move the shake from the camera to the subject.** Enrollment already nudges and rotates the packet — `part4.md §7.5` literally prompts *"thoda ghumaiye"* to capture 8 crops. Same sub-pixel sampling diversity, zero new gesture, zero new prop.

And the consumer changed too: **from an OCR model to the shopkeeper's eyes.** Forced, not chosen — Tesseract's docs want ≥300 DPI (11.81 px/mm); this rig delivers 51–72 DPI. So it doesn't read the MRP. It makes the MRP *readable by a human*, next to the keypad he's already using.

### MUDRA: MediaPipe pinch is structurally illegal here
`hand_landmarker.task` is **7,819,105 bytes** — **1.63× the entire 4.8 MB cold-load budget**, in a rig whose stated invariant is *zero model weights in the browser*. Pinch isn't expensive, it's **forbidden**.

So the hand is read as an **occluder**, not a hand — by analogy to astronomical occultation photometry and industrial shadowgraph metrology, where you characterise a body by what it hides from a calibrated field. Measured solidity on real hand photos: **fist 0.73 · open palm 0.92 · goods 0.96–1.00.** A comfortable dead band, **0.195 ms/frame, zero bytes downloaded, no model.**

And the palm died for a *better* reason than the one we had: **curvature, not contrast** — `ecH` fails at exactly the same 0.12 as `ecM`, so error correction provably cannot save it. Deeper still: *"There is no projector in this rig. The palm was never a surface. It was a picture of a surface."*

### PAKKA: the reference string is not an ML problem, it's a photon problem
A UPI reference line is 12sp text. Digit stroke ≈ **0.19 mm**. At the rig's true scale that is **0.54 px — below Nyquist by ~4×.** No model recovers it. No super-resolution recovers it (2× gets you to 1.1 px). **It is not hard to read; it is not present in the signal.**

The hero *amount* is 40sp → **12.6 px**. So: match on amount + timestamp, exactly as seeded, but for a far stronger reason than "the ids might differ."

*(Also caught: the brief's "2 px/mm" is arithmetically wrong — 840/297 = 2.8283, exactly 2√2.)*

### PEEL: delete the QR-ness entirely
Ships with **no QR library in the browser at all** — no generator, no decoder, no module grid. Just `findTransformECC` re-registration against the stored enrolment crop → `absdiff` → one scalar: ignited-pixel fraction.

`findTransformECC` is the whole feature, and it was measured: **without ECC, a 1 px registration error pushes a genuine unchanged sticker to 16.1% ignited, and 3 px to 38.5%** — the naive diff is a false-accusation machine. **With ECC, the same cases fall to 0.4%.**

### KHATA LIFT: only digits, never names
Handwritten Devanagari *words* are the hard problem; the shopkeeper already knows who each row is. So: detect rows, read digits, tap a row, pick from contacts. And the enrolment is the best part — **flip the mat over, he writes 0–9 in printed boxes with his own pen, three passes, ~40 seconds.** Fixed-coordinate cropping, no detection, no model, no failure mode. *"He does not train it. He writes his numbers once."*

---

## 3. THE KILL GATES — run these before writing any feature code

Every rescue shipped with a cheap pre-build test. **This is the most valuable output of the run.**

| Feature | Gate | Time | Kill if |
|---|---|---|---|
| **KAMPAN** | MTF50 in cycles/mm from a slanted ArUco edge — 1 frame vs 20-frame stack | **1 hour** | Improvement < 10% → the ISP already killed the high frequencies; every number is void |
| **PAKKA** | `await new ImageCapture(track).getPhotoCapabilities()` → read `imageWidth.max` | **5 min** | < 2500 → the amount never exceeds ~13 px cap and the date never exists. No clever recovery |
| **KHATA** | Best available still width on the target phone | **5 min** | ≤ 1280 → do not write a line of feature code |
| **MUDRA** | Browser console: is `cv.convexHull` actually in the shipped WASM? | **2 min** | Missing → fall back to compactness-only |
| **PEEL** | 120-photo held-out set, genuine-sticker ignited fraction | day 1 | p95 > 3%, or any overlap with the tampered distribution → it can only falsely accuse a shopkeeper |

**Run the three 5-minute gates first.** They cost 15 minutes total and can delete two features before you spend a day on either.

---

## 4. ONE FACT WORTH KNOWING REGARDLESS

**UPI Collect is deprecated, effective 28 Feb 2026 per NPCI** — six months ago. It killed an elegant alternative design (customer lays their phone on the mat, we read their VPA, gesture fires a collect request). Worth knowing before anyone proposes a collect-based flow.

---



---

# PART II — INTEGRATED SIX-CAPABILITY DESIGN

## GAWAAH — Integrated Six-Capability Design

*"It bills what leaves the shelf." One mat, one plane, one money path, six observers.*

---

## 0. The six capabilities

| # | Name | One line |
|---|---|---|
| 1 | **CORE** | Bills goods that cross the exit edge: absdiff → contour → crossing predicate → MobileCLIP identity → basket → Payment Link → webhook-verified green. |
| 2 | **MUDRA** | An open palm over the mat reveals the payment target; a fist retracts it. |
| 3 | **KHATA LIFT** | The paper udhaar ledger, laid on the mat, sorts itself into open vs settled and collects. |
| 4 | **SAAF** (ex-KAMPAN) | Saturation-guarded sharpness gate on every enrollment burst, so one bad capture cannot permanently poison a gallery entry. |
| 5 | **PAKKA-PARCHI** | A customer's "Payment Successful" screen is checked against what the ledger is owed. |
| 6 | **PEEL / IDENT** | A counter payment sticker is verified against the destination the shopkeeper enrolled. |

Five are rescued features at reduced scope; CORE is the existing P0 loop. The integration argument below is what makes the set coherent rather than a pile.

---

## 1. Why six half-working features can compose safely

The instinct is that six features at 60–90% reliability multiply into a product at 10%. That is only true if each feature can independently reach the money. In this design none of them can.

**Every feature is an observer. Only `paisa` is an actor. Only the webhook is a verdict.**

- There is exactly **one mint function**, `paisa.mint(session_id, amount_paise, reason_code)`, which re-runs the crossing predicate server-side. MUDRA does not mint — it *reveals* a target CORE already minted. KHATA mints only through the same function with its own server-side predicate. Nothing else calls it.
- There is exactly **one green rule**, unchanged: valid HMAC **and** event in the green set **and** `notes.session_id` matches an open intent **and** `amount == intent.amount_paise` exactly. **No feature may produce green.** MUDRA reveals, PAKKA corroborates, KHATA proposes, PEEL warns, SAAF selects — none of them turn a light green.
- There is exactly **one uncertain state**, ROKO (the hold square). Every feature's AMBER routes there: goods do not cross the exit edge, the session stays open, the ring flips green when and only when the webhook lands.

So the number of *money outcomes* is invariant under adding features. Six features multiply the number of ways the screen can say "I don't know" — which is the product's thesis, not its failure mode — and do not multiply the number of ways money can move. That asymmetry is the entire reason this ships.

---

## 2. Six invariants (violating any one is a stop-ship)

1. **No feature emits GREEN.** Green comes from a signature-verified webhook, in every state, always.
2. **No feature emits RED unless GAWAAH minted the target, the mirror is fresh (<60 s), and the evidence is unambiguous.** Three of six can never emit RED at all (SAAF, MUDRA, KHATA).
3. **Every mint goes through `paisa.mint()`, which re-validates server-side.** The browser never holds a key and never asserts an amount.
4. **The only buffer that survives a frame-grab call is the masked rectified mat crop** — for *every* capture rung, including stills. One masked-capture function, used by all six.
5. **Gallery writes happen only in ENROLL.** No live session may add or mutate a gallery row.
6. **Capture rung ≥1 is forbidden while a basket is open and unsettled.** No feature may interrupt the money loop to take a photograph.

---

## 3. P0 / P1 feature table

| Capability | Tier | Ships as | New browser bytes | Brain cost | Can emit |
|---|---|---|---|---|---|
| CORE crossing + mint + green | **P0** | As built (+ state-machine refactor) | 0 | MobileCLIP @0.5 Hz | GREEN, AMBER, RED |
| CORE identity gallery | **P0** | 24 SKUs × 8 crops, namespaced | 0 | shared | — |
| CHILLAR paise nonce | **P0** | 2 h in `paisa` | 0 | 0 | — |
| ROKO hold state | **P0** | Shared session state + painter | 0 | 0 | AMBER |
| SAAF frame gate | **P0** | Saturation guard + vLap ranking | 0 | 0 | — |
| MUDRA reveal (palm/fist) | **P0** | Reveals a pre-minted target | ~12 KB (qrcode-generator) | 0 | — |
| MUDRA gesture→cancel | **P1** | Behind existing hold-to-confirm | 0 | 0 | — |
| PEEL payload pinning | **P0** | `cv.QRCodeDetector`, already in bundle | **0** | 0 | RED (gated), GREY, AMBER |
| PEEL module-grid overlay | **P1** | Decoration only, never a detector | 0 | 0 | — |
| PAKKA Tier 0 (ledger) | **P0** | Already built | 0 | 0 | GREEN via webhook |
| PAKKA Tier 1 (screen class) | **P0** | ~10 gallery rows, non-billable | 0 | MobileCLIP, quiescent | AMBER |
| PAKKA Tier 2 (amount verify) | **P1**, gated | Template NCC at rung 2 | ~40 KB templates | 0 | AMBER only |
| KHATA rows + name mask + strike triage | **P0** | Rung 2, 1440p, gated on px/row | 0 | 0 | — |
| KHATA amount → editable pre-fill | **P1**, likely cut | Free decode, never asserted | 0 | ~2 MB digit CNN | — |
| KHATA strike-as-commit watcher | **P1** | 2.5 s watchdog + tap fallback | 0 | 0 | — |
| SAAF MRP read | **CUT** | Dead on optics (see §11) | — | — | — |
| Contact Picker | **CUT** | Chrome-Android-only; adds third-party PII | — | — | — |

---

## 4. The shared substrate

Six features, one stack. Anything below that appears twice in the codebase is an integration bug.

### 4.1 One capture ladder (`CaptureBroker`)

The features disagree about resolution, not about camera. Three rungs, one implementation, **never concurrent**:

| Rung | Mode | Delivered px/mm on the mat | Consumers |
|---|---|---|---|
| **0** | 720p continuous video | ~1.8–2.2 optical (buffer nominal 2.83) | CORE, MUDRA, PEEL close-hold |
| **1** | `applyConstraints({width:{ideal:1920}})`, N frames, revert | ~2.7–3.6 | SAAF enrollment bursts |
| **2** | `ImageCapture.takePhoto()`, one frame | measured, target ≥4.0 | KHATA, PAKKA, PEEL flat-on-mat |

Rules, all enforced in the broker rather than in feature code:

- Use `ideal` only. `min`/`exact` throw `OverconstrainedError` rather than degrading.
- **Never trust `getPhotoCapabilities().imageWidth.max`.** Call `takePhoto()`, decode the blob, measure its actual width. Capability advertisement and delivery diverge on Android, and the documented polyfill returns video resolution.
- **`takePhoto()` mutes the video track** (W3C: devices *may* stop streaming, reconfigure, resume; *should* fire `onmute`/`onunmute`). On resume, AE/AWB reconverge. This invalidates `REF_EMPTY_MAT` and throws spurious contours into the CORE loop. The broker therefore: parks CORE → takes the still → forces a re-baseline with a visible `RE-BASELINING` state → resumes. This single hazard touches KHATA, PAKKA and PEEL, and it lands on the money loop. Handle it once.
- Free the full-res `Mat` immediately after cropping. Never hold two rung-2 buffers. A 3840×2160 RGBA frame is 33.2 MB; capture + crop + ImageBitmap + decode peaks near 100 MB on a 4 GB handset already carrying the OpenCV wasm heap and a screen recorder. **A tab reload destroys the session and the open intent** — so the phone must be able to rejoin a session by id from `paisa`.

### 4.2 One reference stack

Three features want to re-baseline the background model. Make it explicit and auditable:

```
REF_EMPTY_MAT     canonical, owned by CORE, maintained continuously
REF_PAGE_ON_MAT   pushed on entering LEDGER, so absdiff sees only the fingertip
REF_SCREEN_ZONE   pushed on PAKKA screen placement
```

One baseline is active. Push requires the basket to be quiescent. Pop restores. Every push/pop emits an audit row with a frame hash. Any rung-1/2 capture forces a re-push of `REF_EMPTY_MAT`.

### 4.3 One geometry service

- ArUco via `cv.aruco_ArucoDetector` — **`detectMarkers` is not a top-level function** in the shipped build.
- **Turn on `CORNER_REFINE_SUBPIX`.** It defaults to `CORNER_REFINE_NONE`. One line, and it improves the homography under every one of the six features. This is the single highest-leverage cross-cutting change in the entire pack.
- **Cache H.** The phone is clamped, so H is quasi-static. Solve once at session start, re-solve only when ≥3 markers are visible with reprojection error under threshold. This is what stops a splayed hand (MUDRA) or a ledger page (KHATA) from moving the overlay mid-take. Do not ship without it.
- **Anchoring axis-aligned overlays uses the *local Jacobian* of H at the anchor point**, never a global similarity decomposition. Off-axis error reaches 15% in scale at the mat edge, which makes an edge-anchored MUDRA QR visibly float off the plane.
- `px_per_mm_measured` is read off the printed 20 mm scale patch at session start and written to the audit log. **This one number is the entry gate for KHATA and PEEL.**

### 4.4 One gallery, five namespaces

The sticker registry, the screen classes, the ledger page and the SKU gallery are the same object: few-shot enrolment of a rectangle photographed on the mat, embedded, matched by cosine top-k with a margin gate. Do not build four of them.

```
sku:*      billable      CORE
screen:*   NON-billable  PAKKA
sticker:*  NON-billable  PEEL
page:*     NON-billable  KHATA
neg:*      NON-billable  hands, forearms, cloth
```

The **non-billable flag is ~20 lines and it resolves three separate conflicts at once** (§9.3, §9.5, §9.6). A customer's phone, a payment sticker and a ledger page all land on the mat during a session; without the flag, CORE segments them, embeds them and tries to bill them.

### 4.5 One overlay painter, two modes

- **PERSPECTIVE** — `warpPerspective` through H⁻¹. Rupee glyphs, rings, hatching, the KHATA row lift, the PEEL diff overlay.
- **CRISP** — axis-aligned canvas positioned by the local similarity of H, rasterised by the browser at `devicePixelRatio`, `INTER_NEAREST`, never resampled twice. **Mandatory for the MUDRA QR.** Rasterising a QR through `warpPerspective` into the preview buffer costs a resampling stage for a keystone of 1.017 across the symbol at nadir — visually free to delete, expensive to keep.

### 4.6 One audit log

Single append-only JSONL. One row per decision. Closed reason-code vocabulary.

```json
{"ts":…, "session_id":…, "feature":"MUDRA|CORE|KHATA|PAKKA|PEEL|SAAF",
 "event":…, "reason_code":…, "capture_rung":0|1|2, "baseline_id":…,
 "H_age_ms":…, "px_per_mm_measured":…, "evidence":{…}}
```

`evidence` carries the feature-specific numbers that must be publishable: `footprint_mm{long_edge, sigma, pair_delta}`, `solidity`, `ignited_fraction`, `vlap`, `saturated_fraction`, `amount_read_paise`, `payload_match`. Abstention rate per feature is a first-class published number, not an appendix.

### 4.7 One money path

- **CHILLAR is global, not a PAKKA feature.** Every intent is minted at ₹X.nn where `nn = HMAC(session_secret, session_id) mod 100`, never 00, **with rejection sampling** (a raw byte mod 100 gives residues 0–55 a 3/256 chance and 56–99 a 2/256 chance — a 1.5× skew, and exactly the kind of thing that gets found). Cost: two hours in `paisa`, no camera. Payoff: the mirror match becomes an exact primary key, the RRN/UTR/PSP-txn-id join problem is deleted rather than solved, a gallery-replay screenshot fails automatically because it shows round rupees, and PAKKA's read collapses from "extract an unknown amount" to "verify a known five-digit string."
- **Delete `upi_link: true`.** It is Live-Mode-only and Android-only; the demo runs test mode. A standard Payment Link returns the same 24-char `short_url`.
- **Cancellation is the only death.** `POST /v1/payment_links/{id}/cancel` works only from the `issued` state, so it can never void a completed payment. It stays behind the existing hold-to-confirm. Closing an intent locally without cancelling leaves an orphaned-but-payable link, because `expire_by` has a 15-minute floor — a customer's real money vanishes into an account with no green. MUDRA's fist *hides*; the operator's confirmed hold *cancels*.
- **ROKO is the universal AMBER destination.** One hold square, one ring, N exception rows.

---

## 5. Unified state machine

```
BOOT → CALIBRATE → IDLE
```

| State | Rung | Gallery writes | Mint allowed | MUDRA armed | Rung ≥1 allowed |
|---|---|---|---|---|---|
| `CALIBRATE` | 0→2 probe | no | no | no | yes |
| `IDLE` | 0 | no | no | no | **yes** |
| `BILLING` | 0 **locked** | no | no | no | **no** |
| `AWAITING_SETTLEMENT` | 0 **locked** | no | CORE only | **yes** | **no** |
| `HOLD` (ROKO) | 0 | no | no | yes | no |
| `SETTLED` | 0 | no | no | no | **yes** |
| `ENROLL` | 1 | **yes** | no | no | yes |
| `LEDGER` (KHATA) | 2 | no | KHATA rows | no | yes |
| `AUDIT` (PEEL) | 0 or 2 | no | no | no | yes |

Guards worth stating explicitly:

- `LEDGER` and `AUDIT` are reachable only from `IDLE` or `SETTLED`. Entering `LEDGER` **visibly suspends the crossing detector** and says so on screen — a mode change, not a silent one.
- `LEDGER` refuses to enter if fewer than 2 diagonal ArUco markers are visible, or if measured row height is <40 px, or if no name mask is defined.
- `AUDIT` refuses to enter if measured px/module on the largest enrolled sticker is <2.0.
- `AWAITING_SETTLEMENT` disarms MUDRA the moment a `screen:*` match lands (PAKKA and MUDRA both want the mat), and re-arms when it leaves.
- Green arrives asynchronously from the webhook in any state and is the only transition into `SETTLED`.

---

## 6. Enrollment: one session, six capabilities

Roughly 12 minutes, once, on the counter. Every step seeds something.

| # | Step | Seeds |
|---|---|---|
| 1 | **Mat lock + scale.** ArUco with SUBPIX, H cached, `px_per_mm` measured off the printed 20 mm patch and logged. | Geometry for all six; **entry gate for KHATA and PEEL** |
| 2 | **Capability probe.** What `{ideal:1920}` and `{ideal:2560}` actually return (read back from `getSettings()`); `takePhoto()` → decode → **measure real width**; `cv.QRCodeDetector` decodes a rendered canvas. | Rung ladder; PEEL decoder liveness; MUDRA render self-test |
| 3 | **Empty-mat baseline.** | `REF_EMPTY_MAT` |
| 4 | **SKU enrolment.** Place, "thoda ghumaiye", capture ~30 crops at rung 1, keep best 8 by saturation-guarded vLap, tap the price once. | `sku:*` (CORE) **and SAAF is the mechanism, not a separate feature** |
| 5 | **Sticker enrolment.** Each counter sticker decoded, payload + VPA stored per slot, shopkeeper taps MINE / NOT MINE. | `sticker:*` (PEEL) |
| 6 | **Screen-class enrolment.** ~10 crops: PhonePe/GPay/Paytm/BHIM success, generic failed, generic pending, not-a-screen. | `screen:*` (PAKKA Tier 1) |
| 7 | **Ledger enrolment.** Page laid down; **operator drags the name mask** (stored in mat coordinates); ruling pitch measured; row height in px computed and gated. | `page:*`, the DPDP mask, the KHATA gate |
| 8 | **MUDRA calibration.** 10 open palms + 10 fists over the pay panel, **under this shop's light, on this shopkeeper's hand**. Fit the solidity threshold live. Refuse to arm the solidity channel if the measured 95th-percentile-open to 5th-percentile-fist gap is <0.08. | MUDRA thresholds — **never a hardcoded 0.82** |

Step 8 is the one that matters most. Every published solidity number in the pack comes from synthetic silhouettes or Wikimedia photographs, and under the rig's own prescribed two-LED lighting the measured gap falls to 0.072 — below the feature's own kill bar. A hardcoded threshold fails on the day; a calibrated one either works or refuses to arm, out loud.

---

## 7. Model and memory budget

### Browser (4.8 MB cold-load budget)

| Item | Size |
|---|---|
| `@techstark/opencv-js` | ~2.55 MB brotli (the rig spec's "3.48 MB brotli" is the **gzip** figure) |
| App shell + UI | ~1.5 MB |
| `qrcode-generator` (MUDRA + CORE QR render) | 11.7 KB gzip |
| PAKKA digit templates (rendered at build time) | ~40 KB |
| **Total** | **≈4.1 MB — fits** |
| **Model weights in browser** | **0** |

Explicitly excluded and why:

- **MediaPipe** — `hand_landmarker.task` 7,819,105 B (gzip 5,813,803; brotli 5,368,497 — it is *not* incompressible, correcting the PRD, though the conclusion stands) plus `vision_wasm_internal.wasm` 2,611,110 B brotli. Total addition 1.76× the entire budget for one boolean. Structurally inadmissible.
- **zxing-wasm reader** (357 KB brotli) — **unnecessary. `cv.QRCodeDetector` is already in the shipped bundle and decodes a real `rzp.io` code.** PEEL costs zero new bytes. Do not ship the reader; do not ship `/full`.
- **PaddleOCR in the browser** — any variant.

### Brain (laptop, onnxruntime CPU)

| Model | Size | Latency | Fired by | Rate |
|---|---|---|---|---|
| MobileCLIP-S0 int8 | 11.85 MB | 28 ms / 45 ms p95 | CORE identity, PAKKA screen class | ~0.5 Hz |
| Digit CNN (KHATA, P1) | ~2 MB | ~5 ms | KHATA, once per capture | ~0.02 Hz |
| **Total weights** | **≈14 MB** | | | |

PAKKA's amount read is **template NCC, zero model** — with CHILLAR the task is verifying a known five-digit string, not extracting an unknown one, and templates rendered *down* to the measured cap height beat a recogniser upscaled to a fake one. `rapidocr-onnxruntime` (14.2 MB, Python ≤3.12) is not needed. PEEL, SAAF and MUDRA have no models at all.

**No contention in practice.** MobileCLIP's two consumers are mutually exclusive by state: CORE fires in `BILLING`, PAKKA only in `AWAITING_SETTLEMENT`/`HOLD`. One session pool, single-threaded FIFO, 250 ms budget, drop-oldest.

Peak brain RSS: onnxruntime + 2 sessions + one 3.9 MP still (~16 MB RGBA) + a 30-crop ring buffer ≈ **under 400 MB**. The brain is not the constraint. The phone is.

---

## 8. Conflict matrix

Fifteen real collisions. Each has a stated resolution; the ones marked ★ are the load-bearing ones.

### 8.1 ★ MUDRA gesture vs CORE placement — the same hand, the same absdiff mask

The hand that places goods is the hand that makes the gesture. Four-part resolution:

1. **Temporal** — MUDRA is armed only in `AWAITING_SETTLEMENT`, after DONE, when no new goods are expected.
2. **Spatial** — the pay panel and the sigil live in the merchant-side margin, outside the shelf→exit lane. The gesture mask is cropped to that ROI.
3. **Topological** — select the contour that **touches the mat border**. A forearm always exits the mat; goods never do. Measured: this selector picks the hand and rejects a 97,200 px goods rectangle.
4. **Arbitration** — a contour that is both border-touching *and* inside the goods lane routes to CORE, never MUDRA.

### 8.2 ★ MUDRA solidity vs goods already on the mat

The maintained empty-mat reference makes every item permanent foreground, and MUDRA fires precisely when the mat is most cluttered. An open hand overlapping a goods blob measures solidity 0.859 — an OPEN hand scoring above a CLOSED threshold, i.e. a false cancel.
**Resolution:** if any goods blob intersects the pay panel, MUDRA refuses to arm and says "panel blocked." Plus the calibrated 0.85/0.90 threshold pair (not 0.70/0.79) with an explicit UNKNOWN band, which absorbs the merged-contour case into abstention rather than a wrong answer.

### 8.3 ★ Sticker registry vs SKU gallery

Same object, same code path. A payment sticker on the mat during a session would be segmented, embedded and billed.
**Resolution:** one gallery, `sticker:*` namespace, non-billable flag. Any match into a non-billable namespace excludes the item from the basket and routes to that feature's handler.

### 8.4 ★ PEEL slot binding → false public accusation

Nearest-slot binding with no distance threshold bound a genuine *unenrolled* sticker at 703 px error and captioned it CODE SUBSTITUTED. This is exactly the multi-rail landmine the design claims to have dissolved.
**Resolution:** distance gate (~1.5 sticker widths) + one-to-one assignment; unbound → **GREY, never RED**; RED only when the shopkeeper has explicitly selected which pin is being checked.

### 8.5 PAKKA screen vs CORE item

A customer's phone is a bright quad the crossing detector will bill.
**Resolution:** `screen:*` non-billable, plus a geometric prefilter (aspect 1.6–2.4, mean luminance far above the mat reference) so it does not even reach MobileCLIP.

### 8.6 ★ KHATA page vs ArUco markers

A ledger page can cover the corner markers and kill H. An A4 page centred on A3 leaves 43 mm / 61 mm margins, so markers within 40 mm of the corners survive; the minimum is 2 diagonal markers = 8 correspondences. A foolscap bound register covers everything.
**Resolution:** cached H (the phone is clamped, so occlusion cannot move the overlay) + refuse to enter `LEDGER` below 2 diagonal markers + a printed page-placement outline.

### 8.7 KHATA re-baseline vs CORE reference

Handled by the reference stack (§4.2). Push on entry, pop on exit, both audited.

### 8.8 ★ Capture rung contention

Three features want a still, one wants a burst, CORE wants uninterrupted 720p.
**Resolution:** the `CaptureBroker` (§4.1). Rung ≥1 requires a quiescent basket; every return forces a `REF_EMPTY_MAT` re-baseline behind a visible state.

### 8.9 Four features want "the sharpest, least-glared frame"

SAAF, KHATA, PAKKA and PEEL all need it.
**Resolution:** one primitive. `select_best(frames, k)` = reject any frame with >2% of pixels within 2% of saturation, then rank by variance-of-Laplacian, take k. **The saturation guard is not optional** — vLap is *anti-correlated* with legibility under glare, because a blown specular edge maximises Laplacian variance. Ungated, the gate preferentially selects the worst frames.

### 8.10 MUDRA vs PAKKA — both live in `AWAITING_SETTLEMENT`

A phone laid on the mat is a border-touching bright quad that could read as a hand.
**Resolution:** mutual exclusion in the state machine. A `screen:*` match disarms MUDRA until it leaves.

### 8.11 Five AMBER sources, one hold square

**Resolution:** the hold is a *session* property, not per-feature. One ring, N exception rows, reason codes stacked in the audit line.

### 8.12 ★ DPDP surface expansion

CORE's invariant ("at 45 cm nadir a standing person cannot be inside the crop") is not violated by PAKKA and KHATA — it is *sidestepped*, because they deliberately place a third party's name, bank last-4 and phone number **inside** that crop.

- **KHATA:** name mask applied in the browser before the buffer leaves the frame-grab call. A hardcoded left-35% mask silently fails on page-per-customer ledgers, digitising the name while the screen says it didn't. **The mask is dragged by the operator at enrolment, stored in mat coordinates, and `LEDGER` refuses to enter without one.**
- **PAKKA:** only the screen quad, warped to canonical, leaves the phone. The brain returns tokens and discards pixels. Never persisted, never logged, never rendered back into the preview.
- **Every rung-2 still is a new frame-grab path** that bypasses the audited mask unless it uses the same masked-capture function. Invariant 4.
- **On-screen and README copy is a *design* claim** ("the name never leaves the paper", "no image is persisted"), never a *legal* claim. DPDP s.3(a)(ii) covers data "in non-digital form and digitised subsequently"; s.2(x) makes mere collection processing; there is no on-device exemption; s.3(c)(i)'s personal/domestic carve-out does not rescue a commercial shop; and "anonymis" appears nowhere in the Act.

### 8.13 Shared brittleness in the OpenCV build

All six features share one binding surface, so one build fact breaks several at once. See §10.

### 8.14 Version pinning

The pack contains claims about 4.11.0, 4.12.0-release.1 and 5.0.0-release.1 with materially different symbol tables (4.5.2 has no aruco at all; 5.0.0 drops AKAZE). **Pin exactly one version, record it in every enrollment record, and refuse cross-version comparison of any stored geometry.**

### 8.15 Contact Picker

Chrome-Android-only; Samsung Internet's implementation is partial and was removed at 22.0; iOS is behind a flag. It also reintroduces third-party PII — the debtor's — processed for dunning, without their presence or consent.
**Resolution: cut it.** The shopkeeper sends the `short_url` himself on his own WhatsApp. One fewer API, one fewer PII surface, one fewer browser dependency, and a better legal posture.

---

## 9. Day-0.5 gate battery — before any feature code

Nine checks. Four can kill a feature outright; **two can kill the core loop**. Run them first, in this order.

| # | Check | Time | Kills |
|---|---|---|---|
| 1 | **`Mat.clone()` audit.** In at least one shipped build, `clone()` aliases rather than deep-copies; the exact P0 pattern (hold empty-mat reference → clone → absdiff) returns zero, so **no crossing ever fires**. Replace every `clone()` with `new cv.Mat(); src.copyTo(dst)`. *(Reproduced in Node under 4.12.0; confirm on the pinned version in-browser.)* | 30 m | **CORE** |
| 2 | **`notes` propagation.** Razorpay's docs are silent on Payment Link `notes` reaching the payment entity. The green rule depends on it. Test-mode check. | 20 m | **CORE money path** |
| 3 | **Does a UPI app scan an `https://rzp.io/...` QR?** It is a web URL, not a `upi://` payload; QR is not a documented Payment Link channel. Three handsets, GPay/PhonePe/Paytm. Fallback: `POST /v1/payments/qr_codes` server-side, fetch `image_url` on `paisa` (which is online anyway), push bytes over the LAN. | 10 m | CORE + MUDRA customer beat |
| 4 | **`takePhoto()` actual width.** Call it, decode the blob, measure. Never trust `getPhotoCapabilities`. | 5 m | KHATA, PAKKA T2, PEEL flat |
| 5 | **`px_per_mm` off the 20 mm scale patch**, at rung 0/1/2. | 10 m | KHATA, PEEL |
| 6 | **Ruling pitch of the real bahi-khata**, with a ruler. Row height must clear 40 px at the delivered rung-2 resolution. | 5 m | KHATA |
| 7 | **Printed width and QR version of three real counter stickers.** Decode on the laptop, print version and mm. Payload decode needs ≥2.0 px/module. | 30 m | PEEL |
| 8 | **Hero-amount cap height on real GPay/PhonePe success screens**, with a ruler. | 20 m | PAKKA T2 |
| 9 | **CHILLAR survival.** Five live links at odd paise; confirm `amount_paise` round-trips exactly 5/5 and no UPI app rounds or refuses. | 30 m | CHILLAR (and with it, PAKKA's whole key) |

Plus two build traps that masquerade as broken algorithms and will cost hours if not pre-empted:

- **`cv.TERM_CRITERIA_COUNT` / `cv.TERM_CRITERIA_EPS` are undefined.** The names are `cv.TermCriteria_COUNT` (1) and `cv.TermCriteria_EPS` (2). Passing the 4.x names yields `type=0` and every `findTransformECC` call throws an assertion — silently, because the honest failure text reads "not enough frames."
- **`findTransformECC` throws** on non-convergence rather than returning a low correlation. The throw *is* catchable in wasm (`cv.exceptionFromPtr(e).msg`), but the build lacks `--enable_exception`, so unwinding never runs and every throw leaks; ~9 consecutive throws hung the runtime in testing. Wrap every call, treat a throw as frame rejection, and hard-cap the count.

---

## 10. Rig spec corrections

The brief's library inventory is wrong in ways that matter across features. Fix the spec before anyone designs around it.

| Claim | Reality |
|---|---|
| "AKAZE VERIFIED present" | **Absent.** ORB is the only descriptor. |
| "SIFT, MOG2 not present" | Correct. Also absent: `createCLAHE` (the `CLAHE` *class* is present), `goodFeaturesToTrack`, `sortIdx`, `phaseCorrelate`, `estimateAffinePartial2D`. |
| "`detectMarkers`" | Not a top-level function. Method on `cv.aruco_ArucoDetector`. |
| "opencv-js 3.48 MB brotli" | ~2.55 MB brotli; 3.48 MB is the **gzip** figure. Favourable. |
| "840×1188 at 2 px/mm, A3 mat" | **Internally inconsistent.** 840/297 = 2.828, 1188/420 = 2.829 — so it is A3 at 2.83 px/mm (A3 in PostScript points). At 2 px/mm those dimensions describe A2. **Optical sampling from 720p over the stated 60×40 cm FOV is only ~1.8–2.2 px/mm, so the rectified buffer upsamples ~1.4×.** Fix the line, and never quote buffer scale as available resolution. |
| — | **`cv.QRCodeDetector` IS present and functional.** This is the finding that makes PEEL free. |
| — | **`cv.QRCodeEncoder` is NOT bound** (only its enums leak). An external encoder is required — `qrcode-generator`, 11.7 KB gzip. |

---

## 11. What was cut, and why it stays cut

- **SAAF's MRP read.** At the rig's real 1.8–2.2 px/mm a 2 mm MRP digit is 3.6–5.9 px against a recogniser that wants 48 px. It also raises enrollment capture resolution — the one variable the PRD publishes as a *privacy control* — and it automates away the single price tap that is the product's honesty mechanic. Four independent reasons, any one sufficient.
- **PAKKA's reference-string / UTR / timestamp read.** ~1.1 px stroke width at full resolution. Not marginal — below Nyquist, permanently, at this camera height. The corroboration line and its 20-real-payment experiment go with it; running it would buy a near-certain null result.
- **PEEL's module-grid diff as a *detector*.** The UEC ≥1.0 safety gate and the grid diff are mutually exclusive by construction: UEC = 1.0 means zero error correction consumed, which means the data modules read exactly, which means Hamming is 0. Measured: 0 of 72 abrasion captures pass the gate; 96/96 attacks are caught by the payload string alone; 0 are caught by the grid that the string missed. **Keep the red blizzard as the rendering of a RED that the payload already established** — labelled honestly, never implying it did the detecting. The wear/AMBER claim is deleted.
- **MediaPipe**, everywhere.
- **Contact Picker.**
- **`upi_link: true`.**

---

## 12. Honest build cost

| Block | Days |
|---|---|
| **Shared substrate** | |
| CaptureBroker + rung ladder + re-baseline discipline | 0.75 |
| Reference stack | 0.25 |
| Gallery namespacing + non-billable flag | 0.25 |
| Unified state machine refactor | 1.00 |
| Audit log schema + reason-code vocabulary | 0.50 |
| CHILLAR in `paisa` + rejection sampling | 0.50 |
| ROKO hold state (shared) | 0.50 |
| Day-0.5 gate battery | 0.50 |
| Shared frame-selection primitive | 0.25 |
| Corner refinement + H caching + local-Jacobian anchor | 0.50 |
| **Subtotal** | **5.00** |
| **Features (at reduced scope)** | |
| CORE rework (clone audit, state machine) | 0.50 |
| MUDRA — reveal tier, 160 mm panel, calibrated solidity, tap fallback | 1.50 |
| KHATA LIFT — rows, mask, strike triage, 1440p mode, pre-fill | 3.50 |
| SAAF — saturation guard, extend to cold enroll, instrumentation | 0.50 |
| PAKKA-PARCHI — T0 + T1 + CHILLAR + gated T2 | 2.00 |
| PEEL / IDENT — payload pinning, distance gate, close-hold capture | 1.50 |
| **Subtotal** | **9.50** |
| **Held-out measurement (the kill criteria)** | |
| MUDRA 200 hand events × 3 lighting | 0.50 |
| KHATA 5 real pages hand-labelled + gap histogram | 0.50 |
| PAKKA 40 stills × 4 apps × 2 lighting | 0.75 |
| PEEL 20 real sticker photographs | 0.25 |
| **Subtotal** | **2.00** |
| **TOTAL** | **16.5 engineer-days** |

**Against 8 calendar days, solo: 2.06× over.**

That is the honest number. Since the decision to ship all six is final, the question is not *whether* to cut but *what degrades* — and every feature already has a stub tier that is filmable and honest, because they all fail into ROKO.

### The 8-day plan that actually lands

| Day | Work | What ships |
|---|---|---|
| **0.5** | Gate battery (§9) + build traps | Four features live or die here, on measurement |
| **1–2** | Shared substrate, all of §4 | Nothing visible; everything downstream |
| **3** | Enrolment session (§6) + SAAF (it *is* enrolment) | CORE gallery, sticker pins, screen classes, page mask, MUDRA thresholds |
| **4** | MUDRA + PEEL together (both are "render + compare a string") | Two features |
| **5** | PAKKA (T0 built, T1 is gallery rows, T2 gated) | One feature |
| **6** | KHATA at **stub tier** | Rows rake, name mask lands, strike triage runs, amounts are **redacted bars + numpad**, no digit recognition |
| **7** | Threshold fitting on real data + held-out measurement | The numbers on screen become true |
| **8** | Shoot, README, repo | — |

**KHATA's digit layer is the casualty.** Its own Angle-C fallback pre-committed to exactly this — page lands, name column blacks out, rows rake in age order, amounts lift as shimmering redacted bars, he taps the oldest and types three digits, QR paints on the plane. Five of seven beats, 100% reliable, zero AI, and the caption ("it finds the rows, it does not read handwriting") is true.

Day 7 is not cuttable. Six features shipping with synthetic thresholds is the failure mode that looks fine in rehearsal and breaks on camera.

---

## 13. Realistic quality at that cost

| Capability | Ships at | Honest quality | Most likely on-camera failure |
|---|---|---|---|
| **CORE** | Full | Works. It is P0. | Neither of the two day-0.5 gates (clone, notes) had been checked; either is a silent total failure of billing. |
| **MUDRA** | Reveal tier | Render half ~95%: payload is v2/v3, geometry is exact on a marker-tracked plane, at a **160 mm** panel the customer scans at 3.3–4.2 px/module at 25 cm with real margin. Gesture half after live calibration ~85–90% balanced accuracy with an explicit UNKNOWN band. | The gesture does not fire and the shopkeeper holds a palm over a mat while nothing happens. Tap-to-arm bound before the first take; rehearse with it armed. |
| **KHATA LIFT** | Stub tier | Rows + mask + strike triage. Triage ~0.90–0.95 on real pages **if** row height clears 40 px, and it refuses to enter if not. Age ordering is exact by construction. Amounts: not read, typed. | Rows silently fail to lift because the phone delivered fewer pixels than the bench predicted. Preflight off the scale patch and **block the mode** rather than half-populate the page. |
| **SAAF** | Full | Works. Invisible. Insurance against permanently poisoning one gallery entry. Its real delta over the PRD is the saturation guard plus extending vLap ranking to cold enroll — do not pitch it as +35 pp. | Cannot fail on camera because it cannot appear on camera. Silent regression if the guard is mistuned; ship behind a one-line flag. |
| **PAKKA-PARCHI** | T0 + T1, T2 gated | T0 decides everything and is built. T1 screen class ~85%+ (and survives even a MobileCLIP deletion, since these screens separate on brand colour alone via `compareHist`). T2 only if `takePhoto()` clears ~3000 px: then ~90% exact match, ≤20% abstention, and **~0% false-match measured across every condition including 5 px caps, wrong fonts, 40° tilt and heavy glare** — the architecture routes all failure to MISMATCH or ABSTAIN. | Specular glare on a face-up glossy screen under a nadir camera. The recovery is scripted: "cannot read — not guessing," slide the phone 3 cm into the printed box, retake. The abstention *is* the beat. |
| **PEEL / IDENT** | Payload only | 96/96 substitution recall, zero false positives under every tested condition, no gate, no threshold, ~30 lines. Needs ≥53 mm stickers at 720p flat, or close-hold at 25–30 cm (3.9–5.8 px/mm). Zero new bytes. | No decode at all under a tube light on laminated vinyl. Matte-laminate the demo stickers, kill the overhead light, side-light at ±45°. |

---

## 14. If forced to cut further, in order

1. **KHATA's strike-as-commit watcher** (2.5 s watchdog + tap already exists as the fallback) — 0.5 d.
2. **PAKKA Tier 2 entirely.** Every screen outcome becomes AMBER. Nothing is asserted about the customer's screen, so nothing can mislead. Keeps Beats 2 and 3, which are the beats that carry the thesis — 2.0 d.
3. **PEEL's module-grid overlay.** Keep the verdict strip; drop the blizzard — 0.5 d.
4. **MUDRA's solidity channel.** Ship sigil-occlusion + border-connectivity + 400 ms dwell alone. It is still a working button made of ink; it just loses open-vs-closed semantics and becomes press-and-release. **Say so on screen** rather than pretending — 0.75 d.
5. **KHATA entirely**, falling back to the published roadmap sentence — 3.5 d.

That ladder recovers 7.25 days and lands the plan inside 8. Nothing on it touches the money path, the green rule, the hold state, or the audit log — which is the point of building the substrate first.

---

## 15. The two sentences that must be true on camera

> *"I do not classify the image. I ask the ledger. The ledger decides, and the ledger only says green when a signature-verified webhook says so."*

> *"There are six things this can do and five of them can only ever tell you it does not know. Here is how often each one says that."*

Publish the abstention rate for every feature, on screen, in the README, and in the repo. Six features that each visibly decline to guess is a stronger claim than one feature that never admits it. It is also, given what these six actually measure, the only claim that survives a judge with a ruler.



---

# PART III — THE 5-MINUTE VIDEO, ALL SIX

## GAWAAH — 5:00 SUBMISSION VIDEO PLAN
### Six capabilities, one prop family, zero lies on screen

---

## 0. THE STRUCTURAL PROBLEM, AND THE ACTUAL SOLUTION

Six capabilities. Three of them require a new physical object:

| Capability | New prop? |
|---|---|
| **P0 crossing loop** — bills what leaves the shelf | No — packets on the mat |
| **MUDRA** — gesture-summoned payment target | No — a hand |
| **SAAF** — enrollment glare/blur gate | No — invisible |
| **PEEL** — sticker destination check | **Yes** — a payment sticker |
| **PAKKA** — customer payment-screen check | **Yes** — a stranger's phone |
| **KHATA LIFT** — paper ledger triage | **Yes** — a bahi-khata |

**Continuity does not solve this. Continuity makes it worse.** A new object appearing inside a shot that looks like the hero shot reads as *demo #2 pretending to be demo #1* — which is the version viewers punish hardest. The 21-demo finding is about **register**, not about cuts.

**The fix: two registers, one hard hinge, and an honesty chip that never leaves the screen.**

- **REGISTER A — THE TAKE.** Portrait, pillarboxed inside the 16:9 frame. Nadir phone view only. Warm practical light. Room tone, no music, no lower thirds. One prop family. Unbroken. This is *the product working*.
- **REGISTER B — THE BENCH.** Full-bleed landscape. Overhead bench board or full-screen capture. Cool light. No room tone. Monospace overlays, hard slates, numbers on screen. This is *proof about the product*.

A new prop inside Register B does not read as a second demo, because Register B has already told the viewer it is a lab notebook. The aspect-ratio flip alone carries this — nobody misreads portrait-phone-footage → full-bleed-bench.

**The through-line that makes six things one thing.** Every capability is the same verb:

> **A physical event happens on a calibrated plane. Arithmetic — not a model — decides what it means, and it abstains when it cannot see.**

- goods cross the exit edge → bill
- a hand occludes a sigil → reveal the target
- a sticker enters the plane → compare to what was enrolled
- a screen enters the plane → compare the amount to the ledger
- a pen stroke crosses a row → close the debt
- frames enter the buffer → reject the ones that cannot be seen

Six variations, one verb. The mat is the universal surface. **The camera never moves once in five minutes.** Say that out loud at the close; it is the single strongest unifying claim available and it is true.

---

## 1. THE HONESTY CHIP — non-negotiable UI element

A persistent one-line monospace chip, bottom-left, present in **every frame of the video**. It states the mode of the footage you are watching:

```
LIVE · razorpay test mode · trigger: hand · webhook: verified
BENCH · sticker 58mm · 720p stream · n=1
MEASURED · held-out n=200 · fit on disjoint set
REPLAY · bundle a3f2 · labelled per README
```

This is the mechanism that lets you show six capabilities without any of them being a lie. It is also visually distinctive and no competitor will have it. **If a beat is triggered by a hidden key instead of a hand, the chip must read `trigger: key`.** Bind that rule before the first take.

---

## 2. PRE-FLIGHT GATES — nothing below is filmable until these pass

Run in this order. Total ≈ 4 hours. Every one of them can kill a section.

| # | Gate | Time | Kills if it fails |
|---|---|---|---|
| **G0** | **Fix `Mat.clone()`.** In `@techstark/opencv-js` 4.12.0 `clone()` aliases the source buffer. If the empty-mat reference is cloned anywhere, `absdiff` returns zero and **no crossing ever fires**. Replace every `clone()` with `new cv.Mat(); src.copyTo(dst)`. | 45 min | **The entire video.** Do this first. |
| **G1** | **Reconcile the mat spec.** `840×1188 @ 2 px/mm` = A2, not A3. Resolve to A3 @ 2.83 px/mm and fix the PRD line. Then measure delivered px/mm off the printed 20 mm scale patch on the real phone. | 20 min | Every mm-denominated threshold in the build. |
| **G2** | **Turn on ArUco `CORNER_REFINE_SUBPIX`.** Default is `CORNER_REFINE_NONE`. One line. Improves the plane engine globally, not one feature. | 15 min | Nothing — pure win. |
| **G3** | **Scan an `https://rzp.io/i/...` QR with GPay, PhonePe, Paytm.** It is a web URL, not a `upi://` payload. If no UPI in-app scanner takes it, the customer must use the camera app → browser → checkout. | 10 min | The hero take's payment beat as scripted. Fallback: shoot the scan with the phone camera app, or switch to `POST /v1/payments/qr_codes` and push the PNG over LAN. |
| **G4** | **`ImageCapture.getPhotoCapabilities().imageWidth.max`, then actually call `takePhoto()` and measure the returned blob.** Capabilities can lie. | 15 min | **PAKKA Tier 2** (amount read). Under ~3000 px, cut it — there is no lower tier. |
| **G5** | **Ruler on a real GPay and PhonePe success screen.** Measure the hero-amount cap height in mm. | 10 min | PAKKA Tier 2 again, independently. Below ~2.5 mm it dies. |
| **G6** | **Row height in pixels on the real ledger, at 1440p.** Not px/mm — *row height*. Requires ≥40 px. 1080p delivers 3.64 px/mm isotropic; the 90° book rotation buys nothing. | 20 min | **KHATA LIFT.** Below 40 px/row, cut the section. |
| **G7** | **Solidity gap under the actual two-LED lighting.** 20 shots, open vs fist, forearm attached. Fit the threshold live; the published 0.82 and 0.70/0.79 pairs are both wrong. Need ≥0.08 separation. | 30 min | **MUDRA's gesture.** Below 0.08 → degrade to tap-to-arm, keep every frame of the visual. |
| **G8** | **20 real photographs of one real sticker on the real mat.** Compute ignited fraction across re-lays. Settles glare, aliasing and threshold calibration at once. | 45 min | **PEEL's grid render** (not the payload layer). |
| **G9** | **Print demo stickers at 55–60 mm.** At 2.0 px/module the payload layer decodes off the **existing 720p stream** — no `takePhoto`, no re-homography. | 0 min | Nothing. Do it. |

**Ordering note:** G0, G1, G2 gate everything. G3 gates the hero take. G6, G7 each gate one section. If G6 fails, KHATA's 40 s redistributes to the refusals and the close — which is a *better* video, not a worse one.

---

## 3. THE FULL 5:00 PLAN

### `0:00 – 0:14` — COLD OPEN
**REGISTER A · LIVE TAKE**

| t | Shot |
|---|---|
| 0:00 | Black. One second of counter room tone. |
| 0:01 | Cut to nadir. Empty A3 takhti, four ArUco corners, exit arrow. No UI. |
| 0:03 | A hand slides three packets across the exit edge, one after another. Rupee glyphs paint on the wood in perspective as each crosses. Total climbs: `₹68.00 → ₹142.00 → ₹214.37`. |
| 0:12 | Card, Devanagari + English: **"जो शेल्फ़ से जाता है, वही बिल होता है" / "It bills what leaves the shelf."** |

No narration. No explanation. The `.37` is planted here and paid off at 1:44.

---

### `0:14 – 1:38` — THE HERO TAKE (84 s)
**REGISTER A · ONE UNBROKEN NADIR SHOT · LIVE, real Razorpay test mode**

> **One camera move is permitted in this entire block: none.** Camera B (locked-off wide of the whole counter, timecode-locked, rolling the whole time) is used once, at H8, for a viewpoint cut inside one continuous real-time event. That is not an edit in time and the chip says so.

| t | Beat | Detail | Fallback |
|---|---|---|---|
| **0:14–0:21** | **H1 · Mat lock** | Four ArUco corners tick green in sequence. A readout appears on the wood: `2.83 px/mm · from the 20mm patch`. This 7 seconds earns every number that follows — the plane is *measured*, not asserted. | Cached H (phone is clamped, mat is taped). Solve once, refuse to re-solve mid-take. |
| **0:21–0:34** | **H2 · Three crossings** | Packet placed → contour snaps → `minAreaRect` → slid over the exit arrow → glyph paints in perspective → total climbs. ~4 s each, natural pace, real hands. | Re-baseline the empty mat between takes as routine, not repair. |
| **0:34–0:46** | **H3 · The abstention** | Fourth packet. Unknown SKU. **Amber hatched outline. The total visibly does not move.** Hold on this for a full three seconds. This is the most important beat in the film and it costs nothing. | None needed — this beat cannot fail, it *is* the failure state. |
| **0:46–0:58** | **H4 · Warm enroll** | Keypad slides up. One price tap. Amber → green. Total updates to `₹214.37`. Shopkeeper nudges the packet (`थोड़ा घुमाइए`). A tiny chip, bottom-right, reads `saaf 8/8` — **SAAF running, unnarrated.** | If the enroll keypad misbehaves, this is the beat to re-shoot; it is the only one that is cheap to redo. |
| **0:58–1:05** | **H5 · DONE** | Tap DONE. `paisa` re-runs the crossing predicate **server-side**. Payment Link **pre-minted here**, not on the gesture. Counter goes amber: `AWAITING SETTLEMENT`. | — |
| **1:05–1:22** | **H6 · MUDRA bloom** | Open palm slides in from the merchant edge, settles flat over the printed sigil in the margin. 400 ms dwell. Ring closes. A **150 mm** QR blooms outward from under the hand, drawn *occluded by the silhouette mask* so it reads as lying under the palm. Hand lifts. **The QR stays** — anchored to mat coordinates, not to the hand. `₹214.37` beside it, in plane. | **Tap-to-arm, bound before the first take.** Operator triggers off-camera; the bloom is identical; **the chip flips to `trigger: key`.** |
| **1:22–1:29** | **H7 · The switch is a switch** | Fist closes over the panel → QR collapses, ring opens, mat is bare. Palm opens again → QR returns. Two seconds each. This proves it is a control, not an animation. | The fist **hides locally**. The real `/cancel` is behind hold-to-confirm and is filmed separately at E0. A stray sleeve must never be able to kill a live payment. |
| **1:29–1:38** | **H8 · Green** | **Cut to Camera B** (wide, same take, timecode locked): customer leans in, scans the face-up screen, pays. **Cut back to nadir:** ring pulses **GREEN**. Chip: `webhook: HMAC verified · amount == intent.amount_paise`. | Camera B is *required*, not optional: the QR is on the shopkeeper's upward-facing screen, which is above the nadir camera. **The nadir camera can never show the scan.** State this in the README rather than hiding it. |

**What is genuinely live here:** the crossing predicate, the abstention, the enroll, the server-side re-check, the mint, the render, the webhook, the green. **What may be assisted:** the gesture trigger, and the chip says so.

---

### `1:38 – 1:44` — THE HINGE (6 s)
**HARD REGISTER BREAK**

Room tone cuts to silence. Pillarbox opens to full-bleed 16:9. Grade shifts warm → cool. Black card, monospace, white:

```
Everything after this is evidence, not demo.

Different camera. Different register.
Every shot is labelled LIVE, BENCH, MEASURED or REPLAY.
Four things this system refuses to do are at 4:04.
```

**This card is the load-bearing element of the whole structure.** It gives the viewer explicit permission to re-file the new objects that are about to appear. Without it, the sticker at 1:58 reads as demo #2. With it, the sticker reads as exhibit A.

---

### `1:44 – 1:58` — E0 · THE MONEY LEDGER + CHILLAR (14 s)
**REGISTER B · LIVE screen recording · zero optics**

| t | Shot |
|---|---|
| 1:44 | Full-screen Razorpay dashboard. The link minted at 0:58 — `created`, then `paid`. Timestamps match the hero take. |
| 1:49 | A second link, minted and then **`cancelled`** by a fist. Split screen: nadir footage of the fist / dashboard status flipping. |
| 1:53 | Zoom the amount field: `21437`. Overlay: **`the paise are the receipt number`** — `nn = HMAC(session_secret, session_id) mod 100`. The amount is the only field guaranteed byte-identical across the payer's screen, the PSP, the acquirer and our mirror — because if it differed, the payment would be wrong. So the ledger match is an exact primary key. **No RRN. No UTR. No join.** |

**Why this is the strongest 14 seconds in the evidence half:** it is a screen recording. It has no optics, no lighting, no gesture, no prop. It cannot fail on the day, and no competitor's AR demo can show a payment API status flipping in response to a hand.

---

### `1:58 – 2:32` — E1 · PEEL (34 s)
**REGISTER B · LIVE (payload layer) + BENCH (render)** · NEW PROP: a payment sticker

Slate: `PEEL — does this sticker still pay you? · LIVE · 720p stream · 58mm sticker`

| t | Shot |
|---|---|
| 1:58 | Bench board. Four counter stickers laid out. Tap ENROL. Each decodes; a green outline snaps; caption per sticker: `PhonePe · enrolled 21 Aug`. **What is stored is the payload string, not the module grid.** |
| 2:08 | Tap CHECK. Three go green instantly. One ignites red. Caption: `razorpay: CODE SUBSTITUTED`. The red module-difference blizzard paints through H-inverse onto the sticker, finder patterns punched out as black holes. Label underneath, honestly: **`554 of 1369 modules differ — this is the rendering of a verdict the payload string already made.`** |
| 2:20 | An **unenrolled** sticker enters frame. **Grey card: `NOT ENROLLED — enrol it?`** No red. Overlay: *there is no registry of legitimate UPI handles anywhere in this system, so no accusation is expressible.* |
| 2:27 | Numbers: `96/96 substitutions caught · 0 false positives across glare, shadow, curl, occlusion · decodes at 2.0 px/module` |

**Honest scope stated on screen:** *detects substitution, not wear.* The UEC≥1.0 gate that makes the feature safe also makes Hamming zero by construction — so the wear/AMBER claim is cut, and that cut is stated. The grid is a visualisation; the string is the detector.

---

### `2:32 – 3:06` — E2 · PAKKA (34 s)
**REGISTER B · LIVE (Tier 0 + Tier 1) · Tier 2 GATED on G4/G5** · NEW PROP: a customer's phone

Slate: `PAKKA — the screen says paid. Does the ledger?`

| t | Shot |
|---|---|
| 2:32 | Counter is amber, `AWAITING SETTLEMENT`. A customer's phone is laid **face-up in a printed corner box on the mat** — deliberately off the optical axis, so the rig's own mirror image reflects away from the lens. Absdiff finds it instantly: brightest rectangle the mat has ever seen. |
| 2:39 | Two lines paint on the bare wood beside it, in the same perspective glyphs as the rupee amounts: `SCREEN  ₹185.00` / `OWED    ₹214.37` and one word: **`MISMATCH`**. |
| 2:47 | Second attempt, correct screen: `SCREEN ₹214.37 / OWED ₹214.37 / LEDGER: settled 8s ago`. Then the line that is the whole argument, on screen and in voice: **"I did not decide this from the image. The image told me what to look up. The ledger decided."** |
| 2:56 | Third attempt: glare across the screen. **`CANNOT READ THIS SCREEN — NOT GUESSING. AMBER.`** Hold three seconds. |
| 3:01 | Card: `never GREEN from a screen. never RED from a screen. four AMBER states, and that is the whole verdict space.` |

**If G4 or G5 fails:** cut beats 2:39 and 2:47, keep Tier 1 (screen-class from the existing gallery) and the abstention. Section shrinks to 20 s; the redistributed 14 s goes to the refusals. **The pitch loses nothing important**, because the argument was never "I read your screen" — it was "the ledger decides."

**Stated on screen, unprompted:** the reference string / UTR is **optically dead at this rig** — ~8 px cap, ~1.1 px stroke, against a recogniser that wants 48 px. No model fixes that. The amount is the only resolvable field, and it is also the only conserved one. Optics and payments semantics agreed independently.

---

### `3:06 – 3:46` — E3 · KHATA LIFT (40 s)
**REGISTER B · BENCH · MEASURED** · NEW PROP: a bahi-khata

Slate: `KHATA LIFT — no OCR, no names, no amounts read`

| t | Shot |
|---|---|
| 3:06 | The ledger lands on the takhti. **1440p capture mode.** A preflight chip reads `row height 47px — OK` off the 20 mm scale patch. If it read below 40, the mode would refuse to enter. Show that refusal for one second in a second take. |
| 3:13 | Rules rake down the page. Rows band. **Struck rows desaturate and sink flat. Unstruck rows get a green edge and lift off the paper in perspective, stacked in age order, oldest riding highest.** Age is row index — a ledger is append-only, so vertical position *is* chronological. No date parsing anywhere. |
| 3:24 | Caption over the lift: **`no OCR / no names / no amounts read`** — and the shopkeeper's own handwriting stays legible inside every lifted card, which is the visible proof of that claim. |
| 3:29 | He taps the top card. A QR grows out of that line of the ledger. Payment lands. The app says one word: **`कट`**. He draws the line. The camera watches the stroke. **The card drops back onto the page and goes grey.** The pen is the receipt. |
| 3:40 | Numbers: `0.974 row accuracy · 352 held-out rows · 0 open debts hidden · 9 settled rows shown as open (one dismissing tap)` and the honest claim: **`from today, cross it out and the camera sees it.`** Not "it reads your existing ledger." |

**The claim discipline here is the whole point.** Whether shopkeepers already strike settled rows is unverified. GAWAAH does not need the convention to pre-exist — it *instructs* the mark, and the instruction is the beat. Say the true thing.

**If G6 fails:** cut the section entirely. Reallocate 40 s: +14 s to refusals, +16 s to the close, +10 s of extra hold on the hero take's abstention beat. A five-feature video that is airtight beats a six-feature video with a section that shows half a page lifting.

---

### `3:46 – 4:04` — E4 · SAAF (18 s)
**REGISTER B · MEASURED · README-adjacent**

Slate: `SAAF — the gallery only keeps what it can see`

This capability is **invisible by construction**. It is a gate. It gets a still, not a shot, and that is stated.

| t | Shot |
|---|---|
| 3:46 | A 2×4 contact sheet of eight enrollment crops captured during the 0:46 nudge. Three struck through in red, each labelled with its measured variance-of-Laplacian and saturated-pixel fraction. |
| 3:54 | The finding, plainly: **`variance-of-Laplacian selects the WORST frame when there is glare in it — a saturated specular edge maximises Laplacian variance.`** Five lines of saturation guard fix it. |
| 3:59 | Paired A/B: vLap alone vs vLap + saturation guard, restricted to the glare-lit condition, on ≥250 held-out placements, with the bootstrap CI shown. **If the CI crosses zero, that number goes on screen anyway.** |

**Honest framing, on screen:** *the PRD already sorted by variance-of-Laplacian. This is a bug fix against committed design, not a new feature.* Claiming +35 pp as a new win would be the one genuinely misleading thing in this video.

---

### `4:04 – 4:32` — E5 · THE REFUSALS (28 s)
**REGISTER B · MEASURED**

Slate: `four things this system will not do, and why`

Rapid cuts, one card each ~5.5 s, each with the number that killed it:

| Refusal | The number |
|---|---|
| **No QR on a palm.** Ever. | The cliff is **curvature at 8–12 % displacement/width**, not contrast. `ecM`, `ecQ` and `ecH` all fail at exactly 0.12. Error correction provably cannot buy it back. A real palm cups at 4–13 %. *(And the contrast argument everyone reaches for is wrong — a v2 code decodes down to 20 % contrast.)* |
| **No reading the transaction ID.** | **~8 px cap height, ~1.1 px stroke**, against a recogniser that normalises to 48 px. Below Nyquist by ~4×. Not hard — *not present in the signal*. |
| **No generative text super-resolution.** | The DiffTSR split-screen, running live: the model produces a **confident wrong rupee digit**. Multi-frame fusion measured at **+4 to +8 pp with overlapping CIs** — inside noise. Frame *selection* was worth +35 pp; frame *fusion* was worth nothing. |
| **No fake-screenshot classifier.** | Every repo in that genre is defeated by a genuine screenshot of a real payment to a different merchant. **We do not classify the image. We ask the ledger.** |

Then one card: **`the reverse channel is also dead — UPI Collect deprecated 28 Feb 2026 per NPCI. There is no merchant-push. The payment target must be an optical code the customer scans. That is an industry boundary, not an engineering one.`**

**This section is the credibility engine of the video.** A judge who has watched forty demos has never seen one publish its own negative results with the confidence intervals attached.

---

### `4:32 – 5:00` — THE CLOSE (28 s)
**REGISTER A returns · pillarbox closes · room tone returns**

| t | Shot |
|---|---|
| 4:32 | Back to the nadir. Same mat, same clamp, same frame as 0:01. Empty. |
| 4:36 | Voice, over the empty mat: *"The camera did not move once in the last five minutes. The mat did not change. Six capabilities, one plane, one verb —"* |
| 4:42 | Six one-line cards over the still mat, ~2 s each: `goods cross the edge → bill` / `a hand covers a sigil → reveal` / `a sticker enters the plane → compare` / `a screen enters the plane → compare` / `a pen crosses a row → close` / `a frame is unreadable → reject` |
| 4:54 | Final card: **`It witnesses what happens on the plane. When it cannot see, it says so.`** Repo URL, licence, and the line: `every number in this video is on a held-out set. the exception list is in the repo.` |
| 4:59 | Cut to black on the last frame of room tone. |

---

## 4. THE HONEST LEDGER — what is what

| Capability | Status in the video | Where |
|---|---|---|
| **P0 crossing loop** | **LIVE TAKE** — unbroken, real-time, real test-mode money | 0:00–1:38 |
| **Abstention (unknown SKU)** | **LIVE TAKE** | 0:34–0:46 |
| **Warm enroll / price tap** | **LIVE TAKE** | 0:46–0:58 |
| **MUDRA — reveal + fist** | **LIVE TAKE**, with the trigger disclosed by the chip. QR is **pre-minted**; the gesture reveals, it does not mint. Panel is **150 mm**, not 120. | 1:05–1:29 |
| **MUDRA — mint/cancel proof** | **LIVE screen recording** (Razorpay dashboard, `created`→`paid`, `created`→`cancelled`) | 1:44–1:58 |
| **CHILLAR (paise nonce)** | **LIVE**, ledger-side only. No camera involved. | 1:53–1:58 |
| **Customer scan → green** | **LIVE**, Camera B, timecode-locked, same take. Disclosed: the nadir camera geometrically cannot show this. | 1:29–1:38 |
| **PEEL — payload layer** | **LIVE**, off the existing 720p stream at 58 mm stickers | 1:58–2:27 |
| **PEEL — module render** | **BENCH**, and labelled as a rendering of a verdict the string already made | 2:08–2:20 |
| **PEEL — wear/abrasion detection** | **README-ONLY.** Dead by construction behind the UEC gate. Stated, not hidden. | — |
| **PAKKA — ledger verdict (Tier 0)** | **LIVE**. Already built; it is what decides. | 2:47–2:56 |
| **PAKKA — screen class (Tier 1)** | **LIVE**, via the existing gallery + ~10 new non-billable rows | 2:32–2:39 |
| **PAKKA — amount read (Tier 2)** | **BENCH, GATED on G4/G5.** Cut entirely if the still is under ~3000 px. | 2:39–2:47 |
| **PAKKA — reference string / UTR** | **README-ONLY.** Optically dead. Shown as a refusal. | 4:04–4:32 |
| **KHATA LIFT — strike triage + AR lift** | **BENCH + MEASURED**, gated on G6 (≥40 px/row at 1440p) | 3:06–3:46 |
| **KHATA LIFT — digit OCR / amount read** | **README-ONLY.** Segmentation cliff at ~1.6 px of inter-digit gap. Not attempted on camera. | — |
| **SAAF** | **MEASURED still + paired A/B.** Invisible on camera and said to be. | 3:46–4:04 |
| **KAMPAN multi-frame fusion** | **README-ONLY**, published as a **measured negative result** | 4:04–4:32 |

**Five capabilities appear as live footage. Six appear in the video. Nothing appears that is not what the chip says it is.**

---

## 5. THE 15-SECOND SOCIAL CUT

**One shot. One prop family. One verb. No cut, no card until the last second, no explanation.**

Portrait, full-frame, nadir, cropped from the hero take. This is the only place the new-prop test is absolute — **nothing but packets and a hand may enter this frame.**

| t | Beat |
|---|---|
| **0.0–5.5 s** | Two packets slide across the exit arrow. Rupee glyphs paint on the wood in perspective as each crosses. Total climbs to `₹214.37`. |
| **5.5–6.5 s** | DONE. Counter goes amber. |
| **6.5–11.5 s** | Open palm settles flat on the mat. A QR **blooms outward from under the hand**, drawn occluded by the palm. The hand lifts. **The QR stays**, rock-steady, in plane, with the amount beside it. |
| **11.5–14.0 s** | The QR pulses **green**. |
| **14.0–15.0 s** | Card: **"It bills what leaves the shelf."** Handle. |

**Design notes:**
- **No fist.** The collapse is the better *argument* but without context it reads as a bug. Cut it.
- **No second camera, no customer, no scan.** The green pulse implies the payment without introducing a second person or a second device.
- **No text before 14 s.** The occlusion of the QR by the palm is the thing that makes people rewind — do not put a caption over it.
- **Audio: room tone only**, with one soft mechanical click on the crossing and one on the bloom. No music. Silence is the differentiator on a feed where everything has a track.
- **Loop point:** the last frame (green QR on the mat) and the first frame (empty mat) are the same framing. It loops cleanly.

**Alt 15 s cut, if G3 fails** (no UPI app scans an `rzp.io` URL): swap the green pulse for the **fist collapse**, and end on the bare mat. `0–5.5` crossings, `5.5–7` DONE, `7–11` bloom, `11–14` fist, QR shatters, mat bare, `14–15` card. Slightly darker, equally arresting, and it needs no payment rail to work.

---

## 6. SHOOTING ORDER AND THE FALLBACK LADDER

**Shoot in this order** — not video order. Bank the un-losable material first.

1. **E0, the dashboard screen recording.** Zero optics, zero lighting, zero props. Get it in the can on day one. If everything else fails, this plus the cold open is still a submission.
2. **E5, the refusals.** Screen captures and cards. Also unfailable.
3. **E4, the SAAF contact sheet.** A still.
4. **E1, PEEL.** Bench, controlled light, one sticker, matte lamination.
5. **E3, KHATA LIFT** — only if G6 passed.
6. **E2, PAKKA** — only Tier 0/Tier 1 unless G4 and G5 both passed.
7. **The hero take, last.** It is the hardest and it needs the most rehearsal. Budget 10+ takes.
8. **The 15 s cut is extracted from the best hero take.** Do not shoot it separately.

**Lighting doctrine, and it fixes three problems at once:** two small diffused LEDs at ±45°, and **kill the overhead tube for every take**. Specular from ±45° goes *away* from a nadir lens. This simultaneously (a) suppresses the hand shadow that inflates MUDRA's solidity, (b) removes the specular hotspot that kills PEEL's decode and PAKKA's read, and (c) is what a real kirana counter has anyway.

**Fallback ladder, per beat:**

| Beat | Most likely failure | Fallback, in order |
|---|---|---|
| **Crossings** | Lighting drift breaks the empty-mat reference | One-keypress re-baseline **between every take, as routine** |
| **MUDRA gesture** | Doesn't fire — shadow inflates solidity, or the dwell isn't met | (1) tap-to-arm, chip flips to `trigger: key`; (2) re-fit threshold live from a 20-shot calibration; (3) degrade to press-and-release on sigil occultation alone and *say so on screen* |
| **MUDRA false fire** | A sleeve mints unbidden | **Do not cut.** Close the fist, it hides, do it again. The recovery *is* the feature — and because the link is pre-minted and cancel is behind hold-to-confirm, it is financially inert |
| **Customer scan** | GPay won't take an `https` QR | Camera app → browser → checkout; or `qr_codes` API PNG pushed over LAN; or shoot merchant-side and cut to a separate scan close-up |
| **PEEL decode** | Glare on laminated vinyl | (1) matte-laminate the demo stickers; (2) the payload verdict needs no gate — show the verdict strip the instant a decode lands, let the render paint when it can; (3) 58 mm stickers, not 40 |
| **PEEL false accusation** | An unenrolled sticker binds to the nearest slot | **Distance threshold + one-to-one assignment. Build this before the enrolment UI.** An unbound code is GREY, never RED |
| **PAKKA read** | Glare on a face-up glossy screen | The abstention **is the shot.** `CANNOT READ — NOT GUESSING`, then slide the phone 3 cm into the printed box and retake. That demonstrates the placement box on camera instead of explaining it |
| **KHATA rows** | Fewer pixels than the bench predicted; half the rows silently don't lift | **Refuse to enter khata mode below 40 px/row, with `MOVE THE BOOK CLOSER` on screen.** A blocked mode is recoverable on camera; a half-populated page is not |
| **Anything, catastrophically** | — | `?replay=<bundle>`, already in the settings surface, **labelled on screen and in the README** |

---

## 7. THREE THINGS THAT MUST NOT APPEAR

1. **Green from anything but a signature-verified webhook.** No `GREEN*`, no "screen evidence, webhook pending" ring. A judge who was told green means a verified webhook, and then sees green appear because a template matched a photograph of a stranger's phone, has been handed the question that unravels everything.
2. **A claim that any feature *reads* something it verifies.** PAKKA verifies a known amount; it does not read a screen. KHATA finds rows and witnesses a stroke; it does not read a ledger. PEEL compares a string; it does not detect wear. Every one of these is a stronger sentence than the overclaim it replaces.
3. **A number in the video that is not on held-out data.** Including the ones that came back bad. Especially those.

---

## 8. THE ONE-LINE ANSWER TO THE NEW-PROP TEST

> **The hero take has one prop family and never cuts. The evidence half announces itself as evidence, changes aspect ratio, changes grade, changes light, and puts a mode chip on every frame. A new object inside a lab notebook is an exhibit. A new object inside the hero shot is a second demo. The hinge card at 1:38 is what tells the viewer which one they are looking at — and it costs six seconds.**



---

# PART IV — 8-DAY PLAN, CUT LADDER, HONESTY LAYER

## GAWAAH — 8-Day Plan, Cut Ladder, and Honesty Layer
**Dated 2026-08-28 · D1 = today · D8 = 2026-09-04 · solo · zero lines of code**

I read "the six" as: **the core billing loop** (the money path, which is the product) plus the five rescued features — **MUDRA, PAKKA, PEEL, KHATA LIFT, KAMPAN**. If the sixth was meant to be something else, the ladder below still holds; only the bottom rung changes.

---

## 0. The arithmetic, blunt

Sum the rescue authors' own estimates: ~14–17 days. Sum the adversarial verifiers' corrected estimates: **19–22 days.** Add the core loop, which nobody costed because it is not a rescue — plane engine, tracker, crossing predicate, gallery, brain, paisa, webhook verification, UI, exception taxonomy — realistically **4–5 days solo moving fast.**

Available: 8 days minus ~1 day of bench and ~1.5 days of shoot/README = **~5.5 build days.**

Even after cutting every feature to its verifier-approved reduced scope (core 4.5 + MUDRA 2 + KHATA 2.5 + PAKKA 0.75 + PEEL 1 + KAMPAN 0.5 = **11.25 days**), the plan is buying **2× more scope than time.** That is not a scheduling problem to be solved by working harder. It is a cutting problem, and the cut order has to be decided today, sober, not on Day 6 at 2am.

**What six features costs versus one.** One feature — the core loop, done properly — would get: a 250-placement frozen held-out set, calibrated thresholds, a published risk-coverage curve, a measured abstention rate, and an exception taxonomy with real counts. That is a submission that survives a hostile question from a judge who understands measurement.

Six features gets: **one real held-out set** (the core loop's), two cheap ones (PEEL, MUDRA-decode), and three features whose evidence is "a demonstration on one book / one counter / one afternoon." Every accuracy number outside the core loop will be n<200, single-writer, single-lighting, or synthetic. That is not a benchmark, and calling it one is the fastest way to lose.

**The deal you are making:** breadth in exchange for statistical power. It is defensible — but *only* if every feature states its own limit on screen, in its own words, unprompted. That is what Section 5 is for, and it is not garnish. It is the thing that makes the other five features honest instead of padding.

---

## 1. The cut ladder — decided now, not on Day 6

Cut from the bottom. Do not renegotiate.

| Rung | Item | Cost | Why it sits here |
|---|---|---|---|
| **1** | **Core loop + green rule** | 4.5d | No submission without it. Never cut, never descoped. |
| **2** | **CHILLAR** (paise nonce in `amount_paise`) | 2h | No camera, no OCR, one line in paisa. Deletes the join-key problem outright and makes the *already-built* ledger half strictly better. Highest value-per-hour in the entire plan. |
| **3** | **PEEL payload layer** | 0.75d | ~30 lines. Decode, compare string. 96/96 substitution recall, zero false positives, works off the existing 720p stream at 55–60mm stickers. No gate, no threshold, no tuning. |
| **4** | **KAMPAN extracted fixes** | 0.5d | Saturation guard, `edge_sigma` predicate fix, ArUco subpixel refinement. These are P0 bug fixes wearing a feature's name — they land on D1–D2 regardless. |
| **5** | **MUDRA render half** | 1d | QR blooms on the mat, anchored, ≥150mm mat-space, pre-minted. Deterministic. This is where the wow lives. |
| **6** | **PAKKA ROKO** (hold square) | 0.5d | State machine + existing painter. Produces the identical on-camera beat with zero OCR. |
| **7** | **MUDRA gesture half** | 1d | Real risk, real reward. Degrades cleanly to tap-to-arm. |
| **8** | **KHATA LIFT strike triage** | 2.5d | **First to cut.** Second prop class, forbidden in the main film by your own M2 constraint, needs 1440p capture, and rests on an unverified cultural assumption. |
| **9** | **PAKKA screen-amount OCR** | 2d | Cut unless the D1 probe passes with margin. Straddles the decode floor. |
| **10** | **PEEL module-grid overlay** | 0.5d | Pure cosmetics. It detects nothing the payload string doesn't. |

**Trigger rules.** If P0 has not frozen by end of D4 → cut rungs 8, 9, 10 immediately. If it has not frozen by end of D5 → cut rung 7 as well and MUDRA becomes tap-triggered. If it has not frozen by end of D6 → you are shipping rungs 1–6 and a README, and that is still a real submission.

---

## 2. Day-by-day, with hard gates

### D1 — Tue 28 Aug · **THE BENCH DAY. Zero product code.**

Twelve probes decide which features are physically possible. Six of them can save 1–5 days each. Run B1 and B5 first — they are the two that can kill the entire submission.

| # | Probe | Time | Gates |
|---|---|---|---|
| **B1** | `Mat.clone()` aliasing **in the browser.** Hold an empty-mat reference, clone, absdiff, `countNonZero`. | 15m | **CORE LOOP.** Reproduced in Node: clone aliases the buffer, absdiff returns 0, no crossing ever fires. Fix everywhere: `const dst = new cv.Mat(); src.copyTo(dst)`. Grep the tree for `.clone()` before you write line one. |
| **B2** | `cv.TERM_CRITERIA_COUNT` vs `cv.TermCriteria_COUNT` | 5m | Anything using ECC. The classic 4.x names are **undefined**; passing them yields `type=0` and `findTransformECC` throws at ecc.cpp:384. Fails silently as "the gesture never fires." |
| **B3** | Load the pinned opencv.js in Node, enumerate the `cv` namespace. | 20m | **Rig spec correctness.** AKAZE is **absent** (brief says verified present). `detectMarkers` is a method on `aruco_ArucoDetector`, not a free function. `createCLAHE` absent (CLAHE class present). SIFT, MOG2 absent. `QRCodeDetector` **present and free**. Correct the spec today; it is cited in the README. |
| **B4** | px/mm ground truth off the printed 20mm patch at 720p / 1080p / 1440p / 4K, and via `takePhoto()` — **decode the blob and measure its real width**, do not trust `getPhotoCapabilities().imageWidth.max`. | 30m | KHATA, PAKKA-OCR, PEEL, MUDRA sizing. Real optical sampling at 720p is **~1.8–2.2 px/mm**, not 2.83 — the rectified buffer is upsampling ~1.35×. Every mm threshold in every rescue inherits this. |
| **B5** | Mint a test-mode Payment Link with `notes.session_id`, pay it, inspect the **payment entity**. | 20m | **THE GREEN RULE.** Razorpay's docs never state that Payment Link `notes` propagate to the payment; they point at `reference_id` instead. If notes don't propagate, your green predicate is broken and this is a money-path emergency, not a feature question. |
| **B6** | Print an `https://rzp.io/i/...` QR. Scan with **GPay, PhonePe, Paytm.** | 10m | **Payment beat across three features.** This is not a `upi://` payload and not BharatQR. If the apps refuse it, fall back to `POST /v1/payments/qr_codes` → paisa fetches the PNG server-side → pushes bytes over LAN. (paisa must be online to mint anyway; and `qr_codes` has a `/close` endpoint that maps onto the fist beat.) |
| **B7** | Mint 5 links at odd paise (₹X.nn). Pay them. Confirm `amount_paise` in the mirror equals the minted value **5/5**. | 30m | CHILLAR. This is the direct empirical test of the conservation-law claim. If it fails, CHILLAR is wrong and you want to know today. |
| **B8** | A second person tries to scan the clamped, ceiling-facing screen past the clamp arm. | 20m | **Payment beat, physically.** Untested by everyone. If it is awkward, plan the two-camera cut now, not on shoot day. |
| **B9** | `POST /v1/payment_links/{id}/cancel` round trip → status `cancelled`. | 15m | MUDRA death beat. Confirm it only works in `issued` state (it cannot void a paid link — that is the safety property). |
| **B10** | ArUco `CORNER_REFINE_SUBPIX` on vs off; log reprojection error. | 20m | **The whole plane engine.** Default is `CORNER_REFINE_NONE`. One line, global accuracy win. |
| **B11** | Decode three real counter stickers. Record QR version and measured printed width. | 20m | PEEL. If they are v3–v5 the feature is nearly free at 720p. If v12–v14 at 40mm, you need 60mm+ demo stickers. |
| **B12** | Ruler on a real GPay/PhonePe success screen: measure the hero amount's cap height in mm. | 15m | PAKKA-OCR go/no-go. |
| **B13** | One photograph of one real bahi-khata page. Measure ruling pitch and inter-digit gap. | 30m | KHATA. Ruling <10mm at 1080p inverts the feature into the money-losing direction. |
| **B14** | Glare survey: nadir over a phone screen and a laminated sticker, under the actual counter light. | 30m | Four features. Build the ±45° diffused-LED rig and **kill the overhead tube**. This one mitigation covers MUDRA, PAKKA, PEEL and KHATA simultaneously. |

**D1 gate:** B1 and B5 both green, or you spend D2 on the money path's foundations instead of the pipeline. Publish the corrected rig spec (px/mm, A3-vs-A2, the opencv symbol list) into the README skeleton the same evening.

---

### D2 — Wed 29 Aug · Plane engine + segmentation
ArUco → homography (cached; the phone is clamped, so solve once and refresh only when ≥3 markers are visible with low reprojection error) → rectified buffer → maintained empty-mat reference → absdiff → threshold → morphologyEx → findContours → minAreaRect → tracker → crossing predicate. Subpixel corner refinement on. Ratio-based shadow suppression built **pre-emptively**, not as a fallback.

**Gate:** mat lock holds for 10 minutes unattended; a packet crossing the exit edge fires exactly one event; ≤1 spurious blob per 2000 empty frames in both lighting conditions.

### D3 — Thu 30 Aug · The money path, end to end
paisa as a separate process, separate OS user, sole key holder. Server-side re-run of the crossing predicate before minting. Payment Link create (**no `upi_link:true`** — it is Live-Mode-only and Android-only, and you are demoing in test mode). `short_url` → local QR render → warp through H⁻¹. Webhook receiver with HMAC verification. Green predicate: valid signature AND event in green set AND session match AND exact amount.

**Gate — the hardest one in the plan:** a real test-mode rupee moves and the counter goes green *on a signature-verified webhook*, with the router off between mint and scan. If this is not true at end of D3, cut rungs 8, 9, 10 tonight.

### D4 — Fri 31 Aug · Identity, abstention, CHILLAR, freeze
MobileCLIP-S0 on the brain, fired only on confirmed crossings. Gallery, top-k cosine, margin gates, AMBER on low margin. CHILLAR (2h). Then **capture the frozen held-out set**: ≥250 placements, 24 SKUs, 2 lighting × 2 hand-pairs, hashed and tagged **before** any threshold is tuned.

**Gate: P0 FREEZE.** From here the core loop only receives bug fixes. Nothing below rung 4 may touch it.

### D5 — Sat 1 Sep · MUDRA render + PEEL payload
MUDRA: pre-mint on DONE; render the QR into the mat plane at **≥150mm mat-space** (60mm gives the customer 1.05–2.53 px/module at 25–40cm, at or below ML Kit's 2px floor; 150mm gives 2.6–6.0 with margin), axis-aligned via the **local Jacobian** of H at the anchor, integer px/module, `INTER_NEAREST`, ecQ. PEEL: enroll payload strings per slot; distance-gated one-to-one binding; version-pinned enrollment records.

**Gate:** a real UPI app decodes the on-screen QR ≥95% at 25cm and ≥90% at 40cm across 3 handsets. PEEL flags a substitution and produces **zero REDs** across 20 genuine re-lays including one unenrolled sticker.

### D6 — Sun 2 Sep · MUDRA gesture + PAKKA ROKO
Solidity + circularity + deep-defect count on the **border-touching** contour, cropped to the pay panel. **Calibrate on the day, on real hands, under the shoot lighting** — the published thresholds (0.70/0.79) and the verifier's corrections (0.85/0.90) are both from synthetic silhouettes, and one verifier measured only a 0.072 gap under your own two-LED rig. Schmitt trigger + 400ms dwell + a visible UNCLEAR band. ROKO: one new session state, amber hold ring, goods do not cross until the webhook lands.

**Gate:** measured solidity gap ≥0.08 between the 95th percentile of OPEN and the 5th of FIST on 60 real frames → ship the gesture. Below 0.08 → **degrade to hold-to-reveal or tap-to-arm and say so on screen.** No tuning your way out; retune once, remeasure once, then degrade.

### D7 — Mon 3 Sep · KHATA (or cut) + first shoot
If B4 delivered ≥40px per ledger row and B13 showed a workable ruling: build strike triage (no OCR, tap-to-select-row, numpad for legacy amounts, 1440p capture mode with a preflight refuse-below-threshold). Otherwise **cut it and shoot instead.** Shoot the main take (one prop family — packets on the mat, nothing else), then the evidence half separately.

**Gate:** one clean unedited take of the core loop plus MUDRA. In the can by 20:00.

### D8 — Tue 4 Sep · Reshoot, README, measurement tables, submit
Reshoot whatever failed. Write the README with the honesty table (Section 5), the measurement tables (Section 6), the refusals section including KAMPAN's negative result, and the corrected rig spec. Ship with 3 hours of margin.

---

## 3. What each of the six will actually be, and the sentence it must say

The sentence is not a disclaimer bolted on. It goes **on screen during the take** (short form) and **verbatim in the README** (long form). If a feature cannot say its sentence truthfully, it does not ship.

### F1 — Core loop · **Working**
Real, measured, held-out. The only feature with statistical power.

> **"Green appears only when a signature-verified webhook arrives from Razorpay, for a payment target this session minted, for exactly this amount. Nothing on this screen turns green because a model was confident."**

Plus, permanently in the chrome: *"Abstention rate is printed next to accuracy, on the same screen, always."*

### F2 — MUDRA · **Render works; gesture is 50–75%**
The QR bloom, the in-plane anchoring, the occlusion by the hand mask, the cancel-on-fist dashboard cut — all deterministic. The hand classifier is the honest coin flip.

> **"The QR is an AR overlay anchored to the mat, not light projected onto wood. The link is minted when the basket is confirmed — the gesture only reveals it, so a misread hand can never mint money. The number on the strip is the hand's solidity, calibrated on this counter under this light. When it reads UNCLEAR it is declining to guess, not glitching."**

### F3 — PAKKA · **Ledger half works; screen-reading half is one field, or none**
CHILLAR and ROKO are real. The OCR is a day-1 gate that will probably fail.

> **"I do not read the transaction ID. At this camera height a UTR digit is about one pixel of stroke — it is not in the signal, and no model recovers it. I use the amount instead: the last two paise are a nonce this counter generated for this basket, so the amount is the one field that cannot be rewritten between your app and my ledger. The screen never turns the light green. The webhook does."**

If the OCR ships at all, it must add: *"On a screen I cannot read cleanly I say so and hold the goods. I never say the amount is wrong unless two independent crops agree."*

### F4 — PEEL · **Substitution detection works; wear detection does not exist**
The module-grid diff and the UEC safety gate are mutually exclusive — UEC=1.0 means zero error correction consumed, which means Hamming=0 by construction. Zero of 72 abrasion captures pass the gate. The grid draws; the payload string detects.

> **"I compare the payload string of the sticker in front of me to the string I enrolled. I do not detect fraud and I do not detect wear — I detect that this is not the code you showed me. A sticker I never enrolled is grey, never red. The red pattern is how I show you where it differs; the string is what caught it."**

### F5 — KHATA LIFT · **Triage works; reading does not exist**
0.974 row-level accuracy on 352 held-out rows of real ink with zero open debts hidden — genuinely the best-evidenced number in the whole rescue set. It reads a mark, not a script.

> **"It never reads a name, an amount, or a date. It reads one thing: whether a row is struck out. Age is row order, because a ledger is append-only. On rows GAWAAH itself billed, the amount is exact. On every older row, it is one tap."**

And the cultural caveat, non-negotiable: **"From today, cross out what's settled and the camera sees it"** — never *"it reads your existing ledger."* Those are different products and only one of them is true.

### F6 — KAMPAN · **A published negative result, plus three extracted bug fixes**
This is not a feature and pretending otherwise is the one thing here that would mislead a judge. Multi-frame fusion bought +4 to +8 points over simply picking the sharpest frame — inside the noise. The blur gate it proposed already exists in the spec.

> **"KAMPAN is a negative result and it is in the repo as one. I built the registration stack, measured it against a fair baseline, and frame fusion bought four to eight points over simply choosing the sharpest frame — inside my confidence intervals. What survived is five lines: a saturation guard, because sharpness scoring actively prefers the glare-blown frame. A blown specular edge maximises Laplacian variance."**

A measured refusal, published with its numbers, is worth more here than a sixth mediocre feature. Put it in the refusals section next to the DiffTSR split-screen — **do not replace that split-screen**; it is a scripted refusal beat and swapping it for a contact sheet is a straight loss.

---

## 4. Measurement plan — who gets a held-out set and who gets a caveat

Frozen means: captured **before** any threshold is chosen, hashed, tagged, committed, and never looked at during tuning.

| Feature | Evidence class | n | Headline metric | Ship gate |
|---|---|---|---|---|
| **Core loop** | **Frozen held-out** | ≥250 placements, 24 SKUs, 2 lighting × 2 hand-pairs | Recall@1 on decided placements **and** abstention rate, reported together | Overcharge ₹ per ₹1,000 billed; false-charge rate |
| **MUDRA — decode** | **Frozen held-out** | 60 scans, 3 unseen handsets × 4 angles × 5 reps | First-try decode rate + median time-to-decode | ≥95% at 25cm, ≥90% at 40cm |
| **MUDRA — gesture** | **Frozen held-out** | 120 frames min, 3 lighting incl. hard shadow | Balanced accuracy **and, separately, false-mint rate** | gap ≥0.08; false-open ≤2%; else degrade to tap |
| **PEEL** | **Frozen held-out** | 20 genuine + 20 attack, real photographs | **Zero false REDs** on genuine — not "few", zero | Any false RED → payload-only, grid disabled |
| **CHILLAR** | Live API test | 5 real payments | `amount_paise` mirror-match | 5/5 or the mechanism is wrong |
| **KHATA LIFT** | **Demonstration** | 5 pages, 1 book, 1 writer | Row accuracy + **dangerous-error count** (open debt hidden) separately | ≥0.90 accuracy, ≤1% dangerous |
| **PAKKA — OCR** | **Demonstration** | 40 stills if it ships at all | Exact-match, abstention, **false-mismatch rate** | false-mismatch ≤2% is the hard ceiling |
| **KAMPAN** | Bench measurement, reported as negative | n=80/cell | Paired delta with CI | Published as-is, whatever it says |

**The caveat sentence for the demonstration tier**, which must appear verbatim under every KHATA and PAKKA-OCR number:

> **"One book, one writer, one counter, one afternoon. This is a demonstration, not a benchmark, and the number below has no confidence interval worth quoting."**

**Two metrics that matter more than accuracy and must be reported separately everywhere:** the *dangerous-direction* error rate (an open debt hidden, a fist read as open, a genuine sticker called red, a confidently-wrong amount) and the *abstention* rate. A system that declines to answer and is never wrong beats one that always answers — and saying so with numbers is the entire pitch.

---

## 5. Risk register

| # | Risk | P | Cost | Detect | Mitigation / fallback |
|---|---|---|---|---|---|
| **R1** | `clone()` aliases the empty-mat reference → **no crossing ever fires** | Med-High | **Total** | B1, 15 min | Ban `.clone()` repo-wide; `new cv.Mat(); src.copyTo(dst)`. Grep before writing line one. |
| **R2** | Payment Link `notes` do not propagate to the payment entity → **green rule broken** | Unknown | **Total** | B5, 20 min | Switch the binding to `reference_id` (max 40 chars, documented for reconciliation). Same predicate, different key. |
| **R3** | UPI apps refuse an `https://rzp.io/...` QR → payment beat dies in 3 features | Med | High | B6, 10 min | paisa calls `qr_codes`, fetches the PNG server-side, pushes bytes over LAN. `/close` maps onto the fist beat. |
| **R4** | Customer cannot physically reach the ceiling-facing clamped screen | Med | High | B8, 20 min | Two-camera cut (merchant-side beat + separate scan close-up) is a legitimate edit. Or tilt the rig 10–15° for the payment beat only — decode survives to 50°. |
| **R5** | **Glare.** Nadir camera over any glossy surface is a mirror aimed at the ceiling tube | **High** | Degrades 4 features | B14 | Matte lamination, two diffused LEDs at ±45°, **overhead tube off**. Textbook copy-stand anti-specular. Saturation guard rejects any frame >2% near-clipping. |
| **R6** | Shadow inflates the absdiff silhouette → gesture fails or false-fires | High | MUDRA gesture | D6 calibration | Ratio-based suppression (shadow scales texture multiplicatively; an occluder replaces it — MOG2's detector is absent, so hand-roll it). Abstention band absorbs the rest. |
| **R7** | **Orphaned payment.** A local intent close leaves the link payable — the customer pays real money and gets no green | Low | **Reputational, on camera, in front of a payments judge** | Design review | Death is **`/cancel` only**, never a local close. Cancel works only in `issued` state, so it can never void a completed payment. |
| **R8** | Handset caps at 720p stills | Med | Kills PAKKA-OCR and KHATA-1440p | B4 | Both are rungs 8–9. Cut them and recover 4.5 days. This is a *good* outcome on D1. |
| **R9** | A **false RED / false accusation** on camera — genuine sticker, genuine customer, genuine shopkeeper | Med | Worse than a missing feature | D5 gate | Structural, not tuned: RED requires a prior enrollment on that slot with a distance-gated bind. Unenrolled → grey. No decode → "cannot read." |
| **R10** | `takePhoto()` mutes the video track → AE reconverges → **empty-mat reference invalidated** → spurious contours mid-take | Med | P0-adjacent | D5 | Any takePhoto path must re-baseline the reference immediately after. One-keypress re-baseline between every take as routine, not as repair. |
| **R11** | **Schedule.** P0 not frozen by D4 | High | Everything | D4 gate | The ladder. Cut rungs 8–10 that night without discussion. |
| **R12** | Six features → no feature but the core has real statistical power | **Certain** | The honesty thesis | — | Section 5's sentences and Section 6's tiering. Name the demonstration tier as a demonstration, loudly, in your own voice, before anyone asks. |

---

## 6. The three sentences that decide whether this holds up

1. **"I do not classify the image. I ask the ledger."** — the core loop's claim, and it is true.
2. **"Here is the abstention rate, next to the accuracy, on the same screen."** — the thing no competitor will show.
3. **"This one is a demonstration on one book. This one has a 250-placement held-out set. They are not the same kind of number and I am not going to present them as though they were."** — the sentence that makes shipping six features defensible instead of padded.

If you say all three and mean them, six half-working features reads as a builder who knows exactly where the edges are. If you skip the third, it reads as five demos and a product.
