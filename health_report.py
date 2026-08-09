from __future__ import annotations

from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import shutil

from playwright.sync_api import sync_playwright

from config import Settings
from heartbeat import parse_utc_timestamp, read_heartbeat
from runtime_control import ParserPauseControl
from session_recovery import SessionRecoveryManager
from version import APP_VERSION


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "не установлен"


def _age_text(timestamp: datetime | None) -> str:
    if timestamp is None:
        return "ещё не было"
    seconds = max(0, int((datetime.now(timezone.utc) - timestamp).total_seconds()))
    if seconds < 60:
        return f"{seconds} сек. назад"
    if seconds < 3600:
        return f"{seconds // 60} мин. назад"
    return f"{seconds // 3600} ч. назад"


def chromium_installed() -> bool:
    try:
        with sync_playwright() as playwright:
            return Path(playwright.chromium.executable_path).exists()
    except Exception:
        return False


def build_health_report(
    settings: Settings,
    recovery: SessionRecoveryManager,
    control: ParserPauseControl,
) -> str:
    heartbeat = read_heartbeat(settings.heartbeat_path)
    alive_at = parse_utc_timestamp(heartbeat.get("process_alive_at"))
    success_at = parse_utc_timestamp(heartbeat.get("last_success_at"))
    disk = shutil.disk_usage(settings.project_dir)
    free_mb = disk.free // (1024 * 1024)
    disk_icon = "✅" if free_mb >= settings.min_free_disk_mb else "⚠️"

    if control.paused:
        parser_state = f"пауза — {control.reason}"
    elif recovery.in_progress:
        parser_state = "восстановление сессии"
    else:
        parser_state = str(heartbeat.get("status") or "нет данных")

    rotating_profi_routes = settings.profi_proxy_rotation_enabled
    if rotating_profi_routes:
        primary = "прокси" if settings.profi_proxy else "прямой маршрут"
        start_mode = (
            "старт через пул"
            if settings.profi_proxy_start_from_pool
            else f"старт: {primary}"
        )
        proxy_state = (
            f"Profi.ru: пул из {len(settings.profi_proxy_pool)} маршрутов, {start_mode}; "
            f"Telegram: {'прокси' if settings.telegram_proxy else 'напрямую'}"
        )
    elif settings.telegram_proxy and settings.profi_proxy:
        proxy_state = "Telegram и Profi.ru через прокси"
    elif settings.telegram_proxy:
        proxy_state = "Telegram через прокси; Profi.ru напрямую"
    elif settings.profi_proxy:
        proxy_state = "Telegram напрямую; Profi.ru через прокси"
    else:
        proxy_state = "не используется"

    return (
        f"🩺 Состояние сервиса v{APP_VERSION}\n\n"
        f"Telegram: ✅ команда получена\n"
        f"Chromium: {'✅ установлен' if chromium_installed() else '❌ не найден'}\n"
        f"Прокси: {proxy_state}\n"
        f"Cookies: {'✅ есть' if settings.auth_state_path.exists() else '⚠️ отсутствуют'}\n"
        f"Парсер: {parser_state}\n"
        f"Heartbeat: {_age_text(alive_at)}\n"
        f"Успешная проверка Profi.ru: {_age_text(success_at)}\n"
        f"{disk_icon} Свободно на диске: {free_mb} МБ\n\n"
        f"Playwright: {_package_version('playwright')}\n"
        f"aiogram: {_package_version('aiogram')}"
    )
