"""gawaah/daybook.py — closing the shop, and the record of having closed it.

A close-out is only worth something if it is still true later. Everything below
is organised around the four ways this module could quietly stop being worth
something:

  1. IT COULD DRIFT. The whole point of a close-out is that a day closed on
     Tuesday reads the same on Friday. So the tests write a real hash-chained
     ledger, close a day off it, then keep TRADING on the same chain — and
     assert the frozen record does not move, while the live comparison beside
     it does. The same for a product renamed after the close.

  2. IT COULD DERIVE ITS OWN NUMBERS. It must not: `/manage/today` already folds
     the chain and a second definition of "the day's takings" is a second truth.
     The tests assert the two agree figure for figure on the same chain, and
     that the module refuses BY NAME when the day brief is not there to be read,
     rather than quietly computing something of its own.

  3. IT COULD BE OVERWRITTEN. Closing twice is refused by name, the first record
     survives the attempt untouched, and the chain carries a digest of the file
     so an edit afterwards is visible.

  4. IT COULD TURN A RUPEE INTO A FLOAT. The counted cash is integer paise,
     entered either as whole paise or as the rupee STRING the shopkeeper typed,
     parsed by `money.from_rupees_str`, which never touches a float. `4820.345`
     is refused rather than rounded.

Every named refusal in the module has a test here, and a meta-test fails if a
new one is added without one. Nothing in this file talks to a gateway, and no
response any of it produces settles money.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import daybook, manage  # noqa: E402
from gawaah.daybook import (  # noqa: E402
    MAX_CLOSED_BY,
    MAX_COUNTED_CASH_PAISE,
    MAX_NOTE,
    R_ALREADY_CLOSED,
    R_BAD_BODY,
    R_BAD_DAY,
    R_BAD_LIMIT,
    R_BAD_RUPEES,
    R_CASH_NEGATIVE,
    R_CASH_NOT_INTEGER,
    R_CASH_TOO_LARGE,
    R_CASH_TWICE,
    R_CLOSED_BY_TOO_LONG,
    R_DAY_IN_FUTURE,
    R_INTERNAL,
    R_NO_BILL_BOOK,
    R_NO_CASH,
    R_NO_TILL,
    R_NOT_CLOSED,
    R_NOT_WRITTEN,
    R_NOTE_TOO_LONG,
)
from gawaah.ledger import Ledger, verify  # noqa: E402
from tools import upload_app  # noqa: E402


# ------------------------------------------------------------------ fixtures


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Nothing in this suite may see, let alone write, results/.

    The shop directory is redirected TWO ways on purpose: `set_store_dir` moves
    the till's cached handle, and `GAWAAH_SHOP_DIR` covers any code that
    re-reads the environment. A harness that honoured only one of them once
    destroyed the live catalogue, and that is a mistake with no undo. The cached
    handle is a module global, so it is put back afterwards rather than left
    pointing at a tmp_path that no longer exists.

    The audit chain lives under GAWAAH_DATA_DIR, which is a DIFFERENT directory
    here — the same split the three real processes use, and the thing that makes
    "the day book writes its own chain, never results/audit.jsonl" checkable
    rather than asserted.
    """
    shop = tmp_path / "shop"
    shop.mkdir(parents=True)
    data = tmp_path / "data"
    data.mkdir(parents=True)
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(shop))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(data))
    was = upload_app._DEPS.get("store_dir")
    upload_app.set_store_dir(shop)
    manage._CHAIN_CACHE.clear()
    yield
    manage._CHAIN_CACHE.clear()
    upload_app._DEPS["store_dir"] = was
    upload_app._DEPS["store"] = None


@pytest.fixture()
def client() -> TestClient:
    """The router mounted the way the orchestrator will mount it: bare."""
    app = FastAPI()
    app.include_router(daybook.router)
    return TestClient(app)


@pytest.fixture()
def both() -> TestClient:
    """daybook AND manage in one app, so the two screens can be compared.

    Claim 2 in the module docstring is that this module freezes the day brief
    rather than inventing a second one. The only way to check that is to ask
    both endpoints about the same chain in the same process.
    """
    app = FastAPI()
    app.include_router(daybook.router)
    app.include_router(manage.router)
    return TestClient(app)


def _today() -> str:
    return daybook._today_label()


def _yesterday() -> str:
    tz = datetime.now().astimezone().tzinfo
    return (datetime.now(tz) - timedelta(days=1)).strftime("%Y-%m-%d")


def _tomorrow() -> str:
    tz = datetime.now().astimezone().tzinfo
    return (datetime.now(tz) + timedelta(days=1)).strftime("%Y-%m-%d")


def _at(day: str, hour: int = 12, second: int = 0) -> str:
    """Noon on `day` in the counter's own timezone, as the ledger stamps it.

    Noon and not midnight: the day window is local midnight to local midnight,
    and a fixture that wrote bills at 00:00 would be testing the boundary by
    accident on every run instead of on purpose.
    """
    tz = datetime.now().astimezone().tzinfo
    base = datetime.strptime(day, "%Y-%m-%d").replace(hour=hour, tzinfo=tz)
    return (base + timedelta(seconds=second)).isoformat()


def _catalogue(**names: str) -> None:
    """A catalogue on disk, the way manage.catalogue() reads one."""
    shop = Path(upload_app.store_dir())
    shop.mkdir(parents=True, exist_ok=True)
    (shop / "catalog.json").write_text(json.dumps({
        "format": 2, "dim": 4, "sha256": "", "gates": {},
        "skus": {sku: {"name": name, "price_paise": 2145,
                       "vectors": [[0, 0, 0, 1]], "footprint_mm": 95.0}
                 for sku, name in names.items()},
    }), encoding="utf-8")


def _bill(session_id: str, amount: int, *, day: str | None = None,
          sku: str = "parle", settle: bool = False, close: bool = True,
          hour: int = 12) -> None:
    """Write one session into the REAL chain, the way the real modules write it.

    The event names and reason strings are the ones `gawaah/manage.py` folds on
    — `session/exit`, `session/done`, `session/webhook` with `settled_green` —
    so these tests exercise the shipped definition of a bill rather than a
    private one invented for the harness.
    """
    day = day or _today()
    led = Ledger(manage.ledger_path())
    led.append(ts=_at(day, hour, 0), module="session", event="exit",
               session_id=session_id, reason="exit_crossing_committed",
               item_id=f"{sku}#0", price_paise=amount, abstained=False,
               excluded_from_total=False,
               **{"from": "PRICED", "to": "BASKET_OPEN"}, total_paise=amount)
    if close:
        led.append(ts=_at(day, hour, 1), module="session", event="done",
                   session_id=session_id, reason="intent_requested",
                   lines=1, amber_excluded=0,
                   **{"from": "BASKET_OPEN", "to": "AWAITING_SETTLEMENT"},
                   total_paise=amount)
    if settle:
        led.append(ts=_at(day, hour, 2), module="session", event="webhook",
                   session_id=session_id, reason="settled_green",
                   razorpay_event="payment.captured",
                   event_id=f"evt_{session_id}", webhook_amount_paise=amount,
                   **{"from": "AWAITING_SETTLEMENT", "to": "PAID"},
                   total_paise=amount)
    manage._CHAIN_CACHE.clear()


def _amber(session_id: str, *, day: str | None = None, hour: int = 13) -> None:
    """A packet the counter could not name: committed, excluded from the total.

    Invariant 7 on the chain. A close-out that dropped it would hide the one
    line the shopkeeper has to check by hand.
    """
    day = day or _today()
    led = Ledger(manage.ledger_path())
    led.append(ts=_at(day, hour, 0), module="session", event="exit",
               session_id=session_id, item_id="unknown#0",
               reason="exit_crossing_committed_amber_excluded",
               abstained=True, excluded_from_total=True, total_paise=0)
    led.append(ts=_at(day, hour, 1), module="session", event="done",
               session_id=session_id, reason="intent_requested",
               lines=0, amber_excluded=1,
               **{"from": "BASKET_OPEN", "to": "AWAITING_SETTLEMENT"},
               total_paise=0)
    manage._CHAIN_CACHE.clear()


def _closed_ok(client: TestClient, **over) -> dict:
    """Close a day and insist it worked.

    The default count is only supplied when the caller named NEITHER spelling —
    sending both is a refusal the module has on purpose, and a helper that
    silently added the second key would make that refusal fire inside every
    test that meant to use the rupee string.
    """
    body = dict(over)
    if "counted_cash_paise" not in body and "counted_cash_rupees" not in body:
        body["counted_cash_paise"] = 482000
    r = client.post("/daybook/close", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ============================================================ what it derives


def test_preview_reads_the_days_takings_off_the_chain(client):
    _bill("s1", 2145)
    _bill("s2", 3950, settle=True)
    d = client.get("/daybook/preview").json()
    assert d["ok"] is True
    assert d["day"] == _today()
    assert d["derived"]["bills"] == 2
    assert d["derived"]["revenue_paise"] == 2145 + 3950
    assert d["derived"]["revenue_rupees"] == "60.95"
    assert d["derived"]["settled_count"] == 1
    assert d["derived"]["settled_paise"] == 3950
    assert d["derived"]["awaiting_paise"] == 2145


def test_preview_writes_nothing(client):
    _bill("s1", 2145)
    client.get("/daybook/preview")
    assert client.get("/daybook").json()["count"] == 0
    assert not daybook.daybook_dir().exists()


def test_yesterdays_bill_is_not_in_todays_preview(client):
    _bill("s_old", 9999, day=_yesterday())
    _bill("s_new", 2145)
    d = client.get("/daybook/preview").json()
    assert d["derived"]["bills"] == 1
    assert d["derived"]["revenue_paise"] == 2145


def test_a_past_day_can_be_previewed_by_date(client):
    _bill("s_old", 9999, day=_yesterday())
    d = client.get("/daybook/preview", params={"day": _yesterday()}).json()
    assert d["day"] == _yesterday()
    assert d["derived"]["revenue_paise"] == 9999
    assert d["day_has_ended"] is True


def test_a_session_that_never_closed_is_not_a_bill(client):
    _bill("s_walked_away", 2145, close=False)
    d = client.get("/daybook/preview").json()
    assert d["derived"]["bills"] == 0
    assert d["derived"]["revenue_paise"] == 0


def test_an_amber_line_is_counted_and_never_priced(client):
    _bill("s1", 2145)
    _amber("s_amber")
    d = client.get("/daybook/preview").json()
    assert d["derived"]["excluded_lines"] == 1
    # The amber basket closed at zero, so it is a bill with nothing on it. The
    # revenue must not have moved.
    assert d["derived"]["revenue_paise"] == 2145


def test_top_sellers_rank_by_units_and_carry_the_catalogue_name(client):
    _catalogue(parle="Parle-G 200g", lifebuoy="Lifebuoy 125g")
    _bill("s1", 2145, sku="parle")
    _bill("s2", 2145, sku="parle", hour=13)
    _bill("s3", 3950, sku="lifebuoy", hour=14)
    top = client.get("/daybook/preview").json()["top_sellers"]
    assert [t["sku_id"] for t in top] == ["parle", "lifebuoy"]
    assert top[0]["name"] == "Parle-G 200g"
    assert top[0]["units"] == 2
    assert top[0]["revenue_paise"] == 4290
    assert top[0]["in_catalogue_at_close"] is True


def test_the_close_out_agrees_with_the_day_brief_figure_for_figure(both):
    """Claim 2: this module freezes the day brief, it does not re-derive it."""
    _bill("s1", 2145)
    _bill("s2", 3950, settle=True, hour=13)
    _amber("s_amber")
    brief = both.get("/manage/today").json()["today"]
    frozen = both.get("/daybook/preview").json()["derived"]
    for key in ("bills", "revenue_paise", "revenue_rupees", "average_paise",
                "settled_count", "settled_paise", "awaiting_count",
                "awaiting_paise", "excluded_lines", "first_bill_at",
                "last_bill_at"):
        assert frozen[key] == brief[key], key


# ================================================================== closing


def test_closing_freezes_the_figures_and_the_count(client):
    _bill("s1", 2145)
    out = _closed_ok(client, counted_cash_rupees="4820.00",
                     note="quiet evening", closed_by="Ramesh")
    rec = out["record"]
    assert out["closed"] is True
    assert rec["day"] == _today()
    assert rec["derived"]["revenue_paise"] == 2145
    assert rec["counted_cash_paise"] == 482000
    assert rec["counted_cash_rupees"] == "4820.00"
    assert rec["note"] == "quiet evening"
    assert rec["closed_by"] == "Ramesh"
    assert rec["format"] == daybook.CLOSE_FORMAT


def test_the_record_does_not_move_when_the_shop_keeps_trading(client):
    """The whole reason this module exists, as one assertion."""
    _bill("s1", 2145)
    _closed_ok(client)
    _bill("s_after_the_shutter", 5000, hour=21)

    d = client.get(f"/daybook/{_today()}").json()
    assert d["record"]["derived"]["bills"] == 1
    assert d["record"]["derived"]["revenue_paise"] == 2145
    assert d["after_close"]["changed"] is True
    assert d["after_close"]["difference"]["bills"] == 1
    assert d["after_close"]["difference"]["revenue_paise"] == 5000
    assert d["after_close"]["derived_now"]["revenue_paise"] == 7145


def test_a_late_settlement_shows_up_beside_the_record_never_inside_it(client):
    _bill("s1", 2145)
    _closed_ok(client)
    frozen_settled = client.get(f"/daybook/{_today()}").json()
    assert frozen_settled["record"]["derived"]["settled_paise"] == 0

    # The webhook lands the next morning, for yesterday's bill.
    Ledger(manage.ledger_path()).append(
        ts=_at(_today(), 12, 2), module="session", event="webhook",
        session_id="s1", reason="settled_green",
        razorpay_event="payment.captured", event_id="evt_late",
        webhook_amount_paise=2145,
        **{"from": "AWAITING_SETTLEMENT", "to": "PAID"}, total_paise=2145)
    manage._CHAIN_CACHE.clear()

    d = client.get(f"/daybook/{_today()}").json()
    assert d["record"]["derived"]["settled_paise"] == 0
    assert d["after_close"]["difference"]["settled_paise"] == 2145
    assert "settled_paise" in d["after_close"]["changed_fields"]


def test_a_product_renamed_after_the_close_does_not_rename_itself_inside_it(client):
    _catalogue(parle="Parle-G 200g")
    _bill("s1", 2145, sku="parle")
    _closed_ok(client)
    _catalogue(parle="PARLE GLUCO BISCUIT")

    top = client.get(f"/daybook/{_today()}").json()["record"]["top_sellers"]
    assert top[0]["name"] == "Parle-G 200g"


def test_a_quiet_day_can_still_be_closed(client):
    out = _closed_ok(client, counted_cash_paise=0)
    assert out["record"]["derived"]["bills"] == 0
    assert out["record"]["derived"]["revenue_paise"] == 0
    assert out["record"]["counted_cash_paise"] == 0
    assert out["record"]["counted_cash_rupees"] == "0.00"


def test_closing_early_is_allowed_and_the_record_says_when(client):
    """Shops close early. The calendar does not get a vote; the record gets one."""
    _bill("s1", 2145)
    rec = _closed_ok(client)["record"]
    assert rec["day_had_ended"] is False
    assert rec["seconds_left_in_day_at_close"] > 0
    assert rec["closed_at"].endswith("+00:00")
    assert rec["closed_at_local"]


def test_closing_a_day_that_has_ended_records_that_it_had(client):
    _bill("s_old", 9999, day=_yesterday())
    rec = _closed_ok(client, day=_yesterday())["record"]
    assert rec["day_had_ended"] is True
    assert rec["seconds_left_in_day_at_close"] == 0


def test_preview_of_a_closed_day_says_so_with_the_moment(client):
    closed = _closed_ok(client)["record"]
    d = client.get("/daybook/preview").json()
    assert d["already_closed"] is True
    assert d["closed_at"] == closed["closed_at"]


# ================================================== the record cannot be edited


def test_the_chain_carries_a_digest_that_recomputes_from_the_file(client):
    _bill("s1", 2145)
    _closed_ok(client, note="counted twice")
    d = client.get(f"/daybook/{_today()}").json()
    assert d["record_unedited"] is True
    assert d["record_sha256_recomputed"] == d["record"]["record_sha256"]

    lines = [json.loads(x) for x in
             daybook.audit_path().read_text(encoding="utf-8").splitlines()]
    closed = [x for x in lines if x["event"] == "day.closed"]
    assert len(closed) == 1
    assert closed[0]["record_sha256"] == d["record"]["record_sha256"]


def test_editing_the_record_on_disk_is_visible(client):
    _bill("s1", 2145)
    _closed_ok(client)
    path = daybook.daybook_dir() / f"{_today()}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["counted_cash_paise"] = 999999
    path.write_text(json.dumps(doc), encoding="utf-8")

    d = client.get(f"/daybook/{_today()}").json()
    assert d["record_unedited"] is False
    assert d["record_sha256_recomputed"] != d["record"]["record_sha256"]


def test_the_days_own_chain_verifies_and_is_not_the_money_ledger(client):
    """Rule 6: our own chain file, under the shop dir, never results/audit.jsonl."""
    _bill("s1", 2145)
    _closed_ok(client, day=_today())
    _closed_ok(client, day=_yesterday())

    ok, n, head, err = verify(daybook.audit_path())
    assert ok is True and err is None
    assert n == 2
    assert daybook.audit_path() != manage.ledger_path()
    assert daybook.audit_path().parent == daybook.shop_dir()
    # The money chain was only ever appended to by the bill fixture.
    money = [json.loads(x) for x in
             manage.ledger_path().read_text(encoding="utf-8").splitlines()]
    assert all(rec["module"] == "session" for rec in money)


def test_the_chain_records_the_close_but_never_the_words(client):
    """An audit log is the file most likely to be pasted into a bug report."""
    secret_note = "short 200, Ramu took it for the gas cylinder"
    _closed_ok(client, note=secret_note, closed_by="Ramesh Kumar")
    raw = daybook.audit_path().read_text(encoding="utf-8")
    assert secret_note not in raw
    assert "Ramesh Kumar" not in raw
    line = json.loads(raw.splitlines()[0])
    assert line["note_len"] == len(secret_note)
    assert line["note_sha256"] == daybook._sha256(secret_note)
    assert line["closed_by_sha256"] == daybook._sha256("Ramesh Kumar")
    assert line["minted"] is False


# ============================================================== the refusals


def test_closing_twice_is_refused_by_name(client):
    _bill("s1", 2145)
    first = _closed_ok(client, counted_cash_paise=482000)
    r = client.post("/daybook/close", json={"counted_cash_paise": 111111})
    assert r.status_code == 400
    d = r.json()
    assert d["ok"] is False
    assert d["reason"] == R_ALREADY_CLOSED
    assert first["record"]["closed_at"] in d["detail"]


def test_the_refused_second_close_leaves_the_first_record_untouched(client):
    _closed_ok(client, counted_cash_paise=482000, note="first")
    client.post("/daybook/close",
                json={"counted_cash_paise": 111111, "note": "second"})
    rec = client.get(f"/daybook/{_today()}").json()["record"]
    assert rec["counted_cash_paise"] == 482000
    assert rec["note"] == "first"
    # And the chain shows one close, not two.
    lines = daybook.audit_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_a_day_that_was_never_closed_is_a_named_404(client):
    r = client.get(f"/daybook/{_yesterday()}")
    assert r.status_code == 404
    d = r.json()
    assert d["reason"] == R_NOT_CLOSED
    assert "/daybook/preview" in d["detail"]


def test_a_future_day_cannot_be_closed_or_previewed(client):
    r = client.post("/daybook/close",
                    json={"counted_cash_paise": 1, "day": _tomorrow()})
    assert r.status_code == 400
    assert r.json()["reason"] == R_DAY_IN_FUTURE
    assert client.get("/daybook/preview",
                      params={"day": _tomorrow()}).json()["reason"] == R_DAY_IN_FUTURE
    assert client.get(f"/daybook/{_tomorrow()}").json()["reason"] == R_DAY_IN_FUTURE


@pytest.mark.parametrize("day", ["yesterday", "2026-13-01", "2026-02-30",
                                 "01-09-2026", "2026-9-1", "../../catalog"])
def test_a_day_that_is_not_a_calendar_date_is_refused(client, day):
    r = client.post("/daybook/close",
                    json={"counted_cash_paise": 1, "day": day})
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_DAY


def test_a_day_is_shape_checked_before_it_is_joined_to_a_path():
    """The id becomes a filename, so the check has to be on the way IN."""
    with pytest.raises(daybook.DaybookRefused) as caught:
        daybook._valid_day("../../catalog")
    assert caught.value.reason == R_BAD_DAY


def test_a_day_that_is_not_a_string_is_refused(client):
    r = client.post("/daybook/close",
                    json={"counted_cash_paise": 1, "day": 20260901})
    assert r.json()["reason"] == R_BAD_DAY


def test_closing_with_no_count_of_the_drawer_is_refused(client):
    r = client.post("/daybook/close", json={"note": "forgot to count"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_NO_CASH
    assert client.get("/daybook").json()["count"] == 0


def test_counting_the_drawer_twice_is_refused_rather_than_resolved(client):
    r = client.post("/daybook/close", json={"counted_cash_paise": 482000,
                                            "counted_cash_rupees": "4820.00"})
    assert r.json()["reason"] == R_CASH_TWICE


@pytest.mark.parametrize("value", [4820.0, 4820.5, True, "482000", [482000]])
def test_a_count_that_is_not_whole_paise_is_refused(client, value):
    """4820.0 is refused too. It is exactly representable and it is still a
    float, and a path that accepts the ones that happen to be round is a path
    that accepts 4819.999999999999 on the day the browser computes it."""
    r = client.post("/daybook/close", json={"counted_cash_paise": value})
    assert r.json()["reason"] == R_CASH_NOT_INTEGER
    assert client.get("/daybook").json()["count"] == 0


@pytest.mark.parametrize("value", ["4820.345", "four thousand", "", "12,000"])
def test_a_rupee_string_that_is_not_money_is_refused(client, value):
    r = client.post("/daybook/close", json={"counted_cash_rupees": value})
    assert r.json()["reason"] == R_BAD_RUPEES


def test_a_rupee_string_becomes_integer_paise_without_a_float(client):
    assert _closed_ok(client, counted_cash_rupees="4820.5",
                      day=_yesterday())["record"]["counted_cash_paise"] == 482050
    assert _closed_ok(client, counted_cash_rupees="4820",
                      day=_today())["record"]["counted_cash_paise"] == 482000


def test_a_negative_count_is_refused_and_zero_is_not(client):
    assert client.post("/daybook/close", json={"counted_cash_paise": -1}
                       ).json()["reason"] == R_CASH_NEGATIVE
    assert _closed_ok(client, counted_cash_paise=0)["record"][
        "counted_cash_paise"] == 0


def test_a_count_past_the_ceiling_is_refused_by_name_never_clamped(client):
    r = client.post("/daybook/close",
                    json={"counted_cash_paise": MAX_COUNTED_CASH_PAISE + 1})
    assert r.json()["reason"] == R_CASH_TOO_LARGE
    assert client.get("/daybook").json()["count"] == 0


def test_a_note_past_the_cap_is_refused_and_nothing_is_closed(client):
    r = client.post("/daybook/close", json={"counted_cash_paise": 1,
                                            "note": "x" * (MAX_NOTE + 1)})
    assert r.json()["reason"] == R_NOTE_TOO_LONG
    assert client.get("/daybook").json()["count"] == 0


def test_a_closed_by_past_the_cap_is_refused(client):
    r = client.post("/daybook/close",
                    json={"counted_cash_paise": 1,
                          "closed_by": "y" * (MAX_CLOSED_BY + 1)})
    assert r.json()["reason"] == R_CLOSED_BY_TOO_LONG


@pytest.mark.parametrize("body", ["not json at all", "[1, 2, 3]", '"a string"'])
def test_a_body_that_is_not_a_json_object_is_refused(client, body):
    r = client.post("/daybook/close", content=body,
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_BODY


def test_a_note_that_is_not_text_is_refused(client):
    r = client.post("/daybook/close",
                    json={"counted_cash_paise": 1, "note": 5})
    assert r.json()["reason"] == R_BAD_BODY


@pytest.mark.parametrize("limit", ["0", "-3", "many", "1.5"])
def test_a_limit_that_is_not_a_positive_whole_number_is_refused(client, limit):
    r = client.get("/daybook", params={"limit": limit})
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_LIMIT


def test_a_close_that_cannot_reach_the_disk_is_refused_and_retracted(client):
    """The chain must not be left saying a day was closed when it was not."""
    blocker = daybook.shop_dir() / "daybook"
    blocker.write_text("this is a file where a directory should be")

    r = client.post("/daybook/close", json={"counted_cash_paise": 482000})
    assert r.status_code == 400
    assert r.json()["reason"] == R_NOT_WRITTEN

    events = [json.loads(x)["event"] for x in
              daybook.audit_path().read_text(encoding="utf-8").splitlines()]
    assert events == ["day.closed", "day.close_not_written"]
    ok, _, _, err = verify(daybook.audit_path())
    assert ok is True and err is None


def test_a_missing_till_is_a_named_refusal_not_a_crash(client, monkeypatch):
    def boom():
        raise ImportError("no module named tools.upload_app")

    monkeypatch.setattr(daybook, "_TILL_NAMES", ())
    monkeypatch.setattr(daybook, "_import_till", boom)
    r = client.get("/daybook")
    assert r.status_code == 400
    assert r.json()["reason"] == R_NO_TILL


def test_a_missing_day_brief_is_a_named_refusal_not_a_crash(client, monkeypatch):
    def boom():
        raise ImportError("gawaah.manage is not importable")

    monkeypatch.setattr(daybook, "_import_manage", boom)
    r = client.post("/daybook/close", json={"counted_cash_paise": 1})
    assert r.status_code == 400
    d = r.json()
    assert d["reason"] == R_NO_BILL_BOOK
    assert client.get("/daybook/preview").json()["reason"] == R_NO_BILL_BOOK


def test_a_renamed_derivation_in_manage_is_named_rather_than_an_attributeerror(
        client, monkeypatch):
    """The one real cost of depending on a private name, made loud."""
    import types

    stub = types.SimpleNamespace(
        read_chain=manage.read_chain,
        bills_from=manage.bills_from,
        _local_day_bounds=manage._local_day_bounds,
    )
    monkeypatch.setattr(daybook, "_import_manage", lambda: stub)
    r = client.post("/daybook/close", json={"counted_cash_paise": 1})
    assert r.status_code == 400
    assert r.json()["reason"] == R_NO_BILL_BOOK
    assert "_brief_for" in r.json()["detail"]


def test_an_unexpected_failure_is_a_named_400_and_never_a_500(client, monkeypatch):
    def boom():
        raise ZeroDivisionError("something nobody predicted")

    monkeypatch.setattr(daybook, "_all_closed", boom)
    r = client.get("/daybook")
    assert r.status_code == 400
    assert r.json()["reason"] == R_INTERNAL
    assert "ZeroDivisionError" in r.json()["detail"]


# ================================================================== the list


def test_closed_days_are_listed_newest_first(client):
    tz = datetime.now().astimezone().tzinfo
    days = [(datetime.now(tz) - timedelta(days=n)).strftime("%Y-%m-%d")
            for n in (2, 1, 0)]
    for n, day in enumerate(days):
        _closed_ok(client, day=day, counted_cash_paise=1000 + n)
    rows = client.get("/daybook").json()["days"]
    assert [r["day"] for r in rows] == list(reversed(days))


def test_the_list_row_carries_the_frozen_figures_not_a_recount(client):
    _bill("s1", 2145, settle=True)
    _closed_ok(client, counted_cash_rupees="4820.00", closed_by="Ramesh")
    _bill("s_after", 5000, hour=21)

    row = client.get("/daybook").json()["days"][0]
    assert row["bills"] == 1
    assert row["revenue_paise"] == 2145
    assert row["settled_paise"] == 2145
    assert row["counted_cash_paise"] == 482000
    assert row["counted_cash_rupees"] == "4820.00"
    assert row["closed_by"] == "Ramesh"
    assert row["chain_verified_at_close"] is True


def test_the_list_is_capped_and_says_when_it_capped(client):
    tz = datetime.now().astimezone().tzinfo
    for n in range(4):
        _closed_ok(client, counted_cash_paise=1,
                   day=(datetime.now(tz) - timedelta(days=n)).strftime("%Y-%m-%d"))
    d = client.get("/daybook", params={"limit": "2"}).json()
    assert d["count"] == 2
    assert d["days_on_record"] == 4
    assert d["truncated"] is True
    assert d["limit"] == 2
    assert client.get("/daybook").json()["truncated"] is False


def test_an_unreadable_file_in_the_daybook_does_not_hide_the_rest(client):
    _closed_ok(client, counted_cash_paise=1)
    (daybook.daybook_dir() / f"{_yesterday()}.json").write_text("{ broken")
    (daybook.daybook_dir() / "notes.txt").write_text("ignore me")
    d = client.get("/daybook").json()
    assert d["count"] == 1
    assert d["days"][0]["day"] == _today()


def test_an_empty_daybook_is_an_empty_list_not_an_error(client):
    d = client.get("/daybook").json()
    assert d["ok"] is True
    assert d["count"] == 0
    assert d["days"] == []


# ========================================================== the broken chain


def _break_the_chain() -> None:
    """Append a line whose prev_hash does not follow. verify() stops there."""
    with manage.ledger_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": _at(_today(), 20), "module": "session", "event": "done",
            "session_id": "s_after_the_break", "reason": "intent_requested",
            "lines": 1, "total_paise": 99999,
            "prev_hash": "0" * 64, "hash": "d" * 64}) + "\n")
    manage._CHAIN_CACHE.clear()


def test_a_broken_chain_is_named_in_the_preview_and_nothing_is_adjusted(client):
    _bill("s1", 2145)
    _break_the_chain()
    d = client.get("/daybook/preview").json()
    assert d["chain"]["ok"] is False
    assert "does not verify past line" in d["chain_warning"]
    assert "read LOW" in d["chain_warning"]
    # The bill on the far side of the break is absent, not approximated.
    assert d["derived"]["revenue_paise"] == 2145


def test_a_day_closed_over_a_broken_chain_records_that_it_was(client):
    """Refusing would lock the shop out of closing forever. Saying so does not."""
    _bill("s1", 2145)
    _break_the_chain()
    rec = _closed_ok(client)["record"]
    assert rec["chain_at_close"]["ok"] is False
    assert rec["chain_warning_at_close"]
    assert client.get(f"/daybook/{_today()}").json()[
        "record"]["chain_warning_at_close"]


# ================================================== what it is not allowed to do


def test_nothing_this_module_answers_ever_settles_money(client):
    _closed_ok(client, counted_cash_paise=1)
    for r in (client.get("/daybook"),
              client.get("/daybook/preview"),
              client.get(f"/daybook/{_today()}"),
              client.post("/daybook/close", json={"counted_cash_paise": 1})):
        assert r.json()["settles_money"] is False


def test_the_module_never_reaches_for_the_money_service(client):
    """A shopkeeper must be able to close the day with paisa stopped.

    Asserted against the shipped source rather than by mocking, because the
    claim is that there is no code path at all — not that today's path happens
    not to be taken.
    """
    import ast

    tree = ast.parse(Path(daybook.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    strings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.append(node.value.lower())

    # No network client of any kind is reachable from this module.
    assert imported.isdisjoint(
        {"urllib", "http", "socket", "requests", "httpx", "aiohttp"}), imported

    # INVARIANT 6. No payable string is constructed here, and there is no
    # template one could be built from. Checked over string LITERALS rather than
    # the whole file so the prose that explains the rule does not trip it.
    for text in strings:
        for forbidden in ("upi:", "razorpay.com", "rzp.io", "pay?", "://"):
            assert forbidden not in text, (forbidden, text[:80])


def test_the_close_out_never_computes_a_difference_against_the_drawer(client):
    """/cash reconciles the drawer. A second, wrong reconciliation here would
    look like an answer, so the record says why it is not one."""
    _bill("s1", 2145)
    out = _closed_ok(client, counted_cash_paise=482000)
    assert "difference_paise" not in out["record"]
    assert "expected_closing_paise" not in out["record"]
    # THE NOTE POINTS AT A SCREEN, NOT AT A ROUTE. It used to send the reader to
    # "/cash", which is a JSON endpoint in gawaah/expenses.py with no page
    # behind it and no sidebar entry to find instead; a shopkeeper typing that
    # into the address bar gets a wall of JSON. The drawer is drawn on the
    # Expenses screen. The assertion is on the SIGNPOST, because the failure
    # this test protects against is the note quietly losing the pointer to
    # wherever the real reconciliation is.
    assert "Expenses screen" in out["note"]
    assert "/cash" not in out["note"]


# ------------------------------------------------------- the reconciliation --
#
# `/daybook/reconcile` exists because `/manage/today` counts BILLS and cannot
# answer the questions a shopkeeper loses money to, all of which are about a gap
# between two books. Each test below is one of those gaps, written as the chain
# actually records it.
#
# The RULE these tests defend, and the reason the endpoint is worth having: NO
# FIGURE IS NETTED AGAINST ANOTHER. A bill with no link, a bill the counter
# refused and a bill waiting on a link are three different problems, and the
# moment they are added together the disagreement stops being sayable.


def _mint(session_id: str, *, day: str | None = None, hour: int = 12,
          second: int = 0) -> None:
    """paisa's own line saying a payment link was issued for this session."""
    Ledger(manage.ledger_path()).append(
        ts=_at(day or _today(), hour, second), module="paisa",
        event="intent.minted", session_id=session_id,
        payment_link_id=f"plink_{session_id}", minted=True)
    manage._CHAIN_CACHE.clear()


def _webhook_post(reason: str, *, day: str | None = None, hour: int = 12,
                  second: int = 0) -> None:
    """One POST as `gawaah/webhook.py` records it — BEFORE anything decides
    whether to believe it. A post whose signature fails never reaches a session,
    so this line is the only place in the program it exists."""
    Ledger(manage.ledger_path()).append(
        ts=_at(day or _today(), hour, second), module="webhook",
        event=None, reason=reason, minted=False)
    manage._CHAIN_CACHE.clear()


def _reconcile(client: TestClient, day: str | None = None) -> dict:
    r = client.get("/daybook/reconcile" + (f"?day={day}" if day else ""))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["settles_money"] is False
    return body


def test_the_five_states_are_disjoint_and_add_back_up_to_what_was_billed(client):
    """The split is checkable by addition, which is the point of splitting it.

    A reader who cannot add the parts back to the whole has been shown five
    numbers and asked to trust them.
    """
    _bill("settled_one", 1000, settle=True)
    _mint("settled_one")
    _bill("waiting_one", 2000)
    _mint("waiting_one")
    _bill("no_link_one", 500)          # closed, and never minted

    d = _reconcile(client)["today"]
    assert d["billed"] == {"bills": 3, "paise": 3500, "rupees": "35.00"}
    parts = ("settled", "settled_unwitnessed", "refused", "never_asked", "awaiting")
    assert sum(d[p]["bills"] for p in parts) == d["billed"]["bills"]
    assert sum(d[p]["paise"] for p in parts) == d["billed"]["paise"]


def test_a_bill_with_no_payment_link_is_not_counted_as_awaiting_the_gateway(client):
    """The defect this endpoint was written for.

    `/manage/today` reports `awaiting_count` as `bills - settled`, and the Today
    screen printed that as "N links sent, not settled". A bill that closed with
    no link ever minted is in that count and no link was ever sent for it — it
    is not waiting on the gateway, because nothing was ever asked of the
    gateway. Measured on the live counter when this was found: 238 bills closed,
    233 links minted, 5 bills in the awaiting figure that had no link at all.
    """
    _bill("has_link", 2000)
    _mint("has_link")
    _bill("no_link", 500)

    d = _reconcile(client)["today"]
    assert d["awaiting"] == {"bills": 1, "paise": 2000, "rupees": "20.00"}
    assert d["never_asked"] == {"bills": 1, "paise": 500, "rupees": "5.00"}
    # Both are owed. The split is about WHY, never about how much.
    assert d["owed"]["paise"] == 2500
    codes = {x["code"] for x in d["disagreements"]}
    assert "bills_the_gateway_was_never_asked_for" in codes


def test_a_webhook_that_arrived_and_was_refused_is_reported(client):
    """The loudest thing a counter can say, and it was nowhere on the books.

    `paisa /health` counts webhooks the CURRENT PROCESS has seen, so a money
    service restarted an hour ago reports none while the chain holds a dozen.
    The Today screen printed "No webhook has reached this counter" over exactly
    that. A run of bad-signature posts is somebody sending this counter
    webhooks it cannot trust, and it must never be invisible.
    """
    _bill("s1", 1000)
    _mint("s1")
    _webhook_post("bad_signature", second=1)
    _webhook_post("bad_signature", second=2)
    _webhook_post("unknown_session", second=3)

    d = _reconcile(client)["today"]
    assert d["events"]["webhooks_received"] == 3
    assert d["events"]["webhooks_green"] == 0
    assert d["events"]["webhooks_refused"] == {"bad_signature": 2, "unknown_session": 1}
    say = [x for x in d["disagreements"]
           if x["code"] == "webhooks_arrived_and_were_refused"]
    assert len(say) == 1
    assert say[0]["count"] == 3
    # The reason names are in the sentence, because "3 webhooks refused" and
    # "3 webhooks with a bad signature" are different problems to go and fix.
    assert "bad signature" in say[0]["detail"]


def test_a_settlement_with_no_webhook_line_is_never_added_to_the_settled_figure(client):
    """INVARIANT 2, stated as arithmetic.

    `bills_from` accepts `kernel/intent.settled` as a fallback and labels it
    `settled_by: kernel`. That money may well have arrived — but only a
    signature-verified webhook may say so on this counter, so it is counted
    apart, reported by name, and left in what is owed. Promoting it silently is
    the one thing this whole product exists not to do.
    """
    _bill("witnessed", 1000, settle=True)
    _mint("witnessed")
    _bill("kernel_only", 4000)
    _mint("kernel_only")
    Ledger(manage.ledger_path()).append(
        ts=_at(_today(), 12, 30), module="kernel", event="intent.settled",
        session_id="kernel_only", payment_id="pay_x", minted=False)
    manage._CHAIN_CACHE.clear()

    d = _reconcile(client)["today"]
    assert d["settled"] == {"bills": 1, "paise": 1000, "rupees": "10.00"}
    assert d["settled_unwitnessed"] == {"bills": 1, "paise": 4000, "rupees": "40.00"}
    assert d["owed"]["paise"] == 4000, "unwitnessed money is still owed"
    codes = {x["code"] for x in d["disagreements"]}
    assert "settled_without_a_webhook_line" in codes


def _refuse(session_id: str, *, reason: str = "amount_disagreement",
            day: str | None = None, hour: int = 12, second: int = 5) -> None:
    """paisa's own line saying it declined to mint for this session."""
    Ledger(manage.ledger_path()).append(
        ts=_at(day or _today(), hour, second), module="paisa",
        event="intent.refused", session_id=session_id, reason=reason,
        minted=False)
    manage._CHAIN_CACHE.clear()


def test_a_refusal_the_counter_then_minted_a_link_for_is_awaited_not_refused(client):
    """A refusal is an event on a session, not a state of it.

    The money service re-prices and declines; the counter re-asks and gets a
    link. `bills_from` keeps BOTH lines on the bill, so testing `refusals`
    before `minted` filed a basket with a live payment link under "the counter
    refused to charge" and printed "money that did not move" over money the
    customer could be paying at that moment — while taking it out of the awaited
    figure the shopkeeper chases. Retries are not rare here: 95 of 422 sessions
    on the live chain have had a link minted more than once.

    The bill with no link after its refusal is the one the sentence is true of,
    and it stays where it was.
    """
    _bill("retried", 3000, hour=9)
    _refuse("retried", hour=9, second=2)
    _mint("retried", hour=9, second=3)          # the retry got a link
    _bill("declined", 700, hour=10)
    _refuse("declined", hour=10, second=2)      # and nothing after it

    d = _reconcile(client)["today"]
    assert d["awaiting"] == {"bills": 1, "paise": 3000, "rupees": "30.00"}
    assert d["refused"] == {"bills": 1, "paise": 700, "rupees": "7.00"}
    assert d["never_asked"]["bills"] == 0, "both were asked of the gateway"
    # Still disjoint, still adds back up — the fix moved a bill, not a total.
    parts = ("settled", "settled_unwitnessed", "refused", "never_asked", "awaiting")
    assert sum(d[p]["paise"] for p in parts) == d["billed"]["paise"] == 3700
    said = {x["code"]: x for x in d["disagreements"]}
    assert said["the_counter_refused_to_charge"]["count"] == 1
    assert "no link was minted for these afterwards" \
        in said["the_counter_refused_to_charge"]["detail"].lower()


def test_the_channel_split_names_the_till_and_the_storefront_and_guesses_neither(client):
    """Where a bill was rung up, from the id the writing module chose.

    `gawaah/storefront.py` writes `session_id = f"shop_{order_id}"` and the till
    writes `till_<...>`. An id that is neither is reported as `unnamed` rather
    than being pushed into whichever bucket is closer — a probe, a demo session
    and a hand-written id all land there, and filing them under "till" would be
    inventing a channel the chain never recorded.
    """
    _bill("till_abc", 1000)
    _bill("shop_ord_0123456789ab", 2000)
    _bill("counter_live_9", 500)

    d = _reconcile(client)["today"]
    assert set(d["by_channel"]) == {"till", "storefront", "unnamed"}
    assert d["by_channel"]["till"]["billed"]["paise"] == 1000
    assert d["by_channel"]["storefront"]["billed"]["paise"] == 2000
    assert d["by_channel"]["unnamed"]["billed"]["paise"] == 500
    assert sum(v["billed"]["paise"] for v in d["by_channel"].values()) \
        == d["billed"]["paise"]


def test_lifetime_is_reported_beside_the_day_and_is_not_the_same_question(client):
    """"Nothing settled today" and "nothing has ever settled here" are different
    shops, and a day-shaped answer cannot tell them apart."""
    _bill("old", 5000, day=_yesterday(), settle=True)
    _mint("old", day=_yesterday())
    _bill("new", 1000)
    _mint("new")

    body = _reconcile(client)
    assert body["today"]["billed"]["paise"] == 1000
    assert body["today"]["settled"]["paise"] == 0
    assert body["lifetime"]["billed"]["paise"] == 6000
    assert body["lifetime"]["settled"]["paise"] == 5000
    assert body["lifetime"]["owed"]["paise"] == 1000


def test_an_empty_day_disagrees_about_nothing_and_invents_no_figure(client):
    """A quiet morning is not a clean bill of health, and it is not zeros.

    Every bucket is genuinely zero here, which is the honest answer to "how much
    was billed"; what matters is that NO disagreement is reported, so a screen
    can tell "nothing has been checked" from "the books agree".
    """
    d = _reconcile(client)["today"]
    assert d["billed"] == {"bills": 0, "paise": 0, "rupees": "0.00"}
    assert d["disagreements"] == []
    assert d["by_channel"] == {}
    assert d["events"]["webhooks_received"] == 0


def test_the_reconciliation_reads_one_chain_and_never_calls_the_money_service(client):
    """This module promises a day can be closed with the payment process
    stopped. The gateway's side of the story is already in the chain — paisa
    writes its own lines there — so no request is made to get it."""
    _bill("s1", 1000)
    _mint("s1")
    body = _reconcile(client)
    assert "read_chain" in body["derived_from"]
    assert "money service is not called" in body["derived_from"]
    # The window is the counter's own calendar day, not UTC and not the
    # gateway's: a bill counts on the day it closed here.
    assert body["window"]["from"].startswith(_today())


def test_every_named_refusal_in_the_module_is_exercised_here():
    """A new refusal without a test is a refusal nobody has ever seen fire."""
    named = {n for n in dir(daybook)
             if n.startswith("R_") and isinstance(getattr(daybook, n), str)}
    mine = Path(__file__).read_text(encoding="utf-8")
    unexercised = sorted(n for n in named if mine.count(n) < 2)
    assert unexercised == []
