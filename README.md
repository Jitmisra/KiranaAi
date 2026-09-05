<div align="center">

# Kirana Shop AI

### किराना शॉप — a shop counter that is just a camera

Put the packets down. Press once. It reads what it can read, recognises what it cannot,
prices it from the shop's own list, and takes payment with a real Razorpay link.

The same catalogue becomes the shop's storefront, so the kirana two lanes away
is finally on a customer's phone.

<br>

[**Open the counter →**](https://gawaah-till.onrender.com) &nbsp;·&nbsp;
[Money service](https://gawaah-money.onrender.com/health) &nbsp;·&nbsp;
[FAILURES.md](FAILURES.md) &nbsp;·&nbsp;
[DEPLOY.md](DEPLOY.md)

*Razorpay AI Buildathon 2026 · Open Track*

</div>

---

<div align="center">

Built by **Agnik Misra** — [linkedin.com/in/agnikmisra](https://www.linkedin.com/in/agnikmisra/)

<sub>2× GSoC '25 & '26 · LFX '25 · NST-28 · Co-founder, In.culcate · Apache Committer · 5× Hackathon Winner · Amazon ML Summer School 2026</sub>

</div>

---

## Contents

[The problem](#the-problem) · [What we built](#what-we-built) · [In thirty seconds](#for-a-reviewer-in-thirty-seconds) ·
[Run it](#run-it) · [Architecture](#architecture) · [Where the AI is](#where-the-ai-is) ·
[The nine invariants](#the-nine-invariants) · [The money path](#the-money-path) ·
[Khata · Waapsi · Milan · Parchi](#four-things-a-real-shop-runs-on) · [How it sees](#how-it-sees) ·
[Salaahkaar](#salaahkaar) · [The storefront](#the-storefront) · [Security](#security-and-privacy) ·
[Languages](#five-languages) · [Verification](#verification) · [Deployment](#deployment) ·
[Layout](#layout) · [Limits](#limits-stated-plainly)

---

## The problem

There are roughly thirteen million kirana shops in India. Two things are quietly
killing them, and they are the same thing wearing different clothes.

### One — the counter runs on somebody's word

The customer's word about what is in their hand. The phone screen's word that money
came, and every shopkeeper in the country has been shown a screenshot of a success
page that was never a payment. The shopkeeper's own memory of what is on the shelf,
what it cost last month, and who owes for last week.

Nobody is watching, because watching is not a job a person standing behind a counter
can afford to do. There is one pair of hands, and they are busy.

Billing software does exist. It assumes a barcode on every packet and a person free
to type in whatever has none. A kirana has neither. It has loose dal weighed out of a
sack, a soap turned label-inward on the shelf, a jar whose sticker peeled off in
March, and a wholesaler's invoice of forty handwritten-looking lines that nobody is
ever going to key into a form. So the software gets bought, used for a fortnight, and
abandoned — and the shop goes back to a notebook.

### Two — the shop cannot be found

Ten-minute delivery did not win on price. It won on being *reachable*. A customer with
a phone can see what is in stock, tap twice, and have it at the door. The kirana two
lanes away has the same rice at the same price and no way to be seen at all.

Its owner is not going to build an app. Listing on a platform means handing over
20–30% of a margin that was already thin, plus the customer relationship — which is
not a business model, it is a slower way to close. So the shop stays invisible, and
the trade walks past the shutter to a phone.

Both problems have the same root: **the shop's own inventory has never been written
down in a form a computer can read.** No catalogue, no billing that scales past a
notebook, and nothing to put online even if somebody wanted to.

---

## What we built

One counter that solves both, because both need the same missing thing.

### Inside the shop, it is just a camera

No scanner gun. No shelf sensors. No weight plates. No model weights in the browser.

Lay the shopping on the counter and press once. Every printed code in view is decoded
at once. Anything with no code is recognised by how it looks, matched against views
the shopkeeper taught it. Prices come from the shop's own catalogue, never from the
page. Payment is a real Razorpay link.

The important part is not that it sees. It is that it *says when it cannot*. An item
that fails to clear its similarity bar goes amber and stays out of the total, with its
crop shown beside the bill so a person can decide. A bill that is short in a way you
can see beats a confident bill that is wrong, every time — because the short one gets
corrected at the counter and the confident one gets discovered at closing.

### Outside the shop, that same catalogue is already a storefront

One QR sticker on the shutter. A customer scans it and gets the shop's own page — its
name, its photograph, its address, its own link — showing exactly what the counter has
been taught, at the price the counter charges. They fill a basket, give an address,
and pay. No app to install. No platform, no listing fee, no commission, no
intermediary owning the customer.

Stock gates it honestly: anything at or below its floor reads OUT OF STOCK rather than
accepting an order the shop cannot fill, and units already committed to placed orders
are held back. The storefront and the till read the same numbers, so they cannot drift.

And the shopkeeper does nothing extra to be online. Teaching a product so the camera
can bill it *is* the act that puts it on a customer's phone. The work they already do
builds the catalogue; the catalogue is the storefront.

> The Python package is `gawaah/` — गवाह, *witness*. That was the codename during the
> build, left alone because a rename across 52 modules on demo week is a rename that
> breaks something quietly. The product is Kirana Shop AI.

---

## For a reviewer, in thirty seconds

| | |
|---|---|
| Live | [gawaah-till.onrender.com](https://gawaah-till.onrender.com) — deployed from this repo's `render.yaml` |
| Does it run locally? | `make ui && make serve && make serve-money`, then `127.0.0.1:8790`. `make preflight` walks the money path and prints PASS/FAIL per hop |
| Is it real, or slides? | 4,788 Python tests · 360 browser-logic · 47 end-to-end through real Chromium with real `getUserMedia` |
| How big? | 141,609 lines of Python · 60,886 of TypeScript · 52 server modules · 31 screens |
| Where is the AI? | Three models, each named below with what it may and may not do. The load-bearing one reads a wholesaler's printed invoice into cost lines |
| Real money? | Real Razorpay payment links in test mode. A bill turns green only on a signature-verified webhook |
| The one idea | It says when it cannot see, and it never lets the page decide that money moved |
| Written down | [FAILURES.md](FAILURES.md) — 4,349 lines of every defect on this build, including the times the test harness was wrong and the product was fine |

---

## Try the live counter

The lock is on, so the demo needs a sign-in — a counter judges cannot get into is a
counter they cannot look at.

| | |
|---|---|
| Counter | <https://gawaah-till.onrender.com> |
| Phone | `9820114477` |
| Password | `kirana-demo-2026` |
| Storefront (no sign-in) | append `/#/shop` — the customer's side is open by design |

Two honest notes. This password is published on purpose and anyone can therefore edit
the demo shop; the data is seeded fixtures, and on a free tier with no persistent disk
a restart wipes and re-seeds it, so anything broken heals itself. And the first request
after an idle spell takes ~50 seconds — Render's free instances spin down, which is the
same cold start that can outrun a webhook.

### The data you are looking at

Everything in the live shop is generated by `tools/seed_shop.py`, not hand-written into
the repo: 36 real Indian products with photographs, prices in integer paise, shelves,
category tags, opening stock, a month of history, and one shopkeeper account. `results/`
is gitignored — the shop's own data, including password hashes and customers' phone
numbers, is not in this repository and never has been.

### Moving your own shop from local to production

There is no import step, because there is no export step. The catalogue lives in
`results/`, which is a directory of JSON and a SQLite file, so a real shop moving to a
server copies it:

```sh
# on the laptop the shop was taught on
tar czf shop.tgz results/

# on the server, beside the running counter
tar xzf shop.tgz && make serve
```

`GAWAAH_SHOP_DIR` and `GAWAAH_DATA_DIR` point both services at wherever that directory
lands, which is how one image serves a laptop and a container without a code change.
On Render's free tier there is no persistent disk, so this is the one thing the free
plan cannot keep — [DEPLOY.md](DEPLOY.md) says so plainly rather than hiding it.

---

## Run it

```sh
make ui           # build the front end — required, there is no second one
make serve        # the till,  :8790   (catalogue, camera, books. no gateway secrets.)
make serve-money  # the money, :8788   (the only process with gateway credentials)
```

Open <http://127.0.0.1:8790>.

### Seed a whole shop to look at

```sh
.venv/bin/python tools/seed_shop.py
```

36 real Indian products with real photographs, prices in integer paise, shelves,
category tags, stock counts, a month of history, and a shopkeeper account whose
password is generated fresh and printed once. There is no default password anywhere
in this repository, deliberately.

### Prove it is ready, rather than hoping

```sh
make preflight
```

It does not ask whether a process is running — a revoked tunnel's process stays alive
and retrying for hours. It sends a callback down the public address and requires the
money service to answer. The probe is deliberately unsigned, so the only honest answer
is `400 bad_signature`; that refusal proves the path is open and nothing turns green.

```
The path a callback travels
  PASS  the address resolves to this counter
  PASS  a callback reaches the money service
        the probe was refused for a bad signature, which is the correct answer
  PASS  the counter stamped the callback as liveness
  READY -- every path a rupee travels was exercised, not assumed.
```

### Every command

| | |
|---|---|
| `make test` | 4,788 Python + 360 TypeScript |
| `make e2e` | 47 end-to-end, real Chromium, real camera, real server |
| `make lint` | no float may reach a price |
| `make preflight` | is the money path open right now? |
| `make verify-ledger` | re-walk the hash chain, fail on a broken link |

---

## Architecture

```
                    the shop's wifi                        the public internet
                          │                                        │
 ┌────────────────────────┴────────────────┐          ┌────────────┴───────────────┐
 │  THE TILL        tools/upload_app.py    │          │  THE MONEY   live_app.py   │
 │  :8790                                  │          │  :8788                     │
 │                                         │  scan id │                            │
 │  camera · catalogue · books · staff     │  + the   │  paisa    re-prices from   │
 │  27 routers · 52 modules · 31 screens   │  amount  │           its OWN tables   │
 │                                         ├─────────>│  kernel   exactly-once     │
 │  holds no gateway secret                │          │  rzp_*    mints the link   │
 │  cannot turn a bill green               │          │                            │
 └─────────────────────────────────────────┘          │  the only key holder       │
                                                      └────────────┬───────────────┘
                                                                   │ signed webhook,
                                                                   │ verified over
                                                                   │ raw bytes
                                                                   ▼
                                                             GREEN, once
```

The till sends a scan id and the amount it is showing. paisa re-prices the witness
itself from its own tables and refuses to mint if the two disagree by a single paisa.
A till that has been tampered with cannot talk the money service into a different
number, because the number is not what it sends — it is what it *claims*, and the
claim is checked against evidence the till wrote server-side under an id.

---

## Where the AI is

Three models, each doing one job. Every one of them proposes; a person accepts, and
only a signed webhook moves money.

| | model | what it does | what it may never do |
|---|---|---|---|
| Routing | `gemini-3.1-flash-lite` | picks which capability answers a question | invent a figure — every number is read from the shop's files and checked before it is spoken |
| Voice | `gemini-3.1-flash-tts-preview` | speaks the answer, voice *Kore* | say a figure the sentence did not carry |
| Ears | `gemini-3.6-flash` | transcribes an order when Chrome's own recogniser cannot be reached | act on what it heard — it produces a proposal with an UNDO beside it |

### Recognition is not a language model

Products are recognised by SqueezeNet embeddings — 4.96 MB, Apache-2.0, through
`cv2.dnn`, on this machine — matched against the shop's own taught views at a measured
cosine gate. Region proposal is classical contours plus an optional 4 MB YOLOv5n whose
class head is never read. No product recognition depends on a hosted model, and
nothing about a shop leaves the machine for it.

### Parchi is where a language model genuinely earns its seat

Margin is the one number a kirana runs on, and until a cost price is on file the books
say *"no product sold today has a recorded cost"*. The cost prices exist — on a printed
distributor invoice in the shopkeeper's hand — and typing forty lines of it into a form
is the step nobody takes, ever.

So photograph it instead. A messy printed invoice, in mixed Hindi and English, with the
distributor's own abbreviations and no two columns aligned, becomes lines with costs.
Every line lands as a proposal a person accepts or rejects, and a line nobody accepted
has no effect on any book. That is a task with no deterministic solution, done by the
one tool that can do it, with a human holding the pen.

---

## The nine invariants

Each is enforced by code, not by discipline.

**1 · Integer paise, everywhere.** No float ever touches a price. `tools/lint_no_float.py`
walks 6 strict modules and semantically scans 68 files for a float reaching money; the
browser asserts it at the boundary rather than rounding. `₹139.50` is `13950`, always.

**2 · Green only on a signature-verified webhook.** A session turns PAID when a webhook,
verified over the raw request bytes before parsing, matches it on four conditions and
its envelope event id has not been seen before. The browser can refuse a payment; it
can never grant one.

**3 · No model weights in the browser.** The page ships no weights and calls no
third-party inference. `connect-src` is `'self'` alone; `script-src 'self'` permits no
inline script anywhere.

**4 · Only the counter area is uploaded.** In appearance mode everything outside the
chosen rectangle is discarded in the browser before the request. In code mode the whole
frame goes up — and the page says so, because a code can be anywhere on a packet.

**5 · One holder of secrets.** `gawaah/paisa.py` is the only process with gateway
credentials. `RazorpayLive` refuses any key id not starting `rzp_test_` unless
`GAWAAH_ALLOW_LIVE_KEYS` is set to the literal string `yes-i-mean-it`.

**6 · No forgery primitives.** A payment QR is a render of the opaque `short_url` the
gateway issued. Nothing constructs, parses-and-rebuilds, or regenerates a UPI payload
or a payment address. The simulator mints on `pay.gawaah-sim.invalid` — RFC 2606
reserved, unresolvable — so a simulated link can never be mistaken for a real one. It
used to mint on the gateway's own domain shape, and that was a forgery primitive
sitting in a test double.

**7 · Abstain rather than guess.** An item that does not clear its bar is amber and is
excluded from the total.

**8 · The browser is never an author.** The counter writes its own witness server-side
under an id. The page is given no field in which to assert a payload, a SKU or a price.

**9 · Every published number comes from running code.** Where something failed, the
failure is recorded with it — that is what [FAILURES.md](FAILURES.md) is.

### Colour is reserved

| | |
|---|---|
| green | a signature-verified webhook settled it |
| amber | the counter abstained — it saw something it could not name |
| red | refused |
| neutral | on the khata. A debt is not a colour. |

---

## The money path

`gawaah/kernel.py` is the exactly-once core, and the rule it exists to hold is one
sentence:

> A debit for (session, cycle, amount) executes once or never.

Every state transition commits and closes its connection *before* the network call it
authorises, so a crash mid-flight is recoverable by reading the database rather than by
guessing:

```
create_debit        → NEW committed, connection closed. Proves intent survived.
mark_calling        → CALLING committed, connection closed.
   ── the gateway is asked to mint, with no database connection held ──
mark_minted / mark_indeterminate / mark_failed
record_settlement   → the signed webhook arrives. Only this turns a bill green.
                      The HTTP answer to the mint call never does, however confident.
```

Refunds got their own tables and their own state machine rather than being bolted onto
the settlement path, because a refund is not a negative debit and an exactly-once
guarantee you have overloaded is an exactly-once guarantee you have lost.

`results/audit.jsonl` is hash-chained and has exactly one writer, `gawaah/kernel.py`.
`make verify-ledger` re-walks the chain and fails on a broken link.

### Three kinds of evidence, one mint path

A bill is minted against a record the server wrote. There are three kinds, each saying
in its own fields what it is — and `paisa.rerun_scan` re-prices all three identically,
so there is never a second place for money to behave differently:

| `kind` | written by | what it claims |
|---|---|---|
| `scan` | the camera | these codes and products were decoded out of these pixels at this moment |
| `order` | the storefront | these lines came from an order this server priced; no camera was involved |
| `counter_entered` | the shopkeeper | this bill was entered at the counter; no camera was involved |

Every guard stands on all three: each line re-resolved through paisa's own binding
table, re-priced from paisa's own book, a line it cannot price blocks the mint as
`amber_in_basket`, and one paisa of disagreement refuses.

---

## Four things a real shop runs on

The counter bills and settles. These four are the money that moves *around* the bill,
and they are where a kirana actually lives or dies.

### खाता · Khata — the udhaar book

*"Sharma ji, 650, likh do."* Credit written in a notebook is the money flow the till
could not see, and for most shops it is a third of the trade. A bill on the khata
closes in neutral ink — not green, not amber, not red — and stays neutral until the
gateway says otherwise. Collection goes out as a real Razorpay payment link, and the
debt drops only on a signed webhook, never on the shopkeeper tapping *paid*.

### वापसी · Waapsi — a return, by camera

The customer brings a packet back. Show it to the same camera that billed it, with the
receipt QR in the same frame; the counter finds the line on the settled bill and
refunds it through Razorpay. It reads REQUESTED in neutral ink until a signed
`refund.processed` arrives — test-mode refunds take minutes, and a screen that says
REFUNDED before the money moved is the same lie as a screenshot of a success page.
Loyalty points are clawed back off what stayed, not what was billed.

### मिलान · Milan — matched against the bank

Every other Books screen answers from one source: the hash-chained audit log, folded.
That is the right shape for *what did I bill* and it cannot answer *what reached the
bank*. Milan puts Razorpay's own settlement report — one row per payment, refund and
adjustment, with the fee and the tax it took — beside the chain, row by row, and names
every place they disagree rather than netting them out.

### पर्ची · Parchi — photograph the wholesaler's bill

See [Where the AI is](#where-the-ai-is). A printed distributor invoice becomes cost
lines, each one a proposal a person accepts. Margin stops being a number nobody types.

---

## How it sees

### Teaching a product — three ways, not equivalent

| | what is stored | the bar it must clear |
|---|---|---|
| by code | a SKU bound to a printed identifier; nothing about appearance | none — a code is a measurement, not an opinion. The most reliable path. |
| by photograph | appearance only | a stricter similarity gate, because there are no millimetres to check |
| on the printed mat | appearance plus the true footprint in millimetres | a wrong-sized packet is thrown out by the tape measure before appearance is consulted |

Teaching from the camera takes an eight-frame burst through the quality gate in
`gawaah/saaf.py` and enrols the sharpest survivor. If nothing survives, nothing is
taught — there is no override, because a gate you can wave through is decoration.

### The gates, and the measured gap they sit in

| gate | value | meaning |
|---|---|---|
| `DEFAULT_PHI` | 0.55 | required cosine for a match with a footprint to check |
| `PHI_APPEARANCE_ONLY` | 0.60 | required cosine when there are no millimetres |
| `DEFAULT_THETA` | 0.10 | required gap between the best and second-best match |

These are measured, not chosen. On committed fixtures the weakest same-product pair
scores 0.63 and the strongest different-product pair 0.44, and
`tests/test_embedder2_separation.py` holds that frontier as executable numbers.

`THETA` is why the counter abstains when two taught products look identical — it can
see the jar perfectly and still refuse to guess *which* jar, because naming one would
be a coin flip.

### Finding several products in one frame

Two questions, deliberately separated. One vision model asked to do both does the first
adequately and the second not at all.

- *where* — `gawaah/detector.py`, class-agnostic regions, no product knowledge
- *which* — the shop's own taught vectors, at the same cosine gate every path uses

Measured on three products laid on a 1280×720 counter:

| proposer | found | IoU | time |
|---|---|---|---|
| classical contours | 3 / 3 | 0.90–0.93 | 79 ms |
| COCO YOLOv5n | 0 / 3 | — | 35 ms |

YOLO's class head is never read — a bar of Lifebuoy is not one of the eighty things it
knows, and its best guess for one is "person". It stays wired because it adds recall on
the COCO objects that do turn up at a counter, and it is optional: delete `models/` and
everything still works.

### Measured — codes

| | | |
|---|---|---|
| QR, held anywhere in view | 25 / 25 | a 5×5 grid across a 1280×720 view |
| QR, rolled | 8 / 8 | 0° through 180°, at a corner of the view |
| QR, near and far | 6 / 6 | 400 px down to 64 px, at the left edge |
| QR floor | 55 px | 1.9 px per module — below it the information is not in the image |
| Barcode, any angle | 220 px | 180 px square-on; nothing below that decodes |
| A frame with no code | 104 ms | against a 240 ms poll — the loop keeps up |
| Curved bottle, rolled | ~20° | nothing past 30°; that is geometry, not tuning |

The position row is the one that matters. Before it was fixed the page cropped the
camera to its centre and scored 3 of 25 — the decoder was healthy the whole time and
the barcode was being discarded in the browser, before the request was ever sent.

---

## Salaahkaar

सलाहकार. One room, one door on every screen. She routes a question to the right
capability, reads the answer out of the shop's own tables, and checks every figure
before speaking it.

- A drawn presenter with a live vector mouth: 15 Oculus visemes driven from the audio's
  own level through a Web Audio `AnalyserNode`. Labelled *synthetic presenter · not a
  person* on screen, always.
- Ears that do not depend on Chrome. `SpeechRecognition` is a cloud call to Google's
  speech service wearing a browser API's clothes; when the network refuses it, the
  microphone simply stops. The counter falls back to its own key automatically and the
  page says which ears are listening.
- Money is read as money. `Rs 3173.00` is how a page writes an amount; a voice is given
  `3173 रुपये` — digits plus the word for rupees in the asker's language, paise only
  when they are not zero.

What she cannot do: move money. Every instruction lands as a proposal with an UNDO
beside it. The audio cap is 15 seconds and 1.5 MB, because a page that could post an
hour of audio is a page that can empty an account.

---

## The storefront

`GET /store/qr` prints a QR for the shutter, and `/shop/link` gives the shop its own
unique address — one link that opens this shop and no other. A customer scans it and
gets their own shell: their own sign-in, their own orders, none of the shopkeeper's
sidebar.

The customer's phone never sets a price. It sends SKU ids and quantities; the server
prices the basket from the shop's own catalogue, and paisa re-prices the whole thing
from *its* tables before it mints. The only payable string that ever reaches a phone is
the opaque `short_url` the gateway issued.

---

## Security and privacy

The lock ships fitted and open. `gawaah/auth.py` gives the counter accounts, sign-in and
a session; `GAWAAH_REQUIRE_AUTH=1` closes it. The session is an HttpOnly cookie with a
12-hour life. The first account on an empty counter opens without an invite — somebody
has to be first — and every account after that needs a code from someone already signed
in. The storefront and its receipts stay open, because a customer has no account.

`tests/test_the_lock.py` does not check a list of open paths. That is the wrong
question, and asking it that way once let `/store/link/for` mint a customer identity for
anyone who asked. It interrogates the live route tree for routes that carry no guard, so
a router mounted without one fails the suite rather than shipping.

What leaves this machine: nothing about a shop, unless you turn on the assistant — then
a question and the figures needed to answer it go to the provider you configured, and a
recording goes when you press the microphone. The catalogue, the customers, the prices
and the bill do not. No telemetry, no analytics, no third-party script.

Secrets live in `.env`, `chmod 600`, gitignored. `results/` — the shop's own data,
including password hashes, live sessions and customers' names and phone numbers — is not
in this repository; `tools/seed_shop.py` regenerates a shop from nothing.

---

## Five languages

English, हिन्दी, বাংলা, தமிழ், తెలుగు.

English is the source of truth and the others are overlays on it, so an untranslated key
renders the English sentence — never a blank button, never a raw key. 496 string keys;
Hindi and Bengali are complete and fetched only when somebody reads one, so a shopkeeper
working in English never downloads scripts they cannot read. Tamil and Telugu reach the
assistant — she hears and answers in them — and the button's own tooltip says the chrome
around her stays English, because a control that implied otherwise would be the one kind
of lie this counter is built not to tell.

The counter hears all of them in one sentence: `"do Maggi aur ek Parle-G"`,
`"दो मैगी एक पार्ले जी"`, `"2 soap and 1 Maggi"` all reach the same parser.

---

## Verification

| | |
|---|---|
| `make test` | 4,788 Python (73 files, 2 skipped) · 360 browser-logic (17 files) |
| `make e2e` | 47 end-to-end across 9 specs — real Chromium, real `getUserMedia`, real server |
| `make lint` | PASS — 6 strict modules, 68 files semantically scanned |
| `make preflight` | every hop of the money path, exercised not assumed |
| `make verify-ledger` | the hash chain, re-walked |
| `npx tsc -b --noEmit` | clean |
| `vite build` | fails the build if the entry bundle passes 400 kB |

That last one is not decoration. The counter runs on a shop's phone or an old laptop,
and the bundle has crossed that ceiling twice — once at 505 kB, once back to 494 kB when
Products crept into the entry chunk. It is 340 kB (112 kB gzipped) now, with Products
prefetched on the first idle frame so the hot path stayed hot.

Mutation testing runs against the money modules: `tests/test_mutation.py`.

---

## Deployment

Live on Render from this repo's `render.yaml` — two services, one image, distinguished
only by which start script they run:

- [gawaah-till.onrender.com](https://gawaah-till.onrender.com) — the counter
- [gawaah-money.onrender.com/health](https://gawaah-money.onrender.com/health) — the money service

`Dockerfile`, `.dockerignore`, `render.yaml`, `requirements.txt` and the two start
scripts in `docker/` are all in the tree; [DEPLOY.md](DEPLOY.md) is the runbook. The
image builds in ~56 s to 160 MB compressed.

Two limits stated as limits, not footnotes. On a free tier the two services do not share
a filesystem and there is no persistent disk, so a deployed demo needs the
single-container fallback DEPLOY.md documents. And free web services spin down when
idle — a cold start can outrun Razorpay's webhook timeout, which is the exact failure
`make preflight` exists to catch.

---

## Layout

```
gawaah/     52 modules, one file per capability.
            money      paisa · kernel · rzp_live · rzp_sim · webhook · live_app
                       khata · milan          (live_app is the only secret holder)
            seeing     recogniser · embedder2 · detector · identity · saaf · takhti
            the shop   shop_store · storefront · shopface · shopadmin · offers
                       categories · stock · expiry · weighed · shelf · labels
                       loyalty · customers · receipts
            the books  daybook · expenses · purchases · po · gst · manage · insights
                       parchi
            talking    assistant · advisor · tts · stt · search · share
            the door   auth
models/     5 files, 10.5 MB. yolov5n.onnx is optional; absent is a supported state.
tools/      upload_app.py       the till server, 27 routers mounted
            preflight.py        is the money path open right now?
            lint_no_float.py    invariant 1, enforced
            seed_shop.py        a whole kirana, from nothing
docker/     till.sh · money.sh  the two start commands, as files
ui/         React 18 + TypeScript + Vite. 31 routes, 39 stylesheets, everything but
            the Till lazy-loaded. src/lib/strings/  496 keys, fetched on demand
tests/      73 Python files · ui/src/**/*.test.ts · ui/e2e/*.spec.ts
results/    not in this repository — the shop's own data. Regenerate with seed_shop.py.
```

---

## Limits, stated plainly

Every one of these is asserted by a test, so it cannot be discovered by a shopkeeper
whose bill was short.

- Packets closer together than about a finger's width read as one item. Below ~20 px of
  mask separation the two genuinely fuse.
- A barcode needs ~220 px of frame width, and nothing under 180 px decodes at all.
- A curved label past ~30° of roll does not decode. Geometry, not tuning.
- A single uploaded file cannot be quality-gated the way a camera burst can: the
  measurements are comparative, and one still has nothing to be compared against.
- The customer display is a same-browser channel. A second window on the counter's own
  machine works; a separate phone does not.
- Two taught products that look identical make the counter abstain, by design.

---

## What is written down

[FAILURES.md](FAILURES.md) — 4,349 lines of every defect on this build, written as it
happened. It includes the times the test harness was wrong and the product was fine,
because that turned out to be the most useful pattern in the file: a 28-screen sweep
that passed while photographing one screen three times; an audit that measured the
previous route's DOM; a security test that asked *is this path on the open list?*
instead of *is this path actually guarded?*

[SUBMISSION.md](SUBMISSION.md) — the Buildathon write-up.
[DEPLOY.md](DEPLOY.md) — the deployment runbook.

---

## Licence and credit

SqueezeNet weights are Apache-2.0. YOLOv5n is optional and its class head is never read.
Plus Jakarta Sans is self-hosted under the SIL Open Font License. Everything else here
was written for this build.

<div align="center">

<br>

**A kirana counter runs on somebody's word. This is the witness.**

[Open the counter →](https://gawaah-till.onrender.com) &nbsp;·&nbsp; [Agnik Misra](https://www.linkedin.com/in/agnikmisra/)

</div>
