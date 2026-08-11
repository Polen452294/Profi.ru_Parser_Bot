from __future__ import annotations

import logging
import random
import time

from playwright.sync_api import sync_playwright

from client import BrowserUnavailableError, ProfiClient, SiteResponseError
from config import ConfigurationError, Settings
from filters import evaluate_order
from health import (
    ACCESS_CHALLENGE_EXIT_CODE,
    SESSION_EXPIRED_EXIT_CODE,
    SiteHealthReporter,
)
from heartbeat import HeartbeatReporter
from logger_setup import setup_logger
from parser import parse_order_snippet
from site_cooldown import activate_site_cooldown
from storage import append_jsonl, load_seen_ids, save_seen_ids


logger = logging.getLogger("parser")


class SessionExpiredError(RuntimeError):
    pass


class AccessChallengeError(RuntimeError):
    pass


def _select_initial_proxy_index(settings: Settings) -> int:
    return random.choice(settings.initial_profi_proxy_candidates)


def _sleep_with_jitter(base_seconds: int, jitter_seconds: int) -> None:
    time.sleep(base_seconds + random.uniform(0, jitter_seconds))


def failure_backoff_seconds(
    settings: Settings,
    consecutive_errors: int,
    retry_after: int | None = None,
) -> float:
    exponent = min(max(0, consecutive_errors - 1), 8)
    calculated = settings.error_backoff_base_sec * (2**exponent)
    if retry_after is not None:
        calculated = max(calculated, retry_after)
    capped = min(calculated, settings.error_backoff_max_sec)
    jitter = random.uniform(0, min(30, capped * 0.2))
    return min(settings.error_backoff_max_sec, capped + jitter)


def _sleep_after_failure(
    settings: Settings,
    consecutive_errors: int,
    retry_after: int | None = None,
) -> None:
    delay = failure_backoff_seconds(settings, consecutive_errors, retry_after)
    logger.warning("Защитная пауза перед следующим запросом: %.0f сек.", delay)
    time.sleep(delay)


def _open_started_client(client: ProfiClient) -> ProfiClient:
    try:
        client.open_board()
    except SiteResponseError as exc:
        if exc.status == 403:
            screenshot_path, _, _ = client.save_debug("access_challenge")
            if screenshot_path.exists():
                exc.screenshot_path = screenshot_path
        client.close()
        raise
    page = client.page
    logger.info(
        "Страница заказов открыта: title=%r, url=%s",
        page.title() if page else None,
        page.url if page else None,
    )
    return client


def _start_client(
    playwright,
    settings: Settings,
    *,
    proxy_index: int = 0,
) -> ProfiClient:
    client = ProfiClient(playwright, settings, proxy_index=proxy_index).start()
    return _open_started_client(client)


def _restart_client(
    client: ProfiClient | None,
    playwright,
    settings: Settings,
    reason: str,
    *,
    proxy_index: int = 0,
) -> ProfiClient:
    logger.warning("Перезапуск браузера: %s", reason)
    if client is not None:
        target_proxy_index = proxy_index % len(settings.profi_proxy_pool)
        if target_proxy_index != client.proxy_index:
            client.switch_proxy_and_identity(target_proxy_index)
            return _open_started_client(client)
        client.close()
    return _start_client(playwright, settings, proxy_index=proxy_index)


def _raise_access_challenge(
    client: ProfiClient,
    health: SiteHealthReporter,
    heartbeat: HeartbeatReporter,
    reason: str,
    *,
    debug_prefix: str = "access_challenge",
) -> None:
    screenshot_path, _, _ = client.save_debug(debug_prefix)
    screenshot = str(screenshot_path) if screenshot_path.exists() else None
    health.access_challenge(reason, screenshot)
    heartbeat.mark_paused(reason)
    raise AccessChallengeError(reason)


def _restart_after_ip_limit(
    client: ProfiClient,
    playwright,
    settings: Settings,
    heartbeat: HeartbeatReporter,
    reason: str,
    *,
    proxy_index: int,
    reset_identity: bool,
) -> ProfiClient:
    client.save_debug("ip_rotation_limit")
    if reset_identity:
        client.replace_blocked_identity(reason)
    logger.warning(
        "%s. Переключаю маршрут Profi.ru на %s/%s",
        reason,
        proxy_index + 1,
        len(settings.profi_proxy_pool),
    )
    heartbeat.mark_failure(reason)
    time.sleep(min(10, settings.poll_base_sec))
    return _restart_client(
        client,
        playwright,
        settings,
        "12-часовой лимит для текущего IP",
        proxy_index=proxy_index,
    )


def _page_looks_logged_out(client: ProfiClient) -> bool:
    if client.page is None:
        return False
    title = client.page.title().lower()
    url = client.page.url.lower()
    return "вход" in title or "login" in title or "login" in url


def _collect_matching_orders(
    client: ProfiClient,
    seen_ids: set[str],
    *,
    debug_filter: bool,
) -> list[dict]:
    cards = client.cards_locator()
    orders: list[dict] = []

    for index in range(cards.count()):
        try:
            order = parse_order_snippet(cards.nth(index))
        except Exception:
            logger.exception("Не удалось разобрать карточку #%d", index + 1)
            continue

        order_id = order.get("order_id")
        if not order_id or order_id in seen_ids:
            continue

        decision = evaluate_order(order)
        if debug_filter:
            logger.info(
                "Фильтр: id=%s, принят=%s, правило=%r, исключение=%r, заголовок=%r",
                order_id,
                decision.accepted,
                decision.matched_rule.phrase if decision.matched_rule else None,
                decision.excluded_rule.phrase if decision.excluded_rule else None,
                order.get("title"),
            )

        if not decision.accepted:
            continue

        seen_ids.add(str(order_id))
        orders.append(order)

    return orders


def run_parser(settings: Settings) -> None:
    settings.ensure_directories()
    setup_logger("parser", settings.log_dir)
    health = SiteHealthReporter(
        settings.system_events_path,
        settings.site_error_threshold,
    )

    if not settings.auth_state_path.exists():
        message = "Файл авторизации Profi.ru отсутствует"
        health.session_expired(message)
        raise SessionExpiredError(message)

    with (
        HeartbeatReporter(
            settings.heartbeat_path,
            settings.heartbeat_interval_sec,
        ) as heartbeat,
        sync_playwright() as playwright,
    ):
        seen_ids = load_seen_ids(settings.seen_ids_path)
        seen_ids = save_seen_ids(
            settings.seen_ids_path,
            seen_ids,
            retention_days=settings.seen_ids_retention_days,
            max_count=settings.seen_ids_max_count,
        )
        health.parser_started()
        logger.info(
            "Мониторинг запущен. Интервал: %s–%s сек.; обработано ранее: %d",
            settings.poll_base_sec,
            settings.poll_base_sec + settings.poll_jitter_sec,
            len(seen_ids),
        )

        client: ProfiClient | None = None
        proxy_index = _select_initial_proxy_index(settings)
        try:
            try:
                client = _start_client(
                    playwright,
                    settings,
                    proxy_index=proxy_index,
                )
            except SiteResponseError as exc:
                if exc.status == 403:
                    message = "Profi.ru ограничил доступ при открытии страницы: HTTP 403"
                    screenshot = (
                        str(exc.screenshot_path) if exc.screenshot_path else None
                    )
                    health.access_challenge(message, screenshot)
                    heartbeat.mark_paused(message)
                    raise AccessChallengeError(message) from exc
                raise

            while True:
                try:
                    client.soft_refresh()

                    ip_limit = client.detect_ip_rotation_limit()

                    challenge = client.detect_access_challenge()
                    if challenge:
                        _raise_access_challenge(client, health, heartbeat, challenge)

                    if not client.wait_cards():
                        ip_limit = client.detect_ip_rotation_limit()
                        challenge = client.detect_access_challenge()
                        if challenge:
                            _raise_access_challenge(
                                client,
                                health,
                                heartbeat,
                                challenge,
                            )
                        if _page_looks_logged_out(client):
                            message = "Сессия Profi.ru завершена или сайт запросил вход"
                            health.session_expired(message)
                            heartbeat.mark_failure(message)
                            raise SessionExpiredError(message)

                        message = "Profi.ru не показывает карточки заказов"
                        health.record_failure(message)
                        heartbeat.mark_failure(message)
                        logger.warning(
                            "Карточки не найдены; уменьшаю частоту запросов"
                        )
                        _sleep_after_failure(
                            settings,
                            health.consecutive_errors,
                        )
                        continue

                    health.record_success()
                    heartbeat.mark_success()
                    new_orders = _collect_matching_orders(
                        client,
                        seen_ids,
                        debug_filter=settings.debug_filter,
                    )
                    if new_orders:
                        for order in new_orders:
                            append_jsonl(settings.orders_path, order)
                        seen_ids = save_seen_ids(
                            settings.seen_ids_path,
                            seen_ids,
                            retention_days=settings.seen_ids_retention_days,
                            max_count=settings.seen_ids_max_count,
                        )
                        logger.info("Новых подходящих заявок: %d", len(new_orders))

                except SessionExpiredError:
                    raise
                except AccessChallengeError:
                    raise
                except SiteResponseError as exc:
                    if exc.status == 401:
                        message = "Profi.ru отклонил завершившуюся сессию (HTTP 401)"
                        health.session_expired(message)
                        heartbeat.mark_failure(message)
                        raise SessionExpiredError(message) from exc
                    if exc.status == 403:
                        message = "Profi.ru ограничил доступ: HTTP 403"
                        screenshot_path, _, _ = client.save_debug("access_challenge")
                        screenshot = (
                            str(screenshot_path) if screenshot_path.exists() else None
                        )
                        health.access_challenge(message, screenshot)
                        heartbeat.mark_paused(message)
                        raise AccessChallengeError(message) from exc
                    message = f"Profi.ru ограничил запросы: HTTP {exc.status}"
                    health.record_failure(message)
                    heartbeat.mark_failure(message)
                    client.save_debug(f"http_{exc.status}")
                    _sleep_after_failure(
                        settings,
                        health.consecutive_errors,
                        exc.retry_after,
                    )
                    continue
                except BrowserUnavailableError as exc:
                    health.record_failure(f"Ошибка браузера: {exc}")
                    heartbeat.mark_failure(f"Ошибка браузера: {exc}")
                    _sleep_after_failure(settings, health.consecutive_errors)
                    client = _restart_client(
                        client,
                        playwright,
                        settings,
                        str(exc),
                        proxy_index=proxy_index,
                    )
                    continue
                except Exception as exc:
                    health.record_failure(f"Ошибка получения заказов: {exc}")
                    heartbeat.mark_failure(f"Ошибка получения заказов: {exc}")
                    logger.exception("Ошибка цикла мониторинга; браузер будет перезапущен")
                    _sleep_after_failure(settings, health.consecutive_errors)
                    client = _restart_client(
                        client,
                        playwright,
                        settings,
                        "ошибка цикла мониторинга",
                        proxy_index=proxy_index,
                    )
                    continue

                _sleep_with_jitter(settings.poll_base_sec, settings.poll_jitter_sec)
        finally:
            if client is not None:
                client.close()


def main() -> int:
    try:
        settings = Settings.load()
        errors = settings.validation_errors(require_telegram=False)
        if errors:
            for error in errors:
                print(f"ОШИБКА: {error}")
            return 2
        run_parser(settings)
        return 0
    except SessionExpiredError as exc:
        print(f"СЕССИЯ PROFI.RU ЗАВЕРШЕНА: {exc}")
        return SESSION_EXPIRED_EXIT_CODE
    except AccessChallengeError as exc:
        print(f"ДОСТУП PROFI.RU ПРИОСТАНОВЛЕН: {exc}")
        return ACCESS_CHALLENGE_EXIT_CODE
    except ConfigurationError as exc:
        print(f"ОШИБКА НАСТРОЕК: {exc}")
        return 2
    except KeyboardInterrupt:
        print("\nПарсер остановлен пользователем.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
