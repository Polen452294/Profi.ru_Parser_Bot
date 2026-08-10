from __future__ import annotations

from dataclasses import dataclass
import os
from os import environ
from pathlib import Path
from typing import Mapping
from urllib.parse import unquote, urlsplit

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = PROJECT_DIR / ".env"


class ConfigurationError(ValueError):
    """Ошибка в пользовательских настройках проекта."""


def _parse_bool(values: Mapping[str, str], name: str, default: bool) -> bool:
    raw_value = values.get(name)
    if raw_value is None or not raw_value.strip():
        return default

    value = raw_value.strip().lower()
    if value in {"1", "true", "yes", "on", "да"}:
        return True
    if value in {"0", "false", "no", "off", "нет"}:
        return False
    raise ConfigurationError(
        f"{name}: ожидается true/false, yes/no, 1/0 или да/нет; получено {raw_value!r}"
    )


def _parse_int(
    values: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int = 0,
) -> int:
    raw_value = values.get(name)
    if raw_value is None or not raw_value.strip():
        return default

    try:
        value = int(raw_value.strip())
    except ValueError as exc:
        raise ConfigurationError(f"{name}: ожидается целое число") from exc

    if value < minimum:
        raise ConfigurationError(f"{name}: значение должно быть не меньше {minimum}")
    return value


def _parse_optional_int(values: Mapping[str, str], name: str) -> int | None:
    raw_value = values.get(name, "").strip()
    if not raw_value:
        return None
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name}: ожидается целое число") from exc
    if value == 0:
        raise ConfigurationError(f"{name}: значение не может быть равно нулю")
    return value


def _resolve_path(project_dir: Path, raw_value: str) -> Path:
    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        path = project_dir / path
    return path.resolve()


def _parse_proxy_url(values: Mapping[str, str], name: str) -> str | None:
    raw_value = values.get(name, "").strip()
    if not raw_value:
        return None

    try:
        parsed = urlsplit(raw_value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ConfigurationError(f"{name}: некорректный адрес прокси") from exc

    if parsed.scheme.lower() not in {"http", "https", "socks4", "socks5"}:
        raise ConfigurationError(
            f"{name}: поддерживаются схемы http, https, socks4 и socks5"
        )
    if not hostname or port is None:
        raise ConfigurationError(f"{name}: укажите адрес и порт прокси")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConfigurationError(f"{name}: путь, query и fragment не поддерживаются")
    return raw_value


def _parse_profi_proxy_pool(
    values: Mapping[str, str],
    primary_proxy: str | None,
    pool_path: Path,
) -> tuple[str | None, ...]:
    """Возвращает маршруты Chromium: основной и резервные без дублей."""
    routes: list[str | None] = [primary_proxy]
    candidates: list[tuple[str, str]] = []

    if pool_path.exists():
        try:
            file_lines = pool_path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError) as exc:
            raise ConfigurationError(
                f"PROFI_PROXY_POOL_FILE: не удалось прочитать {pool_path}"
            ) from exc
        for line_number, raw_line in enumerate(file_lines, start=1):
            route = raw_line.strip()
            if route and not route.startswith("#"):
                candidates.append(
                    (route, f"PROFI_PROXY_POOL_FILE, строка {line_number}")
                )

    for raw_route in values.get("PROFI_PROXY_POOL", "").split(","):
        if raw_route.strip():
            candidates.append((raw_route, "PROFI_PROXY_POOL"))

    for raw_route, source_name in candidates:
        route = raw_route.strip()
        if route.lower() == "direct":
            parsed_route = None
        else:
            parsed_route = _parse_proxy_url(
                {source_name: route},
                source_name,
            )
        if parsed_route not in routes:
            routes.append(parsed_route)
    return tuple(routes)


@dataclass(frozen=True, slots=True)
class Settings:
    project_dir: Path
    data_dir: Path
    log_dir: Path
    debug_dir: Path
    auth_state_path: Path
    seen_ids_path: Path
    orders_path: Path
    bot_cursor_path: Path
    system_events_path: Path
    system_event_cursor_path: Path
    telegram_chats_path: Path
    heartbeat_path: Path
    site_cooldown_path: Path
    version_state_path: Path
    instance_lock_path: Path
    backup_dir: Path

    page_url: str
    card_selector: str
    headless: bool
    debug_filter: bool
    selector_timeout_ms: int
    page_timeout_ms: int
    poll_base_sec: int
    poll_jitter_sec: int
    site_error_threshold: int
    error_backoff_base_sec: int
    error_backoff_max_sec: int
    heartbeat_interval_sec: int
    heartbeat_stale_sec: int
    success_stale_sec: int
    watchdog_poll_sec: int
    min_free_disk_mb: int
    trace_on_failure: bool
    debug_retention_days: int
    queue_compact_bytes: int
    seen_ids_retention_days: int
    seen_ids_max_count: int
    backup_retention_days: int

    bot_token: str
    admin_chat_id: int | None
    telegram_proxy: str | None
    telegram_proxy_rdns: bool
    profi_proxy: str | None
    profi_proxy_pool_path: Path
    profi_proxy_pool: tuple[str | None, ...]
    profi_proxy_start_from_pool: bool
    profi_proxy_random_on_start: bool
    profi_http_impersonate: str
    profi_browser_profile_path: Path
    profi_browser_stealth: bool
    profi_identity_rotate_on_repeat_block: bool
    profi_browser_locale: str
    profi_browser_timezone: str
    profi_user_agent: str
    profi_http_cookie_bridge: bool
    bot_poll_sec: int
    restart_delay_sec: int
    max_restarts: int
    session_recovery_enabled: bool
    session_recovery_headless: bool
    profi_login: str
    profi_otp_selector: str
    sms_code_timeout_sec: int
    recovery_cooldown_sec: int

    @classmethod
    def load(
        cls,
        *,
        env_file: Path | None = DEFAULT_ENV_FILE,
        values: Mapping[str, str] | None = None,
    ) -> "Settings":
        if values is None:
            if env_file is not None:
                load_dotenv(dotenv_path=env_file, override=False)
            values = environ

        project_dir = PROJECT_DIR
        data_dir = _resolve_path(project_dir, values.get("DATA_DIR", "data"))
        log_dir = _resolve_path(project_dir, values.get("LOG_DIR", "logs"))
        backup_dir = _resolve_path(project_dir, values.get("BACKUP_DIR", "backups"))
        debug_dir = log_dir / "debug"
        raw_proxy_pool_path = values.get("PROFI_PROXY_POOL_FILE", "").strip()
        profi_proxy_pool_path = _resolve_path(
            project_dir,
            raw_proxy_pool_path or str(data_dir / "profi_proxies.txt"),
        )
        profi_browser_profile_path = _resolve_path(
            project_dir,
            values.get(
                "PROFI_BROWSER_PROFILE_PATH",
                str(data_dir / "chromium-profile"),
            ),
        )

        proxy = _parse_proxy_url(values, "TELEGRAM_PROXY")
        raw_profi_proxy = values.get("PROFI_PROXY", "").strip()
        if raw_profi_proxy.lower() == "direct":
            profi_proxy = None
        elif raw_profi_proxy:
            profi_proxy = _parse_proxy_url(values, "PROFI_PROXY")
        else:
            profi_proxy = None
        profi_proxy_pool = _parse_profi_proxy_pool(
            values,
            profi_proxy,
            profi_proxy_pool_path,
        )

        return cls(
            project_dir=project_dir,
            data_dir=data_dir,
            log_dir=log_dir,
            debug_dir=debug_dir,
            auth_state_path=data_dir / "storage_state.json",
            seen_ids_path=data_dir / "seen_ids.json",
            orders_path=data_dir / "new_orders.jsonl",
            bot_cursor_path=data_dir / "bot_cursor.json",
            system_events_path=data_dir / "system_events.jsonl",
            system_event_cursor_path=data_dir / "system_event_cursor.json",
            telegram_chats_path=data_dir / "telegram_chats.json",
            heartbeat_path=data_dir / "heartbeat.json",
            site_cooldown_path=data_dir / "site_cooldown.json",
            version_state_path=data_dir / "version_state.json",
            instance_lock_path=data_dir / "parser.lock",
            backup_dir=backup_dir,
            page_url=values.get("PROFI_PAGE_URL", "https://profi.ru/backoffice/").strip(),
            card_selector=values.get(
                "PROFI_CARD_SELECTOR",
                'a[data-testid$="_order-snippet"]',
            ).strip(),
            headless=_parse_bool(values, "HEADLESS", True),
            debug_filter=_parse_bool(values, "DEBUG_FILTER", False),
            selector_timeout_ms=_parse_int(
                values,
                "SELECTOR_TIMEOUT_SEC",
                60,
                minimum=5,
            )
            * 1000,
            page_timeout_ms=_parse_int(
                values,
                "PAGE_TIMEOUT_SEC",
                90,
                minimum=10,
            )
            * 1000,
            poll_base_sec=_parse_int(values, "POLL_BASE_SEC", 90, minimum=5),
            poll_jitter_sec=_parse_int(values, "POLL_JITTER_SEC", 60),
            site_error_threshold=_parse_int(
                values,
                "SITE_ERROR_THRESHOLD",
                3,
                minimum=1,
            ),
            error_backoff_base_sec=_parse_int(
                values,
                "ERROR_BACKOFF_BASE_SEC",
                60,
                minimum=10,
            ),
            error_backoff_max_sec=_parse_int(
                values,
                "ERROR_BACKOFF_MAX_SEC",
                900,
                minimum=60,
            ),
            heartbeat_interval_sec=_parse_int(
                values,
                "HEARTBEAT_INTERVAL_SEC",
                30,
                minimum=5,
            ),
            heartbeat_stale_sec=_parse_int(
                values,
                "HEARTBEAT_STALE_SEC",
                120,
                minimum=30,
            ),
            success_stale_sec=_parse_int(
                values,
                "SUCCESS_STALE_SEC",
                900,
                minimum=120,
            ),
            watchdog_poll_sec=_parse_int(
                values,
                "WATCHDOG_POLL_SEC",
                30,
                minimum=10,
            ),
            min_free_disk_mb=_parse_int(
                values,
                "MIN_FREE_DISK_MB",
                1024,
                minimum=100,
            ),
            trace_on_failure=_parse_bool(values, "TRACE_ON_FAILURE", True),
            debug_retention_days=_parse_int(
                values,
                "DEBUG_RETENTION_DAYS",
                14,
                minimum=1,
            ),
            queue_compact_bytes=_parse_int(
                values,
                "QUEUE_COMPACT_BYTES",
                1_000_000,
                minimum=10_000,
            ),
            seen_ids_retention_days=_parse_int(
                values,
                "SEEN_IDS_RETENTION_DAYS",
                180,
                minimum=7,
            ),
            seen_ids_max_count=_parse_int(
                values,
                "SEEN_IDS_MAX_COUNT",
                100_000,
                minimum=1_000,
            ),
            backup_retention_days=_parse_int(
                values,
                "BACKUP_RETENTION_DAYS",
                30,
                minimum=1,
            ),
            bot_token=values.get("BOT_TOKEN", "").strip(),
            admin_chat_id=_parse_optional_int(values, "ADMIN_CHAT_ID"),
            telegram_proxy=proxy,
            telegram_proxy_rdns=_parse_bool(
                values,
                "TELEGRAM_PROXY_RDNS",
                True,
            ),
            profi_proxy=profi_proxy,
            profi_proxy_pool_path=profi_proxy_pool_path,
            profi_proxy_pool=profi_proxy_pool,
            profi_proxy_start_from_pool=_parse_bool(
                values,
                "PROFI_PROXY_START_FROM_POOL",
                False,
            ),
            profi_proxy_random_on_start=_parse_bool(
                values,
                "PROFI_PROXY_RANDOM_ON_START",
                False,
            ),
            profi_http_impersonate=values.get(
                "PROFI_HTTP_IMPERSONATE",
                "chrome",
            ).strip()
            or "chrome",
            profi_browser_profile_path=profi_browser_profile_path,
            profi_browser_stealth=_parse_bool(
                values,
                "PROFI_BROWSER_STEALTH",
                True,
            ),
            profi_identity_rotate_on_repeat_block=_parse_bool(
                values,
                "PROFI_IDENTITY_ROTATE_ON_REPEAT_BLOCK",
                True,
            ),
            profi_browser_locale=values.get(
                "PROFI_BROWSER_LOCALE",
                "ru-RU",
            ).strip()
            or "ru-RU",
            profi_browser_timezone=values.get(
                "PROFI_BROWSER_TIMEZONE",
                "Europe/Moscow",
            ).strip()
            or "Europe/Moscow",
            profi_user_agent=values.get(
                "PROFI_USER_AGENT",
                (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/136.0.0.0 Safari/537.36"
                ),
            ).strip(),
            profi_http_cookie_bridge=_parse_bool(
                values,
                "PROFI_HTTP_COOKIE_BRIDGE",
                True,
            ),
            bot_poll_sec=_parse_int(values, "BOT_POLL_SEC", 3, minimum=1),
            restart_delay_sec=_parse_int(values, "RESTART_DELAY_SEC", 10, minimum=1),
            max_restarts=_parse_int(values, "MAX_RESTARTS", 50, minimum=1),
            session_recovery_enabled=_parse_bool(
                values,
                "SESSION_RECOVERY_ENABLED",
                True,
            ),
            session_recovery_headless=_parse_bool(
                values,
                "SESSION_RECOVERY_HEADLESS",
                True,
            ),
            profi_login=values.get("PROFI_LOGIN", "").strip(),
            profi_otp_selector=values.get(
                "PROFI_OTP_SELECTOR",
                '[data-testid="auth_pin_input"]',
            ).strip(),
            sms_code_timeout_sec=_parse_int(
                values,
                "SMS_CODE_TIMEOUT_SEC",
                300,
                minimum=60,
            ),
            recovery_cooldown_sec=_parse_int(
                values,
                "RECOVERY_COOLDOWN_SEC",
                300,
                minimum=30,
            ),
        )

    @property
    def playwright_proxy(self) -> dict[str, str] | None:
        """Преобразует URL прокси Profi.ru в формат запуска Playwright."""
        return self.playwright_proxy_for(self.profi_proxy)

    @property
    def profi_proxy_rotation_enabled(self) -> bool:
        """Ротация включена только при наличии резервного маршрута."""
        return len(self.profi_proxy_pool) > 1

    @property
    def initial_profi_proxy_index(self) -> int:
        """Выбирает основной маршрут или первый адрес из пула для старта."""
        if self.profi_proxy_start_from_pool and self.profi_proxy_rotation_enabled:
            return 1
        return 0

    @property
    def initial_profi_proxy_candidates(self) -> tuple[int, ...]:
        """Возвращает допустимые стартовые маршруты без раскрытия их адресов."""
        if self.profi_proxy_random_on_start:
            proxy_indexes = tuple(
                index
                for index, proxy_url in enumerate(self.profi_proxy_pool)
                if index > 0 and proxy_url is not None
            )
            if proxy_indexes:
                return proxy_indexes
        return (self.initial_profi_proxy_index,)

    @staticmethod
    def playwright_proxy_for(proxy_url: str | None) -> dict[str, str] | None:
        if not proxy_url:
            return None

        parsed = urlsplit(proxy_url)
        hostname = parsed.hostname or ""
        host = f"[{hostname}]" if ":" in hostname else hostname
        options = {
            "server": f"{parsed.scheme.lower()}://{host}:{parsed.port}",
        }
        if parsed.username is not None:
            options["username"] = unquote(parsed.username)
        if parsed.password is not None:
            options["password"] = unquote(parsed.password)
        return options

    def playwright_launch_options(
        self,
        *,
        headless: bool,
        proxy_url: str | None = None,
        use_primary_proxy: bool = True,
    ) -> dict[str, object]:
        options: dict[str, object] = {"headless": headless}
        selected_proxy = self.profi_proxy if use_primary_proxy else proxy_url
        proxy = self.playwright_proxy_for(selected_proxy)
        if proxy:
            options["proxy"] = proxy
        else:
            # Не наследовать прокси рабочего стола или окружения: сайт должен
            # использовать обычный маршрут Raspberry Pi.
            options["args"] = ["--no-proxy-server"]
        return options

    @property
    def proxy_endpoint(self) -> tuple[str, int] | None:
        if not self.telegram_proxy:
            return None
        parsed = urlsplit(self.telegram_proxy)
        if parsed.hostname is None or parsed.port is None:
            return None
        return parsed.hostname, parsed.port

    def ensure_directories(self) -> None:
        if os.name == "posix":
            os.umask(0o077)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.profi_browser_profile_path.mkdir(parents=True, exist_ok=True)
        for directory in (
            self.data_dir,
            self.log_dir,
            self.debug_dir,
            self.backup_dir,
            self.profi_browser_profile_path,
        ):
            try:
                directory.chmod(0o700)
            except OSError:
                pass
        for private_file in (
            DEFAULT_ENV_FILE,
            self.auth_state_path,
            self.profi_proxy_pool_path,
            self.site_cooldown_path,
        ):
            if private_file.exists():
                try:
                    private_file.chmod(0o600)
                except OSError:
                    pass

    def validation_errors(self, *, require_telegram: bool) -> list[str]:
        errors: list[str] = []

        if not self.page_url:
            errors.append("PROFI_PAGE_URL не может быть пустым")
        if not self.card_selector:
            errors.append("PROFI_CARD_SELECTOR не может быть пустым")
        if not self.profi_user_agent:
            errors.append("PROFI_USER_AGENT не может быть пустым")
        if not self.profi_http_impersonate:
            errors.append("PROFI_HTTP_IMPERSONATE не может быть пустым")
        if not self.profi_browser_locale:
            errors.append("PROFI_BROWSER_LOCALE не может быть пустым")
        if not self.profi_browser_timezone:
            errors.append("PROFI_BROWSER_TIMEZONE не может быть пустым")

        if require_telegram:
            if not self.bot_token or self.bot_token.lower() in {
                "вставьте_токен",
                "your_bot_token",
            }:
                errors.append("Укажите BOT_TOKEN в файле .env")
            if self.session_recovery_enabled and not self.profi_login:
                errors.append(
                    "Укажите PROFI_LOGIN для автоматического восстановления сессии"
                )
            if self.session_recovery_enabled and not self.profi_otp_selector:
                errors.append("PROFI_OTP_SELECTOR не может быть пустым")

        if self.error_backoff_max_sec < self.error_backoff_base_sec:
            errors.append(
                "ERROR_BACKOFF_MAX_SEC не может быть меньше ERROR_BACKOFF_BASE_SEC"
            )
        if self.heartbeat_stale_sec <= self.heartbeat_interval_sec:
            errors.append(
                "HEARTBEAT_STALE_SEC должен быть больше HEARTBEAT_INTERVAL_SEC"
            )

        return errors
