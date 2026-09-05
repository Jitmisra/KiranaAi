# DEPLOY — GAWAAH on Render

The runbook for putting this counter on the internet. Read it in order. The
section you must not skip is **[3. The shop is not there](#3-the-shop-is-not-there-and-the-two-services-cannot-see-each-others)** —
it is the difference between a demo that bills and a demo that refuses.

Everything here assumes `render` CLI v2.22.0, already authenticated as
`agnikmisra@gmail.com` in workspace `tea-culeoul6l47c73dnfdq0`.

---

## 1. What gets deployed

`render.yaml` declares **two web services from one repo and one Dockerfile**,
distinguished only by their start command:

| service        | port    | what it is                                             | credentials |
|----------------|---------|--------------------------------------------------------|-------------|
| `gawaah-till`  | `$PORT` | the counter: catalogue, camera, books, storefront, receipts | model key only |
| `gawaah-money` | `$PORT` | the money: mints, prices, the audit chain              | **the only service with Razorpay keys** |

That split is invariant 5. It survives the move to Render: `render.yaml`
declares no `RAZORPAY_*` variable on the till, exactly as the Makefile's `serve`
target greps seven model/voice names out of `.env` and pointedly does **not**
`. ./.env` the way `serve-money` does.

The browser never talks to the money service. The till proxies at
`/api/money/*` (`tools/upload_app.py:7194`), so there is no CORS story and the
money service's URL is a server-to-server address.

### What the blueprint assumes of the image

Five things, all of them **checked against the `Dockerfile` now at the repo
root**. Re-check them if that file changes, because `render.yaml` breaks quietly
if any one of them moves:

1. **`WORKDIR /app`, and `/app` is the repo root.** Both commands use
   `python -m uvicorn`, which puts the working directory on `sys.path`
   (`PYTHONPATH=/app` is set too), so `gawaah/` imports and
   `python tools/seed_shop.py` resolves. ✔
2. **`ui/dist` is built into `/app/ui/dist`** by the node stage. `ui/dist` is
   gitignored (`.gitignore:12`), so it does not exist in a fresh checkout;
   `UI_DIST` is `Path(__file__).parent.parent / "ui" / "dist"`, and a till
   without it answers `GET /` with **503 and an instruction** — a better failure
   than a different product, but not a demo. ✔
3. **`models/` is copied.** `squeezenet1.1-7.onnx` is **not optional** — without
   it every teach and every recognition refuses with `embedder_unavailable`.
   `yolov5n.onnx` is optional and its absence is a supported state. ✔
4. **`.env` is never COPYed** and `.dockerignore` excludes it. ✔
5. **It runs as the non-root user `gawaah` (uid 10001), and the only writable
   directory is `/app/results`,** which the image creates and chowns. That is
   why `GAWAAH_DATA_DIR=/app/results` and `GAWAAH_SHOP_DIR=/app/results/shop` in
   `render.yaml` and not `/data`: a path outside that tree is root-owned and the
   first write fails. ✔

One consequence worth knowing before the seeder runs: the image installs
`fonts-dejavu-core` but **no Devanagari face, deliberately** — this Pillow wheel
has no Raqm shaping, so `tools/packshot.py` drops the Hindi line regardless. The
seeded product tiles carry the Latin name and pack size and no Devanagari. That
is a decision, not a defect. If you would rather skip the drawn tiles entirely,
add `--no-photos` to the seeder line in `render.yaml`.

The image's own `HEALTHCHECK` is Docker-level and Render ignores it; Render uses
`healthCheckPath: /health`, which is declared per service in the blueprint. They
happen to check the same thing.

Prove the image locally before you push anything:

```bash
docker build -t gawaah:local .

# The till, seeding itself, exactly as Render will run it.
docker run --rm -p 8790:8790 \
  -e PORT=8790 -e GAWAAH_DATA_DIR=/app/results -e GAWAAH_SHOP_DIR=/app/results/shop \
  -e GAWAAH_SEED_ON_BOOT=1 \
  gawaah:local \
  sh -c 'mkdir -p "$GAWAAH_DATA_DIR" "$GAWAAH_SHOP_DIR" || exit 1;
  if [ "$GAWAAH_SEED_ON_BOOT" = "1" ]; then
  ( i=0; while [ $i -lt 24 ]; do sleep 5;
  python tools/seed_shop.py --till "http://127.0.0.1:$PORT"; c=$?;
  [ $c -eq 1 ] || break; i=$((i+1)); done ) &
  fi;
  exec python -m uvicorn upload_app:app --app-dir tools --host 0.0.0.0 --port "$PORT"'

# The money, in another shell.
docker run --rm -p 8788:8788 \
  -e PORT=8788 -e RZP_MODE=sim \
  -e GAWAAH_DATA_DIR=/app/results -e GAWAAH_SHOP_DIR=/app/results/shop \
  -e RAZORPAY_KEY_ID=rzp_test_LOCALPROOF -e RAZORPAY_KEY_SECRET=localproof \
  -e RAZORPAY_WEBHOOK_SECRET=localproof \
  gawaah:local \
  sh -c 'mkdir -p "$GAWAAH_DATA_DIR" "$GAWAAH_SHOP_DIR" || exit 1;
  exec python -m uvicorn --factory gawaah.live_app:app --host 0.0.0.0 --port "$PORT"'
```

And prove there is no secret in it:

```bash
docker run --rm gawaah:local env | grep -iE 'razorpay|api_key|secret'   # expect nothing
docker run --rm gawaah:local sh -c 'ls -la .env'                        # expect "No such file"
docker history --no-trunc gawaah:local | grep -iE 'rzp_|secret'          # expect nothing
```

---

## 2. Push the blueprint

`render.yaml` must be at the repo root of the branch Render watches (`main`).

```bash
cd /Users/agnik/Desktop/razor
git status --short                      # .env must NOT appear. It is gitignored; check anyway.
git add render.yaml DEPLOY.md Dockerfile requirements.txt
git add .dockerignore 2>/dev/null || true
git commit -m "Render blueprint: the till and the money as two services"
git push origin main
```

Then launch it. **Blueprint creation is a dashboard action** — as of CLI
v2.22.0 there is no `render blueprint launch`; the CLI manages services that
already exist. Run `render --help` if your build differs.

1. https://dashboard.render.com → **New → Blueprint**
2. Connect `Jitmisra/KiranaAi`, branch `main`
3. Render reads `render.yaml` and offers **gawaah-till** and **gawaah-money**
4. It will **prompt for every `sync: false` variable** before it will create
   anything. That prompt is the only place a secret is ever typed. See §4.

---

## 3. The shop is not there, and the two services cannot see each other's

This is the honest part, and it decides what you can demo.

### 3a. There is no shop in a fresh container

`results/` is gitignored, deliberately — it holds a password hash, live session
tokens, customer names and phone numbers, ~6,000 camera frames and the money
chain. So a fresh Render container has **no catalogue, no taught products, no
accounts, no orders**.

`render.yaml` wires the fix: the till's start command backgrounds
`tools/seed_shop.py`, which waits for the till's own `/health` and then adds 30
real kirana products **through the counter's own HTTP API** — the same
`POST /shop` a shopkeeper's screen posts to, every price through
`gawaah/money.py`, every category through `gawaah/categories.py`. Exit code 1
means "not up yet" and is the only one it retries; 2 means already seeded and
stops.

Three things the seeder does not and cannot do, and you should not claim
otherwise on stage: it writes **no bill** (a bill is the fold of a session's own
chain and there is no honest way to manufacture one), it marks **nothing
green**, and the pack shots are **drawn, not photographed** — each tile says so
and so does the PNG metadata.

**It prints a freshly generated shopkeeper password once, to stdout — which on
Render is the service log.** Anyone with dashboard access can read it. It
changes on every boot. That is a real disclosure; it is the price of an
unattended seed, and the alternative is a counter nobody can sign into.

### 3b. The two services cannot see each other's files — and that is the blocker

The till and the money service share state **through files**:

| file | writer | reader |
|---|---|---|
| `$GAWAAH_DATA_DIR/shop.json` | the till, on every product added | the money service, to price the basket (`gawaah/live_app.py:39`) |
| `$GAWAAH_DATA_DIR/audit.jsonl` | the money service | the till, for Books, Today, receipts, loyalty |
| `$GAWAAH_SHOP_DIR/offers.json` | the till | the money service (`gawaah/offers.py:252`) |

The code already knows this can happen. `publish_price_map` in
`tools/upload_app.py:4984` writes to `GAWAAH_DATA_DIR / shop.json` rather than
`store_dir().parent` precisely because the money service "may keep its data
somewhere else — **which the two-service layout is free to do**". Same variable,
same path, two disks.

Two Render services are two containers with two filesystems. **Render disks
cannot be shared between services at any tier** — a disk attaches to exactly one
service — so this is not a free-tier problem you can pay your way out of. On a
two-service deploy:

* The money service boots with `price_book_entries: 0` and never learns
  otherwise. Every recognised line is **amber**, and PAY refuses with
  `amber_in_basket` / `line_unpriced`. paisa re-prices every basket from its own
  book and refuses to mint a total it did not derive — invariant 7 — so **no
  bill can go green.** This is the product refusing correctly, on a deployment
  that lied to it.
* "and never learns otherwise" is literal, and it is a second, independent
  lock on the same door. `gawaah/paisa.py:1357` is
  `price_book or DictPriceBook({})`; an empty `OfferPriceBook` is falsy, so a
  money process that starts before `shop.json` exists **discards its
  file-backed book at construction** and can never pick the file up later. So
  this is not a problem a shared disk would solve even if Render had one:
  boot order alone is enough to break it. §3c's command waits for the file for
  exactly this reason.
* The till's **Books, Today and receipts read `audit.jsonl`**, which the money
  service writes on its own disk. Even if you fixed prices, they would stay
  empty and a receipt would answer `session_not_in_the_ledger`.
* `gawaah/offers.py:252` already names this case: `GAWAAH_OFFERS_FILE` exists as
  "an explicit escape hatch for a deployment that splits the till and the money
  service across machines and **syncs one file between them**". Render gives you
  nothing to sync it with.

**`make preflight` will not catch this.** Its "does the shop agree with itself"
check reads `/shop` and `/store` — both off the till. The number that tells the
truth is on the money service:

```bash
curl -s https://gawaah-money-XXXX.onrender.com/health | python3 -m json.tool | grep price_book_entries
# 0  =>  this deployment cannot bill anything
```

### 3c. The fallback that actually bills: one service, two processes

If the demo has to turn a bill green on Render, run both processes in **one**
container so they share a filesystem. Set the till service's Docker Command to:

```sh
sh -c 'mkdir -p "$GAWAAH_DATA_DIR" "$GAWAAH_SHOP_DIR" || exit 1;
( j=0; while [ $j -lt 150 ]; do
python -c "import json,os,sys;sys.exit(0 if json.load(open(os.environ[\"GAWAAH_DATA_DIR\"]+\"/shop.json\")) else 1)" 2>/dev/null && break;
sleep 2; j=$((j+1)); done;
exec python -m uvicorn --factory gawaah.live_app:app --host 127.0.0.1 --port 8788 ) &
if [ "$GAWAAH_SEED_ON_BOOT" = "1" ]; then
( i=0; while [ $i -lt 24 ]; do sleep 5;
python tools/seed_shop.py --till "http://127.0.0.1:$PORT"; c=$?;
[ $c -eq 1 ] || break; i=$((i+1)); done ) &
fi;
exec env -u RAZORPAY_KEY_ID -u RAZORPAY_KEY_SECRET -u RAZORPAY_WEBHOOK_SECRET
-u RAZORPAY_ACCOUNT_ID python -m uvicorn upload_app:app --app-dir tools
--host 0.0.0.0 --port "$PORT"'
```

**THE MONEY PROCESS MUST NOT START BEFORE `shop.json` EXISTS, AND THAT IS WHAT
THE `while` LOOP IS FOR.** This is not defensive padding; without it the
fallback does not bill either, and the failure is silent. `gawaah/paisa.py:1357`
reads

```python
self.price_book: PriceBook = price_book or DictPriceBook({})
```

`OfferPriceBook` defines `__len__`, so an empty book — which is exactly what
`FileBackedPriceBook` is when `shop.json` does not exist yet — is **falsy**, and
`or` throws the injected, self-reloading book away and substitutes a permanently
empty `DictPriceBook({})`. The mtime reload in `FileBackedPriceBook._fresh` then
never runs, because the object holding it is no longer referenced by anything.
A money process that starts one second before the seeder can never price
anything, for the life of that process, no matter what lands on disk afterwards.

Measured in this image, one container, everything else identical:

```
money started first, then seeded  ->  price_book_entries = 0    (30 SKUs on disk)
seeded first, then money started  ->  price_book_entries = 30
```

The loop polls for a `shop.json` that parses to a **non-empty** object; `[ -s
… ]` is not enough, because a two-byte `{}` is a file with a size and still an
empty book. 150 tries at 2 s is five minutes, which is longer than a cold free
instance plus a full seed.

Set `GAWAAH_PAISA_URL=http://127.0.0.1:8788` on that service, add the three
`RAZORPAY_*` variables and `RZP_MODE=sim` to it, and delete `gawaah-money`.
Prices, offers and the chain are then one set of files and the whole path works:
teach → recognise → PAY → `/sim/pay` → green → receipt → Books.

Paste it as **one line** — Render's Docker Command field is a single-line input.
It is valid shell either way (`sh -n` agrees), but the dashboard will not take
the newlines.

Two things to know about it: **nothing supervises the backgrounded money
process** — if it dies, the health check still passes because Render only
watches the till's port, and every PAY fails until the service is restarted. And
the seeder subshell inherits the gateway variables (it is a child of the same
shell); it never reads them, but `env -u` protects only the till.

**What it costs, said plainly.** `env -u` scrubs the four gateway variables from
the till process, so the till still never holds a credential — but they are in
the container, in `/proc/1/environ`, readable by anything running as that user.
Two processes on one machine is a weaker boundary than two machines, and the
README should say which one is deployed. Nothing else about invariant 5 changes:
the money service is still the only thing that mints, still re-prices every
basket, and still refuses a total it did not derive.

### 3d. The third option: bake the shop into the image

The Dockerfile can start a till at build time, run `tools/seed_shop.py` against
it, and bake the resulting `/app/results` into the image — then both containers
boot from **identical** prices, and a two-service deploy bills correctly for the
seeded catalogue. What still breaks: anything **taught after boot** exists only
on the till, so a newly photographed product is unpriceable at the money service
until the next build. That is a Dockerfile change and belongs to whoever owns
that file.

---

## 4. Set the secrets by hand

Never in a file. Never in `render.yaml`. Dashboard → the service → **Environment**.

**gawaah-money** (and nowhere else):

| variable | value |
|---|---|
| `RAZORPAY_KEY_ID` | your **test** key, `rzp_test_…` |
| `RAZORPAY_KEY_SECRET` | shown exactly once when generated; regenerate if lost |
| `RAZORPAY_WEBHOOK_SECRET` | a long random string, the same one you paste in the Razorpay dashboard |

Generate the webhook secret:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set `RAZORPAY_WEBHOOK_SECRET` **even in sim mode**. Empty means paisa falls back
to a placeholder, `/health` reports `webhook_secret_configured: false`, and
`make preflight` calls that a failure — correctly, because an empty secret makes
every signature forgeable.

`RZP_MODE` is pinned to `sim` as a literal in `render.yaml`. Do not add
`GAWAAH_ALLOW_LIVE_KEYS`: without it `gawaah/rzp_live.py` refuses any key id that
is not `rzp_test_`, and that refusal is the safety rail on a public URL.

**gawaah-till**:

| variable | value |
|---|---|
| `GOOGLE_API_KEY` | the model key. Blank is legal — the advisor falls back to the deterministic parser and browser speech |
| `GAWAAH_PAISA_URL` | filled in at §5 |
| `GAWAAH_REQUIRE_AUTH` | `1`. See below |

**Set `GAWAAH_REQUIRE_AUTH=1` before you tell anyone the URL.** Unset — which is
the default and is right on a shop's own wifi — the catalogue, the books, stock,
customers and every teach route are open to anyone who finds the hostname.
`gawaah/auth.py` keeps `/`, `/health`, `/assets/*`, `/store*`, `/receipt*` and
`/qr/link/*` open regardless, so a customer with a QR is unaffected.

Sign in with the phone and password the seeder printed into the logs:

```bash
render logs --resources srv-XXXXXXXX --output text | grep -A3 "Sign in"
```

Verify no gateway credential reached the till:

```bash
curl -s https://gawaah-till-XXXX.onrender.com/health | python3 -m json.tool | grep money
# "money": "none — this service cannot price, bill or mark anything GREEN"
```

---

## 5. Find the two URLs and join them up

```bash
render services --output json --confirm | python3 -m json.tool
```

(The CLI is interactive by default; `--output json --confirm` makes it
scriptable. Service ids look like `srv-…`.) Take the two
`https://gawaah-till-XXXX.onrender.com` and
`https://gawaah-money-XXXX.onrender.com` hostnames.

Then set, on **gawaah-till**:

```
GAWAAH_PAISA_URL = https://gawaah-money-XXXX.onrender.com
```

No trailing slash — the till concatenates paths onto it
(`tools/upload_app.py:7205`). This is **not** declared with `fromService` in the
blueprint on purpose: `fromService` yields `host:port` with no scheme, and this
value is used as a string prefix, so `https://` has to be part of it.

Saving an environment variable restarts the service — which, on the free tier,
**wipes the shop and re-seeds it**. Do this before the demo, not during it.

The addresses a human actually uses, all on the till:

```
https://gawaah-till-XXXX.onrender.com/          the counter (camera, catalogue, books)
https://gawaah-till-XXXX.onrender.com/store     the storefront — this is the QR on the shutter
https://gawaah-till-XXXX.onrender.com/receipt/<session>/page   a bill
```

The money service's URL is never given to a person or a phone. It exists for the
till and for Razorpay.

---

## 6. Point Razorpay at the money service

Razorpay dashboard → **Account & Settings → Webhooks → Add New Webhook**:

```
URL     https://gawaah-money-XXXX.onrender.com/webhook
Secret  the exact string you put in RAZORPAY_WEBHOOK_SECRET
Events  payment_link.paid  payment.captured            (green — gawaah/webhook.py:71)
        payment_link.partially_paid                    (khata collections)
        refund.created  refund.processed  refund.failed
```

Subscribe to those and no others. An event outside those sets is refused by
name (`'x' is not in GREEN_EVENTS …`) and does nothing but fill the dashboard's
delivery log with 4xx.

Two honest notes:

* **In `sim` mode this webhook is wired but idle.** Nothing at Razorpay ever
  creates a payment against a simulated link. Green comes from `POST /sim/pay`,
  which builds and verifies a properly signed webhook **in-process** — no
  network, no gateway. The dashboard entry exists so that flipping to live test
  keys later is one variable, not a scramble.
* **In live mode, a spun-down money service will miss the callback.** See §8.

The webhook path is unauthenticated by design and safe: `gawaah/webhook.py`
verifies `HMAC-SHA256(raw_body, secret)` before anything is parsed, and an
unsigned POST gets `400 bad_signature` with `green: false`.

---

## 7. Verify with `make preflight`

`tools/preflight.py` does not ask whether a process is running — a running
process proved nothing the day a revoked tunnel cost a real payment. It sends an
**unsigned** callback down the public path and requires the money service to
refuse it. That `400 bad_signature` is the proof the path is open, and nothing
turns green.

```bash
cd /Users/agnik/Desktop/razor
GAWAAH_TILL=https://gawaah-till-XXXX.onrender.com \
GAWAAH_PAISA=https://gawaah-money-XXXX.onrender.com \
GAWAAH_WEBHOOK_BASE=https://gawaah-money-XXXX.onrender.com \
make preflight
```

Wake both services first — a cold start takes longer than preflight's timeouts
and you will get a fake failure:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://gawaah-till-XXXX.onrender.com/health
curl -s -o /dev/null -w '%{http_code}\n' https://gawaah-money-XXXX.onrender.com/health
```

What it will say on a two-service Render deploy, and what each line means:

| line | on Render |
|---|---|
| the till answers / the built UI is being served | PASS, if the image built `ui/dist` |
| N products in the catalogue | PASS once the seeder has finished (~1–3 min after boot) |
| every product on the storefront can be priced | PASS — and **it is asking the till, not the money service**. §3b |
| the money service answers, test-mode key, secrets loaded | PASS once §4 is done |
| a callback reaches the money service | PASS — `400 bad_signature` from the public URL |
| the counter stamped the callback as liveness | PASS — `webhooks_seen` moves |
| all 27 capabilities are mounted | PASS |

With `GAWAAH_REQUIRE_AUTH=1`, preflight has no session, so `GET /shop` answers
401 and **"the catalogue reads" and "both catalogues can be read" go red**. That
is the lock, not a fault. Run preflight before you turn the lock on, or read
those two failures for what they are.

The one number preflight cannot see is `price_book_entries` on the money
service. Check it by hand (§3b) every time.

---

## 8. Free tier: the limits, not the footnotes

* **No persistent disk.** Free web services get none. The catalogue, taught
  products, shopkeeper accounts, orders, `kernel.db` and the `audit.jsonl` hash
  chain are erased on every deploy, restart, OOM kill and spin-down. The chain
  restarts from genesis; `make verify-ledger` will happily verify a chain with
  no history in it. **Anything demonstrated is gone by the next morning.**
* **No shared disk at any tier.** A Render disk attaches to one service. §3b.
* **Services spin down when idle** (~15 minutes) and cold-start on the next
  request — tens of seconds. The two services spin down *independently*, and
  the till's proxy timeout to the money service is **6 seconds**
  (`PAISA_TIMEOUT_S`, `tools/upload_app.py:7195`). So the first PAY after an
  idle period fails with "the money service did not answer" and keeps failing
  until the money service is warm. **Curl both `/health` endpoints before you
  demo.** A cold start also means a fresh, empty shop and a re-seed.
* **A webhook that arrives cold is a bill that does not turn green on time.**
  Razorpay's callback times out well inside a Render cold start. Razorpay
  retries, so the bill may go green minutes later — long after the customer has
  walked off. In `sim` mode this does not bite (the webhook is in-process); in
  live mode it is the single worst free-tier failure, and the fix is a paid
  instance that does not spin down.
* **512 MB RAM, 0.1 CPU (shared).** The till runs SqueezeNet through
  `cv2.dnn` on frames up to 2600 px, with `MAX_UPLOAD_BYTES` at 48 MB. One
  recognition is seconds, not milliseconds; two concurrent ones can OOM — and an
  OOM restart is also a data wipe.
* **Free instance hours are pooled per workspace** (750/month at the time of
  writing) and two services draw on the same pool. Check the dashboard's usage
  page before a long demo window.
* **No shell on free instances.** You cannot exec into the container to inspect
  or repair the shop. Logs are the only channel — which is also why the seeder's
  password lands in them.
* **Build time.** Every deploy rebuilds the front end (`npm ci` + vite) and
  installs OpenCV. Expect several minutes. `autoDeploy: false` is set so a push
  cannot do this to you mid-demo.
* **The writable directory is `/app/results` and only that.** The image runs as
  uid 10001 and chowns nothing else. Point `GAWAAH_DATA_DIR` or
  `GAWAAH_SHOP_DIR` anywhere else and both services exit at boot on the
  `mkdir`. If you later attach a paid disk, mount it at `/app/results` **and
  check its ownership** — a disk mounted root-owned under a non-root user is
  the same failure wearing a different hat.
* **One thing that gets *better* on Render:** HTTPS. `getUserMedia` needs a
  secure context, and receipt/storefront QR codes are built from the `Host` and
  `X-Forwarded-Proto` headers (`gawaah/receipts.py:717`), so they finally carry a
  URL a customer's phone can actually open — instead of `127.0.0.1`.

---

## 9. Day-to-day

```bash
render services --output json --confirm                 # ids and URLs
render deploys create srv-XXXXXXXX --output json --confirm --wait
render logs --resources srv-XXXXXXXX --output text --tail
```

Flag names move between CLI releases; `render <subcommand> --help` is
authoritative for the build on this machine. What is *not* in the CLI as of
v2.22.0: creating a blueprint, and setting environment variables. Both are
dashboard work, which is the right place for a secret anyway.

Rollback is dashboard-side: the service's **Deploys** tab → an older deploy →
**Redeploy**. Remember that any redeploy re-seeds a fresh, empty shop.

To take it down: dashboard → each service → **Settings → Delete**. Deleting the
blueprint does not delete the environment variables you typed by hand; the
Razorpay webhook entry stays in the Razorpay dashboard until you remove it, and
a webhook pointing at a dead hostname is exactly the failure `tools/preflight.py`
was written for.
