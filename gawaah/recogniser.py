"""S5b — DAAM: "what is this, and what does it cost?"

`identity.Identifier` answers the first half of that question and refuses to
answer it badly. `paisa`/`ShopStore` hold the second half. THIS MODULE IS THE
ONLY PLACE THE TWO ARE JOINED, and that is the entire reason it exists.

Why one place and not two
-------------------------
Before this module, `brain.py` called `identifier.identify(...)`, then looked
the price up separately, and every other consumer that wanted to price a crop
had to remember to do the same two steps in the same order with the same
failure handling. Two steps in N places is how a system ends up billing zero:
somebody writes `price = prices.get(sku, 0)` at 2 a.m. and a shopkeeper gives
away a bar of soap on every till in the fleet. Joining them here means there is
exactly one function that can turn a crop into money, it is 60 lines long, and
it cannot return a price without also returning the sku it belongs to.

Abstention is the feature (invariant 7)
---------------------------------------
`Recognition.abstained` is true on EVERY path that cannot name an item AND
price it with confidence, and on those paths `price_paise` is None and the
caller must exclude the line from the total. The reasons are distinct because
the shopkeeper's next action differs:

  no_gallery                 nothing has been taught yet.  -> "teach me this"
  no_footprint               the mat gave no millimetres.   -> re-place the item
  no_candidate_in_footprint  nothing enrolled is this SIZE. -> offer enrolment
  below_similarity           nothing enrolled LOOKS like it.-> offer enrolment
  ambiguous_pair             two enrolled items tie.        -> name both, ask
  below_margin               a leader, but not by enough.    -> suggest, do not bill
  no_price_for_sku           we know WHAT it is and not what it COSTS.
                             -> the sku IS reported; the UI asks for a price.
                                This is the one abstention that still carries a
                                sku_id, and it is the difference between
                                "I do not know" and "you never told me".
  price_not_integer_paise    the catalog answered with a float or a string.
                             A rounded price is a wrong price. -> refuse.
  price_not_positive         a zero or negative price. A free line at a till is
                             indistinguishable from a bug, and the cost of
                             being wrong is a tap. -> refuse, do not bill 0.
  embed_failed               the descriptor or the gallery blew up on this crop.

The four `identity` reasons are passed through UNCHANGED — this module never
renames them, so a reason code means the same thing in the ledger, in the audit
log and on the panel.

The gates are NEVER widened here
--------------------------------
theta / phi / tau_mm are handed straight to `Identifier` and are published in
`stats()["gates"]`, together with `gates_are_default`. Any accuracy number
produced by this class therefore carries the thresholds it was produced under,
which is the only way an abstention rate means anything at all.

Money
-----
Every price crosses `money.paise()` before it is allowed out, so a catalog that
answers 21.99 (a float) produces an AMBER line and a named reason, never a
silently truncated 21 paise. Nothing here settles anything: invariant 2 says
GREEN comes only from a signature-verified webhook, and recognition proposes a
number that a human and a gateway still have to agree to.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol

import numpy as np

# identity's four reason codes are imported to be RE-EXPORTED (see __all__), so
# a consumer has one import site for every reason a line can carry and never
# has to know which of the two modules invented which string.
from gawaah.identity import (
    ABSTAIN_REASONS,
    DEFAULT_PHI,
    DEFAULT_TAU_MM,
    DEFAULT_THETA,
    REASON_AMBIGUOUS,
    REASON_BELOW_MARGIN,
    REASON_BELOW_SIMILARITY,
    REASON_MATCH,
    REASON_NO_CANDIDATE,
    Gallery,
    Identification,
    Identifier,
    IdentityError,
)
from gawaah.money import MoneyError
from gawaah.money import paise as make_paise
from gawaah.money import total as money_total

# ------------------------------------------------------------------- reasons

#: Nothing has been enrolled at all. Distinct from "nothing matched": an empty
#: shop is a setup state with a different call to action.
REASON_NO_GALLERY = "no_gallery"

#: The caller had no metric long edge. identity.py treats this as a programmer
#: error and raises; at a till the till-safe answer is an amber line, so it is
#: converted into a NAMED, COUNTED abstention here rather than a traceback.
REASON_NO_FOOTPRINT = "no_footprint"

#: Identified, but no price was ever recorded. Same string brain.py already
#: uses, deliberately: one reason code, one meaning, one grep.
REASON_NO_PRICE = "no_price_for_sku"

#: The catalog answered with something that is not integer paise.
REASON_BAD_PRICE = "price_not_integer_paise"

#: A zero or negative price. See the module docstring.
REASON_NONPOSITIVE_PRICE = "price_not_positive"

#: The embedder or the gallery raised on this crop.
REASON_EMBED_FAILED = "embed_failed"

#: Every reason a Recognition can carry when it abstains. `identity`'s four
#: come first and unchanged; the rest are the ones only a priced recogniser can
#: produce.
RECOGNISER_ABSTAIN_REASONS: tuple[str, ...] = ABSTAIN_REASONS + (
    REASON_NO_GALLERY,
    REASON_NO_FOOTPRINT,
    REASON_NO_PRICE,
    REASON_BAD_PRICE,
    REASON_NONPOSITIVE_PRICE,
    REASON_EMBED_FAILED,
)

#: Match plus every abstention. `stats()["by_reason"]` always has all of these
#: keys, so a dashboard never has to guess whether a missing key means zero or
#: means the code path was deleted.
ALL_REASONS: tuple[str, ...] = (REASON_MATCH,) + RECOGNISER_ABSTAIN_REASONS


class RecogniserError(RuntimeError):
    """A wiring error: a store that is not a catalog, an inconsistent
    Recognition. Never an abstention — abstaining is a normal result."""


# --------------------------------------------------------------------- store

class CatalogStore(Protocol):
    """What the recogniser needs from the shopkeeper's catalog.

    Two methods, and they must agree with each other about which SKUs exist.
    `gawaah.shop_store.ShopStore` satisfies this; so does `MemoryStore` below;
    so does anything else that can produce a Gallery and a price.
    """

    def to_gallery(self) -> Gallery: ...

    def price_paise(self, sku_id: str) -> Optional[int]: ...


class MemoryStore:
    """The smallest honest catalog: vectors in a Gallery, prices in a dict.

    Exists so this module and the brain can be exercised with no disk and no
    enrolment UI. It is deliberately DUMB — it does not run the collision
    guard, because refusing a colliding enrolment is a decision that belongs to
    the enrolment surface, where a human is standing there holding the item and
    can take a second photograph.

    `to_gallery()` returns a SNAPSHOT, not the live gallery. That is what makes
    `Recogniser.reload()` mean something: a recogniser built against this store
    keeps pricing the catalog it was built with until somebody reloads it, so
    a half-finished enrolment can never leak into a sale in progress.
    """

    def __init__(self, prices: Mapping[str, int] | None = None) -> None:
        self._gallery = Gallery()
        self._prices: dict[str, int] = {}
        for sku_id, value in (prices or {}).items():
            self._prices[sku_id] = int(make_paise(value))

    def add(
        self,
        sku_id: str,
        vectors: Iterable[Any],
        footprint_mm: float,
        price_paise: Optional[int] = None,
        *,
        replace: bool = False,
    ) -> None:
        self._gallery.enroll(sku_id, vectors, footprint_mm, replace=replace)
        if price_paise is None:
            self._prices.pop(sku_id, None)
        else:
            self._prices[sku_id] = int(make_paise(price_paise))

    def set_price(self, sku_id: str, price_paise: Optional[int]) -> None:
        """Price an already-enrolled sku, or un-price it with None."""
        if sku_id not in self._gallery:
            raise RecogniserError(f"{sku_id!r} is not enrolled")
        if price_paise is None:
            self._prices.pop(sku_id, None)
        else:
            self._prices[sku_id] = int(make_paise(price_paise))

    def remove(self, sku_id: str) -> None:
        self._gallery.remove(sku_id)
        self._prices.pop(sku_id, None)

    def to_gallery(self) -> Gallery:
        return Gallery.from_dict(self._gallery.to_dict())

    def price_paise(self, sku_id: str) -> Optional[int]:
        return self._prices.get(sku_id)

    def skus(self) -> tuple[str, ...]:
        return self._gallery.skus()

    def __len__(self) -> int:
        return len(self._gallery)

    def __repr__(self) -> str:
        return f"MemoryStore(n={len(self)}, priced={len(self._prices)})"


def _gallery_fn(store: Any) -> Callable[[], Gallery]:
    """Bind the store's gallery accessor, or explain why it is not a catalog."""
    if isinstance(store, Gallery):
        raise RecogniserError(
            "a Gallery is not a catalog: it has vectors but no prices. Wrap it "
            "in MemoryStore, or pass a gawaah.shop_store.ShopStore."
        )
    fn = getattr(store, "to_gallery", None)
    if callable(fn):
        return fn
    attr = getattr(store, "gallery", None)
    if isinstance(attr, Gallery):
        return lambda: attr
    if callable(attr):
        return attr
    if isinstance(store, Mapping):
        raise RecogniserError(
            "a price map is not a catalog: it has prices but no enrolled "
            "vectors, so nothing can be recognised. Use MemoryStore."
        )
    raise RecogniserError(
        f"{type(store).__name__} is not a catalog: it needs a to_gallery() "
        "returning a gawaah.identity.Gallery"
    )


def _price_fn(store: Any) -> Callable[[str], Any]:
    """Bind the store's price accessor.

    Tolerant on purpose about WHERE the price lives (a method, a price_book, a
    mapping) and utterly intolerant about WHAT it is: whatever comes back still
    has to pass money.paise() before it can be billed.
    """
    fn = getattr(store, "price_paise", None)
    if callable(fn):
        return fn
    book_fn = getattr(store, "price_book", None)
    if callable(book_fn):
        def _via_book(sku_id: str) -> Any:
            book = book_fn()
            inner = getattr(book, "price_paise", None)
            if callable(inner):
                return inner(sku_id)
            if isinstance(book, Mapping):
                return book.get(sku_id)
            raise RecogniserError(
                f"price_book() returned {type(book).__name__}, which is neither "
                "a mapping nor a PriceBook"
            )
        return _via_book
    prices = getattr(store, "prices", None)
    if isinstance(prices, Mapping):
        return prices.get
    raise RecogniserError(
        f"{type(store).__name__} cannot price a sku: it needs "
        "price_paise(sku_id) -> int | None"
    )


def _default_embed_fn() -> Callable[[np.ndarray], Any]:
    """Resolve the repo's classical descriptor, if it is installed.

    The embedder is INJECTED — that is identity.py's contract and this module
    keeps it. This is only a convenience for callers who want the default, and
    it fails with a sentence rather than an ImportError traceback.
    """
    try:
        from gawaah.embedder2 import embed  # noqa: PLC0415  (deliberately lazy)
    except ImportError as exc:
        raise RecogniserError(
            "no embed_fn was given and gawaah.embedder is not importable "
            f"({exc}). The embedder is injected: pass embed_fn=..."
        ) from None
    return embed


# -------------------------------------------------------------------- result

@dataclass(frozen=True)
class Recognition:
    """One crop, one verdict, and the money that follows from it.

    The class enforces its own consistency in __post_init__, because this is
    the one object in the system that can put a number on a bill:

        abstained is True  <=>  price_paise is None  <=>  do not bill this line

    and a price may only ride along with a sku and the "match" reason. There is
    no way to construct a Recognition that says "I do not know what this is,
    that will be 45 rupees".
    """

    sku_id: Optional[str]
    price_paise: Optional[int]
    reason: str
    top1: float
    top2: float
    margin: float
    abstained: bool
    top1_sku: Optional[str] = None
    top2_sku: Optional[str] = None
    n_candidates: int = 0
    n_skus: int = 0
    long_edge_mm: Optional[float] = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.abstained != (self.price_paise is None):
            raise RecogniserError(
                f"inconsistent Recognition: abstained={self.abstained} but "
                f"price_paise={self.price_paise!r} — an abstention never carries "
                "a price and a decision always does"
            )
        if self.price_paise is None:
            if self.reason == REASON_MATCH:
                raise RecogniserError(
                    "a Recognition with no price cannot be reason 'match'"
                )
            if self.reason not in RECOGNISER_ABSTAIN_REASONS:
                raise RecogniserError(f"unnamed abstention reason {self.reason!r}")
            return
        if self.reason != REASON_MATCH:
            raise RecogniserError(
                f"a priced Recognition must be reason 'match', got {self.reason!r}"
            )
        if self.sku_id is None:
            raise RecogniserError("a price without a sku is a price out of thin air")
        if isinstance(self.price_paise, bool) or not isinstance(self.price_paise, int):
            raise RecogniserError(
                f"price must be integer paise, got {self.price_paise!r}"
            )
        if self.price_paise <= 0:
            raise RecogniserError(
                f"a billable line must cost something, got {self.price_paise} paise"
            )

    @property
    def is_billable(self) -> bool:
        """True exactly when this line may be added to a total."""
        return not self.abstained

    @property
    def is_amber(self) -> bool:
        return self.abstained

    def to_audit(self) -> dict:
        """Plain-JSON fields for Ledger.append(**fields)."""
        return {
            "sku_id": self.sku_id,
            "price_paise": self.price_paise,
            "reason": self.reason,
            "abstained": self.abstained,
            "top1_sku": self.top1_sku,
            "top2_sku": self.top2_sku,
            "top1": round(self.top1, 6),
            "top2": round(self.top2, 6),
            "margin": round(self.margin, 6),
            "n_candidates": self.n_candidates,
            "n_skus": self.n_skus,
            "long_edge_mm": (
                None if self.long_edge_mm is None else round(self.long_edge_mm, 3)
            ),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ReloadResult:
    """What `reload()` actually changed. Returned rather than logged so a
    caller can show "1 new product" instead of guessing."""

    added: tuple[str, ...]
    removed: tuple[str, ...]
    n_skus: int

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)


# ---------------------------------------------------------------- recogniser

class Recogniser:
    """Names an item from the shopkeeper's taught catalog, and prices it.

    Holds an `Identifier` built from a SNAPSHOT of the store's gallery. The
    snapshot is swapped atomically by `reload()`, so a sale in progress is
    never re-priced halfway through by an enrolment happening in another tab.
    """

    def __init__(
        self,
        store: Any,
        embed_fn: Optional[Callable[[np.ndarray], Any]] = None,
        theta: float = DEFAULT_THETA,
        phi: float = DEFAULT_PHI,
        tau_mm: float = DEFAULT_TAU_MM,
        *,
        strict: bool = False,
    ) -> None:
        """`strict=True` re-raises an embedder failure instead of turning it
        into an `embed_failed` abstention. Off by default because a till must
        show amber rather than a traceback; on in tests and in the bench, where
        a silently swallowed dimension mismatch would be a lie."""
        self.store = store
        self._gallery_of = _gallery_fn(store)
        self._price_of = _price_fn(store)
        self.embed_fn = embed_fn if embed_fn is not None else _default_embed_fn()
        if not callable(self.embed_fn):
            raise RecogniserError("embed_fn must be callable — the embedder is injected")
        self.theta = float(theta)
        self.phi = float(phi)
        self.tau_mm = float(tau_mm)
        self.strict = bool(strict)
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {r: 0 for r in ALL_REASONS}
        self._identifier: Optional[Identifier] = None
        self.reload()

    def __repr__(self) -> str:
        return (
            f"Recogniser(n_skus={self.n_skus}, theta={self.theta}, phi={self.phi}, "
            f"tau_mm={self.tau_mm}, abstention_rate={self.abstention_rate:.3f})"
        )

    # -- catalog ------------------------------------------------------------
    @property
    def identifier(self) -> Identifier:
        idf = self._identifier
        if idf is None:  # pragma: no cover — __init__ always reloads
            raise RecogniserError("recogniser was never loaded")
        return idf

    @property
    def gallery(self) -> Gallery:
        return self.identifier.gallery

    @property
    def n_skus(self) -> int:
        return len(self.identifier.gallery)

    def skus(self) -> tuple[str, ...]:
        return self.identifier.gallery.skus()

    def reload(self, *, deep: bool = True) -> ReloadResult:
        """Re-read the catalog. Picks up SKUs enrolled since start without
        reconstructing this object, and WITHOUT resetting the statistics — the
        abstention rate is a running record of this counter's day, and an
        enrolment is not a reason to forget how often it said "I don't know".

        `deep` (the default) first asks the STORE to re-read itself, because
        the enrolment surface and the counter are different processes: the
        upload app writes the catalog on :8790 and the brain reads it on :8787,
        so a recogniser that only re-snapshotted its own store's memory would
        never see a product the shopkeeper just taught. If that re-read raises
        — a corrupt catalog — it is allowed to propagate: an unpriced counter
        with a loud error beats a silently empty shop.
        """
        if deep:
            store_reload = getattr(self.store, "reload", None)
            if callable(store_reload):
                store_reload()
        gallery = self._gallery_of()
        if not isinstance(gallery, Gallery):
            raise RecogniserError(
                f"store.to_gallery() returned {type(gallery).__name__}, "
                "expected gawaah.identity.Gallery"
            )
        before = self.identifier.gallery.skus() if self._identifier else ()
        idf = Identifier(gallery, self.embed_fn, self.theta, self.phi, self.tau_mm)
        # One attribute write: any concurrent identify() sees either the whole
        # old catalog or the whole new one, never a half-built gallery.
        self._identifier = idf
        after = gallery.skus()
        return ReloadResult(
            added=tuple(s for s in after if s not in before),
            removed=tuple(s for s in before if s not in after),
            n_skus=len(gallery),
        )

    # -- the decision -------------------------------------------------------
    def identify(self, crop: Any, long_edge_mm: Any) -> Recognition:
        """Name and price one crop measured at `long_edge_mm` millimetres.

        Never raises for a bad crop, a missing measurement or an empty shop:
        each of those is a named abstention that the caller shows amber and
        excludes from the total. It raises only for a broken CALLER contract
        that `strict` was asked to surface.
        """
        idf = self.identifier
        n_skus = len(idf.gallery)

        if n_skus == 0:
            return self._record(Recognition(
                None, None, REASON_NO_GALLERY, 0.0, 0.0, 0.0, True,
                n_skus=0, detail="nothing has been enrolled yet",
            ))

        try:
            edge_mm = _require_mm(long_edge_mm)
        except IdentityError as exc:
            return self._record(Recognition(
                None, None, REASON_NO_FOOTPRINT, 0.0, 0.0, 0.0, True,
                n_skus=n_skus, detail=str(exc),
            ))

        try:
            ident = idf.identify(crop, edge_mm)
        except Exception as exc:  # noqa: BLE001 — see `strict` in __init__
            if self.strict:
                raise
            return self._record(Recognition(
                None, None, REASON_EMBED_FAILED, 0.0, 0.0, 0.0, True,
                n_skus=n_skus, long_edge_mm=edge_mm,
                detail=f"{type(exc).__name__}: {exc}",
            ))

        return self._record(self._price(ident, n_skus))

    def identify_many(
        self, crops: Iterable[tuple[Any, Any]]
    ) -> tuple[Recognition, ...]:
        """`identify` over `(crop, long_edge_mm)` pairs, in order."""
        return tuple(self.identify(c, mm) for c, mm in crops)

    def _price(self, ident: Identification, n_skus: int) -> Recognition:
        common = dict(
            top1_sku=ident.top1_sku,
            top2_sku=ident.top2_sku,
            n_candidates=ident.n_candidates,
            n_skus=n_skus,
            long_edge_mm=ident.long_edge_mm,
        )
        if ident.sku_id is None:
            # identity abstained. Its reason is passed through UNCHANGED.
            return Recognition(
                None, None, ident.reason,
                ident.top1, ident.top2, ident.margin, True, **common,
            )

        value, reason, detail = self._lookup(ident.sku_id)
        if value is None:
            # We know WHAT it is. We still refuse to bill it, and we say the
            # sku out loud so the shopkeeper can fix the catalog in one tap.
            return Recognition(
                ident.sku_id, None, reason,
                ident.top1, ident.top2, ident.margin, True,
                detail=detail, **common,
            )
        return Recognition(
            ident.sku_id, value, REASON_MATCH,
            ident.top1, ident.top2, ident.margin, False, **common,
        )

    def _lookup(self, sku_id: str) -> tuple[Optional[int], str, str]:
        """The price half. Returns (paise|None, reason, detail).

        A price only escapes this function through money.paise(), so nothing
        downstream ever has to wonder whether a bill is exact.
        """
        try:
            raw = self._price_of(sku_id)
        except Exception as exc:  # noqa: BLE001 — a broken catalog is amber
            if self.strict:
                raise
            return None, REASON_NO_PRICE, f"{type(exc).__name__}: {exc}"
        if raw is None:
            return None, REASON_NO_PRICE, f"no price recorded for {sku_id!r}"
        try:
            value = int(make_paise(raw))
        except MoneyError as exc:
            return None, REASON_BAD_PRICE, str(exc)
        if value <= 0:
            return (
                None,
                REASON_NONPOSITIVE_PRICE,
                f"{sku_id!r} is priced at {value} paise",
            )
        return value, REASON_MATCH, ""

    # -- the published numbers ---------------------------------------------
    def _record(self, r: Recognition) -> Recognition:
        with self._lock:
            self._counts[r.reason] = self._counts.get(r.reason, 0) + 1
        return r

    def stats(self) -> dict:
        """Counts by reason, the abstention rate, and the gates they were
        produced under. The gates are in here on purpose: an abstention rate
        quoted without theta/phi/tau is not a measurement, it is a mood."""
        with self._lock:
            counts = dict(self._counts)
        seen = sum(counts.values())
        decided = counts[REASON_MATCH]
        held = seen - decided
        return {
            "n": seen,
            "decided": decided,
            "abstained": held,
            "abstention_rate": (held / seen) if seen else 0.0,
            "by_reason": counts,
            "n_skus": self.n_skus,
            "skus": list(self.skus()),
            "gates": {
                "theta": self.theta,
                "phi": self.phi,
                "tau_mm": self.tau_mm,
            },
            "gates_are_default": (
                self.theta == DEFAULT_THETA
                and self.phi == DEFAULT_PHI
                and self.tau_mm == DEFAULT_TAU_MM
            ),
        }

    @property
    def abstention_rate(self) -> float:
        s = self.stats()
        return float(s["abstention_rate"])

    def reset_stats(self) -> None:
        """Start a fresh run of counting. Never called implicitly."""
        with self._lock:
            self._counts = {r: 0 for r in ALL_REASONS}


# ------------------------------------------------------------------ helpers

def billable(recognitions: Iterable[Recognition]) -> tuple[Recognition, ...]:
    return tuple(r for r in recognitions if r.is_billable)


def abstentions(recognitions: Iterable[Recognition]) -> tuple[Recognition, ...]:
    return tuple(r for r in recognitions if r.abstained)


def basket_paise(recognitions: Iterable[Recognition]) -> int:
    """Integer paise for the DECIDED lines only.

    The exclusion is structural rather than remembered: an abstention has no
    price to add, so there is nothing here for a caller to forget to skip.
    """
    return int(money_total([r.price_paise for r in billable(recognitions)]))


def _require_mm(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(
        value, (int, float, np.floating, np.integer)
    ):
        raise IdentityError(
            f"a measured long edge in mm is required, got {value!r}"
        )
    v = float(value)
    if not np.isfinite(v) or v <= 0.0:
        raise IdentityError(f"long edge must be a positive real, got {value!r}")
    return v


__all__ = [
    "ALL_REASONS",
    "CatalogStore",
    "MemoryStore",
    "REASON_AMBIGUOUS",
    "REASON_BAD_PRICE",
    "REASON_BELOW_MARGIN",
    "REASON_BELOW_SIMILARITY",
    "REASON_EMBED_FAILED",
    "REASON_MATCH",
    "REASON_NONPOSITIVE_PRICE",
    "REASON_NO_CANDIDATE",
    "REASON_NO_FOOTPRINT",
    "REASON_NO_GALLERY",
    "REASON_NO_PRICE",
    "RECOGNISER_ABSTAIN_REASONS",
    "Recognition",
    "Recogniser",
    "RecogniserError",
    "ReloadResult",
    "abstentions",
    "basket_paise",
    "billable",
]
