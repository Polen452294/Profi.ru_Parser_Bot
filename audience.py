from __future__ import annotations

import asyncio
from typing import Any

from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import FSInputFile

from config import Settings
from storage import load_chat_ids, save_chat_ids


class TelegramAudience:
    """Получатели и правила доступа к Telegram-боту."""

    def __init__(self, settings: Settings, log=None):
        self.settings = settings
        self.log = log
        self.open_mode = settings.admin_chat_id is None
        self._chat_ids = (
            load_chat_ids(settings.telegram_chats_path)
            if self.open_mode
            else {settings.admin_chat_id}
        )
        self._available = asyncio.Event()
        if self._chat_ids:
            self._available.set()

    @property
    def recipients(self) -> tuple[int, ...]:
        return tuple(sorted(self._chat_ids))

    @property
    def has_recipients(self) -> bool:
        return bool(self._chat_ids)

    def is_allowed(self, chat_id: int, chat_type: ChatType | str) -> bool:
        if not self.open_mode:
            return chat_id == self.settings.admin_chat_id
        return getattr(chat_type, "value", chat_type) == ChatType.PRIVATE.value

    def register(self, chat_id: int) -> bool:
        if not self.open_mode or chat_id in self._chat_ids:
            return False
        self._chat_ids.add(chat_id)
        save_chat_ids(self.settings.telegram_chats_path, self._chat_ids)
        self._available.set()
        return True

    def unregister(self, chat_id: int) -> None:
        if not self.open_mode or chat_id not in self._chat_ids:
            return
        self._chat_ids.remove(chat_id)
        save_chat_ids(self.settings.telegram_chats_path, self._chat_ids)
        if not self._chat_ids:
            self._available.clear()

    async def wait_until_available(self) -> None:
        await self._available.wait()

    async def send(self, bot: Bot, text: str, **kwargs: Any) -> int:
        delivered = 0
        for chat_id in self.recipients:
            try:
                await bot.send_message(chat_id, text, **kwargs)
                delivered += 1
            except TelegramForbiddenError:
                self.unregister(chat_id)
                if self.log is not None:
                    self.log.warning("Telegram-пользователь %s заблокировал бота", chat_id)
        return delivered

    async def send_photo(self, bot: Bot, path: str, caption: str) -> int:
        delivered = 0
        for chat_id in self.recipients:
            try:
                await bot.send_photo(
                    chat_id,
                    FSInputFile(path),
                    caption=caption,
                )
                delivered += 1
            except TelegramForbiddenError:
                self.unregister(chat_id)
                if self.log is not None:
                    self.log.warning("Telegram-пользователь %s заблокировал бота", chat_id)
        return delivered
