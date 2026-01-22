import json
import os
from typing import Iterable


def load_seen_ids(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # защищаемся от мусора
    return set(str(x) for x in data if x)


def save_seen_ids(path: str, ids: set[str]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f, ensure_ascii=False, indent=2)


def append_jsonl(path: str, rows: Iterable[dict]) -> None:
    """
    Добавляем новые заказы построчно в формате JSONL:
    1 строка = 1 заказ.
    """
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
