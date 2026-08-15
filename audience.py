from __future__ import annotations

import asyncio
from typing import Any

from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
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
        self._error_muted_chat_ids = load_chat_ids(
            settings.telegram_error_mutes_path
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

    @property
    def error_recipients(self) -> tuple[int, ...]:
        return tuple(
            chat_id
            for chat_id in self.recipients
            if chat_id not in self._error_muted_chat_ids
        )

    @property
    def has_error_recipients(self) -> bool:
        return bool(self.error_recipients)

    def error_notifications_enabled(self, chat_id: int) -> bool:
        return chat_id not in self._error_muted_chat_ids

    def set_error_notifications(self, chat_id: int, *, enabled: bool) -> bool:
        was_enabled = self.error_notifications_enabled(chat_id)
        if enabled:
            self._error_muted_chat_ids.discard(chat_id)
        else:
            self._error_muted_chat_ids.add(chat_id)
        save_chat_ids(
            self.settings.telegram_error_mutes_path,
            self._error_muted_chat_ids,
        )
        return was_enabled != enabled

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
        self._error_muted_chat_ids.discard(chat_id)
        save_chat_ids(self.settings.telegram_chats_path, self._chat_ids)
        save_chat_ids(
            self.settings.telegram_error_mutes_path,
            self._error_muted_chat_ids,
        )
        if not self._chat_ids:
            self._available.clear()

    async def wait_until_available(self) -> None:
        await self._available.wait()

    async def send(self, bot: Bot, text: str, **kwargs: Any) -> int:
        return await self._send_to(bot, self.recipients, text, **kwargs)

    async def send_error(self, bot: Bot, text: str, **kwargs: Any) -> int:
        return await self._send_to(bot, self.error_recipients, text, **kwargs)

    async def _send_to(
        self,
        bot: Bot,
        recipients: tuple[int, ...],
        text: str,
        **kwargs: Any,
    ) -> int:
        delivered = 0
        for chat_id in recipients:
            try:
                await bot.send_message(chat_id, text, **kwargs)
                delivered += 1
            except TelegramForbiddenError:
                self.unregister(chat_id)
                if self.log is not None:
                    self.log.warning("Telegram-пользователь %s заблокировал бота", chat_id)
            except TelegramRetryAfter as exc:
                if self.log is not None:
                    self.log.warning(
                        "Telegram ограничил отправку в чат %s; повтор возможен через %s сек.",
                        chat_id,
                        exc.retry_after,
                    )
            except TelegramNetworkError as exc:
                if self.log is not None:
                    self.log.warning(
                        "Не удалось отправить сообщение в Telegram через сеть/прокси: %s",
                        exc,
                    )
        return delivered

    async def send_photo(self, bot: Bot, path: str, caption: str) -> int:
        return await self._send_photo_to(bot, self.recipients, path, caption)

    async def send_error_photo(self, bot: Bot, path: str, caption: str) -> int:
        return await self._send_photo_to(
            bot,
            self.error_recipients,
            path,
            caption,
        )

    async def _send_photo_to(
        self,
        bot: Bot,
        recipients: tuple[int, ...],
        path: str,
        caption: str,
    ) -> int:
        safe_caption = caption if len(caption) <= 1024 else caption[:1021] + "..."
        delivered = 0
        for chat_id in recipients:
            try:
                await bot.send_photo(
                    chat_id,
                    FSInputFile(path),
                    caption=safe_caption,
                )
                delivered += 1
            except TelegramForbiddenError:
                self.unregister(chat_id)
                if self.log is not None:
                    self.log.warning("Telegram-пользователь %s заблокировал бота", chat_id)
            except TelegramRetryAfter as exc:
                if self.log is not None:
                    self.log.warning(
                        "Telegram ограничил отправку фото в чат %s; повтор через %s сек.",
                        chat_id,
                        exc.retry_after,
                    )
            except TelegramNetworkError as exc:
                if self.log is not None:
                    self.log.warning(
                        "Не удалось отправить фото в Telegram через сеть/прокси: %s",
                        exc,
                    )
        return delivered
