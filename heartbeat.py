from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any

from storage import read_json_object, write_json_atomic


logger = logging.getLogger("parser.heartbeat")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class HeartbeatReporter:
    """Пишет независимый признак жизни и время последней успешной проверки."""

    def __init__(self, path: Path, interval_sec: int):
        self.path = path
        self.interval_sec = interval_sec
        self._lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        previous = read_json_object(path)
        self._payload: dict[str, Any] = {
            "process_started_at": utc_now_iso(),
            "process_alive_at": utc_now_iso(),
            "last_success_at": previous.get("last_success_at"),
            "status": "starting",
            "message": "Парсер запускается",
            "pid": os.getpid(),
        }

    def start(self) -> None:
        self._write()
        self._thread = Thread(
            target=self._run,
            name="parser-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def __enter__(self) -> "HeartbeatReporter":
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def mark_success(self) -> None:
        now = utc_now_iso()
        self._update(
            process_alive_at=now,
            last_success_at=now,
            status="ok",
            message="Страница заказов успешно проверена",
        )

    def mark_failure(self, message: str) -> None:
        self._update(
            process_alive_at=utc_now_iso(),
            status="error",
            message=message,
        )

    def mark_paused(self, message: str) -> None:
        self._update(
            process_alive_at=utc_now_iso(),
            status="paused",
            message=message,
        )

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_sec):
            self._update(process_alive_at=utc_now_iso())

    def _update(self, **values: Any) -> None:
        with self._lock:
            self._payload.update(values)
            self._write_unlocked()

    def _write(self) -> None:
        with self._lock:
            self._write_unlocked()

    def _write_unlocked(self) -> None:
        try:
            write_json_atomic(self.path, self._payload)
        except OSError:
            logger.exception("Не удалось записать heartbeat: %s", self.path)


def read_heartbeat(path: Path) -> dict[str, Any]:
    return read_json_object(path)
