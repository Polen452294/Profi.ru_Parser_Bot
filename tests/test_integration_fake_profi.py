from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
from threading import Thread
import tempfile
import unittest

from playwright.sync_api import sync_playwright

from client import ProfiClient
from config import Settings
from session_recovery import LoginRetryLaterError, recreate_profi_session


class FakeProfiHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/captcha"):
            body = """
                <html><title>Проверка безопасности</title>
                <body><div id="captcha-box">Подтвердите, что вы не робот</div></body>
                </html>
            """
        elif self.path.startswith("/recovery-limit"):
            body = """
                <html><title>Вход на Профи.ру</title><body>
                <div>Повторите через 6 часов</div>
                </body></html>
            """
        elif self.path.startswith("/ip-limit"):
            body = """
                <html><title>Заказы</title><body>
                <div>Можно будет повторить через 12 часов</div>
                </body></html>
            """
        elif self.path.startswith("/recovery"):
            body = """
                <html><title>Вход на Профи.ру</title><body>
                <input data-testid="auth_login_input" placeholder="Логин или телефон">
                <button id="mts" onclick="location.href='/mts-id-was-clicked'">
                    Войти через МТС ID
                </button>
                <button id="sms" data-testid="enter_with_sms_btn"
                        onclick="location.href='/mts-id-was-clicked'">
                    Продолжить
                </button>
                <script>
                const loginInput = document.querySelector('[data-testid="auth_login_input"]');
                const smsButton = document.querySelector('[data-testid="enter_with_sms_btn"]');
                loginInput.addEventListener('input', () => {
                    smsButton.textContent = 'Войти с МТС ID';
                    smsButton.onclick = () => location.href = '/mts-id-was-clicked';
                    setTimeout(() => {
                        smsButton.textContent = 'Войти по сим-пушу или СМС';
                        smsButton.onclick = showOtp;
                    }, 250);
                });
                function showOtp() {
                    if (!document.querySelector('[data-testid="auth_login_input"]').value) return;
                    document.body.innerHTML = Array.from({length: 4}, (_, index) =>
                        `<input class="otp-cell" inputmode="numeric" maxlength="1" aria-label="Цифра ${index + 1}">`
                    ).join('');
                    const otpInputs = [...document.querySelectorAll('.otp-cell')];
                    for (const [index, otp] of otpInputs.entries()) {
                      otp.addEventListener('input', () => {
                        if (otp.value && otpInputs[index + 1]) otpInputs[index + 1].focus();
                        if (otpInputs.map((input) => input.value).join('') === '1234') {
                            document.title = 'Заказы';
                            document.body.innerHTML = '<a data-testid="42_order-snippet" href="/orders/42"><h3>Кровельные работы</h3></a>';
                        }
                      });
                    }
                }
                </script>
                </body></html>
            """
        else:
            body = """
                <html><title>Заказы</title><body>
                <a data-testid="fake_order-snippet" href="/orders/42">
                    <h3>Разработка Telegram-бота</h3>
                </a>
                </body></html>
            """
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        return


@unittest.skipUnless(
    os.environ.get("RUN_BROWSER_INTEGRATION") == "1",
    "запускается отдельно через integration_test.sh",
)
class FakeProfiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeProfiHandler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_cards_and_captcha_detection_against_local_site(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings.load(
                env_file=None,
                values={
                    "DATA_DIR": str(root / "data"),
                    "LOG_DIR": str(root / "logs"),
                    "BACKUP_DIR": str(root / "backups"),
                    "PROFI_PAGE_URL": f"{self.base_url}/backoffice/",
                    "HEADLESS": "true",
                    "TRACE_ON_FAILURE": "true",
                    "SELECTOR_TIMEOUT_SEC": "5",
                    "PAGE_TIMEOUT_SEC": "10",
                },
            )
            settings.ensure_directories()

            with sync_playwright() as playwright:
                client = ProfiClient(playwright, settings).start()
                try:
                    client.open_board()
                    self.assertTrue(client.wait_cards())
                    self.assertIsNone(client.detect_access_challenge())
                    navigator_identity = client.page.evaluate(
                        """() => ({
                            webdriver: navigator.webdriver,
                            platform: navigator.platform,
                            languages: navigator.languages,
                            userAgent: navigator.userAgent,
                            clientHintPlatform: navigator.userAgentData?.platform
                        })"""
                    )
                    self.assertIsNone(navigator_identity.get("webdriver"))
                    self.assertEqual(navigator_identity["platform"], "Win32")
                    self.assertEqual(navigator_identity["languages"][0], "ru-RU")
                    self.assertIn("Chrome/136.", navigator_identity["userAgent"])
                    self.assertEqual(
                        navigator_identity["clientHintPlatform"],
                        "Windows",
                    )

                    client.page.goto(f"{self.base_url}/captcha")
                    reason = client.detect_access_challenge()
                    self.assertIsNotNone(reason)
                    self.assertIn("challenge", reason.lower())

                    client.page.goto(f"{self.base_url}/ip-limit")
                    reason = client.detect_ip_rotation_limit()
                    self.assertIsNotNone(reason)
                    self.assertIn("12 часов", reason)

                    screenshot, html, trace = client.save_debug("integration")
                    self.assertTrue(screenshot.exists())
                    self.assertTrue(html.exists())
                    self.assertIsNotNone(trace)
                    self.assertTrue(trace.exists())
                finally:
                    client.close()

    def test_full_sms_recovery_flow_against_local_site(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings.load(
                env_file=None,
                values={
                    "DATA_DIR": str(root / "data"),
                    "LOG_DIR": str(root / "logs"),
                    "BACKUP_DIR": str(root / "backups"),
                    "PROFI_PAGE_URL": f"{self.base_url}/recovery",
                    "PROFI_LOGIN": "+79990000000",
                    "PROFI_CARD_SELECTOR": 'a[data-testid$="_order-snippet"]',
                    "SESSION_RECOVERY_HEADLESS": "true",
                    "PAGE_TIMEOUT_SEC": "10",
                },
            )
            announcements = []

            recreate_profi_session(
                settings,
                lambda: "1234",
                lambda: announcements.append("sms_requested"),
            )

            self.assertEqual(announcements, ["sms_requested"])
            self.assertTrue(settings.auth_state_path.exists())

    def test_login_retry_limit_is_detected_against_local_site(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings.load(
                env_file=None,
                values={
                    "DATA_DIR": str(root / "data"),
                    "LOG_DIR": str(root / "logs"),
                    "BACKUP_DIR": str(root / "backups"),
                    "PROFI_PAGE_URL": f"{self.base_url}/recovery-limit",
                    "PROFI_LOGIN": "+79990000000",
                    "SESSION_RECOVERY_HEADLESS": "true",
                    "PAGE_TIMEOUT_SEC": "10",
                },
            )
            announcements = []

            with self.assertRaises(LoginRetryLaterError) as raised:
                recreate_profi_session(
                    settings,
                    lambda: self.fail("SMS-код не должен запрашиваться"),
                    lambda: announcements.append("sms_requested"),
                )

            self.assertIn("Повторите через 6 часов", str(raised.exception))
            self.assertEqual(announcements, [])
