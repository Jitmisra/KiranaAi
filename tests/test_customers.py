"""gawaah/customers.py — the people who buy, derived and never stored.

Four claims this file exists to make checkable, because each of them is a claim
a demo can fake:

  1. IT IS A VIEW. Nothing is persisted. Every route is called and the shop
     directory is then compared byte for byte with what it was before, and the
     module's own source is asserted to contain no writing primitive at all. A
     customer record that can be edited is a customer record that can disagree
     with the orders it came from.

  2. A SUMMARY NEVER CARRIES AN ADDRESS. The tests below put a distinctive
     address in the orders and assert that string is ABSENT from the raw body of
     every list endpoint and PRESENT in the one detail endpoint. Not "the field
     is unused" — absent from the bytes.

  3. INTEGER PAISE. Every number in every response is walked and asserted not to
     be a float, cancelled money is kept apart from spent money, and an order
     whose total is not integer paise is counted as a visit and excluded from
     every rupee rather than rounded into one.

  4. EVERY REFUSAL HAS A NAME. A bad limit, an unknown sort, three digits, a
     number nobody has, an orders directory that is a file — each answers with
     its own reason string, and no input of any shape produces a 500.

Nothing here talks to a gateway and nothing here can mark an order paid.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import customers, storefront  # noqa: E402
from gawaah.customers import (  # noqa: E402
    MAX_LIMIT,
    MAX_SEARCH,
    MIN_PHONE_DIGITS,
    MIN_REGULAR_ORDERS,
    MIN_SEARCH_DIGITS,
    R_BAD_LIMIT,
    R_BAD_PHONE,
    R_BAD_SORT,
    R_INTERNAL,
    R_NO_CUSTOMER,
    R_NO_ORDERS,
    R_NO_ORDERS_SOURCE,
    R_NO_PHONE,
    R_NO_TILL,
    R_SHORT_PHONE,
    R_TOO_LONG,
    normalise_phone,
)
from tools import upload_app  # noqa: E402

#: A door nobody else in the test suite writes down, so its presence or absence
#: in a response body is unambiguous.
HOME = "Flat 3B, Neem Gali, behind the temple"
OFFICE = "Unit 9, Ambedkar Market, first floor"

BISCUIT = ("parle_g_200g", "Parle-G 200g", 2145)
SOAP = ("lifebuoy_125g", "Lifebuoy 125g", 3950)


# ------------------------------------------------------------- the harness --


@pytest.fixture()
def shop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A shop that lives and dies with the test.

    REDIRECTED TWO WAYS ON PURPOSE, as `tests/test_storefront.py` documents:
    `set_store_dir` moves the till's cached handle and `GAWAAH_SHOP_DIR` covers
    any code that re-reads the environment. A harness that honoured only one of
    them once destroyed the live catalogue in results/, and that has no undo.
    """
    d = tmp_path / "shop"
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(d))
    monkeypatch.delenv("GAWAAH_SCAN_DIR", raising=False)
    monkeypatch.delenv("GAWAAH_CODES_FILE", raising=False)
    upload_app.set_store_dir(d)
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture()
def client(shop: Path) -> TestClient:
    app = FastAPI()
    app.include_router(customers.router)
    return TestClient(app)


def place(shop: Path, *, order_id: str, at: str, phone: str,
          name: str = "Rekha", address: str = HOME,
          lines: Optional[list[tuple[str, str, int, int]]] = None,
          status: str = "new", paid: bool = False,
          total: Any = "derive",
          raw: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Write one order file in exactly the shape the storefront writes.

    Deliberately hand-written rather than posted through the storefront for most
    tests: these need controlled timestamps and, in two cases, a corrupt file
    the storefront would never produce. One test at the bottom does place real
    orders through the real router, so the shape asserted here is checked
    against the shape that is actually written.
    """
    rows = lines if lines is not None else [(BISCUIT[0], BISCUIT[1],
                                             BISCUIT[2], 1)]
    out_lines = []
    summed = 0
    for sku, nm, unit, qty in rows:
        out_lines.append({
            "sku_id": sku, "name": nm, "qty": qty,
            "unit_paise": unit, "unit_rupees": f"{unit // 100}.{unit % 100:02d}",
            "line_paise": unit * qty,
            "line_rupees": f"{(unit * qty) // 100}.{(unit * qty) % 100:02d}",
            "taught_with": "code_only",
        })
        summed += unit * qty
    doc: dict[str, Any] = {
        "format": 1,
        "order_id": order_id,
        "at": at,
        "status": status,
        "status_changed_at": at,
        "history": [{"at": at, "from": None, "to": "new", "by": "customer"}],
        "customer": {"name": name, "phone": phone, "address": address},
        "lines": out_lines,
        "total_paise": summed if total == "derive" else total,
        "total_rupees": f"{summed // 100}.{summed % 100:02d}",
        "payment": {"session_id": f"shop_{order_id}", "paid": paid,
                    "state": "PAID" if paid else None, "short_url": None,
                    "minted_at": at if paid else None},
    }
    # `raw` writes fields no storefront would ever produce — a float total, a
    # line with no numbers in it — which is the only way to test what this
    # module does with an order file it cannot read.
    doc.update(raw or {})
    d = shop / "orders"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{order_id}.json").write_text(json.dumps(doc, sort_keys=True),
                                        encoding="utf-8")
    return doc


def two_customers(shop: Path) -> None:
    """Rekha, who comes back three times, and Imran, who came once."""
    place(shop, order_id="ord_000000000001", at="2026-08-01T10:00:00+00:00",
          phone="9876543210", name="Rekha", address=HOME)
    place(shop, order_id="ord_000000000002", at="2026-08-09T10:00:00+00:00",
          phone="+91 98765 43210", name="Rekha", address=HOME,
          lines=[(SOAP[0], SOAP[1], SOAP[2], 2)], paid=True)
    place(shop, order_id="ord_000000000003", at="2026-08-21T10:00:00+00:00",
          phone="098765 43210", name="Rekha Devi", address=OFFICE,
          status="delivered")
    place(shop, order_id="ord_000000000004", at="2026-08-25T10:00:00+00:00",
          phone="9000011111", name="Imran", address=OFFICE,
          lines=[(BISCUIT[0], BISCUIT[1], BISCUIT[2], 10)])


def body(response) -> dict[str, Any]:
    assert response.headers["content-type"].startswith("application/json")
    return response.json()


def refused(response, reason: str, status: int = 400) -> dict[str, Any]:
    assert response.status_code == status, response.text
    out = body(response)
    assert out["ok"] is False
    assert out["reason"] == reason, out
    assert isinstance(out["detail"], str) and out["detail"].strip()
    assert out["settles_money"] is False
    return out


def walk_numbers(node: Any, path: str = "$") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            found += walk_numbers(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found += walk_numbers(v, f"{path}[{i}]")
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        found.append((path, node))
    return found


def snapshot(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(
                p.read_bytes()).hexdigest()
        else:
            out[str(p.relative_to(root)) + "/"] = "dir"
    return out


# ------------------------------------------------------ the number is the key --


def test_the_same_number_written_three_ways_is_one_customer(
        client: TestClient, shop: Path) -> None:
    """9876543210, +91 98765 43210 and 098765 43210 are one woman."""
    two_customers(shop)
    out = body(client.get("/customers"))
    assert out["ok"] is True
    phones = [c["phone"] for c in out["customers"]]
    assert phones.count("9876543210") == 1
    assert out["total_customers"] == 2
    rekha = [c for c in out["customers"] if c["phone"] == "9876543210"][0]
    assert rekha["order_count"] == 3


def test_normalise_phone_folds_only_indias_country_code() -> None:
    """The stated limit, asserted so it cannot drift into a wrong guess."""
    assert normalise_phone("9876543210") == "9876543210"
    assert normalise_phone("+91 98765 43210") == "9876543210"
    assert normalise_phone("098765-43210") == "9876543210"
    assert normalise_phone("0091 98765 43210") == "9876543210"
    # A UK number keeps its country code rather than being folded by a guess.
    assert normalise_phone("+44 20 7946 0958") == "442079460958"
    assert normalise_phone("no digits here") == ""
    assert normalise_phone(None) == ""
    assert normalise_phone(9876543210) == ""


def test_an_order_with_an_undialable_number_is_counted_not_hidden(
        client: TestClient, shop: Path) -> None:
    """Neither attributed to anybody nor silently dropped."""
    two_customers(shop)
    place(shop, order_id="ord_00000000000f", at="2026-08-26T10:00:00+00:00",
          phone="1" * (MIN_PHONE_DIGITS - 1), name="Nobody")
    out = body(client.get("/customers"))
    assert out["orders_read"] == 5
    assert out["orders_without_a_phone"] == 1
    assert out["total_customers"] == 2


# --------------------------------------------------------------- the totals --


def test_spent_is_the_sum_of_the_orders_that_were_not_cancelled(
        client: TestClient, shop: Path) -> None:
    two_customers(shop)
    place(shop, order_id="ord_000000000005", at="2026-08-22T10:00:00+00:00",
          phone="9876543210", status="cancelled",
          lines=[(SOAP[0], SOAP[1], SOAP[2], 1)])
    rekha = body(client.get("/customers/9876543210"))
    # 2145 + 2*3950 + 2145 kept; the cancelled 3950 kept apart.
    assert rekha["total_paise"] == 2145 + 7900 + 2145
    assert rekha["cancelled_paise"] == 3950
    assert rekha["order_count"] == 4
    assert rekha["kept_count"] == 3
    assert rekha["cancelled_count"] == 1
    assert rekha["total_rupees"] == "121.90"
    assert rekha["cancelled_rupees"] == "39.50"


def test_paid_is_what_settled_and_is_not_the_same_number_as_spent(
        client: TestClient, shop: Path) -> None:
    """An order placed is not money received, and the two are never merged."""
    two_customers(shop)
    rekha = body(client.get("/customers/9876543210"))
    assert rekha["paid_paise"] == 7900
    assert rekha["paid_count"] == 1
    assert rekha["total_paise"] > rekha["paid_paise"]
    assert rekha["paid_rupees"] == "79.00"


def test_an_order_whose_total_is_not_integer_paise_is_excluded_and_reported(
        client: TestClient, shop: Path) -> None:
    """Abstain rather than guess: it is a visit, and it is not a rupee."""
    place(shop, order_id="ord_000000000006", at="2026-08-02T10:00:00+00:00",
          phone="9876543210",
          raw={"total_paise": 21.45,
               "lines": [{"sku_id": "x", "name": "X", "qty": 1,
                          "unit_paise": 21.45, "line_paise": 21.45}]})
    place(shop, order_id="ord_000000000007", at="2026-08-03T10:00:00+00:00",
          phone="9876543210")
    rekha = body(client.get("/customers/9876543210"))
    assert rekha["order_count"] == 2
    assert rekha["unpriced_count"] == 1
    assert rekha["total_paise"] == 2145
    unpriced = [o for o in rekha["orders"] if not o["priced"]]
    assert len(unpriced) == 1
    assert unpriced[0]["total_paise"] is None
    assert unpriced[0]["lines"][0]["line_paise"] is None


def test_a_float_total_falls_back_to_the_lines_before_it_gives_up(
        client: TestClient, shop: Path) -> None:
    """The ladder, in order: the stated total, then the lines, then abstain.

    A total that is not integer paise is not believed. But if the LINES are
    sound, adding them up is arithmetic and not a guess, so the order is priced
    from them rather than thrown away.
    """
    place(shop, order_id="ord_000000000009", at="2026-08-05T10:00:00+00:00",
          phone="9876543210", lines=[(SOAP[0], SOAP[1], SOAP[2], 2)],
          raw={"total_paise": 79.0})
    rekha = body(client.get("/customers/9876543210"))
    assert rekha["unpriced_count"] == 0
    assert rekha["total_paise"] == 7900


def test_a_missing_total_is_recomputed_from_the_lines(
        client: TestClient, shop: Path) -> None:
    place(shop, order_id="ord_000000000008", at="2026-08-04T10:00:00+00:00",
          phone="9876543210", total=None,
          lines=[(BISCUIT[0], BISCUIT[1], BISCUIT[2], 3)])
    rekha = body(client.get("/customers/9876543210"))
    assert rekha["total_paise"] == 6435
    assert rekha["unpriced_count"] == 0


def test_no_number_anywhere_in_any_response_is_a_float(
        client: TestClient, shop: Path) -> None:
    """INVARIANT 1 at the boundary, checked on the wire and not in the source."""
    two_customers(shop)
    for url in ("/customers", "/customers?sort=spend", "/customers/regulars",
                "/customers/lookup?phone=9876543210",
                "/customers/9876543210"):
        for where, value in walk_numbers(body(client.get(url))):
            assert not isinstance(value, float), f"{url} {where} = {value!r}"


def test_days_known_is_a_whole_number_of_days(
        client: TestClient, shop: Path) -> None:
    two_customers(shop)
    rekha = body(client.get("/customers/9876543210"))
    assert rekha["days_known"] == 20
    assert isinstance(rekha["days_known"], int)


# ------------------------------------------------------------- the identity --


def test_the_name_shown_is_the_one_on_the_newest_order(
        client: TestClient, shop: Path) -> None:
    two_customers(shop)
    rekha = body(client.get("/customers/9876543210"))
    assert rekha["name"] == "Rekha Devi"
    assert rekha["names_seen"] == ["Rekha Devi", "Rekha"]
    assert rekha["names_seen_count"] == 2


def test_first_and_last_order_bracket_the_history(
        client: TestClient, shop: Path) -> None:
    two_customers(shop)
    rekha = body(client.get("/customers/9876543210"))
    assert rekha["first_order_at"] == "2026-08-01T10:00:00+00:00"
    assert rekha["last_order_at"] == "2026-08-21T10:00:00+00:00"
    assert rekha["last_status"] == "delivered"


def test_the_detail_lists_orders_newest_first(
        client: TestClient, shop: Path) -> None:
    two_customers(shop)
    rekha = body(client.get("/customers/9876543210"))
    ats = [o["at"] for o in rekha["orders"]]
    assert ats == sorted(ats, reverse=True)
    assert rekha["orders"][0]["order_id"] == "ord_000000000003"
    assert rekha["orders"][0]["lines"][0]["sku_id"] == BISCUIT[0]


# ---------------------------------------------------------------- privacy --


def test_no_list_endpoint_carries_an_address(
        client: TestClient, shop: Path) -> None:
    """Asserted against the raw bytes, not against a field name."""
    two_customers(shop)
    for url in ("/customers", "/customers?q=rekha", "/customers/regulars",
                "/customers/lookup?phone=9876543210",
                "/customers/lookup?phone=4321"):
        text = client.get(url).text
        assert HOME not in text, url
        assert OFFICE not in text, url
        assert "Neem Gali" not in text, url


def test_the_summary_row_has_a_count_of_addresses_and_no_address(
        client: TestClient, shop: Path) -> None:
    two_customers(shop)
    row = body(client.get("/customers"))["customers"][0]
    assert "addresses" not in row
    assert "address" not in row
    assert row["address_count"] >= 1


def test_the_detail_endpoint_may_show_where_they_live(
        client: TestClient, shop: Path) -> None:
    """One person, asked for by their own number, is the one shape that does."""
    two_customers(shop)
    r = client.get("/customers/9876543210")
    assert HOME in r.text
    rekha = body(r)
    seen = [a["address"] for a in rekha["addresses"]]
    # Most recently used first: the office was the last delivery.
    assert seen == [OFFICE, HOME]
    assert rekha["addresses"][1]["orders"] == 2
    assert rekha["address_count"] == 2


# ------------------------------------------------------------- the listing --


def test_the_list_is_most_recent_first_by_default(
        client: TestClient, shop: Path) -> None:
    two_customers(shop)
    out = body(client.get("/customers"))
    assert [c["phone"] for c in out["customers"]] == ["9000011111",
                                                      "9876543210"]
    assert out["sort"] == "recent"


def test_sorting_by_spend_puts_the_biggest_basket_first(
        client: TestClient, shop: Path) -> None:
    two_customers(shop)
    out = body(client.get("/customers?sort=spend"))
    assert out["customers"][0]["phone"] == "9000011111"  # 10 x 21.45
    assert out["customers"][0]["total_paise"] == 21450


def test_sorting_by_orders_puts_the_most_frequent_first(
        client: TestClient, shop: Path) -> None:
    """A different question from spend, and it gives a different name."""
    two_customers(shop)
    out = body(client.get("/customers?sort=orders"))
    assert [c["phone"] for c in out["customers"]] == ["9876543210",
                                                     "9000011111"]
    assert out["customers"][0]["order_count"] == 3


def test_the_detail_route_takes_the_number_however_it_is_spelled(
        client: TestClient, shop: Path) -> None:
    two_customers(shop)
    plain = body(client.get("/customers/9876543210"))
    zeroed = body(client.get("/customers/09876543210"))
    assert zeroed["phone"] == plain["phone"] == "9876543210"
    assert zeroed["order_count"] == 3


def test_sorting_by_name_is_alphabetical_and_case_insensitive(
        client: TestClient, shop: Path) -> None:
    two_customers(shop)
    out = body(client.get("/customers?sort=name"))
    assert [c["name"] for c in out["customers"]] == ["Imran", "Rekha Devi"]


def test_searching_matches_a_name_or_any_run_of_digits(
        client: TestClient, shop: Path) -> None:
    two_customers(shop)
    by_name = body(client.get("/customers?q=rekha"))
    assert by_name["matched"] == 1
    assert by_name["customers"][0]["phone"] == "9876543210"
    by_digits = body(client.get("/customers?q=4321"))
    assert by_digits["matched"] == 1
    by_nothing = body(client.get("/customers?q=zzzz"))
    assert by_nothing["matched"] == 0
    assert by_nothing["customers"] == []
    assert by_nothing["total_customers"] == 2


def test_a_limit_shortens_the_list_and_says_how_many_matched(
        client: TestClient, shop: Path) -> None:
    two_customers(shop)
    out = body(client.get("/customers?limit=1"))
    assert out["count"] == 1
    assert out["matched"] == 2
    assert out["limit"] == 1


def test_a_shop_with_no_orders_at_all_is_an_empty_list_not_an_error(
        client: TestClient) -> None:
    out = body(client.get("/customers"))
    assert out["ok"] is True
    assert out["customers"] == []
    assert out["orders_read"] == 0
    assert out["total_customers"] == 0


def test_one_unreadable_order_file_does_not_hide_the_others(
        client: TestClient, shop: Path) -> None:
    two_customers(shop)
    (shop / "orders" / "ord_00000000dead.json").write_text(
        "{ this is not json", encoding="utf-8")
    out = body(client.get("/customers"))
    assert out["orders_read"] == 4
    assert out["total_customers"] == 2


# -------------------------------------------------------------- the regulars --


def test_regulars_answers_both_questions_at_once(
        client: TestClient, shop: Path) -> None:
    two_customers(shop)
    out = body(client.get("/customers/regulars"))
    assert out["by_spend"][0]["phone"] == "9000011111"
    assert [c["phone"] for c in out["by_frequency"]] == ["9876543210"]
    assert out["min_orders_for_frequency"] == MIN_REGULAR_ORDERS


def test_one_visit_is_not_a_habit(client: TestClient, shop: Path) -> None:
    """Imran spent the most and has been once. He tops one list and not both."""
    two_customers(shop)
    out = body(client.get("/customers/regulars"))
    frequent = [c["phone"] for c in out["by_frequency"]]
    assert "9000011111" not in frequent
    assert "9000011111" in [c["phone"] for c in out["by_spend"]]


def test_cancelled_orders_do_not_make_somebody_a_regular(
        client: TestClient, shop: Path) -> None:
    for i in range(4):
        place(shop, order_id=f"ord_00000000010{i}",
              at=f"2026-08-1{i}T10:00:00+00:00", phone="9111122222",
              name="Serial Canceller", status="cancelled")
    out = body(client.get("/customers/regulars"))
    assert [c["phone"] for c in out["by_frequency"]] == []
    assert out["by_spend"] == []


def test_asking_for_one_ranking_returns_only_that_one(
        client: TestClient, shop: Path) -> None:
    two_customers(shop)
    spend = body(client.get("/customers/regulars?by=spend"))
    assert "by_spend" in spend and "by_frequency" not in spend
    often = body(client.get("/customers/regulars?by=frequency"))
    assert "by_frequency" in often and "by_spend" not in often
    # `orders` is accepted as a synonym rather than refused as pedantry.
    alias = body(client.get("/customers/regulars?by=orders"))
    assert alias["by"] == "frequency"
    assert alias["by_frequency"] == often["by_frequency"]


def test_regulars_respects_a_limit(client: TestClient, shop: Path) -> None:
    two_customers(shop)
    out = body(client.get("/customers/regulars?limit=1"))
    assert len(out["by_spend"]) == 1


# --------------------------------------------------------------- the lookup --


def test_the_whole_number_said_at_the_counter_matches_exactly(
        client: TestClient, shop: Path) -> None:
    two_customers(shop)
    out = body(client.get("/customers/lookup?phone=+91 98765 43210"))
    assert out["matched_on"] == "exact"
    assert out["customer"]["name"] == "Rekha Devi"
    assert out["customer"]["order_count"] == 3
    assert out["detail_url"] == "/customers/9876543210"


def test_the_last_four_digits_are_enough_to_find_somebody(
        client: TestClient, shop: Path) -> None:
    two_customers(shop)
    assert len("4321") == MIN_SEARCH_DIGITS
    out = body(client.get("/customers/lookup?phone=4321"))
    assert out["matched_on"] == "part_of_the_number"
    assert out["customer"] is None
    assert [c["phone"] for c in out["matches"]] == ["9876543210"]
    # One digit short of the floor is a refusal, not a wider search.
    refused(client.get("/customers/lookup?phone=321"), R_SHORT_PHONE)


def test_a_number_nobody_has_is_an_answer_and_not_a_refusal(
        client: TestClient, shop: Path) -> None:
    """A new customer at the counter is the most ordinary event in a shop."""
    two_customers(shop)
    r = client.get("/customers/lookup?phone=9555500000")
    assert r.status_code == 200
    out = body(r)
    assert out["ok"] is True
    assert out["matched_on"] == "none"
    assert out["matches"] == []
    assert out["customer"] is None


# -------------------------------------------------------------- refusals --


def test_a_lookup_with_no_number_is_refused_by_name(
        client: TestClient, shop: Path) -> None:
    refused(client.get("/customers/lookup"), R_NO_PHONE)
    refused(client.get("/customers/lookup?phone=   "), R_NO_PHONE)


def test_a_number_with_no_digits_in_it_is_refused_by_name(
        client: TestClient, shop: Path) -> None:
    out = refused(client.get("/customers/lookup?phone=rekha"), R_BAD_PHONE)
    assert "digits" in out["detail"]
    refused(client.get("/customers/rekha"), R_BAD_PHONE)


def test_too_few_digits_to_search_on_is_refused_by_name(
        client: TestClient, shop: Path) -> None:
    refused(client.get("/customers/lookup?phone=42"), R_SHORT_PHONE)
    # The detail route needs the WHOLE number: five digits is not a person.
    refused(client.get("/customers/54321"), R_SHORT_PHONE)


def test_a_customer_who_has_never_ordered_is_a_404_by_name(
        client: TestClient, shop: Path) -> None:
    two_customers(shop)
    out = refused(client.get("/customers/9555500000"), R_NO_CUSTOMER,
                  status=404)
    assert "9555500000" in out["detail"]


def test_every_shape_of_bad_limit_is_refused_by_name(
        client: TestClient, shop: Path) -> None:
    refused(client.get("/customers?limit=lots"), R_BAD_LIMIT)
    refused(client.get("/customers?limit=0"), R_BAD_LIMIT)
    refused(client.get("/customers?limit=-3"), R_BAD_LIMIT)
    out = refused(client.get(f"/customers?limit={MAX_LIMIT + 1}"), R_BAD_LIMIT)
    assert str(MAX_LIMIT) in out["detail"]
    refused(client.get("/customers/regulars?limit=nope"), R_BAD_LIMIT)
    refused(client.get("/customers/lookup?phone=9876543210&limit=0"),
            R_BAD_LIMIT)


def test_an_ordering_this_shop_does_not_know_is_refused_by_name(
        client: TestClient, shop: Path) -> None:
    out = refused(client.get("/customers?sort=cheapest"), R_BAD_SORT)
    assert "recent" in out["detail"]
    out = refused(client.get("/customers/regulars?by=vibes"), R_BAD_SORT)
    assert "spend" in out["detail"]


def test_a_search_longer_than_the_cap_is_refused_by_name(
        client: TestClient, shop: Path) -> None:
    out = refused(client.get("/customers?q=" + "a" * (MAX_SEARCH + 1)),
                  R_TOO_LONG)
    assert str(MAX_SEARCH) in out["detail"]


def test_an_orders_path_that_is_not_a_directory_is_refused_by_name(
        client: TestClient, shop: Path) -> None:
    """Saying "no customers" here would be a lie with a plausible face."""
    (shop / "orders").write_text("not a directory", encoding="utf-8")
    out = refused(client.get("/customers"), R_NO_ORDERS)
    assert "not a directory" in out["detail"]


def test_a_missing_till_is_refused_by_the_name_the_storefront_gave_it(
        client: TestClient, shop: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_till() -> Path:
        raise storefront.StorefrontRefused(
            R_NO_TILL, "tools/upload_app.py is not importable.")

    monkeypatch.setattr(storefront, "orders_dir", _no_till)
    out = refused(client.get("/customers"), R_NO_TILL)
    assert "upload_app" in out["detail"]


def test_a_missing_storefront_is_refused_by_name(
        client: TestClient, shop: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """It is the module that knows where orders live; without it there is no guess."""
    import gawaah

    monkeypatch.setitem(sys.modules, "gawaah.storefront", None)
    monkeypatch.delattr(gawaah, "storefront", raising=False)
    out = refused(client.get("/customers/regulars"), R_NO_ORDERS_SOURCE)
    assert "storefront" in out["detail"]


def test_an_unexpected_failure_is_a_400_with_a_name_and_never_a_500(
        client: TestClient, shop: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> dict[str, Any]:
        raise RuntimeError("the disk fell off")

    monkeypatch.setattr(customers, "build", _boom)
    for url in ("/customers", "/customers/regulars",
                "/customers/lookup?phone=9876543210", "/customers/9876543210"):
        out = refused(client.get(url), R_INTERNAL)
        assert "RuntimeError" in out["detail"]


def test_no_input_of_any_shape_produces_a_500(
        client: TestClient, shop: Path) -> None:
    two_customers(shop)
    junk = ["%00", "9876543210%20", "null", "0", "+" * 30, "९८७६५४३२१०",
            "'; drop table --", "." * 200, "-1", "9" * 400]
    for s in junk:
        for url in (f"/customers/{s}", f"/customers/lookup?phone={s}",
                    f"/customers?q={s}", f"/customers?sort={s}",
                    f"/customers/regulars?by={s}"):
            r = client.get(url)
            assert r.status_code in (200, 400, 404), (url, r.status_code)
            if r.status_code == 200:
                continue
            out = r.json()
            # Either this module's refusal shape, or the framework's own 404
            # for a string that matched no route at all. Never a 500, and never
            # a stack trace.
            assert out.get("ok") is False or out == {"detail": "Not Found"}, \
                (url, out)


def test_a_phone_that_looks_like_a_file_path_is_refused_by_name(
        client: TestClient, shop: Path) -> None:
    """The path segment is reduced to digits before anything looks at it."""
    two_customers(shop)
    out = refused(client.get("/customers/..-..-results-shop"), R_BAD_PHONE)
    assert "digits" in out["detail"]
    # A segment carrying real separators never reaches this router at all.
    assert client.get("/customers/..%2F..%2Fresults%2Fshop").status_code == 404


# ------------------------------------------------------ it is a view, not a store --


def test_calling_every_route_changes_not_one_byte_on_disk(
        client: TestClient, shop: Path) -> None:
    two_customers(shop)
    before = snapshot(shop)
    for url in ("/customers", "/customers?q=rekha&sort=spend&limit=2",
                "/customers/regulars", "/customers/regulars?by=spend",
                "/customers/lookup?phone=9876543210",
                "/customers/lookup?phone=4321", "/customers/9876543210",
                "/customers/9555500000", "/customers?limit=0"):
        client.get(url)
    assert snapshot(shop) == before


def test_the_module_calls_no_writing_primitive_and_imports_nothing_that_can() -> None:
    """A record that can be edited is one that can disagree with its source.

    Read off the AST rather than by grepping the text, so the prose in the
    docstrings — which talks about writing at length — cannot fail it and,
    more importantly, cannot be used to hide a call inside a string. The
    byte-for-byte comparison above is the claim; this is the reason.
    """
    import ast

    tree = ast.parse(Path(customers.__file__).read_text(encoding="utf-8"))
    called: set[str] = set()
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            called.add(fn.attr if isinstance(fn, ast.Attribute)
                       else getattr(fn, "id", ""))
        elif isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
            imported.update(a.name for a in node.names)

    assert not called & {"open", "write_text", "write_bytes", "mkdir", "touch",
                         "unlink", "rmdir", "makedirs", "rename", "symlink",
                         "copy", "copytree", "move", "dump", "remove"}
    # `os` covers os.replace and os.remove; `ledger` is how anything in this
    # program appends, and a view has nothing to witness.
    assert not imported & {"os", "shutil", "tempfile", "sqlite3", "pickle",
                           "ledger", "Ledger", "urllib", "socket", "requests",
                           "httpx"}


def test_re_deriving_twice_gives_the_same_answer(
        client: TestClient, shop: Path) -> None:
    two_customers(shop)
    first = body(client.get("/customers"))
    second = body(client.get("/customers"))
    assert first == second
    assert body(client.get("/customers/regulars")) == \
        body(client.get("/customers/regulars"))


def test_deleting_an_order_removes_it_from_the_customer(
        client: TestClient, shop: Path) -> None:
    """The proof that nothing is cached: the view follows its source down."""
    two_customers(shop)
    assert body(client.get("/customers/9876543210"))["order_count"] == 3
    (shop / "orders" / "ord_000000000003.json").unlink()
    rekha = body(client.get("/customers/9876543210"))
    assert rekha["order_count"] == 2
    assert rekha["name"] == "Rekha"
    assert rekha["last_order_at"] == "2026-08-09T10:00:00+00:00"


def test_no_route_here_settles_money(client: TestClient, shop: Path) -> None:
    two_customers(shop)
    for url in ("/customers", "/customers/regulars",
                "/customers/lookup?phone=9876543210", "/customers/9876543210"):
        assert body(client.get(url))["settles_money"] is False


# ------------------------------------------------- against the real storefront --


def test_orders_placed_through_the_real_storefront_become_a_customer(
        shop: Path) -> None:
    """The shape asserted above, checked against the shape actually written.

    Every other test here hand-writes order files. This one posts two real
    orders through `gawaah/storefront.py` — which prices them from the till's
    own catalogue — and then reads the customer back, so a change to the order
    format shows up as a failure here rather than as an empty screen.
    """
    for i, (sku, name, price) in enumerate((BISCUIT, SOAP)):
        upload_app.do_enrol_code_only(b"", sku, name, price,
                                      typed=f"890123456780{i}")
    app = FastAPI()
    app.include_router(storefront.router)
    app.include_router(customers.router)
    client = TestClient(app)

    for qty, phone in ((2, "9876543210"), (1, "+91 98765 43210")):
        r = client.post("/store/order", json={
            "items": [{"sku_id": BISCUIT[0], "qty": qty}],
            "name": "Rekha",
            "phone": phone,
            "address": HOME,
        })
        assert r.status_code == 200, r.text

    out = body(client.get("/customers"))
    assert out["total_customers"] == 1
    assert out["customers"][0]["phone"] == "9876543210"
    assert out["customers"][0]["order_count"] == 2
    assert out["customers"][0]["total_paise"] == BISCUIT[2] * 3
    assert HOME not in client.get("/customers").text

    detail = body(client.get("/customers/9876543210"))
    assert detail["addresses"][0]["address"] == HOME
    assert detail["addresses"][0]["orders"] == 2
    assert len(detail["orders"]) == 2
    assert detail["orders"][0]["lines"][0]["sku_id"] == BISCUIT[0]
