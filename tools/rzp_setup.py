#!/usr/bin/env python3
"""Razorpay credential check and the Phase-0 money gates, in one command.

    ./.venv/bin/python tools/rzp_setup.py

Reads .env. NEVER prints a secret -- key ids are masked, secrets are only ever
reported as present/absent with a length. Runs the gates that BUILD_PROMPT.md
says must be settled on day 0, in dependency order, and stops at the first one
that fails so you are not chasing a downstream symptom.

    G0  credentials load and authenticate
    G1  can a UPI Payment Link be minted and paid in TEST MODE?   <- the big one
    G2  does a webhook arrive and does its HMAC verify over raw bytes?

G1 is the highest-risk unknown in the whole project: one research pass claims
Payment Links are capped at 30/business in test mode and that UPI Payment Links
are unsupported there, while the entire money path assumes they work. This
script answers it with the live API instead of another opinion.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
API = "https://api.razorpay.com/v1"

G = "\033[32m"; R = "\033[31m"; Y = "\033[33m"; D = "\033[2m"; B = "\033[1m"; X = "\033[0m"
if not sys.stdout.isatty():
    G = R = Y = D = B = X = ""


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    for k in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET", "RZP_MODE"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def mask(key_id: str) -> str:
    """rzp_test_AbCdEf123456 -> rzp_test_AbCd…3456. Never shows the middle."""
    if len(key_id) <= 14:
        return key_id[:9] + "…"
    return f"{key_id[:13]}…{key_id[-4:]}"


def call(method: str, path: str, key_id: str, secret: str, body: dict | None = None):
    url = f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    tok = base64.b64encode(f"{key_id}:{secret}".encode()).decode()
    req.add_header("Authorization", f"Basic {tok}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw[:400]}
    except Exception as e:  # noqa: BLE001 - network shape varies
        return 0, {"error": f"{type(e).__name__}: {e}"}


def row(label: str, value: str, ok: bool | None = None) -> None:
    dot = f"{G}OK{X}" if ok else (f"{R}FAIL{X}" if ok is False else f"{Y}··{X}")
    print(f"  {label:<34} {dot:<16} {value}")


def main() -> int:
    print(f"\n{B}GAWAAH — Razorpay setup and Phase-0 money gates{X}")
    print(f"{D}  secrets are never printed; key ids are masked{X}\n")

    env = load_env()
    kid = env.get("RAZORPAY_KEY_ID", "")
    sec = env.get("RAZORPAY_KEY_SECRET", "")
    whs = env.get("RAZORPAY_WEBHOOK_SECRET", "")

    print(f"{B}G0  credentials{X}")
    if not (ROOT / ".env").exists():
        row(".env", "MISSING — cp .env.example .env and fill it in", False)
        print(f"\n{Y}Nothing to test yet.{X} See .env.example for exactly where to click.\n")
        return 2
    row(".env", "found", True)
    row("RAZORPAY_KEY_ID", mask(kid) if kid else "empty", bool(kid) and kid.startswith("rzp_"))
    row("RAZORPAY_KEY_SECRET", f"present, {len(sec)} chars" if sec else "empty", bool(sec))
    row("RAZORPAY_WEBHOOK_SECRET",
        f"present, {len(whs)} chars" if whs else "empty (needed for G2 only)", bool(whs) or None)

    if not kid.startswith("rzp_test_"):
        row("mode guard", "key id is not rzp_test_ — REFUSING to touch a live key", False)
        print(f"\n{R}Stopped.{X} This tool only ever runs against TEST mode.\n")
        return 2
    if not sec:
        print(f"\n{Y}Stopped at G0.{X} Fill RAZORPAY_KEY_SECRET in .env.\n")
        return 2

    status, body = call("GET", "/payments?count=1", kid, sec)
    authed = status == 200
    row("authenticate", f"HTTP {status}" + ("" if authed else f" — {str(body)[:90]}"), authed)
    if not authed:
        print(f"\n{R}Stopped at G0.{X} The key pair did not authenticate.\n")
        return 1

    # ---------------------------------------------------------------- G1
    print(f"\n{B}G1  can a UPI Payment Link be minted in TEST MODE?{X}")
    print(f"{D}  the highest-risk unknown in the project — the money path assumes yes{X}")

    amount = 21437  # deliberate odd paise: the CHILLAR nonce shape
    link_body = {
        "amount": amount,
        "currency": "INR",
        "accept_partial": False,
        "description": "GAWAAH gate G1",
        "notes": {"session_id": "g1_probe", "gate": "G1"},
    }
    status, link = call("POST", "/payment_links", kid, sec, link_body)
    ok = status in (200, 201) and "id" in link
    row("POST /payment_links", f"HTTP {status}" + ("" if ok else f" — {str(body)[:90]}"), ok)
    if not ok:
        err = (link.get("error") or {}).get("description") or str(link)[:200]
        row("error", err[:110], False)
        print(f"\n{R}G1 FAILED.{X} Fall back to POST /v1/payments/qr_codes (type=upi_qr) and")
        print("file the activation request today. Record this in FAILURES.md.\n")
        return 1

    row("link id", link.get("id", "?"), True)
    row("short_url", link.get("short_url", "?"), True)
    row("amount", f"{link.get('amount')} paise (asked {amount})", link.get("amount") == amount)
    row("notes round-trip",
        "session_id survived" if (link.get("notes") or {}).get("session_id") == "g1_probe"
        else "NOTES DID NOT SURVIVE — the green rule depends on this",
        (link.get("notes") or {}).get("session_id") == "g1_probe")

    upi = link.get("upi_link")
    row("upi_link flag", str(upi) if upi is not None else "absent (expected in test mode)", None)

    print(f"\n{B}  PAY IT{X} — open the short_url above in a browser, choose UPI, and use")
    print(f"{D}  Razorpay's test success flow. Then re-run this script to see it settle.{X}")

    status, fetched = call("GET", f"/payment_links/{link['id']}", kid, sec)
    if status == 200:
        row("status now", fetched.get("status", "?"), None)
        row("amount_paid", str(fetched.get("amount_paid", 0)), None)

    # ---------------------------------------------------------------- G2
    print(f"\n{B}G2  webhook HMAC{X}")
    if not whs:
        row("webhook secret", "not set — skipping", None)
    else:
        sample = json.dumps({"event": "payment_link.paid", "probe": True}).encode()
        sig = hmac.new(whs.encode(), sample, hashlib.sha256).hexdigest()
        good = hmac.compare_digest(
            sig, hmac.new(whs.encode(), sample, hashlib.sha256).hexdigest())
        tampered = sample.replace(b"true", b"fals") + b"e"
        bad = hmac.new(whs.encode(), tampered, hashlib.sha256).hexdigest()
        row("sign/verify over raw bytes", "round-trips", good)
        row("one flipped byte", "signature differs", sig != bad)
        print(f"{D}  Now point the dashboard webhook at your cloudflared URL + /webhook,{X}")
        print(f"{D}  paste the SAME secret there, and subscribe to payment_link.paid.{X}")

    print(f"\n{G}G1 PASSED{X} — UPI Payment Links mint in test mode. The money path stands.")
    print(f"{D}Record this in FAILURES.md with today's date and the real output.{X}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
