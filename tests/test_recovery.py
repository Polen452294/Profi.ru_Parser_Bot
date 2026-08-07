import asyncio
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from config import Settings
from session_recovery import (
    SessionRecoveryManager,
    _fill_sms_code,
    _open_mts_then_restore,
    _submit_login_form,
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
    def test_mts_popup_is_closed_before_original_page_is_reloaded(self):
        events = []

        class Popup:
            def close(self):
                events.append("popup_close")

        class Control:
            def is_visible(self):
                return True

            def is_enabled(self):
                return True

            def click(self):
                events.append("mts_click")
                page.context.pages.append(Popup())

        class Controls:
            def __init__(self, items):
                self.items = items

            def count(self):
                return len(self.items)

            def nth(self, index):
                return self.items[index]

        class Page:
            def __init__(self):
                self.url = "https://profi.ru/backoffice/a.php"
                self.context = type("Context", (), {"pages": [self]})()

            def get_by_role(self, role, name):
                if role == "button" and name.search("Войти с МТС ID"):
                    return Controls([Control()])
                return Controls([])

            def get_by_text(self, pattern):
                return Controls([])

            def bring_to_front(self):
                events.append("bring_to_front")

            def reload(self, **kwargs):
                events.append("reload")

        page = Page()
        _open_mts_then_restore(
            page,
            selector_timeout_ms=1_000,
            navigation_timeout_ms=1_000,
        )

        self.assertEqual(
            events,
            ["mts_click", "popup_close", "bring_to_front", "reload"],
        )

    def test_same_tab_mts_login_returns_back_before_reload(self):
        events = []

        class Control:
            def is_visible(self):
                return True

            def is_enabled(self):
                return True

            def click(self):
                events.append("mts_click")
                page.url = "https://login.mts.ru/"

        class Controls:
            def __init__(self, items):
                self.items = items

            def count(self):
                return len(self.items)

            def nth(self, index):
                return self.items[index]

        class Page:
            def __init__(self):
                self.url = "https://profi.ru/backoffice/a.php"
                self.context = type("Context", (), {"pages": [self]})()

            def get_by_role(self, role, name):
                if role == "button" and name.search("Войти с МТС ID"):
                    return Controls([Control()])
                return Controls([])

            def get_by_text(self, pattern):
                return Controls([])

            def go_back(self, **kwargs):
                events.append("go_back")
                self.url = "https://profi.ru/backoffice/a.php"

            def bring_to_front(self):
                events.append("bring_to_front")

            def reload(self, **kwargs):
                events.append("reload")

        page = Page()
        _open_mts_then_restore(
            page,
            selector_timeout_ms=1_000,
            navigation_timeout_ms=1_000,
        )

        self.assertEqual(
            events,
            ["mts_click", "go_back", "bring_to_front", "reload"],
        )

    def test_phone_uses_original_direct_fill_method(self):
        events = []

        class LoginInput:
            def fill(self, value):
                events.append(("fill", value))

            def input_value(self):
                raise AssertionError("Старый способ не должен читать значение поля")

            def type(self, value, delay=0):
                raise AssertionError("Старый способ не должен вводить номер посимвольно")

        class LoginButton:
            def click(self):
                events.append("click")

        _submit_login_form(
            (LoginInput(), LoginButton()),
            "+79990000000",
        )

        self.assertEqual(events, [("fill", "+79990000000"), "click"])

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
                if self.kind == "login" and self.page.login_submissions >= 2:
                    return False
                if self.kind == "otp" and self.page.login_submissions < 2:
                    return False
                if self.kind == "otp" and self.page.otp_closed:
                    return False
                return True

            def fill(self, value):
                events.append(("fill", value))

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
                self.page.login_submissions += 1
                events.append(f"login_click_{self.page.login_submissions}")

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
                self.login_submissions = 0
                self.otp_closed = False

            def goto(self, *args, **kwargs):
                events.append("goto")

            def get_by_test_id(self, test_id):
                return Element(self, "login")

            def get_by_text(self, pattern):
                return EmptyInputs()

            def wait_for_selector(self, selector, **kwargs):
                events.append(("wait", selector))

            def locator(self, selector):
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

            def open_mts_then_restore(*args, **kwargs):
                events.append("mts_return_reload")

            with (
                patch(
                    "session_recovery.sync_playwright",
                    return_value=PlaywrightContext(),
                ),
                patch(
                    "session_recovery._open_mts_then_restore",
                    side_effect=open_mts_then_restore,
                ),
            ):
                recreate_profi_session(settings, provide_code, announce)

        phone_fills = [
            index
            for index, event in enumerate(events)
            if event == ("fill", "+79990000000")
        ]
        self.assertEqual(len(phone_fills), 2)
        self.assertLess(phone_fills[0], events.index("login_click_1"))
        self.assertLess(events.index("login_click_1"), events.index("mts_return_reload"))
        self.assertLess(events.index("mts_return_reload"), phone_fills[1])
        self.assertLess(phone_fills[1], events.index("login_click_2"))
        self.assertLess(events.index("login_click_2"), events.index("announce"))
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
