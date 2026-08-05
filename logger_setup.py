from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


def setup_logger(
    name: str,
    log_dir: Path,
    level: int = logging.INFO,
    max_bytes: int = 2_000_000,
    backup_count: int = 5,
) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        log_dir / f"{name}.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    (log_dir / f"{name}.log").chmod(0o600)

    error_handler = RotatingFileHandler(
        log_dir / f"{name}.error.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    logger.addHandler(error_handler)
    (log_dir / f"{name}.error.log").chmod(0o600)

    return logger


def log_json(
    logger: logging.Logger,
    prefix: str,
    payload: dict[str, Any],
    level: int = logging.INFO,
) -> None:
    try:
        serialized = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        serialized = "<не удалось преобразовать в JSON>"
    logger.log(level, "%s | %s", prefix, serialized)
