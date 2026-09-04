#!/usr/bin/env python3
"""Is this counter ready to be demonstrated RIGHT NOW?

    ./.venv/bin/python tools/preflight.py

Run it before every demo, and again the moment anything looks wrong.

WHY THIS EXISTS. A real Rs 99 payment settled on the customer's phone while
the till span for seventy-eight seconds saying nothing. Everything looked
healthy: both services up, `cloudflared` running, its process two days old. The
tunnel behind that process had been REVOKED. It was looping on

    ERR Register tunnel error from server side error="Unauthorized: Tunnel not found"

while Razorpay posted the callback into an address that no longer existed. The
same thing had happened again by the next afternoon.

So this script does not ask whether a process is running. A process proves
nothing. It asks whether the path a rupee travels is open, end to end, from
the public internet to the money service, RIGHT NOW -- by sending a callback
down it and requiring the money service to answer.

THE PROBE IS DELIBERATELY UNSIGNED. It carries a signature that cannot verify,
so the only honest answer is 400 `bad_signature`. That 400 is the proof: it can
only have been produced by the money service, which means the path is open. A
probe that could turn something green would be a forgery primitive, and this
codebase does not contain one. The money service stamps liveness on a REJECTED
callback exactly as it does on a real one, because reachability and
authenticity are different questions and only the first one is being asked.

WHAT IT WILL NOT DO. It changes nothing. It cannot edit the Razorpay dashboard,
so where a URL has drifted it prints the URL and says where to paste it. Quick
tunnels get a new hostname every restart, which is the underlying reason this
keeps breaking, and pretending otherwise would just move the surprise later.
"""
from __future__ import annotations

import json
import os
import pathlib
import socket
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent

G = "\033[32m"; R = "\033[31m"; Y = "\033[33m"; D = "\033[2m"; B = "\033[1m"; X = "\033[0m"
if not sys.stdout.isatty():
    G = R = Y = D = B = X = ""

TILL = os.environ.get("GAWAAH_TILL", "http://127.0.0.1:8790")
PAISA = os.environ.get("GAWAAH_PAISA", "http://127.0.0.1:8788")

# Where the tunnel hostnames get written when they are started. An override
# exists because these live in a scratch directory that differs per machine.
URL_DIR = pathlib.Path(os.environ.get("GAWAAH_TUNNEL_DIR", "")) if os.environ.get("GAWAAH_TUNNEL_DIR") else None

FAILED: list[str] = []
WARNED: list[str] = []


def say(state: str, what: str, detail: str = "") -> None:
    colour = {"PASS": G, "FAIL": R, "WARN": Y}[state]
    print(f"  {colour}{state:<4}{X}  {what}")
    if detail:
        for line in detail.splitlines():
            print(f"        {D}{line}{X}")


def bad(what: str, detail: str = "") -> None:
    say("FAIL", what, detail); FAILED.append(what)


def meh(what: str, detail: str = "") -> None:
    say("WARN", what, detail); WARNED.append(what)


def get(url: str, timeout: float = 8.0) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        return 0, str(e).encode()


def post(url: str, body: bytes, headers: dict[str, str], timeout: float = 25.0) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        return 0, str(e).encode()


def jget(url: str) -> dict | None:
    code, raw = get(url)
    if code != 200:
        return None
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        return None


# ------------------------------------------------------------------ checks --

def check_till() -> None:
    print(f"\n{B}The till{X}  {D}{TILL}{X}")
    h = jget(f"{TILL}/health")
    if h is None:
        bad("the till answers", f"nothing on {TILL} -- start it with: ./.venv/bin/python tools/upload_app.py")
        return
    say("PASS", "the till answers")

    # `/` returns 503 with an instruction when the React app has not been built.
    # That is a better failure than serving a different, older product.
    code, raw = get(f"{TILL}/")
    if code == 503:
        bad("the built UI is being served", "run: make ui")
    elif code != 200:
        bad("the built UI is being served", f"GET / returned {code}")
    elif b"<div id=\"root\"" not in raw and b"<div id=root" not in raw:
        meh("the built UI is being served", "GET / returned 200 but no React root -- check `make ui`")
    else:
        say("PASS", "the built UI is being served")


def check_catalogue() -> None:
    print(f"\n{B}The shop{X}")
    cat = jget(f"{TILL}/shop")
    if cat is None:
        bad("the catalogue reads", "GET /shop did not answer with JSON")
        return
    skus = cat.get("skus") or cat.get("products") or []
    n = len(skus) if isinstance(skus, list) else 0
    if n == 0:
        meh("something is taught", "the catalogue is empty -- a demo needs at least one product taught by photo or code")
    else:
        say("PASS", f"{n} products in the catalogue")


def check_the_shop_agrees_with_itself() -> None:
    """Can the money service price everything the shop is advertising?

    THE CHECK THAT WOULD HAVE CAUGHT IT. The storefront listed six products
    while the money service held three, so a customer built a basket, pressed
    PAY, and was refused with `amber_in_basket` on a line the shop was openly
    selling. The cause was two copies of the till module loaded under different
    names; the SYMPTOM was two catalogues, and that is cheap to test for.

    Asked of the running server rather than of the files, because the failure
    was precisely that a process disagreed with itself.
    """
    print(f"\n{B}Does the shop agree with itself?{X}")
    till = jget(f"{TILL}/shop")
    store = jget(f"{TILL}/store")
    if till is None or store is None:
        bad("both catalogues can be read", "/shop or /store did not answer with JSON")
        return

    sells = {i["sku_id"] for i in store.get("items", []) if isinstance(i, dict) and "sku_id" in i}
    prices = {s["sku_id"] for s in till.get("skus", []) if isinstance(s, dict) and "sku_id" in s}

    unpayable = sorted(sells - prices)
    if unpayable:
        bad("every product on the storefront can be priced",
            "the shop is advertising " + ", ".join(unpayable) + "\n"
            "A customer can add these to a basket and will be refused at PAY with\n"
            "`amber_in_basket`. Either they are not for sale, or they need a price.")
    else:
        say("PASS", f"all {len(sells)} products on the storefront can be priced")

    hidden = sorted(prices - sells)
    if hidden:
        meh(f"{len(prices)} priced products are all on sale",
            "priced but not on the storefront: " + ", ".join(hidden))


def check_paisa() -> dict | None:
    print(f"\n{B}The money service{X}  {D}{PAISA}{X}")
    h = jget(f"{PAISA}/health")
    if h is None:
        bad("the money service answers",
            f"nothing on {PAISA} -- start it with: ./.venv/bin/python -m gawaah.live_app")
        return None
    say("PASS", "the money service answers")

    key = str(h.get("key_id") or "")
    if not key:
        bad("a key is loaded", "no key_id -- check .env")
    elif key.startswith("rzp_test_"):
        say("PASS", "test-mode key", f"{key[:13]}...")
    else:
        meh("test-mode key", f"{key[:9]}... is NOT a test key. Live keys move real money.")

    for field, what in (("key_secret_configured", "the API secret is loaded"),
                        ("webhook_secret_configured", "the webhook secret is loaded")):
        if h.get(field):
            say("PASS", what)
        else:
            bad(what, "check .env -- it is never printed, only counted")

    stuck = int(h.get("intents_needing_human") or 0)
    if stuck:
        meh("no intent is stuck", f"{stuck} intent(s) need a human decision")
    else:
        say("PASS", "no intent is stuck")
    return h


def check_tunnel(before: dict | None) -> None:
    """The whole point of this script.

    A hostname on disk and a running process are both circumstantial. The only
    evidence that counts is the money service answering a callback that arrived
    from outside.
    """
    print(f"\n{B}The path a callback travels{X}")
    url = None
    if URL_DIR and (URL_DIR / "tunnel_url.txt").exists():
        url = (URL_DIR / "tunnel_url.txt").read_text().strip()
    url = os.environ.get("GAWAAH_WEBHOOK_BASE", url or "").strip().rstrip("/")
    if not url:
        meh("a public address is known",
            "set GAWAAH_WEBHOOK_BASE to the address Razorpay posts to, or GAWAAH_TUNNEL_DIR to\n"
            "the directory holding tunnel_url.txt. Without it this check cannot run,\n"
            "and a dead tunnel is exactly the failure that has cost a real payment.")
        return

    print(f"  {D}{url}{X}")
    if not url.startswith("https://"):
        meh("the address is https", "Razorpay will not post to plain http")

    code, raw = get(f"{url}/health", timeout=20.0)
    if code != 200:
        bad("the address resolves to this counter",
            f"GET {url}/health returned {code or 'nothing'}.\n"
            "If cloudflared is running, its tunnel has probably been REVOKED -- the process\n"
            "survives that and logs `Unauthorized: Tunnel not found` forever. Restart it,\n"
            "take the NEW hostname, and update the webhook URL in the Razorpay dashboard.")
        return
    say("PASS", "the address resolves to this counter")

    # The probe. Unsigned on purpose: 400 bad_signature is the pass condition.
    code, raw = post(
        f"{url}/webhook",
        b'{"event":"gawaah.preflight.reachability"}',
        {"Content-Type": "application/json", "X-Razorpay-Signature": "preflight-probe-cannot-verify"},
    )
    if code == 0:
        bad("a callback reaches the money service", f"the POST never arrived: {raw.decode()[:160]}")
        return
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        body = {}
    if code == 400 and body.get("reason") == "bad_signature" and body.get("green") is False:
        say("PASS", "a callback reaches the money service",
            "the probe was refused for a bad signature, which is the correct answer and\n"
            "proves the path is open. Nothing turned green.")
    elif body.get("green"):
        bad("the money service refuses forged callbacks",
            "an unsigned probe was accepted. This is the one result that must never happen.")
    else:
        meh("a callback reaches the money service",
            f"the POST arrived and returned {code}, but not the expected 400 bad_signature: {str(body)[:160]}")

    after = jget(f"{PAISA}/health") or {}
    seen_before = int((before or {}).get("webhooks_seen") or 0)
    seen_after = int(after.get("webhooks_seen") or 0)
    if seen_after > seen_before:
        say("PASS", "the counter stamped the callback as liveness",
            f"webhooks_seen {seen_before} -> {seen_after}, last at {after.get('last_webhook_at')}")
    else:
        meh("the counter stamped the callback as liveness",
            "the probe was answered but liveness did not move. The pay screen will not be\n"
            "able to tell the shopkeeper when the callback path last worked.")

    print(f"\n  {B}Paste this into the Razorpay dashboard{X}  {D}Settings -> Webhooks{X}")
    print(f"    {B}{url}/webhook{X}")
    if "trycloudflare.com" in url:
        # Naming the KIND of address, not just the address. A quick tunnel is
        # ephemeral by design: this hostname is gone the moment the process
        # restarts, and the dashboard keeps pointing at it. That has cost a
        # real payment once and nearly cost a demo twice. The permanent fix is
        # a NAMED tunnel on a domain the operator owns, which needs a DNS
        # record and is therefore their decision, not this script's.
        print(f"    {Y}This is a QUICK tunnel: the hostname dies with the process.{X}")
        print(f"    {D}It has already changed several times today. A named tunnel on a domain{X}")
        print(f"    {D}you own gives a permanent address and this line stops changing:{X}")
        print(f"    {D}  cloudflared tunnel create gawaah{X}")
        print(f"    {D}  cloudflared tunnel route dns gawaah till.<your-domain>{X}")
    else:
        print(f"    {D}The dashboard is the one thing this script cannot check.{X}")


def check_routers() -> None:
    """Which parts of the shop are mounted, asked of the server rather than guessed.

    The first version of this check invented twenty-four URLs from memory and
    reported three of them as dead routers. All three were my typos:
    `/store/catalogue` is `/store`, `/daybook/today` collided with the
    `/daybook/{day}` parameter and came back 400, and `/receipt/health` does not
    exist because receipts are addressed by session. That is the eleventh time
    on this build that a broken instrument has looked like a broken product.

    So it no longer guesses. It reads the app's own OpenAPI schema -- the list
    of what is actually mounted -- and then GETs only endpoints that take no
    parameters, where a 404 is unambiguous.
    """
    print(f"\n{B}The rest of the shop{X}")
    schema = jget(f"{TILL}/openapi.json")
    if schema is None:
        bad("the shop's routes can be listed", "GET /openapi.json did not answer")
        return
    paths = schema.get("paths")
    if not isinstance(paths, dict):
        bad("the shop's routes can be listed", "/openapi.json carried no paths")
        return

    # One prefix per capability this counter claims to have. A prefix missing
    # here means a router was written and never mounted, which is a whole
    # feature that exists in the tests and nowhere else.
    WANT = {
        "/shop": "catalogue", "/store": "storefront", "/orders": "orders",
        "/offers": "offers", "/stock": "stock", "/categories": "categories",
        "/customers": "customers", "/expenses": "expenses", "/purchases": "purchases",
        "/daybook": "day book", "/search": "search", "/assistant": "assistant",
        "/advisor": "advisor", "/expiry": "expiry", "/weighed": "by weight",
        "/shelf": "shelf", "/labels": "labels", "/loyalty": "loyalty",
        "/insights": "insights", "/po": "reorder", "/gst": "GST",
        "/share": "share", "/manage": "today and history", "/receipt": "receipts",
        "/auth": "sign in", "/cash": "cash drawer", "/counter": "the whole-counter read",
    }
    known = list(paths)
    missing = [w for p_, w in WANT.items()
               if not any(k == p_ or k.startswith(p_ + "/") for k in known)]
    if missing:
        bad(f"{len(WANT) - len(missing)}/{len(WANT)} capabilities are mounted",
            "not mounted: " + ", ".join(missing))
    else:
        say("PASS", f"all {len(WANT)} capabilities are mounted", f"{len(known)} routes in total")

    # Now actually call the ones that need no argument, so "mounted" is not
    # mistaken for "working".
    free = [k for k in known
            if "{" not in k and any(k == p_ or k.startswith(p_ + "/") for p_ in WANT)
            and "get" in (paths.get(k) or {})]
    dead = []
    for k in sorted(free):
        code, _ = get(f"{TILL}{k}", timeout=10.0)
        # 4xx is a legitimate answer from a mounted route that wants arguments
        # or a signed-in user; 0 and 5xx are not.
        if code == 0 or code >= 500:
            dead.append(f"{k} -> {code or 'no answer'}")
    if dead:
        bad(f"{len(free) - len(dead)}/{len(free)} argument-free routes answer", "\n".join(dead))
    else:
        say("PASS", f"all {len(free)} argument-free routes answer without a server error")


def main() -> int:
    print(f"\n{B}GAWAAH preflight{X}  {D}is this counter ready to be demonstrated right now?{X}")
    check_till()
    check_catalogue()
    check_the_shop_agrees_with_itself()
    before = check_paisa()
    check_tunnel(before)
    check_routers()

    print()
    if FAILED:
        print(f"  {R}{B}NOT READY{X} -- {len(FAILED)} thing(s) would break a demo:")
        for f in FAILED:
            print(f"    {R}x{X} {f}")
        if WARNED:
            print(f"  {Y}and {len(WARNED)} worth a look:{X} " + "; ".join(WARNED))
        print()
        return 1
    if WARNED:
        print(f"  {Y}{B}READY, with {len(WARNED)} caveat(s){X}")
        for w in WARNED:
            print(f"    {Y}!{X} {w}")
        print()
        return 0
    print(f"  {G}{B}READY{X} -- every path a rupee travels was exercised, not assumed.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
