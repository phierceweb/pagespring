"""liveness: a crawl can fetch successfully while saving nothing, which only progress can show."""

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
