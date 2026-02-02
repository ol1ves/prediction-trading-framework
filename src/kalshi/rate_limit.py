"""Rate limiting utilities for the Kalshi client.

This is intentionally minimal scaffolding to support the async refactor.
We will likely replace/extend this once we implement full retry + global
rate limiting behavior across endpoints.
"""

from __future__ import annotations

import asyncio
import time


class TokenBucketRateLimiter:
    """A simple token-bucket rate limiter.

    Spec:
    - rate = KALSHI_RATE_LIMIT
    - capacity = rate
    - token_count
    - last_checked_time
    - acquire():
        - add (now - last_checked_time) * rate
        - clamp to capacity
        - if tokens >= 1: consume token and return
        - else: sleep until 1 token would exist
    """

    def __init__(self, rate: int):
        if rate <= 0:
            raise ValueError(f"rate must be > 0. Got: {rate}")

        self.rate: float = float(rate)
        self.capacity: float = float(rate)
        self.token_count: float = float(rate)
        self.last_checked_time: float = time.monotonic()

    async def acquire(self) -> None:
        """Wait until at least one token is available, then consume it."""
        while True:
            now = time.monotonic()
            elapsed = now - self.last_checked_time
            self.last_checked_time = now

            self.token_count = min(self.capacity, self.token_count + elapsed * self.rate)

            if self.token_count >= 1.0:
                self.token_count -= 1.0
                return

            # How long until token_count reaches 1.0?
            deficit = 1.0 - self.token_count
            sleep_seconds = max(0.0, deficit / self.rate)
            await asyncio.sleep(sleep_seconds)