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
    _submit_login_form,
    _visible_login_button_choices,
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
    def test_visible_login_buttons_are_listed_without_clicking_them(self):
        events = []

        class Control:
            def __init__(self, name):
                self.name = name

            def is_visible(self):
                return True

            def is_enabled(self):
                return True

            def inner_text(self):
                return self.name

            def get_attribute(self, name):
                return None

            def click(self):
                events.append(self.name)

        class Controls:
            def __init__(self, items):
                self.items = items

            def count(self):
                return len(self.items)

            def nth(self, index):
                return self.items[index]

        class Page:
            def get_by_role(self, role, name=None):
                if role != "button":
                    return Controls([])
                names = (
                    "Продолжить",
                    "Войти с МТС ID",
                    "Войти по сим-пушу или СМС",
                )
                self.assert_no_name_filter(name)
                return Controls([Control(text) for text in names])

            @staticmethod
            def assert_no_name_filter(name):
                if name is not None:
                    raise AssertionError("Код не должен угадывать кнопку по тексту")

        page = Page()
        choices = _visible_login_button_choices(page)

        self.assertEqual(
            [choice.label for choice in choices],
            ["Войти с МТС ID", "Войти по сим-пушу или СМС"],
        )
        self.assertEqual(events, [])

    def test_button_listing_does_not_enter_other_tabs_or_frames(self):
        class Controls:
            def count(self):
                return 0

        class TrapRoot:
            def get_by_role(self, role, name=None):
                raise AssertionError("Вкладки и iframe МТС нельзя просматривать")

        class Page:
            frames = [TrapRoot()]
            main_frame = None

            def __init__(self):
                self.context = type("Context", (), {"pages": [self, TrapRoot()]})()

            def get_by_role(self, role, name=None):
                return Controls()

        self.assertEqual(_visible_login_button_choices(Page()), [])

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
                if self.kind == "login" and self.page.method_selected:
                    return False
                if self.kind == "otp" and self.page.otp_closed:
                    return False
                return True

            def is_enabled(self):
                return True

            def inner_text(self):
                if self.kind == "sms_method":
                    return "Войти по сим-пушу или СМС"
                if self.kind == "mts_method":
                    return "Войти с МТС ID"
                return ""

            def get_attribute(self, name):
                return None

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
                if self.kind == "sms_method":
                    self.page.method_selected = True
                    events.append("sms_method_click")
                elif self.kind == "mts_method":
                    raise AssertionError("Кнопка МТС ID не должна нажиматься")
                else:
                    events.append("login_click")

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

        class Elements:
            def __init__(self, elements):
                self.elements = elements

            def count(self):
                return len(self.elements)

            def nth(self, index):
                return self.elements[index]

        class Page:
            def __init__(self):
                self.method_selected = False
                self.otp_closed = False

            def goto(self, *args, **kwargs):
                events.append("goto")

            def get_by_test_id(self, test_id):
                return Element(self, "login")

            def get_by_role(self, role, name=None):
                if role == "button" and name is None and not self.method_selected:
                    return Elements(
                        [
                            Element(self, "mts_method"),
                            Element(self, "sms_method"),
                        ]
                    )
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

            def choose_button(labels):
                events.append(("choices", tuple(labels)))
                return labels.index("Войти по сим-пушу или СМС")

            with patch(
                "session_recovery.sync_playwright",
                return_value=PlaywrightContext(),
            ):
                recreate_profi_session(
                    settings,
                    provide_code,
                    announce,
                    choose_button,
                )

        self.assertLess(events.index(("fill", "+79990000000")), events.index("login_click"))
        self.assertIn(
            (
                "choices",
                ("Войти с МТС ID", "Войти по сим-пушу или СМС"),
            ),
            events,
        )
        self.assertLess(events.index("login_click"), events.index("sms_method_click"))
        self.assertLess(events.index("login_click"), events.index("announce"))
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

    def test_manager_accepts_only_a_number_from_login_button_list(self):
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
            manager.awaiting_login_choice = True
            manager.login_button_choices = (
                "Войти с МТС ID",
                "Войти по сим-пушу или СМС",
            )

            accepted, message = await manager.submit_login_choice("3")
            self.assertFalse(accepted)
            self.assertTrue(manager.awaiting_login_choice)
            self.assertIn("от 1 до 2", message)

            accepted, message = await manager.submit_login_choice("2")
            self.assertTrue(accepted)
            self.assertFalse(manager.awaiting_login_choice)
            self.assertIn("Войти по сим-пушу или СМС", message)
            self.assertEqual(manager._login_choice_queue.get_nowait(), 1)

        asyncio.run(scenario())

    def test_manager_announces_numbered_login_buttons(self):
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

            await manager._announce_login_choices(
                ["Войти с МТС ID", "Войти по сим-пушу или СМС"]
            )

            self.assertTrue(manager.awaiting_login_choice)
            self.assertIn("1. Войти с МТС ID", bot.messages[-1][1])
            self.assertIn("2. Войти по сим-пушу или СМС", bot.messages[-1][1])

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
