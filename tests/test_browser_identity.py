from pathlib import Path
import tempfile
import time
import unittest

from curl_cffi.requests import Session as CurlSession

from browser_identity import (
    generate_browser_identity,
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
MAC_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5_2) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)
LINUX_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)


class BrowserIdentityTests(unittest.TestCase):
    def test_platform_bundle_is_consistent_with_user_agent(self):
        cases = (
            (
                USER_AGENT,
                "Win32",
                "Windows",
                "x86",
                "10.0.0",
                "Segoe UI",
                ("NVIDIA", "AMD", "Intel"),
                1.0,
            ),
            (
                MAC_USER_AGENT,
                "MacIntel",
                "macOS",
                "x86",
                "13.5.2",
                "Helvetica Neue",
                ("Intel", "AMD"),
                2.0,
            ),
            (
                LINUX_USER_AGENT,
                "Linux x86_64",
                "Linux",
                "x86",
                "",
                "Noto Sans",
                ("Intel", "AMD"),
                1.0,
            ),
        )
        for (
            user_agent,
            platform,
            hint_platform,
            architecture,
            platform_version,
            font,
            renderer_vendors,
            scale,
        ) in cases:
            with self.subTest(platform=hint_platform):
                identity = generate_browser_identity(
                    user_agent=user_agent,
                    impersonate="chrome136",
                    locale="ru-RU",
                    timezone_id="Europe/Moscow",
                )

                self.assertEqual(identity.platform, platform)
                self.assertEqual(identity.client_hint_platform, hint_platform)
                self.assertEqual(identity.client_hint_architecture, architecture)
                self.assertEqual(
                    identity.client_hint_platform_version,
                    platform_version,
                )
                self.assertIn(font, identity.fonts)
                self.assertTrue(
                    any(vendor in identity.webgl_vendor for vendor in renderer_vendors)
                )
                self.assertTrue(
                    any(vendor in identity.webgl_renderer for vendor in renderer_vendors)
                )
                self.assertEqual(identity.device_scale_factor, scale)

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
            self.assertNotEqual(first.canvas_seed, rotated.canvas_seed)
            self.assertNotEqual(first.audio_seed, rotated.audio_seed)
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
        self.assertIn("Navigator.prototype, 'hardwareConcurrency'", script)
        self.assertIn("CanvasRenderingContext2D.prototype.getImageData", script)
        self.assertIn("AudioBuffer.prototype.getChannelData", script)
        self.assertIn("FontFaceSet.prototype.check", script)
        self.assertIn("globalThis.WebGLRenderingContext", script)
        self.assertIn("globalThis.WebGL2RenderingContext", script)
        self.assertIn(identity.webgl_renderer, script)
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

    def test_fresh_identity_can_be_created_on_demand(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.load(
                env_file=None,
                values={"DATA_DIR": directory},
            )
            client = ProfiClient(object(), settings)
            first = client._ensure_identity()

            second = client.create_browser_identity(
                apply_immediately=False,
                clear_site_data=False,
            )
            third = client.create_browser_identity(
                apply_immediately=False,
                clear_site_data=False,
            )
            applied = []
            client.browser = object()
            client._start_with_identity = (
                lambda identity: applied.append(identity) or client
            )
            fourth = client.create_browser_identity(clear_site_data=False)

        self.assertNotEqual(first.identity_id, second.identity_id)
        self.assertNotEqual(second.identity_id, third.identity_id)
        self.assertNotEqual(first.canvas_seed, second.canvas_seed)
        self.assertNotEqual(second.audio_seed, third.audio_seed)
        self.assertNotEqual(first.webgl_renderer, second.webgl_renderer)
        self.assertNotEqual(second.webgl_renderer, third.webgl_renderer)
        self.assertEqual(first.client_hint_platform, third.client_hint_platform)
        self.assertEqual(first.client_hint_architecture, third.client_hint_architecture)
        self.assertEqual(applied, [fourth])

    def test_regular_restart_preserves_auth_and_identity_until_explicit_purge(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.load(
                env_file=None,
                values={"DATA_DIR": directory},
            )
            client = ProfiClient(object(), settings)
            opened = []
            client._start_with_identity = lambda identity: opened.append(identity) or client

            settings.data_dir.mkdir(parents=True, exist_ok=True)
            settings.auth_state_path.write_text("{}", encoding="utf-8")
            cache_dir = settings.profi_browser_profile_path / "Default" / "Cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            (cache_dir / "cache.bin").write_bytes(b"cache")
            (settings.profi_browser_profile_path / "Cookies").write_bytes(b"cookies")
            client.start()

            self.assertTrue(settings.auth_state_path.exists())
            self.assertTrue((cache_dir / "cache.bin").exists())

            settings.auth_state_path.write_text("{}", encoding="utf-8")
            indexed_db = settings.profi_browser_profile_path / "Default" / "IndexedDB"
            indexed_db.mkdir(parents=True, exist_ok=True)
            (indexed_db / "data.leveldb").write_bytes(b"indexed-db")
            client.start()
            client.close()

            self.assertEqual(len(opened), 2)
            self.assertEqual(opened[0], opened[1])
            self.assertIsNotNone(client._identity)
            self.assertTrue(settings.auth_state_path.exists())
            self.assertTrue((indexed_db / "data.leveldb").exists())
            self.assertTrue(identity_path(settings.profi_browser_profile_path).exists())

            client.purge_session_state()

            self.assertIsNone(client._identity)
            self.assertFalse(settings.auth_state_path.exists())
            self.assertEqual(list(settings.profi_browser_profile_path.iterdir()), [])
            self.assertFalse(identity_path(settings.profi_browser_profile_path).exists())

    def test_repeat_block_clears_site_data_and_rotates_identity(self):
        class FakePage:
            def evaluate(self, script):
                self.script = script

        class FakeContext:
            def clear_cookies(self):
                self.cookies_cleared = True

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


if __name__ == "__main__":
    unittest.main()
