import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from storage import (
    append_jsonl,
    compact_jsonl_if_consumed,
    load_cursor,
    load_chat_ids,
    load_seen_ids,
    save_cursor,
    save_chat_ids,
    save_seen_ids,
)


class StorageTests(unittest.TestCase):
    def test_seen_ids_round_trip_and_atomic_temp_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "seen.json"

            save_seen_ids(path, {"2", "1"})

            self.assertEqual(load_seen_ids(path), {"1", "2"})
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(payload), {"1", "2"})
            self.assertFalse(path.with_name(f".{path.name}.tmp").exists())
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_broken_seen_file_is_treated_as_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seen.json"
            path.write_text("not json", encoding="utf-8")

            with self.assertLogs("parser.storage", level="WARNING"):
                self.assertEqual(load_seen_ids(path), set())

    def test_cursor_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cursor.json"

            save_cursor(path, 125)

            self.assertEqual(load_cursor(path), 125)

    def test_append_jsonl_creates_parent_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data" / "orders.jsonl"

            append_jsonl(path, {"order_id": "42", "title": "Разработка CRM"})

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["order_id"], "42")
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_chat_ids_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telegram_chats.json"

            save_chat_ids(path, {42, 99})

            self.assertEqual(load_chat_ids(path), {42, 99})

    def test_consumed_jsonl_queue_is_compacted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "orders.jsonl"
            cursor_path = Path(directory) / "cursor.json"
            append_jsonl(path, {"order_id": "42", "title": "x" * 100})
            offset = path.stat().st_size
            save_cursor(cursor_path, offset)

            new_offset = compact_jsonl_if_consumed(
                path,
                cursor_path,
                offset,
                threshold_bytes=1,
            )

            self.assertEqual(new_offset, 0)
            self.assertEqual(path.read_text(encoding="utf-8"), "")
            self.assertEqual(load_cursor(cursor_path), 0)


if __name__ == "__main__":
    unittest.main()
