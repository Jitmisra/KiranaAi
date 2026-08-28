"""KAALA DABBA — the append-only, hash-chained audit log.

Every money action and every perception decision appends one line. The chain is
verifiable from genesis by a command that does not import the code that wrote it.

Design notes that are load-bearing:
  - Canonical JSON (sorted keys, no spaces, ensure_ascii=False) so the hash is
    reproducible across processes and machines.
  - prev_hash is inside the hashed payload, so reordering or deleting a line
    breaks every subsequent hash, not just the tampered one.
  - Timestamps are supplied by a Clock, never by wall-clock inside this module,
    so replays are byte-identical.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

GENESIS = "0" * 64


def canonical(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def entry_hash(payload: dict) -> str:
    return hashlib.sha256(canonical(payload)).hexdigest()


@dataclass
class Ledger:
    """Append-only hash chain persisted as JSONL."""

    path: Path
    _head: str = field(default=GENESIS, init=False)
    _count: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self._head = rec["hash"]
                    self._count += 1

    @property
    def head(self) -> str:
        return self._head

    @property
    def count(self) -> int:
        return self._count

    def append(self, *, ts: str, module: str, **fields: Any) -> str:
        """Append one auditable line. Returns the new head hash."""
        payload = {"ts": ts, "module": module, "prev_hash": self._head, **fields}
        h = entry_hash(payload)
        rec = {**payload, "hash": h}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")
        self._head = h
        self._count += 1
        return h

    def read(self) -> Iterator[dict]:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield json.loads(line)


def verify(path: Path) -> tuple[bool, int, str, str | None]:
    """Recompute the chain from genesis.

    Deliberately standalone: it does not use Ledger, so a bug in the writer
    cannot mask itself in the verifier.

    Returns (ok, lines_checked, head_hash, error_or_None).
    """
    path = Path(path)
    if not path.exists():
        return True, 0, GENESIS, None
    prev = GENESIS
    n = 0
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            return False, n, prev, f"line {i}: not valid JSON: {e}"
        stored = rec.pop("hash", None)
        if stored is None:
            return False, n, prev, f"line {i}: missing hash"
        if rec.get("prev_hash") != prev:
            return False, n, prev, (
                f"line {i}: chain break — prev_hash {rec.get('prev_hash')!r} "
                f"!= expected {prev!r}"
            )
        recomputed = entry_hash(rec)
        if recomputed != stored:
            return False, n, prev, (
                f"line {i}: hash mismatch — stored {stored[:16]}… "
                f"recomputed {recomputed[:16]}…"
            )
        prev = stored
        n += 1
    return True, n, prev, None
