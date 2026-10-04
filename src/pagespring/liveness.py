"""Progress watchdog for queue-driven crawls, which can fetch 200s forever while saving nothing. A
stall breaks the loop with work queued, so it surfaces as ``truncated``."""

from __future__ import annotations

import time
from collections.abc import Callable

from pf_core.exceptions import InvalidInputError


class ProgressWatchdog:
    """Time since the last real progress; ``stalled()`` past ``stall_after_s`` idle seconds. 0
    disables it, and a negative window raises, so a typo'd config can't silently drop the guard."""

    def __init__(self, *, stall_after_s: float, now: Callable[[], float] = time.monotonic) -> None:
        if stall_after_s < 0:
            raise InvalidInputError(f"stall_after_s must be >= 0 (0 disables), got {stall_after_s}")
        self._window = stall_after_s
        self._now = now
        self._last = now()

    def progress(self) -> None:
        """Call when the crawl produced something — a page saved, not a page fetched."""
        self._last = self._now()

    def idle_s(self) -> float:
        return self._now() - self._last

    def stalled(self) -> bool:
        return bool(self._window) and self.idle_s() > self._window
