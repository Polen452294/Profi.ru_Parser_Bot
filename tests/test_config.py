from pathlib import Path
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
