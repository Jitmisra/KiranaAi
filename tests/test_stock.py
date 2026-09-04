"""gawaah/stock.py — stock that moves, on top of the count somebody typed.

This module exists to make a shelf figure BELIEVABLE, so the suite is organised
around the ways it could stop being one:

  1. It could invent a figure          -> the never-counted and no-history tests
  2. It could keep a second sales      -> the "manage owns billed-since" tests
     count that drifts from manage.py
  3. It could lose a movement          -> the chain tests: every write is
                                          verifiable, a failed append refuses
  4. It could accept a number nobody   -> the refusal tests, one per named
     typed                                refusal, all asserting nothing moved

Every fixture writes a REAL hash chain with gawaah.ledger.Ledger and a real
catalogue sidecar, so the file the code reads is the file the product writes.
Nothing here asserts against a hand-typed hash, and nothing here monkeypatches
the derivation it is testing.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
import pathlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import manage, stock  # noqa: E402
from gawaah.ledger import Ledger, verify  # noqa: E402


# ------------------------------------------------------------------ fixtures

@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Nothing in this suite may see, let alone write, results/.

    A harness once destroyed the live catalogue by ignoring GAWAAH_SHOP_DIR, so
    both overrides are set for EVERY test whether it uses them or not, and
    manage's chain cache is dropped so one test's ledger can never answer
    another test's request.
    """
    data = tmp_path / "data"
    shop = data / "shop"
    shop.mkdir(parents=True)
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(data))
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(shop))
    manage._CHAIN_CACHE.clear()
    yield
    manage._CHAIN_CACHE.clear()


@pytest.fixture
def client() -> TestClient:
    """The router mounted the way the orchestrator will mount it: bare."""
    app = FastAPI()
    app.include_router(stock.router)
    return TestClient(app)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ago(**kw) -> str:
    return (_now() - timedelta(**kw)).isoformat()


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


def _bill(ledger: Ledger, session_id: str, lines, *, at: str,
          close: bool = True) -> None:
    """One closed bill written the way session.py writes it.

    The event names and reason strings are the ones manage.py folds on. If they
    ever change, THIS fixture is what stops the suite passing against a product
    that has quietly stopped counting sales — the shapes are pinned in
    test_the_bill_shape_this_suite_writes_is_the_one_manage_reads.
    """
    running = 0
    for i, (sku_id, price) in enumerate(lines):
        running += price
        ledger.append(ts=at, module="session", event="exit",
                      session_id=session_id, reason="exit_crossing_committed",
                      item_id=f"{sku_id}#{i}", price_paise=price,
                      abstained=False, excluded_from_total=False,
                      **{"from": "PRICED", "to": "BASKET_OPEN"},
                      total_paise=running)
    if close:
        ledger.append(ts=at, module="session", event="done",
                      session_id=session_id, reason="intent_requested",
                      lines=len(lines), amber_excluded=0,
                      **{"from": "BASKET_OPEN", "to": "AWAITING_SETTLEMENT"},
                      total_paise=running)


def _count_at(sku_id: str, units: int, when: str) -> None:
    """A baseline with a chosen timestamp, through manage's own writer.

    The endpoint stamps `now`, which is right for a shopkeeper and useless for a
    test that needs a count from six weeks ago. This is the same file the
    endpoint writes, written by the same function.
    """
    stock_map, _ = manage.read_opening_stock()
    stock_map[sku_id] = {"units": units, "counted_at": when}
    manage.write_opening_stock(stock_map)


def _row(client: TestClient, sku_id: str) -> dict:
    body = client.get("/stock").json()
    return next(r for r in body["items"] if r["sku_id"] == sku_id)


# ========================================================== the empty counter

def test_a_shop_with_nothing_taught_lists_nothing_and_does_not_crash(client):
    body = client.get("/stock").json()
    assert body["ok"] is True
    assert body["count"] == 0
    assert body["items"] == []
    assert body["chain"]["exists"] is False


def test_a_product_that_was_never_counted_has_no_figure_and_not_a_zero(client):
    """A zero is a claim about a shelf. An absence is the truth about a shelf
    nobody has looked at."""
    _catalogue(parle_g=_sku("Parle-G"))
    row = _row(client, "parle_g")
    assert row["on_hand_units"] is None
    assert row["basis"] == "never_counted"
    assert row["counted_units"] is None
    assert "would be a claim" in row["derivation"]


def test_a_movement_against_a_never_counted_product_is_still_recorded(client):
    """The delivery happened whether or not anybody has counted the shelf. It
    goes on the log; the figure stays absent until there is a baseline."""
    _catalogue(parle_g=_sku("Parle-G"))
    r = client.post("/stock/parle_g/in", json={"units": 12, "reason": "delivery"})
    assert r.status_code == 200
    assert r.json()["on_hand_units"] is None
    assert _row(client, "parle_g")["units_in_since_count"] == 12


# =============================================================== the movements

def test_stock_in_adds_to_the_count(client):
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/count", json={"units": 40})
    r = client.post("/stock/parle_g/in", json={"units": 24, "reason": "delivery"})
    assert r.status_code == 200
    assert r.json()["units"] == 24
    assert r.json()["on_hand_units"] == 64


def test_stock_out_subtracts_from_the_count(client):
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/count", json={"units": 40})
    r = client.post("/stock/parle_g/out",
                    json={"units": 3, "reason": "breakage"})
    assert r.status_code == 200
    # The SIGN belongs to the route, not to the page: the browser sent 3.
    assert r.json()["units"] == -3
    assert r.json()["on_hand_units"] == 37


def test_the_sign_is_the_servers_and_a_page_cannot_send_one(client):
    """INVARIANT 8 on stock: the page says what happened, the server derives
    what that means. A negative posted to /in would be a page authoring the
    direction of a movement."""
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/count", json={"units": 40})
    r = client.post("/stock/parle_g/in", json={"units": -5, "reason": "delivery"})
    assert r.status_code == 400
    assert r.json()["reason"] == stock.R_UNITS_NOT_POSITIVE
    assert "stock-out" in r.json()["detail"]
    assert _row(client, "parle_g")["on_hand_units"] == 40


def test_several_movements_accumulate(client):
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/count", json={"units": 10})
    client.post("/stock/parle_g/in", json={"units": 24, "reason": "delivery"})
    client.post("/stock/parle_g/out", json={"units": 2, "reason": "breakage"})
    client.post("/stock/parle_g/out", json={"units": 1, "reason": "personal_use"})
    row = _row(client, "parle_g")
    assert row["units_in_since_count"] == 24
    assert row["units_out_since_count"] == 3
    assert row["movement_delta_units"] == 21
    assert row["on_hand_units"] == 31
    assert row["movements_since_count"] == 3


def test_a_note_is_kept_with_the_movement(client):
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/out",
                json={"units": 2, "reason": "breakage",
                      "note": "  crate   dropped at the door  "})
    mv = client.get("/stock/movements").json()["movements"][0]
    assert mv["note"] == "crate dropped at the door"
    assert mv["reason_label"] == "broken or spoiled"


def test_a_mistake_is_corrected_with_an_opposite_movement_not_an_edit(client):
    """There is no route that edits or deletes a movement, on purpose. The log
    reads as what happened, not as what somebody wishes had happened."""
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/count", json={"units": 40})
    client.post("/stock/parle_g/out", json={"units": 20, "reason": "breakage"})
    client.post("/stock/parle_g/in", json={"units": 20, "reason": "correction",
                                           "note": "typed 20, meant 2"})
    row = _row(client, "parle_g")
    assert row["on_hand_units"] == 40
    assert row["movements_since_count"] == 2      # both lines are still there
    assert len(client.get("/stock/movements").json()["movements"]) == 2


def test_movements_are_listed_newest_first(client):
    _catalogue(a=_sku("A"), b=_sku("B"))
    client.post("/stock/a/in", json={"units": 1, "reason": "delivery"})
    client.post("/stock/b/in", json={"units": 2, "reason": "delivery"})
    client.post("/stock/a/out", json={"units": 3, "reason": "expiry"})
    rows = client.get("/stock/movements").json()["movements"]
    assert [(m["sku_id"], m["units"]) for m in rows] == [
        ("a", -3), ("b", 2), ("a", 1)]


def test_movements_can_be_narrowed_to_one_product(client):
    _catalogue(a=_sku("A"), b=_sku("B"))
    client.post("/stock/a/in", json={"units": 1, "reason": "delivery"})
    client.post("/stock/b/in", json={"units": 2, "reason": "delivery"})
    body = client.get("/stock/movements?sku=b").json()
    assert body["matched"] == 1
    assert body["movements"][0]["sku_id"] == "b"


def test_the_movement_list_can_be_shortened_and_says_what_it_left_out(client):
    _catalogue(a=_sku("A"))
    for _ in range(5):
        client.post("/stock/a/in", json={"units": 1, "reason": "delivery"})
    body = client.get("/stock/movements?limit=2").json()
    assert body["count"] == 2 and body["matched"] == 5
    assert len(body["movements"]) == 2


# ==================================================== the log is the only store

def test_every_movement_lands_on_a_chain_that_verifies(client):
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/count", json={"units": 40})
    client.post("/stock/parle_g/in", json={"units": 24, "reason": "delivery"})
    client.post("/stock/parle_g/out", json={"units": 2, "reason": "expiry"})
    client.post("/stock/parle_g/reorder", json={"units": 10})
    ok, lines, _head, error = verify(stock.audit_path())
    assert ok is True and error is None
    assert lines == 4


def test_the_chain_is_this_modules_own_and_not_the_money_ledger(client):
    """results/audit.jsonl has one writer holding it open in another process.
    A second appender breaks `make verify-ledger` on the one log that must be
    beyond argument."""
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/in", json={"units": 1, "reason": "delivery"})
    assert stock.audit_path() == manage.store_dir() / "stock.audit.jsonl"
    assert stock.audit_path().exists()
    assert not manage.ledger_path().exists()


def test_a_movement_that_cannot_be_appended_is_a_refusal_not_a_warning(
        client, monkeypatch):
    """The chain IS the store here. An unappended movement did not happen, and
    reporting it as recorded would put a figure on the page with no file behind
    it."""
    _catalogue(parle_g=_sku("Parle-G"))

    class _Broken:
        def __init__(self, path):
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(stock, "Ledger", _Broken)
    r = client.post("/stock/parle_g/in", json={"units": 5, "reason": "delivery"})
    assert r.status_code == 400
    assert r.json()["reason"] == stock.R_NOT_RECORDED
    assert "NOTHING WAS RECORDED" in r.json()["detail"]


def test_a_tampered_chain_is_truncated_and_said_so_out_loud(client):
    """Serving the verified prefix and naming the break beats serving nothing:
    the shopkeeper keeps yesterday's movements and is told the file was edited.
    """
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/in", json={"units": 10, "reason": "delivery"})
    client.post("/stock/parle_g/in", json={"units": 90, "reason": "delivery"})

    lines = stock.audit_path().read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[1])
    rec["units"] = 900                       # a delivery nobody took
    lines[1] = json.dumps(rec, sort_keys=True, ensure_ascii=False)
    stock.audit_path().write_text("\n".join(lines) + "\n", encoding="utf-8")

    body = client.get("/stock/movements").json()
    assert body["chain"]["ok"] is False
    assert "line 2" in body["chain"]["error"]
    assert body["matched"] == 1                       # only the verified prefix
    assert body["movements"][0]["units"] == 10
    assert 900 not in [m["units"] for m in body["movements"]]


def test_a_line_whose_direction_and_sign_disagree_is_not_counted(client):
    """`stock.in` with a negative unit count cannot be written by this module,
    so a line like it has been hand-edited. Guessing which half to believe is
    how a figure becomes fiction."""
    _catalogue(parle_g=_sku("Parle-G"))
    Ledger(stock.audit_path()).append(
        ts=_now().isoformat(), module="stock", event="stock.in",
        movement_id="mv_forged", sku_id="parle_g", units=-5,
        reason="delivery", note=None, name="Parle-G")
    body = client.get("/stock/movements").json()
    assert body["matched"] == 0
    assert body["unreadable_movement_lines"] == 1
    assert body["chain"]["ok"] is True           # the CHAIN is fine; the line is not


# ================================================================= the re-count

def test_a_recount_resets_the_baseline_and_supersedes_what_came_before(client):
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/count", json={"units": 40})
    client.post("/stock/parle_g/in", json={"units": 24, "reason": "delivery"})
    assert _row(client, "parle_g")["on_hand_units"] == 64

    r = client.post("/stock/parle_g/count", json={"units": 50})
    assert r.status_code == 200
    row = _row(client, "parle_g")
    assert row["on_hand_units"] == 50
    assert row["movements_since_count"] == 0
    assert row["movements_superseded_by_count"] == 1
    # superseded, not deleted: the delivery is still readable
    assert client.get("/stock/movements").json()["matched"] == 1


def test_the_recount_says_what_the_counter_expected_and_names_the_gap(client):
    """This is the whole point of counting a shelf a computer is tracking."""
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/count", json={"units": 40})
    client.post("/stock/parle_g/out", json={"units": 5, "reason": "breakage"})

    r = client.post("/stock/parle_g/count", json={"units": 32}).json()
    assert r["expected_units"] == 35
    assert r["discrepancy_units"] == -3
    assert "3 fewer than it can account for" in r["detail"]
    assert r["counted_units"] == 32


def test_a_count_that_matches_says_nothing_has_gone_missing(client):
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/count", json={"units": 40})
    client.post("/stock/parle_g/in", json={"units": 10, "reason": "delivery"})
    r = client.post("/stock/parle_g/count", json={"units": 50}).json()
    assert r["discrepancy_units"] == 0
    assert "exactly what the counter expected" in r["detail"]


def test_a_surplus_is_reported_as_plainly_as_a_shortfall(client):
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/count", json={"units": 10})
    r = client.post("/stock/parle_g/count", json={"units": 14}).json()
    assert r["discrepancy_units"] == 4
    assert "never booked in" in r["detail"]


def test_the_recount_writes_the_baseline_manage_reads(client):
    """One baseline in this program, not two. Counting on this screen moves the
    figure on the inventory screen because it is the same file."""
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/count", json={"units": 40})
    counts, err = manage.read_opening_stock()
    assert err is None
    assert counts["parle_g"]["units"] == 40
    assert manage.stock_path().exists()


def test_a_count_that_cannot_be_written_refuses_rather_than_reporting_success(
        client, monkeypatch):
    _catalogue(parle_g=_sku("Parle-G"))

    def _boom(_stock):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(manage, "write_opening_stock", _boom)
    r = client.post("/stock/parle_g/count", json={"units": 5})
    assert r.status_code == 400
    assert r.json()["reason"] == stock.R_COUNT_NOT_WRITTEN
    assert "Nothing was recorded" in r.json()["detail"]


def test_a_count_whose_audit_line_fails_still_stands_and_says_so(
        client, monkeypatch):
    """The baseline is on disk by then. Telling the shopkeeper his count was
    lost when it was not would send him to count the shelf again for nothing."""
    _catalogue(parle_g=_sku("Parle-G"))

    class _Broken:
        def __init__(self, path):
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(stock, "Ledger", _Broken)
    r = client.post("/stock/parle_g/count", json={"units": 5})
    assert r.status_code == 200
    assert r.json()["audited"] is False
    assert "could not be appended" in r.json()["audit_error"]
    assert manage.read_opening_stock()[0]["parle_g"]["units"] == 5


# ================================ what the counter billed is manage.py's answer

def test_what_the_counter_billed_since_the_count_comes_off_the_shelf(client):
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/count", json={"units": 40})
    client.post("/stock/parle_g/in", json={"units": 10, "reason": "delivery"})
    _bill(Ledger(manage.ledger_path()), "s1",
          [("parle_g", 1000), ("parle_g", 1000)],
          at=(_now() + timedelta(seconds=5)).isoformat())
    manage._CHAIN_CACHE.clear()

    row = _row(client, "parle_g")
    assert row["billed_since_count"] == 2
    assert row["on_hand_units"] == 48        # 40 counted + 10 in - 2 billed
    assert "- 2 billed" in row["derivation"]


def test_bills_from_before_the_count_are_not_subtracted_from_it(client):
    """Subtracting a year of history from this morning's shelf count would print
    a large negative number with complete confidence. manage.py already refuses
    to; this module inherits that and must not undo it."""
    _catalogue(parle_g=_sku("Parle-G"))
    _bill(Ledger(manage.ledger_path()), "old", [("parle_g", 1000)] * 6,
          at=_ago(days=9))
    manage._CHAIN_CACHE.clear()
    client.post("/stock/parle_g/count", json={"units": 40})

    row = _row(client, "parle_g")
    assert row["billed_since_count"] == 0
    assert row["on_hand_units"] == 40


def test_the_billed_figure_is_manage_pys_own_number_and_not_a_second_count(
        client):
    """If this module ever grew its own sales counter, the two screens would
    disagree the first time either changed. They are asserted equal here against
    manage's derivation directly, not against a hand-typed expectation."""
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/count", json={"units": 40})
    _bill(Ledger(manage.ledger_path()), "s1", [("parle_g", 1000)] * 3,
          at=(_now() + timedelta(seconds=5)).isoformat())
    manage._CHAIN_CACHE.clear()

    mine = _row(client, "parle_g")
    theirs = next(r for r in manage._inventory_rows()["items"]
                  if r["sku_id"] == "parle_g")
    assert mine["billed_since_count"] == theirs["billed_since_count"] == 3
    assert mine["remaining_after_billing"] == theirs["remaining_units"] == 37


def test_the_bill_shape_this_suite_writes_is_the_one_manage_reads(client):
    """A harness bug looks exactly like a system bug. If session.py renames its
    exit reason, THIS is the test that goes red rather than the suite quietly
    passing against a product that has stopped counting sales."""
    _catalogue(parle_g=_sku("Parle-G"))
    _bill(Ledger(manage.ledger_path()), "s1", [("parle_g", 1000)],
          at=_ago(days=1))
    manage._CHAIN_CACHE.clear()
    records, chain = manage.read_chain()
    assert chain["ok"] is True
    bills = manage.bills_from(records)
    assert bills["s1"]["closed"] is True
    assert [ln["sku_id"] for ln in bills["s1"]["line_items"]] == ["parle_g"]


def test_selling_is_not_a_reason_stock_can_leave_here(client):
    """A sale leaves through the counter, which writes it to the audit chain,
    which manage.py already subtracts. Recording it here as well would take the
    same packet off the shelf twice."""
    _catalogue(parle_g=_sku("Parle-G"))
    assert "sold" not in stock.OUT_REASONS and "sale" not in stock.OUT_REASONS
    r = client.post("/stock/parle_g/out", json={"units": 1, "reason": "sold"})
    assert r.status_code == 400
    assert r.json()["reason"] == stock.R_REASON_UNKNOWN


# ========================================================== the reorder level

def test_a_level_can_be_set_and_cleared(client):
    _catalogue(parle_g=_sku("Parle-G"))
    r = client.post("/stock/parle_g/reorder", json={"units": 12})
    assert r.status_code == 200 and r.json()["reorder_level"] == 12
    assert _row(client, "parle_g")["reorder_level"] == 12

    r = client.post("/stock/parle_g/reorder", json={"units": None})
    assert r.status_code == 200 and r.json()["cleared"] is True
    assert _row(client, "parle_g")["reorder_level"] is None


def test_the_last_level_written_is_the_one_in_force(client):
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/reorder", json={"units": 12})
    client.post("/stock/parle_g/reorder", json={"units": 30})
    assert _row(client, "parle_g")["reorder_level"] == 30


def test_low_lists_what_is_at_or_under_the_level_and_nothing_above_it(client):
    _catalogue(low=_sku("Low"), fine=_sku("Fine"), exact=_sku("Exact"))
    for sku_id, counted, level in (("low", 3, 10), ("fine", 40, 10),
                                   ("exact", 10, 10)):
        client.post(f"/stock/{sku_id}/count", json={"units": counted})
        client.post(f"/stock/{sku_id}/reorder", json={"units": level})

    body = client.get("/stock/low").json()
    assert body["ok"] is True
    # AT the level counts as low: "reorder at ten" means ten is the moment.
    assert [r["sku_id"] for r in body["low"]] == ["low", "exact"]
    assert body["count"] == 2
    assert body["skus_with_a_level"] == 3


def test_low_puts_the_worst_shortfall_first(client):
    _catalogue(a=_sku("A"), b=_sku("B"))
    client.post("/stock/a/count", json={"units": 9})
    client.post("/stock/a/reorder", json={"units": 10})     # short by 1
    client.post("/stock/b/count", json={"units": 1})
    client.post("/stock/b/reorder", json={"units": 10})     # short by 9
    body = client.get("/stock/low").json()
    assert [r["sku_id"] for r in body["low"]] == ["b", "a"]


def test_a_level_on_a_shelf_nobody_counted_is_unknown_and_not_low(client):
    """Listing it as low would be a claim about a shelf nobody has looked at.
    Dropping it silently would hide a product the shopkeeper asked to watch."""
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/reorder", json={"units": 10})
    body = client.get("/stock/low").json()
    assert body["low"] == []
    assert [u["sku_id"] for u in body["unknown"]] == ["parle_g"]
    assert "never been counted" in body["unknown"][0]["why"]


def test_a_figure_below_zero_asks_for_a_recount_rather_than_pretending(client):
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/count", json={"units": 2})
    client.post("/stock/parle_g/out", json={"units": 5, "reason": "theft"})
    row = _row(client, "parle_g")
    assert row["on_hand_units"] == -3
    assert row["needs_recount"] is True
    body = client.get("/stock/low").json()
    assert [r["sku_id"] for r in body["needs_recount"]] == ["parle_g"]


def test_low_is_a_report_and_not_the_name_of_a_product(client):
    """FastAPI matches in declaration order. If /stock/{sku_id} were declared
    first, the low-stock report would be a 404 for a product called 'low' and
    the only symptom would be an empty screen."""
    _catalogue(parle_g=_sku("Parle-G"))
    r = client.get("/stock/low")
    assert r.status_code == 200
    assert "low" in r.json() and "unknown" in r.json()


def test_movements_is_a_report_and_not_the_name_of_a_product(client):
    _catalogue(parle_g=_sku("Parle-G"))
    r = client.get("/stock/movements")
    assert r.status_code == 200
    assert "movements" in r.json()


# ============================================================== days of cover

def test_cover_is_absent_and_says_why_when_nothing_has_been_billed(client):
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/count", json={"units": 40})
    row = _row(client, "parle_g")
    assert row["days_of_cover"] is None
    assert "billed none of this" in row["cover"]["why"]
    assert "will last forever" in row["cover"]["why"]


def test_cover_is_absent_when_nothing_has_been_counted(client):
    _catalogue(parle_g=_sku("Parle-G"))
    _bill(Ledger(manage.ledger_path()), "s1", [("parle_g", 1000)] * 8,
          at=_ago(days=5))
    manage._CHAIN_CACHE.clear()
    row = _row(client, "parle_g")
    assert row["days_of_cover"] is None
    assert "Nothing has been counted" in row["cover"]["why"]


def test_cover_refuses_a_rate_from_too_few_packets(client):
    _catalogue(parle_g=_sku("Parle-G"))
    _count_at("parle_g", 100, _ago(days=20))
    ledger = Ledger(manage.ledger_path())
    _bill(ledger, "s1", [("parle_g", 1000)], at=_ago(days=10))
    _bill(ledger, "s2", [("parle_g", 1000)], at=_ago(days=4))
    manage._CHAIN_CACHE.clear()

    row = _row(client, "parle_g")
    assert row["on_hand_units"] == 98
    assert row["days_of_cover"] is None
    assert "too little to call a rate" in row["cover"]["why"]
    assert row["cover"]["units_billed"] == 2


def test_cover_refuses_a_rate_from_a_single_days_trade(client):
    """Four packets in one afternoon is not four packets a day."""
    _catalogue(parle_g=_sku("Parle-G"))
    _count_at("parle_g", 100, _ago(days=20))
    _bill(Ledger(manage.ledger_path()), "s1", [("parle_g", 1000)] * 4,
          at=_ago(hours=2))
    manage._CHAIN_CACHE.clear()

    row = _row(client, "parle_g")
    assert row["days_of_cover"] is None
    assert "one day's trade is a guess" in row["cover"]["why"]


def test_cover_is_derived_once_there_is_enough_history(client):
    _catalogue(parle_g=_sku("Parle-G"))
    _count_at("parle_g", 100, _ago(days=25))
    ledger = Ledger(manage.ledger_path())
    for day in (20, 15, 10, 5, 2, 1):
        _bill(ledger, f"s{day}", [("parle_g", 1000)], at=_ago(days=day))
    manage._CHAIN_CACHE.clear()

    row = _row(client, "parle_g")
    assert row["on_hand_units"] == 94                 # 100 counted - 6 billed
    assert row["cover"]["units_billed"] == 6
    assert row["cover"]["over_days"] == 20
    # 94 on hand, 6 billed over 20 days -> 94 * 20 // 6, floored, no float.
    assert row["days_of_cover"] == 313
    assert row["cover"]["rate_text"] == "6 billed in 20 days"


def test_cover_ignores_trade_older_than_the_window(client):
    """Last season's Diwali rush must not set today's reorder advice."""
    _catalogue(parle_g=_sku("Parle-G"))
    _count_at("parle_g", 100, _ago(days=60))
    _bill(Ledger(manage.ledger_path()), "old", [("parle_g", 1000)] * 20,
          at=_ago(days=45))
    manage._CHAIN_CACHE.clear()

    row = _row(client, "parle_g")
    assert row["billed_since_count"] == 20            # still off the shelf
    assert row["on_hand_units"] == 80
    assert row["days_of_cover"] is None               # but not a rate
    assert f"last {stock.RATE_WINDOW_DAYS} days" in row["cover"]["why"]


def test_an_empty_shelf_covers_no_days_at_all(client):
    _catalogue(parle_g=_sku("Parle-G"))
    _count_at("parle_g", 0, _ago(days=25))
    ledger = Ledger(manage.ledger_path())
    for day in (20, 15, 10):
        _bill(ledger, f"s{day}", [("parle_g", 1000)], at=_ago(days=day))
    manage._CHAIN_CACHE.clear()

    row = _row(client, "parle_g")
    assert row["on_hand_units"] == -3        # billed after a zero count
    assert row["days_of_cover"] is None
    assert "below zero" in row["cover"]["why"]


# ================================================================== refusals

@pytest.mark.parametrize("path", ["in", "out"])
@pytest.mark.parametrize("body,expected", [
    ({"reason": "correction"}, "stock_units_missing"),
    ({"units": 2.5, "reason": "correction"}, "stock_units_fractional"),
    ({"units": 2.0, "reason": "correction"}, "stock_units_not_a_whole_number"),
    ({"units": "4", "reason": "correction"}, "stock_units_not_a_whole_number"),
    ({"units": True, "reason": "correction"}, "stock_units_not_a_whole_number"),
    ({"units": None, "reason": "correction"}, "stock_units_not_a_whole_number"),
    ({"units": 0, "reason": "correction"}, "stock_units_not_positive"),
    ({"units": -4, "reason": "correction"}, "stock_units_not_positive"),
    ({"units": 100_001, "reason": "correction"}, "stock_units_implausible"),
])
def test_movement_unit_refusals_are_named_and_write_nothing(
        client, path, body, expected):
    _catalogue(parle_g=_sku("Parle-G"))
    r = client.post(f"/stock/parle_g/{path}", json=body)
    assert r.status_code == 400
    assert r.json()["reason"] == expected
    assert r.json()["detail"]
    assert not stock.audit_path().exists()


def test_a_fractional_movement_is_refused_by_name_and_names_the_number(client):
    """Half a packet is not something a shelf holds. Rounding it would store a
    number nobody typed."""
    _catalogue(parle_g=_sku("Parle-G"))
    r = client.post("/stock/parle_g/out", json={"units": 1.5, "reason": "breakage"})
    assert r.status_code == 400
    assert r.json()["reason"] == stock.R_UNITS_FRACTIONAL
    assert "units=1.5" in r.json()["detail"]
    assert "Half a packet" in r.json()["detail"]


@pytest.mark.parametrize("raw", [b'{"units": 1e999, "reason": "delivery"}',
                                 b'{"units": NaN, "reason": "delivery"}'])
def test_a_number_that_is_not_finite_is_named_and_not_an_overflow(client, raw):
    """JSON has no infinity; Python's parser accepts `Infinity` and `NaN` and
    int() of either raises. A typed number must come back with a name on it,
    not with an OverflowError from the generic handler."""
    _catalogue(parle_g=_sku("Parle-G"))
    r = client.post("/stock/parle_g/in", content=raw,
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.json()["reason"] == stock.R_UNITS_NOT_INTEGER
    assert not stock.audit_path().exists()


@pytest.mark.parametrize("body,expected", [
    ({"units": 4}, "stock_reason_missing"),
    ({"units": 4, "reason": "   "}, "stock_reason_missing"),
    ({"units": 4, "reason": 7}, "stock_reason_not_text"),
    ({"units": 4, "reason": "because"}, "stock_reason_unknown"),
])
def test_movement_reason_refusals_are_named(client, body, expected):
    _catalogue(parle_g=_sku("Parle-G"))
    r = client.post("/stock/parle_g/in", json=body)
    assert r.status_code == 400
    assert r.json()["reason"] == expected
    assert not stock.audit_path().exists()


def test_a_reason_posted_to_the_wrong_direction_says_which_way_it_belongs(
        client):
    """'breakage' on the stock-in endpoint is the right word and the wrong
    button. Calling the word unknown would send him looking for another one."""
    _catalogue(parle_g=_sku("Parle-G"))
    r = client.post("/stock/parle_g/in", json={"units": 2, "reason": "breakage"})
    assert r.status_code == 400
    assert r.json()["reason"] == stock.R_REASON_WRONG_WAY
    assert "stock-out endpoint" in r.json()["detail"]

    r = client.post("/stock/parle_g/out", json={"units": 2, "reason": "delivery"})
    assert r.json()["reason"] == stock.R_REASON_WRONG_WAY
    assert "stock-in endpoint" in r.json()["detail"]


@pytest.mark.parametrize("note,expected", [
    (7, "stock_note_not_text"),
    (["a"], "stock_note_not_text"),
    ("x" * 201, "stock_note_too_long"),
])
def test_note_refusals_are_named(client, note, expected):
    _catalogue(parle_g=_sku("Parle-G"))
    r = client.post("/stock/parle_g/in",
                    json={"units": 2, "reason": "delivery", "note": note})
    assert r.status_code == 400
    assert r.json()["reason"] == expected


@pytest.mark.parametrize("raw", [b"", b"not json", b"[1,2,3]", b'"a string"'])
def test_a_body_that_is_not_a_json_object_is_refused_by_name(client, raw):
    _catalogue(parle_g=_sku("Parle-G"))
    for path in ("in", "out", "count", "reorder"):
        r = client.post(f"/stock/parle_g/{path}", content=raw,
                        headers={"Content-Type": "application/json"})
        assert r.status_code == 400, path
        assert r.json()["reason"] == stock.R_BAD_BODY, path


@pytest.mark.parametrize("path,body", [
    ("in", {"units": 2, "reason": "delivery"}),
    ("out", {"units": 2, "reason": "breakage"}),
    ("count", {"units": 2}),
    ("reorder", {"units": 2}),
])
def test_a_movement_against_an_unknown_sku_is_a_404(client, path, body):
    _catalogue(real=_sku("Real"))
    r = client.post(f"/stock/ghost/{path}", json=body)
    assert r.status_code == 404
    assert r.json()["reason"] == stock.R_UNKNOWN_SKU
    assert not stock.audit_path().exists()


def test_reading_an_unknown_sku_is_a_404(client):
    _catalogue(real=_sku("Real"))
    r = client.get("/stock/ghost")
    assert r.status_code == 404
    assert r.json()["reason"] == stock.R_UNKNOWN_SKU


@pytest.mark.parametrize("body,expected", [
    ({}, "stock_units_missing"),
    ({"units": 1.5}, "stock_units_fractional"),
    ({"units": "40"}, "stock_units_not_a_whole_number"),
    ({"units": True}, "stock_units_not_a_whole_number"),
    ({"units": -1}, "stock_units_negative"),
    ({"units": 1_000_001}, "stock_units_implausible"),
])
def test_recount_refusals_are_named_and_write_nothing(client, body, expected):
    _catalogue(parle_g=_sku("Parle-G"))
    r = client.post("/stock/parle_g/count", json=body)
    assert r.status_code == 400
    assert r.json()["reason"] == expected
    assert not manage.stock_path().exists()
    assert not stock.audit_path().exists()


def test_zero_is_a_valid_count_and_means_the_shelf_is_empty(client):
    _catalogue(parle_g=_sku("Parle-G"))
    r = client.post("/stock/parle_g/count", json={"units": 0})
    assert r.status_code == 200
    assert _row(client, "parle_g")["on_hand_units"] == 0


@pytest.mark.parametrize("body,expected", [
    ({}, "reorder_level_missing"),
    ({"units": 1.5}, "reorder_level_fractional"),
    ({"units": "12"}, "reorder_level_not_a_whole_number"),
    ({"units": True}, "reorder_level_not_a_whole_number"),
    ({"units": -1}, "reorder_level_negative"),
    ({"units": 100_001}, "reorder_level_implausible"),
])
def test_reorder_refusals_are_named_and_write_nothing(client, body, expected):
    _catalogue(parle_g=_sku("Parle-G"))
    r = client.post("/stock/parle_g/reorder", json=body)
    assert r.status_code == 400
    assert r.json()["reason"] == expected
    assert not stock.audit_path().exists()


def test_a_level_of_zero_is_valid_and_means_tell_me_when_it_is_empty(client):
    _catalogue(parle_g=_sku("Parle-G"))
    assert client.post("/stock/parle_g/reorder", json={"units": 0}).status_code == 200
    client.post("/stock/parle_g/count", json={"units": 0})
    assert [r["sku_id"] for r in client.get("/stock/low").json()["low"]] == ["parle_g"]


@pytest.mark.parametrize("raw", ["nought", "0", "-2", "2001"])
def test_limit_refusals_are_named(client, raw):
    r = client.get(f"/stock/movements?limit={raw}")
    assert r.status_code == 400
    assert r.json()["reason"] == stock.R_BAD_LIMIT


def test_the_inventory_derivation_going_missing_is_named_not_a_crash(
        client, monkeypatch):
    """If manage.py is refactored out from under this module, the screen must
    say so rather than compute a second answer to a question manage owns."""
    _catalogue(parle_g=_sku("Parle-G"))
    monkeypatch.delattr(manage, "_inventory_rows", raising=False)
    monkeypatch.delattr(manage, "inventory_rows", raising=False)
    r = client.get("/stock")
    assert r.status_code == 400
    assert r.json()["reason"] == stock.R_NO_INVENTORY
    assert "manage.py" in r.json()["detail"]


def test_a_public_derivation_on_manage_is_preferred_to_the_private_one(
        client, monkeypatch):
    """So the day the orchestrator promotes manage's private helper, this module
    follows without an edit."""
    _catalogue(parle_g=_sku("Parle-G"))
    monkeypatch.setattr(
        manage, "inventory_rows",
        lambda: {"items": [{"sku_id": "promoted", "name": "Promoted",
                            "opening_stock_units": 7,
                            "opening_stock_counted_at": None,
                            "billed_since_count": 0,
                            "remaining_units": 7}], "chain": {}},
        raising=False)
    body = client.get("/stock").json()
    assert [r["sku_id"] for r in body["items"]] == ["promoted"]
    assert body["items"][0]["on_hand_units"] == 7


def test_an_unexpected_failure_is_a_400_and_never_a_500(client, monkeypatch):
    _catalogue(parle_g=_sku("Parle-G"))

    def _boom(*_a, **_k):
        raise RuntimeError("the disk fell off")

    monkeypatch.setattr(stock, "read_events", _boom)
    for path in ("/stock", "/stock/low", "/stock/movements", "/stock/parle_g"):
        r = client.get(path)
        assert r.status_code == 400, path
        assert r.json()["reason"] == stock.R_INTERNAL, path
        assert "RuntimeError" in r.json()["detail"]


def test_a_movement_that_moves_a_product_out_of_the_catalogue_is_still_shown(
        client):
    """A SKU deleted after a delivery was booked against it must not make the
    movement list disagree with the products, with no way to see why."""
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/in", json={"units": 4, "reason": "delivery"})
    _catalogue(other=_sku("Other"))                   # parle_g is gone
    body = client.get("/stock").json()
    gone = body["moved_but_not_in_catalogue"]
    assert [g["sku_id"] for g in gone] == ["parle_g"]
    assert gone[0]["units_in"] == 4
    assert gone[0]["in_catalogue"] is False


# =============================================================== the invariants

def test_no_endpoint_ever_claims_to_settle_money(client):
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/count", json={"units": 4})
    for path in ("/stock", "/stock/low", "/stock/movements", "/stock/parle_g"):
        assert client.get(path).json()["settles_money"] is False
    r = client.post("/stock/parle_g/in", json={"units": 1, "reason": "delivery"})
    assert r.json()["settles_money"] is False


def test_this_module_handles_no_money_at_all(client):
    """Units are counts, never money. A valuation of the shelf would be an
    arithmetic claim about rupees, and this module has no business making one —
    so no field on the wire is money, and there is no float anywhere either."""
    _catalogue(parle_g=_sku("Parle-G", 1999))
    client.post("/stock/parle_g/count", json={"units": 40})
    client.post("/stock/parle_g/in", json={"units": 2, "reason": "delivery"})
    client.post("/stock/parle_g/reorder", json={"units": 10})

    def _walk(node, where):
        if isinstance(node, dict):
            for k, v in node.items():
                assert "paise" not in k, f"{where}.{k} is money"
                assert "rupee" not in k, f"{where}.{k} is money"
                _walk(v, f"{where}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, f"{where}[{i}]")
        else:
            assert not isinstance(node, float), \
                f"{where} is a float: {node!r}"

    for path in ("/stock", "/stock/low", "/stock/movements", "/stock/parle_g"):
        _walk(client.get(path).json(), path)




def _repo_file_fingerprint(*parts: str):
    """(exists, size, mtime_ns) for a file under the repo's own `results/`.

    THE POINT IS "THIS TEST DID NOT TOUCH IT", NOT "IT DOES NOT EXIST". The
    leak checks below used to assert the real file was absent outright, which
    made them fail the moment anybody ran the counter for real — a seeded shop,
    a demo, an agent driving the live server all legitimately create
    `results/shop/*.audit.jsonl`, and the suite then reported a leak that had
    not happened. Comparing a before/after fingerprint detects the actual
    defect (a scratch test writing into the repo) and is blind to a shop that
    has simply been used.
    """
    p = pathlib.Path(__file__).resolve().parent.parent.joinpath(*parts)
    try:
        st = p.stat()
        return (True, st.st_size, st.st_mtime_ns)
    except FileNotFoundError:
        return (False, 0, 0)


def test_nothing_is_written_outside_the_shop_directory(client, tmp_path):
    """GAWAAH_SHOP_DIR, honoured everywhere. A harness once destroyed the live
    catalogue by not doing this."""
    before = _repo_file_fingerprint("results", "shop", "stock.audit.jsonl")
    _catalogue(parle_g=_sku("Parle-G"))
    client.post("/stock/parle_g/count", json={"units": 4})
    client.post("/stock/parle_g/in", json={"units": 4, "reason": "delivery"})
    client.post("/stock/parle_g/reorder", json={"units": 2})

    shop = manage.store_dir()
    assert shop.is_relative_to(tmp_path)
    assert stock.audit_path().is_relative_to(shop)
    assert manage.stock_path().is_relative_to(shop)
    assert _repo_file_fingerprint("results", "shop", "stock.audit.jsonl") == before


def test_the_router_carries_no_prefix_and_the_paths_are_absolute():
    """The orchestrator mounts this bare. A prefix would give /stock/stock."""
    assert stock.router.prefix == ""
    paths = {r.path for r in stock.router.routes}
    assert paths == {
        "/stock", "/stock/low", "/stock/movements", "/stock/{sku_id}",
        "/stock/{sku_id}/in", "/stock/{sku_id}/out", "/stock/{sku_id}/count",
        "/stock/{sku_id}/reorder", "/stock/{sku_id}/floor",
    }


def test_the_literal_routes_are_declared_before_the_wildcard_one():
    """Declaration order is what stops 'low' being read as a product id, and
    nothing about the code makes that visible at a glance."""
    order = [r.path for r in stock.router.routes]
    assert order.index("/stock/low") < order.index("/stock/{sku_id}")
    assert order.index("/stock/movements") < order.index("/stock/{sku_id}")
