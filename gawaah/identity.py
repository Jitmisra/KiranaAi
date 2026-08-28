"""S5a — PEHCHAAN: identity PROPOSES, thresholds DISPOSE.

The embedder is INJECTED. This module downloads nothing, ships no weights and
holds no model. It is given a callable that turns a rectified crop into a
vector, and everything it does on top of that is deterministic arithmetic you
can read in one sitting.

The shape of the decision (invariant 7 — abstain rather than guess):

  1. FOOTPRINT FIRST.  The TAKHTI gives us real millimetres, so the metric
     tiebreak runs BEFORE appearance, not after it. Only gallery entries whose
     enrolled long edge is within tau_mm of the measured long edge are even
     allowed to compete. A 500 ml bottle and a 1 L bottle of the same brand look
     nearly identical to any embedder and are 60 mm apart on the mat; the mat
     wins that argument, not the vector.

  2. APPEARANCE RANKS.  Survivors are scored by cosine similarity, best-of over
     the enrolled views of each SKU.

  3. THRESHOLDS DISPOSE.  A SKU is returned ONLY when
         top1 >= phi        (it actually looks like the thing)
     AND (top1 - top2) >= theta   (and it does not look equally like something else)
     Otherwise sku_id is None, a named reason is attached, and the caller shows
     AMBER. Amber is a correct outcome. It is never a failure.

The four abstention reasons are distinct on purpose, because the UI does a
different thing for each one:

  no_candidate_in_footprint  nothing in the gallery is the right SIZE.
                             -> offer enrolment, do not offer a guess.
  below_similarity           nothing in the gallery LOOKS like this.
                             -> offer enrolment.
  ambiguous_pair             the top two are tied to within numerical noise, so
                             which one is "first" is an artefact of sort order
                             and carries no information at all.
                             -> name BOTH skus and demand a disambiguation.
  below_margin               there is a leader, but not by enough.
                             -> name the leader as a suggestion, never as fact.

Nothing here touches money, so plain floats are correct and the no-float lint
does not (and must not) cover this file.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

import numpy as np

from gawaah.ledger import canonical

# An embedder: rectified crop -> 1-D vector. Injected, never constructed here.
EmbedFn = Callable[[np.ndarray], Any]

DEFAULT_THETA = 0.10      # required cosine gap between top1 and top2
DEFAULT_PHI = 0.55        # required absolute cosine for top1
DEFAULT_TAU_MM = 4.0      # footprint tolerance, millimetres
DEFAULT_EPS = 1e-9        # below this a top-2 gap is numerical noise, not signal

REASON_MATCH = "match"
REASON_BELOW_MARGIN = "below_margin"
REASON_BELOW_SIMILARITY = "below_similarity"
REASON_NO_CANDIDATE = "no_candidate_in_footprint"
REASON_AMBIGUOUS = "ambiguous_pair"

#: Every value `Identification.reason` can take when sku_id is None.
ABSTAIN_REASONS: tuple[str, ...] = (
    REASON_BELOW_MARGIN,
    REASON_BELOW_SIMILARITY,
    REASON_NO_CANDIDATE,
    REASON_AMBIGUOUS,
)

GALLERY_FORMAT = 1


class IdentityError(ValueError):
    """A malformed gallery, vector or query. Distinct from an abstention:
    abstaining is a normal result, this is a bug in the caller."""


class CollisionError(IdentityError):
    """Enrolment refused: the new item is indistinguishable from one already
    enrolled, by the very thresholds identify() would later use."""

    def __init__(self, collision: "Collision") -> None:
        super().__init__(collision.message)
        self.collision = collision


# --------------------------------------------------------------------- vectors

def as_vector(v: Any) -> np.ndarray:
    """Coerce to a finite 1-D float64 vector, or raise."""
    a = np.asarray(v, dtype=np.float64)
    if a.ndim != 1:
        raise IdentityError(f"embedding must be 1-D, got shape {a.shape}")
    if a.size == 0:
        raise IdentityError("embedding is empty")
    if not np.all(np.isfinite(a)):
        raise IdentityError("embedding contains NaN or inf")
    return a


def cosine(a: Any, b: Any) -> float:
    """Cosine similarity, clipped to [-1, 1].

    A zero vector has no direction, so its similarity to anything is defined
    here as 0.0 rather than NaN. 0.0 is the honest answer: it carries no
    evidence either way, and downstream it lands below phi and abstains.
    """
    va, vb = as_vector(a), as_vector(b)
    if va.shape != vb.shape:
        raise IdentityError(f"dimension mismatch: {va.shape[0]} vs {vb.shape[0]}")
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.clip(float(np.dot(va, vb)) / (na * nb), -1.0, 1.0))


def _unit_rows(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    if not np.all(norms > 0.0):
        raise IdentityError("a zero vector cannot be enrolled: it has no direction")
    return m / norms


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v if n == 0.0 else v / n


# --------------------------------------------------------------------- gallery

@dataclass(eq=False)
class GalleryEntry:
    """One enrolled SKU: its views, and its measured long edge in millimetres."""

    sku_id: str
    vectors: np.ndarray          # (k, dim) float64, exactly as enrolled
    footprint_mm: float
    unit: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.vectors = np.ascontiguousarray(self.vectors, dtype=np.float64)
        if self.vectors.ndim != 2 or self.vectors.shape[0] == 0:
            raise IdentityError(
                f"{self.sku_id!r}: expected a non-empty (k, dim) block, "
                f"got shape {self.vectors.shape}"
            )
        if not np.all(np.isfinite(self.vectors)):
            raise IdentityError(f"{self.sku_id!r}: vectors contain NaN or inf")
        if not np.isfinite(self.footprint_mm) or self.footprint_mm <= 0.0:
            raise IdentityError(
                f"{self.sku_id!r}: footprint_mm must be a positive real, "
                f"got {self.footprint_mm!r}"
            )
        self.unit = _unit_rows(self.vectors)

    @property
    def dim(self) -> int:
        return int(self.vectors.shape[1])

    @property
    def n_views(self) -> int:
        return int(self.vectors.shape[0])

    def score(self, q_unit: np.ndarray) -> float:
        """Best-of over enrolled views. Best-of, not mean: a SKU enrolled from
        four angles should match on the angle it was photographed from, and
        averaging in three mismatched views only buries the evidence."""
        return float(np.clip(float(np.max(self.unit @ q_unit)), -1.0, 1.0))


class Gallery:
    """The enrolled catalogue. A dumb, deterministic store — every policy
    decision lives in Identifier, so the gallery can be inspected and diffed
    without knowing anything about thresholds."""

    def __init__(self) -> None:
        self._entries: dict[str, GalleryEntry] = {}

    # -- construction -------------------------------------------------------
    def enroll(
        self,
        sku_id: str,
        vectors: Iterable[Any],
        footprint_mm: float,
        *,
        replace: bool = False,
    ) -> GalleryEntry:
        """Enrol one SKU from one or more views.

        Refuses to silently overwrite: re-enrolling an existing sku_id needs
        replace=True. Repeat captures of the same item should be passed
        together as multiple vectors, not enrolled one at a time.
        """
        if not isinstance(sku_id, str) or not sku_id:
            raise IdentityError(f"sku_id must be a non-empty string, got {sku_id!r}")
        if sku_id in self._entries and not replace:
            raise IdentityError(
                f"{sku_id!r} is already enrolled; pass replace=True to overwrite"
            )
        rows = [as_vector(v) for v in vectors]
        if not rows:
            raise IdentityError(f"{sku_id!r}: no vectors supplied")
        dims = {r.shape[0] for r in rows}
        if len(dims) != 1:
            raise IdentityError(f"{sku_id!r}: mixed vector dimensions {sorted(dims)}")
        entry = GalleryEntry(sku_id, np.vstack(rows), float(footprint_mm))
        existing = self.dim
        if existing is not None and entry.dim != existing:
            raise IdentityError(
                f"{sku_id!r}: dimension {entry.dim} does not match gallery dimension "
                f"{existing}"
            )
        self._entries[sku_id] = entry
        return entry

    def remove(self, sku_id: str) -> None:
        if sku_id not in self._entries:
            raise IdentityError(f"{sku_id!r} is not enrolled")
        del self._entries[sku_id]

    # -- inspection ---------------------------------------------------------
    @property
    def dim(self) -> int | None:
        """Embedding dimension, or None while empty. All entries share it."""
        for e in self._entries.values():
            return e.dim
        return None

    def skus(self) -> tuple[str, ...]:
        """Enrolled sku ids, sorted. Sorted, not insertion-ordered, so two
        galleries built in different orders behave identically."""
        return tuple(sorted(self._entries))

    def get(self, sku_id: str) -> GalleryEntry:
        try:
            return self._entries[sku_id]
        except KeyError:
            raise IdentityError(f"{sku_id!r} is not enrolled") from None

    def footprint(self, sku_id: str) -> float:
        return self.get(sku_id).footprint_mm

    def entries(self) -> Iterator[GalleryEntry]:
        for sku in self.skus():
            yield self._entries[sku]

    def __contains__(self, sku_id: object) -> bool:
        return sku_id in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        return f"Gallery(n={len(self)}, dim={self.dim}, skus={self.skus()})"

    # -- persistence --------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "format": GALLERY_FORMAT,
            "dim": self.dim,
            "entries": {
                e.sku_id: {
                    "footprint_mm": e.footprint_mm,
                    "vectors": e.vectors.tolist(),
                }
                for e in self.entries()
            },
        }

    def save(self, path: str | Path) -> Path:
        """Write canonical JSON (sorted keys, compact) so the same gallery is
        always the same bytes — diffable in git, hashable into the ledger."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(canonical(self.to_dict()) + b"\n")
        return p

    @classmethod
    def from_dict(cls, data: dict) -> "Gallery":
        fmt = data.get("format")
        if fmt != GALLERY_FORMAT:
            raise IdentityError(f"unsupported gallery format {fmt!r}")
        g = cls()
        for sku_id in sorted(data.get("entries", {})):
            rec = data["entries"][sku_id]
            g.enroll(sku_id, rec["vectors"], rec["footprint_mm"])
        declared = data.get("dim")
        if g.dim is not None and declared is not None and int(declared) != g.dim:
            raise IdentityError(
                f"gallery declares dim {declared} but entries are dim {g.dim}"
            )
        return g

    @classmethod
    def load(cls, path: str | Path) -> "Gallery":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# --------------------------------------------------------------------- results

@dataclass(frozen=True)
class Identification:
    """What identity PROPOSED and what the thresholds DISPOSED.

    sku_id is None whenever we abstain. top1_sku is still filled in, because
    "I think this is Parle-G but I am not confident" is useful to a shopkeeper
    and "I have no idea" is not — as long as the UI shows it amber and the
    total excludes it.
    """

    sku_id: str | None
    top1: float
    top2: float
    margin: float
    reason: str
    top1_sku: str | None = None
    top2_sku: str | None = None
    n_candidates: int = 0
    long_edge_mm: float | None = None

    @property
    def is_amber(self) -> bool:
        return self.sku_id is None

    @property
    def is_match(self) -> bool:
        return self.sku_id is not None

    def to_audit(self) -> dict:
        """Plain-JSON fields for Ledger.append(**fields)."""
        return {
            "sku_id": self.sku_id,
            "top1_sku": self.top1_sku,
            "top2_sku": self.top2_sku,
            "top1": round(self.top1, 6),
            "top2": round(self.top2, 6),
            "margin": round(self.margin, 6),
            "reason": self.reason,
            "n_candidates": self.n_candidates,
            "long_edge_mm": (
                None if self.long_edge_mm is None else round(self.long_edge_mm, 3)
            ),
        }


@dataclass(frozen=True)
class Collision:
    """Result of the enrolment collision guard."""

    collides: bool
    sku_id: str | None            # worst offender
    similarity: float
    footprint_delta_mm: float | None
    reason: str
    colliding: tuple[str, ...] = ()

    @property
    def message(self) -> str:
        if not self.collides:
            return "no collision"
        return (
            f"indistinguishable from {self.sku_id!r}: cosine {self.similarity:.4f}, "
            f"footprint delta {self.footprint_delta_mm:.2f} mm — a disambiguation "
            f"capture is required"
        )

    def to_audit(self) -> dict:
        return {
            "collides": self.collides,
            "sku_id": self.sku_id,
            "similarity": round(self.similarity, 6),
            "footprint_delta_mm": (
                None
                if self.footprint_delta_mm is None
                else round(self.footprint_delta_mm, 3)
            ),
            "reason": self.reason,
            "colliding": list(self.colliding),
        }


# ------------------------------------------------------------------ identifier

class Identifier:
    """Proposes a SKU for a crop, and refuses to when it should not."""

    def __init__(
        self,
        gallery: Gallery,
        embed_fn: EmbedFn,
        theta: float = DEFAULT_THETA,
        phi: float = DEFAULT_PHI,
        tau_mm: float = DEFAULT_TAU_MM,
        eps: float = DEFAULT_EPS,
    ) -> None:
        if not callable(embed_fn):
            raise IdentityError("embed_fn must be callable — the embedder is injected")
        if theta < 0.0:
            raise IdentityError(f"theta must be >= 0, got {theta!r}")
        if tau_mm < 0.0:
            raise IdentityError(f"tau_mm must be >= 0, got {tau_mm!r}")
        if eps < 0.0:
            raise IdentityError(f"eps must be >= 0, got {eps!r}")
        self.gallery = gallery
        self.embed_fn = embed_fn
        self.theta = float(theta)
        self.phi = float(phi)
        self.tau_mm = float(tau_mm)
        self.eps = float(eps)

    def __repr__(self) -> str:
        return (
            f"Identifier(n_skus={len(self.gallery)}, theta={self.theta}, "
            f"phi={self.phi}, tau_mm={self.tau_mm})"
        )

    # -- the metric tiebreak, run FIRST ------------------------------------
    def candidates(self, long_edge_mm: float) -> tuple[GalleryEntry, ...]:
        """Entries whose enrolled long edge is within tau_mm of the measured
        one. Sorted by sku_id so the shortlist never depends on enrolment
        order."""
        lo = _require_mm(long_edge_mm)
        return tuple(
            e for e in self.gallery.entries()
            if abs(e.footprint_mm - lo) <= self.tau_mm
        )

    def embed(self, crop: np.ndarray) -> np.ndarray:
        v = as_vector(self.embed_fn(crop))
        gdim = self.gallery.dim
        if gdim is not None and v.shape[0] != gdim:
            raise IdentityError(
                f"embed_fn returned dimension {v.shape[0]}, gallery is {gdim}"
            )
        return v

    # -- the decision -------------------------------------------------------
    def identify(self, crop: np.ndarray, long_edge_mm: float) -> Identification:
        """Propose a SKU for one rectified crop measured at long_edge_mm.

        long_edge_mm is REQUIRED and must be a positive real. Identity is never
        attempted without a metric footprint: if the plane did not lock, the
        caller abstains upstream rather than asking this function to guess.
        """
        lo = _require_mm(long_edge_mm)
        shortlist = self.candidates(lo)
        if not shortlist:
            return Identification(
                None, 0.0, 0.0, 0.0, REASON_NO_CANDIDATE,
                n_candidates=0, long_edge_mm=lo,
            )

        q = _unit(self.embed(crop))
        # (-score, sku_id): ties break lexicographically, so the ORDER is always
        # reproducible even though a tie means the order carries no meaning.
        ranked = sorted(
            ((e.score(q), e.sku_id) for e in shortlist),
            key=lambda t: (-t[0], t[1]),
        )
        top1, top1_sku = ranked[0]
        if len(ranked) > 1:
            top2, top2_sku = ranked[1]
        else:
            # Nothing else was even the right size. 0.0 is what an unrelated
            # direction scores in expectation, so it is the honest stand-in for
            # "no competitor" and keeps margin comparable across shortlist sizes.
            # Consequence, deliberately not clamped away: a lone anti-correlated
            # candidate yields a negative margin. It is never consulted, because
            # a negative top1 fails phi first.
            top2, top2_sku = 0.0, None
        margin = top1 - top2

        common = dict(
            top1_sku=top1_sku,
            top2_sku=top2_sku,
            n_candidates=len(shortlist),
            long_edge_mm=lo,
        )
        if top1 < self.phi:
            return Identification(
                None, top1, top2, margin, REASON_BELOW_SIMILARITY, **common
            )
        if top2_sku is not None and margin <= self.eps:
            # A tie. Which of the two is "first" is an artefact of the sort key,
            # so naming one would be inventing evidence.
            return Identification(
                None, top1, top2, margin, REASON_AMBIGUOUS, **common
            )
        if margin < self.theta:
            return Identification(
                None, top1, top2, margin, REASON_BELOW_MARGIN, **common
            )
        return Identification(top1_sku, top1, top2, margin, REASON_MATCH, **common)

    # -- the enrolment collision guard -------------------------------------
    def check_collision(
        self, new_vectors: Iterable[Any], new_footprint: float
    ) -> Collision:
        """Refuse to enrol an item that identify() could never separate.

        Fires when an enrolled entry is within BOTH:
          appearance : best cosine >= 1 - theta
          footprint  : |delta| <= tau_mm

        Those two bounds are not arbitrary, they are the identify() thresholds
        read backwards. If the new item is later presented, it scores ~1.0
        against itself and >= 1 - theta against the colliding entry, so the
        top-2 margin cannot reach theta; and the footprint filter cannot drop
        the impostor because it is inside tau_mm. The pair is therefore
        permanently amber. Better to say so at enrolment, when the shopkeeper
        is still holding the item and can take a disambiguation capture, than
        at the counter with a customer waiting.
        """
        return self._collide(
            self.gallery,
            [as_vector(v) for v in new_vectors],
            _require_mm(new_footprint),
        )

    def _collide(
        self, gallery: Gallery, rows: list[np.ndarray], fp: float
    ) -> Collision:
        if not rows:
            raise IdentityError("no vectors supplied")
        dims = {r.shape[0] for r in rows}
        if len(dims) != 1:
            raise IdentityError(f"mixed vector dimensions {sorted(dims)}")
        gdim = gallery.dim
        if gdim is not None and dims != {gdim}:
            raise IdentityError(
                f"vectors are dimension {dims.pop()}, gallery is {gdim}"
            )
        new_unit = _unit_rows(np.vstack(rows))
        appearance_bar = 1.0 - self.theta
        hits: list[tuple[float, float, str]] = []
        for e in gallery.entries():
            sim = float(np.clip(float(np.max(new_unit @ e.unit.T)), -1.0, 1.0))
            delta = abs(e.footprint_mm - fp)
            if sim >= appearance_bar and delta <= self.tau_mm:
                hits.append((sim, delta, e.sku_id))

        if not hits:
            return Collision(False, None, 0.0, None, "clear")
        hits.sort(key=lambda t: (-t[0], t[1], t[2]))
        sim, delta, sku = hits[0]
        return Collision(
            True, sku, sim, delta, "collision",
            colliding=tuple(sorted(h[2] for h in hits)),
        )

    def enroll(
        self,
        sku_id: str,
        vectors: Iterable[Any],
        footprint_mm: float,
        *,
        force: bool = False,
        replace: bool = False,
    ) -> GalleryEntry:
        """Guarded enrolment. Raises CollisionError (carrying the colliding
        sku) unless force=True, so the UI can demand a disambiguation capture
        instead of poisoning the gallery."""
        rows = [as_vector(v) for v in vectors]
        if sku_id in self.gallery and not replace:
            raise IdentityError(
                f"{sku_id!r} is already enrolled; pass replace=True to overwrite"
            )
        if not force:
            probe = self.gallery
            if sku_id in probe:
                # An item must not collide with its own outgoing entry.
                probe = Gallery.from_dict(self.gallery.to_dict())
                probe.remove(sku_id)
            c = self._collide(probe, rows, _require_mm(footprint_mm))
            if c.collides:
                raise CollisionError(c)
        return self.gallery.enroll(sku_id, rows, footprint_mm, replace=replace)


def _require_mm(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, np.floating,
                                                         np.integer)):
        raise IdentityError(
            f"a measured long edge in mm is required, got {value!r} — identity is "
            "never attempted without a metric footprint"
        )
    v = float(value)
    if not np.isfinite(v) or v <= 0.0:
        raise IdentityError(f"long edge must be a positive real, got {value!r}")
    return v
