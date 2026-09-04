"""gawaah/expiry.py — what goes off, and what it is worth when it does.

The module exists so a shopkeeper can read, at a glance, what to clear first.
The suite is organised around the ways that list could stop being believable:

  1. It could invent a value        -> unpriced rows are null with a sentence,
                                       totals say how many rows they omit, and
                                       every rupee is units × marked price in
                                       integer paise (the odd prices below make
                                       a rounding bug show in the second place)
  2. It could get the day wrong     -> "expired" means before today, a packet
                                       dated today is not expired, and today is
                                       pinned so the suite does not drift at
                                       midnight
  3. It could lose a write-off      -> every write is a verifiable chain line,
     or double one                     the stock OUT goes through stock.py's
                                       own writer, and every failure mode of
                                       that path is exercised: writer absent,
                                       writer refusing, own line failing after
                                       the stock line landed
  4. It could accept a number       -> one refusal test per named refusal, each
     nobody typed                      asserting nothing was recorded

Every fixture writes a REAL catalogue sidecar and a REAL hash chain, and the
stock router is mounted beside the expiry router so the figure a write-off
moves is read back from the Stock screen's own endpoint, not from a mock.
Nothing here writes to results/: both directory overrides are set for every
test, and one test asserts the chain landed under tmp.
"""
from __future__ import annotations

import ast
import json
import sys
from datetime import date, timedelta
import pathlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import expiry, manage, stock  # noqa: E402
from gawaah.ledger import Ledger, verify  # noqa: E402

#: Pinned, so "in 3 days" means the same thing at 23:59 as at 00:01.
TODAY = date(2026, 9, 2)

# Deliberately not round: 2145 × 12 = 25740 and a bug that divides or rounds
# shows up in the second decimal place or not at all.
DAHI = ("amul_dahi_400g", "Amul Dahi 400g", 2145)
BREAD = ("britannia_bread", "Britannia Bread", 3950)


def day(n: int) -> str:
    return (TODAY + timedelta(days=n)).isoformat()


# ------------------------------------------------------------------ fixtures

@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Nothing in this suite may see, let alone write, results/."""
    data = tmp_path / "data"
    shop = data / "shop"
    shop.mkdir(parents=True)
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(data))
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(shop))
    monkeypatch.setattr(expiry, "_today", lambda: TODAY)
    manage._CHAIN_CACHE.clear()
    yield
    manage._CHAIN_CACHE.clear()


@pytest.fixture
def client() -> TestClient:
    """Both routers, mounted bare, the way the orchestrator will mount them."""
    app = FastAPI()
    app.include_router(expiry.router)
    app.include_router(stock.router)
    return TestClient(app)


def _catalogue(**skus: dict) -> None:
    (manage.store_dir() / "catalog.json").write_text(json.dumps({
        "format": 2, "dim": 4,
        "gates": {"phi": 0.9, "theta": 0.1, "tau_mm": 4.0,
                  "phi_appearance_only": 0.92},
        "skus": skus,
    }), encoding="utf-8")


def _sku(name: str, price: int | None = 1000) -> dict:
    rec = {"name": name, "footprint_mm": 95.1, "taught_by": "mat_measured",
           "vectors": [[1.0, 0.0, 0.0, 0.0]], "photo": None, "photo_bytes": 0}
    if price is not None:
        rec["price_paise"] = price
    return rec


def _shop_with_dahi_and_bread() -> None:
    _catalogue(**{DAHI[0]: _sku(DAHI[1], DAHI[2]),
                  BREAD[0]: _sku(BREAD[1], BREAD[2])})


def _book(client: TestClient, sku_id: str, units: int, expires: str,
          **extra) -> dict:
    r = client.post("/expiry/batch", json={"sku_id": sku_id, "units": units,
                                           "expires_on": expires, **extra})
    assert r.status_code == 200, r.json()
    return r.json()


def _stock_lines() -> list[dict]:
    """The MOVEMENTS on the stock chain. A `stock.count` line written by a
    fixture is on the same file and is not one."""
    p = stock.audit_path()
    if not p.exists():
        return []
    out = [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]
    return [ln for ln in out if ln.get("event") in ("stock.in", "stock.out")]


def _expiry_lines() -> list[dict]:
    p = expiry.audit_path()
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


# ============================================================ the empty shop

def test_an_empty_shop_answers_with_nothing_and_no_chain(client):
    body = client.get("/expiry").json()
    assert body["ok"] is True
    assert body["settles_money"] is False
    assert body["counts"] == {"batches": 0, "open": 0, "expired": 0,
                              "soon": 0, "closed": 0}
    assert body["expired"] == [] and body["soon"] == []
    assert body["value_at_risk"]["expired_paise"] == 0
    assert body["chain"]["exists"] is False
    assert body["today"] == TODAY.isoformat()


def test_the_overview_publishes_the_picker_list_with_marked_prices(client):
    _shop_with_dahi_and_bread()
    body = client.get("/expiry").json()
    names = [p["sku_id"] for p in body["products"]]
    assert names == [DAHI[0], BREAD[0]]  # sorted by name: Amul before Britannia
    assert body["products"][0]["price_paise"] == 2145
    assert body["products"][0]["price_rupees"] == "21.45"


# ========================================================= recording a batch



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


def test_recording_a_batch_writes_one_verifiable_line_under_the_shop_dir(
        client, tmp_path):
    before = _repo_file_fingerprint("results", "shop", "expiry.audit.jsonl")
    _shop_with_dahi_and_bread()
    out = _book(client, DAHI[0], 12, day(5), note="morning van")
    assert out["batch_id"].startswith("bt_") and len(out["batch_id"]) == 15
    assert out["units"] == 12 and out["units_remaining"] == 12
    assert out["days_left"] == 5 and out["state"] == "open"
    assert out["stock_in_recorded"] is False
    assert "not a delivery" in out["detail"] or "note about a date" in out["detail"]

    path = expiry.audit_path()
    # HONOURS GAWAAH_SHOP_DIR: the chain is under tmp, never under results/.
    assert str(path).startswith(str(tmp_path))
    assert path.name == "expiry.audit.jsonl"
    ok, n, _head, err = verify(path)
    assert ok and n == 1 and err is None
    line = _expiry_lines()[0]
    assert line["event"] == "expiry.batch"
    assert line["units"] == 12 and line["expires_on"] == day(5)
    assert line["note"] == "morning van"
    # And the real shop's chain in results/ was never touched by this test.
    assert _repo_file_fingerprint("results", "shop", "expiry.audit.jsonl") == before


def test_a_batch_of_something_the_shop_does_not_sell_is_a_404_by_name(client):
    _shop_with_dahi_and_bread()
    r = client.post("/expiry/batch", json={"sku_id": "nope", "units": 1,
                                           "expires_on": day(1)})
    assert r.status_code == 404
    assert r.json()["reason"] == expiry.R_UNKNOWN_SKU
    assert r.json()["ok"] is False
    assert _expiry_lines() == []


@pytest.mark.parametrize("body,reason", [
    ({"units": 1, "expires_on": "2026-09-10"}, expiry.R_SKU_MISSING),
    ({"sku_id": "", "units": 1, "expires_on": "2026-09-10"}, expiry.R_SKU_MISSING),
    ({"sku_id": 7, "units": 1, "expires_on": "2026-09-10"}, expiry.R_SKU_MISSING),
])
def test_a_batch_has_to_be_of_something(client, body, reason):
    _shop_with_dahi_and_bread()
    r = client.post("/expiry/batch", json=body)
    assert r.status_code == 400
    assert r.json()["reason"] == reason
    assert _expiry_lines() == []


@pytest.mark.parametrize("units,reason", [
    (None, expiry.R_UNITS_MISSING),
    (2.5, expiry.R_UNITS_FRACTIONAL),
    (2.0, expiry.R_UNITS_NOT_INTEGER),
    ("12", expiry.R_UNITS_NOT_INTEGER),
    (True, expiry.R_UNITS_NOT_INTEGER),
    (0, expiry.R_UNITS_NOT_POSITIVE),
    (-3, expiry.R_UNITS_NOT_POSITIVE),
    (expiry.MAX_BATCH_UNITS + 1, expiry.R_UNITS_TOO_LARGE),
])
def test_units_are_whole_positive_packets_and_each_wrong_kind_has_a_name(
        client, units, reason):
    """2.5 and 2.0 and '12' are three different mistakes with three different
    fixes, and a shopkeeper on a phone should not have to work out which."""
    _shop_with_dahi_and_bread()
    body = {"sku_id": DAHI[0], "expires_on": day(3)}
    if units is not None:
        body["units"] = units
    r = client.post("/expiry/batch", json=body)
    assert r.status_code == 400
    assert r.json()["reason"] == reason
    assert "Nothing" in r.json()["detail"] or "Send" in r.json()["detail"]
    assert _expiry_lines() == []


@pytest.mark.parametrize("expires,reason", [
    (None, expiry.R_DATE_MISSING),
    ("", expiry.R_DATE_MISSING),
    (20260915, expiry.R_DATE_NOT_TEXT),
    ("15/09/2026", expiry.R_DATE_MALFORMED),
    ("2026-9-5", expiry.R_DATE_MALFORMED),
    ("2026-02-30", expiry.R_DATE_IMPOSSIBLE),
    (day(expiry.MAX_DAYS_AHEAD + 1), expiry.R_DATE_TOO_FAR),
    (day(-(expiry.MAX_DAYS_BEHIND + 1)), expiry.R_DATE_TOO_OLD),
])
def test_the_date_is_the_one_printed_on_the_packet_or_it_is_refused_by_name(
        client, expires, reason):
    _shop_with_dahi_and_bread()
    body = {"sku_id": DAHI[0], "units": 4}
    if expires is not None:
        body["expires_on"] = expires
    r = client.post("/expiry/batch", json=body)
    assert r.status_code == 400
    assert r.json()["reason"] == reason
    assert _expiry_lines() == []


def test_a_date_within_a_year_back_is_accepted_and_is_expired_at_once(client):
    """A packet found on the shelf a month past its date is a real thing to
    record — so it can be written off with the same one tap."""
    _shop_with_dahi_and_bread()
    out = _book(client, DAHI[0], 2, day(-30))
    assert out["state"] == "expired" and out["days_left"] == -30
    assert "went off 30 days ago" in out["detail"]


@pytest.mark.parametrize("note,reason", [
    (12, expiry.R_NOTE_NOT_TEXT),
    ("x" * (expiry.MAX_NOTE + 1), expiry.R_NOTE_TOO_LONG),
])
def test_a_note_is_text_and_is_capped(client, note, reason):
    _shop_with_dahi_and_bread()
    r = client.post("/expiry/batch", json={"sku_id": DAHI[0], "units": 1,
                                           "expires_on": day(1), "note": note})
    assert r.status_code == 400
    assert r.json()["reason"] == reason
    assert _expiry_lines() == []


def test_a_body_that_is_not_a_json_object_is_refused_not_crashed(client):
    _shop_with_dahi_and_bread()
    r = client.post("/expiry/batch", content=b"not json",
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.json()["reason"] == expiry.R_BAD_BODY
    r = client.post("/expiry/batch", json=[1, 2, 3])
    assert r.status_code == 400
    assert r.json()["reason"] == expiry.R_BAD_BODY
    assert _expiry_lines() == []


@pytest.mark.parametrize("key", ["price_paise", "value_at_risk_paise", "amount"])
def test_the_browser_cannot_price_a_batch(client, key):
    """INVARIANT 8. A value the page asserted and the server quietly dropped
    is a value the page can go on showing as though the shop agreed to it."""
    _shop_with_dahi_and_bread()
    r = client.post("/expiry/batch", json={"sku_id": DAHI[0], "units": 1,
                                           "expires_on": day(1), key: 500})
    assert r.status_code == 400
    assert r.json()["reason"] == expiry.R_CLIENT_PRICED
    assert _expiry_lines() == []


# ============================================================ expiring soon

def test_soon_is_ranked_soonest_first_and_a_packet_dated_today_is_in_it(client):
    _shop_with_dahi_and_bread()
    _book(client, BREAD[0], 3, day(6))
    _book(client, DAHI[0], 12, day(2))
    _book(client, DAHI[0], 5, day(0))
    _book(client, BREAD[0], 8, day(20))     # outside a week
    _book(client, DAHI[0], 1, day(-1))      # expired: not "soon", it is gone

    body = client.get("/expiry/soon").json()
    assert body["ok"] is True
    assert body["window_days"] == expiry.DEFAULT_WINDOW_DAYS == 7
    assert [b["days_left"] for b in body["batches"]] == [0, 2, 6]
    assert body["count"] == 3
    assert all(b["state"] == "open" for b in body["batches"])


def test_the_window_widens_and_narrows_and_zero_means_today(client):
    _shop_with_dahi_and_bread()
    _book(client, DAHI[0], 5, day(0))
    _book(client, DAHI[0], 12, day(2))
    _book(client, BREAD[0], 8, day(20))
    assert [b["days_left"] for b in
            client.get("/expiry/soon?days=0").json()["batches"]] == [0]
    assert [b["days_left"] for b in
            client.get("/expiry/soon?days=30").json()["batches"]] == [0, 2, 20]
    over = client.get("/expiry?days=30").json()
    assert over["counts"]["soon"] == 3 and over["window_days"] == 30


@pytest.mark.parametrize("days", ["abc", "-1", str(expiry.MAX_WINDOW_DAYS + 1), "2.5"])
def test_a_window_that_is_not_a_whole_number_of_days_in_range_is_refused(
        client, days):
    r = client.get(f"/expiry/soon?days={days}")
    assert r.status_code == 400
    assert r.json()["reason"] == expiry.R_BAD_DAYS
    r = client.get(f"/expiry?days={days}")
    assert r.status_code == 400
    assert r.json()["reason"] == expiry.R_BAD_DAYS


# ============================================================ value at risk

def test_value_at_risk_is_units_times_marked_price_in_integer_paise(client):
    _shop_with_dahi_and_bread()
    _book(client, DAHI[0], 12, day(2))      # 12 × 2145 = 25740
    _book(client, BREAD[0], 3, day(4))      # 3 × 3950 = 11850
    body = client.get("/expiry/soon").json()
    dahi, bread = body["batches"]
    assert dahi["price_paise"] == 2145 and dahi["value_at_risk_paise"] == 25740
    assert isinstance(dahi["value_at_risk_paise"], int)
    assert dahi["value_at_risk_rupees"] == "257.40"
    assert bread["value_at_risk_paise"] == 11850
    assert body["value_at_risk_paise"] == 25740 + 11850 == 37590
    assert body["value_at_risk_rupees"] == "375.90"
    assert body["unpriced_batches"] == 0
    # The response says what the number is, and what it is not.
    assert "not a charge" in body["value_at_risk"]["note"]
    assert "marked price" in body["value_at_risk"]["basis"]


def test_a_product_with_no_price_is_null_with_a_sentence_and_out_of_the_total(
        client):
    """Never a zero standing in for "unknown": a zero is a claim."""
    _catalogue(**{DAHI[0]: _sku(DAHI[1], DAHI[2]),
                  "loose_bread": _sku("Loose bread", None)})
    _book(client, DAHI[0], 2, day(1))       # 4290
    _book(client, "loose_bread", 9, day(1))
    body = client.get("/expiry/soon").json()
    unpriced = next(b for b in body["batches"] if b["sku_id"] == "loose_bread")
    assert unpriced["price_paise"] is None
    assert unpriced["value_at_risk_paise"] is None
    assert unpriced["value_at_risk_rupees"] is None
    assert "no price" in unpriced["value_why"]
    assert body["value_at_risk_paise"] == 4290
    assert body["unpriced_batches"] == 1
    assert body["value_at_risk"]["soon_unpriced_batches"] == 1


def test_a_batch_whose_product_left_the_catalogue_is_still_listed_unpriced(
        client):
    _shop_with_dahi_and_bread()
    _book(client, BREAD[0], 4, day(1))
    _catalogue(**{DAHI[0]: _sku(DAHI[1], DAHI[2])})    # bread is gone
    body = client.get("/expiry").json()
    assert body["counts"]["soon"] == 1
    row = body["soon"][0]
    assert row["in_catalogue"] is False
    assert row["name"] == BREAD[1]                    # the name on the line
    assert row["value_at_risk_paise"] is None
    assert "no longer in the catalogue" in row["value_why"]


# ================================================================= expired

def test_expired_means_before_today_and_a_packet_dated_today_is_not(client):
    _shop_with_dahi_and_bread()
    _book(client, DAHI[0], 5, day(0))
    _book(client, DAHI[0], 2, day(-1))
    _book(client, BREAD[0], 1, day(-9))
    body = client.get("/expiry/expired").json()
    # Longest expired first: the packet most likely to be sold by mistake.
    assert [b["days_left"] for b in body["batches"]] == [-9, -1]
    assert all(b["state"] == "expired" for b in body["batches"])
    assert body["value_at_risk_paise"] == 3950 + 2 * 2145 == 8240
    over = client.get("/expiry").json()
    assert over["counts"] == {"batches": 3, "open": 3, "expired": 2,
                              "soon": 1, "closed": 0}
    assert over["value_at_risk"]["expired_paise"] == 8240
    assert over["value_at_risk"]["soon_paise"] == 5 * 2145


# =============================================================== write-off

def test_a_write_off_appends_a_stock_out_with_reason_expiry_through_stock_py(
        client):
    """The one integration that matters: the shelf figure on the STOCK
    screen's own endpoint comes down by the units written off."""
    _shop_with_dahi_and_bread()
    client.post(f"/stock/{DAHI[0]}/count", json={"units": 40})
    booked = _book(client, DAHI[0], 12, day(-2))

    r = client.post(f"/expiry/batch/{booked['batch_id']}/write-off")
    assert r.status_code == 200, r.json()
    out = r.json()
    assert out["written_off_now"] == 12
    assert out["units_remaining"] == 0 and out["state"] == "closed"
    assert out["stock_recorded"] is True
    assert out["stock_figure_needs_recount"] is False
    assert out["stock_movement_id"].startswith("mv_")
    assert out["written_off_value_paise"] == 12 * 2145 == 25740
    assert out["written_off_value_rupees"] == "257.40"
    assert "come down by 12" in out["detail"]
    assert "not a charge" in out["detail"]

    lines = _stock_lines()
    assert len(lines) == 1
    assert lines[0]["event"] == "stock.out"
    assert lines[0]["units"] == -12
    assert lines[0]["reason"] == "expiry"
    assert lines[0]["movement_id"] == out["stock_movement_id"]
    assert booked["batch_id"] in lines[0]["note"]
    ok, _n, _h, err = verify(stock.audit_path())
    assert ok and err is None

    shelf = client.get(f"/stock/{DAHI[0]}").json()
    assert shelf["on_hand_units"] == 28
    assert shelf["units_out_since_count"] == 12
    # And stock.py's own reader labels the line in its own words.
    assert shelf["movements"][0]["reason_label"] == "past its date"

    ok, n, _h, err = verify(expiry.audit_path())
    assert ok and n == 2 and err is None
    mine = _expiry_lines()[1]
    assert mine["event"] == "expiry.written_off"
    assert mine["stock_recorded"] is True
    assert mine["stock_movement_id"] == out["stock_movement_id"]
    assert client.get("/expiry").json()["counts"]["expired"] == 0


def test_a_write_off_can_be_partial_and_the_batch_closes_when_nothing_is_left(
        client):
    _shop_with_dahi_and_bread()
    b = _book(client, DAHI[0], 12, day(-1))
    r = client.post(f"/expiry/batch/{b['batch_id']}/write-off", json={"units": 4})
    assert r.status_code == 200
    assert r.json()["units_remaining"] == 8 and r.json()["state"] == "expired"
    assert r.json()["value_at_risk_paise"] == 8 * 2145
    r = client.post(f"/expiry/batch/{b['batch_id']}/write-off", json={"units": 8})
    assert r.status_code == 200
    assert r.json()["state"] == "closed"
    assert r.json()["written_off_units"] == 12
    r = client.post(f"/expiry/batch/{b['batch_id']}/write-off")
    assert r.status_code == 400
    assert r.json()["reason"] == expiry.R_BATCH_CLOSED
    assert sum(ln["units"] for ln in _stock_lines()) == -12


def test_more_than_the_batch_has_left_is_refused_and_nothing_moves(client):
    _shop_with_dahi_and_bread()
    b = _book(client, DAHI[0], 8, day(-1))
    r = client.post(f"/expiry/batch/{b['batch_id']}/write-off", json={"units": 12})
    assert r.status_code == 400
    assert r.json()["reason"] == expiry.R_UNITS_OVER_REMAINING
    assert _stock_lines() == []
    assert len(_expiry_lines()) == 1
    assert client.get("/expiry").json()["expired"][0]["units_remaining"] == 8


def test_a_batch_that_does_not_exist_is_a_404_and_a_bad_id_is_a_400(client):
    _shop_with_dahi_and_bread()
    r = client.post("/expiry/batch/bt_000000000000/write-off")
    assert r.status_code == 404
    assert r.json()["reason"] == expiry.R_NO_BATCH
    r = client.post("/expiry/batch/../catalog/write-off")
    assert r.status_code in (400, 404)
    r = client.post("/expiry/batch/nope/write-off")
    assert r.status_code == 400
    assert r.json()["reason"] == expiry.R_BAD_BATCH_ID
    assert _stock_lines() == []


def test_when_stock_py_exposes_no_writer_the_write_off_is_recorded_here_and_says_recount(
        client, monkeypatch):
    """The fallback the module promises: the batch is written off, the stock
    figure is stated NOT to have moved, and the shopkeeper is told to count."""
    _shop_with_dahi_and_bread()
    b = _book(client, DAHI[0], 3, day(-1))
    monkeypatch.setattr(expiry, "_stock_writer", lambda: None)
    assert client.get("/expiry").json()["stock_link"]["available"] is False

    r = client.post(f"/expiry/batch/{b['batch_id']}/write-off")
    assert r.status_code == 200
    out = r.json()
    assert out["state"] == "closed"
    assert out["stock_recorded"] is False
    assert out["stock_movement_id"] is None
    assert out["stock_figure_needs_recount"] is True
    assert "count that shelf again" in out["detail"]
    assert _stock_lines() == []
    mine = _expiry_lines()[1]
    assert mine["stock_recorded"] is False and mine["stock_error"]


def test_when_stock_pys_writer_refuses_the_write_off_still_lands_here(
        client, monkeypatch):
    _shop_with_dahi_and_bread()
    b = _book(client, DAHI[0], 3, day(-1))

    def refuse(*_a, **_k):
        raise stock.StockRefused(stock.R_NOT_RECORDED, "No space left on device")
    monkeypatch.setattr(stock, "_append", refuse)

    r = client.post(f"/expiry/batch/{b['batch_id']}/write-off")
    assert r.status_code == 200
    out = r.json()
    assert out["stock_recorded"] is False
    assert out["stock_figure_needs_recount"] is True
    assert "No space left on device" in out["stock_error"]
    assert "StockRefused" in out["stock_error"]
    assert out["state"] == "closed"
    assert _stock_lines() == []


def test_if_this_modules_own_line_fails_after_the_stock_line_landed_the_stock_line_is_reversed(
        client, monkeypatch):
    """The two logs may not disagree about whether a write-off happened."""
    _shop_with_dahi_and_bread()
    client.post(f"/stock/{DAHI[0]}/count", json={"units": 20})
    b = _book(client, DAHI[0], 5, day(-1))

    def fail(*_a, **_k):
        raise expiry.ExpiryRefused(expiry.R_NOT_RECORDED, "disk full")
    real_append = expiry._append
    monkeypatch.setattr(expiry, "_append", fail)

    r = client.post(f"/expiry/batch/{b['batch_id']}/write-off")
    assert r.status_code == 400
    assert r.json()["reason"] == expiry.R_NOT_RECORDED

    lines = _stock_lines()
    assert [(ln["event"], ln["units"], ln["reason"]) for ln in lines] == [
        ("stock.out", -5, "expiry"),
        ("stock.in", 5, "correction"),
    ]
    assert lines[0]["movement_id"] in lines[1]["note"]
    assert client.get(f"/stock/{DAHI[0]}").json()["on_hand_units"] == 20
    # Restore ONLY the writer. `monkeypatch.undo()` would also lift the
    # fixture's GAWAAH_SHOP_DIR, and the next read would be of the live shop.
    monkeypatch.setattr(expiry, "_append", real_append)
    assert client.get("/expiry").json()["expired"][0]["units_remaining"] == 5


def test_a_write_off_with_no_body_at_all_takes_everything_left(client):
    _shop_with_dahi_and_bread()
    b = _book(client, BREAD[0], 7, day(-3))
    r = client.post(f"/expiry/batch/{b['batch_id']}/write-off", content=b"")
    assert r.status_code == 200
    assert r.json()["written_off_now"] == 7 and r.json()["state"] == "closed"


# ==================================================================== sold

def test_sold_through_takes_units_off_the_batch_and_writes_no_stock_line(
        client):
    """A sale is already on the audit chain; a second line would take the
    same packet off the shelf twice."""
    _shop_with_dahi_and_bread()
    b = _book(client, DAHI[0], 12, day(2))
    r = client.post(f"/expiry/batch/{b['batch_id']}/sold", json={"units": 10})
    assert r.status_code == 200
    out = r.json()
    assert out["sold_now"] == 10 and out["units_remaining"] == 2
    assert out["stock_recorded"] is False
    assert out["stock_figure_needs_recount"] is False
    assert out["value_at_risk_paise"] == 2 * 2145
    assert "already on the audit chain" in out["detail"]
    assert _stock_lines() == []

    r = client.post(f"/expiry/batch/{b['batch_id']}/sold", json={"units": 5})
    assert r.status_code == 400
    assert r.json()["reason"] == expiry.R_UNITS_OVER_REMAINING

    r = client.post(f"/expiry/batch/{b['batch_id']}/sold")
    assert r.status_code == 200
    assert r.json()["state"] == "closed" and r.json()["sold_units"] == 12
    assert client.get("/expiry").json()["counts"]["closed"] == 1


# ================================================================ stock in

def test_stock_in_true_books_the_delivery_on_the_stock_log_too(client):
    _shop_with_dahi_and_bread()
    client.post(f"/stock/{DAHI[0]}/count", json={"units": 10})
    out = _book(client, DAHI[0], 24, day(5), stock_in=True)
    assert out["stock_in_requested"] is True
    assert out["stock_in_recorded"] is True
    assert out["stock_figure_needs_recount"] is False
    assert "gone up by 24" in out["detail"]
    lines = _stock_lines()
    assert len(lines) == 1
    assert (lines[0]["event"], lines[0]["units"], lines[0]["reason"]) == \
        ("stock.in", 24, "delivery")
    assert client.get(f"/stock/{DAHI[0]}").json()["on_hand_units"] == 34
    listed = client.get(f"/expiry/batches?sku={DAHI[0]}").json()["batches"][0]
    assert listed["stock_in_recorded"] is True
    assert listed["stock_in_movement_id"] == lines[0]["movement_id"]


@pytest.mark.parametrize("flag", [None, False, "true", 1])
def test_only_a_real_json_true_books_a_delivery(client, flag):
    _shop_with_dahi_and_bread()
    extra = {} if flag is None else {"stock_in": flag}
    out = _book(client, DAHI[0], 6, day(5), **extra)
    assert out["stock_in_recorded"] is False
    assert _stock_lines() == []


def test_stock_in_with_no_writer_records_the_batch_and_says_recount(
        client, monkeypatch):
    _shop_with_dahi_and_bread()
    monkeypatch.setattr(expiry, "_stock_writer", lambda: None)
    out = _book(client, DAHI[0], 6, day(5), stock_in=True)
    assert out["stock_in_requested"] is True
    assert out["stock_in_recorded"] is False
    assert out["stock_figure_needs_recount"] is True
    assert "has not moved" in out["detail"]
    assert len(_expiry_lines()) == 1


def test_if_the_batch_line_fails_after_a_delivery_landed_the_delivery_is_reversed(
        client, monkeypatch):
    _shop_with_dahi_and_bread()
    client.post(f"/stock/{DAHI[0]}/count", json={"units": 10})

    def fail(*_a, **_k):
        raise expiry.ExpiryRefused(expiry.R_NOT_RECORDED, "disk full")
    monkeypatch.setattr(expiry, "_append", fail)
    r = client.post("/expiry/batch", json={"sku_id": DAHI[0], "units": 6,
                                           "expires_on": day(5),
                                           "stock_in": True})
    assert r.status_code == 400
    assert r.json()["reason"] == expiry.R_NOT_RECORDED
    lines = _stock_lines()
    assert [(ln["event"], ln["units"], ln["reason"]) for ln in lines] == [
        ("stock.in", 6, "delivery"),
        ("stock.out", -6, "correction"),
    ]
    assert client.get(f"/stock/{DAHI[0]}").json()["on_hand_units"] == 10


# ============================================================ listing batches

def test_batches_filters_by_sku_and_hides_closed_ones_unless_asked(client):
    _shop_with_dahi_and_bread()
    a = _book(client, DAHI[0], 2, day(3))
    _book(client, DAHI[0], 4, day(1))
    _book(client, BREAD[0], 1, day(2))
    client.post(f"/expiry/batch/{a['batch_id']}/sold")

    body = client.get(f"/expiry/batches?sku={DAHI[0]}").json()
    assert body["count"] == 1
    assert body["batches"][0]["days_left"] == 1
    body = client.get(f"/expiry/batches?sku={DAHI[0]}&include_closed=1").json()
    assert [b["state"] for b in body["batches"]] == ["open", "closed"]
    body = client.get("/expiry/batches").json()
    assert [b["days_left"] for b in body["batches"]] == [1, 2]
    assert body["sku"] is None


def test_the_history_on_a_batch_is_every_line_that_touched_it(client):
    _shop_with_dahi_and_bread()
    b = _book(client, DAHI[0], 10, day(-1), note="van")
    client.post(f"/expiry/batch/{b['batch_id']}/sold", json={"units": 4})
    client.post(f"/expiry/batch/{b['batch_id']}/write-off",
                json={"units": 6, "note": "bin"})
    row = client.get("/expiry/batches?include_closed=1").json()["batches"][0]
    assert [(h["kind"], h["units"]) for h in row["history"]] == [
        ("booked", 10), ("sold", 4), ("written_off", 6)]
    assert row["history"][0]["note"] == "van"
    assert row["history"][2]["note"] == "bin"
    assert row["history"][2]["stock_recorded"] is True
    assert all(h["hash"] for h in row["history"])


# ================================================================== the chain

def test_a_broken_chain_is_reported_and_only_the_verified_prefix_counts(client):
    _shop_with_dahi_and_bread()
    _book(client, DAHI[0], 1, day(1))
    _book(client, DAHI[0], 2, day(2))
    _book(client, DAHI[0], 3, day(3))
    path = expiry.audit_path()
    lines = path.read_text().splitlines()
    rec = json.loads(lines[1])
    rec["units"] = 200                                  # an edit by hand
    lines[1] = json.dumps(rec, sort_keys=True, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n")

    body = client.get("/expiry").json()
    assert body["ok"] is True
    assert body["chain"]["ok"] is False
    assert body["chain"]["lines_verified"] == 1
    assert "line 2" in body["chain"]["error"]
    assert [b["units"] for b in body["soon"]] == [1]


def test_a_write_off_against_a_batch_not_on_the_chain_is_not_believed():
    """Such a line cannot be written by this module; if it is there the file
    was edited, and guessing which batch it meant would be fiction."""
    led = Ledger(expiry.audit_path())
    led.append(ts="2026-09-01T00:00:00+00:00", module="expiry",
               event="expiry.written_off", batch_id="bt_deadbeefcafe", units=3)
    led.append(ts="2026-09-01T00:00:01+00:00", module="expiry",
               event="expiry.batch", batch_id="bt_0123456789ab",
               sku_id=DAHI[0], units="nine", expires_on="2026-09-09")
    events, chain = expiry.read_events()
    assert chain["ok"] is True
    batches, skipped = expiry.batches_from(events)
    assert batches == {} and skipped == 2


# ================================================================ never a 500

def test_an_unreadable_catalogue_is_a_named_refusal_not_a_crash(
        client, monkeypatch):
    def boom():
        raise OSError("Permission denied")
    monkeypatch.setattr(manage, "catalogue", boom)
    for url in ("/expiry", "/expiry/soon", "/expiry/expired", "/expiry/batches"):
        r = client.get(url)
        assert r.status_code == 400, url
        assert r.json()["reason"] == expiry.R_CATALOGUE_UNAVAILABLE
        assert "Permission denied" in r.json()["detail"]
    r = client.post("/expiry/batch", json={"sku_id": DAHI[0], "units": 1,
                                           "expires_on": day(1)})
    assert r.status_code == 400
    assert r.json()["reason"] == expiry.R_CATALOGUE_UNAVAILABLE


def test_an_unexpected_exception_is_a_400_with_the_type_named(
        client, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("something nobody planned for")
    monkeypatch.setattr(expiry, "rows", boom)
    r = client.get("/expiry/expired")
    assert r.status_code == 400
    assert r.json()["reason"] == expiry.R_INTERNAL
    assert "RuntimeError" in r.json()["detail"]


# ============================================================== invariant 1

def test_no_float_and_no_true_division_anywhere_in_the_module():
    """The same three checks tools/lint_no_float.py applies to the strict
    money modules, applied to this one in full: value-at-risk is money."""
    src = Path(expiry.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            bad.append((node.lineno, "float literal"))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "float":
            bad.append((node.lineno, "float() cast"))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            bad.append((node.lineno, "true division"))
    assert bad == []


def test_the_module_builds_no_payment_string():
    """Invariant 6, asserted against the source: no UPI payload, no payment URL."""
    src = Path(expiry.__file__).read_text(encoding="utf-8").lower()
    assert "upi:" not in src
    assert "pay?" not in src and "payment_link" not in src and "rzp.io" not in src


def test_every_response_says_it_settles_no_money(client):
    _shop_with_dahi_and_bread()
    b = _book(client, DAHI[0], 2, day(-1))
    for r in (client.get("/expiry"), client.get("/expiry/soon"),
              client.get("/expiry/expired"), client.get("/expiry/batches"),
              client.post(f"/expiry/batch/{b['batch_id']}/sold", json={"units": 1}),
              client.post(f"/expiry/batch/{b['batch_id']}/write-off")):
        assert r.status_code == 200
        assert r.json()["settles_money"] is False
