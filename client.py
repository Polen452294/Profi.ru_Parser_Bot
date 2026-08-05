from __future__ import annotations

from contextlib import suppress
from datetime import datetime
import logging
from pathlib import Path

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
)

from config import Settings


logger = logging.getLogger("parser.client")


class BrowserUnavailableError(RuntimeError):
    pass


class SiteResponseError(RuntimeError):
    def __init__(self, status: int, retry_after: int | None = None):
        self.status = status
        self.retry_after = retry_after
        self.screenshot_path: Path | None = None
        super().__init__(f"Profi.ru вернул HTTP {status}")


CHALLENGE_SELECTORS = (
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    'iframe[src*="challenges.cloudflare.com"]',
    '[id*="captcha" i]',
    '[class*="captcha" i]',
    'input[name*="captcha" i]',
)

CHALLENGE_TEXT_MARKERS = (
    "подтвердите, что вы не робот",
    "докажите, что вы не робот",
    "проверка безопасности",
    "доступ временно ограничен",
    "ваш доступ ограничен",
    "verify you are human",
    "checking your browser",
    "unusual traffic",
    "attention required!",
    "just a moment...",
)


class ProfiClient:
    def __init__(self, playwright: Playwright, settings: Settings):
        self.playwright = playwright
        self.settings = settings
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._tracing_active = False

    def start(self) -> "ProfiClient":
        self.close()
        self.browser = self.playwright.chromium.launch(
            **self.settings.playwright_launch_options(headless=self.settings.headless)
        )

        context_options: dict[str, object] = {
            "viewport": {"width": 1440, "height": 900},
        }
        if self.settings.auth_state_path.exists():
            context_options["storage_state"] = str(self.settings.auth_state_path)

        self.context = self.browser.new_context(**context_options)
        if self.settings.trace_on_failure:
            self.context.tracing.start(
                screenshots=True,
                snapshots=True,
                sources=False,
            )
            self._tracing_active = True
        self.page = self.context.new_page()
        logger.info(
            "Браузер запущен. headless=%s, сессия=%s, прокси=%s",
            self.settings.headless,
            self.settings.auth_state_path.exists(),
            "включён" if self.settings.playwright_proxy else "выключен",
        )
        return self

    def close(self) -> None:
        if self.page is not None:
            with suppress(Exception):
                self.page.close()
            self.page = None
        if self.context is not None:
            if self._tracing_active:
                with suppress(Exception):
                    self.context.tracing.stop()
                self._tracing_active = False
            with suppress(Exception):
                self.context.close()
            self.context = None
        if self.browser is not None:
            with suppress(Exception):
                self.browser.close()
            self.browser = None

    def __enter__(self) -> "ProfiClient":
        return self.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _page(self) -> Page:
        if self.page is None:
            raise BrowserUnavailableError("Страница браузера ещё не открыта")
        return self.page

    def open_board(self) -> None:
        response = self._page().goto(
            self.settings.page_url,
            wait_until="domcontentloaded",
            timeout=self.settings.page_timeout_ms,
        )
        self._check_response(response)

    def soft_refresh(self) -> None:
        try:
            response = self._page().reload(
                wait_until="domcontentloaded",
                timeout=self.settings.page_timeout_ms,
            )
            self._check_response(response)
        except PlaywrightError as exc:
            message = str(exc).lower()
            if self._is_closed_error(message):
                raise BrowserUnavailableError("Браузер или вкладка закрылись") from exc
            if self._is_network_error(message):
                self.open_board()
                return
            raise

    def cards_locator(self):
        return self._page().locator(self.settings.card_selector)

    def wait_cards(self) -> bool:
        try:
            self._page().wait_for_selector(
                self.settings.card_selector,
                timeout=self.settings.selector_timeout_ms,
                state="attached",
            )
            return True
        except PlaywrightTimeoutError:
            self.save_debug("no_cards")
            return False
        except PlaywrightError as exc:
            if self._is_closed_error(str(exc).lower()):
                raise BrowserUnavailableError("Браузер или вкладка закрылись") from exc
            raise

    def detect_access_challenge(self) -> str | None:
        page = self._page()
        url = page.url.lower()
        title = page.title().lower()

        if "/captcha" in url or "/challenge" in url:
            return f"Обнаружена challenge-страница: {page.url}"

        for marker in CHALLENGE_TEXT_MARKERS:
            if marker in title:
                return f"Заголовок страницы содержит признак блокировки: {marker}"

        for selector in CHALLENGE_SELECTORS:
            try:
                locator = page.locator(selector)
                if locator.count() and locator.first.is_visible():
                    return f"Обнаружен видимый элемент CAPTCHA: {selector}"
            except PlaywrightError:
                continue

        try:
            body_text = page.locator("body").inner_text(timeout=3_000).lower()
        except PlaywrightError:
            return None
        for marker in CHALLENGE_TEXT_MARKERS:
            if marker in body_text:
                return f"Страница содержит признак блокировки: {marker}"
        return None

    def save_debug(self, prefix: str = "debug") -> tuple[Path, Path, Path | None]:
        self.settings.debug_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        screenshot_path = self.settings.debug_dir / f"{prefix}_{timestamp}.png"
        html_path = self.settings.debug_dir / f"{prefix}_{timestamp}.html"
        trace_path = (
            self.settings.debug_dir / f"{prefix}_{timestamp}.trace.zip"
            if self.settings.trace_on_failure
            else None
        )

        try:
            page = self._page()
            page.screenshot(path=str(screenshot_path), full_page=True)
            html_path.write_text(page.content(), encoding="utf-8")
            screenshot_path.chmod(0o600)
            html_path.chmod(0o600)
            if trace_path is not None and self.context is not None:
                if self._tracing_active:
                    self.context.tracing.stop(path=str(trace_path))
                    self._tracing_active = False
                    trace_path.chmod(0o600)
                self.context.tracing.start(
                    screenshots=True,
                    snapshots=True,
                    sources=False,
                )
                self._tracing_active = True
            logger.warning("Диагностика страницы сохранена в %s", self.settings.debug_dir)
        except Exception:
            logger.exception("Не удалось сохранить диагностику страницы")
        return screenshot_path, html_path, trace_path

    @staticmethod
    def _is_closed_error(message: str) -> bool:
        markers = (
            "page crashed",
            "target page, context or browser has been closed",
            "browser has been closed",
            "context has been closed",
            "page has been closed",
        )
        return any(marker in message for marker in markers)

    @staticmethod
    def _is_network_error(message: str) -> bool:
        markers = (
            "err_name_not_resolved",
            "err_internet_disconnected",
            "net::err",
        )
        return any(marker in message for marker in markers)

    @staticmethod
    def _check_response(response) -> None:
        if response is None:
            return
        status = response.status
        if status not in {401, 403, 429} and status < 500:
            return

        retry_after = None
        raw_retry_after = response.headers.get("retry-after")
        if raw_retry_after:
            try:
                retry_after = max(0, int(raw_retry_after))
            except ValueError:
                pass
        raise SiteResponseError(status, retry_after)
