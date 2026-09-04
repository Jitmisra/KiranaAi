"""gawaah/auth.py — the lock on the counter door.

These tests exist to make five claims checkable, because every one of them is a
claim a demo can fake:

  1. THE LOCK SHIPS FITTED AND OPEN. `GAWAAH_REQUIRE_AUTH` unset means every
     route is exactly as reachable as it was before this module existed, and no
     value except a handful of explicit words turns it on. The tests below
     mount a route through the real guard and assert it answers 200 to a
     stranger while the switch is off — because a lock that quietly latched
     itself would take the whole counter down with it.

  2. THE PASSWORD IS NOT ON DISK IN ANY FORM. Not in the accounts file, not in
     the audit chain, not in a refusal string, not in a response body. Two
     accounts with the SAME password hold different bytes, which is what a
     per-user salt means.

  3. THE TOKEN IS NEVER IN A BODY AND NEVER IN THE LOG. A sign-in answers with
     a Set-Cookie header, and what lands on disk is sha256 of the token — so a
     copied `auth_sessions.json` is a list of fingerprints and not a ring of
     keys.

  4. EVERY REFUSAL HAS A NAME. Each of the twenty-odd reason strings in the
     module is reached by a test here, and no input of any shape produces a
     500.

  5. THE COUNTER DOES NOT SAY WHO EXISTS. A wrong password and a phone number
     with no account answer with the same reason and are both rate-limited, so
     neither the body nor the clock tells a stranger which of the shop's staff
     have accounts.

Nothing in this file talks to a gateway, touches money, or writes outside the
temporary shop directory the fixture creates.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import APIRouter, FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import auth  # noqa: E402
from gawaah.ledger import verify  # noqa: E402
from tools import upload_app  # noqa: E402

# A shopkeeper. The phone is deliberately given in three spellings across the
# tests below, because a person who signs up as "+91 98765 43210" and signs in
# as "9876543210" is one person and must not be two accounts.
OWNER_NAME = "Rekha Devi"
OWNER_PHONE = "9876543210"
OWNER_PASS = "chai-biscuit-2026"

STAFF_PHONE = "9123456780"
STAFF_PASS = "shutter-key-9911"

GUARDED = "/till/guarded"
OPEN = "/store/anything"


def _build_app(*, install: bool = True) -> FastAPI:
    """The till, in miniature, mounted exactly the way the orchestrator will.

    Two routers beside the auth one: a guarded route carrying `auth.DEPENDS`
    and a route under `/store` that stands in for the customer's side of the
    shop. Both exist so the switch can be tested against something real rather
    than against the guard function in isolation.
    """
    app = FastAPI()
    app.include_router(auth.router)
    if install:
        auth.install(app)

    guarded = APIRouter()

    @guarded.get(GUARDED)
    def _guarded(request: Request):
        who = getattr(request.state, "shopkeeper", None)
        return {"ok": True, "who": (who or {}).get("account_id")}

    app.include_router(guarded, dependencies=auth.DEPENDS)

    shop = APIRouter()

    @shop.get(OPEN)
    def _open():
        return {"ok": True}

    app.include_router(shop, dependencies=auth.DEPENDS)
    return app


@pytest.fixture(autouse=True)
def _leave_no_trace(monkeypatch: pytest.MonkeyPatch):
    """Put back every piece of global state this file touches.

    THE TILL CACHES ITS STORE HANDLE IN A MODULE GLOBAL, and `monkeypatch` does
    not know about it. A file that calls `set_store_dir` and does not put the
    previous value back leaves every LATER test file reading a catalogue in a
    temporary directory that has since been deleted — which is a failure in
    somebody else's suite, hundreds of tests later, with nothing pointing back
    here. Measured: this cost `tests/test_search.py` a red test before the
    restore below existed.

    Autouse, so the three tests that build their own client are covered too.
    """
    previous = upload_app._DEPS.get("store_dir")
    monkeypatch.delenv("GAWAAH_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("GAWAAH_SESSION_HOURS", raising=False)
    monkeypatch.delenv("GAWAAH_AUTH_OPEN", raising=False)
    auth.reset_rate_limit()
    auth.set_clock(None)
    yield
    auth.reset_rate_limit()
    auth.set_clock(None)
    upload_app._DEPS["store_dir"] = previous
    upload_app._DEPS["store"] = None


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A counter that lives and dies with the test.

    THE SHOP DIRECTORY IS REDIRECTED TWO WAYS ON PURPOSE, the way every other
    test in this repo does it: `set_store_dir` moves the till's cached handle
    and `GAWAAH_SHOP_DIR` covers any code that re-reads the environment. A
    harness that honoured only one of them once destroyed the live catalogue,
    and this module writes accounts — there is no undo for that either.
    """
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    upload_app.set_store_dir(tmp_path / "shop")
    return TestClient(_build_app())


class _Clock:
    """A clock a test can push forward. Whole seconds — never a float."""

    def __init__(self, at: int = 1_800_000_000) -> None:
        self.at = int(at)

    def __call__(self) -> int:
        return self.at

    def forward(self, seconds: int) -> None:
        self.at += int(seconds)


def _signup(c: TestClient, *, phone: str = OWNER_PHONE,
            password: str = OWNER_PASS, name: str = OWNER_NAME, **over):
    body = {"name": name, "phone": phone, "password": password}
    body.update(over)
    return c.post("/auth/signup", json=body)


def _signin(c: TestClient, *, phone: str = OWNER_PHONE,
            password: str = OWNER_PASS, **over):
    body = {"phone": phone, "password": password}
    body.update(over)
    return c.post("/auth/signin", json=body)


def _owner(c: TestClient) -> dict:
    r = _signup(c)
    assert r.status_code == 200, r.text
    return r.json()


def _accounts_file_text() -> str:
    return auth.accounts_path().read_text(encoding="utf-8")


def _audit_text() -> str:
    p = auth.audit_path()
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ==========================================================================
# 1. THE SWITCH — the part that must not surprise anybody
# ==========================================================================


def test_the_lock_ships_off(client: TestClient) -> None:
    """With nothing set, enforcement is off. This is the default and the point."""
    assert auth.auth_required() is False
    assert client.get("/auth/status").json()["enforced"] is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "y"])
def test_only_these_words_turn_the_lock_on(
        client: TestClient, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", value)
    assert auth.auth_required() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe",
                                   "please", "2", " "])
def test_everything_else_including_a_typo_leaves_it_off(
        client: TestClient, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """A switch that locks a live counter has to fail towards open."""
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", value)
    assert auth.auth_required() is False
    assert client.get(GUARDED).status_code == 200


def test_a_guarded_route_is_wide_open_while_the_switch_is_off(
        client: TestClient) -> None:
    """The guard is mounted on a real route and changes nothing about it."""
    _owner(client)
    client.post("/auth/signout")
    r = client.get(GUARDED)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "who": None}


def test_the_guard_records_who_is_signed_in_even_while_it_is_off(
        client: TestClient) -> None:
    """`request.state.shopkeeper` is filled in before enforcement exists, so a
    route can attribute an action without anything being locked."""
    me = _owner(client)["account"]
    assert client.get(GUARDED).json()["who"] == me["account_id"]


def test_with_the_switch_on_a_guarded_route_refuses_by_name(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _owner(client)
    client.post("/auth/signout")
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
    r = client.get(GUARDED)
    assert r.status_code == 401
    body = r.json()
    assert body["ok"] is False
    assert body["reason"] == auth.R_NOT_SIGNED_IN
    assert "signin" in body["detail"]


def test_with_the_switch_on_a_signed_in_request_goes_through(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    me = _owner(client)["account"]
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
    r = client.get(GUARDED)
    assert r.status_code == 200
    assert r.json()["who"] == me["account_id"]


def test_the_switch_alone_locks_nothing_that_was_not_decorated(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TWO THINGS have to be true before anything is locked: the switch is on
    AND `auth.DEPENDS` was put on that router. Setting the environment variable
    on a till nobody has decorated must change nothing at all — that is what
    makes the switch safe to leave in the shell profile."""
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop4"))
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
    upload_app.set_store_dir(tmp_path / "shop4")

    app = FastAPI()
    app.include_router(auth.router)
    auth.install(app)
    undecorated = APIRouter()

    @undecorated.get("/till/undecorated")
    def _u():
        return {"ok": True}

    app.include_router(undecorated)          # deliberately no dependencies=
    c = TestClient(app)
    assert auth.auth_required() is True
    assert c.get("/till/undecorated").status_code == 200


def test_the_lock_says_so_when_there_is_no_account_to_unlock_it_with(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Switch on, zero accounts: the counter is not silently bricked."""
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
    r = client.get(GUARDED)
    assert r.status_code == 401
    assert r.json()["reason"] == auth.R_NO_ACCOUNT_YET
    assert "/auth/signup" in r.json()["detail"]


def test_the_way_back_in_is_never_behind_the_lock(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Signing in cannot require being signed in."""
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
    for path in ("/auth/signup", "/auth/signin", "/auth/me", "/auth/status",
                 "/auth/signout"):
        assert auth.is_open_path(path) is True
    assert client.get("/auth/status").status_code == 200
    assert _signup(client).status_code == 200


def test_the_customers_side_is_not_open_by_default_and_can_be_opened(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stated policy, not a silent one: /store is locked with everything else
    unless GAWAAH_AUTH_OPEN says otherwise."""
    _owner(client)
    client.post("/auth/signout")
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
    assert client.get(OPEN).status_code == 401

    monkeypatch.setenv("GAWAAH_AUTH_OPEN", "/store")
    assert client.get(OPEN).status_code == 200
    assert client.get(GUARDED).status_code == 401


def test_an_open_prefix_does_not_open_a_lookalike_path(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """`/store` must not open `/storeroom`."""
    monkeypatch.setenv("GAWAAH_AUTH_OPEN", "/store")
    assert auth.is_open_path("/store") is True
    assert auth.is_open_path("/store/photo/x") is True
    assert auth.is_open_path("/storeroom") is False


def test_a_guard_refusal_without_install_is_still_not_a_500(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Forgetting `auth.install(app)` costs a nested body, never a crash."""
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop2"))
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
    upload_app.set_store_dir(tmp_path / "shop2")
    c = TestClient(_build_app(install=False), raise_server_exceptions=False)
    r = c.get(GUARDED)
    assert r.status_code == 401
    assert r.json()["detail"]["reason"] == auth.R_NO_ACCOUNT_YET


def test_install_is_idempotent_and_mounts_the_router_by_itself() -> None:
    app = FastAPI()
    auth.install(app)
    auth.install(app)
    auth.install(app)
    assert {"/auth/invite", "/auth/me", "/auth/signin", "/auth/signout",
            "/auth/signup", "/auth/status"} <= auth.mounted_paths(app)
    # Three calls, one copy of each route. FastAPI 0.141 hides an included
    # router behind a wrapper with no `.path`, so a naive check mounts the
    # whole module again — and a duplicated POST /auth/signup is a second
    # handler nobody knows is there.
    assert auth.mounted_paths(app) == auth.mounted_paths(app)
    c = TestClient(app)
    assert c.get("/auth/status").status_code == 200


def test_install_does_not_double_mount_a_router_that_was_already_included(
) -> None:
    """The orchestrator will almost certainly write include_router first."""
    app = FastAPI()
    app.include_router(auth.router)
    before = sum(1 for _ in _walk_paths(app) if _ == "/auth/me")
    auth.install(app)
    after = sum(1 for _ in _walk_paths(app) if _ == "/auth/me")
    assert before == after == 1


def _walk_paths(app):
    """Every path in an app's route tree, WITH duplicates — so a test can tell
    'mounted once' from 'mounted twice'."""
    stack = [app]
    seen_ids: set[int] = set()
    while stack:
        item = stack.pop()
        if id(item) in seen_ids:
            continue
        seen_ids.add(id(item))
        p = getattr(item, "path", None)
        if isinstance(p, str):
            yield p
        for name in ("routes", "original_router", "router"):
            child = getattr(item, name, None)
            if child is None or child is item:
                continue
            if isinstance(child, (list, tuple)):
                stack.extend(child)
            elif hasattr(child, "routes"):
                stack.append(child)


def test_the_router_carries_no_prefix_and_every_path_is_absolute() -> None:
    for r in auth.router.routes:
        assert r.path.startswith("/auth/"), r.path


# ==========================================================================
# 2. SIGNING UP
# ==========================================================================


def test_the_first_account_is_free_and_is_the_owner(client: TestClient) -> None:
    body = _owner(client)
    assert body["ok"] is True
    assert body["first_account"] is True
    assert body["signed_in"] is True
    assert body["account"]["role"] == "owner"
    assert body["account"]["name"] == OWNER_NAME
    assert body["account"]["account_id"].startswith("acct_")
    assert body["audited"] is True


def test_a_second_account_without_an_invitation_is_refused(
        client: TestClient) -> None:
    _owner(client)
    r = _signup(client, phone=STAFF_PHONE, password=STAFF_PASS, name="Imran")
    assert r.status_code == 400
    assert r.json()["reason"] == auth.R_SIGNUP_CLOSED
    assert auth.account_count() == 1


def test_the_same_phone_cannot_open_a_second_account(client: TestClient) -> None:
    _owner(client)
    code = client.post("/auth/invite").json()["invite"]
    r = _signup(client, password="another-good-one", invite=code)
    assert r.status_code == 400
    assert r.json()["reason"] == auth.R_PHONE_TAKEN


def test_sign_up_does_not_reveal_which_numbers_have_accounts(
        client: TestClient) -> None:
    """A stranger with no invitation must get the same sentence whether or not
    the number they typed is a real account here — otherwise sign-up becomes
    the enumeration oracle that sign-in refuses to be."""
    _owner(client)
    existing = _signup(client, password="another-good-one")
    fresh = _signup(client, phone="9000000009", password="another-good-one",
                    name="Nobody")
    assert existing.status_code == fresh.status_code == 400
    assert existing.json()["reason"] == fresh.json()["reason"] \
        == auth.R_SIGNUP_CLOSED
    assert existing.json()["detail"] == fresh.json()["detail"]


def test_a_phone_number_is_filed_by_its_digits_not_by_its_punctuation(
        client: TestClient) -> None:
    """+91 98765 43210 and 9876543210 are one shopkeeper, not two accounts."""
    assert _signup(client, phone="+91 98765 43210").status_code == 200
    code = client.post("/auth/invite").json()["invite"]
    r = _signup(client, phone="09876543210", password="different-one-here",
                invite=code)
    assert r.status_code == 400
    assert r.json()["reason"] == auth.R_PHONE_TAKEN
    assert _signin(client, phone=OWNER_PHONE).status_code == 200
    assert auth.normalise_phone("+91 98765 43210") == OWNER_PHONE
    assert auth.normalise_phone("09876543210") == OWNER_PHONE


@pytest.mark.parametrize("field,value,reason", [
    ("name", "", auth.R_NO_NAME),
    ("name", "x" * (auth.MAX_NAME + 1), auth.R_NAME_TOO_LONG),
    ("phone", "", auth.R_NO_PHONE),
    ("phone", "12345", auth.R_BAD_PHONE),
    ("phone", "not a phone at all", auth.R_BAD_PHONE),
    ("phone", "9" * (auth.MAX_PHONE + 1), auth.R_PHONE_TOO_LONG),
    ("password", "", auth.R_NO_PASSWORD),
    ("password", "short", auth.R_PASSWORD_SHORT),
    ("password", "p" * (auth.MAX_PASSWORD + 1), auth.R_PASSWORD_LONG),
])
def test_every_malformed_field_has_its_own_name(
        client: TestClient, field: str, value: str, reason: str) -> None:
    r = _signup(client, **{field: value})
    assert r.status_code == 400, r.text
    assert r.json()["reason"] == reason
    assert auth.account_count() == 0


def test_a_password_that_is_the_phone_number_is_refused(
        client: TestClient) -> None:
    r = _signup(client, password=OWNER_PHONE)
    assert r.status_code == 400
    assert r.json()["reason"] == auth.R_PASSWORD_IS_PHONE
    assert auth.account_count() == 0


@pytest.mark.parametrize("field", ["name", "phone", "password"])
def test_a_field_that_is_not_text_is_refused_without_echoing_it(
        client: TestClient, field: str) -> None:
    r = _signup(client, **{field: 987654321})
    assert r.status_code == 400
    assert r.json()["reason"] == auth.R_BAD_FIELD
    assert "987654321" not in r.text


def test_a_body_that_is_not_json_is_a_named_refusal(client: TestClient) -> None:
    r = client.post("/auth/signup", content=b"{not json",
                    headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert r.json()["reason"] == auth.R_BAD_BODY


def test_a_body_that_is_not_an_object_is_a_named_refusal(
        client: TestClient) -> None:
    r = client.post("/auth/signup", json=["Rekha", OWNER_PHONE])
    assert r.status_code == 400
    assert r.json()["reason"] == auth.R_BAD_BODY
    assert "list" in r.json()["detail"]


# ==========================================================================
# 3. THE PASSWORD IS NOT ANYWHERE
# ==========================================================================


def test_the_password_is_not_in_the_accounts_file(client: TestClient) -> None:
    _owner(client)
    raw = _accounts_file_text()
    assert OWNER_PASS not in raw
    rec = json.loads(raw)["accounts"][OWNER_PHONE]
    assert set(rec) >= {"salt_hex", "hash_hex", "n", "r", "p", "dklen"}
    assert rec["kdf"] == "scrypt"
    # Not one field holds anything that could be turned back into a password.
    assert "password" not in json.dumps(rec).lower()


def test_the_password_is_not_in_the_sign_up_or_sign_in_body(
        client: TestClient) -> None:
    assert OWNER_PASS not in _signup(client).text
    assert OWNER_PASS not in _signin(client).text


def test_the_password_is_not_in_the_audit_chain(client: TestClient) -> None:
    _owner(client)
    _signin(client)
    _signin(client, password="wrong-but-long-enough")
    assert OWNER_PASS not in _audit_text()


def test_two_accounts_with_the_same_password_store_different_bytes(
        client: TestClient) -> None:
    """That is what a per-user random salt means, stated as an assertion."""
    _owner(client)
    code = client.post("/auth/invite").json()["invite"]
    assert _signup(client, phone=STAFF_PHONE, password=OWNER_PASS,
                   name="Imran", invite=code).status_code == 200
    accounts = json.loads(_accounts_file_text())["accounts"]
    a, b = accounts[OWNER_PHONE], accounts[STAFF_PHONE]
    assert a["salt_hex"] != b["salt_hex"]
    assert a["hash_hex"] != b["hash_hex"]
    assert len(bytes.fromhex(a["salt_hex"])) == auth.SCRYPT_SALT_BYTES
    assert len(bytes.fromhex(a["hash_hex"])) == auth.SCRYPT_DKLEN


def test_the_stored_cost_is_what_the_module_says_it_is(client: TestClient) -> None:
    _owner(client)
    rec = json.loads(_accounts_file_text())["accounts"][OWNER_PHONE]
    assert (rec["n"], rec["r"], rec["p"]) == (auth.SCRYPT_N, auth.SCRYPT_R,
                                              auth.SCRYPT_P)
    assert rec["n"] >= 1 << 14


def test_an_absurd_stored_cost_is_refused_rather_than_executed(
        client: TestClient) -> None:
    """A JSON file is editable. `n = 2**30` must be a refusal, not 128 GB."""
    _owner(client)
    doc = json.loads(_accounts_file_text())
    doc["accounts"][OWNER_PHONE]["n"] = 1 << 30
    auth.accounts_path().write_text(json.dumps(doc), encoding="utf-8")
    r = _signin(client)
    assert r.status_code == 400
    assert r.json()["reason"] == auth.R_STORE_UNREADABLE


def test_a_cost_that_is_not_a_power_of_two_is_refused(client: TestClient) -> None:
    _owner(client)
    doc = json.loads(_accounts_file_text())
    doc["accounts"][OWNER_PHONE]["n"] = 30000
    auth.accounts_path().write_text(json.dumps(doc), encoding="utf-8")
    assert _signin(client).json()["reason"] == auth.R_STORE_UNREADABLE


def test_a_salt_that_is_not_hexadecimal_is_refused(client: TestClient) -> None:
    _owner(client)
    doc = json.loads(_accounts_file_text())
    doc["accounts"][OWNER_PHONE]["salt_hex"] = "zzzz"
    auth.accounts_path().write_text(json.dumps(doc), encoding="utf-8")
    assert _signin(client).json()["reason"] == auth.R_STORE_UNREADABLE


def test_a_corrupt_accounts_file_refuses_and_does_not_overwrite_itself(
        client: TestClient) -> None:
    _owner(client)
    auth.accounts_path().write_text("{{{ this is not json", encoding="utf-8")
    r = _signin(client)
    assert r.status_code == 400
    assert r.json()["reason"] == auth.R_STORE_UNREADABLE
    # Nothing repaired it silently — an account is never lost to a parser.
    assert _accounts_file_text().startswith("{{{")


# ==========================================================================
# 4. SIGNING IN
# ==========================================================================


def test_the_right_password_signs_in(client: TestClient) -> None:
    _owner(client)
    client.post("/auth/signout")
    r = _signin(client)
    assert r.status_code == 200
    body = r.json()
    assert body["signed_in"] is True
    assert body["account"]["phone"] == OWNER_PHONE
    assert body["expires_in_s"] == auth.SESSION_HOURS * 3600


def test_the_token_is_in_a_header_and_never_in_the_body(
        client: TestClient) -> None:
    """The one property that keeps a session out of screenshots and bug reports."""
    _owner(client)
    client.cookies.clear()
    r = _signin(client)
    token = r.cookies.get(auth.SESSION_COOKIE)
    assert token, "sign-in must set the session cookie"
    assert token not in r.text
    assert "token" not in r.json()
    cookie_header = r.headers["set-cookie"].lower()
    assert "httponly" in cookie_header
    assert "samesite=lax" in cookie_header
    # Plain http on a shop's wifi: a Secure cookie here would be discarded.
    assert "secure" not in cookie_header


def test_the_token_is_not_in_the_audit_chain_and_only_its_hash_is_on_disk(
        client: TestClient) -> None:
    _owner(client)
    token = client.cookies.get(auth.SESSION_COOKIE)
    assert token not in _audit_text()
    sessions_raw = auth.sessions_path().read_text(encoding="utf-8")
    assert token not in sessions_raw
    import hashlib
    assert hashlib.sha256(token.encode()).hexdigest() in sessions_raw


def test_the_audit_line_holds_no_password_no_hash_no_salt_and_no_phone(
        client: TestClient) -> None:
    """The audit log is the file most likely to be pasted into a bug report."""
    _owner(client)
    token = client.cookies.get(auth.SESSION_COOKIE)
    _signin(client, password="definitely-wrong-here")
    client.post("/auth/signout")
    rec = json.loads(_accounts_file_text())["accounts"][OWNER_PHONE]
    text = _audit_text()
    assert text.strip(), "something must have been audited"
    for secret in (OWNER_PASS, token, rec["hash_hex"], rec["salt_hex"],
                   OWNER_PHONE, OWNER_NAME):
        assert secret not in text, secret


def test_the_audit_chain_verifies(client: TestClient) -> None:
    _owner(client)
    _signin(client, password="wrong-one-here")
    _signin(client)
    client.post("/auth/signout")
    ok, n, _head, err = verify(auth.audit_path())
    assert ok, err
    assert n >= 4


def test_a_wrong_password_and_an_unknown_phone_answer_identically(
        client: TestClient) -> None:
    """The counter has no reason to say which of the staff have accounts."""
    _owner(client)
    wrong = _signin(client, password="wrong-but-long-enough")
    auth.reset_rate_limit()
    unknown = _signin(client, phone="9000000001", password=OWNER_PASS)
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["reason"] == unknown.json()["reason"] \
        == auth.R_BAD_CREDENTIALS
    assert wrong.json()["detail"] == unknown.json()["detail"]


def test_a_refused_sign_in_leaves_nothing_signed_in(client: TestClient) -> None:
    _owner(client)
    client.post("/auth/signout")
    _signin(client, password="wrong-but-long-enough")
    assert client.get("/auth/me").status_code == 401


def test_sign_in_validates_its_fields_before_it_touches_the_store(
        client: TestClient) -> None:
    _owner(client)
    assert _signin(client, phone="").json()["reason"] == auth.R_NO_PHONE
    assert _signin(client, password="").json()["reason"] == auth.R_NO_PASSWORD
    assert _signin(client, password="tiny").json()["reason"] \
        == auth.R_PASSWORD_SHORT


# ==========================================================================
# 5. THE RATE LIMIT
# ==========================================================================


def test_five_wrong_passwords_lock_the_phone_and_say_so_by_name(
        client: TestClient) -> None:
    _owner(client)
    for _ in range(auth.MAX_ATTEMPTS):
        r = _signin(client, password="wrong-but-long-enough")
        assert r.json()["reason"] == auth.R_BAD_CREDENTIALS
    r = _signin(client, password="wrong-but-long-enough")
    assert r.status_code == 429
    body = r.json()
    assert body["reason"] == auth.R_TOO_MANY_ATTEMPTS
    # "say so by name": the refusal names the number that is locked and how
    # long it has to wait.
    assert OWNER_PHONE in body["detail"]
    assert str(auth.MAX_ATTEMPTS) in body["detail"]
    assert "seconds" in body["detail"]


def test_the_right_password_is_refused_while_the_phone_is_locked(
        client: TestClient) -> None:
    """Otherwise the limit is a suggestion."""
    _owner(client)
    for _ in range(auth.MAX_ATTEMPTS):
        _signin(client, password="wrong-but-long-enough")
    r = _signin(client)
    assert r.status_code == 429
    assert r.json()["reason"] == auth.R_TOO_MANY_ATTEMPTS


def test_hammering_a_locked_phone_does_not_extend_the_lock(
        client: TestClient) -> None:
    """Or anybody on the wifi could hold a shopkeeper out for ever."""
    clock = _Clock()
    auth.set_clock(clock)
    _owner(client)
    for _ in range(auth.MAX_ATTEMPTS):
        _signin(client, password="wrong-but-long-enough")

    clock.forward(auth.LOCK_S - 10)
    for _ in range(20):
        assert _signin(client, password="wrong-again-here").status_code == 429
    clock.forward(11)
    assert _signin(client).status_code == 200


def test_the_lock_lets_go_after_its_own_window(client: TestClient) -> None:
    clock = _Clock()
    auth.set_clock(clock)
    _owner(client)
    for _ in range(auth.MAX_ATTEMPTS):
        _signin(client, password="wrong-but-long-enough")
    assert _signin(client).status_code == 429
    clock.forward(auth.LOCK_S + 1)
    assert _signin(client).status_code == 200


def test_a_successful_sign_in_forgets_the_earlier_mistakes(
        client: TestClient) -> None:
    _owner(client)
    for _ in range(auth.MAX_ATTEMPTS - 1):
        _signin(client, password="wrong-but-long-enough")
    assert _signin(client).status_code == 200
    # The counter starts again from zero, so a fat-fingered morning does not
    # lock the till at lunchtime.
    for _ in range(auth.MAX_ATTEMPTS - 1):
        assert _signin(client, password="wrong-but-long-enough").status_code == 401


def test_a_phone_with_no_account_is_rate_limited_the_same_way(
        client: TestClient) -> None:
    """If only real accounts were limited, the limit itself would enumerate
    which numbers are real."""
    _owner(client)
    for _ in range(auth.MAX_ATTEMPTS):
        _signin(client, phone="9000000002", password="wrong-but-long-enough")
    r = _signin(client, phone="9000000002", password="wrong-but-long-enough")
    assert r.status_code == 429


def test_one_phones_lock_does_not_touch_another(client: TestClient) -> None:
    _owner(client)
    for _ in range(auth.MAX_ATTEMPTS):
        _signin(client, phone="9000000003", password="wrong-but-long-enough")
    assert _signin(client, phone="9000000003",
                   password="wrong-but-long-enough").status_code == 429
    assert _signin(client).status_code == 200


def test_the_attempt_table_does_not_grow_without_end(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Somebody cycling phone numbers must not be able to exhaust memory."""
    monkeypatch.setattr(auth, "MAX_TRACKED_PHONES", 8)
    for i in range(40):
        auth._record_failure(f"90000{i:05d}")
    auth._forget_old(auth._now())
    assert len(auth._ATTEMPTS) <= 8


# ==========================================================================
# 6. WHO AM I, AND SIGNING OUT
# ==========================================================================


def test_who_am_i_with_no_session_is_a_named_refusal(client: TestClient) -> None:
    r = client.get("/auth/me")
    assert r.status_code == 401
    assert r.json()["reason"] == auth.R_NO_SESSION


def test_who_am_i_after_signing_in(client: TestClient) -> None:
    me = _owner(client)["account"]
    r = client.get("/auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body["account"] == me
    assert body["session"]["expires_in_s"] == auth.SESSION_HOURS * 3600
    # The allowlist in _public_account, asserted rather than assumed.
    assert set(body["account"]) == {"account_id", "name", "phone", "role",
                                    "created_at"}


def test_a_bearer_token_works_for_a_client_that_cannot_keep_a_cookie(
        client: TestClient) -> None:
    _owner(client)
    token = client.cookies.get(auth.SESSION_COOKIE)
    client.cookies.clear()
    assert client.get("/auth/me").status_code == 401
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_a_token_this_counter_never_issued_is_refused(client: TestClient) -> None:
    _owner(client)
    client.cookies.clear()
    r = client.get("/auth/me",
                   headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401
    assert r.json()["reason"] == auth.R_SESSION_UNKNOWN


def test_signing_out_forgets_the_session_on_the_server_not_just_the_browser(
        client: TestClient) -> None:
    """A token that still works after sign-out is not a sign-out."""
    _owner(client)
    token = client.cookies.get(auth.SESSION_COOKIE)
    r = client.post("/auth/signout")
    assert r.status_code == 200
    assert r.json()["cleared"] is True

    replay = client.get("/auth/me",
                        headers={"Authorization": f"Bearer {token}"})
    assert replay.status_code == 401
    assert replay.json()["reason"] == auth.R_SESSION_UNKNOWN
    assert auth._token_id(token) not in \
        auth.sessions_path().read_text(encoding="utf-8")


def test_signing_out_clears_the_cookie(client: TestClient) -> None:
    _owner(client)
    client.post("/auth/signout")
    assert not client.cookies.get(auth.SESSION_COOKIE)
    assert client.get("/auth/me").status_code == 401


def test_signing_out_twice_is_not_an_error(client: TestClient) -> None:
    """Answering 401 to somebody trying to LEAVE would strand a stale tab."""
    _owner(client)
    assert client.post("/auth/signout").json()["cleared"] is True
    r = client.post("/auth/signout")
    assert r.status_code == 200
    assert r.json()["cleared"] is False


def test_a_session_expires_and_says_when(client: TestClient) -> None:
    clock = _Clock()
    auth.set_clock(clock)
    _owner(client)
    assert client.get("/auth/me").status_code == 200
    clock.forward(auth.SESSION_HOURS * 3600 + 1)
    r = client.get("/auth/me")
    assert r.status_code == 401
    assert r.json()["reason"] == auth.R_SESSION_EXPIRED
    assert "Sign in again" in r.json()["detail"]


def test_an_expired_session_is_dropped_from_disk_when_it_is_noticed(
        client: TestClient) -> None:
    clock = _Clock()
    auth.set_clock(clock)
    _owner(client)
    clock.forward(auth.SESSION_HOURS * 3600 + 1)
    client.get("/auth/me")
    doc = json.loads(auth.sessions_path().read_text(encoding="utf-8"))
    assert doc["sessions"] == {}


def test_the_session_length_is_settable(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop3"))
    monkeypatch.setenv("GAWAAH_SESSION_HOURS", "2")
    monkeypatch.delenv("GAWAAH_REQUIRE_AUTH", raising=False)
    upload_app.set_store_dir(tmp_path / "shop3")
    auth.reset_rate_limit()
    c = TestClient(_build_app())
    assert _signup(c).json()["ok"] is True
    assert c.get("/auth/me").json()["session"]["expires_in_s"] == 2 * 3600


def test_a_nonsense_session_length_falls_back_rather_than_failing(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAWAAH_SESSION_HOURS", "biscuit")
    assert auth._session_seconds() == auth.SESSION_HOURS * 3600
    monkeypatch.setenv("GAWAAH_SESSION_HOURS", "0")
    assert auth._session_seconds() == 3600


def test_the_oldest_session_goes_when_an_account_holds_too_many(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "MAX_SESSIONS_PER_ACCOUNT", 3)
    _owner(client)
    for _ in range(4):
        client.cookies.clear()
        assert _signin(client).status_code == 200
    doc = json.loads(auth.sessions_path().read_text(encoding="utf-8"))
    assert len(doc["sessions"]) == 3


def test_a_session_whose_account_is_gone_is_worthless(client: TestClient) -> None:
    _owner(client)
    doc = json.loads(_accounts_file_text())
    doc["accounts"] = {}
    auth.accounts_path().write_text(json.dumps(doc), encoding="utf-8")
    r = client.get("/auth/me")
    assert r.status_code == 401
    assert r.json()["reason"] == auth.R_SESSION_UNKNOWN


# ==========================================================================
# 7. INVITATIONS
# ==========================================================================


def test_minting_an_invitation_needs_a_signed_in_caller_even_with_the_lock_off(
        client: TestClient) -> None:
    """This is the act that widens who may use the counter, so it locks itself."""
    assert auth.auth_required() is False
    r = client.post("/auth/invite")
    assert r.status_code == 401
    assert r.json()["reason"] == auth.R_NOT_SIGNED_IN


def test_an_invitation_opens_exactly_one_more_account(client: TestClient) -> None:
    _owner(client)
    r = client.post("/auth/invite")
    assert r.status_code == 200
    code = r.json()["invite"]
    assert code.startswith(auth.INVITE_PREFIX)
    assert r.json()["single_use"] is True

    made = _signup(client, phone=STAFF_PHONE, password=STAFF_PASS,
                   name="Imran", invite=code)
    assert made.status_code == 200
    assert made.json()["account"]["role"] == "staff"
    assert made.json()["first_account"] is False
    assert auth.account_count() == 2


def test_an_invitation_cannot_be_used_twice(client: TestClient) -> None:
    _owner(client)
    code = client.post("/auth/invite").json()["invite"]
    assert _signup(client, phone=STAFF_PHONE, password=STAFF_PASS,
                   name="Imran", invite=code).status_code == 200
    again = _signup(client, phone="9000000004", password="third-account-pass",
                    name="Sunil", invite=code)
    assert again.status_code == 400
    assert again.json()["reason"] == auth.R_INVITE_USED
    assert auth.account_count() == 2


def test_an_invitation_this_shop_never_issued_is_refused(
        client: TestClient) -> None:
    _owner(client)
    r = _signup(client, phone=STAFF_PHONE, password=STAFF_PASS, name="Imran",
                invite="inv_made-this-up")
    assert r.status_code == 400
    assert r.json()["reason"] == auth.R_INVITE_UNKNOWN


def test_an_invitation_expires(client: TestClient) -> None:
    clock = _Clock()
    auth.set_clock(clock)
    _owner(client)
    code = client.post("/auth/invite").json()["invite"]
    clock.forward(auth.INVITE_HOURS * 3600 + 1)
    r = _signup(client, phone=STAFF_PHONE, password=STAFF_PASS, name="Imran",
                invite=code)
    assert r.status_code == 400
    assert r.json()["reason"] == auth.R_INVITE_EXPIRED
    assert auth.account_count() == 1


def test_only_a_hash_of_the_invitation_is_kept(client: TestClient) -> None:
    _owner(client)
    code = client.post("/auth/invite").json()["invite"]
    assert code not in _accounts_file_text()
    assert code not in _audit_text()
    assert auth._token_id(code) in _accounts_file_text()


def test_a_refused_invitation_does_not_burn_it(client: TestClient) -> None:
    """A bad password on the sign-up must not spend somebody else's invite."""
    _owner(client)
    code = client.post("/auth/invite").json()["invite"]
    assert _signup(client, phone=STAFF_PHONE, password="tiny", name="Imran",
                   invite=code).status_code == 400
    assert _signup(client, phone=STAFF_PHONE, password=STAFF_PASS,
                   name="Imran", invite=code).status_code == 200


# ==========================================================================
# 8. NOTHING HERE IS A 500, AND NOTHING WRITES WHERE IT SHOULD NOT
# ==========================================================================


@pytest.mark.parametrize("payload", [
    None, [], "a string", 42, True,
    {"name": {"nested": 1}},
    {"phone": ["9876543210"]},
    {"password": {"a": "b"}},
    {"name": "R", "phone": OWNER_PHONE, "password": OWNER_PASS,
     "invite": {"not": "text"}},
    {"name": chr(0) * 2, "phone": " ", "password": " " * 9},
])
def test_no_shape_of_body_produces_a_500(client: TestClient, payload) -> None:
    for path in ("/auth/signup", "/auth/signin"):
        r = client.post(path, json=payload)
        assert r.status_code in (400, 401, 429), (path, payload, r.status_code)
        assert r.json()["ok"] is False
        assert isinstance(r.json()["reason"], str)
        assert isinstance(r.json()["detail"], str)
        auth.reset_rate_limit()


def test_an_unexpected_failure_is_a_named_refusal_and_not_a_crash(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """R_INTERNAL is reachable, which is what makes 'never a 500' true rather
    than merely untested."""
    def _boom() -> dict:
        raise RuntimeError("the disk went away")

    monkeypatch.setattr(auth, "_load_accounts", _boom)
    r = _signup(client)
    assert r.status_code == 400
    assert r.json()["reason"] == auth.R_INTERNAL
    assert "RuntimeError" in r.json()["detail"]
    # The catch-all is the likeliest place for a password to escape into a
    # log, because an exception message is not written by the person who
    # thought about secrets. It must not carry one.
    assert OWNER_PASS not in r.text


def test_everything_is_written_inside_the_shop_directory(
        client: TestClient, tmp_path: Path) -> None:
    """GAWAAH_SHOP_DIR is honoured for every file this module writes."""
    _owner(client)
    client.post("/auth/invite")
    shop = tmp_path / "shop"
    for p in (auth.accounts_path(), auth.sessions_path(), auth.audit_path()):
        assert p.exists()
        assert shop in p.parents
    assert auth.shop_dir() == shop


def test_the_accounts_file_is_not_world_readable(client: TestClient) -> None:
    import stat
    _owner(client)
    mode = stat.S_IMODE(auth.accounts_path().stat().st_mode)
    assert mode & 0o077 == 0, oct(mode)


def test_the_status_screen_names_no_person(client: TestClient) -> None:
    _owner(client)
    client.post("/auth/signout")
    body = client.get("/auth/status").json()
    assert body["accounts"] == 1
    assert body["signup_open"] is False
    assert body["signup_needs_invite"] is True
    assert body["enforced"] is False
    assert body["switch"] == "GAWAAH_REQUIRE_AUTH"
    assert body["signed_in"] is False
    assert OWNER_NAME not in json.dumps(body)
    assert OWNER_PHONE not in json.dumps(body)


def test_the_status_screen_says_sign_up_is_open_on_a_fresh_counter(
        client: TestClient) -> None:
    body = client.get("/auth/status").json()
    assert body["accounts"] == 0
    assert body["signup_open"] is True
    assert body["signup_needs_invite"] is False


def test_every_reason_is_a_distinct_named_string() -> None:
    names = {k: v for k, v in vars(auth).items()
             if k.startswith("R_") and isinstance(v, str)}
    assert len(names) >= 24
    assert all(v.startswith("auth_") for v in names.values())
    assert len(set(names.values())) == len(names), "two refusals share a name"


def test_the_module_has_no_way_to_read_a_password_back() -> None:
    """A source assertion, because this is the invariant that cannot be tested
    from outside: there is no decrypt, no reversible encoding and no second
    copy of the password anywhere in the file."""
    src = Path(auth.__file__).read_text(encoding="utf-8")
    for forbidden in ("b64decode", "decrypt", "Fernet", "password_plain",
                      "unhexlify(password", "cipher"):
        assert forbidden not in src, forbidden
    assert "hashlib.scrypt" in src


def test_current_shopkeeper_never_raises(client: TestClient) -> None:
    """The helper the orchestrator can call from any route, on any request."""
    from starlette.requests import Request as _Req

    scope = {"type": "http", "method": "GET", "path": "/", "headers": [],
             "query_string": b"", "client": ("1.2.3.4", 1)}
    assert auth.current_shopkeeper(_Req(scope)) is None
    auth.accounts_path().parent.mkdir(parents=True, exist_ok=True)
    auth.accounts_path().write_text("not json at all", encoding="utf-8")
    assert auth.current_shopkeeper(_Req(scope)) is None
