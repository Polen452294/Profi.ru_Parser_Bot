from asyncio.log import logger
import os
from datetime import datetime
from playwright.sync_api import TimeoutError as PWTimeoutError


class ProfiClient:
    def __init__(self, playwright, settings):
        self.p = playwright
        self.s = settings
        self.browser = None
        self.context = None
        self.page = None

    def __enter__(self):
        # Оставь свои параметры (proxy/user_agent/headless) если они есть в Settings.
        self.browser = self.p.chromium.launch(headless=getattr(self.s, "headless", False))

        storage_state = getattr(self.s, "auth_state_path", None) or getattr(self.s, "storage_state_path", None)
        if storage_state:
            self.context = self.browser.new_context(storage_state=storage_state)
            logger.info("Context created. storage_state=%s", getattr(self.s, "auth_state_path", None))

        else:
            self.context = self.browser.new_context()
            logger.info("Context created. storage_state=%s", getattr(self.s, "auth_state_path", None))


        self.page = self.context.new_page()
        logger.info("Client page: title=%r url=%s", self.page.title(), self.page.url)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.context:
                self.context.close()
        finally:
            if self.browser:
                self.browser.close()

    def open_board(self):
        self.page.goto(getattr(self.s, "page_url", "https://profi.ru/backoffice/"), wait_until="domcontentloaded")

    def soft_refresh(self):
        self.page.reload(wait_until="domcontentloaded")

    def cards_locator(self):
        return self.page.locator(self.s.card_selector)

    def wait_cards(self) -> bool:
        """
        НЕ падаем по таймауту.
        Возвращаем True если карточки появились в DOM, иначе False.
        """
        try:
            self.page.wait_for_selector(
                self.s.card_selector,
                timeout=self.s.selector_timeout_ms,
                state="attached",  # важно: не visible
            )
            return True
        except PWTimeoutError:
            self.save_debug(prefix="no_cards")
            return False

    def save_debug(self, prefix: str = "debug"):
        debug_dir = getattr(self.s, "debug_dir", "logs/debug")
        try:
            os.makedirs(debug_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            png_path = os.path.join(debug_dir, f"{prefix}_{ts}.png")
            html_path = os.path.join(debug_dir, f"{prefix}_{ts}.html")

            try:
                self.page.screenshot(path=png_path, full_page=True)
            except Exception:
                pass

            try:
                html = self.page.content()
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(html)
            except Exception:
                pass
        except Exception:
            # debug не должен ломать основной процесс
            return
