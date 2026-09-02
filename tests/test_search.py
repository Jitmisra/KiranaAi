"""gawaah/search.py — one box over the whole counter.

A search box is the easiest thing in a demo to fake and the easiest thing in a
shop to be quietly wrong. So this suite is organised around the five ways this
module could lie:

  1. It could not find something that is there        -> the finding tests
  2. It could find the WRONG thing confidently        -> the tolerance tests,
                                                         which pin what typo
                                                         tolerance must NOT do
                                                         to a barcode or an id
  3. It could rank by something it cannot explain     -> every result carries
                                                         `why`, and the order
                                                         is asserted, not
                                                         eyeballed
  4. It could go quiet when a file is unreadable      -> the source tests: one
                                                         broken file must not
                                                         empty the box, and
                                                         must never be silent
  5. It could get slower than the number it prints    -> the cost test measures
                                                         the published figure
                                                         on every run

Every fixture builds a REAL shop: products through the till's own enrolment,
orders through the storefront's own endpoint, bills through a real
hash-chained ledger. Nothing here asserts against data this file invented in a
shape the product does not use.
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

from gawaah import manage, search, storefront  # noqa: E402
from gawaah.ledger import Ledger  # noqa: E402
from gawaah.search import (  # noqa: E402
    CEILING_US_PER_1000_PRODUCTS,
    MAX_LIMIT,
    MAX_QUERY,
    R_BAD_KIND,
    R_BAD_LIMIT,
    R_BILLS_UNREADABLE,
    R_CATALOGUE_UNREADABLE,
    R_INTERNAL,
    R_LIMIT_RANGE,
    R_NO_QUERY,
    R_NO_TILL,
    R_NOTHING_TO_SEARCH,
    R_ORDERS_UNREADABLE,
    R_QUERY_TOO_LONG,
    SearchRefused,
    edit_distance,
)
from tools import upload_app  # noqa: E402

# Deliberately not round numbers: a bug that divides or rounds shows up in the
# second decimal place or not at all.
BISCUIT = ("parle_g_200g", "Parle-G 200g", 2145, "8901234567890")
NOODLES = ("maggi_70g", "Maggi Noodles 70g", 1400, "8901234567891")
PASTE = ("colgate_100g", "Colgate Toothpaste 100g", 5500, "8901234567892")
#: Taught from a photograph and bound to no code at all — the product that
#: makes the "no printed code" and "from a photograph" groups non-empty.
BUTTER = ("amul_butter_100g", "Amul Butter 100g", 5600)

T0 = datetime(2026, 8, 29, 5, 0, 0, tzinfo=timezone.utc)

#: The route ids the front end's own shell knows (ui/src/components/shell.tsx).
#: A result pointing anywhere else is a dead link.
SHELL_ROUTES = {"till", "products", "offers", "shop", "orders", "shopprofile",
                "today", "history", "inventory", "settings"}


def _ts(offset_s: int) -> str:
    return (T0 + timedelta(seconds=offset_s)).isoformat()


# ------------------------------------------------------------------ fixtures


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Nothing in this suite may see, let alone write, results/.

    Both overrides are set for EVERY test whether it uses them or not: a
    harness that honoured only one of them once destroyed the live catalogue,
    and that is a mistake with no undo.
    """
    data = tmp_path / "data"
    shop = tmp_path / "data" / "shop"
    shop.mkdir(parents=True)
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(data))
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(shop))
    monkeypatch.delenv("GAWAAH_SCAN_DIR", raising=False)
    # The till caches its store handle in a module global, so the previous
    # value is put back afterwards: this file must not leave a deleted temp
    # directory as the catalogue every later test file reads.
    previous = upload_app._DEPS.get("store_dir")
    upload_app.set_store_dir(shop)
    manage._CHAIN_CACHE.clear()
    yield
    manage._CHAIN_CACHE.clear()
    upload_app._DEPS["store_dir"] = previous
    upload_app._DEPS["store"] = None


@pytest.fixture()
def client(tmp_path) -> TestClient:
    """A counter with three coded products, one photographed one, two orders
    and two sessions — mounted the way the orchestrator will mount it: bare."""
    for sku, name, price, code in (BISCUIT, NOODLES, PASTE):
        upload_app.do_enrol_code_only(b"", sku, name, price, typed=code)
    upload_app._ao_put(BUTTER[0], BUTTER[1], BUTTER[2], [[0.1, 0.2, 0.3]], None)

    app = FastAPI()
    app.include_router(search.router)
    # The storefront is mounted only so the orders in these tests are placed by
    # the code that owns orders, rather than hand-written into the directory.
    app.include_router(storefront.router)
    c = TestClient(app)

    _place(c, "Rekha Sharma", "9876543210", BISCUIT[0], 2)
    _place(c, "Imran Qureshi", "98123 45678", NOODLES[0], 1)

    led = Ledger(manage.ledger_path())
    _session(led, "sess-morning", [(BISCUIT[0], 2145), (NOODLES[0], 1400)],
             at=0, close=True, settle=True)
    _session(led, "sess-probe", [], at=100, close=False)
    manage._CHAIN_CACHE.clear()
    return c


def _place(c: TestClient, who: str, phone: str, sku: str, qty: int) -> dict:
    r = c.post("/store/order", json={
        "items": [{"sku_id": sku, "qty": qty}],
        "name": who, "phone": phone,
        "address": "12 MG Road, second floor, near the water tank"})
    assert r.status_code == 200, r.text
    return r.json()


def _session(led: Ledger, session_id: str, lines, *, at: int,
             close: bool, settle: bool = False,
             base: datetime = T0) -> None:
    """One session written into the chain the way the real modules write it.

    The event shapes are copied from results/audit.jsonl, not invented — they
    are what gawaah/manage.py rebuilds a bill out of.
    """
    def _ts(offset_s: int) -> str:
        return (base + timedelta(seconds=offset_s)).isoformat()

    clock = at
    running = 0
    led.append(ts=_ts(clock), module="session", event="session",
               session_id=session_id, reason="session_opened",
               **{"from": "SETUP", "to": "SETUP"}, total_paise=0)
    for i, (sku, price) in enumerate(lines):
        clock += 1
        item_id = f"{sku}#{i}"
        led.append(ts=_ts(clock), module="session", event="classify",
                   session_id=session_id, reason="priced_from_gallery",
                   item_id=item_id, price_paise=price, abstained=False,
                   excluded_from_total=False,
                   **{"from": "MEASURING", "to": "PRICED"}, total_paise=running)
        running += price
        clock += 1
        led.append(ts=_ts(clock), module="session", event="exit",
                   session_id=session_id, reason="exit_crossing_committed",
                   item_id=item_id, price_paise=price, abstained=False,
                   excluded_from_total=False,
                   **{"from": "PRICED", "to": "BASKET_OPEN"},
                   total_paise=running)
    if close:
        clock += 1
        led.append(ts=_ts(clock), module="session", event="done",
                   session_id=session_id, reason="intent_requested",
                   lines=len(lines), amber_excluded=0,
                   intent_amount_paise=running,
                   **{"from": "BASKET_OPEN", "to": "AWAITING_SETTLEMENT"},
                   total_paise=running)
    if settle:
        clock += 1
        led.append(ts=_ts(clock), module="kernel", event="intent.settled",
                   session_id=session_id, amount_paise=running,
                   payment_id=f"pay_{session_id}", settled_by="webhook")


def _hits(c: TestClient, q: str, **params) -> list[dict]:
    r = c.get("/search", params={"q": q, **params})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    return body["results"]


def _ids(rows: list[dict], kind: str | None = None) -> list[str]:
    return [r["id"] for r in rows if kind is None or r["type"] == kind]


# =========================================================== finding things


def test_a_product_is_found_by_the_start_of_its_name(client: TestClient) -> None:
    rows = _hits(client, "parle")
    assert rows[0]["id"] == BISCUIT[0]
    assert rows[0]["type"] == "product"
    assert rows[0]["why"] == "the name starts with what you typed"


def test_a_product_is_found_by_a_word_in_the_middle_of_its_name(
        client: TestClient) -> None:
    rows = _hits(client, "toothpaste")
    assert _ids(rows, "product") == [PASTE[0]]


def test_a_product_is_found_by_its_sku_id(client: TestClient) -> None:
    rows = _hits(client, "maggi_70g")
    assert rows[0]["id"] == NOODLES[0]
    # An id is what a machine typed, so an exact id scores a shade below an
    # exact name or code rather than being excluded from the ranking.
    assert rows[0]["score"] == search.S_EXACT * search.W_SKU // 100
    assert rows[0]["score"] < search.S_EXACT


def test_a_product_is_found_by_a_printed_code_bound_to_it(
        client: TestClient) -> None:
    rows = _hits(client, BISCUIT[3])
    assert rows[0]["id"] == BISCUIT[0]
    assert rows[0]["why"] == "the bound code is exactly what you typed"
    assert BISCUIT[3] in rows[0]["codes"]


def test_the_price_shown_is_integer_paise_and_a_rupee_string(
        client: TestClient) -> None:
    row = _hits(client, "parle")[0]
    assert row["price_paise"] == 2145
    assert row["price_rupees"] == "21.45"
    assert isinstance(row["price_paise"], int)


def test_an_order_is_found_by_the_customers_name(client: TestClient) -> None:
    rows = _hits(client, "rekha")
    assert rows[0]["type"] == "order"
    assert rows[0]["customer_name"] == "Rekha Sharma"


def test_an_order_is_found_by_a_partial_phone_number(
        client: TestClient) -> None:
    rows = _hits(client, "98765")
    assert [r["customer_name"] for r in rows if r["type"] == "order"] \
        == ["Rekha Sharma"]


def test_a_phone_typed_without_its_space_still_finds_the_order(
        client: TestClient) -> None:
    """Imran's number was stored as '98123 45678'. A shopkeeper types it flat."""
    rows = _hits(client, "9812345678")
    assert [r["customer_name"] for r in rows if r["type"] == "order"] \
        == ["Imran Qureshi"]


def test_an_order_is_found_by_its_own_id(client: TestClient) -> None:
    placed = client.get("/orders").json()["orders"][0]
    rows = _hits(client, placed["order_id"])
    assert rows[0]["id"] == placed["order_id"]
    assert rows[0]["type"] == "order"


def test_a_bill_is_found_by_its_session_id(client: TestClient) -> None:
    rows = _hits(client, "sess-morning")
    assert rows[0]["id"] == "sess-morning"
    assert rows[0]["type"] == "bill"
    assert rows[0]["settled"] is True
    assert rows[0]["total_paise"] == 3545


def test_a_bill_is_found_by_its_exact_rupee_amount(client: TestClient) -> None:
    rows = _hits(client, "35.45")
    assert rows[0]["id"] == "sess-morning"
    assert rows[0]["why"] == "it comes to exactly Rs 35.45"


def test_a_product_is_found_by_its_exact_price(client: TestClient) -> None:
    rows = _hits(client, "21.45")
    assert rows[0]["id"] == BISCUIT[0]
    assert rows[0]["why"] == "it comes to exactly Rs 21.45"


def test_a_whole_rupee_query_matches_amounts_that_start_with_it(
        client: TestClient) -> None:
    """'35' is a shopkeeper asking about the thirty-five-rupee bill."""
    rows = _hits(client, "35")
    assert "sess-morning" in _ids(rows, "bill")
    assert [r["why"] for r in rows if r["id"] == "sess-morning"] \
        == ["it comes to Rs 35.45"]


def test_a_session_that_never_closed_is_found_and_marked_as_such(
        client: TestClient) -> None:
    rows = _hits(client, "sess-probe")
    assert rows[0]["id"] == "sess-probe"
    assert rows[0]["closed"] is False
    assert "never became a bill" in rows[0]["subtitle"]


def test_a_closed_bill_outranks_an_unclosed_session_on_an_equal_score(
        client: TestClient) -> None:
    rows = _hits(client, "sess")
    assert _ids(rows, "bill") == ["sess-morning", "sess-probe"]


# ==================================================== typo tolerance, exactly


def test_a_misspelt_product_name_still_finds_the_product(
        client: TestClient) -> None:
    rows = _hits(client, "maggie")
    assert rows[0]["id"] == NOODLES[0]
    assert rows[0]["why"] == "the name is one letter off what you typed"


def test_two_swapped_letters_still_find_the_product(
        client: TestClient) -> None:
    """'magig' for 'maggi' is one slip of two fingers, so it costs one edit."""
    rows = _hits(client, "magig")
    assert rows[0]["id"] == NOODLES[0]


def test_a_longer_word_gets_two_edits(client: TestClient) -> None:
    rows = _hits(client, "colgatte")
    assert rows[0]["id"] == PASTE[0]


def test_a_printed_code_one_digit_out_finds_nothing(
        client: TestClient) -> None:
    """A barcode that is one digit out is a DIFFERENT barcode. Offering the
    wrong packet for it is how a bill goes wrong quietly."""
    assert _ids(_hits(client, "8901234567899"), "product") == []


def test_a_sku_id_one_letter_out_finds_nothing(client: TestClient) -> None:
    assert _ids(_hits(client, "maggi_70h"), "product") == []


def test_a_three_letter_word_gets_no_typo_budget(client: TestClient) -> None:
    """At three letters one edit reaches most short words in any catalogue,
    and a search that returns everything has answered nothing."""
    assert search._max_edits("dal") == 0
    assert _ids(_hits(client, "amu"), "product") == [BUTTER[0]]  # prefix, not fuzz
    assert _ids(_hits(client, "xmu"), "product") == []


def test_every_word_of_the_query_has_to_land_somewhere(
        client: TestClient) -> None:
    assert _ids(_hits(client, "parle 200g"), "product") == [BISCUIT[0]]
    assert _ids(_hits(client, "parle biscuit"), "product") == []


def test_edit_distance_matches_known_values() -> None:
    assert edit_distance("kitten", "sitting", 3) == 3
    assert edit_distance("maggi", "maggi", 2) == 0
    assert edit_distance("maggi", "maggie", 2) == 1
    assert edit_distance("maggi", "magig", 2) == 1      # one transposition
    assert edit_distance("colgatte", "colgate", 2) == 1


def test_edit_distance_abandons_a_comparison_it_cannot_win() -> None:
    """Over budget returns budget+1 rather than the true distance: the caller
    only ever asks 'is this within budget'."""
    assert edit_distance("dal", "dahi", 1) == 2
    assert edit_distance("parle", "colgate", 2) == 3
    assert edit_distance("a", "b", 0) == 1
    assert edit_distance("", "abc", 2) == 3


# ================================================================ categories


def test_groups_are_derived_from_what_is_stored_and_counted(
        client: TestClient) -> None:
    rows = _hits(client, "taught", kind="category")
    by_id = {r["id"]: r for r in rows}
    assert by_id["taught:product_code_only"]["count"] == 3
    assert by_id["taught:appearance_only"]["count"] == 1
    assert by_id["taught:appearance_only"]["derived_from"]


def test_a_group_is_found_by_a_word_a_shopkeeper_would_type(
        client: TestClient) -> None:
    assert "taught:appearance_only" in _ids(_hits(client, "photo"), "category")
    assert "bill:paid" in _ids(_hits(client, "paid"), "category")


def test_a_group_with_nothing_in_it_is_not_offered(
        client: TestClient) -> None:
    """No order has been cancelled, so 'cancelled' is a dead end, not a group."""
    assert "order:cancelled" not in _ids(_hits(client, "cancelled"), "category")
    assert "order:new" in _ids(_hits(client, "new"), "category")


def test_the_product_with_no_bound_code_is_its_own_group(
        client: TestClient) -> None:
    rows = _hits(client, "no code", kind="category")
    by_id = {r["id"]: r for r in rows}
    assert by_id["product:no_code"]["count"] == 1


# ============================================== ranking, routes and the shape


def test_an_exact_code_outranks_a_name_that_merely_starts_with_the_query(
        client: TestClient) -> None:
    code_hit = _hits(client, NOODLES[3])[0]
    name_hit = _hits(client, "maggi nood")[0]
    assert code_hit["score"] > name_hit["score"]


def test_the_same_query_ranks_identically_every_time(
        client: TestClient) -> None:
    for q in ("products", "parle", "9", "35"):
        once = [(r["id"], r["score"]) for r in _hits(client, q)]
        assert once, q
        for _ in range(4):
            assert [(r["id"], r["score"]) for r in _hits(client, q)] == once


def test_every_result_carries_a_type_a_route_and_a_reason(
        client: TestClient) -> None:
    for q in ("parle", "rekha", "sess", "photo", "35.45"):
        for row in _hits(client, q):
            assert row["type"] in search.KINDS
            assert row["route"].startswith("#/")
            assert row["why"], f"{row['id']} matched {q!r} for no stated reason"
            assert row["title"] and row["subtitle"]


def test_every_route_lands_on_a_screen_the_front_end_knows(
        client: TestClient) -> None:
    """`routeFromHash` falls back to the till for a route id it does not know,
    so a typo here would silently send every result to the wrong screen."""
    for q in ("parle", "rekha", "sess", "photo", "offer", "no code"):
        for row in _hits(client, q):
            head = row["route"][2:].split("?")[0]
            assert head in SHELL_ROUTES, row["route"]
            assert row["screen"] == head


def test_the_limit_caps_the_results_and_says_it_truncated(
        client: TestClient) -> None:
    r = client.get("/search", params={"q": "products", "limit": 2})
    body = r.json()
    assert len(body["results"]) == 2
    assert body["count"] == 2
    assert body["matched"] > 2
    assert body["truncated"] is True


def test_a_kind_filter_narrows_to_one_kind(client: TestClient) -> None:
    """Rs 14.00 is both what a packet of Maggi costs and what Imran's order
    came to, so this query reaches two kinds before it is narrowed."""
    assert {r["type"] for r in _hits(client, "14.00")} == {"product", "order"}
    r = client.get("/search", params={"q": "14.00", "kind": "order"})
    body = r.json()
    assert body["kinds"] == ["order"]
    assert {row["type"] for row in body["results"]} == {"order"}
    # A narrowed search does not pay for the sources it will not use.
    assert set(body["sources"]) == {"orders"}


def test_two_kinds_can_be_asked_for_at_once(client: TestClient) -> None:
    r = client.get("/search", params={"q": "14.00", "kind": "product,order"})
    assert set(r.json()["kinds"]) == {"product", "order"}
    assert {row["type"] for row in r.json()["results"]} == {"product", "order"}


def test_a_query_that_matches_nothing_is_an_empty_answer_not_a_refusal(
        client: TestClient) -> None:
    r = client.get("/search", params={"q": "zzqqxx"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["results"] == []
    assert body["matched"] == 0


# ============================================================ named refusals


def test_a_missing_query_is_refused_by_name(client: TestClient) -> None:
    r = client.get("/search")
    assert r.status_code == 400
    assert r.json()["reason"] == R_NO_QUERY
    assert r.json()["ok"] is False


def test_a_blank_query_is_refused_by_name(client: TestClient) -> None:
    r = client.get("/search", params={"q": "   "})
    assert r.status_code == 400
    assert r.json()["reason"] == R_NO_QUERY


def test_an_over_long_query_is_refused_by_name(client: TestClient) -> None:
    r = client.get("/search", params={"q": "x" * (MAX_QUERY + 1)})
    assert r.status_code == 400
    assert r.json()["reason"] == R_QUERY_TOO_LONG
    assert str(MAX_QUERY) in r.json()["detail"]


def test_a_limit_that_is_not_a_whole_number_is_refused_by_name(
        client: TestClient) -> None:
    for bad in ("lots", "3.5", "-"):
        r = client.get("/search", params={"q": "a", "limit": bad})
        assert r.status_code == 400, bad
        assert r.json()["reason"] == R_BAD_LIMIT, bad


def test_a_limit_outside_the_range_is_refused_by_name(
        client: TestClient) -> None:
    for bad in (0, -3, MAX_LIMIT + 1):
        r = client.get("/search", params={"q": "a", "limit": bad})
        assert r.status_code == 400, bad
        assert r.json()["reason"] == R_LIMIT_RANGE, bad


def test_an_unknown_kind_is_refused_by_name(client: TestClient) -> None:
    r = client.get("/search", params={"q": "a", "kind": "bananas"})
    assert r.status_code == 400
    body = r.json()
    assert body["reason"] == R_BAD_KIND
    # The refusal names what IS searchable, so it can be acted on.
    for kind in search.KINDS:
        assert kind in body["detail"]


def test_a_missing_till_is_named_and_the_other_sources_still_answer(
        client: TestClient, monkeypatch) -> None:
    def no_till():
        raise SearchRefused(R_NO_TILL, "tools/upload_app.py is not importable")

    monkeypatch.setattr(search, "_till", no_till)
    r = client.get("/search", params={"q": "rekha"})
    assert r.status_code == 200
    body = r.json()
    assert body["partial"] is True
    assert body["sources"]["products"]["available"] is False
    assert body["sources"]["products"]["reason"] == R_NO_TILL
    # The orders were still searched, so the shopkeeper still gets an answer.
    assert _ids(body["results"], "order")


def test_an_unreadable_catalogue_is_named_and_the_rest_still_answers(
        client: TestClient, monkeypatch) -> None:
    def boom():
        raise RuntimeError("appearance_only.json is half written")

    monkeypatch.setattr(upload_app, "offer_priced_skus", boom)
    body = client.get("/search", params={"q": "sess-morning"}).json()
    assert body["ok"] is True
    assert body["partial"] is True
    assert body["sources"]["products"]["reason"] == R_CATALOGUE_UNREADABLE
    assert "half written" in body["sources"]["products"]["detail"]
    assert _ids(body["results"], "bill") == ["sess-morning"]


def test_unreadable_orders_are_named_and_the_rest_still_answers(
        client: TestClient, monkeypatch) -> None:
    def boom():
        raise OSError("the orders directory is gone")

    monkeypatch.setattr(storefront, "_all_orders", boom)
    body = client.get("/search", params={"q": "parle"}).json()
    assert body["ok"] is True
    assert body["partial"] is True
    assert body["sources"]["orders"]["reason"] == R_ORDERS_UNREADABLE
    assert _ids(body["results"], "product") == [BISCUIT[0]]


def test_unreadable_bills_are_named_and_the_rest_still_answers(
        client: TestClient, monkeypatch) -> None:
    def boom():
        raise ValueError("the chain is not JSON")

    monkeypatch.setattr(manage, "read_chain", boom)
    body = client.get("/search", params={"q": "parle"}).json()
    assert body["ok"] is True
    assert body["partial"] is True
    assert body["sources"]["bills"]["reason"] == R_BILLS_UNREADABLE
    assert _ids(body["results"], "product") == [BISCUIT[0]]


def test_a_chain_that_stops_verifying_is_a_partial_answer_not_a_silent_one(
        client: TestClient) -> None:
    """What came back is true and it is not everything, and those are two
    different claims. A search that reported the truncated set as complete
    would tell a shopkeeper his bill does not exist."""
    with manage.ledger_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _ts(999), "module": "session",
                            "prev_hash": "0" * 64, "hash": "not-a-hash"}) + "\n")
    manage._CHAIN_CACHE.clear()

    body = client.get("/search", params={"q": "sess"}).json()
    assert body["ok"] is True
    assert body["partial"] is True
    bills = body["sources"]["bills"]
    assert bills["available"] is True
    assert bills["complete"] is False
    assert "does not verify" in bills["detail"]


def test_when_nothing_can_be_read_at_all_the_search_refuses_by_name(
        client: TestClient, monkeypatch) -> None:
    """A search over nothing is not a short answer, it is a wrong one."""
    down = search.SourceState("x", available=False, reason="gone",
                              detail="the disk is not there")
    monkeypatch.setattr(search, "load_products", lambda: ([], down))
    monkeypatch.setattr(search, "load_orders", lambda: ([], down))
    monkeypatch.setattr(search, "load_bills", lambda: ([], down))
    r = client.get("/search", params={"q": "parle"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_NOTHING_TO_SEARCH
    assert "the disk is not there" in r.json()["detail"]


def test_an_unexpected_failure_is_a_named_400_and_never_a_500(
        client: TestClient, monkeypatch) -> None:
    def boom(pool, q):
        raise TypeError("something nobody predicted")

    monkeypatch.setattr(search, "rank", boom)
    r = client.get("/search", params={"q": "parle"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_INTERNAL
    assert "TypeError" in r.json()["detail"]


def test_no_query_of_any_shape_produces_a_500(client: TestClient) -> None:
    nasty = ["../../etc/passwd", "'; drop table skus; --", "%00", "\\", "{}",
             "[]", "😀", "मैगी", "a" * MAX_QUERY, "-1", "0.0.0", "..", "*",
             "%s", "‮", "<script>alert(1)</script>", "\t\n", "1e9999"]
    for q in nasty:
        r = client.get("/search", params={"q": q})
        assert r.status_code in (200, 400), (q, r.status_code)
        assert r.status_code != 500
        body = r.json()
        assert "ok" in body
        if body["ok"] is False:
            assert body["reason"] and body["detail"]


# ==================================================================== recent


def test_recent_shows_the_newest_thing_first(client: TestClient) -> None:
    body = client.get("/search/recent").json()
    assert body["ok"] is True
    stamps = [row["at"] for row in body["items"]]
    assert stamps == sorted(stamps, reverse=True)
    # The orders were placed today; the ledger fixture is dated last August.
    assert body["items"][0]["type"] == "order"
    assert body["items"][0]["why"].startswith("placed ")


def test_recent_includes_the_bills_from_the_chain(client: TestClient) -> None:
    body = client.get("/search/recent", params={"limit": 50}).json()
    assert "sess-morning" in [r["id"] for r in body["items"]]


def test_recent_says_why_no_product_is_listed_rather_than_faking_a_date(
        client: TestClient) -> None:
    """The catalogue stores no per-product timestamp. Listing products in file
    order would be alphabetical order presented as history."""
    body = client.get("/search/recent").json()
    assert [r for r in body["items"] if r["type"] == "product"] == []
    assert any("no per-product date" in n for n in body["notes"])


def test_a_product_edited_through_the_products_screen_is_recent(
        client: TestClient) -> None:
    chain = Ledger(search.shop_dir() / "catalogue.audit.jsonl")
    chain.append(ts=datetime.now(timezone.utc).isoformat(), module="shopadmin",
                 event="sku_edited", sku_id=BISCUIT[0], changed=["price"])
    body = client.get("/search/recent", params={"limit": 50}).json()
    products = [r for r in body["items"] if r["type"] == "product"]
    assert [r["id"] for r in products] == [BISCUIT[0]]
    assert products[0]["why"].startswith("edited ")
    assert body["notes"] == []


def test_recent_buckets_a_few_of_each_kind_beside_the_literal_list(
        client: TestClient) -> None:
    """A counter that rings up two hundred bills and takes three orders has a
    strictly newest-first list that is all bills — true, and useless as a
    palette. Both views are returned; neither replaces the other."""
    led = Ledger(manage.ledger_path())
    now = datetime.now(timezone.utc)
    for i in range(9):
        _session(led, f"sess-late-{i}", [(BISCUIT[0], 2145)], at=i * 10,
                 close=True, base=now)
    manage._CHAIN_CACHE.clear()

    body = client.get("/search/recent").json()
    assert {r["type"] for r in body["items"]} == {"bill"}
    assert set(body["by_kind"]) == {"bill", "order"}
    assert len(body["by_kind"]["bill"]) == body["per_kind"] == 3
    assert len(body["by_kind"]["order"]) == 2
    stamps = [r["at"] for r in body["by_kind"]["bill"]]
    assert stamps == sorted(stamps, reverse=True)


def test_recent_offers_groups_so_the_palette_is_never_empty(
        client: TestClient) -> None:
    body = client.get("/search/recent").json()
    assert body["categories"]
    for cat in body["categories"]:
        assert cat["count"] > 0
        assert cat["route"].startswith("#/")


def test_recent_refuses_a_bad_limit_by_name(client: TestClient) -> None:
    r = client.get("/search/recent", params={"limit": "0"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_LIMIT_RANGE


def test_recent_on_a_counter_where_nothing_has_happened_says_so(
        client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(storefront, "_all_orders", lambda: [])
    monkeypatch.setattr(manage, "bills_from", lambda records: {})
    body = client.get("/search/recent").json()
    assert body["items"] == []
    assert body["notes"]


# ==================================================================== health


def test_health_reports_the_shop_it_is_reading_and_every_source(
        client: TestClient) -> None:
    body = client.get("/search/health").json()
    assert body["ok"] is True
    assert body["shop_dir"] == str(upload_app.store_dir())
    assert set(body["sources"]) == {"products", "orders", "bills", "categories"}
    assert body["sources"]["products"]["scanned"] == 4
    assert body["sources"]["orders"]["scanned"] == 2
    assert body["sources"]["bills"]["scanned"] == 2


def test_health_states_what_is_deliberately_not_searched(
        client: TestClient) -> None:
    body = client.get("/search/health").json()
    joined = " ".join(body["not_searched"]).lower()
    assert "address" in joined
    assert "barcode" in joined
    assert "category field" in body["categories_are_derived"].lower()


def test_a_delivery_address_is_never_searchable(client: TestClient) -> None:
    """Search runs on every keystroke; a doorstep is not something to spill for
    a two-letter query. The address is on the order itself."""
    rows = _hits(client, "water tank")
    assert _ids(rows, "order") == []
    body = client.get("/search", params={"q": "rekha"}).json()
    assert "MG Road" not in json.dumps(body)


# ============================================================ what it costs


def test_a_search_on_this_catalogue_is_well_inside_the_budget(
        client: TestClient) -> None:
    body = client.get("/search", params={"q": "maggie parle"}).json()
    assert body["cost"]["within_budget"] is True
    assert body["cost"]["took_ms"] <= search.BUDGET_MS


def test_the_cost_block_states_what_10000_skus_would_cost(
        client: TestClient) -> None:
    note = client.get("/search", params={"q": "parle"}).json()["cost"]["note"]
    assert "10,000" in note
    assert "index" in note


def test_the_published_cost_per_thousand_products_is_still_true(
        tmp_path) -> None:
    """The module publishes a microseconds-per-1000-products figure and
    extrapolates it to 10,000 SKUs. This measures it, so the published number
    cannot quietly become fiction.

    The worst query shape found: two words, one of them only fuzzy-matching, so
    the edit-distance pass runs over every name in the catalogue.
    """
    shop = tmp_path / "big"
    shop.mkdir()
    upload_app.set_store_dir(shop)
    words = ["Parle", "Maggi", "Colgate", "Lifebuoy", "Amul", "Tata",
             "Britannia", "Dettol", "Surf", "Nescafe"]
    skus = {
        f"sku_{words[i % len(words)].lower()}_{i}": {
            "name": f"{words[i % len(words)]} pack {i} 200g",
            "price_paise": 1000 + i, "vectors": [], "photo": None,
            "taught_with": "appearance_only", "footprint_mm": None}
        for i in range(1000)
    }
    (shop / "appearance_only.json").write_text(
        json.dumps({"format": upload_app.AO_FORMAT, "skus": skus}))

    app = FastAPI()
    app.include_router(search.router)
    c = TestClient(app)

    runs = []
    for _ in range(5):
        body = c.get("/search", params={"q": "colgatte 200g",
                                        "kind": "product"}).json()
        assert body["cost"]["scanned"]["products"] == 1000
        runs.append(body["cost"]["took_us"])
    runs.sort()
    median = runs[len(runs) // 2]
    assert median <= CEILING_US_PER_1000_PRODUCTS, (
        f"1000 products now cost {median} us per query, over the published "
        f"ceiling of {CEILING_US_PER_1000_PRODUCTS}. Either the scan got "
        f"slower or the number in gawaah/search.py is out of date — the "
        f"extrapolation to 10,000 SKUs printed by /search/health depends on "
        f"this being honest.")


# ================================================== invariants of this module


def test_no_float_appears_anywhere_in_a_search_response(
        client: TestClient) -> None:
    """INVARIANT 1. Prices, totals and scores are integers end to end; a float
    in this response would mean one was created on the way out."""
    def walk(node, where="$"):
        if isinstance(node, float):
            raise AssertionError(f"float at {where}: {node!r}")
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{where}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{where}[{i}]")

    for url, params in (("/search", {"q": "a", "limit": MAX_LIMIT}),
                        ("/search", {"q": "35.45"}),
                        ("/search/recent", {"limit": 50}),
                        ("/search/health", {})):
        walk(client.get(url, params=params).json(), url)


def test_search_never_writes_anything(client: TestClient, tmp_path) -> None:
    """It reads four things and changes none of them. A search that touched the
    catalogue would be a search that can corrupt it."""
    def snapshot():
        out = {}
        for p in sorted((tmp_path / "data").rglob("*")):
            if p.is_file():
                st = p.stat()
                out[str(p)] = (st.st_size, st.st_mtime_ns)
        return out

    client.get("/search", params={"q": "parle"})  # warm any first-touch
    before = snapshot()
    for q in ("parle", "rekha", "sess-morning", "35.45", "photo"):
        client.get("/search", params={"q": q})
    client.get("/search/recent")
    client.get("/search/health")
    assert snapshot() == before


def test_this_module_constructs_no_payment_string() -> None:
    """INVARIANT 6. Search shows what exists; it mints nothing and links to
    nothing payable."""
    src = Path(search.__file__).read_text(encoding="utf-8")
    for forbidden in ("upi:", "pa=", "razorpay.com", "http://", "https://"):
        assert forbidden not in src, forbidden


def test_the_router_is_mountable_bare_with_absolute_paths() -> None:
    paths = sorted(r.path for r in search.router.routes)
    assert paths == ["/search", "/search/health", "/search/recent"]
    assert search.router.prefix == ""


def test_the_routes_answer_when_mounted_on_a_bare_app() -> None:
    """Exactly how the orchestrator will mount it: no prefix, nothing else."""
    app = FastAPI()
    app.include_router(search.router)
    c = TestClient(app)
    assert c.get("/search", params={"q": "anything"}).status_code == 200
    assert c.get("/search/recent").status_code == 200
    assert c.get("/search/health").status_code == 200


def test_the_shop_directory_is_the_tills_own_answer(client: TestClient) -> None:
    """GAWAAH_SHOP_DIR, honoured through upload_app.store_dir() and never by
    reading the environment a second time — a harness that answered this
    question differently once destroyed the live catalogue."""
    assert search.shop_dir() == Path(upload_app.store_dir())
    assert "results" not in str(search.shop_dir())
