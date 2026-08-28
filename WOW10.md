# THE WOW 10
### 89 agents · 24 concepts · judged by "would a stranger stop scrolling, sound off, 2 seconds"

---

## THE BIGGEST SIGNAL IN THE DATA

Eight ideators worked **independently**, with different lenses, never seeing each other's output.

**Six of the twenty-four ideas landed on the same thing: point a phone at a shop's UPI QR sticker and expose that it has been swapped.**

PEEL, ASLI, JAAL, and three separate NAZARs. Different lenses — "point-and-know", "hands in air", "living picture", "caught in the act", "agent with a body" — all converged.

That is not repetition. That is six independent searches finding the same peak. I have merged them into one idea below, and used the freed slots for genuinely distinct concepts.

**Why it keeps winning:** a tampered QR is *visually identical by design*. Delhi Police, 22 Dec 2025 — a 19-year-old diverted **₹1.40 lakh** from one Chandni Chowk boutique; **100+ AI-edited QR codes** were seized off his phone; he told police he kept the code's appearance unchanged on purpose. Khajuraho, Jan 2025 — stickers pasted over genuine ones overnight at ~6 places including petrol pumps, caught only because one customer happened to read the payee name and saw *"Chhotu Tiwari"*.

Nobody on earth has ever seen **which pixels of a QR code are the lie.** That is the shot.

---

## THE RANKING

| # | Name | Track | The shot, in one line | Wow | Build risk |
|---|---|---|---|---|---|
| **1** | **PEEL** | 02 Risk | Four QR stickers on a counter. Three bracket green. The fourth slams red and ~40 squares inside the code ignite one by one. A thumb peels it off — the real one underneath is green. | 🔥🔥🔥🔥🔥 | **Low** |
| **2** | **JUGNU** | 02 Risk | A success screen looks totally normal to the eye. Point a phone at it: **LIVE**. Point it at a screenshot of the same screen: **NO SIGNAL**. | 🔥🔥🔥🔥🔥 | **High** |
| **3** | **NAZAR** | 04 Finance | A phone taped over a shop counter. Items leave the shelf, a rupee total floats on the counter *in perspective*, a QR appears, money lands, everything flushes green. | 🔥🔥🔥🔥🔥 | Medium |
| 4 | **KHATA LIFT** | 03 Recovery | Point at a handwritten udhaar ledger. Amounts **lift off the paper** into the air, sorted by age. Pinch the oldest → a real payment link. | 🔥🔥🔥🔥 | Med-High |
| 5 | **GOONJ** | 03 Recovery | Network dies mid-sale. The two phones **chirp at each other**. Sale completes offline. Settles when signal returns. | 🔥🔥🔥🔥 | Medium |
| 6 | **KAMPAN** | 04 Finance | A faded thermal receipt nobody can read. Just let your hand **shake**. 100 frames stack. Text appears that was in none of them. | 🔥🔥🔥🔥 | Medium |
| 7 | **COUNTER** | 01 Growth | Put items on a counter with no scanner, no POS. They **price themselves**. A QR for the exact amount lies on the counter. | 🔥🔥🔥🔥 | Med-High |
| 8 | **GAWAH** | 02 Risk | Open a returned parcel on camera. Items get glowing silhouettes as they come out. A **ghost of the missing item** rises from the empty carton. | 🔥🔥🔥🔥 | Medium |
| 9 | **MUDRA** | 01 Growth | Pinch a price in the air. Open your hand. A **real UPI QR snaps onto your palm**. Close your fist — it dies. | 🔥🔥🔥🔥🔥 | **High** |
| 10 | **SUTRA** | 04 Finance | 60 real invoices on a desk. Glowing **threads snap through the air** between matching lines. The unmatched ones sit alone, pulsing red. | 🔥🔥🔥🔥 | Medium |

---
---

# THE TOP 3, IN FULL

---

## 🥇 1 — PEEL · *the QR X-ray*
**Track 02 — AI Risk Manager (defense only)**

> Point your phone at any shop's UPI QR sticker. In under a second it tells you whose bank account it *actually* pays — and lights up the exact squares of the code that were tampered with.

### The shot, frame by frame

Full-bleed phone camera. One continuous handheld take. No UI, no chrome, no words.

1. A hand pans along a grubby kirana counter. Four paper UPI stickers taped to it.
2. As each sticker enters frame, a **four-cornered bracket snaps onto it in correct perspective** and stays welded to the paper while the phone keeps moving.
3. Floating above each: the decoded payee name in big white type. `SHARMA GENERAL STORE` — green. Again green. Again green.
4. The fourth bracket **slams red**. The name reads `chhotu.tiwari@ybl`.
5. Inside the QR itself, **~40 individual black modules ignite red one after another**, in a constellation — showing precisely which squares of the code are the lie.
6. A thumb and forefinger enter frame and **peel the fake sticker off**. The genuine QR underneath snaps green.

Five seconds. Zero words. Zero audio needed.

### Why it looks impossible

Every viewer knows from their own life that a QR is an unreadable black-and-white mess. Watching a camera render *the difference between the real code and the forgery* as a glowing constellation, in perspective, on a moving piece of paper, contradicts their direct experience.

And **the peel proves it wasn't staged.** The truth was physically underneath the whole time.

### The problem, with real numbers

- **56.86 crore QR codes** deployed across **~6.5 crore merchants**. That is the exposed surface.
- Delhi, 13 Dec 2025: **₹1.40 lakh** diverted from a Chandni Chowk boutique in two payments. Arrest 22 Dec: 100+ edited and original QRs on the phone, made with AI image-editing apps.
- Khajuraho, Jan 2025: overnight sticker-pasting at ~6 establishments including petrol pumps.
- Srinagar field study of 102 merchants, Jul 2026: **57.8% already lost money** to fraudulent digital transactions · **60.8% display QRs as loose paper stickers** · **41.2% display them outside the counter in blind spots** · **90.2% did not know the 1930 helpline exists.**
- The incumbent answer, Soundbox, reaches only ~10–14% of merchants at ₹100–150/month plus hardware — and it catches a swapped QR **only by silence**, which a busy counter never notices.

**The merchant's current detection mechanism is: nothing happens, and nobody notices.**

### How it actually works

**Fast loop — 30–60fps, in a browser tab, fully on-device.**

`getUserMedia` → canvas → **`Sec-ant/zxing-wasm`** (reader-only, ~1.04 MiB, MIT) or **`nimiq/qr-scanner`** (2.9k ⭐, MIT, 59.3kB min, 25 scans/sec default). Both return **`cornerPoints`** — four real corners in the live frame. *That is the whole unlock*, the same primitive the pothole detector uses.

The `upi://pay?pa=...&pn=...` payload is parsed with **plain string code — no model**.

Four corners + the three finder patterns → `cv.findHomography` → rectify the photographed sticker to a canonical square → sample the module grid at the code's version pitch.

**THE X-RAY:** regenerate the merchant's *registered* payload as a QR bitmap locally at the same version/ECC/mask, **XOR the two module matrices**, ignite the differing modules.

That is **exact arithmetic, not inference.** This is the single best answer to "where did you choose not to use AI" that exists in this whole list.

**The verdict is three deterministic tiers:**

| Condition | Verdict |
|---|---|
| decoded VPA == registered VPA | **GREEN** |
| valid UPI handle not in merchant's registry | **RED** |
| undecodable or partial | **AMBER HOLD** — authorises nothing |

**Slow loop — 2–4Hz.** PP-OCRv5 mobile (det 4.7MB @ 28–58ms CPU, rec 16MB @ 5–21ms) reads the printed shop name off the standee as a soft cross-check. A small self-trained ONNX classifier looks for the physical sticker-over-sticker cue — double border, laminate seam, hairline shadow — as a **suspicion-only** signal that never gates the verdict.

**Auto-responder (bounded):** on a confirmed mismatch on a code the merchant owns, offer *one* action — `POST /v1/payments/qr_codes/:id/close` then `POST /v1/payments/qr_codes` to mint a replacement — behind a two-step confirm plus a reason code, every step appended to a hash-chained local log and reconciled against `qr_code.created` / `credited` webhooks.

### Features

1. Live multi-QR bracket tracking in perspective, several codes at once
2. Payee VPA decode + registry check, under 1 second
3. **The module-XOR constellation** — the money shot
4. Three-tier verdict with an explicit abstain state
5. Printed-shop-name OCR cross-check
6. Physical sticker-over-sticker classifier (suspicion only, never gates)
7. Bounded auto-responder: close compromised QR, mint replacement, two-step confirm
8. Hash-chained local audit log, reconciled against Razorpay webhooks
9. Merchant QR registry, enrolled from the Razorpay API
10. Works offline after first load — the registry is cached
11. Torch toggle + on-screen tilt coach for bad shop lighting
12. Zero install: a web page, opens from a QR code
13. Held-out evaluation harness, one command
14. "Whole code replaced" fallback verdict when versions differ

### The honest metric

**240 held-out photographs the tuning pass never saw:** 60 genuine Razorpay test-mode QR standees, 60 tampered (payload-swapped reprints *plus* physically pasted overlays), each at two angles × two lighting conditions, **shot on a different phone**.

Reported: precision/recall of the RED verdict · decode rate vs angle (0–60°) and illuminance · median time-to-verdict in ms · and — **reported separately so it cannot hide inside the headline** — the precision/recall/FPR of the suspicion-only classifier.

**Expected honest shape:** near-perfect precision on payload mismatch, *because that is a decode and not a guess*. Materially lower, openly published recall on physical sticker-over-sticker detection.

### What could break on camera

| Risk | Reality | Fallback |
|---|---|---|
| Glare on laminated sticker under a tube light | Real, will happen | Torch toggle + tilt coach. A truly bad shop light can still beat it. |
| **Module XOR only defined if same version/ECC/mask** | If the fraudster's code is a different version, the XOR is meaningless | Falls back to **"WHOLE CODE REPLACED"** — a *stronger* red. Say this on camera rather than hide it. |
| Native `BarcodeDetector` is Chrome-Android only | Safari hides it behind a flag | `zxing-wasm` ponyfill ships as the default path |
| opencv.js is 3.33MB over Indian 4G | Cold load | Lazy-load *after* first decode — bracket appears instantly, X-ray arrives a beat later |

**The deliberate on-camera failure beat:** a thumb covers half the sticker → bracket goes dashed amber → a confidence ring closes down → **no verdict, no action.** That is your "one failure handled gracefully," and it is honest.

---

## 🥈 2 — JUGNU (जुगनू, *firefly*)
**Track 02 — AI Risk Manager (defense only)**

> The payment-success screen secretly flickers a signed code no human eye can see. A shopkeeper points their phone at the customer's phone and knows in one second whether that green screen is live, a screenshot, or a fake app.

### The shot

A customer's phone showing `Payment Successful ₹46,000`. To your eye: completely normal. Nothing flickers. Nothing looks odd.

The shopkeeper points their phone at it. **`LIVE ✓`**

Then the shopkeeper points at a **screenshot of that exact same screen** — pixel-identical to a human. **`NO SIGNAL ✗`**

Then a **video replay** of the real screen. **`STALE ✗`**

Same image. Three different truths.

### Why it looks impossible

The two screens are *visually identical*. The viewer's own eyes tell them there is no difference. The phone disagrees, and is right. There is no visible mechanism — the information is hiding in plain sight, below the threshold of human vision.

This is the highest "**wait, HOW?**" score in the entire list.

### How it works

**(A) The emitter** — a ~40-line drop-in web component for the Razorpay checkout/status page.

The success screen is split into 2 (or 4) large **antiphase** regions whose luminance is modulated **±2/255 in OKLAB L** (perceptually flat, zero chroma shift). 15Hz symbol rate on a 60Hz panel (4 display frames per symbol), differential BPSK, an 11-bit Barker sync prefix, and a **32-bit payload = truncated HMAC-SHA256 over (payment_id ‖ amount_paise ‖ 30-second epoch bucket)** under a per-merchant key. Full transmission ≈2.5s, looping.

**Antiphase is the load-bearing trick:** the receiving camera's auto-exposure and any 50Hz mains flicker hit both regions equally and **cancel in the difference** `d[t] = L_A − L_B`.

**(B) The receiver** — a PWA, no install, on the merchant's existing Android phone.

`getUserMedia` at 60fps, exposure and white balance **locked** via `applyConstraints({exposureMode:'manual'})`. Screen quad found deterministically with OpenCV.js: **adaptive local threshold by blur-compare** — never a global threshold, which is the documented lesson from the browser AR-Sudoku post-mortem — largest quadrilateral contour, `findHomography`, tracked frame to frame. A fine-tuned YOLO26n (17.6ms median = 57fps on M3 Pro CPU) seeds the quad when contours die under glare. Warp to canonical rectangle → integrate mean luminance over A and B → resample to symbol rate → correlate for Barker sync → differential-decode 32 bits.

**Three-state verdict:** `LIVE` (tag valid + epoch fresh + payment_id credited to *this* merchant + amount matches) · `NO SIGNAL` (a screen is present but carries no carrier — screenshot, fake app, any non-Razorpay app) · `STALE` (valid tag, expired epoch = a video replay).

### Honest risks — this is the high-risk pick

- **Auto-exposure is the number one killer.** Antiphase saves the differential, but if AE clips, nothing survives. `exposureMode:'manual'` support is **uneven across Android OEMs** and must be measured per device.
- **Rolling shutter** samples screen rows at different times — which is exactly why the regions are large and spatial. But a steep tilt shrinks region B and craters SNR. Captures below a rectified-area threshold are **rejected with a "come closer" hint** rather than guessed at.
- **Screen brightness below ~30% kills it.** The honest on-camera fix is asking the customer to raise brightness — a graceful failure that looks fine.
- **±2/255 may be faintly visible** on some OLED panels at low brightness due to panel dithering. Run a small human A/B, publish the result, drop to ±1 with doubled symbol time if it fails.

**Held-out set: 400 captures** — 100 live carrier screens, 100 screenshots *of those same screens*, 100 video replays, 100 no-carrier third-party screens. Across 4 phone models (including a sub-₹12,000 Android), 3 lighting conditions, 3 angles. Bit error rate vs distance (20/40/60/80cm) and vs brightness (25/50/100%). **The decode rate below 30% brightness is reported as a failure, not buried.**

---

## 🥉 3 — NAZAR (नज़र) · *the counter that watches the shelf*
**Track 04 — AI Finance Controller**, with a Track 02 defense sub-loop

> A phone propped over a shop counter watches what physically leaves the shelf, mints a real Razorpay QR for exactly that amount, and turns the counter green only when the money actually lands.

*Highest overall judge score: **93/120**, stop-the-scroll **8.7/10** — the best of any concept.*

### The shot

A phone propped over a real counter. A hand puts down a biscuit packet, a soap bar, a shampoo sachet.

Each item gets a box. **A rupee amount floats above each one, painted flat onto the counter surface in correct perspective** — not overlaid on the screen, *lying on the counter*.

The items slide across a line toward the customer. A running total assembles itself. **A real UPI QR unfurls onto the counter plane.**

The customer scans. The webhook fires. **Every box flushes green inside the live frame.**

### How it works

**Fast loop, 30–57fps.** YOLO26n ONNX — **measured 17.6ms median / 18.8ms p90 = 57fps** on plain CPU on an M3 Pro at 640×640 fp32. Ultralytics publishes **3.2ms** with the ANE on iPhone 17 Pro. Fine-tuned to a single generic class: `counter item`.

**SKU identity is a few-shot embedding gallery, not a trained detector.** The merchant photographs each SKU once from three angles at enrollment; SigLIP embeddings go into a local index; each detected crop is a nearest-neighbour lookup **with an explicit reject margin**. Unknown → amber dashed box, **excluded from the total**.

**Geometry is pure OpenCV.js.** A printed ArUco tile taped to the counter gives `findHomography`/`solvePnP` a rock-solid plane. The floating rupee glyphs are projected *through* the homography — the atomic14 trick: project the cell centre through H and draw text, **never warp pixels every frame**.

**The sell event is a deterministic directional line-crossing test** on the tracked centroid. An item counts only when it crosses the counter's sell-line *toward the customer*. Drag it back and it decrements. **No model in the money decision.**

**Money out:** one tap creates a genuine Razorpay QR (`type: upi_qr`, `usage: single_use`, `fixed_amount: true`, `payment_amount` in paise, `close_by` set) via the official `razorpay-mcp-server`. **Money in:** the `qr_code.credited` webhook flushes the boxes green.

### The honest risk

**Near-identical SKUs will collide** in embedding space — 200g vs 500g Parle-G is the canonical failure. Mitigation: a real-world size prior from the calibrated counter plane, plus aggressive abstention. *"I would rather be visibly unsure than confidently wrong on camera."* The confusion matrix gets published, not hidden.

Second risk: **reading a UTR off a glossy phone screen under a shop tube light** is the single most fragile step in the Track-02 sub-loop.

**Three held-out numbers:** sell-line crossing precision/recall across 60+ item movements *including adversarial cases* (items dragged back, two moved together, a hand occluding mid-crossing) · reconciliation match rate with every unmatched row itemised by exception class and the rupees sitting in the exception bucket · screen-read exact-match accuracy on 100 held-out photos across 4+ phone models, **reported separately for UTR and amount because they fail differently**.

---
---

# IDEAS 4–10

### 4 · KHATA LIFT — the paper ledger that collects its own debts
**Track 03 Revenue Recovery**

Point a phone at a shopkeeper's handwritten *udhaar* ledger. The amounts **physically lift off the paper** into the air, age-sorted by how long the money has been rotting. Pinch the oldest row → it becomes a real Razorpay payment link.

**Best structural feature:** the most impressive layer has *no model in it at all.* Page-corner detection → `findHomography` → row segmentation by morphological ruled-line extraction and horizontal projection profile, over an **adaptive local threshold by blur-compare**. That entire snap-flat-and-rake-rows sequence — the part that looks like magic — is **zero AI**. MediaPipe pinch (4.49M npm downloads/week) is the most reliable interaction in this whole space.

**The honest risk, stated by the ideator:** *"Handwritten Devanagari is the risk and I will not pretend otherwise."* Open-weight handwritten Devanagari recognition is weak. PaddleOCR-VL claims 96.3% Hindi on OmniDocBench but that is printed text. **Mitigation: confirm-before-send on every row, and publish the character error rate.**

Also — no citable national rupee figure for outstanding udhaar exists, and the ideator explicitly refused to invent one. The measurable *is* the product: rupees actually recovered from a real batch of rows.

---

### 5 · GOONJ (गूँज, *resonance*) — the sale that survives no network
**Track 03 Revenue Recovery**

The network dies mid-sale. The two phones just **chirp at each other**. The sale completes offline as a cryptographically bounded promise, and settles the instant either side gets one bar of signal.

**Tech:** `ggwave` (MIT, 7,840⭐) — multi-frequency FSK with Reed-Solomon ECC, F0 = 1875Hz, dF = 46.875Hz, 96 frequencies across a 4.5kHz band, 6 simultaneous tones, **8–16 bytes/sec**. Payload is an 8-byte challenge: 4-byte nonce + 4-byte truncated digest of (merchant_id ‖ amount_paise). Customer side is also a PWA — **no install, no pairing, no Bluetooth permission.** A deterministic 800ms network probe (DNS + socket, no model) triggers offline mode.

**Context:** UPI did **23.66 billion transactions worth ₹29.9 lakh crore in July 2026**. Dead zones are everywhere — basement shops, market interiors, autos. ToneTag alone claims 3 million merchants at ₹100–150/month *plus* ₹1,300–1,500 of hardware. **This needs zero hardware.**

**The honest ceiling:** 8–16 bytes/sec means a full handshake is **2.5–4 seconds**. The GIF compresses that; the video must state the real number on screen. A noisy shop at 65–75dBA is the genuine risk, and `ggwave` makes *no* reliability or range claims — so you measure it yourself and **publish the failure envelope**.

---

### 6 · KAMPAN (कंपन, *tremor*) — your shaking hand is the sensor
**Track 04 Finance Controller**

A faded, crumpled thermal receipt in a dim shop. Nobody can read it. **You just let your hand shake.** A hundred frames stack into text that was legible in none of them individually.

**This is Google's Super-Res-Zoom with the human as the shift rig.** Your natural physiological tremor (~8–12Hz, sub-pixel to a few pixels at counter distance) supplies the sampling diversity. No OIS, no tripod, no hardware.

**Registration is 100% deterministic, 100% not-AI:** OpenCV.js ORB/AKAZE + `findHomography` with RANSAC per frame against a reference, refined sub-pixel by `findTransformECC`. Frames rejected on a variance-of-Laplacian blur gate. **Exposure and white balance locked** so frames are radiometrically comparable.

**Problem scale:** central tax authorities detected **₹74,782 crore of fake input tax credit across 30,162 cases in FY26**, up from ₹36,373 crore across 9,190 cases in FY24 — **more than doubling in two years.** Every detection requires actually reading a document and matching it to a record.

**The beautiful failure mode:** if your hand is *too steady* there is no sub-pixel diversity and it degenerates into plain frame-averaging. Google hits this too and injects deliberate OIS movement, which a browser cannot. So it **detects near-zero inter-frame displacement and asks you to move slightly** rather than silently returning a worse result.

---

### 7 · COUNTER — the shop that prices itself
**Track 01 Growth & Agentic Commerce**

A kirana counter with no barcode scanner and no POS. Put the items down. **They price themselves.** A UPI QR for the exact amount appears lying on the counter — which an AI buyer agent can also check out against.

**The problem is precise:** the overwhelming majority of those 56.86 crore QRs are **open-amount** — the customer types the amount themselves. That produces short-payments, wrong-amount disputes, **zero line-item data, no basket analytics, no catalog — and therefore nothing an AI buyer can transact against at all.** A card machine is ~₹7,000 vs ₹1,300–1,500 for a soundbox; a scanner POS is out of reach entirely.

**SKU identity without training:** CLIP/DINOv2 embedding + cosine nearest-neighbour against the merchant's own enrolled gallery.

**The killer risk, honestly:** near-identical SKUs — same brand, different gram weight, identical artwork — **will** collide. 200g vs 500g Parle-G is the canonical failure. Mitigations are a real-world size prior from the calibrated plane and aggressive abstention, **and the confusion matrix gets published rather than hidden.** Occlusion and stacking also drop items.

---

### 8 · GAWAH (गवाह, *witness*) — the witness inside the box
**Track 02 Risk Manager**

Open a returned courier parcel under a camera. Every item gets a **glowing silhouette cut around it** as it comes out. When something that was supposed to be there isn't, **a translucent ghost of the missing item rises out of the empty carton.**

**Tech that makes it feel like magic:** MobileSAM — **9.66M params, 12ms end-to-end** vs SAM's 615M/456ms, Apache-2.0 with documented ONNX export. The silhouette appears *on the same frame as the tap*. The mask then tracks through the hand's rotation with **EdgeTAM** (16FPS on iPhone 15 Pro Max, 22× faster than SAM 2, Apache-2.0 on code *and* checkpoints).

**Identity is closed-set, never open-vocabulary** — the order tells you what should be in the box, so you match against a known list instead of asking a model to name things. That is a real AI-judgment answer.

**Problem scale:** Myntra lost **₹1.1 crore in Bengaluru alone** to a refund scam run out of Jaipur — ~5,529 fraudulent orders in Bengaluru, an estimated **₹50 crore nationwide** — method: *"order 10 pairs of shoes, claim only 5 arrived."* Meesho was cheated of ₹5.5 crore by a Surat gang. Underneath: **COD is 60–65% of Indian e-commerce, 25–30% of COD orders end in RTO vs 2–3% for prepaid**, social commerce RTO runs 35–40%.

Both sellers and buyers *already film unboxings*. Nobody has productised it.

---

### 9 · MUDRA (मुद्रा) — the palm that is the payment terminal
**Track 01 Growth & Agentic Commerce**

Pinch a price into the air with two fingers. Open your hand. **A real Razorpay UPI QR for exactly that amount snaps onto the skin of your palm.** The customer scans your hand. The second you close your fist, the QR is dead.

**The purest "what did they invent" reaction in the list.** Also the second-riskiest.

`@mediapipe/tasks-vision` HandLandmarker (7.8MB model; **12.27ms GPU / 17.12ms CPU on a Pixel 6**) in a Web Worker with OffscreenCanvas — Google's own docs warn `detectForVideo()` blocks the UI thread. Gestures hand-rolled from raw landmarks rather than importing GestureRecognizer, which saves an 8.4MB bundle and lets you own the thresholds. One Euro filtering so the overlay never jitters.

**The load-bearing risk, stated plainly by the ideator:** *"decodability, and I will not pretend otherwise."* A UPI payload lands around QR version 7–10 — **a lot of modules to fit on a palm.** At 720p a palm 50cm from a webcam gives ~250–350px square. Workable, but little margin. **Motion blur is the killer** — a hand that drifts during the scan fails.

Mitigation is a "hold steady" ring. But be clear-eyed: this one either lands spectacularly or fails on camera.

---

### 10 · SUTRA (सूत्र, *thread*) — reconciliation on a desk covered in real paper
**Track 04 Finance Controller**

Spread sixty real printed invoices and bank statements across a desk. Point a phone at it. **Glowing threads snap through the air between the lines that match** — leaving the unmatched ones alone, pulsing red.

**The fast loop has zero machine learning.** Green channel as cheap greyscale → adaptive local threshold by blur-compare → connected components to find sheet-sized quads → Manhattan-distance corner heuristic → `findHomography` per sheet. Project each line's box centre through the homography; **never warp pixels.** It follows a public post-mortem rather than inventing an approach.

PP-OCRv5 mobile on a 2–4Hz slow loop. PaddleOCR is Apache-2.0, 88.3k⭐. The match engine is ordinary deterministic software with **no research risk** — which is exactly the point, and exactly what Track 04's bar asks for.

Naturally produces the **honest exception list** the track demands: the sheets no thread ever reaches.

---
---

# THE THREE RANKINGS DISAGREE

This is the most useful part of the analysis.

| Rank | Pure WOW | Chance of winning the panel | Lowest build risk |
|---|---|---|---|
| 1 | **JUGNU** | **PEEL** | **PEEL** |
| 2 | **MUDRA** | **NAZAR** | **SUTRA** |
| 3 | **PEEL** | **SUTRA** | **GAWAH** |
| 4 | KHATA LIFT | GAWAH | KAMPAN |
| 5 | NAZAR | KAMPAN | NAZAR |
| … | … | … | … |
| last | SUTRA | MUDRA | **MUDRA / JUGNU** |

**What the disagreement tells you:**

**MUDRA and JUGNU top the wow ranking and bottom the risk ranking.** They are the two that could produce either the best submission in the competition or a video where the thing visibly doesn't work. MUDRA depends on a QR being decodable off curved skin at 250–350px with motion blur. JUGNU depends on `exposureMode:'manual'` working on your specific Android.

**SUTRA is the inverse** — very safe, real Track-04 fit, but it is the one shot that a hostile judge could call "a nice visualisation of a spreadsheet."

**PEEL is the only idea in the top 3 of all three rankings.** That is the finding.

It is #1 on panel-fit and #1 on low risk while still being #3 on pure wow — and its wow is only "third" because two ideas above it are gambles. Its core operation is **exact arithmetic on a decoded payload**, so it cannot hallucinate; the demo's centrepiece is a XOR, not a model. The peel-the-sticker beat proves it live. And the "one failure handled gracefully" beat is already designed.

---

# BEST COMBINATIONS

**A. PEEL + JUGNU → one verifier, two attack surfaces.**
The fake QR *on the counter* and the fake success screen *in the customer's hand* are the same merchant's same bad afternoon. One app, one camera, two verdicts. **Build PEEL first and completely; add JUGNU only if it works by day 6.** JUGNU becomes upside, not a dependency.

**B. PEEL + NAZAR → the shop that watches itself.**
PEEL is the handheld check. NAZAR is the same engine with the phone taped to the wall, watching your standee all day and screaming the moment someone pastes over it. Same code, two form factors, and the second one is a *product*, not a demo.

**C. KHATA LIFT + GOONJ → the shop with no network and no computer.**
Paper ledger in, payment link out, and when the network dies the phones chirp. Deeply India-native. But this is two hard builds — only if you have real time.

---

# WHAT I WOULD BUILD

## PEEL, with NAZAR as the wall-mounted mode.

1. It is the **only idea in the top 3 of wow, panel-fit, and low-risk simultaneously.**
2. Six independent ideators converged on this problem. That is the strongest signal in 24 concepts.
3. The wow shot is **exact arithmetic** — a XOR of two module matrices. It cannot hallucinate, and it will work on camera every single take.
4. "Where did you choose *not* to use AI?" has a devastating answer: **the entire verdict path.** The decode is a decode, the comparison is a XOR, the payload parse is a regex. The ML is confined to a suspicion-only overlay that is reported separately and never gates a decision.
5. The problem is **dated, prosecuted, and citable** — Delhi 22 Dec 2025, ₹1.40 lakh, 100+ seized codes — against a 56.86 crore surface with a 57.8%-already-hit merchant base.
6. Track 02's bar is *"measured precision and recall on a held-out test set"* and *"strictly defense-only"*. PEEL hits both literally: 240 held-out photos, and it only ever *reads* a code.
7. The peel-the-sticker ending proves on camera that nothing was staged.
8. Zero install, opens from a QR — **so you can put a QR in the pitch video and the judge can try it while reviewing.**

**Runner-up: NAZAR (the counter that watches the shelf)** — highest raw judge score at 93/120 and the best stop-the-scroll at 8.7, but the SKU-collision risk is real and it needs an enrollment step before anything works.

---

# THE TECH YOU ACTUALLY NEED

**QR decode with corner points** (the core primitive)
- `Sec-ant/zxing-wasm` — reader-only ~1.04 MiB, MIT
- `nimiq/qr-scanner` — 2.9k⭐, MIT, 59.3kB min / 16.3kB gz, 25 scans/sec default
- `Sec-ant/barcode-detector` — BarcodeDetector ponyfill over zxing-cpp WASM
- Native `BarcodeDetector` — Chrome Android 83+ only; Safari behind a flag. **Feature-detect, never assume.**

**Geometry** — OpenCV.js, 3.33MB brotli. Verified exported in the default JS whitelist: `findHomography`, `solvePnP`, `warpPerspective`, `Rodrigues`, `aruco_ArucoDetector.detectMarkers`.

**Detection** — YOLO26n ONNX. **Measured 17.6ms median / 18.8ms p90 = 57fps** on M3 Pro CPU EP at 640×640 fp32. 3.2ms on iPhone 17 Pro ANE (INT8) per Ultralytics.

**OCR** — PP-OCRv5 mobile: det 4.7MB @ 28–58ms CPU, rec 16MB @ 5–21ms CPU → ~35–80ms end to end. PaddleOCR Apache-2.0, 88.3k⭐.

**Segmentation** — MobileSAM 9.66M params, 12ms, Apache-2.0, ONNX export. EdgeTAM for tracking, 16FPS iPhone 15 Pro Max, Apache-2.0 on code *and* checkpoints.

**Hands** — `@mediapipe/tasks-vision` v1.0.1 HandLandmarker, 7,819,105 bytes, 12.27ms GPU / 17.12ms CPU on Pixel 6. 4.49M npm downloads/week.

**Sound** — `ggwave`, MIT, 7,840⭐, three live browser demos.

**Razorpay** — `razorpay/razorpay-mcp-server` (230⭐, Go, MIT, already cloned in `reference/`). QR: `POST /v1/payments/qr_codes` with `type: upi_qr`, `usage: single_use`, `fixed_amount: true`, `payment_amount` in paise, `close_by`. Webhooks: `qr_code.created`, `qr_code.credited`.

---

# THE DEMO RULES

From dissecting 21 viral demos. These are not style preferences — they are mechanics.

**1 · The physical world.** 18 of 21 viral demos involve a real object, room, or body. The three that don't substitute a **human-made mark** — a wobbly scribble, visibly hand-authored. Never typed text.

**2 · One verb.** Test: can you caption the whole demo in one sentence with **exactly one verb**? *"The sign changes language." "The tray prices itself."* If your sentence needs an "and" or a "then", it is two demos and will spread as zero. **A demo that shows five things shows zero.**

**3 · A hand in frame within the first 15 frames.** 19 of 21 have one. It does four jobs: **scale**, **liveness** (a screen recording cannot contain a hand — it kills the "is this a mockup?" reflex instantly), **agency**, and **identification** ("that could be my hand" is the actual share trigger). **Keep the hand in frame during the result beat too** — that is precisely when the viewer decides whether to believe you.

**4 · Overlay, never a separate window.** A separate window forces a saccade; during that eye-jump the viewer loses the causal link. Same-frame output registers as **one event** instead of two. tldraw's decisive improvement over draw-a-ui was simply *moving the output onto the input canvas* — thousands of posts within 72 hours.

**5 · One continuous unedited take.** Demos that got cut got a virality spike followed by a *viral debunking wave*. For a hackathon judged on failure recovery by people who read the repo, **one-take is simultaneously more viral and safer. There is no tradeoff here.**

**6 · Visible risk.** The strongest demos contain a moment where failure is obviously possible and doesn't happen. **Risk converts a video into a test.** A demo with no visible failure mode reads as pre-recorded even when it isn't.

**7 · Changing pixels > 40% of frame.** Under ~10% and nothing happened.

**8 · Under 400ms.** Anything slower reads as "loading", which is the opposite of magic. MediaPipe at 17ms and YOLO26n at 1.7ms are inside budget. **A cloud VLM round-trip at 1.5s is not.** Choose models by the shot's latency budget, not by benchmark scores.

**Lighting, for Indian shops specifically:**
- **Adaptive LOCAL thresholding by blur-compare. Never a global threshold.** The single documented fix from the browser AR-Sudoku post-mortem. Bad lighting is the norm in Indian shops, not the exception.
- **Torch OFF on glossy screens and laminated stickers** — it makes glare worse. Torch is for matte print only, and is Chrome-Android only.
- **Watch 50Hz mains flicker.** Indian tube and cheap LED lighting beating against a rolling shutter puts horizontal luminance bands on everything. Shutter no slower than 1/60.
- **Lock exposure and white balance** before any multi-frame trick. Support is uneven across Android OEMs — feature-detect with a fallback.
- **Printed ArUco markers degrade far more gracefully under poor light than feature-based tracking.** Strong argument for markers in any Indian-context demo. But **disguise your fiducials** — a bare black-and-white ArUco tile tells the viewer "there is a trick, and here it is." Print it as a shop-branded counter mat.

**Framing:** Full-bleed, no chrome, no cursor, no address bar. **Vertical or square, never landscape** — the feed is a phone. Punch in so the subject is 65–70% of frame. **Locked-off camera + moving objects beats moving camera + static objects** — a static tripod with a hand entering frame proves the hand caused the effect, and kills motion blur and autofocus hunt in one decision. **Never point the camera at a monitor** — text recognition measurably degrades on screens. (The one exception is a screen-verifier like JUGNU, where looking at a screen *is* the product — and then you report the extraction rate honestly rather than hiding it inside an accuracy number.)
