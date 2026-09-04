# GAWAAH · गवाह

### A kirana counter runs on somebody's word. This is the witness.

A shop counter that is **just a camera**. Put packets down; it reads their printed codes — or
recognises them by appearance when there is no code — prices them, and settles with a real Razorpay
payment link. No scanner gun, no shelf sensors, no weight plates, no model weights in the browser.

**Razorpay AI Buildathon 2026**

---

## The screens

Twenty-eight of them, over twenty-seven server capabilities. The three that matter most:

| | what it does |
|---|---|
| **Till** | bill what is on the counter, then charge it |
| **Products** | teach it what things are, from a photograph |
| **The storefront** | what a customer sees when they scan the shutter QR |

And the rest of a shop, grouped as the sidebar groups them:

| | |
|---|---|
| **Counter** | Till · Products · Categories · Stock · Expiry · By weight · Shelf · Labels · Offers · Ask · Advisor (a drawn presenter with a natural voice, answering in Hindi, Bengali or English) |
| **Shop** | Storefront · Orders · Customers · Loyalty · Shop profile |
| **Books** | Today · Insights · History · Expenses · Purchases · Reorder · GST · Day close · Inventory · Settings |

Plus a **customer display** — a spare tab or window turned to face the customer, showing the bill
as it is built and the payment code when it is time to pay, with none of the shop's own chrome on
it. It carries no controls and computes no money: it re-adds the lines only to CHECK the till's
total, and when the two disagree it says so instead of picking one. The wire is a same-browser
channel, so a second window on the counter's own machine works and a separate phone does not — that
limit is in the code's own docstring rather than discovered later.

There were nine screens once, and seven of them were pages *about* the counter rather than the
counter. Those were deleted before any of this was built. What is here is the thing a shopkeeper
stands in front of, the thing a customer holds, and the books behind both.

The chrome is in **English, Hindi and Bengali**, and so is the till: गल्ला · सामान · क़िस्में ·
कैमरा चालू करो, ক্যাশ · জিনিসপত্র · ধরন · ক্যামেরা চালু করুন. The assistant reads all three in
**native script as well as Latin** — the browser's speech recogniser is set to hi-IN and returns
Devanagari — through one shared vocabulary rather than three parsers, because a counter hears all
three in one sentence.

### Three ways to fill a bill

- **Hold a packet up.** Every printed code in view is read at once and priced.
- **Read the whole counter.** Lay the shopping out, press once. Several products in one frame — and
  it reports what it could *see* but not *name* rather than guessing.
- **Say it.** `"do Maggi aur ek Pepsi"` in Hinglish. Voice proposes lines; a person accepts them.
  Voice never moves money on its own.

---

## Run it

```sh
make ui           # build the front end — required, there is no second one
make serve        # the till,  :8790   (catalogue + camera. no secrets.)
make serve-money  # the money, :8788   (the ONLY process with gateway keys)
```

Then open <http://127.0.0.1:8790>.

For a real payment to turn a bill green, the gateway's signed webhook has to reach `:8788` from the
public internet. Expose it and point the Razorpay dashboard at `<public-url>/webhook`:

```sh
cloudflared tunnel --protocol http2 --url http://localhost:8788
```

A quick tunnel gets a **new address every restart**, and it can be revoked while the process keeps
running and retrying. That has cost a real payment once and nearly cost a demo twice, so there is a
check for it:

```sh
make preflight    # is this counter ready to be demonstrated RIGHT NOW?
```

It does not ask whether a process is running — the revoked tunnel's process was alive and retrying
the whole time. It sends a callback down the public address and requires the money service to
answer. The probe is **deliberately unsigned**, so the only honest answer is `400 bad_signature`;
that refusal proves the path is open, and nothing turns green. Then it prints the exact URL to paste
into the Razorpay dashboard, which is the one thing it cannot check for you.

See *The webhook had nowhere to land* in [FAILURES.md](FAILURES.md).

```sh
make test       # python + typescript
make e2e        # real Chromium, real getUserMedia, real server
make lint       # no float may reach a price
make preflight  # is the money path open right now?
```

---

## The nine invariants

1. **Integer paise, everywhere.** No float ever touches a price. `tools/lint_no_float.py` enforces
   it on the money modules; the browser asserts it at the boundary rather than rounding.
2. **Green only on a verified webhook.** A session turns PAID when a signature-verified webhook
   matches it on four conditions. The browser can *refuse* a payment; it can never *grant* one.
3. **No model weights in the browser.** The page ships no weights and calls no third-party
   inference; `connect-src` is `'self'` alone and `script-src` permits no inline script anywhere.
   The SERVER embeds through 4.96 MB of Apache-2.0 SqueezeNet weights (`gawaah/embedder2.py`) and
   proposes regions with an optional 4 MB YOLO — both loaded through `cv2.dnn`, both on this
   machine. The retired claim is "recognition is handcrafted maths end to end"; the kept claim is
   that nothing about a shop leaves the machine and nothing runs on the customer's device.
4. **Only the counter area is uploaded.** In appearance mode everything outside the chosen rectangle
   is discarded in the browser before the request. In code mode the whole frame goes up — and the
   page says so, because a code can be anywhere on a packet.
5. **One holder of secrets.** `gawaah/paisa.py` is the only process with gateway credentials, and it
   re-derives every rupee from its own tables before minting. The till sends a scan id and the
   amount it is showing; paisa re-prices the witness itself and refuses if the two disagree.
6. **No forgery primitives.** A payment QR is a render of the opaque link the gateway issued.
   Nothing here constructs or regenerates a UPI payload.
7. **Abstain rather than guess.** An item that does not clear its bar is amber and is excluded from
   the total. A short bill an operator can see beats a confident bill that is wrong.
8. **The browser is never an author.** The counter writes its own witness server-side under an id.
   The page is given no field in which to assert a payload or a SKU.
9. **Every published number comes from running code.** Where something failed, the failure is
   recorded with it — that is what [FAILURES.md](FAILURES.md) is.

---

## Measured

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

---

## Teaching a product

Three ways, and they are not equivalent:

- **by code** — binds a SKU to a printed identifier. Nothing about appearance is stored, and
  nothing about appearance is needed. This is the most reliable path.
- **by photograph** — appearance only. No millimetres, no size check, and a stricter similarity bar
  to compensate.
- **on the printed mat** — the TAKHTI A3 sheet carries four ArUco markers of known spacing, so the
  product is stored with its true footprint in millimetres and a wrong-sized packet is thrown out by
  the tape measure before appearance is consulted.

**How by-look decides now.** Crops are embedded by `gawaah/embedder2.py` — SqueezeNet features,
nuisance-whitened, rotation-averaged — and matched against the shop's own taught views at a gate of
0.55 (0.60 with no footprint). The gates sit inside a measured gap: on committed fixtures the
weakest same-product pair scores 0.63 and the strongest different-product pair 0.44, and
`tests/test_embedder2_separation.py` holds that frontier as executable numbers. The case that
forced the change — the same jar taught in daylight and shown in warm evening light — scored 0.74
against the old 0.92 gate and abstained; it now scores 0.82 against 0.60 and is priced. **+ VIEW**
still helps: another angle is another view, and the collision guard still refuses a view that would
make two products ambiguous.

Teaching **from the camera** takes an eight-frame burst through the quality gate in `gawaah/saaf.py`
and enrols the sharpest survivor. If nothing survives, nothing is taught — there is no override,
because a gate you can wave through is decoration. A single uploaded *file* cannot be gated this
way: the measurements are comparative, and one still has nothing to be compared against.

---

## Finding several products in one frame

Two questions, deliberately separated — a single vision model asked to do both does the first
adequately and the second not at all:

- **where** — `gawaah/detector.py`, class-agnostic regions, no product knowledge
- **which** — the shop's own taught vectors, at the same cosine gate every other path uses

Measured on three products laid on a 1280×720 counter:

| proposer | found | IoU | time |
|---|---|---|---|
| classical contours | **3 / 3** | 0.90–0.93 | 79 ms |
| COCO YOLOv5n | 0 / 3 | — | 35 ms |

YOLO runs server-side through `cv2.dnn` (4 MB ONNX, no torch) and its **class head is never read** —
a bar of Lifebuoy is not one of the eighty things it knows, and its best guess for one is "person".
It stays wired because it adds recall on the COCO objects that do appear at a counter — a bottle, a
cup, a phone — and it is optional: delete `models/` and everything still works.

**A stated limit:** packets closer together than about a finger's width read as one item. Below
~20 px of separation the two masks genuinely fuse and nothing in the mask can separate them. A test
asserts that failure so it cannot be discovered by a shopkeeper whose bill was short.

## The storefront

`GET /store/qr` prints a QR for the shutter. A customer scans it, browses the catalogue this counter
has taught, and places an order with an address. The order lands on the **Orders** screen.

The customer's phone never sets a price: it sends sku ids and quantities, the server prices the
basket from the shop's own catalogue, and paisa then re-prices the whole thing from *its* tables
before it mints. The only payable string that ever reaches a phone is the opaque `short_url` the
gateway issued.

For a phone to reach it at all, the till has to be publicly addressable — `/store/link` reports
`reachable_from_a_phone: false` when it is not, rather than printing a QR that cannot work:

```sh
cloudflared tunnel --protocol http2 --url http://localhost:8790
```

## Layout

```
gawaah/     the modules, one file per capability.
            money      paisa · kernel · rzp_live · live_app (the ONLY secret holder)
            seeing     recogniser · embedder2 · detector · identity · saaf · takhti
            the shop   shop_store · storefront · offers · categories · stock · expiry
                       weighed · shelf · labels · loyalty · customers · receipts
            the books  daybook · expenses · purchases · po · gst · manage · insights
            talking    assistant · advisor · search · share
            the door   auth
models/     an OPTIONAL yolov5n.onnx. Absent is a supported state.
tools/      upload_app.py    the till server, 23 routers mounted
            preflight.py     is the money path open right now?
            lint_no_float.py invariant 1, enforced
            migrate_gallery.py  re-embeds every taught product when the metric changes
ui/         the React front end. 28 routes, all but Till and Products lazy-loaded.
            src/lib/strings/{en,hi,bn}.ts  every word of the chrome, three languages
tests/      4044 python · 169 browser-logic · 28 end-to-end
results/    the shop's own data: catalogue, scan witnesses, hash-chained audit log
```

`make verify-ledger` re-walks the audit chain and fails if any link is broken.
`make preflight` re-walks the path a rupee travels and fails if it is not open.

---

## What is written down

[FAILURES.md](FAILURES.md) is the running record of every defect on this build, written as it
happened — nearly three thousand lines. It includes the twelve times the *test harness* was wrong
and the product was fine, because that turned out to be the most useful pattern in the file.

[SUBMISSION.md](SUBMISSION.md) is the Buildathon write-up.
