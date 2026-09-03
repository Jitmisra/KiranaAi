#!/usr/bin/env python3
"""Re-embed the whole catalogue through the new embedder. One shot, honest.

    ./.venv/bin/python tools/migrate_gallery.py            # migrate results/shop
    GAWAAH_SHOP_DIR=/x ./.venv/bin/python tools/migrate_gallery.py

WHY THIS EXISTS. The 461-dim handcrafted descriptor was retired for
gawaah/embedder2.py (SqueezeNet, whitened — see its docstring for the measured
reasons). A vector written by one embedder means nothing to the other, so every
taught product must be re-embedded FROM ITS STORED PHOTOGRAPH — both enrolment
paths have always persisted the enrolment crop, which is what makes this
migration possible at all.

WHAT IT REFUSES, all-or-nothing:
  - a product with vectors but NO stored photograph. Migrating around it would
    silently drop its appearance; the tool stops and names it instead.
  - two products that COLLIDE under the new embedder's own metric. Every entry
    is re-admitted through the ordinary add_sku collision guard at the NEW
    gates, because ShopStore's own rule is right: entries admitted under one
    guard must not be smuggled past a different one. A collision here is
    information, not an obstacle — the two products genuinely look alike to
    the descriptor now doing the looking.

Nothing here touches prices, code bindings, footprints or names. Views added
with +VIEW after the original teach carry no stored pixels, so they cannot
survive re-embedding; each one lost is NAMED in the report so the shopkeeper
can re-add them at the counter (which takes seconds and produces views in the
new space, which is what those views are for anyway).

A timestamped backup of both files is written beside them before anything is
replaced.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gawaah import embedder2  # noqa: E402
from gawaah.shop_store import ShopStore  # noqa: E402


def store_dir() -> Path:
    return Path(os.environ.get(
        "GAWAAH_SHOP_DIR",
        str(Path(__file__).resolve().parent.parent / "results" / "shop")))


def two_orientations(img: np.ndarray) -> list[np.ndarray]:
    """The crop and its 180° turn — the same pair enrolment stores."""
    v1 = np.asarray(embedder2.embed(img), dtype=np.float64).ravel()
    v2 = np.asarray(embedder2.embed(cv2.rotate(img, cv2.ROTATE_180)),
                    dtype=np.float64).ravel()
    return [v1, v2]


def main() -> int:
    d = store_dir()
    cat_path = d / "catalog.json"
    ao_path = d / "appearance_only.json"
    stamp = time.strftime("%Y%m%d-%H%M%S")

    old_cat = json.loads(cat_path.read_text()) if cat_path.exists() else None
    old_dim = old_cat.get("dim") if old_cat else None
    if old_dim == embedder2.EMBED_DIM:
        print(f"catalog is already {embedder2.EMBED_DIM}-dim; nothing to do")
        return 0

    # ---- gather, and refuse before touching anything -----------------------
    problems: list[str] = []
    lost_views: list[str] = []

    mat_rows = []
    if old_cat:
        gates = old_cat.get("gates") or {}
        old = ShopStore(d, theta=gates.get("theta", 0.10),
                        phi=gates.get("phi", 0.90),
                        tau_mm=gates.get("tau_mm", 4.0),
                        phi_appearance_only=gates.get("phi_appearance_only", 0.92),
                        write_sidecar=False)
        for rec in old.all():
            photo = d / "photos" / f"{rec.sku_id}.png"
            if not photo.is_file():
                problems.append(f"{rec.sku_id}: vectors but no stored photo — "
                                f"re-teach it, then re-run")
                continue
            if len(rec.vectors) > 2:
                lost_views.append(f"{rec.sku_id}: {len(rec.vectors) - 2} added "
                                  f"view(s) had no stored pixels")
            mat_rows.append((rec, cv2.imread(str(photo))))

    ao = json.loads(ao_path.read_text()) if ao_path.exists() else {"skus": {}}
    ao_rows = []
    for sku, rec in sorted(ao.get("skus", {}).items()):
        vecs = rec.get("vectors") or []
        if not vecs:
            continue                     # code-only: no appearance to migrate
        raw = rec.get("photo")
        if not raw:
            problems.append(f"{sku}: sidecar vectors but no stored photo — "
                            f"re-teach it, then re-run")
            continue
        img = cv2.imdecode(np.frombuffer(base64.b64decode(raw), np.uint8),
                           cv2.IMREAD_COLOR)
        if img is None:
            problems.append(f"{sku}: stored photo does not decode")
            continue
        if len(vecs) > 2:
            lost_views.append(f"{sku}: {len(vecs) - 2} added view(s) had no "
                              f"stored pixels")
        ao_rows.append((sku, rec, img))

    if problems:
        print("REFUSING TO MIGRATE — nothing was changed:")
        for line in problems:
            print(f"  - {line}")
        return 1

    # ---- re-admit the mat store through the ordinary guard, in a scratch ---
    with tempfile.TemporaryDirectory() as tmp:
        fresh = ShopStore(Path(tmp))     # new gates come from identity.py
        for rec, img in mat_rows:
            res = fresh.add_sku(rec.sku_id, rec.name, int(rec.price_paise),
                                two_orientations(img), rec.footprint_mm,
                                photo_png=img)
            if not res.ok:
                print(f"REFUSING TO MIGRATE — {rec.sku_id!r} collides under "
                      f"the new embedder with {res.collides_with!r}: "
                      f"{res.message or res.reason}. The two genuinely look "
                      f"alike to the descriptor now doing the looking; teach "
                      f"a more distinct face of one of them first.")
                return 1
        new_cat = fresh.to_json()

    for sku, rec, img in ao_rows:
        rec["vectors"] = [v.tolist() for v in two_orientations(img)]

    # ---- write, with backups ----------------------------------------------
    if cat_path.exists():
        shutil.copy2(cat_path, d / f"catalog.pre-embedder2.{stamp}.json")
        cat_path.write_text(json.dumps(new_cat))
    if ao_path.exists():
        shutil.copy2(ao_path, d / f"appearance_only.pre-embedder2.{stamp}.json")
        ao_path.write_text(json.dumps(ao))

    print(f"migrated {len(mat_rows)} mat-taught and {len(ao_rows)} photo-taught "
          f"products to {embedder2.EMBED_DIM}-dim ({embedder2.NAME})")
    for line in lost_views:
        print(f"  note - {line}; re-add at the counter with + VIEW")
    print(f"backups: *.pre-embedder2.{stamp}.json in {d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
