"""Clock protocol. Every gate and every ledger line takes time as an argument,
so replays are byte-identical and tests do not depend on wall-clock."""
from __future__ import annotations

import datetime as _dt
from typing import Protocol


class Clock(Protocol):
    def now_iso(self) -> str: ...
    def monotonic_ns(self) -> int: ...


class RealClock:
    def now_iso(self) -> str:
        return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds")

    def monotonic_ns(self) -> int:
        import time
        return time.monotonic_ns()


class VirtualClock:
    """Deterministic clock for tests and replay."""

    def __init__(self, start: str = "2026-08-29T00:00:00.000+00:00", step_ms: int = 100):
        self._t = _dt.datetime.fromisoformat(start)
        self._step = _dt.timedelta(milliseconds=step_ms)
        self._mono = 0

    def now_iso(self) -> str:
        v = self._t.isoformat(timespec="milliseconds")
        self._t += self._step
        return v

    def monotonic_ns(self) -> int:
        self._mono += self._step.microseconds * 1000
        return self._mono
