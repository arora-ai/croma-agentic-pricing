"""Keeps the harness workers inside the model's token-per-minute quota and limits how many deep-reasoning calls run at once.

Every model turn re-sends its whole input, and the quota is shared by every agent on the deployment, so a burst of large calls is what produces
rate-limit failures. The governor holds a call back until the last minute's usage (actual tokens of finished calls plus the estimate of calls in
flight) leaves room for it. It is a guard rail for the harness's own workers; other agents on the same deployment are not counted."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from contextlib import asynccontextmanager

WINDOW_S = 60.0


class Governor:
    def __init__(self, tpm_budget: int, parallelism: int, clock=time.monotonic, sleep=asyncio.sleep):
        self.budget = max(1, tpm_budget)
        self._sem = asyncio.Semaphore(max(1, parallelism))
        self._window: deque[list] = deque()  # [timestamp, tokens] per call; a list so the actual usage can replace the estimate
        self._clock, self._sleep = clock, sleep
        self.waited_s = 0.0

    def _purge(self, now: float) -> None:
        while self._window and now - self._window[0][0] >= WINDOW_S:
            self._window.popleft()

    def used(self) -> int:
        self._purge(self._clock())
        return int(sum(tokens for _t, tokens in self._window))

    @asynccontextmanager
    async def slot(self, estimate: int):
        """Waits for a free slot and for room in the minute's budget; the caller sets `entry[1]` to the actual tokens when it knows them."""
        await self._sem.acquire()
        try:
            while True:
                now = self._clock()
                self._purge(now)
                used = sum(tokens for _t, tokens in self._window)
                if not self._window or used + estimate <= self.budget:  # a call larger than the whole budget still runs, alone
                    entry = [now, float(estimate)]
                    self._window.append(entry)
                    break
                wait = min(WINDOW_S, max(0.5, WINDOW_S - (now - self._window[0][0]) + 0.1))
                self.waited_s += wait
                await self._sleep(wait)
            yield entry
        finally:
            self._sem.release()
