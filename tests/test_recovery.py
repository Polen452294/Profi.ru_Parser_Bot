import asyncio
import time
import unittest

from config import Settings
from session_recovery import (
    SessionRecoveryManager,
    _fill_sms_code,
    normalize_sms_code,
)


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))


class FakeLog:
    def info(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass


class FakeInput:
    def __init__(self):
        self.value = None
        self.pressed = []

    def is_visible(self):
        return True

    def fill(self, value):
        self.value = value

    def press(self, key):
        self.pressed.append(key)


class FakeInputs:
    def __init__(self, count):
        self.items = [FakeInput() for _ in range(count)]

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


class FakePage:
    def __init__(self, input_count):
        self.inputs = FakeInputs(input_count)

    def locator(self, selector):
        return self.inputs


class RecoveryTests(unittest.TestCase):
    def test_sms_code_normalization(self):
        self.assertEqual(normalize_sms_code("12 34-56"), "123456")
        self.assertIsNone(normalize_sms_code("12ab"))
        self.assertIsNone(normalize_sms_code("123"))

    def test_manager_accepts_code_only_when_requested(self):
        async def scenario():
            settings = Settings.load(
                env_file=None,
                values={
                    "BOT_TOKEN": "123:abc",
                    "ADMIN_CHAT_ID": "42",
                    "PROFI_LOGIN": "+79990000000",
                },
            )
            manager = SessionRecoveryManager(settings, FakeBot(), FakeLog())

            accepted, _ = await manager.submit_code("123456")
            self.assertFalse(accepted)

            manager.awaiting_code = True
            accepted, message = await manager.submit_code("123 456")
            self.assertTrue(accepted)
            self.assertIn("Код получен", message)

        asyncio.run(scenario())

    def test_invalid_code_keeps_waiting_state(self):
        async def scenario():
            settings = Settings.load(
                env_file=None,
                values={
                    "BOT_TOKEN": "123:abc",
                    "ADMIN_CHAT_ID": "42",
                    "PROFI_LOGIN": "+79990000000",
                },
            )
            manager = SessionRecoveryManager(settings, FakeBot(), FakeLog())
            manager.awaiting_code = True

            accepted, message = await manager.submit_code("не код")

            self.assertFalse(accepted)
            self.assertTrue(manager.awaiting_code)
            self.assertIn("4 до 8 цифр", message)

        asyncio.run(scenario())

    def test_manual_recovery_has_sms_spam_cooldown(self):
        async def scenario():
            settings = Settings.load(
                env_file=None,
                values={
                    "BOT_TOKEN": "123:abc",
                    "ADMIN_CHAT_ID": "42",
                    "PROFI_LOGIN": "+79990000000",
                    "RECOVERY_COOLDOWN_SEC": "300",
                },
            )
            bot = FakeBot()
            manager = SessionRecoveryManager(settings, bot, FakeLog())
            manager._last_started_at = time.monotonic()

            started = await manager.start("test")

            self.assertFalse(started)
            self.assertIn("ограничен", bot.messages[-1][1])

        asyncio.run(scenario())

    def test_single_sms_input_receives_full_code(self):
        page = FakePage(input_count=1)

        _fill_sms_code(page, "unused", "123456")

        self.assertEqual(page.inputs.items[0].value, "123456")
        self.assertEqual(page.inputs.items[0].pressed, ["Enter"])

    def test_segmented_sms_inputs_receive_one_digit_each(self):
        page = FakePage(input_count=6)

        _fill_sms_code(page, "unused", "123456")

        self.assertEqual(
            [item.value for item in page.inputs.items],
            list("123456"),
        )


if __name__ == "__main__":
    unittest.main()
