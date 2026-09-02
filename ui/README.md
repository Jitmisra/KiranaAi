# GAWAAH — the front end

React + TypeScript + Vite. Served by FastAPI at `/` once built; the server falls
back to the old inline page when `dist/` is absent, so a fresh checkout still has
a working till.

```bash
make ui        # install + build  -> ui/dist, served at /
make ui-dev    # vite dev server on :5173, API proxied to :8790
make ui-test   # unit tests (vitest)
make e2e       # real Chromium, fake camera, against a running till
```

## Why it is laid out this way

The old page was 74 000 characters of JavaScript inside a Python string. Every
rule the till had learned lived in a closure that could only be tested by
scraping the source out of `upload_app.py` and running it against a stub DOM.

So the split here is not by screen, it is **by what can be tested without a
browser**:

```
src/lib/       pure decisions — no DOM, no fetch, no React
  money.ts       integer paise; formatting that cannot round
  roi.ts         which pixels leave the browser
  counter.ts     when a packet becomes a line on the bill
  audio.ts       the three voices, and the loudness contract
  overlay.ts     boxes and the 620 ms snap
  api.ts         every request, typed against what the server really returns

src/hooks/     the parts that need a browser
  useCamera.ts   getUserMedia, and the ONE place a frame becomes bytes
  useScanLoop.ts grab -> upload -> decide -> show -> sound, single-flight

src/routes/    the four screens: Till, Products, Setup, Proof
```

`lib/` holds the rules that were expensive to learn. They are unit-tested at
34 assertions and none of those tests needs a DOM.

## The three test layers, and what each one is for

| layer | command | catches |
|---|---|---|
| unit (vitest) | `make ui-test` | the commit rules, the money arithmetic, the counter area |
| contract (playwright) | `make e2e` | the server returning a different shape than `api.ts` claims |
| end to end (playwright) | `make e2e` | the whole loop in real Chromium with a fake camera |

The **contract** layer exists because of a real defect. `api.ts` once declared
`health.catalog_size` and `moneyHealth.reachable`; neither field exists. In
TypeScript both were simply `undefined` — nothing threw, no test failed, and the
status chips reported *"0 taught"* over a shop of seven products and *"gateway
down"* over a working gateway. A type is a claim about someone else's data, and
only a running server can check it.

The same class of bug produced `NaN×NaN MM` on every measured product, because
`footprint_mm` is a single float and the UI indexed it as a pair. The contract
test now asserts **shapes**, not just presence.

## The fake camera

`make e2e` generates a Y4M feed with `tools/make_fake_cam.py`: a QR code held in
the **top-left corner** of the frame, rolled 37 degrees, wobbling slightly. That
is deliberate. The till used to crop the camera to its centre 56% × 66% and would
read nothing at all from that position — measured **3 of 25** held positions
against **25 of 25** for the whole frame. If the bill fills in this test, the
counter really is looking at the whole frame.

## Things worth knowing before changing something

- **`defaultRoi` differs by mode on purpose.** Reading codes uploads the whole
  frame; measuring appearance uploads a bounded rectangle. Merging them back
  into one default is the bug that made the till look broken for days.
- **A code commits on its first clean read; a look waits three frames.** A code
  is an identifier that was *read*. A look is a guess.
- **Packets are keyed by position, never by payload.** Two identical packets
  share a barcode.
- **The browser never authors money.** `scan()` sends pixels and gets an id;
  `mint()` sends that id. The money service re-derives every rupee itself.
- **The overlay canvas must keep `pointer-events: none`.** It once covered
  START CAMERA completely, and `element.click()` in tests skips hit-testing, so
  the button passed every test and was dead in every real hand.
