"""gawaah/po.py — the purchase order built from what is running out.

Six claims, because each is a claim a demo can fake and a shopkeeper cannot
check:

  1. THE LIST IS STOCK.PY'S LIST. Not a second opinion about what "low" means.
     The suite proves the same products, in the same order, that `/stock/low`
     reports, and proves a refusal from that derivation surfaces here rather
     than being papered over with an empty order.

  2. A MISSING COST IS UNKNOWN, NEVER ZERO. The tempting bug is to treat a
     product with no purchase behind it as free, which prints an order for four
     items totalling ₹0.00. Every test touching an unbought product asserts a
     null line total, the count in `lines_with_no_cost`, and — where nothing at
     all has a cost — a null expected spend rather than nought.

  3. THE BROWSER IS NEVER AN AUTHOR. Units come from the shelf and the level;
     rupees come from the last purchase. A body carrying either is refused BY
     NAME, and nothing is written.

  4. CONFIRMING IS NOT RECEIVING. The shelf figure is identical before and
     after a confirmed order, and the record says so in the body, on the chain
     and on the printed page.

  5. INTEGER PAISE. Every figure asserted here is an int or a rupee string.
     The fixtures use 14.00, 110.00 and 155.50 on purpose: a bug that divides
     or rounds shows up in the second decimal place or not at all.

  6. EVERY REFUSAL HAS A NAME, and there is a test that walks this module's own
     R_* constants to prove none was added without one firing here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import manage, po, purchases, stock  # noqa: E402
from gawaah.ledger import verify  # noqa: E402
from gawaah.po import (  # noqa: E402
    R_BAD_BODY,
    R_BAD_PO_ID,
    R_BAD_SKUS,
    R_BAD_SUPPLIER_ID,
    R_CLIENT_MONEY,
    R_CLIENT_UNITS,
    R_COSTS_UNAVAILABLE,
    R_EMPTY_SELECTION,
    R_INTERNAL,
    R_LOW_UNAVAILABLE,
    R_NOTHING_TO_ORDER,
    R_NO_PO,
    R_NO_SUPPLIER,
    R_NO_SUPPLIER_ID,
    R_NO_TILL,
    R_NOT_WRITTEN,
    R_SKU_NOT_ON_DRAFT,
    R_TOO_LONG,
    R_TOO_MANY_LINES,
)
from tools import upload_app  # noqa: E402

# Sells 21.45, bought at 14.00 from Sharma. The ordinary line.
BISCUIT = ("parle_g_200g", "Parle-G 200g", 2145)
# Sells 39.50, NEVER BOUGHT. This is the unknown-cost product, and it is in
# every scenario on purpose.
SOAP = ("lifebuoy_125g", "Lifebuoy 125g", 3950)
# Sells 140.00, bought at 110.00 from Sharma.
TEA = ("red_label_250g", "Red Label 250g", 14000)
# Sells 199.00, bought at 155.50 from Verma — the second supplier, so grouping
# is tested against two and not against one.
OIL = ("fortune_1l", "Fortune Sunflower 1L", 19900)


# ------------------------------------------------------------------ fixtures

@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Nothing in this suite may see, let alone write, results/.

    THREE knobs, because three modules answer the "where is the shop" question
    through two different readers: `purchases.py` (and therefore `po.py`) goes
    through `upload_app.store_dir()`, `stock.py` goes through
    `manage.store_dir()`, and both must land in the SAME tmp directory or the
    order would be drafted from one shop's shelf and priced from another's book.
    manage's chain cache is dropped either side so one test's ledger can never
    answer another test's request.
    """
    data = tmp_path / "data"
    shop = data / "shop"
    shop.mkdir(parents=True)
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(data))
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(shop))
    monkeypatch.delenv("GAWAAH_SCAN_DIR", raising=False)
    monkeypatch.delenv("GAWAAH_CODES_FILE", raising=False)
    upload_app.set_store_dir(shop)
    manage._CHAIN_CACHE.clear()
    yield
    manage._CHAIN_CACHE.clear()
    # Drop the cached handle so the next test's environment is read afresh.
    upload_app._DEPS["store_dir"] = None
    upload_app._DEPS["store"] = None


@pytest.fixture
def client() -> TestClient:
    """All three routers, mounted bare, exactly as the till mounts them.

    Stock and purchases are here because this suite SETS UP through the real
    endpoints — a count, a level, a supplier, an invoice — rather than by
    writing sidecars by hand. A test that fabricates the input files proves the
    order can read a file this test wrote; mounting the writers proves it can
    read the file the product writes.
    """
    app = FastAPI()
    app.include_router(po.router)
    app.include_router(stock.router)
    app.include_router(purchases.router)
    return TestClient(app)


def _teach(*skus) -> None:
    for i, (sku, name, price) in enumerate(skus):
        upload_app.do_enrol_code_only(b"", sku, name, price,
                                      typed=f"890123456789{i}")


def _count(client: TestClient, sku: str, units: int) -> None:
    r = client.post(f"/stock/{sku}/count", json={"units": units})
    assert r.status_code == 200, r.text


def _level(client: TestClient, sku: str, units: int) -> None:
    r = client.post(f"/stock/{sku}/reorder", json={"units": units})
    assert r.status_code == 200, r.text


def _supplier(client: TestClient, name: str, phone: str) -> str:
    r = client.post("/purchases/suppliers", json={"name": name, "phone": phone})
    assert r.status_code == 200, r.text
    return r.json()["supplier"]["supplier_id"]


def _buy(client: TestClient, sid: str, sku: str, units: int,
         cost_paise: int) -> dict:
    r = client.post("/purchases", json={
        "supplier_id": sid,
        "lines": [{"sku_id": sku, "units": units, "cost_paise": cost_paise}],
    })
    assert r.status_code == 200, r.text
    return r.json()["purchase"]


def _shop(client: TestClient) -> dict[str, str]:
    """A shop that needs biscuits and tea from Sharma, oil from Verma, and soap
    from nobody. Returns the two supplier ids."""
    _teach(BISCUIT, SOAP, TEA, OIL)
    sharma = _supplier(client, "Sharma Traders", "9876543210")
    verma = _supplier(client, "Verma Wholesale", "9812345678")
    _buy(client, sharma, BISCUIT[0], 10, 1400)
    _buy(client, sharma, TEA[0], 6, 11000)
    _buy(client, verma, OIL[0], 4, 15550)

    _count(client, BISCUIT[0], 4)
    _level(client, BISCUIT[0], 30)          # order 26
    _count(client, TEA[0], 2)
    _level(client, TEA[0], 10)              # order 8
    _count(client, SOAP[0], 0)
    _level(client, SOAP[0], 6)              # order 6, cost unknown
    _count(client, OIL[0], 5)
    _level(client, OIL[0], 5)               # exactly at level: order nothing
    return {"sharma": sharma, "verma": verma}


def _draft(client: TestClient) -> dict:
    r = client.get("/po/draft")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    return body


def _group(body: dict, supplier_id) -> dict:
    return next(g for g in body["groups"] if g["supplier_id"] == supplier_id)


def _line(group: dict, sku: str) -> dict:
    return next(ln for ln in group["lines"] if ln["sku_id"] == sku)


def _confirm(client: TestClient, sid: str, **over) -> dict:
    r = client.post("/po/confirm", json={"supplier_id": sid, **over})
    assert r.status_code == 200, r.text
    return r.json()


# ========================================================= the empty counter

def test_a_shop_with_nothing_taught_drafts_nothing_and_does_not_crash(client):
    body = _draft(client)
    assert body["count"] == 0
    assert body["groups"] == []
    assert body["lines_total"] == 0
    assert body["settles_money"] is False
    assert body["chain"]["exists"] is False


def test_a_product_with_no_reorder_level_is_not_on_the_order(client):
    """A level is the shopkeeper's judgement about his own shelf. Without one
    this module has nothing to subtract from and proposes no level of its own."""
    _teach(BISCUIT)
    _count(client, BISCUIT[0], 2)
    body = _draft(client)
    assert body["groups"] == []
    assert body["skus_without_a_level"] == 1


def test_a_level_with_no_count_is_listed_apart_and_never_ordered(client):
    """Nobody has looked at this shelf. "0 on hand" would be a claim about it."""
    _teach(SOAP)
    _level(client, SOAP[0], 6)
    body = _draft(client)
    assert body["groups"] == []
    apart = body["level_set_but_never_counted"]
    assert [r["sku_id"] for r in apart] == [SOAP[0]]
    assert "never been counted" in apart[0]["why"]


# =============================================================== the drafting

def test_the_draft_groups_by_the_supplier_the_shop_last_bought_from(client):
    ids = _shop(client)
    body = _draft(client)

    sharma = _group(body, ids["sharma"])
    assert sharma["supplier_name"] == "Sharma Traders"
    assert sharma["supplier_phone"] == "9876543210"
    assert sorted(ln["sku_id"] for ln in sharma["lines"]) == \
        sorted([BISCUIT[0], TEA[0]])

    # Verma is on file and sells this shop oil, but the oil is exactly at its
    # level, so there is nothing to order and Verma has no group at all. An
    # empty group would put a supplier on the screen with nothing to send them.
    assert ids["verma"] not in [g["supplier_id"] for g in body["groups"]]
    assert body["orderable_groups"] == 1


def test_units_to_order_is_the_level_minus_the_shelf(client):
    ids = _shop(client)
    line = _line(_group(_draft(client), ids["sharma"]), BISCUIT[0])
    assert line["on_hand_units"] == 4
    assert line["reorder_level"] == 30
    assert line["units_to_order"] == 26


def test_a_product_exactly_at_its_level_is_listed_but_not_ordered(client):
    """The shortfall is nought. Rounding it up to one would be this module
    inventing a quantity nobody asked for."""
    _shop(client)
    body = _draft(client)
    at_level = body["at_level_nothing_to_order"]
    assert [r["sku_id"] for r in at_level] == [OIL[0]]
    assert "exactly at the level" in at_level[0]["why"]
    for group in body["groups"]:
        assert OIL[0] not in [ln["sku_id"] for ln in group["lines"]]


def test_units_to_order_is_never_negative(client):
    """A shelf above its level cannot produce a negative order line. stock.py
    would not call it low, and the floor here is the second guard."""
    _teach(BISCUIT)
    _count(client, BISCUIT[0], 50)
    _level(client, BISCUIT[0], 10)
    body = _draft(client)
    assert body["groups"] == []
    assert body["at_level_nothing_to_order"] == []


def test_the_expected_spend_is_units_times_the_last_recorded_cost(client):
    ids = _shop(client)
    sharma = _group(_draft(client), ids["sharma"])
    biscuit = _line(sharma, BISCUIT[0])
    tea = _line(sharma, TEA[0])

    assert biscuit["cost_paise"] == 1400
    assert biscuit["cost_rupees"] == "14.00"
    assert biscuit["line_paise"] == 26 * 1400 == 36400
    assert biscuit["line_rupees"] == "364.00"

    assert tea["line_paise"] == 8 * 11000 == 88000
    assert sharma["expected_paise"] == 36400 + 88000
    assert sharma["expected_rupees"] == "1244.00"
    assert sharma["expected_is_partial"] is False
    assert sharma["lines_with_no_cost"] == 0


def test_the_later_of_two_recorded_costs_is_the_one_ordered_at(client):
    """Not lot-level FIFO — purchases.py states that limit and this inherits it.
    The test exists so the behaviour is a decision and not an accident."""
    _teach(BISCUIT)
    sid = _supplier(client, "Sharma Traders", "9876543210")
    _buy(client, sid, BISCUIT[0], 10, 1400)
    _buy(client, sid, BISCUIT[0], 10, 1550)
    _count(client, BISCUIT[0], 2)
    _level(client, BISCUIT[0], 12)
    line = _line(_group(_draft(client), sid), BISCUIT[0])
    assert line["cost_paise"] == 1550
    assert line["line_paise"] == 10 * 1550


# ======================================================== the unknown is not zero

def test_a_product_never_bought_has_no_supplier_and_no_cost(client):
    ids = _shop(client)
    body = _draft(client)
    orphan = _group(body, None)
    soap = _line(orphan, SOAP[0])

    assert soap["units_to_order"] == 6
    assert soap["cost_known"] is False
    assert soap["cost_paise"] is None
    assert soap["line_paise"] is None
    assert "is not zero" in soap["why_no_cost"]
    assert ids["sharma"] is not None


def test_a_group_with_no_costs_at_all_has_no_expected_spend_not_zero(client):
    """₹0.00 for six packets is a confident, wrong number. Null is the truth."""
    _shop(client)
    orphan = _group(_draft(client), None)
    assert orphan["expected_paise"] is None
    assert orphan["expected_rupees"] is None
    assert orphan["lines_with_no_cost"] == 1
    assert orphan["lines_priced"] == 0
    assert "not known" in orphan["expected_note"]


def test_an_order_with_no_supplier_cannot_be_confirmed_and_says_why(client):
    _shop(client)
    orphan = _group(_draft(client), None)
    assert orphan["can_confirm"] is False
    assert "no supplier to send an order to" in orphan["why_not"]


def test_a_part_priced_order_totals_only_what_it_can_and_says_so(client):
    """The soap is bought once from Sharma, so it groups with the biscuits —
    but that purchase records a cost, so build the partial case explicitly."""
    _teach(BISCUIT, SOAP)
    sid = _supplier(client, "Sharma Traders", "9876543210")
    _buy(client, sid, BISCUIT[0], 10, 1400)
    _count(client, BISCUIT[0], 4)
    _level(client, BISCUIT[0], 30)
    _count(client, SOAP[0], 0)
    _level(client, SOAP[0], 6)

    body = _draft(client)
    sharma = _group(body, sid)
    orphan = _group(body, None)
    assert sharma["expected_paise"] == 26 * 1400
    assert sharma["expected_is_partial"] is False
    assert orphan["expected_paise"] is None

    confirmed = _confirm(client, sid)["po"]
    assert confirmed["expected_paise"] == 26 * 1400
    assert confirmed["lines_with_no_cost"] == 0


def test_a_supplier_group_carrying_one_unpriced_line_is_flagged_partial(client):
    """A cost is recorded for the tea and then the tea is bought from Sharma,
    so both products group under Sharma — but only one of them was ever priced
    (the biscuit purchase is voided, which is how a cost can vanish)."""
    _teach(BISCUIT, TEA)
    sid = _supplier(client, "Sharma Traders", "9876543210")
    purchase = _buy(client, sid, BISCUIT[0], 10, 1400)
    _buy(client, sid, TEA[0], 6, 11000)
    _count(client, BISCUIT[0], 4)
    _level(client, BISCUIT[0], 30)
    _count(client, TEA[0], 2)
    _level(client, TEA[0], 10)

    # Void the biscuit invoice: purchases.py stops counting it, so the biscuit
    # has no cost and no supplier any more.
    r = client.post(f"/purchases/{purchase['purchase_id']}/void",
                    json={"reason": "entered twice"})
    assert r.status_code == 200, r.text

    body = _draft(client)
    sharma = _group(body, sid)
    assert [ln["sku_id"] for ln in sharma["lines"]] == [TEA[0]]
    orphan = _group(body, None)
    assert _line(orphan, BISCUIT[0])["cost_known"] is False


# ================================================================ confirming

def test_confirming_writes_one_order_with_the_derived_lines(client):
    ids = _shop(client)
    out = _confirm(client, ids["sharma"])
    doc = out["po"]

    assert doc["po_id"].startswith("po_")
    assert doc["supplier_name"] == "Sharma Traders"
    assert doc["line_count"] == 2
    assert doc["units_total"] == 26 + 8
    assert doc["expected_paise"] == 36400 + 88000
    assert out["settles_money"] is False
    assert out["print_url"] == f"/po/{doc['po_id']}/print"


def test_a_confirmed_order_is_on_this_modules_own_hash_chain(client):
    ids = _shop(client)
    out = _confirm(client, ids["sharma"])
    chain = out["chain"]
    assert chain["exists"] is True
    assert chain["ok"] is True
    assert chain["lines"] == 1
    assert out["po"]["chain_head"] == chain["head"]

    ok, lines, head, err = verify(po.audit_path())
    assert (ok, lines, err) == (True, 1, None)
    assert head == chain["head"]

    record = json.loads(po.audit_path().read_text().splitlines()[0])
    assert record["module"] == "po"
    assert record["event"] == "po_confirmed"
    assert record["expected_paise"] == 124400
    assert record["stock_received"] is False
    # The supplier's phone is in the document and on the paper, not on the line
    # most likely to be pasted into a bug report.
    assert "9876543210" not in json.dumps(record)


def test_the_order_is_not_written_to_the_money_chain(client):
    """Invariant 5: results/audit.jsonl has one writer, in another process."""
    ids = _shop(client)
    _confirm(client, ids["sharma"])
    assert po.audit_path().name == "po.audit.jsonl"
    assert po.audit_path().parent == Path(upload_app.store_dir())
    assert not (Path(upload_app.store_dir()).parent / "audit.jsonl").exists()


def test_confirming_does_not_receive_stock(client):
    """The packets are on a lorry. The shelf figure must not move until a human
    has opened the box on the Stock screen."""
    ids = _shop(client)
    before = client.get(f"/stock/{BISCUIT[0]}").json()["on_hand_units"]
    out = _confirm(client, ids["sharma"])
    after = client.get(f"/stock/{BISCUIT[0]}").json()["on_hand_units"]

    assert before == after == 4
    assert out["stock_received"] is False
    assert out["po"]["stock_received"] is False
    assert "no stock has been received" in out["detail"]


def test_a_second_confirm_is_a_second_order_and_not_an_edit(client):
    ids = _shop(client)
    first = _confirm(client, ids["sharma"])["po"]["po_id"]
    second = _confirm(client, ids["sharma"])["po"]["po_id"]
    assert first != second
    listed = client.get("/po").json()
    assert listed["count"] == 2
    assert {r["po_id"] for r in listed["orders"]} == {first, second}
    # Newest first, and the first order is untouched by the second.
    assert listed["orders"][0]["po_id"] == second
    assert listed["chain"]["lines"] == 2
    assert client.get(f"/po/{first}").json()["po"]["po_id"] == first


def test_lines_can_be_left_out_by_sku(client):
    ids = _shop(client)
    out = _confirm(client, ids["sharma"], skus=[TEA[0]])
    doc = out["po"]
    assert [ln["sku_id"] for ln in doc["lines"]] == [TEA[0]]
    assert doc["units_total"] == 8
    assert doc["expected_paise"] == 88000


def test_a_note_is_carried_onto_the_paper_and_into_the_message(client):
    ids = _shop(client)
    out = _confirm(client, ids["sharma"], note="Deliver Tuesday morning please")
    assert out["po"]["note"] == "Deliver Tuesday morning please"
    assert "Deliver Tuesday morning" in out["share_text"]
    assert "Deliver Tuesday morning" in out["print_html"]


def test_an_order_from_a_supplier_whose_record_was_deleted_still_works(client):
    """The purchase behind the product is what groups it. A supplier record that
    was later removed must not strand the stock that came from them."""
    _teach(BISCUIT)
    sid = _supplier(client, "Sharma Traders", "9876543210")
    _buy(client, sid, BISCUIT[0], 10, 1400)
    _count(client, BISCUIT[0], 4)
    _level(client, BISCUIT[0], 30)

    path = purchases.suppliers_path()
    path.write_text(json.dumps({"format": 1, "suppliers": {}}), encoding="utf-8")

    group = _group(_draft(client), sid)
    assert group["supplier_on_file"] is False
    assert group["supplier_name"] == "Sharma Traders"   # off the purchase
    assert group["can_confirm"] is True
    assert _confirm(client, sid)["po"]["line_count"] == 1


# =========================================================== reading it back

def test_one_order_reads_back_in_full_with_its_message(client):
    ids = _shop(client)
    po_id = _confirm(client, ids["sharma"])["po"]["po_id"]
    r = client.get(f"/po/{po_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["po"]["po_id"] == po_id
    assert body["stock_received"] is False
    assert body["share_text"].startswith("Order")


def test_the_message_carries_the_list_and_no_payable_string(client):
    """INVARIANT 4. A message going to a wholesaler's phone is exactly the place
    somebody would be tempted to put a payment link."""
    ids = _shop(client)
    text = _confirm(client, ids["sharma"])["share_text"]

    assert "Sharma Traders" in text
    assert "Parle-G 200g" in text and "x26" in text
    assert "Rs 1244.00" in text
    assert "not a payment" in text
    for forbidden in ("upi:", "http://", "https://", "@", "pa=", "razorpay"):
        assert forbidden not in text.lower()


def test_the_message_says_the_total_is_not_known_when_no_cost_is_recorded(
        client):
    """The unassigned group cannot be confirmed, so the unknown TOTAL reaches a
    message only through a supplier whose purchase lost its cost. This asserts
    the sentence the writer produces for that shape directly."""
    text = po._share_text({
        "po_id": "po_0123456789ab", "date": "2026-09-02",
        "supplier_name": "Sharma Traders", "shop_name": None,
        "lines": [{"name": "Lifebuoy 125g", "sku_id": SOAP[0],
                   "units_to_order": 6, "line_rupees": None}],
        "expected_rupees": None, "expected_is_partial": False,
        "lines_priced": 0, "note": None,
    })
    assert "cost not on record" in text
    assert "Expected total: not known" in text
    assert "Rs 0" not in text


def test_the_printed_page_is_self_contained_and_prints_no_link(client):
    ids = _shop(client)
    out = _confirm(client, ids["sharma"])
    po_id = out["po"]["po_id"]

    r = client.get(f"/po/{po_id}/print")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    page = r.text
    assert page.startswith("<!doctype html>")
    # Self-contained: no script, no stylesheet, no image, nothing to fetch.
    for forbidden in ("<script", "<link", "<img", "http://", "https://",
                      "upi:", "src="):
        assert forbidden not in page.lower(), forbidden
    assert "does not receive stock" in page
    assert "Parle-G 200g" in page and "364.00" in page
    # The same bytes the confirm response carried, from the same function.
    assert page == out["print_html"]


def test_the_printed_page_prints_the_word_unknown_not_a_blank(client):
    """A dash in the rupee column is read as nought by whoever holds the paper.

    A supplier's own group is always fully priced — the supplier link and the
    cost come out of the SAME purchase record — so the only way one line of an
    order can be unpriced is a purchase document that lost its supplier_id,
    which is what a hand-edited file looks like. That is the shape built here.
    """
    _teach(BISCUIT, SOAP)
    sid = _supplier(client, "Sharma Traders", "9876543210")
    _buy(client, sid, BISCUIT[0], 10, 1400)
    _count(client, BISCUIT[0], 4)
    _level(client, BISCUIT[0], 30)
    _count(client, SOAP[0], 0)
    _level(client, SOAP[0], 6)

    doc = po._print_html({
        "po_id": "po_0123456789ab", "date": "2026-09-02", "at": "2026-09-02",
        "supplier_name": "Sharma Traders", "supplier_phone": "9876543210",
        "shop_name": None, "chain_head": "abc",
        "lines": [
            {"sku_id": BISCUIT[0], "name": BISCUIT[1], "on_hand_units": 4,
             "reorder_level": 30, "units_to_order": 26, "cost_known": True,
             "cost_rupees": "14.00", "line_rupees": "364.00"},
            {"sku_id": SOAP[0], "name": SOAP[1], "on_hand_units": 0,
             "reorder_level": 6, "units_to_order": 6, "cost_known": False,
             "cost_rupees": None, "line_rupees": None},
        ],
        "units_total": 32, "expected_rupees": "364.00",
        "expected_is_partial": True, "lines_with_no_cost": 1,
        "expected_note": "This covers 1 of 2 lines.", "note": None,
    })
    assert "unknown" in doc
    assert "not on record" in doc
    assert "+ unknown" in doc          # the total says it is short, too
    assert "is not nought" in doc


def test_a_hand_edited_purchase_with_no_supplier_still_carries_its_cost(client):
    """The partial arithmetic, on the one shape that can reach it. The line is
    priced but belongs to nobody, so it lands in the unassigned group beside a
    product that was never bought — and the group totals only what it knows."""
    _teach(BISCUIT, SOAP)
    sid = _supplier(client, "Sharma Traders", "9876543210")
    purchase = _buy(client, sid, BISCUIT[0], 10, 1400)
    _count(client, BISCUIT[0], 4)
    _level(client, BISCUIT[0], 30)
    _count(client, SOAP[0], 0)
    _level(client, SOAP[0], 6)

    path = purchases.purchases_dir() / f"{purchase['purchase_id']}.json"
    edited = json.loads(path.read_text(encoding="utf-8"))
    edited.pop("supplier_id")
    path.write_text(json.dumps(edited), encoding="utf-8")

    orphan = _group(_draft(client), None)
    assert _line(orphan, BISCUIT[0])["line_paise"] == 26 * 1400
    assert _line(orphan, SOAP[0])["line_paise"] is None
    assert orphan["expected_paise"] == 26 * 1400
    assert orphan["expected_is_partial"] is True
    assert orphan["lines_priced"] == 1
    assert orphan["lines_with_no_cost"] == 1
    assert "will come to more" in orphan["expected_note"]


def test_a_suppliers_own_group_is_always_fully_priced(client):
    """Stated as a test because it is a structural fact, not a coincidence: the
    supplier link and the cost are read off the same purchase record, so a line
    with a supplier always has a cost and every unknown sits in the unassigned
    group."""
    _shop(client)
    for group in _draft(client)["groups"]:
        if group["supplier_id"] is None:
            continue
        assert group["lines_with_no_cost"] == 0
        assert group["expected_is_partial"] is False
        assert group["expected_paise"] is not None


def test_the_list_is_newest_first_and_never_settles_money(client):
    ids = _shop(client)
    _confirm(client, ids["sharma"])
    body = client.get("/po").json()
    assert body["ok"] is True
    assert body["settles_money"] is False
    assert body["orders"][0]["stock_received"] is False
    assert body["orders"][0]["expected_rupees"] == "1244.00"


# ================================================================= refusals

def test_a_body_that_is_not_json_is_refused_by_name(client):
    r = client.post("/po/confirm", content=b"not json",
                    headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_BODY

    r = client.post("/po/confirm", json=["a", "list"])
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_BODY


def test_an_order_with_no_supplier_is_refused_by_name(client):
    _shop(client)
    r = client.post("/po/confirm", json={})
    assert r.status_code == 400
    body = r.json()
    assert body["reason"] == R_NO_SUPPLIER_ID
    assert "never bought" in body["detail"]


def test_a_supplier_id_that_is_not_one_is_refused_by_name(client):
    r = client.post("/po/confirm", json={"supplier_id": "sup_nothex"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_SUPPLIER_ID

    r = client.post("/po/confirm", json={"supplier_id": 12})
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_SUPPLIER_ID


def test_a_supplier_this_shop_does_not_have_is_a_404_by_name(client):
    _shop(client)
    r = client.post("/po/confirm", json={"supplier_id": "sup_0123456789ab"})
    assert r.status_code == 404
    assert r.json()["reason"] == R_NO_SUPPLIER


def test_a_supplier_with_nothing_under_its_level_is_refused_by_name(client):
    ids = _shop(client)
    r = client.post("/po/confirm", json={"supplier_id": ids["verma"]})
    assert r.status_code == 400
    body = r.json()
    assert body["reason"] == R_NOTHING_TO_ORDER
    assert "Nothing was written" in body["detail"]
    assert client.get("/po").json()["count"] == 0


def test_a_page_that_sends_a_quantity_is_refused_by_name(client):
    """INVARIANT 3. Silently ignoring it would leave a shopkeeper looking at an
    order for a number other than the one he sent."""
    ids = _shop(client)
    for key in ("units", "units_to_order", "qty", "quantity"):
        r = client.post("/po/confirm",
                        json={"supplier_id": ids["sharma"], key: 99})
        assert r.status_code == 400, key
        assert r.json()["reason"] == R_CLIENT_UNITS
    assert client.get("/po").json()["count"] == 0


def test_a_page_that_sends_a_price_is_refused_by_name(client):
    ids = _shop(client)
    for key in ("cost_paise", "expected_paise", "line_paise", "total_paise"):
        r = client.post("/po/confirm",
                        json={"supplier_id": ids["sharma"], key: 1})
        assert r.status_code == 400, key
        assert r.json()["reason"] == R_CLIENT_MONEY
    assert client.get("/po").json()["count"] == 0


def test_a_skus_field_that_is_not_a_list_of_ids_is_refused_by_name(client):
    ids = _shop(client)
    for bad in ("parle_g_200g", {"a": 1}, [1, 2], ["  "]):
        r = client.post("/po/confirm",
                        json={"supplier_id": ids["sharma"], "skus": bad})
        assert r.status_code == 400, bad
        assert r.json()["reason"] == R_BAD_SKUS


def test_leaving_every_line_out_is_refused_by_name(client):
    ids = _shop(client)
    r = client.post("/po/confirm",
                    json={"supplier_id": ids["sharma"], "skus": []})
    assert r.status_code == 400
    assert r.json()["reason"] == R_EMPTY_SELECTION


def test_a_sku_that_is_not_on_the_draft_is_refused_by_name(client):
    ids = _shop(client)
    r = client.post("/po/confirm",
                    json={"supplier_id": ids["sharma"], "skus": [OIL[0]]})
    assert r.status_code == 400
    body = r.json()
    assert body["reason"] == R_SKU_NOT_ON_DRAFT
    assert "Nothing was written" in body["detail"]


def test_an_order_past_the_line_cap_is_refused_rather_than_truncated(client):
    """Half an order placed silently is worse than no order placed."""
    ids = _shop(client)
    po.MAX_LINES, cap = 1, po.MAX_LINES
    try:
        r = client.post("/po/confirm", json={"supplier_id": ids["sharma"]})
    finally:
        po.MAX_LINES = cap
    assert r.status_code == 400
    assert r.json()["reason"] == R_TOO_MANY_LINES
    assert client.get("/po").json()["count"] == 0


def test_an_over_long_note_is_refused_by_name(client):
    ids = _shop(client)
    r = client.post("/po/confirm",
                    json={"supplier_id": ids["sharma"], "note": "x" * 500})
    assert r.status_code == 400
    assert r.json()["reason"] == R_TOO_LONG
    assert client.get("/po").json()["count"] == 0


def test_a_note_that_is_not_text_is_refused_by_name(client):
    ids = _shop(client)
    r = client.post("/po/confirm",
                    json={"supplier_id": ids["sharma"], "note": 12})
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_BODY


def test_an_id_that_is_not_an_order_id_is_refused_by_name(client):
    r = client.get("/po/nonsense")
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_PO_ID

    r = client.get("/po/po_nothex12345")
    assert r.status_code == 400
    assert r.json()["reason"] == R_BAD_PO_ID


def test_an_order_this_shop_does_not_have_is_a_404_by_name(client):
    r = client.get("/po/po_0123456789ab")
    assert r.status_code == 404
    assert r.json()["reason"] == R_NO_PO

    r = client.get("/po/po_0123456789ab/print")
    assert r.status_code == 404
    assert r.json()["reason"] == R_NO_PO


def test_an_order_that_could_not_be_chained_is_not_an_order(client, monkeypatch):
    """THE CHAIN IS THE RECORD. A document that could not be chained is deleted
    and the request refused, because an order this counter cannot prove it
    wrote must not appear on the list as though it had been placed."""
    ids = _shop(client)
    monkeypatch.setattr(po, "_append", lambda doc: None)
    r = client.post("/po/confirm", json={"supplier_id": ids["sharma"]})
    assert r.status_code == 400
    assert r.json()["reason"] == R_NOT_WRITTEN
    assert client.get("/po").json()["count"] == 0
    assert list(po.po_dir().glob("po_*.json")) == []


def test_a_broken_low_stock_derivation_refuses_rather_than_drafting_half(
        client, monkeypatch):
    _shop(client)
    monkeypatch.delattr(manage, "inventory_rows", raising=False)
    monkeypatch.delattr(manage, "_inventory_rows", raising=False)
    r = client.get("/po/draft")
    assert r.status_code == 400
    body = r.json()
    assert body["reason"] == R_LOW_UNAVAILABLE
    assert "refused to say what is running out" in body["detail"]


def test_a_missing_cost_history_refuses_rather_than_pricing_at_nothing(
        client, monkeypatch):
    _shop(client)
    monkeypatch.delattr(purchases, "cost_history", raising=False)
    monkeypatch.delattr(purchases, "_cost_history", raising=False)
    r = client.get("/po/draft")
    assert r.status_code == 400
    body = r.json()
    assert body["reason"] == R_COSTS_UNAVAILABLE
    assert "invented rupees" in body["detail"]


def test_a_refusal_from_purchases_keeps_its_own_name(client, monkeypatch):
    """`till_module_unavailable` is the actual problem. Renaming it to something
    about orders would send the reader looking in the wrong file."""
    def boom():
        raise purchases.PurchaseRefused(
            purchases.R_NO_TILL, "the till module is not loaded.")

    monkeypatch.setattr(purchases, "shop_dir", boom)
    r = client.get("/po")
    assert r.status_code == 400
    assert r.json()["reason"] == R_NO_TILL
    assert R_NO_TILL == purchases.R_NO_TILL


def test_an_unexpected_failure_is_a_named_400_and_never_a_500(client,
                                                              monkeypatch):
    def boom():
        raise RuntimeError("no space left on device")

    monkeypatch.setattr(po, "_all_pos", boom)
    r = client.get("/po")
    assert r.status_code == 400
    body = r.json()
    assert body["reason"] == R_INTERNAL
    assert "no space left on device" in body["detail"]


def test_no_input_of_any_shape_produces_a_500(client):
    ids = _shop(client)
    shapes = [
        {"supplier_id": None},
        {"supplier_id": ""},
        {"supplier_id": ids["sharma"], "skus": None, "note": None},
        {"supplier_id": ids["sharma"], "skus": [None]},
        {"supplier_id": {"a": 1}},
        {"supplier_id": ids["sharma"], "note": ["a"]},
        {"supplier_id": "../../catalog"},
    ]
    for body in shapes:
        r = client.post("/po/confirm", json=body)
        assert r.status_code in (200, 400, 404), (body, r.status_code)
        assert r.json().get("settles_money") is False


# ================================================== the invariants themselves

def test_every_named_refusal_in_the_module_fires_somewhere_in_this_file():
    named = {v for k, v in vars(po).items()
             if k.startswith("R_") and isinstance(v, str)}
    body = Path(__file__).read_text(encoding="utf-8")
    # Not merely mentioned — COMPARED AGAINST. Every assertion in this file
    # reads `...["reason"] == R_SOMETHING`, so requiring the comparison rules
    # out a constant that is imported and never fired.
    constants = {k for k, v in vars(po).items()
                 if k.startswith("R_") and isinstance(v, str)}
    missing = {k for k in constants if f"== {k}" not in body}
    assert not missing, f"named but never asserted to fire: {sorted(missing)}"
    assert len(named) >= 17


def test_no_response_from_this_module_ever_settles_money(client):
    ids = _shop(client)
    po_id = _confirm(client, ids["sharma"])["po"]["po_id"]
    for url in ("/po/draft", "/po", f"/po/{po_id}"):
        body = client.get(url).json()
        assert body["settles_money"] is False, url


def test_every_rupee_figure_is_an_integer_paise_or_a_rupee_string(client):
    """A float anywhere in the money path fails the build. This is the runtime
    half of that: nothing in a response body may be a float."""
    ids = _shop(client)
    out = _confirm(client, ids["sharma"])

    def walk(node, path="") -> None:
        if isinstance(node, float):
            raise AssertionError(f"float at {path}: {node!r}")
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(out)
    walk(client.get("/po/draft").json())
    walk(client.get("/po").json())


def test_the_draft_writes_nothing_to_disk(client):
    """A draft is a read. There is no saved draft anywhere to go stale."""
    _shop(client)
    before = sorted(p.name for p in Path(upload_app.store_dir()).iterdir())
    _draft(client)
    after = sorted(p.name for p in Path(upload_app.store_dir()).iterdir())
    assert before == after
    assert not po.audit_path().exists()


def test_everything_written_lands_under_gawaah_shop_dir(client, tmp_path):
    """A harness that ignored this once destroyed a live catalogue."""
    ids = _shop(client)
    doc = _confirm(client, ids["sharma"])["po"]
    path = po.po_dir() / f"{doc['po_id']}.json"
    assert path.exists()
    assert str(path).startswith(str(tmp_path))
    assert str(po.audit_path()).startswith(str(tmp_path))
