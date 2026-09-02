"""gawaah/share.py — a bill, an order or a reorder list, on WhatsApp.

This module composes a `wa.me` deep link. It is the only place in the program
that hands a phone an address on somebody else's host, and the text riding on
that address is built out of THREE STRINGS A PERSON OTHER THAN THE SHOPKEEPER
CAN WRITE: a customer's name typed into the storefront from the open internet,
a supplier's name, and a product name. So the suite is organised around the
ways this could be worse than the WhatsApp message a shopkeeper types by hand:

  1. It could carry a payment payload         -> the forgery tests. A customer
     out of the shop in the shop's own voice     called `upi://pay?pa=…` is a
                                                 real order this counter will
                                                 accept, and the message it
                                                 produces is refused.

  2. It could open a chat with the wrong       -> the phone tests, one per named
     person, or with nobody                      refusal, all asserting nothing
                                                 was composed.

  3. It could print a number nobody derived    -> the receipt and reorder tests.
                                                 Every rupee comes from
                                                 receipts.build_receipt over a
                                                 real hash chain; the reorder
                                                 message carries NO order
                                                 quantity, because a quantity
                                                 needs a case size this counter
                                                 is never told.

  4. It could claim it sent something          -> the limits test.

Every fixture writes a REAL hash-chained ledger with gawaah.ledger.Ledger, a
real catalogue sidecar, and real orders through the storefront's own endpoint,
so the shapes the code reads are the shapes the product writes.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import manage, receipts, share, stock, storefront  # noqa: E402
from gawaah.ledger import Ledger  # noqa: E402
from gawaah.share import (  # noqa: E402
    MAX_LINES_IN_A_MESSAGE,
    MAX_MESSAGE_CHARS,
    R_BAD_BODY,
    R_BAD_SUPPLIER_ID,
    R_MODULE_UNAVAILABLE,
    R_NO_SUPPLIER,
    R_NOTHING_IS_LOW,
    R_PHONE_MISSING,
    R_PHONE_NOT_A_MOBILE,
    R_PHONE_NOT_A_NUMBER,
    R_PHONE_NOT_INDIA,
    R_PHONE_NOT_TEXT,
    R_PHONE_TOO_LONG,
    R_PHONE_TOO_SHORT,
    R_REFUSED_LINK,
    R_REFUSED_MESSAGE,
    R_TOO_LONG,
    SHARE_HOSTS,
    ShareRefused,
    to_e164,
)
from tools import upload_app  # noqa: E402

T0 = datetime(2026, 8, 29, 5, 0, 0, tzinfo=timezone.utc)

BISCUIT = ("parle_g_200g", "Parle-G 200g", 2145)
SOAP = ("lifebuoy_125g", "Lifebuoy 125g", 3950)


def _ts(offset_s: int) -> str:
    return (T0 + timedelta(seconds=offset_s)).isoformat()


# ------------------------------------------------------------------ fixtures


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Nothing in this suite may see, let alone write, results/.

    Three overrides and not one. `GAWAAH_DATA_DIR` moves the audit chain the
    receipt is rebuilt from; `GAWAAH_SHOP_DIR` moves the catalogue, the orders
    and the shop profile; and `set_store_dir` moves the till's own CACHED
    handle, which the storefront reads through and which no environment
    variable can reach once the module has been imported by another test in
    the same session. A harness that honoured only one of these once destroyed
    a live catalogue, and that is a mistake with no undo.
    """
    data = tmp_path / "data"
    shop = data / "shop"
    shop.mkdir(parents=True)
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(data))
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(shop))
    monkeypatch.delenv("GAWAAH_SCAN_DIR", raising=False)
    monkeypatch.delenv("GAWAAH_CODES_FILE", raising=False)
    upload_app.set_store_dir(shop)
    manage._CHAIN_CACHE.clear()
    yield
    manage._CHAIN_CACHE.clear()


@pytest.fixture
def client() -> TestClient:
    """The router mounted the way the orchestrator will mount it: bare."""
    app = FastAPI()
    app.include_router(share.router)
    return TestClient(app)


@pytest.fixture
def full(client: TestClient) -> TestClient:
    """Share mounted alongside the modules it reads, for the order path."""
    app = FastAPI()
    app.include_router(share.router)
    app.include_router(storefront.router)
    app.include_router(stock.router)
    return TestClient(app)


# --------------------------------------------------------------- the chain


def _ledger() -> Ledger:
    return Ledger(manage.ledger_path())


def _bill(session_id: str, lines: list[tuple[str, int]], *,
          amber: tuple[str, ...] = (), settle: str = "none",
          at: int = 0) -> int:
    """One closed bill, written the way session.py writes it.

    Copied from tests/test_receipts.py rather than invented: receipts.py folds
    this through manage.bills_from, so a fixture writing a shape the real
    session module never writes would test nothing at all.
    """
    led = _ledger()
    clock = at
    running = 0
    led.append(ts=_ts(clock), module="session", event="session",
               session_id=session_id, reason="session_opened",
               **{"from": "SETUP", "to": "SETUP"}, total_paise=0)
    for i, (sku, price) in enumerate(lines):
        clock += 1
        running += price
        led.append(ts=_ts(clock), module="session", event="exit",
                   session_id=session_id, reason="exit_crossing_committed",
                   item_id=f"{sku}#{i}", price_paise=price, abstained=False,
                   excluded_from_total=False,
                   **{"from": "PRICED", "to": "BASKET_OPEN"},
                   total_paise=running)
    for sku in amber:
        clock += 1
        led.append(ts=_ts(clock), module="session", event="exit",
                   session_id=session_id,
                   reason="exit_crossing_committed_amber_excluded",
                   item_id=sku, abstained=True, excluded_from_total=True,
                   **{"from": "AMBER", "to": "BASKET_OPEN"},
                   total_paise=running)
    clock += 1
    led.append(ts=_ts(clock), module="session", event="done",
               session_id=session_id, reason="intent_requested",
               lines=len(lines), amber_excluded=len(amber),
               intent_amount_paise=running,
               **{"from": "BASKET_OPEN", "to": "AWAITING_SETTLEMENT"},
               total_paise=running)
    if settle in ("webhook", "kernel"):
        clock += 1
        led.append(ts=_ts(clock), module="kernel", event="intent.settled",
                   session_id=session_id, amount_paise=running,
                   payment_id=f"pay_{session_id}", from_state="CALLING",
                   to_state="SETTLED", reason=None)
    if settle == "webhook":
        clock += 1
        led.append(ts=_ts(clock), module="session", event="webhook",
                   session_id=session_id, reason="settled_green",
                   razorpay_event="payment.captured",
                   event_id=f"evt_{session_id}",
                   webhook_amount_paise=running, money_authorised=True,
                   **{"from": "AWAITING_SETTLEMENT", "to": "PAID"},
                   total_paise=running)
    manage._CHAIN_CACHE.clear()
    return running


def _catalogue(**skus: dict) -> None:
    """A catalog.json shaped like the one on disk in results/shop/."""
    (manage.store_dir() / "catalog.json").write_text(json.dumps({
        "format": 2, "dim": 4,
        "gates": {"phi": 0.9, "theta": 0.1, "tau_mm": 4.0,
                  "phi_appearance_only": 0.92},
        "skus": skus,
    }), encoding="utf-8")


def _sku(name: str, price: int = 1000) -> dict:
    return {"name": name, "price_paise": price, "footprint_mm": 95.1,
            "taught_by": "mat_measured", "vectors": [[1.0, 0.0, 0.0, 0.0]],
            "photo": None, "photo_bytes": 0}


def _profile(name: Optional[str] = "Sharma Kirana Store") -> None:
    doc: dict[str, Any] = {"format": receipts.PROFILE_FORMAT, "name": name,
                           "address": "12 Nehru Road", "phone": "9876543210"}
    (manage.store_dir() / receipts.PROFILE_NAME).write_text(
        json.dumps(doc), encoding="utf-8")


def _teach() -> None:
    """Two products in the till's own catalogue, for the storefront to price."""
    for i, (sku, name, price) in enumerate((BISCUIT, SOAP)):
        upload_app.do_enrol_code_only(b"", sku, name, price,
                                      typed=f"890123456789{i}")


def _order(client: TestClient, **over) -> dict:
    body: dict[str, Any] = {
        "items": [{"sku_id": BISCUIT[0], "qty": 2}],
        "name": "Rekha",
        "phone": "9811122233",
        "address": "12 MG Road, second floor, near the water tank",
    }
    body.update(over)
    r = client.post("/store/order", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def refusal(r, reason: str) -> dict:
    """Every refusal in this program has the same shape. Assert all of it."""
    assert r.status_code in (400, 404), r.text
    body = r.json()
    assert body["ok"] is False
    assert body["reason"] == reason, body
    assert isinstance(body["detail"], str) and body["detail"].strip()
    assert body["settles_money"] is False
    return body


# ============================================================== the phone ==
#
# One test per named refusal. The failure this guards against is not an ugly
# error message: it is a WhatsApp chat opened with a stranger, which the
# shopkeeper cannot tell from a chat opened with his customer.


@pytest.mark.parametrize("typed", [
    "9876543210",           # as it is stored in every Indian phone
    "+919876543210",        # as WhatsApp itself renders it
    "919876543210",         # as a CSV export writes it
    "09876543210",          # with the trunk prefix people still type
    "+91 98765 43210",      # with the spaces WhatsApp puts in
    "+91-98765-43210",      # with the dashes a contacts app puts in
    "(+91) 98765 43210",    # with brackets
])
def test_the_four_shapes_an_indian_mobile_arrives_in_all_reach_one_number(typed):
    assert to_e164(typed) == "+919876543210"


def test_the_number_is_shown_back_grouped_so_a_typo_is_visible():
    """The whole failure being guarded against is a message opening on the
    wrong contact, so the number is echoed in the shape a person reads."""
    assert share.display_phone("+919876543210") == "+91 98765 43210"


def test_no_phone_at_all_is_refused_by_name():
    for empty in (None, "", "   "):
        with pytest.raises(ShareRefused) as e:
            to_e164(empty)
        assert e.value.reason == R_PHONE_MISSING


def test_a_phone_sent_as_a_json_number_is_refused_rather_than_coerced():
    """9876543210 as a JSON int has already lost a leading zero by the time it
    arrives, and str() of it would hide that it ever had one."""
    with pytest.raises(ShareRefused) as e:
        to_e164(9876543210)
    assert e.value.reason == R_PHONE_NOT_TEXT


def test_letters_in_a_phone_number_are_refused_and_named():
    with pytest.raises(ShareRefused) as e:
        to_e164("98765abcde")
    assert e.value.reason == R_PHONE_NOT_A_NUMBER
    assert "abcde" in e.value.detail


def test_devanagari_digits_are_not_e164_digits():
    """'९८७६५४३२१०' passes str.isdigit() and int() and is not a phone number.
    A number pasted from an Indian keyboard is a real way to reach this."""
    assert "९८७६५४३२१०".isdigit()
    with pytest.raises(ShareRefused) as e:
        to_e164("९८७६५४३२१०")
    assert e.value.reason == R_PHONE_NOT_A_NUMBER


def test_another_countrys_number_is_refused_not_prefixed_with_91():
    """Prefixing would produce a plausible Indian number belonging to somebody
    else entirely, which is the worst available outcome here."""
    with pytest.raises(ShareRefused) as e:
        to_e164("+14155550123")
    assert e.value.reason == R_PHONE_NOT_INDIA
    assert "91" in e.value.detail


def test_a_short_number_is_refused():
    with pytest.raises(ShareRefused) as e:
        to_e164("98765")
    assert e.value.reason == R_PHONE_TOO_SHORT
    assert "5 digits" in e.value.detail


def test_an_over_long_number_is_refused():
    with pytest.raises(ShareRefused) as e:
        to_e164("+9198765432101")
    assert e.value.reason == R_PHONE_TOO_LONG


def test_a_pasted_paragraph_is_refused_before_it_is_parsed():
    with pytest.raises(ShareRefused) as e:
        to_e164("9876543210 " * 8)
    assert e.value.reason == R_PHONE_TOO_LONG


@pytest.mark.parametrize("lead", "012345")
def test_a_number_that_is_not_a_mobile_is_refused(lead):
    """WhatsApp has no account on a landline or a service number, so opening a
    chat with one is a link that does nothing and says nothing."""
    with pytest.raises(ShareRefused) as e:
        to_e164(lead + "234567890")
    assert e.value.reason == R_PHONE_NOT_A_MOBILE


def test_the_stated_limit_on_a_leading_zero_behaves_as_it_is_stated(client):
    """`08023456789` is a trunk-prefixed mobile OR a Bangalore landline and
    nothing in the digits tells them apart. This counter reads it as a mobile
    and says so out loud rather than quietly picking one."""
    assert to_e164("08023456789") == "+918023456789"
    limits = client.get("/share/limits").json()
    assert "cannot be told apart" in limits["numbers"]["stated_limit"]


# ================================================= the message, and forgery ==


def _one_bill_message(client: TestClient, session_id="till_a") -> dict:
    _profile()
    _catalogue(**{BISCUIT[0]: _sku(BISCUIT[1], BISCUIT[2])})
    _bill(session_id, [(BISCUIT[0], BISCUIT[2])])
    r = client.get(f"/share/receipt/{session_id}")
    assert r.status_code == 200, r.text
    return r.json()


def test_a_customer_who_names_himself_a_upi_payload_gets_no_message(full):
    """THE TEST THIS MODULE EXISTS FOR.

    A storefront order is placed from the open internet and the customer's own
    name is printed into the message the shopkeeper forwards. `upi://pay?pa=…`
    is a name as far as the order form is concerned — the order is accepted,
    which is correct, and the message is refused, which is the point.
    """
    _teach()
    order = _order(full, name="upi://pay?pa=thief@okbank&am=50000&cu=INR")
    r = full.get(f"/share/order/{order['order_id']}")
    body = refusal(r, R_REFUSED_MESSAGE)
    assert "UPI" in body["detail"]


def test_a_customer_name_carrying_a_gateway_link_gets_no_message(full):
    _teach()
    order = _order(full, name="https://rzp.io/l/forged")
    refusal(full.get(f"/share/order/{order['order_id']}"), R_REFUSED_MESSAGE)


def test_a_bare_gateway_host_with_no_scheme_is_still_refused(full):
    """WhatsApp linkifies `rzp.io/l/abc` perfectly well, so a check that only
    looked for `scheme://` would let the tappable half through."""
    _teach()
    order = _order(full, name="rzp.io/l/forged")
    body = refusal(full.get(f"/share/order/{order['order_id']}"),
                   R_REFUSED_MESSAGE)
    assert "rzp.io" in body["detail"]


def test_any_address_this_counter_did_not_put_there_is_refused(full):
    _teach()
    order = _order(full, name="https://example.com/pay-me")
    body = refusal(full.get(f"/share/order/{order['order_id']}"),
                   R_REFUSED_MESSAGE)
    assert "did not put there" in body["detail"]


def test_a_product_name_that_is_a_payment_payload_is_refused_on_a_receipt(client):
    """The same guard from the other side: a product NAME reaches the receipt
    message through the catalogue, and the catalogue is a file people edit."""
    _profile()
    _catalogue(**{BISCUIT[0]: _sku("upi://pay?pa=thief@okbank&am=1", 2145)})
    _bill("till_upi", [(BISCUIT[0], 2145)])
    refusal(client.get("/share/receipt/till_upi"), R_REFUSED_MESSAGE)


def test_a_bill_too_long_for_a_deep_link_is_refused_not_truncated(client):
    """A truncated bill is a wrong bill. The cap is a refusal, and the refusal
    says to send the receipt address instead."""
    _profile()
    long_name = "Aashirvaad Select Sharbati Atta ten kilo bag " * 3
    skus = {f"sku_{i}": _sku(f"{long_name}{i}", 1000) for i in range(25)}
    _catalogue(**skus)
    _bill("till_long", [(s, 1000) for s in skus])
    body = refusal(client.get("/share/receipt/till_long"), R_TOO_LONG)
    assert str(MAX_MESSAGE_CHARS) in body["detail"]


def test_a_long_bill_that_fits_says_how_many_lines_it_left_out(client):
    """Folding is allowed; folding SILENTLY is not. A short list nobody can
    see is short is the failure this program refuses everywhere."""
    _profile()
    skus = {f"sku_{i}": _sku(f"Item {i}", 1000) for i in range(28)}
    _catalogue(**skus)
    _bill("till_fold", [(s, 1000) for s in skus])
    body = client.get("/share/receipt/till_fold").json()
    assert body["ok"] is True
    assert f"and {28 - MAX_LINES_IN_A_MESSAGE} more items" in body["message"]


# ==================================================================== links ==


def test_the_link_points_at_wa_me_and_carries_the_whole_message(client):
    _one_bill_message(client)
    r = client.post("/share/receipt/till_a", json={"phone": "9876543210"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["wa_url"].startswith("https://wa.me/919876543210?text=")
    assert body["to"] == "+919876543210"
    assert body["to_display"] == "+91 98765 43210"
    assert body["wa_host"] == "wa.me"
    # Percent-encoded whole: a '&' or a '#' inside a product name must not end
    # the query string early and lop the rest of the bill off.
    assert "&" not in body["wa_url"].split("?text=", 1)[1]
    assert "%0A" in body["wa_url"]


def test_no_link_this_module_composes_can_carry_a_payment_target(client):
    body = _one_bill_message(client)
    r = client.post("/share/receipt/till_a", json={"phone": "9876543210"})
    url = r.json()["wa_url"].lower()
    assert "upi" not in url
    for host in receipts._gateway_hosts():
        assert host.lower() not in url
    assert body["carries_a_payment_link"] is False


def test_a_link_pointing_anywhere_but_the_allowlist_is_refused(monkeypatch):
    """Not browser-reachable today — every byte of the URL is built in this
    file. The guard has to hold on its own for the day somebody makes the host
    configurable, which is the same argument storefront.store_qr_ep makes."""
    monkeypatch.setattr(share, "SHARE_HOSTS", ("telegram.example",))
    with pytest.raises(ShareRefused) as e:
        share.wa_url("+919876543210", "hello")
    assert e.value.reason == R_REFUSED_LINK
    assert "wa.me" in e.value.detail


def test_the_source_builds_no_payment_target():
    """INVARIANT 6, asserted against the file rather than against behaviour.

    Every string this module can EMIT is examined — every literal that is not
    a docstring — and none may contain a UPI scheme or a gateway host. A
    docstring explaining the threat is not a forgery primitive; a literal the
    code can interpolate into a link is, whatever the tests around it said.
    """
    tree = ast.parse(Path(share.__file__).read_text(encoding="utf-8"))
    prose = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            first = (node.body or [None])[0]
            if isinstance(first, ast.Expr) and \
                    isinstance(first.value, ast.Constant) and \
                    isinstance(first.value.value, str):
                prose.add(id(first.value))

    emitted = [n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and id(n) not in prose]
    assert emitted, "the walk found no string literals at all — check it works"
    for text in emitted:
        low = text.lower()
        assert "upi:" not in low, text
        assert "rzp.io" not in low, text
        assert "razorpay" not in low, text


# =================================================================== receipt ==


def test_a_receipt_message_carries_the_lines_the_total_and_the_page(client):
    _profile()
    _catalogue(**{BISCUIT[0]: _sku(BISCUIT[1], BISCUIT[2]),
                  SOAP[0]: _sku(SOAP[1], SOAP[2])})
    total = _bill("till_b", [(BISCUIT[0], BISCUIT[2]), (BISCUIT[0], BISCUIT[2]),
                             (SOAP[0], SOAP[2])])
    body = client.get("/share/receipt/till_b").json()
    assert body["ok"] is True
    assert body["kind"] == "receipt"
    assert "Sharma Kirana Store" in body["message"]
    # Folded to one row per product and price, with the count.
    assert "Parle-G 200g x2 — ₹42.90" in body["message"]
    assert "Lifebuoy 125g x1 — ₹39.50" in body["message"]
    assert "Total: ₹82.40" in body["message"]
    # The integer is the number; the rupee string is derived from it.
    assert body["total_paise"] == total == 8240
    assert body["total_rupees"] == "82.40"
    assert body["receipt_url"] == "http://testserver/receipt/till_b/page"
    assert body["link_included"] is True


def test_an_unpaid_bill_says_so_and_a_settled_one_says_who_settled_it(client):
    _profile()
    _catalogue(**{BISCUIT[0]: _sku(BISCUIT[1], BISCUIT[2])})
    _bill("till_unpaid", [(BISCUIT[0], BISCUIT[2])])
    _bill("till_green", [(BISCUIT[0], BISCUIT[2])], settle="webhook", at=100)
    _bill("till_amber", [(BISCUIT[0], BISCUIT[2])], settle="kernel", at=200)

    unpaid = client.get("/share/receipt/till_unpaid").json()
    assert "Not paid" in unpaid["message"]
    assert unpaid["settled_by_verified_webhook"] is False

    green = client.get("/share/receipt/till_green").json()
    assert "Paid. The payment gateway" in green["message"]
    assert green["settled_by_verified_webhook"] is True

    # INVARIANT 2. A settlement with no signed webhook beside it is PAID with
    # a qualification, never PAID plainly — the customer is told which.
    counter = client.get("/share/receipt/till_amber").json()
    assert "recorded by this counter" in counter["message"]
    assert counter["settled_by_verified_webhook"] is False


def test_an_item_the_counter_could_not_name_is_on_the_message(client):
    """INVARIANT 7 travels with the bill. A customer reading only the WhatsApp
    message must not be the last person to know the bill was short."""
    _profile()
    _catalogue(**{BISCUIT[0]: _sku(BISCUIT[1], BISCUIT[2])})
    _bill("till_amb", [(BISCUIT[0], BISCUIT[2])], amber=("mystery#0",))
    body = client.get("/share/receipt/till_amb").json()
    assert body["excluded_count"] == 1
    assert "1 item could not be identified at the counter" in body["message"]
    # Singular. "1 item ... They were not charged" was the first draft, and a
    # receipt that cannot count to one is a receipt nobody trusts to count to
    # eighty-two rupees forty.
    assert "was left off this bill. It was not charged for." in body["message"]


def test_a_counter_on_loopback_sends_the_figures_and_not_a_dead_address(client):
    """A link reading 127.0.0.1 opens on the customer's own phone. Leaving it
    out costs the message its link; leaving it IN costs the customer the bill."""
    _one_bill_message(client, "till_lo")
    body = client.get("/share/receipt/till_lo",
                      headers={"host": "127.0.0.1:8790"}).json()
    assert body["ok"] is True
    assert body["link_included"] is False
    assert "Full bill:" not in body["message"]
    assert "loopback" in body["link_problem"]
    assert "Total:" in body["message"]


def test_a_host_header_this_counter_cannot_read_costs_only_the_address(client):
    _one_bill_message(client, "till_host")
    body = client.get("/share/receipt/till_host",
                      headers={"host": "not a host"}).json()
    assert body["ok"] is True
    assert body["link_included"] is False
    assert receipts.R_NO_HOST in body["link_problem"] or \
        "cannot tell" in body["link_problem"]
    assert "Total: ₹21.45" in body["message"]


def test_a_shop_with_no_name_sends_a_bill_and_not_an_invented_signboard(client):
    _catalogue(**{BISCUIT[0]: _sku(BISCUIT[1], BISCUIT[2])})
    _bill("till_anon", [(BISCUIT[0], BISCUIT[2])])
    body = client.get("/share/receipt/till_anon").json()
    assert body["ok"] is True
    assert body["message"].startswith("Your bill")


def test_a_session_that_is_not_in_the_chain_is_refused_by_name(client):
    _catalogue()
    refusal(client.get("/share/receipt/till_nothing"),
            receipts.R_UNKNOWN_SESSION)


def test_a_basket_that_never_became_a_bill_is_refused_by_name(client):
    """A basket is not a bill until the counter writes its 'done' line, and a
    receipt for an open basket would be a total nobody agreed to."""
    _catalogue(**{BISCUIT[0]: _sku(BISCUIT[1], BISCUIT[2])})
    led = _ledger()
    led.append(ts=_ts(0), module="session", event="session",
               session_id="till_open", reason="session_opened",
               **{"from": "SETUP", "to": "SETUP"}, total_paise=0)
    led.append(ts=_ts(1), module="session", event="exit",
               session_id="till_open", reason="exit_crossing_committed",
               item_id=f"{BISCUIT[0]}#0", price_paise=BISCUIT[2],
               abstained=False, excluded_from_total=False,
               **{"from": "PRICED", "to": "BASKET_OPEN"}, total_paise=BISCUIT[2])
    manage._CHAIN_CACHE.clear()
    refusal(client.get("/share/receipt/till_open"), receipts.R_NOT_A_BILL)


@pytest.mark.parametrize("bad", ["till%20a", "till%3Cscript%3E", "-till", "%20"])
def test_a_session_id_that_is_not_one_is_refused_before_it_touches_a_path(
        client, bad):
    """The id becomes part of a URL, a QR and an HTML page downstream, so it
    is checked against a charset FIRST. receipts.py owns that check and this
    module inherits both the check and its name."""
    refusal(client.get(f"/share/receipt/{bad}"), receipts.R_BAD_SESSION_ID)


def test_a_receipt_needs_a_phone_before_it_gets_a_link(client):
    _one_bill_message(client)
    refusal(client.post("/share/receipt/till_a", json={}), R_PHONE_MISSING)


def test_a_body_that_is_not_json_is_refused_by_name(client):
    _one_bill_message(client)
    r = client.post("/share/receipt/till_a", content=b"phone=9876543210",
                    headers={"content-type": "application/json"})
    refusal(r, R_BAD_BODY)


def test_a_body_that_is_a_list_is_refused_by_name(client):
    _one_bill_message(client)
    refusal(client.post("/share/receipt/till_a", json=["9876543210"]),
            R_BAD_BODY)


# ===================================================================== order ==


def test_an_order_message_carries_the_order_the_total_and_the_status(full):
    _profile()
    _teach()
    order = _order(full)
    body = full.get(f"/share/order/{order['order_id']}").json()
    assert body["ok"] is True
    assert body["kind"] == "order"
    assert order["order_id"] in body["message"]
    assert "For Rekha" in body["message"]
    assert "Parle-G 200g x2 — ₹42.90" in body["message"]
    assert "Total: ₹42.90" in body["message"]
    assert "The shop has your order." in body["message"]
    assert body["total_paise"] == 4290
    assert body["phone_on_file"] == "9811122233"


def test_an_order_message_never_carries_the_payment_link_the_order_has(full):
    """The order document can hold a real gateway short_url. Forwarding a
    payment link through a chat app is the exact shape of the fraud a kirana
    customer is warned about, so it is not in the message and the guard in
    _check_message would refuse it if somebody put it there."""
    _profile()
    _teach()
    order = _order(full)
    doc = storefront._read_order(order["order_id"])
    doc["payment"]["short_url"] = "https://rzp.io/l/realone"
    doc["payment"]["state"] = "created"
    storefront._write_order(doc)

    body = full.get(f"/share/order/{order['order_id']}").json()
    assert body["ok"] is True
    assert "rzp.io" not in body["message"]
    assert body["carries_a_payment_link"] is False
    assert "Nothing has been charged" in body["message"]


def test_the_number_on_the_order_is_used_when_the_page_sends_none(full):
    _profile()
    _teach()
    order = _order(full)
    r = full.post(f"/share/order/{order['order_id']}", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["to"] == "+919811122233"
    assert body["phone_from"] == "the number on this order"


def test_a_number_the_shopkeeper_types_beats_the_one_on_the_order(full):
    """A customer ringing from a second number is the normal case, not an
    error, so the typed number wins and the response says which was used."""
    _profile()
    _teach()
    order = _order(full)
    r = full.post(f"/share/order/{order['order_id']}",
                  json={"phone": "+91 70000 11111"})
    body = r.json()
    assert body["to"] == "+917000011111"
    assert body["phone_from"] == "the number you typed"


def test_an_order_id_that_is_not_one_is_refused_before_it_touches_a_path(full):
    _teach()
    refusal(full.get("/share/order/not-an-order"), storefront.R_BAD_ORDER_ID)


def test_an_order_this_shop_does_not_have_is_a_404_by_name(full):
    _teach()
    r = full.get("/share/order/ord_0123456789ab")
    assert r.status_code == 404
    refusal(r, storefront.R_NO_ORDER)


def test_the_order_reader_this_module_borrows_still_exists(full):
    """This module reads an order through storefront's OWN reader rather than
    opening the JSON itself, so that there is one answer to where an order
    lives. Pinned here so a rename upstream fails loudly instead of silently
    growing a second reader."""
    assert callable(getattr(storefront, "_read_order", None))
    assert callable(getattr(storefront, "_valid_order_id", None))


# =================================================================== reorder ==


def _low_shelf(full: TestClient) -> None:
    """Two products under their level, one never counted, one below zero."""
    _catalogue(parle_g=_sku("Parle-G"), maggi=_sku("Maggi 70g"),
               soap=_sku("Lifebuoy"), rice=_sku("Sona Masoori"))
    for sku, count, level in (("parle_g", 4, 20), ("maggi", 0, 12)):
        assert full.post(f"/stock/{sku}/count", json={"units": count}).status_code == 200
        assert full.post(f"/stock/{sku}/reorder", json={"units": level}).status_code == 200
    # A level with no count behind it: whether it is low CANNOT be said.
    assert full.post("/stock/soap/reorder", json={"units": 6}).status_code == 200
    # A figure below zero: stock left without being recorded.
    assert full.post("/stock/rice/count", json={"units": 2}).status_code == 200
    assert full.post("/stock/rice/reorder", json={"units": 5}).status_code == 200
    assert full.post("/stock/rice/out",
                     json={"units": 9, "reason": "breakage"}).status_code == 200


def test_the_reorder_message_carries_the_shelf_figures_and_no_quantity(full):
    """The order quantity needs a case size and a lead time this counter is
    never told. Printing one would be the plausible-looking number the whole
    product exists to refuse, so the message carries what IS derived and asks
    the shopkeeper for the rest."""
    _profile()
    _low_shelf(full)
    body = full.get("/share/reorder").json()
    assert body["ok"] is True
    assert body["kind"] == "reorder"
    assert "Parle-G — 4 packets on the shelf, level 20 (short by 16" in body["message"]
    assert "Maggi 70g — 0 packets on the shelf, level 12 (short by 12" in body["message"]
    assert "does not know your case sizes" in body["message"]
    assert "Please write the quantity against each line." in body["message"]
    # No money anywhere in a purchase order: this counter does not know a cost.
    assert "₹" not in body["message"]


def test_a_shelf_that_was_never_counted_is_named_and_not_ordered(full):
    _profile()
    _low_shelf(full)
    body = full.get("/share/reorder").json()
    assert body["unknown_count"] == 1
    assert "never been counted" in body["message"]
    assert "Lifebuoy" in body["message"]


def test_a_figure_below_zero_is_named_as_needing_a_recount(full):
    """-7 on the shelf means something left without being recorded. Ordering
    against that number would order the wrong amount, so it is named."""
    _profile()
    _low_shelf(full)
    body = full.get("/share/reorder").json()
    assert body["needs_recount_count"] == 1
    assert "needs a recount" in body["message"]
    assert "Sona Masoori" in body["message"]


def test_nothing_low_is_a_refusal_and_not_an_empty_purchase_order(full):
    _profile()
    _catalogue(parle_g=_sku("Parle-G"))
    full.post("/stock/parle_g/count", json={"units": 40})
    full.post("/stock/parle_g/reorder", json={"units": 5})
    body = refusal(full.get("/share/reorder"), R_NOTHING_IS_LOW)
    assert "reorder level" in body["detail"]


def test_a_reorder_can_be_addressed_to_a_supplier_on_file(full):
    _profile()
    _low_shelf(full)
    app = FastAPI()
    from gawaah import purchases
    app.include_router(purchases.router)
    pc = TestClient(app)
    made = pc.post("/purchases/suppliers",
                   json={"name": "Sharma Traders", "phone": "9822233344"})
    assert made.status_code == 200, made.text
    sid = made.json()["supplier"]["supplier_id"]

    r = full.post("/share/reorder", json={"supplier_id": sid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["to"] == "+919822233344"
    assert body["phone_from"] == "this supplier's record"
    assert "For Sharma Traders" in body["message"]


def test_a_supplier_id_that_is_not_one_is_refused_by_name(full):
    _profile()
    _low_shelf(full)
    refusal(full.get("/share/reorder?supplier_id=sharma"), R_BAD_SUPPLIER_ID)


def test_a_supplier_this_shop_does_not_have_is_a_404_by_name(full):
    _profile()
    _low_shelf(full)
    r = full.get("/share/reorder?supplier_id=sup_0123456789ab")
    assert r.status_code == 404
    refusal(r, R_NO_SUPPLIER)


def test_a_reorder_with_no_supplier_and_no_phone_is_refused_by_name(full):
    _profile()
    _low_shelf(full)
    refusal(full.post("/share/reorder", json={}), R_PHONE_MISSING)


def test_the_low_list_is_stock_pys_own_and_is_not_filtered_by_supplier(full):
    """This counter does not record which supplier a product comes from, so a
    filter by one would be a claim it cannot support. It says so."""
    _profile()
    _low_shelf(full)
    mine = full.get("/share/reorder").json()
    theirs = full.get("/stock/low").json()
    # stock.py's own count of what is at or under a level, unchanged.
    assert mine["at_or_under_level_count"] == theirs["count"] == 3
    assert mine["unknown_count"] == len(theirs["unknown"]) == 1
    assert mine["needs_recount_count"] == len(theirs["needs_recount"]) == 1
    # And what actually goes on a purchase order: the same list minus the one
    # whose shelf figure is below zero, which is named instead of ordered.
    assert mine["low_count"] == 2
    assert mine["filtered_by_supplier"] is False


# ================================================================ structural ==


def test_nothing_is_written_to_disk_when_a_message_is_composed(client):
    """A line saying 'receipt shared' would be a record of an act this server
    cannot observe: the shopkeeper may close WhatsApp without pressing send."""
    _one_bill_message(client, "till_disk")
    before = {p: p.stat().st_mtime_ns
              for p in manage.store_dir().rglob("*") if p.is_file()}
    chain_before = manage.ledger_path().read_bytes()

    r = client.post("/share/receipt/till_disk", json={"phone": "9876543210"})
    assert r.status_code == 200
    assert r.json()["note"].startswith("Nothing has been sent")

    after = {p: p.stat().st_mtime_ns
             for p in manage.store_dir().rglob("*") if p.is_file()}
    assert after == before
    assert manage.ledger_path().read_bytes() == chain_before


def test_the_limits_endpoint_says_this_counter_sends_nothing(client):
    body = client.get("/share/limits").json()
    assert body["sends_messages"] is False
    assert body["records_what_was_sent"] is False
    assert body["carries_a_payment_link"] is False
    assert body["host"] == SHARE_HOSTS[0] == "wa.me"
    assert "presses send" in body["how"]


def test_a_module_this_needs_being_unavailable_is_a_named_refusal(client,
                                                                  monkeypatch):
    """Five agents are editing this repo. If a sibling module stops importing,
    that must cost the shopkeeper the share button and not the whole till."""
    def _boom(name, package=None):
        raise ImportError("simulated: gawaah/stock.py is mid-edit")

    monkeypatch.setattr("importlib.import_module", _boom)
    body = refusal(client.get("/share/reorder"), R_MODULE_UNAVAILABLE)
    assert "stock.py" in body["detail"]


@pytest.mark.parametrize("path", [
    "/share/receipt/%00", "/share/receipt/" + "x" * 400,
    "/share/order/ord_zzzzzzzzzzzz", "/share/order/%2e%2e",
    "/share/reorder?supplier_id=" + "y" * 300,
])
def test_no_input_of_any_shape_produces_a_500(full, path):
    """A refusal is a result. A 500 is a bug, and a shopkeeper cannot act on
    one."""
    r = full.get(path)
    assert r.status_code in (200, 400, 404), (path, r.status_code, r.text)
    if r.status_code != 200:
        assert r.json()["ok"] is False
        assert r.json()["reason"]


@pytest.mark.parametrize("body", [
    {"phone": []}, {"phone": {"n": 1}}, {"phone": True}, {"phone": 0},
    {"supplier_id": 12}, {"supplier_id": []}, {"supplier_id": None},
    {}, {"message": "send this instead"}, {"total_paise": 1},
])
def test_no_body_of_any_shape_produces_a_500(full, body):
    """Including the ones that are not refusals. A body carrying `message` or
    `total_paise` is answered as if it did not — INVARIANT 8: the page cannot
    author what goes out, so an attempt to is ignored rather than obeyed."""
    _teach()
    order = _order(full)
    for url in ("/share/receipt/till_x", f"/share/order/{order['order_id']}",
                "/share/reorder"):
        r = full.post(url, json=body)
        assert r.status_code in (200, 400, 404), (url, r.status_code, r.text)
        out = r.json()
        assert isinstance(out["ok"], bool)
        if out["ok"]:
            assert "send this instead" not in out["message"]
        else:
            assert isinstance(out["reason"], str) and out["reason"]


def test_every_named_refusal_in_this_module_is_covered_by_this_file():
    """A refusal nobody tests is a sentence nobody has read. This asserts the
    suite keeps up with the module rather than trusting that it did."""
    src = Path(share.__file__).read_text(encoding="utf-8")
    named = set(re.findall(r"^(R_[A-Z_]+) = ", src, re.M))
    mine = Path(__file__).read_text(encoding="utf-8")
    missed = {n for n in named if n not in mine}
    # R_INTERNAL is the crash handler: it is asserted by the two no-500 fuzz
    # tests above, which accept it as one legal answer, and it is deliberately
    # not reachable by a chosen input.
    assert missed <= {"R_INTERNAL"}, f"untested refusals: {sorted(missed)}"
