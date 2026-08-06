"""Progress watchdog for queue-driven crawls.

A crawl can keep fetching successfully while producing nothing new — every
request returns 200 and nothing is slow, so no socket timeout sees it. Only
progress distinguishes a working crawl from a spinning one.

A stalled crawl breaks its loop with work still queued, so it surfaces through
the existing truncation path rather than a separate error channel.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from pf_core.exceptions import InvalidInputError


class ProgressWatchdog:
    """Tracks time since the last real progress; ``stalled()`` when it exceeds
    the window.

    Args:
        stall_after_s: Idle seconds before the crawl counts as stalled.
            ``0`` disables the watchdog. Negative is a caller error, not a
            quiet opt-out — a typo'd config must fail loudly rather than
            silently remove the guard.
        now: Monotonic clock, injectable for tests.
    """

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
