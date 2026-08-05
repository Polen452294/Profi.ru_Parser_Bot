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
    profi_proxy: str | None
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

        proxy = _parse_proxy_url(values, "TELEGRAM_PROXY")
        raw_profi_proxy = values.get("PROFI_PROXY", "").strip()
        if raw_profi_proxy.lower() == "direct":
            profi_proxy = None
        elif raw_profi_proxy:
            profi_proxy = _parse_proxy_url(values, "PROFI_PROXY")
        else:
            profi_proxy = proxy

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
            profi_proxy=profi_proxy,
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
                (
                    '[data-testid="auth_sms_code_input"], '
                    'input[data-testid*="sms"][data-testid*="code"], '
                    'input[data-testid*="code"], '
                    'input[autocomplete="one-time-code"], '
                    'input[name*="code"], '
                    'input[inputmode="numeric"]'
                ),
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
        if not self.profi_proxy:
            return None

        parsed = urlsplit(self.profi_proxy)
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

    def playwright_launch_options(self, *, headless: bool) -> dict[str, object]:
        options: dict[str, object] = {"headless": headless}
        proxy = self.playwright_proxy
        if proxy:
            options["proxy"] = proxy
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
        for directory in (
            self.data_dir,
            self.log_dir,
            self.debug_dir,
            self.backup_dir,
        ):
            try:
                directory.chmod(0o700)
            except OSError:
                pass
        for private_file in (DEFAULT_ENV_FILE, self.auth_state_path):
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
