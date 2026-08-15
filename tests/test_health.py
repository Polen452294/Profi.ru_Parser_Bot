import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from health import EVENT_SITE_ERROR, EVENT_SITE_RECOVERED, SiteHealthReporter
from config import Settings
from telegram_control import _deliver_system_event, _format_system_event


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

    def test_error_event_contains_screenshot_from_alerting_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            reporter = SiteHealthReporter(path, error_threshold=2)

            reporter.record_failure("Первая ошибка", "ignored.png")
            self.assertTrue(reporter.will_alert_on_next_failure)
            reporter.record_failure("Вторая ошибка", "browser.png")

            event = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(event["details"]["screenshot_path"], "browser.png")
            self.assertFalse(reporter.will_alert_on_next_failure)

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

    def test_system_error_is_delivered_with_debug_screenshot(self):
        async def scenario():
            class FakeAudience:
                def __init__(self):
                    self.calls = []

                async def send_error_photo(self, bot, path, caption):
                    self.calls.append((path, caption))
                    return 1

                async def send_error(self, bot, text):
                    raise AssertionError("Ожидалась отправка со скриншотом")

            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                settings = Settings.load(
                    env_file=None,
                    values={
                        "DATA_DIR": str(root / "data"),
                        "LOG_DIR": str(root / "logs"),
                    },
                )
                settings.ensure_directories()
                screenshot = settings.debug_dir / "site_error.png"
                screenshot.write_bytes(b"png")
                audience = FakeAudience()

                delivered = await _deliver_system_event(
                    settings,
                    object(),
                    audience,
                    {
                        "type": EVENT_SITE_ERROR,
                        "message": "Нет карточек",
                        "details": {
                            "consecutive_errors": 3,
                            "screenshot_path": str(screenshot),
                        },
                    },
                )

                self.assertEqual(delivered, 1)
                self.assertEqual(audience.calls[0][0], str(screenshot.resolve()))

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
