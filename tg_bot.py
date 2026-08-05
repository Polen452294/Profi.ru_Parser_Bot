"""Совместимый запуск только Telegram-уведомлений без процесса парсера."""

from __future__ import annotations

import asyncio

from config import ConfigurationError, Settings
from run_all import run_telegram_only


def main() -> int:
    try:
        settings = Settings.load()
        errors = settings.validation_errors(require_telegram=True)
        if errors:
            for error in errors:
                print(f"ОШИБКА: {error}")
            return 2
        asyncio.run(run_telegram_only(settings))
        return 0
    except ConfigurationError as exc:
        print(f"ОШИБКА НАСТРОЕК: {exc}")
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
