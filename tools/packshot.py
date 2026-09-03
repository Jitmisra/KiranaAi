#!/usr/bin/env python3
"""Draw a synthetic product tile — a pack shot that is not a photograph.

WHY THIS FILE EXISTS. A demo counter with no pictures on it reads as a counter
that cannot hold pictures. The obvious fix — pull the brand's own pack shot off
the web — is wrong twice over: the page's Content-Security-Policy is
``default-src 'self'`` so nothing external loads at runtime anyway, and that
photography belongs to somebody. So the tiles are DRAWN here, locally, from a
name, a pack size and a colour family, and they never claim to be more than
that.

WHAT A TILE IS AND IS NOT
-------------------------
It IS: a plausible packet/bottle/carton silhouette in the brand's colour
family, with the product's name set in Latin and (where a shopkeeper would use
one) Devanagari, the pack size on it, on a neutral ground — a thing that reads
as a real product at tile size in a grid, which is what a shelf screen needs.

It is NOT: a photograph, a logo, a reproduction of anybody's trade dress, or
evidence of anything. No brand mark is drawn. The only thing taken from a real
brand is the COLOUR its packet is usually printed in, which is why the palette
below is spelt out as ordinary hex rather than hidden in an asset. Every file
written by `save_png` carries that statement in a PNG ``tEXt`` chunk, and every
tile carries a small "generated, not a photograph" line under the pack so it is
legible on the image itself rather than only in a report.

FONTS. macOS ships Kohinoor Devanagari and Arial; a Linux box usually ships
Noto. `_font` walks a list and takes the first that opens, so a machine with no
Devanagari face at all still produces a Latin-only tile instead of tofu boxes
or a crash — `has_devanagari()` says which happened, and the seeder prints it.
Complex-script shaping (the conjunct in बिस्कुट, the matra in पारले) needs
Pillow built with Raqm; `shaping_available()` reports that separately, because
a tile with unshaped Devanagari on it is worse than one with none.

Nothing here talks to the network, reads a shop, or knows what money is.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, features
    from PIL import PngImagePlugin
except Exception as exc:  # noqa: BLE001 - a missing Pillow is a named answer
    raise SystemExit(
        f"tools/packshot.py needs Pillow and it is not importable "
        f"({type(exc).__name__}: {exc}).\n"
        f"Install it into this repo's venv:  ./.venv/bin/python -m pip install pillow"
    ) from None


# --------------------------------------------------------------- provenance --

#: Written into every PNG this module saves, and said again on the tile itself.
#: A picture that travels without its provenance eventually gets used as one.
PROVENANCE = (
    "Synthetic product tile drawn by tools/packshot.py for the GAWAAH demo "
    "counter. NOT a photograph, not a logo, not a reproduction of any brand's "
    "packaging. Only the colour family is taken from the real product."
)

#: The line drawn on the tile, under the pack. Kept short enough to stay legible
#: at 96 px and quiet enough not to fight the product name.
ON_TILE_MARK = "generated image · not a photograph"


# -------------------------------------------------------------------- fonts --
#
# Ordered by preference, walked until one opens. The index picks a face out of a
# .ttc collection; Kohinoor.ttc holds Light/Regular/Medium/Semibold/Bold and 4
# is the heaviest, which is what a packet's brand line wants.

_LATIN_BOLD = (
    ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
    ("/System/Library/Fonts/Supplemental/Arial Black.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
    ("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf", 0),
    ("/Library/Fonts/Arial Bold.ttf", 0),
)
_LATIN_PLAIN = (
    ("/System/Library/Fonts/Supplemental/Arial.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf", 0),
    ("/Library/Fonts/Arial.ttf", 0),
)
_DEVANAGARI = (
    ("/System/Library/Fonts/Kohinoor.ttc", 4),
    ("/System/Library/Fonts/Kohinoor.ttc", 2),
    ("/System/Library/Fonts/Supplemental/DevanagariMT.ttc", 1),
    ("/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc", 1),
    ("/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf", 0),
    ("/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf", 0),
)

_CACHE: dict[tuple[str, int, int], Any] = {}


def _font(candidates: Sequence[tuple[str, int]], size: int):
    """The first face in `candidates` that opens at `size`, or None.

    None is a legitimate answer and every caller handles it: a machine with no
    Devanagari face draws a Latin-only tile, which is worse than the intended
    one and much better than a row of empty boxes or a traceback in the middle
    of seeding a shop.
    """
    for path, index in candidates:
        key = (path, index, size)
        if key in _CACHE:
            return _CACHE[key]
        try:
            f = ImageFont.truetype(path, size, index=index)
        except Exception:  # noqa: BLE001 - an absent font is not an error here
            continue
        _CACHE[key] = f
        return f
    return None


def has_devanagari() -> bool:
    """Whether a Devanagari face was found on this machine."""
    return _font(_DEVANAGARI, 24) is not None


def shaping_available() -> bool:
    """Whether Pillow can SHAPE complex scripts (Raqm/HarfBuzz).

    Without it Devanagari draws as a run of unjoined codepoints — matras beside
    their consonant instead of over it, conjuncts unformed. That is not a Hindi
    word, so `pack_shot` drops the Devanagari line rather than printing
    something no shopkeeper could read.
    """
    try:
        return bool(features.check("raqm"))
    except Exception:  # noqa: BLE001
        return False


def font_report() -> dict[str, Any]:
    """What this machine can actually draw, for the seeder to print."""
    lat = _font(_LATIN_BOLD, 24)
    dev = _font(_DEVANAGARI, 24)
    return {
        "latin": getattr(lat, "path", None),
        "devanagari": getattr(dev, "path", None),
        "shaping": shaping_available(),
        "devanagari_drawn": bool(dev is not None and shaping_available()),
    }


# ------------------------------------------------------------------ colours --

RGB = tuple[int, int, int]


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    return (int(round(a[0] + (b[0] - a[0]) * t)),
            int(round(a[1] + (b[1] - a[1]) * t)),
            int(round(a[2] + (b[2] - a[2]) * t)))


def _shade(c: RGB, t: float) -> RGB:
    """Darker for t<0, lighter for t>0. Used for seams, caps and gloss."""
    return _mix(c, (0, 0, 0) if t < 0 else (255, 255, 255), abs(t))


def _luma(c: RGB) -> float:
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _ink_for(bg: RGB) -> RGB:
    """Black or white text, whichever the packet's own colour can carry.

    Picked by luminance rather than by eye: a fixed white breaks on Parle-G
    yellow and a fixed black breaks on Thums Up red, and both look like a bug
    on the one screen whose job is to look finished.
    """
    return (26, 22, 20) if _luma(bg) > 150 else (255, 253, 250)


# The board the pack sits on. Warm neutral, not white: a white ground makes
# every white-ish packet float and gives the segmenter nothing to cut against.
GROUND_TOP: RGB = (243, 240, 235)
GROUND_BOTTOM: RGB = (226, 222, 214)


# ------------------------------------------------------------- the tile spec --


@dataclass(frozen=True)
class Pack:
    """One product, as much of it as a drawing needs.

    `shape` names the silhouette; `primary`/`accent` are the colour family the
    real packet is printed in. Nothing here is a logo and nothing is measured.
    """

    brand: str                      # the big line: "PARLE-G", "TATA SALT"
    variant: str = ""               # the small line under it: "Glucose Biscuits"
    size_text: str = ""             # "100 g", "1 L", "5 kg"
    devanagari: str = ""            # the same product as a shopkeeper says it
    shape: str = "packet"
    primary: RGB = (46, 92, 168)
    accent: RGB = (238, 196, 40)
    band: str = "middle"            # where the accent band sits
    tall: float = 1.0               # >1 makes the silhouette taller and narrower
    #: A printed device on the pack — see `_MOTIFS`. It is decoration to a
    #: reader and STRUCTURE to the embedder, which is the point: measured on
    #: this catalogue, thirty packs that differ only in colour and wording
    #: collide at the identity gate and seven of them were refused as
    #: indistinguishable (`gawaah/identity.py`, cosine over 1 - theta = 0.90).
    #: `gawaah/embedder2.py` band-limits a crop to 96 px and whitens away the
    #: directions that lighting moves, so a recoloured copy of one layout is
    #: very nearly the same descriptor. A different device is a different shape,
    #: and a different shape survives that.
    motif: str = "none"


# ------------------------------------------------------------ text helpers --


def _fit(draw: ImageDraw.ImageDraw, text: str, candidates: Sequence[tuple[str, int]],
         max_w: int, start: int, floor: int = 11):
    """The largest size in `candidates` at which `text` fits `max_w`, and its font.

    Shrink-to-fit rather than truncate: "AASHIRVAAD" cut to "AASHIRV…" on a
    packet reads as a rendering bug, while the same word two points smaller
    reads as a packet.
    """
    size = start
    while size > floor:
        f = _font(candidates, size)
        if f is None:
            return None, size
        if draw.textlength(text, font=f) <= max_w:
            return f, size
        size -= 1
    return _font(candidates, floor), floor


# ---------------------------------------------------------------- silhouettes --
#
# Each of these draws ONE pack into `d` inside the box (x0, y0, x1, y1) and
# returns the rectangle that text may be written in. They are deliberately
# crude solids with two or three tones: at 96 px in a grid — the size these are
# actually looked at — a shaded rectangle with a band across it and a word on it
# is indistinguishable from a photograph of a packet, and anything more elaborate
# is detail nobody sees.


def _rounded(d, box, r, fill, outline=None, w=2) -> None:
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=w)


def _crimp(d, x0, x1, y, height, colour, teeth=22) -> None:
    """The zigzag seal on a biscuit packet's top and bottom edge."""
    step = (x1 - x0) / float(teeth)
    pts = []
    for i in range(teeth + 1):
        pts.append((x0 + i * step, y + (0 if i % 2 == 0 else height)))
    pts += [(x1, y + height * 2), (x0, y + height * 2)]
    d.polygon(pts, fill=colour)


def _gloss(im: Image.Image, box, ground: Image.Image, strength: int = 46) -> None:
    """A soft vertical highlight, so plastic does not read as cardboard.

    MASKED TO THE PACK, and that is not a nicety. The highlight is drawn over
    the pack's bounding BOX, and a bottle or a sack fills nowhere near its own
    box — unmasked, the stripe ran off the shoulder of the oil bottle and lit
    up the background beside it, which reads as a smudge on the lens rather
    than as a shine on the plastic. `ground` is the board as it was before the
    pack was drawn, so anything that differs from it IS the pack.
    """
    x0, y0, x1, y1 = [int(v) for v in box]
    if x1 - x0 < 8 or y1 - y0 < 8:
        return
    w = x1 - x0
    layer = Image.new("L", (x1 - x0, y1 - y0), 0)
    ld = ImageDraw.Draw(layer)
    ld.rectangle([int(w * 0.10), 0, int(w * 0.26), y1 - y0], fill=strength)
    ld.rectangle([int(w * 0.30), 0, int(w * 0.35), y1 - y0], fill=strength // 2)
    layer = layer.filter(ImageFilter.GaussianBlur(w * 0.05))

    crop = (x0, y0, x1, y1)
    solid = ImageChops.difference(im.crop(crop), ground.crop(crop))
    solid = solid.convert("L").point(lambda v: 255 if v > 6 else 0)
    # Pulled in a little so the highlight does not bleed over the silhouette's
    # own edge, which would soften the outline the segmenter cuts on.
    solid = solid.filter(ImageFilter.MinFilter(5))
    im.paste(Image.new("RGB", layer.size, (255, 255, 255)), crop,
             ImageChops.multiply(layer, solid))


def _packet(im, d, box, p: Pack):
    x0, y0, x1, y1 = box
    seal = _shade(p.primary, -0.28)
    _crimp(d, x0, x1, y0, (y1 - y0) * 0.020, seal)
    _crimp(d, x0, x1, y1 - (y1 - y0) * 0.040, (y1 - y0) * 0.020, seal)
    body = (x0, y0 + (y1 - y0) * 0.045, x1, y1 - (y1 - y0) * 0.045)
    _rounded(d, body, 10, p.primary)
    return body


def _pouch(im, d, box, p: Pack):
    """A gusseted sack — atta, rice, dal. Wider at the foot, sealed at the top."""
    x0, y0, x1, y1 = box
    w = x1 - x0
    h = y1 - y0
    d.polygon([(x0 + w * 0.10, y0), (x1 - w * 0.10, y0),
               (x1 - w * 0.10, y0 + h * 0.07), (x0 + w * 0.10, y0 + h * 0.07)],
              fill=_shade(p.primary, -0.32))
    body = [(x0 + w * 0.06, y0 + h * 0.06), (x1 - w * 0.06, y0 + h * 0.06),
            (x1, y1), (x0, y1)]
    d.polygon(body, fill=p.primary)
    d.polygon([(x0 + w * 0.06, y0 + h * 0.06), (x0 + w * 0.22, y0 + h * 0.06),
               (x0 + w * 0.16, y1), (x0, y1)], fill=_shade(p.primary, -0.12))
    return (x0 + w * 0.10, y0 + h * 0.12, x1 - w * 0.06, y1 - h * 0.06)


def _bottle(im, d, box, p: Pack):
    x0, y0, x1, y1 = box
    w = x1 - x0
    h = y1 - y0
    cx = (x0 + x1) / 2
    neck_w = w * 0.24
    cap = _shade(p.accent, -0.16)
    _rounded(d, (cx - neck_w / 2 - w * 0.02, y0, cx + neck_w / 2 + w * 0.02,
                 y0 + h * 0.075), 5, cap)
    d.rectangle((cx - neck_w / 2, y0 + h * 0.07, cx + neck_w / 2, y0 + h * 0.155),
                fill=_shade(p.primary, -0.20))
    d.polygon([(cx - neck_w / 2, y0 + h * 0.15), (cx + neck_w / 2, y0 + h * 0.15),
               (x1 - w * 0.02, y0 + h * 0.27), (x0 + w * 0.02, y0 + h * 0.27)],
              fill=p.primary)
    _rounded(d, (x0 + w * 0.02, y0 + h * 0.25, x1 - w * 0.02, y1), 14, p.primary)
    return (x0 + w * 0.06, y0 + h * 0.33, x1 - w * 0.06, y1 - h * 0.05)


def _carton(im, d, box, p: Pack):
    """A box with one side face showing, so it reads as a carton and not a card."""
    x0, y0, x1, y1 = box
    w = x1 - x0
    h = y1 - y0
    side = w * 0.16
    d.polygon([(x1 - side, y0 + h * 0.05), (x1, y0), (x1, y1 - h * 0.05),
               (x1 - side, y1)], fill=_shade(p.primary, -0.30))
    d.rectangle((x0, y0 + h * 0.05, x1 - side, y1), fill=p.primary)
    return (x0 + w * 0.06, y0 + h * 0.12, x1 - side - w * 0.05, y1 - h * 0.05)


def _jar(im, d, box, p: Pack):
    x0, y0, x1, y1 = box
    w = x1 - x0
    h = y1 - y0
    _rounded(d, (x0 + w * 0.10, y0, x1 - w * 0.10, y0 + h * 0.15), 8,
             _shade(p.accent, -0.18))
    _rounded(d, (x0, y0 + h * 0.13, x1, y1), 22, p.primary)
    return (x0 + w * 0.07, y0 + h * 0.24, x1 - w * 0.07, y1 - h * 0.06)


def _bar(im, d, box, p: Pack):
    """A wrapped bar — bathing soap, a detergent cake, a dishwash bar."""
    x0, y0, x1, y1 = box
    w = x1 - x0
    h = y1 - y0
    inset = h * 0.20
    _rounded(d, (x0, y0 + inset, x1, y1 - inset), 16, p.primary)
    d.polygon([(x0, y0 + inset), (x0 + w * 0.07, y0 + inset * 0.35),
               (x0 + w * 0.07, y1 - inset * 0.35), (x0, y1 - inset)],
              fill=_shade(p.primary, -0.26))
    d.polygon([(x1, y0 + inset), (x1 - w * 0.07, y0 + inset * 0.35),
               (x1 - w * 0.07, y1 - inset * 0.35), (x1, y1 - inset)],
              fill=_shade(p.primary, -0.26))
    return (x0 + w * 0.10, y0 + inset + h * 0.05, x1 - w * 0.10, y1 - inset - h * 0.05)


def _tube(im, d, box, p: Pack):
    x0, y0, x1, y1 = box
    w = x1 - x0
    h = y1 - y0
    cx = (x0 + x1) / 2
    _rounded(d, (cx - w * 0.11, y0, cx + w * 0.11, y0 + h * 0.09), 4,
             _shade(p.accent, -0.20))
    d.polygon([(cx - w * 0.11, y0 + h * 0.085), (cx + w * 0.11, y0 + h * 0.085),
               (x1, y0 + h * 0.26), (x0, y0 + h * 0.26)], fill=p.primary)
    _rounded(d, (x0, y0 + h * 0.24, x1, y1), 12, p.primary)
    d.rectangle((x0, y1 - h * 0.05, x1, y1), fill=_shade(p.primary, -0.30))
    return (x0 + w * 0.06, y0 + h * 0.32, x1 - w * 0.06, y1 - h * 0.09)


def _tetra(im, d, box, p: Pack):
    """A tetra brick with the little straw stuck on the front."""
    x0, y0, x1, y1 = box
    w = x1 - x0
    h = y1 - y0
    d.polygon([(x0 + w * 0.08, y0), (x1 - w * 0.08, y0), (x1, y0 + h * 0.09),
               (x0, y0 + h * 0.09)], fill=_shade(p.primary, -0.28))
    d.rectangle((x0, y0 + h * 0.08, x1, y1), fill=p.primary)
    d.rectangle((x1 - w * 0.16, y0 + h * 0.16, x1 - w * 0.11, y1 - h * 0.16),
                fill=_shade(p.accent, 0.10))
    return (x0 + w * 0.07, y0 + h * 0.16, x1 - w * 0.22, y1 - h * 0.06)


def _sachet_strip(im, d, box, p: Pack):
    """A hanging strip of shampoo sachets, which is how a kirana sells them."""
    x0, y0, x1, y1 = box
    w = x1 - x0
    h = y1 - y0
    n = 4
    cell = h / n
    for i in range(n):
        top = y0 + i * cell
        c = p.primary if i % 2 == 0 else _shade(p.primary, -0.14)
        _rounded(d, (x0, top + cell * 0.06, x1, top + cell * 0.94), 7, c)
        d.rectangle((x0, top + cell * 0.94, x1, top + cell * 1.06),
                    fill=_shade(p.primary, -0.38))
    return (x0 + w * 0.08, y0 + cell * 0.20, x1 - w * 0.08, y0 + cell * 0.82)


_SHAPES = {
    "packet": _packet, "pouch": _pouch, "bottle": _bottle, "carton": _carton,
    "jar": _jar, "bar": _bar, "tube": _tube, "tetra": _tetra,
    "sachet": _sachet_strip,
}


# -------------------------------------------------------------------- motifs --
#
# Each draws one printed device into `box` on a transparent layer, which the
# caller masks to the pack's own silhouette before compositing. They take the
# BOX and not the text region on purpose: a device that stops where the writing
# starts is a border, and a border is the one thing every one of these packets
# would then have in common again.

def _m_stripes(d, box, p: Pack) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    c = (*_shade(p.primary, -0.18), 255)
    step = w * 0.16
    x = x0 - h
    while x < x1 + h:
        d.polygon([(x, y1), (x + step * 0.5, y1), (x + step * 0.5 + h, y0),
                   (x + h, y0)], fill=c)
        x += step


def _m_burst(d, box, p: Pack) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    cx, cy, r = (x0 + x1) / 2, y0 + h * 0.30, min(w, h) * 0.27
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*_shade(p.primary, 0.20), 255))
    d.ellipse((cx - r * 0.62, cy - r * 0.62, cx + r * 0.62, cy + r * 0.62),
              fill=(*_shade(p.accent, -0.10), 255))


def _m_panel(d, box, p: Pack) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    d.rounded_rectangle((x0 + w * 0.08, y0 + h * 0.46, x1 - w * 0.08, y1 - h * 0.06),
                        radius=w * 0.07, fill=(*_shade(p.primary, 0.22), 255))


def _m_waves(d, box, p: Pack) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    for i, t in enumerate((0.62, 0.76)):
        c = _shade(p.primary, -0.22 if i == 0 else 0.16)
        d.chord((x0 - w * 0.35, y0 + h * t, x1 + w * 0.35, y1 + h * 0.55),
                180, 360, fill=(*c, 255))


def _m_checker(d, box, p: Pack) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    n, top, size = 9, y1 - h * 0.16, w / 9.0
    for i in range(n):
        if i % 2:
            continue
        d.rectangle((x0 + i * size, top, x0 + (i + 1) * size, top + size),
                    fill=(*_shade(p.accent, -0.05), 255))


def _m_corner(d, box, p: Pack) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    d.polygon([(x1, y0), (x1, y0 + h * 0.42), (x1 - w * 0.55, y0)],
              fill=(*_shade(p.accent, -0.05), 255))
    d.polygon([(x0, y1), (x0, y1 - h * 0.30), (x0 + w * 0.40, y1)],
              fill=(*_shade(p.primary, -0.24), 255))


def _m_columns(d, box, p: Pack) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    c = (*_shade(p.primary, -0.16), 255)
    for i in range(5):
        left = x0 + w * (0.06 + i * 0.19)
        d.rectangle((left, y0 + h * 0.05, left + w * 0.075, y1 - h * 0.05), fill=c)


def _m_skyline(d, box, p: Pack) -> None:
    """A stepped block along the foot — a wholly different low-frequency shape
    from every other motif here, which is what the descriptor is looking at."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    c = (*_shade(p.primary, -0.26), 255)
    steps = (0.10, 0.22, 0.14, 0.30, 0.18)
    for i, up in enumerate(steps):
        left = x0 + w * (i / len(steps))
        d.rectangle((left, y1 - h * up, left + w / len(steps), y1), fill=c)


def _m_halo(d, box, p: Pack) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2, y0 + h * 0.52
    r = min(w, h) * 0.40
    d.ellipse((cx - r, cy - r, cx + r, cy + r),
              outline=(*_shade(p.primary, 0.28), 255), width=int(max(3, w * 0.045)))
    r2 = r * 0.62
    d.ellipse((cx - r2, cy - r2, cx + r2, cy + r2),
              outline=(*_shade(p.primary, 0.28), 255), width=int(max(2, w * 0.028)))


_MOTIFS = {
    "none": None, "stripes": _m_stripes, "burst": _m_burst, "panel": _m_panel,
    "waves": _m_waves, "checker": _m_checker, "corner": _m_corner,
    "columns": _m_columns, "skyline": _m_skyline, "halo": _m_halo,
}

#: Every device this module can print, for a caller wanting to spread them.
MOTIF_NAMES: tuple[str, ...] = tuple(k for k in _MOTIFS if k != "none")


# ------------------------------------------------------------------ the tile --


def pack_shot(p: Pack, px: int = 720, *, mark: bool = True,
              cut_out: bool = False) -> Image.Image:
    """One product tile, `px` square.

    Drawn at 2x and resampled down, because the crimped seals and the thin
    gloss stripes alias badly at 720 and a jagged edge is the one thing that
    makes a drawn tile look drawn.

    `cut_out=True` returns RGBA with the board removed and only the pack left,
    for a caller building a SHELF out of several of these. Without it, pasting
    tiles side by side puts each pack inside its own pale square and the result
    reads as a contact sheet rather than as a shelf — which is also what the
    region detector sees, and it proposed two regions out of seven. The honesty
    mark is dropped in this mode because it belongs to the tile, not to the
    packet, and the caller puts its own on the composed frame.
    """
    s = px * 2
    im = Image.new("RGB", (s, s), GROUND_TOP)
    d = ImageDraw.Draw(im)

    # The ground: a vertical ramp, so the pack has something to sit against and
    # the segmenter downstream has an edge to find.
    for y in range(s):
        d.line([(0, y), (s, y)], fill=_mix(GROUND_TOP, GROUND_BOTTOM, y / s))

    # Footprint shadow, drawn before the pack so the pack sits on it.
    shadow = Image.new("L", (s, s), 0)
    sd = ImageDraw.Draw(shadow)
    sd.ellipse((s * 0.14, s * 0.815, s * 0.86, s * 0.905), fill=88)
    shadow = shadow.filter(ImageFilter.GaussianBlur(s * 0.028))
    im.paste(Image.new("RGB", (s, s), (120, 112, 102)), (0, 0), shadow)

    # The pack box. `tall` narrows a 5 kg sack relative to a biscuit packet, so
    # a grid of these does not read as one shape in nine colours.
    h = s * 0.72
    w = min(s * 0.70, h / max(p.tall, 0.35) * 0.92)
    box = ((s - w) / 2, s * 0.10, (s + w) / 2, s * 0.10 + h)
    ground = im.copy()          # the board alone, so `_gloss` can find the pack
    text_box = _SHAPES.get(p.shape, _packet)(im, d, box, p)
    # The silhouette, taken the same way `_gloss` takes it: whatever now differs
    # from the board IS the pack. Captured before the gloss so the highlight's
    # soft edge cannot widen it.
    silhouette = ImageChops.difference(im, ground).convert("L").point(
        lambda v: 255 if v > 6 else 0)

    # THE PRINTED DEVICE, drawn on its own layer and then clipped to the pack.
    # Drawn straight onto the image it would run off the shoulder of a bottle
    # and past the taper of a sack; the silhouette above is exactly the region
    # ink may land on.
    motif = _MOTIFS.get(p.motif)
    if motif is not None:
        layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        motif(ImageDraw.Draw(layer), box, p)
        layer.putalpha(ImageChops.multiply(layer.getchannel("A"), silhouette))
        im.paste(layer, (0, 0), layer)

    _gloss(im, box, ground)

    # The accent band. It is what makes two packets in the same colour family
    # tell apart at a glance, so its position is per-product rather than fixed.
    tx0, ty0, tx1, ty1 = text_box
    if p.band != "none":
        band_h = (ty1 - ty0) * 0.30
        top = {"top": ty0 - band_h * 0.25,
               "middle": ty0 + (ty1 - ty0) * 0.36,
               "bottom": ty1 - band_h * 1.05}.get(p.band, ty0 + (ty1 - ty0) * 0.36)
        d.rectangle((box[0], top, box[2], top + band_h), fill=p.accent)
        ink = _ink_for(p.accent)
        band = (top, top + band_h)
    else:
        ink = _ink_for(p.primary)
        band = None

    im = im.resize((px, px), Image.LANCZOS)
    alpha = silhouette.resize((px, px), Image.LANCZOS) if cut_out else None
    d = ImageDraw.Draw(im)
    k = px / float(s)
    tx0, ty0, tx1, ty1 = [v * k for v in text_box]
    cx = int((tx0 + tx1) / 2)
    max_w = int(tx1 - tx0)

    # THE BRAND LINE goes on the band when there is one, because that is where a
    # real packet puts it and because the band is the one region whose colour is
    # known — text over the gloss stripe would sit on two tones at once.
    stack_top = ty0 + px * 0.02
    if band is not None:
        by0, by1 = band[0] * k, band[1] * k
        f, _size = _fit(d, p.brand, _LATIN_BOLD, max_w, int(px * 0.115))
        if f is not None:
            bb = d.textbbox((0, 0), p.brand, font=f)
            d.text((cx - (bb[2] - bb[0]) / 2 - bb[0],
                    (by0 + by1) / 2 - (bb[3] + bb[1]) / 2), p.brand,
                   font=f, fill=ink)
        # A band in the MIDDLE leaves the region under it for the rest; a band
        # at the bottom leaves the region above it. Getting this wrong is what
        # put the weight pill on top of the Devanagari line the first time.
        if p.band == "bottom":
            stack_top, stack_bottom = ty0 + px * 0.02, by0 - px * 0.02
        else:
            stack_top, stack_bottom = by1 + px * 0.030, ty1 - px * 0.02
    else:
        stack_bottom = ty1 - px * 0.02

    # EVERY REMAINING LINE IS MEASURED BEFORE ANY OF IT IS DRAWN, and the whole
    # stack is then centred in what is left. Drawing line by line off a running
    # cursor is what overlapped the pack size onto the Hindi name: the cursor
    # knew where it had got to and nothing knew where it had to stop.
    body_ink = _ink_for(p.primary)
    gap = px * 0.022
    blocks: list[tuple[str, Any, str, int, int]] = []   # kind, font, text, w, h

    if band is None:
        f, _ = _fit(d, p.brand, _LATIN_BOLD, max_w, int(px * 0.115))
        if f is not None:
            bb = d.textbbox((0, 0), p.brand, font=f)
            blocks.append(("text", f, p.brand, bb[2] - bb[0], bb[3] - bb[1]))
    if p.variant:
        f, _ = _fit(d, p.variant, _LATIN_PLAIN, max_w, int(px * 0.048))
        if f is not None:
            bb = d.textbbox((0, 0), p.variant, font=f)
            blocks.append(("text", f, p.variant, bb[2] - bb[0], bb[3] - bb[1]))
    # THE DEVANAGARI LINE, only when this machine can actually shape it. An
    # unshaped रा is not a Hindi syllable, and a shopkeeper reading one would be
    # right to distrust everything else on the screen.
    if p.devanagari and has_devanagari() and shaping_available():
        f, _ = _fit(d, p.devanagari, _DEVANAGARI, max_w, int(px * 0.062))
        if f is not None:
            bb = d.textbbox((0, 0), p.devanagari, font=f)
            blocks.append(("text", f, p.devanagari, bb[2] - bb[0], bb[3] - bb[1]))
    # THE PACK SIZE in a pill, which is the one number a shopkeeper looks for
    # when two packets differ only in weight.
    pad = px * 0.020
    if p.size_text:
        f, _ = _fit(d, p.size_text, _LATIN_BOLD, int(max_w * 0.8), int(px * 0.052))
        if f is not None:
            bb = d.textbbox((0, 0), p.size_text, font=f)
            blocks.append(("pill", f, p.size_text, bb[2] - bb[0],
                           int(bb[3] - bb[1] + pad * 1.6)))

    if blocks:
        total = sum(b[4] for b in blocks) + gap * (len(blocks) - 1)
        room = stack_bottom - stack_top
        # Overflow is squeezed out of the gaps first and then simply clipped at
        # the top of the region: a line hanging off the packet is a bug a viewer
        # sees, and a slightly tight stack is not.
        if total > room and len(blocks) > 1:
            gap = max(px * 0.006, gap - (total - room) / (len(blocks) - 1))
            total = sum(b[4] for b in blocks) + gap * (len(blocks) - 1)
        y = stack_top + max(0.0, (room - total) / 2)
        for kind, f, text, tw, th in blocks:
            bb = d.textbbox((0, 0), text, font=f)
            if kind == "pill":
                pill = (cx - tw / 2 - pad * 1.4, y, cx + tw / 2 + pad * 1.4,
                        y + th)
                d.rounded_rectangle(pill, radius=(pill[3] - pill[1]) / 2,
                                    fill=body_ink)
                d.text((cx - tw / 2 - bb[0], y + pad * 0.8 - bb[1]), text,
                       font=f, fill=p.primary)
            else:
                d.text((cx - tw / 2 - bb[0], y - bb[1]), text, font=f,
                       fill=body_ink)
            y += th + gap

    if alpha is not None:
        out = im.convert("RGBA")
        out.putalpha(alpha)
        return out

    # THE HONESTY LINE, on the tile and outside the pack so a crop of the pack
    # does not carry a caption into a bill. Quiet on purpose: it is a fact about
    # the picture, not a label on the product.
    if mark:
        f = _font(_LATIN_PLAIN, max(9, int(px * 0.026)))
        if f is not None:
            bb = d.textbbox((0, 0), ON_TILE_MARK, font=f)
            d.text((px / 2 - (bb[2] - bb[0]) / 2 - bb[0], px * 0.955 - bb[1]),
                   ON_TILE_MARK, font=f, fill=(163, 157, 148))
    return im


def png_bytes(im: Image.Image, *, optimise: bool = True) -> bytes:
    """The tile as PNG bytes, provenance in a tEXt chunk."""
    meta = PngImagePlugin.PngInfo()
    meta.add_text("Comment", PROVENANCE)
    meta.add_text("Software", "gawaah tools/packshot.py")
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=optimise, pnginfo=meta)
    return buf.getvalue()


def save_png(im: Image.Image, path) -> int:
    """Write the tile to `path`; return the byte count."""
    data = png_bytes(im)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return len(data)


if __name__ == "__main__":  # a look at one tile without running the seeder
    import sys
    from pathlib import Path

    out = Path(sys.argv[1] if len(sys.argv) > 1 else "packshot-sample.png")
    demo = Pack(brand="PARLE-G", variant="Glucose Biscuits", size_text="100 g",
                devanagari="पारले-जी बिस्कुट", shape="packet",
                primary=(240, 196, 46), accent=(196, 30, 45), band="middle")
    print(font_report())
    print(out, save_png(pack_shot(demo), out), "bytes")
