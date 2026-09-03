"""gawaah/manage.py — the three management screens.

These screens exist to be BELIEVED. A history that quietly drops a bill, an
inventory that invents a stock level, or a settings page that says "waiting"
when the webhook path is dead are each worse than no screen at all, because
each one replaces a shopkeeper's uncertainty with false confidence.

So the suite is organised around the four ways this module could lie:

  1. It could show a number it did not derive       -> the derivation tests
  2. It could hide something it did derive          -> the amber-exclusion and
                                                       fell-out-of-catalogue tests
  3. It could trust a chain that does not verify    -> the corruption tests
  4. It could leak a secret while looking helpful   -> the settings tests

Every fixture builds a REAL hash-chained ledger with gawaah.ledger.Ledger, so
the chain the code verifies is the chain the writer wrote. Nothing here asserts
against a hand-typed hash.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import manage  # noqa: E402
from gawaah.ledger import Ledger, verify  # noqa: E402

T0 = datetime(2026, 8, 29, 5, 0, 0, tzinfo=timezone.utc)


def _ts(offset_s: int) -> str:
    return (T0 + timedelta(seconds=offset_s)).isoformat()


# ------------------------------------------------------------------ fixtures

@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Nothing in this suite may see, let alone write, results/.

    A test harness once destroyed the live catalogue by ignoring
    GAWAAH_SHOP_DIR, so both overrides are set for EVERY test whether it uses
    them or not, and the chain cache is dropped so one test's ledger can never
    answer another test's request.
    """
    data = tmp_path / "data"
    shop = tmp_path / "data" / "shop"
    shop.mkdir(parents=True)
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(data))
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(shop))
    manage._CHAIN_CACHE.clear()
    yield
    manage._CHAIN_CACHE.clear()


@pytest.fixture
def client(monkeypatch) -> TestClient:
    """The router mounted the way the orchestrator will mount it: bare."""
    app = FastAPI()
    app.include_router(manage.router)
    # The money service is a separate process that may or may not be running on
    # this machine. A unit test that silently passes or fails on that is not a
    # test, so the seam is closed by default and opened deliberately.
    monkeypatch.setattr(manage, "paisa_get", lambda path: (503, {
        "ok": False, "reason": "paisa_unreachable", "detail": "not started"}))
    return TestClient(app)


def _ledger() -> Ledger:
    return Ledger(manage.ledger_path())


def _bill(
    ledger: Ledger,
    session_id: str,
    lines: list[tuple[str, int]],
    *,
    amber: list[str] | None = None,
    at: int = 0,
    close: bool = True,
    mint: bool = False,
    settle: bool = False,
) -> int:
    """Write one session into the chain the way the real modules write it.

    The event shapes and reason strings are copied from results/audit.jsonl, not
    invented: if session.py renames `exit_crossing_committed_amber_excluded`,
    this fixture keeps passing while the product breaks, so the shapes are
    pinned separately in test_event_shapes_match_the_shipped_ledger.
    """
    amber = amber or []
    clock = at
    running = 0
    ledger.append(ts=_ts(clock), module="session", event="session",
                  session_id=session_id, reason="session_opened",
                  **{"from": "SETUP", "to": "SETUP"}, total_paise=0)
    clock += 1
    ledger.append(ts=_ts(clock), module="session", event="mat_lock",
                  session_id=session_id, reason="mat_reacquired",
                  **{"from": "SETUP", "to": "IDLE"}, total_paise=0)
    for i, (sku, price) in enumerate(lines):
        clock += 1
        item_id = f"{sku}#{i}"
        ledger.append(ts=_ts(clock), module="session", event="placement",
                      session_id=session_id, reason="placement_seen",
                      item_id=item_id, name=sku,
                      **{"from": "IDLE", "to": "MEASURING"}, total_paise=running)
        clock += 1
        ledger.append(ts=_ts(clock), module="session", event="classify",
                      session_id=session_id, reason="priced_from_gallery",
                      item_id=item_id, price_paise=price, abstained=False,
                      excluded_from_total=False,
                      **{"from": "MEASURING", "to": "PRICED"}, total_paise=running)
        running += price
        clock += 1
        ledger.append(ts=_ts(clock), module="session", event="exit",
                      session_id=session_id, reason="exit_crossing_committed",
                      item_id=item_id, price_paise=price, abstained=False,
                      excluded_from_total=False,
                      **{"from": "PRICED", "to": "BASKET_OPEN"}, total_paise=running)
    for sku in amber:
        clock += 1
        ledger.append(ts=_ts(clock), module="session", event="classify",
                      session_id=session_id, reason="unknown_sku", item_id=sku,
                      abstained=True, excluded_from_total=True,
                      **{"from": "MEASURING", "to": "AMBER"}, total_paise=running)
        clock += 1
        ledger.append(ts=_ts(clock), module="session", event="exit",
                      session_id=session_id,
                      reason="exit_crossing_committed_amber_excluded",
                      item_id=sku, abstained=True, excluded_from_total=True,
                      **{"from": "AMBER", "to": "BASKET_OPEN"}, total_paise=running)
    if close:
        clock += 1
        ledger.append(ts=_ts(clock), module="session", event="done",
                      session_id=session_id, reason="intent_requested",
                      lines=len(lines), amber_excluded=len(amber),
                      intent_amount_paise=running,
                      **{"from": "BASKET_OPEN", "to": "AWAITING_SETTLEMENT"},
                      total_paise=running)
    if mint:
        clock += 1
        ledger.append(ts=_ts(clock), module="paisa", event="intent.minted",
                      session_id=session_id, minted=True, replayed=False,
                      amount_paise=running, amber_items=list(amber),
                      priced_items=[s for s, _ in lines],
                      payment_link_id=f"plink_{session_id}")
    if settle:
        clock += 1
        ledger.append(ts=_ts(clock), module="kernel", event="intent.settled",
                      session_id=session_id, amount_paise=running,
                      payment_id=f"pay_{session_id}", from_state="CALLING",
                      to_state="SETTLED", reason=None)
        clock += 1
        ledger.append(ts=_ts(clock), module="session", event="webhook",
                      session_id=session_id, reason="settled_green",
                      razorpay_event="payment.captured",
                      event_id=f"evt_{session_id}", webhook_amount_paise=running,
                      money_authorised=True,
                      **{"from": "AWAITING_SETTLEMENT", "to": "PAID"},
                      total_paise=running)
    return running


def _catalogue(**skus: dict) -> None:
    """Write a catalog.json shaped like the one on disk in results/shop/."""
    path = manage.store_dir() / "catalog.json"
    path.write_text(json.dumps({
        "format": 2, "dim": 4,
        "gates": {"phi": 0.9, "theta": 0.1, "tau_mm": 4.0,
                  "phi_appearance_only": 0.92},
        "skus": skus,
    }), encoding="utf-8")


def _mat_sku(name: str, price: int) -> dict:
    return {"name": name, "price_paise": price, "footprint_mm": 95.1,
            "taught_by": "mat_measured", "vectors": [[1.0, 0.0, 0.0, 0.0]],
            "photo": None, "photo_bytes": 0}


# ============================================================ the empty counter

def test_absent_audit_log_is_an_empty_shop_not_an_error(client):
    """A counter installed this morning has no bills. That is a fact about the
    shop, not a failure of the screen, and it must not read as one."""
    assert not manage.ledger_path().exists()
    r = client.get("/manage/history")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["bills"] == []
    assert body["chain"]["exists"] is False
    assert body["chain"]["ok"] is True


def test_an_empty_but_present_log_is_distinguished_from_an_absent_one(client):
    """Deleted and never-written look identical in a bill count and are very
    different things. `exists` is reported separately from `ok` so the page can
    tell them apart."""
    manage.ledger_path().parent.mkdir(parents=True, exist_ok=True)
    manage.ledger_path().write_text("", encoding="utf-8")
    body = client.get("/manage/history").json()
    assert body["chain"]["exists"] is True
    assert body["chain"]["lines_verified"] == 0
    assert body["bills"] == []


def test_inventory_with_no_catalogue_and_no_log_says_nothing_rather_than_zero(client):
    body = client.get("/manage/inventory").json()
    assert body["ok"] is True
    assert body["count"] == 0
    assert body["items"] == []
    assert body["sold_but_not_in_catalogue"] == []


# ================================================================ the bill book

def test_history_derives_a_bill_from_the_done_event(client):
    total = _bill(_ledger(), "s1", [("parle_g", 1000), ("soap", 3500)], mint=True)
    body = client.get("/manage/history").json()
    assert len(body["bills"]) == 1
    bill = body["bills"][0]
    assert bill["session_id"] == "s1"
    assert bill["total_paise"] == total == 4500
    assert bill["total_rupees"] == "45.00"
    assert bill["payment_link_id"] == "plink_s1"


def test_a_session_that_never_closed_is_not_a_bill(client):
    """A probe, or a customer who walked away, has line items and no total. A
    bill book that listed them would show takings nobody ever asked to charge."""
    ledger = _ledger()
    _bill(ledger, "closed", [("a", 100)])
    _bill(ledger, "abandoned", [("b", 200)], close=False)
    body = client.get("/manage/history").json()
    assert [b["session_id"] for b in body["bills"]] == ["closed"]
    # but it is still readable one at a time, so the gap is explicable
    assert body["sessions_in_ledger"] == 2
    assert client.get("/manage/history/abandoned").status_code == 200


def test_history_is_newest_first(client):
    ledger = _ledger()
    _bill(ledger, "first", [("a", 100)], at=0)
    _bill(ledger, "second", [("a", 100)], at=1000)
    _bill(ledger, "third", [("a", 100)], at=2000)
    body = client.get("/manage/history").json()
    assert [b["session_id"] for b in body["bills"]] == ["third", "second", "first"]


def test_limit_truncates_the_newest_end_and_reports_what_it_dropped(client):
    ledger = _ledger()
    for i in range(5):
        _bill(ledger, f"s{i}", [("a", 100)], at=i * 100)
    body = client.get("/manage/history?limit=2").json()
    assert [b["session_id"] for b in body["bills"]] == ["s4", "s3"]
    assert body["count"] == 2
    assert body["matched"] == 5          # the shopkeeper is told there are more


def test_since_filters_by_the_moment_the_basket_closed(client):
    ledger = _ledger()
    _bill(ledger, "old", [("a", 100)], at=0)
    _bill(ledger, "new", [("a", 100)], at=10_000)
    cutoff = (T0 + timedelta(seconds=5_000)).isoformat()
    body = client.get("/manage/history", params={"since": cutoff}).json()
    assert [b["session_id"] for b in body["bills"]] == ["new"]


def test_since_survives_a_plus_that_a_url_turned_into_a_space(client):
    """'+00:00' pasted into a query string arrives as ' 00:00'. Refusing a
    timestamp the shopkeeper copied out of this very product would be a refusal
    he cannot act on."""
    _bill(_ledger(), "s1", [("a", 100)], at=10_000)
    raw = (T0 + timedelta(seconds=5_000)).isoformat().replace("+", " ")
    body = client.get(f"/manage/history?since={raw}").json()
    assert body["ok"] is True
    assert len(body["bills"]) == 1


def test_settlement_is_credited_to_the_webhook_not_the_kernel(client):
    """INVARIANT 2: only a signature-verified webhook turns a bill green. The
    kernel's own settled row is downstream of the same webhook and is accepted
    only as a labelled fallback, so a reader can always tell which line was
    believed."""
    _bill(_ledger(), "paid", [("a", 6900)], mint=True, settle=True)
    bill = client.get("/manage/history").json()["bills"][0]
    assert bill["settled"] is True
    assert bill["settled_by"] == "webhook"
    assert bill["payment_id"] == "pay_paid"
    assert bill["state"] == "PAID"


def test_an_unsettled_bill_is_never_reported_as_paid(client):
    _bill(_ledger(), "unpaid", [("a", 6900)], mint=True, settle=False)
    bill = client.get("/manage/history").json()["bills"][0]
    assert bill["settled"] is False
    assert bill["settled_at"] is None
    assert bill["payment_id"] is None
    assert bill["minted"] is True       # a link was issued; nobody paid it


# ============================================================ the amber refusal

def test_an_amber_item_is_listed_and_carries_no_price(client):
    """Excluding an item the counter could not identify is invariant 7 working.
    A history that hid it would be lying by omission, and one that gave it a
    price would be inventing the very number the counter refused to guess."""
    total = _bill(_ledger(), "s1", [("parle_g", 1000)], amber=["unknown_sachet"])
    assert total == 1000
    detail = client.get("/manage/history/s1").json()
    assert [line["sku_id"] for line in detail["line_items"]] == ["parle_g"]
    assert len(detail["excluded"]) == 1
    excluded = detail["excluded"][0]
    assert excluded["sku_id"] == "unknown_sachet"
    assert excluded["price_paise"] is None
    assert excluded["price_rupees"] is None
    assert excluded["counted"] is False
    assert excluded["abstained"] is True


def test_the_exclusion_is_visible_in_the_list_view_too(client):
    """The detail page is not enough: a shopkeeper scanning the day's bills has
    to be able to see WHICH ones came up short without opening each."""
    _bill(_ledger(), "s1", [("parle_g", 1000)], amber=["unknown_sachet"])
    bill = client.get("/manage/history").json()["bills"][0]
    assert bill["excluded_lines"] == 1
    assert bill["excluded"][0]["sku_id"] == "unknown_sachet"


def test_an_amber_item_is_excluded_from_the_total_it_reports(client):
    _bill(_ledger(), "s1", [("a", 1000), ("b", 2000)], amber=["ghost"])
    detail = client.get("/manage/history/s1").json()
    assert detail["total_paise"] == 3000
    assert detail["lines_sum_paise"] == 3000
    assert detail["total_agrees"] is True


def test_line_ids_are_resolved_back_to_their_sku(client):
    """paisa writes one line per placed packet as f'{sku}#{i}' so that two
    Parle-G are two lines. Both the id and the sku are reported: the first is
    what the ledger says, the second is what the shelf calls it."""
    _bill(_ledger(), "s1", [("parle_g", 1000), ("parle_g", 1000)])
    detail = client.get("/manage/history/s1").json()
    assert [line["item_id"] for line in detail["line_items"]] == \
        ["parle_g#0", "parle_g#1"]
    assert {line["sku_id"] for line in detail["line_items"]} == {"parle_g"}


def test_sku_of_never_splits_a_legal_sku_id():
    """shop_store.SKU_RE forbids '#', so splitting on the first one is
    unambiguous rather than a guess."""
    assert manage.sku_of("parle_g_200g") == "parle_g_200g"
    assert manage.sku_of("parle_g_200g#0") == "parle_g_200g"
    assert manage.sku_of("a.b-c_1#12") == "a.b-c_1"
    assert manage.sku_of("") == ""


# ========================================================== the broken chain

def test_a_corrupted_chain_serves_only_the_verified_prefix(client):
    """A line whose hash does not recompute is not evidence of anything, so the
    bills after it are ABSENT — never approximated, never quietly adjusted."""
    ledger = _ledger()
    _bill(ledger, "before", [("a", 100)], at=0)
    _bill(ledger, "after", [("b", 999_99)], at=1000)

    path = manage.ledger_path()
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[-1])
    tampered["total_paise"] = 1                       # same hash, new content
    lines[-1] = json.dumps(tampered, sort_keys=True, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manage._CHAIN_CACHE.clear()

    ok, _, _, error = verify(path)
    assert ok is False and error                       # the premise of the test

    body = client.get("/manage/history").json()
    assert body["ok"] is True                          # the request succeeded
    assert body["chain"]["ok"] is False                # the CHAIN did not
    assert body["chain"]["error"] == error             # verbatim, not paraphrased
    assert [b["session_id"] for b in body["bills"]] == ["before"]


def test_a_chain_break_is_reported_on_every_screen(client):
    """The banner has to be where the shopkeeper is looking, not only where the
    corruption happened to be noticed."""
    ledger = _ledger()
    _bill(ledger, "s1", [("a", 100)])
    path = manage.ledger_path()
    path.write_text(path.read_text(encoding="utf-8") + '{"hash":"deadbeef"}\n',
                    encoding="utf-8")
    manage._CHAIN_CACHE.clear()

    assert client.get("/manage/history").json()["chain"]["ok"] is False
    assert client.get("/manage/inventory").json()["chain"]["ok"] is False
    assert client.get("/manage/settings").json()["ledger"]["chain_ok"] is False


def test_an_unparseable_line_truncates_rather_than_crashing(client):
    _bill(_ledger(), "s1", [("a", 100)])
    path = manage.ledger_path()
    path.write_text(path.read_text(encoding="utf-8") + "{not json at all\n",
                    encoding="utf-8")
    manage._CHAIN_CACHE.clear()
    r = client.get("/manage/history")
    assert r.status_code == 200
    assert r.json()["chain"]["ok"] is False
    assert len(r.json()["bills"]) == 1


def test_the_chain_cache_never_serves_a_stale_answer(client):
    """Keyed on mtime and size, which an append-only file cannot repeat. A cache
    that lied here would show the shopkeeper a bill book missing this morning."""
    ledger = _ledger()
    _bill(ledger, "s1", [("a", 100)])
    assert len(client.get("/manage/history").json()["bills"]) == 1
    _bill(Ledger(manage.ledger_path()), "s2", [("b", 200)])
    assert len(client.get("/manage/history").json()["bills"]) == 2


# =============================================================== the inventory

def test_inventory_counts_sales_from_the_chain_not_from_a_second_store(client):
    _catalogue(parle_g=_mat_sku("Parle-G", 1000))
    ledger = _ledger()
    _bill(ledger, "s1", [("parle_g", 1000)], at=0)
    _bill(ledger, "s2", [("parle_g", 1000), ("parle_g", 1000)], at=100)
    row = client.get("/manage/inventory").json()["items"][0]
    assert row["sku_id"] == "parle_g"
    assert row["billed_count"] == 3
    assert row["price_paise"] == 1000
    assert row["taught_by"] == "mat_measured"
    assert row["taught_label"] == "on the printed mat"
    assert row["last_billed_at"] is not None


def test_billed_and_settled_are_counted_separately(client):
    """Collapsing them would lie in one direction or the other: `settled` alone
    shows a shop that has sold almost nothing, `billed` alone counts baskets
    nobody ever paid for."""
    _catalogue(parle_g=_mat_sku("Parle-G", 1000))
    ledger = _ledger()
    _bill(ledger, "paid", [("parle_g", 1000)], at=0, mint=True, settle=True)
    _bill(ledger, "unpaid", [("parle_g", 1000)], at=100, mint=True)
    row = client.get("/manage/inventory").json()["items"][0]
    assert row["billed_count"] == 2
    assert row["settled_count"] == 1
    assert row["last_settled_at"] is not None


def test_an_unclosed_session_does_not_count_as_a_sale(client):
    _catalogue(parle_g=_mat_sku("Parle-G", 1000))
    _bill(_ledger(), "abandoned", [("parle_g", 1000)], close=False)
    assert client.get("/manage/inventory").json()["items"][0]["billed_count"] == 0


def test_a_sku_sold_but_since_removed_is_surfaced_not_hidden(client):
    """Otherwise the sales column silently stops adding up to the bill book and
    the shopkeeper chasing the difference has nowhere to look."""
    _catalogue(parle_g=_mat_sku("Parle-G", 1000))
    _bill(_ledger(), "s1", [("parle_g", 1000), ("discontinued", 500)])
    body = client.get("/manage/inventory").json()
    assert [r["sku_id"] for r in body["items"]] == ["parle_g"]
    gone = body["sold_but_not_in_catalogue"]
    assert [r["sku_id"] for r in gone] == ["discontinued"]
    assert gone[0]["billed_count"] == 1
    assert gone[0]["in_catalogue"] is False
    assert gone[0]["price_paise"] is None      # no catalogue, so no price. None.


def test_the_three_ways_of_teaching_are_told_apart(client):
    """Photo-taught and code-only both live in the same sidecar and differ only
    by whether there are vectors. Reporting them as one would hide which
    products have no appearance check at all."""
    _catalogue(on_mat=_mat_sku("On the mat", 1000))
    (manage.store_dir() / "appearance_only.json").write_text(json.dumps({
        "format": 2,
        "skus": {
            "by_photo": {"name": "By photo", "price_paise": 2200,
                         "footprint_mm": None, "taught_with": "appearance_only",
                         "vectors": [[1.0, 0.0]], "photo": None},
            "by_code": {"name": "By code", "price_paise": 9900,
                        "footprint_mm": None, "taught_with": "appearance_only",
                        "vectors": [], "photo": None},
        },
    }), encoding="utf-8")
    (manage.store_dir() / "product_codes.json").write_text(json.dumps({
        "format": 1, "codes": {"8901234567890": "by_code"}}), encoding="utf-8")

    rows = {r["sku_id"]: r for r in client.get("/manage/inventory").json()["items"]}
    assert rows["on_mat"]["taught_by"] == "mat_measured"
    assert rows["by_photo"]["taught_by"] == "appearance_only"
    assert rows["by_code"]["taught_by"] == "product_code_only"
    assert rows["by_code"]["codes"] == ["8901234567890"]
    assert rows["on_mat"]["codes"] == []
    assert {r["taught_label"] for r in rows.values()} == {
        "on the printed mat", "from a photograph", "by its printed code"}


def test_a_mat_record_shadows_the_sidecar_for_the_same_sku(client):
    """A product re-taught on the mat is the stronger record — it has
    millimetres — and the catalogue must not report it as appearance-only."""
    _catalogue(dup=_mat_sku("Re-taught on the mat", 1000))
    (manage.store_dir() / "appearance_only.json").write_text(json.dumps({
        "format": 2, "skus": {"dup": {"name": "Old photo", "price_paise": 999,
                                      "footprint_mm": None, "vectors": [[1.0]],
                                      "photo": None}}}), encoding="utf-8")
    row = client.get("/manage/inventory").json()["items"][0]
    assert row["taught_by"] == "mat_measured"
    assert row["price_paise"] == 1000


def test_a_hand_edited_catalogue_is_named_not_crashed_on(client):
    """A management screen exists so the shopkeeper can look at a catalogue that
    has gone wrong. One that raises on a bad file shows him nothing at the one
    moment he needs to see it."""
    (manage.store_dir() / "catalog.json").write_text("{ this is not json",
                                                     encoding="utf-8")
    r = client.get("/manage/inventory")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["catalogue_problems"]
    assert body["catalogue_problems"][0]["file"] == "catalog.json"


def test_a_non_integer_price_on_disk_is_withheld_not_rendered(client):
    """A float here is invariant 1 broken on disk. Showing it would launder it
    into a number the shopkeeper believes."""
    _catalogue(bad={"name": "Bad", "price_paise": 10.5, "footprint_mm": None,
                    "taught_by": "mat_measured", "vectors": [], "photo": None})
    body = client.get("/manage/inventory").json()
    assert body["items"][0]["price_paise"] is None
    assert body["items"][0]["price_rupees"] is None
    assert any("integer paise" in p["detail"] for p in body["catalogue_problems"])


def test_a_code_bound_to_a_sku_nobody_taught_is_named(client):
    _catalogue(real=_mat_sku("Real", 1000))
    (manage.store_dir() / "product_codes.json").write_text(json.dumps({
        "format": 1, "codes": {"111": "real", "222": "ghost"}}), encoding="utf-8")
    body = client.get("/manage/inventory").json()
    assert body["orphan_code_bindings"] == ["ghost"]


# ============================================================= opening stock

def test_no_stock_count_reads_as_not_counted_never_as_zero(client):
    """A zero is a claim. This system has never had a stock sensor, so the only
    honest answer for an uncounted shelf is null."""
    _catalogue(parle_g=_mat_sku("Parle-G", 1000))
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    body = client.get("/manage/inventory").json()
    row = body["items"][0]
    assert row["opening_stock_units"] is None
    assert row["remaining_units"] is None
    assert row["billed_since_count"] is None
    assert body["counted_skus"] == 0
    assert body["stock_tracking"] == "opening_count"


def test_a_recorded_count_persists_and_is_stamped_with_the_moment(client):
    _catalogue(parle_g=_mat_sku("Parle-G", 1000))
    r = client.post("/manage/inventory/parle_g/stock", json={"units": 40})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["units"] == 40
    assert body["settles_money"] is False
    assert body["counted_at"]

    on_disk = json.loads(manage.stock_path().read_text(encoding="utf-8"))
    assert on_disk["stock"]["parle_g"]["units"] == 40
    assert on_disk["stock"]["parle_g"]["counted_at"] == body["counted_at"]

    row = client.get("/manage/inventory").json()["items"][0]
    assert row["opening_stock_units"] == 40
    assert row["remaining_units"] == 40          # nothing billed since


def test_remaining_subtracts_only_what_was_billed_after_the_count(client):
    """Subtracting a year of history from this morning's shelf count would print
    a large negative number with complete confidence. Sales BEFORE the count are
    already reflected in the number the shopkeeper counted."""
    _catalogue(parle_g=_mat_sku("Parle-G", 1000))
    ledger = _ledger()
    _bill(ledger, "before", [("parle_g", 1000), ("parle_g", 1000)], at=0)
    client.post("/manage/inventory/parle_g/stock", json={"units": 10})
    # ...and a sale that lands after the count, stamped in the future so it is
    # unambiguously on the far side of it.
    _bill(Ledger(manage.ledger_path()), "after", [("parle_g", 1000)],
          at=400_000_000)
    manage._CHAIN_CACHE.clear()

    row = client.get("/manage/inventory").json()["items"][0]
    assert row["billed_count"] == 3            # all of them, for the sales column
    assert row["billed_since_count"] == 1      # but only one against the count
    assert row["remaining_units"] == 9


def test_zero_is_a_legitimate_count(client):
    """'The shelf is empty' is something a shopkeeper needs to be able to say."""
    _catalogue(parle_g=_mat_sku("Parle-G", 1000))
    assert client.post("/manage/inventory/parle_g/stock",
                       json={"units": 0}).status_code == 200
    row = client.get("/manage/inventory").json()["items"][0]
    assert row["opening_stock_units"] == 0
    assert row["remaining_units"] == 0


def test_a_stock_file_in_an_unknown_format_is_discarded_but_reported(client):
    """Silently dropping the shopkeeper's counts is how 'my numbers vanished'
    becomes unanswerable."""
    _catalogue(parle_g=_mat_sku("Parle-G", 1000))
    manage.stock_path().write_text(json.dumps({"format": 99, "stock": {}}),
                                   encoding="utf-8")
    body = client.get("/manage/inventory").json()
    assert body["ok"] is True
    assert "format" in (body["stock_problem"] or "")


# ================================================================== settings

def test_settings_reports_the_gates_the_catalogue_was_built_under(client):
    _catalogue(parle_g=_mat_sku("Parle-G", 1000))
    body = client.get("/manage/settings").json()
    assert body["recognition"]["phi"] == 0.9
    assert body["recognition"]["theta"] == 0.1
    assert body["recognition"]["tau_mm"] == 4.0
    assert body["recognition"]["source"] == "catalogue"
    assert body["catalogue"]["gates_from_disk"] is True


def test_settings_says_when_the_gates_are_only_library_defaults(client):
    """They are equal today. A reader who assumed that would be wrong the day
    somebody re-teaches the shop under a different phi."""
    body = client.get("/manage/settings").json()
    assert body["catalogue"]["gates_from_disk"] is False
    assert "defaults" in body["recognition"]["source"]
    assert body["recognition"]["phi"] == manage.DEFAULT_PHI


def test_settings_reports_the_mat_geometry_from_takhti(client):
    body = client.get("/manage/settings").json()
    assert body["mat"]["width_mm"] == 297.0
    assert body["mat"]["height_mm"] == 420.0
    assert body["mat"]["rectified_buffer_px"] == [840, 1188]
    assert body["mat"]["markers"] == 4
    assert body["mat"]["marker_centres_mm"][0] == [27.0, 27.0]


def test_settings_exposes_no_secret_value_prefix_or_length(client, monkeypatch):
    """The strong form: the secrets are fed in with recognisable values and the
    entire response is searched for any trace of them."""
    monkeypatch.setattr(manage, "paisa_get", lambda path: (200, {
        "ok": True, "mode": "live", "key_id": "rzp_live_ABCDEF123456",
        "key_secret_configured": True, "webhook_secret_configured": True,
        # Fields a future paisa might add. None of them may reach the page.
        "key_secret": "SUPERSECRETVALUE", "webhook_secret": "WHSECRETVALUE",
        "webhooks_seen": 3, "last_webhook_at": _ts(0),
    }))
    blob = json.dumps(client.get("/manage/settings").json())
    assert "SUPERSECRETVALUE" not in blob
    assert "WHSECRETVALUE" not in blob
    assert "ABCDEF123456" not in blob            # not even the key id's tail
    body = json.loads(blob)
    assert body["money"]["key_id_prefix"] == "rzp_live"
    assert body["money"]["key_secret_configured"] is True
    assert body["money"]["webhook_secret_configured"] is True


def test_key_id_prefix_keeps_the_mode_and_drops_the_account():
    assert manage.key_id_prefix("rzp_live_AbCdEf1234") == "rzp_live"
    assert manage.key_id_prefix("rzp_test_SIMULATED") == "rzp_test"
    assert manage.key_id_prefix("weird") == "weird"
    assert manage.key_id_prefix(None) is None
    assert manage.key_id_prefix("") is None


def test_a_counter_that_has_heard_nothing_says_SINCE_IT_STARTED_not_EVER():
    """THE CASE THIS BLOCK EXISTS FOR, and the limit of what it may claim.

    A counter whose webhook path is dead looks identical to one where nobody
    has paid yet: both show a link, both spin, neither turns green. So this
    block exists to tell them apart.

    But it may only speak for THIS PROCESS. `webhooks_seen` is a plain
    attribute on paisa — never persisted, never reloaded, back to zero on every
    restart — and the headline used to turn that into the word *ever*, a claim
    about all of history. An audit caught the same server answering
    `bills_settled: 1` on a chain it said verifies, in the same breath as "no
    webhook has ever reached this counter". A bill cannot settle without a
    signature-verified webhook, so both could not be true.

    That is the tunnel incident in FAILURES.md running BACKWARDS: a false alarm
    sending a shopkeeper to rebuild infrastructure that works. The chain is the
    record of what has ever happened; this field is the record of what has
    happened since the counter started, and the sentence must say the second."""
    live = manage.webhook_liveness({"webhooks_seen": 0, "last_webhook_at": None},
                                   reachable=True)
    # The tag is unchanged on purpose: two screens and a TypeScript union
    # consume it, and renaming it drops both through to their else branch.
    assert live["status"] == "never"
    assert "since it started" in live["headline"]
    assert "ever" not in live["headline"].split()
    assert "turn green" in live["headline"]
    assert live["silent_for_seconds"] is None      # nothing to measure from


def test_a_recent_webhook_is_live(client):
    recent = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    live = manage.webhook_liveness(
        {"webhooks_seen": 4, "last_webhook_at": recent}, reachable=True)
    assert live["status"] == "live"
    assert live["silent_for_seconds"] is not None
    assert live["silent_for_seconds"] < manage.WEBHOOK_SILENT_AFTER_S


def test_a_long_silence_after_a_working_webhook_is_amber_not_red(client):
    """It worked once, so this is either a quiet shop or a revoked tunnel. The
    page must not claim to know which."""
    old = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    live = manage.webhook_liveness(
        {"webhooks_seen": 4, "last_webhook_at": old}, reachable=True)
    assert live["status"] == "silent"
    assert live["silent_for_seconds"] > manage.WEBHOOK_SILENT_AFTER_S
    assert "6 hours ago" in live["headline"]


def test_an_unreachable_money_service_is_unknown_never_never(client):
    """Reporting 'no webhook has ever arrived' when the truth is 'we could not
    ask' would send the shopkeeper to re-point a dashboard that is fine."""
    live = manage.webhook_liveness({}, reachable=False)
    assert live["status"] == "unknown"
    assert "did not answer" in live["headline"]

    body = client.get("/manage/settings").json()          # the fixture's 503
    assert body["ok"] is True                             # the page still loads
    assert body["money"]["reachable"] is False
    assert body["money"]["mode"] is None
    assert body["money"]["key_id_prefix"] is None
    assert body["webhook"]["status"] == "unknown"
    assert body["ledger"]["chain_ok"] is True             # ...and still useful


def test_settings_reports_the_ledger_head(client):
    _bill(_ledger(), "s1", [("a", 100)])
    _, _, head, _ = verify(manage.ledger_path())
    body = client.get("/manage/settings").json()
    assert body["ledger"]["head"] == head
    assert body["ledger"]["bills_closed"] == 1
    assert body["ledger"]["bills_settled"] == 0


# ================================================================== refusals
#
# Every named refusal, by name. A refusal is a RESULT: 400 (or 404 for an id
# that does not exist), a machine-readable reason, and a sentence saying what to
# change. Never a 500 — a crash is the one answer that teaches nobody anything.

@pytest.mark.parametrize("query,expected", [
    ("limit=abc", manage.R_BAD_LIMIT),
    ("limit=0", manage.R_BAD_LIMIT),
    ("limit=-1", manage.R_BAD_LIMIT),
    ("limit=1000000", manage.R_BAD_LIMIT),
    ("limit=1.5", manage.R_BAD_LIMIT),
    ("since=yesterday", manage.R_BAD_SINCE),
    ("since=2026-13-45", manage.R_BAD_SINCE),
])
def test_history_query_refusals_are_named(client, query, expected):
    r = client.get(f"/manage/history?{query}")
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False
    assert body["reason"] == expected
    assert body["detail"]
    assert body["settles_money"] is False


def test_an_unknown_session_is_a_404_that_names_the_ledger(client):
    _bill(_ledger(), "s1", [("a", 100)])
    r = client.get("/manage/history/no_such_session")
    assert r.status_code == 404
    assert r.json()["reason"] == manage.R_UNKNOWN_SESSION
    assert str(manage.ledger_path()) in r.json()["detail"]


def test_stock_against_an_unknown_sku_is_a_404(client):
    _catalogue(real=_mat_sku("Real", 1000))
    r = client.post("/manage/inventory/ghost/stock", json={"units": 5})
    assert r.status_code == 404
    assert r.json()["reason"] == manage.R_UNKNOWN_SKU
    assert not manage.stock_path().exists()      # and nothing was written


@pytest.mark.parametrize("body,expected", [
    ({}, manage.R_STOCK_MISSING),
    ({"units": 1.5}, manage.R_STOCK_NOT_INTEGER),
    ({"units": "40"}, manage.R_STOCK_NOT_INTEGER),
    ({"units": True}, manage.R_STOCK_NOT_INTEGER),
    ({"units": None}, manage.R_STOCK_NOT_INTEGER),
    ({"units": -1}, manage.R_STOCK_NEGATIVE),
    ({"units": 1_000_001}, manage.R_STOCK_TOO_LARGE),
])
def test_stock_body_refusals_are_named(client, body, expected):
    _catalogue(parle_g=_mat_sku("Parle-G", 1000))
    r = client.post("/manage/inventory/parle_g/stock", json=body)
    assert r.status_code == 400
    assert r.json()["reason"] == expected
    assert r.json()["detail"]
    assert not manage.stock_path().exists()      # a refusal writes nothing


@pytest.mark.parametrize("raw", [b"", b"not json", b"[1,2,3]", b'"a string"'])
def test_a_body_that_is_not_a_json_object_is_refused_by_name(client, raw):
    _catalogue(parle_g=_mat_sku("Parle-G", 1000))
    r = client.post("/manage/inventory/parle_g/stock", content=raw,
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.json()["reason"] == manage.R_BAD_BODY


def test_a_count_that_cannot_be_written_refuses_rather_than_reporting_success(
    client, monkeypatch
):
    """The page must never show a number that is not on disk."""
    _catalogue(parle_g=_mat_sku("Parle-G", 1000))

    def _boom(_stock):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(manage, "write_opening_stock", _boom)
    r = client.post("/manage/inventory/parle_g/stock", json={"units": 5})
    assert r.status_code == 400
    assert r.json()["reason"] == manage.R_STOCK_NOT_WRITTEN
    assert "Nothing was recorded" in r.json()["detail"]


def test_an_unexpected_failure_is_a_400_and_never_a_500(client, monkeypatch):
    def _boom():
        raise RuntimeError("the disk fell off")

    monkeypatch.setattr(manage, "read_chain", _boom)
    for path in ("/manage/history", "/manage/history/x", "/manage/inventory",
                 "/manage/settings"):
        r = client.get(path)
        assert r.status_code == 400, path
        assert r.json()["reason"] == manage.R_INTERNAL
        assert "RuntimeError" in r.json()["detail"]


def test_no_endpoint_ever_claims_to_settle_money(client):
    _catalogue(parle_g=_mat_sku("Parle-G", 1000))
    _bill(_ledger(), "s1", [("parle_g", 1000)])
    for path in ("/manage/history", "/manage/history/s1", "/manage/inventory",
                 "/manage/settings"):
        assert client.get(path).json()["settles_money"] is False


# ============================================================== the invariants

def test_every_reported_amount_is_an_integer_number_of_paise(client):
    """INVARIANT 1, checked on the wire rather than in the source. A float that
    reached a browser would be a money bug no matter which module produced it."""
    _catalogue(parle_g=_mat_sku("Parle-G", 1000))
    _bill(_ledger(), "s1", [("parle_g", 1000)], amber=["ghost"], mint=True)

    def _walk(node, where=""):
        if isinstance(node, dict):
            for k, v in node.items():
                if k.endswith("_paise") and v is not None:
                    assert isinstance(v, int) and not isinstance(v, bool), \
                        f"{where}.{k} is {type(v).__name__}, not integer paise"
                _walk(v, f"{where}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, f"{where}[{i}]")

    for path in ("/manage/history", "/manage/history/s1", "/manage/inventory"):
        _walk(client.get(path).json(), path)


def test_the_router_carries_its_own_prefix_and_must_not_be_given_another():
    """The orchestrator mounts this bare. Mounting it with prefix='/manage'
    would produce /manage/manage/history, which is a 404 nobody can explain."""
    assert manage.router.prefix == "/manage"
    paths = {r.path for r in manage.router.routes}
    assert paths == {
        "/manage/history",
        "/manage/history/{session_id}",
        "/manage/inventory",
        "/manage/inventory/{sku_id}/stock",
        "/manage/settings",
        # The day brief. tests/test_manage_today.py owns its behaviour; this
        # set is only the census of what is mounted.
        "/manage/today",
    }


def test_the_env_overrides_are_honoured_so_tests_never_touch_results(monkeypatch,
                                                                     tmp_path):
    """A test harness once destroyed the live catalogue by ignoring
    GAWAAH_SHOP_DIR. Both paths are resolved per call, never memoised at import,
    because a constant captured at import time ignores a fixture silently."""
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "a"))
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "b"))
    assert manage.data_dir() == tmp_path / "a"
    assert manage.store_dir() == tmp_path / "b"
    assert manage.ledger_path() == tmp_path / "a" / "audit.jsonl"
    assert manage.stock_path() == tmp_path / "b" / "opening_stock.json"

    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "c"))
    assert manage.store_dir() == tmp_path / "c"      # changed, not cached


def test_ago_is_plain_english_and_integer_only():
    assert manage._ago(5) == "5 seconds"
    assert manage._ago(300) == "5 minutes"
    assert manage._ago(7200) == "2 hours"
    assert manage._ago(86400 * 3) == "3 days"
    # A clock that disagrees with the money service is worth saying out loud
    # rather than rendering as '0 seconds'.
    assert "clocks disagree" in manage._ago(-30)


def test_event_shapes_match_the_shipped_ledger():
    """The fixtures above copy their event shapes from results/audit.jsonl. If
    session.py renames a reason, the fixtures keep passing while the product
    breaks — so the strings are pinned here, against the real file, once.

    Skipped rather than failed when the shipped log is absent: a fresh clone is
    not a regression.
    """
    shipped = Path(__file__).resolve().parent.parent / "results" / "audit.jsonl"
    if not shipped.exists():
        pytest.skip("no shipped ledger in this checkout")
    reasons, events = set(), set()
    for line in shipped.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            reasons.add(rec.get("reason"))
            events.add((rec.get("module"), rec.get("event")))
    assert manage.REASON_COMMITTED in reasons
    assert manage.REASON_AMBER in reasons
    assert manage.REASON_GREEN in reasons
    assert ("session", manage.EV_DONE) in events
    assert ("session", manage.EV_EXIT) in events
    assert ("paisa", manage.EV_MINTED) in events
    assert ("kernel", manage.EV_SETTLED) in events
