"""DUKAAN — the shopkeeper's catalog on disk.

WHY THIS FILE EXISTS
====================
Today a price lives in ``results/shop.json`` as a bare ``{"sku": paise}`` map,
and the enrolled VECTORS live nowhere at all. A restart therefore forgets every
product the shopkeeper taught the counter: the price survives, the *identity*
does not, and a counter that knows a price for ``parle_g_200g`` but can no
longer recognise a Parle-G packet is a counter that ambers everything forever.

``ShopStore`` is the one place where the four things that make up "a product the
shopkeeper taught this counter" are kept together and kept CONSISTENT:

    name            what a human calls it
    price_paise     INTEGER PAISE, always (invariant 1)
    vectors         what the embedder saw, so identity survives a restart
    footprint_mm    the measured long edge, so the metric tiebreak survives too,
                    or None for a SKU taught from a photo with no mat in it
    taught_by       which of those two it was, so nobody has to infer it

Keeping them together is the entire point. Two files drift; one file cannot.
``to_gallery()`` and ``price_book()`` are projections of the SAME dict, so the
question "does the till know a price for everything it can recognise?" has one
answer by construction rather than by discipline.

THE TWO REFUSALS THIS FILE OWNS
===============================
1. MONEY (invariant 1). A price enters the system HERE. Everything downstream
   treats a price as an established fact, so this is the last door at which
   ``214.507`` can still be refused instead of silently becoming 214 paise. The
   door is ``money.paise()`` and the refusal is a ``MoneyError`` that is allowed
   straight through to the caller, never caught and turned into a default.
   A float, a bool, a string, a negative and a zero are all refused by name.

2. COLLISION (invariant 7). On every add we run
   ``Identifier.check_collision`` against the gallery as it stands, using the
   SAME theta/phi/tau_mm the till will later use. If the new item is inside both
   the appearance margin and the footprint tolerance of something already
   enrolled, the enrolment is REFUSED and the colliding sku is named.

   This is the cheapest possible moment to catch it. At enrolment the shopkeeper
   is still holding the packet and can take a disambiguation capture or give the
   two items different names; the cost of the catch is ten seconds. Caught at
   the till instead, the same pair is a permanent amber on a customer-facing
   screen — or worse, a coin-flip between two prices. We refuse, and we say
   which sku we refused against.

   The guard is never widened to make a demo look better. The gates are
   persisted INTO the catalog file, and reopening a store with different gates
   raises rather than silently re-admitting entries under looser thresholds.

TWO WAYS TO TEACH, AND THE WEAKER ONE IS LABELLED
=================================================
``footprint_mm`` may be None. That means the shopkeeper taught this product
from an ordinary photo — a packet on a table, no TAKHTI in frame — so there are
no millimetres to store and the metric tiebreak can never run for it. Every
record therefore carries ``taught_by``:

    TAUGHT_ON_MAT       mat_measured      a lock gave a real long edge
    TAUGHT_FROM_PHOTO   appearance_only   no scale; judged on looks alone

It is derived from ``footprint_mm`` rather than stored beside it, so the two
cannot drift into disagreeing; it is nonetheless WRITTEN OUT to the catalog, so
a UI, an audit line or a human reading the JSON sees the word rather than
having to know that null means weak. A file whose ``taught_by`` contradicts its
``footprint_mm`` is a hand-edit and is refused on load.

Appearance-only is a first-class mode and a measurably weaker one: it loses a
discriminator, so ``gawaah.identity`` judges it at the higher
``PHI_APPEARANCE_ONLY`` bar and this store admits it under a STRICTER collision
guard (size cannot rescue a pair when one side has no size). ``taught_by``
exists so a shopkeeper can find the weak ones later and re-teach them on the
mat, which is the only thing that actually fixes them.

WHAT IS ON DISK
===============
    <dir>/catalog.json          the whole catalog, canonical JSON, sha256'd
    <dir>/shop.json             {sku: paise} — regenerated, for live_app.py
    <dir>/photos/<sku>.png      the enrolment photo, downscaled and capped

``catalog.json`` is the source of truth. ``shop.json`` is a DERIVED sidecar in
exactly the legacy format ``gawaah/live_app.py`` already reads, so pointing
``GAWAAH_DATA_DIR`` at a ShopStore directory makes the live money app read this
catalog's prices with no code change. It is rewritten on every mutation and is
never read back as truth — if it exists and disagrees with the catalog, that is
a hand-edit that would price a sale differently from the gallery that
recognised it, and loading raises instead of picking a winner.

A ``shop.json`` with NO catalog beside it is somebody else's file — the legacy
``results/shop.json`` this repo ships with four prices in it — and the first
save REFUSES rather than overwriting four real prices with one enrolment.
``write_sidecar=False`` is the escape hatch, and it leaves the legacy file
untouched.

SIZE COST OF JSON VECTORS, STATED PLAINLY
=========================================
Vectors are stored as JSON lists of floats. Python's ``repr`` for a float is the
shortest string that round-trips, so this is EXACT — a vector written and read
back is bit-identical, which is tested. It is also fat: a general float64 costs
about 20 bytes as text against 8 bytes as raw IEEE-754, roughly 2.5x.

Measured on this machine (see tests/test_shop_store.py::
test_size_cost_of_json_vectors_is_measured_and_stated), a shop of 24 SKUs with
4 enrolled views each at 256 dimensions — 24576 float64 values — writes a
catalog.json of about 0.5 MB. That is the honest ceiling for a kirana counter:
half a megabyte of text, parsed once at boot. Below roughly a thousand SKUs the
simplicity of one greppable, diffable, git-friendly file is worth more than the
2.5x, and above it this format should be replaced by an .npy sidecar rather than
patched. That threshold is stated so the next person does not have to guess it.

PHOTOS
======
The enrolment photo exists so the UI can show what was taught — a name and a
price are not enough for a shopkeeper to spot that he enrolled the wrong packet.
It is NOT part of identity: nothing here ever re-embeds it, and deleting every
photo changes no decision the counter makes.

It is downscaled to a LONG EDGE OF 256 px and re-encoded PNG, and the encoded
result is capped at 128 KiB; if a pathological image is still over the cap the
store shrinks it further (192, 128, 96, 64 px) and raises if even 64 px cannot
fit. Input is refused above 8 MiB before decode, so a decompression bomb never
gets to allocate.

INVARIANT 4 NOTE, HONESTLY: this module cannot verify that the pixels it is
handed came from the rectified 840x1188 mat buffer. It stores what it is given.
The obligation to hand it only the mat crop belongs to the caller that owns the
frame grab, and is stated here so the next reader does not mistake this file's
silence for a guarantee.

WHAT THIS FILE NEVER DOES
=========================
It never embeds (it holds no embedder and constructs none), never identifies,
and never settles money. Recognition proposes, thresholds dispose, and only a
signature-verified webhook turns anything green (invariant 2). A ShopStore is a
filing cabinet with two locks on it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import numpy as np

from gawaah.identity import (
    DEFAULT_PHI,
    DEFAULT_TAU_MM,
    DEFAULT_THETA,
    MODE_APPEARANCE_ONLY,
    PHI_APPEARANCE_ONLY,
    Collision,
    Gallery,
    Identifier,
    IdentityError,
    as_vector,
)
from gawaah.ledger import canonical
from gawaah.money import MoneyError, from_rupees_str, paise

CATALOG_FORMAT = 2
CATALOG_NAME = "catalog.json"
SIDECAR_NAME = "shop.json"
PHOTO_DIR = "photos"

#: sku ids are filenames (photos/<sku>.png) and json keys. Constrain them once,
#: here, so no other layer has to wonder whether '../../etc/passwd' is a sku.
SKU_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
NAME_MAX_CHARS = 96

#: Photo policy. Stated as constants because "downscaled" and "capped" are
#: claims, and a claim with no number in it is not a claim.
PHOTO_EDGE_PX = 256                     # long edge after downscale
PHOTO_CAP_BYTES = 128 * 1024            # encoded PNG must fit in 128 KiB
PHOTO_EDGE_LADDER = (256, 192, 128, 96, 64)
PHOTO_INPUT_CAP_BYTES = 8 * 1024 * 1024  # refuse before decode

ACTION_ADDED = "added"
ACTION_REPLACED = "replaced"
ACTION_REFUSED = "refused"

REASON_ADDED = "added"
REASON_REPLACED = "replaced"
REASON_COLLISION = "collision"

PHOTO_STORED = "stored"
PHOTO_RETAINED = "retained"
PHOTO_NONE = "none"

#: How a SKU came to be in this catalog. Exactly two ways, and the difference
#: is not cosmetic: one of them has millimetres and one of them never will.
TAUGHT_ON_MAT = "mat_measured"
#: Deliberately the same string as gawaah.identity.MODE_APPEARANCE_ONLY, so a
#: UI has ONE word for "weak" whether it is looking at a stored record or at a
#: live identification.
TAUGHT_FROM_PHOTO = MODE_APPEARANCE_ONLY

TAUGHT_BY: tuple[str, ...] = (TAUGHT_ON_MAT, TAUGHT_FROM_PHOTO)


class ShopError(ValueError):
    """The catalog on disk is not a catalog, or the caller asked for something
    the catalog cannot honestly do. Distinct from MoneyError (a bad price) and
    from IdentityError (a bad vector), both of which are allowed straight
    through so the caller sees the real diagnosis."""


# --------------------------------------------------------------- the money door

def price_from_rupees(text: str) -> int:
    """Parse a human-typed RUPEE STRING into integer paise, or refuse.

    This is the only place in this module that accepts anything other than an
    integer number of paise, and it is deliberately a separate, explicitly named
    function rather than a polymorphic argument to ``add_sku``: ``"2000"`` is
    ambiguous — twenty rupees or two thousand — and an ambiguous money literal
    is exactly the bug this codebase refuses to have. Callers must choose.

    The parse happens in STRING SPACE inside ``money.from_rupees_str``; no float
    is constructed at any point, because ``float("214.507")`` has already lost
    before any check could run. Sub-paisa precision is a refusal, not a round.
    """
    if isinstance(text, bool) or not isinstance(text, str):
        raise MoneyError(
            f"a rupee string is required, got {text!r} "
            f"({type(text).__name__}); pass integer paise to add_sku instead"
        )
    return _require_positive_paise(from_rupees_str(text))


def _require_positive_paise(value: Any) -> int:
    """money.paise() plus the two bounds a *catalog* price additionally has.

    ``paise()`` refuses float, bool and non-integers — that is invariant 1 and
    it is the important half. On top of it a shelf price has a sign and a
    floor:

      negative — a shop does not pay you to take the packet. Almost always a
                 stray '-' or a subtraction that escaped upstream.
      zero     — refused too, which is STRICTER than asked for. An empty price
                 field parsed as 0 would enrol an item that silently bills
                 nothing, and 'free' is not a price a counter should infer from
                 a blank box. A genuinely free item is a removal, not a 0.
    """
    p = int(paise(value))
    if p < 0:
        raise MoneyError(f"a negative price is not a price: {p} paise")
    if p == 0:
        raise MoneyError(
            "a price of 0 paise is not a price — an empty field parses as 0 and "
            "would silently bill nothing; remove the SKU instead"
        )
    return p


# ------------------------------------------------------------------- the record

@dataclass(eq=False, frozen=True)
class SkuRecord:
    """One product the shopkeeper taught this counter.

    ``vectors`` is a read-only (k, dim) float64 block. Read-only on purpose: a
    caller that mutated it in place would change what the till recognises
    without changing anything on disk, and the next restart would silently
    disagree with the running process.
    """

    sku_id: str
    name: str
    price_paise: int
    vectors: np.ndarray
    footprint_mm: Optional[float]
    photo: str | None = None
    photo_bytes: int = 0

    @property
    def n_views(self) -> int:
        return int(self.vectors.shape[0])

    @property
    def dim(self) -> int:
        return int(self.vectors.shape[1])

    @property
    def is_appearance_only(self) -> bool:
        """No footprint means no size check, ever, for this product."""
        return self.footprint_mm is None

    @property
    def taught_by(self) -> str:
        """DERIVED, not stored: one source of truth cannot disagree with
        itself. It is written to the catalog anyway so that a reader does not
        have to know that ``"footprint_mm": null`` is the weak case."""
        return TAUGHT_FROM_PHOTO if self.footprint_mm is None else TAUGHT_ON_MAT

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "price_paise": int(self.price_paise),
            "footprint_mm": (None if self.footprint_mm is None
                             else float(self.footprint_mm)),
            "taught_by": self.taught_by,
            "vectors": self.vectors.tolist(),
            "photo": self.photo,
            "photo_bytes": int(self.photo_bytes),
        }


@dataclass(frozen=True)
class Result:
    """What ``add_sku``/``remove`` actually did, in words the UI can render.

    A refusal is a RESULT, not an exception, because a collision is a normal
    thing for a shopkeeper to do (two flavours of the same 50g packet) and the
    surface needs to name the offender rather than show a stack trace. A bad
    PRICE, by contrast, raises: it is a broken caller, not a shopkeeper who
    needs a better photo.
    """

    ok: bool
    sku_id: str
    action: str
    reason: str
    message: str = ""
    replaced: bool = False
    previous_price_paise: Optional[int] = None
    collides_with: Optional[str] = None
    colliding: tuple[str, ...] = ()
    similarity: float = 0.0
    footprint_delta_mm: Optional[float] = None
    photo_action: str = PHOTO_NONE
    photo_bytes: int = 0
    #: TAUGHT_ON_MAT or TAUGHT_FROM_PHOTO — what the surface should SAY it just
    #: did. An enrolment that quietly produced a weaker SKU than the shopkeeper
    #: thought is the failure this field exists to prevent.
    taught_by: str = TAUGHT_ON_MAT

    @property
    def is_appearance_only(self) -> bool:
        return self.taught_by == TAUGHT_FROM_PHOTO

    def __bool__(self) -> bool:
        return self.ok

    def to_audit(self) -> dict:
        """Plain-JSON fields for Ledger.append(**fields)."""
        return {
            "sku_id": self.sku_id,
            "action": self.action,
            "reason": self.reason,
            "ok": self.ok,
            "replaced": self.replaced,
            "collides_with": self.collides_with,
            "colliding": list(self.colliding),
            "similarity": round(self.similarity, 6),
            "footprint_delta_mm": (
                None
                if self.footprint_delta_mm is None
                else round(self.footprint_delta_mm, 3)
            ),
            "photo_action": self.photo_action,
            "photo_bytes": int(self.photo_bytes),
            "taught_by": self.taught_by,
        }


def _never_embeds(_crop: Any) -> Any:
    """The embedder the store does not have.

    ``Identifier`` requires a callable because identification is impossible
    without one. The store only ever calls ``check_collision``, which compares
    vectors it was already handed and never embeds anything — so passing this
    is not a stub standing in for missing work, it is a statement that the
    store is on the enrolment path only. If it is ever reached, that is a real
    bug and it says so instead of returning zeros.
    """
    raise ShopError(
        "ShopStore holds no embedder: it guards enrolments, it never identifies"
    )


# -------------------------------------------------------------------- the store

class ShopStore:
    """The shopkeeper's catalog, persisted, with the two locks on the door.

        store = ShopStore("results/shop")
        store.add_sku("parle_g_200g", "Parle-G 200g", 2000, [v1, v2], 118.4,
                      photo_png=crop)
        ident = Identifier(store.to_gallery(), embed_fn)
        service_price_book = store.price_book()
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        theta: float = DEFAULT_THETA,
        phi: float = DEFAULT_PHI,
        tau_mm: float = DEFAULT_TAU_MM,
        phi_appearance_only: float = PHI_APPEARANCE_ONLY,
        write_sidecar: bool = True,
        photo_edge_px: int = PHOTO_EDGE_PX,
        photo_cap_bytes: int = PHOTO_CAP_BYTES,
    ) -> None:
        self.dir = Path(directory)
        self.theta = float(theta)
        self.phi = float(phi)
        self.tau_mm = float(tau_mm)
        self.phi_appearance_only = float(phi_appearance_only)
        self.write_sidecar = bool(write_sidecar)
        self.photo_edge_px = int(photo_edge_px)
        self.photo_cap_bytes = int(photo_cap_bytes)
        if self.theta < 0.0 or self.tau_mm < 0.0:
            raise ShopError("theta and tau_mm must be >= 0")
        if self.phi_appearance_only < self.phi:
            raise ShopError(
                f"phi_appearance_only={self.phi_appearance_only} is below "
                f"phi={self.phi}: a SKU with no size check must not be the "
                f"easier one to match (invariant 7)"
            )
        self._skus: dict[str, SkuRecord] = {}
        self._sidecar_is_ours = True
        self.reload()

    # -- paths --------------------------------------------------------------
    @property
    def catalog_path(self) -> Path:
        return self.dir / CATALOG_NAME

    @property
    def sidecar_path(self) -> Path:
        return self.dir / SIDECAR_NAME

    def photo_path(self, sku_id: str) -> Path:
        return self.dir / PHOTO_DIR / f"{_require_sku_id(sku_id)}.png"

    def __repr__(self) -> str:
        return (
            f"ShopStore(dir={str(self.dir)!r}, n={len(self._skus)}, "
            f"dim={self.dim}, theta={self.theta}, phi={self.phi}, "
            f"phi_appearance_only={self.phi_appearance_only}, "
            f"tau_mm={self.tau_mm}, "
            f"appearance_only={len(self.appearance_only_skus())})"
        )

    # -- reading ------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._skus)

    def __contains__(self, sku_id: object) -> bool:
        return sku_id in self._skus

    def skus(self) -> tuple[str, ...]:
        """Sorted, never insertion-ordered, so two shops built in a different
        order are the same shop."""
        return tuple(sorted(self._skus))

    def get(self, sku_id: str) -> Optional[SkuRecord]:
        return self._skus.get(sku_id)

    def all(self) -> tuple[SkuRecord, ...]:
        return tuple(self._skus[s] for s in self.skus())

    def appearance_only_skus(self) -> tuple[str, ...]:
        """The SKUs taught from a photo with no mat, sorted.

        The list a surface needs in order to say "these N products have no size
        check", and the worklist a shopkeeper works through with the mat when
        he wants them fixed.
        """
        return tuple(s for s in self.skus() if self._skus[s].is_appearance_only)

    def taught_by(self, sku_id: str) -> Optional[str]:
        """TAUGHT_ON_MAT / TAUGHT_FROM_PHOTO, or None for an unknown sku."""
        rec = self._skus.get(sku_id)
        return None if rec is None else rec.taught_by

    def price_paise(self, item_id: str) -> Optional[int]:
        """``paisa.PriceBook`` protocol: an unknown item is None, never a guess
        and never 0. None is AMBER downstream, which is a correct outcome.

        Because this method exists with this signature, a ShopStore may be
        handed straight to ``paisa.build_service(price_book=...)``.
        """
        rec = self._skus.get(item_id)
        return None if rec is None else int(rec.price_paise)

    @property
    def dim(self) -> Optional[int]:
        for rec in self._skus.values():
            return rec.dim
        return None

    # -- the two projections, which cannot disagree -------------------------
    def to_gallery(self) -> Gallery:
        """Build a ``gawaah.identity.Gallery`` ready to hand to an Identifier.

        Rebuilt on demand rather than cached: a cached gallery is one more thing
        that can be stale relative to the prices, and rebuilding 24 SKUs is
        microseconds.
        """
        g = Gallery()
        for rec in self.all():
            # None goes through UNCHANGED. A 0.0 or a "best guess" here would
            # be a size the metric tiebreak then compares against, and the
            # counter would silently gate an appearance-only SKU on a
            # measurement nobody ever took.
            g.enroll(rec.sku_id, rec.vectors, rec.footprint_mm)
        return g

    def price_map(self) -> dict[str, int]:
        """The bare ``{sku: paise}`` mapping — the legacy ``shop.json`` shape,
        and what ``paisa.DictPriceBook`` takes."""
        return {s: int(self._skus[s].price_paise) for s in self.skus()}

    def price_book(self):
        """A ``paisa.DictPriceBook`` over this catalog.

        ``paisa`` is imported lazily: it pulls in FastAPI, and a catalog should
        be readable by a tool that has no interest in serving HTTP.
        """
        from gawaah.paisa import DictPriceBook

        return DictPriceBook(self.price_map())

    # -- writing ------------------------------------------------------------
    def add_sku(
        self,
        sku_id: str,
        name: str,
        price_paise: int,
        vectors: Iterable[Any],
        footprint_mm: Optional[float],
        photo_png: Any = None,
    ) -> Result:
        """Teach the counter one product. Returns a Result; raises on bad money.

        ``footprint_mm=None`` teaches it APPEARANCE-ONLY: from a photo with no
        mat, so there is no long edge to store and no size check will ever run
        for this product. It is deliberately spelled as an explicit None rather
        than given a default, because a caller that forgot to measure and a
        caller that could not measure must not produce the same record by
        accident. The Result says which happened, in words, in
        ``taught_by`` and in ``message``.

        Order of checks is deliberate and is the order of increasing cost:

          1. shape   — sku id, name, footprint. Cheap, and a bad id is a
                       filesystem question, not a catalog question.
          2. MONEY   — before anything is computed, because a price that cannot
                       be trusted must not cause a photo to be written.
          3. vectors — dimension must match the shop.
          4. COLLISION against the gallery as it stands, minus this sku's own
             outgoing entry (an item must never collide with itself).
          5. photo, then disk. Nothing touches disk until every gate has passed,
             so a refused enrolment leaves not one byte behind.

        Re-adding an existing sku_id REPLACES it and says so
        (``Result.action == "replaced"``, ``previous_price_paise`` filled in).
        It never appends a second entry: the store is a dict keyed by sku_id, so
        doubling is not merely avoided, it is unrepresentable.

        ``photo_png=None`` on a replace RETAINS the existing photo rather than
        deleting it, so correcting a price does not blank the picture. Pass
        ``clear_photo()`` to actually remove one.
        """
        self._assert_writable()
        sku = _require_sku_id(sku_id)
        clean_name = _require_name(name)
        fp = _optional_mm(footprint_mm)
        price = _require_positive_paise(price_paise)   # MoneyError flies free
        taught = TAUGHT_FROM_PHOTO if fp is None else TAUGHT_ON_MAT

        rows = _require_vectors(vectors)
        block = np.vstack(rows)
        shop_dim = self.dim
        if shop_dim is not None and block.shape[1] != shop_dim:
            raise IdentityError(
                f"{sku!r}: vectors are dimension {block.shape[1]}, this shop is "
                f"{shop_dim} — a shop cannot hold two embedders"
            )

        previous = self._skus.get(sku)
        collision = self._check_collision(sku, rows, fp)
        if collision.collides:
            if collision.footprint_delta_mm is None:
                why = (
                    f"cosine {collision.similarity:.4f} >= "
                    f"{1.0 - self.theta:.4f}, and one of the two has no "
                    f"footprint, so no size check can ever tell them apart. "
                    f"Teaching this one on the mat would not help either "
                    f"unless the OTHER one has a footprint too."
                )
            else:
                why = (
                    f"cosine {collision.similarity:.4f} >= "
                    f"{1.0 - self.theta:.4f} and footprint delta "
                    f"{collision.footprint_delta_mm:.2f} mm <= "
                    f"{self.tau_mm:.2f} mm."
                )
            return Result(
                ok=False,
                sku_id=sku,
                action=ACTION_REFUSED,
                reason=REASON_COLLISION,
                message=(
                    f"refused: {sku!r} is indistinguishable from "
                    f"{collision.sku_id!r} — {why} Both would be permanently "
                    f"amber at the till. Take a disambiguation capture, or "
                    f"enrol them as one sku."
                ),
                collides_with=collision.sku_id,
                colliding=collision.colliding,
                similarity=collision.similarity,
                footprint_delta_mm=collision.footprint_delta_mm,
                taught_by=taught,
            )

        photo_rel: str | None = None
        photo_action = PHOTO_NONE
        photo_size = 0
        encoded: bytes | None = None
        if photo_png is not None:
            encoded = self._encode_photo(photo_png)
            photo_rel = f"{PHOTO_DIR}/{sku}.png"
            photo_action = PHOTO_STORED
            photo_size = len(encoded)
        elif previous is not None and previous.photo:
            photo_rel = previous.photo
            photo_action = PHOTO_RETAINED
            photo_size = previous.photo_bytes

        block = np.ascontiguousarray(block, dtype=np.float64)
        block.setflags(write=False)
        record = SkuRecord(
            sku_id=sku,
            name=clean_name,
            price_paise=price,
            vectors=block,
            footprint_mm=fp,
            photo=photo_rel,
            photo_bytes=photo_size,
        )

        if encoded is not None:
            self._write_photo(sku, encoded)
        self._skus[sku] = record
        self._save()

        # Said in words, on every single enrolment, because a shopkeeper who
        # does not know he taught the weak kind cannot choose to fix it.
        how = (
            f" MAT-MEASURED at {fp:.1f} mm: the size check protects it."
            if fp is not None else
            " APPEARANCE-ONLY: taught from a photo with no mat, so there is no "
            "size check and it is easier to confuse with something that looks "
            "like it. Re-teach it on the TAKHTI to get one."
        )
        if previous is None:
            return Result(
                ok=True, sku_id=sku, action=ACTION_ADDED, reason=REASON_ADDED,
                message=f"added {sku!r} at {price} paise.{how}",
                photo_action=photo_action, photo_bytes=photo_size,
                taught_by=taught,
            )
        return Result(
            ok=True, sku_id=sku, action=ACTION_REPLACED, reason=REASON_REPLACED,
            message=(
                f"replaced {sku!r}: {previous.price_paise} -> {price} paise, "
                f"{previous.n_views} -> {record.n_views} view(s). The previous "
                f"entry is gone, not duplicated.{how}"
            ),
            replaced=True,
            previous_price_paise=int(previous.price_paise),
            photo_action=photo_action, photo_bytes=photo_size,
            taught_by=taught,
        )

    def remove(self, sku_id: str) -> bool:
        """Forget a product. True if it was there, False if it never was.

        Deleting a sku deletes its photo too: an orphan photo is a picture of a
        product the counter can no longer price, and the UI would happily show
        it.
        """
        sku = _require_sku_id(sku_id)
        if sku not in self._skus:
            return False
        self._assert_writable()
        rec = self._skus.pop(sku)
        if rec.photo:
            _unlink_quietly(self.dir / rec.photo)
        self._save()
        return True

    def clear_photo(self, sku_id: str) -> bool:
        """Drop the enrolment photo, keeping the product. Identity is
        untouched — the photo was never part of it."""
        sku = _require_sku_id(sku_id)
        rec = self._skus.get(sku)
        if rec is None or not rec.photo:
            return False
        self._assert_writable()
        _unlink_quietly(self.dir / rec.photo)
        self._skus[sku] = SkuRecord(
            sku_id=rec.sku_id, name=rec.name, price_paise=rec.price_paise,
            vectors=rec.vectors, footprint_mm=rec.footprint_mm,
            photo=None, photo_bytes=0,
        )
        self._save()
        return True

    def photo_bytes(self, sku_id: str) -> Optional[bytes]:
        """The stored PNG, or None. Read from disk each time: the catalog holds
        a path and a length, never the pixels."""
        rec = self._skus.get(sku_id)
        if rec is None or not rec.photo:
            return None
        p = self.dir / rec.photo
        return p.read_bytes() if p.exists() else None

    # -- the collision guard ------------------------------------------------
    def _check_collision(
        self, sku: str, rows: list[np.ndarray], footprint: Optional[float]
    ) -> Collision:
        """Run ``Identifier.check_collision`` against everything BUT ``sku``.

        Excluding the item's own outgoing entry is not a loophole: re-enrolling
        parle_g_200g from a better photo obviously matches the parle_g_200g
        already on file, and refusing that would make every correction
        impossible. What must not pass is a collision with a DIFFERENT product.
        """
        probe = Gallery()
        for rec in self.all():
            if rec.sku_id == sku:
                continue
            probe.enroll(rec.sku_id, rec.vectors, rec.footprint_mm)
        ident = Identifier(
            probe, _never_embeds,
            theta=self.theta, phi=self.phi, tau_mm=self.tau_mm,
            phi_appearance_only=self.phi_appearance_only,
        )
        return ident.check_collision(rows, footprint)

    # -- photos -------------------------------------------------------------
    def _encode_photo(self, photo: Any) -> bytes:
        """This store's own photo budget, applied by `encode_photo_png`.

        The policy — decode, downscale down a ladder, re-encode PNG, refuse
        rather than store unbounded — is one function at module level now,
        because a SECOND caller appeared that has no store record to hang it
        off: `gawaah/shopadmin.py` validates the photograph a shopkeeper
        uploads for a product that lives only in the till's appearance-only
        sidecar. Re-implementing the ladder there would have been a second
        photo policy, and the two would have drifted the first time either
        number moved.
        """
        return encode_photo_png(photo, edge_px=self.photo_edge_px,
                                cap_bytes=self.photo_cap_bytes)

    def _write_photo(self, sku: str, data: bytes) -> None:
        p = self.photo_path(sku)
        p.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(p, data)

    # -- persistence --------------------------------------------------------
    def to_json(self) -> dict:
        payload = {
            "format": CATALOG_FORMAT,
            "gates": {
                "theta": self.theta,
                "phi": self.phi,
                "tau_mm": self.tau_mm,
                "phi_appearance_only": self.phi_appearance_only,
            },
            "dim": self.dim,
            "skus": {rec.sku_id: rec.to_json() for rec in self.all()},
        }
        payload["sha256"] = _digest(payload)
        return payload

    def _assert_writable(self) -> None:
        """Refuse a mutation that would destroy somebody else's price file.

        Called BEFORE any work, not just before the write: a store whose memory
        held a SKU its disk had never seen would be the exact kind of quiet
        disagreement this module exists to prevent.

        A shop.json with no catalog beside it is the legacy ``results/shop.json``
        that live_app.py reads today. Overwriting it would delete real prices
        that no catalog can give back, because they were never enrolled.
        """
        if not self.write_sidecar or self._sidecar_is_ours:
            return
        try:
            n = len(json.loads(self.sidecar_path.read_text(encoding="utf-8")))
            how_many = f"{n} price(s)"
        except (OSError, ValueError, TypeError):
            how_many = "prices"          # unreadable, but still not ours to eat
        raise ShopError(
            f"{self.sidecar_path} exists but {self.catalog_path} does not: that "
            f"is a legacy price file this store did not write, and saving would "
            f"overwrite {how_many} with a catalog that never enrolled them. Move "
            f"it aside, or open the store with write_sidecar=False."
        )

    def _save(self) -> None:
        self._assert_writable()
        self.dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.catalog_path, canonical(self.to_json()) + b"\n")
        if self.write_sidecar:
            _atomic_write(
                self.sidecar_path, canonical(self.price_map()) + b"\n"
            )
        self._sidecar_is_ours = True

    def reload(self) -> None:
        """Read the catalog from disk, replacing whatever is in memory.

        A MISSING catalog is an empty shop — that is a shopkeeper who has not
        enrolled anything yet, and it is not an error. A catalog that EXISTS and
        cannot be read is an error, and it is raised: returning an empty shop
        from a corrupt file would silently unprice the whole counter and every
        item would go amber for a reason no one could see.
        """
        path = self.catalog_path
        if not path.exists():
            self._skus = {}
            # A sidecar with no catalog beside it was written by something else.
            # Remember that, so the first save refuses to clobber it.
            self._sidecar_is_ours = not self.sidecar_path.exists()
            return
        self._sidecar_is_ours = True
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as e:
            raise ShopError(f"{path}: cannot be read: {e}") from e
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ShopError(
                f"{path}: not valid JSON ({e}) — refusing to start with an "
                f"empty shop, which would silently unprice every item"
            ) from e
        self._skus = self._parse(data, path)
        self._check_sidecar_agrees()

    def _parse(self, data: Any, path: Path) -> dict[str, SkuRecord]:
        if not isinstance(data, dict):
            raise ShopError(f"{path}: catalog must be a JSON object, got "
                            f"{type(data).__name__}")
        fmt = data.get("format")
        if fmt != CATALOG_FORMAT:
            raise ShopError(
                f"{path}: unsupported catalog format {fmt!r} "
                f"(this build writes {CATALOG_FORMAT})"
            )
        stored = data.get("sha256")
        if not isinstance(stored, str):
            raise ShopError(f"{path}: missing sha256")
        body = {k: v for k, v in data.items() if k != "sha256"}
        recomputed = _digest(body)
        if recomputed != stored:
            raise ShopError(
                f"{path}: sha256 mismatch — stored {stored[:16]}..., recomputed "
                f"{recomputed[:16]}.... The file was truncated or edited by hand; "
                f"a catalog that half-loads prices half a counter."
            )

        gates = body.get("gates")
        if not isinstance(gates, dict):
            raise ShopError(f"{path}: missing gates block")
        for key, mine in (("theta", self.theta), ("phi", self.phi),
                          ("tau_mm", self.tau_mm)):
            theirs = gates.get(key)
            if not isinstance(theirs, (int, float)) or isinstance(theirs, bool):
                raise ShopError(f"{path}: gate {key!r} is {theirs!r}")
            if float(theirs) != float(mine):
                raise ShopError(
                    f"{path}: this catalog was admitted under {key}={theirs}, "
                    f"you opened it with {key}={mine}. Every entry in it passed a "
                    f"different collision guard, so re-admitting them under a "
                    f"looser one would be inventing safety it never had."
                )

        skus = body.get("skus")
        if not isinstance(skus, dict):
            raise ShopError(f"{path}: 'skus' must be an object")

        out: dict[str, SkuRecord] = {}
        for sku_id in sorted(skus):
            rec = skus[sku_id]
            if not isinstance(rec, dict):
                raise ShopError(f"{path}: {sku_id!r} is not an object")
            sku = _require_sku_id(sku_id)
            if "footprint_mm" not in rec:
                # A MISSING key is not the same claim as an explicit null. The
                # first is a truncated or foreign file; the second is a product
                # taught with no mat. Defaulting the first to the second would
                # quietly DOWNGRADE a mat-measured SKU to the weaker mode — it
                # would lose its size check and nobody would be told — so it is
                # refused instead.
                raise ShopError(
                    f"{path}: {sku_id!r}: no 'footprint_mm' key. Write null to "
                    f"mean 'taught from a photo with no mat'; omitting it means "
                    f"nothing at all."
                )
            try:
                name = _require_name(rec.get("name"))
                # MoneyError, not ShopError: a float on disk is a MONEY bug and
                # must be diagnosed as one, not blurred into 'bad file'.
                price = _require_positive_paise(rec.get("price_paise"))
                fp = _optional_mm(rec.get("footprint_mm"))
                rows = _require_vectors(rec.get("vectors"))
            except MoneyError:
                raise
            except (ShopError, IdentityError) as e:
                raise ShopError(f"{path}: {sku_id!r}: {e}") from e

            # ``taught_by`` is DERIVED from footprint_mm when we write, so on
            # read the only thing it can tell us is whether somebody edited one
            # of the two by hand. A file claiming a product was mat-measured
            # while carrying no millimetres is a file whose audit trail lies
            # about how carefully the counter was taught, so it is refused
            # rather than silently corrected to whichever field we happened to
            # trust.
            claimed = rec.get("taught_by")
            derived = TAUGHT_FROM_PHOTO if fp is None else TAUGHT_ON_MAT
            if claimed is not None and claimed != derived:
                raise ShopError(
                    f"{path}: {sku_id!r}: 'taught_by' says {claimed!r} but "
                    f"'footprint_mm' is {rec.get('footprint_mm')!r}, which means "
                    f"{derived!r}. One of the two was hand-edited; a record that "
                    f"disagrees with itself about whether it was size-checked "
                    f"cannot be trusted to say so in the UI."
                )
            block = np.ascontiguousarray(np.vstack(rows), dtype=np.float64)
            block.setflags(write=False)
            photo = rec.get("photo")
            if photo is not None and not isinstance(photo, str):
                raise ShopError(f"{path}: {sku_id!r}: 'photo' must be a string "
                                f"or null")
            photo_size = rec.get("photo_bytes", 0)
            if not isinstance(photo_size, int) or isinstance(photo_size, bool):
                raise ShopError(f"{path}: {sku_id!r}: 'photo_bytes' must be an "
                                f"integer")
            out[sku] = SkuRecord(sku, name, price, block, fp, photo, photo_size)

        # The appearance-only gate is checked LAST, because unlike the other
        # three it is allowed to be absent — and whether that is acceptable
        # depends on what the file turned out to contain.
        #
        # A catalog written before this mode existed has no such key, and it
        # cannot contain an appearance-only SKU either: a null footprint was
        # unrepresentable then. So for those files the gate governs nothing that
        # was ever admitted, and re-admitting them under any value invents no
        # safety they did not have. That is a real argument, not a convenience,
        # and it stops holding the moment the file contains a footprint-less
        # entry — which is exactly the case this refuses.
        weak = sorted(s for s, r in out.items() if r.is_appearance_only)
        theirs = gates.get("phi_appearance_only")
        if theirs is None:
            if weak:
                raise ShopError(
                    f"{path}: contains appearance-only sku(s) {weak} but its "
                    f"gates block has no 'phi_appearance_only'. Those entries "
                    f"have no size check, so the bar they are matched at is the "
                    f"only thing protecting them, and a catalog that does not "
                    f"record which bar it was admitted under cannot be reopened "
                    f"under a different one."
                )
        elif not isinstance(theirs, (int, float)) or isinstance(theirs, bool):
            raise ShopError(f"{path}: gate 'phi_appearance_only' is {theirs!r}")
        elif float(theirs) != float(self.phi_appearance_only):
            raise ShopError(
                f"{path}: this catalog was admitted under "
                f"phi_appearance_only={theirs}, you opened it with "
                f"{self.phi_appearance_only}. Every entry in it passed a "
                f"different collision guard, so re-admitting them under a "
                f"looser one would be inventing safety it never had."
            )

        dims = {r.dim for r in out.values()}
        if len(dims) > 1:
            raise ShopError(f"{path}: mixed vector dimensions {sorted(dims)}")
        declared = body.get("dim")
        if dims and declared is not None and int(declared) != dims.copy().pop():
            raise ShopError(
                f"{path}: declares dim {declared} but entries are "
                f"dim {sorted(dims)[0]}"
            )
        return out

    def _check_sidecar_agrees(self) -> None:
        """The derived ``shop.json`` must not have been hand-edited.

        This is the invariant 'to_gallery and price_book never disagree' pushed
        all the way down to the filesystem. ``live_app.py`` prices a sale from
        shop.json; the gallery that recognised the item comes from catalog.json.
        If a price was typed into shop.json alone, the counter would charge a
        number that no enrolment ever authorised — a wrong price on a real sale,
        which is the one failure this whole product exists to prevent. So we
        raise and name the disagreeing skus rather than pick a winner.
        """
        p = self.sidecar_path
        if not p.exists():
            return
        try:
            side = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ShopError(f"{p}: derived price sidecar is not valid JSON: {e}") from e
        if not isinstance(side, dict):
            raise ShopError(f"{p}: derived price sidecar must be an object")
        mine = {s: int(self._skus[s].price_paise) for s in self._skus}
        theirs = {str(k): v for k, v in side.items()}
        bad = sorted(
            set(mine) ^ set(theirs)
            # `2000 != 2000.0` is False, so an int-vs-float difference would
            # slip through a value comparison. Compare the TYPE too: a float in
            # the sidecar is a money bug even when it equals the right number.
            | {
                k for k in set(mine) & set(theirs)
                if mine[k] != theirs[k]
                or isinstance(theirs[k], bool)
                or not isinstance(theirs[k], int)
            }
        )
        if bad:
            raise ShopError(
                f"{p} disagrees with {self.catalog_path} on {bad}. shop.json is "
                f"DERIVED — it is regenerated from the catalog on every change "
                f"and must never be hand-edited, because live_app.py prices a "
                f"sale from it while the gallery comes from the catalog. Fix the "
                f"catalog and call sync(), or delete shop.json."
            )

    def sync(self) -> None:
        """Rewrite the derived files from the in-memory catalog."""
        self._save()


# ---------------------------------------------------------------- small helpers

def _require_sku_id(sku_id: Any) -> str:
    """A sku id is a JSON key AND a filename. One regex, checked once, here.

    Rejecting '../x' is not paranoia theatre: photo_path() joins this straight
    onto a directory, so an unchecked id writes wherever it likes.
    """
    if isinstance(sku_id, bool) or not isinstance(sku_id, str):
        raise ShopError(f"sku_id must be a string, got {sku_id!r}")
    if not SKU_RE.match(sku_id):
        raise ShopError(
            f"sku_id {sku_id!r} is not usable: it is both a JSON key and a "
            f"filename, so it must match {SKU_RE.pattern}"
        )
    return sku_id


def _require_name(name: Any) -> str:
    if isinstance(name, bool) or not isinstance(name, str):
        raise ShopError(f"name must be a string, got {name!r}")
    clean = " ".join(name.split())
    if not clean:
        raise ShopError("name is empty — a product the shopkeeper cannot read "
                        "back is a product he cannot correct")
    if len(clean) > NAME_MAX_CHARS:
        raise ShopError(f"name is {len(clean)} chars, over {NAME_MAX_CHARS}")
    return clean


def _require_mm(value: Any) -> float:
    """A real, positive measurement in millimetres, or raise.

    None is refused HERE. Only ``_optional_mm`` lets it through, so every call
    site has to state in one word whether "no footprint" is a legal answer for
    it. The two are never the same question.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, np.floating,
                                                         np.integer)):
        raise ShopError(
            f"footprint_mm must be a measured long edge in mm, got {value!r} — "
            f"pass None only to mean 'taught from a photo with no mat in it', "
            f"never to stand in for a measurement that failed"
        )
    v = float(value)
    if not np.isfinite(v) or v <= 0.0:
        raise ShopError(f"footprint_mm must be a positive real, got {value!r}")
    return v


def _optional_mm(value: Any) -> Optional[float]:
    """``_require_mm``, except that None passes through as None.

    ABSENT AND INVALID ARE DIFFERENT THINGS, and this helper is the only place
    that distinction is drawn. ``None`` means "this product was taught from an
    ordinary photo, there is no scale and there never will be one" — a
    supported, deliberately weaker mode. ``0``, ``-1``, ``NaN``, ``inf``,
    ``True`` and ``"118"`` are each still refused BY NAME, because every one of
    those is a bug that would otherwise be laundered into the weak mode and
    never seen again. A 0 mm packet in particular would not merely be wrong, it
    would be COMPARED against: the metric tiebreak would gate a real query on a
    measurement nobody ever took.
    """
    return None if value is None else _require_mm(value)


def _require_vectors(vectors: Any) -> list[np.ndarray]:
    """One or more finite 1-D vectors of equal length. Delegates the per-vector
    rules to identity.as_vector so the store and the identifier agree on what a
    vector is."""
    if vectors is None:
        raise IdentityError("no vectors supplied — a SKU with no enrolled view "
                            "could be priced but never recognised, and the "
                            "gallery and the price book would disagree")
    if isinstance(vectors, np.ndarray) and vectors.ndim == 2:
        seq: list[Any] = list(vectors)
    else:
        seq = list(vectors)
    rows = [as_vector(v) for v in seq]
    if not rows:
        raise IdentityError("no vectors supplied")
    dims = {r.shape[0] for r in rows}
    if len(dims) != 1:
        raise IdentityError(f"mixed vector dimensions {sorted(dims)}")
    return rows


def _digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical(dict(payload))).hexdigest()


def encode_photo_png(photo: Any, *, edge_px: int = PHOTO_EDGE_PX,
                     cap_bytes: int = PHOTO_CAP_BYTES) -> bytes:
    """Whatever a caller was handed -> a downscaled, size-capped PNG, or refuse.

    THE ONE PHOTO POLICY IN THIS PROGRAM, and public so it can be the one
    everywhere. `ShopStore._encode_photo` is this function with the store's own
    two numbers; `gawaah/shopadmin.py` calls it with SMALLER ones for a picture
    that has to live inside a JSON file rather than beside it. Both budgets are
    stated by their caller; neither is invented here.

    Accepts encoded bytes (what a browser upload is) or an ndarray (what the
    rectified crop is), because both callers exist and forcing one to encode
    just so we can decode it again is waste.

    THREE THINGS ARE REFUSED RATHER THAN REPAIRED, and each is a place a photo
    path normally guesses: input past the byte cap (before decode, so a
    decompression bomb never allocates), an image that is not uint8 (a float
    image has no agreed range and would be silently clipped), and a picture
    still over `cap_bytes` at the smallest rung of the ladder.
    """
    import cv2  # local: a catalog with no photos should not pay for cv2

    if isinstance(photo, (bytes, bytearray, memoryview)):
        data = bytes(photo)
        if len(data) > PHOTO_INPUT_CAP_BYTES:
            raise ShopError(
                f"enrolment photo is {len(data)} bytes, over the "
                f"{PHOTO_INPUT_CAP_BYTES} byte input cap — refused before "
                f"decode so a decompression bomb never allocates"
            )
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise ShopError("enrolment photo did not decode as an image")
    elif isinstance(photo, np.ndarray):
        img = photo
    else:
        raise ShopError(
            f"enrolment photo must be encoded bytes or an ndarray, got "
            f"{type(photo).__name__}"
        )

    img = np.asarray(img)
    if img.dtype != np.uint8:
        raise ShopError(
            f"enrolment photo must be uint8, got {img.dtype} — a float image "
            f"has no agreed range and would be silently clipped"
        )
    if img.ndim not in (2, 3) or img.size == 0:
        raise ShopError(f"enrolment photo has unusable shape {img.shape}")

    ladder = tuple(e for e in PHOTO_EDGE_LADDER if e <= edge_px) or (edge_px,)
    last = b""
    for edge in ladder:
        small = _downscale(cv2, img, edge)
        ok, buf = cv2.imencode(".png", small,
                               [int(cv2.IMWRITE_PNG_COMPRESSION), 9])
        if not ok:
            raise ShopError("enrolment photo failed to encode as PNG")
        last = buf.tobytes()
        if len(last) <= cap_bytes:
            return last
    raise ShopError(
        f"enrolment photo is {len(last)} bytes even at "
        f"{ladder[-1]} px long edge, over the {cap_bytes} byte "
        f"cap — refused rather than stored unbounded"
    )


def _downscale(cv2: Any, img: np.ndarray, edge: int) -> np.ndarray:
    h, w = int(img.shape[0]), int(img.shape[1])
    longest = max(h, w)
    if longest <= edge:
        return img
    scale = edge / longest
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    return cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)


def _atomic_write(path: Path, data: bytes) -> None:
    """Write-then-rename, so a crash mid-write leaves the OLD catalog intact.

    A half-written catalog would fail the sha256 check and refuse to load, which
    is better than silence but still a dead counter. os.replace is atomic within
    a filesystem, so the old file is either fully there or fully replaced.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


__all__ = [
    "ACTION_ADDED",
    "ACTION_REFUSED",
    "ACTION_REPLACED",
    "CATALOG_FORMAT",
    "CATALOG_NAME",
    "PHOTO_CAP_BYTES",
    "PHOTO_EDGE_PX",
    "PHOTO_INPUT_CAP_BYTES",
    "REASON_ADDED",
    "REASON_COLLISION",
    "REASON_REPLACED",
    "Result",
    "ShopError",
    "ShopStore",
    "SIDECAR_NAME",
    "SkuRecord",
    "TAUGHT_BY",
    "TAUGHT_FROM_PHOTO",
    "TAUGHT_ON_MAT",
    "price_from_rupees",
]
