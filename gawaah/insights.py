"""NAZAR — what this counter has seen over time, and only what it can stand behind.

`gawaah/manage.py` answers "aaj kitna hua?" for one day. This module answers the
questions that only make sense across many days: is this week better than last,
is this Tuesday a normal Tuesday, when is the shop actually busy, what is
selling more than it was, and — the one a shopkeeper cannot get from a paper
book at all — which day or which hour was FAR from its own usual, said with the
usual beside it.

    GET /insights              everything below, in one fold of the chain
    GET /insights/days         the last N days, one figure per day
    GET /insights/week         the last seven complete days against the seven before
    GET /insights/weekday      this Tuesday against the last four Tuesdays
    GET /insights/hours        the hour-of-day profile
    GET /insights/products     what is selling more, and what is selling less
    GET /insights/anomalies    a day or an hour far from its own baseline

MOUNTING
========
An ``APIRouter`` with NO prefix and absolute paths::

    from gawaah import insights
    app.include_router(insights.router)        # -> /insights, /insights/days

Do not pass a prefix; the paths above are already what a browser asks for.

THERE IS NO SECOND BILL BOOK
============================
Not one bill is derived here. Every figure is folded out of
``gawaah.manage.read_chain()`` and ``gawaah.manage.bills_from()`` — the same two
functions that draw History, Today and the day close — and each day's numbers
come from ``manage._brief_for()`` over ``manage._local_day_bounds()``. A second
definition of "the day's takings" is a second truth, and the first time the
Insights screen and the Today screen disagreed there would be no way to tell
which one was lying. What this module owns is the WINDOWING, the comparisons and
the baselines. Nothing else.

The one thing it buckets itself is the hour of day, because `_brief_for` windows
by time and cannot report inside a window. That bucketing sums the same
``total_paise`` off the same closed bills, so the hours of a day add up to
exactly what `/manage/today` says that day was — `tests/test_insights.py` asserts
that equality rather than trusting it.

NO FORECAST. NOT ONE.
=====================
There is no projection, no trend line, no "on track for", no run rate. A kirana
counter that has been running for a few weeks has tens of days of history, one
festival in the middle of them and a monsoon at the end. A line drawn forward
through that is a lie with a chart under it, and it is the exact failure this
product exists to refuse. Every number here describes something that already
happened.

WHAT "NOT ENOUGH HISTORY YET" MEANS, AND WHY IT IS A RESULT
===========================================================
Each block below states how many days it stands on. Below its stated minimum the
block does not appear with a smaller, weaker number — it appears saying
``"not enough history yet"`` with the count it has and the count it needs. This
is not politeness. A week-over-week change computed against a week that did not
exist prints "down 100%" with total confidence, and a shopkeeper who acts on it
has been actively misled by a screen that could have said nothing.

Two things follow from the same rule and are worth naming:

  * Days BEFORE this counter's first bill are gaps, not zeros. A counter
    installed five days ago has twenty-five days of no-history in a thirty-day
    window, and letting those into a median drags every baseline to nothing and
    makes every trading day an anomaly.
  * TODAY IS NOT A COMPLETE DAY. Week-over-week uses complete days only, and the
    same-weekday comparison cuts the previous Tuesdays at the same clock time
    today has reached. Comparing a half-finished day against four whole ones is
    the most inviting mistake on this screen and it is not made here.

A REFUSAL IS A RESULT
=====================
Every path answers ``{"ok": false, "reason": ..., "detail": ...}`` with a 400.
Nothing here raises a 500, and nothing here settles money: this module holds no
gateway, mints nothing and never calls the money service. ``settles_money`` is
False on every response and that is a fact about the code, not a promise.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Callable, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .money import to_rupees_str

router = APIRouter()


# --------------------------------------------------------------- refusals --
#
# Every one of these is a state a request can actually reach. The reason names
# the STATE in lowercase snake_case; the sentence that says what to do goes in
# `detail`, never in the reason.

R_BAD_DAYS = "days_not_a_positive_integer"
R_DAYS_TOO_FEW = "days_below_the_minimum_window"
R_DAYS_TOO_MANY = "days_beyond_the_window_ceiling"
R_BAD_DAY = "day_not_a_calendar_date"
R_DAY_IN_FUTURE = "day_has_not_started"
R_BAD_TOP = "top_not_a_positive_integer"
R_TOP_TOO_MANY = "top_beyond_the_ceiling"
R_NO_BILL_BOOK = "bill_book_unavailable"
R_INTERNAL = "insights_internal_error"


# ------------------------------------------------------------- the windows --

#: The window a screen asks for by default. Thirty days is four weeks plus the
#: change: enough for four same-weekday samples and two clean weeks, short
#: enough that a shop's character has not changed underneath it.
DEFAULT_WINDOW_DAYS = 30

#: FIFTEEN, and the extra day is not an off-by-one.
#:
#: The two comparisons this module exists for — week against week and product
#: against product — are seven COMPLETE days against seven complete days, and
#: the day you are standing in is not complete. A fourteen-day window contains
#: only thirteen complete days for most of every day, so `days=14` would refuse
#: both comparisons every afternoon and look like a bug rather than a bound.
#: Fifteen is fourteen complete days plus today.
#:
#: Refused BY NAME rather than answered with the blocks silently missing: a
#: screen that quietly drops half its content when a parameter is wrong teaches
#: nobody anything.
MIN_WINDOW_DAYS = 15

#: A year. Past this the fold is a report, not a screen, and the chain should be
#: read directly. Refused rather than clamped: a clamped window would answer a
#: question about 400 days with an answer about 365 and label it neither.
MAX_WINDOW_DAYS = 365

#: How many movers a list names. Six fits a card without scrolling; the rest are
#: counted so the page can say how many it did not show.
DEFAULT_TOP = 6
MAX_TOP = 40


# ------------------------------------------------------------ the minimums --
#
# Each of these is a judgement, so each says what it costs when it is wrong.

#: Week over week. Fourteen COMPLETE days of history, because both sides are
#: seven complete days. Too low and the older week is partly the day the counter
#: was installed, which reads as explosive growth. Too high and a shop three
#: weeks old is told nothing at all for no good reason.
MIN_DAYS_FOR_WEEK = 14

#: The same-weekday comparison. Two prior Tuesdays is the fewest that can be
#: called a baseline; one is an anecdote with a percentage on it. Four is what
#: it looks back for.
MIN_SAME_WEEKDAY_SAMPLES = 2
SAME_WEEKDAY_LOOKBACK = 4

#: The hour profile. Seven trading days, so that one unusual afternoon cannot
#: define when the shop is busy. Below it the profile would be a picture of one
#: or two days wearing the word "profile".
MIN_TRADING_DAYS_FOR_HOURS = 7

#: Anomalies. A baseline needs enough days that the median is a median. Seven is
#: the fewest that survives one outlier in either direction. What it costs when
#: this is too low: the second unusual day in a young shop's history is measured
#: against the first, and both get flagged.
MIN_TRADING_DAYS_FOR_BASELINE = 7

#: Rising and falling products. Same two seven-day windows as the week block.
MIN_DAYS_FOR_PRODUCTS = 14

#: A product that went from one packet to two has not doubled; it has sold one
#: more packet. Below this many packets across BOTH windows a SKU is not called
#: a mover at all — it is counted under `too_few_to_judge` and named nowhere
#: else. What it costs: a genuinely new slow-moving line takes a fortnight to
#: appear here. That is the right trade against printing "up 100%" about two
#: biscuit packets.
MIN_UNITS_FOR_MOVEMENT = 3


# ------------------------------------------------------------- the anomaly --
#
# THE RULE, STATED ONCE. A point is flagged only when ALL THREE hold. Each one
# is there to kill a different false positive, and dropping any of them puts a
# wrong flag on this screen:
#
#   * at least ANOMALY_MAD_MULTIPLE times the spread — the statistical part. On
#     its own it fails for a shop whose days are nearly identical: the spread
#     collapses to a few rupees and every ordinary day is nine spreads out.
#   * at least ANOMALY_MIN_DEVIATION_PCT of the baseline — kills that case.
#   * at least ANOMALY_MIN_DEVIATION_PAISE in absolute money — kills the case
#     where the baseline itself is tiny, so that a ₹60 hour against a ₹20
#     baseline is not reported as a 200% event worth a shopkeeper's attention.
#
# The baseline is the MEDIAN and the spread is the MEDIAN ABSOLUTE DEVIATION,
# not the mean and the standard deviation. A mean is dragged by the very day
# being tested, so a festival day partly defines the baseline it is measured
# against and then looks less unusual than it was. The median does not move.
ANOMALY_MAD_MULTIPLE = 3
ANOMALY_MIN_DEVIATION_PCT = 25
ANOMALY_MIN_DEVIATION_PAISE = 5_000        # ₹50

#: How many are reported. Sorted by how far out they are, so the cap drops the
#: least remarkable. The number CHECKED is reported beside them, so a cap is
#: never mistaken for "there were only this many".
MAX_DAY_ANOMALIES = 8
MAX_HOUR_ANOMALIES = 6


WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday")
WEEKDAYS_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

NOT_ENOUGH = "not_enough_history_yet"

#: What this screen does not do, said out loud rather than left to be assumed.
#: It rides on every response because the most dangerous reading of a chart is
#: the one the page never disclaimed.
LIMITS = (
    "Nothing here is a forecast. This data cannot support one, and a line drawn "
    "forward through a few weeks of a kirana's trading would be a lie with a "
    "chart under it.",
    "Every figure counts bills that CLOSED. A basket still open on the counter "
    "is in none of them.",
    "A bill counts on the day and in the hour it closed, in this counter's own "
    "timezone — not the gateway's, and not UTC.",
    "Revenue is what was BILLED. Only the settled figure is money a "
    "signature-verified webhook stands behind, and the two are never added "
    "together.",
    "Days before this counter's first bill are gaps, not zeros, and are left out "
    "of every baseline.",
    "Both sides of every comparison are folded the same way out of the same "
    "chain. There is no cached delta anywhere on this page.",
)


class InsightsRefused(Exception):
    """A named refusal with a sentence a shopkeeper can act on."""

    def __init__(self, reason: str, detail: str, status: int = 400) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.status = status


def _refusal(exc: InsightsRefused) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "reason": exc.reason, "detail": exc.detail,
         "settles_money": False},
        status_code=exc.status,
    )


def _crash(exc: BaseException) -> JSONResponse:
    """The last resort. There are no 500s here.

    The exception TYPE is named and the message passed through, because on a
    screen derived from a file the message is usually the whole diagnosis —
    "No such file or directory: results/audit.jsonl" says what to do.
    """
    return JSONResponse(
        {"ok": False, "reason": R_INTERNAL,
         "detail": f"{type(exc).__name__}: {exc}", "settles_money": False},
        status_code=400,
    )


# ------------------------------------------------------------ the bill book --

#: The names this module borrows from `gawaah.manage`. Listed so that a rename
#: over there fails HERE with a sentence naming the missing function, instead of
#: an AttributeError inside a fold that a reader has to trace back.
BORROWED = ("read_chain", "bills_from", "_brief_for", "_local_day_bounds",
            "_parse_ts", "catalogue")


def _bill_book() -> dict[str, Callable[..., Any]]:
    """The functions that already know how to read the chain.

    Imported LATE, inside the call, for the same reason `gawaah/daybook.py`
    does it: `gawaah.manage` pulls the vision constants in through `identity`,
    and a module imported at the top of this file would make every process that
    mounts this router pay for that at start-up. It also makes the refusal
    reachable in a test, which a top-level import would not be.
    """
    try:
        from . import manage
    except Exception as exc:  # noqa: BLE001 - an import failure is a refusal
        raise InsightsRefused(
            R_NO_BILL_BOOK,
            f"gawaah.manage could not be imported ({type(exc).__name__}: {exc}), "
            f"and this screen has no bill book of its own. Nothing is shown "
            f"rather than a figure derived some second way.",
        ) from None
    missing = [n for n in BORROWED if not callable(getattr(manage, n, None))]
    if missing:
        raise InsightsRefused(
            R_NO_BILL_BOOK,
            f"gawaah.manage is missing {', '.join(missing)}. Every number on "
            f"this screen is folded out of that module on purpose; this one "
            f"will not re-derive a bill a second way to fill the gap.",
        )
    return {n: getattr(manage, n) for n in BORROWED}


# ------------------------------------------------------------ plain numbers --

def _median(values: list[int]) -> int:
    """The middle value, in whole paise.

    An even-length list takes the FLOOR of the two middle values rather than
    their true midpoint, because half a paisa is not money and this figure is
    printed on a page as a rupee amount. It is at most one paisa low and it is
    never a float.
    """
    if not values:
        return 0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return int(ordered[mid])
    return int((ordered[mid - 1] + ordered[mid]) // 2)


def _mad(values: list[int], centre: int) -> int:
    """Median absolute deviation from `centre`. The spread, robust to the very
    point being tested — see the note on the anomaly rule above."""
    if not values:
        return 0
    return _median([abs(int(v) - centre) for v in values])


def _pct_of(part: int, whole: int) -> Optional[int]:
    """`part` as a whole-number percentage of `whole`, or None when there is no
    honest answer. Integer arithmetic; a percentage is a description and still
    may not invent a fraction of a paisa."""
    if whole == 0:
        return None
    return (part * 100) // whole


def _rs(paise: int) -> str:
    """A rupee amount for PROSE.

    The `*_rupees` fields stay bare, exactly as `money.to_rupees_str` renders
    them, so the page can group and prefix them its own way. A SENTENCE is
    different: it is the server's own words, read as they are, and "took 7663.00"
    beside the page's own "₹7,663.00" reads as two different systems talking.
    """
    return f"\u20b9{to_rupees_str(paise)}"


def _hhmm(seconds_into_day: int) -> str:
    hours = seconds_into_day // 3600
    minutes = (seconds_into_day % 3600) // 60
    return f"{hours:02d}:{minutes:02d}"


def _hour_label(hour: int) -> str:
    return f"{hour:02d}:00-{(hour + 1) % 24:02d}:00"


def _short(iso_day: str) -> str:
    """'2026-08-14' -> 'Thu 14 Aug'. Fixed English, no locale: this string is
    asserted in tests and read across a counter, and a machine whose locale
    changed would change the page under both."""
    d = _dt.date.fromisoformat(iso_day)
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{WEEKDAYS_SHORT[d.weekday()]} {d.day} {months[d.month - 1]}"


def _not_enough(has: int, needs: int, what: str,
                unit: str = "days of history") -> dict[str, Any]:
    """The one shape every block wears when it will not answer.

    It carries the counts on purpose. "Not enough history" with no numbers reads
    as a bug; "9 trading days, this needs 14" reads as a wait. `unit` names what
    is being counted, because the blocks do not all count the same thing — the
    week wants COMPLETE days, the hour profile wants days that took money, and
    a message that said "days" for all three would be wrong twice.
    """
    return {
        "available": False,
        "reason": NOT_ENOUGH,
        "detail": (
            f"{what} needs {needs} {unit} and this counter has {has}. No smaller "
            f"version of this figure is shown, because a comparison made against "
            f"days that do not exist reads as a real change."
        ),
        "days_of_history": has,
        "days_needed": needs,
        "counting": unit,
    }


# ------------------------------------------------------ reading the request --

def _require_days(raw: Any) -> int:
    if raw is None or raw == "":
        return DEFAULT_WINDOW_DAYS
    try:
        want = int(str(raw))
    except (TypeError, ValueError):
        raise InsightsRefused(
            R_BAD_DAYS,
            f"days={raw!r} is not a whole number of days. Leave it out for "
            f"{DEFAULT_WINDOW_DAYS}.",
        ) from None
    if want < MIN_WINDOW_DAYS:
        raise InsightsRefused(
            R_DAYS_TOO_FEW,
            f"days={want} is under {MIN_WINDOW_DAYS}. Every comparison on this "
            f"screen is seven days against seven days, so a shorter window "
            f"cannot carry one.",
        )
    if want > MAX_WINDOW_DAYS:
        raise InsightsRefused(
            R_DAYS_TOO_MANY,
            f"days={want} is over the ceiling of {MAX_WINDOW_DAYS}. Past a year "
            f"this is a report rather than a screen; read the chain directly.",
        )
    return want


def _require_top(raw: Any) -> int:
    if raw is None or raw == "":
        return DEFAULT_TOP
    try:
        want = int(str(raw))
    except (TypeError, ValueError):
        raise InsightsRefused(
            R_BAD_TOP,
            f"top={raw!r} is not a whole number. Leave it out for {DEFAULT_TOP}.",
        ) from None
    if want < 1:
        raise InsightsRefused(
            R_BAD_TOP,
            f"top={want} asks for no products at all; the smallest useful "
            f"number is 1.",
        )
    if want > MAX_TOP:
        raise InsightsRefused(
            R_TOP_TOO_MANY,
            f"top={want} is over the ceiling of {MAX_TOP}. A list that long is "
            f"the inventory screen, not a card on this one.",
        )
    return want


def _require_day(raw: Any) -> Optional[str]:
    """A calendar day, or None for today.

    Validated HERE rather than left to `manage._local_day_bounds`, so that a
    malformed day comes back under this module's own refusal name instead of
    manage's — two names for one mistake is how a page ends up with two error
    paths for the same typo.
    """
    if raw is None or raw == "":
        return None
    text = str(raw)
    if not DAY_RE.match(text):
        raise InsightsRefused(
            R_BAD_DAY,
            f"day={text!r} is not a calendar day. Write it as YYYY-MM-DD, for "
            f"example 2026-09-01.",
        )
    try:
        _dt.date.fromisoformat(text)
    except ValueError:
        raise InsightsRefused(
            R_BAD_DAY,
            f"day={text!r} looks like a date but is not one. Check the month "
            f"and the day of the month.",
        ) from None
    return text


# ------------------------------------------------------------- the assembly --

def _assemble(day: Optional[str], days: int, top: int) -> dict[str, Any]:
    """One fold of the chain, and every block on the screen out of it.

    Deliberately ONE function rather than six endpoints each folding the chain
    again. The blocks share a window, a timezone and a set of eligible days, and
    six independent folds could disagree about any of the three at a midnight
    boundary. The sub-endpoints below slice this, so /insights/hours can never
    say a different thing from the hours inside /insights.
    """
    bb = _bill_book()
    read_chain = bb["read_chain"]
    bills_from = bb["bills_from"]
    brief_for = bb["_brief_for"]
    day_bounds = bb["_local_day_bounds"]
    parse_ts = bb["_parse_ts"]

    anchor_start, anchor_end, anchor_label = day_bounds(day)
    tz = anchor_start.tzinfo
    now = _dt.datetime.now(tz)
    if anchor_start > now:
        raise InsightsRefused(
            R_DAY_IN_FUTURE,
            f"{anchor_label} has not started yet on this counter's clock. There "
            f"is nothing to look back from.",
        )

    records, chain = read_chain()
    bills = bills_from(records)

    # ---- bucket the closed bills by local day, once ------------------------
    #
    # Parsed once here rather than inside every window: `_brief_for` re-filters
    # whatever dict it is handed, so handing it one day's bills gives exactly
    # the same answer as handing it all of them, at a fraction of the work.
    by_day: dict[str, dict[str, Any]] = {}
    by_day_hour: dict[str, dict[int, int]] = {}
    undated = 0
    total_closed = 0
    first_at: Optional[str] = None
    last_at: Optional[str] = None
    for session_id, bill in bills.items():
        if not bill.get("closed"):
            continue
        at = parse_ts(bill.get("at"))
        if at is None:
            # A closed bill whose timestamp will not parse is COUNTED, never
            # dropped silently and never given a guessed date. It cannot appear
            # on a day, so it appears as a number the page can show.
            undated += 1
            continue
        total_closed += 1
        local = at.astimezone(tz)
        label = local.strftime("%Y-%m-%d")
        by_day.setdefault(label, {})[session_id] = bill
        hours = by_day_hour.setdefault(label, {})
        amount = int(bill.get("total_paise") or 0)
        hours[local.hour] = hours.get(local.hour, 0) + amount
        stamp = local.isoformat(timespec="seconds")
        if first_at is None or stamp < first_at:
            first_at = stamp
        if last_at is None or stamp > last_at:
            last_at = stamp

    trading_dates = sorted(by_day)
    first_date = trading_dates[0] if trading_dates else None
    last_date = trading_dates[-1] if trading_dates else None

    # Days of history: from the first bill to the day being looked back from.
    # Not "days with a bill" — a shop shut on Sunday still has that Sunday of
    # history, and a baseline that skipped it would flatter every Monday.
    days_spanned = 0
    if first_date is not None:
        span = (_dt.date.fromisoformat(anchor_label) - _dt.date.fromisoformat(first_date)).days
        days_spanned = span + 1 if span >= 0 else 0

    # ---- the window, oldest first ------------------------------------------
    series: list[dict[str, Any]] = []
    units_by_day: dict[str, dict[str, int]] = {}
    revenue_by_day_sku: dict[str, dict[str, int]] = {}
    for back in range(days - 1, -1, -1):
        d_label = (anchor_start - _dt.timedelta(days=back)).strftime("%Y-%m-%d")
        d_start, d_end, _ = day_bounds(d_label)
        brief = brief_for(by_day.get(d_label, {}), d_start, d_end)
        units_by_day[d_label] = dict(brief["units_by_sku"])
        revenue_by_day_sku[d_label] = dict(brief["line_revenue_by_sku"])
        wd = _dt.date.fromisoformat(d_label).weekday()
        before_history = first_date is not None and d_label < first_date
        series.append({
            "date": d_label,
            "short": _short(d_label),
            "weekday": WEEKDAYS[wd],
            "weekday_short": WEEKDAYS_SHORT[wd],
            "weekday_index": wd,
            "bills": brief["bills"],
            "revenue_paise": brief["revenue_paise"],
            "revenue_rupees": brief["revenue_rupees"],
            "settled_paise": brief["settled_paise"],
            "settled_rupees": brief["settled_rupees"],
            "units": sum(brief["units_by_sku"].values()),
            "excluded_lines": brief["excluded_lines"],
            "complete": d_end <= now,
            # A day the counter did not exist for is a GAP. Drawn as a gap and
            # kept out of every median below; see the module docstring.
            "no_history": bool(before_history or first_date is None),
        })

    eligible = [d for d in series if not d["no_history"]]
    complete_eligible = [d for d in eligible if d["complete"]]

    window = {
        "days": days,
        "from": series[0]["date"] if series else anchor_label,
        "to": anchor_label,
        "anchor_complete": anchor_end <= now,
        "now": now.isoformat(timespec="seconds"),
        "utc_offset": now.strftime("%z"),
        "days_with_history": len(eligible),
        "complete_days_with_history": len(complete_eligible),
    }

    history = {
        "first_bill_at": first_at,
        "last_bill_at": last_at,
        "first_bill_day": first_date,
        "last_bill_day": last_date,
        "days_spanned": days_spanned,
        "trading_days": len(trading_dates),
        "trading_days_in_window": sum(1 for d in eligible if d["bills"] > 0),
        "closed_bills": total_closed,
        "undated_bills": undated,
        "note": (
            "Days of history is counted from the first bill this chain holds to "
            "the day being looked back from — not the number of days that took "
            "money. A Sunday the shop was shut is still a day of history, and a "
            "baseline that skipped it would flatter every Monday."
        ),
    }

    return {
        "window": window,
        "history": history,
        "days": _days_block(series, eligible),
        "week": _week_block(complete_eligible, days_spanned),
        "same_weekday": _same_weekday_block(
            by_day, brief_for, day_bounds, anchor_start, anchor_end,
            anchor_label, now, first_date),
        "hours": _hours_block(by_day_hour, eligible),
        "products": _products_block(
            complete_eligible, units_by_day, revenue_by_day_sku, days_spanned,
            top, bb),
        "anomalies": _anomalies_block(eligible, by_day_hour),
        "chain": chain,
        "limits": list(LIMITS),
        "derived_from": (
            "Every figure is folded out of the hash-chained audit log by "
            "gawaah.manage — the same read_chain(), bills_from() and per-day "
            "window the History and Today screens use. Nothing is cached, "
            "nothing is estimated, and nothing is projected forward."
        ),
    }


# ------------------------------------------------------------ block: days --

def _days_block(series: list[dict[str, Any]],
                eligible: list[dict[str, Any]]) -> dict[str, Any]:
    """The last N days, one figure each.

    Always available, and that is deliberate: a series of what each day actually
    took is a record, not a claim about a trend, so there is no minimum below
    which it should be withheld. The BASELINE derived from it does have one,
    and it is reported separately.
    """
    revenues = [int(d["revenue_paise"]) for d in eligible]
    trading = [d for d in eligible if d["bills"] > 0]
    total_paise = sum(revenues)
    settled_paise = sum(int(d["settled_paise"]) for d in eligible)
    baseline_ok = len(trading) >= MIN_TRADING_DAYS_FOR_BASELINE

    # COMPLETE days only. A day still running has not finished taking money, so
    # calling it the quietest day is a statement about the clock rather than
    # about the shop — at nine in the morning it would be the quietest day
    # every single day.
    finished = [d for d in trading if d["complete"]]
    busiest = max(finished, key=lambda d: (d["revenue_paise"], d["date"])) if finished else None
    quietest = min(finished, key=lambda d: (d["revenue_paise"], d["date"])) if finished else None

    return {
        "available": True,
        "series": series,
        "days_of_history": len(eligible),
        "trading_days": len(trading),
        "total_paise": total_paise,
        "total_rupees": to_rupees_str(total_paise),
        "settled_paise": settled_paise,
        "settled_rupees": to_rupees_str(settled_paise),
        "bills": sum(int(d["bills"]) for d in eligible),
        # A per-day average over days the counter existed for, floored. It is
        # NOT divided by the window length: dividing by thirty when the counter
        # is nine days old prints a third of the truth.
        "average_day_paise": (total_paise // len(eligible)) if eligible else 0,
        "average_day_rupees": to_rupees_str(total_paise // len(eligible)) if eligible else "0.00",
        "peak_paise": max(revenues) if revenues else 0,
        "complete_trading_days": len(finished),
        # Named `..._complete_day` so a reader cannot take them for the busiest
        # and quietest of every day in the window, today included.
        "extremes_note": (
            "The busiest and quietest days below are the finished ones. A day "
            "still running has not taken all the money it is going to."
        ),
        "busiest_complete_day": None if busiest is None else {
            "date": busiest["date"], "short": busiest["short"],
            "revenue_paise": busiest["revenue_paise"],
            "revenue_rupees": busiest["revenue_rupees"],
        },
        "quietest_complete_day": None if quietest is None else {
            "date": quietest["date"], "short": quietest["short"],
            "revenue_paise": quietest["revenue_paise"],
            "revenue_rupees": quietest["revenue_rupees"],
        },
        # `available` is what the union on the other side keys off. Without it
        # the success branch is indistinguishable from the refusal branch to
        # every reader, and the screen rendered "undefined of undefined" over a
        # median it had been handed.
        "baseline": ({
            "available": True,
            "median_paise": _median(revenues),
            "median_rupees": to_rupees_str(_median(revenues)),
            "method": (
                f"the median of all {len(eligible)} days this counter has "
                f"existed for inside the window, closed days included"
            ),
        } if baseline_ok else _not_enough(
            len(trading), MIN_TRADING_DAYS_FOR_BASELINE, "A typical-day figure",
            "days that took money")),
    }


# ------------------------------------------------------------ block: week --

def _totals_of(days: list[dict[str, Any]]) -> dict[str, Any]:
    revenue = sum(int(d["revenue_paise"]) for d in days)
    settled = sum(int(d["settled_paise"]) for d in days)
    n_bills = sum(int(d["bills"]) for d in days)
    return {
        "from": days[0]["date"] if days else None,
        "to": days[-1]["date"] if days else None,
        "days": len(days),
        "bills": n_bills,
        "revenue_paise": revenue,
        "revenue_rupees": to_rupees_str(revenue),
        "settled_paise": settled,
        "settled_rupees": to_rupees_str(settled),
        "units": sum(int(d["units"]) for d in days),
        "average_bill_paise": (revenue // n_bills) if n_bills else 0,
        "average_bill_rupees": to_rupees_str(revenue // n_bills) if n_bills else "0.00",
    }


def _week_block(complete_eligible: list[dict[str, Any]],
                days_spanned: int) -> dict[str, Any]:
    """Seven complete days against the seven complete days before them.

    COMPLETE days only. Today is half-finished at four in the afternoon and
    putting it on one side of a week-over-week comparison is the fastest way to
    print "down 40%" about a shop that is having a normal day. Today's own
    figure is on the days series and in the same-weekday block, where it is
    compared against the same hour rather than a whole day.
    """
    # Gated on COMPLETE days, which is the thing actually being counted. Gating
    # on days_spanned instead would pass at fourteen days of history and then
    # slice a thirteen-day list, quietly comparing six days against seven.
    if len(complete_eligible) < MIN_DAYS_FOR_WEEK:
        return _not_enough(len(complete_eligible), MIN_DAYS_FOR_WEEK,
                           "A week-against-week comparison",
                           "complete days of history")

    this_week = complete_eligible[-7:]
    last_week = complete_eligible[-14:-7]
    now_t = _totals_of(this_week)
    was_t = _totals_of(last_week)
    delta = now_t["revenue_paise"] - was_t["revenue_paise"]
    pct = _pct_of(delta, was_t["revenue_paise"])

    if was_t["revenue_paise"] == 0:
        sentence = (
            f"The seven days to {now_t['to']} took "
            f"{_rs(now_t['revenue_paise'])}. The seven before them took nothing, "
            f"so there is no percentage to put on that."
        )
    else:
        direction = "up" if delta > 0 else "down" if delta < 0 else "level"
        moved = _rs(abs(delta))
        tail = ("level." if delta == 0
                else f"{direction} {moved}, {abs(pct or 0)}% on the week.")
        sentence = (
            f"The seven days to {now_t['to']} took {_rs(now_t['revenue_paise'])} "
            f"against {_rs(was_t['revenue_paise'])} the seven days before — {tail}"
        )

    return {
        "available": True,
        "days_of_history": days_spanned,
        "complete_days_only": True,
        "this_week": now_t,
        "last_week": was_t,
        "delta_paise": delta,
        "delta_rupees": to_rupees_str(delta),
        "delta_pct": pct,
        "bills_delta": now_t["bills"] - was_t["bills"],
        "direction": "up" if delta > 0 else "down" if delta < 0 else "level",
        "sentence": sentence,
        "method": (
            "the seven most recent COMPLETE days this counter existed for, "
            "against the seven complete days before them, both folded the same "
            "way out of the same chain"
        ),
    }


# -------------------------------------------------- block: the same weekday --

def _same_weekday_block(by_day: dict[str, dict[str, Any]],
                        brief_for: Callable[..., Any],
                        day_bounds: Callable[..., Any],
                        anchor_start: _dt.datetime,
                        anchor_end: _dt.datetime,
                        anchor_label: str,
                        now: _dt.datetime,
                        first_date: Optional[str]) -> dict[str, Any]:
    """This Tuesday against the last four Tuesdays — cut at the same clock time.

    THE PART THAT MATTERS. If today is not over, every previous Tuesday is
    measured only up to the time today has reached. Comparing a day that is
    four hours old against four days that ran fourteen hours makes an ordinary
    morning look like a collapse, and it is the single most inviting mistake
    available on this screen.

    The complete totals for those Tuesdays are reported too, separately and
    labelled, because "and they finished on ₹4,200" is worth knowing — it is
    just not the comparison.
    """
    weekday_index = _dt.date.fromisoformat(anchor_label).weekday()
    weekday = WEEKDAYS[weekday_index]

    complete = anchor_end <= now
    if complete:
        cut_seconds = 86_400
    else:
        elapsed = now - anchor_start
        cut_seconds = elapsed.days * 86_400 + elapsed.seconds

    def _upto(label: str) -> dict[str, Any]:
        start, _end, _ = day_bounds(label)
        return brief_for(by_day.get(label, {}), start,
                         start + _dt.timedelta(seconds=cut_seconds))

    def _full(label: str) -> dict[str, Any]:
        start, end, _ = day_bounds(label)
        return brief_for(by_day.get(label, {}), start, end)

    # A same-weekday that fell before this counter's first bill is not a quiet
    # Tuesday, it is a Tuesday the counter did not exist for. The walk back
    # stops there rather than averaging in a zero.
    previous: list[dict[str, Any]] = []
    for weeks in range(1, SAME_WEEKDAY_LOOKBACK + 1):
        if first_date is None:
            break
        label = (anchor_start - _dt.timedelta(days=7 * weeks)).strftime("%Y-%m-%d")
        if label < first_date:
            break
        part = _upto(label)
        whole = _full(label)
        previous.append({
            "date": label,
            "short": _short(label),
            "weeks_ago": weeks,
            "revenue_paise": part["revenue_paise"],
            "revenue_rupees": part["revenue_rupees"],
            "bills": part["bills"],
            "full_day_paise": whole["revenue_paise"],
            "full_day_rupees": whole["revenue_rupees"],
            "full_day_bills": whole["bills"],
        })

    if len(previous) < MIN_SAME_WEEKDAY_SAMPLES:
        block = _not_enough(len(previous), MIN_SAME_WEEKDAY_SAMPLES,
                            f"A comparison against previous {weekday}s",
                            f"earlier {weekday}s on this counter")
        block["weekday"] = weekday
        block["samples"] = len(previous)
        block["previous"] = previous
        return block

    today = _upto(anchor_label)
    values = [int(p["revenue_paise"]) for p in previous]
    baseline = _median(values)
    delta = int(today["revenue_paise"]) - baseline
    pct = _pct_of(delta, baseline)
    cut = _hhmm(cut_seconds) if not complete else "the whole day"

    if baseline == 0:
        sentence = (
            f"The last {len(previous)} {weekday}s took nothing by this point in "
            f"the day, so there is no percentage to put on today's "
            f"{_rs(today['revenue_paise'])}."
        )
    else:
        direction = "ahead of" if delta > 0 else "behind" if delta < 0 else "level with"
        sentence = (
            f"This {weekday} has taken {_rs(today['revenue_paise'])}"
            + (f" by {cut}. " if not complete else ". ")
            + f"The last {len(previous)} {weekday}s took "
              f"{_rs(baseline)} by the same point (median). "
              f"Today is {_rs(abs(delta))} {direction} that"
            + (f", {abs(pct or 0)}%." if delta else ".")
        )

    return {
        "available": True,
        "weekday": weekday,
        "date": anchor_label,
        "day_complete": complete,
        "cut_at": cut,
        "cut_seconds_into_day": cut_seconds,
        "today": {
            "date": anchor_label,
            "revenue_paise": today["revenue_paise"],
            "revenue_rupees": today["revenue_rupees"],
            "bills": today["bills"],
        },
        "previous": previous,
        "samples": len(previous),
        "days_of_history": len(previous) * 7,
        "baseline_paise": baseline,
        "baseline_rupees": to_rupees_str(baseline),
        "baseline_method": (
            f"the median of the last {len(previous)} {weekday}s, each measured "
            + (f"only up to {cut}, exactly as far as today has run"
               if not complete else "over the whole day")
        ),
        "delta_paise": delta,
        "delta_rupees": to_rupees_str(delta),
        "delta_pct": pct,
        "direction": "up" if delta > 0 else "down" if delta < 0 else "level",
        "sentence": sentence,
    }


# ----------------------------------------------------------- block: hours --

def _hours_block(by_day_hour: dict[str, dict[int, int]],
                 eligible: list[dict[str, Any]]) -> dict[str, Any]:
    """When this counter is actually busy.

    Summed over the days in the window the counter existed for. Twenty-four
    entries always, including the empty ones: a profile that silently dropped
    03:00 would let a reader believe the shop was measured round the clock and
    found nothing, which happens to be true here but would not be on a counter
    that had simply never been switched on at night.
    """
    labels = [d["date"] for d in eligible]
    trading = [d for d in eligible if d["bills"] > 0]
    if len(trading) < MIN_TRADING_DAYS_FOR_HOURS:
        return _not_enough(len(trading), MIN_TRADING_DAYS_FOR_HOURS,
                           "An hour-of-day profile", "days that took money")

    totals = {h: 0 for h in range(24)}
    day_counts = {h: 0 for h in range(24)}
    for label in labels:
        hours = by_day_hour.get(label, {})
        for hour, amount in hours.items():
            totals[hour] += int(amount)
            if amount:
                day_counts[hour] += 1

    grand = sum(totals.values())
    profile = [{
        "hour": h,
        "label": _hour_label(h),
        "revenue_paise": totals[h],
        "revenue_rupees": to_rupees_str(totals[h]),
        "days_with_a_bill": day_counts[h],
        # Whole-number percent of the window's takings, floored. The twenty-four
        # of these will sum to a little under 100 and the page says so rather
        # than fudging the last one up to make it look tidy.
        "share_pct": _pct_of(totals[h], grand),
    } for h in range(24)]

    active = [p for p in profile if p["revenue_paise"] > 0]
    busiest = max(active, key=lambda p: (p["revenue_paise"], -p["hour"])) if active else None

    return {
        "available": True,
        "days_of_history": len(trading),
        "profile": profile,
        "total_paise": grand,
        "total_rupees": to_rupees_str(grand),
        "peak_paise": max(totals.values()) if totals else 0,
        "first_active_hour": active[0]["hour"] if active else None,
        "last_active_hour": active[-1]["hour"] if active else None,
        "busiest_hour": None if busiest is None else {
            "hour": busiest["hour"], "label": busiest["label"],
            "revenue_paise": busiest["revenue_paise"],
            "revenue_rupees": busiest["revenue_rupees"],
            "share_pct": busiest["share_pct"],
        },
        "method": (
            f"every closed bill in the window, counted in the hour it closed, "
            f"across the {len(trading)} days this counter took money"
        ),
        "shares_note": (
            "The shares are whole percentages, floored, so they add up to a "
            "little under a hundred. Nothing is rounded up to make the column "
            "look tidy."
        ),
    }


# -------------------------------------------------------- block: products --

def _products_block(complete_eligible: list[dict[str, Any]],
                    units_by_day: dict[str, dict[str, int]],
                    revenue_by_day_sku: dict[str, dict[str, int]],
                    days_spanned: int,
                    top: int,
                    bb: dict[str, Callable[..., Any]]) -> dict[str, Any]:
    """What is selling more than it was, and what has gone quiet.

    Seven complete days against the seven before, in PACKETS as well as rupees,
    because a shopkeeper reorders by the packet. A SKU under
    MIN_UNITS_FOR_MOVEMENT packets across both windows is not called a mover at
    all — see the note on that constant.

    Products that appear in one window and not the other are listed separately
    rather than shown as a percentage. "Up 100%" and "new this week" are
    different facts and only one of them is a rate of change.
    """
    if len(complete_eligible) < MIN_DAYS_FOR_PRODUCTS:
        return _not_enough(len(complete_eligible), MIN_DAYS_FOR_PRODUCTS,
                           "A rising-and-falling comparison",
                           "complete days of history")

    recent = [d["date"] for d in complete_eligible[-7:]]
    before = [d["date"] for d in complete_eligible[-14:-7]]

    def _sum(labels: list[str], source: dict[str, dict[str, int]]) -> dict[str, int]:
        out: dict[str, int] = {}
        for label in labels:
            for sku, n in source.get(label, {}).items():
                out[sku] = out.get(sku, 0) + int(n)
        return out

    units_now = _sum(recent, units_by_day)
    units_was = _sum(before, units_by_day)
    money_now = _sum(recent, revenue_by_day_sku)
    money_was = _sum(before, revenue_by_day_sku)

    # Names from the catalogue, which is the shop's own record. A SKU the chain
    # sold and the catalogue no longer holds keeps its id and is LABELLED as
    # gone, rather than being hidden — a mover that vanished from the list is a
    # question with nowhere to go.
    try:
        cat = bb["catalogue"]()
        names = {sku: (rec.get("name") or sku) for sku, rec in cat["items"].items()}
    except Exception:  # noqa: BLE001 - a hand-edited catalogue is not an outage
        names = {}

    rising: list[dict[str, Any]] = []
    falling: list[dict[str, Any]] = []
    started: list[dict[str, Any]] = []
    stopped: list[dict[str, Any]] = []
    too_few = 0

    for sku in sorted(set(units_now) | set(units_was)):
        n_now = units_now.get(sku, 0)
        n_was = units_was.get(sku, 0)
        if n_now + n_was < MIN_UNITS_FOR_MOVEMENT:
            too_few += 1
            continue
        row = {
            "sku_id": sku,
            "name": names.get(sku, sku),
            "still_in_catalogue": sku in names,
            "units_now": n_now,
            "units_before": n_was,
            "delta_units": n_now - n_was,
            "revenue_now_paise": money_now.get(sku, 0),
            "revenue_now_rupees": to_rupees_str(money_now.get(sku, 0)),
            "revenue_before_paise": money_was.get(sku, 0),
            "revenue_before_rupees": to_rupees_str(money_was.get(sku, 0)),
            "delta_pct": _pct_of(n_now - n_was, n_was),
        }
        if n_was == 0:
            started.append(row)
        elif n_now == 0:
            stopped.append(row)
        elif n_now > n_was:
            rising.append(row)
        elif n_now < n_was:
            falling.append(row)

    rising.sort(key=lambda r: (-r["delta_units"], r["sku_id"]))
    falling.sort(key=lambda r: (r["delta_units"], r["sku_id"]))
    started.sort(key=lambda r: (-r["units_now"], r["sku_id"]))
    stopped.sort(key=lambda r: (-r["units_before"], r["sku_id"]))

    return {
        "available": True,
        "days_of_history": days_spanned,
        "this_week": {"from": recent[0], "to": recent[-1]},
        "last_week": {"from": before[0], "to": before[-1]},
        "rising": rising[:top],
        "falling": falling[:top],
        "started_selling": started[:top],
        "stopped_selling": stopped[:top],
        "rising_total": len(rising),
        "falling_total": len(falling),
        "started_total": len(started),
        "stopped_total": len(stopped),
        "too_few_to_judge": too_few,
        "min_units_to_judge": MIN_UNITS_FOR_MOVEMENT,
        "method": (
            f"packets billed in the seven complete days to {recent[-1]} against "
            f"the seven before, counted off the same chain. A product under "
            f"{MIN_UNITS_FOR_MOVEMENT} packets across both weeks is not called "
            f"a mover."
        ),
    }


# ------------------------------------------------------- block: anomalies --

def _flagged(value: int, baseline: int, spread: int) -> bool:
    """All three tests, or it is not an anomaly. See the note on the constants.

    Written as a multiplication rather than "how many spreads out is this",
    which would be a division and would round a borderline case into or out of
    a flag depending on which way the floor fell.
    """
    deviation = abs(value - baseline)
    if deviation < ANOMALY_MIN_DEVIATION_PAISE:
        return False
    if deviation < ANOMALY_MAD_MULTIPLE * spread:
        return False
    pct = _pct_of(deviation, baseline)
    if pct is None:
        # No baseline to be a percentage of: a run of zero days followed by a
        # sale is a shop opening, not an anomaly, so it is left alone.
        return False
    return pct >= ANOMALY_MIN_DEVIATION_PCT


def _anomaly_row(kind: str, key: str, label: str, value: int, baseline: int,
                 spread: int, samples: int, method: str) -> dict[str, Any]:
    deviation = value - baseline
    away = abs(deviation)
    # SIGNED, to match `deviation_paise` beside it. A day 100% below its
    # baseline and a day 100% above it are not the same fact, and a reader
    # pairing the two fields must not have to work out which from a word.
    pct = _pct_of(deviation, baseline)
    # How many spreads out, in tenths, so "6.3 spreads" can be printed without a
    # float ever existing. None when the spread is zero — half the days were
    # identical and the multiple is not a number.
    mads_x10 = (away * 10) // spread if spread > 0 else None
    direction = "above" if deviation > 0 else "below"
    spread_words = (
        f"and half of them within {_rs(spread)} of it"
        if spread > 0 else "and half of them exactly on it"
    )
    sentence = (
        f"{label} took {_rs(value)}. {method.capitalize()} is "
        f"{_rs(baseline)}, {spread_words}. This is "
        f"{_rs(away)} {direction} that"
        + (f" — {abs(pct)}% of the baseline" if pct is not None else "")
        + (f", {mads_x10 // 10}.{mads_x10 % 10} times the usual spread."
           if mads_x10 is not None else ".")
    )
    return {
        "kind": kind,
        "key": key,
        "label": label,
        "value_paise": value,
        "value_rupees": to_rupees_str(value),
        "baseline_paise": baseline,
        "baseline_rupees": to_rupees_str(baseline),
        "baseline_method": method,
        "spread_paise": spread,
        "spread_rupees": to_rupees_str(spread),
        "deviation_paise": deviation,
        "deviation_rupees": to_rupees_str(deviation),
        "deviation_pct": pct,
        "deviation_spreads_x10": mads_x10,
        "direction": direction,
        "samples": samples,
        "sentence": sentence,
    }


def _anomalies_block(eligible: list[dict[str, Any]],
                     by_day_hour: dict[str, dict[int, int]]) -> dict[str, Any]:
    """A day, or an hour, far from ITS OWN baseline — with the baseline shown.

    Two scans, and they answer different questions:

      * a DAY against the other days in the window. "Thursday was unlike this
        counter's other Thursdays-and-everything-elses."
      * an HOUR of the most recent COMPLETE trading day against the same hour on
        the other days. "Six o'clock yesterday was dead, and six o'clock is
        normally this shop's best hour." That is the one a shopkeeper can act
        on, and it is invisible in a daily total.

    The most recent complete day is used rather than today, because an hour that
    is still running has not finished taking money and would be flagged every
    afternoon.
    """
    trading = [d for d in eligible if d["bills"] > 0]
    if len(trading) < MIN_TRADING_DAYS_FOR_BASELINE:
        block = _not_enough(len(trading), MIN_TRADING_DAYS_FOR_BASELINE,
                            "An anomaly is a distance from a baseline, and a "
                            "baseline", "days that took money")
        block["days"] = []
        block["hours"] = []
        return block

    method_note = {
        "baseline": "the median of the other days in the window",
        "spread": "the median absolute deviation from that median",
        "flagged_when": (
            f"all three hold: at least {ANOMALY_MAD_MULTIPLE} times the spread, "
            f"at least {ANOMALY_MIN_DEVIATION_PCT}% of the baseline, and at "
            f"least {_rs(ANOMALY_MIN_DEVIATION_PAISE)} in money"
        ),
        "why_median": (
            "A mean is dragged by the very day being tested, so an unusual day "
            "helps define the baseline it is measured against and then looks "
            "less unusual than it was. A median does not move."
        ),
    }

    # ---- days -------------------------------------------------------------
    #
    # COMPLETE days only, on both sides. A day still running is not yet a day,
    # so it is neither tested nor allowed into the baseline the others are
    # tested against — half of today's takings sitting in the median would drag
    # every other day's comparison a little, every afternoon.
    day_rows: list[dict[str, Any]] = []
    checked_days = 0
    values = {d["date"]: int(d["revenue_paise"]) for d in eligible if d["complete"]}
    for d in eligible:
        if not d["complete"]:
            continue
        others = [v for label, v in values.items() if label != d["date"]]
        if len(others) < MIN_TRADING_DAYS_FOR_BASELINE:
            continue
        checked_days += 1
        baseline = _median(others)
        spread = _mad(others, baseline)
        value = int(d["revenue_paise"])
        if not _flagged(value, baseline, spread):
            continue
        day_rows.append(_anomaly_row(
            "day", d["date"], str(d["short"]), value, baseline, spread,
            len(others), f"the median of the other {len(others)} days in this window"))
    day_rows.sort(key=lambda r: (-abs(int(r["deviation_paise"])), r["key"]))

    # ---- hours of the most recent complete trading day ---------------------
    hour_rows: list[dict[str, Any]] = []
    subject = None
    for d in reversed(eligible):
        if d["complete"] and d["bills"] > 0:
            subject = d
            break

    checked_hours = 0
    if subject is not None:
        others_labels = [d["date"] for d in eligible
                         if d["complete"] and d["date"] != subject["date"]]
        subject_hours = by_day_hour.get(subject["date"], {})
        for hour in range(24):
            samples = [int(by_day_hour.get(label, {}).get(hour, 0))
                       for label in others_labels]
            if len(samples) < MIN_TRADING_DAYS_FOR_BASELINE:
                continue
            baseline = _median(samples)
            # An hour that is normally dead everywhere is not a baseline worth
            # testing against: it would flag the single evening somebody bought
            # milk at eleven, which is not information.
            if baseline == 0:
                continue
            checked_hours += 1
            value = int(subject_hours.get(hour, 0))
            spread = _mad(samples, baseline)
            if not _flagged(value, baseline, spread):
                continue
            hour_rows.append(_anomaly_row(
                "hour", f"{subject['date']}T{hour:02d}",
                f"{subject['short']}, {_hour_label(hour)}",
                value, baseline, spread, len(samples),
                f"the median of {_hour_label(hour)} across the other "
                f"{len(samples)} days in this window"))
    hour_rows.sort(key=lambda r: (-abs(int(r["deviation_paise"])), r["key"]))

    return {
        "available": True,
        "days_of_history": len(trading),
        "days": day_rows[:MAX_DAY_ANOMALIES],
        "hours": hour_rows[:MAX_HOUR_ANOMALIES],
        "days_found": len(day_rows),
        "hours_found": len(hour_rows),
        "days_checked": checked_days,
        "hours_checked": checked_hours,
        "subject_day": None if subject is None else subject["date"],
        "subject_day_short": None if subject is None else subject["short"],
        "method": method_note,
        "nothing_found_note": (
            "Nothing was far enough from its own baseline to report. That is a "
            "result: this counter's days have been ordinary."
        ),
    }


# ------------------------------------------------------------- the routes --
#
# Every one of these folds the chain ONCE through `_assemble` and returns a
# slice of it. A sub-endpoint that computed its own block could disagree with
# the same block inside `/insights` at a midnight boundary, and two screens
# showing two different Tuesdays is exactly the failure this module was written
# to avoid.

def _respond(day: Optional[str], days: Optional[str], top: Optional[str],
             pick: Optional[str] = None) -> JSONResponse:
    try:
        want_day = _require_day(day)
        want_days = _require_days(days)
        want_top = _require_top(top)
        payload = _assemble(want_day, want_days, want_top)
        body: dict[str, Any] = {
            "ok": True,
            "settles_money": False,
            "window": payload["window"],
            "history": payload["history"],
            "chain": payload["chain"],
            "derived_from": payload["derived_from"],
        }
        if pick is None:
            body.update({k: v for k, v in payload.items() if k not in body})
        else:
            body[pick] = payload[pick]
            body["limits"] = payload["limits"]
        return JSONResponse(body)
    except InsightsRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/insights")
def insights_ep(day: str | None = None, days: str | None = None,
                top: str | None = None) -> JSONResponse:
    """Everything this counter can honestly say about its own last N days."""
    return _respond(day, days, top)


@router.get("/insights/days")
def insights_days_ep(day: str | None = None, days: str | None = None) -> JSONResponse:
    """One figure per day, and the median day beside them."""
    return _respond(day, days, None, "days")


@router.get("/insights/week")
def insights_week_ep(day: str | None = None, days: str | None = None) -> JSONResponse:
    """Seven complete days against the seven complete days before them."""
    return _respond(day, days, None, "week")


@router.get("/insights/weekday")
def insights_weekday_ep(day: str | None = None, days: str | None = None) -> JSONResponse:
    """This Tuesday against the last four Tuesdays, cut at the same clock time."""
    return _respond(day, days, None, "same_weekday")


@router.get("/insights/hours")
def insights_hours_ep(day: str | None = None, days: str | None = None) -> JSONResponse:
    """When this counter is actually busy."""
    return _respond(day, days, None, "hours")


@router.get("/insights/products")
def insights_products_ep(day: str | None = None, days: str | None = None,
                         top: str | None = None) -> JSONResponse:
    """What is selling more than it was, and what has gone quiet."""
    return _respond(day, days, top, "products")


@router.get("/insights/anomalies")
def insights_anomalies_ep(day: str | None = None, days: str | None = None) -> JSONResponse:
    """A day or an hour far from its own baseline, with the baseline shown."""
    return _respond(day, days, None, "anomalies")


__all__ = [
    "InsightsRefused",
    "MIN_DAYS_FOR_PRODUCTS",
    "MIN_DAYS_FOR_WEEK",
    "MIN_SAME_WEEKDAY_SAMPLES",
    "MIN_TRADING_DAYS_FOR_BASELINE",
    "MIN_TRADING_DAYS_FOR_HOURS",
    "MIN_UNITS_FOR_MOVEMENT",
    "NOT_ENOUGH",
    "router",
]
