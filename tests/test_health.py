import json
from pathlib import Path
import tempfile
import unittest

from health import EVENT_SITE_ERROR, EVENT_SITE_RECOVERED, SiteHealthReporter
from telegram_control import _format_system_event


class HealthTests(unittest.TestCase):
    def test_error_is_emitted_once_at_threshold_and_recovery_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            reporter = SiteHealthReporter(path, error_threshold=3)

            reporter.record_failure("Нет карточек")
            reporter.record_failure("Нет карточек")
            self.assertFalse(path.exists())

            reporter.record_failure("Нет карточек")
            reporter.record_failure("Нет карточек")
            reporter.record_success()

            events = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [event["type"] for event in events],
                [EVENT_SITE_ERROR, EVENT_SITE_RECOVERED],
            )
            self.assertEqual(events[0]["details"]["consecutive_errors"], 3)

    def test_success_without_prior_alert_does_not_emit_event(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            reporter = SiteHealthReporter(path, error_threshold=2)

            reporter.record_failure("Разовая ошибка")
            reporter.record_success()

            self.assertFalse(path.exists())

    def test_site_error_notification_is_human_readable(self):
        text = _format_system_event(
            {
                "type": EVENT_SITE_ERROR,
                "message": "Нет карточек",
                "details": {"consecutive_errors": 3},
            }
        )

        self.assertIn("Profi.ru", text)
        self.assertIn("Ошибок подряд: 3", text)


if __name__ == "__main__":
    unittest.main()
