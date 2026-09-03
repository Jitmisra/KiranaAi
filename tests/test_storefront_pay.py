"""A customer who orders from their phone can pay — and cannot be overcharged.

THE DEFECT THESE TESTS PIN. The customer's journey worked to the last step and
died there. The catalogue loaded, the order was placed and priced by the server,
the shopkeeper saw it arrive, and PAY returned:

    POST /store/order/ord_d0aa4191c87d/pay -> 400
    {"reason": "scan_not_found",
     "detail": "no scan witness 'orde349f24e2cdc452e1a' on this counter"}

The witness had been written correctly half a second earlier. It was in the
wrong DIRECTORY. `gawaah/storefront.py` wrote it to `upload_app.scans_dir()` —
`store_dir().parent / "scans"`, a fact about the till's own layout — while
`paisa.load_scan_witness` reads `GAWAAH_SCAN_DIR`, or `<GAWAAH_DATA_DIR>/scans`.
Two answers to one question, agreeing only by the coincidence of the shipped
directory tree, and the refusal blamed the customer's order for it.

So the fixture below deliberately uses the DIVERGENT layout: the catalogue is at
`<tmp>/catalogue` and the data directory is `<tmp>/data`, so the till's rule and
paisa's rule point at two different places. Every test here would have failed
before the fix, and the mint tests run against a REAL `PaisaService` — the real
`/intent` route, the real `rerun_scan`, the real kernel, the real hash-chained
ledger — with only the gateway simulated. Nothing here stubs the re-derivation
that invariant 5 is made of.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# `from tools import upload_app`, AND NOT `import upload_app`, WHICH COST AN
# HOUR. The two spellings register the till under two different names, and
# `gawaah/till_ref.py` resolves `"upload_app"` BEFORE `"tools.upload_app"` — so
# a single file importing it the second way gives the whole suite a second copy
# of a 7,000-line module with its own store handle, and every later test that
# redirects the catalogue redirects the copy the storefront is no longer
# reading. Written up in that module's docstring, from the time it happened in
# the product: the storefront served one catalogue while the money service
# priced another. Thirty test files here use this spelling. This is the
# thirty-first.
from tools import upload_app  # noqa: E402

from gawaah import rzp_sim
from gawaah import storefront  # noqa: E402
from gawaah.clock import RealClock  # noqa: E402
from gawaah.ledger import verify  # noqa: E402
from gawaah.paisa import (  # noqa: E402
    DictPriceBook,
    PaisaConfig,
    build_service,
    create_app,
    load_scan_witness,
)

#: sku, name, integer paise. Ten rupees, so two of them is the twenty rupees the
#: original defect report measured.
BISCUIT = ("biscuit", "Parle-G 200g", 1000)
SOAP = ("lifebuoy", "Lifebuoy 100g", 3500)

KEY_SECRET = "sec_" + "s" * 24
WEBHOOK_SECRET = "whs_" + "w" * 24


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Nothing in this file may see, let alone write, `results/`.

    The till caches its store handle in a module global that `monkeypatch` knows
    nothing about, so the previous value is put back afterwards — this file must
    not leave a deleted temp directory as the catalogue every later test file
    reads. Copied deliberately from `tests/test_search.py`, which learned it.
    """
    previous = upload_app._DEPS.get("store_dir")
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "catalogue"))
    monkeypatch.delenv("GAWAAH_SCAN_DIR", raising=False)
    monkeypatch.delenv("GAWAAH_CODES_FILE", raising=False)
    yield
    upload_app._DEPS["store_dir"] = previous
    upload_app._DEPS["store"] = None


class Scene:
    """The two halves of the shop, wired to each other and to nothing else."""

    def __init__(self, phone: TestClient, money: TestClient, paisa_data: Path,
                 shop_dir: Path, service) -> None:
        self.phone = phone
        self.money = money
        self.paisa_data = paisa_data
        self.shop_dir = shop_dir
        self.service = service

    def close(self) -> None:
        """Release the kernel's sqlite handle and the ledger's lock fd."""
        self.service.kernel.close()

    def place(self, **over) -> dict:
        body = {
            "items": [{"sku_id": BISCUIT[0], "qty": 2}],
            "name": "Rekha",
            "phone": "9876543210",
            "address": "12 MG Road, second floor, near the water tank",
        }
        body.update(over)
        r = self.phone.post("/store/order", json=body)
        assert r.status_code == 200, r.text
        return r.json()

    def pay(self, order_id: str):
        return self.phone.post(f"/store/order/{order_id}/pay")

    def view(self, order_id: str) -> dict:
        r = self.phone.get(f"/store/order/{order_id}")
        assert r.status_code == 200, r.text
        return r.json()


def _build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
           *, prices: dict[str, int] | None = None,
           paisa_data: Path | None = None) -> Scene:
    """A whole shop: catalogue, storefront, money service, one wire between.

    BOTH DIRECTORY VARIABLES ARE SET, AND DELIBERATELY NOT TO THE SAME TREE.
    `GAWAAH_SHOP_DIR` is `<tmp>/catalogue` and `GAWAAH_DATA_DIR` is
    `<tmp>/data`, so `store_dir().parent` is `<tmp>` and the money service's
    scan directory is `<tmp>/data/scans`. The old code wrote to the first and
    paisa read the second. Nothing is written under `results/`.
    """
    shop_dir = tmp_path / "catalogue"
    data_dir = paisa_data if paisa_data is not None else tmp_path / "data"

    upload_app.set_store_dir(shop_dir)

    for i, (sku, name, price) in enumerate((BISCUIT, SOAP)):
        upload_app.do_enrol_code_only(b"", sku, name, price,
                                      typed=f"890111122223{i}")

    # THE SIMULATOR MINTS ON A `.invalid` HOST, AND THIS TEST NEEDS A GATEWAY.
    #
    # `rzp_sim.SHORT_URL_PREFIX` used to be `https://rzp.io/i/` — Razorpay's
    # own short-link host — so every simulated link was a fabricated address on
    # the real gateway's domain. One reached a live customer order and answered
    # `404 {}` when they pressed PAY. It now mints under a reserved `.invalid`
    # host that can never resolve, and the storefront's host allowlist rightly
    # refuses to show a customer a link that is not on a gateway host.
    #
    # What THIS test is about is the storefront's plumbing — mint once, replay
    # afterwards, never mark the order paid, keep both chains verifiable — and
    # for that it needs the simulator standing in for a gateway that answers
    # normally. So the prefix is pointed back at a gateway host here, in the
    # one place where "pretend this reply came from Razorpay" is the point.
    # `tests/test_rzp_sim.py` is what holds the real default in place.
    monkeypatch.setattr(rzp_sim, "SHORT_URL_PREFIX", "https://rzp.io/i/")

    # The money service. A real one: real kernel, real ledger, real re-pricing.
    book = DictPriceBook(prices if prices is not None
                         else {BISCUIT[0]: BISCUIT[2], SOAP[0]: SOAP[2]})
    svc = build_service(
        data_dir=str(data_dir),
        clock=RealClock(),
        config=PaisaConfig(mode="sim", key_secret=KEY_SECRET,
                           webhook_secret=WEBHOOK_SECRET, seed=11),
        price_book=book,
    )
    money = TestClient(create_app(svc))

    # The one wire between the two, standing in for the HTTP hop. It carries
    # the same three fields `_post_intent` puts on the wire and returns the same
    # (status, body) pair, so the storefront cannot tell the difference.
    def wired(session_id: str, amount_paise: int, scan_id: str):
        r = money.post("/intent", json={
            "session_id": str(session_id),
            "amount_paise": int(amount_paise),
            "scan": {"scan_id": str(scan_id)},
        })
        return r.status_code, r.json()

    monkeypatch.setattr(storefront, "_post_intent", wired)

    app = FastAPI()
    app.include_router(storefront.router)
    return Scene(TestClient(app), money, Path(data_dir), shop_dir, svc)


@pytest.fixture()
def make_scene(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build one or more scenes, and close every kernel afterwards.

    A `Kernel` holds a sqlite handle and the ledger's lock fd. Leaving them for
    the garbage collector is how a suite starts failing somewhere else, on a
    machine with a lower descriptor limit, for a reason nobody can find.
    """
    built: list[Scene] = []

    def build(**kw) -> Scene:
        sc = _build(tmp_path, monkeypatch, **kw)
        built.append(sc)
        return sc

    yield build
    for sc in built:
        sc.close()


@pytest.fixture()
def scene(make_scene) -> Scene:
    return make_scene()


# ------------------------------------------------- the defect, pinned by id --


def test_the_order_witness_is_written_where_the_money_service_reads_it(
        scene: Scene, tmp_path: Path) -> None:
    """The regression. The witness must land under paisa's data directory.

    Asserted against `load_scan_witness` — paisa's OWN loader, with paisa's own
    directory rule — and not against a path this test composed, because a test
    that recomputes the writer's arithmetic can only ever agree with it.
    """
    placed = scene.place()
    doc = json.loads((scene.shop_dir / "orders"
                      / f"{placed['order_id']}.json").read_text())
    scan_id = storefront._write_witness(doc)

    assert load_scan_witness(scan_id, str(scene.paisa_data)) is not None, (
        "the money service cannot see the witness the storefront just wrote")
    assert (scene.paisa_data / "scans" / f"{scan_id}.json").exists()

    # And the place it used to go is not the place paisa looks. If these two
    # ever become the same directory this test stops proving anything, so it
    # says so out loud rather than passing quietly.
    till_dir = Path(upload_app.scans_dir())
    assert till_dir != scene.paisa_data / "scans"
    assert not (till_dir / f"{scan_id}.json").exists()


def test_a_customer_can_pay_for_an_order_placed_from_a_phone(
        scene: Scene) -> None:
    """Browse, order, look at it, pay. The journey, end to end, in one test."""
    catalogue = scene.phone.get("/store")
    assert catalogue.status_code == 200
    assert catalogue.json()["count"] == 2

    placed = scene.place()
    assert placed["total_paise"] == BISCUIT[2] * 2
    assert placed["status"] == "new"

    before = scene.view(placed["order_id"])
    assert before["paid"] is False and before["short_url"] is None

    r = scene.pay(placed["order_id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["settles_money"] is False
    assert body["amount_paise"] == BISCUIT[2] * 2
    assert body["amount_rupees"] == "20.00"
    # The gateway's own link, on a gateway host. Never a string we composed.
    assert body["short_url"].startswith("https://rzp.io/")

    # The shopkeeper sees the same order, and the same link.
    book = scene.phone.get("/orders").json()
    assert book["count"] == 1
    assert book["orders"][0]["payment"]["short_url"] == body["short_url"]


def test_pressing_pay_does_not_make_the_order_paid(scene: Scene) -> None:
    """A minted link is an invitation, not a settlement. Invariant, not polish."""
    placed = scene.place()
    assert scene.pay(placed["order_id"]).status_code == 200

    after = scene.view(placed["order_id"])
    assert after["paid"] is False
    assert after["short_url"].startswith("https://rzp.io/")
    assert after["status"] == "new"


# --------------------------------------------- invariant 5 is not weakened --


def test_the_mint_refuses_when_the_money_services_book_disagrees_by_one_paisa(
        make_scene) -> None:
    """THE TEST THE FIX EXISTS TO KEEP HONEST.

    The shop's catalogue says a packet is 1000 paise; the money service's own
    book says 999. Every other number in the journey is identical. Nothing is
    minted, the refusal is `scan_total_disagreement`, and the order is left
    unpaid with no link on it.
    """
    sc = make_scene(prices={BISCUIT[0]: BISCUIT[2] - 1, SOAP[0]: SOAP[2]})
    placed = sc.place()

    r = sc.pay(placed["order_id"])
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False
    assert body["reason"] == "scan_total_disagreement"
    assert "1998" in body["detail"] and "2000" in body["detail"]

    after = sc.view(placed["order_id"])
    assert after["paid"] is False
    assert after["short_url"] is None


def test_a_line_the_money_service_cannot_price_blocks_the_whole_mint(
        make_scene) -> None:
    """Blocked, not shrunk. The bill is never quietly short by one line.

    The shop sells both products; the money service's book has only one. The
    storefront's own re-pricing therefore PASSES — it is reading the shop's
    catalogue, where both are priced — and the refusal comes from paisa, which
    is exactly the point: the earlier check added by this fix must not shadow
    the amber guard or stand in for it.
    """
    sc = make_scene(prices={BISCUIT[0]: BISCUIT[2]})
    placed = sc.place(items=[{"sku_id": BISCUIT[0], "qty": 1},
                             {"sku_id": SOAP[0], "qty": 1}])

    r = sc.pay(placed["order_id"])
    assert r.status_code == 400
    assert r.json()["reason"] == "amber_in_basket"
    assert SOAP[0] in r.json()["detail"]
    assert sc.view(placed["order_id"])["short_url"] is None


def test_the_witness_carries_one_line_per_unit_so_each_is_priced_alone(
        scene: Scene) -> None:
    """`rerun_scan` prices a line at a time; three packets must be three lines.

    A witness that carried a quantity instead would need paisa to multiply, and
    the multiplication would be the one arithmetic step happening somewhere the
    money service could not check.
    """
    placed = scene.place(items=[{"sku_id": BISCUIT[0], "qty": 3}])
    doc = json.loads((scene.shop_dir / "orders"
                      / f"{placed['order_id']}.json").read_text())
    scan_id = storefront._write_witness(doc)

    witness = load_scan_witness(scan_id, str(scene.paisa_data))
    assert witness is not None
    assert len(witness["lines"]) == 3
    assert {ln["code"] for ln in witness["lines"]} == {f"gawaah:{BISCUIT[0]}"}


# ------------------------------------ the record says what kind of record it is --


def test_the_order_witness_does_not_claim_a_camera_saw_anything(
        scene: Scene) -> None:
    """A distinct kind of evidence, and it says so in its own fields.

    A scan witness is testimony about a photograph and carries `frame_sha256` to
    say which one. This record carries the order instead. A shopkeeper reading
    the scans directory can tell the two apart without knowing the code.
    """
    placed = scene.place()
    doc = json.loads((scene.shop_dir / "orders"
                      / f"{placed['order_id']}.json").read_text())
    scan_id = storefront._write_witness(doc)
    witness = load_scan_witness(scan_id, str(scene.paisa_data))
    assert witness is not None

    assert witness["kind"] == "order"
    assert witness["source"] == "storefront"
    assert witness["read_by"] == "storefront"
    assert witness["frame_sha256"] is None
    assert witness["order_id"] == placed["order_id"]
    assert witness["order_total_paise"] == placed["total_paise"]
    assert "no camera" in witness["evidence"]
    assert scan_id.startswith("ord")

    # It still carries the shape the money service re-prices from, because
    # there must not be a second mint path.
    assert witness["lines"] and all(
        ln["sku_id"] and ln["code"] for ln in witness["lines"])


# --------------------------------------- the shop's own book, at mint time --


def test_the_mint_refuses_when_the_shops_price_moved_after_the_order(
        scene: Scene) -> None:
    """An order is what a basket cost THEN. The shop re-derives it NOW.

    The refusal names both numbers in rupees, because the person reading it is
    a customer looking at a button that did not work.
    """
    placed = scene.place()
    upload_app.do_enrol_code_only(b"", BISCUIT[0], BISCUIT[1], 1200,
                                  typed="8901111222230")

    r = scene.pay(placed["order_id"])
    assert r.status_code == 400
    body = r.json()
    assert body["reason"] == "order_price_no_longer_agrees"
    assert "10.00" in body["detail"] and "12.00" in body["detail"]
    assert scene.view(placed["order_id"])["short_url"] is None


def test_the_mint_refuses_when_a_line_left_the_catalogue(
        scene: Scene) -> None:
    """A product the shop stopped selling has no price, so there is none to charge.

    paisa would refuse this too, as `amber_in_basket`. Refusing here first means
    the customer is told which product and what to do, rather than being handed
    a counter's vocabulary.
    """
    placed = scene.place(items=[{"sku_id": BISCUIT[0], "qty": 1},
                                {"sku_id": SOAP[0], "qty": 1}])
    assert upload_app._ao_remove(SOAP[0]) is True

    r = scene.pay(placed["order_id"])
    assert r.status_code == 400
    body = r.json()
    assert body["reason"] == "order_line_no_longer_on_sale"
    assert SOAP[1] in body["detail"]
    assert scene.view(placed["order_id"])["short_url"] is None


def test_a_hand_edited_order_total_is_not_the_number_that_gets_charged(
        scene: Scene) -> None:
    """The lines are the evidence; the stored total is a summary that can lie."""
    placed = scene.place()
    path = scene.shop_dir / "orders" / f"{placed['order_id']}.json"
    doc = json.loads(path.read_text())
    doc["total_paise"] = 100
    path.write_text(json.dumps(doc), encoding="utf-8")

    r = scene.pay(placed["order_id"])
    assert r.status_code == 400
    assert r.json()["reason"] == "order_price_no_longer_agrees"
    assert "1.00" in r.json()["detail"] and "20.00" in r.json()["detail"]


# ------------------------------------------- a misconfiguration says so now --


def test_a_witness_the_money_service_cannot_see_names_the_misconfiguration(
        make_scene, tmp_path: Path) -> None:
    """The old failure mode, if it ever comes back, is now legible.

    The money service is pointed at a DIFFERENT data directory from the one the
    storefront writes to — the two-process version of the original bug, which no
    in-process check can catch. paisa answers `scan_not_found`; the storefront
    must not repeat that, because a witness it wrote and read back a line
    earlier cannot be missing on account of anything the customer did.
    """
    sc = make_scene(paisa_data=tmp_path / "elsewhere")
    placed = sc.place()

    r = sc.pay(placed["order_id"])
    assert r.status_code == 400
    body = r.json()
    assert body["reason"] == "order_witness_not_visible_to_the_money_service"
    assert "GAWAAH_DATA_DIR" in body["detail"]
    assert "nothing about this order is wrong" in body["detail"]
    assert sc.view(placed["order_id"])["short_url"] is None


def test_an_unwritable_witness_directory_is_a_named_refusal_not_a_crash(
        scene: Scene, monkeypatch: pytest.MonkeyPatch) -> None:
    placed = scene.place()
    monkeypatch.setattr(storefront, "witness_dir",
                        lambda: Path("/dev/null/not-a-directory"))

    r = scene.pay(placed["order_id"])
    assert r.status_code == 400
    assert r.json()["reason"] == "order_witness_could_not_be_written"
    assert r.json()["ok"] is False


# ------------------------------------------------------------- the ledgers --


def test_paying_leaves_both_hash_chains_verifiable(scene: Scene) -> None:
    """Two chains — the orders' own and the money service's — and both hold.

    The storefront writes its own chain precisely so it never appends to the
    file paisa holds open. This checks that the payment path did not blur that.
    """
    placed = scene.place()
    assert scene.pay(placed["order_id"]).status_code == 200

    ok, n, _, err = verify(scene.shop_dir / "orders.audit.jsonl")
    assert ok, err
    assert n > 0

    ok, n, _, err = verify(scene.paisa_data / "audit.jsonl")
    assert ok, err
    assert n > 0


def test_a_float_in_an_order_file_refuses_rather_than_truncating(
        scene: Scene) -> None:
    """The money guard runs BEFORE the int(), which is the whole point of it.

    `paise(int(x))` reads like an assertion and is not one: `int()` truncates
    first, so `paise(int(214.507))` is 214 — Rs 2.14 charged for a Rs 214.51
    item — and `paise` never sees the float it exists to refuse. Written the
    right way round, `int(paise(x))`, the float reaches the guard.

    Every door into this module already rejects a float, so this drives the one
    remaining way a bad number gets in: a hand-edited order file on disk. It
    must refuse by name, and it must NOT quietly charge a truncated rupee.
    """
    placed = scene.place()
    path = scene.shop_dir / "orders" / f"{placed['order_id']}.json"
    doc = json.loads(path.read_text())
    doc["total_paise"] = 2000.5
    path.write_text(json.dumps(doc), encoding="utf-8")

    r = scene.pay(placed["order_id"])
    assert r.status_code == 400
    assert r.json()["ok"] is False
    # Truncation would have made this a successful mint for 2000 paise.
    assert "short_url" not in r.json()

    # And the order cannot be READ back as rupees either — named, not a
    # catch-all, and certainly not rounded into something that looks fine.
    v = scene.phone.get(f"/store/order/{placed['order_id']}")
    assert v.status_code == 400
    assert v.json()["reason"] == "order_total_is_not_integer_paise"


def test_a_float_price_in_the_catalogue_refuses_the_shelf_rather_than_rounding(
        scene: Scene, monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half of the same swap: a wrong rupee never reaches a shelf.

    `paise(int(21.45))` was 21 — the product listed at twenty-one paise instead
    of Rs 21.45, on the one screen a stranger sees. Written the right way round
    the catalogue refuses to list at all, which is the honest answer: a shop
    that cannot say what something costs must not put it out.
    """
    monkeypatch.setattr(
        upload_app, "offer_priced_skus",
        lambda: {BISCUIT[0]: {"sku_id": BISCUIT[0], "name": BISCUIT[1],
                              "price_paise": 2145.7, "how": "product_code_only"}})

    r = scene.phone.get("/store")
    assert r.status_code == 400
    assert r.json()["reason"] == "catalogue_unavailable"
    assert "not integer paise" in r.json()["detail"]


# ------------------------------------ a refused mint is written ON the order --


def _shop_order(sc: Scene, order_id: str) -> dict:
    """The order as the SHOPKEEPER's screen reads it, through the real route."""
    book = sc.phone.get("/orders").json()
    return next(o for o in book["orders"] if o["order_id"] == order_id)


def test_a_refused_mint_is_recorded_on_the_order_not_only_in_the_response(
        make_scene) -> None:
    """The silence was the bug, not the refusal.

    A refused mint used to exist only in the HTTP response to the phone. The
    order kept `minted_at: null`, which is indistinguishable from an order
    nobody ever tried to pay — so the shopkeeper's screen offered PAY AT THE
    DOOR on orders that had been refused and then delivered.
    """
    sc = make_scene(prices={BISCUIT[0]: BISCUIT[2] - 1, SOAP[0]: SOAP[2]})
    placed = sc.place()

    r = sc.pay(placed["order_id"])
    assert r.status_code == 400

    pay = _shop_order(sc, placed["order_id"])["payment"]
    assert pay["minted_at"] is None
    assert pay["short_url"] is None
    assert pay["paid"] is False

    note = pay["last_refusal"]
    # The money service's own words, verbatim — not a paraphrase of them.
    assert note["reason"] == "scan_total_disagreement"
    assert note["reason"] == r.json()["reason"]
    assert note["detail"] == r.json()["detail"]
    assert note["at"]


def test_the_recorded_refusal_names_the_product_a_shopkeeper_must_fix(
        make_scene) -> None:
    """`amber_in_basket` is only actionable if it still names the line."""
    sc = make_scene(prices={BISCUIT[0]: BISCUIT[2]})
    placed = sc.place(items=[{"sku_id": BISCUIT[0], "qty": 1},
                             {"sku_id": SOAP[0], "qty": 1}])
    assert sc.pay(placed["order_id"]).status_code == 400

    note = _shop_order(sc, placed["order_id"])["payment"]["last_refusal"]
    assert note["reason"] == "amber_in_basket"
    assert SOAP[0] in note["detail"]


def test_an_order_nobody_tried_to_pay_carries_no_refusal(scene: Scene) -> None:
    """The field's ABSENCE has to mean something, or its presence cannot."""
    placed = scene.place()
    assert "last_refusal" not in _shop_order(scene, placed["order_id"])["payment"]


def test_a_successful_mint_clears_an_earlier_refusal(scene: Scene) -> None:
    """A stale amber note beside a live payment link is the same lie, reversed.

    The order is refused first — the shop's price moved — then put back, and
    paid. The note must be gone, not merely outranked by the link beside it.
    """
    placed = scene.place()
    upload_app.do_enrol_code_only(b"", BISCUIT[0], BISCUIT[1], 1200,
                                  typed="8901111222230")
    assert scene.pay(placed["order_id"]).status_code == 400
    assert _shop_order(scene, placed["order_id"])["payment"]["last_refusal"]

    # The shopkeeper puts the price back to what the order was placed at.
    upload_app.do_enrol_code_only(b"", BISCUIT[0], BISCUIT[1], BISCUIT[2],
                                  typed="8901111222230")
    assert scene.pay(placed["order_id"]).status_code == 200

    pay = _shop_order(scene, placed["order_id"])["payment"]
    assert "last_refusal" not in pay
    assert pay["short_url"].startswith("https://rzp.io/")


def test_a_refusal_is_never_hung_on_an_order_that_is_already_paid(
        scene: Scene, monkeypatch: pytest.MonkeyPatch) -> None:
    """Money arrived. A later refusal is not a fact about this order's payment."""
    placed = scene.place()
    path = scene.shop_dir / "orders" / f"{placed['order_id']}.json"
    doc = json.loads(path.read_text())
    doc["payment"] = {"session_id": "s", "paid": True, "state": "PAID",
                      "short_url": None, "minted_at": None}
    path.write_text(json.dumps(doc), encoding="utf-8")

    r = scene.pay(placed["order_id"])
    assert r.status_code == 400
    assert r.json()["reason"] == "order_cannot_be_paid"
    assert "last_refusal" not in _shop_order(scene, placed["order_id"])["payment"]


def test_a_bad_order_id_records_nothing_because_there_is_no_order(
        scene: Scene) -> None:
    """`_record_mint_refusal` must not invent an order to hang a note on."""
    r = scene.phone.post("/store/order/ord_ffffffffffff/pay")
    assert r.status_code == 404
    assert r.json()["reason"] == "no_such_order"
    assert scene.phone.get("/orders").json()["count"] == 0


def test_recording_a_refusal_keeps_the_orders_hash_chain_verifiable(
        make_scene) -> None:
    """The note is audited, and the chain it is audited into still holds."""
    sc = make_scene(prices={BISCUIT[0]: BISCUIT[2] - 1, SOAP[0]: SOAP[2]})
    placed = sc.place()
    assert sc.pay(placed["order_id"]).status_code == 400

    ok, n, _, err = verify(sc.shop_dir / "orders.audit.jsonl")
    assert ok, err
    assert n > 0

    events = [json.loads(ln)["event"]
              for ln in (sc.shop_dir / "orders.audit.jsonl")
              .read_text().splitlines() if ln.strip()]
    assert "order.mint_refused" in events


def test_a_refused_mint_leaves_the_order_otherwise_untouched(
        make_scene) -> None:
    """The note is the ONLY change. A refusal must not move an order along."""
    sc = make_scene(prices={BISCUIT[0]: BISCUIT[2] - 1, SOAP[0]: SOAP[2]})
    placed = sc.place()
    before = _shop_order(sc, placed["order_id"])
    assert sc.pay(placed["order_id"]).status_code == 400
    after = _shop_order(sc, placed["order_id"])

    assert after["status"] == before["status"] == "new"
    assert after["total_paise"] == before["total_paise"]
    assert after["lines"] == before["lines"]
    assert after["history"] == before["history"]
    assert after["customer"] == before["customer"]


def test_the_link_is_minted_once_and_replayed_afterwards(scene: Scene) -> None:
    """One basket, one live payment link, however many times PAY is pressed."""
    placed = scene.place()
    first = scene.pay(placed["order_id"])
    assert first.status_code == 200
    second = scene.pay(placed["order_id"])
    assert second.status_code == 200

    assert second.json()["replayed"] is True
    assert second.json()["short_url"] == first.json()["short_url"]
