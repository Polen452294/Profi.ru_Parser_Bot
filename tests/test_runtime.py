from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from client import ProfiClient, SiteResponseError
from config import Settings
from main import (
    AccessChallengeError,
    _raise_login_retry_cooldown,
    _restart_after_ip_limit,
    _restart_client,
    _select_initial_proxy_index,
    failure_backoff_seconds,
)
from run_all import read_order_batch
from site_cooldown import load_site_cooldown
from tg_formatter import MAX_DESCRIPTION_LENGTH, format_order


class RuntimeTests(unittest.TestCase):
    def test_proxy_restart_rotates_identity_in_the_same_operation(self):
        class FakePage:
            url = "https://profi.ru/backoffice/n.php"

            def title(self):
                return "Заказы"

        class FakeClient:
            proxy_index = 0
            page = FakePage()

            def switch_proxy_and_identity(self, proxy_index):
                self.switched_to = proxy_index
                self.proxy_index = proxy_index

            def open_board(self):
                self.board_opened = True

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.load(
                env_file=None,
                values={
                    "DATA_DIR": directory,
                    "PROFI_PROXY": "http://primary.local:3128",
                    "PROFI_PROXY_POOL": "http://backup.local:3128",
                },
            )
            client = FakeClient()
            result = _restart_client(
                client,
                object(),
                settings,
                "смена IP",
                proxy_index=1,
            )

        self.assertIs(result, client)
        self.assertEqual(client.switched_to, 1)
        self.assertTrue(client.board_opened)
        self.assertFalse(hasattr(client, "closed"))

    def test_random_start_selects_one_proxy_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.load(
                env_file=None,
                values={
                    "DATA_DIR": directory,
                    "PROFI_PROXY": "direct",
                    "PROFI_PROXY_POOL": (
                        "http://proxy-one.local:1000,"
                        "http://proxy-two.local:1000"
                    ),
                    "PROFI_PROXY_RANDOM_ON_START": "true",
                },
            )

            with patch("main.random.choice", return_value=2) as choice:
                selected = _select_initial_proxy_index(settings)

        self.assertEqual(selected, 2)
        choice.assert_called_once_with((1, 2))

    def test_twelve_hour_page_stops_parser_and_persists_pause(self):
        class FakeClient:
            def save_debug(self, prefix):
                self.prefix = prefix
                screenshot = settings.debug_dir / f"{prefix}_test.png"
                screenshot.parent.mkdir(parents=True, exist_ok=True)
                screenshot.write_bytes(b"png")
                return screenshot, screenshot.with_suffix(".html"), None

        class FakeHealth:
            def access_challenge(self, message, screenshot):
                self.message = message
                self.screenshot = screenshot

        class FakeHeartbeat:
            def mark_paused(self, message):
                self.message = message

        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.load(
                env_file=None,
                values={
                    "DATA_DIR": str(Path(directory) / "data"),
                    "LOG_DIR": str(Path(directory) / "logs"),
                },
            )
            client = FakeClient()
            health = FakeHealth()
            heartbeat = FakeHeartbeat()

            with self.assertRaises(AccessChallengeError):
                _raise_login_retry_cooldown(
                    client,
                    settings,
                    health,
                    heartbeat,
                    "Можно будет повторить через 12 часов",
                )

            cooldown = load_site_cooldown(settings.site_cooldown_path)

        self.assertEqual(client.prefix, "login_retry_cooldown")
        self.assertIsNotNone(cooldown)
        self.assertGreaterEqual(cooldown.remaining_seconds(), 43_199)
        self.assertIn("обязательную паузу", health.message)
        self.assertEqual(heartbeat.message, health.message)

    def test_repeat_ip_limit_replaces_identity_before_proxy_restart(self):
        class FakeClient:
            def save_debug(self, prefix):
                self.debug_prefix = prefix

            def replace_blocked_identity(self, reason):
                self.identity_reason = reason

        class FakeHeartbeat:
            def mark_failure(self, reason):
                self.reason = reason

        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.load(
                env_file=None,
                values={
                    "DATA_DIR": directory,
                    "PROFI_PROXY_POOL": "http://proxy.local:3128",
                },
            )
            client = FakeClient()
            heartbeat = FakeHeartbeat()
            replacement = object()
            with (
                patch("main.time.sleep"),
                patch("main._restart_client", return_value=replacement) as restart,
            ):
                result = _restart_after_ip_limit(
                    client,
                    object(),
                    settings,
                    heartbeat,
                    "повторная блокировка",
                    proxy_index=1,
                    reset_identity=True,
                )

        self.assertIs(result, replacement)
        self.assertEqual(client.debug_prefix, "ip_rotation_limit")
        self.assertEqual(client.identity_reason, "повторная блокировка")
        self.assertEqual(heartbeat.reason, "повторная блокировка")
        self.assertEqual(restart.call_args.kwargs["proxy_index"], 1)

    def test_twelve_hour_message_is_detected_for_ip_rotation(self):
        class FakeLocator:
            def inner_text(self, timeout):
                return "  Можно будет\nповторить   через 12 часов  "

        class FakePage:
            def locator(self, selector):
                self.selector = selector
                return FakeLocator()

        settings = Settings.load(env_file=None, values={})
        client = ProfiClient(object(), settings)
        client.page = FakePage()

        reason = client.detect_ip_rotation_limit()

        self.assertIsNotNone(reason)
        self.assertIn("12 часов", reason)

    def test_main_browser_receives_shared_proxy(self):
        class FakeTracing:
            def start(self, **kwargs):
                return None

            def stop(self, **kwargs):
                return None

        class FakePage:
            def close(self):
                return None

        class FakeContext:
            tracing = FakeTracing()

            def set_extra_http_headers(self, headers):
                self.headers = headers

            def add_init_script(self, **kwargs):
                self.init_script = kwargs["script"]

            def new_page(self):
                return FakePage()

            def close(self):
                return None

        class FakeBrowser:
            def new_context(self, **kwargs):
                return FakeContext()

            def close(self):
                return None

        class FakeChromium:
            def __init__(self):
                self.launch_options = None

            def launch(self, **kwargs):
                self.launch_options = kwargs
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()

        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.load(
                env_file=None,
                values={
                    "DATA_DIR": str(Path(directory) / "data"),
                    "LOG_DIR": str(Path(directory) / "logs"),
                    "TELEGRAM_PROXY": "socks5://127.0.0.1:10808",
                    "PROFI_PROXY": "socks5://127.0.0.1:10808",
                    "PROFI_PROXY_POOL": "http://backup.local:3128",
                    "TRACE_ON_FAILURE": "false",
                },
            )
            playwright = FakePlaywright()

            client = ProfiClient(playwright, settings).start()
            primary_identity = client._ensure_identity()
            primary_launch_options = playwright.chromium.launch_options
            backup_identity = client.switch_proxy_and_identity(1)
            backup_launch_options = playwright.chromium.launch_options
            client.close()

        self.assertTrue(primary_launch_options["headless"])
        self.assertEqual(
            primary_launch_options["proxy"],
            {"server": "socks5://127.0.0.1:10808"},
        )
        self.assertIn(
            "--disable-blink-features=AutomationControlled",
            primary_launch_options["args"],
        )
        self.assertTrue(
            any(arg.startswith("--window-size=") for arg in primary_launch_options["args"])
        )
        self.assertTrue(backup_launch_options["headless"])
        self.assertEqual(
            backup_launch_options["proxy"],
            {"server": "http://backup.local:3128"},
        )
        self.assertEqual(client.proxy_index, 1)
        self.assertNotEqual(primary_identity.identity_id, backup_identity.identity_id)
        self.assertNotEqual(primary_identity.canvas_seed, backup_identity.canvas_seed)
        self.assertNotEqual(primary_identity.audio_seed, backup_identity.audio_seed)
        self.assertNotEqual(
            primary_identity.webgl_renderer,
            backup_identity.webgl_renderer,
        )

    def test_failure_backoff_grows_and_is_capped(self):
        settings = Settings.load(
            env_file=None,
            values={
                "ERROR_BACKOFF_BASE_SEC": "60",
                "ERROR_BACKOFF_MAX_SEC": "900",
            },
        )

        with patch("main.random.uniform", return_value=0):
            self.assertEqual(failure_backoff_seconds(settings, 1), 60)
            self.assertEqual(failure_backoff_seconds(settings, 2), 120)
            self.assertEqual(failure_backoff_seconds(settings, 99), 900)
            self.assertEqual(failure_backoff_seconds(settings, 1, 600), 600)

    def test_restricted_http_response_is_not_silently_ignored(self):
        response = type(
            "Response",
            (),
            {"status": 429, "headers": {"retry-after": "120"}},
        )()

        with self.assertRaises(SiteResponseError) as raised:
            ProfiClient._check_response(response)

        self.assertEqual(raised.exception.status, 429)
        self.assertEqual(raised.exception.retry_after, 120)

    def test_order_batch_tracks_each_line_and_skips_bad_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "orders.jsonl"
            path.write_text(
                '{"order_id": "1"}\nnot-json\n[1, 2]\n{"order_id": "2"}\n',
                encoding="utf-8",
            )

            records, normalized_offset = read_order_batch(path, 0)

            self.assertEqual(normalized_offset, 0)
            self.assertEqual([record for record, _ in records], [
                {"order_id": "1"},
                None,
                None,
                {"order_id": "2"},
            ])
            self.assertEqual(records[-1][1], path.stat().st_size)

    def test_order_batch_resets_cursor_after_file_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "orders.jsonl"
            path.write_text('{"order_id": "1"}\n', encoding="utf-8")

            records, normalized_offset = read_order_batch(path, 10_000)

            self.assertEqual(normalized_offset, 0)
            self.assertEqual(records[0][0], {"order_id": "1"})

    def test_formatter_escapes_html_and_builds_absolute_link(self):
        message = format_order(
            {
                "title": "Крыша <срочно>",
                "description": "Нужен мастер & помощник",
                "href": "/orders/42",
                "order_id": "42",
            }
        )

        self.assertIn("Крыша &lt;срочно&gt;", message)
        self.assertIn("мастер &amp; помощник", message)
        self.assertIn('href="https://profi.ru/orders/42"', message)

    def test_formatter_truncates_oversized_description(self):
        message = format_order({"title": "Telegram-бот", "description": "x" * 5_000})

        self.assertIn("x" * MAX_DESCRIPTION_LENGTH, message)
        self.assertNotIn("x" * (MAX_DESCRIPTION_LENGTH + 1), message)


if __name__ == "__main__":
    unittest.main()
