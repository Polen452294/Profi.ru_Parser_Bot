from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

from dotenv import dotenv_values

from config import DEFAULT_ENV_FILE, Settings
from version import APP_VERSION


SECRET_NAME_MARKERS = ("TOKEN", "PASSWORD", "SECRET", "COOKIE", "LOGIN", "PROXY")


def cleanup_old_files(directory: Path, retention_days: int) -> int:
    if not directory.exists():
        return 0
    cutoff = datetime.now(timezone.utc).timestamp() - retention_days * 86_400
    removed = 0
    for path in directory.iterdir():
        if not path.is_file() or path.stat().st_mtime >= cutoff:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def create_safe_backup(
    settings: Settings,
    env_file: Path = DEFAULT_ENV_FILE,
) -> Path:
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    env_values = dotenv_values(env_file) if env_file.exists() else {}
    safe_env = {
        key: value
        for key, value in env_values.items()
        if value is not None
        and not any(marker in key.upper() for marker in SECRET_NAME_MARKERS)
    }
    filter_path = settings.project_dir / "filters.py"
    filter_rules_source = (
        filter_path.read_text(encoding="utf-8") if filter_path.exists() else None
    )
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "app_version": APP_VERSION,
        "env_without_secrets": safe_env,
        "filter_rules_source": filter_rules_source,
    }
    backup_path = settings.backup_dir / (
        f"safe-backup-{datetime.now(timezone.utc).date().isoformat()}.json"
    )
    backup_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    backup_path.chmod(0o600)
    return backup_path


async def maintenance_loop(settings: Settings, log) -> None:
    while True:
        try:
            debug_removed = cleanup_old_files(
                settings.debug_dir,
                settings.debug_retention_days,
            )
            backup_removed = cleanup_old_files(
                settings.backup_dir,
                settings.backup_retention_days,
            )
            backup_path = create_safe_backup(settings)
            log.info(
                "Обслуживание завершено: удалено debug=%s, backup=%s; копия=%s",
                debug_removed,
                backup_removed,
                backup_path.name,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка автоматического обслуживания")
        await asyncio.sleep(6 * 60 * 60)
