"""
Injectable clock for the domain layer.
Production uses real time; tests use a fake clock for deterministic behavior.
"""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    """Protocol for injectable time source."""

    def now(self) -> float:
        """Return current time as Unix timestamp."""
        ...


class RealClock:
    """Production clock using real system time."""

    def now(self) -> float:
        return time.time()


class FakeClock:
    """
    Fake clock for testing. Time only advances when explicitly told to.
    Starts at a fixed epoch for reproducibility.
    """

    def __init__(self, start: float = 1_000_000.0):
        self._time = start

    def now(self) -> float:
        return self._time

    def advance(self, seconds: float) -> None:
        """Advance the clock by the given number of seconds."""
        if seconds < 0:
            raise ValueError("Cannot go back in time")
        self._time += seconds

    def set(self, time: float) -> None:
        """Set the clock to a specific time."""
        self._time = time
