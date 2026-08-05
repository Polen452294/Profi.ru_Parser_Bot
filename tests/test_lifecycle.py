import asyncio
from pathlib import Path
import tempfile
import unittest

from audience import TelegramAudience
from config import Settings
from lifecycle import notify_service_started, notify_service_stopped
from storage import write_json_atomic
from version import APP_VERSION


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text))


class LifecycleTests(unittest.TestCase):
    def test_update_start_and_stop_notifications(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                settings = Settings.load(
                    env_file=None,
                    values={
                        "DATA_DIR": str(root / "data"),
                        "LOG_DIR": str(root / "logs"),
                        "BACKUP_DIR": str(root / "backups"),
                        "BOT_TOKEN": "123:abc",
                        "ADMIN_CHAT_ID": "42",
                        "PROFI_LOGIN": "+79990000000",
                    },
                )
                settings.ensure_directories()
                write_json_atomic(settings.version_state_path, {"version": "1.0.0"})
                bot = FakeBot()
                audience = TelegramAudience(settings)

                await notify_service_started(settings, bot, audience)
                await notify_service_stopped(bot, audience)

                self.assertIn(f"1.0.0 → {APP_VERSION}", bot.messages[0][1])
                self.assertIn("остановлен", bot.messages[1][1])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
