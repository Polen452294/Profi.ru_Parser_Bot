from __future__ import annotations

from contextlib import suppress
from datetime import datetime
import logging
from pathlib import Path
import shutil
import time
from urllib.parse import urlsplit

from curl_cffi.requests import Session as CurlSession
from curl_cffi.requests.exceptions import RequestException as CurlRequestError

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
)

from browser_identity import (
    BrowserIdentity,
    generate_browser_identity,
    resolve_http_impersonate,
    stealth_init_script,
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

IP_ROTATION_LIMIT_MARKER = "можно будет повторить через 12 часов"

PROFI_SITE_DATA_CLEAR_SCRIPT = """
async () => {
  localStorage.clear();
  sessionStorage.clear();
  if (window.caches) {
    for (const key of await window.caches.keys()) await window.caches.delete(key);
  }
  if (window.indexedDB?.databases) {
    for (const database of await window.indexedDB.databases()) {
      if (database.name) window.indexedDB.deleteDatabase(database.name);
    }
  }
}
"""


class ProfiClient:
    def __init__(
        self,
        playwright: Playwright,
        settings: Settings,
        *,
        proxy_index: int = 0,
    ):
        self.playwright = playwright
        self.settings = settings
        self.proxy_index = proxy_index % len(settings.profi_proxy_pool)
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._identity: BrowserIdentity | None = None
        self._last_identity: BrowserIdentity | None = None
        self._curl_session: CurlSession | None = None
        self._cookie_bridge_completed = False
        self._tracing_active = False

    def _ensure_identity(self) -> BrowserIdentity:
        if self._identity is None:
            self._identity = self._generate_session_identity()
        return self._identity

    def _generate_session_identity(
        self,
        previous: BrowserIdentity | None = None,
    ) -> BrowserIdentity:
        identity = generate_browser_identity(
            user_agent=self.settings.profi_user_agent,
            impersonate=resolve_http_impersonate(
                self.settings.profi_user_agent,
                self.settings.profi_http_impersonate,
            ),
            locale=self.settings.profi_browser_locale,
            timezone_id=self.settings.profi_browser_timezone,
            previous=previous if previous is not None else self._last_identity,
        )
        self._last_identity = identity
        return identity

    def start(self) -> "ProfiClient":
        self.close()
        identity = self._ensure_identity()
        return self._start_with_identity(identity)

    def _start_with_identity(self, identity: BrowserIdentity) -> "ProfiClient":
        self._identity = identity
        launch_options = self.settings.playwright_launch_options(
            headless=self.settings.headless,
            proxy_url=self.selected_proxy_url,
            use_primary_proxy=False,
        )
        browser_args = list(launch_options.get("args", []))
        browser_args.append(
            f"--window-size={identity.screen_width},{identity.screen_height}"
        )
        if self.settings.profi_browser_stealth:
            browser_args.append("--disable-blink-features=AutomationControlled")
        launch_options["args"] = browser_args
        self.browser = self.playwright.chromium.launch(**launch_options)

        context_options: dict[str, object] = {
            "viewport": identity.viewport,
            "screen": identity.screen,
            "device_scale_factor": identity.device_scale_factor,
            "user_agent": identity.user_agent,
            "locale": identity.locale,
            "timezone_id": identity.timezone_id,
        }
        self.context = self.browser.new_context(**context_options)
        self.context.set_extra_http_headers(identity.http_headers)
        if self.settings.profi_browser_stealth:
            self.context.add_init_script(script=stealth_init_script(identity))
        if self.settings.trace_on_failure:
            self.context.tracing.start(
                screenshots=True,
                snapshots=True,
                sources=False,
            )
            self._tracing_active = True
        self.page = self.context.new_page()
        logger.info(
            "Браузер запущен. headless=%s, сессия=%s, маршрут Profi.ru=%s/%s, "
            "прокси=%s, identity=%s, viewport=%sx%s, impersonate=%s",
            self.settings.headless,
            self.settings.auth_state_path.exists(),
            self.proxy_index + 1,
            len(self.settings.profi_proxy_pool),
            "включён" if self.selected_proxy_url else "выключен",
            identity.identity_id,
            identity.viewport_width,
            identity.viewport_height,
            identity.impersonate,
        )
        return self

    @property
    def selected_proxy_url(self) -> str | None:
        return self.settings.profi_proxy_pool[self.proxy_index]

    @property
    def next_proxy_index(self) -> int:
        return (self.proxy_index + 1) % len(self.settings.profi_proxy_pool)

    def switch_proxy_and_identity(
        self,
        proxy_index: int | None = None,
    ) -> BrowserIdentity:
        """Restart Chromium with a new proxy and a new coherent fingerprint."""
        previous_proxy_index = self.proxy_index
        previous_identity = self._ensure_identity()
        target_proxy_index = (
            self.next_proxy_index if proxy_index is None else proxy_index
        ) % len(self.settings.profi_proxy_pool)
        self.proxy_index = target_proxy_index
        self.start()
        current_identity = self._ensure_identity()
        logger.info(
            "Proxy и browser fingerprint заменены одним перезапуском: "
            "route=%s/%s -> %s/%s, identity=%s -> %s",
            previous_proxy_index + 1,
            len(self.settings.profi_proxy_pool),
            target_proxy_index + 1,
            len(self.settings.profi_proxy_pool),
            previous_identity.identity_id,
            current_identity.identity_id,
        )
        return current_identity

    def close(self) -> None:
        self._clear_active_browser_storage()
        if self._curl_session is not None:
            with suppress(Exception):
                self._curl_session.close()
            self._curl_session = None
        self._cookie_bridge_completed = False
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
        if self._identity is not None:
            self._last_identity = self._identity
            self._identity = None
        self._clear_browser_session_files()

    def _clear_active_browser_storage(self) -> None:
        page = self.page
        context = self.context
        if page is not None:
            with suppress(Exception):
                page.evaluate(PROFI_SITE_DATA_CLEAR_SCRIPT)
        if context is not None:
            with suppress(Exception):
                context.clear_cookies()
            if page is not None:
                with suppress(Exception):
                    cdp = context.new_cdp_session(page)
                    origin = page.evaluate("location.origin")
                    if origin not in {"null", "about:blank"}:
                        cdp.send(
                            "Storage.clearDataForOrigin",
                            {"origin": origin, "storageTypes": "all"},
                        )
                    cdp.send("Network.clearBrowserCookies")
                    cdp.send("Network.clearBrowserCache")
                    cdp.detach()

    def _clear_browser_session_files(self) -> None:
        with suppress(OSError):
            self.settings.auth_state_path.unlink()

        profile_path = self.settings.profi_browser_profile_path.resolve()
        project_path = self.settings.project_dir.resolve()
        data_path = self.settings.data_dir.resolve()
        protected_paths = {
            Path(profile_path.anchor).resolve(),
            project_path,
            data_path,
        }
        if (
            profile_path in protected_paths
            or project_path.is_relative_to(profile_path)
            or data_path.is_relative_to(profile_path)
        ):
            logger.error(
                "Очистка browser profile пропущена: небезопасный путь %s",
                profile_path,
            )
            return
        try:
            if profile_path.exists():
                for child in profile_path.iterdir():
                    try:
                        if child.is_dir() and not child.is_symlink():
                            shutil.rmtree(child)
                        else:
                            child.unlink()
                    except FileNotFoundError:
                        pass
            profile_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "Не удалось полностью очистить browser profile %s: %s",
                profile_path,
                type(exc).__name__,
            )

    def __enter__(self) -> "ProfiClient":
        return self.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _page(self) -> Page:
        if self.page is None:
            raise BrowserUnavailableError("Страница браузера ещё не открыта")
        return self.page

    def open_board(self) -> None:
        self._run_cookie_bridge()
        response = self._page().goto(
            self.settings.page_url,
            wait_until="domcontentloaded",
            timeout=self.settings.page_timeout_ms,
        )
        self._check_response(response)

    def _get_curl_session(self) -> CurlSession:
        if self._curl_session is not None:
            return self._curl_session
        identity = self._ensure_identity()
        proxies = None
        if self.selected_proxy_url:
            proxies = {
                "http": self.selected_proxy_url,
                "https": self.selected_proxy_url,
            }
        self._curl_session = CurlSession(
            impersonate=identity.impersonate,
            headers=identity.http_headers,
            timeout=max(5, self.settings.page_timeout_ms // 1000),
            trust_env=False,
            proxies=proxies,
        )
        return self._curl_session

    def _sync_browser_cookies_to_curl(self, session: CurlSession) -> int:
        if self.context is None:
            return 0
        browser_cookies = self.context.cookies([self.settings.page_url])
        session.cookies.clear()
        synchronized = 0
        now = time.time()
        for cookie in browser_cookies:
            expires_value = cookie.get("expires")
            if (
                isinstance(expires_value, (int, float))
                and expires_value > 0
                and expires_value <= now
            ):
                continue
            session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie.get("domain") or ".profi.ru",
                path=cookie.get("path") or "/",
                secure=bool(cookie.get("secure")),
            )
            synchronized += 1
        return synchronized

    def _sync_curl_cookies_to_browser(self, session: CurlSession) -> int:
        if self.context is None:
            return 0
        expected_host = (urlsplit(self.settings.page_url).hostname or "profi.ru").lower()
        browser_cookies: list[dict[str, object]] = []
        now = time.time()
        for cookie in session.cookies.jar:
            domain = (cookie.domain or f".{expected_host}").lower()
            hostname = domain.lstrip(".")
            if hostname != expected_host and not expected_host.endswith(f".{hostname}"):
                if hostname != "profi.ru" and not hostname.endswith(".profi.ru"):
                    continue
            if cookie.expires is not None and cookie.expires <= now:
                continue
            browser_cookie: dict[str, object] = {
                "name": cookie.name,
                "value": cookie.value,
                "domain": domain,
                "path": cookie.path or "/",
                "secure": bool(cookie.secure),
                "httpOnly": cookie.has_nonstandard_attr("HttpOnly"),
            }
            if cookie.expires is not None and cookie.expires > 0:
                browser_cookie["expires"] = float(cookie.expires)
            browser_cookies.append(browser_cookie)
        if browser_cookies:
            self.context.add_cookies(browser_cookies)
        return len(browser_cookies)

    def _run_cookie_bridge(self) -> None:
        if (
            self._cookie_bridge_completed
            or not self.settings.profi_http_cookie_bridge
        ):
            return
        self._cookie_bridge_completed = True
        try:
            session = self._get_curl_session()
            browser_count = self._sync_browser_cookies_to_curl(session)
            response = session.get(self.settings.page_url, allow_redirects=True)
            status = response.status_code
            response.close()
            returned_count = self._sync_curl_cookies_to_browser(session)
        except (CurlRequestError, PlaywrightError, OSError, TypeError, ValueError) as exc:
            logger.warning(
                "Стартовый HTTP-сеанс curl_cffi не выполнен; Chromium продолжит работу: %s",
                type(exc).__name__,
            )
            return
        logger.info(
            "Cookies Chromium -> curl_cffi: %s; Set-Cookie -> Chromium: %s; "
            "HTTP=%s, identity=%s",
            browser_count,
            returned_count,
            status,
            self._ensure_identity().identity_id,
        )

    def _clear_browser_identity_state(self) -> None:
        page = self.page
        if page is not None:
            with suppress(PlaywrightError):
                page.evaluate(PROFI_SITE_DATA_CLEAR_SCRIPT)
        if self.context is not None:
            with suppress(PlaywrightError):
                self.context.clear_cookies()
        if self._curl_session is not None:
            self._curl_session.cookies.clear()

    def create_browser_identity(
        self,
        *,
        apply_immediately: bool = True,
        clear_site_data: bool = True,
    ) -> BrowserIdentity:
        """Create and optionally apply a fresh session-only coherent identity."""
        previous = self._ensure_identity()
        browser_was_running = self.browser is not None
        if clear_site_data:
            self._clear_browser_identity_state()

        current = self._generate_session_identity(previous)
        self._identity = current
        if apply_immediately and browser_was_running:
            self.close()
            self._start_with_identity(current)
        logger.info(
            "Создан новый согласованный browser fingerprint: %s -> %s; "
            "platform=%s, architecture=%s, WebGL=%s",
            previous.identity_id,
            current.identity_id,
            current.client_hint_platform,
            current.client_hint_architecture,
            current.webgl_renderer,
        )
        return current

    def replace_blocked_identity(self, reason: str) -> None:
        previous = self._ensure_identity()
        self._clear_browser_identity_state()

        if self.settings.profi_identity_rotate_on_repeat_block:
            self.create_browser_identity(
                apply_immediately=False,
                clear_site_data=False,
            )
        current = self._ensure_identity()
        logger.warning(
            "Identity Chromium %s после повторной блокировки (%s): %s -> %s; "
            "cookies и хранилища Profi.ru очищены",
            "заменена" if current.identity_id != previous.identity_id else "сохранена",
            reason,
            previous.identity_id,
            current.identity_id,
        )

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

    def detect_ip_rotation_limit(self) -> str | None:
        """Распознаёт только лимит, для которого разрешена смена маршрута."""
        page = self._page()
        try:
            visible_text = " ".join(
                page.locator("body").inner_text(timeout=3_000).lower().split()
            )
        except PlaywrightError:
            return None
        if IP_ROTATION_LIMIT_MARKER in visible_text:
            return (
                "Profi.ru ограничил текущий IP: "
                "«Можно будет повторить через 12 часов»"
            )
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
