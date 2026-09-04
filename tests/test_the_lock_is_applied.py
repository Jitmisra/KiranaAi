"""The counter reports itself as locked, and IS.

`tests/test_auth.py` proves the guard works. It proves it against a two-route
app the test builds itself, and every one of its assertions stayed green while
the real till answered 200 to a stranger on every screen it has — because
nothing applied the guard to the real till. The measurement that found it:

    GAWAAH_REQUIRE_AUTH=1
    GET /auth/status  ->  {"enforced": true, "accounts": 1, "signed_in": false}
    signed OUT: GET /shop         -> 200
    signed OUT: GET /manage/today -> 200
    signed OUT: GET /orders       -> 200

So this file tests THE APP THAT SHIPS — `tools.upload_app.app`, the same object
`make serve` runs — and it tests the three things that were separately true and
jointly useless:

  1. THE GUARD IS ON EVERY ROUTE. Not "on a router in a test", not "importable":
     a walk of the live route tree with zero routes unaccounted for. This is the
     assertion that would have failed before the fix and passes after it.
  2. WHAT IS OPEN IS OPEN ON PURPOSE. A customer with no account and no session
     can still open the shop's front door, the bill they were sent, and the
     payment QR their own order page draws — with the lock ON. Getting this
     wrong does not look like a bug, it looks like the shop being shut.
  3. `enforced` REPORTS THE WIRING, NOT THE ENVIRONMENT. An app with the switch
     on and the guard applied to nothing must answer `enforced: false`, because
     that is the truth, and the old answer to that exact state was `true`.

Nothing here writes outside `tmp_path`: both `GAWAAH_SHOP_DIR` and
`GAWAAH_DATA_DIR` are redirected, and the till's cached store handle is put back
afterwards. Nothing here touches money, mints anything, or appends to
`results/audit.jsonl`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import APIRouter, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import auth  # noqa: E402
from tools import upload_app  # noqa: E402

OWNER = {"name": "Rekha Devi", "phone": "9876543210",
         "password": "chai-biscuit-2026"}

#: The shopkeeper's own data. The first three are the bug report's, verbatim;
#: the rest are one route from every other router mounted on the till, so that
#: a twenty-fourth router mounted without the guard is a red test here and not
#: a discovery somebody makes in a shop.
SHOPKEEPERS_OWN = (
    "/shop",
    "/manage/today",
    "/orders",
    "/manage/history",
    "/manage/inventory",
    "/manage/settings",
    "/shop/profile",
    "/offers",
    "/assistant/health",
    "/categories",
    "/customers",
    "/daybook",
    "/expenses",
    "/purchases",
    "/search?q=x",
    "/stock",
    "/advisor/health",
    "/expiry",
    "/gst/rules",
    "/insights",
    "/labels/layouts",
    "/loyalty/rules",
    "/po",
    "/share/limits",
    "/shelf",
    "/weighed",
    "/detector",
    "/api/money/health",
    "/qr/gawaah_demo",
)
#: NOT in the list above, and each absence is a decision recorded in
#: `tools/upload_app.py`: `/receipt/*` is the bill a customer is sent on
#: WhatsApp, and `/qr/link/*` is the payment QR their own order page draws.

#: Reachable by somebody who has never signed in and never will, with the lock
#: ON. Every one of these has a reason recorded in `tools/upload_app.py`.
STAYS_OPEN = (
    "/",                       # the page. Lock it and there is no sign-in screen.
    "/health",                 # a monitor has no account.
    "/store",                  # the shutter QR. The shop's own front door.
    "/store/qr",
    "/store/link",
    "/auth/status",            # the way back in.
)


@pytest.fixture(autouse=True)
def _leave_no_trace(monkeypatch: pytest.MonkeyPatch):
    """Put back the till's cached store handle. See tests/test_auth.py."""
    previous = upload_app._DEPS.get("store_dir")
    monkeypatch.delenv("GAWAAH_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("GAWAAH_AUTH_OPEN", raising=False)
    auth.reset_rate_limit()
    yield
    auth.reset_rate_limit()
    upload_app._DEPS["store_dir"] = previous
    upload_app._DEPS["store"] = None


@pytest.fixture()
def till(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """THE APP THAT SHIPS, pointed at a shop directory that dies with the test.

    BOTH variables, not one. `GAWAAH_SHOP_DIR` alone leaves `GAWAAH_DATA_DIR`
    reading the live catalogue in `results/`, which has produced false failures
    in this repo before — and this file signs accounts up, which is not
    something to do in a real shop's directory.
    """
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "data"))
    upload_app.set_store_dir(tmp_path / "shop")
    return TestClient(upload_app.app)


def _owner(c: TestClient) -> None:
    r = c.post("/auth/signup", json=OWNER)
    assert r.status_code == 200, r.text


def _signed_out(c: TestClient) -> None:
    c.post("/auth/signout")


def _reason(r) -> str:
    if "json" not in r.headers.get("content-type", ""):
        return ""
    body = r.json()
    return str(body.get("reason") or "") if isinstance(body, dict) else ""


# ==========================================================================
# 1. THE GUARD IS ON THE REAL TILL
# ==========================================================================


def test_every_route_on_the_shipped_till_carries_the_guard() -> None:
    """The assertion that was false. No route may be quietly unguarded.

    `guard_coverage` walks the live tree rather than `app.routes`, because
    FastAPI 0.141 hides an included router behind a wrapper that keeps the
    `dependencies=` argument to one side — a naive check reads every guarded
    route in this program as unguarded.
    """
    cov = auth.guard_coverage(upload_app.app)
    assert cov["unguarded_paths"] == [], (
        "these routes serve the shop and nothing guards them")
    # A guard on nothing would also satisfy the line above.
    assert cov["guarded"] > 100, cov["guarded"]


def test_the_only_routes_without_a_guard_are_the_ones_that_cannot_take_one(
) -> None:
    """`/assets` is a StaticFiles mount and `/docs` is FastAPI's own. Neither
    can carry a `Depends`, and both are named rather than silently excused."""
    cov = auth.guard_coverage(upload_app.app)
    assert set(cov["no_guard_possible"]) <= {
        "/assets", "/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"}
    # The sign-in routes are open by definition; /auth/invite guards itself.
    assert set(cov["open_by_auth"]) == set(auth.OPEN_PATHS) | auth.SELF_GUARDED


def test_the_router_list_and_the_guarded_list_are_the_same_list() -> None:
    """Every `include_router` in the till passes `dependencies=`.

    A source assertion, because the failure mode is a router mounted next month
    without the keyword — which no request-level test can see until somebody
    happens to ask for that path.
    """
    src = (Path(upload_app.__file__)).read_text(encoding="utf-8")
    mounts = [ln.strip() for ln in src.splitlines()
              if ln.startswith("app.include_router(")]
    assert len(mounts) >= 23, mounts
    bare = [m for m in mounts if "dependencies=AUTH_GUARD" not in m]
    assert bare == [], bare


# ==========================================================================
# 2. THE THREE-WAY MEASUREMENT
# ==========================================================================


def test_with_the_lock_off_everything_answers_exactly_as_before(
        till: TestClient) -> None:
    """The default. Twenty other agents are working against this app right now
    and nothing above may have changed what they can reach."""
    assert auth.auth_required() is False
    for path in SHOPKEEPERS_OWN + STAYS_OPEN:
        r = till.get(path)
        assert r.status_code != 401, (path, r.status_code, r.text[:200])
        assert _reason(r) != auth.R_NOT_SIGNED_IN, path


def test_with_the_lock_on_and_signed_out_the_shop_refuses_by_name(
        till: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _owner(till)
    _signed_out(till)
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
    for path in SHOPKEEPERS_OWN:
        r = till.get(path)
        assert r.status_code == 401, (path, r.status_code, r.text[:200])
        assert _reason(r) == auth.R_NOT_SIGNED_IN, path


def test_with_the_lock_on_and_signed_in_the_shop_answers(
        till: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _owner(till)                       # signing up signs you in
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
    for path in SHOPKEEPERS_OWN:
        r = till.get(path)
        assert r.status_code != 401, (path, r.status_code, r.text[:200])


def test_the_bug_report_measured_three_paths_and_all_three_are_shut(
        till: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verbatim from the report, because a regression will look exactly like it."""
    _owner(till)
    _signed_out(till)
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
    status = till.get("/auth/status").json()
    assert status["enforced"] is True
    assert status["accounts"] == 1
    assert status["signed_in"] is False
    for path in ("/shop", "/manage/today", "/orders"):
        assert till.get(path).status_code == 401, path


# ==========================================================================
# 3. A CUSTOMER HAS NO ACCOUNT AND NEVER WILL
# ==========================================================================


def test_a_customer_can_open_the_storefront_while_the_lock_is_on(
        till: TestClient, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The shutter QR must survive the lock. THIS IS THE ONE THAT COSTS A SHOP
    MONEY if it is wrong: a customer standing outside with a phone has no
    account, cannot be given one, and sees only whatever this returns."""
    _owner(till)
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
    # A different client: its own cookie jar, no session, none possible.
    phone = TestClient(upload_app.app)
    for path in ("/", "/store", "/store/qr", "/store/link"):
        r = phone.get(path)
        assert r.status_code == 200, (path, r.status_code)
        assert _reason(r) != auth.R_NOT_SIGNED_IN, path


def test_a_customer_reaches_their_own_order_and_bill_while_the_lock_is_on(
        till: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Not 200 — these ids are nonsense — but never the LOCK's refusal.

    An order the customer placed, the bill they were sent on WhatsApp and the
    payment QR their own page draws all have to answer for themselves. A 401
    here means the shop stopped serving the person it is for.
    """
    _owner(till)
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
    phone = TestClient(upload_app.app)
    for path in ("/store/photo/nope", "/store/order/nope", "/qr/link/nope",
                 "/receipt/nope", "/receipt/nope/page", "/receipt/nope/qr"):
        r = phone.get(path)
        assert r.status_code != 401, (path, r.text[:160])
        assert _reason(r) not in (auth.R_NOT_SIGNED_IN, auth.R_NO_ACCOUNT_YET), path


def test_the_open_prefixes_do_not_open_their_lookalikes(
        till: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """`/store` must not open `/stock`, and `/qr/link` must not open `/qr/x`.

    The sticker printer and the payment QR live one segment apart, and the
    longer prefix is the whole of what keeps the shopkeeper's printer behind
    the lock.
    """
    _owner(till)
    _signed_out(till)
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
    for path in ("/stock", "/qr/gawaah_demo", "/share/limits"):
        assert till.get(path).status_code == 401, path


def test_the_page_and_its_bundle_load_with_the_lock_on(
        till: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A locked counter with no way to draw the sign-in screen is a brick."""
    _owner(till)
    _signed_out(till)
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
    assert till.get("/").status_code == 200
    assert till.get("/health").status_code == 200
    # /assets is a StaticFiles mount and cannot carry a guard at all; assert
    # that it is still MOUNTED rather than that it answers, because a fresh
    # checkout has no ui/dist.
    cov = auth.guard_coverage(upload_app.app)
    if (Path(upload_app.__file__).resolve().parent.parent
            / "ui" / "dist" / "assets").is_dir():
        assert "/assets" in cov["no_guard_possible"]


# ==========================================================================
# 4. THE REFUSAL IS THIS REPO'S REFUSAL
# ==========================================================================


def test_a_locked_out_request_gets_this_repos_flat_refusal_shape(
        till: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Not Starlette's `{"detail": {...}}`. A page reads `body.reason`.

    `install()` registers the handler that flattens it; this asserts the
    handler actually fires for the guard on the shipped app, which is a
    different claim from "the handler exists".
    """
    _owner(till)
    _signed_out(till)
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
    r = till.get("/shop")
    assert r.status_code == 401
    body = r.json()
    assert set(body) == {"ok", "reason", "detail", "settles_money"}
    assert body["ok"] is False
    assert body["settles_money"] is False
    assert body["reason"] == auth.R_NOT_SIGNED_IN
    assert "/auth/signin" in body["detail"]


def test_an_empty_counter_says_so_instead_of_asking_for_a_session(
        till: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Switch on, no account: the shopkeeper is told to create one, not to sign
    in to something that does not exist."""
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
    r = till.get("/shop")
    assert r.status_code == 401
    assert r.json()["reason"] == auth.R_NO_ACCOUNT_YET
    assert "/auth/signup" in r.json()["detail"]
    assert till.post("/auth/signup", json=OWNER).status_code == 200


def test_a_session_that_ran_out_locks_the_shop_and_names_a_session_reason(
        till: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A twelve-hour shift ends and the counter shuts behind it.

    WHICH of the three session refusals `/auth/me` gives depends on who asked
    first, and that is worth stating because a screen must not switch on it.
    `_resolve` PRUNES an expired session the moment it sees one — so the first
    guarded request after expiry gets `auth_session_expired` internally, drops
    the record, and every request after that gets `auth_session_not_known_here`
    instead. On a real page the poller in App.tsx reaches a guarded route long
    before anybody opens the sign-in screen, so the precise "ran out at half
    past two" sentence is usually already gone. All three mean one thing to a
    shopkeeper — sign in again — and `ui/src/routes/SignIn.tsx` treats them as
    one for that reason.
    """
    clock = _Clock()
    auth.set_clock(clock)
    try:
        _owner(till)
        monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
        assert till.get("/shop").status_code == 200
        clock.forward(auth.SESSION_HOURS * 3600 + 60)
        r = till.get("/shop")
        assert r.status_code == 401
        assert r.json()["reason"] == auth.R_NOT_SIGNED_IN
        me = till.get("/auth/me")
        assert me.status_code == 401
        assert me.json()["reason"] in (auth.R_SESSION_EXPIRED,
                                       auth.R_SESSION_UNKNOWN,
                                       auth.R_NO_SESSION)
        # And the status endpoint, which the page asks first, agrees.
        assert till.get("/auth/status").json()["signed_in"] is False
    finally:
        auth.set_clock(None)


class _Clock:
    """A clock a test can push forward. Whole seconds — never a float."""

    def __init__(self, at: int = 1_800_000_000) -> None:
        self.at = int(at)

    def __call__(self) -> int:
        return self.at

    def forward(self, seconds: int) -> None:
        self.at += int(seconds)


# ==========================================================================
# 5. `enforced` REPORTS THE WIRING
# ==========================================================================


def test_enforced_is_false_when_the_switch_is_on_and_nothing_is_guarded(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """THE ORIGINAL LIE, reproduced against the old condition.

    Switch on, guard applied to nothing: the environment variable says locked
    and the counter is wide open. `enforced` must say what is true.
    """
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
    upload_app.set_store_dir(tmp_path / "shop")

    app = FastAPI()
    naked = APIRouter()

    @naked.get("/till/naked")
    def _naked():
        return {"ok": True}

    app.include_router(naked)          # deliberately no dependencies=
    auth.install(app)
    c = TestClient(app)

    assert auth.auth_required() is True
    assert c.get("/till/naked").status_code == 200
    body = c.get("/auth/status").json()
    assert body["switch_on"] is True
    assert body["guard_applied"] is False
    assert body["enforced"] is False, "a lock that is not fitted is not a lock"
    assert body["lock"]["unguarded_paths"] == ["/till/naked"]
    assert "NOT locked" in body["note"]
    assert auth.enforced_on(app) is False


def test_enforced_is_true_only_when_the_switch_and_the_wiring_agree(
        till: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    off = till.get("/auth/status").json()
    assert off["switch_on"] is False
    assert off["guard_applied"] is True      # wired, and waiting
    assert off["enforced"] is False
    assert off["lock"]["unguarded_routes"] == 0

    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
    on = till.get("/auth/status").json()
    assert on["switch_on"] is True
    assert on["guard_applied"] is True
    assert on["enforced"] is True
    assert auth.enforced_on(upload_app.app) is True


def test_the_status_readout_names_what_this_deployment_leaves_open(
        till: TestClient) -> None:
    """Read off the mounted guard, not off a constant — a readout copied from a
    constant is how `enforced` came to disagree with the thing it described."""
    body = till.get("/auth/status").json()
    assert body["open_here"]["paths"] == ["/", "/health"]
    assert body["open_here"]["prefixes"] == ["/qr/link", "/receipt", "/store"]


def test_the_status_screen_still_names_no_person(till: TestClient) -> None:
    """The new fields must not have put a phone number or a name in the one
    endpoint that answers before anybody has signed in."""
    _owner(till)
    _signed_out(till)
    text = till.get("/auth/status").text
    assert OWNER["name"] not in text
    assert OWNER["phone"] not in text
    assert OWNER["password"] not in text


def test_signin_and_me_report_the_same_enforced_as_status(
        till: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Three endpoints answered `enforced` off the environment variable. All
    three now answer off the wiring, and they must not disagree."""
    _owner(till)
    _signed_out(till)
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
    signin = till.post("/auth/signin",
                       json={"phone": OWNER["phone"],
                             "password": OWNER["password"]}).json()
    me = till.get("/auth/me").json()
    status = till.get("/auth/status").json()
    assert signin["enforced"] is me["enforced"] is status["enforced"] is True


# ==========================================================================
# 6. THE GUARD ITSELF, AS A MECHANISM
# ==========================================================================


def test_depends_open_leaves_only_what_it_was_given_open(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
    upload_app.set_store_dir(tmp_path / "shop")

    app = FastAPI()
    guard = auth.depends_open(paths=("/ping",), prefixes=("/open",))
    r = APIRouter()

    for p in ("/ping", "/pinger", "/open", "/open/deep", "/opener", "/shut"):
        r.add_api_route(p, (lambda: {"ok": True}), methods=["GET"])
    app.include_router(r, dependencies=guard)
    auth.install(app)
    c = TestClient(app)
    c.post("/auth/signup", json=OWNER)
    c.post("/auth/signout")

    assert c.get("/ping").status_code == 200
    assert c.get("/open").status_code == 200
    assert c.get("/open/deep").status_code == 200
    # A prefix is a path prefix, not a string prefix.
    assert c.get("/pinger").status_code == 401
    assert c.get("/opener").status_code == 401
    assert c.get("/shut").status_code == 401


def test_the_environment_can_still_open_something_nobody_thought_of(
        till: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """`GAWAAH_AUTH_OPEN` adds to the deployment's list rather than replacing
    it — an operator must be able to open a path without a deploy."""
    _owner(till)
    _signed_out(till)
    monkeypatch.setenv("GAWAAH_REQUIRE_AUTH", "1")
    assert till.get("/stock").status_code == 401
    monkeypatch.setenv("GAWAAH_AUTH_OPEN", "/stock")
    assert till.get("/stock").status_code == 200
    assert till.get("/store").status_code == 200      # still open
    assert till.get("/shop").status_code == 401       # still shut


def test_the_guard_on_the_real_till_records_who_without_locking_anything(
        till: TestClient) -> None:
    """With the lock OFF the guard is not inert-and-useless, it is
    inert-and-recording: `request.state.shopkeeper` is filled in on every
    guarded route today, so a route can attribute an action before enforcement
    is ever switched on."""
    from starlette.requests import Request as _Req

    seen: list = []
    real = auth.current_shopkeeper

    def _spy(request: _Req):
        who = real(request)
        seen.append((request.url.path, (who or {}).get("account_id")))
        return who

    assert till.post("/auth/signup", json=OWNER).status_code == 200
    auth.current_shopkeeper = _spy               # type: ignore[assignment]
    try:
        assert till.get("/shop").status_code == 200
    finally:
        auth.current_shopkeeper = real           # type: ignore[assignment]
    assert seen, "the guard did not run on /shop"
    assert seen[0][0] == "/shop"
    assert seen[0][1] is not None, "signed in, and the guard did not see it"


def test_no_route_in_the_till_settles_money_through_the_guard() -> None:
    """The guard is on the money routes too, and its refusal says so.

    A refusal body that omitted `settles_money` would be the one shape in this
    program a caller could mistake for a result.
    """
    body = auth._body(auth.R_NOT_SIGNED_IN, "x")
    assert body["settles_money"] is False
    assert isinstance(body["ok"], bool) and body["ok"] is False
