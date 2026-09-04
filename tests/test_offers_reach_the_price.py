"""An offer that only some screens know about is worse than no offer.

THE BREAK THIS PINS, measured the first time an offer was switched on with
everything else already wired:

    storefront quoted   3500 paise  (the marked price)
    paisa derived       3150 paise  (its own book, offers applied)
    payment             REFUSED, scan_total_disagreement

That refusal is invariant 5 working exactly as designed — the browser proposed
a total the money service had not derived, and the money service declined
rather than charge it. The bug was not the check. It was a shop quoting a price
it was not going to charge.

paisa re-prices every basket through `offers.OfferPriceBook`, so the discount is
a number PAISA DERIVES. Every other surface that puts a price in front of a
customer has to ask the same question, or it proposes a total that cannot be
minted. These tests hold each of those surfaces to it.
"""

from __future__ import annotations

import pathlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gawaah import offers  # noqa: E402
from tools import upload_app  # noqa: E402


SKU = "__offer_probe__"
BASE = 5000          # ₹50.00


@pytest.fixture
def shop(tmp_path, monkeypatch):
    """A scratch shop with one product. Never `results/`."""
    d = tmp_path / "shop"
    d.mkdir()
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(d))
    upload_app.set_store_dir(d)
    offers.set_offers_path(d / "offers.json")
    upload_app._ao_put(SKU, "Probe", BASE, [], None)
    yield d
    offers.save_offers([], d / "offers.json")


def _oid(seed: int) -> str:
    """`off_` + twelve hex, which is what `OFFER_ID_RE` accepts.

    A made-up id like `off_probe_10` is DROPPED by `_offer_from_record` — the
    loader refuses records it cannot trust rather than raising on the money
    path. That is right, and it silently made four of these tests assert that a
    discount had not been applied when in fact no offer had been loaded at all.
    """
    return f"off_{seed:012x}"


def _percent(pct: int) -> offers.Offer:
    return offers.Offer(
        offer_id=_oid(pct), sku_id=SKU, kind=offers.KIND_PERCENT,
        value=pct, label=f"{pct}% off", active=True, created_at="2026-09-01T00:00:00+00:00",
    )


# ------------------------------------------------ the surfaces that price --

def test_the_marked_price_is_untouched_without_an_offer(shop):
    assert upload_app.priced_skus()[SKU]["price_paise"] == BASE
    assert upload_app.offer_priced_skus()[SKU]["price_paise"] == BASE


def test_an_active_offer_changes_what_the_till_will_charge(shop):
    offers.save_offers([_percent(10)], shop / "offers.json")
    row = upload_app.offer_priced_skus()[SKU]
    assert row["price_paise"] == 4500
    # The shelf-edge price rides along so a screen can show it struck through.
    # A line that is cheaper than the shelf, with no explanation, reads as a
    # mistake rather than a discount.
    assert row["marked_paise"] == BASE
    assert row["off_paise"] == 500


def test_the_undiscounted_view_stays_undiscounted(shop):
    """`priced_skus()` is what `gawaah/offers.py` reads to learn the marked
    price. Discounting it there would apply every offer twice — a 10% offer
    would quietly become 19%."""
    offers.save_offers([_percent(10)], shop / "offers.json")
    assert upload_app.priced_skus()[SKU]["price_paise"] == BASE


def test_the_customers_storefront_quotes_the_price_it_will_charge(shop):
    """THE SURFACE THAT ACTUALLY BROKE. It read `priced_skus()` directly."""
    from gawaah import storefront
    offers.save_offers([_percent(10)], shop / "offers.json")
    assert storefront.catalogue()[SKU]["price_paise"] == 4500


def test_the_catalogue_the_till_and_the_voice_bar_read_agrees(shop):
    """`/shop` is assembled from TWO stores. Discounting one input showed the
    offer on code-taught rows and the marked price on every other."""
    offers.save_offers([_percent(10)], shop / "offers.json")
    row = next(r for r in upload_app.catalog()["skus"] if r["sku_id"] == SKU)
    assert row["price_paise"] == 4500
    assert row["marked_paise"] == BASE


def test_every_pricing_surface_gives_the_same_answer(shop):
    """The property that matters. Any one of these disagreeing is a total the
    money service will refuse."""
    offers.save_offers([_percent(25)], shop / "offers.json")
    from gawaah import storefront
    answers = {
        "offer_priced_skus": upload_app.offer_priced_skus()[SKU]["price_paise"],
        "storefront": storefront.catalogue()[SKU]["price_paise"],
        "catalog": next(r for r in upload_app.catalog()["skus"]
                        if r["sku_id"] == SKU)["price_paise"],
        "paisa's own book": offers.OfferPriceBook(
            _FixedBook({SKU: BASE}), path=shop / "offers.json").price_paise(SKU),
    }
    assert len(set(answers.values())) == 1, answers
    assert answers["offer_priced_skus"] == 3750


class _FixedBook:
    def __init__(self, prices):
        self._p = dict(prices)

    def price_paise(self, item_id):
        return self._p.get(item_id)


# --------------------------------------------------------- money is money --

def test_the_book_paisa_prices_from_is_never_written_discounted(shop):
    """`publish_price_map()` writes the MARKED prices. paisa's own
    `OfferPriceBook` then discounts them. Writing discounted prices there too
    would apply every offer twice, and nothing would ever say so."""
    offers.save_offers([_percent(10)], shop / "offers.json")
    import json
    # `store_dir().parent / "shop.json"` — the file `live_app` loads, which is
    # one level ABOVE the catalogue directory, not inside it. Two price files
    # once existed and products taught on this site reached only the inner one,
    # so at mint time they were unpriceable and fell out of the bill as amber.
    written = upload_app.publish_price_map()
    assert written is not None, "the money service was left unable to see a price"
    book = json.loads(pathlib.Path(written).read_text())
    assert book[SKU] == BASE


def test_the_price_map_lands_where_the_money_service_actually_reads(shop, tmp_path, monkeypatch):
    """The writer and the reader must name the SAME file in every layout.

    `live_app.py` loads `GAWAAH_DATA_DIR / shop.json`. This function used to
    write `store_dir().parent / "shop.json"` unconditionally, which is that file
    only when the catalogue happens to sit one level inside the data directory.
    Point the two variables at different places and the publish succeeded, the
    caller was told the money service could see the product, and the money
    service was reading an entirely different path — so every taught product
    was unpriceable at mint and fell out of the bill as amber. That is the
    quietly-short total this program calls disqualifying.
    """
    import json

    elsewhere = tmp_path / "money-reads-here"
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(elsewhere))
    written = upload_app.publish_price_map()
    assert written is not None
    assert pathlib.Path(written) == elsewhere / "shop.json", (
        "published to the catalogue's parent, which is not where paisa reads")
    assert json.loads(pathlib.Path(written).read_text())[SKU] == BASE

    # And with the variable unset the old layout is unchanged: beside the
    # catalogue's parent, which is `results/` in the shipped arrangement.
    monkeypatch.delenv("GAWAAH_DATA_DIR")
    again = upload_app.publish_price_map()
    assert pathlib.Path(again) == upload_app.store_dir().parent / "shop.json"


def test_a_discount_never_reaches_zero_or_below(shop):
    huge = offers.Offer(offer_id=_oid(0xBAD), sku_id=SKU, kind=offers.KIND_FLAT,
                        value=BASE * 10, label="broken", active=True,
                        created_at="2026-09-01T00:00:00+00:00")
    offers.save_offers([huge], shop / "offers.json")
    price = upload_app.offer_priced_skus()[SKU]["price_paise"]
    assert price >= offers.MIN_PRICE_PAISE
    assert price > 0


def test_an_inactive_offer_changes_nothing(shop):
    off = _percent(10)
    offers.save_offers([offers.Offer(**{**off.__dict__, "active": False})],
                       shop / "offers.json")
    assert upload_app.offer_priced_skus()[SKU]["price_paise"] == BASE


def test_an_unreadable_offers_file_falls_back_to_the_marked_price(shop):
    """It must never take the till down, and must never invent a price."""
    (shop / "offers.json").write_text("{ this is not json")
    assert upload_app.offer_priced_skus()[SKU]["price_paise"] == BASE


def test_every_discounted_price_is_a_whole_number_of_paise(shop):
    for pct in range(1, 100):
        offers.save_offers([_percent(pct)], shop / "offers.json")
        p = upload_app.offer_priced_skus()[SKU]["price_paise"]
        assert isinstance(p, int), (pct, p)
        assert p == int(p)
