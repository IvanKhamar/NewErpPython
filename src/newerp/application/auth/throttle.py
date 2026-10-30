"""Protection of the login from password guessing."""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from newerp.application.common import Clock


@dataclass(frozen=True)
class LoginThrottleOptions:
    max_failures_per_user: int = 10
    """Failures allowed per window for one username (protects an account attacked from many addresses)."""
    max_failures_per_address: int = 30
    """Failures allowed per window from one IP address (protects against trying many usernames)."""
    window_minutes: int = 15
    """Window from the first failure; after it the counter starts again."""


@dataclass(frozen=True)
class _Window:
    count: int
    until: datetime


class LoginThrottle:
    """
    Counts only failed attempts, so a person with the right password never hits the limit. When there are too
    many, the login for that username or address is closed until the window ends (even with the right password,
    otherwise guessing would go on). Counters live in the memory of one process; a restart resets them.
    """

    # Memory limit for an attack with thousands of invented usernames: then expired counters are dropped first.
    MAX_ENTRIES = 10_000

    def __init__(self, options: LoginThrottleOptions, clock: Clock) -> None:
        self._options = options
        self._clock = clock
        self._failures: dict[str, _Window] = {}

    def retry_after(self, address: str | None, username: str | None) -> timedelta | None:
        """How long to wait if the login is closed now; None means the user may try."""
        now = self._clock.utc_now()
        wait: timedelta | None = None
        for key, limit in self._keys(address, username):
            window = self._failures.get(key)
            if window and window.until > now and window.count >= limit:
                left = window.until - now
                wait = left if wait is None or left > wait else wait
        return wait

    def failed(self, address: str | None, username: str | None) -> None:
        now = self._clock.utc_now()
        if len(self._failures) >= self.MAX_ENTRIES:
            self._prune(now)
        for key, _ in self._keys(address, username):
            window = self._failures.get(key)
            if window is None or window.until <= now:
                self._failures[key] = _Window(1, now + timedelta(minutes=self._options.window_minutes))
            else:
                self._failures[key] = _Window(window.count + 1, window.until)

    def succeeded(self, username: str | None) -> None:
        """A successful login resets the username counter; the address counter stays."""
        self._failures.pop(self._user_key(username), None)

    def _keys(self, address: str | None, username: str | None) -> list[tuple[str, int]]:
        return [
            (self._user_key(username), self._options.max_failures_per_user),
            ("ip:" + (address or "unknown"), self._options.max_failures_per_address),
        ]

    @staticmethod
    def _user_key(username: str | None) -> str:
        return "user:" + (username or "").strip().lower()

    def _prune(self, now: datetime) -> None:
        self._failures = {k: w for k, w in self._failures.items() if w.until > now}
        # Still too many live counters: a mass attack; starting over is better than running out of memory.
        if len(self._failures) >= self.MAX_ENTRIES:
            self._failures.clear()


def whole_minutes(wait: timedelta) -> int:
    return math.ceil(wait.total_seconds() / 60)
