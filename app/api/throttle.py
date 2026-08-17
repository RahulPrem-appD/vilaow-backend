"""A small in-process rate limiter for the two public endpoints that write.

Deliberately in memory and deliberately modest. It exists because the login
endpoint had no limit of any kind — an internet-facing form where an attacker
could run unlimited parallel password guesses, and where each guess costs a
bcrypt hash, so the same requests are also a cheap way to exhaust the CPU of a
single starter instance.

In memory means per process. On this deployment that is one instance, so it is
genuinely effective; if the service is ever scaled out it becomes best-effort
and wants Redis or a table. That is a real limitation and it is written here
rather than discovered later.

It is a speed bump, not an authorisation boundary. The rules that actually
decide who may do what live in app/security.py and the services.
"""
from __future__ import annotations

import threading
import time
from collections import deque

# Keep the table from growing without bound if someone rotates keys at us.
_MAX_KEYS = 20_000


class SlidingWindow:
    """How many times `key` has been seen inside `window` seconds."""

    def __init__(self, *, limit: int, window: float) -> None:
        self._limit = limit
        self._window = window
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, hits: deque[float], now: float) -> None:
        while hits and hits[0] <= now - self._window:
            hits.popleft()

    def check(self, key: str | None) -> bool:
        """True if this hit is allowed. A None key is always allowed — an
        unknown caller must not share a bucket with every other unknown
        caller, which is precisely the mistake that made the introduction
        form's per-IP cap into a site-wide one."""
        if key is None:
            return True

        now = time.monotonic()
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                if len(self._hits) >= _MAX_KEYS:
                    self._evict(now)
                hits = self._hits[key] = deque()
            self._prune(hits, now)
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            return True

    def clear(self, key: str | None) -> None:
        """Forget a key — called after a successful login, so one mistyped
        password does not count against someone who then gets it right."""
        if key is None:
            return
        with self._lock:
            self._hits.pop(key, None)

    def _evict(self, now: float) -> None:
        """Drop keys whose window has fully expired; if that frees nothing,
        drop everything rather than grow. Losing counters fails open, which is
        the right direction for a speed bump."""
        stale = [k for k, hits in self._hits.items()
                 if not hits or hits[-1] <= now - self._window]
        for key in stale:
            del self._hits[key]
        if not stale:
            self._hits.clear()


# Five failures a minute per address, and per account, is generous for someone
# typing and hopeless for someone guessing.
login_attempts = SlidingWindow(limit=5, window=60.0)

# The public callback form. The introduction form already had a cap; this one
# had none, so the caller worklist could be flooded by anyone with a script.
lead_submissions = SlidingWindow(limit=5, window=60.0 * 60.0)
