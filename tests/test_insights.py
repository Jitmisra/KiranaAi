"""NAZAR — the insights screen, and the numbers it refuses to print.

Two kinds of test here and they are not the same kind of claim:

  * that a figure is CORRECT — asserted against a chain this file wrote packet
    by packet, so the expected number is arithmetic on the fixture and not a
    re-run of the code under test;
  * that a figure is ABSENT — the harder and more important half. A screen that
    prints a week-over-week change against a week the counter did not exist for
    is worse than a screen that prints nothing, so "not enough history yet" is
    pinned as tightly as any total.

Every fixture sets BOTH GAWAAH_SHOP_DIR and GAWAAH_DATA_DIR. Setting only the
first points the reads at the live chain in results/, and the first version of
tests/test_manage_today.py asserted against a real trading day before anyone
noticed.
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

from gawaah import insights  # noqa: E402
from gawaah.ledger import Ledger  # noqa: E402


# ------------------------------------------------------------------ helpers --

def _tz():
    return datetime.now().astimezone().tzinfo


def _stamp(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def _midnight(offset_days: int = 0) -> datetime:
    """Local midnight, `offset_days` back from today."""
    now = datetime.now(_tz())
    return (now.replace(hour=0, minute=0, second=0, microsecond=0)
            - timedelta(days=offset_days))


class Shop:
    """A scratch counter whose only history is the bills this class wrote."""

    def __init__(self, tmp_path: Path) -> None:
        self.dir = tmp_path / "shop"
        self.dir.mkdir(exist_ok=True)
        self.led = Ledger(self.dir / "audit.jsonl")
        self.n = 0
        self.catalogue({"parle": ("Parle-G", 1000), "pepsi": ("Pepsi", 4000)})

    def catalogue(self, skus: dict[str, tuple[str, int]]) -> None:
        (self.dir / "catalog.json").write_text(json.dumps({
            "format": 2, "dim": 4, "sha256": "", "gates": {},
            "skus": {sku: {"name": name, "price_paise": price,
                           "vectors": [[0, 0, 0, 1]], "footprint_mm": 95.0}
                     for sku, (name, price) in skus.items()},
        }))

    def bill(self, at: datetime, lines: list[tuple[str, int]], *,
             settle: bool = False, amber: int = 0) -> str:
        """One closed bill of `lines` — (sku, price_paise) each."""
        self.n += 1
        session = f"s_{self.n:04d}"
        total = 0
        for i, (sku, price) in enumerate(lines):
            self.led.append(ts=_stamp(at), module="session", event="exit",
                            session_id=session, item_id=f"{sku}#{i}",
                            reason="exit_crossing_committed", price_paise=price)
            total += price
        for j in range(amber):
            self.led.append(
                ts=_stamp(at), module="session", event="exit",
                session_id=session, item_id=f"unknown#{j}",
                reason="exit_crossing_committed_amber_excluded",
                excluded_from_total=True, abstained=True)
        self.led.append(ts=_stamp(at), module="session", event="done",
                        session_id=session, from_state="BASKET_OPEN",
                        total_paise=total, intent_amount_paise=total,
                        lines=len(lines), amber_excluded=amber)
        if settle:
            self.led.append(ts=_stamp(at + timedelta(seconds=40)),
                            module="session", event="webhook",
                            session_id=session, from_state="PAID", to="PAID",
                            reason="settled_green", payment_id="pay_x")
        return session


@pytest.fixture
def empty(tmp_path, monkeypatch):
    """A counter installed this morning: a catalogue, and no bills at all."""
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "shop"))
    Shop(tmp_path)
    app = FastAPI()
    app.include_router(insights.router)
    return TestClient(app)


@pytest.fixture
def young(tmp_path, monkeypatch):
    """Three days old. Enough to trade, nowhere near enough to compare."""
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "shop"))
    shop = Shop(tmp_path)
    for back in (2, 1, 0):
        shop.bill(_midnight(back) + timedelta(hours=10), [("parle", 1000)])
    app = FastAPI()
    app.include_router(insights.router)
    return TestClient(app)


@pytest.fixture
def grown(tmp_path, monkeypatch):
    """Forty days of steady trading, with three things planted in it.

    The plants are the whole point and every expected number below is arithmetic
    on THIS list, not a second run of the code under test:

      * every day from 40 days ago to yesterday takes exactly ₹100 at 10:00 and
        ₹100 at 18:00 — two bills, 20000 paise;
      * day -9 is a festival: an extra ₹500 at 18:00, so 70000 paise;
      * day -1, YESTERDAY, has no 18:00 bill at all, so its evening hour is a
        hole against a baseline that is otherwise identical every day. It is
        yesterday deliberately: the hour scan reads the most recent COMPLETE
        trading day, because an hour that is still running has not finished
        taking money;
      * the last seven days each carry one extra ₹40 Pepsi at 11:00, so Pepsi
        is rising and the week is up by a fixed, checkable amount.
    """
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "shop"))
    shop = Shop(tmp_path)
    for back in range(40, 0, -1):
        base = _midnight(back)
        shop.bill(base + timedelta(hours=10), [("parle", 10000)], settle=True)
        if back == 9:
            shop.bill(base + timedelta(hours=18), [("parle", 60000)])
        elif back != 1:
            shop.bill(base + timedelta(hours=18), [("parle", 10000)])
        if back <= 7:
            shop.bill(base + timedelta(hours=11), [("pepsi", 4000)])
    app = FastAPI()
    app.include_router(insights.router)
    return TestClient(app)


def _get(client, path, **params):
    return client.get(path, params=params).json()


# ------------------------------------------------------- shape and refusals --

def test_router_paths_are_absolute_and_unprefixed():
    """The orchestrator mounts this with include_router and no prefix."""
    paths = {r.path for r in insights.router.routes}
    assert paths == {
        "/insights", "/insights/days", "/insights/week", "/insights/weekday",
        "/insights/hours", "/insights/products", "/insights/anomalies",
    }


def test_nothing_here_settles_money(grown):
    for path in ("/insights", "/insights/days", "/insights/week",
                 "/insights/weekday", "/insights/hours", "/insights/products",
                 "/insights/anomalies"):
        assert _get(grown, path)["settles_money"] is False, path


def test_refuses_days_that_is_not_a_number(grown):
    r = grown.get("/insights", params={"days": "thirty"})
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False
    assert body["reason"] == insights.R_BAD_DAYS
    assert "thirty" in body["detail"]


def test_refuses_a_window_shorter_than_two_weeks(grown):
    """R_DAYS_TOO_FEW. Seven days cannot carry a seven-against-seven."""
    r = grown.get("/insights", params={"days": 14})
    assert r.status_code == 400
    assert r.json()["reason"] == insights.R_DAYS_TOO_FEW
    assert str(insights.MIN_WINDOW_DAYS) in r.json()["detail"]


def test_refuses_a_window_past_a_year(grown):
    r = grown.get("/insights", params={"days": 400})
    assert r.status_code == 400
    assert r.json()["reason"] == insights.R_DAYS_TOO_MANY


def test_a_window_is_refused_not_clamped(grown):
    """A clamped window would answer a different question under the same label."""
    body = grown.get("/insights", params={"days": 400}).json()
    assert "window" not in body


def test_refuses_a_malformed_day(grown):
    r = grown.get("/insights", params={"day": "yesterday"})
    assert r.status_code == 400
    assert r.json()["reason"] == insights.R_BAD_DAY


def test_refuses_a_day_that_is_not_on_the_calendar(grown):
    """'2026-02-31' matches the shape and is not a date. Different sentence,
    same named refusal, because it is the same mistake to the shopkeeper."""
    r = grown.get("/insights", params={"day": "2026-02-31"})
    assert r.status_code == 400
    assert r.json()["reason"] == insights.R_BAD_DAY
    assert "month" in r.json()["detail"]


def test_refuses_a_day_that_has_not_started(grown):
    ahead = (_midnight() + timedelta(days=3)).strftime("%Y-%m-%d")
    r = grown.get("/insights", params={"day": ahead})
    assert r.status_code == 400
    assert r.json()["reason"] == insights.R_DAY_IN_FUTURE


def test_refuses_top_that_is_not_a_number(grown):
    r = grown.get("/insights/products", params={"top": "lots"})
    assert r.status_code == 400
    assert r.json()["reason"] == insights.R_BAD_TOP


def test_refuses_top_of_zero(grown):
    r = grown.get("/insights/products", params={"top": 0})
    assert r.status_code == 400
    assert r.json()["reason"] == insights.R_BAD_TOP


def test_refuses_top_past_the_ceiling(grown):
    r = grown.get("/insights/products", params={"top": 500})
    assert r.status_code == 400
    assert r.json()["reason"] == insights.R_TOP_TOO_MANY


def test_refuses_when_the_bill_book_has_moved(grown, monkeypatch):
    """R_NO_BILL_BOOK. If gawaah.manage stops exporting what this module folds,
    the screen says so by name instead of deriving a bill a second way."""
    from gawaah import manage
    monkeypatch.delattr(manage, "_brief_for")
    r = grown.get("/insights")
    assert r.status_code == 400
    body = r.json()
    assert body["reason"] == insights.R_NO_BILL_BOOK
    assert "_brief_for" in body["detail"]


def test_an_unreadable_chain_is_a_refusal_not_a_crash(grown, monkeypatch):
    """R_INTERNAL. Never a 500 — the exception type and message are the whole
    diagnosis on a screen derived from one file."""
    from gawaah import manage

    def boom():
        raise OSError("No such file or directory: audit.jsonl")

    monkeypatch.setattr(manage, "read_chain", boom)
    r = grown.get("/insights")
    assert r.status_code == 400
    body = r.json()
    assert body["reason"] == insights.R_INTERNAL
    assert "OSError" in body["detail"]


# --------------------------------------------------- not enough history yet --

def test_a_brand_new_counter_says_so_everywhere(empty):
    body = _get(empty, "/insights")
    assert body["ok"] is True
    assert body["history"]["closed_bills"] == 0
    assert body["history"]["days_spanned"] == 0
    for block in ("week", "same_weekday", "hours", "products", "anomalies"):
        assert body[block]["available"] is False, block
        assert body[block]["reason"] == insights.NOT_ENOUGH, block


def test_not_enough_history_carries_both_counts(young):
    """"Not enough history" with no numbers reads as a bug; with the counts it
    reads as a wait. The week counts COMPLETE days, and says which it counted:
    a three-day-old counter has two of them, today not being over."""
    week = _get(young, "/insights/week")["week"]
    assert week["available"] is False
    assert week["days_needed"] == insights.MIN_DAYS_FOR_WEEK
    assert week["days_of_history"] == 2
    assert week["counting"] == "complete days of history"
    assert "needs 14 complete days of history" in week["detail"]
    assert "has 2." in week["detail"]


def test_a_young_counter_still_gets_its_real_days(young):
    """The per-day series is a record, not a claim about a trend, so there is no
    minimum below which it is withheld."""
    days = _get(young, "/insights/days")["days"]
    assert days["available"] is True
    assert days["trading_days"] == 3
    assert days["total_paise"] == 3000
    assert days["baseline"]["available"] is False


def test_days_before_the_first_bill_are_gaps_not_zeros(young):
    """Twenty-seven days of a thirty-day window predate this counter. Counting
    them as zeros would drag every baseline to nothing."""
    body = _get(young, "/insights/days")
    series = body["days"]["series"]
    assert len(series) == 30
    assert sum(1 for d in series if d["no_history"]) == 27
    assert body["window"]["days_with_history"] == 3
    assert body["days"]["days_of_history"] == 3


def test_the_hour_profile_waits_for_seven_trading_days(young):
    hours = _get(young, "/insights/hours")["hours"]
    assert hours["available"] is False
    assert hours["days_needed"] == insights.MIN_TRADING_DAYS_FOR_HOURS


def test_one_previous_tuesday_is_not_a_baseline(tmp_path, monkeypatch):
    """MIN_SAME_WEEKDAY_SAMPLES. One prior same-weekday is an anecdote with a
    percentage on it, so no percentage is printed."""
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "shop"))
    shop = Shop(tmp_path)
    for back in (9, 7, 2, 0):
        shop.bill(_midnight(back) + timedelta(hours=9), [("parle", 5000)])
    app = FastAPI()
    app.include_router(insights.router)
    client = TestClient(app)
    block = _get(client, "/insights/weekday")["same_weekday"]
    assert block["available"] is False
    assert block["reason"] == insights.NOT_ENOUGH
    assert block["samples"] == 1
    assert "baseline_paise" not in block


# ------------------------------------------------------- the days themselves --

def test_the_series_is_oldest_first_and_the_right_length(grown):
    series = _get(grown, "/insights/days")["days"]["series"]
    assert len(series) == 30
    assert series[0]["date"] < series[-1]["date"]
    assert series[-1]["date"] == _midnight().strftime("%Y-%m-%d")


def test_an_ordinary_day_is_two_hundred_rupees(grown):
    """Arithmetic on the fixture: 10000 at 10:00 plus 10000 at 18:00."""
    series = _get(grown, "/insights/days")["days"]["series"]
    ordinary = [d for d in series
                if d["date"] == _midnight(20).strftime("%Y-%m-%d")][0]
    assert ordinary["bills"] == 2
    assert ordinary["revenue_paise"] == 20000
    assert ordinary["revenue_rupees"] == "200.00"


def test_the_median_day_carries_its_availability_flag(grown):
    """The success branch of a Block is as much a shape as the refusal branch.

    It shipped without `available` once. Every assertion in this file was still
    green — they all tested the refusing side — and the screen rendered
    "undefined of undefined" over a median it had been handed. Both sides of a
    union get pinned.
    """
    baseline = _get(grown, "/insights/days")["days"]["baseline"]
    assert baseline["available"] is True
    # Every fixture day is 20000 but for the festival and the missing evening.
    assert baseline["median_paise"] == 20000
    assert baseline["median_rupees"] == "200.00"
    assert "median" in baseline["method"]


def test_the_busiest_day_is_the_festival(grown):
    days = _get(grown, "/insights/days")["days"]
    assert days["busiest_complete_day"]["date"] == _midnight(9).strftime("%Y-%m-%d")
    assert days["busiest_complete_day"]["revenue_paise"] == 70000


def test_the_quietest_day_never_names_the_day_still_running(tmp_path, monkeypatch):
    """Today has taken one rupee and every finished day took a hundred, and today
    is still not "the quietest day" — at nine in the morning it would be the
    quietest day every single day. Both extremes read finished days only."""
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "shop"))
    shop = Shop(tmp_path)
    for back in range(20, 0, -1):
        amount = 5000 if back == 4 else 10000
        shop.bill(_midnight(back) + timedelta(hours=10), [("parle", amount)])
    shop.bill(_midnight() + timedelta(minutes=1), [("parle", 100)])
    app = FastAPI()
    app.include_router(insights.router)
    days = _get(TestClient(app), "/insights/days")["days"]

    today = _midnight().strftime("%Y-%m-%d")
    assert days["series"][-1]["date"] == today
    assert days["series"][-1]["revenue_paise"] == 100      # it IS in the series
    assert days["quietest_complete_day"]["date"] != today  # and not in this row
    assert days["quietest_complete_day"]["date"] == _midnight(4).strftime("%Y-%m-%d")
    assert days["quietest_complete_day"]["revenue_paise"] == 5000
    assert days["busiest_complete_day"]["date"] != today
    assert days["complete_trading_days"] == days["trading_days"] - 1
    assert "still running" in days["extremes_note"]


def test_the_settled_figure_is_separate_from_the_billed_one(grown):
    """Invariant 2. Only the 10:00 bill settles in the fixture, so the two
    numbers must differ and must never be added together."""
    days = _get(grown, "/insights/days")["days"]
    assert days["settled_paise"] < days["total_paise"]
    assert days["settled_paise"] > 0


def test_today_is_marked_incomplete(grown):
    series = _get(grown, "/insights/days")["days"]["series"]
    assert series[-1]["complete"] is False
    assert all(d["complete"] for d in series[:-1])


def test_a_past_day_is_a_complete_anchor(grown):
    day = _midnight(1).strftime("%Y-%m-%d")
    body = _get(grown, "/insights/days", day=day)
    assert body["window"]["to"] == day
    assert body["window"]["anchor_complete"] is True
    assert body["days"]["series"][-1]["date"] == day


# ------------------------------------------------------------ week on week --

def test_week_on_week_uses_complete_days_only(grown):
    """Today is half over. Putting it on one side of a seven-against-seven is
    the fastest way to print 'down 40%' about a normal morning."""
    week = _get(grown, "/insights/week")["week"]
    assert week["available"] is True
    assert week["complete_days_only"] is True
    assert week["this_week"]["to"] == _midnight(1).strftime("%Y-%m-%d")
    assert week["last_week"]["to"] == _midnight(8).strftime("%Y-%m-%d")


def test_week_on_week_delta_is_the_planted_pepsi(grown):
    """Days -7..-1 carry one extra 4000 Pepsi each; days -14..-8 carry a 60000
    festival on day -9. Both sides are arithmetic on the fixture."""
    week = _get(grown, "/insights/week")["week"]
    # Days -7..-2 are 20000 + a 4000 Pepsi; day -1 lost its 18:00 bill.
    assert week["this_week"]["revenue_paise"] == 6 * 24000 + 14000
    # Day -9 is the festival at 70000; the other six are ordinary.
    assert week["last_week"]["revenue_paise"] == 6 * 20000 + 70000
    assert week["delta_paise"] == week["this_week"]["revenue_paise"] \
        - week["last_week"]["revenue_paise"]
    assert week["delta_pct"] == (week["delta_paise"] * 100) // week["last_week"]["revenue_paise"]


def test_week_on_week_names_both_windows_in_its_sentence(grown):
    week = _get(grown, "/insights/week")["week"]
    assert week["this_week"]["revenue_rupees"] in week["sentence"]
    assert week["last_week"]["revenue_rupees"] in week["sentence"]
    assert week["direction"] in ("up", "down", "level")


# ------------------------------------------------------- the same weekday --

def test_the_same_weekday_compares_against_four_of_them(grown):
    block = _get(grown, "/insights/weekday")["same_weekday"]
    assert block["available"] is True
    assert block["samples"] == insights.SAME_WEEKDAY_LOOKBACK
    assert [p["weeks_ago"] for p in block["previous"]] == [1, 2, 3, 4]
    assert block["weekday"] in insights.WEEKDAYS


def test_the_same_weekday_is_cut_at_the_same_clock_time(grown):
    """THE ONE THAT MATTERS. Today is not over, so each previous same-weekday is
    measured only as far as today has run — and its full-day total is reported
    separately, never as the comparison."""
    block = _get(grown, "/insights/weekday")["same_weekday"]
    assert block["day_complete"] is False
    assert block["cut_seconds_into_day"] < 86_400
    now_hour = datetime.now(_tz()).hour
    for previous in block["previous"]:
        # Every fixture day has 10000 at 10:00 and 10000 at 18:00 (and 4000 at
        # 11:00 in the last week). The truncated figure must reflect the cut.
        if now_hour < 10:
            assert previous["revenue_paise"] == 0
        assert previous["revenue_paise"] <= previous["full_day_paise"]


def test_the_same_weekday_baseline_is_a_median_of_the_cut_figures(grown):
    block = _get(grown, "/insights/weekday")["same_weekday"]
    values = sorted(p["revenue_paise"] for p in block["previous"])
    expected = (values[1] + values[2]) // 2      # four samples, floored midpoint
    assert block["baseline_paise"] == expected
    assert block["delta_paise"] == block["today"]["revenue_paise"] - expected


def test_a_complete_past_day_is_compared_over_the_whole_day(grown):
    day = _midnight(1).strftime("%Y-%m-%d")
    block = _get(grown, "/insights/weekday", day=day)["same_weekday"]
    assert block["day_complete"] is True
    assert block["cut_seconds_into_day"] == 86_400
    assert block["cut_at"] == "the whole day"
    for previous in block["previous"]:
        assert previous["revenue_paise"] == previous["full_day_paise"]


# ------------------------------------------------------------ hour profile --

def test_the_hour_profile_has_all_twenty_four_hours(grown):
    hours = _get(grown, "/insights/hours")["hours"]
    assert hours["available"] is True
    assert [h["hour"] for h in hours["profile"]] == list(range(24))


def test_the_hours_of_a_day_add_up_to_that_day(grown):
    """The one figure this module buckets itself. If the hours did not sum to
    what /manage/today says the day was, there would be two truths."""
    body = _get(grown, "/insights")
    hours_total = sum(h["revenue_paise"] for h in body["hours"]["profile"])
    days_total = sum(d["revenue_paise"] for d in body["days"]["series"]
                     if not d["no_history"])
    assert hours_total == days_total == body["days"]["total_paise"]


def test_the_busiest_hour_is_the_evening(grown):
    """18:00 carries the festival's extra ₹500 on top of a daily ₹100."""
    hours = _get(grown, "/insights/hours")["hours"]
    assert hours["busiest_hour"]["hour"] == 18
    assert hours["busiest_hour"]["label"] == "18:00-19:00"


def test_dead_hours_are_reported_as_dead_not_dropped(grown):
    hours = _get(grown, "/insights/hours")["hours"]
    three_am = [h for h in hours["profile"] if h["hour"] == 3][0]
    assert three_am["revenue_paise"] == 0
    assert three_am["days_with_a_bill"] == 0
    assert three_am["share_pct"] == 0


def test_hour_shares_are_floored_and_say_so(grown):
    hours = _get(grown, "/insights/hours")["hours"]
    assert sum(h["share_pct"] for h in hours["profile"]) <= 100
    assert "floored" in hours["shares_note"]


# ---------------------------------------------------------------- products --

def test_pepsi_is_the_rising_product(grown):
    """Seven Pepsi this week, none the week before — so it is NEW, not 'up
    700%'. A percentage against zero is not a rate of change."""
    products = _get(grown, "/insights/products")["products"]
    assert products["available"] is True
    started = {p["sku_id"]: p for p in products["started_selling"]}
    assert "pepsi" in started
    assert started["pepsi"]["units_now"] == 7
    assert started["pepsi"]["units_before"] == 0
    assert started["pepsi"]["delta_pct"] is None
    assert started["pepsi"]["name"] == "Pepsi"


def test_parle_is_falling_because_a_day_lost_its_evening(grown):
    """Day -3 has no 18:00 bill, so this week billed one fewer Parle-G than
    last. One packet, and the module says one packet rather than a percentage
    dressed up as a trend."""
    products = _get(grown, "/insights/products")["products"]
    falling = {p["sku_id"]: p for p in products["falling"]}
    assert "parle" in falling
    assert falling["parle"]["delta_units"] == -1
    assert falling["parle"]["units_now"] == 13
    assert falling["parle"]["units_before"] == 14


def test_a_product_of_two_packets_is_not_called_a_mover(tmp_path, monkeypatch):
    """MIN_UNITS_FOR_MOVEMENT. One packet to two is one more packet, not a
    doubling, and it is counted rather than named."""
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "shop"))
    shop = Shop(tmp_path)
    shop.catalogue({"parle": ("Parle-G", 1000), "rare": ("Rare thing", 9900)})
    for back in range(20, 0, -1):
        shop.bill(_midnight(back) + timedelta(hours=10), [("parle", 10000)])
    shop.bill(_midnight(9) + timedelta(hours=12), [("rare", 9900)])
    shop.bill(_midnight(2) + timedelta(hours=12), [("rare", 9900)])
    app = FastAPI()
    app.include_router(insights.router)
    products = _get(TestClient(app), "/insights/products")["products"]
    named = {p["sku_id"] for group in ("rising", "falling", "started_selling",
                                       "stopped_selling")
             for p in products[group]}
    assert "rare" not in named
    assert products["too_few_to_judge"] == 1
    assert products["min_units_to_judge"] == insights.MIN_UNITS_FOR_MOVEMENT


def test_top_caps_the_list_and_the_total_says_how_many_there_were(grown):
    products = _get(grown, "/insights/products", top=1)["products"]
    assert len(products["falling"]) <= 1
    assert products["falling_total"] >= len(products["falling"])


# --------------------------------------------------------------- anomalies --

def test_the_festival_day_is_flagged_with_its_baseline(grown):
    """Never a bare 'unusual'. The row carries the baseline, the spread, the
    deviation in rupees, in percent and in spreads."""
    block = _get(grown, "/insights/anomalies")["anomalies"]
    assert block["available"] is True
    flagged = {row["key"]: row for row in block["days"]}
    festival = _midnight(9).strftime("%Y-%m-%d")
    assert festival in flagged
    row = flagged[festival]
    assert row["value_paise"] == 70000
    assert row["baseline_paise"] == 20000
    assert row["deviation_paise"] == 50000
    assert row["deviation_pct"] == 250
    assert row["direction"] == "above"
    assert row["samples"] >= insights.MIN_TRADING_DAYS_FOR_BASELINE


def test_an_anomaly_sentence_states_the_baseline_in_words(grown):
    row = _get(grown, "/insights/anomalies")["anomalies"]["days"][0]
    assert row["baseline_rupees"] in row["sentence"]
    assert row["value_rupees"] in row["sentence"]
    assert "median" in row["baseline_method"]


def test_prose_carries_the_rupee_sign_and_the_fields_do_not(grown):
    """A sentence is the server's own words and wears the symbol; the `*_rupees`
    fields stay bare so the page groups and prefixes them its own way. Mixing
    the two put "took 7663.00" beside the page's own "\u20b97,663.00"."""
    body = _get(grown, "/insights")
    row = body["anomalies"]["days"][0]
    assert "\u20b9" in row["sentence"]
    assert "\u20b9" not in row["value_rupees"]
    assert "\u20b9" not in row["baseline_rupees"]
    assert "\u20b9" in body["week"]["sentence"]
    assert "\u20b9" in body["same_weekday"]["sentence"]
    assert "\u20b9" in body["anomalies"]["method"]["flagged_when"]


def test_the_ordinary_days_are_not_flagged(grown):
    """Thirty-odd identical days must produce exactly the planted anomalies and
    nothing else, or the rule is too loose to be worth having."""
    block = _get(grown, "/insights/anomalies")["anomalies"]
    keys = {row["key"] for row in block["days"]}
    assert keys == {_midnight(9).strftime("%Y-%m-%d"),
                    _midnight(1).strftime("%Y-%m-%d")}
    assert block["days_checked"] > 20


def test_the_missing_evening_is_flagged_as_an_hour(grown):
    """Yesterday lost its 18:00 bill. The daily total says 'a quiet day'; the
    hour scan says WHICH hour, which is the thing a shopkeeper can act on."""
    block = _get(grown, "/insights/anomalies")["anomalies"]
    assert block["subject_day"] == _midnight(1).strftime("%Y-%m-%d")
    evening = [row for row in block["hours"] if row["key"].endswith("T18")]
    assert evening, block["hours"]
    row = evening[0]
    assert row["value_paise"] == 0
    assert row["baseline_paise"] == 10000
    assert row["direction"] == "below"
    assert "18:00-19:00" in row["label"]


def test_an_hour_is_measured_against_that_same_hour(grown):
    for row in _get(grown, "/insights/anomalies")["anomalies"]["hours"]:
        hour = row["key"].split("T")[1]
        assert f"{hour}:00" in row["baseline_method"]


def test_a_flat_shop_has_no_anomalies_and_says_that_is_a_result(tmp_path, monkeypatch):
    """Identical days collapse the spread to zero. Without the percentage and
    money floors every one of them would be flagged as many spreads out."""
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "shop"))
    shop = Shop(tmp_path)
    for back in range(25, 0, -1):
        shop.bill(_midnight(back) + timedelta(hours=10), [("parle", 10000)])
    app = FastAPI()
    app.include_router(insights.router)
    block = _get(TestClient(app), "/insights/anomalies")["anomalies"]
    assert block["available"] is True
    assert block["days"] == []
    assert block["days_found"] == 0
    assert "ordinary" in block["nothing_found_note"]


def test_a_small_wobble_is_below_the_money_floor(tmp_path, monkeypatch):
    """A ₹60 day against a ₹100 baseline is 40% out and, with every other day
    identical, unboundedly many spreads out. It is still forty rupees.
    ANOMALY_MIN_DEVIATION_PAISE is what stops this screen calling that an
    event."""
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "shop"))
    shop = Shop(tmp_path)
    for back in range(25, 0, -1):
        amount = 6000 if back == 11 else 10000
        shop.bill(_midnight(back) + timedelta(hours=10), [("parle", amount)])
    app = FastAPI()
    app.include_router(insights.router)
    block = _get(TestClient(app), "/insights/anomalies")["anomalies"]
    # 40% out and, with every other day identical, infinitely many spreads out.
    # It is still forty rupees, so it is not an event.
    assert block["days"] == []
    assert 4000 < insights.ANOMALY_MIN_DEVIATION_PAISE


# -------------------------------------------------------- the whole payload --

def test_the_composite_carries_every_block(grown):
    body = _get(grown, "/insights")
    for key in ("window", "history", "days", "week", "same_weekday", "hours",
                "products", "anomalies", "chain", "limits", "derived_from"):
        assert key in body, key


def test_a_sub_endpoint_agrees_with_the_composite(grown):
    """Six independent folds could disagree about a Tuesday at a midnight
    boundary. They are one fold, sliced."""
    whole = _get(grown, "/insights")
    for path, key in (("/insights/days", "days"), ("/insights/week", "week"),
                      ("/insights/weekday", "same_weekday"),
                      ("/insights/hours", "hours"),
                      ("/insights/products", "products"),
                      ("/insights/anomalies", "anomalies")):
        assert _get(grown, path)[key] == whole[key], path


def test_the_chain_state_rides_on_every_response(grown):
    """A bill book derived from a chain that does not verify is not a bill book,
    and the shopkeeper must never go looking for that fact."""
    for path in ("/insights", "/insights/days", "/insights/anomalies"):
        chain = _get(grown, path)["chain"]
        assert chain["ok"] is True
        assert chain["exists"] is True
        assert chain["lines_verified"] > 0


def test_no_block_forecasts_anything(grown):
    """The stated limit, asserted. Nothing named like a projection exists."""
    body = _get(grown, "/insights")
    text = json.dumps(body).lower()
    for word in ("forecast\"", "projection\"", "predicted", "run_rate", "on_track"):
        assert word not in text
    assert any("forecast" in limit.lower() for limit in body["limits"])


def test_limits_are_stated_rather_than_implied(grown):
    limits = _get(grown, "/insights")["limits"]
    joined = " ".join(limits).lower()
    assert "forecast" in joined
    assert "timezone" in joined
    assert "settled" in joined


def test_every_figure_says_how_many_days_it_stands_on(grown):
    body = _get(grown, "/insights")
    for block in ("days", "week", "hours", "products", "anomalies",
                  "same_weekday"):
        assert "days_of_history" in body[block], block


def test_an_undated_bill_is_counted_not_guessed_at(tmp_path, monkeypatch):
    """A closed bill whose timestamp will not parse cannot appear on a day. It
    appears as a count instead of being dropped or given a guessed date."""
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "shop"))
    shop = Shop(tmp_path)
    shop.bill(_midnight(1) + timedelta(hours=10), [("parle", 10000)])
    shop.led.append(ts="not-a-timestamp", module="session", event="done",
                    session_id="s_broken", from_state="BASKET_OPEN",
                    total_paise=5000, lines=1)
    app = FastAPI()
    app.include_router(insights.router)
    body = _get(TestClient(app), "/insights")
    assert body["history"]["undated_bills"] == 1
    assert body["history"]["closed_bills"] == 1
    assert body["days"]["total_paise"] == 10000
