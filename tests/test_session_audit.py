from pathlib import Path
import tempfile
import unittest

from config import Settings
from session_audit import run_local_session_audit


class SessionAuditTests(unittest.TestCase):
    def test_local_audit_checks_storage_environment_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings.load(
                env_file=None,
                values={
                    "DATA_DIR": str(root / "data"),
                    "LOG_DIR": str(root / "logs"),
                    "BACKUP_DIR": str(root / "backups"),
                    "PROFI_PROXY": "direct",
                    "PROFI_BROWSER_STEALTH": "true",
                },
            )

            result = run_local_session_audit(settings)

        self.assertTrue(result.passed, result)
        self.assertTrue(result.storage.passed, result.storage.details)
        self.assertEqual(result.environment_diff, {})
        self.assertEqual(result.active_sessions_after_audit, 0)
        self.assertEqual(result.close_errors, {})


if __name__ == "__main__":
    unittest.main()
