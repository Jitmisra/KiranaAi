# GAWAAH — PRODUCT REQUIREMENTS DOCUMENT
### गवाह — *the counter's witness* · Razorpay AI Buildathon 2026 · Track 01
> **"It bills what leaves the shelf."**

*Assembled from 23 agents / 3.6M tokens: 10 recon briefs, 4 competing theses, 4 adversarial attacks, 4 design documents. The final assembler truncated; this document is stitched from the surviving design agents' full output, which is the primary source and is more precise than any summary of it.*

---

## HOW THIS DOCUMENT IS ORDERED

| Part | What it covers | Source |
|---|---|---|
| **I** | Product, thesis, features, enrollment, abstention register, metric targets, cut list | design:features |
| **II** | System architecture | design:architecture |
| **III** | The demo and the video | design:demo |
| **IV** | Measurement, privacy, and the 8-day plan | design:metrics-privacy-plan |
| **V** | Repos, models, datasets, and open questions | assembler tail |

---




---

# PART I — PRODUCT & FEATURE SPECIFICATION

## GAWAAH
### गवाह — *the counter's witness*
**Track 01 — AI Growth & Agentic Commerce** (second clause: *make a merchant transactable by an AI buyer end to end*)

> Naming note: not NAZAR — *nazar* carries the evil-eye connotation in Hindi and is wrong on a shop. Not anything with "Kirana" (Kirana AI, YC S25) or "Vision" (Snapbizz SnapVision).

---

## 0. Decision summary — read this and nothing else

| Field | Decision |
|---|---|
| **Product** | GAWAAH — a printed mat, a phone clamped above it, and a ledger of what physically left the shop |
| **Track** | 01. Chosen and closed. The track says *grow revenue **OR** make a merchant transactable by an AI buyer end to end.* We satisfy the second clause literally. No further track deliberation. |
| **On-screen caption (one verb, never changes)** | **"It bills what leaves the shelf."** |
| **Positioning line (spoken once, slide one)** | *"This shop has a perfect record of what it was **paid** and no record at all of what it **sold**. This is the missing column."* |
| **Physical resolution** | The **TAKHTI** — an A3 ArUco mat. Phone **nadir** (straight down) at 45–55 cm on a ₹400 gooseneck clamp. |
| **Runtime, disclosed on screen** | Phone = eye + display + mouth. A laptop on the same LAN = brain (v2: ₹6,000 mini-PC on the counter). No cloud in the money path. |
| **P0 models** | One small embedder. That is it. No object detector. |
| **Hard state** | Today: **zero lines of product code, no git repo.** 28 reference checkouts and ~235 KB of planning prose. Verified this session. |

Three claims from the research are **retired as overstatements** and must never appear: *"zero learned parameters between the shelf and the rupee"*, *"false-charge rate is structurally zero"*, and *"the shopkeeper never does data entry."* Corrected forms in §14.

---

## 1. The one verb, and why there are two sentences

The demo purist and the problem-taste skeptic are both right and they govern **different artifacts**. Stop trying to write one sentence that does both jobs.

- **CAPTION** (burned into the video from 0:03, never changes, six words, one verb, no conjunction): *It bills what leaves the shelf.* It is a **mechanism** sentence, not a benefit claim. The verb is visible: a rupee glyph blooms on the wood, a QR appears, the counter goes green.
- **POSITIONING** (slide one, README first line, "what does it solve" field): *the missing column.* Never claim speed. At three items and ₹214 there is no labour to save, and our own honest amber rate proves it. We sell the **record**, which nothing on that counter produces today: the soundbox confirms the credit and can never see its cause; Khatabook records what the shopkeeper types; Udaan and Jumbotail know only what he bought.

---

## 2. Unifying logic — three mechanisms, not a theme

"A camera at the counter" is a category and categories spread as zero. These three are falsifiable in under a minute each.

**M1 — ONE CAUSAL CHAIN (code).** README acceptance test, grep-checkable: *every source file in `src/` sits on the path from "a stable object leaves the TAKHTI across the exit edge" to "a signature-verified `qr_code.credited` / `payment_link.paid` lands."* If a subsystem is not on that path, it was cut. A judge can check this in ninety seconds.

**M2 — ONE PROP FAMILY (film).** Written into the PRD as a hard constraint, not a preference: **filmed footage contains exactly one prop class — packets on one mat — plus two anti-props (a person who is absent from the masked buffer, a router that gets switched off).** Replace "one viewfinder, no mode switch" (an engineer's continuity argument that viewers cannot perceive) with "one prop family" (what viewers actually count). The moment a customer's phone screen, a peelable sticker, a thermal receipt or a banknote enters the frame, the count goes from one to two, and two spreads as zero as reliably as six.

**M3 — ONE MISSING COLUMN (product).** The cut rule, stated as a rule so the cut list is *derived* rather than asserted: **a capability ships only if it fills or defends the SOLD column.** Item identity fills it. Metric footprint defends it. Amber defends it by refusing to fill it wrongly. The audit log proves it. The agent feed is that column serialised. Palm QRs, luminance HMACs, handwritten ledgers, banknote counting and receipt super-resolution fill nothing in it. That is why they are gone — a rule, not taste.

---

## 3. The TAKHTI — one printed sheet that resolves three fatal problems

This is the single most important design decision in the document and it is what makes the build fit in 8 days.

**Physical:** A3 (297 × 420 mm), four ArUco markers at the corners (`DICT_4X4_50`), a printed 20 mm-square scale patch for verification, and a printed arrow marking the **exit edge** (toward the bag). Taped to the counter. Phone clamped **nadir** at 45–55 cm.

**Five jobs, one sheet:**

| Job | How |
|---|---|
| Fiducial | `detectMarkers` on a 320×240 greyscale pass |
| Metric plane | `getPerspectiveTransform` → fixed **840 × 1188 px rectified buffer at 2 px/mm**. Everything downstream sees only this. |
| Measurement surface | The object is **at rest on the plane** when measured. Parallax term = 0. |
| Privacy quad | The only buffer that survives the function call is the rectified mat crop. At 45 cm nadir a human cannot be inside it. |
| Sell-event boundary | Directional exit across the mat's far edge — `sv.LineZone`, `minimum_crossing_threshold=3` (verified: `reference/supervision/src/supervision/detection/line_zone.py:99`, `crossing_history_length = max(2, threshold+1)` at :116) |

**What this kills, explicitly:**

1. **The metric-footprint tiebreak was geometrically unsound.** A homography is metric only for points *on* the plane. In the previously-scripted demo the packet is held in a hand 10–15 cm above the counter while it crosses the line: at H≈45 cm and h=15 cm, area inflates by (H/(H−h))² ≈ **1.9×**, and the entire Parle-G 200 g/500 g signal is ~2.0×. The crown-jewel invention failed to one question — *"at what height was the packet?"* Placing the item on the mat makes the measurement exact. This is the same constraint that put Mashgin on the surviving side of the camera-checkout graveyard.
2. **The privacy beat was geometrically inverted.** "Hold your face over the counter and it goes black" is **false** — anything between camera and plane projects *inside* the image-space quad. Scripting it would have been a live failure or a lie, in the one section whose entire value is honesty. The nadir + rectified-crop architecture makes the true claim stronger: *nothing outside the mat is ever decoded, embedded, hashed or written, and the evidence frame stored against every rupee is a mat crop that cannot contain a person.*
3. **The AABB-is-not-a-footprint bug.** On the rectified plane we take `findContours` → `minAreaRect` → an **oriented** box. A 45° rotation costs nothing.

**Display keeps the magic.** All arithmetic happens in rectified space; the *preview* is the live camera feed with glyphs warped back through **H⁻¹** so the rupee amount is painted in perspective on the wood. The preview is a viewfinder — never recorded, never a tensor, never persisted. Say exactly that; do not claim the preview does not exist.

**Cost of the trade, stated on camera:** the shopkeeper puts the item down for ~400 ms before sweeping it into the bag. That is a real behaviour change and we own it.

---

## 4. Architecture

**Tier 0 — the money path. No model output reaches money unreviewed.**
ArUco detect → `getPerspectiveTransform` → rectified mat → `absdiff` vs maintained empty-mat reference → `threshold` → `morphologyEx` → `findContours` → `minAreaRect` → stability gate → mm arithmetic → `LineZone` directional exit → integer paise arithmetic → Razorpay mint → **HMAC webhook signature verify** → exact `payment_id` + amount match → green.

**Tier 1 — perception. Exactly one model.** A small image embedder (MobileCLIP-S0 int8 ≈ 11.9 MB, or DINOv2-small int8 ≈ 24.4 MB — chosen by the ablation in §11) over a bounded 24-SKU gallery, run **once per placement**, not per frame. It *proposes* a family. It never sets a price alone.

**There is no object detector in P0.** On a locked-off nadir camera over a static rectified mat, background subtraction is better than a detector: it gives oriented masks (which the measurement requires and a box detector cannot supply), runs at 200+ fps, downloads nothing, has no ORT-Web operator-coverage risk, and has no AGPL question. *"I deleted the object detector"* is a stronger criterion-3 exhibit than any of the eleven legal refusals. RF-DETR-Nano ships in P1 **as a published comparison**, not as a dependency.

**Tier 2 — batch, off the clock.** Nightly clustering of the day's amber crops to propose catalog rows; a VLM writes only the `description` field. Latency does not exist here and being wrong is cheap and reviewable. Nothing in Tier 2 can touch a rupee.

**What runs where — disclosed on screen at 0:10 of the video, not hidden:**
> *"Phone is the eye. This box is the brain, on the same wifi. v2 is a ₹6,000 mini-PC on the counter. No cloud in the money path."*

Ten seconds. It inoculates the single largest credibility hole in the whole package: a 4 GB Android 11 handset does not run an embedder + capture pipeline at usable framerate, and a shot list saying "no laptop in frame" beside an architecture section saying "the brain is a laptop" is the one honesty failure this submission cannot survive.

**One audit line, one schema, every module:**
```
{ts_monotonic, module, mat_crop_sha256, rectified_h, contour_mm, long_edge_mm,
 embed_top1, embed_top2, margin, reject_outcome, deterministic_rule_fired,
 money_action, gate_state, ledger_source: webhook|poll, capability_tier,
 razorpay_entity_id, webhook_event_id, human_override}
```
Click any ledger line → the app seeks to that mat crop with the decision inputs overlaid. One day of work; satisfies the audit-trail bar of four tracks.

---

## 5. Feature specification

### P0 — ships or the submission does not exist (Days 1–5)

| # | Feature | One-line justification |
|---|---|---|
| P0.1 | **TAKHTI plane engine** — detect → rectify → 2 px/mm buffer → mask-at-grab | Five jobs from one sheet; every other feature stands on it |
| P0.2 | **Perspective glyph renderer** (H⁻¹ warp of rupee amounts onto the wood) | The thumbnail, the first three seconds, and one of only two genuine "wait, HOW" moments left |
| P0.3 | **Classical placement detector** (absdiff → morph → contours → minAreaRect → 5-frame stability gate) | Deletes a day of fine-tuning, ~30 MB of load, the AGPL question and the ORT-Web risk — and is a criterion-3 exhibit |
| P0.4 | **Metric measurement** (long edge + area in mm on the plane, calibrated against the printed 20 mm patch every frame) | The variant discriminant, now geometrically valid; self-verifying |
| P0.5 | **24-SKU few-shot gallery + explicit reject margin** (θ = top1−top2 margin, φ = min similarity, τ = footprint tolerance) | Identity proposes; three published thresholds dispose |
| P0.6 | **Deterministic sell event** — `LineZone` directional exit across the mat edge, `minimum_crossing_threshold=3` | No model decides that money moves; 30 auditable lines with an upstream test suite |
| P0.7 | **Uncounted-crossing instrumentation** — two counters: *detected-but-never-counted* and *exited-with-no-tracker_id* | `line_zone.py:170` returns silently when `tracker_id is None`; a dropped track is an uncounted **sale** — a money bug in a vision bug's clothes |
| P0.8 | **Contestable live total + tap-to-revert** | The exact inversion of Amazon's stated cause of death; preserves the shopkeeper's authority instead of replacing it; criterion 4 as a gesture |
| P0.9 | **Razorpay money path** — Payment Link (UPI) primary, `upi_qr` as upgrade; `single_use`, `fixed_amount`, `payment_amount` in paise, `close_by`, `notes` = session id + audit-line hash | Links need no on-demand activation and fire real webhooks; removes the largest uncontrolled dependency from the critical path |
| P0.10 | **Green only on a signature-verified webhook** whose amount matches, with a labelled `fetch_payments` poll fallback (`ledger_source` recorded) | Never green on mint, on render, or on optimism — and the fallback is disclosed, not hidden |
| P0.11 | **Offline queue / AMBER PENDING** | Every money *decision* is already local; only the mint and the webhook need network. The most realistic failure in the actual market |
| P0.12 | **Amber lane + amber-as-enrollment** (unknown → excluded → one tap on price → green forever after) | The honesty mechanic, the catalog-building mechanic, and the best four seconds of the video, in one component |
| P0.13 | **KAALA DABBA** — append-only audit log with mat-crop SHA-256, click-a-line-jump-to-frame | *"Show me why you charged ₹240"* answered by pointing at a frame |
| P0.14 | **AWAAZ (minimal)** — pre-rendered Hindi lexicon, concatenated at runtime; speaks **item names and the amber earcon only** | No vocoder in the path; and it deliberately does **not** announce credits, because that is the judges' own soundbox's job at 110 dB |
| P0.15 | **Frozen held-out set + `make bench`** | Where criterion 2 is actually scored, and the insurance that the demo cannot fail on venue wifi |
| P0.16 | **Capability tier as an audit field** (measured fps, backend, thread count, warmup) | Every money action explainable down to the silicon — free, no UI |
| P0.17 | **Degrade-to-manual** — typed amount → real Payment Link, always reachable | There is always a path to money. Best single sentence available for the field the organisers read first |

### P1 — ships if Day 5 checkpoint is clean (Days 6–7)

| # | Feature | Justification |
|---|---|---|
| P1.1 | **Reserve-then-verify + shop-side MCP server** (`search_shelf`, `get_item`, `check_stock`, `create_cart`, `checkout`), composed with `razorpay-mcp-server` **deployed locally** (`close_qr_code` is ❌ on the hosted remote — verified, README:48) | ~1.5 days of server work, **zero camera risk**, and the only artifact a judge can still interact with a week later. Track 01's literal second clause. |
| P1.2 | **Availability-evidence feed** (ACP/Google-local shape: `pickup_method: reserve`, `pickup_SLA: same_day`) with `last_seen_on_shelf` + TTL that deterministically flips `is_eligible_checkout` to false | A catalog that revokes its own rows. No published spec (ACP, UCP, AP2, Merchant Center, schema.org) has this. |
| P1.3 | **Price observations, secondary** — mode of settled amounts with `payment_ids`, K≥3 stopping rule, `price_status: unconfirmed` below K | Kept, **demoted**: MRP is printed on the packet and is a legal ceiling, so citing payments to prove a packaged price is elaborate proof of a non-fact. Availability is the invention; price is the corroboration. |
| P1.4 | **Sticker registry** — merchant photographs **every** payment sticker on the counter once (Paytm, PhonePe, BharatPe, Razorpay); store VPA + rectified module grid | Fixes the multi-rail false-RED landmine *and* replaces PEEL's payload generator with a photo diff |
| P1.5 | **Detector ablation** — RF-DETR-Nano vs classical, published table | Turns a cut into evidence of judgment |
| P1.6 | **Low-light stopping rule** — measured lux floor below which auto-commit is refused | Track bars ask for stopping rules; this is one you can plot |
| P1.7 | **Interleaving exception class** — "a second customer's item entered an open session", tracked and rated | Admitting the hardest structural limitation is worth more than any accuracy number |

### P2 — README roadmap prose only, dated, nothing shipped

`PAKKA` OCR half · full PEEL module diff · `/.well-known/ucp` + signed JWS feed · generated bilingual compliance card · thermal-governor UI · undo scrub bar · additional languages · Hindi ASR of any kind · browser-only inference tier.

---

## 6. Core loop

```
IDLE ─ mat empty, reference frame fresh, counter chrome amber
  │
  ├─ object appears on mat → MEASURING (≤5 frames)
  │     hand still overlapping? wait. timeout 3s → AMBER
  │     object touching mat edge? → "poora rakhiye" → no measurement
  │
  ├─ stable → MEASURE (long edge mm) → CANDIDATE SET (footprint ±τ)
  │     → EMBED once → rank
  │        margin ≥ θ AND sim ≥ φ  → PRICED: glyph blooms beside it, voice says the name
  │        else                    → AMBER: no glyph, two-tone earcon, never a guessed name
  │
  ├─ object exits across the EXIT edge (debounce 3) → COMMITTED to basket, total climbs
  ├─ object exits across any OTHER edge             → cancelled, never committed
  ├─ tap any glyph                                  → that line reverts, logged as human_override
  │
  └─ tap DONE → brain RE-RUNS the exit predicate on the submitted geometry
        → Razorpay Payment Link / QR minted for the exact paise total
        → counter AMBER (awaiting settlement — nothing is authorised)
        → signature-verified webhook, amount matches → GREEN, voice speaks the item count
           amount mismatch → RED HOLD
           no network      → AMBER PENDING, intent queued, billing continues locally
```

**The client computes; the brain adjudicates.** Every money action ships the homography, the four mat corners, the contour, the exit timestamps and the 64×64 mat thumbnail. A compromised display client cannot mint.

---

## 7. Onboarding & enrollment — the hidden killer, specified

### 7.1 Rig (once, ~4 minutes)
Print the TAKHTI on A3 (a fallback A4 layout ships too). Tape it down with the exit arrow pointing at the bag. Clamp the phone on the gooseneck at 45–55 cm, **straight down**. The app shows a live "mat lock" indicator: all four markers detected, mm/px error against the printed 20 mm patch under 1.5 %, tilt under 8°. It refuses to leave setup until locked. *This screen is also the best possible proof the geometry is real.*

### 7.2 Pairing (once, ~30 seconds)
The brain serves the app and prints a URL **and a QR** on its own screen. The phone opens the URL — or scans the QR with the phone camera using the `zxing-wasm` reader already checked out. Thematically correct: the product's first act is reading a QR. Pairing token is a LAN-scoped nonce; no cloud.

### 7.3 Razorpay connect (once, ~6 minutes) — **do this in hour one of Day 0**
1. Paste test `key_id` / `key_secret` into the brain's local setup page. **Never on the phone.** Keys live in one process.
2. Brain starts a `cloudflared` quick tunnel, displays the webhook URL, and links straight to the Razorpay webhook settings page. Copy-paste, select `payment_link.paid`, `payment.captured`, `qr_code.created`, `qr_code.credited`, `qr_code.closed`, set the secret.
3. **Handshake test**: setup page fires a `create_payment_link_upi` for ₹1, shows a live "waiting for webhook" spinner, and will not let you finish until a **signature-verified** event lands. The onboarding flow *is* the day-0 risk mitigation.
4. **Ledger backfill**: `fetch_all_payments` for the last 30 days seeds the local mirror.
5. **Storage discipline**: persist only `{payment_id, amount_paise, status, created_at, HMAC(utr), HMAC(vpa)}` with a salt in the local keystore. `email`, `contact`, raw `vpa` and the entire `card` object are **dropped on receipt**. Exact lookup works identically on a hash.

### 7.4 Sticker registry (P1, once, ~90 seconds)
Photograph **every** payment sticker on the counter, one at a time, on the mat. Decode → store VPA + rectified module grid. This is not optional garnish: without it, a rule of the form *"valid UPI handle, not in the registry → RED"* accuses the shopkeeper's own legitimate PhonePe sticker of fraud, out loud, in Hindi, in front of a queue, on first run.

### 7.5 SKU enrollment — two paths, and the second one is the product

**COLD ENROLL (seeding, ~15 s per SKU).** Place the item on the mat. The app auto-captures 8 crops as you nudge/rotate it (it prompts: *"thoda ghumaiye"*), measures the footprint, and asks for a price on a numeric keypad. Done. 24 SKUs ≈ 7 minutes.

**WARM ENROLL (the real loop, 4 s).** An unknown item goes amber mid-sale, is excluded from the total, the shopkeeper taps the price once, and the crops already captured during that placement become the gallery entry. The item is green forever after. **The claim must be stated precisely:** *he does not maintain a catalog — he confirms a price once, on an item he was already selling.* Do not say "the shopkeeper never types"; the imprecision matters, because the Khatabook/OkCredit retention lesson is the whole reason this design is right.

**COLLISION GUARD, at enroll time, when fixing is free.** On every enroll, compute similarity to every existing entry. If the nearest neighbour is inside the danger band, the app **refuses to complete** and demands a disambiguation capture: *"yeh Parle-G 200g jaisa lagta hai (0.94) — dono ek saath rakhiye."* Both are then measured on the plane and the footprint separation is recorded with the pair. Failure surfaced at enrollment, not at the till.

### 7.6 Test-set capture protocol (Day 3, **before** the matcher is finished)
Enrollment and evaluation are **different physical sessions on different days**. Capture is **scripted**: a printed running order of SKUs means placements are auto-labelled from the script, then spot-checked — this converts ~4 hours of labelling into ~40 minutes and is the reason the set actually gets built. Freeze with a git tag, a SHA-256 manifest and a date, pushed before any threshold is tuned.

---

## 8. Settings (one screen, twelve controls, nothing else)

Language (hi / en) · mat size (A3/A4) · exit edge (left/right/top/bottom) · commit mode (auto vs tap-to-commit) · audio (names + amber / silent) · lux floor · θ, φ, τ (shown with the measured operating point; editable behind a "you are changing money behaviour" confirm) · TTL hours · K observations · brain address · replay mode (`?replay=<bundle>`) · **Export & wipe** (one tap, deletes gallery, ledger mirror, audit log).

---

## 9. State machine & failure states

| State | Chrome | Money authorised | Trigger out |
|---|---|---|---|
| `SETUP` | grey | none | mat lock achieved |
| `IDLE` | amber | none | object appears |
| `MEASURING` | amber | none | stability gate passes / 3 s timeout |
| `PRICED` | white glyph | none | exit crossing |
| `AMBER` | hatched outline | none, **excluded from total** | tap price → enroll, or exit → logged unpriced |
| `BASKET_OPEN` | amber total | none | DONE tap |
| `AWAITING_SETTLEMENT` | amber + QR | link minted, bounded | webhook |
| `PENDING_OFFLINE` | amber + queue count | **none** | connectivity restored |
| `PAID` | green flood | settled | new placement |
| `AMOUNT_MISMATCH` | red hold | none | manual resolution, logged |
| `MAT_LOST` | grey freeze | none, total frozen | markers re-detected |
| `BRAIN_LOST` | grey freeze | none, events buffered locally | reconnect + replay |
| `DEGRADED` | yellow chrome + reason string on screen | commit requires tap | p95 recovers |
| `FROZEN_TOTAL` | amber + "check the counter" | none | shopkeeper acknowledges |

**Named recoveries, each demonstrable:** network drop → queue drains. Incoming phone call / screen-off → `visibilitychange` re-acquires wake lock, replays missed audio as a catch-up chime, session intact. Brain restart → phone buffers, replays, dedupes on session id. Webhook never arrives → poll fallback fires at 3 s intervals and stamps `ledger_source: poll`.

---

## 10. Abstention register — every place the system refuses rather than guesses

This is the criterion-3 centrepiece and it is a shipped artifact (`ABSTENTIONS.md`), each row with its trigger, its state, and its measured rate on the frozen set.

**Perception**
1. Top1−top2 margin < θ, or top1 similarity < φ → **AMBER**, excluded from total.
2. Two candidates inside both the appearance margin **and** the footprint tolerance (the identical-size, different-flavour sachet pair) → **AMBER**. Correct behaviour is 100 % amber and it is reported as a result, not confessed as a caveat.
3. Object not stable on the plane for 5 frames → no measurement.
4. Object overlapping the mat boundary → refuse to measure (*"poora rakhiye"*).
5. Hand contour still overlapping the object → wait, then amber.
6. Two overlapping components → both amber, *"alag alag rakhiye."*
7. Fewer than 4 ArUco markers, or mm/px error > 1.5 % → **MAT_LOST**, all sell events refused.
8. Frame luminance below the measured lux floor → auto-commit disabled, tap required.
9. Measured p95 frame time above threshold → **DEGRADED**, tap required.
10. **Loose/weighed goods (dal, rice, oil, produce) → out of scope by construction.** No discrete stable footprint, no packet to embed, no counted unit. Stated in the README and on camera: the catalog covers exactly the goods that already have an MRP, and misses exactly the goods where pricing evidence would matter most.

**Counting**
11. Exit crossing with `tracker_id is None` (`line_zone.py:170` returns silently) → counted as an **uncounted-crossing exception**; total freezes amber.
12. Track lost between PRICED and exit → *detected-but-never-counted*; total freezes amber.
13. A second customer's item enters an open session → session flagged `contested`, reported as its own rate.

**Money**
14. No network → **AMBER PENDING**. Billing continues; nothing is authorised.
15. Webhook not received → counter stays amber indefinitely. Never green on optimism.
16. Webhook amount ≠ requested paise → **RED HOLD**, manual resolution.
17. Invalid webhook signature → discarded, logged, alerted. Never a state change.
18. **Ledger mirror stale (> 60 s since last verified webhook or successful poll) → any "not paid" verdict is AMBER, never RED.** The system is architecturally incapable of calling a paying customer a liar while uncertain whether it is looking at a complete ledger.
19. **Payment target not minted by this system → explicit out-of-scope refusal.** *"This counter has other stickers on other rails and I am blind to them, by construction."* Absence from a Razorpay mirror is dispositive only for a target we asked for.

**Catalog / agent (P1)**
20. Fewer than K=3 agreeing settled observations → `price_status: unconfirmed`, `is_eligible_checkout: false`.
21. `last_seen_on_shelf` older than TTL → availability unknown; an agent order is **REFUSED with a machine-readable reason code** rather than taking money we cannot fulfil.
22. Enrollment collision inside the danger band → enrollment **blocked** until a disambiguation capture exists.

**Voice**
23. No pre-rendered clip for a number or name → **silence plus visual only**. Never a runtime-synthesised guess. (Hindi 1–99 are 99 irregular words — *ikkis, baais, teis, chauhattar* — they do not compose from tens+units. Budget half a day for a real lexicon of ~130 clips with unit tests at 100 / 200 / 214 / 1000. Getting this wrong in front of an Indian panel is the worst possible outcome for the one feature whose entire job is market credibility.)

**Refused outright, with reasons (§13).** Face recognition. Counterfeit-note authentication. Generative text super-resolution. Voice commands that move money.

---

## 11. Metrics — targets stated here, before measuring

**Frozen set:** 24 SKUs including 6 adversarial pairs (4 differing in pack size, 2 identical-size different-flavour). Enrollment: 8 crops/SKU, Day 3 morning. **Held-out: ≥250 placements across Day 4 and Day 5, two lighting conditions, two pairs of hands**, scripted order for free labels. Git-tagged, SHA-256 manifest, freeze date, pushed before threshold tuning. Gallery images are never evaluated on; the split script is in the repo.

| Metric | Target (declared first) |
|---|---|
| **Unknown-item charge rate** | 0 — structural; an item below the reject margin cannot enter a payable total |
| **Misidentification charge rate, in rupees** | reported as ₹X wrong of ₹Y billed over N events. **Non-zero and measured.** This is the honest headline, not "structurally zero" |
| Per-item amber rate | ≤ 12 % |
| **Baskets requiring ≥1 touch** (derived, 3-item basket) | reported alongside — pre-empt the multiplication a judge will do on stage |
| Sell-event recall | ≥ 0.98 |
| Uncounted-crossing exceptions / 1,000 items | ≤ 2 |
| **Shopkeeper confirmations / 1,000 items** | ≤ 150 — the Amazon ratio, target published *before* measurement, miss reported honestly |
| Footprint error vs calipers, long edge | ≤ 3 mm across 24 SKUs |
| Placement → priced glyph, p95 | ≤ 700 ms |
| **Identity ablation** | footprint-only \| histogram-only \| embed-only \| embed+footprint. **The data chooses the model.** If ₹0 of extra model beats the embedder on a bounded gallery, that is the result we publish. |
| Confusion matrix | before / after the footprint tiebreak, on the 6 adversarial pairs |
| Height-sensitivity curve | footprint error vs item height above plane — the second-order bug in our own fix, published |
| Zero-detection privacy claim | an off-the-shelf face detector run over N stored mat crops; report the count |
| Engineering | fps with hardware + backend + threads + precision + warmup **always attached**; never a bare latency number |

`make bench` — one command, deterministic seed, replays stored mat bundles and stored webhook fixtures **through the production code path** and regenerates every table in the README.

---

## 12. Demo — constraints, not a shot list

**Hard rule:** filmed footage contains one prop family. Total 5:00; hero take ≤ 100 s and shot in a **real shop with a real shopkeeper's hands** (a wooden board on a desk turns a problem-taste submission into a literature review with a prop, and that is visible in one frame).

- **0:00–0:03** one event only: a hand lifts a packet off the mat, ₹20 blooms in perspective on the wood. No caption yet, no UI chrome, no laptop.
- **0:03** caption appears and never changes.
- **0:10** rig disclosure, ten seconds, laptop named and priced.
- **0:20** privacy, silent, 8 s: **split screen, raw feed | processed buffer**, a person standing at the counter present in one and absent from the other; a packet on the mat appears in both. (Do **not** script "face over the counter goes black" — it is geometrically false and would fail live.)
- **0:30–1:40 the take, four beats, one prop:** three items priced and committed; the two-visually-identical-packets gesture with **zero numbers on screen** (the viewer cannot tell them apart either — their own confusion is the beat); an unknown item → amber → one tap → green in four seconds; router killed on camera → AMBER PENDING → restored → queue drains → **green on a real signature-verified webhook.**
- **1:40 onward — a visibly different register** (screen recording, different pacing): `make bench` live · the ablation and confusion tables · the shaded no-model-zone diagram · four refusals at 4 s each · the Amazon four-axis table as one 12 s still · the P1 agent coda framed as **consequence** (*"agentic commerce will exclude 1.3 crore kiranas for two reasons: no catalog and no fulfilment. I cannot fix fulfilment in eight days. Here is a machine-readable catalog nobody typed"*) · the cut list · close.

**Separate 15-second social cut, one beat, no "and":** packet lifts, glyph blooms on wood, counter floods green. Caption: *It bills what leaves the shelf.* Same verb as the pitch video. Do **not** ship a second artifact with a different caption — reach bought with a split identity is a bad trade for a panel that discusses you verbally after watching.

---

## 13. CUT — the exact list and the exact reason

**Cut outright, named in the README under "what I chose not to build":**

| Cut | Reason |
|---|---|
| **MUDRA** (palm QR) | Fills no cell in the SOLD column. Zero incumbent *and* zero wound — it competes with a ₹0 sticker against ~56.86 crore deployed QRs. And the optics are a coin flip: a UPI payload is QR v7–10 needing ~180–270 px of **flat, high-contrast** code; a palm gives ~250–350 px of curved, specular, ~30 % contrast skin. It is a contrast-and-curvature problem, not a resolution problem, and no execution provider fixes either. |
| **JUGNU** (luminance HMAC) | `exposureMode` has **zero entries** in MDN browser-compat-data; Chromium-only; W3C lists iOS as unsupported; `applyConstraints()` can resolve and do nothing. Entire OSS prior art is one dead 4-star stub. Highest ceiling, no floor. |
| **KHATA LIFT** | Khatabook shut MyStore, OkCredit shut OkShop; ledger digitisation is trust-negative. Weakest tech (PP-OCRv5's 41.7 % handwriting figure is for **Chinese**). Smallest money (₹10–50 k). And **DPDP s.3(a)(ii)** pulls a paper bahi-khata into statutory scope the instant a camera digitises it. |
| **KAMPAN**, in all forms including "conditional half-day" | Demoted features are how 8 days become 12. Its ship criterion (a measurable Recall@1 delta on a 24-SKU gallery) is almost certainly inside noise. Survives as a **30-minute static DiffTSR-vs-registration split-screen PNG** in the README — the cheapest possible criterion-3 image. |
| **PEEL's payload generator** | Deleted, not demoted. Regenerating UPI payloads at matching version/ECC/mask is a forgery primitive guarded by a deletable unit test, in a **public repo**, in a track that disqualifies anything offence-capable. Replaced by the **enrollment-photo diff** (P1.4), which produces the identical visual and Hamming number and has no code path that can construct a payload. And the XOR could only ever fire for same-version substitutions anyway — a partial overlay exhausts error correction and simply does not decode. |
| **PAKKA's OCR half** | Kept honest: *"the lookup half is built, because green-on-webhook requires it; the OCR half is one week."* Also unresolved and load-bearing: the string a UPI app shows on its success screen is often the PSP transaction id, **not** the acquirer RRN/UTR in our mirror — if they differ, the exact lookup has nothing to match on. Better answer in the field read first than a rushed detector. |
| **GINTI** (cash counting) | A full day shooting a self-built banknote dataset, an on-screen disclaimer that confuses a demo, and — decisively — it is the single feature that most directly manufactures the tax-visible record the target merchant is avoiding. |
| **ggwave / SUR** | 0.75–1.5 s of FSK screech appended to every confirmation; 15 kHz ultrasonic dies on cheap Android speakers; the verifier must be a second phone running our app; compressed video destroys the evidence (it needs a waveform overlay to be visible at all — which is itself proof it is not a demo beat). |
| **The four-column Track 04 close** | SAW and CHARGED come out of the same pipeline, so their match rate is ~100 % by construction; CHARGED vs SETTLED is Razorpay confirming Razorpay. It is a self-check dressed as a reconciliation and one question exposes it. A two-column *charged vs settled* summary survives as an evidence slide, not as a headline or a track. |
| **`price_evidence` as the headline invention** | MRP is printed on the packet and is a legal ceiling. Rescoped to **availability evidence**, which is real and not derivable from a wrapper. |
| **Neural object detector in P0** | Background subtraction on a rectified static plane is *better* here (oriented masks, 200+ fps, no download, no AGPL) and the deletion is a stronger criterion-3 story than the model. |
| **Browser-only inference; Ultralytics; WebNN; `MediaStreamTrackProcessor`** | No published in-browser fps for RF-DETR/YOLO26 under ORT-Web; AGPL-3.0 §13 would virally license the mandatory public repo; WebNN is origin-trial-only; MDN warns browsers expose `MediaStreamTrackProcessor` in incompatible global contexts. Use `requestVideoFrameCallback` → `createImageBitmap` → Worker + `OffscreenCanvas`. |
| **Face recognition / returning customer** | Not declined — made impossible. **DPDP s.9(3)** absolutely prohibits behavioural monitoring of children, is not waivable by parental consent, ₹200 crore ceiling, in a shop where children are the customers. No face model in the binary; CI fails the build if one, or `RECORD_AUDIO`, or a non-allowlisted domain, enters the tree. The business need is met by `HMAC(vpa)` on a payment the customer chose to make — exact, not probabilistic. |
| **Counterfeit-note detection · generative text SR · voice commands for amounts** | RBI's own MANI app does not authenticate genuineness; DiffTSR produces a confident wrong rupee digit; best published Hindi WER is 13.2 % on *clean* audio and nothing exists for a counter with a fan and a TV. |
| **"6.5 crore merchants"** | That is the Udyam MSME registration count (7.83 crore, Feb 2026), including consultancies with no counter. Use **~1.3 crore kirana stores, with its pre-2021 vintage attached** — and attach the same vintage to the 0.125 % adoption figure (ORF, March 2021) and the 70 %-willing figure, or drop all three. Selective rigour reads worse than none. |
| **National-scale fraud framing · SaaS revenue model · the 371-repo census on screen · the "never cut, never touched the phone" brag · the 11-item refusal list on screen (→ 4)** | ~0.0005 % event rate and falling; nobody makes money selling software to kiranas; the census is unverifiable by a viewer and reads as surveying competitors rather than the market; process brags waste the scarcest slot in the artifact; eleven refusals reads as a man listing everything he read, four reads as taste. |

---

## 14. Language discipline — sentences allowed, sentences banned

| ❌ Banned | ✅ Required form |
|---|---|
| "Zero learned parameters between the shelf and the rupee." | "**No model decides that money moves, and no model decides that money arrived.** A model proposes what the item is; that proposal must clear a published reject margin, is checked against a physical measurement, is painted on the counter before any money moves, and can be reverted with one tap." |
| "False-charge rate is structurally zero." | Two metrics: "**unknown-item charge rate: 0, structurally**" and "**misidentification charge rate: ₹X of ₹Y billed over N events, measured.**" Lead with the second. |
| "The shopkeeper never does data entry." | "He does not maintain a catalog. He confirms a price once, on an item he was already selling." |
| "Not in your ledger" = "not paid" | "Absence is dispositive only for a payment target this system minted. Your counter has other stickers on other rails and I am blind to them, by construction." |
| "It replaces your soundbox." | "The Bharat Soundbox is a UNISOC 8850 on RTOS with 8 MB of RAM. It is the ear on crores of counters and it will never have an eye. **This is the eye that tells that ear what to say.**" |
| "An AI buys biscuits from a shop with no computer." | "Agentic commerce will exclude 1.3 crore kiranas for two reasons: no catalog and no fulfilment. I cannot fix fulfilment in eight days. **I can manufacture the catalog, and here it is.**" |
| "It bills faster." | Never make a speed or labour claim. Sell the record. |

---

## 15. Build order — 8 days, front-loaded so measurement exists before the last feature

| Day | Deliverable | Gate |
|---|---|---|
| **0 (tonight, ~3 h)** | `git init` + public repo + LICENSE (Apache-2.0) + README skeleton. File the `type=upi_qr` activation request **and in the same ticket ask how to credit a test-mode UPI target.** Stand up the webhook receiver with real signature verification. Deploy `razorpay-mcp-server` locally. `npm i @techstark/opencv-js@4.11.0-release.1` (not in `reference/` yet). | **A signature-verified webhook must land by end of Day 1.** |
| **1** | TAKHTI: print, mount nadir, mat lock screen, rectification to 2 px/mm, mm/px self-check against the printed patch. Payment Link path end-to-end, `success@razorpay` test-UPI flow verified *(believed available — this is the single highest-value hour in the plan; if it fails, ladder to a live-mode ₹1 filmed once, then to a webhook replay **labelled on screen and in the repo**)*. | Green counter on a real webhook, filmed rough, **footage in hand on Day 1.** |
| **2** | Placement detector, stability gate, `minAreaRect` mm measurement, LineZone exit, basket, glyph render via H⁻¹, tap-to-revert, audit log with mat-crop hashes. | The full causal chain runs once, end to end. |
| **3** | Gallery + embedder + θ/φ/τ. Amber lane + warm enroll + collision guard. **Enroll 24 SKUs.** | Amber-to-green in 4 s, on camera. |
| **4** | **Capture the held-out set** (scripted order, 2 lightings, 2 hands, ≥250 placements). Freeze, tag, push. Build `make bench`. | **Hard gate: if the set is not frozen by end of Day 4, cut all P1 and spend Days 5–7 on measurement. Never add a feature to compensate for a missing number.** |
| **5** | Numbers: ablation, confusion matrices, amber rate, height-sensitivity curve, confirmations/1000. AWAAZ lexicon (~130 clips, boundary tests). Offline queue + degraded mode + degrade-to-manual. | Every README table generated by `make bench`. |
| **6** | P1: MCP server + availability feed + reserve-then-verify. Sticker registry. | A judge's Claude can buy from the shop. |
| **7** | **Shoot in a real shop.** Budget the full day. Rehearse six times with the real webhook in the loop; put the network beat last so a stall costs only the tail. Cut the evidence section. | One clean ≤100 s take. |
| **8** | Submission. Write "what issues did you face" **first** — it is read first. | — |

---

## 16. "What issues did you face" — drafted now, refined later

1. **The Parle-G 200 g/500 g collision, and the second-order bug in my own fix.** Identical artwork collides inside any embedding's reject margin, and a bigger model makes it *worse* because the packets genuinely look the same. I fixed it with a ruler — the ArUco homography I had already computed to paint rupee glyphs inverts into millimetres. Then I found the bug in the fix: a homography is metric only *on* the plane, and my demo had the packet held 15 cm in the air, which inflates measured area ~1.9× against a signal of ~2.0×. So I redesigned the interaction: the item is set down on the mat before it is swept into the bag. Here is the height-sensitivity curve, and here is the confusion matrix before and after.
2. **A dropped track is a lost sale, not a lost detection.** `sv.LineZone` counts by `tracker_id` and returns silently when it is `None` (`line_zone.py:170`). That is a money bug in a vision bug's clothes. I instrumented both failure modes and the total freezes amber when either fires.
3. **The privacy demo I had scripted was geometrically false.** "Hold your face over the counter and it goes black" — anything between camera and plane projects *inside* the image-space mask. Discovering this changed the mount (nadir), the buffer (rectified mat crop only) and the claim.
4. **`BarcodeDetector` returns only the payload string, never the module matrix** — a two-line design correction on Day 1 that would have been fatal on Day 6.
5. **The closer:** *I absorbed three capabilities into one causal chain and cut eight outright. Here is the rule I used — a capability ships only if it fills or defends the column this shop does not have — and here is what each cut cost me.*

---

## 17. Day-0 verification checklist (unknowns that engineering cannot fix)

- [ ] Can a **test-mode UPI Payment Link** actually be paid and fire `payment_link.paid` / `payment.captured`? (Believed yes via the test UPI success handle — **verify hour one**.)
- [ ] Is `type=upi_qr` enabled on the test account, and if not, how long does the request take?
- [ ] Does `close_qr_code` work from the **local** MCP deployment? (Confirmed ❌ on hosted remote: `reference/razorpay-mcp-server/README.md:48`.)
- [ ] Does the webhook survive a `cloudflared` quick tunnel, and does the poll fallback stamp `ledger_source: poll` correctly?
- [ ] **Confirm the 5 September deadline and the "students only" eligibility line on razorpay.com/buildathon before spending eight days.**



---

# PART II — SYSTEM ARCHITECTURE

## GAWAAH — System Architecture
**"This shop has a perfect record of what it was paid, and no record at all of what it sold."**
Internal module names: NAZAR (the eye), AWAAZ (the voice), KAALA DABBA (the black box).
Track 01 — AI Growth & Agentic Commerce. Apache-2.0.

---

## 0. The resolved product, stated as one chain

There is exactly one causal chain, and every source file in the repo sits on it:

```
item is placed on the mat  →  contour on a known metric plane
  →  deterministic directional line crossing (tracked centroid)
  →  identity proposed by a model, gated by arithmetic
  →  amount in paise, painted on the counter, contestable
  →  merchant taps DONE  →  money service re-runs the crossing predicate
  →  real Razorpay payment target minted
  →  counter goes GREEN only on a signature-verified webhook
```

**Acceptance test, written in the README and grep-checkable by a judge:** every module under `apps/` and `packages/` is on the path from *a tracked centroid crossing a line* to *a signature-verified `payment_link.paid` / `qr_code.credited`*, or it is test/bench/tooling. If a subsystem is not on that path, it was cut.

Three variations on that one chain, and nothing else, are user-visible: **unknown → amber → one tap → enrolled**; **network dies → amber-pending → drains → green**; **the buyer is an agent instead of a human → same mat, same line, same webhook, same green.**

---

## 1. Physical rig (this is architecture, not decoration)

| Element | Spec | Why it is load-bearing |
|---|---|---|
| Mount | Gooseneck clamp / shelf bracket, **nadir**, lens 45–55 cm above the counter, optical axis ⟂ counter plane | Kills the top-face parallax term (displacement → 0 on axis), makes the AABB ≈ true footprint, and makes the privacy quad genuinely exclude a standing person. An oblique desk stand breaks all three. |
| Mat | Laminated A5 **ChArUco** board, 5×7 squares @ 20 mm, `DICT_4X4_50` | ~24 interior corners instead of 4 → homography residual falls from ~5% to ~1%. It is simultaneously: fiducial, metric reference, placement surface, mask anchor, and the sell line's origin. |
| Interaction | Item is **set down on the mat for ~300 ms** on its way into the bag, then lifted off across the customer-side edge | This is the single change that makes metrology sound. Metric coordinates are only valid *on* the plane; "cross an invisible line at an unknown height" is not measurable. Mashgin monetised exactly this constraint. |
| Counter quad | 4 corners dragged once at install, **stored in plane coordinates** | Survives camera nudges. Mask = quad ∩ image, applied at frame-grab. |
| Metrology zone | Mat + 50 mm margin, in plane coords | Outside it, contact-edge measurement is **not trusted** and near-identical pairs go amber. Bounded, stated, measured. |
| Phone | Android 11+, 4 GB, 720p rear, wifi-only, **screen on, plugged in** | The plugged-in condition is stated out loud. Soundboxes are plugged in too. |
| Box | Laptop in the demo; Rs 6,000 mini-PC in v2. **Named and shown on camera at 0:10.** | Disclosed, not hidden. This is the credibility hole that otherwise sinks the submission. |

---

## 2. Host boundary and why it is drawn there

Four processes, three trust levels.

```
┌─ COUNTER PHONE ── browser PWA, no model weights ──────────────────┐
│  camera · counter mask (pre-tensor) · ChArUco/homography          │
│  motion gate · AR render · audio playback · tap UI                │
│  OUT: masked ROI JPEG + H + corners + monotonic ts                │
└────────────────────────────┬──────────────────────────────────────┘
                             │ LAN WebSocket (ws://box:8787), no cloud
┌─ COUNTER BOX ── nazar-brain (Python), NO Razorpay keys ───────────┐
│  segmentation · ByteTrack · LineZone · metrology · gallery        │
│  SQLite ledger · audit log · ring buffer · bench harness          │
│  OUT: signed intent {session, lines[], H, corners, ts[], thumb}   │
└────────────────────────────┬──────────────────────────────────────┘
                             │ localhost HTTP, separate process + user
┌─ MONEY SERVICE ── paisa (FastAPI), ONLY holder of RZP keys ───────┐
│  re-runs the crossing predicate on submitted geometry             │
│  mints · cancels · verifies webhook HMAC · mirrors ledger         │
└────────────────────────────┬──────────────────────────────────────┘
                             │ HTTPS, one allowlisted egress: api.razorpay.com
                        Razorpay (test mode)
                             │ inbound webhooks via cloudflared tunnel

┌─ NIGHTLY (cron on the box, off the money path) ───────────────────┐
│  HDBSCAN over amber crops · catalog emit · feed sign · MCP serve  │
└───────────────────────────────────────────────────────────────────┘
```

**Why the phone holds the geometry and nothing else.** The counter mask must be applied *before any pixel leaves the device*, or the claim "no unmasked frame ever exists off-camera" is false. `@techstark/opencv-js` gives ArUco + `findHomography` + contours in-browser for **3.48 MB brotli**, and I verified the embind symbol table of the embedded wasm (8.43 MB decoded from the 11.39 MB single-file JS): `aruco_ArucoDetector`, `aruco_CharucoDetector`, `getPredefinedDictionary`, `detectMarkers`, `solvePnP`, `findHomography`, `warpPerspective`, `absdiff`, `morphologyEx`, `findContours`, `minAreaRect`, `boxPoints`, `calcHist`/`compareHist`, `findTransformECC`, `ORB`, `AKAZE` are all present (contrib build). `SIFT` and `createBackgroundSubtractorMOG2` are **not** — neither is needed.

**Why inference is not in the browser.** There is no published in-browser fps figure anywhere for RF-DETR or YOLO26 under `onnxruntime-web`. `ort.env.wasm.proxy` is documented as incompatible with the WebGPU EP, so the "keep the main thread free" trick costs a hand-rolled worker. `ort.env.wasm.numThreads` requires `crossOriginIsolated`, which requires COOP `same-origin` + COEP `require-corp`, which GitHub Pages cannot set and which then breaks every cross-origin CDN load. Building the hot loop in a tab is unbounded risk with no prior art. **We publish the measured browser-only byte budget as a dated roadmap line instead of a claim.**

**Why money is a separate process from vision.** "Client computes, server adjudicates" needs a real trust boundary, not a function call. `paisa` runs as a different OS user, owns the only copy of `RAZORPAY_KEY_SECRET` and `RAZORPAY_WEBHOOK_SECRET`, and **re-executes the line-crossing predicate** on the submitted `{H, corners, track polyline, crossing frame indices}` before it will mint. A compromised display client, or a spoofed brain, cannot move a rupee.

---

## 3. Repo tree

```
gawaah/
├── LICENSE                          # Apache-2.0
├── README.md                        # chain, acceptance test, gate matrix, refusals
├── ISSUES.md                        # the "what issues did you face" field, written day 0
├── Makefile                         # make bench | make demo | make replay | make ci
├── docker-compose.yml               # paisa + razorpay-mcp-server (local) + tunnel
│
├── apps/
│   ├── counter-web/                 # the phone. Vite + TS. ZERO model weights.
│   │   ├── src/
│   │   │   ├── main.ts              # OPEN SHIFT gesture: AudioContext + WakeLock + probe
│   │   │   ├── capture.ts           # requestVideoFrameCallback → createImageBitmap
│   │   │   ├── mask.ts              # quad fill, destination-in, BEFORE anything else
│   │   │   ├── plane.ts             # ChArUco detect (3 Hz) + LK corner track (30 Hz)
│   │   │   ├── overlay.gl.ts        # WebGL glyph render warped by H
│   │   │   ├── awaaz.ts             # concatenative Hindi playback, no TTS model
│   │   │   ├── link.ts              # WS client, backoff, staleness gate
│   │   │   ├── passport.ts          # capability probe → execution_tier
│   │   │   └── workers/
│   │   │       ├── cv.worker.ts     # opencv.js, OffscreenCanvas
│   │   │       └── encode.worker.ts # masked ROI JPEG for the truth loop
│   │   ├── public/audio/hi/         # 137 pre-rendered clips (see §4.3)
│   │   └── public/vendor/opencv.js  # self-hosted, pinned, integrity-hashed
│   │
│   ├── nazar-brain/                 # the box. Python 3.11.
│   │   ├── loop.py                  # the slow truth loop
│   │   ├── segment.py               # absdiff → morphologyEx → contours → minAreaRect
│   │   ├── track.py                 # ByteTrack via supervision
│   │   ├── sell_event.py            # LineZone + BOTH tracker failure counters
│   │   ├── metrology.py             # contact-edge mm, plane-contact gate
│   │   ├── identity.py              # hist + edge + MobileCLIP-S0, reject margin
│   │   ├── gallery.py               # few-shot enrolment, collision warning
│   │   ├── ringbuffer.py            # 300 frames, sha256 per keyframe
│   │   ├── ledger.py                # SQLite, hash-chained audit log
│   │   ├── outbox.py                # offline queue, idempotent replay
│   │   └── ws.py                    # server for counter-web
│   │
│   ├── paisa/                       # the money service. FastAPI. Keys live here only.
│   │   ├── mint.py                  # payment_links primary, qr_codes upgrade
│   │   ├── adjudicate.py            # server-side re-run of the crossing predicate
│   │   ├── webhook.py               # HMAC-SHA256 verify, constant-time, replay-safe
│   │   ├── mirror.py                # ledger mirror: HMAC(utr), HMAC(vpa) only
│   │   └── recon.py                 # reconnect poll to catch webhooks missed offline
│   │
│   └── bahi/                        # nightly + agent surface. Off the money path.
│       ├── cluster.py               # HDBSCAN over amber crops
│       ├── feed.py                  # ACP-shaped local/pickup feed + availability evidence
│       ├── wellknown.py             # /.well-known/ucp
│       └── mcp_server.py            # search_shelf, get_item, check_stock, reserve
│
├── packages/
│   ├── plane/                       # ONE rectification engine, ONE test suite
│   │   ├── charuco.py / charuco.ts  # parity implementations
│   │   ├── homography.py
│   │   └── tests/test_plane.py      # synthetic + real, reprojection RMSE assertions
│   ├── audit/                       # the single log schema, shared by all writers
│   └── proto/                       # WS + intent JSON schemas (single source of truth)
│
├── data/
│   ├── gallery/                     # 40 SKUs × 8 enrolment crops (committed)
│   └── heldout/                     # FROZEN. tag v1-frozen-2026-09-01, SHA256SUMS
│       ├── MANIFEST.json            # freeze date, split rule, session ids
│       ├── sessions/                # ~250 labelled crossings, 2 lightings, 2 hands
│       └── webhooks/                # captured real payloads for replay
│
├── bench/
│   ├── run.py                       # make bench → every README table, one seed
│   └── report/                      # generated: confusion matrices, sweeps, curves
│
├── tools/
│   ├── print_mat.py                 # generates the ChArUco PDF at exact scale
│   └── lexicon_build.py             # build-time TTS → 137 clips. Never at runtime.
│
└── .github/workflows/
    ├── ci.yml
    └── refusals.yml                 # FAILS BUILD on face model / RECORD_AUDIO / new egress
```

---

## 4. Stack — exact versions, exact bytes

### 4.1 Browser (counter-web)

| Package | Version | Wire size (brotli) | Decompressed | Role |
|---|---|---|---|---|
| `@techstark/opencv-js` | `4.11.0-release.1` (**pinned; not 5.x**) | **3.48 MB** | 11.39 MB JS / 8.43 MB wasm | ChArUco, homography, contours, LK |
| App bundle (TS, WebGL, UI) | — | ~180 KB | ~600 KB | — |
| Hindi lexicon (Opus 24 kbps mono) | 137 clips | ~1.1 MB | — | AWAAZ |
| ChArUco PDF | — | — | — | printed, not shipped |
| **Total cold load** | | **≈ 4.8 MB** | | **≈ 2.0 s @ 18.93 Mbps (India median 4G); ≈ 7.7 s @ 5 Mbps** |

5.x is pinned out deliberately: its `opencv.js` is 13.30 MB raw vs 11.39 MB, and its API surface is newer than most docs. `onnxruntime-web` is **not** in the browser bundle at all — that alone removes 3.00 MB br / 13.96 MB decompressed (`ort-wasm-simd-threaded.wasm`) or 5.36 MB br / 27.80 MB (`.jsep`), and removes the COOP/COEP trap entirely.

**Zero model weights ship to the browser.** That is an architectural statement, and it is the reason cold load is 4.8 MB rather than the ~55.5 MB brotli a six-lens bundle would have cost (≈ 89 s on a congested 5 Mbps in-shop link — a loading screen, not a demo).

Platform floors used, all Baseline-safe:
- `video.requestVideoFrameCallback` — Chrome Android 83 / Safari iOS 15.4 / Firefox 132 ✅
- `OffscreenCanvas` — Chrome Android 69 / Safari iOS 16.4 / Firefox 105 / Samsung 10 ✅
- `MediaStreamTrackProcessor` — **refused.** MDN warns browsers expose it in incompatible global contexts; Firefox has it at `false`.
- `exposureMode` / `torch` / `focusMode` — **refused.** Zero entries in MDN browser-compat-data 8.0.13; Chromium-only mediacapture-image extensions; W3C lists iOS as unsupported outright. Nothing in this build depends on manual camera control.
- WebGPU — **probed, never required.** `navigator.gpu` is Chrome Android 121 / Safari iOS 26 / **Firefox Android false**.
- `Screen Wake Lock` — acquired at OPEN SHIFT, re-acquired on `visibilitychange`, with an explicit catch-up announcement for anything that landed while hidden.

### 4.2 Box (nazar-brain)

| Package | Version | Licence | Why |
|---|---|---|---|
| `opencv-python-headless` | 4.11.0.86 | Apache-2.0 | segmentation, metrology |
| `supervision` | 0.26.x | MIT | `ByteTrack`, `LineZone` |
| `onnxruntime` | 1.29.0 (CPU) | MIT | embedder only, event-driven |
| `numpy` / `scipy` | 2.x / 1.14 | BSD | — |
| `hdbscan` (nightly only) | via `scikit-learn` 1.5 | BSD | catalog clustering |
| `fastapi` + `uvicorn` | 0.115 / 0.32 | MIT | WS + paisa |

**`supervision.LineZone` — two documented traps, both instrumented, not hoped away.** `line_zone.py:99` exposes `minimum_crossing_threshold` (default **1**; we set **3** explicitly and say why — it is the pre-built answer to "what if the hand wobbles on the line"). `line_zone.py:170` **emits a warning and returns without counting when `tracker_id is None`** — a silent uncounted sale with no exception object anywhere. So we ship *two* counters, not one:

- `detected_but_never_counted` — a track that entered the ROI and vanished without a crossing (dropped track).
- `crossed_without_tracker_id` — a contour crossed the plane-space line while the tracker yielded nothing.

Either firing **freezes the total to amber**. A dropped track is a money bug wearing a vision bug's clothes.

**Detector: deleted for P0.** On a locked-off nadir camera over a static plane, a neural detector is the wrong tool. `absdiff` against a maintained empty-mat reference → `threshold` → `morphologyEx(OPEN, CLOSE)` → `findContours` → `minAreaRect`/`boxPoints`. All verified present in the pinned opencv build. This buys: **oriented** boxes and masks (which the metrology actually requires and a detector cannot supply), ~200 fps, zero model download, zero AGPL question, zero ORT-Web op-coverage risk, and a full day back. `RF-DETR-Nano` (Apache-2.0, 30.5M params, 48.4 AP50:95, 2.3 ms T4 @384) stays in the repo as a documented fallback behind `--detector=rfdetr`, with the head-to-head published on the frozen set. Publishing "I deleted the object detector, here is the comparison" is a stronger AI-judgment artefact than either choice alone.

**Ultralytics is not a dependency.** `ultralytics` is AGPL-3.0 (61,043 stars; LICENSE §13 network clause) and would virally license the entire mandatory-public repo. One README paragraph, `docs/why-not-ultralytics.md`, states this.

### 4.3 Model files — the complete inventory

| File | Size | Where it runs | When | Licence |
|---|---|---|---|---|
| `mobileclip_s0_vision_int8.onnx` (`Xenova/mobileclip_s0`) | **11.85 MB** | box, CPU | **only on a confirmed crossing** (~0.5 Hz) | Apple ML (permissive) |
| `mobile_sam_encoder_int8.onnx` (optional) | ~12 MB | box, CPU | **enrolment only**, 8 frames per new SKU | Apache-2.0 |
| Hindi lexicon, 137 Opus clips | ~1.1 MB | phone | runtime playback | generated by us |
| `rf_detr_nano.onnx` (fallback, not default) | ~30 MB | box | `--detector=rfdetr` only | Apache-2.0 |
| **Total weights on the money path** | **11.85 MB** | | | |

Explicitly **not** shipped, with the arithmetic:
- `SigLIP-base` vision — 371.8 MB fp32 / **99.5 MB int8**. On a 4 GB Android 11 handset alongside a 720p pipeline that is an OOM or a ~137 s cold load at 5 Mbps. `SigLIP2-base` (86M params) is the same order. For a bounded 40-SKU gallery on a fixed rig, an 86M generic encoder at **40.58 % Recall@1** (nyris Visual Product Search Benchmark, best *open* model, 11 models × 6 datasets) is the wrong tool at the wrong size.
- `PP-OCRv5 mobile` — det 4.69 MB + rec 16.46 MB = **21.2 MB**. Cut with the OCR half of PAKKA.
- `hand_landmarker.task` — 7,819,105 bytes, and **incompressible** (brotli and gzip both return exactly 7,819,105 — it is an already-packed flatbuffer). Cut with MUDRA.
- Any face model — **CI fails the build if one enters the tree.**
- Any runtime TTS. Kokoro's four Hindi voices are graded **C** by their own authors on 1–10 hours of training data; IndicF5 has 1,417 hours but is 0.4B params and is a *build-time* tool.

### 4.4 AWAAZ lexicon — built once, never inferred

Hindi 1–99 are **99 irregular words** (*ikkis, baais, teis, chauhattar*) — they do **not** compose from tens + units the way English does. "digits 0–9999 compositionally" produces something audibly wrong to every Indian in the room, which is the worst possible failure for the one feature whose only job is market credibility.

Actual lexicon: 99 number words + `sau` + `hazaar` + 6 SKU-name slots ×40 + `rupaye` + `paise` + 8 status phrases = **137 clips**, generated once by `tools/lexicon_build.py` using **IndicF5** (MIT, 0.4B, 1,417 h across 11 languages) or **Sarvam Bulbul v3** (Rs 30 / 10,000 chars → ~Rs 90 total, inside the Rs 100 free credit). Composition rules live in `awaaz.ts` with **unit tests asserting 100, 200, 214, 240, 999, 1000**.

Runtime is a file lookup and a concatenation: **sub-100 ms, offline, byte-identical every time.** *No model in the money decision, and no model in the announcement — the only two things that speak are arithmetic and a webhook.*

Scope discipline: AWAAZ announces the **item name** as it is billed and the **amber refusal earcon**. It does **not** announce credit confirmation — that is the soundbox's job, done at 110 dB @ 10 cm by hardware the judging panel sells. *The Bharat Soundbox is a UNISOC 8850 on RTOS with 8 MB of RAM. It is the ear on crores of counters and it will never have an eye. This is the eye that tells that ear what to say.*

---

## 5. The two-loop pattern

The render loop must never block on truth, and truth must never be interpolated.

```
FAST RENDER LOOP — phone — 30 Hz — 33.3 ms budget
  rVFC → ImageBitmap → downscale → MASK → plane track (LK) → H
  → draw LAST CONFIRMED ledger state, warped by the CURRENT H
  → composite
  (never waits on the box; never invents a line item)

              ▲ state push (ledger delta)      │ masked ROI JPEG
              │  ~5–20 ms LAN                  ▼  only when motion gate passes

SLOW TRUTH LOOP — box — 8 Hz nominal — 125 ms budget
  decode → absdiff/morph/contours → ByteTrack → LineZone
  → [on crossing only] plane-contact gate → contact-edge mm
                       → hist match → MobileCLIP-S0 embed → reject margin
  → append observation + audit line → push delta
```

**Why this split is correct, not just convenient.** The glyph must sit on the wood at 30 fps or the illusion dies — that is a *geometry* problem and geometry is cheap and local. The amount must be *true* — that is a *money* problem and it is allowed to take 40 ms. Decoupling them means a truth-loop stall shows a **stale total in the right place**, never a **wrong total in the right place**. Staleness > 1500 ms dims the overlay and disables DONE (gate **A5**).

**Motion gating.** A frame-difference check runs at **5 Hz** on a 160×90 luma downsample inside the mask. The truth loop only wakes when in-mask motion energy exceeds a threshold. The **embedder only runs on a confirmed crossing** — roughly 0.5 Hz on a real counter, not 30 Hz. Inference is an **event**, not a loop. This is a deliberate, measurable decision *not* to run a model, and we publish the battery-hours delta.

**Thermal governor.** Peer-reviewed CPU edge-inference work (arXiv 2010.06291) reports **up to 27.7 % throughput variation with no cooling across 22–36 °C ambient**. Measured p95 frame time drives a ladder — 8 Hz → 4 Hz → motion-gated-only — with the reason written into the audit log's `execution_tier` field. A system that visibly gets tired and says so beats one that silently degrades.

---

## 6. Deterministic core vs model edges

```
╔══════════════════════════════════════════════════════════════════════════╗
║  TIER 0 — MONEY PATH — ZERO LEARNED PARAMETERS                           ║
║                                                                          ║
║   ChArUco detect → findHomography → counter mask → mm/px scale           ║
║   contour → minAreaRect → contact-edge length (mm)                       ║
║   plane-contact stationarity test (σ < 2 mm over ≥3 frames)              ║
║   ByteTrack centroid → LineZone directional crossing (thr=3)             ║
║   reject-margin comparison  ·  contact-edge Δ comparison                 ║
║   integer paise arithmetic  ·  session set arithmetic                    ║
║   server-side re-run of the crossing predicate                           ║
║   HMAC-SHA256 webhook signature verify (constant-time)                   ║
║   exact match: webhook.amount == intent.amount_paise                     ║
║   hash-chained audit append (prev_hash → this_hash)                      ║
╚══════════════════════════════════════════════════════════════════════════╝
        ▲                                              ▲
        │ proposes (never decides)                     │ proposes
        │                                              │
┌───────┴──────────────────┐              ┌────────────┴──────────────────┐
│ TIER 1 — PERCEPTION      │              │ TIER 2 — NIGHTLY / OFF-PATH   │
│ MobileCLIP-S0 int8       │              │ HDBSCAN over amber crops      │
│  → SKU family candidate  │              │ VLM → ACP `description` only  │
│ (MobileSAM, enrolment)   │              │ latency irrelevant,           │
│  → clean gallery cutout  │              │ wrong is cheap and reviewable │
└──────────────────────────┘              └───────────────────────────────┘
```

**The claim, stated in its true and still-strong form — because the slogan version overstates by exactly one link and this submission cannot afford that:**

> No model decides that money moves, and no model decides that money arrived. A model *proposes* what the item is; that proposal must clear a published reject margin, is cross-checked against a physical measurement in millimetres, is painted on the counter before any money moves, and can be reverted with one tap.

Correspondingly the headline metric splits in two:
- **Unknown-item charge rate: 0, structurally** — an item below the reject margin cannot enter a payable total. This is a property of the architecture.
- **Misidentification charge rate: Rs X over Rs Y billed across N events, measured.** Non-zero, reported first, and the adversarial pairs exist to produce it.

---

## 7. Identity and metrology — the fix, and the second-order bug in the fix

Identity is a **triple**, not a classification:

```python
candidates = topk_cosine(embed(crop), gallery, k=2)          # Tier 1 proposes
hist_score = compareHist(hist(crop), gallery_hist, HISTCMP_BHATTACHARYYA)
edge_mm    = contact_edge_length_mm(contour, H)              # Tier 0 measures

margin_ok  = (candidates[0].d - candidates[1].d) > TAU_MARGIN
edge_ok    = abs(edge_mm - candidates[0].edge_mm) < TAU_MM
zone_ok    = in_metrology_zone(contour, H)
plane_ok   = plane_contact_stable(track, frames=3, sigma_mm=2.0)

if margin_ok:                       resolve(candidates[0])
elif zone_ok and plane_ok and edge_ok and edge_separates(candidates):
                                    resolve_by_measurement(candidates)
else:                               AMBER                    # excluded from total
```

### Why contact edge, not area

**The second-order bug that would have killed the headline beat.** A homography is metric **only for points on the plane**. Back-projected *area* squares every error you make, and an axis-aligned box on a 45° packet inflates area by up to 1.41×. If the packet is held 15 cm above a 50 cm camera, projected linear size inflates ~1.4× and area ~1.9× — which is the entire signal (Parle-G 200 g ≈ 73 cm² vs 500 g ≈ 145 cm², ratio ~2.0). *A 200 g held high measures as a 500 g lying flat.*

Two changes make it sound:
1. **The mat is a placement surface.** The item is set down. The bottom contour is *on the plane*, parallax-free.
2. **Measure the contact edge, in mm, not the area.** Errors are **linear**, not squared. Parle-G long edges are ≈ 175 mm vs ≈ 230 mm — a 55 mm gap that survives 2–3 % homography error trivially.

And it is gated three ways: `zone_ok` (inside the metrology zone), `plane_ok` (bottom edge stationary in plane coords for ≥3 frames), `hand_occlusion_iou < 0.25`. **No valid measurement frame → AMBER, not a guess.**

We publish `bench/report/edge_error_vs_height.png` — measured mm error against item height at the real camera height — and state the operating assumption out loud. *Caper shipped an NTEP/OIML-certified scale and Mashgin shipped a fixed 3D rig for this problem. I shipped a ruler I already had, and here is the curve showing where the ruler stops working.*

---

## 8. Abstention and human-confirm gates

### Abstention predicates (all Tier 0, all logged)

| ID | Condition | Action | Voice |
|---|---|---|---|
| A1 | ChArUco reprojection RMSE > 1.5 px, or < 8 interior corners | overlay frozen, **mint hard-blocked** | "camera hil gaya" |
| A2 | `!margin_ok` and `!edge_separates` | **AMBER**, excluded from total | amber two-tone earcon |
| A3 | `!plane_ok` or `!zone_ok` or occlusion IoU ≥ 0.25 | measurement void → **AMBER** | amber earcon |
| A4 | `detected_but_never_counted` or `crossed_without_tracker_id` fires | **total freezes**, amber banner | "ek cheez chhoot gayi" |
| A5 | last truth-loop state older than 1500 ms | overlay dims, **DONE disabled** | silent |
| A6 | mean in-mask luma below floor (published lux number) | `TAU_MARGIN` raised; **auto-mint disabled**, tap required | "roshni kam hai" |
| A7 | webhook mirror not refreshed within 120 s | any "not paid" verdict → **AMBER HOLD**, never RED | silent |

**Amber never guesses aloud.** A known SKU gets a spoken name; an amber gets an earcon. The *form* of the sound carries the model's uncertainty, so the system is structurally incapable of bluffing.

### Gate matrix (published verbatim in the README)

| Action | Auto | One tap | Hard-blocked |
|---|---|---|---|
| Add a resolved line to the running total | ✅ | | |
| Add an **amber** line to the total | | | ✅ never |
| Enrol a new SKU + price | | ✅ | |
| Mint a payment target | | ✅ (DONE) | if A1 / A5 / A6 |
| Revert a line (tap the glyph) | | ✅ | |
| Cancel a minted link / close a QR | | ✅ hold-to-confirm | |
| Turn the counter GREEN | ✅ **only** on verified webhook | | on OCR, on mint, on render |
| Fulfil an agent reservation | | ✅ (physical crossing + tap) | if TTL expired |
| Publish a catalog row as checkout-eligible | ✅ if K≥3 + TTL fresh | | otherwise |

---

## 9. Data model

SQLite (`gawaah.db`, WAL). One file, one schema, every module writes the same audit line.

```sql
CREATE TABLE sku (
  sku_id TEXT PRIMARY KEY, display_name TEXT, name_audio_clip TEXT,
  price_paise INTEGER NOT NULL, contact_edge_mm REAL, edge_sigma_mm REAL,
  enrolled_at INTEGER, gallery_version INTEGER, canonical_crop_sha256 TEXT
);

CREATE TABLE gallery_vec (
  sku_id TEXT, frame_sha256 TEXT, vec BLOB,          -- 512×f32, MobileCLIP-S0
  hist BLOB, edge_mm REAL, model_version TEXT,
  PRIMARY KEY (sku_id, frame_sha256)
);

CREATE TABLE session (
  session_id TEXT PRIMARY KEY, opened_at INTEGER, closed_at INTEGER,
  total_paise INTEGER, state TEXT,                   -- see §11 state machine
  interleave_flag INTEGER DEFAULT 0                  -- a second customer's item crossed
);

CREATE TABLE observation (                            -- one per line crossing
  obs_id INTEGER PRIMARY KEY, session_id TEXT, tracker_id INTEGER,
  crossing_frame_idx INTEGER, direction TEXT,        -- 'out' sells, 'in' decrements
  sku_id TEXT NULL, amount_paise INTEGER NULL,
  embedding_d1 REAL, embedding_d2 REAL, hist_score REAL,
  contact_edge_mm REAL, plane_contact_frames INTEGER, occlusion_iou REAL,
  outcome TEXT,                                       -- resolved | measured | amber
  reverted_by TEXT NULL, frame_sha256 TEXT
);

CREATE TABLE money_intent (
  intent_id TEXT PRIMARY KEY, session_id TEXT,
  amount_paise INTEGER, reference_id TEXT UNIQUE,     -- idempotency key
  rzp_kind TEXT,                                      -- payment_link | qr_code
  rzp_entity_id TEXT NULL, short_url TEXT NULL, expire_by INTEGER,
  state TEXT, created_at INTEGER, adjudicated_at INTEGER,
  geometry_blob BLOB                                  -- H, corners, crossing ts, thumb
);

CREATE TABLE ledger_mirror (                          -- NEVER stores raw PII
  payment_id TEXT PRIMARY KEY, amount_paise INTEGER, status TEXT,
  created_at INTEGER, utr_hmac TEXT, vpa_hmac TEXT,
  rzp_entity_id TEXT, webhook_event_id TEXT UNIQUE, received_at INTEGER
);

CREATE TABLE amber_tray (
  tray_id INTEGER PRIMARY KEY, obs_id INTEGER, reason_code TEXT,
  opened_at INTEGER, cleared_at INTEGER NULL, cleared_by TEXT NULL
);

CREATE TABLE audit_log (                              -- KAALA DABBA, hash-chained
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_wall INTEGER, ts_mono_ns INTEGER,
  module TEXT, execution_tier TEXT,
  frame_sha256 TEXT, frame_ring_offset INTEGER,
  observation_json TEXT, model_versions TEXT,
  embedding_distance REAL, reject_margin_outcome TEXT,
  contact_edge_mm REAL, tracker_id INTEGER, crossing_frame_idx INTEGER,
  deterministic_rule TEXT,                            -- e.g. 'linezone.out@thr=3'
  money_action TEXT, gate_state TEXT,
  razorpay_entity_id TEXT, webhook_event_id TEXT,
  prev_hash TEXT, this_hash TEXT                      -- sha256(prev_hash || row)
);
```

**What is never stored, definitively:** any full frame; any unmasked pixel; any face; any raw VPA, email, contact or card object from a webhook (all dropped on receipt — the Razorpay payment entity returns `email`, `contact`, `vpa`, `card{last4,network,type,name,issuer}`, `acquirer_data{rrn,...}`, and none of it survives `mirror.py`); any audio. The Android/PWA surface never requests `RECORD_AUDIO` and CI fails if the string appears.

**Click-a-line → jump-to-frame.** `frame_ring_offset` + `frame_sha256` address the 300-frame masked ring buffer. "Show me why you charged Rs 240" is answered by pointing at a frame with the decision inputs overlaid. One implementation, and it satisfies the audit-trail bar of four tracks.

---

## 10. Razorpay integration surface

### 10.1 Primary money path — Payment Links (no activation required)

This is the **plan of record from hour one**, not a fallback. `type=upi_qr` is an **on-demand feature** requiring an enablement request through a POC or the Dashboard, and there is no confirmed path to make a test-mode UPI QR actually *receive* a credit. Three account-level dependencies under the one beat the whole video rests on is not acceptable when a dependency-free path exists.

```
POST https://api.razorpay.com/v1/payment_links
{
  "amount": 21400,                     // paise, integer, == sum(observation.amount_paise)
  "currency": "INR",
  "accept_partial": false,
  "reference_id": "<session_id>",      // UNIQUE per merchant → free idempotency
  "description": "GAWAAH counter session",
  "expire_by": <unix, now+900>,
  "notes": { "session_id": "...", "audit_hash": "<this_hash>",
             "catalog_version": "...", "execution_tier": "..." }
}
→ { id: "plink_...", short_url: "https://rzp.io/i/...", status: "created" }

POST /v1/payment_links/{id}/cancel      // the undo path
GET  /v1/payment_links/{id}             // reconnect reconciliation poll
```

**Critical property that makes Payment Links the *architecturally* better choice, not merely the safer one:** `short_url` is a **string**, so the QR is rendered **locally on the counter plane** from that string. The `qr_codes` API returns only a hosted `image_url` (`https://rzp.io/i/...`) and no payload — meaning a `upi_qr` mint **cannot complete without fetching a remote image**, which breaks the offline-resilience story at exactly the wrong moment.

**Do not substitute a self-generated `upi://pay` intent QR to the merchant's own VPA.** It settles outside Razorpay and produces **no webhook**. The payment target must be Razorpay-issued or the green state has no source.

### 10.2 Upgrade path — QR Codes (enabled if activation lands)

```
POST /v1/payments/qr_codes
{ "type": "upi_qr",            // ON-DEMAND FEATURE — raise activation day 0
  "usage": "single_use",
  "fixed_amount": true,
  "payment_amount": 21400,     // paise, min 1, mandatory when single_use+fixed
  "close_by": <unix>,          // min now+2min, MAX now+2h, silently capped
  "notes": {...} }             // max 15 kv, 256 chars each
POST /v1/payments/qr_codes/{id}/close
GET  /v1/payments/qr_codes/{id}/payments
```

`close_qr_code` and `create_refund` are marked **❌ unsupported on Razorpay's hosted Remote MCP server** (`reference/razorpay-mcp-server/README.md:48`; implementation at `pkg/razorpay/qr_codes.go:131/451/502`). So `razorpay-mcp-server` runs **locally** in `docker-compose.yml` — a day-1 architectural requirement, not a day-6 discovery.

### 10.3 Webhooks

| Event | Handler | Effect |
|---|---|---|
| `payment_link.paid` | `webhook.py` | amount match → session **GREEN** |
| `payment.captured` | `webhook.py` | mirror row (hashed), price-evidence append |
| `payment_link.cancelled` | `webhook.py` | intent → CANCELLED |
| `qr_code.created` / `.credited` / `.closed` | `webhook.py` | upgrade path equivalents |

Verification: `X-Razorpay-Signature` = `HMAC-SHA256(raw_request_body, WEBHOOK_SECRET)`, compared with `hmac.compare_digest` **on the raw bytes before JSON parsing**. `webhook_event_id` is `UNIQUE` → replay-safe. Ingress in dev via `cloudflared tunnel`.

**Green requires all four:** valid signature ∧ `event` in the green set ∧ `notes.session_id` matches an open intent ∧ `amount == intent.amount_paise` exactly. Not on mint. Not on render. Not on an OCR read. Not on a timer.

### 10.4 Test-mode constraints, stated as constraints

- Test mode needs **no KYC**; test webhooks **do** fire.
- `type=upi_qr` needs on-demand activation. **Filed day 0, hour 1.**
- No confirmed simulator for a test-mode UPI QR credit.
- **Fallback ladder, executed by end of Day 1, not Day 6:** Payment Links (primary, real webhook) → one genuine **live-mode Rs 1** payment filmed once with the amount visible → last resort, a webhook replay from `data/heldout/webhooks/`, **labelled on screen and in the README**. Being caught faking the green counter destroys every honesty point in the package; labelling one costs a caption.
- **Degrade-to-QR, shipped regardless:** when the camera fails, the segmentation fails and the model fails, the app still mints a plain payment link for a typed amount. There is always a path to money.

---

## 11. Offline behaviour and sync

Every money **decision** is already local and deterministic. Only the **mint** and the **webhook** need the network.

```
DRAFT ──DONE──▶ ADJUDICATED ──online──▶ MINTED ──▶ AWAITING_CREDIT ──▶ SETTLED
                     │                                    │              (GREEN)
                     └──offline──▶ QUEUED ────reconnect───┘
                        (AMBER PENDING — authorises nothing)
   any state ──merchant──▶ CANCELLED        AWAITING_CREDIT ──expire_by──▶ EXPIRED
```

- `outbox` rows carry `reference_id = session_id`, which is **unique per merchant**, so replaying the queue after a partial failure is idempotent by construction — a duplicate POST returns the existing link rather than a second charge.
- On reconnect: drain outbox → then run `recon.py`, which polls `GET /v1/payment_links/{id}` for every `AWAITING_CREDIT` intent, because **webhooks that fired while we were offline are simply gone**. Webhook and poll both write to `ledger_mirror`, deduped on `webhook_event_id` / `payment_id`.
- The counter **never** goes green from a poll alone without an amount-exact match, and the audit line records `source: poll` vs `source: webhook`.
- On `visibilitychange → visible`: re-acquire the Wake Lock, replay any state that landed while hidden, and announce the catch-up with a distinct earcon ("while you were away: 1 payment").

This is the **staged failure** in the video, and it is the most realistic failure in the actual market — ORF names unreliable connectivity as a systemic kirana obstacle and OkCredit sells offline operation as a headline feature.

---

## 12. Enrolment data flow

Enrolment is not a setup phase. It **is** the amber exception path, so the catalog is the residue of selling.

```
1. crossing → identity() → AMBER (A2/A3) → excluded from total → earcon → tray+1
2. merchant taps the amber chip → price keypad → one tap
3. brain selects 8 frames for that tracker_id from the ring buffer, ranked by:
     plane_contact == true  AND  occlusion_iou < 0.15
     → sort by variance_of_Laplacian desc → take 8 spread across the track
4. per frame: minAreaRect crop → (optional MobileSAM cutout, removes counter texture)
     → MobileCLIP-S0 embed  → gallery_vec
     → calcHist            → gallery_vec.hist
     → contact_edge_length_mm → median + sigma → sku.contact_edge_mm
5. COLLISION WARNING, at enrolment, when it is free to fix:
     nearest = min cosine over the whole gallery
     if nearest < DANGER and |edge_mm - nearest.edge_mm| < TAU_MM:
        speak+show: "yeh Parle-G 200g jaisa lag raha hai (0.94) —
                     dono ko ek saath rakhiye, size seekh loonga"
6. gallery_version++ ; catalog_version++ ; audit line with the 8 frame hashes
7. the same item crosses again → GREEN, spoken, in the total.  Elapsed: ~4 s.
```

The claim is stated precisely, because the imprecise version is what killed Khatabook's MyStore and OkCredit's OkShop: **the shopkeeper never maintains a catalog; he confirms a price once, on an item he was already selling.** No forms, no lists, no re-typing.

---

## 13. Performance budget per frame at 30 fps

**Budget, not a measurement.** Every number that ships in the README carries device, `imgsz`, backend, thread count, precision and warmup policy. The published reference points this is designed against: YOLO26n is **38.9 ± 0.7 ms CPU ONNX @640 on a Xeon @ 2.00 GHz**; MediaPipe HandLandmarker is **17.12 ms CPU / 12.27 ms GPU on a Pixel 6** — both *native* figures, not browser-tab figures. An unqualified "17.6 ms" is a self-inflicted wound in a competition whose stated bar is honest metrics.

### Phone — render loop, 33.3 ms

| Stage | Typical | Worst frame | Notes |
|---|---|---|---|
| `requestVideoFrameCallback` + `createImageBitmap` | 1.5 ms | 2.5 ms | main thread |
| GPU downscale 1280×720 → 640×360 (`drawImage`) | 1.0 ms | 1.5 ms | OffscreenCanvas |
| Counter mask (path fill, `destination-in`) | 0.4 ms | 0.6 ms | **before anything else touches pixels** |
| ChArUco `detectMarkers` @ 640×360 grey | — | **9.0 ms** | full detect at **3 Hz** only |
| `calcOpticalFlowPyrLK` on 24 corners | 1.2 ms | 2.0 ms | the other 27 frames/s |
| `findHomography` (24 pts, RANSAC) | 0.15 ms | 0.3 ms | — |
| WebGL glyph draw, warped by H | 2.0 ms | 3.0 ms | ≤ 12 glyphs |
| **Amortised render total** | **≈ 6.3 ms** | **≈ 18 ms** | |
| **Headroom** | **27 ms** | **15 ms** | |
| *(worker, off critical path)* JPEG encode masked ROI q70 | 4–6 ms | 8 ms | only on motion-gate pass |

Amortised ChArUco cost = 9.0 × (3/30) + 1.2 × (27/30) ≈ **1.98 ms/frame**.

### Box — truth loop, 8 Hz nominal (125 ms)

| Stage | p50 | p95 | Frequency |
|---|---|---|---|
| WS receive + JPEG decode 640×360 | 3.0 ms | 5 ms | every gated frame |
| `absdiff` + `threshold` | 0.8 ms | 1.2 ms | " |
| `morphologyEx` OPEN+CLOSE (5×5) | 1.4 ms | 2.0 ms | " |
| `findContours` + `minAreaRect` + `boxPoints` | 0.9 ms | 1.6 ms | " |
| ByteTrack update | 1.0 ms | 2.0 ms | " |
| `LineZone.trigger` | 0.05 ms | 0.1 ms | " |
| **Non-crossing frame total** | **≈ 7.2 ms** | **12 ms** | ~8 Hz |
| contact-edge metrology | 0.3 ms | 0.5 ms | **crossing only** |
| `compareHist` × 40 gallery | 0.5 ms | 0.8 ms | crossing only |
| **MobileCLIP-S0 int8, ORT CPU, 1×224×224** | **28 ms** | **45 ms** | **crossing only ≈ 0.5 Hz** |
| ledger append + hash chain + WS push | 2.0 ms | 4 ms | crossing only |
| **Crossing frame total** | **≈ 38 ms** | **≈ 62 ms** | |

Both loops sit inside budget with ≥ 2× headroom, and the expensive model runs roughly **once every two seconds**, not thirty times a second. Duty-cycled steady-state CPU on the box is under 15 % of one core; the battery-hours delta on the phone (motion-gated vs always-on 30 fps capture+encode) is published.

### Cold start

| | Bytes (br) | @ 18.93 Mbps | @ 5 Mbps |
|---|---|---|---|
| counter-web full | 4.8 MB | **2.0 s** | 7.7 s |
| box models (local disk) | 11.85 MB | n/a | n/a |

Service worker precaches on first OPEN SHIFT with a byte-accurate progress bar. Storage headroom is a non-issue (Chrome allows an origin up to 60 % of disk).

---

## 14. Replay mode = the bench harness

One mechanism, two judging criteria, zero extra code.

```
make bench     # deterministic seed; replays data/heldout/ through the EXACT
               # production code path (loop.py, not a script); regenerates
               # every table and figure in the README into bench/report/
make replay    # same fixtures, but driving the live UI over WS —
               # demo-cannot-fail insurance on venue wifi
```

`data/heldout/` is git-tagged `v1-frozen-<date>` with a `SHA256SUMS` manifest, **pushed before the matcher is written**. Enrolment frames and evaluation frames are different physical recording sessions, and `MANIFEST.json` states the split rule. The frozen set contains, deliberately: the size pair (Parle-G 200 g / 500 g), an **identical-size flavour-only sachet pair the geometry cannot separate and which must go 100 % amber**, a 45° placement, a hand-occluded placement, two items placed together, and a faded label.

Numbers `bench/run.py` emits, whatever they say:
1. Unknown-item charge rate (expected **0**, structural) and **misidentification charge rate in rupees** (measured, non-zero).
2. **Amber rate**, per item, published proudly — **and the derived number nobody else reports: baskets requiring ≥1 touch** (at 3 items/basket, a 10 % per-item amber rate ⇒ ~27 % of baskets), paired with the honest claim: *it prices three of four baskets with no touch, it has never charged for an item it could not name, and when it does not know it says so out loud instead of guessing.*
3. Confusion matrix **before and after** contact-edge disambiguation, including the flavour pair it does not solve.
4. `edge_error_vs_height` curve, and reprojection RMSE distribution.
5. Sell-event recall, plus **both** tracker counters (`detected_but_never_counted`, `crossed_without_tracker_id`).
6. Interleaved-customer exception rate — a named, tracked, tray-visible class, because a kirana counter serves three customers at once and this handles one session at a time today.
7. Confirmations per 1,000 items, with the target stated in the README **before** the measurement was run.
8. Reject-margin sweep with the operating point marked and justified in rupees.
9. Latency, fully qualified. Cold-load bytes and seconds on the declared floor device. Battery hours gated vs ungated.
10. **Zero-detection privacy claim, measured like a metric:** an off-the-shelf face detector run against our own post-mask frames — "0 detections across N frames at operating resolution."

---

## 15. Nightly batch and the agent surface (off the money path)

`bahi/` runs on cron, cannot touch a rupee, and every row it proposes lands in the amber tray as `is_eligible_checkout: false` until one human confirmation.

- **Clustering:** HDBSCAN over the day's amber embeddings → candidate SKUs.
- **Feed:** ACP-shaped, emitted in Google's **local/pickup** shape (`pickup_method: reserve`, `pickup_SLA: same_day`) — a kirana is a single-store same-day inventory, not a shipping catalog.
- **Availability evidence, not price evidence.** Price provenance is demoted deliberately: MRP is printed on the packet and is a legal ceiling, so citing three settled `pay_` ids to prove a price stamped on the wrapper is elaborate proof of a non-fact. What is real and unclaimed by ACP, UCP, AP2, Merchant Center or schema.org is **`last_seen_on_shelf` + TTL**, past which `is_eligible_checkout` deterministically flips false. *A catalog that revokes its own rows is a stronger trust claim than a signature, because a signature only proves who said it.* The `price_evidence` block still ships, as a secondary field, honest about the MRP ceiling.
- **Coverage inversion, admitted before anyone asks:** the camera catalogs *packaged* goods, which already have MRP and often a barcode; it cannot price loose dal, rice or oil, which are weighed, have no pack, no embedding and no discrete crossing. Published as `bench/report/coverage.md`: "day 7: N of M shelf-visible SKUs checkout-eligible."
- **MCP surface:** `search_shelf`, `get_item` (returns the camera's own crop), `check_stock`, `reserve`, composed with the **local** `razorpay-mcp-server` so the money tools are Razorpay's own.
- **Reserve-then-verify:** an agent reservation paints the reserved slot **blue**; the *same* deterministic crossing test confirms it physically left; the *same* webhook turns the counter green. Human buyer and agent buyer terminate in the identical event — the unification shown, not argued.
- **Stale-shelf refusal:** an agent ordering past TTL gets a machine-readable reason code and **no money is taken**. Refusing money is the more memorable beat.
- Framed honestly: *agentic commerce will exclude 1.3 crore kirana stores for two reasons — no catalog and no fulfilment. I cannot fix fulfilment in eight days. I can fix the catalog.*

---

## 16. Refusals, enforced by CI

`.github/workflows/refusals.yml` **fails the build** on:

```yaml
- name: no face models
  run: '! grep -rniE "face_detect|facenet|arcface|insightface|retinaface|blazeface|haarcascade_frontalface" --include="*" . '
- name: no audio capture
  run: '! grep -rn "RECORD_AUDIO\|getUserMedia({ *audio: *true" .'
- name: single egress allowlist
  run: python tools/check_egress.py   # only api.razorpay.com + ws://localhost
- name: no AGPL in dependency tree
  run: python tools/check_licences.py
```

The refusals that ship on screen (four, not eleven — eleven reads as a man listing everything he read):

1. **Face recognition / re-identification / dwell / emotion / demographics** — not declined, made *impossible*. The mask zeroes everything outside the counter quad before a tensor exists. DPDP s.9(3) absolutely prohibits behavioural monitoring of children, is **not waivable by parental consent**, ceiling Rs 200 crore, in a shop where children are the customers. The business need is met non-biometrically by `HMAC(vpa)` on the payment the customer *chose* to make — exact, not probabilistic. *We can tell you who paid. We refuse to tell you who walked in.*
2. **Counterfeit-note detection** — the RBI's own MANI app does not authenticate genuineness; security thread, intaglio and UV are invisible to a 720p sensor.
3. **Generative text super-resolution** — one static split-screen in the README: the same faded label through DiffTSR producing a crisp, confident, **wrong** rupee digit, beside registration-only stacking.
4. **Voice commands for amounts** — best published Hindi WER is 13.2 (IndicConformer-600M, Vaani) and 20.9–24.7 (Vosk small) on *clean* audio; nothing is published for a counter with a fan, a TV and traffic. There is no acceptable WER for money. The mic listens to the world, never to instructions.

The engineering refusals — stronger than any of the legal ones, and they live in `docs/`: **I deleted the object detector**, **I removed the camera from the paid/not-paid verdict**, and **I removed the QR payload generator from PEEL** (replaced by an enrolment-photo module diff against the merchant's *own* photographed sticker — identical visual beat, identical Hamming number in every branch, and no code path anywhere that can construct a UPI payload, which removes the Track 02 offence-capability exposure entirely).

---

## 17. Build order with hard gates

| Day | Ship | Gate — miss it and cut, never add |
|---|---|---|
| 0 | `git init` + public repo + LICENSE; file `upi_qr` activation; `paisa` webhook receiver verifying a real signature; local `razorpay-mcp-server` up | **A signature-verified webhook lands in a log by end of Day 1**, else execute the fallback ladder immediately |
| 1 | `packages/plane` + counter mask + ChArUco mat + AR glyph on wood. **Film the money moment today** in whatever mode works | Overlay tracks at 30 fps; reprojection RMSE < 1.5 px |
| 2 | Classical segmentation + ByteTrack + LineZone + both tracker counters; contact-edge metrology | End-to-end crossing → paise → mint → green, once |
| 3 | **Shoot and freeze the held-out set.** Gallery enrolment + amber tray + AWAAZ lexicon with boundary tests | Dataset tagged and pushed **before the matcher is finished**. If not done by EOD3, cut all of §15 |
| 4 | `make bench` producing the real tables; offline outbox + reconnect recon | The headline table exists, whatever it says |
| 5 | Undo/revert; capability passport into `execution_tier`; thermal ladder; refusal CI | — |
| 6 | `bahi/` feed + MCP + reserve-then-verify | Only if Days 1–5 landed clean |
| 7 | Video: 4-beat take ≤ 100 s, rehearsed ≥ 6× with the real webhook in the loop; evidence section from `bench/report/` | Shoot in a **real shop**, on real wood, with a real shopkeeper's hands |

If the Day-4 gate fails, the correct response is to cut §15 entirely and spend Days 5–7 on measurement. **Never add a feature to compensate for a missing number.**



---

# PART III — THE DEMO AND THE VIDEO

## THE DEMO AND THE VIDEO
## GAWAAH — hero take, evidence half, and the production plan

---

## 0. The resolved principle

The one-verb rule is real, and every thesis defends its breadth with the wrong test. "Same viewfinder, no mode switch, one audit log, one homography" is a **compiler's** continuity argument. A viewer does not parse codepaths. A viewer segments video by **subject**.

**The principle: a capability is not a demo. A PROP is a demo.**

> Inside the hero take there is exactly ONE prop class — packaged goods on one counter — and any number of things that happen to it. The instant the camera looks at a new physical object with a new verdict source (a customer's phone screen, a QR sticker on a wall, a faded receipt, a stack of banknotes), a second demo has started, and the shot being unbroken makes the video *longer*, not *singular*.

Two exemptions, which I'll call **anti-props**: objects that enter the frame in order to *break* the one thing (a router switch) or to be *erased* by it (a person who vanishes when the mask engages). They introduce no capability. They stress one. Anti-props read as depth.

The consequence, stated as a law:

- **Variations of one verb read as DEPTH.** It also handles the unknown item. It also handles the item put back. It also handles the dead wifi. The viewer keeps learning new things about one thing.
- **Introductions of new subjects read as BREADTH.** Breadth divides attention by the number of subjects. Depth multiplies confidence by the number of variations.

**The disclosure ladder** — this is how six capabilities ship without six demos:

| Tier | Contains | Rule |
|---|---|---|
| **The take** (0:00–1:58) | One prop class, one verb, five variations | No reading. No numbers on screen. No second object. |
| **The evidence half** (1:58–5:00) | Every unfilmed capability, as stills, screen recordings and measured numbers | Visibly a different kind of shot: wide, screen-capture, cards. This is where reading happens. |
| **The repo / written fields** | Everything else, dated, with reasons | The organisers read "what issues did you face" first. That is the real home for depth. |

**Three sentences carry the film, and none of them contains "and":**

- **Title card (the wound):** *"This shop knows exactly what it was paid. It has no idea what it sold."*
- **Caption (the mechanism, on screen from 0:08, never changed):** *"It bills what leaves the shelf."*
- **Closing line (the payoff):** *"Every shop in India knows what it was paid. This one knows what it sold."*

Note what the caption deliberately does **not** claim. It does not claim speed. At three items and ₹214 there is no labour to save — the shopkeeper does that arithmetic in three seconds and is proud of it. BILLS is a mechanism verb, not a benefit claim. The benefit is the record, and the record is claimed by the title card and the close, not by the caption.

**What this costs, said out loud:** the PEEL sticker peel — a hand physically peeling a fake QR off a wall while the genuine one underneath ignites green — is the single best *image* this project can produce, and it does not appear in the video. It is a new prop, a new verb, a new villain, twenty seconds before the payoff. It gets a still in the evidence half and, if you want it, its own standalone 12-second post. It does not get to be beat number seven inside someone else's film.

---

## 1. Structure at a glance

| Segment | Time | Duration | Register |
|---|---|---|---|
| **THE TAKE** — one continuous screen recording | 0:00–1:58 | 118 s | Nadir camera feed, AR composited, hands only |
| The rig, disclosed | 1:58–2:20 | 22 s | Wide, second camera, real shop |
| The numbers | 2:20–3:00 | 40 s | Terminal + cards, no music |
| The audit trail | 3:00–3:20 | 20 s | Screen recording |
| Four refusals | 3:20–3:36 | 16 s | Cards |
| What else it does — with numbers, not footage | 3:36–3:56 | 20 s | Stills |
| The limits | 3:56–4:08 | 12 s | One card |
| The consequence (agent) | 4:08–4:42 | 34 s | Screen recording + counter cutaway |
| What I cut and why | 4:42–4:52 | 10 s | One still |
| Close | 4:52–5:00 | 8 s | Wide |

The take is **39% of runtime**. That is correct. The take's job is wonder; the remaining 61% is the job of being believed, and only one of those two jobs is done by a continuous shot.

---

## 2. THE HERO TAKE — 0:00 to 1:58, one continuous shot

**What it physically is:** a screen recording off the propped product phone, mounted **nadir** (straight down) at 50 cm over the counter. The viewer is looking at the phone's live camera feed with the AR composited into it — which is why the rupee glyphs really are painted in perspective on real wood. Nothing is filmed off a screen. Nothing is composited in post except the burned-in caption and subtitles.

**Why nadir and not oblique** — three problems die with one mounting decision: the top-face parallax term that wrecks the footprint measurement collapses on the optical axis; the axis-aligned box gets much closer to the true footprint; and the privacy quad genuinely excludes a person standing at the counter instead of falsely claiming to.

### Beat 1 — BILL (0:00–0:38)

| Time | What happens |
|---|---|
| **0:00.0** | A shopkeeper's hand enters from frame right holding a Parle-G packet. *(Hand in frame in the first 15 frames. Non-negotiable.)* |
| **0:00.5** | The packet is **set down** on the marked mat. |
| **0:00.8** | **₹20 blooms on the wood beside it, in perspective.** Voice, Hindi: *"Parle-G, bees rupaye."* Subtitle burned in. |
| **0:01.6** | The hand lifts the packet into a cloth bag, out of frame. |
| **0:02.5–0:07** | Two more items, same rhythm. Total climbs 20 → 32 → 58. |
| **0:04** | Title card fades in over the live frame, lower third: *"This shop knows exactly what it was paid."* |
| **0:06** | Second line: *"It has no idea what it sold."* |
| **0:08** | Title clears. **Caption drops in bottom-centre and never changes again:** *"It bills what leaves the shelf."* Simultaneously a small persistent strip appears top-left: `Redmi Note 9 · Android 12 · laptop on LAN · Razorpay test mode`. |
| **0:08–0:22** | Two more items. Total 186. |
| **0:22–0:30** | **The put-back.** The hand takes the Lays packet back *out* of the bag and returns it to the shelf. **The total decrements to 166.** Voice speaks the reversal. |
| **0:30–0:38** | One more item. Total ₹214. |

The put-back is free, uses the same prop, and does the thing no scripted sequence can fake: it proves the system is responding to an action the script did not obviously need. ₹214 is the actual median UPI grocery ticket. That is not narrated; it is just what the number lands on, and it gets one line in the written submission.

### Beat 2 — THE PAIR (0:38–1:02)

| Time | What happens |
|---|---|
| **0:38** | Two Parle-G packets — 200 g and 500 g, identical artwork — placed side by side on the mat. |
| **0:39–0:42** | Held. The viewer looks at two packets they cannot tell apart. Nothing on screen. |
| **0:42** | **Two different glyphs bloom: ₹20 and ₹45.** |
| **0:52** | Two masala sachets — **identical size**, different flavour — placed on the mat. |
| **0:53** | **Both go AMBER.** Hatched outlines, an amber two-tone earcon, no spoken name, **the total does not move.** |

This is the project's best technical idea and every thesis was about to kill it by rendering it as a picture-in-picture panel showing cosine distances and mm². Delete the panel. **The viewer's own inability to tell the packets apart is the beat.** Then, twelve seconds later, the honest half: a pair the ruler *cannot* separate, correctly refusing. A demo that shows its own limit ten seconds after showing its own trick is a demo that gets believed.

*Engineering prerequisite this beat depends on:* the item is **set down on the mat**, not swung across a line at unknown height. A homography is metric only on the plane. A 200 g packet held 15 cm above the counter measures larger than a 500 g packet lying flat — the height error is the same magnitude as the entire signal. Making the ArUco/ChArUco tile a **placement mat** rather than a fiducial fixes this, and it is a smaller behaviour change than asking a shopkeeper to cross an invisible line. It is also exactly the constraint that made Mashgin work.

### Beat 3 — AMBER BECOMES THE CATALOG (1:02–1:16)

| Time | What happens |
|---|---|
| **1:02** | The shopkeeper taps one of the two amber outlines. |
| **1:05** | He taps ₹10. The voice repeats it back. |
| **1:08** | He places the same sachet again. **Green. ₹10. Spoken by name.** Total ₹224. |
| **1:14** | *The second sachet is never resolved. It stays amber for the rest of the take.* |

Four seconds of on-camera catalog construction, and then a deliberate refusal to tidy up. Leaving one amber unresolved is worth more than resolving both.

### Beat 4 — THE DELIBERATE FAILURE (1:16–1:46)

| Time | What happens |
|---|---|
| **1:16** | Tap DONE. **A real Razorpay QR paints on the mat in perspective.** Counter edge: amber. |
| **1:20** | A customer scans it — **from outside the frame.** The scanning phone never enters the shot. |
| **1:24** | A hand reaches to the frame edge and **flips the router switch.** Visible. On camera. |
| **1:25** | Counter holds **AMBER PENDING**. Strip: `no network — authorising nothing`. Voice: *"internet nahi hai."* |
| **1:32** | **A sixth item is placed and billed anyway.** The glyph blooms, the total updates. The money *decision* never needed the network. |
| **1:40** | Router back on. |
| **1:42–1:46** | Queue drains. **The signature-verified `qr_code.credited` webhook lands. The counter floods green.** A rising two-note earcon. |

**Two production rules for this beat, and they matter more than anything else in the film.**

1. **Do not cut the dead time between the scan and the green.** Whatever it is — one second, three seconds — leave it. That silence is the entire proof that the counter is not turning green because a QR rendered. It is turning green because money arrived. Editing it out would make the video smoother and the claim worthless.
2. **The phone does not speak the credit.** No *"paisa mila."* That is the soundbox's job, at 110 dB, on hardware the judges sell, and a browser tab losing that fight in front of a Razorpay POS engineer is a self-inflicted wound. The phone speaks item names and refusals only. One line in the evidence half turns this into a point: *"It never announces the credit. That's the soundbox's job and the soundbox does it better. This is the eye that tells that ear what to say."*

### Beat 5 — THE MASK (1:46–1:58)

| Time | What happens |
|---|---|
| **1:46** | The shopkeeper leans in; his torso and face fill the top third of the nadir frame. |
| **1:50** | **The mask engages, live, in the feed. Everything outside the counter quad goes black. He vanishes.** The packet on the mat remains, lit. |
| **1:52–1:58** | Held, silent. One small line: `outside this rectangle is never written to memory.` |

**This is the corrected version of a beat that was scripted wrong in three of the four theses.** "Lean your face over the counter and the frame goes black" is geometrically false — anything between the camera and the plane projects *inside* the quad. What is true, and better, is that the raw sensor sees a whole human being and the pipeline receives nothing but wood. So don't move the face; **move the mask**, live, and let the viewer watch a person get erased. It is honest, it is a bigger image, and it needs no words.

**TAKE ENDS AT 1:58.** Prop classes used: **one** (packaged goods on one counter), plus two anti-props (a router switch, a person who disappears).

---

## 3. Where each secondary capability lives — without becoming its own demo

| Capability | Home | Screen time | Why not in the take |
|---|---|---|---|
| **AWAAZ (voice)** | *Inside* the take, uncaptioned | Throughout | It is an output channel, not a subject. It never gets named. |
| **Counter mask / privacy** | Inside the take as a gesture; CI proof in evidence | 12 s + 4 s | Same prop, same rig — it is the camera doing less, not more. |
| **Offline queue / recovery** | Inside the take | 30 s | An anti-prop stressing the one verb. |
| **KAALA DABBA (audit, frame hashes)** | Evidence, screen recording | 20 s | Click-to-jump reads better as a screen capture than as a physical beat. |
| **PAKKA (exact ledger lookup)** | Evidence, one still + one number + one sentence | 8 s | New prop (a stranger's phone screen) with a new verdict source. |
| **PEEL (sticker verification)** | Evidence, one still of the enrolment-photo diff | 8 s | New prop, new villain. Best image in the project; still cut. |
| **KAMPAN** | Evidence, as the DiffTSR hallucination still inside the refusals | 4 s | It has no money action. It exists to make a point about generative models. |
| **Catalog / price + availability evidence** | Evidence, one `curl` | 10 s | It is a JSON artefact. Show the JSON. |
| **Agent buyer** | Evidence, labelled as consequence, payoff on the counter | 34 s | See below. |
| **MUDRA, JUGNU, KHATA LIFT, GINTI** | The cut slide, 10 s, one line each | 10 s | Not built. Never shown as footage. |

**The agent coda is the one judgement call I'll flag as residual risk.** It introduces a chat window, which is a new subject. Three mitigations: it lives in the evidence half where the register has already changed; the chat window appears only as a small picture-in-picture, never full screen; and **the payoff returns to the original prop** — the mat glows blue, the shopkeeper places that exact item, the same webhook lands, the same green. Same shot, same verb, different buyer. That is the unification *shown* rather than argued, and it needs no new caption because the sentence has not changed. If the take runs long or the coda looks bolted on in the edit, cut it to a 10-second `curl` and lose nothing structural.

---

## 4. The evidence half — 1:58 to 5:00, shot by shot

**1:58–2:20 — THE RIG, DISCLOSED (22 s).** Wide, filmed on the second camera, in the real shop. The gooseneck clamp. The phone at 50 cm, nadir, **charger plugged in and visible**. The laptop on the counter behind it. The router. Voice-over, one sentence: *"The phone is the eye. This laptop is the brain, on the shop's wifi. In v2 it's a ₹6,000 box that sits exactly where the laptop is. Nothing in the money path goes to a cloud."* Then **ten seconds of the shopkeeper**, to camera, in Hindi with subtitles, saying one true sentence about a loss he has personally had.

Those ten seconds are the highest-value ten seconds in the entire submission for criterion 1, and they cost a train fare. A wooden board on a desk with a hand-lettered price card turns a problem-taste project into a literature review with a prop, and it is visible in one frame.

**2:20–3:00 — THE NUMBERS (40 s).** `make bench` runs live in a terminal for 8 seconds. Then one card holds for 20 s. Then the before/after confusion matrix for 12 s. **No music under this.**

```
FROZEN SET  tag v1.0-2026-09-01   sha256 a3f9…   enrolled Sun · evaluated Tue
24 SKUs · 51 counter sessions · 312 place-and-bag events

unknown-item charge rate           0 / 312     structural: an item below the reject
                                               margin cannot enter a payable total
misidentification charge rate      ₹45 of ₹8,214 billed   (1 event, the size pair)
amber rate, per item               9.6%   (30 / 312)
amber rate, per basket             22%    (11 / 51)   ← the number that costs him time
sell-event recall                  308 / 312
   dropped track (detected, never counted)    3
   crossed ROI with no tracker_id             1
confirmations per 1,000 items      118    target published 2026-09-01: ≤100 — MISSED
glyph latency, place → paint, p95  310 ms  Redmi Note 9 · 720p · WASM-SIMD q8 · 4 threads · warmed
```

*(Shape, not values — the builder fills the real numbers, including the ones that look bad.)*

Three things this card does that no incumbent in this category has ever done. It **splits the false-charge metric** into the part that is structurally zero and the part that is measured and non-zero, instead of claiming both. It reports the **per-basket** amber rate, because a 10% per-item rate at three items per basket means roughly a quarter of baskets need a tap, and a judge will do that multiplication on stage if you don't. And it publishes a **confirmations-per-1,000 target that was stated before the measurement and then missed** — which is the direct answer to the ratio that destroyed Just Walk Out's reputation and which Mashgin, Caper and Standard AI all decline to publish.

**3:00–3:20 — THE AUDIT (20 s).** Screen recording. Click a ledger line → the video seeks to the exact frame that caused it, with the deterministic inputs overlaid. Then click an **amber** line → it shows why the system refused. Showing the refusal's provenance is stronger than showing the charge's.

**3:20–3:36 — FOUR REFUSALS (16 s, 4 s each).** Face recognition — DPDP s.9(3), unwaivable by parental consent, in a shop where children are the customers; the CI check that fails the build if a face model or `RECORD_AUDIO` enters the tree, shown green. Counterfeit-note detection — the RBI's own MANI app does not authenticate either. Generative text super-resolution — the DiffTSR split-screen producing a crisp, confident, *wrong* rupee digit. Voice commands for amounts — 13–25% Hindi WER on *clean* audio, nothing published for a counter with a fan and a TV.

Four, not eleven. Eleven reads as a man listing everything he read. Four reads as taste. The other seven live in the README where a curious judge finds them and is impressed.

**3:36–3:56 — WHAT ELSE THIS CAMERA DOES (20 s, two stills).** Labelled on screen: `not in the take — here are the numbers instead`.
- **PAKKA**, 10 s: *"I do not classify the image. I ask the ledger."* Precision/recall on the frozen screen set. Plus the scoping sentence that saves it from a hostile question: *absence from the ledger is dispositive only for a payment target this system minted for this session — this counter has other stickers on other rails and this system is blind to them, by construction.*
- **PEEL**, 10 s: the enrolment-photo diff. The merchant photographs his own sticker once; verification diffs against the stored module grid. **No payload generator exists anywhere in this repo** — same image, same Hamming number, zero offence capability, and strictly more honest because it compares against the physical artefact rather than a reconstruction.

**3:56–4:08 — THE LIMITS (12 s, one card).** A kirana serves three customers at once; this handles one session at a time, and here is the measured interleave-error rate. It cannot price 400 g of loose dal, which is a large share of what a kirana actually sells. It needs a plugged-in second phone.

Naming the interleaved-customer problem yourself is the single strongest available signal that you stood at a real counter. Nobody who only read about kiranas knows that goods go hand-to-hand over the counter to a customer standing in the street.

**4:08–4:42 — THE CONSEQUENCE (34 s).** *"Agentic commerce is going to skip 1.3 crore kirana stores for two reasons: no catalog, and no fulfilment. I cannot fix fulfilment in eight days. I can fix the catalog — and here is one nobody typed."* `curl` the feed: rows carrying settled `pay_` ids and a `last_seen_on_shelf` TTL that flips `is_eligible_checkout` false on its own. Small PiP of Claude calling `search_shelf` → `checkout`. Then back to the counter: the mat glows blue, the shopkeeper places that exact item, the same webhook lands, the same green.

Saying the harder half out loud is what makes the easier half believable to a panel that works on this for a living.

**4:42–4:52 — WHAT I CUT AND WHY (10 s).** One still. Six lines, one clause each. No clips of unbuilt things.

**4:52–5:00 — CLOSE (8 s).** Wide of the shop, the phone on its clamp, the counter green.

> **"Every shop in India knows what it was paid. This one knows what it sold."**

Then: repo URL, and the caption one last time.

**On the Amazon objection:** it does not get a slide. The four-axis table (one counter not a store, fixed geometry not a ceiling rig, a live contestable total *before* money moves not a receipt hours later, human in the loop by design not by embarrassment) goes on one README page and one line of the written submission. The video answers it by demonstration between 0:00 and 0:38 — a total painted on the wood before any money moves, and a put-back that reverses it. Forty seconds of pre-emptive defence in a five-minute pitch is forty seconds spent on the judge's question instead of your answer.

---

## 5. Production setup

**Cameras — there are two, and they never do each other's job.**
- **Product phone:** the genuinely old handset. Redmi/Realme class, Android 12, 4 GB, 720p rear. Its screen recording *is* the hero take. Locked on a gooseneck clamp, **nadir, 48–55 cm** above the counter, FOV covering roughly 60 × 40 cm: the mat, the bagging zone, the shelf edge, and the router at the frame margin.
- **Second camera:** a phone or mirrorless on a small tripod, used only for the rig shot and the close. Never for the take.

**Lighting an Indian counter — five specific problems and their fixes.**
1. **The nadir phone shadows its own subject.** Two small diffused LEDs at ±45° from the sides, or one A3 white bounce card catching the overhead tube. Never a bare on-camera torch.
2. **Specular blowout on glossy packet film** kills both the embedding and the image. Everything bounced or diffused. Nothing direct.
3. **Many kirana counters are glass display cases** — a nadir camera sees a mirror of the ceiling tube. The mat must be a **matte-laminated board** covering the working zone. Print the ChArUco pattern on matte stock and laminate matte; glossy lamination reintroduces the exact problem the board is there to solve.
4. **50 Hz mains flicker** bands the frame under cheap LED tubes. Lock the phone's capture to a 1/50 or 1/100 s equivalent if the platform exposes it; otherwise swap the shop's tube for a low-ripple daylight LED panel for the shoot and say so.
5. **Auto-exposure hunts when a hand enters.** Lock exposure and white balance before rolling, then **read the settings back** to confirm the OEM camera HAL actually applied it. `applyConstraints()` resolving is not proof it took.

**Framing note for the social cut:** compose the nadir shot so a **9:16 centre crop still contains the mat and the glyph**. Decide this before the shoot, not in the edit.

**Audio.** The take's soundtrack is the phone's own TTS plus shop ambience. Put a second recorder or a lav near the phone speaker so the Hindi lines are clean. **Every Hindi line is subtitled in burned-in English**, because autoplay is muted and because not every judge speaks Hindi. No music under the take or under the numbers. Music only under the rig shot and the last eight seconds.

**Pre-staged (and stated as such in the README):**
- ChArUco mat taped and calibrated.
- 24-SKU gallery pre-enrolled — **except** the item used for the amber-enrol beat, which must be genuinely never-seen. Coaxing that beat by pre-enrolling and then deleting is the one shortcut that would poison everything else.
- Router pre-wired to a switch you can reach inside the frame.
- Razorpay account, mint path, webhook tunnel **pre-warmed by a dummy transaction two minutes before rolling**, so DNS/TLS/tunnel are hot and the only latency on camera is real latency.
- Payer's UPI app open on the scan screen, off-frame.

**Live, and nothing else:** every glyph, every amber, the enrolment, the mint, the network kill, the queue drain, the webhook, the mask engage.

**The money moment — resolve this on day 0, not day 6.** `type=upi_qr` is on-demand activation; `close_qr_code` is unsupported on the hosted remote MCP server. Invert the usual ladder and make the **live-mode ₹1 payment the plan of record**: film one genuine live-mode credit on day 1, on camera, amount visible. A real rupee moving is a stronger beat than any simulation and it removes the one dependency you do not control. `create_payment_link_upi` is the no-activation-needed alternative and costs almost nothing visually. If neither works and you must replay a webhook, **label it on screen and in the README.** Being caught faking the green counter destroys every honesty point in the package; labelling one costs a caption and arguably scores on criterion 4.

**Shoot budget: one full day.** Five beats at 85% individual reliability is a 44% chance of a clean take; you need roughly a dozen attempts for confidence, at 3–5 minutes of reset each. Shoot the network beat **last** in the sequence so a webhook stall only costs you the tail.

---

## 6. Props

**Load-bearing**
- A4 ChArUco board, matte print, matte lamination, double-sided tape
- Gooseneck clamp or light stand with phone holder (₹300–600) — **not** a desk stand; a desk stand gives you 15 cm and an oblique angle, which breaks the geometry and the privacy claim simultaneously
- Old Android + charger (plugged in, visible — a soundbox is plugged in too)
- Laptop, on the counter, in the rig shot
- Router with a reachable power switch, sitting at the frame margin
- Cloth jhola for the bagging motion

**SKUs**
- Parle-G 200 g **and** 500 g — the size pair
- Two identical-size masala sachets, different flavour — the ambiguity pair the ruler cannot solve
- Maggi 70 g, a Lays packet (the put-back item), one more staple
- One genuinely never-enrolled item for the amber beat — a soap bar or a shampoo sachet strip
- A small poly bag of loose dal — **for the evidence-half still only**, as the honest "this camera cannot price this" prop

**Set dressing that reads "kirana" in one frame**
Hand-lettered price card, a sweet jar, a spike of receipts, worn wood. Optionally the printed bilingual counter card taped up — free if it exists, and not claimed as a feature.

---

## 7. MUST NOT APPEAR

**Prop discipline (inside the take)**
- A customer's phone screen. A QR sticker being peeled. A faded receipt. Banknotes. A weighing scale.
- A second scanning phone entering frame — the customer scans from off-camera.
- Any dashboard, terminal or laptop screen.
- Any cosine distance, mm² readout, or picture-in-picture panel.
- Any capability montage, chapter list or feature grid, anywhere in the video.

**Claims that must not go on screen anywhere**
- *"Zero learned parameters between the shelf and the rupee."* False by one link — a model proposes the SKU and the SKU carries the price. Use: *"No model decides that money moves, and no model decides that money arrived."*
- *"False-charge rate is structurally zero."* Only the *unknown-item* charge rate is structural. Split it, and lead with the measured one.
- *"6.5 crore merchants"* — that is the Udyam MSME count. Use ~1.3 crore kirana stores, with the vintage attached.
- *"₹805 crore of UPI fraud"* — a falling 0.0005% event rate. Per-merchant felt loss only.
- *"No hardware."* There is a laptop. Say so.
- Anything implying the soundbox is obsolete. It is the panel's product line and the correct framing is complementary.
- *"An AI buys biscuits from a shop with no computer"* as a claim rather than a labelled consequence.
- *"We never cut and we never touched the phone."* Keep the discipline, delete the boast — it spends the scarcest slot in the artifact on a claim about process. One line in the README.
- *"Your books close themselves."* Test every such line against *"...and visible to the tax department,"* because that is the merchant's first thought and the panel knows it.

**Production**
- No stock B-roll of Indian markets. No music over the take or the numbers. No English-only UI.
- No board-on-a-desk standing in for a shop. No builder's hands standing in for a shopkeeper's.
- No cut between the QR scan and the green flood.
- No unlabelled webhook replay.
- No third party's face in the raw footage — the person in the mask beat is the shopkeeper, with recorded consent, stated in the README.
- No number on screen that `make bench` cannot reproduce.

---

## 8. The 60-second social cut

Vertical 9:16, burned-in captions, **works fully muted**, no voiceover. One prop, one verb, one arc. It is the take, compressed — not a different film with a different sentence.

| Time | Shot |
|---|---|
| **0:00–0:04** | Hand sets Parle-G on the mat. **₹20 blooms on the wood.** Hand lifts it into the bag. Caption, bottom: *"It bills what leaves the shelf."* |
| **0:04–0:16** | Three more items. Glyphs bloom. Total climbs. One item put back — **total goes down.** |
| **0:16–0:24** | The two identical Parle-G packets. Held three seconds so the viewer fails to tell them apart. **Two different prices bloom.** Small text, one line: `it doesn't recognise the packet — it measures it` |
| **0:24–0:34** | An item it has never seen. **Amber. No price. Total does not move.** Text: `it says when it doesn't know` One tap. Placed again. **Green.** |
| **0:34–0:42** | DONE. A real QR paints on the mat. **A hand flips the router off.** Counter holds amber. Text: `no network — authorising nothing` |
| **0:42–0:52** | Router back. **Counter floods green.** Text: `it only turns green when the money actually landed` |
| **0:52–0:58** | The mask engages. The person at the counter **vanishes**. The packet stays. Text: `it can only see the rectangle` |
| **0:58–1:00** | Black. *"Every shop in India knows what it was paid. This one knows what it sold."* + repo URL. |

**The 6-second loop** for the thumbnail and the first three seconds is 0:00–0:04 plus the green flood from 0:42. Packet down, ₹20 blooms on wood, counter goes green. Nothing else. If you want a second social post, the mask beat (0:52–0:58) stands alone as a 10-second clip with the caption *"It can only see the rectangle"* — it is the more shareable "wait, HOW," and it must be posted **separately**, never combined.

---

## 9. The four demo rules, audited against this plan

| Rule | Status |
|---|---|
| One verb | BILLS. One caption, on screen from 0:08, never changed. Five variations, one prop class. |
| Hand in frame in first 15 frames | Frame 1 is a hand holding a Parle-G packet. |
| Overlay, not a separate window | The take *is* the camera feed with AR composited. There is no second window until 3:00, in a different register. |
| One continuous take | 118 seconds, uncut, screen-recorded, nothing pre-recorded inside it. |
| Visible risk | The router kill at 1:24. The uncut dead time between scan and green at 1:20–1:46. An amber the system refuses to resolve and never does. |
| Changing pixels > 40% | Nadir framing of a 60 × 40 cm counter; a hand plus a packet plus a blooming glyph occupies well over half the frame in every beat. |
| Under 400 ms response | Place → glyph paint, p95 target **≤ 400 ms**, published with hardware, resolution, backend, thread count, precision and warmup policy. If it misses, drop capture resolution until it doesn't — the glyph is a proposal and it must feel instant; the *mint* is the gate and it stays gated. |

---

## 10. Two decisions outside this brief that the video depends on

1. **Track.** The first two minutes are track-agnostic; only the last sixty seconds carry the track. If it is Track 01, the honest growth claim is the second clause (transactable by an AI buyer) with the fulfilment gap admitted out loud — *not* a billing-speed claim, which grows no revenue at a three-item counter. If that framing feels thin on the day, Track 05 carries the same execution bar with no revenue clause. Decide before the coda is shot.

2. **The wow you are trading away.** Every high-wow item is gone: the palm QR, the invisible luminance HMAC, the hand-tremor super-resolution. What remains that produces a physical *"wait, HOW"* is exactly two shots — **a rupee amount painted in correct perspective on real wood as an item leaves the shelf**, and **a person vanishing from a live frame while a biscuit packet stays**. That is a good trade for a hiring panel and a worse one for virality, and it should be made knowingly. It also means those two shots must be the best-lit, best-composed, most-rehearsed seconds in the entire film, shot in a real shop, and one of them must open the video.



---

# PART IV — MEASUREMENT, PRIVACY, 8-DAY PLAN

## MEASUREMENT, PRIVACY, AND THE 8-DAY PLAN
### For: GAWAAH — a phone propped over one kirana counter, Track 01, submission 2026-09-05

**Track decision, made once and not revisited.** Track 01 (AI Growth & Agentic Commerce). Reason: the census says 147 of 371 visible repos are agent-over-JSON entries competing head-on with Agent Studio and Vulcan; a camera is the only physical entry in the most-watched track. Track 04 is rejected because SAW and CHARGED come out of the same pipeline — their match rate is ~100% by construction, and a finance-ops judge kills it with one question ("what are your two *independent* ledgers?"). Track 02 is rejected because the surviving detector (ledger lookup) is a lookup, not a detector, and the sticker channel drags an offence-capability argument into a track that disqualifies for it.

**The claim, stated so it survives 90 seconds of hostile questioning:** *this shop has a complete record of what it was **paid** and no record at all of what it **sold**.* The camera produces the missing first column. It is not sold as billing speed — at three items and ₹214 there is no labour to save, and the honest amber rate proves it. Track 01's second clause ("make a merchant transactable by an AI buyer end to end") is the growth mechanism; the revenue claim is *not* made for billing.

---

## PART 1 — MEASUREMENT

## 1.1 The unit of evaluation

Two units, both reported:

| Unit | Definition | Why it exists |
|---|---|---|
| **Sell event** | One item placed on the marked mat and removed, producing (or failing to produce) one priced line | The model-facing unit |
| **Session** | One basket, bounded by the `DONE` tap, ending in one minted payment target | The money-facing unit; the only unit a shopkeeper cares about |

Everything downstream is reported at both levels. A 10% per-item amber rate is a ~27% per-basket touch rate at three items; **publish the derived basket number yourself** rather than letting a judge do the multiplication on stage.

## 1.2 The held-out set: composition

24 SKUs, not 40. Scoping is stated up front against the honest ceiling (nyris Visual Product Search Benchmark: best open embedding = 40.58% R@1 on instance-level product retrieval — a generic embedding cannot carry a 2,000-SKU catalog, and claiming otherwise is falsifiable in one question).

| Bucket | Count | Purpose |
|---|---|---|
| Ordinary enrolled SKUs | 12 | Base accuracy |
| **Size-variant pairs** (same artwork, different pack) | 3 pairs / 6 SKUs | The Parle-G collision. Geometry *should* resolve these. |
| **Flavour-variant pairs** (identical artwork family, identical pack size) | 2 pairs / 4 SKUs | Separable by **neither** embedding nor geometry. Correct behaviour is ~100% abstention. This bucket exists to be failed on purpose. |
| Degraded instance (faded / creased packet) | 2 | Enrolment-quality sensitivity |
| **Never-enrolled distractors** | 6 SKUs | Unknown recall. A miss here *is* a false charge. |

**Motion classes, sampled across all buckets:** flat-on-mat (baseline), rotated ~45°, hand occlusion >30%, two items placed simultaneously, item placed then retracted (must **not** count), item put back after counting (must decrement), item slid off-mat (must abstain — geometry invalid off-plane).

**Size:** ≥300 sell events across ≥50 sessions. That is ~2.5–3 h of capture plus ~2 h of labelling. It is the single largest uncosted line item in every version of this plan, and it is budgeted on Day 3 (see Part 3).

## 1.3 Leakage controls — six, and they are the credibility

Most hackathon "held-out sets" leak in ways nobody names. Enumerate the controls in the README:

1. **Temporal separation.** Enrolment: Day 3 morning, tube light. DEV split: Day 3 midday. HELD-OUT: Day 3 evening + a second session under different light. Different sessions, never interleaved.
2. **Physical-instance separation** (the strongest control, almost never done). Where budget allows, buy **two packets of the same SKU**: enrol instance A, evaluate on instance B. Evaluating on the same physical packet leaks scuffs, crease patterns and print registration — it inflates R@1 substantially and is invisible in the numbers.
3. **Operator separation.** Two pairs of hands; one never appears in enrolment frames.
4. **Rig perturbation.** The mount is physically dismounted and re-seated between enrolment and held-out capture, forcing a fresh homography solve. Otherwise you are evaluating a geometry you tuned against.
5. **Freeze protocol.** The held-out bundle is written, `sha256`-manifested, committed and **git-tagged before the matcher and the threshold code exist**. The tag date is visible in `git log`. Threshold selection happens **only** on DEV, which is never reported as a headline.
6. **Run accounting.** A written rule: the held-out set may be executed at most **three** times, and every execution appends `{utc, commit_sha, metrics_hash}` to `bench/runs.log`, which is committed. Publish the log. This is the cheapest anti-overfit artifact in existence and no other entrant will have one.

**Labelling protocol.** Each session is scripted in advance (`bench/scripts/session_XX.yaml`: ordered SKU list + motion class per event). Labels come from the script; the operator announces each SKU aloud so the audio track is a second channel. Then the honest part: **every event where system output ≠ script is adjudicated by watching the frame**, and adjudications are logged, because script-driven labelling otherwise hides the case where the system counted something the script never contained.

## 1.4 Primary metric

> **Overcharge rupees per ₹1,000 billed, at the operating reject margin, with abstention permitted.**

Signed, and split, because overcharge and undercharge are not the same failure:

- **Overcharge ₹/₹1,000** — the reputational killer. A shopkeeper netting ₹15,000–50,000/month uninstalls after one wrong total in front of a customer. Target declared before measurement.
- **Undercharge ₹/₹1,000** — the merchant's own loss. Tolerable, tracked, reported.

Accuracy percentages are *secondary* on purpose. At a ₹214 median grocery ticket the meaningful question is not "what fraction of items were classified correctly" but "how many rupees were wrong, in which direction, in how many baskets."

## 1.5 Secondary metrics

| # | Metric | Target set before measurement | Note |
|---|---|---|---|
| S1 | **Unknown-item charge rate** | 0, structurally | An item below the reject margin **cannot** enter a payable total. Asserted as an **invariant with a unit test**, not measured. |
| S2 | **Misidentification charge rate (₹)** | measured, non-zero | The honest split. A 200g billed as 500g *is* a false charge and is *not* structurally excluded. Lead with this number, not with S1. |
| S3 | **Amber rate**, per item and **per basket** | 8–15% item / publish derived basket | Reported on the main screen, not in an appendix. |
| S4 | **Unknown recall** (never-enrolled → amber) | ≥ 0.99 | A safety metric, not an accuracy metric. |
| S5 | **Sell-event recall** (counted / physically occurred) | ≥ 0.97 | Plus two loss counters (below). |
| S6 | `dropped_track` and `crossed_without_tracker_id` | reported raw | `supervision/detection/line_zone.py` counts by `tracker_id` and **silently returns without counting** when `tracker_id is None` (~line 170; `minimum_crossing_threshold` at ~line 99 — re-verify against the local checkout before quoting). A dropped track is a *silently uncounted sale*: a money bug wearing a vision bug's clothes. Instrument **both** modes; most people instrument one. |
| S7 | **Put-back precision** | ≥ 0.95 | B→A decrements. Proves the system reacts, not replays. |
| S8 | **Human touches per 1,000 items** | target stated first, miss reported | Deliberately mirrors the Just Walk Out ratio (~700 reviews per 1,000 sales against an internal target of 50). No incumbent — Mashgin, Caper, Standard AI — publishes an equivalent. |
| S9 | **Session-attribution error rate** | measured | A kirana serves 3–4 customers interleaved. Named as a structural limit and *measured*, not hidden. |
| S10 | Latency p50/p95, fully qualified | — | Hardware, `imgsz`, backend, thread count, precision, warm-up policy attached to **every** latency number. Never a bare figure — Ultralytics' own published YOLO26n number is 38.9 ± 0.7 ms CPU ONNX and a judge will find it. |

## 1.6 Baselines to beat

Nobody in a hackathon ships baselines. Three, all cheap, and one of them is designed to beat you:

**B0 — the shopkeeper.** The same 50 scripted baskets, added up mentally, hand-timed. Report seconds/basket and error rate. **He will probably win on speed and tie on accuracy.** Publish that. It is the most credible page in the submission and it is what forces the correct framing: the product's value is the *record*, not the speed. A submission that measures itself against the human it replaces and reports losing is doing something no other entry will do.

**B1 — no-embedding baseline.** Colour-histogram `compareHist` + metric long edge. Zero learned parameters, ~40 lines, both primitives confirmed present in the `@techstark/opencv-js` 4.11 contrib build. **If SigLIP2 does not beat this on 24 SKUs on a fixed rig, ship the histogram and say so** — that is a criterion-3 win either way, and "I deleted the embedder" is a stronger sentence than any of the eleven legal refusals.

**B2 — no-geometry baseline.** Embedding only, no contact-edge tiebreak. This *is* the before/after confusion matrix.

*(Optional B3 — EAN-13 where a barcode exists, to bound the ceiling. Note in the same breath that loose grain, pulses, oil and produce — the actual staples — can never have one.)*

## 1.7 The abstention / coverage curve

This is the core artifact, and it is a **selective prediction** problem, so report it like one.

- Sweep the reject margin τ **on DEV only**. For each τ, record coverage (fraction of events priced) and overcharge ₹/₹1,000.
- Plot **risk–coverage** and report **AURC** as a single summary number.
- Report two anchor points that summarise the whole curve in one line each:
  - **Selective risk at 90% coverage** — "if I insist on pricing 9 of 10 items, I am wrong by ₹X per ₹1,000."
  - **Coverage at zero overcharge** — "if I refuse to overcharge at all, I can price Y% of items."
- **Justify the operating point in rupees, not in F1.** "τ chosen so expected overcharge ≤ ₹2 per ₹1,000 billed; the coverage cost of that choice is 11 percentage points."
- Report the same curve for the OCR channel (confidence gate) if it ships.

## 1.8 The SKU-collision confusion matrix — exact specification

Restricted to the 12 SKUs in the 5 adversarial pairs. **Three matrices, in this order:**

| Matrix | Configuration | Expected story |
|---|---|---|
| **M1** | Embedding argmax, no abstain | The collision, undisguised. Off-diagonal mass on the size pairs. |
| **M2** | Embedding + reject margin, with an explicit **AMBER column** (13th column) | The collision converted from wrong charges into refusals. |
| **M3** | Embedding + reject margin + contact-edge geometry | Size pairs resolved; **flavour pairs still ~100% AMBER** and that is the correct output. |

**Per-pair diagnostics table** (this is what makes the geometry claim survive):

- **Embedding separation:** mean inter-member cosine distance vs mean intra-member distance, per pair. If inter ≈ intra, the pair is inside the reject margin by construction and no threshold fixes it.
- **Geometric separation:** measured contact-edge long-edge difference in mm, **with an empirically measured σ**.

**How σ is measured (30 minutes, and it is load-bearing):** place one packet at 9 positions on the mat × 3 orientations × 3 mount re-seatings = 81 measurements. Report mean and σ per SKU.

**Decision rule, published:** geometry is consulted only when `|Δmm| > 3σ`; otherwise **abstain**. This turns the tiebreak from an assertion into a calibrated instrument.

**The height-error curve — publish it, because it is the hostile question.** A homography is metric only for points *on* the counter plane. Shim the same packet at 0/1/3/5 cm and plot measured footprint error vs height. At a ~45 cm nadir camera, a 5 cm lift inflates linear dimensions by roughly `H/(H−h) ≈ 1.12`, area by ~1.25 — and the 200g/500g area ratio is only ~2.0, so a lifted small packet can read as a flat large one. **This is why the interaction is `place on the mat`, not `wave across a line`**, and why off-plane events abstain. Publishing the curve converts the single biggest hole in the invention into the best paragraph in the "what issues did you face" field.

## 1.9 OCR character error rate — exact specification

Applies to whatever OCR actually ships. Recommended scope: the numeric line on a customer's presented payment-success screen (amount + identifier), used **only** to drive an exact ledger lookup. Measured at four levels, because CER alone is misleading:

1. **CER on the extracted numeric field** = `Levenshtein(pred, gt) / len(gt)`, ROI ground-truthed by hand, reported **per condition**: light (tube / daylight / evening CFL) × tilt (0° / 20° / 40°) × panel (OLED / LCD) × glare (with / without screen protector). ~120 screens is enough for a per-condition breakdown with visible error bars.
2. **Field exact-match rate** — the only metric that licenses an exact lookup. A 12-digit identifier at 3% CER is ~70% exact-match. Report exact-match, not CER, as the headline.
3. **Gated exact-match.** Apply an OCR confidence gate; below it, AMBER, never RED. Report **exact-match within the covered set** (needs ≥ 0.995 to justify an exact lookup) **and the coverage cost of the gate**. This is the same risk–coverage machinery as §1.7 applied to text.
4. **Downstream verdict metrics** (what actually gets published): RED precision (must be 1.00 on the frozen set), RED recall, AMBER rate, and the false-RED cost priced in **customer-lifetime terms, not ticket terms** — a false RED is telling a neighbour, in front of a queue, that he is lying.

**The stopping rule that makes RED precision structural rather than statistical.** RED fires only when *all four* hold:

| Condition | Failure → |
|---|---|
| OCR confidence above gate | AMBER |
| Identifier absent from the local mirror | — |
| Mirror provably fresh (webhook or successful poll < 60 s) | AMBER |
| **The payment target was one this system minted for this session** | AMBER |

That fourth row is the one everybody forgets. A real kirana counter carries two to four QR stickers on different rails; a Razorpay mirror is structurally blind to all of them. **Absence from the ledger means "not paid" only for a target you controlled.** State it in one sentence in the README and it converts the biggest hole a payments judge will find into evidence you understand acquiring.

**The join-rate measurement nobody has done.** The identifier a UPI app displays on its success screen is often the PSP's own transaction id, not the acquirer RRN/UTR in the Razorpay payload. **Measure it:** over N real payments, how often does the on-screen string appear in `utr` / `rrn` / `acquirer_data`? Publish the join rate. If it is low, the verdict degrades honestly from *exact* to *bounded* (amount + 120 s window), and both are reported. Discovering this on Day 6 is fatal; measuring it on Day 1 is a headline.

## 1.10 Exception list format

One JSONL file per bench run, committed alongside the metrics, linked from the README. An exception list nobody can read is a claim, not an artifact.

```json
{"ts":"2026-09-02T18:41:07.221Z","session_id":"s_0042","event_idx":3,
 "reason":"AMBIGUOUS_FLAVOUR","frame_sha256":"9f2c…","frame_idx":11947,
 "top2":[{"sku":"masala_10g","d":0.913},{"sku":"chilli_10g","d":0.919}],
 "margin":0.006,"reject_margin":0.040,
 "footprint_mm":{"long_edge":78.1,"sigma":1.9,"pair_delta":0.4,"gate":"below_3sigma"},
 "on_plane":true,"occlusion":0.11,"execution_tier":"cpu-wasm-q8",
 "rupees_at_stake":10,"resolution":"human_tap","latency_to_resolution_s":3.8}
```

**Closed reason-code vocabulary** (anything outside it is a bug, and the bench fails on an unknown code):
`UNKNOWN_SKU` · `AMBIGUOUS_VARIANT` · `AMBIGUOUS_FLAVOUR` · `OCCLUDED_NO_CLEAN_FRAME` · `NOT_ON_PLANE` · `TRACK_LOST` · `CROSSED_NO_TRACKER_ID` · `SESSION_AMBIGUOUS` · `LEDGER_STALE` · `OCR_LOW_CONF` · `IDENTIFIER_NOT_IN_LEDGER` · `TARGET_NOT_MINTED_BY_US` · `MINT_QUEUED_OFFLINE` · `LOW_LIGHT_BELOW_FLOOR`

Plus a per-run summary table: count and ₹-at-stake by reason code, share auto-resolved, share human-resolved, share **left unresolved** (named, not aggregated away).

## 1.11 One-command reproduction

```
make bench          # full held-out run, CPU-only, < 10 min on a judge's laptop
make bench-quick    # 30 events, < 60 s, runs in CI on every push
```

`make bench` must:

1. Fetch the git-tagged frame bundle and webhook fixtures; **verify every sha256 against the committed manifest** and abort on mismatch.
2. Run **the production code path** — same modules, same thresholds, same audit writer. Only two things are swapped: the frame source (bundle instead of camera) and the money source (recorded webhook fixtures instead of the live tunnel). Replay mode is selected by one flag, and it is the same flag the demo uses as its no-network insurance.
3. Pin everything: deterministic seed, model file hashes, OpenCV and ORT versions, thread count.
4. Emit `bench/out/{metrics.json, confusion/M1.csv, M2.csv, M3.csv, coverage_curve.csv, pair_diagnostics.csv, height_error.csv, ocr_conditions.csv, exceptions.jsonl, run.log}`.
5. **Diff `metrics.json` against committed `bench/expected.json` with per-metric tolerances and exit non-zero on regression.** The README numbers are therefore mechanically true or the build is red.
6. Append to `bench/runs.log`.

Test the clean-clone path on a **different machine** before submission. A `make bench` that only works in the author's shell is worse than none.

## 1.12 Where this loses — written, in the repo, linked from the README

1. **Identical-size flavour variants are unsolvable** by either axis. Correct output is ~100% abstention; measured and reported. If it is not ~100%, that is the headline of the issues field.
2. **Loose and weighed goods** — dal, rice, oil, produce — have no discrete placement event, no packet, no footprint. The camera cannot price them, and they are a large share of a real basket. This is a coverage *inversion*: the system catalogs precisely the goods that already carry MRP and barcodes, and misses precisely the goods where evidence would matter.
3. **Interleaved customers.** A kirana serves three at once. Sessions are bounded by a tap; `SESSION_AMBIGUOUS` is a first-class exception with a measured rate. The system handles one customer at a time and says so on camera.
4. **Hand-to-hand sales** that never touch the counter are invisible, unmeasured, unclaimed.
5. **Off-plane events abstain.** Geometry is valid only on the mat. The height-error curve is published.
6. **Ledger blindness.** Other rails' stickers are invisible; verdicts are dispositive only for targets this system minted.
7. **A model still chooses the price.** The line-crossing test decides *that* money moves; the embedding decides *how much*, because the SKU carries the price. The honest slogan is **not** "zero learned parameters between the shelf and the rupee." It is: *no model decides that money moves, and no model decides that money arrived; a model proposes what the item is, and that proposal must clear a published reject margin, is checked against physical size, is painted on the counter before any money moves, and can be reverted with one tap.* Longer, and unattackable.
8. **Scale.** One shop, one operator, one rig, 24 SKUs, ~300 events. No cross-shop generalisation is claimed.
9. **Night.** The lux floor at which auto-mint stops is published as a number.
10. **Thermals and battery.** Measured hours with and without the motion gate, not asserted.
11. **Self-reported.** Every number is reproducible, which is not the same as audited. No benchmark is made against Mashgin's 99.9% (never third-party tested) or Caper's published figures (loyalty engagement, conspicuously not recognition accuracy).

---

## PART 2 — PRIVACY ARCHITECTURE

## 2.1 Legal posture, dated correctly (most entrants will get this wrong in one direction or the other)

| Milestone | Date | Status today (2026-08-28) |
|---|---|---|
| DPDP Act 2023 enacted | 11 Aug 2023 | In force as an Act |
| DPDP Rules 2025 notified | 13/14 Nov 2025 | — |
| Phase 1: Rules 1, 2, 17–21 | immediate | **In force** (Board constituted, complaints possible) |
| Phase 2: Rule 4, Consent Manager registration | ~14 Nov 2026 | ~2.5 months out — *during the internship* |
| Phase 3: Rules 3, 5–16, 22, 23 — notice, consent, safeguards, breach reporting, children's data, rights | **~13/14 May 2027** | **Not yet in force** |

**Operative regime today: IT Act 2000 + SPDI Rules 2011. Stated posture: we build to 13 May 2027, not to today's weaker rules.**

Three corrections to make explicitly, because each one is a common error a knowledgeable judge will catch:

- **DPDP has no "sensitive personal data" tier.** It deliberately abolished SPDI-style categorisation; everything is "personal data." The widely-repeated "DPDP treats faces as sensitive data requiring explicit consent" is wrong as a matter of statute. The biometric-as-sensitive rule with **written** consent lives in **SPDI Rules 3 and 5**, which are a *today* argument with an expiry date.
- **The argument that survives the transition is children.** s.2(k) sets the age at 18. s.9(1) requires verifiable parental consent (Rule 10's methods: government ID, birth/school records, DigiLocker token — absurd at a shop counter). **s.9(3) is an absolute prohibition on tracking or behavioural monitoring of children, not waivable by parental consent**, up to ₹200 crore. Indian children are sent to the kirana daily. Lead with s.9; support with SPDI; name the transition rather than being caught by it.
- **Do not claim exemptions you do not have.** s.3(c)(i) (personal/domestic) does not apply — this is commercial. **"On-device therefore exempt" is false and is the fastest way to lose this question**: processing is processing regardless of locality. The defensible claim is three-part: (a) minimisation — the frame is masked before any model sees it; (b) immediate erasure under s.8(7); (c) the persisted artifact carries no identifier, and non-personal data is outside the Act.

**Fiduciary allocation.** The merchant determines purpose and means, so the merchant is the data fiduciary; the vendor receives zero bytes and is arguably not even a processor. But s.8(1) makes the fiduciary responsible "irrespective of any agreement to the contrary" — so the correct move is not a EULA, it is **generating the merchant's compliance artifacts for them** (§2.6).

## 2.2 The mount is the privacy architecture (correct a real geometric error)

**Nadir, not oblique.** 40–60 cm above the counter, aimed straight down, on a named gooseneck clamp or shelf bracket — not a ₹200 desk stand, which sits *on* the counter at 15 cm and gives an oblique view.

This single physical decision does four things at once:

1. It makes the counter mask **true**. Under an oblique mount, anything *between* the camera and the counter plane projects *inside* the quadrilateral — so a face leaning over the counter is fully visible in the "masked" buffer, and the scripted demo beat ("face over the counter goes black") is geometrically inverted and would either fail live or be staged misleadingly. Under a nadir mount, a person standing at the counter is genuinely outside the quad.
2. It removes the top-face parallax term from the footprint measurement (displacement → 0 on the optical axis).
3. It tightens the bounding geometry toward the true footprint.
4. It makes the placement-mat interaction natural.

**Document the discovery.** "I found that my privacy claim was false under the mount I had specified, and that the fix was also the fix for my measurement error" is a first-rate entry in the field the organisers read first.

## 2.3 Data classes: captured / processed-and-discarded / stored / never

### CAPTURED (exists in RAM only, never touches disk)

- 720p frames from a nadir camera, **cropped to the ArUco/ChArUco counter quadrilateral at frame-grab**, before the buffer is handed to any consumer. Pixels outside the quad are zeroed in the `ImageBitmap`; the unmasked frame is never referenced after the crop call. This is ~8 lines and they should be quotable in the README.
- A short in-memory ring buffer (N frames, stated duration, **memory only**, zeroed on shift close) backing tap-to-revert.

### PROCESSED AND DISCARDED, within one function call

Masked tensor · detection boxes · object contour/mask · the **item's** embedding vector (a product, not a person) · OCR ROI crop and the decoded string (hashed, then dropped) · webhook fields `email`, `contact`, raw `vpa`, the entire `card` object (**dropped on receipt**).

### STORED (one SQLite file on the counter box, encrypted at rest with an OS-keystore key)

| Table | Fields | Classification |
|---|---|---|
| SKU gallery | embeddings + canonical crops of **products** | Non-personal (pictures of biscuit packets) |
| Sell events | `session_id, sku_id, qty, ts, amount_paise, frame_sha256, item_thumb_96, decision_inputs` | No identifier → non-personal → outside DPDP scope |
| Ledger mirror | `payment_id, amount_paise, status, created_at, HMAC(utr), HMAC(vpa)` | Salt in device keystore, never leaves |
| Audit log | append-only, one schema, includes `execution_tier` | — |
| Exceptions | as §1.10 | — |

**The black-box resolution, stated precisely.** "Click a ledger line, jump to the frame" appears to require retaining frames. It does not. What is stored is (a) the **SHA-256 of the masked frame** — integrity without retention — and (b) a **96×96 greyscale thumbnail of the item's bounding box only**, not the scene. The audit shows *the biscuit packet*, not the room. Optional, off by default, merchant-toggled: a 24-hour masked-frame retention mode for dispute resolution, with the retention period printed on the counter card.

### NEVER — enforced by tests, not promised in prose

| Refusal | Enforcement |
|---|---|
| No face model in the binary | CI job greps the dependency tree and the model directory; **build fails** |
| No microphone | `RECORD_AUDIO` absent from the manifest; permissions test asserts it |
| No gait, re-identification, dwell-time, emotion, demographics, headcount | Absent by construction; named in the refusal list |
| No raw VPA / email / phone persisted | Schema test asserts the columns do not exist |
| No card data of any kind | RBI card-on-file storage prohibition since 30 Jun 2022; Razorpay never returns the PAN anyway |
| No egress except one allowlisted host | Network-policy test fails CI when a new host appears; the demo shows the network log with exactly the right number of lines |
| No frames leave the device | Egress policy + the above |

*(Note the one honest wrinkle and state it: a face detector **is** used — in `tests/`, in the harness that proves the mask works. It is never in the shipped bundle, and the CI check exempts the test directory by explicit path. Say this, or the CI claim and the zero-detection measurement contradict each other and someone will notice.)*

## 2.4 Privacy reported as a metric, not as a promise

Run an off-the-shelf face detector against **your own post-mask frames** and report it exactly the way Track 02 demands metrics be reported:

> "0 detections across N frames at operating resolution, including M frames deliberately shot with a person leaning over the counter."

Do not quote a generic pixel threshold from a blog. Measure your own system. This is a held-out-set discipline applied to a privacy claim, and no other entrant will do it.

Also report the **residual honestly**: hands are in frame by necessity. Hand landmarks are probably not "biometric information" under SPDI Rule 3 (whose qualifier is "used for authentication purposes"), but a high-resolution palm crop could carry palm-print detail. So: no landmark arrays persisted, no palm crops written, and **state the reasoning and the mitigation rather than asserting the conclusion**.

**Resolution as a designed control.** Downscale and greyscale to the minimum at which ArUco detection and the placement/removal test still hold. Publish that resolution as a stated privacy parameter with the measured accuracy at each step. It reframes a performance optimisation as a safeguard and gives you a real trade-off curve.

## 2.5 The customer's phone screen — the hole nobody else defends

This is the only place the product deliberately reads a **third party's device**, and every version of this plan spends fifteen paragraphs on faces and one clause here. Fix it:

- **Interaction rule:** the customer holds and **presents** their own phone. The merchant never takes it. Presentation is the consent act, and it is the reason this is not ambient capture.
- **Technical rule, one function call:** detect screen quad → rectify → crop to the numeric ROI by layout anchor → OCR → HMAC the identifier → **drop the string, drop the crop, drop the frame**. No thumbnail is retained for a screen event — an explicit, documented exception to the item-thumbnail rule. The audit line carries `HMAC(identifier)`, `amount_paise`, `verdict`, `frame_sha256`, and nothing else.
- **Say the uncomfortable part out loud:** the full screen is momentarily in memory in order to locate the ROI. We do not pretend otherwise. The mitigation is retention (zero), egress (zero) and duration (one call) — not capture.
- **No escalation.** If OCR abstains, the system abstains. It never ships the crop to a bigger model or to a server.

## 2.6 The on-screen notice and the printed card

**In-app persistent strip (always visible):**

> `counter only · 0 frames stored · 0 bytes off device · no face model`

Tappable to a **"Kya dekha?"** panel: the last N events, what was retained for each, and a one-tap **Erase today's data**.

**Printed A5 counter card, generated by the app on first run** (bilingual; Rule 3 requires English or one of the 22 Eighth Schedule languages), with the shop's name filled in, purposes itemised, retention stated, and the owner's own phone as the s.8(9) contact:

> **CAMERA IN USE — GOODS ONLY.** This shop uses a phone camera to add up your bill and make a payment QR. It looks only at the marked rectangle on this counter. It does not record video. It does not record sound. It does not recognise faces — no face software is installed. If you show your payment screen, it reads only the amount and the reference number, and keeps neither picture nor screen. Nothing from the camera leaves this phone. We keep only what was sold and the payment reference. **Don't want it? Say so — we will add up your bill by hand.**
> Questions: [Shop] · [Owner] · [Phone]

> **कैमरा चालू है — सिर्फ़ सामान पर।** इस दुकान में बिल जोड़ने और पेमेंट QR बनाने के लिए फ़ोन का कैमरा इस्तेमाल होता है। यह सिर्फ़ काउंटर पर निशान वाले हिस्से को देखता है। वीडियो रिकॉर्ड नहीं होता। आवाज़ रिकॉर्ड नहीं होती। चेहरा नहीं पहचानता — चेहरा पहचानने वाला सॉफ़्टवेयर इसमें है ही नहीं। अगर आप अपनी पेमेंट स्क्रीन दिखाते हैं, तो सिर्फ़ रकम और रेफरेंस नंबर पढ़ा जाता है, न फ़ोटो रखी जाती है न स्क्रीन। कैमरे से कुछ भी इस फ़ोन से बाहर नहीं जाता। सिर्फ़ यह रखा जाता है: क्या बिका और पेमेंट का नंबर। **नहीं चाहिए? बता दीजिए — हम हाथ से बिल बना देंगे।**
> सवाल: [दुकान] · [मालिक] · [फ़ोन]

**A consent regime with no refusal path is not consent.** The one-tap "bill by hand" button exists in the app and is named on the printed card. That is criterion 4 — failure recovery — applied to ethics rather than to code.

## 2.7 Merchant-facing disclosure (one page in the repo, `PRIVACY-MERCHANT.md`)

Written for the shopkeeper, not for a lawyer: **you** are the data fiduciary and we receive nothing; here is the exact list of what the device stores and for how long; here is how to erase a day, a customer, or everything, in one tap; **if the phone is lost, the database is encrypted with a key held in the device keystore with no escrow and the data is unrecoverable — that is deliberate**; here is what this product refuses to build and why; here is the CI check you can look at yourself.

**Breach posture, framed as operations rather than as legal opinion:** an architecture that never creates personal data has no reportable breach, no Rule 7 72-hour clock, no ₹250 crore ceiling to sit under, and no data-principal rights requests to service. Privacy here does not cost money; it *deletes an operational surface*.

## 2.8 The refusal that costs you the bigger business — put it in the issues field

> The strongest wound I found in this market was not billing. It was goods leaving unbilled while the owner is away from the counter — shrinkage, with a hired helper at ₹10–15k/month. That is a loss the owner has personally suffered, and it is what the one funded company in this space (Kirana AI, YC S25) sells in the US. I refused to build it. The same masked-quad architecture that makes this not-surveillance forbids watching the person behind the counter, and a product that surveils the helper is a product the owner cannot install without changing his shop. So I built the version that watches the goods and not the people, and I am telling you that this cost me the bigger business.

Criterion 1, criterion 3 and criterion 4 in one paragraph. Nobody else will have anything like it.

## 2.9 What makes this a differentiator (five things, all verifiable in under a minute)

1. The mask is a **mechanism**, not a policy — enforced by geometry, delivered as an eight-second silent gesture.
2. The **CI check** makes "no face model, no microphone, one allowlisted host" falsifiable in five seconds on a public repo.
3. Privacy is **reported as a metric** with a measured zero-detection count.
4. The **generated printed compliance card** is an artifact no other entrant will ship, and it photographs well.
5. The **refusal list carries statutory citations and its own cost** — including the business it forfeits.

Compress it in the video to *one mechanism* (the mask) plus *one artifact* (the card). Everything else lives in the README for the judge who digs. A six-part privacy section reads as a compliance appendix and gets skipped.

---

## PART 3 — THE 8-DAY PLAN (2026-08-28 → 2026-09-05)

Eight working days plus a submission morning. Two hard rules govern everything below:

> **Rule 1 — Nothing ships without a number.** A capability without a measured entry in `metrics.json` actively *lowers* the score under criterion 2. Six measured things beat sixteen unmeasured ones.
> **Rule 2 — Measurement infrastructure is built before the last feature.** Dataset frozen Day 3; first end-to-end bench Day 4; headline table Day 5. If a gate fails, cut a feature — never add one to compensate for a missing number.

## 3.1 Day by day

### Day 0 — Friday 28 Aug (tonight, ~4 h). Unblock only. Write no product code.

- `git init`, **push a public repo** with `LICENSE` (Apache-2.0), README skeleton, and `bench/` scaffolding. The repo URL is a submission field; an empty repo on Day 7 is a real failure mode.
- **File the `type=upi_qr` activation request**, and in the same ticket ask explicitly how to simulate a test-mode credit.
- Stand up the webhook receiver with **real HMAC signature verification** behind a tunnel; get `qr_code.created` to land in a log.
- Deploy `razorpay-mcp-server` **locally** — `close_qr_code` and `create_refund` are unsupported on the hosted remote server (README ~line 48).
- **Confirm the deadline and eligibility** from the actual application form. 5 Sep is reported, unconfirmed on razorpay.com, and the page reportedly says "Students only."
- Order or assemble the **nadir mount**; print the ChArUco / four-corner mat.
- Draft the **"what issues did you face"** field. It is ~80% writable today and it is read first.

**GATE G0 (23:00):** Is a signature-verified webhook landing in your log? **No → tomorrow starts on `create_payment_link_upi`, not on QR.**

### Day 1 — Saturday 29 Aug. The money path, thin, and the insurance footage.

- Mint → webhook → a green state in a CLI. That is the entire chain, with no camera in it.
- **Film one real credit turning a screen green, today.** This is the only beat outside your control and the only one that cannot be re-shot on Day 7 if the account is blocked. Get the footage in hand.
- ArUco/ChArUco detect → `findHomography` / `solvePnP` → perspective glyph render on a static frame.
- Measure the **join rate** (§1.9) on whatever real payments you can make: does the on-screen identifier appear in the Razorpay payload?

**GATE G1 (20:00):** Has a real webhook turned something green on camera? **No → execute the fallback ladder now** (§3.3, R1), not on Day 6.

### Day 2 — Sunday 30 Aug. Geometry, and the decision that de-risks everything.

- Placement-mat interaction (place → measure → lift), not wave-across-a-line.
- Classical item segmentation: `absdiff` against a maintained empty-mat reference → threshold → `morphologyEx` → `findContours` → `minAreaRect` → `boxPoints`. All confirmed present in the `@techstark/opencv-js` 4.11 contrib wasm. This gives **oriented** boxes and masks, which the footprint measurement requires and an AABB detector cannot supply.
- **σ calibration:** 81 measurements (9 positions × 3 orientations × 3 re-seatings). **Height-error curve:** 0/1/3/5 cm shims.
- Decide the detector: classical vs RF-DETR, **with the measured comparison** committed either way.

**GATE G2 (20:00):** Do the size pairs separate by `> 3σ`? **No → the footprint tiebreak is demoted from "the fix" to "a tiebreak that sometimes works", abstention takes the load, and the PRD language changes today.** This is the single most valuable gate in the plan, because discovering it on Day 6 costs the headline table and the issues field.

### Day 3 — Monday 31 Aug. **DATASET DAY.** Nothing else happens.

- Buy the 24 SKUs (two physical instances each where budget allows). **Arrange the shop access for Day 7 while you are out buying.**
- Morning: enrolment capture (24 × 8, tube light).
- Midday: **DEV** split.
- Afternoon/evening: **HELD-OUT** — different light, mount re-seated, second pair of hands, all motion classes and adversarial pairs.
- Label from the scripted run sheets; adjudicate mismatches by frame.
- **Tag, sha256-manifest and push the bundle before the matcher exists.** Record the tag date.

**GATE G3 (22:00):** Is the eval bundle tagged, manifested and pushed? **No → everything stops until it is.** This gate has no fallback because there is no substitute artifact.

### Day 4 — Tuesday 1 Sep. Identity, gates, audit.

- Embedding gallery **and** the B1 histogram baseline, side by side.
- Reject margin + contact-edge tiebreak + abstain path + amber-as-enrolment (one tap → in the gallery forever).
- Line crossing / placement event + tracking + **both** loss counters (`dropped_track`, `crossed_without_tracker_id`).
- Audit log: one schema, frame SHA-256, 96×96 item thumbnail, execution tier stamped into every line.
- `make bench` runs end to end **on DEV only**.

**GATE G4 (21:00):** Does the pipeline emit a valid `metrics.json` from a single command? **No → cut all P1 tomorrow and spend Day 5 finishing the harness.**

### Day 5 — Wednesday 2 Sep. Metrics, baselines, privacy.

- Sweep τ on DEV; choose the operating point in rupees; **run HELD-OUT once** and log the run.
- Three confusion matrices, pair diagnostics, coverage curve, AURC, the two anchor points.
- **B0 baseline:** time the shopkeeper on the same 50 baskets. Publish whatever happens.
- Exception list + summary table.
- Privacy block: mask verification, CI checks (face model / audio permission / egress allowlist), zero-detection measurement, generated counter card PDF.

**GATE G5 (20:00) — THE HONESTY GATE.** The numbers are what they are. If overcharge exceeds target, **raise τ and publish the coverage cost. Do not tune on the test set.** If the per-basket touch rate exceeds ~40%, the pitch framing must be "the record, not the speed" — which it already is, so this gate is survivable by design.

### Day 6 — Thursday 3 Sep. The coda, then freeze.

- Offline queue + amber-pending + reconnect drain (the staged failure).
- Tap-to-revert on the painted total.
- Hindi lexicon: ~130 clips. **Hindi 1–99 are irregular** (ikkis, baais, teis, chauhattar) — they do not compose from tens plus units. Budget half a day and unit-test the 100 / 200 / 214 / 240 / 1000 boundaries. Getting this audibly wrong in front of an Indian panel is the worst possible outcome for the one feature whose entire job is market credibility.
- **Agent coda, timeboxed to 6 h:** shop-side MCP server (`search_shelf`, `get_item`, `check_stock`, `create_cart`, `checkout`) composed with `razorpay-mcp-server`; an ACP-shaped local/pickup feed; **reserve-then-verify** (agent order paints the reserved slot, the same placement test confirms it left). Zero camera risk, highest AI leverage, and the only artifact a judge can still interact with a week later.

**GATE G6 (18:00) — CODE FREEZE for anything that appears in the video.** After this, only README, bench and video work. Unfinished items are cut and listed with a reason.

### Day 7 — Friday 4 Sep. **VIDEO DAY — the whole day.**

- Shoot in a **real shop, on a real counter, with a real shopkeeper's hands**, and get one sentence from him about a loss he has personally suffered. A wooden board on a desk turns a problem-taste submission into a literature review with a prop, and it is visible in one frame.
- Rehearse the take **six times** with the real webhook in the loop; pre-warm the connection before rolling.
- **Shoot the take with the network beat last**, so a webhook stall costs only the tail.
- Shoot the mask gesture **separately** (10 s, silent, no narration).
- Screen-record: `make bench` running live, the coverage curve, the three matrices, the no-model-zone diagram, the four refusals, the agent coda, the cut list.
- Edit.

**GATE G7 (22:00):** Is there a complete, watchable five-minute cut? **If not, ship the rough cut.** A rough complete video beats a polished half.

### Day 8 — Saturday 5 Sep, morning only. Submit.

- Final README pass; `make bench` verified **from a clean clone on a different machine**.
- Fill the twelve fields. The "what issues did you face" field is the one drafted on Day 0 and now true.
- Repo public, LICENSE present, dependency table present, `bench/runs.log` committed.
- **Submit by 14:00 local, not 23:59.** Leave buffer for a portal failure.

## 3.2 Hard cut list, in the order things die

When a gate fails, cut from the top of this list, not from the bottom.

| # | Cut | Trigger |
|---|---|---|
| 1 | Everything already cut across all four theses — MUDRA, JUGNU, KHATA LIFT, GINTI, ggwave/SUR, KAMPAN in **all** forms including "conditional half-day" | Pre-cut. Do not resurrect. "Demoted" features are how eight days become twelve. |
| 2 | PEEL Tier 2 (payload regeneration + module XOR) | Pre-cut. It is a QR-forgery primitive guarded by a deletable unit test. If the sticker beat is wanted at all, use the **enrolment-photo diff** (photograph the merchant's own sticker once at setup, store the rectified module grid, diff against it) — identical visual, identical Hamming number, no generator anywhere. |
| 3 | Signed JWS feed, `/.well-known/ucp`, capability-passport **UI**, thermal-governor **UI**, undo **scrub bar** | Any gate slip. Keep the capability tier as an audit-log *field* (free); drop its screen. Tap-to-revert replaces the scrub bar at a tenth the cost. |
| 4 | The OCR / screen-verdict channel entirely | G4 or G5 slip. The **ledger mirror stays** (green-on-credited needs it). Honest roadmap line: *"the lookup half is built; the OCR half is one week."* That is a better answer in the field read first than a rushed unmeasured detector. |
| 5 | The agent coda | G6 slip. Painful — it is the best "what did they invent" beat — but it is 6 h that must not eat the freeze. |
| 6 | The Hindi voice, down to the amber earcon only | G6 slip. The earcon (form carrying uncertainty, so the system cannot bluff aloud) is the valuable half. |
| 7 | Held-out set size, 300 events → 180 | G3 slip only. Cut *breadth of motion classes*, never the adversarial pairs or the distractors. |
| 8 | The embedder, replaced by the B1 histogram baseline | G2 or G4 slip. This is a *good* cut: "I deleted the object detector / the embedder, and here is the measured comparison" is a stronger criterion-3 artifact than either choice alone. |

**Never cut:** the frozen dataset, `make bench`, the coverage curve, the exception list, the counter mask, the CI checks, the green-only-on-signature-verified-webhook rule, the offline amber-pending beat.

## 3.3 Risk register

| ID | Risk | Detect by | Fallback ladder |
|---|---|---|---|
| **R1** | **`qr_code.credited` cannot be triggered in test mode; `type=upi_qr` is on-demand-activation.** Three account-level dependencies under the one beat that makes this a payments submission. | G0 / G1 | (a) `create_payment_link_upi` — no activation, real signature-verified webhook, "QR" becomes "payment link" at near-zero visual cost; (b) **one genuine live-mode ₹1 payment to yourself, filmed once, amount visible** — treat this as the *plan of record*, not the third rung; (c) last resort, a webhook replay **labelled on screen and in the README** with the fixture committed. Being caught faking green destroys every honesty point; labelling one costs a caption. |
| **R2** | Homography σ too large; size pairs do not separate | G2 | Demote footprint to "sometimes works", let abstention carry the load, publish the σ and the height curve as the finding. The PRD language changes on Day 2. |
| **R3** | Browser inference too slow / RF-DETR ops unsupported under ORT-Web | G2 | Phone = eye/display/mouth, laptop or ₹6,000 mini-PC = brain, over LAN. **Put it in frame and name it in the first ten seconds.** "No laptop in frame" next to an architecture doc that says the brain is a laptop is the one honesty failure this submission cannot afford. |
| **R4** | Shop access falls through for Day 7 | Day 3 | Second-choice shop identified on Day 3; last resort, a real counter in a real shop for the 15-second social cut only, with the full take at a domestic counter and the compromise stated. |
| **R5** | Take never lands cleanly | G7 | Cut to four beats. Accept one hidden cut on a hand-over-lens or whip-pan — a hidden cut costs far less than a failed live beat. Insurance footage from Day 1 covers the green transition. |
| **R6** | Held-out numbers are bad | G5 | Publish them. Raise τ, report the coverage cost, lead with "I refused to price 14% of items and overcharged ₹0". Do not re-run more than three times; the run log is public. |
| **R7** | On-screen identifier does not join to the Razorpay payload | Day 1 (join rate) | Verdict degrades from *exact* to *bounded* (amount + 120 s window); both are measured and reported. If the join rate is very low, cut the channel per cut-list #4. |
| **R8** | Hindi number grammar sounds wrong | Day 6 boundary tests | Ship digits + status words only; drop composed amounts. Silence beats an audibly wrong "do sau chalees" in front of an Indian panel. |
| **R9** | Deadline or eligibility differs from assumption | G0 | Confirmed from the application form on Day 0. If earlier, collapse Days 5–6 and ship after G5. If "students only" excludes the builder, that is worth knowing before spending eight days. |
| **R10** | Thermal / battery / backgrounding kills the propped phone mid-take | Day 6 | Plugged in, screen on, PWA installed — state it as an explicit operating condition. Soundboxes are plugged in too; this is honest and costs nothing. |

## 3.4 Gate summary

| Gate | When | Question | Fail action |
|---|---|---|---|
| G0 | Fri 23:00 | Signature-verified webhook landing? | Switch to payment links tomorrow |
| G1 | Sat 20:00 | Real webhook turned something green **on camera**? | Execute R1 ladder now |
| G2 | Sun 20:00 | Size pairs separate by >3σ? | Demote footprint; rewrite the PRD line today |
| G3 | Mon 22:00 | Eval bundle tagged, manifested, pushed? | **Everything stops until it is** |
| G4 | Tue 21:00 | `make bench` emits valid `metrics.json`? | Cut all P1; Day 5 is the harness |
| G5 | Wed 20:00 | Numbers acceptable at the chosen τ? | Raise τ, publish the coverage cost, **do not tune on test** |
| G6 | Thu 18:00 | **CODE FREEZE** for anything on camera | Cut unfinished; list it with a reason |
| G7 | Fri 22:00 | Complete watchable cut exists? | Ship the rough cut |

**When to record:** insurance footage of the green transition on **Day 1**; the full shoot on **Day 7**, all day, in a real shop.
**When to freeze:** demo-visible code at **Day 6, 18:00**; all code except README and bench at **Day 7, 18:00**.

## 3.5 One instruction that outranks the rest

The directory currently holds 28 reference checkouts and ~235 KB of planning prose and **zero lines of product code, eight days out**. That is more upfront analysis than most senior engineers do in a quarter, and it is now the only thing standing between this and a submission. Every additional day of research from here has negative expected value.

**Ship the chain. Shoot the dataset. Publish the amber rate.**



---

# PART V — REPOS, MODELS, OPEN QUESTIONS

wasm | 260 stars; local checkout `reference/zxing-wasm` | MIT | QR decode **and write**. Exposes `ReadResult.symbol` (one-channel image of the symbol), `.version`, `.ecLevel`, `extra.DataMask`; `WriterOptions` accepts `options="version=N,dataMask=M,ecLevel=X"` with `WriteResult.symbol`. Used for: rendering the Payment Link `short_url` as a local QR, the pairing QR, and (P1) the sticker-registry module grid. **Both `symbol` fields are marked `@experimental` in `src/bindings/{readResult,writeResult}.ts` — budget half a day to check whether their quiet-zone and resolution conventions align before promising the module diff** |
| **Xenova/mobileclip_s0** | https://huggingface.co/Xenova/mobileclip_s0 | vision tower **11.85 MB int8** (measured content-length) | Apple ML (permissive) | The only P0 model on the money path. Runs once per confirmed crossing, ~0.5 Hz |
| **ChaoningZhang/MobileSAM** | https://github.com/ChaoningZhang/MobileSAM | 5,900 stars; encoder 5M params / **8 ms per image** (vs SAM ViT-H 611M / 452 ms); full pipeline 9.66M / 12 ms; local checkout `reference/MobileSAM` | Apache-2.0 | **Enrolment only** — background removal so a new SKU's gallery embedding is not contaminated by counter texture. Never per-frame |
| **AI4Bharat/IndicF5** | https://github.com/AI4Bharat/IndicF5 · https://huggingface.co/ai4bharat/IndicF5 | 130 stars; 0.4B params; **1,417 hours**; 11 Indian languages (Rasa, IndicTTS, LIMMITS, IndicVoices-R) | MIT | **Build time only.** Generates the 137-clip Hindi lexicon. Needs reference audio + transcript at inference |
| **ai4bharat/indic-parler-tts** | https://huggingface.co/ai4bharat/indic-parler-tts | 0.9B params, 21 languages (20 Indic + English), 69 voices | Apache-2.0 | Alternative lexicon generator with the most permissive licence and widest language coverage |
| Sarvam Bulbul v3 (API) | https://docs.sarvam.ai | ₹30 per 10,000 chars; **₹100 free credit**; Starter rate limit 60 req/min | Commercial | Alternative lexicon generator. ~137 clips × ~25 chars ≈ ₹90 total, inside the free credit. **Build time only — never a runtime call** |

### 13.2 Evaluated, documented, deliberately not shipped

| Repo / model | URL | Numbers | Why not |
|---|---|---|---|
| **ultralytics (YOLO26)** | https://github.com/ultralytics/ultralytics · https://docs.ultralytics.com/models/yolo26 | 61,043 stars; v8.4.131 (2026-08-27). YOLO26n: 40.9 mAP50-95 (40.1 e2e), **38.9 ± 0.7 ms CPU ONNX @640 on Xeon @2.00 GHz**, 1.7 ± 0.0 ms T4 TensorRT, 2.4M params, 5.4B FLOPs. Family: 26s 48.6/2.5ms/9.5M · 26m 53.1/4.7ms/20.4M · 26l 55.0/6.2ms/24.8M · 26x 57.5/11.8ms/55.7M. NMS-free dual head, DFL removed (−12% head params, −20% head FLOPs), STAL, MuSGD (47.4 mAP in 500 epochs vs 47.0 in 600). Local checkout `reference/ultralytics` | **AGPL-3.0, LICENSE §13 network clause** — would virally license the mandatory public repo. One paragraph in `docs/why-not-ultralytics.md` |
| **roboflow/rf-detr** | https://github.com/roboflow/rf-detr | 9,084–9,100 stars. RF-DETR-N: 67.6 AP50 / **48.4 AP50:95**, 2.3 ms, 30.5M params, 384×384. S 72.1/53.0/3.5ms/32.1M/512 · M 73.6/54.7/4.4ms/33.7M/576 · L 75.1/56.5/6.8ms/33.9M/704 · XL 77.4/58.6/11.5ms/126.4M · 2XL 78.5/60.1/17.2ms/126.9M. All T4, TensorRT 10.4, CUDA 12.4, FP16, batch 1. Seg-N 63.0/40.3/3.4ms/33.6M/312 → Seg-2XL 73.1/49.9/21.8ms/38.6M/768 | Apache-2.0 core + Nano–Large weights (`rfdetr_plus`, XL/2XL are PML 1.0). **Ships as the published ablation behind `--detector=rfdetr`, not as the default.** Roboflow explicitly recommends it over YOLO26 for cross-domain transfer — relevant because a real kirana counter is out-of-distribution for COCO |
| **google/siglip2** | https://huggingface.co/google/siglip2-so400m-patch14-384 | 761,299 downloads/month; sizes B 86M / L 303M / So400m 400M / g 1B; 109 languages; NaFlex variants. nyris: **40.54 R@1 / 32.98 mAP@20** | apache-2.0, and it wins on mAP@20 — but SigLIP-base vision is 371.8 MB fp32 / **99.5 MB int8**, unshippable on a 4 GB Android alongside a 720p pipeline (~137 s cold load at 5 Mbps). Wrong size for a 24-SKU bounded gallery |
| **facebookresearch/dinov3** | https://github.com/facebookresearch/dinov3 | 11,300 stars. ViT-S/16 21M · S+/16 29M · B/16 86M · L/16 300M · H+/16 840M · 7B/16 6,716M; ConvNeXt T/S/B/L 29/50/89/198M; LVD-1689M. nyris best open: **40.58 R@1 / 29.73 mAP@20** (+9.05 R@1 over DINOv2-Large) | **Custom "DINOv3 License", not OSI.** On a mandatory public repo that is a real risk |
| **onnx-community/dinov2-small** | https://huggingface.co/onnx-community/dinov2-small | **88.5 MB fp32 / 24.4 MB int8** | Fallback embedder if MobileCLIP-S0 underperforms the histogram baseline. Apache-2.0 lineage |
| **PaddlePaddle/PaddleOCR** | https://github.com/PaddlePaddle/PaddleOCR · monorepo `/paddleocr-js`, npm `@paddleocr/paddleocr-js`; local checkout `reference/PaddleOCR` | 88,400 stars; 3.7.0 (11 Jun 2026) ships PP-OCRv6 tiny 1.5M / small 7.7M / medium 34.5M, 50 languages. PP-OCRv5 mobile 5M params; weighted avg accuracy **80.1% vs 53.0% (v4)**; OmniDocBench normalised edit distance 0.067 overall / 0.058 EN / 0.076 ZH / **0.012 at 90°** / 0.139 at 270°; 22.6M training samples. Measured: `PP-OCRv5_mobile_det` 4.69 MB + `_rec` 16.46 MB ≈ **21.2 MB**. `devanagari_PP-OCRv5_mobile_rec` 84.96% on 3,611 text images. Browser SDK is ONNX Runtime Web + OpenCV.js, Node ≥ 20.11 | Apache-2.0. **Cut with PAKKA's OCR half.** Note for anyone quoting it: the 41.7% handwriting figure is **handwritten Chinese**, never Hindi |
| **facebookresearch/EdgeTAM** | https://github.com/facebookresearch/EdgeTAM · arXiv 2501.07256; local checkout `reference/EdgeTAM` | **16 FPS on iPhone 15 Pro Max** unquantized, 22× faster than SAM 2 (0.7 FPS); J&F 87.7 DAVIS 2017 / 70.0 MOSE / 72.3 SA-V val / 71.7 SA-V test | Apache-2.0. **Evaluated and deliberately excluded from the money path** — a box centroid crossing a line is sufficient; a mask adds nothing to a money decision |
| **chongzhou96/EdgeSAM** | https://github.com/chongzhou96/EdgeSAM | First SAM variant **>30 FPS on an iPhone 14**; ~40× faster than SAM, ~14× faster than MobileSAM on edge; +2.3 mIoU COCO, +3.2 LVIS | Alternative to MobileSAM if enrolment must run on the shopkeeper's phone |
| **THU-MIG/yoloe** | https://github.com/THU-MIG/yoloe | 2,300 stars. v8-S 27.9 AP LVIS / 305.8 FPS T4 / **64.3 FPS iPhone 12** / 12.0h train; v8-M 32.6/156.7/41.7/17.0h; v8-L 35.9/102.5/27.2/22.5h. Beats YOLO-Worldv2-S by 3.5 AP at 1.4× speed and 3× less training cost | **AGPL-3.0.** Would have been the nightly catalog bootstrapper; cut with the AGPL rule |
| **SAM 3** | (Meta, released 19 Nov 2025) | ~30 ms/image on an **H200** for 100+ objects; 30 FPS video needs **2 H200s for ≤10 objects, 4 for ≤28, 8 for ≤64**. SA-Co: 214K phrases / 126K images+videos; 4M concept labels | Cannot be part of any live demo. Nightly-batch-only at best, and we have no H200 |
| **facebookresearch/audioseal** | https://github.com/facebookresearch/audioseal | 776 stars; 16 kHz and 24 kHz; **optional payload is 16 bits**; streaming detection since 0.2 | MIT (code **and** weights). **Rejected on arithmetic** — 16 bits cannot carry an HMAC, and no speaker-to-microphone robustness is published |
| **ggerganov/ggwave** | https://github.com/ggerganov/ggwave; local checkout `reference/ggwave` | **7,842 stars** (verified 2026-08-28, last push 2026-04-16); **8–16 bytes/sec**; 3 bytes per 6 tones, 4-bit chunks, 96 equally-spaced frequencies over 4.5 kHz, dF = 46.875 Hz, F0 = **1875.000 Hz audible / 15000.000 Hz ultrasonic**; Reed-Solomon ECC; `framesPerTx` 9/6/3; `bytesPerTx` 3 (audible/ultrasound), 1 (DT/MT); default 48 kHz. npm v0.4.0, 665 downloads/wk | MIT. **Cut.** Its README claims only "relatively robust towards background noise, although not perfect" — no SNR threshold, no distance limit, no decode curve. Precedent worth knowing: **Gibberlink won the global top prize at the ElevenLabs Worldwide Hackathon and went viral 2025-02-23** on exactly this trick |
| **hexgrad/kokoro** + `kokoro-js` | https://github.com/hexgrad/kokoro | 8,592 stars; 82M params; StyleTTS2 + ISTFTNet; kokoro-js v1.2.1, **126,413 npm downloads/wk**; 9 languages / 54 voices | Apache-2.0. **Hindi voices `hf_alpha`, `hf_beta`, `hm_omega`, `hm_psi` are all graded C by their own authors**, trained on tens of minutes each, total Hindi data 1–10 hours vs IndicF5's 1,417. Not the primary voice |
| **OHF-Voice/piper1-gpl** | https://github.com/OHF-Voice/piper1-gpl | 5,308 stars; `hi_IN-pratham-medium.onnx` = exactly 63,516,050 bytes (~63.5 MB); embeds espeak-ng | **GPL-3.0.** The original MIT `rhasspy/piper` (11,284 stars) is **archived read-only** since 2025-08-26. **The licence flipped** — do not link it into a permissive submission repo |
| **alphacep/vosk-api** | https://github.com/alphacep/vosk-api | 15,081 stars. `vosk-model-small-hi-0.22` 42 MB, WER **20.89** (IITM) / **24.72** (MUCS); `vosk-model-hi-0.22` 1.5 GB, WER 14.85/14.83/13.11; `vosk-model-small-en-in-0.4` 36 MB, WER 49.05 (NPTEL Pure); `vosk-model-small-te-0.42` 58 MB, WER 87.9 (Fleurs). `vosk-browser` 17,568 npm/wk | Apache-2.0. Not used — the mic never takes an instruction |
| **ai4bharat/indic-conformer-600m-multilingual** | https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual | 600M params, 22 languages, CTC + RNNT; **Hindi WER 13.2 on ARTPARK-IISc Vaani-Benchmark-V1.0** | MIT. Cited as the ceiling that justifies refusing voice commands |
| **snakers4/silero-vad** | https://github.com/snakers4/silero-vad | 10,076 stars | MIT. Not needed once AWAAZ is item-names-only |
| **huggingface/transformers.js** | https://github.com/huggingface/transformers.js; local checkout `reference/transformers.js` | v4.2.0 (16,277–16,300 stars); `@huggingface/transformers` **2,712,271 npm/wk**; `onnxruntime-web` **4,175,059 npm/wk**; `ModelRegistry.get_available_dtypes()` probes which quantisations a repo actually has | Apache-2.0. Not in the browser bundle for P0, but the download numbers are the credibility argument that browser ML is a normal production choice |
| **Li-Chongyi/Zero-DCE** | https://github.com/Li-Chongyi/Zero-DCE | CVPR 2020; **2 ms for 600×400**; zero-reference | If night mode ships: enhance the **display path only**, never before detection |
| **Nixtla/statsforecast** | https://github.com/Nixtla/statsforecast | `CrostonClassic`, `IMAPA`, `TSB` | Not shipped. Noted because kirana demand is **intermittent** — using Prophet or ARIMA here is a tell |
| **facebookresearch dot / rednote-hilab/dots.ocr** | https://huggingface.co/rednote-hilab/dots.ocr | 1.7B params; OmniDocBench edit 0.125 EN / 0.160 ZH; table TEDS 88.6/89.0; ~100 languages; needs CUDA + vLLM | MIT. Server-side escalation only; not shipped |
| **datalab-to/surya** | https://github.com/datalab-to/surya; local checkout `reference/surya` | 650M params; 83.3% olmOCR-bench; 87.2% on a 91-language internal set | **Code Apache-2.0 but MODEL licence is OpenRAIL-M** with use restrictions. Do not vendor weights |
| **Sec-ant/barcode-detector** | https://github.com/Sec-ant/barcode-detector; local checkout `reference/barcode-detector` | v3.2.2; ZXing-C++ WASM ponyfill; reads ean_13/ean_8/upc_a/upc_e/itf_14/code_128/DataBar/qr/data_matrix/pdf417/aztec | Fallback if any barcode path ships. **Critical design fact: the native `BarcodeDetector` returns only the decoded payload STRING, never the module matrix** — and it is Safari-17-behind-a-flag, Firefox-never, Chrome Android 83 / Chrome 88 |

### 13.3 Datasets — reference, never vendor

| Dataset | URL | Numbers | Licence note |
|---|---|---|---|
| **SKU-110K** | https://github.com/eg4000/SKU110K_CVPR19 · https://docs.ultralytics.com/datasets/detect/sku-110k/ | 11,743 images (8,219 train / 588 val / 2,936 test), **1,730,996 boxes**, ~147 objects/image, **ONE class literally named `object`**, >110,000 unique SKUs, 13.6 GB. Goldman et al., CVPR 2019 (arXiv 1904.00853); 848 stars / 189 forks | **No licence file.** Research-use with citation. Cite; do not vendor. *Its single-class annotation is the reason detection and identity are decoupled — a new SKU needs 8 photos, not retraining* |
| **RP2K** | https://arxiv.org/abs/2006.12634 · pinlandata.com/rp2k_dataset | >500,000 images of 2,000 products shot in physical stores under natural lighting, annotated with SKU ID, **size, shape and flavour/scent** | Non-standard `nonexclusive-distrib/1.0`. **The only public set annotating size and flavour variants** — i.e. the only public proxy for the Parle-G collision. Verify terms before any redistribution |
| **RPC checkout** | https://arxiv.org/abs/1901.07249 · rpc-dataset.github.io · https://github.com/DIYer22/retail_product_checkout_tools (41 stars) | 83,739 images = 53,739 single-product exemplars + 30,000 checkout-tray scenes, 200 SKU classes, ~12 products/tray, three clutter levels | Terms on the project site. **Its exemplar/checkout split mirrors our enrolment/held-out split exactly** |
| **Products-10K** | https://arxiv.org/abs/2008.10545 | 10,000 fine-grained classes, >150,000 images, JD.com expert-labelled, stated error <0.5%, hierarchical label graph | Research-use. Useful for a family-then-variant two-stage decision |
| **nyris Visual Product Search Benchmark** | https://benchmark.nyris.io · arXiv:2603.17186 | 11 models × 6 datasets = 60 evaluations, image-to-image retrieval, no post-processing. Full R@1/mAP@20: GEM v5.1 56.84/48.88 (proprietary) · DINOv3 ViT-L/16 40.58/29.73 · SigLIP2 SO400M 40.54/32.98 · PE-Core L/14 40.39/32.40 · Gemini Embedding 2 38.87/31.92 · Vertex AI Multi-Modal 38.67/32.14 · Cohere Embed v4 33.67/27.07 · DINOv2 Large 31.53/21.47 · Jina v4 27.60/19.86 | **Cite this in the PRD and the README to pre-empt "why not just use CLIP" — and to state the honest ceiling before a judge can spring it** |
| **IIIT-HW-Dev / IIIT-INDIC-HW-WORDS** | — | 95K handwritten Devanagari words; 872K words across 8 Indic scripts from **135 writers** | Cited as the reason KHATA LIFT was cut. **No published Devanagari CER could be verified — UNVERIFIED** |
| **Open Food Facts** | https://world.openfoodfacts.org/api/v3 · https://in.openfoodfacts.org | India country page: **22,716 products**. Rate limits **15 read req/min/IP, 10 search req/min/IP**, IP bans documented; custom `AppName/Version (ContactEmail)` User-Agent required | Database **ODbL**, contents DbCL, images **CC-BY-SA**. **Use the offline JSONL/CSV export, never the live API** — it will break a demo loop. Keep OFF-derived columns physically separate so share-alike does not contaminate our catalog |
| **Indian currency (if GINTI ever revives)** | https://github.com/pankaj-2k01/Indian-Currency-Detection-Yolov4 · https://github.com/Gowtham-369/IndianCurrencyNotesDetection | 7 denomination classes (10/20/50/100/200/500/2000), **~450 images per class**, YOLOv4/v5-era, clean conditions | Too small and too old for counter conditions. Budget a full day to shoot your own — which is why GINTI is cut |

### 13.4 Prior art to cite, differentiate from, and never vendor

| Repo | URL | Stats | Relationship |
|---|---|---|---|
| **Eben-Siyabalapitiya/KARTX** | https://github.com/Eben-Siyabalapitiya/KARTX | 0 stars, MIT, Python, created 2026-06-29, last push 2026-07-22 | **The closest thing on GitHub.** YOLOv8 + webcam + "virtual tripwire line" + running subtotal. Stops exactly where we start: no SKU identity beyond COCO-ish classes, no reject margin, no perspective AR, **no payment**. Cite as proof the tripwire primitive is sound and the unbuilt part is the money |
| **yorkeyao/Automated-Retail-Checkout** | https://github.com/yorkeyao/Automated-Retail-Checkout | 18 stars, MIT; CVPR AI City Challenge Track 4 | **116 product classes solved with 116,500 Unity-rendered synthetic images from 116 3D scans.** The bar the field set. Our few-shot enrolment (8 photos) is a radically cheaper story — *and* it means the field already knows near-identical SKUs are the hard part, so the collision must be **measured, not hidden** |
| **kongesque/locus-vision** | https://github.com/kongesque/locus-vision | 10 stars, MIT, SvelteKit 5 + FastAPI | Cleanest MIT reference for **directional** line-crossing semantics (A→B, B→A, both). Read it, then write your own ~30 lines |
| **Thanghuynh2808/Retail-Insight-Pipeline** | https://github.com/Thanghuynh2808/Retail-Insight-Pipeline | 1 star, no licence | Confirms YOLO-detect-then-embedding-match is the current consensus for open-set retail SKU ID. Has **no** reject margin, no abstain, no counter geometry, no payment — and reaches for an LLM for rule validation, the exact place we will not |
| **Jamy-L/Handheld-Multi-Frame-Super-Resolution** | https://github.com/Jamy-L/Handheld-Multi-Frame-Super-Resolution | 208 stars / 33 forks, MIT, last push 2025-11-15 | Reference implementation of Wronski et al. SIGGRAPH 2019. **Requires CUDA + Numba + raw `.dng`/`.ARW`/`.CR2`, benchmarked on an RTX 3090** (12 MP 20-image burst in under 4 s). Unrunnable for us. Cite as lineage in the KAMPAN cut note |
| **kunzmi/ImageStackAlignator** | https://github.com/kunzmi/ImageStackAlignator | 435 stars / 71 forks, **GPL-3.0**, C#, dead since 2020-05-02 | The other serious implementation. Evidence the algorithm is genuinely hard — two independent implementations, 643 combined stars |
| **DiffTSR / MARCONet / TextZoom** | https://github.com/YuzheZhang-1999/DiffTSR · https://github.com/csxmli2016/MARCONet · https://github.com/WenjiaWang0312/TextZoom | 200 / 268 / 493 stars (DiffTSR Apache-2.0, MARCONet NOASSERTION, TextZoom no licence) | **Use as the foil, not the tool.** One faded receipt through DiffTSR produces a crisp confident **wrong** rupee digit. That single split-screen PNG is the cheapest criterion-3 artifact available — 30 minutes of work |
| **sargamgandotra/QRShield** | https://github.com/sargamgandotra/QRShield | 1 star, no licence, no code, created **and** last pushed 2026-08-22 | **Direct concept collision by name, zero by substance.** Someone had the PEEL idea six days before this research. Treat as a clock, not a threat |
| **ABUBAKARSIDDIQPOONA786/Al-Driven-detection-mechanism-for-UPI-fraud-and-QR-code-tampering** | (as named) | 4 stars, no licence, dormant since 2025-05-17 | Confirms the reflex solution in this space is "point an AI at it" |
| **oelna/signed-qr-codes** | https://github.com/oelna/signed-qr-codes | 33 stars, no licence, JS, 2021 | The perfect contrast: signing content requires controlling issuance of every QR in the country. Our sticker check controls nothing and works on stickers printed years ago |
| **J0j1n/fake_Upi** + 4 others | https://github.com/J0j1n/fake_Upi · Hariomlokhande-coder/Fake-Payment-Screenshot-Detector · Priteshkumar0804/UPI-TruthScan · anishwagh-sudo/upi-fraud-detector · AnushkaGunjal10/Secureupi | 0–1 stars each | The fake-screenshot genre. **Every one is an image-forensics classifier (ELA / CNN / LayoutLM) and every one is defeated by a screenshot of a real payment to a different merchant.** If PAKKA is ever mentioned, lead with "I do not classify the image, I ask the ledger" or it gets filed with these |
| **Aditya-2434/Handwritten-Bill-Digitization-Engine** | https://github.com/Aditya-2434/Handwritten-Bill-Digitization-Engine | 0 stars, no licence, Electron + Gemini vision, created 2026-08-24, pushed 2026-08-27 | **Live concept collision with KHATA LIFT, same country, same document, same week** — and it independently converged on confirm-before-send. It has no AR, no geometry, no payment rail and **no published accuracy number** |
| **Walleve/ChromaCode** | https://github.com/Walleve/ChromaCode | **4 stars, no code, no licence, dead since 2020-07-04** | The **entire** open-source universe for imperceptible screen-camera communication. This is the JUGNU cut, in one line |
| **vinissimus/opencv-js-webworker** | https://github.com/vinissimus/opencv-js-webworker | 193 stars / 30 forks, **no licence file**, last push 2023-01-06 | Copy the Worker/OffscreenCanvas **pattern**; take the actual build from `@techstark/opencv-js`. No licence file = all rights reserved |
| The digital-khata CRUD genre | MrAkshay143/Mandal-Khata · UM4IRR/DigiKhata · infoadcraftstudio-hub/munshi-app · 0xshubhs/khatabook · Thechiragji/medical-store-khata | 0–1 stars each | ~15 apps that all ask the shopkeeper to **re-type** the paper ledger. The negative space our product occupies |

### 13.5 Standards and protocol surfaces (P1)

| Thing | URL / identifier | What to copy |
|---|---|---|
| **ACP product feed** | https://developers.openai.com/commerce/specs/file-upload/products | **Exactly 12 required fields:** `item_id`, `title`, `description`, `url`, `brand`, `image_url`, `price`, `availability`, `seller_name`, `target_countries`, `is_eligible_search`, `is_eligible_checkout`. Optional `pickup_method`, `pickup_sla`, `store_country`, `geo_price`, `geo_availability`, `availability_date`, `expiration_date` |
| **ACP checkout** | https://developers.openai.com/commerce/specs/checkout | **5 merchant endpoints:** `POST /checkout_sessions`, `POST /checkout_sessions/{id}`, `POST /checkout_sessions/{id}/complete`, `POST /checkout_sessions/{id}/cancel`, `GET /checkout_sessions/{id}`. Headers: `Authorization`, `Idempotency-Key`, `Request-Id`, `Signature` (base64 of body), `Timestamp` (RFC 3339), `API-Version`. Status enum: `not_ready_for_payment` / `ready_for_payment` / `completed` / `canceled` |
| **ACP delegated payment** | same spec tree | `POST /agentic_commerce/delegate_payment`; HTTP 201 on success; vault token prefixed `vt_`; `allowance{reason, max_amount, currency, expires_at, merchant_id, checkout_session_id}`. **`payment_method.type` is `"card"` with fpan/network_token/exp_month/cvc/cryptogram — there is NO UPI path.** Direct integration is restricted to "PSPs or PCI DSS level 1 merchants using their own vaults" |
| **Google local product data** | https://support.google.com/merchants/answer/14779112 | Required: `id`, `title`, `description`, `image_link`; `gtin`/`brand`/`condition` conditional. `pickup_method` ∈ buy/reserve/ship_to_store/not_supported; `pickup_SLA` ∈ same_day/next_day/2-day/…/multi-week. **A kirana is `reserve` + `same_day`, single-store inventory — emit this shape, not a shipping catalog** |
| **UCP** | manifest at `/.well-known/ucp`; capabilities `dev.ucp.shopping.checkout`, `dev.ucp.shopping.discount`, `dev.ucp.shopping.fulfillment`; agents POST `/checkout-sessions`, PUT `/checkout-sessions/{ID}`. Announced at NRF 11 Jan 2026 with 20+ backers, Apache 2.0 | **UNVERIFIED:** `https://github.com/google-agentic-commerce/ucp` returned 404 on fetch. Verify the repo URL before citing it |
| **AP2** | https://github.com/google-agentic-commerce/AP2 | Announced 16 Sep 2025, 60+ partners, Apache 2.0. Mandates as W3C Verifiable Credentials. **Borrow the shape** (scope + constraints + timestamp + nonce + key reference + signature); do not implement it |
| **RFC 9421 HTTP Message Signatures** | — | Published Feb 2024, Standards Track; `Signature-Input` and `Signature` headers |
| **llms.txt** | https://llmstxt.org | Proposed 3 Sep 2024 by Jeremy Howard. **A convention, not a standard, with no known commerce-feed ingestion by any major AI shopping surface.** Razorpay's own docs publish one indexing 1,405 India pages (651 Payments, 140 Banking Plus, 359 API Reference), plus Singapore 522 and US 437. Twenty minutes of work, one slide line, zero substance — say so if you ship it |

**The honest boundary line for the README:** **ACP-SHAPED, RAZORPAY-SETTLED.** If the submission implies end-to-end ACP compliance, a judge who has read the spec kills it in one sentence and takes the honesty points with it.

### 13.6 Local checkouts already present in `/Users/agnik/Desktop/razor/reference/`

`ultralytics` (HEAD 2026-08-28) · `supervision` (HEAD 2026-08-25) · `PaddleOCR` (incl. `paddleocr-js/`) · `razorpay-mcp-server` · `zxing-wasm` · `barcode-detector` · `EdgeTAM` · `MobileSAM` · `surya` · `transformers.js` · `ggwave` · plus ~17 others including `TruFor`, `tau2-bench`, `toxiproxy`, `agentdojo`, `scikit-uplift`.

**Not yet present and needed on Day 0:** `@techstark/opencv-js@4.11.0-release.1` (npm), `Xenova/mobileclip_s0` ONNX weights, `hdbscan`/`scikit-learn`, `fastapi`+`uvicorn`, `cloudflared`.

---

## 14. OPEN QUESTIONS

Only the human can decide these. Each is a crisp choice with a recommendation and the consequence of each branch.

**Q1 — Eligibility and deadline. BLOCKING. Resolve tonight.**
The buildathon page as fetched states the tracks, the ₹75,000 stipend, the 6-or-12-month duration and in-person Bangalore from September, but **does not state a submission date anywhere** — 5 September 2026 is reported and **UNVERIFIED**. The page also reportedly says **"Students only"** (**UNVERIFIED**).
→ **Confirm both from the application form before writing code.** If the deadline is earlier, collapse Days 5–6 and ship after G5. If "students only" excludes you, that is worth knowing before eight days.

**Q2 — Product name.** GAWAAH / TAKHTI / HISAAB. **Recommendation: GAWAAH.** Consequence: the name appears in the repo URL, the video and every field; changing it on Day 6 costs a rename across the tree. Decide Day 0.

**Q3 — Do you have shop access, and whose hands are in the shot?**
Twenty seconds of a real shopkeeper's hands plus one sentence from him is the highest criterion-1 value per rupee in the plan. A wooden board on a desk is visible in one frame and turns problem taste into a literature review.
→ **Arrange it on Day 3 while buying SKUs.** If you cannot: shoot the 15-second social cut in a real shop and the full take at a domestic counter, and **state the compromise in the README.** Do not pretend.

**Q4 — Live-mode ₹1, or test mode?**
Test mode needs no KYC and its webhooks fire, but there is **no confirmed path to credit a test-mode UPI QR (UNVERIFIED)**. A live-mode ₹1 payment to yourself, filmed once with the amount visible, removes the one dependency you do not control — at the cost of real KYC and a real account.
→ **Recommendation: make live-mode ₹1 the plan of record for the filmed beat, and test mode the convenience path for batch runs.** Decide by Gate G1 (Sat 20:00). The last rung — a labelled webhook replay — is acceptable **only** if labelled on screen and in the README.

**Q5 — The agent coda: in the video, or repo-only?**
It introduces a chat window, which is a new subject, in a film whose whole discipline is one prop class. Mitigations: it lives in the evidence half after the register has changed; the chat is a small PiP, never full screen; and the payoff returns to the mat (blue slot, hand places the item, same webhook, same green).
→ **Recommendation: keep it, with those three mitigations.** If the edit makes it look bolted on, **cut it to a 10-second `curl` of the feed** and lose nothing structural.

**Q6 — Ship the embedder, or ship the histogram?**
This is decided by data on Day 5, not by taste. If MobileCLIP-S0 does not beat `compareHist` + metric long edge on 24 SKUs on a fixed rig, **ship the histogram and publish the comparison.** *"I deleted the embedder"* is a stronger criterion-3 artifact than either choice alone — and it removes 11.85 MB and one dependency.
→ Pre-commit now to following the number, so the decision is not made under Day-5 fatigue.

**Q7 — A3 or A4 mat, and does the shopkeeper accept "place it down"?**
A3 gives more metrology zone and a longer sell line; A4 fits a small counter and is cheaper to print and laminate. The behaviour change (~400 ms placement) is the load-bearing assumption of the whole geometry.
→ **Ask the shopkeeper on Day 3 and film his answer.** If he refuses to place items down, the footprint tiebreak demotes to "sometimes works", abstention carries the load, and the PRD language changes that day — that is Gate G2's fallback, and it is survivable.

**Q8 — Sticker registry in P1, or cut entirely?**
It fixes a real landmine (a rule of the form "valid UPI handle not in registry → RED" will accuse the shopkeeper's own legitimate PhonePe sticker on first run) and it replaces PEEL's payload generator with a photo diff. But it is a new capability with its own honesty burden.
→ **Recommendation: ship it as ~90 seconds of onboarding and one still in the evidence half.** If Gate G6 is tight, cut it and delete every sticker-verdict rule with it — do not ship the rule without the registry.

**Q9 — Price observations at all?**
MRP is printed on the packet and is a legal ceiling, so `price_evidence` proves a non-fact for packaged goods. Availability evidence is the real invention.
→ **Recommendation: ship `price_evidence` as a secondary field with the MRP ceiling stated in the same breath, and lead the feed on `last_seen_on_shelf` + TTL.** Alternative: cut price observations entirely and lose a nice-sounding line, gaining one less thing to defend.

**Q10 — How honest is the amber rate allowed to be on screen?**
The per-basket touch rate (~22–27% at a 10% per-item rate) is the number that costs the merchant time, and publishing it invites the obvious objection.
→ **Recommendation: publish it, first, with the pairing sentence:** *"it prices three of four baskets with no touch, it has never charged for an item it could not name, and when it does not know it says so out loud instead of guessing."* The alternative — reporting only the per-item rate — is the exact move a judge will catch, and it costs more than the number does.

**Q11 — Does B0 (the shopkeeper, timed) go in the video, or only the README?**
He will probably beat the system on speed. That is the most credible page in the submission and also the most uncomfortable ten seconds.
→ **Recommendation: README and evidence-half card, not the take.** It is a reading artifact, and the take has no room for it.

**Q12 — What is the single number you are willing to lose on?**
Pre-commit before Day 5 so Gate G5 is a decision and not a negotiation. Suggested: **overcharge ≤ ₹2 per ₹1,000 billed**, with coverage as the free variable, and the coverage cost published.

---

*End of PRD. Tomorrow morning: `git init`, file the `upi_qr` activation request, stand up the webhook receiver, confirm the deadline. Gate G0 is at 23:00 tonight.*
