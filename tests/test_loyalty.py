"""gawaah/loyalty.py — points earned on money that arrived, and nothing else.

Five claims this suite exists to make checkable, because each is a claim a
demo can fake:

  1. A BILL EARNS ONLY WHEN THE CHAIN SAYS IT SETTLED. Link-sent, closed but
     unminted, open, absent — every one of those earns zero and the response
     names which. The fixtures write a REAL hash-chained ledger with
     gawaah.ledger.Ledger in the shapes the live modules write.
  2. WHOLE RUPEES, WHOLE POINTS, INTEGER PAISE. 6950 paise at one point per
     rupee is 69 points. Every number in every response is walked and asserted
     not to be a float.
  3. A REDEMPTION NEVER EXCEEDS THE BALANCE — at proposal time AND at apply
     time, so two proposals that each fit cannot both be applied.
  4. THE RULE IN FORCE WHEN A BILL SETTLED IS THE ONE THAT COUNTS. Changing the
     rule today does not rewrite yesterday.
  5. NOTHING HERE TOUCHES results/. Both the environment and the till's cached
     handle are redirected for every test.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from gawaah import loyalty, manage  # noqa: E402
from gawaah.ledger import Ledger, verify  # noqa: E402
from tools import upload_app  # noqa: E402

NOW = datetime.now(timezone.utc)
#: The chain's bills are written in the PAST so a rule set "now" by the
#: endpoint is after them, and a rule the test back-dates is before them.
T0 = NOW - timedelta(days=2)
RULE_BEFORE = (T0 - timedelta(days=1)).isoformat()
RULE_AFTER = (NOW + timedelta(seconds=1)).isoformat()

PHONE = "9876543210"
PHONE_SPACED = "+91 98765 43210"
OTHER = "9123456789"


def _ts(offset_s: int) -> str:
    return (T0 + timedelta(seconds=offset_s)).isoformat()


# ------------------------------------------------------------------ rigging


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A shop and a chain that live and die with the test. Never results/."""
    data = tmp_path / "data"
    shop = data / "shop"
    shop.mkdir(parents=True)
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(data))
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(shop))
    upload_app.set_store_dir(shop)
    manage._CHAIN_CACHE.clear()
    yield
    manage._CHAIN_CACHE.clear()


@pytest.fixture()
def client() -> TestClient:
    """The router mounted the way the orchestrator will mount it: bare."""
    app = FastAPI()
    app.include_router(loyalty.router)
    return TestClient(app)


def _ledger() -> Ledger:
    return Ledger(manage.ledger_path())


def _bill(session_id: str, amount_paise: int, *, at: int = 0,
          close: bool = True, mint: bool = False, settle: bool = False,
          via_session: bool = True) -> None:
    """One session in the chain, in the shapes results/audit.jsonl holds.

    `via_session=False` is a React-till or storefront bill: paisa mints and the
    kernel settles, and the camera session module never writes a line — no
    `done`, no `webhook`. The live chain has both shapes in it.
    """
    led = _ledger()
    clock = at
    if via_session and close:
        led.append(ts=_ts(clock), module="session", event="done",
                   session_id=session_id, reason="intent_requested", lines=1,
                   amber_excluded=0, intent_amount_paise=amount_paise,
                   **{"from": "BASKET_OPEN", "to": "AWAITING_SETTLEMENT"},
                   total_paise=amount_paise)
        clock += 1
    if mint:
        led.append(ts=_ts(clock), module="paisa", event="intent.minted",
                   session_id=session_id, minted=True, replayed=False,
                   amount_paise=amount_paise, amber_items=[],
                   priced_items=["x"], payment_link_id=f"plink_{session_id}")
        clock += 1
    if settle:
        led.append(ts=_ts(clock), module="kernel", event="intent.settled",
                   session_id=session_id, amount_paise=amount_paise,
                   payment_id=f"pay_{session_id}", from_state="CALLING",
                   to_state="SETTLED", reason=None)
        clock += 1
        if via_session:
            led.append(ts=_ts(clock), module="session", event="webhook",
                       session_id=session_id, reason="settled_green",
                       razorpay_event="payment.captured",
                       event_id=f"evt_{session_id}",
                       webhook_amount_paise=amount_paise, money_authorised=True,
                       **{"from": "AWAITING_SETTLEMENT", "to": "PAID"},
                       total_paise=amount_paise)
            clock += 1
            # The live chain carries a SECOND webhook the session ignored,
            # with a null amount. It must not double-count or blank the figure.
            led.append(ts=_ts(clock), module="session", event="webhook",
                       session_id=session_id,
                       reason="webhook_after_settlement_ignored",
                       razorpay_event="payment_link.paid",
                       event_id=f"evt2_{session_id}", webhook_amount_paise=None,
                       money_authorised=True,
                       **{"from": "PAID", "to": "PAID"}, total_paise=amount_paise)
    manage._CHAIN_CACHE.clear()


def _order(order_id: str, phone: str, total_paise: int) -> str:
    """A storefront order on disk, with the session id the storefront mints."""
    d = Path(os.environ["GAWAAH_SHOP_DIR"]) / "orders"
    d.mkdir(parents=True, exist_ok=True)
    sid = f"shop_{order_id}"
    doc = {
        "format": 1, "order_id": order_id, "at": _ts(0), "status": "new",
        "status_changed_at": _ts(0), "history": [],
        "customer": {"name": "A", "phone": phone, "address": "12 Lane, Town"},
        "lines": [{"sku_id": "x", "name": "x", "qty": 1,
                   "unit_paise": total_paise, "line_paise": total_paise}],
        "total_paise": total_paise, "total_rupees": "0.00",
        "payment": {"session_id": sid, "paid": False, "state": None,
                    "short_url": None, "minted_at": None},
    }
    (d / f"{order_id}.json").write_text(json.dumps(doc), encoding="utf-8")
    return sid


def _rules(client: TestClient, ppr: int, ppp: int) -> dict:
    r = client.post("/loyalty/rules",
                    json={"points_per_rupee": ppr, "paise_per_point": ppp})
    assert r.status_code == 200, r.text
    return r.json()


def _attach(client: TestClient, sid: str, phone: str) -> dict:
    r = client.post("/loyalty/attach", json={"session_id": sid, "phone": phone})
    assert r.status_code == 200, r.text
    return r.json()


def _balance(client: TestClient, phone: str) -> dict:
    r = client.get(f"/loyalty/balance/{phone}")
    assert r.status_code == 200, r.text
    return r.json()


def _refused(r, reason: str) -> dict:
    assert r.status_code in (400, 404), r.text
    doc = r.json()
    assert doc["ok"] is False
    assert doc["settles_money"] is False
    assert doc["reason"] == reason, doc
    assert isinstance(doc["detail"], str) and doc["detail"]
    return doc


def _no_floats(obj: Any, path: str = "$") -> None:
    if isinstance(obj, float):
        raise AssertionError(f"float at {path}: {obj!r}")
    if isinstance(obj, dict):
        for k, v in obj.items():
            _no_floats(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _no_floats(v, f"{path}[{i}]")


# ======================================================================= rules


def test_rules_default_to_off_and_say_so(client):
    body = client.get("/loyalty/rules").json()
    assert body["ok"] is True
    assert body["settles_money"] is False
    assert body["rules"] == {"points_per_rupee": 0, "paise_per_point": 0,
                             "set_at": None, "on": False}
    assert body["example"] is None
    assert "off" in body["note"]


def test_rules_set_and_read_back_with_a_worked_example(client):
    body = _rules(client, 2, 25)
    assert body["rules"]["points_per_rupee"] == 2
    assert body["rules"]["paise_per_point"] == 25
    assert body["rules"]["on"] is True
    assert body["audited"] is True
    assert body["was"]["points_per_rupee"] == 0
    # 100 points at 25 paise each is 2500 paise, computed server-side.
    assert body["example"] == {"points": 100, "value_paise": 2500,
                               "value_rupees": "25.00"}
    again = client.get("/loyalty/rules").json()
    assert again["rules"]["points_per_rupee"] == 2
    assert again["history_count"] == 1


@pytest.mark.parametrize("bad", [1.5, True, "1.5", "two", None, [1]])
def test_rules_refuse_anything_that_is_not_a_whole_number(client, bad):
    r = client.post("/loyalty/rules",
                    json={"points_per_rupee": bad, "paise_per_point": 10})
    _refused(r, loyalty.R_RULE_NOT_INTEGER)


def test_rules_refuse_negative_and_over_cap_by_name(client):
    _refused(client.post("/loyalty/rules", json={"points_per_rupee": -1,
                                                 "paise_per_point": 10}),
             loyalty.R_RULE_OUT_OF_RANGE)
    _refused(client.post("/loyalty/rules",
                         json={"points_per_rupee": 1,
                               "paise_per_point": loyalty.MAX_PAISE_PER_POINT + 1}),
             loyalty.R_RULE_OUT_OF_RANGE)


def test_rules_refuse_a_missing_field_and_a_non_object_body(client):
    _refused(client.post("/loyalty/rules", json={"points_per_rupee": 1}),
             loyalty.R_RULE_MISSING)
    _refused(client.post("/loyalty/rules", json=[1, 2]), loyalty.R_BAD_BODY)
    r = client.post("/loyalty/rules", content=b"not json",
                    headers={"Content-Type": "application/json"})
    _refused(r, loyalty.R_BAD_BODY)


# ===================================================================== earning


def test_a_settled_counter_bill_earns_whole_rupees_times_the_rule(client):
    loyalty.save_rules(1, 25, at=RULE_BEFORE)
    _bill("counter_1", 6950, mint=True, settle=True)
    a = _attach(client, "counter_1", PHONE)
    assert a["bill"]["settled"] is True
    assert a["bill"]["settled_paise"] == 6950
    assert a["earns"] == {"points": 69, "why": loyalty.WHY_EARNED,
                          "said": loyalty.WHY_SAID[loyalty.WHY_EARNED]}
    b = _balance(client, PHONE)
    assert b["earned_points"] == 69          # 69 whole rupees; the 50 paise earns nothing
    assert b["balance_points"] == 69
    assert b["settled_paise"] == 6950
    assert b["bills_settled"] == 1
    assert b["known"] is True
    _no_floats(b)


def test_a_link_sent_bill_earns_nothing_and_says_why(client):
    loyalty.save_rules(1, 25, at=RULE_BEFORE)
    _bill("counter_2", 5000, mint=True, settle=False)
    a = _attach(client, "counter_2", PHONE)
    assert a["bill"]["minted"] is True and a["bill"]["settled"] is False
    assert a["earns"]["points"] == 0
    assert a["earns"]["why"] == loyalty.WHY_LINK_SENT
    b = _balance(client, PHONE)
    assert b["balance_points"] == 0
    assert b["bills_awaiting"] == 1
    led = client.get(f"/loyalty/ledger/{PHONE}").json()
    assert led["count"] == 1
    assert led["entries"][0]["why"] == loyalty.WHY_LINK_SENT
    assert "not money that arrived" in led["entries"][0]["said"]


def test_closed_unminted_and_open_bills_are_each_named(client):
    loyalty.save_rules(1, 25, at=RULE_BEFORE)
    _bill("closed_only", 5000, mint=False)
    _attach(client, "closed_only", PHONE)
    led = client.get(f"/loyalty/ledger/{PHONE}").json()
    assert led["entries"][0]["why"] == loyalty.WHY_CLOSED_NOT_MINTED
    assert led["balance_points"] == 0


def test_a_session_not_yet_in_the_chain_is_accepted_and_earns_when_it_settles(client):
    """The till knows its session id before the first packet is priced."""
    loyalty.save_rules(1, 25, at=RULE_BEFORE)
    a = _attach(client, "till_abc_123", PHONE)
    assert a["bill"]["found"] is False
    assert a["earns"]["why"] == loyalty.WHY_NOT_IN_LEDGER
    assert _balance(client, PHONE)["bills_not_in_ledger"] == 1
    _bill("till_abc_123", 12000, mint=True, settle=True, via_session=False)
    b = _balance(client, PHONE)
    assert b["earned_points"] == 120
    assert b["bills_not_in_ledger"] == 0


def test_a_bill_settled_before_any_rule_earns_nothing_and_says_so(client):
    """Rules are not applied backwards. The endpoint dates its rule NOW and the
    fixture's bill settled two days ago."""
    _bill("old_bill", 10000, mint=True, settle=True)
    _rules(client, 1, 25)
    _attach(client, "old_bill", PHONE)
    led = client.get(f"/loyalty/ledger/{PHONE}").json()
    assert led["balance_points"] == 0
    assert led["entries"][0]["why"] == loyalty.WHY_NO_RULE
    assert led["entries"][0]["bill"]["settled"] is True


def test_the_rule_in_force_at_settlement_counts_not_todays(client):
    loyalty.save_rules(1, 25, at=RULE_BEFORE)
    _bill("bill_a", 10000, mint=True, settle=True, at=0)
    _attach(client, "bill_a", PHONE)
    assert _balance(client, PHONE)["earned_points"] == 100
    # Doubling the rule today does not rewrite a bill that settled under 1/rupee.
    _rules(client, 2, 25)
    assert _balance(client, PHONE)["earned_points"] == 100
    # A rule set BETWEEN two bills: the first keeps its rule, the second earns
    # at the new one.
    loyalty.save_rules(3, 25, at=_ts(5))
    _bill("bill_b", 10000, mint=True, settle=True, at=10)
    _attach(client, "bill_b", PHONE)
    assert _balance(client, PHONE)["earned_points"] == 100 + 300
    led = client.get(f"/loyalty/ledger/{PHONE}").json()
    by_sid = {e["session_id"]: e for e in led["entries"] if e["kind"] == "earn"}
    assert by_sid["bill_a"]["points_per_rupee"] == 1
    assert by_sid["bill_b"]["points_per_rupee"] == 3


def test_under_a_rupee_earns_nothing_and_names_it(client):
    loyalty.save_rules(5, 25, at=RULE_BEFORE)
    _bill("tiny", 99, mint=True, settle=True)
    _attach(client, "tiny", PHONE)
    led = client.get(f"/loyalty/ledger/{PHONE}").json()
    assert led["balance_points"] == 0
    assert led["entries"][0]["why"] == loyalty.WHY_UNDER_A_RUPEE


def test_three_spellings_of_one_number_are_one_balance(client):
    loyalty.save_rules(1, 25, at=RULE_BEFORE)
    _bill("s1", 10000, mint=True, settle=True, at=0)
    _bill("s2", 20000, mint=True, settle=True, at=20)
    _attach(client, "s1", PHONE_SPACED)
    _attach(client, "s2", "09876543210")
    assert _balance(client, PHONE)["earned_points"] == 300
    assert _balance(client, PHONE_SPACED)["earned_points"] == 300


def test_a_storefront_order_that_settled_earns_without_anybody_typing(client):
    loyalty.save_rules(1, 25, at=RULE_BEFORE)
    sid = _order("ord_0123456789ab", PHONE_SPACED, 7500)
    _bill(sid, 7500, mint=True, settle=True, via_session=False)
    b = _balance(client, PHONE)
    assert b["earned_points"] == 75
    led = client.get(f"/loyalty/ledger/{PHONE}").json()
    e = led["entries"][0]
    assert e["source"] == loyalty.SOURCE_STOREFRONT
    assert e["order_id"] == "ord_0123456789ab"
    assert e["bill"]["settled_by"] == "kernel"


def test_a_storefront_order_that_was_only_link_sent_earns_nothing(client):
    loyalty.save_rules(1, 25, at=RULE_BEFORE)
    sid = _order("ord_0123456789ac", PHONE, 7500)
    _bill(sid, 7500, mint=True, settle=False, via_session=False)
    b = _balance(client, PHONE)
    assert b["earned_points"] == 0
    assert b["bills_awaiting"] == 1
    led = client.get(f"/loyalty/ledger/{PHONE}").json()
    assert led["entries"][0]["why"] == loyalty.WHY_LINK_SENT


def test_the_ignored_second_webhook_neither_doubles_nor_blanks_the_figure(client):
    """The live chain carries a second `webhook` line per bill with a null
    amount. One bill is one earn entry with the real amount."""
    loyalty.save_rules(1, 25, at=RULE_BEFORE)
    _bill("dup", 6900, mint=True, settle=True)
    _attach(client, "dup", PHONE)
    led = client.get(f"/loyalty/ledger/{PHONE}").json()
    assert led["count"] == 1
    assert led["entries"][0]["bill"]["settled_paise"] == 6900
    assert led["earned_points"] == 69


# ==================================================================== attaching


def test_attach_refuses_moving_a_settled_bill_to_another_number(client):
    loyalty.save_rules(1, 25, at=RULE_BEFORE)
    _bill("paid", 10000, mint=True, settle=True)
    _attach(client, "paid", PHONE)
    r = client.post("/loyalty/attach", json={"session_id": "paid", "phone": OTHER})
    _refused(r, loyalty.R_CREDITED_ELSEWHERE)
    assert _balance(client, PHONE)["earned_points"] == 100
    assert _balance(client, OTHER)["earned_points"] == 0


def test_attach_to_the_same_number_twice_changes_nothing(client):
    loyalty.save_rules(1, 25, at=RULE_BEFORE)
    _bill("once", 10000, mint=True, settle=True)
    first = _attach(client, "once", PHONE)
    assert first["changed"] is True and first["audited"] is True
    again = _attach(client, "once", PHONE_SPACED)
    assert again["changed"] is False
    assert _balance(client, PHONE)["earned_points"] == 100
    assert client.get(f"/loyalty/ledger/{PHONE}").json()["count"] == 1


def test_an_unsettled_bill_can_be_moved_to_the_right_number(client):
    loyalty.save_rules(1, 25, at=RULE_BEFORE)
    _bill("typo", 10000, mint=True, settle=False)
    _attach(client, "typo", OTHER)
    _attach(client, "typo", PHONE)
    _bill("typo", 10000, mint=False, settle=True)
    assert _balance(client, PHONE)["earned_points"] == 100
    assert _balance(client, OTHER)["earned_points"] == 0


@pytest.mark.parametrize("sid,reason", [
    ("", loyalty.R_NO_SESSION), (None, loyalty.R_NO_SESSION),
    ("../../catalog", loyalty.R_BAD_SESSION), ("a b", loyalty.R_BAD_SESSION),
    ("x" * 81, loyalty.R_BAD_SESSION),
])
def test_attach_refuses_a_bad_session_id_by_name(client, sid, reason):
    _refused(client.post("/loyalty/attach", json={"session_id": sid, "phone": PHONE}),
             reason)


@pytest.mark.parametrize("phone,reason", [
    ("", loyalty.R_NO_PHONE), (None, loyalty.R_NO_PHONE),
    ("abc", loyalty.R_BAD_PHONE), ("12345", loyalty.R_SHORT_PHONE),
    ("1" * 25, loyalty.R_PHONE_TOO_LONG), ("1234567890123456", loyalty.R_BAD_PHONE),
])
def test_phones_are_refused_by_name(client, phone, reason):
    _refused(client.post("/loyalty/attach", json={"session_id": "s", "phone": phone}),
             reason)
    if phone:
        _refused(client.get(f"/loyalty/balance/{phone}"), reason)


# =================================================================== redeeming


def _earn(client: TestClient, points: int, sid: str = "earned") -> None:
    loyalty.save_rules(1, 25, at=RULE_BEFORE)
    _bill(sid, points * 100, mint=True, settle=True)
    _attach(client, sid, PHONE)


def test_redeem_proposes_a_line_and_deducts_nothing(client):
    _earn(client, 200)
    r = client.post("/loyalty/redeem", json={"phone": PHONE, "points": 80})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is False
    assert body["redemption"]["points"] == 80
    assert body["redemption"]["value_paise"] == 80 * 25
    assert body["line"] == {"kind": "loyalty_redemption",
                            "redemption_id": body["redemption"]["redemption_id"],
                            "label": "Loyalty points (80 pts)",
                            "off_paise": 2000, "off_rupees": "20.00", "points": 80}
    assert body["balance_before_points"] == 200
    assert body["balance_if_applied_points"] == 120
    assert any("scan_total_disagreement" in s for s in body["till_must"])
    b = _balance(client, PHONE)
    assert b["balance_points"] == 200            # nothing left yet
    assert b["proposed_points"] == 80
    _no_floats(body)


def test_redeem_refuses_more_than_the_balance_by_name(client):
    _earn(client, 50)
    doc = _refused(client.post("/loyalty/redeem",
                               json={"phone": PHONE, "points": 51}),
                   loyalty.R_EXCEEDS_BALANCE)
    assert "50 points" in doc["detail"] and "51" in doc["detail"]
    assert _balance(client, PHONE)["proposed_points"] == 0


def test_redeem_refuses_an_unknown_number_as_exceeding_a_zero_balance(client):
    loyalty.save_rules(1, 25, at=RULE_BEFORE)
    _refused(client.post("/loyalty/redeem", json={"phone": OTHER, "points": 1}),
             loyalty.R_EXCEEDS_BALANCE)


@pytest.mark.parametrize("pts,reason", [
    (None, loyalty.R_POINTS_MISSING), (0, loyalty.R_POINTS_NOT_POSITIVE),
    (-5, loyalty.R_POINTS_NOT_POSITIVE), (2.5, loyalty.R_POINTS_NOT_INTEGER),
    (True, loyalty.R_POINTS_NOT_INTEGER), ("lots", loyalty.R_POINTS_NOT_INTEGER),
    (loyalty.MAX_POINTS_PER_REDEMPTION + 1, loyalty.R_POINTS_TOO_MANY),
])
def test_redeem_refuses_bad_points_by_name(client, pts, reason):
    _earn(client, 10)
    _refused(client.post("/loyalty/redeem", json={"phone": PHONE, "points": pts}),
             reason)


def test_redeem_refuses_when_no_rule_is_set_or_a_point_is_worth_nothing(client):
    _refused(client.post("/loyalty/redeem", json={"phone": PHONE, "points": 1}),
             loyalty.R_NO_RULE)
    loyalty.save_rules(1, 0, at=RULE_BEFORE)
    _bill("z", 10000, mint=True, settle=True)
    _attach(client, "z", PHONE)
    _refused(client.post("/loyalty/redeem", json={"phone": PHONE, "points": 1}),
             loyalty.R_POINT_WORTHLESS)


def test_apply_is_the_debit_and_is_chained(client):
    _earn(client, 200)
    rid = client.post("/loyalty/redeem",
                      json={"phone": PHONE, "points": 80}).json()["redemption"]["redemption_id"]
    r = client.post(f"/loyalty/redemptions/{rid}/apply",
                    json={"session_id": "till_new_bill"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is True
    assert body["balance_before_points"] == 200
    assert body["balance_after_points"] == 120
    assert body["audited"] is True
    b = _balance(client, PHONE)
    assert b["balance_points"] == 120
    assert b["redeemed_points"] == 80
    assert b["proposed_points"] == 0
    # The module's own chain verifies, and the debit is on it with the value.
    ok, lines, _, err = verify(loyalty.audit_path())
    assert ok and err is None and lines >= 3
    events = [json.loads(l) for l in loyalty.audit_path().read_text().splitlines()]
    applied = [e for e in events if e["event"] == "redemption.applied"]
    assert applied and applied[0]["points"] == 80 and applied[0]["value_paise"] == 2000
    assert applied[0]["session_id"] == "till_new_bill"
    # No phone number reaches the chain, only its tail.
    assert PHONE not in loyalty.audit_path().read_text()
    assert applied[0]["phone_tail"] == "3210"


def test_apply_twice_is_refused_by_name(client):
    _earn(client, 100)
    rid = client.post("/loyalty/redeem",
                      json={"phone": PHONE, "points": 10}).json()["redemption"]["redemption_id"]
    assert client.post(f"/loyalty/redemptions/{rid}/apply",
                       json={"session_id": "b1"}).status_code == 200
    _refused(client.post(f"/loyalty/redemptions/{rid}/apply",
                         json={"session_id": "b2"}),
             loyalty.R_ALREADY_APPLIED)
    assert _balance(client, PHONE)["balance_points"] == 90


def test_two_proposals_that_each_fit_cannot_both_be_applied(client):
    _earn(client, 100)
    a = client.post("/loyalty/redeem", json={"phone": PHONE, "points": 70}).json()
    b = client.post("/loyalty/redeem", json={"phone": PHONE, "points": 70}).json()
    assert a["ok"] and b["ok"]                     # each fits a balance of 100
    ra = a["redemption"]["redemption_id"]
    rb = b["redemption"]["redemption_id"]
    assert client.post(f"/loyalty/redemptions/{ra}/apply",
                       json={"session_id": "b1"}).status_code == 200
    _refused(client.post(f"/loyalty/redemptions/{rb}/apply",
                         json={"session_id": "b2"}),
             loyalty.R_EXCEEDS_BALANCE)
    bal = _balance(client, PHONE)
    assert bal["balance_points"] == 30
    assert bal["proposed_points"] == 70            # still listed, never deducted


def test_apply_refuses_a_bill_that_already_settled(client):
    """The gateway took the full amount; a discount after the fact is one
    nobody received."""
    _earn(client, 100)
    _bill("already_paid", 5000, mint=True, settle=True, at=50)
    rid = client.post("/loyalty/redeem",
                      json={"phone": PHONE, "points": 10}).json()["redemption"]["redemption_id"]
    _refused(client.post(f"/loyalty/redemptions/{rid}/apply",
                         json={"session_id": "already_paid"}),
             loyalty.R_BILL_SETTLED)
    assert _balance(client, PHONE)["balance_points"] == 100


def test_apply_refuses_a_bad_session_and_an_unknown_id(client):
    _earn(client, 10)
    rid = client.post("/loyalty/redeem",
                      json={"phone": PHONE, "points": 1}).json()["redemption"]["redemption_id"]
    _refused(client.post(f"/loyalty/redemptions/{rid}/apply", json={"session_id": ""}),
             loyalty.R_NO_SESSION)
    _refused(client.post("/loyalty/redemptions/red_000000000000/apply",
                         json={"session_id": "s"}),
             loyalty.R_NO_REDEMPTION)
    _refused(client.post("/loyalty/redemptions/red_notahexid1/apply",
                         json={"session_id": "s"}),
             loyalty.R_BAD_REDEMPTION_ID)
    _refused(client.get("/loyalty/redemptions/red_ZZZZZZZZZZZZ"),
             loyalty.R_BAD_REDEMPTION_ID)


def test_a_redemption_reads_back_and_an_unknown_one_is_a_404(client):
    _earn(client, 10)
    rid = client.post("/loyalty/redeem",
                      json={"phone": PHONE, "points": 4}).json()["redemption"]["redemption_id"]
    body = client.get(f"/loyalty/redemptions/{rid}").json()
    assert body["ok"] and body["redemption"]["points"] == 4 and body["applied"] is False
    r = client.get("/loyalty/redemptions/red_000000000000")
    assert r.status_code == 404
    _refused(r, loyalty.R_NO_REDEMPTION)


def test_a_settled_bill_after_a_redemption_earns_on_what_actually_arrived(client):
    """Points come off the settled amount, which is already after any discount
    the gateway saw. Nothing here re-adds the redemption."""
    _earn(client, 100)
    rid = client.post("/loyalty/redeem",
                      json={"phone": PHONE, "points": 20}).json()["redemption"]["redemption_id"]
    client.post(f"/loyalty/redemptions/{rid}/apply", json={"session_id": "next"})
    _attach(client, "next", PHONE)
    _bill("next", 9500, mint=True, settle=True, at=100)    # ₹100 less ₹5 off
    b = _balance(client, PHONE)
    assert b["earned_points"] == 100 + 95
    assert b["redeemed_points"] == 20
    assert b["balance_points"] == 175
    led = client.get(f"/loyalty/ledger/{PHONE}").json()
    red = [e for e in led["entries"] if e["kind"] == "redeem"][0]
    assert red["applied"] is True and red["bill"]["settled"] is True


# ====================================================================== ledger


def test_ledger_is_newest_first_and_carries_no_floats(client):
    loyalty.save_rules(1, 25, at=RULE_BEFORE)
    _bill("first", 10000, mint=True, settle=True, at=0)
    _bill("second", 20000, mint=True, settle=True, at=100)
    _attach(client, "first", PHONE)
    _attach(client, "second", PHONE)
    led = client.get(f"/loyalty/ledger/{PHONE}").json()
    assert [e["session_id"] for e in led["entries"]] == ["second", "first"]
    assert led["balance_points"] == 300
    assert led["why"][loyalty.WHY_LINK_SENT]
    _no_floats(led)


def test_members_lists_every_number_with_history_highest_balance_first(client):
    loyalty.save_rules(1, 25, at=RULE_BEFORE)
    _bill("m1", 10000, mint=True, settle=True, at=0)
    _bill("m2", 30000, mint=True, settle=True, at=10)
    _attach(client, "m1", PHONE)
    _attach(client, "m2", OTHER)
    sid = _order("ord_0123456789ad", "+91 91111 22222", 5000)
    _bill(sid, 5000, mint=True, settle=True, via_session=False, at=20)
    body = client.get("/loyalty/members").json()
    assert body["ok"] and body["count"] == 3
    assert [m["phone"] for m in body["members"]] == [OTHER, PHONE, "9111122222"]
    assert body["members"][0]["balance_points"] == 300
    assert body["members"][0]["balance_value_paise"] == 300 * 25
    assert "address" not in json.dumps(body) and "name" not in body["members"][0]
    _no_floats(body)


# ======================================================================= chain


def test_an_absent_money_chain_is_an_empty_account_not_an_error(client):
    assert not manage.ledger_path().exists()
    b = _balance(client, PHONE)
    assert b["balance_points"] == 0 and b["known"] is False
    assert b["chain"]["exists"] is False and b["chain"]["ok"] is True


def test_a_broken_money_chain_stops_counting_at_the_break_and_says_so(client):
    loyalty.save_rules(1, 25, at=RULE_BEFORE)
    _bill("good", 10000, mint=True, settle=True, at=0)
    _bill("after_break", 50000, mint=True, settle=True, at=100)
    _attach(client, "good", PHONE)
    _attach(client, "after_break", PHONE)
    assert _balance(client, PHONE)["earned_points"] == 600
    # Corrupt a line in the middle: everything after it is not evidence.
    p = manage.ledger_path()
    lines = p.read_text().splitlines()
    idx = next(i for i, l in enumerate(lines)
               if '"after_break"' in l and '"intent.minted"' in l)
    assert '"amount_paise": 50000' in lines[idx]
    lines[idx] = lines[idx].replace('"amount_paise": 50000', '"amount_paise": 1')
    p.write_text("\n".join(lines) + "\n")
    manage._CHAIN_CACHE.clear()
    b = _balance(client, PHONE)
    assert b["chain"]["ok"] is False and b["chain"]["error"]
    assert b["earned_points"] == 100


def test_the_chain_is_read_from_manage_and_refuses_when_it_cannot_be(client, monkeypatch):
    def boom():
        raise OSError("disk gone")
    monkeypatch.setattr(manage, "read_chain", boom)
    _refused(client.get(f"/loyalty/balance/{PHONE}"), loyalty.R_CHAIN_UNAVAILABLE)


def test_a_corrupt_loyalty_file_is_refused_not_treated_as_blank(client):
    loyalty.loyalty_path().write_text("{not json", encoding="utf-8")
    _refused(client.get("/loyalty/rules"), loyalty.R_FILE_UNREADABLE)
    _refused(client.get(f"/loyalty/balance/{PHONE}"), loyalty.R_FILE_UNREADABLE)
    h = client.get("/loyalty/health").json()
    assert h["ok"] is True and h["file_error"]


# ===================================================================== hygiene


def test_nothing_is_written_outside_the_test_shop(client, tmp_path):
    _earn(client, 10)
    rid = client.post("/loyalty/redeem",
                      json={"phone": PHONE, "points": 1}).json()["redemption"]["redemption_id"]
    client.post(f"/loyalty/redemptions/{rid}/apply", json={"session_id": "s"})
    shop = Path(os.environ["GAWAAH_SHOP_DIR"])
    assert loyalty.loyalty_path() == shop / "loyalty.json"
    assert loyalty.audit_path() == shop / "loyalty.audit.jsonl"
    assert str(loyalty.shop_dir()).startswith(str(tmp_path))
    assert not (Path(REPO) / "results" / "shop" / "loyalty.json").exists() or \
        (Path(REPO) / "results" / "shop" / "loyalty.json").stat().st_mtime < NOW.timestamp() - 5


def test_health_names_both_files_and_both_chains(client):
    body = client.get("/loyalty/health").json()
    assert body["ok"] and body["module"] == "loyalty"
    assert body["file"].endswith("loyalty.json")
    assert body["audit_file"].endswith("loyalty.audit.jsonl")
    assert body["audit"]["ok"] is True and body["audit"]["lines"] == 0
    assert body["rules"]["on"] is False
    assert "settled" in body["earns_on"]


def test_router_paths_are_absolute_and_carry_no_prefix():
    assert loyalty.router.prefix == ""
    paths = {r.path for r in loyalty.router.routes}
    assert paths == {
        "/loyalty/rules", "/loyalty/balance/{phone}", "/loyalty/ledger/{phone}",
        "/loyalty/members", "/loyalty/attach", "/loyalty/redeem",
        "/loyalty/redemptions/{redemption_id}",
        "/loyalty/redemptions/{redemption_id}/apply", "/loyalty/health",
    }


def test_every_response_says_it_settles_no_money(client):
    _earn(client, 10)
    for path in ("/loyalty/rules", f"/loyalty/balance/{PHONE}",
                 f"/loyalty/ledger/{PHONE}", "/loyalty/members", "/loyalty/health"):
        assert client.get(path).json()["settles_money"] is False, path


def test_the_module_holds_no_forgery_primitive():
    src = Path(loyalty.__file__).read_text(encoding="utf-8")
    assert "upi:" not in src.lower()
    assert "short_url" not in src
    assert "rzp.io" not in src
