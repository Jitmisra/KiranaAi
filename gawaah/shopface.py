"""CHEHRA — the face of the shop: its own link, its printed code, its photograph.

Three things a shop has on its shutter that this counter did not: a name a
customer can point their phone at and reach THIS shop and no other, a code that
carries that link, and a picture of the place. This module is all three, and
the one open endpoint the storefront reads them from.

WHY THE LINK NEEDED AN IDENTITY
===============================
`/store/qr` encodes `<origin>/#/shop`. That string is the same for every
counter on earth: peel the sticker off one shop, stick it on another, and the
second shop's catalogue opens under the first shop's name. The link a customer
saves in WhatsApp says nothing about which kirana it opens. So the profile
carries a SLUG (`gawaah/shopadmin.py` mints and keeps it) and the customer link
becomes `<origin>/#/shop?s=<slug>`. The storefront hands the slug back to
`GET /store/shop`, which answers `matches: false` — by name — when the link
was printed for a different shop.

THIS SLUG IS NOT A SECRET AND NOT A CREDENTIAL. It is painted on the shutter.
It identifies which shop a link means; it authorises nothing. The token that
identifies a CUSTOMER (`?k=`, `gawaah/storefront.py`) is a different thing and
is stripped from the address bar on arrival for exactly that reason.

THE CODE IS A NAVIGATION QR AND NOTHING ELSE. It encodes this server's origin,
the storefront route and the slug — no amount, no order, no payment target —
and it is refused if the string could ever be read as a UPI payload or points
at a gateway host. Those checks cannot fire on the string as built here; they
are the same two `/store/qr` carries, for the same reason: the day somebody
makes this take a free parameter is the day invariant 1 needs a guard that was
already in place.

THE PHOTOGRAPH goes through the catalogue's own photo door
(`gawaah.shop_store.encode_photo_png`) with the store's OWN two numbers —
256 px long edge, 128 KiB — so the shop's picture is bounded exactly as a
product's is. It lives as a file beside the profile, never inside it, and is
served by an open GET because the customer is who it is for.

WHAT IS OPEN, AND WHY ONLY THAT
===============================
`GET /store/shop` and `GET /store/shop/photo` are reachable with no session:
they are what a stranger's phone reads after scanning the shutter. They answer
the slug, the name, the address and the photo's URL — the four things painted
on the shutter — and nothing else: no phone, no hours, no file path. Everything
that WRITES (`PUT /shop/photo`, `POST /shop/link/renew`) and the shopkeeper's
own link and code (`GET /shop/link`, `GET /shop/link/qr`) stay behind the lock.

MOUNTING
========
An ``APIRouter`` with NO prefix; the paths are absolute::

    from gawaah import shopface
    app.include_router(shopface.router, dependencies=AUTH_GUARD)

    GET   /store/shop?s=<slug>      the header a customer sees, and whether the
                                    slug they arrived with is this shop's  (open)
    GET   /store/shop/photo         the shop's picture                       (open)
    GET   /shop/link                this shop's own customer link, as text
    GET   /shop/link/qr             the same, as a printable code
    POST  /shop/link/renew          mint a new slug — the old stickers stop
                                    matching, and the response says so
    PUT   /shop/photo               give the shop a picture, or take it away

THIS FILE NEVER SETTLES MONEY. It holds no gateway, mints no payment, and the
one QR it draws is refused if it could carry one.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from . import shopadmin as _sa
from .shopadmin import AdminRefused, _crash, _refusal

router = APIRouter()

# --------------------------------------------------------------- refusals --

R_NO_HOST = "cannot_tell_this_shops_address"
R_REFUSED_LINK = "refused_to_encode_this_string"
R_NO_PROFILE = "shop_has_no_name_yet"
R_NO_PHOTO = "shop_has_no_photo"
R_INTERNAL = "shopface_internal_error"

#: THE SHOP PHOTO'S BUDGET IS THE STORE'S OWN. `gawaah/shop_store.py` keeps a
#: product photograph at PHOTO_EDGE_PX / PHOTO_CAP_BYTES; the shop's picture is
#: stored the same way, beside the profile, and is charged to nobody's JSON
#: read, so it gets the full budget rather than the sidecar's smaller one.
#: Restated here rather than imported because importing shop_store pulls in
#: numpy at module scope, and this router must load without it. A test holds
#: the two pairs equal.
SHOP_PHOTO_EDGE_PX = 256
SHOP_PHOTO_CAP_BYTES = 128 * 1024
PHOTO_NAME = "shop_photo.png"

#: Loopback names. A QR reading `http://127.0.0.1:8790` is a perfectly good QR
#: that no phone on earth can open, and that failure is silent unless said.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1", "0.0.0.0")

#: How much of a foreign slug is echoed back. A customer arriving with a
#: mangled `?s=` deserves to see what they arrived with; a 4 KB one does not
#: need to be repeated in full.
REQUESTED_ECHO_MAX = 80


# ------------------------------------------------------------------ paths --


def photo_path() -> Path:
    """Beside the profile, in the shop directory the till answers with.

    Through `shopadmin.shop_dir()` — which is the till's own `store_dir()` — so
    a scratch `GAWAAH_SHOP_DIR` gets a scratch photo, and a test cannot write a
    picture into the live shop.
    """
    return _sa.shop_dir() / PHOTO_NAME


def has_photo() -> bool:
    try:
        return photo_path().is_file() and photo_path().stat().st_size > 0
    except OSError:
        return False


def photo_url() -> Optional[str]:
    """The open URL of the shop's picture, versioned by its mtime, or None.

    The version query is what lets a browser cache the picture and still see a
    replacement: the shopkeeper uploads a new one, the mtime moves, the URL
    changes, and no page anywhere has to remember to bust a cache.
    """
    if not has_photo():
        return None
    try:
        v = int(photo_path().stat().st_mtime_ns)
    except OSError:
        v = 0
    return f"/store/shop/photo?v={v}"


# ---------------------------------------------------------------- the link --


def own_origin(request: Request) -> str:
    """This server's address as the browser reached it.

    The same rule `gawaah/storefront.py` applies to the shutter code, and for
    the same reason: the useful address is the one on the shop's wifi that the
    shopkeeper's laptop was opened at, which only the Host header knows. That
    header is client-controlled, so it is charset-checked and refused if it is
    anything but a plain host and port. Not imported from storefront because
    that module is mounted and edited independently of this one; the rule is
    short enough that two copies are cheaper than a private-name import.
    """
    host = (request.headers.get("host") or "").strip().lower()
    if not host or not re.fullmatch(r"[a-z0-9.\-]+(:[0-9]{1,5})?", host):
        raise AdminRefused(
            R_NO_HOST,
            f"this server cannot tell what address it was reached on "
            f"({host!r}), so it will not print a code pointing at a guess.")
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme
             or "http").strip().lower()
    if proto not in ("http", "https"):
        proto = "http"
    return f"{proto}://{host}"


def customer_url(origin: str, slug: Optional[str]) -> str:
    """The address a customer opens. Hash routing: App.tsx's router is untouched."""
    if slug:
        return f"{origin}/#/shop?s={slug}"
    return f"{origin}/#/shop"


def _is_loopback(origin: str) -> bool:
    return (urlsplit(origin).hostname or "").lower() in LOOPBACK_HOSTS


def _refuse_if_money(url: str) -> None:
    """INVARIANT 1, applied to a string that cannot violate it — on purpose.

    `_looks_like_upi` and the gateway-host list are the till's own; asked
    rather than re-implemented so the rule this code is refused by is the rule
    every other QR in the program is refused by.
    """
    up = _sa._till()
    looks = getattr(up, "_looks_like_upi", None)
    if callable(looks) and looks(url):
        raise AdminRefused(
            R_REFUSED_LINK,
            "that string is a UPI payload. This code opens a shop; it does "
            "not carry money.")
    host = (urlsplit(url).hostname or "").lower()
    hosts = tuple(getattr(up, "LINK_HOSTS", ()))
    if any(host == h or host.endswith("." + h) for h in hosts):
        raise AdminRefused(
            R_REFUSED_LINK,
            f"this code would point at {host!r}, a payment gateway host. A "
            f"shutter sticker points at the shop, never at money.")


def _qr_png(url: str, px: int) -> bytes:
    """`url` as a padded QR card, PNG bytes. The encoder is OpenCV's own."""
    import cv2
    import numpy as np

    _refuse_if_money(url)
    enc = cv2.QRCodeEncoder.create()
    q = enc.encode(url)
    q = (q * 255).astype(np.uint8) if q.max() <= 1 else q.astype(np.uint8)
    side = max(200, min(int(px), 1600))
    q = cv2.resize(q, (side, side), interpolation=cv2.INTER_NEAREST)
    pad = side // 12
    card = np.full((side + 2 * pad, side + 2 * pad), 255, np.uint8)
    card[pad:pad + side, pad:pad + side] = q
    ok, buf = cv2.imencode(".png", cv2.cvtColor(card, cv2.COLOR_GRAY2BGR))
    if not ok:
        raise AdminRefused(R_INTERNAL, "the code would not encode")
    return buf.tobytes()


def link_facts(request: Request) -> dict[str, Any]:
    """Everything the shopkeeper's page needs to show, copy and print the link."""
    slug = _sa.ensure_slug()
    doc = _sa.read_profile() or {}
    origin = own_origin(request)
    url = customer_url(origin, slug)
    _refuse_if_money(url)
    loopback = _is_loopback(origin)
    return {
        "ok": True,
        "settles_money": False,
        "configured": bool(doc),
        "slug": slug,
        "name": doc.get("name") or None,
        "url": url,
        "qr_url": "/shop/link/qr",
        "origin": origin,
        "reachable_from_a_phone": not loopback,
        "note": (
            "This address is the loopback interface, which means it points "
            "at whatever device opens it. A phone scanning this code will "
            "try to reach itself and fail. Open this till at the laptop's "
            "address on the shop's wifi and print the code from there."
            if loopback else
            "A phone on the same network can open this address."),
        "unique": slug is not None,
        "unique_note": (
            None if slug is not None else
            "This shop has no name yet, so the link is the plain storefront "
            "address — the same one every counter prints. Name the shop and "
            "the link gets a slug of its own."),
    }


@router.get("/shop/link")
def shop_link_ep(request: Request) -> JSONResponse:
    """This shop's own customer link, as text, with whether a phone can open it."""
    try:
        return JSONResponse(link_facts(request))
    except AdminRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/shop/link/qr")
def shop_link_qr_ep(request: Request, px: int = 700):
    """The same link as a printable code. Navigation only — see the module doc."""
    try:
        facts = link_facts(request)
        png = _qr_png(str(facts["url"]), px)
        return Response(png, media_type="image/png",
                        headers={"Cache-Control": "no-store",
                                 "X-Gawaah-Storefront-Url": str(facts["url"]),
                                 "Content-Disposition":
                                     'inline; filename="gawaah_shop_qr.png"'})
    except AdminRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/shop/link/renew")
def renew_link_ep(request: Request) -> JSONResponse:
    """A NEW slug, because the shopkeeper asked. Nothing else ever changes it.

    The cost is stated in the response rather than discovered on the shutter:
    every sticker carrying the old slug now opens the storefront with a
    "this link is for a different shop" notice. That is the correct behaviour
    for a link that was deliberately retired, and it is why this is a press
    and not a side effect of renaming.
    """
    try:
        doc = _sa.read_profile()
        if doc is None:
            raise AdminRefused(
                R_NO_PROFILE,
                "this shop has no name yet, so there is no link to renew. "
                "Name the shop first. Nothing was changed.")
        before = _sa.valid_slug(doc.get("slug"))
        after = _sa.make_slug(str(doc.get("name") or ""))
        doc["slug"] = after
        _sa._write_profile(doc)
        entry = _sa._audit("shop_link_renewed", slug_before=before,
                           slug_after=after)
        facts = link_facts(request)
        facts.update({
            "reason": "shop_link_renewed",
            "slug_before": before,
            "audit": entry,
            "audit_note": ("written to the shop's own catalogue chain"
                           if entry is not None else
                           "THE LINK WAS RENEWED BUT COULD NOT BE WRITTEN TO "
                           "THE AUDIT CHAIN"),
            "warning": (
                f"Every code already printed carries {before!r} and now opens "
                f"the storefront with a notice that it was made for a "
                f"different shop. Print the new one and replace the sticker."
                if before else
                "This is the shop's first link; nothing printed before it "
                "carried a slug."),
        })
        return JSONResponse(facts)
    except AdminRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


# ------------------------------------------------------- the open header --


def store_shop_facts(requested: Optional[str]) -> dict[str, Any]:
    """What a customer's phone learns about this shop, and whether it is the
    shop their link was printed for. Callable without HTTP.

    READ-ONLY, DELIBERATELY. This is the one path a stranger reaches, and a
    profile that predates slugs is NOT given one here — `ensure_slug` runs on
    the shopkeeper's side. Until then `slug` is None and any `?s=` is "other",
    which is the truthful answer: no code carrying a slug can have been printed
    for a shop that has none.
    """
    doc = _sa.read_profile() or {}
    slug = _sa.valid_slug(doc.get("slug"))
    name = doc.get("name") or None
    req = (requested or "").strip()
    if not req:
        link, matches = "none", True
    elif slug is not None and req == slug:
        link, matches = "own", True
    else:
        link, matches = "other", False
    return {
        "ok": True,
        "settles_money": False,
        "configured": bool(doc),
        "slug": slug,
        "name": name,
        "address": doc.get("address") or None,
        "photo_url": photo_url(),
        "requested": req[:REQUESTED_ECHO_MAX] or None,
        "matches": matches,
        "link": link,
        "note": (
            "This link is this shop's own." if link == "own" else
            "This link carries no shop name; it opens whichever counter "
            "served it." if link == "none" else
            (f"This link was made for a different shop ({req[:REQUESTED_ECHO_MAX]!r}). "
             f"The shop it has reached is {name!r}." if name else
             f"This link was made for a different shop "
             f"({req[:REQUESTED_ECHO_MAX]!r}), and the counter it has reached "
             f"has no name yet.")),
    }


@router.get("/store/shop")
def store_shop_ep(s: Optional[str] = None) -> JSONResponse:
    """The header a customer sees. Open: no session, and nothing private in it.

    Four fields a customer is being invited to read off the shutter — the
    slug, the name, the address, the picture — plus the verdict on the slug
    they arrived with. The phone number, the hours and every file path stay
    on `/shop/profile`, behind the lock. GET only; nothing here writes.
    """
    try:
        return JSONResponse(store_shop_facts(s))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/store/shop/photo")
def store_shop_photo_ep(v: Optional[str] = None):
    """The shop's picture, for the storefront header. Open, like the products'.

    `v` is the cache version `photo_url()` appends and is not read: it exists
    so the URL changes when the picture does. Cached for an hour on the same
    reasoning as `/store/photo/<sku>` — the file changes only when the
    shopkeeper replaces it, and when they do the URL moves with it.
    """
    del v
    try:
        if not has_photo():
            raise AdminRefused(
                R_NO_PHOTO,
                "this shop has no photograph yet. The shopkeeper adds one on "
                "the Your Shop screen.")
        data = photo_path().read_bytes()
        return Response(data, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"})
    except AdminRefused as exc:
        return _refusal(exc, status=404)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


# ----------------------------------------------------------- the photograph --


def _atomic_write(path: Path, data: bytes) -> None:
    """Write-then-rename, so a crash mid-write leaves the OLD picture intact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def set_shop_photo(body: dict[str, Any]) -> dict[str, Any]:
    """Store or remove the shop's picture. Callable without HTTP."""
    body = _sa._body_dict(body)
    data = _sa._photo_upload(body)
    if data is None:
        raise AdminRefused(
            _sa.R_NOTHING_TO_CHANGE,
            "this request carries no picture. Send `photo_b64` with an image, "
            "or an empty string to remove the one the shop has.")
    clearing = data == b""
    if clearing:
        had = has_photo()
        try:
            photo_path().unlink()
        except FileNotFoundError:
            pass
        stored = 0
    else:
        png = _sa._encoded_photo(data, edge_px=SHOP_PHOTO_EDGE_PX,
                                 cap_bytes=SHOP_PHOTO_CAP_BYTES)
        _atomic_write(photo_path(), png)
        had = True
        stored = len(png)

    entry = _sa._audit("shop_photo_cleared" if clearing else "shop_photo_set",
                       photo_bytes=stored)
    return {
        "ok": True,
        "settles_money": False,
        "reason": ("photo_cleared" if clearing else "photo_stored"),
        "has_photo": not clearing,
        "had_photo": had,
        "photo_bytes": stored,
        "photo_url": photo_url(),
        "edge_px": SHOP_PHOTO_EDGE_PX,
        "cap_bytes": SHOP_PHOTO_CAP_BYTES,
        "on_storefront": not clearing,
        "audit": entry,
        "audit_note": ("written to the shop's own catalogue chain"
                       if entry is not None else
                       "THE PICTURE WAS CHANGED BUT COULD NOT BE WRITTEN TO "
                       "THE AUDIT CHAIN"),
        "untouched": (
            "The name, the address, the phone, the hours and the link were "
            "not read and were not written. A picture of the shop is not part "
            "of any decision the counter makes."),
    }


@router.put("/shop/photo")
async def set_shop_photo_ep(request: Request) -> JSONResponse:
    """Give the shop a picture, or take it away. Nothing else moves."""
    try:
        body = await _sa._json_body(request)
        return JSONResponse(set_shop_photo(body))
    except AdminRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)
