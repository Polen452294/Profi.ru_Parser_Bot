from playwright.sync_api import Playwright, TimeoutError as PWTimeoutError

from config import Settings


class ProfiClient:
    """
    Обёртка над Playwright: открывает страницу, ждёт карточки,
    возвращает локатор всех карточек.
    """
    def __init__(self, p: Playwright, s: Settings):
        self.p = p
        self.s = s
        self.browser = None
        self.page = None

    def __enter__(self):
        self.browser = self.p.chromium.launch(headless=self.s.headless)
        context = self.browser.new_context(storage_state=self.s.state_path)
        self.page = context.new_page()

        self.page.set_default_navigation_timeout(self.s.selector_timeout_ms)
        self.page.set_default_timeout(self.s.selector_timeout_ms)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.browser:
            self.browser.close()

    def open_board(self):
        assert self.page is not None
        self.page.goto(self.s.page_url, wait_until="domcontentloaded", timeout=self.s.selector_timeout_ms)

    def wait_cards(self):
        assert self.page is not None
        try:
            self.page.wait_for_selector(self.s.card_selector, timeout=self.s.selector_timeout_ms)
        except PWTimeoutError as e:
            raise RuntimeError(
                "Не нашёл карточки order-snippet. Проверь page_url или авторизацию (storage_state.json)."
            ) from e

    def cards_locator(self):
        assert self.page is not None
        return self.page.locator(self.s.card_selector)
