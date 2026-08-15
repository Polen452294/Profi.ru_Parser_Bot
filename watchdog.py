from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import shutil
import time
from typing import Callable

from aiogram import Bot

from audience import TelegramAudience
from config import Settings
from heartbeat import parse_utc_timestamp, read_heartbeat
from runtime_control import ParserPauseControl
from session_recovery import SessionRecoveryManager


def _age_seconds(value) -> float | None:
    timestamp = parse_utc_timestamp(value)
    if timestamp is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - timestamp).total_seconds())


async def heartbeat_watchdog(
    settings: Settings,
    bot: Bot,
    audience: TelegramAudience,
    recovery: SessionRecoveryManager,
    control: ParserPauseControl,
    log,
    parser_pid: Callable[[], int | None],
) -> None:
    heartbeat_alert = False
    disk_alert = False
    observed_pid: int | None = None
    observed_pid_at = time.monotonic()

    while True:
        try:
            disk = shutil.disk_usage(settings.project_dir)
            free_mb = disk.free // (1024 * 1024)
            disk_low = free_mb < settings.min_free_disk_mb
            if disk_low and not disk_alert:
                delivered = await audience.send_error(
                    bot,
                    "⚠️ На сервере заканчивается место.\n"
                    f"Свободно: {free_mb} МБ; требуется не менее "
                    f"{settings.min_free_disk_mb} МБ.",
                )
                disk_alert = delivered > 0
            elif not disk_low and disk_alert:
                await audience.send_error(
                    bot,
                    "✅ Свободное место на сервере восстановлено.",
                )
                disk_alert = False

            current_pid = parser_pid()
            if current_pid != observed_pid:
                observed_pid = current_pid
                observed_pid_at = time.monotonic()

            should_check = (
                current_pid is not None
                and not control.paused
                and not recovery.in_progress
            )
            if should_check:
                heartbeat = read_heartbeat(settings.heartbeat_path)
                heartbeat_pid = heartbeat.get("pid")
                if (
                    heartbeat_pid != current_pid
                    and time.monotonic() - observed_pid_at
                    < settings.heartbeat_stale_sec
                ):
                    await asyncio.sleep(settings.watchdog_poll_sec)
                    continue
                alive_age = _age_seconds(heartbeat.get("process_alive_at"))
                heartbeat_stale = (
                    alive_age is None or alive_age > settings.heartbeat_stale_sec
                )

                if heartbeat_stale and not heartbeat_alert:
                    delivered = await audience.send_error(
                        bot,
                        "🚨 Парсер запущен, но heartbeat перестал обновляться. "
                        "Возможное зависание процесса.",
                    )
                    heartbeat_alert = delivered > 0
                elif not heartbeat_stale and heartbeat_alert:
                    await audience.send_error(
                        bot,
                        "✅ Heartbeat парсера восстановлен.",
                    )
                    heartbeat_alert = False

            await asyncio.sleep(settings.watchdog_poll_sec)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка watchdog")
            await asyncio.sleep(settings.watchdog_poll_sec)
