<div align="center">

# Kirana Shop AI

### A kirana counter runs on somebody's word. This is the witness.

**A camera-native point-of-sale for the Indian corner shop.** Put the packets down, press once.
It reads printed codes — or recognises products by appearance when there is no code — prices them
from the shop's own catalogue, and settles with a real Razorpay payment link.

No scanner gun. No shelf sensors. No weight plates. No model weights in the browser.

**Razorpay AI Buildathon 2026 · Open Track**

</div>

---

## For the reviewer, in thirty seconds

| Question | Answer |
|---|---|
| **Does it run?** | `make ui && make serve && make serve-money`, then <http://127.0.0.1:8790>. `make preflight` walks the whole money path and prints PASS/FAIL per hop. |
| **Is it real, or slides?** | 4,788 Python tests, 360 browser-logic tests, 47 end-to-end through real Chromium with a real camera and a real server. All green at the commit you are reading. |
| **How big?** | 141,609 lines of Python · 60,886 of TypeScript · 52 server modules · 31 screens · 73 commits |
| **Where is the AI?** | Three places, each named below with what it may and may not do. The load-bearing one is PARCHI — reading a wholesaler's printed invoice into cost lines. |
| **Does it touch real money?** | Yes — real Razorpay payment links, in test mode. A bill turns green **only** on a signature-verified webhook. |
| **What is the one idea?** | It says when it *cannot* see. An item that misses its bar goes amber and is excluded from the total; a short bill you can see beats a confident bill that is wrong. |
| **What is written down?** | [FAILURES.md](FAILURES.md) — 4,349 lines of every defect on this build, including the times the *test harness* was wrong and the product was fine. |

> The Python package is `gawaah/` — गवाह, *witness*. That was the codename during the build and it
> is left alone in the tree: a rename across 52 modules and a dozen environment variables on demo
> week is a rename that breaks something quietly. The product is **Kirana Shop AI**.

---

## Contents

[The problem](#the-problem-stated-once) ·
[Three ways to bill](#three-ways-to-fill-a-bill) ·
[Run it](#run-it) ·
[Architecture](#architecture) ·
[Where the AI is](#where-the-ai-is-and-what-it-may-not-do) ·
[The nine invariants](#the-nine-invariants) ·
[The money path](#the-money-path-end-to-end) ·
[KHATA · WAAPSI · MILAN · PARCHI](#four-things-a-real-kirana-runs-on) ·
[How it sees](#how-it-sees) ·
[Salaahkaar](#salaahkaar--the-assistant) ·
[The customer's side](#the-customers-side) ·
[Security](#security-and-privacy) ·
[Languages](#five-languages) ·
[Verification](#verification) ·
[Deployment](#deployment) ·
[Layout](#layout) ·
[Limits](#limits-stated-plainly)

---

## The problem, stated once

A kirana counter runs on somebody's word.

The customer's word about what is in their hand. The phone screen's word that money arrived — a
screenshot of a success page is not a payment, and every shopkeeper in India has been shown one.
The shopkeeper's own memory of what is on the shelf, what it cost, and who owes for last week.

Nobody is watching, because watching is not a job a person standing behind a counter can afford to
do. Existing billing software assumes a barcode on every packet and a person to type what is not.
An Indian kirana has neither: loose dal from a sack, a soap with the label facing away, forty lines
of a distributor's invoice nobody will ever key in.

This is a counter that watches — and, more importantly, **says when it cannot see.**

---

## Three ways to fill a bill

| | how | when it is used |
|---|---|---|
| **Hold a packet up** | every printed code in view is read at once and priced | the supermarket lane, without the scanner gun |
| **Read the whole counter** | lay the shopping out, press once — several products in one frame | a full basket at once |
| **Say it** | `"do Maggi aur ek Parle-G"` in Hinglish, Hindi, Bengali, Tamil or Telugu | hands full, or a product the lens cannot resolve |

Voice **proposes**; a person accepts. Nothing said or typed reaches the money service.

---

## Run it

```sh
make ui           # build the front end — required, there is no second one
make serve        # the till,  :8790   (catalogue + camera. no gateway secrets.)
make serve-money  # the money, :8788   (the ONLY process with gateway credentials)
```

Then open <http://127.0.0.1:8790>.

### Seed a whole shop to look at

```sh
.venv/bin/python tools/seed_shop.py
```

36 real Indian products with real photographs, prices in integer paise, shelves, category tags,
stock counts, a month of history, and a shopkeeper account whose **password is generated fresh and
printed once** — there is no default password anywhere in this repository, deliberately.

### Prove it is ready, rather than hoping

```sh
make preflight
```

It does not ask whether a process is running. It sends a callback down the public address and
requires the money service to answer. The probe is **deliberately unsigned**, so the only honest
answer is `400 bad_signature` — that refusal proves the path is open, and nothing turns green. Then
it prints the exact URL to paste into the Razorpay dashboard, which is the one thing it cannot
check for you.

```
The path a callback travels
  PASS  the address resolves to this counter
  PASS  a callback reaches the money service
        the probe was refused for a bad signature, which is the correct answer
  PASS  the counter stamped the callback as liveness
  READY -- every path a rupee travels was exercised, not assumed.
```

### For a real payment to turn a bill green

```sh
cloudflared tunnel --protocol http2 --url http://localhost:8788
# then point the Razorpay dashboard webhook at <public-url>/webhook
```

A quick tunnel gets a **new address every restart**, and it can be revoked while the process keeps
running and retrying. That has cost a real payment once and nearly cost a demo twice — which is
exactly why `make preflight` exists. See *The webhook had nowhere to land* in [FAILURES.md](FAILURES.md).

### Every command

| | |
|---|---|
| `make test` | 4,788 Python + 360 TypeScript |
| `make e2e` | 47 end-to-end, real Chromium, real `getUserMedia`, real server |
| `make lint` | no float may reach a price |
| `make preflight` | is the money path open right now? |
| `make verify-ledger` | re-walk the hash chain, fail on a broken link |

---

## Architecture

```
                     the shop's wifi                        the public internet
                           │                                        │
 ┌─────────────────────────┴───────────────┐          ┌─────────────┴──────────────┐
 │  THE TILL         tools/upload_app.py   │          │  THE MONEY   live_app.py   │
 │  :8790                                  │          │  :8788                     │
 │                                         │  scan id │                            │
 │  camera · catalogue · books · staff     │  + the   │  paisa    re-prices from   │
 │  27 routers · 52 modules · 31 screens   │  amount  │           its OWN tables   │
 │                                         ├─────────>│  kernel   exactly-once     │
 │  HOLDS NO GATEWAY SECRET                │          │  rzp_*    mints the link   │
 │  CANNOT TURN A BILL GREEN               │          │                            │
 └─────────────────────────────────────────┘          │  THE ONLY KEY HOLDER       │
                                                      └─────────────┬──────────────┘
                                                                    │ signed webhook,
                                                                    │ verified over
                                                                    │ RAW BYTES
                                                                    ▼
                                                              GREEN, once
```

The till sends a scan id and the amount it is showing. **paisa re-prices the witness itself** from
its own tables and refuses to mint if the two disagree by one paisa. A till that has been tampered
with cannot talk the money service into a different number, because the number is not what it
sends — it is what it *claims*, and the claim is checked against evidence the till wrote
server-side.

---

## Where the AI is, and what it may not do

Three models, each doing one job. Every one of them **proposes**; a person accepts, and only a
signed webhook moves money.

| | model | what it does | what it may never do |
|---|---|---|---|
| **Routing** | `gemini-3.1-flash-lite` | picks which capability answers a question | invent a figure — every number is read from the shop's files and checked before it is spoken |
| **Voice** | `gemini-3.1-flash-tts-preview` | speaks the answer, voice *Kore* | say a figure the sentence did not carry |
| **Ears** | `gemini-3.6-flash` | transcribes an order when Chrome's own recogniser cannot be reached | act on what it heard — it produces a proposal with an UNDO beside it |

### Recognition is not a language model

Products are recognised by **SqueezeNet embeddings** (4.96 MB, Apache-2.0, `cv2.dnn`, on this
machine) matched against the shop's own taught views at a measured cosine gate. Region proposal is
classical contours plus an **optional** 4 MB YOLOv5n whose class head is never read. No product
recognition depends on a hosted model, and nothing about a shop leaves the machine for it.

### PARCHI is where a language model genuinely earns its seat

Margin is the one number a kirana runs on, and until a cost price is on file the books say *"no
product sold today has a recorded cost"*. The cost prices exist — on a printed distributor invoice
in the shopkeeper's hand — and typing forty lines of it into a form is the step nobody takes. A
messy printed invoice, in mixed Hindi and English, with the distributor's own abbreviations,
becomes lines with costs. **Every line lands as a proposal a person accepts or rejects**, and a line
nobody accepted has no effect on any book.

---

## The nine invariants

Each one is enforced by code, not by discipline.

**1 · Integer paise, everywhere.** No float ever touches a price. `tools/lint_no_float.py` walks 6
strict modules and semantically scans 68 files for a float reaching money; the browser asserts it at
the boundary rather than rounding. `₹139.50` is `13950`, always.

**2 · Green only on a signature-verified webhook.** A session turns PAID when a webhook — verified
over the **raw request bytes** before parsing — matches it on four conditions and its envelope event
id has not been seen before. **The browser can *refuse* a payment; it can never *grant* one.**

**3 · No model weights in the browser.** The page ships no weights and calls no third-party
inference. `connect-src` is `'self'` alone; `script-src 'self'` permits no inline script anywhere.

**4 · Only the counter area is uploaded.** In appearance mode everything outside the chosen
rectangle is discarded *in the browser* before the request. In code mode the whole frame goes up —
and the page says so, because a code can be anywhere on a packet.

**5 · One holder of secrets.** `gawaah/paisa.py` is the only process with gateway credentials.
`RazorpayLive` refuses any key id not starting `rzp_test_` unless `GAWAAH_ALLOW_LIVE_KEYS` is set to
the literal string `yes-i-mean-it`.

**6 · No forgery primitives.** A payment QR is a render of the opaque `short_url` the gateway
issued. **Nothing constructs, parses-and-rebuilds, or regenerates a UPI payload or a payment
address.** The simulator mints on `pay.gawaah-sim.invalid` — RFC 2606 reserved, unresolvable — so a
simulated link can never be mistaken for a real one. It used to mint on the gateway's own domain
shape, and that was a forgery primitive sitting in a test double.

**7 · Abstain rather than guess.** An item that does not clear its bar is **amber** and is excluded
from the total.

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

Refunds got their **own tables and their own state machine** rather than being bolted onto the
settlement path, because a refund is not a negative debit and an exactly-once guarantee you have
overloaded is an exactly-once guarantee you have lost:

```
create_refund → mark_refund_calling → (requested | indeterminate | failed)
                                    → record_refund_event  ← signed refund.processed
```

`results/audit.jsonl` is hash-chained and has **exactly one writer**, `gawaah/kernel.py`.
`make verify-ledger` re-walks the chain and fails on a broken link.

### Three kinds of evidence, one mint path

A bill is minted against a record the server wrote. There are three kinds, and each says in its own
fields what it is — `paisa.rerun_scan` re-prices all three identically, so there is never a second
place for money to behave differently:

| `kind` | written by | what it claims |
|---|---|---|
| `scan` | the camera | these codes and these products were decoded out of these pixels at this moment |
| `order` | the storefront | these lines came from an order this server priced; no camera was involved |
| `counter_entered` | the shopkeeper | this bill was entered at the counter; no camera was involved |

Every guard stands on all three: each line re-resolved through paisa's own binding table, re-priced
from paisa's **own** book, a line it cannot price **blocks** the mint as `amber_in_basket`, and one
paisa of disagreement refuses.

---

## Four things a real kirana runs on

The counter bills and settles. These four are the money flows that happen *around* the bill.

### खाता · KHATA — the udhaar book

*"Sharma ji, 650, likh do."* Credit written in a notebook is the money flow the till could not see.
A bill on the khata closes in **neutral ink** — not green, not amber, not red — and stays neutral
until the gateway says otherwise. Collection goes out as a real Razorpay payment link; the debt
**drops only on a signed webhook**, never on the shopkeeper tapping "paid".

### वापसी · WAAPSI — a return, by camera

The customer brings a packet back. Show it to the same camera that billed it with the receipt QR in
the same frame; the counter finds the line on the settled bill and refunds it through Razorpay. It
reads `REQUESTED` in neutral ink until a signed `refund.processed` arrives — test-mode refunds take
minutes, and a screen that says REFUNDED before the money moved is the same lie as a screenshot of
a success page. Loyalty points are clawed back off what **stayed**, not what was billed.

### मिलान · MILAN — matched against the bank

Every other Books screen answers from one source: the hash-chained audit log, folded. That is the
right shape for *"what did I bill"* and it cannot answer *"what reached the bank"*. MILAN puts
Razorpay's own settlement report — one row per payment, refund and adjustment, with the fee and the
tax it took — beside the chain, row by row, and **names every place they disagree** rather than
netting them out.

### पर्ची · PARCHI — photograph the wholesaler's bill

See [Where the AI is](#where-the-ai-is-and-what-it-may-not-do). A printed distributor invoice
becomes cost lines, each one a proposal a person accepts. Margin stops being a number nobody typed.

---

## How it sees

### Teaching a product — three ways, not equivalent

| | what is stored | the bar it must clear |
|---|---|---|
| **by code** | a SKU bound to a printed identifier. Nothing about appearance. | none — a code is a measurement, not an opinion. **The most reliable path.** |
| **by photograph** | appearance only | a stricter similarity gate, because there are no millimetres to check |
| **on the printed mat** | appearance **plus the true footprint in millimetres** | a wrong-sized packet is thrown out by the tape measure before appearance is consulted |

Teaching **from the camera** takes an eight-frame burst through the quality gate in
`gawaah/saaf.py` and enrols the sharpest survivor. If nothing survives, nothing is taught — there is
no override, because a gate you can wave through is decoration. (`GAWAAH_MAX_BLUR_SCORE` retunes the
resolution ceiling for one machine's camera; the shipped default is calibrated and does not move.)

### The gates, and the measured gap they sit in

| gate | value | what it means |
|---|---|---|
| `DEFAULT_PHI` | **0.55** | required cosine for a match with a footprint to check |
| `PHI_APPEARANCE_ONLY` | **0.60** | required cosine when there are no millimetres |
| `DEFAULT_THETA` | **0.10** | required gap between the best and second-best match |

These are **measured**, not chosen: on committed fixtures the weakest same-product pair scores
**0.63** and the strongest different-product pair **0.44**, and
`tests/test_embedder2_separation.py` holds that frontier as executable numbers.

`THETA` is why the counter abstains when two taught products look identical — it can see the jar
perfectly and still refuse to guess *which* jar, because naming one would be a coin flip.

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

YOLO's **class head is never read** — a bar of Lifebuoy is not one of the eighty things it knows,
and its best guess for one is "person". It stays wired because it adds recall on the COCO objects
that do turn up at a counter, and it is **optional**: delete `models/` and everything still works.

### Measured — codes

| | | |
|---|---|---|
| QR, held anywhere in view | **25 / 25** | a 5×5 grid across a 1280×720 view |
| QR, rolled | **8 / 8** | 0° through 180°, at a corner of the view |
| QR, near and far | **6 / 6** | 400 px down to 64 px, at the left edge |
| QR floor | **55 px** | 1.9 px per module — below it the information is not in the image |
| Barcode, any angle | **220 px** | 180 px square-on; nothing below that decodes |
| A frame with no code | **104 ms** | against a 240 ms poll — the loop keeps up |
| Curved bottle, rolled | **~20°** | nothing past 30°; that is geometry, not tuning |

The position row is the one that matters. Before it was fixed the page cropped the camera to its
centre and scored **3 of 25** — the decoder was healthy the whole time and the barcode was being
discarded in the browser, before the request was ever sent.

---

## Salaahkaar — the assistant

सलाहकार. One room, one door on every screen. She routes a question to the right capability, reads
the answer out of the shop's own tables, and **checks every figure before speaking it**.

- **A drawn presenter** with a live vector mouth: 15 Oculus visemes driven from the audio's own
  level through a Web Audio `AnalyserNode`. Labelled `SYNTHETIC PRESENTER · NOT A PERSON` on screen,
  always.
- **Ears that do not depend on Chrome.** `SpeechRecognition` is a cloud call to Google's speech
  service wearing a browser API's clothes; when the network refuses it, the microphone simply stops.
  The counter falls back to its own key automatically and the page says which ears are listening.
- **Money is read as money.** `Rs 3173.00` is how a page *writes* an amount; a voice is given
  `3173 रुपये` — digits plus the word for rupees in the asker's language, paise only when they are
  not zero.

**What she cannot do.** Move money. Every instruction lands as a proposal with an UNDO beside it.
The audio cap is 15 seconds and 1.5 MB, because a page that could post an hour of audio is a page
that can empty an account.

---

## The customer's side

`GET /store/qr` prints a QR for the shutter. A customer scans it and gets **their own shell** —
their own sign-in, their own orders, none of the shopkeeper's sidebar — browses the catalogue this
counter has taught, and places an order.

The customer's phone **never sets a price**. It sends SKU ids and quantities; the server prices the
basket from the shop's own catalogue, and paisa re-prices the whole thing from *its* tables before
it mints. The only payable string that ever reaches a phone is the opaque `short_url`.

Stock gates the storefront: a product below its floor shows **OUT OF STOCK** rather than taking an
order the shop cannot fill, and units already held for placed orders are reserved.

---

## Security and privacy

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
No telemetry, no analytics, no third-party script: `script-src 'self'`.

**Secrets** live in `.env`, `chmod 600`, gitignored. `results/` — the shop's own data, including
password hashes, live sessions and customers' names and phone numbers — **is not in this
repository**; `tools/seed_shop.py` regenerates a shop from nothing.

---

## Five languages

English, **हिन्दी**, **বাংলা**, **தமிழ்**, **తెలుగు**.

English is the source of truth and the others are overlays on it, so an untranslated key renders the
English sentence — never a blank button, never a raw key. **496 string keys**; Hindi and Bengali are
complete and **fetched only when somebody reads one**, so a shopkeeper working in English never
downloads scripts they cannot read. Tamil and Telugu reach the *assistant* — she hears and answers
in them — and the button's own tooltip says the chrome around her stays English, because a control
that implied otherwise would be the one kind of lie this counter is built not to tell.

The counter hears all five in one sentence: `"do Maggi aur ek Parle-G"`, `"दो मैगी एक पार्ले जी"`,
`"2 soap and 1 Maggi"` all reach the same parser.

---

## Verification

| | |
|---|---|
| `make test` | **4,788** Python (73 files, 2 skipped) · **360** browser-logic (17 files) |
| `make e2e` | **47** end-to-end across 9 specs — real Chromium, real `getUserMedia`, real server |
| `make lint` | PASS — 6 strict modules, 68 files semantically scanned for floats reaching money |
| `make preflight` | every hop of the money path, exercised not assumed |
| `make verify-ledger` | the hash chain, re-walked |
| `npx tsc -b --noEmit` | clean |
| `vite build` | **fails the build** if the entry bundle passes 400 kB |

That last one is not decoration. The counter runs on a shop's phone or an old laptop, and the bundle
has crossed that ceiling twice — once at 505 kB, once back to 494 kB when Products crept into the
entry chunk. It is **340 kB** (112 kB gzipped) now, with Products prefetched on the first idle frame
so the hot path stayed hot.

**Mutation testing** runs against the money modules: `tests/test_mutation.py`.

---

## Deployment

`Dockerfile`, `.dockerignore`, `render.yaml` and `requirements.txt` are in the tree, and
[DEPLOY.md](DEPLOY.md) is the runbook. The image builds in ~56 s to **160 MB compressed**; the till
serves `/health` and the built front end, and the money service comes up refusing live keys.

**Two limits stated as limits, not footnotes.** On a free tier the two services do not share a
filesystem and there is no persistent disk, so a deployed demo needs the single-container fallback
DEPLOY.md documents. And free web services spin down when idle — a cold start can outrun Razorpay's
webhook timeout, which is the exact failure `make preflight` exists to catch.

---

## Layout

```
gawaah/     52 modules, one file per capability.
            money      paisa · kernel · rzp_live · rzp_sim · webhook · live_app
                       khata · milan          (live_app is the ONLY secret holder)
            seeing     recogniser · embedder2 · detector · identity · saaf · takhti
            the shop   shop_store · storefront · shopface · shopadmin · offers
                       categories · stock · expiry · weighed · shelf · labels
                       loyalty · customers · receipts
            the books  daybook · expenses · purchases · po · gst · manage · insights
                       parchi
            talking    assistant · advisor · tts · stt · search · share
            the door   auth
models/     5 files, 10.5 MB. yolov5n.onnx is OPTIONAL; absent is a supported state.
tools/      upload_app.py       the till server, 27 routers mounted
            preflight.py        is the money path open right now?
            lint_no_float.py    invariant 1, enforced
            seed_shop.py        a whole kirana, from nothing
            migrate_gallery.py  re-embeds every taught product when the metric changes
ui/         React 18 + TypeScript + Vite. 31 routes, 39 stylesheets, everything but
            the Till lazy-loaded. src/lib/strings/  496 keys, fetched on demand
tests/      73 Python files · ui/src/**/*.test.ts · ui/e2e/*.spec.ts
results/    NOT IN THIS REPOSITORY — the shop's own data. Regenerate with seed_shop.py.
```

---

## Limits, stated plainly

Every one of these is asserted by a test, so it cannot be discovered by a shopkeeper whose bill was
short:

- **Packets closer together than about a finger's width read as one item.** Below ~20 px of mask
  separation the two genuinely fuse.
- **A barcode needs ~220 px of frame width**, and nothing under 180 px decodes at all. That is
  geometry.
- **A curved label past ~30° of roll does not decode.** Also geometry.
- **A single uploaded file cannot be quality-gated** the way a camera burst can: the measurements
  are comparative, and one still has nothing to be compared against.
- **The customer display is a same-browser channel.** A second window on the counter's own machine
  works; a separate phone does not.
- **Two taught products that look identical make the counter abstain**, by design — see `THETA`.

---

## What is written down

[FAILURES.md](FAILURES.md) — **4,349 lines** of every defect on this build, written as it happened.
It includes the times the *test harness* was wrong and the product was fine, because that turned out
to be the most useful pattern in the file: a 28-screen sweep that passed while photographing one
screen three times; an audit that measured the previous route's DOM; a mobile check that counted
elements past the viewport without asking whether they were inside a scroll container.

[SUBMISSION.md](SUBMISSION.md) — the Buildathon write-up.
[DEPLOY.md](DEPLOY.md) — the deployment runbook.

---

## Licence and credit

SqueezeNet weights are Apache-2.0. YOLOv5n is optional and its class head is never read. Plus
Jakarta Sans is self-hosted under the SIL Open Font License. Everything else here was written for
this build.

<div align="center">

**A kirana counter runs on somebody's word. This is the witness.**

</div>
