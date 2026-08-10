from __future__ import annotations

import asyncio
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import BotCommand, Message

from audience import TelegramAudience
from config import Settings
from health import EVENT_SITE_ERROR, EVENT_SITE_RECOVERED
from health_report import build_health_report
from runtime_control import ParserPauseControl
from session_recovery import SessionRecoveryManager, normalize_sms_code
from site_cooldown import format_remaining_time
from storage import (
    compact_jsonl_if_consumed,
    load_cursor,
    read_jsonl_batch,
    save_cursor,
)
from version import APP_VERSION


async def notify_admin(
    bot: Bot,
    settings: Settings,
    text: str,
    audience: TelegramAudience | None = None,
) -> int:
    audience = audience or TelegramAudience(settings)
    return await audience.send(bot, text)


def _accept_message(message: Message, audience: TelegramAudience) -> bool:
    if not audience.is_allowed(message.chat.id, message.chat.type):
        return False
    audience.register(message.chat.id)
    return True


def build_dispatcher(
    settings: Settings,
    recovery: SessionRecoveryManager,
    audience: TelegramAudience | None = None,
    control: ParserPauseControl | None = None,
) -> Dispatcher:
    audience = audience or TelegramAudience(settings)
    control = control or ParserPauseControl()
    dispatcher = Dispatcher()
    router = Router(name="telegram_control")

    @router.message(Command("start", "help"))
    async def help_command(message: Message) -> None:
        if not _accept_message(message, audience):
            return
        access_text = (
            "Доступ: открытый режим, личные чаты.\n"
            if audience.open_mode
            else "Доступ: только администратор.\n"
        )
        await message.answer(
            "Бот контролирует парсер Profi.ru.\n\n"
            f"{access_text}\n"
            "/status — состояние сессии\n"
            "/health — полная диагностика сервиса\n"
            "/version — версия проекта\n"
            "/renew — перевыпустить cookies через SMS\n"
            "/resume — продолжить после CAPTCHA/блокировки\n"
            "/cancel — отменить восстановление сессии\n\n"
            "После ввода телефона бот ждёт точный вход по сим-пушу или СМС "
            "и не нажимает промежуточные состояния «Продолжить» или МТС ID. "
            "Когда бот запросит код, отправьте сообщением ровно 4 цифры."
        )

    @router.message(Command("status"))
    async def status_command(message: Message) -> None:
        if not _accept_message(message, audience):
            return

        if recovery.awaiting_code:
            recovery_status = "ожидается SMS-код"
        elif recovery.in_progress:
            recovery_status = "обновление выполняется"
        else:
            recovery_status = "не выполняется"

        session_status = (
            "файл cookies существует"
            if settings.auth_state_path.exists()
            else "файл cookies отсутствует"
        )
        hard_pause_remaining = max(
            control.remaining_seconds,
            recovery.site_cooldown_remaining_seconds,
        )
        pause_status = (
            f"обязательная, осталось {format_remaining_time(hard_pause_remaining)}"
            if hard_pause_remaining
            else ("да" if control.paused else "нет")
        )
        await message.answer(
            "ℹ️ Состояние парсера\n\n"
            f"Сессия: {session_status}\n"
            f"Восстановление: {recovery_status}\n"
            f"Безопасная пауза: {pause_status}"
        )

    @router.message(Command("renew"))
    async def renew_command(message: Message) -> None:
        if not _accept_message(message, audience):
            return
        started = await recovery.start("Запрошено пользователем через Telegram")
        if started:
            await message.answer("Запускаю перевыпуск cookies Profi.ru…")

    @router.message(Command("health"))
    async def health_command(message: Message) -> None:
        if not _accept_message(message, audience):
            return
        report = await asyncio.to_thread(
            build_health_report,
            settings,
            recovery,
            control,
        )
        await message.answer(report)

    @router.message(Command("version"))
    async def version_command(message: Message) -> None:
        if not _accept_message(message, audience):
            return
        await message.answer(f"Версия парсера: {APP_VERSION}")

    @router.message(Command("cancel"))
    async def cancel_command(message: Message) -> None:
        if not _accept_message(message, audience):
            return
        hard_pause_remaining = max(
            control.remaining_seconds,
            recovery.site_cooldown_remaining_seconds,
        )
        if hard_pause_remaining:
            await message.answer(
                "Обязательную паузу Profi.ru нельзя отменить раньше срока. "
                f"Осталось: {format_remaining_time(hard_pause_remaining)}."
            )
            return
        if await recovery.cancel():
            await message.answer("Восстановление сессии отменено.")
        else:
            await message.answer("Сейчас восстановление сессии не выполняется.")

    @router.message(Command("resume"))
    async def resume_command(message: Message) -> None:
        if not _accept_message(message, audience):
            return
        hard_pause_remaining = max(
            control.remaining_seconds,
            recovery.site_cooldown_remaining_seconds,
        )
        if hard_pause_remaining:
            await message.answer(
                "Обязательную паузу Profi.ru нельзя снять раньше срока. "
                f"Осталось: {format_remaining_time(hard_pause_remaining)}."
            )
            return
        if control.resume():
            await message.answer("Возобновляю безопасную проверку Profi.ru…")
        else:
            await message.answer("Парсер сейчас не находится на безопасной паузе.")

    @router.message(F.text)
    async def sms_code_message(message: Message) -> None:
        if not _accept_message(message, audience):
            return
        text = (message.text or "").strip()
        if recovery.awaiting_code or (
            recovery.in_progress and normalize_sms_code(text) is not None
        ):
            _, response = await recovery.submit_code(text)
            await message.answer(response)
        elif normalize_sms_code(text) is not None:
            await message.answer(
                "Сейчас код не запрашивался. Для обновления сессии используйте /renew."
            )

    dispatcher.include_router(router)
    return dispatcher


async def telegram_command_polling(
    settings: Settings,
    bot: Bot,
    recovery: SessionRecoveryManager,
    log,
    audience: TelegramAudience | None = None,
    control: ParserPauseControl | None = None,
) -> None:
    audience = audience or TelegramAudience(settings, log)
    if audience.open_mode:
        log.warning(
            "ADMIN_CHAT_ID не задан: бот доступен любому пользователю в личном чате"
        )
    else:
        log.info("Telegram-команды доступны только ADMIN_CHAT_ID")

    retry_delay = 5
    while True:
        dispatcher = build_dispatcher(settings, recovery, audience, control)
        try:
            await bot.set_my_commands(
                [
                    BotCommand(command="status", description="состояние парсера и сессии"),
                    BotCommand(command="health", description="полная диагностика сервиса"),
                    BotCommand(command="version", description="версия проекта"),
                    BotCommand(command="renew", description="перевыпустить cookies через SMS"),
                    BotCommand(command="cancel", description="отменить восстановление сессии"),
                    BotCommand(command="resume", description="продолжить после блокировки"),
                    BotCommand(command="help", description="справка по командам"),
                ]
            )
            log.info("Приём команд Telegram запущен")
            retry_delay = 5
            await dispatcher.start_polling(
                bot,
                handle_signals=False,
                close_bot_session=False,
                allowed_updates=dispatcher.resolve_used_update_types(),
            )
            log.warning("Telegram polling завершился; запускаю его повторно")
        except asyncio.CancelledError:
            raise
        except TelegramRetryAfter as exc:
            retry_delay = max(retry_delay, int(exc.retry_after) + 2)
            log.warning(
                "Telegram временно ограничил запросы; повтор через %s сек.",
                retry_delay,
            )
        except TelegramNetworkError as exc:
            log.warning(
                "Telegram недоступен через сеть/прокси: %s. Повтор через %s сек.",
                exc,
                retry_delay,
            )
        except Exception:
            log.exception(
                "Неожиданная ошибка Telegram polling; повтор через %s сек.",
                retry_delay,
            )

        await asyncio.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 60)


def _format_system_event(event: dict[str, Any]) -> str | None:
    event_type = event.get("type")
    message = str(event.get("message") or "Без описания")
    details = event.get("details") if isinstance(event.get("details"), dict) else {}

    if event_type == EVENT_SITE_ERROR:
        count = details.get("consecutive_errors")
        count_text = f"\nОшибок подряд: {count}" if count else ""
        return (
            "🚨 Profi.ru перестал корректно выводить заказы.\n"
            f"Причина: {message}{count_text}\n\n"
            "Бот продолжает попытки и сообщит о восстановлении."
        )
    if event_type == EVENT_SITE_RECOVERED:
        return f"✅ Работа Profi.ru восстановлена.\n{message}"
    return None


async def system_event_notifier(
    settings: Settings,
    bot: Bot,
    log,
    audience: TelegramAudience | None = None,
) -> None:
    audience = audience or TelegramAudience(settings, log)
    offset = load_cursor(settings.system_event_cursor_path)

    while True:
        try:
            records, normalized_offset = read_jsonl_batch(
                settings.system_events_path,
                offset,
            )
            if normalized_offset != offset:
                offset = normalized_offset
                save_cursor(settings.system_event_cursor_path, offset)

            for event, next_offset in records:
                if event is None:
                    log.warning("Пропущена повреждённая строка системных событий")
                else:
                    notification = _format_system_event(event)
                    if notification:
                        delivered = await notify_admin(
                            bot,
                            settings,
                            notification,
                            audience,
                        )
                        if delivered == 0:
                            log.info("Системное уведомление ожидает получателя")
                            await audience.wait_until_available()
                            delivered = await notify_admin(
                                bot,
                                settings,
                                notification,
                                audience,
                            )
                            if delivered == 0:
                                break
                offset = next_offset
                save_cursor(settings.system_event_cursor_path, offset)

            offset = compact_jsonl_if_consumed(
                settings.system_events_path,
                settings.system_event_cursor_path,
                offset,
                settings.queue_compact_bytes,
            )

            await asyncio.sleep(settings.bot_poll_sec)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка отправки системного уведомления")
            await asyncio.sleep(settings.bot_poll_sec)
