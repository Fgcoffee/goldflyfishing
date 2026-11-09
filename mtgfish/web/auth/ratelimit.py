"""Rate limits: a sliding window per key, in memory.

In memory is right for one server process (which the in-memory sessions and
job queue already require). Several processes would need a shared store such
as Redis behind the same ``hit`` call.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimited(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"too many attempts - try again in {retry_after} s")
        self.retry_after = retry_after


#: name -> (attempts allowed, per seconds)
LIMITS: dict[str, tuple[int, int]] = {
    "auth": (60, 60),            # any /auth/* request, per address
    "login": (10, 300),          # per address
    "login_email": (5, 300),     # per account email
    "register": (5, 3600),       # per address
    "reset": (5, 3600),          # per address
    "reset_email": (3, 3600),    # per account email
    "verify_resend": (3, 3600),  # per account
    "oauth": (20, 300),          # Discord login/callback, per address
}


class RateLimiter:
    def __init__(self, limits: dict[str, tuple[int, int]] | None = None) -> None:
        self.limits = dict(LIMITS if limits is None else limits)
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, name: str, key: str) -> None:
        """Count an attempt; raise ``RateLimited`` past the limit."""
        allowed, window = self.limits.get(name, (0, 0))
        if not allowed:
            return
        now = time.monotonic()
        with self._lock:
            hits = self._hits[(name, key)]
            while hits and hits[0] <= now - window:
                hits.popleft()
            if len(hits) >= allowed:
                raise RateLimited(max(1, int(hits[0] + window - now)))
            hits.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
