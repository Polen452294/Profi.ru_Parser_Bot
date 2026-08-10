from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import math
import time

from storage import read_json_object, write_json_atomic


SITE_COOLDOWN_SECONDS = 12 * 60 * 60


@dataclass(frozen=True, slots=True)
class SiteCooldown:
    until_timestamp: float
    reason: str

    def remaining_seconds(self, *, now: float | None = None) -> int:
        current = time.time() if now is None else now
        return max(0, math.ceil(self.until_timestamp - current))


def activate_site_cooldown(
    path: Path,
    reason: str,
    *,
    duration_sec: int = SITE_COOLDOWN_SECONDS,
    now: float | None = None,
) -> SiteCooldown:
    """Persists a hard Profi.ru pause so it survives service restarts."""
    started_at = time.time() if now is None else now
    cooldown = SiteCooldown(
        until_timestamp=started_at + max(1, duration_sec),
        reason=reason,
    )
    write_json_atomic(
        path,
        {
            "started_at": datetime.fromtimestamp(
                started_at,
                tz=timezone.utc,
            ).isoformat(),
            "until": datetime.fromtimestamp(
                cooldown.until_timestamp,
                tz=timezone.utc,
            ).isoformat(),
            "until_timestamp": cooldown.until_timestamp,
            "reason": reason,
        },
    )
    return cooldown


def load_site_cooldown(
    path: Path,
    *,
    now: float | None = None,
) -> SiteCooldown | None:
    payload = read_json_object(path)
    if not payload:
        return None
    try:
        until_timestamp = float(payload["until_timestamp"])
    except (KeyError, TypeError, ValueError):
        clear_site_cooldown(path)
        return None

    cooldown = SiteCooldown(
        until_timestamp=until_timestamp,
        reason=str(payload.get("reason") or "Profi.ru ограничил повторный вход"),
    )
    if cooldown.remaining_seconds(now=now) <= 0:
        clear_site_cooldown(path)
        return None
    return cooldown


def clear_site_cooldown(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def format_remaining_time(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 60 * 60)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} ч.")
    if minutes:
        parts.append(f"{minutes} мин.")
    if not parts or (not hours and seconds):
        parts.append(f"{seconds} сек.")
    return " ".join(parts)
