from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import shutil
import socket
import sys

from config import ConfigurationError, DEFAULT_ENV_FILE, Settings
from version import APP_VERSION


IS_WINDOWS = sys.platform == "win32"
INSTALL_COMMAND = "install.bat" if IS_WINDOWS else "bash install.sh"
CHECK_COMMAND = "check.bat" if IS_WINDOWS else "bash check.sh"
START_COMMAND = "start.bat" if IS_WINDOWS else "bash start.sh"


def _print_check(status: str, message: str) -> None:
    print(f"[{status}] {message}")


def _proxy_connection_error(settings: Settings, timeout: float = 3.0) -> str | None:
    endpoint = settings.proxy_endpoint
    if endpoint is None:
        return None
    host, port = endpoint
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return None
    except OSError as exc:
        return (
            f"Прокси {host}:{port} недоступен: {exc}. "
            "Запустите прокси-сервис или оставьте TELEGRAM_PROXY пустым"
        )


def run_doctor(settings: Settings) -> int:
    errors = 0
    warnings = 0

    print("\nПроверка готовности проекта\n")
    _print_check("OK", f"Версия проекта: {APP_VERSION}")

    if sys.version_info >= (3, 10):
        _print_check("OK", f"Python {sys.version_info.major}.{sys.version_info.minor}")
    else:
        _print_check("ОШИБКА", "Требуется Python 3.10 или новее")
        errors += 1

    if DEFAULT_ENV_FILE.exists():
        _print_check("OK", f"Файл настроек найден: {DEFAULT_ENV_FILE.name}")
    else:
        _print_check("ОШИБКА", "Нет файла .env. Скопируйте .env.example в .env")
        errors += 1

    for validation_error in settings.validation_errors(require_telegram=True):
        _print_check("ОШИБКА", validation_error)
        errors += 1

    if settings.admin_chat_id is None:
        _print_check(
            "ВНИМАНИЕ",
            "ADMIN_CHAT_ID пуст: бот работает в открытом режиме для любых личных чатов",
        )

    try:
        settings.ensure_directories()
        _print_check("OK", f"Папка данных доступна: {settings.data_dir}")
        free_mb = shutil.disk_usage(settings.project_dir).free // (1024 * 1024)
        if free_mb >= settings.min_free_disk_mb:
            _print_check("OK", f"Свободно на диске: {free_mb} МБ")
        else:
            _print_check(
                "ВНИМАНИЕ",
                f"На диске осталось {free_mb} МБ; рекомендуется не менее "
                f"{settings.min_free_disk_mb} МБ",
            )
    except OSError as exc:
        _print_check("ОШИБКА", f"Не удаётся создать служебные папки: {exc}")
        errors += 1

    try:
        from filters import (
            DISALLOWED_PLATFORM_PATTERNS,
            DISALLOWED_TOPICS,
            TARGET_KEYWORD_PATTERNS,
        )

        _print_check(
            "OK",
            f"Фильтр загружен: {len(TARGET_KEYWORD_PATTERNS)} целевых шаблонов, "
            f"{len(DISALLOWED_TOPICS) + len(DISALLOWED_PLATFORM_PATTERNS)} исключений",
        )
    except Exception as exc:
        _print_check("ОШИБКА", f"Не удалось загрузить фильтр: {exc}")
        errors += 1

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser_path = Path(playwright.chromium.executable_path)
        if browser_path.exists():
            _print_check("OK", "Браузер Chromium установлен")
        else:
            _print_check(
                "ОШИБКА",
                f"Chromium не установлен. Запустите {INSTALL_COMMAND} ещё раз",
            )
            errors += 1
    except Exception as exc:
        _print_check("ОШИБКА", f"Playwright недоступен: {exc}")
        errors += 1

    if settings.telegram_proxy:
        try:
            __import__("aiohttp_socks")
        except ImportError:
            _print_check(
                "ОШИБКА",
                "Для общего TELEGRAM_PROXY требуется пакет aiohttp-socks; "
                f"повторите {INSTALL_COMMAND}",
            )
            errors += 1
        else:
            proxy_error = _proxy_connection_error(settings)
            if proxy_error:
                _print_check("ОШИБКА", proxy_error)
                errors += 1
            else:
                _print_check(
                    "OK",
                    "Общий прокси доступен для Telegram и Chromium/Profi.ru",
                )

    if settings.auth_state_path.exists():
        _print_check("OK", "Авторизация Profi.ru сохранена")
    elif settings.session_recovery_enabled and settings.profi_login:
        _print_check(
            "OK",
            "Первичная сессия Profi.ru будет создана через Telegram и SMS после запуска",
        )
    else:
        _print_check("ВНИМАНИЕ", "Авторизация Profi.ru ещё не выполнена")
        warnings += 1

    if settings.session_recovery_enabled and settings.profi_login:
        _print_check("OK", "Автовосстановление cookies через Telegram включено")

    print()
    if errors:
        print(f"Найдены ошибки: {errors}. Исправьте их перед запуском.")
        return 1
    if warnings:
        print("Основные настройки верны, но требуется авторизация на Profi.ru.")
        return 0

    print(f"Всё готово. Можно запускать {START_COMMAND}.")
    return 0


def _load_settings() -> Settings | None:
    try:
        return Settings.load()
    except ConfigurationError as exc:
        print(f"ОШИБКА НАСТРОЕК: {exc}")
        return None


def _validate(settings: Settings, *, require_telegram: bool) -> bool:
    errors = settings.validation_errors(require_telegram=require_telegram)
    for error in errors:
        print(f"ОШИБКА: {error}")
    return not errors


def _runtime_preflight(settings: Settings, *, require_telegram: bool) -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser_path = Path(playwright.chromium.executable_path)
        if not browser_path.exists():
            print(f"ОШИБКА: Chromium не установлен. Запустите {INSTALL_COMMAND}.")
            return False
    except Exception as exc:
        print(f"ОШИБКА: Playwright недоступен: {exc}")
        return False

    if require_telegram and settings.telegram_proxy:
        try:
            __import__("aiohttp_socks")
        except ImportError:
            print(
                "ОШИБКА: для Telegram-прокси повторно запустите "
                f"{INSTALL_COMMAND}."
            )
            return False
        proxy_error = _proxy_connection_error(settings)
        if proxy_error:
            print(f"ОШИБКА: {proxy_error}")
            return False
    return True


def command_run(settings: Settings) -> int:
    if not _validate(settings, require_telegram=True):
        print(f"\nЗапустите {CHECK_COMMAND} для подробной диагностики.")
        return 2
    if not _runtime_preflight(settings, require_telegram=True):
        return 2

    if not settings.auth_state_path.exists():
        print(
            "Сохранённой сессии Profi.ru пока нет. После запуска бот сам запросит "
            "SMS-код в Telegram.\n"
        )

    from run_all import run
    from instance_lock import AlreadyRunningError

    print("Запускаю мониторинг. Для остановки нажмите Ctrl+C.\n")
    try:
        asyncio.run(run(settings))
    except AlreadyRunningError as exc:
        print(f"ОШИБКА: {exc}")
        return 3
    except KeyboardInterrupt:
        print("\nРабота остановлена пользователем.")
    return 0


def command_parser(settings: Settings) -> int:
    if not _validate(settings, require_telegram=False):
        return 2
    if not _runtime_preflight(settings, require_telegram=False):
        return 2

    if not settings.auth_state_path.exists():
        print(
            "ОШИБКА: режим без Telegram не может создать первичную сессию. "
            f"Запустите полный режим: {START_COMMAND}."
        )
        return 2

    from main import run_parser

    print("Запускаю только парсер, без Telegram. Для остановки нажмите Ctrl+C.\n")
    try:
        run_parser(settings)
    except KeyboardInterrupt:
        print("\nПарсер остановлен пользователем.")
    return 0


def command_auth(settings: Settings, *, force: bool) -> int:
    from auth import authorize
    from playwright.sync_api import sync_playwright

    if settings.auth_state_path.exists() and not force:
        print("Авторизация уже сохранена. Используйте --force для повторного входа.")
        return 0

    with sync_playwright() as playwright:
        authorize(playwright, settings, force=force)
    return 0


def command_filter(text: str | None) -> int:
    from filters import evaluate_order

    if not text:
        text = input("Введите текст заявки: ").strip()
    decision = evaluate_order({"title": text})

    if decision.excluded_rule:
        print(f"НЕ ПОДХОДИТ: найдено исключение «{decision.excluded_rule.phrase}»")
    elif decision.matched_rule:
        print(f"ПОДХОДИТ: сработало правило «{decision.matched_rule.phrase}»")
        print(f"Группа: {decision.matched_rule.group}")
    else:
        print("НЕ ПОДХОДИТ: ни одно целевое правило не сработало")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Парсер целевых заявок Profi.ru",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="проверить установку и настройки")
    subparsers.add_parser("run", help="запустить парсер и Telegram")
    subparsers.add_parser("parser", help="запустить только парсер")

    auth_parser = subparsers.add_parser("auth", help="авторизоваться на Profi.ru")
    auth_parser.add_argument(
        "--force",
        action="store_true",
        help="заново выполнить вход, даже если сессия уже сохранена",
    )

    filter_parser = subparsers.add_parser("filter", help="проверить текст фильтром")
    filter_parser.add_argument("text", nargs="?", help="текст тестовой заявки")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    settings = _load_settings()
    if settings is None:
        return 2

    if arguments.command == "doctor":
        return run_doctor(settings)
    if arguments.command == "run":
        return command_run(settings)
    if arguments.command == "parser":
        return command_parser(settings)
    if arguments.command == "auth":
        return command_auth(settings, force=arguments.force)
    if arguments.command == "filter":
        return command_filter(arguments.text)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
