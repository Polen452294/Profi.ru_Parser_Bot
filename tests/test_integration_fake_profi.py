from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
from threading import Thread
import tempfile
import unittest

from playwright.sync_api import sync_playwright

from client import ProfiClient
from config import Settings


class FakeProfiHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/captcha"):
            body = """
                <html><title>Проверка безопасности</title>
                <body><div id="captcha-box">Подтвердите, что вы не робот</div></body>
                </html>
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

                    client.page.goto(f"{self.base_url}/captcha")
                    reason = client.detect_access_challenge()
                    self.assertIsNotNone(reason)
                    self.assertIn("challenge", reason.lower())

                    screenshot, html, trace = client.save_debug("integration")
                    self.assertTrue(screenshot.exists())
                    self.assertTrue(html.exists())
                    self.assertIsNotNone(trace)
                    self.assertTrue(trace.exists())
                finally:
                    client.close()
