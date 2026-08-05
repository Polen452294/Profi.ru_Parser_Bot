from __future__ import annotations

import json
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any

from instance_lock import InterProcessFileLock

logger = logging.getLogger("parser.storage")


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.chmod(0o600)
    temporary_path.replace(path)


def read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_seen_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning(
            "Список обработанных заявок повреждён; начинаю с пустого списка: %s",
            path,
        )
        return set()

    if isinstance(data, dict):
        return {str(item) for item in data if item}
    if not isinstance(data, list):
        logger.error("Некорректный формат списка обработанных заявок: %s", path)
        return set()
    return {str(item) for item in data if item}


def save_seen_ids(
    path: Path,
    ids: set[str],
    *,
    retention_days: int | None = None,
    max_count: int | None = None,
) -> set[str]:
    now = datetime.now(timezone.utc)
    now_text = now.isoformat()
    existing = read_json_object(path)
    records: dict[str, str] = {
        str(order_id): str(timestamp)
        for order_id, timestamp in existing.items()
        if order_id and isinstance(timestamp, str)
    }
    for order_id in ids:
        records.setdefault(str(order_id), now_text)

    if retention_days is not None:
        cutoff = now - timedelta(days=retention_days)
        retained: dict[str, str] = {}
        for order_id, timestamp in records.items():
            try:
                parsed = datetime.fromisoformat(timestamp)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                parsed = now
            if parsed >= cutoff:
                retained[order_id] = timestamp
        records = retained

    if max_count is not None and len(records) > max_count:
        records = dict(
            sorted(records.items(), key=lambda item: item[1], reverse=True)[:max_count]
        )

    write_json_atomic(path, records)
    return set(records)


def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with InterProcessFileLock(lock_path):
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(obj, ensure_ascii=False) + "\n")
        path.chmod(0o600)


def load_chat_ids(path: Path) -> set[int]:
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Список Telegram-чатов повреждён: %s", path)
        return set()
    if not isinstance(payload, list):
        return set()
    chat_ids: set[int] = set()
    for value in payload:
        try:
            chat_id = int(value)
        except (TypeError, ValueError):
            continue
        if chat_id:
            chat_ids.add(chat_id)
    return chat_ids


def save_chat_ids(path: Path, chat_ids: set[int]) -> None:
    write_json_atomic(path, sorted(chat_ids))


def load_cursor(path: Path) -> int:
    if not path.exists():
        return 0

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return max(0, int(payload.get("offset", 0)))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        logger.warning("Не удалось прочитать позицию Telegram-бота: %s", path)
        return 0


def save_cursor(path: Path, offset: int) -> None:
    write_json_atomic(path, {"offset": max(0, int(offset))})


def read_jsonl_batch(
    path: Path,
    offset: int,
) -> tuple[list[tuple[dict[str, Any] | None, int]], int]:
    """Читает новые JSONL-строки и возвращает позицию после каждой строки."""
    if not path.exists():
        return [], offset

    normalized_offset = offset if path.stat().st_size >= offset else 0
    records: list[tuple[dict[str, Any] | None, int]] = []

    with path.open("r", encoding="utf-8") as file:
        file.seek(normalized_offset)
        while line := file.readline():
            next_offset = file.tell()
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = None
            records.append((payload if isinstance(payload, dict) else None, next_offset))

    return records, normalized_offset


def compact_jsonl_if_consumed(
    path: Path,
    cursor_path: Path,
    offset: int,
    threshold_bytes: int,
) -> int:
    if not path.exists() or path.stat().st_size < threshold_bytes:
        return offset

    lock_path = path.with_name(f".{path.name}.lock")
    with InterProcessFileLock(lock_path):
        current_size = path.stat().st_size if path.exists() else 0
        if offset < current_size:
            return offset
        path.write_text("", encoding="utf-8")
        path.chmod(0o600)
        save_cursor(cursor_path, 0)
        return 0
