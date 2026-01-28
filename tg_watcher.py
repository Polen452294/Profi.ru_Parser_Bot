import json
from pathlib import Path
import os
import time


STATE_FILE = "tg_state.json"
ORDERS_FILE = "new_orders.jsonl"


def load_offset() -> int:
    if not os.path.exists(STATE_FILE):
        return 0
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return int(json.load(f).get("offset", 0))
    except Exception:
        return 0


def save_offset(offset: int):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"offset": offset}, f)


def read_new_orders():
    offset = load_offset()

    if not os.path.exists(ORDERS_FILE):
        return [], offset

    size = os.path.getsize(ORDERS_FILE)
    if size < offset:
        offset = 0

    new_items = []
    with open(ORDERS_FILE, "r", encoding="utf-8") as f:
        f.seek(offset)
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                new_items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        offset = f.tell()

    save_offset(offset)
    return new_items, offset
