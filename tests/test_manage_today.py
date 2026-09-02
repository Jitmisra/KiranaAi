"""The day brief: "aaj kitna hua?", answered from the chain and nowhere else.

The product held every number a shopkeeper asks for at the end of a shift and
never answered the question. These pin the answering: derived per calendar day
in the counter's own timezone, both sides of every comparison computed the same
way, and nothing estimated.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gawaah import manage  # noqa: E402
from gawaah.ledger import Ledger  # noqa: E402


def _tz():
    return datetime.now().astimezone().tzinfo


def _stamp(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


@pytest.fixture
def shop(tmp_path, monkeypatch):
    """A scratch shop whose chain holds two bills today and one yesterday.

    TWO environment knobs, not one. The catalogue lives under GAWAAH_SHOP_DIR
    but the chain lives under GAWAAH_DATA_DIR — the same split the three real
    processes share. Setting only the first pointed these tests' reads at the
    LIVE chain in results/, and the first run asserted 30 == 2 against the
    day's real trading.
    """
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "shop"))
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "catalog.json").write_text(json.dumps(
        {"format": 2, "dim": 4, "sha256": "", "gates": {},
         "skus": {"parle": {"name": "Parle-G", "price_paise": 1000,
                            "vectors": [[0, 0, 0, 1]], "footprint_mm": 95.0}}}))

    led = Ledger(tmp_path / "shop" / "audit.jsonl")
    now = datetime.now(_tz()).replace(hour=12, minute=0, second=0, microsecond=0)

    def bill(session: str, at: datetime, total: int, *, settle: bool) -> None:
        led.append(ts=_stamp(at), module="session", event="classify",
                   session_id=session, item_id="parle#0", from_state="MEASURING")
        led.append(ts=_stamp(at), module="session", event="exit",
                   session_id=session, item_id="parle#0",
                   reason="exit_crossing_committed", price_paise=total)
        led.append(ts=_stamp(at), module="session", event="done",
                   session_id=session, from_state="BASKET_OPEN",
                   total_paise=total, intent_amount_paise=total, lines=1)
        if settle:
            led.append(ts=_stamp(at + timedelta(seconds=40)), module="session",
                       event="webhook", session_id=session, from_state="PAID",
                       reason="settled_green", payment_id="pay_x")

    bill("s_today_1", now, 1000, settle=True)
    bill("s_today_2", now + timedelta(hours=1), 1000, settle=False)
    bill("s_yesterday", now - timedelta(days=1), 1000, settle=False)

    # The ledger holds the file lock for the process; today_ep re-reads by path.
    app = FastAPI()
    app.include_router(manage.router)
    return TestClient(app)


def test_today_counts_only_today(shop):
    d = shop.get("/manage/today").json()
    assert d["ok"] is True
    assert d["today"]["bills"] == 2
    assert d["today"]["revenue_paise"] == 2000
    assert d["yesterday"]["bills"] == 1


def test_the_comparison_is_computed_not_cached(shop):
    """Yesterday's figure must come out of the same derivation, asked about a
    different window — a delta between two differently-derived numbers is a
    random number with a percent sign."""
    d = shop.get("/manage/today").json()
    y = datetime.now(_tz()) - timedelta(days=1)
    direct = shop.get(f"/manage/today?day={y.strftime('%Y-%m-%d')}").json()
    assert direct["today"]["bills"] == d["yesterday"]["bills"]
    assert direct["today"]["revenue_paise"] == d["yesterday"]["revenue_paise"]


def test_settled_and_awaiting_split_the_revenue_exactly(shop):
    t = shop.get("/manage/today").json()["today"]
    assert t["settled_count"] == 1
    assert t["awaiting_count"] == 1
    assert t["settled_paise"] + t["awaiting_paise"] == t["revenue_paise"]


def test_top_sellers_come_from_the_lines(shop):
    d = shop.get("/manage/today").json()
    assert d["top_sellers"], "two bills of Parle-G is a top seller"
    top = d["top_sellers"][0]
    assert top["sku_id"] == "parle"
    assert top["name"] == "Parle-G"
    assert top["units"] == 2
    assert top["revenue_paise"] == 2000


def test_the_average_is_integer_paise_and_the_floor(shop):
    """An average describes; it is still money-shaped and may not invent a
    fraction of a paisa. 2000 / 2 = 1000 here; the floor rule is what a bill of
    999 would exercise."""
    t = shop.get("/manage/today").json()["today"]
    assert t["average_paise"] == 1000
    assert isinstance(t["average_paise"], int)


def test_a_day_with_nothing_says_zero_not_error(shop):
    old = shop.get("/manage/today?day=2020-01-01").json()
    assert old["ok"] is True
    assert old["today"]["bills"] == 0
    assert old["today"]["revenue_paise"] == 0
    assert old["top_sellers"] == []
    assert old["today"]["first_bill_at"] is None


def test_a_malformed_day_is_refused_by_name(shop):
    r = shop.get("/manage/today?day=yesterday")
    assert r.status_code == 400
    assert r.json()["reason"] == "day_malformed"


def test_nothing_here_settles_money(shop):
    assert shop.get("/manage/today").json()["settles_money"] is False


def test_an_absent_chain_is_an_empty_day_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "empty"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    app = FastAPI()
    app.include_router(manage.router)
    d = TestClient(app).get("/manage/today").json()
    assert d["ok"] is True
    assert d["today"]["bills"] == 0
