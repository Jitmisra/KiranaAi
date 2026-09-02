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

WHEN THERE ARE NO MILLIMETRES: THE APPEARANCE-ONLY MODE
=======================================================
A shopkeeper photographing a packet on a kitchen table has no mat, so step 1
has nothing to work with. Refusing to teach from such a photo makes the feature
unusable for the case people try first, so `footprint_mm=None` is legal and
means exactly one thing: THIS SKU WAS TAUGHT WITH NO SCALE. It is not zero, it
is not a default, and it is not a footprint of unknown value — it is the
absence of a measurement. A footprint that is PRESENT but nonsense (negative,
zero, NaN, inf, a string) is still refused, because absent and invalid are
different bugs and blurring them would hide the second one.

An entry with no footprint skips step 1 and competes on appearance alone. So
does any entry when the QUERY has no millimetres (`identify(crop, None)`) —
scale is a property of the comparison, not of one side of it. Everything else
is unchanged: an entry with a footprint, queried with a measurement, is gated
exactly as it always was, at exactly the same tau_mm.

Losing step 1 removes a discriminator, so it is paid for in two places:

  * a HIGHER similarity bar, PHI_APPEARANCE_ONLY, whose value is measured below
    rather than chosen;
  * the margin gate, which turns out to do most of the work. An impostor the
    mat used to delete now enters the ranking, and if it scores near the true
    entry the top-2 margin collapses and we abstain. The pairs the enrolment
    guard admits ONLY because the mat separates them (the 500 ml / 1 L case)
    are therefore not mis-priced when the mat is missing; they go amber, which
    is the correct outcome and is tested.

`Identification.mode` names which of the two decided, so a caller and a UI can
say "no size check was possible here" instead of pretending the two modes are
the same thing.

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
# RE-DERIVED 0.90 -> 0.55 on 2026-09-01, for a NEW EMBEDDER, from measurement.
#
# Every number in the old table here was a fact about the retired 461-dim
# handcrafted descriptor (gawaah/embedder.py) — including the 61% false-price
# rate that forced phi up to 0.90, and the cross-product cosines of 0.7-0.88
# that made that necessary. Those tables are preserved in git history and they
# remain true OF THAT DESCRIPTOR. They are not true of this one, and keeping a
# gate calibrated for a retired scale would reject nearly everything genuine.
#
# The live path now embeds through gawaah/embedder2.py (SqueezeNet fire9,
# nuisance-whitened, rotation-TTA). Measured on a 56-image bench of six real
# products, two real captured frames and deterministic lighting/rotation/blur
# variants — then adversarially verified with shadow, partial occlusion, JPEG
# q40, 60% scale, 90-degree rotation and open-set impostor sets:
#
#     worst same-product cosine        0.6281
#     best DIFFERENT-product cosine    0.4379   (fully separated: gap +0.19)
#     open-set impostor top1           0.333    (an untaught jar names nothing)
#     the once-failing real jar        0.8199   (was 0.7421 vs a 0.92 gate)
#
# 0.55 sits above the strongest measured wrong answer by 0.11 and below the
# weakest measured genuine pair by 0.08. The hardest verified genuine cases —
# rot90 at 0.588, shadow at 0.653, occlusion at 0.668 — clear it too.
#
# A floor, not a ceiling. Lowering it requires re-running the separation bench
# (tests/test_embedder2_separation.py holds the frontier as executable
# numbers) and stating the measured false-price rate, because that rate is the
# number this gate exists to hold down. INVARIANT 7 stands: a wrong price
# costs a shopkeeper money; an abstention costs one tap.

PHI_APPEARANCE_ONLY = 0.60
# The bar for a decision that had NO footprint to check. Higher than
# DEFAULT_PHI for the same reason as always: a mode with fewer discriminators
# must never be the easier one to pass.
#
# Re-derived for embedder2 alongside DEFAULT_PHI. 0.60 clears the strongest
# measured open-set impostor (0.4379) by +0.16 and accepts the once-failing
# real jar (0.8199) by +0.22. Its measured cost is exactly one verified
# genuine case: a 90-degree-rotated live frame at 0.588 goes amber — one tap.
#
# THE VERIFIED LIMIT THIS GATE CANNOT FIX, stated rather than glossed: an
# untaught SAME-BRAND sibling (identical print, recoloured band) scores ~0.83
# and is named as its taught sibling; no gate between the genuine floor (0.63)
# and 0.83 exists. The retired descriptor failed the identical case harder
# (0.970, with a ranking inversion). Mitigations are the product's own: teach
# the sibling and the pair is permanently amber via theta; a size difference
# is caught by the footprint gate.
#
# A floor, not a ceiling. Lowering it below DEFAULT_PHI is refused outright by
# Identifier.
DEFAULT_TAU_MM = 4.0      # footprint tolerance, millimetres
DEFAULT_EPS = 1e-9        # below this a top-2 gap is numerical noise, not signal

#: How a decision was reached. `Identification.mode` carries one of these, and
#: an entry that was taught with no scale can only ever produce the second.
MODE_FOOTPRINT_GATED = "footprint_gated"
MODE_APPEARANCE_ONLY = "appearance_only"
MODE_NONE = "none"          # nothing was ranked, so nothing decided anything

MODES: tuple[str, ...] = (MODE_FOOTPRINT_GATED, MODE_APPEARANCE_ONLY, MODE_NONE)

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
    """One enrolled SKU: its views, and its measured long edge in millimetres.

    ``footprint_mm is None`` means the SKU was taught from a photo with no
    scale. It is the ONLY legal way to have no footprint; anything else that is
    not a positive real is a bug and is refused by name.
    """

    sku_id: str
    vectors: np.ndarray          # (k, dim) float64, exactly as enrolled
    footprint_mm: float | None
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
        self.footprint_mm = _optional_mm(self.footprint_mm, self.sku_id)
        self.unit = _unit_rows(self.vectors)

    @property
    def dim(self) -> int:
        return int(self.vectors.shape[1])

    @property
    def n_views(self) -> int:
        return int(self.vectors.shape[0])

    @property
    def is_appearance_only(self) -> bool:
        """True when this SKU was taught with no scale, so no query can ever
        size-check it. Such an entry is measurably easier to confuse and every
        surface that shows it is expected to say so."""
        return self.footprint_mm is None

    def mode_against(self, long_edge_mm: float | None) -> str:
        """Which mode a comparison with `long_edge_mm` would be judged in.

        Scale is a property of the COMPARISON: it takes millimetres on both
        sides to run a metric tiebreak, so a measured query against a
        footprint-less entry is appearance-only, and so is a footprinted entry
        against an unmeasured query.
        """
        if self.footprint_mm is None or long_edge_mm is None:
            return MODE_APPEARANCE_ONLY
        return MODE_FOOTPRINT_GATED

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
        footprint_mm: float | None,
        *,
        replace: bool = False,
    ) -> GalleryEntry:
        """Enrol one SKU from one or more views.

        ``footprint_mm=None`` enrols an APPEARANCE-ONLY SKU: taught from a
        photo with no scale, so no query will ever be able to size-check it.
        That is a supported, deliberately weaker mode, not a shortcut — see the
        module docstring and PHI_APPEARANCE_ONLY.

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
        entry = GalleryEntry(sku_id, np.vstack(rows),
                             _optional_mm(footprint_mm, sku_id))
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

    def footprint(self, sku_id: str) -> float | None:
        """The enrolled long edge in mm, or None for an appearance-only SKU.

        None is not an error and callers must not coerce it: ``float(None)``
        raises and ``float(x or 0)`` would invent a 0 mm packet that the metric
        tiebreak would then compare against. Use
        ``is_appearance_only(sku_id)`` to branch.
        """
        return self.get(sku_id).footprint_mm

    def is_appearance_only(self, sku_id: str) -> bool:
        return self.get(sku_id).is_appearance_only

    def appearance_only_skus(self) -> tuple[str, ...]:
        """The weak half of the catalogue, sorted. A UI is expected to mark
        these, and a shopkeeper can re-teach them on the mat to get a
        footprint."""
        return tuple(e.sku_id for e in self.entries() if e.is_appearance_only)

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
        """The gallery as plain JSON.

        ``footprint_mm`` is null for an appearance-only SKU. The format number
        is deliberately NOT bumped for that: the key is still always present
        and every reader that ever existed either understands the null or
        refuses that one entry loudly (`GalleryEntry` raises on a footprint
        that is neither absent nor a positive real). A bump would instead make
        every old reader refuse every NEW file, including files with no
        appearance-only entry in them at all, which trades a loud failure on
        the affected entry for a loud failure on everything.
        """
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
            if "footprint_mm" not in rec:
                # A MISSING key is not the same claim as an explicit null. The
                # first is a truncated or foreign file; the second is a SKU
                # taught with no scale. Defaulting the first to the second
                # would quietly downgrade a mat-taught entry to the weaker
                # mode, so it is refused.
                raise IdentityError(
                    f"{sku_id!r}: no 'footprint_mm' key. Write null to mean "
                    f"'taught from a photo with no scale'; omitting it means "
                    f"nothing at all."
                )
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
    #: Which mode top1 was judged in — the answer to "was this size-checked?".
    #: Set even when we abstain, because the UI copy differs: an appearance-only
    #: near-miss should offer "re-teach it on the mat", a gated one should not.
    mode: str = MODE_NONE
    #: The similarity bar top1 actually had to clear. phi for a gated decision,
    #: the higher PHI_APPEARANCE_ONLY for one with no size check. None when
    #: nothing was ranked, because then no bar was applied to anything.
    phi_applied: float | None = None
    #: How many of the shortlist could not be size-checked.
    n_appearance_only: int = 0

    @property
    def is_amber(self) -> bool:
        return self.sku_id is None

    @property
    def is_match(self) -> bool:
        return self.sku_id is not None

    @property
    def is_appearance_only(self) -> bool:
        """True when nothing about size was checked. A caller that bills on
        this is billing on the weaker evidence and should say so on screen."""
        return self.mode == MODE_APPEARANCE_ONLY

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
            "mode": self.mode,
            "phi_applied": (
                None if self.phi_applied is None else round(self.phi_applied, 6)
            ),
            "n_appearance_only": self.n_appearance_only,
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
    #: How the pair was judged. MODE_APPEARANCE_ONLY means at least one side had
    #: no footprint, so size could not be used to let the pair through — the
    #: guard is STRICTER there, not looser.
    mode: str = MODE_FOOTPRINT_GATED

    @property
    def message(self) -> str:
        if not self.collides:
            return "no collision"
        if self.footprint_delta_mm is None:
            return (
                f"indistinguishable from {self.sku_id!r}: cosine "
                f"{self.similarity:.4f}, and one of the two was taught with no "
                f"footprint, so size can never separate them — a disambiguation "
                f"capture is required"
            )
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
            "mode": self.mode,
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
        phi_appearance_only: float | None = None,
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
        # Default: the measured constant, but never below whatever bar the
        # operator set for the STRONGER mode. An operator who raises phi to
        # 0.97 has said appearance is worth less than they thought, and that
        # applies at least as hard where there is no size check at all.
        if phi_appearance_only is None:
            self.phi_appearance_only = max(PHI_APPEARANCE_ONLY, self.phi)
        else:
            self.phi_appearance_only = float(phi_appearance_only)
        if self.phi_appearance_only < self.phi:
            raise IdentityError(
                f"phi_appearance_only={self.phi_appearance_only} is below "
                f"phi={self.phi}. A decision with no size check has one fewer "
                f"discriminator, so it must never be the EASIER one to pass — "
                f"invariant 7 does not allow buying the weak mode a discount."
            )

    def __repr__(self) -> str:
        return (
            f"Identifier(n_skus={len(self.gallery)}, theta={self.theta}, "
            f"phi={self.phi}, phi_appearance_only={self.phi_appearance_only}, "
            f"tau_mm={self.tau_mm})"
        )

    def bar_for(self, entry: GalleryEntry, long_edge_mm: float | None) -> float:
        """The similarity `entry` must reach to be returned for this query."""
        return (self.phi if entry.mode_against(long_edge_mm) == MODE_FOOTPRINT_GATED
                else self.phi_appearance_only)

    # -- the metric tiebreak, run FIRST ------------------------------------
    def candidates(self, long_edge_mm: float | None) -> tuple[GalleryEntry, ...]:
        """Who is even allowed to compete for this query.

        An entry WITH a footprint, asked about a query WITH millimetres, is
        admitted only within tau_mm — the gate, unchanged and unsoftened. An
        entry with no footprint, or any entry when the query has no
        millimetres, has no metric to be gated on and is admitted to be judged
        on appearance at the higher bar instead. Skipping a check we cannot run
        is not the same as passing it, and `Identification.mode` says which
        happened.

        Sorted by sku_id so the shortlist never depends on enrolment order.
        """
        lo = _optional_mm(long_edge_mm)
        return tuple(
            e for e in self.gallery.entries()
            if e.mode_against(lo) == MODE_APPEARANCE_ONLY
            or abs(float(e.footprint_mm) - float(lo)) <= self.tau_mm
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
    def identify(
        self, crop: np.ndarray, long_edge_mm: float | None
    ) -> Identification:
        """Propose a SKU for one rectified crop measured at long_edge_mm.

        long_edge_mm must be a positive real, or None meaning "this photo has
        no scale". None is NOT a fallback for a failed measurement: if the
        plane was supposed to lock and did not, the caller abstains upstream
        rather than quietly downgrading the sale to the weaker mode. It is for
        a photo that never had a mat in it at all.

        With no millimetres nothing can be size-checked, so every entry
        competes and every entry is judged at the higher appearance-only bar.
        The pairs that the enrolment guard admitted ONLY because the mat could
        separate them (a 500 ml and a 1 L bottle) then land next to each other
        in the ranking and fail the top-2 margin, which is exactly right: amber
        is the correct answer to "which of these two sizes is it?" when the
        size is unknown.
        """
        lo = _optional_mm(long_edge_mm)
        shortlist = self.candidates(lo)
        n_weak = sum(1 for e in shortlist
                     if e.mode_against(lo) == MODE_APPEARANCE_ONLY)
        if not shortlist:
            return Identification(
                None, 0.0, 0.0, 0.0, REASON_NO_CANDIDATE,
                n_candidates=0, long_edge_mm=lo, mode=MODE_NONE,
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

        # The bar is the WINNER's bar. A decision is only as strong as the
        # evidence behind the entry it names, so an appearance-only leader has
        # to clear the appearance-only bar even in a shortlist full of
        # footprinted rivals — and a footprinted leader is not made to pay for
        # a weak entry sitting behind it in the ranking.
        winner = self.gallery.get(top1_sku)
        mode = winner.mode_against(lo)
        bar = self.bar_for(winner, lo)

        common = dict(
            top1_sku=top1_sku,
            top2_sku=top2_sku,
            n_candidates=len(shortlist),
            long_edge_mm=lo,
            mode=mode,
            phi_applied=bar,
            n_appearance_only=n_weak,
        )
        if top1 < bar:
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
        self, new_vectors: Iterable[Any], new_footprint: float | None
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

        WHEN EITHER SIDE HAS NO FOOTPRINT the second bound cannot be evaluated,
        and the honest reading of "cannot be evaluated" is "cannot save the
        pair": there is no query, at any size, that would put those two in
        different shortlists. So appearance decides alone and the guard gets
        STRICTER, not looser. A consequence worth stating: teaching a 1 L
        bottle from a plain photo will be refused against a mat-taught 500 ml
        bottle it resembles, even though the mat separates those two fine
        today. That refusal is correct — the new entry would compete against
        every query regardless of size — and the fix is to teach it on the mat,
        which is what the refusal says.
        """
        return self._collide(
            self.gallery,
            [as_vector(v) for v in new_vectors],
            _optional_mm(new_footprint),
        )

    def _collide(
        self, gallery: Gallery, rows: list[np.ndarray], fp: float | None
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
        hits: list[tuple[float, float | None, str]] = []
        for e in gallery.entries():
            sim = float(np.clip(float(np.max(new_unit @ e.unit.T)), -1.0, 1.0))
            if sim < appearance_bar:
                continue
            if e.footprint_mm is None or fp is None:
                # No metric on one side: no query can ever separate them.
                hits.append((sim, None, e.sku_id))
                continue
            delta = abs(float(e.footprint_mm) - float(fp))
            if delta <= self.tau_mm:
                hits.append((sim, delta, e.sku_id))

        if not hits:
            return Collision(
                False, None, 0.0, None, "clear",
                mode=(MODE_APPEARANCE_ONLY if fp is None
                      else MODE_FOOTPRINT_GATED),
            )
        # Worst offender first: highest cosine, then — among equals — the pair
        # size could not separate, since that one is inseparable at every size
        # rather than merely at this one. `-1.0` sorts before any real delta.
        hits.sort(key=lambda t: (-t[0], -1.0 if t[1] is None else t[1], t[2]))
        sim, delta, sku = hits[0]
        return Collision(
            True, sku, sim, delta, "collision",
            colliding=tuple(sorted(h[2] for h in hits)),
            mode=(MODE_FOOTPRINT_GATED if delta is not None
                  else MODE_APPEARANCE_ONLY),
        )

    def enroll(
        self,
        sku_id: str,
        vectors: Iterable[Any],
        footprint_mm: float | None,
        *,
        force: bool = False,
        replace: bool = False,
    ) -> GalleryEntry:
        """Guarded enrolment. Raises CollisionError (carrying the colliding
        sku) unless force=True, so the UI can demand a disambiguation capture
        instead of poisoning the gallery.

        ``footprint_mm=None`` enrols an appearance-only SKU and is guarded on
        appearance alone — see check_collision."""
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
            c = self._collide(probe, rows, _optional_mm(footprint_mm))
            if c.collides:
                raise CollisionError(c)
        return self.gallery.enroll(sku_id, rows, footprint_mm, replace=replace)


def _require_mm(value: Any) -> float:
    """A real, positive measurement in millimetres, or raise.

    None is refused HERE and allowed only by `_optional_mm`, so every caller
    has to say in one word which of the two it means.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, np.floating,
                                                         np.integer)):
        raise IdentityError(
            f"a measured long edge in mm is required, got {value!r} — pass None "
            "only to mean 'this photo has no scale at all', never to stand in "
            "for a measurement that failed"
        )
    v = float(value)
    if not np.isfinite(v) or v <= 0.0:
        raise IdentityError(f"long edge must be a positive real, got {value!r}")
    return v


def _optional_mm(value: Any, who: str = "") -> float | None:
    """`_require_mm`, except that None passes through as None.

    ABSENT AND INVALID ARE DIFFERENT THINGS. None means "taught from a photo
    with no scale" and is a supported, weaker mode. 0, -3, NaN, inf, True and
    "118" are all still refused by name, because each of those is a bug that
    would otherwise be laundered into the weak mode and never seen again.
    """
    if value is None:
        return None
    try:
        return _require_mm(value)
    except IdentityError as e:
        raise IdentityError(f"{who + ': ' if who else ''}{e}") from None
