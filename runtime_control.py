from __future__ import annotations

import asyncio
import math
import time


class ParserPauseControl:
    """Координирует безопасную паузу парсера и команду /resume."""

    def __init__(self):
        self._paused = False
        self.reason = ""
        self._pause_until_timestamp: float | None = None
        self._resume_event = asyncio.Event()

    @property
    def paused(self) -> bool:
        if (
            self._paused
            and self._pause_until_timestamp is not None
            and self._pause_until_timestamp <= time.time()
        ):
            self._clear()
        return self._paused

    @property
    def remaining_seconds(self) -> int:
        if not self.paused or self._pause_until_timestamp is None:
            return 0
        return max(0, math.ceil(self._pause_until_timestamp - time.time()))

    def pause(self, reason: str) -> None:
        self.reason = reason
        self._paused = True
        self._pause_until_timestamp = None
        self._resume_event.clear()

    def pause_until(self, reason: str, until_timestamp: float) -> None:
        self.reason = reason
        self._paused = True
        self._pause_until_timestamp = until_timestamp
        self._resume_event.clear()

    def _clear(self) -> None:
        self._paused = False
        self.reason = ""
        self._pause_until_timestamp = None

    def resume(self) -> bool:
        if not self.paused:
            return False
        if self.remaining_seconds > 0:
            return False
        self._clear()
        self._resume_event.set()
        return True

    async def wait_for_resume(self) -> None:
        while self.paused:
            remaining = self.remaining_seconds
            if remaining <= 0 and self._pause_until_timestamp is not None:
                self._clear()
                return
            if self._pause_until_timestamp is None:
                await self._resume_event.wait()
                self._resume_event.clear()
                return
            timeout = max(0.0, self._pause_until_timestamp - time.time())
            try:
                await asyncio.wait_for(
                    self._resume_event.wait(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                self._clear()
                return
            finally:
                self._resume_event.clear()
