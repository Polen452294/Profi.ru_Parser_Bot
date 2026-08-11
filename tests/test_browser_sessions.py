from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import tempfile
import unittest

from playwright.sync_api import sync_playwright

from browser_identity import generate_browser_identity
from browser_sessions import (
    DEFAULT_PROFILE_NAME,
    MOSCOW_GEO_PROFILE_NAME,
    BrowserProfile,
    BrowserProfileRegistry,
    BrowserSessionManager,
    BrowserStorageMode,
    audit_storage_isolation,
    build_profile_catalog,
    collect_browser_snapshot,
    diff_browser_snapshots,
)


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)


def make_profile(name: str = "desktop_ru") -> BrowserProfile:
    identity = generate_browser_identity(
        user_agent=USER_AGENT,
        impersonate="chrome136",
        locale="ru-RU",
        timezone_id="Europe/Moscow",
    )
    return BrowserProfile.from_identity(identity, name=name)


class IsolationHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/sw"):
            content_type = "application/javascript"
            body = b"self.addEventListener('fetch', () => {});"
        else:
            content_type = "text/html; charset=utf-8"
            if self.path.startswith("/pwa"):
                body = (
                    b"<!doctype html><script type=module>"
                    b"await navigator.serviceWorker.register('/sw.js');"
                    b"await navigator.serviceWorker.ready;</script>"
                )
            else:
                body = b"<!doctype html><title>isolation</title>"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


class BrowserSessionUnitTests(unittest.TestCase):
    def test_registry_rejects_duplicates_and_unknown_profiles(self):
        profile = make_profile()
        registry = BrowserProfileRegistry((profile,))

        self.assertEqual(registry.names, ("desktop_ru",))
        self.assertEqual(registry.get("desktop_ru"), profile)
        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register(profile)
        with self.assertRaisesRegex(KeyError, "Unknown browser profile"):
            registry.get("missing")

    def test_profile_exposes_permissions_and_geolocation(self):
        identity = generate_browser_identity(
            user_agent=USER_AGENT,
            impersonate="chrome136",
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )
        profile = BrowserProfile.from_identity(
            identity,
            permissions=("geolocation",),
            geolocation={"latitude": 55.7558, "longitude": 37.6173},
        )

        options = profile.context_options()

        self.assertEqual(options["permissions"], ["geolocation"])
        self.assertEqual(options["geolocation"]["latitude"], 55.7558)

    def test_catalog_contains_default_and_geolocated_profiles(self):
        identity = generate_browser_identity(
            user_agent=USER_AGENT,
            impersonate="chrome136",
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )

        catalog = build_profile_catalog(identity)

        self.assertEqual(
            catalog.names,
            (DEFAULT_PROFILE_NAME, MOSCOW_GEO_PROFILE_NAME),
        )
        self.assertEqual(
            catalog.get(MOSCOW_GEO_PROFILE_NAME).permissions,
            ("geolocation",),
        )
        with self.assertRaises(TypeError):
            catalog.get(DEFAULT_PROFILE_NAME).viewport["width"] = 1

    def test_authenticated_mode_requires_and_passes_storage_state(self):
        class Page:
            def close(self):
                return None

        class Context:
            def new_page(self):
                return Page()

            def close(self):
                return None

        class Browser:
            def new_context(self, **kwargs):
                self.options = kwargs
                return Context()

        with tempfile.TemporaryDirectory() as directory:
            auth_state = Path(directory) / "state.json"
            browser = Browser()
            profile = make_profile()
            manager = BrowserSessionManager(
                browser,
                BrowserProfileRegistry((profile,)),
                auth_state_path=auth_state,
            )
            with self.assertRaises(FileNotFoundError):
                manager.create_session(
                    profile.name,
                    storage_mode=BrowserStorageMode.AUTHENTICATED,
                )

            auth_state.write_text("{}", encoding="utf-8")
            session = manager.create_session(
                profile.name,
                storage_mode="authenticated",
            )
            session.close()

        self.assertEqual(browser.options["storage_state"], str(auth_state))

    def test_invalid_storage_mode_is_rejected(self):
        class Browser:
            def new_context(self, **kwargs):
                raise AssertionError("new_context must not be called")

        profile = make_profile()
        manager = BrowserSessionManager(
            Browser(),
            BrowserProfileRegistry((profile,)),
        )

        with self.assertRaisesRegex(ValueError, "Unknown browser storage mode"):
            manager.create_session(profile.name, storage_mode="reused")

    def test_manager_tracks_sessions_and_logs_cleanup_failures(self):
        class Page:
            def close(self):
                raise RuntimeError("page close failed")

        class Context:
            def new_page(self):
                return Page()

            def close(self):
                return None

        class Browser:
            def new_context(self, **kwargs):
                return Context()

        profile = make_profile()
        manager = BrowserSessionManager(
            Browser(),
            BrowserProfileRegistry((profile,)),
        )
        session = manager.create_session(profile.name)

        self.assertEqual(manager.active_session_count, 1)
        self.assertEqual(manager.active_session_ids, (session.session_id,))
        with self.assertLogs("parser.browser_sessions", level="WARNING"):
            errors = manager.close_all()

        self.assertEqual(errors[session.session_id], ("page:RuntimeError",))
        self.assertEqual(manager.active_session_count, 0)
        self.assertTrue(session.closed)

    def test_snapshot_diff_reports_nested_changes(self):
        left = {"sessionId": "A", "navigator": {"language": "ru-RU"}}
        right = {"sessionId": "B", "navigator": {"language": "en-US"}}

        diff = diff_browser_snapshots(left, right)

        self.assertEqual(
            diff,
            {"navigator.language": {"left": "ru-RU", "right": "en-US"}},
        )


class BrowserSessionLeakageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), IsolationHandler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}/"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_real_chromium_contexts_do_not_leak_browser_state(self):
        profile = make_profile()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                manager = BrowserSessionManager(
                    browser,
                    BrowserProfileRegistry((profile,)),
                )
                report = audit_storage_isolation(
                    manager,
                    profile.name,
                    self.base_url,
                    service_worker_url=f"{self.base_url}sw.js",
                )
            finally:
                browser.close()

        self.assertTrue(report.passed, report.details)

    def test_pwa_worker_created_in_new_context_is_not_reported_as_leak(self):
        profile = make_profile()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                manager = BrowserSessionManager(
                    browser,
                    BrowserProfileRegistry((profile,)),
                )
                report = audit_storage_isolation(
                    manager,
                    profile.name,
                    f"{self.base_url}pwa",
                    service_worker_url=f"{self.base_url}sw-audit.js",
                )
            finally:
                browser.close()

        self.assertTrue(report.service_workers, report.details)
        self.assertTrue(report.details["serviceWorkers"])

    def test_two_live_contexts_keep_state_independent(self):
        profile = make_profile()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                manager = BrowserSessionManager(
                    browser,
                    BrowserProfileRegistry((profile,)),
                )
                first = manager.create_session(profile.name)
                second = manager.create_session(profile.name)
                try:
                    first.page.goto(self.base_url)
                    second.page.goto(self.base_url)
                    first.page.evaluate("localStorage.setItem('owner', 'A')")
                    second.page.evaluate("localStorage.setItem('owner', 'B')")

                    self.assertEqual(
                        first.page.evaluate("localStorage.getItem('owner')"),
                        "A",
                    )
                    self.assertEqual(
                        second.page.evaluate("localStorage.getItem('owner')"),
                        "B",
                    )
                    self.assertNotEqual(first.session_id, second.session_id)
                finally:
                    first.close()
                    second.close()
            finally:
                browser.close()

    def test_authenticated_context_restores_saved_state(self):
        profile = make_profile()
        with tempfile.TemporaryDirectory() as directory:
            auth_state = Path(directory) / "state.json"
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    manager = BrowserSessionManager(
                        browser,
                        BrowserProfileRegistry((profile,)),
                        auth_state_path=auth_state,
                    )
                    with manager.session(profile.name) as first:
                        first.page.goto(self.base_url)
                        first.page.evaluate("localStorage.setItem('authenticated', 'yes')")
                        first.context.add_cookies(
                            [{"name": "auth", "value": "yes", "url": self.base_url}]
                        )
                        first.context.storage_state(path=str(auth_state))

                    with manager.session(
                        profile.name,
                        storage_mode=BrowserStorageMode.AUTHENTICATED,
                    ) as restored:
                        restored.page.goto(self.base_url)
                        local_value = restored.page.evaluate(
                            "localStorage.getItem('authenticated')"
                        )
                        cookie_names = {
                            cookie["name"] for cookie in restored.context.cookies()
                        }
                finally:
                    browser.close()

        self.assertEqual(local_value, "yes")
        self.assertIn("auth", cookie_names)

    def test_real_snapshot_contains_environment_and_storage(self):
        profile = make_profile()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                manager = BrowserSessionManager(
                    browser,
                    BrowserProfileRegistry((profile,)),
                )
                with manager.session(profile.name) as session:
                    session.page.goto(self.base_url)
                    snapshot = collect_browser_snapshot(session)
            finally:
                browser.close()

        self.assertEqual(snapshot["profile"], profile.name)
        self.assertEqual(snapshot["navigator"]["language"], "ru-RU")
        self.assertEqual(snapshot["timezone"], "Europe/Moscow")
        self.assertIn("storage", snapshot)
