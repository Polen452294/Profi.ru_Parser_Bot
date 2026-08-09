from pathlib import Path
import tempfile
import unittest

from config import ConfigurationError, PROJECT_DIR, Settings


class SettingsTests(unittest.TestCase):
    def test_defaults_use_project_local_data_directories(self):
        settings = Settings.load(env_file=None, values={})

        self.assertEqual(settings.project_dir, PROJECT_DIR)
        self.assertEqual(settings.data_dir, PROJECT_DIR / "data")
        self.assertEqual(settings.log_dir, PROJECT_DIR / "logs")
        self.assertEqual(settings.orders_path, PROJECT_DIR / "data" / "new_orders.jsonl")
        self.assertTrue(settings.headless)

    def test_default_sms_code_selector_uses_exact_pin_test_id(self):
        settings = Settings.load(env_file=None, values={})

        self.assertEqual(
            settings.profi_otp_selector,
            '[data-testid="auth_pin_input"]',
        )

    def test_relative_custom_paths_are_resolved_from_project(self):
        settings = Settings.load(
            env_file=None,
            values={"DATA_DIR": "runtime", "LOG_DIR": "runtime_logs"},
        )

        self.assertEqual(settings.data_dir, (PROJECT_DIR / "runtime").resolve())
        self.assertEqual(settings.log_dir, (PROJECT_DIR / "runtime_logs").resolve())

    def test_boolean_values_are_user_friendly(self):
        enabled = Settings.load(env_file=None, values={"HEADLESS": "да"})
        disabled = Settings.load(env_file=None, values={"HEADLESS": "no"})

        self.assertTrue(enabled.headless)
        self.assertFalse(disabled.headless)

    def test_invalid_boolean_has_clear_error(self):
        with self.assertRaisesRegex(ConfigurationError, "HEADLESS"):
            Settings.load(env_file=None, values={"HEADLESS": "иногда"})

    def test_invalid_number_has_clear_error(self):
        with self.assertRaisesRegex(ConfigurationError, "POLL_BASE_SEC"):
            Settings.load(env_file=None, values={"POLL_BASE_SEC": "быстро"})

    def test_profi_proxy_can_be_explicitly_shared_with_playwright(self):
        settings = Settings.load(
            env_file=None,
            values={
                "TELEGRAM_PROXY": "socks5://127.0.0.1:10808",
                "PROFI_PROXY": "socks5://127.0.0.1:10808",
            },
        )

        self.assertEqual(
            settings.playwright_proxy,
            {"server": "socks5://127.0.0.1:10808"},
        )

    def test_telegram_proxy_does_not_affect_playwright_by_default(self):
        settings = Settings.load(
            env_file=None,
            values={"TELEGRAM_PROXY": "socks5://127.0.0.1:20808"},
        )

        self.assertEqual(settings.telegram_proxy, "socks5://127.0.0.1:20808")
        self.assertIsNone(settings.playwright_proxy)
        self.assertEqual(
            settings.playwright_launch_options(headless=True),
            {"headless": True, "args": ["--no-proxy-server"]},
        )

    def test_authenticated_proxy_is_converted_for_playwright(self):
        settings = Settings.load(
            env_file=None,
            values={"PROFI_PROXY": "http://user:p%40ss@proxy.local:3128"},
        )

        self.assertEqual(
            settings.playwright_proxy,
            {
                "server": "http://proxy.local:3128",
                "username": "user",
                "password": "p@ss",
            },
        )

    def test_profi_proxy_can_use_direct_connection(self):
        settings = Settings.load(
            env_file=None,
            values={
                "TELEGRAM_PROXY": "socks5://127.0.0.1:10808",
                "PROFI_PROXY": "direct",
            },
        )

        self.assertEqual(settings.telegram_proxy, "socks5://127.0.0.1:10808")
        self.assertIsNone(settings.profi_proxy)
        self.assertIsNone(settings.playwright_proxy)
        self.assertEqual(
            settings.playwright_launch_options(headless=True),
            {"headless": True, "args": ["--no-proxy-server"]},
        )

    def test_telegram_proxy_can_use_local_dns(self):
        settings = Settings.load(
            env_file=None,
            values={
                "TELEGRAM_PROXY": "socks5://127.0.0.1:20808",
                "TELEGRAM_PROXY_RDNS": "false",
            },
        )

        self.assertFalse(settings.telegram_proxy_rdns)

    def test_profi_proxy_can_override_telegram_proxy(self):
        settings = Settings.load(
            env_file=None,
            values={
                "TELEGRAM_PROXY": "socks5://127.0.0.1:10808",
                "PROFI_PROXY": "http://proxy.local:3128",
            },
        )

        self.assertEqual(
            settings.playwright_proxy,
            {"server": "http://proxy.local:3128"},
        )

    def test_profi_proxy_pool_keeps_primary_and_removes_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.load(
                env_file=None,
                values={
                    "DATA_DIR": directory,
                    "PROFI_PROXY": "direct",
                    "PROFI_PROXY_POOL": (
                        "socks5://proxy-one.local:1080, "
                        "http://user:pass@proxy-two.local:3128, "
                        "direct, socks5://proxy-one.local:1080"
                    ),
                },
            )

        self.assertEqual(
            settings.profi_proxy_pool,
            (
                None,
                "socks5://proxy-one.local:1080",
                "http://user:pass@proxy-two.local:3128",
            ),
        )
        self.assertTrue(settings.profi_proxy_rotation_enabled)
        self.assertEqual(settings.initial_profi_proxy_index, 0)
        self.assertEqual(
            settings.playwright_launch_options(
                headless=True,
                proxy_url=settings.profi_proxy_pool[2],
                use_primary_proxy=False,
            ),
            {
                "headless": True,
                "proxy": {
                    "server": "http://proxy-two.local:3128",
                    "username": "user",
                    "password": "pass",
                },
            },
        )

    def test_invalid_profi_proxy_pool_entry_is_rejected(self):
        with self.assertRaisesRegex(ConfigurationError, "PROFI_PROXY_POOL"):
            Settings.load(
                env_file=None,
                values={"PROFI_PROXY_POOL": "socks5://missing-port.local"},
            )

    def test_missing_profi_proxy_pool_disables_ip_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.load(
                env_file=None,
                values={
                    "DATA_DIR": directory,
                    "PROFI_PROXY": "socks5://primary.local:1080",
                },
            )

        self.assertEqual(
            settings.profi_proxy_pool,
            ("socks5://primary.local:1080",),
        )
        self.assertFalse(settings.profi_proxy_rotation_enabled)

    def test_profi_proxy_pool_is_loaded_from_private_file(self):
        with tempfile.TemporaryDirectory() as directory:
            pool_path = Path(directory) / "proxies.txt"
            pool_path.write_text(
                "# GProxy export\n"
                "http://user:pass@proxy-one.local:1000\n"
                "\n"
                "socks5://proxy-two.local:1080\n"
                "http://user:pass@proxy-one.local:1000\n",
                encoding="utf-8",
            )

            settings = Settings.load(
                env_file=None,
                values={
                    "DATA_DIR": directory,
                    "PROFI_PROXY": "direct",
                    "PROFI_PROXY_POOL_FILE": str(pool_path),
                    "PROFI_PROXY_START_FROM_POOL": "true",
                },
            )

        self.assertEqual(settings.profi_proxy_pool_path, pool_path)
        self.assertEqual(
            settings.profi_proxy_pool,
            (
                None,
                "http://user:pass@proxy-one.local:1000",
                "socks5://proxy-two.local:1080",
            ),
        )
        self.assertTrue(settings.profi_proxy_rotation_enabled)
        self.assertTrue(settings.profi_proxy_start_from_pool)
        self.assertEqual(settings.initial_profi_proxy_index, 1)

    def test_start_from_pool_falls_back_to_primary_when_pool_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.load(
                env_file=None,
                values={
                    "DATA_DIR": directory,
                    "PROFI_PROXY": "direct",
                    "PROFI_PROXY_START_FROM_POOL": "true",
                },
            )

        self.assertFalse(settings.profi_proxy_rotation_enabled)
        self.assertEqual(settings.initial_profi_proxy_index, 0)

    def test_invalid_proxy_in_pool_file_reports_line_number(self):
        with tempfile.TemporaryDirectory() as directory:
            pool_path = Path(directory) / "proxies.txt"
            pool_path.write_text(
                "http://valid.local:1000\nsocks5://missing-port.local\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ConfigurationError,
                "PROFI_PROXY_POOL_FILE, строка 2",
            ):
                Settings.load(
                    env_file=None,
                    values={"PROFI_PROXY_POOL_FILE": str(pool_path)},
                )

    def test_invalid_proxy_is_rejected_early(self):
        invalid_values = (
            "127.0.0.1:10808",
            "ftp://127.0.0.1:10808",
            "socks5://127.0.0.1",
            "socks5://127.0.0.1:10808/path",
        )
        for proxy in invalid_values:
            with self.subTest(proxy=proxy):
                with self.assertRaisesRegex(ConfigurationError, "TELEGRAM_PROXY"):
                    Settings.load(
                        env_file=None,
                        values={"TELEGRAM_PROXY": proxy},
                    )

    def test_telegram_settings_are_validated_only_when_required(self):
        settings = Settings.load(env_file=None, values={})

        self.assertEqual(settings.validation_errors(require_telegram=False), [])
        errors = settings.validation_errors(require_telegram=True)
        self.assertTrue(any("BOT_TOKEN" in error for error in errors))
        self.assertTrue(any("PROFI_LOGIN" in error for error in errors))

    def test_empty_admin_id_enables_valid_open_mode(self):
        settings = Settings.load(
            env_file=None,
            values={
                "BOT_TOKEN": "123:abc",
                "PROFI_LOGIN": "+79990000000",
            },
        )

        self.assertIsNone(settings.admin_chat_id)
        self.assertEqual(settings.validation_errors(require_telegram=True), [])

    def test_login_is_optional_when_session_recovery_is_disabled(self):
        settings = Settings.load(
            env_file=None,
            values={
                "BOT_TOKEN": "123:abc",
                "ADMIN_CHAT_ID": "123456",
                "SESSION_RECOVERY_ENABLED": "false",
            },
        )

        self.assertEqual(settings.validation_errors(require_telegram=True), [])

    def test_complete_telegram_settings_pass_validation(self):
        settings = Settings.load(
            env_file=None,
            values={
                "BOT_TOKEN": "123:abc",
                "ADMIN_CHAT_ID": "123456",
                "PROFI_LOGIN": "+79990000000",
            },
        )

        self.assertEqual(settings.validation_errors(require_telegram=True), [])

    def test_negative_group_chat_id_is_allowed(self):
        settings = Settings.load(
            env_file=None,
            values={
                "BOT_TOKEN": "123:abc",
                "ADMIN_CHAT_ID": "-100123456",
                "PROFI_LOGIN": "+79990000000",
            },
        )

        self.assertEqual(settings.admin_chat_id, -100123456)
        self.assertEqual(settings.validation_errors(require_telegram=True), [])


if __name__ == "__main__":
    unittest.main()
