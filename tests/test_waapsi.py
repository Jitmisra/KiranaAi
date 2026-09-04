"""WAAPSI — a return by camera, refunded by Razorpay, REFUNDED only on a signed
refund.processed.

What this suite pins, in the order a demo could fake each one:

  1. A REFUND IS A SEPARATE MACHINE. The kernel never writes a negative debit
     and never moves the settled intent; a refund is its own row with its own
     states (requested -> processed | failed), keyed to the payment and the
     line.
  2. REFUNDED ONLY ON A SIGNED refund.processed. The HTTP answer to the refund
     call makes it REQUESTED and nothing more; a signature-verified
     refund.processed over the raw bytes is the only thing that turns it
     PROCESSED, and a replay of that event moves nothing.
  3. PRESS REFUND TWICE -> already_refunded. AMOUNT ON THE SIGNED EVENT MUST
     EQUAL THE PAISE ASKED FOR, OR THE REFUND PARKS needs_human.
  4. REFUSALS BY NAME: item_not_on_this_bill, bill_not_settled,
     already_refunded, amount_disagrees.
  5. STOCK AND LOYALTY ARE UNTOUCHED UNTIL PROCESSED, then a stock IN "return"
     is derived and points are clawed back for exactly those paise.
  6. INTEGERS EVERYWHERE, and the chains verify.
"""
from __future__ import annotations

import datetime as _dt
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

from gawaah import loyalty, manage, receipts, stock  # noqa: E402
from gawaah.clock import VirtualClock  # noqa: E402
from gawaah.kernel import (  # noqa: E402
    RF_CALLING, RF_FAILED, RF_INDETERMINATE, RF_NEW, RF_PROCESSED, RF_REQUESTED,
    RFE_APPLIED, RFE_PARKED, IllegalTransition, Kernel, RefundRefused,
    SETTLED, UnknownRefund,
)
from gawaah.ledger import Ledger, verify  # noqa: E402
from gawaah.paisa import (  # noqa: E402
    DictPriceBook, PaisaConfig, PaisaService, create_app,
)
from gawaah.rzp_sim import SIM_BODY_MARKER, RazorpaySim, RazorpaySimError  # noqa: E402
from gawaah.webhook import RefundPredicate  # noqa: E402
from tools import upload_app  # noqa: E402

SECRET = "whsec_waapsi_test_only"
PRICES = {"parle_g": 10000, "maggi": 15000, "soap": 40000}   # Rs 650.00 together
BILL = 65000
PARLE = 10000


# ------------------------------------------------------------------ rigging


@pytest.fixture()
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A whole counter in a temp dir. Never results/."""
    data = tmp_path / "data"
    shop = data / "shop"
    (data / "scans").mkdir(parents=True)
    shop.mkdir(parents=True)
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(data))
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(shop))
    upload_app.set_store_dir(shop)
    manage._CHAIN_CACHE.clear()

    clock = VirtualClock(start=_dt.datetime.now(_dt.timezone.utc).isoformat())
    ledger = Ledger(data / "audit.jsonl")
    kernel = Kernel(str(data / "kernel.db"), clock, ledger)
    cfg = PaisaConfig(mode="sim", key_id="rzp_test_WAAPSI", key_secret="k",
                      webhook_secret=SECRET, seed=23)
    sim = RazorpaySim(webhook_secret=SECRET, clock=clock, seed=23)
    svc = PaisaService(clock=clock, ledger=ledger, kernel=kernel, gateway=sim,
                       config=cfg, price_book=DictPriceBook(PRICES), data_dir=str(data))
    paisa = TestClient(create_app(svc))

    class Rig:
        pass

    r = Rig()
    r.data, r.shop = data, shop
    r.clock, r.ledger, r.kernel, r.sim, r.svc = clock, ledger, kernel, sim, svc
    r.paisa = paisa
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


def settle_bill(rig, session_id="till_r1", skus=("parle_g", "maggi", "soap")):
    """Mint, pay in the simulator, deliver the signed webhook: a SETTLED bill.

    Returns the mint body. After this the kernel holds a SETTLED intent with a
    payment id, and results/audit.jsonl carries the session exit lines that
    carry each line's charged price."""
    total = sum(PRICES[s] for s in skus)
    sid = witness(rig, f"scan_{session_id}", skus=skus)
    m = rig.paisa.post("/intent", json={
        "session_id": session_id, "amount_paise": total,
        "scan": {"scan_id": sid}})
    assert m.status_code == 200, m.text
    link_id = m.json()["payment_link_id"]
    res = rig.sim.pay_link(link_id)
    for d in res.deliveries:
        w = rig.paisa.post("/webhook", content=d.body, headers=dict(d.headers))
        assert w.status_code == 200, w.text
    sv = rig.paisa.get(f"/session/{session_id}").json()
    assert sv["paid"] is True and sv["state"] == "PAID"
    return m.json()


def refund_line(rig, session_id="till_r1", item="parle_g#0", sku="parle_g",
                amount=PARLE):
    return rig.paisa.post("/refund", json={
        "session_id": session_id, "item_id": item, "sku_id": sku,
        "amount_paise": amount})


def sim_process(rig, refund_key, outcome="processed"):
    return rig.paisa.post("/sim/refund", json={
        "refund_key": refund_key, "outcome": outcome})


def walk_ints(obj: Any, path: str = "") -> None:
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


# ==========================================================================
# 1. the kernel: a refund is a separate machine, never a negative debit
# ==========================================================================


def test_the_refund_machine_never_reaches_the_intent_table(rig):
    """A refund hangs off a SETTLED intent and never moves it. There is no
    negative debit anywhere: the intent stays SETTLED, and the paise going back
    live only in the refunds table."""
    it = rig.kernel.create_intent("s1", BILL)
    rig.kernel.mark_calling(it.nonce)
    it = rig.kernel.mark_settled(it.nonce, "pay_1")
    rf = rig.kernel.create_refund(it.nonce, item_id="parle_g#0", sku_id="parle_g",
                                  amount_paise=PARLE)
    assert rf.state == RF_NEW and rf.amount_paise == PARLE
    # the intent is untouched, still SETTLED, still the full amount
    again = rig.kernel.get(it.nonce)
    assert again.state == SETTLED and again.amount_paise == BILL
    # no intent anywhere carries a negative or reduced amount
    for x in rig.kernel.all_intents():
        assert x.amount_paise > 0


def test_a_refund_needs_a_settled_intent_with_a_payment_id(rig):
    it = rig.kernel.create_intent("s_open", BILL)
    with pytest.raises(RefundRefused) as ei:
        rig.kernel.create_refund(it.nonce, item_id="x#0", sku_id="x", amount_paise=100)
    assert ei.value.code == "bill_not_settled"


def test_pressing_refund_twice_is_a_replay_not_a_second_refund(rig):
    it = rig.kernel.create_intent("s2", BILL)
    rig.kernel.mark_calling(it.nonce)
    rig.kernel.mark_settled(it.nonce, "pay_2")
    first = rig.kernel.create_refund(it.nonce, item_id="parle_g#0", sku_id="parle_g",
                                     amount_paise=PARLE)
    assert not first.replayed
    second = rig.kernel.create_refund(it.nonce, item_id="parle_g#0", sku_id="parle_g",
                                      amount_paise=PARLE)
    assert second.replayed and second.refund_key == first.refund_key
    assert len(rig.kernel.refunds_for_nonce(it.nonce)) == 1


def test_refunds_cannot_together_exceed_the_bill(rig):
    it = rig.kernel.create_intent("s3", BILL)
    rig.kernel.mark_calling(it.nonce)
    rig.kernel.mark_settled(it.nonce, "pay_3")
    # refund the whole bill in one line
    rig.kernel.create_refund(it.nonce, item_id="whole#0", sku_id="whole",
                             amount_paise=BILL)
    with pytest.raises(RefundRefused) as ei:
        rig.kernel.create_refund(it.nonce, item_id="extra#1", sku_id="extra",
                                 amount_paise=100)
    assert ei.value.code == "refund_exceeds_bill"
    assert rig.kernel.committed_refund_paise(it.nonce) == BILL


def test_processed_only_moves_a_live_refund_and_only_on_the_right_amount(rig):
    it = rig.kernel.create_intent("s4", BILL)
    rig.kernel.mark_calling(it.nonce)
    rig.kernel.mark_settled(it.nonce, "pay_4")
    rf = rig.kernel.create_refund(it.nonce, item_id="parle_g#0", sku_id="parle_g",
                                  amount_paise=PARLE)
    rf = rig.kernel.mark_refund_calling(rf.refund_key)
    rf = rig.kernel.mark_refund_requested(rf.refund_key, gateway_refund_id="rfnd_1")
    assert rf.state == RF_REQUESTED and rig.kernel.refunded_paise(it.nonce) == 0

    # a processed event whose amount disagrees PARKS, moves nothing
    ev, rf = rig.kernel.record_refund_event(
        event_id="ev_wrong", event="refund.processed", refund_key=rf.refund_key,
        amount_paise=PARLE + 1, gateway_refund_id="rfnd_1")
    assert ev.state == RFE_PARKED and rf.state == RF_REQUESTED and rf.needs_human
    assert rig.kernel.refunded_paise(it.nonce) == 0

    # the right amount moves it to PROCESSED
    ev, rf = rig.kernel.record_refund_event(
        event_id="ev_ok", event="refund.processed", refund_key=rf.refund_key,
        amount_paise=PARLE, gateway_refund_id="rfnd_1")
    assert ev.state == RFE_APPLIED and rf.state == RF_PROCESSED
    assert rig.kernel.refunded_paise(it.nonce) == PARLE

    # a replay of the signed event moves nothing and writes no line
    lines = rig.ledger.count
    ev2, rf2 = rig.kernel.record_refund_event(
        event_id="ev_ok", event="refund.processed", refund_key=rf.refund_key,
        amount_paise=PARLE, gateway_refund_id="rfnd_1")
    assert ev2.replayed and rf2.state == RF_PROCESSED
    assert rig.ledger.count == lines, "a replay wrote an audit line"
    assert verify(rig.ledger.path)[0]


def test_a_processed_refund_is_terminal(rig):
    it = rig.kernel.create_intent("s5", BILL)
    rig.kernel.mark_calling(it.nonce)
    rig.kernel.mark_settled(it.nonce, "pay_5")
    rf = rig.kernel.create_refund(it.nonce, item_id="a#0", sku_id="a", amount_paise=PARLE)
    rf = rig.kernel.mark_refund_calling(rf.refund_key)
    rf = rig.kernel.mark_refund_requested(rf.refund_key, gateway_refund_id="rfnd_5")
    rig.kernel.record_refund_event(event_id="e5", event="refund.processed",
                                   refund_key=rf.refund_key, amount_paise=PARLE,
                                   gateway_refund_id="rfnd_5")
    with pytest.raises(IllegalTransition):
        rig.kernel.mark_refund_failed(rf.refund_key)


def test_a_failed_refund_frees_the_line_for_another_attempt(rig):
    it = rig.kernel.create_intent("s6", BILL)
    rig.kernel.mark_calling(it.nonce)
    rig.kernel.mark_settled(it.nonce, "pay_6")
    rf = rig.kernel.create_refund(it.nonce, item_id="a#0", sku_id="a", amount_paise=PARLE)
    rf = rig.kernel.mark_refund_calling(rf.refund_key)
    rf = rig.kernel.mark_refund_failed(rf.refund_key, "declined")
    assert rf.state == RF_FAILED and rig.kernel.committed_refund_paise(it.nonce) == 0
    # the line is free again: a new refund is NOT a replay
    rf2 = rig.kernel.create_refund(it.nonce, item_id="a#0", sku_id="a", amount_paise=PARLE)
    assert not rf2.replayed and rf2.refund_key != rf.refund_key and rf2.attempt == 1


# ==========================================================================
# 2. the simulator produces a signed refund.processed
# ==========================================================================


def test_sim_refund_is_pending_then_a_signed_processed(rig):
    link = rig.sim.create_payment_link(BILL, {"session_id": "s"}, reference_id="r1")
    pay = rig.sim.pay_link(link["id"]).payment
    ref = rig.sim.refund(pay["id"], PARLE, speed="optimum",
                         notes={"refund_key": "rf_abc"})
    assert ref["status"] == "pending" and ref["amount"] == PARLE
    assert ref[SIM_BODY_MARKER] is True if SIM_BODY_MARKER in ref else True
    result = rig.sim.process_refund(ref["id"])
    (d,) = result.deliveries
    assert d.event == "refund.processed"
    body = json.loads(d.body)
    assert body[SIM_BODY_MARKER] is True
    assert list(body)[:3] == ["entity", "account_id", "event"], "not insertion-ordered"
    ent = body["payload"]["refund"]["entity"]
    assert ent["status"] == "processed" and ent["amount"] == PARLE
    assert ent["notes"]["refund_key"] == "rf_abc"
    from gawaah.rzp_sim import verify_webhook_signature
    assert verify_webhook_signature(d.body, d.signature, SECRET)
    # bytes, not JSON: a reserialised body fails
    assert not verify_webhook_signature(
        d.body.replace(b'"event":', b'"event" :', 1), d.signature, SECRET)


def test_sim_refund_cannot_exceed_what_is_refundable(rig):
    link = rig.sim.create_payment_link(BILL, {"session_id": "s"}, reference_id="r2")
    pay = rig.sim.pay_link(link["id"]).payment
    with pytest.raises(RazorpaySimError):
        rig.sim.refund(pay["id"], BILL + 1)
    rig.sim.refund(pay["id"], BILL - 100)          # leaves 100 refundable
    with pytest.raises(RazorpaySimError):
        rig.sim.refund(pay["id"], 200)             # more than the 100 left


# ==========================================================================
# 3. the money service, end to end: requested -> processed -> refunded
# ==========================================================================


def test_a_refund_is_requested_then_refunded_only_on_the_webhook(rig):
    settle_bill(rig)
    r = refund_line(rig)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == RF_REQUESTED and body["refunded"] is False
    assert body["amount_paise"] == PARLE and body["gateway_refund_id"]
    assert body["bill_amount_paise"] == BILL and body["refunded_paise"] == 0
    walk_ints(body)
    key = body["refund_key"]

    # nothing is REFUNDED until the signed callback lands
    assert rig.kernel.get_refund(key).state == RF_REQUESTED

    sp = sim_process(rig, key)
    assert sp.status_code == 200, sp.text
    sb = sp.json()
    assert sb["refunded"] is True and sb["state"] == RF_PROCESSED
    assert sb["refunded_paise"] == PARLE
    wh = sb["webhooks"][0]
    assert wh["event"] == "refund.processed"
    assert wh["refund"]["reason"] == "refund"
    assert wh["refund"]["applied"] is True and wh["refund"]["refunded"] is True
    walk_ints(sb)

    view = rig.paisa.get(f"/refunds/till_r1").json()
    assert view["refunded_paise"] == PARLE and view["bill_amount_paise"] == BILL
    assert view["requested_paise"] == 0


def test_the_bill_never_turns_green_from_a_refund(rig):
    settle_bill(rig)
    r = refund_line(rig)
    sim_process(rig, r.json()["refund_key"])
    # the intent stayed SETTLED for its FULL amount; a refund did not add or
    # subtract a debit
    it = rig.kernel.settled_intent_for("till_r1")
    assert it.state == SETTLED and it.amount_paise == BILL
    sv = rig.paisa.get("/session/till_r1").json()
    assert sv["paid"] is True   # it WAS paid; the refund is a separate movement


def test_refusals_by_name(rig):
    settle_bill(rig)
    # a packet that was not on this bill
    bad = rig.paisa.post("/refund", json={
        "session_id": "till_r1", "item_id": "ghee#9", "sku_id": "ghee",
        "amount_paise": 10000})
    assert bad.status_code == 409 and bad.json()["error"] == "item_not_on_this_bill"

    # a bill no signed webhook ever settled
    witness(rig, "scan_unpaid", skus=("parle_g",))
    rig.paisa.post("/intent", json={"session_id": "till_unpaid",
                                    "amount_paise": PARLE,
                                    "scan": {"scan_id": "scan_unpaid"}})
    ns = rig.paisa.post("/refund", json={
        "session_id": "till_unpaid", "item_id": "parle_g#0", "sku_id": "parle_g",
        "amount_paise": PARLE})
    assert ns.status_code == 409 and ns.json()["error"] == "bill_not_settled"

    # the till's figure disagreeing with the charged price
    wrong = rig.paisa.post("/refund", json={
        "session_id": "till_r1", "item_id": "parle_g#0", "sku_id": "parle_g",
        "amount_paise": PARLE + 500})
    assert wrong.status_code == 409 and wrong.json()["error"] == "amount_disagrees"

    # pressing REFUND twice
    ok = refund_line(rig)
    assert ok.status_code == 200
    again = refund_line(rig)
    assert again.status_code == 409 and again.json()["error"] == "already_refunded"


def test_a_replayed_refund_webhook_changes_nothing(rig):
    settle_bill(rig)
    key = refund_line(rig).json()["refund_key"]
    rf = rig.kernel.get_refund(key)
    # deliver the same signed processed event twice by hand
    result = rig.sim.process_refund(rf.gateway_refund_id)
    (d,) = result.deliveries
    a = rig.paisa.post("/webhook", content=d.body, headers=dict(d.headers)).json()
    b = rig.paisa.post("/webhook", content=d.body, headers=dict(d.headers)).json()
    assert a["refund"]["applied"] is True and a["refund"]["replayed"] is False
    assert b["refund"]["applied"] is False and b["refund"]["replayed"] is True
    assert rig.kernel.refunded_paise(rig.kernel.settled_intent_for("till_r1").nonce) == PARLE


# ==========================================================================
# 4. stock and loyalty: untouched until processed, then derived
# ==========================================================================


def _catalogue(rig):
    (rig.shop / "catalog.json").write_text(json.dumps({
        "format": 2, "dim": 4,
        "gates": {"phi": 0.9, "theta": 0.1, "tau_mm": 4.0, "phi_appearance_only": 0.92},
        "skus": {s: {"name": s, "price_paise": p, "footprint_mm": 95.1,
                     "taught_by": "mat_measured", "vectors": [[1.0, 0, 0, 0]],
                     "photo": None, "photo_bytes": 0}
                 for s, p in PRICES.items()},
    }), encoding="utf-8")


def _count_shelf(rig, sku, units):
    manage._CHAIN_CACHE.clear()
    st, _ = manage.read_opening_stock()
    st[sku] = {"units": units, "counted_at": "2000-01-01T00:00:00+00:00"}
    manage.write_opening_stock(st)


def test_stock_return_appears_only_after_the_refund_is_processed(rig):
    _catalogue(rig)
    _count_shelf(rig, "parle_g", 10)
    settle_bill(rig)                       # parle_g billed once: on hand 10 - 1 = 9
    manage._CHAIN_CACHE.clear()
    row0 = next(r for r in stock.stock_rows()["items"] if r["sku_id"] == "parle_g")
    assert row0["on_hand_units"] == 9 and row0["returned_since_count"] == 0

    key = refund_line(rig).json()["refund_key"]
    manage._CHAIN_CACHE.clear()
    # requested, NOT processed: the packet has not come back yet
    row1 = next(r for r in stock.stock_rows()["items"] if r["sku_id"] == "parle_g")
    assert row1["returned_since_count"] == 0 and row1["on_hand_units"] == 9

    sim_process(rig, key)
    manage._CHAIN_CACHE.clear()
    row2 = next(r for r in stock.stock_rows()["items"] if r["sku_id"] == "parle_g")
    assert row2["returned_since_count"] == 1 and row2["on_hand_units"] == 10
    assert row2["returns"][0]["reason"] == "return"
    walk_ints({"row": {k: v for k, v in row2.items() if k not in ("cover",)}})


def test_a_hand_posted_return_reason_is_refused(rig):
    _catalogue(rig)
    app = FastAPI()
    app.include_router(stock.router)
    till = TestClient(app)
    r = till.post("/stock/parle_g/in", json={"units": 1, "reason": "return"})
    assert r.status_code == 400
    assert r.json()["reason"] == "stock_reason_written_by_the_counter_only"


def test_loyalty_claws_back_points_for_exactly_the_refunded_paise(rig):
    app = FastAPI()
    app.include_router(loyalty.router)
    till = TestClient(app)
    # one point per rupee, set before the bill settles
    assert till.post("/loyalty/rules",
                     json={"points_per_rupee": 1, "paise_per_point": 100}).status_code == 200
    settle_bill(rig)                       # Rs 650 settled -> 650 points
    till.post("/loyalty/attach", json={"session_id": "till_r1", "phone": "9876543210"})
    manage._CHAIN_CACHE.clear()
    bal0 = till.get("/loyalty/balance/9876543210").json()
    assert bal0["earned_points"] == 650 and bal0["refunded_paise"] == 0

    key = refund_line(rig).json()["refund_key"]
    manage._CHAIN_CACHE.clear()
    # requested only: nothing clawed back yet
    bal1 = till.get("/loyalty/balance/9876543210").json()
    assert bal1["earned_points"] == 650 and bal1["refunded_paise"] == 0

    sim_process(rig, key)
    manage._CHAIN_CACHE.clear()
    bal2 = till.get("/loyalty/balance/9876543210").json()
    # Rs 650 settled, Rs 100 refunded -> points on Rs 550
    assert bal2["earned_points"] == 550 and bal2["refunded_paise"] == PARLE
    walk_ints(bal2)


# ==========================================================================
# 5. the receipt shows the refund, and the chains verify
# ==========================================================================


def test_the_receipt_shows_the_refund_and_leaves_the_total_alone(rig):
    _catalogue(rig)
    settle_bill(rig)
    key = refund_line(rig).json()["refund_key"]
    sim_process(rig, key)
    manage._CHAIN_CACHE.clear()
    rec = receipts.build_receipt("till_r1")
    assert rec["total_paise"] == BILL, "the total must not be netted"
    assert rec["refunded_paise"] == PARLE
    assert rec["net_paise"] == BILL - PARLE
    assert any("refunded" in n.lower() for n in rec["notes"])
    walk_ints(rec)


def test_the_receipt_qr_session_reads_back_but_a_upi_string_does_not():
    # our own bookmark resolves
    url = "http://192.168.1.7:8790/receipt/till_r1/page"
    assert receipts.receipt_session_from_payload(url) == "till_r1"
    # a payment payload, a gateway host, and a foreign path do NOT
    assert receipts.receipt_session_from_payload("upi://pay?pa=x@y&am=100") is None
    assert receipts.receipt_session_from_payload("https://rzp.io/i/abc") is None
    assert receipts.receipt_session_from_payload("http://192.168.1.7:8790/shop") is None
    assert receipts.receipt_session_from_payload("gawaah:parle_g") is None


def test_every_refund_line_verifies_on_the_money_chain(rig):
    settle_bill(rig)
    key = refund_line(rig).json()["refund_key"]
    sim_process(rig, key)
    ok, _n, _head, err = verify(rig.ledger.path)
    assert ok, err


def test_the_refund_predicate_refuses_a_tampered_body(rig):
    """A signed refund.processed with one byte changed is bad_signature, and
    an unknown refund key is unknown_refund. The predicate parses nothing above
    the HMAC."""
    settle_bill(rig)
    rf = rig.kernel.get_refund(refund_line(rig).json()["refund_key"])
    (d,) = rig.sim.process_refund(rf.gateway_refund_id).deliveries
    pred = RefundPredicate(lambda key, gid: rig.kernel.get_refund(key)
                           if key else rig.kernel.refund_by_gateway_id(gid))
    good = pred.evaluate(d.body, d.signature, SECRET)
    assert good.known and good.outcome == "PROCESSED"
    bad = pred.evaluate(d.body.replace(b"processed", b"p-ocessed", 1),
                        d.signature, SECRET)
    assert not bad.known and bad.reason == "bad_signature"
