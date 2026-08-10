from pathlib import Path
import tempfile
import time
import unittest

from curl_cffi.requests import Session as CurlSession

from browser_identity import (
    identity_path,
    load_browser_identity,
    resolve_http_impersonate,
    rotate_browser_identity,
    stealth_init_script,
)
from client import ProfiClient
from config import Settings


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)


class BrowserIdentityTests(unittest.TestCase):
    def test_identity_persists_and_rotates_as_one_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "chromium-profile"
            kwargs = {
                "profile_path": profile,
                "user_agent": USER_AGENT,
                "impersonate": "chrome136",
                "locale": "ru-RU",
                "timezone_id": "Europe/Moscow",
            }

            first = load_browser_identity(**kwargs)
            restored = load_browser_identity(**kwargs)
            rotated = rotate_browser_identity(profile_path=profile, current=first)
            restored_after_rotation = load_browser_identity(**kwargs)

            self.assertEqual(first, restored)
            self.assertNotEqual(first.identity_id, rotated.identity_id)
            self.assertNotEqual(first.viewport, rotated.viewport)
            self.assertEqual(rotated, restored_after_rotation)
            self.assertTrue(identity_path(profile).exists())

    def test_chrome_alias_resolves_to_user_agent_version(self):
        self.assertEqual(resolve_http_impersonate(USER_AGENT, "chrome"), "chrome136")
        self.assertEqual(
            resolve_http_impersonate(USER_AGENT, "safari184"),
            "safari184",
        )

    def test_stealth_script_contains_consistent_navigator_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            identity = load_browser_identity(
                profile_path=Path(directory),
                user_agent=USER_AGENT,
                impersonate="chrome136",
                locale="ru-RU",
                timezone_id="Europe/Moscow",
            )

        script = stealth_init_script(identity)

        self.assertIn("Navigator.prototype, 'webdriver'", script)
        self.assertIn("Navigator.prototype, 'platform'", script)
        self.assertIn("Navigator.prototype, 'languages'", script)
        self.assertIn("Navigator.prototype, 'userAgentData'", script)
        self.assertIn('"chromeMajor": "136"', script)

    def test_cookie_bridge_omits_expired_cookies_and_returns_set_cookie(self):
        class FakeContext:
            def __init__(self):
                self.added = []

            def cookies(self, urls):
                self.urls = urls
                return [
                    {
                        "name": "active",
                        "value": "one",
                        "domain": ".profi.ru",
                        "path": "/",
                        "secure": True,
                        "expires": time.time() + 600,
                    },
                    {
                        "name": "expired",
                        "value": "two",
                        "domain": ".profi.ru",
                        "path": "/",
                        "secure": True,
                        "expires": time.time() - 1,
                    },
                ]

            def add_cookies(self, cookies):
                self.added = cookies

        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.load(
                env_file=None,
                values={"DATA_DIR": directory},
            )
            client = ProfiClient(object(), settings)
            context = FakeContext()
            client.context = context
            session = CurlSession(impersonate="chrome136", trust_env=False)
            try:
                sent = client._sync_browser_cookies_to_curl(session)
                sent_names = {cookie.name for cookie in session.cookies.jar}
                session.cookies.set(
                    "server_cookie",
                    "value",
                    domain=".profi.ru",
                    path="/",
                    secure=True,
                )
                returned = client._sync_curl_cookies_to_browser(session)
            finally:
                session.close()

        self.assertEqual(sent, 1)
        self.assertEqual(sent_names, {"active"})
        self.assertGreaterEqual(returned, 2)
        names = {cookie["name"] for cookie in context.added}
        self.assertIn("active", names)
        self.assertIn("server_cookie", names)
        self.assertNotIn("expired", names)

    def test_repeat_block_clears_site_data_and_rotates_identity(self):
        class FakePage:
            def evaluate(self, script):
                self.script = script

        class FakeContext:
            def clear_cookies(self):
                self.cookies_cleared = True

            def storage_state(self, path):
                self.storage_path = path

        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.load(
                env_file=None,
                values={"DATA_DIR": directory},
            )
            client = ProfiClient(object(), settings)
            client.page = FakePage()
            client.context = FakeContext()
            previous = client._ensure_identity()

            client.replace_blocked_identity("тест")

            current = client._ensure_identity()

        self.assertNotEqual(previous.identity_id, current.identity_id)
        self.assertIn("indexedDB", client.page.script)
        self.assertTrue(client.context.cookies_cleared)
        self.assertEqual(client.context.storage_path, str(settings.auth_state_path))


if __name__ == "__main__":
    unittest.main()
