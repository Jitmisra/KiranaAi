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
