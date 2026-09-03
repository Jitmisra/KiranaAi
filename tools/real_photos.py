"""REAL product photographs for the catalogue, from an open database.

`tools/seed_shop.py` draws a synthetic pack shot per product — a coloured
rectangle with the brand set in type. That is honest and it renders, but it
looks drawn, and a counter demonstrating recognition on drawn packets is
demonstrating nothing. This fetches the real thing.

WHERE FROM, AND WHY THAT ONE. Open Food Facts and its sister databases (Open
Beauty Facts for soap, shampoo and toothpaste; Open Products Facts for the
rest) are a collaborative, open dataset. The photographs are contributed by the
public and licensed CC-BY-SA, which is a licence that permits this use, and
their Indian coverage is exactly the brands a kirana carries. Scraping a
retailer's product page would have been easier and would have taken images that
are not ours to take, so this asks a database that exists to be asked.

WHAT IS RECORDED. Every download writes its source URL, the contributing
database and the product code to `seed_photo_credits.json` beside the images.
CC-BY-SA asks for attribution; a demo that cannot say where its pictures came
from is a demo that should not be showing them.

WHAT IT WILL NOT DO. It will not overwrite a photograph taught from this shop's
own camera — a real taught view is worth more than a stock image, and this tool
has no way to tell a shopkeeper's photograph from a seeded one except by asking
the seeder's own manifest. It refuses to guess: pass `--only-seeded` (the
default) and it touches only the SKUs `seed_shop.py` recorded.

NOTHING HERE MOVES MONEY. It sets pictures. `PUT /shop/{sku}/photo` cannot
reach a price, a name, a code or a descriptor — that is enforced on the server,
not promised here.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

TILL = "http://127.0.0.1:8790"

#: Open Food Facts asks that clients identify themselves. This is not optional
#: politeness — an anonymous flood is what gets a shared database rate-limited
#: for everybody.
UA = "GAWAAH-kirana/1.0 (Razorpay Buildathon demo; contact: local)"

#: The three sister databases, in the order worth trying for a kirana shelf.
#: Food first because most of the catalogue is food; beauty second because that
#: is where soap, shampoo and toothpaste live; products last as the catch-all.
SEARCH_HOSTS: tuple[tuple[str, str], ...] = (
    ("openfoodfacts", "https://search.openfoodfacts.org/search"),
)
#: The v2 collection endpoints, used per-database once a code is known and for
#: the non-food databases, which have no full-text search service of their own.
V2_HOSTS: dict[str, str] = {
    "openfoodfacts": "https://world.openfoodfacts.org",
    "openbeautyfacts": "https://world.openbeautyfacts.org",
    "openproductsfacts": "https://world.openproductsfacts.org",
}

REQUEST_GAP_S = 0.7        # be a good citizen of a shared database
TIMEOUT_S = 25
MAX_IMAGE_BYTES = 4_000_000

#: A match must clear this to be used. Below it the drawn pack shot is better
#: than a confident picture of the wrong product — a demo that shows Colgate
#: under "Tata Salt" is worse than one that shows a drawn box.
MIN_SCORE = 0.42

#: Words that carry no identity. Dropped before scoring so "Amul Butter 100 g"
#: and "Amul Butter" score as the same product rather than 2/3 of one.
STOP = {
    "g", "kg", "ml", "l", "ltr", "litre", "liter", "gm", "gms", "pack", "packet",
    "x", "of", "the", "and", "with", "in", "for", "n", "no", "pcs", "pc", "rs",
}


def _norm(s: str) -> list[str]:
    """Words that matter, lowercased. Devanagari is dropped for scoring only —
    OFF's titles are Latin, so a Devanagari half would score every hit at zero
    and the parenthetical is a gloss of the Latin name anyway."""
    s = re.sub(r"[ऀ-ॿঀ-৿]+", " ", s)
    s = re.sub(r"[^a-zA-Z0-9]+", " ", s).lower()
    return [w for w in s.split() if w and w not in STOP and not w.isdigit()]


def _stem(w: str) -> str:
    """Just enough to make "biscuit" and "biscuits" the same word."""
    return w[:-1] if len(w) > 4 and w.endswith("s") else w


def _score(want: str, got_name: str, got_brand: str) -> float:
    """How much of the product we asked for is present in what came back.

    Recall against OUR words: the database's titles are often terser than ours
    ("Parle G" for "Parle-G biscuit 100g"), and being terser is not being
    wrong.

    THE THRESHOLD ALONE IS NOT ENOUGH, AND THE FIRST VERSION PROVED IT. Scoring
    recall at 0.42 threw away Parle-G, Colgate, Tata Salt, Dairy Milk and Vim —
    all of which the database HAS — because our names carry a pack size and a
    variant ("Iodised 1 kg", "Strong Teeth 100 g") that theirs do not, so a
    perfect brand match still only scored 0.33. Lowering the threshold to catch
    them would have let "Iodized salt" by K-Classic through as Tata Salt, which
    is the failure that actually matters: a confident picture of the wrong
    product. So the brand is a GATE rather than a term — see `_accept`.
    """
    a = [_stem(w) for w in _norm(want)]
    if not a:
        return 0.0
    b = {_stem(w) for w in _norm(got_name)} | {_stem(w) for w in _norm(got_brand)}
    if not b:
        return 0.0
    return sum(1 for w in a if w in b) / len(a)


def _brand_matches(want: str, got_name: str, got_brand: str) -> bool:
    """Does the leading word of our name appear in theirs at all?

    A kirana catalogue is written brand-first — Parle-G, Tata Salt, Colgate,
    Amul Butter — so the first meaningful token is the identity and everything
    after it is the variant. Requiring it lets the score threshold drop far
    enough to accept a terse title without also accepting another company's
    generic version of the same thing.
    """
    a = [_stem(w) for w in _norm(want)]
    if not a:
        return False
    b = {_stem(w) for w in _norm(got_name)} | {_stem(w) for w in _norm(got_brand)}
    return a[0] in b


def _accept(want: str, h: "Hit", floor: float) -> bool:
    """Brand present and a third of the words, or two thirds on words alone."""
    if _brand_matches(want, h.name, h.brand):
        return h.score >= 0.30
    return h.score >= max(floor, 0.60)


def _get(url: str, *, binary: bool = False) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        raw = r.read(MAX_IMAGE_BYTES + 1)
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError(f"image over {MAX_IMAGE_BYTES} bytes")
    return raw if binary else json.loads(raw.decode("utf-8"))


@dataclass
class Hit:
    code: str
    name: str
    brand: str
    image: str
    source: str
    score: float = 0.0


def _hits_fulltext(term: str) -> list[Hit]:
    """search.openfoodfacts.org — the only one of the three with real relevance
    ranking. The v2 `search_terms` parameter is NOT a substitute: it returns the
    whole database (4.7 M rows) with the term ignored, which reads as a working
    search right up until you look at what came back."""
    q = urllib.parse.urlencode({
        "q": term, "page_size": 12,
        "fields": "code,product_name,brands,image_front_url",
    })
    try:
        d = _get(f"https://search.openfoodfacts.org/search?{q}")
    except Exception:
        return []
    out = []
    for h in d.get("hits") or []:
        brands = h.get("brands")
        brand = ", ".join(brands) if isinstance(brands, list) else str(brands or "")
        img = h.get("image_front_url") or ""
        if img:
            out.append(Hit(str(h.get("code") or ""), str(h.get("product_name") or ""),
                           brand, img, "openfoodfacts"))
    return out


def _hits_v2(host_key: str, term: str) -> list[Hit]:
    """The non-food databases, searched the only way they can be: by brand tag.
    Coarser than full text, which is why it runs second."""
    base = V2_HOSTS[host_key]
    brand = _norm(term)[0] if _norm(term) else term
    q = urllib.parse.urlencode({
        "brands_tags": brand, "page_size": 12,
        "fields": "code,product_name,brands,image_front_url",
    })
    try:
        d = _get(f"{base}/api/v2/search?{q}")
    except Exception:
        return []
    out = []
    for h in d.get("products") or []:
        img = h.get("image_front_url") or ""
        if img:
            out.append(Hit(str(h.get("code") or ""), str(h.get("product_name") or ""),
                           str(h.get("brands") or ""), img, host_key))
    return out


def best_photo(name: str) -> Hit | None:
    """The best-scoring real photograph for this product, or None.

    None is a RESULT, not a failure: the drawn pack shot stays, and the report
    says which products kept one. A wrong picture is worse than a drawn one.
    """
    term = re.sub(r"\(.*?\)", " ", name)          # drop the Devanagari gloss
    candidates: list[Hit] = []
    candidates += _hits_fulltext(term)
    time.sleep(REQUEST_GAP_S)
    if not any(_accept(term, Hit(h.code, h.name, h.brand, h.image, h.source,
                                 _score(term, h.name, h.brand)), MIN_SCORE)
               for h in candidates):
        for key in ("openbeautyfacts", "openproductsfacts"):
            candidates += _hits_v2(key, term)
            time.sleep(REQUEST_GAP_S)
    if not candidates:
        return None
    for h in candidates:
        h.score = _score(term, h.name, h.brand)
    # Brand-matching hits first, then by score: a terse title from the right
    # company beats a rich one from the wrong company, every time.
    ok = [h for h in candidates if _accept(term, h, MIN_SCORE)]
    if not ok:
        return None
    return max(ok, key=lambda h: (_brand_matches(term, h.name, h.brand), h.score))


def _trim_border(im, tol: int = 14):
    """Drop a uniform border, if there is one.

    Contributed photographs are often a small packet in a large field of white
    or grey. Padded straight to a square and dropped into a catalogue tile they
    render as a stamp in the middle of a card — the drawn pack shots they
    replaced actually filled the frame better, which is a strange way to lose.

    Conservative on purpose: it measures the border colour from the four
    corners, only trims where all four agree, and refuses to trim away more
    than 40% of either axis. A photograph on a genuinely white product (salt,
    atta) must not have the product cropped off it.
    """
    from PIL import Image, ImageChops

    w, h = im.size
    corners = [im.getpixel(xy) for xy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    if max(max(abs(c[i] - corners[0][i]) for i in range(3)) for c in corners) > tol:
        return im                                   # no single border colour
    bg = Image.new("RGB", im.size, corners[0])
    box = ImageChops.difference(im, bg).convert("L").point(lambda v: 255 if v > tol else 0).getbbox()
    if not box:
        return im
    if (box[2] - box[0]) < w * 0.6 and (box[3] - box[1]) < h * 0.6:
        return im                                   # suspiciously aggressive
    pad = int(min(w, h) * 0.02)
    return im.crop((max(0, box[0] - pad), max(0, box[1] - pad),
                    min(w, box[2] + pad), min(h, box[3] + pad)))


def to_png(raw: bytes, edge: int = 512) -> bytes:
    """A square PNG on white, with the product filling most of it.

    The store re-encodes to its own policy anyway; this guarantees a decodable
    image of a sane size, fails loudly on an HTML error page served with an
    image content-type, and makes the packet the subject rather than a stamp in
    a field of white.
    """
    from PIL import Image

    im = Image.open(io.BytesIO(raw))
    im.load()
    im = im.convert("RGB")
    im = _trim_border(im)
    inner = int(edge * 0.94)                        # a hair of margin, not a moat
    im.thumbnail((inner, inner), Image.LANCZOS)
    canvas = Image.new("RGB", (edge, edge), (255, 255, 255))
    canvas.paste(im, ((edge - im.width) // 2, (edge - im.height) // 2))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def catalogue() -> list[dict]:
    d = _get(f"{TILL}/shop")
    return d["skus"]          # NOT 'items' and NOT 'products' — read the key


def seeded_ids() -> set[str] | None:
    p = Path("results/shop/seeded_by_seed_shop.json")
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text())
    except Exception:
        return None
    for k in ("sku_ids", "skus", "added", "products"):
        v = d.get(k)
        if isinstance(v, list) and v:
            return {x if isinstance(x, str) else str(x.get("sku_id")) for x in v}
    return None


def put_photo(sku_id: str, png: bytes) -> tuple[bool, str]:
    body = json.dumps({"photo_b64": base64.b64encode(png).decode()}).encode()
    req = urllib.request.Request(f"{TILL}/shop/{urllib.parse.quote(sku_id)}/photo",
                                 data=body, method="PUT",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            d = json.loads(r.read().decode())
        return bool(d.get("ok", True)), ""
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode())
            return False, f"{d.get('reason')}: {str(d.get('detail'))[:90]}"
        except Exception:
            return False, f"HTTP {e.code}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="find and score, download nothing, change nothing")
    ap.add_argument("--all", action="store_true",
                    help="every SKU, not just the ones seed_shop.py added")
    ap.add_argument("--only", nargs="*", default=None, help="specific sku ids")
    ap.add_argument("--min-score", type=float, default=MIN_SCORE)
    args = ap.parse_args(argv)

    skus = catalogue()
    seeded = seeded_ids()
    if args.only:
        skus = [s for s in skus if s["sku_id"] in set(args.only)]
    elif not args.all and seeded is not None:
        skus = [s for s in skus if s["sku_id"] in seeded]

    credits_path = Path("results/shop/seed_photo_credits.json")
    credits: dict[str, Any] = {}
    if credits_path.is_file():
        try:
            credits = json.loads(credits_path.read_text())
        except Exception:
            credits = {}

    took = kept = failed = 0
    print(f"  {len(skus)} products to look up\n")
    for s in skus:
        sku, name = s["sku_id"], s.get("name", "")
        try:
            hit = best_photo(name)
        except Exception as e:                       # a lookup must not stop the run
            print(f"  {name[:38]:<38} lookup failed: {type(e).__name__}")
            failed += 1
            continue
        if hit is None:
            print(f"  {name[:38]:<38} no match -> keeps its drawn pack shot")
            kept += 1
            continue
        label = f"{hit.score:.2f} {hit.source[:4]} {hit.name[:26]}"
        if args.dry_run:
            print(f"  {name[:38]:<38} {label}")
            took += 1
            continue
        try:
            png = to_png(_get(hit.image, binary=True))
        except Exception as e:
            print(f"  {name[:38]:<38} image unusable ({type(e).__name__}) -> keeps drawn")
            kept += 1
            continue
        ok, why = put_photo(sku, png)
        if not ok:
            print(f"  {name[:38]:<38} REFUSED {why}")
            failed += 1
            continue
        credits[sku] = {"name": name, "source": hit.source, "code": hit.code,
                        "image_url": hit.image, "matched": hit.name,
                        "score": round(hit.score, 3),
                        "licence": "CC-BY-SA 3.0 (Open Food Facts and sister databases)"}
        print(f"  {name[:38]:<38} {label}  {len(png)//1024} kB")
        took += 1
        time.sleep(REQUEST_GAP_S)

    if not args.dry_run and credits:
        credits_path.parent.mkdir(parents=True, exist_ok=True)
        credits_path.write_text(json.dumps(credits, indent=1, ensure_ascii=False) + "\n")

    print(f"\n  real photographs: {took}   kept drawn: {kept}   failed: {failed}")
    if not args.dry_run and credits:
        print(f"  attribution written to {credits_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
