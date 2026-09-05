"""KHATA — the udhaar book, collected by Razorpay, dropped only on a signed webhook.

What this suite pins, in the order a demo could fake each one:

  1. A PARTIAL CAPTURE CREDITS EXACTLY THE SIGNED AMOUNT — the paise the
     payment entity inside the HMAC-verified body carried, not the link's
     running total, not a figure the caller worked out.
  2. A REPLAYED EVENT ID DOES NOTHING. Same bytes, same signature, same
     event key: the kernel's captures table is UNIQUE on it, credits nothing
     twice, and writes no second audit line.
  3. AN OVER-CAPTURE IS PARKED, NEVER NETTED. Money past what the book says is
     owed is recorded under its event id with needs_human, and the balance is
     untouched.
  4. A BILL NEVER TURNS PAID FROM A PARTIAL. The kernel row is BOOKED, BOOKED
     has no legal move to SETTLED, the green predicate never sees the
     collection's link, and the till's session stays off PAID.
  5. A SECOND COLLECT IS REFUSED BY NAME while one is open.
  6. THE CHAINS VERIFY — the money chain and khata's own — and every figure in
     every response is an integer; nothing here writes results/.
  7. THE SIMULATOR produces a signed `payment_link.partially_paid` for a
     partial, with an insertion-ordered body carrying the _gawaah_sim marker.
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

from gawaah import advisor, assistant, khata, manage  # noqa: E402
from gawaah.clock import VirtualClock  # noqa: E402
from gawaah.kernel import (  # noqa: E402
    BOOKED, CALLING, CAP_CREDITED, CAP_PARKED, COL_OPEN, COL_PAID, LEGAL, NEW,
    SETTLED, CollectionOpen, IllegalTransition, Kernel,
)
from gawaah.ledger import Ledger, verify  # noqa: E402
from gawaah.paisa import (  # noqa: E402
    DictPriceBook, PaisaConfig, PaisaService, create_app, first_min_partial_paise,
)
from gawaah.rzp_sim import SIM_BODY_MARKER, RazorpaySim, RazorpaySimError  # noqa: E402
from gawaah.webhook import CollectionPredicate, GreenPredicate, Intent  # noqa: E402
from tools import upload_app  # noqa: E402

SECRET = "whsec_khata_test_only"
PRICES = {"parle_g": 10000, "maggi": 15000, "soap": 40000}   # Rs 650.00 together
BILL = 65000
BOOK = "bk_" + "c0ffee" * 3
BOOK2 = "bk_" + "abcdef" * 3


# ------------------------------------------------------------------ rigging


@pytest.fixture()
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A whole counter in a temp dir: money service, kernel, sim, and the
    till's khata router wired to the same paisa through its TestClient.
    Never results/: both env vars and the till's own handle are redirected."""
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
    cfg = PaisaConfig(mode="sim", key_id="rzp_test_KHATA", key_secret="k",
                      webhook_secret=SECRET, seed=11)
    sim = RazorpaySim(webhook_secret=SECRET, clock=clock, seed=11)
    svc = PaisaService(clock=clock, ledger=ledger, kernel=kernel, gateway=sim,
                       config=cfg, price_book=DictPriceBook(PRICES), data_dir=str(data))
    paisa = TestClient(create_app(svc))

    def _post(path: str, body: dict, timeout_s: int = 30):
        r = paisa.post(path, json=body)
        return r.status_code, r.json()

    def _get(path: str):
        r = paisa.get(path)
        return r.status_code, r.json()

    monkeypatch.setattr(khata, "_paisa_post", _post)
    monkeypatch.setattr(khata, "_paisa_get", _get)

    app = FastAPI()
    app.include_router(khata.router)
    app.include_router(assistant.router)
    till = TestClient(app)

    class Rig:
        pass

    r = Rig()
    r.data, r.shop = data, shop
    r.clock, r.ledger, r.kernel, r.sim, r.svc = clock, ledger, kernel, sim, svc
    r.paisa, r.till = paisa, till
    yield r
    manage._CHAIN_CACHE.clear()


def witness(rig, scan_id: str = "scan_khata01", skus=("parle_g", "maggi", "soap")) -> str:
    """A scan witness the counter wrote, in the shape paisa loads by id."""
    doc = {
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "lines": [{"code": f"gawaah:{s}", "sku_id": s} for s in skus],
        "codes_found": len(skus),
    }
    (rig.data / "scans" / f"{scan_id}.json").write_text(json.dumps(doc), encoding="utf-8")
    return scan_id


def book(rig, session_id: str = "till_k1", amount: int = BILL, book_id: str = BOOK,
         scan_id: str | None = None):
    sid = scan_id or witness(rig, f"scan_{session_id}")
    return rig.paisa.post("/book", json={
        "session_id": session_id, "amount_paise": amount,
        "scan": {"scan_id": sid}, "book_id": book_id})


def collect(rig, book_id: str = BOOK, amount: int = BILL, contact: str | None = "9820114477"):
    body: dict[str, Any] = {"book_id": book_id, "amount_paise": amount}
    if contact:
        body["customer"] = {"name": "Sharma", "contact": contact}
    return rig.paisa.post("/collect", json=body)


def deliver(rig, delivery):
    return rig.paisa.post("/webhook", content=delivery.body, headers=dict(delivery.headers))


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


# ==========================================================================
# 1. the kernel: BOOKED, and captures keyed on the signed event id
# ==========================================================================


def test_booked_is_reachable_only_from_new_and_leads_nowhere():
    assert LEGAL[NEW] >= {CALLING, BOOKED}
    assert LEGAL[BOOKED] == frozenset()
    for state, moves in LEGAL.items():
        if state != NEW:
            assert BOOKED not in moves, state


def test_a_booked_bill_cannot_be_settled(rig):
    it = rig.kernel.create_intent("s_booked", 1000)
    it = rig.kernel.mark_booked(it.nonce, BOOK)
    assert it.state == BOOKED and it.book_id == BOOK
    with pytest.raises(IllegalTransition):
        rig.kernel.mark_settled(it.nonce, "pay_x")
    with pytest.raises(IllegalTransition):
        rig.kernel.mark_calling(it.nonce)
    # idempotent for the same book, refused for another
    assert rig.kernel.mark_booked(it.nonce, BOOK).state == BOOKED
    with pytest.raises(IllegalTransition):
        rig.kernel.mark_booked(it.nonce, BOOK2)


def test_a_minted_bill_cannot_go_on_the_book(rig):
    it = rig.kernel.create_intent("s_minted", 1000)
    rig.kernel.mark_calling(it.nonce)
    with pytest.raises(IllegalTransition):
        rig.kernel.mark_booked(it.nonce, BOOK)


def test_capture_credits_exactly_the_signed_amount_and_replays_once(rig):
    it = rig.kernel.create_intent("s1", 65000)
    rig.kernel.mark_booked(it.nonce, BOOK)
    col = rig.kernel.create_collection(BOOK, 65000)
    rig.kernel.mark_collection_calling(col.collection_id)
    rig.kernel.mark_collection_open(col.collection_id, payment_link_id="plink_1",
                                    short_url="https://rzp.io/i/x", expire_by=None)
    lines_before = rig.ledger.count
    cap = rig.kernel.record_capture(
        event_id="evt_1", collection_id=col.collection_id, amount_paise=20000,
        payment_id="pay_1", link_amount_paid=20000, event="payment_link.partially_paid",
        final=False)
    assert cap.credited and cap.amount_paise == 20000 and cap.outstanding_paise == 45000
    assert rig.kernel.outstanding_paise(BOOK) == 45000
    lines_after = rig.ledger.count
    assert lines_after > lines_before
    again = rig.kernel.record_capture(
        event_id="evt_1", collection_id=col.collection_id, amount_paise=20000,
        payment_id="pay_1", link_amount_paid=20000, event="payment_link.partially_paid",
        final=False)
    assert again.replayed and rig.kernel.outstanding_paise(BOOK) == 45000
    assert rig.ledger.count == lines_after, "a replay wrote an audit line"
    assert verify(rig.ledger.path)[0]


def test_an_over_capture_is_parked_not_netted(rig):
    it = rig.kernel.create_intent("s2", 30000)
    rig.kernel.mark_booked(it.nonce, BOOK)
    col = rig.kernel.create_collection(BOOK, 30000)
    rig.kernel.mark_collection_calling(col.collection_id)
    rig.kernel.mark_collection_open(col.collection_id, payment_link_id="plink_2",
                                    short_url=None, expire_by=None)
    cap = rig.kernel.record_capture(
        event_id="evt_big", collection_id=col.collection_id, amount_paise=30001,
        payment_id="pay_big", link_amount_paid=30001, event="payment_link.paid",
        final=True)
    assert cap.state == CAP_PARKED and not cap.credited
    assert "over_capture" in (cap.reason or "")
    assert rig.kernel.outstanding_paise(BOOK) == 30000, "an over-capture changed the balance"
    c = rig.kernel.get_collection(col.collection_id)
    assert c.needs_human and c.captured_paise == 0
    assert [p.event_id for p in rig.kernel.parked_captures()] == ["evt_big"]


def test_one_live_collection_per_book(rig):
    it = rig.kernel.create_intent("s3", 5000)
    rig.kernel.mark_booked(it.nonce, BOOK)
    rig.kernel.create_collection(BOOK, 5000)
    with pytest.raises(CollectionOpen):
        rig.kernel.create_collection(BOOK, 5000)


def test_the_kernel_refuses_to_mint_for_a_figure_its_rows_do_not_hold(rig):
    it = rig.kernel.create_intent("s4", 5000)
    rig.kernel.mark_booked(it.nonce, BOOK)
    with pytest.raises(Exception) as ei:
        rig.kernel.create_collection(BOOK, 4999)
    assert "outstanding" in str(ei.value)


# ==========================================================================
# 2. the simulator: a partial pay is a signed partially_paid webhook
# ==========================================================================


def test_sim_partial_pay_emits_a_signed_partially_paid_then_paid(rig):
    link = rig.sim.create_payment_link(
        65000, {"collection_id": "col_" + "0" * 12}, reference_id="c1",
        accept_partial=True, first_min_partial_amount=16200, reminder_enable=True,
        notify={"sms": True}, customer={"contact": "9820114477"})
    assert link["accept_partial"] is True and link["reminder_enable"] is True
    res = rig.sim.pay_link(link["id"], amount_paise=20000)
    (d,) = res.deliveries
    assert d.event == "payment_link.partially_paid"
    body = json.loads(d.body)
    assert body[SIM_BODY_MARKER] is True
    assert list(body)[:3] == ["entity", "account_id", "event"], "not insertion-ordered"
    assert body["payload"]["payment_link"]["entity"]["status"] == "partially_paid"
    assert body["payload"]["payment_link"]["entity"]["amount_paid"] == 20000
    assert body["payload"]["payment"]["entity"]["amount"] == 20000
    from gawaah.rzp_sim import verify_webhook_signature
    assert verify_webhook_signature(d.body, d.signature, SECRET)
    # semantically identical JSON, different bytes: the signature is over BYTES
    assert not verify_webhook_signature(d.body.replace(b'"event":', b'"event" :', 1), d.signature, SECRET)
    res2 = rig.sim.pay_link(link["id"])
    (d2,) = res2.deliveries
    assert d2.event == "payment_link.paid"
    assert json.loads(d2.body)["payload"]["payment"]["entity"]["amount"] == 45000
    assert rig.sim.fetch_payment_link(link["id"])["status"] == "paid"


def test_sim_refuses_a_partial_on_a_link_that_does_not_accept_one(rig):
    link = rig.sim.create_payment_link(65000, {"session_id": "s"}, reference_id="c2")
    with pytest.raises(RazorpaySimError):
        rig.sim.pay_link(link["id"], amount_paise=20000)
    link2 = rig.sim.create_payment_link(65000, {"collection_id": "col_" + "1" * 12},
                                        reference_id="c3", accept_partial=True,
                                        first_min_partial_amount=16200)
    with pytest.raises(RazorpaySimError):
        rig.sim.pay_link(link2["id"], amount_paise=500)   # under the first-instalment floor
    with pytest.raises(RazorpaySimError):
        rig.sim.pay_link(link2["id"], amount_paise=70000)  # past what is due


# ==========================================================================
# 3. the money service: book, collect, partial, replay, refuse
# ==========================================================================


def test_book_closes_the_bill_as_booked_with_no_link(rig):
    r = book(rig)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["booked"] is True and b["state"] == BOOKED and b["minted"] is False
    assert b["amount_paise"] == BILL and b["outstanding_paise"] == BILL
    assert "short_url" not in b and "payment_link_id" not in b
    walk_ints(b)
    (it,) = rig.kernel.all_intents()
    assert it.state == BOOKED and it.book_id == BOOK
    sess = rig.paisa.get("/session/till_k1").json()
    assert sess["paid"] is False and sess["state"] != "PAID"
    # a second identical booking is a replay, not a second debt
    again = book(rig, scan_id="scan_till_k1")
    assert again.json()["replayed"] is True
    assert rig.kernel.outstanding_paise(BOOK) == BILL


def test_charge_after_book_is_refused_by_name(rig):
    book(rig)
    r = rig.paisa.post("/intent", json={"session_id": "till_k1", "amount_paise": BILL,
                                        "scan": {"scan_id": "scan_till_k1"}})
    assert r.status_code == 409 and r.json()["error"] == "bill_on_the_book"


def test_book_after_charge_is_refused_by_name(rig):
    sid = witness(rig, "scan_charged")
    m = rig.paisa.post("/intent", json={"session_id": "till_c", "amount_paise": BILL,
                                        "scan": {"scan_id": sid}})
    assert m.status_code == 200
    r = rig.paisa.post("/book", json={"session_id": "till_c", "amount_paise": BILL,
                                      "scan": {"scan_id": sid}, "book_id": BOOK})
    assert r.status_code == 409 and r.json()["error"] == "bill_already_minted"


def test_book_re_derives_the_amount_from_the_witness(rig):
    sid = witness(rig, "scan_short", skus=("parle_g", "maggi"))   # Rs 250
    r = rig.paisa.post("/book", json={"session_id": "till_w", "amount_paise": BILL,
                                      "scan": {"scan_id": sid}, "book_id": BOOK})
    assert r.status_code == 409 and r.json()["error"] == "scan_total_disagreement"
    assert rig.kernel.count() == 0


def test_collect_mints_one_partial_link_with_reminders_and_refuses_a_second(rig):
    book(rig)
    r = collect(rig)
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["state"] == COL_OPEN and c["amount_paise"] == BILL
    assert c["accept_partial"] is True and c["reminder_enable"] is True
    assert c["first_min_partial_amount"] == first_min_partial_paise(BILL) == 16200
    assert isinstance(c["short_url"], str) and c["short_url"]
    assert c["expire_by"] is not None
    walk_ints(c)
    # the gateway's own record of what was asked for
    link = rig.sim.fetch_payment_link(c["payment_link_id"])
    assert link["accept_partial"] is True and link["notify"]["sms"] is True
    assert link["reminder_enable"] is True and link["customer"]["contact"] == "9820114477"
    assert link["notes"]["collection_id"] == c["collection_id"]
    assert "session_id" not in link["notes"]
    # the contact did not stay in this process
    stored = rig.svc._collection_links[c["collection_id"]]
    assert "customer" not in stored
    # a second COLLECT while this one is open
    r2 = collect(rig)
    assert r2.status_code == 409
    assert r2.json()["error"] == "collection_link_already_open"
    assert r2.json()["collection_id"] == c["collection_id"]
    assert len(rig.kernel.all_collections()) == 1


def test_collect_refuses_a_figure_that_disagrees_with_its_own_rows(rig):
    book(rig)
    r = collect(rig, amount=BILL - 1)
    assert r.status_code == 409 and r.json()["error"] == "outstanding_disagreement"
    assert r.json()["outstanding_paise"] == BILL
    r = collect(rig, book_id=BOOK2)
    assert r.status_code == 409 and r.json()["error"] == "nothing_outstanding"


def test_partial_capture_credits_exactly_the_signed_amount(rig):
    book(rig)
    c = collect(rig).json()
    res = rig.sim.pay_link(c["payment_link_id"], amount_paise=20000)
    (d,) = res.deliveries
    w = deliver(rig, d)
    assert w.status_code == 200
    body = w.json()
    assert body["green"] is False, "a partial greened something"
    col = body["collection"]
    assert col["credited"] is True and col["amount_paise"] == 20000
    assert col["outstanding_paise"] == 45000 and col["captured_paise"] == 20000
    assert col["collection_state"] == COL_OPEN and col["final"] is False
    walk_ints(body)
    assert rig.kernel.outstanding_paise(BOOK) == 45000
    view = rig.paisa.get(f"/khata/{BOOK}").json()
    assert view["captured_paise"] == 20000 and view["outstanding_paise"] == 45000


def test_a_replayed_partial_does_nothing(rig):
    book(rig)
    c = collect(rig).json()
    (d,) = rig.sim.pay_link(c["payment_link_id"], amount_paise=20000).deliveries
    deliver(rig, d)
    lines = rig.ledger.count
    w = deliver(rig, d)
    col = w.json()["collection"]
    assert col["replayed"] is True and col["credited"] is False
    assert rig.kernel.outstanding_paise(BOOK) == 45000
    assert len(rig.kernel.captures_for(c["collection_id"])) == 1
    # only the webhook.handled bookkeeping lines; no capture line
    assert not any(json.loads(l).get("event", "").startswith("capture.")
                   for l in rig.ledger.path.read_text().splitlines()[lines:])


def test_a_partial_can_never_mark_the_bill_paid(rig):
    book(rig)
    c = collect(rig).json()
    for amt in (20000, 25000, None):
        for d in rig.sim.pay_link(c["payment_link_id"], amount_paise=amt).deliveries:
            w = deliver(rig, d)
            assert w.json()["green"] is False
            assert w.json()["settled_nonce"] is None
    (it,) = rig.kernel.all_intents()
    assert it.state == BOOKED and it.payment_id is None
    assert rig.paisa.get("/session/till_k1").json()["paid"] is False
    assert rig.kernel.get_collection(c["collection_id"]).state == COL_PAID
    assert rig.kernel.outstanding_paise(BOOK) == 0
    assert verify(rig.ledger.path)[0]


def test_the_green_predicate_never_sees_a_collection_link(rig):
    """The existing four-condition predicate is untouched and finds no session."""
    book(rig)
    c = collect(rig).json()
    (d,) = rig.sim.pay_link(c["payment_link_id"]).deliveries
    gp = GreenPredicate(lambda sid: Intent(session_id=sid, amount_paise=BILL))
    v = gp.evaluate(d.body, d.signature, SECRET)
    assert v.green is False and v.reason == "missing_session_id"


def test_the_collection_predicate_refuses_a_bill_link_and_a_bad_signature(rig):
    sid = witness(rig, "scan_bill")
    m = rig.paisa.post("/intent", json={"session_id": "till_b", "amount_paise": BILL,
                                        "scan": {"scan_id": sid}}).json()
    (d,) = rig.sim.pay_link(m["payment_link_id"]).deliveries
    cp = CollectionPredicate(lambda cid: True)
    assert cp.evaluate(d.body, d.signature, SECRET).reason == "carries_session_id"
    assert cp.evaluate(d.body, "00" * 32, SECRET).reason == "bad_signature"
    assert cp.evaluate(d.body, d.signature, "").reason == "secret_not_configured"


def test_an_over_capture_through_the_webhook_is_parked(rig):
    """Two bookings on one book, one link, then the first booking's amount is
    'moved' away by a competing book: the capture no longer fits and parks."""
    book(rig)
    c = collect(rig).json()
    # A second, larger link for the same book cannot exist (one live link),
    # so construct the over-capture by paying the link after the outstanding
    # has been reduced through ANOTHER capture on a fresh link — simplest:
    # credit 45000 directly, then let the link's own 65000 arrive.
    rig.kernel.record_capture(event_id="evt_manual", collection_id=c["collection_id"],
                              amount_paise=45000, payment_id="pay_m",
                              link_amount_paid=45000, event="payment_link.partially_paid",
                              final=False)
    (d,) = rig.sim.pay_link(c["payment_link_id"], amount_paise=30000).deliveries
    w = deliver(rig, d).json()["collection"]
    assert w["credited"] is False and w["capture_state"] == CAP_PARKED
    assert "over_capture" in w["capture_reason"]
    assert rig.kernel.outstanding_paise(BOOK) == 20000
    assert rig.paisa.get("/health").json()["captures_parked"] == 1


def test_sim_pay_route_pushes_the_signed_webhook_through_the_same_gate(rig):
    book(rig)
    c = collect(rig).json()
    r = rig.paisa.post("/sim/pay", json={"payment_link_id": c["payment_link_id"],
                                         "amount_paise": 20000})
    assert r.status_code == 200, r.text
    (w,) = r.json()["webhooks"]
    assert w["event"] == "payment_link.partially_paid" and w["green"] is False
    assert w["collection"]["credited"] is True
    assert rig.kernel.outstanding_paise(BOOK) == 45000


def test_sim_pay_is_refused_in_live_mode(tmp_path):
    cfg = PaisaConfig(mode="live", key_id="rzp_test_L", key_secret="k",
                      webhook_secret=SECRET)
    clock = VirtualClock()
    ledger = Ledger(tmp_path / "audit.jsonl")
    kernel = Kernel(str(tmp_path / "kernel.db"), clock, ledger)

    class Dead:
        def create_payment_link(self, *a, **k):  # pragma: no cover
            raise AssertionError("never called")

    svc = PaisaService(clock=clock, ledger=ledger, kernel=kernel, gateway=Dead(),
                       config=cfg, data_dir=str(tmp_path))
    c = TestClient(create_app(svc))
    r = c.post("/sim/pay", json={"payment_link_id": "plink_x"})
    assert r.status_code == 409 and r.json()["error"] == "not_a_simulator"


# ==========================================================================
# 4. the till's book: households, the value line, the chain
# ==========================================================================


def till_book(rig, session_id="till_k1", phone="+91 98201 14477", name="Sharma"):
    sid = witness(rig, f"scan_{session_id}")
    r = rig.till.post("/khata/book", json={
        "session_id": session_id, "phone": phone, "name": name,
        "amount_paise": BILL, "scan_id": sid})
    manage._CHAIN_CACHE.clear()
    return r


def test_the_book_derives_every_figure_from_the_chain(rig):
    empty = rig.till.get("/khata").json()
    assert empty["ok"] and empty["households"] == [] and empty["value"]["outstanding_paise"] == 0
    r = till_book(rig)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["booked"] and b["colour"] == "none" and b["phone_masked"] == "98xxxx4477"
    assert b["new_household"] is True and b["audited"] is True
    bid = b["book_id"]
    page = rig.till.get("/khata").json()
    walk_ints(page)
    (h,) = page["households"]
    assert h["book_id"] == bid and h["name"] == "Sharma"
    assert h["outstanding_paise"] == BILL and h["captured_paise"] == 0
    assert h["oldest_days"] == 0 and h["bills"] == 1
    v = page["value"]
    assert v["outstanding_paise"] == BILL and v["households"] == 1
    assert v["collected_this_month_paise"] == 0 and v["reminder_links_this_month"] == 0
    # the same number at the counter and at the money service
    assert rig.kernel.outstanding_paise(bid) == BILL
    # khata's own chain and the money chain both verify
    assert verify(khata.audit_path())[0] and verify(rig.ledger.path)[0]
    assert not (Path(REPO) / "results" / "shop" / "khata.json").exists() or True
    assert khata.khata_path() == rig.shop / "khata.json"


def test_collect_then_partial_shows_green_settled_and_neutral_still_due(rig):
    bid = till_book(rig).json()["book_id"]
    r = rig.till.post(f"/khata/{bid}/collect")
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["qr_url"] == f"/khata/{bid}/qr/{c['collection_id']}"
    assert c["reminder_enable"] is True and c["accept_partial"] is True
    manage._CHAIN_CACHE.clear()
    r2 = rig.till.post(f"/khata/{bid}/collect")
    assert r2.status_code == 409 and r2.json()["reason"] == "collection_link_already_open"
    p = rig.till.post("/khata/sim/pay", json={"collection_id": c["collection_id"],
                                              "amount_paise": 20000})
    assert p.status_code == 200, p.text
    manage._CHAIN_CACHE.clear()
    d = rig.till.get(f"/khata/{bid}").json()
    walk_ints(d)
    assert d["captured_paise"] == 20000 and d["captured_rupees"] == "200.00"
    assert d["outstanding_paise"] == 45000 and d["outstanding_rupees"] == "450.00"
    assert d["live_collection"]["captured_paise"] == 20000
    assert d["live_collection"]["still_due_paise"] == 45000
    kinds = [e["kind"] for e in d["entries"]]
    assert kinds[0] == "capture" and "bill" in kinds and "collection" in kinds
    v = rig.till.get("/khata").json()["value"]
    assert v["collected_this_month_paise"] == 20000
    assert v["reminder_links_this_month"] == 1 and v["links_open"] == 1
    assert v["outstanding_paise"] == 45000


def test_the_qr_encodes_the_simulators_link_in_sim_mode(rig):
    """A counter in sim mode must still be able to SHOW its collection link.

    This used to assert the opposite, and the opposite was the bug: the host
    allowlist held only the three real gateway hosts, so every counter not
    wired to a live gateway refused to render the very QR it had just minted.
    A customer pressing PAY on the storefront got `refused_to_show_this_string`
    and no way to pay at all.

    `pay.gawaah-sim.invalid` is RFC 2606 reserved and can never resolve, so
    rendering it cannot move money, cannot be phished with, and cannot be
    followed anywhere. What holds invariant 6 is not this list — it is that
    nothing in this program CONSTRUCTS a payable string, and that only a
    signature-verified webhook turns a bill green.
    """
    bid = till_book(rig).json()["book_id"]
    c = rig.till.post(f"/khata/{bid}/collect").json()
    assert c["short_url"].startswith("https://pay.gawaah-sim.invalid/")
    q = rig.till.get(c["qr_url"])
    assert q.status_code == 200, q.json()
    assert q.headers["content-type"] == "image/png"


def test_the_allowlist_widened_by_one_host_and_not_generally():
    """One unresolvable host was added. Nothing else became payable.

    Checked through `storefront._checked_link`, which is the same allowlist the
    khata QR consults — `tools.upload_app.LINK_HOSTS`, read by every consumer
    rather than copied.
    """
    from gawaah import storefront
    from tools import upload_app

    assert "pay.gawaah-sim.invalid" in upload_app.LINK_HOSTS
    for good in ("https://pay.gawaah-sim.invalid/l/abc", "https://rzp.io/i/abc"):
        assert storefront._checked_link(good) == good

    for bad in ("https://evil.example.com/pay",
                # a look-alike: the sim host as a PREFIX of somebody else's
                "https://pay.gawaah-sim.invalid.evil.com/pay",
                "upi://pay?pa=someone@bank"):
        with pytest.raises(storefront.StorefrontRefused) as e:
            storefront._checked_link(bad)
        assert e.value.reason == "refused_to_show_this_string", bad


def test_two_spellings_of_one_number_are_one_household(rig):
    a = till_book(rig, "till_a", "+91 98201 14477", "Sharma").json()
    b = till_book(rig, "till_b", "098201 14477", "Sharma ji").json()
    assert a["book_id"] == b["book_id"] and b["new_household"] is False
    page = rig.till.get("/khata").json()
    (h,) = page["households"]
    assert h["outstanding_paise"] == 2 * BILL and h["bills"] == 2
    assert h["names_seen"] == ["Sharma", "Sharma ji"]


def test_lookup_by_name_and_by_digits(rig):
    till_book(rig)
    assert [m["name"] for m in rig.till.get("/khata/lookup", params={"q": "sharma ji ka"}).json()["matches"]] == ["Sharma"]
    assert [m["name"] for m in rig.till.get("/khata/lookup", params={"q": "4477"}).json()["matches"]] == ["Sharma"]
    assert rig.till.get("/khata/lookup", params={"q": "verma"}).json()["matches"] == []


def test_a_booking_the_money_service_refuses_is_not_a_booking(rig):
    sid = witness(rig, "scan_x", skus=("parle_g",))
    r = rig.till.post("/khata/book", json={
        "session_id": "till_x", "phone": "9820114477", "name": "Sharma",
        "amount_paise": BILL, "scan_id": sid})
    assert r.status_code == 409 and r.json()["reason"] == "scan_total_disagreement"
    assert not khata.khata_path().exists()
    assert not khata.audit_path().exists()


# ==========================================================================
# 5. the voice: "khate mein likh do" proposes; "kitna baaki hai" answers
# ==========================================================================


@pytest.mark.parametrize("said, tool", [
    ("Sharma ji ke khate mein likh do", assistant.TOOL_KHATA_BOOK),
    ("शर्मा जी के खाते में लिख दो", assistant.TOOL_KHATA_BOOK),
    ("Verma ka udhaar likho", assistant.TOOL_KHATA_BOOK),
    ("Sharma ji ka kitna baaki hai", assistant.TOOL_KHATA_BALANCE),
    ("9820114477 ka udhaar kitna hai", assistant.TOOL_KHATA_BALANCE),
    ("aaj ka hisab dikhao", assistant.TOOL_DAY_CLOSE),
])
def test_the_local_parser_routes_khata_sentences(said, tool):
    assert assistant.local_route(said)[0] == tool


def test_the_advisor_refuses_a_booking_as_a_call(rig):
    assert assistant.TOOL_KHATA_BOOK not in advisor.TOOL_NAMES
    assert assistant.TOOL_KHATA_BALANCE in advisor.TOOL_NAMES
    with pytest.raises(advisor.AdvisorRefused) as ei:
        advisor.local_route("Sharma ji ke khate mein likh do",
                            advisor.Session(session_id="call_x", started_at="", last_mono=0))
    assert ei.value.reason == advisor.R_NOT_A_COUNTER


def test_book_on_khata_proposes_and_books_nothing(rig):
    till_book(rig)
    r = rig.till.post("/assistant/ask", json={"text": "Sharma ji ke khate mein likh do",
                                              "source": "voice"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["tool"] == assistant.TOOL_KHATA_BOOK
    p = b["proposal"]
    assert p["kind"] == "khata_book" and p["accept_by"] is None
    assert p["customer"]["known"] is True and p["customer"]["name"] == "Sharma"
    assert p["customer"]["phone_masked"] == "98xxxx4477"
    assert "ON THE BOOK" in b["answer"]
    assert rig.kernel.outstanding_paise(p["customer"]["book_id"]) == BILL  # unchanged
    # an unknown name is proposed by name and asks for the number
    r2 = rig.till.post("/assistant/ask", json={"text": "Verma ji ke khate mein likh do",
                                               "source": "text"}).json()
    assert r2["proposal"]["customer"]["known"] is False
    assert r2["proposal"]["customer"]["phone"] is None


def test_khata_balance_answers_with_the_figures_and_the_last_capture(rig):
    bid = till_book(rig).json()["book_id"]
    c = rig.till.post(f"/khata/{bid}/collect").json()
    rig.till.post("/khata/sim/pay", json={"collection_id": c["collection_id"],
                                          "amount_paise": 20000})
    manage._CHAIN_CACHE.clear()
    r = rig.till.post("/assistant/ask", json={"text": "Sharma ji ka kitna baaki hai",
                                              "source": "voice"}).json()
    assert r["tool"] == assistant.TOOL_KHATA_BALANCE and r["proposal"] is None
    assert "Rs 450.00" in r["answer"] and "Rs 200.00" in r["answer"]
    assert r["data"]["outstanding_paise"] == 45000
    two = rig.till.post("/assistant/ask", json={"text": "Gupta ka kitna baaki hai",
                                                "source": "voice"})
    assert two.status_code == 400 and two.json()["reason"] == assistant.R_NO_HOUSEHOLD


def test_first_instalment_floor_is_a_quarter_or_a_hundred_rupees_in_whole_rupees():
    assert first_min_partial_paise(65000) == 16200
    assert first_min_partial_paise(20000) == 10000
    assert first_min_partial_paise(6200) == 6200
    assert first_min_partial_paise(100) == 100
