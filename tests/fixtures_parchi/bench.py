#!/usr/bin/env python3
"""The PARCHI bench: five printed bills through the REAL model, scored.

Run from the repo root with the same variables `make serve` exports:

    set -a; for v in GOOGLE_API_KEY GAWAAH_LLM_BASE_URL GAWAAH_LLM_MODEL; do
      export $v="$(grep -E "^$v=" .env | head -1 | cut -d= -f2-)"; done; set +a
    .venv/bin/python tests/fixtures_parchi/bench.py

It builds a scratch shop holding the seeded catalogue (never `results/`),
sends each of the five PNG/JPEG bills to the provider through the very code
path the till uses (`parchi.parse_image`), and scores the model's reading
against `truth.json`:

    per line     name (exactly as printed, whitespace-normalised), qty, rate,
                 amount — each right or wrong, so a bill's score is honest
                 about WHICH field the model dropped
    per bill     supplier name, invoice number, date, subtotal, taxes, total
    the gate     did the arithmetic gate reach the verdict the bill deserves
                 (#3 must be REFUSED, naming line 2; the rest must pass)
    the match    did every printed line land on the sku it should, or on
                 nothing when it should land on nothing

and writes BENCH.md beside this file with the numbers. A run with no key
writes nothing and says so; the numbers in BENCH.md are always from a real
run, never from the fake transport.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

TRUTH = json.loads((HERE / "truth.json").read_text(encoding="utf-8"))


def _norm(s: object) -> str:
    return " ".join(str(s or "").split()).casefold()


def main() -> int:
    from gawaah import assistant, parchi, purchases
    from gawaah.money import from_rupees_str
    from tools import upload_app
    sys.path.insert(0, str(ROOT / "tests"))
    from test_parchi import CATALOGUE  # the seeded names, Devanagari and all

    if not assistant.api_key():
        print("no model key in the environment; the bench sends nothing and "
              "writes nothing. Export GOOGLE_API_KEY (or the xAI pair) first.")
        return 2

    scratch = Path(tempfile.mkdtemp(prefix="parchi-bench-"))
    os.environ["GAWAAH_SHOP_DIR"] = str(scratch / "shop")
    os.environ["GAWAAH_DATA_DIR"] = str(scratch / "data")
    upload_app.set_store_dir(scratch / "shop")
    for i, (sku, (name, price)) in enumerate(CATALOGUE.items()):
        upload_app.do_enrol_code_only(b"", sku, name, price, typed=f"89012345{i:05d}")

    rows = []
    for inv in TRUTH["invoices"]:
        raw = (HERE / inv["file"]).read_bytes()
        t0 = time.monotonic()
        try:
            doc = parchi.parse_image(raw)
            err = None
        except parchi.ParchiRefused as exc:
            doc, err = None, f"{exc.reason}: {exc.detail}"
        dt = time.monotonic() - t0
        rows.append(score(inv, doc, err, dt, from_rupees_str))
        print(f"#{inv['n']} {inv['file']}: {rows[-1]['summary']}  ({dt:.1f}s)")

    write_md(rows, parchi.model_name(), parchi.provider())
    print(f"BENCH.md written; scratch shop at {scratch}")
    return 0


def score(inv: dict, doc: dict | None, err: str | None, dt: float, from_rupees_str) -> dict:
    truth = inv["answer"]
    expect = inv["expect"]
    out = {"n": inv["n"], "file": inv["file"], "seconds": dt, "error": err,
           "lines_expected": len(truth["lines"]), "lines_read": 0,
           "name_ok": 0, "qty_ok": 0, "rate_ok": 0, "amount_ok": 0,
           "header": {}, "gate_expected": "refused" if not expect["gate_ok"] else "passed",
           "gate_got": None, "gate_named_line": None, "match_ok": 0,
           "match_expected": len(expect["skus"]), "refusal_reason": None}
    if doc is None:
        out["summary"] = f"REFUSED before a verdict: {err}"
        return out

    got = doc["lines"]
    out["lines_read"] = len(got)
    # Lines are compared in order; a dropped or invented line shifts the rest,
    # which is the honest outcome — the gate would catch it on the total.
    for i, tl in enumerate(truth["lines"]):
        if i >= len(got):
            break
        gl = got[i]
        out["name_ok"] += _norm(gl["name"]) == _norm(tl["name"])
        out["qty_ok"] += gl["qty"] == tl["qty"]
        out["rate_ok"] += gl["rate_paise"] == int(from_rupees_str(tl["rate"]))
        out["amount_ok"] += gl["amount_paise"] == int(from_rupees_str(tl["amount"]))
        want = expect["skus"][i]
        m = gl["match"]
        out["match_ok"] += (m["sku_id"] == want) if want else (m["status"] == "none")

    g = doc["gate"]
    out["header"] = {
        "supplier": _norm(doc["supplier"]["name"]) == _norm(truth["supplier"]["name"]),
        "phone": _norm(doc["supplier"]["phone"]) == _norm(truth["supplier"]["phone"]),
        "invoice_no": _norm(doc["invoice_no"]) == _norm(truth["invoice_no"]),
        "date": doc["date"] == truth["date"],
        "subtotal": g["subtotal_paise"] == expect["subtotal_paise"],
        "taxes": [t["amount_paise"] for t in g["taxes"]]
                 == [int(from_rupees_str(t["amount"])) for t in truth["taxes"]],
        "total": g["printed_total_paise"] == expect["total_paise"],
    }
    out["gate_got"] = "passed" if g["ok"] else "refused"
    out["gate_named_line"] = g["failing_lines"]
    out["refusal_reason"] = None if g["ok"] else g["detail"]
    n = len(truth["lines"])
    fields_ok = out["name_ok"] + out["qty_ok"] + out["rate_ok"] + out["amount_ok"]
    out["field_pct"] = (fields_ok * 100) // (4 * n)
    out["summary"] = (f"{out['lines_read']}/{n} lines; fields {fields_ok}/{4 * n}; "
                      f"header {sum(out['header'].values())}/7; match "
                      f"{out['match_ok']}/{n}; gate {out['gate_got']} "
                      f"(expected {out['gate_expected']}"
                      + (f", names {g['failing_lines']}" if not g["ok"] else "") + ")")
    return out


def write_md(rows: list[dict], model: str, provider: str) -> None:
    lines = [
        "# PARCHI bench — five printed bills through the real model",
        "",
        f"Model: `{model}` via `{provider}` · run on "
        f"{time.strftime('%Y-%m-%d %H:%M %Z')} · `tests/fixtures_parchi/bench.py`",
        "",
        "Each bill was generated by `make_invoices.py` from the seeded catalogue, "
        "sent through `gawaah.parchi.parse_image` (the till's own path), and the "
        "model's reading scored against `truth.json`. Four fields per line "
        "(name, qty, rate, amount); seven header facts (supplier, phone, invoice "
        "no, date, subtotal, taxes, total); the gate's verdict; and whether each "
        "line matched the product it should have, locally.",
        "",
        "| # | bill | lines read | name | qty | rate | amount | header | match | gate | seconds |",
        "|---|------|-----------:|-----:|----:|-----:|-------:|-------:|------:|------|--------:|",
    ]
    tot = {"n": 0, "name": 0, "qty": 0, "rate": 0, "amount": 0, "match": 0, "hdr": 0}
    for r in rows:
        n = r["lines_expected"]
        tot["n"] += n
        if r["error"]:
            lines.append(f"| {r['n']} | {r['file']} | — | — | — | — | — | — | — | "
                         f"refused: {r['error']} | {r['seconds']:.1f} |")
            continue
        hdr = sum(r["header"].values())
        tot["hdr"] += hdr
        for k in ("name", "qty", "rate", "amount", "match"):
            tot[k] += r[f"{k}_ok"]
        gate = r["gate_got"]
        if gate == r["gate_expected"]:
            gate += " ✓"
        else:
            gate += f" ✗ (expected {r['gate_expected']})"
        if r["gate_got"] == "refused":
            gate += f", line {[i + 1 for i in r['gate_named_line']]}"
        lines.append(
            f"| {r['n']} | {r['file']} | {r['lines_read']}/{n} | {r['name_ok']}/{n} | "
            f"{r['qty_ok']}/{n} | {r['rate_ok']}/{n} | {r['amount_ok']}/{n} | {hdr}/7 | "
            f"{r['match_ok']}/{n} | {gate} | {r['seconds']:.1f} |")
    N = tot["n"]
    lines += [
        f"| **all** | | | **{tot['name']}/{N}** | **{tot['qty']}/{N}** | "
        f"**{tot['rate']}/{N}** | **{tot['amount']}/{N}** | **{tot['hdr']}/{7 * len(rows)}** | "
        f"**{tot['match']}/{N}** | | |",
        "",
        "## Refusals, and why",
        "",
    ]
    any_ref = False
    for r in rows:
        if r["error"]:
            any_ref = True
            lines.append(f"- **#{r['n']}** refused before a verdict: `{r['error']}`")
        elif r["gate_got"] == "refused":
            any_ref = True
            lines.append(f"- **#{r['n']}** `parchi_arithmetic_refused` — {r['refusal_reason']}")
    if not any_ref:
        lines.append("- none")
    lines += [
        "",
        "## Reading the numbers",
        "",
        "- A wrong **name** costs a match, never a booking: the match is made on "
        "this machine and a person confirms or corrects it.",
        "- A wrong **qty / rate / amount** is caught by the gate unless the model "
        "mis-reads all three of a line consistently, which the total then catches.",
        "- **#3** is printed wrong on purpose (36 × 11.75 = 423.00, printed 423.01) "
        "and the correct outcome is a refusal naming line 2. A pass on #3 would be "
        "a bug in the gate or a model that 'corrected' the bill — both wrong.",
        "- Header facts are informational: a wrong date or invoice number is shown "
        "for a person to correct before ACCEPT and never changes a cost.",
        "",
        "Nothing here uses a Razorpay product; nothing here settles money.",
        "",
    ]
    (HERE / "BENCH.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
