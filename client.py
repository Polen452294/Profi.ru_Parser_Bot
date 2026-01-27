from __future__ import annotations

import os
from datetime import datetime
from playwright.sync_api import TimeoutError as PWTimeoutError


class ProfiClient:
    """
    Обёртка над Playwright Page для работы с доской заказов.
    """

    def __init__(self, playwright, settings):
        self.p = playwright
        self.s = settings
        self.browser = None
        self.context = None
        self.page = None

    def __enter__(self) -> "ProfiClient":
        # ВАЖНО: здесь оставь свою текущую логику запуска (headless/прокси/user_agent/storage_state и т.д.)
        self.browser = self.p.chromium.launch(headless=self.s.headless)
        self.context = self.browser.new_context(
            storage_state=self.s.auth_state_path if getattr(self.s, "auth_state_path", None) else None
        )
        self.page = self.context.new_page()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.context:
                self.context.close()
        finally:
            if self.browser:
                self.browser.close()

    # ---------- Навигация ----------

    def open_board(self):
        self.page.goto(self.s.page_url, wait_until="domcontentloaded")

    def soft_refresh(self):
        # мягкий рефреш — можно оставить как есть, если у тебя уже реализовано по-другому
        self.page.reload(wait_until="domcontentloaded")

    # ---------- Карточки ----------

    def cards_locator(self):
        return self.page.locator(self.s.card_selector)

    def wait_cards(self) -> bool:
        """
        Ждём появления карточек.
        НЕ бросаем исключение при таймауте — возвращаем False и сохраняем debug.
        """
        try:
            # Важно: attached куда стабильнее, чем visible (оверлеи/ленивая загрузка часто ломают visible)
            self.page.wait_for_selector(
                self.s.card_selector,
                timeout=self.s.selector_timeout_ms,
                state="attached",
            )
            return True

        except PWTimeoutError:
            # Сохраняем дебаг и просто даём main.py шанс восстановиться
            self.save_debug(prefix="no_cards")
            return False

    # ---------- Debug ----------

    def save_debug(self, prefix: str = "debug"):
        try:
            os.makedirs(self.s.debug_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            png_path = os.path.join(self.s.debug_dir, f"{prefix}_{ts}.png")
            html_path = os.path.join(self.s.debug_dir, f"{prefix}_{ts}.html")

            # screenshot
            try:
                self.page.screenshot(path=png_path, full_page=True)
            except Exception:
                pass

            # html
            try:
                html = self.page.content()
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(html)
            except Exception:
                pass

        except Exception:
            # debug не должен ломать основной процесс
            return
