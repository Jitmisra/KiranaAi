# Razorpay AI Buildathon — Open Track submission

The form asks for twelve things. The personal six are yours. The six about the
build are drafted here from the record, so the answers are true rather than
tidy. Edit freely; do not add a claim the repo cannot back.

---

## Track

**05 · Open Track.** The bar stated: a real problem, a working product,
meaningful use of AI, and evidence that it creates value.

## Project name

**GAWAAH** (गवाह — *witness*)

## What it solves

A kirana counter runs on somebody's word. The customer's word about what is in
their hand. The phone screen's word that money arrived. The shopkeeper's memory
of what is on the shelf and what it cost. Nobody is watching, because watching
is not a job a person can afford to do.

GAWAAH is a counter that is just a camera. Put packets down; it reads their
printed codes, or recognises them by appearance when there is no code, prices
them, and settles with a real Razorpay payment link — and **nothing turns green
until Razorpay's own signed webhook says the money landed.** The browser can
refuse a payment; it can never grant one.

Around that counter it grew the rest of a shop — twenty-eight screens over
twenty-seven server capabilities. A storefront a customer opens by scanning a
QR on the shutter. Orders. Offers that the money service itself re-prices.
Stock, with what is running low and a reorder list you can send to a
wholesaler. Expiry dates, so a batch is pulled before it is sold. Rice, dal and
atta sold by weight from the sack. Shelf positions, printable labels, a
customer loyalty book, expenses, the cash drawer, purchases, a day-close, GST
summaries, receipts to WhatsApp, and a second window turned to face the
customer while their bill is built — which computes no money of its own, and
re-adds the lines only to check the till's total against them.

And an assistant that answers "aaj kitna hua?" from the shop's own books over
**28 tools** — takings, orders, stock, prices, margins, expiry, loyalty, GST —
in English, Hinglish, Hindi and Bengali, typed or spoken. The browser's speech
recognition is set to hi-IN and returns Devanagari, so the parser reads native
script directly: `आज कितनी बिक्री हुई` and `আজ কত বিক্রি হয়েছে` reach the same
tool as the English. One shared vocabulary, not three parsers, because a
counter hears all three in one sentence. It proposes and never bills: voice
cannot move money. The model is a router over local tools, so the shop's data
never leaves the machine — and with no model key at all, every one of those 28
tools still answers. All three languages reach the screen, not just the assistant's answers: the
sidebar, the status pills, the tagline and every word of the till itself — 100
strings, checked by the compiler at 97 call sites, verified in a real browser
rather than from the table. गल्ला · सामान · क़िस्में · कैमरा चालू करो;
ক্যাশ · জিনিসপত্র · ধরন · ক্যামেরা চালু করুন.

Every number on every screen is counted from a hash-chained audit log. Where a
figure cannot be derived, the screen says so instead of showing a
plausible-looking one. That rule is the product.

The GST month is the clearest example. It emits a GSTR-1 B2C summary and a CSV
in integer paise, and for every line whose product has no rate assigned it
returns an **exception** rather than a zero — 91 of them today, listed by SKU
and by bill. It records that the slabs it knows are 0, 5, 12, 18 and 28, that
aerated drinks and tobacco moved to 40 per cent in September 2025, and that a
product taxed outside its slabs "stays unrated and is listed as an exception
rather than summarised at a rate it is not taxed at". It reports
`complete: false`, says storefront orders are on a separate chain and excluded,
documents its own rounding rule, and states plainly that it files nothing with
the government and is not tax advice. A number that would be wrong is worth
less than an empty column that says why.

## What broke, and how you got out

Four, chosen because each one changed how the product was built. The full
record is `FAILURES.md` — 2,700 lines, written as it happened.

**1. A real payment settled and the till spun for 78 seconds saying nothing —
twice.**
The customer's screen said *Payment Completed*. The till said
*AWAITING_SETTLEMENT*. Every diagnostic asked "is cloudflared running?" and it
was — the process had been up for two days. Its tunnel had been revoked, and it
was looping on `Unauthorized: Tunnel not found` while Razorpay posted the
callback into a dead address. The audit chain showed it plainly: twelve
webhooks ever, the newest two days old, one settlement in eighty-nine intents.

The way out was not to make the till guess. It was to make it *say why*. The
money service now stamps every inbound callback — including ones it rejects for
a bad signature, because a forged POST proves the path is open exactly as well
as a real one — and the pay screen, after twenty-five quiet seconds, says: *the
last callback reached this counter two days ago; the customer may well have
paid; check the tunnel.* Refusing to go green was correct. Being unable to
explain the refusal was the bug.

**And then it happened again.** Checking the money path a day later: both
services healthy, both tunnel processes running, uptime an hour, and the same
`Unauthorized: Tunnel not found` in the log with the last callback twenty-six
hours old. The explanation we had built works for a shopkeeper standing in
front of a spinning till. Nothing checked *before* a demo. So `make preflight`
now exercises the path a rupee travels instead of asking whether a process is
up — it POSTs a callback down the public address and requires the money service
to answer. The probe is **deliberately unsigned**, so the only honest answer is
`400 bad_signature`; that refusal is the proof the path is open, and nothing
turns green. A probe that could produce green would be a forgery primitive, so
a test pins it: the script may not import `hmac` or `hashlib` at all, and if
the counter ever *accepts* the unsigned probe the check reports a hard failure.
It was verified in both directions — passing against the live tunnel, failing
against the dead one with the cause and the fix named.

**2. The counter stored a picture of my face and called it a product.**
Teaching a jar of cream by photograph produced a reference that was 58 % pure
black — it kept my hair, my hand and the jar's rim, and cut out the label. The
segmenter estimated the background from the frame's edge, which was a mixture
of wall, cupboard and shirt whose median landed closest to the jar's pale
label. Five approaches were built in parallel against a bench made from the
real failing frame; three were broken by adversarial verifiers (one accepted
faces and empty rooms where the old code correctly refused). The survivor
ranks regions by how solidly each fills its own outline instead of taking the
largest: IoU 0.28 → 0.77, black 62 % → 2.6 %, the working products unchanged
to three decimals.

Then the operator found the second half: pressing CAPTURE while holding the
jar perfectly still failed 8 of 8 frames for *glare* — the quality gate judged
the whole frame, and a bright wall outvoted the product. The fix was the
operator's own suggestion: freeze instantly, draw a box around the product,
and gate only the box. 0 kept → 8 kept, same scene.

**3. The recogniser ranked perfectly and abstained anyway.** The same jar,
taught in daylight and shown in evening light, scored 0.74 against a 0.92 gate
while the best *wrong* product scored 0.34. On a 56-image bench the
handcrafted descriptor's worst same-product score sat *below* its best
different-product score — overlapping distributions, for which no threshold
can be right. Five candidates, one survivor after adversarial attack: a
SqueezeNet feature extractor through `cv2.dnn`, whitened against a synthetic
nuisance corpus. Worst-case gap −0.21 → +0.19. Every stored gallery was
re-embedded from its photograph by a migration tool that refuses on a missing
photo or a collision under the new metric, and the gates were re-derived with
their measurements in the docstring. One limit, written down rather than
hidden: an untaught same-brand variant still scores 0.83.

**4. Offers broke the mint the moment one was switched on.** The storefront
quoted ₹35.00; the money service, re-pricing from its own book, derived
₹31.50 and refused. That refusal is the fifth invariant working — the browser
proposed a total the money service had not derived. There were five surfaces
that put a price in front of somebody, and a discount known to four of them
is a bill nobody can pay. One server-side function now answers the question
the money service will ask, and every surface reads it. The rounding rule is a
decision, not a default: the discount rounds *up* and the shop absorbs the
part-paisa, because a "10 % OFF" sign that floors to 9.9 % is a lie by one
paisa.

And a pattern under all four, counted honestly: **twelve times this build, the
test harness was wrong and the product was fine.** A fixture rotated with
replicated borders; an uncropped scene fed to a function the server always
crops for; a test file importing a module under a second name, leaving two
copies in memory; invented digests; invented field names; and, on the last day, three
URLs invented from memory that were reported as dead routers before I checked
and found all three were my own typos. That last one was fixed by making the
check stop guessing: it reads the app's own OpenAPI schema and asks the server
what it has. The rule that earned its place: when a measurement says something
surprising, suspect the instrument before the code.

## Four money flows, added last

Everything above watches money the counter already sees. These four are the
money it could not see: credit written in a notebook, a packet handed back, the
gap between what the till says settled and what the bank received, and the cost
side of every margin. Each uses a Razorpay product the rest of the product does
not, except the last, which uses none and says so.

**KHATA (खाता) — the udhaar book, collected by Razorpay.** Every kirana runs on
credit, written in a notebook nobody can audit. Say *"Sharma ji ke khate mein
likh do"* at the till and the bill closes **on the book** — neutral ink, no
colour, because nothing settled. COLLECT mints one Payment Link with
`accept_partial`, a first-instalment floor, and `reminder_enable` — **Razorpay
sends the reminders; the shopkeeper never sends a message.** A partial payment
credits the book only when `payment_link.partially_paid` arrives signed, and
only the paise inside that signed body. The bill's own state never becomes
PAID from a partial: a capture is a row in its own table, `INSERT OR IGNORE` on
the signed event id, and it never touches the intents table at all. A capture
larger than the balance parks as `needs_human` rather than netting. Pressing
COLLECT twice is refused by name — `collection_link_already_open`.

> Today, on the live counter: **₹349.50 still on Sharma's book, ₹210.00 already
> collected this month through 2 Razorpay reminder links the shopkeeper never
> sent** — and not one paisa of it turned green until the gateway said so.

**WAAPSI (वापसी) — a return by camera, refunded by Razorpay.** Hold the packet
and the paper receipt's QR up together. The counter reads the SKU off the
packet and the bill id off *its own* `/receipt/{session}` code — recognising its
own host, never parsing a payment payload; a `upi://` string or a gateway host
in that frame is refused by name. It then proves the line was on that bill, that
the bill was settled by a signed webhook, and refunds **the price actually
charged** — off the chain, not the catalogue, so an offer price at the time of
sale is what comes back. The refund is its own state machine, never a negative
debit: `requested` on the HTTP answer, and REFUNDED only when
`refund.processed` arrives over the raw signed bytes. The audit chain shows the
difference plainly — `refund.created`, `refund.calling` and `refund.requested`
carry no event id; `refund.processed` carries one. Stock returns and loyalty
clawback are derived from that same signed line, never a second copy.

> On the live receipt, derived and not typed: **"Refunded via the gateway —
> ₹10.00 of ₹72.00."** The total above it is what was billed; it is not adjusted.

**MILAN (मिलान) — the day close matched against Razorpay's own report.** At day
close the counter asks the gateway what it actually settled to the shop's bank,
matches every green bill by `payment_id` and exact paise, and lists every
exception by name: `settled_not_yet_in_recon` (T+1, said on screen),
`settled_not_in_recon`, `amount_mismatch` (parked, never netted), refunds,
adjustments, unreadable rows — and `in_recon_not_on_chain`, **money Razorpay has
that the counter never heard about.** That last class is the dead tunnel found
after the fact: a customer paid while nobody was listening, and the row proves
it. Pressing SETTLE FROM THE GATEWAY'S RECORD runs the kernel's existing
read-only reconcile path; it mints nothing and charges nothing, and a test greps
the module's imports to pin that it cannot.

> **"₹976.42 reached the bank today, net of ₹23.58 fees and tax, 4 bills
> matched"** — and it found the ₹58.00 paid while the tunnel was dead, settled
> from the gateway's own record.

**PARCHI (पर्ची) — photograph the wholesaler's bill and the margin becomes
known.** The books said it plainly: *no product sold today has a recorded cost,
so the margin is not known.* Nobody types cost prices in. So: photograph the
distributor's invoice. A vision model turns a messy printed document into
lines — the one job here rules genuinely cannot do — and everything after it is
deterministic. Every figure is read as a **string** and parsed digit by digit;
a float in the model's answer is refused. Names are matched **locally** against
the catalogue, which the model never sees. Then the gate, in integer paise:
every line's `qty × rate` must equal its printed amount, and the lines plus
printed taxes must equal the printed total. **One paisa off and the whole bill
is refused, naming the line.** This one uses no Razorpay product, and the screen
says so.

Measured on a bench of five generated invoices, 30 lines, reported because
unmeasured results deserve to be judged harder: name 30/30, quantity 30/30,
rate 30/30, amount 30/30, local match 30/30, and the gate correct on 5/5 —
including the invoice deliberately built one paisa over, refused as *"line 2:
36 × ₹11.75 is ₹423.00; the bill prints ₹423.01."*

> **Cost known for 0 → 6 of 36 products from one photograph in under ten
> seconds; today's margin goes from partial to complete — ₹43.20 on ₹240.00
> sold, 18.0%.**

## GitHub repo URL

*(public — add before submitting)*

## 5-minute pitch video

Unlisted is fine. A script that fits, timed against the product as it stands:

| time | on screen | say |
|---|---|---|
| 0:00 | a kirana counter, real | A kirana counter runs on somebody's word. This is the witness. |
| 0:20 | Till, hold up a packet | Codes read anywhere in the frame. Hold it, don't aim it. |
| 0:40 | Lay four things down, READ THE WHOLE COUNTER | Several at once. And what it *can't* name, it says — it never guesses a price. |
| 1:05 | Say "दो साबुन add karo" into the mic | Devanagari straight off the recogniser, mixed with English in one sentence. It proposes ₹63.00 — two soaps at the offer price — and adds nothing. A person accepts. Voice never moves money. |
| 1:25 | CHARGE → QR → pay on a phone → PAID | That green is Razorpay's signed webhook, not the browser. Nothing else can produce it. |
| 1:55 | Settings → the webhook card | And when the callback can't reach the counter, it says so — this is what saved a real payment from a silent spinner. |
| 2:15 | Scan the shutter QR on a phone → storefront → order → Orders screen | The same catalogue, the customer's side. Server prices it; the phone never sets a price. |
| 2:45 | Offers → switch one on → storefront shows ₹35 struck to ₹31.50 | The money service re-prices from its own book. The discount is a number *it* derives. |
| 3:05 | Today · Insights | Every number counted from a hash-chained log. Where it can't derive one, it says "not enough history" instead. |
| 3:20 | GST → the month, then the CSV | GSTR-1 B2C shape, integer paise, ready for whoever files it. Ninety-one lines are listed as **exceptions** rather than summarised at a rate they are not taxed at — including the 40 % slab it will not pretend to record. It says it does not file, and it doesn't. |
| 3:35 | Ask the advisor "aaj ka margin kitna hai", spoken, answered aloud | It says: *no product sold today has a recorded cost, so the margin is not known — **it is not zero***. That is the whole product in one sentence. The model is a router over local tools; every figure comes from the module that owns it, scrubbed before the model ever sees it, and the shop's data never leaves the machine. |
| 4:05 | FAILURES.md, scrolling | What broke, written as it broke. Twenty-seven hundred lines. Six of them are the test harness being wrong, not the product. |
| 4:30 | Products → teach a jar → the box → 8 kept | It got here by measuring, refusing to guess, and writing down every limit. |
| 4:50 | the counter, again | गवाह. The witness. |

---

*Drafted from the repository's own record on 2 September 2026, extended
4 September. Gates at the time of writing: **4,758 python tests, 344
browser-logic tests, 47 end-to-end tests**, no-float lint over the money path,
TypeScript clean, and every screen free of horizontal overflow and console
errors at 1440px and 390px. Nothing above is a claim the code cannot back;
where the product has a limit, the limit is in the sentence.*
