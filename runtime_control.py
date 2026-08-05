from __future__ import annotations

import asyncio


class ParserPauseControl:
    """Координирует безопасную паузу парсера и команду /resume."""

    def __init__(self):
        self.paused = False
        self.reason = ""
        self._resume_event = asyncio.Event()

    def pause(self, reason: str) -> None:
        self.reason = reason
        self.paused = True
        self._resume_event.clear()

    def resume(self) -> bool:
        if not self.paused:
            return False
        self.paused = False
        self._resume_event.set()
        return True

    async def wait_for_resume(self) -> None:
        await self._resume_event.wait()
        self._resume_event.clear()
