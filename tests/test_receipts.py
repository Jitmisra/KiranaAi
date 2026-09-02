"""gawaah/receipts.py — the bill a customer keeps.

A receipt is the one artefact in this program that leaves the shop. It goes
home in somebody's photo library and it is what gets produced when a customer
says "I paid for that". So the suite is organised around the four ways it could
be worse than useless:

  1. It could say money moved when it did not      -> the settlement tests
  2. It could show a number it did not derive      -> the derivation tests, and
                                                      the one that re-prices a
                                                      bill from today's catalogue
  3. It could hand a phone a code that is not      -> the QR tests
     this counter's own address
  4. It could crash, or render somebody's file     -> the refusal tests and the
     content as markup                                escaping tests

Every fixture writes a REAL hash-chained ledger with gawaah.ledger.Ledger, so
the chain the code verifies is the chain the writer wrote, and the event shapes
are copied from tests/test_manage.py rather than invented — receipts.py folds
the ledger through manage.bills_from, so a fixture that writes a shape the real
session module never writes would test nothing at all.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import manage, receipts  # noqa: E402
from gawaah.ledger import Ledger  # noqa: E402

T0 = datetime(2026, 8, 29, 5, 0, 0, tzinfo=timezone.utc)


def _ts(offset_s: int) -> str:
    return (T0 + timedelta(seconds=offset_s)).isoformat()


# ------------------------------------------------------------------ fixtures

@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Nothing in this suite may see, let alone write, results/.

    A harness once destroyed the live catalogue by ignoring GAWAAH_SHOP_DIR, so
    both overrides are set for EVERY test whether it uses them or not.

    The till module caches its store directory in `_DEPS` on first use, so if
    any other test module in this session has already imported it, that cache
    is pointed at THIS test's directory too. Without that, the second test in a
    session would read the first test's shop profile and the failure would look
    like a bug in receipts.py.
    """
    data = tmp_path / "data"
    shop = tmp_path / "data" / "shop"
    shop.mkdir(parents=True)
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(data))
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(shop))
    for name in ("upload_app", "tools.upload_app"):
        mod = sys.modules.get(name)
        setter = getattr(mod, "set_store_dir", None) if mod is not None else None
        if callable(setter):
            setter(shop)
    manage._CHAIN_CACHE.clear()
    yield
    manage._CHAIN_CACHE.clear()


@pytest.fixture
def client() -> TestClient:
    """The router mounted the way the orchestrator will mount it: bare."""
    app = FastAPI()
    app.include_router(receipts.router)
    return TestClient(app)


def _ledger() -> Ledger:
    return Ledger(manage.ledger_path())


def _bill(
    ledger: Ledger,
    session_id: str,
    lines: list[tuple[str, int]],
    *,
    amber: tuple[str, ...] = (),
    unpriced: tuple[str, ...] = (),
    at: int = 0,
    close: bool = True,
    mint: bool = False,
    settle: str = "none",
    total_override: Optional[int] = None,
) -> int:
    """Write one session into the chain the way the real modules write it.

    `settle` is 'none', 'webhook' (the kernel line AND the signature-verified
    webhook line, which is what a real settlement writes) or 'kernel' (the
    kernel line alone, which is what a chain looks like when the webhook line
    is missing).
    """
    clock = at
    running = 0
    ledger.append(ts=_ts(clock), module="session", event="session",
                  session_id=session_id, reason="session_opened",
                  **{"from": "SETUP", "to": "SETUP"}, total_paise=0)
    for i, (sku, price) in enumerate(lines):
        clock += 1
        item_id = f"{sku}#{i}"
        running += price
        ledger.append(ts=_ts(clock), module="session", event="exit",
                      session_id=session_id, reason="exit_crossing_committed",
                      item_id=item_id, price_paise=price, abstained=False,
                      excluded_from_total=False,
                      **{"from": "PRICED", "to": "BASKET_OPEN"},
                      total_paise=running)
    for i, sku in enumerate(unpriced):
        # A packet committed into the basket whose exit line carries no integer
        # price. The chain can hold this; a receipt must not turn it into a
        # free packet.
        clock += 1
        ledger.append(ts=_ts(clock), module="session", event="exit",
                      session_id=session_id, reason="exit_crossing_committed",
                      item_id=f"{sku}#u{i}", abstained=False,
                      excluded_from_total=False,
                      **{"from": "PRICED", "to": "BASKET_OPEN"},
                      total_paise=running)
    for sku in amber:
        clock += 1
        ledger.append(ts=_ts(clock), module="session", event="exit",
                      session_id=session_id,
                      reason="exit_crossing_committed_amber_excluded",
                      item_id=sku, abstained=True, excluded_from_total=True,
                      **{"from": "AMBER", "to": "BASKET_OPEN"},
                      total_paise=running)
    if close:
        clock += 1
        ledger.append(ts=_ts(clock), module="session", event="done",
                      session_id=session_id, reason="intent_requested",
                      lines=len(lines), amber_excluded=len(amber),
                      intent_amount_paise=running,
                      **{"from": "BASKET_OPEN", "to": "AWAITING_SETTLEMENT"},
                      total_paise=running if total_override is None
                      else total_override)
    if mint:
        clock += 1
        ledger.append(ts=_ts(clock), module="paisa", event="intent.minted",
                      session_id=session_id, minted=True, replayed=False,
                      amount_paise=running,
                      payment_link_id=f"plink_{session_id}")
    if settle in ("webhook", "kernel"):
        clock += 1
        ledger.append(ts=_ts(clock), module="kernel", event="intent.settled",
                      session_id=session_id, amount_paise=running,
                      payment_id=f"pay_{session_id}", from_state="CALLING",
                      to_state="SETTLED", reason=None)
    if settle == "webhook":
        clock += 1
        ledger.append(ts=_ts(clock), module="session", event="webhook",
                      session_id=session_id, reason="settled_green",
                      razorpay_event="payment.captured",
                      event_id=f"evt_{session_id}",
                      webhook_amount_paise=running, money_authorised=True,
                      **{"from": "AWAITING_SETTLEMENT", "to": "PAID"},
                      total_paise=running)
    return running


def _catalogue(**skus: dict) -> None:
    """A catalog.json shaped like the one on disk in results/shop/."""
    path = manage.store_dir() / "catalog.json"
    path.write_text(json.dumps({
        "format": 2, "dim": 4,
        "gates": {"phi": 0.9, "theta": 0.1, "tau_mm": 4.0,
                  "phi_appearance_only": 0.92},
        "skus": skus,
    }), encoding="utf-8")


def _sku(name: str, price: int) -> dict:
    return {"name": name, "price_paise": price, "footprint_mm": 95.1,
            "taught_by": "mat_measured", "vectors": [[1.0, 0.0, 0.0, 0.0]],
            "photo": None, "photo_bytes": 0}


def _profile(**fields: Any) -> Path:
    """A shop_profile.json as gawaah/shopadmin.py writes it."""
    doc = {"format": receipts.PROFILE_FORMAT, "name": "Sharma Kirana Store",
           "address": "12 Nehru Road, Indiranagar", "phone": "9876543210",
           "phone_e164": "+919876543210", "updated_at": _ts(0)}
    doc.update(fields)
    path = manage.store_dir() / receipts.PROFILE_NAME
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _floats_in(value: Any, trail: str = "$") -> list[str]:
    """Every path in a decoded JSON document that holds a float. Invariant 1."""
    found: list[str] = []
    if isinstance(value, float):
        found.append(f"{trail} = {value!r}")
    elif isinstance(value, dict):
        for key, sub in value.items():
            found += _floats_in(sub, f"{trail}.{key}")
    elif isinstance(value, list):
        for i, sub in enumerate(value):
            found += _floats_in(sub, f"{trail}[{i}]")
    return found


# ==================================================== the bill that is not there

def test_unknown_session_is_a_404_and_never_a_crash(client):
    """A receipt for a bill nobody billed is a 404 with a name, not a 500."""
    r = client.get("/receipt/never_happened")
    assert r.status_code == 404
    body = r.json()
    assert body["ok"] is False
    assert body["reason"] == receipts.R_UNKNOWN_SESSION
    assert body["settles_money"] is False


def test_unknown_session_names_the_chain_it_looked_in(client):
    """The shopkeeper's next move is to look in the ledger, so the refusal says
    which file that is and how much of it verified."""
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    body = client.get("/receipt/s2").json()
    assert str(manage.ledger_path()) in body["detail"]
    assert "verified lines" in body["detail"]


def test_a_session_that_never_closed_is_not_a_bill_yet(client):
    """A basket on the counter has no total. Printing one would mean inventing
    the moment the shopkeeper decided it was finished."""
    _bill(_ledger(), "open_basket", [("parle_g", 1000)], close=False)
    r = client.get("/receipt/open_basket")
    assert r.status_code == 400
    body = r.json()
    assert body["reason"] == receipts.R_NOT_A_BILL
    assert "never closed" in body["detail"]


def test_every_route_refuses_an_unknown_session_the_same_way(client):
    """The page, the QR and the link all resolve the bill first. A code that
    opens a 404 is worse than no code: the customer finds out after they have
    left."""
    for path in ("/receipt/nope", "/receipt/nope/page", "/receipt/nope/qr",
                 "/receipt/nope/link"):
        r = client.get(path)
        assert r.status_code == 404, path
        assert r.json()["reason"] == receipts.R_UNKNOWN_SESSION, path


# ============================================================ the session id

@pytest.mark.parametrize("bad", [
    "has a space",
    "<script>alert(1)</script>",
    "../../etc/passwd",
    "-leading-dash",
    "sess\x00null",
    "sess\nnewline",
    "",
    "   ",
])
def test_a_malformed_session_id_is_refused_before_anything_is_read(bad):
    """The id becomes part of a URL, a QR and an HTML page. It is checked
    against a charset first rather than escaped three different ways later."""
    with pytest.raises(receipts.ReceiptRefused) as exc:
        receipts._valid_session_id(bad)
    assert exc.value.reason == receipts.R_BAD_SESSION_ID


def test_an_over_long_session_id_is_refused(client):
    """Bounded because it ends up in a filename-shaped URL and on a page."""
    long_id = "s" * 129
    r = client.get(f"/receipt/{long_id}")
    assert r.status_code == 400
    assert r.json()["reason"] == receipts.R_BAD_SESSION_ID
    # One character shorter is a shape this counter could genuinely mint, so it
    # gets as far as looking for the bill.
    r2 = client.get("/receipt/" + "s" * 128)
    assert r2.json()["reason"] == receipts.R_UNKNOWN_SESSION


def test_the_ids_this_counter_actually_mints_are_accepted():
    """Taken from results/audit.jsonl, not invented. A charset check that
    refuses the counter's own ids would make every receipt unreachable."""
    for real in ("till_mth34cri_s4d6jemx", "counter_live_4", "shop_ord_2f1c9a",
                 "probe-parle_g_biscuit", "a1788103355"):
        assert receipts._valid_session_id(real) == real


def test_a_refusal_always_carries_the_house_shape(client):
    """ok, reason, detail, settles_money — the same four keys every other
    endpoint in this program answers a refusal with."""
    for path in ("/receipt/nope", "/receipt/has%20a%20space",
                 "/receipt/nope/qr"):
        body = client.get(path).json()
        assert set(("ok", "reason", "detail", "settles_money")) <= set(body)
        assert body["ok"] is False
        assert body["settles_money"] is False
        assert isinstance(body["detail"], str) and body["detail"]


# ============================================================== the derivation

def test_the_total_is_the_one_the_chain_recorded(client):
    total = _bill(_ledger(), "s1", [("parle_g", 1000), ("soap", 3500)])
    body = client.get("/receipt/s1").json()
    assert body["ok"] is True
    assert total == 4500
    assert body["total_paise"] == 4500
    assert body["total_rupees"] == "45.00"


def test_packets_are_grouped_into_lines_with_a_quantity(client):
    """The chain records one exit per packet. A customer reads 'x3'."""
    _bill(_ledger(), "s1", [("parle_g", 1000), ("parle_g", 1000),
                            ("parle_g", 1000), ("soap", 3500)])
    body = client.get("/receipt/s1").json()
    assert body["line_count"] == 2
    assert body["item_count"] == 4
    first = body["lines"][0]
    assert first["sku_id"] == "parle_g"
    assert first["qty"] == 3
    assert first["item_ids"] == ["parle_g#0", "parle_g#1", "parle_g#2"]


def test_a_line_total_is_the_unit_price_times_the_count(client):
    _bill(_ledger(), "s1", [("parle_g", 1050), ("parle_g", 1050)])
    line = client.get("/receipt/s1").json()["lines"][0]
    assert line["unit_paise"] == 1050
    assert line["line_paise"] == 2100
    assert line["unit_rupees"] == "10.50"
    assert line["line_rupees"] == "21.00"


def test_the_lines_add_up_to_the_total_and_the_receipt_says_so(client):
    _bill(_ledger(), "s1", [("parle_g", 1000), ("soap", 3500)])
    body = client.get("/receipt/s1").json()
    assert body["lines_sum_paise"] == 4500
    assert body["total_agrees"] is True
    assert body["notes"] == []


def test_a_total_that_disagrees_with_the_lines_shows_both_numbers(client):
    """Neither number is adjusted to match the other. A receipt that quietly
    picked one would hide the only evidence that something is wrong."""
    _bill(_ledger(), "s1", [("parle_g", 1000), ("soap", 3500)],
          total_override=9900)
    body = client.get("/receipt/s1").json()
    assert body["total_paise"] == 9900
    assert body["lines_sum_paise"] == 4500
    assert body["total_agrees"] is False
    assert any("4500" in n and "9900" in n for n in body["notes"])


def test_amber_items_appear_on_the_receipt_and_are_never_priced(client):
    """Invariant 7 on paper: the counter saw something it would not name, left
    it off the total, and the customer is told rather than left to notice."""
    _bill(_ledger(), "s1", [("parle_g", 1000)], amber=("unknown_jar",))
    body = client.get("/receipt/s1").json()
    assert body["excluded_count"] == 1
    item = body["excluded"][0]
    assert item["sku_id"] == "unknown_jar"
    assert item["charged"] is False
    assert "price_paise" not in item
    assert body["total_paise"] == 1000
    assert any("could not be identified" in n for n in body["notes"])


def test_two_prices_for_one_product_stay_two_lines(client):
    """Averaging them would print a unit price nobody was charged."""
    _bill(_ledger(), "s1", [("parle_g", 1000), ("parle_g", 1200)])
    lines = client.get("/receipt/s1").json()["lines"]
    assert len(lines) == 2
    assert [ln["unit_paise"] for ln in lines] == [1000, 1200]
    assert [ln["qty"] for ln in lines] == [1, 1]


def test_a_counted_packet_with_no_price_is_not_a_free_packet(client):
    """A gap in the record is shown as a gap. A zero would be a claim that the
    shop gave something away."""
    _bill(_ledger(), "s1", [("parle_g", 1000)], unpriced=("mystery",))
    body = client.get("/receipt/s1").json()
    blank = [ln for ln in body["lines"] if ln["sku_id"] == "mystery"][0]
    assert blank["unit_paise"] is None
    assert blank["line_paise"] is None
    assert blank["priced"] is False
    assert body["unpriced_items"] == 1
    assert body["lines_sum_paise"] == 1000
    assert body["total_agrees"] is False
    assert any("no price on the audit chain" in n for n in body["notes"])


def test_product_names_come_from_the_catalogue(client):
    _catalogue(parle_g=_sku("Parle-G 80g", 1000))
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    line = client.get("/receipt/s1").json()["lines"][0]
    assert line["name"] == "Parle-G 80g"
    assert line["named_from_catalogue"] is True


def test_a_product_that_left_the_catalogue_keeps_its_id(client):
    """A bill from last month must stay readable after the product was
    deleted. The id is shown and the receipt says the name is missing."""
    _catalogue(soap=_sku("Lifebuoy", 3500))
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    line = client.get("/receipt/s1").json()["lines"][0]
    assert line["name"] == "parle_g"
    assert line["named_from_catalogue"] is False


def test_todays_catalogue_price_never_reprices_an_old_bill(client):
    """The catalogue supplies the NAME and never the money. This is the test
    that would fail if somebody 'helpfully' read price_paise off it."""
    _catalogue(parle_g=_sku("Parle-G 80g", 1000))
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    _catalogue(parle_g=_sku("Parle-G 80g", 9900))       # the price went up
    manage._CHAIN_CACHE.clear()
    body = client.get("/receipt/s1").json()
    assert body["lines"][0]["unit_paise"] == 1000
    assert body["total_paise"] == 1000


def test_an_unreadable_catalogue_costs_the_names_and_not_the_money(client):
    (manage.store_dir() / "catalog.json").write_text("{ not json",
                                                     encoding="utf-8")
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    body = client.get("/receipt/s1").json()
    assert body["ok"] is True
    assert body["total_paise"] == 1000
    assert body["lines"][0]["name"] == "parle_g"


def test_no_float_appears_anywhere_in_the_json(client):
    """INVARIANT 1. The catalogue on disk holds floats — footprint_mm, the
    gates, the taught vectors — and none of them may reach a receipt."""
    _catalogue(parle_g=_sku("Parle-G 80g", 1000))
    _profile()
    _bill(_ledger(), "s1", [("parle_g", 1050), ("parle_g", 1050)],
          amber=("jar",), mint=True, settle="webhook")
    body = client.get("/receipt/s1").json()
    assert _floats_in(body) == []


# =============================================================== settlement

def test_an_unsettled_bill_says_it_is_unsettled(client):
    """A receipt is a record of what happened, not a claim that money moved."""
    _bill(_ledger(), "s1", [("parle_g", 1000)], mint=True)
    body = client.get("/receipt/s1").json()
    assert body["settled"] is False
    assert body["settled_by_verified_webhook"] is False
    assert body["payment_state"] == "unpaid"
    assert body["payment_headline"] == "NOT PAID"
    assert "Nothing here says money moved" in body["payment_detail"]
    assert body["payment_id"] is None
    assert body["link_minted"] is True


def test_a_minted_link_alone_is_not_a_payment(client):
    """A payment link existing means somebody was asked. Invariant 2 says only
    the gateway's signed callback says they paid."""
    _bill(_ledger(), "s1", [("parle_g", 1000)], mint=True)
    body = client.get("/receipt/s1").json()
    assert body["payment_link_id"] == "plink_s1"
    assert body["settled"] is False


def test_a_webhook_settled_bill_is_paid_and_names_the_payment(client):
    _bill(_ledger(), "s1", [("parle_g", 1000)], mint=True, settle="webhook")
    body = client.get("/receipt/s1").json()
    assert body["settled"] is True
    assert body["settled_by"] == "webhook"
    assert body["settled_by_verified_webhook"] is True
    assert body["payment_state"] == "paid"
    assert body["payment_headline"] == "PAID"
    assert body["payment_id"] == "pay_s1"
    assert body["settled_at"]
    assert body["webhooks_seen"] == 1


def test_settlement_the_counter_recorded_alone_is_labelled_as_such(client):
    """kernel/intent.settled with no webhook line beside it. manage.py accepts
    it as a labelled fallback; a receipt must not print it as an unqualified
    'paid', or a bill turns green on this counter's own word."""
    _bill(_ledger(), "s1", [("parle_g", 1000)], mint=True, settle="kernel")
    body = client.get("/receipt/s1").json()
    assert body["settled"] is True
    assert body["settled_by"] == "kernel"
    assert body["settled_by_verified_webhook"] is False
    assert body["payment_state"] == "recorded_paid_by_the_counter"
    assert "not the gateway's confirmation" in body["payment_detail"]
    assert body["webhooks_seen"] == 0


def test_the_settlement_time_is_printed_in_the_words_a_person_reads(client):
    _bill(_ledger(), "s1", [("parle_g", 1000)], mint=True, settle="webhook")
    body = client.get("/receipt/s1").json()
    assert body["settled_at_human"] is not None
    assert body["settled_at_human"] in body["payment_detail"]
    assert "T05:" not in body["payment_detail"]


def test_counts_are_written_the_way_a_person_writes_them(client):
    """'1 item(s)' was written for a machine and is being read by somebody who
    just paid for something."""
    _bill(_ledger(), "one", [("parle_g", 1000)], amber=("a",))
    _bill(_ledger(), "two", [("parle_g", 1000)], amber=("a", "b"), at=100)
    assert any("1 item " in n for n in client.get("/receipt/one").json()["notes"])
    assert any("2 items " in n for n in client.get("/receipt/two").json()["notes"])


def test_payment_id_is_null_rather_than_a_placeholder(client):
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    body = client.get("/receipt/s1").json()
    assert body["payment_id"] is None
    assert body["payment_link_id"] is None


# ================================================================== the page

def test_the_page_is_html_and_carries_the_bill(client):
    _catalogue(parle_g=_sku("Parle-G 80g", 1000))
    _profile()
    _bill(_ledger(), "s1", [("parle_g", 1000), ("parle_g", 1000)])
    r = client.get("/receipt/s1/page")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    html = r.text
    assert "Sharma Kirana Store" in html
    assert "Parle-G 80g" in html
    assert "20.00" in html
    assert "2000 paise" in html


def test_the_page_is_self_contained(client):
    """The Content-Security-Policy this server sends is `default-src 'self';
    script-src 'self'`, so an external stylesheet or font would be blocked and
    the page would silently lose its layout. There is no script of any kind."""
    _profile()
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    html = client.get("/receipt/s1/page").text
    assert "<script" not in html.lower()
    assert "http://" not in html
    assert "https://" not in html
    assert "//cdn" not in html
    assert "@import" not in html


def test_the_page_escapes_a_product_name_that_looks_like_markup(client):
    """A catalogue is a file a person can edit. Its contents render as text."""
    _catalogue(parle_g=_sku("<script>alert('x')</script>", 1000))
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    html = client.get("/receipt/s1/page").text
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_the_page_escapes_a_shop_name_that_looks_like_markup(client):
    _profile(name="<img src=x onerror=alert(1)>")
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    html = client.get("/receipt/s1/page").text
    assert "<img src=x" not in html
    assert "&lt;img" in html


def test_an_unnamed_shop_is_stated_rather_than_invented(client):
    """A counter set up this morning has a catalogue and no signboard. That is
    not an error, and inventing a name would be."""
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    body = client.get("/receipt/s1").json()
    assert body["shop"]["configured"] is False
    assert body["shop"]["name"] is None
    assert "not been named" in client.get("/receipt/s1/page").text


def test_a_corrupt_shop_profile_is_reported_and_not_fatal(client):
    (manage.store_dir() / receipts.PROFILE_NAME).write_text(
        "{ half a file", encoding="utf-8")
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    body = client.get("/receipt/s1").json()
    assert body["ok"] is True
    assert body["shop"]["configured"] is False
    assert "could not be read" in body["shop"]["problem"]


def test_a_profile_in_an_unknown_format_is_not_guessed_at(client):
    _profile(format=999)
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    shop = client.get("/receipt/s1").json()["shop"]
    assert shop["configured"] is False
    assert shop["name"] is None
    assert "format" in shop["problem"]


def test_the_page_says_not_paid_in_words_a_customer_reads(client):
    _bill(_ledger(), "s1", [("parle_g", 1000)], mint=True)
    html = client.get("/receipt/s1/page").text
    assert "NOT PAID" in html
    assert "not paid" in html         # the browser tab title
    assert "Nothing here says money moved" in html


def test_the_page_says_paid_only_on_a_verified_webhook(client):
    _bill(_ledger(), "s1", [("parle_g", 1000)], mint=True, settle="webhook")
    html = client.get("/receipt/s1/page").text
    assert "NOT PAID" not in html
    assert "signed callback reached this counter" in html
    assert "pay_s1" in html


def test_the_page_qualifies_a_settlement_with_no_webhook_behind_it(client):
    _bill(_ledger(), "s1", [("parle_g", 1000)], mint=True, settle="kernel")
    html = client.get("/receipt/s1/page").text
    assert "recorded by the counter" in html
    assert "not the gateway&#x27;s confirmation" in html or \
           "not the gateway's confirmation" in html


def test_the_page_still_prints_when_the_host_header_is_useless(client):
    """A bad Host header costs the page its QR and nothing else. The address of
    this server is needed to draw a code, not to print a bill."""
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    r = client.get("/receipt/s1/page", headers={"Host": "not a host"})
    assert r.status_code == 200
    assert "10.00" in r.text
    assert "No code is shown on this bill" in r.text
    assert "<img" not in r.text


def test_the_page_shows_the_amber_items_it_did_not_charge_for(client):
    _bill(_ledger(), "s1", [("parle_g", 1000)], amber=("unknown_jar",))
    html = client.get("/receipt/s1/page").text
    assert "not charged" in html
    assert "unknown_jar" in html


def test_a_customer_is_never_shown_the_chains_own_reason_codes(client):
    """`exit_crossing_committed_amber_excluded` is what the ledger writes and
    it is not English. The raw string stays in the JSON for the shopkeeper to
    match against History; the page prints the sentence."""
    _bill(_ledger(), "s1", [("parle_g", 1000)], amber=("unknown_jar",))
    item = client.get("/receipt/s1").json()["excluded"][0]
    assert item["reason"] == "exit_crossing_committed_amber_excluded"
    assert item["why"] == receipts.EXCLUSION_LABELS[item["reason"]]
    html = client.get("/receipt/s1/page").text
    assert "exit_crossing_committed" not in html
    assert "could not name this" in html


def test_the_time_is_printed_in_the_words_a_person_reads(client):
    """The chain stamps UTC. A customer told their bill was at 05:27 when the
    shop clock said 10:57 reads it as somebody else's bill."""
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    body = client.get("/receipt/s1").json()
    assert body["at"].startswith("2026-08-29T05:00:0")
    assert body["at_human"] is not None
    assert "August 2026" in body["at_human"]
    assert body["at_human"] in client.get("/receipt/s1/page").text


def test_a_timestamp_that_will_not_parse_says_nothing_rather_than_guessing():
    assert receipts.human_time("not a timestamp") is None
    assert receipts.human_time(None) is None
    assert receipts.human_time("") is None


def test_a_naive_timestamp_is_read_as_utc_the_way_the_chain_writes_it():
    """Every stamp in the chain carries an offset. One that does not is read as
    UTC rather than as local time, which is what the rest of this program
    assumes — guessing local would move a bill by hours."""
    aware = receipts.human_time("2026-08-29T05:27:59.359+00:00")
    naive = receipts.human_time("2026-08-29T05:27:59.359")
    assert aware == naive


# ==================================================================== the QR

def test_the_qr_decodes_back_to_this_receipts_own_page(client):
    """A round trip through a real encoder and a real decoder. Asserting the
    bytes are a PNG would not prove a phone can follow it."""
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    _bill(_ledger(), "s1", [("parle_g", 1000)])
    r = client.get("/receipt/s1/qr", headers={"Host": "192.168.1.7:8790"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
    decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
    assert decoded == "http://192.168.1.7:8790/receipt/s1/page"
    assert r.headers["X-Gawaah-Receipt-Url"] == decoded


def test_the_qr_carries_the_address_the_request_arrived_on(client):
    """Not a configured default: 127.0.0.1 is the one address guaranteed not to
    work from the customer's phone."""
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    r = client.get("/receipt/s1/qr", headers={"Host": "shop-laptop.local:8790"})
    assert r.headers["X-Gawaah-Receipt-Url"] == \
        "http://shop-laptop.local:8790/receipt/s1/page"


def test_a_forwarded_https_proto_is_honoured(client):
    """Behind a tunnel the browser reached https; a QR carrying http would be a
    downgrade the shopkeeper never chose."""
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    r = client.get("/receipt/s1/qr",
                   headers={"Host": "abc.trycloudflare.com",
                            "X-Forwarded-Proto": "https"})
    assert r.headers["X-Gawaah-Receipt-Url"].startswith(
        "https://abc.trycloudflare.com/")


def test_a_gateway_host_is_refused_by_name(client):
    """A receipt code opens a bill and never asks for money. This is the guard
    that has to be in place before somebody gives this endpoint a parameter."""
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    r = client.get("/receipt/s1/qr", headers={"Host": "rzp.io"})
    assert r.status_code == 400
    body = r.json()
    assert body["reason"] == receipts.R_REFUSED_QR
    assert "payment gateway host" in body["detail"]


def test_a_subdomain_of_a_gateway_host_is_refused_too(client):
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    r = client.get("/receipt/s1/qr", headers={"Host": "pay.razorpay.com"})
    assert r.json()["reason"] == receipts.R_REFUSED_QR


@pytest.mark.parametrize("host", [
    "evil.com\\.rzp.io",      # one host to RFC 3986, two to WHATWG
    "exa mple.com",
    "host#fragment",
    "[::1]:8790",             # a stated limit: IPv6 literals are refused
    "",
])
def test_a_host_header_that_is_not_a_plain_host_is_refused(client, host):
    """The Host header is client-controlled. Somebody who can set it on the
    shopkeeper's own request is already inside the shop; this stops the printed
    code being the thing that lets them in."""
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    r = client.get("/receipt/s1/qr", headers={"Host": host})
    assert r.status_code == 400
    assert r.json()["reason"] in (receipts.R_NO_HOST, receipts.R_REFUSED_QR)


def test_a_missing_qr_encoder_is_a_named_refusal_not_a_crash(client,
                                                             monkeypatch):
    """cv2 is a heavy optional-feeling dependency. Its absence costs the code
    and not the bill, and the refusal says where the bill still is."""
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    monkeypatch.setitem(sys.modules, "cv2", None)
    r = client.get("/receipt/s1/qr")
    assert r.status_code == 400
    body = r.json()
    assert body["reason"] == receipts.R_NO_ENCODER
    assert "/receipt/s1/page" in body["detail"]


def test_the_qr_size_is_clamped_at_both_ends(client):
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    _bill(_ledger(), "s1", [("parle_g", 1000)])
    small = client.get("/receipt/s1/qr?px=1")
    big = client.get("/receipt/s1/qr?px=99999")
    for r, floor in ((small, receipts.MIN_QR_PX), (big, receipts.MAX_QR_PX)):
        img = cv2.imdecode(np.frombuffer(r.content, np.uint8),
                           cv2.IMREAD_COLOR)
        # side plus the quiet zone on both edges
        assert img.shape[0] >= floor
        assert img.shape[0] <= receipts.MAX_QR_PX + (receipts.MAX_QR_PX // 7)


def test_a_session_id_needing_percent_encoding_survives_the_round_trip(client):
    """'#' unencoded would turn everything after it into a URL fragment and
    hand the phone a receipt for a different bill."""
    assert receipts.page_path("a#b") == "/receipt/a%23b/page"


# ================================================================== the link

def test_link_says_a_loopback_address_cannot_be_reached_by_a_phone(client):
    """A QR reading http://127.0.0.1:8790 is a perfectly good QR that no phone
    on earth can open, and that failure is silent unless something says it."""
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    body = client.get("/receipt/s1/link",
                      headers={"Host": "127.0.0.1:8790"}).json()
    assert body["ok"] is True
    assert body["reachable_from_a_phone"] is False
    assert "try to reach itself" in body["note"]


def test_link_on_a_network_address_is_reachable(client):
    _bill(_ledger(), "s1", [("parle_g", 1000)], settle="webhook")
    body = client.get("/receipt/s1/link",
                      headers={"Host": "192.168.1.7:8790"}).json()
    assert body["reachable_from_a_phone"] is True
    assert body["url"] == "http://192.168.1.7:8790/receipt/s1/page"
    assert body["qr_url"] == "/receipt/s1/qr"
    assert body["settled_by_verified_webhook"] is True
    assert body["total_paise"] == 1000


def test_the_page_warns_when_its_own_code_points_at_a_loopback(client):
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    html = client.get("/receipt/s1/page",
                      headers={"Host": "localhost:8790"}).text
    assert "points at" in html and "whatever device opens it" in html


# ================================================================= the chain

def test_a_broken_chain_is_reported_on_the_receipt(client):
    """manage.py serves the verified prefix and says so loudly. A receipt built
    from that prefix has to carry the same warning or it launders it."""
    ledger = _ledger()
    _bill(ledger, "s1", [("parle_g", 1000)])
    complete = len(manage.ledger_path().read_text(
        encoding="utf-8").splitlines())
    _bill(ledger, "s2", [("soap", 3500)], at=100)

    # Doctor the FIRST line of the second bill, so the break falls after s1 is
    # whole. s1 is then inside the verified prefix and must still print — with
    # the warning, which is the thing being tested.
    path = manage.ledger_path()
    lines = path.read_text(encoding="utf-8").splitlines()
    doctored = json.loads(lines[complete])
    doctored["total_paise"] = 999999
    lines[complete] = json.dumps(doctored, sort_keys=True, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manage._CHAIN_CACHE.clear()

    body = client.get("/receipt/s1").json()
    assert body["ok"] is True
    assert body["chain"]["ok"] is False
    assert any("does not verify" in n for n in body["notes"])
    assert "does not verify" in client.get("/receipt/s1/page").text


def test_a_bill_beyond_a_chain_break_is_unknown_rather_than_approximated(
        client):
    """Lines after a break are not evidence of anything, so the bill they would
    have described is absent — never reconstructed from what came before."""
    ledger = _ledger()
    _bill(ledger, "s1", [("parle_g", 1000)])
    path = manage.ledger_path()
    lines = path.read_text(encoding="utf-8").splitlines()
    # Break the chain, then write a whole second bill after the break.
    broken = json.loads(lines[-1])
    broken["prev_hash"] = "0" * 64
    lines[-1] = json.dumps(broken, sort_keys=True, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _bill(Ledger(path), "s2", [("soap", 3500)], at=100)
    manage._CHAIN_CACHE.clear()

    assert client.get("/receipt/s2").status_code == 404


# ============================================================ writes and 500s

def test_a_receipt_writes_nothing_anywhere(client):
    """Rule 4 of the module: no receipt file, no view counter, no audit line.
    The chain in results/ has a single writer holding a lock in another
    process, and a page a customer can refresh must never queue behind it."""
    _catalogue(parle_g=_sku("Parle-G 80g", 1000))
    _profile()
    _bill(_ledger(), "s1", [("parle_g", 1000)], mint=True, settle="webhook")
    root = manage.data_dir()
    before = {p: p.stat().st_mtime_ns for p in sorted(root.rglob("*"))}

    for path in ("/receipt/s1", "/receipt/s1/page", "/receipt/s1/qr",
                 "/receipt/s1/link"):
        assert client.get(path).status_code == 200

    after = {p: p.stat().st_mtime_ns for p in sorted(root.rglob("*"))}
    assert before == after


def test_an_internal_error_is_a_400_with_a_name_and_never_a_500(client,
                                                                monkeypatch):
    """A receipt that crashes at the counter tells a customer nothing they can
    act on. The exception type survives into the detail because it is usually
    the whole diagnosis."""
    def _boom():
        raise RuntimeError("the disk went away")

    monkeypatch.setattr(manage, "read_chain", _boom)
    for path in ("/receipt/s1", "/receipt/s1/page", "/receipt/s1/qr",
                 "/receipt/s1/link"):
        r = client.get(path)
        assert r.status_code == 400, path
        body = r.json()
        assert body["reason"] == receipts.R_INTERNAL
        assert "RuntimeError" in body["detail"]
        assert "the disk went away" in body["detail"]


# ================================================= pins against other modules

def test_the_profile_constants_match_the_module_that_writes_the_file():
    """receipts.py names the shop profile file so it can be read on a counter
    whose admin module is not loaded. If shopadmin renames it, every receipt
    would silently lose the shop name — so the two constants are pinned."""
    shopadmin = pytest.importorskip("gawaah.shopadmin")
    assert receipts.PROFILE_NAME == shopadmin.PROFILE_NAME
    assert receipts.PROFILE_FORMAT == shopadmin.PROFILE_FORMAT


def test_the_gateway_host_list_matches_the_tills():
    """Refusing to point a receipt code at a gateway is only as good as the
    list of gateways. It is the till's list."""
    up = pytest.importorskip("tools.upload_app")
    assert set(receipts.GATEWAY_HOSTS) == set(up.LINK_HOSTS)


@pytest.mark.parametrize("payload", [
    "upi://pay?pa=someone@bank&am=139.50",
    "UPI://pay?pa=x",
    "\tupi://pay?pa=x",
    "  upi:pay",
])
def test_the_upi_check_refuses_what_a_scanner_would_read_as_upi(payload):
    """Copied from tools/upload_app.py, including the leading-whitespace strip:
    a scanner reading a payload off a screen does not care about a tab."""
    assert receipts._looks_like_upi(payload) is True


def test_the_upi_check_does_not_refuse_an_ordinary_address():
    assert receipts._looks_like_upi("http://192.168.1.7:8790/receipt/s1/page") \
        is False


def test_the_upi_check_stands_on_its_own_without_the_till(monkeypatch):
    """The till's function is used when the till is in the process; this body
    is what answers when it is not, and it has to agree."""
    monkeypatch.setattr(receipts, "_till_if_loaded", lambda: None)
    assert receipts._looks_like_upi("\tUPI://pay?pa=x") is True
    assert receipts._looks_like_upi("http://127.0.0.1/receipt/s1/page") is False


def test_the_gateway_host_list_stands_on_its_own_without_the_till(monkeypatch,
                                                                  client):
    monkeypatch.setattr(receipts, "_till_if_loaded", lambda: None)
    assert receipts._gateway_hosts() == receipts.GATEWAY_HOSTS
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    r = client.get("/receipt/s1/qr", headers={"Host": "rzp.link"})
    assert r.json()["reason"] == receipts.R_REFUSED_QR


def test_the_shop_profile_is_one_file_whether_the_till_is_loaded_or_not(
        monkeypatch):
    """Two ways of finding one file is how a test writes to a live shop. Both
    paths are asserted equal rather than assumed to be."""
    shopadmin = pytest.importorskip("gawaah.shopadmin")
    up = pytest.importorskip("tools.upload_app")
    up.set_store_dir(manage.store_dir())
    assert Path(shopadmin.profile_path()) == receipts._profile_path()
    monkeypatch.setattr(receipts, "_till_if_loaded", lambda: None)
    assert receipts._profile_path() == \
        manage.store_dir() / receipts.PROFILE_NAME


def test_the_receipt_is_derived_and_says_where_from(client):
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    body = client.get("/receipt/s1").json()
    assert "hash-chained audit log" in body["derived_from"]
    assert body["settles_money"] is False
    assert body["chain"]["path"] == str(manage.ledger_path())


def test_the_router_carries_no_prefix_and_absolute_paths():
    """The orchestrator mounts it bare. A prefix added here would move every
    path in this file and the QR would encode an address that 404s."""
    paths = {route.path for route in receipts.router.routes}
    assert paths == {
        "/receipt/{session_id}",
        "/receipt/{session_id}/page",
        "/receipt/{session_id}/qr",
        "/receipt/{session_id}/link",
    }
