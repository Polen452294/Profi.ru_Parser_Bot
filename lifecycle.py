from __future__ import annotations

from aiogram import Bot

from audience import TelegramAudience
from config import Settings
from storage import read_json_object, write_json_atomic
from version import APP_VERSION


async def notify_service_started(
    settings: Settings,
    bot: Bot,
    audience: TelegramAudience,
) -> None:
    previous = read_json_object(settings.version_state_path).get("version")
    if not audience.has_recipients:
        await audience.wait_until_available()

    if previous and previous != APP_VERSION:
        text = (
            f"🆕 Сервис обновлён: {previous} → {APP_VERSION}.\n"
            "Парсер и Telegram-бот успешно запущены."
        )
    else:
        text = f"🟢 Сервис запущен. Версия: {APP_VERSION}."

    delivered = await audience.send(bot, text)
    if delivered:
        write_json_atomic(
            settings.version_state_path,
            {"version": APP_VERSION},
        )


async def notify_service_stopped(
    bot: Bot,
    audience: TelegramAudience,
) -> None:
    if audience.has_recipients:
        await audience.send(bot, "🔴 Сервис парсера остановлен.")
