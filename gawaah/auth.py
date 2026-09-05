"""TAALA — the lock on the counter door.

Until now anything that could reach the till on the shop's wifi could bill,
teach, re-price and mint. That is fine on a laptop in a locked room and wrong
in a shop with a phone-shaped guest on the same router. This module gives the
counter accounts, sign-in, and a session — and NOTHING ELSE IN THIS PROGRAM IS
CHANGED BY IT.

READ THIS BEFORE YOU TURN ANYTHING ON. The lock ships FITTED AND OPEN. Every
route in the till, the storefront and the back office is exactly as reachable
after this module is mounted as it was before. What is shipped is the
mechanism:

  - `GAWAAH_REQUIRE_AUTH` — the switch. UNSET AND ANYTHING BUT A TRUTHY VALUE
    MEANS OFF, which is the default, which is what a demo and twenty parallel
    agents need. Nothing in this file turns it on for you.
  - `require_shopkeeper` — a FastAPI dependency. With the switch off it lets
    every request through and merely records who was signed in, if anyone. With
    the switch on it refuses by name.
  - `DEPENDS` — that dependency, pre-wrapped, so a router is protected with
    `app.include_router(x.router, dependencies=auth.DEPENDS)` and unprotected
    by deleting eleven characters.
  - `depends_open(paths, prefixes)` — the same guard, carrying THIS
    deployment's list of what stays reachable without a session. A shop has one
    server and two audiences, and the customer's half (`/store`, the receipt
    link, the payment QR) sits in the same routers as the shopkeeper's; a list
    passed in where the routers are mounted is the only place that separation
    can be both correct and readable. `tools/upload_app.py` is the caller.
  - `guard_coverage(app)` / `enforced_on(app)` — a walk of the live route tree
    that answers "is the guard actually attached", which is what `/auth/status`
    now reports. It once reported the environment variable instead, while the
    guard was attached to nothing, and said `enforced: true` about a lock that
    did not exist.

Four things this file is careful about, in the order they would hurt:

  1. THE PASSWORD IS NEVER STORED AND NEVER LEAVES. `hashlib.scrypt` — standard
     library, no new dependency — with a fresh 16-byte random salt per account.
     What lands on disk is the salt, the derived key, and the cost parameters
     that produced it. There is no reversible form anywhere and no code path
     that reads a password back.

  2. THE TOKEN IS NEVER IN A BODY AND NEVER IN THE LOG. A sign-in answers with
     a `Set-Cookie` and nothing else: the session token appears in the response
     HEADERS, never in the JSON, so it cannot be pasted into a bug report,
     screenshotted off a network tab, or dropped into `localStorage` by a page
     that meant well. On disk we keep only `sha256(token)`, so a stolen
     `auth_sessions.json` is not a set of working keys.

  3. A REFUSAL IS A RESULT. Every failure this module can reach has a name and
     the name is in the body, in this program's usual shape. Nothing here
     raises a 500. THE ONE DEPARTURE FROM THE HOUSE 400: the three "you are not
     signed in" refusals answer 401 and the rate limit answers 429, because a
     page has to tell "your session ran out" from "you sent nonsense" before it
     has parsed anything, and a browser's own retry and cache behaviour keys off
     the status. The BODY is identical to every other refusal in this repo. If
     that is the wrong call, `_STATUS_BY_REASON` below is one dict and one line
     changes it back.

  4. THE ROLES ARE A RECORD, NOT A PERMISSION SYSTEM. The first account is
     `owner` and an invited one is `staff`, and NOTHING IN THIS PROGRAM READS
     THAT FIELD. It is written so that the day someone builds authorisation
     they have the history to build it from. Do not mistake it for a fence.

The router carries NO prefix: the paths below are absolute and are what a
browser types. Mount it with `app.include_router(auth.router)` and then call
`auth.install(app)` so a guard refusal comes out in this program's own shape.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .ledger import Ledger

router = APIRouter()


# --------------------------------------------------------------- refusals --
#
# Every one of these is a state this module can actually reach, and every one
# has a test. None is a guess and none is decoration.

#: OPEN SIGN-UP, AND THE ONE PLACE IT BELONGS.
#:
#: By default this counter lets exactly one account be opened without an
#: invitation -- the first, because somebody has to be first -- and every
#: account after that needs a code from a person already signed in. That is
#: correct for a shop: the till holds the catalogue, the books, the customers'
#: numbers and the till's own money screens, and a stranger who finds the URL
#: should not be able to make themselves a shopkeeper.
#:
#: A PUBLIC DEMONSTRATION IS THE EXCEPTION, AND IT IS A REAL ONE. A counter
#: nobody can create an account on is a counter a reviewer can only look at
#: through a screenshot. So the door can be propped open -- deliberately, by
#: whoever deploys, with a value they had to type out in full:
#:
#:     GAWAAH_OPEN_SIGNUP=yes-i-mean-it
#:
#: Same shape as GAWAAH_ALLOW_LIVE_KEYS in gawaah/rzp_live.py and for the same
#: reason: a switch that turns a safety off should cost more than a `1`, and
#: should be greppable in a deployment's config as an obvious decision. Any
#: other value, including "true" and "1", leaves the invitation gate shut.
#:
#: What it does NOT do: weaken anything else. Passwords are still scrypt-hashed
#: and still refused if they are short or are the phone number, the phone is
#: still unique, and every route still needs a session.
OPEN_SIGNUP_ENV = "GAWAAH_OPEN_SIGNUP"
OPEN_SIGNUP_VALUE = "yes-i-mean-it"


def signup_is_open() -> bool:
    """Whether a stranger may open an account with no invitation. Read fresh
    every time, so an operator who sets it does not have to restart."""
    return (os.environ.get(OPEN_SIGNUP_ENV) or "").strip() == OPEN_SIGNUP_VALUE


R_BAD_BODY = "auth_body_not_json"
R_BAD_FIELD = "auth_field_not_text"
R_NO_NAME = "auth_name_missing"
R_NAME_TOO_LONG = "auth_name_too_long"
R_NO_PHONE = "auth_phone_missing"
R_BAD_PHONE = "auth_phone_not_a_number"
R_PHONE_TOO_LONG = "auth_phone_too_long"
R_PHONE_TAKEN = "auth_phone_already_has_an_account"
R_NO_PASSWORD = "auth_password_missing"
R_PASSWORD_SHORT = "auth_password_too_short"
R_PASSWORD_LONG = "auth_password_too_long"
R_PASSWORD_IS_PHONE = "auth_password_is_the_phone_number"
R_SIGNUP_CLOSED = "auth_signup_needs_an_invite"
R_INVITE_UNKNOWN = "auth_invite_not_from_this_shop"
R_INVITE_USED = "auth_invite_already_used"
R_INVITE_EXPIRED = "auth_invite_expired"
R_BAD_CREDENTIALS = "auth_phone_or_password_wrong"
R_TOO_MANY_ATTEMPTS = "auth_too_many_sign_in_attempts"
R_NO_SESSION = "auth_no_session_presented"
R_SESSION_UNKNOWN = "auth_session_not_known_here"
R_SESSION_EXPIRED = "auth_session_expired"
R_NOT_SIGNED_IN = "auth_sign_in_required"
R_NO_ACCOUNT_YET = "auth_no_account_exists_yet"
R_STORE_UNREADABLE = "auth_store_unreadable"
R_INTERNAL = "auth_internal_error"

#: The status each refusal answers with. Anything absent answers 400, which is
#: what every other endpoint in this program does. See point 3 of the module
#: docstring for why these seven are not 400; changing this dict is the whole
#: change if that judgement is overruled.
_STATUS_BY_REASON: dict[str, int] = {
    R_BAD_CREDENTIALS: 401,
    R_NO_SESSION: 401,
    R_SESSION_UNKNOWN: 401,
    R_SESSION_EXPIRED: 401,
    R_NOT_SIGNED_IN: 401,
    R_NO_ACCOUNT_YET: 401,
    R_TOO_MANY_ATTEMPTS: 429,
}


class AuthRefused(Exception):
    """A named refusal with a reason a shopkeeper can act on."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail

    @property
    def status(self) -> int:
        return _STATUS_BY_REASON.get(self.reason, 400)


class AuthHTTPRefusal(HTTPException):
    """What the DEPENDENCY raises, so a guarded route never becomes a 500.

    A FastAPI dependency cannot return a response; it can only raise. A plain
    exception out of a dependency is a 500, and this program does not have
    those. So the guard raises an `HTTPException` subclass whose `detail` IS
    the refusal body: with `install(app)` called the body is flat and exactly
    like every other refusal here, and WITHOUT it Starlette's own handler still
    answers the right status with every field present one level down under
    `detail`. Forgetting to call `install` costs you a nested body, not a
    crash.
    """

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.why = detail
        super().__init__(status_code=_STATUS_BY_REASON.get(reason, 400),
                         detail=_body(reason, detail))


def _body(reason: str, detail: str) -> dict[str, Any]:
    return {"ok": False, "reason": reason, "detail": detail,
            "settles_money": False}


def _refusal(exc: AuthRefused) -> JSONResponse:
    """The shape every other endpoint in this program answers a refusal with."""
    return JSONResponse(_body(exc.reason, exc.detail), status_code=exc.status)


def _crash(exc: BaseException) -> JSONResponse:
    """The last resort. A 500 teaches the reader nothing, so there are none.

    Deliberately does NOT include the exception's own arguments beyond its type
    and message: everything in this module that carries a secret carries it in
    a local, and an exception repr is the classic way a password reaches a log.
    `type(exc).__name__` and `str(exc)` are what every other module here
    prints, and nothing in this file ever puts a password or a token into an
    exception message.
    """
    return JSONResponse(
        _body(R_INTERNAL, f"{type(exc).__name__}: {exc}"), status_code=400)


# ------------------------------------------------------------- the tunables --

#: scrypt cost. n=2**15, r=8, p=1 is 32 MB and about 50 ms on the laptop this
#: counter runs on — measured, not guessed. That is a deliberate middle: OWASP
#: would rather see n=2**17 (128 MB, ~200 ms), and a kirana till that takes a
#: fifth of a second to open in the morning is a till a shopkeeper stops
#: locking. THE COST IS STORED ON EACH ACCOUNT, so raising it later re-hashes
#: on the next sign-in instead of invalidating everyone.
SCRYPT_N = 1 << 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_SALT_BYTES = 16
#: OpenSSL refuses a scrypt call that would exceed its memory bound, and its
#: default (32 MB) is exactly the working set of the parameters above, so it
#: has to be raised explicitly or every hash fails on some builds.
SCRYPT_MAXMEM = 128 * 1024 * 1024

#: Bounds a *stored* cost is allowed to claim. An accounts file is a file, and
#: a file can be edited; without this, `n = 2**30` in a JSON document is a
#: memory bomb that a stranger's sign-in attempt detonates.
SCRYPT_N_MIN, SCRYPT_N_MAX = 1 << 12, 1 << 20
SCRYPT_R_MAX, SCRYPT_P_MAX = 32, 16
SCRYPT_DKLEN_MIN, SCRYPT_DKLEN_MAX = 16, 64

MIN_PASSWORD = 8
#: A cap, because scrypt over an unbounded string is a denial of service that
#: costs the sender one request. 256 characters is longer than any passphrase
#: anybody types at a counter.
MAX_PASSWORD = 256
MAX_NAME = 80
MAX_PHONE = 24
MIN_PHONE_DIGITS = 7
#: E.164 tops out at fifteen digits.
MAX_PHONE_DIGITS = 15

SESSION_HOURS = 12
SESSION_COOKIE = "gawaah_session"
#: Bounds the sessions file. A shopkeeper who signs in from the till, a phone
#: and a tablet has three; two dozen means something is looping, and the oldest
#: goes rather than the file growing without end.
MAX_SESSIONS_PER_ACCOUNT = 24

INVITE_HOURS = 48
INVITE_PREFIX = "inv_"
ACCOUNT_PREFIX = "acct_"

#: The rate limit. Five wrong passwords for one phone inside five minutes and
#: that phone cannot try again for five minutes.
#:
#: WHAT THIS COSTS, SAID OUT LOUD: anybody who can reach the till can lock a
#: shopkeeper out of sign-in for five minutes by guessing wrong five times.
#: There is no version of a per-account rate limit without that property. The
#: alternative is unlimited guessing against a counter that holds a payment
#: gateway's session, and that is worse.
MAX_ATTEMPTS = 5
ATTEMPT_WINDOW_S = 300
LOCK_S = 300
#: Bounds the in-memory table against somebody cycling through phone numbers.
MAX_TRACKED_PHONES = 4096

FORMAT = 1

_LOCK = threading.RLock()


# ---------------------------------------------------------------- the clock --
#
# A seam, not a feature. Session expiry and invite expiry are the two things
# here that cannot be tested at all without moving time, and sleeping for
# twelve hours is not a test. `gawaah/clock.py` exists in this repo for the
# same reason.

_CLOCK: dict[str, Optional[Callable[[], int]]] = {"now": None}


def _now() -> int:
    """Whole seconds since the epoch. Never a float — see invariant 1."""
    fn = _CLOCK["now"]
    return int(fn()) if fn is not None else int(time.time())


def set_clock(fn: Optional[Callable[[], int]]) -> None:
    """Point this module at another clock. For tests; None restores the real one."""
    _CLOCK["now"] = fn


def _iso(epoch_s: int) -> str:
    return _dt.datetime.fromtimestamp(
        int(epoch_s), _dt.timezone.utc).isoformat()


def _now_iso() -> str:
    return _iso(_now())


# ------------------------------------------------------------- the switch --


def _truthy(raw: Optional[str]) -> bool:
    """Only these five words are on. EVERYTHING ELSE, INCLUDING UNSET, IS OFF.

    Deliberately not `bool(os.environ.get(...))`, which makes the string "0"
    and the string "false" both mean yes. A switch that locks a live counter
    has to fail towards open, and a typo has to be a typo.
    """
    return (raw or "").strip().lower() in ("1", "true", "yes", "on", "y")


def auth_required() -> bool:
    """Is enforcement on? Read every time, so a test can set it per-test.

    THIS IS FALSE UNLESS SOMEBODY TYPED `GAWAAH_REQUIRE_AUTH=1`. Nothing in
    this repository sets it, and turning it on mid-hackathon breaks every other
    screen at once — which is the point of it being a switch and not a default.
    """
    return _truthy(os.environ.get("GAWAAH_REQUIRE_AUTH"))


#: Routes that stay reachable even with the switch on, because locking them
#: would lock the door with the key inside: you cannot sign in through a guard
#: that requires you to be signed in.
OPEN_PATHS = frozenset({
    "/auth/signup", "/auth/signin", "/auth/signout", "/auth/me", "/auth/status",
    # The name over the door. The sign-in screen draws it, and that screen is
    # by definition reached without a session — a locked counter that cannot
    # say whose counter it is tells its own owner it has no name. GET-only and
    # two fields; `/shop/profile`, which also takes a PUT, stays guarded.
    "/shop/nameplate",
    # The header a CUSTOMER'S phone reads after scanning the shutter, and the
    # shop's picture beside it. Both are `gawaah/shopface.py`, both GET-only,
    # and neither carries more than what is painted on the shutter: slug,
    # name, address, photo. The one thing `/store/shop` adds is whether the
    # slug the phone arrived with is THIS shop's — the answer a stranger needs
    # most and the one a locked counter must still give. Everything that
    # writes (`PUT /shop/photo`, `POST /shop/link/renew`) stays guarded.
    "/store/shop",
    "/store/shop/photo",
})


def _open_prefixes() -> tuple[str, ...]:
    """Extra path prefixes to leave open, from `GAWAAH_AUTH_OPEN`.

    THE CUSTOMER'S SIDE OF THE SHOP IS NOT OPEN BY DEFAULT AND THAT IS ON
    PURPOSE. `gawaah/storefront.py` serves a stranger's phone: if the guard is
    ever applied app-wide, `/store` has to be listed here or the shutter QR
    stops working for everyone who is not the shopkeeper. That is a policy call
    for whoever turns the switch on, so this module makes it one line —
    `GAWAAH_AUTH_OPEN=/store` — rather than deciding it quietly here.
    """
    raw = os.environ.get("GAWAAH_AUTH_OPEN") or ""
    return tuple(p.strip() for p in raw.split(",") if p.strip())


#: `/auth/invite` is NOT in OPEN_PATHS and must never be. It guards itself with
#: `require_shopkeeper_always` — the one route in this program that needs a
#: session whether or not the switch is on. It is named here so the coverage
#: report below can tell "deliberately not carrying the guard" from "somebody
#: forgot", which are the two states a wiring check exists to separate.
SELF_GUARDED = frozenset({"/auth/invite"})


def _matches(path: str, paths: frozenset[str] | set[str] | tuple[str, ...],
             prefixes: tuple[str, ...]) -> bool:
    """Exact match against `paths`, or segment-boundary match against `prefixes`.

    The boundary is the whole point: `/store` opens `/store/order/x` and must
    not open `/storeroom`. A plain `startswith` would open both, and the shop's
    stockroom is not the shop's front door.
    """
    p = path or "/"
    if p in paths:
        return True
    for pre in prefixes:
        if not pre:
            continue
        if p == pre or p.startswith(pre if pre.endswith("/") else pre + "/"):
            return True
    return False


def is_open_path(path: str) -> bool:
    """Does this path stay reachable without a session?

    THIS IS AUTH'S OWN ANSWER AND IT IS DELIBERATELY NARROW: the five routes
    that are the way back in, plus whatever the operator named in
    `GAWAAH_AUTH_OPEN`. It does NOT know about the storefront, the receipt link
    or the payment QR — those are a *deployment's* policy, not this module's,
    and they are declared where the routers are mounted (see `guard_open` and
    `tools/upload_app.py`). Keeping this function ignorant of them is what lets
    an auth-only app, a test app and the till each have a different front door
    without any of them editing this file.
    """
    return _matches(path, OPEN_PATHS, _open_prefixes())


# ---------------------------------------------------------------- the till --
#
# Imported LATE and out of sys.modules FIRST, for the reason `storefront.py`
# spells out at length: `make serve` registers the till as `upload_app` and the
# test suite registers it as `tools.upload_app`, and importing the other
# spelling loads a SECOND copy of the file with its own store handle — so a
# test that redirected the shop would be writing accounts into a directory
# nobody is serving, with nothing anywhere saying so.

from gawaah import till_ref as _till_ref

#: One definition, in `gawaah/till_ref.py`. This was sixteen copies of a tuple
#: that was missing `__main__`, and every one of them was wrong the same way.
_TILL_NAMES = _till_ref.TILL_NAMES

#: Remembers that the import was ATTEMPTED, so a deployment without the till
#: does not re-walk the import machinery on every sign-in. A success needs no
#: cache — it lands in sys.modules, which is checked first and is the answer.
_TILL_CACHE: dict[str, bool] = {"tried": False}


def _till() -> Optional[Any]:
    import sys

    for name in _TILL_NAMES:
        mod = sys.modules.get(name)
        if mod is not None and _till_ref.is_the_till(mod):
            return mod
    if _TILL_CACHE["tried"]:
        return None
    _TILL_CACHE["tried"] = True
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from tools import upload_app  # noqa: WPS433 - deliberately late
    except Exception:  # noqa: BLE001 - see shop_dir; a missing till is survivable
        return None
    return upload_app


def shop_dir() -> Path:
    """Where the accounts live — the till's own answer, never a second one.

    This is what honours `GAWAAH_SHOP_DIR`: `upload_app.store_dir()` reads that
    variable and `upload_app.set_store_dir()` redirects it for a test. Asking
    the till rather than the environment is not politeness — a harness that
    redirected the catalogue with `set_store_dir` alone and left this file
    behind would write accounts into the LIVE shop directory, and there is no
    undo for that.

    UNLIKE `storefront.py`, A MISSING TILL IS NOT FATAL HERE. The storefront
    cannot price a basket without the catalogue; sign-in does not need a
    catalogue at all, only somewhere to keep a file. So if the till is not
    importable — an auth-only test app, a cut-down deployment — we fall back to
    the environment variable and then to the same default path the till itself
    would compute. The fallback cannot silently miss a `set_store_dir`,
    because a process where the till never imported never called it.
    """
    up = _till()
    if up is not None:
        try:
            return Path(up.store_dir())
        except Exception:  # noqa: BLE001 - fall through to the plain answer
            pass
    return Path(os.environ.get(
        "GAWAAH_SHOP_DIR",
        str(Path(__file__).resolve().parent.parent / "results" / "shop")))


def accounts_path() -> Path:
    """Accounts and invitations: who may use this counter."""
    return shop_dir() / "auth_accounts.json"


def sessions_path() -> Path:
    """Live sessions. Separate from the accounts because it churns and they do
    not, and one bad write should not be able to take both."""
    return shop_dir() / "auth_sessions.json"


def audit_path() -> Path:
    """This module's own hash-chained log.

    DELIBERATELY NOT `results/audit.jsonl`. The money service holds that file
    open in a DIFFERENT PROCESS and computes `prev_hash` from a head it keeps
    in memory; a second process appending between two of its writes hands it a
    stale head and every line paisa writes afterwards fails `verify`. Somebody
    signing in must not be able to break the money audit trail.
    `storefront.py` and `shopadmin.py` made the same call and document the
    trade: there are now four chains to walk instead of one, and a reader who
    checks only `results/audit.jsonl` will not see sign-ins.
    """
    return shop_dir() / "auth.audit.jsonl"


def _audit(event: str, **fields: Any) -> Optional[str]:
    """Append one auditable line. Returns the new head, or None if it failed.

    NO PASSWORD, NO HASH, NO SALT, NO TOKEN AND NO NAME REACHES THIS FILE, and
    `tests/test_auth.py` asserts it against the bytes on disk. An audit log is
    the file most likely to be pasted into a bug report.

    A sign-in is not money and not stock, so this chain is a record rather than
    an obligation: a failed append must not stop a shopkeeper opening the till
    in the morning. The caller is told (`audited: false`) rather than the
    failure being swallowed.
    """
    try:
        return Ledger(audit_path()).append(
            ts=_now_iso(), module="auth", event=event, minted=False, **fields)
    except Exception:  # noqa: BLE001 - a failed audit must not lock the door
        return None


def _subject(digits: str) -> str:
    """A stable handle for a phone number, for the audit chain.

    NOT A SECRET, and calling it one would be a lie: a ten-digit space is
    trivially enumerable, so anybody with this hash and an afternoon has the
    number back. It exists so the chain reads `subject: 8f3a…` instead of a
    customer's phone number when a shopkeeper mails a log to whoever is
    helping — and so two lines about the same person can still be tied
    together.
    """
    return hashlib.sha256(digits.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------ reading input --


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a named refusal
        raise AuthRefused(
            R_BAD_BODY, "this request's body is not JSON.") from None
    if not isinstance(body, dict):
        raise AuthRefused(
            R_BAD_BODY,
            f"this request's body is a {type(body).__name__}; it must be a "
            f"JSON object.")
    return body


def _text(body: dict[str, Any], key: str) -> str:
    """One text field, whitespace-collapsed. NEVER USED FOR A PASSWORD.

    A password is read by `_password` instead, which never puts the value into
    a message — this function names the type it was given, and one day somebody
    would send `{"password": 12345}` and find the value in a refusal string.
    """
    raw = body.get(key)
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise AuthRefused(
            R_BAD_FIELD, f"{key!r} must be text, not {type(raw).__name__}.")
    return " ".join(raw.split())


def normalise_phone(raw: str) -> str:
    """The digits a phone number comes down to, as this shop files them.

    An account is keyed on this, not on what was typed, so `+91 98765 43210`
    and `9876543210` are the same shopkeeper rather than two accounts one
    person cannot tell apart.

    A STATED LIMIT, BECAUSE THIS IS A HEURISTIC AND NOT A PHONE LIBRARY: a
    leading `91` on a twelve-digit number and a leading `0` on an eleven-digit
    one are dropped, which is right for India and wrong for a country whose
    numbers happen to look like that. This counter is a kirana counter. A
    number that is neither shape is filed exactly as its digits came.
    """
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits


def _phone(body: dict[str, Any]) -> tuple[str, str]:
    """(as typed, as filed). Both, because the shopkeeper should see their own."""
    typed = _text(body, "phone")
    if not typed:
        raise AuthRefused(
            R_NO_PHONE,
            "a phone number is required — it is how this counter tells one "
            "person from another.")
    if len(typed) > MAX_PHONE:
        raise AuthRefused(
            R_PHONE_TOO_LONG,
            f"that phone number is {len(typed)} characters and the cap is "
            f"{MAX_PHONE}. Nothing was saved.")
    digits = normalise_phone(typed)
    if len(digits) < MIN_PHONE_DIGITS or len(digits) > MAX_PHONE_DIGITS:
        raise AuthRefused(
            R_BAD_PHONE,
            f"{typed!r} has {len(digits)} digits in it. A number that can be "
            f"dialled has between {MIN_PHONE_DIGITS} and {MAX_PHONE_DIGITS}.")
    return typed, digits


def _password(body: dict[str, Any]) -> str:
    """The password, checked for shape and NEVER echoed.

    Every refusal below describes the password by a property — its length, or
    that it equals the phone number — and never by its value. That is the whole
    reason this is not `_text(body, "password")`: a refusal string is a thing
    that gets logged, screenshotted and pasted.
    """
    raw = body.get("password")
    if raw is None or raw == "":
        raise AuthRefused(
            R_NO_PASSWORD, "a password is required. Nothing was saved.")
    if not isinstance(raw, str):
        raise AuthRefused(
            R_BAD_FIELD,
            f"'password' must be text, not {type(raw).__name__}.")
    # NOT whitespace-collapsed and not stripped: a space inside a passphrase is
    # part of it, and quietly editing what somebody typed means they cannot
    # sign in with the thing they think they set.
    if len(raw) < MIN_PASSWORD:
        raise AuthRefused(
            R_PASSWORD_SHORT,
            f"that password is {len(raw)} characters. The shortest this "
            f"counter accepts is {MIN_PASSWORD}.")
    if len(raw) > MAX_PASSWORD:
        raise AuthRefused(
            R_PASSWORD_LONG,
            f"that password is {len(raw)} characters and the cap is "
            f"{MAX_PASSWORD}.")
    return raw


def _name(body: dict[str, Any]) -> str:
    name = _text(body, "name")
    if not name:
        raise AuthRefused(
            R_NO_NAME,
            "a name is required — the audit log should say who did a thing, "
            "not which phone number did it.")
    if len(name) > MAX_NAME:
        raise AuthRefused(
            R_NAME_TOO_LONG,
            f"that name is {len(name)} characters and the cap is {MAX_NAME}.")
    return name


# ------------------------------------------------------------ the hashing --


def _derive(password: str, salt: bytes, *, n: int = SCRYPT_N, r: int = SCRYPT_R,
            p: int = SCRYPT_P, dklen: int = SCRYPT_DKLEN) -> bytes:
    """scrypt. The only thing in this program that ever sees a password.

    Standard library, so this feature adds no dependency to a repo that has to
    install on a laptop the morning of a demo.
    """
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                          dklen=dklen, maxmem=SCRYPT_MAXMEM)


#: A fixed salt used only to burn the same ~50 ms on a phone number that has no
#: account as on one that does. Without it, "unknown phone" answers in a
#: millisecond and "wrong password" in fifty, and the difference is a free
#: oracle for which of a shop's staff exist. It is a constant on purpose:
#: nothing is ever verified against it.
_TIMING_SALT = b"gawaah-timing-equaliser-not-a-secret"[:SCRYPT_SALT_BYTES]


def _burn_the_same_time() -> None:
    """Cost a caller the same as a real verification would. Result discarded."""
    try:
        _derive("x" * MIN_PASSWORD, _TIMING_SALT)
    except Exception:  # noqa: BLE001 - a timing measure must never be the error
        pass


def _checked_cost(rec: dict[str, Any]) -> dict[str, int]:
    """The cost parameters off an account record, bounded before they are used.

    See SCRYPT_N_MIN: these numbers come off disk, and a JSON file is editable
    by anything that can write to the shop directory. Unbounded, `n` is an
    allocation somebody else's sign-in attempt performs.
    """
    try:
        n = int(rec.get("n", SCRYPT_N))
        r = int(rec.get("r", SCRYPT_R))
        p = int(rec.get("p", SCRYPT_P))
        dklen = int(rec.get("dklen", SCRYPT_DKLEN))
    except Exception:  # noqa: BLE001 - a non-numeric cost is a corrupt store
        raise AuthRefused(
            R_STORE_UNREADABLE,
            "this account's stored hashing cost is not a number, so the "
            "password cannot be checked. Nothing was changed.") from None
    if not (SCRYPT_N_MIN <= n <= SCRYPT_N_MAX) or (n & (n - 1)) != 0:
        raise AuthRefused(
            R_STORE_UNREADABLE,
            f"this account claims a scrypt cost of n={n}, which is outside "
            f"what this counter will spend memory on. Nothing was changed.")
    if not (1 <= r <= SCRYPT_R_MAX) or not (1 <= p <= SCRYPT_P_MAX):
        raise AuthRefused(
            R_STORE_UNREADABLE,
            f"this account claims scrypt r={r}, p={p}, which is outside what "
            f"this counter will spend memory on. Nothing was changed.")
    if not (SCRYPT_DKLEN_MIN <= dklen <= SCRYPT_DKLEN_MAX):
        raise AuthRefused(
            R_STORE_UNREADABLE,
            f"this account claims a {dklen}-byte derived key, which this "
            f"counter does not recognise. Nothing was changed.")
    return {"n": n, "r": r, "p": p, "dklen": dklen}


def _verify_password(password: str, rec: dict[str, Any]) -> bool:
    """Constant-time compare of a fresh derivation against the stored one."""
    cost = _checked_cost(rec)
    try:
        salt = bytes.fromhex(str(rec.get("salt_hex") or ""))
        want = bytes.fromhex(str(rec.get("hash_hex") or ""))
    except ValueError:
        raise AuthRefused(
            R_STORE_UNREADABLE,
            "this account's stored salt or key is not hexadecimal, so the "
            "password cannot be checked. Nothing was changed.") from None
    if not salt or not want:
        raise AuthRefused(
            R_STORE_UNREADABLE,
            "this account has no stored salt or key. It cannot be signed in "
            "to, and it cannot be repaired from here.")
    got = _derive(password, salt, **cost)
    # hmac.compare_digest and not ==: the comparison of a derived key is the
    # one place left where a byte-at-a-time answer leaks the key.
    return hmac.compare_digest(got, want)


# ------------------------------------------------------------- the storage --


def _read_doc(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return json.loads(json.dumps(default))
    except Exception as exc:  # noqa: BLE001 - unreadable is a named refusal
        raise AuthRefused(
            R_STORE_UNREADABLE,
            f"{path.name} is on disk but could not be read "
            f"({type(exc).__name__}). Nothing was changed.") from None
    try:
        doc = json.loads(raw)
    except Exception:  # noqa: BLE001
        raise AuthRefused(
            R_STORE_UNREADABLE,
            f"{path.name} is not valid JSON. Nothing was changed, and nothing "
            f"here will overwrite it — repair or remove it by hand so an "
            f"account is never lost to a parser.") from None
    if not isinstance(doc, dict):
        raise AuthRefused(
            R_STORE_UNREADABLE,
            f"{path.name} is a {type(doc).__name__}, not an object. Nothing "
            f"was changed.")
    return doc


def _write_doc(path: Path, doc: dict[str, Any]) -> None:
    """Temp file then rename, so a reader never sees half an accounts file.

    Mode 0600 on both the temp file and the result. It is not much — anything
    running as this user can still read it — but a shop directory gets copied
    to a USB stick, and the salted keys should not be world-readable when it
    does.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, sort_keys=True, indent=1) + "\n",
                   encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:  # noqa: PERF203 - a filesystem without modes is not fatal
        pass
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _load_accounts() -> dict[str, Any]:
    doc = _read_doc(accounts_path(),
                    {"format": FORMAT, "accounts": {}, "invites": {}})
    if not isinstance(doc.get("accounts"), dict):
        doc["accounts"] = {}
    if not isinstance(doc.get("invites"), dict):
        doc["invites"] = {}
    return doc


def _save_accounts(doc: dict[str, Any]) -> None:
    doc["format"] = FORMAT
    _write_doc(accounts_path(), doc)


def _load_sessions() -> dict[str, Any]:
    doc = _read_doc(sessions_path(), {"format": FORMAT, "sessions": {}})
    if not isinstance(doc.get("sessions"), dict):
        doc["sessions"] = {}
    return doc


def _save_sessions(doc: dict[str, Any]) -> None:
    doc["format"] = FORMAT
    _write_doc(sessions_path(), doc)


def account_count() -> int:
    """How many accounts exist. Zero means sign-up is open to the first person."""
    try:
        return len(_load_accounts()["accounts"])
    except AuthRefused:
        return 0


def _public_account(rec: dict[str, Any]) -> dict[str, Any]:
    """What may be said about an account OUT LOUD.

    Built by naming the four fields that leave, rather than by copying the
    record and deleting the secret ones. A blocklist is one added field away
    from shipping a salted key to a browser; an allowlist is not.
    """
    return {
        "account_id": rec.get("account_id"),
        "name": rec.get("name"),
        "phone": rec.get("phone"),
        "role": rec.get("role"),
        "created_at": rec.get("created_at"),
    }


# ------------------------------------------------------------- the sessions --


def _token_id(token: str) -> str:
    """sha256 of the token. THE ONLY FORM OF A TOKEN THAT TOUCHES DISK.

    A session file that is a list of working keys is a session file you cannot
    let anyone copy. This one is a list of fingerprints: it can say whether a
    token presented now is live, and it cannot produce one.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _session_seconds() -> int:
    """How long a sign-in lasts, in whole seconds. `GAWAAH_SESSION_HOURS`."""
    raw = (os.environ.get("GAWAAH_SESSION_HOURS") or "").strip()
    hours = SESSION_HOURS
    if raw:
        try:
            hours = int(raw)
        except ValueError:
            hours = SESSION_HOURS
    if hours < 1:
        hours = 1
    if hours > 24 * 30:
        hours = 24 * 30
    return hours * 3600


def _prune(doc: dict[str, Any], at: int) -> bool:
    """Drop expired sessions. True if anything went."""
    live = {k: v for k, v in doc["sessions"].items()
            if isinstance(v, dict) and int(v.get("expires_at") or 0) > at}
    if len(live) == len(doc["sessions"]):
        return False
    doc["sessions"] = live
    return True


def _mint_session(rec: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """A new session. Returns (token, stored record) — the token is NOT stored.

    `secrets.token_urlsafe(32)` is 256 bits from the OS entropy pool. It is
    opaque: it encodes no account, no time and no shop, so it cannot be
    inspected, tampered with or forged into another one.
    """
    token = secrets.token_urlsafe(32)
    at = _now()
    stored = {
        "account_id": rec["account_id"],
        "phone_key": rec["phone_key"],
        "created_at": at,
        "expires_at": at + _session_seconds(),
        "last_seen_at": at,
    }
    with _LOCK:
        doc = _load_sessions()
        _prune(doc, at)
        mine = sorted(
            ((k, v) for k, v in doc["sessions"].items()
             if isinstance(v, dict) and v.get("account_id") == rec["account_id"]),
            key=lambda kv: int(kv[1].get("created_at") or 0))
        while len(mine) >= MAX_SESSIONS_PER_ACCOUNT:
            doc["sessions"].pop(mine.pop(0)[0], None)
        doc["sessions"][_token_id(token)] = stored
        _save_sessions(doc)
    return token, stored


def _presented_token(request: Request) -> str:
    """The token on this request: the cookie, or an `Authorization: Bearer`.

    The cookie is the ordinary path — it is what sign-in sets and what a
    same-origin `fetch` sends without the page having to hold the token at all.
    Bearer is here for `curl` and for anything that cannot keep a cookie jar.
    """
    tok = request.cookies.get(SESSION_COOKIE) or ""
    if tok:
        return tok
    header = request.headers.get("authorization") or ""
    if header[:7].lower() == "bearer ":
        return header[7:].strip()
    return ""


def _resolve(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(account, session) for a live token, or a named refusal."""
    if not token:
        raise AuthRefused(
            R_NO_SESSION,
            "this request carried no session. Sign in at POST /auth/signin.")
    tid = _token_id(token)
    at = _now()
    with _LOCK:
        doc = _load_sessions()
        sess = doc["sessions"].get(tid)
        if not isinstance(sess, dict):
            raise AuthRefused(
                R_SESSION_UNKNOWN,
                "that session is not one this counter is holding. It was "
                "signed out, or it was never issued here. Sign in again.")
        if int(sess.get("expires_at") or 0) <= at:
            doc["sessions"].pop(tid, None)
            _save_sessions(doc)
            raise AuthRefused(
                R_SESSION_EXPIRED,
                f"that session ran out at {_iso(int(sess.get('expires_at') or 0))}. "
                f"Sign in again.")
        accounts = _load_accounts()["accounts"]
        rec = accounts.get(str(sess.get("phone_key") or ""))
        if not isinstance(rec, dict):
            # The account was removed while a session for it was live. The
            # session is worthless; say so in the same words as any other dead
            # session rather than confirming that an account used to exist.
            doc["sessions"].pop(tid, None)
            _save_sessions(doc)
            raise AuthRefused(
                R_SESSION_UNKNOWN,
                "that session is not one this counter is holding. Sign in "
                "again.")
        # `last_seen_at` is NOT written here. This runs on every guarded
        # request, and a disk write per request buys a field nothing reads.
    return rec, sess


def _drop_session(token: str) -> bool:
    """Forget a session. True if there was one to forget."""
    if not token:
        return False
    tid = _token_id(token)
    with _LOCK:
        try:
            doc = _load_sessions()
        except AuthRefused:
            return False
        if tid not in doc["sessions"]:
            return False
        doc["sessions"].pop(tid, None)
        _save_sessions(doc)
    return True


def _set_cookie(resp: JSONResponse, request: Request, token: str,
                max_age_s: int) -> None:
    """Put the token in a header, and only in a header.

    httponly    — script on the page cannot read it, so an injected script
                  cannot post it somewhere.
    samesite=lax— another site's page cannot make the browser POST to this till
                  with the shopkeeper's cookie attached. Lax and not strict, so
                  that following a link or a QR to the till still arrives
                  signed in.
    secure      — only when the request itself arrived over https. A till on a
                  shop's wifi is plain http, and a Secure cookie set over http
                  is a cookie the browser silently discards: sign-in would
                  appear to work and nothing would stay signed in.
    """
    proto = (request.headers.get("x-forwarded-proto")
             or request.url.scheme or "http").strip().lower()
    resp.set_cookie(SESSION_COOKIE, token, max_age=max_age_s, path="/",
                    httponly=True, samesite="lax", secure=(proto == "https"))


def _clear_cookie(resp: JSONResponse) -> None:
    resp.delete_cookie(SESSION_COOKIE, path="/")


# ---------------------------------------------------------- the rate limit --
#
# In memory, on purpose, and that IS a limit rather than an oversight: a
# restart clears the counters. Persisting them would put a disk write behind
# every wrong password — which is exactly the request an attacker controls the
# rate of — and the counter is a single process that nobody but the shopkeeper
# can restart.

_ATTEMPTS: dict[str, dict[str, Any]] = {}


def _forget_old(at: int) -> None:
    dead = [k for k, v in _ATTEMPTS.items()
            if int(v.get("until") or 0) <= at
            and not [t for t in v.get("at", []) if t > at - ATTEMPT_WINDOW_S]]
    for k in dead:
        _ATTEMPTS.pop(k, None)
    if len(_ATTEMPTS) > MAX_TRACKED_PHONES:
        # Somebody is cycling numbers. Keep the most recently active and let
        # the rest go; the alternative is a dict that grows until the process
        # does not fit in memory.
        keep = sorted(_ATTEMPTS.items(),
                      key=lambda kv: int(kv[1].get("touched") or 0),
                      reverse=True)[:MAX_TRACKED_PHONES]
        _ATTEMPTS.clear()
        _ATTEMPTS.update(dict(keep))


def _locked_for(digits: str) -> int:
    """Seconds this phone must wait, or 0. Reading this never extends a lock."""
    at = _now()
    with _LOCK:
        _forget_old(at)
        rec = _ATTEMPTS.get(digits)
        if not rec:
            return 0
        left = int(rec.get("until") or 0) - at
        return left if left > 0 else 0


def _record_failure(digits: str) -> int:
    """One wrong password. Returns the lock in seconds, 0 if not tripped yet.

    A LOCKED PHONE'S ATTEMPTS ARE NOT RECORDED — see `signin_ep`, which returns
    before it gets here. If they were, anybody could hold a shopkeeper out
    indefinitely by hammering, instead of for the five minutes the rule says.
    """
    at = _now()
    with _LOCK:
        _forget_old(at)
        rec = _ATTEMPTS.setdefault(digits, {"at": [], "until": 0})
        rec["at"] = [t for t in rec["at"] if t > at - ATTEMPT_WINDOW_S] + [at]
        rec["touched"] = at
        if len(rec["at"]) >= MAX_ATTEMPTS:
            rec["until"] = at + LOCK_S
            rec["at"] = []
            return LOCK_S
        return 0


def _clear_failures(digits: str) -> None:
    with _LOCK:
        _ATTEMPTS.pop(digits, None)


def reset_rate_limit() -> None:
    """Forget every recorded attempt. For tests, and for a wedged counter."""
    with _LOCK:
        _ATTEMPTS.clear()


# ------------------------------------------------------------ the invites --


def _mint_invite(by: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """A single-use code. Only its HASH is stored, for the token's reasons."""
    code = INVITE_PREFIX + secrets.token_urlsafe(18)
    at = _now()
    rec = {
        "created_at": at,
        "expires_at": at + INVITE_HOURS * 3600,
        "by_account_id": by.get("account_id"),
        "used_at": None,
        "used_by": None,
    }
    with _LOCK:
        doc = _load_accounts()
        doc["invites"][_token_id(code)] = rec
        _save_accounts(doc)
    return code, rec


def _check_invite(doc: dict[str, Any], code: str) -> dict[str, Any]:
    """Is this code good? Returns the record IN THE CALLER'S OPEN DOCUMENT.

    Checking and spending are two functions on purpose. A sign-up that is going
    to be refused for some other reason — a short password, a taken number —
    must not burn somebody's one invitation on the way to refusing it.

    The document is passed in rather than loaded here so that creating the
    account and burning the code end up in ONE write. Two writes have a window
    in which a code is spent and the account it was spent on does not exist.
    """
    rec = doc["invites"].get(_token_id(code))
    if not isinstance(rec, dict):
        raise AuthRefused(
            R_INVITE_UNKNOWN,
            "that invitation code is not one this shop issued. Ask the "
            "shopkeeper for another. Nothing was created.")
    if rec.get("used_at"):
        raise AuthRefused(
            R_INVITE_USED,
            f"that invitation was already used, at {_iso(int(rec['used_at']))}. "
            f"Each one opens exactly one account. Nothing was created.")
    if int(rec.get("expires_at") or 0) <= _now():
        raise AuthRefused(
            R_INVITE_EXPIRED,
            f"that invitation expired at {_iso(int(rec.get('expires_at') or 0))}. "
            f"Ask the shopkeeper for another. Nothing was created.")
    return rec


def _burn_invite(rec: dict[str, Any], account_id: str) -> None:
    """Spend a code that `_check_invite` has already approved."""
    rec["used_at"] = _now()
    rec["used_by"] = account_id


# -------------------------------------------------- what the orchestrator uses --


def current_shopkeeper(request: Request) -> Optional[dict[str, Any]]:
    """Who is signed in on this request, or None. NEVER RAISES.

    For a route that wants to record who did something without caring whether
    anybody did — which, with the switch off, is every route in this program.
    """
    try:
        rec, _sess = _resolve(_presented_token(request))
        return _public_account(rec)
    except Exception:  # noqa: BLE001 - "nobody" is the answer, not an error
        return None


#: The attribute that marks a callable as one of this module's guards. Set on
#: `require_shopkeeper` and on every closure `guard_open` builds, and read by
#: `guard_coverage` — which is how `/auth/status` can answer "is the lock
#: actually wired to anything" with a measurement instead of a hope.
GUARD_MARK = "gawaah_auth_guard"


def _decide(request: Request, extra_paths: tuple[str, ...] = (),
            extra_prefixes: tuple[str, ...] = ()) -> Optional[dict[str, Any]]:
    """The whole guard, with the caller's own open list folded in."""
    who = current_shopkeeper(request)
    # Recorded either way. A route that wants to attribute an action can read
    # `request.state.shopkeeper` before enforcement is ever turned on.
    try:
        request.state.shopkeeper = who
    except Exception:  # noqa: BLE001 - a request without state is not a crash
        pass

    if not auth_required():
        return who
    path = request.url.path
    if is_open_path(path):
        return who
    if extra_paths or extra_prefixes:
        if _matches(path, extra_paths, extra_prefixes):
            return who
    if who is not None:
        return who
    if account_count() == 0:
        raise AuthHTTPRefusal(
            R_NO_ACCOUNT_YET,
            "GAWAAH_REQUIRE_AUTH is on and this counter has no account yet, so "
            "nobody can sign in to it. Create the first one at POST "
            "/auth/signup, or unset GAWAAH_REQUIRE_AUTH.")
    raise AuthHTTPRefusal(
        R_NOT_SIGNED_IN,
        "this counter is locked and this request is not signed in. Sign in at "
        "POST /auth/signin.")


def require_shopkeeper(request: Request) -> Optional[dict[str, Any]]:
    """THE GUARD. Use it as `Depends(require_shopkeeper)`.

    WITH `GAWAAH_REQUIRE_AUTH` UNSET — which is how this ships and how it must
    stay until somebody decides otherwise — this lets every request through and
    returns whoever was signed in, or None. It is inert. Applying it to a
    router today changes nothing about who can reach it, which is the property
    that makes it safe to apply today and switch on later.

    With the switch on it refuses by name, with the status a browser needs, and
    it leaves the routes in `OPEN_PATHS` reachable so that the way back in is
    never behind the lock.

    IT DOES NOT KNOW ABOUT THE STOREFRONT. `/store` is locked by this guard
    unless `GAWAAH_AUTH_OPEN` says otherwise — see `is_open_path`. A deployment
    that has a customer-facing side declares it with `depends_open` instead of
    relying on an environment variable being remembered.
    """
    return _decide(request)


setattr(require_shopkeeper, GUARD_MARK, True)


def guard_open(paths: tuple[str, ...] = (),
               prefixes: tuple[str, ...] = ()) -> Callable[[Request],
                                                           Optional[dict]]:
    """`require_shopkeeper`, plus a written promise that THESE stay reachable.

    WHY THIS EXISTS AT ALL. A shop has two audiences and one server. The
    shopkeeper's screens are the shop's books and belong behind the lock; the
    storefront a stranger opens off the shutter QR, the bill link a customer is
    sent on WhatsApp, and the payment QR their own order page draws belong in
    front of it. Those three live in the SAME routers as guarded routes —
    `gawaah/storefront.py` serves `/store/order` and `/orders` side by side —
    so a per-router `dependencies=` cannot separate them and a per-route one
    would mean editing eleven modules this change does not own.

    So the policy is a LIST, it is passed in by whoever mounts the routers, and
    it is therefore visible in one place next to the mounting. An environment
    variable would have worked and is worse: `GAWAAH_AUTH_OPEN=/store` is a
    thing somebody has to remember on every machine, and forgetting it takes
    the shop's front door down with no message anywhere saying why.

    `GAWAAH_AUTH_OPEN` still works and still adds to this — an operator can
    open something the deployment did not think of without editing code.
    """
    paths = tuple(paths)
    prefixes = tuple(prefixes)

    def _guard(request: Request) -> Optional[dict[str, Any]]:
        return _decide(request, paths, prefixes)

    _guard.__name__ = "require_shopkeeper_or_open"
    _guard.__doc__ = (
        "The counter's guard. Open without a session: "
        + ", ".join(sorted(paths) + [p + "*" for p in sorted(prefixes)]))
    setattr(_guard, GUARD_MARK, True)
    setattr(_guard, "open_paths", paths)
    setattr(_guard, "open_prefixes", prefixes)
    return _guard


def depends_open(paths: tuple[str, ...] = (),
                 prefixes: tuple[str, ...] = ()) -> list[Any]:
    """`guard_open`, pre-wrapped for `dependencies=`.

    Call it ONCE and reuse the list. Calling it per router would build one
    closure per router, and FastAPI caches a dependency's result per request by
    the callable's identity — twenty-three identities means twenty-three
    resolutions of the same session on a request that touches one route. One
    identity means one.
    """
    return [Depends(guard_open(paths, prefixes))]


def require_shopkeeper_always(request: Request) -> dict[str, Any]:
    """The guard WITHOUT the switch — the caller must be signed in, full stop.

    Used by `/auth/invite` and nowhere else. Minting an invitation is the act
    that widens who may use this counter, and an unauthenticated one would make
    "the first account, or invited" mean "anybody, always".
    """
    who = current_shopkeeper(request)
    if who is None:
        raise AuthRefused(
            R_NOT_SIGNED_IN,
            "only somebody signed in to this counter can do that. Sign in at "
            "POST /auth/signin.")
    return who


#: Pre-wrapped, so protecting a router is one keyword argument:
#:     app.include_router(x.router, dependencies=auth.DEPENDS)
#: With the switch off this is a no-op that records the signed-in user.
DEPENDS = [Depends(require_shopkeeper)]


async def _http_refusal_handler(request: Request,
                                exc: Exception) -> JSONResponse:
    """Flatten a guard refusal into this program's usual refusal body."""
    reason = getattr(exc, "reason", R_NOT_SIGNED_IN)
    why = getattr(exc, "why", "this request is not signed in.")
    return JSONResponse(_body(reason, why),
                        status_code=_STATUS_BY_REASON.get(reason, 400))


def mounted_paths(app: Any) -> set[str]:
    """Every route path reachable on this app, however deeply it is nested.

    NOT `[r.path for r in app.routes]`, AND THAT IS NOT PEDANTRY. FastAPI 0.141
    does not flatten an included router into the app's route list any more — it
    appends one wrapper object with NO `.path` at all. A membership test
    written the obvious way therefore sees nothing, reports "not mounted", and
    `install()` mounts a second copy of every route on top of the first. This
    walks whatever shape the framework is using this week.
    """
    seen: set[str] = set()
    visited: set[int] = set()
    stack = [app]
    steps = 0
    #: The three names a route tree hides its children behind, across the
    #: versions of Starlette and FastAPI this repo might be installed against.
    #: `original_router` is the 0.141 wrapper; `router` is a sub-application;
    #: `app` is a Mount.
    holders = ("routes", "original_router", "router", "app")
    while stack and steps < 10000:
        steps += 1
        item = stack.pop()
        if id(item) in visited:
            continue
        visited.add(id(item))
        path = getattr(item, "path", None)
        if isinstance(path, str):
            seen.add(path)
        for name in holders:
            child = getattr(item, name, None)
            if child is None or child is item:
                continue
            if isinstance(child, (str, bytes)):
                continue
            if isinstance(child, (list, tuple)):
                stack.extend(child)
            elif callable(child) or hasattr(child, "routes"):
                stack.append(child)
    return seen


# ------------------------------------------------- is the lock ACTUALLY on --
#
# THE BUG THIS SECTION EXISTS FOR. `/auth/status` used to answer
# `enforced: true` whenever `GAWAAH_REQUIRE_AUTH` was set — a claim about an
# environment variable dressed up as a claim about a lock. The guard was
# complete, tested, and applied to NOTHING; every screen in the shop answered
# 200 to a stranger while the counter's own status endpoint said it was shut.
# A boolean that reports its own wiring is worth more than one that reports its
# configuration, so `enforced` below is now a walk of the live route tree.


def _is_guard(dep: Any) -> bool:
    """Is this `Depends(...)` one of ours?"""
    return bool(getattr(getattr(dep, "dependency", None), GUARD_MARK, False))


def _route_leaves(app: Any) -> list[tuple[str, str, tuple[Any, ...]]]:
    """(path, kind, dependencies) for every endpoint this app can serve.

    Kind is `api` (a FastAPI route, which CAN carry a dependency), `mount` (a
    StaticFiles or sub-app mount, which cannot) or `plain` (a bare Starlette
    route such as `/openapi.json`, which cannot either).

    THE DEPENDENCIES ARE NOT ON THE ROUTES. FastAPI 0.141 stopped copying an
    included router's routes onto the app: it appends one `_IncludedRouter`
    wrapper that keeps the `dependencies=` argument in its `include_context`
    and leaves the underlying `APIRoute.dependencies` EMPTY. A coverage check
    written the obvious way would therefore read every guarded route in this
    program as unguarded and report the lock as broken while it worked. So the
    walk carries inherited dependencies down the tree, and it duck-types the
    wrapper rather than importing a private class, so an older FastAPI (which
    bakes the dependencies onto the route itself) reads correctly too.
    """
    try:
        from fastapi.routing import APIRoute as _APIRoute
    except Exception:  # noqa: BLE001 - no FastAPI is not a crash here
        _APIRoute = ()  # type: ignore[assignment]
    try:
        from starlette.routing import Mount as _Mount
    except Exception:  # noqa: BLE001
        _Mount = ()  # type: ignore[assignment]

    out: list[tuple[str, str, tuple[Any, ...]]] = []
    seen: set[tuple[int, str]] = set()
    root = getattr(app, "router", None) or app
    stack: list[tuple[Any, tuple[Any, ...], str]] = [(root, (), "")]
    steps = 0
    while stack and steps < 20000:
        steps += 1
        node, inherited, prefix = stack.pop()
        key = (id(node), prefix)
        if key in seen:
            continue
        seen.add(key)

        ctx = getattr(node, "include_context", None)
        orig = getattr(node, "original_router", None)
        if ctx is not None and orig is not None:
            stack.append((
                orig,
                inherited + tuple(getattr(ctx, "dependencies", ()) or ()),
                prefix + str(getattr(ctx, "prefix", "") or "")))
            continue

        if _Mount and isinstance(node, _Mount):
            here = prefix + str(getattr(node, "path", "") or "")
            kids = list(getattr(node, "routes", ()) or ())
            if kids:
                for child in kids:
                    stack.append((child, inherited, here))
            else:
                out.append((here or "/", "mount", inherited))
            continue

        routes = getattr(node, "routes", None)
        if isinstance(routes, (list, tuple)) and routes:
            here = inherited + tuple(getattr(node, "dependencies", ()) or ())
            for child in routes:
                stack.append((child, here, prefix))
            continue

        path = getattr(node, "path", None)
        if not isinstance(path, str):
            continue
        deps = inherited + tuple(getattr(node, "dependencies", ()) or ())
        kind = "api" if (_APIRoute and isinstance(node, _APIRoute)) else "plain"
        out.append((prefix + path, kind, deps))
    return out


def guard_coverage(app: Any) -> dict[str, Any]:
    """Which of this app's routes actually carry the guard. A measurement.

    Four buckets, and the boundaries between them are the whole content:

      guarded      — carries one of this module's guards. Whether a given
                     REQUEST to it is refused still depends on the switch and
                     on the guard's own open list; what is asserted here is
                     only that the guard is wired to it.
      open_by_auth — `OPEN_PATHS` (the way back in) and `SELF_GUARDED`
                     (`/auth/invite`, which requires a session on its own
                     regardless of the switch). Not carrying the guard is
                     CORRECT for these, so they are not counted as a hole.
      unguarded    — an API route that could carry the guard and does not.
                     THIS IS THE ONE THAT MATTERS. A non-empty list here is the
                     difference between a locked counter and a counter that
                     says it is locked.
      no_guard_possible — mounts and bare Starlette routes. `/assets` is a
                     StaticFiles mount and a hashed JS bundle; `/openapi.json`,
                     `/docs` and `/redoc` are FastAPI's own. None of them can
                     take a `Depends`, and saying so out loud is better than
                     quietly counting them as either safe or broken.
    """
    guarded: list[str] = []
    open_by_auth: list[str] = []
    unguarded: list[str] = []
    impossible: list[str] = []
    for path, kind, deps in _route_leaves(app):
        if kind != "api":
            impossible.append(path)
        elif path in OPEN_PATHS or path in SELF_GUARDED:
            open_by_auth.append(path)
        elif any(_is_guard(d) for d in deps):
            guarded.append(path)
        else:
            unguarded.append(path)
    return {
        "guarded": len(set(guarded)),
        "open_by_auth": sorted(set(open_by_auth)),
        "unguarded_paths": sorted(set(unguarded)),
        "no_guard_possible": sorted(set(impossible)),
    }


def _the_guard(app: Any) -> dict[str, Any]:
    """What the guard actually mounted on this app leaves open, in its words.

    Read off the live dependency rather than off a constant, because a readout
    copied from a constant is exactly how `enforced` came to disagree with the
    thing it described.
    """
    paths: set[str] = set()
    prefixes: set[str] = set()
    try:
        for _path, kind, deps in _route_leaves(app):
            if kind != "api":
                continue
            for d in deps:
                fn = getattr(d, "dependency", None)
                if not getattr(fn, GUARD_MARK, False):
                    continue
                paths.update(getattr(fn, "open_paths", ()) or ())
                prefixes.update(getattr(fn, "open_prefixes", ()) or ())
    except Exception:  # noqa: BLE001 - a readout must not be able to fail
        pass
    return {"paths": sorted(paths), "prefixes": sorted(prefixes)}


def enforced_on(app: Any) -> bool:
    """Is this counter LOCKED — the switch on AND the guard on every route?

    Deliberately strict, and deliberately not a percentage. One unguarded route
    into the books is not "97% locked", it is a way in; and the day somebody
    mounts a twenty-fourth router and forgets the dependency, this flips to
    False and `/auth/status` names the path. A partial answer here would be the
    same species of lie the whole section exists to remove.
    """
    if not auth_required():
        return False
    try:
        cov = guard_coverage(app)
    except Exception:  # noqa: BLE001 - unmeasurable is not "locked"
        return False
    return cov["guarded"] > 0 and not cov["unguarded_paths"]


def _enforced_for(request: Request) -> bool:
    """`enforced_on` for the app this request arrived at, never raising."""
    try:
        return enforced_on(request.app)
    except Exception:  # noqa: BLE001
        return False


def install(app: Any) -> Any:
    """Mount the routes and make a guard refusal look like every other refusal.

    Idempotent, and safe to call whether or not the caller already did
    `app.include_router(auth.router)` — the routes are looked for before they
    are mounted, so a second call cannot produce two copies of everything.

    Calling this is not optional-but-nice: without it a guard refusal comes back
    as Starlette's `{"detail": {...}}` instead of this repo's flat body. It is
    still the right status and still carries every field. It is never a 500.
    """
    if not getattr(app, "_gawaah_auth_mounted", False) \
            and "/auth/me" not in mounted_paths(app):
        app.include_router(router)
    try:
        app._gawaah_auth_mounted = True
    except Exception:  # noqa: BLE001 - an app that refuses attributes is fine
        pass
    app.add_exception_handler(AuthHTTPRefusal, _http_refusal_handler)
    return app


# ----------------------------------------------------------------- routes --


@router.post("/auth/signup")
async def signup_ep(request: Request) -> JSONResponse:
    """Open an account. The FIRST one is free; every one after it needs an invite.

    Body: {name, phone, password, invite?}. The first account on a counter is
    open because there is nobody yet who could invite anyone — this is the one
    moment where anyone who can reach the till can claim it, which is why the
    right time to open a counter's first account is the first time it is
    switched on, and why `GET /auth/status` says out loud whether that moment
    has passed.

    Succeeding signs you in: the response sets the session cookie. The token is
    NOT in the body — see the module docstring.
    """
    try:
        body = await _json_body(request)
        name = _name(body)
        typed, digits = _phone(body)
        password = _password(body)
        invite = _text(body, "invite")

        # A password that is the phone number is the single most common thing
        # somebody types at a counter under time pressure, and it is public
        # information printed on the shop's own board.
        if normalise_phone(password) == digits and password.strip():
            raise AuthRefused(
                R_PASSWORD_IS_PHONE,
                "that password is this account's own phone number, which is "
                "written on the shop board. Pick something else.")

        with _LOCK:
            doc = _load_accounts()
            first = not doc["accounts"]

            # THE INVITATION IS CHECKED BEFORE THE PHONE NUMBER IS, AND THE
            # ORDER IS THE WHOLE POINT. The other way round, a stranger with no
            # code learns whether a number has an account here — `phone_taken`
            # for one and `needs_an_invite` for the other — which is exactly the
            # question `/auth/signin` goes to some trouble not to answer. With
            # this order, everybody without a good code gets the same sentence.
            invite_rec: Optional[dict[str, Any]] = None
            if not first and not signup_is_open():
                if not invite:
                    raise AuthRefused(
                        R_SIGNUP_CLOSED,
                        "this counter already has an account, so a new one "
                        "needs an invitation from somebody signed in. Ask them "
                        "for a code from POST /auth/invite. Nothing was "
                        "created.")
                invite_rec = _check_invite(doc, invite)
            elif not first and invite:
                # The door is open, but a code that was typed is still checked.
                # Accepting a wrong one because the gate happens to be down
                # would burn nothing and teach the operator that their invites
                # do not mean anything.
                invite_rec = _check_invite(doc, invite)

            if digits in doc["accounts"]:
                raise AuthRefused(
                    R_PHONE_TAKEN,
                    f"{typed} already has an account on this counter. Sign in "
                    f"instead, or use another number. Nothing was created.")

            account_id = ACCOUNT_PREFIX + secrets.token_hex(6)
            if invite_rec is not None:
                _burn_invite(invite_rec, account_id)

            salt = secrets.token_bytes(SCRYPT_SALT_BYTES)
            rec = {
                "account_id": account_id,
                "name": name,
                "phone": typed,
                "phone_key": digits,
                "role": "owner" if first else "staff",
                "created_at": _now_iso(),
                # The salt, the derived key, and what derived it. There is no
                # fourth field, and no field anywhere holds the password.
                "salt_hex": salt.hex(),
                "hash_hex": _derive(password, salt).hex(),
                "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
                "dklen": SCRYPT_DKLEN,
                "kdf": "scrypt",
            }
            doc["accounts"][digits] = rec
            _save_accounts(doc)

        token, sess = _mint_session(rec)
        _clear_failures(digits)
        head = _audit("auth.signup", account_id=account_id,
                      subject=_subject(digits), role=rec["role"],
                      first_account=first, invited=bool(invite))
        resp = JSONResponse({
            "ok": True,
            "settles_money": False,
            "signed_in": True,
            "first_account": first,
            "account": _public_account(rec),
            "expires_at": _iso(int(sess["expires_at"])),
            "audited": head is not None,
            "note": ("The session is in a cookie, not in this body. This "
                     "counter is not locked unless GAWAAH_REQUIRE_AUTH is set "
                     "— an account exists now, but every screen is as open as "
                     "it was."),
        })
        _set_cookie(resp, request, token, int(sess["expires_at"]) - _now())
        return resp
    except AuthRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/auth/signin")
async def signin_ep(request: Request) -> JSONResponse:
    """Sign in. Body: {phone, password}.

    A wrong phone and a wrong password answer with the SAME reason and take the
    SAME time. Telling them apart tells a stranger on the shop's wifi which of
    the staff have accounts here, and the counter has no reason to say.
    """
    try:
        body = await _json_body(request)
        typed, digits = _phone(body)
        password = _password(body)

        left_s = _locked_for(digits)
        if left_s > 0:
            # Refused BEFORE the password is looked at and WITHOUT recording
            # another failure, so hammering cannot extend the lock past the
            # five minutes the rule promises.
            head = _audit("auth.rate_limited", subject=_subject(digits),
                          locked_for_s=left_s)
            raise AuthRefused(
                R_TOO_MANY_ATTEMPTS,
                f"too many wrong passwords for {typed}. That number cannot try "
                f"again for {left_s} seconds. This counter allows "
                f"{MAX_ATTEMPTS} attempts in {ATTEMPT_WINDOW_S} seconds."
                + ("" if head is not None else " (Not audited.)"))

        rec = _load_accounts()["accounts"].get(digits)
        if not isinstance(rec, dict):
            # Cost the caller the same fifty milliseconds a real check costs.
            _burn_the_same_time()
            ok = False
        else:
            ok = _verify_password(password, rec)

        if not ok:
            locked_s = _record_failure(digits)
            _audit("auth.signin_refused", subject=_subject(digits),
                   reason=R_BAD_CREDENTIALS, locked=bool(locked_s))
            tail = (f" That number has now been locked for {locked_s} seconds."
                    if locked_s else "")
            raise AuthRefused(
                R_BAD_CREDENTIALS,
                "that phone number and password do not go together on this "
                "counter. Nothing was changed." + tail)

        _clear_failures(digits)
        token, sess = _mint_session(rec)
        head = _audit("auth.signin", account_id=rec["account_id"],
                      subject=_subject(digits), role=rec.get("role"))
        resp = JSONResponse({
            "ok": True,
            "settles_money": False,
            "signed_in": True,
            "account": _public_account(rec),
            "expires_at": _iso(int(sess["expires_at"])),
            "expires_in_s": int(sess["expires_at"]) - _now(),
            "enforced": _enforced_for(request),
            "audited": head is not None,
            "note": ("The session is in a cookie, not in this body. Send it "
                     "back with the request, or use Authorization: Bearer if "
                     "you cannot keep a cookie."),
        })
        _set_cookie(resp, request, token, int(sess["expires_at"]) - _now())
        return resp
    except AuthRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/auth/signout")
def signout_ep(request: Request) -> JSONResponse:
    """Sign out. Forgets the session server-side and clears the cookie.

    IDEMPOTENT ON PURPOSE, and this is the one place in the module where a
    failure is not a refusal. Signing out when you were not signed in has
    already achieved what it was asked to achieve, and answering 401 to
    somebody trying to LEAVE would mean a stale tab could not clear itself.
    """
    try:
        token = _presented_token(request)
        who = current_shopkeeper(request)
        cleared = _drop_session(token)
        head = None
        if cleared:
            head = _audit("auth.signout",
                          account_id=(who or {}).get("account_id"))
        resp = JSONResponse({
            "ok": True,
            "settles_money": False,
            "signed_in": False,
            "cleared": cleared,
            "audited": head is not None,
            "note": ("That session is gone from this counter, not just from "
                     "the browser. The token cannot be replayed."
                     if cleared else
                     "There was no session on this request. Nothing to clear."),
        })
        _clear_cookie(resp)
        return resp
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/auth/me")
def me_ep(request: Request) -> JSONResponse:
    """Who am I. The account, and how long this session has left.

    Three different refusals rather than one, because "you never signed in",
    "you signed out" and "your session ran out at half past two" are three
    different sentences on a screen.
    """
    try:
        rec, sess = _resolve(_presented_token(request))
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "signed_in": True,
            "account": _public_account(rec),
            "session": {
                "created_at": _iso(int(sess["created_at"])),
                "expires_at": _iso(int(sess["expires_at"])),
                "expires_in_s": int(sess["expires_at"]) - _now(),
            },
            "enforced": _enforced_for(request),
        })
    except AuthRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/auth/invite")
def invite_ep(request: Request) -> JSONResponse:
    """Mint a single-use code that opens one more account on this counter.

    THE CODE IS IN THIS BODY, unlike a session token, because it is meant to be
    read off the screen and handed to somebody. Only its hash is kept here, so
    a copy of the accounts file cannot be turned back into a usable invitation
    and this endpoint is the only place the code is ever legible.

    Requires a signed-in caller REGARDLESS of `GAWAAH_REQUIRE_AUTH`.
    """
    try:
        who = require_shopkeeper_always(request)
        code, rec = _mint_invite(who)
        head = _audit("auth.invite_minted",
                      by_account_id=who.get("account_id"),
                      expires_at=_iso(int(rec["expires_at"])))
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "invite": code,
            "expires_at": _iso(int(rec["expires_at"])),
            "single_use": True,
            "audited": head is not None,
            "note": ("Give this to one person. It opens one account and stops "
                     "working after that, or after "
                     f"{INVITE_HOURS} hours, whichever comes first. This "
                     "counter keeps only a hash of it and cannot show it "
                     "again."),
        })
    except AuthRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/auth/status")
def status_ep(request: Request) -> JSONResponse:
    """What this counter's lock is doing. Reachable without signing in.

    A page has to know whether to draw "create the first account", "sign in",
    or nothing at all, and it has to know before it has a session. Nothing here
    names a person: a count, two flags, and the names of the switches.
    """
    try:
        try:
            n = account_count()
            readable = True
        except Exception:  # noqa: BLE001 - report it rather than failing
            n, readable = 0, False
        switch_on = auth_required()
        # THE MEASUREMENT, NOT THE SETTING. See the `enforced_on` section: this
        # field used to be `auth_required()`, which reported an environment
        # variable while the guard was attached to nothing.
        try:
            cov = guard_coverage(request.app)
        except Exception:  # noqa: BLE001 - report it rather than failing
            cov = {"guarded": 0, "open_by_auth": [], "unguarded_paths": [],
                   "no_guard_possible": []}
        applied = cov["guarded"] > 0 and not cov["unguarded_paths"]
        enforced = switch_on and applied
        guard = _the_guard(request.app)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "accounts": n,
            "store_readable": readable,
            "signup_open": n == 0 or signup_is_open(),
            "signup_needs_invite": n > 0 and not signup_is_open(),
            "signup_switch": OPEN_SIGNUP_ENV,
            "enforced": enforced,
            "switch": "GAWAAH_REQUIRE_AUTH",
            "switch_on": switch_on,
            "guard_applied": applied,
            "session_hours_switch": "GAWAAH_SESSION_HOURS",
            "open_paths_switch": "GAWAAH_AUTH_OPEN",
            "session_seconds": _session_seconds(),
            "open_paths": sorted(OPEN_PATHS) + list(_open_prefixes()),
            # What THIS deployment leaves open on top of auth's own five. Read
            # off the guard that is actually mounted, so the readout cannot
            # drift from the wiring the way `enforced` did.
            "open_here": guard,
            "lock": {
                "guarded_routes": cov["guarded"],
                "open_by_auth": cov["open_by_auth"],
                "unguarded_routes": len(cov["unguarded_paths"]),
                # Named, not counted. A count says "something is wrong" and a
                # list says which router to go and fix.
                "unguarded_paths": cov["unguarded_paths"],
                "no_guard_possible": cov["no_guard_possible"],
            },
            "signed_in": current_shopkeeper(request) is not None,
            "rate_limit": {"attempts": MAX_ATTEMPTS,
                           "window_s": ATTEMPT_WINDOW_S,
                           "lock_s": LOCK_S},
            "note": (
                "GAWAAH_REQUIRE_AUTH is set and the guard is on every route "
                "this app can guard. A request without a session is refused "
                "unless its path is listed in open_paths or open_here."
                if enforced else
                ("GAWAAH_REQUIRE_AUTH is set, but "
                 f"{len(cov['unguarded_paths'])} route(s) do not carry the "
                 "guard, so this counter is NOT locked. They are named in "
                 "lock.unguarded_paths."
                 if switch_on else
                 "Nothing on this counter is locked. Accounts exist and "
                 "sessions work, and every route is as reachable as it was "
                 "before they did — until GAWAAH_REQUIRE_AUTH is set. The "
                 f"guard is already wired to {cov['guarded']} route(s), so "
                 "setting it is the whole change.")),
        })
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)
