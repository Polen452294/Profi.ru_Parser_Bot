from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app import (
    _proxy_connection_error,
    _telegram_api_connection_error,
    build_parser,
    command_filter,
    command_parser,
    command_run,
)
from config import Settings


class AppTests(unittest.TestCase):
    def test_telegram_proxy_check_allows_slow_connection(self):
        settings = Settings.load(
            env_file=None,
            values={
                "BOT_TOKEN": "123:abc",
                "TELEGRAM_PROXY": "socks5://127.0.0.1:20808",
            },
        )
        session = MagicMock()
        session.close = AsyncMock()
        bot = MagicMock()
        bot.get_me = AsyncMock(return_value=MagicMock())
        bot.session = session

        with (
            patch(
                "aiogram.client.session.aiohttp.AiohttpSession",
                return_value=session,
            ) as session_class,
            patch("aiogram.Bot", return_value=bot),
        ):
            error = asyncio.run(_telegram_api_connection_error(settings))

        self.assertIsNone(error)
        session_class.assert_called_once_with(
            proxy="socks5://127.0.0.1:20808",
            timeout=30,
        )
        session.close.assert_awaited_once()

    def test_unavailable_proxy_is_reported_before_start(self):
        settings = Settings.load(
            env_file=None,
            values={"TELEGRAM_PROXY": "socks5://127.0.0.1:10808"},
        )

        with patch(
            "app.socket.create_connection",
            side_effect=ConnectionRefusedError(111, "Connection refused"),
        ):
            error = _proxy_connection_error(settings)

        self.assertIn("127.0.0.1:10808", error)
        self.assertIn("TELEGRAM_PROXY", error)

    def test_cli_exposes_all_user_commands(self):
        parser = build_parser()

        for command in ("doctor", "run", "parser", "auth", "filter"):
            with self.subTest(command=command):
                arguments = parser.parse_args([command])
                self.assertEqual(arguments.command, command)

    def test_filter_command_explains_positive_match(self):
        output = StringIO()

        with redirect_stdout(output):
            exit_code = command_filter(
                "Нужно разработать Telegram-бота, бюджет 50 000 рублей"
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("ПОДХОДИТ", output.getvalue())
        self.assertIn("бот", output.getvalue().lower())

    def test_filter_command_explains_exclusion(self):
        output = StringIO()

        with redirect_stdout(output):
            exit_code = command_filter(
                "Нужно разработать бота для таргетинга, бюджет 50 000 рублей"
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("НЕ ПОДХОДИТ", output.getvalue())
        self.assertIn("таргет", output.getvalue())

    def test_full_run_can_create_first_session_through_telegram(self):
        settings = SimpleNamespace(auth_state_path=Path("missing-storage-state.json"))
        fake_run = AsyncMock()
        output = StringIO()

        with (
            patch("app._validate", return_value=True),
            patch("app._runtime_preflight", return_value=True),
            patch("run_all.run", fake_run),
            redirect_stdout(output),
        ):
            exit_code = command_run(settings)

        self.assertEqual(exit_code, 0)
        fake_run.assert_awaited_once_with(settings)
        self.assertIn("бот сам запросит SMS-код", output.getvalue())

    def test_parser_only_requires_existing_session(self):
        settings = SimpleNamespace(auth_state_path=Path("missing-storage-state.json"))
        output = StringIO()

        with (
            patch("app._validate", return_value=True),
            patch("app._runtime_preflight", return_value=True),
            redirect_stdout(output),
        ):
            exit_code = command_parser(settings)

        self.assertEqual(exit_code, 2)
        self.assertIn("режим без Telegram", output.getvalue())


if __name__ == "__main__":
    unittest.main()
