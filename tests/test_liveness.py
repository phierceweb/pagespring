"""liveness — progress watchdog for queue-driven crawls.

A crawl can keep fetching successfully while saving nothing. A duplicate-id bug
did exactly that on the Logic Pro guide: after 1972 real pages it spent ~38
minutes re-fetching 1963 short-form duplicates that wrote no files. No socket
timeout can see this — every request completed fine. Only progress can.
"""

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring.liveness import ProgressWatchdog


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_fresh_watchdog_is_not_stalled():
    clock = _Clock()
    assert ProgressWatchdog(stall_after_s=300, now=clock).stalled() is False


def test_stalls_after_the_window_without_progress():
    clock = _Clock()
    wd = ProgressWatchdog(stall_after_s=300, now=clock)
    clock.advance(299)
    assert wd.stalled() is False
    clock.advance(2)
    assert wd.stalled() is True


def test_progress_resets_the_window():
    clock = _Clock()
    wd = ProgressWatchdog(stall_after_s=300, now=clock)
    clock.advance(299)
    wd.progress()
    clock.advance(299)
    assert wd.stalled() is False, "progress should have reset the clock"
    clock.advance(2)
    assert wd.stalled() is True


def test_zero_disables_the_watchdog():
    """Opting out must not degrade to 'stalls immediately'."""
    clock = _Clock()
    wd = ProgressWatchdog(stall_after_s=0, now=clock)
    clock.advance(10_000)
    assert wd.stalled() is False


def test_negative_is_rejected_rather_than_silently_disabling():
    with pytest.raises(InvalidInputError):
        ProgressWatchdog(stall_after_s=-1)


def test_reports_how_long_it_has_been_idle():
    clock = _Clock()
    wd = ProgressWatchdog(stall_after_s=300, now=clock)
    clock.advance(42)
    assert wd.idle_s() == pytest.approx(42)
