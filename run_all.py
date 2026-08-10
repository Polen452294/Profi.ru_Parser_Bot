from __future__ import annotations

import asyncio
from asyncio.subprocess import Process
import os
from pathlib import Path
import sys
import time
from typing import Any

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter

from audience import TelegramAudience
from config import ConfigurationError, Settings
from health import ACCESS_CHALLENGE_EXIT_CODE, SESSION_EXPIRED_EXIT_CODE
from instance_lock import AlreadyRunningError, SingleInstanceLock
from lifecycle import notify_service_started, notify_service_stopped
from logger_setup import setup_logger
from maintenance import maintenance_loop
from runtime_control import ParserPauseControl
from session_recovery import SessionRecoveryManager
from site_cooldown import (
    clear_site_cooldown,
    format_remaining_time,
    load_site_cooldown,
)
from storage import (
    compact_jsonl_if_consumed,
    load_cursor,
    read_jsonl_batch,
    save_cursor,
)
from telegram_control import (
    notify_admin,
    system_event_notifier,
    telegram_command_polling,
)
from telegram_transport import create_telegram_session
from tg_formatter import format_order
from watchdog import heartbeat_watchdog


CURRENT_PARSER_PROCESS: Process | None = None
PARSER_RESTART_REQUESTED = False


def read_order_batch(
    path: Path,
    offset: int,
) -> tuple[list[tuple[dict[str, Any] | None, int]], int]:
    """Совместимое имя для чтения очереди заказов."""
    return read_jsonl_batch(path, offset)


def request_parser_restart() -> None:
    global PARSER_RESTART_REQUESTED
    process = CURRENT_PARSER_PROCESS
    if process is not None and process.returncode is None:
        PARSER_RESTART_REQUESTED = True
        process.terminate()


def parser_pid() -> int | None:
    process = CURRENT_PARSER_PROCESS
    if process is None or process.returncode is not None:
        return None
    return process.pid


async def start_parser_process(settings: Settings, log) -> Process:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"

    for key in (
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "PROXYCHAINS_CONF_FILE",
        "PROXYCHAINS_QUIET_MODE",
        "PROXYRESOLV_DNS",
    ):
        environment.pop(key, None)

    parser_script = settings.project_dir / "main.py"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(parser_script),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(settings.project_dir),
        env=environment,
    )

    global CURRENT_PARSER_PROCESS
    CURRENT_PARSER_PROCESS = process
    log.info("Парсер запущен, PID=%s", process.pid)
    return process


async def pipe_process_output(process: Process, log) -> None:
    if process.stdout is None:
        return

    while line := await process.stdout.readline():
        log.info("[ПАРСЕР] %s", line.decode("utf-8", errors="replace").rstrip())


async def wait_for_active_site_cooldown(
    settings: Settings,
    log,
    bot: Bot,
    audience: TelegramAudience,
    control: ParserPauseControl,
) -> bool:
    """Waits out the persisted Profi.ru limit while Telegram stays available."""
    cooldown = load_site_cooldown(settings.site_cooldown_path)
    if cooldown is None:
        return False

    remaining = cooldown.remaining_seconds()
    control.pause_until(cooldown.reason, cooldown.until_timestamp)
    resume_at = time.strftime(
        "%d.%m.%Y %H:%M:%S",
        time.localtime(cooldown.until_timestamp),
    )
    log.warning(
        "Profi.ru ограничил повторный вход; пауза ещё %s, до %s",
        format_remaining_time(remaining),
        resume_at,
    )

    caption = (
        "⏸ Profi.ru сообщил: «Слишком много попыток. Можно будет повторить "
        "через 12 часов».\n\n"
        "Парсер полностью прекратил обращения к сайту. "
        f"Осталось: {format_remaining_time(remaining)}.\n"
        f"Автоматическое возобновление: {resume_at}.\n\n"
        "Команда /resume не снимает это ограничение раньше срока."
    )
    screenshots = list(settings.debug_dir.glob("login_retry_cooldown_*.png"))
    screenshot = (
        max(screenshots, key=lambda path: path.stat().st_mtime)
        if screenshots
        else None
    )
    if screenshot is not None:
        await audience.send_photo(bot, str(screenshot), caption)
    else:
        await audience.send(bot, caption)

    await control.wait_for_resume()
    clear_site_cooldown(settings.site_cooldown_path)
    await audience.send(
        bot,
        "▶️ 12-часовая пауза завершена. Автоматически возобновляю работу парсера.",
    )
    log.info("12-часовая пауза Profi.ru завершена; запускаю парсер")
    return True


async def send_order_message(
    bot: Bot,
    settings: Settings,
    log,
    text: str,
    audience: TelegramAudience,
) -> bool:
    while True:
        try:
            delivered = await audience.send(
                bot,
                text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            if delivered == 0:
                return False
            await asyncio.sleep(1)
            return True
        except TelegramRetryAfter as exc:
            log.warning("Лимит Telegram; повтор через %s сек.", exc.retry_after)
            await asyncio.sleep(exc.retry_after + 2)


async def order_notifier(
    settings: Settings,
    bot: Bot,
    log,
    audience: TelegramAudience | None = None,
) -> None:
    audience = audience or TelegramAudience(settings, log)
    offset = load_cursor(settings.bot_cursor_path)

    if offset == 0 and settings.orders_path.exists() and audience.has_recipients:
        offset = settings.orders_path.stat().st_size
        save_cursor(settings.bot_cursor_path, offset)
        log.info("Существующие заявки пропущены; ожидаю новые")

    log.info("Отправка заявок в Telegram запущена")
    while True:
        try:
            if not audience.has_recipients:
                log.info("Ожидаю первого получателя Telegram")
                await audience.wait_until_available()
            records, normalized_offset = read_jsonl_batch(settings.orders_path, offset)
            if normalized_offset != offset:
                offset = normalized_offset
                save_cursor(settings.bot_cursor_path, offset)

            for order, next_offset in records:
                if order is None:
                    log.warning("Пропущена повреждённая строка в файле заявок")
                else:
                    delivered = await send_order_message(
                        bot,
                        settings,
                        log,
                        format_order(order),
                        audience,
                    )
                    if not delivered:
                        log.warning("Заявка ожидает первого получателя Telegram")
                        break
                    log.info("Заявка отправлена: %s", order.get("order_id", "без ID"))

                offset = next_offset
                save_cursor(settings.bot_cursor_path, offset)

            offset = compact_jsonl_if_consumed(
                settings.orders_path,
                settings.bot_cursor_path,
                offset,
                settings.queue_compact_bytes,
            )

            await asyncio.sleep(settings.bot_poll_sec)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка Telegram; повторяю через %s сек.", settings.bot_poll_sec)
            await asyncio.sleep(settings.bot_poll_sec)


async def supervise_parser(
    settings: Settings,
    log,
    bot: Bot,
    recovery: SessionRecoveryManager,
    audience: TelegramAudience,
    control: ParserPauseControl,
) -> None:
    global CURRENT_PARSER_PROCESS, PARSER_RESTART_REQUESTED
    restart_count = 0
    crash_alert_sent = False

    try:
        while True:
            await wait_for_active_site_cooldown(
                settings,
                log,
                bot,
                audience,
                control,
            )
            parser_started_at = time.time()
            process = await start_parser_process(settings, log)
            output_task = asyncio.create_task(pipe_process_output(process, log))
            try:
                return_code = await process.wait()
            finally:
                await output_task

            if PARSER_RESTART_REQUESTED:
                PARSER_RESTART_REQUESTED = False
                restart_count = 0
                log.info("Перезапуск после обновления сессии")
                continue

            if return_code == SESSION_EXPIRED_EXIT_CODE:
                restart_count = 0
                log.warning("Парсер остановлен из-за завершения сессии Profi.ru")
                await recovery.start(
                    "Сайт завершил сессию или запросил повторный вход",
                    bypass_cooldown=True,
                )
                await recovery.wait_until_ready()
                log.info("Новая сессия готова; запускаю парсер")
                continue

            if return_code == ACCESS_CHALLENGE_EXIT_CODE:
                restart_count = 0
                if load_site_cooldown(settings.site_cooldown_path) is not None:
                    log.info("Получен обязательный лимит Profi.ru; включаю таймер паузы")
                    continue
                reason = "Profi.ru показал CAPTCHA или страницу ограничения доступа"
                control.pause(reason)
                log.error("Парсер поставлен на безопасную паузу: %s", reason)
                if not audience.has_recipients:
                    await audience.wait_until_available()
                screenshots = list(
                    path
                    for path in settings.debug_dir.glob("access_challenge_*.png")
                    if path.stat().st_mtime >= parser_started_at - 1
                )
                screenshot = (
                    max(screenshots, key=lambda path: path.stat().st_mtime)
                    if screenshots
                    else None
                )
                caption = (
                    "🛑 Profi.ru показал CAPTCHA или ограничил доступ.\n\n"
                    "Все запросы остановлены. Проверьте изображение и отправьте "
                    "/resume, когда можно безопасно повторить проверку."
                )
                if screenshot is not None:
                    await audience.send_photo(bot, str(screenshot), caption)
                else:
                    await audience.send(bot, caption)
                await control.wait_for_resume()
                await audience.send(
                    bot,
                    "▶️ Получена команда /resume. Возобновляю проверку Profi.ru.",
                )
                log.info("Безопасная пауза снята пользователем")
                continue

            if return_code == 0:
                log.info("Парсер завершился без ошибки")
                return

            restart_count += 1
            log.error("Парсер завершился с кодом %s", return_code)

            if restart_count >= settings.site_error_threshold and not crash_alert_sent:
                crash_alert_sent = True
                await notify_admin(
                    bot,
                    settings,
                    "🚨 Парсер несколько раз завершился с ошибкой. "
                    "Автоматические перезапуски продолжаются. Проверьте logs/run_all.error.log.",
                    audience,
                )

            if restart_count > settings.max_restarts:
                await notify_admin(
                    bot,
                    settings,
                    "❌ Парсер остановлен: достигнут лимит автоматических перезапусков.",
                    audience,
                )
                return

            log.info(
                "Перезапуск через %s сек. (%s/%s)",
                min(
                    settings.restart_delay_sec * (2 ** min(restart_count - 1, 8)),
                    settings.error_backoff_max_sec,
                ),
                restart_count,
                settings.max_restarts,
            )
            restart_delay = min(
                settings.restart_delay_sec * (2 ** min(restart_count - 1, 8)),
                settings.error_backoff_max_sec,
            )
            await asyncio.sleep(restart_delay)
    finally:
        CURRENT_PARSER_PROCESS = None


def _create_bot(settings: Settings) -> Bot:
    session = create_telegram_session(settings)
    return Bot(token=settings.bot_token, session=session)


async def _run_service(settings: Settings) -> None:
    settings.ensure_directories()
    run_log = setup_logger("run_all", settings.log_dir)
    bot_log = setup_logger("bot", settings.log_dir)
    bot = _create_bot(settings)
    audience = TelegramAudience(settings, bot_log)
    control = ParserPauseControl()
    recovery = SessionRecoveryManager(
        settings,
        bot,
        bot_log,
        on_success=request_parser_restart,
        audience=audience,
    )

    lifecycle_task = asyncio.create_task(
        notify_service_started(settings, bot, audience)
    )
    tasks = [
        asyncio.create_task(
            supervise_parser(settings, run_log, bot, recovery, audience, control)
        ),
        asyncio.create_task(order_notifier(settings, bot, bot_log, audience)),
        asyncio.create_task(system_event_notifier(settings, bot, bot_log, audience)),
        asyncio.create_task(
            telegram_command_polling(
                settings,
                bot,
                recovery,
                bot_log,
                audience,
                control,
            )
        ),
        asyncio.create_task(
            heartbeat_watchdog(
                settings,
                bot,
                audience,
                recovery,
                control,
                bot_log,
                parser_pid,
            )
        ),
        asyncio.create_task(maintenance_loop(settings, run_log)),
    ]
    run_log.info("Парсер, уведомления и Telegram-команды запущены")

    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        lifecycle_task.cancel()

        await recovery.stop()

        global CURRENT_PARSER_PROCESS
        process = CURRENT_PARSER_PROCESS
        if process is not None and process.returncode is None:
            run_log.info("Останавливаю процесс парсера")
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(lifecycle_task, return_exceptions=True)
        try:
            await notify_service_stopped(bot, audience)
        except Exception:
            bot_log.exception("Не удалось отправить уведомление об остановке")
        await bot.session.close()
        run_log.info("Работа завершена")


async def run(settings: Settings) -> None:
    settings.ensure_directories()
    with SingleInstanceLock(settings.instance_lock_path):
        await _run_service(settings)


async def run_telegram_only(settings: Settings) -> None:
    settings.ensure_directories()
    with SingleInstanceLock(settings.instance_lock_path):
        log = setup_logger("bot", settings.log_dir)
        bot = _create_bot(settings)
        audience = TelegramAudience(settings, log)
        recovery = SessionRecoveryManager(settings, bot, log, audience=audience)
        tasks = [
            asyncio.create_task(order_notifier(settings, bot, log, audience)),
            asyncio.create_task(system_event_notifier(settings, bot, log, audience)),
            asyncio.create_task(
                telegram_command_polling(settings, bot, recovery, log, audience)
            ),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await recovery.stop()
            await asyncio.gather(*tasks, return_exceptions=True)
            await bot.session.close()


def main() -> int:
    try:
        settings = Settings.load()
        errors = settings.validation_errors(require_telegram=True)
        if errors:
            for error in errors:
                print(f"ОШИБКА: {error}")
            return 2
        asyncio.run(run(settings))
        return 0
    except ConfigurationError as exc:
        print(f"ОШИБКА НАСТРОЕК: {exc}")
        return 2
    except AlreadyRunningError as exc:
        print(f"ОШИБКА: {exc}")
        return 3
    except KeyboardInterrupt:
        print("\nРабота остановлена пользователем.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
