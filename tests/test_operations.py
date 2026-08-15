import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch

from config import Settings
from heartbeat import HeartbeatReporter, read_heartbeat
from health_report import build_health_report
from instance_lock import AlreadyRunningError, SingleInstanceLock
from maintenance import create_safe_backup
from runtime_control import ParserPauseControl
from run_all import wait_for_active_site_cooldown
from site_cooldown import activate_site_cooldown


class OperationsTests(unittest.TestCase):
    def test_pause_control_waits_for_resume(self):
        async def scenario():
            control = ParserPauseControl()
            control.pause("captcha")
            waiter = asyncio.create_task(control.wait_for_resume())
            await asyncio.sleep(0)

            self.assertTrue(control.paused)
            self.assertTrue(control.resume())
            await asyncio.wait_for(waiter, timeout=1)
            self.assertFalse(control.paused)

        asyncio.run(scenario())

    def test_timed_pause_cannot_be_resumed_early(self):
        control = ParserPauseControl()
        control.pause_until("12-hour limit", time.time() + 60)

        self.assertTrue(control.paused)
        self.assertGreater(control.remaining_seconds, 0)
        self.assertFalse(control.resume())
        self.assertTrue(control.paused)

    def test_timed_pause_ends_automatically(self):
        async def scenario():
            control = ParserPauseControl()
            control.pause_until("short test limit", time.time() + 0.02)

            await asyncio.wait_for(control.wait_for_resume(), timeout=1)

            self.assertFalse(control.paused)

        asyncio.run(scenario())

    def test_service_clears_persisted_pause_and_resumes_automatically(self):
        async def scenario():
            class FakeAudience:
                has_recipients = False

                def __init__(self):
                    self.messages = []

                async def send(self, bot, text):
                    self.messages.append(text)
                    return 0

                async def send_photo(self, bot, path, caption):
                    self.messages.append(caption)
                    return 0

                async def send_error(self, bot, text):
                    self.messages.append(text)
                    return 0

                async def send_error_photo(self, bot, path, caption):
                    self.messages.append(caption)
                    return 0

            class FakeLog:
                def warning(self, *args):
                    pass

                def info(self, *args):
                    pass

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
                activate_site_cooldown(
                    settings.site_cooldown_path,
                    "12-hour test limit",
                    duration_sec=0.02,
                )
                audience = FakeAudience()
                control = ParserPauseControl()

                waited = await asyncio.wait_for(
                    wait_for_active_site_cooldown(
                        settings,
                        FakeLog(),
                        object(),
                        audience,
                        control,
                    ),
                    timeout=2,
                )

                self.assertTrue(waited)
                self.assertFalse(settings.site_cooldown_path.exists())
                self.assertFalse(control.paused)
                self.assertTrue(any("возобновляю" in text for text in audience.messages))

        asyncio.run(scenario())

    def test_heartbeat_tracks_process_and_last_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "heartbeat.json"
            reporter = HeartbeatReporter(path, interval_sec=1)
            reporter.start()
            reporter.mark_success()
            time.sleep(0.02)
            reporter.stop()

            payload = read_heartbeat(path)
            self.assertEqual(payload["status"], "ok")
            self.assertTrue(payload["process_alive_at"])
            self.assertTrue(payload["last_success_at"])

    def test_second_instance_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "service.lock"
            first = SingleInstanceLock(path)
            second = SingleInstanceLock(path)
            first.acquire()
            try:
                with self.assertRaises(AlreadyRunningError):
                    second.acquire()
            finally:
                first.release()

    def test_safe_backup_excludes_credentials_and_cookies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / ".env"
            env_file.write_text(
                "BOT_TOKEN=secret\n"
                "PROFI_LOGIN=+79990000000\n"
                "TELEGRAM_PROXY=socks5://secret\n"
                "POLL_BASE_SEC=90\n",
                encoding="utf-8",
            )
            settings = Settings.load(
                env_file=None,
                values={
                    "DATA_DIR": str(root / "data"),
                    "LOG_DIR": str(root / "logs"),
                    "BACKUP_DIR": str(root / "backups"),
                },
            )

            backup_path = create_safe_backup(settings, env_file)
            payload = json.loads(backup_path.read_text(encoding="utf-8"))

            safe_env = payload["env_without_secrets"]
            self.assertEqual(safe_env["POLL_BASE_SEC"], "90")
            self.assertNotIn("BOT_TOKEN", safe_env)
            self.assertNotIn("PROFI_LOGIN", safe_env)
            self.assertNotIn("TELEGRAM_PROXY", safe_env)
            self.assertIn(
                "TARGET_KEYWORD_PATTERNS",
                payload["filter_rules_source"],
            )

    def test_health_report_contains_core_components(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings.load(
                env_file=None,
                values={
                    "DATA_DIR": str(root / "data"),
                    "LOG_DIR": str(root / "logs"),
                    "BACKUP_DIR": str(root / "backups"),
                },
            )
            settings.ensure_directories()
            control = ParserPauseControl()

            with patch("health_report.chromium_installed", return_value=True):
                report = build_health_report(
                    settings,
                    SimpleNamespace(in_progress=False),
                    control,
                )

            self.assertIn("Telegram", report)
            self.assertIn("Chromium", report)
            self.assertIn("Cookies", report)
            self.assertIn("Свободно на диске", report)


if __name__ == "__main__":
    unittest.main()
