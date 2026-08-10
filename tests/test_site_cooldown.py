from pathlib import Path
import os
import tempfile
import unittest

from site_cooldown import (
    SITE_COOLDOWN_SECONDS,
    activate_site_cooldown,
    format_remaining_time,
    load_site_cooldown,
)


class SiteCooldownTests(unittest.TestCase):
    def test_cooldown_is_persisted_for_exactly_twelve_hours(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "site_cooldown.json"

            created = activate_site_cooldown(
                path,
                "Слишком много попыток",
                now=1_000,
            )
            loaded = load_site_cooldown(path, now=1_001)

            self.assertEqual(created.until_timestamp, 1_000 + SITE_COOLDOWN_SECONDS)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.remaining_seconds(now=1_001), 43_199)
            self.assertEqual(loaded.reason, "Слишком много попыток")
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_expired_cooldown_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "site_cooldown.json"
            activate_site_cooldown(path, "limit", duration_sec=10, now=100)

            self.assertIsNone(load_site_cooldown(path, now=110))
            self.assertFalse(path.exists())

    def test_remaining_time_is_human_readable(self):
        self.assertEqual(format_remaining_time(43_200), "12 ч.")
        self.assertEqual(format_remaining_time(3_660), "1 ч. 1 мин.")
        self.assertEqual(format_remaining_time(45), "45 сек.")


if __name__ == "__main__":
    unittest.main()
