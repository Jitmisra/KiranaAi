"""Tests for gawaah.offers — the shopkeeper's discount, and the money.

Four properties, in order of how much money they are worth:

  1. THE DISCOUNT REACHES paisa. An offer that only the browser or only the till
     knows about is a total the money service never derived, and invariant 5
     kills the mint at the counter. The tests at the bottom drive a real
     `PaisaService` with a real simulated gateway and assert that a discounted
     basket MINTS, and that an undiscounted one is still refused.
  2. A DISCOUNT MAY NEVER MAKE SOMETHING FREE. Refused at creation, clamped at
     pricing time, and reported when the clamp bites.
  3. INTEGER PAISE. The percentage rounds by a stated rule and the boundary is
     pinned, because "10% of ₹9.99" is where a float would get in.
  4. A REFUSAL IS A RESULT. Every named refusal has a test and none is a 500.

Nothing here may see, let alone write, `results/`. Both the environment and the
till's cached handle are redirected for every test — a harness that honoured
only one of them once destroyed the live catalogue, and that has no undo.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from gawaah import offers  # noqa: E402
from gawaah.clock import VirtualClock  # noqa: E402
from gawaah.kernel import Kernel  # noqa: E402
from gawaah.ledger import Ledger  # noqa: E402
from gawaah.money import MoneyError  # noqa: E402
from gawaah.offers import (  # noqa: E402
    KIND_FLAT,
    KIND_PERCENT,
    MIN_PRICE_PAISE,
    Offer,
    OfferPriceBook,
    discount_off_paise,
    load_offers,
    offers_path,
    quote,
    save_offers,
)
from gawaah.paisa import (  # noqa: E402
    DictPriceBook,
    IntentRequest,
    PaisaConfig,
    PaisaService,
    create_app,
    expected_marker_points,
    rerun_geometry,
)
from gawaah.rzp_sim import RazorpaySim  # noqa: E402
from tools import upload_app  # noqa: E402

# The shop these tests sell out of. Prices chosen so the rounding boundary and
# the clamp are both reachable with real-looking numbers.
BISCUIT = ("parle_g_biscuit", "Parle-G 200g", 1000)     # ₹10.00
SOAP = ("lifebuoy_soap", "Lifebuoy 125g", 3500)         # ₹35.00
SACHET = ("shampoo_sachet", "Shampoo sachet", 300)      # ₹3.00
ODD = ("odd_priced_item", "Nine ninety-nine", 999)      # ₹9.99 — the boundary
CATALOGUE = (BISCUIT, SOAP, SACHET, ODD)


# ------------------------------------------------------------------ rigging


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A shop that lives and dies with the test. Never `results/`."""
    shop = tmp_path / "shop"
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(shop))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAWAAH_OFFERS_FILE", raising=False)
    monkeypatch.delenv("GAWAAH_SCAN_DIR", raising=False)
    monkeypatch.delenv("GAWAAH_CODES_FILE", raising=False)
    upload_app.set_store_dir(shop)
    offers.set_offers_path(None)
    yield
    offers.set_offers_path(None)


@pytest.fixture()
def shop(tmp_path: Path) -> TestClient:
    """The router mounted the way the orchestrator will mount it: bare."""
    for i, (sku, name, price) in enumerate(CATALOGUE):
        upload_app.do_enrol_code_only(b"", sku, name, price,
                                      typed=f"89012345678{i:02d}")
    app = FastAPI()
    app.include_router(offers.router)
    return TestClient(app)


def _make(client: TestClient, **body) -> dict:
    r = client.post("/offers", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _refused(client: TestClient, **body) -> dict:
    r = client.post("/offers", json=body)
    assert r.status_code == 400, r.text
    doc = r.json()
    assert doc["ok"] is False
    assert doc["settles_money"] is False
    assert isinstance(doc["reason"], str) and doc["reason"]
    assert isinstance(doc["detail"], str) and doc["detail"]
    return doc


def _offer(sku, kind, value, *, active=True, created_at="2026-01-01T00:00:00+00:00",
           offer_id="off_000000000001") -> Offer:
    return Offer(offer_id=offer_id, sku_id=sku, kind=kind, value=value,
                 active=active, created_at=created_at, label="")


def _price_of(client: TestClient, sku_id: str) -> dict:
    r = client.get("/offers/prices")
    assert r.status_code == 200, r.text
    rows = {i["sku_id"]: i for i in r.json()["items"]}
    return rows[sku_id]


# ------------------------------------------------------- the arithmetic --


def test_a_flat_offer_takes_exactly_that_many_paise_off() -> None:
    q = quote(SOAP[0], SOAP[2], [_offer(SOAP[0], KIND_FLAT, 500)])
    assert q.price_paise == 3000
    assert q.off_paise == 500
    assert q.clamped is False


def test_a_percentage_offer_takes_a_percentage_off() -> None:
    q = quote(SOAP[0], SOAP[2], [_offer(SOAP[0], KIND_PERCENT, 10)])
    assert q.price_paise == 3150      # ₹35.00 - ₹3.50
    assert q.off_paise == 350


def test_the_percentage_rounds_the_discount_up_at_the_boundary() -> None:
    """THE RULE, PINNED. 10% of ₹9.99 is 99.9 paise and somebody gets the paisa.

    It goes to the customer: the discount rounds UP to 100 paise, so a shutter
    that says 10% off delivers 10.01% and not 9.90%. Rounding the other way
    would leave the price at 900 and the sign would be a lie for one paisa.
    """
    assert discount_off_paise(999, KIND_PERCENT, 10) == 100
    q = quote(ODD[0], 999, [_offer(ODD[0], KIND_PERCENT, 10)])
    assert q.price_paise == 899
    assert q.off_paise == 100


def test_a_percentage_that_divides_exactly_costs_the_shop_nothing_extra() -> None:
    """The ceiling only bites on a remainder; 10% of ₹10.00 is exactly ₹1.00."""
    assert discount_off_paise(1000, KIND_PERCENT, 10) == 100
    assert quote(BISCUIT[0], 1000, [_offer(BISCUIT[0], KIND_PERCENT, 10)]).price_paise == 900


def test_the_rounding_costs_at_most_one_paisa_per_unit_at_every_price() -> None:
    """Ceiling, not something-shaped-like-it. Checked against exact integers."""
    for base in range(1, 400):
        for pct in (1, 3, 7, 10, 33, 50, 99):
            off = discount_off_paise(base, KIND_PERCENT, pct)
            exact_numerator = base * pct
            assert off * 100 >= exact_numerator          # never under-delivers
            assert (off - 1) * 100 < exact_numerator     # never over-delivers


def test_an_offer_is_per_unit_so_two_packets_get_it_twice() -> None:
    """A price book prices ONE unit; that is what "₹5 off Parle-G" means here."""
    q = quote(BISCUIT[0], BISCUIT[2], [_offer(BISCUIT[0], KIND_FLAT, 500)])
    assert q.price_paise * 2 == 1000


def test_an_offer_never_produces_a_float() -> None:
    q = quote(ODD[0], 999, [_offer(ODD[0], KIND_PERCENT, 33)])
    assert isinstance(q.price_paise, int) and not isinstance(q.price_paise, bool)
    assert isinstance(q.off_paise, int)


def test_a_price_book_that_answers_with_a_float_is_refused_not_truncated() -> None:
    """INVARIANT 1 at this boundary too. 214.507 is not 214 paise; it is a bug."""

    class FloatyBook:
        def price_paise(self, item_id):
            return 214.507

    with pytest.raises(MoneyError):
        OfferPriceBook(FloatyBook()).price_paise("anything")


# --------------------------------------------------------------- the clamp --


def test_a_discount_bigger_than_the_price_is_clamped_not_negative() -> None:
    """The shopkeeper dropped the price after making the offer. Still not free."""
    q = quote(SACHET[0], 300, [_offer(SACHET[0], KIND_FLAT, 20000)])
    assert q.price_paise == MIN_PRICE_PAISE
    assert q.price_paise > 0
    assert q.clamped is True


def test_a_discount_equal_to_the_price_is_clamped_off_zero() -> None:
    q = quote(SACHET[0], 300, [_offer(SACHET[0], KIND_FLAT, 300)])
    assert q.price_paise == MIN_PRICE_PAISE
    assert q.clamped is True


def test_a_ninety_nine_percent_offer_on_one_paisa_still_charges_one_paisa() -> None:
    q = quote(SACHET[0], 1, [_offer(SACHET[0], KIND_PERCENT, 99)])
    assert q.price_paise == MIN_PRICE_PAISE
    assert q.clamped is True


def test_the_clamp_is_reported_so_a_shopkeeper_can_see_it(shop: TestClient) -> None:
    """Created while the sachet was ₹3; the price then fell to 1 paisa."""
    made = _make(shop, sku_id=SACHET[0], kind="flat", off_paise=250)
    assert made["offer"]["clamped"] is False
    upload_app.do_enrol_code_only(b"", SACHET[0], SACHET[1], 1, typed="8901234567802")
    row = _price_of(shop, SACHET[0])
    assert row["price_paise"] == MIN_PRICE_PAISE
    assert row["clamped"] is True
    listing = shop.get("/offers").json()
    assert listing["clamped"] == 1


# ----------------------------------------------------------- named refusals --


def test_a_flat_offer_worth_more_than_the_product_is_refused_at_creation(
        shop: TestClient) -> None:
    """₹200 off a ₹10 packet is a typing mistake, not a free packet."""
    doc = _refused(shop, sku_id=BISCUIT[0], kind="flat", off_rupees="200.00")
    assert doc["reason"] == offers.R_EXCEEDS_PRICE
    assert "10.00" in doc["detail"]
    assert load_offers() == []


def test_a_flat_offer_on_everything_is_checked_against_the_cheapest_thing(
        shop: TestClient) -> None:
    """₹5 off everything gives away the ₹3 sachet, so it is refused by name."""
    doc = _refused(shop, sku_id=None, kind="flat", off_rupees="5.00")
    assert doc["reason"] == offers.R_EXCEEDS_PRICE
    assert SACHET[0] in doc["detail"]


def test_an_offer_on_a_product_this_shop_has_never_priced_is_refused(
        shop: TestClient) -> None:
    doc = _refused(shop, sku_id="ghost_masala", kind="percent", percent=10)
    assert doc["reason"] == offers.R_UNKNOWN_SKU
    assert "ghost_masala" in doc["detail"]


def test_a_kind_this_counter_cannot_price_is_refused_by_name(shop: TestClient) -> None:
    doc = _refused(shop, sku_id=BISCUIT[0], kind="buy_one_get_one", percent=10)
    assert doc["reason"] == offers.R_BAD_KIND


def test_a_whole_bill_offer_is_refused_with_the_reason_it_cannot_work(
        shop: TestClient) -> None:
    """It is not "unsupported"; it is unpriceable through a per-unit price book."""
    doc = _refused(shop, sku_id=None, kind="bill_threshold", off_rupees="20.00")
    assert doc["reason"] == offers.R_BILL_KIND
    assert "price book" in doc["detail"]


def test_a_percentage_of_a_hundred_is_refused(shop: TestClient) -> None:
    doc = _refused(shop, sku_id=SOAP[0], kind="percent", percent=100)
    assert doc["reason"] == offers.R_PERCENT_RANGE


def test_a_percentage_beyond_a_hundred_is_refused(shop: TestClient) -> None:
    doc = _refused(shop, sku_id=SOAP[0], kind="percent", percent=250)
    assert doc["reason"] == offers.R_PERCENT_RANGE


def test_an_offer_of_nothing_is_refused(shop: TestClient) -> None:
    doc = _refused(shop, sku_id=SOAP[0], kind="percent", percent=0)
    assert doc["reason"] == offers.R_NOTHING_OFF


def test_a_negative_flat_offer_is_refused(shop: TestClient) -> None:
    """A negative discount is a surcharge, and nobody meant to type one."""
    doc = _refused(shop, sku_id=SOAP[0], kind="flat", off_paise=-500)
    assert doc["reason"] == offers.R_NOTHING_OFF


def test_a_fractional_discount_is_refused_rather_than_rounded(shop: TestClient) -> None:
    doc = _refused(shop, sku_id=SOAP[0], kind="flat", off_paise=250.5)
    assert doc["reason"] == offers.R_BAD_VALUE


def test_a_rupee_amount_sent_as_a_number_is_refused(shop: TestClient) -> None:
    """`float('5.10')` is lossy before anything rounds it, so the wire takes text."""
    doc = _refused(shop, sku_id=SOAP[0], kind="flat", off_rupees=5.1)
    assert doc["reason"] == offers.R_BAD_VALUE


def test_a_rupee_string_with_sub_paisa_precision_is_refused(shop: TestClient) -> None:
    doc = _refused(shop, sku_id=SOAP[0], kind="flat", off_rupees="5.005")
    assert doc["reason"] == offers.R_BAD_VALUE


def test_a_percentage_offer_with_no_percentage_is_refused(shop: TestClient) -> None:
    doc = _refused(shop, sku_id=SOAP[0], kind="percent")
    assert doc["reason"] == offers.R_BAD_VALUE


def test_a_flat_offer_with_no_amount_is_refused(shop: TestClient) -> None:
    doc = _refused(shop, sku_id=SOAP[0], kind="flat")
    assert doc["reason"] == offers.R_BAD_VALUE


def test_a_body_that_is_not_json_is_a_named_refusal_not_a_crash(
        shop: TestClient) -> None:
    r = shop.post("/offers", content=b"not json at all",
                  headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.json()["reason"] == offers.R_BAD_BODY


def test_a_body_that_is_a_list_is_a_named_refusal(shop: TestClient) -> None:
    r = shop.post("/offers", json=[1, 2, 3])
    assert r.status_code == 400
    assert r.json()["reason"] == offers.R_BAD_BODY


def test_a_label_longer_than_the_field_is_refused(shop: TestClient) -> None:
    doc = _refused(shop, sku_id=SOAP[0], kind="percent", percent=10,
                   label="x" * (offers.MAX_LABEL + 1))
    assert doc["reason"] == offers.R_TOO_LONG


def test_an_sku_that_is_not_text_is_refused(shop: TestClient) -> None:
    doc = _refused(shop, sku_id=7, kind="percent", percent=10)
    assert doc["reason"] == offers.R_BAD_SKU


def test_turning_on_an_offer_that_does_not_exist_is_a_named_404(
        shop: TestClient) -> None:
    r = shop.post("/offers/off_ffffffffffff/active", json={"active": True})
    assert r.status_code == 404
    assert r.json()["reason"] == offers.R_NO_OFFER


def test_deleting_an_offer_that_does_not_exist_is_a_named_404(
        shop: TestClient) -> None:
    r = shop.delete("/offers/off_ffffffffffff")
    assert r.status_code == 404
    assert r.json()["reason"] == offers.R_NO_OFFER


def test_active_must_be_true_or_false(shop: TestClient) -> None:
    made = _make(shop, sku_id=SOAP[0], kind="percent", percent=10)
    r = shop.post(f"/offers/{made['offer']['offer_id']}/active",
                  json={"active": "yes"})
    assert r.status_code == 400
    assert r.json()["reason"] == offers.R_BAD_ACTIVE


def test_more_offers_than_this_counter_holds_is_refused(shop: TestClient) -> None:
    rows = [_offer(SOAP[0], KIND_PERCENT, 5, offer_id=f"off_{i:012x}")
            for i in range(offers.MAX_OFFERS)]
    save_offers(rows)
    doc = _refused(shop, sku_id=SOAP[0], kind="percent", percent=10)
    assert doc["reason"] == offers.R_TOO_MANY


def test_every_refusal_code_is_a_string_a_person_can_act_on() -> None:
    """No bare codes, no numbers, nothing a shopkeeper would have to look up."""
    named = [v for k, v in vars(offers).items()
             if k.startswith("R_") and isinstance(v, str)]
    assert len(named) >= 14
    for code in named:
        assert code == code.lower()
        assert " " not in code


# --------------------------------------------------------- being switched off --


def test_an_inactive_offer_changes_no_price_at_all(shop: TestClient) -> None:
    made = _make(shop, sku_id=SOAP[0], kind="percent", percent=10)
    assert _price_of(shop, SOAP[0])["price_paise"] == 3150
    r = shop.post(f"/offers/{made['offer']['offer_id']}/active",
                  json={"active": False})
    assert r.status_code == 200
    assert _price_of(shop, SOAP[0])["price_paise"] == SOAP[2]
    assert _price_of(shop, SOAP[0])["off_paise"] == 0
    assert _price_of(shop, SOAP[0])["offer_id"] is None


def test_an_offer_created_switched_off_has_no_effect_until_it_is_switched_on(
        shop: TestClient) -> None:
    made = _make(shop, sku_id=SOAP[0], kind="flat", off_rupees="5.00", active=False)
    assert _price_of(shop, SOAP[0])["price_paise"] == SOAP[2]
    shop.post(f"/offers/{made['offer']['offer_id']}/active", json={"active": True})
    assert _price_of(shop, SOAP[0])["price_paise"] == 3000


def test_a_deleted_offer_returns_the_product_to_its_marked_price(
        shop: TestClient) -> None:
    made = _make(shop, sku_id=SOAP[0], kind="flat", off_rupees="5.00")
    assert _price_of(shop, SOAP[0])["price_paise"] == 3000
    r = shop.delete(f"/offers/{made['offer']['offer_id']}")
    assert r.status_code == 200
    assert _price_of(shop, SOAP[0])["price_paise"] == SOAP[2]
    assert load_offers() == []


# ------------------------------------------------------------- two offers --


def test_only_one_offer_applies_and_it_is_the_best_one_for_the_customer() -> None:
    """No stacking: two 60% offers would compound to 84% and nobody could audit it."""
    rows = [
        _offer(SOAP[0], KIND_FLAT, 200, offer_id="off_000000000001"),
        _offer(None, KIND_PERCENT, 20, offer_id="off_000000000002"),
    ]
    q = quote(SOAP[0], 3500, rows)
    assert q.off_paise == 700               # 20%, not 700 + 200
    assert q.offer_id == "off_000000000002"


def test_an_offer_of_a_kind_this_version_cannot_price_is_skipped_not_raised() -> None:
    """A 500 inside paisa's price book is the one crash this program may not have."""
    weird = Offer("off_00000000000f", SOAP[0], "buy_one_get_one", 1, True,
                  "2026-01-01T00:00:00+00:00", "")
    q = quote(SOAP[0], 3500, [weird, _offer(SOAP[0], KIND_FLAT, 500)])
    assert q.price_paise == 3000
    assert quote(SOAP[0], 3500, [weird]).price_paise == 3500


def test_a_tie_between_two_offers_goes_to_the_one_created_first() -> None:
    """Deterministic, so two machines reading one file charge the same rupee."""
    rows = [
        _offer(SOAP[0], KIND_FLAT, 350, offer_id="off_00000000000b",
               created_at="2026-02-01T00:00:00+00:00"),
        _offer(SOAP[0], KIND_PERCENT, 10, offer_id="off_00000000000a",
               created_at="2026-01-01T00:00:00+00:00"),
    ]
    assert quote(SOAP[0], 3500, rows).offer_id == "off_00000000000a"
    assert quote(SOAP[0], 3500, list(reversed(rows))).offer_id == "off_00000000000a"


def test_an_offer_on_everything_reaches_a_product_with_no_offer_of_its_own(
        shop: TestClient) -> None:
    _make(shop, sku_id=None, kind="percent", percent=10)
    assert _price_of(shop, SOAP[0])["price_paise"] == 3150
    assert _price_of(shop, BISCUIT[0])["price_paise"] == 900


def test_a_star_means_every_product_because_a_form_cannot_send_null(
        shop: TestClient) -> None:
    made = _make(shop, sku_id="*", kind="percent", percent=10)
    assert made["offer"]["sku_id"] is None
    assert made["offer"]["scope"] == "every product"


# ------------------------------------------------------------ the storage --


def test_nothing_is_written_outside_the_shop_directory(shop: TestClient,
                                                       tmp_path: Path) -> None:
    """GAWAAH_SHOP_DIR, honoured. A harness once destroyed the live catalogue.

    The live file is checked by its BYTES, not by its absence. Asserting it does
    not exist would be an assertion about the developer's machine — it passes on
    a clean checkout, fails the moment anybody makes a real offer, and teaches
    whoever hits it that the test is noise. What matters is that this test did
    not touch it, and that is what is measured.
    """
    live = Path(REPO) / "results" / "shop" / "offers.json"
    before = live.read_bytes() if live.exists() else None

    _make(shop, sku_id=SOAP[0], kind="percent", percent=10)

    where = offers_path()
    assert where == tmp_path / "shop" / "offers.json"
    assert where.exists()
    after = live.read_bytes() if live.exists() else None
    assert after == before


def test_an_unreadable_offers_file_charges_the_marked_price_and_never_crashes(
        shop: TestClient) -> None:
    """Half a JSON document must not turn a working till into a dead one.

    Falling back to NO offers is the only outcome in which the till and the
    money service still agree with each other, so nothing is mispriced — the
    customer is charged the marked price and the shopkeeper sees no discount.
    """
    _make(shop, sku_id=SOAP[0], kind="percent", percent=10)
    offers_path().write_text('{"offers": [{"offer_id": "off_00', encoding="utf-8")
    assert load_offers() == []
    assert _price_of(shop, SOAP[0])["price_paise"] == SOAP[2]


def test_a_single_malformed_row_is_dropped_and_the_rest_still_apply() -> None:
    offers_path().parent.mkdir(parents=True, exist_ok=True)
    offers_path().write_text(json.dumps({"offers": [
        {"offer_id": "not-an-id", "kind": "flat", "value": 100},
        {"offer_id": "off_00000000000c", "sku_id": SOAP[0], "kind": "flat",
         "value": 500, "active": True, "created_at": "2026-01-01T00:00:00+00:00"},
        {"offer_id": "off_00000000000d", "kind": "percent", "value": 500,
         "active": True, "created_at": "2026-01-01T00:00:00+00:00"},
    ]}), encoding="utf-8")
    rows = load_offers()
    assert [o.offer_id for o in rows] == ["off_00000000000c"]


def test_a_stored_offer_whose_value_is_a_float_is_dropped_not_truncated() -> None:
    """214.507 must never become 214. The row is refused whole."""
    offers_path().parent.mkdir(parents=True, exist_ok=True)
    offers_path().write_text(json.dumps({"offers": [
        {"offer_id": "off_00000000000e", "sku_id": SOAP[0], "kind": "flat",
         "value": 500.5, "active": True, "created_at": "2026-01-01T00:00:00+00:00"},
    ]}), encoding="utf-8")
    assert load_offers() == []


def test_the_offer_survives_a_round_trip_through_the_file(shop: TestClient) -> None:
    made = _make(shop, sku_id=SOAP[0], kind="flat", off_rupees="5.00",
                 label="Diwali")
    again = shop.get("/offers").json()
    assert again["count"] == 1
    row = again["offers"][0]
    assert row["offer_id"] == made["offer"]["offer_id"]
    assert row["sku_id"] == SOAP[0]
    assert row["kind"] == KIND_FLAT
    assert row["value"] == 500
    assert row["label"] == "Diwali"
    assert row["says"] == f"₹5.00 off {SOAP[0]}"


def test_health_names_the_file_both_processes_must_agree_on(
        shop: TestClient, tmp_path: Path) -> None:
    r = shop.get("/offers/health")
    assert r.status_code == 200
    body = r.json()
    assert body["file"] == str(tmp_path / "shop" / "offers.json")
    assert body["shop_dir"] == str(tmp_path / "shop")
    assert body["min_price_paise"] == MIN_PRICE_PAISE


# ------------------------------------------------------------ the pricebook --


def test_the_price_book_wrapper_discounts_what_paisa_asks_it_for() -> None:
    save_offers([_offer(SOAP[0], KIND_PERCENT, 10)])
    book = OfferPriceBook(DictPriceBook({SOAP[0]: 3500, BISCUIT[0]: 1000}))
    assert book.price_paise(SOAP[0]) == 3150
    assert book.price_paise(BISCUIT[0]) == 1000


def test_an_offer_never_invents_a_price_for_something_never_taught() -> None:
    """Amber stays amber. 10% off nothing is still nothing, not a guess."""
    save_offers([_offer(None, KIND_PERCENT, 50)])
    book = OfferPriceBook(DictPriceBook({SOAP[0]: 3500}))
    assert book.price_paise("never_taught") is None


def test_the_price_book_notices_an_offer_created_after_it_was_built() -> None:
    """The money service boots at seven; the offer is written at three."""
    book = OfferPriceBook(DictPriceBook({SOAP[0]: 3500}))
    assert book.price_paise(SOAP[0]) == 3500
    save_offers([_offer(SOAP[0], KIND_FLAT, 500)])
    assert book.price_paise(SOAP[0]) == 3000


def test_the_price_book_still_reports_how_many_skus_it_knows() -> None:
    """`/health` reads len(price_book); a wrapper that broke it would blind it."""
    book = OfferPriceBook(DictPriceBook({SOAP[0]: 3500, BISCUIT[0]: 1000}))
    assert len(book) == 2


# ------------------------------------------------------------ and the money --
#
# Everything above is arithmetic. These drive the real PaisaService with a real
# simulated gateway, because the only claim worth making about a discount is
# that money moves at the discounted amount.

IN_YS = (390.0, 392.0, 394.0, 396.0)
OUT_YS = (406.0, 409.0, 412.0)


def _out_path(x_mm: float) -> list[list[float]]:
    return [[x_mm, y] for y in IN_YS] + [[x_mm, y] for y in OUT_YS]


def _identity_corners() -> list[list[float]]:
    pts, _ = expected_marker_points()
    assert pts is not None
    return [[float(x), float(y)] for x, y in pts]


def _basket(amount_paise: int, session_id: str = "sess-offer") -> dict:
    return {
        "session_id": session_id,
        "amount_paise": amount_paise,
        "geometry": {
            "H": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            "corners": _identity_corners(),
            "crossings": [
                {"item_id": SOAP[0], "track_id": 1, "path_mm": _out_path(80.0),
                 "committed": True, "name": SOAP[1]},
                {"item_id": BISCUIT[0], "track_id": 2, "path_mm": _out_path(160.0),
                 "committed": True, "name": BISCUIT[1]},
            ],
            "untracked": [],
            "min_crossing_frames": 3,
        },
    }


@pytest.fixture()
def money(tmp_path: Path) -> TestClient:
    """A whole money service, with the offers wired in exactly as live_app does."""
    clock = VirtualClock()
    ledger = Ledger(str(tmp_path / "money.audit.jsonl"))
    kernel = Kernel(str(tmp_path / "money.db"), clock, ledger)
    cfg = PaisaConfig(mode="sim", key_id="rzp_test_OFFERS",
                      key_secret="secret", webhook_secret="whsec_offers", seed=11)
    svc = PaisaService(
        clock=clock,
        ledger=ledger,
        kernel=kernel,
        gateway=RazorpaySim(webhook_secret=cfg.effective_webhook_secret,
                            clock=clock, seed=11),
        config=cfg,
        price_book=OfferPriceBook(DictPriceBook({SOAP[0]: 3500, BISCUIT[0]: 1000})),
    )
    return TestClient(create_app(svc))


def test_paisa_reprices_a_discounted_basket_and_agrees_with_it() -> None:
    """The server's OWN re-run, from its own book, lands on the discounted total."""
    save_offers([
        _offer(SOAP[0], KIND_PERCENT, 10, offer_id="off_00000000001a"),
        _offer(BISCUIT[0], KIND_FLAT, 500, offer_id="off_00000000001b"),
    ])
    book = OfferPriceBook(DictPriceBook({SOAP[0]: 3500, BISCUIT[0]: 1000}))
    verdict = rerun_geometry(IntentRequest(**_basket(3150 + 500)), book)
    assert verdict.agrees is True, verdict.detail
    assert verdict.server_total_paise == 3650


def test_a_discounted_basket_mints(money: TestClient) -> None:
    """THE POINT OF THE WHOLE MODULE. Money moves, at the discounted amount."""
    save_offers([
        _offer(SOAP[0], KIND_PERCENT, 10, offer_id="off_00000000001a"),
        _offer(BISCUIT[0], KIND_FLAT, 500, offer_id="off_00000000001b"),
    ])
    r = money.post("/intent", json=_basket(3650))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["amount_paise"] == 3650
    assert body["amount_rupees"] == "36.50"
    assert body["short_url"]
    assert body["state"] == "CALLING"


def test_the_undiscounted_total_is_still_refused_while_an_offer_is_on(
        money: TestClient) -> None:
    """INVARIANT 5 INTACT. Offers do not teach paisa to believe the till."""
    save_offers([_offer(SOAP[0], KIND_PERCENT, 10, offer_id="off_00000000001a")])
    r = money.post("/intent", json=_basket(4500))
    assert r.status_code == 409
    assert r.json()["error"] == "amount_disagreement"
    assert r.json()["minted"] is False


def test_the_full_price_basket_mints_when_the_offer_is_switched_off(
        money: TestClient) -> None:
    save_offers([_offer(SOAP[0], KIND_PERCENT, 10, active=False)])
    r = money.post("/intent", json=_basket(4500))
    assert r.status_code == 200, r.text
    assert r.json()["amount_paise"] == 4500


def test_a_line_a_discount_would_have_zeroed_still_charges_something(
        money: TestClient) -> None:
    """The clamp holds on the money path, not only in the arithmetic tests."""
    save_offers([_offer(BISCUIT[0], KIND_FLAT, 900_00, offer_id="off_00000000001c")])
    r = money.post("/intent", json=_basket(3500 + MIN_PRICE_PAISE))
    assert r.status_code == 200, r.text
    assert r.json()["amount_paise"] == 3501


def test_an_offer_written_after_the_money_service_started_is_still_applied(
        money: TestClient) -> None:
    """No restart. The book re-reads the file the moment its mtime moves."""
    assert money.post("/intent", json=_basket(4500, "sess-a")).status_code == 200
    save_offers([_offer(SOAP[0], KIND_FLAT, 500, offer_id="off_00000000001d")])
    r = money.post("/intent", json=_basket(4000, "sess-b"))
    assert r.status_code == 200, r.text
    assert r.json()["amount_paise"] == 4000
