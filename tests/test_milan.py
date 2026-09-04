"""MILAN — the day close matched against the gateway's own settlement report.

What this suite pins, in the order a demo could fake each one:

  1. A MATCH IS BY PAYMENT ID AND EXACT PAISE. A bill the chain settled on a
     signed webhook is matched to the gateway's row by the payment id the
     kernel wrote, and by nothing softer. The totals are integer sums of the
     gateway's own figures; net is the gateway's credit, not gross minus fee.
  2. EVERY EXCEPTION CLASS IS PRODUCED DELIBERATELY AND NAMED: the T+1 gap,
     the found money, the mismatch, the refund, the adjustment, the
     unreadable row, the settled bill the report should carry and does not.
  3. THE FOUND MONEY SETTLES THROUGH THE KERNEL'S EXISTING RECONCILE PATH —
     a read-only lookup — and the second press does nothing. A NEW or BOOKED
     row is refused by name; nothing is minted and nothing is charged.
  4. THE SIMULATOR'S REPORT IS DERIVED, NOT TYPED: row for row it equals the
     simulator's own payments collection, it is byte-identical across two
     seeds-alike runs, and a payment captured today is in nobody's report.
  5. MILAN IS READ-ONLY BY CONSTRUCTION: its imports and paisa's recon routes
     are grepped for every gateway write method.
  6. INTEGERS EVERYWHERE, NOTHING IN results/, and the frozen day-close
     figures are not touched by the block that sits beside them.
  7. THE VOICE: "kal bank mein kitna aaya" routes to the one tool and speaks
     the figures with the exceptions in the same breath.
"""
from __future__ import annotations

import ast
import datetime as _dt
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from gawaah import assistant, daybook, manage, milan  # noqa: E402
from gawaah.clock import VirtualClock  # noqa: E402
from gawaah.kernel import (  # noqa: E402
    BOOKED, CALLING, INDETERMINATE, NEW, SETTLED, Kernel,
)
from gawaah.ledger import Ledger, verify  # noqa: E402
from gawaah.milan import (  # noqa: E402
    EXCEPTION_CLASSES, X_ADJUSTMENTS, X_FOUND, X_MISMATCH, X_NOT_IN_RECON,
    X_NOT_YET, X_REFUNDS, X_UNREADABLE,
)
from gawaah.paisa import (  # noqa: E402
    DictPriceBook, PaisaConfig, PaisaService, create_app,
)
from gawaah.rzp_sim import (  # noqa: E402
    SETTLEMENT_T_PLUS_DAYS, SIM_BODY_MARKER, RazorpaySim, RazorpaySimError,
)
from tools import upload_app  # noqa: E402

ROOT = Path(REPO)
SECRET = "whsec_milan_test_only"
# Deliberately not round: a bug that divides or rounds shows up in the paise.
PRICES = {"parle_g": 10037, "maggi": 14950, "soap": 40113}
BILL = sum(PRICES.values())          # 65100
DAY_S = 86400
#: A fixed morning, IST, so "yesterday" and "tomorrow" are the same days on
#: the gateway's calendar and the counter's.
T0 = "2026-09-03T10:00:00+05:30"


# ------------------------------------------------------------------ rigging


@pytest.fixture()
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A whole counter in a temp dir: money service, kernel, simulator, and
    the till's milan/daybook/assistant routers wired to the same paisa
    through its TestClient. Never results/: both env vars and the till's
    own handle are redirected, and the real chain's size is checked after."""
    data = tmp_path / "data"
    shop = data / "shop"
    (data / "scans").mkdir(parents=True)
    shop.mkdir(parents=True)
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(data))
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(shop))
    upload_app.set_store_dir(shop)
    manage._CHAIN_CACHE.clear()

    clock = VirtualClock(start=T0, step_ms=100)
    ledger = Ledger(data / "audit.jsonl")
    kernel = Kernel(str(data / "kernel.db"), clock, ledger)
    cfg = PaisaConfig(mode="sim", key_id="rzp_test_MILAN", key_secret="k",
                      webhook_secret=SECRET, seed=7)
    sim = RazorpaySim(webhook_secret=SECRET, clock=clock, seed=7)
    svc = PaisaService(clock=clock, ledger=ledger, kernel=kernel, gateway=sim,
                       config=cfg, price_book=DictPriceBook(PRICES), data_dir=str(data))
    paisa = TestClient(create_app(svc))

    def _post(path: str, body: dict, timeout_s: int = 30):
        r = paisa.post(path, json=body)
        return r.status_code, r.json()

    def _get(path: str):
        r = paisa.get(path)
        return r.status_code, r.json()

    monkeypatch.setattr(milan, "_paisa_post", _post)
    monkeypatch.setattr(milan, "_paisa_get", _get)
    monkeypatch.setattr(manage, "paisa_get", _get)

    app = FastAPI()
    app.include_router(milan.router)
    app.include_router(daybook.router)
    app.include_router(assistant.router)
    till = TestClient(app)

    class Rig:
        pass

    r = Rig()
    r.data, r.shop = data, shop
    r.clock, r.ledger, r.kernel, r.sim, r.svc = clock, ledger, kernel, sim, svc
    r.paisa, r.till = paisa, till
    r.n = 0
    yield r
    manage._CHAIN_CACHE.clear()


def witness(rig, scan_id: str, skus=("parle_g", "maggi", "soap")) -> str:
    doc = {
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "lines": [{"code": f"gawaah:{s}", "sku_id": s} for s in skus],
        "codes_found": len(skus),
    }
    (rig.data / "scans" / f"{scan_id}.json").write_text(json.dumps(doc), encoding="utf-8")
    return scan_id


#: Every non-empty basket of the three products, so forty bills carry seven
#: different totals without any of them being a figure the till made up:
#: paisa re-prices the witness from its own book and refuses a mismatch.
BASKETS = (("parle_g", "maggi", "soap"), ("parle_g",), ("maggi",), ("soap",),
           ("parle_g", "maggi"), ("maggi", "soap"), ("parle_g", "soap"))


def basket_total(skus) -> int:
    return sum(PRICES[s] for s in skus)


def mint(rig, session_id: str | None = None, skus=BASKETS[0]) -> dict:
    """One bill, minted the way the till mints one: a scan witness, re-derived
    by paisa, one kernel row, one link."""
    rig.n += 1
    sid = session_id or f"till_m{rig.n}"
    r = rig.paisa.post("/intent", json={"session_id": sid, "amount_paise": basket_total(skus),
                                        "scan": {"scan_id": witness(rig, f"scan_{sid}", skus)}})
    assert r.status_code == 200, r.json()
    return r.json()


def pay(rig, minted: dict, *, deliver: bool = True) -> Any:
    """The customer pays. With `deliver=False` the signed webhook is produced
    and never posted — the dead-tunnel case."""
    result = rig.sim.pay_link(minted["payment_link_id"])
    if deliver:
        for d in result.deliveries:
            rr = rig.paisa.post("/webhook", content=d.body, headers=dict(d.headers))
            assert rr.status_code == 200 and rr.json()["green"] is True, rr.json()
    return result


def paid_bill(rig, **kw) -> tuple[dict, Any]:
    m = mint(rig, **kw)
    return m, pay(rig, m)


def next_day(rig, days: int = 1) -> None:
    """Move the shared clock forward by whole days, so the T+1 batch is due."""
    # VirtualClock has no advance; it is a test double, and its start is
    # the only thing it holds, so it is moved directly.
    rig.clock._t += _dt.timedelta(days=days)
    manage._CHAIN_CACHE.clear()


def day_of(rig, offset_days: int = 0) -> str:
    now = _dt.datetime.fromisoformat(rig.clock.now_iso())
    return (now + _dt.timedelta(days=offset_days)).date().isoformat()


def match(rig, day: str) -> dict:
    r = rig.till.get(f"/milan?day={day}")
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["ok"] is True, body
    walk_ints(body)
    return body


def walk_ints(obj: Any, path: str = "") -> None:
    """Every number in a response is an int. A float anywhere is a money bug."""
    if isinstance(obj, bool):
        return
    if isinstance(obj, float):
        raise AssertionError(f"float at {path}: {obj!r}")
    if isinstance(obj, dict):
        for k, v in obj.items():
            walk_ints(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk_ints(v, f"{path}[{i}]")


@pytest.fixture(autouse=True)
def _results_untouched():
    """The live chain has ONE writer and it is not this suite."""
    live = ROOT / "results" / "audit.jsonl"
    before = live.stat().st_size if live.exists() else None
    yield
    after = live.stat().st_size if live.exists() else None
    assert before == after, "a milan test wrote results/audit.jsonl"


# ==========================================================================
# 1. the match: by payment id, exact paise, integer sums of the gateway's rows
# ==========================================================================


def test_a_settled_bill_matches_its_row_by_payment_id_with_the_gateways_fees(rig):
    minted, paid = paid_bill(rig)
    next_day(rig)                                   # the T+1 batch has gone out
    body = match(rig, day_of(rig))
    m = body["matched"]
    assert m["count"] == 1 and body["exception_count"] == 0, body
    row = m["rows"][0]
    assert row["entity_id"] == paid.payment["id"]
    assert row["session_id"] == minted["session_id"]
    assert row["settled_by"] == "webhook"
    # The gateway's own figures, summed, and net is its credit.
    pay_doc = rig.sim.fetch_payments(payment_id=paid.payment["id"])["items"][0]
    assert m["gross_paise"] == BILL == pay_doc["amount"]
    assert m["fee_paise"] == pay_doc["fee"] and m["tax_paise"] == pay_doc["tax"]
    assert m["net_paise"] == BILL - pay_doc["fee"] - pay_doc["tax"]
    assert m["deducted_paise"] == m["gross_paise"] - m["net_paise"]
    assert m["by_webhook"] == 1 and m["by_kernel"] == 0
    assert body["simulated"] is True and row["simulated"] is True
    assert body["settlement_cycle"] == "T+1"
    assert "Rs" in body["value_line"] and "1 bill matched" in body["value_line"]


def test_forty_bills_sum_to_the_gateways_batch_exactly(rig):
    """The demo's figure: forty bills, one batch, every paisa accounted for."""
    ids = []
    for i in range(40):
        _m, paid = paid_bill(rig, skus=BASKETS[i % len(BASKETS)])
        ids.append(paid.payment["id"])
    next_day(rig)
    body = match(rig, day_of(rig))
    m = body["matched"]
    assert m["count"] == 40 and body["exception_count"] == 0
    batch = rig.sim.fetch_settlements()["items"]
    assert len(batch) == 1
    assert m["net_paise"] == batch[0]["amount"]
    assert m["fee_paise"] == batch[0]["fees"] and m["tax_paise"] == batch[0]["tax"]
    assert m["gross_paise"] == sum(basket_total(BASKETS[i % len(BASKETS)]) for i in range(40))
    assert sorted(r["entity_id"] for r in m["rows"]) == sorted(ids)
    assert "40 bills matched" in body["value_line"]


def test_the_report_for_today_is_empty_until_tomorrow(rig):
    """T+1, on screen and in the figures: a bill paid today is in nobody's
    report yet, and is named as still with the gateway."""
    paid_bill(rig)
    body = match(rig, day_of(rig))
    assert body["matched"]["count"] == 0
    ny = body["exceptions"][X_NOT_YET]
    assert ny["count"] == 1 and ny["paise"] == BILL
    assert ny["rows"][0]["due_day"] == day_of(rig, 1)
    assert "still with Razorpay" in body["value_line"]
    # ...and yesterday's report — the default — is empty of everything.
    yesterday = match(rig, day_of(rig, -1))
    assert yesterday["matched"]["count"] == 0
    assert yesterday["exceptions"][X_NOT_YET]["count"] == 1


def test_the_default_day_is_yesterday(rig, monkeypatch):
    r = rig.till.get("/milan")
    body = r.json()
    assert body["ok"] is True
    today = _dt.datetime.now().astimezone().date()
    assert body["day"] == (today - _dt.timedelta(days=1)).isoformat()


def test_a_bad_day_is_refused_by_name(rig):
    r = rig.till.get("/milan?day=yesterday-ish")
    assert r.status_code == 400 and r.json()["reason"] == milan.R_BAD_DAY


# ==========================================================================
# 2. the found money: in the report, not on the chain, and the settle press
# ==========================================================================


def test_a_payment_the_tunnel_lost_is_found_and_settled_from_the_gateways_record(rig):
    """The dead-tunnel story, end to end. The customer paid; the webhook never
    landed; the chain shows a minted bill and no settlement; the gateway's
    report names the payment and the nonce. One press runs kernel.reconcile,
    which looks the link up and settles the intent for exactly its amount."""
    minted = mint(rig)
    paid = pay(rig, minted, deliver=False)
    assert rig.kernel.get(minted["nonce"]).state == CALLING
    next_day(rig)

    body = match(rig, day_of(rig))
    assert body["matched"]["count"] == 0
    found = body["exceptions"][X_FOUND]
    assert found["count"] == 1 and found["paise"] == BILL
    row = found["rows"][0]
    assert row["entity_id"] == paid.payment["id"]
    assert row["nonce"] == minted["nonce"]
    assert row["session_id"] == minted["session_id"]
    assert row["counter_state"] == CALLING
    assert row["bill_on_chain"] is True and row["bill_settled_on_chain"] is False
    assert row["settleable"] is True and row["needs_human"] is False
    assert row["settled_at"] is not None
    assert "no bill on this counter settled" in body["value_line"]

    # THE PRESS. Through the till, to paisa, to the kernel's reconcile path.
    r = rig.till.post("/milan/settle", json={"nonce": minted["nonce"]})
    assert r.status_code == 200, r.json()
    ans = r.json()
    assert ans["settled"] is True and ans["state"] == SETTLED
    assert ans["state_before"] == CALLING
    assert ans["payment_id"] == paid.payment["id"]
    assert ans["reason"] == "reconciled:captured"
    assert ans["minted"] is False and ans["charged"] is False
    assert ans["audited"] is True
    it = rig.kernel.get(minted["nonce"])
    assert it.state == SETTLED and it.payment_id == paid.payment["id"]
    assert it.needs_human is False

    # And now it matches — labelled as settled by the kernel, not a webhook.
    manage._CHAIN_CACHE.clear()
    again = match(rig, day_of(rig))
    assert again["exceptions"][X_FOUND]["count"] == 0
    assert again["matched"]["count"] == 1
    assert again["matched"]["rows"][0]["settled_by"] == "kernel"
    assert again["matched"]["by_kernel"] == 1 and again["matched"]["by_webhook"] == 0

    # The chains verify: the money chain carries the kernel's own lines for
    # the retrieve and the settlement, milan's carries the press.
    ok, _n, _h, err = verify(rig.data / "audit.jsonl")
    assert ok, err
    lines = [json.loads(l) for l in (rig.data / "audit.jsonl").read_text().splitlines()]
    events = [l["event"] for l in lines if l.get("nonce") == minted["nonce"]]
    assert "intent.indeterminate" in events and "intent.retrieve" in events
    assert "intent.settled" in events and "recon.settle" in events
    assert "intent.minted" in events and events.count("intent.minted") == 1
    ok2, _n2, _h2, err2 = verify(milan.audit_path())
    assert ok2, err2
    mine = [json.loads(l) for l in milan.audit_path().read_text().splitlines()]
    assert mine[-1]["event"] == "settle.pressed" and mine[-1]["settled"] is True
    assert mine[-1]["minted"] is False and mine[-1]["charged"] is False


def test_a_second_press_does_nothing_and_charges_nothing(rig):
    minted = mint(rig)
    pay(rig, minted, deliver=False)
    next_day(rig)
    first = rig.till.post("/milan/settle", json={"nonce": minted["nonce"]}).json()
    assert first["settled"] is True and first["changed"] is True
    links_before = len(rig.sim.fetch_payment_links()["items"])
    payments_before = rig.sim.fetch_payments()["count"]
    second = rig.till.post("/milan/settle", json={"nonce": minted["nonce"]}).json()
    assert second["ok"] is True and second["settled"] is True
    assert second["changed"] is False and second["state_before"] == SETTLED
    assert second["payment_id"] == first["payment_id"]
    # The gateway was not asked to do anything: no link, no payment appeared.
    assert len(rig.sim.fetch_payment_links()["items"]) == links_before
    assert rig.sim.fetch_payments()["count"] == payments_before


def test_a_settle_for_a_link_nobody_paid_fails_the_intent_rather_than_settling_it(rig):
    """The reconcile path is a lookup, so it can only report what the gateway
    holds. An unpaid link stays unpaid: the row goes INDETERMINATE (the
    gateway says `created`) and nothing turns green."""
    minted = mint(rig)
    r = rig.till.post("/milan/settle", json={"nonce": minted["nonce"]})
    assert r.status_code == 200, r.json()
    ans = r.json()
    assert ans["settled"] is False and ans["state"] == INDETERMINATE
    assert ans["reason"] == "gateway_pending:created"
    assert ans["payment_id"] is None


def test_a_new_or_booked_intent_is_refused_by_name_and_nothing_is_looked_up(rig):
    # NEW: a row the gateway was never asked about.
    it = rig.kernel.create_intent("till_new", BILL)
    assert it.state == NEW
    r = rig.till.post("/milan/settle", json={"nonce": it.nonce})
    assert r.status_code == 409 and r.json()["paisa_reason"] == "nothing_to_settle"
    assert rig.kernel.get(it.nonce).state == NEW
    # BOOKED: a debt on the khata, collected elsewhere.
    it2 = rig.kernel.create_intent("till_booked", BILL)
    rig.kernel.mark_booked(it2.nonce, "bk_" + "c0ffee" * 3)
    r = rig.till.post("/milan/settle", json={"nonce": it2.nonce})
    assert r.status_code == 409 and r.json()["paisa_reason"] == "not_reconcilable"
    assert rig.kernel.get(it2.nonce).state == BOOKED
    # Unknown: refused, nothing minted.
    r = rig.till.post("/milan/settle", json={"nonce": "gwn_" + "0" * 32})
    assert r.status_code == 409 and r.json()["paisa_reason"] == "unknown_nonce"
    assert rig.sim.fetch_payment_links()["count"] == 0


@pytest.mark.parametrize("body,reason", [
    ({}, milan.R_NO_NONCE),
    ({"nonce": ""}, milan.R_NO_NONCE),
    ({"nonce": "../../etc"}, milan.R_BAD_NONCE),
    ({"nonce": 12}, milan.R_BAD_NONCE),
])
def test_a_malformed_settle_body_is_refused_before_paisa_is_asked(rig, body, reason):
    r = rig.till.post("/milan/settle", json=body)
    assert r.status_code == 400 and r.json()["reason"] == reason


def test_a_row_naming_nothing_this_counter_minted_is_reported_not_settleable(rig):
    """A payment on a link some other client of the same account minted: in
    the report, unknown to this kernel. Named, no button."""
    link = rig.sim.create_payment_link(4200, {"session_id": "elsewhere"}, reference_id="ref_x")
    rig.sim.pay_link(link["id"])
    next_day(rig)
    body = match(rig, day_of(rig))
    found = body["exceptions"][X_FOUND]
    assert found["count"] == 1
    row = found["rows"][0]
    assert row["settleable"] is False and row["needs_human"] is True
    assert row["counter_state"] is None and row["bill_on_chain"] is False
    assert row["nonce"] is None


# ==========================================================================
# 3. every other exception class, produced deliberately on the pure matcher
# ==========================================================================


def _chain_with_one_settled_bill(rig):
    _minted, paid = paid_bill(rig)
    next_day(rig)
    records, _chain = manage.read_chain()
    bills = manage.bills_from(records)
    recon = rig.paisa.get(f"/recon?day={day_of(rig)}").json()
    assert recon["count"] == 1
    return records, bills, recon["rows"], paid


def _tz(rig):
    return _dt.datetime.fromisoformat(rig.clock.now_iso()).tzinfo


def test_an_amount_that_disagrees_is_parked_by_name_not_corrected(rig):
    records, bills, rows, paid = _chain_with_one_settled_bill(rig)
    off = dict(rows[0])
    off["amount"] = off["amount"] + 1                 # one paisa
    out = milan.match(bills, records, [off], day=_dt.date.fromisoformat(day_of(rig)), tz=_tz(rig))
    assert out["matched"]["count"] == 0
    mm = out["exceptions"][X_MISMATCH]
    assert mm["count"] == 1
    assert mm["rows"][0]["needs_human"] is True
    assert mm["rows"][0]["difference_paise"] == 1
    assert mm["rows"][0]["bill_paise"] == BILL and mm["rows"][0]["amount_paise"] == BILL + 1
    # The bill is not ALSO listed as missing from the report: it was seen.
    assert out["exceptions"][X_NOT_IN_RECON]["count"] == 0
    assert "do not agree" in milan.value_line(day_of(rig), out) or "does not agree" in milan.value_line(day_of(rig), out)


def test_a_settled_bill_whose_day_has_come_and_is_not_in_the_report_is_named(rig):
    records, bills, _rows, _paid = _chain_with_one_settled_bill(rig)
    out = milan.match(bills, records, [], day=_dt.date.fromisoformat(day_of(rig)), tz=_tz(rig))
    missing = out["exceptions"][X_NOT_IN_RECON]
    assert missing["count"] == 1 and missing["paise"] == BILL
    assert missing["rows"][0]["needs_human"] is True
    assert out["exceptions"][X_NOT_YET]["count"] == 0
    assert "missing from the report" in milan.value_line(day_of(rig), out)


def test_a_bill_settled_before_this_days_window_is_counted_as_an_earlier_day(rig):
    records, bills, _rows, _paid = _chain_with_one_settled_bill(rig)
    later = _dt.date.fromisoformat(day_of(rig)) + _dt.timedelta(days=3)
    out = milan.match(bills, records, [], day=later, tz=_tz(rig))
    assert out["earlier_days"] == {"count": 1, "paise": BILL, "rupees": "651.00"}
    assert out["exception_count"] == 0


def test_refunds_and_adjustments_are_listed_under_their_own_names(rig):
    records, bills, rows, paid = _chain_with_one_settled_bill(rig)
    refund = {**rows[0], "entity_id": "rfnd_test000000001", "type": "refund",
              "debit": 2500, "credit": 0, "amount": 2500, "fee": 0, "tax": 0,
              "payment_id": paid.payment["id"]}
    adjustment = {**rows[0], "entity_id": "adj_test00000001", "type": "adjustment",
                  "debit": 0, "credit": 300, "amount": 300, "fee": 0, "tax": 0,
                  "notes": {}, "counter_intent": None}
    out = milan.match(bills, records, [rows[0], refund, adjustment],
                      day=_dt.date.fromisoformat(day_of(rig)), tz=_tz(rig))
    assert out["matched"]["count"] == 1
    rf = out["exceptions"][X_REFUNDS]
    assert rf["count"] == 1 and rf["paise"] == 2500
    assert rf["rows"][0]["payment_id"] == paid.payment["id"]
    assert rf["rows"][0]["bill_session_id"] == bills[rows[0]["notes"]["session_id"]]["session_id"]
    adj = out["exceptions"][X_ADJUSTMENTS]
    assert adj["count"] == 1 and adj["paise"] == 300
    # Neither touched the matched totals: nothing is netted.
    assert out["matched"]["net_paise"] == rows[0]["credit"]
    assert "1 refund, Rs 25.00 back" in milan.value_line(day_of(rig), out)


def test_a_row_that_is_not_whole_paise_is_abstained_on_never_coerced(rig):
    records, bills, rows, _paid = _chain_with_one_settled_bill(rig)
    bad = {**rows[0], "amount": "65100.0"}
    out = milan.match(bills, records, [bad], day=_dt.date.fromisoformat(day_of(rig)), tz=_tz(rig))
    assert out["matched"]["count"] == 0
    assert out["exceptions"][X_UNREADABLE]["count"] == 1
    assert out["exceptions"][X_UNREADABLE]["rows"][0]["raw"]["amount"] == "65100.0"
    walk_ints(out)


def test_every_exception_class_is_in_the_response_even_when_empty(rig):
    body = match(rig, day_of(rig))
    assert set(body["exceptions"]) == set(EXCEPTION_CLASSES)
    for name in EXCEPTION_CLASSES:
        b = body["exceptions"][name]
        assert b["count"] == 0 and b["rows"] == []


# ==========================================================================
# 4. the simulator's report is derived from its payments, never typed
# ==========================================================================


def test_the_sim_report_equals_its_own_payments_row_for_row(rig):
    for i in range(5):
        paid_bill(rig, skus=BASKETS[i])
    next_day(rig)
    y, m, d = (int(x) for x in day_of(rig).split("-"))
    report = rig.sim.settlements_recon(year=y, month=m, day=d)
    payments = {p["id"]: p for p in rig.sim.fetch_payments()["items"]}
    assert report["count"] == 5 == len(payments)
    for row in report["items"]:
        p = payments[row["entity_id"]]
        assert row["type"] == "payment"
        assert row["amount"] == p["amount"] and row["fee"] == p["fee"] and row["tax"] == p["tax"]
        assert row["credit"] == p["amount"] - p["fee"] - p["tax"] and row["debit"] == 0
        assert row["notes"] == p["notes"]
        assert row[SIM_BODY_MARKER] is True
        assert row["settled"] is True and row["settlement_id"].startswith("setl_")
        for key in ("amount", "fee", "tax", "credit", "debit", "settled_at", "created_at"):
            assert isinstance(row[key], int) and not isinstance(row[key], bool)
    # One batch for the day, its amount the sum of the credits.
    batches = rig.sim.fetch_settlements()["items"]
    assert len(batches) == 1
    assert batches[0]["amount"] == sum(r["credit"] for r in report["items"])
    assert rig.sim.fetch_settlement(batches[0]["id"]) == batches[0]
    with pytest.raises(RazorpaySimError):
        rig.sim.fetch_settlement("setl_nothere")


def test_the_sim_report_is_deterministic_across_two_runs():
    def run():
        clock = VirtualClock(start=T0, step_ms=100)
        sim = RazorpaySim(webhook_secret=SECRET, clock=clock, seed=3)
        for i in range(3):
            link = sim.create_payment_link(1000 + i, {"session_id": f"s{i}"},
                                           reference_id=f"r{i}")
            sim.pay_link(link["id"])
        clock._t += _dt.timedelta(days=1)
        now = _dt.datetime.fromisoformat(clock.now_iso())
        return json.dumps(sim.settlements_recon(year=now.year, month=now.month,
                                                day=now.day), sort_keys=True)
    assert run() == run()


def test_a_payment_captured_today_is_in_nobodys_report_until_t_plus_one(rig):
    _m, paid = paid_bill(rig)
    y, m, d = (int(x) for x in day_of(rig).split("-"))
    assert rig.sim.settlements_recon(year=y, month=m, day=d)["count"] == 0
    y2, m2, d2 = (int(x) for x in day_of(rig, SETTLEMENT_T_PLUS_DAYS).split("-"))
    # Asked for tomorrow's report TODAY: not gone out yet, so still empty.
    assert rig.sim.settlements_recon(year=y2, month=m2, day=d2)["count"] == 0
    next_day(rig)
    rows = rig.sim.settlements_recon(year=y2, month=m2, day=d2)["items"]
    assert [r["entity_id"] for r in rows] == [paid.payment["id"]]


def test_the_on_demand_batch_moves_the_day_and_never_an_amount(rig):
    _m, paid = paid_bill(rig)
    p = rig.sim.fetch_payments(payment_id=paid.payment["id"])["items"][0]
    r = rig.till.post("/milan/sim/settle")
    assert r.status_code == 200, r.json()
    ans = r.json()
    assert ans["payments"] == 1 and ans["simulated"] is True
    assert ans["amount_settled"] == p["amount"] - p["fee"] - p["tax"]
    body = match(rig, day_of(rig))
    assert body["matched"]["count"] == 1 and body["exceptions"][X_NOT_YET]["count"] == 0
    assert body["matched"]["rows"][0]["amount_paise"] == p["amount"]
    # A second sweep finds nothing to sweep and says so.
    again = rig.till.post("/milan/sim/settle").json()
    assert again["payments"] == 0 and again["amount_settled"] == 0
    # Tomorrow's scheduled batch does not carry it a second time.
    next_day(rig)
    assert rig.paisa.get(f"/recon?day={day_of(rig)}").json()["count"] == 0


def test_sim_settle_is_refused_by_name_on_the_live_gateway(tmp_path):
    clock = VirtualClock(start=T0, step_ms=100)
    ledger = Ledger(tmp_path / "audit.jsonl")
    kernel = Kernel(str(tmp_path / "kernel.db"), clock, ledger)
    cfg = PaisaConfig(mode="live", key_id="rzp_test_X", key_secret="s",
                      webhook_secret=SECRET, seed=1)

    class NoGateway:
        def create_payment_link(self, *a, **k):
            raise AssertionError("never called")

    svc = PaisaService(clock=clock, ledger=ledger, kernel=kernel, gateway=NoGateway(),
                       config=cfg, price_book=DictPriceBook(PRICES), data_dir=str(tmp_path))
    c = TestClient(create_app(svc))
    r = c.post("/sim/settle")
    assert r.status_code == 409 and r.json()["error"] == "not_a_simulator"
    r = c.get("/recon?day=2026-09-03")
    assert r.status_code == 409 and r.json()["error"] == "recon_unavailable"


def test_the_sim_and_rzp_live_settlement_code_is_float_free():
    spec = importlib.util.spec_from_file_location(
        "lint_no_float", ROOT / "tools" / "lint_no_float.py")
    lint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lint)
    for name in ("rzp_sim.py", "milan.py"):
        target = ROOT / "gawaah" / name
        v = lint.V(str(target))
        v.visit(ast.parse(target.read_text(encoding="utf-8")))
        assert v.bad == [], f"float in {name}: {v.bad}"


# ==========================================================================
# 5. read-only by construction
# ==========================================================================

#: Every method on either gateway adapter that can move money or mint a
#: payable thing. milan.py may name none of them; paisa's recon routes may
#: call none of them.
GATEWAY_WRITES = (
    "create_payment_link", "pay_link", "cancel_payment_link", "refund",
    "create_refund", "create_ondemand_settlement", "mark_settled",
    "mark_failed", "resolve_escalated", "record_capture", "create_collection",
    "sign_body",
)


def _imports_of(tree: ast.AST) -> set[str]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            out.add(mod)
            out.update(f"{mod}.{a.name}" for a in node.names)
    return out


def _attr_names(tree: ast.AST) -> set[str]:
    return {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}


def test_milan_is_read_only_by_construction():
    src = (ROOT / "gawaah" / "milan.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = _imports_of(tree)
    for banned in ("rzp_sim", "rzp_live", "kernel", "paisa", "webhook", "hmac"):
        assert not any(banned in i for i in imported), f"milan imports {banned}: {imported}"
    names = _attr_names(tree)
    for w in GATEWAY_WRITES:
        assert w not in names, f"milan.py reaches for {w}"
        assert f".{w}(" not in src
    # The one writer of the money chain is the kernel.
    assert "audit.jsonl" not in src.replace("milan.audit.jsonl", "")
    # Its own POST forwards a nonce and nothing money-shaped.
    assert '"/recon/settle", {"nonce": nonce}' in src


def test_paisas_recon_routes_call_no_gateway_write():
    src = (ROOT / "gawaah" / "paisa.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    wanted = {"gateway_lookup", "recon_view", "settle_from_recon", "sim_settle",
              "_recon_day", "_intent_summary"}
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            seen.add(node.name)
            names = _attr_names(node)
            for w in GATEWAY_WRITES:
                if node.name == "sim_settle" and w == "create_ondemand_settlement":
                    continue      # the simulator's batch, refused by name in live mode
                assert w not in names, f"paisa.{node.name} reaches for {w}"
    assert seen == wanted, f"routes renamed under the pin: {wanted - seen}"


def test_the_health_says_what_it_can_and_cannot_do(rig):
    body = rig.till.get("/milan/health").json()
    assert body["ok"] is True and body["settles_money"] is False
    assert body["holds_gateway_key"] is False
    assert body["can_mint"] is False and body["can_charge"] is False
    assert body["can_refund"] is False
    assert body["exception_classes"] == list(EXCEPTION_CLASSES)


# ==========================================================================
# 6. beside the frozen day, never in it; paisa unreachable is a named answer
# ==========================================================================


def test_the_closed_day_shows_the_match_beside_the_record_and_the_record_is_untouched(rig):
    paid_bill(rig)
    today = day_of(rig)
    r = rig.till.post("/daybook/close", json={"counted_cash_rupees": "100.00", "day": today})
    assert r.status_code == 200, r.json()
    record_before = json.dumps(r.json()["record"], sort_keys=True)
    next_day(rig)
    body = rig.till.get(f"/daybook/{today}").json()
    assert body["ok"] is True
    assert json.dumps(body["record"], sort_keys=True) == record_before
    assert body["record_unedited"] is True
    m = body["milan"]
    assert body["milan_unavailable"] is None, body
    assert m["settlement_day"] == day_of(rig)
    assert m["matched"]["count"] == 1 and m["matched"]["net_paise"] > 0
    assert "rows" not in m["matched"]
    assert m["exception_count"] == 0
    assert "milan" not in body["record"]


def test_paisa_unreachable_is_a_named_answer_and_the_record_still_serves(rig, monkeypatch):
    today = day_of(rig)
    rig.till.post("/daybook/close", json={"counted_cash_rupees": "0", "day": today})
    down = (503, {"ok": False, "reason": "paisa_unreachable", "detail": "not started"})
    monkeypatch.setattr(milan, "_paisa_get", lambda path: down)
    r = rig.till.get("/milan?day=" + today)
    assert r.status_code == 503 and r.json()["reason"] == milan.R_PAISA
    body = rig.till.get(f"/daybook/{today}").json()
    assert body["ok"] is True and body["milan"] is None
    assert "paisa_unreachable" in body["milan_unavailable"]


# ==========================================================================
# 7. the voice
# ==========================================================================


@pytest.mark.parametrize("said,day", [
    ("kal bank mein kitna aaya", "yesterday"),
    ("aaj bank me kitna aya", "today"),
    ("parso bank mein kya aaya", "day_before"),
    ("kal ka settlement batao", "yesterday"),
    ("gateway ne kitna diya", "yesterday"),
    ("कल बैंक में कितना आया", "yesterday"),
    ("কাল ব্যাংকে কত এল", "yesterday"),
    ("how much came into the bank yesterday", "yesterday"),
])
def test_the_local_parser_routes_bank_questions(said, day):
    tool, args = assistant.local_route(said)
    assert tool == assistant.TOOL_BANK and args == {"day": day}


@pytest.mark.parametrize("said,tool", [
    ("bank se kharcha 200", assistant.TOOL_PROPOSE_EXPENSE),
    ("cash kitna hai", assistant.TOOL_CASH_POSITION),
    ("aaj ka hisab", assistant.TOOL_DAY_CLOSE),
    ("Sharma ji ka kitna baaki hai", assistant.TOOL_KHATA_BALANCE),
])
def test_the_neighbouring_questions_still_route_where_they_did(said, tool):
    assert assistant.local_route(said)[0] == tool


def test_kal_bank_mein_kitna_aaya_speaks_the_figures_and_the_exception(rig):
    for _ in range(3):
        paid_bill(rig)
    lost = mint(rig)
    pay(rig, lost, deliver=False)
    next_day(rig)
    # "kal" is yesterday by the WALL clock inside assistant; the rig's clock
    # is virtual, so ask for the day explicitly through the same executor.
    out = assistant.execute(assistant.TOOL_BANK, {"day": day_of(rig)})
    said = out["answer"]
    net = out["data"]["matched"]["net_rupees"]
    assert f"Rs {net} reached the bank" in said
    assert "3 bills matched" in said
    assert "1 payment in Razorpay's report that no bill on this counter settled" in said
    assert "simulator" in said
    assert out["proposal"] is None
    assert out["data"]["exceptions"][X_FOUND]["count"] == 1
    walk_ints(out["data"])
    # The sentence itself routes to that tool with that day, offline.
    assert assistant.local_route("kal bank mein kitna aaya") == (
        assistant.TOOL_BANK, {"day": "yesterday"})


def test_a_bank_day_the_counter_cannot_read_is_refused(rig):
    with pytest.raises(assistant.AssistantRefused) as exc:
        assistant.execute(assistant.TOOL_BANK, {"day": "last tuesday"})
    assert exc.value.reason == assistant.R_BAD_TOOL_ARGS
