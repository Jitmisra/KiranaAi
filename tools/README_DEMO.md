# `tools/demo.py` — the six-minute demo

A complete synthetic counter session, driven through the **real** GAWAAH modules,
rendered live in your terminal. It runs on a clean clone with **no camera, no
credentials and no network**.

```sh
./.venv/bin/python tools/demo.py
```

That is the whole setup. It takes about half a second and ends on a GREEN counter, a
paise total, and a verified audit chain.

---

## Run it

| Command | What you get |
|---|---|
| `python tools/demo.py` | the happy path, end to end, ~0.5 s |
| `python tools/demo.py --scenario amber` | the counter abstains, nothing is minted |
| `python tools/demo.py --scenario offline` | the link dies mid-sale and comes back |
| `python tools/demo.py --scenario mismatch` | money lands for the wrong number → RED hold |
| `python tools/demo.py --scenario attack` | five forgeries, five refusals |
| `python tools/demo.py --all` | all five in order, one summary line each (~0.9 s) |
| `python tools/demo.py --slow` | paced for filming (~16 s) |
| `python tools/demo.py --json` | one machine-readable document, nothing else |
| `python tools/demo.py --selftest` | run all five and assert what they prove (~7 s) |

Other flags: `--seed N` (default 7), `--no-color`, `--ascii`, `--out DIR`.

**If you have six minutes**, run `--slow` and watch it once, then run
`--selftest` and read the assertion list. That is the whole product.

### Rendering

Box drawing, ANSI colour, aligned columns and tabular numbers when stdout is a
TTY. When it is a pipe or a file it degrades automatically: **no ANSI is
emitted off-TTY** (asserted by the self-test), and `--ascii` additionally drops
to pure 7-bit ASCII for terminals that cannot encode the box characters.
`NO_COLOR` is honoured.

### Reproducibility

`--seed N` is byte-reproducible: same seed, same bytes, same chain head.

```sh
python tools/demo.py --seed 7 > a.txt
python tools/demo.py --seed 7 > b.txt
cmp a.txt b.txt        # identical
```

The one behaviour the demo changes to get there is `kernel.new_nonce`, which
normally draws 128 bits from the OS CSPRNG. The demo swaps in a seeded
substitute so the nonce that lands in the audit lines is stable. This is
announced in the header of every run and reported as `deterministic_nonce: true`
in `--json`. **Nothing else is stubbed.**

---

## What each scenario proves

Every number below is printed by the run, not by this file.

| scenario | ends in | total | authorised | amber | refusals | chain |
|---|---|---:|---:|---:|---:|---:|
| `happy` | `PAID` | 31350 p | 31350 p | 1 | 0 | 38 lines |
| `amber` | `FROZEN_TOTAL` | 21450 p | **0 p** | 1 | 1 | 19 lines |
| `offline` | `PAID` | 31350 p | 31350 p | 1 | 0 | 40 lines |
| `mismatch` | `AMOUNT_MISMATCH` | 31350 p | **0 p** | 1 | 1 | 37 lines |
| `attack` | `PAID` | 31350 p | 31350 p | 1 | **5** | 46 lines |

### `happy` — the sale that works

The mat locks on four ArUco markers (scale error 0.402 %, perspective index
0.0179, both inside gate). Four goods land and are measured in millimetres on
the rectified 840×1188 buffer. Three are named from the gallery; one scores
−0.118 cosine and is **abstained on**. Three cross the exit edge, the soap does
not. paisa re-runs the crossing predicate server-side over 18 frames, agrees,
and mints. The webhook arrives signed and the counter turns GREEN.

Proves: the whole pipeline, and that **the abstained line is shown, logged, and
excluded from the total** (INVARIANT 7). The basket is ₹313.50, not ₹358.50 —
the unknown sachet is never guessed at, and the soap that stayed on the mat is
never billed.

### `amber` — the counter refuses to guess

A hand occludes an item mid-crossing; it reappears past the exit line. Two
crossings cannot be attributed to a tracker. The counter freezes its total at
21450 p and paisa independently refuses the mint with `uncounted_crossing` —
409, no payment link, **zero rupees moved**.

Proves INVARIANT 7 under adversarial motion, and that the refusal is reached
*twice, independently*: the counter froze on its own, and paisa (which has never
seen a camera) refused on its own replay. A silent under-count is the failure
mode that matters here, and it does not happen.

### `offline` — billing survives, money does not move

The link drops before settlement. The counter goes `PENDING_OFFLINE`, keeps
billing locally (31350 p), and **authorises nothing** — it does not even ask
paisa to mint. When the link returns, exactly one intent is queued, drains, and
settles.

Proves: a payment target you cannot display is worse than none, and a retry
queue that mints twice is worse than both. Queue depth is asserted to be exactly 1.

### `mismatch` — a valid signature is not enough

The simulator pays the wrong amount. The signature is valid, the event is in the
green set, the session is a known open intent — three of the four legs pass. The
amount is off, so the counter goes to a RED hold at `AMOUNT_MISMATCH` and
authorises nothing.

Proves INVARIANT 2's fourth leg is load-bearing rather than decorative. A
tampering-with-money test the run cannot pass by accident.

### `attack` — five forgeries in a row

1. A **lying price** in the intent → 409 `price_disagreement` (paisa reprices from its own book).
2. A **lying crossing** set → 409 `crossing_set_mismatch` (paisa replays the predicate itself).
3. A **tampered body** → 400 `bad_signature`.
4. A **re-serialised body**, semantically identical → 400 `bad_signature`.
5. A **byte-identical replay** of the genuine delivery → `replay`, not re-greened.

The one genuine delivery still settles, exactly once.

Number 4 is the interesting one. `rzp_sim` serialises in insertion order, not
sorted-key canonical JSON, so a receiver that parses and re-serialises before
checking the HMAC **fails**. INVARIANT 2's "raw bytes before any JSON parse" is
therefore enforced by the fixture rather than merely requested.

---

## What it prints at the end

Every run ends with the exact paise total, the ledger verification result, and
one screenshot-sized summary line:

```
GAWAAH happy/seed 7 → PAID · total 31350 paise (Rs 313.50) · authorised 31350 paise
· 1 amber excluded · 0 refusal(s) · ledger VERIFIED 38 lines head 4a0b9d524380
```

It also prints a copy-pasteable command to re-verify the chain **with code that
did not write it**, carrying the real path for your platform (on macOS the
system temp dir is under `/var/folders/…`, not `/tmp`). Run it:

```sh
python -c "from gawaah.ledger import verify; print(verify('<the path the run printed>'))"
# (True, 38, '4a0b9d52438040f08761e6dc658efd341c10c71bcb6e255526ccce2b5eb62487', None)
```

That head is the same one the run printed, and the same one you get with the
network blocked.

Change one digit anywhere in that file and it stops verifying, naming the line:

```
(False, 15, '4f00180bc82bebdd…', 'line 16: hash mismatch — stored afbfb47b020eb765… recomputed 6bd211d6eced7c11…')
```

---

## The self-test

`--selftest` is the demo's own test, and the thing to run if you trust nothing
else here. It shells out to `demo.py` the way you would — real subprocess, real
argv, real exit code — three times per scenario: once for `--json`, twice plain
to compare byte for byte and to scan for leaked ANSI.

```
$ ./.venv/bin/python tools/demo.py --selftest

   PASS  happy     exit 0  PAID             22/22 checks  camera  head 4a0b9d524380
   PASS  amber     exit 0  FROZEN_TOTAL     22/22 checks  camera  head 4f2ba886a693
   PASS  offline   exit 0  PAID             23/23 checks  camera  head e07639fefd08
   PASS  mismatch  exit 0  AMOUNT_MISMATCH  22/22 checks  camera  head 219e49cf19c5
   PASS  attack    exit 0  PAID             26/26 checks  camera  head 99f1031393c3

   ALL PASS   115 assertion(s) passed, 0 failed, across 5 scenario(s)
```

Exit code is 0 only if all 115 hold. `--selftest --json` emits the same result
as a document for CI. Eighteen assertions apply to every scenario (exit code,
expected final state, chain verifies from genesis, chain only grows, total is
whole paise, amber lines carry no price *and* are excluded from the total, money
authorised never exceeds the basket, no ANSI off-TTY, byte-identical reruns, and
the mat locked if OpenCV is installed); the rest are specific to what each
scenario exists to prove. `--selftest --scenario NAME` narrows it to one.

The `camera` / `NO-CAM` column is there because a pass is not always the same
pass — see the OpenCV note below.

These assertions have been mutation-checked — deliberately breaking the demo
makes exactly the right ones fail, and nothing else:

| mutation | what fails |
|---|---|
| wrong `EXPECTED_STATE` | exit code, expected state, self-reported ok |
| nonce ignores the seed | byte-identical rerun, head-matches-JSON |
| emit ANSI even off-TTY | no-ANSI-when-piped |
| total quietly +100 paise | amber-excluded-from-total, authorised-equals-basket |
| claim OpenCV while it is blocked | the-mat-locked |

---

## What this does **not** prove

Read this section before you believe anything above.

**There is no camera.** The frames are drawn by `tools/demo.py` itself and
warped off-nadir to give the plane engine something real to solve. The
homography, the scale and perspective gates, the millimetre measurements, the
tracking and the crossing decisions are all genuinely computed by the shipped
modules — but on synthetic pixels of our own choosing. This demo says nothing
about glare, motion blur, a dirty mat, a torn marker, or a real shop's lighting.
Those live in `tools/bench.py` and in the failure log, not here.

**There is no Razorpay.** `gawaah/rzp_sim.py` signs the webhooks with a secret
this repo generated, and it stamps every body it emits with `_gawaah_sim: true`
so a simulated green can never be mistaken for a real one. The HMAC check, the
raw-bytes discipline and the four-part green predicate are the real shipped
code — but the counterparty is a fixture. No live payment has been made.

**There is no network.** Everything above happens in-process; `paisa` is reached
over `fastapi.testclient`. The whole demo, including the self-test, runs with
`socket.connect`, `socket.create_connection` and `socket.getaddrinfo` all
raising — and produces byte-identical chain heads. That is a strength for
reviewability and a limit on what it demonstrates about deployment.

**Without OpenCV, a green run proves considerably less.** `paisa` deliberately
never imports `cv2`, so the demo degrades rather than dying: if OpenCV is not
importable it skips the camera stage, submits the identity matrix as the
homography, and still runs the whole money path to GREEN — exit 0, all
assertions passing. That is honest behaviour for a server, and a trap for a
reviewer, so the self-test prints `NO-CAM` per scenario and a banner at the end,
and `--json` carries `camera_stage_ran`. **If you see `NO-CAM`, the mat lock,
the millimetre measurements and the crossings did not run.** Check the column
before you believe the table. (`pip install opencv-python-headless` fixes it;
the chain heads differ between the two modes, which is expected — different
inputs, different chain.)

**The identity gallery is small and synthetic.** The abstention in every
scenario is real — a real cosine score below a real threshold — but a
four-item gallery is not evidence about SKU discrimination at kirana scale.

**The nonce is seeded.** As described above. In production it is 128 bits of
CSPRNG. If you want to see the difference, run twice without `--seed`… you
can't: the demo always seeds. That is a deliberate trade of one production
property for reviewability, and it is the only one made.

**No forgery primitives — and the QR is not a QR.** The block that looks like a
QR code is a **sha256 fingerprint of the payment link's `short_url`, rendered
15×15 as blocks**. It is deliberately not scannable, it is labelled as such on
screen, and it is not a UPI payload: the demo never constructs or regenerates
one (INVARIANT 6). If you were hoping to scan it with a phone, that is the point
— there is nothing there to scan.

---

## Where the artefacts land

Each run wipes and rewrites a stable directory per `(scenario, seed)` under the
system temp dir — `gawaah-demo-happy-7/` and friends, under `/tmp` on Linux and
`/var/folders/…` on macOS — containing `audit.jsonl` (the hash chain) and
`kernel.db` (the exactly-once intent table). The path is printed at the end of
every run. Pass `--out DIR` to put them somewhere you will still have tomorrow.

The directory being stable per `(scenario, seed)` is what makes the output
byte-reproducible: the path itself appears in the rendering, so a random temp
dir would defeat `cmp`.
