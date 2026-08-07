import asyncio
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from config import Settings
from session_recovery import (
    SessionRecoveryManager,
    _fill_login_input,
    _fill_sms_code,
    normalize_sms_code,
    recreate_profi_session,
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
    def test_phone_uses_original_direct_fill_method(self):
        events = []

        class LoginInput:
            def fill(self, value):
                events.append(("fill", value))

            def input_value(self):
                raise AssertionError("Старый способ не должен читать значение поля")

            def type(self, value, delay=0):
                raise AssertionError("Старый способ не должен вводить номер посимвольно")

        _fill_login_input(LoginInput(), "+79990000000")

        self.assertEqual(events, [("fill", "+79990000000")])

    def test_sms_is_accepted_before_otp_field_becomes_visible(self):
        events = []

        class Element:
            def __init__(self, page=None, kind="input"):
                self.page = page
                self.kind = kind

            def count(self):
                return 1

            def nth(self, index):
                if index != 0:
                    raise IndexError(index)
                return self

            def is_visible(self):
                if self.kind == "login_input":
                    return not self.page.method_selected
                if self.kind == "sms_method":
                    return not self.page.method_selected
                if self.kind == "otp":
                    return self.page.method_selected and not self.page.otp_closed
                return True

            def is_enabled(self):
                return True

            def inner_text(self):
                if self.kind == "sms_method":
                    if not self.page.phone_filled:
                        return "Продолжить"
                    self.page.button_text_reads += 1
                    if self.page.button_text_reads <= 2:
                        return "Войти с МТС ID"
                    return "Войти по сим-пушу или СМС"
                if self.kind == "mts_method":
                    return "Войти с МТС ID"
                return ""

            def get_attribute(self, name):
                return None

            def fill(self, value):
                events.append(("fill", value))
                if self.kind == "login_input":
                    self.page.phone_filled = True

            def input_value(self):
                phone_values = []
                for item in events:
                    if (
                        isinstance(item, tuple)
                        and item[0] == "fill"
                        and item[1].startswith("+")
                    ):
                        phone_values.append(item[1])
                return phone_values[-1] if phone_values else ""

            def click(self):
                if self.kind == "sms_method":
                    if not self.page.phone_filled:
                        raise AssertionError("Номер должен вводиться до клика")
                    if "сим-пушу" not in self.inner_text():
                        raise AssertionError("Нельзя нажимать переход в МТС ID")
                    self.page.method_selected = True
                    events.append("sms_method_click")
                elif self.kind == "mts_method":
                    raise AssertionError("Кнопка МТС ID не должна нажиматься")
                else:
                    raise AssertionError(f"Нельзя нажимать элемент {self.kind}")

            def press(self, key):
                events.append(("press", key))
                if self.kind == "otp" and key == "Enter":
                    self.page.otp_closed = True

            def type(self, value, delay=0):
                events.append(("type", value, delay))

        class Inputs:
            def __init__(self, element=None):
                self.element = element or Element()

            def count(self):
                return 1

            def nth(self, index):
                return self.element

        class Page:
            def __init__(self):
                self.phone_filled = False
                self.button_text_reads = 0
                self.method_selected = False
                self.otp_closed = False

            def goto(self, *args, **kwargs):
                events.append("goto")

            def get_by_test_id(self, test_id):
                if test_id == "auth_login_input":
                    return Element(self, "login_input")
                return EmptyInputs()

            def get_by_role(self, role, name=None):
                raise AssertionError("Кнопки нельзя искать по роли или тексту")

            def wait_for_selector(self, selector, **kwargs):
                events.append(("wait", selector))

            def locator(self, selector):
                if selector == '[data-testid="enter_with_sms_btn"]':
                    return Inputs(Element(self, "sms_method"))
                return Inputs(Element(self, "otp"))

        class EmptyInputs:
            def count(self):
                return 0

            def nth(self, index):
                raise IndexError(index)

        class Context:
            def new_page(self):
                return Page()

            def storage_state(self, path):
                Path(path).write_text("{}", encoding="utf-8")

        class Browser:
            def new_context(self, **kwargs):
                return Context()

            def close(self):
                return None

        class Chromium:
            def launch(self, **kwargs):
                return Browser()

        class Playwright:
            chromium = Chromium()

        class PlaywrightContext:
            def __enter__(self):
                return Playwright()

            def __exit__(self, exc_type, exc, traceback):
                return None

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings.load(
                env_file=None,
                values={
                    "DATA_DIR": str(root / "data"),
                    "LOG_DIR": str(root / "logs"),
                    "BACKUP_DIR": str(root / "backups"),
                    "PROFI_LOGIN": "+79990000000",
                    "PROFI_OTP_SELECTOR": "otp-selector",
                    "PROFI_CARD_SELECTOR": "card-selector",
                },
            )

            def announce():
                events.append("announce")

            def provide_code():
                events.append("code")
                return "8796"

            with patch(
                "session_recovery.sync_playwright",
                return_value=PlaywrightContext(),
            ), patch(
                "session_recovery.time.sleep",
                return_value=None,
            ) as sleep_mock:
                recreate_profi_session(
                    settings,
                    provide_code,
                    announce,
                )

            sleep_mock.assert_any_call(2.0)

        self.assertLess(
            events.index(("fill", "+79990000000")),
            events.index("sms_method_click"),
        )
        self.assertNotIn("mts_method_click", events)
        self.assertNotIn("login_click", events)
        self.assertLess(events.index("sms_method_click"), events.index("announce"))
        self.assertLess(events.index("announce"), events.index("code"))
        self.assertLess(events.index("code"), events.index(("fill", "8796")))

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

    def test_code_is_accepted_during_early_recovery_stage(self):
        async def scenario():
            settings = Settings.load(
                env_file=None,
                values={
                    "BOT_TOKEN": "123:abc",
                    "ADMIN_CHAT_ID": "42",
                    "PROFI_LOGIN": "+79990000000",
                },
            )
            bot = FakeBot()
            manager = SessionRecoveryManager(settings, bot, FakeLog())
            manager._task = asyncio.create_task(asyncio.sleep(10))
            try:
                accepted, message = await manager.submit_code("8796")

                self.assertTrue(accepted)
                self.assertFalse(manager.awaiting_code)
                self.assertIn("Код получен", message)
                await manager._announce_sms_request()
                self.assertFalse(manager.awaiting_code)
                self.assertEqual(bot.messages, [])
                self.assertEqual(manager._code_queue.get_nowait(), "8796")
            finally:
                manager._task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await manager._task

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
