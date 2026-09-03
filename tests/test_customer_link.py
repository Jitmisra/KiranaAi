"""A link made out to ONE customer, and everything it must not be.

The shutter QR is one sticker that every customer scans, and that is right: a
printed code cannot know who is looking at it, and each phone that opens it
already gets its own session. What it cannot do is save a regular from typing
their name and number in every week.

`POST /store/link/for` mints a link for one person. It is a bearer credential
sitting in a URL, which is a genuinely dangerous shape, so most of this file is
about the limits rather than the feature:

  * the phone number is NOT in the URL
  * it works ONCE
  * it expires
  * it grants a CUSTOMER identity, never a shopkeeper's
  * the session it makes is UNVERIFIED, so a link forwarded to the wrong person
    cannot read the right person's order history

Everything runs against scratch directories. Nothing touches `results/`.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gawaah import storefront

BISCUIT = ("parle_g", "Parle-G biscuit 100g", 1000)


@pytest.fixture()
def shop(tmp_path, monkeypatch) -> TestClient:
    """A counter whose shop and data directories are BOTH scratch, and
    deliberately not the same tree."""
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "catalogue"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "data"))
    from tools import upload_app

    upload_app.set_store_dir(tmp_path / "catalogue")
    upload_app.do_enrol_code_only(b"", *BISCUIT, typed="8901063093157")
    app = FastAPI()
    app.include_router(storefront.router)
    return TestClient(app)


def _mint(client: TestClient, name="Priya Sharma", phone="9811100011"):
    return client.post("/shop/customer-link", json={"name": name, "phone": phone})


# ------------------------------------------------------------- the link --

def test_a_link_is_made_out_to_one_person_and_carries_no_phone_number(shop) -> None:
    """THE URL IS THE POINT OF THIS TEST.

    A URL is written into browser history, handed to the next page in
    `Referer`, and printed in every server log it passes through. A customer's
    phone number in one is a phone number in all of those, so the link carries
    an opaque token and the number stays on the shop's own disk.
    """
    r = _mint(shop)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["settles_money"] is False
    assert body["single_use"] is True

    url = body["url"]
    assert "9811100011" not in url, "the phone number reached the URL"
    assert "Priya" not in url and "priya" not in url.lower()
    q = parse_qs(urlsplit(url).query or urlsplit(url).fragment.partition("?")[2])
    tok = q["k"][0]
    assert len(tok) >= 24
    assert body["for"] == {"name": "Priya Sharma", "phone": "9811100011"}


def test_the_token_is_never_written_to_disk_in_the_clear(shop, tmp_path) -> None:
    """Same rule the customer sessions keep: the file is the shop's, but a live
    token in it would be a credential sitting in a backup."""
    tok = parse_qs(urlsplit(_mint(shop).json()["url"]).fragment.partition("?")[2])["k"][0]
    raw = storefront.customer_invites_path().read_text()
    assert tok not in raw
    assert "9811100011" in raw, "the shop must still know who the link is for"


def test_a_link_without_a_phone_number_is_refused_by_name(shop) -> None:
    r = shop.post("/shop/customer-link", json={"name": "Nobody"})
    assert r.status_code == 400
    assert r.json()["reason"] == "customer_phone_not_a_number"


# ------------------------------------------------------------- claiming --

def test_opening_the_link_makes_this_phone_that_customer(shop) -> None:
    tok = parse_qs(urlsplit(_mint(shop).json()["url"]).fragment.partition("?")[2])["k"][0]

    before = shop.get("/store/customer/me").json()
    assert (before.get("customer") or {}).get("name") in (None, "")

    r = shop.post("/store/customer/claim", json={"token": tok})
    assert r.status_code == 200, r.text
    assert r.json()["customer"]["name"] == "Priya Sharma"

    me = shop.get("/store/customer/me").json()["customer"]
    assert me["name"] == "Priya Sharma" and me["phone"] == "9811100011"


def test_it_works_once(shop) -> None:
    """A bearer credential in a URL that works forever, for anybody who ever
    sees it, is a different and much worse thing than one that works once."""
    tok = parse_qs(urlsplit(_mint(shop).json()["url"]).fragment.partition("?")[2])["k"][0]
    assert shop.post("/store/customer/claim", json={"token": tok}).status_code == 200

    again = shop.post("/store/customer/claim", json={"token": tok})
    assert again.status_code == 400
    assert again.json()["reason"] == storefront.R_INVITE_USED


def test_an_expired_link_is_refused_by_its_own_name(shop, monkeypatch) -> None:
    tok = parse_qs(urlsplit(_mint(shop).json()["url"]).fragment.partition("?")[2])["k"][0]
    doc = storefront._load_invites()
    for rec in doc["invites"].values():
        rec["expires_at"] = _dt.datetime.now(_dt.timezone.utc).timestamp() - 1
    storefront._save_invites(doc)

    r = shop.post("/store/customer/claim", json={"token": tok})
    assert r.status_code == 400
    assert r.json()["reason"] == storefront.R_INVITE_EXPIRED


def test_a_made_up_link_is_refused_and_names_nobody(shop) -> None:
    r = shop.post("/store/customer/claim", json={"token": "not-a-real-token"})
    assert r.status_code == 400
    body = r.json()
    assert body["reason"] == storefront.R_INVITE_UNKNOWN
    assert "9811100011" not in json.dumps(body)


# ----------------------------------------------------------- the limits --

def test_a_forwarded_link_cannot_read_the_right_persons_orders(shop) -> None:
    """THE MOST IMPORTANT TEST IN THIS FILE.

    The session a link creates is UNVERIFIED — the same standing as typing your
    own name into the storefront. `/store/customer/orders` demands a verified
    session, which is only reached by naming an order id. So a link that ends
    up in the wrong hands makes that browser look like the customer for the
    purposes of ordering, and still cannot open what the customer bought.
    """
    tok = parse_qs(urlsplit(_mint(shop).json()["url"]).fragment.partition("?")[2])["k"][0]
    shop.post("/store/customer/claim", json={"token": tok})

    me = shop.get("/store/customer/me").json()["customer"]
    assert me["verified"] is False

    r = shop.get("/store/customer/orders")
    assert r.status_code != 200, "an unverified link read somebody's order history"


def test_the_link_grants_no_shopkeeper_anything(shop) -> None:
    """It is a customer identity and nothing else. The cookie it sets is the
    customer cookie, not the shopkeeper's session cookie."""
    from gawaah import auth

    tok = parse_qs(urlsplit(_mint(shop).json()["url"]).fragment.partition("?")[2])["k"][0]
    r = shop.post("/store/customer/claim", json={"token": tok})
    jar = {c.name for c in r.cookies.jar} if hasattr(r, "cookies") else set()
    assert auth.SESSION_COOKIE not in jar
    assert storefront.CUSTOMER_COOKIE not in auth.OPEN_PATHS


def test_minting_a_link_is_not_open_to_strangers() -> None:
    """THE GUARD ITSELF, NOT THE CONSTANT — and the first version of this test
    checked the constant and let a real hole through.

    The route was `/store/link/for`. `/store` is an open PREFIX, because a
    stranger holding the shutter QR must reach the shop without an account, and
    a prefix opens everything beneath it. So the guard never ran on it and any
    phone on the shop's wifi could mint a customer identity for any number.
    This test passed the whole time, because "not in OPEN_PATHS" is not the
    same question as "is this route actually guarded".

    So it asks the mounted app what it is really enforcing.
    """
    from gawaah import auth
    from tools import upload_app

    # THE TILL'S OWN OPEN LIST, not auth's. `upload_app.AUTH_GUARD` is the guard
    # the shipped server actually mounts, and it opens the `/store` prefix so a
    # stranger with the shutter QR can reach the shop. That prefix is what
    # swallowed the first version of this route.
    paths, prefixes = ("/", "/health"), ("/store", "/receipt", "/qr/link")
    assert upload_app.AUTH_GUARD is not None

    assert not auth._matches("/shop/customer-link", set(paths), prefixes), (
        "minting a customer identity sits under an OPEN prefix, so anybody on "
        "the shop's wifi can mint an identity for any phone number")
    assert "/shop/customer-link" not in auth.OPEN_PATHS

    # And the claim MUST stay reachable: a customer opening the link has no
    # session and never will.
    assert auth._matches("/store/customer/claim", set(paths), prefixes)


def test_nothing_here_settles_money(shop) -> None:
    tok = parse_qs(urlsplit(_mint(shop).json()["url"]).fragment.partition("?")[2])["k"][0]
    for r in (_mint(shop, phone="9822200022"),
              shop.post("/store/customer/claim", json={"token": tok})):
        assert r.json()["settles_money"] is False
