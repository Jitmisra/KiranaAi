# FAILURES.md

Dated log of what broke, the diagnosis, the fix, and what changed in the design because of it.
Started day 0. Every Phase-0 gate is recorded here, pass or fail.

---

## 2026-08-29 — G4 PASS · opencv.js symbol availability

**Gate:** does the shipped `@techstark/opencv-js` WASM actually export the primitives the whole architecture assumes — `convexHull` (MUDRA's model-free gesture), `findTransformECC` (load-bearing for IDENT/PEEL), the ArUco detector, and the placement pipeline?

**Result: PASS. All 21 required symbols present in the pinned 4.11.0 build.**

| Symbol | 4.11.0 | Needed by |
|---|---|---|
| `convexHull`, `convexityDefects`, `contourArea`, `arcLength` | ✅ | MUDRA gesture (occluder solidity) |
| `findTransformECC` | ✅ (2) | IDENT — without it a 1px registration error puts a genuine sticker at 16.1% ignited |
| `getPerspectiveTransform`, `findHomography`, `warpPerspective`, `solvePnP` | ✅ | TAKHTI plane |
| `ArucoDetector`, `getPredefinedDictionary`, `detectMarkers` | ✅ (14/1/5) | mat lock |
| `absdiff`, `morphologyEx`, `findContours`, `minAreaRect`, `boxPoints` | ✅ | placement detector |
| `calcHist`, `compareHist` | ✅ | identity prefilter |
| `ORB`, `AKAZE` | ✅ (13/16) | KAMPAN registration |
| `SIFT`, `createBackgroundSubtractorMOG2` | ❌ absent | documented as absent; neither is used |

**Two things broke while running this gate, both worth recording.**

**1. `grep` silently returned zero matches for every symbol, including ones that must exist.**
Diagnosis: `dist/opencv.js` contains embedded binary, so GNU grep switched to binary mode and suppressed match output. `wc -l` said 29 lines while the file is 13.3 MB — that inconsistency was the tell. Fix: `grep -a`. **Lesson recorded because it would have silently produced a false "library is broken" conclusion.**

**2. I briefly concluded AKAZE was missing. That was wrong, and the method was wrong.**
The first scan ran against **5.0.0**, whose plain-text strings expose only a *subset* of the symbol table. Reading absence from a plain-text scan of a partially-binary bundle is not evidence of absence.
Correct method, now the standard for this repo: extract the base64 blob from the bundle, decode it to a real `.wasm`, verify the `\0asm` magic, and search the decoded binary.
Corroboration that the method is right: the decoded wasm is **8,432,127 bytes**, matching the independently-researched figure of "8.43 MB decoded from the 11.39 MB single-file JS" to the byte.

**What changed in the design:** pin **`@techstark/opencv-js@4.11.0-release.1`**, not `latest`. `npm install` today resolves to 5.0.0-release.1, whose bundle is 13,298,869 bytes versus 11,386,540 for 4.11.0 — **1.91 MB heavier on a 4.8 MB cold-load budget, for zero benefit.** A `latest` install would have silently eaten 40% of the budget.

---

## 2026-08-29 — G0/G1/G2 BLOCKED · need Razorpay credentials

Not attempted — require a Razorpay test account, which needs a human signup. **G1 is the highest-risk unknown in the entire project** (see BUILD_PROMPT §2): research conflicts on whether UPI Payment Links function in test mode, and the money path assumes they do.

---

## 2026-08-29 — G3 HARNESS BUILT · needs a phone

`tools/device-passport.html` written. Reports `ImageCapture.getPhotoCapabilities().imageWidth.max` and renders the kill verdict directly. Also captures the `exposureMode` / `torch` / `focusMode` support that killed JUGNU, plus WebGPU and device memory.

Requires a **secure context** — `getUserMedia` is blocked over plain HTTP from a LAN IP, so it must be served over HTTPS via `cloudflared`. Same dependency as G2, so both are unblocked by the same step.

---

## 2026-08-29 — S2 PASS · three real bugs found by the plane tests

**Acceptance: reprojection RMSE < 1.0 px across tilts. Achieved 2.4e-05 px.** 48 tests green.

### Bug 1 — the synthetic harness projected the mat outside the frame
First run: every tilt test failed with `no markers detected`, while the raw mat detected fine. Diagnosis: the camera model put the mat quad at y=-320..1280 in a 960-tall frame, so the markers were off-sensor. **The test harness was broken, not the detector.** Fix: derive camera distance from a fit ratio so the mat always occupies ~82% of frame. Recorded because a harness bug that looks like a library bug is the most expensive kind of wrong turn.

### Bug 2 — `_scale_error` measured perspective foreshortening, not scale
It compared marker side lengths **in the raw frame**, so a legitimate 2° tilt produced a 2.657% "scale error" and refused to lock. Under perspective, sides *should* differ.

Fix: measure the marker sides **on the rectified plane** against the known 30 mm, by pushing corners through `H` (four `perspectiveTransform` calls, no full remap). Measured result: **0.4–0.9% across tilts from 0° to 20°** — genuinely tilt-invariant, which is what a scale check must be. Pinned by `test_scale_error_is_tilt_invariant`.

### Bug 3 — the tilt estimator is focal-length dependent, and the test hid it
Replaced a hand-rolled homography decomposition with a **dimensionless perspective index** from the last row of the buffer→frame homography (zero for an affine/nadir view). Calibrated against synthetic ground truth: `persp_index ≈ 0.286 × tan(tilt)`, recovering true tilt within 0.3°.

**But `PERSP_K` absorbs the camera's focal length.** My first calibration test asserted accuracy at a different `fit` ratio than it was fitted at, and failed at ≥5° — which is the estimator telling the truth about its own limits.

Design consequence, now enforced by three tests:
- the **gate thresholds the raw index**, which is monotonic in tilt for *any* lens
- `persp_to_deg` is tested only **at its calibration geometry**
- `test_persp_to_deg_does_not_transfer_across_focal_length` **asserts the limitation holds**, so nobody later reports it as a measured angle on real hardware

A number that is only valid on one lens must not be printed as degrees on a shop counter.

---

## 2026-08-29 — Build traps verified independently, and a NEW cross-platform hazard found

The rescue spec lists three build traps. I ran all three against the actual installed libraries rather than trusting the writeup.

| Trap | Claim | Verified result |
|---|---|---|
| `Mat.clone()` aliases the source | breaks absdiff → no crossing ever fires | **JS/wasm only.** Python `.copy()` is safe; `absdiff` returned 2500 nonzero as it should. Applies to `web/` only. |
| `cv.TERM_CRITERIA_COUNT` undefined | `findTransformECC` silently throws | **JS/wasm only.** Python cv2 5.0.0 exposes **both** spellings (`TERM_CRITERIA_COUNT`=1 *and* `TermCriteria_COUNT`=1). Applies to `web/` only. |
| `findTransformECC` throws instead of returning a low correlation | must wrap every call | **CONFIRMED IN PYTHON.** Two unrelated noise images raise `cv2.error` at `ecc.cpp:597`. It does **not** return a low `cc`. |

**Consequence:** every `findTransformECC` call in `ident_sticker` and `saaf` must be wrapped in `try/except cv2.error` with a throw treated as *frame rejected*, never as *no motion*. An unwrapped call turns a normal registration failure into a crash.

### NEW FINDING — the feature detectors are inverted between our two OpenCV builds

| | AKAZE | SIFT | ORB |
|---|---|---|---|
| **JS wasm** `@techstark/opencv-js@4.11.0` (decoded binary) | ✅ present (16 refs) | ❌ absent | ✅ |
| **Python** `opencv-contrib-python-headless 5.0.0` | ❌ **absent** | ✅ present | ✅ |

Exactly inverted. A module that registers frames with AKAZE works in the browser and **crashes on the brain**; one that uses SIFT does the reverse.

**Design consequence: ORB is the only descriptor present in both, so ORB is mandatory for any registration code that must run on both sides.** This also corrects the earlier record — I previously noted "AKAZE present, prior research was right", which was true *for the JS 4.11.0 wasm only*. It is false for the Python build we actually run the brain on. Both facts are needed; neither alone is safe to design from.

---

## 2026-08-29 — G0/G1/G2 PASS · the money path is real, not simulated

Run: `./.venv/bin/python tools/rzp_setup.py` against live Razorpay **test mode**.

| Gate | Result |
|---|---|
| G0 authenticate | HTTP 200 |
| **G1 mint a UPI Payment Link in test mode** | **HTTP 200**, `plink_TVSsfYMLbBsxi5`, `https://rzp.io/rzp/CxbtXQE` |
| G1 amount round-trip | **21437 paise asked, 21437 returned** |
| **G1 `notes.session_id` survives** | **YES** |
| G2 HMAC over raw bytes | round-trips; a single flipped byte changes the signature |

**Why G1 was the project's biggest risk.** One research pass asserted that Payment Links are capped at 30 per business in test mode and that UPI Payment Links are unsupported there — while the entire money path assumes they work. Rather than resolve a documentation conflict with more reading, this was settled against the live API. **Payment Links mint fine in test mode.** No fallback to `qr_codes`, no activation request.

**The finding that actually mattered: `notes.session_id` propagates.** Razorpay's docs never state that Payment Link `notes` reach the payment entity — they point at `reference_id` for reconciliation instead. Condition 3 of our four-part green predicate is *"`notes.session_id` names an OPEN intent"*. Had it not survived, the green rule was broken and we would have had to re-bind to `reference_id` **on day 0 rather than discovering it on day 6**. It survived, verified end to end.

**Odd paise round-trip exactly**, which validates the CHILLAR design: a per-session nonce in the last two paise makes the amount an exact primary key against the mirror, deleting the RRN/UTR join problem instead of solving it.

**`upi_link: False`** — expected. That flag is Live-Mode and Android only; nothing depends on it, and a standard link returns the same `short_url`.

**Two failures on the way in, both recorded because they cost real minutes.** The first secret paste never reached `.env` (file mtime unchanged — it went into the chat window instead). The second landed at **11 characters against an expected 24** — truncated by hand-selection or shell quoting. Both were caught by printing *character counts only*, never values. The setup tool masks key ids, reports secrets as `present, N chars`, and **refuses to run against any key id not starting `rzp_test_`**, so it structurally cannot touch a live key.
