"""
utils/antiban.py — Anti-ban layer
Fix #9: Semaphore released BEFORE sleeping on FloodWait
        so other users aren't blocked while we wait
"""
import asyncio
import random
import time
from collections import defaultdict
from typing import Dict

from config import MIN_DELAY, MAX_DELAY, BOT_RATE_LIMIT


class TokenBucket:
    """Token bucket: allows `rate` sends per second per bot."""
    def __init__(self, rate: int):
        self.rate    = rate
        self._tokens = float(rate)
        self._last   = time.monotonic()
        self._lock   = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now     = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(self.rate, self._tokens + elapsed * self.rate)
            self._last   = now
            if self._tokens < 1:
                wait = (1 - self._tokens) / self.rate
                await asyncio.sleep(wait)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0


_buckets: Dict[int, TokenBucket] = defaultdict(lambda: TokenBucket(BOT_RATE_LIMIT))


async def throttle(bot_id: int):
    """Enforce per-bot rate limit + random jitter before each send."""
    await _buckets[bot_id].acquire()
    await asyncio.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


async def handle_flood_wait(seconds: int, bot_id: int, sem: asyncio.Semaphore = None):
    """
    Fix #9: If semaphore is passed, RELEASE it before sleeping.
    This frees the concurrency slot so other users keep sending
    while this bot waits out the FloodWait.
    """
    wait = seconds + 5   # +5s buffer
    print(f"[antiban] FloodWait {seconds}s on bot {bot_id} → sleeping {wait}s")

    if sem:
        sem.release()          # ← release BEFORE sleeping
        await asyncio.sleep(wait)
        await sem.acquire()    # ← re-acquire after sleep
    else:
        await asyncio.sleep(wait)
