import asyncio
from pathlib import Path
import tempfile
import unittest

from aiogram.enums import ChatType

from audience import TelegramAudience
from config import Settings


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))


class AudienceTests(unittest.TestCase):
    def test_open_mode_accepts_any_private_chat_and_persists_it(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.load(
                env_file=None,
                values={
                    "DATA_DIR": directory,
                    "BOT_TOKEN": "123:abc",
                    "PROFI_LOGIN": "+79990000000",
                },
            )
            audience = TelegramAudience(settings)

            self.assertTrue(audience.open_mode)
            self.assertTrue(audience.is_allowed(42, ChatType.PRIVATE))
            self.assertTrue(audience.is_allowed(99, "private"))
            self.assertFalse(audience.is_allowed(-1001, ChatType.GROUP))
            self.assertTrue(audience.register(42))

            restored = TelegramAudience(settings)
            self.assertEqual(restored.recipients, (42,))
            self.assertEqual(
                settings.telegram_chats_path,
                Path(directory).resolve() / "telegram_chats.json",
            )

    def test_admin_mode_rejects_every_other_chat(self):
        settings = Settings.load(
            env_file=None,
            values={
                "BOT_TOKEN": "123:abc",
                "ADMIN_CHAT_ID": "42",
                "PROFI_LOGIN": "+79990000000",
            },
        )
        audience = TelegramAudience(settings)

        self.assertFalse(audience.open_mode)
        self.assertTrue(audience.is_allowed(42, ChatType.PRIVATE))
        self.assertFalse(audience.is_allowed(99, ChatType.PRIVATE))

    def test_notifications_are_broadcast_to_open_mode_subscribers(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                settings = Settings.load(
                    env_file=None,
                    values={
                        "DATA_DIR": directory,
                        "BOT_TOKEN": "123:abc",
                        "PROFI_LOGIN": "+79990000000",
                    },
                )
                audience = TelegramAudience(settings)
                audience.register(42)
                audience.register(99)
                bot = FakeBot()

                delivered = await audience.send(bot, "test")

                self.assertEqual(delivered, 2)
                self.assertEqual(
                    [chat_id for chat_id, _, _ in bot.messages],
                    [42, 99],
                )

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
