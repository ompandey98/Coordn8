"""A per-client token bucket: each client earns requests at a fixed rate up to a burst cap."""
from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, per_minute: int = 60, burst: int = 10):
        self.rate = per_minute / 60.0
        self.burst = burst
        self._buckets = {}
        self._lock = threading.Lock()

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(client, (float(self.burst), now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens < 1.0:
                self._buckets[client] = (tokens, now)
                return False
            self._buckets[client] = (tokens - 1.0, now)
            return True
