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
