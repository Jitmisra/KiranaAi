"""gawaah/expenses.py — the shop's outgoings and the cash drawer.

Money coming IN is witnessed by a camera, a hash chain and a signed webhook.
Money going OUT is a shopkeeper typing what he paid the chaiwala. There is no
sensor for that and there never will be, so the only thing that makes these
numbers worth anything is that the module is strict about the four ways it could
quietly become useless:

  1. IT COULD TURN A RUPEE INTO A FLOAT. Every amount below is integer paise,
     entered either as whole paise or as the rupee STRING the shopkeeper typed,
     parsed by `money.from_rupees_str`, which never touches a float. `12.345` is
     refused rather than rounded, because sub-paisa precision is not money.

  2. IT COULD INVENT A CASH SALE. "Cash" here is not a flag anybody sets — it is
     the day's closed bills that the hash-chained audit log does NOT record the
     gateway settling. The tests below write real chains with
     `gawaah.ledger.Ledger` and assert the split comes off them, including that
     a bill which never closed is not a sale and yesterday's bill is not today's
     cash.

  3. IT COULD HIDE A CORRECTION. There is no delete. A voided expense keeps its
     id, its amount and its note, stops counting in every total, and is still
     returned in the list.

  4. IT COULD ACCUSE SOMEBODY. A drawer that does not match is a difference and
     is described as a difference. A test reads the shipped copy and fails on
     the vocabulary of blame.

Every named refusal in the module has a test here. Nothing in this file talks to
a gateway, and no response any of it produces settles money.
"""
from __future__ import annotations

import ast
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import expenses, manage  # noqa: E402
from gawaah.expenses import (  # noqa: E402
    CATEGORIES,
    MAX_CASH_PAISE,
    MAX_EXPENSE_PAISE,
    MAX_NOTE,
    R_ALREADY_VOID,
    R_AMOUNT_NOT_INTEGER,
    R_AMOUNT_NOT_POSITIVE,
    R_AMOUNT_TOO_LARGE,
    R_AMOUNT_TWICE,
    R_BAD_BODY,
    R_BAD_CATEGORY,
    R_BAD_DAY,
    R_BAD_EXPENSE_ID,
    R_BAD_LIMIT,
    R_BAD_PAID_WITH,
    R_BAD_RUPEES,
    R_CASH_NEGATIVE,
    R_CASH_TOO_LARGE,
    R_DAY_IN_FUTURE,
    R_INTERNAL,
    R_NO_AMOUNT,
    R_NO_BILL_BOOK,
    R_NO_CATEGORY,
    R_NO_EXPENSE,
    R_NO_TILL,
    R_NO_VOID_REASON,
    R_NOT_WRITTEN,
    R_NOTE_REQUIRED,
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
    app.include_router(expenses.router)
    return TestClient(app)


def _today() -> str:
    return expenses._today_label()


def _yesterday() -> str:
    tz = datetime.now().astimezone().tzinfo
    return (datetime.now(tz) - timedelta(days=1)).strftime("%Y-%m-%d")


def _tomorrow() -> str:
    tz = datetime.now().astimezone().tzinfo
    return (datetime.now(tz) + timedelta(days=1)).strftime("%Y-%m-%d")


def _at(day: str, hour: int = 12, second: int = 0) -> str:
    """Noon on `day` in the counter's own timezone, as the ledger stamps it.

    Noon and not midnight: the module's day window is local midnight to local
    midnight, and a fixture that writes bills at 00:00 would be testing the
    boundary by accident on every run instead of on purpose.
    """
    tz = datetime.now().astimezone().tzinfo
    base = datetime.strptime(day, "%Y-%m-%d").replace(hour=hour, tzinfo=tz)
    return (base + timedelta(seconds=second)).isoformat()


def _bill(session_id: str, amount: int, *, day: str | None = None,
          settle: bool = False, close: bool = True, hour: int = 12) -> None:
    """Write one session into the REAL chain, the way the real modules write it.

    The event names and reason strings are the ones `gawaah/manage.py` folds on
    — `session/exit`, `session/done`, `session/webhook` with `settled_green` —
    so this fixture exercises the shipped definition of "the gateway settled
    it" rather than a private one invented for the test.
    """
    day = day or _today()
    led = Ledger(manage.ledger_path())
    led.append(ts=_at(day, hour, 0), module="session", event="exit",
               session_id=session_id, reason="exit_crossing_committed",
               item_id="parle_g#0", price_paise=amount, abstained=False,
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


def _spend(client: TestClient, **over) -> dict:
    body = {"amount_paise": 5000, "category": "tea", "note": "chai"}
    body.update(over)
    r = client.post("/expenses", json=body)
    assert r.status_code == 200, r.text
    return r.json()["expense"]


# ============================================================== the categories


def test_the_category_list_is_short_and_always_carries_other(client):
    r = client.get("/expenses/categories")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["settles_money"] is False
    names = [c["category"] for c in body["categories"]]
    assert names == list(CATEGORIES)
    assert "other" in names
    for wanted in ("rent", "electricity", "wages", "tea"):
        assert wanted in names
    # Every category carries a label a shopkeeper would say out loud.
    assert all(c["label"] and c["label"] != c["category"].upper()
               for c in body["categories"])
    assert body["paid_with"] == ["cash", "bank"]


# ============================================================ recording a cost


def test_an_expense_is_recorded_in_integer_paise(client):
    row = _spend(client, amount_paise=12050, category="tea", note="chai")
    assert row["expense_id"].startswith("exp_")
    assert row["amount_paise"] == 12050
    assert isinstance(row["amount_paise"], int)
    assert row["amount_rupees"] == "120.50"
    assert row["category"] == "tea"
    assert row["category_label"] == "Tea and snacks"
    assert row["void"] is False


def test_rupees_may_be_typed_as_a_string_and_never_become_a_float(client):
    """12.10 * 100 is 1209.9999999999998 in every browser on earth.

    So the shopkeeper's rupee string is parsed on the server by
    `money.from_rupees_str`, which multiplies integers and never divides.
    """
    row = _spend(client, amount_paise=None, amount_rupees="12.10",
                 category="transport", note="tempo")
    assert row["amount_paise"] == 1210
    assert row["amount_rupees"] == "12.10"


def test_the_defaults_are_today_and_out_of_the_drawer(client):
    row = _spend(client)
    assert row["day"] == _today()
    assert row["paid_with"] == "cash"


def test_a_bank_transfer_is_recorded_as_one(client):
    row = _spend(client, category="rent", amount_paise=2500000,
                 paid_with="bank", note="September")
    assert row["paid_with"] == "bank"
    assert row["amount_rupees"] == "25000.00"


def test_an_expense_is_written_beside_the_catalogue_and_not_into_results(client):
    row = _spend(client)
    path = expenses.expenses_dir() / f"{row['expense_id']}.json"
    assert path.exists()
    assert str(path).startswith(str(expenses.shop_dir()))
    assert "results" not in str(path).split("shop")[0]
    on_disk = json.loads(path.read_text())
    assert on_disk["amount_paise"] == 5000
    assert isinstance(on_disk["amount_paise"], int)


def test_the_day_book_has_its_own_verifiable_chain_not_the_money_ledger(client):
    """Invariant: paisa holds results/audit.jsonl open as sole writer.

    A second appender there gives it a stale head and every line it writes
    afterwards fails verification. So the day book gets its own chain, under the
    shop directory, verified by exactly the same `verify()`.
    """
    _spend(client)
    _spend(client, category="wages", amount_paise=50000, note="Ramu")
    chain = expenses.audit_path()
    assert chain.name == "expenses.audit.jsonl"
    assert chain.parent == expenses.shop_dir()
    assert chain != manage.ledger_path()
    ok, lines, head, error = verify(chain)
    assert ok is True and error is None
    assert lines == 2


def test_the_note_is_not_in_the_chain_only_a_digest_of_it(client):
    """An audit log is the file most likely to end up in a bug report."""
    _spend(client, category="wages", amount_paise=50000,
           note="gave Ramu 500 for the week")
    text = expenses.audit_path().read_text()
    assert "Ramu" not in text
    line = json.loads(text.splitlines()[0])
    assert line["event"] == "expense.recorded"
    assert line["amount_paise"] == 50000
    assert len(line["note_sha256"]) == 64
    assert line["note_len"] == len("gave Ramu 500 for the week")


def test_recording_reports_whether_it_was_audited(client, monkeypatch):
    """`audited` is a fact, not decoration: a failed chain write says so."""
    body = client.post("/expenses",
                       json={"amount_paise": 100, "category": "tea"}).json()
    assert body["audited"] is True
    monkeypatch.setattr(expenses, "_audit", lambda *a, **k: None)
    body = client.post("/expenses",
                       json={"amount_paise": 100, "category": "tea"}).json()
    assert body["ok"] is True
    assert body["audited"] is False


# =========================================== every refusal on the way in


def test_a_body_that_is_not_json_is_refused_by_name(client):
    r = client.post("/expenses", content=b"rent 5000",
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_BODY
    assert r.json()["settles_money"] is False


def test_a_json_body_that_is_not_an_object_is_refused_by_name(client):
    r = client.post("/expenses", json=[1, 2, 3])
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_BODY
    assert "list" in r.json()["detail"]


def test_an_expense_with_no_amount_is_refused(client):
    r = client.post("/expenses", json={"category": "tea"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_NO_AMOUNT


def test_an_amount_given_twice_is_refused_rather_than_resolved(client):
    """Two numbers that disagree have no correct winner."""
    r = client.post("/expenses", json={"amount_paise": 5000,
                                       "amount_rupees": "60.00",
                                       "category": "tea"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_AMOUNT_TWICE


def test_a_float_amount_is_refused_and_not_rounded(client):
    r = client.post("/expenses", json={"amount_paise": 120.5,
                                       "category": "tea"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_AMOUNT_NOT_INTEGER
    assert "12050" in r.json()["detail"]


def test_a_boolean_amount_is_refused(client):
    """True is an int in Python and one paisa is never what anybody meant."""
    r = client.post("/expenses", json={"amount_paise": True,
                                       "category": "tea"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_AMOUNT_NOT_INTEGER


def test_a_rupee_string_with_sub_paisa_precision_is_refused(client):
    r = client.post("/expenses", json={"amount_rupees": "12.345",
                                       "category": "tea"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_RUPEES


def test_a_rupee_field_that_is_not_a_string_is_refused(client):
    r = client.post("/expenses", json={"amount_rupees": 12.5,
                                       "category": "tea"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_RUPEES


def test_an_expense_of_zero_is_refused(client):
    r = client.post("/expenses", json={"amount_paise": 0, "category": "tea"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_AMOUNT_NOT_POSITIVE


def test_a_negative_expense_is_refused(client):
    r = client.post("/expenses", json={"amount_paise": -100,
                                       "category": "tea"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_AMOUNT_NOT_POSITIVE


def test_an_expense_past_the_cap_is_refused_as_a_probable_typo(client):
    r = client.post("/expenses", json={"amount_paise": MAX_EXPENSE_PAISE + 1,
                                       "category": "stock"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_AMOUNT_TOO_LARGE
    assert "paise were typed where rupees were meant" in r.json()["detail"]


def test_an_expense_with_no_category_is_refused(client):
    r = client.post("/expenses", json={"amount_paise": 5000})
    assert r.status_code == 400
    assert r.json()["reason"] == R_NO_CATEGORY
    assert "tea" in r.json()["detail"]


def test_a_category_this_shop_does_not_keep_is_refused(client):
    r = client.post("/expenses", json={"amount_paise": 5000,
                                       "category": "marketing"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_CATEGORY
    assert "other" in r.json()["detail"]


def test_other_without_a_note_is_refused(client):
    """A month of unlabelled 'other' is a number nobody can act on."""
    r = client.post("/expenses", json={"amount_paise": 5000,
                                       "category": "other"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_NOTE_REQUIRED

    ok = client.post("/expenses", json={"amount_paise": 5000,
                                        "category": "other",
                                        "note": "broom for the shop"})
    assert ok.status_code == 200


def test_a_note_past_the_cap_is_refused(client):
    r = client.post("/expenses", json={"amount_paise": 5000, "category": "tea",
                                       "note": "x" * (MAX_NOTE + 1)})
    assert r.status_code == 400
    assert r.json()["reason"] == R_NOTE_TOO_LONG


def test_paid_with_anything_but_cash_or_bank_is_refused(client):
    r = client.post("/expenses", json={"amount_paise": 5000, "category": "tea",
                                       "paid_with": "credit"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_PAID_WITH


def test_a_day_that_is_not_a_calendar_date_is_refused(client):
    for bad in ("yesterday", "01-09-2026", "2026-13-01", 20260901):
        r = client.post("/expenses", json={"amount_paise": 5000,
                                           "category": "tea", "day": bad})
        assert r.status_code == 400, bad
        assert r.json()["reason"] == R_BAD_DAY, bad


def test_a_day_in_the_future_is_refused(client):
    r = client.post("/expenses", json={"amount_paise": 5000, "category": "tea",
                                       "day": _tomorrow()})
    assert r.status_code == 400
    assert r.json()["reason"] == R_DAY_IN_FUTURE


# ================================================================ the day book


def test_a_day_with_nothing_in_it_is_zero_and_not_an_error(client):
    r = client.get("/expenses/day")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 0
    assert body["total_paise"] == 0
    assert body["total_rupees"] == "0.00"
    assert body["by_category"] == []


def test_the_day_summary_groups_by_category_and_totals_in_integers(client):
    _spend(client, category="tea", amount_paise=3000, note="morning")
    _spend(client, category="tea", amount_paise=2000, note="evening")
    _spend(client, category="wages", amount_paise=50000, note="Ramu")
    body = client.get("/expenses/day").json()

    assert body["day"] == _today()
    assert body["count"] == 3
    assert body["total_paise"] == 55000
    assert body["total_rupees"] == "550.00"
    # Biggest first, so the page draws the thing worth looking at at the top.
    assert [b["category"] for b in body["by_category"]] == ["wages", "tea"]
    tea = [b for b in body["by_category"] if b["category"] == "tea"][0]
    assert tea["count"] == 2
    assert tea["paise"] == 5000
    assert tea["rupees"] == "50.00"
    assert tea["label"] == "Tea and snacks"


def test_cash_and_bank_are_totalled_apart(client):
    _spend(client, category="tea", amount_paise=3000, note="chai")
    _spend(client, category="rent", amount_paise=2500000, paid_with="bank",
           note="September")
    body = client.get("/expenses/day").json()
    assert body["total_paise"] == 2503000
    assert body["cash_paise"] == 3000
    assert body["cash_count"] == 1
    assert body["bank_paise"] == 2500000
    assert body["bank_count"] == 1


def test_a_day_summary_only_counts_that_day(client):
    _spend(client, amount_paise=3000)
    _spend(client, amount_paise=7000, day=_yesterday())
    assert client.get("/expenses/day").json()["total_paise"] == 3000
    assert client.get(
        f"/expenses/day?day={_yesterday()}").json()["total_paise"] == 7000


def test_the_list_is_newest_business_day_first_and_filters_by_day(client):
    """Filed under the day it is ABOUT, not the day it was typed.

    `old` is entered LAST and dated yesterday. A shopkeeper who catches up on
    Monday expects Saturday's rent under Saturday, not at the top of today.
    """
    first = _spend(client, amount_paise=100, note="one")
    second = _spend(client, amount_paise=200, note="two")
    old = _spend(client, amount_paise=300, day=_yesterday(), note="old")

    body = client.get("/expenses").json()
    assert body["count"] == 3
    ids = [e["expense_id"] for e in body["expenses"]]
    assert ids == [second["expense_id"], first["expense_id"],
                   old["expense_id"]]

    only_old = client.get(f"/expenses?day={_yesterday()}").json()
    assert only_old["count"] == 1
    assert only_old["expenses"][0]["expense_id"] == old["expense_id"]


def test_a_capped_list_says_it_was_capped(client):
    for i in range(4):
        _spend(client, amount_paise=100 + i)
    body = client.get("/expenses?limit=2").json()
    assert body["count"] == 2
    assert body["total_on_record"] == 4
    assert body["truncated"] is True
    # The totals describe the rows RETURNED, so a capped page never reads as a
    # complete day.
    assert body["total_paise"] == sum(
        e["amount_paise"] for e in body["expenses"])


def test_a_limit_that_is_not_a_positive_whole_number_is_refused(client):
    for bad in ("0", "-1", "many", "2.5"):
        r = client.get(f"/expenses?limit={bad}")
        assert r.status_code == 400, bad
        assert r.json()["reason"] == R_BAD_LIMIT, bad


def test_one_unreadable_file_does_not_hide_the_rest(client):
    good = _spend(client, amount_paise=4200)
    (expenses.expenses_dir() / "exp_deadbeefcafe.json").write_text("{ not json")
    body = client.get("/expenses/day").json()
    assert body["count"] == 1
    assert body["expenses"][0]["expense_id"] == good["expense_id"]


# ==================================================================== voiding


def test_a_void_keeps_the_row_and_stops_it_counting(client):
    row = _spend(client, amount_paise=9900, note="typed twice")
    r = client.post(f"/expenses/{row['expense_id']}/void",
                    json={"reason": "entered twice"})
    assert r.status_code == 200
    voided = r.json()["expense"]
    assert voided["void"] is True
    assert voided["void_reason"] == "entered twice"
    assert voided["amount_paise"] == 9900
    assert voided["note"] == "typed twice"

    body = client.get("/expenses/day").json()
    assert body["total_paise"] == 0
    assert body["count"] == 0
    assert body["voided_count"] == 1
    assert body["voided_paise"] == 9900
    # Still listed. A correction that makes the original disappear is
    # indistinguishable from an edit.
    assert [e["expense_id"] for e in body["expenses"]] == [row["expense_id"]]


def test_a_void_is_appended_to_the_chain(client):
    row = _spend(client, amount_paise=9900)
    client.post(f"/expenses/{row['expense_id']}/void", json={"reason": "oops"})
    lines = [json.loads(x)
             for x in expenses.audit_path().read_text().splitlines()]
    assert [x["event"] for x in lines] == ["expense.recorded", "expense.voided"]
    ok, _, _, error = verify(expenses.audit_path())
    assert ok is True and error is None


def test_a_void_with_no_reason_is_refused(client):
    row = _spend(client)
    for body in ({}, {"reason": ""}, {"reason": "   "}, {"reason": 7}):
        r = client.post(f"/expenses/{row['expense_id']}/void", json=body)
        assert r.status_code == 400, body
        assert r.json()["reason"] == R_NO_VOID_REASON, body


def test_a_void_reason_past_the_cap_is_refused(client):
    row = _spend(client)
    r = client.post(f"/expenses/{row['expense_id']}/void",
                    json={"reason": "x" * 400})
    assert r.status_code == 400
    assert r.json()["reason"] == R_NOTE_TOO_LONG


def test_voiding_twice_is_refused(client):
    row = _spend(client)
    first = client.post(f"/expenses/{row['expense_id']}/void",
                        json={"reason": "one"})
    assert first.status_code == 200
    second = client.post(f"/expenses/{row['expense_id']}/void",
                         json={"reason": "two"})
    assert second.status_code == 400
    assert second.json()["reason"] == R_ALREADY_VOID


def test_an_expense_this_shop_does_not_have_is_a_404_by_name(client):
    r = client.post("/expenses/exp_0123456789ab/void", json={"reason": "x"})
    assert r.status_code == 404
    assert r.json()["reason"] == R_NO_EXPENSE


def test_an_id_that_is_not_from_this_shop_never_reaches_a_path(client):
    """The id becomes a filename, so it is charset-checked before it is joined."""
    r = client.post("/expenses/catalog/void", json={"reason": "x"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_EXPENSE_ID

    for evil in ("../../catalog", "exp_../../x", "exp_ZZZZZZZZZZZZ", ""):
        with pytest.raises(expenses.ExpenseRefused) as caught:
            expenses._valid_expense_id(evil)
        assert caught.value.reason == R_BAD_EXPENSE_ID


# ============================================================== the cash drawer


def test_an_opening_and_a_closing_count_are_recorded(client):
    r = client.post("/cash/opening", json={"counted_paise": 200000})
    assert r.status_code == 200
    assert r.json()["counted_paise"] == 200000
    assert r.json()["counted_rupees"] == "2000.00"

    r = client.post("/cash/closing", json={"counted_rupees": "2032.40",
                                           "note": "all in fifties"})
    assert r.status_code == 200
    assert r.json()["counted_paise"] == 203240

    body = client.get("/cash").json()
    assert body["opening"]["counted"] is True
    assert body["opening"]["rupees"] == "2000.00"
    assert body["counted_closing"]["counted"] is True
    assert body["counted_closing"]["note"] == "all in fifties"


def test_an_empty_drawer_is_a_count_and_an_uncounted_one_is_not(client):
    """Zero and 'not counted yet' look identical in a total and are not."""
    before = client.get("/cash").json()
    assert before["opening"]["counted"] is False
    assert before["opening"]["paise"] is None

    client.post("/cash/opening", json={"counted_paise": 0})
    after = client.get("/cash").json()
    assert after["opening"]["counted"] is True
    assert after["opening"]["paise"] == 0
    assert after["opening"]["rupees"] == "0.00"


def test_a_hand_edited_count_that_is_not_integer_paise_reads_as_uncounted(
        client):
    """The cash file is the one thing here a person edits with a text editor.

    `2032.40` typed into it must not become the drawer figure. Saying "not
    counted" is honest; showing 2032.4 rupees of something is not.
    """
    client.post("/cash/opening", json={"counted_paise": 200000})
    path = expenses.cash_dir() / f"{_today()}.json"
    doc = json.loads(path.read_text())
    doc["opening_paise"] = 2032.40
    path.write_text(json.dumps(doc))

    body = client.get("/cash").json()
    assert body["opening"]["counted"] is False
    assert body["opening"]["paise"] is None
    assert body["expected_closing_paise"] is None


def test_a_negative_count_is_refused(client):
    r = client.post("/cash/opening", json={"counted_paise": -1})
    assert r.status_code == 400
    assert r.json()["reason"] == R_CASH_NEGATIVE


def test_a_count_past_the_cap_is_refused(client):
    r = client.post("/cash/closing", json={"counted_paise": MAX_CASH_PAISE + 1})
    assert r.status_code == 400
    assert r.json()["reason"] == R_CASH_TOO_LARGE


def test_a_closing_count_with_no_amount_is_refused(client):
    r = client.post("/cash/closing", json={"note": "forgot the number"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_NO_AMOUNT


def test_a_closing_note_past_the_cap_is_refused(client):
    r = client.post("/cash/closing", json={"counted_paise": 100,
                                           "note": "x" * (MAX_NOTE + 1)})
    assert r.status_code == 400
    assert r.json()["reason"] == R_NOTE_TOO_LONG


def test_a_count_that_cannot_be_written_is_refused_not_reported(client,
                                                                monkeypatch):
    def _no(*_a, **_k):
        raise PermissionError("read-only file system")

    monkeypatch.setattr(expenses, "_write_json", _no)
    r = client.post("/cash/opening", json={"counted_paise": 100})
    assert r.status_code == 400
    assert r.json()["reason"] == R_NOT_WRITTEN
    assert "Nothing was recorded" in r.json()["detail"]


def test_an_expense_that_cannot_be_written_is_refused_not_reported(client,
                                                                   monkeypatch):
    def _no(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(expenses, "_write_json", _no)
    r = client.post("/expenses", json={"amount_paise": 100, "category": "tea"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_NOT_WRITTEN


# ========================================== the cash position, off the chain


def test_the_cash_position_is_opening_plus_unsettled_sales_minus_cash_spend(
        client):
    """The whole point of the screen, in one arithmetic assertion.

    Two bills the gateway never confirmed (42.90 and 39.50), one it did
    (100.00), fifty rupees of chai out of the drawer and twenty-five thousand of
    rent off the bank account.
    """
    _bill("s_cash_a", 4290)
    _bill("s_cash_b", 3950)
    _bill("s_gateway", 10000, settle=True)
    _spend(client, category="tea", amount_paise=5000, note="chai")
    _spend(client, category="rent", amount_paise=2500000, paid_with="bank",
           note="September")
    client.post("/cash/opening", json={"counted_paise": 200000})

    body = client.get("/cash").json()
    assert body["ok"] is True
    assert body["settles_money"] is False
    assert body["cash_sales"]["bills"] == 2
    assert body["cash_sales"]["paise"] == 8240
    assert body["gateway_sales"]["bills"] == 1
    assert body["gateway_sales"]["paise"] == 10000
    assert body["gateway_sales"]["settled_by"] == {"webhook": 1}
    assert body["cash_expenses"]["paise"] == 5000
    assert body["bank_expenses"]["paise"] == 2500000
    assert body["movement_paise"] == 8240 - 5000
    assert body["expected_closing_paise"] == 200000 + 8240 - 5000
    assert body["expected_closing_rupees"] == "2032.40"
    assert body["chain"]["ok"] is True


def test_a_gateway_settled_bill_is_never_counted_as_cash(client):
    """Invariant 2 read backwards: green means the money did not come to hand."""
    _bill("s1", 12345, settle=True)
    body = client.get("/cash").json()
    assert body["cash_sales"]["paise"] == 0
    assert body["cash_sales"]["bills"] == 0
    assert body["gateway_sales"]["paise"] == 12345


def test_a_bill_that_never_closed_is_not_a_sale(client):
    """A customer who walked away mid-basket did not buy anything."""
    _bill("s_open", 9999, close=False)
    _bill("s_done", 1111)
    body = client.get("/cash").json()
    assert body["cash_sales"]["bills"] == 1
    assert body["cash_sales"]["paise"] == 1111


def test_yesterdays_bills_are_not_todays_cash(client):
    _bill("s_old", 5000, day=_yesterday())
    _bill("s_new", 700)
    assert client.get("/cash").json()["cash_sales"]["paise"] == 700
    yday = client.get(f"/cash?day={_yesterday()}").json()
    assert yday["cash_sales"]["paise"] == 5000


def test_a_bank_expense_does_not_come_out_of_the_drawer(client):
    client.post("/cash/opening", json={"counted_paise": 100000})
    _spend(client, category="rent", amount_paise=90000, paid_with="bank",
           note="September")
    body = client.get("/cash").json()
    assert body["bank_expenses"]["paise"] == 90000
    assert body["cash_expenses"]["paise"] == 0
    assert body["expected_closing_paise"] == 100000


def test_a_voided_expense_stops_moving_the_drawer(client):
    client.post("/cash/opening", json={"counted_paise": 100000})
    row = _spend(client, category="tea", amount_paise=4000, note="chai")
    assert client.get("/cash").json()["expected_closing_paise"] == 96000
    client.post(f"/expenses/{row['expense_id']}/void",
                json={"reason": "entered twice"})
    assert client.get("/cash").json()["expected_closing_paise"] == 100000


def test_without_an_opening_count_there_is_no_expected_figure(client):
    """A zero opening would read as the whole float being over."""
    _bill("s1", 8000)
    body = client.get("/cash").json()
    assert body["expected_closing_paise"] is None
    assert body["expected_closing_rupees"] is None
    assert body["difference_paise"] is None
    # What CAN be derived is still derived.
    assert body["movement_paise"] == 8000
    assert "Count the opening cash" in body["difference_note"]


def test_without_a_closing_count_nothing_is_compared(client):
    client.post("/cash/opening", json={"counted_paise": 100000})
    body = client.get("/cash").json()
    assert body["expected_closing_paise"] == 100000
    assert body["difference_paise"] is None
    assert body["difference_direction"] is None
    assert "Count the drawer" in body["difference_note"]


def test_a_drawer_that_matches_says_so_to_the_paisa(client):
    _bill("s1", 4290)
    client.post("/cash/opening", json={"counted_paise": 200000})
    client.post("/cash/closing", json={"counted_paise": 204290})
    body = client.get("/cash").json()
    assert body["difference_paise"] == 0
    assert body["difference_direction"] == "exact"
    assert body["difference_rupees"] == "0.00"


def test_a_short_drawer_is_reported_as_a_difference_and_nothing_else(client):
    _bill("s1", 4290)
    client.post("/cash/opening", json={"counted_paise": 200000})
    client.post("/cash/closing", json={"counted_paise": 204050})
    body = client.get("/cash").json()
    assert body["difference_paise"] == -240
    assert body["difference_rupees"] == "-2.40"
    assert body["difference_direction"] == "short"
    assert "not an accusation" in body["difference_note"]


def test_an_over_drawer_is_a_fact_too(client):
    client.post("/cash/opening", json={"counted_paise": 100000})
    client.post("/cash/closing", json={"counted_paise": 100500})
    body = client.get("/cash").json()
    assert body["difference_paise"] == 500
    assert body["difference_direction"] == "over"
    assert "not an accusation" in body["difference_note"]


def test_the_cash_screen_never_uses_the_vocabulary_of_blame(client):
    """A difference is a fact. The copy is part of the product, so it is tested.

    Every branch of the note is exercised — no counts, an over drawer and a
    short one — and the whole response body is read for the words that would
    turn a bookkeeping gap into an allegation about a person.
    """
    blame = ("theft", "stolen", "stealing", "missing", "unaccounted",
             "shortfall", "discrepanc", "suspicio", "blame", "fraud",
             "who took", "loss")
    _bill("s1", 4290)

    seen = []
    for setup in (
            None,
            {"counted_paise": 204500},   # over
            {"counted_paise": 204000},   # short
    ):
        if setup is not None:
            client.post("/cash/opening", json={"counted_paise": 200000})
            client.post("/cash/closing", json=setup)
        body = client.get("/cash").json()
        seen.append(body["difference_direction"])
        text = json.dumps(body).lower()
        for word in blame:
            assert word not in text, f"{word!r} in the cash screen copy"
    assert seen == [None, "over", "short"]


def test_a_broken_chain_shortens_the_sales_figure_and_the_page_says_so(client):
    """The failure mode that would otherwise look like a shopkeeper's mistake.

    `read_chain` serves the verified prefix and stops at the first link whose
    hash does not recompute. The bills after the break come straight off the
    cash sales figure, so the drawer reads OVER by exactly those bills — and a
    difference the counter caused must not be presented as a difference the
    shopkeeper has to explain.
    """
    _bill("s_first", 4000, hour=10)
    _bill("s_second", 6000, hour=11)
    path = manage.ledger_path()
    lines = path.read_text().splitlines()
    tampered = json.loads(lines[2])
    tampered["total_paise"] = 999999
    lines[2] = json.dumps(tampered, sort_keys=True, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n")
    manage._CHAIN_CACHE.clear()

    body = client.get("/cash").json()
    assert body["ok"] is True
    assert body["chain"]["ok"] is False
    assert body["cash_sales"]["paise"] == 4000
    assert body["chain_warning"] is not None
    assert "read over" in body["chain_warning"]
    assert "adjusted to hide" in body["chain_warning"]


def test_a_chain_that_verifies_carries_no_warning(client):
    _bill("s1", 4000)
    body = client.get("/cash").json()
    assert body["chain"]["ok"] is True
    assert body["chain_warning"] is None


def test_the_cash_sales_figure_states_what_it_cannot_see(client):
    """Stating the limit rather than implying it away.

    The unsettled column is mostly cash and it is not only cash. Anyone reading
    the number has to be told that in the same response, not in a document.
    """
    body = client.get("/cash").json()
    note = body["cash_sales_note"].lower()
    assert "upi" in note
    assert "cannot see" in note
    assert "not only cash" in note
    assert "hash-chained audit log" in body["derived_from"]


# ==================================== what happens when the neighbours are gone


def test_a_missing_till_is_a_named_refusal_not_a_crash(client, monkeypatch):
    """The day book lives beside the catalogue and will not guess where that is."""
    def _no():
        raise ImportError("no module named tools.upload_app")

    monkeypatch.setattr(expenses, "_TILL_NAMES", ("gawaah_no_such_till",))
    monkeypatch.setattr(expenses, "_import_till", _no)
    r = client.get("/expenses")
    assert r.status_code == 400
    assert r.json()["reason"] == R_NO_TILL
    assert r.json()["settles_money"] is False


def test_a_missing_bill_book_refuses_the_cash_position_and_nothing_else(
        client, monkeypatch):
    """Expenses still work. Only the figure that needs the chain is refused."""
    def _no():
        raise ImportError("no module named gawaah.manage")

    _spend(client, amount_paise=1000)
    monkeypatch.setattr(expenses, "_import_manage", _no)

    r = client.get("/cash")
    assert r.status_code == 400
    assert r.json()["reason"] == R_NO_BILL_BOOK

    still = client.get("/expenses/day")
    assert still.status_code == 200
    assert still.json()["total_paise"] == 1000


def test_an_unexpected_failure_is_a_named_400_and_never_a_500(client,
                                                              monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("the disk caught fire")

    monkeypatch.setattr(expenses, "_all_expenses", _boom)
    r = client.get("/expenses/day")
    assert r.status_code == 400
    assert r.json()["reason"] == R_INTERNAL
    assert "RuntimeError" in r.json()["detail"]


def test_an_absent_audit_chain_is_an_empty_day_not_an_error(client):
    """A counter installed this morning has no bills, and that is not a fault."""
    assert not manage.ledger_path().exists()
    body = client.get("/cash").json()
    assert body["ok"] is True
    assert body["cash_sales"]["paise"] == 0
    assert body["chain"]["exists"] is False
    assert body["chain"]["ok"] is True


def test_no_shaped_body_produces_a_500(client):
    """Every path answers with a named refusal, whatever is thrown at it."""
    bodies = [
        {}, [], "text", 7, None, True,
        {"amount_paise": "5000", "category": "tea"},
        {"amount_paise": {"x": 1}, "category": "tea"},
        {"amount_paise": 5000, "category": ["tea"]},
        {"amount_paise": 5000, "category": "tea", "note": 9},
        {"amount_paise": 5000, "category": "tea", "paid_with": 1},
        {"amount_paise": 5000, "category": "tea", "day": {"y": 2026}},
        {"amount_rupees": "", "category": "tea"},
        {"amount_rupees": "abc", "category": "tea"},
        {"counted_paise": "many"},
        {"counted_rupees": "1.234"},
    ]
    for path in ("/expenses", "/cash/opening", "/cash/closing",
                 "/expenses/exp_0123456789ab/void"):
        for body in bodies:
            r = client.post(path, json=body)
            assert r.status_code in (400, 404), (path, body, r.status_code)
            payload = r.json()
            assert payload["ok"] is False
            assert isinstance(payload["reason"], str) and payload["reason"]
            assert payload["settles_money"] is False


# ================================================================= invariants


def test_the_router_carries_no_prefix_and_every_path_is_absolute():
    """The orchestrator mounts it bare, so the paths here are what a page asks."""
    assert expenses.router.prefix == ""
    paths = sorted({r.path for r in expenses.router.routes})
    assert paths == [
        "/cash", "/cash/closing", "/cash/opening",
        "/expenses", "/expenses/categories", "/expenses/day",
        "/expenses/{expense_id}/void",
    ]
    assert all(p.startswith("/") for p in paths)


def test_the_module_contains_no_float_and_no_rounding():
    """Invariant 1, asserted against the shipped source rather than a habit.

    `tools/lint_no_float.py` covers this repo-wide; this pins the one file, so
    the day somebody adds `round(x, 2)` to make a total look tidy, the failure
    arrives here with a name.
    """
    tree = ast.parse(Path(expenses.__file__).read_text())
    for node in ast.walk(tree):
        assert not (isinstance(node, ast.Constant)
                    and isinstance(node.value, float)), \
            f"float literal at line {node.lineno}"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in ("float", "round"), \
                f"{node.func.id}() at line {node.lineno}"


def test_the_module_settles_no_money_and_mints_nothing():
    """It holds no gateway and constructs no payable string, by inspection."""
    source = Path(expenses.__file__).read_text().lower()
    for forbidden in ("upi:", "razorpay", "short_url", "payment_link",
                      "key_secret", "api.razorpay"):
        assert forbidden not in source, forbidden
    assert '"settles_money": false' in source


def test_the_copy_is_plain_and_carries_no_marketing_voice():
    """No exclamation mark in any string this module can put on a screen.

    Walked as an AST rather than grepped, so `!=` and the `!r` conversion inside
    an f-string are not mistaken for a raised voice — only the literal text is
    read.
    """
    tree = ast.parse(Path(expenses.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "!" not in node.value, f"line {node.lineno}: {node.value!r}"
