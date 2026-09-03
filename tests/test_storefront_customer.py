"""The CUSTOMER's side of the shop: a live PAY button, an identity, and a rule.

Three defects, all of them things a demo hides and a customer finds.

  1. THE PAY BUTTON WENT NOWHERE. Order ord_eabcde66be86 (4x derma, Rs 1,600.00)
     showed a green PAY button pointing at a short link on the gateway's own
     host. Fetching it returned HTTP 404, `application/json`, two bytes: `{}`.

     The link had never existed. `GET /v1/payment_links/plink_kqD9HyAzA1nf4R`
     against the key the shop is configured with answers "The id provided does
     not exist", and every payment link that key HAS issued lives under a
     different path prefix on that host. It had been minted by
     `gawaah/rzp_sim.py`, which composes short links under a hard-coded prefix on
     the gateway's REAL domain. So the shop showed a customer a payment address
     that the gateway never issued — which is invariant 1 reached by accident
     rather than by anyone writing a `upi://` string.

     `_checked_link` could not catch it: it checks scheme, hostname and a host
     allowlist, and a fabricated link on the right host passes all three. Shape
     is what a forgery gets right. The tests below pin the check that does work,
     which is asking the gateway whether it serves the code.

  2. A CUSTOMER HAD NO IDENTITY. The storefront rendered inside the shopkeeper's
     chrome and there was no such thing as being signed in as a customer.

  3. A SHOPKEEPER COULD ORDER FROM THEIR OWN SHOP, which writes a real order, a
     real payment link and a real line in the books for a sale that never
     happened.

NOTHING HERE TOUCHES THE NETWORK. `_gateway_serves` is the seam and every test
replaces it, so these tests state what the shop does with each of the gateway's
three possible answers rather than depending on the gateway to give one.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import auth, storefront  # noqa: E402
from gawaah.storefront import (  # noqa: E402
    R_LINK_IS_ALIVE,
    R_NOT_PAYABLE,
    R_NOT_SIGNED_IN,
    R_SHOPKEEPER_PREVIEW,
    R_UNPROVEN_NUMBER,
)

# `from tools import upload_app`, not `import upload_app` — the two spellings
# register the till twice and the storefront then reads a different catalogue
# from the one the test filled. Written up in tools/upload_app.py's docstring.
from tools import upload_app  # noqa: E402

BISCUIT = ("parle_g_200g", "Parle-G 200g", 2145)
SOAP = ("lifebuoy_125g", "Lifebuoy 125g", 3950)

#: What the simulator actually produced for the order in the defect report: the
#: right host, the right shape, and a code the gateway has never heard of. Used
#: verbatim so these tests fail if the module ever starts believing it again.
FORGED = "https://rzp.io/i/BjQNyPd"

#: The shape of a link the configured key really did issue.
REAL = "https://rzp.io/rzp/ykXAkfX"

OWNER_PHONE = "9876500000"
OWNER_PASS = "a-good-long-passphrase"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """BOTH directory variables, and the till's cached handle put back after.

    `results/` must not be readable, let alone writable, from this file. The
    till caches its store handle in a module global that monkeypatch cannot see,
    so the previous value is restored by hand — otherwise this file leaves a
    deleted temp directory as the catalogue every later test reads.
    """
    previous = upload_app._DEPS.get("store_dir")
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("GAWAAH_SCAN_DIR", raising=False)
    monkeypatch.delenv("GAWAAH_CODES_FILE", raising=False)
    monkeypatch.delenv("GAWAAH_REQUIRE_AUTH", raising=False)
    auth.reset_rate_limit()
    # The link verdict cache is process-wide and keyed on the URL, and every
    # test here reuses the same two URLs. Left alone, the FIRST test to classify
    # `REAL` decides it for all the others and half this file stops testing
    # anything. Cleared both sides of the test so neither direction leaks.
    storefront._LINK_VERDICTS.clear()
    yield
    storefront._LINK_VERDICTS.clear()
    upload_app._DEPS["store_dir"] = previous
    upload_app._DEPS["store"] = None


@pytest.fixture()
def shop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A whole shop front, with auth mounted so a shopkeeper can sign in."""
    upload_app.set_store_dir(tmp_path / "shop")
    for i, (sku, name, price) in enumerate((BISCUIT, SOAP)):
        upload_app.do_enrol_code_only(b"", sku, name, price,
                                      typed=f"890123456789{i}")

    # The gateway is never called from a test. Default: every link is live, so a
    # test that does not care about link health gets the ordinary happy path.
    monkeypatch.setattr(storefront, "_gateway_serves", lambda url: True)

    app = FastAPI()
    app.include_router(storefront.router)
    app.include_router(auth.router)
    return TestClient(app)


def _place(client: TestClient, **over) -> dict:
    body = {
        "items": [{"sku_id": BISCUIT[0], "qty": 2}],
        "name": "Rekha",
        "phone": "9876543210",
        "address": "12 MG Road, second floor, near the water tank",
    }
    body.update(over)
    r = client.post("/store/order", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _mint(client: TestClient, monkeypatch: pytest.MonkeyPatch, order_id: str,
          short_url: str = REAL) -> dict:
    """Give an order a payment link, standing in for the money service.

    The wire, not the gateway: `_post_intent` is the one function that talks to
    paisa, and replacing it here keeps the mint path — repricing, witness,
    refusals — exactly as it ships.
    """
    monkeypatch.setattr(
        storefront, "_post_intent",
        lambda s, a, k: (200, {"short_url": short_url, "state": "CALLING",
                               "payment_link_id": "plink_test000000000"}))
    r = client.post(f"/store/order/{order_id}/pay")
    assert r.status_code == 200, r.text
    return r.json()


def _signed_in_shopkeeper(client: TestClient) -> None:
    """Create and sign in the counter's first account, on this client."""
    r = client.post("/auth/signup", json={
        "name": "Vikram", "phone": OWNER_PHONE, "password": OWNER_PASS})
    assert r.status_code == 200, r.text


# ------------------------------------------- defect 1: the dead pay button --


def test_a_link_the_gateway_denies_is_not_offered_as_payable(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """THE DEFECT ITSELF, with the string that actually shipped.

    The gateway answers 404 for this code, so the shop must not present it as
    something to press. It is still RETURNED — the caller is told which string
    was refused rather than handed a null to guess about — but `payable` is
    false and there is a sentence a customer can read.
    """
    order = _place(shop)
    monkeypatch.setattr(storefront, "_gateway_serves", lambda url: False)
    body = _mint(shop, monkeypatch, order["order_id"], short_url=FORGED)

    assert body["short_url"] == FORGED
    assert body["payable"] is False
    assert body["link_state"] == "dead"
    assert body["can_relink"] is True
    # Plain words, and about this order.
    assert order["order_id"] in body["note"]
    assert "no longer there" in body["note"]
    assert "nothing has been charged" in body["note"].lower()


def test_the_order_screen_repeats_that_verdict(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The tracking page is where the customer actually is when they press PAY."""
    order = _place(shop)
    monkeypatch.setattr(storefront, "_gateway_serves", lambda url: False)
    _mint(shop, monkeypatch, order["order_id"], short_url=FORGED)

    view = shop.get(f"/store/order/{order['order_id']}").json()
    assert view["payable"] is False
    assert view["link_state"] == "dead"
    assert view["can_relink"] is True
    assert "pay the delivery person at the door" in view["payment_note"]


def test_a_link_the_gateway_serves_is_payable_and_is_not_minted_twice(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The happy path stays the happy path, and PAY twice is still one link."""
    order = _place(shop)
    first = _mint(shop, monkeypatch, order["order_id"])
    assert first["payable"] is True
    assert first["link_state"] == "live"

    second = shop.post(f"/store/order/{order['order_id']}/pay").json()
    assert second["short_url"] == first["short_url"]
    assert second["replayed"] is True
    assert second["payable"] is True


def test_a_gateway_that_cannot_be_reached_does_not_condemn_the_link(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """UNKNOWN IS NOT DEAD, and this is the asymmetry that matters.

    A shop's wifi dropping is not evidence against a payment link. Calling a
    working link dead refuses money the customer wanted to pay; showing a link
    that turns out to be stale costs them one tap. The failure is deliberately
    pointed at the cheaper mistake.
    """
    order = _place(shop)
    monkeypatch.setattr(storefront, "_gateway_serves", lambda url: None)
    body = _mint(shop, monkeypatch, order["order_id"])

    assert body["link_state"] == "unknown"
    assert body["payable"] is True
    assert "can_relink" not in body


def test_an_expired_but_real_link_is_still_shown(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Measured against the real gateway: an expired link still resolves.

    It serves a page that explains itself in the gateway's own words, which is a
    far better screen than this shop refusing and saying nothing. So `serves` is
    the question, and `still payable` is the gateway's business to answer.
    """
    order = _place(shop)
    monkeypatch.setattr(storefront, "_gateway_serves", lambda url: True)
    body = _mint(shop, monkeypatch, order["order_id"])
    assert body["payable"] is True


def test_the_verdict_is_cached_so_a_waiting_phone_does_not_hammer_the_gateway(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The order screen polls every few seconds. A live link is asked about once."""
    order = _place(shop)
    calls: list[str] = []
    monkeypatch.setattr(storefront, "_gateway_serves",
                        lambda url: calls.append(url) or True)
    _mint(shop, monkeypatch, order["order_id"])
    assert len(calls) == 1
    for _ in range(5):
        shop.get(f"/store/order/{order['order_id']}")
    assert len(calls) == 1, f"asked the gateway {len(calls)} times for one link"


# --------------------------------------------- defect 1: minting a new one --


def test_looking_at_an_order_never_writes_to_it(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A READ MUST NOT WRITE, and the first version of this code got it wrong.

    The link verdict was originally cached onto the order document, which made
    GET /store/order/{id} a route that modifies an order file. The customer's
    tracking screen polls that route every four seconds, so an idle page rewrote
    the order on disk — and running the suite against a real shop mutated live
    order documents just by reading them. The cache is in memory now, and this
    is the test that says so.
    """
    order = _place(shop)
    oid = order["order_id"]
    monkeypatch.setattr(storefront, "_gateway_serves", lambda url: False)
    _mint(shop, monkeypatch, oid, short_url=FORGED)

    path = storefront.orders_dir() / f"{oid}.json"
    before = path.read_bytes()
    for _ in range(4):
        assert shop.get(f"/store/order/{oid}").json()["payable"] is False
    assert path.read_bytes() == before, "reading the order rewrote it"


def test_a_dead_link_can_be_replaced_and_the_new_one_is_a_different_link(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The offer the customer is given when their link is denied.

    A NEW SESSION ID is the point. paisa keys its intents on the session id and
    passes it to the gateway as `reference_id`, so re-minting under the same id
    would replay the same dead link and this route would do nothing.
    """
    order = _place(shop)
    oid = order["order_id"]
    monkeypatch.setattr(storefront, "_gateway_serves", lambda url: False)
    first = _mint(shop, monkeypatch, oid, short_url=FORGED)
    assert first["payable"] is False

    monkeypatch.setattr(storefront, "_gateway_serves", lambda url: True)
    monkeypatch.setattr(
        storefront, "_post_intent",
        lambda s, a, k: (200, {"short_url": REAL, "state": "CALLING",
                               "payment_link_id": "plink_test111111111"}))
    r = shop.post(f"/store/order/{oid}/relink")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["short_url"] == REAL
    assert body["payable"] is True
    assert body["session_id"] != first["session_id"]

    stored = json.loads(
        (storefront.orders_dir() / f"{oid}.json").read_text("utf-8"))
    retired = stored["payment"]["superseded"]
    assert len(retired) == 1
    assert retired[0]["short_url"] == FORGED
    assert "does not serve" in retired[0]["why"]


def test_a_live_link_is_never_replaced(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """TWO LIVE LINKS ON ONE ORDER IS HOW A CUSTOMER PAYS TWICE.

    Worse than the dead link this route exists to clear, so the precondition is
    checked server-side and the refusal is named.
    """
    order = _place(shop)
    oid = order["order_id"]
    _mint(shop, monkeypatch, oid)

    r = shop.post(f"/store/order/{oid}/relink")
    assert r.status_code == 400
    assert r.json()["reason"] == R_LINK_IS_ALIVE
    assert "charged twice" in r.json()["detail"]

    view = shop.get(f"/store/order/{oid}").json()
    assert view["short_url"] == REAL


def test_a_link_that_cannot_be_checked_is_never_replaced_either(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """`unknown` refuses exactly like `live` does — same reason, same direction.

    A shop that could not reach the gateway must not conclude the link is dead
    and mint a second one beside a link that may be perfectly live.
    """
    order = _place(shop)
    oid = order["order_id"]
    _mint(shop, monkeypatch, oid)
    monkeypatch.setattr(storefront, "_gateway_serves", lambda url: None)
    # Drop the cached `live` so the unknown answer is the one that counts.
    storefront._LINK_VERDICTS.clear()

    r = shop.post(f"/store/order/{oid}/relink")
    assert r.status_code == 400
    assert r.json()["reason"] == R_LINK_IS_ALIVE
    assert "could not get an answer" in r.json()["detail"]


def test_a_paid_order_is_never_relinked(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    order = _place(shop)
    oid = order["order_id"]
    _mint(shop, monkeypatch, oid)
    path = storefront.orders_dir() / f"{oid}.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["payment"]["paid"] = True
    path.write_text(json.dumps(doc), "utf-8")

    r = shop.post(f"/store/order/{oid}/relink")
    assert r.status_code == 400
    assert r.json()["reason"] == R_NOT_PAYABLE


def test_relinking_an_order_with_no_link_says_to_press_pay(
        shop: TestClient) -> None:
    order = _place(shop)
    r = shop.post(f"/store/order/{order['order_id']}/relink")
    assert r.status_code == 400
    assert r.json()["reason"] == R_NOT_PAYABLE
    assert "Press PAY" in r.json()["detail"]


def test_no_route_here_can_mark_an_order_paid(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """INVARIANT 2. Nothing a customer can press turns anything green."""
    order = _place(shop)
    oid = order["order_id"]
    monkeypatch.setattr(storefront, "_gateway_serves", lambda url: False)
    _mint(shop, monkeypatch, oid, short_url=FORGED)
    monkeypatch.setattr(storefront, "_gateway_serves", lambda url: True)
    monkeypatch.setattr(
        storefront, "_post_intent",
        lambda s, a, k: (200, {"short_url": REAL, "state": "CALLING"}))
    shop.post(f"/store/order/{oid}/relink")

    view = shop.get(f"/store/order/{oid}").json()
    assert view["paid"] is False
    assert view["settles_money"] is False
    assert shop.post(f"/store/order/{oid}/pay").json()["settles_money"] is False
    # And nothing wrote a settlement onto the order behind the screen's back.
    stored = json.loads(
        (storefront.orders_dir() / f"{oid}.json").read_text("utf-8"))
    assert stored["payment"].get("paid") is False
    assert "paid_at" not in stored["payment"]


# ------------------------------------------- defect 2: a customer identity --


def test_a_customer_can_identify_themselves_without_a_password(
        shop: TestClient) -> None:
    """The honest identity for a kirana shop: a name and a number, no password.

    See the block comment in storefront.py for why this is not a role in
    `gawaah/auth.py`.
    """
    r = shop.post("/store/customer/signin",
                  json={"name": "Rekha", "phone": "9876543210"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["customer"]["phone"] == "9876543210"
    assert body["customer"]["verified"] is False

    me = shop.get("/store/customer/me").json()
    assert me["signed_in"] is True
    assert me["customer"]["name"] == "Rekha"


def test_an_unproved_number_cannot_read_that_numbers_orders(
        shop: TestClient) -> None:
    """THE WHOLE REASON `verified` EXISTS. A phone number is not a secret.

    Typing one proves nothing, so a session that has only been told a number is
    refused the order history for it. Without this gate, a box on a page reads
    back a stranger's every order, total and delivery address status.
    """
    _place(shop)  # Rekha has an order on 9876543210
    shop.post("/store/customer/signin",
              json={"name": "Somebody Else", "phone": "9876543210"})

    r = shop.get("/store/customer/orders")
    assert r.status_code == 400
    assert r.json()["reason"] == R_UNPROVEN_NUMBER
    assert "not a secret" in r.json()["detail"]


def test_an_order_id_proves_the_number_and_opens_the_history(
        shop: TestClient) -> None:
    """The order id is the token. 48 bits from `secrets.token_hex(6)`."""
    first = _place(shop)
    second = _place(shop, items=[{"sku_id": SOAP[0], "qty": 1}])

    r = shop.post("/store/customer/signin",
                  json={"name": "Rekha", "phone": "9876543210",
                        "order_id": first["order_id"]})
    assert r.status_code == 200, r.text
    assert r.json()["customer"]["verified"] is True

    orders = shop.get("/store/customer/orders").json()
    assert orders["count"] == 2
    ids = {o["order_id"] for o in orders["orders"]}
    assert ids == {first["order_id"], second["order_id"]}


def test_another_persons_order_id_is_refused_and_not_quietly_downgraded(
        shop: TestClient) -> None:
    """Somebody who typed an order id meant to prove something.

    Handing them an unverified session and an empty list would read as "you have
    never ordered here", which is a different claim and a false one.
    """
    rekha = _place(shop)
    r = shop.post("/store/customer/signin",
                  json={"name": "Imran", "phone": "9000000001",
                        "order_id": rekha["order_id"]})
    assert r.status_code == 400
    assert r.json()["reason"] == R_UNPROVEN_NUMBER
    assert shop.get("/store/customer/me").json()["signed_in"] is False


def test_a_verified_customer_sees_only_their_own_orders(
        shop: TestClient) -> None:
    mine = _place(shop)
    _place(shop, name="Imran", phone="9000000001",
           address="4 Station Road, behind the temple")

    shop.post("/store/customer/signin",
              json={"name": "Rekha", "phone": "9876543210",
                    "order_id": mine["order_id"]})
    orders = shop.get("/store/customer/orders").json()
    assert orders["count"] == 1
    assert orders["orders"][0]["order_id"] == mine["order_id"]


def test_a_customer_order_row_carries_no_address_or_phone(
        shop: TestClient) -> None:
    """The same rule `_customer_view` already keeps, kept on this list too."""
    mine = _place(shop)
    shop.post("/store/customer/signin",
              json={"name": "Rekha", "phone": "9876543210",
                    "order_id": mine["order_id"]})
    row = shop.get("/store/customer/orders").json()["orders"][0]
    blob = json.dumps(row)
    assert "MG Road" not in blob
    assert "9876543210" not in blob


def test_signing_out_drops_the_stored_session_not_only_the_cookie(
        shop: TestClient) -> None:
    """Clearing the cookie alone leaves a live credential on disk."""
    mine = _place(shop)
    shop.post("/store/customer/signin",
              json={"name": "Rekha", "phone": "9876543210",
                    "order_id": mine["order_id"]})
    stored = json.loads(storefront.customer_sessions_path().read_text("utf-8"))
    assert len(stored["sessions"]) == 1

    assert shop.post("/store/customer/signout").json()["signed_out"] is True
    stored = json.loads(storefront.customer_sessions_path().read_text("utf-8"))
    assert stored["sessions"] == {}
    assert shop.get("/store/customer/me").json()["signed_in"] is False


def test_the_token_itself_is_never_written_to_disk(shop: TestClient) -> None:
    """A token in the shop's own file is a live credential sitting in a backup."""
    r = shop.post("/store/customer/signin",
                  json={"name": "Rekha", "phone": "9876543210"})
    token = r.cookies.get(storefront.CUSTOMER_COOKIE)
    assert token
    assert token not in storefront.customer_sessions_path().read_text("utf-8")


def test_a_customer_session_is_not_a_shopkeeper_session(
        shop: TestClient) -> None:
    """TWO STORES THAT CANNOT LEAK INTO EACH OTHER.

    The reason customers are not a role in `gawaah/auth.py`: a customer session
    minted here can never satisfy the guard that opens the till and the books.
    """
    shop.post("/store/customer/signin",
              json={"name": "Rekha", "phone": "9876543210"})
    assert shop.get("/store/customer/me").json()["signed_in"] is True
    # auth does not know this browser at all.
    assert shop.get("/auth/me").json().get("account") is None
    assert shop.get("/store/customer/me").json()["previewing"] is False


def test_a_phone_number_too_short_to_dial_is_refused_on_both_doors(
        shop: TestClient) -> None:
    """One rule, named once. A number that cannot order cannot sign in either."""
    r = shop.post("/store/customer/signin",
                  json={"name": "Rekha", "phone": "12345"})
    assert r.status_code == 400
    assert r.json()["reason"] == "customer_phone_not_a_number"


# ---------------------------- defect 3: the shopkeeper is not a customer --


def test_a_signed_in_shopkeeper_cannot_order_from_their_own_storefront(
        shop: TestClient) -> None:
    """REFUSED ON THE SERVER, because hiding a button is not a rule.

    Ordering from yourself writes a real order file, mints a real payment link
    against the shop's own gateway account and puts a line in the books for a
    sale that never happened.
    """
    _signed_in_shopkeeper(shop)
    r = shop.post("/store/order", json={
        "items": [{"sku_id": BISCUIT[0], "qty": 2}],
        "name": "Vikram", "phone": OWNER_PHONE,
        "address": "the shop itself, behind the counter"})
    assert r.status_code == 400
    assert r.json()["reason"] == R_SHOPKEEPER_PREVIEW
    detail = r.json()["detail"]
    assert "preview" in detail
    assert "Vikram" in detail
    assert "private window" in detail


def test_the_refused_preview_writes_no_order_at_all(shop: TestClient) -> None:
    """Not a cancelled order, not an order marked preview. Nothing."""
    _signed_in_shopkeeper(shop)
    shop.post("/store/order", json={
        "items": [{"sku_id": BISCUIT[0], "qty": 2}],
        "name": "Vikram", "phone": OWNER_PHONE,
        "address": "the shop itself, behind the counter"})
    assert shop.get("/orders").json()["count"] == 0
    assert list(storefront.orders_dir().glob("ord_*.json")) == []


def test_the_refusal_lands_before_the_basket_is_even_priced(
        shop: TestClient) -> None:
    """Checked first, so a preview is refused as a preview and not as a typo.

    A shopkeeper poking at their own shop front with an empty basket should be
    told they are previewing, not told their cart is empty — the second answer
    invites them to fix it and try again.
    """
    _signed_in_shopkeeper(shop)
    r = shop.post("/store/order", json={"items": [], "name": "", "phone": "",
                                        "address": ""})
    assert r.status_code == 400
    assert r.json()["reason"] == R_SHOPKEEPER_PREVIEW


def test_a_customer_on_the_same_shop_can_still_order(shop: TestClient) -> None:
    """The rule must not close the shop. A stranger's phone carries no cookie."""
    _signed_in_shopkeeper(shop)
    stranger = TestClient(shop.app)
    r = stranger.post("/store/order", json={
        "items": [{"sku_id": BISCUIT[0], "qty": 2}],
        "name": "Rekha", "phone": "9876543210",
        "address": "12 MG Road, second floor, near the water tank"})
    assert r.status_code == 200, r.text


def test_the_storefront_tells_the_page_it_is_a_preview(
        shop: TestClient) -> None:
    """So the page can say so BEFORE a delivery address is typed.

    Finding out at the end, after filling in a form, is the version of this that
    reads as a bug.
    """
    _signed_in_shopkeeper(shop)
    me = shop.get("/store/customer/me").json()
    assert me["previewing"] is True
    assert me["shopkeeper_name"] == "Vikram"


def test_a_shopkeeper_can_still_look_at_an_order(shop: TestClient) -> None:
    """Refusing to ORDER is not refusing to LOOK. The preview must still work."""
    stranger = TestClient(shop.app)
    placed = _place(stranger)
    _signed_in_shopkeeper(shop)
    assert shop.get("/store").json()["count"] == 2
    assert shop.get(f"/store/order/{placed['order_id']}").status_code == 200


# ------------------------------------------------------------ house rules --


def test_no_route_added_here_can_return_a_500(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A REFUSAL IS A RESULT. Every shape, and none of them crash."""
    bad_bodies = [None, [], "text", {"name": 5}, {"phone": {}},
                  {"name": "x", "phone": "9876543210", "order_id": 7}]
    for body in bad_bodies:
        r = shop.post("/store/customer/signin", json=body)
        assert r.status_code < 500, f"{body!r} -> {r.status_code} {r.text}"
    for path in ("/store/order/not-an-id/relink",
                 "/store/order/ord_000000000000/relink"):
        assert shop.post(path).status_code < 500


# ------------------------------ the shape check on the READ path (regression) --
#
# The liveness check above was added to `_link_health` without the SHAPE check
# beside it, and `_checked_link` ran only in the /pay response. That left GET
# /store/order/{id} — the route the customer's screen renders its PAY button
# from and polls every four seconds — with no shape check at all. Measured before
# the fix: an order holding `http://127.0.0.1:8788/health` came back
# `link_state: live, payable: true`.


def _order_holding(shop: TestClient, url: str) -> str:
    """Put a link straight onto an order, past the mint that would refuse it.

    The mint calls `_checked_link`, so a string of the wrong shape cannot get
    onto an order through the front door. It CAN be on one already — written by
    an older build, or by anything else with the shop directory — which is
    exactly the case the read path has to survive.
    """
    order = _place(shop)
    path = storefront.orders_dir() / f"{order['order_id']}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["payment"].update({"short_url": url, "state": "CALLING",
                           "minted_at": "2026-09-03T00:00:00+00:00",
                           "payment_link_id": "plink_test000000000"})
    path.write_text(json.dumps(doc, sort_keys=True, indent=1) + "\n",
                    encoding="utf-8")
    return str(order["order_id"])


def test_a_link_that_is_not_on_the_gateway_is_never_shown_as_payable(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The read path refuses it, and says WHO refused.

    `refused` and not `dead`: the gateway was never asked, so the customer must
    not be told the gateway denied anything.
    """
    oid = _order_holding(shop, "http://127.0.0.1:8788/health")
    view = shop.get(f"/store/order/{oid}").json()

    assert view["link_state"] == "refused"
    assert view["payable"] is False
    assert view["can_relink"] is True
    # The words name the real reason and do not put them in the gateway's mouth.
    assert "does not point at the payment gateway" in view["payment_note"]
    assert "gateway does not recognise" not in view["payment_note"]


def test_the_gateway_is_never_asked_about_a_link_of_the_wrong_shape(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """SHAPE BEFORE NETWORK. The probe opens whatever it is handed, so nothing
    may be handed to it until something has checked where it points."""
    asked: list[str] = []
    monkeypatch.setattr(storefront, "_gateway_serves",
                        lambda url: asked.append(url) or True)

    oid = _order_holding(shop, "http://169.254.169.254/latest/meta-data/")
    assert shop.get(f"/store/order/{oid}").json()["payable"] is False
    assert asked == [], f"the server fetched an unvalidated URL: {asked!r}"


def test_a_link_of_the_wrong_shape_can_be_replaced(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """`refused` earns the same offer as `dead`, and it is the safer of the two.

    A link this shop will not show has never been offered to anyone as payable,
    so there is no live link to mint beside — the double-charge this route
    guards against cannot arise from it.
    """
    oid = _order_holding(shop, "http://127.0.0.1:8788/health")
    monkeypatch.setattr(
        storefront, "_post_intent",
        lambda s, a, k: (200, {"short_url": REAL, "state": "CALLING",
                               "payment_link_id": "plink_test000000001"}))
    r = shop.post(f"/store/order/{oid}/relink")

    assert r.status_code == 200, r.text
    assert r.json()["short_url"] == REAL
    assert r.json()["payable"] is True
    doc = json.loads(
        (storefront.orders_dir() / f"{oid}.json").read_text(encoding="utf-8"))
    retired = doc["payment"]["superseded"][0]
    assert retired["short_url"] == "http://127.0.0.1:8788/health"
    # The trail records who said no, and it was not the gateway.
    assert retired["why"] == "this link does not point at the payment gateway"


def test_an_unreachable_gateway_still_leaves_a_real_link_payable(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The shape gate must not have turned `unknown` into a refusal.

    A well-shaped link the shop could not reach stays payable — the asymmetry
    the module is built around, re-checked from the other side of the new gate.
    """
    order = _place(shop)
    monkeypatch.setattr(storefront, "_gateway_serves", lambda url: None)
    body = _mint(shop, monkeypatch, order["order_id"], short_url=REAL)

    assert body["link_state"] == "unknown"
    assert body["payable"] is True
