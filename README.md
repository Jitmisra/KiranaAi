# Kirana Shop AI

### A kirana counter runs on somebody's word. This is the witness.

A shop counter that is **just a camera**. Put packets down; it reads their printed codes — or
recognises them by appearance when there is no code — prices them, and settles with a real Razorpay
payment link. No scanner gun, no shelf sensors, no weight plates, no model weights in the browser.

Built for the **Razorpay AI Buildathon 2026 · Open Track**.

> The Python package is `gawaah/` — गवाह, *witness*. That was the codename while it was being
> built and it is left alone in the tree, because a rename that touches 52 modules on the last
> day is a rename that breaks something quietly. The product is Kirana Shop AI.

| | |
|---|---|
| **Two services** | the till (`:8790`, no secrets) · the money (`:8788`, the only key holder) |
| **Code** | 140,791 lines of Python · 60,393 lines of TypeScript |
| **Tests** | **4,758** Python · **346** browser-logic · **47** end-to-end, real Chromium |
| **Screens** | 31 routes, 39 stylesheets, 496 strings × 3 languages |
| **Server capability** | 52 modules, 27 routers |
| **Entry bundle** | 338 kB (112 kB gzipped) against a 400 kB ceiling the build enforces |

---

## The problem, stated once

A kirana counter runs on somebody's word.

The customer's word about what is in their hand. The phone screen's word that money arrived — a
screenshot of a success page is not a payment, and every shopkeeper in India has been shown one.
The shopkeeper's own memory of what is on the shelf, what it cost, and who owes for last week.

Nobody is watching, because watching is not a job a person standing behind a counter can afford to
do. This is a counter that watches, and — this is the whole design — **says when it cannot see**.

---

## The screens

Thirty-one of them over twenty-seven server capabilities. The three that matter most:

| | what it does |
|---|---|
| **Till** | bill what is on the counter, then charge it |
| **Products** | teach it what things are, from a photograph |
| **Storefront** | what a customer sees when they scan the shutter QR |

And the rest of a shop, grouped as the sidebar groups them:

| | |
|---|---|
| **Counter** | Till · Products · Categories · Stock · Expiry · By weight · Shelf · Labels · Offers · Salaahkaar |
| **Shop** | Storefront · Orders · Customers · Loyalty · Khata · Shop profile |
| **Books** | Today · Insights · History · Expenses · Purchases · Parchi · Reorder · GST · Day close · Milan · Inventory · Settings |

Plus a **customer display** — a spare tab turned to face the customer, showing the bill as it is
built and the payment code when it is time to pay, with none of the shop's own chrome on it. It
carries no controls and computes no money: it re-adds the lines only to CHECK the till's total, and
when the two disagree it says so instead of picking one.

The chrome is in **English, Hindi and Bengali**: गल्ला · सामान · क़िस्में · कैमरा चालू करो,
ক্যাশ · জিনিসপত্র · ধরন · ক্যামেরা চালু করুন. English is the source of truth and the other two are
overlays on it, so an untranslated key renders the English sentence — never a blank button, never a
raw key. Hindi and Bengali are **fetched only when somebody reads one**, so a shopkeeper working in
English never downloads 120 kB of scripts they cannot read.

### Three ways to fill a bill

- **Hold a packet up.** Every printed code in view is read at once and priced.
- **Read the whole counter.** Lay the shopping out, press once. Several products in one frame — and
  it reports what it could *see* but not *name* rather than guessing.
- **Say it.** `"do Maggi aur ek Parle-G"` in Hinglish. Voice proposes lines; a person accepts them.
  **Voice never moves money on its own.**

---

## Run it

```sh
make ui           # build the front end — required, there is no second one
make serve        # the till,  :8790   (catalogue + camera. no secrets.)
make serve-money  # the money, :8788   (the ONLY process with gateway keys)
```

Then open <http://127.0.0.1:8790>.

```sh
make test        # 4,758 python + 346 typescript
make e2e         # real Chromium, real getUserMedia, real server
make lint        # no float may reach a price
make preflight   # is the money path open RIGHT NOW?
make verify-ledger  # re-walk the hash chain, fail on a broken link
```

### Seeding a shop to look at

```sh
.venv/bin/python tools/seed_shop.py
```

Builds a whole kirana: 36 real Indian products with real photographs, prices in paise, shelves,
category tags, stock counts, a month of history, and a shopkeeper account whose **password is
generated fresh and printed once** — there is no default password anywhere in this repository, on
purpose.

### For a real payment to turn green

The gateway's signed webhook has to reach `:8788` from the public internet. Expose it and point the
Razorpay dashboard at `<public-url>/webhook`:

```sh
cloudflared tunnel --protocol http2 --url http://localhost:8788
```

A quick tunnel gets a **new address every restart**, and it can be revoked while the process keeps
running and retrying. That has cost a real payment once and nearly cost a demo twice, so there is a
check for it:

```sh
make preflight
```

It does not ask whether a process is running — the revoked tunnel's process was alive and retrying
the whole time. It sends a callback down the public address and requires the money service to
answer. The probe is **deliberately unsigned**, so the only honest answer is `400 bad_signature`;
that refusal proves the path is open, and nothing turns green. Then it prints the exact URL to paste
into the Razorpay dashboard, which is the one thing it cannot check for you.

See *The webhook had nowhere to land* in [FAILURES.md](FAILURES.md).

---

## Architecture

```
                      the shop's wifi                     the public internet
                            │                                     │
  ┌─────────────────────────┴──────────────┐          ┌───────────┴──────────────┐
  │  THE TILL          tools/upload_app.py │          │  THE MONEY  live_app.py  │
  │  :8790                                 │          │  :8788                   │
  │                                        │          │                          │
  │  camera · catalogue · books · staff    │  scan id │  paisa   re-prices from  │
  │  27 routers, 52 modules                │  + the   │          its OWN tables  │
  │                                        │  amount  │  kernel  exactly-once    │
  │  HOLDS NO GATEWAY SECRET               ├─────────>│  rzp_*   mints the link  │
  │  CANNOT TURN A BILL GREEN              │          │                          │
  └────────────────────────────────────────┘          │  THE ONLY KEY HOLDER     │
                                                      └───────────┬──────────────┘
                                                                  │ signed webhook
                                                                  │ verified over
                                                                  │ RAW BYTES
                                                                  ▼
                                                            GREEN, once
```

The till sends a scan id and the amount it is showing. **paisa re-prices the witness itself** from
its own tables and refuses to mint if the two disagree. A till that has been tampered with cannot
talk the money service into a different number, because the number is not what it sends — it is
what it *claims*, and the claim is checked.

---

## The nine invariants

These are the load-bearing rules. Each one is enforced by code, not by discipline.

**1 · Integer paise, everywhere.** No float ever touches a price. `tools/lint_no_float.py` walks 6
strict modules and semantically scans 68 files for a float reaching money; the browser asserts it at
the boundary rather than rounding. `₹139.50` is `13950`, always.

**2 · Green only on a signature-verified webhook.** A session turns PAID when a webhook, verified
over the **raw request bytes** before parsing, matches it on four conditions and its envelope event
id has not been seen before. **The browser can *refuse* a payment; it can never *grant* one.**

**3 · No model weights in the browser.** The page ships no weights and calls no third-party
inference. `connect-src` is `'self'` alone; `script-src 'self'` permits no inline script anywhere.
The SERVER embeds through 4.96 MB of Apache-2.0 SqueezeNet weights (`gawaah/embedder2.py`) and
proposes regions with an optional 4 MB YOLO — both through `cv2.dnn`, both on this machine.

**4 · Only the counter area is uploaded.** In appearance mode everything outside the chosen
rectangle is discarded *in the browser* before the request. In code mode the whole frame goes up —
and the page says so, because a code can be anywhere on a packet.

**5 · One holder of secrets.** `gawaah/paisa.py` is the only process with gateway credentials.
`RazorpayLive` refuses any key id not starting `rzp_test_` unless `GAWAAH_ALLOW_LIVE_KEYS` is set to
the literal string `yes-i-mean-it`.

**6 · No forgery primitives.** A payment QR is a render of the opaque `short_url` the gateway
issued. **Nothing here constructs, parses-and-rebuilds, or regenerates a UPI payload or a payment
address.** The simulator mints on `pay.gawaah-sim.invalid`, a domain that cannot resolve, so a
simulated link can never be mistaken for a real one — it used to mint on the gateway's own domain
shape, and that was a forgery primitive sitting in a test double.

**7 · Abstain rather than guess.** An item that does not clear its bar is **amber** and is excluded
from the total. A short bill an operator can see beats a confident bill that is wrong.

**8 · The browser is never an author.** The counter writes its own witness server-side under an id.
The page is given no field in which to assert a payload, a SKU or a price.

**9 · Every published number comes from running code.** Where something failed, the failure is
recorded with it — that is what [FAILURES.md](FAILURES.md) is.

### Colour is reserved

| | |
|---|---|
| **green** | a signature-verified webhook settled it |
| **amber** | the counter abstained — it saw something it could not name |
| **red** | refused |
| **neutral** | on the khata. A debt is not a colour. |

---

## The money path, end to end

`gawaah/kernel.py` is the exactly-once core, and the rule it exists to hold is one sentence:

> **A debit for (session, cycle, amount) executes ONCE OR NEVER.**

Every state transition commits and closes its connection *before* the network call it authorises, so
a crash mid-flight is recoverable by reading the database rather than by guessing:

```
create_debit        → NEW committed, connection closed. Proves intent survived.
mark_calling        → CALLING committed, connection closed.
   ── the gateway is asked to mint, with NO database connection held ──
mark_minted / mark_indeterminate / mark_failed
record_settlement   → the signed webhook arrives. ONLY THIS turns a bill green.
                      The HTTP answer to the mint call never does, however confident.
```

Refunds (WAAPSI) got their **own tables and their own state machine** rather than being bolted onto
the settlement path, because a refund is not a negative debit and an exactly-once guarantee you
have overloaded is an exactly-once guarantee you have lost:

```
create_refund → mark_refund_calling → (requested | indeterminate | failed)
                                    → record_refund_event  ← signed refund.processed
```

`results/audit.jsonl` is hash-chained and has **exactly one writer**, `gawaah/kernel.py`.
`make verify-ledger` re-walks the chain and fails on a broken link.

---

## Four things a kirana actually runs on

The counter bills and settles. These four are the money flows that happen *around* the bill, which
is where a real shop lives.

### KHATA (खाता) — the udhaar book

"Sharma ji, 650, likh do." Credit written in a notebook is the money flow the till could not see. A
bill on the khata closes in **neutral ink** — not green, not amber, not red — and stays neutral
until the gateway says otherwise. Collection goes out as a real Razorpay payment link; the debt
**drops only on a signed webhook**, never on the shopkeeper tapping "paid".

### WAAPSI (वापसी) — a return, by camera

The customer brings a packet back. Show it to the same camera that billed it, the counter finds the
line on the settled bill, and the refund goes to Razorpay. It reads `REQUESTED` in neutral ink until
a signed `refund.processed` arrives — test-mode refunds take minutes, and a screen that says
REFUNDED before the money moved is the same lie as a screenshot of a success page. Loyalty points
are clawed back off what **stayed**, not what was billed.

### MILAN (मिलान) — matched against the bank

Every other Books screen answers from one source: the hash-chained audit log, folded. That is the
right shape for *"what did I bill"* and it cannot answer *"what reached the bank"*. MILAN puts
Razorpay's own settlement report — one row per payment, refund and adjustment, with the fee and the
tax it took — beside the chain, row by row, and **names every place they disagree** rather than
netting them out.

### PARCHI (पर्ची) — photograph the wholesaler's bill

Margin is the one number a kirana runs on, and until a cost price is on file the books say "no
product sold today has a recorded cost". The cost prices exist — on a printed distributor invoice —
and typing forty lines of it into a form is the step nobody takes.

**This is the one place a language model genuinely earns its seat.** A messy printed invoice, in
mixed Hindi and English, with the distributor's own abbreviations, becomes lines with costs. Every
line lands as a **proposal a person accepts or rejects**; nothing a model read moves money, and a
line nobody accepted has no effect on any book.

---

## How it sees

### Teaching a product — three ways, not equivalent

- **by code** — binds a SKU to a printed identifier. Nothing about appearance is stored, and nothing
  about appearance is needed. **This is the most reliable path.**
- **by photograph** — appearance only. No millimetres, no size check, and a stricter similarity bar
  to compensate.
- **on the printed mat** — the TAKHTI A3 sheet carries four ArUco markers of known spacing, so the
  product is stored with its **true footprint in millimetres** and a wrong-sized packet is thrown
  out by the tape measure before appearance is ever consulted.

Teaching **from the camera** takes an eight-frame burst through the quality gate in `gawaah/saaf.py`
and enrols the sharpest survivor. If nothing survives, nothing is taught — there is no override,
because a gate you can wave through is decoration.

### How by-look decides

Crops are embedded by `gawaah/embedder2.py` — SqueezeNet features, nuisance-whitened,
rotation-averaged — and matched against the shop's own taught views at a cosine gate of **0.55**
(**0.60** with no footprint to check).

The gates sit inside a **measured** gap: on committed fixtures the weakest same-product pair scores
**0.63** and the strongest different-product pair **0.44**, and `tests/test_embedder2_separation.py`
holds that frontier as executable numbers. The case that forced the change — the same jar taught in
daylight and shown in warm evening light — scored 0.74 against the old 0.92 gate and abstained; it
now scores 0.82 against 0.60 and is priced.

### Finding several products in one frame

Two questions, deliberately separated — one vision model asked to do both does the first adequately
and the second not at all:

- **where** — `gawaah/detector.py`, class-agnostic regions, no product knowledge
- **which** — the shop's own taught vectors, at the same cosine gate every other path uses

Measured on three products laid on a 1280×720 counter:

| proposer | found | IoU | time |
|---|---|---|---|
| classical contours | **3 / 3** | 0.90–0.93 | 79 ms |
| COCO YOLOv5n | 0 / 3 | — | 35 ms |

YOLO runs server-side through `cv2.dnn` (4 MB ONNX, no torch) and its **class head is never read** —
a bar of Lifebuoy is not one of the eighty things it knows, and its best guess for one is "person".
It stays wired because it adds recall on the COCO objects that do turn up at a counter — a bottle, a
cup, a phone — and it is **optional**: delete `models/` and everything still works.

### Measured

| | | |
|---|---|---|
| QR, held anywhere in view | **25 / 25** | a 5×5 grid across a 1280×720 view |
| QR, rolled | **8 / 8** | 0° through 180°, at a corner of the view |
| QR, near and far | **6 / 6** | 400 px down to 64 px, at the left edge |
| QR floor | **55 px** | 1.9 px per module — below it the information is not in the image |
| Barcode, any angle | **220 px** | 180 px square-on |
| A frame with no code | **104 ms** | against a 240 ms poll — the loop keeps up |
| Curved bottle, rolled | **~20°** | nothing past 30°; that is geometry, not tuning |

The position row is the one that matters. Before it was fixed the page cropped the camera to its
centre and scored **3 of 25** — the decoder was healthy the whole time and the barcode was being
discarded in the browser, before the request was ever sent.

### Stated limits

- Packets closer together than about a finger's width read as **one item**. Below ~20 px of mask
  separation the two genuinely fuse. A test asserts that failure so it cannot be discovered by a
  shopkeeper whose bill was short.
- A single uploaded *file* cannot be quality-gated the way a camera burst can: the measurements are
  comparative, and one still has nothing to be compared against.
- The customer display is a **same-browser** channel. A second window on the counter's own machine
  works; a separate phone does not. That limit is in the code's own docstring rather than discovered
  later.

---

## Salaahkaar — the assistant

One room, one door on every screen. She routes a question to the right capability, reads the answer
out of the shop's own tables, and **checks every figure before speaking it**.

- **Routing** — `gemini-3.1-flash-lite` on this build. The provider is whatever `GAWAAH_LLM_*`
  points at; nothing in the code is married to one vendor, and with no key set every
  assistant route refuses by name instead of failing.
- **Voice** — `gemini-2.5-flash-preview-tts`, a drawn presenter with a live vector mouth: 15 Oculus
  visemes driven from the audio's own level through a Web Audio `AnalyserNode`
- **Ears** — `gemini-2.5-flash`. The browser's `SpeechRecognition` is a cloud call to Google's
  speech service dressed as a browser feature; when the network refuses it, the microphone simply
  stops with `network` and the shopkeeper can do nothing. `gawaah/stt.py` is a second pair of ears
  on the counter's own key, and the browser falls back to it automatically.
- **Three languages**, in native script as well as Latin, through one shared vocabulary rather than
  three parsers — because a counter hears all three in one sentence.

**What she cannot do.** Move money. Every instruction lands as a proposal with an UNDO beside it,
and a bill she touched is a bill a person accepted. The audio cap is 15 seconds and 1.5 MB, because
a page that could post an hour of audio is a page that can empty an account.

---

## The customer's side

`GET /store/qr` prints a QR for the shutter. A customer scans it, gets **their own shell** — their
own sign-in, their own orders, none of the shopkeeper's sidebar — browses the catalogue this counter
has taught, and places an order.

The customer's phone **never sets a price**. It sends sku ids and quantities; the server prices the
basket from the shop's own catalogue, and paisa re-prices the whole thing from *its* tables before
it mints. The only payable string that ever reaches a phone is the opaque `short_url`.

Stock gates the storefront: a product below its floor shows **OUT OF STOCK** rather than taking an
order the shop cannot fill, and units already held for placed orders are reserved.

For a phone to reach it at all the till has to be publicly addressable — `/shop/link` reports
`reachable_from_a_phone: false` when it is not, rather than printing a QR that cannot work.

---

## Security

**The lock ships fitted and open.** `gawaah/auth.py` gives the counter accounts, sign-in and a
session; set `GAWAAH_REQUIRE_AUTH=1` to close it. The session is an **HttpOnly** cookie with a 12
hour life. The storefront and its receipts stay open, because a customer has no account.

`tests/test_the_lock.py` does not check a list of open paths — that is the wrong question, and
asking it that way once let `/store/link/for` mint a customer identity for anyone who asked. It
interrogates the **live route tree** for routes that carry no guard, so a router mounted without one
fails the suite rather than shipping.

**What leaves this machine.** Nothing about a shop, unless you turn on the assistant — then a
question and the figures needed to answer it go to the provider *you* configured, and a recording
goes when you press the microphone. The catalogue, the customers, the prices and the bill do not.
There is no telemetry, no analytics, and no third-party script: `script-src 'self'`.

**Secrets** live in `.env`, `chmod 600`, gitignored. `results/` — the shop's own data, including
password hashes, live sessions and customers' names and phone numbers — **is not in this
repository**; `tools/seed_shop.py` regenerates a shop from nothing.

---

## Verification

| | |
|---|---|
| `make test` | 4,758 Python (73 files) · 346 browser-logic (17 files) |
| `make e2e` | 47 end-to-end across 9 specs — real Chromium, real `getUserMedia`, real server |
| `make lint` | no float may reach a price |
| `make preflight` | is the money path open right now? |
| `make verify-ledger` | re-walk the hash chain, fail on a broken link |
| `npx tsc -b --noEmit` | clean |
| `vite build` | fails the build if the entry bundle passes 400 kB |

That last one is not decoration. The counter runs on a shop's phone or an old laptop, and the
bundle has crossed that ceiling twice — once at 505 kB, once back to 494 kB when Products crept
back into the entry chunk. It is **338 kB** now, with Products prefetched on the first idle frame
so the hot path stayed hot.

Mutation testing runs against the money modules: `tests/test_mutation.py`.

---

## Layout

```
gawaah/     52 modules, one file per capability.
            money      paisa · kernel · rzp_live · rzp_sim · webhook · live_app
                       khata · milan            (live_app is the ONLY secret holder)
            seeing     recogniser · embedder2 · detector · identity · saaf · takhti
            the shop   shop_store · storefront · shopface · shopadmin · offers
                       categories · stock · expiry · weighed · shelf · labels
                       loyalty · customers · receipts
            the books  daybook · expenses · purchases · po · gst · manage · insights
                       parchi
            talking    assistant · advisor · tts · stt · search · share
            the door   auth
models/     an OPTIONAL yolov5n.onnx. Absent is a supported state.
tools/      upload_app.py       the till server, 27 routers mounted
            preflight.py        is the money path open right now?
            lint_no_float.py    invariant 1, enforced
            seed_shop.py        a whole kirana, from nothing
            migrate_gallery.py  re-embeds every taught product when the metric changes
ui/         the React front end. 31 routes, everything but the Till lazy-loaded.
            src/lib/strings/{en,hi,bn}.ts   496 keys × 3 languages, fetched on demand
tests/      73 python files · ui/src/**/*.test.ts · ui/e2e/*.spec.ts
results/    NOT IN THIS REPOSITORY — the shop's own data. Regenerate with seed_shop.py.
```

---

## What is written down

[FAILURES.md](FAILURES.md) is the running record of every defect on this build, written as it
happened — **4,349 lines**. It includes the times the *test harness* was wrong and the product was
fine, because that turned out to be the most useful pattern in the file: a 28-screen sweep that
passed while photographing one screen three times; an audit that measured the previous route's DOM;
a mobile check that counted elements past the viewport without asking whether they were inside a
scroll container.

[SUBMISSION.md](SUBMISSION.md) is the Buildathon write-up.

---

## Licence and credit

SqueezeNet weights are Apache-2.0. YOLOv5n is optional and its class head is never read. Plus
Jakarta Sans is self-hosted under the OFL. Everything else here was written for this build.
