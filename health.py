from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from storage import append_jsonl


EVENT_SITE_ERROR = "site_error"
EVENT_SITE_RECOVERED = "site_recovered"
EVENT_SESSION_EXPIRED = "session_expired"
EVENT_PARSER_STARTED = "parser_started"
EVENT_ACCESS_CHALLENGE = "access_challenge"
SESSION_EXPIRED_EXIT_CODE = 20
ACCESS_CHALLENGE_EXIT_CODE = 21


def emit_system_event(
    path: Path,
    event_type: str,
    message: str,
    **details: Any,
) -> None:
    append_jsonl(
        path,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "message": message,
            "details": details,
        },
    )


class SiteHealthReporter:
    def __init__(self, path: Path, error_threshold: int):
        self.path = path
        self.error_threshold = error_threshold
        self.consecutive_errors = 0
        self.alert_sent = False

    def parser_started(self) -> None:
        emit_system_event(self.path, EVENT_PARSER_STARTED, "Парсер запущен")

    @property
    def will_alert_on_next_failure(self) -> bool:
        return (
            not self.alert_sent
            and self.consecutive_errors + 1 >= self.error_threshold
        )

    def record_failure(
        self,
        message: str,
        screenshot_path: str | None = None,
    ) -> None:
        self.consecutive_errors += 1
        if self.consecutive_errors < self.error_threshold or self.alert_sent:
            return

        self.alert_sent = True
        emit_system_event(
            self.path,
            EVENT_SITE_ERROR,
            message,
            consecutive_errors=self.consecutive_errors,
            screenshot_path=screenshot_path,
        )

    def record_success(self) -> None:
        had_alert = self.alert_sent
        self.consecutive_errors = 0
        self.alert_sent = False
        if had_alert:
            emit_system_event(
                self.path,
                EVENT_SITE_RECOVERED,
                "Profi.ru снова корректно показывает карточки заказов",
            )

    def session_expired(
        self,
        message: str,
        screenshot_path: str | None = None,
    ) -> None:
        emit_system_event(
            self.path,
            EVENT_SESSION_EXPIRED,
            message,
            screenshot_path=screenshot_path,
        )

    def access_challenge(self, message: str, screenshot_path: str | None) -> None:
        emit_system_event(
            self.path,
            EVENT_ACCESS_CHALLENGE,
            message,
            screenshot_path=screenshot_path,
        )
