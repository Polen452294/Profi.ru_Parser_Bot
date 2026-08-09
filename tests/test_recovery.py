import asyncio
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from config import Settings
from session_recovery import (
    LoginRetryLaterError,
    SessionRecoveryManager,
    _fill_login_input,
    _find_login_retry_later_text,
    _type_sms_code_digit_by_digit,
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
        self.value = ""
        self.pressed = []
        self.clicks = 0

    def is_visible(self):
        return True

    def fill(self, value):
        raise AssertionError("SMS-код нельзя вставлять через fill()")

    def click(self):
        self.clicks += 1

    def press(self, key):
        self.pressed.append(key)
        if key.isdigit():
            self.value += key


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

    def test_sms_is_requested_only_after_otp_field_becomes_visible(self):
        events = []

        class Element:
            def __init__(self, page=None, kind="input"):
                self.page = page
                self.kind = kind
                self.value = ""

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
                self.value = value
                events.append(("fill", value))
                if self.kind == "login_input":
                    self.page.phone_filled = True

            def input_value(self):
                return self.value

            def click(self):
                if self.kind == "sms_method":
                    if not self.page.phone_filled:
                        raise AssertionError("Номер должен вводиться до клика")
                    if "сим-пушу" not in self.inner_text():
                        raise AssertionError("Нельзя нажимать переход в МТС ID")
                    self.page.method_selected = True
                    events.append("sms_method_click")
                elif self.kind == "otp":
                    events.append("otp_focus")
                elif self.kind == "mts_method":
                    raise AssertionError("Кнопка МТС ID не должна нажиматься")
                else:
                    raise AssertionError(f"Нельзя нажимать элемент {self.kind}")

            def press(self, key):
                events.append(("press", key))
                if self.kind == "otp" and key.isdigit():
                    self.value += key
                    if len(self.value) == 4:
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
            ) as sleep_mock, patch(
                "session_recovery.LOGIN_POST_CLICK_STATUS_CHECK_SEC",
                0,
            ):
                recreate_profi_session(
                    settings,
                    provide_code,
                    announce,
                )

            sleep_mock.assert_any_call(1.0)

        self.assertLess(
            events.index(("fill", "+79990000000")),
            events.index("sms_method_click"),
        )
        self.assertNotIn("mts_method_click", events)
        self.assertNotIn("login_click", events)
        self.assertLess(events.index("sms_method_click"), events.index("announce"))
        self.assertLess(events.index("announce"), events.index("code"))
        self.assertLess(events.index("code"), events.index("otp_focus"))
        self.assertEqual(
            [event for event in events if isinstance(event, tuple) and event[0] == "press"],
            [("press", "8"), ("press", "7"), ("press", "9"), ("press", "6")],
        )
        self.assertNotIn(("fill", "8796"), events)
        self.assertNotIn(("press", "Enter"), events)

    def test_sms_code_normalization(self):
        self.assertEqual(normalize_sms_code("1234"), "1234")
        self.assertEqual(normalize_sms_code(" 1234 "), "1234")
        self.assertIsNone(normalize_sms_code("12 34"))
        self.assertIsNone(normalize_sms_code("123456"))
        self.assertIsNone(normalize_sms_code("12ab"))
        self.assertIsNone(normalize_sms_code("123"))

    def test_login_retry_later_text_is_detected(self):
        class Body:
            def inner_text(self, timeout):
                return "Слишком много попыток. Повторите через 12 часов"

        class Page:
            def locator(self, selector):
                self.assert_body(selector)
                return Body()

            @staticmethod
            def assert_body(selector):
                if selector != "body":
                    raise AssertionError(selector)

        self.assertEqual(
            _find_login_retry_later_text(Page()),
            "Повторите через 12 часов",
        )

    def test_manager_notifies_user_about_login_retry_limit(self):
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

            async def fail_recovery(*args, **kwargs):
                raise LoginRetryLaterError(
                    "Profi.ru ограничил повторный вход: «Повторите через 12 часов»"
                )

            with patch(
                "session_recovery.asyncio.to_thread",
                new=fail_recovery,
            ):
                await manager._run("test")

            notification = bot.messages[-1][1]
            self.assertIn("⏳", notification)
            self.assertIn("Повторите через 12 часов", notification)
            self.assertIn("не стал повторно нажимать", notification)

        asyncio.run(scenario())

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

            accepted, _ = await manager.submit_code("1234")
            self.assertFalse(accepted)

            manager.awaiting_code = True
            accepted, message = await manager.submit_code("1234")
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
            self.assertIn("ровно 4 цифры", message)

        asyncio.run(scenario())

    def test_code_is_rejected_until_bot_announces_ready_field(self):
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

                self.assertFalse(accepted)
                self.assertFalse(manager.awaiting_code)
                self.assertIn("не ожидает SMS-код", message)
                await manager._announce_sms_request()
                self.assertTrue(manager.awaiting_code)
                self.assertEqual(len(bot.messages), 1)
                self.assertTrue(manager._code_queue.empty())
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

    def test_single_sms_input_receives_four_separate_key_presses(self):
        page = FakePage(input_count=1)

        with patch("session_recovery.time.sleep") as sleep_mock:
            _type_sms_code_digit_by_digit(page, "unused", "1234")

        self.assertEqual(page.inputs.items[0].value, "1234")
        self.assertEqual(page.inputs.items[0].pressed, ["1", "2", "3", "4"])
        self.assertEqual(page.inputs.items[0].clicks, 1)
        self.assertEqual(sleep_mock.call_count, 3)
        self.assertNotIn("Enter", page.inputs.items[0].pressed)

    def test_sms_input_rejects_code_that_is_not_exactly_four_digits(self):
        page = FakePage(input_count=1)

        with self.assertRaisesRegex(Exception, "ровно 4 цифры"):
            _type_sms_code_digit_by_digit(page, "unused", "123456")

    def test_segmented_sms_inputs_receive_one_digit_each(self):
        page = FakePage(input_count=4)

        with patch("session_recovery.time.sleep") as sleep_mock:
            _type_sms_code_digit_by_digit(page, "unused", "1234")

        self.assertEqual(
            [item.value for item in page.inputs.items],
            list("1234"),
        )
        self.assertEqual(
            [item.pressed for item in page.inputs.items],
            [["1"], ["2"], ["3"], ["4"]],
        )
        self.assertEqual([item.clicks for item in page.inputs.items], [1, 1, 1, 1])
        self.assertEqual(sleep_mock.call_count, 3)


if __name__ == "__main__":
    unittest.main()
