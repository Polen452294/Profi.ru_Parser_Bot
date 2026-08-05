from __future__ import annotations

from urllib.parse import urlsplit

from aiogram.client.session.aiohttp import AiohttpSession

from config import Settings


def create_telegram_session(
    settings: Settings,
    *,
    timeout: int = 60,
) -> AiohttpSession:
    """Создаёт Telegram-транспорт с управляемым DNS-режимом SOCKS."""
    session = AiohttpSession(proxy=settings.telegram_proxy, timeout=timeout)
    if settings.telegram_proxy:
        scheme = urlsplit(settings.telegram_proxy).scheme.lower()
        if scheme in {"socks4", "socks5"}:
            # Aiogram всегда задаёт rdns=True. Некоторые локальные SOCKS-сервисы
            # на Raspberry Pi требуют обычный socks5 с локальным DNS.
            session._connector_init["rdns"] = settings.telegram_proxy_rdns
    return session
